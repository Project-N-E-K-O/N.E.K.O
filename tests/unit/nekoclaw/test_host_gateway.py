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
and a task agent using it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    BuiltinImage,
    BuiltinOutcome,
    builtin_tool,
)
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
        "route": "task_agent",
        "conversation_id": "conv-3",
        "lang": "en",
    }
    fields.update(values)
    commission, _created = await host.ledger.create(**fields)
    await host.ledger.transition(commission.id, "working")
    return commission


def _grant(host: Any, commission: Any, *candidates: str, images: bool = False) -> Any:
    """Issue a token for a commission; ``images`` says whether its model sees pictures."""
    token = host.gateway.issue(commission.id, candidate_plugin_ids=candidates, supports_images=images)
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


async def test_result_fields_the_entry_declares_are_all_a_task_agent_gets(host, server):
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


async def test_confirmation_request_is_passed_to_the_task_agent_as_such(host, server):
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


# ---------------------------------------------------------------------------
# A plugin that wants the user's confirmation
# ---------------------------------------------------------------------------


class UserAnswers:
    """Stands in for the waiting a task agent's own questions go through."""

    def __init__(self, text: str = "yes, delete them") -> None:
        self.text = text
        self.asked: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        self.asked.append((commission_id, question))
        return {"text": self.text, "choice": None}


def _memo(server: FakePluginServer, data: dict[str, Any]) -> None:
    server.add_plugin(
        plugin("memo", "Memos", [entry("purge", "Delete all memos", llm_result_fields=["reply"])]),
        purge=_returns(finish(data)),
    )


async def test_a_plugins_request_for_confirmation_is_put_to_the_user_not_to_the_task_agent(host, server):
    _memo(server, {"needs_confirmation": True, "reply": "really delete 3 memos?"})
    user = UserAnswers("yes, delete them")
    host.gateway.set_ask_user(user)
    commission = await _commission(host)
    grant = _grant(host, commission)

    result = await _call(host, grant, "plugin__memo__purge")

    assert user.asked == [
        (
            commission.id,
            {
                "kind": "confirmation",
                "question": "really delete 3 memos?",
                "capability": "plugin:memo:purge",
                "title": "memo: purge",
            },
        )
    ]
    assert result["isError"] is False
    assert _text(result) == (
        "[The plugin did not carry this out: it needs the user's confirmation first. The user was asked and "
        'answered: "yes, delete them". Only if that is a go-ahead, call it again the way the plugin asks; '
        "otherwise leave it.]\nreally delete 3 memos?"
    )
    # The plugin was called once; calling it again is the task agent's next step.
    assert len(server.calls) == 1


async def test_confirm_required_status_is_a_request_for_confirmation_too(host, server):
    _memo(server, {"status": "confirm_required", "reply": "send the mail?"})
    user = UserAnswers("no")
    host.gateway.set_ask_user(user)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__purge")

    assert [question["question"] for _cid, question in user.asked] == ["send the mail?"]
    assert 'answered: "no"' in _text(result)


@pytest.mark.parametrize(
    "data",
    [
        {"action": "clarify", "reply": "which city?"},
        {"status": "clarify", "reply": "which city?"},
        {"status": "blocked", "reply": "not signed in"},
        # Marked as an observation only: nothing in it asks for anything.
        {"needs_confirmation": True, "observation_only": True, "reply": "noted"},
    ],
)
async def test_a_clarification_goes_back_to_the_task_agent_and_the_user_is_not_asked(host, server, data):
    _memo(server, data)
    user = UserAnswers()
    host.gateway.set_ask_user(user)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__purge")

    assert user.asked == []
    assert "The user was asked" not in _text(result)


async def test_deferred_result_is_not_put_to_the_user(host, server):
    _memo(server, {"deferred": True, "reply": "will remind at five"})
    user = UserAnswers()
    host.gateway.set_ask_user(user)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__purge")

    assert user.asked == []
    assert _text(result) == "[Accepted; the plugin completes this later on its own.]\nwill remind at five"


async def test_a_confirmation_that_cannot_be_asked_goes_back_to_the_task_agent_as_before(host, server):
    _memo(server, {"needs_confirmation": True, "reply": "really delete 3 memos?"})

    async def nobody(commission_id: str, question: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("the commission has no session to ask for")

    host.gateway.set_ask_user(nobody)
    grant = _grant(host, await _commission(host))

    result = await _call(host, grant, "plugin__memo__purge")

    assert _text(result) == (
        "[The plugin did not carry this out: it needs the user's confirmation or a clarification first.]\n"
        "really delete 3 memos?"
    )


async def test_the_question_put_to_the_user_is_capped_in_tokens(host, server):
    from config.nekoclaw_settings import GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS
    from utils.tokenize import count_tokens

    _memo(server, {"needs_confirmation": True, "reply": "confirm " + " ".join(f"word{n}" for n in range(2000))})
    user = UserAnswers()
    host.gateway.set_ask_user(user)
    grant = _grant(host, await _commission(host))

    await _call(host, grant, "plugin__memo__purge")

    assert count_tokens(user.asked[0][1]["question"]) <= GATEWAY_CONFIRMATION_QUESTION_MAX_TOKENS


# ---------------------------------------------------------------------------
# Built-in tools: keyboard and mouse, browser
# ---------------------------------------------------------------------------

COMPUTER = {
    "name": "computer",
    "description": "Operate the desktop with the mouse and the keyboard, one action per call.",
    "parameters": {
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["screenshot", "click"]}},
        "required": ["action"],
    },
}
NAVIGATE = {
    "name": "browser_navigate_page",
    "description": "Go to a URL. Returns a snapshot of the loaded page.",
    "parameters": {"type": "object", "properties": {"url": {"type": "string"}}},
}
SCREENSHOT = {
    "name": "browser_take_screenshot",
    "description": "Take a screenshot of the page.",
    "parameters": {"type": "object", "properties": {}},
}
BUILTIN_NAMES = ["computer", "browser_navigate_page", "browser_take_screenshot"]


class Builtins:
    """A provider of three built-in tools whose calls are recorded.

    ``computer`` and ``browser_take_screenshot`` hand pictures back, so they
    need a model that sees them; ``browser_navigate_page`` does not.
    """

    def __init__(self) -> None:
        # name -> (group, definition, needs a model that sees pictures)
        self.definitions: dict[str, tuple[str, dict[str, Any], bool]] = {
            "computer": (BUILTIN_GROUP_KEYBOARD_MOUSE, COMPUTER, True),
            "browser_navigate_page": (BUILTIN_GROUP_BROWSER, NAVIGATE, False),
            "browser_take_screenshot": (BUILTIN_GROUP_BROWSER, SCREENSHOT, True),
        }
        self.switched_off: set[str] = set()
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.started = asyncio.Event()
        # Set to an event to keep a call going until the event is set.
        self.hold: asyncio.Event | None = None
        self.cancelled: list[str] = []
        # A ``BuiltinOutcome`` to return, or an exception to raise.
        self.outcome: Any = None
        # Called at the start of every call; what it returns is kept in ``observed``.
        self.observe: Any = None
        self.observed: list[Any] = []

    def available(self) -> list[Any]:
        return [
            builtin_tool(
                group,
                definition,
                available=name not in self.switched_off,
                unavailable_reason="switched off" if name in self.switched_off else "",
                needs_images=needs_images,
            )
            for name, (group, definition, needs_images) in self.definitions.items()
        ]

    async def run(self, capability: Any, *, commission_id: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((capability.tool_name, commission_id, dict(arguments)))
        if self.observe is not None:
            self.observed.append(self.observe())
        self.started.set()
        if self.hold is not None:
            try:
                await self.hold.wait()
            except asyncio.CancelledError:
                self.cancelled.append(capability.tool_name)
                raise
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome or BuiltinOutcome(True, f"{capability.tool_name} done")

    async def released(self, group: str, commission_id: str) -> None:
        return None


@pytest.fixture
async def builtin_host(server: FakePluginServer, tmp_path: Any) -> Any:
    provider = Builtins()
    built = make_host(tmp_path, server, decision_llm=FakeDecisionLLM(), builtins=provider)
    built.provider = provider
    yield built
    await built.shutdown()


@pytest.fixture
def short_wait(monkeypatch: Any) -> None:
    """A commission waits only a moment for a group another one occupies."""
    import config.nekoclaw_settings as settings

    monkeypatch.setattr(settings, "BUILTIN_OCCUPIED_WAIT_SECONDS", 0.05)


async def _tool_names(host: Any, grant: Any) -> list[str]:
    answer = await _rpc(host, grant, "tools/list")
    return [tool["name"] for tool in answer["result"]["tools"]]


def _cost(definition: dict[str, Any]) -> int:
    """The tokens one listed definition is counted as."""
    from utils.tokenize import count_tokens

    return count_tokens(json.dumps(definition, ensure_ascii=False))


def _found(text: str) -> list[str]:
    """The names of the definitions a search returned."""
    return [json.loads(line)["name"] for line in text.splitlines() if line.startswith("{")]


# -- what is listed ----------------------------------------------------------


async def test_available_builtins_are_listed_up_front_without_being_candidates(builtin_host, server):
    _catalog(server)
    grant = _grant(builtin_host, await _commission(builtin_host), "weather", images=True)

    names = await _tool_names(builtin_host, grant)

    assert names == [*BUILTIN_NAMES, "plugin__weather__today", SEARCH_TOOL, CALL_TOOL]


async def test_builtin_definitions_are_served_as_written(builtin_host):
    from config.nekoclaw_settings import GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    from utils.tokenize import count_tokens

    # Longer than the description of a plugin entry may be.
    long_description = "Operate the desktop. " + " ".join(f"detail{n}" for n in range(150))
    assert count_tokens(long_description) > GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    builtin_host.provider.definitions["computer"] = (
        BUILTIN_GROUP_KEYBOARD_MOUSE,
        {**COMPUTER, "description": long_description},
        True,
    )
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    tools = (await _rpc(builtin_host, grant, "tools/list"))["result"]["tools"]

    # Name, description and schema exactly as defined: nothing put in front, nothing cut.
    assert tools == [
        {"name": "computer", "description": long_description, "inputSchema": COMPUTER["parameters"]},
        {
            "name": "browser_navigate_page",
            "description": NAVIGATE["description"],
            "inputSchema": NAVIGATE["parameters"],
        },
        {
            "name": "browser_take_screenshot",
            "description": SCREENSHOT["description"],
            "inputSchema": SCREENSHOT["parameters"],
        },
    ]


async def test_a_long_list_of_plugin_entries_does_not_push_the_builtins_off_the_list(
    builtin_host, server, monkeypatch
):
    import config.nekoclaw_settings as settings

    entries = [entry(f"entry{n}", "does something " + "very specific " * 400) for n in range(60)]
    server.add_plugin(plugin("big", "A plugin with many entries", entries))
    grant = _grant(builtin_host, await _commission(builtin_host), "big", images=True)

    names = await _tool_names(builtin_host, grant)

    # More entries than their budget holds; the built-in tools are all there, in front.
    listed_entries = [name for name in names if name.startswith("plugin__")]
    assert 0 < len(listed_entries) < 60
    assert names[: len(BUILTIN_NAMES)] == BUILTIN_NAMES
    assert names[-2:] == [SEARCH_TOOL, CALL_TOOL]

    # Even with no room at all for plugin entries.
    monkeypatch.setattr(settings, "GATEWAY_TOOL_LIST_MAX_TOKENS", 1)
    assert await _tool_names(builtin_host, grant) == [*BUILTIN_NAMES, SEARCH_TOOL, CALL_TOOL]


async def test_builtins_take_nothing_from_the_budget_of_the_plugin_entries(builtin_host, server, monkeypatch):
    import config.nekoclaw_settings as settings

    _catalog(server)
    grant = _grant(builtin_host, await _commission(builtin_host), "weather", "lamp", images=True)
    definitions = {item["name"]: item for item in await builtin_host.gateway.tool_definitions(grant)}
    # Exactly the room the two plugin entries need.
    monkeypatch.setattr(
        settings,
        "GATEWAY_TOOL_LIST_MAX_TOKENS",
        _cost(definitions["plugin__weather__today"]) + _cost(definitions["plugin__lamp__switch"]),
    )

    names = await _tool_names(builtin_host, grant)

    assert names == [*BUILTIN_NAMES, "plugin__weather__today", "plugin__lamp__switch"]


async def test_a_builtin_over_the_budget_of_the_builtins_is_left_out_and_no_plugin_entry_with_it(
    builtin_host, server, monkeypatch
):
    import config.nekoclaw_settings as settings

    _catalog(server)
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), "weather", "lamp", images=True)
    definitions = {item["name"]: item for item in await builtin_host.gateway.tool_definitions(grant)}
    # Room for the first two built-in tools only.
    monkeypatch.setattr(
        settings,
        "GATEWAY_BUILTIN_TOOL_LIST_MAX_TOKENS",
        _cost(definitions["computer"]) + _cost(definitions["browser_navigate_page"]),
    )

    names = await _tool_names(builtin_host, grant)
    found = _found(_text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "screenshot of the page"})))
    reached = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})

    assert names == [
        "computer",
        "browser_navigate_page",
        "plugin__weather__today",
        "plugin__lamp__switch",
        SEARCH_TOOL,
        CALL_TOOL,
    ]
    # What did not fit is found and called like any capability that is not listed.
    assert "browser_take_screenshot" in found
    assert reached["isError"] is False
    assert [name for name, _cid, _args in builtin_host.provider.calls] == ["browser_take_screenshot"]


async def test_builtins_alone_are_enough_for_a_gateway(builtin_host):
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    assert await _tool_names(builtin_host, grant) == BUILTIN_NAMES


async def test_a_builtin_that_is_switched_off_is_neither_listed_nor_callable(builtin_host):
    builtin_host.provider.switched_off.add("computer")
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    names = await _tool_names(builtin_host, grant)
    direct = await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    through = await _call(builtin_host, grant, CALL_TOOL, {"name": "computer", "arguments": {"action": "screenshot"}})
    found = _found(_text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "operate the desktop with the mouse"})))

    assert names == ["browser_navigate_page", "browser_take_screenshot"]
    assert direct["isError"] is True and "no capability named computer" in _text(direct)
    assert through["isError"] is True
    assert "computer" not in found
    assert builtin_host.provider.calls == [] and asker.asked == []


async def test_without_a_provider_there_are_no_builtin_tools(host):
    grant = _grant(host, await _commission(host), images=True)

    assert await _tool_names(host, grant) == []
    assert host.registry.builtins() == []


async def test_two_builtin_tools_of_one_name_are_served_for_neither(builtin_host):
    builtin_host.provider.definitions["again"] = (BUILTIN_GROUP_BROWSER, dict(COMPUTER), False)
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    names = await _tool_names(builtin_host, grant)
    result = await _call(builtin_host, grant, "computer", {"action": "screenshot"})

    # A call by that name could reach the desktop or the browser.
    assert names == ["browser_navigate_page", "browser_take_screenshot"]
    assert result["isError"] is True
    assert builtin_host.provider.calls == []


async def test_the_task_brain_never_routes_to_a_builtin_directly(builtin_host, server):
    # Only task agents reach the built-in tools; the decision model is shown
    # plugins and nothing else.
    _catalog(server)

    shown = await builtin_host.registry.plugins()

    assert sorted(item["id"] for item in shown) == ["lamp", "weather"]
    assert [item.tool_name for item in builtin_host.registry.builtins()] == BUILTIN_NAMES
    assert all(not item.one_step for item in builtin_host.registry.builtins())


# -- tools that need a model that sees pictures -------------------------------


async def test_a_tool_that_needs_pictures_is_kept_from_a_model_that_cannot_see_them(builtin_host):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=False)

    names = await _tool_names(builtin_host, grant)
    direct = await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    through = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})
    found = _found(
        _text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "screenshot of the page, operate the desktop"}))
    )

    # All it can use is listed, so it is not given the search tools either.
    assert names == ["browser_navigate_page"]
    assert direct["isError"] is True and "no capability named computer" in _text(direct)
    assert through["isError"] is True and "no capability named browser_take_screenshot" in _text(through)
    assert "computer" not in found and "browser_take_screenshot" not in found
    assert builtin_host.provider.calls == [] and asker.asked == []


async def test_a_tool_that_needs_pictures_is_offered_to_a_model_that_sees_them(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    names = await _tool_names(builtin_host, grant)
    direct = await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    through = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})
    found = _found(_text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "screenshot of the page"})))

    assert names == BUILTIN_NAMES
    assert direct["isError"] is False and through["isError"] is False
    assert "browser_take_screenshot" in found
    assert [name for name, _cid, _args in builtin_host.provider.calls] == ["computer", "browser_take_screenshot"]


async def test_a_new_token_for_the_commission_decides_anew_whether_its_model_sees_pictures(builtin_host):
    commission = await _commission(builtin_host)

    seeing = _grant(builtin_host, commission, images=True)
    assert await _tool_names(builtin_host, seeing) == BUILTIN_NAMES
    blind = _grant(builtin_host, commission, images=False)
    assert await _tool_names(builtin_host, blind) == ["browser_navigate_page"]
    # Without a word about it, a model is not taken to see pictures.
    unsaid = builtin_host.gateway._grant(builtin_host.gateway.issue(commission.id))
    assert await _tool_names(builtin_host, unsaid) == ["browser_navigate_page"]


# -- calls ---------------------------------------------------------------------


async def test_a_builtin_call_asks_first_and_then_runs_for_the_commission(builtin_host):
    asked: list[Any] = []

    async def asker(commission_id: str, ask: Any) -> str:
        # What had run and what was occupied when the user was asked.
        asked.append((commission_id, ask, list(builtin_host.provider.calls), builtin_host.leases.occupied()))
        return "once"

    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission)
    before = [event.type for event in await builtin_host.ledger.events(commission.id)]

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    ((asked_for, ask, ran_before, occupied_before),) = asked
    assert (asked_for, ask.category, ask.key) == (commission.id, "browser", None)
    assert ask.detail == {
        "tool": "browser_navigate_page",
        "capability": "builtin:browser:browser_navigate_page",
        "title": "browser_navigate_page",
    }
    assert ran_before == [] and occupied_before == {}
    assert builtin_host.provider.calls == [
        ("browser_navigate_page", commission.id, {"url": "https://example.com"})
    ]
    assert result == {"content": [{"type": "text", "text": "browser_navigate_page done"}], "isError": False}
    # A step that went well adds no row to the ledger: the task agent's own
    # tool events show it already, and a task is many such steps.
    assert [event.type for event in await builtin_host.ledger.events(commission.id)] == before


async def test_a_declined_builtin_is_not_run(builtin_host):
    builtin_host.permissions.set_asker(Asker("deny"))
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)

    result = await _call(builtin_host, grant, "computer", {"action": "screenshot"})

    assert result["isError"] is True and "the user declined" in _text(result)
    assert builtin_host.provider.calls == []
    # Declined before the desktop was occupied.
    assert builtin_host.leases.occupied() == {}
    denied = [
        event.payload
        for event in await builtin_host.ledger.events(commission.id)
        if event.type == "capability_denied"
    ]
    assert denied == [
        {"capability": "builtin:keyboard_mouse:computer", "reason": "permission denied: the user declined"}
    ]


async def test_the_desktop_and_the_browser_are_asked_about_separately(builtin_host):
    asker = Asker("session")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})
    await _call(builtin_host, grant, "browser_take_screenshot")
    await _call(builtin_host, grant, "computer", {"action": "screenshot"})

    # Once for each group, not once for each tool.
    assert [ask.category for _cid, ask in asker.asked] == ["browser", "keyboard_mouse"]
    assert len(builtin_host.provider.calls) == 3


async def test_a_group_allowed_once_is_not_asked_about_again_until_the_run_is_over(builtin_host):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)

    for _step in range(3):
        await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})
    assert [ask.category for _cid, ask in asker.asked] == ["browser"]

    builtin_host.permissions.end_run(commission.id)
    await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert [ask.category for _cid, ask in asker.asked] == ["browser", "browser"]
    assert len(builtin_host.provider.calls) == 4


async def test_a_builtin_that_fails_is_an_error_result(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = BuiltinOutcome(False, "the page never loaded")
    grant = _grant(builtin_host, await _commission(builtin_host))

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result == {"content": [{"type": "text", "text": "the page never loaded"}], "isError": True}


@pytest.mark.parametrize(("success", "text"), [(True, "done"), (False, "failed")])
async def test_an_outcome_without_text_still_says_how_it_ended(builtin_host, success, text):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = BuiltinOutcome(success, "")
    grant = _grant(builtin_host, await _commission(builtin_host))

    result = await _call(builtin_host, grant, "browser_navigate_page")

    assert result == {"content": [{"type": "text", "text": text}], "isError": not success}


async def test_a_builtin_that_raises_is_an_error_result_without_its_text(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = RuntimeError("secret screen contents")
    grant = _grant(builtin_host, await _commission(builtin_host))

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result["isError"] is True
    assert "secret screen contents" not in _text(result)
    assert _text(result) == "browser_navigate_page failed: RuntimeError"


async def test_stopping_the_commission_stops_a_builtin_call_under_way(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.hold = asyncio.Event()
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)

    call = asyncio.ensure_future(_call(builtin_host, grant, "computer", {"action": "click"}))
    grant.in_flight.add(call)
    await asyncio.wait_for(builtin_host.provider.started.wait(), 5)
    builtin_host.gateway.revoke(commission.id)
    with pytest.raises(asyncio.CancelledError):
        await call

    assert builtin_host.provider.cancelled == ["computer"]


async def test_builtin_result_text_is_capped_in_tokens(builtin_host):
    from config.nekoclaw_settings import GATEWAY_RESULT_MAX_TOKENS
    from utils.tokenize import count_tokens

    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = BuiltinOutcome(True, " ".join(f"line{n}" for n in range(20000)))
    grant = _grant(builtin_host, await _commission(builtin_host))

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert GATEWAY_RESULT_MAX_TOKENS - 50 <= count_tokens(_text(result)) <= GATEWAY_RESULT_MAX_TOKENS


# -- pictures in a result --------------------------------------------------------


async def test_a_result_with_pictures_is_one_text_block_followed_by_image_blocks(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = BuiltinOutcome(
        True, "clicked", images=(BuiltinImage("QUJD", "image/jpeg"), BuiltinImage("REVG", "image/png"))
    )
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    result = await _call(builtin_host, grant, "computer", {"action": "click"})

    assert result == {
        "content": [
            {"type": "text", "text": "clicked"},
            {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"},
            {"type": "image", "data": "REVG", "mimeType": "image/png"},
        ],
        "isError": False,
    }


async def test_a_step_that_failed_still_hands_its_picture_back(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = BuiltinOutcome(
        False, "nothing to click there", images=(BuiltinImage("QUJD", "image/jpeg"),)
    )
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    result = await _call(builtin_host, grant, "computer", {"action": "click"})

    assert result == {
        "content": [
            {"type": "text", "text": "nothing to click there"},
            {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"},
        ],
        "isError": True,
    }


async def test_pictures_are_left_out_for_a_model_that_cannot_see_them_and_the_text_says_so(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    # A tool such a model is offered hands a picture back all the same.
    builtin_host.provider.outcome = BuiltinOutcome(True, "loaded", images=(BuiltinImage("QUJD", "image/jpeg"),))
    grant = _grant(builtin_host, await _commission(builtin_host), images=False)

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result == {
        "content": [
            {"type": "text", "text": "loaded\n[A picture was returned, but this model cannot see pictures.]"}
        ],
        "isError": False,
    }


# -- one commission at a time on the desktop and in the browser -------------------


async def test_a_builtin_call_occupies_its_group_before_it_runs_and_keeps_it(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.observe = builtin_host.leases.occupied
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)

    await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    # Occupied by the time the tool ran, and still: the next step finds the
    # page as this one left it.
    assert builtin_host.provider.observed == [{"browser": commission.id}]
    held = builtin_host.leases.holder("browser")
    assert (held.team, held.title, held.members) == (commission.id, "plan the trip", {commission.id})
    assert builtin_host.leases.holder("keyboard_mouse") is None

    await _call(builtin_host, grant, "computer", {"action": "screenshot"})

    assert builtin_host.leases.occupied() == {"browser": commission.id, "keyboard_mouse": commission.id}


@pytest.mark.parametrize(
    ("tool", "group", "what"),
    [("browser_navigate_page", "browser", "browser"), ("computer", "keyboard_mouse", "desktop")],
)
async def test_a_group_another_task_occupies_is_left_alone_and_the_task_agent_is_told_who_has_it(
    builtin_host, short_wait, tool, group, what
):
    builtin_host.permissions.set_asker(Asker("once"))
    first = await _commission(builtin_host)
    second = await _commission(builtin_host, title="book the hotel")
    first_grant = _grant(builtin_host, first, images=True)
    second_grant = _grant(builtin_host, second, images=True)
    await _call(builtin_host, first_grant, tool)

    result = await asyncio.wait_for(_call(builtin_host, second_grant, tool), 10)

    assert result["isError"] is True
    text = _text(result)
    assert text.startswith(f"The {what} is in use by another task (plan the trip) and did not come free in time.")
    assert "Nothing was done." in text
    # Only the first commission's step ran, and it still has the group.
    assert [(name, cid) for name, cid, _args in builtin_host.provider.calls] == [(tool, first.id)]
    assert builtin_host.leases.occupied() == {group: first.id}
    occupied = [
        event.payload for event in await builtin_host.ledger.events(second.id) if event.type == "builtin_occupied"
    ]
    assert occupied == [{"capability": f"builtin:{group}:{tool}", "by": first.id}]


async def test_the_browser_being_occupied_does_not_keep_another_task_from_the_desktop(builtin_host, short_wait):
    builtin_host.permissions.set_asker(Asker("once"))
    first = await _commission(builtin_host)
    second = await _commission(builtin_host, title="book the hotel")
    await _call(builtin_host, _grant(builtin_host, first, images=True), "browser_navigate_page")

    result = await asyncio.wait_for(
        _call(builtin_host, _grant(builtin_host, second, images=True), "computer", {"action": "screenshot"}), 10
    )

    assert result["isError"] is False
    assert builtin_host.leases.occupied() == {"browser": first.id, "keyboard_mouse": second.id}


async def test_a_commission_waiting_for_a_group_gets_it_when_the_other_commission_ends(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    first = await _commission(builtin_host)
    second = await _commission(builtin_host, title="book the hotel")
    await _call(builtin_host, _grant(builtin_host, first), "browser_navigate_page")

    waiting = asyncio.ensure_future(_call(builtin_host, _grant(builtin_host, second), "browser_navigate_page"))
    await wait_until(lambda: builtin_host.leases._waiters, what="the second commission waiting for the browser")
    assert len(builtin_host.provider.calls) == 1
    await builtin_host.ledger.transition(first.id, "done", reason="test")

    result = await asyncio.wait_for(waiting, 10)
    assert result["isError"] is False
    assert [cid for _name, cid, _args in builtin_host.provider.calls] == [first.id, second.id]
    assert builtin_host.leases.occupied() == {"browser": second.id}


@pytest.mark.parametrize("status", ["cancelled", "waiting_user"])
async def test_a_commission_that_stopped_working_while_it_waited_for_a_group_does_not_run_the_tool(
    builtin_host, status
):
    builtin_host.permissions.set_asker(Asker("once"))
    first = await _commission(builtin_host)
    second = await _commission(builtin_host, title="book the hotel")
    await _call(builtin_host, _grant(builtin_host, first), "browser_navigate_page")
    waiting = asyncio.ensure_future(_call(builtin_host, _grant(builtin_host, second), "browser_navigate_page"))
    await wait_until(lambda: builtin_host.leases._waiters, what="the second commission waiting for the browser")

    # Stopped, or asking the user something, by the time its turn comes.
    await builtin_host.ledger.transition(second.id, status, reason="test")
    await builtin_host.ledger.transition(first.id, "done", reason="test")
    result = await asyncio.wait_for(waiting, 10)

    assert result["isError"] is True and "no longer under way" in _text(result)
    assert [cid for _name, cid, _args in builtin_host.provider.calls] == [first.id]
    # The browser it was handed is given straight back.
    assert builtin_host.leases.occupied() == {}


async def test_stopping_a_commission_stops_its_call_that_waits_for_a_group(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    first = await _commission(builtin_host)
    second = await _commission(builtin_host, title="book the hotel")
    await _call(builtin_host, _grant(builtin_host, first), "browser_navigate_page")
    second_grant = _grant(builtin_host, second)
    waiting = asyncio.ensure_future(_call(builtin_host, second_grant, "browser_navigate_page"))
    second_grant.in_flight.add(waiting)
    await wait_until(lambda: builtin_host.leases._waiters, what="the second commission waiting for the browser")

    builtin_host.gateway.revoke(second.id)
    with pytest.raises(asyncio.CancelledError):
        await waiting

    assert builtin_host.leases._waiters == set()
    assert builtin_host.leases.occupied() == {"browser": first.id}
    assert len(builtin_host.provider.calls) == 1


async def test_a_task_agent_uses_the_group_the_commission_that_started_it_occupies(builtin_host, short_wait):
    builtin_host.permissions.set_asker(Asker("once"))
    lead = await _commission(builtin_host)
    child = await _commission(
        builtin_host, title="look up trains", parent_id=lead.id, name="agent-1", source="task_agent"
    )
    await _call(builtin_host, _grant(builtin_host, lead), "browser_navigate_page")

    result = await asyncio.wait_for(
        _call(builtin_host, _grant(builtin_host, child), "browser_navigate_page"), 10
    )

    assert result["isError"] is False
    held = builtin_host.leases.holder("browser")
    assert (held.team, held.members) == (lead.id, {lead.id, child.id})
    assert [cid for _name, cid, _args in builtin_host.provider.calls] == [lead.id, child.id]
