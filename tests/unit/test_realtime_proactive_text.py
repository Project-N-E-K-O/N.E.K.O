"""Realtime proactive turns use text injection, never synthetic user audio."""

import json
import os
import sys
import asyncio
from unittest.mock import AsyncMock

import pytest


sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode


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


@pytest.mark.unit
async def test_prompt_ephemeral_injects_text_and_never_audio():
    client = _make_client()

    delivered = await client.prompt_ephemeral(language="zh")

    events = _sent_events(client)
    assert delivered is True
    assert any("主动搭话触发" in text for text in _input_texts(events))
    assert any(event.get("type") == "response.create" for event in events)
    assert not any(
        event.get("type") == "input_audio_buffer.append"
        for event in events
    )
    await client.close()


@pytest.mark.unit
async def test_free_prompt_sends_native_image_before_text():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    delivered = await client.prompt_ephemeral("describe what you notice")

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
async def test_async_inject_rejection_returns_false_and_preserves_image():
    client = _make_client()
    client._latest_image_b64 = DUMMY_IMAGE_B64
    client._proactive_image_consumed = False

    async def reject_after_send(_text, *, on_rejected):
        async def reject():
            await asyncio.sleep(0)
            on_rejected("response_already_active")

        asyncio.create_task(reject())

    client.inject_text_and_request_response = reject_after_send

    delivered = await client.prompt_ephemeral("describe what you notice")

    assert delivered is False
    assert client._proactive_image_consumed is False
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    await client.close()


@pytest.mark.unit
async def test_standard_stepfun_uses_annotation_text_before_trigger():
    client = _make_client(api_type="step", model="step-realtime")
    client._image_recognized_this_turn = True
    client._image_description = "画面里有一只猫。"

    delivered = await client.prompt_ephemeral("start a conversation")

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
