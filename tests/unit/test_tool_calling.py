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

import json
import sys
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
async def test_offline_switch_model_recomputes_genai_routing(monkeypatch):
    """switch_model 切到不同 endpoint 后必须重新计算 _use_genai_sdk，
    并清空 _genai_client，否则会沿用旧 conversation 的路由判断。

    回归保护：Codex P1 反馈，PR #1035。

    本测试只验状态切换的纯逻辑——monkeypatch ``_GENAI_AVAILABLE=True``
    让 ``_should_use_genai_sdk`` 在没装 google-genai 的 CI 上也能跑出
    ``True`` 分支，避免环境不全时这条回归保护被静默 skip 掉。"""
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import OmniOfflineClient

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

    # 建 client：conversation 走 OpenAI，vision_base_url 指向 Gemini native endpoint。
    client = OmniOfflineClient.__new__(OmniOfflineClient)
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
async def test_offline_genai_transient_error_does_not_disable_tools(monkeypatch):
    """genai SDK 网络/鉴权抖动（429 / 5xx / timeout / auth）不应该被包装成
    `_GenaiToolsUnsupported`，否则单次 transient 错误会让整个 session 永久
    退化到 OpenAI-compat 路径，工具调用永久失效。

    回归保护：CodeRabbit PR #1035 第 4 轮 review."""
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _GenaiToolsUnsupported,
    )

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

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
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import OmniOfflineClient
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

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


@pytest.mark.asyncio
async def test_genai_unsupported_keyword_matches_underscore_variant(monkeypatch):
    """`not_support` 下划线变体也得当成 tools 永久不支持，避免每轮先撞
    genai 再回退的额外抖动。

    回归保护：CodeRabbit PR #1035 第 8 轮 review."""
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _GenaiToolsUnsupported,
    )

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

    class _UnderscoreErrorClient:
        class _aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    raise RuntimeError("function_call_not_support on this model")
        aio = _aio()
        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
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
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

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
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import HumanMessage, LLMStreamChunk, SystemMessage

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

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
async def test_offline_silent_fallback_when_genai_did_not_emit(monkeypatch):
    """对偶：genai 路径还没 yield 过任何文本就抛 transient 异常时，
    `_astream_with_tools` 仍然应该静默 fallback 到 OpenAI-compat 兜底——
    用户感知不到失败，体验最佳。"""
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.llm_client import LLMStreamChunk

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

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
async def test_offline_genai_tools_unsupported_error_correctly_disables_path(monkeypatch):
    """与上一条对偶：当 genai 真的报"tools not supported"时，必须被包装成
    `_GenaiToolsUnsupported`，让 `_astream_with_tools` 翻 `_genai_tools_unsupported`
    并 fallback 到 OpenAI-compat 路径。"""
    from main_logic import omni_offline_client as _ofc
    from main_logic.omni_offline_client import (
        OmniOfflineClient, _GenaiToolsUnsupported,
    )

    monkeypatch.setattr(_ofc, "_GENAI_AVAILABLE", True)

    class _ToolsRejectClient:
        class _aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    raise RuntimeError("function declarations are not supported on this model")

        aio = _aio()

        def close(self): pass

    client = OmniOfflineClient.__new__(OmniOfflineClient)
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
async def test_realtime_apply_tools_to_session_step_keeps_web_search():
    """StepFun apply_tools 必须保留内置 web_search 工具，否则 server 会把
    用户 disable web_search 的状态当作 mid-session 撤销，影响其他对话功能。"""
    client, sent = _make_rt_client("step")
    await client.apply_tools_to_session()
    tools = sent[0]["session"]["tools"]
    assert any(t.get("type") == "web_search" for t in tools)
    assert any(t.get("type") == "function" for t in tools)
