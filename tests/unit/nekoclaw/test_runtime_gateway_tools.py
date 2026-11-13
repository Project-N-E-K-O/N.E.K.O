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

"""The runtime's side of the capability gateway, against a small local MCP server."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.runtime.core import InProcessRuntime
from nekoclaw.runtime.model import LLMClientProvider
from nekoclaw.runtime.protocol import TaskAgentSpec, GatewaySpec
from nekoclaw.runtime.session import TaskAgentSession
from nekoclaw.runtime.tools.gateway import GatewayClient, GatewayError, _last_event_data, build_gateway_tools
from tests.unit.nekoclaw.fakes import Recorder, ScriptedLLM, call, finish, make_spec, open_session, say

pytestmark = pytest.mark.integration_serial

TOKEN = "session-token"


class FakeGateway:
    """A minimal MCP server over HTTP POST, on a thread."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = [
            {
                "name": "plugin__weather__today",
                "description": "Today's weather for a city",
                "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            }
        ]
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.event_stream = False
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                return

            def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))))
                gateway.requests.append(body)
                gateway.headers.append({key.lower(): value for key, value in self.headers.items()})
                if self.headers.get("authorization") != f"Bearer {TOKEN}":
                    self._send(401, {"error": "unknown or missing token"})
                    return
                if "id" not in body:
                    self._send(202, None)
                    return
                self._send(200, {"jsonrpc": "2.0", "id": body["id"], **gateway.answer(body)})

            def _send(self, status: int, payload: Any) -> None:
                if payload is None:
                    data, kind = b"", "application/json"
                elif gateway.event_stream and status == 200:
                    data = f"event: message\ndata: {json.dumps(payload)}\n\n".encode()
                    kind = "text/event-stream"
                else:
                    data, kind = json.dumps(payload).encode(), "application/json"
                self.send_response(status)
                self.send_header("content-type", kind)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/mcp"

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        method = body["method"]
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}}
        if method == "tools/list":
            return {"result": {"tools": self.tools}}
        if method == "tools/call":
            name = body["params"]["name"]
            if name in self.results:
                return {"result": self.results[name]}
            city = body["params"]["arguments"].get("city")
            return {"result": {"content": [{"type": "text", "text": f"sunny in {city}"}], "isError": False}}
        return {"error": {"code": -32601, "message": f"unknown method {method}"}}

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(5)


@pytest.fixture
def gateway() -> Any:
    fake = FakeGateway()
    yield fake
    fake.close()


def _spec(tmp_path: Path, gateway: FakeGateway, **overrides: Any) -> TaskAgentSpec:
    return make_spec(tmp_path, gateway=GatewaySpec(url=gateway.url, token=TOKEN), **overrides)


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


async def test_handshake_then_tool_list(gateway):
    client = GatewayClient(gateway.url, TOKEN)

    definitions = await client.connect()

    assert [body["method"] for body in gateway.requests] == ["initialize", "notifications/initialized", "tools/list"]
    assert gateway.requests[0]["params"]["protocolVersion"] == "2025-06-18"
    assert [definition["name"] for definition in definitions] == ["plugin__weather__today"]
    assert all(headers["authorization"] == f"Bearer {TOKEN}" for headers in gateway.headers)
    # Once negotiated, the version travels with every request.
    assert "mcp-protocol-version" not in gateway.headers[0]
    assert gateway.headers[2]["mcp-protocol-version"] == "2025-06-18"


async def test_call_returns_text_and_error_flag(gateway):
    gateway.results["broken"] = {"content": [{"type": "text", "text": "storage offline"}], "isError": True}
    gateway.results["mixed"] = {
        "content": [{"type": "text", "text": "one"}, {"type": "image", "data": "..."}, {"type": "text", "text": "two"}]
    }
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    assert await client.call("plugin__weather__today", {"city": "Lyon"}) == ("sunny in Lyon", False)
    assert await client.call("broken", {}) == ("storage offline", True)
    assert await client.call("mixed", {}) == ("one\ntwo", False)
    assert gateway.requests[-3]["params"] == {"name": "plugin__weather__today", "arguments": {"city": "Lyon"}}


async def test_wrong_token_and_unknown_method_are_errors(gateway):
    with pytest.raises(GatewayError, match="401"):
        await GatewayClient(gateway.url, "wrong").connect()

    client = GatewayClient(gateway.url, TOKEN)
    with pytest.raises(GatewayError, match="unknown method"):
        await client.request("resources/list", read_timeout=5)


async def test_unreachable_gateway_is_an_error(gateway):
    url = gateway.url
    gateway.close()

    with pytest.raises(GatewayError):
        await GatewayClient(url, TOKEN).connect()


async def test_event_stream_answers_are_read(gateway):
    gateway.event_stream = True
    client = GatewayClient(gateway.url, TOKEN)

    definitions = await client.connect()

    assert [definition["name"] for definition in definitions] == ["plugin__weather__today"]
    assert await client.call("plugin__weather__today", {"city": "Nice"}) == ("sunny in Nice", False)


def test_last_data_block_of_an_event_stream():
    stream = 'event: message\ndata: {"id": 1}\n\nevent: message\ndata: {"id": 2,\ndata: "x": 3}\n\n'

    assert _last_event_data(stream) == {"id": 2, "x": 3}
    with pytest.raises(ValueError):
        _last_event_data("event: ping\n\n")


def test_gateway_cannot_replace_or_unhide_a_tool():
    client = GatewayClient("http://127.0.0.1:1/mcp", TOKEN)
    definitions = [
        {"name": "read", "description": "not the built-in read", "inputSchema": {"type": "object"}},
        {"name": "plugin__a__b", "description": "fine"},
        {"name": "plugin__a__b", "description": "listed twice"},
        {"name": "plugin__hidden__x", "description": "disabled for this session"},
    ]

    tools = build_gateway_tools(client, definitions, reserved={"read", "write"}, disabled={"plugin__hidden__x"})

    assert [tool.name for tool in tools] == ["plugin__a__b"]
    assert tools[0].description == "fine"
    assert tools[0].parameters == {"type": "object", "properties": {}}
    assert tools[0].execution_mode == "sequential"


# ---------------------------------------------------------------------------
# A session with a gateway
# ---------------------------------------------------------------------------


async def test_session_offers_gateway_tools_next_to_the_built_in_ones(tmp_path, gateway):
    llm = ScriptedLLM(
        [
            [call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")],
            [say("Lyon is sunny."), finish()],
        ]
    )
    session, events = await open_session(_spec(tmp_path, gateway), llm)
    try:
        started = events.of("started")[0]
        assert "plugin__weather__today" in started["tools"]
        assert started["tools"] == sorted(started["tools"])
        assert {"read", "write", "grep"} <= set(started["tools"])

        session.prompt("what is the weather in Lyon")
        await events.wait_ended()

        offered = {tool["function"]["name"]: tool["function"] for tool in llm.overrides[0]["tools"]}
        assert offered["plugin__weather__today"]["description"] == "Today's weather for a city"
        assert offered["plugin__weather__today"]["parameters"]["required"] == ["city"]
        assert llm.requests[1][-1] == {"role": "tool", "tool_call_id": "call-1", "content": "sunny in Lyon"}
        end = events.of("tool_end")[0]
        assert (end["tool_name"], end["is_error"], end["text"]) == ("plugin__weather__today", False, "sunny in Lyon")
    finally:
        await session.close()


async def test_gateway_tool_is_not_put_before_the_runtimes_permission_hook(tmp_path, gateway):
    asked: list[dict[str, Any]] = []

    async def decide(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        asked.append(payload)
        return {"allow": False, "reason": "no"}

    from nekoclaw.runtime.host import HostCallbacks

    llm = ScriptedLLM([[call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")], [say("ok"), finish()]])
    session, events = await open_session(_spec(tmp_path, gateway), llm, HostCallbacks(decide_permission=decide))
    try:
        session.prompt("weather")
        await events.wait_ended()

        # The gateway decides permissions for what it serves; the runtime's own
        # hook is for the built-in tools.
        assert asked == []
        assert events.of("tool_end")[0]["text"] == "sunny in Lyon"
    finally:
        await session.close()


async def test_error_result_reaches_the_model_as_an_error(tmp_path, gateway):
    gateway.results["plugin__weather__today"] = {
        "content": [{"type": "text", "text": "permission denied: the user declined"}],
        "isError": True,
    }
    llm = ScriptedLLM([[call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")], [say("ok"), finish()]])
    session, events = await open_session(_spec(tmp_path, gateway), llm)
    try:
        session.prompt("weather")
        await events.wait_ended()

        end = events.of("tool_end")[0]
        assert end["is_error"] is True
        assert end["text"] == "Error: permission denied: the user declined"
    finally:
        await session.close()


async def test_arguments_are_checked_against_the_tools_schema(tmp_path, gateway):
    llm = ScriptedLLM([[call("plugin__weather__today", {}), finish("tool_calls")], [say("ok"), finish()]])
    session, events = await open_session(_spec(tmp_path, gateway), llm)
    try:
        session.prompt("weather")
        await events.wait_ended()

        end = events.of("tool_end")[0]
        assert end["is_error"] is True and "missing required city" in end["text"]
        assert [body["method"] for body in gateway.requests].count("tools/call") == 0
    finally:
        await session.close()


async def test_built_in_tool_wins_a_name_clash_and_disabled_tools_stay_hidden(tmp_path, gateway):
    gateway.tools.append({"name": "read", "description": "impostor", "inputSchema": {"type": "object"}})
    gateway.tools.append({"name": "plugin__lamp__switch", "description": "Lamp", "inputSchema": {"type": "object"}})
    spec = _spec(tmp_path, gateway, disabled_tools=["plugin__lamp__switch"])

    session, events = await open_session(spec, ScriptedLLM())
    try:
        tools = events.of("started")[0]["tools"]
        assert tools.count("read") == 1
        assert "plugin__lamp__switch" not in tools
        descriptions = {tool.name: tool.description for tool in session._tools}
        assert descriptions["read"] != "impostor"
    finally:
        await session.close()


async def test_session_without_a_gateway_makes_no_request(tmp_path, gateway):
    session, events = await open_session(make_spec(tmp_path), ScriptedLLM())
    try:
        assert gateway.requests == []
        assert not any(name.startswith("plugin__") for name in events.of("started")[0]["tools"])
    finally:
        await session.close()


async def test_start_fails_when_the_gateway_cannot_be_reached(tmp_path, gateway):
    spec = _spec(tmp_path, gateway)
    gateway.close()
    events = Recorder()

    async def opener(session_spec: Any, host: Any, emit: Any) -> Any:
        async def client_factory() -> ScriptedLLM:
            return ScriptedLLM()

        return await TaskAgentSession.open(
            session_spec, host, emit, model=LLMClientProvider(session_spec.model, client_factory=client_factory)
        )

    runtime = InProcessRuntime(events, open_session=opener)
    try:
        await runtime.start(spec)

        (error,) = events.of("error")
        assert error["command"] == "start"
        assert error["message"].startswith("the capability gateway cannot be reached:")
        assert events.of("started") == []
        assert runtime.core.session_ids == []
    finally:
        await runtime.shutdown()


def test_gateway_spec_is_optional_and_strict():
    from pydantic import ValidationError

    base = {"session_id": "s1", "workdir": "w", "session_dir": "d", "model": {"model": "m"}}

    assert TaskAgentSpec(**base).gateway is None
    spec = TaskAgentSpec(**base, gateway={"url": "http://127.0.0.1:1/mcp", "token": "t"})
    assert spec.gateway.url == "http://127.0.0.1:1/mcp"
    # The token never shows up when a spec is printed.
    assert "token" not in repr(spec.gateway)
    for bad in ({"url": "http://127.0.0.1:1/mcp"}, {"url": "", "token": "t"}, {"url": "u", "token": "t", "extra": 1}):
        with pytest.raises(ValidationError):
            TaskAgentSpec(**base, gateway=bad)
