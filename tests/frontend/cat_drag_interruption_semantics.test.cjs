const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const dragPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js');
const journeyPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js');
const actionsPath = path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-actions-and-audio.js');

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
    _cancelNekoIdleCat1StretchAction: (_button, options) => cancellations.push({ action: 'stretch', options }),
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
  assert.deepEqual(cancellations.map((item) => item.action), ['eat', 'stretch', 'play']);
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

test('journey-local play and stretch report only their own completion feedback', () => {
  let state;
  let randomValue = 0.1;
  let terminalCallback;
  const observations = [];
  const profile = {
    idleSubstate: 'idle',
    tier: 'cat1',
  };
  const button = {};
  const context = {
    Math: { random: () => randomValue },
    Date: { now: () => 1234 },
    _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE: 'minimized-side',
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY: 0.25,
    _NEKO_IDLE_RETURN_TRANSITION_MS: 500,
    _NEKO_CAT_MIND_ACTION_RESULTS: { DONE: 'done' },
    _NEKO_CAT_IDLE_OBSERVATION_TYPES: {
      CAT1_WALK_DONE_NEAR_CHAT: 'cat1_walk_done_near_chat',
      CAT1_LOCAL_PLAY_DONE: 'cat1_local_play_done',
      CAT1_LOCAL_PLAY_CANCELLED: 'cat1_local_play_cancelled',
      CAT1_STRETCH_DONE_NEAR_CHAT: 'cat1_stretch_done_near_chat',
    },
    _getNekoIdleCat1Journey: () => state,
    _isNekoIdleCat1StretchActionActive: () => false,
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
    _playNekoIdleCat1StretchAction: (_button, options) => {
      terminalCallback = options.onTerminal;
      return true;
    },
    _getNekoIdleChatMinimizedRect: () => null,
    _getNekoIdleChatCompactSurfaceRect: () => null,
    _scheduleNekoIdleCat1JourneySync: () => {},
    _cancelNekoIdleCat1PairMove: () => {},
    setTimeout: () => 1,
    _setNekoIdleCat1Classes: () => {},
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _completeNekoIdleCat1JourneyStretch',
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
  randomValue = 0.1;
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

  reset();
  randomValue = 0.9;
  context._finishNekoIdleCat1Walk(button);
  assert.equal(observations.length, 1);
  assert.equal(observations[0].type, 'cat1_walk_done_near_chat');
  assert.equal(typeof terminalCallback, 'function');
  terminalCallback('done', { reason: 'cat1-stretch-action-finished' });
  assert.equal(observations.length, 2);
  assert.equal(observations[1].type, 'cat1_stretch_done_near_chat');

  reset();
  randomValue = 0.9;
  context._finishNekoIdleCat1Walk(button);
  terminalCallback('interrupted', { reason: 'return-ball-drag-active' });
  assert.equal(observations.length, 1);
});

test('independent stretch runner and hiss entry keep hard gates and one owned lifecycle', async () => {
  let edgePeekActive = true;
  let scheduled = null;
  let journeySyncs = 0;
  const runtimeGate = {
    validCatRuntime: true,
    tier: 'cat1',
    returnPending: false,
    dragPending: false,
    dragging: false,
    transitionActive: false,
    activeIndependentAction: false,
    returnBallVisible: true,
    chatSurfaceDragging: false,
    yarnDragActive: false,
    yarnSettling: false,
  };
  const artSources = [];
  const hissSounds = [];
  const soundStops = [];
  const terminals = [];
  const buttonClasses = new Map();
  const containerClasses = new Map();
  const makeClassList = (store) => ({
    toggle(name, active) {
      store.set(name, active === true);
    },
  });
  const art = {};
  const container = {
    style: { display: '' },
    classList: makeClassList(containerClasses),
  };
  const button = {
    classList: makeClassList(buttonClasses),
    getAttribute: () => 'cat1',
    querySelector: () => art,
    __nekoIdleCat1Journey: {
      pairMovePlan: null,
      pairMoveFrame: 0,
    },
  };
  const context = {
    window: {},
    Date: { now: () => 1000 },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_CAT1_STRETCH_FINAL_HOLD_MS: 700,
    _NEKO_IDLE_CAT1_CHAT_HISS_SOUND_URL: 'hiss.mp3',
    _NEKO_IDLE_CAT1_CHAT_HISS_SOUND_VOLUME: 0.12,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoIdleReturnDragActionBlocking: () => false,
    _isAnyNekoIdleReturnDragActionBlocking: () => false,
    _isNekoIdleCompactSurfaceDragging: () => false,
    _isNekoCatMindReturnPending: () => false,
    _isAnyNekoCatMindReturnPending: () => false,
    _isNekoCatMindTransitionActive: () => false,
    _isNekoIdleCat1EdgePeekActive: () => edgePeekActive,
    _isNekoIdleCat1EatActionActive: () => false,
    _isNekoIdleCat1PlayActionActive: () => false,
    _isAnyNekoIdleCat1IndependentActionActive: () => false,
    _getNekoIdleReturnContainerFromButton: () => container,
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton: () => {},
    _isNekoIdleReturnDragActionActive: () => false,
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _setNekoIdleReturnArtSource: (_art, src, tier, options) => artSources.push({ src, tier, options }),
    _resumeNekoIdleCat1Journey: () => {},
    _scheduleNekoIdleCat1JourneySync: () => { journeySyncs += 1; },
    _getNekoCatMindCancelResult: (reason, finishedReason) => reason === finishedReason ? 'done' : 'interrupted',
    _clearNekoIdleHoverPlayback: () => {},
    _cleanupNekoIdleArtTransition: () => {},
    _clearNekoIdleGifPlaybackSource: () => {},
    _getNekoIdleCat1StretchAssetUrl: () => 'stretch.gif',
    _findNekoCatMindVisibleButtonForTier: () => button,
    _getNekoCatMindRuntimeGateSnapshot: () => ({
      ...runtimeGate,
      edgePeekActive,
    }),
    _getNekoIdleGifDurationMs: () => Promise.resolve(900),
    _playNekoIdleSound: (state, src, volume) => {
      hissSounds.push({ state, src, volume });
      return {};
    },
    _stopNekoIdleSoundAudio: (state) => soundStops.push(state),
    _forEachNekoIdleReturnButton: () => {},
    setTimeout(callback, delay) {
      scheduled = { callback, delay };
      return 1;
    },
    clearTimeout: () => {},
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    actionsPath,
    'function _getNekoIdleCat1StretchActionState',
    'function _getNekoIdleCat1PlayActionState'
  ), context);

  assert.equal(typeof context.window.NekoCatIdlePresentation.requestCat1HissStretch, 'function');
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  assert.equal(context._playNekoIdleCat1StretchAction(button), false);
  assert.equal(artSources.length, 0);
  assert.equal(hissSounds.length, 0);

  edgePeekActive = false;
  runtimeGate.yarnDragActive = true;
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  runtimeGate.yarnDragActive = false;
  runtimeGate.yarnSettling = true;
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  runtimeGate.yarnSettling = false;
  runtimeGate.activeIndependentAction = true;
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  runtimeGate.activeIndependentAction = false;
  button.__nekoIdleCat1Journey.pairMovePlan = {};
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  button.__nekoIdleCat1Journey.pairMovePlan = null;
  button.__nekoIdleCat1Journey.pairMoveFrame = 1;
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  button.__nekoIdleCat1Journey.pairMoveFrame = 0;
  assert.equal(hissSounds.length, 0);

  assert.equal(context._playNekoIdleCat1StretchAction(button, {
    onTerminal: (result, detail) => terminals.push({ result, detail }),
  }), true);
  assert.equal(button.__nekoIdleCat1StretchActionState.active, true);
  assert.equal(buttonClasses.get('is-cat1-stretching'), true);
  assert.equal(artSources[0].src, 'stretch.gif');
  assert.equal(artSources[0].tier, 'cat1');
  assert.equal(artSources[0].options.animate, true);

  await Promise.resolve();
  assert.equal(scheduled.delay, 1600);
  scheduled.callback();

  assert.equal(button.__nekoIdleCat1StretchActionState.active, false);
  assert.equal(buttonClasses.get('is-cat1-stretching'), false);
  assert.equal(containerClasses.get('is-cat1-stretching'), false);
  assert.equal(artSources.at(-1).src, 'idle.gif');
  assert.equal(journeySyncs, 1);
  assert.equal(soundStops.length, 1);
  assert.equal(terminals.length, 1);
  assert.equal(terminals[0].result, 'done');
  assert.equal(terminals[0].detail.reason, 'cat1-stretch-action-finished');

  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), true);
  assert.equal(hissSounds.length, 1);
  assert.equal(hissSounds[0].state, button.__nekoIdleCat1StretchActionState);
  assert.equal(hissSounds[0].src, 'hiss.mp3');
  assert.equal(hissSounds[0].volume, 0.12);
  await Promise.resolve();
  scheduled.callback();
  assert.equal(button.__nekoIdleCat1StretchActionState.active, false);
  assert.equal(soundStops.length, 2);
});
