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


def _make_qwen_client() -> OmniRealtimeClient:
    client = OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model="qwen-omni-turbo-realtime",
        api_type="qwen",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
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
async def test_raw_visual_guard_rejects_native_qwen_frame_without_provider_event():
    client = _make_qwen_client()
    client.block_raw_visual_delivery()

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="sync-failed-screen",
    )

    assert result == ImageStageResult(
        accepted=False,
        mode="external_description",
        generation=0,
    )
    client.ws.send.assert_not_awaited()
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
