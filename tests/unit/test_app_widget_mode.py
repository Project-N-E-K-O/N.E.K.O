import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_WIDGET_MODE_PATH = PROJECT_ROOT / "static" / "app" / "app-widget-mode.js"
AVATAR_UI_POPUP_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
INDEX_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "index.html"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the Widget Mode browser contract test")
    return subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def test_widget_mode_browser_client_syncs_minimal_state_and_event() -> None:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(APP_WIDGET_MODE_PATH))}, 'utf8');
        const listeners = new Map();
        const stateEvents = [];
        const fetchCalls = [];
        const notices = [];

        class CustomEventLike {{
          constructor(type, init = {{}}) {{
            this.type = type;
            this.detail = init.detail;
          }}
        }}

        const win = {{
          addEventListener(type, callback) {{
            if (!listeners.has(type)) listeners.set(type, []);
            listeners.get(type).push(callback);
          }},
          dispatchEvent(event) {{
            for (const callback of listeners.get(event.type) || []) callback(event);
          }},
          showStatusToast(message) {{ notices.push(message); }},
          t(key) {{ return key; }},
          nekoLocalMutationSecurity: {{
            peekCachedToken() {{ return 'csrf-token'; }},
          }},
        }};
        win.addEventListener('neko:widget-mode-state-changed', (event) => {{
          stateEvents.push(event.detail);
        }});

        const doc = {{
          readyState: 'complete',
          querySelectorAll() {{ return []; }},
        }};
        async function fetch(url, init = {{}}) {{
          fetchCalls.push({{ url, init }});
          const enabled = url === '/api/widget-mode/enabled';
          return {{
            ok: true,
            status: 200,
            async json() {{
              return {{ success: true, state: {{ enabled }} }};
            }},
          }};
        }}

        const context = {{
          window: win,
          document: doc,
          console,
          CustomEvent: CustomEventLike,
          fetch,
          Promise,
          Object,
          JSON,
        }};
        vm.createContext(context);
        vm.runInContext(source, context, {{ filename: 'app-widget-mode.js' }});

        const flush = async () => {{
          await new Promise((resolve) => setImmediate(resolve));
          await new Promise((resolve) => setImmediate(resolve));
        }};
        (async () => {{
          await flush();
          if (JSON.stringify(win.nekoWidgetMode.getState()) !== JSON.stringify({{
            enabled: false,
            backendState: {{ enabled: false }},
          }})) throw new Error('initial state is not minimal');

          const result = await win.nekoWidgetMode.setEnabled(true);
          await flush();
          if (result !== true || win.nekoWidgetMode.isEnabled() !== true) {{
            throw new Error('enabled state did not update');
          }}
          const keys = Object.keys(win.nekoWidgetMode).sort().join(',');
          if (keys !== 'getState,isEnabled,refreshState,setEnabled') {{
            throw new Error('unexpected public API: ' + keys);
          }}
          const mutation = fetchCalls.find((call) => call.url === '/api/widget-mode/enabled');
          if (!mutation || mutation.init.headers['X-CSRF-Token'] !== 'csrf-token') {{
            throw new Error('mutation security header missing');
          }}
          if (mutation.init.body !== '{{"enabled":true}}') {{
            throw new Error('unexpected mutation payload');
          }}
          if (!stateEvents.some((state) => state.enabled === false)
              || !stateEvents.some((state) => state.enabled === true)) {{
            throw new Error('state event did not cover both states');
          }}
          if (notices.length !== 1 || notices[0] !== '贴边探身 Beta 已开启。') {{
            throw new Error('toggle notice did not use the local fallback');
          }}
          console.log('Widget Mode minimal browser client passed');
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, (
        "node harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "Widget Mode minimal browser client passed" in result.stdout


def test_widget_mode_browser_client_has_no_lifecycle_or_polling_protocol() -> None:
    source = APP_WIDGET_MODE_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "nekoWidgetModeHost",
        "setInterval",
        "/api/widget-mode/windows",
        "/api/widget-mode/compaction",
        "/api/widget-mode/renderer-suspension",
        "handleLifecycleMessage",
        "cancelActiveModelLoadForWidgetMode",
        "widget_mode_compaction",
    ):
        assert forbidden not in source


def test_app_widget_mode_is_home_only_and_versioned() -> None:
    from main_routers import pages_router

    index_source = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    chat_source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert '/static/app/app-widget-mode.js?v={{ static_asset_version }}' in index_source
    assert '/static/app/app-widget-mode.js?v={{ static_asset_version }}' not in chat_source
    assert APP_WIDGET_MODE_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS


def test_widget_mode_status_keys_are_removed_from_all_web_locales() -> None:
    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        payload = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        widget_mode = payload["settings"]["widgetMode"]
        assert "statusOn" not in widget_mode
        assert "statusOff" not in widget_mode
        assert "enabledNotice" in widget_mode
        assert "disabledNotice" in widget_mode


def test_widget_mode_uses_approved_localized_names_and_fallbacks() -> None:
    expected_names = {
        "en": "Widget Mode Beta",
        "ja": "エッジのぞき Beta",
        "ko": "가장자리 내다보기 Beta",
        "zh-CN": "贴边探身 Beta",
        "zh-TW": "貼邊探身 Beta",
        "ru": "Выглядывание из-за края Beta",
        "pt": "Espiar pela borda Beta",
        "es": "Asomarse por el borde Beta",
    }
    legacy_names = {
        "en": "Edge Dock Mode",
        "ja": "エッジドックモード",
        "ko": "가장자리 도킹 모드",
        "zh-CN": "挂边模式",
        "zh-TW": "掛邊模式",
        "ru": "Режим крепления к краю",
        "pt": "Modo de encaixe na borda",
        "es": "Modo de acoplamiento al borde",
    }

    for locale, expected_name in expected_names.items():
        payload = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        name_root = expected_name.removesuffix(" Beta")
        widget_mode = payload["settings"]["widgetMode"]
        toggles = payload["settings"]["toggles"]
        visible_copy = json.dumps(
            {
                "widgetMode": widget_mode,
                "label": toggles["widgetMode"],
                "tooltip": toggles["widgetModeTooltip"],
            },
            ensure_ascii=False,
        )

        assert toggles["widgetMode"] == expected_name
        assert name_root in toggles["widgetModeTooltip"]
        assert expected_name in widget_mode["enabledNotice"]
        assert expected_name in widget_mode["disabledNotice"]
        assert expected_name in widget_mode["toggleFailed"]
        assert legacy_names[locale] not in visible_copy

    app_source = APP_WIDGET_MODE_PATH.read_text(encoding="utf-8")
    avatar_source = AVATAR_UI_POPUP_PATH.read_text(encoding="utf-8")
    assert "贴边探身 Beta 已开启。" in app_source
    assert "贴边探身 Beta 已关闭。" in app_source
    assert "贴边探身 Beta 切换失败，请稍后重试。" in app_source
    assert "贴边探身 Beta" in avatar_source
    assert "function translateWidgetModeText(key, fallback)" in avatar_source
    assert "window.t(key, { defaultValue: fallback })" in avatar_source
    assert "挂边模式" not in app_source
    assert "挂边模式" not in avatar_source


def test_widget_mode_toggle_mutation_stays_serialized_by_settings_ui() -> None:
    source = AVATAR_UI_POPUP_PATH.read_text(encoding="utf-8")

    assert "function queueWidgetModeMutation(operation)" in source
    assert "return queueWidgetModeMutation(function ()" in source
    assert ".then(function () { return window.nekoWidgetMode.setEnabled(isChecked); })" in source
