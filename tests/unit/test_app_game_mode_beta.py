import json
import shutil
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_GAME_MODE_BETA_PATH = PROJECT_ROOT / "static" / "app" / "app-game-mode-beta.js"
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


def test_app_game_mode_beta_frontend_contracts():
    script = textwrap.dedent(
        f"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync({json.dumps(str(APP_GAME_MODE_BETA_PATH))}, 'utf8');

        class EventTargetLike {{
          constructor() {{
            this.listeners = new Map();
          }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            event.target = this;
            const handlers = this.listeners.get(event.type) || [];
            for (const handler of handlers.slice()) handler.call(this, event);
            return true;
          }}
        }}

        class CustomEventLike {{
          constructor(type, init = {{}}) {{
            this.type = type;
            this.detail = init.detail;
          }}
        }}

        function createHarness(options = {{}}) {{
          const win = new EventTargetLike();
          const doc = new EventTargetLike();
          const fetchCalls = [];
          const notices = [];
          const goodbyeEvents = [];
          const returnEvents = [];
          const lifecycleOrder = [];
          const queryNodes = options.queryNodes || [];

          doc.readyState = 'complete';
          doc.querySelectorAll = () => queryNodes;
          doc.addEventListener = EventTargetLike.prototype.addEventListener.bind(doc);
          doc.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(doc);

          win.document = doc;
          win.live2dManager = {{
            _goodbyeClicked: options.initialCat === true,
            _isLoadingModel: false,
            cancelActiveModelLoadForGameMode() {{
              if (options.invalidateModelLoad !== true) return false;
              this._nekoGameModeReloadRequired = true;
              this._nekoGameModeLoadCancelReason = 'game-mode-protection';
              lifecycleOrder.push('model-invalidated');
              return true;
            }},
          }};
          win.vrmManager = {{ _goodbyeClicked: options.initialCat === true }};
          win.mmdManager = {{ _goodbyeClicked: options.initialCat === true }};
          win.pngtuberManager = {{ _isInReturnState: options.initialCat === true }};
          win.showCurrentModel = async () => {{
            lifecycleOrder.push('model-reload');
            return options.modelReloadSucceeds !== false;
          }};
          win.showStatusToast = (message) => notices.push(message);
          win.t = (_key, payload) => payload && payload.defaultValue ? payload.defaultValue : _key;
          win.nekoLocalMutationSecurity = {{
            peekCachedToken: () => 'test-csrf-token',
            getMutationHeaders: async () => ({{ 'X-CSRF-Token': 'test-csrf-token' }}),
          }};
          win.addEventListener = EventTargetLike.prototype.addEventListener.bind(win);
          win.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(win);
          win.removeEventListener = () => {{}};

          function setCat(active) {{
            win.live2dManager._goodbyeClicked = active;
            win.vrmManager._goodbyeClicked = active;
            win.mmdManager._goodbyeClicked = active;
            win.pngtuberManager._isInReturnState = active;
          }}

          win.addEventListener('live2d-goodbye-click', (event) => {{
            setCat(true);
            lifecycleOrder.push('goodbye');
            goodbyeEvents.push(event.detail || {{}});
          }});
          win.addEventListener('live2d-return-click', (event) => {{
            setCat(false);
            lifecycleOrder.push('return');
            returnEvents.push(event.detail || {{}});
          }});

          const responses = (options.responses || []).slice();
          win.fetch = async (url, init = {{}}) => {{
            fetchCalls.push({{ url, method: init.method || 'GET', body: init.body || '', headers: init.headers || {{}} }});
            const next = responses.length ? responses.shift() : {{ success: true, state: {{ enabled: true }} }};
            if (next.reject) throw new Error(next.reject);
            return {{
              ok: next.ok !== false,
              status: next.status || 200,
              json: async () => next.body || next,
            }};
          }};

          const context = {{
            window: win,
            document: doc,
            console,
            CustomEvent: CustomEventLike,
            Event: CustomEventLike,
            Promise,
            Math,
            Number,
            setTimeout,
            clearTimeout,
            fetch: win.fetch,
          }};
          vm.createContext(context);
          vm.runInContext(source, context, {{ filename: 'static/app/app-game-mode-beta.js' }});

          return {{
            win,
            doc,
            fetchCalls,
            goodbyeEvents,
            returnEvents,
            lifecycleOrder,
            notices,
            queryNodes,
            flush() {{
              return new Promise((resolve) => setTimeout(resolve, 0));
            }},
            setCat,
          }};
        }}

        function assert(condition, message) {{
          if (!condition) throw new Error(message);
        }}

        (async () => {{
          const autoPayload = {{
            source: 'game_mode_auto',
            reason: 'gpu',
            percent: 99,
            duration_seconds: 30,
          }};

          const gate = createHarness({{
            responses: [{{ success: true, state: {{ enabled: true }} }}],
          }});
          await gate.flush();
          gate.win.nekoGameModeBeta.handleAutoSwitchEvent({{ reason: 'debug', percent: 99 }});
          assert(gate.goodbyeEvents.length === 0, 'events without game_mode_auto source must be ignored');
          gate.win.nekoGameModeBeta.handleAutoSwitchEvent(autoPayload);
          assert(gate.goodbyeEvents.length === 1, 'game mode event should trigger existing cat flow');
          assert(gate.goodbyeEvents[0].source === 'game_mode_auto', 'goodbye detail should mark game mode source');
          assert(gate.win.nekoGameModeBeta.getState().autoSwitched === true, 'autoSwitched should be tracked');
          assert(gate.win.nekoGameModeBeta.getState().lastReason.metric === 'gpu', 'last reason should be tracked');
          assert(gate.win.nekoGameModeBeta.getStatusText().includes('GPU 99% / 30s'), 'status should include latest reason');

          const restore = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ success: true, state: {{ enabled: false }} }},
            ],
          }});
          await restore.flush();
          restore.win.nekoGameModeBeta.handleAutoSwitchEvent(autoPayload);
          assert(restore.goodbyeEvents.length === 1, 'auto event should enter cat form before disable');
          const disabledOk = await restore.win.nekoGameModeBeta.setEnabled(false);
          assert(disabledOk === true, 'disable should succeed');
          assert(restore.returnEvents.length === 1, 'disable should restore when game mode caused cat form');
          assert(restore.returnEvents[0].source === 'game_mode_auto', 'restore event should carry source');

          const invalidatedDisable = createHarness({{
            invalidateModelLoad: true,
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ success: true, state: {{ enabled: false }} }},
            ],
          }});
          await invalidatedDisable.flush();
          invalidatedDisable.win.nekoGameModeBeta.handleAutoSwitchEvent(autoPayload);
          const invalidatedDisableOk = await invalidatedDisable.win.nekoGameModeBeta.setEnabled(false);
          assert(invalidatedDisableOk === true, 'disable with an invalidated model should succeed');
          const reloadIndex = invalidatedDisable.lifecycleOrder.indexOf('model-reload');
          const returnIndex = invalidatedDisable.lifecycleOrder.indexOf('return');
          assert(reloadIndex >= 0, 'disable should reload an invalidated model');
          assert(returnIndex > reloadIndex, 'disable return must wait for invalidated model reload');

          const alreadyCat = createHarness({{
            initialCat: true,
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ success: true, state: {{ enabled: false }} }},
            ],
          }});
          await alreadyCat.flush();
          alreadyCat.win.nekoGameModeBeta.handleAutoSwitchEvent(autoPayload);
          assert(alreadyCat.win.nekoGameModeBeta.getState().alreadyCatWhenTriggered === true, 'pre-existing cat form should be remembered');
          await alreadyCat.win.nekoGameModeBeta.setEnabled(false);
          assert(alreadyCat.returnEvents.length === 0, 'disable must not restore a user pre-existing cat form');

          const manualRestore = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ success: true, state: {{ enabled: true, suppressed_until: 1600 }} }},
            ],
          }});
          await manualRestore.flush();
          manualRestore.win.nekoGameModeBeta.handleAutoSwitchEvent(autoPayload);
          manualRestore.win.dispatchEvent(new CustomEventLike('live2d-return-click'));
          await manualRestore.flush();
          const manualCall = manualRestore.fetchCalls.find((call) => call.url === '/api/game-mode-beta/manual-restore');
          assert(manualCall && manualCall.method === 'POST', 'manual return should notify backend cooldown');
          assert(manualRestore.win.nekoGameModeBeta.getState().manualOverride === true, 'manual override should be tracked');

          const retryManualRestore = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ ok: false, status: 403, body: {{ detail: 'stale csrf' }} }},
              {{ success: true, state: {{ enabled: true, suppressed_until: 1800 }} }},
            ],
          }});
          await retryManualRestore.flush();
          retryManualRestore.win.nekoGameModeBeta.handleAutoSwitchEvent(Object.assign({{
            cycle_id: 'manual-retry-cycle',
          }}, autoPayload));
          retryManualRestore.win.dispatchEvent(new CustomEventLike('live2d-return-click'));
          await retryManualRestore.flush();
          assert(retryManualRestore.win.nekoGameModeBeta.getState().autoSwitched === true, 'failed POST must retain auto-switch ownership');
          assert(retryManualRestore.win.nekoGameModeBeta.getState().manualOverride === false, 'failed POST must release the in-flight manual override');
          assert(retryManualRestore.win.nekoGameModeBeta.getState().currentCycleId === 'manual-retry-cycle', 'failed POST must retain the cycle id');
          assert(retryManualRestore.goodbyeEvents.length === 2, 'failed POST should return to protected cat form');

          retryManualRestore.win.dispatchEvent(new CustomEventLike('live2d-return-click'));
          await retryManualRestore.flush();
          assert(retryManualRestore.win.nekoGameModeBeta.getState().autoSwitched === false, 'successful retry should release auto-switch ownership');
          assert(retryManualRestore.win.nekoGameModeBeta.getState().manualOverride === true, 'successful retry should keep the manual override marker');
          assert(retryManualRestore.win.nekoGameModeBeta.getState().currentCycleId === null, 'successful retry should clear the cycle id');

          const pngtuberManualRestore = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ success: true, state: {{ enabled: true, suppressed_until: 1700 }} }},
            ],
          }});
          await pngtuberManualRestore.flush();
          pngtuberManualRestore.win.nekoGameModeBeta.handleAutoSwitchEvent(autoPayload);
          pngtuberManualRestore.win.dispatchEvent(new CustomEventLike('pngtuber-return-click'));
          await pngtuberManualRestore.flush();
          const pngtuberManualCall = pngtuberManualRestore.fetchCalls.find((call) => call.url === '/api/game-mode-beta/manual-restore');
          assert(pngtuberManualCall && pngtuberManualCall.method === 'POST', 'pngtuber return should notify backend cooldown');

          const userManualCat = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
              {{ success: true, state: {{ enabled: false }} }},
            ],
          }});
          await userManualCat.flush();
          userManualCat.win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {{
            detail: {{ source: 'user' }},
          }}));
          assert(userManualCat.win.nekoGameModeBeta.getState().manualOverride === true, 'user cat form should be marked as manual override');
          assert(userManualCat.win.nekoGameModeBeta.getState().autoSwitched === false, 'user cat form must not be treated as game mode auto switch');
          await userManualCat.win.nekoGameModeBeta.setEnabled(false);
          assert(userManualCat.returnEvents.length === 0, 'disabling game mode must not restore a user-entered cat form');

          const userManualReturn = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
            ],
          }});
          await userManualReturn.flush();
          userManualReturn.win.dispatchEvent(new CustomEventLike('live2d-return-click', {{
            detail: {{ source: 'user' }},
          }}));
          await userManualReturn.flush();
          const unexpectedManualCall = userManualReturn.fetchCalls.find((call) => call.url === '/api/game-mode-beta/manual-restore');
          assert(!unexpectedManualCall, 'manual return without game mode auto switch must not start backend cooldown');

          let changeCount = 0;
          let styleUpdateCount = 0;
          let statusUpdateCount = 0;
          const toggleItem = {{
            _nekoUpdateGameModeBetaStatus() {{ statusUpdateCount += 1; }},
            _nekoUpdateSettingsToggleStyle() {{ styleUpdateCount += 1; }},
          }};
          const checkbox = {{
            checked: false,
            closest(selector) {{
              return selector === '[role="switch"]' ? toggleItem : null;
            }},
            dispatchEvent() {{
              changeCount += 1;
            }},
          }};
          const visibleToggle = createHarness({{
            responses: [
              {{ success: true, state: {{ enabled: true }} }},
            ],
            queryNodes: [checkbox],
          }});
          await visibleToggle.flush();
          assert(checkbox.checked === true, 'visible settings toggle should mirror backend state');
          assert(statusUpdateCount > 0, 'visible settings toggle status should refresh');
          assert(styleUpdateCount > 0, 'visible settings toggle style should refresh');
          assert(changeCount === 0, 'programmatic toggle sync must not dispatch change as a user action');
          const mutationCalls = restore.fetchCalls.concat(manualRestore.fetchCalls).filter((call) => call.method === 'POST');
          assert(
            mutationCalls.every((call) => call.headers['X-CSRF-Token'] === 'test-csrf-token'),
            'all mutations should include the local CSRF token: ' + JSON.stringify(mutationCalls)
          );

          console.log('app-game-mode-beta frontend contracts passed');
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
    assert "app-game-mode-beta frontend contracts passed" in result.stdout


def test_app_game_mode_beta_is_home_only_and_versioned():
    from main_routers import pages_router

    index_source = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    chat_source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")

    assert '/static/app/app-game-mode-beta.js?v={{ static_asset_version }}' in index_source
    assert '/static/app/app-game-mode-beta.js?v={{ static_asset_version }}' not in chat_source
    assert APP_GAME_MODE_BETA_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS


def test_disabling_game_mode_clears_model_reload_markers_before_restore_event():
    source = APP_GAME_MODE_BETA_PATH.read_text(encoding="utf-8")
    helper = source.split("function clearModelReloadProtection()", 1)[1].split(
        "function handleDisabledRestore", 1
    )[0]
    block = source.split("function handleDisabledRestore()", 1)[1].split("function metricLabel", 1)[0]

    assert "manager._nekoGameModeReloadRequired = false;" in helper
    assert "manager._nekoGameModeLoadCancelReason = '';" in helper
    assert "clientState.modelLoadInvalidated = false;" in helper
    assert block.index("clearModelReloadProtection();") < block.index("live2d-return-click")


def test_backend_restore_clears_model_reload_markers_before_restore_event():
    source = APP_GAME_MODE_BETA_PATH.read_text(encoding="utf-8")
    block = source.split("payload.type === 'game_mode_restore'", 1)[1].split(
        "payload.type === 'game_mode_semantic_signal_unavailable'", 1
    )[0]

    assert block.index("clearModelReloadProtection();") < block.index("live2d-return-click")


def test_game_mode_settings_rejections_restore_ui_and_notify_user():
    source = AVATAR_UI_POPUP_PATH.read_text(encoding="utf-8")
    detail_block = source.split("function createGameModeBetaDetailPanel", 1)[1].split(
        "function createAdvancedSettingsSidePanel", 1
    )[0]
    toggle_block = source.split("function createSettingsToggleItem", 1)[1].split(
        "const refreshDependentToggles", 1
    )[0]

    assert "function showGameModeBetaMutationFailure" in source
    assert "settings.gameModeBeta.toggleFailed" in source
    assert "window.showStatusToast(message, 3000);" in source
    assert "persistSettings({ auto_cat_on_game: autoCheckbox.checked });" in detail_block
    assert "persistSettings({ game_trigger_mode: mode });" in detail_block
    assert ".catch(function (error)" in detail_block
    assert "checkbox.checked = !isChecked;" in toggle_block
    assert ".catch(function (error)" in toggle_block
    assert "showGameModeBetaMutationFailure(error);" in toggle_block
