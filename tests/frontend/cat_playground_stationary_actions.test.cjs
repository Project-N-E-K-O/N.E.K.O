const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.NEKO_TEST_SOURCE_ROOT || path.resolve(__dirname, '../..');
const avatar = path.join(root, 'static/avatar/avatar-ui-buttons');
const visualKeys = ['happy', 'touch', 'cosplay1', 'change', 'cosplay2', 'eat2', 'eat3',
  'laugh', 'lionDance', 'love1', 'mouse', 'banana', 'catnip1', 'catnip2', 'cry',
  'ninjutsu', 'wag1', 'wag2', 'oiia', 'wave', 'sad', 'sad2', 'scared', 'stroke',
  'wagging', 'cat2Back1_1', 'cat2Turn1', 'cat3Turn2', 'cat3Back1_1', 'cat3Back1_2'];

function classList() {
  const names = new Set();
  return { add: (...values) => values.forEach(value => names.add(value)),
    remove: (...values) => values.forEach(value => names.delete(value)),
    contains: value => names.has(value),
    toggle(value, active) { if (active) names.add(value); else names.delete(value); } };
}

// All browser scripts, runners, providers, resource metadata, current-art and
// body/hull functions are production code. DOM/image/audio and RAF delivery are
// controlled here; a dedicated case also advances the real physics frame chain.
function runtime(tier = 'cat1', { playground = true, catOnly = false } = {}) {
  let now = 1000, sequence = 0, randomValue = 0, randomCalls = 0;
  const timers = new Map(), frames = new Map(), events = [], audios = [], images = [], writes = [];
  const windowListeners = new Map();
  const controls = { collisionActive: false, audioEnabled: true, audioDelay: false };
  function element(tag = 'div') {
    const attrs = new Map(), listeners = new Map(), children = [];
    const result = {
      tagName: tag.toUpperCase(), isConnected: true, classList: classList(),
      style: { setProperty(name, value) { this[name] = String(value); },
        removeProperty(name) { delete this[name]; } }, dataset: {},
      offsetWidth: 96, complete: false, naturalWidth: 512,
      getAttribute: name => attrs.has(name) ? attrs.get(name) : null,
      setAttribute: (name, value) => attrs.set(name, String(value)),
      removeAttribute: name => attrs.delete(name), hasAttribute: name => attrs.has(name),
      addEventListener(type, callback, options = {}) {
        const group = listeners.get(type) || [];
        group.push({ callback, once: options.once === true }); listeners.set(type, group);
      },
      removeEventListener(type, callback) {
        listeners.set(type, (listeners.get(type) || []).filter(item => item.callback !== callback));
      },
      dispatchEvent(event) {
        for (const item of [...(listeners.get(event.type) || [])]) {
          if (item.once) result.removeEventListener(event.type, item.callback);
          item.callback(event);
        }
        result[`on${event.type}`]?.(event);
      },
      appendChild(child) { children.push(child); child.parentNode = result; return child; },
      removeChild(child) { children.splice(children.indexOf(child), 1); child.parentNode = null; return child; },
      querySelector: () => null,
      getBoundingClientRect() {
        const left = Number.parseFloat(result.style.left) || 100;
        const top = Number.parseFloat(result.style.top) || 100;
        return { left, top, right: left + 96, bottom: top + 96, width: 96, height: 96 };
      },
    };
    Object.defineProperty(result, 'src', {
      get: () => result.getAttribute('src') || '',
      set(value) { result.setAttribute('src', value); result.complete = false; writes.push(value); },
    });
    if (tag === 'img') images.push(result);
    return result;
  }
  const container = element(), button = element('button'), art = element('img');
  const bubble = element(), bubbleBg = element(), bubbleItem = element('img');
  container.id = 'live2d-return-button-container'; button.id = 'live2d-btn-return';
  container.style.display = 'block';
  button.setAttribute('data-neko-idle-tier', tier); container.setAttribute('data-neko-idle-tier', tier);
  container.setAttribute('data-neko-return-visible', 'true');
  container.querySelector = () => button;
  button.querySelector = selector => ({ '.neko-idle-return-art': art,
    '.neko-idle-thought-bubble': bubble, '.neko-idle-thought-bubble-bg': bubbleBg,
    '.neko-idle-thought-bubble-item': bubbleItem })[selector] || null;
  button.closest = () => container; art.closest = () => button;
  const window = {
    APP_VERSION: 'stationary-actions-test', innerWidth: 1200, innerHeight: 900,
    screenX: 0, screenY: 0, location: { href: 'http://neko.test/', pathname: '/' },
    localStorage: { getItem: key => key === 'neko.idleCatAudio.enabled' && !controls.audioEnabled ? 'false' : null },
    sessionStorage: { getItem: () => null },
    addEventListener(type, callback) {
      const group = windowListeners.get(type) || []; group.push(callback); windowListeners.set(type, group);
    },
    dispatchEvent(event) { events.push(event); for (const callback of windowListeners.get(event.type) || []) callback(event); return true; },
    requestAnimationFrame(callback) { frames.set(++sequence, callback); return sequence; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(callback, delay = 0) { timers.set(++sequence, { callback, at: now + Math.max(0, delay) }); return sequence; },
    clearTimeout(id) { timers.delete(id); }, matchMedia: () => ({ matches: false }),
    getComputedStyle: value => ({ display: value.style.display || 'block',
      visibility: value.style.visibility || 'visible', opacity: value.style.opacity ?? '1' }),
    NekoCollisionDeformation: { isCollisionDeformationActive: () => controls.collisionActive },
    Audio: class extends EventTarget {
      constructor(src) { super(); Object.assign(this, { src, volume: 1, currentTime: 0,
        duration: 7.18, paused: true, ended: false, starts: [] }); audios.push(this); }
      play() { this.paused = false; this.starts.push(now);
        if (!controls.audioDelay) return Promise.resolve();
        return new Promise((resolve, reject) => { this.resolvePlay = resolve; this.rejectPlay = reject; }); }
      pause() { this.paused = true; }
      end() { this.ended = true; this.dispatchEvent(new Event('ended')); }
    },
    Image: class { set src(value) { this._src = value; } },
  };
  const document = { hidden: false, visibilityState: 'visible', readyState: 'complete', body: element(),
    getElementById: () => null, querySelector: () => null,
    querySelectorAll: selector => selector.includes('return') ? [button] : [],
    addEventListener() {}, createElement: element };
  const math = Object.create(Math); math.random = () => { randomCalls++; return randomValue; };
  const context = vm.createContext({ window, document, console, Math: math, URL, Event, EventTarget,
    fetch: async source => {
      const buffer = fs.readFileSync(path.join(root, new URL(source, window.location.href).pathname));
      return { ok: true, arrayBuffer: async () => buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength) };
    },
    Date: { now: () => now }, performance: { now: () => now },
    CustomEvent: function(type, init = {}) { this.type = type; this.detail = init.detail || {}; },
    requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame: window.cancelAnimationFrame,
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout });
  for (const name of ['cat-resource-registry.js', 'core.js', 'idle-assets-and-question.js',
    'idle-playground.js', 'idle-actions-and-audio.js', 'idle-drag-and-subactions.js',
    'idle-journey-and-presentation.js']) {
    const source = fs.readFileSync(path.join(avatar, name), 'utf8');
    vm.runInContext(name === 'idle-journey-and-presentation.js'
      ? source.replace(/\n_ensureNekoIdleReturnPresentationBridge\(\);\s*$/, '\n') : source,
    context, { filename: name });
  }
  const no = () => false;
  Object.assign(context, {
    _getNekoGoodbyeIdleAppearance: () => 'cat',
    _readNekoAutoGoodbyeVisualTier: () => button.getAttribute('data-neko-idle-tier'),
    _getNekoIdleReturnContainerFromButton: value => value === button ? container : null,
    _getNekoIdleReturnButtonFromArt: value => value === art ? button : null,
    _getNekoIdleReturnButtonFromContainer: value => value === container ? button : null,
    _forEachNekoIdleReturnButton: callback => callback(button),
    _isNekoIdleDesktopWindowInteractionActionActive: no, _isNekoIdleDesktopWindowTopEdgeActionActive: no,
    _isNekoIdleDesktopWindowEdgePeekActionActive: no, _isNekoIdleDesktopWindowDoorWalkActionActive: no,
    _isNekoIdleCat1EdgePeekActive: no, _isNekoIdleCompactSurfaceDragging: no,
    _getNekoIdleCat1PairMoveChatTarget: () => null,
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt() {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton() {},
    _scheduleNekoIdleCat1JourneySync() {},
  });
  // Real ordinary journey cancellation leaves this object behind on entry.
  // It must never be treated as the current owner of a settled Playground cat.
  const journey = context._getNekoIdleCat1Journey(button);
  context._cancelNekoIdleCat1Journey(button, { reason: 'playground-entry' });
  if (!playground) journey.actionSettled = true;
  const state = context._getNekoIdleCat1PlaygroundDropState(button);
  art.src = context._getNekoIdleReturnAssetUrl(tier);
  const body = context._createNekoIdleCat1PlaygroundPhysicsBody('cat', container);
  body.grounded = true;
  context._updateNekoIdleCat1PlaygroundBodyBounds(body); body.y = body.floorY;
  context._setNekoIdleCat1PlaygroundBodyPosition(body, body.x, body.y);
  Object.assign(state, { active: playground, released: !playground, container, button,
    phase: 'settled', frame: 41, catOnly, yarnEnabled: !catOnly,
    bodies: new Map([['cat', body], ...(!catOnly ? [['yarn', { id: 'yarn', vx: 160, vy: 30, grounded: false }]] : [])]) });
  const demotions = [];
  window.nekoAutoGoodbye = { demoteVisualTierToCat1(reason) {
    demotions.push(reason); button.setAttribute('data-neko-idle-tier', 'cat1');
    container.setAttribute('data-neko-idle-tier', 'cat1'); context._syncNekoIdleReturnCurrentArt(button, 'cat1');
    context._syncNekoIdleCat1PlaygroundCatGeometry(button); return true;
  } };
  const acknowledgements = [];
  window.nekoCatMind = { getState: () => ({ active: true, tier: button.getAttribute('data-neko-idle-tier'), enteredAt: 500 }),
    acknowledgeActionRequest(value) { acknowledgements.push(value); return true; }, observe: () => true };
  writes.length = 0;
  const api = {
    context, window, document, state, body, journey, button, container, art, bubble, bubbleItem,
    controls, events, audios, images, timers, frames, writes, acknowledgements, demotions,
    now: () => now, random(value) { randomValue = value; }, randomCalls: () => randomCalls,
    profile: key => context._getNekoIdleCat1VisualActionProfile(key),
    duration: url => context._getNekoCatAppearanceDurationMs(url),
    decision: id => context._dryRunNekoCatMindActionProvider(id, { button }),
    visualState: () => context._getNekoIdleCat1VisualActionState(button),
    eatState: () => context._getNekoIdleCat1EatActionState(button),
    stretchState: () => context._getNekoIdleCat1StretchActionState(button),
    results: () => events.filter(event => event.type === 'neko:cat-mind:action-result').map(event => event.detail),
    request(id, requestId = 'request-1') {
      context._runNekoCatMindActionRequest({ actionId: id, source: 'cat_mind',
        tier: button.getAttribute('data-neko-idle-tier'), requestId });
    },
    monitor() { context._syncNekoIdleCat1PlaygroundStationaryActions(button); },
    async flush() { for (let i = 0; i < 12; i++) await Promise.resolve(); },
    load(image = art) { image.complete = true; image.naturalWidth = 512; image.dispatchEvent(new Event('load')); },
    fail(image = art) { image.complete = true; image.naturalWidth = 0; image.dispatchEvent(new Event('error')); },
    async advance(ms) {
      const target = now + ms;
      for (let pass = 0; pass < 10000; pass++) {
        const next = [...timers].filter(([, value]) => value.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
        if (!next) break;
        now = next[1].at; timers.delete(next[0]); next[1].callback(); await api.flush();
        if (pass === 9999) assert.fail('timer loop did not settle');
      }
      now = target; await api.flush();
    },
    async enter() {
      // Preload images are independent from the main image in real transitions.
      for (const image of images) if (image !== art && image.src) api.load(image);
      if (art.__nekoIdleTransitionTo) await api.advance(820);
      api.load(); await api.flush();
    },
  };
  return api;
}

function tierFor(key) { return key.startsWith('cat2') ? 'cat2' : key.startsWith('cat3') ? 'cat3' : 'cat1'; }
function sameSource(r, expected) {
  assert.equal(r.context._isNekoIdleAnimationRestartUrlForSource(r.art.src, expected), true, r.art.src);
}
function assertPhysicsOwner(r, state = r.state, body = r.body) {
  assert.equal(r.button.__nekoIdleCat1PlaygroundDropState, state);
  assert.equal(state.bodies.get('cat'), body);
  assert.equal(state.active, true); assert.equal(state.released, false);
  assert.equal(body.vx, 0); assert.equal(body.vy, 0); assert.equal(body.grounded, true);
  assert.equal(Number.parseFloat(r.container.style.left), Math.round(body.x));
  assert.equal(Number.parseFloat(r.container.style.top), Math.round(body.y));
}

for (const playground of [false, true]) {
  for (const key of visualKeys) {
    test(`${playground ? 'Playground' : 'ordinary'}: ${key} runs its registered full cycle and restores current tier`, async () => {
      const r = runtime(tierFor(key), { playground, catOnly: key === 'oiia' });
      const profile = r.profile(key), terminal = [];
      assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, key, {
        onTerminal: (result, detail) => terminal.push({ result, detail }),
      }), true);
      const actionSrc = r.context._getNekoIdleCat1VisualActionAssetUrlForSlot(profile.slot);
      await r.enter(); sameSource(r, actionSrc);
      const duration = r.duration(actionSrc) * (profile.repeatCount || 1);
      assert.ok(duration > 0, 'use registry duration, not a duplicate timer constant');
      await r.advance(duration - 1); assert.equal(r.visualState().active, true);
      await r.advance(1); assert.equal(r.visualState().active, false);
      assert.equal(terminal.length, 1); assert.equal(terminal[0].result, 'done');
      assert.equal(r.button.getAttribute('data-neko-idle-tier'), profile.terminalTier || tierFor(key));
      sameSource(r, r.context._getNekoIdleReturnCurrentArtUrl(r.button, r.button.getAttribute('data-neko-idle-tier')));
      if (playground) assertPhysicsOwner(r);
      r.context._cancelNekoIdleCat1VisualAction(r.button); await r.advance(duration + 1000);
      assert.equal(terminal.length, 1, 'completion and cancellation cannot both report');

      const cancelled = runtime(tierFor(key), { playground });
      const cancelledTerminal = [];
      assert.equal(cancelled.context._playNekoIdleCat1VisualAction(cancelled.button, key, {
        onTerminal: result => cancelledTerminal.push(result),
      }), true);
      await cancelled.enter(); await cancelled.advance(1);
      cancelled.context._cancelNekoIdleCat1VisualAction(cancelled.button, { reason: 'stationary-test-cancelled' });
      cancelled.load(); await cancelled.advance(duration + 1000);
      assert.equal(cancelled.visualState().active, false);
      assert.deepEqual(cancelledTerminal, ['cancelled']);
      assert.equal(cancelled.button.getAttribute('data-neko-idle-tier'), tierFor(key), 'cancellation never demotes');
      assert.ok(cancelled.audios.every(value => value.paused));
      if (playground) assertPhysicsOwner(cancelled);
    });
  }
}

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: semantic provider uses settled physics owner despite cancelled ordinary journey`, () => {
    const r = runtime(tier);
    assert.equal(r.journey.actionSettled, false);
    assert.equal(r.context._getNekoCatMindRuntimeGateSnapshot().cat1PositionPresentationBusy, false);
    assert.equal(r.decision(`${tier}_idle_random`).allowed, true);
    if (tier === 'cat1') {
      assert.equal(r.decision('cat1_eat_snack').allowed, true);
      assert.equal(r.decision('cat1_inactive_sad').allowed, true);
    }
    assert.equal(r.randomCalls(), 0, 'eligibility must not consume action selection');
    assert.equal(r.audios.length, 0);
  });
  test(`${tier}: catOnly semantic request keeps one body through its loaded action and terminal tier`, async () => {
    const r = runtime(tier, { catOnly: true });
    // Select the existing turn (which stays in its tier), with no extra selector.
    r.random(0); r.request(`${tier}_idle_random`);
    assert.deepEqual(r.acknowledgements.map(value => value.status), ['accepted']);
    await r.enter();
    assert.deepEqual(r.acknowledgements.map(value => value.status), ['accepted', 'started']);
    await r.advance(r.duration(r.art.src));
    assert.equal(r.results().length, 1); assert.equal(r.results()[0].result, 'done');
    assert.deepEqual([...r.state.bodies.keys()], ['cat']); assertPhysicsOwner(r);
  });
}

test('original random pool and sleep weights consume exactly one selection draw', () => {
  const r = runtime();
  const cat1 = ['happy', 'touch', 'laugh', 'love1', 'wagging', 'banana', 'ninjutsu', 'oiia'];
  for (let index = 0; index < cat1.length; index++) {
    r.random((index + 0.25) / 8);
    assert.equal(r.context._pickNekoIdleRandomVisualActionKey('cat1'), cat1[index]);
  }
  for (const [tier, roll, expected] of [
    ['cat2', 0, 'cat2Turn1'], ['cat2', 0.799999, 'cat2Turn1'], ['cat2', 0.8, 'cat2Back1_1'],
    ['cat3', 0.799999, 'cat3Turn2'], ['cat3', 0.8, 'cat3Back1_1'], ['cat3', 0.9, 'cat3Back1_2'],
  ]) { r.random(roll); assert.equal(r.context._pickNekoIdleRandomVisualActionKey(tier), expected); }
  assert.equal(r.randomCalls(), 14);
});

for (const [label, change] of [
  ['airborne', r => { r.body.grounded = false; }],
  ['sliding', r => { r.body.vx = 2; }],
  ['collision deformation', r => { r.controls.collisionActive = true; }],
  ['pending press', r => { r.state.pointerBodyId = 'cat'; }],
  ['actual drag', r => { r.state.draggingBodyId = 'cat'; r.body.dragging = true; }],
  ['entry pending', r => { r.button.__nekoIdleCat1PlaygroundPendingEntry = {}; }],
  ['foreign art transition', r => { r.art.__nekoIdleTransitionTo = 'foreign.webp'; }],
  ['hidden cat', r => { r.container.style.display = 'none'; }],
]) {
  test(`stationary starts reject ${label} without selecting art or audio`, () => {
    const r = runtime(); change(r);
    for (const id of ['cat1_eat_snack', 'cat1_idle_random', 'cat1_inactive_sad']) assert.equal(r.decision(id).allowed, false, id);
    assert.equal(r.context._playNekoIdleCat1EatAction(r.button), false);
    assert.equal(r.context._playNekoIdleCat1StretchAction(r.button), false);
    assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'mouse'), false);
    assert.equal(r.randomCalls(), 0); assert.equal(r.audios.length, 0); assert.equal(r.writes.length, 0);
  });
}

test('unadapted position consumers remain closed and playing requires a real reachable yarn', () => {
  const r = runtime();
  for (const key of ['ordinary-drag', 'return-container-position',
    'return-art-source', 'compact-follow', 'dock-return-goodbye', 'edge-peek', 'unknown']) {
    assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, key), true, key);
  }
  assert.equal(r.decision('cat1_play_yarn').allowed, false);
  assert.equal(r.context._playNekoIdleCat1PlayAction(r.button), false);
  assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button), true);
});

test('delayed visual image load anchors the full animation and semantic started acknowledgement', async () => {
  const r = runtime(); r.random(0.01); r.request('cat1_idle_random');
  assert.deepEqual(r.acknowledgements.map(value => value.status), ['accepted']);
  await r.advance(2100); assert.equal(r.visualState().timer, 0);
  r.load(); await r.flush();
  assert.deepEqual(r.acknowledgements.map(value => value.status), ['accepted', 'started']);
  const duration = r.duration(r.art.src);
  await r.advance(duration - 1); assert.equal(r.results().length, 0);
  await r.advance(1); assert.equal(r.results().length, 1); assert.equal(r.results()[0].result, 'done');
});

test('inactive sadness remains its own original semantic action', async () => {
  const r = runtime(); r.request('cat1_inactive_sad');
  assert.equal(r.visualState().actionKey, 'sad'); assert.equal(r.randomCalls(), 0);
  await r.enter(); await r.advance(r.duration(r.art.src));
  assert.equal(r.results().length, 1); assert.equal(r.results()[0].actionId, 'cat1_inactive_sad');
  assert.equal(r.results()[0].result, 'done');
});

test('OIIA remains one action with three full image cycles and 510ms music cues', async () => {
  const r = runtime(); const terminal = [];
  assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'oiia', { onTerminal: value => terminal.push(value) }), true);
  await r.advance(1400); assert.equal(r.audios.length, 0);
  const started = r.now(); r.load(); await r.flush();
  const cycle = r.duration(r.art.src); assert.equal(cycle, 1960);
  await r.advance(509); assert.equal(r.audios.filter(value => value.starts.length).length, 0);
  await r.advance(1); assert.equal(r.audios[0].starts[0], started + 510);
  await r.advance(cycle); assert.equal(r.audios[1].starts[0], started + cycle + 510);
  await r.advance(cycle); assert.equal(r.audios[2].starts[0], started + cycle * 2 + 510);
  await r.advance(cycle - 510 - 1); assert.equal(r.visualState().active, true);
  await r.advance(1); assert.equal(r.visualState().active, false); assert.deepEqual(terminal, ['done']);
  assert.ok(r.audios.every(value => value.paused));
});

test('eat waits for its loaded full image cycle and existing audio completion', async () => {
  const r = runtime(); r.request('cat1_eat_snack');
  assert.deepEqual(r.acknowledgements.map(value => value.status), ['accepted']);
  await r.advance(2200); assert.equal(r.audios.length, 0); r.load(); await r.flush();
  assert.deepEqual(r.acknowledgements.map(value => value.status), ['accepted', 'started']);
  const duration = await r.context._getNekoIdleAnimationDurationMs(r.art.src); assert.ok(duration > 0);
  r.audios[0].end(); await r.advance(duration - 1); assert.equal(r.eatState().active, true);
  await r.advance(1); assert.equal(r.eatState().active, false);
  assert.equal(r.results().length, 1); assert.equal(r.results()[0].result, 'done'); assertPhysicsOwner(r);
});

test('eat keeps the audio fallback and muted image-only completion', async () => {
  for (const muted of [false, true]) {
    const r = runtime(); r.controls.audioEnabled = !muted;
    assert.equal(r.context._playNekoIdleCat1EatAction(r.button), true); await r.enter();
    const duration = r.duration(r.art.src);
    await r.advance(duration - 1); assert.equal(r.eatState().active, true);
    await r.advance(1);
    if (!muted) {
      assert.equal(r.eatState().active, true); await r.advance(7430 - duration);
    }
    assert.equal(r.eatState().active, false); assertPhysicsOwner(r);
  }
});

test('hiss stretch starts its audio at actual entry and retains its 700ms final hold', async () => {
  const r = runtime();
  assert.equal(r.context._requestNekoIdleCat1HissStretchPresentation(), true);
  assert.equal(r.audios.length, 0); assert.ok(r.art.__nekoIdleTransitionTo);
  await r.enter(); assert.equal(r.audios.length, 1);
  const duration = await r.context._getNekoIdleAnimationDurationMs(r.art.src); assert.ok(duration > 0);
  await r.advance(duration + 699); assert.equal(r.stretchState().active, true);
  await r.advance(1); assert.equal(r.stretchState().active, false);
  assert.equal(r.audios[0].paused, true); assertPhysicsOwner(r);
});

for (const [kind, start, state, cancel] of [
  ['eat', r => r.request('cat1_eat_snack'), r => r.eatState(), '_cancelNekoIdleCat1EatAction'],
  ['stretch', r => r.context._playNekoIdleCat1StretchAction(r.button), r => r.stretchState(), '_cancelNekoIdleCat1StretchAction'],
  ['visual', r => r.request('cat1_idle_random'), r => r.visualState(), '_cancelNekoIdleCat1VisualAction'],
]) {
  test(`${kind}: image failure and stale load callbacks cannot resurrect a cancelled action`, async () => {
    const r = runtime(); start(r); assert.equal(state(r).active, true);
    r.fail(); await r.flush(); assert.equal(state(r).active, false);
    const resultCount = r.results().length;
    r.load(); r.context[cancel](r.button); await r.advance(30000);
    assert.equal(state(r).active, false); assert.equal(r.results().length, resultCount);
    assert.ok(r.audios.every(value => value.paused));
  });
  test(`${kind}: press may continue playback, actual drag interrupts exactly once`, async () => {
    const r = runtime(); start(r); await r.enter(); assert.equal(state(r).active, true);
    r.state.pointerBodyId = 'cat'; r.monitor(); assert.equal(state(r).active, true);
    r.state.draggingBodyId = 'cat'; r.body.dragging = true; r.monitor();
    assert.equal(state(r).active, false);
    const results = r.results().length;
    r.monitor(); r.load(); for (const audio of r.audios) audio.end(); await r.advance(30000);
    assert.equal(r.results().length, results); assert.ok(r.audios.every(value => value.paused));
  });
  test(`${kind}: losing physical support cancels instead of completing or moving ordinary journey`, async () => {
    const r = runtime(); start(r); await r.enter(); r.body.grounded = false; r.monitor();
    assert.equal(state(r).active, false); const count = r.results().length;
    for (const audio of r.audios) audio.end(); await r.advance(30000);
    assert.equal(r.results().length, count); assert.equal(r.journey.actionSettled, false);
    assert.equal(r.journey.paused, false); assert.equal(r.journey.frame, 0);
    if (count) assert.equal(r.results()[0].result, 'cancelled');
  });
  test(`${kind}: entry timeout and Playground release clear pending source and playback timers`, async () => {
    for (const release of [false, true]) {
      const r = runtime(); start(r); assert.equal(state(r).active, true);
      if (release) { r.state.released = true; r.state.releaseReason = 'playground-release'; r.monitor(); }
      else await r.advance(5000);
      assert.equal(state(r).active, false); assert.equal(state(r).sourceLoadCleanup, null);
      assert.equal(state(r).entryTimer, 0); assert.equal(state(r).timer, 0);
      const count = r.results().length; r.load(); await r.advance(30000);
      assert.equal(r.results().length, count); assert.ok(r.audios.every(value => value.paused));
    }
  });
}

test('owned stretch transition can continue but unrelated art transition cancels it', async () => {
  const r = runtime();
  assert.equal(r.context._playNekoIdleCat1StretchAction(r.button), true);
  r.monitor(); assert.equal(r.stretchState().active, true, 'its own entry fade is part of the action');
  r.art.__nekoIdleTransitionTo = 'foreign-action.webp'; r.monitor();
  assert.equal(r.stretchState().active, false);
  await r.advance(30000); assert.equal(r.audios.length, 0);
});

test('real physics frame interrupts a moving cat through the shared presentation monitor', async () => {
  const r = runtime('cat1', { catOnly: true }); r.request('cat1_idle_random'); await r.enter();
  assert.equal(r.visualState().active, true);
  r.state.lastTickAt = r.now(); r.body.vx = 40;
  r.context._stepNekoIdleCat1PlaygroundPhysics(r.button, r.now() + 16);
  assert.equal(r.visualState().active, false); assert.equal(r.results().length, 1);
  assert.equal(r.results()[0].reason, 'playground-unstable');
  await r.advance(30000); assert.equal(r.results().length, 1);
});

test('ordinary eat and stretch keep their existing immediate entry clocks', async () => {
  const eat = runtime('cat1', { playground: false });
  eat.request('cat1_eat_snack'); await eat.flush();
  assert.deepEqual(eat.acknowledgements.map(value => value.status), ['accepted', 'started']);
  assert.equal(eat.audios.length, 1); assert.equal(eat.art.complete, false);
  const duration = await eat.context._getNekoIdleAnimationDurationMs(eat.art.src);
  eat.audios[0].end(); await eat.advance(duration - 1); assert.equal(eat.eatState().active, true);
  await eat.advance(1); assert.equal(eat.eatState().active, false);
  const stretch = runtime('cat1', { playground: false });
  assert.equal(stretch.context._requestNekoIdleCat1HissStretchPresentation(), true); await stretch.flush();
  assert.equal(stretch.audios.length, 1, 'ordinary hiss still starts on accepted entry');
  const stretchDuration = await stretch.context._getNekoIdleAnimationDurationMs(stretch.context._getNekoIdleCat1StretchAssetUrl());
  await stretch.advance(stretchDuration + 699); assert.equal(stretch.stretchState().active, true);
  await stretch.advance(1); assert.equal(stretch.stretchState().active, false);
});

test('action hull and restored hull keep the cat supported without resetting physics or yarn', async () => {
  const r = runtime(); const originalHull = r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body);
  const yarn = r.state.bodies.get('yarn');
  assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'mouse'), true); await r.enter();
  const actionHull = r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body);
  assert.notEqual(actionHull.width, originalHull.width);
  assert.equal(Math.round(actionHull.bottom), 900); assertPhysicsOwner(r);
  await r.advance(r.duration(r.art.src));
  const restored = r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body);
  assert.equal(restored.width, originalHull.width); assert.equal(Math.round(restored.bottom), 900);
  assert.equal(r.state.bodies.get('yarn'), yarn); assert.equal(yarn.vx, 160); assertPhysicsOwner(r);
});

for (const reducedMotion of [false, true]) {
  test(`visual animated entry waits for the main image before its full cycle (reduced motion: ${reducedMotion})`, async () => {
    const r = runtime();
    r.window.matchMedia = () => ({ matches: reducedMotion });
    const started = [], terminals = [];
    assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'scared', {
      animateEntry: true,
      onPlaybackStarted: () => started.push(r.now()),
      onTerminal: result => terminals.push(result),
    }), true);
    if (!reducedMotion) {
      for (const image of r.images) if (image !== r.art && image.src) r.load(image);
      await r.advance(820);
    }
    await r.advance(2000);
    assert.equal(r.art.complete, false, 'the main art has not loaded even if its preload has');
    assert.equal(r.visualState().active, true);
    assert.equal(r.visualState().timer, 0);
    assert.equal(started.length, 0);
    assert.deepEqual(terminals, []);
    r.load(); await r.flush();
    assert.equal(started.length, 1);
    assert.equal(r.visualState().entryTimer, 0);
    assert.equal(r.visualState().sourceLoadCleanup, null);
    await r.advance(r.duration(r.art.src) - 1);
    assert.equal(r.visualState().active, true);
    await r.advance(1);
    assert.equal(r.visualState().active, false);
    assert.deepEqual(terminals, ['done']);
    assertPhysicsOwner(r);
  });

  for (const terminal of ['error', 'timeout', 'cancel', 'exit']) {
    test(`visual animated entry ${terminal} rejects late load callbacks (reduced motion: ${reducedMotion})`, async () => {
      const r = runtime();
      r.window.matchMedia = () => ({ matches: reducedMotion });
      const started = [], terminals = [], oldLoads = [];
      const addListener = r.art.addEventListener.bind(r.art);
      r.art.addEventListener = (type, callback, options) => {
        if (type === 'load') oldLoads.push(callback);
        addListener(type, callback, options);
      };
      assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'scared', {
        animateEntry: true,
        onPlaybackStarted: () => started.push(r.now()),
        onTerminal: result => terminals.push(result),
      }), true);
      if (!reducedMotion) {
        for (const image of r.images) if (image !== r.art && image.src) r.load(image);
        await r.advance(820);
      }
      if (terminal === 'error') r.fail();
      else if (terminal === 'timeout') await r.advance(5100);
      else if (terminal === 'exit') {
        r.state.released = true; r.state.releaseReason = 'test-exit'; r.monitor();
      } else r.context._cancelNekoIdleCat1VisualAction(r.button, { reason: 'return-click' });
      assert.equal(r.visualState().active, false);
      assert.equal(r.visualState().entryTimer, 0);
      assert.equal(r.visualState().sourceLoadCleanup, null);
      assert.equal(r.visualState().timer, 0);
      assert.equal(started.length, 0);
      assert.equal(terminals.length, 1);
      assert.notEqual(terminals[0], 'done');
      r.load(); for (const callback of oldLoads) callback(new Event('load'));
      await r.advance(30000);
      assert.equal(r.visualState().active, false);
      assert.equal(started.length, 0);
      assert.equal(terminals.length, 1);
      assert.equal(r.audios.length, 0);
    });
  }
}

test('ordinary reduced-motion visual entry keeps its original immediate cycle', async () => {
  const r = runtime('cat1', { playground: false });
  r.window.matchMedia = () => ({ matches: true });
  const terminals = [];
  assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'scared', {
    animateEntry: true, onTerminal: result => terminals.push(result),
  }), true);
  await r.flush();
  assert.equal(r.art.complete, false);
  assert.ok(r.visualState().timer);
  await r.advance(r.duration(r.art.src));
  assert.deepEqual(terminals, ['done']);
});

for (const [item, action] of [
  ['_NEKO_IDLE_THOUGHT_BUBBLE_FISH_COOKIE_URL', 'eat'],
  ['_NEKO_IDLE_THOUGHT_BUBBLE_TOY_MOUSE_URL', 'mouse'],
  ['_NEKO_IDLE_THOUGHT_BUBBLE_CATNIP_POUCH_URL', 'catnip1'],
]) {
  test(`existing popped bubble dispatches ${action} through the same stationary runner`, async () => {
    const r = runtime();
    const state = vm.runInContext('_nekoIdleCat1AmbientSoundState', r.context);
    state.active = true; state.catMindRunId = 'bubble-run';
    const audio = new r.window.Audio('bubble.wav'); state.audio = audio;
    r.button.__nekoIdleThoughtBubbleTier = 'cat1'; r.button.__nekoIdleThoughtBubbleAudio = audio;
    r.button.__nekoIdleThoughtBubbleItemAssetUrl = vm.runInContext(item, r.context);
    r.button.classList.add('is-thought-bubble-active');
    assert.equal(r.context._popNekoIdleThoughtBubble(r.button, { source: 'click' }), true);
    await r.advance(539); assert.equal(r.eatState().active, false); assert.equal(r.visualState().active, false);
    await r.advance(1); await r.enter();
    assert.equal(audio.paused, true); assert.equal(r.button.__nekoIdleThoughtBubblePop, null);
    if (action === 'eat') assert.equal(r.eatState().active, true);
    else assert.equal(r.visualState().actionKey, action);
  });
}

test('catnip keeps the original 50/50 follow-up boundary', () => {
  for (const [roll, expected] of [[0.499999, 'catnip1'], [0.5, 'catnip2']]) {
    const r = runtime(); r.random(roll);
    r.context._playNekoIdleCat1ThoughtBubbleFollowUp(r.button,
      vm.runInContext('_NEKO_IDLE_THOUGHT_BUBBLE_CATNIP_POUCH_URL', r.context));
    assert.equal(r.visualState().actionKey, expected); assert.equal(r.randomCalls(), 1);
  }
});

test('real local chat Easter egg enters the existing hiss stretch presentation', async () => {
  const r = runtime();
  r.window.NekoCatIdlePresentation = { requestCat1HissStretch: r.context._requestNekoIdleCat1HissStretchPresentation };
  vm.runInContext(fs.readFileSync(path.join(root,
    'static/app/app-react-chat-window/cat-local-chat-lexicon.js'), 'utf8'), r.context);
  const source = fs.readFileSync(path.join(root, 'static/app/app-react-chat-window/cat-local-chat.js'), 'utf8');
  vm.runInContext(source, r.context, { filename: 'cat-local-chat.js' });
  const expectedReply = r.window.nekoCatLocalChatManager.composeHissStretchReply(() => 0);
  assert.equal(r.window.nekoCatLocalChatManager.submit({ text: '你好', requestId: 'local-1', enteredAt: 500 }), true);
  await r.advance(320); assert.equal(r.stretchState().active, true);
  await r.enter(); assert.equal(r.audios.length, 1);
  assert.ok(r.window.nekoCatLocalChatManager.getSnapshot().items.some(value => value.text === expectedReply));
});

for (const kind of ['eat', 'visual']) test(`${kind}: accepted and started reentrancy cannot continue an old cancelled run`, async () => {
  for (const interruptAt of ['accepted', 'started']) {
    const r = runtime(); const calls = [];
    const interrupt = () => {
      r.body.grounded = false; r.monitor(); calls.push(interruptAt);
    };
    const play = kind === 'eat' ? r.context._playNekoIdleCat1EatAction : r.context._playNekoCatMindIdleRandomVisualAction;
    assert.equal(play(r.button, {
      source: 'cat_mind', requestId: `reentry-${interruptAt}`,
      onAccepted: interruptAt === 'accepted' ? interrupt : undefined,
      onStarted: interruptAt === 'started' ? interrupt : undefined,
    }), true);
    await r.enter(); await r.advance(30000);
    assert.deepEqual(calls, [interruptAt]);
    assert.equal((kind === 'eat' ? r.eatState() : r.visualState()).active, false);
    assert.equal(r.results().length, 1); assert.equal(r.results()[0].result, 'cancelled');
  }
});

test('synchronous eat result listener may start a new run without losing its result ownership', async () => {
  const r = runtime(); let nextStarted = false;
  r.window.addEventListener('neko:cat-mind:action-result', () => {
    if (nextStarted) return;
    nextStarted = true;
    assert.equal(r.context._playNekoIdleCat1EatAction(r.button, { source: 'cat_mind', requestId: 'new-run' }), true);
  });
  r.request('cat1_eat_snack', 'old-run'); await r.enter();
  r.audios[0].end(); await r.advance(r.duration(r.art.src));
  assert.equal(r.eatState().active, true); assert.equal(r.eatState().catMindRequestId, 'new-run');
  await r.enter(); r.audios.at(-1).end(); await r.advance(r.duration(r.art.src));
  assert.deepEqual(r.results().map(value => value.detail.requestId), ['old-run', 'new-run']);
  assert.ok(r.results().every(value => value.result === 'done'));
});

test('old eat audio events and rejected play promise cannot finish or stop the replacement run', async () => {
  const r = runtime(); r.controls.audioDelay = true;
  r.request('cat1_eat_snack', 'old-run'); await r.enter();
  const oldAudio = r.audios[0];
  r.context._cancelNekoIdleCat1EatAction(r.button, { reason: 'manual-cancelled' });
  r.request('cat1_eat_snack', 'new-run'); await r.enter();
  const nextAudio = r.audios[1];
  oldAudio.rejectPlay(new Error('late rejected old play')); oldAudio.end();
  oldAudio.dispatchEvent(new Event('error')); await r.flush();
  assert.equal(r.eatState().audio, nextAudio); assert.equal(r.eatState().active, true);
  assert.equal(r.eatState().catMindRequestId, 'new-run');
  nextAudio.resolvePlay(); await r.flush(); nextAudio.end(); await r.advance(r.duration(r.art.src));
  assert.deepEqual(r.results().map(value => value.detail.requestId), ['old-run', 'new-run']);
});

// Keep the real app clock, selector, request lease and lifecycle consumer in the
// same VM as the production providers and runners. Only interval delivery uses
// the harness clock; no action requests or acknowledgements are manufactured.
function attachRealCatMind(r, tier) {
  let nextInterval = 0;
  const intervals = new Map();
  r.window.setInterval = (callback, delay) => {
    const id = ++nextInterval;
    const interval = { timer: 0 };
    intervals.set(id, interval);
    const run = () => {
      if (!intervals.has(id)) return;
      callback();
      if (intervals.has(id)) interval.timer = r.window.setTimeout(run, delay);
    };
    interval.timer = r.window.setTimeout(run, delay);
    return id;
  };
  r.window.clearInterval = id => {
    const interval = intervals.get(id);
    if (interval) r.window.clearTimeout(interval.timer);
    intervals.delete(id);
  };
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-cat-mind.js'), 'utf8'),
    r.context, { filename: 'app-cat-mind.js' });
  r.window.dispatchEvent({ type: 'neko:cat-local-active-change', detail: {
    active: true, tier, source: 'manual-goodbye', timestamp: r.now(),
  } });
  return () => r.window.dispatchEvent({ type: 'neko:cat-local-active-change', detail: { active: false } });
}

for (const playground of [false, true]) {
  for (const catOnly of playground ? [false, true] : [false]) {
    for (const tier of ['cat1', 'cat2', 'cat3']) {
      test(`${tier} ${playground ? 'Playground' : 'ordinary'} ${playground ? (catOnly ? 'catOnly' : 'with yarn') : 'default presentation'}: real Cat Mind clock reaches provider, playback, result and cooldown`, async () => {
        const r = runtime(tier, { playground, catOnly });
        const detach = attachRealCatMind(r, tier);
        const actionId = `${tier}_idle_random`;
        await r.advance(0);
        assert.equal(r.journey.actionSettled, !playground, 'retain the actual ordinary journey residue');
        await r.advance(30000);
        const requests = () => r.events.filter(event => event.type === 'neko:cat-mind:action-request');
        assert.equal(requests().length, 1);
        assert.equal(requests()[0].detail.actionId, actionId);
        let state = r.window.nekoCatMind.getState();
        if (playground) {
          assert.equal(state.pendingActionRequest.actionId, actionId);
          assert.ok(state.pendingActionRequest.runId, 'the real renderer acknowledged accepted');
          assert.equal(state.activeAction, null);
          assert.equal(state.actionCooldowns[actionId], undefined, 'accepted never starts cooldown');
        } else {
          assert.equal(state.pendingActionRequest, null);
          assert.equal(state.activeAction.actionId, actionId, 'preserve ordinary acknowledgement timing');
          assert.equal(state.actionCooldowns[actionId].startedAt, r.now());
        }
        const requestedAt = r.now();
        await r.advance(1500);
        if (playground) {
          state = r.window.nekoCatMind.getState();
          assert.equal(state.activeAction, null);
          assert.equal(state.actionCooldowns[actionId], undefined);
          assert.equal(r.results().length, 0);
        }
        await r.enter();
        state = r.window.nekoCatMind.getState();
        assert.equal(state.pendingActionRequest, null);
        assert.equal(state.activeAction.actionId, actionId);
        const expectedStart = playground ? r.now() : requestedAt;
        assert.equal(state.activeAction.startedAt, expectedStart);
        assert.equal(state.actionCooldowns[actionId].startedAt, expectedStart);
        assert.equal(state.actionCooldowns[actionId].fullCooldownMs, tier === 'cat2' ? 60000 : 300000);
        const duration = r.duration(r.art.src);
        assert.ok(duration > 0);
        if (playground) {
          await r.advance(duration - 1);
          assert.equal(r.window.nekoCatMind.getState().activeAction.actionId, actionId);
          assert.equal(r.results().length, 0);
          await r.advance(1);
        } else {
          await r.advance(duration);
        }
        state = r.window.nekoCatMind.getState();
        assert.equal(state.pendingActionRequest, null);
        assert.equal(state.activeAction, null, 'real app consumes the renderer terminal result');
        assert.equal(r.results().length, 1);
        assert.equal(r.results()[0].result, 'done');
        assert.equal(r.results()[0].detail.requestId, requests()[0].detail.requestId);
        assert.equal(state.actionCooldowns[actionId].startedAt, expectedStart);
        if (playground) assertPhysicsOwner(r);
        detach();
      });
    }
  }
}

for (const tier of ['cat1', 'cat2', 'cat3']) {
  for (const failure of ['image error', 'entry timeout']) {
    test(`${tier}: real Cat Mind releases accepted ${failure} without started, cooldown or completion feedback`, async () => {
      const r = runtime(tier, { catOnly: true });
      const detach = attachRealCatMind(r, tier);
      await r.advance(0); await r.advance(30000);
      const before = r.window.nekoCatMind.getState();
      assert.ok(before.pendingActionRequest.runId);
      if (failure === 'image error') r.fail();
      else await r.advance(5000);
      await r.advance(0);
      const after = r.window.nekoCatMind.getState();
      assert.equal(after.pendingActionRequest, null);
      assert.equal(after.activeAction, null);
      assert.equal(after.actionCooldowns[`${tier}_idle_random`], undefined);
      assert.deepEqual(after.fields, before.fields, 'a never-started action has no completion feedback');
      assert.equal(r.results().length, 1);
      assert.equal(r.results()[0].result, 'cancelled');
      assert.equal(r.visualState().active, false);
      r.load(); await r.advance(1000);
      assert.equal(r.results().length, 1);
      assert.equal(r.window.nekoCatMind.getState().activeAction, null);
      detach();
    });
  }
}


for (const owner of ['eat', 'stretch', 'visual']) {
  test(`${owner}: release callbacks cannot start stationary actions before teardown completes`, async () => {
    const r = runtime('cat1', { catOnly: true });
    // The harness has no collision DOM; keep the real release lifecycle intact.
    r.window.NekoCollisionDeformation.clearCollisionDeformation = () => false;
    const attempts = [], terminals = [];
    const tryEntrances = () => {
      terminals.push(owner);
      assert.equal(r.state.active, true);
      assert.equal(r.state.released, true, 'callback runs within release, before ownership is relinquished');
      const options = { source: 'cat_mind', requestId: 'must-not-reenter-release' };
      attempts.push(
        r.context._playNekoIdleCat1EatAction(r.button, options),
        r.context._playNekoIdleCat1StretchAction(r.button),
        r.context._playNekoIdleCat1VisualAction(r.button, 'banana'),
        r.window.NekoCatIdlePresentation.requestCat1HissStretch(),
        r.window.NekoCatIdlePresentation.requestCat1Banana(),
        r.context._playNekoCatMindIdleRandomVisualAction(r.button, options),
        r.context._playNekoCatMindInactiveSadAction(r.button, options),
      );
    };
    if (owner === 'eat') {
      r.window.addEventListener('neko:cat-mind:action-result', event => {
        if (event.detail.detail.requestId === 'release-owner') tryEntrances();
      });
      assert.equal(r.context._playNekoIdleCat1EatAction(r.button,
        { source: 'cat_mind', requestId: 'release-owner' }), true);
    } else if (owner === 'stretch') {
      assert.equal(r.context._playNekoIdleCat1StretchAction(r.button, {
        onTerminal: tryEntrances,
        onPlaybackStarted: () => r.context._playNekoIdleSound(r.stretchState(),
          r.context._getNekoCatVoiceUrl('cat1.hiss'), 0.12),
      }), true);
    } else {
      assert.equal(r.context._playNekoIdleCat1VisualAction(r.button, 'oiia', {
        onTerminal: tryEntrances,
      }), true);
    }
    await r.enter();
    if (owner === 'visual') await r.advance(510);
    const oldAudio = r.audios.find(audio => !audio.paused);
    assert.ok(oldAudio, 'release an already-playing owner');
    assert.equal(r.context._setNekoIdleCat1GravityCatEnabled(false), false);
    assert.deepEqual(terminals, [owner]);
    assert.deepEqual(attempts, Array(7).fill(false));
    assert.equal(r.state.active, false); assert.equal(r.state.released, true);
    assert.equal(oldAudio.paused, true);
    oldAudio.end(); oldAudio.dispatchEvent(new Event('error')); r.load();
    await r.advance(30000);
    for (const state of [r.eatState(), r.stretchState(), r.visualState()]) {
      assert.equal(state.active, false);
      assert.equal(state.timer, 0); assert.equal(state.entryTimer, 0);
      assert.equal(state.sourceLoadCleanup, null); assert.equal(state.audio, null);
      assert.equal(state.audioTimer || state.audioStartTimer || 0, 0);
    }
    assert.deepEqual(terminals, [owner]);
    assert.equal(r.audios.length, 1, 'rejected callback entrances never construct replacement audio');

    // Positive controls: completed OFF must release the gate for ordinary APIs.
    const ordinaryTerminals = [];
    assert.equal(r.window.NekoCatIdlePresentation.requestCat1Banana({
      onTerminal: result => ordinaryTerminals.push(result),
    }), true);
    await r.enter(); await r.advance(r.duration(r.art.src));
    assert.deepEqual(ordinaryTerminals, ['done']);
    assert.equal(r.visualState().active, false);
    assert.equal(r.window.NekoCatIdlePresentation.requestCat1HissStretch(), true);
    await r.flush();
    const hiss = r.stretchState().audio;
    assert.ok(hiss && !hiss.paused, 'ordinary public hiss still starts immediately');
    const duration = await r.context._getNekoIdleAnimationDurationMs(r.context._getNekoIdleCat1StretchAssetUrl());
    await r.advance(duration + 700);
    assert.equal(r.stretchState().active, false); assert.equal(hiss.paused, true);
    assert.equal(r.state.active, false);
  });
}
