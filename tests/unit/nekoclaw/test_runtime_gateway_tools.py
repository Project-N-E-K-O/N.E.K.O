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

from config.prompts import prompts_nekoclaw as texts
from nekoclaw.runtime.core import InProcessRuntime
from nekoclaw.runtime.loop.messages import ImageContent, TextContent, ToolResultMessage
from nekoclaw.runtime.model import LLMClientProvider
from nekoclaw.runtime.protocol import TaskAgentSpec, GatewaySpec, ModelConfig
from nekoclaw.runtime.session import TaskAgentSession
from nekoclaw.runtime.tools import gateway as gateway_module
from nekoclaw.runtime.tools.base import ToolError
from nekoclaw.runtime.tools.gateway import (
    MAX_IMAGES_PER_RESULT,
    GatewayClient,
    GatewayError,
    GatewayResult,
    _last_event_data,
    build_gateway_tools,
)
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


async def test_every_request_is_made_with_the_one_tls_configuration_of_the_process(gateway, monkeypatch):
    import ssl

    import httpx

    given: list[Any] = []
    real_client = httpx.AsyncClient

    def client(**kwargs: Any) -> Any:
        given.append(kwargs.get("verify"))
        return real_client(**kwargs)

    made: list[Any] = []
    real_context = httpx.create_ssl_context

    def context(**kwargs: Any) -> Any:
        made.append(kwargs)
        return real_context(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setattr(httpx, "create_ssl_context", context)
    monkeypatch.setattr(gateway_module, "_TLS", None)

    # Two sessions: a handshake each, and a call.
    await GatewayClient(gateway.url, TOKEN).connect()
    second = GatewayClient(gateway.url, TOKEN)
    await second.connect()
    await second.call("plugin__weather__today", {"city": "Lyon"})

    # A client that is given none loads the certificate bundle anew, for every request.
    assert len(given) == 7 and isinstance(given[0], ssl.SSLContext)
    assert all(item is given[0] for item in given)
    # Put together once, and without what the environment says of certificates and proxies.
    assert made == [{"trust_env": False}]


def _picture(data: str = "QUJD", mime: Any = "image/jpeg") -> dict[str, Any]:
    """One MCP image block as the gateway sends it."""
    block: dict[str, Any] = {"type": "image", "data": data}
    if mime is not None:
        block["mimeType"] = mime
    return block


async def test_call_returns_text_pictures_and_the_error_flag(gateway):
    gateway.results["broken"] = {"content": [{"type": "text", "text": "storage offline"}], "isError": True}
    gateway.results["mixed"] = {
        "content": [
            {"type": "text", "text": "clicked"},
            _picture("QUJD", "image/jpeg"),
            {"type": "text", "text": "the window moved"},
            _picture("REVG", "image/png"),
            # Neither text nor a picture: passed over.
            {"type": "resource", "resource": {"uri": "file:///x"}},
        ]
    }
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    assert await client.call("plugin__weather__today", {"city": "Lyon"}) == GatewayResult("sunny in Lyon", (), False)
    assert await client.call("broken", {}) == GatewayResult("storage offline", (), True)
    # The text parts joined, and the pictures in the order they came. Text
    # that came as more than one block is also kept as it came.
    assert await client.call("mixed", {}) == GatewayResult(
        "clicked\nthe window moved",
        (ImageContent(data="QUJD", mime_type="image/jpeg"), ImageContent(data="REVG", mime_type="image/png")),
        False,
        ("clicked", "the window moved"),
    )
    assert gateway.requests[-3]["params"] == {
        "name": "plugin__weather__today",
        "arguments": {"city": "Lyon"},
        "_meta": {"nekoclaw/reply": 0, "nekoclaw/compactions": 0},
    }


async def test_every_call_names_the_reply_of_the_model_it_was_written_in(gateway):
    from config.nekoclaw_settings import GATEWAY_REPLY_META_KEY

    assert GATEWAY_REPLY_META_KEY == "nekoclaw/reply"
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    await client.call("plugin__weather__today", {"city": "Lyon"})
    client.next_reply()
    await client.call("plugin__weather__today", {"city": "Nice"})
    await client.call("plugin__weather__today", {"city": "Metz"})
    client.next_reply()
    await client.call("plugin__weather__today", {"city": "Brest"})

    named = [body["params"]["_meta"]["nekoclaw/reply"] for body in gateway.requests if body["method"] == "tools/call"]
    # The calls of one reply name the same reply, those of the next another.
    assert named == [0, 1, 1, 2]
    # Nothing but the calls names one.
    assert all("_meta" not in (body.get("params") or {}) for body in gateway.requests if body["method"] != "tools/call")


async def test_every_call_names_how_often_the_conversation_was_summed_up(gateway):
    from config.nekoclaw_settings import GATEWAY_COMPACTIONS_META_KEY

    assert GATEWAY_COMPACTIONS_META_KEY == "nekoclaw/compactions"
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    await client.call("plugin__weather__today", {"city": "Lyon"})
    client.compactions = 2
    await client.call("plugin__weather__today", {"city": "Nice"})

    named = [body["params"]["_meta"] for body in gateway.requests if body["method"] == "tools/call"]
    assert [meta["nekoclaw/compactions"] for meta in named] == [0, 2]


async def test_a_session_tells_the_gateway_when_its_conversation_was_summed_up(tmp_path, gateway):
    from nekoclaw.runtime.protocol import ModelConfig

    model = ModelConfig(model="fake-model", api_key="k", context_window=32_000, max_output_tokens=2_000)
    spec = _spec(tmp_path, gateway, model=model)

    def weather(city: str, call_id: str) -> list[Any]:
        return [call("plugin__weather__today", {"city": city}, call_id), finish("tool_calls")]

    def told() -> list[int]:
        return [
            body["params"]["_meta"]["nekoclaw/compactions"] for body in gateway.requests if body["method"] == "tools/call"
        ]

    llm = ScriptedLLM([weather("Lyon", "c1"), [say("Sunny."), finish()]])
    session, events = await open_session(spec, llm)
    try:
        session.prompt("weather in Lyon")
        await events.wait_ended()
        for index in range(4):
            llm.add(say(f"Step {index}. " + " ".join(f"s{index}w{word}" for word in range(300))), finish())
            mark = len(events.events)
            session.prompt(f"Do step {index}.")
            await events.wait_ended(start=mark)
        # The next request is refused as too long: the conversation is summed up, and the call after that says so.
        llm.add(RuntimeError("Error code: 400 - This model's maximum context length is 32000 tokens."))
        llm.add(say("## Current Work\n- summed up"), finish())
        llm.add(*weather("Nice", "c2"))
        llm.add(say("Sunny there too."), finish())
        mark = len(events.events)
        session.prompt("and in Nice")
        await events.wait_ended(start=mark)
        assert [event["count"] for event in events.of("compacted")] == [1]
        assert told() == [0, 1]
    finally:
        await session.close()

    # A session opened later goes on counting from what its transcript holds.
    llm = ScriptedLLM([weather("Metz", "c3"), [say("Sunny."), finish()]])
    session, events = await open_session(spec, llm)
    try:
        session.prompt("and in Metz")
        await events.wait_ended()
        assert told() == [0, 1, 1]
    finally:
        await session.close()


async def test_a_session_tells_the_gateway_which_calls_were_written_in_one_reply(tmp_path, gateway):
    llm = ScriptedLLM(
        [
            # Two calls in one reply, then one in the next.
            [
                call("plugin__weather__today", {"city": "Lyon"}, "c1"),
                call("plugin__weather__today", {"city": "Nice"}, "c2", index=1),
                finish("tool_calls"),
            ],
            [call("plugin__weather__today", {"city": "Metz"}, "c3"), finish("tool_calls")],
            [say("Sunny everywhere."), finish()],
        ]
    )
    session, events = await open_session(_spec(tmp_path, gateway), llm)
    try:
        session.prompt("what is the weather in Lyon, Nice and Metz")
        await events.wait_ended()

        calls = [body["params"] for body in gateway.requests if body["method"] == "tools/call"]
        assert [params["arguments"]["city"] for params in calls] == ["Lyon", "Nice", "Metz"]
        first, second, third = (params["_meta"]["nekoclaw/reply"] for params in calls)
        assert first == second != third
    finally:
        await session.close()


async def test_more_pictures_than_a_result_may_carry_are_dropped_and_the_text_says_how_many(gateway):
    shots = [_picture(f"QUJ{n}", "image/png") for n in range(MAX_IMAGES_PER_RESULT + 2)]
    gateway.results["many"] = {"content": [{"type": "text", "text": "six shots"}, *shots]}
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    result = await client.call("many", {})

    assert MAX_IMAGES_PER_RESULT == 4
    assert [image.data for image in result.images] == ["QUJ0", "QUJ1", "QUJ2", "QUJ3"]
    assert result.text == "six shots\n[2 picture(s) of this result could not be passed on.]"
    assert result.parts == ("six shots", "[2 picture(s) of this result could not be passed on.]")
    assert result.is_error is False


async def test_pictures_of_a_kind_a_model_does_not_take_are_dropped_and_the_text_says_so(gateway):
    gateway.results["odd"] = {
        "content": [
            {"type": "text", "text": "a drawing"},
            _picture("QUJD", "image/svg+xml"),
            _picture("QUJD", None),
            _picture("", "image/png"),
            {"type": "image", "data": 5, "mimeType": "image/png"},
            _picture("R0lG", "image/gif"),
            _picture("UklG", "image/webp"),
        ]
    }
    gateway.results["only"] = {"content": [_picture("QUJD", "application/pdf")]}
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    odd = await client.call("odd", {})
    only = await client.call("only", {})

    assert [(image.data, image.mime_type) for image in odd.images] == [("R0lG", "image/gif"), ("UklG", "image/webp")]
    assert odd.text == "a drawing\n[4 picture(s) of this result could not be passed on.]"
    # Said even when it is all the result has: the model may be waiting for the picture.
    assert only == GatewayResult("[1 picture(s) of this result could not be passed on.]", (), False)


async def test_an_oversized_picture_is_dropped_and_the_text_says_so(gateway, monkeypatch):
    monkeypatch.setattr(gateway_module, "MAX_IMAGE_BASE64_CHARS", 16)
    gateway.results["big"] = {
        "content": [{"type": "text", "text": "two shots"}, _picture("A" * 16, "image/png"), _picture("B" * 17, "image/png")]
    }
    client = GatewayClient(gateway.url, TOKEN)
    await client.connect()

    result = await client.call("big", {})

    # One at the limit passes; one over it does not.
    assert [image.data for image in result.images] == ["A" * 16]
    assert result.text == "two shots\n[1 picture(s) of this result could not be passed on.]"


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
    assert await client.call("plugin__weather__today", {"city": "Nice"}) == GatewayResult("sunny in Nice", (), False)


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


class _Answers:
    """Stands in for the client: every call comes back with the result it was given."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, arguments: dict[str, Any]) -> GatewayResult:
        self.calls.append((name, dict(arguments)))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


async def _forward(result: Any, arguments: dict[str, Any] | None = None) -> Any:
    """Run the tool the runtime builds for a gateway tool named ``computer``."""
    client = _Answers(result)
    definition = {
        "name": "computer",
        "description": "Operate the desktop.",
        "inputSchema": {"type": "object", "properties": {"action": {"type": "string"}}},
    }
    (tool,) = build_gateway_tools(client, [definition])
    outcome = await tool.execute("call-1", arguments or {"action": "click"})
    assert client.calls == [("computer", arguments or {"action": "click"})]
    return outcome


async def test_the_pictures_of_a_result_follow_its_text_in_the_tool_result():
    first = ImageContent(data="QUJD", mime_type="image/jpeg")
    second = ImageContent(data="REVG", mime_type="image/png")

    outcome = await _forward(GatewayResult("clicked", (first, second), False))

    assert outcome.content == [TextContent(text="clicked"), first, second]


async def test_a_result_of_text_alone_is_a_tool_result_of_text_alone():
    assert (await _forward(GatewayResult("clicked", (), False))).content == [TextContent(text="clicked")]
    assert (await _forward(GatewayResult("", (), False))).content == [TextContent(text="(no output)")]


async def test_a_result_of_several_texts_stays_several_blocks_that_read_as_the_one_text():
    shot = ImageContent(data="QUJD", mime_type="image/jpeg")
    parts = ("the tools of a group", "what the call returned", "[1 picture(s) of this result could not be passed on.]")
    text = "\n".join(parts)

    outcome = await _forward(GatewayResult(text, (shot,), False, parts))
    wordless = await _forward(GatewayResult(text, (), False, parts))

    # A block each, in the order they came, and the pictures after them: the
    # runtime cuts an old result block by block.
    assert outcome.content == [
        TextContent(text="the tools of a group\n"),
        TextContent(text="what the call returned\n"),
        TextContent(text="[1 picture(s) of this result could not be passed on.]"),
        shot,
    ]
    # Put together they read as the one text did.
    assert outcome.text == text
    # The same without a picture.
    assert wordless.content == outcome.content[:3]


async def test_an_error_result_of_several_texts_is_raised_with_all_of_them():
    parts = ("the tools of a group", "the user declined")
    with pytest.raises(ToolError) as refused:
        await _forward(GatewayResult("\n".join(parts), (), True, parts))

    assert str(refused.value) == "the tools of a group\nthe user declined"


async def test_a_picture_without_a_word_still_gets_a_line_of_text_in_front():

    shot = ImageContent(data="QUJD", mime_type="image/jpeg")

    outcome = await _forward(GatewayResult("", (shot,), False))

    assert outcome.content == [TextContent(text="(no output)"), shot]


async def test_an_error_result_is_raised_with_its_text():
    shot = ImageContent(data="QUJD", mime_type="image/jpeg")

    with pytest.raises(ToolError) as refused:
        await _forward(GatewayResult("The desktop is in use by another task.", (shot,), True))
    with pytest.raises(ToolError) as wordless:
        await _forward(GatewayResult("", (), True))
    with pytest.raises(ToolError) as unreachable:
        await _forward(GatewayError("HTTP 401: unknown or missing token"))

    assert str(refused.value) == "The desktop is in use by another task."
    assert str(wordless.value) == "computer failed"
    assert str(unreachable.value) == "computer failed: HTTP 401: unknown or missing token"


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


async def test_a_group_is_loaded_with_its_name_alone_through_the_search_tool_of_a_session_opened_without_groups(
    tmp_path, gateway
):
    from nekoclaw.host.gateway import CALL_TOOL, SEARCH_TOOL, _CALL_TOOL_DEFINITION, _search_tool_definition

    # As the host lists its two tools while no group of built-in tools can be used.
    gateway.tools = [_search_tool_definition([]), _CALL_TOOL_DEFINITION]
    gateway.results[SEARCH_TOOL] = {"content": [{"type": "text", "text": "the browser tools"}], "isError": False}
    llm = ScriptedLLM([[call(SEARCH_TOOL, {"group": "browser"}), finish("tool_calls")], [say("Loaded."), finish()]])
    session, events = await open_session(_spec(tmp_path, gateway), llm)
    try:
        assert {SEARCH_TOOL, CALL_TOOL} <= set(events.of("started")[0]["tools"])
        session.prompt("open the page")
        await events.wait_ended()

        # The call reaches the gateway: the definition the session keeps does not ask for a query.
        asked = [body["params"] for body in gateway.requests if body.get("method") == "tools/call"]
        assert [(item["name"], item["arguments"]) for item in asked] == [(SEARCH_TOOL, {"group": "browser"})]
        end = events.of("tool_end")[0]
        assert (end["tool_name"], end["is_error"], end["text"]) == (SEARCH_TOOL, False, "the browser tools")
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


def _pictures(request: list[dict[str, Any]]) -> list[str]:
    """The pictures one model request carries, as their data URLs."""
    return [
        part["image_url"]["url"]
        for message in request
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image_url"
    ]


def _seeing() -> ModelConfig:
    return ModelConfig(model="fake-model", api_key="test-key", supports_images=True)


async def test_a_screenshot_from_a_gateway_tool_reaches_the_model_after_the_tools_text(tmp_path, gateway):
    gateway.results["plugin__weather__today"] = {
        "content": [{"type": "text", "text": "the sky over Lyon"}, _picture("QUJD", "image/jpeg")],
        "isError": False,
    }
    llm = ScriptedLLM([[call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")], [say("Sunny."), finish()]])
    session, events = await open_session(_spec(tmp_path, gateway, model=_seeing()), llm)
    try:
        session.prompt("what does the sky look like")
        await events.wait_ended()

        (result,) = [message for message in session.harness.message_list if isinstance(message, ToolResultMessage)]
        assert result.content == [
            TextContent(text="the sky over Lyon"),
            ImageContent(data="QUJD", mime_type="image/jpeg"),
        ]
        assert result.is_error is False
        second = llm.requests[1]
        assert {"role": "tool", "tool_call_id": "call-1", "content": "the sky over Lyon"} in second
        assert _pictures(second) == ["data:image/jpeg;base64,QUJD"]
    finally:
        await session.close()


async def test_a_model_that_sees_no_pictures_gets_a_placeholder_for_the_screenshot(tmp_path, gateway):
    gateway.results["plugin__weather__today"] = {
        "content": [{"type": "text", "text": "the sky over Lyon"}, _picture("QUJD", "image/jpeg")],
        "isError": False,
    }
    llm = ScriptedLLM([[call("plugin__weather__today", {"city": "Lyon"}), finish("tool_calls")], [say("Sunny."), finish()]])
    session, events = await open_session(_spec(tmp_path, gateway), llm)
    try:
        session.prompt("what does the sky look like")
        await events.wait_ended()

        second = llm.requests[1]
        assert _pictures(second) == []
        assert second[-1] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "the sky over Lyon\n[image omitted: the model does not accept images]",
        }
    finally:
        await session.close()


@pytest.mark.parametrize("pooled", [True, False])
async def test_tools_of_one_pool_share_the_screenshots_that_stay_in_the_conversation(tmp_path, gateway, pooled):
    empty = {"type": "object", "properties": {}}
    gateway.tools[:] = [
        {"name": "browser_click", "description": "Click an element.", "inputSchema": empty},
        {"name": "browser_take_screenshot", "description": "Take a screenshot.", "inputSchema": empty},
    ]
    gateway.results["browser_click"] = {"content": [{"type": "text", "text": "clicked"}, _picture("QUJD")]}
    gateway.results["browser_take_screenshot"] = {"content": [{"type": "text", "text": "the page"}, _picture("REVG")]}
    llm = ScriptedLLM(
        [
            [call("browser_click", {}, "c1"), finish("tool_calls")],
            [call("browser_take_screenshot", {}, "c2"), finish("tool_calls")],
            [say("Done."), finish()],
        ]
    )
    names = ("browser_click", "browser_take_screenshot")
    spec = _spec(
        tmp_path,
        gateway,
        model=_seeing(),
        image_retention={name: 1 for name in names},
        image_pools={name: "browser" for name in names} if pooled else {},
    )
    session, events = await open_session(spec, llm)
    try:
        assert session.context.image_retention == {name: 1 for name in names}
        assert session.context.image_pools == ({name: "browser" for name in names} if pooled else {})

        session.prompt("click, then look")
        await events.wait_ended()

        assert _pictures(llm.requests[1]) == ["data:image/jpeg;base64,QUJD"]
        earlier = next(message for message in llm.requests[2] if message.get("tool_call_id") == "c1")
        if pooled:
            # One screenshot for the browser as a whole: the newer one.
            assert _pictures(llm.requests[2]) == ["data:image/jpeg;base64,REVG"]
            assert earlier["content"] == f"clicked{texts.IMAGE_OMITTED_PLACEHOLDER}"
        else:
            # One for each tool.
            assert _pictures(llm.requests[2]) == ["data:image/jpeg;base64,QUJD", "data:image/jpeg;base64,REVG"]
            assert earlier["content"] == "clicked"
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
