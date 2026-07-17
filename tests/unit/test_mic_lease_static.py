from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CAPTURE = ROOT / "static" / "app" / "app-audio-capture.js"
STATE = ROOT / "static" / "app" / "app-state.js"


def test_mic_lease_state_and_priority_are_explicit() -> None:
    state = STATE.read_text(encoding="utf-8")
    source = CAPTURE.read_text(encoding="utf-8")

    assert "micLeaseOwner: 'none'" in state
    assert "voiceInputLifecycleState: 'off'" in state
    priority = source.split("function resolveMicLeaseOwner()", 1)[1].split(
        "function refreshMicLease()", 1
    )[0]
    assert priority.index("!S.isRecording") < priority.index("S.isMicMuted")
    assert priority.index("S.isMicMuted") < priority.index("S.gameVoiceSttGateActive")
    assert "return MIC_LEASE.CORE" in priority


def test_worklet_upload_is_governed_by_one_mic_lease_gate() -> None:
    source = CAPTURE.read_text(encoding="utf-8")
    handler = source.split("S.workletNode.port.onmessage = (event) => {", 1)[1].split(
        "};", 1
    )[0]

    assert "canUploadOrdinaryMicFrame()" in handler
    assert "S.isMicMuted" not in handler
    assert "S.gameVoiceSttGateActive" not in handler
    assert "S.focusModeEnabled" not in handler


def test_stop_and_game_takeover_update_mic_lease() -> None:
    source = CAPTURE.read_text(encoding="utf-8")
    game_start = source.split("function startGameVoiceSttGate()", 1)[1].split(
        "function stopGameVoiceSttGate", 1
    )[0]
    stop = source.split("function stopRecording(options)", 1)[1].split(
        "function startMicVolumeVisualization", 1
    )[0]

    assert "setMicLeaseOwner(MIC_LEASE.GAME)" in game_start
    assert "refreshMicLease();" in stop


def test_mic_lease_projects_local_off_and_suspended_lifecycle_states() -> None:
    source = CAPTURE.read_text(encoding="utf-8")
    setter = source.split("function setMicLeaseOwner(owner)", 1)[1].split(
        "function resolveMicLeaseOwner()", 1
    )[0]

    assert "setVoiceInputLifecycleState('off')" in setter
    assert "setVoiceInputLifecycleState('suspended')" in setter
    assert "setVoiceInputLifecycleState('local_listen')" in setter


def test_mic_lease_changes_are_sent_to_backend_with_generation() -> None:
    source = CAPTURE.read_text(encoding="utf-8")

    assert "action: 'voice_input_control'" in source
    assert "lease_generation" in source
    assert "hard_mute" in source
    assert "hard_unmute" in source
    assert "game_takeover" in source
    assert "game_release" in source
    assert "focus_suppress" in source
    assert "focus_resume" in source


def test_worklet_uses_binary_pcm_frame_instead_of_json_sample_array() -> None:
    source = CAPTURE.read_text(encoding="utf-8")
    handler = source.split("S.workletNode.port.onmessage = (event) => {", 1)[1].split(
        "};", 1
    )[0]

    assert "new ArrayBuffer" in handler
    assert "setUint32(4, targetSampleRate, true)" in handler
    assert "Array.from(audioData)" not in handler
