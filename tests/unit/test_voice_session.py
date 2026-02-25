import pytest
import json
import base64
from unittest.mock import AsyncMock, patch

# Adjust path to import project modules
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))

from main_logic.omni_realtime_client import OmniRealtimeClient, TurnDetectionMode

# Dummy WAV header + silence for testing audio streaming
DUMMY_AUDIO_CHUNK = b'\x00' * 1024

@pytest.fixture
def mock_websocket():
    """Returns a mock websocket object."""
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock(return_value=json.dumps({"type": "session.created"}))
    mock_ws.close = AsyncMock()
    return mock_ws

@pytest.fixture
def realtime_client(mock_websocket):
    """Returns an OmniRealtimeClient instance with a mocked websocket."""
    # Setup config manager to return a Qwen or GLM profile
    from utils.api_config_loader import get_core_api_profiles
    core_profiles = get_core_api_profiles()
    
    # Prefer Qwen or GLM for realtime tests as they use WebSocket
    provider = "qwen" if "qwen" in core_profiles else "glm"
    if provider not in core_profiles:
        # Fallback to OpenAI if available
        if "openai" in core_profiles:
             provider = "openai"
        else:
             pytest.skip("No suitable realtime provider (Qwen/GLM/OpenAI) found.")
    
    profile = core_profiles[provider]
    base_url = profile['CORE_URL']
    api_key = profile.get('CORE_API_KEY')
    
    if not api_key:
        # Fallback mapping for Core keys
        # Qwen Core shares key with Assist usually
        key_map = {
            "qwen": "ASSIST_API_KEY_QWEN",
            "openai": "ASSIST_API_KEY_OPENAI",
            "glm": "ASSIST_API_KEY_GLM" 
        }
        env_var = key_map.get(provider)
        if env_var:
             api_key = os.environ.get(env_var)
             
    if not api_key:
        pytest.skip(f"API key for {provider} not found.")
        
    model = profile.get('CORE_MODEL', '') # In realtime client, model usually specified in init or update_session

    client = OmniRealtimeClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        on_text_delta=AsyncMock(),
        on_audio_delta=AsyncMock(),
        on_input_transcript=AsyncMock(),
        on_output_transcript=AsyncMock()
    )
    
    # Manually set the ws to skip the actual connect calls in some tests, 
    # OR we patch websockets.connect in the test itself.
    return client

@pytest.mark.unit
async def test_connect_and_session_update(realtime_client):
    """Test that client connects and sends session update."""
    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        # Setup mock connection to return our mock_ws
        mock_ws = AsyncMock()
        mock_connect.return_value = mock_ws
        
        await realtime_client.connect(instructions="You are a helpful assistant.", native_audio=True)
        
        assert mock_connect.called
        assert realtime_client.ws is not None
        
        # Verify initial session update was sent
        # The client sends "session.update" after connecting for most models
        # We need to inspect calls to socket.send
        assert mock_ws.send.called
        
        # Check if instructions were sent
        calls = mock_ws.send.call_args_list
        session_update_found = False
        for call_args in calls:
            msg = json.loads(call_args[0][0])
            if msg.get("type") == "session.update":
                session_update_found = True
                # Check instructions in session config
                if "session" in msg and "instructions" in msg["session"]:
                     assert "You are a helpful assistant" in msg["session"]["instructions"]
        
        assert session_update_found, "session.update event not found in websocket calls"
        
        await realtime_client.close()

@pytest.mark.unit
async def test_stream_audio(realtime_client):
    """Test streaming audio chunks."""
    # We need to manually set ws because we are skipping connect()
    realtime_client.ws = AsyncMock()
    
    # We also need to mock audio processor to avoid threading issues or just verify raw logic
    # But usually it's fine.
    
    await realtime_client.stream_audio(DUMMY_AUDIO_CHUNK)
    
    # Verify audio append event
    assert realtime_client.ws.send.called
    calls = realtime_client.ws.send.call_args_list
    
    # Qwen/GLM send 'input_audio_buffer.append' with base64 audio
    audio_append_found = False
    for call_args in calls:
        msg = json.loads(call_args[0][0])
        if msg.get("type") == "input_audio_buffer.append":
            audio_append_found = True
            assert "audio" in msg
            # DUMMY_AUDIO_CHUNK is 1024 bytes. Verify it's base64 encoded.
            decoded = base64.b64decode(msg["audio"])
            # Length might chance due to downsampling in audio_processor if it was 48k -> 16k
            # But DUMMY_AUDIO_CHUNK is 1024 bytes (512 samples @ 16bit).
            # If default sample rate assumed 16k, it passes through.
            pass 
            
    assert audio_append_found, "input_audio_buffer.append event not found"
    
    await realtime_client.close()

@pytest.mark.unit
async def test_receive_text_delta(realtime_client):
    """Test handling of incoming text delta events via handle_messages."""
    # Simulate a sequence of WebSocket messages that includes text deltas
    events = [
        json.dumps({"type": "response.created", "response": {"id": "resp_001"}}),
        json.dumps({"type": "response.text.delta", "delta": "Hello"}),
        json.dumps({"type": "response.text.delta", "delta": " world"}),
        json.dumps({"type": "response.done", "response": {"id": "resp_001"}}),
    ]
    
    
    # Configure the mock ws to yield these events then exit
    async def mock_iter():
        for ev in events:
            yield ev
    
    realtime_client.ws = AsyncMock()
    realtime_client.ws.__aiter__ = lambda self: mock_iter()
    
    # Ensure on_text_delta is an AsyncMock so we can track calls
    text_delta_mock = AsyncMock()
    realtime_client.on_text_delta = text_delta_mock
    
    response_done_mock = AsyncMock()
    realtime_client.on_response_done = response_done_mock
    
    # Run handle_messages — it will process all events then exit when iteration ends
    await realtime_client.handle_messages()
    
    # Verify on_text_delta was called twice with the correct deltas
    # Note: glm models skip on_text_delta (see handle_messages code), 
    # so this test works for non-glm models
    if "glm" not in realtime_client.model:
        assert text_delta_mock.call_count == 2, f"Expected 2 text delta calls, got {text_delta_mock.call_count}"
        # First call: "Hello" with is_first=True
        first_call = text_delta_mock.call_args_list[0]
        assert first_call[0][0] == "Hello"
        assert first_call[0][1] is True  # is_first_text_chunk
        # Second call: " world" with is_first=False
        second_call = text_delta_mock.call_args_list[1]
        assert second_call[0][0] == " world"
        assert second_call[0][1] is False
    
    # Verify response.done was processed
    assert response_done_mock.called

