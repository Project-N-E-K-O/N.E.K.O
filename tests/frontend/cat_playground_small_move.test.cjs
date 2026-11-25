const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.NEKO_TEST_SOURCE_ROOT || path.resolve(__dirname, '../..');
const avatar = path.join(root, 'static/avatar/avatar-ui-buttons');
function classList() {
  const names = new Set();
  return { add: (...values) => values.forEach(value => names.add(value)),
    remove: (...values) => values.forEach(value => names.delete(value)),
    contains: value => names.has(value),
    toggle(value, active) { if (active) names.add(value); else names.delete(value); } };
}

// The real position route, Playground physics, window gravity, providers and
// movement runner share one VM. Only DOM layout and event/time delivery are
// controlled; no movement planner, owner, admission or runner is replaced.
function runtime(tier = 'cat1', { playground = true, catOnly = false } = {}) {
  let now = 1000, sequence = 0, randomValue = 0, randomCalls = 0;
  const timers = new Map(), frames = new Map(), events = [], audios = [], images = [], writes = [];
  const windowListeners = new Map();
  const controls = { collisionActive: false, audioEnabled: true, audioDelay: false, expandedChat: !playground };
  const sensingSubscribers = new Set();
  let sensingFact = null, sensingRevision = 0;
  function element(tag = 'div') {
    const attrs = new Map(), listeners = new Map(), children = [];
    const result = {
      tagName: tag.toUpperCase(), isConnected: true, classList: classList(),
      style: { setProperty(name, value) { this[name] = String(value); },
        removeProperty(name) { delete this[name]; },
        getPropertyValue(name) { return this[name] || ''; } }, dataset: {},
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
    removeEventListener(type, callback) {
      windowListeners.set(type, (windowListeners.get(type) || []).filter(value => value !== callback));
    },
    dispatchEvent(event) { events.push(event); for (const callback of windowListeners.get(event.type) || []) callback(event); return true; },
    requestAnimationFrame(callback) { frames.set(++sequence, callback); return sequence; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(callback, delay = 0) { timers.set(++sequence, { callback, at: now + Math.max(0, delay) }); return sequence; },
    clearTimeout(id) { timers.delete(id); }, matchMedia: () => ({ matches: false }),
    getComputedStyle: value => ({ display: value.style.display || 'block',
      visibility: value.style.visibility || 'visible', opacity: value.style.opacity ?? '1' }),
    NekoCollisionDeformation: { isCollisionDeformationActive: () => controls.collisionActive,
      beginCollisionDeformation: () => false, renderCollisionDeformation: () => false, clearCollisionDeformation: () => false },
    nekoDesktopWindowSensingContext: { getCurrent: () => sensingFact,
      subscribe(callback) { sensingSubscribers.add(callback); return () => sensingSubscribers.delete(callback); } },
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
  const expandedChat = element();
  const document = { documentElement: element(), hidden: false, visibilityState: 'visible', readyState: 'complete', body: element(),
    getElementById: id => id === 'react-chat-window-shell' && controls.expandedChat ? expandedChat : null,
    querySelector: () => null,
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
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt() {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton() {},
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
  const yarnElement = element(); yarnElement.style.left = '900px'; yarnElement.style.top = '200px';
  const yarn = context._createNekoIdleCat1PlaygroundPhysicsBody('yarn', yarnElement, { vx: 160, vy: 30 });
  Object.assign(state, { active: playground, released: !playground, container, button,
    phase: 'settled', frame: 0, catOnly, yarnEnabled: !catOnly,
    bodies: new Map([['cat', body], ...(!catOnly ? [['yarn', yarn]] : [])]) });
  vm.runInContext(fs.readFileSync(path.join(avatar, 'idle-desktop-window-gravity.js'), 'utf8'), context, { filename: 'idle-desktop-window-gravity.js' });
  window.dispatchEvent({ type: 'neko:idle-cat1-playground-state', detail: { active: playground } });
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
    controls, events, audios, images, timers, frames, writes, acknowledgements, demotions, expandedChat,
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
    async frame(ms = 16) {
      await api.advance(ms);
      const pending = [...frames.values()]; frames.clear();
      for (const callback of pending) callback(now);
      await api.flush(); await api.advance(0);
    },
    async runFrames(ms) {
      const until = now + ms;
      while (now < until) await api.frame(Math.min(16, until - now));
    },
    sample(rect = null, extra = {}) {
      sensingFact = rect ? { status: 'current', sessionId: 'small-move-window', revision: ++sensingRevision,
        timestamp: now, rect, changes: [], windows: [{ key: 'window-1', rect }], ...extra } : null;
      for (const callback of sensingSubscribers) callback(sensingFact);
      return sensingFact;
    },
    admitWindow(rect = { x: 100, y: 100, width: 900, height: 650 }) {
      api.sample(rect);
      body.x = 300; body.y = rect.y + rect.height - body.height + body.height * body.visibleInsetRatios.bottom;
      body.vx = 0; body.vy = 0; body.grounded = true;
      context._setNekoIdleCat1PlaygroundBodyPosition(body, body.x, body.y);
      return window.NekoDesktopWindowGravity.requestAdmission(button, body);
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

function ownerSnapshot(r) {
  return { x: r.body.x, y: r.body.y, vx: r.body.vx, vy: r.body.vy,
    grounded: r.body.grounded, left: r.container.style.left, top: r.container.style.top };
}
function assertPositionSynced(r) {
  assert.ok(Math.abs(Number.parseFloat(r.container.style.left) - r.body.x) <= .5);
  assert.ok(Math.abs(Number.parseFloat(r.container.style.top) - r.body.y) <= .5);
  if (r.window.NekoDesktopWindowGravity.isActive(r.button)) {
    const owner = r.window.NekoDesktopWindowGravity.getState();
    assert.equal(owner.x, r.body.x); assert.equal(owner.y, r.body.y);
  }
}
function requestMove(r, id = 'move-1', options = {}) {
  return r.context._startNekoIdleCat1PairMove(r.button, {
    source: 'cat_mind', requestId: id, ...options,
  });
}
function moveResults(r) { return r.results().filter(value => value.actionId === 'cat1_small_move'); }
function attachRealMind(r) {
  let sequence = 0; const intervals = new Map();
  r.window.setInterval = (callback, delay) => {
    const id = ++sequence, interval = { timer: 0 }; intervals.set(id, interval);
    const run = () => { if (!intervals.has(id)) return; callback();
      if (intervals.has(id)) interval.timer = r.window.setTimeout(run, delay); };
    interval.timer = r.window.setTimeout(run, delay); return id;
  };
  r.window.clearInterval = id => { const value = intervals.get(id);
    if (value) r.window.clearTimeout(value.timer); intervals.delete(id); };
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-cat-mind.js'), 'utf8'),
    r.context, { filename: 'app-cat-mind.js' });
  r.window.dispatchEvent({ type: 'neko:cat-local-active-change', detail: {
    active: true, source: 'manual-goodbye', tier: 'cat1', timestamp: r.now(),
  } });
}

if (require.main !== module) {
  module.exports = { runtime, requestMove, moveResults, attachRealMind, ownerSnapshot, assertPositionSynced };
} else {

for (const windowOwned of [false, true]) {
  test(`${windowOwned ? 'window' : 'free'}: owner position route commits body and DOM once without recursion or a later jump`, async () => {
    const r = runtime('cat1', { catOnly: true });
    if (windowOwned) assert.equal(r.admitWindow(), true);
    const initial = ownerSnapshot(r);
    assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, initial.x + 60, initial.y), true);
    assert.equal(r.body.x, initial.x + 60); assert.equal(r.body.y, initial.y);
    assertPositionSynced(r);
    await r.frame(); assert.equal(r.body.x, initial.x + 60); assertPositionSynced(r);
    assert.equal(r.body.vx, 0); assert.equal(r.body.vy, 0); assert.equal(r.body.grounded, true);
  });
  test(`${windowOwned ? 'window' : 'free'}: target preview is pure, horizontal and uses current bounds`, () => {
    const r = runtime('cat1', { catOnly: true });
    if (windowOwned) assert.equal(r.admitWindow(), true);
    const before = ownerSnapshot(r), draws = r.randomCalls(), writes = r.writes.length;
    const frames = [...r.frames.keys()];
    const target = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button,
      { left: before.x + 70, top: before.y - 400 });
    assert.ok(target); assert.equal(target.left, before.x + 70); assert.equal(target.top, before.y);
    assert.equal(target.distance, 70);
    const far = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button, { left: 100000, top: before.y });
    assert.ok(far.left <= r.body.wallRight + .5);
    assert.deepEqual(ownerSnapshot(r), before);
    assert.equal(r.randomCalls(), draws); assert.equal(r.writes.length, writes);
    assert.deepEqual([...r.frames.keys()], frames);
  });
  test(`${windowOwned ? 'window' : 'free'}: repeated providers keep ordinary journey residue, hover and random untouched`, () => {
    const r = runtime('cat1', { catOnly: true });
    if (windowOwned) assert.equal(r.admitWindow(), true);
    const before = ownerSnapshot(r), writes = r.writes.length, draws = r.randomCalls();
    const frames = [...r.frames.keys()];
    for (let index = 0; index < 8; index++) assert.equal(r.decision('cat1_small_move').allowed, true);
    assert.equal(r.journey.actionSettled, false);
    assert.deepEqual(ownerSnapshot(r), before); assert.equal(r.writes.length, writes);
    assert.equal(r.randomCalls(), draws); assert.deepEqual([...r.frames.keys()], frames);
    r.context._playNekoIdleHoverArt(r.art, 'cat1');
    const hover = { src: r.art.src, token: r.art.__nekoIdleHoverToken,
      startedAt: r.art.__nekoIdleHoverStartedAt, timer: r.art.__nekoIdleHoverTimer };
    for (let index = 0; index < 5; index++) r.decision('cat1_small_move');
    assert.deepEqual({ src: r.art.src, token: r.art.__nekoIdleHoverToken,
      startedAt: r.art.__nekoIdleHoverStartedAt, timer: r.art.__nekoIdleHoverTimer }, hover);
  });
}

test('ordinary position fallback remains available only when no Playground owner exists', () => {
  const r = runtime('cat1', { playground: false }); const before = ownerSnapshot(r);
  assert.equal(r.context._applyNekoIdleCat1PlaygroundMovePosition(r.container, 220, 310), null);
  assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, 220, 310), true);
  assert.equal(r.container.style.left, '220px'); assert.equal(r.container.style.top, '310px');
  assert.equal(r.body.x, before.x); assert.equal(r.body.y, before.y);
});

test('a free cat on a native platform stays on that support and rejects after the platform disappears', () => {
  const r = runtime('cat1', { catOnly: true });
  const platform = { x: 200, y: 400, width: 300, height: 40 }; r.sample(platform);
  r.body.x = 300; r.body.y = platform.y - r.body.height * (1 - r.body.visibleInsetRatios.bottom);
  r.body.grounded = true; r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, r.body.x, r.body.y);
  const before = ownerSnapshot(r), owner = r.window.NekoDesktopWindowGravity.getState();
  const target = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button, { left: 100000, top: 0 });
  assert.ok(target); assert.equal(target.top, before.y); assert.ok(target.left > before.x);
  assert.deepEqual(ownerSnapshot(r), before); assert.deepEqual(r.window.NekoDesktopWindowGravity.getState(), owner);
  assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, target.left, target.top), true);
  assertPositionSynced(r); assert.equal(r.window.NekoDesktopWindowGravity.isActive(r.button), false);
  r.sample(platform, { windows: [] });
  assert.equal(r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button, { left: r.body.x + 50, top: r.body.y }), null);
  const lost = ownerSnapshot(r);
  assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, r.body.x + 50, r.body.y), false);
  assert.deepEqual(ownerSnapshot(r), lost);
});

test('a large free-floor move cannot tunnel across a native window border', () => {
  const r = runtime('cat1', { catOnly: true });
  const wall = { x: 500, y: 700, width: 100, height: 200 }; r.sample(wall);
  r.body.x = 300; r.body.y = r.body.floorY; r.body.grounded = true;
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, r.body.x, r.body.y);
  const before = ownerSnapshot(r);
  const target = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button, { left: 1000, top: r.body.y });
  assert.ok(target); assert.ok(target.left > before.x); assert.ok(target.left < 1000);
  assert.deepEqual(ownerSnapshot(r), before);
  assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, target.left, target.top), true);
  assert.ok(r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body).right <= wall.x + .5);
  assertPositionSynced(r);
});

test('moving toward another Playground body stops at contact without moving the other object', () => {
  const r = runtime('cat1', { catOnly: true });
  const other = { id: 'yarn', x: 250, y: r.body.y, width: 50, height: 96,
    visibleInsetRatios: { left: 0, right: 0, top: 0, bottom: 0 } };
  r.state.bodies.set('yarn', other);
  const target = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button, { left: 800, top: r.body.y });
  assert.ok(target); assert.ok(target.left < 800);
  assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, target.left, target.top), true);
  assert.ok(r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body).right <= other.x + .5);
  assert.equal(other.x, 250); assert.equal(other.y, r.body.y); assert.equal(r.state.bodies.get('yarn'), other);
  assertPositionSynced(r);
});

for (const [label, change] of [
  ['airborne', r => { r.body.grounded = false; }],
  ['sliding', r => { r.body.vx = 12; }],
  ['collision deformation', r => { r.controls.collisionActive = true; }],
  ['pending cat press', r => { r.state.pointerBodyId = 'cat'; r.body.dragging = true; }],
  ['active drag', r => { r.state.draggingBodyId = 'cat'; r.body.dragging = true; }],
  ['released owner still cleaning', r => { r.state.released = true; }],
  ['entry pending', r => { r.button.__nekoIdleCat1PlaygroundPendingEntry = {}; }],
  ['return pending', r => { r.container.setAttribute('data-neko-return-click-suppressed', 'true'); }],
]) {
  test(`existing owner rejects ${label} without falling through to direct DOM position`, () => {
    const r = runtime('cat1', { catOnly: true }); change(r);
    const before = ownerSnapshot(r);
    assert.equal(r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button,
      { left: r.body.x + 80, top: r.body.y }), null);
    assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, r.body.x + 80, r.body.y), false);
    assert.deepEqual(ownerSnapshot(r), before);
    assert.equal(r.decision('cat1_small_move').allowed, false);
    assert.equal(requestMove(r), false); assert.equal(moveResults(r).length, 0);
  });
}

for (const windowOwned of [false, true]) for (const catOnly of [false, true]) {
  test(`${windowOwned ? 'window' : 'free'} ${catOnly ? 'catOnly' : 'yarn moving'}: one solo plan follows real body path and reports registered activity once`, async () => {
    const r = runtime('cat1', { catOnly });
    if (windowOwned) assert.equal(r.admitWindow(), true);
    assert.equal(r.controls.expandedChat, false, 'Playground solo does not require an ordinary chat surface');
    const bodies = [...r.state.bodies.values()], state = r.state;
    assert.equal(requestMove(r), true);
    const plan = r.journey.pairMovePlan;
    assert.ok(plan); assert.equal(plan.chatMode, 'solo');
    assert.equal(plan.playgroundState, state); assert.equal(plan.playgroundToken, state.token);
    assert.equal(plan.dy, 0); assert.ok(Math.abs(plan.dx) >= 1);
    await r.enter();
    const start = ownerSnapshot(r), duration = plan.durationMs;
    let pathDistance = 0, previous = { x: r.body.x, y: r.body.y };
    while (r.journey.pairMovePlan) {
      await r.frame(16); assertPositionSynced(r);
      pathDistance += Math.abs(r.body.x - previous.x);
      previous = { x: r.body.x, y: r.body.y };
      assert.ok(r.now() - 1000 <= duration + 2000, 'the current plan must terminate');
    }
    const results = moveResults(r); assert.equal(results.length, 1); assert.equal(results[0].result, 'done');
    assert.ok(pathDistance > 0); assert.ok(Math.abs(results[0].detail.pathDistancePx - pathDistance) <= 1);
    assert.ok(Math.abs(results[0].detail.distancePx - Math.abs(plan.dx)) <= 1);
    assert.equal(r.state, state); assert.equal(r.state.active, true); assert.equal(r.state.released, false);
    assert.deepEqual([...r.state.bodies.values()], bodies, 'movement never manufactures or replaces bodies');
    assert.equal(r.events.filter(event => event.type === 'neko:idle-chat-pair-move-bounds').length, 0);
    const final = ownerSnapshot(r); await r.frame(); assert.equal(r.body.x, final.x);
    assert.equal(moveResults(r).length, 1);
  });
}

for (const playground of [false, true]) {
  test(`${playground ? 'Playground' : 'ordinary'}: real Cat Mind clock selects small move, starts, consumes actual path and keeps original cooldown`, async () => {
    const r = runtime('cat1', { playground, catOnly: true }); attachRealMind(r); r.random(.99);
    await r.advance(0);
    for (let index = 0; index < 20 && !r.journey.pairMovePlan; index++) await r.advance(30000);
    const requests = r.events.filter(event => event.type === 'neko:cat-mind:action-request');
    assert.ok(requests.length); assert.equal(requests[0].detail.actionId, 'cat1_small_move');
    const plan = r.journey.pairMovePlan; assert.ok(plan);
    if (playground) {
      const accepted = r.window.nekoCatMind.getState();
      assert.equal(accepted.pendingActionRequest.actionId, 'cat1_small_move');
      assert.equal(accepted.activeAction, null); assert.equal(accepted.actionCooldowns.cat1_small_move, undefined);
      await r.enter();
    }
    const mind = r.window.nekoCatMind.getState();
    assert.equal(mind.pendingActionRequest, null);
    assert.equal(mind.activeAction.actionId, 'cat1_small_move');
    assert.equal(mind.actionCooldowns.cat1_small_move.fullCooldownMs, 8 * 60 * 1000);
    const energyBefore = mind.fields.energy;
    await r.runFrames(plan.durationMs + 32);
    const finished = r.window.nekoCatMind.getState();
    assert.equal(finished.activeAction, null); assert.equal(finished.pendingActionRequest, null);
    assert.equal(moveResults(r).length, 1); assert.equal(moveResults(r)[0].result, 'done');
    assert.ok(moveResults(r)[0].detail.pathDistancePx > 0);
    assert.ok(finished.fields.energy < energyBefore, 'existing physical effort is consumed by the real app');
    assert.ok(finished.actionCooldowns.cat1_small_move);
  });
}

for (const [label, interrupt] of [
  ['active drag', r => { r.state.pointerBodyId = 'cat'; r.state.draggingBodyId = 'cat'; r.body.dragging = true; }],
  ['support loss', r => { r.body.grounded = false; r.body.y -= 20; }],
  ['motion impulse', r => { r.body.vx = 80; }],
  ['tier change', r => { r.button.setAttribute('data-neko-idle-tier', 'cat2'); }],
]) {
  test(`${label} terminates the old plan with only applied path and no late-frame completion`, async () => {
    const r = runtime('cat1', { catOnly: true }); assert.equal(requestMove(r), true);
    const plan = r.journey.pairMovePlan;
    await r.enter();
    await r.runFrames(plan.durationMs / 3); const partial = Math.abs(r.body.x - plan.catStartLeft);
    assert.ok(partial > 0); assert.ok(partial < Math.abs(plan.dx));
    interrupt(r); await r.frame(); await r.advance(0);
    assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
    assert.notEqual(moveResults(r)[0].result, 'done');
    assert.ok(moveResults(r)[0].detail.pathDistancePx <= partial + 1);
    const results = moveResults(r).length; await r.runFrames(plan.durationMs + 1000);
    assert.equal(moveResults(r).length, results);
  });
}

test('pending press freezes actual displacement, then release resumes the same plan and one terminal', async () => {
  const r = runtime('cat1', { catOnly: true }); assert.equal(requestMove(r), true);
  const plan = r.journey.pairMovePlan; await r.enter(); await r.runFrames(plan.durationMs / 4);
  r.state.pointerBodyId = 'cat'; r.body.dragging = true;
  const held = ownerSnapshot(r); await r.runFrames(1200);
  assert.equal(r.journey.pairMovePlan, plan); assert.equal(r.body.x, held.x); assert.equal(r.body.y, held.y);
  assert.equal(moveResults(r).length, 0);
  r.state.pointerBodyId = ''; r.body.dragging = false; await r.runFrames(plan.durationMs + 100);
  assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
  assert.equal(moveResults(r)[0].result, 'done');
});

test('window disappearance interrupts its plan without direct DOM fallback into the lost support', async () => {
  const r = runtime('cat1', { catOnly: true }); assert.equal(r.admitWindow(), true);
  assert.equal(requestMove(r), true); const plan = r.journey.pairMovePlan;
  await r.enter(); await r.runFrames(plan.durationMs / 3);
  r.sample(null); await r.frame(); await r.advance(0);
  assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
  assert.notEqual(moveResults(r)[0].result, 'done'); assertPositionSynced(r);
});

test('completed result callback may start a new plan without old reporter clearing its run', async () => {
  const r = runtime('cat1', { catOnly: true }); let secondPlan = null;
  r.window.addEventListener('neko:cat-mind:action-result', event => {
    if (event.detail.actionId !== 'cat1_small_move' || secondPlan) return;
    assert.equal(requestMove(r, 'second'), true); secondPlan = r.journey.pairMovePlan;
  });
  assert.equal(requestMove(r, 'first'), true); const first = r.journey.pairMovePlan;
  await r.enter(); await r.runFrames(first.durationMs + 16);
  assert.ok(secondPlan); assert.equal(r.journey.pairMovePlan, secondPlan);
  assert.equal(r.journey.catMindRequestId, 'second');
  await r.enter();
  await r.runFrames(secondPlan.durationMs + 100);
  assert.deepEqual(moveResults(r).map(value => value.detail.requestId), ['first', 'second']);
});

test('final release clears old movement frames before the deferred terminal can start ordinary presentation', async () => {
  const r = runtime('cat1', { catOnly: true }); let accepted = null, callbackOwnerActive = null;
  r.window.addEventListener('neko:cat-mind:action-result', event => {
    if (event.detail.actionId === 'cat1_small_move') {
      callbackOwnerActive = r.state.active;
      accepted = r.window.NekoCatIdlePresentation.requestCat1Happy();
    }
  });
  assert.equal(requestMove(r), true); const plan = r.journey.pairMovePlan; await r.enter(); await r.frame();
  r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'page-destroy');
  await r.advance(0); assert.equal(callbackOwnerActive, false); assert.equal(accepted, true);
  assert.equal(r.journey.pairMovePlan, null);
  assert.equal(r.visualState().active, true); assert.equal(r.stretchState().active, false);
  const source = r.art.src, position = { left: r.container.style.left, top: r.container.style.top };
  await r.runFrames(plan.durationMs + 100); assert.equal(moveResults(r).length, 1);
  assert.equal(r.state.bodies.size, 0); assert.equal(r.state.active, false);
  assert.equal(r.art.src, source); assert.deepEqual({ left: r.container.style.left, top: r.container.style.top }, position);
});

test('Playground selects solo even when a minimized chat exists, without moving that surface', async () => {
  const r = runtime('cat1', { catOnly: false });
  r.controls.expandedChat = true; r.expandedChat.classList.add('is-minimized');
  assert.ok(r.context._getNekoIdleCat1PairMoveChatTarget());
  const chatPosition = { ...r.expandedChat.style };
  assert.equal(requestMove(r), true); const plan = r.journey.pairMovePlan;
  assert.equal(plan.chatMode, 'solo'); assert.equal(plan.shell, null);
  await r.enter(); await r.runFrames(plan.durationMs + 32);
  assert.deepEqual(r.expandedChat.style, chatPosition);
  assert.equal(r.events.filter(event => event.type === 'neko:idle-chat-pair-move-bounds').length, 0);
});

test('a provider allowance does not survive losing support before the runner starts', () => {
  const r = runtime('cat1', { catOnly: true });
  assert.equal(r.decision('cat1_small_move').allowed, true);
  r.body.grounded = false;
  const draws = r.randomCalls(), writes = r.writes.length;
  assert.equal(requestMove(r), false); assert.equal(r.randomCalls(), draws); assert.equal(r.writes.length, writes);
  assert.equal(moveResults(r).length, 0); assert.equal(r.journey.pairMovePlan, null);
});

test('walking image error never reports started or invents physical effort', async () => {
  const r = runtime('cat1', { catOnly: true }), callbacks = [];
  assert.equal(requestMove(r, 'failed-image', {
    onAccepted: () => callbacks.push('accepted'), onStarted: () => callbacks.push('started'),
  }), true);
  assert.deepEqual(callbacks, ['accepted']); r.fail(); await r.flush();
  assert.equal(r.journey.pairMovePlan, null); assert.deepEqual(callbacks, ['accepted']);
  assert.equal(moveResults(r).length, 1); assert.equal(moveResults(r)[0].detail.pathDistancePx, 0);
  const position = ownerSnapshot(r); r.load(); await r.runFrames(1000);
  assert.deepEqual(ownerSnapshot(r), position); assert.equal(moveResults(r).length, 1);
});

test('an already queued old movement RAF cannot advance or finish the replacement plan', async () => {
  const r = runtime('cat1', { catOnly: true }); assert.equal(requestMove(r, 'old'), true); await r.enter();
  await r.frame();
  const lateFrame = r.frames.get(r.journey.pairMoveFrame); assert.equal(typeof lateFrame, 'function');
  r.context._cancelNekoIdleCat1PairMove(r.journey, { reason: 'manual-cancelled' }); await r.flush();
  assert.equal(requestMove(r, 'replacement'), true); await r.enter();
  const plan = r.journey.pairMovePlan, before = ownerSnapshot(r), results = moveResults(r).length;
  lateFrame(r.now() + plan.durationMs + 1000); await r.flush();
  assert.equal(r.journey.pairMovePlan, plan); assert.deepEqual(ownerSnapshot(r), before);
  assert.equal(moveResults(r).length, results);
  await r.runFrames(plan.durationMs + 32);
  assert.deepEqual(moveResults(r).map(value => value.detail.requestId), ['old', 'replacement']);
});

test('an obsolete session callback cancels its remaining plan without new displacement or duplicate release result', async () => {
  const r = runtime('cat1', { catOnly: true }); assert.equal(requestMove(r), true); await r.enter(); await r.frame();
  const oldFrame = r.frames.get(r.journey.pairMoveFrame), before = ownerSnapshot(r);
  const foot = r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body).bottom;
  r.state.token++;
  oldFrame(r.now() + 5000); await r.flush();
  assert.equal(r.body.x, before.x);
  assert.equal(r.context._getNekoIdleCat1PlaygroundBodyCollisionRect(r.body).bottom, foot,
    'restoring the default hull may change CSS top while preserving its physical support');
  assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
  r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'page-destroy'); await r.flush();
  assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
  assert.notEqual(moveResults(r)[0].result, 'done');
});

for (const at of ['accepted', 'started']) {
  test(`synchronous ${at} exit cannot continue the old image or movement callback`, async () => {
    const r = runtime('cat1', { catOnly: true }), notifications = [];
    const exit = () => r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'page-destroy');
    assert.equal(requestMove(r, `exit-${at}`, {
      onAccepted() { notifications.push('accepted'); if (at === 'accepted') exit(); },
      onStarted() { notifications.push('started'); if (at === 'started') exit(); },
    }), true);
    await r.enter(); await r.runFrames(5000);
    assert.equal(r.state.active, false); assert.equal(r.state.bodies.size, 0);
    assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
    assert.notEqual(moveResults(r)[0].result, 'done');
    assert.deepEqual(notifications, at === 'accepted' ? ['accepted'] : ['accepted', 'started']);
    assert.equal(r.art.__nekoIdleTransitionTo || '', '');
  });
}

test('movement keeps tier restrictions, missing-ball actions and deferred surfaces guarded', () => {
  for (const tier of ['cat2', 'cat3']) {
    const r = runtime(tier, { catOnly: true });
    assert.equal(r.decision('cat1_small_move').allowed, false); assert.equal(requestMove(r), false);
  }
  const r = runtime('cat1', { catOnly: true });
  for (const capability of ['compact-follow', 'dock-return-goodbye', 'edge-peek']) {
    assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, capability), true, capability);
  }
  assert.equal(r.decision('cat1_play_yarn').allowed, false);
  assert.equal(r.context._playNekoIdleCat1PlayAction(r.button), false);
  assert.equal(r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button), null);
  assert.equal(r.context._getNekoIdleCat1PlaygroundPairMoveTarget(r.button, {left: 250, top: r.body.y}), null);
  assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button), true);
});

for (const [label, gapMs, windowRect] of [
  ['a 2px floor rise', 0, { x: 100, y: 100, width: 900, height: 648 }],
  ['a lateral clamp after a native sample gap', 1000, { x: 400, y: 100, width: 600, height: 650 }],
]) {
  test(`${label} cancels solo movement before passive displacement enters its activity`, async () => {
    const r = runtime('cat1', { catOnly: true });
    assert.equal(r.admitWindow(), true);
    assert.equal(requestMove(r), true);
    await r.enter(); await r.runFrames(400);
    const plan = r.journey.pairMovePlan;
    const before = {
      x: r.body.x, y: r.body.y, path: plan.activityPathDistancePx,
      dx: plan.activityLastOffsetX, dy: plan.activityLastOffsetY,
    };
    assert.ok(before.path > 0);
    if (gapMs) await r.advance(gapMs);
    r.sample(windowRect);
    assert.ok(Math.hypot(r.body.x - before.x, r.body.y - before.y) > .5);
    assert.equal(r.body.grounded, true);
    assert.equal(r.body.vx, 0); assert.equal(r.body.vy, 0);
    let autonomousWrites = 0;
    const commit = r.context._setNekoIdleCat1ContainerPosition;
    r.context._setNekoIdleCat1ContainerPosition = (...args) => {
      autonomousWrites++;
      return commit(...args);
    };
    await r.frame();
    assert.equal(r.journey.pairMovePlan, null);
    assert.equal(autonomousWrites, 0, 'the old absolute trajectory must not be followed after an owner correction');
    const results = moveResults(r);
    assert.equal(results.length, 1); assert.notEqual(results[0].result, 'done');
    assert.equal(results[0].detail.pathDistancePx, before.path);
    assert.equal(results[0].detail.distancePx, Math.hypot(before.dx, before.dy));
    await r.runFrames(4000);
    assert.equal(moveResults(r).length, 1);
    assert.equal(autonomousWrites, 0);
    assert.equal(r.state.active, true); assert.equal(r.state.released, false);
  });
}

for (const windowOwned of [false, true]) {
  test(`${windowOwned ? 'window' : 'free'}: pending through image readiness resumes the accepted plan after pointercancel`, async () => {
    const r = runtime('cat1', { catOnly: true });
    if (windowOwned) assert.equal(r.admitWindow(), true);
    const phases = [];
    assert.equal(requestMove(r, 'held-entry', {
      onAccepted: () => phases.push('accepted'), onStarted: () => phases.push('started'),
    }), true);
    const plan = r.journey.pairMovePlan;
    const pointer = { type: 'pointerdown', pointerId: 19, button: 0,
      clientX: r.body.x + 48, clientY: r.body.y + 48, stopPropagation() {} };
    assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, r.body, pointer), true);
    const before = ownerSnapshot(r);
    await r.enter(); await r.runFrames(600);
    assert.equal(r.journey.pairMovePlan, plan);
    assert.deepEqual(phases, ['accepted']);
    assert.equal(plan.activityPathDistancePx, 0); assert.equal(moveResults(r).length, 0);
    assert.deepEqual(ownerSnapshot(r), before);
    assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button,
      { type: 'pointercancel', pointerId: 19 }), false);
    assert.equal(r.state.pointerBodyId, ''); assert.equal(r.body.dragging, false);
    await r.frame();
    assert.equal(r.journey.pairMovePlan, plan); assert.deepEqual(phases, ['accepted', 'started']);
    await r.runFrames(plan.durationMs - 1);
    assert.equal(r.journey.pairMovePlan, plan, 'held loading time must not consume the movement cycle');
    await r.frame(32);
    assert.equal(r.journey.pairMovePlan, null);
    assert.deepEqual(moveResults(r).map(value => value.result), ['done']);
    assert.ok(Math.abs(moveResults(r)[0].detail.pathDistancePx - Math.abs(plan.dx)) < .01);
    assertPositionSynced(r);
  });
}

for (const interrupt of ['drag', 'tier', 'exit']) {
  test(`ready image held by pending press cannot revive after ${interrupt}`, async () => {
    const r = runtime('cat1', { catOnly: true }), phases = [];
    assert.equal(requestMove(r, 'interrupted-held-entry', {
      onAccepted: () => phases.push('accepted'), onStarted: () => phases.push('started'),
    }), true);
    const plan = r.journey.pairMovePlan;
    const pointer = { type: 'pointerdown', pointerId: 23, button: 0,
      clientX: r.body.x + 48, clientY: r.body.y + 48, stopPropagation() {} };
    assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, r.body, pointer), true);
    await r.enter(); await r.frame();
    const oldFrame = r.frames.get(r.journey.pairMoveFrame);
    assert.equal(typeof oldFrame, 'function'); assert.deepEqual(phases, ['accepted']);
    if (interrupt === 'drag') {
      r.context._handleNekoIdleCat1PlaygroundPointerMove(r.button,
        {...pointer, type: 'pointermove', clientX: pointer.clientX + 30, clientY: pointer.clientY - 40});
      r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button, {type: 'pointercancel', pointerId: 23});
    } else if (interrupt === 'tier') {
      r.button.setAttribute('data-neko-idle-tier', 'cat2');
      r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    } else {
      r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'test-exit');
    }
    await r.flush();
    assert.equal(r.journey.pairMovePlan, null); assert.equal(r.journey.pairMoveFrame, 0);
    assert.equal(moveResults(r).length, 1); assert.notEqual(moveResults(r)[0].result, 'done');
    assert.equal(moveResults(r)[0].detail.pathDistancePx, 0);
    oldFrame(r.now() + 10000); r.load(); await r.runFrames(10000);
    assert.deepEqual(phases, ['accepted']);
    assert.equal(r.journey.pairMovePlan, null); assert.equal(r.journey.pairMoveFrame, 0);
    assert.equal(moveResults(r).length, 1); assert.equal(plan.activityPathDistancePx, 0);
  });
}


for (const [axis, shift] of [['screenX', 100], ['screenY', 80]]) {
  test(`Playground waits for its window owner to synchronize changed ${axis}`, () => {
    const r = runtime('cat1', { catOnly: true });
    assert.equal(r.admitWindow(), true);
    const snapshot = ownerSnapshot(r);
    const windowBefore = r.window.NekoDesktopWindowGravity.getState();
    const frames = r.frames.size, writes = r.writes.length, draws = r.randomCalls();
    r.window[axis] += shift;
    assert.equal(r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button,
      {left: 100000, top: r.body.y}), null);
    assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, r.body.x + 40, r.body.y), false);
    assert.deepEqual(ownerSnapshot(r), snapshot);
    assert.deepEqual(r.window.NekoDesktopWindowGravity.getState(), windowBefore);
    assert.equal(r.frames.size, frames); assert.equal(r.writes.length, writes);
    assert.equal(r.randomCalls(), draws);
    r.sample({x: 100, y: 100, width: 900, height: 650});
    const target = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button,
      {left: 100000, top: r.body.y});
    assert.ok(target);
    assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, target.left, target.top), true);
    assert.ok(r.window.screenX + r.body.x + r.body.width * (1 - r.body.visibleInsetRatios.right) <= 1000 + .5);
    assert.ok(Math.abs(r.window.screenY + r.body.y + r.body.height * (1 - r.body.visibleInsetRatios.bottom) - 750) <= .5);
    assertPositionSynced(r);
    assert.equal(moveResults(r).length, 0, 'owner synchronization is not autonomous activity');
  });
}

for (const scenario of ['short held', 'expired accepted lease', 'image never loads']) {
  test(`real Cat Mind ${scenario}: pending pointer before first image cannot move or invent effort`, async () => {
    const r = runtime('cat1', { catOnly: true }); attachRealMind(r); r.random(.99);
    await r.advance(0);
    for (let index = 0; index < 20 && !r.journey.pairMovePlan; index++) await r.advance(30000);
    const plan = r.journey.pairMovePlan; assert.ok(plan);
    const entryFields = r.window.nekoCatMind.getState().fields, x = r.body.x;
    assert.ok(r.window.nekoCatMind.getState().pendingActionRequest.runId);
    assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, r.body, {
      pointerId: 19, clientX: r.body.x + 48, clientY: r.body.y + 48,
      button: 0, isPrimary: true, preventDefault() {}, stopPropagation() {},
    }), true);
    if (scenario !== 'image never loads') { r.load(); await r.flush(); }
    await r.runFrames(scenario === 'short held' ? 1000 : 13000);
    assert.equal(r.body.x, x); assert.equal(r.window.nekoCatMind.getState().activeAction, null);
    assert.equal(r.window.nekoCatMind.getState().actionCooldowns.cat1_small_move, undefined);
    for (const field of ['appetite', 'energy', 'sleepiness']) assert.equal(r.window.nekoCatMind.getState().fields[field], entryFields[field]);
    if (scenario === 'short held') {
      assert.equal(r.journey.pairMovePlan, plan);
      assert.equal(moveResults(r).length, 0);
    }
    r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button, {
      pointerId: 19, clientX: r.body.x + 48, clientY: r.body.y + 48,
      type: 'pointerup', preventDefault() {}, stopPropagation() {},
    });
    await r.frame();
    if (scenario === 'short held') {
      const started = r.window.nekoCatMind.getState();
      assert.equal(started.activeAction.actionId, 'cat1_small_move');
      assert.equal(started.actionCooldowns.cat1_small_move.startedAt, started.activeAction.startedAt);
      await r.runFrames(plan.durationMs + 32);
      assert.equal(moveResults(r).length, 1); assert.equal(moveResults(r)[0].result, 'done');
      assert.ok(moveResults(r)[0].detail.pathDistancePx > 0);
      assert.ok(moveResults(r)[0].detail.durationMs <= plan.durationMs + 32, 'held loading time is not physical work');
    } else {
      await r.runFrames(plan.durationMs + 32);
      assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
      assert.equal(moveResults(r)[0].result, 'cancelled');
      assert.equal(moveResults(r)[0].detail.pathDistancePx, 0);
      assert.equal(moveResults(r)[0].detail.durationMs, 0);
      const ended = r.window.nekoCatMind.getState();
      assert.notEqual(ended.pendingActionRequest?.actionId, 'cat1_small_move');
      assert.notEqual(ended.activeAction?.actionId, 'cat1_small_move');
      assert.equal(ended.actionCooldowns.cat1_small_move, undefined);
      for (const field of ['appetite', 'energy', 'sleepiness']) assert.equal(ended.fields[field], entryFields[field]);
      r.load(); await r.runFrames(1000); assert.equal(moveResults(r).length, 1);
    }
  });
}

async function runProtocolHeldMove(heldMs, terminal) {
  const r = runtime('cat1', { catOnly: true }); attachRealMind(r); r.random(.99);
  await r.advance(0);
  for (let index = 0; index < 20 && !r.journey.pairMovePlan; index++) await r.advance(30000);
  const plan = r.journey.pairMovePlan; assert.ok(plan); await r.enter();
  await r.runFrames(plan.durationMs / 2);
  assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, r.body, {
    pointerId: 23, clientX: r.body.x + 48, clientY: r.body.y + 48,
    button: 0, isPrimary: true, preventDefault() {}, stopPropagation() {},
  }), true);
  // Deliver the existing physics/runner frame at the press timestamp. Both
  // controls take the same gesture; only the stationary hold length differs.
  await r.frame(0); const heldX = r.body.x; await r.runFrames(heldMs);
  assert.equal(r.body.x, heldX);
  r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button, {
    pointerId: 23, clientX: r.body.x + 48, clientY: r.body.y + 48,
    type: 'pointerup', preventDefault() {}, stopPropagation() {},
  });
  await r.frame(0);
  if (terminal === 'cancelled') {
    r.context._cancelNekoIdleCat1Journey(r.button, { resetArt: true, reason: 'held-partial-cancelled' });
    await r.flush(); await r.advance(0);
  } else {
    await r.runFrames(plan.durationMs / 2 + 32);
  }
  assert.equal(moveResults(r).length, 1); assert.equal(moveResults(r)[0].result, terminal);
  assert.equal(r.window.nekoCatMind.getState().activeAction, null);
  const result = moveResults(r)[0];
  const facts = r.context._getNekoIdleCat1PairMoveActivityFacts(plan, result.timestamp);
  assert.equal(result.detail.durationMs, facts.durationMs, 'transport preserves the producer active duration');
  const after = r.window.nekoCatMind.getState().fields;
  r.window.dispatchEvent({ type: 'neko:cat-mind:action-result', detail: result }); await r.advance(0);
  assert.deepEqual(r.window.nekoCatMind.getState().fields, after, 'duplicate terminal cannot charge effort twice');
  return { r, result, fields: after };
}

for (const terminal of ['done', 'cancelled']) {
  test(`PG ${terminal}: actual Cat Mind effort excludes pending hold and duplicated terminal`, async () => {
    const control = await runProtocolHeldMove(0, terminal);
    const held = await runProtocolHeldMove(1500, terminal);
    assert.equal(held.result.detail.pathDistancePx, control.result.detail.pathDistancePx);
    assert.equal(held.result.detail.durationMs, control.result.detail.durationMs);
    assert.equal(held.result.timestamp - held.r.window.nekoCatMind.getState().actionCooldowns.cat1_small_move.startedAt,
      control.result.timestamp - control.r.window.nekoCatMind.getState().actionCooldowns.cat1_small_move.startedAt + 1500);
    for (const field of ['appetite', 'energy', 'sleepiness']) {
      assert.equal(held.fields[field], control.fields[field], `held time must not change physical ${field}`);
    }
  });
}

test('ordinary small move retains its original elapsed result duration and no untravelled effort', async () => {
  const r = runtime('cat1', { playground: false }); attachRealMind(r); r.random(.99);
  await r.advance(0);
  for (let index = 0; index < 20 && !r.journey.pairMovePlan; index++) await r.advance(30000);
  assert.ok(r.journey.pairMovePlan);
  const started = r.window.nekoCatMind.getState(), startedAt = started.activeAction.startedAt;
  await r.advance(777); // No movement RAF has applied a position yet.
  r.context._cancelNekoIdleCat1Journey(r.button, { resetArt: true, reason: 'ordinary-cancelled' });
  await r.flush(); await r.advance(0);
  assert.equal(moveResults(r).length, 1);
  assert.equal(moveResults(r)[0].detail.durationMs, r.now() - startedAt);
  assert.equal(moveResults(r)[0].detail.durationMs, 777);
  assert.equal(moveResults(r)[0].detail.pathDistancePx, 0);
  for (const field of ['appetite', 'energy', 'sleepiness']) {
    assert.equal(r.window.nekoCatMind.getState().fields[field], started.fields[field]);
  }
});

test('a changed walking hull never pulls an outside pointer release back into its old window', async () => {
  const r = runtime('cat1', {catOnly: true});
  assert.equal(r.admitWindow(), true);
  const phases = [];
  assert.equal(requestMove(r, 'outside-held-entry', {
    onAccepted: () => phases.push('accepted'), onStarted: () => phases.push('started'),
  }), true);
  const pointer = {type: 'pointerdown',pointerId: 29,button: 0,
    clientX: r.body.x+48,clientY: r.body.y+48,stopPropagation() {}};
  assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button,r.body,pointer),true);
  await r.enter(); await r.runFrames(600);
  const oldHull = r.body.visibleInsetRatios;
  const outsideX = r.window.NekoDesktopWindowGravity.getState().bounds.right+100;
  // Isolate release classification with a real outside body point. The
  // release itself still performs the production art-hull synchronization.
  r.body.x = outsideX;
  r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button,{type:'pointercancel',pointerId:29});
  await r.flush();
  assert.notEqual(r.body.visibleInsetRatios,oldHull);
  assert.equal(r.body.x,outsideX,'no bounds update may clamp the release back into the old window');
  assert.equal(r.window.NekoDesktopWindowGravity.isActive(r.button),false);
  assert.equal(r.body.grounded,false);
  assert.equal(r.journey.pairMovePlan,null);
  assert.deepEqual(phases,['accepted']);
  assert.equal(moveResults(r).length,1);
  assert.notEqual(moveResults(r)[0].result,'done');
  assert.equal(moveResults(r)[0].detail.pathDistancePx,0);
  assert.equal(moveResults(r)[0].detail.durationMs,0);
});

}
