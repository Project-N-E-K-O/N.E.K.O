import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_WIDGET_MODE_PATH = PROJECT_ROOT / "static" / "app" / "app-widget-mode.js"
APP_WIDGET_INTERACTION_PATH = (
    PROJECT_ROOT / "static" / "app" / "app-widget-interaction.js"
)
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
        const serverState = {{ enabled: false, stealthEnabled: false }};

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
          if (url === '/api/widget-mode/enabled' && init.method === 'POST') {{
            serverState.enabled = JSON.parse(init.body).enabled === true;
          }}
          if (url === '/api/widget-mode/stealth-enabled' && init.method === 'POST') {{
            serverState.stealthEnabled = JSON.parse(init.body).enabled === true;
          }}
          return {{
            ok: true,
            status: 200,
            async json() {{
              return {{ success: true, state: {{ ...serverState }} }};
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
            stealthEnabled: false,
            backendState: {{ enabled: false, stealthEnabled: false }},
          }})) throw new Error('initial state is not minimal');

          const result = await win.nekoWidgetMode.setEnabled(true);
          await flush();
          if (result !== true || win.nekoWidgetMode.isEnabled() !== true) {{
            throw new Error('enabled state did not update');
          }}
          const stealthResult = await win.nekoWidgetMode.setStealthEnabled(true);
          await flush();
          if (stealthResult !== true || win.nekoWidgetMode.isStealthEnabled() !== true) {{
            throw new Error('stealth state did not update');
          }}
          const keys = Object.keys(win.nekoWidgetMode).sort().join(',');
          if (keys !== 'getState,isEnabled,isStealthEnabled,refreshState,setEnabled,setStealthEnabled') {{
            throw new Error('unexpected public API: ' + keys);
          }}
          const mutation = fetchCalls.find((call) => call.url === '/api/widget-mode/enabled');
          if (!mutation || mutation.init.headers['X-CSRF-Token'] !== 'csrf-token') {{
            throw new Error('mutation security header missing');
          }}
          if (mutation.init.body !== '{{"enabled":true}}') {{
            throw new Error('unexpected mutation payload');
          }}
          const stealthMutation = fetchCalls.find(
            (call) => call.url === '/api/widget-mode/stealth-enabled'
          );
          if (!stealthMutation || stealthMutation.init.body !== '{{"enabled":true}}') {{
            throw new Error('unexpected stealth mutation payload');
          }}
          if (!stateEvents.some((state) => state.enabled === false)
              || !stateEvents.some((state) => state.enabled === true)) {{
            throw new Error('state event did not cover both states');
          }}
          if (notices.length !== 2) throw new Error('toggle notices missing');
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
    interaction_script = (
        '/static/app/app-widget-interaction.js?v={{ static_asset_version }}"'
    )
    assert interaction_script in index_source
    assert interaction_script in chat_source
    assert APP_WIDGET_MODE_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    assert APP_WIDGET_INTERACTION_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS


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
        assert "widgetMode" not in payload["settings"]["toggles"]
        assert "widgetModeTooltip" not in payload["settings"]["toggles"]


def test_widget_mode_has_no_web_settings_control_or_dom_sync() -> None:
    popup_source = (PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js").read_text(
        encoding="utf-8"
    )
    client_source = APP_WIDGET_MODE_PATH.read_text(encoding="utf-8")

    for forbidden in (
        "createAdvancedSettingsSidePanel",
        "queueWidgetModeMutation",
        "id: 'widget-mode'",
        "toggle.id === 'widget-mode'",
        "_nekoUpdateWidgetModeStatus",
    ):
        assert forbidden not in popup_source
    assert "input[id$=\"-widget-mode\"]" not in client_source
    assert "document.querySelectorAll" not in client_source
