"""Regression tests for google-genai thinking config (R1111-P2-18) and TokenTracker usage (R1111-P2-19)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.omni_offline_client import _genai_support, OmniOfflineClient
from main_logic.tool_calling import ToolDefinition, ToolResult
from utils.token_tracker import TokenTracker, _current_call_type


class _FakeThinkingConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeGenerateContentConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeTool:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakePart:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        for k, v in kwargs.items():
            setattr(self, k, v)

    @staticmethod
    def from_text(**kw):
        return _FakePart(**kw)

    @staticmethod
    def from_bytes(**kw):
        return _FakePart(**kw)

    @staticmethod
    def from_function_call(**kw):
        return _FakePart(**kw)

    @staticmethod
    def from_function_response(**kw):
        return _FakePart(**kw)


def _install_fake_genai(monkeypatch, captured_configs: list, stream_factory):
    fake_types = SimpleNamespace(
        Content=lambda **kw: SimpleNamespace(**kw),
        Part=_FakePart,
        FunctionDeclaration=lambda **kw: SimpleNamespace(**kw),
        FunctionCall=lambda **kw: SimpleNamespace(**kw),
        FunctionResponse=lambda **kw: SimpleNamespace(**kw),
        Tool=_FakeTool,
        ThinkingConfig=_FakeThinkingConfig,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )

    async def _gen_stream(*, model, contents, config):
        captured_configs.append((model, config))
        return stream_factory(len(captured_configs))

    fake_client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content_stream=_gen_stream),
        )
    )
    fake_genai = SimpleNamespace(Client=lambda **kw: fake_client)

    monkeypatch.setattr(_genai_support, "_genai", fake_genai)
    monkeypatch.setattr(_genai_support, "_genai_types", fake_types)
    monkeypatch.setattr(_genai_support, "_GENAI_AVAILABLE", True)
    return fake_client


def _make_client(model: str = "gemini-2.5-flash", *, llm_extra_body=None, max_tokens: int = 2048):
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client.model = model
    client.base_url = "https://generativelanguage.googleapis.com/v1beta/openai/"
    client.api_key = "test-key"
    client.llm = SimpleNamespace(max_completion_tokens=max_tokens, extra_body=llm_extra_body)
    client.max_tool_iterations = 2
    client.tool_timeout_seconds = 5.0
    client._tool_definitions = []
    client.on_tool_call = None
    client._genai_client = None
    client._genai_tools_unsupported = False
    client._last_finish_reason = None
    client._last_block_reason = None
    client._last_prompt_tokens = None
    client._publish_pending_tool_frames = lambda *args, **kwargs: None
    return client


async def _async_iter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_genai_threads_extra_body_thinking_config_and_token_limit_overrides(monkeypatch):
    from config.providers import EXTRA_BODY_GEMINI_THINKING

    captured_configs: list = []

    def stream_factory(_call_idx):
        return _async_iter([
            SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        finish_reason="STOP",
                        content=SimpleNamespace(parts=[SimpleNamespace(text="hello", thought=False, function_call=None)]),
                    )
                ],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=12,
                    candidates_token_count=5,
                    thoughts_token_count=3,
                    total_token_count=20,
                    cached_content_token_count=2,
                ),
            )
        ])

    _install_fake_genai(monkeypatch, captured_configs, stream_factory)
    client = _make_client(
        model="gemini-2.5-flash",
        llm_extra_body={"extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}}},
        max_tokens=1000,
    )

    chunks = [
        c
        async for c in client._astream_genai_with_tools(
            [{"role": "user", "content": "hi"}],
            extra_body=EXTRA_BODY_GEMINI_THINKING,
            max_completion_tokens=1800,
        )
    ]
    assert [c.content for c in chunks if c.content] == ["hello"]
    assert len(captured_configs) == 1
    cfg = captured_configs[0][1]
    assert cfg.kwargs["max_output_tokens"] == 1800
    assert isinstance(cfg.kwargs["thinking_config"], _FakeThinkingConfig)
    assert cfg.kwargs["thinking_config"].kwargs == {"thinking_budget": 800}


@pytest.mark.asyncio
async def test_genai_explicit_none_extra_body_and_forced_finalize_thinking_config(monkeypatch):
    captured_configs: list = []

    def stream_factory(call_idx):
        if call_idx <= 1:
            return _async_iter([
                SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            finish_reason="STOP",
                            content=SimpleNamespace(
                                parts=[
                                    SimpleNamespace(
                                        text=None,
                                        thought=False,
                                        function_call=SimpleNamespace(id="c1", name="ping", args={}),
                                    )
                                ]
                            ),
                        )
                    ],
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=10,
                        candidates_token_count=4,
                        thoughts_token_count=0,
                        total_token_count=14,
                    ),
                )
            ])
        return _async_iter([
            SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        finish_reason="STOP",
                        content=SimpleNamespace(parts=[SimpleNamespace(text="final", thought=False)]),
                    )
                ],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=20,
                    candidates_token_count=6,
                    thoughts_token_count=2,
                    total_token_count=28,
                ),
            )
        ])

    _install_fake_genai(monkeypatch, captured_configs, stream_factory)
    client = _make_client(
        model="gemini-3-flash-preview",
        llm_extra_body={"extra_body": {"google": {"thinking_config": {"thinking_level": "low", "include_thoughts": False}}}},
        max_tokens=1024,
    )
    client.max_tool_iterations = 1
    client._tool_definitions = [ToolDefinition(name="ping", description="ping", parameters={"type": "object", "properties": {}})]
    client.on_tool_call = AsyncMock(return_value=ToolResult(call_id="c1", name="ping", output={"ok": True}))

    # Turn 1: explicit extra_body=None must suppress llm's thinking_config.
    _ = [c async for c in client._astream_genai_with_tools([{"role": "user", "content": "hi"}], extra_body=None)]
    assert len(captured_configs) == 2
    assert "thinking_config" not in captured_configs[0][1].kwargs
    assert "thinking_config" not in captured_configs[1][1].kwargs

    # Turn 2: no override uses llm.extra_body across both loop and forced-finalize.
    captured_configs.clear()
    out = [c async for c in client._astream_genai_with_tools([{"role": "user", "content": "hi"}], max_tokens=512)]
    assert [c.content for c in out if c.content] == ["final"]
    assert len(captured_configs) == 2
    for _, cfg in captured_configs:
        assert cfg.kwargs["max_output_tokens"] == 512
        assert cfg.kwargs["thinking_config"].kwargs == {"thinking_level": "low", "include_thoughts": False}


@pytest.mark.asyncio
async def test_genai_records_token_tracker_usage_for_stream_trailing_chunk_finalize_and_errors(monkeypatch):
    recorded: list[dict] = []

    class _StubTracker:
        def record(self, **kwargs):
            recorded.append(kwargs)

    monkeypatch.setattr(TokenTracker, "get_instance", classmethod(lambda cls: _StubTracker()))

    captured_configs: list = []

    def stream_factory(call_idx):
        if call_idx == 1:
            # Tool call + trailing usage-only chunk with candidates=[]
            return _async_iter([
                SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            finish_reason="STOP",
                            content=SimpleNamespace(
                                parts=[
                                    SimpleNamespace(
                                        text=None,
                                        thought=False,
                                        function_call=SimpleNamespace(id="c1", name="ping", args={}),
                                    )
                                ]
                            ),
                        )
                    ],
                    usage_metadata=None,
                ),
                SimpleNamespace(
                    candidates=[],
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=30,
                        candidates_token_count=10,
                        thoughts_token_count=5,
                        total_token_count=45,
                        cached_content_token_count=4,
                    ),
                ),
            ])
        if call_idx == 2:
            # Forced finalize succeeds
            return _async_iter([
                SimpleNamespace(
                    candidates=[
                        SimpleNamespace(
                            finish_reason="STOP",
                            content=SimpleNamespace(parts=[SimpleNamespace(text="done", thought=False)]),
                        )
                    ],
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=40,
                        candidates_token_count=12,
                        thoughts_token_count=3,
                        total_token_count=55,
                        cached_content_token_count=0,
                    ),
                )
            ])
        raise RuntimeError("upstream 503")

    _install_fake_genai(monkeypatch, captured_configs, stream_factory)
    client = _make_client(model="gemini-2.5-flash", llm_extra_body=None)
    client.max_tool_iterations = 1
    client._tool_definitions = [ToolDefinition(name="ping", description="ping", parameters={"type": "object", "properties": {}})]
    client.on_tool_call = AsyncMock(return_value=ToolResult(call_id="c1", name="ping", output={"ok": True}))

    token = _current_call_type.set("conversation")
    try:
        chunks = [c async for c in client._astream_genai_with_tools([{"role": "user", "content": "hi"}])]
        usage_chunks = [c for c in chunks if getattr(c, "usage_metadata", None)]
        assert len(usage_chunks) == 1
        assert usage_chunks[0].usage_metadata == {
            "prompt_tokens": 30,
            "completion_tokens": 15,
            "total_tokens": 45,
        }
        assert len(recorded) == 2
        assert recorded[0] == {
            "model": "gemini-2.5-flash",
            "prompt_tokens": 30,
            "completion_tokens": 15,
            "total_tokens": 45,
            "cached_tokens": 4,
            "call_type": "conversation",
            "source": "main_logic/omni_offline_client",
            "success": True,
        }
        assert recorded[1] == {
            "model": "gemini-2.5-flash",
            "prompt_tokens": 40,
            "completion_tokens": 15,
            "total_tokens": 55,
            "cached_tokens": 0,
            "call_type": "conversation",
            "source": "main_logic/omni_offline_client",
            "success": True,
        }

        # Error call must record success=False once.
        with pytest.raises(RuntimeError, match="upstream 503"):
            _ = [c async for c in client._astream_genai_with_tools([{"role": "user", "content": "fail"}])]
        assert len(recorded) == 3
        assert recorded[2]["success"] is False
        assert recorded[2]["call_type"] == "conversation"
    finally:
        _current_call_type.reset(token)
