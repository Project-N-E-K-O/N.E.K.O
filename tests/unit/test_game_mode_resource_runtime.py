import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = ROOT / "static" / "app" / "app-game-mode-resource-runtime.js"


def test_game_mode_resource_runtime_event_driven_contract():
    script = f"""
      const fs = require('fs');
      const vm = require('vm');
      const source = fs.readFileSync({json.dumps(str(RUNTIME_PATH))}, 'utf8');
      const listeners = new Map();
      const timers = [];
      const fetchCalls = [];
      const managerCalls = [];
      const hostCalls = [];
      const activityCalls = [];
      const add = (name, fn) => {{
        if (!listeners.has(name)) listeners.set(name, []);
        listeners.get(name).push(fn);
      }};
      const emit = (name, detail) => {{
        for (const fn of listeners.get(name) || []) fn({{ type: name, detail, clientX: detail?.clientX, clientY: detail?.clientY }});
      }};
      const bodyClasses = new Set();
      let hostContract = null;
      const manager = {{
        setGameModeResourceProtection(phase) {{ managerCalls.push(['phase', phase]); }},
        translateModelByScreenPixels(x, y) {{ managerCalls.push(['translate', x, y]); }},
        getModelScreenBounds() {{ return {{ left: 100, top: 120, width: 200, height: 300 }}; }},
      }};
      const host = {{
        acquireCompactLease(payload) {{ hostCalls.push(['acquire', payload]); return Promise.resolve({{ ok: true, originDelta: {{ x: -700, y: -40 }} }}); }},
        updateCompactLease(payload) {{ hostCalls.push(['update', payload]); return Promise.resolve({{ ok: true, originDelta: {{ x: 0, y: 0 }} }}); }},
        suspendCompactLeaseForDrag(payload) {{ hostCalls.push(['suspend', payload]); return Promise.resolve({{ ok: true }}); }},
        resumeCompactLeaseAfterDrag(payload) {{ hostCalls.push(['resume', payload]); return Promise.resolve({{ ok: true, originDelta: {{ x: 700, y: 40 }} }}); }},
        releaseCompactLease(payload) {{ hostCalls.push(['release', payload]); return Promise.resolve({{ ok: true }}); }},
        onCompactLeaseInvalidated(callback) {{ host.invalidated = callback; return () => {{}}; }},
      }};
      const context = {{
        console,
        Promise,
        Date,
        Math,
        JSON,
        setTimeout(fn, ms) {{ timers.push({{ fn, ms }}); return timers.length; }},
        clearTimeout() {{}},
        fetch(url, options = {{}}) {{ fetchCalls.push({{ url, options }}); return Promise.resolve({{ ok: true, json: async () => ({{ success: true }}) }}); }},
        CustomEvent: function(type, init) {{ this.type = type; this.detail = init?.detail; }},
        document: {{
          readyState: 'complete',
          body: {{ classList: {{ add: x => bodyClasses.add(x), remove: x => bodyClasses.delete(x) }} }},
          addEventListener: add,
        }},
      }};
      context.window = {{
        ...context,
        addEventListener: add,
        removeEventListener() {{}},
        dispatchEvent(event) {{ emit(event.type, event.detail); }},
        screenX: 10,
        screenY: 20,
        devicePixelRatio: 1,
        lanlan_config: {{ model_type: 'live2d' }},
        live2dManager: manager,
        nekoGameModeHost: host,
        nekoGameModeBeta: {{
          getState() {{ return {{ hostContract }}; }},
        }},
        nekoActivitySignalClient: {{ stop() {{ activityCalls.push('stop'); }}, start() {{ activityCalls.push('start'); }} }},
      }};
      context.globalThis = context.window;
      vm.createContext(context);
      vm.runInContext(source, context, {{ filename: 'app-game-mode-resource-runtime.js' }});

      emit('neko:game-mode-beta-message', {{
        type: 'game_mode_resource_protection_enter',
        source: 'game_mode_resource_protection',
        resource_session_id: 'session-1',
        pet_instance_ids: ['pet-1'],
        target_fps: 15,
        deep_sleep_after_seconds: 90,
        compact_pet_window_enabled: true,
      }});

      Promise.resolve().then(() => Promise.resolve()).then(async () => {{
        if (!managerCalls.some(x => x[0] === 'phase' && x[1] === 'soft_protected')) throw new Error('soft phase missing');
        if (timers.length !== 1 || timers[0].ms !== 90000) throw new Error('must use one 90s deep timer');
        if (hostCalls.length) throw new Error('host lease must wait for registration');
        hostContract = {{ petInstanceId: 'pet-1', hostCapabilities: {{ compactPetWindowLeaseV1: true }} }};
        emit('neko:game-mode-resource-registration', {{
          resource_session_active: true,
          resource_session_id: 'session-1',
          pet_instance_id: 'pet-1',
          resource_target_fps: 15,
          resource_deep_sleep_after_seconds: 90,
          compact_pet_window_enabled: true,
        }});
        await new Promise(resolve => setImmediate(resolve));
        if (hostCalls.filter(x => x[0] === 'acquire').length !== 1) throw new Error('compact acquire missing');
        if (!managerCalls.some(x => x[0] === 'translate' && x[1] === -700 && x[2] === -40)) throw new Error('origin compensation missing');

        emit('live2d-model-loaded');
        await new Promise(resolve => setImmediate(resolve));
        if (hostCalls.filter(x => x[0] === 'update').length !== 1) throw new Error('compact update missing');

        hostContract = null;
        emit('live2d-model-loaded');
        await new Promise(resolve => setImmediate(resolve));
        if (context.window.nekoGameModeResourceRuntime.getState().compactAcquired !== false) throw new Error('unsupported host kept stale compact state');
        hostContract = {{ petInstanceId: 'pet-1', hostCapabilities: {{ compactPetWindowLeaseV1: true }} }};
        emit('live2d-model-loaded');
        await new Promise(resolve => setImmediate(resolve));
        if (hostCalls.filter(x => x[0] === 'acquire').length !== 2) throw new Error('compact lease did not reacquire after host recovery');

        emit('pointerdown', {{ clientX: 20, clientY: 20 }});
        emit('pointerup', {{ clientX: 20, clientY: 20 }});
        await Promise.resolve();
        if (hostCalls.some(x => x[0] === 'suspend')) throw new Error('click must not expand compact window');

        emit('pointerdown', {{ clientX: 20, clientY: 20 }});
        emit('pointermove', {{ clientX: 30, clientY: 20 }});
        emit('pointerup', {{ clientX: 30, clientY: 20 }});
        await Promise.resolve();
        if (hostCalls.filter(x => x[0] === 'suspend').length !== 1 || hostCalls.filter(x => x[0] === 'resume').length !== 1) throw new Error('drag lease lifecycle missing');
        for (const method of ['acquire', 'update', 'suspend', 'resume']) {{
          const call = hostCalls.find(x => x[0] === method);
          if (!call || call[1].sessionId !== 'session-1' || call[1].petInstanceId !== 'pet-1') {{
            throw new Error(method + ' lease identity changed');
          }}
        }}
        const interactions = fetchCalls
          .filter(x => x.url === '/api/game-mode-beta/resource/interaction')
          .map(x => JSON.parse(x.options.body).interaction);
        for (const expected of ['click', 'drag-start', 'drag-end']) {{
          if (!interactions.includes(expected)) throw new Error('missing interaction: ' + expected);
        }}
        if (interactions.includes('pointerdown') || interactions.includes('pointermove')) throw new Error('high-frequency interaction leaked');

        timers[timers.length - 1].fn();
        await Promise.resolve();
        if (!managerCalls.some(x => x[0] === 'phase' && x[1] === 'deep_sleep')) throw new Error('deep phase missing');
        if (!bodyClasses.has('neko-game-resource-deep')) throw new Error('deep body class missing');
        if (activityCalls.length) throw new Error('resource deep sleep must keep Activity Signal alive');

        const exited = await context.window.nekoGameModeResourceRuntime.exitCurrentSession();
        if (!exited) throw new Error('explicit exit failed');
        if (!managerCalls.some(x => x[0] === 'phase' && x[1] === 'idle')) throw new Error('idle restore missing');
        if (!hostCalls.some(x => x[0] === 'release')) throw new Error('lease release missing');
        const netTranslation = managerCalls
          .filter(x => x[0] === 'translate')
          .reduce((sum, x) => [sum[0] + x[1], sum[1] + x[2]], [0, 0]);
        if (netTranslation[0] !== 0 || netTranslation[1] !== 0) throw new Error('compact origin compensation not restored');
        if (!fetchCalls.some(x => x.url === '/api/game-mode-beta/resource/exit')) throw new Error('explicit exit API missing');
        if (fetchCalls.some(x => String(x.url).endsWith('/'))) throw new Error('API URL has trailing slash');
        console.log('resource runtime contract passed');
      }}).catch(error => {{ console.error(error); process.exitCode = 1; }});
    """
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "resource runtime contract passed" in result.stdout


def test_model_managers_expose_temporary_resource_runtime_without_persisting_preferences():
    files = {
        "live2d": (ROOT / "static/live2d/live2d-core.js").read_text(encoding="utf-8"),
        "vrm": (ROOT / "static/vrm/vrm-manager.js").read_text(encoding="utf-8"),
        "mmd": (ROOT / "static/mmd/mmd-manager.js").read_text(encoding="utf-8"),
        "png": (ROOT / "static/pngtuber-core.js").read_text(encoding="utf-8"),
    }
    for source in files.values():
        assert "setGameModeResourceProtection(phase)" in source
        assert "translateModelByScreenPixels" in source
    assert "window.targetFrameRate = 15" not in "\n".join(files.values())
    assert "window.mouseTrackingEnabled = false" not in "\n".join(files.values())
    assert "window.humanoidLocalTrackingEnabled = false" not in "\n".join(files.values())
    assert "if (next !== 'idle' && this._idleFpsGovernorTimer)" in files["live2d"]
    assert "this._gameModeResourceIdleGovernorWasRunning = true" in files["live2d"]
    assert "_shouldRunGameModeFrame(timestamp, 'lip-sync')" in files["png"]


def test_resource_runtime_is_loaded_only_on_pet_page_and_uses_no_polling_loop():
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    assert "setInterval(" not in source
    assert "requestAnimationFrame(" not in source
    index = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    chat = (ROOT / "templates/chat.html").read_text(encoding="utf-8")
    asset = "/static/app/app-game-mode-resource-runtime.js?v={{ static_asset_version }}"
    assert asset in index
    assert asset not in chat
