import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


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
    return run_node_script(
        node,
        script,
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
          await win.nekoWidgetMode.setEnabled(false, {{ silent: true }});
          if (win.nekoWidgetMode.isStealthEnabled() !== false) {{
            throw new Error('stealth must be ineffective while Widget Mode is disabled');
          }}
          if (win.nekoWidgetMode.getState().stealthEnabled !== true) {{
            throw new Error('disabling Widget Mode must preserve the stealth preference');
          }}
          await win.nekoWidgetMode.setEnabled(true, {{ silent: true }});
          if (win.nekoWidgetMode.isStealthEnabled() !== true) {{
            throw new Error('re-enabling Widget Mode must restore the stealth preference');
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


def _run_first_peek_notice_harness(
    stored_value: str | None,
    *,
    storage_throws: bool = False,
    drop_first_toast: bool = False,
    i18n_echoes_key: bool = False,
) -> dict:
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(APP_WIDGET_MODE_PATH))}, 'utf8');

        let dropNextToast = {json.dumps(drop_first_toast)};
        function load(storage) {{
          const listeners = new Map();
          const notices = [];
          const toastText = {{ textContent: '' }};
          class CustomEventLike {{
            constructor(type, init = {{}}) {{
              this.type = type;
              this.detail = init.detail;
            }}
          }}
          const win = {{
            localStorage: storage,
            addEventListener(type, callback) {{
              if (!listeners.has(type)) listeners.set(type, []);
              listeners.get(type).push(callback);
            }},
            dispatchEvent(event) {{
              for (const callback of listeners.get(event.type) || []) callback(event);
            }},
            t(key, options) {{ return {json.dumps(i18n_echoes_key)} ? key : 'i18n:' + key; }},
            showStatusToast(message, durationMs) {{
              // Mirrors the real toast: a higher-priority notice on screen
              // makes it drop this one without rendering anything.
              if (dropNextToast) {{
                dropNextToast = false;
                notices.push({{ message, durationMs, dropped: true }});
                return;
              }}
              toastText.textContent = message;
              notices.push({{ message, durationMs }});
            }},
          }};
          const context = {{
            window: win,
            document: {{
              readyState: 'loading',
              getElementById(id) {{ return id === 'status-toast-text' ? toastText : null; }},
            }},
            console,
            CustomEvent: CustomEventLike,
            fetch: async () => ({{ ok: false, status: 503, async json() {{ return {{}}; }} }}),
            Promise,
            Object,
            JSON,
          }};
          vm.createContext(context);
          vm.runInContext(source, context, {{ filename: 'app-widget-mode.js' }});
          const peek = (detail) => win.dispatchEvent(
            new CustomEventLike('neko:live2d-peek-changed', {{ detail }})
          );
          return {{ notices, peek }};
        }}

        const data = new Map();
        const initial = {json.dumps(stored_value)};
        if (initial !== null) data.set('neko_widget_mode_first_peek_notice_seen', initial);
        const storage = {json.dumps(storage_throws)}
          ? {{
              getItem() {{ throw new Error('SecurityError'); }},
              setItem() {{ throw new Error('QuotaExceededError'); }},
            }}
          : {{
              getItem(key) {{ return data.has(key) ? data.get(key) : null; }},
              setItem(key, value) {{ data.set(key, String(value)); }},
            }};

        const first = load(storage);
        // Visibility toggles of an existing anchor and peek clears are not a new peek.
        first.peek({{ active: true, visible: false, phase: 'hidden', reason: 'interaction-state' }});
        first.peek({{ active: false, phase: 'unanchored', reason: 'drag-away-from-edge' }});
        const beforeAnchor = first.notices.length;
        first.peek({{ active: true, visible: true, phase: 'peeking', reason: 'anchor-created' }});
        first.peek({{ active: true, visible: true, phase: 'peeking', reason: 'anchor-created' }});

        // A reload of the page must remember that the notice was already shown.
        const reloaded = load(storage);
        reloaded.peek({{ active: true, visible: false, phase: 'hidden', reason: 'anchor-created' }});

        console.log(JSON.stringify({{
          beforeAnchor,
          firstNotices: first.notices,
          reloadedNotices: reloaded.notices,
          stored: data.get('neko_widget_mode_first_peek_notice_seen') || null,
        }}));
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, (
        "node harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_first_edge_peek_shows_notice_once_and_persists() -> None:
    outcome = _run_first_peek_notice_harness(None)

    assert outcome["beforeAnchor"] == 0
    assert outcome["firstNotices"] == [
        {"message": "i18n:settings.widgetMode.firstPeekNotice", "durationMs": 10000}
    ]
    assert outcome["reloadedNotices"] == []
    assert outcome["stored"] == "1"


def test_first_edge_peek_notice_is_skipped_once_seen() -> None:
    outcome = _run_first_peek_notice_harness("1")

    assert outcome["firstNotices"] == []
    assert outcome["reloadedNotices"] == []


def test_first_edge_peek_notice_is_localized_without_honorifics() -> None:
    for locale in ("en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"):
        payload = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(
                encoding="utf-8"
            )
        )
        notice = payload["settings"]["widgetMode"]["firstPeekNotice"]
        assert notice.strip(), locale
        for honorific in ("主人", "Master", "ご主人", "주인", "Хозяин"):
            assert honorific not in notice, locale


def test_first_edge_peek_notice_shows_once_per_session_without_storage() -> None:
    outcome = _run_first_peek_notice_harness(None, storage_throws=True)

    assert outcome["firstNotices"] == [
        {"message": "i18n:settings.widgetMode.firstPeekNotice", "durationMs": 10000}
    ]
    assert outcome["stored"] is None


def test_first_edge_peek_notice_dropped_by_busier_toast_is_retried() -> None:
    outcome = _run_first_peek_notice_harness(None, drop_first_toast=True)

    message = "i18n:settings.widgetMode.firstPeekNotice"
    assert outcome["firstNotices"] == [
        {"message": message, "durationMs": 10000, "dropped": True},
        {"message": message, "durationMs": 10000},
    ]
    assert outcome["reloadedNotices"] == []
    assert outcome["stored"] == "1"


def test_first_edge_peek_notice_never_shows_a_raw_i18n_key() -> None:
    outcome = _run_first_peek_notice_harness(None, i18n_echoes_key=True)

    assert len(outcome["firstNotices"]) == 1
    message = outcome["firstNotices"][0]["message"]
    assert message != "settings.widgetMode.firstPeekNotice"
    assert "贴边探身" in message
