"""Offline turn images: unconditional compression, then a copy onto the bus.

Two properties are pinned here, and they are the same property seen twice.

1. The bytes the provider receives are always re-encoded to the model
   resolution profile, even on an ordinary one-attachment turn that was never
   anywhere near the 8 MiB turn budget. The budget ladder used to be a pure
   ceiling, so exactly those turns went out untouched at whatever resolution
   the frontend or a plugin happened to produce.

2. What the host copies onto the plugin frame bus is THOSE bytes -- the
   compressed, attached ones -- and never the caller's originals. A plugin
   reading ``bus.frames`` must see the picture the model saw, not a bigger,
   different one.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from main_logic.omni_offline_client._client import OmniOfflineClient
from main_logic.proactive_delivery import (
    TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES,
    approx_base64_decoded_bytes,
)
from utils.llm_client import SystemMessage
from utils.screenshot_utils import COMPRESS_TARGET_HEIGHT, MODEL_IMAGE_MAX_WIDTH

pytestmark = pytest.mark.unit


_PUBLISHER = (
    "main_logic.omni_offline_client._media."
    "publish_provider_frame_observed_best_effort"
)


def _png_b64(
    width: int,
    height: int,
    colour: tuple[int, int, int] = (18, 160, 90),
) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _decode(b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _make_client() -> tuple[OmniOfflineClient, list]:
    """A client wired just far enough to build one turn and call the stream.

    ``_astream_visible_with_tools`` captures the messages and raises, which is
    the generic-except -> break path: no retry, no sleep, and the user message
    has already been appended to history by then.
    """
    client = OmniOfflineClient.__new__(OmniOfflineClient)
    client._conversation_history = [SystemMessage(content="sys")]
    client._pending_images = []
    client._pending_plugin_images = []
    client._proactive_image_to_inject = None
    client._proactive_image_staged_at = 0.0
    client._proactive_image_history_len = 0
    client.lanlan_name = "neko"
    client.llm = SimpleNamespace(max_completion_tokens=2000)
    client.model = "m"
    client.vision_model = None
    client.max_response_rerolls = 0
    client.max_response_length = 2000
    client._prefix_buffer_size = 0
    for hook in (
        "on_input_transcript", "on_status_message", "on_response_discarded",
        "on_response_done", "on_text_delta", "on_thinking_active",
        "on_output_transcript", "on_repetition_detected",
    ):
        setattr(client, hook, None)
    client._begin_reasoning_stream = MagicMock()
    client.switch_model = AsyncMock()
    client._is_responding = False
    client._skip_next_response = False

    captured: list = []

    async def _fake_astream(messages, **_overrides):
        captured.append(list(messages))
        raise RuntimeError("stop-after-construction")
        yield  # pragma: no cover - marks this an async generator

    client._astream_visible_with_tools = _fake_astream
    return client, captured


def _attached_b64(captured: list) -> list[str]:
    assert captured, "the stream was never reached -- no turn was built"
    message = captured[0][-1]
    assert isinstance(message.content, list), "turn did not go multi-modal"
    return [
        item["image_url"]["url"].split(",", 1)[1]
        for item in message.content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]


def _has_image_parts(captured: list) -> bool:
    message = captured[0][-1]
    if not isinstance(message.content, list):
        return False
    return any(
        isinstance(item, dict) and item.get("type") == "image_url"
        for item in message.content
    )


class _Spy:
    """Records every frame handed to the bus publisher."""

    def __init__(self, result: bool = True) -> None:
        self.calls: list[dict] = []
        self._result = result

    async def __call__(self, lanlan_name, **kwargs):
        self.calls.append({"lanlan_name": lanlan_name, **kwargs})
        return self._result

    @property
    def images(self) -> list[str]:
        return [call["image_base64"] for call in self.calls]

    @property
    def sources(self) -> list[str]:
        return [call["source"] for call in self.calls]


# ---------------------------------------------------------------------------
# 1. The compression that an ordinary turn used to miss entirely.
# ---------------------------------------------------------------------------


def test_a_small_single_attachment_turn_is_still_re_encoded_to_the_profile():
    """One screenshot, far under budget: the case the old ceiling never touched.

    The fixture is deliberately tiny in BYTES and oversized in PIXELS, which is
    exactly the shape that used to slip through: the ladder only ran when the
    turn exceeded TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES, so a 1600x1200 frame at
    a few KiB reached the model at full resolution. The assertion is therefore
    two-part -- the turn was never over budget, AND the bytes still changed.

    Mutation: in ``fit_images_to_turn_budget``, put the normalize step back
    behind the ``total <= max_total_bytes`` early return (or simply set
    ``normalized = kept``). This goes red.
    """
    source = _png_b64(1600, 1200)
    assert approx_base64_decoded_bytes(source) < TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES, (
        "fixture must be UNDER budget or this tests the ladder, not rung 0"
    )

    client, captured = _make_client()
    client._pending_images = [source]
    with patch(_PUBLISHER, _Spy()):
        asyncio.run(client.stream_text("这是什么"))

    sent = _attached_b64(captured)
    assert len(sent) == 1
    assert sent[0] != source, "the attachment reached the provider un-recompressed"
    image = _decode(sent[0])
    assert image.format == "JPEG"
    assert image.size[0] <= MODEL_IMAGE_MAX_WIDTH
    assert image.size[1] <= COMPRESS_TARGET_HEIGHT


def test_routine_recompression_never_toasts_the_user():
    """Downscaling to the profile is silent. Deliberate: no picture was lost.

    Rung 0 fires on nearly every turn that carries an image, so a notice keyed
    on "the ladder ran" would put a banner on screen every single turn for
    something the reader cannot see.

    Mutation: gate the toast on ``if _budget_notice:`` instead of
    ``_budget_notice.get("user_visible")`` in ``_streaming.py``.
    """
    client, captured = _make_client()
    client.on_status_message = AsyncMock()
    client._pending_images = [_png_b64(1600, 1200)]
    with patch(_PUBLISHER, _Spy()):
        asyncio.run(client.stream_text("看看这个"))

    assert _attached_b64(captured), "no turn was built"
    # 这条流以 RuntimeError 收场，finally 会补一条 LLM_NO_RESPONSE —— 断言必须
    # 只盯裁剪提示，否则它测的是"这一轮完全没发过状态消息"，与本条主张无关。
    codes = [
        json.loads(call.args[0]).get("code")
        for call in client.on_status_message.await_args_list
    ]
    assert "TURN_IMAGES_TRIMMED" not in codes, codes


# ---------------------------------------------------------------------------
# 2. The bus copy.
# ---------------------------------------------------------------------------


def test_the_bus_gets_the_compressed_bytes_not_the_callers_original():
    """What the provider got, byte for byte -- the whole contract in one test.

    Mutation: publish ``_ordered_images`` instead of ``_attached_images``.
    """
    source = _png_b64(1600, 1200)
    client, captured = _make_client()
    client._pending_images = [source]
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        asyncio.run(client.stream_text("这是什么"))

    sent = _attached_b64(captured)
    assert spy.images == sent
    assert source not in spy.images
    assert spy.calls[0]["lanlan_name"] == "neko"


def test_each_frame_is_labelled_with_the_queue_it_came_from():
    """Order and labels follow the attachment order the turn was built in.

    A plugin filtering on ``source`` is the reason this is not one flat label:
    ``"screen"`` has to mean the screen, so a dropped photo and a plugin's own
    push must not wear it.

    Mutation: collapse ``_ordered_sources`` to one repeated label, or swap the
    proactive/plugin/user order relative to ``_ordered_images``.
    """
    client, captured = _make_client()
    client._pending_images = [_png_b64(300, 200, (200, 30, 30))]
    client._pending_plugin_images = [_png_b64(300, 200, (30, 30, 200))]
    client.set_proactive_screenshot(_png_b64(300, 200, (30, 200, 30)))

    spy = _Spy()
    with patch(_PUBLISHER, spy):
        asyncio.run(client.stream_text("这几张图"))

    assert spy.sources == ["screen", "plugin", "user"]
    # 标签是按位对齐的，不是"恰好三条"：颜色能把每一条钉回它自己的来源。
    assert spy.images == _attached_b64(captured)
    assert _decode(spy.images[0]).getpixel((0, 0))[1] > 150   # proactive: green
    assert _decode(spy.images[1]).getpixel((0, 0))[2] > 150   # plugin: blue
    assert _decode(spy.images[2]).getpixel((0, 0))[0] > 150   # user: red


def test_callback_media_is_labelled_plugin_not_user():
    """Passive-callback media rides ``system_prefix_images`` and is not his.

    Mutation: label ``prefix_images`` with the user constant.
    """
    client, _captured = _make_client()
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        asyncio.run(client.stream_text(
            "在说什么",
            system_prefix="======[系统通知] x======",
            system_prefix_images=[_png_b64(300, 200)],
        ))

    assert spy.sources == ["plugin"]


def test_labels_degrade_to_unknown_once_the_ladder_changes_the_count():
    """Positional attribution is only sound while the turn keeps its shape.

    Sampling and trimming both drop frames, and the surviving strings carry no
    trace of where they sat in the original list. Guessing there would hand a
    plugin's own push to a plugin filtering for ``"screen"``.

    Mutation: return ``_ordered_sources`` unconditionally.
    """
    client, captured = _make_client()
    client.set_proactive_screenshot(_png_b64(300, 200))
    client._pending_images = [_png_b64(300, 200)]

    async def _fit_that_drops_one(images, _budget):
        return list(images)[1:], None

    spy = _Spy()
    with patch(
        "main_logic.omni_offline_client._streaming.fit_images_to_turn_budget",
        _fit_that_drops_one,
    ), patch(_PUBLISHER, spy):
        asyncio.run(client.stream_text("这几张图"))

    assert len(_attached_b64(captured)) == 1
    assert spy.sources == ["unknown"]


def test_an_independent_asr_turn_carries_its_turn_id_onto_the_bus():
    """The frames sampled from one utterance have to be recognisable as one set.

    ``submit_multimodal_turn`` is the description-mode frame channel, and its
    turn id is the only thing tying its head/middle/tail frames together once
    they are three separate records on the bus.

    Mutation: stop threading ``turn_id`` through ``_run_external_voice_stream``.
    """
    client, captured = _make_client()
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        accepted = asyncio.run(client.submit_multimodal_turn(
            "刚才说的是这个",
            [_png_b64(300, 200), _png_b64(320, 200)],
            turn_id="utterance-77",
        ))

    assert accepted is True
    assert len(_attached_b64(captured)) == 2
    assert [call["turn_id"] for call in spy.calls] == ["utterance-77"] * 2
    assert spy.sources == ["user", "user"]


def test_an_ordinary_text_turn_publishes_no_turn_id():
    """Ordinary turns have no utterance identity, and inventing one would group
    unrelated frames.

    Mutation: default ``turn_id`` to a generated value instead of None.
    """
    client, _captured = _make_client()
    client._pending_images = [_png_b64(300, 200)]
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        asyncio.run(client.stream_text("这是什么"))

    assert [call["turn_id"] for call in spy.calls] == [None]


def test_a_dropped_proactive_screenshot_never_reaches_the_bus():
    """A frame the turn threw away was never sent, so it may not be copied.

    The staged proactive screenshot is dropped when it has been superseded by a
    later AI turn -- the reply is no longer answering that screen. The provider
    never sees it, and the bus contract is "what the provider got", so neither
    may a plugin. Publishing from ``proactive_image`` before the TTL/supersede
    test would hand plugins a screenshot the model was deliberately not shown.

    Mutation: publish the staged screenshot from the pre-check
    ``proactive_image`` local, or drop the ``if has_images`` gate around the
    publish call.
    """
    client, captured = _make_client()
    client.set_proactive_screenshot(_png_b64(300, 200))
    # 一次后来的 AI 回合：staging 时记下的 history 长度不再成立 => superseded。
    client._conversation_history.append(SystemMessage(content="a later turn"))

    spy = _Spy()
    with patch(_PUBLISHER, spy):
        asyncio.run(client.stream_text("纯文本"))

    assert captured, "the stream was never reached"
    assert not _has_image_parts(captured), "the screenshot should have been dropped"
    assert spy.calls == []


def test_a_turn_that_dies_between_fitting_and_committing_publishes_nothing():
    """The window between the fit and the history append is not "sent" yet.

    That window is real: a user-visible trim notice is awaited in it, and a
    teardown landing there runs ``_restore_consumed_queues()`` and re-raises,
    so the frames go back into the queues and no request is ever made. Copying
    them onto the bus from anywhere inside that window announces a delivery
    that did not happen -- and the same frames get published again next turn.

    Mutation: move the publish call up to just after ``_attached_sources`` is
    computed.
    """
    client, _captured = _make_client()
    client._pending_images = ["user-frame"]
    client._pending_plugin_images = ["plugin-frame"]

    async def _fit_with_a_visible_notice(images, _budget):
        kept = list(images)
        return kept, {
            "original_count": len(kept) + 1,
            "final_count": len(kept),
            "normalized": False,
            "sampled": False,
            "compressed": False,
            "dropped": 1,
            "user_visible": True,
        }

    async def _cancelled_notice(_payload):
        raise asyncio.CancelledError()

    client.on_status_message = _cancelled_notice

    spy = _Spy()
    with patch(
        "main_logic.omni_offline_client._streaming.fit_images_to_turn_budget",
        _fit_with_a_visible_notice,
    ), patch(_PUBLISHER, spy):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(client.stream_text("看看这个"))

    assert spy.calls == []
    # 回滚仍然完整，两条队列都原样还了回来。
    assert client._pending_images == ["user-frame"]
    assert client._pending_plugin_images == ["plugin-frame"]
    assert len(client._conversation_history) == 1


def test_a_failing_bus_never_costs_the_user_the_turn():
    """The copy is a courtesy. A bus that is down must not break the reply.

    Mutation: drop the ``except Exception`` guard in
    ``_publish_provider_frames``.
    """
    client, captured = _make_client()
    client._pending_images = [_png_b64(300, 200), _png_b64(320, 200)]

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("plane down")

    with patch(_PUBLISHER, _explode):
        asyncio.run(client.stream_text("这是什么"))

    # 回合照常构建、照常提交。
    assert len(_attached_b64(captured)) == 2
    assert captured[0][-1] is client._conversation_history[-1]


def test_bus_cancellation_is_not_swallowed():
    """A cancelled publish is the session being torn down, not a bus hiccup.

    Swallowing it would let a turn keep streaming through a teardown.

    Mutation: catch ``BaseException`` (or drop the CancelledError re-raise) in
    ``_publish_provider_frames``.
    """
    client, _captured = _make_client()
    client._pending_images = [_png_b64(300, 200)]

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    with patch(_PUBLISHER, _cancel):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(client.stream_text("这是什么"))
