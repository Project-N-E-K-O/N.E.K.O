import asyncio
import json
import time
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.omni_realtime_client import (
    ImageStageResult,
    OmniRealtimeClient,
    TurnDetectionMode,
    VisualDeliveryMode,
)


DUMMY_IMAGE_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL"
    "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAA"
    "AAAAAACf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AE0A/9k="
)
VISUAL_CONTEXT_PREFIX = "[系统视觉感知结果，不是用户陈述]"
ASR_TRANSCRIPT_PREFIX = "[用户语音转写]"


def _make_qwen_client(
    *,
    turn_admission_lock: asyncio.Lock | None = None,
) -> OmniRealtimeClient:
    client = OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model="qwen-omni-turbo-realtime",
        api_type="qwen",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        turn_admission_lock=turn_admission_lock,
    )
    client.ws = AsyncMock()
    client._audio_in_buffer = True
    client._last_native_image_time = 0
    return client


def test_external_visual_join_and_fallback_windows_are_configurable():
    client = OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model="qwen-omni-turbo-realtime",
        api_type="qwen",
        external_visual_join_timeout=1.25,
        external_visual_frame_ttl=9.0,
    )

    assert client._external_visual_join_timeout == 1.25
    assert client._external_visual_frame_ttl == 9.0


def _wire_completed_response_transport(client: OmniRealtimeClient) -> list[dict]:
    sent: list[dict] = []

    async def send_event(_self, event, **_kwargs):
        copied = dict(event)
        sent.append(copied)
        arbiter = _self._response_arbiter
        if event["type"] == "conversation.item.create":
            if not event["item"]["id"].startswith("item_neko_visual_"):
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

    client.send_event = MethodType(send_event, client)
    client._response_arbiter._send_event = client.send_event
    return sent


def _event_text(event: dict) -> str:
    if event.get("type") != "conversation.item.create":
        return ""
    return event["item"]["content"][0].get("text", "")


@pytest.mark.asyncio
async def test_external_description_stages_qwen_frame_without_raw_provider_event():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._analyze_image_with_vision_model = AsyncMock()

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="screen-1",
    )

    assert isinstance(result, ImageStageResult)
    assert result.accepted is True
    assert result.mode == "external_description"
    assert result.generation == 1
    client.ws.send.assert_not_awaited()
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_external_description_rejects_frame_older_than_latest_ingress():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)

    newer = await client.stream_image(
        DUMMY_IMAGE_B64 + "newer",
        source="camera",
        request_id="camera-2",
        captured_at=20.0,
    )
    older = await client.stream_image(
        DUMMY_IMAGE_B64 + "older",
        source="screen",
        request_id="screen-1",
        captured_at=10.0,
    )

    assert newer.accepted is True
    assert older == ImageStageResult(
        accepted=False,
        mode="external_description",
        generation=newer.generation,
        rejection_reason="stale_frame",
    )
    assert client._latest_image_b64 == DUMMY_IMAGE_B64 + "newer"
    assert client._latest_image_source == "camera"
    assert client._latest_image_request_id == "camera-2"
    assert client._latest_image_captured_at == 20.0
    await client.close()


@pytest.mark.asyncio
async def test_external_description_rejects_oversized_callback_before_analysis():
    from utils.screenshot_utils import MAX_BASE64_SIZE

    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._analyze_image_with_vision_model = AsyncMock()

    result = await client.stream_image(
        "A" * (MAX_BASE64_SIZE + 1),
        source="callback",
        request_id="oversized-callback",
        bypass_rate_limit=True,
        cache_latest=False,
    )

    assert result == ImageStageResult(
        accepted=False,
        mode="external_description",
        generation=0,
        rejection_reason="payload_too_large",
    )
    client._analyze_image_with_vision_model.assert_not_awaited()
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bypass_rate_limit", [False, True])
async def test_raw_visual_guard_rejects_native_qwen_frame_without_provider_event(
    bypass_rate_limit,
):
    client = _make_qwen_client()
    client.block_raw_visual_delivery()

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="sync-failed-screen",
        bypass_rate_limit=bypass_rate_limit,
    )

    assert result == ImageStageResult(
        accepted=False,
        mode="native",
        generation=0,
        rejection_reason="raw_visual_delivery_blocked",
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_native_mode_noop_does_not_clear_independent_raw_visual_fence():
    client = _make_qwen_client()
    client.block_raw_visual_delivery()

    client.set_visual_delivery_mode(VisualDeliveryMode.NATIVE)

    assert client._raw_visual_delivery_blocked is True
    result = await client.stream_image(DUMMY_IMAGE_B64)
    assert result.rejection_reason == "raw_visual_delivery_blocked"
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_native_frame_rechecks_raw_fence_after_send_slot_wait():
    class _ObservedSemaphore(asyncio.Semaphore):
        def __init__(self):
            super().__init__(0)
            self.acquire_started = asyncio.Event()

        async def acquire(self):
            self.acquire_started.set()
            return await super().acquire()

    client = _make_qwen_client()
    send_semaphore = _ObservedSemaphore()
    client._send_semaphore = send_semaphore

    task = asyncio.create_task(
        client.stream_image(
            DUMMY_IMAGE_B64,
            source="screen",
            request_id="delayed-screen",
            bypass_rate_limit=True,
        )
    )
    await asyncio.wait_for(send_semaphore.acquire_started.wait(), timeout=0.5)

    client.block_raw_visual_delivery()
    send_semaphore.release()
    result = await task

    assert result == ImageStageResult(
        accepted=False,
        mode="native",
        generation=1,
        rejection_reason="raw_visual_delivery_blocked",
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_native_image_unsent_does_not_report_acceptance_or_start_throttle():
    client = _make_qwen_client()
    client.ws = None
    client._last_native_image_time = 0.0

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is False
    assert client._last_native_image_time == 0.0


@pytest.mark.asyncio
async def test_native_oversized_image_unsent_does_not_start_throttle():
    client = _make_qwen_client()
    client._last_native_image_time = 0.0

    result = await client.stream_image(
        "A" * 400_000,
        source="screen",
        bypass_rate_limit=False,
    )

    assert result.accepted is False
    assert client._last_native_image_time == 0.0
    await client.close()


@pytest.mark.asyncio
async def test_native_route_can_release_temporary_raw_visual_fence():
    client = _make_qwen_client()
    client.block_raw_visual_delivery()
    client.allow_raw_visual_delivery()

    result = await client.stream_image(DUMMY_IMAGE_B64, source="screen")

    assert result.accepted is True
    assert client.ws.send.await_count == 1
    await client.close()


@pytest.mark.asyncio
async def test_external_callback_bypass_analyzes_once_without_raw_provider_event():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._audio_in_buffer = False
    client._analyze_image_with_vision_model = AsyncMock(
        return_value="回调截图显示学习计时器已结束"
    )

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        request_id="callback-1",
        bypass_rate_limit=True,
        cache_latest=False,
    )

    assert isinstance(result, ImageStageResult)
    assert result.accepted is True
    assert result.mode == "external_description"
    assert result.description == "回调截图显示学习计时器已结束"
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_external_callback_empty_analysis_is_terminal_rejection():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._analyze_image_with_vision_model = AsyncMock(return_value="")

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        request_id="callback-empty-analysis",
        bypass_rate_limit=True,
        cache_latest=False,
    )

    assert result == ImageStageResult(
        accepted=False,
        mode="external_description",
        generation=0,
        rejection_reason="analysis_empty",
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_external_callback_transient_analysis_error_remains_retriable(
    monkeypatch,
):
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    monkeypatch.setattr(
        "utils.screenshot_utils.analyze_image_with_vision_model",
        AsyncMock(side_effect=RuntimeError("vision provider timeout")),
    )

    with pytest.raises(RuntimeError, match="vision provider timeout"):
        await client.stream_image(
            DUMMY_IMAGE_B64,
            source="callback",
            request_id="callback-transient-analysis",
            bypass_rate_limit=True,
            cache_latest=False,
        )

    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_external_proactive_snapshot_queues_description_without_raw_event():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client._analyze_image_with_vision_model = AsyncMock(
        return_value="屏幕上显示番茄钟结束提醒"
    )
    injected = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return SimpleNamespace()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="proactive-screen",
    )

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    visual_event = injected["events_before_text"][0]
    assert _event_text(visual_event) == (
        f"{VISUAL_CONTEXT_PREFIX}\n当前画面：屏幕上显示番茄钟结束提醒"
    )
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_proactive_sid_rotation_revalidates_visual_route_before_staging():
    client = _make_qwen_client()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._latest_image_generation = 7
    client._proactive_image_consumed = False
    client._analyze_image_with_vision_model = AsyncMock()

    async def rotate_to_external():
        client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)

    client.on_sid_rotate = rotate_to_external
    client.inject_text_and_request_response = AsyncMock()

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is False
    client._analyze_image_with_vision_model.assert_not_awaited()
    client.inject_text_and_request_response.assert_not_awaited()
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_proactive_sid_rotation_raw_fence_rejection_keeps_snapshot():
    client = _make_qwen_client()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._latest_image_generation = 8
    client._proactive_image_consumed = False

    async def rotate_into_blocked_route():
        client.block_raw_visual_delivery()

    client.on_sid_rotate = rotate_into_blocked_route
    client.inject_text_and_request_response = AsyncMock()

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is False
    assert client._proactive_image_consumed is False
    client.inject_text_and_request_response.assert_not_awaited()
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_external_proactive_transient_analysis_error_keeps_snapshot_for_retry():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client._analyze_image_with_vision_model = AsyncMock(
        side_effect=RuntimeError("vision provider timeout")
    )
    client.inject_text_and_request_response = AsyncMock()
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="proactive-transient-analysis",
    )

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is False
    assert client._proactive_image_consumed is False
    client.inject_text_and_request_response.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_external_proactive_visual_await_yields_to_independent_asr_turn():
    """A user turn that starts during vision analysis must preempt the nudge."""
    client = _make_qwen_client()
    client._is_gemini = True
    client._gemini_session = object()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    independent_turn_active = False

    async def analyze(_image_b64, *, update_turn_state=False):
        nonlocal independent_turn_active
        assert update_turn_state is False
        independent_turn_active = True
        return "屏幕上显示番茄钟结束提醒"

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    client.inject_text_and_request_response = AsyncMock()
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="proactive-independent-asr-race",
    )

    delivered = await client.prompt_ephemeral(
        "主动看看屏幕",
        user_turn_active=lambda: independent_turn_active,
    )

    assert delivered is False
    client.inject_text_and_request_response.assert_not_awaited()
    assert client._proactive_image_consumed is False
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_cancels_proactive_sdk_send_before_returning():
    """Independent ASR preempts a proactive inject parked inside SDK send."""
    client = _make_qwen_client()
    client._is_gemini = True
    client._gemini_session = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client.handle_interruption = AsyncMock()
    client.cancel_response = AsyncMock()
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_client_content(*_args, **_kwargs):
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise

    client._gemini_session.send_client_content.side_effect = send_client_content
    proactive_task = asyncio.create_task(client.prompt_ephemeral("主动提醒"))
    await send_started.wait()

    await client.prepare_external_voice_turn(turn_id="user-turn")

    assert send_cancelled.is_set()
    assert proactive_task.cancelled()
    assert client._gemini_proactive_submit_task is None
    client._settle_gemini_proactive_inject(notify=False)
    client.abandon_external_voice_turn("user-turn")
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_quarantines_completed_proactive_sdk_send(
    monkeypatch,
):
    """SDK-send success still owns the old Gemini session until terminal."""
    monkeypatch.setattr(
        "main_logic.omni_realtime_client._responses."
        "_GEMINI_PROACTIVE_CANCEL_GRACE_SECONDS",
        0,
    )
    client = _make_qwen_client()
    client._is_gemini = True
    old_session = AsyncMock()
    old_session.send_client_content = AsyncMock()
    old_context = AsyncMock()
    old_context.__aexit__ = AsyncMock()
    client._gemini_session = old_session
    client.ws = old_session
    client._gemini_context_manager = old_context
    client.instructions = "system prompt"
    client._native_audio = True
    client.handle_interruption = AsyncMock()
    client.cancel_response = AsyncMock()
    replacement_session = AsyncMock()

    async def reconnect(*_args, **_kwargs):
        client._connection_generation += 1
        client._gemini_session = replacement_session
        client.ws = replacement_session

    client.connect = AsyncMock(side_effect=reconnect)
    rejected = []

    await client.inject_text_and_request_response(
        "主动提醒",
        on_rejected=rejected.append,
    )

    assert client._gemini_proactive_submit_task is None
    assert client._gemini_proactive_outcome is not None
    assert client._is_responding is False

    reconnected = await client.prepare_external_voice_turn(
        turn_id="external-successor"
    )

    client.cancel_response.assert_awaited_once()
    old_context.__aexit__.assert_awaited_once_with(None, None, None)
    client.connect.assert_awaited_once_with("system prompt", native_audio=True)
    assert client._gemini_session is replacement_session
    assert client._gemini_proactive_outcome is None
    assert rejected == [
        "Gemini proactive turn was superseded by external voice input"
    ]
    assert reconnected is True
    client.abandon_external_voice_turn("external-successor")
    await client.close()


@pytest.mark.asyncio
async def test_gemini_close_cancels_and_joins_proactive_sdk_send():
    client = _make_qwen_client()
    client._is_gemini = True
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client.cancel_response = AsyncMock()
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_client_content(*_args, **_kwargs):
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise

    session = AsyncMock()
    session.send_client_content.side_effect = send_client_content
    context = AsyncMock()
    context.__aexit__ = AsyncMock()
    client._gemini_session = session
    client._gemini_context_manager = context
    client.ws = session
    proactive_task = asyncio.create_task(client.prompt_ephemeral("主动提醒"))
    await send_started.wait()

    await client._close_gemini()

    assert send_cancelled.is_set()
    assert proactive_task.cancelled()
    assert client._gemini_proactive_submit_task is None
    assert client._gemini_proactive_outcome is None
    client.cancel_response.assert_not_awaited()
    context.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.asyncio
async def test_external_proactive_failure_retires_only_the_failed_snapshot():
    """A terminally empty analysis is not retried forever on the same frame."""
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client._analyze_image_with_vision_model = AsyncMock(return_value="")

    async def inject_text(_text, **kwargs):
        kwargs["on_completed"]()
        return SimpleNamespace()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="failed-proactive-screen",
    )
    failed_generation = client._latest_image_generation

    assert await client.prompt_ephemeral("第一次主动看看屏幕") is True
    assert client._proactive_image_consumed is True
    assert client._latest_image_generation == failed_generation
    assert await client.prompt_ephemeral("第二次主动看看屏幕") is True
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    await client.close()


@pytest.mark.asyncio
async def test_external_proactive_description_registers_exact_rejection_event():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    client._analyze_image_with_vision_model = AsyncMock(return_value="屏幕描述")
    observed: dict[str, object] = {}

    async def inject_text(_text, **kwargs):
        visual_event = kwargs["events_before_text"][0]
        event_id = visual_event.get("event_id")
        observed["event_id"] = event_id
        observed["handler"] = client._inject_rejection_handlers.get(event_id)
        kwargs["on_completed"]()
        return SimpleNamespace()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)
    await client.stream_image(DUMMY_IMAGE_B64, source="screen", request_id="screen")

    assert await client.prompt_ephemeral("看屏幕") is True
    assert isinstance(observed["event_id"], str)
    assert callable(observed["handler"])
    await client.close()


@pytest.mark.asyncio
async def test_gemini_external_voice_turn_includes_visual_description():
    client = _make_qwen_client()
    client._is_gemini = True
    client._gemini_session = object()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client.create_response = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(return_value="桌上有一只白杯子")

    await client.prepare_external_voice_turn(turn_id="gemini-turn")
    await client.stream_image(DUMMY_IMAGE_B64, source="camera", request_id="camera")
    await client.submit_external_voice_turn("这是什么", turn_id="gemini-turn")

    client.create_response.assert_awaited_once_with(
        f"{VISUAL_CONTEXT_PREFIX}\n当前画面：桌上有一只白杯子\n"
        f"{ASR_TRANSCRIPT_PREFIX}\n这是什么"
    )
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_cancels_resolved_turn_during_sdk_send():
    """A superseded Gemini SDK send retires before its successor is admitted."""
    client = _make_qwen_client()
    client._is_gemini = True
    old_session = AsyncMock()
    client._gemini_session = old_session
    client.ws = old_session
    old_context = AsyncMock()
    client._gemini_context_manager = old_context
    client.instructions = "system prompt"
    client._native_audio = True
    replacement_session = AsyncMock()

    async def reconnect(*_args, **_kwargs):
        client._connection_generation += 1
        client._gemini_session = replacement_session
        client.ws = replacement_session

    client.connect = AsyncMock(side_effect=reconnect)
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(return_value="旧画面")
    send_started = asyncio.Event()
    release_send = asyncio.Event()

    async def send_client_content(*_args, **_kwargs):
        send_started.set()
        await release_send.wait()

    old_session.send_client_content.side_effect = send_client_content
    await client.prepare_external_voice_turn(turn_id="gemini-old-turn")
    await client.stream_image(DUMMY_IMAGE_B64, source="screen", request_id="old")
    old_submit = asyncio.create_task(
        client.submit_external_voice_turn("旧转写", turn_id="gemini-old-turn")
    )
    await send_started.wait()

    await client.prepare_external_voice_turn(turn_id="gemini-new-turn")
    await asyncio.sleep(0)
    cancelled_by_new_turn = old_submit.done()
    if not cancelled_by_new_turn:
        old_submit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await old_submit

    assert cancelled_by_new_turn is True
    assert "gemini-old-turn" not in client._external_visual_turns
    old_session.send_client_content.assert_awaited_once()
    old_context.__aexit__.assert_awaited_once_with(None, None, None)
    client.connect.assert_awaited_once_with("system prompt", native_audio=True)
    assert client._gemini_session is replacement_session
    client.abandon_external_voice_turn("gemini-new-turn")
    release_send.set()
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_quarantines_accepted_turn_before_first_content():
    """A returned SDK send still owns the session until Gemini terminates it."""
    client = _make_qwen_client()
    client._is_gemini = True
    old_session = AsyncMock()
    client._gemini_session = old_session
    client.ws = old_session
    old_context = AsyncMock()
    client._gemini_context_manager = old_context
    client.instructions = "system prompt"
    client._native_audio = True
    replacement_session = AsyncMock()

    async def reconnect(*_args, **_kwargs):
        client._connection_generation += 1
        client._gemini_session = replacement_session
        client.ws = replacement_session

    client.connect = AsyncMock(side_effect=reconnect)
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client.create_response = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="gemini-accepted-old")
    await client.submit_external_voice_turn(
        "已经被 Gemini 接受",
        turn_id="gemini-accepted-old",
    )

    assert client._gemini_external_submit_task is None
    assert client._gemini_external_outcome_token is not None
    assert client._is_responding is False

    reconnected = await client.prepare_external_voice_turn(
        turn_id="gemini-successor"
    )

    old_context.__aexit__.assert_awaited_once_with(None, None, None)
    client.connect.assert_awaited_once_with("system prompt", native_audio=True)
    assert client._gemini_session is replacement_session
    assert client._gemini_external_outcome_token is None
    assert reconnected is True
    client.abandon_external_voice_turn("gemini-successor")
    await client.close()


@pytest.mark.asyncio
async def test_retired_gemini_handler_cannot_settle_replacement_external_turn():
    client = _make_qwen_client()
    client._is_gemini = True
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
async def test_server_vad_receive_does_not_wait_for_turn_admission_boundary():
    admission_lock = asyncio.Lock()
    client = _make_qwen_client(turn_admission_lock=admission_lock)
    client.ws.__aiter__.return_value = [
        json.dumps({"type": "input_audio_buffer.speech_started"})
    ]

    await admission_lock.acquire()
    await client.handle_messages()

    assert client._speech_started_total == 1
    assert client._client_vad_active is True

    admission_lock.release()
    await client.close()


@pytest.mark.asyncio
async def test_native_audio_send_waits_for_turn_admission_boundary():
    admission_lock = asyncio.Lock()
    client = _make_qwen_client(turn_admission_lock=admission_lock)
    client.send_event = AsyncMock()

    loud_pcm = int(1000).to_bytes(2, "little", signed=True) * 512
    client._user_recent_activity_time = 0.0
    await admission_lock.acquire()
    send_task = asyncio.create_task(client.stream_audio(loud_pcm))
    await asyncio.sleep(0)

    client.send_event.assert_not_awaited()
    assert client._user_recent_activity_time == 0.0

    admission_lock.release()
    await send_task

    client.send_event.assert_awaited_once()
    assert client.send_event.await_args.args[0]["type"] == "input_audio_buffer.append"
    assert client._user_recent_activity_time > 0.0
    await client.close()


@pytest.mark.asyncio
async def test_queued_native_audio_uses_capture_timeline_for_client_vad():
    client = _make_qwen_client()
    client.send_event = AsyncMock()
    client._has_server_vad = False
    client._speech_sustain_threshold = 0.5
    client._client_vad_grace_period = 2.0
    client._client_vad_active = False
    client._speech_detect_start = 0.0
    client._audio_processor.noise_reduce_enabled = True
    client._audio_processor._denoiser = object()
    client._audio_processor._last_speech_prob = 0.9
    client.process_audio_chunk_async = AsyncMock(
        return_value=int(1000).to_bytes(2, "little", signed=True) * 160
    )
    loud_pcm = int(1000).to_bytes(2, "little", signed=True) * 480
    first_captured_at = time.time() - 5.0

    await client.stream_audio(loud_pcm, captured_at=first_captured_at)
    await client.stream_audio(loud_pcm, captured_at=first_captured_at + 1.0)

    assert client._speech_detect_start == first_captured_at
    assert client._client_vad_active is True
    assert client._client_vad_last_speech_time == first_captured_at + 1.0
    assert client._last_local_loud_time == first_captured_at + 1.0
    assert client._user_recent_activity_time > first_captured_at + 1.0
    await client.close()


@pytest.mark.asyncio
async def test_gemini_proactive_inject_folds_external_description_into_user_turn():
    client = OmniRealtimeClient.__new__(OmniRealtimeClient)
    client._fatal_error_occurred = False
    client._is_gemini = True
    client._gemini_session = object()
    client._gemini_send_user_turn = AsyncMock()
    visual_text = f"{VISUAL_CONTEXT_PREFIX}\n当前画面：番茄钟结束"
    visual_event = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": visual_text}],
        },
    }

    await OmniRealtimeClient.inject_text_and_request_response(
        client,
        "主动提醒用户",
        events_before_text=(visual_event,),
    )

    client._gemini_send_user_turn.assert_awaited_once_with(
        f"{visual_text}\n主动提醒用户"
    )


@pytest.mark.asyncio
async def test_external_voice_turn_analyzes_only_its_first_new_frame_once():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(
        return_value="桌面上放着一只白色杯子"
    )
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-1")
    first = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="camera",
        request_id="camera-1",
    )
    second = await client.stream_image(
        DUMMY_IMAGE_B64 + "newer",
        source="camera",
        request_id="camera-2",
    )
    ticket = await client.submit_external_text_turn("这是什么？", turn_id="turn-1")
    await ticket.done

    assert first.accepted is True
    assert second.accepted is True
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    assert [_event_text(event) for event in sent if _event_text(event)] == [
        (
            f"{VISUAL_CONTEXT_PREFIX}\n当前画面：桌面上放着一只白色杯子\n"
            f"{ASR_TRANSCRIPT_PREFIX}\n这是什么？"
        ),
    ]
    assert [event["type"] for event in sent].count("response.create") == 1
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("frame_age, expected_visual", [(0.1, True), (6.0, False)])
async def test_external_voice_turn_uses_only_ttl_fresh_fallback_frame(
    frame_age,
    expected_visual,
):
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(return_value="最近的屏幕")
    sent = _wire_completed_response_transport(client)

    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="fallback-screen",
    )
    client._latest_image_captured_at -= frame_age
    await client.prepare_external_voice_turn(turn_id="turn-fallback")
    ticket = await client.submit_external_text_turn(
        "看看刚才的画面", turn_id="turn-fallback"
    )
    await ticket.done

    item_texts = [_event_text(event) for event in sent if _event_text(event)]
    if expected_visual:
        assert item_texts == [
            (
                f"{VISUAL_CONTEXT_PREFIX}\n当前画面：最近的屏幕\n"
                f"{ASR_TRANSCRIPT_PREFIX}\n看看刚才的画面"
            ),
        ]
        client._analyze_image_with_vision_model.assert_awaited_once()
    else:
        assert item_texts == ["看看刚才的画面"]
        client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_stale_visual_generation_cannot_enter_a_new_external_turn():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    old_started = asyncio.Event()
    release_old = asyncio.Event()

    async def analyze(image_b64, *, update_turn_state=False):
        assert update_turn_state is False
        if image_b64 == "old-frame":
            old_started.set()
            await release_old.wait()
            return "旧画面"
        return "新画面"

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_result = await client.stream_image(
        "old-frame", source="screen", request_id="old"
    )
    await old_started.wait()
    client.abandon_external_voice_turn("turn-old")

    await client.prepare_external_voice_turn(turn_id="turn-new")
    new_result = await client.stream_image(
        "new-frame", source="screen", request_id="new"
    )
    release_old.set()
    ticket = await client.submit_external_text_turn("继续", turn_id="turn-new")
    await ticket.done

    assert old_result.generation < new_result.generation
    assert "旧画面" not in [_event_text(event) for event in sent]
    assert [_event_text(event) for event in sent if _event_text(event)] == [
        (
            f"{VISUAL_CONTEXT_PREFIX}\n当前画面：新画面\n"
            f"{ASR_TRANSCRIPT_PREFIX}\n继续"
        ),
    ]
    assert [event["type"] for event in sent].count("response.create") == 1
    await client.close()


@pytest.mark.asyncio
async def test_visual_join_timeout_submits_asr_text_without_placeholder():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._external_visual_join_timeout = 0.01
    analysis_started = asyncio.Event()
    never_finishes = asyncio.Event()

    async def analyze(_image_b64, *, update_turn_state=False):
        assert update_turn_state is False
        analysis_started.set()
        await never_finishes.wait()
        return "不应送达"

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-timeout")
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="timeout-frame",
    )
    await analysis_started.wait()
    ticket = await client.submit_external_text_turn(
        "只回答文字也可以", turn_id="turn-timeout"
    )
    await ticket.done

    item_texts = [_event_text(event) for event in sent if _event_text(event)]
    assert item_texts == ["只回答文字也可以"]
    assert [event["type"] for event in sent].count("response.create") == 1
    client.abandon_external_voice_turn("turn-timeout")
    await client.close()


@pytest.mark.asyncio
async def test_ambient_visual_error_falls_back_to_transcript_only():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(
        side_effect=RuntimeError("vision provider timeout")
    )
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-ambient-error")
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="ambient-error-frame",
    )
    ticket = await client.submit_external_text_turn(
        "只发送转写", turn_id="turn-ambient-error"
    )
    await ticket.done

    assert [_event_text(event) for event in sent if _event_text(event)] == [
        "只发送转写"
    ]
    await client.close()


@pytest.mark.asyncio
async def test_cancelled_visual_join_cancels_background_analysis_task():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    analysis_started = asyncio.Event()
    analysis_cancelled = asyncio.Event()

    async def analyze(_image_b64, *, update_turn_state=False):
        assert update_turn_state is False
        analysis_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            analysis_cancelled.set()
            raise

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    await client.prepare_external_voice_turn(turn_id="turn-cancel-join")
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="cancel-join-frame",
    )
    await analysis_started.wait()

    submit_task = asyncio.create_task(
        client.submit_external_text_turn("取消这一轮", turn_id="turn-cancel-join")
    )
    await asyncio.sleep(0)
    submit_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit_task
    await asyncio.sleep(0)

    assert analysis_cancelled.is_set()
    assert "turn-cancel-join" not in client._external_visual_turns
    await client.close()


@pytest.mark.asyncio
async def test_close_cancels_pending_external_visual_turn():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    analysis_started = asyncio.Event()
    analysis_cancelled = asyncio.Event()

    async def analyze(_image_b64, *, update_turn_state=False):
        assert update_turn_state is False
        analysis_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            analysis_cancelled.set()
            raise

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    await client.prepare_external_voice_turn(turn_id="turn-close")
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="close-frame",
    )
    await analysis_started.wait()

    await client.close()

    assert analysis_cancelled.is_set()
    assert client._external_visual_turns == {}


@pytest.mark.asyncio
async def test_new_external_turn_cancels_superseded_transcript_before_enqueue():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    analysis_started = asyncio.Event()

    async def analyze(_image_b64, *, update_turn_state=False):
        assert update_turn_state is False
        analysis_started.set()
        await asyncio.Event().wait()

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    sent = _wire_completed_response_transport(client)
    await client.prepare_external_voice_turn(turn_id="old-turn")
    await client.stream_image(DUMMY_IMAGE_B64, source="screen", request_id="old")
    await analysis_started.wait()
    old_submit = asyncio.create_task(
        client.submit_external_text_turn("旧转写", turn_id="old-turn")
    )
    await asyncio.sleep(0)

    await client.prepare_external_voice_turn(turn_id="new-turn")

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(old_submit, timeout=0.2)
    assert sent == []
    client.abandon_external_voice_turn("new-turn")
    await client.close()


@pytest.mark.asyncio
async def test_new_external_turn_cancels_resolved_turn_while_ticket_enqueue_returns():
    """A resolved visual turn stays cancellable until its transcript ticket is owned."""
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(return_value="旧画面")
    sent = _wire_completed_response_transport(client)
    arbiter = client._response_arbiter
    real_enqueue = arbiter.enqueue
    old_ticket_queued = asyncio.Event()
    release_enqueue_return = asyncio.Event()

    async def _enqueue_then_pause_return(*args, **kwargs):
        ticket = await real_enqueue(*args, **kwargs)
        old_ticket_queued.set()
        await release_enqueue_return.wait()
        return ticket

    arbiter.enqueue = _enqueue_then_pause_return
    await client.prepare_external_voice_turn(turn_id="resolved-old-turn")
    await client.stream_image(DUMMY_IMAGE_B64, source="screen", request_id="old")
    old_submit = asyncio.create_task(
        client.submit_external_text_turn("旧转写", turn_id="resolved-old-turn")
    )
    await old_ticket_queued.wait()

    await client.prepare_external_voice_turn(turn_id="new-turn")
    release_enqueue_return.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(old_submit, timeout=0.2)
    assert sent == []
    client.abandon_external_voice_turn("new-turn")
    await client.close()


@pytest.mark.asyncio
async def test_interruption_cannot_persist_visual_without_matching_asr_text():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(return_value="旧画面")
    item_send_started = asyncio.Event()
    release_item_send = asyncio.Event()
    sent_item_texts: list[str] = []

    async def send_event(_self, event, **_kwargs):
        if event["type"] == "conversation.item.create":
            sent_item_texts.append(_event_text(event))
            item_send_started.set()
            await release_item_send.wait()

    client.send_event = MethodType(send_event, client)
    client._response_arbiter._send_event = client.send_event
    await client.prepare_external_voice_turn(turn_id="turn-before-interrupt")
    await client.stream_image(DUMMY_IMAGE_B64, source="screen", request_id="old")
    submit_task = asyncio.create_task(
        client.submit_external_text_turn("旧语音", turn_id="turn-before-interrupt")
    )
    await item_send_started.wait()

    next_prepare = asyncio.create_task(
        client.prepare_external_voice_turn(turn_id="turn-after-interrupt")
    )
    await asyncio.sleep(0)
    release_item_send.set()
    await next_prepare
    with pytest.raises(RuntimeError, match="interrupted"):
        await submit_task

    assert sent_item_texts
    assert all(
        not text.startswith(VISUAL_CONTEXT_PREFIX)
        or f"{ASR_TRANSCRIPT_PREFIX}\n旧语音" in text
        for text in sent_item_texts
    )
    client.abandon_external_voice_turn("turn-after-interrupt")
    await client.close()


@pytest.mark.asyncio
async def test_delivery_mode_switch_discards_pending_external_visual_context():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    client.handle_interruption = AsyncMock()
    analysis_started = asyncio.Event()
    release_analysis = asyncio.Event()

    async def analyze(_image_b64, *, update_turn_state=False):
        assert update_turn_state is False
        analysis_started.set()
        await release_analysis.wait()
        return "切换前的画面"

    client._analyze_image_with_vision_model = AsyncMock(side_effect=analyze)
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-before-switch")
    await client.stream_image(
        DUMMY_IMAGE_B64,
        source="camera",
        request_id="before-switch",
    )
    await analysis_started.wait()
    client.set_visual_delivery_mode(VisualDeliveryMode.NATIVE)
    release_analysis.set()
    await asyncio.sleep(0)

    client.set_visual_delivery_mode(VisualDeliveryMode.EXTERNAL_DESCRIPTION)
    await client.prepare_external_voice_turn(turn_id="turn-after-switch")
    ticket = await client.submit_external_text_turn(
        "模式已经切换", turn_id="turn-after-switch"
    )
    await ticket.done

    assert [_event_text(event) for event in sent if _event_text(event)] == [
        "模式已经切换"
    ]
    assert [event["type"] for event in sent].count("response.create") == 1
    await client.close()


@pytest.mark.asyncio
async def test_native_delivery_mode_preserves_qwen_raw_image_transport():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.NATIVE)

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="native-screen",
    )

    assert isinstance(result, ImageStageResult)
    assert result.accepted is True
    assert result.mode == "native"
    events = [
        json.loads(call.args[0]) for call in client.ws.send.await_args_list
    ]
    assert [event["type"] for event in events] == ["input_image_buffer.append"]
    assert events[0]["image"] == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.asyncio
async def test_native_rate_limited_frame_is_not_reported_as_accepted():
    client = _make_qwen_client()
    client.set_visual_delivery_mode(VisualDeliveryMode.NATIVE)
    client._last_native_image_time = time.time()

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="rate-limited-screen",
    )

    assert result.accepted is False
    assert result.mode == "native"
    client.ws.send.assert_not_awaited()
    await client.close()
