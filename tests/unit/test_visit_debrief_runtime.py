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

"""Home-coming debrief, runtime side (OD-16 v4): summary input, cleaning, chips; the registry wiring."""

from __future__ import annotations

import asyncio
import builtins

import pytest

from config.visit_settings import VISIT_DEBRIEF_INPUT_MAX_TOKENS
from main_routers.visit_router import debrief
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from tests.unit.visit_runtime_harness import FakeHost, Replies, bring_up, finish, teardown, wait_for
from utils import external_route_registry as registry
from utils import visit_route_state
from utils.tokenize import count_tokens


@pytest.fixture(autouse=True)
def _clean():
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()
    rtm.register_visit_route_kind()
    yield
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()


async def _visit_with_lines(tmp_path, monkeypatch, **kw):
    hgate, ggate = asyncio.Event(), asyncio.Event()
    host_replies = Replies(queue=[["主人家开场。"]])
    guest_replies = Replies(queue=[["客人开场，我带了小鱼干。"]])
    host_replies.default = [hgate, "之后。"]
    guest_replies.default = [ggate, "之后。"]
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=host_replies,
                                                    guest_replies=guest_replies, **kw)
    await wait_for(lambda: len(host.rt.journal.lines()) >= 2 and len(guest.rt.journal.lines()) >= 2)
    return host, guest, wire, clock, (hgate, ggate)


async def test_summary_reads_both_sides_from_memory_even_with_memory_off(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _visit_with_lines(tmp_path, monkeypatch,
                                                              settings={"visitMemoryEnabled": False})
    host.replies.queue = [["我回来啦。"], ["今天和客人聊了小鱼干。"]]
    opened = []
    real_open = builtins.open

    def spy_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    try:
        monkeypatch.setattr(builtins, "open", spy_open)
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        monkeypatch.setattr(builtins, "open", real_open)
        prompt = host.clients[0].prompts[-1]
        assert "主人家开场。" in prompt and "客人开场，我带了小鱼干。" in prompt
        assert prompt.index("主人家开场。") < prompt.index("客人开场")
        assert "======以下为" in prompt                        # 对端句在数据块里
        assert not [p for p in opened if p.endswith(".jsonl")]   # 简述不读 spool
        assert "今天和客人聊了小鱼干。" in host.host.outputs
        assert not [e for e in host.host.events if e.startswith("blocks:")]
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_summary_copying_the_peer_falls_back_to_the_fixed_line(tmp_path, monkeypatch):
    from config.prompts.prompts_visit import get_visit_debrief_fallback

    host, guest, wire, clock, gates = await _visit_with_lines(tmp_path, monkeypatch)
    host.replies.queue = [["我回来啦。"], ["她说客人开场，我带了小鱼干。"]]
    try:
        host.rt.request_finalize("recall")
        await finish(host.rt, clock)
        assert get_visit_debrief_fallback(host.rt.lang) in host.host.outputs
        assert not [o for o in host.host.outputs if "小鱼干" in o]
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_chips_and_ask_later_only_with_memory_on(tmp_path, monkeypatch):
    host, guest, wire, clock, gates = await _visit_with_lines(tmp_path, monkeypatch)
    host.replies.queue = [["我回来啦。"], ["聊得很开心。"]]
    try:
        host.rt.request_finalize("route_end")
        await finish(host.rt, clock)
        blocks = [b for b in host.host.blocks if b[1] == f"visit-debrief:{host.rt.visit_id}"]
        assert len(blocks) == 1
        buttons = blocks[0][0][1]["buttons"]
        assert [b["payload"]["choice"] for b in buttons] == ["diary", "forget"]
        assert buttons[1]["variant"] == "danger" and all(b["action"] == "visit_debrief_choice" for b in buttons)
        state = await host.rt.spool.read_state()
        assert state["debrief_choice"] == "ask_later" and state["debrief_chip_pending"] is True
        # 简述与芯片都不写私聊记忆：mirror 元数据显式关掉记忆
        assert host.host.user_inputs == []
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_record_block_is_bounded_and_keeps_the_newest_lines():
    lines = [{"lp": i, "side": "host" if i % 2 else "guest", "from": "own_cat" if i % 2 else "peer_cat",
              "ts": float(i), "text": f"第{i}句话，说了一些关于天气和小鱼干的事情。", "truncated": False}
             for i in range(7500)]
    block = await debrief.build_debrief_record(lines, "zh")
    assert count_tokens(block) <= VISIT_DEBRIEF_INPUT_MAX_TOKENS
    assert "第7499句话" in block and "第0句话" not in block
    assert block.index("第7498句话") < block.index("第7499句话")


async def test_render_chips_recovery_callback(monkeypatch):
    from main_routers.visit_router import host_port

    fake = FakeHost("Host")
    monkeypatch.setattr(host_port.ManagerHost, "for_character", classmethod(lambda cls, name: fake))
    assert await debrief.render_chips("v" * 22, own_char="Host", status="interrupted") is True
    assert fake.status_codes() == ["VISIT_INTERRUPTED_LAST_TIME"]
    assert fake.blocks[0][1] == "visit-debrief:" + "v" * 22
    monkeypatch.setattr(host_port.ManagerHost, "for_character", classmethod(lambda cls, name: None))
    assert await debrief.render_chips("v" * 22, own_char="Gone", status=None) is False


def test_visit_kind_is_registered_with_every_hook():
    kinds = {k.kind: k for k in registry._registered_kinds()}
    spec = kinds["neko_visit"]
    for hook in ("on_page_signal", "is_locked", "has_background_tasks", "route_voice_transcript",
                 "on_start_session", "current_instance"):
        assert getattr(spec, hook) is not None, hook
    assert spec.audio_passthrough is False


async def test_page_signal_reaches_the_handoff_after_the_route_is_gone(tmp_path, monkeypatch):
    from main_routers.visit_router import inbox_handoff

    handoff = inbox_handoff.InboxHandoff("h" * 22, finalize_at=0.0, clock=lambda: 1.0)
    handoff.attach_speech("ritual", "sp-late")
    handoff.mark_queued("ritual", 1000)
    assert rtm.get_runtime("Host") is None
    assert await registry.route_external_page_signal("Host", {"speech_id": "sp-late", "played_ms": 900,
                                                              "ended": True, "final": True})
    assert handoff._segments["ritual"].done


async def test_a_failed_roster_write_keeps_the_visit_memory(tmp_path, monkeypatch):
    from main_logic.visit.subjects import PeerRoster

    async def broken_upsert(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(PeerRoster, "upsert", broken_upsert)
    host, guest, wire, clock, gates = await _visit_with_lines(tmp_path, monkeypatch)
    try:
        assert host.rt.memory_enabled is True and host.rt.spool.is_open
        assert host.rt.spool_lines >= 2                    # 名册写不进，转录照样逐句落盘
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)


async def test_no_diary_chip_when_nothing_reached_the_spool(tmp_path, monkeypatch):
    from main_logic.visit.spool import VisitSpool

    async def broken_append(self, line):
        raise OSError("disk full")

    monkeypatch.setattr(VisitSpool, "append", broken_append)
    host, guest, wire, clock, gates = await _visit_with_lines(tmp_path, monkeypatch)
    host.replies.queue = [["我回来啦。"], ["聊得很开心。"]]
    try:
        assert host.rt.journal.lines() and host.rt.spool_lines == 0
        host.rt.request_finalize("route_end")
        await finish(host.rt, clock)
        # 上传流水有句子，但日记读的 spool 一句没有：不出芯片
        assert not [b for b in host.host.blocks if b[1] == f"visit-debrief:{host.rt.visit_id}"]
    finally:
        for g in gates:
            g.set()
        await teardown(host, guest, wire=wire, clock=clock)
