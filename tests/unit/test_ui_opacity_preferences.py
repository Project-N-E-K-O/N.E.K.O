import json
import re
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFERENCES_PATH = PROJECT_ROOT / "static" / "theme-manager.js"
POPUP_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
DARK_MODE_CSS_PATH = PROJECT_ROOT / "static" / "css" / "dark-mode.css"
CHAT_STYLES_PATH = PROJECT_ROOT / "frontend" / "react-neko-chat" / "src" / "styles.css"
INDEX_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "index.html"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"
LOCALES_PATH = PROJECT_ROOT / "static" / "locales"
LOCALES = ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es")
CHAT_OPACITY_FACTOR = "var(--neko-chat-opacity-factor, 1)"
FLOATING_MENU_OPACITY_FACTOR = "var(--neko-floating-menu-opacity-factor, 1)"


def _css_rule_body(source: str, selector: str) -> str:
    match = re.search(
        rf"^\s*{re.escape(selector)}\s*\{{(?P<body>.*?)^\s*\}}",
        source,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, f"missing CSS rule for {selector}"
    return match.group("body")


def _assert_css_properties_use_opacity(
    source: str,
    selector: str,
    properties: tuple[str, ...],
    opacity_factor: str,
) -> None:
    rule_body = _css_rule_body(source, selector)
    for property_name in properties:
        declaration = re.search(
            rf"^\s*{re.escape(property_name)}\s*:[^;]*;",
            rule_body,
            flags=re.DOTALL | re.MULTILINE,
        )
        assert declaration is not None, f"missing {property_name} in {selector}"
        assert opacity_factor in declaration.group(0), (
            f"{selector} {property_name} must use {opacity_factor}"
        )


def test_ui_opacity_preferences_restore_clamp_apply_and_sync() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the UI opacity browser contract test")

    script = textwrap.dedent(
        f"""
        const assert = require('node:assert/strict');
        const fs = require('node:fs');
        const vm = require('node:vm');

        const stored = new Map([
            ['nekoChatOpacity', '64'],
            ['nekoFloatingMenuOpacity', '   '],
        ]);
        const applied = new Map();
        const listeners = new Map();
        const dispatched = [];
        const localStorage = {{
            getItem(key) {{ return stored.has(key) ? stored.get(key) : null; }},
            setItem(key, value) {{ stored.set(key, String(value)); }},
        }};
        class CustomEvent {{
            constructor(type, init) {{ this.type = type; this.detail = init.detail; }}
        }}
        const document = {{
            readyState: 'loading',
            addEventListener() {{}},
            documentElement: {{
                style: {{ setProperty(name, value) {{ applied.set(name, value); }} }},
            }},
        }};
        const window = {{
            localStorage,
            document,
            CustomEvent,
            addEventListener(type, listener) {{ listeners.set(type, listener); }},
            dispatchEvent(event) {{ dispatched.push(event); }},
        }};

        const source = fs.readFileSync({json.dumps(str(PREFERENCES_PATH))}, 'utf8');
        vm.runInNewContext(source, {{ window, document, localStorage, CustomEvent, console, setTimeout, clearTimeout }}, {{ filename: 'theme-manager.js' }});

        const preferences = window.NekoUiOpacityPreferences;
        assert.equal(preferences.get('chat'), 64);
        assert.equal(preferences.get('floatingMenu'), 50);
        assert.equal(applied.get('--neko-chat-opacity-factor'), '1.28');
        assert.equal(applied.get('--neko-floating-menu-opacity-factor'), '1');

        assert.equal(preferences.set('floatingMenu', 37.6), 38);
        assert.equal(stored.get('nekoFloatingMenuOpacity'), '38');
        assert.equal(applied.get('--neko-floating-menu-opacity-factor'), '0.76');
        assert.equal(dispatched.at(-1).detail.name, 'floatingMenu');
        assert.equal(dispatched.at(-1).detail.value, 38);
        assert.equal(preferences.normalize(-10), 0);
        assert.equal(preferences.normalize(120), 100);
        assert.equal(preferences.normalize(''), 50);
        assert.equal(preferences.normalize('   '), 50);
        assert.equal(preferences.normalize(null), 50);
        assert.equal(preferences.normalize('not-a-number'), 50);
        assert.equal(preferences.normalize('0'), 0);

        preferences.set('chat', 100);
        assert.equal(applied.get('--neko-chat-opacity-factor'), '2');

        const storedChatOpacity = stored.get('nekoChatOpacity');
        const dispatchedCount = dispatched.length;
        assert.equal(preferences.apply('chat', 25), 25);
        assert.equal(applied.get('--neko-chat-opacity-factor'), '0.5');
        assert.equal(stored.get('nekoChatOpacity'), storedChatOpacity);
        assert.equal(dispatched.length, dispatchedCount);

        assert.equal(preferences.sync('chat', 75), 75);
        assert.equal(applied.get('--neko-chat-opacity-factor'), '1.5');
        assert.equal(stored.get('nekoChatOpacity'), '75');
        assert.equal(dispatched.length, dispatchedCount);

        listeners.get('storage')({{
            key: 'nekoChatOpacity',
            newValue: '25',
            storageArea: localStorage,
        }});
        assert.equal(applied.get('--neko-chat-opacity-factor'), '0.5');
        """
    )
    result = run_node_script(
        node,
        script,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_ui_opacity_has_all_surface_adapters_and_no_model_popup_controls() -> None:
    popup_source = POPUP_PATH.read_text(encoding="utf-8")
    assert "createSurfaceOpacitySettingsSidePanel" not in popup_source
    assert "settings.toggles.opacitySettings" not in popup_source

    theme_source = PREFERENCES_PATH.read_text(encoding="utf-8")
    assert "nekoChatOpacity" in theme_source
    assert "nekoFloatingMenuOpacity" in theme_source

    for template_path in (INDEX_TEMPLATE_PATH, CHAT_TEMPLATE_PATH):
        template_source = template_path.read_text(encoding="utf-8")
        assert "/static/theme-manager.js" in template_source
        assert "/static/ui-opacity-preferences.js" not in template_source

    chat_styles = CHAT_STYLES_PATH.read_text(encoding="utf-8")
    dark_mode_css = DARK_MODE_CSS_PATH.read_text(encoding="utf-8")
    chat_template = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    chat_surface_properties = {
        ".window-topbar": ("background",),
        ".chat-body": ("background",),
        ".composer-panel": ("background",),
        ".composer-input-shell": ("background",),
        ".composer-input-shell:focus-within": ("background",),
        ".compact-chat-surface-frame": (
            "--compact-chat-surface-bg",
            "--compact-chat-capsule-surface-bg",
            "--compact-chat-surface-edge-top",
            "--compact-chat-surface-edge-right",
            "--compact-chat-surface-edge-bottom",
            "--compact-chat-surface-edge-left",
            "--compact-chat-surface-glow",
            "--compact-chat-highlight-top",
            "--compact-chat-highlight-bottom",
            "--compact-chat-highlight-left",
            "--compact-chat-highlight-right",
            "--compact-chat-refraction-blue",
            "--compact-chat-refraction-violet",
            "--compact-chat-refraction-rose",
            "background-color",
        ),
        ".compact-chat-surface-frame::after": ("background-image",),
        '[data-theme="dark"] .window-topbar': ("background",),
        '[data-theme="dark"] .window-topbar::before': ("background",),
        '[data-theme="dark"] .chat-body': ("background",),
        '[data-theme="dark"] .composer-panel': ("background",),
        '[data-theme="dark"] .composer-input-shell': ("background",),
        '[data-theme="dark"] .composer-input-shell:focus-within': ("background",),
        '[data-theme="dark"] .compact-chat-surface-frame': (
            "--compact-chat-surface-bg",
            "--compact-chat-capsule-surface-bg",
            "--compact-chat-surface-edge-top",
            "--compact-chat-surface-edge-right",
            "--compact-chat-surface-edge-bottom",
            "--compact-chat-surface-edge-left",
            "--compact-chat-surface-glow",
            "--compact-chat-highlight-top",
            "--compact-chat-highlight-bottom",
            "--compact-chat-highlight-left",
            "--compact-chat-highlight-right",
            "--compact-chat-refraction-blue",
            "--compact-chat-refraction-violet",
            "--compact-chat-refraction-rose",
            "background",
        ),
    }
    for selector, properties in chat_surface_properties.items():
        _assert_css_properties_use_opacity(
            chat_styles,
            selector,
            properties,
            CHAT_OPACITY_FACTOR,
        )

    floating_menu_properties = (
        "--neko-popup-bg",
        "--neko-btn-bg",
        "--neko-btn-bg-hover",
        "--neko-btn-bg-active",
    )
    for selector in (":root", '[data-theme="dark"]'):
        _assert_css_properties_use_opacity(
            dark_mode_css,
            selector,
            floating_menu_properties,
            FLOATING_MENU_OPACITY_FACTOR,
        )

    electron_full_selector = (
        'body.neko-electron-runtime #react-chat-window-shell[data-chat-surface-mode="full"]'
        ":not(.is-minimized):not(.neko-e-collapsed)"
    )
    _assert_css_properties_use_opacity(
        chat_template,
        electron_full_selector,
        ("border-top", "border-left", "border-right", "border-bottom", "box-shadow"),
        CHAT_OPACITY_FACTOR,
    )
    _assert_css_properties_use_opacity(
        chat_template,
        f"{electron_full_selector}::before",
        ("box-shadow", "background"),
        CHAT_OPACITY_FACTOR,
    )
    _assert_css_properties_use_opacity(
        chat_template,
        f"{electron_full_selector}::after",
        ("background-image",),
        CHAT_OPACITY_FACTOR,
    )
    full_topbar_selector = f"{electron_full_selector} .window-topbar"
    for selector in (full_topbar_selector, f'[data-theme="dark"] {full_topbar_selector}'):
        _assert_css_properties_use_opacity(
            chat_template,
            selector,
            ("background",),
            CHAT_OPACITY_FACTOR,
        )
        topbar_rule = _css_rule_body(chat_template, selector)
        assert "0.3 * max(0, var(--neko-chat-opacity-factor, 1) - 1)" in topbar_rule

    for locale in LOCALES:
        messages = json.loads((LOCALES_PATH / f"{locale}.json").read_text(encoding="utf-8"))
        toggles = messages["settings"]["toggles"]
        assert "opacitySettings" not in toggles
        assert "chatOpacity" not in toggles
        assert "floatingMenuOpacity" not in toggles
