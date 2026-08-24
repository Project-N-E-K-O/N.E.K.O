"""Static contracts for voice proactive no-response accounting."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_user_reset_during_pending_voice_proactive_is_preserved():
    proactive = (ROOT / "static/app/app-proactive.js").read_text(encoding="utf-8")
    state = (ROOT / "static/app/app-state.js").read_text(encoding="utf-8")

    assert "_voiceProactiveBackoffResetVersion: 0" in state
    assert "var voiceResetVersion = S._voiceProactiveBackoffResetVersion || 0;" in proactive
    assert (
        "(S._voiceProactiveBackoffResetVersion || 0) === voiceResetVersion"
        in proactive
    )
    assert (
        "S._voiceProactiveBackoffResetVersion =\n"
        "            (S._voiceProactiveBackoffResetVersion || 0) + 1;"
        in proactive
    )
