const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const dragPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js');
const journeyPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js');

function sourceBetween(filePath, startMarker, endMarker) {
  const source = fs.readFileSync(filePath, 'utf8');
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${path.basename(filePath)} helper slice not found`);
  return source.slice(start, end);
}

function loadPairMoveActivityHelpers(context) {
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _easeNekoIdleCat1PairMove',
    'function _dispatchNekoIdleCat1MotionInputRegionState'
  ), context);
}

function loadWalkActivityHelpers(context) {
  vm.runInContext(sourceBetween(
    journeyPath,
    'let _nekoIdleCat1WalkActivitySequence',
    'function _setNekoIdleCat1PairMoveChatPosition'
  ), context);
}

test('partially applied small_move reports one cancelled terminal with actual run facts', () => {
  let now = 1000;
  const microtasks = [];
  const cancelledFrames = [];
  const reports = [];
  const container = { style: {} };
  const context = {
    Math,
    Date: { now: () => now },
    Promise,
    _NEKO_CAT_MIND_ACTION_IDS: { CAT1_SMALL_MOVE: 'cat1_small_move' },
    _setNekoIdleCat1ContainerPosition: (_container, left, top) => {
      container.style.left = left;
      container.style.top = top;
    },
    _dispatchNekoIdleDesktopChatPairMoveBounds: () => {},
    _setNekoIdleCat1PairMoveChatPosition: () => {},
    _dispatchNekoIdleCat1MotionInputRegionState: () => {},
    _getNekoCatMindCancelResult: () => 'cancelled',
    _reportNekoCatMindStateActionResult: (state, result, detail) => {
      reports.push({ result, detail });
      state.catMindActionId = '';
      state.catMindRunId = '';
    },
    clearTimeout: () => {},
    window: {
      cancelAnimationFrame: (frame) => cancelledFrames.push(frame),
      queueMicrotask: (callback) => microtasks.push(callback)
    }
  };
  vm.createContext(context);
  loadPairMoveActivityHelpers(context);
  vm.runInContext(sourceBetween(
    dragPath,
    'function _queueNekoCatMindSmallMoveCancelledResult',
    'function _interruptNekoIdleCat1PairMoveForRetarget'
  ), context);

  const plan = {
    activityId: '',
    chatMode: 'solo',
    container,
    catStartLeft: 10,
    catStartTop: 20,
    dx: 60,
    dy: 80,
    durationMs: 1000
  };
  const run = { runId: 'cat1_small_move:17', startedAt: 1000 };
  context._beginNekoIdleCat1PairMoveActivity(plan, run);
  now = 1250;
  context._applyNekoIdleCat1PairMovePlan(plan, 0.5);

  const state = {
    pairMovePlan: plan,
    pairMoveFrame: 73,
    pairMoveTimer: 0,
    pairMoveToken: 1,
    catMindActionId: 'cat1_small_move',
    catMindRunId: run.runId,
    catMindRequestId: 'request-17',
    catMindStartedAt: run.startedAt,
    catMindSource: 'cat_mind',
    catMindTier: 'cat1',
    profile: { tier: 'cat1' }
  };
  context._cancelNekoIdleCat1PairMove(state, { reason: 'provider-state-changed' });

  assert.equal(state.pairMovePlan, null);
  assert.deepEqual(cancelledFrames, [73]);
  assert.equal(reports.length, 0);
  assert.equal(microtasks.length, 1);
  microtasks.shift()();

  assert.equal(reports.length, 1);
  assert.equal(reports[0].result, 'cancelled');
  assert.equal(reports[0].detail.runId, run.runId);
  assert.equal(reports[0].detail.requestId, 'request-17');
  assert.equal(reports[0].detail.detail.activityId, run.runId);
  assert.equal(reports[0].detail.detail.distancePx, 50);
  assert.equal(reports[0].detail.detail.displacementPx, 50);
  assert.equal(reports[0].detail.detail.pathDistancePx, 50);
  assert.equal(reports[0].detail.detail.durationMs, 250);
  assert.equal(reports[0].detail.detail.plannedDurationMs, 1000);
  assert.equal(reports.some((report) => report.result === 'done'), false);

  context._cancelNekoIdleCat1PairMove(state, { reason: 'duplicate-cancel' });
  assert.equal(reports.length, 1);
});

test('compact-top-edge completion forwards the completed walk activity facts', () => {
  let now = 3000;
  const observations = [];
  const profile = {
    idleSubstate: 'idle',
    tier: 'cat1',
    assets: { idle: () => 'idle.gif' }
  };
  const state = {
    profile,
    target: { left: 6, top: 8 },
    targetKind: 'compact-top-edge',
    compactTopEdgeSettleToken: 4,
    substate: 'walking',
    actionSettled: false,
    compactTopEdgeRearmRequired: true
  };
  const button = {
    __nekoIdleReturnSubactionState: state,
    querySelector: () => null
  };
  const context = {
    Math,
    Date: { now: () => now },
    Object,
    _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW: profile,
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_RETURN_TRANSITION_MS: 800,
    _NEKO_CAT_IDLE_OBSERVATION_TYPES: { CAT1_COMPACT_TOP_EDGE_DONE: 'cat1_compact_top_edge_done' },
    _getNekoIdleCat1Journey: () => state,
    _getNekoIdleChatCompactSurfaceRect: () => ({ left: 0, top: 0, width: 100, height: 20 }),
    _cancelNekoIdleCat1Frame: () => {},
    _dispatchNekoIdleCat1MotionInputRegionState: () => {},
    _invalidateNekoIdleCat1CompactTopEdgeSettle: () => {},
    _cancelNekoIdleReturnPendingWalk: () => {},
    _cancelNekoIdleCat1PairMove: () => {},
    _resetNekoIdleCat1WalkSpeed: () => {},
    _rememberNekoIdleCat1CompactFollowAnchor: () => {},
    _rememberNekoIdleCat1CompactFollowSurface: () => {},
    _getNekoIdleNowMs: () => now,
    _setNekoIdleCat1Classes: () => {},
    _dispatchNekoCatIdleObservationSource: (type, detail) => observations.push({ type, detail }),
    _setNekoIdleReturnArtSource: () => {},
    setTimeout: () => 1
  };
  vm.createContext(context);
  loadWalkActivityHelpers(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _finishNekoIdleCat1CompactTopEdgeWalk',
    'function _pickNekoIdleWeightedDelayMs'
  ), context);

  context._beginNekoIdleCat1WalkActivity(state, { left: 0, top: 0 });
  const activityId = state.walkActivity.activityId;
  context._appendNekoIdleCat1WalkActivityPoint(state, 3, 4);
  context._appendNekoIdleCat1WalkActivityPoint(state, 6, 8);
  now = 3600;
  context._finishNekoIdleCat1CompactTopEdgeWalk(button);

  assert.equal(state.walkActivity, null);
  assert.equal(observations.length, 1);
  assert.equal(observations[0].type, 'cat1_compact_top_edge_done');
  assert.equal(observations[0].detail.activityId, activityId);
  assert.equal(observations[0].detail.pathDistancePx, 10);
  assert.equal(observations[0].detail.displacementPx, 10);
  assert.equal(observations[0].detail.durationMs, 600);
});

test('journey cancellation settles a travelled path once and clears zero-distance activity', () => {
  let now = 5000;
  const observations = [];
  const profile = { idleSubstate: 'idle', tier: 'cat1' };
  const context = {
    Math,
    Date: { now: () => now },
    Object,
    Promise,
    _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW: profile,
    _NEKO_CAT_IDLE_OBSERVATION_TYPES: { CAT1_WALK_DONE_NEAR_CHAT: 'cat1_walk_done_near_chat' },
    _NEKO_CAT_MIND_ACTION_IDS: { CAT1_SMALL_MOVE: 'cat1_small_move' },
    _dispatchNekoCatIdleObservationSource: (type, detail) => observations.push({ type, detail }),
    _cancelNekoIdleCat1Frame: () => {},
    _cancelNekoIdleCat1SyncFrame: () => {},
    _invalidateNekoIdleCat1CompactTopEdgeSettle: () => {},
    _cancelNekoIdleReturnPendingWalk: () => {},
    _disconnectNekoIdleCat1Observer: () => {},
    _clearNekoIdleCat1WalkApproachSide: () => {},
    _getNekoIdleReturnContainerFromButton: () => null,
    _setNekoIdleCat1Classes: () => {},
    _setNekoIdleReturnArtSource: () => {},
    clearTimeout: () => {},
    window: {
      cancelAnimationFrame: () => {},
      queueMicrotask: (callback) => callback()
    }
  };
  vm.createContext(context);
  loadWalkActivityHelpers(context);
  vm.runInContext(sourceBetween(
    dragPath,
    'function _settleNekoIdleCat1CancelledWalkActivity',
    'function _scheduleNekoIdleCat1JourneySyncForContainer'
  ), context);

  const state = {
    profile,
    target: { kind: 'minimized-side' },
    targetKind: 'minimized-side',
    substate: 'walking',
    pairMovePlan: null,
    pairMoveFrame: 0
  };
  const button = {
    __nekoIdleReturnSubactionState: state,
    querySelector: () => null
  };
  context._beginNekoIdleCat1WalkActivity(state, { left: 10, top: 20 });
  const activityId = state.walkActivity.activityId;
  context._appendNekoIdleCat1WalkActivityPoint(state, 13, 24);
  now = 5400;
  context._cancelNekoIdleCat1Journey(button, {
    resetArt: false,
    preserveObservers: true,
    reason: 'chat-target-lost'
  });

  assert.equal(state.walkActivity, null);
  assert.equal(observations.length, 1);
  assert.equal(observations[0].type, 'cat1_walk_done_near_chat');
  assert.equal(observations[0].detail.activityId, activityId);
  assert.equal(observations[0].detail.pathDistancePx, 5);
  assert.equal(observations[0].detail.displacementPx, 5);
  assert.equal(observations[0].detail.durationMs, 400);
  assert.equal(observations[0].detail.completed, false);
  assert.equal(observations[0].detail.cancelled, true);
  assert.equal(observations[0].detail.reason, 'chat-target-lost');

  context._beginNekoIdleCat1WalkActivity(state, { left: 13, top: 24 });
  now = 5500;
  context._cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
  assert.equal(state.walkActivity, null);
  assert.equal(observations.length, 1);
});
