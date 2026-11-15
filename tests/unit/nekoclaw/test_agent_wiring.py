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

"""The agent process around the host: routes, the task HUD's view, reports, built-in tools.

The host is real, with its files under the test's own directory, a fake
plugin server and a scripted decision model. Nothing here starts the agent
server or opens a socket: the route functions are called as plain functions
and what they would send to the main server is collected. Nothing touches a
desktop or a browser either.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, pick_plugin, wait_until
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, finish, plugin


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


class Wiring:
    """What a test needs of the agent process, set up without starting it."""

    def __init__(self, monkeypatch: Any, tmp_path: Any, server: FakePluginServer) -> None:
        from app.agent_server import _shared, api_routes, api_runtime, nekoclaw_mount, plugin_host, results
        from nekoclaw.host import mount

        self.shared = _shared
        self.runtime = api_runtime
        self.routes = api_routes
        self.mount_module = nekoclaw_mount
        self.host_mount = mount
        self.llm = FakeDecisionLLM(fine=[pick_plugin("weather", "today", {"city": "Shanghai"})])
        self.events: list[dict[str, Any]] = []
        self.bridge_up = True

        async def send(event: dict[str, Any]) -> bool:
            if not self.bridge_up:
                return False
            self.events.append(event)
            return True

        async def emit(event_type: str, lanlan_name: Any, **payload: Any) -> None:
            await send({"event_type": event_type, "lanlan_name": lanlan_name, **payload})

        async def friendly_name(plugin_id: str) -> str:
            return {"weather": "Weather"}.get(plugin_id, plugin_id)

        monkeypatch.setattr(results, "_send_main_event", send)
        monkeypatch.setattr(nekoclaw_mount, "_send_main_event", send)
        monkeypatch.setattr(nekoclaw_mount, "_emit_main_event", emit)
        monkeypatch.setattr(api_runtime, "_emit_main_event", emit)
        monkeypatch.setattr(plugin_host, "_get_plugin_friendly_name", friendly_name)
        monkeypatch.setattr(_shared.Modules, "analyzer_enabled", True)
        monkeypatch.setattr(_shared.Modules, "agent_flags", {"user_plugin_enabled": True})
        monkeypatch.setattr(nekoclaw_mount, "_filled_from", None)
        monkeypatch.setattr(nekoclaw_mount, "_restore_failed_at", None)
        monkeypatch.setattr(nekoclaw_mount, "_updates", None)
        monkeypatch.setattr(nekoclaw_mount, "_updates_worker", None)
        nekoclaw_mount.hud.clear()
        api_runtime._turn_checks_seen.clear()
        mount.reset_for_tests()
        mount.configure(
            root=tmp_path / "nekoclaw",
            client_factory=server.client,
            decision_llm=self.llm,
            enabled=nekoclaw_mount.nekoclaw_on,
            plugins_enabled=nekoclaw_mount._plugins_enabled,
            send_report=nekoclaw_mount.send_report,
            on_progress=nekoclaw_mount._on_progress,
            on_commission=nekoclaw_mount._on_commission_change,
        )

    def of(self, event_type: str) -> list[dict[str, Any]]:
        return [event for event in self.events if event.get("event_type") == event_type]

    def updates(self, task_id: str | None = None) -> list[dict[str, Any]]:
        tasks = [event["task"] for event in self.of("task_update")]
        return [task for task in tasks if task_id is None or task["id"] == task_id]

    async def close(self) -> None:
        worker = self.mount_module._updates_worker
        await self.host_mount.shutdown_host()
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.shared.Modules._persistent_tasks.discard(worker)
        self.mount_module.hud.clear()
        self.host_mount.reset_for_tests()


@pytest.fixture
async def wiring(monkeypatch: Any, tmp_path: Any, server: FakePluginServer) -> Any:
    built = Wiring(monkeypatch, tmp_path, server)
    yield built
    await built.close()


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


# ---------------------------------------------------------------------------
# Her two tools, as routes
# ---------------------------------------------------------------------------


async def test_dispatch_route_returns_the_receipt_and_the_task_runs(wiring, server):
    _weather(server)

    answer = await wiring.runtime.nekoclaw_dispatch(
        {"text": "查一下上海的天气", "lanlan_name": "Lan", "user_text": "上海天气怎么样", "language": "zh-CN"}
    )

    assert (answer["success"], answer["kind"], answer["text"]) == (True, "accepted", "已受理：查一下上海的天气")
    assert answer["model_calls"] == 0
    task_id = answer["commission_id"]
    await wait_until(lambda: any(task["status"] == "completed" for task in wiring.updates(task_id)), what="the result")
    # How it ended reaches the main server as the task_result event it has always received.
    await wait_until(lambda: wiring.of("task_result"), what="the report")
    (report,) = wiring.of("task_result")
    assert (report["task_id"], report["channel"], report["status"]) == (task_id, "user_plugin", "completed")
    assert report["summary"] == "sunny in Shanghai"
    assert report["lanlan_name"] == "Lan"
    assert report["timestamp"].endswith("Z")
    # One fine selection for the whole dispatch.
    assert wiring.llm.tiers() == ["fine"]


async def test_the_hud_follows_a_task_from_filing_to_its_end_in_order(wiring, server):
    _weather(server)

    answer = await wiring.runtime.nekoclaw_dispatch({"text": "查一下上海的天气", "lanlan_name": "Lan"})

    task_id = answer["commission_id"]
    await wait_until(lambda: any(task["status"] == "completed" for task in wiring.updates(task_id)), what="the end")
    updates = wiring.updates(task_id)
    assert [(task["status"], task["type"]) for task in updates] == [
        ("queued", "task"),
        ("running", "user_plugin"),
        ("completed", "user_plugin"),
    ]
    running = updates[1]
    assert running["params"] == {
        "description": "查一下上海的天气",
        "instruction": "查一下上海的天气",
        "plugin_id": "weather",
        "entry_id": "today",
        "plugin_name": "Weather",
    }
    assert running["lanlan_name"] == "Lan"
    assert running["start_time"].endswith("Z") and "end_time" not in running
    assert updates[2]["end_time"].endswith("Z")
    # Every update names the character whose HUD it belongs to.
    assert {event["lanlan_name"] for event in wiring.of("task_update")} == {"Lan"}


async def test_dispatch_with_the_master_switch_off_builds_nothing(wiring, monkeypatch):
    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)

    answer = await wiring.runtime.nekoclaw_dispatch({"text": "查天气", "lanlan_name": "Lan", "language": "zh-CN"})

    assert (answer["kind"], answer["reason"], answer["text"]) == ("no_capability", "disabled", "办不了：猫爪没有开")
    assert wiring.host_mount.peek_host() is None


@pytest.mark.parametrize(
    ("flags", "on"),
    [
        ({"user_plugin_enabled": True}, True),
        ({"computer_use_enabled": True}, True),
        ({"browser_use_enabled": True}, True),
        ({}, False),
        ({"user_plugin_enabled": False, "computer_use_enabled": False, "browser_use_enabled": False}, False),
        (None, False),
    ],
)
def test_nekoclaw_is_on_with_the_master_switch_and_one_capability_switch(wiring, monkeypatch, flags, on):
    # The same reading as the main server's: a session it gave no task tool
    # to must not have anything dispatched in her name on this side.
    monkeypatch.setattr(wiring.shared.Modules, "agent_flags", flags)

    assert wiring.mount_module.nekoclaw_on() is on
    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)
    assert wiring.mount_module.nekoclaw_on() is False


async def test_the_master_switch_alone_dispatches_checks_and_tells_nothing(wiring, monkeypatch):
    monkeypatch.setattr(wiring.shared.Modules, "agent_flags", {})

    dispatched = await wiring.runtime.nekoclaw_dispatch({"text": "查天气", "lanlan_name": "Lan", "language": "zh-CN"})
    queried = await wiring.runtime.nekoclaw_query({"text": "在办什么", "lanlan_name": "Lan", "language": "en"})
    brief = await wiring.runtime.nekoclaw_brief(lanlan_name="Lan")
    await wiring.runtime._handle_turn_check_request(_turn("alone"))
    await wait_until(lambda: wiring.of("turn_check_ack"), what="the acknowledgement")

    assert (dispatched["kind"], dispatched["reason"]) == ("no_capability", "disabled")
    assert queried == {"success": True, "text": "Cannot be done: NekoClaw is switched off", "can_do": False}
    assert brief == {"success": True, "lines": []}
    assert wiring.host_mount.peek_host() is None
    assert wiring.llm.calls == []


async def test_stopping_everything_drops_a_dispatch_still_in_its_first_round(wiring, server, monkeypatch):
    _weather(server)
    await wiring.runtime.nekoclaw_query({"text": "", "lanlan_name": "Lan"})
    host = wiring.host_mount.peek_host()
    looking, go_on = asyncio.Event(), asyncio.Event()
    read_request = host.brain._request

    async def slow_request(*args: Any, **kwargs: Any) -> Any:
        request = await read_request(*args, **kwargs)
        looking.set()
        await go_on.wait()
        return request

    monkeypatch.setattr(host.brain, "_request", slow_request)
    first_round = asyncio.ensure_future(host.brain._first_round("查一下上海的天气", "", "Lan", None, "zh", "dispatch"))
    await asyncio.wait_for(looking.wait(), 2.0)

    stopped = await wiring.mount_module.stop_everything()
    go_on.set()
    receipt = await asyncio.wait_for(first_round, 2.0)

    # Nothing was under way to stop, and the dispatch caught in between files nothing.
    assert stopped == 0
    assert (receipt.kind, receipt.reason) == ("no_capability", "stopped")
    assert receipt.text("zh") == "办不了：刚才已经全部叫停了"
    assert await host.ledger.select() == []
    assert server.calls == []


async def test_a_dispatch_does_not_file_once_nekoclaw_was_switched_off_under_it(wiring, server, monkeypatch):
    _weather(server)
    await wiring.runtime.nekoclaw_query({"text": "", "lanlan_name": "Lan"})
    host = wiring.host_mount.peek_host()
    read_request = host.brain._request

    async def switched_off_meanwhile(*args: Any, **kwargs: Any) -> Any:
        request = await read_request(*args, **kwargs)
        monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)
        return request

    monkeypatch.setattr(host.brain, "_request", switched_off_meanwhile)

    receipt = await host.brain._first_round("查一下上海的天气", "", "Lan", None, "zh", "dispatch")

    assert (receipt.kind, receipt.reason) == ("no_capability", "disabled")
    assert await host.ledger.select() == []


@pytest.mark.parametrize("payload", [{"lanlan_name": "Lan"}, {"text": "  ", "lanlan_name": "Lan"}, {"text": "查天气"}])
async def test_dispatch_route_refuses_a_request_without_a_sentence_or_a_name(wiring, payload):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await wiring.runtime.nekoclaw_dispatch(payload)

    assert raised.value.status_code == 400
    assert wiring.host_mount.peek_host() is None


async def test_query_route_answers_from_the_ledger_without_a_model(wiring, server):
    _weather(server)
    gate = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await gate.wait()
        return finish({"reply": "sunny"})

    server.handlers[("weather", "today")] = slow
    await wiring.runtime.nekoclaw_dispatch({"text": "查一下上海的天气", "lanlan_name": "Lan", "language": "zh"})
    await wait_until(lambda: any(task["status"] == "running" for task in wiring.updates()), what="the call")
    calls_before = wiring.llm.count()

    progress = await wiring.runtime.nekoclaw_query({"text": "", "lanlan_name": "Lan", "language": "zh"})
    can_do = await wiring.runtime.nekoclaw_query({"text": "能不能查天气", "lanlan_name": "Lan", "language": "zh"})
    gate.set()

    assert progress["text"] == "在办的事：\n- 查一下上海的天气 (进行中)"
    assert can_do["can_do"] is True
    assert wiring.llm.count() == calls_before


async def test_query_route_with_the_master_switch_off(wiring, monkeypatch):
    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)

    answer = await wiring.runtime.nekoclaw_query({"text": "在办什么", "lanlan_name": "Lan", "language": "en"})

    assert answer == {"success": True, "text": "Cannot be done: NekoClaw is switched off", "can_do": False}
    assert wiring.host_mount.peek_host() is None


# ---------------------------------------------------------------------------
# What a new session is told
# ---------------------------------------------------------------------------


async def test_brief_without_a_ledger_file_builds_and_opens_nothing(wiring, tmp_path, monkeypatch):

    answer = await wiring.runtime.nekoclaw_brief(lanlan_name="Lan", language="zh")

    assert answer == {"success": True, "lines": []}
    assert wiring.host_mount.peek_host() is None
    assert not (tmp_path / "nekoclaw").exists()


async def test_brief_with_the_master_switch_off_does_not_even_look_for_the_file(wiring, monkeypatch):
    looked: list[int] = []

    async def look() -> bool:
        looked.append(1)
        return True

    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)
    monkeypatch.setattr(wiring.runtime, "ledger_exists", look)

    assert await wiring.runtime.nekoclaw_brief(lanlan_name="Lan") == {"success": True, "lines": []}
    assert looked == []


async def test_brief_lists_what_is_under_way(wiring, server, monkeypatch):
    _weather(server)
    gate = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await gate.wait()
        return finish({"reply": "sunny"})

    server.handlers[("weather", "today")] = slow
    await wiring.runtime.nekoclaw_dispatch({"text": "查一下上海的天气", "lanlan_name": "Lan", "language": "zh"})
    await wait_until(lambda: any(task["status"] == "running" for task in wiring.updates()), what="the call")

    answer = await wiring.runtime.nekoclaw_brief(lanlan_name="Lan", language="zh-CN")
    gate.set()

    assert answer == {"success": True, "lines": ["- [进行中] 查一下上海的天气"]}


async def test_what_an_earlier_process_left_under_way_shows_in_the_hud_on_first_use(wiring, tmp_path, monkeypatch):
    # A deferred plugin result survives a restart of the host; the HUD of the
    # new process has to show it without waiting for it to change.
    from nekoclaw.host.ledger import Ledger

    earlier = Ledger(tmp_path / "nekoclaw" / "ledger.db")
    commission, _ = await earlier.create(
        lanlan_name="Lan", title="五点提醒我", dispatch_text="五点提醒我", source="dispatch"
    )
    await earlier.transition(
        commission.id, "working", route="plugin", plugin_id="memo", entry_id="remind", deferred_until=time.time() + 600
    )
    await earlier.close()

    answer = await wiring.runtime.nekoclaw_brief(lanlan_name="Lan", language="zh")

    assert answer["lines"] == ["- [进行中] 五点提醒我"]
    assert [(task["id"], task["status"]) for task in wiring.mount_module.hud.active()] == [(commission.id, "running")]
    listed = await wiring.routes.list_tasks()
    assert [task["id"] for task in listed["tasks"]] == [commission.id]


async def _left_by_an_earlier_process(tmp_path: Any, *, due_in: float = 600.0) -> Any:
    from nekoclaw.host.ledger import Ledger

    earlier = Ledger(tmp_path / "nekoclaw" / "ledger.db")
    commission, _ = await earlier.create(
        lanlan_name="Lan", title="五点提醒我", dispatch_text="五点提醒我", source="dispatch"
    )
    await earlier.transition(
        commission.id,
        "working",
        route="plugin",
        plugin_id="memo",
        entry_id="remind",
        deferred_until=time.time() + due_in,
    )
    await earlier.close()
    return commission


# Each way a page asks first, from a host nothing has used yet: no session
# started, nothing was dispatched.


async def test_the_task_list_right_after_a_restart_has_what_was_left_under_way(wiring, tmp_path):
    commission = await _left_by_an_earlier_process(tmp_path)

    listed = await wiring.routes.list_tasks()

    assert [task["id"] for task in listed["tasks"]] == [commission.id]


async def test_the_state_snapshot_right_after_a_restart_has_what_was_left_under_way(wiring, tmp_path):
    commission = await _left_by_an_earlier_process(tmp_path)

    state = await wiring.routes.get_agent_state()

    assert [task["id"] for task in state["snapshot"]["active_tasks"]] == [commission.id]


async def test_one_task_can_be_looked_up_right_after_a_restart(wiring, tmp_path):
    commission = await _left_by_an_earlier_process(tmp_path)

    one = await wiring.runtime.get_task(commission.id)

    assert (one["id"], one["status"]) == (commission.id, "running")


async def test_a_task_left_by_an_earlier_process_can_be_stopped_before_anything_else_happens(wiring, tmp_path):
    commission = await _left_by_an_earlier_process(tmp_path)

    stopped = await wiring.runtime.cancel_task(commission.id)

    assert stopped == {"success": True, "task_id": commission.id, "status": "cancelled"}
    assert (await wiring.host_mount.peek_host().ledger.get(commission.id)).status == "cancelled"


async def test_asking_for_the_tasks_builds_nothing_without_a_record_or_with_nekoclaw_off(wiring, tmp_path, monkeypatch):
    assert (await wiring.routes.list_tasks())["tasks"] == []
    assert wiring.host_mount.peek_host() is None
    assert not (tmp_path / "nekoclaw").exists()

    await _left_by_an_earlier_process(tmp_path)
    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)
    assert (await wiring.routes.list_tasks())["tasks"] == []
    assert wiring.host_mount.peek_host() is None


async def test_a_restore_that_failed_is_tried_again(wiring, tmp_path, monkeypatch):
    commission = await _left_by_an_earlier_process(tmp_path)
    host = wiring.host_mount.get_host()
    select, failing = host.ledger.select, {"on": True}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        if failing["on"]:
            raise RuntimeError("database is locked")
        return await select(*args, **kwargs)

    monkeypatch.setattr(host.ledger, "select", flaky)
    await wiring.mount_module.ready_host()
    assert wiring.mount_module.hud.active() == []

    failing["on"] = False
    await wiring.mount_module.ready_host()

    assert [task["id"] for task in wiring.mount_module.hud.active()] == [commission.id]


async def test_the_pages_that_read_the_tasks_still_answer_when_the_host_cannot_be_built(wiring, tmp_path, monkeypatch):
    await _left_by_an_earlier_process(tmp_path)

    def broken() -> Any:
        raise RuntimeError("cannot open the ledger")

    monkeypatch.setattr(wiring.host_mount, "get_host", broken)

    # They read what is in memory; before the host they would answer from
    # memory alone, and a host that cannot be built must not make them fail.
    assert (await wiring.routes.list_tasks())["tasks"] == []
    assert (await wiring.routes.get_agent_state())["success"] is True
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as missing:
        await wiring.runtime.get_task("nope")
    assert missing.value.status_code == 404


async def test_a_host_that_cannot_be_built_is_not_tried_again_on_every_poll(wiring, tmp_path, monkeypatch):
    await _left_by_an_earlier_process(tmp_path)
    tries: list[int] = []
    now = [1000.0]

    def broken() -> Any:
        tries.append(1)
        raise RuntimeError("cannot open the ledger")

    monkeypatch.setattr(wiring.host_mount, "get_host", broken)
    monkeypatch.setattr(wiring.mount_module, "_restore_clock", lambda: now[0])

    import logging

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect(level=logging.WARNING)
    wiring.mount_module.logger.addHandler(handler)
    try:
        # The task window polls every few seconds.
        for _ in range(3):
            await wiring.routes.list_tasks()
            now[0] += 5
    finally:
        wiring.mount_module.logger.removeHandler(handler)
    assert tries == [1]
    # Logged once, with what went wrong and where.
    (logged,) = [record for record in records if "restore the HUD" in record.getMessage()]
    assert logged.exc_info is not None and "cannot open the ledger" in str(logged.exc_info[1])

    now[0] += wiring.mount_module.RESTORE_RETRY_SECONDS
    await wiring.routes.list_tasks()
    assert tries == [1, 1]



async def test_shutting_the_host_down_forgets_a_failed_restore(wiring, monkeypatch):
    # A host built again after a shutdown is not left unfilled for the rest
    # of the pause a failure before the shutdown started.
    monkeypatch.setattr(wiring.mount_module, "_restore_failed_at", 123.0)
    monkeypatch.setattr(wiring.mount_module, "_unsubscribe", None)

    await wiring.mount_module.shutdown_nekoclaw_host()

    assert wiring.mount_module._restore_failed_at is None

async def test_a_ledger_that_cannot_be_read_is_not_tried_again_on_every_poll(wiring, tmp_path, monkeypatch):
    # The host is built, but reading what was left under way fails: that
    # waits as well, instead of one warning per poll.
    await _left_by_an_earlier_process(tmp_path)
    host = wiring.host_mount.get_host()
    reads: list[int] = []
    now = [1000.0]

    async def locked(*args: Any, **kwargs: Any) -> Any:
        reads.append(1)
        raise RuntimeError("database is locked")

    monkeypatch.setattr(host.ledger, "select", locked)
    monkeypatch.setattr(wiring.mount_module, "_restore_clock", lambda: now[0])

    for _ in range(3):
        await wiring.routes.list_tasks()
        now[0] += 5
    assert reads == [1]

    now[0] += wiring.mount_module.RESTORE_RETRY_SECONDS
    await wiring.routes.list_tasks()
    assert reads == [1, 1]


@pytest.mark.parametrize("nekoclaw_is_on", [True, False])
async def test_stopping_everything_right_after_a_restart_stops_what_was_left_under_way(
    wiring, tmp_path, monkeypatch, nekoclaw_is_on
):
    # Nothing read the tasks first: no page was open, nothing was dispatched.
    commission = await _left_by_an_earlier_process(tmp_path)
    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", nekoclaw_is_on)
    assert wiring.host_mount.peek_host() is None

    stopped = await wiring.mount_module.stop_everything()

    assert stopped == 1
    assert (await wiring.host_mount.peek_host().ledger.get(commission.id)).status == "cancelled"


async def test_stopping_everything_without_a_record_builds_nothing(wiring, tmp_path):
    assert await wiring.mount_module.stop_everything() == 0

    assert wiring.host_mount.peek_host() is None
    assert not (tmp_path / "nekoclaw").exists()


async def test_stopping_everything_starts_every_stop_before_it_waits_for_one(wiring, monkeypatch):
    host = wiring.host_mount.get_host()
    ids = []
    for title in ("第一件", "第二件", "第三件"):
        commission, _ = await host.ledger.create(lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch")
        await host.ledger.transition(commission.id, "working")
        ids.append(commission.id)
    started: list[str] = []
    slow_to_answer = asyncio.Event()
    really_abort = host.abort

    async def abort(commission_id: str, *, reason: str = "stopped") -> Any:
        started.append(commission_id)
        if len(started) == 1:
            # The plugin of whichever is stopped first takes its time to
            # acknowledge the cancel.
            await slow_to_answer.wait()
        return await really_abort(commission_id, reason=reason)

    monkeypatch.setattr(host, "abort", abort)

    stopping = asyncio.ensure_future(wiring.mount_module.stop_everything())
    await wait_until(lambda: len(started) == 3, what="every stop being started")
    await wait_until(
        lambda: sorted(task["id"] for task in wiring.mount_module.hud.all() if task["status"] == "cancelled")
        == sorted(started[1:]),
        what="the other two being stopped meanwhile",
    )
    assert not stopping.done()
    slow_to_answer.set()

    assert await asyncio.wait_for(stopping, 5.0) == 3


async def test_a_stop_that_fails_does_not_keep_the_others_from_being_stopped(wiring, monkeypatch):
    host = wiring.host_mount.get_host()
    ids = []
    for title in ("第一件", "第二件"):
        commission, _ = await host.ledger.create(lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch")
        await host.ledger.transition(commission.id, "working")
        ids.append(commission.id)
    really_abort = host.abort

    async def abort(commission_id: str, *, reason: str = "stopped") -> Any:
        if commission_id == ids[0]:
            raise RuntimeError("database is locked")
        return await really_abort(commission_id, reason=reason)

    monkeypatch.setattr(host, "abort", abort)

    assert await wiring.mount_module.stop_everything() == 1
    assert (await host.ledger.get(ids[1])).status == "cancelled"


async def test_a_deferred_result_that_ran_out_while_the_process_was_down_fails_when_the_tasks_are_first_read(
    wiring, tmp_path
):
    # Its timer went with the earlier process. A page that only reads the
    # tasks dispatches nothing, so nothing else would ever look at it.
    commission = await _left_by_an_earlier_process(tmp_path, due_in=-5.0)

    listed = await wiring.routes.list_tasks()

    assert (await wiring.host_mount.peek_host().ledger.get(commission.id)).status == "failed"
    assert [(task["id"], task["status"]) for task in listed["tasks"]] == [(commission.id, "failed")]


async def test_a_deferred_result_still_in_time_is_timed_again_when_the_tasks_are_first_read(wiring, tmp_path):
    commission = await _left_by_an_earlier_process(tmp_path)

    await wiring.routes.list_tasks()

    host = wiring.host_mount.peek_host()
    assert (await host.ledger.get(commission.id)).status == "working"
    assert commission.id in host.brain._deferred_timers


# ---------------------------------------------------------------------------
# The task endpoints the HUD uses
# ---------------------------------------------------------------------------


async def _running_task(wiring: Any, server: FakePluginServer) -> tuple[str, asyncio.Event]:
    _weather(server)
    gate = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        await gate.wait()
        return finish({"reply": "sunny"})

    server.handlers[("weather", "today")] = slow
    answer = await wiring.runtime.nekoclaw_dispatch({"text": "查一下上海的天气", "lanlan_name": "Lan"})
    task_id = answer["commission_id"]
    await wait_until(lambda: any(task["status"] == "running" for task in wiring.updates(task_id)), what="the call")
    return task_id, gate


async def test_tasks_and_the_status_snapshot_read_the_same_view(wiring, server, monkeypatch):
    task_id, gate = await _running_task(wiring, server)
    monkeypatch.setattr("app.agent_server._check_agent_api_gate", lambda: {"ready": True, "reasons": []})

    listed = await wiring.routes.list_tasks()
    one = await wiring.runtime.get_task(task_id)
    snapshot = wiring.routes._collect_agent_status_snapshot()
    gate.set()

    assert [(task["id"], task["status"], task["source"]) for task in listed["tasks"]] == [
        (task_id, "running", "runtime")
    ]
    assert (one["id"], one["status"], one["type"]) == (task_id, "running", "user_plugin")
    assert [task["id"] for task in snapshot["active_tasks"]] == [task_id]
    assert "openclaw" not in snapshot["capabilities"]
    assert set(snapshot["flags"]) == {"user_plugin_enabled"}


async def test_unknown_task_is_not_found(wiring):
    from fastapi import HTTPException

    for route in (wiring.runtime.get_task, wiring.runtime.cancel_task):
        with pytest.raises(HTTPException) as raised:
            await route("no-such-task")
        assert raised.value.status_code == 404


async def test_cancel_stops_the_task_tells_the_plugin_and_reports_it(wiring, server):
    task_id, gate = await _running_task(wiring, server)

    answer = await wiring.runtime.cancel_task(task_id)

    assert answer == {"success": True, "task_id": task_id, "status": "cancelled"}
    await wait_until(lambda: server.cancelled, what="the cancel reaching the plugin")
    assert [plugin_id for plugin_id, _run in server.cancelled] == ["weather"]
    await wait_until(lambda: wiring.of("task_result"), what="the report of the stop")
    (report,) = wiring.of("task_result")
    assert (report["task_id"], report["status"], report["success"]) == (task_id, "cancelled", False)
    last = wiring.updates(task_id)[-1]
    assert (last["status"], last["error"]) == ("cancelled", "Cancelled by user")
    # A second cancel finds it ended and says what it is.
    again = await wiring.runtime.cancel_task(task_id)
    assert again == {"success": False, "error": "task is not active", "status": "cancelled"}
    gate.set()


async def test_end_all_stops_every_task_under_way(wiring, server, monkeypatch):
    from app.agent_server import nekoclaw_builtins

    task_id, gate = await _running_task(wiring, server)
    second = await wiring.runtime.nekoclaw_dispatch({"text": "查一下北京后天的天气", "lanlan_name": "Mei"})
    closes: list[int] = []

    async def close_browser() -> None:
        closes.append(1)

    # There is no browser and no desktop in a test run; that end_all turns to
    # the browser is recorded.
    monkeypatch.setattr(nekoclaw_builtins.provider, "close_browser", close_browser)
    monkeypatch.setattr(nekoclaw_builtins.provider, "_desktop", None)

    answer = await wiring.routes.admin_control({"action": "end_all"})

    assert answer["success"] is True
    host = wiring.host_mount.peek_host()
    assert (await host.ledger.get(task_id)).status == "cancelled"
    assert (await host.ledger.get(second["commission_id"])).status == "cancelled"
    assert wiring.mount_module.hud.active() == []
    # The browser the browser tools started does not outlive the tasks.
    assert closes == [1]
    gate.set()


async def test_deferred_callback_completes_the_commission(wiring, server, monkeypatch):
    async def remind(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"deferred": True, "reminder_id": "r-1", "reply": "will remind"})

    async def bind_task(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"ok": True})

    server.add_plugin(
        plugin(
            "memo",
            "Reminders",
            [entry("remind", "Remind later"), entry("bind_task", "Bind", metadata={"agent_hidden": True})],
            keywords=["提醒"],
        ),
        remind=remind,
        bind_task=bind_task,
    )
    wiring.llm.fine = [pick_plugin("memo", "remind", {"at": "17:00"})]
    answer = await wiring.runtime.nekoclaw_dispatch({"text": "五点提醒我开会", "lanlan_name": "Lan"})
    task_id = answer["commission_id"]
    host = wiring.host_mount.peek_host()
    await wait_until(lambda: _deferred(host, task_id), what="the deferred result")

    done = await wiring.runtime.complete_deferred_task(task_id)
    again = await wiring.runtime.complete_deferred_task(task_id)

    assert done == {"ok": True}
    assert again == {"ok": True, "skipped": True, "status": "completed"}
    assert (await host.ledger.get(task_id)).status == "done"
    # The plugin was handed the task's id as the agent_task_id to call back with.
    bind = next(call for call in server.calls if call["entry_id"] == "bind_task")
    assert bind["args"]["agent_task_id"] == task_id
    assert bind["args"]["reminder_id"] == "r-1"


async def _deferred(host: Any, task_id: str) -> bool:
    commission = await host.ledger.get(task_id)
    return commission is not None and commission.deferred_until is not None


async def test_deferred_callback_for_something_that_is_not_deferred(wiring, server, monkeypatch):
    from fastapi import HTTPException

    task_id, gate = await _running_task(wiring, server)

    with pytest.raises(HTTPException) as not_deferred:
        await wiring.runtime.complete_deferred_task(task_id)
    with pytest.raises(HTTPException) as unknown:
        await wiring.runtime.complete_deferred_task("no-such-task")
    gate.set()

    assert (not_deferred.value.status_code, unknown.value.status_code) == (400, 404)


# ---------------------------------------------------------------------------
# The HUD's view
# ---------------------------------------------------------------------------


def _commission(**values: Any) -> Any:
    base = {
        "id": "c1",
        "lanlan_name": "Lan",
        "title": "sort the photos",
        "dispatch_text": "sort the photos by month",
        "status": "working",
        "status_reason": "",
        "route": "task_agent",
        "plugin_id": None,
        "entry_id": None,
        "parent_id": None,
        "created_at": 1_700_000_000.0,
        "started_at": 1_700_000_005.0,
        "ended_at": None,
        "updated_at": 1_700_000_005.0,
    }
    base.update(values)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "status, shown",
    [
        ("queued", "queued"),
        ("working", "running"),
        ("waiting_user", "waiting_user"),
        ("done", "completed"),
        ("blocked", "blocked"),
        ("failed", "failed"),
        ("cancelled", "cancelled"),
    ],
)
def test_every_ledger_status_has_its_word_for_the_hud(status, shown):
    from app.agent_server.nekoclaw_mount import HUD_ACTIVE, hud_task
    from nekoclaw.host.ledger import STATUSES

    task = hud_task(_commission(status=status, ended_at=1_700_000_050.0, status_reason="why"))

    assert status in STATUSES
    assert task["status"] == shown
    # Ended tasks carry when they ended; the ones that went wrong, why.
    assert ("end_time" in task) == (shown not in HUD_ACTIVE)
    assert task.get("error") == ("why" if shown in ("failed", "blocked", "cancelled") else None)


def test_hud_task_of_a_task_agent_and_of_a_task_not_yet_decided():
    from app.agent_server.nekoclaw_mount import hud_task

    shown = hud_task(_commission())
    undecided = hud_task(_commission(route=None, status="queued", started_at=None))

    assert shown == {
        "id": "c1",
        "status": "running",
        "type": "task_agent",
        "start_time": "2023-11-14T22:13:25Z",
        "params": {"description": "sort the photos", "instruction": "sort the photos by month"},
        "lanlan_name": "Lan",
    }
    assert (undecided["type"], undecided["start_time"]) == ("task", "2023-11-14T22:13:20Z")


def test_a_task_agents_own_task_agent_is_not_shown():
    from app.agent_server.nekoclaw_mount import HudTasks, hud_task

    child = _commission(id="c2", parent_id="c1")

    assert hud_task(child) is None
    view = HudTasks()
    assert view.update(child) is None
    assert view.all() == []


def test_ended_tasks_stay_in_view_for_a_while_and_then_go():
    from app.agent_server.nekoclaw_mount import HUD_RETENTION_SECONDS, HudTasks

    now = [100.0]
    view = HudTasks(clock=lambda: now[0])
    view.update(_commission(id="a", status="working"))
    view.update(_commission(id="b", status="done", ended_at=1_700_000_050.0))

    assert [task["id"] for task in view.active()] == ["a"]
    assert sorted(task["id"] for task in view.all()) == ["a", "b"]

    now[0] += HUD_RETENTION_SECONDS + 1
    assert [task["id"] for task in view.all()] == ["a"]
    assert view.get("b") is None


def test_a_task_taken_up_again_is_under_way_again():
    from app.agent_server.nekoclaw_mount import HUD_RETENTION_SECONDS, HudTasks

    now = [100.0]
    view = HudTasks(clock=lambda: now[0])
    view.update(_commission(status="failed", ended_at=1_700_000_050.0))
    view.update(_commission(status="working"))

    now[0] += HUD_RETENTION_SECONDS + 1
    assert [task["status"] for task in view.all()] == ["running"]


def test_progress_moves_only_a_running_task_and_keeps_the_plugins_name():
    from app.agent_server.nekoclaw_mount import HudTasks

    view = HudTasks()
    view.update(_commission(route="plugin", plugin_id="weather", entry_id="today"))
    view._tasks["c1"]["params"]["plugin_name"] = "Weather"

    moved = view.progress("c1", {"progress": 0.4, "step": 2, "step_total": 5, "stage": None})
    after = view.update(_commission(route="plugin", plugin_id="weather", entry_id="today", status="done"))

    assert (moved["progress"], moved["step"], moved["step_total"]) == (0.4, 2, 5)
    assert "stage" not in moved
    assert after["params"]["plugin_name"] == "Weather"
    assert view.progress("c1", {"progress": 0.9}) is None
    assert view.progress("unknown", {"progress": 0.9}) is None


# ---------------------------------------------------------------------------
# Reports to the main server
# ---------------------------------------------------------------------------


def _item(kind: str, payload: dict[str, Any], item_id: int = 7) -> Any:
    return SimpleNamespace(id=item_id, kind=kind, payload=payload, commission_id=payload.get("task_id"))


async def test_a_task_result_row_goes_out_as_the_task_result_event(wiring):
    payload = {"lanlan_name": "Lan", "task_id": "c1", "channel": "task_agent", "status": "completed", "summary": "done"}

    sent = await wiring.mount_module.send_report(_item("task_result", payload))

    assert sent is True
    (event,) = wiring.events
    assert {key: event[key] for key in payload} == payload
    assert event["event_type"] == "task_result"


@pytest.mark.parametrize(
    "kind, payload, status",
    [
        ("question", {"question": {"kind": "report"}, "delivery_mode": "proactive"}, "completed"),
        ("correction", {"reason": "no_capability", "delivery_mode": "proactive"}, "blocked"),
        ("correction", {"reason": "can_do", "delivery_mode": "proactive"}, "blocked"),
        ("correction", {"reason": "filed", "delivery_mode": "passive"}, "completed"),
        ("receipt", {"receipt": "accepted", "delivery_mode": "passive"}, "completed"),
    ],
)
async def test_a_sentence_for_her_goes_out_as_a_message_from_the_system(wiring, kind, payload, status):
    payload = {"lanlan_name": "Lan", "task_id": "c1", "text": "something she is told", **payload}

    sent = await wiring.mount_module.send_report(_item(kind, payload))

    assert sent is True
    (event,) = wiring.events
    assert event["event_type"] == "proactive_message"
    assert (event["lanlan_name"], event["task_id"]) == ("Lan", "c1")
    assert event["text"] == event["summary"] == event["detail"] == "something she is told"
    assert (event["source_kind"], event["channel"]) == ("system", "nekoclaw")
    assert event["delivery_mode"] == payload["delivery_mode"]
    assert event["status"] == status
    # For her only: an explicit empty list shows it neither in the chat nor as a HUD toast.
    assert event["visibility"] == []
    assert event["coalesce_key"] == f"nekoclaw:{kind}:c1"


async def test_a_row_with_nothing_to_say_is_done_with_and_not_sent(wiring):
    sent = await wiring.mount_module.send_report(_item("correction", {"lanlan_name": "Lan", "text": "  "}))

    assert sent is True
    assert wiring.events == []


async def test_a_report_the_bridge_does_not_take_is_tried_again(wiring, server):
    _weather(server)
    wiring.bridge_up = False
    answer = await wiring.runtime.nekoclaw_dispatch({"text": "查一下上海的天气", "lanlan_name": "Lan"})
    host = wiring.host_mount.peek_host()
    await wait_until(lambda: _attempted(host), what="a failed attempt")
    assert wiring.of("task_result") == []

    wiring.bridge_up = True
    await host.ledger.run(lambda conn: conn.execute("UPDATE outbox SET next_attempt_at = 0"))
    host.outbox.kick()

    await wait_until(lambda: wiring.of("task_result"), what="the report going out after all")
    assert wiring.of("task_result")[0]["task_id"] == answer["commission_id"]


async def _attempted(host: Any) -> bool:
    return any(item.attempts >= 1 for item in await host.ledger.outbox_pending())


# ---------------------------------------------------------------------------
# The check of a turn
# ---------------------------------------------------------------------------


def _turn(event_id: str = "e1", **values: Any) -> dict[str, Any]:
    event = {
        "event_type": "turn_check_request",
        "event_id": event_id,
        "lanlan_name": "Lan",
        "user_text": "帮我查一下上海明天的天气",
        "reply_text": "好的，我这就去查",
        "external_intent": 0.8,
        "language": "zh-CN",
        "conversation_id": "conv-1",
    }
    event.update(values)
    return event


async def test_a_turn_check_request_is_acknowledged_and_her_promise_dispatched(wiring, server):
    _weather(server)
    wiring.llm.turn = [{"verdict": "promised", "task": "查上海明天的天气"}]

    await wiring.runtime._handle_turn_check_request(_turn())

    await wait_until(lambda: wiring.of("turn_check_ack"), what="the acknowledgement")
    assert [(ack["event_id"], ack["lanlan_name"]) for ack in wiring.of("turn_check_ack")] == [("e1", "Lan")]
    host = wiring.host_mount.peek_host()
    (commission,) = await host.ledger.select(lanlan_name="Lan")
    assert (commission.source, commission.dispatch_text) == ("promised", "查上海明天的天气")
    assert commission.conversation_id == "conv-1"
    await wait_until(lambda: wiring.of("proactive_message"), what="her being told")
    notice = wiring.of("proactive_message")[0]
    assert (notice["delivery_mode"], notice["text"]) == ("passive", "你刚才答应的事已经替你派发了。已受理：查上海明天的天气")


async def test_the_same_request_sent_again_is_acknowledged_but_checked_once(wiring, server):
    _weather(server)
    wiring.llm.turn = [{"verdict": "none"}]

    await wiring.runtime._handle_turn_check_request(_turn("same"))
    await wiring.runtime._handle_turn_check_request(_turn("same"))
    await wiring.runtime._handle_turn_check_request(_turn("other"))

    await wait_until(lambda: len(wiring.of("turn_check_ack")) == 3, what="three acknowledgements")
    assert wiring.llm.tiers() == ["turn_check", "turn_check"]


async def test_a_turn_check_with_the_master_switch_off_builds_nothing(wiring, monkeypatch):
    monkeypatch.setattr(wiring.shared.Modules, "analyzer_enabled", False)

    await wiring.runtime._handle_turn_check_request(_turn())

    await wait_until(lambda: wiring.of("turn_check_ack"), what="the acknowledgement")
    assert wiring.host_mount.peek_host() is None
    assert wiring.llm.calls == []


@pytest.mark.parametrize("values", [{"user_text": None}, {"reply_text": 3}, {"lanlan_name": ""}])
async def test_a_malformed_turn_check_request_is_dropped(wiring, values):
    await wiring.runtime._handle_turn_check_request(_turn(**values))

    assert wiring.host_mount.peek_host() is None
    assert wiring.llm.calls == []


async def test_a_score_that_is_not_a_number_is_no_score(wiring, server):
    _weather(server)
    wiring.llm.turn = [{"verdict": "none"}]

    # No usable score never holds a turn back, so the check runs.
    await wiring.runtime._handle_turn_check_request(_turn(external_intent="high", user_text="今天好累啊"))

    assert wiring.llm.tiers() == ["turn_check"]


async def test_session_events_route_a_turn_check_and_ignore_the_old_analyze_request(wiring, server):
    _weather(server)
    wiring.llm.turn = [{"verdict": "none"}]

    await wiring.runtime._on_session_event(_turn("via-event"))
    await wiring.runtime._on_session_event({"event_type": "analyze_request", "lanlan_name": "Lan", "messages": []})

    await wait_until(lambda: wiring.llm.count("turn_check") == 1, what="the check")
    assert [ack["event_id"] for ack in wiring.of("turn_check_ack")] == ["via-event"]


@pytest.mark.parametrize(
    "given, expected",
    [("zh-TW", "zh-TW"), ("ja", "ja"), ("zh-CN", "zh-CN")],
)
def test_the_sessions_language_is_passed_on_as_given(given, expected):
    from app.agent_server import api_runtime

    assert api_runtime._resolve_session_language(given) == expected


def test_an_unknown_language_falls_back_to_the_process_default(monkeypatch):
    from app.agent_server import api_runtime
    from utils import result_parser

    monkeypatch.setattr(result_parser, "_get_lang", lambda lang: "ko")

    assert api_runtime._resolve_session_language("klingon") == "ko"
    assert api_runtime._resolve_session_language(None) == "ko"


# ---------------------------------------------------------------------------
# The built-in tools, as this process offers them
# ---------------------------------------------------------------------------
# The tools themselves and how a call is carried out are covered with the
# package (test_builtin_*.py); here it is what only the agent process knows.


class Bridge:
    """Stands in for the event bridge to the main server: keeps what it is handed."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit_to_main(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


@pytest.fixture
def builtins(monkeypatch: Any) -> Any:
    from app.agent_server import _shared, nekoclaw_builtins, nekoclaw_mount

    bridge = Bridge()
    monkeypatch.setattr(_shared.Modules, "analyzer_enabled", True)
    monkeypatch.setattr(
        _shared.Modules,
        "agent_flags",
        {"computer_use_enabled": True, "browser_use_enabled": True, "user_plugin_enabled": True},
    )
    monkeypatch.setattr(
        _shared.Modules,
        "capability_cache",
        {"computer_use": {"ready": True, "reason": ""}, "browser_use": {"ready": True, "reason": ""}},
    )
    monkeypatch.setattr(_shared.Modules, "agent_bridge", bridge)
    monkeypatch.setattr(_shared.Modules, "_background_tasks", set())
    monkeypatch.setattr(nekoclaw_builtins, "_in_use", {})
    # Neither a desktop nor a browser exists in a test run.
    monkeypatch.setattr(nekoclaw_builtins.provider, "_desktop", None)
    monkeypatch.setattr(nekoclaw_builtins.provider, "_browser", None)
    # A use is counted in the usage statistics; a test run must not add to them.
    counted: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr("utils.instrument.counter", lambda name, value=1, **dims: counted.append((name, dims)))

    async def sent() -> list[dict[str, Any]]:
        # The events go out from tasks, so that telling never holds a call up.
        await asyncio.gather(*list(_shared.Modules._background_tasks))
        return [event for event in bridge.events if event.get("event_type") == nekoclaw_builtins.ACTIVITY_EVENT]

    yield SimpleNamespace(
        module=nekoclaw_builtins,
        provider=nekoclaw_builtins.provider,
        modules=_shared.Modules,
        hud=nekoclaw_mount.hud,
        bridge=bridge,
        counted=counted,
        sent=sent,
    )
    nekoclaw_mount.hud.clear()


KEYBOARD, BROWSER = "keyboard_mouse", "browser"


def test_a_group_is_available_while_both_switches_are_on_and_its_check_passed(builtins):
    assert builtins.module._state(KEYBOARD) == (True, "")
    assert builtins.module._state(BROWSER) == (True, "")


def test_the_master_switch_turns_both_groups_off(builtins, monkeypatch):
    monkeypatch.setattr(builtins.modules, "analyzer_enabled", False)

    assert builtins.module._state(KEYBOARD) == (False, "switched off")
    assert builtins.module._state(BROWSER) == (False, "switched off")


@pytest.mark.parametrize(
    ("flags", "keyboard", "browser"),
    [
        ({"computer_use_enabled": False, "browser_use_enabled": True}, (False, "switched off"), (True, "")),
        ({"computer_use_enabled": True, "browser_use_enabled": False}, (True, ""), (False, "switched off")),
        # A switch that was never set is off.
        ({"browser_use_enabled": True}, (False, "switched off"), (True, "")),
        ({}, (False, "switched off"), (False, "switched off")),
        (None, (False, "switched off"), (False, "switched off")),
    ],
)
def test_each_group_follows_its_own_switch(builtins, monkeypatch, flags, keyboard, browser):
    monkeypatch.setattr(builtins.modules, "agent_flags", flags)

    assert builtins.module._state(KEYBOARD) == keyboard
    assert builtins.module._state(BROWSER) == browser


@pytest.mark.parametrize(
    ("cache", "keyboard", "browser"),
    [
        (
            {"computer_use": {"ready": True}, "browser_use": {"ready": False, "reason": "AGENT_LLM_UNREACHABLE"}},
            (True, ""),
            (False, "not ready (AGENT_LLM_UNREACHABLE)"),
        ),
        (
            {"computer_use": {"ready": False, "reason": "AGENT_PYAUTOGUI_NOT_INSTALLED"}, "browser_use": {"ready": True}},
            (False, "not ready (AGENT_PYAUTOGUI_NOT_INSTALLED)"),
            (True, ""),
        ),
        # Never checked, or checked without a reason being kept.
        ({"browser_use": {"ready": False, "reason": ""}}, (False, "not ready (unknown)"), (False, "not ready (unknown)")),
        (None, (False, "not ready (unknown)"), (False, "not ready (unknown)")),
    ],
)
def test_a_group_whose_last_check_did_not_pass_is_not_available_and_says_why(
    builtins, monkeypatch, cache, keyboard, browser
):
    monkeypatch.setattr(builtins.modules, "capability_cache", cache)

    assert builtins.module._state(KEYBOARD) == keyboard
    assert builtins.module._state(BROWSER) == browser


def test_a_switch_that_is_off_is_the_reason_given_before_a_failed_check(builtins, monkeypatch):
    monkeypatch.setattr(builtins.modules, "agent_flags", {"computer_use_enabled": True, "browser_use_enabled": False})
    monkeypatch.setattr(
        builtins.modules, "capability_cache", {"browser_use": {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}}
    )

    assert builtins.module._state(BROWSER) == (False, "switched off")


def test_something_that_is_not_a_group_is_not_available(builtins):
    assert builtins.module._state("plugin_entry") == (False, "unknown")
    assert builtins.module._state("") == (False, "unknown")


def test_the_provider_lists_the_computer_tool_and_the_fifteen_browser_tools(builtins):
    from config.prompts.prompts_nekoclaw_builtin import BROWSER_TOOL_NAMES
    from nekoclaw.builtin.provider import BuiltinToolProvider

    assert isinstance(builtins.provider, BuiltinToolProvider)

    listed = builtins.provider.available()

    assert len(BROWSER_TOOL_NAMES) == 15 and all(name.startswith("browser_") for name in BROWSER_TOOL_NAMES)
    assert [(item.id, item.group) for item in listed] == [("builtin:keyboard_mouse:computer", KEYBOARD)] + [
        (f"builtin:browser:{name}", BROWSER) for name in BROWSER_TOOL_NAMES
    ]
    assert [item.tool_name for item in listed] == ["computer", *BROWSER_TOOL_NAMES]
    assert all(item.available and item.unavailable_reason == "" for item in listed)
    # The ones that work from a picture, for a model that can see it.
    assert [item.tool_name for item in listed if item.needs_images] == ["computer", "browser_take_screenshot"]


def test_the_listed_tools_carry_the_state_of_their_group_as_it_is_right_now(builtins, monkeypatch):
    monkeypatch.setattr(builtins.modules, "agent_flags", {"computer_use_enabled": False, "browser_use_enabled": True})
    monkeypatch.setattr(
        builtins.modules,
        "capability_cache",
        {"computer_use": {"ready": True}, "browser_use": {"ready": False, "reason": "AGENT_BROWSER_NOT_FOUND"}},
    )

    by_group: dict[str, set[tuple[bool, str]]] = {}
    for item in builtins.provider.available():
        by_group.setdefault(item.group, set()).add((item.available, item.unavailable_reason))

    assert by_group == {
        KEYBOARD: {(False, "switched off")},
        BROWSER: {(False, "not ready (AGENT_BROWSER_NOT_FOUND)")},
    }

    # Nothing is remembered: the next listing reads the switches again.
    monkeypatch.setattr(builtins.modules, "agent_flags", {"computer_use_enabled": True, "browser_use_enabled": True})
    assert {item.available for item in builtins.provider.available() if item.group == KEYBOARD} == {True}


async def test_a_call_of_a_group_that_is_switched_off_is_refused_without_touching_anything(builtins, monkeypatch):
    monkeypatch.setattr(builtins.modules, "agent_flags", {"computer_use_enabled": False, "browser_use_enabled": False})
    by_name = {item.tool_name: item for item in builtins.provider.available()}

    keyboard = await builtins.provider.run(by_name["computer"], commission_id="c1", arguments={"action": "screenshot"})
    browser = await builtins.provider.run(by_name["browser_list_pages"], commission_id="c1", arguments={})

    assert (keyboard.success, keyboard.text) == (False, "computer is not available: switched off")
    assert (browser.success, browser.text) == (False, "browser_list_pages is not available: switched off")
    # Neither a desktop nor a browser was made for it.
    assert builtins.provider._desktop is None and builtins.provider._browser is None


async def test_beginning_to_use_a_group_records_who_counts_once_and_tells_the_main_server(builtins):
    builtins.hud.update(_commission(id="c1", lanlan_name="Lan"))

    builtins.module.on_builtin_activity(KEYBOARD, "started", "c1")

    assert builtins.module.occupant(KEYBOARD) == "c1"
    assert builtins.module.group_in_use(KEYBOARD) is True and builtins.module.group_in_use() is True
    assert builtins.module.occupant(BROWSER) is None and builtins.module.group_in_use(BROWSER) is False
    assert builtins.counted == [("agent_invoked", {"agent_type": "cua"})]
    assert await builtins.sent() == [
        {
            "event_type": "builtin_activity",
            # The character whose task it is.
            "lanlan_name": "Lan",
            "activity": {"group": KEYBOARD, "state": "started", "commission_id": "c1", "window": None},
        }
    ]


async def test_stopping_to_use_a_group_forgets_who_tells_the_main_server_and_counts_nothing(builtins):
    builtins.module.on_builtin_activity(BROWSER, "started", "c1")
    builtins.module.on_builtin_activity(BROWSER, "ended", "c1")

    assert builtins.module.occupant(BROWSER) is None
    assert builtins.module.group_in_use() is False
    # One use, however many calls it made and although it has ended.
    assert builtins.counted == [("agent_invoked", {"agent_type": "browser"})]
    events = await builtins.sent()
    assert [event["activity"] for event in events] == [
        {"group": BROWSER, "state": "started", "commission_id": "c1", "window": None},
        {"group": BROWSER, "state": "ended", "commission_id": "c1", "window": None},
    ]
    # A commission the task window does not know belongs to no character's page in particular.
    assert {event["lanlan_name"] for event in events} == {None}


async def test_an_end_that_arrives_late_does_not_forget_the_commission_that_has_the_group_now(builtins):
    builtins.module.on_builtin_activity(KEYBOARD, "started", "c1")
    # The next commission took the desktop over before the first one's end was told.
    builtins.module.on_builtin_activity(KEYBOARD, "started", "c2")

    builtins.module.on_builtin_activity(KEYBOARD, "ended", "c1")

    assert builtins.module.occupant(KEYBOARD) == "c2"
    assert builtins.module.group_in_use(KEYBOARD) is True
    # The display is still told of every change.
    assert [event["activity"]["state"] for event in await builtins.sent()] == ["started", "started", "ended"]

    builtins.module.on_builtin_activity(KEYBOARD, "ended", "c2")
    assert builtins.module.occupant(KEYBOARD) is None


async def test_the_two_groups_are_occupied_independently(builtins):
    builtins.module.on_builtin_activity(KEYBOARD, "started", "c1")
    builtins.module.on_builtin_activity(BROWSER, "started", "c2")
    builtins.module.on_builtin_activity(KEYBOARD, "ended", "c1")

    assert (builtins.module.occupant(KEYBOARD), builtins.module.occupant(BROWSER)) == (None, "c2")
    assert builtins.module.group_in_use() is True
    assert [dims["agent_type"] for _name, dims in builtins.counted] == ["cua", "browser"]
    await builtins.sent()


async def test_the_event_for_the_browser_says_where_its_window_is_while_it_is_used(builtins, monkeypatch):
    window = {"left": 40, "top": 60, "width": 1280, "height": 800}
    monkeypatch.setattr(builtins.provider, "browser_window", lambda: dict(window))

    builtins.module.on_builtin_activity(BROWSER, "started", "c1")
    builtins.module.on_builtin_activity(BROWSER, "ended", "c1")
    # For the keyboard and mouse the whole desktop is meant.
    builtins.module.on_builtin_activity(KEYBOARD, "started", "c1")

    assert [event["activity"]["window"] for event in await builtins.sent()] == [window, None, None]
    assert builtins.module.activity_payload(BROWSER, "started", "c9") == {
        "group": BROWSER,
        "state": "started",
        "commission_id": "c9",
        "window": window,
    }


async def test_a_browser_window_that_becomes_known_or_moves_is_told_again_while_the_browser_is_used(
    builtins, monkeypatch
):
    # The browser is only started by a commission's first call, after the
    # "started" event went out: that event cannot say where the window is.
    where: dict[str, Any] = {"window": None}
    monkeypatch.setattr(builtins.provider, "browser_window", lambda: where["window"])
    tell = builtins.provider.on_browser_window
    assert tell is not None

    builtins.module.on_builtin_activity(BROWSER, "started", "c1")
    where["window"] = {"left": 40, "top": 60, "width": 1280, "height": 800}
    tell(where["window"])
    where["window"] = {"left": 0, "top": 0, "width": 1920, "height": 1040}
    tell(where["window"])

    assert [(event["activity"]["state"], event["activity"]["commission_id"], event["activity"]["window"]) for event in await builtins.sent()] == [
        ("started", "c1", None),
        ("started", "c1", {"left": 40, "top": 60, "width": 1280, "height": 800}),
        ("started", "c1", {"left": 0, "top": 0, "width": 1920, "height": 1040}),
    ]
    # Telling again is not another use of the tools.
    assert len(builtins.counted) == 1


async def test_a_browser_window_is_told_to_nobody_while_no_commission_uses_the_browser(builtins, monkeypatch):
    window = {"left": 40, "top": 60, "width": 1280, "height": 800}
    monkeypatch.setattr(builtins.provider, "browser_window", lambda: dict(window))
    tell = builtins.provider.on_browser_window

    # Left open by a commission that has finished, or only the keyboard is in use.
    tell(window)
    builtins.module.on_builtin_activity(KEYBOARD, "started", "c1")
    tell(window)
    builtins.module.on_builtin_activity(BROWSER, "started", "c2")
    builtins.module.on_builtin_activity(BROWSER, "ended", "c2")
    tell(window)

    assert [(event["activity"]["group"], event["activity"]["state"]) for event in await builtins.sent()] == [
        (KEYBOARD, "started"),
        (BROWSER, "started"),
        (BROWSER, "ended"),
    ]


async def test_a_missing_bridge_keeps_nothing_from_being_recorded(builtins, monkeypatch):
    monkeypatch.setattr(builtins.modules, "agent_bridge", None)

    builtins.module.on_builtin_activity(BROWSER, "started", "c1")
    assert builtins.module.occupant(BROWSER) == "c1"
    builtins.module.on_builtin_activity(BROWSER, "ended", "c1")
    assert builtins.module.occupant(BROWSER) is None

    assert await builtins.sent() == []


async def test_a_failure_to_send_the_event_is_not_raised_to_the_host(builtins, monkeypatch):
    from app.agent_server import results

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("no bridge to speak of")

    monkeypatch.setattr(results, "_emit_main_event", broken)

    # The host calls this in the middle of occupying a group; the banner is a display.
    builtins.module.on_builtin_activity(KEYBOARD, "started", "c1")

    assert builtins.module.occupant(KEYBOARD) == "c1"
    assert len(builtins.counted) == 1


async def test_the_host_is_given_the_tools_and_tells_this_process_who_uses_them(
    wiring, server, builtins, tmp_path, monkeypatch
):
    from nekoclaw.host import mount
    from plugin.core.state import state as plugin_state

    monkeypatch.setattr(plugin_state.bus_change_hub, "subscribe", lambda bus, callback: lambda: None)
    monkeypatch.setattr(wiring.mount_module, "_unsubscribe", None)
    mount.reset_for_tests()

    wiring.mount_module.mount_nekoclaw_host()

    # Registering builds nothing. The host is then built with what was
    # registered, in this test's own directory.
    assert mount.peek_host() is None
    registered = dict(mount._options)
    mount.configure(
        **registered, root=tmp_path / "nekoclaw", client_factory=server.client, decision_llm=wiring.llm
    )
    host = mount.get_host()
    commission, _ = await host.ledger.create(lanlan_name="Lan", title="订票", dispatch_text="订票", source="dispatch")
    await host.ledger.transition(commission.id, "working", route="task_agent")

    # The tools the host offers task agents are this process's.
    assert [item.tool_name for item in host.registry.builtins()] == [
        item.tool_name for item in builtins.provider.available()
    ]
    assert len(host.registry.builtins()) == 16

    # A commission that occupies a group is known here, and the banner is told.
    assert await host.leases.acquire(BROWSER, commission.id, title="订票") is None
    assert builtins.module.occupant(BROWSER) == commission.id
    host.leases.release(commission.id)
    await host.leases.settle()
    assert builtins.module.occupant(BROWSER) is None

    await asyncio.gather(*list(builtins.modules._background_tasks))
    activity = [event for event in wiring.of("builtin_activity")]
    assert [(event["lanlan_name"], event["activity"]["state"]) for event in activity] == [
        ("Lan", "started"),
        ("Lan", "ended"),
    ]
    assert {event["activity"]["commission_id"] for event in activity} == {commission.id}


# ---------------------------------------------------------------------------
# The banner's routes: who is using the tools, and stopping them with Esc
# ---------------------------------------------------------------------------


async def _working(host: Any, title: str) -> str:
    commission, _ = await host.ledger.create(lanlan_name="Lan", title=title, dispatch_text=title, source="dispatch")
    await host.ledger.transition(commission.id, "working", route="task_agent")
    return commission.id


def _spy_on_abort(host: Any, monkeypatch: Any) -> list[tuple[str, str]]:
    aborted: list[tuple[str, str]] = []
    really_abort = host.abort

    async def abort(commission_id: str, *, reason: str = "stopped") -> Any:
        aborted.append((commission_id, reason))
        return await really_abort(commission_id, reason=reason)

    monkeypatch.setattr(host, "abort", abort)
    return aborted


async def test_the_activity_route_is_empty_while_nothing_uses_the_tools(wiring, builtins):
    assert await wiring.runtime.builtin_activity() == {"activity": []}


async def test_the_activity_route_has_one_entry_for_each_group_in_use(wiring, builtins, monkeypatch):
    window = {"left": 0, "top": 0, "width": 1024, "height": 768}
    monkeypatch.setattr(builtins.provider, "browser_window", lambda: dict(window))
    builtins.module._in_use[BROWSER] = "c2"

    assert await wiring.runtime.builtin_activity() == {
        "activity": [{"group": BROWSER, "state": "started", "commission_id": "c2", "window": window}]
    }

    builtins.module._in_use[KEYBOARD] = "c1"

    # What a banner that starts late is given is what the events carried.
    assert await wiring.runtime.builtin_activity() == {
        "activity": [
            builtins.module.activity_payload(KEYBOARD, "started", "c1"),
            builtins.module.activity_payload(BROWSER, "started", "c2"),
        ]
    }
    assert (await wiring.runtime.builtin_activity())["activity"][0] == {
        "group": KEYBOARD,
        "state": "started",
        "commission_id": "c1",
        "window": None,
    }


@pytest.mark.parametrize("group", ["desktop", "", "builtin:browser", 3])
async def test_stopping_a_group_that_does_not_exist_is_refused(wiring, builtins, monkeypatch, group):
    from fastapi import HTTPException

    host = wiring.host_mount.get_host()
    task_id = await _working(host, "订票")
    builtins.module._in_use[BROWSER] = task_id
    aborted = _spy_on_abort(host, monkeypatch)

    with pytest.raises(HTTPException) as raised:
        await wiring.runtime.stop_builtin({"group": group})

    assert raised.value.status_code == 400
    assert aborted == []
    assert (await host.ledger.get(task_id)).status == "working"


async def test_stopping_a_group_stops_the_commission_that_uses_it_and_no_other(wiring, builtins, monkeypatch):
    host = wiring.host_mount.get_host()
    typing, booking = await _working(host, "填表"), await _working(host, "订票")
    builtins.module._in_use.update({KEYBOARD: typing, BROWSER: booking})
    aborted = _spy_on_abort(host, monkeypatch)

    answer = await wiring.runtime.stop_builtin({"group": BROWSER})

    assert answer == {"success": True, "stopped": [booking]}
    assert aborted == [(booking, "Stopped with Esc")]
    stopped = await host.ledger.get(booking)
    assert (stopped.status, stopped.status_reason) == ("cancelled", "Stopped with Esc")
    assert (await host.ledger.get(typing)).status == "working"
    # The task window shows it as it shows a task stopped from there.
    assert [task["status"] for task in wiring.mount_module.hud.all() if task["id"] == booking] == ["cancelled"]


@pytest.mark.parametrize("payload", [None, {}, {"group": None}])
async def test_stopping_without_naming_a_group_stops_both(wiring, builtins, monkeypatch, payload):
    host = wiring.host_mount.get_host()
    typing, booking = await _working(host, "填表"), await _working(host, "订票")
    builtins.module._in_use.update({BROWSER: booking, KEYBOARD: typing})

    answer = await wiring.runtime.stop_builtin(payload)

    assert answer == {"success": True, "stopped": [typing, booking]}
    assert (await host.ledger.get(typing)).status == "cancelled"
    assert (await host.ledger.get(booking)).status == "cancelled"


async def test_a_commission_that_uses_both_groups_is_stopped_once(wiring, builtins, monkeypatch):
    host = wiring.host_mount.get_host()
    task_id = await _working(host, "订票")
    builtins.module._in_use.update({KEYBOARD: task_id, BROWSER: task_id})
    aborted = _spy_on_abort(host, monkeypatch)

    answer = await wiring.runtime.stop_builtin({})

    assert answer == {"success": True, "stopped": [task_id]}
    assert aborted == [(task_id, "Stopped with Esc")]


async def test_stopping_while_nothing_uses_the_tools_stops_nothing_and_is_no_error(wiring, builtins, monkeypatch):
    host = wiring.host_mount.get_host()
    other = await _working(host, "整理照片")
    aborted = _spy_on_abort(host, monkeypatch)

    assert await wiring.runtime.stop_builtin({}) == {"success": True, "stopped": []}
    assert await wiring.runtime.stop_builtin({"group": KEYBOARD}) == {"success": True, "stopped": []}

    # Esc on the banner is not "stop everything": a task that uses neither goes on.
    assert aborted == []
    assert (await host.ledger.get(other)).status == "working"


async def test_stopping_before_the_host_exists_stops_nothing_and_builds_nothing(wiring, builtins):
    builtins.module._in_use[BROWSER] = "left-over"

    assert await wiring.runtime.stop_builtin({}) == {"success": True, "stopped": []}
    assert wiring.host_mount.peek_host() is None


async def test_a_commission_that_had_already_ended_is_not_reported_as_stopped(wiring, builtins, monkeypatch):
    host = wiring.host_mount.get_host()
    finished = await _working(host, "订票")
    await host.ledger.transition(finished, "done")
    # Its end has not been told yet; and one that the ledger never had.
    builtins.module._in_use.update({BROWSER: finished, KEYBOARD: "no-such-commission"})

    answer = await wiring.runtime.stop_builtin({})

    assert answer == {"success": True, "stopped": []}
    assert (await host.ledger.get(finished)).status == "done"


# ---------------------------------------------------------------------------
# Which browser the browser tools use: the settings routes
# ---------------------------------------------------------------------------


@pytest.fixture
def browser_settings(monkeypatch: Any, tmp_path: Any) -> Any:
    from app.agent_server import _shared, api_runtime
    from nekoclaw.builtin.browser import launcher, settings

    root = tmp_path / "data" / "nekoclaw"
    found = [
        SimpleNamespace(kind="chrome", name="Google Chrome", executable="/opt/google/chrome", user_data_dir="/home/u/.config/chrome"),
        SimpleNamespace(kind="edge", name="Microsoft Edge", executable="/opt/microsoft/edge", user_data_dir=None),
    ]
    closes: list[dict[str, Any]] = []

    async def close_browser(**options: Any) -> None:
        closes.append(options)

    monkeypatch.setattr(settings, "_root", lambda: root)
    monkeypatch.setattr(launcher, "find_browsers", lambda *args, **kwargs: list(found))
    monkeypatch.setattr(api_runtime, "_close_browser", close_browser)
    monkeypatch.setattr(_shared.Modules, "_background_tasks", set())

    async def closed() -> list[dict[str, Any]]:
        await asyncio.gather(*list(_shared.Modules._background_tasks))
        return closes

    return SimpleNamespace(
        runtime=api_runtime,
        file=root / "browser.json",
        closed=closed,
        modes=["own_profile", "attach"],
        browsers=[
            {"kind": "chrome", "name": "Google Chrome", "executable": "/opt/google/chrome"},
            {"kind": "edge", "name": "Microsoft Edge", "executable": "/opt/microsoft/edge"},
        ],
    )


async def test_the_browser_settings_start_as_the_defaults_with_the_browsers_found(browser_settings):
    view = await browser_settings.runtime.get_browser_settings()

    assert view == {
        "settings": {"mode": "own_profile", "executable": None, "headless": False},
        "modes": browser_settings.modes,
        "browsers": browser_settings.browsers,
    }
    # Reading writes nothing and closes nothing.
    assert not browser_settings.file.exists()
    assert await browser_settings.closed() == []


async def test_changing_the_browser_settings_saves_them_and_closes_the_browser_that_is_open(browser_settings):
    import json

    answer = await browser_settings.runtime.set_browser_settings({"mode": "attach"})

    assert answer == {
        "success": True,
        "settings": {"mode": "attach", "executable": None, "headless": False},
        "modes": browser_settings.modes,
        "browsers": browser_settings.browsers,
    }
    assert json.loads(browser_settings.file.read_text(encoding="utf-8")) == {
        "version": 1,
        "mode": "attach",
        "executable": None,
        "headless": False,
    }
    assert (await browser_settings.runtime.get_browser_settings())["settings"]["mode"] == "attach"
    # The next call starts a browser anew, with the new settings. What the
    # switch shows is not touched.
    assert await browser_settings.closed() == [{"update_capability": False}]


async def test_a_change_of_one_setting_keeps_the_others(browser_settings, tmp_path):
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    await browser_settings.runtime.set_browser_settings(
        {"mode": "attach", "executable": str(executable), "headless": True}
    )

    answer = await browser_settings.runtime.set_browser_settings({"headless": False})

    assert answer["settings"] == {"mode": "attach", "executable": str(executable), "headless": False}
    await browser_settings.closed()


@pytest.mark.parametrize("blank", ["", "   ", None])
async def test_a_blank_executable_goes_back_to_searching_for_a_browser(browser_settings, tmp_path, blank):
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    await browser_settings.runtime.set_browser_settings({"executable": str(executable)})

    answer = await browser_settings.runtime.set_browser_settings({"executable": blank})

    assert answer["settings"]["executable"] is None
    await browser_settings.closed()


@pytest.mark.parametrize("headless", ["yes", 1, None])
async def test_only_a_plain_true_starts_the_browser_without_a_window(browser_settings, headless):
    answer = await browser_settings.runtime.set_browser_settings({"headless": headless})

    assert answer["settings"]["headless"] is False
    await browser_settings.closed()


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ({"mode": "incognito"}, "unknown mode"),
        ({"mode": None}, "unknown mode"),
        ({"mode": "attach", "executable": "/nowhere/at/all/browser"}, "executable not found"),
    ],
)
async def test_a_setting_that_cannot_be_used_is_refused_and_nothing_is_saved_or_closed(browser_settings, payload, why):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await browser_settings.runtime.set_browser_settings(payload)

    assert (raised.value.status_code, raised.value.detail) == (400, why)
    assert not browser_settings.file.exists()
    assert await browser_settings.closed() == []


@pytest.mark.parametrize("name", ["powershell.exe", "my-browser", "chrome.exe.bat", "python"])
async def test_a_file_that_is_no_browser_is_not_taken_for_the_browser(browser_settings, tmp_path, name):
    from fastapi import HTTPException

    other = tmp_path / name
    other.write_bytes(b"")
    # It would be started with a debugging port: not any program will do.
    with pytest.raises(HTTPException) as raised:
        await browser_settings.runtime.set_browser_settings({"executable": str(other)})

    assert raised.value.status_code == 400
    assert raised.value.detail == "executable is not a browser the tools can drive (Chrome, Edge, Brave or Chromium)"
    assert not browser_settings.file.exists()
    assert await browser_settings.closed() == []


async def test_a_directory_is_not_an_executable(browser_settings, tmp_path):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as raised:
        await browser_settings.runtime.set_browser_settings({"executable": str(tmp_path)})

    assert raised.value.status_code == 400
    assert not browser_settings.file.exists()


# ---------------------------------------------------------------------------
# Counting task agents against the free quota, and shutting down
# ---------------------------------------------------------------------------


async def test_a_task_agents_request_is_counted_against_the_agent_models_daily_quota(wiring, monkeypatch):
    import utils.config_manager as config_manager

    counted: list[tuple[str, int]] = []

    class Manager:
        def __init__(self, allowed: bool) -> None:
            self.allowed = allowed

        async def aconsume_agent_daily_quota(self, source: str = "", units: int = 1) -> tuple[bool, dict[str, Any]]:
            counted.append((source, units))
            return self.allowed, {}

    monkeypatch.setattr(config_manager, "get_config_manager", lambda *args, **kwargs: Manager(True))
    assert await wiring.mount_module._count_task_agent_request("c1") is True
    monkeypatch.setattr(config_manager, "get_config_manager", lambda *args, **kwargs: Manager(False))
    assert await wiring.mount_module._count_task_agent_request("c1") is False

    assert counted == [("nekoclaw.task_agent", 1), ("nekoclaw.task_agent", 1)]


async def test_shutting_the_host_down_is_bounded_and_the_steps_still_finish(wiring, server, monkeypatch):
    import config.nekoclaw_settings as settings

    _weather(server)
    await wiring.runtime.nekoclaw_dispatch({"text": "查一下上海的天气", "lanlan_name": "Lan"})
    host = wiring.host_mount.peek_host()
    finished = asyncio.Event()
    real_shutdown = host.shutdown

    async def slow_shutdown() -> None:
        await asyncio.sleep(0.3)
        await real_shutdown()
        finished.set()

    monkeypatch.setattr(host, "shutdown", slow_shutdown)
    monkeypatch.setattr(settings, "HOST_SHUTDOWN_BUDGET_SECONDS", 0.05)
    monkeypatch.setattr(wiring.mount_module, "_unsubscribe", None)

    started = time.monotonic()
    await wiring.mount_module.shutdown_nekoclaw_host()
    waited = time.monotonic() - started

    assert waited < 0.25
    assert not finished.is_set()
    # Not cancelled by the caller that stopped waiting: it runs to its end.
    await asyncio.wait_for(finished.wait(), 5)
    assert host.closed is True


def test_the_budget_for_shutting_down_fits_the_time_the_launcher_gives_the_agent_process():
    import ast
    from pathlib import Path

    from config.nekoclaw_settings import HOST_CLOSE_STOP_WAIT_SECONDS, HOST_SHUTDOWN_BUDGET_SECONDS

    source = (Path(__file__).resolve().parents[3] / "launcher_core" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    merged = next(
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "MERGED_SERVER_SHUTDOWN_TIMEOUTS" for target in node.targets)
    )

    # The host gets a part of what the process has to stop in, never all of it.
    assert HOST_SHUTDOWN_BUDGET_SECONDS <= merged["Agent"] / 2
    assert HOST_CLOSE_STOP_WAIT_SECONDS < HOST_SHUTDOWN_BUDGET_SECONDS
