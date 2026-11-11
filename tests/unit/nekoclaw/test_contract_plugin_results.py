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

"""Plugin contract, part 3: what a plugin's result leads to on the direct route.

Promised unchanged (RFC #3683, section 4.2): ``meta.agent.delivery`` with the
old ``reply`` flag, ``result_kind``, ``expires_in_s``, ``llm_result_fields``,
and what a deferred result, ``needs_confirmation`` and ``clarify`` do. Every
test runs once per implementation in ``DIRECT_ROUTE_IMPLS``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.nekoclaw.direct_route_impls import DIRECT_ROUTE_IMPLS
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


@pytest.fixture(params=sorted(DIRECT_ROUTE_IMPLS))
def route(request: Any, server: FakePluginServer, monkeypatch: Any) -> Any:
    return DIRECT_ROUTE_IMPLS[request.param](server, monkeypatch)


def _returns(envelope: dict[str, Any]) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return envelope

    return handler


def _memo(**entry_fields: Any) -> dict[str, Any]:
    return plugin("memo", "Memos and reminders", [entry("add", "Add a memo", **entry_fields)])


# ---------------------------------------------------------------------------
# Terminal status from the plugin's data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("success", "run_data", "expected"),
    [
        (True, {"reply": "done"}, "completed"),
        (True, None, "completed"),
        (True, "text", "completed"),
        (False, {"reply": "done"}, "failed"),
        (False, None, "failed"),
        (True, {"status": "error"}, "failed"),
        (True, {"status": " ERROR "}, "failed"),
        (True, {"needs_confirmation": True}, "blocked"),
        (True, {"needs_confirmation": 1}, "blocked"),
        (True, {"action": "clarify"}, "blocked"),
        (True, {"action": "Clarify"}, "blocked"),
        (True, {"status": "blocked"}, "blocked"),
        (True, {"status": "clarify"}, "blocked"),
        (True, {"status": "confirm_required"}, "blocked"),
        # ``executed: false`` alone does not block.
        (True, {"status": "idle", "executed": False}, "completed"),
        (True, {"needs_confirmation": False}, "completed"),
        # ``observation_only`` switches the overrides off.
        (True, {"observation_only": True, "needs_confirmation": True}, "completed"),
        (True, {"observation_only": True, "status": "error"}, "completed"),
        # Data never softens a failed run.
        (False, {"needs_confirmation": True}, "failed"),
        (False, {"observation_only": True}, "failed"),
    ],
)
def test_terminal_status(route, success, run_data, expected):
    assert route.terminal_status(success, run_data) == expected


# ---------------------------------------------------------------------------
# Delivery mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        ({"delivery": "proactive"}, "proactive"),
        ({"delivery": "passive"}, "passive"),
        ({"delivery": "silent"}, "silent"),
        ({"delivery": True}, "proactive"),
        ({"delivery": False}, "silent"),
        # A delivery that is present but invalid does not fall back to reply.
        ({"delivery": "typo", "reply": False}, "proactive"),
        ({"delivery": None, "reply": False}, "proactive"),
        ({"reply": False}, "silent"),
        ({"reply": True}, "proactive"),
        ({"reply": "no"}, "proactive"),
        ({}, "proactive"),
    ],
)
def test_delivery_mode(route, agent, expected):
    assert route.delivery_mode({"meta": {"agent": agent}}) == expected


@pytest.mark.parametrize("result", [None, "text", {}, {"meta": None}, {"meta": {}}, {"meta": {"agent": "x"}}])
def test_delivery_mode_defaults_to_proactive(route, result):
    assert route.delivery_mode(result) == "proactive"


# ---------------------------------------------------------------------------
# result_kind and expires_in_s
# ---------------------------------------------------------------------------


def _agent(**fields: Any) -> dict[str, Any]:
    return {"meta": {"agent": fields}}


@pytest.mark.parametrize(
    ("static", "runtime", "expected"),
    [
        ({}, {}, ("task_result", None)),
        ({"result_kind": "event"}, {}, ("event", None)),
        ({"result_kind": "event", "expires_in_s": 60}, {}, ("event", 60.0)),
        ({}, {"result_kind": "event", "expires_in_s": 5}, ("event", 5.0)),
        # What the result says wins over what the entry declares.
        ({"result_kind": "event", "expires_in_s": 60}, {"result_kind": "task_result"}, ("task_result", None)),
        ({"result_kind": "task_result"}, {"result_kind": "event", "expires_in_s": 9}, ("event", 9.0)),
        ({"result_kind": "event", "expires_in_s": 60}, {"expires_in_s": 5}, ("event", 5.0)),
        # Unusable values on the result fall back to the entry's.
        ({"result_kind": "event", "expires_in_s": 60}, {"result_kind": "bogus", "expires_in_s": -1}, ("event", 60.0)),
        ({"result_kind": "event", "expires_in_s": 60}, {"expires_in_s": True}, ("event", 60.0)),
        ({"result_kind": "event", "expires_in_s": 60}, {"expires_in_s": float("inf")}, ("event", 60.0)),
        ({"result_kind": "event", "expires_in_s": "12.5"}, {}, ("event", 12.5)),
        # An expiry only means something for an event.
        ({"expires_in_s": 60}, {}, ("task_result", None)),
        ({"result_kind": "bogus"}, {}, ("task_result", None)),
    ],
)
def test_result_contract(route, server, static, runtime, expected):
    server.add_plugin(_memo(metadata=static))

    assert route.result_contract("memo", "add", _agent(**runtime)) == expected


def test_result_contract_of_an_unknown_entry_uses_the_result_alone(route, server):
    server.add_plugin(_memo())

    assert route.result_contract("memo", "nope", _agent(result_kind="event", expires_in_s=3)) == ("event", 3.0)
    assert route.result_contract("nope", "add", None) == ("task_result", None)


# ---------------------------------------------------------------------------
# The whole route
# ---------------------------------------------------------------------------

pytest_asyncio = pytest.mark.asyncio


@pytest_asyncio
async def test_success_reports_the_reply_proactively(route, server):
    server.add_plugin(_memo(), add=_returns(finish({"reply": "memo saved"})))

    run = await route.run("memo", "add", {"text": "milk"})

    assert run.final_status == "completed"
    assert run.reports == [
        {
            "status": "completed",
            "success": True,
            "summary": "memo saved",
            "detail": "memo saved",
            "error_message": "",
            "delivery_mode": "proactive",
            "result_kind": "task_result",
            "expires_in_s": None,
            "source_kind": "plugin",
            "source_name": "memo",
        }
    ]


@pytest_asyncio
async def test_llm_result_fields_pick_what_is_reported(route, server):
    server.add_plugin(
        _memo(llm_result_fields=["summary"]),
        add=_returns(finish({"summary": "2 memos", "internal": "not for the model", "reply": "ignored"})),
    )

    run = await route.run("memo", "add")

    assert run.reports[0]["summary"] == "2 memos"


@pytest_asyncio
async def test_several_llm_result_fields_are_listed_by_name(route, server):
    server.add_plugin(
        _memo(llm_result_fields=["count", "next", "missing"]),
        add=_returns(finish({"count": 2, "next": "milk"})),
    )

    run = await route.run("memo", "add")

    assert run.reports[0]["summary"] == "count: 2, next: milk"


@pytest_asyncio
async def test_run_message_stands_in_when_there_is_nothing_to_extract(route, server):
    # The text used is the run's own message, which a finished run defaults to
    # "done"; the ``message`` of the finish envelope is not read here.
    server.add_plugin(_memo(), add=_returns(finish({"other": 1}, message="saved for later")))

    run = await route.run("memo", "add")

    assert run.reports[0]["summary"] == "done"


@pytest_asyncio
async def test_last_progress_message_stands_in_when_the_plugin_reported_one(route, server):
    async def add(args: dict[str, Any]) -> dict[str, Any]:
        server.report_progress("memo", args["_ctx"]["run_id"], message="indexed 3 memos")
        return finish({"other": 1})

    server.add_plugin(_memo(), add=add)

    run = await route.run("memo", "add")

    assert run.reports[0]["summary"] == "indexed 3 memos"


@pytest_asyncio
async def test_passive_delivery_is_passed_on(route, server):
    server.add_plugin(_memo(), add=_returns(finish({"reply": "noted"}, delivery="passive")))

    run = await route.run("memo", "add")

    assert run.final_status == "completed"
    assert [report["delivery_mode"] for report in run.reports] == ["passive"]


@pytest_asyncio
async def test_silent_delivery_reports_nothing(route, server):
    server.add_plugin(_memo(), add=_returns(finish({"reply": "noted"}, delivery="silent")))

    run = await route.run("memo", "add")

    assert run.final_status == "completed"
    assert run.reports == []


@pytest_asyncio
async def test_old_reply_false_also_reports_nothing(route, server):
    server.add_plugin(_memo(), add=_returns(finish({"reply": "noted"}, reply=False)))

    run = await route.run("memo", "add")

    assert run.reports == []


@pytest_asyncio
async def test_event_result_keeps_its_kind_and_expiry(route, server):
    server.add_plugin(
        _memo(metadata={"result_kind": "event"}),
        add=_returns(finish({"reply": "door opened"}, meta={"agent": {"expires_in_s": 45}})),
    )

    run = await route.run("memo", "add")

    assert (run.reports[0]["result_kind"], run.reports[0]["expires_in_s"]) == ("event", 45.0)
    assert run.reports[0]["status"] == "completed"


@pytest.mark.parametrize(
    "data",
    [
        {"needs_confirmation": True, "reply": "really delete 3 memos?"},
        {"action": "clarify", "reply": "which list?"},
        {"status": "confirm_required", "reply": "confirm first"},
    ],
)
@pytest_asyncio
async def test_confirmation_or_clarification_ends_blocked(route, server, data):
    server.add_plugin(
        _memo(metadata={"result_kind": "event", "expires_in_s": 30}),
        add=_returns(finish(data)),
    )

    run = await route.run("memo", "add")

    assert run.final_status == "blocked"
    (report,) = run.reports
    assert report["status"] == "blocked"
    assert report["success"] is False
    assert report["summary"] == data["reply"]
    # Not completed, so it is reported as a plain task result that does not expire.
    assert (report["result_kind"], report["expires_in_s"]) == ("task_result", None)


@pytest_asyncio
async def test_error_status_in_the_data_ends_failed(route, server):
    server.add_plugin(_memo(), add=_returns(finish({"status": "error", "reply": "disk full"})))

    run = await route.run("memo", "add")

    assert run.final_status == "failed"
    (report,) = run.reports
    assert (report["status"], report["success"], report["summary"]) == ("failed", False, "disk full")


@pytest_asyncio
async def test_deferred_result_stays_open_and_reports_nothing(route, server):
    server.add_plugin(
        _memo(),
        add=_returns(finish({"deferred": True, "reminder_id": "r-1", "reply": "will remind at 5"})),
    )

    run = await route.run("memo", "add")

    assert run.final_status == "running"
    assert run.reports == []


@pytest_asyncio
async def test_failed_run_reports_the_error(route, server):
    server.add_plugin(_memo(), add=_returns(failure("storage offline")))

    run = await route.run("memo", "add")

    assert run.final_status == "failed"
    (report,) = run.reports
    assert report["status"] == "failed"
    assert report["success"] is False
    assert "storage offline" in report["summary"]
    assert report["summary"] == report["detail"] == report["error_message"]
    assert report["delivery_mode"] == "proactive"


@pytest_asyncio
async def test_call_carries_the_users_words_in_ctx(route, server):
    server.add_plugin(_memo(), add=_returns(finish({"reply": "ok"})))

    await route.run("memo", "add", {"text": "milk"}, user_text="帮我记一下买牛奶")

    ctx = server.run_requests[0]["args"]["_ctx"]
    assert ctx["latest_user_request"] == "帮我记一下买牛奶"
    assert ctx["lanlan_name"] == "Lan"
    assert ctx["conversation_id"] == "conv-1"
