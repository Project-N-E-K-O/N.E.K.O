"""Realtime proactive turns use text injection, never synthetic user audio."""

import json
import os
import sys
import asyncio
from unittest.mock import AsyncMock

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode
from main_logic.omni_realtime_client import _responses as responses_module
from main_logic.omni_realtime_client import _transport as transport_module


DUMMY_IMAGE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP/Z"


def _make_client(*, api_type: str = "free", model: str = "free-model"):
    client = OmniRealtimeClient(
        base_url="wss://www.lanlan.tech/api/v1/realtime",
        api_key="test-key",
        model=model,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type=api_type,
    )
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    return client


def _sent_events(client):
    return [
        json.loads(call_args[0][0])
        for call_args in client.ws.send.call_args_list
    ]


def _input_texts(events):
    texts = []
    for event in events:
        if event.get("type") != "conversation.item.create":
            continue
        for content in event.get("item", {}).get("content", []):
            if content.get("type") == "input_text":
                texts.append(content.get("text"))
    return texts


def _ack_pending_input_item(client, events):
    for event in events:
        if event.get("type") != "conversation.item.create":
            continue
        client._response_arbiter.notify_item_created({
            "type": "conversation.item.created",
            "item": event["item"],
        })


async def _prompt_and_complete(client, *args, **kwargs):
    task = asyncio.create_task(client.prompt_ephemeral(*args, **kwargs))
    for _ in range(20):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        if any(
            event.get("type") == "response.create"
            for event in events
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("prompt_ephemeral did not send response.create")
    client._sweep_inject_rejection_handlers()
    return await task


@pytest.mark.unit
async def test_prompt_ephemeral_injects_text_and_never_audio():
    client = _make_client()

    delivered = await _prompt_and_complete(client, language="zh")

    events = _sent_events(client)
    input_texts = _input_texts(events)
    assert delivered is True
    assert any("主动搭话触发" in text for text in input_texts)
    assert any("不要假设刚刚看到了新的画面或事件" in text for text in input_texts)
    assert not any("屏幕主动搭话触发" in text for text in input_texts)
    assert any(event.get("type") == "response.create" for event in events)
    assert not any(
        event.get("type") == "input_audio_buffer.append"
        for event in events
    )
    await client.close()


@pytest.mark.unit
async def test_prompt_ephemeral_selects_screen_prompt_when_visual_context_exists():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await _prompt_and_complete(client, language="zh")

    events = _sent_events(client)
    event_types = [event.get("type") for event in events]
    input_texts = _input_texts(events)
    assert delivered is True
    assert event_types.index("input_image_buffer.append") < event_types.index(
        "conversation.item.create"
    )
    assert any("屏幕主动搭话触发" in text for text in input_texts)
    assert any("画面中的具体内容" in text for text in input_texts)
    assert not any("不要假设刚刚看到了新的画面或事件" in text for text in input_texts)
    await client.close()


@pytest.mark.unit
async def test_free_prompt_sends_native_image_before_text():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await _prompt_and_complete(client, "describe what you notice")

    events = _sent_events(client)
    event_types = [event.get("type") for event in events]
    assert delivered is True
    assert event_types.index("input_image_buffer.append") < event_types.index(
        "conversation.item.create"
    )
    assert _input_texts(events) == ["describe what you notice"]
    assert client._proactive_image_consumed is True
    await client.close()


@pytest.mark.unit
async def test_server_vad_prompt_rotates_tts_sid_before_text_response():
    client = _make_client()
    client.on_sid_rotate = AsyncMock()

    delivered = await _prompt_and_complete(client, "start a new TTS turn")

    assert delivered is True
    client.on_sid_rotate.assert_awaited_once_with()
    await client.close()


@pytest.mark.unit
async def test_delayed_inject_rejection_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    async def reject_after_send(_text, *, on_rejected, on_completed):
        async def reject():
            # The rejection may arrive well after send_event() returned.
            await asyncio.sleep(0.02)
            on_rejected("response_already_active")

        asyncio.create_task(reject())

    client.inject_text_and_request_response = reject_after_send

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_failed_response_done_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    task = asyncio.create_task(
        client.prompt_ephemeral("describe what you notice")
    )
    for _ in range(20):
        events = _sent_events(client)
        _ack_pending_input_item(client, events)
        if any(
            event.get("type") == "response.create"
            for event in events
        ):
            break
        await asyncio.sleep(0)
    else:
        raise AssertionError("prompt_ephemeral did not send response.create")
    client._sweep_inject_rejection_handlers(
        error_msg="response.done status=cancelled",
    )

    assert await task is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_delivery_timeout_cancels_and_quarantines_until_lifecycle(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(
        responses_module,
        "_PROACTIVE_INJECT_DELIVERY_TIMEOUT_SECONDS",
        0.01,
    )

    delivered = await client.prompt_ephemeral("retry after timeout")

    assert delivered is False
    assert _sent_events(client)[-1]["type"] == "response.cancel"
    assert client._proactive_inject_awaiting_outcome is True
    assert client._inject_rejection_handlers
    assert client._inject_completion_handlers

    client._sweep_inject_rejection_handlers(
        error_msg="response.done status=cancelled",
    )
    assert client._proactive_inject_awaiting_outcome is False
    assert client._inject_rejection_handlers == {}
    assert client._inject_completion_handlers == {}
    await client.close()


@pytest.mark.unit
async def test_sync_inject_failure_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    async def fail_inject(_text, *, on_rejected, on_completed):
        raise RuntimeError("websocket disconnected")

    client.inject_text_and_request_response = fail_inject

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_prompt_skips_while_another_proactive_inject_awaits_outcome():
    client = _make_client()
    client._proactive_inject_awaiting_outcome = True

    delivered = await client.prompt_ephemeral("do not overlap")

    assert delivered is False
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_gemini_image_send_failure_preserves_snapshot_and_skips_text():
    client = _make_client(api_type="gemini", model="gemini-live")
    client._gemini_session = AsyncMock()
    client._gemini_session.send_realtime_input.side_effect = RuntimeError(
        "transient image send failure"
    )
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    client._gemini_session.send_client_content.assert_not_awaited()
    await client.close()


@pytest.mark.unit
async def test_oversized_native_image_drop_preserves_snapshot(monkeypatch):
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False
    monkeypatch.setattr(transport_module, "OMNI_WS_FRAME_LIMIT_BYTES", 1)
    monkeypatch.setattr(
        type(client),
        "_try_shrink_image_payload",
        staticmethod(lambda _event, _payload: None),
    )

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert not any(
        event.get("type") == "response.create"
        for event in _sent_events(client)
    )
    await client.close()


@pytest.mark.unit
async def test_standard_stepfun_uses_annotation_text_before_trigger():
    client = _make_client(api_type="step", model="step-realtime")
    client._image_recognized_this_turn = True
    client._image_description = "画面里有一只猫。"

    delivered = await _prompt_and_complete(client, "start a conversation")

    events = _sent_events(client)
    assert delivered is True
    assert _input_texts(events) == [
        "画面里有一只猫。",
        "start a conversation",
    ]
    assert not any(
        event.get("type") == "input_image_buffer.append"
        for event in events
    )
    await client.close()
