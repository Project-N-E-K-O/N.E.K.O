const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..', '..');
const CORE_PATH = path.join(
  ROOT,
  'static/avatar/avatar-ui-buttons/core.js',
);
const ACTIONS_PATH = path.join(
  ROOT,
  'static/avatar/avatar-ui-buttons/idle-actions-and-audio.js',
);
const DRAG_PATH = path.join(
  ROOT,
  'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js',
);
const PRESENTATION_PATH = path.join(
  ROOT,
  'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js',
);
const ACTIONS_SOURCE = fs.readFileSync(ACTIONS_PATH, 'utf8');
const CORE_SOURCE = fs.readFileSync(CORE_PATH, 'utf8');
const EAT_SOURCE = ACTIONS_SOURCE.slice(
  0,
  ACTIONS_SOURCE.indexOf('const _NEKO_IDLE_CAT1_VISUAL_ACTION_PROFILES'),
);
const EAT_SLOTS = Object.freeze([
  'action.cat1.eat',
  'action.cat1.eat_2',
  'action.cat1.eat_3',
]);
const URL_BY_SLOT = Object.freeze({
  'action.cat1.eat': 'eat.gif?v=1',
  'action.cat1.eat_2': 'eat-2.gif?v=1',
  'action.cat1.eat_3': 'eat-3.gif?v=1',
});

test('the production eat pool declares exactly the three intended slots', () => {
  const match = CORE_SOURCE.match(
    /const _NEKO_IDLE_CAT1_EAT_ASSET_SLOTS = Object\.freeze\(\[([\s\S]*?)\]\);/,
  );
  assert.ok(match, 'eat asset slot list');
  const actualSlots = Array.from(match[1].matchAll(/'([^']+)'/g), (entry) => entry[1]);
  assert.deepEqual(actualSlots, Array.from(EAT_SLOTS));
});

function classList() {
  const values = new Set();
  return {
    values,
    toggle(name, active) {
      if (active) values.add(name);
      else values.delete(name);
    },
  };
}

function harness(randomAt = () => 0) {
  let randomCalls = 0;
  const math = Object.create(Math);
  math.random = () => {
    const value = randomAt(randomCalls);
    randomCalls += 1;
    return value;
  };
  const art = { src: 'idle.gif?v=1' };
  const button = {
    classList: classList(),
    getAttribute(name) {
      return name === 'data-neko-idle-tier' ? 'cat1' : '';
    },
    querySelector(selector) {
      return selector === '.neko-idle-return-art' ? art : null;
    },
  };
  const container = { classList: classList(), style: { display: 'block' } };
  const appearanceReads = [];
  const artWrites = [];
  const mirrorWrites = [];
  const durationReads = [];
  const context = {
    Math: math,
    Date,
    Number,
    Object,
    Promise,
    setTimeout: () => 1,
    clearTimeout: () => {},
    _NEKO_IDLE_CAT1_EAT_ASSET_SLOTS: EAT_SLOTS,
    _NEKO_IDLE_TIER_NONE: 'none',
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_IDLE_CAT1_EAT_SOUND_VOLUME: 0.12,
    _NEKO_IDLE_CAT1_EAT_SOUND_FALLBACK_MS: 5000,
    _NEKO_CAT_MIND_ACTION_IDS: { CAT1_EAT_SNACK: 'cat1_eat_snack' },
    _getNekoCatAppearanceUrl(slot, options) {
      appearanceReads.push([slot, options]);
      return URL_BY_SLOT[slot] || '';
    },
    _isNekoCatActionResourceAvailable: () => true,
    _isNekoIdleDesktopWindowInteractionActionActive: () => false,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    _normalizeNekoIdleReturnTier: (tier) => tier,
    _isNekoIdleReturnDragActionActive: () => false,
    _isNekoIdleCat1StretchActionActive: () => false,
    _isNekoIdleCat1VisualActionActive: () => false,
    _getNekoIdleReturnContainerFromButton: () => container,
    _cancelNekoIdleCat1PlayAction: () => {},
    _pauseNekoIdleCat1Journey: () => {},
    _stopNekoIdleCat1AmbientSoundAudio: () => {},
    _fadeOutNekoIdleCat1DragSound: () => {},
    _clearNekoIdleHoverPlayback: () => {},
    _cleanupNekoIdleArtTransition: () => {},
    _clearNekoIdleGifPlaybackSource: () => {},
    _beginNekoCatMindStateAction: () => ({ runId: 'eat-run' }),
    _notifyNekoCatMindRunnerAccepted: () => {},
    _notifyNekoCatMindRunnerStarted: () => {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton: () => {},
    _setNekoIdleReturnArtSource(_art, url, tier, options) {
      artWrites.push({ url, tier, options });
      art.src = url;
    },
    _syncNekoIdleCat1CompactMirrorReaction(_button, _container, url, reason) {
      mirrorWrites.push({ url, reason });
    },
    _getNekoIdleAnimationDurationMs(url) {
      durationReads.push(url);
      return new Promise(() => {});
    },
    _playNekoIdleSound: () => null,
    _getNekoCatVoiceUrl: () => 'eat.mp3?v=1',
    _stopNekoIdleSoundAudio: () => {},
    _getNekoCatMindCancelResult: () => 'interrupted',
    _reportNekoCatMindStateActionResult: () => {},
    _syncNekoIdleCat1AmbientSoundForTier: () => {},
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif?v=1',
    _resumeNekoIdleCat1Journey: () => {},
    _scheduleNekoIdleCat1JourneySync: () => {},
    _forEachNekoIdleReturnButton: () => {},
  };
  vm.createContext(context);
  vm.runInContext(EAT_SOURCE, context, { filename: ACTIONS_PATH });
  return {
    context,
    button,
    art,
    appearanceReads,
    artWrites,
    mirrorWrites,
    durationReads,
    randomCalls: () => randomCalls,
  };
}

test('eat variants divide the random interval into three equal ranges', () => {
  const sampleCount = 300;
  const h = harness((index) => (index + 0.5) / sampleCount);
  const counts = new Map(EAT_SLOTS.map((slot) => [URL_BY_SLOT[slot], 0]));
  for (let index = 0; index < sampleCount; index += 1) {
    const url = h.context._pickNekoIdleCat1EatActionAssetUrl();
    counts.set(url, counts.get(url) + 1);
  }
  assert.deepEqual(Array.from(counts.values()), [100, 100, 100]);
  assert.equal(h.randomCalls(), sampleCount);
  assert.ok(h.appearanceReads.every(([, options]) => options.random === false));
});

test('eat interval boundaries map to the intended variants', () => {
  const epsilon = Number.EPSILON;
  const values = [0, (1 / 3) - epsilon, 1 / 3, (2 / 3) - epsilon, 2 / 3, 1 - epsilon];
  const expected = [
    'eat.gif?v=1',
    'eat.gif?v=1',
    'eat-2.gif?v=1',
    'eat-2.gif?v=1',
    'eat-3.gif?v=1',
    'eat-3.gif?v=1',
  ];
  const h = harness((index) => values[index]);
  assert.deepEqual(
    expected.map(() => h.context._pickNekoIdleCat1EatActionAssetUrl()),
    expected,
  );
});

test('one accepted eat run locks one variant for art, mirror, duration and active lookups', () => {
  const h = harness(() => 0.5);
  assert.equal(h.context._playNekoIdleCat1EatAction(h.button), true);
  assert.equal(h.randomCalls(), 1);
  assert.equal(h.context._getNekoIdleCat1EatActionAssetUrl(h.button), 'eat-2.gif?v=1');
  assert.equal(h.artWrites.length, 1);
  assert.equal(h.artWrites[0].url, 'eat-2.gif?v=1');
  assert.equal(h.artWrites[0].tier, 'cat1');
  assert.equal(h.artWrites[0].options.animate, false);
  assert.deepEqual(h.mirrorWrites, [{ url: 'eat-2.gif?v=1', reason: 'cat1-eat-action' }]);
  assert.deepEqual(h.durationReads, ['eat-2.gif?v=1']);

  assert.equal(h.context._getNekoIdleCat1EatActionAssetUrl(h.button), 'eat-2.gif?v=1');
  assert.equal(h.randomCalls(), 1, 'active lookups never redraw the variant');
  h.context._cancelNekoIdleCat1EatAction(h.button, {
    animate: false,
    reason: 'test-cancel',
  });
  assert.equal(h.artWrites.at(-1).url, 'idle.gif?v=1');
  assert.equal(h.mirrorWrites.at(-1).url, 'idle.gif?v=1');
  assert.equal(h.context._getNekoIdleCat1EatActionAssetUrl(h.button), '');
  assert.equal(h.button.__nekoIdleCat1EatActionState.assetUrl, '');
});

test('rejected eat requests do not consume a random selection', () => {
  const h = harness(() => 0.5);
  h.context._isNekoIdleDesktopWindowInteractionActionActive = () => true;
  assert.equal(h.context._playNekoIdleCat1EatAction(h.button), false);
  assert.equal(h.randomCalls(), 0);
});

test('all active eat art consumers read the run-scoped variant getter', () => {
  const dragSource = fs.readFileSync(DRAG_PATH, 'utf8');
  const presentationSource = fs.readFileSync(PRESENTATION_PATH, 'utf8');
  assert.match(
    dragSource,
    /_isNekoIdleCat1EatActionActive\(button\)[\s\S]*return _getNekoIdleCat1EatActionAssetUrl\(button\)/,
  );
  assert.match(
    presentationSource,
    /eatActionActive && normalizedTier === _NEKO_IDLE_TIER_CAT1[\s\S]*_getNekoIdleCat1EatActionAssetUrl\(button\)/,
  );
  assert.doesNotMatch(
    `${ACTIONS_SOURCE}\n${dragSource}\n${presentationSource}`,
    /_NEKO_IDLE_CAT1_EAT_ASSET_URL\b/,
  );
});
