from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_AUDIO_PLAYBACK_PATH = PROJECT_ROOT / "static" / "app-audio-playback.js"


def _source() -> str:
    return APP_AUDIO_PLAYBACK_PATH.read_text(encoding="utf-8")


def test_playback_gate_opens_when_audio_drains_before_turn_completion():
    source = _source()
    mismatch_start = source.index(
        "if (!normalizedTurnId || S.assistantTurnCompletedId !== normalizedTurnId)"
    )
    drained_guard_start = source.index(
        "if (!isAssistantTurnPlaybackDrained(normalizedTurnId))",
        mismatch_start,
    )
    mismatch_block = source[mismatch_start:drained_guard_start]

    assert "isAssistantTurnPlaybackDrained(normalizedTurnId)" in mismatch_block
    assert "dispatchAssistantSpeechEnd(normalizedTurnId)" in mismatch_block
    assert "return true" in mismatch_block
