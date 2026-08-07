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


# The signature bytes deliberately encode to a string containing BOTH '+' and
# '/', so the standard and URL-safe base64 alphabets disagree on it. A neutral
# fixture (e.g. b"sig-123" -> "c2lnLTEyMw==", identical under both alphabets)
# would leave the encode/decode pair free to drift to another alphabet with
# every test still green — and the alphabet is the one thing that decides
# whether a real signature survives the round trip.
_SIGNATURE_BYTES = b"\xfb\xef\xbe\x03\xff\xe0sig"
_SIGNATURE_B64 = "++++A//gc2ln"  # standard alphabet; URL-safe would be "----A__gc2ln"
_SIGNATURE_EXTRA = {"google": {"thought_signature": _SIGNATURE_B64}}

# 9 bytes divides by 3, so _SIGNATURE_B64 needs no padding at all — it cannot
# exercise the "re-pad a stripped string" branch. This 10-byte twin encodes to a
# "==" tail, and still differs between the two alphabets.
_PADDED_BYTES = _SIGNATURE_BYTES + b"\xff"
_PADDED_B64 = "++++A//gc2ln/w=="


def test_signature_fixture_discriminates_base64_alphabets():
    """Guard on the guard: if the fixtures ever lose their alphabet-specific
    characters — or the padded twin stops needing padding — every other test in
    this file silently stops being able to catch an encoder/decoder alphabet or
    padding change."""
    assert base64.b64encode(_SIGNATURE_BYTES).decode() == _SIGNATURE_B64
    assert base64.urlsafe_b64encode(_SIGNATURE_BYTES).decode() != _SIGNATURE_B64
    assert base64.b64encode(_PADDED_BYTES).decode() == _PADDED_B64
    assert base64.urlsafe_b64encode(_PADDED_BYTES).decode() != _PADDED_B64
    assert _PADDED_B64.endswith("==") and "=" not in _SIGNATURE_B64


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
            thought_signature=_SIGNATURE_BYTES,
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
    encoded = stored["google"]["thought_signature"]
    assert base64.b64decode(encoded) == _SIGNATURE_BYTES
    # Pin the alphabet, not just round-trippability: the compat endpoint's own
    # strings are what this history is replayed as.
    assert encoded == _SIGNATURE_B64


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
    assert fc_parts[0].thought_signature == _SIGNATURE_BYTES
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


@pytest.mark.parametrize("encoded,expected", [
    # standard alphabet, length needs no padding
    (_SIGNATURE_B64, _SIGNATURE_BYTES),
    (_SIGNATURE_B64.replace("+", "-").replace("/", "_"), _SIGNATURE_BYTES),
    # standard alphabet, padded — and the same string with its padding stripped,
    # which is the only case that reaches the re-padding branch
    (_PADDED_B64, _PADDED_BYTES),
    (_PADDED_B64.rstrip("="), _PADDED_BYTES),
    # URL-safe, padded and unpadded — the exact forms google-genai's own pydantic
    # serializer produces and accepts
    (_PADDED_B64.replace("+", "-").replace("/", "_"), _PADDED_BYTES),
    (_PADDED_B64.replace("+", "-").replace("/", "_").rstrip("="), _PADDED_BYTES),
])
def test_thought_signature_decodes_both_base64_alphabets(encoded, expected):
    """Only the half we write ourselves is guaranteed standard+padded; the other
    half is whatever the compat endpoint sent down, and google-genai's own
    pydantic serializer emits the URL-safe alphabet. An alphabet or padding
    difference must not silently drop the signature — that would put the very
    400 this change fixes right back."""
    decoded = _ofc_genai._thought_signature_from_extra_content(
        {"google": {"thought_signature": encoded}}
    )
    assert decoded == expected


def test_thought_signature_rejects_garbage_instead_of_decoding_it():
    """Accepting two alphabets must not slide into accepting anything: without
    strict validation, base64 silently DISCARDS non-alphabet characters, so a
    corrupted string decodes to plausible-but-wrong bytes and we hand Gemini a
    signature that was never issued. Returning None (replay without a signature)
    is the honest failure."""
    # Lenient base64 would drop the '!' characters and happily return b'ABCD\x00B'.
    assert _ofc_genai._thought_signature_from_extra_content(
        {"google": {"thought_signature": "Q!U!J!D!RABC"}}
    ) is None


# ---------------------------------------------------------------------------
# 4. Signature belongs to the call it arrived on, not to a position
# ---------------------------------------------------------------------------


def test_collect_tool_calls_signature_stays_on_non_zero_index():
    """Mirror of the index-0 case: a signature that arrives on index 1 must land
    on index 1. Testing only "index 0 has it, index 1 doesn't" cannot tell a
    correct implementation apart from one that always attaches to the first
    call."""
    from utils.llm_client import ChatOpenAI

    deltas_per_chunk = [[
        {"index": 0, "id": "c0", "type": "function",
         "function": {"name": "first_tool", "arguments": "{}"}},
        {"index": 1, "id": "c1", "type": "function",
         "function": {"name": "recall_memory", "arguments": "{}"},
         "extra_content": _SIGNATURE_EXTRA},
    ]]
    out = ChatOpenAI.collect_tool_calls(deltas_per_chunk)
    assert [c.name for c in out] == ["first_tool", "recall_memory"]
    assert out[0].extra_content is None
    assert out[1].extra_content == _SIGNATURE_EXTRA


@pytest.mark.asyncio
async def test_offline_openai_history_keeps_signature_on_its_own_call():
    """Same invariant one layer up: with two parallel calls where only the
    second carries a signature, the history entry that gets it must be the
    second one."""
    from main_logic.tool_calling import ToolCall, ToolDefinition, ToolResult
    from utils.llm_client import LLMStreamChunk

    tools = [
        ToolDefinition(name="first_tool", description="a",
                       parameters={"type": "object", "properties": {}}),
        ToolDefinition(name="recall_memory", description="b",
                       parameters={"type": "object", "properties": {}}),
    ]
    chunks_call_1 = [
        LLMStreamChunk(content="", tool_call_deltas=[
            {"index": 0, "id": "c0", "type": "function",
             "function": {"name": "first_tool", "arguments": "{}"}},
            {"index": 1, "id": "c1", "type": "function",
             "function": {"name": "recall_memory", "arguments": "{}"},
             "extra_content": _SIGNATURE_EXTRA},
        ]),
        LLMStreamChunk(content="", finish_reason="tool_calls"),
    ]
    chunks_call_2 = [LLMStreamChunk(content="done", finish_reason="stop")]

    client = _bare_offline_client()
    client.llm = _FakeLLM([chunks_call_1, chunks_call_2])
    client._tool_definitions = tools
    client.max_tool_iterations = 4
    client._use_genai_sdk = False

    async def handler(call: ToolCall) -> ToolResult:
        return ToolResult(call_id=call.call_id, name=call.name, output={})

    client.on_tool_call = handler

    messages = [{"role": "user", "content": "x"}]
    async for _ in client._astream_with_tools(messages):
        pass

    calls = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["first_tool", "recall_memory"]
    assert "extra_content" not in calls[0]
    assert calls[1]["extra_content"] == _SIGNATURE_EXTRA


@pytest.mark.asyncio
async def test_offline_genai_signature_stays_on_its_own_part(monkeypatch):
    """genai twin: two function_call parts in one chunk, only the second Part
    carries a thought_signature."""
    from main_logic.tool_calling import ToolCall, ToolResult

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)

    async def _round1():
        yield _Chunk([
            _Part(function_call=_FunctionCall("first_tool", {}, id_="c0")),
            _Part(function_call=_FunctionCall("recall_memory", {}, id_="c1"),
                  thought_signature=_SIGNATURE_BYTES),
        ])

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

    calls = next(
        m for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["first_tool", "recall_memory"]
    assert "extra_content" not in calls[0]
    assert calls[1]["extra_content"] == _SIGNATURE_EXTRA


# ---------------------------------------------------------------------------
# 5. The real SDK boundary — fakes above cannot prove either of these
# ---------------------------------------------------------------------------


def test_extra_content_survives_openai_sdk_into_request_body():
    """The whole change is worthless unless the key survives openai-python's
    request transform into the actual HTTP body. Every other test in this file
    stops at "the key is in the messages list", which a TypedDict-filtering SDK
    would happily strip afterwards."""
    import json

    import httpx
    import openai

    captured = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "ok"}}],
        })

    sdk = openai.OpenAI(
        api_key="sk-test", base_url="https://api.example.com/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    sdk.chat.completions.create(model="m", messages=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "recall_memory", "arguments": "{}"},
            "extra_content": _SIGNATURE_EXTRA,
        }]},
        {"role": "tool", "tool_call_id": "c1", "name": "recall_memory", "content": "{}"},
    ])

    wire_call = captured["body"]["messages"][1]["tool_calls"][0]
    assert wire_call["extra_content"] == _SIGNATURE_EXTRA, (
        "openai-python 把 tool_calls 里的未知字段剥掉了——本改动的整条链就断了"
    )


def test_extra_content_readable_off_real_sdk_delta_object():
    """The streaming tests fake the SDK's delta with SimpleNamespace, which can
    expose an attribute the real model class would have dropped. Pin the read
    against the type openai-python actually constructs from a raw chunk."""
    from openai._models import construct_type
    from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall

    from utils.llm_client.openai_client import _plain_dict

    tool_call = construct_type(
        value={
            "index": 0, "id": "c1", "type": "function",
            "function": {"name": "recall_memory", "arguments": "{}"},
            "extra_content": _SIGNATURE_EXTRA,
        },
        type_=ChoiceDeltaToolCall,
    )
    assert _plain_dict(getattr(tool_call, "extra_content", None)) == _SIGNATURE_EXTRA


def test_plain_dict_handles_model_objects_and_scalars():
    """``_plain_dict``'s non-dict fallbacks are the reason a future SDK that
    materializes unknown fields as models still works. Untested, they are just
    a claim in a comment."""
    from utils.llm_client.openai_client import _plain_dict

    class _Dumpable:
        def model_dump(self):
            return dict(_SIGNATURE_EXTRA)

    class _ToDict:
        def to_dict(self):
            return dict(_SIGNATURE_EXTRA)

    class _Exploding:
        def model_dump(self):
            raise RuntimeError("boom")

    assert _plain_dict(_Dumpable()) == _SIGNATURE_EXTRA
    assert _plain_dict(_ToDict()) == _SIGNATURE_EXTRA
    assert _plain_dict(None) is None
    assert _plain_dict("not-a-dict") is None
    # A dumper that raises must degrade to "no extra_content", never propagate.
    assert _plain_dict(_Exploding()) is None


# ---------------------------------------------------------------------------
# 6. The signature is bound to the route that minted it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_model_strips_signature_when_endpoint_changes(monkeypatch):
    """``switch_model(vision_model, use_vision_config=True)`` re-points the
    session at a separately configured provider and deliberately keeps the
    history. A Google-private blob must not ride along to that endpoint —
    openai-python forwards unknown tool_call keys verbatim, and a strict
    endpoint rejects the request, breaking every remaining turn."""
    import main_logic.omni_offline_client._streaming as _streaming

    client = _bare_offline_client()
    client.model = "gemini-3-pro"
    client.base_url = "https://www.lanlan.app/v1"
    client.api_key = "free-key"
    client.vision_model = "gpt-4o"
    client.vision_base_url = "https://api.openai.com/v1"
    client.vision_api_key = "sk-vision"
    client.max_response_length = 300
    client._genai_client = None
    client._use_genai_sdk = False

    async def _aclose():
        return None

    client.llm = type("F", (), {"aclose": staticmethod(_aclose)})()
    client._conversation_history = [
        {"role": "user", "content": "还记得吗"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "recall_memory", "arguments": "{}"},
            "extra_content": _SIGNATURE_EXTRA,
        }]},
        {"role": "tool", "tool_call_id": "c1", "name": "recall_memory", "content": "{}"},
    ]

    async def _fake_create(*_a, **_kw):
        return type("F", (), {"aclose": staticmethod(_aclose)})()

    monkeypatch.setattr(_streaming, "create_chat_llm_async", _fake_create)

    await client.switch_model("gpt-4o", use_vision_config=True)

    assistant_turn = client._conversation_history[1]
    assert "extra_content" not in assistant_turn["tool_calls"][0], (
        "切到另一个 endpoint 后，Gemini 的 thought_signature 不能跟着历史发过去"
    )
    # The rest of the tool round must survive — dropping the whole turn would
    # orphan the tool result and cost the model its own context.
    assert assistant_turn["tool_calls"][0]["function"]["name"] == "recall_memory"
    assert client._conversation_history[2]["role"] == "tool"


@pytest.mark.asyncio
async def test_switch_model_keeps_signature_on_same_endpoint(monkeypatch):
    """Dual, and the more important half: swapping models WITHIN one endpoint
    (both slots pointed at the same Gemini route) must keep the signature —
    stripping there would hand back the exact 400 this change fixes."""
    import main_logic.omni_offline_client._streaming as _streaming

    async def _aclose():
        return None

    client = _bare_offline_client()
    client.model = "gemini-3-pro"
    client.base_url = "https://www.lanlan.app/v1"
    client.api_key = "free-key"
    client.vision_model = "gemini-3-pro-vision"
    client.vision_base_url = "https://www.lanlan.app/v1"
    client.vision_api_key = "free-key"
    client.max_response_length = 300
    client._genai_client = None
    client._use_genai_sdk = False
    client.llm = type("F", (), {"aclose": staticmethod(_aclose)})()
    client._conversation_history = [
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "recall_memory", "arguments": "{}"},
            "extra_content": _SIGNATURE_EXTRA,
        }]},
    ]

    async def _fake_create(*_a, **_kw):
        return type("F", (), {"aclose": staticmethod(_aclose)})()

    monkeypatch.setattr(_streaming, "create_chat_llm_async", _fake_create)

    await client.switch_model("gemini-3-pro-vision", use_vision_config=True)

    assert client._conversation_history[0]["tool_calls"][0]["extra_content"] == _SIGNATURE_EXTRA


@pytest.mark.asyncio
async def test_switch_model_treats_empty_and_none_credentials_as_same_route(monkeypatch):
    """Empty string and None are the same "no explicit endpoint / key", and the
    two branches that pick them normalize differently. Comparing the raw values
    would call one endpoint two routes and strip a signature that is still
    valid — the misfire lands exactly on the case this change exists to fix."""
    import main_logic.omni_offline_client._streaming as _streaming

    async def _aclose():
        return None

    client = _bare_offline_client()
    client.model = "gemini-3-pro"
    client.base_url = ""        # 空串
    client.api_key = ""
    client.vision_model = "gemini-3-pro-vision"
    client.vision_base_url = None   # 同一个"默认端点"，另一种写法
    client.vision_api_key = None
    client.max_response_length = 300
    client._genai_client = None
    client._use_genai_sdk = False
    client.llm = type("F", (), {"aclose": staticmethod(_aclose)})()
    client._conversation_history = [
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "recall_memory", "arguments": "{}"},
            "extra_content": _SIGNATURE_EXTRA,
        }]},
    ]

    async def _fake_create(*_a, **_kw):
        return type("F", (), {"aclose": staticmethod(_aclose)})()

    monkeypatch.setattr(_streaming, "create_chat_llm_async", _fake_create)

    await client.switch_model("gemini-3-pro-vision", use_vision_config=True)

    assert client._conversation_history[0]["tool_calls"][0]["extra_content"] == _SIGNATURE_EXTRA
