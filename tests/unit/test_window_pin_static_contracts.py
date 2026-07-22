import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_text(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def assert_pin_precedes_minimize(
    source: str,
    path: str,
    minimize_token: str = 'data-neko-window-control="minimize"',
) -> None:
    pin_index = source.index('data-neko-window-control="pin"')
    minimize_index = source.index(minimize_token)
    assert pin_index < minimize_index, path


def test_shared_window_controls_bind_only_explicit_hidden_pin_buttons():
    script = read_text("static/js/window_controls.js")
    stylesheet = read_text("static/css/window_controls.css")
    asset_version_source = read_text("main_routers/pages_router.py")

    assert 'data-neko-window-control="pin"' in script
    assert 'querySelectorAll(`${CONTROL_SELECTOR}[data-neko-window-control="pin"]`)' in script
    assert "pinButtons.forEach" in script
    assert "typeof api.getPinState !== 'function'" in script
    assert "typeof api.togglePin !== 'function'" in script
    assert "PIN_STATE_RETRY_DELAYS_MS = [50, 150, 350, 750]" in script
    assert "function schedulePinStateRefreshRetry(generation, retryIndex)" in script
    assert "if (!normalizedState.available)" in script
    assert "window.addEventListener('focus', () => refreshPinState())" in script
    assert "pinButton.hidden = !available" in script
    assert "pinButton.classList.toggle('is-pinned', pinned)" in script
    assert "common.pinWindow" in script
    assert "common.unpinWindow" in script
    assert "data-neko-pin-label" in script
    assert "data-neko-unpin-label" in script
    assert ".neko-window-pin-icon" in stylesheet
    assert ".neko-window-control-btn.is-pinned" in stylesheet
    assert '[data-neko-window-control="pin"].is-pinned .neko-window-pin-icon' in stylesheet
    assert "@keyframes neko-window-pin-lock" in stylesheet
    assert "prefers-reduced-motion: reduce" in stylesheet
    assert '_PROJECT_ROOT / "static/css/window_controls.css"' in asset_version_source
    assert '_PROJECT_ROOT / "static/js/window_controls.js"' in asset_version_source
    assert not re.search(
        r'\.neko-window-control-btn\[data-neko-window-control="pin"\]\s*\{[^}]*\bcolor\s*:',
        stylesheet,
        re.DOTALL,
    ), "shared pin styles must not override page-specific icon colors"


def test_only_requested_top_level_templates_define_pin_controls():
    pin_templates = (
        "templates/voice_clone.html",
        "templates/api_key_settings.html",
        "templates/memory_browser.html",
        "templates/cookies_login.html",
        "templates/cloudsave_manager.html",
    )
    for path in pin_templates:
        source = read_text(path)
        assert 'data-neko-window-control="pin"' in source, path
        assert 'data-neko-window-control="pin" hidden' in source, path
        assert 'class="neko-window-pin-icon" aria-hidden="true"' in source, path
        assert_pin_precedes_minimize(source, path)

    character_manager = read_text("templates/character_card_manager.html")
    assert 'data-neko-window-control="pin" hidden' in character_manager
    assert 'class="neko-window-pin-icon" aria-hidden="true"' in character_manager
    assert_pin_precedes_minimize(
        character_manager,
        "templates/character_card_manager.html",
        'class="minimize-btn"',
    )

    openclaw_guide = read_text("templates/openclaw_guide.html")
    assert 'data-neko-window-control="pin" hidden' in openclaw_guide
    assert 'class="neko-window-pin-icon" aria-hidden="true"' in openclaw_guide
    assert "/static/css/window_controls.css" in openclaw_guide
    assert "/static/js/window_controls.js" in openclaw_guide
    assert_pin_precedes_minimize(
        openclaw_guide,
        "templates/openclaw_guide.html",
        'id="minimizeGuideBtn"',
    )

    for path in (
        "templates/card_maker.html",
        "templates/jukebox_manager.html",
        "templates/live2d_emotion_manager.html",
        "templates/vrm_emotion_manager.html",
        "templates/mmd_emotion_manager.html",
    ):
        assert 'data-neko-window-control="pin"' not in read_text(path), path


def test_pin_templates_version_shared_window_control_and_locale_assets():
    for path in (
        "templates/voice_clone.html",
        "templates/api_key_settings.html",
        "templates/character_card_manager.html",
        "templates/memory_browser.html",
        "templates/cookies_login.html",
        "templates/cloudsave_manager.html",
        "templates/jukebox.html",
        "templates/openclaw_guide.html",
    ):
        source = read_text(path)
        assert "/static/i18n-i18next.js?v={{ static_asset_version" in source, path
        assert "/static/css/window_controls.css?v={{ static_asset_version" in source, path
        assert "/static/js/window_controls.js?v={{ static_asset_version" in source, path

    routes = read_text("main_routers/pages_router.py")
    jukebox_route = re.search(
        r"async def get_jukebox_page\(request: Request\):(?P<body>[\s\S]*?)"
        r"(?=\n@router\.get)",
        routes,
    )
    assert jukebox_route
    assert "**_static_assets_ctx()" in jukebox_route.group("body")

    agent_routes = read_text("main_routers/agent_router.py")
    openclaw_route = re.search(
        r"async def openclaw_guide_page\(request: Request\):(?P<body>[\s\S]*?)"
        r"(?=\n@router\.get)",
        agent_routes,
    )
    assert openclaw_route
    assert "**_static_assets_ctx()" in openclaw_route.group("body")


def test_jukebox_has_an_explicit_pin_before_minimize_without_touching_manager():
    shell = read_text("static/jukebox/jukebox/shell.js")
    template = read_text("templates/jukebox.html")
    manager = read_text("static/jukebox/jukebox/manager.js")

    assert 'data-neko-window-control="pin" hidden' in shell
    assert 'class="jukebox-pin neko-window-control-btn"' in shell
    assert "color: rgba(45, 78, 104, 0.8);" in shell
    assert_pin_precedes_minimize(
        shell,
        "static/jukebox/jukebox/shell.js",
        'class="jukebox-minimize"',
    )
    assert "/static/css/window_controls.css" in template
    assert "/static/js/window_controls.js" in template
    assert "nekoWindowControls.init" in shell
    assert 'data-neko-window-control="pin"' not in manager


def test_chat_export_preview_uses_named_windows_and_pin_contract():
    script = read_text("static/app/app-chat-export.js")

    assert "exportPreviewAssetVersion = getCurrentExportAssetVersion()" in script
    assert "function getVersionedExportAssetUrl(path)" in script
    assert "getVersionedExportAssetUrl('/static/css/window_controls.css')" in script
    assert "getVersionedExportAssetUrl('/static/js/window_controls.js')" in script
    assert "script.src = getVersionedExportAssetUrl('/static/js/window_controls.js')" in script
    assert 'data-neko-window-control="pin"' in script
    assert "windowControls.appendChild(pinButton)" in script
    assert "windowControls.appendChild(minimizeButton)" in script
    assert script.index("windowControls.appendChild(pinButton)") < script.index(
        "windowControls.appendChild(minimizeButton)"
    )
    assert "neko_chat_export_preview_main_" in script
    assert "neko_chat_export_preview_child_" in script
    assert "window.open('', getExportPreviewWindowName('main')" in script
    assert "window.open('', getExportPreviewWindowName('child')" in script
    assert 'data-neko-window-control="pin" hidden' in script
    assert "function syncPinButtonLocale(button)" in script
    assert "button.setAttribute('data-neko-pin-label', pinLabel)" in script
    assert "button.setAttribute('data-neko-unpin-label', unpinLabel)" in script
    assert script.count("syncPinButtonLocale(pinButton)") >= 2
    assert "syncPinButtonLocale(modal.pinButton)" in script


def test_plugin_manager_pin_control_and_bridge_contract():
    component = read_text("frontend/plugin-manager/src/components/layout/AppLayout.vue")
    types = read_text("frontend/plugin-manager/env.d.ts")

    pin_button = component.index('class="titlebar-control titlebar-control--pin"')
    minimize_button = component.index('@click="minimizeWindow"')
    assert pin_button < minimize_button
    assert 'v-if="pinAvailable"' in component
    assert "api.getPinState" in component
    assert "api.togglePin" in component
    assert ':disabled="pinPending"' in component
    assert "if (pinPending.value) return" in component
    assert "PIN_STATE_RETRY_DELAYS_MS = [50, 150, 350, 750]" in component
    assert "function schedulePinStateRetry(generation: number, retryIndex: number)" in component
    assert "const generation = ++pinRequestGeneration" in component
    assert "generation === pinRequestGeneration" in component
    assert "clearPinStateRetry()" in component
    assert "pinDisposed = true" in component
    assert "pinAvailable.value = !!state.available" in component
    assert "isPinned.value = !!state.pinned" in component
    assert "@keyframes neko-plugin-pin-lock" in component
    assert "prefers-reduced-motion: reduce" in component
    assert "getPinState?:" in types
    assert "togglePin?:" in types
    assert "available?: boolean" in types
    assert "pinned?: boolean" in types


def test_pin_labels_exist_in_all_main_and_plugin_locales():
    i18n_bootstrap = read_text("static/i18n-i18next.js")
    assert "LOCALE_VERSION = '2026-07-22-window-pin-controls-i18n'" in i18n_bootstrap

    locale_names = (
        "en",
        "es",
        "ja",
        "ko",
        "pt",
        "ru",
        "zh-CN",
        "zh-TW",
    )
    for locale_name in locale_names:
        payload = json.loads(read_text(f"static/locales/{locale_name}.json"))
        assert payload["common"]["pinWindow"], locale_name
        assert payload["common"]["unpinWindow"], locale_name

    plugin_locale_names = (
        "en-US",
        "es",
        "ja",
        "ko",
        "pt",
        "ru",
        "zh-CN",
        "zh-TW",
    )
    for locale_name in plugin_locale_names:
        source = read_text(f"frontend/plugin-manager/src/i18n/locales/{locale_name}.ts")
        assert re.search(r"\bpinWindow\s*:\s*['\"].+['\"]", source), locale_name
        assert re.search(r"\bunpinWindow\s*:\s*['\"].+['\"]", source), locale_name
