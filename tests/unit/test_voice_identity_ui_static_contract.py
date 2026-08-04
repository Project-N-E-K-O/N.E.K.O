from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def _literal_string_set(source: str, assignment_name: str) -> set[str]:
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == assignment_name
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Set):
            raise AssertionError(f"{assignment_name} must remain a set literal")
        return {
            element.value
            for element in node.value.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        }
    raise AssertionError(f"{assignment_name} not found")


def test_voice_identity_page_is_routed_and_available_in_settings_window() -> None:
    pages = (ROOT / "main_routers/pages_router.py").read_text(encoding="utf-8")
    server = (ROOT / "app/main_server/__init__.py").read_text(encoding="utf-8")
    popup = (ROOT / "static/avatar/avatar-ui-popup.js").read_text(
        encoding="utf-8"
    )

    assert '@router.get("/voice_identity", response_class=HTMLResponse)' in pages
    assert '"templates/voice_identity.html"' in pages
    assert "/voice_identity" in _literal_string_set(
        server,
        "_MAIN_LIMITED_MODE_ALLOWED_PAGE_PATHS",
    )
    assert "finalUrl.startsWith('/voice_identity')" in popup
    assert "windowName = 'neko_voice_identity'" in popup
    assert "icon: '/static/icons/mic_icon_off.png'" in popup
    assert (ROOT / "static/icons/mic_icon_off.png").is_file()

    api_index = popup.index("id: 'api-keys'")
    identity_index = popup.index("id: 'voice-identity'")
    memory_index = popup.index("id: 'memory'")
    assert api_index < identity_index < memory_index


def test_voice_identity_template_is_an_accessible_step_wizard() -> None:
    template = (ROOT / "templates/voice_identity.html").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(
        encoding="utf-8"
    )

    assert 'data-i18n="voiceIdentity.title"' in template
    assert 'id="voice-identity-step"' in template
    assert 'id="voice-identity-step-announcement"' in template
    assert 'role="status" aria-live="polite" aria-atomic="true"' in template
    assert 'id="voice-identity-record"' in template
    assert 'id="voice-identity-filter"' in template
    assert 'aria-labelledby="voice-filter-title"' in template
    assert 'aria-describedby="voice-filter-help"' in template
    assert ".switch input:focus-visible + .switch-track" in stylesheet
    assert '[data-theme="dark"]' in stylesheet
    assert "--voice-panel: rgba(27, 39, 48, 0.96)" in stylesheet
    assert "/static/js/voice_identity.js" in template
    assert "/static/css/voice_identity.css" in template
    assert "embedding" not in template.lower()
    assert "similarity" not in template.lower()


def test_browser_capture_is_fixed_pcm16_and_cancels_on_close() -> None:
    script = (ROOT / "static/js/voice_identity.js").read_text(encoding="utf-8")

    for contract in (
        "navigator.mediaDevices.getUserMedia",
        "AudioContext",
        "Int16Array",
        "TARGET_SAMPLE_RATE = 16000",
        "RECORDING_MS = 4000",
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
    assert "RECORDING_MS + CAPTURE_TIMEOUT_GRACE_MS" in script
    assert "new Error('incomplete_capture')" in script
    assert "maxSourceSamples - capturedSamples" in script
    assert "input.subarray(0, length)" in script
    assert "MAX_RECORDING_MS" not in script
    assert "if (sampleCount === 0)" in script
    assert "throw new Error('empty_capture')" in script
    assert script.count("error.name === 'NotAllowedError'") == 2
    assert script.count("error.name === 'NotFoundError'") == 2
    assert "state.stage === 'ready_to_commit'" in script
    assert "await commitEnrollment()" in script
    assert "window.addEventListener('localechange', render)" in script
    assert "elements.reenroll.disabled = !state.initialized || !isIdle" in script
    assert "elements.start.disabled = !state.initialized || state.busy" in script
    assert "state.initialized = true;\n            applyStatus(status)" in script
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


def test_voice_identity_locale_addition_bumps_locale_cache_key() -> None:
    bootstrap = (ROOT / "static/i18n-i18next.js").read_text(encoding="utf-8")

    assert "LOCALE_VERSION = '2026-08-04-voice-identity'" in bootstrap
