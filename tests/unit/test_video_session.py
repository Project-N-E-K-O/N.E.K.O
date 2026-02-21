"""
test_video_session.py — OmniRealtimeClient video/screen streaming tests.

Tests that video (screen share / camera) image streaming works correctly
for different model providers (qwen, glm, gpt), including:
- Correct WebSocket event types per provider
- Image data encoding (base64)
- Rate limiting behavior for native image input
- Vision model fallback for models without native vision support
"""
import pytest
import json
from unittest.mock import AsyncMock

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode


# Dummy 1x1 pixel JPEG image in base64
DUMMY_IMAGE_B64 = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAAAAAAAACf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AE0A/9k="


def _make_client(model: str, supports_native_image: bool = True, base_url: str = "wss://test.example.com") -> OmniRealtimeClient:
    """Helper to create a test OmniRealtimeClient with mocked ws."""
    client = OmniRealtimeClient(
        base_url=base_url,
        api_key="test-key",
        model=model,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        on_text_delta=AsyncMock(),
        on_audio_delta=AsyncMock(),
        on_input_transcript=AsyncMock(),
        on_output_transcript=AsyncMock()
    )
    # Pre-set ws mock to skip connect()
    client.ws = AsyncMock()
    client._supports_native_image = supports_native_image
    client._audio_in_buffer = True  # Simulate active audio session
    client._last_native_image_time = 0  # Allow first image through
    return client


@pytest.mark.unit
async def test_qwen_image_streaming():
    """Test that Qwen models send images as input_image_buffer.append events."""
    client = _make_client("qwen-omni-turbo")
    
    await client.stream_image(DUMMY_IMAGE_B64)
    
    # Verify send was called with correct event
    assert client.ws.send.called
    calls = client.ws.send.call_args_list
    
    image_event_found = False
    for call_args in calls:
        msg = json.loads(call_args[0][0])
        if msg.get("type") == "input_image_buffer.append":
            image_event_found = True
            assert msg["image"] == DUMMY_IMAGE_B64
    
    assert image_event_found, "Expected input_image_buffer.append event for Qwen model"
    await client.close()


@pytest.mark.unit
async def test_glm_image_streaming():
    """Test that GLM models send images as append_video_frame events."""
    client = _make_client("glm-4-realtime")
    
    await client.stream_image(DUMMY_IMAGE_B64)
    
    assert client.ws.send.called
    calls = client.ws.send.call_args_list
    
    video_frame_found = False
    for call_args in calls:
        msg = json.loads(call_args[0][0])
        if msg.get("type") == "input_audio_buffer.append_video_frame":
            video_frame_found = True
            assert msg["video_frame"] == DUMMY_IMAGE_B64
    
    assert video_frame_found, "Expected input_audio_buffer.append_video_frame event for GLM model"
    await client.close()


@pytest.mark.unit
async def test_gpt_image_streaming():
    """Test that GPT models send images as conversation.item.create events."""
    client = _make_client("gpt-4o-realtime")
    
    await client.stream_image(DUMMY_IMAGE_B64)
    
    assert client.ws.send.called
    calls = client.ws.send.call_args_list
    
    image_msg_found = False
    for call_args in calls:
        msg = json.loads(call_args[0][0])
        if msg.get("type") == "conversation.item.create":
            item = msg.get("item", {})
            if item.get("role") == "user":
                content = item.get("content", [])
                for c in content:
                    if c.get("type") == "input_image":
                        image_msg_found = True
                        assert DUMMY_IMAGE_B64 in c["image_url"]
    
    assert image_msg_found, "Expected conversation.item.create with input_image for GPT model"
    await client.close()


@pytest.mark.unit
async def test_image_rate_limiting():
    """Test that native image input is rate-limited."""
    client = _make_client("qwen-omni-turbo")
    
    # Send first image — should go through (last_native_image_time = 0)
    await client.stream_image(DUMMY_IMAGE_B64)
    first_call_count = client.ws.send.call_count
    assert first_call_count > 0, "First image should be sent"
    
    # Immediately send second image — should be rate-limited (too soon)
    await client.stream_image(DUMMY_IMAGE_B64)
    second_call_count = client.ws.send.call_count
    assert second_call_count == first_call_count, "Second image should be rate-limited (sent too quickly)"
    
    await client.close()


@pytest.mark.unit
async def test_non_native_vision_fallback():
    """Test that models without native vision use VISION_MODEL fallback."""
    client = _make_client("step-realtime", supports_native_image=False)
    # Mark the image description as "analyzing" to trigger the vision model path
    client._image_description = "实时屏幕截图或相机画面正在分析中"
    
    # Mock the _analyze_image_with_vision_model method
    client._analyze_image_with_vision_model = AsyncMock()
    
    await client.stream_image(DUMMY_IMAGE_B64)
    
    # Should have called vision model fallback
    assert client._analyze_image_with_vision_model.called
    assert client._analyze_image_with_vision_model.call_args[0][0] == DUMMY_IMAGE_B64
    
    await client.close()
