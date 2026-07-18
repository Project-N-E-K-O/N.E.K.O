import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_WIDGET_MODE_PATH = PROJECT_ROOT / "static" / "app" / "app-widget-mode.js"
INDEX_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "index.html"
CHAT_TEMPLATE_PATH = PROJECT_ROOT / "templates" / "chat.html"


def _run_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        raise AssertionError("node is required to run Widget Mode frontend tests")
    return subprocess.run(
        [node_path, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_app_widget_mode_frontend_contracts() -> None:
    script = textwrap.dedent(
        """
        const fs = require('node:fs');
        const vm = require('node:vm');
        const source = fs.readFileSync(__SOURCE_PATH__, 'utf8');

        class EventTargetLike {{
          constructor() {{ this.listeners = new Map(); }}
          addEventListener(type, handler) {{
            if (!this.listeners.has(type)) this.listeners.set(type, []);
            this.listeners.get(type).push(handler);
          }}
          dispatchEvent(event) {{
            event.target = this;
            for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
            return true;
          }}
        }}
        class CustomEventLike {{
          constructor(type, init = {{}}) {{ this.type = type; this.detail = init.detail; }}
        }}

        function createHarness(options = {{}}) {{
          const win = new EventTargetLike();
          const doc = new EventTargetLike();
          const fetchCalls = [];
          const goodbyeEvents = [];
          const returnEvents = [];
          const registrations = [];
          const stateEvents = [];
          const leaseCalls = [];
          let resumeCallback = null;
          let leaseInvalidatedCallback = null;
          let paused = 0;
          let resumed = 0;
          let modelReloads = 0;
          let resolveLeaseAcquire = null;

          doc.readyState = 'complete';
          doc.body = {{}};
          doc.querySelectorAll = () => [];
          const returnBall = {{
            getBoundingClientRect: () => ({{ left: 100, top: 120, width: 80, height: 90 }}),
          }};
          doc.querySelector = () => returnBall;
          const manager = {{
            _goodbyeClicked: false,
            cancelActiveModelLoadForWidgetMode() {{ return options.invalidateModelLoad === true; }},
            pauseRendering() {{
              if (options.pauseFails) throw new Error('pause failed');
              paused += 1;
            }},
            resumeRendering() {{ resumed += 1; }},
          }};
          win.document = doc;
          win.screenX = 10;
          win.screenY = 20;
          win.lanlan_config = {{ model_type: options.modelType || 'vrm' }};
          win.live2dManager = options.modelType === 'live2d' ? manager : {{ _goodbyeClicked: false }};
          win.vrmManager = options.modelType === 'live2d' ? {{ _goodbyeClicked: false }} : manager;
          win.mmdManager = {{ _goodbyeClicked: false }};
          win.pngtuberManager = {{ _isInReturnState: false }};
          win.showCurrentModel = async () => {{
            modelReloads += 1;
            return options.modelReloadFails !== true;
          }};
          win.nekoLocalMutationSecurity = {{ peekCachedToken: () => 'csrf' }};
          const hostContract = {{
              petInstanceId: 'pet-1',
              windowType: 'pet',
              widgetModeProtocolVersion: options.protocolVersion || 1,
              widgetModeCompactionLeaseV1: options.lease !== false,
          }};
          if (!options.omitSignalCapabilities) hostContract.signalCapabilities = {{}};
          win.nekoWidgetModeHost = {{
            getContract: async () => hostContract,
            setRegistered: (value) => registrations.push(value === true),
            onSystemResume: (callback) => {{ resumeCallback = callback; return () => {{}}; }},
            acquireCompactLease: async (payload) => {{
              leaseCalls.push(['acquire', payload]);
              if (options.deferLeaseAcquire) {{
                return new Promise((resolve) => {{ resolveLeaseAcquire = resolve; }});
              }}
              return options.leaseAcquireFails
                ? {{ ok: false, code: 'set-bounds-failed' }}
                : {{ ok: true, status: 'acquired' }};
            }},
            suspendCompactLeaseForDrag: async (payload) => {{
              leaseCalls.push(['suspend', payload]);
              return {{ ok: true, status: 'suspended' }};
            }},
            resumeCompactLeaseAfterDrag: async (payload) => {{
              leaseCalls.push(['resume', payload]);
              return {{ ok: true, status: 'acquired' }};
            }},
            releaseCompactLease: async (payload) => {{
              leaseCalls.push(['release', payload]);
              return {{ ok: true, status: 'released' }};
            }},
            onCompactLeaseInvalidated: (callback) => {{
              leaseInvalidatedCallback = callback;
              return () => {{}};
            }},
          }};
          win.addEventListener = EventTargetLike.prototype.addEventListener.bind(win);
          win.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(win);
          win.addEventListener('live2d-goodbye-click', (event) => {{
            manager._goodbyeClicked = true;
            win.live2dManager._goodbyeClicked = true;
            goodbyeEvents.push(event.detail || {{}});
          }});
          win.addEventListener('live2d-return-click', (event) => {{
            manager._goodbyeClicked = false;
            win.live2dManager._goodbyeClicked = false;
            returnEvents.push(event.detail || {{}});
          }});
          win.addEventListener('neko:widget-mode-state-changed', (event) => stateEvents.push(event.detail));

          win.fetch = async (url, init = {{}}) => {{
            const body = init.body ? JSON.parse(init.body) : null;
            fetchCalls.push({{ url, method: init.method || 'GET', body }});
            let payload = {{ success: true, state: {{ enabled: true }} }};
            if (url === '/api/widget-mode/windows/register') {{
              const compatible = (options.protocolVersion || 1) === 1
                && options.lease !== false
                && !options.omitSignalCapabilities;
              payload = {{
                protocol_compatible: compatible,
                widget_mode_capable: compatible,
                compaction_cycle_id: options.registrationCycleId || null,
                compaction_phase: options.registrationCycleId ? 'compacted' : 'idle',
                join_as_compacted: !!options.registrationCycleId,
              }};
            }}
            return {{ ok: true, status: 200, json: async () => payload }};
          }};

          const context = {{
            window: win,
            document: doc,
            console,
            CustomEvent: CustomEventLike,
            Event: CustomEventLike,
            Promise,
            Set,
            Math,
            Number,
            Date,
            setTimeout,
            clearTimeout,
            setInterval: () => 1,
            clearInterval: () => {{}},
            fetch: win.fetch,
          }};
          vm.createContext(context);
          vm.runInContext(source, context, {{ filename: 'app-widget-mode.js' }});

          return {{
            win,
            manager,
            fetchCalls,
            goodbyeEvents,
            returnEvents,
            registrations,
            stateEvents,
            leaseCalls,
            returnBall,
            paused: () => paused,
            resumed: () => resumed,
            modelReloads: () => modelReloads,
            resume: () => resumeCallback && resumeCallback({{ reason: 'system-resume' }}),
            invalidateLease: () => leaseInvalidatedCallback && leaseInvalidatedCallback({{
              reason: 'display-metrics-changed',
            }}),
            resolveLeaseAcquire: (result = {{ ok: true, status: 'acquired' }}) =>
              resolveLeaseAcquire && resolveLeaseAcquire(result),
            flush: async () => {{
              await new Promise((resolve) => setTimeout(resolve, 0));
              await new Promise((resolve) => setTimeout(resolve, 0));
            }},
          }};
        }}

        function assert(condition, message) {{ if (!condition) throw new Error(message); }}
        const request = (cycle) => ({
          type: 'widget_mode_compaction_requested',
          source: 'widget_mode_compaction',
          compaction_cycle_id: cycle,
          reason: 'widget-mode',
        }});
        const lifecycle = (type, cycle, extra = {{}}) => Object.assign({{
          type,
          source: 'widget_mode_compaction',
          compaction_cycle_id: cycle,
        }}, extra);

        (async () => {{
          const vrm = createHarness({{ modelType: 'vrm' }});
          await vrm.flush();
          assert(vrm.registrations.length === 1 && vrm.registrations[0] === true,
            'backend registration must confirm host registration');
          await vrm.win.nekoWidgetMode.handleLifecycleMessage(request('cycle-1'));
          const compactAck = vrm.fetchCalls.find((call) =>
            call.url === '/api/widget-mode/compaction/ack' && call.body.status === 'compacted');
          assert(vrm.goodbyeEvents.length === 1, 'non-Live2D model should compact');
          assert(compactAck && compactAck.body.compaction_cycle_id === 'cycle-1', 'compaction ACK uses cycle id');
          assert(vrm.leaseCalls.length === 1 && vrm.leaseCalls[0][0] === 'acquire',
            'compaction must acquire the host window lease before ACK');
          assert(vrm.leaseCalls[0][1].sessionId === 'cycle-1', 'window lease uses cycle id');

          vrm.win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
            detail: {{ reason: 'return-ball-drag-start', container: vrm.returnBall }},
          }}));
          await vrm.flush();
          vrm.win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {{
            detail: {{ reason: 'return-ball-drag-end', container: vrm.returnBall, dragCancelled: false }},
          }}));
          await vrm.flush();
          assert(vrm.leaseCalls.some((call) => call[0] === 'suspend'),
            'return-ball drag must suspend the compact lease');
          assert(vrm.leaseCalls.some((call) => call[0] === 'resume'),
            'return-ball drag end must resume the compact lease at the new anchor');

          await vrm.win.nekoWidgetMode.handleLifecycleMessage(lifecycle(
            'widget_mode_renderer_suspension_requested', 'cycle-1', {{ pet_instance_ids: ['pet-1'] }}
          ));
          assert(vrm.paused() === 1, 'owned renderer should pause once');
          const rendererAck = vrm.fetchCalls.find((call) =>
            call.url === '/api/widget-mode/renderer-suspension/ack');
          assert(rendererAck && rendererAck.body.success === true, 'renderer ACK should report success');

          await vrm.win.nekoWidgetMode.handleLifecycleMessage(lifecycle(
            'widget_mode_compaction_restore_requested', 'old-cycle'
          ));
          assert(vrm.returnEvents.length === 0, 'stale restore must be ignored');
          await vrm.win.nekoWidgetMode.handleLifecycleMessage(lifecycle(
            'widget_mode_compaction_restore_requested', 'cycle-1', {{ pet_instance_ids: ['pet-1'] }}
          ));
          assert(vrm.resumed() === 1, 'only the manager paused in this cycle should resume');
          assert(vrm.returnEvents.length === 1, 'current cycle restore should restore model');
          assert(vrm.leaseCalls.some((call) => call[0] === 'release'),
            'restore must release the host window lease');
          const ackCountAfterRestore = vrm.fetchCalls.filter((call) =>
            call.url === '/api/widget-mode/compaction/ack').length;
          await vrm.win.nekoWidgetMode.handleLifecycleMessage(request('cycle-1'));
          assert(vrm.goodbyeEvents.length === 1,
            'a late request from an already-finished cycle must be ignored');
          assert(vrm.fetchCalls.filter((call) =>
            call.url === '/api/widget-mode/compaction/ack').length === ackCountAfterRestore,
            'a late request from an already-finished cycle must not ACK again');

          const live2d = createHarness({{ modelType: 'live2d' }});
          await live2d.flush();
          await live2d.win.nekoWidgetMode.handleLifecycleMessage(request('live-cycle'));
          const alreadyAck = live2d.fetchCalls.find((call) =>
            call.url === '/api/widget-mode/compaction/ack' && call.body.status === 'already_compacted');
          assert(alreadyAck, 'Live2D Widget Mode request must not change the model');
          assert(live2d.goodbyeEvents.length === 0, 'Live2D should remain unchanged');

          const mismatch = createHarness({{ protocolVersion: 2 }});
          await mismatch.flush();
          await mismatch.win.nekoWidgetMode.handleLifecycleMessage(request('mismatch-cycle'));
          assert(mismatch.goodbyeEvents.length === 0, 'protocol mismatch must fail closed');
          const failedAck = mismatch.fetchCalls.find((call) =>
            call.url === '/api/widget-mode/compaction/ack' && call.body.status === 'failed');
          assert(failedAck, 'protocol mismatch reports failed without partial compaction');

          const missingCapabilities = createHarness({{ omitSignalCapabilities: true }});
          await missingCapabilities.flush();
          await missingCapabilities.win.nekoWidgetMode.handleLifecycleMessage(request('missing-capability-cycle'));
          assert(missingCapabilities.goodbyeEvents.length === 0,
            'missing host capabilities must fail closed');

          const leaseFailure = createHarness({{ modelType: 'vrm', leaseAcquireFails: true }});
          await leaseFailure.flush();
          await leaseFailure.win.nekoWidgetMode.handleLifecycleMessage(request('lease-failure-cycle'));
          const leaseFailedAck = leaseFailure.fetchCalls.find((call) =>
            call.url === '/api/widget-mode/compaction/ack' && call.body.status === 'failed');
          assert(leaseFailedAck, 'window lease failure must ACK failed');
          assert(leaseFailure.returnEvents.length === 1,
            'window lease failure must restore the original model');
          assert(!leaseFailure.fetchCalls.some((call) =>
            call.url === '/api/widget-mode/compaction/ack' && call.body.status === 'compacted'),
            'window lease failure must never ACK compacted');

          const obsoleteLease = createHarness({{ modelType: 'vrm', deferLeaseAcquire: true }});
          await obsoleteLease.flush();
          const obsoleteRequest = obsoleteLease.win.nekoWidgetMode.handleLifecycleMessage(
            request('obsolete-lease-cycle')
          );
          await obsoleteLease.flush();
          await obsoleteLease.win.nekoWidgetMode.setEnabled(false);
          obsoleteLease.resolveLeaseAcquire();
          await obsoleteRequest;
          await obsoleteLease.flush();
          const obsoleteRelease = obsoleteLease.leaseCalls.find((call) =>
            call[0] === 'release' && call[1].sessionId === 'obsolete-lease-cycle');
          assert(obsoleteRelease,
            'a lease acquired for an obsolete cycle must be released with the original payload');

          const lateJoin = createHarness({{ modelType: 'vrm', registrationCycleId: 'late-cycle' }});
          await lateJoin.win.nekoWidgetMode.handleLifecycleMessage(request('late-cycle'));
          await lateJoin.flush();
          await lateJoin.flush();
          assert(lateJoin.goodbyeEvents.length === 1,
            'an early compatibility failure must not suppress the registration late-join replay');

          const pauseFailure = createHarness({{ modelType: 'vrm', pauseFails: true }});
          await pauseFailure.flush();
          await pauseFailure.win.nekoWidgetMode.handleLifecycleMessage(request('pause-cycle'));
          await pauseFailure.win.nekoWidgetMode.handleLifecycleMessage(lifecycle(
            'widget_mode_renderer_suspension_requested', 'pause-cycle', {{ pet_instance_ids: ['pet-1'] }}
          ));
          const failedRendererAck = pauseFailure.fetchCalls.find((call) =>
            call.url === '/api/widget-mode/renderer-suspension/ack');
          assert(failedRendererAck.body.success === false, 'renderer failure should be acknowledged as failure');

          const reloadFailure = createHarness({{
            modelType: 'vrm',
            invalidateModelLoad: true,
            modelReloadFails: true,
          }});
          await reloadFailure.flush();
          await reloadFailure.win.nekoWidgetMode.handleLifecycleMessage(request('reload-failure-cycle'));
          await reloadFailure.win.nekoWidgetMode.handleLifecycleMessage(lifecycle(
            'widget_mode_compaction_restore_requested',
            'reload-failure-cycle',
            {{ pet_instance_ids: ['pet-1'] }}
          ));
          assert(reloadFailure.modelReloads() === 1, 'restore must retry an invalidated model load');
          assert(reloadFailure.returnEvents.length === 0,
            'failed model reload must not leave the compacted cat form');
          assert(reloadFailure.goodbyeEvents.at(-1).reason === 'restore-failed',
            'failed model reload must keep the cycle compacted for a later retry');

          const systemResume = createHarness({{ modelType: 'vrm' }});
          await systemResume.flush();
          await systemResume.win.nekoWidgetMode.handleLifecycleMessage(request('system-resume-cycle'));
          await systemResume.win.nekoWidgetMode.handleLifecycleMessage(lifecycle(
            'widget_mode_renderer_suspension_requested',
            'system-resume-cycle',
            {{ pet_instance_ids: ['pet-1'] }}
          ));
          systemResume.resume();
          await systemResume.flush();
          assert(systemResume.resumed() === 1,
            'system resume must resume exactly the renderer suspended by this cycle');
          const resumeRendererAck = systemResume.fetchCalls.filter((call) =>
            call.url === '/api/widget-mode/renderer-suspension/ack').at(-1);
          assert(resumeRendererAck && resumeRendererAck.body.success === false,
            'system resume must report renderer suspension no longer active');
          assert(systemResume.registrations.length === 2 && systemResume.registrations.every(Boolean),
            'system resume must re-register the compatible pet window');

          vrm.win.dispatchEvent(new CustomEventLike('beforeunload'));
          assert(vrm.registrations.at(-1) === false, 'unload must clear host registration');
          assert(vrm.stateEvents.length > 0, 'state changes must use the Widget Mode DOM event');
          console.log('Widget Mode frontend contracts passed');
        }})().catch((error) => {{
          console.error(error && error.stack ? error.stack : error);
          process.exit(1);
        }});
        """
    ).replace("{{", "{").replace("}}", "}").replace(
        "__SOURCE_PATH__",
        json.dumps(str(APP_WIDGET_MODE_PATH)),
    )
    result = _run_node_harness(script)
    assert result.returncode == 0, (
        "node harness failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "Widget Mode frontend contracts passed" in result.stdout


def test_app_widget_mode_is_home_only_and_versioned() -> None:
    from main_routers import pages_router

    index_source = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")
    chat_source = CHAT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert '/static/app/app-widget-mode.js?v={{ static_asset_version }}' in index_source
    assert '/static/app/app-widget-mode.js?v={{ static_asset_version }}' not in chat_source
    assert APP_WIDGET_MODE_PATH in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS


def test_widget_mode_websocket_capability_and_event_contract() -> None:
    source = (PROJECT_ROOT / "static" / "app" / "app-websocket.js").read_text(encoding="utf-8")
    assert "?widget_mode_capable=1" in source
    assert "response.type.startsWith('widget_mode_')" in source
    assert "neko:widget-mode-message" in source


def test_widget_mode_product_names_and_fallbacks_are_localized() -> None:
    app_source = APP_WIDGET_MODE_PATH.read_text(encoding="utf-8")
    popup_source = (PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js").read_text(
        encoding="utf-8"
    )

    for fallback in (
        "挂边模式 Beta 已开启。",
        "挂边模式 Beta 已关闭。",
        "挂边模式 Beta 切换失败，请稍后重试。",
        "挂边模式 Beta 已将当前模型收缩为猫形态。",
    ):
        assert fallback in app_source
    assert "挂边模式 Beta 切换失败，请稍后重试。" in popup_source
    assert "label: window.t ? window.t('settings.toggles.widgetMode') : '挂边模式 Beta'" in popup_source

    expected_names = {
        "en": "Edge Dock Mode Beta",
        "es": "Modo de acoplamiento al borde Beta",
        "ja": "エッジドックモード Beta",
        "ko": "가장자리 도킹 모드 Beta",
        "pt": "Modo de encaixe na borda Beta",
        "ru": "Режим крепления к краю Beta",
        "zh-CN": "挂边模式 Beta",
        "zh-TW": "掛邊模式 Beta",
    }
    for locale, product_name in expected_names.items():
        payload = json.loads(
            (PROJECT_ROOT / "static" / "locales" / f"{locale}.json").read_text(encoding="utf-8")
        )
        settings = payload["settings"]
        widget_mode = settings["widgetMode"]
        assert product_name in widget_mode["enabledNotice"]
        assert product_name in widget_mode["disabledNotice"]
        assert product_name in widget_mode["toggleFailed"]
        assert product_name in widget_mode["compactionConfirmed"]
        assert settings["toggles"]["widgetMode"] == product_name


def test_widget_mode_toggle_mutation_is_serialized_without_fixed_timer_reentry() -> None:
    source = (PROJECT_ROOT / "static" / "avatar" / "avatar-ui-popup.js").read_text(
        encoding="utf-8"
    )

    assert "function queueWidgetModeMutation(operation)" in source
    widget_toggle_block = source.split("} else if (toggle.id === 'widget-mode')", 1)[1].split(
        "}",
        1,
    )[0]
    assert "return queueWidgetModeMutation(function ()" in widget_toggle_block
    assert re.search(
        r"toggle\.id === 'widget-mode'.*mutation.*\.finally\(clearProcessing\)",
        source,
        re.DOTALL,
    )
