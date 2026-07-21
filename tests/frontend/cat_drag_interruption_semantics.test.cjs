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

test('drag pending preserves active runners and real drag motion interrupts all with one reason', () => {
  const cancellations = [];
  const journeyCancellations = [];
  const sounds = [];
  const pendingStates = [];
  const dragStates = [];
  const state = { active: false, token: 0, tier: 'none' };
  const button = {
    __nekoIdleReturnSubactionState: { targetKind: '' },
    getAttribute: () => 'cat1',
  };
  const container = {};
  const context = {
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _NEKO_IDLE_TIER_NONE: 'none',
    _getNekoIdleReturnButtonFromContainer: () => button,
    _cancelNekoIdleCat1EatAction: (_button, options) => cancellations.push({ action: 'eat', options }),
    _cancelNekoIdleCat1PlayAction: (_button, options) => cancellations.push({ action: 'play', options }),
    _logNekoIdleReturnDragDebug: () => {},
    _setNekoIdleReturnDragPendingClasses: (_button, active) => pendingStates.push(active),
    _cancelNekoIdleCat1Journey: (_button, options) => journeyCancellations.push(options),
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _getNekoIdleReturnDragActionState: () => state,
    _resetNekoIdleCat1RapidDragMotion: () => {},
    _pickNekoIdleReturnDragAssetUrl: () => 'drag.gif',
    _setNekoIdleReturnDragActionClasses: (_button, active) => dragStates.push(active),
    _setNekoIdleReturnDragActionArt: () => {},
    _playNekoIdleCat1DragSound: (_tier, options) => sounds.push(options),
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    dragPath,
    'function _prepareNekoIdleReturnDragActionForContainer',
    'function _finishNekoIdleReturnDragAction'
  ), context);

  context._prepareNekoIdleReturnDragActionForContainer(container);
  assert.deepEqual(cancellations, []);
  assert.deepEqual(journeyCancellations, []);
  assert.deepEqual(pendingStates, [true]);

  context._startNekoIdleReturnDragActionForContainer(container);
  assert.deepEqual(cancellations.map((item) => item.action), ['eat', 'play']);
  for (const item of cancellations) {
    assert.equal(item.options.restoreArt, false);
    assert.equal(item.options.reason, 'return-ball-drag-active');
  }
  assert.equal(journeyCancellations.length, 1);
  assert.equal(journeyCancellations[0].reason, 'return-ball-drag-active');
  assert.equal(sounds[0].reason, 'return-ball-drag-active');
  assert.equal(state.active, true);
  assert.deepEqual(dragStates, [true]);
});

test('journey-local play reports real completion feedback without becoming a Cat Mind action result', () => {
  let state;
  let terminalCallback;
  const observations = [];
  const profile = { idleSubstate: 'idle', finishingSubstate: 'stretch' };
  const button = {};
  const context = {
    Math: { random: () => 0.1 },
    Date: { now: () => 1234 },
    _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE: 'minimized-side',
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY: 0.25,
    _NEKO_CAT_MIND_ACTION_RESULTS: { DONE: 'done' },
    _NEKO_CAT_IDLE_OBSERVATION_TYPES: {
      CAT1_WALK_DONE_NEAR_CHAT: 'cat1_walk_done_near_chat',
      CAT1_LOCAL_PLAY_DONE: 'cat1_local_play_done',
      CAT1_LOCAL_PLAY_CANCELLED: 'cat1_local_play_cancelled',
    },
    _getNekoIdleCat1Journey: () => state,
    _cancelNekoIdleCat1Frame: () => {},
    _clearNekoIdleCat1WalkApproachSide: () => {},
    _getNekoIdleReturnContainerFromButton: () => null,
    _dispatchNekoIdleCat1MotionInputRegionState: () => {},
    _resetNekoIdleCat1WalkSpeed: () => {},
    _dispatchNekoCatIdleObservationSource: (type, detail) => observations.push({ type, detail }),
    _playNekoIdleCat1PlayAction: (_button, options) => {
      terminalCallback = options.onTerminal;
      return true;
    },
    _setNekoIdleCat1Classes: () => {},
    _setNekoIdleCat1Substate: () => {},
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _finishNekoIdleCat1Walk',
    'function _finishNekoIdleCat1CompactTopEdgeWalk'
  ), context);

  const reset = () => {
    state = {
      targetKind: 'minimized-side',
      target: null,
      lastStepAt: 0,
      actionSettled: false,
      walkFinishResolution: '',
      substate: 'walking',
      profile,
    };
    terminalCallback = null;
    observations.length = 0;
  };

  reset();
  context._finishNekoIdleCat1Walk(button);
  assert.equal(observations.length, 1);
  assert.equal(observations[0].type, 'cat1_walk_done_near_chat');
  assert.equal(typeof terminalCallback, 'function');
  terminalCallback('done', { reason: 'cat1-play-action-finished' });
  assert.equal(observations.length, 2);
  assert.equal(observations[1].type, 'cat1_local_play_done');

  reset();
  context._finishNekoIdleCat1Walk(button);
  terminalCallback('interrupted', { reason: 'return-ball-drag-active' });
  assert.equal(observations[1].type, 'cat1_local_play_cancelled');
  assert.equal(observations[1].detail.reason, 'return-ball-drag-active');
});
