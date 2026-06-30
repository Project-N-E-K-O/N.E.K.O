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
    assert "finalizeAssistantSpeechTurn(normalizedTurnId, S.assistantTurnCompletionSource)" in mismatch_block
    assert "return true" in mismatch_block


def test_late_turn_completion_cleans_already_settled_turn_bookkeeping():
    source = _source()
    settled_guard_start = source.index(
        "if (normalizedTurnId && S.assistantTurnSettledId === normalizedTurnId)"
    )
    mismatch_start = source.index(
        "if (!normalizedTurnId || S.assistantTurnCompletedId !== normalizedTurnId)",
        settled_guard_start,
    )
    settled_guard_block = source[settled_guard_start:mismatch_start]

    assert "maybeFinalizeAssistantSpeech:skip_already_settled" in settled_guard_block
    assert "clearAssistantTurnCompletion()" in settled_guard_block
    assert "return true" in settled_guard_block
    assert "scheduleProactiveChat" not in settled_guard_block


def test_finalize_assistant_speech_turn_clears_completion_and_reschedules():
    source = _source()
    helper_start = source.index("function finalizeAssistantSpeechTurn(normalizedTurnId, completionSource)")
    maybe_start = source.index("function maybeFinalizeAssistantSpeech(turnId)", helper_start)
    helper_block = source[helper_start:maybe_start]

    assert "dispatchAssistantSpeechEnd(normalizedTurnId)" in helper_block
    assert "clearAssistantTurnCompletion()" in helper_block
    assert "S.assistantTurnSettledId = normalizedTurnId" in helper_block
    assert "scheduleProactiveChatAfterAssistantSpeech(completionSource)" in helper_block
