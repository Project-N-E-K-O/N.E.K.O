import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

COMMON_TITLEBAR_TEMPLATES = (
    "voice_clone.html",
    "api_key_settings.html",
    "memory_browser.html",
    "card_maker.html",
    "cloudsave_manager.html",
    "cookies_login.html",
)

STATIC_LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")
PLUGIN_LOCALES = ("en-US", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")

WINDOW_CONTROL_TEMPLATES = (
    *COMMON_TITLEBAR_TEMPLATES,
    "character_card_manager.html",
    "jukebox.html",
    "jukebox_manager.html",
)


def test_every_pinnable_template_loads_the_shared_window_control_assets_once():
    for template_name in WINDOW_CONTROL_TEMPLATES:
        source = (PROJECT_ROOT / "templates" / template_name).read_text(encoding="utf-8")
        assert source.count("window_controls.css") == 1, template_name
        assert source.count("window_controls.js") == 1, template_name

    dynamic_controls = {
        "jukebox.html": PROJECT_ROOT / "static" / "jukebox" / "jukebox" / "shell.js",
        "jukebox_manager.html": PROJECT_ROOT / "static" / "jukebox" / "jukebox" / "manager.js",
    }
    for template_name, script_path in dynamic_controls.items():
        source = script_path.read_text(encoding="utf-8")
        assert source.count('data-neko-window-control="pin"') == 1, template_name
        assert 'aria-pressed="false" hidden' in source, template_name


def test_common_titlebars_put_the_pin_before_the_existing_window_controls():
    for template_name in COMMON_TITLEBAR_TEMPLATES:
        source = (PROJECT_ROOT / "templates" / template_name).read_text(encoding="utf-8")
        pin_index = source.index('data-neko-window-control="pin"')
        minimize_index = source.index('data-neko-window-control="minimize"', pin_index)
        assert pin_index < minimize_index, template_name
        assert 'aria-pressed="false" hidden' in source[pin_index:minimize_index]


def test_custom_titlebars_put_the_pin_before_the_existing_window_controls():
    character_manager = (PROJECT_ROOT / "templates" / "character_card_manager.html").read_text(encoding="utf-8")
    jukebox = (PROJECT_ROOT / "static" / "jukebox" / "jukebox" / "shell.js").read_text(encoding="utf-8")
    manager = (PROJECT_ROOT / "static" / "jukebox" / "jukebox" / "manager.js").read_text(encoding="utf-8")
    export_preview = (PROJECT_ROOT / "static" / "app" / "app-chat-export.js").read_text(encoding="utf-8")
    plugin_layout = (
        PROJECT_ROOT / "frontend" / "plugin-manager" / "src" / "components" / "layout" / "AppLayout.vue"
    ).read_text(encoding="utf-8")

    assert character_manager.index('data-neko-window-control="pin"') < character_manager.index('class="minimize-btn"')
    assert jukebox.index('data-neko-window-control="pin"') < jukebox.index('class="jukebox-minimize"')
    assert manager.index('data-neko-window-control="pin"') < manager.index('class="sam-close-btn"')

    controls_start = export_preview.index("if (isStandaloneWindow) {")
    controls_end = export_preview.index("windowControls.appendChild(maximizeButton);", controls_start)
    controls = export_preview[controls_start:controls_end]
    assert controls.index("'pin'") < controls.index("'minimize'")
    assert controls.index("windowControls.appendChild(pinButton);") < controls.index(
        "windowControls.appendChild(minimizeButton);"
    )

    document_chrome_start = export_preview.index("function buildWindowChromeHtml(title)")
    document_chrome_end = export_preview.index("function openExportDocumentWindow()", document_chrome_start)
    document_chrome = export_preview[document_chrome_start:document_chrome_end]
    assert document_chrome.index('data-neko-window-control="pin"') < document_chrome.index(
        'data-neko-window-control="minimize"'
    )

    template = plugin_layout[: plugin_layout.index("</template>")]
    assert template.index('@click="toggleAlwaysOnTop"') < template.index('@click="minimizeWindow"')


def test_common_window_control_script_uses_the_individual_topmost_bridge():
    script = (PROJECT_ROOT / "static" / "js" / "window_controls.js").read_text(encoding="utf-8")

    assert "api.getAlwaysOnTopState" in script
    assert "api.toggleAlwaysOnTop" in script
    assert "function getPinButtons()" in script
    assert "document.querySelectorAll" in script
    assert "getPinButtons().forEach((pinButton)" in script
    assert "pinButton.hidden = !allowed;" in script
    assert "pinButton.setAttribute('aria-pressed', pinned ? 'true' : 'false');" in script
    assert "pinned ? 'common.unpinWindow' : 'common.pinWindow'" in script
    assert "pinned ? '取消置顶' : '置顶窗口'" in script
    assert "typeof window.opener.t === 'function'" in script
    assert "translator.fn.call(translator.owner, key)" in script


def test_hidden_pin_controls_override_display_styles():
    styles = (PROJECT_ROOT / "static" / "css" / "window_controls.css").read_text(encoding="utf-8")

    assert '[data-neko-window-control="pin"][hidden]' in styles
    assert "display: none !important;" in styles


def test_pin_labels_are_kept_in_all_eight_locale_variants():
    for locale in STATIC_LOCALES:
        locale_path = PROJECT_ROOT / "static" / "locales" / f"{locale}.json"
        common = json.loads(locale_path.read_text(encoding="utf-8"))["common"]
        assert common["pinWindow"], locale
        assert common["unpinWindow"], locale

    plugin_locale_root = PROJECT_ROOT / "frontend" / "plugin-manager" / "src" / "i18n" / "locales"
    for locale in PLUGIN_LOCALES:
        source = (plugin_locale_root / f"{locale}.ts").read_text(encoding="utf-8")
        assert "pinWindow:" in source, locale
        assert "unpinWindow:" in source, locale
