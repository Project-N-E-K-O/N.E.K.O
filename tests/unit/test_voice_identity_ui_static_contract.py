from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return sum(
            coefficient * channel
            for coefficient, channel in zip(
                (0.2126, 0.7152, 0.0722), linear, strict=True
            )
        )

    lighter, darker = sorted(
        (luminance(foreground), luminance(background)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


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
    popup = (ROOT / "static/avatar/avatar-ui-popup.js").read_text(encoding="utf-8")

    assert '@router.get("/voice_identity", response_class=HTMLResponse)' in pages
    assert '"templates/voice_identity.html"' in pages
    assert "/voice_identity" in _literal_string_set(
        server, "_MAIN_LIMITED_MODE_ALLOWED_PAGE_PATHS"
    )
    assert "finalUrl.startsWith('/voice_identity')" in popup
    assert "windowName = 'neko_voice_identity'" in popup
    assert "icon: '/static/icons/voice_clone_icon.png'" in popup
    assert (ROOT / "static/icons/voice_clone_icon.png").is_file()
    assert "menuItem.setAttribute('role', 'button')" in popup
    assert "menuItem.tabIndex = 0" in popup
    assert "menuItem.addEventListener('keydown'" in popup
    assert "e.key !== 'Enter' && e.key !== ' '" in popup
    assert 'static/js/voice_identity.js' in pages
    assert 'static/css/voice_identity.css' in pages

    api_index = popup.index("id: 'api-keys'")
    identity_index = popup.index("id: 'voice-identity'")
    memory_index = popup.index("id: 'memory'")
    assert api_index < identity_index < memory_index


def test_settings_menu_icons_are_decorative_for_button_names() -> None:
    popup = (ROOT / "static/avatar/avatar-ui-popup.js").read_text(encoding="utf-8")
    menu_item = popup[
        popup.index("ManagerProto._createMenuItem = function") : popup.index(
            "ManagerProto._createSettingsMenuItems = function"
        )
    ]

    assert "iconImg.alt = '';" in menu_item
    assert "iconImg.setAttribute('aria-hidden', 'true')" in menu_item
    assert "iconImg.alt = item.label;" not in menu_item
    assert "menuItem.querySelector('img').alt" not in menu_item


def test_voice_identity_header_keeps_title_bounded() -> None:
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(encoding="utf-8")
    title = re.search(r"\.voice-identity-header h2\s*\{([^}]*)\}", stylesheet)
    title_layers = re.search(
        r"\.voice-identity-header h2::before,\s*"
        r"\.voice-identity-header h2::after\s*\{([^}]*)\}",
        stylesheet,
    )
    assert title is not None
    assert "min-width: 0" in title.group(1)
    assert "overflow: hidden" in title.group(1)
    assert "text-overflow: ellipsis" in title.group(1)
    assert title_layers is not None
    assert "overflow: hidden" in title_layers.group(1)
    assert "text-overflow: ellipsis" in title_layers.group(1)


def test_voice_identity_template_is_a_single_action_enrollment_flow() -> None:
    template = (ROOT / "templates/voice_identity.html").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(encoding="utf-8")

    assert '<title data-i18n="voiceIdentity.pageTitle">Owner 声纹</title>' in template
    assert 'class="voice-identity-shell container"' in template
    assert 'class="voice-identity-header container-header page-title-bar"' in template
    assert 'class="container-content"' in template
    assert 'data-neko-window-control="pin"' in template
    assert 'id="voice-identity-start"' in template
    assert 'data-i18n="voiceIdentity.enrollAndEnable"' in template
    assert 'id="voice-identity-capture-status" hidden' in template
    assert 'id="voice-identity-profile-controls"' in template
    assert 'aria-labelledby="voice-filter-title"' in template
    assert 'aria-describedby="voice-filter-help"' in template
    assert 'role="status" aria-live="polite" aria-atomic="true"' in template
    assert "step-progress" not in template
    assert "voice-identity-record" not in template
    assert "voice-identity-prompt" not in template
    assert "embedding" not in template.lower()
    assert "similarity" not in template.lower()

    assert ".switch input:focus-visible + .switch-track" in stylesheet
    assert "--voice-blue-dark: #075b80" in stylesheet
    assert "--voice-danger: #b4233b" in stylesheet
    assert "--voice-focus: #082f45" in stylesheet
    assert "--voice-focus: #8edcff" in stylesheet
    assert "outline: 3px solid var(--voice-focus)" in stylesheet
    assert _contrast_ratio("#075b80", "#f8fcff") >= 4.5
    assert _contrast_ratio("#b4233b", "#fff0f2") >= 4.5
    assert _contrast_ratio("#61798a", "#ffffff") >= 4.5
    assert '[data-theme="dark"]' in stylesheet
    assert "--voice-panel: rgba(27, 39, 48, 0.96)" in stylesheet
    assert "padding: 18px 24px" in stylesheet
    assert "linear-gradient(to right, #4bd4fd, #17a7ff)" in stylesheet
    assert "/static/js/voice_identity.js" in template
    assert "/static/css/voice_identity.css" in template


def test_voice_identity_enrollment_focus_target_is_programmatically_focusable() -> None:
    template = (ROOT / "templates/voice_identity.html").read_text(encoding="utf-8")
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(encoding="utf-8")

    assert 'id="voice-identity-enrollment-title" tabindex="-1"' in template
    assert "#voice-identity-enrollment-title:focus-visible" in stylesheet


def test_browser_capture_is_one_click_audio_worklet_pcm16_and_cancels_on_close() -> None:
    script = (ROOT / "static/js/voice_identity.js").read_text(encoding="utf-8")
    processor = (ROOT / "static/audio-processor.js").read_text(encoding="utf-8")

    for contract in (
        "navigator.mediaDevices.getUserMedia",
        "AudioContext",
        "AudioWorkletNode",
        "audioWorklet.addModule('/static/audio-processor.js')",
        "Int16Array",
        "TARGET_SAMPLE_RATE = 16000",
        "RECORDING_MS = 4000",
        "API_ROOT = '/api/voice-identity'",
        "'/enrollment/start'",
        "'/enrollment/profile'",
        "'/enrollment/cancel'",
        "'/profile'",
        "'/filter'",
        "X-Voice-Identity-Enrollment",
        "X-Voice-Identity-Profile",
        "audio/pcm;format=pcm_s16le;rate=16000;channels=1",
        "X-CSRF-Token",
        "window.nekoBeforeWindowClose",
        "pagehide",
        "keepalive: true",
    ):
        assert contract in script

    assert "RECORDING_MS + CAPTURE_TIMEOUT_GRACE_MS" in script
    assert "targetSamples = TARGET_SAMPLE_RATE * RECORDING_MS / 1000" in script
    assert "capturedSamples < targetSamples" in script
    assert "state.profileId || createProfileId()" in script
    assert "['has_profile', 'profile_available', 'available']" in script
    assert "const replacementConfirmed = uploadStarted" in script
    assert "state.profileRevision !== profileRevisionBefore" in script
    assert "MediaRecorder" not in script
    assert "createScriptProcessor" not in script
    assert "'/enrollment/segment'" not in script
    assert "'/enrollment/verify'" not in script
    assert "'/enrollment/commit'" not in script
    assert "fixedPrompts" not in script
    assert "ready_to_commit" not in script
    assert "embedding" not in script.lower()
    assert "similarity" not in script.lower()
    assert "window.addEventListener('localechange', render)" in script
    assert "needsLowPass = this.targetSampleRate < this.originalSampleRate" in processor
    assert "createLowPassFilter()" in processor
    assert "applyLowPassFilter(audioData)" in processor
    assert "const sourceData = this.applyLowPassFilter(audioData)" in processor
    assert "Array.from(" not in processor
    assert ".concat(" not in processor
    assert "resampleInputSize" not in processor
    assert "resampleBufferIndex" not in processor
    assert "extended.slice" not in processor
    assert "audioData[audioData.length - 1]" not in processor


def test_voice_identity_lowpass_is_causal_across_worklet_blocks() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for audio processor behavioural contract")
    script = textwrap.dedent(
        """
        const fs = require('fs');
        global.AudioWorkletProcessor = class {};
        global.registerProcessor = (_name, processorClass) => {
          global.AudioProcessor = processorClass;
        };
        eval(fs.readFileSync('static/audio-processor.js', 'utf8'));

        const processor = new global.AudioProcessor({
          processorOptions: {
            originalSampleRate: 48000,
            targetSampleRate: 16000,
          },
        });
        processor.lowPassTaps = new Float32Array([0, 0, 1]);
        processor.lowPassHistory = new Float32Array(2);
        processor.lowPassHistoryFilled = 0;

        const first = Array.from(
          processor.applyLowPassFilter(new Float32Array([1, 2]))
        );
        const second = Array.from(
          processor.applyLowPassFilter(new Float32Array([3, 4]))
        );

        if (JSON.stringify(first) !== JSON.stringify([0, 0])) {
          throw new Error(`first block used fabricated future samples: ${first}`);
        }
        if (JSON.stringify(second) !== JSON.stringify([1, 2])) {
          throw new Error(`second block did not consume prior history: ${second}`);
        }
        """
    )
    result: subprocess.CompletedProcess[str] = run_node_script(
        node,
        script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_voice_identity_resampler_preserves_phase_across_worklet_blocks() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for audio processor behavioural contract")
    script = textwrap.dedent(
        """
        const fs = require('fs');
        global.AudioWorkletProcessor = class {};
        global.registerProcessor = (_name, processorClass) => {
          global.AudioProcessor = processorClass;
        };
        eval(fs.readFileSync('static/audio-processor.js', 'utf8'));

        const processor = new global.AudioProcessor({
          processorOptions: {
            originalSampleRate: 44100,
            targetSampleRate: 16000,
          },
        });
        processor.applyLowPassFilter = (audioData) => audioData;

        const makeRamp = (start, length) => {
          const data = new Float32Array(length);
          for (let i = 0; i < length; i++) {
            data[i] = start + i;
          }
          return data;
        };

        const first = processor.resampleAudio(makeRamp(0, 1412));
        const second = processor.resampleAudio(makeRamp(1412, 1412));
        const expectedSecondFirst = 512 * 44100 / 16000;

        if (first.length !== 512) {
          throw new Error(`unexpected first block sample count: ${first.length}`);
        }
        if (second.length !== 513) {
          throw new Error(`unexpected second block sample count: ${second.length}`);
        }
        if (Math.abs(second[0] - expectedSecondFirst) > 0.0001) {
          throw new Error(
            `resampler restarted at ${second[0]} instead of ${expectedSecondFirst}`
          );
        }
        """
    )
    result: subprocess.CompletedProcess[str] = run_node_script(
        node,
        script,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_voice_identity_route_is_reserved_for_character_profiles() -> None:
    backend = (ROOT / "utils/character_name.py").read_text(encoding="utf-8")
    frontend = (
        ROOT / "static/js/character_card_manager/character-data-and-transfer.js"
    ).read_text(encoding="utf-8")
    backend_routes = backend.split("RESERVED_ROUTE_NAMES = frozenset({", 1)[1].split(
        "})", 1
    )[0]
    frontend_routes = frontend.split(
        "CHARACTER_PROFILE_RESERVED_ROUTE_NAMES = new Set([", 1
    )[1].split("]);", 1)[0]
    assert '"voice_identity"' in backend_routes
    assert "'voice_identity'" in frontend_routes


def test_all_locales_define_complete_voice_identity_copy() -> None:
    required = {
        "pageTitle",
        "title",
        "profileStatus",
        "localOnly",
        "privacyTitle",
        "privacyBody",
        "enrollAndEnable",
        "recording",
        "cancel",
        "delete",
        "reenroll",
        "filterLabel",
        "filterHelp",
        "recordingSeconds",
        "saving",
        "profileReady",
        "profileSavedDisabled",
        "profileMissing",
        "reasonDisabled",
        "reasonModelUnavailable",
        "reasonProfileIncompatible",
        "reasonSecureStorageUnavailable",
        "reasonEnrollmentActive",
        "reasonRuntimeDegraded",
        "reasonUnsupportedAsrRoute",
        "reasonShadowMode",
        "enrollmentComplete",
        "microphoneDenied",
        "requestFailed",
        "deleteConfirm",
    }
    removed_wizard_keys = {
        "fixedTitle",
        "fixedHelp",
        "fixedPrompts",
        "freeTitle1",
        "freeTitle2",
        "freePrompt1",
        "freePrompt2",
        "stepCount",
        "verificationPassed",
        "verificationRetry",
    }
    for locale in LOCALES:
        payload = json.loads(
            (ROOT / "static/locales" / f"{locale}.json").read_text(encoding="utf-8")
        )
        copy = payload["voiceIdentity"]
        assert required <= set(copy)
        assert removed_wizard_keys.isdisjoint(copy)
        assert all(isinstance(copy[key], str) and copy[key].strip() for key in required)
        assert payload["settings"]["menu"]["voiceIdentity"]


def test_locale_bootstrap_declares_a_non_empty_locale_cache_key() -> None:
    bootstrap = (ROOT / "static/i18n-i18next.js").read_text(encoding="utf-8")
    locale_version = re.search(r"const\s+LOCALE_VERSION\s*=\s*'([^']+)'", bootstrap)
    assert locale_version and locale_version.group(1).strip()
    assert locale_version.group(1) != "2026-08-07-credentials-console-guide"
