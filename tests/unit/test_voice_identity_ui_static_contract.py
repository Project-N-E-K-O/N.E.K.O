from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def test_voice_identity_page_is_routed_and_available_in_settings_window() -> None:
    pages = (ROOT / "main_routers/pages_router.py").read_text(encoding="utf-8")
    server = (ROOT / "app/main_server/__init__.py").read_text(encoding="utf-8")
    popup = (ROOT / "static/avatar/avatar-ui-popup.js").read_text(
        encoding="utf-8"
    )

    assert '@router.get("/voice_identity", response_class=HTMLResponse)' in pages
    assert '"templates/voice_identity.html"' in pages
    assert '"/voice_identity"' in server
    assert "finalUrl.startsWith('/voice_identity')" in popup
    assert "windowName = 'neko_voice_identity'" in popup

    api_index = popup.index("id: 'api-keys'")
    identity_index = popup.index("id: 'voice-identity'")
    memory_index = popup.index("id: 'memory'")
    assert api_index < identity_index < memory_index


def test_voice_identity_template_is_an_accessible_step_wizard() -> None:
    template = (ROOT / "templates/voice_identity.html").read_text(
        encoding="utf-8"
    )

    assert 'data-i18n="voiceIdentity.title"' in template
    assert 'id="voice-identity-step"' in template
    assert 'aria-live="polite"' in template
    assert 'id="voice-identity-record"' in template
    assert 'id="voice-identity-filter"' in template
    assert "/static/js/voice_identity.js" in template
    assert "/static/css/voice_identity.css" in template
    assert "embedding" not in template.lower()
    assert "similarity" not in template.lower()


def test_browser_capture_is_bounded_pcm16_and_cancels_on_close() -> None:
    script = (ROOT / "static/js/voice_identity.js").read_text(encoding="utf-8")

    for contract in (
        "navigator.mediaDevices.getUserMedia",
        "AudioContext",
        "Int16Array",
        "TARGET_SAMPLE_RATE = 16000",
        "MAX_RECORDING_MS = 8000",
        "API_ROOT = '/api/voice-identity'",
        "'/enrollment/start'",
        "'/enrollment/segment'",
        "'/enrollment/verify'",
        "'/enrollment/commit'",
        "'/enrollment/cancel'",
        "'/profile'",
        "'/filter'",
        "X-Voice-Identity-Enrollment",
        "X-CSRF-Token",
        "window.nekoBeforeWindowClose",
        "pagehide",
    ):
        assert contract in script
    assert "MediaRecorder" not in script
    assert "embedding" not in script.lower()
    assert "similarity" not in script.lower()


def test_all_locales_define_complete_voice_identity_copy() -> None:
    required = {
        "title",
        "privacyTitle",
        "privacyBody",
        "start",
        "record",
        "recording",
        "cancel",
        "retry",
        "delete",
        "reenroll",
        "filterLabel",
        "filterHelp",
        "fixedPrompts",
        "freePrompt1",
        "freePrompt2",
        "profileReady",
        "profileMissing",
        "persistenceUnavailable",
        "verificationPassed",
        "verificationRetry",
        "microphoneDenied",
        "requestFailed",
    }
    for locale in LOCALES:
        payload = json.loads(
            (ROOT / "static/locales" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        copy = payload["voiceIdentity"]
        assert required <= set(copy)
        assert len(copy["fixedPrompts"]) == 3
        assert payload["settings"]["menu"]["voiceIdentity"]
