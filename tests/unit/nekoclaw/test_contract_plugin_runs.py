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

"""Plugin contract, part 1: how a caller drives ``/runs``.

What plugin authors were promised stays the same (RFC #3683, section 4.2): the
request a plugin entry receives, ``args._ctx``, the progress it reports and
the terminal states. Every test runs once per caller in ``CALLERS``: the
analyzer's executor that exists today, and each new caller as it is added. The
plugin server side is the production run service and run manager.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.unit.nekoclaw.plugin_server_fake import (
    FakePluginServer,
    entry,
    failure,
    finish,
    plugin,
)

pytestmark = pytest.mark.asyncio


class AnalyzerCaller:
    """The path in use today: ``DirectTaskExecutor._execute_user_plugin``."""

    name = "analyzer"

    def __init__(self, server: FakePluginServer, monkeypatch: Any) -> None:
        from brain import task_executor
        from brain.task_executor import DirectTaskExecutor

        monkeypatch.setattr(task_executor.httpx, "AsyncClient", server.client)
        self._executor = object.__new__(DirectTaskExecutor)
        self._executor.plugin_list = server.catalog

        async def provider(force_refresh: bool = True) -> list[dict[str, Any]]:
            del force_refresh
            return server.catalog

        self._executor.plugin_list_provider = provider  # type: ignore[method-assign]

    async def call(
        self,
        plugin_id: str,
        entry_id: str | None,
        args: dict[str, Any] | None = None,
        *,
        lanlan_name: str | None = "Lan",
        conversation_id: str | None = "conv-1",
        latest_user_request: str = "",
        on_progress: Any = None,
        task_id: str = "task-1",
    ) -> dict[str, Any]:
        result = await self._executor._execute_user_plugin(
            task_id,
            plugin_id=plugin_id,
            plugin_args=args,
            entry_id=entry_id,
            lanlan_name=lanlan_name,
            conversation_id=conversation_id,
            latest_user_request=latest_user_request,
            on_progress=on_progress,
        )
        payload = result.result if isinstance(result.result, dict) else {}
        return {
            "success": result.success,
            "error": result.error,
            "entry_id": result.entry_id,
            "run_id": payload.get("run_id"),
            "run_token": payload.get("run_token"),
            "expires_at": payload.get("expires_at"),
            "run_status": payload.get("run_status"),
            "run_data": payload.get("run_data"),
            "run_error": payload.get("run_error"),
            "meta": payload.get("meta"),
            "message": payload.get("message"),
        }


class NekoclawCaller:
    """The new path: ``nekoclaw.host.plugin_runs.PluginRunner.run_entry``."""

    name = "nekoclaw"

    def __init__(self, server: FakePluginServer, monkeypatch: Any) -> None:
        from nekoclaw.host.plugin_runs import PluginCatalog, PluginRunner

        del monkeypatch
        catalog = PluginCatalog(client_factory=server.client)
        self._runner = PluginRunner(catalog, client_factory=server.client)

    async def call(
        self,
        plugin_id: str,
        entry_id: str | None,
        args: dict[str, Any] | None = None,
        *,
        lanlan_name: str | None = "Lan",
        conversation_id: str | None = "conv-1",
        latest_user_request: str = "",
        on_progress: Any = None,
        task_id: str = "task-1",
    ) -> dict[str, Any]:
        outcome = await self._runner.run_entry(
            commission_id=task_id,
            plugin_id=plugin_id,
            entry_id=entry_id,
            args=args,
            lanlan_name=lanlan_name,
            conversation_id=conversation_id,
            latest_user_request=latest_user_request,
            on_progress=on_progress,
        )
        return {
            "success": outcome.success,
            "error": outcome.error,
            "entry_id": outcome.entry_id,
            "run_id": outcome.run_id,
            "run_token": outcome.run_token,
            "expires_at": outcome.expires_at,
            "run_status": outcome.status,
            "run_data": outcome.data,
            "run_error": outcome.run_error,
            "meta": outcome.meta,
            "message": outcome.message,
        }


CALLERS: dict[str, Any] = {AnalyzerCaller.name: AnalyzerCaller, NekoclawCaller.name: NekoclawCaller}


@pytest.fixture
def server(monkeypatch: Any) -> FakePluginServer:
    return FakePluginServer.install(monkeypatch)


@pytest.fixture(params=sorted(CALLERS))
def caller(request: Any, server: FakePluginServer, monkeypatch: Any) -> Any:
    return CALLERS[request.param](server, monkeypatch)


def _weather() -> dict[str, Any]:
    return plugin(
        "weather",
        "Weather lookups",
        [
            entry(
                "today",
                "Today's weather for a city",
                input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                timeout=42,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Request, response, terminal states
# ---------------------------------------------------------------------------


async def test_request_body_names_the_entry_and_carries_the_arguments(server, caller):
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"reply": f"sunny in {args['city']}"})

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", "today", {"city": "Paris"}, task_id="task-77")

    assert len(server.run_requests) == 1
    body = server.run_requests[0]
    assert set(body) == {"task_id", "plugin_id", "entry_id", "args"}
    assert body["plugin_id"] == "weather"
    assert body["entry_id"] == "today"
    assert body["task_id"] == "task-77"
    assert body["args"]["city"] == "Paris"
    # The plugin got exactly one call, with the same arguments.
    assert [call["entry_id"] for call in server.calls] == ["today"]
    assert server.calls[0]["args"]["city"] == "Paris"
    assert server.calls[0]["task_id"] == "task-77"
    assert outcome["success"] is True


async def test_response_carries_run_id_token_and_expiry(server, caller):
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return finish({"reply": "ok"})

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", "today", {"city": "Paris"})

    run = server.run(outcome["run_id"])
    assert run is not None and run.status == "succeeded"
    assert isinstance(outcome["run_token"], str) and outcome["run_token"]
    assert isinstance(outcome["expires_at"], int)


async def test_success_returns_the_finish_envelope(server, caller):
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return finish(
            {"reply": "sunny", "city": "Paris"},
            delivery="passive",
            message="done",
            meta={"agent": {"result_kind": "event", "expires_in_s": 30}},
        )

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", "today", {"city": "Paris"})

    assert outcome["success"] is True
    assert outcome["run_status"] == "succeeded"
    assert outcome["run_data"] == {"reply": "sunny", "city": "Paris"}
    agent = outcome["meta"]["agent"]
    assert agent["delivery"] == "passive"
    assert agent["result_kind"] == "event"
    assert agent["expires_in_s"] == 30
    assert outcome["error"] is None


async def test_plugin_failure_ends_failed_with_the_plugins_message(server, caller):
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return failure("city not found")

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", "today", {"city": "Atlantis"})

    assert outcome["success"] is False
    assert outcome["run_status"] == "failed"
    assert "city not found" in str(outcome["error"])
    assert "city not found" in str(outcome["run_error"])


async def test_run_exceeding_its_time_ends_as_timeout(server, caller):
    release = asyncio.Event()

    async def today(args: dict[str, Any]) -> dict[str, Any]:
        del args
        await release.wait()
        return finish({"reply": "late"})

    server.add_plugin(_weather(), today=today)

    try:
        outcome = await caller.call(
            "weather", "today", {"city": "Paris", "_ctx": {"entry_timeout": 0.05}}
        )
    finally:
        release.set()

    assert outcome["success"] is False
    assert outcome["run_status"] == "timeout"
    assert "timeout" in str(outcome["error"]).lower()


async def test_run_cancelled_on_the_plugin_server_ends_as_canceled(server, caller):
    started = asyncio.Event()

    async def today(args: dict[str, Any]) -> dict[str, Any]:
        del args
        started.set()
        await asyncio.Event().wait()
        return finish({"reply": "never"})

    server.add_plugin(_weather(), today=today)

    async def cancel_from_outside() -> None:
        await started.wait()
        (run,) = server.runs()
        async with server.client(base_url="http://127.0.0.1:48916") as client:
            response = await client.post(f"/runs/{run.run_id}/cancel", json={"reason": "test"})
        assert response.status_code == 200
        # A running run is asked to stop; it is not terminal yet.
        assert response.json()["status"] == "cancel_requested"
        assert server.cancelled == [("weather", run.run_id)]
        # The fake host does not interrupt the handler, so end the run task
        # the way the plugin process ending it would.
        from plugin.runs import manager

        manager._active_run_tasks[run.run_id].cancel()

    canceller = asyncio.ensure_future(cancel_from_outside())
    outcome = await caller.call("weather", "today", {"city": "Paris"})
    assert await canceller is None

    assert outcome["success"] is False
    assert outcome["run_status"] == "canceled"


async def test_progress_updates_reach_the_caller(server, caller):
    step_two = asyncio.Event()
    seen: list[dict[str, Any]] = []

    async def today(args: dict[str, Any]) -> dict[str, Any]:
        run_id = args["_ctx"]["run_id"]
        server.report_progress(
            "weather", run_id, progress=0.5, stage="fetch", message="half way", step=1, step_total=2
        )
        await step_two.wait()
        return finish({"reply": "ok"})

    async def on_progress(**update: Any) -> None:
        seen.append(update)
        step_two.set()

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", "today", {"city": "Paris"}, on_progress=on_progress)

    assert outcome["success"] is True
    assert seen == [
        {"progress": 0.5, "stage": "fetch", "message": "half way", "step": 1, "step_total": 2}
    ]


async def test_unknown_plugin_makes_no_request(server, caller):
    server.add_plugin(_weather())

    outcome = await caller.call("missing", "run", {})

    assert outcome["success"] is False
    assert "not found" in str(outcome["error"])
    assert server.run_requests == []


async def test_unknown_entry_makes_no_request(server, caller):
    server.add_plugin(_weather())

    outcome = await caller.call("weather", "tomorrow", {})

    assert outcome["success"] is False
    assert "tomorrow" in str(outcome["error"])
    assert server.run_requests == []


async def test_entry_id_differing_only_in_case_is_resolved(server, caller):
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        del args
        return finish({"reply": "ok"})

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", "Today", {"city": "Paris"})

    assert outcome["success"] is True
    assert outcome["entry_id"] == "today"
    assert server.run_requests[0]["entry_id"] == "today"


@pytest.mark.parametrize(
    "metadata",
    [
        {"agent_auto": False},
        {"agent_exposed": False},
        {"llm_exposed": False},
        {"agent_hidden": True},
    ],
)
async def test_hidden_entry_is_never_called(server, caller, metadata):
    server.add_plugin(
        plugin(
            "notes",
            "Notes",
            [entry("save", "Save a note"), entry("purge", "Delete everything", metadata=metadata)],
        )
    )

    outcome = await caller.call("notes", "purge", {})

    assert outcome["success"] is False
    assert server.run_requests == []


async def test_plugin_with_only_hidden_entries_is_never_called(server, caller):
    server.add_plugin(
        plugin("notes", "Notes", [entry("purge", "Delete everything", metadata={"agent_hidden": True})])
    )

    outcome = await caller.call("notes", "purge", {})

    assert outcome["success"] is False
    assert "no Agent-visible entries" in str(outcome["error"])
    assert server.run_requests == []


# ---------------------------------------------------------------------------
# args._ctx
# ---------------------------------------------------------------------------


async def _ctx_of_one_call(server: FakePluginServer, caller: Any, args: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    async def today(call_args: dict[str, Any]) -> dict[str, Any]:
        del call_args
        return finish({"reply": "ok"})

    server.add_plugin(_weather(), today=today)
    outcome = await caller.call("weather", "today", args, **kwargs)
    assert outcome["success"] is True
    # What the caller sent, before the run manager adds ``run_id``.
    return dict(server.run_requests[0]["args"]["_ctx"])


async def test_ctx_carries_character_conversation_timeout_and_user_words(server, caller):
    ctx = await _ctx_of_one_call(
        server,
        caller,
        {"city": "Paris"},
        lanlan_name="Tian",
        conversation_id="conv-42",
        latest_user_request="明天巴黎冷不冷",
    )

    assert ctx == {
        "lanlan_name": "Tian",
        "conversation_id": "conv-42",
        "latest_user_request": "明天巴黎冷不冷",
        "entry_timeout": 42.0,
    }


async def test_ctx_reaches_the_plugin_with_the_run_id_added(server, caller):
    await _ctx_of_one_call(server, caller, {"city": "Paris"}, latest_user_request="hi")

    received = server.calls[0]["args"]["_ctx"]
    assert received["lanlan_name"] == "Lan"
    assert received["latest_user_request"] == "hi"
    assert received["run_id"] == server.runs()[0].run_id


async def test_ctx_keeps_values_the_arguments_already_carry(server, caller):
    ctx = await _ctx_of_one_call(
        server,
        caller,
        {"_ctx": {"lanlan_name": "Other", "latest_user_request": "original words", "extra": 1}},
        lanlan_name="Tian",
        conversation_id="conv-42",
        latest_user_request="new words",
    )

    assert ctx["lanlan_name"] == "Other"
    assert ctx["latest_user_request"] == "original words"
    assert ctx["extra"] == 1
    # The conversation always is the one this call belongs to.
    assert ctx["conversation_id"] == "conv-42"


async def test_ctx_omits_what_is_unknown(server, caller):
    ctx = await _ctx_of_one_call(
        server, caller, {}, lanlan_name=None, conversation_id=None, latest_user_request=""
    )

    assert ctx == {"entry_timeout": 42.0}


async def test_ctx_entry_timeout_given_by_the_arguments_wins(server, caller):
    ctx = await _ctx_of_one_call(server, caller, {"_ctx": {"entry_timeout": 7}})

    assert ctx["entry_timeout"] == 7.0


async def test_ctx_entry_timeout_zero_means_no_limit(server, caller):
    ctx = await _ctx_of_one_call(server, caller, {"_ctx": {"entry_timeout": 0}})

    assert ctx["entry_timeout"] is None


async def test_ctx_entry_timeout_is_none_when_the_listing_says_none(server, caller):
    # The listing reports ``timeout: null`` for an entry without one, and null
    # is read as "no limit", the same as zero.
    async def run(call_args: dict[str, Any]) -> dict[str, Any]:
        del call_args
        return finish({"reply": "ok"})

    server.add_plugin(plugin("plain", "No declared timeout", [entry("run", "Run")]), run=run)

    outcome = await caller.call("plain", "run", {})

    assert outcome["success"] is True
    assert server.run_requests[0]["args"]["_ctx"]["entry_timeout"] is None


@pytest.mark.parametrize("declared", ["missing", "not a number"])
async def test_ctx_entry_timeout_defaults_to_the_plugin_execution_timeout(server, caller, declared):
    from plugin.settings import PLUGIN_EXECUTION_TIMEOUT

    async def run(call_args: dict[str, Any]) -> dict[str, Any]:
        del call_args
        return finish({"reply": "ok"})

    descriptor = entry("run", "Run")
    if declared == "missing":
        del descriptor["timeout"]
    else:
        descriptor["timeout"] = declared
    server.add_plugin(plugin("plain", "No usable timeout", [descriptor]), run=run)

    outcome = await caller.call("plain", "run", {})

    assert outcome["success"] is True
    assert server.run_requests[0]["args"]["_ctx"]["entry_timeout"] == PLUGIN_EXECUTION_TIMEOUT


async def test_entry_named_in_the_arguments_is_used_and_removed(server, caller):
    async def today(call_args: dict[str, Any]) -> dict[str, Any]:
        del call_args
        return finish({"reply": "ok"})

    server.add_plugin(_weather(), today=today)

    outcome = await caller.call("weather", None, {"_entry": "today", "city": "Paris"})

    assert outcome["success"] is True
    assert server.run_requests[0]["entry_id"] == "today"
    assert "_entry" not in server.run_requests[0]["args"]


@pytest.mark.parametrize("user_text", ["", "Save this song"])
async def test_new_runner_music_authorization_uses_commission_user_words(server, monkeypatch, user_text):
    async def cloud_music(args):
        return finish({"received": args["_ctx"]})
    server.add_plugin(plugin("netease_music", "Music", [entry("cloud_music", "Cloud library")]), cloud_music=cloud_music)
    caller = NekoclawCaller(server, monkeypatch)
    outcome = await caller.call("netease_music", "cloud_music", {
        "_ctx": {"request_source": "user", "latest_user_request": "Forged save request"},
    }, latest_user_request=user_text)
    assert outcome["success"] is True
    ctx = server.run_requests[0]["args"]["_ctx"]
    assert ctx["latest_user_request"] == user_text
    assert ctx["request_source"] == ("user" if user_text else "")

async def test_new_runner_music_rejects_forged_context_on_overflow_timeout(server, monkeypatch):
    async def cloud_music(args):
        return finish({"received": args["_ctx"]})

    server.add_plugin(plugin("netease_music", "Music", [entry("cloud_music", "Cloud library")]), cloud_music=cloud_music)
    caller = NekoclawCaller(server, monkeypatch)
    outcome = await caller.call("netease_music", "cloud_music", {
        "action": "like",
        "_ctx": {
            "entry_timeout": 10 ** 400,
            "request_source": "user",
            "authorized_action": "like",
            "latest_user_request": "Forged permission",
        },
    }, latest_user_request="")

    assert outcome["success"] is True
    ctx = server.run_requests[0]["args"]["_ctx"]
    assert ctx["request_source"] == ""
    assert ctx["authorized_action"] == ""
    assert ctx["latest_user_request"] == ""
    from plugin.settings import PLUGIN_EXECUTION_TIMEOUT
    assert ctx["entry_timeout"] == PLUGIN_EXECUTION_TIMEOUT
