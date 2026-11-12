const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const ACTIONS_PATH = path.join(ROOT, 'static/avatar/avatar-ui-buttons/idle-actions-and-audio.js');
const DRAG_PATH = path.join(ROOT, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js');
const RETURN_PATH = path.join(ROOT, 'static/avatar/avatar-ui-buttons/methods-return.js');
const PRESENTATION_PATH = path.join(ROOT, 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js');
const ACTIONS_SOURCE = fs.readFileSync(ACTIONS_PATH, 'utf8');
const DRAG_SOURCE = fs.readFileSync(DRAG_PATH, 'utf8');
const RETURN_SOURCE = fs.readFileSync(RETURN_PATH, 'utf8');
const PRESENTATION_SOURCE = fs.readFileSync(PRESENTATION_PATH, 'utf8');

const CASES = Object.freeze({
  happy: Object.freeze({
    capability: 'cat1_happy', slot: 'action.cat1.happy', legacyClassName: 'is-cat1-happy',
    wrapper: '_playNekoIdleCat1HappyAction',
    requestApi: 'requestCat1Happy', url: 'happy.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-happy-action', finished: 'cat1-happy-action-finished',
  }),
  touch: Object.freeze({
    capability: 'cat1_touch', slot: 'action.cat1.touch', legacyClassName: 'is-cat1-touching',
    wrapper: '_playNekoIdleCat1TouchAction',
    requestApi: 'requestCat1Touch', url: 'touch.gif?v=1', duration: 6040, layout: 'normal',
    started: 'cat1-touch-action', finished: 'cat1-touch-action-finished',
  }),
  cosplay1: Object.freeze({
    capability: 'cat1_cosplay_1', slot: 'action.cat1.cosplay_1', legacyClassName: 'is-cat1-cosplay-1',
    wrapper: '_playNekoIdleCat1Cosplay1Action',
    requestApi: 'requestCat1Cosplay1', url: 'cos-1.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-cosplay-1-action', finished: 'cat1-cosplay-1-action-finished',
  }),
  change: Object.freeze({
    capability: 'cat1_change', slot: 'action.cat1.change',
    requestApi: 'requestCat1Change', url: 'change.gif?v=1', duration: 6040, layout: 'normal',
    started: 'cat1-change-action', finished: 'cat1-change-action-finished',
  }),
  cosplay2: Object.freeze({
    capability: 'cat1_cosplay_2', slot: 'action.cat1.cosplay_2',
    requestApi: 'requestCat1Cosplay2', url: 'cos-2.gif?v=1', duration: 6040, layout: 'normal',
    started: 'cat1-cosplay-2-action', finished: 'cat1-cosplay-2-action-finished',
  }),
  eat2: Object.freeze({
    capability: 'cat1_eat_2', slot: 'action.cat1.eat_2',
    requestApi: 'requestCat1Eat2', url: 'eat-2.gif?v=1', duration: 5040, layout: 'normal',
    started: 'cat1-eat-2-action', finished: 'cat1-eat-2-action-finished',
  }),
  eat3: Object.freeze({
    capability: 'cat1_eat_3', slot: 'action.cat1.eat_3',
    requestApi: 'requestCat1Eat3', url: 'eat-3.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-eat-3-action', finished: 'cat1-eat-3-action-finished',
  }),
  laugh: Object.freeze({
    capability: 'cat1_laugh', slot: 'action.cat1.laugh',
    requestApi: 'requestCat1Laugh', url: 'laugh.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-laugh-action', finished: 'cat1-laugh-action-finished',
  }),
  lionDance: Object.freeze({
    capability: 'cat1_lion_dance', slot: 'action.cat1.lion_dance',
    requestApi: 'requestCat1LionDance', url: 'lion_dance.gif?v=1', duration: 4040, layout: 'tall-bottom',
    started: 'cat1-lion-dance-action', finished: 'cat1-lion-dance-action-finished',
  }),
  love1: Object.freeze({
    capability: 'cat1_love_1', slot: 'action.cat1.love_1',
    requestApi: 'requestCat1Love1', url: 'love-1.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-love-1-action', finished: 'cat1-love-1-action-finished',
  }),
  mouse: Object.freeze({
    capability: 'cat1_mouse', slot: 'action.cat1.mouse',
    requestApi: 'requestCat1Mouse', url: 'mouse.gif?v=1', duration: 5040, layout: 'wide-centered',
    started: 'cat1-mouse-action', finished: 'cat1-mouse-action-finished',
  }),
  wave: Object.freeze({
    capability: 'cat1_wave', slot: 'action.cat1.wave',
    requestApi: 'requestCat1Wave', url: 'wave.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-wave-action', finished: 'cat1-wave-action-finished',
  }),
  sad: Object.freeze({
    capability: 'cat1_sad', slot: 'action.cat1.sad',
    requestApi: 'requestCat1Sad', url: 'sad.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-sad-action', finished: 'cat1-sad-action-finished',
  }),
  scared: Object.freeze({
    capability: 'cat1_scared', slot: 'action.cat1.scared',
    requestApi: 'requestCat1Scared', url: 'scared.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-scared-action', finished: 'cat1-scared-action-finished',
  }),
  stroke: Object.freeze({
    capability: 'cat1_stroke', slot: 'action.cat1.stroke',
    requestApi: 'requestCat1Stroke', url: 'stroke.gif?v=1', duration: 7980, layout: 'normal',
    started: 'cat1-stroke-action', finished: 'cat1-stroke-action-finished',
  }),
  wagging: Object.freeze({
    capability: 'cat1_wagging', slot: 'action.cat1.wagging',
    requestApi: 'requestCat1Wagging', url: 'wagging.gif?v=1', duration: 4040, layout: 'normal',
    started: 'cat1-wagging-action', finished: 'cat1-wagging-action-finished',
  }),
});

const SLEEP_CASES = Object.freeze({
  cat2Back1_1: Object.freeze({
    capability: 'cat2_back1_1', slot: 'action.cat2.back1_1',
    requestApi: 'requestCat2Back1_1', url: 'cat2-back1-1.webp?v=1', duration: 4040,
    layout: 'wide-125-centered', tier: 'cat2', terminalTier: 'cat1',
    started: 'cat2-back1-1-action', finished: 'cat2-back1-1-action-finished',
  }),
  cat2Turn1: Object.freeze({
    capability: 'cat2_turn_1', slot: 'action.cat2.turn_1',
    requestApi: 'requestCat2Turn1', url: 'cat2-turn-1.webp?v=1', duration: 5040,
    layout: 'wide-centered', tier: 'cat2',
    started: 'cat2-turn-1-action', finished: 'cat2-turn-1-action-finished',
  }),
  cat3Turn2: Object.freeze({
    capability: 'cat3_turn_2', slot: 'action.cat3.turn_2',
    requestApi: 'requestCat3Turn2', url: 'cat3-turn-2.webp?v=1', duration: 5930,
    layout: 'normal', tier: 'cat3',
    started: 'cat3-turn-2-action', finished: 'cat3-turn-2-action-finished',
  }),
  cat3Back1_1: Object.freeze({
    capability: 'cat3_back1_1', slot: 'action.cat3.back1_1',
    requestApi: 'requestCat3Back1_1', url: 'cat3-back1-1.webp?v=1', duration: 5040,
    layout: 'normal', tier: 'cat3', terminalTier: 'cat1',
    started: 'cat3-back1-1-action', finished: 'cat3-back1-1-action-finished',
  }),
  cat3Back1_2: Object.freeze({
    capability: 'cat3_back1_2', slot: 'action.cat3.back1_2',
    requestApi: 'requestCat3Back1_2', url: 'cat3-back1-2.webp?v=1', duration: 5390,
    layout: 'normal', tier: 'cat3', terminalTier: 'cat1',
    started: 'cat3-back1-2-action', finished: 'cat3-back1-2-action-finished',
  }),
});

const ALL_CASES = Object.freeze({ ...CASES, ...SLEEP_CASES });

function between(source, startMarker, endMarker, label) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${label} source slice`);
  return source.slice(start, end);
}

function cssDeclarations(source, selector) {
  const uncommented = source.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const match of uncommented.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].trim() !== selector) continue;
    return Object.fromEntries(match[2]
      .split(';')
      .map((declaration) => declaration.trim())
      .filter(Boolean)
      .map((declaration) => {
        const colon = declaration.indexOf(':');
        assert.ok(colon > 0, `${selector} CSS declaration`);
        return [
          declaration.slice(0, colon).trim(),
          declaration.slice(colon + 1).trim().replace(/\s*!important\s*$/, ''),
        ];
      }));
  }
  assert.fail(`missing CSS rule: ${selector}`);
}

function javascriptFilesUnder(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) return javascriptFilesUnder(target);
    return entry.isFile() && entry.name.endsWith('.js') ? [target] : [];
  });
}

function visualSource() {
  return between(
    ACTIONS_SOURCE,
    'function _shouldResumeNekoIdleJourneyAfterActionTakeover',
    'function _getNekoIdleCat1StretchActionState',
    'visual action kernel',
  );
}

function classList(store) {
  return {
    toggle(name, active) { if (active) store.add(name); else store.delete(name); },
    contains(name) { return store.has(name); },
  };
}

function harness(options = {}) {
  const buttonClasses = new Set();
  const containerClasses = new Set();
  const sources = [];
  const mirrors = [];
  const timers = [];
  const terminals = [];
  const cleanup = { hover: 0, transition: 0, playback: 0 };
  const journeyCalls = { pause: 0, resume: 0, sync: 0 };
  let nowMs = 1000;
  const gates = {
    desktop: false, playground: false, tier: 'cat1', drag: false, anyDrag: false,
    compactDrag: false, pending: false, anyPending: false, transition: false,
    edge: false, independent: false, audio: false,
  };
  const capabilities = new Set(Object.values(ALL_CASES).map((entry) => entry.capability));
  const urls = Object.fromEntries(Object.entries(ALL_CASES).map(([key, entry]) => [key, entry.url]));
  const durations = Object.fromEntries(Object.values(ALL_CASES).map((entry) => [entry.url, entry.duration]));
  const demotions = [];
  let resolveDuration;
  const art = options.hasArt === false ? null : {
    src: options.artSrc || 'idle.gif?v=1',
    style: { transform: 'scaleX(-1)', width: '100%', height: '100%', objectFit: 'contain' },
    getAttribute(name) { return name === 'src' ? this.src : null; },
  };
  const container = {
    style: { display: '', left: '12px', top: '24px', width: '122px', height: '122px', transform: 'none' },
    classList: classList(containerClasses),
  };
  const journey = {
    paused: false, profile: { walkingSubstate: 'walking' }, substate: 'walking',
  };
  const attributes = new Map();
  const button = {
    style: { transform: 'translate3d(0, 0, 0)' },
    classList: classList(buttonClasses),
    __nekoIdleCat1Journey: journey,
    getAttribute: (name) => name === 'data-neko-idle-tier' ? gates.tier : (attributes.get(name) || null),
    setAttribute: (name, value) => attributes.set(name, String(value)),
    removeAttribute: (name) => attributes.delete(name),
    querySelector: (selector) => selector === '.neko-idle-return-art' ? art : null,
  };
  const keyBySlot = Object.fromEntries(Object.entries(ALL_CASES).map(([key, entry]) => [entry.slot, key]));
  const context = {
    window: {
      nekoAutoGoodbye: {
        demoteVisualTierToCat1(source, reason) {
          if (gates.tier !== 'cat2' && gates.tier !== 'cat3') return false;
          demotions.push({ from: gates.tier, source, reason });
          gates.tier = 'cat1';
          return true;
        },
      },
    },
    Date: { now: () => nowMs },
    _NEKO_IDLE_TIER_CAT1: 'cat1', _NEKO_IDLE_TIER_CAT2: 'cat2', _NEKO_IDLE_TIER_CAT3: 'cat3',
    _NEKO_IDLE_RETURN_TRANSITION_MS: 300,
    _isNekoCatActionResourceAvailable: (key) => capabilities.has(key),
    _getNekoIdleCat1VisualActionAssetUrlForSlot: (slot) => urls[keyBySlot[slot]] || '',
    _isNekoIdleDesktopWindowInteractionActionActive: () => gates.desktop,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => gates.playground,
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoIdleReturnDragActionBlocking: () => gates.drag,
    _isAnyNekoIdleReturnDragActionBlocking: () => gates.anyDrag,
    _isNekoIdleCompactSurfaceDragging: () => gates.compactDrag,
    _isNekoCatMindReturnPending: () => gates.pending,
    _isAnyNekoCatMindReturnPending: () => gates.anyPending,
    _isNekoCatMindTransitionActive: () => gates.transition,
    _isNekoIdleCat1EdgePeekActive: () => gates.edge,
    _isAnyNekoIdleCat1IndependentActionActive: () => gates.independent,
    _isNekoCatMindAudioActionActive: () => gates.audio,
    _isNekoIdleAnimationRestartUrlForSource: (candidate, base, playbackToken = null) => (
      playbackToken === null
        ? candidate === base || candidate.startsWith(`${base}&entry=`)
        : candidate === `${base}&entry=${playbackToken}`
    ),
    _getNekoIdleReturnContainerFromButton: () => container,
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton: () => {},
    _isNekoIdleReturnDragActionActive: () => false,
    _getNekoIdleReturnCurrentArtUrl: (_button, tier) => tier === 'cat1'
      ? 'idle.gif?v=1'
      : `${tier}-idle.gif?v=1`,
    _pauseNekoIdleCat1Journey: () => { journeyCalls.pause += 1; journey.paused = true; },
    _resumeNekoIdleCat1Journey: () => { journeyCalls.resume += 1; journey.paused = false; },
    _scheduleNekoIdleCat1JourneySync: () => { journeyCalls.sync += 1; },
    _clearNekoIdleHoverPlayback: (target) => {
      cleanup.hover += 1;
      if (target) target.__nekoIdleHoverPausedJourney = false;
    },
    _cleanupNekoIdleArtTransition: () => { cleanup.transition += 1; },
    _clearNekoIdleGifPlaybackSource: () => { cleanup.playback += 1; },
    _setNekoIdleReturnArtSource: (target, src, tier, sourceOptions) => {
      const playbackSrc = sourceOptions.restartAnimation
        ? `${src}&entry=${sourceOptions.playbackToken}`
        : src;
      if (sourceOptions.animate) {
        target.__nekoIdleTransitionTo = src;
        target.__nekoIdleTransitionNext = { src };
      } else {
        target.src = playbackSrc;
        target.__nekoIdleTransitionTo = '';
        target.__nekoIdleTransitionNext = null;
      }
      sources.push({ src, tier, options: { ...sourceOptions } });
    },
    _syncNekoIdleCat1CompactMirrorReaction: (_button, _container, src, reason, artLayout) => (
      mirrors.push({ src, reason, artLayout })
    ),
    _getNekoCatMindCancelResult: (reason, finished) => reason === finished ? 'done' : 'interrupted',
    _forEachNekoIdleReturnButton: (callback) => callback(button),
    _getNekoIdleAnimationDurationMs: (src) => options.deferDuration
      ? new Promise((resolve) => { resolveDuration = resolve; })
      : Promise.resolve(durations[src]),
    setTimeout(callback, delay) {
      const timer = { id: timers.length + 1, callback, delay, cleared: false };
      timers.push(timer);
      return timer.id;
    },
    clearTimeout(id) {
      const timer = timers.find((entry) => entry.id === id);
      if (timer) timer.cleared = true;
    },
  };
  vm.createContext(context);
  vm.runInContext(visualSource(), context, { filename: ACTIONS_PATH });
  vm.runInContext(between(
    ACTIONS_SOURCE,
    'function _requestNekoIdleCat1VisualActionPresentation',
    'function _requestNekoIdleCat1HappyPresentation',
    'visual action presentation request',
  ), context, { filename: ACTIONS_PATH });
  vm.runInContext(
    `this.profiles = _NEKO_IDLE_VISUAL_ACTION_PROFILES;
     this.requestVisual = _requestNekoIdleCat1VisualActionPresentation;`,
    context,
  );
  return {
    context, button, container, art, journey, gates, capabilities, urls,
    attributes, buttonClasses, containerClasses, sources, mirrors, timers, terminals, cleanup, journeyCalls,
    demotions,
    play(key, onTerminal = (result, detail) => terminals.push({ result, detail })) {
      const wrapper = CASES[key].wrapper;
      return wrapper
        ? context[wrapper](button, { onTerminal })
        : context._playNekoIdleCat1VisualAction(button, key, { onTerminal });
    },
    cancel(cancelOptions) { context._cancelNekoIdleCat1VisualAction(button, cancelOptions); },
    playSleep(key, onTerminal = (result, detail) => terminals.push({ result, detail })) {
      const entry = SLEEP_CASES[key];
      gates.tier = entry.tier;
      return context._playNekoIdleCat1VisualAction(button, key, { onTerminal });
    },
    resolveDuration(ms) { assert.equal(typeof resolveDuration, 'function'); resolveDuration(ms); },
    setNow(ms) { nowMs = ms; },
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

test('every visual action shares one kernel with an explicit profile and request API', () => {
  const source = visualSource();
  assert.match(source, /function _playNekoIdleCat1VisualAction\(button, actionKey, options = \{\}\)/);
  assert.match(source, /function _cancelNekoIdleCat1VisualAction\(button, options = \{\}\)/);
  assert.doesNotMatch(source, /_playNekoIdleSound\s*\(|_beginNekoCatMindStateAction\s*\(/);
  const h = harness();
  assert.deepEqual(Object.keys(h.context.profiles), Object.keys(ALL_CASES));
  for (const [key, entry] of Object.entries(ALL_CASES)) {
    assert.equal(h.context.profiles[key].capability, entry.capability);
    assert.equal(h.context.profiles[key].slot, entry.slot);
    assert.equal(h.context.profiles[key].layout, entry.layout);
    assert.equal(h.context._getNekoIdleVisualActionTier(h.context.profiles[key]), entry.tier || 'cat1');
    assert.equal(h.context._getNekoIdleVisualActionTerminalTier(h.context.profiles[key]), entry.terminalTier || entry.tier || 'cat1');
    assert.equal(h.context.profiles[key].animateEntry === true, entry.animateEntry === true);
    assert.equal(h.context.profiles[key].className, entry.legacyClassName,
      `${key} has no action-specific class beyond the three compatibility classes`);
    if (entry.wrapper) assert.equal(typeof h.context[entry.wrapper], 'function');
  }
  assert.match(ACTIONS_SOURCE, /presentationApi\[profile\.requestName\]/,
    'every profile contributes its named public presentation API');
});

test('five sleep-tier actions start in their filename tier and keep explicit terminal behavior', async () => {
  for (const [key, entry] of Object.entries(SLEEP_CASES)) {
    const h = harness();
    assert.equal(h.playSleep(key), true, `${key} starts from ${entry.tier}`);
    assert.equal(h.attributes.get('data-neko-cat1-visual-action'), key);
    assert.equal(h.attributes.get('data-neko-cat1-visual-layout') || 'normal', entry.layout);
    assert.equal(h.sources[0].src, entry.url);
    assert.equal(h.sources[0].tier, entry.tier);
    assert.equal(h.journeyCalls.pause, 0, 'sleep actions do not own the CAT1 journey');
    assert.equal(h.mirrors.length, 0, 'sleep actions do not publish a CAT1 compact mirror');

    if (entry.animateEntry) {
      assert.equal(h.sources[0].options.animate, true);
      h.art.src = `${entry.url}&entry=1`;
      h.sources[0].options.onTransitionEnd();
    } else {
      assert.deepEqual(h.sources[0].options, { animate: false });
    }
    await flush();
    const finishTimer = h.timers.find((timer) => timer.delay === entry.duration);
    assert.ok(finishTimer, `${key} owns one audited cycle`);
    finishTimer.callback();

    if (entry.terminalTier === 'cat1') {
      assert.deepEqual(h.demotions, [{
        from: entry.tier, source: entry.finished, reason: entry.finished,
      }]);
      assert.equal(h.gates.tier, 'cat1');
      assert.equal(h.sources.length, 1, 'tier presentation owns the terminal CAT1 art');
    } else {
      assert.equal(h.demotions.length, 0);
      assert.deepEqual(h.sources.at(-1), {
        src: `${entry.tier}-idle.gif?v=1`, tier: entry.tier, options: { animate: false },
      });
      assert.equal(h.gates.tier, entry.tier);
    }
    assert.equal(h.terminals[0].result, 'done');
    assert.equal(h.terminals[0].detail.reason, entry.finished);
  }

  const actionSourceWithoutProfiles = ACTIONS_SOURCE.replace(
    between(ACTIONS_SOURCE, 'const _NEKO_IDLE_CAT1_VISUAL_ACTION_PROFILES', 'const _NEKO_IDLE_CAT1_RANDOM_VISUAL_ACTION_KEYS', 'profiles'),
    '',
  );
  for (const entry of Object.values(SLEEP_CASES)) {
    assert.doesNotMatch(actionSourceWithoutProfiles, new RegExp(`['\"]${entry.capability}['\"]`));
  }
});

test('CAT1 idle random pool contains six equal-width visual actions including mouse', () => {
  const h = harness();
  vm.runInContext(
    'this.randomVisualKeys = Array.from(_NEKO_IDLE_CAT1_RANDOM_VISUAL_ACTION_KEYS);',
    h.context,
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(h.context.randomVisualKeys)),
    ['happy', 'touch', 'mouse', 'laugh', 'love1', 'wagging'],
  );

  const cases = [
    [0, 'happy'],
    [(1 / 6) - 0.000001, 'happy'],
    [1 / 6, 'touch'],
    [2 / 6, 'mouse'],
    [3 / 6, 'laugh'],
    [4 / 6, 'love1'],
    [5 / 6, 'wagging'],
    [0.999999, 'wagging'],
  ];
  for (const [roll, expected] of cases) {
    h.context.__idleRandomRoll = roll;
    vm.runInContext('Math.random = () => globalThis.__idleRandomRoll;', h.context);
    assert.equal(h.context._pickNekoIdleRandomVisualActionKey('cat1'), expected, String(roll));
  }
});

test('sleep-tier visual pools keep turn at 80% and split CAT3 back evenly', () => {
  const h = harness();
  const cases = [
    ['cat2', 0, 'cat2Turn1'],
    ['cat2', 0.799999, 'cat2Turn1'],
    ['cat2', 0.8, 'cat2Back1_1'],
    ['cat3', 0.799999, 'cat3Turn2'],
    ['cat3', 0.8, 'cat3Back1_1'],
    ['cat3', 0.899999, 'cat3Back1_1'],
    ['cat3', 0.9, 'cat3Back1_2'],
    ['cat3', 0.999999, 'cat3Back1_2'],
  ];
  for (const [tier, roll, expected] of cases) {
    h.context.__idleRandomRoll = roll;
    vm.runInContext('Math.random = () => globalThis.__idleRandomRoll;', h.context);
    assert.equal(h.context._pickNekoIdleRandomVisualActionKey(tier), expected, `${tier} ${roll}`);
  }
  assert.equal(h.context._pickNekoIdleRandomVisualActionKey('none'), '');
});

test('each visual action owns one audited cycle, restores directly and restarts its animation', async () => {
  for (const [key, entry] of Object.entries(CASES)) {
    const h = harness();
    const before = JSON.stringify({ button: h.button.style, container: h.container.style, art: h.art.style });
    assert.equal(h.play(key), true, `${key} starts`);
    assert.equal(h.buttonClasses.has('is-cat1-visual-action'), true);
    assert.equal(h.containerClasses.has('is-cat1-visual-action'), true);
    assert.equal(h.attributes.get('data-neko-cat1-visual-action'), key);
    if (entry.layout === 'normal') {
      assert.equal(h.attributes.has('data-neko-cat1-visual-layout'), false);
    } else {
      assert.equal(h.attributes.get('data-neko-cat1-visual-layout'), entry.layout);
    }
    if (entry.legacyClassName) {
      assert.equal(h.buttonClasses.has(entry.legacyClassName), true);
      assert.equal(h.containerClasses.has(entry.legacyClassName), true);
    }
    assert.deepEqual(h.sources[0], { src: entry.url, tier: 'cat1', options: { animate: false } });
    assert.deepEqual(h.mirrors[0], { src: entry.url, reason: entry.started, artLayout: entry.layout });
    assert.deepEqual(h.cleanup, { hover: 1, transition: 1, playback: 1 });
    assert.equal(h.journeyCalls.pause, 1);
    await flush();
    assert.equal(h.timers.length, 1);
    assert.equal(h.timers[0].delay, entry.duration);
    assert.equal(h.play(key), false, 'same action cannot reset the active cycle');
    const other = Object.keys(CASES).find((candidate) => candidate !== key);
    assert.equal(h.play(other), false, `${key} excludes ${other}`);
    assert.equal(h.timers.length, 1);

    const timer = h.timers[0];
    timer.callback();
    assert.equal(h.buttonClasses.has('is-cat1-visual-action'), false);
    assert.equal(h.containerClasses.has('is-cat1-visual-action'), false);
    assert.equal(h.attributes.has('data-neko-cat1-visual-action'), false);
    assert.equal(h.attributes.has('data-neko-cat1-visual-layout'), false);
    if (entry.legacyClassName) {
      assert.equal(h.buttonClasses.has(entry.legacyClassName), false);
      assert.equal(h.containerClasses.has(entry.legacyClassName), false);
    }
    assert.deepEqual(h.sources.at(-1), {
      src: 'idle.gif?v=1', tier: 'cat1', options: { animate: false },
    }, 'exit is a direct source swap, not the generic transition');
    assert.equal(h.journeyCalls.resume, 1);
    assert.equal(h.journeyCalls.sync, 0);
    assert.equal(h.terminals.length, 1);
    assert.equal(h.terminals[0].result, 'done');
    assert.equal(h.terminals[0].detail.reason, entry.finished);
    timer.callback();
    assert.equal(h.terminals.length, 1, 'stale timer is idempotent');

    assert.equal(h.play(key, null), true);
    assert.equal(h.sources.filter((item) => item.src === entry.url).length, 2,
      'a later run assigns the animation source again from its first frame');
    assert.equal(h.cleanup.playback, 2);
    h.cancel({ restoreArt: false, reason: 'test-cleanup' });
    assert.equal(JSON.stringify({ button: h.button.style, container: h.container.style, art: h.art.style }), before,
      `${key} changes no layout or transform style`);
  }
});

test('visual action takes over a hover-owned walking pause and resumes it after finishing', async () => {
  const h = harness();
  h.journey.paused = true;
  h.art.__nekoIdleHoverPausedJourney = true;
  assert.equal(h.play('happy'), true);
  await flush();
  h.timers[0].callback();
  assert.equal(h.journeyCalls.resume, 1);
  assert.equal(h.journey.paused, false);
});

test('visual action does not resume a walking pause owned by another subsystem', async () => {
  const h = harness();
  h.journey.paused = true;
  assert.equal(h.play('happy'), true);
  await flush();
  h.timers[0].callback();
  assert.equal(h.journeyCalls.resume, 0);
  assert.equal(h.journey.paused, true);
});

test('animated visual entry starts its full animation cycle only after the shared tier fade completes', async () => {
  const h = harness();
  assert.equal(h.context._playNekoIdleCat1VisualAction(
    h.button,
    'scared',
    { animateEntry: true },
  ), true);
  assert.equal(h.sources.length, 1);
  assert.equal(h.sources[0].src, CASES.scared.url);
  assert.equal(h.sources[0].options.animate, true);
  assert.equal(h.sources[0].options.restartAnimation, true);
  assert.equal(h.sources[0].options.playbackToken, 1);
  assert.equal(typeof h.sources[0].options.onTransitionEnd, 'function');
  await flush();
  assert.equal(h.timers.length, 1, 'only the transition watchdog runs underneath the fade');
  assert.equal(h.timers[0].delay, 300);

  h.art.src = `${CASES.scared.url}&entry=1`;
  h.art.__nekoIdleTransitionTo = '';
  h.art.__nekoIdleTransitionNext = null;
  h.sources[0].options.onTransitionEnd();
  await flush();
  assert.equal(h.timers[0].cleared, true);
  assert.equal(h.timers.length, 2);
  assert.equal(h.timers[1].delay, CASES.scared.duration);
});

test('animated entry alternates two bounded playback URLs so the main art starts a fresh timeline', async () => {
  const h = harness();
  for (const expectedToken of [1, 2, 1]) {
    assert.equal(h.context._playNekoIdleCat1VisualAction(
      h.button,
      'scared',
      { animateEntry: true },
    ), true);
    const source = h.sources.at(-1);
    assert.equal(source.options.playbackToken, expectedToken);
    h.art.src = `${CASES.scared.url}&entry=${expectedToken}`;
    source.options.onTransitionEnd();
    await flush();
    h.cancel({ restoreArt: false, reason: 'test-cleanup' });
  }
});

test('animation restart URLs preserve the canonical asset and version while changing the cache key', () => {
  const source = between(
    DRAG_SOURCE,
    'function _buildNekoIdleAnimationRestartUrl',
    'function _cleanupNekoIdleArtTransition',
    'animation restart URL helpers',
  );
  const context = vm.createContext({
    URL,
    window: { location: { href: 'https://localhost/avatar.html' } },
  });
  vm.runInContext(source, context, { filename: DRAG_PATH });
  const base = '/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-scared.webp?v=7';
  const restarted = context._buildNekoIdleAnimationRestartUrl(base, 2);
  assert.equal(
    restarted,
    'https://localhost/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-scared.webp?v=7&entry=2',
  );
  assert.equal(context._isNekoIdleAnimationRestartUrlForSource(restarted, base), true);
  assert.equal(context._isNekoIdleAnimationRestartUrlForSource(restarted, base, 2), true);
  assert.equal(context._isNekoIdleAnimationRestartUrlForSource(restarted, base, 1), false);
  assert.equal(context._isNekoIdleAnimationRestartUrlForSource(
    restarted,
    '/static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-happy.webp?v=7',
  ), false);
  assert.equal(
    context._buildNekoIdleAnimationRestartUrl('/static/assets/neko-idle/cat1-question-mark.png', 2),
    '/static/assets/neko-idle/cat1-question-mark.png',
  );
});

test('the shared fade keeps its next art until the restarted main image has loaded', async () => {
  const source = between(
    PRESENTATION_SOURCE,
    'function _setNekoIdleReturnArtSource',
    'function _playNekoIdleHoverArt',
    'return art transition',
  );
  const timers = [];
  const created = [];
  let transitionEnds = 0;
  let transitionErrors = 0;
  const button = {
    appendChild(node) { node.parentNode = this; },
    removeChild(node) { node.parentNode = null; },
    classList: { add() {}, remove() {} },
  };
  const attributes = new Map([['src', 'sleep.webp?v=1']]);
  const art = {
    closest: () => button,
    getAttribute: (name) => attributes.get(name) || '',
    setAttribute: (name, value) => attributes.set(name, String(value)),
    removeAttribute: (name) => attributes.delete(name),
  };
  Object.defineProperty(art, 'src', {
    get: () => attributes.get('src') || '',
    set: (value) => attributes.set('src', String(value)),
  });
  const context = vm.createContext({
    Promise,
    document: {
      createElement() {
        const node = {
          setAttribute() {},
          removeAttribute(name) { if (name === 'src') this.src = ''; },
        };
        created.push(node);
        return node;
      },
    },
    _NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR: 'data-finishing',
    _NEKO_IDLE_RETURN_TRANSITION_MS: 300,
    _buildNekoIdleAnimationRestartUrl: (src, token) => `${src}&entry=${token}`,
    _clearNekoIdleHoverPlayback() {},
    _clearNekoIdleGifPlaybackSource() {},
    _shouldReduceNekoIdleMotion: () => false,
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt() {},
    _cleanupNekoIdleArtTransition(target) {
      if (typeof target.__nekoIdleTransitionPreloadCancel === 'function') {
        target.__nekoIdleTransitionPreloadCancel();
      }
      target.__nekoIdleTransitionPreloadCancel = null;
      target.__nekoIdleTransitionPreload = null;
      target.__nekoIdleTransitionNext = null;
      target.__nekoIdleTransitionTo = '';
    },
    setTimeout(callback, delay) {
      timers.push({ callback, delay });
      return timers.length;
    },
  });
  vm.runInContext(source, context, { filename: PRESENTATION_PATH });
  context._setNekoIdleReturnArtSource(
    art,
    'scared.webp?v=1',
    'cat1',
    {
      animate: true,
      restartAnimation: true,
      playbackToken: 1,
      onTransitionEnd: () => { transitionEnds += 1; },
    },
  );
  assert.equal(created.length, 2, 'one visible fade image and one detached preload image');
  assert.equal(created[0].src, 'scared.webp?v=1');
  assert.equal(created[1].src, 'scared.webp?v=1&entry=1');
  assert.equal(attributes.get('src'), 'sleep.webp?v=1');

  timers[0].callback();
  await flush();
  assert.equal(created[0].parentNode, button, 'the visible fade art stays mounted while preload is pending');
  assert.equal(attributes.get('src'), 'sleep.webp?v=1', 'the old main art stays until preload is ready');
  assert.equal(transitionEnds, 0);

  created[1].onload();
  await flush();
  assert.equal(attributes.get('src'), 'scared.webp?v=1&entry=1');
  assert.equal(transitionEnds, 1);

  art.src = 'sleep.webp?v=1';
  context._setNekoIdleReturnArtSource(art, 'scared.webp?v=1', 'cat1', {
    animate: true,
    restartAnimation: true,
    playbackToken: 1,
  });
  timers[1].callback();
  context._cleanupNekoIdleArtTransition(art);
  context._setNekoIdleReturnArtSource(art, 'scared.webp?v=1', 'cat1', {
    animate: true,
    restartAnimation: true,
    playbackToken: 2,
    onTransitionError: () => { transitionErrors += 1; },
  });
  await flush();
  assert.equal(
    art.__nekoIdleTransitionNext,
    created[4],
    'a cancelled preload continuation cannot clear a newer transition for the same source',
  );
  created[5].onerror();
  timers[2].callback();
  await flush();
  assert.equal(transitionErrors, 1);
  assert.equal(art.__nekoIdleTransitionNext, null);
});

test('failed animated entry restores CAT1 art and resumes the paused journey', async () => {
  const h = harness({ artSrc: 'sleep.gif?v=1' });
  h.gates.tier = 'cat2';
  assert.equal(h.context._playNekoIdleCat1VisualAction(h.button, 'scared', {
    animateEntry: true,
    prepareCat1() {
      h.gates.tier = 'cat1';
      return true;
    },
    onTerminal: (result, detail) => h.terminals.push({ result, detail }),
  }), true);
  assert.equal(h.journey.paused, true);

  const source = h.sources[0];
  assert.equal(typeof source.options.onTransitionError, 'function');
  source.options.onTransitionError();
  await flush();

  assert.equal(h.art.src, 'idle.gif?v=1');
  assert.equal(h.journey.paused, false);
  assert.equal(h.button.__nekoIdleCat1VisualActionState.active, false);
  assert.equal(h.terminals[0].result, 'interrupted');
});

test('timed-out animated entry restores CAT1 art and resumes the paused journey', async () => {
  const h = harness({ artSrc: 'sleep.gif?v=1' });
  h.gates.tier = 'cat2';
  assert.equal(h.context._playNekoIdleCat1VisualAction(h.button, 'scared', {
    animateEntry: true,
    prepareCat1() {
      h.gates.tier = 'cat1';
      return true;
    },
  }), true);
  assert.equal(h.journey.paused, true);

  h.setNow(7000);
  h.timers[0].callback();
  await flush();

  assert.equal(h.art.src, 'idle.gif?v=1');
  assert.equal(h.journey.paused, false);
  assert.equal(h.button.__nekoIdleCat1VisualActionState.active, false);
});

test('animated entry watchdog rejects a stale playback token', async () => {
  const h = harness();
  assert.equal(h.context._playNekoIdleCat1VisualAction(
    h.button,
    'scared',
    { animateEntry: true },
  ), true);
  h.art.src = `${CASES.scared.url}&entry=2`;
  h.art.__nekoIdleTransitionTo = '';
  h.art.__nekoIdleTransitionNext = null;
  h.timers[0].callback();
  await flush();

  assert.equal(h.button.__nekoIdleCat1VisualActionState.active, false);
  assert.equal(h.timers.some((timer) => timer.delay === CASES.scared.duration), false);
});

test('interrupted animated entry releases the action instead of reporting an unseen cycle as done', async () => {
  const h = harness();
  assert.equal(h.context._playNekoIdleCat1VisualAction(
    h.button,
    'scared',
    {
      animateEntry: true,
      onTerminal: (result, detail) => h.terminals.push({ result, detail }),
    },
  ), true);
  const watchdog = h.timers[0];
  h.art.__nekoIdleTransitionTo = '';
  h.art.__nekoIdleTransitionNext = null;
  watchdog.callback();
  await flush();
  assert.equal(h.timers.some((timer) => timer.delay === CASES.scared.duration), false);
  assert.equal(h.art.src, 'idle.gif?v=1', 'the interrupted fade never installed the action image');
  assert.equal(h.button.__nekoIdleCat1VisualActionState.active, false);
  assert.equal(h.terminals.length, 1);
  assert.equal(h.terminals[0].result, 'interrupted');
  assert.equal(h.terminals[0].detail.reason, 'cat1-scared-action-cancelled');
  assert.equal(h.journey.paused, true, 'another presentation owns the paused journey');
});

test('sleep-tier prepared visual action runs its hard gates before demotion', () => {
  const h = harness();
  h.gates.tier = 'cat2';
  let prepares = 0;
  assert.equal(h.context._playNekoIdleCat1VisualAction(h.button, 'scared', {
    prepareCat1() {
      prepares += 1;
      h.gates.tier = 'cat1';
      return true;
    },
  }), true);
  assert.equal(prepares, 1);

  const blocked = harness();
  blocked.gates.tier = 'cat2';
  blocked.gates.transition = true;
  let blockedPrepares = 0;
  assert.equal(blocked.context._playNekoIdleCat1VisualAction(blocked.button, 'scared', {
    prepareCat1() { blockedPrepares += 1; return true; },
  }), false);
  assert.equal(blockedPrepares, 0);
});

test('shared runner keeps hard gates and late duration resolution cannot revive cancellation', async () => {
  const gateCases = [
    ['desktop', (h) => { h.gates.desktop = true; }],
    ['playground', (h) => { h.gates.playground = true; }],
    ['tier', (h) => { h.gates.tier = 'cat2'; }],
    ['drag', (h) => { h.gates.drag = true; }],
    ['any drag', (h) => { h.gates.anyDrag = true; }],
    ['compact drag', (h) => { h.gates.compactDrag = true; }],
    ['return pending', (h) => { h.gates.pending = true; }],
    ['any return pending', (h) => { h.gates.anyPending = true; }],
    ['transition', (h) => { h.gates.transition = true; }],
    ['edge', (h) => { h.gates.edge = true; }],
    ['independent', (h) => { h.gates.independent = true; }],
    ['audio', (h) => { h.gates.audio = true; }],
    ['hidden', (h) => { h.container.style.display = 'none'; }],
    ['capability', (h) => { h.capabilities.delete('cat1_touch'); }],
    ['asset', (h) => { h.urls.touch = ''; }],
  ];
  for (const [label, mutate] of gateCases) {
    const h = harness();
    mutate(h);
    assert.equal(h.play('touch'), false, label);
    assert.equal(h.sources.length, 0, `${label} does not touch art`);
  }
  assert.equal(harness({ hasArt: false }).play('touch'), false);

  const h = harness({ deferDuration: true });
  assert.equal(h.play('touch'), true);
  h.cancel({ restoreArt: false, reason: 'return-ball-drag-active' });
  h.cancel({ restoreArt: false, reason: 'return-ball-drag-active' });
  h.resolveDuration(CASES.touch.duration);
  await flush();
  assert.equal(h.timers.length, 0);
  assert.equal(h.terminals.length, 1);
  assert.equal(h.terminals[0].result, 'interrupted');
  assert.equal(h.terminals[0].detail.reason, 'return-ball-drag-active');
});

test('presentation sync preserves the active visual animation instead of restarting its current cycle', () => {
  const source = between(
    PRESENTATION_SOURCE,
    'function _applyNekoIdleReturnPresentation',
    'function _readNekoAutoGoodbyeVisualTier',
    'return presentation sync',
  );
  let currentSrc = 'touch.gif?v=1';
  let visualActionActive = true;
  let visualActionTier = 'cat1';
  const writes = [];
  const visualCancels = [];
  let journeySyncs = 0;
  const art = { getAttribute: (name) => name === 'src' ? currentSrc : '' };
  const container = { setAttribute() {} };
  const button = {
    __nekoIdleThoughtBubbleTier: '',
    querySelector: () => art,
    closest: () => container,
    setAttribute() {},
  };
  const context = vm.createContext({
    _NEKO_IDLE_TIER_NONE: 'none',
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoGoodbyeIdleBallButton: () => false,
    _setNekoGoodbyeIdleAppearanceForButton: () => {},
    _isNekoIdleCat1PlaygroundEntryPending: () => false,
    _isNekoIdleCat1PlaygroundDropActive: () => false,
    _clearNekoIdleThoughtBubble: () => {},
    _isNekoIdleReturnDragActionActive: () => false,
    _syncNekoIdleSleepSoundForTier: () => {},
    _syncNekoIdleCat1AmbientSoundForTier: () => {},
    _isNekoIdleCat1EatActionActive: () => false,
    _isNekoIdleCat1PlayActionActive: () => false,
    _isNekoIdleCat1VisualActionActive: () => visualActionActive,
    _getNekoIdleCat1ActiveVisualActionProfile: () => visualActionActive
      ? { tier: visualActionTier }
      : null,
    _getNekoIdleVisualActionTier: (profile) => profile.tier,
    _cancelNekoIdleCat1VisualAction: (_button, options) => {
      visualCancels.push({ ...options });
      visualActionActive = false;
    },
    _getNekoIdleCat1VisualActionAssetUrl: () => 'touch.gif?v=1',
    _getNekoIdleReturnAssetUrl: (tier) => `${tier}-idle.gif?v=1`,
    _isNekoIdleAnimationRestartUrlForSource: (candidate, base) => candidate === base,
    _setNekoIdleReturnArtSource: (_art, src, tier, options) => writes.push({
      src, tier, options: { ...options },
    }),
    _scheduleNekoIdleCat1JourneySync: () => { journeySyncs += 1; },
  });
  vm.runInContext(source, context, { filename: PRESENTATION_PATH });
  context._applyNekoIdleReturnPresentation(button, 'cat1');
  assert.equal(writes.length, 0, 'same active src is not assigned again');
  assert.equal(journeySyncs, 0, 'active action does not restart journey scheduling');

  currentSrc = 'idle.gif?v=1';
  context._applyNekoIdleReturnPresentation(button, 'cat1');
  assert.deepEqual(writes, [{ src: 'touch.gif?v=1', tier: 'cat1', options: { animate: false } }]);

  visualActionActive = true;
  visualActionTier = 'cat2';
  currentSrc = 'cat2-turn.gif?v=1';
  context._applyNekoIdleReturnPresentation(button, 'cat1');
  assert.deepEqual(visualCancels, [{ restoreArt: false, reason: 'tier-change' }],
    'an external tier change releases a sleep action immediately');
  assert.deepEqual(writes.at(-1), {
    src: 'cat1-idle.gif?v=1', tier: 'cat1', options: {},
  });
});

test('CAT1 right-click gives stroke 50% and splits the other 50% across five independent actions', () => {
  const source = between(
    RETURN_SOURCE,
    'const _NEKO_IDLE_CAT1_VISUAL_ACTION_OTHER_RANDOM_REQUESTS',
    'Object.assign(AvatarButtonMixin.methods',
    'visual action random contextmenu',
  );
  const expectedOtherRequests = [
    'requestCat1Cosplay1',
    'requestCat1Change',
    'requestCat1Cosplay2',
    'requestCat1LionDance',
    'requestCat1Wave',
  ];
  let tier = 'cat1';
  let bubble = false;
  let goodbye = false;
  let requestAllowed = true;
  const requests = [];
  const requestOptions = [];
  const presentation = {
    requestCat1Stroke: (options) => {
      requests.push('requestCat1Stroke'); requestOptions.push(options); return requestAllowed;
    },
  };
  for (const requestName of expectedOtherRequests) {
    presentation[requestName] = (options) => {
      requests.push(requestName); requestOptions.push(options); return requestAllowed;
    };
  }
  const context = {
    window: { NekoCatIdlePresentation: presentation },
    _isNekoIdleThoughtBubbleEventHit: () => bubble,
    _isNekoGoodbyeIdleBallButton: () => goodbye,
    _normalizeNekoIdleReturnTier: (value) => value,
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_TIER_CAT2: 'cat2',
    _NEKO_IDLE_TIER_CAT3: 'cat3',
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: RETURN_PATH });
  vm.runInContext(
    `this.otherRequests = Array.from(_NEKO_IDLE_CAT1_VISUAL_ACTION_OTHER_RANDOM_REQUESTS);
     this.primaryRequest = _NEKO_IDLE_CAT1_VISUAL_ACTION_PRIMARY_REQUEST;`,
    context,
  );
  assert.deepEqual(JSON.parse(JSON.stringify(context.otherRequests)), expectedOtherRequests);
  assert.equal(context.primaryRequest, 'requestCat1Stroke');
  assert.equal(context.otherRequests.includes('requestCat1Scared'), false);
  const button = { getAttribute: () => tier };
  const event = (number) => ({
    button: number, prevented: 0, stopped: 0,
    preventDefault() { this.prevented += 1; },
    stopPropagation() { this.stopped += 1; },
  });

  const left = event(0);
  assert.equal(context._handleNekoIdleCat1VisualActionRandomContextMenu(button, left), false);
  assert.deepEqual([left.prevented, requests.length], [0, 0]);
  tier = 'cat2';
  const cat2 = event(2);
  assert.equal(context._handleNekoIdleCat1VisualActionRandomContextMenu(button, cat2), false);
  assert.equal(cat2.prevented, 0);
  tier = 'cat1';
  bubble = true;
  assert.equal(context._handleNekoIdleCat1VisualActionRandomContextMenu(button, event(2)), false);
  bubble = false;
  goodbye = true;
  assert.equal(context._handleNekoIdleCat1VisualActionRandomContextMenu(button, event(2)), false);
  goodbye = false;
  assert.equal(requests.length, 0);

  const boundaries = [
    [0, 'requestCat1Stroke'],
    [0.499999, 'requestCat1Stroke'],
    [0.5, 'requestCat1Cosplay1'],
    [0.599999, 'requestCat1Cosplay1'],
    [0.6, 'requestCat1Change'],
    [0.7, 'requestCat1Cosplay2'],
    [0.8, 'requestCat1LionDance'],
    [0.9, 'requestCat1Wave'],
    [0.999999, 'requestCat1Wave'],
  ];
  for (const [roll, expectedRequest] of boundaries) {
    context.__rightClickRoll = roll;
    vm.runInContext('Math.random = () => globalThis.__rightClickRoll;', context);
    const right = event(2);
    assert.equal(context._handleNekoIdleCat1VisualActionRandomContextMenu(button, right), true);
    assert.equal(requests.at(-1), expectedRequest, String(roll));
    assert.equal(requestOptions.at(-1).button, button);
    assert.equal(requestOptions.at(-1).userInitiated, true);
    assert.deepEqual([right.prevented, right.stopped], [1, 1]);
  }
  requestAllowed = false;
  const rejected = event(2);
  assert.equal(context._handleNekoIdleCat1VisualActionRandomContextMenu(button, rejected), false);
  assert.deepEqual([rejected.prevented, rejected.stopped], [0, 0]);

  assert.equal(requests.includes('requestCat1Scared'), false);
  assert.equal((RETURN_SOURCE.match(/addEventListener\(['"]contextmenu['"]/g) || []).length, 1);
  assert.match(
    RETURN_SOURCE,
    /_handleNekoIdleCat1VisualActionRandomContextMenu\(returnBtn, event\)/,
  );
});

test('CAT2 and CAT3 right-click demote to CAT1 and request scared with the shared fade', () => {
  const source = between(
    RETURN_SOURCE,
    'const _NEKO_IDLE_CAT1_VISUAL_ACTION_OTHER_RANDOM_REQUESTS',
    'Object.assign(AvatarButtonMixin.methods',
    'sleep-tier scared contextmenu',
  );
  for (const initialTier of ['cat2', 'cat3']) {
    let tier = initialTier;
    const demotions = [];
    const requests = [];
    const context = {
      window: {
        nekoAutoGoodbye: {
          demoteVisualTierToCat1(sourceName, reason) {
            demotions.push({ sourceName, reason, from: tier });
            tier = 'cat1';
            return true;
          },
        },
        NekoCatIdlePresentation: {
          requestCat1Scared(options) {
            requests.push(options);
            return options.prepareCat1() === true;
          },
        },
      },
      _NEKO_IDLE_TIER_CAT1: 'cat1',
      _NEKO_IDLE_TIER_CAT2: 'cat2',
      _NEKO_IDLE_TIER_CAT3: 'cat3',
      _NEKO_CAT_MIND_ACTION_RESULTS: { DONE: 'done' },
      _isNekoIdleThoughtBubbleEventHit: () => false,
      _isNekoGoodbyeIdleBallButton: () => false,
      _isNekoCatMindTransitionActive: () => false,
      _normalizeNekoIdleReturnTier: (value) => value,
    };
    vm.createContext(context);
    vm.runInContext(source, context, { filename: RETURN_PATH });
    const button = { getAttribute: () => tier };
    const event = {
      button: 2, prevented: 0, stopped: 0,
      preventDefault() { this.prevented += 1; },
      stopPropagation() { this.stopped += 1; },
    };

    assert.equal(context._handleNekoIdleSleepTierScaredContextMenu(button, event), true);
    assert.deepEqual(demotions, [{
      sourceName: 'cat-contextmenu-demotion',
      reason: 'cat1-scared-action',
      from: initialTier,
    }]);
    assert.equal(tier, 'cat1');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].animateEntry, true);
    assert.equal(requests[0].button, button);
    assert.equal(requests[0].userInitiated, true);
    assert.equal(typeof requests[0].prepareCat1, 'function');
    assert.deepEqual([event.prevented, event.stopped], [1, 1]);
  }
});

test('sleep-tier rejected request neither demotes nor consumes the context menu', () => {
  const source = between(
    RETURN_SOURCE,
    'const _NEKO_IDLE_CAT1_VISUAL_ACTION_OTHER_RANDOM_REQUESTS',
    'Object.assign(AvatarButtonMixin.methods',
    'sleep-tier rejected contextmenu',
  );
  let demotions = 0;
  const context = {
    window: {
      nekoAutoGoodbye: { demoteVisualTierToCat1() { demotions += 1; return true; } },
      NekoCatIdlePresentation: { requestCat1Scared: () => false },
    },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_TIER_CAT2: 'cat2',
    _NEKO_IDLE_TIER_CAT3: 'cat3',
    _NEKO_CAT_MIND_ACTION_RESULTS: { DONE: 'done' },
    _isNekoIdleThoughtBubbleEventHit: () => false,
    _isNekoGoodbyeIdleBallButton: () => false,
    _isNekoCatMindTransitionActive: () => false,
    _normalizeNekoIdleReturnTier: (value) => value,
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: RETURN_PATH });
  const event = {
    button: 2, prevented: 0, stopped: 0,
    preventDefault() { this.prevented += 1; },
    stopPropagation() { this.stopped += 1; },
  };
  assert.equal(context._handleNekoIdleSleepTierScaredContextMenu(
    { getAttribute: () => 'cat2' }, event,
  ), false);
  assert.equal(demotions, 0);
  assert.deepEqual([event.prevented, event.stopped], [0, 0]);
});

test('sleep-tier right-click stays atomic while a transition hard gate is active', () => {
  const source = between(
    RETURN_SOURCE,
    'const _NEKO_IDLE_CAT1_VISUAL_ACTION_OTHER_RANDOM_REQUESTS',
    'Object.assign(AvatarButtonMixin.methods',
    'sleep-tier scared contextmenu',
  );
  let demotions = 0;
  let requests = 0;
  const context = {
    window: {
      nekoAutoGoodbye: { demoteVisualTierToCat1() { demotions += 1; return true; } },
      NekoCatIdlePresentation: { requestCat1Scared() { requests += 1; return true; } },
    },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_TIER_CAT2: 'cat2',
    _NEKO_IDLE_TIER_CAT3: 'cat3',
    _NEKO_CAT_MIND_ACTION_RESULTS: { DONE: 'done' },
    _isNekoIdleThoughtBubbleEventHit: () => false,
    _isNekoGoodbyeIdleBallButton: () => false,
    _isNekoCatMindTransitionActive: () => true,
    _normalizeNekoIdleReturnTier: (value) => value,
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: RETURN_PATH });
  const event = {
    button: 2, prevented: 0, stopped: 0,
    preventDefault() { this.prevented += 1; },
    stopPropagation() { this.stopped += 1; },
  };
  assert.equal(context._handleNekoIdleSleepTierScaredContextMenu(
    { getAttribute: () => 'cat2' }, event,
  ), false);
  assert.equal(demotions, 0);
  assert.equal(requests, 0);
  assert.deepEqual([event.prevented, event.stopped], [0, 0]);
});

test('sleep-tier actions stay independent from middle-button input', () => {
  assert.doesNotMatch(RETURN_SOURCE, /_NEKO_IDLE_SLEEP_VISUAL_ACTION_PREVIEW_REQUESTS/);
  assert.doesNotMatch(RETURN_SOURCE, /_handleNekoIdleSleepVisualActionMiddleClick/);
  assert.doesNotMatch(RETURN_SOURCE, /__nekoIdleSleepVisualActionPreviewState/);
});

test('user visual actions preempt only the active Cat Mind audio before normal gates run', () => {
  const h = harness();
  const interruptions = [];
  h.gates.tier = 'cat3';
  h.gates.audio = true;
  h.context._getNekoCatMindRuntimeGateSnapshot = () => ({
    validCatRuntime: true,
    tier: h.gates.tier,
    returnPending: false,
    dragPending: false,
    dragging: false,
    edgePeekActive: false,
    transitionActive: false,
    activeIndependentAction: h.gates.independent || h.gates.audio,
    returnBallVisible: true,
    chatSurfaceDragging: false,
    yarnDragActive: false,
    yarnSettling: false,
  });
  h.context._findNekoCatMindVisibleButtonForTier = () => h.button;
  h.context._stopNekoIdleSleepSoundAudio = (options) => {
    interruptions.push({ kind: 'sleep', ...options });
    h.gates.audio = false;
  };
  h.context._stopNekoIdleCat1AmbientSoundAudio = (options) => {
    interruptions.push({ kind: 'cat1', ...options });
    h.gates.audio = false;
  };

  assert.equal(h.context.requestVisual(
    'cat3Turn2', { button: h.button }
  ), false, 'non-user presentation still respects audio ownership');
  assert.equal(interruptions.length, 0);

  assert.equal(h.context.requestVisual(
    'cat3Turn2', { button: h.button, userInitiated: true }
  ), true);
  assert.deepEqual(interruptions, [{ kind: 'sleep', reason: 'user-visual-action' }]);
  assert.equal(h.button.__nekoIdleCat1VisualActionState.actionKey, 'cat3Turn2');
});

test('individual visual APIs stay presentation-only while Cat Mind uses grouped random and timed actions', () => {
  assert.match(ACTIONS_SOURCE, /presentationApi\[profile\.requestName\]/);
  const requestPattern = new RegExp(
    `(?:${Object.values(CASES).map((entry) => entry.requestApi).join('|')})\\s*\\(`,
  );
  const wrappers = Object.values(CASES).map((entry) => entry.wrapper).filter(Boolean);
  const wrapperPattern = wrappers.length
    ? new RegExp(`(?:${wrappers.join('|')})\\s*\\(`)
    : null;
  for (const filename of javascriptFilesUnder(path.join(ROOT, 'static'))) {
    if (filename === ACTIONS_PATH || filename === RETURN_PATH) continue;
    const source = fs.readFileSync(filename, 'utf8');
    const relative = path.relative(ROOT, filename);
    assert.doesNotMatch(source, requestPattern, `${relative} has no additional visual action trigger`);
    if (wrapperPattern) {
      assert.doesNotMatch(source, wrapperPattern, `${relative} does not bypass the public API`);
    }
  }
  assert.match(RETURN_SOURCE, /_NEKO_IDLE_CAT1_VISUAL_ACTION_OTHER_RANDOM_REQUESTS/);
  assert.match(RETURN_SOURCE, /presentation\[requestName\]/);
  assert.doesNotMatch(RETURN_SOURCE, /_playNekoIdleCat1VisualAction\s*\(/);
  const catMindPattern = new RegExp(Object.values(CASES).flatMap((entry) => [
    entry.capability,
    entry.requestApi,
  ]).join('|'));
  for (const relative of [
    'static/app/app-cat-mind.js',
    'static/avatar/avatar-ui-buttons/idle-cat-mind-observations.js',
  ]) {
    const source = fs.readFileSync(path.join(ROOT, relative), 'utf8');
    assert.doesNotMatch(source, catMindPattern,
      `${relative} does not register these visual-only actions`);
  }
  assert.doesNotMatch(visualSource(), /_NEKO_CAT_MIND_ACTION_IDS|_notifyNekoCatMindRunner|catMindRunId/);
  assert.match(
    fs.readFileSync(path.join(ROOT, 'static/app/app-cat-mind.js'), 'utf8'),
    /CAT1_IDLE_RANDOM:\s*'cat1_idle_random'/,
  );
  assert.match(
    fs.readFileSync(path.join(ROOT, 'static/app/app-cat-mind.js'), 'utf8'),
    /CAT1_INACTIVE_SAD:\s*'cat1_inactive_sad'/,
  );
  assert.match(
    fs.readFileSync(path.join(ROOT, 'static/avatar/avatar-ui-buttons/idle-assets-and-question.js'), 'utf8'),
    /_playNekoCatMindIdleRandomVisualAction/,
  );
});

test('all interruption paths cancel the shared owner and action CSS does not alter layout', () => {
  for (const relative of [
    'static/avatar/avatar-ui-buttons/core.js',
    'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js',
    'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js',
    'static/avatar/avatar-ui-buttons/idle-playground.js',
    'static/avatar/avatar-ui-buttons/methods-return.js',
  ]) {
    const source = fs.readFileSync(path.join(ROOT, relative), 'utf8');
    assert.match(source, /_cancelNekoIdleCat1VisualAction\s*\(/, `${relative} cancels visual owner`);
    assert.doesNotMatch(source, /_cancelNekoIdleCat1HappyAction\s*\(/, `${relative} has no happy-only cleanup`);
  }
  const independent = between(
    ACTIONS_SOURCE,
    'function _isNekoIdleCat1IndependentActionActive',
    'function _clearNekoIdleCat1PlayActionTimers',
    'independent action gates',
  );
  assert.match(independent, /_isNekoIdleCat1VisualActionActive\(button\)/);
  assert.match(independent, /_isAnyNekoIdleCat1VisualActionActive\(\)/);

  const css = fs.readFileSync(path.join(ROOT, 'static/css/index.css'), 'utf8');
  const commonRules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter((match) => match[1].includes('.is-cat1-visual-action'));
  assert.ok(commonRules.length > 0, 'shared visual action class hides overlays');
  for (const [, selector, declarations] of commonRules) {
    if (selector.includes('data-neko-cat1-visual-layout')) continue;
    assert.doesNotMatch(
      declarations,
      /(?:^|;)\s*(?:transform|translate|scale|position|inset|top|right|bottom|left|width|height|margin|padding|object-fit)\s*:/,
      `${selector.trim()} does not change normal action layout`,
    );
  }

  const transitionSource = fs.readFileSync(path.join(ROOT, 'static/app/app-ui/return-transitions.js'), 'utf8');
  assert.match(transitionSource, /'is-cat1-visual-action'/);
  assert.match(transitionSource, /removeAttribute\(['"]data-neko-cat1-visual-action['"]\)/);
  assert.match(transitionSource, /removeAttribute\(['"]data-neko-cat1-visual-layout['"]\)/);
  assert.match(
    transitionSource,
    /visualActionClassNames\.forEach\(\(className\) => container\.classList\.remove\(className\)\)/,
    'ball fallback clears generic and legacy visual classes from the container too',
  );
});

test('wide and tall visual layouts compensate once in each rendering context', () => {
  const css = fs.readFileSync(path.join(ROOT, 'static/css/index.css'), 'utf8');
  const mainBase = cssDeclarations(css, '.neko-idle-return-art');
  const mirrorBase = cssDeclarations(css, '.neko-idle-cat1-compact-mirror-art');
  const cases = [
    {
      layout: 'wide-centered',
      mainExpected: {
        width: '175%', minWidth: '175%', maxWidth: 'none', height: '100%',
        position: 'relative',
      },
      mirrorExpected: {
        width: '175%', minWidth: '175%', maxWidth: 'none', height: '100%',
        position: 'relative', left: '-37.5%',
      },
      absentFromMainRule: ['left'],
    },
    {
      layout: 'tall-bottom',
      mainExpected: {
        width: '100%', height: '137.5%', minHeight: '137.5%', maxHeight: 'none',
        position: 'relative', alignSelf: 'flex-end', objectPosition: 'center bottom',
      },
      mirrorExpected: {
        width: '100%', height: '137.5%', minHeight: '137.5%', maxHeight: 'none',
        position: 'relative', top: '-37.5%', objectPosition: 'center bottom',
      },
      absentFromMainRule: ['top'],
    },
  ];
  const toCamel = (property) => property.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  const camelCase = (declarations) => Object.fromEntries(
    Object.entries(declarations).map(([property, value]) => [toCamel(property), value]),
  );

  for (const entry of cases) {
    const mainRule = camelCase(cssDeclarations(
      css,
      `.neko-idle-return-btn[data-neko-cat1-visual-layout="${entry.layout}"] > .neko-idle-return-art`,
    ));
    const main = {
      ...camelCase(mainBase),
      ...mainRule,
    };
    const mirror = {
      ...camelCase(mirrorBase),
      ...camelCase(cssDeclarations(
        css,
        `.neko-idle-cat1-compact-mirror[data-neko-cat1-art-layout="${entry.layout}"] .neko-idle-cat1-compact-mirror-art`,
      )),
    };
    for (const [property, value] of Object.entries(entry.mainExpected)) {
      assert.equal(main[property], value, `${entry.layout} main ${property}`);
    }
    for (const [property, value] of Object.entries(entry.mirrorExpected)) {
      assert.equal(mirror[property], value, `${entry.layout} mirror ${property}`);
    }
    for (const property of entry.absentFromMainRule) {
      assert.equal(mainRule[property], undefined,
        `${entry.layout} main relies on flex placement instead of a second ${property} shift`);
    }
  }
  assert.match(RETURN_SOURCE, /display:\s*'flex'/);
  assert.match(RETURN_SOURCE, /alignItems:\s*'center'/);
  assert.match(RETURN_SOURCE, /justifyContent:\s*'center'/);
});
