import asyncio
from types import MethodType
from unittest.mock import AsyncMock

import pytest

from main_logic.omni_realtime_client import (
    ImageStageResult,
    MultimodalTurnDelivery,
    OmniRealtimeClient,
    TurnDetectionMode,
)


DUMMY_IMAGE_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL"
    "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAA"
    "AAAAAACf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AE0A/9k="
)


def _make_client(
    api_type: str,
    model: str,
    *,
    turn_admission_lock: asyncio.Lock | None = None,
) -> OmniRealtimeClient:
    return OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model=model,
        api_type=api_type,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        turn_admission_lock=turn_admission_lock,
    )


def _wire_completed_response_transport(client: OmniRealtimeClient) -> list[dict]:
    sent: list[dict] = []

    async def send_event(_self, event, **_kwargs):
        copied = dict(event)
        sent.append(copied)
        arbiter = _self._response_arbiter
        if event["type"] == "conversation.item.create":
            arbiter.notify_item_created(
                {
                    "type": "conversation.item.created",
                    "item": {
                        "id": event["item"]["id"],
                        "type": "message",
                        "role": "user",
                    },
                }
            )
        elif event["type"] == "response.create":
            arbiter.notify_response_created({"type": "response.created"})
            arbiter.notify_response_terminal({"type": "response.done"})
        return True

    client.send_event = MethodType(send_event, client)
    client._response_arbiter._send_event = client.send_event
    return sent


@pytest.mark.parametrize(
    ("api_type", "model", "expected"),
    [
        ("openai", "gpt-4o-realtime", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("gpt", "gpt-4o-realtime", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("gemini", "gemini-2.5-flash-native-audio", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("qwen", "qwen3-omni-flash-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("glm", "glm-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("step", "step-audio-2-mini", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("grok", "grok-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("free", "free-model", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("local", "custom-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
    ],
)
def test_multimodal_turn_delivery_is_provider_adapter_capability(
    api_type,
    model,
    expected,
):
    client = _make_client(api_type, model)

    assert client.get_multimodal_turn_delivery() is expected


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_is_one_atomic_user_item():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)
    client._analyze_image_with_vision_model = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gpt")
    ticket = await client.submit_multimodal_turn(
        "图片里是什么？",
        DUMMY_IMAGE_B64,
        turn_id="turn-gpt",
    )

    assert ticket is not None
    assert [event["type"] for event in sent] == [
        "conversation.item.create",
        "response.create",
    ]
    content = sent[0]["item"]["content"]
    assert content == [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + DUMMY_IMAGE_B64,
        },
        {"type": "input_text", "text": "图片里是什么？"},
    ]
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_carries_the_sampled_span_in_one_item():
    """The sampled span shares one user item and still triggers one reply."""
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-span")
    await client.submit_multimodal_turn(
        "这是什么？",
        (DUMMY_IMAGE_B64, DUMMY_IMAGE_B64, DUMMY_IMAGE_B64),
        turn_id="turn-span",
    )

    assert [event["type"] for event in sent] == [
        "conversation.item.create",
        "response.create",
    ]
    content = sent[0]["item"]["content"]
    assert [part["type"] for part in content] == [
        "input_image",
        "input_image",
        "input_image",
        "input_text",
    ]
    await client.close()


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_caps_frames_at_the_per_turn_budget():
    """Provider-side floor: an item written into the conversation is final."""
    from config import MAX_MULTIMODAL_TURN_IMAGES

    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-flood")
    await client.submit_multimodal_turn(
        "这是什么？",
        tuple(DUMMY_IMAGE_B64 for _ in range(12)),
        turn_id="turn-flood",
    )

    content = sent[0]["item"]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    assert len(images) == MAX_MULTIMODAL_TURN_IMAGES
    await client.close()


@pytest.mark.asyncio
async def test_new_external_turn_rejects_superseded_multimodal_ticket():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    _wire_completed_response_transport(client)
    arbiter = client._response_arbiter
    real_enqueue = arbiter.enqueue
    old_ticket_queued = asyncio.Event()
    release_enqueue_return = asyncio.Event()

    async def enqueue_then_pause_return(*args, **kwargs):
        ticket = await real_enqueue(*args, **kwargs)
        old_ticket_queued.set()
        await release_enqueue_return.wait()
        return ticket

    arbiter.enqueue = enqueue_then_pause_return
    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_submit = asyncio.create_task(
        client.submit_multimodal_turn(
            "旧问题",
            DUMMY_IMAGE_B64,
            turn_id="turn-old",
        )
    )
    await old_ticket_queued.wait()

    await client.prepare_external_voice_turn(turn_id="turn-new")
    release_enqueue_return.set()

    with pytest.raises(RuntimeError, match="admission rejected"):
        await old_submit
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_gemini_multimodal_turn_is_one_content_with_image_and_text():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gemini")
    result = await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-gemini",
    )

    assert result is None
    session.send_client_content.assert_awaited_once()
    kwargs = session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is True
    assert len(kwargs["turns"]) == 1
    content = kwargs["turns"][0]
    assert content.role == "user"
    assert len(content.parts) == 2
    assert bytes(content.parts[0].inline_data.data)
    assert content.parts[0].inline_data.mime_type == "image/jpeg"
    assert content.parts[1].text == "看一下这张图"
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_gemini_multimodal_turn_carries_the_sampled_span_in_one_content():
    """The sampled span shares one Content and still triggers one reply."""
    from config import MAX_MULTIMODAL_TURN_IMAGES

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gemini-span")
    await client.submit_multimodal_turn(
        "看一下这张图",
        tuple(DUMMY_IMAGE_B64 for _ in range(9)),
        turn_id="turn-gemini-span",
    )

    session.send_client_content.assert_awaited_once()
    kwargs = session.send_client_content.await_args.kwargs
    assert len(kwargs["turns"]) == 1
    parts = kwargs["turns"][0].parts
    # Provider 侧独立兜底，多余的丢弃。
    assert len(parts) == MAX_MULTIMODAL_TURN_IMAGES + 1
    assert all(part.inline_data is not None for part in parts[:-1])
    assert parts[-1].text == "看一下这张图"
    await client.close()


@pytest.mark.parametrize(
    ("api_type", "model"),
    [
        ("qwen", "qwen3-omni-flash-realtime"),
        ("glm", "glm-realtime"),
        ("step", "step-audio-2-mini"),
        ("grok", "grok-realtime"),
        ("free", "free-model"),
        ("local", "custom-realtime"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_realtime_multimodal_turn_fails_closed_without_send(
    api_type,
    model,
):
    client = _make_client(api_type, model)
    client.ws = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    with pytest.raises(RuntimeError, match="requires VLM handoff"):
        await client.submit_multimodal_turn(
            "不要退化成纯文本",
            DUMMY_IMAGE_B64,
            turn_id="turn-handoff",
        )

    client.ws.send.assert_not_awaited()
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_multimodal_turn_rejects_invalid_image_before_provider_send():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()

    with pytest.raises(ValueError, match="valid base64"):
        await client.submit_multimodal_turn(
            "这张图坏了",
            "not-base64!",
            turn_id="turn-invalid",
        )

    client.ws.send.assert_not_awaited()
    await client.close()


def test_stage_multimodal_frame_only_updates_raw_cache():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    client._analyze_image_with_vision_model = AsyncMock()

    result = client.stage_multimodal_frame(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="screen-1",
        captured_at=10.0,
    )

    assert result == ImageStageResult(
        accepted=True,
        mode="staged",
        generation=1,
    )
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    assert client._latest_image_captured_at == 10.0
    assert client._latest_image_source == "screen"
    assert client._latest_image_request_id == "screen-1"
    client._analyze_image_with_vision_model.assert_not_called()


def test_stage_multimodal_frame_rejects_stale_capture():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    first = client.stage_multimodal_frame(DUMMY_IMAGE_B64, captured_at=20.0)
    stale = client.stage_multimodal_frame(DUMMY_IMAGE_B64, captured_at=19.0)

    assert first.accepted is True
    assert stale == ImageStageResult(
        accepted=False,
        mode="staged",
        generation=1,
        rejection_reason="stale_frame",
    )
    assert client._latest_image_captured_at == 20.0


@pytest.mark.parametrize(
    ("api_type", "model"),
    [
        ("openai", "gpt-4o-realtime"),
        ("gemini", "gemini-2.5-flash-native-audio"),
    ],
)
@pytest.mark.asyncio
async def test_callback_owned_image_bypasses_raw_frame_fence_only(
    api_type,
    model,
):
    client = _make_client(api_type, model)
    provider = AsyncMock()
    client.ws = provider
    if api_type == "gemini":
        client._gemini_session = provider
    client.block_raw_visual_delivery()

    ambient = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        bypass_rate_limit=True,
    )
    proactive = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="proactive",
        bypass_rate_limit=True,
        cache_latest=False,
    )
    callback_cached = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        bypass_rate_limit=True,
    )
    callback = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        bypass_rate_limit=True,
        cache_latest=False,
    )

    assert ambient.accepted is False
    assert ambient.rejection_reason == "raw_visual_delivery_blocked"
    assert proactive.accepted is False
    assert proactive.rejection_reason == "raw_visual_delivery_blocked"
    assert callback_cached.accepted is False
    assert callback_cached.rejection_reason == "raw_visual_delivery_blocked"
    assert callback.accepted is True
    assert client._latest_image_b64 is None
    if api_type == "gemini":
        provider.send_realtime_input.assert_awaited_once()
    else:
        provider.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_callback_fence_bypass_still_rejects_visual_mode_change_before_send():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    client.block_raw_visual_delivery()
    client._send_semaphore = asyncio.Semaphore(1)
    await client._send_semaphore.acquire()
    sending = asyncio.create_task(
        client.stream_image(
            DUMMY_IMAGE_B64,
            source="callback",
            bypass_rate_limit=True,
            cache_latest=False,
        )
    )
    await asyncio.sleep(0)

    client.set_visual_delivery_mode("external_description")
    client._send_semaphore.release()
    result = await sending

    assert result.accepted is False
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_loud_pcm_publishes_activity_before_provider_admission():
    admission_lock = asyncio.Lock()
    client = _make_client(
        "openai",
        "gpt-4o-realtime",
        turn_admission_lock=admission_lock,
    )
    client.ws = AsyncMock()
    client._resample_uplink = lambda audio: audio
    client._user_recent_activity_time = 0.0
    await admission_lock.acquire()
    loud_pcm = (1_000).to_bytes(2, "little", signed=True) * 512

    streaming = asyncio.create_task(client.stream_audio(loud_pcm))
    await asyncio.sleep(0)

    assert client._user_recent_activity_time > 0.0
    assert not streaming.done()
    client.ws.send.assert_not_awaited()

    admission_lock.release()
    await streaming
    client.ws.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_step_legacy_one_shot_annotation_remains_outside_asr_routing_scope():
    client = _make_client("step", "step-audio-2-mini")
    client.ws = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(
        return_value="legacy Step description"
    )

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        cache_latest=False,
        bypass_rate_limit=True,
        source="callback",
        request_id="callback-1",
    )

    assert result == ImageStageResult(
        accepted=True,
        mode="external_description",
        generation=0,
        description="legacy Step description",
    )
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_proactive_prompt_ephemeral_keeps_environment_annotation_exception(
    monkeypatch,
):
    client = _make_client("step", "step-audio-2-mini")
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    analyze_image = AsyncMock(return_value="屏幕上显示番茄钟结束提醒")
    monkeypatch.setattr(
        "utils.screenshot_utils.analyze_image_with_vision_model",
        analyze_image,
    )
    real_analyze = client._analyze_image_with_vision_model
    client._analyze_image_with_vision_model = AsyncMock(wraps=real_analyze)
    injected: dict = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return object()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)

    staged = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="proactive-screen",
    )
    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert staged.accepted is True
    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    visual_event = injected["events_before_text"][0]
    visual_text = visual_event["item"]["content"][0]["text"]
    assert "屏幕上显示番茄钟结束提醒" in visual_text
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64
    )
    analyze_image.assert_awaited_once()
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_native_qwen_streaming_remains_audio_buffer_bound():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    client.ws = AsyncMock()
    client._audio_in_buffer = True

    result = await client.stream_image(DUMMY_IMAGE_B64)

    assert result.accepted is True
    sent = client.ws.send.await_args.args[0]
    assert "input_image_buffer.append" in sent
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_cancels_multimodal_sdk_send_before_returning():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_client_content(*_args, **_kwargs):
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise

    session.send_client_content.side_effect = send_client_content
    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_submit = asyncio.create_task(
        client.submit_multimodal_turn(
            "旧问题",
            DUMMY_IMAGE_B64,
            turn_id="turn-old",
        )
    )
    await send_started.wait()

    await client.prepare_external_voice_turn(turn_id="turn-new")

    assert send_cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await old_submit
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_quarantines_accepted_multimodal_turn():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    old_session = AsyncMock()
    old_context = AsyncMock()
    old_context.__aexit__ = AsyncMock()
    client._gemini_session = old_session
    client._gemini_context_manager = old_context
    client.ws = old_session
    client.instructions = "system prompt"
    client._native_audio = True
    client.handle_interruption = AsyncMock()
    replacement_session = AsyncMock()

    async def reconnect(*_args, **_kwargs):
        client._connection_generation += 1
        client._gemini_session = replacement_session
        client.ws = replacement_session

    client.connect = AsyncMock(side_effect=reconnect)
    await client.prepare_external_voice_turn(turn_id="turn-old")
    await client.submit_multimodal_turn(
        "Gemini 已接受",
        DUMMY_IMAGE_B64,
        turn_id="turn-old",
    )

    assert client._gemini_external_submit_task is None
    assert client._gemini_external_outcome_token is not None

    reconnected = await client.prepare_external_voice_turn(turn_id="turn-new")

    old_context.__aexit__.assert_awaited_once_with(None, None, None)
    client.connect.assert_awaited_once_with("system prompt", native_audio=True)
    assert client._gemini_session is replacement_session
    assert client._gemini_external_outcome_token is None
    assert reconnected is True
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_retired_gemini_handler_cannot_settle_replacement_external_turn():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    release_old_handler = asyncio.Event()

    class OldSession:
        def receive(self):
            async def responses():
                await release_old_handler.wait()
                raise RuntimeError("closed")
                yield None

            return responses()

    client._gemini_session = OldSession()
    old_handler = asyncio.create_task(client._handle_messages_gemini())
    await asyncio.sleep(0)

    client._connection_generation += 1
    replacement_token = object()
    client._gemini_external_outcome_token = replacement_token
    release_old_handler.set()
    await old_handler

    assert client._gemini_external_outcome_token is replacement_token
    client._gemini_external_outcome_token = None
    await client.close()


@pytest.mark.asyncio
async def test_gemini_text_only_external_asr_stays_text_only():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session

    await client.submit_external_voice_turn("只有转写", turn_id="turn-text")

    kwargs = session.send_client_content.await_args.kwargs
    content = kwargs["turns"][0]
    assert len(content.parts) == 1
    assert content.parts[0].text == "只有转写"
    await client.close()


@pytest.mark.asyncio
async def test_proactive_nudge_still_speaks_while_raw_frames_are_fenced():
    """A fenced raw route means no visual, not no nudge.

    Independent ASR arms the session's raw-frame fence but keeps the
    latest-frame cache warm for proactive observation. Treating that cached
    frame as deliverable makes every proactive turn fail its native image
    inject and return without sending even its text -- and a screen share
    keeps rearming the cache, so she stays silent for the whole session.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    injected: dict = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return object()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)
    assert client._supports_native_image is True

    # Core owns the frames while independent ASR runs: the cache is kept warm
    # without the frame ever being allowed onto the provider connection.
    client.block_raw_visual_delivery()
    staged = client.stage_multimodal_frame(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="independent-screen-1",
    )
    assert staged.accepted is True
    assert client._latest_image_b64 == DUMMY_IMAGE_B64

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    assert not injected.get("events_before_text")
    # 帧没被消费：栅栏解除之后它还能用。
    assert client._proactive_image_consumed is False
    await client.close()


@pytest.mark.asyncio
async def test_live_gemini_external_turn_counts_as_an_active_response():
    """Gemini owns the turn before its first content event arrives.

    Between the SDK send returning and the first model content, ``_is_responding``
    is still false and the arbiter is idle, yet the provider has already accepted
    the external-ASR turn (`_gemini_external_outcome_token` stays live until
    turn_complete/interrupted). A queued proactive callback that passes every
    busy check in that window submits a second, unscoped Gemini turn.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._is_responding = False
    assert client.is_active_response() is False

    client._gemini_external_outcome_token = object()
    assert client.is_active_response() is True

    # 终结边缘落地后重新变空闲。
    client._gemini_external_outcome_token = None
    assert client.is_active_response() is False
    await client.close()
