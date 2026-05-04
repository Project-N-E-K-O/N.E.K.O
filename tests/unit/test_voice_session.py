import pytest
import json
import base64
from unittest.mock import AsyncMock, MagicMock, patch

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


# ──────────────────────────────────────────────────────────────────────
# VAD MANUAL turn detection tests
# ──────────────────────────────────────────────────────────────────────
#
# These tests exercise the MANUAL branch added in the
# OmniRealtimeClient.connect() per-provider chain. For each provider we:
#   1. Construct the client with turn_detection_mode=MANUAL
#   2. Patch websockets.connect (websocket-based providers) or the
#      genai live SDK (Gemini)
#   3. Call connect() and capture the session config that was sent
#   4. Assert the manual-mode payload structure (turn_detection=null,
#      or for Gemini: realtime_input_config.automatic_activity_detection
#      .disabled=True)
#
# All tests bypass real API keys / models — they construct a stub client
# directly and only exercise connect() once the constructor has run with
# valid placeholder values.


def _make_manual_client(model: str, base_url: str = "wss://example.test/realtime", api_type: str = ""):
    """Construct a minimal OmniRealtimeClient with TurnDetectionMode.MANUAL.

    Skips dependency on real config — passes a valid model/base_url so the
    provider selector inside connect() picks the right branch.
    """
    return OmniRealtimeClient(
        base_url=base_url,
        api_key="sk-test",
        model=model,
        turn_detection_mode=TurnDetectionMode.MANUAL,
        api_type=api_type,
    )


async def _run_connect_and_capture_session(client):
    """Patch websockets.connect, run client.connect(), return the session
    dict from the captured session.update event.
    """
    captured: dict = {}

    async def fake_send(payload):
        try:
            event = json.loads(payload)
        except Exception:
            return
        if event.get("type") == "session.update":
            captured["session"] = event.get("session")

    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=fake_send)

    with patch("websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws
        try:
            await client.connect(instructions="You are helpful.", native_audio=True)
        finally:
            # GLM/free providers start a background silence-detection task in
            # connect(); without close() it lingers across tests and can cause
            # cross-test interference / pytest warnings. close() cancels the
            # task before returning.
            await client.close()

    return captured.get("session")


@pytest.mark.unit
async def test_connect_qwen_manual_vad_sends_null_turn_detection():
    """Qwen MANUAL: turn_detection=None, transcription model preserved."""
    client = _make_manual_client(model="qwen-omni-turbo-realtime", api_type="qwen")
    session = await _run_connect_and_capture_session(client)

    assert session is not None, "session.update event not captured"
    assert session.get("turn_detection") is None
    # Qwen's input_audio_transcription must remain pinned to gummy-realtime-v1
    assert session.get("input_audio_transcription") == {"model": "gummy-realtime-v1"}


@pytest.mark.unit
async def test_connect_openai_manual_vad_sends_null_audio_input_turn_detection():
    """OpenAI MANUAL: audio.input.turn_detection=None, transcription preserved."""
    client = _make_manual_client(
        model="gpt-realtime",
        base_url="wss://api.openai.com/v1/realtime",
        api_type="openai",
    )
    session = await _run_connect_and_capture_session(client)

    assert session is not None, "session.update event not captured"
    audio_input = session.get("audio", {}).get("input", {})
    assert audio_input.get("turn_detection") is None
    assert audio_input.get("transcription") == {"model": "gpt-4o-mini-transcribe"}


@pytest.mark.unit
async def test_connect_glm_manual_vad_sends_null_turn_detection():
    """GLM MANUAL: turn_detection=None (best-effort; may be rejected server-side)."""
    client = _make_manual_client(model="glm-realtime", api_type="glm")
    session = await _run_connect_and_capture_session(client)

    assert session is not None
    assert session.get("turn_detection") is None


@pytest.mark.unit
async def test_connect_step_manual_vad_sends_null_turn_detection():
    """Step MANUAL: turn_detection=None."""
    client = _make_manual_client(model="step-1o-audio", api_type="step")
    session = await _run_connect_and_capture_session(client)

    assert session is not None
    assert session.get("turn_detection") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "proxy_url",
    [
        "wss://lanlan.tech/realtime",  # StepFun proxy
        "wss://lanlan.app/realtime",   # Vertex Gemini proxy
    ],
)
async def test_connect_free_proxy_routes_manual_vad_per_backend(proxy_url):
    """Free MANUAL: both StepFun (lanlan.tech) and Vertex Gemini (lanlan.app)
    proxies receive turn_detection=None via the StepFun-shape websocket
    session config. Server-side translation happens at the proxy.
    """
    client = _make_manual_client(model="free-model", base_url=proxy_url, api_type="free")
    session = await _run_connect_and_capture_session(client)

    assert session is not None
    assert session.get("turn_detection") is None


@pytest.mark.unit
async def test_connect_gemini_manual_vad_disables_automatic_activity_detection():
    """Gemini MANUAL: realtime_input_config.automatic_activity_detection.disabled=True
    is added to the LiveConnectConfig passed into client.aio.live.connect(...).
    """
    pytest.importorskip("google.genai")

    client = _make_manual_client(
        model="gemini-2.0-flash-exp",
        base_url="https://generativelanguage.googleapis.com",
        api_type="gemini",
    )

    # Patch the genai.Client constructor so we capture the LiveConnectConfig
    # passed to client.aio.live.connect(). The connect() method returns an
    # async context manager; we mock both __aenter__ and __aexit__.
    captured: dict = {}

    fake_session = AsyncMock()
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_ctx.__aexit__ = AsyncMock(return_value=False)

    def fake_live_connect(*, model, config):
        captured["model"] = model
        captured["config"] = config
        return fake_ctx

    fake_genai_client = MagicMock()
    fake_genai_client.aio.live.connect = MagicMock(side_effect=fake_live_connect)

    with patch("main_logic.omni_realtime_client.genai") as mock_genai_module:
        mock_genai_module.Client = MagicMock(return_value=fake_genai_client)
        await client.connect(instructions="You are helpful.", native_audio=True)

    config = captured.get("config")
    assert config is not None, "Gemini live.connect was not called"
    rt_input = config.get("realtime_input_config")
    assert rt_input is not None, (
        "realtime_input_config missing — MANUAL mode must disable automatic VAD"
    )
    aad = getattr(rt_input, "automatic_activity_detection", None)
    assert aad is not None
    assert getattr(aad, "disabled", False) is True


# ──────────────────────────────────────────────────────────────────────
# VAD SERVER_VAD regression tests — ensure refactor preserved old behaviour
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_connect_qwen_server_vad_preserves_payload():
    """Sanity check: SERVER_VAD path still sends the structured turn_detection dict."""
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime",
        api_key="sk-test",
        model="qwen-omni-turbo-realtime",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type="qwen",
    )
    session = await _run_connect_and_capture_session(client)

    assert session is not None
    td = session.get("turn_detection")
    assert isinstance(td, dict)
    assert td.get("type") in ("server_vad", "semantic_vad")
    assert "threshold" in td


# ──────────────────────────────────────────────────────────────────────
# Regression: connect() must reset _has_server_vad to False in MANUAL
# mode for every provider that defaults to server-VAD. Otherwise
# stream_audio() and _check_silence_timeout() take the wrong branch
# (stale _last_speech_time, false GLM/free auto-close, mis-applied
# client-VAD suppression). Codex finding on PR #1128 (id 3181989081).
# ──────────────────────────────────────────────────────────────────────


# Provider matrix → (model, base_url, api_type, expected_default_has_vad).
# expected_default_has_vad is what __init__ would set for SERVER_VAD on the
# same constructor args; MANUAL must override to False regardless.
_VAD_PROVIDER_MATRIX = [
    # provider id, model, base_url, api_type, default_has_server_vad
    ("qwen", "qwen-omni-turbo-realtime", "wss://example.test/realtime", "qwen", True),
    ("openai", "gpt-realtime", "wss://api.openai.com/v1/realtime", "openai", True),
    ("glm", "glm-realtime", "wss://example.test/realtime", "glm", True),
    ("step", "step-1o-audio", "wss://example.test/realtime", "step", True),
    # lanlan.tech (China free, StepFun proxy) — has server VAD by default
    ("free_stepfun", "free-model", "wss://lanlan.tech/realtime", "free", True),
    # lanlan.app (international free, Vertex Gemini proxy) — __init__ already
    # treats this as client-VAD only (False), so MANUAL has nothing to flip.
    # Included to verify we don't accidentally re-enable server VAD.
    ("free_vertex", "free-model", "wss://lanlan.app/realtime", "free", False),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    # NOTE: parameter renamed from ``base_url`` to ``ws_url`` to avoid
    # collision with the session-scoped ``base_url`` fixture from
    # pytest-base-url (otherwise pytest raises ScopeMismatch).
    "provider_id,model,ws_url,api_type,default_has_vad",
    _VAD_PROVIDER_MATRIX,
    ids=[row[0] for row in _VAD_PROVIDER_MATRIX],
)
async def test_connect_manual_mode_resets_has_server_vad_for_all_providers(
    provider_id, model, ws_url, api_type, default_has_vad,
):
    """MANUAL mode must force _has_server_vad=False for every websocket
    provider, since connect() sends turn_detection=null and the provider
    will not emit speech_started/stopped events.

    Compares against the SERVER_VAD baseline to confirm the default
    matches the codebase's __init__ heuristic, then asserts MANUAL flips
    the flag to False post-connect().
    """
    # Baseline: SERVER_VAD client should keep the documented default.
    server_vad_client = OmniRealtimeClient(
        base_url=ws_url,
        api_key="sk-test",
        model=model,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type=api_type,
    )
    assert server_vad_client._has_server_vad is default_has_vad, (
        f"{provider_id}: SERVER_VAD default mismatch — fixture expectation "
        f"is stale; expected {default_has_vad}, got {server_vad_client._has_server_vad}"
    )

    # MANUAL: pre-connect the flag matches __init__ default; post-connect
    # it must be False regardless of provider default.
    manual_client = _make_manual_client(model=model, base_url=ws_url, api_type=api_type)
    assert manual_client._has_server_vad is default_has_vad, (
        f"{provider_id}: pre-connect baseline drift"
    )

    await _run_connect_and_capture_session(manual_client)

    assert manual_client._has_server_vad is False, (
        f"{provider_id}: connect() MANUAL path must reset _has_server_vad to "
        f"False so stream_audio/_check_silence_timeout pick the client-VAD "
        f"branch (codex review id 3181989081)"
    )


@pytest.mark.unit
async def test_connect_gemini_manual_mode_keeps_has_server_vad_false():
    """Gemini path: __init__ already sets _has_server_vad=False (since
    Gemini Live emits no speech_started/stopped). MANUAL path must not
    accidentally flip it back to True. This guards the symmetry of the
    fix across the websocket and Gemini connect paths.
    """
    pytest.importorskip("google.genai")

    client = _make_manual_client(
        model="gemini-2.0-flash-exp",
        base_url="https://generativelanguage.googleapis.com",
        api_type="gemini",
    )
    assert client._has_server_vad is False  # __init__ default for Gemini

    fake_session = AsyncMock()
    fake_ctx = AsyncMock()
    fake_ctx.__aenter__ = AsyncMock(return_value=fake_session)
    fake_ctx.__aexit__ = AsyncMock(return_value=False)
    fake_genai_client = MagicMock()
    fake_genai_client.aio.live.connect = MagicMock(return_value=fake_ctx)

    with patch("main_logic.omni_realtime_client.genai") as mock_genai_module:
        mock_genai_module.Client = MagicMock(return_value=fake_genai_client)
        await client.connect(instructions="hi", native_audio=True)

    assert client._has_server_vad is False


@pytest.mark.unit
async def test_connect_server_vad_mode_preserves_has_server_vad_default():
    """SERVER_VAD path must NOT touch _has_server_vad — provider defaults
    from __init__ heuristic carry through. Counter-test to the MANUAL
    override above.
    """
    client = OmniRealtimeClient(
        base_url="wss://example.test/realtime",
        api_key="sk-test",
        model="qwen-omni-turbo-realtime",
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        api_type="qwen",
    )
    assert client._has_server_vad is True

    await _run_connect_and_capture_session(client)

    assert client._has_server_vad is True, (
        "SERVER_VAD must not flip _has_server_vad — only MANUAL forces False"
    )
