# -*- coding: utf-8 -*-
"""End-to-end smoke for the unified tool-calling pipeline.

Covers:
  1. ``ToolRegistry`` local execution + remote dispatcher fallback.
  2. ``ChatOpenAI.collect_tool_calls`` aggregating delta fragments.
  3. ``OmniOfflineClient._astream_openai_with_tools`` running a single
     tool-call → tool-result → final-text round trip with a mocked
     ``ChatOpenAI.astream`` (no real LLM).
  4. ``OmniRealtimeClient`` wire-format helpers (tools_for_*).

No network. No LLM SDKs called. Pure logic verification — designed to
catch contract regressions in the tool plumbing.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path

import httpx
import pytest

import main_logic.omni_offline_client as _ofc_package
import main_logic.omni_offline_client._genai_support as _ofc_genai
import main_logic.omni_offline_client._streaming as _ofc_streaming

# Ensure the project root is importable when pytest is invoked from
# anywhere (mirrors other tests/unit/* files).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_offline_package_state_reexports_follow_canonical_owner(monkeypatch):
    sentinel_genai = object()
    sentinel_types = object()

    monkeypatch.setattr(_ofc_package, "_GENAI_AVAILABLE", False)
    monkeypatch.setattr(_ofc_package, "_genai", sentinel_genai)
    monkeypatch.setattr(_ofc_genai, "_genai_types", sentinel_types)

    assert _ofc_genai._GENAI_AVAILABLE is False
    assert _ofc_genai._genai is sentinel_genai
    assert _ofc_package._genai_types is sentinel_types


def _init_bare(client):
    """Populate the ``__init__``-guaranteed baseline attributes that
    ``stream_text`` / ``switch_model`` read but that these ``__new__``-bypassing
    harnesses don't otherwise establish (proactive-vision staging slot + vision
    provider type). Real clients always have these set in ``__init__``; the tests
    build a minimal object, so mirror that minimal state here."""
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    if not hasattr(client, "vision_provider_type"):
        client.vision_provider_type = None
    # stream_text reads self.llm.max_completion_tokens before reaching astream (focus overrides /
    # summary budget). Real clients always have self.llm; if a test installs a fake llm in the harness,
    # use it; otherwise provide a placeholder with max_completion_tokens=None (= unlimited budget, matching
    # the old behavior before summary/focus). Do not set it to None: stream_text treats
    # ``self.llm is None`` as a closed client and breaks early.
    if not hasattr(client, "llm"):
        from types import SimpleNamespace
        client.llm = SimpleNamespace(max_completion_tokens=None)
    return client



# ---------------------------------------------------------------------------
# 1. ToolRegistry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_local_handler_runs():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    calls = []

    async def echo_handler(args):
        calls.append(args)
        return {"echoed": args}

    reg.register(ToolDefinition(name="echo", description="echo", handler=echo_handler))
    result = await reg.execute(ToolCall(name="echo", arguments={"x": 1}, call_id="c1"))
    assert result.is_error is False
    assert result.output == {"echoed": {"x": 1}}
    assert calls == [{"x": 1}]


@pytest.mark.asyncio
async def test_registry_local_handler_envelope_extracts_images():
    """Local handlers share the remote ``{output, images}`` envelope.

    Without normalization, pixels stay inside ``output`` and never reach
    ``_route_tool_images`` — the model gets base64 text instead of a picture.
    """
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    jpeg_b64 = (
        "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////"
        "////////////////////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
        "AAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AN//Z"
    )

    async def screenshot_handler(_args):
        return {
            "output": {"ok": True},
            "images": [{
                "data_b64": jpeg_b64,
                "mime": "image/jpeg",
                "vision_prompt": "minimap",
            }],
        }

    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="shot", description="shot", handler=screenshot_handler))
    result = await reg.execute(ToolCall(name="shot", arguments={}, call_id="c1"))

    assert result.is_error is False
    assert result.output == {"ok": True}
    assert "images" not in result.output
    assert len(result.images) == 1
    assert result.images[0].data_b64 == jpeg_b64
    assert result.images[0].vision_prompt == "minimap"


@pytest.mark.asyncio
async def test_registry_local_handler_keeps_data_dicts_that_contain_output():
    """``output`` alone is not an envelope marker — same rule as the remote
    plugin callback. Unwrapping would drop sibling fields the model needs."""
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    async def ready_handler(_args):
        return {"output": "ready", "duration": 12}

    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="ready", description="ready", handler=ready_handler))
    result = await reg.execute(ToolCall(name="ready", arguments={}, call_id="c1"))

    assert result.is_error is False
    assert result.output == {"output": "ready", "duration": 12}
    assert result.images == []


@pytest.mark.asyncio
async def test_registry_local_handler_keeps_business_images_as_plain_data():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry

    payload = {
        "query": "cats",
        "images": [{"url": "https://example.test/cat.jpg"}],
    }

    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="search", description="search", handler=lambda _args: payload))
    result = await reg.execute(ToolCall(name="search", arguments={}, call_id="c1"))

    assert result.is_error is False
    assert result.output == payload
    assert result.images == []


@pytest.mark.asyncio
async def test_registry_unknown_tool_returns_error_not_raise():
    from main_logic.tool_calling import ToolCall, ToolRegistry

    reg = ToolRegistry()
    result = await reg.execute(ToolCall(name="missing", arguments={}, call_id="c1"))
    assert result.is_error is True
    assert "not registered" in result.error_message


@pytest.mark.asyncio
async def test_registry_remote_dispatcher_invoked_when_no_handler():
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolRegistry, ToolResult

    seen_metadata = {}

    async def dispatcher(call, metadata):
        seen_metadata.update(metadata)
        return ToolResult(call_id=call.call_id, name=call.name, output={"remote": True})

    reg = ToolRegistry(remote_dispatcher=dispatcher)
    reg.register(ToolDefinition(
        name="r",
        description="remote",
        handler=None,
        metadata={"source": "plugin:foo", "callback_url": "http://x/y"},
    ))
    result = await reg.execute(ToolCall(name="r", arguments={}, call_id="c"))
    assert result.output == {"remote": True}
    assert seen_metadata["source"] == "plugin:foo"
    assert seen_metadata["callback_url"] == "http://x/y"


def test_registry_clear_by_source():
    from main_logic.tool_calling import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(name="a", description="", handler=lambda _: 1, metadata={"source": "plugin:foo"}))
    reg.register(ToolDefinition(name="b", description="", handler=lambda _: 1, metadata={"source": "plugin:bar"}))
    reg.register(ToolDefinition(name="c", description="", handler=lambda _: 1, metadata={"source": "plugin:foo"}))
    assert reg.clear(source="plugin:foo") == 2
    assert sorted(reg.names()) == ["b"]


def test_registry_specs_for_dialect_shapes():
    from main_logic.tool_calling import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(ToolDefinition(
        name="weather",
        description="city weather",
        parameters={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        handler=lambda _: 0,
    ))
    chat = reg.specs_for(dialect="openai_chat")[0]
    rt = reg.specs_for(dialect="openai_realtime")[0]
    gem = reg.specs_for(dialect="gemini")[0]
    # OpenAI Chat Completions: {type, function:{name,...}}
    assert chat["type"] == "function" and chat["function"]["name"] == "weather"
    # OpenAI Realtime / GLM: flat
    assert rt["type"] == "function" and rt["name"] == "weather"
    # Gemini function_declaration: bare name/desc/parameters
    assert "type" not in gem and gem["name"] == "weather"


# ---------------------------------------------------------------------------
# 2. ChatOpenAI.collect_tool_calls
# ---------------------------------------------------------------------------

def test_collect_tool_calls_drops_empty_name_fragments():
    """The SDK occasionally streams incomplete tool_calls without a name; discard them to avoid polluting
    tool_calls history and causing server schema rejection on the next round.

    Regression coverage: CodeRabbit feedback on PR #1035."""
    from utils.llm_client import ChatOpenAI

    deltas_per_chunk = [
        # Call 0: complete.
        [{"index": 0, "id": "ok", "function": {"name": "good_tool", "arguments": "{}"}}],
        # Call 1: missing name (and id) -- must be discarded.
        [{"index": 1, "function": {"arguments": "{}"}}],
        # Call 2: only arguments arrive; name remains empty -- must be discarded.
        [{"index": 2, "function": {"arguments": "{\"x\":1}"}}],
    ]
    out = ChatOpenAI.collect_tool_calls(deltas_per_chunk)
    assert len(out) == 1
    assert out[0].name == "good_tool"


def test_collect_tool_calls_merges_fragments():
    from utils.llm_client import ChatOpenAI

    deltas_per_chunk = [
        # call 0: id+name in first chunk
        [{"index": 0, "id": "call_x", "type": "function",
          "function": {"name": "weather", "arguments": '{"ci'}}],
        # call 0 args continued; call 1 starts
        [
            {"index": 0, "function": {"name": "", "arguments": 'ty":"'}},
            {"index": 1, "id": "call_y", "function": {"name": "now", "arguments": ""}},
        ],
        # both finish
        [
            {"index": 0, "function": {"name": "", "arguments": 'Tokyo"}'}},
            {"index": 1, "function": {"name": "", "arguments": "{}"}},
        ],
    ]
    out = ChatOpenAI.collect_tool_calls(deltas_per_chunk)
    assert len(out) == 2
    assert out[0].id == "call_x" and out[0].name == "weather"
    assert json.loads(out[0].arguments) == {"city": "Tokyo"}
    assert out[1].id == "call_y" and out[1].name == "now"
    assert out[1].arguments == "{}"


# ---------------------------------------------------------------------------
# 3. OmniOfflineClient OpenAI-compat tool loop end-to-end
# ---------------------------------------------------------------------------


class _FakeAsyncStream:
    """Mimics ``ChatOpenAI.astream`` — yields ``LLMStreamChunk`` objects
    from a scripted list. One ``_FakeAsyncStream`` per call invocation."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self._chunks:
            yield c


class _FakeLLM:
    """Drop-in for ``self.llm`` inside ``OmniOfflineClient``. ``astream``
    pops one batch of chunks per invocation; tracks every call's args.
    """

    def __init__(self, scripted_chunks_per_call, max_completion_tokens=100):
        self._scripted = list(scripted_chunks_per_call)
        self.calls = []  # list of (messages, overrides)
        self.max_completion_tokens = max_completion_tokens

    def astream(self, messages, **overrides):
        self.calls.append((messages, overrides))
        if not self._scripted:
            raise RuntimeError("FakeLLM ran out of scripted responses")
        return _FakeAsyncStream(self._scripted.pop(0))

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_offline_openai_path_runs_tool_then_text():
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    # Tool that records invocations.
    seen_args = []

    async def get_weather(args):
        seen_args.append(args)
        return {"temp_c": 22, "city": args.get("city")}

    tool_def = ToolDefinition(
        name="get_weather",
        description="weather lookup",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        handler=get_weather,
    )

    # Two scripted LLM responses:
    # Call 1: model emits a tool_call (finish_reason="tool_calls")
    # Call 2: model emits final text
    chunks_call_1 = [
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0,
                "id": "call_w",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
            }],
            finish_reason=None,
        ),
        LLMStreamChunk(content="", tool_call_deltas=None, finish_reason="tool_calls"),
    ]
    chunks_call_2 = [
        LLMStreamChunk(content="It's 22°C in Paris.", finish_reason="stop"),
    ]

    fake_llm = _FakeLLM([chunks_call_1, chunks_call_2])

    # Hand-build the client without going through __init__'s ChatOpenAI
    # construction. We bypass __init__ entirely and patch the minimum
    # state needed by _astream_openai_with_tools.
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False  # force OpenAI-compat
    client._genai_tools_unsupported = False

    # bridge handler — the registry isn't exercised here, just the
    # client→handler contract.
    async def handler(call: ToolCall) -> ToolResult:
        result_value = await get_weather(call.arguments)
        return ToolResult(call_id=call.call_id, name=call.name, output=result_value)

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "what's the weather in Paris?"}]
    out_chunks = []
    async for ch in client._astream_with_tools(messages):
        out_chunks.append(ch)

    # Two LLM calls, second one yielded the text.
    assert len(fake_llm.calls) == 2
    text_emitted = "".join(ch.content for ch in out_chunks)
    assert "Paris" in text_emitted
    assert seen_args == [{"city": "Paris"}]

    # History after the loop must include the assistant tool_calls turn
    # and the tool result message before the final assistant text.
    roles = [m.get("role") if isinstance(m, dict) else getattr(m, "role", None) for m in messages]
    # original user, assistant w/ tool_calls, tool, (no final-text appended
    # because _astream_with_tools yields the text but doesn't persist it —
    # that's stream_text's job).
    assert roles[0] == "user"
    assert roles[1] == "assistant"
    assert roles[2] == "tool"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(messages[2]["content"])["temp_c"] == 22
    # Tool messages must include name (the Gemini conversion uses it for FunctionResponse.name).
    assert messages[2]["name"] == "get_weather"


@pytest.mark.asyncio
async def test_cancelled_tool_round_does_not_start_another_provider_call():
    """Cancellation during a tool callback must retire the whole tool loop.

    The provider stream that produced the tool call has already happened, but
    the next loop iteration must not issue another provider request (including
    the forced-finalize fallback).
    """
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolDefinition, ToolResult
    from utils.llm_client import LLMStreamChunk

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._openai_tools_unsupported = False
    client._openai_tools_unsupported_with_images = False
    client.max_tool_iterations = 3

    async def cancel_in_tool(_call):
        await client.cancel_response()
        return ToolResult(call_id="call_1", name="lookup", output={"ok": True})

    client.on_tool_call = cancel_in_tool
    client._tool_definitions = [ToolDefinition(
        name="lookup",
        description="lookup",
        parameters={"type": "object", "properties": {}},
        handler=None,
    )]
    client.llm = _FakeLLM([[
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "lookup", "arguments": "{}"},
            }],
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]])

    generation = client._begin_response_generation()
    messages = [{"role": "user", "content": "look it up"}]
    async for _chunk in client._astream_with_tools(
        messages,
        _response_generation=generation,
    ):
        pass

    assert len(client.llm.calls) == 1
    assert client._response_generation_is_active(generation) is False


@pytest.mark.asyncio
async def test_cancelled_stream_does_not_emit_response_done_boundary():
    """A cancelled user turn must not send the TTS/turn-end callback."""
    from unittest.mock import AsyncMock

    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import HumanMessage, LLMStreamChunk, SystemMessage

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "Test"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 300
    client.max_response_rerolls = 0
    client.enable_response_guard = False
    client.vision_model = ""
    client.model = "m"
    client.on_text_delta = AsyncMock()
    client.on_input_transcript = None
    client.on_response_done = AsyncMock()
    client.on_response_discarded = None
    client.on_status_message = None
    client.on_repetition_detected = None

    async def _cancelled_stream(_messages, **_overrides):
        yield LLMStreamChunk(content="已经说出的半句")
        await client.cancel_response()
        yield LLMStreamChunk(content="不应继续送出的尾巴")

    client._astream_visible_with_tools = _cancelled_stream

    await client.stream_text("打断这轮")

    assert client.on_text_delta.await_count == 1
    client.on_response_done.assert_not_awaited()
    assert client._active_response_generation is None
    assert client._is_responding is False


@pytest.mark.asyncio
async def test_offline_openai_path_filters_pretool_leak_before_history():
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
    from utils.llm_tool_leak_filter import ToolLeakFilter

    async def recall_handler(args):
        return {"hits": []}

    tool_def = ToolDefinition(
        name="recall_memory",
        description="recall",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        handler=recall_handler,
    )
    leak = 'recall_memory</name><parameter name="query">secret</parameter></function></seed:tool_call>'
    chunks_call_1 = [
        LLMStreamChunk(content="让我查一下。"),
        LLMStreamChunk(content=leak),
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0,
                "id": "call_m",
                "type": "function",
                "function": {"name": "recall_memory", "arguments": '{"query":"x"}'},
            }],
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_call_2 = [LLMStreamChunk(content="没有查到。", finish_reason="stop")]
    fake_llm = _FakeLLM([chunks_call_1, chunks_call_2])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.base_url = "free"
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output=await recall_handler(call.arguments))

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "以前聊过什么？"}]
    out_chunks = []
    leak_filter = ToolLeakFilter(tool_names={"recall_memory"})
    async for ch in client._astream_with_tools(
        messages,
        _tool_leak_filter=leak_filter,
        _tool_leak_provider="free",
    ):
        out_chunks.append(ch)
    tail, _event = leak_filter.finalize()
    if tail:
        out_chunks.append(LLMStreamChunk(content=tail))

    visible = "".join(ch.content for ch in out_chunks)
    assert visible == "让我查一下。没有查到。"
    assistant_with_tool = next(
        m for m in messages if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_with_tool["content"] == "让我查一下。"
    assert "recall_memory</name>" not in assistant_with_tool["content"]
    assert "secret" not in assistant_with_tool["content"]


@pytest.mark.asyncio
async def test_offline_openai_path_emits_finalized_pretool_tail():
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
    from utils.llm_tool_leak_filter import ToolLeakFilter

    async def recall_handler(args):
        return {"hits": []}

    tool_def = ToolDefinition(
        name="recall_memory",
        description="recall",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        handler=recall_handler,
    )
    chunks_call_1 = [
        LLMStreamChunk(content="Let"),
        LLMStreamChunk(content="s"),
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0,
                "id": "call_m",
                "type": "function",
                "function": {"name": "recall_memory", "arguments": '{"query":"x"}'},
            }],
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_call_2 = [LLMStreamChunk(content=" continue.", finish_reason="stop")]
    fake_llm = _FakeLLM([chunks_call_1, chunks_call_2])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.base_url = "free"
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output=await recall_handler(call.arguments))

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "以前聊过什么？"}]
    out_chunks = []
    leak_filter = ToolLeakFilter(tool_names={"recall_memory"})
    async for ch in client._astream_with_tools(
        messages,
        _tool_leak_filter=leak_filter,
        _tool_leak_provider="free",
    ):
        out_chunks.append(ch)

    visible = "".join(ch.content for ch in out_chunks)
    assert visible == "Lets continue."
    assistant_with_tool = next(
        m for m in messages if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_with_tool["content"] == "Lets"


@pytest.mark.asyncio
async def test_visible_tool_leak_filter_flushes_pending_text_before_stream_error(monkeypatch):
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient

    async def _astream_with_tools_then_raise(self, messages, **overrides):
        yield LLMStreamChunk(content="Let")
        yield LLMStreamChunk(content="s")
        raise RuntimeError("stream broke")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_with_tools_then_raise)

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.base_url = "free"
    client.model = "fake"
    client._tool_definitions = []

    out_chunks = []
    with pytest.raises(RuntimeError, match="stream broke"):
        async for chunk in client._astream_visible_with_tools([{"role": "user", "content": "hi"}]):
            out_chunks.append(chunk)

    assert "".join(chunk.content for chunk in out_chunks) == "Lets"
    assert getattr(out_chunks[-1], "_tool_leak_filtered", False) is True


@pytest.mark.asyncio
async def test_offline_openai_path_persists_reasoning_content_with_tool_call():
    """Thinking models (DeepSeek-R / Qwen / GLM thinking and other OpenAI-compatible endpoints)
    require the assistant message initiating tool_calls in multi-round tool calling to include this round's streamed
    ``reasoning_content`` unchanged; otherwise the next round returns 400 \"The `reasoning_content` in the
    thinking mode must be passed back to the API.\" (reproduced when triggering memory_recall).

    This test scripts a tool-call round with reasoning_content and asserts that the assistant tool_calls
    message written back to history preserves reasoning_content."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    tool_def = ToolDefinition(
        name="memory_recall",
        description="recall",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=lambda args: {"hits": []},
    )

    # First round: stream reasoning, then tool_call, with finish_reason=tool_calls.
    chunks_call_1 = [
        LLMStreamChunk(content="", reasoning_content="用户问起以前的事，"),
        LLMStreamChunk(content="", reasoning_content="我得查一下记忆。"),
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0,
                "id": "call_m",
                "type": "function",
                "function": {"name": "memory_recall", "arguments": '{"q":"生日"}'},
            }],
            finish_reason=None,
        ),
        LLMStreamChunk(content="", tool_call_deltas=None, finish_reason="tool_calls"),
    ]
    chunks_call_2 = [
        LLMStreamChunk(content="我记得是夏天。", finish_reason="stop"),
    ]

    fake_llm = _FakeLLM([chunks_call_1, chunks_call_2])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"hits": []})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "我生日是什么时候？"}]
    out_chunks = []
    async for ch in client._astream_with_tools(messages):
        out_chunks.append(ch)

    # Pure reasoning chunks (empty content, no tool/finish/usage) must not be forwarded downstream,
    # or stream_text mistakes the first reasoning chunk for the TTFT first token (Codex P2).
    assert not any(
        getattr(ch, "reasoning_content", None)
        and not getattr(ch, "content", None)
        and not ch.tool_call_deltas
        and not ch.finish_reason
        and not ch.usage_metadata
        for ch in out_chunks
    ), "reasoning-only chunk 不应 surface 给通用流式消费者（TTFT 会被拉低）"

    assistant_with_tool_calls = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_with_tool_calls.get("reasoning_content") == "用户问起以前的事，我得查一下记忆。", (
        "thinking 模型发起 tool_calls 那轮的 reasoning_content 必须累积并回填进历史，"
        "否则部分 provider 下一轮报 400"
    )
    # The second request must include history with reasoning_content exactly matching the accumulated
    # reasoning from this round (check the value, not just existence, to catch truncation / rewriting along the way).
    second_call_messages = fake_llm.calls[1][0]
    replayed = next(
        m.get("reasoning_content")
        for m in second_call_messages
        if isinstance(m, dict) and m.get("reasoning_content")
    )
    assert replayed == "用户问起以前的事，我得查一下记忆。"


@pytest.mark.asyncio
async def test_offline_openai_path_pulses_thinking_on_reasoning_chunk():
    """A reasoning-only chunk (thinking model) must fire ``on_thinking_active``
    exactly ONCE per stream — even on a non-Focus turn — so the chat thinking-dots
    bubble reflects the real reasoning stream, decoupled from the Focus decision.
    The reasoning TEXT itself is still filtered out (not yielded downstream)."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient

    chunks = [
        LLMStreamChunk(content="", reasoning_content="先想想……"),
        LLMStreamChunk(content="", reasoning_content="再想想……"),
        LLMStreamChunk(content="答案是 42。", finish_reason="stop"),
    ]
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = _FakeLLM([chunks])
    client._tool_definitions = []
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client.on_tool_call = None
    # _astream_with_tools doesn't open a stream scope (stream_text does) — seed
    # the pulse-ownership state here so a fresh stream starts un-pulsed.
    client._reasoning_stream_seq = 0
    client._reasoning_active_pulse_seq = None

    pulses = []

    async def on_thinking_active(active):
        pulses.append(active)

    client.on_thinking_active = on_thinking_active

    out = []
    async for ch in client._astream_with_tools([{"role": "user", "content": "hi"}]):
        out.append(ch)

    assert pulses == [True], "两个 reasoning chunk 只应 pulse 一次 True（per-stream 幂等）"
    # Reasoning text remains filtered and is not yielded downstream.
    assert not any(
        getattr(ch, "reasoning_content", None) and not getattr(ch, "content", None)
        for ch in out
    )
    assert any(getattr(ch, "content", None) == "答案是 42。" for ch in out)


@pytest.mark.asyncio
async def test_offline_openai_path_no_thinking_pulse_without_reasoning():
    """A non-thinking endpoint (no reasoning_content on any delta) must NOT fire
    on_thinking_active, so a regular turn never flashes a spurious thinking bubble."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient

    chunks = [LLMStreamChunk(content="直接答。", finish_reason="stop")]
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = _FakeLLM([chunks])
    client._tool_definitions = []
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client.on_tool_call = None
    client._reasoning_active_pulse_seq = None

    pulses = []

    async def on_thinking_active(active):
        pulses.append(active)

    client.on_thinking_active = on_thinking_active

    async for _ in client._astream_with_tools([{"role": "user", "content": "hi"}]):
        pass

    assert pulses == [], "无 reasoning chunk 时不应 pulse 思考气泡"


@pytest.mark.asyncio
async def test_notify_reasoning_done_clears_only_after_a_pulse():
    """``_notify_reasoning_done`` is the symmetric clear bracketing prompt_ephemeral:
    it pushes on_thinking_active(False) IFF this stream actually pulsed True, and is
    idempotent (a second call is a no-op). Guards the stuck-bubble case where a
    proactive turn reasons but commits no visible text."""
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._reasoning_active_pulse_seq = None
    pulses = []

    async def on_thinking_active(active):
        pulses.append(active)

    client.on_thinking_active = on_thinking_active

    # Never pulsed → clear is a no-op.
    await client._notify_reasoning_done()
    assert pulses == []

    # Pulse True, then the end-of-stream clear emits exactly one False.
    await client._notify_reasoning_active()
    await client._notify_reasoning_done()
    await client._notify_reasoning_done()  # idempotent
    assert pulses == [True, False]


@pytest.mark.asyncio
async def test_notify_reasoning_done_owner_seq_guards_cross_stream_clear():
    """A backgrounded stream's clear must NOT fire once a newer stream has
    re-pulsed under a fresher ownership seq — otherwise a preempted proactive
    turn's finally would clear the bubble a foreground user turn is still
    reasoning under (Codex P2)."""
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._reasoning_stream_seq = 0
    client._reasoning_active_pulse_seq = None
    pulses = []

    async def on_thinking_active(active):
        pulses.append(active)

    client.on_thinking_active = on_thinking_active

    s1 = client._begin_reasoning_stream()          # proactive scope
    await client._notify_reasoning_active()        # proactive pulses
    s2 = client._begin_reasoning_stream()          # user stream_text interleaves
    await client._notify_reasoning_active()        # user pulses under fresher seq
    assert pulses == [True, True]

    # Proactive finally with its OWN (older) seq must be suppressed.
    await client._notify_reasoning_done(s1)
    assert pulses == [True, True], "旧流的 finally 不能清掉新流的气泡"

    # The owning (newer) stream's clear still works.
    await client._notify_reasoning_done(s2)
    assert pulses == [True, True, False]


@pytest.mark.asyncio
async def test_notify_reasoning_done_clears_own_pulse_after_preemption_without_new_pulse():
    """A proactive turn that pulsed, then got preempted by a user stream that only
    STARTED (bumped seq) without pulsing yet, must still clear ITS OWN bubble in
    finally. The ownership seq is NOT reset by _begin_reasoning_stream, so the
    older owner stays valid — a shared per-stream boolean would have been reset
    here, leaking the bubble (Codex P2)."""
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._reasoning_stream_seq = 0
    client._reasoning_active_pulse_seq = None
    pulses = []

    async def on_thinking_active(active):
        pulses.append(active)

    client.on_thinking_active = on_thinking_active

    s1 = client._begin_reasoning_stream()       # proactive scope
    await client._notify_reasoning_active()      # proactive pulses → bubble on
    client._begin_reasoning_stream()             # user stream starts, NO pulse yet
    assert pulses == [True]

    # Proactive finally: user hasn't pulsed, proactive still owns → clears its own.
    await client._notify_reasoning_done(s1)
    assert pulses == [True, False], "被抢占但新流未 pulse 时，旧流必须清掉自己的气泡"


@pytest.mark.asyncio
async def test_offline_openai_path_pulses_when_reasoning_bundled_with_other_signal():
    """A thinking provider can pack reasoning_content onto the SAME delta as a
    visible token / finish_reason (or a tool_call_delta). The pulse must still
    fire — it can't live only inside the pure-reasoning skip, or a reasoning
    tool-call turn shows no bubble at all (Codex P2)."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient

    # reasoning_content + content + finish_reason all on one chunk → fails the
    # pure-reasoning skip (has content/finish) yet must still pulse.
    chunks = [LLMStreamChunk(content="答案", reasoning_content="先想想", finish_reason="stop")]
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = _FakeLLM([chunks])
    client._tool_definitions = []
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client.on_tool_call = None
    client._reasoning_stream_seq = 0
    client._reasoning_active_pulse_seq = None

    pulses = []

    async def on_thinking_active(active):
        pulses.append(active)

    client.on_thinking_active = on_thinking_active

    out = []
    async for ch in client._astream_with_tools([{"role": "user", "content": "hi"}]):
        out.append(ch)

    assert pulses == [True], "reasoning 与可见 token / finish 同 chunk 时仍应 pulse"
    assert any(getattr(ch, "content", None) == "答案" for ch in out)


@pytest.mark.asyncio
async def test_offline_openai_path_omits_reasoning_when_absent():
    """For non-thinking endpoints (deltas without reasoning_content), assistant tool_calls
    messages must not invent a reasoning_content field, which could pollute ordinary sessions / trigger some providers'
    reverse validation of reasoning_content."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    tool_def = ToolDefinition(
        name="memory_recall",
        description="recall",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=lambda args: {"hits": []},
    )
    chunks_call_1 = [
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0,
                "id": "call_m",
                "type": "function",
                "function": {"name": "memory_recall", "arguments": "{}"},
            }],
            finish_reason=None,
        ),
        LLMStreamChunk(content="", tool_call_deltas=None, finish_reason="tool_calls"),
    ]
    chunks_call_2 = [LLMStreamChunk(content="好的喵。", finish_reason="stop")]
    fake_llm = _FakeLLM([chunks_call_1, chunks_call_2])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"hits": []})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "hi"}]
    async for _ in client._astream_with_tools(messages):
        pass

    assistant_with_tool_calls = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert "reasoning_content" not in assistant_with_tool_calls


@pytest.mark.asyncio
async def test_offline_switch_model_recomputes_genai_routing(monkeypatch):
    """switch_model must recalculate _use_genai_sdk after switching endpoints
    and clear _genai_client rather than reusing the old conversation's routing decision.

    Regression coverage: Codex P1 feedback on PR #1035.

    This test checks only the state transition logic: monkeypatch ``_GENAI_AVAILABLE=True``
    so ``_should_use_genai_sdk`` can take its ``True`` branch even in CI without google-genai,
    preventing this regression check from being silently skipped in incomplete environments."""
    from main_logic.omni_offline_client import OmniOfflineClient

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    # Build the client: conversation uses OpenAI; vision_base_url points to a native Gemini endpoint.
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gpt-4o-mini"
    client.base_url = "https://api.openai.com/v1"
    client.api_key = "sk-fake"
    client.vision_model = "gemini-2.5-flash"
    client.vision_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
    client.vision_api_key = "fake-gemini-key"
    client.max_response_length = 300
    client._tool_definitions = []
    client.on_tool_call = None
    client._genai_tools_unsupported = False
    client._genai_client = "stale-sentinel"  # Simulate the old client.
    # Initially use OpenAI conversation; the routing flag must be False.
    from main_logic.omni_offline_client import _should_use_genai_sdk
    client._use_genai_sdk = _should_use_genai_sdk(client.model, client.base_url)
    assert client._use_genai_sdk is False

    # Provide a placeholder llm supporting aclose().
    class _FakeLLM2:
        max_completion_tokens = 100
        async def aclose(self): pass
    client.llm = _FakeLLM2()

    # Switch to vision config (with a native Gemini endpoint).
    await client.switch_model("gemini-2.5-flash", use_vision_config=True)

    # The routing flag must be recalculated as True.
    assert client._use_genai_sdk is True, (
        "switch_model 后 _use_genai_sdk 必须重算，否则 vision/Gemini 切换路由错"
    )
    # Clear the old _genai_client so the next use initializes it lazily.
    assert client._genai_client is None
    # Synchronize base_url / api_key with the vision configuration.
    assert client.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert client.api_key == "fake-gemini-key"


@pytest.mark.asyncio
async def test_offline_switch_model_serializes_concurrent_switches(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gpt-4o-mini"
    client.base_url = "https://api.openai.com/v1"
    client.api_key = "sk-fake"
    client.vision_model = "vision-model"
    client.vision_base_url = "https://vision.example/v1"
    client.vision_api_key = "vision-key"
    client.max_response_length = 300
    client._genai_client = None
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    class _FakeLLM:
        def __init__(self, name: str) -> None:
            self.name = name
            self.closed = 0

        async def aclose(self) -> None:
            self.closed += 1

    old_llm = _FakeLLM("old")
    client.llm = old_llm
    created: list[_FakeLLM] = []

    async def fake_create_chat_llm_async(*_args, **_kwargs):
        await asyncio.sleep(0)
        llm = _FakeLLM("vision")
        created.append(llm)
        return llm

    monkeypatch.setattr(_ofc_streaming, "create_chat_llm_async", fake_create_chat_llm_async)

    await asyncio.gather(
        client.switch_model("vision-model", use_vision_config=True),
        client.switch_model("vision-model", use_vision_config=True),
    )

    assert len(created) == 1
    assert client.llm is created[0]
    assert client.model == "vision-model"
    assert client.base_url == "https://vision.example/v1"
    assert client.api_key == "vision-key"
    assert old_llm.closed == 1


@pytest.mark.asyncio
async def test_offline_genai_transient_error_does_not_disable_tools(monkeypatch):
    """genai SDK network / authentication glitches (429 / 5xx / timeout / auth) must not be wrapped as
    `_GenaiToolsUnsupported`, or one transient error would permanently degrade the entire session
    to the OpenAI-compatible path and permanently disable tool calling.

    Regression coverage: CodeRabbit round 4 review on PR #1035."""
    from main_logic.omni_offline_client import (
        OmniOfflineClient,
    )

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    class _BoomClient:
        class _aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    # Simulate a 5xx server error unrelated to tools.
                    raise RuntimeError("HTTP 503 Service Unavailable: upstream timeout")

        aio = _aio()

        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-2.5-flash"
    client.api_key = "fake"
    client._tool_definitions = []
    client.on_tool_call = None
    client.has_tools = lambda: False  # bypass tools check
    client.max_tool_iterations = 1
    client._genai_client = _BoomClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    # Expect transient errors to raise the original exception rather than wrapping it as _GenaiToolsUnsupported.
    with pytest.raises(RuntimeError, match="503"):
        async for _ in client._astream_genai_with_tools([{"role": "user", "content": "x"}]):
            pass


@pytest.mark.asyncio
async def test_offline_genai_streamed_text_persisted_with_tool_call(monkeypatch):
    """When text and function_call coexist in one Gemini turn, the assistant history message's
    content must include text already streamed to the user, or the LLM cannot see the first half of its
    own utterance next round and may repeat or contradict it.

    Regression coverage: CodeRabbit round 5 review on PR #1035."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    # Build a fake stream: one chunk contains both a text part and a function_call part.
    class _Part:
        def __init__(self, *, text=None, function_call=None):
            self.text = text
            self.function_call = function_call

    class _FunctionCall:
        def __init__(self, name, args, id_=""):
            self.name = name
            self.args = args
            self.id = id_

    class _Content:
        def __init__(self, parts):
            self.parts = parts

    class _Candidate:
        def __init__(self, content):
            self.content = content

    class _Chunk:
        def __init__(self, candidates, usage=None):
            self.candidates = candidates
            self.usage_metadata = usage

    async def _fake_stream():
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(text="让我查一下天气，"),
            _Part(function_call=_FunctionCall("get_weather", {"city": "Tokyo"}, id_="c1")),
        ]))])

    class _StreamWrapper:
        def __init__(self): self._gen = _fake_stream()
        def __aiter__(self): return self
        async def __anext__(self):
            try:
                return await self._gen.__anext__()
            except StopAsyncIteration:
                raise

    call_count = [0]

    class _FakeAioClient:
        class models:
            @staticmethod
            async def generate_content_stream(**_kw):
                call_count[0] += 1
                if call_count[0] == 1:
                    return _StreamWrapper()
                # Second round (tool executed): return the closing text.
                async def _fin():
                    yield _Chunk(candidates=[_Candidate(_Content([
                        _Part(text="Tokyo 现在 22°C 喵。"),
                    ]))])
                w = _StreamWrapper()
                w._gen = _fin()
                return w

    class _FakeClient:
        aio = _FakeAioClient()
        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-2.5-flash"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False  # bypass; we still want function_call detected
    client.max_tool_iterations = 3
    client._genai_client = _FakeClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"temp_c": 22})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "weather Tokyo"}]
    out = []
    async for ch in client._astream_genai_with_tools(messages):
        if ch.content:
            out.append(ch.content)

    # The user receives the first half plus the second half.
    full_user_text = "".join(out)
    assert "让我查一下天气" in full_user_text
    assert "22°C" in full_user_text

    # The assistant history message containing tool_calls must include the already yielded first half in content.
    assistant_with_tool_calls = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert "让我查一下天气" in assistant_with_tool_calls["content"], (
        "同轮先 yield text 再调工具时，写历史的 content 必须保留 streamed text，"
        "否则下一轮 LLM 看不到自己已说过的前半句"
    )


@pytest.mark.asyncio
async def test_genai_messages_to_contents_preserves_text_with_tool_calls():
    """When the assistant has both content and tool_calls, conversion to Gemini Content must emit
    text and function_call together as parts. Otherwise the next generate_content_stream
    still sees history missing the already streamed first half, and the model may repeat / contradict it.

    Regression coverage: CodeRabbit round 6 review on PR #1035."""
    pytest.importorskip("google.genai")
    from main_logic.omni_offline_client import _genai_messages_to_contents

    messages = [
        {"role": "user", "content": "查天气"},
        {
            "role": "assistant",
            "content": "让我查一下天气，",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Tokyo"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "get_weather",
         "content": '{"temp_c": 22}'},
    ]
    _, contents = _genai_messages_to_contents(messages)
    # Find the assistant turn.
    assistant_turn = next(c for c in contents if c.role == "model")
    parts = list(assistant_turn.parts)
    # The first part must be text, followed by function_call.
    text_parts = [p for p in parts if getattr(p, "text", None)]
    fc_parts = [p for p in parts if getattr(p, "function_call", None)]
    assert text_parts, (
        "assistant 同 turn 里有 content 时，转 Gemini Content 必须保留 text part，"
        "否则下一轮 LLM 看不到自己已 stream 出去的前半句"
    )
    assert any("查一下天气" in (p.text or "") for p in text_parts)
    assert fc_parts and fc_parts[0].function_call.name == "get_weather"


@pytest.mark.asyncio
async def test_register_tool_and_sync_serializes_concurrent_updates():
    """Consecutive register_tool_and_sync calls must serialize session.update pushes, or
    OpenAI Realtime / GLM / Qwen wire events may arrive out of order and the last snapshot
    may not match the registry's final state.

    Regression coverage: CodeRabbit round 6 review on PR #1035."""
    import asyncio as _asyncio

    from main_logic.tool_calling import ToolDefinition

    # Build a mgr with _tool_sync_lock + tool_registry and stub all other fields.
    class _StubMgr:
        def __init__(self):
            from main_logic.tool_calling import ToolRegistry
            self.tool_registry = ToolRegistry()
            self._tool_sync_lock = _asyncio.Lock()
            self.session = None
            self.pending_session = None
            self.sync_call_log: list = []

        async def _sync_tools_to_active_session(self):
            # Simulate the real implementation: read the registry and "push" only after entering the lock.
            async with self._tool_sync_lock:
                names = self.tool_registry.names()
                # Simulate a ~10ms session.update push.
                await _asyncio.sleep(0.01)
                self.sync_call_log.append(tuple(sorted(names)))

        async def register_tool_and_sync(self, tool, *, replace=True):
            self.tool_registry.register(tool, replace=replace)
            await self._sync_tools_to_active_session()

    mgr = _StubMgr()

    # Three concurrent registrations.
    await _asyncio.gather(
        mgr.register_tool_and_sync(ToolDefinition(name="a", description="", handler=lambda _: 0)),
        mgr.register_tool_and_sync(ToolDefinition(name="b", description="", handler=lambda _: 0)),
        mgr.register_tool_and_sync(ToolDefinition(name="c", description="", handler=lambda _: 0)),
    )

    # Serialization: snapshots seen by successive syncs grow monotonically (never see abc followed by ab).
    sizes = [len(snap) for snap in mgr.sync_call_log]
    assert sizes == sorted(sizes), (
        f"sync_call_log 必须单调增加（串行推送），实际：{mgr.sync_call_log}"
    )
    # The final sync must see all three tools.
    assert mgr.sync_call_log[-1] == ("a", "b", "c")


def test_tool_register_request_rejects_non_loopback_callback_url():
    """callback_url host allowlist: allow only 127.0.0.0/8 / ::1 / localhost,
    preventing local callers from using main_server as an outbound SSRF proxy.

    Regression coverage: CodeRabbit round 14 review on PR #1035."""
    from pydantic import ValidationError

    from main_routers.tool_router import ToolRegisterRequest

    base = {
        "name": "x",
        "callback_url": "http://127.0.0.1:9000/cb",
        "parameters": {"type": "object", "properties": {}},
    }

    # Valid cases.
    for url in [
        "http://127.0.0.1:9000/cb",
        "http://localhost:9000/cb",
        "http://[::1]:9000/cb",
        "http://127.0.0.5/cb",  # The entire 127.0.0.0/8 range is loopback.
        "https://localhost/cb",
        # IPv4-mapped IPv6 loopback: CPython < 3.11.11 is_loopback
        # does not inspect the mapping; the validator must unpack ipv4_mapped explicitly.
        "http://[::ffff:127.0.0.1]:9000/cb",
    ]:
        ToolRegisterRequest(**{**base, "callback_url": url})

    # Invalid cases: public IP / LAN IP / private domain / wrong scheme / missing host.
    illegal_urls = [
        "http://8.8.8.8/cb",
        "http://192.168.1.5/cb",  # LAN addresses are forbidden too.
        "http://10.0.0.1/cb",
        "http://example.com/cb",
        "ftp://127.0.0.1/cb",  # Wrong scheme.
        "http:///cb",  # Missing host.
        "http://[2001:db8::1]/cb",  # Public IPv6.
        "http://[::ffff:8.8.8.8]/cb",  # IPv4-mapped public IPs must still be rejected.
    ]
    for url in illegal_urls:
        with pytest.raises(ValidationError):
            ToolRegisterRequest(**{**base, "callback_url": url})


@pytest.mark.asyncio
async def test_register_tool_and_sync_propagates_session_update_failure():
    """`*_and_sync` must propagate wire synchronization failures, or HTTP
    /api/tools would incorrectly return ok=true while session tools are not actually updated.

    Regression coverage: CodeRabbit round 8 review on PR #1035."""
    import asyncio as _asyncio

    from main_logic.tool_calling import ToolDefinition, ToolRegistry

    # Stub mgr: simulate the serialized register_tool_and_sync pipeline with raise_on_failure.
    class _StubMgr:
        def __init__(self):
            self.tool_registry = ToolRegistry()
            self._tool_sync_lock = _asyncio.Lock()
            self.session = object()  # Trigger the sync path.
            self.pending_session = None

        async def _sync_tools_to_active_session(self, *, raise_on_failure=False):
            async with self._tool_sync_lock:
                # Simulate guaranteed wire push failure.
                err = "session.update rejected by mock server"
                if raise_on_failure:
                    raise RuntimeError(f"tool sync failed: active: RuntimeError: {err}")

        async def register_tool_and_sync(self, tool, *, replace=True):
            self.tool_registry.register(tool, replace=replace)
            await self._sync_tools_to_active_session(raise_on_failure=True)

    mgr = _StubMgr()
    with pytest.raises(RuntimeError, match="tool sync failed"):
        await mgr.register_tool_and_sync(
            ToolDefinition(name="x", description="", handler=lambda _: 0),
        )


@pytest.mark.asyncio
async def test_unregister_tool_router_isolates_per_role_failures():
    """When `/api/tools/unregister` spans roles, one mgr exception must not turn the entire request into a 500.
    Include successful roles in affected_roles and failed ones in failed_roles.

    Regression coverage: CodeRabbit round 8 review on PR #1035."""
    from main_routers import tool_router as _tr

    class _GoodMgr:
        lanlan_name = "Good"
        async def unregister_tool_and_sync(self, name): return True

    class _BadMgr:
        lanlan_name = "Bad"
        async def unregister_tool_and_sync(self, name):
            raise RuntimeError("fake sync failure")

    targets = [_GoodMgr(), _BadMgr()]
    # Call the endpoint function directly, bypassing _resolve_target_managers.
    # (This helper needs monkeypatching.)
    import unittest.mock as _mock
    with _mock.patch.object(_tr, "_resolve_target_managers", return_value=targets):
        from main_routers.tool_router import unregister_tool, ToolUnregisterRequest
        result = await unregister_tool(ToolUnregisterRequest(name="x", role=None))

    assert result["affected_roles"] == ["Good"]
    assert len(result["failed_roles"]) == 1
    assert result["failed_roles"][0]["role"] == "Bad"
    assert "fake sync failure" in result["failed_roles"][0]["error"]
    # One success + one failure -> ok=True (partial success).
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Automatically evict dead plugins: after N consecutive connection-level failures for one plugin source, _remote_dispatch
# removes all tools from that source. Cover dead endpoint tools left in the main_server registry after
# kill -9 kills the plugin process: graceful shutdown uses /api/tools/clear, but crashes cannot trigger it.
# ---------------------------------------------------------------------------


def _make_eviction_stub_mgr(name: str):
    """Build a minimal mgr compatible with _evict_dead_callback_origin, exposing
    ``tool_registry`` plus private ``_fire_task`` / ``_sync_tools_to_active_session``
    (used by eviction to trigger wire synchronization through the same path as register_tool / clear_tools).
    ``_fire_task`` closes the coroutine directly to avoid \"coroutine was never awaited\"
    warnings; the unit test needs only to verify that it was called."""
    from main_logic.tool_calling import ToolRegistry

    class _StubMgr:
        def __init__(self, lanlan_name: str):
            self.lanlan_name = lanlan_name
            self.tool_registry = ToolRegistry()
            self.fire_task_count = 0

        async def _sync_tools_to_active_session(self):
            # Unit tests have no active session, so wire synchronization is a noop.
            return None

        def _fire_task(self, coro):
            self.fire_task_count += 1
            coro.close()

    return _StubMgr(name)


@pytest.fixture
def _reset_eviction_counter():
    """Clear module-level failure counters before and after every eviction test to avoid cross-test contamination."""
    from main_routers import tool_router as _tr
    _tr._consecutive_connect_failures.clear()
    yield
    _tr._consecutive_connect_failures.clear()


@pytest.mark.asyncio
async def test_remote_dispatch_evicts_dead_plugin_after_three_connect_failures(
    monkeypatch, _reset_eviction_counter,
):
    """After kill -9 kills a plugin, three model connection-refused failures must remove that plugin's
    tools from every session manager's registry, leaving builtin tools intact.

    Regression coverage: lifecycle audit found no cleanup path for plugin crashes."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    # Prepare a stub session_manager with two roles; each mgr has the same plugin's
    # two tools (simulating global registration with role=None) plus one builtin.
    mgr_a = _make_eviction_stub_mgr("Alpha")
    mgr_b = _make_eviction_stub_mgr("Beta")
    for mgr in (mgr_a, mgr_b):
        mgr.tool_registry.register(ToolDefinition(
            name="weather", description="", handler=None,
            metadata={"source": "plugin:dead_foo", "callback_url": "http://127.0.0.1:9999/cb"},
        ))
        mgr.tool_registry.register(ToolDefinition(
            name="search", description="", handler=None,
            metadata={"source": "plugin:dead_foo", "callback_url": "http://127.0.0.1:9999/cb"},
        ))
        mgr.tool_registry.register(ToolDefinition(
            name="recall_memory", description="", handler=lambda _: "",
            metadata={"source": "builtin"},
        ))

    fake_session_manager = {"Alpha": mgr_a, "Beta": mgr_b}
    monkeypatch.setattr(_tr, "get_session_manager", lambda: fake_session_manager)

    # Make the httpx client always raise ConnectError to simulate a dead plugin process.
    class _DeadClient:
        async def post(self, *_a, **_kw):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _DeadClient())

    # The model dispatches three times (any role increments the shared module-level counter across mgrs).
    call = ToolCall(name="weather", arguments={}, call_id="c")
    metadata = {
        "source": "plugin:dead_foo",
        "callback_url": "http://127.0.0.1:9999/cb",
        "timeout_seconds": 5,
    }
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD):
        result = await _tr._remote_dispatch(call, metadata)
        # Each failed dispatch must still return ToolResult (without raising),
        # so the model sees a structured error rather than a client crash.
        assert result.is_error is True

    # After the threshold, both mgrs must remove every plugin:dead_foo tool,
    # leaving builtin tools intact.
    for mgr in (mgr_a, mgr_b):
        names = sorted(mgr.tool_registry.names())
        assert names == ["recall_memory"], (
            f"mgr {mgr.lanlan_name}: plugin tools should be evicted, "
            f"builtin should remain; got {names}"
        )
        # Eviction must trigger one wire sync so the model's schema no longer exposes dead tools.
        assert mgr.fire_task_count >= 1, (
            f"mgr {mgr.lanlan_name}: eviction must fire session.update sync; "
            f"got fire_task_count={mgr.fire_task_count}"
        )

    # Reset the counter after eviction to avoid a false eviction on the next single failure.
    dead_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert ("plugin:dead_foo", dead_origin) not in _tr._consecutive_connect_failures


@pytest.mark.asyncio
async def test_remote_dispatch_business_error_does_not_evict(
    monkeypatch, _reset_eviction_counter,
):
    """A plugin callback returning ``is_error=True`` (or HTTP 5xx) proves the plugin is alive;
    only the tool failed internally. This is a tool bug, not a lifecycle bug, and must not count toward eviction."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    mgr.tool_registry.register(ToolDefinition(
        name="buggy_tool", description="", handler=None,
        metadata={"source": "plugin:buggy", "callback_url": "http://127.0.0.1:9999/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    # Simulate a live plugin (200 + is_error=true body).
    class _AliveButBuggyResp:
        status_code = 200
        text = '{"is_error": true, "error": "tool blew up"}'
        def json(self):
            return {"is_error": True, "error": "tool blew up"}

    class _AliveClient:
        async def post(self, *_a, **_kw):
            return _AliveButBuggyResp()

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _AliveClient())

    call = ToolCall(name="buggy_tool", arguments={}, call_id="c")
    metadata = {
        "source": "plugin:buggy",
        "callback_url": "http://127.0.0.1:9999/cb",
        "timeout_seconds": 5,
    }
    # Call more times than the threshold.
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD + 2):
        result = await _tr._remote_dispatch(call, metadata)
        assert result.is_error is True  # Pass business errors through.

    # The tool remains registered: business errors are not lifecycle errors.
    assert mgr.tool_registry.names() == ["buggy_tool"]
    assert mgr.fire_task_count == 0
    # The counter never accumulates (each successful HTTP request resets it).
    buggy_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert ("plugin:buggy", buggy_origin) not in _tr._consecutive_connect_failures


@pytest.mark.asyncio
async def test_remote_dispatch_success_resets_failure_counter(
    monkeypatch, _reset_eviction_counter,
):
    """Two consecutive connection failures (below threshold) followed by success reset the counter;
    later failures restart at one. Prevent intermittent connection refusal from accumulating into false eviction."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    mgr.tool_registry.register(ToolDefinition(
        name="flaky", description="", handler=None,
        metadata={"source": "plugin:flaky", "callback_url": "http://127.0.0.1:9999/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    class _OkResp:
        status_code = 200
        text = '{"output": "ok"}'
        def json(self): return {"output": "ok"}

    fail_then_ok = [
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
        _OkResp(),
        httpx.ConnectError("refused"),
        httpx.ConnectError("refused"),
    ]
    class _SwitchingClient:
        async def post(self, *_a, **_kw):
            item = fail_then_ok.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _SwitchingClient())

    call = ToolCall(name="flaky", arguments={}, call_id="c")
    metadata = {"source": "plugin:flaky", "callback_url": "http://127.0.0.1:9999/cb", "timeout_seconds": 5}

    # fail, fail (counter=2), ok (counter=0), fail, fail (counter=2) -- still below threshold.
    for _ in range(5):
        await _tr._remote_dispatch(call, metadata)

    # The tool remains; the counter has accumulated but has not exceeded the threshold.
    assert mgr.tool_registry.names() == ["flaky"]
    assert mgr.fire_task_count == 0
    flaky_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert _tr._consecutive_connect_failures.get(("plugin:flaky", flaky_origin)) == 2


@pytest.mark.asyncio
async def test_remote_dispatch_builtin_source_never_evicted(
    monkeypatch, _reset_eviction_counter,
):
    """``source=\"builtin\"`` never enters eviction counters: builtin tools use in-process
    handlers and usually never reach _remote_dispatch; even a mistakenly remote-configured builtin
    must not be removed for repeated failures."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    # Pathological registration: builtin with handler=None, forced through the remote path.
    mgr.tool_registry.register(ToolDefinition(
        name="recall_memory", description="", handler=None,
        metadata={"source": "builtin", "callback_url": "http://127.0.0.1:9999/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    class _DeadClient:
        async def post(self, *_a, **_kw):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _DeadClient())

    call = ToolCall(name="recall_memory", arguments={}, call_id="c")
    metadata = {"source": "builtin", "callback_url": "http://127.0.0.1:9999/cb", "timeout_seconds": 5}
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD + 3):
        await _tr._remote_dispatch(call, metadata)

    # The tool remains registered and no counter is ever created.
    assert mgr.tool_registry.names() == ["recall_memory"]
    assert mgr.fire_task_count == 0
    # Builtin sources never enter the counter dict.
    assert not any(src == "builtin" for src, _origin in _tr._consecutive_connect_failures)


@pytest.mark.asyncio
async def test_remote_dispatch_read_timeout_does_not_evict(
    monkeypatch, _reset_eviction_counter,
):
    """ReadTimeout means the plugin accepted the request but executes slowly: a tool implementation issue,
    not an unreachable endpoint. Only ConnectError/ConnectTimeout count as lifecycle failures."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    mgr.tool_registry.register(ToolDefinition(
        name="slow", description="", handler=None,
        metadata={"source": "plugin:slow", "callback_url": "http://127.0.0.1:9999/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    class _SlowClient:
        async def post(self, *_a, **_kw):
            raise httpx.ReadTimeout("tool ran past deadline")

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _SlowClient())

    call = ToolCall(name="slow", arguments={}, call_id="c")
    metadata = {"source": "plugin:slow", "callback_url": "http://127.0.0.1:9999/cb", "timeout_seconds": 5}
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD + 2):
        result = await _tr._remote_dispatch(call, metadata)
        assert result.is_error is True  # Timeouts are still passed to the model as errors.

    # Leave the tool intact: ReadTimeout is not connection-level and does not increment the counter.
    assert mgr.tool_registry.names() == ["slow"]
    assert mgr.fire_task_count == 0
    slow_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert ("plugin:slow", slow_origin) not in _tr._consecutive_connect_failures


@pytest.mark.asyncio
async def test_remote_dispatch_endpoint_local_outage_preserves_sibling_endpoints(
    monkeypatch, _reset_eviction_counter,
):
    """If tools registered under one plugin source point to different callback origins (different ports),
    an unreachable endpoint may remove only that endpoint's tools, without affecting healthy tools
    from the same source.

    Regression coverage: Codex review on PR #1382 found that grouping only by source incorrectly escalated
    one dead endpoint into the entire plugin being dead."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    # Same plugin source, two tools pointing to different ports.
    mgr.tool_registry.register(ToolDefinition(
        name="tool_on_dead_port", description="", handler=None,
        metadata={"source": "plugin:multi_port", "callback_url": "http://127.0.0.1:9001/cb"},
    ))
    mgr.tool_registry.register(ToolDefinition(
        name="tool_on_live_port", description="", handler=None,
        metadata={"source": "plugin:multi_port", "callback_url": "http://127.0.0.1:9002/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    # Only port 9001 is unreachable; 9002 works.
    class _OkResp:
        status_code = 200
        text = '{"output": "ok"}'
        def json(self): return {"output": "ok"}

    class _PortSelectiveClient:
        async def post(self, url, *_a, **_kw):
            if ":9001" in url:
                raise httpx.ConnectError("Connection refused on 9001")
            return _OkResp()

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _PortSelectiveClient())

    dead_call = ToolCall(name="tool_on_dead_port", arguments={}, call_id="d")
    dead_metadata = {
        "source": "plugin:multi_port",
        "callback_url": "http://127.0.0.1:9001/cb",
        "timeout_seconds": 5,
    }
    # Three hits on the dead port trigger eviction.
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD):
        await _tr._remote_dispatch(dead_call, dead_metadata)

    # Key assertion: only tools pointing to 9001 are removed; sibling tools on 9002 remain intact.
    names = sorted(mgr.tool_registry.names())
    assert names == ["tool_on_live_port"], (
        f"endpoint-local outage on 9001 must NOT evict sibling tool on 9002; "
        f"got remaining={names}"
    )

    # No counter should ever exist for the healthy port: it never failed.
    live_origin = _tr._callback_origin("http://127.0.0.1:9002/cb")
    assert ("plugin:multi_port", live_origin) not in _tr._consecutive_connect_failures

    # The healthy endpoint stays callable and visible in the schema.
    live_call = ToolCall(name="tool_on_live_port", arguments={}, call_id="L")
    live_metadata = {
        "source": "plugin:multi_port",
        "callback_url": "http://127.0.0.1:9002/cb",
        "timeout_seconds": 5,
    }
    result = await _tr._remote_dispatch(live_call, live_metadata)
    assert result.is_error is False
    assert result.output == "ok"


def test_callback_origin_normalizes_to_scheme_host_port():
    """``_callback_origin`` must collapse (scheme, host, port) into one bucket key;
    paths / queries / fragments must not make different URLs on one server appear to be separate endpoints.
    """
    from main_routers.tool_router import _callback_origin

    base = _callback_origin("http://127.0.0.1:9000/api/cb")
    # Same origin, different path / query -> same key.
    assert _callback_origin("http://127.0.0.1:9000/api/cb") == base
    assert _callback_origin("http://127.0.0.1:9000/other/path") == base
    assert _callback_origin("http://127.0.0.1:9000/api/cb?x=1") == base
    # Different ports -> different keys.
    assert _callback_origin("http://127.0.0.1:9001/api/cb") != base
    # Make default ports explicit (http->80, https->443), so ``http://h/`` and
    # ``http://h:80/`` are not treated as different endpoints.
    assert _callback_origin("http://127.0.0.1/cb") == "http://127.0.0.1:80"
    assert _callback_origin("https://127.0.0.1/cb") == "https://127.0.0.1:443"
    # Invalid input fallback: empty / unparseable values must not raise.
    assert _callback_origin("") == "<unknown>"
    # Malformed ports (nonnumeric) make ParseResult.port raise ValueError;
    # the loopback validator does not check port syntax, so dispatch must handle it itself,
    # or structured ToolResult errors and eviction bookkeeping will break.
    # （Codex review on PR #1382）
    bad_port = "http://127.0.0.1:abc/cb"
    assert _callback_origin(bad_port) == bad_port  # Fall back to the original string without raising.
    # The same malformed URL must map to the same key, or failure counts could
    # degrade through key collisions and never reach the threshold.
    assert _callback_origin(bad_port) == _callback_origin(bad_port)


@pytest.mark.asyncio
async def test_remote_dispatch_survives_malformed_callback_url(
    monkeypatch, _reset_eviction_counter,
):
    """``ToolRegisterRequest``'s loopback validator does not check port syntax, so malformed
    ports (``http://127.0.0.1:abc/cb``) can pass registration. ``_callback_origin``
    must not raise, or dispatch breaks: instead of returning a structured ToolResult
    error it reaches ToolRegistry.execute's fallback catch, losing error-message precision.

    Regression coverage: Codex review on PR #1382."""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    bad_url = "http://127.0.0.1:abc/cb"
    mgr = _make_eviction_stub_mgr("Solo")
    mgr.tool_registry.register(ToolDefinition(
        name="malformed_tool", description="", handler=None,
        metadata={"source": "plugin:malformed", "callback_url": bad_url},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    class _DeadClient:
        async def post(self, *_a, **_kw):
            raise httpx.ConnectError("refused")

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _DeadClient())

    call = ToolCall(name="malformed_tool", arguments={}, call_id="c")
    metadata = {"source": "plugin:malformed", "callback_url": bad_url, "timeout_seconds": 5}

    # Must not raise: always return a structured ToolResult(is_error=True).
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD):
        result = await _tr._remote_dispatch(call, metadata)
        assert result.is_error is True
        assert "HTTP failure" in result.error_message

    # Eviction bookkeeping must still work for malformed URLs: remove them after three failures.
    assert mgr.tool_registry.names() == []
    assert mgr.fire_task_count >= 1


@pytest.mark.asyncio
async def test_genai_unsupported_keyword_matches_underscore_variant(monkeypatch):
    """Treat the underscore variant `not_support` as permanently unsupported tools too, avoiding
    an extra genai attempt followed by fallback on every round.

    Regression coverage: CodeRabbit round 8 review on PR #1035."""
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _GenaiToolsUnsupported,
    )

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    class _UnderscoreErrorClient:
        class _aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    raise RuntimeError("function_call_not_support on this model")
        aio = _aio()
        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-old"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False
    client.max_tool_iterations = 1
    client._genai_client = _UnderscoreErrorClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    with pytest.raises(_GenaiToolsUnsupported):
        async for _ in client._astream_genai_with_tools([{"role": "user", "content": "x"}]):
            pass


@pytest.mark.asyncio
async def test_offline_no_silent_fallback_after_genai_emitted_text(monkeypatch):
    """If the genai path raises a transient exception after yielding a text chunk,
    `_astream_with_tools` must not silently fall back to OpenAI-compatible mode, or the user
    sees a partial Gemini reply concatenated with a fresh OpenAI reply in one turn. Raise instead
    so stream_text's retry/discard flow clears the bubble before retrying.

    Regression coverage: CodeRabbit round 7 review on PR #1035."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _genai_yields_then_raises(self, messages, **overrides):
        # Yield two text chunks, then raise a transient exception.
        yield LLMStreamChunk(content="让我查一下，")
        yield LLMStreamChunk(content="稍等。")
        raise RuntimeError("HTTP 503 transient")

    async def _openai_should_not_run(self, messages, **overrides):
        # Reaching here means the unwanted concatenation of two streams occurred.
        yield LLMStreamChunk(content="OPENAI_FALLBACK_TEXT_SHOULD_NOT_APPEAR")
        raise AssertionError("OpenAI fallback ran after genai emitted text — bug regression")

    monkeypatch.setattr(OmniOfflineClient, "_astream_genai_with_tools", _genai_yields_then_raises)
    monkeypatch.setattr(OmniOfflineClient, "_astream_openai_with_tools", _openai_should_not_run)

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._use_genai_sdk = True
    client._genai_tools_unsupported = False

    yielded = []
    with pytest.raises(RuntimeError, match="503"):
        async for ch in client._astream_with_tools([{"role": "user", "content": "x"}]):
            yielded.append(ch.content)

    # Text already yielded by genai must reach the caller before the exception propagates.
    assert "让我查一下，" in yielded
    assert "稍等。" in yielded
    # OpenAI fallback text must not appear.
    assert not any("OPENAI_FALLBACK" in (s or "") for s in yielded)


@pytest.mark.asyncio
async def test_stream_text_notifies_discarded_when_partial_text_then_error(monkeypatch):
    """stream_text's general except Exception branch must recognize failure after text was emitted
    and call _notify_response_discarded to clear the partial frontend bubble; otherwise
    the interrupted text remains visible forever. This is essential to making _astream_with_tools'
    new contract (raise after genai_emitted_text) effective.

    Regression coverage: CodeRabbit round 9 review on PR #1035."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk, SystemMessage

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _astream_partial_then_raise(self, messages, **overrides):
        yield LLMStreamChunk(content="正在查询天气，")
        raise RuntimeError("transient API failure mid-stream")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_partial_then_raise)

    discarded_calls: list = []
    text_emitted: list = []

    async def fake_notify_discarded(reason, attempt, max_attempts, will_retry, message=None):
        discarded_calls.append({
            "reason": reason, "attempt": attempt, "max_attempts": max_attempts,
            "will_retry": will_retry, "message": message,
        })

    async def fake_text_delta(text, is_first):
        text_emitted.append(text)

    async def fake_done():
        pass

    async def fake_status(_msg):
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "Test"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 300
    client.max_response_rerolls = 0
    client.enable_response_guard = False  # Simplify the logic: go straight to the except branch.
    client.vision_model = ""
    client.model = "gemini-2.5-flash"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = None
    client.on_response_done = fake_done
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = fake_status
    client.on_repetition_detected = None

    await client.stream_text("天气怎么样")

    # Assert that the text reached the frontend.
    assert "正在查询天气，" in "".join(text_emitted)
    # Key: the response-discarded notification must have been called to clear the partial frontend bubble.
    assert len(discarded_calls) >= 1, "已吐文本后必须 notify_response_discarded 让前端清空气泡"
    last = discarded_calls[-1]
    assert "text_gen_error" in last["reason"]
    assert last["will_retry"] is False  # The general except no longer retries.


@pytest.mark.asyncio
async def test_notify_response_discarded_prefers_request_bound_callback():
    """Per-stream callbacks must not fall back to the shared session callback."""
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    shared_calls = []
    bound_calls = []

    async def shared_callback(*args):
        shared_calls.append(args)

    async def bound_callback(*args):
        bound_calls.append(args)

    client.on_response_discarded = shared_callback

    await client._notify_response_discarded(
        "guard",
        1,
        3,
        False,
        None,
        callback=bound_callback,
    )

    assert len(bound_calls) == 1
    assert shared_calls == []


@pytest.mark.asyncio
async def test_stream_text_maps_incorrect_api_key_keyword_to_structured_status(monkeypatch):
    """Raw provider auth errors should not leak the full exception into UI text."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import SystemMessage

    async def _astream_auth_error(self, messages, **overrides):
        raise RuntimeError(
            "AuthenticationError: Error code: 401 - {'error': {'message': "
            "'Incorrect API key provided.', 'code': 'invalid_api_key'}}"
        )
        if False:  # pragma: no cover - keeps this as an async generator
            yield None

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_auth_error)

    status_messages: list[dict] = []

    async def fake_status(msg):
        status_messages.append(json.loads(msg))

    async def fake_done():
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "Test"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 300
    client.max_response_rerolls = 0
    client.enable_response_guard = False
    client.vision_model = ""
    client.model = "qwen-plus"
    client.on_text_delta = None
    client.on_input_transcript = None
    client.on_response_done = fake_done
    client.on_response_discarded = None
    client.on_status_message = fake_status
    client.on_repetition_detected = None

    await client.stream_text("hi")

    assert status_messages == [{"code": "API_KEY_REJECTED"}]


def test_api_key_error_helper_does_not_treat_plain_403_as_invalid_key():
    from main_logic.omni_offline_client import _is_api_key_rejected_error

    class ForbiddenError(Exception):
        status_code = 403

    assert _is_api_key_rejected_error(ForbiddenError("model access denied in this region")) is False
    assert _is_api_key_rejected_error(ForbiddenError("Error 403: Incorrect API key provided")) is True
    assert _is_api_key_rejected_error(RuntimeError("AuthenticationError: OAuth token expired")) is False


def test_safety_violation_helper_detects_provider_block_signals():
    from main_logic.omni_offline_client import _is_safety_violation_signal

    assert _is_safety_violation_signal("content_filter") is True
    assert _is_safety_violation_signal(None, "SAFETY") is True
    assert _is_safety_violation_signal("ResponsibleAIPolicyViolation") is True
    assert _is_safety_violation_signal("connection blocked by proxy") is False
    assert _is_safety_violation_signal("BLOCK_REASON_UNSPECIFIED") is False
    assert _is_safety_violation_signal("1008 connection closed") is False
    assert _is_safety_violation_signal("1008 policy violation") is True
    assert _is_safety_violation_signal("stop", None) is False
    assert _is_safety_violation_signal(None, None) is False


@pytest.mark.asyncio
async def test_stream_text_empty_safety_completion_reports_policy_violation(monkeypatch):
    """Safety-blocked empty completions should not surface as generic no-response."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk, SystemMessage

    async def _astream_empty_safety(self, messages, **overrides):
        self._last_finish_reason = "content_filter"
        self._last_block_reason = "SAFETY"
        self._last_prompt_tokens = 123
        yield LLMStreamChunk(content="", finish_reason="content_filter")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_empty_safety)

    status_messages: list[dict] = []

    async def fake_status(msg):
        status_messages.append(json.loads(msg))

    async def fake_done():
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "Test"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 300
    client.max_response_rerolls = 0
    client.enable_response_guard = False
    client.vision_model = ""
    client.model = "gemini-2.5-flash"
    client.on_text_delta = None
    client.on_input_transcript = None
    client.on_response_done = fake_done
    client.on_response_discarded = None
    client.on_status_message = fake_status
    client.on_repetition_detected = None
    client._last_finish_reason = None
    client._last_block_reason = None
    client._last_prompt_tokens = None

    await client.stream_text("hi")

    assert len(status_messages) == 1
    assert status_messages[0]["code"] == "API_POLICY_VIOLATION"
    assert status_messages[0]["details"]["finish_reason"] == "content_filter"
    assert status_messages[0]["details"]["block_reason"] == "SAFETY"
    assert status_messages[0]["details"]["prompt_tokens"] == 123
    assert status_messages[0]["details"]["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_prompt_ephemeral_reports_key_error_from_catch_all(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import SystemMessage

    async def _astream_auth_error(self, messages, **overrides):
        raise RuntimeError("AuthenticationError: Incorrect API key provided")
        if False:  # pragma: no cover - keeps this as an async generator
            yield None

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_auth_error)

    status_messages: list[dict] = []

    async def fake_status(msg):
        status_messages.append(json.loads(msg))

    async def fake_done(*_args):
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "Test"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._is_responding = False
    client.on_text_delta = None
    client.on_response_done = fake_done
    client.on_status_message = fake_status

    assert await client.prompt_ephemeral("hello") is False
    assert status_messages == [{"code": "API_KEY_REJECTED"}]


@pytest.mark.asyncio
async def test_stream_text_length_guard_finishes_visible_long_reply_without_discard(monkeypatch):
    """Once a normal long reply has streamed to the frontend, the length guard must not use discard/recovery.

    response_discarded clears the old bubble / subtitles, then recovery resends everything;
    subtitle translation processes the same turn twice, and TTS may receive an overlong complete recovery text.
    """
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk, SystemMessage

    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming,
        "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )
    length_log_args = []

    def fake_logger_info(message, *args, **_kwargs):
        if "长回复已流式输出" in message:
            length_log_args.append(args)

    monkeypatch.setattr(_ofc.logger, "info", fake_logger_info)

    stream_calls = 0

    async def _astream_long_reply(self, messages, **overrides):
        nonlocal stream_calls
        stream_calls += 1
        yield LLMStreamChunk(content="one two three four. five")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_long_reply)

    discarded_calls: list = []
    text_emitted: list = []

    async def fake_notify_discarded(reason, attempt, max_attempts, will_retry, message=None):
        discarded_calls.append({
            "reason": reason,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "will_retry": will_retry,
            "message": message,
        })

    async def fake_text_delta(text, is_first):
        text_emitted.append(text)

    async def noop(*_a, **_kw):
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 4
    client.max_response_rerolls = 1
    client.enable_response_guard = True
    client.vision_model = ""
    client.model = "x"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("write a long reply")

    assert stream_calls == 1
    assert "".join(text_emitted) == "one two three four."
    assert discarded_calls == []
    assert client._conversation_history[-1].content == "one two three four."
    assert length_log_args[-1] == (5, 4)


@pytest.mark.asyncio
async def test_offline_silent_fallback_when_genai_did_not_emit(monkeypatch):
    """Symmetric case: when genai raises a transient exception before yielding any text,
    `_astream_with_tools` should still silently fall back to OpenAI-compatible mode,
    keeping the failure invisible to the user for the best experience."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _genai_raises_immediately(self, messages, **overrides):
        # Key: raise before yielding anything.
        if False:
            yield  # make it a generator
        raise RuntimeError("HTTP 503 transient before any chunk")

    async def _openai_emits(self, messages, **overrides):
        yield LLMStreamChunk(content="OpenAI fallback OK")

    monkeypatch.setattr(OmniOfflineClient, "_astream_genai_with_tools", _genai_raises_immediately)
    monkeypatch.setattr(OmniOfflineClient, "_astream_openai_with_tools", _openai_emits)

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._use_genai_sdk = True
    client._genai_tools_unsupported = False

    yielded = []
    async for ch in client._astream_with_tools([{"role": "user", "content": "x"}]):
        yielded.append(ch.content)

    # Nothing yielded -> silent fallback; the user receives text from the OpenAI path.
    assert yielded == ["OpenAI fallback OK"]
    # Transient errors do not flip _genai_tools_unsupported.
    assert client._genai_tools_unsupported is False


@pytest.mark.asyncio
async def test_offline_silent_fallback_resets_unemitted_genai_filter_state(monkeypatch):
    """Silent fallback must reset leak-filter state that did not emit text."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk
    from utils.llm_tool_leak_filter import ToolLeakFilter

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _genai_consumes_filter_then_raises(self, messages, **overrides):
        leak_filter = overrides["_tool_leak_filter"]
        leak_filter.feed("<seed:tool_call>secret")
        if False:
            yield  # make it an async generator
        raise RuntimeError("HTTP 503 transient before visible output")

    async def _openai_emits(self, messages, **overrides):
        leak_filter = overrides["_tool_leak_filter"]
        visible, _event = leak_filter.feed("Hello")
        yield LLMStreamChunk(content=visible)

    monkeypatch.setattr(OmniOfflineClient, "_astream_genai_with_tools", _genai_consumes_filter_then_raises)
    monkeypatch.setattr(OmniOfflineClient, "_astream_openai_with_tools", _openai_emits)

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._use_genai_sdk = True
    client._genai_tools_unsupported = False

    yielded = []
    leak_filter = ToolLeakFilter(tool_names={"recall_memory"})
    async for ch in client._astream_with_tools(
        [{"role": "user", "content": "x"}],
        _tool_leak_filter=leak_filter,
        _tool_leak_provider="free",
    ):
        yielded.append(ch.content)

    assert yielded == ["Hello"]
    assert leak_filter.finalize() == ("", None)
    assert client._genai_tools_unsupported is False


@pytest.mark.asyncio
async def test_offline_genai_tools_unsupported_error_correctly_disables_path(monkeypatch):
    """Symmetric to the previous case: a genuine genai \"tools not supported\" error must be wrapped as
    `_GenaiToolsUnsupported`, letting `_astream_with_tools` flip `_genai_tools_unsupported`
    and fall back to the OpenAI-compatible path."""
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _GenaiToolsUnsupported,
    )

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    class _ToolsRejectClient:
        class _aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    raise RuntimeError("function declarations are not supported on this model")

        aio = _aio()

        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-old"
    client.api_key = "fake"
    client._tool_definitions = []
    client.on_tool_call = None
    client.has_tools = lambda: False
    client.max_tool_iterations = 1
    client._genai_client = _ToolsRejectClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    with pytest.raises(_GenaiToolsUnsupported):
        async for _ in client._astream_genai_with_tools([{"role": "user", "content": "x"}]):
            pass


def _tools_declining_client(astream_impl, *, max_tool_iterations=1):
    """Bare OpenAI-compat client whose llm.astream is ``astream_impl``."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolDefinition, ToolResult

    async def handler(call):
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    tool_def = ToolDefinition(
        name="noop",
        description="noop",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )

    class _Llm:
        max_completion_tokens = 100

        def astream(self, messages, **overrides):
            return astream_impl(messages, **overrides)

        async def aclose(self):
            return None

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "llava"
    client.base_url = "http://127.0.0.1:11434/v1"
    client.llm = _Llm()
    client._tool_definitions = [tool_def]
    client.on_tool_call = handler
    client.max_tool_iterations = max_tool_iterations
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._openai_tools_unsupported = False
    return client


_OLLAMA_NO_TOOLS_ERROR = (
    "Error code: 400 - {'error': {'message': "
    "'registry.ollama.ai/library/llava:latest does not support tools'}}"
)


@pytest.mark.asyncio
async def test_offline_openai_tools_unsupported_retries_same_request_without_tools():
    """An endpoint that rejects ``tools`` itself (Ollama llava: 400 "does not
    support tools") gets the same request re-issued once without tools, and
    the retry does not consume a tool iteration."""
    from utils.llm_client import LLMStreamChunk

    seen = []

    async def astream(_messages, **overrides):
        seen.append(set(overrides))
        if len(seen) == 1:
            raise RuntimeError(_OLLAMA_NO_TOOLS_ERROR)
        yield LLMStreamChunk(content="看到了", finish_reason="stop")

    client = _tools_declining_client(astream, max_tool_iterations=1)
    texts = [
        ch.content
        async for ch in client._astream_openai_with_tools([{"role": "user", "content": "hi"}])
        if getattr(ch, "content", None)
    ]

    assert "".join(texts) == "看到了"
    assert len(seen) == 2
    assert "tools" in seen[0]
    assert "tools" not in seen[1] and "tool_choice" not in seen[1]
    assert client._openai_tools_unsupported is True
    # Later turns in the session no longer build a tools payload at all.
    assert client._openai_tools_payload() is None


@pytest.mark.asyncio
async def test_offline_openai_tools_unsupported_is_not_retried_after_first_chunk():
    """Once the provider has streamed anything, a failure is not silently
    re-generated: replaying would splice two different replies together."""
    from utils.llm_client import LLMStreamChunk

    calls = []

    async def astream(_messages, **overrides):
        calls.append(1)
        yield LLMStreamChunk(content="半句", finish_reason=None)
        raise RuntimeError(_OLLAMA_NO_TOOLS_ERROR)

    client = _tools_declining_client(astream)
    with pytest.raises(RuntimeError):
        async for _ in client._astream_openai_with_tools([{"role": "user", "content": "hi"}]):
            pass
    assert len(calls) == 1
    assert client._openai_tools_unsupported is False


@pytest.mark.asyncio
async def test_offline_openai_request_specific_tools_refusal_is_not_sticky():
    """"tool use is not supported with images" is about THIS request: retry it
    without tools, but later text-only turns keep their tools."""
    from utils.llm_client import LLMStreamChunk

    seen = []

    async def astream(_messages, **overrides):
        seen.append(set(overrides))
        if len(seen) == 1:
            raise RuntimeError("Error code: 400 - tool use is not supported with images")
        yield LLMStreamChunk(content="ok", finish_reason="stop")

    client = _tools_declining_client(astream)
    texts = [
        ch.content
        async for ch in client._astream_openai_with_tools([{"role": "user", "content": "hi"}])
        if getattr(ch, "content", None)
    ]

    assert "".join(texts) == "ok"
    assert "tools" not in seen[1]
    assert client._openai_tools_unsupported is False
    assert client._openai_tools_payload() is not None


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("registry.ollama.ai/library/llava:latest does not support tools", "model"),
        ("Error 400: tools are not supported by this model", "model"),
        ("tool use is not supported for this model", "model"),
        ("tool use is not supported with images", "request"),
        ("this model does not support tools with images", "request"),
        ("tool use is not supported with images for this model", "request"),
        ("tools are not supported in combination with response_format", "request"),
        ("upstream overloaded", None),
    ],
)
def test_classify_openai_tools_refusal(message, expected):
    from main_logic.omni_offline_client import OmniOfflineClient

    assert OmniOfflineClient._classify_openai_tools_refusal(RuntimeError(message)) == expected


@pytest.mark.asyncio
async def test_tools_refused_with_images_are_skipped_while_history_carries_images():
    """"tool use is not supported with images": the picture stays in history,
    so later turns with it skip tools up front instead of being refused again;
    a text-only history still gets its tools."""
    from utils.llm_client import LLMStreamChunk

    seen = []

    async def astream(_messages, **overrides):
        seen.append("tools" in overrides)
        if len(seen) == 1:
            raise RuntimeError("Error code: 400 - tool use is not supported with images")
        yield LLMStreamChunk(content="ok", finish_reason="stop")

    client = _tools_declining_client(astream)
    with_image = [{"role": "user", "content": [
        {"type": "text", "text": "看"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]}]
    async for _ in client._astream_openai_with_tools(list(with_image)):
        pass
    assert seen == [True, False]
    assert client._openai_tools_unsupported is False

    async for _ in client._astream_openai_with_tools(with_image + [{"role": "user", "content": "再说说"}]):
        pass
    assert seen[2] is False  # still carries the image: no refused round-trip

    async for _ in client._astream_openai_with_tools([{"role": "user", "content": "纯文本"}]):
        pass
    assert seen[3] is True


@pytest.mark.asyncio
async def test_offline_openai_unrelated_error_is_not_treated_as_tools_unsupported():
    calls = []

    async def astream(_messages, **overrides):
        calls.append(1)
        raise RuntimeError("Error code: 500 - upstream overloaded")
        yield  # pragma: no cover

    client = _tools_declining_client(astream)
    with pytest.raises(RuntimeError, match="overloaded"):
        async for _ in client._astream_openai_with_tools([{"role": "user", "content": "hi"}]):
            pass
    assert len(calls) == 1
    assert client._openai_tools_unsupported is False


@pytest.mark.asyncio
async def test_switch_model_gives_tools_another_chance(monkeypatch):
    """Rejecting tools is a property of the old model; a new model starts
    with tools enabled again."""
    import main_logic.omni_offline_client._streaming as streaming_mod

    async def astream(_messages, **overrides):  # pragma: no cover - not streamed
        yield None

    client = _tools_declining_client(astream)
    client._openai_tools_unsupported = True
    client._model_switch_lock = None
    client.api_key = "k"
    client.vision_base_url = client.base_url
    client.vision_api_key = "k"
    client.max_response_length = 300
    client._genai_client = None
    client.provider_type = None

    async def fake_create(model, base_url, api_key, **kwargs):
        return client.llm

    monkeypatch.setattr(streaming_mod, "create_chat_llm_async", fake_create)
    await client.switch_model("qwen2.5:7b")

    assert client._openai_tools_unsupported is False
    assert client._openai_tools_payload() is not None


@pytest.mark.asyncio
async def test_offline_openai_path_persists_streamed_text_with_tool_calls():
    """When the OpenAI-compatible path yields text before tool_calls in one turn, the assistant
    history message's content must preserve that streamed text, matching the Gemini path.
    Some OpenAI-compatible providers (GLM-text, Qwen-text, etc.) really produce such streams.

    Regression coverage: CodeRabbit round 10 review on PR #1035."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    async def get_weather(args):
        return {"temp_c": 22}

    tool_def = ToolDefinition(
        name="get_weather", description="weather",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        handler=get_weather,
    )

    # First LLM call: yield text, then tool_call, and finally finish=tool_calls.
    chunks_call_1 = [
        LLMStreamChunk(content="让我查一下，", finish_reason=None),
        LLMStreamChunk(content="稍等。", finish_reason=None),
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0, "id": "call_w", "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
            }],
            finish_reason=None,
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    # Second call: the tool has executed; the model produces final text.
    chunks_call_2 = [
        LLMStreamChunk(content="22°C in Paris.", finish_reason="stop"),
    ]

    fake_llm = _FakeLLM([chunks_call_1, chunks_call_2])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"temp_c": 22})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "weather Paris"}]
    out_chunks = []
    async for ch in client._astream_with_tools(messages):
        out_chunks.append(ch)

    # Find the assistant message with tool_calls written to history.
    assistant_with_tools = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert "让我查一下，稍等。" in assistant_with_tools["content"], (
        "OpenAI-compat 路径 assistant.tool_calls 历史必须保留 streamed text，"
        "否则下一轮 LLM 看不到自己已说过的前半句"
    )


@pytest.mark.asyncio
async def test_offline_genai_path_drops_empty_name_function_calls(monkeypatch):
    """Match OpenAI's collect_tool_calls defense: the GenAI path must also discard streamed
    function_calls with an empty name, or it calls on_tool_call with an empty name
    and writes invalid tool_calls history back to messages, causing schema rejection
    on the next generate_content_stream.

    Regression coverage: CodeRabbit round 11 review on PR #1035."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    # Build a fake stream: one function_call has a name, another has an empty name.
    class _Part:
        def __init__(self, *, function_call=None, text=None):
            self.text = text
            self.function_call = function_call

    class _FunctionCall:
        def __init__(self, name, args, id_=""):
            self.name = name
            self.args = args
            self.id = id_

    class _Content:
        def __init__(self, parts): self.parts = parts

    class _Candidate:
        def __init__(self, content): self.content = content

    class _Chunk:
        def __init__(self, candidates): self.candidates = candidates; self.usage_metadata = None

    async def _fake_stream():
        # Same turn: one valid function_call + one with empty name.
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(function_call=_FunctionCall("good_tool", {"x": 1}, id_="c1")),
            _Part(function_call=_FunctionCall("", {"y": 2}, id_="c_empty")),  # Must be dropped.
        ]))])

    class _StreamWrapper:
        def __init__(self): self._gen = _fake_stream()
        def __aiter__(self): return self
        async def __anext__(self):
            return await self._gen.__anext__()

    call_count = [0]
    handler_calls: list = []

    class _FakeAioClient:
        class models:
            @staticmethod
            async def generate_content_stream(**_kw):
                call_count[0] += 1
                if call_count[0] == 1:
                    return _StreamWrapper()
                # Second round: tool executed; provide final text.
                async def _fin():
                    yield _Chunk(candidates=[_Candidate(_Content([
                        _Part(text="done")
                    ]))])
                w = _StreamWrapper()
                w._gen = _fin()
                return w

    class _FakeClient:
        aio = _FakeAioClient()
        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-2.5-flash"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False
    client.max_tool_iterations = 3
    client._genai_client = _FakeClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "x"}]
    async for _ in client._astream_genai_with_tools(messages):
        pass

    # Only good_tool should invoke the handler; the empty-name call is dropped.
    assert handler_calls == ["good_tool"], (
        f"GenAI 路径必须 drop 空 name 的 function_call，实际 handler 收到：{handler_calls}"
    )
    # The assistant.tool_calls written back to messages must also contain only good_tool.
    assistant_with_tools = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    names = [tc["function"]["name"] for tc in assistant_with_tools["tool_calls"]]
    assert names == ["good_tool"]


@pytest.mark.asyncio
async def test_set_tools_resets_genai_unsupported_flag():
    """set_tools must clear _genai_tools_unsupported, or hot-unloading broken tools cannot restore
    the genai path after the previous tool set triggered schema rejection.

    Regression coverage: CodeRabbit round 12 review on PR #1035."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._tool_definitions = [
        ToolDefinition(name="bad", description="", handler=lambda _: 0),
    ]
    client._genai_tools_unsupported = True  # Simulate an old tool having triggered schema rejection.

    # Hot-unload the broken tool.
    client.set_tools([])
    assert client._genai_tools_unsupported is False, (
        "set_tools 替换工具列表后必须清掉 unsupported 旗标，否则永远走不回 genai 路径"
    )

    # Registering a new tool must reset it too (the caller may supply a new one).
    client._genai_tools_unsupported = True
    client.set_tools([ToolDefinition(name="good", description="", handler=lambda _: 0)])
    assert client._genai_tools_unsupported is False


@pytest.mark.asyncio
async def test_stream_text_does_not_double_write_pretool_text(monkeypatch):
    """After stream_text persists a tool round inline in _astream_with_tools (including
    pre-tool text + tool_calls + tool result), the final AIMessage append
    must contain only post-tool text; otherwise pre-tool text is written twice to history (once
    in assistant.tool_calls.content and once in final AIMessage.content).

    Regression coverage: CodeRabbit round 12 review on PR #1035."""
    from utils.llm_client import LLMStreamChunk, AIMessage, SystemMessage
    from main_logic.omni_offline_client import OmniOfflineClient

    async def _astream_simulating_tool_round(self, messages, **overrides):
        # Pre-tool text from the tool round.
        yield LLMStreamChunk(content="正在查询，")
        # What _astream_*_with_tools does during inline persistence: append the tool round to history.
        messages.append({
            "role": "assistant",
            "content": "正在查询，",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "get_weather", "arguments": "{}"}}],
        })
        messages.append({
            "role": "tool", "tool_call_id": "c1", "name": "get_weather",
            "content": '{"t":22}',
        })
        # Notify upstream to clear the final segment.
        yield LLMStreamChunk(content="", tool_round_persisted=True)
        # Post-tool text (final reply).
        yield LLMStreamChunk(content="22 度。")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_simulating_tool_round)

    text_emitted: list = []
    async def fake_text_delta(text, is_first): text_emitted.append(text)
    async def noop(*_a, **_kw): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 9999
    client.max_response_rerolls = 0
    client.enable_response_guard = False
    client.vision_model = ""
    client.model = "x"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = None
    client.on_repetition_detected = None

    await client.stream_text("天气如何")

    # Complete user-visible text: pre-tool + post-tool.
    assert "".join(text_emitted) == "正在查询，22 度。"

    # Key assertion: pre-tool text must not be written to history twice.
    history = client._conversation_history
    # Expected structure: [system, user, assistant{content:"正在查询，", tool_calls:[...]}, tool, final-AIMessage].
    assistant_with_tool = next(
        m for m in history if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_with_tool["content"] == "正在查询，"
    final_ai = history[-1]
    assert isinstance(final_ai, AIMessage)
    # The final AIMessage must not contain pre-tool text already persisted.
    assert "正在查询" not in final_ai.content, (
        f"pre-tool 文本被双写进 history 了！final AIMessage.content={final_ai.content!r}"
    )
    assert final_ai.content == "22 度。"


@pytest.mark.asyncio
async def test_stream_text_length_guard_after_tool_call_does_not_double_write_pretool_text(monkeypatch):
    """When the length guard triggers after a tool round, append only unpersisted post-tool text to history.

    _astream_*_with_tools already wrote pre-tool text inline into assistant.tool_calls.content.
    Appending the whole turn again during recovery duplicates pre-tool text in the next round's context.
    """
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk, AIMessage, SystemMessage

    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming,
        "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )

    async def _astream_tool_then_long_reply(self, messages, **overrides):
        yield LLMStreamChunk(content="checking now.")
        messages.append({
            "role": "assistant",
            "content": "checking now.",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"}}],
        })
        messages.append({
            "role": "tool", "tool_call_id": "c1", "name": "lookup",
            "content": '{"ok":true}',
        })
        yield LLMStreamChunk(content="", tool_round_persisted=True)
        yield LLMStreamChunk(content="answer one. answer two overflow")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_tool_then_long_reply)

    text_emitted: list = []
    discarded_calls: list = []

    async def fake_text_delta(text, is_first):
        text_emitted.append(text)

    async def fake_notify_discarded(reason, attempt, max_attempts, will_retry, message=None):
        discarded_calls.append({
            "reason": reason,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "will_retry": will_retry,
            "message": message,
        })

    async def noop(*_a, **_kw):
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 5
    client.max_response_rerolls = 0
    client.enable_response_guard = True
    client.vision_model = ""
    client.model = "x"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = None
    client.on_repetition_detected = None

    await client.stream_text("lookup")

    assert "".join(text_emitted) == "checking now.answer one."
    assert discarded_calls == []

    history = client._conversation_history
    assistant_with_tool = next(
        m for m in history if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_with_tool["content"] == "checking now."
    final_ai = history[-1]
    assert isinstance(final_ai, AIMessage)
    assert final_ai.content == "answer one."


@pytest.mark.asyncio
async def test_stream_text_length_guard_after_tool_call_rejects_pretool_only_recovery(monkeypatch):
    """Before post-tool continuation has a complete sentence, pre-tool text alone cannot count as successful recovery."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk, AIMessage, SystemMessage

    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming,
        "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )

    async def _astream_tool_then_unfinished_overflow(self, messages, **overrides):
        yield LLMStreamChunk(content="checking now.")
        messages.append({
            "role": "assistant",
            "content": "checking now.",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"}}],
        })
        messages.append({
            "role": "tool", "tool_call_id": "c1", "name": "lookup",
            "content": '{"ok":true}',
        })
        yield LLMStreamChunk(content="", tool_round_persisted=True)
        yield LLMStreamChunk(content=" unfinished overflow")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream_tool_then_unfinished_overflow)

    text_emitted: list = []
    discarded_calls: list = []

    async def fake_text_delta(text, is_first):
        text_emitted.append(text)

    async def fake_notify_discarded(reason, attempt, max_attempts, will_retry, message=None):
        discarded_calls.append({
            "reason": reason,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "will_retry": will_retry,
            "message": message,
        })

    async def noop(*_a, **_kw):
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 2
    client.max_response_rerolls = 0
    client.enable_response_guard = True
    client.vision_model = ""
    client.model = "x"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = None
    client.on_repetition_detected = None

    await client.stream_text("lookup")

    assert "".join(text_emitted) == "checking now."
    assert len(discarded_calls) == 1
    assert discarded_calls[0]["will_retry"] is False
    assert json.loads(discarded_calls[0]["message"]) == {"code": "RESPONSE_TOO_LONG"}
    assert not any(isinstance(m, AIMessage) for m in client._conversation_history[1:])


@pytest.mark.asyncio
async def test_offline_iteration_cap_breaks_runaway_loop():
    """If the model keeps requesting tools forever, we stop after
    ``max_tool_iterations`` tool rounds and then do ONE forced-finalize
    call (tools removed) so the user gets a final answer instead of
    silence."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    async def loop_tool(args):
        return {"ok": True}

    tool = ToolDefinition(name="loop", description="", handler=loop_tool)

    # Every tool round returns another tool_call.
    def loop_chunks():
        return [
            LLMStreamChunk(
                content="",
                tool_call_deltas=[{
                    "index": 0, "id": "c", "type": "function",
                    "function": {"name": "loop", "arguments": "{}"},
                }],
                finish_reason=None,
            ),
            LLMStreamChunk(content="", finish_reason="tool_calls"),
        ]

    # The forced-finalize call (4th) can no longer call tools → returns text.
    final_text_chunks = [
        LLMStreamChunk(content="最终答案", finish_reason="stop"),
    ]
    fake_llm = _FakeLLM([loop_chunks(), loop_chunks(), loop_chunks(), final_text_chunks])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = fake_llm
    client._tool_definitions = [tool]
    client.max_tool_iterations = 3
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._last_finish_reason = None
    client._last_prompt_tokens = None

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "loop forever"}]
    streamed = ""
    async for c in client._astream_with_tools(messages):
        if getattr(c, "content", ""):
            streamed += c.content

    # max_tool_iterations tool rounds + 1 forced-finalize call.
    assert len(fake_llm.calls) == 4
    # Forced-finalize streamed real text instead of leaving the turn silent.
    assert "最终答案" in streamed
    # The forced-finalize call must NOT advertise tools/tool_choice.
    _final_overrides = fake_llm.calls[-1][1]
    assert "tools" not in _final_overrides
    assert "tool_choice" not in _final_overrides


def _bare_tool_client(fake_llm, tool_def, handler, *, cap):
    """Minimal OpenAI-path client for the tool-loop tests below."""
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.llm = fake_llm
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = cap
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False
    client._last_finish_reason = None
    client._last_prompt_tokens = None
    client.on_tool_call = handler
    return client


@contextlib.contextmanager
def _capture_ofc_logs():
    """Capture the omni_offline_client module logger's records directly.

    This logger has propagate=False via utils/logger_config, so caplog's
    root handler cannot capture it. Attach a temporary handler to the logger itself, then
    remove it and restore the level after the test."""  # noqa: DOCSTRING_CJK
    import logging

    from main_logic.omni_offline_client import _shared as _ofc_shared

    records: list = []

    class _ListHandler(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _ListHandler(level=logging.DEBUG)
    target = _ofc_shared.logger
    old_level = target.level
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        target.removeHandler(handler)
        target.setLevel(old_level)


@pytest.mark.asyncio
async def test_openai_nameless_fragment_round_fires_round_start_and_finalizes():
    """P2 reproduction: the provider streams tool_call fragments but function.name never arrives ->
    collect_tool_calls drops everything -> zero handler calls. This round must:
    1) Trigger on_tool_round_start (buffering callers use it to discard pre-tool text;
       cleanup only at the handler entry point never runs on this path);
    2) Not emit the tool_round_persisted sentinel (its contract says pre-tool text is already in
       history, which is false here; emitting it would remove this text from history entirely);
    3) Still reach forced-finalize to produce final text;
    4) Log the \"0 executed\" cap case separately (the failure most needing investigation must not
       masquerade as an ordinary cap log)."""  # noqa: DOCSTRING_CJK
    import logging

    from utils.llm_client import LLMStreamChunk
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    tool = ToolDefinition(name="lookup", description="", handler=handler)
    chunks_call_1 = [
        LLMStreamChunk(content="我查一下", finish_reason=None),
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0, "id": "c1", "type": "function",
                "function": {"name": "", "arguments": "{}"},
            }],
            finish_reason=None,
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_finalize = [LLMStreamChunk(content="最终回答", finish_reason="stop")]
    fake_llm = _FakeLLM([chunks_call_1, chunks_finalize])
    client = _bare_tool_client(fake_llm, tool, handler, cap=1)

    round_starts: list = []

    async def _round_start():
        round_starts.append(True)

    client.set_tool_round_start_callback(_round_start)

    messages = [{"role": "user", "content": "hi"}]
    streamed = ""
    sentinel_seen = False
    with _capture_ofc_logs() as records:
        async for c in client._astream_with_tools(messages):
            if getattr(c, "content", ""):
                streamed += c.content
            if getattr(c, "tool_round_persisted", False):
                sentinel_seen = True

    assert round_starts == [True], "进入 tool 轮必须触发 round-start 回调"
    assert handler_calls == [], "无名分片不得触达 handler"
    assert not sentinel_seen, (
        "零执行轮什么都没写进 history，sentinel 不得再宣称已持久化"
    )
    assert "最终回答" in streamed
    assert len(fake_llm.calls) == 2  # 1 tool round + 1 forced-finalize
    assert "tools" not in fake_llm.calls[-1][1]
    assert any(
        r.levelno == logging.WARNING and "0 executed" in r.getMessage()
        for r in records
    ), "零执行封顶必须打单列的 WARNING"


@pytest.mark.asyncio
async def test_openai_cap_one_normal_recall_logs_info_not_warning():
    """P3-1: cap=1 is the plugin's intended per-turn budget; one successful recall round necessarily exhausts the loop.
    The former unconditional WARNING emitted the main program's runaway cap signal on every normal recall,
    burying real cap events. Use INFO when cap=1 and a tool actually executed."""  # noqa: DOCSTRING_CJK
    import logging

    from utils.llm_client import LLMStreamChunk
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    tool = ToolDefinition(name="recall_memory", description="", handler=handler)
    chunks_call_1 = [
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0, "id": "c1", "type": "function",
                "function": {"name": "recall_memory", "arguments": "{}"},
            }],
            finish_reason=None,
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_finalize = [LLMStreamChunk(content="按群规是不剧透", finish_reason="stop")]
    fake_llm = _FakeLLM([chunks_call_1, chunks_finalize])
    client = _bare_tool_client(fake_llm, tool, handler, cap=1)

    messages = [{"role": "user", "content": "hi"}]
    streamed = ""
    with _capture_ofc_logs() as records:
        async for c in client._astream_with_tools(messages):
            if getattr(c, "content", ""):
                streamed += c.content

    assert handler_calls == ["recall_memory"]
    assert "按群规是不剧透" in streamed
    assert any(
        r.levelno == logging.INFO
        and "single tool round budget spent" in r.getMessage()
        for r in records
    )
    assert not any(
        r.levelno >= logging.WARNING and "iteration cap" in r.getMessage()
        for r in records
    ), "cap=1 的正常召回轮不得打 runaway 封顶 WARNING"


@pytest.mark.asyncio
async def test_openai_cap_multi_runaway_still_warns():
    """Third branch of the cap logs: true runaway with cap>1 and executed tools retains
    the original WARNING, preserving the main program's cap=3 signal despite the P3-1 downgrade."""  # noqa: DOCSTRING_CJK
    import logging

    from utils.llm_client import LLMStreamChunk
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    tool = ToolDefinition(name="loop", description="", handler=handler)

    def loop_chunks():
        return [
            LLMStreamChunk(
                content="",
                tool_call_deltas=[{
                    "index": 0, "id": "c", "type": "function",
                    "function": {"name": "loop", "arguments": "{}"},
                }],
                finish_reason=None,
            ),
            LLMStreamChunk(content="", finish_reason="tool_calls"),
        ]

    fake_llm = _FakeLLM([
        loop_chunks(), loop_chunks(), loop_chunks(),
        [LLMStreamChunk(content="最终答案", finish_reason="stop")],
    ])
    client = _bare_tool_client(fake_llm, tool, handler, cap=3)

    with _capture_ofc_logs() as records:
        async for _ in client._astream_with_tools(
            [{"role": "user", "content": "loop forever"}]
        ):
            pass

    assert any(
        r.levelno == logging.WARNING
        and "tool iteration cap 3 reached" in r.getMessage()
        and "0 executed" not in r.getMessage()
        for r in records
    )


@pytest.mark.asyncio
async def test_genai_nameless_fragment_round_fires_round_start_and_finalizes(
    monkeypatch,
):
    """Genai's symmetric P2 case (same bug, worse): empty-name function_calls are dropped
    during collection, leaving collected empty. Previously this looked like the model had stopped calling tools
    and returned directly: no sentinel, forced-finalize, or cap log; pre-tool
    \"我查一下\" became the entire outgoing reply. Now round-start still fires (buffering
    callers use it to discard pre-tool text), this round consumes an iteration, and forced-
    finalize produces final text after the cap."""  # noqa: DOCSTRING_CJK
    import logging

    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    class _Part:
        def __init__(self, *, function_call=None, text=None):
            self.text = text
            self.function_call = function_call
            self.thought = False

    class _FunctionCall:
        def __init__(self, name, args, id_=""):
            self.name = name
            self.args = args
            self.id = id_

    class _Content:
        def __init__(self, parts): self.parts = parts

    class _Candidate:
        def __init__(self, content):
            self.content = content
            self.finish_reason = None

    class _Chunk:
        def __init__(self, candidates):
            self.candidates = candidates
            self.usage_metadata = None
            self.prompt_feedback = None

    def _wrap(gen):
        class _StreamWrapper:
            def __aiter__(self): return self
            async def __anext__(self):
                return await gen.__anext__()
        return _StreamWrapper()

    call_count = [0]
    handler_calls: list = []

    async def _round_1():
        # Pre-tool text + function_calls with only empty names (all dropped).
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(text="我查一下"),
            _Part(function_call=_FunctionCall("", {"y": 2}, id_="c_empty")),
        ]))])

    async def _finalize():
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(text="最终回答"),
        ]))])

    class _FakeAioClient:
        class models:
            @staticmethod
            async def generate_content_stream(**_kw):
                call_count[0] += 1
                return _wrap(_round_1() if call_count[0] == 1 else _finalize())

    class _FakeClient:
        aio = _FakeAioClient()
        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-2.5-flash"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False
    client.max_tool_iterations = 1
    client._genai_client = _FakeClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    round_starts: list = []

    async def _round_start():
        round_starts.append(True)

    client.set_tool_round_start_callback(_round_start)

    streamed = ""
    with _capture_ofc_logs() as records:
        async for c in client._astream_genai_with_tools(
            [{"role": "user", "content": "x"}]
        ):
            if getattr(c, "content", ""):
                streamed += c.content

    assert round_starts == [True], "genai 路径进入 tool 轮也必须触发 round-start"
    assert handler_calls == [], "空 name 的 function_call 不得触达 handler"
    assert call_count[0] == 2, "该轮必须消耗一次迭代并走到 forced-finalize"
    assert "最终回答" in streamed, (
        "旧行为在这里直接 return，pre-tool 文本成为整条回复"
    )
    assert any(
        r.levelno == logging.WARNING and "0 executed" in r.getMessage()
        for r in records
    )


@pytest.mark.asyncio
async def test_genai_cap_one_normal_recall_logs_info_not_warning(monkeypatch):
    """Genai's normal cap=1 branch of the three-way cap log (matching OpenAI): one
    successfully executed tool round exhausting cap=1 must log INFO rather than WARNING, or every
    normal recall on the plugin's genai path emits a runaway cap signal."""  # noqa: DOCSTRING_CJK
    import logging

    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    class _Part:
        def __init__(self, *, function_call=None, text=None):
            self.text = text
            self.function_call = function_call
            self.thought = False

    class _FunctionCall:
        def __init__(self, name, args, id_=""):
            self.name = name
            self.args = args
            self.id = id_

    class _Content:
        def __init__(self, parts): self.parts = parts

    class _Candidate:
        def __init__(self, content):
            self.content = content
            self.finish_reason = None

    class _Chunk:
        def __init__(self, candidates):
            self.candidates = candidates
            self.usage_metadata = None
            self.prompt_feedback = None

    def _wrap(gen):
        class _StreamWrapper:
            def __aiter__(self): return self
            async def __anext__(self):
                return await gen.__anext__()
        return _StreamWrapper()

    call_count = [0]
    handler_calls: list = []

    async def _round_1():
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(function_call=_FunctionCall("recall_memory", {"query": "x"}, id_="c1")),
        ]))])

    async def _finalize():
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(text="按群规是不剧透"),
        ]))])

    class _FakeAioClient:
        class models:
            @staticmethod
            async def generate_content_stream(**_kw):
                call_count[0] += 1
                return _wrap(_round_1() if call_count[0] == 1 else _finalize())

    class _FakeClient:
        aio = _FakeAioClient()
        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-2.5-flash"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False
    client.max_tool_iterations = 1
    client._genai_client = _FakeClient()
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    streamed = ""
    with _capture_ofc_logs() as records:
        async for c in client._astream_genai_with_tools(
            [{"role": "user", "content": "x"}]
        ):
            if getattr(c, "content", ""):
                streamed += c.content

    assert handler_calls == ["recall_memory"]
    assert call_count[0] == 2
    assert "按群规是不剧透" in streamed
    assert any(
        r.levelno == logging.INFO
        and "single tool round budget spent" in r.getMessage()
        for r in records
    )
    assert not any(
        r.levelno >= logging.WARNING and "iteration cap" in r.getMessage()
        for r in records
    ), "genai cap=1 的正常召回轮不得打 runaway 封顶 WARNING"


class _GenaiPart:
    def __init__(self, *, function_call=None, text=None):
        self.text = text
        self.function_call = function_call
        self.thought = False


class _GenaiFunctionCall:
    def __init__(self, name, args=None, id_=""):
        self.name = name
        self.args = args or {}
        self.id = id_


class _GenaiChunk:
    def __init__(self, parts):
        from types import SimpleNamespace

        self.candidates = [SimpleNamespace(
            content=SimpleNamespace(parts=parts), finish_reason=None,
        )]
        self.usage_metadata = None
        self.prompt_feedback = None


def _genai_client_stub(rounds, finalize_parts=None):
    """A genai client whose n-th tool-round stream yields ``rounds[n]``.

    When ``finalize_parts`` is not None, identify forced-finalize by **whether the request contains tools**
    (that deliberately tool-free call), rather than its call index. The criterion must be
    the request itself: the behavior under test is how many loop rounds execute. Selecting scripts by index
    makes one-round and three-round implementations read different script entries, hiding correctness differences.

    Repeat the last round after exhausting the script. Return (client, calls); calls accumulates call counts."""  # noqa: DOCSTRING_CJK
    calls: list = []

    async def _one(parts):
        yield _GenaiChunk(parts)

    class _StreamWrapper:
        def __init__(self, gen):
            self._gen = gen

        def __aiter__(self):
            return self

        async def __anext__(self):
            return await self._gen.__anext__()

    class _FakeAioClient:
        class models:
            @staticmethod
            async def generate_content_stream(**kw):
                index = len(calls)
                calls.append(index)
                if (
                    finalize_parts is not None
                    and not getattr(kw.get("config"), "tools", None)
                ):
                    return _StreamWrapper(_one(finalize_parts))
                return _StreamWrapper(_one(rounds[min(index, len(rounds) - 1)]))

    class _FakeClient:
        aio = _FakeAioClient()

        def close(self):
            pass

    return _FakeClient(), calls


def _bare_genai_client(rounds, handler, *, cap, finalize_parts=None):
    """Minimal genai-path client for the tool-loop tests below.

    When ``finalize_parts`` is not None, install a real ToolDefinition: only requests
    that actually include tools let the stub distinguish forced-finalize (the tool-free call) from ordinary
    tool rounds."""  # noqa: DOCSTRING_CJK
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.model = "gemini-2.5-flash"
    client.api_key = "fake"
    if finalize_parts is None:
        client._tool_definitions = []
        client.has_tools = lambda: False
    else:
        client._tool_definitions = [ToolDefinition(
            name="recall_memory", description="recall",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )]
        client.has_tools = lambda: True
    client.max_tool_iterations = cap
    client._genai_tools_unsupported = False
    client.llm = type("F", (), {"max_completion_tokens": 100})()
    client.on_tool_call = handler
    genai_client, calls = _genai_client_stub(rounds, finalize_parts)
    client._genai_client = genai_client
    return client, calls


@pytest.mark.asyncio
async def test_cancelled_genai_tool_round_does_not_start_another_provider_call(monkeypatch):
    """The native Gemini loop obeys the same cancellation generation boundary."""
    from main_logic.tool_calling import ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    client = None

    async def cancel_in_tool(_call):
        await client.cancel_response()
        return ToolResult(call_id="call_1", name="recall_memory", output={"ok": True})

    client, calls = _bare_genai_client(
        [[_GenaiPart(function_call=_GenaiFunctionCall("recall_memory", id_="call_1"))]],
        cancel_in_tool,
        cap=3,
    )
    client._use_genai_sdk = True
    generation = client._begin_response_generation()

    # Enter through the production router: this is the regression boundary for
    # forwarding the private generation token into the Gemini implementation.
    async for _chunk in client._astream_with_tools(
        [{"role": "user", "content": "remember this"}],
        _response_generation=generation,
    ):
        pass

    assert calls == [0]
    assert client._response_generation_is_active(generation) is False


@pytest.mark.asyncio
async def test_genai_image_budget_omission_refreshes_tool_response(monkeypatch):
    from main_logic.tool_calling import ToolCall, ToolImage, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            name=call.name,
            output={"ok": True},
            images=[ToolImage(data_b64=call.name.upper())],
        )

    client, _calls = _bare_genai_client(
        [
            [
                _GenaiPart(
                    function_call=_GenaiFunctionCall(name, {}, id_=name)
                )
                for name in ("first", "second", "third")
            ],
            [_GenaiPart(text="done")],
        ],
        handler,
        cap=2,
    )
    client._user_language_provider = lambda: "en"
    messages = [{"role": "user", "content": "inspect the images"}]

    async for _chunk in client._astream_genai_with_tools(messages):
        pass

    third_result = next(
        message
        for message in messages
        if message.get("role") == "tool" and message.get("name") == "third"
    )
    payload = json.loads(third_result["content"])
    assert any(
        "omitted" in warning and "turn image budget" in warning
        for warning in payload["_image_warnings"]
    )


@pytest.mark.asyncio
async def test_genai_tool_loop_rebuilds_a_client_a_tool_handler_dropped(monkeypatch):
    """A tool that hands back a picture makes the host call
    ``prepare_for_tool_images``, whose ``switch_model`` drops
    ``_genai_client`` -- the vision slot may carry its own key. The loop has
    to rebuild it before the next round rather than dereference None."""
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def handler(call: ToolCall) -> ToolResult:
        # Exactly what switch_model does to the client mid tool loop.
        client._genai_client = None
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client, calls = _bare_genai_client(
        [
            [_GenaiPart(function_call=_GenaiFunctionCall("inspect", {}, id_="c1"))],
            [_GenaiPart(text="done")],
        ],
        handler,
        cap=2,
    )
    stub = client._genai_client
    rebuilt: list = []

    class _FakeGenaiModule:
        @staticmethod
        def Client(**kwargs):
            rebuilt.append(kwargs)
            return stub

    monkeypatch.setattr(_ofc_genai, "_genai", _FakeGenaiModule)

    chunks = [
        chunk
        async for chunk in client._astream_genai_with_tools(
            [{"role": "user", "content": "look at this"}]
        )
    ]

    assert rebuilt, "the loop never rebuilt the client the tool handler dropped"
    assert len(calls) == 2, "the round after the tool call never ran"
    assert any(getattr(chunk, "content", "") == "done" for chunk in chunks)


class _ToolAwareFakeLLM:
    """OpenAI-path fake: select scripts by the presence of tools in the request, not call index.

    Same reason as :func:`_genai_client_stub`: forced-finalize is the only call without
    tools, and the behavior under test is how many loop rounds execute."""  # noqa: DOCSTRING_CJK

    def __init__(self, tool_round_factory, finalize_chunks,
                 max_completion_tokens=100):
        self._tool_round_factory = tool_round_factory
        self._finalize = finalize_chunks
        self.calls = []
        self.max_completion_tokens = max_completion_tokens

    def astream(self, messages, **overrides):
        self.calls.append((messages, overrides))
        chunks = (
            self._finalize if not overrides.get("tools")
            else self._tool_round_factory()
        )
        return _FakeAsyncStream(list(chunks))

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_openai_round_start_fires_after_leak_filter_tail():
    """Ordering invariant: emit all pre-tool text held by the leak filter **before** the round-start
    callback.

    The callback is the buffering caller's ``reply_chunks.clear()``. Notifying before yielding
    the tail puts the held fragment into a freshly cleared buffer; it still becomes part of this round's
    outgoing reply, defeating the filter. Each path (OpenAI / genai) needs this guard,
    or reversing either path's order leaves all tests green."""  # noqa: DOCSTRING_CJK
    from utils.llm_client import LLMStreamChunk
    from utils.llm_tool_leak_filter import ToolLeakFilter
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    tool = ToolDefinition(name="recall_memory", description="", handler=handler)
    chunks_call_1 = [
        LLMStreamChunk(content="我查一下"),
        # A partial text fragment resembling a tail: the filter holds it until finalize releases it.
        LLMStreamChunk(content="<see"),
        LLMStreamChunk(
            content="",
            tool_call_deltas=[{
                "index": 0, "id": "c1", "type": "function",
                "function": {"name": "recall_memory", "arguments": "{}"},
            }],
        ),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    fake_llm = _FakeLLM([
        chunks_call_1,
        [LLMStreamChunk(content="最终回答", finish_reason="stop")],
    ])
    client = _bare_tool_client(fake_llm, tool, handler, cap=2)

    events: list = []

    async def _round_start():
        events.append(("round_start",))

    client.set_tool_round_start_callback(_round_start)

    async for chunk in client._astream_with_tools(
        [{"role": "user", "content": "hi"}],
        _tool_leak_filter=ToolLeakFilter(tool_names={"recall_memory"}),
        _tool_leak_provider="free",
    ):
        if getattr(chunk, "content", ""):
            events.append(("chunk", chunk.content))

    assert events.index(("chunk", "<see")) < events.index(("round_start",)), (
        f"被 leak filter 扣住的 tail 在清理之后才 yield，照样外发: {events!r}"
    )


@pytest.mark.asyncio
async def test_genai_round_start_fires_after_leak_filter_tail(monkeypatch):
    """The same ordering invariant on genai (symmetric with OpenAI).

    #2597 notified before finalizing on genai; observed outgoing text was '<see最终回答',
    while OpenAI produced only '最终回答' for the same input."""  # noqa: DOCSTRING_CJK
    from utils.llm_tool_leak_filter import ToolLeakFilter
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client, _calls = _bare_genai_client(
        [
            [
                _GenaiPart(text="我查一下"),
                _GenaiPart(text="<see"),
                _GenaiPart(function_call=_GenaiFunctionCall(
                    "recall_memory", {"query": "x"}, id_="c1",
                )),
            ],
            [_GenaiPart(text="最终回答")],
        ],
        handler, cap=2,
    )

    events: list = []

    async def _round_start():
        events.append(("round_start",))

    client.set_tool_round_start_callback(_round_start)

    async for chunk in client._astream_genai_with_tools(
        [{"role": "user", "content": "x"}],
        _tool_leak_filter=ToolLeakFilter(tool_names={"recall_memory"}),
        _tool_leak_provider="free",
    ):
        if getattr(chunk, "content", ""):
            events.append(("chunk", chunk.content))

    assert events.index(("chunk", "<see")) < events.index(("round_start",)), (
        f"genai 侧被扣住的 tail 在清理之后才 yield: {events!r}"
    )


@pytest.mark.asyncio
async def test_genai_zero_execution_round_does_not_replay_streamed_text(
    monkeypatch,
):
    """Zero-execution rounds must not amplify text already streamed to the user.

    Dropping every function_call fragment for empty names leaves messages entirely unchanged; another
    round adds no information and only streams the same pre-tool text again. With cap=3,
    the main program actually produced '我查一下我查一下我查一下最终回答'.

    The purpose of #2597's branch (avoid early return; reach forced-finalize and cap
    logging) is fully preserved by break; only pointless retries are removed."""  # noqa: DOCSTRING_CJK
    import logging

    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    nameless_round = [
        _GenaiPart(text="我查一下"),
        _GenaiPart(function_call=_GenaiFunctionCall("", {"y": 2}, id_="c_empty")),
    ]
    client, calls = _bare_genai_client(
        # Each tool request round has text + unnamed fragments; forced-finalize (the only
        # request without tools) uses a separate script. Amplification changes only the call count.
        [nameless_round], handler, cap=3,
        finalize_parts=[_GenaiPart(text="最终回答")],
    )

    streamed = ""
    with _capture_ofc_logs() as records:
        async for c in client._astream_genai_with_tools(
            [{"role": "user", "content": "x"}]
        ):
            if getattr(c, "content", ""):
                streamed += c.content

    assert handler_calls == []
    assert streamed.count("我查一下") == 1, (
        f"pre-tool 文本被重放了 {streamed.count('我查一下')} 次: {streamed!r}"
    )
    assert "最终回答" in streamed, "forced-finalize 兜底不得因为提前跳出而丢失"
    assert len(calls) == 2, (
        f"零执行轮之后只该有 forced-finalize 一次调用，实际 {len(calls)} 次"
    )
    assert any(
        r.levelno == logging.WARNING and "0 executed" in r.getMessage()
        for r in records
    ), "零执行的封顶信号不得因为提前跳出而消失"
    assert not any(
        "tool iteration cap" in r.getMessage() for r in records
    ), "第 1 轮就跳出了，日志不得谎称迭代被耗尽"


@pytest.mark.asyncio
async def test_openai_zero_execution_round_does_not_replay_streamed_text():
    """The same OpenAI case (symmetric with genai): zero executions + already streamed text ->
    go straight to forced-finalize without replay."""  # noqa: DOCSTRING_CJK
    import logging

    from utils.llm_client import LLMStreamChunk
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    tool = ToolDefinition(name="lookup", description="", handler=handler)

    def _nameless_round():
        return [
            LLMStreamChunk(content="我查一下"),
            LLMStreamChunk(
                content="",
                tool_call_deltas=[{
                    "index": 0, "id": "c1", "type": "function",
                    "function": {"name": "", "arguments": "{}"},
                }],
            ),
            LLMStreamChunk(content="", finish_reason="tool_calls"),
        ]

    fake_llm = _ToolAwareFakeLLM(
        _nameless_round,
        [LLMStreamChunk(content="最终回答", finish_reason="stop")],
    )
    client = _bare_tool_client(fake_llm, tool, handler, cap=3)

    streamed = ""
    with _capture_ofc_logs() as records:
        async for c in client._astream_with_tools(
            [{"role": "user", "content": "hi"}]
        ):
            if getattr(c, "content", ""):
                streamed += c.content

    assert handler_calls == []
    assert streamed.count("我查一下") == 1, (
        f"pre-tool 文本被重放了 {streamed.count('我查一下')} 次: {streamed!r}"
    )
    assert "最终回答" in streamed
    assert len(fake_llm.calls) == 2
    assert any(
        r.levelno == logging.WARNING and "0 executed" in r.getMessage()
        for r in records
    )
    assert not any(
        "tool iteration cap" in r.getMessage() for r in records
    )


@pytest.mark.asyncio
async def test_genai_zero_execution_round_without_text_still_retries(monkeypatch):
    """A zero-execution round that streamed nothing to the user may still retry.

    Retrying has no visible cost (no text to replay), and an SDK glitch may genuinely clear
    next round. An implementation removing the already-streamed-text criterion entirely must fail
    either this test or the preceding one."""  # noqa: DOCSTRING_CJK
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client, calls = _bare_genai_client(
        [
            # First round: unnamed fragments, zero text -> allow another attempt.
            [_GenaiPart(function_call=_GenaiFunctionCall("", id_="c_empty"))],
            # Second round: the SDK recovers and the tool executes.
            [_GenaiPart(function_call=_GenaiFunctionCall(
                "recall_memory", {"query": "x"}, id_="c1",
            ))],
            [_GenaiPart(text="最终回答")],
        ],
        handler, cap=3,
    )

    streamed = ""
    async for c in client._astream_genai_with_tools(
        [{"role": "user", "content": "x"}]
    ):
        if getattr(c, "content", ""):
            streamed += c.content

    assert handler_calls == ["recall_memory"], (
        "零文本的零执行轮被直接跳出，下一轮本来能恢复的工具调用没了"
    )
    assert "最终回答" in streamed
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_genai_zero_execution_round_retries_when_text_was_fully_filtered(
    monkeypatch,
):
    """When the leak filter suppresses the whole round, the user has seen nothing: retries remain allowed.

    If the model emits only tool-call marker text, the filter recognizes the leak and swallows it all (feed and
    finalize both return empty), yet `had_text` remains true. Using had_text to mean text was already
    streamed needlessly abandons a retry that could yield a valid call next round
    (Codex P2). The criterion must be **actually yielding nonempty text**.

    OpenAI naturally has this meaning (streamed_text_buffer accumulates filtered
    text); this test keeps genai aligned."""  # noqa: DOCSTRING_CJK
    from utils.llm_tool_leak_filter import ToolLeakFilter
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    leak = (
        'recall_memory</name><parameter name="query">secret</parameter>'
        '</function></seed:tool_call>'
    )
    client, calls = _bare_genai_client(
        [
            # First round: text consists entirely of leak markers (all swallowed by the filter) + unnamed fragments.
            [
                _GenaiPart(text=leak),
                _GenaiPart(function_call=_GenaiFunctionCall("", id_="c_empty")),
            ],
            # Second round: the model produces a valid call, reachable only if retries are allowed.
            [_GenaiPart(function_call=_GenaiFunctionCall(
                "recall_memory", {"query": "x"}, id_="c1",
            ))],
            [_GenaiPart(text="最终回答")],
        ],
        handler, cap=3,
    )

    streamed = ""
    async for c in client._astream_genai_with_tools(
        [{"role": "user", "content": "x"}],
        _tool_leak_filter=ToolLeakFilter(tool_names={"recall_memory"}),
        _tool_leak_provider="free",
    ):
        if getattr(c, "content", ""):
            streamed += c.content

    assert handler_calls == ["recall_memory"], (
        "整段被过滤掉的那一轮被当成「已经流过文本」，白白放弃了重试"
    )
    assert "secret" not in streamed
    assert "最终回答" in streamed
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_genai_zero_execution_round_counts_the_leak_filter_tail_as_visible(
    monkeypatch,
):
    """The only text emitted this round is the leak filter's final tail: the user still saw it.

    Contrast the previous case: recognized leaks are swallowed entirely (unseen, retry allowed),
    but a partial fragment held for more context is released on finalize (seen, so retrying
    would repeat it next round). An implementation inspecting only feed returns and missing the tail
    must fail either this test or the preceding one."""  # noqa: DOCSTRING_CJK
    from utils.llm_tool_leak_filter import ToolLeakFilter
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client, calls = _bare_genai_client(
        [[
            # '<see' is a partial potential marker start: feed holds it and finalize releases it.
            _GenaiPart(text="<see"),
            _GenaiPart(function_call=_GenaiFunctionCall("", id_="c_empty")),
        ]],
        handler, cap=3,
        finalize_parts=[_GenaiPart(text="最终回答")],
    )

    streamed = ""
    async for c in client._astream_genai_with_tools(
        [{"role": "user", "content": "x"}],
        _tool_leak_filter=ToolLeakFilter(tool_names={"recall_memory"}),
        _tool_leak_provider="free",
    ):
        if getattr(c, "content", ""):
            streamed += c.content

    assert handler_calls == []
    assert streamed.count("<see") == 1, (
        f"收尾吐出的 tail 没算进「用户看见了」，同一段文本被重放: {streamed!r}"
    )
    assert len(calls) == 2, (
        f"零执行轮之后只该有 forced-finalize 一次调用，实际 {len(calls)} 次"
    )


@pytest.mark.asyncio
async def test_zero_execution_round_still_retries_for_buffering_callers(
    monkeypatch,
):
    """Buffering callers with a round-start callback still retry zero-execution rounds.

    That callback discards pre-tool text (the QQ recall path's
    ``reply_chunks.clear()``), and it runs before this decision; the text never
    reached the user, so replay is not an issue. A break would needlessly abandon a recoverable
    tool call, precisely the recall round, leaving this reply without memory results (Codex).

    Contrast ``test_genai_zero_execution_round_does_not_replay_streamed_text``:
    without a callback, text really was streamed and break is appropriate."""  # noqa: DOCSTRING_CJK
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    handler_calls: list = []

    async def handler(call: ToolCall) -> ToolResult:
        handler_calls.append(call.name)
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client, calls = _bare_genai_client(
        [
            # First round: pre-tool text + unnamed fragments (zero executions).
            [
                _GenaiPart(text="我查一下"),
                _GenaiPart(function_call=_GenaiFunctionCall("", id_="c_empty")),
            ],
            # Second round: provider recovers, reachable only if retries are allowed.
            [_GenaiPart(function_call=_GenaiFunctionCall(
                "recall_memory", {"query": "x"}, id_="c1",
            ))],
            [_GenaiPart(text="按群规是不剧透")],
        ],
        handler, cap=3,
    )

    discarded: list = []

    async def _round_start():
        # A buffering caller discards all pre-tool text for this round here.
        discarded.append(True)

    client.set_tool_round_start_callback(_round_start)

    streamed = ""
    async for c in client._astream_genai_with_tools(
        [{"role": "user", "content": "x"}]
    ):
        if getattr(c, "content", ""):
            streamed += c.content

    assert handler_calls == ["recall_memory"], (
        "缓冲型调用方的零执行轮被 break 掉了——那截文本本来就没外发，"
        "白丢一次召回"
    )
    assert "按群规是不剧透" in streamed
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_genai_cap_multi_runaway_still_warns(monkeypatch):
    """Genai's third cap-log branch (symmetric with OpenAI): true runaway with cap>1 and executed
    tools retains WARNING, preserving the main program's cap=3 signal despite the cap=1
    INFO downgrade. Without this test, changing ``== 1`` to ``>= 1`` leaves all tests green."""  # noqa: DOCSTRING_CJK
    import logging

    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    loop_round = [_GenaiPart(function_call=_GenaiFunctionCall(
        "loop", {"n": 1}, id_="c",
    ))]
    client, calls = _bare_genai_client(
        [loop_round, loop_round, loop_round, [_GenaiPart(text="最终答案")]],
        handler, cap=3,
    )

    with _capture_ofc_logs() as records:
        async for _ in client._astream_genai_with_tools(
            [{"role": "user", "content": "loop forever"}]
        ):
            pass

    assert len(calls) == 4  # Three tool rounds + one forced-finalize.
    assert any(
        r.levelno == logging.WARNING
        and "tool iteration cap 3 reached" in r.getMessage()
        and "0 executed" not in r.getMessage()
        for r in records
    ), "cap=3 的真 runaway 封顶信号被降级了"


# ---------------------------------------------------------------------------
# 4. OmniRealtimeClient wire-format helpers
# ---------------------------------------------------------------------------

def _make_rt_client(api_type: str, *, tool_name: str = "x", tool_kwargs=None):
    """Build a partially-initialized OmniRealtimeClient for wire-format
    tests. Bypasses ``__init__`` and only sets the fields the wire path
    reads, plus a fake ``send_event`` that just appends to a list.

    Returns ``(client, sent)`` where ``sent`` is the captured event list.
    """
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._api_type = api_type
    client._is_gemini = False
    client._gemini_session = None
    client.ws = object()  # any non-None — triggers the "connected" branch
    client._fatal_error_occurred = False
    tk = tool_kwargs or {}
    client._tool_definitions = [ToolDefinition(
        name=tool_name,
        description=tk.get("description", ""),
        parameters=tk.get("parameters", {"type": "object", "properties": {}}),
        handler=lambda _: 0,
    )]
    client.on_tool_call = lambda _c: None  # truthy → has_tools() == True
    sent: list = []

    async def fake_send_event(ev, _sent=sent):
        _sent.append(ev)

    client.send_event = fake_send_event
    return client, sent


def test_realtime_tools_for_step_uses_nested_function_shape():
    client, _ = _make_rt_client("step", tool_kwargs={"description": "d"})
    out = client._tools_for_step()
    assert out == [{
        "type": "function",
        "function": {"name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}},
    }]


def test_realtime_tools_for_openai_realtime_is_flat():
    client, _ = _make_rt_client("gpt", tool_kwargs={"description": "d"})
    out = client._tools_for_openai_realtime()
    assert out == [{"type": "function", "name": "x", "description": "d",
                    "parameters": {"type": "object", "properties": {}}}]


def test_realtime_tools_for_qwen_uses_nested_function_shape():
    """Qwen-Omni-Realtime shares StepFun's schema (nested function shape),
    unlike GLM/OpenAI Realtime's flat shape. Aliyun documentation specifies this explicitly."""
    client, _ = _make_rt_client(
        "qwen",
        tool_name="get_weather",
        tool_kwargs={
            "description": "天气",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    )
    out = client._tools_for_qwen()
    assert out == [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "天气",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        },
    }]


@pytest.mark.asyncio
async def test_realtime_glm_tool_result_must_not_carry_call_id():
    """GLM protocol: function_call_arguments.done returns no call_id (we synthesize
    glm_<rid>_<idx> for internal tracking), and the server rejects call_id in
    function_call_output responses. This test prevents synthetic ids from leaking onto the wire."""
    from main_logic.tool_calling import ToolResult

    client, sent = _make_rt_client("glm")
    await client._send_tool_result_openai_realtime(ToolResult(
        call_id="glm_resp123_0",  # Internally synthesized placeholder id.
        name="phoneCall",
        output={"ok": True},
    ))

    assert len(sent) == 2  # conversation.item.create + response.create
    item_event = sent[0]
    assert item_event["type"] == "conversation.item.create"
    item = item_event["item"]
    assert item["type"] == "function_call_output"
    assert "output" in item
    assert "call_id" not in item, (
        "GLM function_call_output 不能带 call_id —— 文档示例只有 output 字段，"
        "合成的 glm_xxx 仅供内部追踪"
    )
    # The arbiter stamps a client event_id at enqueue time (previously
    # send_event added the same field at send time), so compare without it.
    assert sent[1]["type"] == "response.create"
    assert set(sent[1]) <= {"type", "event_id"}


@pytest.mark.asyncio
async def test_realtime_qwen_tool_result_carries_call_id():
    """Qwen / OpenAI gpt / StepFun must return call_id so the server can associate the result with function_call."""
    from main_logic.tool_calling import ToolResult

    for api in ("qwen", "gpt", "step", "free"):
        client, sent = _make_rt_client(api)
        await client._send_tool_result_openai_realtime(ToolResult(
            call_id="call_abc",
            name="get_weather",
            output="北京：晴",
        ))
        item = sent[0]["item"]
        assert item.get("call_id") == "call_abc", (
            f"api={api} 必须保留 call_id 字段"
        )


@pytest.mark.asyncio
async def test_realtime_apply_tools_to_session_glm_includes_turn_detection():
    """GLM documentation requires turn_detection whenever tools are updated under ServerVAD,
    or the server may reset turn_detection to its default."""
    client, sent = _make_rt_client("glm")
    await client.apply_tools_to_session()
    # update_session is actually send_event({type:"session.update", session:...}).
    assert len(sent) == 1
    assert sent[0]["type"] == "session.update"
    sess = sent[0]["session"]
    assert "tools" in sess
    assert sess.get("turn_detection") == {"type": "server_vad"}, (
        "GLM 必须同时传 turn_detection"
    )
    assert sess.get("beta_fields") == {
        "chat_mode": "video_passive",
        "auto_search": True,
    }, (
        "GLM 局部 session.update 必须带回 beta_fields，否则服务端把 "
        "video_passive 打回 audio，下游重连超限后关掉连接"
    )


def test_glm_realtime_gateway_model_keeps_allowlisted_names():
    from main_logic.omni_realtime_client._shared import glm_realtime_gateway_model

    public = "wss://open.bigmodel.cn/api/paas/v4/realtime"
    assert glm_realtime_gateway_model("glm-realtime-air", public) == "glm-realtime-air"
    assert glm_realtime_gateway_model("glm-realtime-flash", public) == "glm-realtime-flash"
    assert glm_realtime_gateway_model("glm-realtime", public) == "glm-realtime"
    assert glm_realtime_gateway_model("glm-realtime-plus", public) == "glm-realtime-air"
    assert glm_realtime_gateway_model("  glm-realtime-plus  ", public) == "glm-realtime-air"


def test_glm_realtime_gateway_model_leaves_custom_endpoints_alone():
    """A proxy or self-hosted endpoint may route by ?model=; keep the configured name."""
    from main_logic.omni_realtime_client._shared import glm_realtime_gateway_model

    for url in ("wss://proxy.example.com/v4/realtime", "ws://127.0.0.1:8080/realtime", ""):
        assert glm_realtime_gateway_model("glm-realtime-plus", url) == "glm-realtime-plus"


@pytest.mark.asyncio
async def test_realtime_glm_partial_update_pins_requested_model():
    """Omitting model resets the GLM session to glm-realtime; Plus must ride every update."""
    client, sent = _make_rt_client("glm")
    client.model = "glm-realtime-plus"
    await client.update_session({"tools": []})
    sess = sent[0]["session"]
    assert sess["model"] == "glm-realtime-plus"
    assert "beta_fields" in sess


@pytest.mark.asyncio
async def test_realtime_apply_tools_to_session_qwen_disables_enable_search():
    """Qwen-Omni-Realtime: tools and enable_search are mutually exclusive; custom tool registration
    must explicitly set enable_search=False, or the server rejects session.update."""
    client, sent = _make_rt_client("qwen")
    await client.apply_tools_to_session()
    sess = sent[0]["session"]
    assert sess.get("enable_search") is False, (
        "Qwen tools / enable_search 互斥，已注册工具时必须显式关闭搜索"
    )
    # Tools must use the nested function shape.
    assert sess["tools"][0]["type"] == "function"
    assert "function" in sess["tools"][0]
    assert "name" in sess["tools"][0]["function"]


@pytest.mark.asyncio
async def test_realtime_apply_tools_to_session_step_emits_function_tools_only():
    """stepaudio-2.5-realtime no longer supports builtin web_search;
    apply_tools_to_session sends only caller-registered function tools, consistent with
    the update_session initialization path."""
    client, sent = _make_rt_client("step")
    await client.apply_tools_to_session()
    tools = sent[0]["session"]["tools"]
    assert all(t.get("type") != "web_search" for t in tools)
    assert any(t.get("type") == "function" for t in tools)


# ============================================================================
# Summary mode (emotion-tier summary path for long replies).
# ============================================================================

def _build_summary_client(monkeypatch, *, max_response_length: int = 4):
    """Build an OmniOfflineClient stub using the summary path.

    Unit-test count_tokens splits words on spaces, making each word in strings such as
    ``one two three four. ...`` exactly one token and threshold crossings easy to control.
    """
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import SystemMessage

    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming,
        "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = max_response_length
    client.max_response_rerolls = 1
    client.enable_response_guard = True
    client.enable_long_response_summary = True
    client.vision_model = ""
    client.model = "x"
    return client


@pytest.mark.asyncio
async def test_stream_text_summary_replaces_tail_when_overshoot_large(monkeypatch):
    """Summary happy path: the model greatly exceeds budget; the tail after the first
    terminator following overflow becomes an emotion-tier summary. UI sees all original text; TTS hears
    prefix+summary, and history stores prefix+summary."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    # Make the summary call produce a fixed string.
    async def fake_summarize(self, prefix, tail):
        fake_summarize.captured = {"prefix": prefix, "tail": tail}
        return "总之就这样啦"
    fake_summarize.captured = {}
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # budget=4, but cutover must fall after the overflow point: a period before budget must not count
    # as cutover. Follow the trigger chunk with comma-separated clauses; the first comma is after
    # budget, so cutover belongs there. The tail has >=25 tokens, exceeding slack and triggering summary.
    prefix_segment = "one two three four."  # Four words, exhausting the budget.
    overshoot_segment = " five, six seven eight nine ten."  # The first terminator after overflow is a comma.
    long_tail = " " + " ".join(f"w{i}" for i in range(25)) + "."
    long_text = prefix_segment + overshoot_segment + long_tail

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content=long_text)

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    delta_calls: list[dict] = []

    async def fake_text_delta(text, is_first, **kwargs):
        delta_calls.append({
            "text": text,
            "is_first": is_first,
            "ui_enabled": kwargs.get("ui_enabled", True),
            "tts_enabled": kwargs.get("tts_enabled", True),
        })

    async def noop(*_a, **_kw):
        pass

    client = _build_summary_client(monkeypatch, max_response_length=4)
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("trigger long")

    # All three call types must appear.
    both_emits = [c for c in delta_calls if c["ui_enabled"] and c["tts_enabled"]]
    ui_only_emits = [c for c in delta_calls if c["ui_enabled"] and not c["tts_enabled"]]
    tts_only_emits = [c for c in delta_calls if not c["ui_enabled"] and c["tts_enabled"]]

    assert both_emits, "cutover 之前的 prefix 必须以 both 模式发出"
    assert ui_only_emits, "cutover 之后的 tail 必须只去 UI"
    assert len(tts_only_emits) == 1, "summary 必须以 tts-only 注入一次"
    assert tts_only_emits[0]["text"] == "总之就这样啦"

    # The prefix received by _summarize_tail_for_tts must end with a terminator after overflow.
    # Key: budget already contains the period in "four." (offset 18); the old implementation scanning from the chunk start
    # cut over there. The overflow-offset fix must skip it and cut over at "five,".
    captured_prefix = fake_summarize.captured["prefix"].rstrip()
    assert captured_prefix.endswith(","), (
        "cutover 应该落在越界后的逗号，不应该在 budget 之内的句号 "
        "(actual prefix: %r)" % captured_prefix
    )
    assert fake_summarize.captured["tail"]

    # Write prefix + summary to history (matching what TTS hears).
    last_msg = client._conversation_history[-1].content
    assert last_msg.endswith("总之就这样啦")
    # The prefix necessarily includes the portion before budget.
    assert "one two three four." in last_msg
    # Tail words appearing only after overflow must not enter history.
    assert "w24" not in last_msg


@pytest.mark.asyncio
async def test_stream_text_summary_cancelled_while_summarizing_is_not_delivered(monkeypatch):
    """Cancelled during the summary call: the summary never reaches TTS, and
    history keeps the text the UI already showed, not prefix + summary."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    async def cancelling_summarize(self, prefix, tail):
        await self.cancel_response()
        return "总之就这样啦"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", cancelling_summarize)

    long_text = (
        "one two three four. five, six seven eight nine ten. "
        + " ".join(f"w{i}" for i in range(25)) + "."
    )

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content=long_text)

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)
    delta_calls = []

    async def fake_text_delta(text, is_first, **kwargs):
        delta_calls.append((text, kwargs.get("ui_enabled", True), kwargs.get("tts_enabled", True)))

    async def noop(*_a, **_kw):
        pass

    done = []
    client = _build_summary_client(monkeypatch, max_response_length=4)
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = lambda: done.append(True)
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("trigger long")

    assert not [c for c in delta_calls if c[0] == "总之就这样啦"]
    ui_text = "".join(text for text, ui, _tts in delta_calls if ui)
    assert client._conversation_history[-1].content == ui_text
    assert "总之就这样啦" not in client._conversation_history[-1].content
    assert done == []


@pytest.mark.asyncio
async def test_stream_text_summary_abandoned_when_overshoot_under_slack(monkeypatch):
    """If overflow is only a few tokens (< slack), abandon summary and send the tail to TTS,
    retaining all original text in history without a prefix/summary split."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    summarize_called = []

    async def fake_summarize(self, prefix, tail):
        summarize_called.append((prefix, tail))
        return "should not be used"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # budget=4; the model writes only seven words, and 7 < 4+25 -> abandon. After the trigger chunk there must be
    # at least one terminator after overflow, or cutover falls back to stream end with no tail.
    short_overshoot = "one two three four. five, six seven."

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content=short_overshoot)

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    delta_calls: list[dict] = []

    async def fake_text_delta(text, is_first, **kwargs):
        delta_calls.append({
            "text": text,
            "ui_enabled": kwargs.get("ui_enabled", True),
            "tts_enabled": kwargs.get("tts_enabled", True),
        })

    async def noop(*_a, **_kw):
        pass

    client = _build_summary_client(monkeypatch, max_response_length=4)
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("short overshoot")

    assert summarize_called == [], "overshoot 不够大不应当调摘要"
    # Expect one ui_only emission (tail after cutover) + one tts_only emission (restoring TTS on abandon).
    ui_only_emits = [c for c in delta_calls if c["ui_enabled"] and not c["tts_enabled"]]
    tts_only_emits = [c for c in delta_calls if not c["ui_enabled"] and c["tts_enabled"]]
    assert ui_only_emits, "cutover 后的 tail 必须只去 UI"
    assert len(tts_only_emits) == 1, "abandon 路径 tail 必须补一次 TTS-only"
    # Check content as well as channels: the tail restored to TTS on abandon must be the original text,
    # never the summarizer's return value (that path must not run).
    tts_text = tts_only_emits[0]["text"]
    assert "six seven." in tts_text, f"TTS 续读应是原文 tail，实际: {tts_text!r}"
    assert "should not be used" not in tts_text

    # History contains the complete original text.
    assert client._conversation_history[-1].content.strip() == short_overshoot.strip()


@pytest.mark.asyncio
async def test_stream_text_summary_gibberish_fallback_silently_commits_prefix(monkeypatch):
    """If the tail is detected as gibberish at the recheck threshold after cutover, truncate silently:
    do not emit RESPONSE_INVALID (core would call _clear_tts_pipeline and erase the queued,
    unfinished prefix, cutting off speech the user is already hearing). History retains only the prefix,
    and TTS naturally finishes the remaining queue."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    # Lower the gibberish recheck threshold so the unit test need not emit 100+ tokens to trigger it.
    monkeypatch.setattr(_ofc_streaming, "_SUMMARY_GIBBERISH_RECHECK_TOKENS", 2)

    # Force _is_gibberish_response to return True when checking the tail.
    monkeypatch.setattr(_ofc_streaming, "_is_gibberish_response", lambda text: "GIB" in text)

    summarize_called = []

    async def fake_summarize(self, prefix, tail):
        summarize_called.append((prefix, tail))
        return "unused"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # The prefix first uses both; cutover is the first comma after overflow, then "GIB" enters the tail
    # and the recheck detects gibberish.
    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content="one two three four. five, GIB GIB GIB.")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    discarded: list[dict] = []

    async def fake_text_delta(text, is_first, **kwargs):
        # The exact delta is irrelevant here; focus on the discard notification.
        return None

    async def fake_notify_discarded(reason, attempt, max_attempts, will_retry, message=None):
        discarded.append({
            "reason": reason,
            "will_retry": will_retry,
            "message": message,
        })

    async def noop(*_a, **_kw):
        pass

    client = _build_summary_client(monkeypatch, max_response_length=4)
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("trigger gibberish tail")

    assert summarize_called == [], "gibberish fallback 不应调摘要"
    # Key: no response_discarded event, or core would call _clear_tts_pipeline
    # and clear the prefix already queued for TTS too.
    assert discarded == []

    # History retains only the prefix (before cutover), excluding the tail.
    last_msg = client._conversation_history[-1].content
    assert "GIB" not in last_msg
    assert last_msg.startswith("one two three four.")


@pytest.mark.asyncio
async def test_stream_text_summary_overflow_offset_consumed_across_chunks(monkeypatch):
    """When the trigger chunk has no terminator, state stays pending_cutover;
    `summary_overflow_offset` must be consumed back to zero so the next chunk can scan from its
    leading terminator. Otherwise scanning skips the chunk start and places cutover
    incorrectly at its end. Regression guard for Codex P2."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    summarize_called: list = []

    async def fake_summarize(self, prefix, tail):
        summarize_called.append((prefix, tail))
        return "总之"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # Trigger chunk: five words without a terminator (budget=4) -> state becomes pending_cutover;
    # no terminator is found, so the offset must be consumed back to zero.
    # The next chunk starts with a comma; an unconsumed positive offset would skip it.
    async def _astream(self, messages, **overrides):
        # Yield separately to simulate a multi-chunk provider stream; add a leading space to chunk 2
        # so merged token boundaries do not make word-splitting count_tokens combine "e" and "h,".
        yield LLMStreamChunk(content="a b c d e")
        yield LLMStreamChunk(content=" h, i j k l m n o p q r s t u v w x y z aa bb cc dd ee ff.")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    async def fake_text_delta(text, is_first, **kwargs):
        return None

    async def noop(*_a, **_kw):
        pass

    client = _build_summary_client(monkeypatch, max_response_length=4)
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("multi-chunk offset reset")

    # Cutover must occur (summary called), and the prefix must end at the comma starting
    # chunk 2, which is possible only when the offset has been consumed back to zero.
    assert summarize_called, "second chunk 必须能 cutover（offset 已被消费）"
    captured_prefix = summarize_called[0][0]
    assert captured_prefix.endswith("h,"), (
        "cutover 应该落在 second chunk 头部的逗号 (offset=0)，没消费回 0 "
        "时会跳过该逗号。实际 prefix: %r" % captured_prefix
    )


@pytest.mark.asyncio
async def test_stream_text_summary_budget_bump_scoped_to_stream_text(monkeypatch):
    """Summary mode raises self.llm.max_completion_tokens to _SUMMARY_API_BUDGET_FLOOR only
    during stream_text and restores it exactly afterward, without leaking to prompt_ephemeral,
    which shares self.llm (proactive has no length guard, so raising it to 3000 causes overlong replies)."""
    from types import SimpleNamespace
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _budget_to_max_tokens, _SUMMARY_API_BUDGET_FLOOR,
    )
    from utils.llm_client import LLMStreamChunk

    observed: dict = {}

    async def _astream(self, messages, **overrides):
        # Record the client's cap while streaming.
        observed["during"] = self.llm.max_completion_tokens
        yield LLMStreamChunk(content="hi there.")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    async def noop(*_a, **_kw):
        pass

    client = _build_summary_client(monkeypatch, max_response_length=4)
    normal_budget = _budget_to_max_tokens(4)  # budget+slack, much smaller than the 3000 floor.
    client.llm = SimpleNamespace(max_completion_tokens=normal_budget)
    client.on_text_delta = noop
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("trigger")

    # Raised to the floor during the stream (normal_budget < 3000).
    assert observed["during"] == _SUMMARY_API_BUDGET_FLOOR
    # Restore the exact original value after streaming, without leaking into prompt_ephemeral.
    assert client.llm.max_completion_tokens == normal_budget


@pytest.mark.asyncio
async def test_stream_text_summary_disabled_keeps_old_truncate_behavior(monkeypatch):
    """Clients without summary enabled (game / default stream_text calls) retain the original
    abort+inline truncation behavior, unaffected by the new path, and never invoke the small-model summarizer."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk, SystemMessage

    monkeypatch.setattr(_ofc_streaming, "count_tokens", lambda text: len((text or "").split()))
    monkeypatch.setattr(
        _ofc_streaming,
        "truncate_to_tokens",
        lambda text, budget: " ".join((text or "").split()[:budget]),
    )

    summarize_calls: list = []

    async def fake_summarize(self, prefix, tail):
        summarize_calls.append((prefix, tail))
        return "should never run"

    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    async def _astream(self, messages, **overrides):
        # Six words, budget=4 -> cross the limit and trigger the old guard path.
        yield LLMStreamChunk(content="one two three four. five six.")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    delta_calls: list[dict] = []

    async def fake_text_delta(text, is_first, **kwargs):
        delta_calls.append({
            "text": text,
            "ui_enabled": kwargs.get("ui_enabled", True),
            "tts_enabled": kwargs.get("tts_enabled", True),
        })

    async def fake_notify_discarded(reason, attempt, max_attempts, will_retry, message=None):
        pass

    async def noop(*_a, **_kw):
        pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 4
    client.max_response_rerolls = 1
    client.enable_response_guard = True
    client.enable_long_response_summary = False  # Key.
    client.vision_model = ""
    client.model = "x"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("disabled summary")

    # Old path: every emission uses both (no ui/tts split).
    assert delta_calls, "至少要 emit 过一次"
    for c in delta_calls:
        assert c["ui_enabled"] and c["tts_enabled"]
    # Key regression: the small-model summarizer must never be called in disabled mode.
    assert summarize_calls == []


def _minimal_offline_client_for_leak_tests():
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolDefinition
    from utils.llm_client import SystemMessage

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client.base_url = "free"
    client.lanlan_name = "T"
    client.master_name = "M"
    client._prefix_buffer_size = 0
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._is_responding = False
    client._recent_responses = []
    client._repetition_threshold = 0.8
    client._max_recent_responses = 3
    client.max_response_length = 9999
    client.max_response_rerolls = 0
    client.max_tool_iterations = 1
    client.enable_response_guard = False
    client.enable_long_response_summary = False
    client.vision_model = ""
    client.model = "x"
    client._tool_definitions = [ToolDefinition(name="recall_memory", description="")]
    return client


@pytest.mark.asyncio
async def test_stream_text_filters_tool_call_leak_before_ui_and_history(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import AIMessage, LLMStreamChunk

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content="before ")
        yield LLMStreamChunk(
            content='recall_memory</name><parameter name="query">secret</parameter></function></seed:tool_call>'
        )
        yield LLMStreamChunk(content=" after")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    emitted: list[str] = []

    async def fake_text_delta(text, is_first, **kwargs):
        emitted.append(text)

    async def noop(*_a, **_kw):
        pass

    client = _minimal_offline_client_for_leak_tests()
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("hello")

    visible = "".join(emitted)
    assert visible == "before  after"
    assert "recall_memory" not in visible
    ai_messages = [m for m in client._conversation_history if isinstance(m, AIMessage)]
    assert ai_messages[-1].content == "before  after"
    assert "secret" not in ai_messages[-1].content


@pytest.mark.asyncio
async def test_stream_text_replaces_full_prompt_history_after_memory_callback(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import HumanMessage, LLMStreamChunk

    async def _astream(self, messages, **overrides):
        sent_user_messages = [msg for msg in messages if isinstance(msg, HumanMessage)]
        assert sent_user_messages[-1].content == "full document prompt"
        yield LLMStreamChunk(content="ok")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    async def noop(*_a, **_kw):
        pass

    transcripts: list[str] = []

    async def transcript_callback(text: str) -> None:
        transcripts.append(text)

    client = _minimal_offline_client_for_leak_tests()
    client.on_text_delta = noop
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text(
        "full document prompt",
        system_prefix="callback result",
        input_transcript_callback=transcript_callback,
        history_replacement_text="summary only",
    )

    assert transcripts == ["full document prompt"]
    assert isinstance(client._conversation_history[1], HumanMessage)
    assert client._conversation_history[1].content == "callback result\n\nsummary only"
    assert "full document prompt" not in [
        getattr(message, "content", "") for message in client._conversation_history
    ]


@pytest.mark.asyncio
async def test_stream_text_keeps_system_prefix_images_invocation_local(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import HumanMessage, LLMStreamChunk

    observed_user_content = []

    async def _astream(self, messages, **overrides):
        del overrides
        observed_user_content.append(
            [msg for msg in messages if isinstance(msg, HumanMessage)][-1].content
        )
        yield LLMStreamChunk(content="ok")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    async def noop(*_a, **_kw):
        pass

    client = _minimal_offline_client_for_leak_tests()
    client.on_text_delta = noop
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text(
        "user text",
        system_prefix="callback context",
        system_prefix_images=["callback-image"],
    )

    assert observed_user_content == [[
        {
            "type": "image_url",
            "image_url": {"url": "data:image/jpeg;base64,callback-image"},
        },
        {"type": "text", "text": "callback context\n\nuser text"},
    ]]
    assert client._pending_images == []


@pytest.mark.asyncio
async def test_prompt_ephemeral_filters_tool_call_leak_before_ui_and_history(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import AIMessage, LLMStreamChunk

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content="hi ")
        yield LLMStreamChunk(content="<seed:tool_call>secret</seed:tool_call>")
        yield LLMStreamChunk(content=" done")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    emitted: list[str] = []

    async def fake_text_delta(text, is_first, **kwargs):
        emitted.append(text)

    async def noop(*_a, **_kw):
        pass

    client = _minimal_offline_client_for_leak_tests()
    client.on_text_delta = fake_text_delta
    client.on_response_done = noop
    client.on_status_message = noop
    client.on_proactive_done = noop

    assert await client.prompt_ephemeral("nudge", completion_mode="response") is True

    visible = "".join(emitted)
    assert visible == "hi  done"
    assert "seed:tool_call" not in visible
    ai_messages = [m for m in client._conversation_history if isinstance(m, AIMessage)]
    assert ai_messages[-1].content == "hi  done"
    assert "secret" not in ai_messages[-1].content


@pytest.mark.asyncio
async def test_stream_text_only_leak_yields_no_visible_output(monkeypatch):
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import AIMessage, LLMStreamChunk

    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content="<seed:tool_call>secret</seed:tool_call>")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    emitted: list[str] = []
    statuses: list[str] = []

    async def fake_text_delta(text, is_first, **kwargs):
        emitted.append(text)

    async def fake_status(message):
        statuses.append(message)

    async def noop(*_a, **_kw):
        pass

    client = _minimal_offline_client_for_leak_tests()
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = fake_status
    client.on_repetition_detected = None

    await client.stream_text("hello")

    assert emitted == []
    assert not any(isinstance(m, AIMessage) for m in client._conversation_history)
    assert [json.loads(message) for message in statuses] == [{"code": "LLM_NO_RESPONSE"}]
