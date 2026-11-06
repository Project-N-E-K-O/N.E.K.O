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

test('exploration cleanup cancels a journey even when an action already paused it', () => {
  let cleanupListener = null;
  let cancelCount = 0;
  let forceCleanupCount = 0;
  const state = { substate: 'walking', paused: true };
  const button = { __nekoIdleCat1Journey: state };
  const context = {
    window: {
      addEventListener(type, listener) {
        if (type === 'neko:pet-care:cleanup-request') cleanupListener = listener;
      },
    },
    _NEKO_CAT_MIND_ACTION_IDS: { CAT1_SMALL_MOVE: 'cat1_small_move' },
    _forEachNekoIdleReturnButton: callback => callback(button),
    _cancelNekoIdleCat1Journey: (_button, options) => {
      cancelCount += 1;
      assert.equal(options.resetArt, false);
      assert.equal(options.preserveObservers, false);
      state.paused = false;
      state.substate = 'idle';
    },
    _forceNekoIdleCat1LayerCleanup: () => { forceCleanupCount += 1; },
    _getNekoIdleReturnContainerFromButton: () => ({}),
    _isNekoIdleReturnDragActionActive: () => false,
    _finishNekoIdleReturnDragAction: () => {},
    _clearNekoIdleCat1EdgePeek: () => {},
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    dragPath,
    '// Stage 0 pet-care compatibility cleanup',
    'function _interruptNekoIdleCat1PairMovesForRetarget'
  ), context);

  assert.equal(typeof cleanupListener, 'function');
  cleanupListener({ detail: { source: 'exploration', reason: 'exploration-away' } });
  assert.equal(cancelCount, 1);
  assert.equal(forceCleanupCount, 1);
  assert.equal(state.paused, false);
  assert.equal(state.substate, 'idle');
});

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
    _cancelNekoIdleCat1PlayTeaserWandAction: (_button, options) => cancellations.push({ action: 'play-teaser-wand', options }),
    _cancelNekoIdleCat1PettingAction: (_button, options) => cancellations.push({ action: 'petting', options }),
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
  assert.deepEqual(cancellations.map((item) => item.action), ['eat', 'stretch', 'play', 'play-teaser-wand', 'petting']);
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
  const teaserRequests = [];
  let hissCapabilityAvailable = false;
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
    _NEKO_IDLE_CAT1_CHAT_HISS_SOUND_VOLUME: 0.12,
    _getNekoCatVoiceUrl: () => 'hiss.mp3',
    _getNekoCatAppearanceUrl: () => 'stretch.gif',
    _isNekoCatActionResourceAvailable: () => hissCapabilityAvailable,
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
    _playNekoIdleCat1PlayTeaserWandAction: (target, options) => {
      teaserRequests.push({ target, options });
      return true;
    },
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
  assert.equal(typeof context.window.NekoCatIdlePresentation.requestCat1PlayTeaserWand, 'function');
  assert.equal(Object.hasOwn(context.window.NekoCatIdlePresentation, 'requestCat1Play'), false);
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1HissStretch(), false);
  assert.equal(context._playNekoIdleCat1StretchAction(button), false);
  assert.equal(artSources.length, 0);
  assert.equal(hissSounds.length, 0);

  hissCapabilityAvailable = true;
  edgePeekActive = false;

  const teaserTerminal = () => {};
  assert.equal(context.window.NekoCatIdlePresentation.requestCat1PlayTeaserWand({
    source: 'pet-care-toy', minimumDurationMs: 8000, onTerminal: teaserTerminal,
  }), true);
  assert.equal(teaserRequests.length, 1);
  assert.equal(teaserRequests[0].target, button);
  assert.equal(teaserRequests[0].options.source, 'pet-care-toy');
  assert.equal(teaserRequests[0].options.minimumDurationMs, 8000);
  assert.equal(teaserRequests[0].options.onTerminal, teaserTerminal);

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

  context._getNekoCatAppearanceUrl = () => '';
  assert.equal(context._playNekoIdleCat1StretchAction(button), false);
});

test('user yarn runner uses its authorized duration floor and never completes after cancellation or failure', async () => {
  let now = 1000;
  let gifDuration = Promise.resolve(5000);
  let scheduled = null;
  let timerSequence = 0;
  const terminals = [];
  const artSources = [];
  const buttonClasses = new Map();
  const containerClasses = new Map();
  const art = { setAttribute() {} };
  const container = {
    style: { display: '' },
    classList: { toggle: (name, active) => containerClasses.set(name, active === true) },
  };
  const button = {
    classList: {
      toggle: (name, active) => buttonClasses.set(name, active === true),
      contains: () => false,
    },
    getAttribute: () => 'cat1',
    querySelector: () => art,
  };
  const context = {
    Date: { now: () => now },
    Math,
    Number,
    Promise,
    window: { innerWidth: 1280, innerHeight: 720 },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_TIER_NONE: 'none',
    _NEKO_IDLE_CAT1_PLAY_ASSET_URL: 'play-yarn.gif',
    _NEKO_IDLE_CAT1_PLAY_SOUND_VOLUME: 0.2,
    _NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR: 'data-finishing',
    _NEKO_IDLE_CAT1_PLAY_YARN_RELEASE_SIZE_PX: 88,
    _NEKO_IDLE_CAT1_NATIVE_YARN_VISIBLE_SIZE_PX: 58,
    _NEKO_CAT_MIND_ACTION_IDS: { CAT1_PLAY_YARN: 'cat1_play_yarn' },
    _NEKO_CAT_MIND_ACTION_RESULTS: {
      DONE: 'done', CANCELLED: 'cancelled', INTERRUPTED: 'interrupted',
    },
    _isNekoCatActionResourceAvailable: () => true,
    _isNekoIdleDesktopWindowInteractionActionActive: () => false,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoIdleReturnDragActionActive: () => false,
    _isNekoIdleCat1PlayActionActive: (target) => !!target.__nekoIdleCat1PlayActionState?.active,
    _isNekoIdleCat1StretchActionActive: () => false,
    _isNekoIdleCat1EatActionActive: () => false,
    _isNekoIdleCat1PlayTeaserWandActionActive: () => false,
    _isNekoIdleCat1PettingActionActive: () => false,
    _isNekoIdleCat1PlaygroundDropLifecycleActive: () => false,
    _getNekoIdleReturnContainerFromButton: () => container,
    _cancelNekoIdleCat1EatAction: () => {},
    _pauseNekoIdleCat1Journey: () => {},
    _resumeNekoIdleCat1Journey: () => {},
    _scheduleNekoIdleCat1JourneySync: () => {},
    _stopNekoIdleCat1AmbientSoundAudio: () => {},
    _fadeOutNekoIdleCat1DragSound: () => {},
    _clearNekoIdleHoverPlayback: () => {},
    _cleanupNekoIdleArtTransition: () => {},
    _clearNekoIdleGifPlaybackSource: () => {},
    _setNekoIdleReturnArtSource: (_art, src) => artSources.push(src),
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _syncNekoIdleCat1CompactMirrorReaction: () => {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton: () => {},
    _syncNekoIdleCat1AmbientSoundForTier: () => {},
    _getNekoIdleReactChatMinimizedShell: () => null,
    _getNekoIdleCurrentLanlanName: () => '',
    _isNekoIdleCat1NativeWaylandSelfBallRuntime: () => false,
    _beginNekoCatMindStateAction: (_state, actionId, tier, options) => ({
      actionId, tier, source: options.source, requestId: options.requestId,
      activityId: options.activityId, runId: `${options.requestId}:run`, startedAt: now,
    }),
    _notifyNekoCatMindRunnerAccepted: () => {},
    _notifyNekoCatMindRunnerStarted: () => {},
    _getNekoCatMindCancelResult: (reason, finishedReason) => reason === finishedReason
      ? 'done' : (/drag|return|tier|container|cat-to-model/.test(String(reason))
        ? 'interrupted' : 'cancelled'),
    _reportNekoCatMindStateActionResult: (_state, result, detail) => {
      terminals.push({ result, detail });
      return true;
    },
    _stopNekoIdleSoundAudio: () => {},
    _getNekoIdleGifDurationMs: () => gifDuration,
    _getNekoCatVoiceUrl: () => 'play-yarn.mp3',
    _playNekoIdleSound: () => null,
    setTimeout(callback, delay) {
      const timer = { id: ++timerSequence, callback, delay, cleared: false };
      scheduled = timer;
      return timer;
    },
    clearTimeout(timer) { if (timer) timer.cleared = true; },
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    actionsPath,
    'function _getNekoIdleCat1PlayActionState',
    'function _getNekoIdleCat1PlayTeaserWandActionState'
  ), context);

  const start = async (suffix, minimumDurationMs) => {
    scheduled = null;
    const options = {
      source: 'cat_mind', requestId: `request-${suffix}`, activityId: `activity-${suffix}`,
    };
    if (minimumDurationMs !== undefined) options.minimumDurationMs = minimumDurationMs;
    assert.equal(context._playNekoIdleCat1PlayAction(button, options), true);
    assert.equal(artSources.at(-1), 'play-yarn.gif');
    await Promise.resolve();
    return scheduled;
  };

  gifDuration = Promise.resolve(5000);
  let timer = await start('eight-seconds', 8000);
  assert.equal(timer.delay, 8000);
  assert.equal(terminals.length, 0);
  timer.callback();
  assert.equal(terminals.at(-1).result, 'done');

  gifDuration = Promise.resolve(10860);
  timer = await start('twenty-nine-seconds', 29000);
  assert.equal(timer.delay, 29000);
  timer.callback();
  assert.equal(terminals.at(-1).result, 'done');

  gifDuration = Promise.resolve(5000);
  timer = await start('cancelled', 29000);
  context._cancelNekoIdleCat1PlayAction(button, { reason: 'user-cancelled' });
  const afterCancel = terminals.length;
  assert.equal(terminals.at(-1).result, 'cancelled');
  timer.callback();
  assert.equal(terminals.length, afterCancel, 'cancelled floor timer cannot emit late done');

  timer = await start('timed-out', 29000);
  context._cancelNekoIdleCat1PlayAction(button, { reason: 'cat-mind-action-timeout' });
  const afterTimeout = terminals.length;
  assert.equal(terminals.at(-1).result, 'cancelled');
  timer.callback();
  assert.equal(terminals.length, afterTimeout, 'timed-out floor timer cannot emit late done');

  gifDuration = Promise.reject(new Error('duration unavailable'));
  timer = await start('duration-failed', 8000);
  assert.equal(timer, null);
  assert.equal(terminals.at(-1).result, 'cancelled');
  assert.equal(terminals.at(-1).detail.reason, 'cat1-play-action-duration-failed');

  gifDuration = Promise.resolve(5000);
  timer = await start('legacy-no-floor');
  assert.equal(timer.delay, 5000);
  timer.callback();
  assert.equal(terminals.at(-1).result, 'done');
});

test('play teaser wand owns one independent presentation lifecycle and terminal', async () => {
  let scheduled = null;
  let independentActionActive = false;
  let pettingBlocked = false;
  let pausedJourneys = 0;
  let resumedJourneys = 0;
  let journeySyncs = 0;
  const artSources = [];
  const terminals = [];
  const buttonClasses = new Map();
  const buttonAttributes = new Map([['data-neko-idle-tier', 'cat1']]);
  const containerAttributes = new Map();
  const makeClassList = (store) => ({
    toggle(name, active) {
      store.set(name, active === true);
    },
  });
  const art = {};
  const container = {
    style: { display: '' },
    setAttribute: (name, value) => containerAttributes.set(name, value),
  };
  const button = {
    classList: makeClassList(buttonClasses),
    getAttribute: (name) => buttonAttributes.get(name) || '',
    setAttribute: (name, value) => buttonAttributes.set(name, value),
    closest: () => container,
    querySelector: () => art,
    __nekoIdleCat1Journey: {
      paused: false,
      substate: 'walking',
      profile: { walkingSubstate: 'walking' },
    },
  };
  const context = {
    Date: { now: () => 1000 },
    Math,
    Number,
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_TIER_NONE: 'none',
    _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _NEKO_GOODBYE_IDLE_APPEARANCE_BALL: 'ball',
    _NEKO_IDLE_CAT1_PLAY_TEASER_WAND_ACTION_ID: 'play_teaser_wand',
    _NEKO_IDLE_CAT1_PLAY_TEASER_WAND_ASSET_URL: '/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-play-2.gif',
    _NEKO_IDLE_CAT1_PETTING_ACTION_ID: 'cat1_petting',
    _NEKO_IDLE_CAT1_PETTING_ASSET_URL: '/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat-stroke.gif',
    window: { nekoPetCareMode: { shouldBlockEntityOutput: () => ({ blocked: pettingBlocked }) } },
    _isNekoCatActionResourceAvailable: (actionId) =>
      actionId === 'play_teaser_wand' || actionId === 'cat1_petting',
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    _isNekoIdleCat1PlaygroundEntryPending: () => false,
    _isNekoIdleCat1PlaygroundDropActive: () => false,
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoIdleReturnDragActionBlocking: () => false,
    _isAnyNekoIdleReturnDragActionBlocking: () => false,
    _isNekoIdleCompactSurfaceDragging: () => false,
    _isNekoIdleReturnPending: () => false,
    _isAnyNekoIdleReturnPending: () => false,
    _isNekoIdlePresentationTransitionActive: () => false,
    _isNekoIdleCat1EdgePeekActive: () => false,
    _isAnyNekoIdleCat1IndependentActionActive: () => independentActionActive,
    _isNekoIdleCat1IndependentActionActive: (target) => independentActionActive ||
      !!target.__nekoIdleCat1PlayTeaserWandActionState?.active ||
      !!target.__nekoIdleCat1PettingActionState?.active,
    _isNekoIdleCat1PettingEnabledForButton: (target) =>
      target.getAttribute('data-neko-petting-enabled') === 'true',
    _getNekoIdleReturnContainerFromButton: () => container,
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton: () => {},
    _isNekoIdleReturnDragActionActive: () => false,
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _setNekoIdleReturnArtSource: (_art, src, tier, options) => artSources.push({ src, tier, options }),
    _pauseNekoIdleCat1Journey: () => { pausedJourneys += 1; },
    _resumeNekoIdleCat1Journey: () => { resumedJourneys += 1; },
    _scheduleNekoIdleCat1JourneySync: () => { journeySyncs += 1; },
    _isNekoGoodbyeIdleBallButton: () => false,
    _setNekoGoodbyeIdleAppearanceForButton: () => {},
    _applyNekoIdleCat1PettingAvailability: () => {},
    _clearNekoIdleThoughtBubble: () => {},
    _clearNekoIdleCat1QuestionMark: () => {},
    _syncNekoIdleSleepSoundForTier: () => {},
    _syncNekoIdleCat1AmbientSoundForTier: () => {},
    _cancelNekoIdleCat1EatAction: () => {},
    _cancelNekoIdleCat1StretchAction: () => {},
    _cancelNekoIdleCat1PlayAction: () => {},
    _cancelNekoIdleCat1Journey: () => {},
    _clearNekoIdleCat1EdgePeekForTierExit: () => {},
    _isNekoIdleCat1EatActionActive: () => false,
    _isNekoIdleCat1PlayActionActive: () => false,
    _setNekoIdleReturnDragActionArt: () => {},
    _getNekoIdleReturnAssetUrl: (tier) => `${tier}.gif`,
    _finishNekoIdleReturnDragAction: () => {},
    _clearNekoIdleHoverPlayback: () => {},
    _cleanupNekoIdleArtTransition: () => {},
    _clearNekoIdleGifPlaybackSource: () => {},
    _getNekoIdleGifDurationMs: () => Promise.resolve(10860),
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
    'function _getNekoIdleCat1PlayTeaserWandActionState',
    'function _clearNekoIdleThoughtBubble'
  ), context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _applyNekoIdleReturnPresentation',
    'function _readNekoAutoGoodbyeVisualTier'
  ), context);

  assert.equal(context._playNekoIdleCat1PlayTeaserWandAction(button, {
    source: 'pet-care-toy',
    activityId: 'teaser-activity-1',
    requestId: 'teaser-request-1',
    runId: 'teaser-run-1',
    minimumDurationMs: 8000,
    onTerminal: (result, detail) => terminals.push({ result, detail }),
  }), true);
  assert.equal(button.__nekoIdleCat1PlayTeaserWandActionState.active, true);
  assert.equal(buttonClasses.get('is-cat1-playing-teaser-wand'), true);
  assert.equal(artSources[0].src, context._NEKO_IDLE_CAT1_PLAY_TEASER_WAND_ASSET_URL);
  assert.equal(artSources[0].options.animate, false);
  assert.equal(pausedJourneys, 1);

  const sourceCountBeforeSameTierSync = artSources.length;
  context._applyNekoIdleReturnPresentation(button, 'cat1');
  assert.equal(artSources.length, sourceCountBeforeSameTierSync);
  assert.equal(button.__nekoIdleCat1PlayTeaserWandActionState.active, true);

  await Promise.resolve();
  assert.equal(scheduled.delay, 10860);
  scheduled.callback();

  assert.equal(button.__nekoIdleCat1PlayTeaserWandActionState.active, false);
  assert.equal(buttonClasses.get('is-cat1-playing-teaser-wand'), false);
  assert.equal(artSources.at(-1).src, 'idle.gif');
  assert.equal(artSources.at(-1).options.animate, false);
  assert.equal(resumedJourneys, 1);
  assert.equal(journeySyncs, 0);
  assert.equal(terminals.length, 1);
  assert.equal(terminals[0].result, 'done');
  assert.equal(terminals[0].detail.actionId, 'play_teaser_wand');
  assert.equal(terminals[0].detail.source, 'pet-care-toy');
  assert.equal(terminals[0].detail.activityId, 'teaser-activity-1');
  assert.equal(terminals[0].detail.requestId, 'teaser-request-1');
  assert.equal(terminals[0].detail.runId, 'teaser-run-1');
  assert.equal(terminals[0].detail.reason, 'play-teaser-wand-action-finished');
  scheduled.callback();
  assert.equal(terminals.length, 1);

  scheduled = null;
  context._getNekoIdleGifDurationMs = () => Promise.reject(new Error('duration unavailable'));
  assert.equal(context._playNekoIdleCat1PlayTeaserWandAction(button, {
    source: 'pet-care-toy',
    minimumDurationMs: 8000,
    onTerminal: (result, detail) => terminals.push({ result, detail }),
  }), true);
  await Promise.resolve();
  assert.equal(scheduled.delay, 8000, 'service-provided floor survives GIF duration lookup failure');
  const failedLookupTimer = scheduled;
  context._cancelNekoIdleCat1PlayTeaserWandAction(button, { reason: 'return-ball-drag-active' });
  assert.equal(terminals.length, 2);
  assert.equal(terminals[1].result, 'interrupted');
  assert.equal(terminals[1].detail.actionId, 'play_teaser_wand');
  assert.equal(terminals[1].detail.reason, 'return-ball-drag-active');
  failedLookupTimer.callback();
  assert.equal(terminals.length, 2, 'cancelled floor timer must not emit a late done terminal');

  context._getNekoIdleGifDurationMs = () => Promise.resolve(10860);
  assert.equal(context._playNekoIdleCat1PlayTeaserWandAction(button, {
    source: 'pet-care-toy',
    onTerminal: (result, detail) => terminals.push({ result, detail }),
  }), true);
  await Promise.resolve();
  const tierChangeTimer = scheduled;
  context._applyNekoIdleReturnPresentation(button, 'cat2');
  assert.equal(button.__nekoIdleCat1PlayTeaserWandActionState.active, false);
  assert.equal(terminals.length, 3);
  assert.equal(terminals[2].result, 'interrupted');
  assert.equal(terminals[2].detail.reason, 'tier-change');
  tierChangeTimer.callback();
  assert.equal(terminals.length, 3);

  button.setAttribute('data-neko-idle-tier', 'cat1');

  independentActionActive = true;
  const artSourceCount = artSources.length;
  assert.equal(context._playNekoIdleCat1PlayTeaserWandAction(button), false);
  assert.equal(artSources.length, artSourceCount);

  independentActionActive = false;
  button.setAttribute('data-neko-petting-enabled', 'true');
  scheduled = null;
  const pettingStartIndex = artSources.length;
  assert.equal(context._playNekoIdleCat1PettingAction(button, {
    source: 'pet-care-unlocked-action',
  }), true);
  assert.equal(button.__nekoIdleCat1PettingActionState.active, true);
  assert.equal(buttonClasses.get('is-cat1-petting'), true);
  assert.equal(artSources[pettingStartIndex].src, context._NEKO_IDLE_CAT1_PETTING_ASSET_URL);
  await Promise.resolve();
  assert.equal(scheduled.delay, 10860);
  scheduled.callback();
  assert.equal(button.__nekoIdleCat1PettingActionState.active, false);
  assert.equal(buttonClasses.get('is-cat1-petting'), false);
  assert.equal(artSources.at(-1).src, 'idle.gif');

  pettingBlocked = true;
  const blockedPettingSourceCount = artSources.length;
  assert.equal(context._playNekoIdleCat1PettingAction(button), false);
  assert.equal(artSources.length, blockedPettingSourceCount);
  pettingBlocked = false;

  const actionsSource = fs.readFileSync(actionsPath, 'utf8');
  assert.match(actionsSource, /requestCat1PlayTeaserWand/);
  assert.doesNotMatch(actionsSource, /_requestNekoPetCareCat1PlayPresentation|requestCat1Play\s*:/);
  const teaserActionSource = sourceBetween(
    actionsPath,
    'function _getNekoIdleCat1PlayTeaserWandActionState',
    'function _clearNekoIdleThoughtBubble'
  );
  assert.doesNotMatch(teaserActionSource, /play_yarn|PlayActionState|PlayYarn|YarnHidden|PLAY_ASSET_URL/);
  assert.doesNotMatch(teaserActionSource, /EatAction|StretchAction|CatMind|AmbientSound|DragSound|NekoIdleSound|Voice/);
  const pettingActionSource = sourceBetween(
    actionsPath,
    'function _getNekoIdleCat1PettingActionState',
    'function _clearNekoIdleThoughtBubble'
  );
  assert.doesNotMatch(pettingActionSource, /fetch\(|nekoPetCareEntry|progress|reward|inventory|onTerminal/);

  const eatRunnerSource = sourceBetween(
    actionsPath,
    'function _playNekoIdleCat1EatAction',
    'function _getNekoIdleCat1StretchActionState'
  );
  const playRunnerSource = sourceBetween(
    actionsPath,
    'function _playNekoIdleCat1PlayAction',
    'function _getNekoIdleCat1PlayTeaserWandActionState'
  );
  assert.doesNotMatch(`${eatRunnerSource}\n${playRunnerSource}`, /TeaserWand/);
  const cssSource = fs.readFileSync(path.join(projectRoot, 'static/css/index.css'), 'utf8');
  const teaserSelector = '.neko-idle-return-btn.is-cat1-playing-teaser-wand > .neko-idle-return-art {';
  const teaserRuleStart = cssSource.indexOf(teaserSelector);
  const teaserRuleEnd = cssSource.indexOf('}', teaserRuleStart);
  assert.ok(teaserRuleStart >= 0 && teaserRuleEnd > teaserRuleStart,
    'the tall teaser-wand canvas should own its bottom anchor');
  const teaserRule = cssSource.slice(teaserRuleStart, teaserRuleEnd);
  assert.match(teaserRule, /position:\s*absolute/);
  assert.match(teaserRule, /width:\s*100%\s*!important/);
  assert.match(teaserRule, /height:\s*auto\s*!important/);
  assert.match(teaserRule, /inset:\s*auto 0 0/);
  const occupiedContext = {
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _isNekoCatActionResourceAvailable: () => true,
    _isNekoIdleDesktopWindowInteractionActionActive: () => false,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoIdleReturnDragActionActive: () => false,
    _isNekoIdleCat1EatActionActive: () => false,
    _isNekoIdleCat1StretchActionActive: () => false,
    _isNekoIdleCat1PlayActionActive: () => false,
    _isNekoIdleCat1IndependentActionActive: () => true,
  };
  vm.createContext(occupiedContext);
  vm.runInContext(`${eatRunnerSource}\n${playRunnerSource}`, occupiedContext);
  assert.equal(occupiedContext._playNekoIdleCat1EatAction(button), false);
  assert.equal(occupiedContext._playNekoIdleCat1PlayAction(button), false);
});

test('petting availability writes one explicit marker and revocation cancels the local action', () => {
  const attributes = new Map([['data-neko-idle-tier', 'cat1']]);
  const button = {
    dataset: {},
    getAttribute: name => attributes.get(name) || '',
    setAttribute(name, value) { attributes.set(name, String(value)); },
    removeAttribute(name) { attributes.delete(name); },
    __nekoIdleCat1PettingActionState: { active: false },
  };
  let cancellations = 0;
  let plays = 0;
  const context = {
    window: { nekoPetCareMode: { shouldBlockEntityOutput: () => ({ blocked: false }) } },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _normalizeNekoIdleReturnTier: value => value,
    _isNekoGoodbyeIdleBallButton: () => false,
    _forEachNekoIdleReturnButton: callback => callback(button),
    _isNekoIdleCat1PettingActionActive: target => target.__nekoIdleCat1PettingActionState.active,
    _cancelNekoIdleCat1PettingAction() { cancellations += 1; },
    _findNekoIdleCat1StretchPresentationButton: () => button,
    _playNekoIdleCat1PettingAction() { plays += 1; return true; },
  };
  vm.createContext(context);
  vm.runInContext(`let _nekoIdleCat1PettingUnlocked = false;\n${sourceBetween(
    actionsPath,
    'function _applyNekoIdleCat1PettingAvailability',
    'function _cancelNekoPetCarePresentation'
  )}`, context);

  assert.equal(context._setNekoIdleCat1PettingUnlocked(true), true);
  assert.equal(button.getAttribute('data-neko-petting-enabled'), 'true');
  assert.equal(context._requestNekoIdleCat1PettingPresentation(button), true);
  assert.equal(plays, 1);

  button.__nekoIdleCat1PettingActionState.active = true;
  assert.equal(context._setNekoIdleCat1PettingUnlocked(false), false);
  assert.equal(button.getAttribute('data-neko-petting-enabled'), '');
  assert.equal(cancellations, 1);
  assert.equal(context._requestNekoIdleCat1PettingPresentation(button), false);
  assert.equal(plays, 1);
});
