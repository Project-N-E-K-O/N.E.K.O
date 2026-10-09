const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const catMindSource = fs.readFileSync(
  path.join(projectRoot, 'static', 'app', 'app-cat-mind.js'),
  'utf8'
);

class EventTargetLike {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  dispatchEvent(event) {
    for (const handler of (this.listeners.get(event.type) || []).slice()) {
      handler.call(this, event);
    }
    return true;
  }
}

class CustomEventLike {
  constructor(type, init = {}) {
    this.type = type;
    this.detail = init.detail || {};
  }
}

function createRuntime(allowedActionId, options = {}) {
  let now = 1000;
  const timers = new Map();
  let nextTimerId = 1;
  const requests = [];
  const gates = {
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
  };
  let providerReady = options.providerReady !== false;
  let dryRunCalls = 0;
  const win = new EventTargetLike();
  win.setTimeout = (callback, delayMs = 0) => {
    const id = nextTimerId++;
    timers.set(id, {
      callback,
      dueAt: now + Math.max(0, Number(delayMs) || 0),
    });
    return id;
  };
  win.clearTimeout = (id) => timers.delete(id);
  win.setInterval = () => 1;
  win.clearInterval = () => {};
  const context = {
    window: win,
    CustomEvent: CustomEventLike,
    Date: { now: () => now },
    console,
  };
  vm.createContext(context);
  vm.runInContext(catMindSource, context);
  win.NekoCatMindActionProviders = {
    getRuntimeGateSnapshot() {
      return { ...gates };
    },
    dryRun(actionId) {
      dryRunCalls += 1;
      const allowed = actionId === allowedActionId && providerReady;
      return {
        allowed,
        reason: allowed ? 'allowed' : (actionId === allowedActionId ? 'provider_not_ready' : 'test_disabled'),
      };
    },
  };
  win.addEventListener('neko:cat-mind:action-request', (event) => requests.push(event.detail));

  const flush = () => {
    let remaining = 500;
    while (remaining-- > 0) {
      const due = [...timers.entries()]
        .filter(([, timer]) => timer.dueAt <= now)
        .sort((left, right) => left[1].dueAt - right[1].dueAt || left[0] - right[0])[0];
      if (!due) break;
      timers.delete(due[0]);
      due[1].callback();
    }
    assert.ok(remaining > 0, 'scheduler must remain asynchronous and bounded');
  };
  const observe = (type, detail = {}, tier = 'cat1', source = 'scheduler-test') => {
    now += 1;
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: { type, source, tier, timestamp: now, detail },
    }));
    flush();
  };
  const enter = () => {
    win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
      detail: { active: true, source: 'manual-goodbye', timestamp: now },
    }));
    flush();
  };
  const advanceNeed = (minutes = 15) => {
    now += minutes * 60 * 1000;
    win.dispatchEvent(new CustomEventLike('neko:cat-mind:observation', {
      detail: {
        type: 'cat_elapsed',
        source: 'cat-mind-clock',
        tier: 'cat1',
        timestamp: now,
        detail: { elapsedMs: minutes * 60 * 1000 },
      },
    }));
    flush();
  };
  return {
    win,
    gates,
    requests,
    flush,
    observe,
    enter,
    advanceNeed,
    advanceTime: (milliseconds) => {
      now += milliseconds;
      flush();
    },
    now: () => now,
    setNow: (value) => { now = value; },
    setProviderReady: (value) => { providerReady = value; },
    dryRunCalls: () => dryRunCalls,
  };
}

function startRequest(runtime, request, runId) {
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'accepted',
    runId,
    timestamp: runtime.now(),
  }), true);
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'started',
    runId,
    timestamp: runtime.now(),
  }), true);
}

function reportResult(runtime, request, runId, result, reason, detail = {}) {
  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-mind:action-result', {
    detail: {
      actionId: request.actionId,
      result,
      reason,
      source: 'cat_mind',
      tier: 'cat1',
      timestamp: runtime.now(),
      detail: { requestId: request.requestId, runId, ...detail },
    },
  }));
  runtime.flush();
}

test('active user observations coalesce into one evaluation after terminal settle', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'social-active-run');

  const dryRunsBeforeInput = runtime.dryRunCalls();
  for (let index = 0; index < 20; index += 1) {
    runtime.observe('cat_hover_reaction');
  }
  assert.equal(runtime.dryRunCalls(), dryRunsBeforeInput, 'active runner blocks selector dry-runs');
  const lastEvaluatedAfterInput = runtime.win.nekoCatMind.getDebugSnapshot().scheduler.lastEvaluatedAt;
  for (let index = 0; index < 20; index += 1) {
    runtime.observe('desktop_occlusion_or_layer_change', {
      status: 'changed',
      changes: ['position'],
      movement: { x: index % 2 === 0 ? 1 : -1, y: 0 },
    }, 'cat1', 'desktop-window-sensing');
  }
  assert.equal(
    runtime.win.nekoCatMind.getDebugSnapshot().scheduler.lastEvaluatedAt,
    lastEvaluatedAfterInput,
    'desktop churn must not wake evaluations while user input is already deferred'
  );

  reportResult(runtime, request, 'social-active-run', 'done', 'social-finished');
  assert.equal(
    runtime.dryRunCalls() - dryRunsBeforeInput,
    4,
    'twenty inputs coalesce into one CAT1 selector pass after post-settle'
  );
  assert.ok(
    runtime.win.nekoCatMind.getDebugSnapshot().lastDecision.triggerTypes.includes('cat_hover_reaction')
  );
});

test('desktop sensing churn remains observable without chaining settled actions', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'social-before-desktop-churn');
  reportResult(runtime, request, 'social-before-desktop-churn', 'done', 'social-finished');

  const dryRunsAfterSettle = runtime.dryRunCalls();
  for (let index = 0; index < 20; index += 1) {
    runtime.observe('desktop_occlusion_or_layer_change', {
      status: 'changed',
      changes: ['position'],
      movement: { x: index % 2 === 0 ? 1 : -1, y: 0 },
    }, 'cat1', 'desktop-window-sensing');
  }

  assert.equal(runtime.requests.length, 1, 'desktop facts must not start the next action');
  assert.equal(runtime.dryRunCalls(), dryRunsAfterSettle, 'desktop facts must not wake selector dry-runs');
  assert.equal(
    runtime.win.nekoCatMind.getRecentEvents().at(-1).type,
    'desktop_occlusion_or_layer_change'
  );
});

test('non-native desktop provider changes may wake one retained explicit intent', () => {
  const runtime = createRuntime('cat1_play_yarn', { providerReady: false });
  runtime.enter();
  runtime.observe('chat_yarn_drag_completed', {
    userInitiated: true,
    startedFarFromCat: true,
    endedNearCat: true,
    startDistanceToCatPx: 320,
    endDistanceToCatPx: 20,
    directApproachDistancePx: 300,
    pathDistancePx: 310,
    movementThresholdPx: 24,
  });
  assert.equal(runtime.requests.length, 0);
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().scheduler.providerRecheckNeeded, true);

  runtime.setProviderReady(true);
  runtime.observe('desktop_occlusion_or_layer_change', {
    status: 'changed',
    changes: ['position'],
    movement: { x: 1, y: 0 },
  }, 'cat1', 'return-ball');
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_play_yarn');
});

test('provider-ready presentation wakes retained yarn intent without waiting for clock', () => {
  const runtime = createRuntime('cat1_play_yarn', { providerReady: false });
  runtime.enter();
  runtime.observe('chat_yarn_drag_completed', {
    userInitiated: true,
    startedFarFromCat: true,
    endedNearCat: true,
    startDistanceToCatPx: 320,
    endDistanceToCatPx: 20,
    directApproachDistancePx: 300,
    pathDistancePx: 310,
    movementThresholdPx: 24,
  });
  assert.equal(runtime.requests.length, 0);
  assert.ok(runtime.win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn);

  runtime.setProviderReady(true);
  runtime.observe('cat1_stretch_done_near_chat', { reason: 'stretch-settled' });
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_play_yarn');
  assert.ok(runtime.win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn);

  startRequest(runtime, runtime.requests[0], 'provider-ready-yarn');
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn, undefined);
});

test('desktop window facts stay observable without waking an ordinary Cat Mind action', () => {
  const runtime = createRuntime('cat1_social_ping', { providerReady: false });
  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.requests.length, 0);

  runtime.setProviderReady(true);
  const dryRunsBeforeDesktopFact = runtime.dryRunCalls();
  runtime.observe('desktop_occlusion_or_layer_change', {
    status: 'changed',
    changes: ['position'],
    rect: { x: 100, y: 100, width: 640, height: 480 },
  }, 'cat1', 'desktop-window-sensing');

  assert.equal(runtime.requests.length, 0);
  assert.equal(runtime.dryRunCalls(), dryRunsBeforeDesktopFact);
  assert.equal(
    runtime.win.nekoCatMind.getRecentEvents().at(-1).type,
    'desktop_occlusion_or_layer_change'
  );

  runtime.observe('desktop_occlusion_or_layer_change', {
    visible: true,
  }, 'cat1', 'return-ball');
  assert.equal(runtime.requests.length, 1);
  assert.equal(runtime.requests[0].actionId, 'cat1_social_ping');
});

test('interrupted small move settles its physical facts once and interruption metadata adds no needs', () => {
  const runtime = createRuntime('cat1_small_move');
  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'small-move-interrupted');
  const before = runtime.win.nekoCatMind.getState().fields;

  reportResult(runtime, request, 'small-move-interrupted', 'interrupted', 'return-ball-drag-active', {
    activityId: 'small-move-interrupted',
    pathDistancePx: 80,
    durationMs: 1100,
  });
  const after = runtime.win.nekoCatMind.getState().fields;
  assert.ok(Math.abs(after.appetite - (before.appetite + 0.02)) < 1e-9);
  assert.ok(Math.abs(after.energy - (before.energy - 0.0225)) < 1e-9);
  assert.ok(Math.abs(after.sleepiness - (before.sleepiness + 0.01)) < 1e-9);
  assert.equal(after.social_need, before.social_need);
  assert.equal(after.stimulation_need, before.stimulation_need);
  const recentTypes = runtime.win.nekoCatMind.getDebugSnapshot().recentEvents.map((event) => event.type);
  assert.ok(recentTypes.includes('small_move_cancelled'));
  assert.ok(recentTypes.includes('action_interrupted_by_drag'));
});

test('done before started releases the request without cooldown or completion feedback', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed();
  const request = runtime.requests[0];
  assert.equal(runtime.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'accepted',
    runId: 'done-before-started',
    timestamp: runtime.now(),
  }), true);
  const before = runtime.win.nekoCatMind.getState().fields;

  reportResult(runtime, request, 'done-before-started', 'done', 'protocol-bad-order');
  const state = runtime.win.nekoCatMind.getState();
  assert.deepEqual(state.fields, before);
  assert.equal(state.actionCooldowns.cat1_social_ping, undefined);
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().returnEpisode.preview, null);
  assert.equal(
    runtime.win.nekoCatMind.getDebugSnapshot().scheduler.lastProtocolFailure.type,
    'result_before_started'
  );
});

test('request lease deadlines release pending state and reconsider retained input on time', () => {
  const unacknowledged = createRuntime('cat1_social_ping');
  unacknowledged.enter();
  unacknowledged.advanceNeed();
  assert.equal(unacknowledged.requests.length, 1);
  unacknowledged.observe('cat_hover_reaction');
  assert.equal(
    unacknowledged.win.nekoCatMind.getDebugSnapshot().lastDecision.reason,
    'action_request_pending'
  );
  unacknowledged.setProviderReady(false);
  unacknowledged.advanceTime(4998);
  assert.ok(unacknowledged.win.nekoCatMind.getState().pendingActionRequest);
  unacknowledged.advanceTime(1);
  assert.equal(unacknowledged.win.nekoCatMind.getState().pendingActionRequest, null);
  assert.equal(
    unacknowledged.win.nekoCatMind.getDebugSnapshot().scheduler.lastProtocolFailure.type,
    'request_unacknowledged_timeout'
  );
  assert.equal(unacknowledged.requests.length, 1, 'deadline must not self-retry a failed request');

  const accepted = createRuntime('cat1_social_ping');
  accepted.enter();
  accepted.advanceNeed();
  const request = accepted.requests[0];
  assert.equal(accepted.win.nekoCatMind.acknowledgeActionRequest({
    requestId: request.requestId,
    actionId: request.actionId,
    status: 'accepted',
    runId: 'accepted-without-start',
    timestamp: accepted.now(),
  }), true);
  accepted.observe('cat_hover_reaction');
  accepted.setProviderReady(false);
  accepted.advanceTime(11999);
  assert.equal(accepted.win.nekoCatMind.getState().pendingActionRequest, null);
  assert.equal(
    accepted.win.nekoCatMind.getDebugSnapshot().scheduler.lastProtocolFailure.type,
    'accepted_not_started_timeout'
  );
  assert.equal(accepted.requests.length, 1, 'accepted timeout must not invent a terminal or retry');
});

test('Cat Mind follows the actual cat appearance instead of raw goodbye and return clicks', () => {
  const runtime = createRuntime('cat1_social_ping');

  runtime.win.dispatchEvent(new CustomEventLike('live2d-goodbye-click', {
    detail: { source: 'manual-goodbye', timestamp: runtime.now() },
  }));
  assert.equal(runtime.win.nekoCatMind.getState().active, false);

  runtime.enter();
  runtime.advanceNeed();
  assert.equal(runtime.win.nekoCatMind.getState().active, true);
  assert.ok(runtime.win.nekoCatMind.getState().pendingActionRequest);

  runtime.win.dispatchEvent(new CustomEventLike('live2d-return-click'));
  assert.equal(runtime.win.nekoCatMind.getState().active, true);
  assert.equal(runtime.win.nekoCatMind.getReturnSummaryDraft(), null);

  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: false, reason: 'appearance-change', appearance: 'ball' },
  }));
  const stopped = runtime.win.nekoCatMind.getState();
  assert.equal(stopped.active, false);
  assert.equal(stopped.pendingActionRequest, null);
  assert.equal(stopped.activeAction, null);
  assert.equal(stopped.returnSummaryDraft, null);
  assert.equal(stopped.lastResetReason, 'appearance-change');

  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: true, source: 'goodbye-idle-appearance', tier: 'cat2' },
  }));
  assert.equal(runtime.win.nekoCatMind.getState().active, true);
  assert.equal(runtime.win.nekoCatMind.getState().tier, 'cat2');
});

test('committed real returns preserve one summary for every supported avatar', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();

  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: {
      active: false,
      reason: 'return-commit',
      returnCommitted: true,
      returnSource: 'live2d-return-click',
    },
  }));
  const committed = runtime.win.nekoCatMind.getState();
  assert.equal(committed.active, false);
  assert.ok(committed.returnSummaryDraft);

  runtime.win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: { active: false, reason: 'duplicate-inactive-observation' },
  }));
  assert.ok(
    runtime.win.nekoCatMind.getReturnSummaryDraft(),
    'a duplicate inactive observation must not clear a committed return summary before its consumer runs',
  );
  runtime.win.dispatchEvent(new CustomEventLike('neko:goodbye-state-cleared', {
    detail: { reason: 'character-switch' },
  }));
  assert.equal(runtime.win.nekoCatMind.getReturnSummaryDraft(), null);

  const png = createRuntime('cat1_social_ping');
  png.enter();
  png.win.dispatchEvent(new CustomEventLike('neko:cat-local-active-change', {
    detail: {
      active: false,
      reason: 'return-commit',
      returnCommitted: true,
      returnSource: 'pngtuber-return-click',
    },
  }));
  assert.equal(png.win.nekoCatMind.getState().active, false);
  assert.ok(png.win.nekoCatMind.getReturnSummaryDraft());
});

test('short action burst guard prevents an immediate autonomous repeat', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed(15);
  assert.equal(runtime.requests.length, 1);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'burst-guard-run');
  reportResult(runtime, request, 'burst-guard-run', 'done', 'runner_done');

  runtime.advanceTime(30000);
  runtime.observe('cat_elapsed', { elapsedMs: 30000 }, 'cat1', 'cat-mind-clock');
  assert.equal(runtime.requests.length, 1, 'autonomous tick inside the short guard must not start another action');
});

test('entering idle alone does not arm the short action burst guard', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceTime(10000);
  runtime.observe('cat_elapsed', { elapsedMs: 10000 }, 'cat1', 'cat-mind-clock');
  const decision = runtime.win.nekoCatMind.getDebugSnapshot().lastDecision;
  assert.ok(decision, 'expected an autonomous evaluation');
  assert.notEqual(decision.reason, 'action_start_burst_guard');
});

test('repeated hovers inside the short window wake only one decision but are all observed', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  const lastEvaluatedAt = () => runtime.win.nekoCatMind.getDebugSnapshot().scheduler.lastEvaluatedAt;
  runtime.observe('cat_hover_reaction', { reason: 'return-hover' });
  const firstEvaluatedAt = lastEvaluatedAt();
  assert.equal(firstEvaluatedAt, runtime.now());
  runtime.observe('cat_hover_reaction', { reason: 'subaction-interactive' });
  assert.equal(lastEvaluatedAt(), firstEvaluatedAt, 'a hover inside the window must not wake another decision');
  const hovers = runtime.win.nekoCatMind.getRecentEvents()
    .filter((event) => event.type === 'cat_hover_reaction');
  assert.equal(hovers.length, 2, 'need, intent and episode bookkeeping still see every hover');
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().clock.lastUserInteractionAt, runtime.now());

  runtime.advanceTime(1500);
  runtime.observe('cat_hover_reaction', { reason: 'return-hover' });
  assert.equal(lastEvaluatedAt(), runtime.now(), 'a hover after the window wakes a decision again');
});

test('a deferred user trigger is not held back by the short action burst guard', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed(15);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'deferred-hover-run');
  reportResult(runtime, request, 'deferred-hover-run', 'done', 'runner_done');

  runtime.gates.dragging = true;
  runtime.advanceTime(10000);
  runtime.observe('cat_hover_reaction', { reason: 'return-hover' });
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'dragging');
  runtime.gates.dragging = false;
  runtime.advanceTime(20000);
  runtime.observe('cat_elapsed', { elapsedMs: 30000 }, 'cat1', 'cat-mind-clock');
  assert.notEqual(runtime.win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'action_start_burst_guard');
});

test('the short action burst guard does not hide a hard gate reason', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  runtime.advanceNeed(15);
  const request = runtime.requests[0];
  startRequest(runtime, request, 'hard-gate-run');
  reportResult(runtime, request, 'hard-gate-run', 'done', 'runner_done');

  runtime.gates.yarnDragActive = true;
  runtime.advanceTime(30000);
  runtime.observe('cat_elapsed', { elapsedMs: 30000 }, 'cat1', 'cat-mind-clock');
  assert.equal(runtime.win.nekoCatMind.getDebugSnapshot().lastDecision.reason, 'chat_yarn_dragging');
});

test('identical compact surface facts do not create repeated opportunities', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  const detail = {
    source: 'compact-surface',
    available: true,
    visible: true,
    screenRect: { left: 10, top: 20, width: 80, height: 80 },
    timestamp: runtime.now() + 1,
  };
  runtime.win.dispatchEvent(new CustomEventLike('neko:idle-chat-compact-surface-state', { detail }));
  runtime.win.dispatchEvent(new CustomEventLike('neko:idle-chat-compact-surface-state', {
    detail: { ...detail, timestamp: detail.timestamp + 1 },
  }));
  runtime.flush();
  const compactFacts = runtime.win.nekoCatMind.getRecentEvents()
    .filter((event) => event.type === 'chat_compact_surface_visible');
  assert.equal(compactFacts.length, 1);
});

test('compact surface dedupe ignores the per-notification lifecycle sequence', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  const detail = {
    source: 'compact-surface',
    available: true,
    visible: true,
    screenRect: { left: 10, top: 20, width: 80, height: 80 },
  };
  for (let sequence = 1; sequence <= 3; sequence += 1) {
    runtime.advanceTime(1);
    runtime.win.dispatchEvent(new CustomEventLike('neko:idle-chat-compact-surface-state', {
      detail: { ...detail, timestamp: runtime.now(), lifecycleSequence: sequence },
    }));
    runtime.flush();
  }
  const compactFacts = runtime.win.nekoCatMind.getRecentEvents()
    .filter((event) => event.type === 'chat_compact_surface_visible');
  assert.equal(compactFacts.length, 1);
});

test('position presentation busy is a shared Cat Mind hard gate', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.gates.cat1PositionPresentationBusy = true;
  runtime.enter();
  runtime.advanceNeed(15);
  assert.equal(runtime.requests.length, 0);
  assert.equal(
    runtime.win.nekoCatMind.getDebugSnapshot().lastDecision.reason,
    'cat1_position_presentation_busy',
  );
});

test('compact visibility can recover at the same rect after a geometry-free terminal', () => {
  for (const terminal of [{ visible: false }, { available: false }]) {
    const runtime = createRuntime('cat1_social_ping');
    runtime.enter();
    const visible = {
      source: 'compact-surface', available: true, visible: true,
      screenRect: { left: 10, top: 20, width: 80, height: 80 },
    };
    const send = (detail) => {
      runtime.advanceTime(1);
      runtime.win.dispatchEvent(new CustomEventLike('neko:idle-chat-compact-surface-state', {
        detail: { timestamp: runtime.now(), ...detail },
      }));
      runtime.flush();
    };
    const count = () => runtime.win.nekoCatMind.getRecentEvents()
      .filter((event) => event.type === 'chat_compact_surface_visible').length;
    send(visible);
    send(terminal);
    assert.equal(count(), 1, 'terminal without geometry must not invent a visible observation');
    send(visible);
    assert.equal(count(), 2, 'reopening at the same position must be observable');
    send({ ...terminal, timestamp: runtime.now() - 2 });
    send(visible);
    assert.equal(count(), 2, 'a stale terminal must not clear the newer visible signature');
  }
});

test('compact surface dedupe also reads flat web-host layout geometry', () => {
  const runtime = createRuntime('cat1_social_ping');
  runtime.enter();
  const send = (rect) => {
    runtime.advanceTime(1);
    runtime.win.dispatchEvent(new CustomEventLike('neko:compact-surface-layout-change', {
      detail: { ...rect, dragging: false },
    }));
    runtime.flush();
  };
  const count = () => runtime.win.nekoCatMind.getRecentEvents()
    .filter((event) => event.type === 'chat_compact_surface_visible').length;
  send({ left: 10, top: 20, width: 80, height: 80 });
  send({ left: 10, top: 20, width: 80, height: 80 });
  assert.equal(count(), 1, 'an unchanged flat rect is a duplicate');
  send({ left: 200, top: 20, width: 80, height: 80 });
  assert.equal(count(), 2, 'a moved flat rect is a new observation');
});
