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
    # stream_text 在到达 astream 前读 self.llm.max_completion_tokens（focus overrides /
    # summary budget）。真实 client 恒有 self.llm；harness 里若测试自己设了 fake llm
    # 就用它的，否则给个 max_completion_tokens=None 的占位（= 不限 budget，与加
    # summary/focus 之前的老行为一致）。注意不能设成 None —— stream_text 会把
    # ``self.llm is None`` 当成「client 已 close」提前 break。
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
    """SDK 偶发流出无 name 的残缺 tool_call，必须丢弃，否则会污染
    tool_calls 历史导致下一轮 server schema reject。

    回归保护：CodeRabbit PR #1035 反馈。"""
    from utils.llm_client import ChatOpenAI

    deltas_per_chunk = [
        # call 0：完整
        [{"index": 0, "id": "ok", "function": {"name": "good_tool", "arguments": "{}"}}],
        # call 1：name 缺失（id 也缺）—— 该被丢弃
        [{"index": 1, "function": {"arguments": "{}"}}],
        # call 2：仅 arguments 进来，name 始终为空—— 该被丢弃
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
    # tool 消息必须带 name（Gemini 转换路径靠这个字段填 FunctionResponse.name）
    assert messages[2]["name"] == "get_weather"


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
    """Thinking 模型（DeepSeek-R / Qwen / GLM thinking 等 OpenAI-compat 端点）
    在多轮 tool calling 时，发起 tool_calls 的那条 assistant 消息必须把本轮流出的
    ``reasoning_content`` 原样回填，否则下一轮报 400 "The `reasoning_content` in the
    thinking mode must be passed back to the API."（触发 memory_recall 时复现过）。

    本测试 script 一个带 reasoning_content 的 tool-call 轮，断言写回历史的
    assistant tool_calls 消息里 reasoning_content 被保留。"""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    tool_def = ToolDefinition(
        name="memory_recall",
        description="recall",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
        handler=lambda args: {"hits": []},
    )

    # 第一轮：先流推理链，再流 tool_call，finish_reason=tool_calls。
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

    # 纯 reasoning chunk（content 空 + 无 tool/finish/usage）不得向下游转发，
    # 否则 stream_text 会把首个推理 chunk 误记成 TTFT 首 token（Codex P2）。
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
    # 第二轮请求确实带上了含 reasoning_content 的历史，且值与本轮累积的推理链
    # 完全一致（不只验存在——中间路径若截断/改写 reasoning_content 也要抓到）。
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
    # 推理文本仍被过滤，不向下游 yield。
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
    """非 thinking 端点（delta 不带 reasoning_content）时，assistant tool_calls
    消息不应凭空塞入 reasoning_content 字段，免得污染普通会话 / 触发某些 provider
    对 reasoning_content 的反向校验。"""
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
    """switch_model 切到不同 endpoint 后必须重新计算 _use_genai_sdk，
    并清空 _genai_client，否则会沿用旧 conversation 的路由判断。

    回归保护：Codex P1 反馈，PR #1035。

    本测试只验状态切换的纯逻辑——monkeypatch ``_GENAI_AVAILABLE=True``
    让 ``_should_use_genai_sdk`` 在没装 google-genai 的 CI 上也能跑出
    ``True`` 分支，避免环境不全时这条回归保护被静默 skip 掉。"""
    from main_logic.omni_offline_client import OmniOfflineClient

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    # 建 client：conversation 走 OpenAI，vision_base_url 指向 Gemini native endpoint。
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
    client._genai_client = "stale-sentinel"  # 模拟旧 client
    # 初始用 OpenAI conversation，路由旗标必为 False
    from main_logic.omni_offline_client import _should_use_genai_sdk
    client._use_genai_sdk = _should_use_genai_sdk(client.model, client.base_url)
    assert client._use_genai_sdk is False

    # 给一个能 aclose() 的占位 llm
    class _FakeLLM2:
        max_completion_tokens = 100
        async def aclose(self): pass
    client.llm = _FakeLLM2()

    # 切到 vision config（用 Gemini native endpoint）
    await client.switch_model("gemini-2.5-flash", use_vision_config=True)

    # 路由旗标必须重新计算成 True
    assert client._use_genai_sdk is True, (
        "switch_model 后 _use_genai_sdk 必须重算，否则 vision/Gemini 切换路由错"
    )
    # 旧 _genai_client 必须被清空，下次走 lazy init
    assert client._genai_client is None
    # base_url / api_key 必须同步到 vision 配置
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
    """genai SDK 网络/鉴权抖动（429 / 5xx / timeout / auth）不应该被包装成
    `_GenaiToolsUnsupported`，否则单次 transient 错误会让整个 session 永久
    退化到 OpenAI-compat 路径，工具调用永久失效。

    回归保护：CodeRabbit PR #1035 第 4 轮 review."""
    from main_logic.omni_offline_client import (
        OmniOfflineClient,
    )

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    class _BoomClient:
        class _aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    # 模拟 5xx server error —— 与 tools 无关
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

    # 期望：transient 错误以原异常 raise 出来，不包成 _GenaiToolsUnsupported
    with pytest.raises(RuntimeError, match="503"):
        async for _ in client._astream_genai_with_tools([{"role": "user", "content": "x"}]):
            pass


@pytest.mark.asyncio
async def test_offline_genai_streamed_text_persisted_with_tool_call(monkeypatch):
    """同一 Gemini turn 里 text + function_call 并存时，写历史的 assistant
    消息 content 必须包含本轮已流给用户的 text，否则下一轮 LLM 看不到自己
    说过的前半句，会重复或改口。

    回归保护：CodeRabbit PR #1035 第 5 轮 review."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    # 构造一个 fake stream：单个 chunk 同时携带 text part + function_call part
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
                # 第二轮（tool 已执行）：返回结尾文本
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

    # 用户拿到的 text 应该是前半句 + 后半句
    full_user_text = "".join(out)
    assert "让我查一下天气" in full_user_text
    assert "22°C" in full_user_text

    # 历史里 tool_calls 那条 assistant 消息的 content 必须包含已 yield 的前半句
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
    """assistant 同时有 content + tool_calls 时，转 Gemini Content 必须把
    text 和 function_call 一起 emit 成 parts。否则下一轮 generate_content_stream
    看到的历史依然缺已 stream 出去的前半句，模型还是会重复 / 改口。

    回归保护：CodeRabbit PR #1035 第 6 轮 review."""
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
    # 找 assistant turn
    assistant_turn = next(c for c in contents if c.role == "model")
    parts = list(assistant_turn.parts)
    # 第一个 part 必须是 text，后面才是 function_call
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
    """连续多个 register_tool_and_sync 必须串行推送 session.update —— 否则
    OpenAI Realtime / GLM / Qwen 收到的 wire 事件可能乱序，最后一份快照
    不一定对应 registry 的最终状态。

    回归保护：CodeRabbit PR #1035 第 6 轮 review."""
    import asyncio as _asyncio

    from main_logic.tool_calling import ToolDefinition

    # 构造一个带 _tool_sync_lock + tool_registry 但其它字段都 stub 的 mgr。
    class _StubMgr:
        def __init__(self):
            from main_logic.tool_calling import ToolRegistry
            self.tool_registry = ToolRegistry()
            self._tool_sync_lock = _asyncio.Lock()
            self.session = None
            self.pending_session = None
            self.sync_call_log: list = []

        async def _sync_tools_to_active_session(self):
            # 模拟实际实现：进 lock 内才读 registry 并"推送"。
            async with self._tool_sync_lock:
                names = self.tool_registry.names()
                # 模拟 session.update 推送 ~10ms
                await _asyncio.sleep(0.01)
                self.sync_call_log.append(tuple(sorted(names)))

        async def register_tool_and_sync(self, tool, *, replace=True):
            self.tool_registry.register(tool, replace=replace)
            await self._sync_tools_to_active_session()

    mgr = _StubMgr()

    # 三个并发 register。
    await _asyncio.gather(
        mgr.register_tool_and_sync(ToolDefinition(name="a", description="", handler=lambda _: 0)),
        mgr.register_tool_and_sync(ToolDefinition(name="b", description="", handler=lambda _: 0)),
        mgr.register_tool_and_sync(ToolDefinition(name="c", description="", handler=lambda _: 0)),
    )

    # 串行：每次 sync 看到的快照单调增加（不会出现"先看到 abc 后看到 ab"的乱序）。
    sizes = [len(snap) for snap in mgr.sync_call_log]
    assert sizes == sorted(sizes), (
        f"sync_call_log 必须单调增加（串行推送），实际：{mgr.sync_call_log}"
    )
    # 最后一次 sync 必须看到完整 3 个工具
    assert mgr.sync_call_log[-1] == ("a", "b", "c")


def test_tool_register_request_rejects_non_loopback_callback_url():
    """callback_url host 白名单：必须是 127.0.0.0/8 / ::1 / localhost，
    防止本地 caller 把 main_server 当 SSRF 出站代理。

    回归保护：CodeRabbit PR #1035 第 14 轮 review."""
    from pydantic import ValidationError

    from main_routers.tool_router import ToolRegisterRequest

    base = {
        "name": "x",
        "callback_url": "http://127.0.0.1:9000/cb",
        "parameters": {"type": "object", "properties": {}},
    }

    # 合法 case
    for url in [
        "http://127.0.0.1:9000/cb",
        "http://localhost:9000/cb",
        "http://[::1]:9000/cb",
        "http://127.0.0.5/cb",  # 127.0.0.0/8 整段都是 loopback
        "https://localhost/cb",
        # IPv4-mapped IPv6 loopback：CPython < 3.11.11 的 is_loopback
        # 不穿透映射，validator 需手动解包 ipv4_mapped 判断
        "http://[::ffff:127.0.0.1]:9000/cb",
    ]:
        ToolRegisterRequest(**{**base, "callback_url": url})

    # 非法 case：公网 IP / 局域网 IP / 私有域名 / 错误 scheme / 缺 host
    illegal_urls = [
        "http://8.8.8.8/cb",
        "http://192.168.1.5/cb",  # 局域网也禁
        "http://10.0.0.1/cb",
        "http://example.com/cb",
        "ftp://127.0.0.1/cb",  # 错误 scheme
        "http:///cb",  # 缺 host
        "http://[2001:db8::1]/cb",  # 公网 IPv6
        "http://[::ffff:8.8.8.8]/cb",  # IPv4-mapped 公网 IP 仍须拒绝
    ]
    for url in illegal_urls:
        with pytest.raises(ValidationError):
            ToolRegisterRequest(**{**base, "callback_url": url})


@pytest.mark.asyncio
async def test_register_tool_and_sync_propagates_session_update_failure():
    """`*_and_sync` 必须在 wire 同步失败时把异常往上抛 —— 否则 HTTP
    /api/tools 会误回 ok=true 但 session 上的工具其实没生效。

    回归保护：CodeRabbit PR #1035 第 8 轮 review."""
    import asyncio as _asyncio

    from main_logic.tool_calling import ToolDefinition, ToolRegistry

    # Stub mgr：模拟 register_tool_and_sync 的串行+raise_on_failure 流水。
    class _StubMgr:
        def __init__(self):
            self.tool_registry = ToolRegistry()
            self._tool_sync_lock = _asyncio.Lock()
            self.session = object()  # 触发 sync 路径
            self.pending_session = None

        async def _sync_tools_to_active_session(self, *, raise_on_failure=False):
            async with self._tool_sync_lock:
                # 模拟 wire 推送一定失败
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
    """`/api/tools/unregister` 跨角色调用时单个 mgr 抛异常不能让整个请求 500。
    必须把已成功的 role 收进 affected_roles，失败的进 failed_roles。

    回归保护：CodeRabbit PR #1035 第 8 轮 review."""
    from main_routers import tool_router as _tr

    class _GoodMgr:
        lanlan_name = "Good"
        async def unregister_tool_and_sync(self, name): return True

    class _BadMgr:
        lanlan_name = "Bad"
        async def unregister_tool_and_sync(self, name):
            raise RuntimeError("fake sync failure")

    targets = [_GoodMgr(), _BadMgr()]
    # 直接调 endpoint 函数，绕开 _resolve_target_managers
    # （需要 monkeypatch 这个 helper）
    import unittest.mock as _mock
    with _mock.patch.object(_tr, "_resolve_target_managers", return_value=targets):
        from main_routers.tool_router import unregister_tool, ToolUnregisterRequest
        result = await unregister_tool(ToolUnregisterRequest(name="x", role=None))

    assert result["affected_roles"] == ["Good"]
    assert len(result["failed_roles"]) == 1
    assert result["failed_roles"][0]["role"] == "Bad"
    assert "fake sync failure" in result["failed_roles"][0]["error"]
    # 一个成功 + 一个失败 → ok=True（部分成功）
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# 死插件自动驱逐：_remote_dispatch 在同一 plugin source 连续 N 次"连接级"失败
# 后扫除该 source 的所有工具。覆盖 kill -9 杀插件进程后 main_server registry
# 里残留死端点工具的场景——优雅 shutdown 走 /api/tools/clear，崩溃没机会触发。
# ---------------------------------------------------------------------------


def _make_eviction_stub_mgr(name: str):
    """造一个能配合 _evict_dead_callback_origin 的最小 mgr：暴露
    ``tool_registry`` + 私有的 ``_fire_task`` / ``_sync_tools_to_active_session``
    （驱逐通道用它们触发 wire 同步，跟 register_tool / clear_tools 走同一条
    路径）。``_fire_task`` 直接 close 掉 coro 避免"coroutine was never awaited"
    告警，单测只需要验证它被调用过了。"""
    from main_logic.tool_calling import ToolRegistry

    class _StubMgr:
        def __init__(self, lanlan_name: str):
            self.lanlan_name = lanlan_name
            self.tool_registry = ToolRegistry()
            self.fire_task_count = 0

        async def _sync_tools_to_active_session(self):
            # 单测里没有 active session，wire 同步是 noop。
            return None

        def _fire_task(self, coro):
            self.fire_task_count += 1
            coro.close()

    return _StubMgr(name)


@pytest.fixture
def _reset_eviction_counter():
    """每个 eviction 测试前后都把模块级失败计数清空，避免跨测试污染。"""
    from main_routers import tool_router as _tr
    _tr._consecutive_connect_failures.clear()
    yield
    _tr._consecutive_connect_failures.clear()


@pytest.mark.asyncio
async def test_remote_dispatch_evicts_dead_plugin_after_three_connect_failures(
    monkeypatch, _reset_eviction_counter,
):
    """插件 kill -9 之后，model 连撞 3 次 connect refused → 该 plugin 的
    所有工具从所有 session manager 的 registry 里清除；builtin 工具不动。

    回归保护：lifecycle 审计发现 plugin 崩溃没有清理路径。"""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    # 准备一个 stub session_manager，挂两个"角色"，每个 mgr 都有同一个 plugin
    # 的两个工具（模拟 role=None 的全局注册）和一个 builtin。
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

    # 让 httpx client 永远 ConnectError —— 模拟插件进程已经死了。
    class _DeadClient:
        async def post(self, *_a, **_kw):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(_tr, "_get_http_client", lambda: _DeadClient())

    # 模型走 dispatch 3 次（任意角色都会触发 module-level 计数，跨 mgr 共享）。
    call = ToolCall(name="weather", arguments={}, call_id="c")
    metadata = {
        "source": "plugin:dead_foo",
        "callback_url": "http://127.0.0.1:9999/cb",
        "timeout_seconds": 5,
    }
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD):
        result = await _tr._remote_dispatch(call, metadata)
        # 单次 dispatch 失败必须仍然回 ToolResult（不抛异常），
        # 让模型看到结构化错误而不是 client 崩。
        assert result.is_error is True

    # 阈值后：两个 mgr 上所有 plugin:dead_foo 的工具都该被扫掉，
    # builtin 工具留下。
    for mgr in (mgr_a, mgr_b):
        names = sorted(mgr.tool_registry.names())
        assert names == ["recall_memory"], (
            f"mgr {mgr.lanlan_name}: plugin tools should be evicted, "
            f"builtin should remain; got {names}"
        )
        # 驱逐必须 fire 一次 wire 同步，让模型在 schema 上也看不到死工具。
        assert mgr.fire_task_count >= 1, (
            f"mgr {mgr.lanlan_name}: eviction must fire session.update sync; "
            f"got fire_task_count={mgr.fire_task_count}"
        )

    # 计数器在驱逐后清零，避免下次 1 次失败就触发误杀。
    dead_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert ("plugin:dead_foo", dead_origin) not in _tr._consecutive_connect_failures


@pytest.mark.asyncio
async def test_remote_dispatch_business_error_does_not_evict(
    monkeypatch, _reset_eviction_counter,
):
    """插件 callback 业务上返回 ``is_error=True``（或 HTTP 5xx）说明插件是活的，
    只是工具内部出错——这是工具 bug 不是 lifecycle bug，不能算驱逐计数。"""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    mgr.tool_registry.register(ToolDefinition(
        name="buggy_tool", description="", handler=None,
        metadata={"source": "plugin:buggy", "callback_url": "http://127.0.0.1:9999/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    # 模拟插件活着（200 + is_error=true body）。
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
    # 比阈值多调几次。
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD + 2):
        result = await _tr._remote_dispatch(call, metadata)
        assert result.is_error is True  # 业务错误透传

    # 工具仍在 registry 里——业务错不是 lifecycle 错。
    assert mgr.tool_registry.names() == ["buggy_tool"]
    assert mgr.fire_task_count == 0
    # 计数器从未累加（每次成功 HTTP 都清零）。
    buggy_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert ("plugin:buggy", buggy_origin) not in _tr._consecutive_connect_failures


@pytest.mark.asyncio
async def test_remote_dispatch_success_resets_failure_counter(
    monkeypatch, _reset_eviction_counter,
):
    """连续 2 次 connect failure（还不到阈值）后一次成功 → 计数器清零，
    后续再失败要从 1 重新计起。防止"偶发 connection refused"长期累积成误杀。"""
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

    # fail, fail (counter=2), ok (counter=0), fail, fail (counter=2) — 仍未到阈值
    for _ in range(5):
        await _tr._remote_dispatch(call, metadata)

    # 工具还在，counter 累计但没超阈值。
    assert mgr.tool_registry.names() == ["flaky"]
    assert mgr.fire_task_count == 0
    flaky_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert _tr._consecutive_connect_failures.get(("plugin:flaky", flaky_origin)) == 2


@pytest.mark.asyncio
async def test_remote_dispatch_builtin_source_never_evicted(
    monkeypatch, _reset_eviction_counter,
):
    """``source="builtin"`` 永远不进入驱逐计数——builtin 工具是 in-process
    handler，通常根本不走 _remote_dispatch；万一被错配成 remote，也不能因为
    "连续失败"就把内置工具扫掉。"""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    # 病态注册：builtin 但 handler=None，硬走 remote 路径。
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

    # 工具仍在 registry，计数器从未被建。
    assert mgr.tool_registry.names() == ["recall_memory"]
    assert mgr.fire_task_count == 0
    # builtin source 永远不进 counter dict
    assert not any(src == "builtin" for src, _origin in _tr._consecutive_connect_failures)


@pytest.mark.asyncio
async def test_remote_dispatch_read_timeout_does_not_evict(
    monkeypatch, _reset_eviction_counter,
):
    """ReadTimeout 表示插件接住了请求但执行慢——是工具实现问题，不是
    "端点不可达"。只有 ConnectError/ConnectTimeout 才算 lifecycle 失败。"""
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
        assert result.is_error is True  # 超时仍透传给模型作为错误

    # 工具不动：ReadTimeout 不算 connection-level，counter 也没累加。
    assert mgr.tool_registry.names() == ["slow"]
    assert mgr.fire_task_count == 0
    slow_origin = _tr._callback_origin("http://127.0.0.1:9999/cb")
    assert ("plugin:slow", slow_origin) not in _tr._consecutive_connect_failures


@pytest.mark.asyncio
async def test_remote_dispatch_endpoint_local_outage_preserves_sibling_endpoints(
    monkeypatch, _reset_eviction_counter,
):
    """同一 plugin source 下若注册的工具指向不同 callback origin（不同 port），
    单端点不可达只能扫掉该端点的工具，不能把同 source 的其他健康端点工具
    一起带走。

    回归保护：Codex review on PR #1382——单按 source 聚合会把"一个端点
    死了"误升级成"整个 plugin 全死"。"""
    from main_logic.tool_calling import ToolCall, ToolDefinition
    from main_routers import tool_router as _tr

    mgr = _make_eviction_stub_mgr("Solo")
    # 同一 plugin source，两个工具指向不同 port。
    mgr.tool_registry.register(ToolDefinition(
        name="tool_on_dead_port", description="", handler=None,
        metadata={"source": "plugin:multi_port", "callback_url": "http://127.0.0.1:9001/cb"},
    ))
    mgr.tool_registry.register(ToolDefinition(
        name="tool_on_live_port", description="", handler=None,
        metadata={"source": "plugin:multi_port", "callback_url": "http://127.0.0.1:9002/cb"},
    ))
    monkeypatch.setattr(_tr, "get_session_manager", lambda: {"Solo": mgr})

    # 只有 port 9001 不可达；9002 正常。
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
    # 3 次撞死端口 → 触发驱逐
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD):
        await _tr._remote_dispatch(dead_call, dead_metadata)

    # 关键断言：只有指向 9001 的工具被扫，9002 的 sibling 工具完好。
    names = sorted(mgr.tool_registry.names())
    assert names == ["tool_on_live_port"], (
        f"endpoint-local outage on 9001 must NOT evict sibling tool on 9002; "
        f"got remaining={names}"
    )

    # 活端口的 counter 应当从未建立——它没失败过。
    live_origin = _tr._callback_origin("http://127.0.0.1:9002/cb")
    assert ("plugin:multi_port", live_origin) not in _tr._consecutive_connect_failures

    # 健康端点继续可调用、保持 schema 中可见。
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
    """``_callback_origin`` 必须把 (scheme, host, port) 折叠成同一 bucket key——
    path / query / fragment 不能让同一 server 的不同 URL 被当成不同 endpoint。
    """
    from main_routers.tool_router import _callback_origin

    base = _callback_origin("http://127.0.0.1:9000/api/cb")
    # 同 origin 不同 path / query → 同一 key
    assert _callback_origin("http://127.0.0.1:9000/api/cb") == base
    assert _callback_origin("http://127.0.0.1:9000/other/path") == base
    assert _callback_origin("http://127.0.0.1:9000/api/cb?x=1") == base
    # 不同 port → 不同 key
    assert _callback_origin("http://127.0.0.1:9001/api/cb") != base
    # 默认端口要显式化（http→80, https→443），避免 ``http://h/`` 和
    # ``http://h:80/`` 被当成两个 endpoint
    assert _callback_origin("http://127.0.0.1/cb") == "http://127.0.0.1:80"
    assert _callback_origin("https://127.0.0.1/cb") == "https://127.0.0.1:443"
    # 异常输入兜底：空 / 不可 parse → 不抛
    assert _callback_origin("") == "<unknown>"
    # 畸形端口（非数字）会让 ParseResult.port 抛 ValueError——
    # loopback validator 没拦端口语法，所以 dispatch 路径必须自己兜住，
    # 否则会破坏 ToolResult 结构化错误 + 驱逐 bookkeeping。
    # （Codex review on PR #1382）
    bad_port = "http://127.0.0.1:abc/cb"
    assert _callback_origin(bad_port) == bad_port  # 回退到原串、不抛
    # 同一畸形 URL 必须映射到同一 key——不然失败计数会因为 key collision
    # 退化，永远到不了阈值。
    assert _callback_origin(bad_port) == _callback_origin(bad_port)


@pytest.mark.asyncio
async def test_remote_dispatch_survives_malformed_callback_url(
    monkeypatch, _reset_eviction_counter,
):
    """``ToolRegisterRequest`` 的 loopback validator 不管端口语法，畸形
    端口（``http://127.0.0.1:abc/cb``）可以通过注册。``_callback_origin``
    必须不抛——否则 dispatch 路径会被破坏：原本应该返回结构化 ToolResult
    error，结果上抛到 ToolRegistry.execute 的兜底 catch，错误消息丢精度。

    回归保护：Codex review on PR #1382."""
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

    # 不能抛——必须每次回结构化 ToolResult(is_error=True)。
    for _ in range(_tr._EVICTION_FAILURE_THRESHOLD):
        result = await _tr._remote_dispatch(call, metadata)
        assert result.is_error is True
        assert "HTTP failure" in result.error_message

    # 即使 URL 畸形，eviction bookkeeping 仍应正常工作——3 次后扫掉。
    assert mgr.tool_registry.names() == []
    assert mgr.fire_task_count >= 1


@pytest.mark.asyncio
async def test_genai_unsupported_keyword_matches_underscore_variant(monkeypatch):
    """`not_support` 下划线变体也得当成 tools 永久不支持，避免每轮先撞
    genai 再回退的额外抖动。

    回归保护：CodeRabbit PR #1035 第 8 轮 review."""
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
    """genai 路径已经 yield 过 text chunk 之后再抛 transient 异常时，
    `_astream_with_tools` 不能静默 fallback 到 OpenAI-compat —— 否则用户
    在同一轮看到"半截 Gemini + 一份 OpenAI 重新生成"双流拼接。必须 raise
    让 stream_text 的 retry/discard 流程清空气泡后重试。

    回归保护：CodeRabbit PR #1035 第 7 轮 review."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _genai_yields_then_raises(self, messages, **overrides):
        # 先吐两块文本，然后抛 transient 异常
        yield LLMStreamChunk(content="让我查一下，")
        yield LLMStreamChunk(content="稍等。")
        raise RuntimeError("HTTP 503 transient")

    async def _openai_should_not_run(self, messages, **overrides):
        # 如果到这里，说明发生了我们要避免的双流拼接
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

    # 应该确实 yield 出了 genai 已吐的文本，然后异常向上 raise
    assert "让我查一下，" in yielded
    assert "稍等。" in yielded
    # OpenAI fallback 文本不应该出现
    assert not any("OPENAI_FALLBACK" in (s or "") for s in yielded)


@pytest.mark.asyncio
async def test_stream_text_notifies_discarded_when_partial_text_then_error(monkeypatch):
    """stream_text 通用 except Exception 分支必须识别"已吐文本但失败"
    的场景，调用 _notify_response_discarded 让前端清空半截气泡——否则
    用户会看到一段被中断的文本永远停在那。这是 _astream_with_tools
    新契约 (genai_emitted_text 后 raise) 真正生效的关键。

    回归保护：CodeRabbit PR #1035 第 9 轮 review."""
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
    client.enable_response_guard = False  # 简化逻辑：直接走 except 分支
    client.vision_model = ""
    client.model = "gemini-2.5-flash"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = None
    client.on_response_done = fake_done
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = fake_status
    client.on_repetition_detected = None

    await client.stream_text("天气怎么样")

    # 断言已吐文本到前端
    assert "正在查询天气，" in "".join(text_emitted)
    # 关键：响应被丢弃通知必须调用过，让前端清空半截气泡
    assert len(discarded_calls) >= 1, "已吐文本后必须 notify_response_discarded 让前端清空气泡"
    last = discarded_calls[-1]
    assert "text_gen_error" in last["reason"]
    assert last["will_retry"] is False  # 通用 except 不再重试


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
    """正常长回复已经流式吐到前端时，长度 guard 不应走 discard/recovery。

    response_discarded 会清掉旧气泡/字幕，然后 recovery 再整段重发；这会
    让字幕翻译处理两份同一轮文本，TTS 也可能收到过长的整段恢复文本。
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
    """对偶：genai 路径还没 yield 过任何文本就抛 transient 异常时，
    `_astream_with_tools` 仍然应该静默 fallback 到 OpenAI-compat 兜底——
    用户感知不到失败，体验最佳。"""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _genai_raises_immediately(self, messages, **overrides):
        # 关键：还没 yield 任何东西就抛
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

    # 没 yield 过 → 静默 fallback，用户拿到 OpenAI 路径的文本
    assert yielded == ["OpenAI fallback OK"]
    # transient 不翻 _genai_tools_unsupported
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
    """与上一条对偶：当 genai 真的报"tools not supported"时，必须被包装成
    `_GenaiToolsUnsupported`，让 `_astream_with_tools` 翻 `_genai_tools_unsupported`
    并 fallback 到 OpenAI-compat 路径。"""
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


@pytest.mark.asyncio
async def test_offline_openai_path_persists_streamed_text_with_tool_calls():
    """OpenAI-compat 路径同 turn 先 yield text 再进 tool_calls 时，写历史的
    assistant 消息 content 必须保留 streamed text，与 Gemini 路径对偶。
    某些 OpenAI-compat provider（GLM-text、Qwen-text 等）真会出现这种流。

    回归保护：CodeRabbit PR #1035 第 10 轮 review."""
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

    # 第 1 次 LLM 调用：先吐文字，然后给 tool_call，最后 finish=tool_calls
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
    # 第 2 次：tool 已执行，模型出最终文本
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

    # 找写历史的 assistant w/ tool_calls 那条
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
    """与 OpenAI 路径的 collect_tool_calls 防御对偶：GenAI 路径流式收到空
    name 的 function_call 时也必须丢弃，否则会用空 name 调 on_tool_call
    并把非法 tool_calls 历史写回 messages，下一轮 generate_content_stream
    被 schema reject。

    回归保护：CodeRabbit PR #1035 第 11 轮 review."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    # 构造 fake stream：一个 function_call 有 name，另一个 name 为空
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
        # 同 turn 里：1 个有效 function_call + 1 个 name 空的
        yield _Chunk(candidates=[_Candidate(_Content([
            _Part(function_call=_FunctionCall("good_tool", {"x": 1}, id_="c1")),
            _Part(function_call=_FunctionCall("", {"y": 2}, id_="c_empty")),  # 该被 drop
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
                # 第 2 轮：tool 已执行，给个最终文本
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

    # handler 只该被 good_tool 调用过，空 name 的被 drop
    assert handler_calls == ["good_tool"], (
        f"GenAI 路径必须 drop 空 name 的 function_call，实际 handler 收到：{handler_calls}"
    )
    # 写回 messages 的 assistant.tool_calls 也只能有 good_tool
    assistant_with_tools = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    names = [tc["function"]["name"] for tc in assistant_with_tools["tool_calls"]]
    assert names == ["good_tool"]


@pytest.mark.asyncio
async def test_set_tools_resets_genai_unsupported_flag():
    """set_tools 必须清掉 _genai_tools_unsupported —— 否则旧工具集触发
    schema reject 后，热卸载坏工具也不会让 genai 路径恢复。

    回归保护：CodeRabbit PR #1035 第 12 轮 review."""
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    _init_bare(client)
    client._tool_definitions = [
        ToolDefinition(name="bad", description="", handler=lambda _: 0),
    ]
    client._genai_tools_unsupported = True  # 模拟旧工具触发过 schema reject

    # 热卸载坏工具
    client.set_tools([])
    assert client._genai_tools_unsupported is False, (
        "set_tools 替换工具列表后必须清掉 unsupported 旗标，否则永远走不回 genai 路径"
    )

    # 再注册新工具时也必须重置（caller 可能传新的）
    client._genai_tools_unsupported = True
    client.set_tools([ToolDefinition(name="good", description="", handler=lambda _: 0)])
    assert client._genai_tools_unsupported is False


@pytest.mark.asyncio
async def test_stream_text_does_not_double_write_pretool_text(monkeypatch):
    """stream_text 在 _astream_with_tools 内 inline 持久化了 tool 轮（含
    pre-tool text + tool_calls + tool result）之后，final AIMessage append
    必须只包含 post-tool 文本——否则 pre-tool 文本被双写进 history（一份
    在 assistant.tool_calls.content，一份在 final AIMessage.content）。

    回归保护：CodeRabbit PR #1035 第 12 轮 review."""
    from utils.llm_client import LLMStreamChunk, AIMessage, SystemMessage
    from main_logic.omni_offline_client import OmniOfflineClient

    async def _astream_simulating_tool_round(self, messages, **overrides):
        # 工具轮的 pre-tool 文本
        yield LLMStreamChunk(content="正在查询，")
        # _astream_*_with_tools 在 inline 持久化时会做的事：把 tool 轮 append 进 history
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
        # 通知上游 final-segment 该清掉
        yield LLMStreamChunk(content="", tool_round_persisted=True)
        # post-tool 文本（最终回复）
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

    # 用户看到的完整 text：pre-tool + post-tool
    assert "".join(text_emitted) == "正在查询，22 度。"

    # 关键断言：history 里 pre-tool 文本不能被双写
    history = client._conversation_history
    # 期望结构：[system, user, assistant{content:"正在查询，", tool_calls:[...]}, tool, final-AIMessage]
    assistant_with_tool = next(
        m for m in history if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_with_tool["content"] == "正在查询，"
    final_ai = history[-1]
    assert isinstance(final_ai, AIMessage)
    # final AIMessage 不该包含已经持久化的 pre-tool 文本
    assert "正在查询" not in final_ai.content, (
        f"pre-tool 文本被双写进 history 了！final AIMessage.content={final_ai.content!r}"
    )
    assert final_ai.content == "22 度。"


@pytest.mark.asyncio
async def test_stream_text_length_guard_after_tool_call_does_not_double_write_pretool_text(monkeypatch):
    """长度 guard 在 tool 轮之后触发时，history 只能追加未持久化的 post-tool 文本。

    pre-tool 文本已经由 _astream_*_with_tools inline 写进 assistant.tool_calls.content。
    recovery 分支如果把整轮文本再 append 一次，会让下一轮上下文重复看到 pre-tool 文本。
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
    """tool 后续写还没有完整句子时，不能只用 pre-tool 文本当作成功恢复。"""
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
    """Qwen-Omni-Realtime 的 schema 与 StepFun 一致（嵌套 function 形），
    跟 GLM/OpenAI Realtime 的 flat 形不同。这是 Aliyun 文档明确的形状。"""
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
    """GLM 协议：function_call_arguments.done 不返回 call_id（我们合成
    了 glm_<rid>_<idx> 用于内部追踪），且回传 function_call_output 时
    服务端不接受 call_id 字段。这条测试保证 wire 上不外泄合成的伪 id。"""
    from main_logic.tool_calling import ToolResult

    client, sent = _make_rt_client("glm")
    await client._send_tool_result_openai_realtime(ToolResult(
        call_id="glm_resp123_0",  # 内部合成的伪 id
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
    assert sent[1] == {"type": "response.create"}


@pytest.mark.asyncio
async def test_realtime_qwen_tool_result_carries_call_id():
    """Qwen / OpenAI gpt / StepFun：必须回传 call_id，server 用它绑回 function_call。"""
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
    """GLM 文档要求：ServerVAD 时更新 tools 必须同时传入 turn_detection，
    否则服务端可能把 turn_detection reset 成默认。"""
    client, sent = _make_rt_client("glm")
    await client.apply_tools_to_session()
    # update_session 实际上是 send_event({type:"session.update", session:...})
    assert len(sent) == 1
    assert sent[0]["type"] == "session.update"
    sess = sent[0]["session"]
    assert "tools" in sess
    assert sess.get("turn_detection") == {"type": "server_vad"}, (
        "GLM 必须同时传 turn_detection"
    )


@pytest.mark.asyncio
async def test_realtime_apply_tools_to_session_qwen_disables_enable_search():
    """Qwen-Omni-Realtime: tools 与 enable_search 互斥；注册了自定义工具时
    必须显式 enable_search=False，否则服务端会拒绝 session.update。"""
    client, sent = _make_rt_client("qwen")
    await client.apply_tools_to_session()
    sess = sent[0]["session"]
    assert sess.get("enable_search") is False, (
        "Qwen tools / enable_search 互斥，已注册工具时必须显式关闭搜索"
    )
    # tools 必须是嵌套 function 形
    assert sess["tools"][0]["type"] == "function"
    assert "function" in sess["tools"][0]
    assert "name" in sess["tools"][0]["function"]


@pytest.mark.asyncio
async def test_realtime_apply_tools_to_session_step_emits_function_tools_only():
    """stepaudio-2.5-realtime 不再支持内置 web_search；
    apply_tools_to_session 只发送 caller 注册的 function tools，与
    update_session 初始化路径保持一致。"""
    client, sent = _make_rt_client("step")
    await client.apply_tools_to_session()
    tools = sent[0]["session"]["tools"]
    assert all(t.get("type") != "web_search" for t in tools)
    assert any(t.get("type") == "function" for t in tools)


# ============================================================================
# Summary-mode（长回复 emotion-tier 摘要路径）
# ============================================================================

def _build_summary_client(monkeypatch, *, max_response_length: int = 4):
    """组装一个走 summary 路径的 OmniOfflineClient stub。

    单测里 count_tokens 走"按词切空格"，刚好让 ``one two three four. ...``
    这种字符串里每个词都恰好 1 token，方便控制何时跨阈值。
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
    """Summary 路径 happy path：模型超 budget 很多，越过 budget 后第一个
    terminator 后的尾巴替换成 emotion-tier 摘要。UI 看完整原文，TTS 听
    prefix+summary，history 存 prefix+summary。"""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    # 让 summary 调用产出固定字符串
    async def fake_summarize(self, prefix, tail):
        fake_summarize.captured = {"prefix": prefix, "tail": tail}
        return "总之就这样啦"
    fake_summarize.captured = {}
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # budget=4，但要让 cutover 落在越界点之后 —— budget 前的句号不该被当
    # cutover。trigger chunk 后排几个逗号分隔的子句，第一个逗号在 budget
    # 之后，cutover 应该落到那里。尾巴 ≥ 25 tokens 大于 slack，触发摘要。
    prefix_segment = "one two three four."  # 4 words, 用尽 budget
    overshoot_segment = " five, six seven eight nine ten."  # 越界后第一个 terminator 是逗号
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

    # 3 类调用都必须出现
    both_emits = [c for c in delta_calls if c["ui_enabled"] and c["tts_enabled"]]
    ui_only_emits = [c for c in delta_calls if c["ui_enabled"] and not c["tts_enabled"]]
    tts_only_emits = [c for c in delta_calls if not c["ui_enabled"] and c["tts_enabled"]]

    assert both_emits, "cutover 之前的 prefix 必须以 both 模式发出"
    assert ui_only_emits, "cutover 之后的 tail 必须只去 UI"
    assert len(tts_only_emits) == 1, "summary 必须以 tts-only 注入一次"
    assert tts_only_emits[0]["text"] == "总之就这样啦"

    # _summarize_tail_for_tts 收到的 prefix 应当以越界后的 terminator 结尾。
    # 关键：budget 内已经有句号 "four." (offset 18) —— 旧的从 chunk 头扫的实现
    # 会在那里 cutover；overflow-offset 修复后应当跳过它，落到 "five," 处。
    captured_prefix = fake_summarize.captured["prefix"].rstrip()
    assert captured_prefix.endswith(","), (
        "cutover 应该落在越界后的逗号，不应该在 budget 之内的句号 "
        "(actual prefix: %r)" % captured_prefix
    )
    assert fake_summarize.captured["tail"]

    # history 写 prefix + summary（与 TTS 听到的对齐）
    last_msg = client._conversation_history[-1].content
    assert last_msg.endswith("总之就这样啦")
    # prefix 必然包含 budget 之前的部分
    assert "one two three four." in last_msg
    # 越界后才出现的 tail 词不应进 history
    assert "w24" not in last_msg


@pytest.mark.asyncio
async def test_stream_text_summary_abandoned_when_overshoot_under_slack(monkeypatch):
    """超 budget 但只多几个 token（< slack）时放弃摘要，tail 续给 TTS 读完，
    history 留完整原文，没有 prefix/summary 分裂。"""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    summarize_called = []

    async def fake_summarize(self, prefix, tail):
        summarize_called.append((prefix, tail))
        return "should not be used"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # budget=4，模型只写 7 词，7 < 4+25 → abandon。trigger chunk 后必须有
    # 至少一个 terminator 在越界点之后，否则 cutover 退化到流末 / 没 tail。
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
    # 必须看到一次 ui_only 发出（cutover 之后的 tail）+ 一次 tts_only 发出（abandon 时补给 TTS）
    ui_only_emits = [c for c in delta_calls if c["ui_enabled"] and not c["tts_enabled"]]
    tts_only_emits = [c for c in delta_calls if not c["ui_enabled"] and c["tts_enabled"]]
    assert ui_only_emits, "cutover 后的 tail 必须只去 UI"
    assert len(tts_only_emits) == 1, "abandon 路径 tail 必须补一次 TTS-only"
    # 不只验通道，还要验内容：abandon 续给 TTS 的必须是原文 tail，
    # 绝不能是摘要器的返回值（那条 path 不该被走到）。
    tts_text = tts_only_emits[0]["text"]
    assert "six seven." in tts_text, f"TTS 续读应是原文 tail，实际: {tts_text!r}"
    assert "should not be used" not in tts_text

    # history 是完整原文
    assert client._conversation_history[-1].content.strip() == short_overshoot.strip()


@pytest.mark.asyncio
async def test_stream_text_summary_gibberish_fallback_silently_commits_prefix(monkeypatch):
    """cutover 后 tail 在 gibberish 重检阈值上被判定为乱码 → 静默截断：
    不发 RESPONSE_INVALID（那会触发 core 端 _clear_tts_pipeline 把队列里未播完
    的 prefix 一起清掉，反而让用户已经在听的话被截断）。history 只留 prefix，
    TTS 自然把队列残余播完。"""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    # 降低 gibberish 重检阈值，避免单测吐 100+ tokens 才触发
    monkeypatch.setattr(_ofc_streaming, "_SUMMARY_GIBBERISH_RECHECK_TOKENS", 2)

    # 强制 _is_gibberish_response 在尾巴被检测时返 True
    monkeypatch.setattr(_ofc_streaming, "_is_gibberish_response", lambda text: "GIB" in text)

    summarize_called = []

    async def fake_summarize(self, prefix, tail):
        summarize_called.append((prefix, tail))
        return "unused"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # prefix 部分先走 both，cutover 落在越界后的第一个逗号；之后 "GIB" 进 tail，
    # 重检触发 gibberish 命中。
    async def _astream(self, messages, **overrides):
        yield LLMStreamChunk(content="one two three four. five, GIB GIB GIB.")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    discarded: list[dict] = []

    async def fake_text_delta(text, is_first, **kwargs):
        # 这里不关心具体 delta；focus 在 discard 通知上
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
    # 关键：不再发 response_discarded —— 否则 core 会 _clear_tts_pipeline，
    # 把已经在 TTS 队列里的 prefix 一并清掉。
    assert discarded == []

    # history 只保留 prefix（cutover 之前的部分），不含 tail
    last_msg = client._conversation_history[-1].content
    assert "GIB" not in last_msg
    assert last_msg.startswith("one two three four.")


@pytest.mark.asyncio
async def test_stream_text_summary_overflow_offset_consumed_across_chunks(monkeypatch):
    """Trigger chunk 没有 terminator 时 state 停在 pending_cutover，
    `summary_overflow_offset` 必须消费回 0，让下一个 chunk 能从头扫到
    leading terminator。没消费回 0 的话，搜索会跳过 chunk 头部，把 cutover
    错放到 chunk 尾。codex P2 的回归守门。"""
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    summarize_called: list = []

    async def fake_summarize(self, prefix, tail):
        summarize_called.append((prefix, tail))
        return "总之"
    monkeypatch.setattr(OmniOfflineClient, "_summarize_tail_for_tts", fake_summarize)

    # Trigger chunk: 5 词无 terminator (budget=4) → state 切到 pending_cutover，
    # 找不到 terminator，offset 必须消费回 0。
    # 下一个 chunk 头部就有逗号；如果 offset 没消费，搜索从 > 0 起会跳过它。
    async def _astream(self, messages, **overrides):
        # 单独 yield，模拟 provider 多 chunk 流；chunk 2 头部加 leading space
        # 避免 token 边界粘连导致 word-split count_tokens 把 "e" 和 "h," 合并。
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

    # cutover 必须发生（summary 被调），并且 prefix 应该结束于第二 chunk
    # 头部的逗号 —— 这只有在 offset 消费回 0 时才可能。
    assert summarize_called, "second chunk 必须能 cutover（offset 已被消费）"
    captured_prefix = summarize_called[0][0]
    assert captured_prefix.endswith("h,"), (
        "cutover 应该落在 second chunk 头部的逗号 (offset=0)，没消费回 0 "
        "时会跳过该逗号。实际 prefix: %r" % captured_prefix
    )


@pytest.mark.asyncio
async def test_stream_text_summary_budget_bump_scoped_to_stream_text(monkeypatch):
    """summary 模式只在 stream_text 期间把 self.llm.max_completion_tokens 抬到
    _SUMMARY_API_BUDGET_FLOOR，结束后精确还原 —— 不泄漏给共用同一 self.llm 的
    prompt_ephemeral（proactive 没长度 guard，被抬到 3000 会吐超长回复）。"""
    from types import SimpleNamespace
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _budget_to_max_tokens, _SUMMARY_API_BUDGET_FLOOR,
    )
    from utils.llm_client import LLMStreamChunk

    observed: dict = {}

    async def _astream(self, messages, **overrides):
        # 记录流式进行中 client 上的 cap
        observed["during"] = self.llm.max_completion_tokens
        yield LLMStreamChunk(content="hi there.")

    monkeypatch.setattr(OmniOfflineClient, "_astream_with_tools", _astream)

    async def noop(*_a, **_kw):
        pass

    client = _build_summary_client(monkeypatch, max_response_length=4)
    normal_budget = _budget_to_max_tokens(4)  # budget+slack，远小于 3000 floor
    client.llm = SimpleNamespace(max_completion_tokens=normal_budget)
    client.on_text_delta = noop
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = None
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("trigger")

    # 流中被抬到 floor（normal_budget < 3000）
    assert observed["during"] == _SUMMARY_API_BUDGET_FLOOR
    # 流结束后精确还原到原值，不泄漏给 prompt_ephemeral
    assert client.llm.max_completion_tokens == normal_budget


@pytest.mark.asyncio
async def test_stream_text_summary_disabled_keeps_old_truncate_behavior(monkeypatch):
    """没开 summary 的 client（game/默认 stream_text 调用）必须保持原来的
    abort+inline truncate 行为，不被新路径干扰，并且小模型摘要器一次也不能调。"""
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
        # 6 词，budget=4 → 过线触发旧 guard 路径
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
    client.enable_long_response_summary = False  # 关键
    client.vision_model = ""
    client.model = "x"
    client.on_text_delta = fake_text_delta
    client.on_input_transcript = noop
    client.on_response_done = noop
    client.on_response_discarded = fake_notify_discarded
    client.on_status_message = noop
    client.on_repetition_detected = None

    await client.stream_text("disabled summary")

    # 旧路径：所有 emit 都是 both（没 ui/tts 拆分）
    assert delta_calls, "至少要 emit 过一次"
    for c in delta_calls:
        assert c["ui_enabled"] and c["tts_enabled"]
    # 关键回归：禁用模式下小模型摘要器一次都不能被调
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
