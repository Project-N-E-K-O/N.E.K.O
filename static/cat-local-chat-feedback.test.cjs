const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadScript(relativePath, context) {
  const source = fs.readFileSync(path.join(__dirname, relativePath), 'utf8');
  vm.runInContext(source, context, { filename: relativePath });
}

function createRuntime(options = {}) {
  const listeners = new Map();
  const timers = [];
  const intervals = [];
  const requests = [];
  const completedActions = [];
  let timerSequence = 0;
  let now = 1000;
  let autoCompleted = false;

  class EventTargetLike {
    addEventListener(type, listener) {
      const bucket = listeners.get(type) || [];
      bucket.push(listener);
      listeners.set(type, bucket);
    }

    dispatchEvent(event) {
      (listeners.get(event.type) || []).slice().forEach(listener => listener.call(window, event));
      return true;
    }
  }

  class CustomEventLike {
    constructor(type, init = {}) {
      this.type = type;
      this.detail = init.detail || {};
    }
  }

  const window = new EventTargetLike();
  window.location = { pathname: '/' };
  window.__appReactChatWindowParts = {
    getCurrentChatSurfaceMode: () => 'compact',
    setCompactChatState() {},
    renderWindow() {},
  };
  window.postGoodbyeChatComposerHiddenState = () => {};
  window.setTimeout = (callback, delay = 0) => {
    timerSequence += 1;
    timers.push({ id: timerSequence, callback, delay: Number(delay) || 0, cancelled: false });
    return timerSequence;
  };
  window.clearTimeout = id => {
    const timer = timers.find(candidate => candidate.id === id);
    if (timer) timer.cancelled = true;
  };
  window.setInterval = (callback, delay = 0) => {
    intervals.push({ callback, delay });
    return intervals.length;
  };
  window.clearInterval = () => {};

  const runtimeGates = Object.assign({
    returnPending: false,
    dragPending: false,
    dragging: false,
    transitionActive: false,
    activeIndependentAction: false,
    returnBallVisible: true,
    validCatRuntime: true,
    chatSurfaceDragging: false,
    yarnDragActive: false,
    yarnSettling: false,
    edgePeekActive: false,
  }, options.gates || {});

  window.NekoCatMindActionProviders = {
    getRuntimeGateSnapshot: () => ({ ...runtimeGates }),
    dryRun: () => ({ allowed: true, reason: 'allowed' }),
  };

  const context = vm.createContext({
    window,
    CustomEvent: CustomEventLike,
    console,
    Date: { now: () => now },
    Math,
    Number,
    Object,
    Array,
    Map,
    Set,
  });

  loadScript('app/app-cat-mind.js', context);

  window.addEventListener('neko:cat-mind:action-request', event => {
    const request = event.detail;
    requests.push(request);
    if (options.autoCompleteFirstAction !== true || autoCompleted) return;
    autoCompleted = true;
    const runId = 'local-text-run-1';
    assert.equal(window.nekoCatMind.acknowledgeActionRequest({
      requestId: request.requestId,
      actionId: request.actionId,
      status: 'accepted',
      runId,
      timestamp: now,
    }), true);
    assert.equal(window.nekoCatMind.acknowledgeActionRequest({
      requestId: request.requestId,
      actionId: request.actionId,
      status: 'started',
      runId,
      timestamp: now,
    }), true);
    now += 1;
    window.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {
      detail: {
        actionId: request.actionId,
        result: 'done',
        reason: 'local-text-feedback-test',
        source: 'cat_mind',
        tier: 'cat1',
        timestamp: now,
        detail: { requestId: request.requestId, runId },
      },
    }));
    completedActions.push(request.actionId);
  });

  window.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: true, source: 'manual-goodbye', timestamp: now },
  }));
  loadScript('app/app-react-chat-window/cat-local-chat-lexicon.js', context);
  loadScript('app/app-react-chat-window/cat-local-chat.js', context);

  function runZeroDelayTimers() {
    let remaining = 100;
    while (remaining > 0) {
      const index = timers.findIndex(timer => !timer.cancelled && timer.delay === 0);
      if (index < 0) return;
      const [timer] = timers.splice(index, 1);
      timer.callback();
      remaining -= 1;
    }
    throw new Error('zero-delay scheduler did not settle');
  }

  return {
    window,
    manager: window.nekoCatLocalChatManager,
    requests,
    completedActions,
    runtimeGates,
    runZeroDelayTimers,
    finishReturn() {
      window.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
        detail: {
          active: false,
          returnCommitted: true,
          returnSource: 'live2d-return-click',
          timestamp: now,
        },
      }));
    },
    setNow(value) { now = value; },
    get now() { return now; },
  };
}

function mergeDose(current, dose) {
  return current + dose * (1 - current) * (1 + 0.8 * current);
}

test('accepted local text adds one bounded feedback per request without retaining text', () => {
  const runtime = createRuntime();
  runtime.runZeroDelayTimers();
  runtime.setNow(2000);

  assert.equal(runtime.manager.submit({ requestId: 'same-ms-a', text: '第一条正文', enteredAt: 1000 }), true);
  assert.equal(runtime.manager.submit({ requestId: 'same-ms-a', text: '重复正文', enteredAt: 1000 }), true);
  assert.equal(runtime.manager.submit({ requestId: 'same-ms-b', text: '第二条正文', enteredAt: 1000 }), true);

  const state = runtime.window.nekoCatMind.getState();
  const expectedSocial = mergeDose(mergeDose(0.22, 0.10), 0.10);
  const expectedStimulation = mergeDose(mergeDose(0.28, 0.10), 0.10);
  assert.ok(Math.abs(state.fields.social_need - expectedSocial) < 1e-12);
  assert.ok(Math.abs(state.fields.stimulation_need - expectedStimulation) < 1e-12);
  assert.equal(state.fields.appetite, 0.22);
  assert.equal(state.fields.sleepiness, 0.12);
  assert.equal(state.fields.energy, 0.75);

  const events = runtime.window.nekoCatMind.getRecentEvents()
    .filter(event => event.type === 'cat_local_text_received');
  assert.equal(events.length, 2);
  assert.deepEqual(Array.from(events, event => event.category), ['user', 'user']);
  assert.deepEqual(Array.from(events, event => event.detail.requestId), ['same-ms-a', 'same-ms-b']);
  assert.equal(JSON.stringify(events).includes('第一条正文'), false);
  assert.equal(JSON.stringify(events).includes('第二条正文'), false);
});

test('Cat Mind deduplicates one local request across timestamps and sources', () => {
  const runtime = createRuntime();
  runtime.runZeroDelayTimers();

  const observe = (requestId, timestamp, source) => runtime.window.nekoCatMind.observe({
    type: 'cat_local_text_received',
    source,
    tier: 'cat1',
    timestamp,
    detail: { requestId, enteredAt: 1000 },
  });

  assert.ok(observe('stable-request', 2000, 'cat-local-chat'));
  assert.equal(observe('stable-request', 3000, 'duplicated-window'), null);
  assert.ok(observe('different-request', 3000, 'duplicated-window'));

  const state = runtime.window.nekoCatMind.getState();
  assert.ok(Math.abs(state.fields.social_need - mergeDose(mergeDose(0.22, 0.10), 0.10)) < 1e-12);
  assert.ok(Math.abs(state.fields.stimulation_need - mergeDose(mergeDose(0.28, 0.10), 0.10)) < 1e-12);
  const events = runtime.window.nekoCatMind.getRecentEvents()
    .filter(event => event.type === 'cat_local_text_received');
  assert.deepEqual(Array.from(events, event => event.detail.requestId), [
    'stable-request',
    'different-request',
  ]);
});

test('one to three local interactions can create an ordinary CAT1 action opportunity', () => {
  const runtime = createRuntime();
  runtime.runZeroDelayTimers();
  assert.equal(runtime.requests.length, 0);

  for (let count = 1; count <= 3 && runtime.requests.length === 0; count += 1) {
    runtime.setNow(1000 + count);
    assert.equal(runtime.manager.submit({
      requestId: 'opportunity-' + count,
      text: '互动 ' + count,
      enteredAt: 1000,
    }), true);
    runtime.runZeroDelayTimers();
  }

  assert.equal(runtime.requests.length, 1);
  assert.ok([
    'cat1_social_ping',
    'cat1_small_move',
    'cat1_play_yarn',
    'cat1_eat_snack',
  ].includes(runtime.requests[0].actionId));
  const candidates = runtime.window.nekoCatMind.getDebugSnapshot().lastDecision.candidates;
  const byId = Object.fromEntries(Array.from(candidates, candidate => [candidate.actionId, candidate]));
  assert.ok(byId.cat1_social_ping.eligibilityScore > byId.cat1_eat_snack.eligibilityScore);
  assert.ok(byId.cat1_small_move.eligibilityScore > byId.cat1_eat_snack.eligibilityScore);
  assert.ok(byId.cat1_play_yarn.eligibilityScore > byId.cat1_eat_snack.eligibilityScore);
});

test('local feedback cannot bypass an existing hard gate', () => {
  const runtime = createRuntime({ gates: { edgePeekActive: true } });
  runtime.runZeroDelayTimers();

  for (let count = 1; count <= 3; count += 1) {
    runtime.setNow(1000 + count);
    assert.equal(runtime.manager.submit({
      requestId: 'edge-' + count,
      text: '贴边时仍能喵 ' + count,
      enteredAt: 1000,
    }), true);
    runtime.runZeroDelayTimers();
  }

  assert.equal(runtime.requests.length, 0);
  assert.equal(runtime.manager.getSnapshot().items.filter(item => item.role === 'user').length, 3);
  assert.ok(runtime.window.nekoCatMind.getState().fields.social_need > 0.22);
  assert.equal(runtime.window.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'edge_peek_active');
});

test('a strict action completed after local text remains in the return summary', () => {
  const runtime = createRuntime({ autoCompleteFirstAction: true });
  runtime.runZeroDelayTimers();

  for (let count = 1; count <= 3 && runtime.completedActions.length === 0; count += 1) {
    runtime.setNow(1000 + count);
    runtime.manager.submit({
      requestId: 'summary-' + count,
      text: '促成动作 ' + count,
      enteredAt: 1000,
    });
    runtime.runZeroDelayTimers();
  }
  assert.equal(runtime.completedActions.length, 1);

  runtime.finishReturn();
  const summary = runtime.window.nekoCatMind.getReturnSummaryDraft();
  assert.ok(summary);
  const expectedHighlight = {
    cat1_social_ping: 'social_ping',
    cat1_eat_snack: 'ate_snack',
    cat1_small_move: 'small_move',
    cat1_play_yarn: 'played_yarn',
  }[runtime.completedActions[0]];
  assert.deepEqual(JSON.parse(JSON.stringify(summary.episode)), {
    kind: 'activity',
    highlight: expectedHighlight,
  });
});
