# -*- coding: utf-8 -*-
"""Verbatim round-trip of ``extra_content`` / ``thought_signature`` through
the shared tool-call history.

Gemini thinking models require the signature that came down with a function
call to be handed back with that same call on every later request. A history
that keeps only id/name/args gets a stable 400 INVALID_ARGUMENT from the
second round onwards (observed on the international free route's
``recall_memory`` recall).

The two links carry it in different shapes:
  - OpenAI-compat (Google's compat endpoint / the lanlan.app free route):
    ``tool_calls[].extra_content.google.thought_signature``, a base64 string
  - native google-genai: ``Part.thought_signature``, raw bytes

The shared history always stores the former: JSON-serializable, and the two
paths can replay each other's history.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import main_logic.omni_offline_client._genai_support as _ofc_genai

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# base64 of b"sig-123" — the shape the Gemini compat endpoint sends down.
_SIGNATURE_EXTRA = {"google": {"thought_signature": "c2lnLTEyMw=="}}


class _FakeAsyncStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self._chunks:
            yield c


class _FakeLLM:
    """Drop-in for ``self.llm``: pops one scripted chunk batch per astream."""

    def __init__(self, scripted_chunks_per_call, max_completion_tokens=100):
        self._scripted = list(scripted_chunks_per_call)
        self.calls = []
        self.max_completion_tokens = max_completion_tokens

    def astream(self, messages, **overrides):
        self.calls.append((messages, overrides))
        if not self._scripted:
            raise RuntimeError("FakeLLM ran out of scripted responses")
        return _FakeAsyncStream(self._scripted.pop(0))

    async def aclose(self):
        pass


def _bare_offline_client():
    """``__new__``-built client with only the baseline attributes the tool
    loop reads (mirrors ``_init_bare`` in test_tool_calling.py)."""
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.vision_provider_type = None
    client._genai_tools_unsupported = False
    return client


# ---------------------------------------------------------------------------
# 1. Wire ingest: SDK object -> tool_call_deltas -> aggregate
# ---------------------------------------------------------------------------


def test_collect_tool_calls_preserves_extra_content():
    """extra_content must aggregate onto the call it arrived with."""
    from utils.llm_client import ChatOpenAI

    deltas_per_chunk = [
        [
            {"index": 0, "id": "c1", "type": "function",
             "function": {"name": "recall_memory", "arguments": '{"q":'},
             "extra_content": _SIGNATURE_EXTRA},
            {"index": 1, "id": "c2", "type": "function",
             "function": {"name": "other_tool", "arguments": "{}"}},
        ],
        [{"index": 0, "function": {"name": "", "arguments": '"x"}'}}],
    ]
    out = ChatOpenAI.collect_tool_calls(deltas_per_chunk)
    assert [c.name for c in out] == ["recall_memory", "other_tool"]
    assert out[0].extra_content == _SIGNATURE_EXTRA
    # A call without extra_content stays None so ordinary providers keep a
    # clean history.
    assert out[1].extra_content is None


def test_collect_tool_calls_merges_split_extra_content():
    """One call's extra_content split across chunks merges per vendor
    namespace; whole-blob overwrite would silently drop the earlier
    signature."""
    from utils.llm_client import ChatOpenAI

    deltas_per_chunk = [
        [{"index": 0, "id": "c1", "function": {"name": "t", "arguments": "{}"},
          "extra_content": {"google": {"thought_signature": "AAA="}}}],
        [{"index": 0, "function": {"name": "", "arguments": ""},
          "extra_content": {"google": {"other_hint": 1}, "vendor2": {"k": "v"}}}],
    ]
    out = ChatOpenAI.collect_tool_calls(deltas_per_chunk)
    assert out[0].extra_content == {
        "google": {"thought_signature": "AAA=", "other_hint": 1},
        "vendor2": {"k": "v"},
    }


@pytest.mark.asyncio
async def test_openai_astream_forwards_tool_call_extra_content():
    """The non-standard ``extra_content`` field on the SDK's tool_call object
    must reach tool_call_deltas — nothing downstream can recover it."""
    from utils.llm_client import ChatOpenAI

    raw_tool_call = SimpleNamespace(
        index=0, id="c1", type="function",
        function=SimpleNamespace(name="recall_memory", arguments="{}"),
        extra_content=_SIGNATURE_EXTRA,
    )

    class _Stream:
        def __aiter__(self):
            return self._iter()

        async def _iter(self):
            yield SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="", tool_calls=[raw_tool_call]),
                    finish_reason="tool_calls",
                )],
                usage=None,
            )

    async def _create(**_kw):
        return _Stream()

    client = ChatOpenAI.__new__(ChatOpenAI)
    client._params = lambda messages, **kw: {"model": "gemini-3-pro"}
    client._aclient = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )

    chunks = [c async for c in client.astream([{"role": "user", "content": "hi"}])]
    deltas = [d for c in chunks if c.tool_call_deltas for d in c.tool_call_deltas]
    assert len(deltas) == 1
    assert deltas[0]["extra_content"] == _SIGNATURE_EXTRA


# ---------------------------------------------------------------------------
# 2. OpenAI-compat tool loop writing history back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_openai_tool_loop_echoes_extra_content_to_history():
    """The assistant.tool_calls entry the loop appends must carry
    extra_content verbatim — that history IS the next request body."""
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
    from utils.llm_client import LLMStreamChunk

    tool_def = ToolDefinition(
        name="recall_memory", description="recall",
        parameters={"type": "object", "properties": {}},
    )
    chunks_call_1 = [
        LLMStreamChunk(content="", tool_call_deltas=[{
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": "recall_memory", "arguments": "{}"},
            "extra_content": _SIGNATURE_EXTRA,
        }]),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_call_2 = [LLMStreamChunk(content="想起来了喵。", finish_reason="stop")]

    client = _bare_offline_client()
    client.llm = _FakeLLM([chunks_call_1, chunks_call_2])
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "还记得吗"}]
    async for _ in client._astream_with_tools(messages):
        pass

    assistant_turn = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert assistant_turn["tool_calls"][0]["extra_content"] == _SIGNATURE_EXTRA, (
        "工具调用历史必须原样保存 extra_content（thought_signature），"
        "否则 Gemini 第二轮起稳定报 400 INVALID_ARGUMENT"
    )


@pytest.mark.asyncio
async def test_offline_openai_tool_loop_omits_extra_content_when_absent():
    """Dual: no provider blob means no such key in history — an unknown
    field can get a plain OpenAI endpoint to reject the request."""
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
    from utils.llm_client import LLMStreamChunk

    tool_def = ToolDefinition(
        name="t", description="t", parameters={"type": "object", "properties": {}},
    )
    chunks_call_1 = [
        LLMStreamChunk(content="", tool_call_deltas=[{
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": "t", "arguments": "{}"},
        }]),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_call_2 = [LLMStreamChunk(content="done", finish_reason="stop")]

    client = _bare_offline_client()
    client.llm = _FakeLLM([chunks_call_1, chunks_call_2])
    client._tool_definitions = [tool_def]
    client.max_tool_iterations = 4
    client._use_genai_sdk = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "x"}]
    async for _ in client._astream_with_tools(messages):
        pass

    assistant_turn = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert "extra_content" not in assistant_turn["tool_calls"][0]


# ---------------------------------------------------------------------------
# 3. native genai: Part.thought_signature <-> history
# ---------------------------------------------------------------------------


class _Part:
    def __init__(self, *, text=None, function_call=None, thought_signature=None):
        self.text = text
        self.function_call = function_call
        self.thought_signature = thought_signature


class _FunctionCall:
    def __init__(self, name, args, id_=""):
        self.name = name
        self.args = args
        self.id = id_


class _Chunk:
    def __init__(self, parts):
        content = type("K", (), {"parts": parts})()
        self.candidates = [type("C", (), {"content": content})()]
        self.usage_metadata = None


class _StreamWrapper:
    def __init__(self, gen):
        self._gen = gen

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self._gen.__anext__()


def _genai_client_for(round1, round2):
    """Fake genai client: first generate_content_stream call yields the tool
    round, the second the follow-up text."""
    call_count = [0]

    class _FakeClient:
        class aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    call_count[0] += 1
                    gen = round1() if call_count[0] == 1 else round2()
                    return _StreamWrapper(gen)

        def close(self):
            pass

    return _FakeClient()


def _genai_client_state(client, fake_client):
    client.model = "gemini-3-pro"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False
    client.max_tool_iterations = 3
    client._genai_client = fake_client
    client.llm = type("F", (), {"max_completion_tokens": 100})()


@pytest.mark.asyncio
async def test_offline_genai_persists_thought_signature_into_history(monkeypatch):
    """Native genai path: thought_signature hangs off the Part (not the
    FunctionCall), so it must be captured while streaming and stored in the
    shared history in the extra_content shape."""
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _round1():
        yield _Chunk([_Part(
            function_call=_FunctionCall("recall_memory", {"q": "x"}, id_="c1"),
            thought_signature=b"sig-123",
        )])

    async def _round2():
        yield _Chunk([_Part(text="想起来了喵。")])

    client = _bare_offline_client()
    _genai_client_state(client, _genai_client_for(_round1, _round2))

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={"ok": True})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "还记得吗"}]
    async for _ in client._astream_genai_with_tools(messages):
        pass

    assistant_turn = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    stored = assistant_turn["tool_calls"][0].get("extra_content")
    assert stored is not None, "genai 路径必须把 Part.thought_signature 存进历史"
    assert base64.b64decode(stored["google"]["thought_signature"]) == b"sig-123"


@pytest.mark.asyncio
async def test_offline_genai_no_signature_keeps_history_clean(monkeypatch):
    """Dual: a model that sends no signature leaves history without the key."""
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _round1():
        yield _Chunk([_Part(function_call=_FunctionCall("t", {}, id_="c1"))])

    async def _round2():
        yield _Chunk([_Part(text="done")])

    client = _bare_offline_client()
    _genai_client_state(client, _genai_client_for(_round1, _round2))

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "x"}]
    async for _ in client._astream_genai_with_tools(messages):
        pass

    assistant_turn = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
    assert "extra_content" not in assistant_turn["tool_calls"][0]


def test_genai_messages_to_contents_replays_thought_signature():
    """The base64 signature in history must decode back to bytes on the
    rebuilt function_call Part — that is the only thing making Gemini accept
    the replayed history."""
    pytest.importorskip("google.genai")
    from main_logic.omni_offline_client import _genai_messages_to_contents

    messages = [
        {"role": "user", "content": "还记得吗"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "recall_memory", "arguments": "{}"},
             "extra_content": _SIGNATURE_EXTRA},
            {"id": "c2", "type": "function",
             "function": {"name": "other_tool", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "recall_memory",
         "content": '{"ok": true}'},
    ]
    _, contents = _genai_messages_to_contents(messages)
    model_turn = next(c for c in contents if c.role == "model")
    fc_parts = [p for p in model_turn.parts if getattr(p, "function_call", None)]
    assert len(fc_parts) == 2
    assert fc_parts[0].thought_signature == b"sig-123"
    # A call with no signature must not be given a fabricated one.
    assert not fc_parts[1].thought_signature


def test_genai_messages_to_contents_survives_malformed_signature():
    """A history polluted with invalid base64 degrades to "no signature"
    instead of blowing up the whole conversation."""
    pytest.importorskip("google.genai")
    from main_logic.omni_offline_client import _genai_messages_to_contents

    messages = [{"role": "assistant", "content": "", "tool_calls": [{
        "id": "c1", "type": "function",
        "function": {"name": "t", "arguments": "{}"},
        "extra_content": {"google": {"thought_signature": "not!base64!"}},
    }]}]
    _, contents = _genai_messages_to_contents(messages)
    part = next(p for p in contents[0].parts if getattr(p, "function_call", None))
    assert not part.thought_signature
