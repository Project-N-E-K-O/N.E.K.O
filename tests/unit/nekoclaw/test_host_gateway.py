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

from config.prompts.prompts_nekoclaw_builtin import (
    BUILTIN_GROUP_GUIDANCE,
    BUILTIN_GROUP_SUMMARIES,
    TOOL_GROUPS_INTRO,
)
from nekoclaw.host.capabilities import (
    BUILTIN_GROUP_BROWSER,
    BUILTIN_GROUP_KEYBOARD_MOUSE,
    BUILTIN_GROUPS,
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


def _grant(
    host: Any, commission: Any, *candidates: str, images: bool = False, loaded: tuple[str, ...] = BUILTIN_GROUPS
) -> Any:
    """Issue a token for a commission; ``images`` says whether its model sees pictures.

    ``loaded`` names the groups of built-in tools the task agent is taken to
    have loaded already, both unless said otherwise: most tests here are
    about what a call does once it is carried out. A token as it is issued
    has loaded none (``loaded=()``).
    """
    token = host.gateway.issue(commission.id, candidate_plugin_ids=candidates, supports_images=images)
    grant = host.gateway._grant(token)
    grant.loaded_groups.update(loaded)
    return grant


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
        # Why a tool that is switched off is, as the provider words it.
        self.reason = "switched off"
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
                unavailable_reason=self.reason if name in self.switched_off else "",
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

    # Whether the user closed the browser and has not agreed to another, as the real provider tells it.
    closed_by_user = False
    agreed = 0

    def browser_closed_by_user(self) -> bool:
        return self.closed_by_user

    def browser_reopen_agreed(self) -> None:
        self.agreed += 1
        self.closed_by_user = False

    # What had been carried out each time a call of a browser tool was said to be over.
    over: list[int] = []

    def browser_reopen_agreement_over(self) -> None:
        self.over = [*self.over, len(self.calls)]


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


# The search tool as it is offered when there is no group to load: what it
# was before the built-in tools came in groups, word for word.
PLAIN_SEARCH_TOOL = {
    "name": "capability_search",
    "description": (
        "Find further capabilities of the host (plugin entries and tools of connected services) "
        "by what they do. Returns their names, descriptions and argument schemas. Call one with "
        "capability_call."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "What the capability should do."}},
        "required": ["query"],
    },
}
GROUPS_INTRO = TOOL_GROUPS_INTRO.format(call_tool="capability_call")
# The line about each group, ending in the call the group is started with.
GROUP_LINES = {
    "browser": (
        f"- browser: {BUILTIN_GROUP_SUMMARIES['browser']} "
        'First call: browser_navigate_page, arguments {"url": "..."}.'
    ),
    "desktop": (
        f"- desktop: {BUILTIN_GROUP_SUMMARIES['keyboard_mouse']} "
        'First call: computer, arguments {"action": "screenshot"}.'
    ),
}
LOADED_HEADER = (
    "The {name} tools follow, one definition per line. They are not tools of your own: call each "
    'through capability_call as {{"name": "<tool name>", "arguments": {{...}}}}.'
)


def _as_served(definition: dict[str, Any]) -> dict[str, Any]:
    """A built-in tool's definition the way the gateway hands it over."""
    return {
        "name": definition["name"],
        "description": definition["description"],
        "inputSchema": definition["parameters"],
    }


async def _search_tool(host: Any, grant: Any) -> dict[str, Any]:
    """The search tool as this commission is offered it."""
    tools = (await _rpc(host, grant, "tools/list"))["result"]["tools"]
    return next(tool for tool in tools if tool["name"] == SEARCH_TOOL)


async def _groups_on_offer(host: Any, grant: Any) -> list[str]:
    """The groups the search tool tells this commission of, as it names them."""
    search = await _search_tool(host, grant)
    return list(search["inputSchema"]["properties"].get("group", {}).get("enum", []))


async def _load(host: Any, grant: Any, group: str) -> dict[str, Any]:
    return await _call(host, grant, SEARCH_TOOL, {"group": group})


def _served(text: str) -> list[dict[str, Any]]:
    """The definitions in what loading a group, or a search, handed over."""
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


# -- what every request carries: one line about each group ---------------------


async def test_builtin_tools_are_not_listed_and_the_search_tool_tells_of_their_groups(builtin_host, server):
    _catalog(server)
    grant = _grant(builtin_host, await _commission(builtin_host), "weather", images=True, loaded=())

    tools = (await _rpc(builtin_host, grant, "tools/list"))["result"]["tools"]

    assert [tool["name"] for tool in tools] == ["plugin__weather__today", SEARCH_TOOL, CALL_TOOL]
    assert tools[1] == {
        "name": SEARCH_TOOL,
        "description": "\n".join(
            [PLAIN_SEARCH_TOOL["description"], GROUPS_INTRO, GROUP_LINES["browser"], GROUP_LINES["desktop"]]
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What the capability should do."},
                "group": {
                    "type": "string",
                    "enum": ["browser", "desktop"],
                    "description": "Instead of query: a tool group to get the tools of, without making its first call.",
                },
            },
        },
    }
    # Nothing of a built-in tool itself is in what every request carries:
    # not its definition, not its description, not how its group is used.
    # Of the browser's tools only the one a group is started with is named.
    sent = json.dumps(tools)
    for name, (_group, definition, _needs_images) in builtin_host.provider.definitions.items():
        assert f'"name": "{name}"' not in sent and definition["description"] not in sent
    assert sent.count("browser_") == 1 and "browser_take_screenshot" not in sent
    for guidance in BUILTIN_GROUP_GUIDANCE.values():
        assert guidance[:60] not in sent


async def test_without_a_group_to_load_the_search_tool_is_what_it_always_was(host, server):
    _catalog(server)
    grant = _grant(host, await _commission(host), "weather")

    tools = (await _rpc(host, grant, "tools/list"))["result"]["tools"]

    assert [tool["name"] for tool in tools] == ["plugin__weather__today", SEARCH_TOOL, CALL_TOOL]
    assert tools[1] == PLAIN_SEARCH_TOOL


async def test_the_groups_cost_the_plugin_entries_nothing_of_their_budget(builtin_host, server, monkeypatch):
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

    assert await _tool_names(builtin_host, grant) == [
        "plugin__weather__today",
        "plugin__lamp__switch",
        SEARCH_TOOL,
        CALL_TOOL,
    ]
    # Even with no room at all for plugin entries the groups are told of.
    monkeypatch.setattr(settings, "GATEWAY_TOOL_LIST_MAX_TOKENS", 1)
    assert await _tool_names(builtin_host, grant) == [SEARCH_TOOL, CALL_TOOL]
    assert await _groups_on_offer(builtin_host, grant) == ["browser", "desktop"]


async def test_builtins_alone_are_enough_for_a_gateway(builtin_host):
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    # The two tools a group is loaded and called through, and nothing else.
    assert await _tool_names(builtin_host, grant) == [SEARCH_TOOL, CALL_TOOL]
    assert await _groups_on_offer(builtin_host, grant) == ["browser", "desktop"]


async def test_without_a_provider_there_are_no_builtin_tools(host):
    grant = _grant(host, await _commission(host), images=True)

    assert await _tool_names(host, grant) == []
    assert host.registry.builtins() == []
    asked = await _load(host, grant, "browser")
    assert asked["isError"] is True
    assert _text(asked) == (
        "The browser tools are not available right now: switched off. Go on without them, or report that the "
        "task needs them."
    )


async def test_the_task_brain_never_routes_to_a_builtin_directly(builtin_host, server):
    # Only task agents reach the built-in tools; the decision model is shown
    # plugins and nothing else.
    _catalog(server)

    shown = await builtin_host.registry.plugins()

    assert sorted(item["id"] for item in shown) == ["lamp", "weather"]
    assert [item.tool_name for item in builtin_host.registry.builtins()] == BUILTIN_NAMES
    assert all(not item.one_step for item in builtin_host.registry.builtins())


# -- loading a group --------------------------------------------------------------


async def test_loading_a_group_hands_over_how_it_is_used_and_then_its_tools_as_written(builtin_host):
    from config.nekoclaw_settings import GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    from utils.tokenize import count_tokens

    # Longer than the description of a plugin entry may be.
    long_description = "Go to a URL. " + " ".join(f"detail{n}" for n in range(150))
    assert count_tokens(long_description) > GATEWAY_TOOL_DESCRIPTION_MAX_TOKENS
    navigate = {**NAVIGATE, "description": long_description}
    builtin_host.provider.definitions["browser_navigate_page"] = (BUILTIN_GROUP_BROWSER, navigate, False)
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())
    before = [event.type for event in await builtin_host.ledger.events(commission.id)]

    result = await _load(builtin_host, grant, "browser")

    assert result["isError"] is False and len(result["content"]) == 1
    header, guidance, definitions = _text(result).split("\n\n")
    assert header == LOADED_HEADER.format(name="browser")
    assert guidance == BUILTIN_GROUP_GUIDANCE["browser"]
    # Name, description and schema exactly as defined, one to a line, in the
    # order they are defined: nothing put in front, nothing cut, and none of
    # the other group's.
    assert [json.loads(line) for line in definitions.splitlines()] == [_as_served(navigate), _as_served(SCREENSHOT)]
    assert grant.loaded_groups == {"browser"}
    # Reading is all it was: nobody was asked, nothing ran, nothing is
    # occupied, and the ledger has no row for it.
    assert asker.asked == [] and builtin_host.provider.calls == []
    assert builtin_host.leases.occupied() == {}
    assert [event.type for event in await builtin_host.ledger.events(commission.id)] == before


@pytest.mark.parametrize("asked", ["desktop", "keyboard_mouse", "Desktop", "  desktop "])
async def test_a_group_is_loaded_by_the_name_the_model_was_told_or_by_the_hosts_own(builtin_host, asked):
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    result = await _load(builtin_host, grant, asked)

    header, guidance, definitions = _text(result).split("\n\n")
    assert header == LOADED_HEADER.format(name="desktop")
    assert guidance == BUILTIN_GROUP_GUIDANCE["keyboard_mouse"]
    assert _served(definitions) == [_as_served(COMPUTER)]
    assert grant.loaded_groups == {"keyboard_mouse"}


async def test_group_wins_over_query_when_both_are_given(builtin_host):
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    result = await _call(builtin_host, grant, SEARCH_TOOL, {"query": "operate the desktop", "group": "browser"})

    assert _text(result).startswith(LOADED_HEADER.format(name="browser"))
    assert grant.loaded_groups == {"browser"}


@pytest.mark.parametrize("group", [None, "", "   ", 7, ["browser"]])
async def test_a_group_that_is_no_name_at_all_is_a_plain_search(builtin_host, group):
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    result = await _call(builtin_host, grant, SEARCH_TOOL, {"query": "zzz", "group": group})

    assert _text(result) == "No capability matches 'zzz'."
    assert grant.loaded_groups == set()


async def test_a_group_that_does_not_exist_is_answered_with_the_ones_on_offer(builtin_host):
    seeing = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())
    blind = _grant(builtin_host, await _commission(builtin_host, title="book the hotel"), images=False, loaded=())

    told_seeing = await _load(builtin_host, seeing, "files")
    told_blind = await _load(builtin_host, blind, "files")

    assert told_seeing["isError"] is True
    assert _text(told_seeing) == "no tool group named files; the groups on offer: browser, desktop"
    # Only what this commission's model can make something of.
    assert _text(told_blind) == "no tool group named files; the groups on offer: browser"
    assert seeing.loaded_groups == set() and blind.loaded_groups == set()


async def test_a_group_that_is_switched_off_is_not_told_of_and_cannot_be_loaded_or_called(builtin_host):
    builtin_host.provider.switched_off.add("computer")
    builtin_host.provider.reason = "no input can be sent on this computer"
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    search = await _search_tool(builtin_host, grant)
    loaded = await _load(builtin_host, grant, "desktop")
    direct = await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    through = await _call(builtin_host, grant, CALL_TOOL, {"name": "computer", "arguments": {"action": "screenshot"}})
    found = _text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "operate the desktop with the mouse"}))

    assert search["description"].splitlines()[1:] == [GROUPS_INTRO, GROUP_LINES["browser"]]
    assert search["inputSchema"]["properties"]["group"]["enum"] == ["browser"]
    assert loaded["isError"] is True
    # With the provider's own words for why.
    assert _text(loaded) == (
        "The desktop tools are not available right now: no input can be sent on this computer. Go on without "
        "them, or report that the task needs them."
    )
    assert direct["isError"] is True and "no capability named computer" in _text(direct)
    assert through["isError"] is True and "no capability named computer" in _text(through)
    assert 'Tool group "desktop"' not in found and _served(found) == []
    assert grant.loaded_groups == set()
    assert builtin_host.provider.calls == [] and asker.asked == []


async def test_two_builtin_tools_of_one_name_are_served_for_neither(builtin_host):
    builtin_host.provider.definitions["again"] = (BUILTIN_GROUP_BROWSER, dict(COMPUTER), False)
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    groups = await _groups_on_offer(builtin_host, grant)
    loaded = _served(_text(await _load(builtin_host, grant, "browser")))
    result = await _call(builtin_host, grant, "computer", {"action": "screenshot"})

    # A call by that name could reach the desktop or the browser. The
    # desktop is left without a tool, so it is not a group on offer.
    assert groups == ["browser"]
    assert [item["name"] for item in loaded] == ["browser_navigate_page", "browser_take_screenshot"]
    assert result["isError"] is True and "no capability named computer" in _text(result)
    assert builtin_host.provider.calls == []


async def test_a_group_larger_than_a_loaded_group_may_be_is_cut_and_the_log_says_so(
    builtin_host, monkeypatch, caplog
):
    import logging

    from utils.tokenize import count_tokens

    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())
    whole = _text(await _load(builtin_host, grant, "browser"))
    assert not caplog.records
    monkeypatch.setattr("config.nekoclaw_settings.GATEWAY_TOOL_GROUP_MAX_TOKENS", count_tokens(whole) - 1)

    with caplog.at_level(logging.WARNING, logger="nekoclaw.host.gateway"):
        cut = _text(await _load(builtin_host, grant, "browser"))

    assert len(cut) < len(whole) and count_tokens(cut) < count_tokens(whole)
    assert [record.getMessage() for record in caplog.records] == [
        "the tools of the group browser do not fit what loading a group may hand over; cut"
    ]


# -- the first use of a group -------------------------------------------------------

CLICK = {
    "name": "browser_click",
    "description": "Clicks on the provided element",
    "parameters": {"type": "object", "properties": {"uid": {"type": "string"}}, "required": ["uid"]},
}
PICTURE = {"type": "image", "data": "QUJD", "mimeType": "image/jpeg"}


@pytest.mark.parametrize(
    ("tool", "arguments", "group", "name"),
    [
        ("browser_navigate_page", {"url": "https://example.com"}, "browser", "browser"),
        ("browser_navigate_page", {"type": "url", "url": "https://example.com", "timeout": 5000}, "browser", "browser"),
        ("computer", {"action": "screenshot"}, "keyboard_mouse", "desktop"),
    ],
)
@pytest.mark.parametrize("through_the_call_tool", [True, False])
async def test_the_first_call_of_a_group_is_carried_out_and_answered_with_the_group_in_front(
    builtin_host, tool, arguments, group, name, through_the_call_tool
):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    builtin_host.provider.observe = builtin_host.leases.occupied
    builtin_host.provider.outcome = BuiltinOutcome(True, "looked", images=(BuiltinImage("QUJD", "image/jpeg"),))
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())

    async def call() -> dict[str, Any]:
        if through_the_call_tool:
            return await _call(builtin_host, grant, CALL_TOOL, {"name": tool, "arguments": arguments})
        return await _call(builtin_host, grant, tool, arguments)

    result = await call()

    other = _grant(builtin_host, await _commission(builtin_host, title="book the hotel"), images=True, loaded=())
    whole = _text(await _load(builtin_host, other, name))
    # The group as loading it hands it over, in a block of its own, and then
    # what the call returned, text and picture, as any call returns them.
    assert result == {
        "content": [
            {"type": "text", "text": f"{whole}\n\nThis first call returned:"},
            {"type": "text", "text": "looked"},
            PICTURE,
        ],
        "isError": False,
    }
    # Carried out like any call: the user asked first, the group occupied,
    # the tool run once with what it was given.
    assert [ask.category for _cid, ask in asker.asked] == [group]
    assert builtin_host.provider.calls == [(tool, commission.id, arguments)]
    assert builtin_host.provider.observed == [{group: commission.id}]
    assert grant.loaded_groups == {group}

    # From then on a call comes back as itself.
    again = await call()

    assert again == {"content": [{"type": "text", "text": "looked"}, PICTURE], "isError": False}


@pytest.mark.parametrize("arguments", [{"action": "click"}, {}, {"action": "Screenshot"}, {"action": ["screenshot"]}])
async def test_only_looking_at_the_screen_is_the_first_call_of_the_desktop(builtin_host, arguments):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    refused = await _call(builtin_host, grant, CALL_TOOL, {"name": "computer", "arguments": arguments})

    # The one tool of the desktop does everything; what touches the screen
    # waits until how it is used was handed over.
    assert refused["isError"] is True
    assert _text(refused).startswith(
        "Nothing was done: computer is one of the desktop tools, which were not loaded yet."
    )
    assert builtin_host.provider.calls == [] and asker.asked == []
    assert builtin_host.leases.occupied() == {}


@pytest.mark.parametrize(
    "arguments",
    [
        {"type": "back"},
        {"type": "forward"},
        {"type": "reload"},
        {"type": "reload", "url": "https://example.com"},
        {"type": "URL", "url": "https://example.com"},
        {"type": "url"},
        {},
        {"url": "  "},
        {"url": ["https://example.com"]},
    ],
)
async def test_only_going_to_an_address_is_the_first_call_of_the_browser(builtin_host, arguments):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    refused = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_navigate_page", "arguments": arguments})

    # The tool goes back, forward and reloads too, on whatever page is
    # open; that, and a call without an address to go to, waits until how
    # the browser is used was handed over.
    assert refused["isError"] is True
    assert _text(refused).startswith(
        "Nothing was done: browser_navigate_page is one of the browser tools, which were not loaded yet."
    )
    assert builtin_host.provider.calls == [] and asker.asked == []
    assert builtin_host.leases.occupied() == {}
    # With the group in hand the same call is carried out.
    assert grant.loaded_groups == {"browser"}
    await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_navigate_page", "arguments": arguments})
    assert [(tool, given) for tool, _cid, given in builtin_host.provider.calls] == [
        ("browser_navigate_page", arguments)
    ]


async def test_a_group_handed_over_with_its_first_call_holds_only_what_the_model_can_use(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.definitions["browser_click"] = (BUILTIN_GROUP_BROWSER, CLICK, False)
    grant = _grant(builtin_host, await _commission(builtin_host), images=False, loaded=())

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    # Without the screenshot tool, as when the group is loaded by its name.
    opening, returned = result["content"]
    assert [item["name"] for item in _served(opening["text"])] == ["browser_navigate_page", "browser_click"]
    assert returned == {"type": "text", "text": "browser_navigate_page done"}


@pytest.mark.parametrize("why", ["declined", "failed"])
async def test_a_first_call_that_fails_is_answered_as_any_failed_call_and_hands_nothing_over(builtin_host, why):
    builtin_host.permissions.set_asker(Asker("deny" if why == "declined" else "once"))
    builtin_host.provider.outcome = BuiltinOutcome(
        False, "the page never loaded", images=(BuiltinImage("QUJD", "image/jpeg"),)
    )
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())
    arguments = {"url": "https://example.com"}

    result = await _call(builtin_host, grant, "browser_navigate_page", arguments)

    # Just what the call came to. No group in front of it: a runtime keeps
    # an error as one text, and would cut the group along with it.
    if why == "declined":
        said = [{"type": "text", "text": "permission denied: the user declined"}]
        assert builtin_host.provider.calls == [] and builtin_host.leases.occupied() == {}
    else:
        said = [{"type": "text", "text": "the page never loaded"}, PICTURE]
    assert result == {"content": said, "isError": True}
    assert grant.loaded_groups == set()

    # The group is still to be handed over, and comes with the first call that goes well.
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = None
    again = await _call(builtin_host, grant, "browser_navigate_page", arguments)

    assert again["isError"] is False
    assert again["content"][0]["text"].startswith(LOADED_HEADER.format(name="browser"))
    assert again["content"][1] == {"type": "text", "text": "browser_navigate_page done"}
    assert grant.loaded_groups == {"browser"}


async def test_after_a_first_call_that_failed_another_tool_of_the_group_is_refused_with_the_group(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.outcome = BuiltinOutcome(False, "the page never loaded")
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())
    await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})
    builtin_host.provider.outcome = None

    refused = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})

    assert refused["isError"] is True and "which were not loaded yet" in _text(refused)
    assert BUILTIN_GROUP_GUIDANCE["browser"] in _text(refused)
    assert [name for name, _cid, _args in builtin_host.provider.calls] == ["browser_navigate_page"]


async def test_a_first_call_that_is_stopped_has_handed_nothing_over(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.hold = asyncio.Event()
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())
    arguments = {"url": "https://example.com"}

    stopped = asyncio.ensure_future(_call(builtin_host, grant, "browser_navigate_page", arguments))
    await asyncio.wait_for(builtin_host.provider.started.wait(), 5)
    stopped.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopped

    assert builtin_host.provider.cancelled == ["browser_navigate_page"]
    # The answer the group would have come with never reached the task agent.
    assert grant.loaded_groups == set()
    builtin_host.provider.hold = None
    again = await _call(builtin_host, grant, "browser_navigate_page", arguments)
    assert again["content"][0]["text"].startswith(LOADED_HEADER.format(name="browser"))
    assert grant.loaded_groups == {"browser"}


# -- a group handed over while the calls of one reply are carried out ----------

NOT_READ = (
    "Nothing was done: {tool} is one of the {name} tools, which reached you with an earlier result of this "
    "same turn, after this call was written. Read how they are used there, then call it again."
)


async def _call_in(
    host: Any, grant: Any, reply: Any, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A call that names the model reply it was written in, as a task agent's runtime does."""
    params = {"name": name, "arguments": arguments or {}, "_meta": {"nekoclaw/reply": reply}}
    return (await _rpc(host, grant, "tools/call", params))["result"]


@pytest.mark.parametrize("handed_over_by", ["a call that was refused", "loading it by its name", "its first call"])
async def test_a_call_written_in_the_reply_a_group_was_handed_over_in_waits_for_the_next_reply(
    builtin_host, handed_over_by
):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    builtin_host.provider.definitions["browser_click"] = (BUILTIN_GROUP_BROWSER, CLICK, False)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())
    if handed_over_by == "a call that was refused":
        click = {"name": "browser_click", "arguments": {"uid": "1_1"}}
        first = await _call_in(builtin_host, grant, 7, CALL_TOOL, click)
        assert first["isError"] is True and BUILTIN_GROUP_GUIDANCE["browser"] in _text(first)
    elif handed_over_by == "loading it by its name":
        first = await _call_in(builtin_host, grant, 7, SEARCH_TOOL, {"group": "browser"})
        assert first["isError"] is False and BUILTIN_GROUP_GUIDANCE["browser"] in _text(first)
    else:
        first = await _call_in(builtin_host, grant, 7, "browser_navigate_page", {"url": "https://example.com"})
        assert first["isError"] is False and BUILTIN_GROUP_GUIDANCE["browser"] in first["content"][0]["text"]
    done, asked = list(builtin_host.provider.calls), len(asker.asked)
    reload = {"name": "browser_navigate_page", "arguments": {"type": "reload"}}

    # Two more calls of the same reply: the model wrote them before the
    # answer with the group reached it.
    waiting = [
        await _call_in(builtin_host, grant, 7, CALL_TOOL, reload),
        await _call_in(builtin_host, grant, 7, "browser_click", {"uid": "1_2"}),
    ]

    # Not carried out, and told why in a line. The group is in the earlier
    # answer; it is not sent a second time.
    assert waiting == [
        _refusal(NOT_READ.format(tool="browser_navigate_page", name="browser")),
        _refusal(NOT_READ.format(tool="browser_click", name="browser")),
    ]
    assert builtin_host.provider.calls == done and len(asker.asked) == asked

    # In the next reply the same call is carried out.
    again = await _call_in(builtin_host, grant, 8, CALL_TOOL, reload)

    assert again == {"content": [{"type": "text", "text": "browser_navigate_page done"}], "isError": False}
    assert builtin_host.provider.calls[len(done) :] == [("browser_navigate_page", commission.id, {"type": "reload"})]


def _refusal(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


async def test_the_first_call_of_a_group_is_carried_out_in_the_reply_the_group_was_handed_over_in(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())
    refused = await _call_in(builtin_host, grant, 3, CALL_TOOL, {"name": "browser_take_screenshot"})
    assert refused["isError"] is True and BUILTIN_GROUP_GUIDANCE["browser"] in _text(refused)
    arguments = {"url": "https://example.com"}

    # The step a model writes from the line about the group needs no
    # reading. The group went back with the refusal and is not sent again.
    opened = await _call_in(builtin_host, grant, 3, "browser_navigate_page", arguments)

    assert opened == {"content": [{"type": "text", "text": "browser_navigate_page done"}], "isError": False}
    assert builtin_host.provider.calls == [("browser_navigate_page", commission.id, arguments)]


@pytest.mark.parametrize(
    "meta", [None, {}, {"nekoclaw/reply": None}, {"nekoclaw/reply": True}, {"nekoclaw/reply": [7]}, {"reply": 7}, "7"]
)
async def test_a_client_that_names_no_reply_has_a_group_in_hand_as_soon_as_it_was_handed_over(builtin_host, meta):
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    async def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        params = {"name": name, "arguments": arguments, **({} if meta is None else {"_meta": meta})}
        return (await _rpc(builtin_host, grant, "tools/call", params))["result"]

    refused = await call(CALL_TOOL, {"name": "browser_take_screenshot"})
    again = await call("browser_take_screenshot", {})

    # Any MCP client can call the gateway; one that does not say which
    # reply a call is of is taken to have read what it was sent.
    assert refused["isError"] is True and "which were not loaded yet" in _text(refused)
    assert again["isError"] is False


async def test_replies_are_told_apart_by_whatever_a_client_names_them_and_a_second_load_changes_nothing(
    builtin_host,
):
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    loaded = await _call_in(builtin_host, grant, "reply-a", SEARCH_TOOL, {"group": "browser"})
    waiting = await _call_in(builtin_host, grant, "reply-a", "browser_take_screenshot")
    done = await _call_in(builtin_host, grant, "reply-b", "browser_take_screenshot")
    # Loaded once more, in a later reply (its conversation was summarized,
    # say): the group has been in hand since the first time.
    again = await _call_in(builtin_host, grant, "reply-c", SEARCH_TOOL, {"group": "browser"})
    after = await _call_in(builtin_host, grant, "reply-c", "browser_take_screenshot")
    # And one that names no reply is carried out as well.
    unnamed = await _call(builtin_host, grant, "browser_take_screenshot")

    assert (loaded["isError"], waiting["isError"], done["isError"]) == (False, True, False)
    assert _text(waiting) == NOT_READ.format(tool="browser_take_screenshot", name="browser")
    assert again["isError"] is False and BUILTIN_GROUP_GUIDANCE["browser"] in _text(again)
    assert (after["isError"], unnamed["isError"]) == (False, False)


async def test_a_group_whose_first_call_is_not_on_offer_is_told_of_without_one(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    # A provider whose browser has no tool by the name of the first call.
    del builtin_host.provider.definitions["browser_navigate_page"]
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    search = await _search_tool(builtin_host, grant)
    refused = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})

    assert search["description"].splitlines()[1:] == [
        GROUPS_INTRO,
        f"- browser: {BUILTIN_GROUP_SUMMARIES['browser']}",
        GROUP_LINES["desktop"],
    ]
    assert refused["isError"] is True and "which were not loaded yet" in _text(refused)
    assert builtin_host.provider.calls == []


# -- any other call before its group was loaded ---------------------------------------


@pytest.mark.parametrize("through_the_call_tool", [True, False])
async def test_a_call_before_its_group_was_loaded_is_not_carried_out_and_is_answered_with_the_group(
    builtin_host, through_the_call_tool
):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())
    before = [event.type for event in await builtin_host.ledger.events(commission.id)]
    arguments = {"fullPage": True}

    async def call() -> dict[str, Any]:
        if through_the_call_tool:
            return await _call(
                builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot", "arguments": arguments}
            )
        return await _call(builtin_host, grant, "browser_take_screenshot", arguments)

    refused = await call()

    assert refused["isError"] is True and len(refused["content"]) == 1
    said, handed_over = _text(refused).split("\n\n", 1)
    assert said == (
        "Nothing was done: browser_take_screenshot is one of the browser tools, which were not loaded yet. "
        "Read how they are used below, then call it again."
    )
    # Nothing was: nobody was asked, the tool did not run, the browser is
    # not occupied, and the ledger has no row for it.
    assert asker.asked == [] and builtin_host.provider.calls == []
    assert builtin_host.leases.occupied() == {}
    assert [event.type for event in await builtin_host.ledger.events(commission.id)] == before
    # What it is handed is what loading the group hands over, to the letter.
    other = _grant(builtin_host, await _commission(builtin_host, title="book the hotel"), images=True, loaded=())
    assert handed_over == _text(await _load(builtin_host, other, "browser"))

    # So the same call goes through the second time.
    again = await call()

    assert again == {"content": [{"type": "text", "text": "browser_take_screenshot done"}], "isError": False}
    assert builtin_host.provider.calls == [("browser_take_screenshot", commission.id, arguments)]
    assert [ask.category for _cid, ask in asker.asked] == ["browser"]
    assert builtin_host.leases.occupied() == {"browser": commission.id}


async def test_a_group_handed_over_in_place_of_a_call_holds_only_what_the_model_can_use(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    builtin_host.provider.definitions["browser_click"] = (BUILTIN_GROUP_BROWSER, CLICK, False)
    grant = _grant(builtin_host, await _commission(builtin_host), images=False, loaded=())

    refused = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_click", "arguments": {"uid": "1_2"}})

    # Without the screenshot tool, as when the group is loaded by its name.
    _said, handed_over = _text(refused).split("\n\n", 1)
    assert [item["name"] for item in _served(handed_over)] == ["browser_navigate_page", "browser_click"]
    assert handed_over == _text(await _load(builtin_host, grant, "browser"))
    assert builtin_host.provider.calls == []


async def test_a_candidate_plugin_named_like_no_plugin_does_not_list_the_builtin_tools(builtin_host, server):
    _catalog(server)
    # What a task brain that picked nothing might hand over.
    grant = _grant(builtin_host, await _commission(builtin_host), "None", "", images=True, loaded=())

    assert await _tool_names(builtin_host, grant) == [SEARCH_TOOL, CALL_TOOL]


async def test_one_group_handed_over_leaves_the_other_still_to_be_loaded(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())
    await _load(builtin_host, grant, "browser")

    browser = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})
    desktop = await _call(builtin_host, grant, CALL_TOOL, {"name": "computer", "arguments": {"action": "click"}})

    assert browser["isError"] is False
    assert desktop["isError"] is True
    assert _text(desktop).startswith(
        "Nothing was done: computer is one of the desktop tools, which were not loaded yet."
    )
    assert BUILTIN_GROUP_GUIDANCE["keyboard_mouse"] in _text(desktop)
    assert [name for name, _cid, _args in builtin_host.provider.calls] == ["browser_take_screenshot"]
    assert grant.loaded_groups == {"browser", "keyboard_mouse"}


async def test_what_was_loaded_through_one_token_does_not_count_for_another(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    commission = await _commission(builtin_host)
    other = await _commission(builtin_host, title="book the hotel")
    first = _grant(builtin_host, commission, images=True, loaded=())
    others = _grant(builtin_host, other, images=True, loaded=())
    await _load(builtin_host, first, "browser")
    assert (await _call(builtin_host, first, "browser_take_screenshot"))["isError"] is False

    # Another commission's task agent has not read it.
    assert "which were not loaded yet" in _text(await _call(builtin_host, others, "browser_take_screenshot"))
    # Nor has the next session of the same commission, as far as the gateway
    # can tell: its token starts with nothing loaded.
    renewed = _grant(builtin_host, commission, images=True, loaded=())
    assert renewed.loaded_groups == set()
    assert "which were not loaded yet" in _text(await _call(builtin_host, renewed, "browser_take_screenshot"))

    assert [cid for _name, cid, _args in builtin_host.provider.calls] == [commission.id]


@pytest.mark.parametrize("tool", ["browser_navigate_page", "browser_take_screenshot"])
async def test_a_call_before_its_group_was_loaded_is_told_first_that_the_commission_is_over(builtin_host, tool):
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())
    await builtin_host.ledger.transition(commission.id, "cancelled", reason="test")

    # The group's first call and any other alike.
    result = await _call(builtin_host, grant, tool)

    assert result == {"content": [{"type": "text", "text": "this commission is no longer under way"}], "isError": True}
    assert grant.loaded_groups == set() and builtin_host.provider.calls == []


# -- finding a group by what it does -------------------------------------------------


async def test_a_search_finds_a_builtin_tool_as_its_group_and_says_how_to_load_it(builtin_host, server):
    _catalog(server)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    by_tool = _text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "screenshot"}))
    by_summary = _text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "operating websites"}))
    lamp = _text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "lamp"}))

    pointer = f'Tool group "browser": {BUILTIN_GROUP_SUMMARIES["browser"]} Load it with capability_search, group "browser".'
    # Found by what one of its tools says, and by what is said of the group.
    assert by_tool == pointer and by_summary == pointer
    # Never a built-in tool's own definition: of no use without the rest.
    assert _served(by_tool) == []
    # A search by description loads nothing.
    assert grant.loaded_groups == set()
    # Plugin entries are found as they always were.
    assert [item["name"] for item in _served(lamp)] == ["plugin__lamp__switch"]
    assert "Tool group" not in lamp


async def test_a_group_counts_as_one_of_the_results_a_search_returns(builtin_host, server, monkeypatch):
    _catalog(server)
    monkeypatch.setattr("config.nekoclaw_settings.GATEWAY_SEARCH_RESULTS", 1)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    # Both groups and a plugin entry match; the best one is all that is returned.
    text = _text(await _call(builtin_host, grant, SEARCH_TOOL, {"query": "desktop mouse keyboard page lamp"}))

    assert text.splitlines() == [
        f'Tool group "desktop": {BUILTIN_GROUP_SUMMARIES["keyboard_mouse"]} Load it with capability_search, group "desktop".'
    ]


# -- tools that need a model that sees pictures -------------------------------


async def test_a_tool_that_needs_pictures_is_kept_from_a_model_that_cannot_see_them(builtin_host):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=False, loaded=())

    search = await _search_tool(builtin_host, grant)
    browser = await _load(builtin_host, grant, "browser")
    desktop = await _load(builtin_host, grant, "desktop")
    direct = await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    through = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})
    found = _text(
        await _call(builtin_host, grant, SEARCH_TOOL, {"query": "screenshot of the page, operate the desktop"})
    )

    # The desktop's only tool works from pictures: the group is not told of.
    assert search["description"].splitlines()[1:] == [GROUPS_INTRO, GROUP_LINES["browser"]]
    assert search["inputSchema"]["properties"]["group"]["enum"] == ["browser"]
    # The browser is handed over without its screenshot tool.
    assert browser["isError"] is False
    assert [item["name"] for item in _served(_text(browser))] == ["browser_navigate_page"]
    # Said as what it is, not as a group or a tool that does not exist.
    assert desktop["isError"] is True and _text(desktop).startswith(
        "The desktop tools work from pictures, and the model in use does not take pictures"
    )
    assert direct["isError"] is True and _text(direct).startswith(
        "computer works from pictures, and the model in use does not take pictures"
    )
    assert through["isError"] is True and _text(through).startswith("browser_take_screenshot works from pictures")
    unknown = await _call(builtin_host, grant, "computer_use_legacy", {})
    assert unknown["isError"] is True and "no capability named computer_use_legacy" in _text(unknown)
    assert 'Tool group "desktop"' not in found and "browser_take_screenshot" not in found
    assert grant.loaded_groups == {"browser"}
    assert builtin_host.provider.calls == [] and asker.asked == []


async def test_a_tool_that_needs_pictures_is_offered_to_a_model_that_sees_them(builtin_host):
    builtin_host.permissions.set_asker(Asker("once"))
    grant = _grant(builtin_host, await _commission(builtin_host), images=True, loaded=())

    groups = await _groups_on_offer(builtin_host, grant)
    browser = _served(_text(await _load(builtin_host, grant, "browser")))
    desktop = _served(_text(await _load(builtin_host, grant, "desktop")))
    direct = await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    through = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})

    assert groups == ["browser", "desktop"]
    assert [item["name"] for item in browser] == ["browser_navigate_page", "browser_take_screenshot"]
    assert [item["name"] for item in desktop] == ["computer"]
    assert direct["isError"] is False and through["isError"] is False
    assert [name for name, _cid, _args in builtin_host.provider.calls] == ["computer", "browser_take_screenshot"]


async def test_a_model_found_not_to_take_pictures_loses_the_picture_tools_of_a_group_it_loaded(builtin_host):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True, loaded=())
    await _load(builtin_host, grant, "browser")
    await _load(builtin_host, grant, "desktop")

    builtin_host.gateway.images_refused(commission.id)
    screenshot = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_take_screenshot"})
    computer = await _call(builtin_host, grant, CALL_TOOL, {"name": "computer", "arguments": {"action": "click"}})
    navigate = await _call(builtin_host, grant, CALL_TOOL, {"name": "browser_navigate_page"})

    # Having loaded the group does not keep a tool that works from pictures.
    assert screenshot["isError"] is True and _text(screenshot).startswith("browser_take_screenshot works from pictures")
    assert computer["isError"] is True and _text(computer).startswith("computer works from pictures")
    assert navigate["isError"] is False
    assert [name for name, _cid, _args in builtin_host.provider.calls] == ["browser_navigate_page"]
    # Loaded anew, the group comes without them.
    assert [item["name"] for item in _served(_text(await _load(builtin_host, grant, "browser")))] == [
        "browser_navigate_page"
    ]


async def test_a_new_token_for_the_commission_decides_anew_whether_its_model_sees_pictures(builtin_host):
    commission = await _commission(builtin_host)

    seeing = _grant(builtin_host, commission, images=True)
    assert await _groups_on_offer(builtin_host, seeing) == ["browser", "desktop"]
    blind = _grant(builtin_host, commission, images=False)
    assert await _groups_on_offer(builtin_host, blind) == ["browser"]
    # Without a word about it, a model is not taken to see pictures.
    unsaid = builtin_host.gateway._grant(builtin_host.gateway.issue(commission.id))
    assert await _groups_on_offer(builtin_host, unsaid) == ["browser"]
    assert unsaid.loaded_groups == set()


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


async def test_a_browser_the_user_closed_is_asked_about_before_a_call_opens_it_again(builtin_host):
    await builtin_host.permission_store.set_mode("browser", "allow")
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)
    await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})
    assert asker.asked == []

    builtin_host.provider.closed_by_user = True
    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com/next"})

    # Asked although the browser is allowed for good, with the two answers there are to this.
    assert [(cid, ask.category, ask.options) for cid, ask in asker.asked] == [
        (commission.id, "browser_reopen", ("once", "deny"))
    ]
    assert asker.asked[0][1].detail == {
        "tool": "browser_navigate_page",
        "capability": "builtin:browser:browser_navigate_page",
    }
    assert result.get("isError") is not True and builtin_host.provider.agreed == 1
    assert len(builtin_host.provider.calls) == 2
    # Open again, it is a browser like before: not asked about until it is closed again.
    await _call(builtin_host, grant, "browser_take_screenshot")
    assert len(asker.asked) == 1 and len(builtin_host.provider.calls) == 3
    builtin_host.provider.closed_by_user = True
    await _call(builtin_host, grant, "browser_take_screenshot")
    assert [ask.category for _cid, ask in asker.asked] == ["browser_reopen", "browser_reopen"]


@pytest.mark.parametrize(
    ("answer", "told", "reason"),
    [
        ("deny", "does not want it opened again", "permission denied: the user declined"),
        (
            RuntimeError("the commission has no session"),
            "could not be asked whether it may be opened again",
            "permission denied: the request for approval could not be answered",
        ),
    ],
)
async def test_a_browser_the_user_closed_stays_closed_when_they_do_not_agree(builtin_host, answer, told, reason):
    await builtin_host.permission_store.set_mode("browser", "allow")

    async def asker(commission_id: str, ask: Any) -> str:
        if isinstance(answer, BaseException):
            raise answer
        return answer

    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)
    builtin_host.provider.closed_by_user = True

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result["isError"] is True and told in _text(result)
    assert _text(result).startswith("The user closed the browser")
    # Nothing was carried out, the browser was not occupied, and it is still one to ask about.
    assert builtin_host.provider.calls == [] and builtin_host.provider.agreed == 0
    assert builtin_host.provider.closed_by_user is True
    assert builtin_host.leases.holder(BUILTIN_GROUP_BROWSER) is None
    denied = [
        event.payload for event in await builtin_host.ledger.events(commission.id) if event.type == "capability_denied"
    ]
    assert denied == [{"capability": "builtin:browser:browser_navigate_page", "reason": reason}]


async def test_the_provider_is_told_when_a_call_of_a_browser_tool_is_over_however_it_ended(builtin_host, short_wait):
    await builtin_host.permission_store.set_mode("browser", "allow")
    await builtin_host.permission_store.set_mode("keyboard_mouse", "allow")
    builtin_host.permissions.set_asker(Asker("once"))
    first = await _commission(builtin_host)
    grant = _grant(builtin_host, first, images=True)
    builtin_host.provider.closed_by_user = True

    await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})
    assert builtin_host.provider.over == [1]

    # The desktop is none of the browser's business.
    await _call(builtin_host, grant, "computer", {"action": "screenshot"})
    assert builtin_host.provider.over == [1]

    # Another task's call, which the user agrees to and which never gets the browser: it stays in use.
    other = await _commission(builtin_host, title="book the hotel")
    other_grant = _grant(builtin_host, other, images=True)
    builtin_host.provider.closed_by_user = True
    result = await _call(builtin_host, other_grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result["isError"] is True and "in use by another task" in _text(result)
    assert builtin_host.provider.agreed == 2 and len(builtin_host.provider.calls) == 2
    # Said to be over all the same, with nothing carried out for it.
    assert builtin_host.provider.over == [1, 2]

    # A call that is refused before anything is agreed to is not one to end.
    builtin_host.permissions.set_asker(Asker("deny"))
    builtin_host.provider.closed_by_user = True
    await _call(builtin_host, other_grant, "browser_navigate_page", {"url": "https://example.com"})
    assert builtin_host.provider.over == [1, 2]


async def test_the_browser_forbidden_in_the_settings_while_the_user_was_asked_stays_closed(builtin_host):
    await builtin_host.permission_store.set_mode("browser", "allow")

    async def asker(commission_id: str, ask: Any) -> str:
        # The answer is long in coming, and the settings are changed meanwhile.
        await builtin_host.permission_store.set_mode("browser", "deny")
        return "once"

    builtin_host.permissions.set_asker(asker)
    commission = await _commission(builtin_host)
    grant = _grant(builtin_host, commission, images=True)
    builtin_host.provider.closed_by_user = True

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result["isError"] is True
    assert _text(result) == "permission denied: the permission settings forbid this"
    # The newer setting wins over the answer: nothing was carried out, and nothing agreed to.
    assert builtin_host.provider.calls == [] and builtin_host.provider.agreed == 0
    assert builtin_host.provider.closed_by_user is True
    denied = [
        event.payload for event in await builtin_host.ledger.events(commission.id) if event.type == "capability_denied"
    ]
    assert denied == [
        {
            "capability": "builtin:browser:browser_navigate_page",
            "reason": "permission denied: the permission settings forbid this",
        }
    ]


async def test_the_permission_for_the_browser_given_in_the_same_call_stands_for_opening_it_again(builtin_host):
    asker = Asker("once")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)
    builtin_host.provider.closed_by_user = True

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    # One question, not two in a row.
    assert [ask.category for _cid, ask in asker.asked] == ["browser"]
    assert result.get("isError") is not True and builtin_host.provider.agreed == 1
    assert len(builtin_host.provider.calls) == 1


async def test_a_refused_permission_for_the_browser_is_not_followed_by_a_question_about_opening_it(builtin_host):
    asker = Asker("deny")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)
    builtin_host.provider.closed_by_user = True

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result["isError"] is True and [ask.category for _cid, ask in asker.asked] == ["browser"]
    assert builtin_host.provider.agreed == 0 and builtin_host.provider.calls == []


async def test_the_desktop_is_not_asked_about_for_a_browser_the_user_closed(builtin_host):
    await builtin_host.permission_store.set_mode("keyboard_mouse", "allow")
    asker = Asker("deny")
    builtin_host.permissions.set_asker(asker)
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)
    builtin_host.provider.closed_by_user = True

    result = await _call(builtin_host, grant, "computer", {"action": "screenshot"})

    assert result.get("isError") is not True and asker.asked == []
    assert builtin_host.provider.closed_by_user is True


async def test_a_provider_that_does_not_say_whether_the_browser_was_closed_is_called_as_before(builtin_host, monkeypatch):
    await builtin_host.permission_store.set_mode("browser", "allow")
    monkeypatch.delattr(Builtins, "browser_closed_by_user")
    monkeypatch.delattr(Builtins, "browser_reopen_agreed")
    grant = _grant(builtin_host, await _commission(builtin_host), images=True)

    result = await _call(builtin_host, grant, "browser_navigate_page", {"url": "https://example.com"})

    assert result.get("isError") is not True and len(builtin_host.provider.calls) == 1


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
