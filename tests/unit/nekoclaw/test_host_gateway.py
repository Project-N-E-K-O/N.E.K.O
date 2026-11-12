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

"""The capability gateway, asked directly: tools, calls, permissions.

Nothing here opens a socket; ``test_host_gateway_http.py`` covers the listener
and a familiar using it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nekoclaw.host.gateway import CALL_TOOL, SEARCH_TOOL
from tests.unit.nekoclaw.host_fakes import FakeDecisionLLM, make_host, wait_until
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
async def host(server: FakePluginServer, tmp_path: Any) -> Any:
    built = make_host(tmp_path, server, decision_llm=FakeDecisionLLM())
    yield built
    await built.shutdown()


def _returns(envelope: dict[str, Any]) -> Any:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        return envelope

    return handler


def _catalog(server: FakePluginServer) -> None:
    async def today(args: dict[str, Any]) -> dict[str, Any]:
        return finish({"reply": f"sunny in {args.get('city')}"})

    server.add_plugin(
        plugin(
            "weather",
            "Weather lookups",
            [
                entry(
                    "today",
                    "Today's weather for a city",
                    input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
                ),
                entry("admin", "Reset the cache", metadata={"agent_hidden": True}),
            ],
        ),
        today=today,
    )
    server.add_plugin(
        plugin("lamp", "Controls the desk lamp", [entry("switch", "Turn the lamp on or off")]),
        switch=_returns(finish({"reply": "lamp is off"})),
    )
    server.add_plugin(plugin("radio", "Plays radio", [entry("play", "Play a station")], passive=True))


async def _commission(host: Any, **values: Any) -> Any:
    fields = {
        "lanlan_name": "Lan",
        "title": "plan the trip",
        "dispatch_text": "plan the trip",
        "user_text": "帮我规划一下行程",
        "source": "dispatch",
        "route": "familiar",
        "conversation_id": "conv-3",
        "lang": "en",
    }
    fields.update(values)
    commission, _created = await host.ledger.create(**fields)
    await host.ledger.transition(commission.id, "working")
    return commission


def _grant(host: Any, commission: Any, *candidates: str) -> Any:
    token = host.gateway.issue(commission.id, candidate_plugin_ids=candidates)
    return host.gateway._grant(token)


async def _rpc(host: Any, grant: Any, method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> Any:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return await host.gateway.handle(grant, message)


async def _call(host: Any, grant: Any, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    answer = await _rpc(host, grant, "tools/call", {"name": name, "arguments": arguments or {}})
    return answer["result"]


def _text(result: dict[str, Any]) -> str:
    return "\n".join(item["text"] for item in result["content"])


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


async def test_initialize_ping_and_unknown_methods(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host))

    initialized = await _rpc(host, grant, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}})
    newer = await _rpc(host, grant, "initialize", {"protocolVersion": "2099-01-01"})
    ping = await _rpc(host, grant, "ping")
    unknown = await _rpc(host, grant, "resources/list")

    assert initialized["result"]["protocolVersion"] == "2025-03-26"
    assert initialized["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert initialized["result"]["serverInfo"]["name"] == "nekoclaw-gateway"
    # A version it does not know is answered with the newest one it does.
    assert newer["result"]["protocolVersion"] == "2025-06-18"
    assert ping == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert unknown["error"]["code"] == -32601


async def test_notifications_get_no_answer_and_bad_messages_an_error(host, server):
    grant = _grant(host, await _commission(host))

    assert await host.gateway.handle(grant, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert (await host.gateway.handle(grant, {"id": 1, "method": "ping"}))["error"]["code"] == -32600
    assert (await host.gateway.handle(grant, "ping"))["error"]["code"] == -32600
    batch = await host.gateway.handle(
        grant,
        [{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "notifications/initialized"}],
    )
    assert batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


# ---------------------------------------------------------------------------
# What is offered
# ---------------------------------------------------------------------------


async def test_candidates_are_listed_and_the_rest_is_reached_through_search(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host), "weather")

    tools = (await _rpc(host, grant, "tools/list"))["result"]["tools"]

    assert [tool["name"] for tool in tools] == ["plugin__weather__today", SEARCH_TOOL, CALL_TOOL]
    weather = tools[0]
    assert weather["description"] == "weather: today. Today's weather for a city"
    assert weather["inputSchema"]["required"] == ["city"]


async def test_hidden_entries_and_passive_plugins_are_never_served(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host), "weather", "lamp", "radio")

    names = [tool["name"] for tool in (await _rpc(host, grant, "tools/list"))["result"]["tools"]]
    hidden = await _call(host, grant, "plugin__weather__admin")
    passive = await _call(host, grant, CALL_TOOL, {"name": "plugin__radio__play"})
    found = _text(await _call(host, grant, SEARCH_TOOL, {"query": "radio station cache reset"}))

    # Everything visible is listed, so the search tools are not needed.
    assert names == ["plugin__weather__today", "plugin__lamp__switch"]
    assert hidden["isError"] and "no capability named plugin__weather__admin" in _text(hidden)
    assert passive["isError"]
    assert "plugin__radio__play" not in found and "plugin__weather__admin" not in found
    assert server.calls == []


async def test_without_any_capability_no_tool_is_offered(host, server):
    grant = _grant(host, await _commission(host))

    assert (await _rpc(host, grant, "tools/list"))["result"]["tools"] == []


async def test_listed_tools_stay_within_the_token_budget(host, server):
    from config.nekoclaw_settings import GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS, GATEWAY_TOOL_LIST_MAX_TOKENS
    from utils.tokenize import count_tokens

    entries = [entry(f"entry{n}", "does something " + "very specific " * 400) for n in range(60)]
    server.add_plugin(plugin("big", "A plugin with many entries", entries), entry59=_returns(finish({"reply": "ok"})))
    grant = _grant(host, await _commission(host), "big")

    tools = (await _rpc(host, grant, "tools/list"))["result"]["tools"]

    listed = [tool for tool in tools if tool["name"].startswith("plugin__")]
    assert 0 < len(listed) < 60
    assert count_tokens(json.dumps(listed, ensure_ascii=False)) <= GATEWAY_TOOL_LIST_MAX_TOKENS + 2 * len(listed)
    assert all(count_tokens(tool["description"]) <= GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS for tool in listed)
    # What did not fit is still reachable.
    assert [tool["name"] for tool in tools[-2:]] == [SEARCH_TOOL, CALL_TOOL]
    reachable = await _call(host, grant, CALL_TOOL, {"name": "plugin__big__entry59"})
    assert not reachable["isError"]


async def test_search_finds_capabilities_by_what_they_do(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host))

    found = _text(await _call(host, grant, SEARCH_TOOL, {"query": "turn off the lamp"}))
    nothing = _text(await _call(host, grant, SEARCH_TOOL, {"query": "zzz"}))

    definitions = [json.loads(line) for line in found.splitlines()]
    assert definitions[0]["name"] == "plugin__lamp__switch"
    assert definitions[0]["inputSchema"] == {"type": "object", "properties": {}}
    assert nothing == "No capability matches 'zzz'."


# ---------------------------------------------------------------------------
# Calling a plugin entry
# ---------------------------------------------------------------------------


async def test_call_goes_through_the_same_runs_request_with_ctx(host, server):
    _catalog(server)
    commission = await _commission(host)
    grant = _grant(host, commission, "weather")

    result = await _call(host, grant, "plugin__weather__today", {"city": "Paris"})

    assert result["isError"] is False
    assert _text(result).startswith("sunny in Paris")
    (request,) = server.run_requests
    assert set(request) == {"task_id", "plugin_id", "entry_id", "args"}
    assert (request["task_id"], request["plugin_id"], request["entry_id"]) == (commission.id, "weather", "today")
    assert request["args"]["city"] == "Paris"
    assert request["args"]["_ctx"] == {
        "lanlan_name": "Lan",
        "conversation_id": "conv-3",
        "latest_user_request": "帮我规划一下行程",
        "entry_timeout": None,
    }
    runs = [event.payload for event in await host.ledger.events(commission.id) if event.type == "plugin_run"]
    assert runs == [{"run_id": server.runs()[0].run_id, "plugin_id": "weather", "entry_id": "today", "via": "gateway"}]


async def test_capability_call_reaches_an_entry_that_was_not_listed(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host), "weather")

    result = await _call(host, grant, CALL_TOOL, {"name": "plugin__lamp__switch", "arguments": {}})

    assert _text(result).startswith("lamp is off")
    assert [call["entry_id"] for call in server.calls] == ["switch"]


async def test_capability_call_needs_a_name(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, CALL_TOOL, {"arguments": {}})

    assert result["isError"]


async def test_result_fields_the_entry_declares_are_all_a_familiar_gets(host, server):
    server.add_plugin(
        plugin("memo", "Memos", [entry("list", "List memos", llm_result_fields=["summary"])]),
        list=_returns(finish({"summary": "2 memos", "internal": {"token": "secret"}})),
    )
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__list")

    assert _text(result) == "2 memos"


async def test_data_is_added_when_the_entry_declares_no_result_fields(host, server):
    described = entry("list", "List memos")
    del described["llm_result_fields"]
    server.add_plugin(plugin("memo", "Memos", [described]), list=_returns(finish({"items": ["milk", "eggs"]})))
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__list")

    assert _text(result) == 'done\n{"items": ["milk", "eggs"]}'


async def test_data_is_added_when_the_plugin_list_shows_no_result_fields(host, server):
    # ``GET /plugins`` lists ``[]`` for an entry that declares none.
    described = entry("list", "List memos")
    assert described["llm_result_fields"] == []
    server.add_plugin(plugin("memo", "Memos", [described]), list=_returns(finish({"items": ["milk", "eggs"]})))
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__list")

    assert _text(result) == 'done\n{"items": ["milk", "eggs"]}'


async def test_result_fields_are_read_before_the_call(host, server):
    async def listing(args: dict[str, Any]) -> dict[str, Any]:
        # The plugin is switched off while it runs: the list no longer shows it.
        server.catalog.clear()
        host.registry.invalidate()
        return finish({"summary": "2 memos", "internal": {"token": "secret"}})

    server.add_plugin(plugin("memo", "Memos", [entry("list", "List memos", llm_result_fields=["summary"])]), list=listing)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__list")

    assert _text(result) == "2 memos"


async def test_failed_run_is_an_error_result(host, server):
    server.add_plugin(plugin("memo", "Memos", [entry("list", "List memos")]), list=_returns(failure("storage offline")))
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__list")

    assert result["isError"] is True
    assert "storage offline" in _text(result)


async def test_confirmation_request_is_passed_to_the_familiar_as_such(host, server):
    server.add_plugin(
        plugin("memo", "Memos", [entry("purge", "Delete all memos", llm_result_fields=["reply"])]),
        purge=_returns(finish({"needs_confirmation": True, "reply": "really delete 3 memos?"})),
    )
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__purge")

    assert result["isError"] is False
    assert _text(result) == (
        "[The plugin did not carry this out: it needs the user's confirmation or a clarification first.]\n"
        "really delete 3 memos?"
    )


async def test_deferred_result_is_marked(host, server):
    server.add_plugin(
        plugin("memo", "Memos", [entry("remind", "Remind later", llm_result_fields=["reply"])]),
        remind=_returns(finish({"deferred": True, "reply": "will remind at five"})),
    )
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__remind")

    assert _text(result) == "[Accepted; the plugin completes this later on its own.]\nwill remind at five"


async def test_result_text_is_capped_in_tokens(host, server):
    from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS
    from utils.tokenize import count_tokens

    # No declared result fields: the data itself is handed over, and that is
    # where a result can get large.
    described = entry("dump", "Dump everything")
    del described["llm_result_fields"]
    server.add_plugin(plugin("memo", "Memos", [described]), dump=_returns(finish({"text": "word " * 20000})))
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__dump")

    text = _text(result)
    assert text.splitlines()[0] == "done"
    assert text.splitlines()[1].startswith('{"text": "word word')
    assert GATEWAY_RESULT_MAX_TOKENS - 50 <= count_tokens(text) <= GATEWAY_RESULT_MAX_TOKENS


async def test_a_commission_that_ended_can_call_nothing(host, server):
    _catalog(server)
    commission = await _commission(host)
    grant = _grant(host, commission, "weather")
    await host.ledger.transition(commission.id, "cancelled")

    result = await _call(host, grant, "plugin__weather__today", {"city": "Paris"})

    assert result["isError"] and "no longer under way" in _text(result)
    assert server.calls == []


async def test_tokens_are_per_commission_and_revocable(host, server):
    one = await _commission(host)
    two = await _commission(host, title="other")
    first = host.gateway.issue(one.id)
    second = host.gateway.issue(two.id)

    assert first != second
    assert host.gateway._grant(first).commission_id == one.id
    assert host.gateway._grant(second).commission_id == two.id
    assert host.gateway._grant("made-up") is None and host.gateway._grant(None) is None

    reissued = host.gateway.issue(one.id)
    assert host.gateway._grant(first) is None
    assert host.gateway._grant(reissued).commission_id == one.id
    host.gateway.revoke(one.id)
    assert host.gateway._grant(reissued) is None
    assert host.gateway._grant(second) is not None


# ---------------------------------------------------------------------------
# Permissions at the gateway's entry
# ---------------------------------------------------------------------------


class Asker:
    def __init__(self, choice: str) -> None:
        self.choice = choice
        self.asked: list[Any] = []

    async def __call__(self, commission_id: str, ask: Any) -> str:
        self.asked.append((commission_id, ask))
        return self.choice


def _mcp(server: FakePluginServer) -> None:
    server.add_plugin(
        plugin(
            "mcp_adapter",
            "MCP Adapter",
            [
                # As the adapter registers them: named "[server] tool", reading ``summary``.
                entry("mcp_notes_search", "Search the notes", name="[notes] search", llm_result_fields=["summary"]),
                entry("mcp_notes_create", "Create a note", name="[notes] create", llm_result_fields=["summary"]),
                entry("mcp_calendar_today", "Today's events", name="[calendar] today", llm_result_fields=["summary"]),
                entry("connect_server", "Connect an MCP server", name="Connect"),
            ],
        ),
        mcp_notes_search=_returns(finish({"summary": "3 notes"})),
        mcp_notes_create=_returns(finish({"summary": "created"})),
        mcp_calendar_today=_returns(finish({"summary": "no events"})),
        connect_server=_returns(finish({"reply": "connected"})),
    )


async def test_plugin_entries_are_allowed_without_asking(host, server):
    _catalog(server)
    asker = Asker("deny")
    host.permissions.set_asker(asker)
    grant = _grant(host, await _commission(host), "weather")

    result = await _call(host, grant, "plugin__weather__today", {"city": "Paris"})

    assert not result["isError"]
    assert asker.asked == []


async def test_plugin_entries_can_be_forbidden_in_the_settings(host, server):
    _catalog(server)
    await host.permission_store.set_mode("plugin_entry", "deny")
    commission = await _commission(host)
    grant = _grant(host, commission, "weather")

    result = await _call(host, grant, "plugin__weather__today", {"city": "Paris"})

    assert result["isError"] and "the permission settings forbid this" in _text(result)
    assert server.calls == []
    denied = [event for event in await host.ledger.events(commission.id) if event.type == "capability_denied"]
    assert denied[0].payload["capability"] == "plugin:weather:today"


async def test_each_mcp_server_is_asked_about_once_when_always_is_chosen(host, server):
    _mcp(server)
    asker = Asker("always")
    host.permissions.set_asker(asker)
    commission = await _commission(host)
    grant = _grant(host, commission)

    first = await _call(host, grant, CALL_TOOL, {"name": "mcp__notes__search"})
    second = await _call(host, grant, CALL_TOOL, {"name": "mcp__notes__create"})
    third = await _call(host, grant, CALL_TOOL, {"name": "mcp__calendar__today"})

    assert [_text(result) for result in (first, second, third)] == ["3 notes", "created", "no events"]
    assert [(cid, ask.category, ask.key) for cid, ask in asker.asked] == [
        (commission.id, "mcp_server", "notes"),
        (commission.id, "mcp_server", "calendar"),
    ]
    assert asker.asked[0][1].detail["capability"] == "mcp:notes:search"
    assert await host.permission_store.mode("mcp_server", "notes") == "allow"
    # The call itself is the adapter's entry, through the same runs request.
    assert [call["entry_id"] for call in server.calls] == ["mcp_notes_search", "mcp_notes_create", "mcp_calendar_today"]


async def test_declined_mcp_server_is_not_called(host, server):
    _mcp(server)
    host.permissions.set_asker(Asker("deny"))
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, CALL_TOOL, {"name": "mcp__notes__search"})

    assert result["isError"] and "the user declined" in _text(result)
    assert server.calls == []


async def test_the_adapters_own_entries_stay_plugin_entries(host, server):
    _mcp(server)
    asker = Asker("deny")
    host.permissions.set_asker(asker)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, CALL_TOOL, {"name": "plugin__mcp_adapter__connect_server"})

    assert _text(result).startswith("connected")
    assert asker.asked == []


async def test_the_adapters_entries_that_call_any_server_are_not_served(host, server):
    server.add_plugin(
        plugin(
            "mcp_adapter",
            "MCP Adapter",
            [
                entry("mcp_notes_search", "Search the notes", name="[notes] search", llm_result_fields=["summary"]),
                entry("call_tool", "Call a tool of a configured MCP server", name="Call MCP Tool"),
                entry("gateway_invoke", "Call an MCP tool through Gateway Core", name="Gateway Invoke"),
            ],
        ),
        call_tool=_returns(finish({"summary": "called"})),
        gateway_invoke=_returns(finish({"summary": "called"})),
    )
    asker = Asker("deny")
    host.permissions.set_asker(asker)
    grant = _grant(host, await _commission(host), "mcp_adapter")

    names = [tool["name"] for tool in (await _rpc(host, grant, "tools/list"))["result"]["tools"]]
    direct = await _call(host, grant, "plugin__mcp_adapter__call_tool", {"server_name": "notes", "tool_name": "search"})
    through = await _call(host, grant, CALL_TOOL, {"name": "plugin__mcp_adapter__gateway_invoke"})
    found = _text(await _call(host, grant, SEARCH_TOOL, {"query": "call MCP tool gateway invoke"}))

    # Each server's tools are served on their own, under that server's permission.
    assert names == ["mcp__notes__search"]
    assert direct["isError"] and through["isError"]
    assert "call_tool" not in found and "gateway_invoke" not in found
    assert server.calls == [] and asker.asked == []


async def test_entries_whose_names_differ_only_in_where_the_separator_falls_are_told_apart(host, server):
    server.add_plugin(plugin("a__b", "First", [entry("c", "First entry")]), c=_returns(finish({"reply": "first"})))
    server.add_plugin(plugin("a", "Second", [entry("b__c", "Second entry")]), b__c=_returns(finish({"reply": "second"})))
    grant = _grant(host, await _commission(host), "a__b", "a")

    tools = [tool["name"] for tool in (await _rpc(host, grant, "tools/list"))["result"]["tools"]]

    assert len(tools) == 2 and len(set(tools)) == 2
    answers = [_text(await _call(host, grant, name)).splitlines()[0] for name in tools]
    assert sorted(answers) == ["first", "second"]
    assert sorted((call["plugin_id"], call["entry_id"]) for call in server.calls) == [("a", "b__c"), ("a__b", "c")]


def test_tool_names_keep_the_parts_apart():
    from nekoclaw.host.capabilities import gateway_tool_name

    assert gateway_tool_name("plugin", "a__b", "c") != gateway_tool_name("plugin", "a", "b__c")
    assert gateway_tool_name("plugin", "a_", "b") != gateway_tool_name("plugin", "a", "_b")
    assert gateway_tool_name("plugin", "weather", "today") == "plugin__weather__today"


async def test_a_name_two_capabilities_share_is_served_for_neither(host, server):
    server.add_plugin(
        plugin(
            "mcp_adapter",
            "MCP Adapter",
            [
                entry("mcp_notes_search", "Search the notes", name="[notes] search", llm_result_fields=["summary"]),
                entry("mcp_notes_search_2", "Search the notes again", name="[notes] search", llm_result_fields=["summary"]),
            ],
        ),
        mcp_notes_search=_returns(finish({"summary": "first"})),
        mcp_notes_search_2=_returns(finish({"summary": "second"})),
    )
    host.permissions.set_asker(Asker("once"))
    grant = _grant(host, await _commission(host), "mcp_adapter")

    result = await _call(host, grant, "mcp__notes__search")

    assert result["isError"] and "no capability named" in _text(result)
    assert server.calls == []


# ---------------------------------------------------------------------------
# The listener's edges, without a socket
# ---------------------------------------------------------------------------


async def test_a_token_replaced_while_the_body_arrives_is_refused(host, server):
    commission = await _commission(host)
    token = host.gateway.issue(commission.id)
    body_may_come = asyncio.Event()
    received: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        received.append(1)
        if len(received) > 1:
            await asyncio.Event().wait()
        await body_may_come.wait()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"authorization", f"Bearer {token}".encode())]}
    serving = asyncio.ensure_future(host.gateway._asgi(scope, receive, send))
    await wait_until(lambda: received, what="the body being awaited")
    host.gateway.issue(commission.id)
    body_may_come.set()
    await asyncio.wait_for(serving, 5)

    assert sent[0]["status"] == 401


async def test_a_start_given_up_on_closes_its_listener(host, monkeypatch):
    import uvicorn

    serving: list[Any] = []
    ended: list[bool] = []

    async def serve(self: Any, sockets: Any = None) -> None:
        serving.append(self)
        try:
            while not self.should_exit:
                await asyncio.sleep(0.01)
        finally:
            ended.append(True)

    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    starting = asyncio.ensure_future(host.gateway.start())
    try:
        await wait_until(lambda: serving, what="the listener being started")
        starting.cancel()
        await asyncio.gather(starting, return_exceptions=True)

        assert starting.cancelled()
        assert ended == [True]
        assert not host.gateway.started
    finally:
        for listener in serving:
            listener.should_exit = True


class _Listener:
    should_exit = False


async def test_stop_cancelled_while_waiting_for_the_listener_is_cancelled(host):
    listener_may_end = asyncio.Event()
    task = asyncio.ensure_future(listener_may_end.wait())
    host.gateway._server, host.gateway._serve_task, host.gateway._port = _Listener(), task, 1
    stopping = asyncio.ensure_future(host.gateway.stop())
    await asyncio.sleep(0.05)
    stopping.cancel()
    await asyncio.gather(stopping, return_exceptions=True)

    # The cancel reaches whoever called stop, and the listener is not left going.
    assert stopping.cancelled()
    await wait_until(task.done, what="the listener being closed")
    assert not host.gateway.started


async def test_a_listener_that_ends_cancelled_still_lets_stop_return(host):
    async def listener() -> None:
        await asyncio.sleep(0.05)
        raise asyncio.CancelledError

    task = asyncio.ensure_future(listener())
    host.gateway._server, host.gateway._serve_task, host.gateway._port = _Listener(), task, 1

    await asyncio.wait_for(host.gateway.stop(), 5)

    assert task.cancelled()
    assert not host.gateway.started
