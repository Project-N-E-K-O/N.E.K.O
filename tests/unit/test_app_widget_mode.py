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


LEGACY_FIRST_PEEK_KEY = "neko_widget_mode_first_peek_notice_seen"
FIRST_PEEK_SESSION_KEY = "neko_widget_mode_first_peek_notice_shown"
FIRST_PEEK_MESSAGE = "i18n:settings.widgetMode.firstPeekNotice"


def _run_first_peek_notice_harness(
    *,
    legacy_local_value: str | None = None,
    storage_throws: bool = False,
    drop_first_toast: bool = False,
    i18n_echoes_key: bool = False,
    toast_kind: str = "dom",
) -> dict:
    # toast_kind="dom": the page's own toast (bootstrap-goodbye-and-toasts.js),
    # marked and rendering into #status-toast-text.
    # toast_kind="bridge": the desktop shell's IPC bridge, which replaces
    # window.showStatusToast, carries no page marker and never touches this DOM.
    assert toast_kind in ("dom", "bridge")
    script = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const source = fs.readFileSync({json.dumps(str(APP_WIDGET_MODE_PATH))}, 'utf8');

        let dropNextToast = {json.dumps(drop_first_toast)};
        const toastKind = {json.dumps(toast_kind)};
        const storageThrows = {json.dumps(storage_throws)};

        function makeStorage(data) {{
          if (storageThrows) {{
            return {{
              getItem() {{ throw new Error('SecurityError'); }},
              setItem() {{ throw new Error('QuotaExceededError'); }},
              removeItem() {{ throw new Error('SecurityError'); }},
            }};
          }}
          return {{
            getItem(key) {{ return data.has(key) ? data.get(key) : null; }},
            setItem(key, value) {{ data.set(key, String(value)); }},
            removeItem(key) {{ data.delete(key); }},
          }};
        }}

        function load(localStorage, sessionStorage) {{
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
            localStorage,
            sessionStorage,
            addEventListener(type, callback) {{
              if (!listeners.has(type)) listeners.set(type, []);
              listeners.get(type).push(callback);
            }},
            dispatchEvent(event) {{
              for (const callback of listeners.get(event.type) || []) callback(event);
            }},
            t(key, options) {{ return {json.dumps(i18n_echoes_key)} ? key : 'i18n:' + key; }},
            showStatusToast(message, durationMs) {{
              if (toastKind === 'bridge') {{
                // ipcRenderer.send('neko:show-toast', ...): shown in another window.
                notices.push({{ message, durationMs }});
                return;
              }}
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
          if (toastKind === 'dom') {{
            Object.defineProperty(win.showStatusToast, '__nekoDomStatusToast', {{ value: true }});
          }}
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

        const anchor = (visible = true) => ({{
          active: true, visible, phase: visible ? 'peeking' : 'hidden', reason: 'anchor-created',
        }});

        // localStorage outlives app launches; sessionStorage belongs to one pet window.
        const localData = new Map();
        const legacy = {json.dumps(legacy_local_value)};
        if (legacy !== null) localData.set({json.dumps(LEGACY_FIRST_PEEK_KEY)}, legacy);
        const localStorage = makeStorage(localData);
        const launch1Session = new Map();

        const first = load(localStorage, makeStorage(launch1Session));
        // Visibility toggles of an existing anchor and peek clears are not a new peek.
        first.peek({{ active: true, visible: false, phase: 'hidden', reason: 'interaction-state' }});
        first.peek({{ active: false, phase: 'unanchored', reason: 'drag-away-from-edge' }});
        const beforeAnchor = first.notices.length;
        first.peek(anchor());
        first.peek({{ active: false, phase: 'unanchored', reason: 'drag-away-from-edge' }});
        first.peek(anchor());
        const legacyAfterLoad = localData.has({json.dumps(LEGACY_FIRST_PEEK_KEY)})
          ? localData.get({json.dumps(LEGACY_FIRST_PEEK_KEY)})
          : null;

        // Reloading the same pet window in the same launch keeps sessionStorage.
        const reloaded = load(localStorage, makeStorage(launch1Session));
        reloaded.peek(anchor(false));

        // An app restart creates a new pet window with empty sessionStorage.
        const restarted = load(localStorage, makeStorage(new Map()));
        restarted.peek(anchor());
        restarted.peek(anchor());

        console.log(JSON.stringify({{
          beforeAnchor,
          firstNotices: first.notices,
          reloadedNotices: reloaded.notices,
          restartedNotices: restarted.notices,
          sessionShown: launch1Session.get({json.dumps(FIRST_PEEK_SESSION_KEY)}) || null,
          legacyAfterLoad,
          localKeys: Array.from(localData.keys()),
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


ONE_NOTICE = [{"message": FIRST_PEEK_MESSAGE, "durationMs": 10000}]


def test_first_edge_peek_notice_shows_once_per_launch() -> None:
    outcome = _run_first_peek_notice_harness()

    assert outcome["beforeAnchor"] == 0
    # Not repeated by later peeks in the same launch.
    assert outcome["firstNotices"] == ONE_NOTICE
    assert outcome["sessionShown"] == "1"
    # A reload of the same pet window is still the same launch.
    assert outcome["reloadedNotices"] == []
    # Shown again on the first peek after an app restart.
    assert outcome["restartedNotices"] == ONE_NOTICE
    # Nothing is remembered across launches.
    assert outcome["localKeys"] == []


def test_legacy_seen_flag_no_longer_suppresses_first_edge_peek_notice() -> None:
    outcome = _run_first_peek_notice_harness(legacy_local_value="1")

    assert outcome["firstNotices"] == ONE_NOTICE
    assert outcome["restartedNotices"] == ONE_NOTICE
    assert outcome["legacyAfterLoad"] is None


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


def test_first_edge_peek_notice_shows_once_per_page_without_storage() -> None:
    outcome = _run_first_peek_notice_harness(storage_throws=True)

    assert outcome["firstNotices"] == ONE_NOTICE
    assert outcome["sessionShown"] is None


def test_first_edge_peek_notice_dropped_by_busier_toast_is_retried() -> None:
    outcome = _run_first_peek_notice_harness(drop_first_toast=True)

    assert outcome["firstNotices"] == [
        {"message": FIRST_PEEK_MESSAGE, "durationMs": 10000, "dropped": True},
        {"message": FIRST_PEEK_MESSAGE, "durationMs": 10000},
    ]
    assert outcome["reloadedNotices"] == []
    assert outcome["sessionShown"] == "1"


def test_first_edge_peek_notice_never_shows_a_raw_i18n_key() -> None:
    outcome = _run_first_peek_notice_harness(i18n_echoes_key=True)

    assert len(outcome["firstNotices"]) == 1
    message = outcome["firstNotices"][0]["message"]
    assert message != "settings.widgetMode.firstPeekNotice"
    assert "贴边探身" in message


def test_first_edge_peek_notice_via_desktop_toast_bridge_shows_once_per_launch() -> None:
    # The desktop shell forwards window.showStatusToast to a separate toast
    # window, so the pet page's #status-toast-text never holds the notice.
    outcome = _run_first_peek_notice_harness(toast_kind="bridge")

    assert outcome["beforeAnchor"] == 0
    assert outcome["firstNotices"] == ONE_NOTICE
    assert outcome["sessionShown"] == "1"
    assert outcome["reloadedNotices"] == []
    assert outcome["restartedNotices"] == ONE_NOTICE


def test_page_status_toast_is_marked_as_the_dom_implementation() -> None:
    source = (
        PROJECT_ROOT / "static" / "app" / "app-ui" / "bootstrap-goodbye-and-toasts.js"
    ).read_text(encoding="utf-8")
    marker = "Object.defineProperty(I.showStatusToast, '__nekoDomStatusToast'"
    assert marker in source
    assert source.index(marker) < source.index(
        "window.showStatusToast = I.showStatusToast;"
    )
