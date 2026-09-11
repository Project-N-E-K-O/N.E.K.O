"""A tool's pictures on the frames bus: staged on injection, published on delivery.

The bus contract is "frames the provider actually received". A tool image is
written into the outgoing message list one iteration and only reaches the
provider on the NEXT request, so the two events are separated here too:
``_append_tool_result_images`` stages, and the tool loop publishes once that
next request has demonstrably produced output.

Both halves need their own pinning. Staging without a gate would put pictures
on the bus that a loop running out of iterations never sent; a gate without
staging would publish the caller's originals rather than the bytes the turn
budget actually admitted.
"""

from __future__ import annotations

import asyncio

import pytest

from main_logic.omni_offline_client._media import _MediaMixin
from main_logic.omni_offline_client._tools import _ToolingMixin
from main_logic.tool_calling import ToolCall, ToolImage, ToolResult
from utils.llm_client import LLMStreamChunk

pytestmark = pytest.mark.unit


_PUBLISHER = (
    "main_logic.omni_offline_client._media."
    "publish_provider_frame_observed_best_effort"
)

# 1x1 PNG and JPEG. Distinct mimes so a test can prove the label is read off
# the image rather than defaulted.
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0a"
    "HBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAA"
    "AAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AN//Z"
)


class _Call:
    """Shape of an aggregated tool call as ``collect_tool_calls`` yields it."""

    def __init__(self, name="demo_tool", id="call_1", arguments="{}"):
        self.name = name
        self.id = id
        self.arguments = arguments
        self.extra_content = None


class _FakeLLM:
    """Streams one scripted response per iteration and records the requests."""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.requests: list[list] = []

    async def astream(self, messages, **_overrides):
        # Snapshot: the loop mutates the same list between iterations.
        self.requests.append([dict(m) if isinstance(m, dict) else m for m in messages])
        if not self._scripts:
            return
        script = self._scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        for chunk in script:
            yield chunk


class _Client(_ToolingMixin, _MediaMixin):
    """Enough of OmniOfflineClient to run the OpenAI-compat tool loop."""

    lanlan_name = "test-character"
    # Pin the OpenAI-compat branch of the dispatcher; the genai dual has its
    # own loop and is covered separately.
    _use_genai_sdk = False
    _genai_tools_unsupported = False
    base_url = "https://test.invalid/v1"

    def __init__(self, scripts, result, max_tool_iterations=4):
        self.llm = _FakeLLM(scripts)
        self.max_tool_iterations = max_tool_iterations
        # A list lets one test give the SECOND tool round a different (image
        # free) result, which is what a cross-round republish needs to show.
        self._results = list(result) if isinstance(result, list) else [result]
        self._user_language_provider = lambda: "en"
        self._tool_definitions = []
        self.on_tool_call = self._handle
        self.on_tool_round_start = None
        self.model = "test-model"
        self._last_prompt_tokens = None

    async def _handle(self, _call: ToolCall) -> ToolResult:
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]

    def _openai_tools_payload(self):
        # Non-empty so the loop keeps ``tools`` wired; content is never read
        # because the LLM is scripted.
        return [{"type": "function", "function": {"name": "demo_tool"}}]

    async def _notify_tool_round_start(self):
        return None

    async def _notify_reasoning_active(self):
        return None


def _tool_call_chunk():
    return LLMStreamChunk(
        content="",
        tool_call_deltas=[{
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "demo_tool", "arguments": "{}"},
        }],
        finish_reason="tool_calls",
    )


def _image_result(images, name="demo_tool") -> ToolResult:
    return ToolResult(
        call_id="call_1",
        name=name,
        output={"ok": True},
        images=list(images),
    )


# Bound at import so a module-wide sleep patch cannot turn the settle into a
# no-op: a drain that never yields reads zero and every assertion here would
# pass for the wrong reason.
_REAL_SLEEP = asyncio.sleep


async def _settle():
    """Let the fire-and-forget bus copies run.

    The publish is off the response path -- a cross-loop hop with no timeout
    must not hold up the reply -- so a turn returns before any frame reaches
    the spy. Without this, every assertion below is about scheduling luck.
    """
    for _ in range(50):
        await _REAL_SLEEP(0)


async def _drain(client):
    chunks = [chunk async for chunk in client._astream_visible_with_tools([])]
    await _settle()
    return chunks


# --------------------------------------------------------------- staging half


@pytest.mark.asyncio
async def test_an_injected_image_is_staged_with_its_mime_and_tool():
    """Staging carries what the publish needs and cannot re-derive later."""

    client = _Client([], _image_result([ToolImage(PNG_B64, "image/png", "look")]))
    staged: list = []
    messages: list = []

    await client._execute_and_append_openai_tool_calls(
        messages, [_Call()], tool_bus_frames=staged,
    )

    assert staged == [(PNG_B64, "image/png", "demo_tool")]


@pytest.mark.asyncio
async def test_staging_alone_publishes_nothing(monkeypatch):
    """Injection is not delivery. Nothing may reach the bus at this point."""

    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client([], _image_result([ToolImage(PNG_B64, "image/png", "look")]))
    await client._execute_and_append_openai_tool_calls(
        [], [_Call()], tool_bus_frames=[],
    )

    assert published == []


@pytest.mark.asyncio
async def test_an_image_the_turn_budget_dropped_is_never_staged():
    """The bus may only carry what went into the request.

    ``_TOOL_IMAGE_TURN_MAX_COUNT`` is 2, so the third image is omitted with a
    model-visible warning -- and must not be staged, or a plugin would read a
    picture the provider never got.
    """

    three = [ToolImage(PNG_B64, "image/png", f"look {i}") for i in range(3)]
    client = _Client([], _image_result(three))
    staged: list = []

    await client._execute_and_append_openai_tool_calls(
        [], [_Call()], tool_bus_frames=staged,
    )

    assert len(staged) == 2, "the dropped image was staged anyway"


# ------------------------------------------------------------------ gate half


@pytest.mark.asyncio
async def test_a_tool_image_reaches_the_bus_once_the_next_request_answers(
    monkeypatch,
):
    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append({"lanlan_name": lanlan_name, **kwargs})
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [[_tool_call_chunk()], [LLMStreamChunk(content="看到了")]],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
    )
    await _drain(client)

    assert len(published) == 1, "the delivered tool image never reached the bus"
    frame = published[0]
    assert frame["image_base64"] == JPEG_B64
    # 来源标签：插件提供的媒体，不是用户共享的画面。
    assert frame["source"] == "plugin"
    assert frame["mime"] == "image/jpeg"
    assert frame["metadata"] == {"tool_name": "demo_tool"}
    assert frame["lanlan_name"] == "test-character"


@pytest.mark.asyncio
async def test_the_mime_is_read_off_the_image_not_defaulted(monkeypatch):
    """A PNG must not be announced as the publisher's JPEG default."""

    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [[_tool_call_chunk()], [LLMStreamChunk(content="看到了")]],
        _image_result([ToolImage(PNG_B64, "image/png", "look")]),
    )
    await _drain(client)

    assert [f["mime"] for f in published] == ["image/png"]


@pytest.mark.asyncio
async def test_a_request_the_provider_rejected_publishes_nothing(monkeypatch):
    """The gate itself: a request that raised delivered nothing.

    ``max_tool_iterations=1`` runs the tools and drops out to forced-finalize,
    and that finalize call raises -- so the pixels sat in the message list but
    no provider ever took them. Publishing here would be the host asserting a
    delivery that never happened, the one thing this bus must never do.
    """

    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [[_tool_call_chunk()], RuntimeError("provider refused")],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
        max_tool_iterations=1,
    )
    with pytest.raises(RuntimeError):
        await _drain(client)
    # 异常路径不经过 _drain 里的 settle，这里补上——否则"没发布"可能只是
    # 后台任务还没轮到，而不是它真的没被创建。
    await _settle()

    assert published == [], "published a frame no provider ever took"


@pytest.mark.asyncio
async def test_the_forced_finalize_request_also_publishes(monkeypatch):
    """Capping the tool rounds does not un-deliver the pictures.

    The image turns are only swapped back out in the outer ``finally``, so the
    tools-stripped finalize call still carries them. Skipping it would make
    "the model saw it but no plugin can read it" land on exactly the turns that
    used up their tool budget.
    """

    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [[_tool_call_chunk()], [LLMStreamChunk(content="看到了")]],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
        max_tool_iterations=1,
    )
    await _drain(client)

    assert len(client.llm.requests) == 2, "前提没成立：封顶后没跑 forced-finalize"
    assert any(
        isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
        for m in client.llm.requests[1]
    ), "前提没成立：封顶请求没带图"
    assert len(published) == 1
    assert published[0]["source"] == "plugin"


@pytest.mark.asyncio
async def test_a_silent_follow_up_publishes_nothing(monkeypatch):
    """No chunk, no claim.

    An empty stream is indistinguishable here from a request that never
    reached the provider, so the copy stays unmade. Under-publishing costs a
    plugin a picture; over-publishing is a lie about delivery.
    """

    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [[_tool_call_chunk()], []],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
        max_tool_iterations=1,
    )
    await _drain(client)

    assert published == []


@pytest.mark.asyncio
async def test_the_follow_up_request_really_did_carry_the_image(monkeypatch):
    """Premise check for the test above it.

    If the loop stopped putting images into the second request, the gate test
    would pass for the wrong reason and this file would still be green.
    """

    monkeypatch.setattr(_PUBLISHER, lambda *a, **k: None, raising=True)

    client = _Client(
        [[_tool_call_chunk()], [LLMStreamChunk(content="看到了")]],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
    )
    await _drain(client)

    second = client.llm.requests[1]
    assert any(
        isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
        for m in second
    ), "前提没成立：第二次请求根本没带图"


@pytest.mark.asyncio
async def test_a_frame_is_published_once_not_once_per_chunk(monkeypatch):
    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [
            [_tool_call_chunk()],
            [LLMStreamChunk(content="看"), LLMStreamChunk(content="到了")],
        ],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
    )
    await _drain(client)

    assert len(published) == 1, f"published {len(published)} times"


@pytest.mark.asyncio
async def test_a_later_round_does_not_republish_an_earlier_rounds_frame(
    monkeypatch,
):
    """The drain must empty the staging list, not just read it.

    A per-iteration "already published" flag stops a SECOND chunk from
    republishing, so that alone proves nothing. Two tool rounds do: without
    the clear, round three copies round one's picture onto the bus a second
    time, and a plugin deduping by ``id`` sees two records for one delivery.
    """

    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    client = _Client(
        [
            [_tool_call_chunk()],
            [_tool_call_chunk()],
            [LLMStreamChunk(content="看到了")],
        ],
        [
            _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
            # The second round returns no picture: anything published after it
            # can only be the first round's, copied twice.
            ToolResult(call_id="call_1", name="demo_tool", output={"ok": True}),
        ],
    )
    await _drain(client)

    assert len(client.llm.requests) == 3, (
        "前提没成立：循环没跑到第三轮，跨轮重发根本无从发生"
    )
    assert len(published) == 1, f"published {len(published)} times"


@pytest.mark.asyncio
async def test_a_failing_bus_never_costs_the_user_the_reply(monkeypatch):
    """Copying a frame is a courtesy; the turn outranks it."""

    async def _boom(lanlan_name, **_kwargs):
        raise RuntimeError("bus is down")

    monkeypatch.setattr(_PUBLISHER, _boom, raising=True)

    client = _Client(
        [[_tool_call_chunk()], [LLMStreamChunk(content="看到了")]],
        _image_result([ToolImage(JPEG_B64, "image/jpeg", "look")]),
    )
    chunks = await _drain(client)

    assert any(getattr(c, "content", "") for c in chunks), "回复被总线故障吃掉了"


# ------------------------------------------------------- the genai dual loop
#
# The native-Gemini tool loop is a separate implementation with its own
# streaming shape, so its gate has to be pinned separately. Symmetry here is
# not tidiness: independent ASR arms the raw-visual fence, and on that route
# this loop is the only channel a tool picture can reach the model through.


class _Part:
    def __init__(self, *, text=None, function_call=None):
        self.text = text
        self.function_call = function_call
        self.thought_signature = None


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


def _genai_client_for(rounds, calls):
    class _FakeClient:
        class aio:
            class models:
                @staticmethod
                async def generate_content_stream(**_kw):
                    calls.append(1)
                    index = min(len(calls) - 1, len(rounds) - 1)
                    return _StreamWrapper(rounds[index]())

        def close(self):
            pass

    return _FakeClient()


def _genai_client(rounds, result, calls, max_tool_iterations=3):
    from main_logic.omni_offline_client import OmniOfflineClient

    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.vision_provider_type = None
    client._genai_tools_unsupported = False
    client.lanlan_name = "test-character"
    client._user_language_provider = lambda: "en"
    client.model = "gemini-3-pro"
    client.api_key = "fake"
    client._tool_definitions = []
    client.has_tools = lambda: False
    client.max_tool_iterations = max_tool_iterations
    client._genai_client = _genai_client_for(rounds, calls)
    client.llm = type("F", (), {"max_completion_tokens": 100})()

    async def handler(_call):
        return result

    client.on_tool_call = handler
    return client


async def _genai_round_tool():
    yield _Chunk([_Part(function_call=_FunctionCall("demo_tool", {}, id_="c1"))])


async def _genai_round_text():
    yield _Chunk([_Part(text="看到了")])


async def _genai_round_empty():
    """A request that came back with nothing at all."""
    return
    yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_genai_publishes_a_tool_image_once_the_next_request_answers(
    monkeypatch,
):
    from main_logic.omni_offline_client import _genai_support as _ofc_genai

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    calls: list = []
    client = _genai_client(
        [_genai_round_tool, _genai_round_text],
        _image_result([ToolImage(PNG_B64, "image/png", "look")]),
        calls,
    )
    bus_frames: list = []
    messages = [{"role": "user", "content": "看看"}]
    async for _ in client._astream_genai_with_tools(
        messages, _tool_bus_frames=bus_frames,
    ):
        pass
    await _settle()

    assert len(calls) >= 2, "前提没成立：genai 循环没跑到第二次请求"
    assert len(published) == 1, "genai 路径的工具图没上总线"
    assert published[0]["source"] == "plugin"
    assert published[0]["mime"] == "image/png"
    assert published[0]["metadata"] == {"tool_name": "demo_tool"}


@pytest.mark.asyncio
async def test_genai_publishes_nothing_when_no_follow_up_request_runs(
    monkeypatch,
):
    from main_logic.omni_offline_client import _genai_support as _ofc_genai

    monkeypatch.setattr(_ofc_genai, "_GENAI_AVAILABLE", True)
    published: list = []

    async def _publish(lanlan_name, **kwargs):
        published.append(kwargs)
        return True

    monkeypatch.setattr(_PUBLISHER, _publish, raising=True)

    calls: list = []
    client = _genai_client(
        [_genai_round_tool, _genai_round_empty],
        _image_result([ToolImage(PNG_B64, "image/png", "look")]),
        calls,
        max_tool_iterations=1,
    )
    async for _ in client._astream_genai_with_tools(
        [{"role": "user", "content": "看看"}], _tool_bus_frames=[],
    ):
        pass
    await _settle()

    # 封顶后仍会跑 forced-finalize，所以"没有后继请求"在这条路上不存在；
    # 真正的非投递是那次请求一个 chunk 都没吐出来。
    assert len(calls) == 2, "前提没成立：forced-finalize 没跑"
    assert published == [], "published a frame nothing came back from"
