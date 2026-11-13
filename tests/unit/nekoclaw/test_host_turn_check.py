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

"""What the host tells her outside a dispatch: the turn check, the list a new
session is given, reports of a restart, late receipts and questions.

The turn check looks at a turn in which she called no task tool. Each test
that reaches the model asserts how often it was asked: once for the check,
and nothing more unless her promise is dispatched (then the dispatch's own
calls follow, as for any dispatch).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish as stop, usage
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    outbox,
    pick_plugin,
    scripted_runtime,
    wait_status,
    wait_until,
)
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, finish, plugin

LANGUAGES = ["zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"]


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _weather(server: FakePluginServer) -> None:
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"reply": f"sunny in {args.get('city', 'town')}"})

    server.add_plugin(
        plugin(
            "weather",
            "Weather lookups for any city",
            [entry("today", "Today's weather", llm_result_fields=[])],
            keywords=["天气", "weather"],
        ),
        today=today,
    )


def _host(hosts: list[Any], tmp_path: Any, server: Any, llm: FakeDecisionLLM, **kwargs: Any) -> Any:
    host = make_host(tmp_path, server, decision_llm=llm, **kwargs)
    hosts.append(host)
    return host


async def _check(host: Any, user_text: str, reply_text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "zh")
    return await host.check_turn(user_text=user_text, reply_text=reply_text, **kwargs)


async def _notices(host: Any) -> list[tuple[str, str, str]]:
    return [
        (item.payload["reason"], item.payload["delivery_mode"], item.payload["text"])
        for item in await outbox(host, "correction")
    ]


# ---------------------------------------------------------------------------
# Turns that are not checked cost nothing
# ---------------------------------------------------------------------------


async def test_switched_off_host_checks_nothing(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "promised", "task": "查天气"}])
    host = _host(hosts, tmp_path, server, llm, enabled=lambda: False)

    result = await _check(host, "明天上海天气怎么样", "好的，我去查")

    assert (result.verdict, result.skipped, result.model_calls) == ("skipped", "disabled", 0)
    assert llm.calls == []


@pytest.mark.parametrize("user_text, reply_text", [("", "好的"), ("帮我查天气", ""), ("   ", "  ")])
async def test_a_turn_with_nothing_said_on_one_side_is_not_checked(server, hosts, tmp_path, user_text, reply_text):
    _weather(server)
    llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, user_text, reply_text)

    assert (result.verdict, result.skipped) == ("skipped", "empty")
    assert llm.calls == []


async def test_with_nothing_that_could_take_a_task_no_turn_is_checked(server, hosts, tmp_path):
    # No plugin and no task agent: whatever she said, nothing could be done about it.
    llm = FakeDecisionLLM(turn=[{"verdict": "promised", "task": "订机票"}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "帮我订一张去东京的机票", "好的，马上订", external_intent=0.9)

    assert (result.verdict, result.skipped) == ("skipped", "no_capability")
    assert llm.calls == []
    assert await outbox(host) == []


async def test_a_turn_that_does_not_look_like_a_request_is_not_checked(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "promised", "task": "x"}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "今天好累啊", "辛苦啦，早点休息", external_intent=0.05)

    assert (result.verdict, result.skipped, result.model_calls) == ("skipped", "gate", 0)
    assert llm.calls == []


@pytest.mark.parametrize(
    "user_text, external_intent",
    [
        # The input-time score says it needs something outside the conversation.
        ("帮我订一张去东京的机票", 0.6),
        # No score at all: a turn is never held back for want of one.
        ("帮我订一张去东京的机票", None),
        # A low score, but a plugin keyword in the user's words.
        ("明天天气怎么样", 0.0),
    ],
)
async def test_a_turn_that_looks_like_a_request_costs_one_small_model_call(
    server, hosts, tmp_path, user_text, external_intent
):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "none", "task": ""}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, user_text, "嗯嗯，我在听", external_intent=external_intent)

    assert (result.verdict, result.action, result.model_calls) == ("none", "", 1)
    assert llm.tiers() == ["turn_check"]
    # Nothing follows from "none": no task, no word to her.
    assert await outbox(host) == []
    assert await host.ledger.select(lanlan_name="Lan") == []


# ---------------------------------------------------------------------------
# The four verdicts
# ---------------------------------------------------------------------------


async def test_she_declined_on_purpose_and_nothing_is_done(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "none", "task": ""}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "帮我查一下明天的天气", "不要，自己看窗外嘛", external_intent=0.8)

    assert (result.verdict, result.action) == ("none", "")
    assert server.calls == []
    assert await host.ledger.select(lanlan_name="Lan") == []


async def test_she_said_she_cannot_but_it_can_be_done_so_she_is_told_and_nothing_is_run(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "unable", "task": "查上海明天的天气"}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "帮我查一下上海明天的天气", "我没有办法查天气呢", external_intent=0.8)

    assert (result.verdict, result.action, result.model_calls) == ("unable", "corrected", 1)
    # One call in all: whether it can be done is matched locally.
    assert llm.tiers() == ["turn_check"]
    assert await _notices(host) == [
        ("can_do", "proactive", "刚才你说「查上海明天的天气」做不到，其实办得到。请自然地向用户更正，然后用任务派发工具去办。")
    ]
    # The check has no say of its own: nothing is filed and nothing is called.
    assert server.calls == []
    assert await host.ledger.select(lanlan_name="Lan") == []


async def test_she_said_she_cannot_and_indeed_nothing_can(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "unable", "task": "把客厅的灯关掉"}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "把客厅的灯关掉", "我碰不到灯呀", external_intent=0.8)

    assert (result.verdict, result.action, result.model_calls) == ("unable", "", 1)
    assert await outbox(host) == []


async def test_she_agreed_without_dispatching_so_it_is_dispatched_in_her_name(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(
        turn=[{"verdict": "promised", "task": "查上海明天的天气"}],
        fine=[pick_plugin("weather", "today", {"city": "Shanghai"})],
    )
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(
        host, "帮我查一下上海明天的天气", "好的，我这就去查", external_intent=0.8, conversation_id="conv-7"
    )

    assert (result.verdict, result.action) == ("promised", "dispatched")
    assert result.receipt.kind == "accepted"
    # The check itself is one call; the first round of the dispatch adds none.
    assert result.model_calls == 1
    done = await wait_status(host, result.receipt.commission_id, "done")
    assert (done.source, done.dispatch_text, done.user_text) == ("promised", "查上海明天的天气", "帮我查一下上海明天的天气")
    assert done.conversation_id == "conv-7"
    # Then the fine selection, as for any dispatch: two calls for the whole turn.
    assert llm.tiers() == ["turn_check", "fine"]
    assert server.run_requests[0]["args"]["_ctx"]["latest_user_request"] == "帮我查一下上海明天的天气"
    # She is told with her next turn; nothing wakes her for it.
    assert await _notices(host) == [("filed", "passive", "你刚才答应的事已经替你派发了。已受理：查上海明天的天气")]


async def test_a_promise_judged_while_everything_was_stopped_is_not_dispatched(server, hosts, tmp_path, monkeypatch):
    _weather(server)
    llm = FakeDecisionLLM(
        turn=[{"verdict": "promised", "task": "查上海明天的天气"}],
        fine=[pick_plugin("weather", "today", {"city": "Shanghai"})],
    )
    host = _host(hosts, tmp_path, server, llm)
    judge = host.brain._decision.judge_turn

    async def stopped_while_judging(*args, **kwargs):
        verdict = await judge(*args, **kwargs)
        # "Stop everything" comes in while the small model is still at it.
        host.brain.drop_pending_dispatches()
        return verdict

    monkeypatch.setattr(host.brain._decision, "judge_turn", stopped_while_judging)

    result = await _check(host, "帮我查一下上海明天的天气", "好的，我这就去查", external_intent=0.8)

    # A promise from before the stop is not made into work after it.
    assert (result.verdict, result.action, result.skipped, result.receipt) == ("promised", "", "stopped", None)
    assert await host.ledger.select(lanlan_name="Lan") == []
    assert await outbox(host) == []
    assert llm.tiers() == ["turn_check"]
    assert server.calls == []


async def test_a_dispatch_stopped_before_its_first_round_got_going_files_nothing(server, hosts, tmp_path):
    # The first round is a task of its own and starts when the loop gets to
    # it; a stop that comes in between must still reach it.
    _weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "Shanghai"})])
    host = _host(hosts, tmp_path, server, llm)
    original_spawn = host.brain._spawn

    def spawn_then_stop(coro, **kwargs):
        task = original_spawn(coro, **kwargs)
        if getattr(coro, "__name__", "") == "_first_round":
            host.brain.drop_pending_dispatches()
        return task

    host.brain._spawn = spawn_then_stop

    receipt = await host.dispatch("查一下上海明天的天气", lanlan_name="Lan", lang="zh")

    assert (receipt.kind, receipt.reason) == ("no_capability", "stopped")
    assert await host.ledger.select(lanlan_name="Lan") == []
    assert server.calls == []


async def test_a_promise_without_a_named_task_is_dispatched_in_the_users_words(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "promised", "task": ""}], fine=[pick_plugin("weather", "today")])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "明天天气怎么样", "我去看看", external_intent=0.8)

    commission = await host.ledger.get(result.receipt.commission_id)
    assert commission.dispatch_text == "明天天气怎么样"


async def test_a_promise_nothing_can_keep_wakes_her_to_take_it_back(server, hosts, tmp_path):
    # A plugin is there (so the turn is checked), but it does not fit, no
    # task agent can be started and the small model finds nothing either.
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "promised", "task": "把客厅的灯关掉"}], coarse=[])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "把客厅的灯关掉", "好，我去关", external_intent=0.8)

    assert (result.verdict, result.action, result.receipt.kind) == ("promised", "dispatched", "no_capability")
    # The check, and the one coarse screen a dispatch makes before it says no.
    assert llm.tiers() == ["turn_check", "coarse"]
    assert result.model_calls == 2
    ((reason, delivery, text),) = await _notices(host)
    assert (reason, delivery) == ("promised_failed", "proactive")
    assert text == "你刚才答应的事没能办成。办不了：没有找到能办这件事的能力 请自然地向用户说明。"
    assert await host.ledger.select(lanlan_name="Lan") == []


async def test_a_promise_to_stop_something_stops_it(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "promised", "task": "别查天气了"}])
    host = _host(hosts, tmp_path, server, llm)
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="查上海明天的天气", dispatch_text="查上海明天的天气", source="dispatch"
    )
    await host.ledger.transition(commission.id, "working", route="plugin", plugin_id="weather", entry_id="today")

    result = await _check(host, "算了别查天气了", "好，我让它停下", external_intent=0.8)

    assert result.receipt.kind == "stopped"
    assert (await host.ledger.get(commission.id)).status == "cancelled"
    assert await _notices(host) == [("filed", "passive", "你刚才答应的事已经替你派发了。已叫停：查上海明天的天气")]


async def test_she_said_it_is_done_but_no_task_says_so(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "claimed_done", "task": "订去东京的机票"}])
    host = _host(hosts, tmp_path, server, llm)

    result = await _check(host, "机票订了吗", "订好啦，明早八点的", external_intent=0.8)

    assert (result.verdict, result.action, result.model_calls) == ("claimed_done", "corrected", 1)
    assert await _notices(host) == [
        ("no_record", "proactive", "刚才你说「订去东京的机票」已经办好了，但任务记录里没有这件事。请自然地向用户更正；真要办，就用任务派发工具。")
    ]


@pytest.mark.parametrize("status", ["done", "working", "failed"])
async def test_she_said_it_is_done_and_a_task_about_it_is_on_record(server, hosts, tmp_path, status):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "claimed_done", "task": "查上海明天的天气"}])
    host = _host(hosts, tmp_path, server, llm)
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="查上海明天的天气", dispatch_text="查上海明天的天气", source="dispatch"
    )
    if status != "failed":
        await host.ledger.transition(commission.id, "working")
    if status != "working":
        await host.ledger.transition(commission.id, status, result_summary="晴，18 度")

    result = await _check(host, "天气查到了吗", "查到啦，明天晴，18 度", external_intent=0.8)

    assert (result.verdict, result.action) == ("claimed_done", "")
    assert await outbox(host, "correction") == []


async def test_another_characters_task_is_no_record_for_her(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "claimed_done", "task": "查上海明天的天气"}])
    host = _host(hosts, tmp_path, server, llm)
    other, _created = await host.ledger.create(
        lanlan_name="Mei", title="查上海明天的天气", dispatch_text="查上海明天的天气", source="dispatch"
    )
    await host.ledger.transition(other.id, "working")

    result = await _check(host, "天气查到了吗", "查到啦", external_intent=0.8)

    assert result.action == "corrected"


@pytest.mark.parametrize("lang", LANGUAGES)
async def test_what_she_is_told_is_in_the_sessions_language(server, hosts, tmp_path, lang):
    from config.prompts.prompts_nekoclaw_brain import TURN_CHECK_TEXTS

    _weather(server)
    llm = FakeDecisionLLM(turn=[{"verdict": "unable", "task": "weather tomorrow"}])
    host = _host(hosts, tmp_path, server, llm)

    await _check(host, "weather tomorrow?", "I cannot look that up", external_intent=0.8, lang=lang)

    ((reason, _delivery, text),) = await _notices(host)
    assert reason == "can_do"
    assert text == TURN_CHECK_TEXTS[lang]["can_do"].format(task="weather tomorrow")


async def test_both_sentences_are_capped_in_tokens_before_the_model_sees_them(server, hosts, tmp_path):
    from config.nekoclaw_settings import TURN_CHECK_REPLY_MAX_TOKENS, TURN_CHECK_USER_TEXT_MAX_TOKENS
    from utils.tokenize import count_tokens

    _weather(server)
    llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, server, llm)
    long = "天气 " + " ".join(f"词{n}" for n in range(3000))

    await _check(host, long, long, external_intent=0.8)

    user, reply = llm.last("turn_check")[1]["content"].split("\nREPLY: ")
    assert count_tokens(user[len("USER: ") :]) <= TURN_CHECK_USER_TEXT_MAX_TOKENS
    assert count_tokens(reply) <= TURN_CHECK_REPLY_MAX_TOKENS


# ---------------------------------------------------------------------------
# What a new session is told
# ---------------------------------------------------------------------------


async def test_brief_lists_what_is_under_way_and_what_waits_for_the_user(server, hosts, tmp_path):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    queued, _ = await host.ledger.create(lanlan_name="Lan", title="下载电影", dispatch_text="下载电影", source="dispatch")
    working, _ = await host.ledger.create(lanlan_name="Lan", title="整理照片", dispatch_text="整理照片", source="dispatch")
    await host.ledger.transition(working.id, "working")
    waiting, _ = await host.ledger.create(lanlan_name="Lan", title="订餐厅", dispatch_text="订餐厅", source="dispatch")
    await host.ledger.transition(waiting.id, "working")
    await host.ledger.transition(
        waiting.id,
        "waiting_user",
        pending_question=json.dumps({"kind": "report", "question": "订几点的？"}, ensure_ascii=False),
    )
    done, _ = await host.ledger.create(lanlan_name="Lan", title="查天气", dispatch_text="查天气", source="dispatch")
    await host.ledger.transition(done.id, "working")
    await host.ledger.transition(done.id, "done")
    child, _ = await host.ledger.create(
        lanlan_name="Lan", title="子任务", dispatch_text="子任务", source="task_agent", parent_id=working.id
    )
    await host.ledger.transition(child.id, "working")
    other, _ = await host.ledger.create(lanlan_name="Mei", title="别人的事", dispatch_text="别人的事", source="dispatch")

    lines = await host.brief(lanlan_name="Lan", lang="zh")

    # Oldest first; finished tasks, task agents' own task agents and other
    # characters' tasks are left out.
    assert lines == ["- [排队中] 下载电影", "- [进行中] 整理照片", "- [等用户回话] 订餐厅 在等用户回答：订几点的？"]


async def test_a_permission_question_is_told_with_what_it_asks_to_allow(server, hosts, tmp_path):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    waiting, _ = await host.ledger.create(lanlan_name="Lan", title="清理旧文件", dispatch_text="清理旧文件", source="dispatch")
    await host.ledger.transition(waiting.id, "working")
    question = {
        "kind": "permission",
        "category": "run_command",
        "detail": {"command": "Remove-Item old.txt"},
        "options": ["always", "session", "once", "deny"],
    }
    await host.ledger.transition(waiting.id, "waiting_user", pending_question=json.dumps(question, ensure_ascii=False))

    lines = await host.brief(lanlan_name="Lan", lang="zh")
    answer = await host.query("", lanlan_name="Lan", lang="zh")

    # The command itself, not the name of its category.
    expected = "在等用户回答：运行命令 Remove-Item old.txt (始终允许 / 仅本会话允许 / 本次允许 / 拒绝)"
    assert lines == [f"- [等用户回话] 清理旧文件 {expected}"]
    assert expected in answer.text and "run_command" not in answer.text


def _long_command() -> str:
    """A command far longer than any line or question has room for."""
    return "Remove-Item " + " ".join(f"C:/Users/me/Downloads/old-file-{n}.tmp" for n in range(400)) + " -Recurse -Force"


def _full_length_title() -> str:
    from config.nekoclaw_settings import COMMISSION_TITLE_MAX_TOKENS
    from utils.tokenize import count_tokens, truncate_to_tokens

    title = truncate_to_tokens("清理下载文件夹里的旧文件" * 8, COMMISSION_TITLE_MAX_TOKENS)
    assert count_tokens(title) == COMMISSION_TITLE_MAX_TOKENS
    return title


@pytest.mark.parametrize("lang", LANGUAGES)
async def test_a_long_command_gives_way_in_a_line_and_the_answers_stay(server, hosts, tmp_path, lang):
    from config.nekoclaw_settings import QUERY_PERMISSION_ROW_MAX_TOKENS
    from config.prompts.prompts_nekoclaw_brain import PERMISSION_DETAIL_CUT_MARK, PERMISSION_OPTION_NAMES
    from utils.tokenize import count_tokens

    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    title = _full_length_title()
    waiting, _ = await host.ledger.create(lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch")
    await host.ledger.transition(waiting.id, "working")
    question = {
        "kind": "permission",
        "category": "run_command",
        "detail": {"command": _long_command()},
        "options": ["always", "session", "once", "deny"],
    }
    await host.ledger.transition(waiting.id, "waiting_user", pending_question=json.dumps(question))

    (brief_line,) = await host.brief(lanlan_name="Lan", lang=lang)
    answer = await host.query("", lanlan_name="Lan", lang=lang)
    (query_line,) = [line for line in answer.text.splitlines() if line.startswith("- ")]

    for line in (brief_line, query_line):
        assert count_tokens(line) <= QUERY_PERMISSION_ROW_MAX_TOKENS
        # The cut is said, and it is in the middle of the command: how the
        # command begins and how it ends are both still there.
        assert PERMISSION_DETAIL_CUT_MARK[lang] in line
        assert "Remove-Item C:/Users" in line and "-Recurse -Force" in line
        # Every answer on offer is still there, after the command.
        for name in PERMISSION_OPTION_NAMES[lang].values():
            assert name in line
        assert line.rstrip().endswith(")")


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_kind_of_permission_keeps_its_answers_in_a_line_of_full_length(lang):
    """The longest title there can be, the status word of a waiting commission,
    the longest words of every kind of permission and a command that does not
    fit: only the command gives way, the answers on offer all stay.

    It holds because a title is cut to ``COMMISSION_TITLE_MAX_TOKENS`` when
    the commission is made (only top-level commissions are listed); a larger
    cap, longer words or another answer to offer would show up here first.
    """
    from types import SimpleNamespace

    from config.nekoclaw_settings import QUERY_PERMISSION_ROW_MAX_TOKENS
    from config.prompts.prompts_nekoclaw_brain import (
        PERMISSION_CATEGORY_TEXTS,
        PERMISSION_OPTION_NAMES,
        QUERY_TEXTS,
    )
    from nekoclaw.host.task_brain import status_word, waiting_row
    from utils.tokenize import count_tokens

    title = _full_length_title()
    status = status_word("waiting_user", lang)
    frame = QUERY_TEXTS[lang]["asked"]
    # The line of a query and the line of a brief.
    heads = (f"- {title} ({status})", f"- [{status}] {title}")
    for category in PERMISSION_CATEGORY_TEXTS[lang]:
        question = {
            "kind": "permission",
            "category": category,
            "detail": {"command": _long_command()},
            "options": ["always", "session", "once", "deny"],
        }
        commission = SimpleNamespace(question=lambda question=question: question)
        for head in heads:
            line = waiting_row(head, commission, frame, lang)
            assert count_tokens(line) <= QUERY_PERMISSION_ROW_MAX_TOKENS, (category, head)
            assert line.startswith(head), (category, head)
            names = " / ".join(PERMISSION_OPTION_NAMES[lang].values())
            assert line.endswith(f"({names})"), (category, head)


@pytest.mark.parametrize("lang", LANGUAGES)
async def test_a_query_answer_lists_whole_lines_and_says_how_many_it_left_out(server, hosts, tmp_path, lang):
    from config.nekoclaw_settings import QUERY_RESULT_MAX_TOKENS
    from config.prompts.prompts_nekoclaw_brain import PERMISSION_OPTION_NAMES, QUERY_TEXTS
    from utils.tokenize import count_tokens

    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    question = {
        "kind": "permission",
        "category": "run_command",
        "detail": {"command": _long_command()},
        "options": ["always", "session", "once", "deny"],
    }
    for number in range(6):
        title = f"{_full_length_title()[:-2]}{number}"
        waiting, _ = await host.ledger.create(lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch")
        await host.ledger.transition(waiting.id, "working")
        await host.ledger.transition(waiting.id, "waiting_user", pending_question=json.dumps(question))

    answer = await host.query("", lanlan_name="Lan", lang=lang)

    lines = answer.text.splitlines()
    rows = [line for line in lines if line.startswith("- ")]
    assert count_tokens(answer.text) <= QUERY_RESULT_MAX_TOKENS
    # Six lines of about 120 tokens do not fit in 400. Those that are listed
    # are whole, each with every answer on offer; the rest are counted.
    assert 1 <= len(rows) < 6
    for row in rows:
        for name in PERMISSION_OPTION_NAMES[lang].values():
            assert name in row
        assert row.rstrip().endswith(")")
    assert lines[-1] == QUERY_TEXTS[lang]["more"].format(count=6 - len(rows))


def test_lines_that_fit_are_left_as_they_are_and_a_heading_left_alone_is_dropped():
    from config.prompts.prompts_nekoclaw_brain import QUERY_TEXTS
    from nekoclaw.host.task_brain import whole_lines_within
    from utils.tokenize import count_tokens

    lines = ["Under way:", "- one", "Recently finished:", "- " + "word " * 300]

    assert whole_lines_within(lines[:2], 400, "{count} more not listed.") == lines[:2]
    # The long last line does not fit; its heading says nothing on its own.
    assert whole_lines_within(lines, 50, "{count} more not listed.") == ["Under way:", "- one", "1 more not listed."]
    # Whatever the budget, what comes back fits in it with the count line.
    many = ["Under way:", *(f"- task number {n} " + "word " * (n % 7 + 30) for n in range(30))]
    for budget in range(20, 260):
        kept = whole_lines_within(many, budget, "{count} more not listed.")
        assert count_tokens(chr(10).join(kept)) <= budget, budget
        assert kept[-1].endswith("more not listed.")
    # The line that says how many were left out exists in every language.
    assert {lang for lang, words in QUERY_TEXTS.items() if "{count}" in words.get("more", "")} == set(LANGUAGES)


async def test_a_line_of_another_kind_of_question_keeps_the_ordinary_cap(server, hosts, tmp_path):
    from config.nekoclaw_settings import QUERY_ROW_MAX_TOKENS
    from utils.tokenize import count_tokens

    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    waiting, _ = await host.ledger.create(lanlan_name="Lan", title="订餐厅", dispatch_text="订餐厅", source="dispatch")
    await host.ledger.transition(waiting.id, "working")
    question = {"kind": "report", "question": "订几点的？" + "七点还是九点？" * 200}
    await host.ledger.transition(waiting.id, "waiting_user", pending_question=json.dumps(question, ensure_ascii=False))

    (line,) = await host.brief(lanlan_name="Lan", lang="zh")

    assert line.startswith("- [等用户回话] 订餐厅 在等用户回答：订几点的？")
    assert count_tokens(line) == QUERY_ROW_MAX_TOKENS


@pytest.mark.parametrize("lang", LANGUAGES)
def test_the_mark_of_a_cut_fits_in_the_least_room_a_command_is_given(lang):
    from config.nekoclaw_settings import PERMISSION_SUBJECT_MIN_TOKENS
    from config.prompts.prompts_nekoclaw_brain import PERMISSION_DETAIL_CUT_MARK
    from utils.tokenize import count_tokens

    # With less room than the mark takes, the cut would fall back to a silent one.
    assert set(PERMISSION_DETAIL_CUT_MARK) == set(LANGUAGES)
    assert count_tokens(PERMISSION_DETAIL_CUT_MARK[lang]) + 8 <= PERMISSION_SUBJECT_MIN_TOKENS


def test_a_short_line_budget_still_quotes_the_command_with_the_mark():
    from config.nekoclaw_settings import PERMISSION_SUBJECT_MIN_TOKENS
    from nekoclaw.host.reports import describe_permission
    from utils.tokenize import count_tokens

    question = {
        "kind": "permission",
        "category": "run_command",
        "detail": {"command": _long_command()},
        "options": ["always", "session", "once", "deny"],
    }

    # No room at all: the command still gets its least, and says that it is cut.
    line = describe_permission(question, "zh", max_tokens=0)

    assert "中间省略" in line and line.endswith("(始终允许 / 仅本会话允许 / 本次允许 / 拒绝)")
    assert line.startswith("运行命令 Remove-Item")
    assert count_tokens(line) <= PERMISSION_SUBJECT_MIN_TOKENS + 30
    # A command that fits is quoted whole, without a mark.
    short = describe_permission({**question, "detail": {"command": "dir"}}, "zh", max_tokens=40)
    assert short == "运行命令 dir (始终允许 / 仅本会话允许 / 本次允许 / 拒绝)"


async def test_brief_is_empty_with_nothing_under_way_or_when_switched_off(server, hosts, tmp_path):
    switch = {"on": True}
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(), enabled=lambda: switch["on"])

    assert await host.brief(lanlan_name="Lan", lang="zh") == []

    commission, _ = await host.ledger.create(lanlan_name="Lan", title="下载电影", dispatch_text="下载电影", source="dispatch")
    switch["on"] = False

    assert await host.brief(lanlan_name="Lan", lang="zh") == []


async def test_brief_asks_no_model_and_stays_within_its_limits(server, hosts, tmp_path):
    from config.nekoclaw_settings import QUERY_LEDGER_ROWS, QUERY_ROW_MAX_TOKENS
    from utils.tokenize import count_tokens

    llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, server, llm)
    for number in range(QUERY_LEDGER_ROWS + 5):
        title = f"任务{number} " + " ".join(f"词{n}" for n in range(400))
        await host.ledger.create(lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch")

    lines = await host.brief(lanlan_name="Lan", lang="zh")

    assert llm.calls == []
    assert len(lines) == QUERY_LEDGER_ROWS
    assert all(count_tokens(line) <= QUERY_ROW_MAX_TOKENS for line in lines)


# ---------------------------------------------------------------------------
# A restart of the host, late receipts, questions
# ---------------------------------------------------------------------------


async def test_what_a_restart_cut_off_is_reported_with_her_next_turn(server, tmp_path):
    first = make_host(tmp_path, server, decision_llm=FakeDecisionLLM())
    plugin_task, _ = await first.ledger.create(
        lanlan_name="Lan", title="查上海明天的天气", dispatch_text="查上海明天的天气", source="dispatch", lang="zh"
    )
    await first.ledger.transition(plugin_task.id, "working", route="plugin", plugin_id="weather", entry_id="today")
    agent_task, _ = await first.ledger.create(
        lanlan_name="Lan", title="sort the photos", dispatch_text="sort the photos", source="dispatch", lang="en"
    )
    await first.ledger.transition(agent_task.id, "working", route="task_agent")
    child, _ = await first.ledger.create(
        lanlan_name="Lan", title="child", dispatch_text="child", source="task_agent", parent_id=agent_task.id
    )
    await first.ledger.transition(child.id, "working", route="task_agent")
    quiet, _ = await first.ledger.create(
        lanlan_name="Lan", title="quiet", dispatch_text="quiet", source="dispatch", delivery="silent"
    )
    await first.ledger.transition(quiet.id, "working", route="plugin")
    # The process ends without a shutdown.
    await first.ledger.close()

    second = make_host(tmp_path, server, decision_llm=FakeDecisionLLM())
    try:
        reports = {item.payload["task_id"]: item.payload for item in await outbox(second, "task_result")}

        # Her own tasks that are not silent; never a task agent's own task agent.
        assert set(reports) == {plugin_task.id, agent_task.id}
        assert reports[plugin_task.id]["summary"] == "猫爪中途重启了，这件事没有办完。"
        assert reports[agent_task.id]["summary"] == "NekoClaw restarted in the middle of this, so it was not finished."
        for task_id, channel in ((plugin_task.id, "user_plugin"), (agent_task.id, "task_agent")):
            payload = reports[task_id]
            assert (payload["status"], payload["success"], payload["channel"]) == ("failed", False, channel)
            # With her next turn: no model call of its own is spent on it.
            assert payload["delivery_mode"] == "passive"
        for commission in (plugin_task, agent_task, child, quiet):
            assert (await second.ledger.get(commission.id)).status == "failed"
    finally:
        await second.shutdown()


@pytest.mark.parametrize(
    "kind, delivery",
    [
        ("accepted", "passive"),
        ("told", "passive"),
        ("answered", "passive"),
        ("stopped", "passive"),
        ("nothing_active", "passive"),
        ("no_capability", "proactive"),
        ("duplicate", "proactive"),
        ("ambiguous", "proactive"),
        ("unclear_answer", "proactive"),
    ],
)
async def test_a_late_receipt_wakes_her_only_when_she_has_to_act(server, hosts, tmp_path, kind, delivery):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())

    await host.reporter.receipt("Lan", kind=kind, text="the conclusion")

    (item,) = await outbox(host, "receipt")
    assert (item.payload["receipt"], item.payload["delivery_mode"]) == (kind, delivery)


def test_a_permission_question_names_what_is_asked_and_the_answers():
    from nekoclaw.host.reports import render_question

    question = {
        "kind": "permission",
        "category": "run_command",
        "key": None,
        "detail": {"tool": "pwsh", "command": "Remove-Item old.txt"},
        "options": ["always", "session", "once", "deny"],
    }

    assert render_question("整理下载文件夹", question, "zh") == (
        "「整理下载文件夹」要先得到用户批准才能继续：运行命令 Remove-Item old.txt。请转告用户，可以回答："
        "始终允许 / 仅本会话允许 / 本次允许 / 拒绝。用户回答后，用任务派发工具把回答交回来。"
    )


def test_a_category_that_asks_every_time_names_only_the_two_answers_it_offers():
    from nekoclaw.host.reports import render_question

    question = {"kind": "permission", "category": "browser", "detail": {}, "options": ["once", "deny"]}

    text = render_question("look it up", question, "en")

    assert "operate the browser" in text
    assert "possible answers: allow this once / deny." in text
    assert "always allow" not in text and "this session" not in text


@pytest.mark.parametrize(
    "category, detail, key, expected",
    [
        ("outside_write", {"path": "D:/notes.txt"}, None, "write the file D:/notes.txt outside the working directory"),
        ("keyboard_mouse", {"title": "Keyboard and mouse"}, None, "operate the keyboard and the mouse"),
        ("mcp_server", {"title": "notes: search"}, "notes", "use the MCP server notes"),
        ("plugin_entry", {"title": "Memos: purge"}, None, "call Memos: purge"),
        ("something_new", {"tool": "frobnicate"}, None, "use frobnicate"),
    ],
)
def test_what_a_permission_question_is_about(category, detail, key, expected):
    from nekoclaw.host.reports import render_question

    question = {"kind": "permission", "category": category, "key": key, "detail": detail, "options": ["once", "deny"]}

    assert expected in render_question("task", question, "en")


def test_questions_of_a_task_agent_and_a_confirmation_read_as_sentences():
    from nekoclaw.host.reports import render_question

    asked = {
        "kind": "questions",
        "questions": [
            {"question": "按月份还是按地点分？", "options": [{"label": "月份"}, {"label": "地点"}]},
            {"question": "要不要删掉重复的？"},
        ],
    }
    report = {"kind": "report", "question": "订几点的？", "details": "只剩七点和九点"}
    confirmation = {"kind": "confirmation", "question": "真的删掉 3 条备忘吗？"}

    assert render_question("整理照片", asked, "zh") == (
        "「整理照片」有问题要问用户：1. 按月份还是按地点分？（选项：月份 / 地点） 2. 要不要删掉重复的？ "
        "请转告用户；用户回答后，用任务派发工具把回答交回来。"
    )
    assert "订几点的？ 只剩七点和九点" in render_question("订餐厅", report, "zh")
    assert render_question("清理备忘", confirmation, "zh").startswith("「清理备忘」需要用户确认：真的删掉 3 条备忘吗？")


@pytest.mark.parametrize("lang", LANGUAGES)
def test_a_question_is_rendered_in_every_language_within_its_budget(lang):
    from config.nekoclaw_settings import QUESTION_TEXT_MAX_TOKENS
    from config.prompts.prompts_nekoclaw_brain import (
        PERMISSION_DETAIL_CUT_MARK,
        PERMISSION_OPTION_NAMES,
        QUESTION_TEXTS,
    )
    from nekoclaw.host.reports import render_question
    from utils.tokenize import count_tokens

    question = {
        "kind": "permission",
        "category": "run_command",
        "detail": {"command": _long_command()},
        "options": ["always", "session", "once", "deny"],
    }
    title = _full_length_title()

    text = render_question(title, question, lang)

    assert title in text
    assert count_tokens(text) <= QUESTION_TEXT_MAX_TOKENS
    # A command too long to quote loses its middle, and the sentence says so.
    # What comes after it is whole: every answer on offer, and the
    # instruction to hand the answer back, down to its last word.
    assert PERMISSION_DETAIL_CUT_MARK[lang] in text
    assert "Remove-Item C:/Users" in text and "-Recurse -Force" in text
    for name in PERMISSION_OPTION_NAMES[lang].values():
        assert name in text
    closing = QUESTION_TEXTS[lang]["permission"].split("{options}")[1]
    assert len(closing) > 10 and text.endswith(closing)
    short = render_question(title, {**question, "detail": {"command": "dir"}}, lang)
    assert PERMISSION_DETAIL_CUT_MARK[lang] not in short
    for name in PERMISSION_OPTION_NAMES[lang].values():
        assert name in short


async def test_a_stored_question_carries_its_sentence_in_the_commissions_language(server, hosts, tmp_path):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    commission, _ = await host.ledger.create(
        lanlan_name="Lan", title="整理照片", dispatch_text="整理照片", source="dispatch", lang="zh-TW"
    )
    question = {"kind": "report", "question": "按月份還是按地點分？"}

    await host.reporter.question(commission, question)

    (item,) = await outbox(host, "question")
    assert item.payload["question"] == question
    assert item.payload["text"] == "「整理照片」有問題要問使用者：按月份還是按地點分？ 請轉告使用者；使用者回答後，用任務派發工具把回答交回來。"
    assert item.payload["delivery_mode"] == "proactive"


# ---------------------------------------------------------------------------
# What the user allowed for a session ends with the commission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ended", ["done", "failed", "cancelled", "blocked"])
async def test_a_session_answer_is_forgotten_when_its_commission_ends(hosts, tmp_path, ended):
    host = make_host(tmp_path, None, decision_llm=FakeDecisionLLM())
    hosts.append(host)
    asked: list[str] = []

    async def asker(commission_id: str, ask: Any) -> str:
        asked.append(commission_id)
        return "session"

    host.permissions.set_asker(asker)
    ending, _ = await host.ledger.create(lanlan_name="Lan", title="a", dispatch_text="a", source="dispatch")
    other, _ = await host.ledger.create(lanlan_name="Lan", title="b", dispatch_text="b", source="dispatch")
    for commission in (ending, other):
        await host.ledger.transition(commission.id, "working", route="task_agent")
        assert (await host.permissions.check(commission.id, "run_command")).allow
    assert host.permissions.session_grants(ending.id) == {("run_command", None)}

    assert (await host.ledger.transition(ending.id, ended, reason="over")).applied

    # Its own answer is gone; the other commission, still under way, keeps its.
    assert host.permissions.session_grants(ending.id) == frozenset()
    assert host.permissions.session_grants(other.id) == {("run_command", None)}
    assert asked == [ending.id, other.id]


# ---------------------------------------------------------------------------
# Counting a task agent's model requests
# ---------------------------------------------------------------------------


def _task_agent_host(hosts: list[Any], tmp_path: Any, llm: ScriptedLLM, **kwargs: Any) -> Any:
    host = make_host(
        tmp_path,
        None,
        task_agent_model=fake_model,
        decision_llm=FakeDecisionLLM(),
        runtime_factory=scripted_runtime(lambda spec: llm),
        **kwargs,
    )
    hosts.append(host)
    return host


def _report_ready(summary: str) -> list[Any]:
    return [call("report", {"status": "ready", "summary": summary}), usage(100), stop("tool_calls")]


async def test_each_model_request_of_a_task_agent_is_counted_once(hosts, tmp_path):
    counted: list[str] = []

    async def meter(commission_id: str) -> bool:
        counted.append(commission_id)
        return True

    llm = ScriptedLLM([[call("todo_write", {"todos": []}), usage(100), stop("tool_calls")], _report_ready("done")])
    host = _task_agent_host(hosts, tmp_path, llm, task_agent_meter=meter)

    receipt = await host.dispatch("sort the photos", lanlan_name="Lan", lang="en")
    await wait_status(host, receipt.commission_id, "done")

    assert counted == [receipt.commission_id] * len(llm.requests) == [receipt.commission_id] * 2


async def test_a_task_agent_whose_quota_is_used_up_fails_and_she_is_told(hosts, tmp_path):
    async def meter(commission_id: str) -> bool:
        return False

    llm = ScriptedLLM([[call("todo_write", {"todos": []}), usage(100), stop("tool_calls")], _report_ready("done")])
    host = _task_agent_host(hosts, tmp_path, llm, task_agent_meter=meter)

    receipt = await host.dispatch("sort the photos", lanlan_name="Lan", lang="en")
    failed = await wait_status(host, receipt.commission_id, "failed")

    assert failed.status_reason == "the free daily quota of the agent model is used up"
    (report,) = await outbox(host, "task_result")
    assert (report.payload["status"], report.payload["success"]) == ("failed", False)
    assert report.payload["error_message"] == "the free daily quota of the agent model is used up"
    # Counted before the first prompt: not one request went to the model,
    # and the session started for it is closed.
    assert llm.requests == []
    await wait_until(lambda: not host.task_agents._runtime.core._sessions, what="the session being closed")
    assert host.task_agents.running == []


async def test_the_quota_running_out_midway_stops_the_task_agent_after_that_request(hosts, tmp_path):
    left = [True, True, False]

    async def meter(commission_id: str) -> bool:
        return left.pop(0) if left else False

    step = [call("todo_write", {"todos": []}), usage(100), stop("tool_calls")]
    llm = ScriptedLLM([step, step, step, _report_ready("done")])
    host = _task_agent_host(hosts, tmp_path, llm, task_agent_meter=meter)

    receipt = await host.dispatch("sort the photos", lanlan_name="Lan", lang="en")
    failed = await wait_status(host, receipt.commission_id, "failed")

    assert failed.status_reason == "the free daily quota of the agent model is used up"
    # The first request was paid for before it was sent, the second as its
    # usage came in; the third found nothing left. The request already under
    # way when that was found may still have gone out, and nothing after it.
    assert left == []
    assert 3 <= len(llm.requests) <= 4


async def test_a_task_agent_stopped_while_its_first_request_is_counted_gets_no_work(hosts, tmp_path, caplog):
    import logging

    caplog.set_level(logging.WARNING, logger="nekoclaw.host.task_agents")
    llm = ScriptedLLM([_report_ready("done")])
    holder: dict[str, Any] = {}

    async def meter(commission_id: str) -> bool:
        # The user stops it while the quota file is being written.
        await holder["host"].abort(commission_id)
        return True

    host = _task_agent_host(hosts, tmp_path, llm, task_agent_meter=meter)
    holder["host"] = host

    receipt = await host.dispatch("sort the photos", lanlan_name="Lan", lang="en")
    await wait_status(host, receipt.commission_id, "cancelled")
    await wait_until(lambda: not host.task_agents._runtime.core._sessions, what="the session being closed")
    await host.task_agents.settle()

    assert llm.requests == []
    # The stop is seen before the prompt is sent, not as a prompt that fails
    # on a session already closed.
    assert not [record for record in caplog.records if "starting a task agent" in record.getMessage()]


async def test_a_meter_that_cannot_count_does_not_stop_the_work(hosts, tmp_path):
    async def meter(commission_id: str) -> bool:
        raise RuntimeError("quota file locked")

    llm = ScriptedLLM([_report_ready("done")])
    host = _task_agent_host(hosts, tmp_path, llm, task_agent_meter=meter)

    receipt = await host.dispatch("sort the photos", lanlan_name="Lan", lang="en")

    assert (await wait_status(host, receipt.commission_id, "done", "failed")).status == "done"
