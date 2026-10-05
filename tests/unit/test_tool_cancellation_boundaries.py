"""Provider cancellation boundaries: no late output, side effects or orphan calls."""
import asyncio
from types import SimpleNamespace

import pytest
import main_logic.omni_offline_client._tools as tooling

from main_logic.omni_offline_client import OmniOfflineClient
from main_logic.tool_calling import ToolDefinition, ToolResult
from utils.llm_client import LLMStreamChunk
from tests.unit.test_tool_calling import (
    _GenaiChunk, _GenaiFunctionCall, _GenaiPart, _bare_genai_client,
    _init_bare, _ofc_genai,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("failure", ["serialization", "construction", "handler"])
@pytest.mark.parametrize("fail_at", [1, 2])
async def test_round_exception_transaction(provider, failure, fail_at, monkeypatch):
    messages = [{"role": "user", "content": "lookup"}]
    original = messages[0]
    concurrent = {"role": "user", "content": "concurrent"}
    executed = []

    def fail_serialization():
        raise ValueError("serialization failed")

    async def handler(call):
        executed.append(call.call_id)
        if len(executed) == fail_at:
            messages.append(concurrent)
            if failure == "serialization":
                return SimpleNamespace(output_as_json_string=fail_serialization)
            if failure == "handler":
                raise ValueError("ordinary handler error")
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = make_client(provider, monkeypatch, handler, cap=1)
    module = _ofc_genai if provider == "gemini" else tooling
    real_call = module.ToolCall
    constructed = []

    def construct(**kwargs):
        constructed.append(kwargs)
        if failure == "construction" and len(constructed) == fail_at:
            messages.append(concurrent)
            raise ValueError("construction failed")
        return real_call(**kwargs)

    monkeypatch.setattr(module, "ToolCall", construct)
    if provider == "gemini":
        chunks = [_GenaiChunk([
            _GenaiPart(function_call=_GenaiFunctionCall("lookup", id_=str(i)))
            for i in range(3)])]
        stream_method = client._astream_genai_with_tools
    else:
        chunks = [LLMStreamChunk(content="", finish_reason="tool_calls", tool_call_deltas=[
            {"index": i, "id": str(i), "type": "function",
             "function": {"name": "lookup", "arguments": "{}"}}
            for i in range(3)])]
        stream_method = client._astream_openai_with_tools
    install_stream(client, provider, chunks)
    generation = client._begin_response_generation()

    async def consume():
        return [chunk async for chunk in stream_method(
            messages, _response_generation=generation)]

    if failure == "handler":
        await consume()
        assert len(executed) == 3
        replies = [m for m in messages if m.get("role") == "tool"]
        assert len(replies) == 3
        assert "ordinary handler error" in replies[fail_at - 1]["content"]
    else:
        with pytest.raises(ValueError, match=failure + " failed"):
            await consume()
        # Calls are built before the round, so a construction failure stops
        # the turn before any tool ran. A serialization failure lands inside
        # the round: calls recorded before it stay, paired and contiguous;
        # with none recorded the round is gone. The concurrent append stays.
        assert len(executed) == (0 if failure == "construction" else fail_at)
        kept = 0 if failure == "construction" else fail_at - 1
        assert messages[0] is original
        assert messages[-1] is concurrent
        if kept:
            assistant, *replies = messages[1:-1]
            assert [c["id"] for c in assistant["tool_calls"]] == ["0"]
            assert [r["tool_call_id"] for r in replies] == ["0"]
        else:
            assert len(messages) == 2


@pytest.mark.asyncio
async def test_gemini_reasoning_callback_cancellation(monkeypatch):
    client = make_client("gemini", monkeypatch, None)
    client._notify_reasoning_active = client.cancel_response
    thought = _GenaiPart(text="private reasoning")
    thought.thought = True
    install_stream(client, "gemini", [_GenaiChunk([
        thought, _GenaiPart(text="late visible text")])])
    generation = client._begin_response_generation()
    result = [chunk async for chunk in client._astream_genai_with_tools(
        [{"role": "user", "content": "hello"}], _response_generation=generation)]
    # Only the empty "answered" chunk callers publish on; no late text.
    assert [chunk.content for chunk in result] == [""]


def make_client(provider, monkeypatch, handler, cap=2):
    if provider == "gemini":
        monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
        client, _ = _bare_genai_client([[]], handler, cap=cap)
    else:
        client = _init_bare(OmniOfflineClient.__new__(OmniOfflineClient))
    client._use_genai_sdk = provider == "gemini"
    client._genai_tools_unsupported = False
    client._tool_definitions = [ToolDefinition(name="lookup", description="lookup")]
    client.has_tools = lambda: True
    client.on_tool_call = handler
    client.max_tool_iterations = cap
    return client


def install_stream(client, provider, chunks, before=None):
    requests = []

    async def stream():
        if before:
            await before()
        for chunk in chunks:
            yield chunk

    if provider == "gemini":
        async def generate(**kwargs):
            requests.append(kwargs)
            return stream()
        client._genai_client.aio.models.generate_content_stream = generate
    else:
        def generate(*args, **kwargs):
            requests.append(kwargs)
            return stream()
        client.llm = SimpleNamespace(astream=generate, max_completion_tokens=100)
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("cancel_at", [1, 2, 3])
@pytest.mark.parametrize("task_cancel", [False, True])
async def test_cancelled_batch_keeps_executed_calls_by_identity(
    provider, cancel_at, task_cancel, monkeypatch,
):
    """A handler that returned already had its side effects: its record stays.

    The round is trimmed to the calls with results, kept contiguous ahead of
    whatever another turn appended meanwhile, and dropped only when no call
    finished. Equal-valued copies of the round must survive untouched, so
    every match is by identity.
    """
    messages = [{"role": "user", "content": "lookup"}]
    original = messages[0]
    concurrent = []
    executed = []

    async def handler(call):
        executed.append(call.call_id)
        if len(executed) == cancel_at:
            concurrent.extend(dict(message) for message in messages[1:])
            concurrent.append({"role": "user", "content": "new turn"})
            messages.extend(concurrent)
            if task_cancel:
                raise asyncio.CancelledError
            await client.cancel_response()
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client = make_client(provider, monkeypatch, handler)
    if provider == "gemini":
        chunks = [_GenaiChunk([
            _GenaiPart(function_call=_GenaiFunctionCall("lookup", id_=str(i)))
            for i in range(3)
        ])]
    else:
        chunks = [LLMStreamChunk(content="", finish_reason="tool_calls", tool_call_deltas=[
            {"index": i, "id": str(i), "type": "function",
             "function": {"name": "lookup", "arguments": "{}"}}
            for i in range(3)
        ])]
    requests = install_stream(client, provider, chunks)
    generation = client._begin_response_generation()
    yielded = []

    async def consume():
        async for chunk in client._astream_with_tools(
            messages, _response_generation=generation,
        ):
            yielded.append(chunk)

    if task_cancel:
        with pytest.raises(asyncio.CancelledError):
            await consume()
    else:
        await consume()
    kept = cancel_at - task_cancel
    assert len(executed) == cancel_at
    assert len(requests) == 1, "no provider request after the cancellation"
    assert messages[0] is original
    assert all(a is b for a, b in zip(messages[-len(concurrent):], concurrent))
    own = messages[1:-len(concurrent)]
    if kept:
        assistant, *replies = own
        ids = [str(i) for i in range(kept)]
        assert [c["id"] for c in assistant["tool_calls"]] == ids
        assert [r["tool_call_id"] for r in replies] == ids
    else:
        assert own == []
    # The caller learns the pre-tool text is persisted only when it is.
    persisted = [c for c in yielded if getattr(c, "tool_round_persisted", False)]
    assert len(persisted) == int(bool(kept) and not task_cancel)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("cap", [0, 2])
async def test_an_answered_request_publishes_but_yields_no_late_content(
    provider, cap, monkeypatch,
):
    """Cancelled while the request was in flight, answered afterwards: the
    provider received what the request carried, so its frames are published
    and callers get one empty chunk to publish theirs. No content leaks."""
    client = make_client(provider, monkeypatch, None, cap=cap)
    publications = []
    client._publish_pending_tool_frames = lambda *a, **kw: publications.append(kw)
    chunks = ([_GenaiChunk([_GenaiPart(text="late")])] if provider == "gemini"
              else [LLMStreamChunk(content="late")])
    requests = install_stream(client, provider, chunks, client.cancel_response)
    generation = client._begin_response_generation()
    result = [chunk async for chunk in client._astream_with_tools(
        [{"role": "user", "content": "hello"}], _response_generation=generation)]
    assert [chunk.content for chunk in result] == [""]
    assert len(publications) == 1
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [0, 2])
async def test_gemini_cancel_between_parts(cap, monkeypatch):
    client = make_client("gemini", monkeypatch, None, cap=cap)
    install_stream(client, "gemini", [_GenaiChunk([
        _GenaiPart(text="first"), _GenaiPart(text="late")])])
    generation = client._begin_response_generation()
    result = []
    async for chunk in client._astream_with_tools(
        [{"role": "user", "content": "hello"}], _response_generation=generation
    ):
        result.append(chunk.content)
        await client.cancel_response()
    assert result == ["first"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cap", [0, 2])
async def test_gemini_cancel_while_establishing_stream(cap, monkeypatch):
    """With tools (cap=2) the SDK sends the request while the stream is being
    established: a cancellation then is handled on the first chunk, which
    publishes what the request carried and yields only the answered chunk.
    The forced final call (cap=0) has no tools, is lazy, and returns before
    anything is sent."""
    client = make_client("gemini", monkeypatch, None, cap=cap)
    publications = []
    client._publish_pending_tool_frames = lambda *a, **kw: publications.append(kw)
    consumed = []

    async def stream():
        consumed.append(True)
        yield _GenaiChunk([_GenaiPart(text="late")])

    async def generate(**kwargs):
        await client.cancel_response()
        return stream()

    client._genai_client.aio.models.generate_content_stream = generate
    generation = client._begin_response_generation()
    result = [chunk async for chunk in client._astream_with_tools(
        [{"role": "user", "content": "hello"}], _response_generation=generation)]
    if cap:
        assert [chunk.content for chunk in result] == [""]
        assert getattr(result[0], "_answered_ack", False)
        assert consumed == [True]
        assert len(publications) == 1
    else:
        assert result == []
        assert consumed == []
        assert publications == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "gemini"])
async def test_round_start_cancellation_prevents_tool_execution(provider, monkeypatch):
    executed = []

    async def handler(call):
        executed.append(call)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client = make_client(provider, monkeypatch, handler, cap=1)
    client.on_tool_round_start = client.cancel_response
    if provider == "gemini":
        chunks = [_GenaiChunk([_GenaiPart(
            function_call=_GenaiFunctionCall("lookup", id_="c1"))])]
    else:
        chunks = [LLMStreamChunk(content="", finish_reason="tool_calls", tool_call_deltas=[
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "lookup", "arguments": "{}"}}])]
    requests = install_stream(client, provider, chunks)
    messages = [{"role": "user", "content": "lookup"}]
    generation = client._begin_response_generation()
    _ = [chunk async for chunk in client._astream_with_tools(
        messages, _response_generation=generation)]
    assert executed == []
    assert len(messages) == 1
    assert len(requests) == 1
