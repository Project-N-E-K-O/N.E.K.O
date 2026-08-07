from __future__ import annotations

import ast
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCALES = ("zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [
            int(color[index : index + 2], 16) / 255
            for index in (1, 3, 5)
        ]
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
    popup = (ROOT / "static/avatar/avatar-ui-popup.js").read_text(
        encoding="utf-8"
    )
    menu_item = popup[
        popup.index("ManagerProto._createMenuItem = function"):
        popup.index("ManagerProto._createSettingsMenuItems = function")
    ]

    assert "iconImg.alt = '';" in menu_item
    assert "iconImg.setAttribute('aria-hidden', 'true')" in menu_item
    assert "iconImg.alt = item.label;" not in menu_item
    assert "menuItem.querySelector('img').alt" not in menu_item


def test_voice_identity_header_keeps_title_bounded() -> None:
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(
        encoding="utf-8"
    )

    title = re.search(
        r"\.voice-identity-header h2\s*\{([^}]*)\}", stylesheet
    )
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


def test_voice_identity_template_is_an_accessible_step_wizard() -> None:
    template = (ROOT / "templates/voice_identity.html").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(
        encoding="utf-8"
    )

    assert (
        '<title data-i18n="voiceIdentity.pageTitle">声纹注册面板（开发中）</title>'
        in template
    )
    assert (
        '<h2 data-i18n="voiceIdentity.pageTitle" '
        'data-text="声纹注册面板（开发中）">声纹注册面板（开发中）</h2>'
        in template
    )
    assert 'class="voice-identity-shell container"' in template
    assert 'class="voice-identity-header container-header page-title-bar"' in template
    assert 'class="container-content"' in template
    assert 'data-neko-window-control="pin"' in template
    assert 'id="voice-identity-step"' in template
    assert 'id="voice-identity-step-announcement"' in template
    assert 'role="status" aria-live="polite" aria-atomic="true"' in template
    assert 'id="voice-identity-record"' in template
    assert 'id="voice-identity-filter"' in template
    assert 'aria-labelledby="voice-filter-title"' in template
    assert 'aria-describedby="voice-filter-help"' in template
    assert ".switch input:focus-visible + .switch-track" in stylesheet
    assert "--voice-blue-dark: #075b80" in stylesheet
    assert "--voice-danger: #b4233b" in stylesheet
    assert "--voice-focus: #082f45" in stylesheet
    assert "--voice-focus: #8edcff" in stylesheet
    assert "outline: 3px solid var(--voice-focus)" in stylesheet
    assert _contrast_ratio("#075b80", "#f8fcff") >= 4.5
    assert _contrast_ratio("#b4233b", "#fff0f2") >= 4.5
    assert "--voice-muted: #61798a" in stylesheet
    assert _contrast_ratio("#61798a", "#ffffff") >= 4.5
    switch_block = stylesheet[
        stylesheet.index("\n.switch {"):
        stylesheet.index("}", stylesheet.index("\n.switch {"))
    ]
    assert "width: 54px" in switch_block
    assert "height: 30px" in switch_block
    assert "align-self: center" in switch_block
    assert "flex: 0 0 auto" in switch_block
    assert "flex: 0 0 54px" not in switch_block
    switch_track_start = stylesheet.index("\n.switch-track {")
    switch_track_block = stylesheet[
        switch_track_start:stylesheet.index("}", switch_track_start)
    ]
    assert "position: absolute" in switch_track_block
    assert "inset: 0" in switch_track_block
    switch_track_color = re.search(
        r"background:\s*(#[0-9a-fA-F]{6})", switch_track_block
    )
    assert switch_track_color is not None
    assert _contrast_ratio(switch_track_color.group(1), "#ffffff") >= 3
    checked_track = re.search(
        r"\.switch input:checked \+ \.switch-track\s*\{([^}]*)\}",
        stylesheet,
    )
    assert checked_track is not None
    checked_track_color = re.search(
        r"background:\s*(#[0-9a-fA-F]{6})", checked_track.group(1)
    )
    assert checked_track_color is not None
    assert _contrast_ratio(checked_track_color.group(1), "#ffffff") >= 3
    assert _contrast_ratio(checked_track_color.group(1), "#1b2730") >= 3
    assert '[data-theme="dark"]' in stylesheet
    assert "--voice-panel: rgba(27, 39, 48, 0.96)" in stylesheet
    dark_start = stylesheet.index('[data-theme="dark"] {')
    dark_block = stylesheet[
        dark_start:stylesheet.index("}", dark_start)
    ]
    dark_focus = re.search(
        r"--voice-focus:\s*(#[0-9a-fA-F]{6})", dark_block
    )
    dark_panel = re.search(
        r"--voice-panel:\s*rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
        dark_block,
    )
    assert dark_focus is not None
    assert dark_panel is not None
    dark_panel_hex = "#" + "".join(
        f"{int(channel):02x}" for channel in dark_panel.groups()
    )
    assert _contrast_ratio(dark_focus.group(1), dark_panel_hex) >= 3
    header_block = stylesheet[
        stylesheet.index(
            ".voice-identity-page .voice-identity-header {"
        ) : stylesheet.index(
            "}", stylesheet.index(".voice-identity-page .voice-identity-header {")
        )
    ]
    assert "padding: 18px 24px" in header_block
    assert "linear-gradient(to right, #4bd4fd, #17a7ff)" in header_block
    assert "position: relative" in header_block
    assert "box-shadow:" not in header_block
    assert "/static/js/voice_identity.js" in template
    assert "/static/css/voice_identity.css" in template
    assert "embedding" not in template.lower()
    assert "similarity" not in template.lower()


def test_voice_identity_idle_focus_target_is_programmatically_focusable() -> None:
    template = (ROOT / "templates/voice_identity.html").read_text(
        encoding="utf-8"
    )
    stylesheet = (ROOT / "static/css/voice_identity.css").read_text(
        encoding="utf-8"
    )

    assert 'id="voice-identity-step-title" tabindex="-1"' in template
    assert "#voice-identity-step-title:focus-visible" in stylesheet


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


def test_voice_identity_route_is_reserved_for_character_profiles() -> None:
    backend = (ROOT / "utils/character_name.py").read_text(encoding="utf-8")
    frontend = (
        ROOT
        / "static/js/character_card_manager/character-data-and-transfer.js"
    ).read_text(encoding="utf-8")

    backend_routes = backend.split(
        "RESERVED_ROUTE_NAMES = frozenset({", 1
    )[1].split("})", 1)[0]
    frontend_routes = frontend.split(
        "CHARACTER_PROFILE_RESERVED_ROUTE_NAMES = new Set([", 1
    )[1].split("]);", 1)[0]

    assert '"voice_identity"' in backend_routes
    assert "'voice_identity'" in frontend_routes


def test_all_locales_define_complete_voice_identity_copy() -> None:
    required = {
        "pageTitle",
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
        assert copy["pageTitle"].strip()
        assert copy["title"].strip()
        if locale == "zh-CN":
            assert copy["pageTitle"] == "声纹注册面板（开发中）"
            assert copy["title"] == "声纹注册面板"
        assert len(copy["fixedPrompts"]) == 3
        assert all(
            isinstance(prompt, str) and prompt.strip()
            for prompt in copy["fixedPrompts"]
        )
        if locale in {"en", "ru", "es", "pt"}:
            assert all(
                len(prompt.split()) <= 10
                for prompt in copy["fixedPrompts"]
            )
        else:
            assert all(
                len(re.sub(r"[^\w]", "", prompt, flags=re.UNICODE)) <= 24
                for prompt in copy["fixedPrompts"]
            )
        assert payload["settings"]["menu"]["voiceIdentity"]


def test_locale_bootstrap_declares_a_non_empty_locale_cache_key() -> None:
    bootstrap = (ROOT / "static/i18n-i18next.js").read_text(encoding="utf-8")

    # 只要求存在一个非空的 LOCALE_VERSION（locale 文件靠它做 cache-bust），
    # 不再钉死具体取值：钉死等于让每一次无关的版本串变更都打红这条用例。
    # tests/unit/test_window_pin_static_contracts.py 里的同族判据已经因为
    # 同样的原因退役过一次，这条是漏网的第二处。
    locale_version = re.search(r"const\s+LOCALE_VERSION\s*=\s*'([^']+)'", bootstrap)
    assert locale_version and locale_version.group(1).strip(), (
        "i18n-i18next.js 必须带一个非空的 LOCALE_VERSION 常量做 locale cache-bust"
    )
    assert locale_version.group(1) != "2026-08-07-credentials-console-guide"
