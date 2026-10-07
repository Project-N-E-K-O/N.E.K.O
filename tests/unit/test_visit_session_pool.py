# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Isolated visit session: construction, history order, trimming (visit design §3.6.1, PR-09a)."""

from __future__ import annotations

import pytest

from config.prompts.prompts_visit import get_family_neutral_term
from config.visit_settings import VISIT_RESPONSE_MAX_TOKENS
from main_routers.visit_router import session_pool as sp
from utils.llm_client import AIMessage, HumanMessage, SystemMessage

API = {"base_url": "http://llm.test", "api_key": "k", "model": "m", "provider_type": None}


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._conversation_history = []
        self.max_response_rerolls = 1
        self.repetition_reset_enabled = True
        self.closed = 0
        self.fail_connect = False
        FakeClient.instances.append(self)

    async def connect(self, instructions):
        if FakeClient.fail_next:
            raise RuntimeError("connect failed")
        self._conversation_history.append(SystemMessage(content=instructions))

    async def close(self):
        self.closed += 1


FakeClient.fail_next = False


async def _session(side="host"):
    FakeClient.instances.clear()
    return await sp.create_visit_session("Mimi", side, instructions="SYS", lang="zh", api_config=API,
                                         client_factory=FakeClient)


async def test_session_is_isolated_toolless_and_short():
    session = await _session()
    kwargs = FakeClient.instances[0].kwargs
    assert kwargs["tool_definitions"] == [] and kwargs["max_response_length"] == VISIT_RESPONSE_MAX_TOKENS
    assert kwargs["master_name"] == get_family_neutral_term("zh") and kwargs["lanlan_name"] == "Mimi"
    assert session.client.max_response_rerolls == 0
    assert session.client.repetition_reset_enabled is False
    assert isinstance(session.history[0], SystemMessage) and session.history[0].content == "SYS"


async def test_deltas_reach_only_the_installed_sink():
    session = await _session()
    on_delta = FakeClient.instances[0].kwargs["on_text_delta"]
    await on_delta("dropped", True)
    got = []
    session.set_sink(got.append)
    await on_delta("hi", True)
    await on_delta("", False)
    session.set_sink(None)
    await on_delta("late", False)
    assert got == ["hi"]


async def test_failed_connect_closes_the_client():
    FakeClient.fail_next = True
    try:
        with pytest.raises(RuntimeError):
            await _session()
    finally:
        FakeClient.fail_next = False
    assert FakeClient.instances[0].closed == 1


async def test_same_lp_openings_sort_host_first_on_both_sides():
    host_line, guest_line = HumanMessage(content="host opens"), HumanMessage(content="guest opens")
    a = await _session("host")
    sp.append_visit_message(a, guest_line, sp.sort_key(1, "guest"))     # 先到的是对方
    b = await _session("guest")
    sp.append_visit_message(b, HumanMessage(content="host opens"), sp.sort_key(1, "host"))
    sp.append_visit_message(a, host_line, sp.sort_key(1, "host"))
    sp.append_visit_message(b, HumanMessage(content="guest opens"), sp.sort_key(1, "guest"))
    assert [m.content for m in a.history[1:]] == [m.content for m in b.history[1:]] == ["host opens", "guest opens"]


async def test_late_lines_land_by_lp_and_unkeyed_replies_move_with_their_prompt():
    s = await _session()
    sp.append_visit_message(s, HumanMessage(content="lp1"), (1, 0))
    s.history.append(AIMessage(content="reply to lp1"))         # stream_text 追加的回复，尚未登记
    s.history.append(HumanMessage(content="lp3"))
    sp.tag_last_turn(s, prompt_key=(3, 1), reply_key=None)
    s.history.append(HumanMessage(content="lp2"))
    s.tag(s.history[-1], (2, 1))
    sp.sort_visit_history(s)
    assert [m.content for m in s.history[1:]] == ["lp1", "reply to lp1", "lp2", "lp3"]
    assert isinstance(s.history[0], SystemMessage)


async def test_tag_last_turn_keys_prompt_and_reply():
    s = await _session()
    s.history.extend([HumanMessage(content="q"), AIMessage(content="a")])
    sp.tag_last_turn(s, prompt_key=(5, 1), reply_key=(6, 0))
    assert s.key_of(s.history[1]) == (5, 1) and s.key_of(s.history[2]) == (6, 0)


async def test_tag_last_turn_leaves_an_already_keyed_reply_alone():
    s = await _session()
    s.history.extend([HumanMessage(content="q"), AIMessage(content="a")])
    sp.tag_last_turn(s, prompt_key=(5, 1), reply_key=(6, 0))
    sp.tag_last_turn(s, prompt_key=(7, 1), reply_key=(8, 0))      # 空 prompt：stream_text 什么都没追加
    assert s.key_of(s.history[1]) == (5, 1) and s.key_of(s.history[2]) == (6, 0)


async def test_trim_keeps_system_and_newest():
    s = await _session()
    for k in range(50):
        sp.append_visit_message(s, HumanMessage(content=str(k)), (k, 0))
    sp.trim_visit_history(s, max_messages=40)
    assert isinstance(s.history[0], SystemMessage) and len(s.history) == 41
    assert s.history[1].content == "10" and s.history[-1].content == "49"
    assert len(s._keys) == 40


async def test_trim_on_tiny_history_is_a_no_op():
    s = await _session()
    sp.trim_visit_history(s, max_messages=0)
    assert len(s.history) == 1


async def test_pop_trailing_ai_message_only_pops_the_expected_line():
    s = await _session()
    s.history.extend([HumanMessage(content="q"), AIMessage(content="whole line")])
    assert sp.pop_trailing_ai_message(s, "other") is False and len(s.history) == 3
    assert sp.pop_trailing_ai_message(s, "whole line") is True and len(s.history) == 2
    assert sp.pop_trailing_ai_message(s, "whole line") is False


async def test_usage_estimate_counts_history_and_output():
    s = await _session()
    usage = sp.estimate_turn_usage(s, "你好呀")
    assert usage["llm_input_tokens"] > 0 and usage["llm_output_tokens"] > 0


async def test_usage_estimate_does_not_count_the_reply_as_input():
    s = await _session()
    s.history.append(HumanMessage(content="今天去哪儿玩"))
    before = sp.estimate_turn_usage(s, "")["llm_input_tokens"]
    reply = "我们去公园晒太阳吧，那里有好多鸽子和长椅可以坐。" * 4
    s.history.append(AIMessage(content=reply))     # stream_text 把本轮回复追加在末尾
    usage = sp.estimate_turn_usage(s, reply)
    assert usage["llm_input_tokens"] == before and usage["llm_output_tokens"] > 0


async def test_close_drops_the_sink_and_never_raises():
    s = await _session()
    s.set_sink(lambda _t: None)

    async def boom():
        raise RuntimeError("x")

    s.client.close = boom
    await sp.close_visit_session(s)
    assert s._sink is None



async def test_repeated_replies_never_wipe_an_ordered_history():
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import AIMessage, HumanMessage

    client = OmniOfflineClient(base_url="http://llm.test", api_key="k", model="m")
    history = [SystemMessage(content="SYS"), HumanMessage(content="peer"), AIMessage(content="喵喵喵")]
    client._conversation_history = list(history)
    client.repetition_reset_enabled = False
    for _ in range(4):
        assert await client._check_repetition("喵喵喵") is False
    assert client._conversation_history == history
    client.repetition_reset_enabled = True
    fired = [await client._check_repetition("喵喵喵") for _ in range(3)]
    assert fired[-1] is True and client._conversation_history == history[:1]   # 默认行为不变



async def test_an_empty_reply_keeps_the_previous_reply_in_the_input_estimate():
    from utils.llm_client import AIMessage, HumanMessage
    from utils.tokenize import count_tokens

    session = await _session()
    session.history.extend([HumanMessage(content="peer says hi"), AIMessage(content="上一轮的回复内容")])
    usage = sp.estimate_turn_usage(session, "")
    expected = sum(count_tokens(m.content) for m in session.history)
    assert usage == {"llm_input_tokens": expected, "llm_output_tokens": 0}
