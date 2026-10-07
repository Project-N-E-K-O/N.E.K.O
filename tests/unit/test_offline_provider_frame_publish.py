"""Offline turn images: unconditional compression, then a copy onto the bus.

Three properties are pinned here, and the first two are the same property seen
twice.

1. The bytes the provider receives are always re-encoded to the model
   resolution profile, even on an ordinary one-attachment turn that was never
   anywhere near the 8 MiB turn budget. The budget ladder used to be a pure
   ceiling, so exactly those turns went out untouched at whatever resolution
   the frontend or a plugin happened to produce.

2. What the host copies onto the plugin frame bus is THOSE bytes -- the
   compressed, attached ones -- and never the caller's originals. A plugin
   reading ``bus.frames`` must see the picture the model saw, not a bigger,
   different one.

3. The copy happens only once the provider has demonstrably received the turn,
   and exactly once per turn. Appending the message to ``_conversation_history``
   is not that moment: a raising input-transcript callback, a cancellation, or
   three failed provider attempts all leave a committed turn that no provider
   ever saw. Plugins pull frames; a publish is the host asserting delivery, and
   it may only assert what happened.
"""

from __future__ import annotations

import asyncio
import time
import base64
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APIConnectionError
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

# ``_streaming`` imports the asyncio MODULE, so this patches stdlib
# ``asyncio.sleep`` for the duration -- keep every such patch window down to
# the single ``asyncio.run`` it wraps.
_SLEEP = "main_logic.omni_offline_client._streaming.asyncio.sleep"


async def _no_backoff(_delay: float) -> None:
    """The retry ladder without its wall clock (1s + 2s between attempts)."""


def _connection_error() -> BaseException:
    """A transient provider failure: the retry path, not the give-up path.

    Deliberately not an auth/quota flavour -- those break out of the attempt
    loop on the first try and would not exercise all three attempts.
    """
    return APIConnectionError(
        request=httpx.Request("POST", "http://provider.invalid/v1/chat"),
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


def _make_client(chunks=None, error=None) -> tuple[OmniOfflineClient, list]:
    """A client wired just far enough to build one turn and call the stream.

    ``_astream_visible_with_tools`` captures the messages, streams ``chunks``,
    then raises. The default raise is a plain ``RuntimeError``: the
    generic-except -> break path, so one attempt, no backoff, and the user
    message is already in history by then. Pass ``error=_connection_error`` for
    the retry path instead (three attempts; patch ``_SLEEP`` too).

    ``chunks`` defaults to ONE content-less chunk, and that default is
    load-bearing rather than scenery: the bus copy is gated on a chunk actually
    arriving from the provider, so a fixture that raised before yielding would
    make every publish assertion in this file vacuously true. Pass
    ``chunks=[]`` to model a request the provider never accepted.
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
        for chunk in (
            [SimpleNamespace(content="")] if chunks is None else list(chunks)
        ):
            yield chunk
        raise (
            error() if error is not None
            else RuntimeError("stop-after-the-first-chunk")
        )

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



# Bound at import, before any test can patch the module attribute out from
# under us. ``_settled`` needs a yield that really yields.
_REAL_SLEEP = asyncio.sleep


async def _drain_background_tasks(timeout: float = 2.0) -> None:
    """Wait for the fired bus copies, then make sure none outlive this loop.

    Waiting on the TASKS rather than counting event-loop turns: a fixed number
    of ``sleep(0)`` is not a completion condition, so under load it reads a
    half-finished copy -- or leaves one pending for the next test, which then
    spends its patch on a frame from a turn that ended long ago.

    A deliberately stalled copy never finishes, so the wait is bounded and
    whatever is left is cancelled and collected HERE rather than abandoned at
    loop close. ``asyncio.wait`` runs on the loop clock, so the module-wide
    ``asyncio.sleep`` patch in the retry-ladder tests cannot defeat it.
    """
    deadline = time.monotonic() + timeout
    while True:
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
        ]
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            return
        await asyncio.wait(pending, timeout=remaining)


async def _settled(coro):
    """Run a turn, then let its fire-and-forget bus copies finish.

    The publish no longer sits on the response path -- a cross-loop hop with no
    timeout must never hold up the user's reply -- so a turn can return before
    a single frame has reached the spy. Draining here is what keeps these
    assertions about the publish rather than about scheduling luck.
    """
    try:
        return await coro
    finally:
        # In a ``finally`` on purpose. A turn that raises still fired its bus
        # copies, and skipping the drain there leaves a task pending at loop
        # close -- which then surfaces inside the NEXT test.
        await _drain_background_tasks()


def _run_turn(coro):
    """``asyncio.run`` for a turn, with the bus copies drained before teardown."""
    return asyncio.run(_settled(coro))


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
        _run_turn(client.stream_text("这是什么"))

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
        _run_turn(client.stream_text("看看这个"))

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
        _run_turn(client.stream_text("这是什么"))

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
        _run_turn(client.stream_text("这几张图"))

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
        _run_turn(client.stream_text(
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
        _run_turn(client.stream_text("这几张图"))

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
        accepted = _run_turn(client.submit_multimodal_turn(
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
        _run_turn(client.stream_text("这是什么"))

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
        _run_turn(client.stream_text("纯文本"))

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
            _run_turn(client.stream_text("看看这个"))

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
        _run_turn(client.stream_text("这是什么"))

    # 回合照常构建、照常提交。
    assert len(_attached_b64(captured)) == 2
    assert captured[0][-1] is client._conversation_history[-1]


def test_a_cancelled_bus_publish_no_longer_reaches_the_turn():
    """The copy runs off the response path, so its cancellation stays there.

    This used to propagate, because the turn awaited the publish. It must not
    any more: the whole point of moving the hop off the reply is that nothing
    the bus does -- stalling, failing, being torn down -- can cost the user a
    sentence.
    """
    client, captured = _make_client()
    client._pending_images = [_png_b64(300, 200)]

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    with patch(_PUBLISHER, _cancel):
        _run_turn(client.stream_text("这是什么"))

    assert len(_attached_b64(captured)) == 1
    assert captured[0][-1] is client._conversation_history[-1]


def test_the_publish_helper_still_re_raises_cancellation():
    """The dual of the test above, one layer down.

    Moving the publish into a task is not a licence to swallow teardown there.
    A background copy that caught ``CancelledError`` and carried on would keep
    writing frames out of a session that is being closed.

    Mutation: catch ``BaseException`` (or drop the CancelledError re-raise) in
    ``_publish_provider_frames``.
    """
    client, _captured = _make_client()

    async def _cancel(*_args, **_kwargs):
        raise asyncio.CancelledError()

    async def _call():
        await client._publish_provider_frames([_png_b64(8, 8)], ["user"])

    with patch(_PUBLISHER, _cancel):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_call())


# ---------------------------------------------------------------------------
# 3. The copy is gated on delivery, and fires once.
# ---------------------------------------------------------------------------


def test_a_request_the_provider_rejected_publishes_nothing():
    """In history is not the same as delivered, and only delivery may publish.

    The publish used to sit right after the ``_conversation_history`` append,
    reasoning that the frames were in history so the provider would see them.
    It would not: the request has not been made at that point, and this turn
    dies before it ever is -- ``astream`` raises on the first ``__anext__``,
    so not a single chunk exists. The turn is still committed to history,
    which is exactly the state the old site keyed on.

    Mutation: move the publish back beside the history append (or publish from
    the staging assignment itself). This goes red.
    """
    client, captured = _make_client(chunks=[])
    client._pending_images = [_png_b64(300, 200)]

    spy = _Spy()
    with patch(_PUBLISHER, spy):
        _run_turn(client.stream_text("这是什么"))

    assert captured, "the provider call was never attempted"
    assert _has_image_parts(captured), "this turn has to be carrying frames"
    assert client._conversation_history[-1] is captured[0][-1], (
        "the turn must still be committed to history -- otherwise this tests "
        "the commit, not the delivery gate"
    )
    assert spy.calls == []


def test_every_provider_attempt_failing_leaves_the_bus_empty():
    """Three transient failures in a row: the retry ladder, not one bad call.

    ``max_retries`` is 3, and none of the three ever streams a chunk. A publish
    keyed on anything earlier than the first chunk announces a delivery that
    the whole ladder failed to make.

    Mutation: publish beside the history append. Goes red (one publish per
    frame, before the first attempt was even made).
    """
    client, captured = _make_client(chunks=[], error=_connection_error)
    client._pending_images = [_png_b64(300, 200)]

    spy = _Spy()
    with patch(_PUBLISHER, spy), patch(_SLEEP, _no_backoff):
        _run_turn(client.stream_text("这是什么"))

    assert len(captured) == 3, (
        f"the fixture must exercise all three attempts, saw {len(captured)}"
    )
    assert spy.calls == []


def test_a_raising_input_transcript_callback_publishes_nothing():
    """The callback between the commit and the provider call can throw.

    ``on_input_transcript`` runs after the history append and before the
    attempt loop, and nothing catches it -- it takes the whole turn down with
    it, before any request is made. A publish sited above it has already told
    every plugin that the model saw these frames.

    Mutation: publish beside the history append. Goes red.
    """
    client, captured = _make_client()
    client._pending_images = [_png_b64(300, 200)]

    async def _transcript_sink_is_down(_text):
        raise RuntimeError("transcript sink is down")

    client.on_input_transcript = _transcript_sink_is_down

    spy = _Spy()
    with patch(_PUBLISHER, spy):
        with pytest.raises(RuntimeError, match="transcript sink is down"):
            _run_turn(client.stream_text("这是什么"))

    assert captured == [], "the provider must never have been called"
    assert spy.calls == []


def test_the_bus_sees_a_retried_turn_once_not_once_per_attempt():
    """Retries re-send the SAME frames; the bus must not collect duplicates.

    Every attempt here reaches the provider (a chunk arrives) and then dies
    transiently, so the delivery gate opens on all three -- what keeps the
    record set honest is that the pending frames are consumed on the first one.

    Mutation: keep the publish where it is but drop the
    ``_pending_bus_frames = None`` clear, or key the publish off
    ``_ttft_recorded`` (which is re-armed per attempt). Either way this goes
    red at 3 records for 1 frame.
    """
    client, captured = _make_client(error=_connection_error)
    client._pending_images = [_png_b64(300, 200)]

    spy = _Spy()
    with patch(_PUBLISHER, spy), patch(_SLEEP, _no_backoff):
        _run_turn(client.stream_text("这是什么"))

    assert len(captured) == 3, (
        f"the fixture must exercise all three attempts, saw {len(captured)}"
    )
    assert len(spy.calls) == 1
    assert spy.images == _attached_b64(captured)


def test_a_stalled_bus_does_not_hold_up_the_reply():
    """The reason the publish left the response path at all.

    ``publish_session_event_threadsafe`` hands a cross-thread publish to the
    bridge's owner loop through an un-timed ``run_coroutine_threadsafe``. While
    the turn awaited that, a stalled bridge meant a stalled reply -- the user
    waiting on a copy that is explicitly optional. The realtime path settled
    this one round earlier; this is the offline dual.

    Mutation: await ``_publish_provider_frames`` at the publish point in
    ``_streaming`` instead of firing it. This test then hangs rather than
    fails, so it carries its own deadline.
    """
    entered = threading.Event()

    async def _never_returns(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()   # never set

    client, captured = _make_client()
    client._pending_images = [_png_b64(300, 200)]

    async def _turn():
        await asyncio.wait_for(client.stream_text("这是什么"), timeout=5)
        # 抄送确实被排上了，而且确实卡住了——否则本用例什么都没证明。
        for _ in range(50):
            await _REAL_SLEEP(0)
        assert entered.is_set(), "前提没成立：publish 根本没被调用"

    with patch(_PUBLISHER, _never_returns):
        asyncio.run(_turn())

    # 回合照常构建、照常提交，尽管那次抄送到现在都没返回。
    assert len(_attached_b64(captured)) == 1
    assert captured[0][-1] is client._conversation_history[-1]


def test_pending_bus_copies_are_bounded_not_unbounded():
    """A stalled bridge must not turn into unbounded retained base64.

    Taking the publish off the response path traded one failure for another:
    every scheduled copy parks inside the untimed handoff still holding its
    frame, and the sender keeps scheduling more. The agent-side cap cannot
    help -- it lives on the far side of the very hop that is stuck.

    Refusing the NEW copy rather than evicting an old one is deliberate:
    cancelling a scheduled task does not take its callback out of the loop's
    ready queue, so the bytes stay resident and the eviction buys nothing.

    Mutation: drop the cap check in ``spawn_bounded_frame_copy``.
    """
    from main_logic.agent_event_bus import (
        AGENT_FRAME_HANDOFF_MAX_IN_FLIGHT as CAP,
        spawn_bounded_frame_copy,
    )

    async def _check():
        inflight: set = set()

        async def _parked():
            await asyncio.Event().wait()

        admitted = [
            spawn_bounded_frame_copy(_parked(), inflight, label="test")
            for _ in range(CAP)
        ]
        assert all(t is not None for t in admitted), "上限之内就被拒了"
        assert len(inflight) == CAP

        refused = spawn_bounded_frame_copy(_parked(), inflight, label="test")
        assert refused is None, "越过上限还在收"
        assert len(inflight) == CAP, "被拒的那条不该留在集合里"

        for task in admitted:
            task.cancel()
        await asyncio.gather(*admitted, return_exceptions=True)

    asyncio.run(_check())


def test_a_finished_bus_copy_frees_its_slot():
    """The cap is in-flight, not lifetime. A completed copy must not hold one.

    Mutation: drop the ``add_done_callback`` discard.
    """
    from main_logic.agent_event_bus import spawn_bounded_frame_copy

    async def _check():
        inflight: set = set()

        async def _quick():
            return None

        task = spawn_bounded_frame_copy(_quick(), inflight, label="test")
        assert task is not None
        await task
        for _ in range(10):
            await _REAL_SLEEP(0)
        assert inflight == set(), "跑完的抄送仍占着名额"

    asyncio.run(_check())


def test_a_retried_turn_still_shows_the_tool_image_to_the_model():
    """The end of the ownership chain: stream_text keeps the pixels across attempts.

    A follow-up request that raises a retryable error used to leave history with
    the assistant tool_calls, the tool result, and a TEXT PLACEHOLDER where the
    picture had been -- and the outer ladder retried against exactly that. The
    model then answered as though it had already looked at an image no provider
    ever received. Silently wrong answers, not a performance problem.

    Mutation: drop ``_tool_image_slots=_turn_tool_image_slots`` from
    ``stream_text``'s call, or its release from the finally.
    """
    client, _captured = _make_client()
    seen_slots: list = []
    attempts: list = []

    async def _stub(messages, **overrides):
        slots = overrides.get("_tool_image_slots")
        seen_slots.append(slots)
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            # 第一次 attempt：注入一张图（真实实现会这么做），然后可重试地失败。
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": "data:image/jpeg;base64,IMGDATA"}},
                    {"type": "text", "text": "tool image"},
                ],
            })
            assert slots is not None, "stream_text 没有接管槽位"
            slots.append((messages, len(messages) - 1, messages[-1], "[占位符]"))
            raise _connection_error()
        # 第二次 attempt：模型看到的历史里，那张图还在吗？
        client._retry_saw_image = any(
            isinstance(m, dict) and isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        yield SimpleNamespace(content="看到了")

    client._astream_visible_with_tools = _stub
    with patch(_SLEEP, _no_backoff):
        _run_turn(client.stream_text("看看这个"))

    assert len(attempts) == 2, "前提没成立：没有发生重试"
    assert seen_slots[0] is seen_slots[1], "两次 attempt 拿到的不是同一份槽位"
    assert getattr(client, "_retry_saw_image", False), (
        "重试那一轮看不到像素——模型会当作自己已经看过那张图"
    )
    # 回合结束后必须清干净：base64 留在历史里会跟着之后每一次请求走。
    assert not any(
        isinstance(m, dict) and isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
        for m in client._conversation_history
    ), "回合结束后图像轮没有被释放"


def test_a_retried_turn_still_copies_its_tool_image_to_the_bus():
    """The other half of keeping tool images alive across the ladder.

    Making the SLOTS survive a retry without making the staged bus frames
    survive it produces a new asymmetry: the retry that succeeds carries the
    pixels to the provider, while attempt 1's staged copy went out with the
    call that failed and attempt 2's tool loop never runs again (history already
    holds the tool_calls and the result, so the model just answers). The model
    sees it, the plugin never does.

    Mutation: drop ``_tool_bus_frames=_turn_tool_bus_frames`` from
    ``stream_text``'s call, or the ownership check in
    ``_astream_visible_with_tools``.
    """
    client, _captured = _make_client()
    seen_frames: list = []
    attempts: list = []

    async def _stub(messages, **overrides):
        staged = overrides.get("_tool_bus_frames")
        seen_frames.append(staged)
        attempts.append(len(attempts) + 1)
        assert staged is not None, "stream_text 没有接管待抄送帧"
        if len(attempts) == 1:
            # attempt 1：工具图被注入并暂存，然后这次请求可重试地失败。
            staged.append(("IMGDATA", "image/jpeg", "demo_tool"))
            raise _connection_error()
        # attempt 2：工具循环不会再跑，暂存必须还在，否则永远没人抄送。
        client._retry_had_staged = list(staged)
        yield SimpleNamespace(content="看到了")

    client._astream_visible_with_tools = _stub
    with patch(_SLEEP, _no_backoff):
        _run_turn(client.stream_text("看看这个"))

    assert len(attempts) == 2, "前提没成立：没有发生重试"
    assert seen_frames[0] is seen_frames[1], "两次 attempt 拿到的不是同一份暂存"
    assert getattr(client, "_retry_had_staged", None) == [
        ("IMGDATA", "image/jpeg", "demo_tool")
    ], "重试那一轮的暂存被清空了——模型看到了图，插件永远读不到"


def test_an_offline_handoff_keeps_the_frames_channel():
    """The offline dual of freezing the realtime turn's source.

    A HANDOFF_REQUIRED realtime provider sends an independent-ASR visual turn to
    the offline VLM. Those frames are screen or camera pixels, but on this path
    they arrive as ``turn_images`` and were labelled ``user`` -- the label a
    plugin uses to tell what the user shared with the character apart from what
    a plugin handed the model. Wrong label, wrong filter.

    Mutation: drop ``turn_source`` from ``stream_text``'s source list, or stop
    passing it from ``submit_multimodal_turn``.
    """
    client, _captured = _make_client()
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        accepted = _run_turn(client.submit_multimodal_turn(
            "看看这个",
            (_png_b64(320, 200),),
            turn_id="asr-1",
            source="camera",
        ))

    assert accepted is not False
    assert spy.sources == ["camera"], (
        f"独立 ASR 交接过来的帧被标错了通道: {spy.sources}"
    )


def test_an_attachment_keeps_its_own_label_on_a_channelled_turn():
    """The dual guard, and it needs BOTH kinds of image in one turn.

    An independent-ASR turn drains the user's pending attachments and sends
    them alongside its own frames. The turn's frames take the capture channel;
    the attachment is still something he handed over, whatever channel the
    utterance came from. A fix that relabelled the whole list would be just as
    wrong as the bug it replaced -- and a test with only one kind of image
    cannot tell the two apart, which is what let this slip past the first
    version of this guard.

    Mutation: apply ``turn_source`` to the attachment slice too.
    """
    client, _captured = _make_client()
    client._pending_images = [_png_b64(300, 200)]
    spy = _Spy()
    with patch(_PUBLISHER, spy):
        _run_turn(client.submit_multimodal_turn(
            "看看这个",
            (_png_b64(320, 200),),
            turn_id="asr-2",
            source="camera",
        ))

    # 顺序即契约：本轮抽样帧在前、用户附件在后（见 _media.py 的排序说明）。
    assert spy.sources == ["camera", "user"], (
        f"通道标签串到附件上了: {spy.sources}"
    )


def test_an_oversized_frame_never_enters_the_session_channel():
    """The session PUB is shared, so frames cannot be bounded with SNDHWM.

    That socket carries every session event; lowering its high-water mark to
    suit multi-megabyte frames would drop text events sooner too. And the
    in-flight task cap does not help -- each task ends the moment
    ``send(NOBLOCK)`` queues the event, so the socket keeps the bytes.

    Refusing before the enqueue costs nothing: a frame this size is refused by
    the message-plane bridge at the far end anyway.

    Mutation: drop the size check in
    ``publish_provider_frame_observed_best_effort``.
    """
    from main_logic import agent_event_bus

    sent: list = []

    async def _capture(event):
        sent.append(event)
        return True

    with patch.object(agent_event_bus, "publish_session_event_threadsafe", _capture):
        async def _go():
            over = "A" * (agent_event_bus.PROVIDER_FRAME_MAX_B64_BYTES + 1)
            under = "A" * 32
            refused = await agent_event_bus.publish_provider_frame_observed_best_effort(
                "neko", image_base64=over, source="screen",
            )
            accepted = await agent_event_bus.publish_provider_frame_observed_best_effort(
                "neko", image_base64=under, source="screen",
            )
            return refused, accepted

        refused, accepted = asyncio.run(_go())

    assert refused is False, "超限的帧仍然被送进了共用的 session 通道"
    assert accepted is True, "前提没成立：正常大小的帧也没送出去"
    assert len(sent) == 1 and len(sent[0]["image_base64"]) == 32


def test_a_ladder_max_tool_image_survives_the_frame_publish():
    """Build the event and MEASURE it. Comparing constants cannot see this.

    Both ceilings read 500 KiB and looked consistent, but one bounds the image
    and the other bounds the whole event -- and the event carries event_id,
    source, mime, turn_id, generation, metadata and lanlan_name beside the
    pixels. So an image compressed to exactly the ladder's limit was rejected
    at the publish: the model got the picture and no plugin ever did, which is
    the one thing this copy exists to prevent.

    Mutation: set ``_TOOL_IMAGE_DELIVER_MAX_B64_BYTES`` back to
    ``PROVIDER_FRAME_MAX_B64_BYTES``.
    """
    from main_logic import agent_event_bus
    from main_logic.tool_calling import _TOOL_IMAGE_DELIVER_MAX_B64_BYTES

    sent: list = []

    async def _capture(event):
        sent.append(event)
        return True

    with patch.object(agent_event_bus, "publish_session_event_threadsafe", _capture):
        async def _go():
            # 阶梯允许产出的最大一张，配上这条路会带的全部字段。
            return await agent_event_bus.publish_provider_frame_observed_best_effort(
                "a-character-with-a-long-name",
                image_base64="A" * _TOOL_IMAGE_DELIVER_MAX_B64_BYTES,
                source="plugin",
                captured_at=1788095048.5590525,
                turn_id="asr-0-0",
                generation=3,
                mime="image/jpeg",
                metadata={"tool_name": "a_plugin_tool_with_a_long_name"},
            )

        published = asyncio.run(_go())

    assert published is True, (
        "阶梯压到上限的工具图在帧发布处被丢了——模型看到了，插件永远读不到"
    )
    assert len(sent) == 1


def test_the_image_budget_is_the_event_ceiling_minus_its_envelope():
    """The relationship, stated where someone changing either number reads it."""
    from main_logic.agent_event_bus import (
        PROVIDER_FRAME_ENVELOPE_HEADROOM_BYTES,
        PROVIDER_FRAME_MAX_B64_BYTES,
        PROVIDER_FRAME_MAX_IMAGE_B64_BYTES,
    )
    from main_logic.tool_calling import _TOOL_IMAGE_DELIVER_MAX_B64_BYTES
    from plugin.settings import MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    assert PROVIDER_FRAME_MAX_IMAGE_B64_BYTES == (
        PROVIDER_FRAME_MAX_B64_BYTES - PROVIDER_FRAME_ENVELOPE_HEADROOM_BYTES
    )
    assert _TOOL_IMAGE_DELIVER_MAX_B64_BYTES == PROVIDER_FRAME_MAX_IMAGE_B64_BYTES
    # 事件整体仍要装得进 plane 的记录界（那一侧按 msgpack 量整条记录）。
    assert PROVIDER_FRAME_MAX_B64_BYTES < MESSAGE_PLANE_PAYLOAD_MAX_BYTES


def test_metadata_cannot_smuggle_an_oversized_frame_event():
    """The bound has to cover the event, not just the pixels.

    ``metadata`` is caller-supplied and copied straight into the same event, so
    a small picture with a large annotation slips past a pixels-only check and
    puts an oversized record on the shared PUB path -- the very thing the check
    exists to keep off it.

    Mutation: measure ``image_base64`` only.
    """
    from main_logic import agent_event_bus

    sent: list = []

    async def _capture(event):
        sent.append(event)
        return True

    with patch.object(agent_event_bus, "publish_session_event_threadsafe", _capture):
        async def _go():
            small = "A" * 1024
            fat = {"note": "B" * agent_event_bus.PROVIDER_FRAME_MAX_B64_BYTES}
            smuggled = await agent_event_bus.publish_provider_frame_observed_best_effort(
                "neko", image_base64=small, source="screen", metadata=fat,
            )
            plain = await agent_event_bus.publish_provider_frame_observed_best_effort(
                "neko", image_base64=small, source="screen",
                metadata={"tool_name": "demo"},
            )
            return smuggled, plain

        smuggled, plain = asyncio.run(_go())

    assert smuggled is False, "超大的 metadata 把整条事件夹带进了共用通道"
    assert plain is True, "前提没成立：正常的 metadata 也被拒了"
    assert len(sent) == 1


def test_a_max_image_survives_every_hop_to_the_plane_record():
    """The third leg, and the last unmeasured one.

    A tool frame crosses three boundaries with three different measurements:
    the image against the ladder's budget, the orjson EVENT against the session
    channel's ceiling, and the msgpack RECORD against the message plane's bound.
    The first two are pinned above; this pins the last, and it is a different
    serialization on a different shape -- the record adds ``kind``, ``type``,
    ``id`` and ``timestamp`` that the event does not carry.

    Two of the three ceilings in this chain were wrong at some point in this
    branch because they were compared as constants instead of packed and
    measured. This one is measured from the start.

    Mutation: raise ``PROVIDER_FRAME_MAX_IMAGE_B64_BYTES`` past what the plane
    record can hold.
    """
    import ormsgpack

    from main_logic.agent_event_bus import PROVIDER_FRAME_MAX_IMAGE_B64_BYTES
    from plugin.server.messaging.plane_bridge import build_frame_record
    from plugin.settings import MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    record = build_frame_record(
        image_base64="A" * PROVIDER_FRAME_MAX_IMAGE_B64_BYTES,
        source="plugin",
        captured_at=1788095048.5590525,
        turn_id="asr-0-0",
        generation=3,
        mime="image/jpeg",
        lanlan_name="a-character-with-a-long-name",
        frame_id="0123456789abcdef0123456789abcdef",
        metadata={"tool_name": "a_plugin_tool_with_a_long_name"},
    )
    packed = len(ormsgpack.packb(record))

    assert packed <= MESSAGE_PLANE_PAYLOAD_MAX_BYTES, (
        f"最大的一张图打包成 plane 记录是 {packed} B，超过 "
        f"{MESSAGE_PLANE_PAYLOAD_MAX_BYTES} B——bridge 会以 payload_too_big 拒收，"
        "模型看到了而插件读不到"
    )


def test_offline_connect_lowers_the_bus_copy_latch() -> None:
    """The offline dual of the realtime latch reset.

    ``_bus_copies_closed`` is latched by ``_cancel_bus_copies`` at close; a
    reused ``OmniOfflineClient`` that reconnects would otherwise publish
    nothing, silently.
    """
    import ast
    import inspect
    from pathlib import Path

    from main_logic.omni_offline_client import _streaming

    tree = ast.parse(Path(inspect.getfile(_streaming)).read_text(encoding="utf-8"))
    connect = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "connect":
            connect = node
    assert connect is not None, "前提没成立：找不到 connect"

    resets = [
        node
        for node in ast.walk(connect)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and t.attr == "_bus_copies_closed"
            for t in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
    ]
    assert resets, "connect 没把 _bus_copies_closed 落下——重连后抄送永久停摆"
