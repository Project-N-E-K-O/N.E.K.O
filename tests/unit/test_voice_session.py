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


def _stt_only_client(model: str, *, api_type: str = ""):
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime",
        api_key="test-key",
        model=model,
        api_type=api_type,
    )
    ws = AsyncMock()
    client.ws = ws
    return client, ws


def _last_sent_json(ws):
    return json.loads(ws.send.call_args_list[-1][0][0])

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
@pytest.mark.parametrize(
    ("model", "expected_type"),
    [
        ("qwen3-omni-flash-realtime", "server_vad"),
        ("qwen3.5-omni-realtime", "semantic_vad"),
    ],
)
async def test_game_route_stt_only_qwen_disables_and_restores_auto_response(model, expected_type):
    client, ws = _stt_only_client(model, api_type="qwen")

    enabled = await client.set_game_route_stt_only(True)
    assert enabled is True
    msg = _last_sent_json(ws)
    assert msg["type"] == "session.update"
    assert msg["session"]["turn_detection"]["type"] == expected_type
    assert msg["session"]["turn_detection"]["create_response"] is False

    restored = await client.set_game_route_stt_only(False)
    assert restored is True
    msg = _last_sent_json(ws)
    assert msg["session"]["turn_detection"]["type"] == expected_type
    assert msg["session"]["turn_detection"]["create_response"] is True


@pytest.mark.unit
async def test_game_route_stt_only_openai_disables_and_restores_auto_response():
    client, ws = _stt_only_client("gpt-realtime-mini", api_type="openai")

    enabled = await client.set_game_route_stt_only(True)
    assert enabled is True
    msg = _last_sent_json(ws)
    audio_input = msg["session"]["audio"]["input"]
    assert audio_input["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert audio_input["turn_detection"]["type"] == "semantic_vad"
    assert audio_input["turn_detection"]["create_response"] is False

    restored = await client.set_game_route_stt_only(False)
    assert restored is True
    msg = _last_sent_json(ws)
    audio_input = msg["session"]["audio"]["input"]
    assert audio_input["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert audio_input["turn_detection"]["create_response"] is True


@pytest.mark.unit
async def test_game_route_stt_only_openai_api_type_does_not_require_gpt_model_name():
    client, ws = _stt_only_client("realtime-mini", api_type="openai")

    enabled = await client.set_game_route_stt_only(True)

    assert enabled is True
    msg = _last_sent_json(ws)
    audio_input = msg["session"]["audio"]["input"]
    assert audio_input["transcription"]["model"] == "gpt-4o-mini-transcribe"
    assert audio_input["turn_detection"]["create_response"] is False


@pytest.mark.unit
@pytest.mark.parametrize("model", ["glm-realtime-air", "step-audio-2", "free-model"])
async def test_game_route_stt_only_server_vad_providers_try_create_response_false(model):
    client, ws = _stt_only_client(model)

    enabled = await client.set_game_route_stt_only(True)
    assert enabled is True
    msg = _last_sent_json(ws)
    assert msg["session"]["turn_detection"] == {
        "type": "server_vad",
        "create_response": False,
    }

    restored = await client.set_game_route_stt_only(False)
    assert restored is True
    msg = _last_sent_json(ws)
    assert msg["session"]["turn_detection"] == {"type": "server_vad", "create_response": True}


@pytest.mark.unit
async def test_game_route_stt_only_provider_update_failure_falls_back_locally():
    client, ws = _stt_only_client("step-audio-2")
    ws.send.side_effect = RuntimeError("unsupported create_response")

    enabled = await client.set_game_route_stt_only(True)

    assert enabled is False
    assert client._game_route_stt_only is True
    assert client._game_route_stt_only_remote_applied is None


@pytest.mark.unit
async def test_game_route_stt_only_reconnect_reapplies_remote_update(monkeypatch):
    import main_logic.omni_realtime_client as realtime_mod

    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime",
        api_key="test-key",
        model="qwen3-omni-flash-realtime",
        api_type="qwen",
    )
    ws = AsyncMock()

    async def fake_connect(*_args, **_kwargs):
        return ws

    monkeypatch.setattr(realtime_mod.websockets, "connect", fake_connect)
    client._game_route_stt_only = True
    client._game_route_stt_only_remote_applied = True

    await client.connect("base realtime instructions")

    events = [json.loads(call_args[0][0]) for call_args in ws.send.call_args_list]
    turn_detection_updates = [
        event["session"]["turn_detection"]
        for event in events
        if event.get("type") == "session.update" and "turn_detection" in event.get("session", {})
    ]
    assert turn_detection_updates[-1]["create_response"] is False
    assert client._game_route_stt_only_remote_applied is True

    await client.close()
    assert client._game_route_stt_only_remote_applied is None


@pytest.mark.unit
async def test_game_route_stt_only_suppresses_local_model_output_callbacks():
    client, _ws = _stt_only_client("qwen3-omni-flash-realtime", api_type="qwen")
    text_delta_mock = AsyncMock()
    audio_delta_mock = AsyncMock()
    output_transcript_mock = AsyncMock()
    response_done_mock = AsyncMock()
    client.on_text_delta = text_delta_mock
    client.on_audio_delta = audio_delta_mock
    client.on_output_transcript = output_transcript_mock
    client.on_response_done = response_done_mock
    client._game_route_stt_only = True

    events = [
        json.dumps({"type": "response.created", "response": {"id": "resp_001"}}),
        json.dumps({"type": "response.text.delta", "delta": "ordinary"}),
        json.dumps({"type": "response.audio.delta", "delta": base64.b64encode(b"audio").decode("ascii")}),
        json.dumps({"type": "response.audio_transcript.delta", "delta": "spoken"}),
        json.dumps({"type": "response.audio_transcript.done", "transcript": "spoken"}),
        json.dumps({"type": "response.done", "response": {"id": "resp_001"}}),
    ]

    client.ws = AsyncMock()
    client.ws.__aiter__.return_value = events

    await client.handle_messages()

    text_delta_mock.assert_not_called()
    audio_delta_mock.assert_not_called()
    output_transcript_mock.assert_not_called()
    response_done_mock.assert_called_once()


@pytest.mark.unit
async def test_prompt_ephemeral_manual_qwen_uses_one_shot_instruction(monkeypatch):
    import main_logic.omni_realtime_client as realtime_mod

    client, ws = _stt_only_client("qwen3-omni-flash-realtime", api_type="qwen")
    client.instructions = "base realtime instructions"
    client._active_instructions = "base realtime instructions\n[足球小游戏赛后上下文]"
    monkeypatch.setattr(realtime_mod, "_load_proactive_audio", lambda _filename: b"\x00" * 320)

    delivered = await client.prompt_ephemeral(
        "下一句必须自然接刚才这局足球小游戏。",
        language="zh",
        qwen_manual_commit=True,
    )

    assert delivered is True
    events = [json.loads(call_args[0][0]) for call_args in ws.send.call_args_list]
    instruction_updates = [
        event["session"]["instructions"]
        for event in events
        if event.get("type") == "session.update" and "instructions" in event.get("session", {})
    ]
    assert instruction_updates[0].endswith("下一句必须自然接刚才这局足球小游戏。")
    assert instruction_updates[-1] == "base realtime instructions\n[足球小游戏赛后上下文]"
    assert client._active_instructions == "base realtime instructions\n[足球小游戏赛后上下文]"
    assert any(event.get("type") == "input_audio_buffer.commit" for event in events)
    assert any(event.get("type") == "response.create" for event in events)


@pytest.mark.unit
async def test_prompt_ephemeral_manual_qwen_restores_stt_only_turn_detection(monkeypatch):
    import main_logic.omni_realtime_client as realtime_mod

    client, ws = _stt_only_client("qwen3-omni-flash-realtime", api_type="qwen")
    monkeypatch.setattr(realtime_mod, "_load_proactive_audio", lambda _filename: b"\x00" * 320)

    enabled = await client.set_game_route_stt_only(True)
    assert enabled is True
    ws.send.reset_mock()

    delivered = await client.prompt_ephemeral(
        "下一句必须自然接刚才这局足球小游戏。",
        language="zh",
        qwen_manual_commit=True,
    )

    assert delivered is True
    events = [json.loads(call_args[0][0]) for call_args in ws.send.call_args_list]
    turn_detection_updates = [
        event["session"]["turn_detection"]
        for event in events
        if event.get("type") == "session.update" and "turn_detection" in event.get("session", {})
    ]
    assert turn_detection_updates[0] is None
    assert turn_detection_updates[-1]["create_response"] is False


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
    
    
    realtime_client.ws = AsyncMock()
    realtime_client.ws.__aiter__.return_value = events
    
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
