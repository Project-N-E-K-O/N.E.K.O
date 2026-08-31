"""Realtime stream_image copies delivered frames onto the plugin frames bus.

The rule the whole surface rests on: a frame reaches the bus if and only if the
provider received it. The throttle, the delivery-mode fence and a refused send
all mean "never sent", and a frame that was shrunk on the way out must reach
plugins as the picture the provider got -- not the bigger one the caller passed.
"""

import asyncio
import base64
import json
import random
import time
from io import BytesIO
from unittest.mock import AsyncMock

import pytest

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from main_logic.omni_realtime_client import _transport as transport_module


# Dummy 1x1 pixel JPEG image in base64
DUMMY_IMAGE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAAAAAAAACf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AE0A/9k="


class _FrameRecorder:
    """Stand-in for the event-bus publisher; records what the host handed it."""

    def __init__(self):
        self.calls = []
        self.raises = None

    async def __call__(self, lanlan_name, **kwargs):
        self.calls.append(dict(kwargs, lanlan_name=lanlan_name))
        if self.raises is not None:
            raise self.raises
        return True

    @property
    def latest(self):
        assert self.calls, "no frame was published"
        return self.calls[-1]


def _install_recorder(monkeypatch) -> _FrameRecorder:
    recorder = _FrameRecorder()
    monkeypatch.setattr(
        "main_logic.agent_event_bus.publish_provider_frame_observed_best_effort",
        recorder,
    )
    return recorder


async def _wait_for_publish(recorder: _FrameRecorder, count: int = 1) -> None:
    """The publish is fire-and-forget; give its task a chance to run."""

    for _ in range(50):
        if len(recorder.calls) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(
        f"expected {count} published frame(s), saw {len(recorder.calls)}"
    )


async def _assert_nothing_published(recorder: _FrameRecorder) -> None:
    for _ in range(10):
        await asyncio.sleep(0)
    assert recorder.calls == [], f"frame published from a non-delivery: {recorder.calls}"


def _make_client(
    model: str,
    *,
    api_type: str | None = None,
    supports_native_image: bool = True,
    base_url: str = "wss://test.example.com",
) -> OmniRealtimeClient:
    client = OmniRealtimeClient(
        base_url=base_url,
        api_key="test-key",
        model=model,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type=api_type,
        on_text_delta=AsyncMock(),
        on_audio_delta=AsyncMock(),
        on_input_transcript=AsyncMock(),
        on_output_transcript=AsyncMock(),
    )
    client.ws = AsyncMock()
    client._supports_native_image = supports_native_image
    client._audio_in_buffer = True
    client._last_native_image_time = 0
    return client


def _jpeg_bytes(img, quality: int) -> bytes:
    buf = BytesIO()
    # Same save call the shrinkers make, so the expected bytes are exact.
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _oversized_frame_fixture():
    """A frame that really is too big, plus the bytes q50 really produces.

    Noise, so JPEG cannot compress it away, and small enough to keep the PIL
    round trip cheap: the WS limit is lowered to sit between the two encodings
    instead of making a 250 KB picture.
    """

    from PIL import Image as PILImage

    rnd = random.Random(20260830)
    side = 128
    img = PILImage.new("RGB", (side, side))
    img.putdata([
        (rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
        for _ in range(side * side)
    ])
    raw_big = _jpeg_bytes(img, 95)
    # The shrinker re-encodes what it decodes from the outgoing frame, not the
    # original pixels, so the expectation has to come off the same round trip.
    shrunk = _jpeg_bytes(PILImage.open(BytesIO(raw_big)), 50)
    assert len(shrunk) < len(raw_big)
    return (
        base64.b64encode(raw_big).decode(),
        base64.b64encode(shrunk).decode(),
    )


@pytest.mark.unit
@pytest.mark.parametrize("model", ["qwen-omni-turbo", "glm-4-realtime", "gpt-4o-realtime"])
async def test_delivered_frame_is_published_with_its_identity(monkeypatch, model):
    recorder = _install_recorder(monkeypatch)
    client = _make_client(model)
    client.get_host_turn_id = lambda: "speech-42"

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is True
    await _wait_for_publish(recorder)
    published = recorder.latest
    assert published["image_base64"] == DUMMY_IMAGE_B64
    assert published["source"] == "screen"
    assert published["turn_id"] == "speech-42"
    assert published["mime"] == "image/jpeg"
    assert published["generation"] == client._latest_image_generation
    await client.close()


@pytest.mark.unit
@pytest.mark.parametrize("model", ["qwen-omni-turbo", "glm-4-realtime", "gpt-4o-realtime"])
async def test_published_bytes_are_the_recompressed_ones_the_provider_got(
    monkeypatch, model
):
    """The compression rewrites the outgoing event in place, so the caller's
    ``image_b64`` is the picture that was thrown away. Publishing it would put
    a different, larger frame on the bus than the provider ever saw."""

    big_b64, shrunk_b64 = _oversized_frame_fixture()
    recorder = _install_recorder(monkeypatch)
    client = _make_client(model)

    # Sit the frame limit between the two encodings: the frame as built is over
    # it, the q50 rewrite is under it, so the real shrink path runs end to end.
    sent_payloads = []
    client.ws.send.side_effect = lambda payload: sent_payloads.append(payload)
    probe = json.dumps({"image": big_b64})
    limit = (len(probe) + len(json.dumps({"image": shrunk_b64}))) // 2
    monkeypatch.setattr(transport_module, "OMNI_WS_FRAME_LIMIT_BYTES", limit)
    monkeypatch.setattr(transport_module._TransportMixin, "_WS_FRAME_LIMIT", limit)

    result = await client.stream_image(big_b64, source="screen")

    assert result.accepted is True
    assert sent_payloads and shrunk_b64 in sent_payloads[-1]
    await _wait_for_publish(recorder)
    published = recorder.latest["image_base64"]
    assert published == shrunk_b64
    assert published != big_b64
    await client.close()


@pytest.mark.unit
async def test_the_free_route_also_publishes_the_recompressed_bytes(monkeypatch):
    """Same guarantee, second site -- and the parametrised test above cannot
    reach it: the free branch is a separate ``if`` with its own publish call,
    entered only by a free-route client, which every model above bypasses.
    Covering one site and calling the property covered is how the other one
    silently starts publishing the picture that was thrown away."""

    big_b64, shrunk_b64 = _oversized_frame_fixture()
    recorder = _install_recorder(monkeypatch)
    client = _make_client(
        "free-model",
        api_type="free",
        base_url="wss://www.lanlan.tech/api/v1/realtime",
        supports_native_image=False,
    )
    assert client._is_free_provider, "fixture did not land on the free route"
    # This turn already has visual state, so the non-native description path is
    # not armed; the analyser is mocked anyway so nothing can reach the network.
    client._image_description = "已有视觉状态"
    client._analyze_image_with_vision_model = AsyncMock(return_value="")

    sent_payloads = []
    client.ws.send.side_effect = lambda payload: sent_payloads.append(payload)
    probe = json.dumps({"image": big_b64})
    limit = (len(probe) + len(json.dumps({"image": shrunk_b64}))) // 2
    monkeypatch.setattr(transport_module, "OMNI_WS_FRAME_LIMIT_BYTES", limit)
    monkeypatch.setattr(transport_module._TransportMixin, "_WS_FRAME_LIMIT", limit)

    result = await client.stream_image(big_b64, source="screen")

    assert result.accepted is True
    assert result.mode == "native", "fell down the description path, not the free one"
    client._analyze_image_with_vision_model.assert_not_awaited()
    assert sent_payloads and shrunk_b64 in sent_payloads[-1]
    await _wait_for_publish(recorder)
    published = recorder.latest["image_base64"]
    assert published == shrunk_b64
    assert published != big_b64
    await client.close()


@pytest.mark.unit
async def test_gemini_publishes_the_bytes_it_handed_the_sdk(monkeypatch):
    recorder = _install_recorder(monkeypatch)
    client = _make_client("gemini-2.5-flash-native-audio", api_type="gemini")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session

    result = await client.stream_image(DUMMY_IMAGE_B64, source="camera")

    assert result.accepted is True
    session.send_realtime_input.assert_awaited_once()
    await _wait_for_publish(recorder)
    assert recorder.latest["image_base64"] == DUMMY_IMAGE_B64
    assert recorder.latest["source"] == "camera"
    await client.close()


@pytest.mark.unit
async def test_free_route_publishes_even_without_the_native_image_flag(monkeypatch):
    """The free branch sends regardless of ``_supports_native_image``; gating the
    publish on that flag would silently drop real deliveries."""

    recorder = _install_recorder(monkeypatch)
    client = _make_client(
        "free-model",
        api_type="free",
        base_url="wss://www.lanlan.tech/api/v1/realtime",
        supports_native_image=False,
    )
    # This turn already has visual state, so the non-native description path is
    # not armed; the analyzer is mocked anyway so nothing can reach the network.
    client._image_description = "已有视觉状态"
    client._analyze_image_with_vision_model = AsyncMock(return_value="")

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is True
    assert result.mode == "native"
    client._analyze_image_with_vision_model.assert_not_awaited()
    await _wait_for_publish(recorder)
    assert recorder.latest["image_base64"] == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_a_throttled_frame_is_never_published(monkeypatch):
    recorder = _install_recorder(monkeypatch)
    client = _make_client("qwen-omni-turbo")

    await client.stream_image(DUMMY_IMAGE_B64, source="screen")
    await _wait_for_publish(recorder)
    recorder.calls.clear()

    # Inside NATIVE_IMAGE_MIN_INTERVAL: never sent, so never copied.
    second = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert second.accepted is False
    await _assert_nothing_published(recorder)
    await client.close()


@pytest.mark.unit
async def test_a_refused_send_is_never_published(monkeypatch):
    """A frame the fence or the transport refused was not delivered."""

    recorder = _install_recorder(monkeypatch)
    client = _make_client("qwen-omni-turbo")
    client.send_event = AsyncMock(return_value=False)

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is False
    await _assert_nothing_published(recorder)
    await client.close()


@pytest.mark.unit
async def test_external_description_fallback_publishes_nothing(monkeypatch):
    """Standard StepFun sends a text description, not the frame."""

    recorder = _install_recorder(monkeypatch)
    client = _make_client("step-realtime", supports_native_image=False)
    client._image_description = "实时屏幕截图或相机画面正在分析中"
    client._analyze_image_with_vision_model = AsyncMock(return_value="一只猫")

    await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert client._analyze_image_with_vision_model.called
    await _assert_nothing_published(recorder)
    await client.close()


@pytest.mark.unit
async def test_a_failing_publish_cannot_fail_the_delivery(monkeypatch):
    recorder = _install_recorder(monkeypatch)
    recorder.raises = RuntimeError("bus down")
    client = _make_client("qwen-omni-turbo")
    published_tasks = []
    fire_task = client._fire_task
    client._fire_task = lambda coro: published_tasks.append(fire_task(coro)) or published_tasks[-1]

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is True
    assert client.ws.send.called
    # Awaiting the copy task re-raises anything it failed to swallow, which is
    # the only way a background publish can be seen to have gone wrong at all.
    await asyncio.gather(*published_tasks)
    assert recorder.calls, "the publisher was never reached"
    await client.close()


@pytest.mark.unit
async def test_an_unschedulable_publish_cannot_fail_the_delivery(monkeypatch):
    recorder = _install_recorder(monkeypatch)
    client = _make_client("qwen-omni-turbo")

    def _no_tasks(_coro):
        _coro.close()
        raise RuntimeError("no running event loop")

    client._fire_task = _no_tasks

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is True
    await _assert_nothing_published(recorder)
    await client.close()


@pytest.mark.unit
async def test_captured_at_is_published_on_the_wall_clock(monkeypatch):
    """Callers time frames with time.monotonic(); the store sorts wall clock."""

    recorder = _install_recorder(monkeypatch)
    client = _make_client("qwen-omni-turbo")
    ingress = time.monotonic() - 0.5

    before = time.time()
    await client.stream_image(DUMMY_IMAGE_B64, source="screen", captured_at=ingress)
    await _wait_for_publish(recorder)

    published = recorder.latest["captured_at"]
    assert before - 1.0 <= published <= before + 1.0
    assert published < time.time()
    await client.close()


@pytest.mark.unit
def test_the_delivered_frame_reader_takes_the_newest_part_and_its_own_mime():
    """A multi-image item that could not be shrunk enough drops its OLDEST
    parts, so the newest surviving one is the frame the delivery is about."""

    read = transport_module._TransportMixin._delivered_frame_from_event
    event = {
        "type": "conversation.item.create",
        "item": {
            "content": [
                {"type": "input_text", "text": "看一下"},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,OLD"},
                {"type": "input_image", "image_url": "data:image/png;base64,NEW"},
            ]
        },
    }

    assert read(event) == ("NEW", "image/png")
    assert read({"type": "conversation.item.create", "item": {"content": []}}) is None


@pytest.mark.unit
def test_an_uninterpretable_capture_time_falls_back_to_now():
    """An epoch-seconds caller must not file the record decades away."""

    now = time.time()
    from_epoch = transport_module._TransportMixin._frame_bus_wall_clock(time.time())
    from_none = transport_module._TransportMixin._frame_bus_wall_clock(None)

    assert abs(from_epoch - now) < 5.0
    assert abs(from_none - now) < 5.0


@pytest.mark.unit
async def test_a_one_shot_cue_image_reuses_the_ambient_generation(monkeypatch):
    """``generation`` orders ambient frames only: cache_latest=False does not
    advance it, so two published records can share one. Plugin-side dedup is
    documented on the record id, which the forwarder assigns per publish."""

    recorder = _install_recorder(monkeypatch)
    client = _make_client("qwen-omni-turbo")

    await client.stream_image(DUMMY_IMAGE_B64, source="screen")
    await _wait_for_publish(recorder)
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="proactive",
        bypass_rate_limit=True,
        cache_latest=False,
    )
    await _wait_for_publish(recorder, count=2)

    assert recorder.calls[0]["generation"] == recorder.calls[1]["generation"]
    assert recorder.calls[1]["source"] == "proactive"
    await client.close()


@pytest.mark.unit
async def test_close_cancels_the_in_flight_frame_copies():
    """The dual of the offline client's ``_cancel_bus_copies``.

    ``_bg_tasks`` is never cancelled on close, so a copy parked in the
    cross-loop handoff would outlive the session holding its base64 -- and
    publish a frame from a retired session if the bridge later recovered.

    Mutation: drop the ``_cancel_frame_copies`` call from ``close()``, or the
    ``cancel()`` loop inside it.
    """
    client = _make_client("qwen-omni-turbo")
    started = asyncio.Event()

    async def _parked():
        started.set()
        await asyncio.Event().wait()

    task = client._fire_frame_copy(_parked())
    assert task is not None
    await asyncio.wait_for(started.wait(), timeout=5)

    await asyncio.wait_for(client.close(), timeout=5)

    assert task.done(), "close 返回时抄送还在跑"
    assert task.cancelled()
    assert not client._frame_copy_tasks, "集合没被清空"


@pytest.mark.unit
async def test_a_realtime_frame_is_attributed_to_its_character(monkeypatch):
    """frames/all is shared, so an unattributed frame is an unusable one.

    With several characters holding realtime sessions at once, a plugin reading
    the bus could not tell whose pictures it was looking at -- while offline
    frames carried the name correctly. The client is told which character it
    belongs to at construction.

    Mutation: drop ``lanlan_name`` from the constructor, or publish ``None``
    again from the transport / responses publish sites.
    """
    recorder = _install_recorder(monkeypatch)
    client = _make_client("qwen-omni-turbo")
    client.lanlan_name = "neko-b"

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is True
    await _wait_for_publish(recorder)
    assert [call["lanlan_name"] for call in recorder.calls] == ["neko-b"]
    await client.close()


@pytest.mark.unit
async def test_the_constructor_carries_the_character_name():
    """The plumbing, not just the field.

    ``core/lifecycle.py`` passes it at construction; a client built without it
    keeps the old unattributed behaviour rather than raising, which is why this
    pins both halves.
    """
    named = OmniRealtimeClient(
        base_url="wss://test.example.com",
        api_key="k",
        model="qwen-omni-turbo",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        lanlan_name="neko-a",
    )
    assert named.lanlan_name == "neko-a"

    unnamed = OmniRealtimeClient(
        base_url="wss://test.example.com",
        api_key="k",
        model="qwen-omni-turbo",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
    )
    assert unnamed.lanlan_name is None


@pytest.mark.unit
async def test_close_stops_new_realtime_frame_copies_from_starting():
    """Draining is not enough on its own -- the offline client learned this first.

    An image send already awaiting the provider resolves after the drain, fires
    a fresh copy, and that one outlives the closed session with nothing left to
    collect it.

    Mutation: drop the ``_frame_copies_closed`` latch, keeping only the drain.
    """
    client = _make_client("qwen-omni-turbo")

    async def _late():
        return None

    await asyncio.wait_for(client._cancel_frame_copies(), timeout=5)

    assert client._fire_frame_copy(_late()) is None, (
        "close 之后仍然接受了新的帧抄送"
    )


# ── every cue image in prompt_ephemeral is a proactive frame ────────────


def test_prompt_ephemeral_labels_every_cue_image_proactive() -> None:
    """Both delivery branches, not just the one that was noticed.

    ``prompt_ephemeral`` sends its snapshot two ways: natively when the
    provider supports images, and via an external description otherwise. Only
    the second passed ``source="proactive"``; the native one fell through to
    ``stream_image``'s ``"unknown"`` default, so a plugin filtering the frames
    bus for proactive cues silently missed native providers.

    Expressed as "every ``stream_image`` call inside this function", so a third
    branch added later is covered without anyone remembering this test.
    """
    import ast
    import inspect
    from pathlib import Path

    from main_logic.omni_realtime_client import _responses

    tree = ast.parse(Path(inspect.getfile(_responses)).read_text(encoding="utf-8"))

    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "prompt_ephemeral":
            target = node
    assert target is not None, "前提没成立：找不到 prompt_ephemeral"

    calls = [
        node
        for node in ast.walk(target)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "stream_image"
    ]
    assert len(calls) >= 2, f"只找到 {len(calls)} 处 stream_image，分支结构变了"

    for call in calls:
        source = None
        for kw in call.keywords:
            if kw.arg == "source" and isinstance(kw.value, ast.Constant):
                source = kw.value.value
        assert source == "proactive", (
            f"第 {call.lineno} 行的 stream_image 没传 source=\"proactive\"——"
            "这一帧会以默认值进总线，按 proactive 过滤的插件读不到"
        )


def test_connect_lowers_the_frame_copy_latch() -> None:
    """``close()`` latches frame copies off; a reused client must not stay off.

    The same client instance is reused across sessions, and the failure is
    silent — bus copies are best-effort, so nothing raises; the character just
    stops appearing on the frames bus after its first reconnect.

    Asserted from the source of ``connect`` because standing up a real
    websocket session here would test the transport, not the latch.

    Mutation: delete the reset in ``connect``.
    """
    import ast
    import inspect
    from pathlib import Path

    from main_logic.omni_realtime_client import _transport

    tree = ast.parse(Path(inspect.getfile(_transport)).read_text(encoding="utf-8"))
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
            isinstance(t, ast.Attribute) and t.attr == "_frame_copies_closed"
            for t in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and node.value.value is False
    ]
    assert resets, "connect 没把 _frame_copies_closed 落下——重连后帧抄送永久停摆"
