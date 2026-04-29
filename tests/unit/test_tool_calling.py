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
import types as _types
from pathlib import Path

import pytest

# Ensure the project root is importable when pytest is invoked from
# anywhere (mirrors other tests/unit/* files).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


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


@pytest.mark.asyncio
async def test_offline_iteration_cap_breaks_runaway_loop():
    """If the model keeps requesting tools forever, we stop after
    ``max_tool_iterations`` LLM calls instead of looping indefinitely."""
    from utils.llm_client import LLMStreamChunk
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult

    async def loop_tool(args):
        return {"ok": True}

    tool = ToolDefinition(name="loop", description="", handler=loop_tool)

    # Every scripted call returns another tool_call.
    def chunks():
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

    fake_llm = _FakeLLM([chunks() for _ in range(10)])

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client.llm = fake_llm
    client._tool_definitions = [tool]
    client.max_tool_iterations = 3
    client._use_genai_sdk = False
    client._genai_tools_unsupported = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "loop forever"}]
    async for _ in client._astream_with_tools(messages):
        pass

    # Exactly max_tool_iterations LLM calls occurred — no infinite loop.
    assert len(fake_llm.calls) == 3


# ---------------------------------------------------------------------------
# 4. OmniRealtimeClient wire-format helpers
# ---------------------------------------------------------------------------

def test_realtime_tools_for_step_uses_nested_function_shape():
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._tool_definitions = [ToolDefinition(
        name="x", description="d",
        parameters={"type": "object", "properties": {}},
        handler=lambda _: None,
    )]
    client.on_tool_call = lambda _c: None  # truthy, so has_tools() == True
    out = client._tools_for_step()
    assert out == [{
        "type": "function",
        "function": {"name": "x", "description": "d", "parameters": {"type": "object", "properties": {}}},
    }]


def test_realtime_tools_for_openai_realtime_is_flat():
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._tool_definitions = [ToolDefinition(
        name="x", description="d", parameters={"type": "object", "properties": {}},
        handler=lambda _: None,
    )]
    client.on_tool_call = lambda _c: None
    out = client._tools_for_openai_realtime()
    assert out == [{"type": "function", "name": "x", "description": "d",
                    "parameters": {"type": "object", "properties": {}}}]


def test_realtime_tools_for_qwen_uses_nested_function_shape():
    """Qwen-Omni-Realtime 的 schema 与 StepFun 一致（嵌套 function 形），
    跟 GLM/OpenAI Realtime 的 flat 形不同。这是 Aliyun 文档明确的形状。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._tool_definitions = [ToolDefinition(
        name="get_weather", description="天气",
        parameters={"type": "object", "properties": {"city": {"type": "string"}}},
        handler=lambda _: None,
    )]
    client.on_tool_call = lambda _c: None
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
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolResult

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._api_type = "glm"
    client._fatal_error_occurred = True  # 阻止真正发包；只验构造逻辑
    sent = []

    async def fake_send_event(ev):
        sent.append(ev)

    client.send_event = fake_send_event

    # 强制绕过 _fatal_error_occurred 检查 —— 直接 patch send_event 已避开
    # WebSocket，但 _send_tool_result_openai_realtime 自己不查 fatal flag,
    # 所以这里安全。
    client._fatal_error_occurred = False
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
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolResult

    for api in ("qwen", "gpt", "step", "free"):
        client = OmniRealtimeClient.__new__(OmniRealtimeClient)
        client._api_type = api
        client._fatal_error_occurred = False
        sent = []

        async def fake_send_event(ev, _sent=sent):
            _sent.append(ev)

        client.send_event = fake_send_event

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
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._api_type = "glm"
    client._is_gemini = False
    client._gemini_session = None
    client.ws = object()  # 任意非空，触发"已连接"分支
    client._fatal_error_occurred = False
    client._tool_definitions = [ToolDefinition(name="x", description="", handler=lambda _: 0)]
    client.on_tool_call = lambda _c: None
    sent = []

    async def fake_send_event(ev, _sent=sent):
        _sent.append(ev)

    client.send_event = fake_send_event

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
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._api_type = "qwen"
    client._is_gemini = False
    client._gemini_session = None
    client.ws = object()
    client._fatal_error_occurred = False
    client._tool_definitions = [ToolDefinition(name="x", description="", handler=lambda _: 0)]
    client.on_tool_call = lambda _c: None
    sent = []

    async def fake_send_event(ev, _sent=sent):
        _sent.append(ev)

    client.send_event = fake_send_event

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
async def test_realtime_apply_tools_to_session_step_keeps_web_search():
    """StepFun apply_tools 必须保留内置 web_search 工具，否则 server 会把
    用户 disable web_search 的状态当作 mid-session 撤销，影响其他对话功能。"""
    from main_logic.omni_realtime_client import OmniRealtimeClient
    from main_logic.tool_calling import ToolDefinition

    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._api_type = "step"
    client._is_gemini = False
    client._gemini_session = None
    client.ws = object()
    client._fatal_error_occurred = False
    client._tool_definitions = [ToolDefinition(name="x", description="", handler=lambda _: 0)]
    client.on_tool_call = lambda _c: None
    sent = []

    async def fake_send_event(ev, _sent=sent):
        _sent.append(ev)

    client.send_event = fake_send_event

    await client.apply_tools_to_session()
    tools = sent[0]["session"]["tools"]
    assert any(t.get("type") == "web_search" for t in tools)
    assert any(t.get("type") == "function" for t in tools)
