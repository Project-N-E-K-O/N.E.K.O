"""The model endpoint dsh is given: what is sent on, what is read back, what is not sent at all."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from nekoclaw.dsh.forward import (
    ModelForwarder,
    follows_final_report,
    prepare_request,
    unsupported_reason,
)
from nekoclaw.runtime.protocol import ModelConfig

REPORT = "mcp__host__report"
# What tells the dsh process of these tests from any other: its token at the endpoint.
PROCESS = "token-of-the-process"


def _report_turn(arguments: Any, result: str = "report delivered (ready).", name: str = REPORT) -> list[dict[str, Any]]:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": name, "arguments": raw}}]},
        {"role": "tool", "tool_call_id": "c1", "content": result},
    ]


# ---------------------------------------------------------------------------
# The request after a closing report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["ready", "failed"])
def test_a_report_that_finishes_the_commission_is_recognised(status: str) -> None:
    assert follows_final_report(_report_turn({"status": status, "summary": "x"}), REPORT)


@pytest.mark.parametrize("status", ["waiting", "blocked", "progress", "done", ""])
def test_a_report_the_commission_goes_on_from_is_followed_by_the_model(status: str) -> None:
    # What is put into dsh's record in the model's place is put there only when the commission is over.
    assert not follows_final_report(_report_turn({"status": status, "summary": "x"}), REPORT)


def test_a_result_given_as_parts_is_read_too() -> None:
    messages = _report_turn({"status": "ready", "summary": "x"})
    messages[-1]["content"] = [{"type": "text", "text": "report delivered (ready). noted"}]
    assert follows_final_report(messages, REPORT)


@pytest.mark.parametrize(
    "messages",
    [
        # A report that leaves the turn open.
        _report_turn({"status": "progress", "summary": "x"}),
        # Another tool.
        _report_turn({"status": "ready", "summary": "x"}, name="write"),
        # A report the host refused, or that failed.
        _report_turn({"status": "ready", "summary": "x"}, result="Error: the ledger is closed"),
        # Arguments that are not what the tool takes.
        _report_turn("not json"),
        _report_turn(["ready"]),
        # A result that belongs to no call of the reply.
        [*_report_turn({"status": "ready", "summary": "x"}), {"role": "tool", "tool_call_id": "c9", "content": "report delivered (ready)."}],
        # Something came after the result: the turn goes on with it.
        [*_report_turn({"status": "ready", "summary": "x"}), {"role": "user", "content": "<system-reminder>…</system-reminder>"}],
        # No conversation at all.
        [],
        None,
        [{"role": "user", "content": "hello"}],
        [{"role": "tool", "tool_call_id": "c1", "content": "report delivered (ready)."}],
    ],
)
def test_any_other_request_goes_to_the_model(messages: Any) -> None:
    assert not follows_final_report(messages, REPORT)


def test_a_report_made_together_with_other_calls_does_not_end_the_turn() -> None:
    messages = _report_turn({"status": "ready", "summary": "x"})
    messages[2]["tool_calls"].append({"id": "c2", "type": "function", "function": {"name": "read", "arguments": "{}"}})
    messages.append({"role": "tool", "tool_call_id": "c2", "content": "file"})
    assert not follows_final_report(messages, REPORT)


def test_two_closing_reports_in_one_reply_end_the_turn_when_both_were_delivered() -> None:
    messages = _report_turn({"status": "ready", "summary": "x"})
    messages[2]["tool_calls"].append({"id": "c2", "type": "function", "function": {"name": REPORT, "arguments": '{"status": "failed", "summary": "y"}'}})
    assert not follows_final_report(messages, REPORT)  # the second has no result yet
    messages.append({"role": "tool", "tool_call_id": "c2", "content": "report delivered (failed)."})
    assert follows_final_report(messages, REPORT)
    messages[-1]["tool_call_id"] = "someone-else"
    assert not follows_final_report(messages, REPORT)


# ---------------------------------------------------------------------------
# What the real endpoint gets
# ---------------------------------------------------------------------------


def _payload() -> dict[str, Any]:
    return {
        "model": "slot-3",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": 8192,
        "tools": [{"type": "function", "function": {"name": "read"}}],
    }


def test_the_real_model_and_key_replace_dshs_and_the_rest_is_kept() -> None:
    model = ModelConfig(model="real-model", base_url="http://192.168.1.20:8000/v1/", api_key="sk-real", auto_extra_body=False)
    payload = _payload()

    url, headers, body = prepare_request(payload, model)

    # A plain http address on the local network is used as it is configured.
    assert url == "http://192.168.1.20:8000/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-real" and headers["User-Agent"].startswith("neko/")
    assert body == {**payload, "model": "real-model"}
    assert payload["model"] == "slot-3"  # the caller's request is not changed


def test_a_model_without_a_key_gets_the_placeholder_the_model_client_sends() -> None:
    _url, headers, _body = prepare_request(_payload(), ModelConfig(model="m", base_url="http://127.0.0.1:1/v1"))
    assert headers["Authorization"] == "Bearer sk-placeholder"


def test_an_extra_body_given_with_the_model_is_merged_into_the_request() -> None:
    model = ModelConfig(model="m", base_url="http://127.0.0.1:1/v1", extra_body={"enable_thinking": False, "top_k": 5})
    _url, _headers, body = prepare_request(_payload(), model)
    assert body["enable_thinking"] is False and body["top_k"] == 5 and "extra_body" not in body


def test_what_a_registered_provider_needs_is_added_as_the_model_client_adds_it() -> None:
    from config.providers import get_extra_body, get_token_limit_field
    from utils.llm_client import create_chat_llm

    # Whatever the registry says for these endpoints, the forwarder says the same.
    for name, base_url in [
        ("qwen-flash", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        ("deepseek-chat", "https://api.deepseek.com/v1"),
        ("glm-4.5-flash", "https://open.bigmodel.cn/api/paas/v4"),
        ("some-local-model", "http://127.0.0.1:11434/v1"),
    ]:
        _url, headers, body = prepare_request(_payload(), ModelConfig(model=name, base_url=base_url, api_key="k"))
        client = create_chat_llm(name, base_url, "k", max_completion_tokens=8192, streaming=True)
        expected = client._params([{"role": "user", "content": "hi"}], stream=True)
        merged = {**{k: v for k, v in expected.items() if k != "extra_body"}, **(expected.get("extra_body") or {})}
        limit_field = get_token_limit_field(base_url)
        assert body[limit_field] == merged[limit_field] == 8192
        assert ("max_completion_tokens" in body) == (limit_field == "max_completion_tokens")
        for key, value in (get_extra_body(name, base_url) or {}).items():
            assert merged.get(key) == value
        for key in set(merged) - {"messages", "tools", "model", "stream", "stream_options"}:
            assert body[key] == merged[key], (name, key)
        for key, value in (client._aclient.default_headers or {}).items():
            if key.lower().startswith("x-dashscope"):
                assert headers[key] == value


def test_auto_resolution_can_be_switched_off() -> None:
    from config.providers import get_extra_body

    name, base_url = "qwen-flash", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    registered = get_extra_body(name, base_url) or {}
    _url, _headers, body = prepare_request(_payload(), ModelConfig(model=name, base_url=base_url, api_key="k", auto_extra_body=False))
    assert all(key not in body for key in registered)


def test_which_models_are_forwarded() -> None:
    assert unsupported_reason(ModelConfig(model="m", base_url="http://10.0.0.5:8080/v1")) is None
    assert "address" in str(unsupported_reason(ModelConfig(model="m")))
    assert "Anthropic" in str(unsupported_reason(ModelConfig(model="m", base_url="https://x.invalid/v1", provider_type="anthropic")))
    assert "Anthropic" in str(unsupported_reason(ModelConfig(model="claude", base_url="https://api.anthropic.com/v1")))


# ---------------------------------------------------------------------------
# One request through the forwarder
# ---------------------------------------------------------------------------


class _Asgi:
    """Collects what the forwarder sends back to dsh."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = b""
        self.disconnect = asyncio.Event()

    async def send(self, message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = {key.decode(): value.decode() for key, value in message["headers"]}
        else:
            self.body += message.get("body", b"")

    async def receive(self) -> dict[str, Any]:
        await self.disconnect.wait()
        return {"type": "http.disconnect"}

    def events(self) -> list[dict[str, Any]]:
        lines = [line[5:].strip() for line in self.body.decode("utf-8").splitlines() if line.startswith("data:")]
        return [json.loads(line) for line in lines if line != "[DONE]"]


def _sse(*chunks: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in chunks) + b"data: [DONE]\n\n"


def _forwarder(handler: Any, usage: list[tuple[str, dict[str, Any]]]) -> ModelForwarder:
    forwarder = ModelForwarder(lambda session_id, used: usage.append((session_id, used)), report_tool=REPORT, transport=httpx.MockTransport(handler))
    forwarder.bind(PROCESS, "slot-3", "commission-1", ModelConfig(model="real-model", base_url="http://model.test/v1", api_key="sk-real", auto_extra_body=False))
    return forwarder


async def test_an_answer_is_passed_back_as_it_came_and_its_usage_handed_on() -> None:
    seen: list[httpx.Request] = []
    stream = _sse(
        {"choices": [{"index": 0, "delta": {"content": "hel"}}]},
        {"choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128}},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream)

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(handler, usage)
    asgi = _Asgi()

    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    assert asgi.status == 200 and asgi.headers["content-type"] == "text/event-stream" and asgi.body == stream
    assert usage == [("commission-1", {"prompt_tokens": 120, "completion_tokens": 8, "total_tokens": 128})]
    sent = json.loads(seen[0].content)
    assert str(seen[0].url) == "http://model.test/v1/chat/completions" and sent["model"] == "real-model"
    assert seen[0].headers["authorization"] == "Bearer sk-real"


async def test_usage_is_found_whatever_way_the_answer_is_cut_into_pieces() -> None:
    stream = _sse(
        {"choices": [{"index": 0, "delta": {"content": "x"}}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}},
    )

    class Pieces(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(stream), 7):
                yield stream[index : index + 7]

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Pieces()), usage)
    asgi = _Asgi()
    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    assert asgi.body == stream and usage[0][1]["prompt_tokens"] == 5


class _ServerAsgi(_Asgi):
    """As a server may do it: with an answer's last piece its connection is reported as gone, before the send returns."""

    async def send(self, message: dict[str, Any]) -> None:
        await super().send(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            self.disconnect.set()
            await asyncio.sleep(0.05)


async def test_usage_is_handed_on_once_though_the_connection_is_gone_with_the_last_piece() -> None:
    cost = {"prompt_tokens": 40, "completion_tokens": 3, "total_tokens": 43}
    stream = _sse({"choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "stop"}]}, {"choices": [], "usage": cost})
    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream), usage)
    asgi = _ServerAsgi()

    # The request is given up while its last piece is being sent.
    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    assert asgi.body == stream and usage == [("commission-1", cost)]


async def test_a_dsh_that_hangs_up_once_it_has_its_answer_is_counted_once() -> None:
    cost = {"prompt_tokens": 40, "completion_tokens": 3, "total_tokens": 43}
    head = _sse({"choices": [{"index": 0, "delta": {"content": "x"}, "finish_reason": "stop"}]}, {"choices": [], "usage": cost})
    head = head[: -len(b"data: [DONE]\n\n")]
    released = asyncio.Event()

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield head
            try:
                # The endpoint takes its time to end the answer.
                await asyncio.sleep(30)
            finally:
                released.set()
            yield b"data: [DONE]\n\n"

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Slow()), usage)
    asgi = _Asgi()
    serving = asyncio.ensure_future(forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send))
    for _ in range(200):
        if b'"usage"' in asgi.body:
            break
        await asyncio.sleep(0.01)
    assert usage == []

    asgi.disconnect.set()
    await asyncio.wait_for(serving, 5)
    await forwarder.aclose()

    assert released.is_set() and usage == [("commission-1", cost)]


async def test_an_answer_that_is_not_streamed_reports_its_usage_too() -> None:
    answer = {"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 2}}
    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(lambda request: httpx.Response(200, json=answer), usage)
    asgi = _Asgi()
    await forwarder.serve(PROCESS, json.dumps({**_payload(), "stream": False}).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()
    assert json.loads(asgi.body) == answer and usage == [("commission-1", {"prompt_tokens": 9, "completion_tokens": 2})]


@pytest.mark.parametrize("streamed", [True, False])
async def test_an_error_of_the_endpoint_is_passed_back_and_counts_as_no_request(streamed: bool) -> None:
    usage: list[tuple[str, dict[str, Any]]] = []
    # Whatever an error says of usage, nothing was answered.
    refusal = {"error": {"message": "Rate limit exceeded"}, "usage": {"prompt_tokens": 1, "completion_tokens": 0}}
    forwarder = _forwarder(lambda request: httpx.Response(429, json=refusal), usage)
    asgi = _Asgi()
    await forwarder.serve(PROCESS, json.dumps({**_payload(), "stream": streamed}).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()
    assert asgi.status == 429 and json.loads(asgi.body) == refusal and usage == []


async def test_an_endpoint_that_cannot_be_reached_is_a_failed_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(handler, usage)
    asgi = _Asgi()
    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()
    assert asgi.status == 502 and "could not be reached" in json.loads(asgi.body)["error"]["message"] and usage == []
    assert "sk-real" not in asgi.body.decode()


async def test_a_model_name_no_session_holds_is_refused() -> None:
    forwarder = _forwarder(lambda request: httpx.Response(200, content=b""), [])
    forwarder.release(PROCESS, "slot-3")
    asgi = _Asgi()
    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    assert asgi.status == 404
    other = _Asgi()
    await forwarder.serve(PROCESS, b"not json", other.receive, other.send)
    assert other.status == 400
    await forwarder.aclose()


async def test_a_slot_is_one_processes_and_another_process_has_its_own_of_that_name() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), json.loads(request.content)["model"]))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse({"choices": [], "usage": {"prompt_tokens": 1}}))

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = ModelForwarder(lambda session_id, used: usage.append((session_id, used)), report_tool=REPORT, transport=httpx.MockTransport(handler))
    # Every dsh process numbers its slots from zero.
    forwarder.bind("process-a", "slot-0", "commission-a", ModelConfig(model="model-a", base_url="http://a.test/v1", api_key="key-a", auto_extra_body=False))
    forwarder.bind("process-b", "slot-0", "commission-b", ModelConfig(model="model-b", base_url="http://b.test/v1", api_key="key-b", auto_extra_body=False))
    body = json.dumps({**_payload(), "model": "slot-0"}).encode()

    for process in ("process-a", "process-b", "process-a"):
        asgi = _Asgi()
        await forwarder.serve(process, body, asgi.receive, asgi.send)
        assert asgi.status == 200

    # Each one's requests went to its own session's model, and were counted for that session.
    assert seen == [
        ("http://a.test/v1/chat/completions", "model-a"),
        ("http://b.test/v1/chat/completions", "model-b"),
        ("http://a.test/v1/chat/completions", "model-a"),
    ]
    assert [session_id for session_id, _used in usage] == ["commission-a", "commission-b", "commission-a"]
    assert forwarder.route("process-a", "slot-0").session_id == "commission-a"

    # One of them letting its slot go leaves the other's alone; a process is not served under a slot that is not its own.
    forwarder.release("process-b", "slot-0")
    gone, kept, stranger = _Asgi(), _Asgi(), _Asgi()
    await forwarder.serve("process-b", body, gone.receive, gone.send)
    await forwarder.serve("process-a", body, kept.receive, kept.send)
    await forwarder.serve("process-c", body, stranger.receive, stranger.send)
    await forwarder.aclose()
    assert (gone.status, kept.status, stranger.status) == (404, 200, 404)


async def test_the_request_after_a_closing_report_is_answered_here() -> None:
    called: list[httpx.Request] = []
    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(lambda request: called.append(request) or httpx.Response(200, content=b""), usage)
    messages = _report_turn({"status": "failed", "summary": "the disk is full"})

    streamed = _Asgi()
    await forwarder.serve(PROCESS, json.dumps({**_payload(), "messages": messages}).encode(), streamed.receive, streamed.send)
    plain = _Asgi()
    await forwarder.serve(PROCESS, json.dumps({**_payload(), "messages": messages, "stream": False}).encode(), plain.receive, plain.send)
    await forwarder.aclose()

    assert called == [] and usage == []
    events = streamed.events()
    assert streamed.headers["content-type"] == "text/event-stream" and streamed.body.rstrip().endswith(b"data: [DONE]")
    assert events[0]["choices"][0]["delta"] == {"role": "assistant", "content": "Reported."}
    assert events[1]["choices"][0]["finish_reason"] == "stop" and events[0]["model"] == "slot-3"
    answer = json.loads(plain.body)
    assert answer["choices"][0]["message"]["content"] == "Reported." and answer["choices"][0]["finish_reason"] == "stop"


async def test_a_request_dsh_gives_up_is_given_up_at_the_endpoint_too() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200, content=b"")

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(handler, usage)
    asgi = _Asgi()
    serving = asyncio.ensure_future(forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send))
    await asyncio.wait_for(started.wait(), 5)

    asgi.disconnect.set()
    await asyncio.wait_for(serving, 5)
    await forwarder.aclose()

    assert cancelled.is_set() and asgi.status is None and usage == []


async def test_an_answer_cut_off_half_way_ends_the_stream_without_usage() -> None:
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices": [{"index": 0, "delta": {"content": "par"}}]}\n\n'
            raise httpx.ReadError("connection reset")

    usage: list[tuple[str, dict[str, Any]]] = []
    forwarder = _forwarder(lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken()), usage)
    asgi = _Asgi()
    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()
    assert asgi.status == 200 and b"par" in asgi.body and usage == []


# ---------------------------------------------------------------------------
# The window a model endpoint states
# ---------------------------------------------------------------------------


def _learning_forwarder(handler: Any, learned: list[tuple[str, int, str]]) -> ModelForwarder:
    forwarder = ModelForwarder(
        lambda session_id, used: None,
        report_tool=REPORT,
        transport=httpx.MockTransport(handler),
        on_window=lambda session_id, model, tokens, said: learned.append((session_id, model.model, tokens, said)),
    )
    forwarder.bind(PROCESS, "slot-3", "commission-1", ModelConfig(model="real-model", base_url="http://model.test/v1", api_key="sk-real", auto_extra_body=False))
    return forwarder


@pytest.mark.parametrize("status", [400, 413, 422])
async def test_the_window_an_endpoint_states_when_a_request_is_too_long_is_handed_on(status: int) -> None:
    learned: list[tuple[str, str, int, str]] = []
    refusal = {
        "error": {
            "message": "The maximum context length is 32768 tokens. However, you requested 40000 tokens.",
            "code": "context_length_exceeded",
        }
    }
    forwarder = _learning_forwarder(lambda request: httpx.Response(status, json=refusal), learned)
    asgi = _Asgi()

    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    # dsh is told the refusal as it came; the host is told the window.
    assert asgi.status == status and json.loads(asgi.body) == refusal
    # With the model the request went to: under the session's id there may be another by then.
    assert [item[:3] for item in learned] == [("commission-1", "real-model", 32_768)]
    assert "maximum context length is 32768" in learned[0][3]


@pytest.mark.parametrize(
    "status, message",
    [
        # Not about the length of the input.
        (400, "max_tokens: 9000 > 8192 maximum"),
        (429, "The maximum context length is 32768 tokens."),
        (500, "The maximum context length is 32768 tokens."),
        # Too long, and no window stated.
        (400, "context_length_exceeded"),
        (400, ""),
    ],
)
async def test_a_refusal_that_states_no_window_teaches_nothing(status: int, message: str) -> None:
    learned: list[tuple[str, int, str]] = []
    forwarder = _learning_forwarder(lambda request: httpx.Response(status, json={"error": {"message": message}}), learned)
    asgi = _Asgi()

    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    assert asgi.status == status and learned == []


async def test_an_answer_that_is_no_refusal_is_not_read_for_a_window() -> None:
    learned: list[tuple[str, int, str]] = []
    stream = _sse({"choices": [{"index": 0, "delta": {"content": "the maximum context length is 32768 tokens"}}]})
    forwarder = _learning_forwarder(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=stream), learned
    )
    asgi = _Asgi()

    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    assert asgi.status == 200 and learned == []


async def test_a_listener_that_fails_does_not_fail_the_request() -> None:
    def broken(session_id: str, model: Any, tokens: int, said: str) -> None:
        raise RuntimeError("no room")

    refusal = {"error": {"message": "The maximum context length is 8192 tokens."}}
    forwarder = ModelForwarder(
        lambda session_id, used: None,
        report_tool=REPORT,
        transport=httpx.MockTransport(lambda request: httpx.Response(400, json=refusal)),
        on_window=broken,
    )
    forwarder.bind(PROCESS, "slot-3", "commission-1", ModelConfig(model="real-model", base_url="http://model.test/v1", api_key="k", auto_extra_body=False))
    asgi = _Asgi()

    await forwarder.serve(PROCESS, json.dumps(_payload()).encode(), asgi.receive, asgi.send)
    await forwarder.aclose()

    assert asgi.status == 400 and json.loads(asgi.body) == refusal


def test_the_statuses_a_request_that_is_too_long_is_refused_with_are_those_of_the_model_client() -> None:
    from nekoclaw.dsh import forward
    from nekoclaw.runtime import model

    assert forward._OVERFLOW_STATUSES == model.OVERFLOW_STATUSES
