import json
import shutil
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_GAME_MODE_BETA_PATH = PROJECT_ROOT / "static" / "app-game-mode-beta.js"
AVATAR_UI_POPUP_PATH = PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js"
INDEX_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "index.html"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        raise AssertionError("node is required to run app-game-mode-beta harness tests")
    return subprocess.run(
        [node_path, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_app_game_mode_beta_resource_bridge_contract():
    host_contract = json.dumps(
        {
            "windowType": "pet",
            "petInstanceId": "pet-resource",
            "signalCapabilities": {"exactGame": True},
            "hostCapabilities": {"compactPetWindowLeaseV1": True},
        }
    )
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync({json.dumps(str(APP_GAME_MODE_BETA_PATH))}, 'utf8');

        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{
          constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }}
        }}

        const win = new EventTargetLike();
        const doc = new EventTargetLike();
        const calls = [];
        const registrations = [];
        doc.readyState = 'complete';
        doc.querySelectorAll = () => [];
        win.document = doc;
        win.window = win;
        win.CustomEvent = CustomEventLike;
        win.console = console;
        win.alert = () => {{}};
        win.setInterval = () => 1;
        win.clearInterval = () => {{}};
        win.nekoLocalMutationSecurity = {{
          peekCachedToken: () => 'csrf-token',
        }};
        win.nekoGameModeHost = {{
          getContract: async () => ({host_contract}),
          onSystemResume: () => {{}},
        }};
        win.addEventListener('neko:game-mode-resource-registration', (event) => registrations.push(event.detail));

        function response(body) {{
          return {{ ok: true, status: 200, json: async () => body }};
        }}
        win.fetch = async (url, init = {{}}) => {{
          calls.push({{ url, init }});
          if (url === '/api/game-mode-beta/state') {{
            return response({{ success: true, state: {{
              enabled: false,
              pressure_state: 'normal',
              resource_session_phase: 'idle',
              settings: {{ resource_protection_on_game: true, compact_pet_window_enabled: true }},
            }} }});
          }}
          if (url === '/api/game-mode-beta/enabled') {{
            const enabled = JSON.parse(init.body).enabled === true;
            return response({{ success: true, state: {{
              enabled,
              resource_session_phase: 'idle',
              settings: {{ resource_protection_on_game: true, compact_pet_window_enabled: true }},
            }} }});
          }}
          if (url === '/api/game-mode-beta/settings' && init.method === 'POST') {{
            return response(JSON.parse(init.body));
          }}
          if (url === '/api/game-mode-beta/settings') {{
            return response({{ resource_protection_on_game: true, compact_pet_window_enabled: true }});
          }}
          if (url === '/api/game-mode-beta/windows/register') {{
            return response({{
              resource_session_active: true,
              resource_session_id: 'resource-1',
              resource_session_phase: 'soft_protected',
              resource_target_fps: 15,
              compact_pet_window_enabled: true,
            }});
          }}
          return response({{ success: true }});
        }};

        vm.runInNewContext(source, {{
          window: win,
          document: doc,
          CustomEvent: CustomEventLike,
          fetch: win.fetch,
          console,
          setInterval: win.setInterval,
          clearInterval: win.clearInterval,
        }});

        (async () => {{
          await new Promise((resolve) => setTimeout(resolve, 0));
          const api = win.nekoGameModeBeta;
          if (!api) throw new Error('bridge missing');
          if ('handleAuto' + 'SwitchEvent' in api) throw new Error('model switch handler leaked');
          if ('handle' + 'LifecycleMessage' in api) throw new Error('legacy lifecycle handler leaked');

          const settingsOk = await api.setSettings({{
            resource_protection_on_game: false,
            compact_pet_window_enabled: false,
            ['auto' + '_cat_on_game']: true,
          }});
          if (!settingsOk) throw new Error('resource settings update failed');
          const settingsCall = calls.filter((call) => call.url === '/api/game-mode-beta/settings' && call.init.method === 'POST').at(-1);
          const settingsBody = JSON.parse(settingsCall.init.body);
          if (Object.keys(settingsBody).sort().join(',') !== 'compact_pet_window_enabled,resource_protection_on_game') {{
            throw new Error('settings payload leaked a model-switch field');
          }}

          await api.registerHostWindow();
          if (!registrations.some((item) => item.resource_session_id === 'resource-1')) {{
            throw new Error('active resource registration was not relayed');
          }}
          if (!await api.setEnabled(true) || !api.isEnabled()) throw new Error('enable flow failed');
          const mutation = calls.find((call) => call.url === '/api/game-mode-beta/enabled');
          if (mutation.init.headers['X-CSRF-Token'] !== 'csrf-token') throw new Error('csrf header missing');
          console.log('game mode resource bridge passed');
        }})().catch((error) => {{ console.error(error.stack || error); process.exit(1); }});
        """
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "game mode resource bridge passed" in result.stdout


def test_app_game_mode_beta_has_no_model_switching_protocol():
    source = APP_GAME_MODE_BETA_PATH.read_text(encoding="utf-8")
    forbidden = (
        "auto" + "_cat_on_game",
        "game" + "_trigger_mode",
        "game_mode_" + "auto_switch",
        "game_mode_" + "auto",
        "handleAuto" + "SwitchEvent",
        "handle" + "LifecycleMessage",
        "live2d-" + "goodbye-click",
        "/api/game-mode-beta/manual" + "-restore",
        "/api/game-mode-beta/deep-sleep" + "-ack",
    )
    for token in forbidden:
        assert token not in source


def test_game_mode_detail_panel_only_exposes_resource_controls():
    source = AVATAR_UI_POPUP_PATH.read_text(encoding="utf-8")
    block = source.split("function createGameModeBetaDetailPanel", 1)[1].split(
        "function createAdvancedSettingsSidePanel", 1
    )[0]
    assert "resource_protection_on_game" in block
    assert "compact_pet_window_enabled" in block
    assert "exitCurrentSession" in block
    assert "settings.gameModeBeta.privacyNote" in block
    assert "auto" + "_cat_on_game" not in block
    assert "game" + "_trigger_mode" not in block


def test_app_game_mode_beta_is_home_only_and_versioned():
    from main_routers import pages_router

    index_source = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    chat_source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert '/static/app-game-mode-beta.js?v={{ static_asset_version }}' in index_source
    assert '/static/app-game-mode-beta.js?v={{ static_asset_version }}' not in chat_source
    assert APP_GAME_MODE_BETA_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS


def test_game_mode_locale_contract_is_resource_only():
    forbidden = {
        "auto" + "SwitchNotice",
        "auto" + "CatOnGame",
        "trigger" + "Mode",
        "smart" + "Mode",
        "instant" + "Mode",
        "switch" + "Failed",
        "restore" + "Failed",
        "signal" + "Unavailable",
    }
    for locale in ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"):
        payload = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )["settings"]["gameModeBeta"]
        assert forbidden.isdisjoint(payload)
        assert "resourceProtectionOnGame" in payload
        assert "compactPetWindow" in payload
        assert "exitResourceProtection" in payload
