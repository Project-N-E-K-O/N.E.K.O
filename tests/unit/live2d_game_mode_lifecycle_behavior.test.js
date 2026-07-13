'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class EventTargetLike {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }
  dispatchEvent(event) {
    event.target = this;
    for (const handler of (this.listeners.get(event.type) || []).slice()) handler.call(this, event);
    return true;
  }
}

class CustomEventLike {
  constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
}

function classList() {
  const values = new Set();
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    contains(value) { return values.has(value); },
  };
}

async function flush() {
  for (let i = 0; i < 16; i += 1) await Promise.resolve();
}

function createHarness(options = {}) {
  const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-game-mode-beta.js'), 'utf8');
  const win = new EventTargetLike();
  const doc = new EventTargetLike();
  const calls = [];
  const pauses = [];
  const tiers = [];
  const goodbyeEvents = [];
  let activityStarted = 0;
  let activityStopped = 0;
  let cat = false;

  doc.readyState = 'complete';
  doc.body = { classList: classList() };
  doc.querySelectorAll = () => [];
  doc.addEventListener = EventTargetLike.prototype.addEventListener.bind(doc);
  win.document = doc;
  win.addEventListener = EventTargetLike.prototype.addEventListener.bind(win);
  win.dispatchEvent = EventTargetLike.prototype.dispatchEvent.bind(win);
  win.live2dManager = { _goodbyeClicked: false, pauseRendering: () => pauses.push('live2d') };
  let vrmPauseAttempts = 0;
  win.vrmManager = { _goodbyeClicked: false, _modelLoadState: options.invalidateLoad ? 'loading' : 'idle', pauseRendering: () => {
    vrmPauseAttempts += 1;
    if (options.failFirstVrmPause && vrmPauseAttempts === 1) throw new Error('transient pause failure');
    pauses.push('vrm');
  } };
  win.mmdManager = { _goodbyeClicked: false, pauseRendering: () => pauses.push('mmd') };
  win.pngtuberManager = { _isInReturnState: false };
  win.isNekoGoodbyeResourceSuspended = () => cat;
  win.nekoAutoGoodbye = { setVisualTier: (tier, meta) => tiers.push({ tier, meta }) };
  win.nekoActivitySignalClient = {
    start: () => { activityStarted += 1; },
    stop: () => { activityStopped += 1; },
  };
  const edgeAnchor = options.edgeAnchor || {
    kind: 'live2d-edge-peek',
    edge: 'top-left',
    side: 'left',
    edgeAnchorRatio: 0.1,
  };
  win.nekoLive2DGameModeEdgePeek = {
    captureRestoreAnchor: () => edgeAnchor,
    clear: () => {},
    restoreAnchor: async () => true,
  };
  win.showCurrentModel = async () => {
    if (options.failModelRestore) throw new Error('model restore failed');
    if (options.blockModelRestore) return false;
    return true;
  };
  if (!options.hostless) {
    win.nekoGameModeHost = {
      getContract: async () => ({
        petInstanceId: 'pet-test-1',
        windowType: 'pet',
        signalCapabilities: { exact_game: 'supported' },
      }),
      onSystemResume: () => () => {},
    };
  }
  win.t = (_key, payload) => payload && payload.defaultValue ? payload.defaultValue : _key;
  win.showStatusToast = () => {};
  win.addEventListener('live2d-goodbye-click', (event) => {
    goodbyeEvents.push(event.detail || {});
    cat = true;
    win.live2dManager._goodbyeClicked = true;
  });
  win.addEventListener('live2d-return-click', () => {
    if (options.blockReturn) return;
    cat = false;
    win.live2dManager._goodbyeClicked = false;
  });

  let fakeNow = 0;
  const contextDate = options.expireReturnWaitImmediately
    ? { now: () => { fakeNow += 5000; return fakeNow; } }
    : Date;

  async function fetch(url, init = {}) {
    calls.push({ url, init });
    let body;
    if (url === '/api/game-mode-beta/state') {
      body = { success: true, state: options.backendState || { enabled: true } };
    }
    else if (url === '/api/game-mode-beta/windows/register') body = { cycle_active: false, join_as_cat: false };
    else if (url === '/api/game-mode-beta/settings') body = { auto_cat_on_game: false, game_trigger_mode: 'smart' };
    else body = { success: true, state: { enabled: true } };
    return { ok: true, status: 200, json: async () => body };
  }

  const context = {
    window: win,
    document: doc,
    console,
    CustomEvent: CustomEventLike,
    Event: CustomEventLike,
    Promise,
    Number,
    Math,
    Date: contextDate,
    fetch,
    setTimeout,
    clearTimeout,
    setInterval: () => 1,
    clearInterval: () => {},
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-game-mode-beta.js' });
  return {
    win,
    doc,
    calls,
    pauses,
    tiers,
    goodbyeEvents,
    get activityStarted() { return activityStarted; },
    get activityStopped() { return activityStopped; },
    get vrmPauseAttempts() { return vrmPauseAttempts; },
  };
}

test('registered pet ACKs protection, enters deep sleep, and only click restores', async () => {
  const harness = createHarness();
  await flush();
  const payload = {
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-1',
    trigger_source: 'game_semantic',
    reason: 'exact_game',
    duration_seconds: 10,
  };
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent(payload);
  await flush();

  assert.equal(harness.goodbyeEvents[0].gameModeAuto, true);
  assert.deepEqual(harness.goodbyeEvents[0].edgeAnchor, {
    kind: 'live2d-edge-peek',
    edge: 'top-left',
    side: 'left',
    edgeAnchorRatio: 0.1,
  });

  const ack = harness.calls.find((call) => call.url === '/api/game-mode-beta/ack');
  assert.ok(ack, 'switch ACK should be sent');
  assert.equal(JSON.parse(ack.init.body).status, 'protected');

  harness.win.nekoGameModeBeta.handleLifecycleMessage({
    type: 'game_mode_deep_sleep',
    source: 'game_mode_auto',
    cycle_id: 'cycle-1',
    pet_instance_ids: ['pet-test-1'],
  });
  await flush();
  assert.equal(harness.doc.body.classList.contains('neko-game-mode-deep-sleep'), true);
  assert.equal(harness.tiers.at(-1).tier, 'cat3');
  assert.deepEqual(harness.pauses.sort(), ['live2d', 'mmd', 'vrm']);
  assert.equal(harness.activityStopped, 1);
  assert.ok(harness.calls.some((call) => call.url === '/api/game-mode-beta/deep-sleep-ack'));

  harness.win.dispatchEvent(new CustomEventLike('neko:return-ball-manual-move', {
    detail: { reason: 'return-ball-drag-end', dragCancelled: false },
  }));
  await flush();
  assert.equal(harness.doc.body.classList.contains('neko-game-mode-deep-sleep'), true);
  assert.equal(harness.calls.some((call) => call.url === '/api/game-mode-beta/manual-restore'), false);

  harness.win.dispatchEvent(new CustomEventLike('live2d-return-click', { detail: { source: 'user' } }));
  await flush();
  assert.ok(harness.calls.some((call) => call.url === '/api/game-mode-beta/manual-restore'));
  assert.equal(harness.doc.body.classList.contains('neko-game-mode-deep-sleep'), false);
  assert.equal(harness.activityStarted, 1);
});

test('blocked return keeps manual restore ownership and skips backend ACK', async () => {
  const harness = createHarness({
    blockReturn: true,
    expireReturnWaitImmediately: true,
  });
  await flush();
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent({
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-blocked-return',
    trigger_source: 'resource_pressure',
    reason: 'cpu',
  });
  await flush();

  harness.win.dispatchEvent(new CustomEventLike('live2d-return-click', {
    detail: { source: 'user' },
  }));
  await flush();

  assert.equal(
    harness.calls.some((call) => call.url === '/api/game-mode-beta/manual-restore'),
    false,
  );
  assert.equal(harness.win.nekoGameModeBeta.getState().autoSwitched, true);
  assert.equal(
    harness.win.nekoGameModeBeta.getState().currentCycleId,
    'cycle-blocked-return',
  );
});

test('deep sleep retries an idempotent throttle step once', async () => {
  const harness = createHarness({ failFirstVrmPause: true });
  await flush();
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent({
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-retry',
    trigger_source: 'resource_pressure',
    reason: 'cpu',
  });
  await flush();
  harness.win.nekoGameModeBeta.handleLifecycleMessage({
    type: 'game_mode_deep_sleep',
    source: 'game_mode_auto',
    cycle_id: 'cycle-retry',
    pet_instance_ids: ['pet-test-1'],
  });
  await flush();

  assert.equal(harness.vrmPauseAttempts, 2);
  const deepSleepAck = harness.calls.find((call) => call.url === '/api/game-mode-beta/deep-sleep-ack');
  assert.equal(JSON.parse(deepSleepAck.init.body).success, true);
});

test('failed model restore keeps the pet protected for another click', async () => {
  const harness = createHarness({ invalidateLoad: true, failModelRestore: true });
  await flush();
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent({
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-restore-failure',
    trigger_source: 'game_semantic',
    reason: 'exact_game',
  });
  await flush();

  harness.win.dispatchEvent(new CustomEventLike('live2d-return-click', { detail: { source: 'user' } }));
  await flush();

  assert.equal(harness.win.live2dManager._goodbyeClicked, true);
  assert.equal(harness.win.nekoGameModeBeta.getState().autoSwitched, true);
  assert.equal(harness.calls.some((call) => call.url === '/api/game-mode-beta/manual-restore'), false);
});

test('failed switch restores a pet that the frontend already moved into cat form', async () => {
  const harness = createHarness();
  await flush();
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent({
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-ack-failed',
    trigger_source: 'resource_pressure',
    reason: 'cpu',
  });
  await flush();

  harness.win.nekoGameModeBeta.handleLifecycleMessage({
    type: 'game_mode_switch_failed',
    source: 'game_mode_auto',
    cycle_id: 'cycle-ack-failed',
  });
  await flush();

  assert.equal(harness.win.live2dManager._goodbyeClicked, false);
  assert.equal(harness.win.nekoGameModeBeta.getState().autoSwitched, false);
  assert.equal(harness.calls.some((call) => call.url === '/api/game-mode-beta/manual-restore'), false);
});

test('blocked model reload preserves protection and skips manual restore', async () => {
  const harness = createHarness({ invalidateLoad: true, blockModelRestore: true });
  await flush();
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent({
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-blocked-restore',
    trigger_source: 'game_semantic',
    reason: 'exact_game',
  });
  await flush();

  harness.win.dispatchEvent(new CustomEventLike('live2d-return-click', { detail: { source: 'user' } }));
  await flush();

  assert.equal(harness.win.live2dManager._goodbyeClicked, true);
  assert.equal(harness.win.nekoGameModeBeta.getState().autoSwitched, true);
  assert.equal(harness.calls.some((call) => call.url === '/api/game-mode-beta/manual-restore'), false);
});

test('hostless refresh rejoins an active protected cycle', async () => {
  const harness = createHarness({
    hostless: true,
    backendState: {
      enabled: true,
      cycle_id: 'cycle-hostless-refresh',
      cycle_phase: 'protected',
      trigger_reason: { metric: 'cpu' },
    },
  });
  await flush();

  assert.equal(harness.goodbyeEvents.length, 1);
  assert.equal(harness.goodbyeEvents[0].reason, 'join-active-cycle');
  assert.equal(harness.win.nekoGameModeBeta.getState().currentCycleId, 'cycle-hostless-refresh');
  assert.equal(harness.win.nekoGameModeBeta.getState().autoSwitched, true);
});

test('disabling game mode clears cancelled model load protection', async () => {
  const harness = createHarness({ invalidateLoad: true });
  await flush();
  harness.win.nekoGameModeBeta.handleAutoSwitchEvent({
    type: 'game_mode_auto_switch',
    source: 'game_mode_auto',
    cycle_id: 'cycle-disable',
    trigger_source: 'game_semantic',
    reason: 'exact_game',
  });
  await flush();

  assert.equal(harness.win.vrmManager._nekoGameModeReloadRequired, true);
  await harness.win.nekoGameModeBeta.setEnabled(false);
  assert.equal(harness.win.vrmManager._nekoGameModeReloadRequired, false);
  assert.equal(harness.win.vrmManager._nekoGameModeLoadCancelReason, '');
});
