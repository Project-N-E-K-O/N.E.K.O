'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const PARENT_JS_PATH = path.resolve(__dirname, '../../scripts/visit_probe/parent.js');
const PARENT_JS_SOURCE = fs.readFileSync(PARENT_JS_PATH, 'utf8');

function createHarness() {
  const storage = new Map();
  const calls = {
    setTargetFPS: [],
    startIdle: 0,
    stopIdle: 0,
    boostX11Calls: 0,
  };
  const origBoostX11 = function () {
    calls.boostX11Calls += 1;
  };
  const window = {
    targetFrameRate: 60,
    appState: { lipSyncActive: false },
    live2dManager: {
      pixi_app: { ticker: { start() {}, started: true, maxFPS: 60 }, view: { width: 800, height: 600 } },
      boostLinuxX11InteractiveFPS: origBoostX11,
      _startIdleFpsGovernor() { calls.startIdle += 1; },
      _stopIdleFpsGovernor() { calls.stopIdle += 1; },
      _enterIdleTickMode() {},
      _exitIdleTickMode() {},
      _applyRafMaxFps() {},
      boostInteractiveFPS() {},
      setTargetFPS(fps) { calls.setTargetFPS.push(fps); },
    },
    __probeTrace: null,
  };
  const context = {
    window,
    location: { href: 'http://127.0.0.1:48911/' },
    screen: { width: 1920, height: 1080 },
    localStorage: {
      getItem(k) { return storage.has(k) ? storage.get(k) : null; },
      setItem(k, v) { storage.set(k, String(v)); },
    },
    performance: { now: () => 0 },
    clearTimeout() {},
    getComputedStyle: () => ({ visibility: 'visible' }),
    document: { elementFromPoint: () => null },
    Error,
    Array,
    Math,
    String,
  };
  context.globalThis = context;
  vm.runInNewContext(PARENT_JS_SOURCE, context, { filename: 'parent.js' });
  return { P: window.__visitProbe, window, storage, calls, origBoostX11 };
}

test('restoreFps does not clobber project_neko_settings or lipSyncActive or live targetFrameRate', () => {
  const { P, window, storage, calls, origBoostX11 } = createHarness();
  const initialSettings = JSON.stringify({ targetFrameRate: 60, volume: 0.5 });
  const updatedSettings = JSON.stringify({ targetFrameRate: 120, volume: 0.9 });
  storage.set('project_neko_settings', initialSettings);

  P.setMode({ kind: 'timer', fps: 30 });
  storage.set('project_neko_settings', updatedSettings);
  window.appState.lipSyncActive = true;
  window.targetFrameRate = 120;

  assert.equal(P.restoreFps(), true);
  assert.equal(storage.get('project_neko_settings'), updatedSettings);
  assert.equal(window.appState.lipSyncActive, true);
  assert.deepEqual(calls.setTargetFPS, [120]);
  assert.equal(calls.startIdle, 1);
  assert.equal(window.live2dManager.boostLinuxX11InteractiveFPS, origBoostX11);
  assert.equal(P.saved, null);
  assert.equal(P.restoreFps(), false);
});

test('restoreFps uses saved targetFrameRate when window.targetFrameRate is unchanged or non-numeric', () => {
  const { P, window, calls } = createHarness();
  P.setMode({ kind: 'raf', fps: 45 });
  window.targetFrameRate = undefined;

  assert.equal(P.restoreFps(), true);
  assert.deepEqual(calls.setTargetFPS, [60]);
  assert.equal(P.saved, null);
});
