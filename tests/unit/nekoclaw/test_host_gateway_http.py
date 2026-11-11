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

"""The capability gateway over its real listener, and a familiar using it.

These tests open a loopback socket, so they run in the serial shard.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from nekoclaw.runtime.tools.gateway import GatewayClient, GatewayError
from tests.unit.nekoclaw.fakes import ScriptedLLM, call, finish
from tests.unit.nekoclaw.host_fakes import (
    FakeDecisionLLM,
    fake_model,
    make_host,
    scripted_runtime,
    wait_status,
    wait_until,
)
from tests.unit.nekoclaw.plugin_server_fake import FakePluginServer, entry, finish as plugin_finish, plugin

pytestmark = pytest.mark.integration_serial


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
        return plugin_finish({"reply": f"sunny in {args.get('city')}"})

    server.add_plugin(
        plugin(
            "weather",
            "Weather lookups",
            [
                entry(
                    "today",
                    "Today's weather for a city",
                    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                    llm_result_fields=[],
                )
            ],
        ),
        today=handler or today,
    )


async def _working_commission(host: Any) -> Any:
    commission, _created = await host.ledger.create(
        lanlan_name="Lan", title="trip", dispatch_text="plan the trip", user_text="plan my trip", source="dispatch", route="familiar"
    )
    await host.ledger.transition(commission.id, "working")
    return commission


async def _post(url: str, token: str | None, body: Any, **headers: str) -> httpx.Response:
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(proxy=None, trust_env=False, timeout=10) as client:
        if isinstance(body, (bytes, str)):
            return await client.post(url, content=body, headers=headers)
        return await client.post(url, json=body, headers=headers)


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------


async def test_listener_starts_on_first_use_on_the_loopback_address(server, hosts, tmp_path):
    host = make_host(tmp_path, server)
    hosts.append(host)
    assert not host.gateway.started

    url = await host.gateway.start()

    assert url.startswith("http://127.0.0.1:") and url.endswith("/mcp")
    assert await host.gateway.start() == url
    await host.gateway.stop()
    assert not host.gateway.started
    with pytest.raises(httpx.HTTPError):
        await _post(url, "x", {"jsonrpc": "2.0", "id": 1, "method": "ping"})


async def test_requests_without_a_known_token_are_refused(server, hosts, tmp_path):
    host = make_host(tmp_path, server)
    hosts.append(host)
    url = await host.gateway.start()
    token = host.gateway.issue((await _working_commission(host)).id)
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}

    assert (await _post(url, None, ping)).status_code == 401
    assert (await _post(url, "not-the-token", ping)).status_code == 401
    assert (await _post(url, token, ping)).json() == {"jsonrpc": "2.0", "id": 1, "result": {}}


async def test_only_post_to_the_mcp_path_from_this_machine(server, hosts, tmp_path):
    host = make_host(tmp_path, server)
    hosts.append(host)
    url = await host.gateway.start()
    token = host.gateway.issue((await _working_commission(host)).id)
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(proxy=None, trust_env=False, timeout=10) as client:
        get = await client.get(url, headers=headers)
        elsewhere = await client.post(url.replace("/mcp", "/other"), json=ping, headers=headers)
    foreign = await _post(url, token, ping, Origin="https://evil.example")
    local = await _post(url, token, ping, Origin="http://127.0.0.1:5173")
    garbage = await _post(url, token, b"{not json")
    notification = await _post(url, token, {"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert (get.status_code, get.headers["allow"]) == (405, "POST")
    assert elsewhere.status_code == 404
    # A web page cannot drive the gateway, even if it got hold of a token.
    assert foreign.status_code == 403
    assert local.status_code == 200
    assert garbage.status_code == 400 and garbage.json()["error"]["code"] == -32700
    assert (notification.status_code, notification.content) == (202, b"")


async def test_runtime_client_lists_and_calls_tools(server, hosts, tmp_path):
    _weather(server)
    host = make_host(tmp_path, server)
    hosts.append(host)
    url = await host.gateway.start()
    commission = await _working_commission(host)
    client = GatewayClient(url, host.gateway.issue(commission.id, candidate_plugin_ids=["weather"]))

    definitions = await client.connect()
    text, is_error = await client.call("plugin__weather__today", {"city": "Lyon"})

    assert [definition["name"] for definition in definitions] == ["plugin__weather__today"]
    assert (text, is_error) == ("sunny in Lyon", False)
    assert server.run_requests[0]["task_id"] == commission.id
    assert server.run_requests[0]["args"]["_ctx"]["latest_user_request"] == "plan my trip"


async def test_revoked_token_stops_working(server, hosts, tmp_path):
    _weather(server)
    host = make_host(tmp_path, server)
    hosts.append(host)
    url = await host.gateway.start()
    commission = await _working_commission(host)
    client = GatewayClient(url, host.gateway.issue(commission.id, candidate_plugin_ids=["weather"]))
    await client.connect()

    host.gateway.revoke(commission.id)

    with pytest.raises(GatewayError, match="401"):
        await client.call("plugin__weather__today", {"city": "Lyon"})
    assert server.calls == []


async def test_dropping_a_call_cancels_the_plugin_run(server, hosts, tmp_path):
    started = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return plugin_finish({"reply": "never"})

    _weather(server, slow)
    host = make_host(tmp_path, server)
    hosts.append(host)
    url = await host.gateway.start()
    commission = await _working_commission(host)
    client = GatewayClient(url, host.gateway.issue(commission.id, candidate_plugin_ids=["weather"]))
    await client.connect()

    calling = asyncio.ensure_future(client.call("plugin__weather__today", {"city": "Lyon"}))
    await asyncio.wait_for(started.wait(), 10)
    (run,) = server.runs()
    calling.cancel()
    await asyncio.gather(calling, return_exceptions=True)

    # The familiar gave up on the call; the run is cancelled on the plugin server.
    await wait_until(lambda: server.cancelled == [("weather", run.run_id)], what="the run being cancelled")


# ---------------------------------------------------------------------------
# A familiar using the gateway
# ---------------------------------------------------------------------------


def _familiar_host(hosts: list[Any], tmp_path: Any, server: FakePluginServer, llm: ScriptedLLM, **kwargs: Any) -> Any:
    host = make_host(
        tmp_path,
        server,
        familiar_model=fake_model,
        # The fine selection hands the task to a familiar.
        decision_llm=FakeDecisionLLM(fine=[{"route": "familiar", "reason": "several steps"}]),
        runtime_factory=scripted_runtime(lambda spec: llm),
        **kwargs,
    )
    hosts.append(host)
    return host


async def test_familiar_calls_a_plugin_entry_through_the_gateway(server, hosts, tmp_path):
    _weather(server)
    server.add_plugin(plugin("lamp", "Controls the desk lamp", [entry("switch", "Turn the lamp on or off")]))
    llm = ScriptedLLM(
        [
            [call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")],
            [call("report", {"status": "ready", "summary": "Lyon is sunny; pack light"}, "c2"), finish("tool_calls")],
        ]
    )
    host = _familiar_host(hosts, tmp_path, server, llm)
    assert not host.gateway.started

    receipt = await host.dispatch(
        "check the weather in Lyon and write packing advice", lanlan_name="Lan", user_text="what should I pack for Lyon", lang="en"
    )

    done = await wait_status(host, receipt.commission_id, "done")
    assert done.route == "familiar"
    assert host.gateway.started
    # Offered next to the built-in tools, by the names the gateway gave them.
    offered = [tool["function"]["name"] for tool in llm.overrides[0]["tools"]]
    assert {"plugin__weather__today", "plugin__lamp__switch", "read", "report"} <= set(offered)
    assert "capability_search" not in offered
    assert "plugin__ or mcp__" in llm.requests[0][0]["content"]
    # The call reached the plugin as a normal run under this commission.
    (request,) = server.run_requests
    assert (request["task_id"], request["plugin_id"], request["entry_id"]) == (receipt.commission_id, "weather", "today")
    assert request["args"]["_ctx"]["latest_user_request"] == "what should I pack for Lyon"
    assert llm.requests[1][-1]["content"] == "sunny in Lyon"
    # The entry's result went to the familiar only; she gets the familiar's report.
    reports = [item for item in await host.ledger.outbox_pending() if item.kind == "task_result"]
    assert [report.payload["summary"] for report in reports] == ["Lyon is sunny; pack light"]
    assert reports[0].payload["channel"] == "familiar"
    # The token died with the commission.
    await wait_until(lambda: host.familiars.running == [], what="the session being released")
    assert host.gateway._by_commission == {}


async def test_stopping_the_commission_cancels_the_run_its_familiar_started(server, hosts, tmp_path):
    started = asyncio.Event()

    async def slow(args: dict[str, Any]) -> dict[str, Any]:
        started.set()
        await asyncio.Event().wait()
        return plugin_finish({"reply": "never"})

    _weather(server, slow)
    llm = ScriptedLLM([[call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")]])
    host = _familiar_host(hosts, tmp_path, server, llm)
    receipt = await host.dispatch("check the weather in Lyon and write packing advice", lanlan_name="Lan", lang="en")
    await asyncio.wait_for(started.wait(), 15)
    (run,) = server.runs()

    await host.abort(receipt.commission_id)

    assert (await host.ledger.get(receipt.commission_id)).status == "cancelled"
    assert server.cancelled == [("weather", run.run_id)]
    await wait_until(lambda: host.familiars.running == [], what="the session being closed")


async def test_mcp_tool_asks_the_user_through_the_commission(server, hosts, tmp_path):
    async def search(args: dict[str, Any]) -> dict[str, Any]:
        return plugin_finish({"summary": "3 notes about Lyon"})

    server.add_plugin(
        plugin(
            "mcp_adapter",
            "MCP Adapter",
            [entry("mcp_notes_search", "Search the notes", name="[notes] search", llm_result_fields=["summary"])],
        ),
        mcp_notes_search=search,
    )
    llm = ScriptedLLM(
        [
            [call("mcp__notes__search", {}), finish("tool_calls")],
            [call("report", {"status": "ready", "summary": "found 3 notes"}, "c2"), finish("tool_calls")],
        ]
    )
    host = _familiar_host(hosts, tmp_path, server, llm)

    receipt = await host.dispatch("look through my notes about Lyon and summarise them", lanlan_name="Lan", lang="en")

    waiting = await wait_status(host, receipt.commission_id, "waiting_user")
    question = waiting.question()
    assert (question["kind"], question["category"], question["key"]) == ("permission", "mcp_server", "notes")
    assert server.calls == []

    answered = await host.dispatch("yes, always", lanlan_name="Lan", lang="en")

    assert answered.kind == "answered"
    await wait_status(host, receipt.commission_id, "done")
    assert llm.requests[1][-1]["content"] == "3 notes about Lyon"
    assert await host.permission_store.mode("mcp_server", "notes") == "allow"
