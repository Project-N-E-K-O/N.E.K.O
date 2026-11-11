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

"""The task brain: dispatch receipts, the second round, stopping, query."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    outbox,
    pick_plugin,
    wait_status,
    wait_until,
)
from tests.unit.nekoclaw.plugin_server_fake import (
    FakePluginServer,
    entry,
    failure,
    finish,
    plugin,
)


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


@pytest.fixture
async def hosts() -> Any:
    made: list[Any] = []
    yield made
    for host in made:
        await host.shutdown()


def _weather(server: FakePluginServer, handler: Any = None) -> None:
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"reply": f"sunny in {args.get('city', 'town')}"})

    server.add_plugin(
        plugin(
            "weather",
            "Weather lookups for any city",
            [entry("today", "Today's weather", llm_result_fields=[])],
            keywords=["天气", "weather"],
        ),
        today=handler or today,
    )


def _host(hosts: list[Any], tmp_path: Any, server: Any, llm: FakeDecisionLLM, **kwargs: Any) -> Any:
    host = make_host(tmp_path, server, decision_llm=llm, **kwargs)
    hosts.append(host)
    return host


async def _dispatch(host: Any, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("lanlan_name", "Lan")
    kwargs.setdefault("lang", "zh")
    return await host.dispatch(text, **kwargs)


# ---------------------------------------------------------------------------
# Receipts of the first round
# ---------------------------------------------------------------------------


async def test_accepted_then_the_plugin_is_called_directly(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "Shanghai"})])
    host = _host(hosts, tmp_path, server, llm)

    receipt = await _dispatch(host, "查一下上海的天气", user_text="上海今天天气怎么样", conversation_id="conv-9")

    assert receipt.kind == "accepted"
    assert receipt.model_calls == 0
    assert receipt.text("zh") == "已受理：查一下上海的天气"
    done = await wait_status(host, receipt.commission_id, "done")
    assert (done.route, done.plugin_id, done.entry_id) == ("plugin", "weather", "today")
    assert done.result_summary == "sunny in Shanghai"
    # One call, with the commission as the run's task and the user's words in _ctx.
    (call,) = server.calls
    assert call["task_id"] == receipt.commission_id
    assert call["args"]["city"] == "Shanghai"
    ctx = server.run_requests[0]["args"]["_ctx"]
    assert ctx["lanlan_name"] == "Lan"
    assert ctx["conversation_id"] == "conv-9"
    assert ctx["latest_user_request"] == "上海今天天气怎么样"
    # One fine selection in total for the whole dispatch.
    assert llm.tiers() == ["fine"]
    (report,) = await outbox(host, "task_result")
    assert report.payload["summary"] == "sunny in Shanghai"
    assert report.payload["task_id"] == receipt.commission_id


async def test_the_result_is_stored_with_the_status_not_after_it(server, hosts, tmp_path, monkeypatch):
    # A report stored in a transaction of its own could be lost between the
    # two writes; the direct route stores it with the status change instead.
    _weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "Shanghai"})])
    host = _host(hosts, tmp_path, server, llm)

    async def separate_write(*args, **kwargs):
        raise AssertionError("the report was written apart from the status change")

    monkeypatch.setattr(host.ledger, "outbox_add", separate_write)
    receipt = await _dispatch(host, "查一下上海的天气")

    await wait_status(host, receipt.commission_id, "done")
    (report,) = await outbox(host, "task_result")
    assert report.payload["task_id"] == receipt.commission_id


async def test_the_receipt_does_not_wait_for_the_second_round(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    llm.gate = asyncio.Event()
    host = _host(hosts, tmp_path, server, llm)

    receipt = await asyncio.wait_for(_dispatch(host, "查一下上海的天气"), 2)

    # The fine selection has been asked for and has not answered yet.
    assert receipt.kind == "accepted"
    await wait_until(lambda: llm.count("fine") == 1, what="the fine selection being requested")
    assert (await host.ledger.get(receipt.commission_id)).status == "queued"
    assert server.calls == []
    llm.gate.set()
    await wait_status(host, receipt.commission_id, "done")


async def test_duplicate_receipt_names_the_one_under_way(server, hosts, tmp_path):
    release = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await release.wait()
        return finish({"reply": "sunny"})

    _weather(server, slow)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    host = _host(hosts, tmp_path, server, llm)
    first = await _dispatch(host, "查一下上海的天气", user_text="上海天气")
    await wait_status(host, first.commission_id, "working")

    second = await _dispatch(host, "查一下上海的天气", user_text="上海天气")

    assert second.kind == "duplicate"
    assert second.commission_id == first.commission_id
    assert second.text("zh") == "这件事已经在办了：查一下上海的天气（进行中）"
    assert len(await host.ledger.select()) == 1
    assert len(server.calls) == 1
    release.set()
    await wait_status(host, first.commission_id, "done")


async def test_no_capability_when_nothing_is_installed(server, hosts, tmp_path):
    llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, server, llm)

    receipt = await _dispatch(host, "查一下上海的天气")

    assert (receipt.kind, receipt.reason) == ("no_capability", "not_installed")
    assert receipt.text("zh") == "办不了：没有装能办这件事的能力"
    assert llm.calls == []
    assert await host.ledger.select() == []


async def test_no_capability_when_plugins_are_installed_but_not_running(server, hosts, tmp_path):
    server.catalog.append(plugin("weather", "Weather", status="stopped"))
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())

    receipt = await _dispatch(host, "查一下上海的天气")

    assert (receipt.kind, receipt.reason) == ("no_capability", "not_enabled")


async def test_no_capability_when_the_plugin_switch_is_off(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(), plugins_enabled=lambda: False)

    receipt = await _dispatch(host, "查一下上海的天气")

    assert (receipt.kind, receipt.reason) == ("no_capability", "not_enabled")


async def test_no_capability_when_permissions_forbid_plugins(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    host = _host(hosts, tmp_path, server, llm)
    await host.permission_store.set_mode("plugin_entry", "deny")

    receipt = await _dispatch(host, "查一下上海的天气")
    can = await host.query("能不能查天气", lanlan_name="Lan", lang="zh")

    # The plugin is installed, running and matches, and still is not used.
    assert (receipt.kind, receipt.reason) == ("no_capability", "denied")
    assert receipt.text("zh") == "办不了：权限设置里禁止了"
    assert (can.can_do, can.reason) == (False, "denied")
    assert llm.calls == []
    assert server.calls == []


async def test_no_capability_after_the_coarse_screen_finds_nothing(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(coarse=[])
    host = _host(hosts, tmp_path, server, llm)

    receipt = await _dispatch(host, "zzz qqq")

    # The one case that waits for a model: nothing matched and no familiar.
    assert (receipt.kind, receipt.reason, receipt.model_calls) == ("no_capability", "no_match", 1)
    assert llm.tiers() == ["coarse"]
    assert await host.ledger.select() == []


async def test_switched_off_host_takes_nothing(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(), enabled=lambda: False)

    receipt = await _dispatch(host, "查一下上海的天气")

    assert (receipt.kind, receipt.reason) == ("no_capability", "disabled")
    assert receipt.text("en") == "Cannot be done: NekoClaw is switched off"
    # Not even the ledger file was opened.
    assert not host.ledger.opened


async def test_slow_first_round_answers_received_and_reports_the_conclusion(server, hosts, tmp_path, monkeypatch):
    monkeypatch.setattr("config.nekoclaw_settings.DISPATCH_FIRST_ROUND_SECONDS", 0.05)
    _weather(server)
    llm = FakeDecisionLLM(coarse=[])
    llm.gate = asyncio.Event()
    host = _host(hosts, tmp_path, server, llm)

    receipt = await asyncio.wait_for(_dispatch(host, "zzz qqq"), 2)

    assert receipt.kind == "received"
    assert receipt.text("zh") == "收下了，正在判断怎么办。"
    llm.gate.set()
    await wait_until(lambda: outbox(host, "receipt"), what="the late receipt")
    (late,) = await outbox(host, "receipt")
    assert late.payload["receipt"] == "no_capability"
    assert late.payload["text"] == "办不了：没有找到能办这件事的能力"
    assert late.payload["lanlan_name"] == "Lan"


async def test_slow_first_round_that_accepts_reports_the_acceptance_and_the_commission(
    server, hosts, tmp_path, monkeypatch
):
    monkeypatch.setattr("config.nekoclaw_settings.DISPATCH_FIRST_ROUND_SECONDS", 0.05)
    _weather(server)
    llm = FakeDecisionLLM(coarse=["weather"], fine=[pick_plugin("weather", "today")])
    llm.gate = asyncio.Event()
    host = _host(hosts, tmp_path, server, llm)

    receipt = await asyncio.wait_for(_dispatch(host, "zzz qqq"), 2)

    assert receipt.kind == "received"
    llm.gate.set()
    await wait_until(lambda: outbox(host, "receipt"), what="the late receipt")
    (late,) = await outbox(host, "receipt")
    (commission,) = await host.ledger.select()
    assert late.payload["receipt"] == "accepted"
    assert late.payload["task_id"] == commission.id
    assert late.payload["text"] == "已受理：zzz qqq"
    await wait_status(host, commission.id, "done")


async def test_first_round_budget_is_about_one_second():
    from config.nekoclaw_settings import DISPATCH_FIRST_ROUND_SECONDS

    assert DISPATCH_FIRST_ROUND_SECONDS == 1.0


async def test_dispatch_text_is_capped_in_tokens_before_it_is_stored(server, hosts, tmp_path):
    from config.nekoclaw_settings import COMMISSION_TITLE_MAX_TOKENS, DISPATCH_TEXT_MAX_TOKENS
    from utils.tokenize import count_tokens

    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))

    receipt = await _dispatch(host, "天气 " + " ".join(f"词{n}" for n in range(3000)))

    commission = await host.ledger.get(receipt.commission_id)
    assert count_tokens(commission.dispatch_text) <= DISPATCH_TEXT_MAX_TOKENS
    assert count_tokens(commission.title) <= COMMISSION_TITLE_MAX_TOKENS


# ---------------------------------------------------------------------------
# The second round overturning the receipt
# ---------------------------------------------------------------------------


async def test_accepted_but_nothing_fits_is_corrected_at_once(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(fine=[{"route": "familiar", "reason": "no entry fits"}])
    host = _host(hosts, tmp_path, server, llm)

    receipt = await _dispatch(host, "把天气预报画成一张海报")

    assert receipt.kind == "accepted"
    failed = await wait_status(host, receipt.commission_id, "failed")
    assert "nothing can do this" in failed.status_reason
    (correction,) = await outbox(host, "correction")
    assert correction.payload["reason"] == "no_capability"
    assert correction.payload["task_id"] == receipt.commission_id
    assert correction.payload["text"] == "办不了：没有找到能办这件事的能力"
    assert server.calls == []


async def test_accepted_but_a_repeat_after_all_is_withdrawn_and_corrected(server, hosts, tmp_path):
    release = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await release.wait()
        return finish({"reply": "sunny"})

    _weather(server, slow)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today"), {"route": "duplicate", "duplicate_of": 1}])
    host = _host(hosts, tmp_path, server, llm)
    first = await _dispatch(host, "查一下上海的天气")
    await wait_status(host, first.commission_id, "working")

    second = await _dispatch(host, "看看魔都今天气象如何")

    assert second.kind == "accepted"
    withdrawn = await wait_status(host, second.commission_id, "cancelled")
    assert first.commission_id in withdrawn.status_reason
    (correction,) = await outbox(host, "correction")
    assert (correction.payload["reason"], correction.payload["detail"]) == ("duplicate", first.commission_id)
    assert "已经在办了" in correction.payload["text"]
    assert len(server.calls) == 1
    release.set()
    await wait_status(host, first.commission_id, "done")


async def test_a_plugin_that_asked_for_confirmation_runs_again_when_asked_again(server, hosts, tmp_path):
    answers = [
        finish({"needs_confirmation": True, "reply": "确认定明早7点的闹钟吗？"}),
        finish({"reply": "闹钟已定好"}),
    ]

    async def set_alarm(args: dict[str, Any]) -> dict[str, Any]:
        return answers.pop(0)

    server.add_plugin(
        plugin("alarm", "Alarms 闹钟", [entry("set", "Set an alarm")], keywords=["闹钟"]), set=set_alarm
    )
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("alarm", "set")]))
    first = await _dispatch(host, "把明早7点的闹钟定上", user_text="明早7点叫我")
    await wait_status(host, first.commission_id, "blocked")

    # The user confirmed; she dispatches the same request once more.
    second = await _dispatch(host, "把明早7点的闹钟定上", user_text="明早7点叫我")

    assert second.kind == "accepted"
    assert second.commission_id != first.commission_id
    done = await wait_status(host, second.commission_id, "done")
    assert done.result_summary == "闹钟已定好"
    assert len(server.calls) == 2


async def test_failed_plugin_call_ends_failed_with_a_report(server, hosts, tmp_path):
    async def broken(args: dict[str, Any]) -> dict[str, Any]:
        return failure("station offline")

    _weather(server, broken)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))

    receipt = await _dispatch(host, "查一下上海的天气")

    failed = await wait_status(host, receipt.commission_id, "failed")
    assert "station offline" in failed.result_summary
    (report,) = await outbox(host, "task_result")
    assert (report.payload["status"], report.payload["success"]) == ("failed", False)


async def test_a_stated_preference_is_recorded_and_used_next_time(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(
        fine=[
            pick_plugin("weather", "today", {"city": "Shanghai"}, preference="查天气默认查上海"),
            pick_plugin("weather", "today", {"city": "Shanghai"}),
        ]
    )
    host = _host(hosts, tmp_path, server, llm)

    first = await _dispatch(host, "查天气，他说以后默认都查上海")
    await wait_status(host, first.commission_id, "done")
    (entry_,) = await host.preferences.list()
    assert (entry_.text, entry_.origin, entry_.active) == ("查天气默认查上海", "stated", True)

    second = await _dispatch(host, "明天天气怎么样")
    await wait_status(host, second.commission_id, "done")

    assert "PREFERENCES:\n- 查天气默认查上海" in llm.last("fine")[1]["content"]


async def test_a_stated_reporting_preference_applies_to_unrelated_tasks(server, hosts, tmp_path):
    _weather(server)
    server.add_plugin(
        plugin("lamp", "Controls the desk lamp 台灯", [entry("switch", "Switch")], keywords=["台灯"]),
        switch=_returns(finish({"reply": "台灯已关"})),
    )
    llm = FakeDecisionLLM(
        fine=[
            pick_plugin("weather", "today", {"city": "Shanghai"}, preference="结果一律用列表汇报"),
            pick_plugin("lamp", "switch"),
        ]
    )
    host = _host(hosts, tmp_path, server, llm)

    first = await _dispatch(host, "查一下上海的天气，他说以后结果一律用列表汇报")
    await wait_status(host, first.commission_id, "done")
    (entry_,) = await host.preferences.list()
    assert (entry_.text, entry_.kind) == ("结果一律用列表汇报", "report")

    second = await _dispatch(host, "把台灯关掉")
    await wait_status(host, second.commission_id, "done")

    # No word in common, and still given: it is about how every task is reported.
    assert "PREFERENCES:\n- 结果一律用列表汇报" in llm.last("fine")[1]["content"]


# ---------------------------------------------------------------------------
# Stopping
# ---------------------------------------------------------------------------


async def test_plugins_forbidden_while_the_model_chooses_are_not_called(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    llm.gate = asyncio.Event()
    host = _host(hosts, tmp_path, server, llm)

    receipt = await _dispatch(host, "查一下上海的天气")
    assert receipt.kind == "accepted"
    await wait_until(lambda: _asked(llm), what="the fine selection being asked")
    await host.permission_store.set_mode("plugin_entry", "deny")
    llm.gate.set()
    await host.brain.settle()

    failed = await host.ledger.get(receipt.commission_id)
    assert failed.status == "failed"
    assert server.calls == []
    (correction,) = await outbox(host, "correction")
    assert correction.payload["reason"] == "no_capability"


async def _asked(llm: FakeDecisionLLM) -> bool:
    return llm.count("fine") >= 1


async def test_deferred_commissions_kept_over_a_restart_still_time_out(server, hosts, tmp_path):
    async def add(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"deferred": True, "reply": "later"})

    server.add_plugin(plugin("memo", "Memos 提醒", [entry("add", "Add a reminder")]), add=add)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("memo", "add")]))
    receipt = await _dispatch(host, "五点提醒我开会")
    await wait_until(
        lambda: _deferred(host, receipt.commission_id), what="the commission being marked deferred"
    )
    # A new process: its timers are gone, the deadline is still ahead.
    for timer in host.brain._deferred_timers.values():
        timer.cancel()
    host.brain._deferred_timers.clear()
    await host.ledger.update(receipt.commission_id, deferred_until=host.ledger._clock() + 0.2)

    # Anything that sweeps finds it and sets its timer again.
    assert await host.brain.expire_deferred() == 0
    assert receipt.commission_id in host.brain._deferred_timers
    await wait_status(host, receipt.commission_id, "failed", timeout=5)


async def _slow_weather(server: FakePluginServer) -> asyncio.Event:
    started = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return finish({"reply": "never"})

    _weather(server, slow)
    return started


async def test_stop_through_dispatch_cancels_the_plugin_run(server, hosts, tmp_path):
    started = await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    first = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)
    (run,) = server.runs()

    receipt = await _dispatch(host, "天气别查了")

    assert receipt.kind == "stopped"
    assert receipt.commission_id == first.commission_id
    assert receipt.text("zh") == "已叫停：查一下上海的天气"
    assert (await host.ledger.get(first.commission_id)).status == "cancelled"
    # The plugin server was asked to cancel the run, and passed it on to the plugin.
    assert server.cancelled == [("weather", run.run_id)]
    assert server.run(run.run_id).status == "cancel_requested"
    # She asked for the stop herself, so no report repeats it.
    assert await outbox(host, "task_result") == []


async def test_a_result_arriving_after_the_stop_does_not_overwrite_it(server, hosts, tmp_path):
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await release.wait()
        return finish({"reply": "sunny after all"})

    _weather(server, slow)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    first = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)

    # Stop it in the ledger only, leaving the call to finish on its own, the
    # way a result can race a stop.
    moved = await host.ledger.transition(first.commission_id, "cancelled", reason="stopped by the user")
    assert moved.applied
    release.set()
    await host.brain.settle()

    after = await host.ledger.get(first.commission_id)
    assert (after.status, after.status_reason, after.result_summary) == ("cancelled", "stopped by the user", None)
    assert await outbox(host, "task_result") == []
    refused = [event.payload for event in await host.ledger.events(first.commission_id) if event.type == "transition_refused"]
    assert refused == [{"from": "cancelled", "to": "done", "reason": "run_succeeded"}]


async def test_stop_from_the_board_reports_it_to_her(server, hosts, tmp_path):
    started = await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    first = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)

    stopped = await host.abort(first.commission_id, reason="stopped on the board")

    assert stopped.status == "cancelled"
    (report,) = await outbox(host, "task_result")
    assert (report.payload["status"], report.payload["success"]) == ("cancelled", False)


async def test_stopping_twice_or_an_unknown_commission_is_harmless(server, hosts, tmp_path):
    started = await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    first = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)

    await host.abort(first.commission_id, reason="first")
    again = await host.abort(first.commission_id, reason="second")

    assert again.status == "cancelled" and again.status_reason == "first"
    assert await host.abort("no-such-commission") is None
    assert len(await outbox(host, "task_result")) == 1


async def test_being_stopped_twice_on_the_same_entry_becomes_an_inferred_preference(server, hosts, tmp_path):
    started = await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))

    for text in ("查一下上海的天气", "查一下北京的天气"):
        started.clear()
        receipt = await _dispatch(host, text)
        await asyncio.wait_for(started.wait(), 5)
        await host.abort(receipt.commission_id)
        if text == "查一下上海的天气":
            (entry_,) = await host.preferences.list()
            assert (entry_.origin, entry_.active, entry_.observations) == ("inferred", False, 1)

    (entry_,) = await host.preferences.list()
    assert (entry_.origin, entry_.active, entry_.observations) == ("inferred", True, 2)
    assert "weather.today" in entry_.text


async def test_stop_with_nothing_under_way(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())

    receipt = await _dispatch(host, "算了")

    assert receipt.kind == "nothing_active"
    assert receipt.text("zh") == "现在没有在办的事。"


async def test_stop_without_saying_which_lists_the_candidates(server, hosts, tmp_path):
    started = await _slow_weather(server)
    server.add_plugin(plugin("lamp", "Controls the desk lamp 台灯", [entry("switch", "Switch")], keywords=["台灯"]))
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today"), pick_plugin("weather", "today")])
    host = _host(hosts, tmp_path, server, llm)
    one = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)
    two = await _dispatch(host, "查一下北京的天气")
    await wait_status(host, two.commission_id, "working")

    receipt = await _dispatch(host, "算了")

    assert receipt.kind == "ambiguous"
    assert {item.id for item in receipt.commissions} == {one.commission_id, two.commission_id}
    text = receipt.text("zh")
    assert "查一下上海的天气" in text and "查一下北京的天气" in text
    # Nothing was stopped.
    assert (await host.ledger.get(one.commission_id)).status == "working"


async def test_stop_without_saying_which_leaves_sub_familiars_out(server, hosts, tmp_path):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    lead, _created = await host.ledger.create(
        lanlan_name="Lan", title="整理照片", dispatch_text="整理下载文件夹里的照片", user_text="帮我整理照片",
        source="dispatch",
    )
    await host.ledger.transition(lead.id, "working", route="familiar")
    children = []
    for number in range(2):
        child, _created = await host.ledger.create(
            lanlan_name="Lan", title=f"子任务{number}", dispatch_text=f"按月份分第{number}批照片",
            user_text="帮我整理照片", source="familiar", parent_id=lead.id,
        )
        await host.ledger.transition(child.id, "working", route="familiar")
        children.append(child)

    receipt = await _dispatch(host, "算了，别弄了")

    # Her familiar started the other two; she only ever dispatched this one.
    assert (receipt.kind, receipt.commission_id) == ("stopped", lead.id)
    assert (await host.ledger.get(lead.id)).status == "cancelled"


async def test_a_new_dispatch_is_not_taken_for_a_repeat_of_a_sub_familiar(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    lead, _created = await host.ledger.create(
        lanlan_name="Lan", title="出门准备", dispatch_text="帮他准备出门", user_text="我要出门了", source="dispatch",
    )
    await host.ledger.transition(lead.id, "working", route="familiar")
    child, _created = await host.ledger.create(
        lanlan_name="Lan", title="天气", dispatch_text="查一下上海的天气然后汇总",
        user_text="我要出门了", source="familiar", parent_id=lead.id,
    )
    await host.ledger.transition(child.id, "working", route="familiar")

    receipt = await _dispatch(host, "查一下上海的天气", user_text="我要出门了")

    assert receipt.kind == "accepted"
    await wait_status(host, receipt.commission_id, "done")


# ---------------------------------------------------------------------------
# Deferred results
# ---------------------------------------------------------------------------


async def test_deferred_result_stays_open_until_the_plugin_calls_back(server, hosts, tmp_path):
    bound: list[dict[str, Any]] = []

    async def add(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"deferred": True, "reminder_id": "r-7", "reply": "will remind at five"})

    async def bind_task(args: dict[str, Any]) -> dict[str, Any]:
        bound.append({key: value for key, value in args.items() if key != "_ctx"})
        return finish({"reply": "bound"})

    server.add_plugin(
        plugin("memo", "Memos and reminders 提醒", [entry("add", "Add a reminder"), entry("bind_task", "Bind")]),
        add=add,
        bind_task=bind_task,
    )
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("memo", "add")]))

    receipt = await _dispatch(host, "五点提醒我开会")
    await wait_until(lambda: bound, what="the deferred work being bound")

    commission = await host.ledger.get(receipt.commission_id)
    assert commission.status == "working"
    assert commission.deferred_until is not None
    assert bound == [{"reminder_id": "r-7", "agent_task_id": receipt.commission_id}]
    assert await outbox(host, "task_result") == []

    assert await host.complete_deferred(receipt.commission_id) is True
    assert (await host.ledger.get(receipt.commission_id)).status == "done"
    assert await host.complete_deferred(receipt.commission_id) is False


async def test_deferred_result_whose_callback_never_comes_fails(server, hosts, tmp_path):
    async def add(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"deferred": True, "reply": "later"})

    server.add_plugin(plugin("memo", "Memos 提醒", [entry("add", "Add a reminder")]), add=add)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("memo", "add")]))
    receipt = await _dispatch(host, "五点提醒我开会")
    await wait_until(
        lambda: _deferred(host, receipt.commission_id), what="the commission being marked deferred"
    )

    await host.ledger.update(receipt.commission_id, deferred_until=host.ledger._clock() - 1)
    assert await host.brain.expire_deferred() == 1

    failed = await host.ledger.get(receipt.commission_id)
    assert (failed.status, failed.status_reason) == ("failed", "Deferred task timeout (callback not received)")
    # Reported once, the way a failed plugin call is, and its timer is gone.
    (report,) = await outbox(host, "task_result")
    assert (report.payload["status"], report.payload["success"]) == ("failed", False)
    assert report.payload["task_id"] == receipt.commission_id
    assert report.payload["channel"] == "user_plugin"
    assert "callback not received" in report.payload["error_message"]
    assert report.payload["source_kind"] == "plugin"
    assert receipt.commission_id not in host.brain._deferred_timers
    assert await host.brain.expire_deferred() == 0
    assert len(await outbox(host, "task_result")) == 1


@pytest.mark.parametrize("delivery", ["passive", "silent"])
async def test_a_deferred_timeout_is_reported_the_way_the_plugin_asked(server, hosts, tmp_path, delivery):
    async def add(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"deferred": True, "reply": "later"}, delivery=delivery)

    server.add_plugin(plugin("memo", "Memos 提醒", [entry("add", "Add a reminder")]), add=add)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("memo", "add")]))
    receipt = await _dispatch(host, "五点提醒我开会")
    await wait_until(
        lambda: _deferred(host, receipt.commission_id), what="the commission being marked deferred"
    )

    await host.ledger.update(receipt.commission_id, deferred_until=host.ledger._clock() - 1)
    assert await host.brain.expire_deferred() == 1

    reports = await outbox(host, "task_result")
    if delivery == "silent":
        assert reports == []
    else:
        (report,) = reports
        assert report.payload["delivery_mode"] == "passive"


async def _deferred(host: Any, commission_id: str) -> bool:
    commission = await host.ledger.get(commission_id)
    return commission is not None and commission.deferred_until is not None


async def test_completing_something_that_is_not_deferred_is_refused(server, hosts, tmp_path):
    started = await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    receipt = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)

    assert await host.complete_deferred(receipt.commission_id) is False
    assert (await host.ledger.get(receipt.commission_id)).status == "working"


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


async def test_progress_query_reads_the_ledger_and_calls_no_model(server, hosts, tmp_path):
    started = await _slow_weather(server)
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today")])
    host = _host(hosts, tmp_path, server, llm)
    receipt = await _dispatch(host, "查一下上海的天气")
    await asyncio.wait_for(started.wait(), 5)
    calls_before = llm.count()
    plugin_fetches = server.plugins_requests

    answer = await host.query("现在在办什么", lanlan_name="Lan", lang="zh")

    assert llm.count() == calls_before
    assert answer.model_calls == 0
    assert answer.text == "在办的事：\n- 查一下上海的天气 (进行中)"
    assert [item.id for item in answer.commissions] == [receipt.commission_id]
    # Not even the plugin list is needed for it.
    assert server.plugins_requests == plugin_fetches


async def test_query_finds_a_finished_commission_and_its_result(server, hosts, tmp_path):
    _weather(server)
    server.add_plugin(
        plugin("lamp", "Controls the desk lamp 台灯", [entry("switch", "Switch")], keywords=["台灯"]),
        switch=_returns(finish({"reply": "台灯已关"})),
    )
    llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "Shanghai"}), pick_plugin("lamp", "switch")])
    host = _host(hosts, tmp_path, server, llm)
    weather = await _dispatch(host, "查一下上海的天气")
    await wait_status(host, weather.commission_id, "done")
    lamp = await _dispatch(host, "把台灯关掉")
    await wait_status(host, lamp.commission_id, "done")

    answer = await host.query("台灯关了吗", lanlan_name="Lan", lang="zh")

    lines = answer.text.splitlines()
    assert lines[0] == "最近办完的："
    # The one the question is about comes first.
    assert lines[1] == "- 把台灯关掉 (已完成) 结果：台灯已关"
    assert lines[2] == "- 查一下上海的天气 (已完成) 结果：sunny in Shanghai"


def _returns(envelope: dict[str, Any]) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return envelope

    return handler


async def test_query_with_nothing_on_record(server, hosts, tmp_path):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())

    answer = await host.query("", lanlan_name="Lan", lang="en")

    assert answer.text == "There is no task on record."
    assert answer.commissions == []


async def test_query_lists_only_her_own_commissions(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today")]))
    mine = await _dispatch(host, "查一下上海的天气", lanlan_name="Lan")
    await wait_status(host, mine.commission_id, "done")

    other = await host.query("", lanlan_name="Tian", lang="zh")

    assert other.text == "没有任务记录。"


async def test_query_view_has_at_most_eight_rows_and_400_tokens(server, hosts, tmp_path):
    from config.nekoclaw_settings import QUERY_LEDGER_ROWS, QUERY_RESULT_MAX_TOKENS, QUERY_ROW_MAX_TOKENS
    from utils.tokenize import count_tokens

    assert (QUERY_LEDGER_ROWS, QUERY_RESULT_MAX_TOKENS) == (8, 400)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    long = "很长很长的结果 " * 300
    for number in range(20):
        commission, _created = await host.ledger.create(
            lanlan_name="Lan", title=f"任务{number} " + "标题 " * 100, dispatch_text=f"任务{number}", source="dispatch"
        )
        await host.ledger.transition(commission.id, "working")
        if number % 2:
            await host.ledger.transition(commission.id, "done", result_summary=long)

    answer = await host.query("", lanlan_name="Lan", lang="zh")

    rows = [line for line in answer.text.splitlines() if line.startswith("- ")]
    assert len(answer.commissions) == QUERY_LEDGER_ROWS
    assert len(rows) <= QUERY_LEDGER_ROWS
    assert count_tokens(answer.text) <= QUERY_RESULT_MAX_TOKENS
    assert all(count_tokens(row) <= QUERY_ROW_MAX_TOKENS for row in rows)


async def test_can_it_be_done_agrees_with_the_dispatch_receipt_and_files_nothing(server, hosts, tmp_path):
    _weather(server)
    llm = FakeDecisionLLM(coarse=[], fine=[pick_plugin("weather", "today")])
    host = _host(hosts, tmp_path, server, llm)

    can_weather = await host.query("能不能查天气", lanlan_name="Lan", lang="zh")
    unsure = await host.query("能不能 zzz qqq", lanlan_name="Lan", lang="zh")

    assert (can_weather.can_do, can_weather.model_calls) == (True, 0)
    assert can_weather.text == "能办：有对得上的插件（weather）。"
    # Nothing matches locally and there is no familiar. A dispatch would let
    # the small model look before saying no; a query asks no model, so it does
    # not say no either.
    assert (unsure.can_do, unsure.reason, unsure.model_calls) == (None, "", 0)
    assert unsure.text == "没找到现成的能力，真要办可以派发试试。"
    assert llm.calls == []
    # Nothing was filed and nothing ran.
    assert await host.ledger.select() == []
    assert server.calls == []
    # The dispatch of the first sentence is accepted, as the query said; the
    # second one gets its conclusion from the coarse screen.
    assert (await _dispatch(host, "查天气")).kind == "accepted"
    assert (await _dispatch(host, "zzz qqq")).kind == "no_capability"
    assert llm.tiers().count("coarse") == 1


async def test_can_it_be_done_with_nothing_installed_is_a_plain_no(server, hosts, tmp_path):
    llm = FakeDecisionLLM()
    host = _host(hosts, tmp_path, server, llm)

    answer = await host.query("can you book a flight", lanlan_name="Lan", lang="en")

    # No model is needed to know this, and a dispatch says the same.
    assert (answer.can_do, answer.reason, answer.model_calls) == (False, "not_installed", 0)
    assert answer.text == "Cannot be done: nothing that can do this is installed"
    assert (await _dispatch(host, "book a flight")).reason == "not_installed"
    assert llm.calls == []


async def test_can_it_be_done_with_a_familiar_available(server, hosts, tmp_path):
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(), familiar_model=fake_model)

    answer = await host.query("can you tidy my desktop", lanlan_name="Lan", lang="en")

    assert (answer.can_do, answer.model_calls) == (True, 0)
    assert answer.text == "Can be done: a familiar can take it."


async def test_full_language_codes_are_understood(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "台北"})]))

    receipt = await _dispatch(host, "查一下台北的天气", lang="zh-HK")
    done = await wait_status(host, receipt.commission_id, "done")
    answer = await host.query("", lanlan_name="Lan", lang="zh_CN")

    assert done.lang == "zh-TW"
    assert receipt.text("zh-CN") == "已受理：查一下台北的天气"
    assert receipt.text("ja-JP") == "受け付けました：查一下台北的天气"
    assert answer.text.startswith("最近办完的：")
    assert (await host.query("", lanlan_name="Nobody", lang="xx")).text == "There is no task on record."


async def test_receipts_render_in_every_language(server, hosts, tmp_path):
    from config.prompts.prompts_nekoclaw_brain import (
        COMMISSION_STATUS_WORDS,
        NO_CAPABILITY_REASONS,
        QUERY_TEXTS,
        RECEIPT_TEXTS,
    )
    from nekoclaw.host.ledger import STATUSES

    languages = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    for table in (COMMISSION_STATUS_WORDS, NO_CAPABILITY_REASONS, QUERY_TEXTS, RECEIPT_TEXTS):
        assert set(table) == languages
        assert len({frozenset(values) for values in table.values()}) == 1
    assert set(COMMISSION_STATUS_WORDS["en"]) == set(STATUSES)
    assert set(NO_CAPABILITY_REASONS["en"]) == {"disabled", "not_installed", "not_enabled", "denied", "no_match"}
    assert set(RECEIPT_TEXTS["en"]) == {
        "accepted", "received", "duplicate", "no_capability", "stopped", "told", "answered",
        "ambiguous", "nothing_active", "unclear_answer",
    }


# ---------------------------------------------------------------------------
# A caller that goes away
# ---------------------------------------------------------------------------


async def test_run_is_cancelled_when_its_caller_is_cancelled_while_waiting(server, hosts, tmp_path):
    started = await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())

    calling = asyncio.ensure_future(
        host.runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    await wait_until(lambda: host.runner.active_runs("c1"), what="the run being known to the caller")
    (run,) = server.runs()
    assert host.runner.active_runs("c1") == [run.run_id]
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)

    await wait_until(lambda: server.cancelled == [("weather", run.run_id)], what="the run being cancelled")
    assert calling.cancelled()
    assert host.runner.active_runs("c1") == []


async def test_run_is_cancelled_when_its_caller_is_cancelled_while_the_request_is_under_way(server, hosts, tmp_path):
    started = await _slow_weather(server)
    answer_may_return = asyncio.Event()

    class SlowAnswers:
        """A client whose POST has reached the server but whose answer is still on its way."""

        def __init__(self, **kwargs: Any) -> None:
            self._client = server.client(**kwargs)

        async def __aenter__(self) -> Any:
            await self._client.__aenter__()
            return self

        async def __aexit__(self, *exc: Any) -> Any:
            return await self._client.__aexit__(*exc)

        async def post(self, url: str, **kwargs: Any) -> Any:
            response = await self._client.post(url, **kwargs)
            if url.endswith("/runs"):
                await answer_may_return.wait()
            return response

        async def get(self, url: str, **kwargs: Any) -> Any:
            return await self._client.get(url, **kwargs)

    host = _host(hosts, tmp_path, server, FakeDecisionLLM(), client_factory=SlowAnswers)

    calling = asyncio.ensure_future(
        host.runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    (run,) = server.runs()
    # The run exists on the plugin server; the caller does not know its id yet.
    assert host.runner.active_runs("c1") == []
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)
    assert calling.cancelled()
    assert server.cancelled == []

    answer_may_return.set()

    # The answer is still read, and the run it names is cancelled.
    await wait_until(lambda: server.cancelled == [("weather", run.run_id)], what="the run being cancelled")


async def test_run_is_cancelled_when_its_caller_is_cancelled_right_after_it_was_created(server, hosts, tmp_path):
    await _slow_weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())
    created = asyncio.Event()

    async def run_created(run_id: str) -> None:
        created.set()
        # The caller is cancelled here, before it has begun to wait for the run.
        await asyncio.Event().wait()

    calling = asyncio.ensure_future(
        host.runner.run_entry(
            commission_id="c1", plugin_id="weather", entry_id="today", args={}, on_run_created=run_created
        )
    )
    await asyncio.wait_for(created.wait(), 5)
    (run,) = server.runs()
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)

    await wait_until(lambda: server.cancelled == [("weather", run.run_id)], what="the run being cancelled")
    assert host.runner.active_runs("c1") == []


async def test_a_failing_run_listener_does_not_cost_the_result(server, hosts, tmp_path):
    _weather(server)
    host = _host(hosts, tmp_path, server, FakeDecisionLLM())

    async def run_created(run_id: str) -> None:
        raise RuntimeError("could not record the run")

    outcome = await host.runner.run_entry(
        commission_id="c1", plugin_id="weather", entry_id="today", args={"city": "Lyon"}, on_run_created=run_created
    )

    assert outcome.success is True
    assert outcome.data == {"reply": "sunny in Lyon"}


@pytest.mark.parametrize("how", ["turned into another error", "absorbed"])
async def test_run_is_cancelled_even_when_the_request_layer_hides_the_cancellation(server, hosts, tmp_path, how):
    # A layer below can turn a cancellation into an error of its own, or
    # swallow it and answer as if nothing had happened. Either way the caller
    # is gone and the run has to be cancelled.
    started = await _slow_weather(server)
    polling = asyncio.Event()

    class HidesCancellation:
        def __init__(self, **kwargs: Any) -> None:
            self._client = server.client(**kwargs)

        async def __aenter__(self) -> Any:
            await self._client.__aenter__()
            return self

        async def __aexit__(self, *exc: Any) -> Any:
            return await self._client.__aexit__(*exc)

        async def post(self, url: str, **kwargs: Any) -> Any:
            return await self._client.post(url, **kwargs)

        async def get(self, url: str, **kwargs: Any) -> Any:
            response = await self._client.get(url, **kwargs)
            if "/runs/" not in url or "/export" in url:
                return response
            polling.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                if how == "absorbed":
                    return response
                raise AssertionError("the response was not complete") from None
            return response

    host = _host(hosts, tmp_path, server, FakeDecisionLLM(), client_factory=HidesCancellation)

    calling = asyncio.ensure_future(
        host.runner.run_entry(commission_id="c1", plugin_id="weather", entry_id="today", args={})
    )
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(polling.wait(), 5)
    (run,) = server.runs()
    calling.cancel()
    done, _pending = await asyncio.wait({calling}, timeout=5)

    assert done, "the caller kept polling after it was cancelled"
    assert calling.cancelled()
    await wait_until(lambda: server.cancelled == [("weather", run.run_id)], what="the run being cancelled")
