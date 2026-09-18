const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const avatar = path.join(root, 'static/avatar/avatar-ui-buttons');

class Classes {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach(name => this.values.add(name)); }
  remove(...names) { names.forEach(name => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const active = force === undefined ? !this.contains(name) : !!force;
    if (active) this.add(name); else this.remove(name);
    return active;
  }
}

function style(values = {}) {
  return Object.assign({
    setProperty(name, value) { this[name] = String(value); },
    removeProperty(name) { delete this[name]; },
    getPropertyValue(name) { return this[name] || ''; },
  }, values);
}

function runtime(options = {}) {
  let time = 1000;
  let sequence = 0;
  let fact = null;
  let revision = 0;
  let appearance = 'cat';
  const listeners = new Map();
  const subscribers = new Set();
  const frames = new Map();
  const timers = new Map();
  const observers = new Set();
  const attrs = new Map([['data-neko-idle-tier', 'cat1']]);
  const containerAttrs = new Map();
  const gate = {};
  const art = {
    src: 'idle.gif', style: style(),
    setAttribute(name, value) { this[name] = value; },
    getAttribute(name) { return this[name] || ''; },
  };
  const container = {
    id: `${options.model || 'live2d'}-return-button-container`,
    parentNode: {}, isConnected: true, classList: new Classes(),
    style: style({ display: 'block', left: `${options.x ?? 300}px`, top: `${options.y ?? 200}px` }),
    getAttribute(name) {
      if (name === 'data-neko-model-cat-transitioning' && gate.return) return 'cat-to-model';
      return containerAttrs.get(name) || null;
    },
    setAttribute(name, value) { containerAttrs.set(name, value); },
    removeAttribute(name) { containerAttrs.delete(name); },
    getBoundingClientRect() {
      const x = parseFloat(this.style.left), y = parseFloat(this.style.top);
      return { x, y, left: x, top: y, width: 122, height: 122, right: x + 122, bottom: y + 122 };
    },
  };
  const button = {
    isConnected: true, classList: new Classes(), style: style(),
    getAttribute(name) { return attrs.get(name) || null; },
    setAttribute(name, value) { attrs.set(name, value); },
    querySelector(selector) { return selector === '.neko-idle-return-art' ? art : null; },
  };
  const window = {
    innerWidth: 1200, innerHeight: 900,
    screenX: options.screenX || 0, screenY: options.screenY || 0,
    devicePixelRatio: options.devicePixelRatio || 1,
    nekoDesktopWindowSensingContext: options.web ? undefined : {
      getCurrent: () => fact,
      subscribe(fn) { subscribers.add(fn); return () => subscribers.delete(fn); },
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(fn);
    },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    dispatchEvent(event) { [...(listeners.get(event.type) || [])].forEach(fn => fn(event)); },
    requestAnimationFrame(fn) { frames.set(++sequence, fn); return sequence; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(fn) { timers.set(++sequence, fn); return sequence; },
    clearTimeout(id) { timers.delete(id); },
    matchMedia: () => ({ matches: !!options.reduceMotion }),
  };
  const context = vm.createContext({
    window, document: { documentElement: {}, querySelectorAll: () => [button] },
    Date: { now: () => time }, performance: { now: () => time },
    CustomEvent: function (type, init) { this.type = type; this.detail = init.detail; },
    MutationObserver: class {
      constructor(fn) { this.fn = fn; }
      observe() { observers.add(this); }
      disconnect() { observers.delete(this); }
    },
    _NEKO_IDLE_TIER_CAT1: 'cat1', _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _getNekoGoodbyeIdleAppearance: () => appearance,
    _getActiveNekoIdleReturnTier: () => attrs.get('data-neko-idle-tier'),
    _normalizeNekoIdleReturnTier: value => value,
    _getNekoIdleReturnContainerFromButton: value => value === button ? container : null,
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _getNekoIdleCat1WalkingAssetUrl: () => 'walk.gif',
    _setNekoIdleReturnArtSource: (target, src) => { target.src = src; },
    _isNekoIdleReturnDragActionBlocking: () => !!gate.drag,
    _isAnyNekoIdleReturnDragActionBlocking: () => false,
    _isAnyNekoIdleReturnPending: () => false,
    _isNekoIdlePresentationTransitionActive: () => !!gate.transition,
    _isNekoIdleCompactSurfaceDragging: () => !!gate.compact,
    _isNekoIdleCat1EdgePeekActive: () => !!gate.edge,
    _isAnyNekoIdleCat1IndependentActionActive: () => !!gate.action || !!window.NekoDesktopWindowInteractions?.isActive(),
    _isNekoIdleCat1PositionPresentationBusy: () => !!gate.journey,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => !!gate.playground,
  });
  // Use the real return guard: it also covers the drag click-suppression flag.
  // Stubbing it as a transition-only flag hides physics/drag handoff regressions.
  const assetSource = fs.readFileSync(path.join(avatar, 'idle-assets-and-question.js'), 'utf8');
  const pendingStart = assetSource.indexOf('function _isNekoIdleReturnPending(');
  vm.runInContext(assetSource.slice(pendingStart, assetSource.indexOf('\n}', pendingStart) + 2), context);
  for (const file of ['idle-desktop-window-gravity.js', 'idle-desktop-window-interactions.js',
    'idle-desktop-window-top-edge.js', 'idle-desktop-window-edge-peek.js']) {
    vm.runInContext(fs.readFileSync(path.join(avatar, file), 'utf8'), context);
  }
  [...timers.values()].forEach(fn => fn());
  timers.clear();
  const api = {
    window, button, container, art, gate, context,
    state: () => window.NekoDesktopWindowGravity?.getState(),
    frame(ms = 16) {
      time += ms;
      const callbacks = [...frames.values()];
      frames.clear();
      callbacks.forEach(fn => fn(time));
    },
    settle() { for (let i = 0; i < 1000 && frames.size; i++) api.frame(); },
    advance(ms) { time += ms; },
    emit(type, detail = {}) { window.dispatchEvent({ type, detail }); },
    sample(rect = { x: 200, y: 100, width: 600, height: 650 }, extra = {}) {
      fact = rect === null ? null : {
        status: 'current', sessionId: 'session-1', revision: ++revision,
        timestamp: time, rect, changes: [], ...extra,
      };
      [...subscribers].forEach(fn => fn(fact));
      return fact;
    },
    repeat(value) { [...subscribers].forEach(fn => fn(value)); },
    drag(reason, detail = {}) { api.emit('neko:return-ball-manual-move', { reason, container, ...detail }); },
    setAppearance(value) { appearance = value; },
    remove() {
      container.isConnected = button.isConnected = false;
      [...observers].forEach(observer => observer.fn());
    },
    resources: () => ({ frames: frames.size, observers: observers.size, subscribers: subscribers.size }),
  };
  return api;
}

function contained(state) {
  assert.ok(Number.isFinite(state.x) && Number.isFinite(state.y));
  assert.ok(state.x >= state.bounds.left - 0.001 && state.x <= state.bounds.right + 0.001);
  assert.ok(state.y >= state.bounds.top - 0.001 && state.y <= state.bounds.bottom + 0.001);
  assert.ok(Math.abs(state.vx) <= 1800 && Math.abs(state.vy) <= 1800);
}

function sceneWindow(key, rect, kind = 'external') { return { key: `window-${key}`, kind, rect }; }

async function installIdleClock(r) {
  const intervals = new Map();
  const { window, context } = r;
  Object.assign(context.document, {
    body: { classList: new Classes() }, readyState: 'complete',
    addEventListener() {}, getElementById: id => id === 'resetSessionButton' ? {} : null,
    querySelector: () => null,
    querySelectorAll: selector => selector === '.neko-idle-return-btn' ? [r.button] : [],
  });
  context.WebSocket = { OPEN: 1 };
  r.socketMessages = []; r.tierChanges = [];
  Object.assign(window, {
    location: { pathname: '/' }, localStorage: { getItem: () => null },
    appConst: {}, appState: { socket: { readyState: 1, send: message => r.socketMessages.push(message) } },
    live2dManager: { _goodbyeClicked: true },
    setInterval(fn) { intervals.set(intervals.size + 1, fn); return intervals.size; },
    clearInterval(id) { intervals.delete(id); },
  });
  window.addEventListener('neko:auto-goodbye:state-change', event => {
    if (event.detail.type !== 'visual-tier') return;
    r.tierChanges.push(event.detail);
    r.button.setAttribute('data-neko-idle-tier', event.detail.tier);
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-auto-goodbye.js'), 'utf8'), context);
  await Promise.resolve(); await Promise.resolve();
  r.idleTick = () => [...intervals.values()].forEach(fn => fn());
  r.idleState = () => window.nekoAutoGoodbye.getState();
  r.idleTick();
}

test('falling and bouncing pause the real idle clock, then landing starts a full awake interval', async () => {
  const r = runtime(); await installIdleClock(r);
  const napAt = r.idleState().thresholdsMs.cat2;
  r.advance(napAt - 1000); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat1');
  r.sample();
  assert.ok(r.window.nekoAutoGoodbye.getIdleBlockReasons().includes('window-gravity-motion'));
  r.advance(6 * 60 * 1000); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat1', 'time spent airborne cannot advance the tier');
  r.settle(); r.idleTick();
  assert.equal(r.window.NekoDesktopWindowGravity.isMoving(), false);
  assert.ok(!r.window.nekoAutoGoodbye.getIdleBlockReasons().includes('window-gravity-motion'));
  const landedAt = r.context.Date.now();
  assert.equal(r.idleState().lastInteractionAt, landedAt);
  r.advance(1000); r.sample(); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat1', 'the pre-flight countdown must not put the cat to sleep');
  assert.equal(r.idleState().lastInteractionAt, landedAt, 'stationary facts must not restart the clock again');
  const awakeInterval = napAt - r.idleState().thresholdsMs.cat1;
  r.advance(awakeInterval - 1001); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat1');
  r.advance(1); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat2', 'only the full post-landing interval advances the tier');
});

test('another bounce while awake restarts the countdown at the new landing', async () => {
  const r = runtime(); await installIdleClock(r);
  const awakeInterval = r.idleState().thresholdsMs.cat2 - r.idleState().thresholdsMs.cat1;
  r.sample(); r.settle();
  for (let i = 0; i < 2; i++) {
    r.advance(awakeInterval - 1000); r.idleTick();
    assert.equal(r.idleState().visualTier, 'cat1');
    r.sample({ x: 200, y: 100 - i * 30, width: 600, height: 650 }); r.advance(100);
    r.sample({ x: 200, y: 70 - i * 30, width: 600, height: 650 });
    assert.ok(r.state().vy < 0);
    r.settle(); r.idleTick();
    assert.equal(r.idleState().lastInteractionAt, r.context.Date.now());
    assert.equal(r.idleState().visualTier, 'cat1');
  }
});

test('sleeping cats wake immediately on a window launch without resetting their physical trajectory', async () => {
  for (const tier of ['cat2', 'cat3']) for (const secondary of [false, true]) {
    const r = runtime(); await installIdleClock(r);
    const owner = sceneWindow(1, { x: 0, y: 0, width: 1200, height: 900 });
    const panel = { ...sceneWindow(2, { x: 200, y: 450, width: 600, height: 120 }, 'app'), collisionOnly: true };
    const sample = raised => secondary
      ? r.sample(owner.rect, { windows: [{ ...panel, rect: { ...panel.rect, y: raised ? 370 : 450 } }, owner] })
      : r.sample({ x: 200, y: raised ? 20 : 100, width: 600, height: 650 });
    sample(false); r.settle();
    r.advance(r.idleState().thresholdsMs[tier] - r.idleState().thresholdsMs.cat1); r.idleTick();
    assert.equal(r.idleState().visualTier, tier, 'a resting gravity container still allows sleep');
    sample(false); r.advance(100); sample(true);
    assert.equal(r.idleState().visualTier, 'cat1', 'wake occurs in the same native update');
    assert.equal(r.idleState().lastTierSource, 'window-gravity-wake');
    assert.ok(r.state().vy < -400, 'waking must preserve the launch impulse');
    assert.equal(r.window.NekoDesktopWindowGravity.isMoving(), true);
    const beforeTick = r.state(); r.idleTick();
    assert.deepEqual(r.state(), beforeTick);
    r.settle(); r.idleTick();
    assert.equal(r.tierChanges.filter(change => change.source === 'window-gravity-wake').length, 1);
    assert.equal(r.socketMessages.length, 0, 'waking does not trigger a model return or a conversation');
    const awakeInterval = r.idleState().thresholdsMs.cat2 - r.idleState().thresholdsMs.cat1;
    r.advance(awakeInterval - 1); r.idleTick();
    assert.equal(r.idleState().visualTier, 'cat1', 'the awake countdown starts after the bounce finishes');
    r.advance(1); r.idleTick();
    assert.equal(r.idleState().visualTier, 'cat2');
  }
});

test('drag pauses, cancellation and lifecycle cleanup release the idle clock immediately', async () => {
  for (const end of ['press', 'cancel', 'remove', 'ball', 'return', 'pagehide']) {
    const r = runtime(); await installIdleClock(r);
    r.advance(10000); r.sample(); r.frame(); r.advance(20000);
    if (end === 'press') r.drag('return-ball-drag-start');
    if (end === 'cancel') r.window.NekoDesktopWindowGravity.cancel();
    if (end === 'remove') r.remove();
    if (end === 'ball') { r.setAppearance('ball'); r.emit('neko:auto-goodbye:state-change'); }
    if (end === 'return') r.emit('neko:cat-return-commit');
    if (end === 'pagehide') r.emit('pagehide');
    assert.ok(!r.window.NekoDesktopWindowGravity?.isMoving(), end);
    assert.ok(!r.window.nekoAutoGoodbye.getIdleBlockReasons().includes('window-gravity-motion'), end);
    const baseline = r.idleState().lastInteractionAt;
    assert.equal(r.context.Date.now() - baseline, 10000, `${end}: settle the exact moving interval`);
    r.advance(5000); r.idleTick();
    assert.equal(r.idleState().lastInteractionAt, baseline, `${end}: no stuck suppression or double accounting`);
  }
});

test('late idle-controller initialization discovers an already airborne cat and later releases suppression', async () => {
  const r = runtime(); r.sample(); r.frame();
  await installIdleClock(r);
  r.advance(20 * 60 * 1000); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat1');
  r.settle(); r.idleTick();
  r.advance(r.idleState().thresholdsMs.cat2 - r.idleState().thresholdsMs.cat1); r.idleTick();
  assert.equal(r.idleState().visualTier, 'cat2');
});

test('pinned app interiors never activate gravity, including after a drag release; unpinning enables normal containment', () => {
  const r = runtime();
  const panel = { ...sceneWindow(1, { x: 200, y: 100, width: 600, height: 650 }, 'app'), collisionOnly: true };
  r.sample(panel.rect, { windows: [panel] });
  assert.equal(r.state().phase, 'idle');
  prepareThrow(r); releaseThrow(r);
  assert.equal(r.state().phase, 'idle');
  assert.equal(r.window.NekoDesktopWindowGravity.getBounds(r.container), null);
  const y = r.container.style.top;
  r.frame(50);
  assert.equal(r.container.style.top, y);
  r.sample(panel.rect, { windows: [{ ...panel, collisionOnly: false }] });
  assert.equal(r.state().windowKey, panel.key);
  r.frame();
  assert.ok(r.state().vy > 0);
});

test('pinned app borders still support and bounce cats whose gravity belongs to an ordinary window', () => {
  const r = runtime();
  const outer = sceneWindow(2, { x: 0, y: 0, width: 1200, height: 900 });
  const panel = { ...sceneWindow(1, { x: 200, y: 450, width: 600, height: 120 }, 'app'), collisionOnly: true };
  r.sample(outer.rect, { windows: [panel, outer] });
  r.settle();
  assert.equal(r.state().windowKey, outer.key);
  assert.ok(Math.abs(r.state().y + 122 * (1 - 19 / 512) - panel.rect.y) < 0.01);
  r.advance(100);
  r.sample(outer.rect, { windows: [{ ...panel, rect: { ...panel.rect, y: 370 } }, outer] });
  assert.ok(r.state().vy < 0, 'a pinned border still transfers its movement on impact');
});

test('pinned app edges constrain desktop walks in all four directions without starting gravity', () => {
  const panel = { ...sceneWindow(1, { x: 400, y: 300, width: 200, height: 200 }, 'app'), collisionOnly: true };
  for (const [start, target, axis, expected] of [
    [{ x: 200, y: 330 }, { left: 700, top: 330 }, 'left', 400 - 122 * (1 - 118 / 512)],
    [{ x: 700, y: 330 }, { left: 200, top: 330 }, 'left', 600 - 122 * 89 / 512],
    [{ x: 430, y: 100 }, { left: 430, top: 600 }, 'top', 300 - 122 * (1 - 19 / 512)],
    [{ x: 430, y: 600 }, { left: 430, top: 100 }, 'top', 500 - 122 * 49 / 512],
  ]) {
    const r = runtime(start);
    r.gate.action = true; // Keep unrelated edge-peek presentation out of this walk test.
    r.sample(panel.rect, { windows: [panel] });
    const physics = r.window.NekoDesktopWindowGravity;
    const constrained = physics.constrainTarget(r.container, target);
    assert.ok(Math.abs(constrained[axis] - expected) < 0.01);
    assert.equal(physics.applyPosition(r.container, target.left, target.top), true);
    assert.ok(Math.abs(parseFloat(r.container.style[axis]) - expected) < 0.01);
    assert.equal(r.state().phase, 'idle');
    assert.equal(physics.canWalk(r.button), true);
    assert.equal(r.button.classList.contains('is-desktop-window-gravity'), false);
    assert.equal(r.resources().frames, 0);
    r.container.setAttribute('data-dragging', 'true');
    assert.equal(physics.applyPosition(r.container, target.left, target.top), false, 'manual dragging retains control');
    r.container.removeAttribute('data-dragging');
    r.sample(panel.rect, { windows: [] });
    assert.equal(physics.applyPosition(r.container, target.left, target.top), false, 'closed obstacles immediately stop blocking walks');
  }
});

test('pinning an existing gravity container transfers to the ordinary background without resetting momentum', () => {
  const r = runtime();
  const owner = sceneWindow(1, { x: 200, y: 100, width: 600, height: 650 }, 'app');
  const outer = sceneWindow(2, { x: 0, y: 0, width: 1200, height: 900 });
  r.sample(owner.rect, { windows: [owner, outer] });
  assert.equal(r.state().windowKey, owner.key);
  prepareThrow(r); releaseThrow(r);
  const before = r.state();
  const pinned = { ...owner, collisionOnly: true };
  r.sample(owner.rect, { windows: [pinned, outer] });
  assert.equal(r.state().windowKey, outer.key);
  for (const key of ['x', 'y', 'vx', 'vy']) assert.equal(r.state()[key], before[key], key);
  r.sample(owner.rect, { windows: [pinned] });
  assert.equal(r.state().windowKey, '', 'losing the background must not adopt the pinned panel');
  for (const key of ['x', 'y', 'vx', 'vy']) assert.equal(r.state()[key], before[key], key);
});

test('a non-foreground window owns gravity across focus changes and independently moves its floor', () => {
  const r = runtime();
  const front = sceneWindow(1, { x: 850, y: 100, width: 300, height: 500 });
  const back = sceneWindow(2, { x: 200, y: 100, width: 600, height: 650 });
  r.sample(front.rect, { windows: [front, back] });
  assert.equal(r.state().windowKey, back.key);
  r.frame(32);
  const vy = r.state().vy;
  r.sample(back.rect, { windows: [back, front], status: 'changed', changes: ['identity'] });
  assert.equal(r.state().windowKey, back.key);
  assert.equal(r.state().vy, vy);
  r.settle(); r.sample(back.rect, { windows: [front, back] }); r.advance(100);
  const raised = { ...back, rect: { ...back.rect, y: 20 } };
  r.sample(front.rect, { windows: [front, raised] });
  assert.ok(r.state().vy < -400);
  contained(r.state());
});

test('visible foreground borders act as platforms, while occluded borders cannot catch a cat', () => {
  const outer = sceneWindow(1, { x: 0, y: 0, width: 1200, height: 900 });
  const platform = sceneWindow(2, { x: 200, y: 450, width: 600, height: 120 });
  const front = runtime(), covered = runtime();
  front.sample(outer.rect, { windows: [platform, outer] });
  covered.sample(outer.rect, { windows: [outer, platform] });
  front.settle(); covered.settle();
  assert.equal(front.state().phase, 'resting');
  assert.ok(front.state().y > 330 && front.state().y < 334);
  assert.equal(covered.state().y, covered.state().bounds.bottom);
  front.sample(outer.rect, { windows: [platform, outer] }); front.advance(100);
  front.sample(outer.rect, { windows: [{ ...platform, rect: { ...platform.rect, y: 370 } }, outer] });
  assert.ok(front.state().vy < -400, 'a moving secondary border launches a resting cat');
});

test('swept side collisions catch large action moves and a fast moving secondary window', () => {
  const outer = sceneWindow(1, { x: 0, y: 0, width: 1200, height: 900 });
  const obstacle = sceneWindow(2, { x: 400, y: 100, width: 180, height: 800 });
  const r = runtime({ x: 800 }); r.sample(outer.rect, { windows: [obstacle, outer] });
  r.settle();
  r.window.NekoDesktopWindowGravity.applyPosition(r.container, 200, 200);
  assert.ok(r.state().x > 550 && r.state().x < 560, 'the body stops at the right border');
  const target = r.window.NekoDesktopWindowGravity.constrainTarget(r.container, { left: 100, top: 200 });
  assert.ok(target.distance < 0.01, 'walking must finish at a blocking border');
  const moving = runtime({ x: 800 }); moving.sample(outer.rect, { windows: [obstacle, outer] });
  moving.advance(16);
  moving.sample(outer.rect, { windows: [{ ...obstacle, rect: { ...obstacle.rect, x: 1050 } }, outer] });
  assert.ok(moving.state().x > 1020, 'a moving border cannot jump through the cat');
  assert.ok(moving.state().vx > 0);
});

test('explicit empty scenes and transparent gaps between app surfaces never become a full-window gravity box', () => {
  const r = runtime();
  r.sample({ x: 0, y: 0, width: 1200, height: 900 }, { windows: [] });
  assert.equal(r.state().phase, 'idle');
  r.sample(undefined, { windows: [
    sceneWindow(1, { x: 0, y: 0, width: 200, height: 500 }, 'app'),
    sceneWindow(2, { x: 700, y: 0, width: 200, height: 500 }, 'app'),
  ] });
  assert.equal(r.state().phase, 'idle');
  r.sample(undefined, { windows: [sceneWindow(3, { x: 200, y: 100, width: 600, height: 650 }, 'app')] });
  r.frame(); assert.equal(r.state().phase, 'falling');
});

test('secondary left and underside borders bounce action motion and upward playground throws', () => {
  const outer = sceneWindow(1, { x: 0, y: 0, width: 1200, height: 900 });
  const wall = sceneWindow(2, { x: 400, y: 100, width: 180, height: 800 });
  const left = runtime({ x: 80 }); left.sample(outer.rect, { windows: [wall, outer] });
  left.settle();
  left.window.NekoDesktopWindowGravity.applyPosition(left.container, 800);
  assert.ok(left.state().x > 300 && left.state().x < 310);
  assert.ok(left.state().vx < 0);

  const r = runtime({ x: 350, y: 500 });
  const body = { id: 'cat', element: r.container, x: 350, y: 500, vx: 0, vy: -1100, grounded: false };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, bodies: new Map([['cat', body]]) };
  r.sample(outer.rect, { windows: [sceneWindow(3, { x: 200, y: 300, width: 600, height: 120 }), outer] });
  for (let i = 0; i < 14 && r.state().vy < 0; i++) r.frame();
  assert.ok(r.state().vy > 0, 'the bottom border catches the upward throw');
  assert.ok(r.state().y >= 408);
});

test('playground can rest on a secondary border without losing support or restarting gravity', () => {
  const r = runtime();
  const body = { id: 'cat', element: r.container, x: 300, y: 200, vx: 0, vy: 0, grounded: false };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, bodies: new Map([['cat', body]]) };
  r.sample(undefined, { windows: [
    sceneWindow(2, { x: 200, y: 450, width: 600, height: 120 }),
    sceneWindow(1, { x: 0, y: 0, width: 1200, height: 900 }),
  ] });
  r.settle();
  r.window.NekoDesktopWindowGravity.stepBody(r.button, body, r.context.performance.now());
  assert.equal(r.state().phase, 'resting');
  assert.equal(body.grounded, true);
  assert.ok(body.y < 334);
  assert.equal(r.resources().frames, 0);
  r.sample(undefined, { windows: [sceneWindow(1, { x: 0, y: 0, width: 1200, height: 900 })] });
  r.frame(); assert.ok(body.vy > 0, 'closing the supporting window resumes falling');
});

test('closing the containing window transfers motion to a background window without a reset', () => {
  const r = runtime();
  const owner = sceneWindow(1, { x: 200, y: 100, width: 600, height: 650 });
  const outer = sceneWindow(2, { x: 0, y: 0, width: 1200, height: 900 });
  r.sample(owner.rect, { windows: [owner, outer] });
  prepareThrow(r); releaseThrow(r);
  const before = r.state();
  r.sample(outer.rect, { windows: [outer], status: 'changed', changes: ['identity'] });
  const after = r.state();
  assert.equal(after.windowKey, outer.key);
  for (const key of ['x', 'y', 'vx', 'vy']) assert.equal(after[key], before[key], key);
  assert.equal(r.resources().frames, 1); assert.equal(r.resources().observers, 1);
  r.frame(); assert.ok(r.state().y < before.y && r.state().x > before.x);
});

test('closed owner and secondary borders disappear before integrating the pending frame', () => {
  for (const closeOwner of [true, false]) {
    const r = runtime(), control = runtime();
    const outer = sceneWindow(2, { x: 0, y: 0, width: 1200, height: 900 });
    const removed = sceneWindow(1, closeOwner
      ? { x: 200, y: 100, width: 600, height: 350 }
      : { x: 200, y: 450, width: 600, height: 120 });
    r.sample(outer.rect, { windows: [removed, outer] });
    control.sample(outer.rect, { windows: [outer] });
    for (const runtime of [r, control]) { prepareThrow(runtime, 10, 40); releaseThrow(runtime); }
    r.advance(50); r.sample(outer.rect, { windows: [outer] }); control.frame(50);
    assert.ok(r.state().vy > 1000, 'a closed border must not produce a final ghost rebound');
    for (const key of ['x', 'y', 'vx', 'vy']) assert.ok(Math.abs(r.state()[key] - control.state()[key]) < 0.001, key);
    // A RAF whose timestamp precedes the native close notification cannot step twice.
    const before = r.state(); r.frame(-2); assert.equal(r.state().y, before.y);
  }
});

test('closing the last window preserves the throw and finishes once at the desktop floor', () => {
  for (const empty of [
    { status: 'unavailable', reason: 'no-window' },
    { status: 'unavailable', reason: 'no-window-on-model-display' },
    { windows: [] },
  ]) {
    const r = runtime(); r.sample(); prepareThrow(r); releaseThrow(r);
    const before = r.state(); r.sample(undefined, empty);
    assert.equal(r.state().windowKey, '');
    for (const key of ['x', 'y', 'vx', 'vy']) assert.equal(r.state()[key], before[key], key);
    r.frame(); assert.ok(r.state().x > before.x && r.state().y < before.y);
    r.settle(); assert.equal(r.state().phase, 'idle');
    assert.ok(Math.abs(parseFloat(r.container.style.top) + 122 * (1 - 19 / 512) - 900) < 0.01);
    assert.equal(r.resources().frames, 0); assert.equal(r.resources().observers, 0);
    r.sample(undefined, empty); assert.equal(r.state().phase, 'idle', 'empty scenes do not restart gravity');
  }
});

test('no-window notifications never start gravity for a cat that was not inside a window', () => {
  const r = runtime();
  r.sample(undefined, { status: 'unavailable', reason: 'no-window' });
  assert.equal(r.state().phase, 'idle');
  assert.equal(r.resources().frames, 0);
});

test('an ongoing closure fall can bind a newly available window without losing velocity', () => {
  const r = runtime(); r.sample(); prepareThrow(r); releaseThrow(r);
  r.sample(undefined, { windows: [] }); r.frame();
  const before = r.state();
  const next = sceneWindow(3, { x: 0, y: 0, width: 1200, height: 900 });
  r.sample(next.rect, { windows: [next] });
  assert.equal(r.state().windowKey, next.key);
  for (const key of ['x', 'y', 'vx', 'vy']) assert.equal(r.state()[key], before[key], key);
  r.settle(); assert.equal(r.state().phase, 'resting');
});

test('a remaining window border supports a closure fall and closing it resumes the same fall', () => {
  const r = runtime();
  const owner = sceneWindow(1, { x: 200, y: 100, width: 600, height: 650 });
  const platform = sceneWindow(2, { x: 200, y: 500, width: 600, height: 120 });
  r.sample(owner.rect, { windows: [owner, platform] });
  r.sample(platform.rect, { windows: [platform] }); r.settle();
  assert.equal(r.state().windowKey, ''); assert.equal(r.state().phase, 'resting');
  const before = r.state(); assert.ok(Math.abs(before.y + 122 * (1 - 19 / 512) - 500) < 0.01);
  for (let i = 0; i < 20; i++) { r.advance(16); r.sample(platform.rect, { windows: [platform] }); }
  assert.equal(r.resources().frames, 0, 'unchanged scenes must not wake a resting continuation');
  r.sample(undefined, { status: 'unavailable', reason: 'no-window' });
  assert.equal(r.state().x, before.x); assert.equal(r.state().y, before.y);
  r.frame(); assert.ok(r.state().y > before.y); r.settle();
  assert.equal(r.state().phase, 'idle');
});

test('closing a window during a held press preserves the pointer and releases smoothly', () => {
  const r = runtime(); r.sample(); r.frame();
  r.drag('return-ball-drag-start');
  const before = r.state(), top = r.container.style.top;
  r.advance(200); r.sample(undefined, { status: 'unavailable', reason: 'no-window' });
  r.frame(500);
  assert.equal(r.state().pointerHeld, true); assert.equal(r.container.style.top, top);
  r.drag('return-ball-drag-end');
  assert.equal(r.state().vy, before.vy);
  r.frame(); assert.ok(r.state().y > before.y);
});

test('closing a moving box after impact preserves its rebound and squash through the handoff', () => {
  const r = runtime();
  const owner = sceneWindow(1, { x: 200, y: 100, width: 600, height: 650 });
  const outer = sceneWindow(2, { x: 0, y: 0, width: 1200, height: 900 });
  r.sample(owner.rect, { windows: [owner, outer] }); r.settle();
  r.sample(owner.rect, { windows: [owner, outer] }); r.advance(100);
  const raised = { ...owner, rect: { ...owner.rect, y: 20 } };
  r.sample(raised.rect, { windows: [raised, outer] });
  const before = r.state(), squash = r.button.style.getPropertyValue('--neko-window-gravity-scale-y');
  assert.ok(before.vy < -400); assert.notEqual(squash, '1.0000');
  r.sample(outer.rect, { windows: [outer] });
  assert.equal(r.state().vy, before.vy); assert.equal(r.state().y, before.y);
  assert.equal(r.button.style.getPropertyValue('--neko-window-gravity-scale-y'), squash);
  r.frame(); assert.ok(r.state().y < before.y);
});

test('closure continuation preserves playground impulses and is still cancelled by lifecycle cleanup', () => {
  const r = runtime();
  const body = { id: 'cat', element: r.container, x: 300, y: 200, vx: 250, vy: -200,
    grounded: false, dragging: false };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, bodies: new Map([['cat', body]]) };
  r.sample(); r.sample(undefined, { status: 'unavailable', reason: 'no-window' });
  r.frame(16);
  const before = r.state();
  r.window.NekoDesktopWindowGravity.stepBody(r.button, body, r.context.performance.now());
  assert.equal(r.state().y, before.y); assert.equal(body.vy, -200 + 1440 * 0.016);
  r.sample(null);
  assert.equal(r.state().phase, 'idle'); assert.equal(r.resources().frames, 0);
  assert.equal(r.resources().observers, 0);
});

test('a contained cat falls alongside actions and settles without owning the presentation lock or RAF work', () => {
  const r = runtime();
  r.sample();
  assert.equal(r.window.NekoDesktopWindowInteractions.getState().activeKind, '');
  let fell = false, rebounded = false;
  for (let i = 0; i < 800 && r.resources().frames; i++) {
    r.frame();
    const state = r.state();
    contained(state);
    fell ||= state.vy > 100;
    rebounded ||= state.vy < -100;
  }
  assert.ok(fell && rebounded);
  assert.equal(r.state().phase, 'resting');
  assert.equal(r.state().y, r.state().bounds.bottom);
  assert.equal(r.resources().frames, 0);
  for (let i = 0; i < 20; i++) { r.advance(200); r.sample(); }
  assert.equal(r.resources().frames, 0, 'stationary window heartbeats must not inject energy');
});

test('vertical window shaking launches the cat and loses energy after the window stops', () => {
  const r = runtime();
  r.sample(); r.settle(); r.sample(); r.advance(200);
  r.sample({ x: 200, y: 20, width: 600, height: 650 }, { status: 'changed', changes: ['position'] });
  assert.ok(r.state().vy < -400);
  const peaks = [];
  let previousVy = r.state().vy;
  for (let i = 0; i < 1000 && r.resources().frames; i++) {
    r.frame();
    const state = r.state(); contained(state);
    if (previousVy < 0 && state.vy >= 0) peaks.push(state.bounds.bottom - state.y);
    previousVy = state.vy;
  }
  assert.ok(peaks.length >= 2);
  assert.ok(peaks[1] < peaks[0] * 0.6, 'later rebounds must be visibly smaller');
  assert.equal(r.state().phase, 'resting');
  assert.equal(r.resources().frames, 0);
});

test('all four moving walls resolve inward with bounded speed, including large resize steps', () => {
  for (const [box, axis, sign] of [
    [{ x: 390, y: 100, width: 500, height: 650 }, 'vx', 1],
    [{ x: 200, y: 100, width: 120, height: 650 }, 'vx', -1],
    [{ x: 200, y: 300, width: 600, height: 500 }, 'vy', 1],
    [{ x: 200, y: 0, width: 600, height: 230 }, 'vy', -1],
  ]) {
    const r = runtime(); r.sample(); r.advance(200); r.sample(box);
    contained(r.state());
    assert.ok(r.state()[axis] * sign > 0, `${axis} points into the box`);
    for (let i = 0; i < 80; i++) { r.frame(); contained(r.state()); }
  }
});

test('floor friction carries horizontal motion and reversing windows do not tunnel through the cat', () => {
  const r = runtime(); r.sample(); r.settle();
  for (let i = 0; i < 12; i++) {
    r.advance(200);
    r.sample({ x: i % 2 ? 100 : 440, y: i % 2 ? 90 : 40, width: 600, height: 650 });
    contained(r.state());
    for (let j = 0; j < 8; j++) { r.frame(); contained(r.state()); }
  }
  r.settle();
  assert.equal(r.state().phase, 'resting');
});

test('old revisions and long sample gaps cannot fabricate a shake', () => {
  const r = runtime(); const old = r.sample(); r.settle(); r.advance(5000);
  const moved = r.sample({ x: 200, y: 20, width: 600, height: 650 });
  assert.equal(r.state().vy, 0);
  r.repeat(old);
  assert.equal(r.state().revision, moved.revision);
  r.repeat(moved);
  assert.equal(r.state().vy, 0);
  r.frame(60000); contained(r.state());
});

test('frame-rate window updates preserve falling time between native facts and RAF callbacks', () => {
  const still = runtime(); const moving = runtime();
  still.sample(); moving.sample();
  for (let i = 0; i < 30; i++) {
    still.frame(16);
    moving.advance(8);
    moving.sample({ x: 200 + i % 2, y: 100, width: 600, height: 650 });
    moving.frame(8);
  }
  assert.ok(Math.abs(still.state().y - moving.state().y) < 0.5);
  assert.ok(Math.abs(still.state().vy - moving.state().vy) < 0.5);
});

test('a native sample followed by an older RAF timestamp cannot double-integrate motion', () => {
  const r = runtime(); r.sample();
  r.advance(16); r.sample({ x: 201, y: 100, width: 600, height: 650 });
  const before = r.state();
  r.frame(-2);
  assert.equal(r.state().y, before.y);
  assert.equal(r.state().vy, before.vy);
  r.frame(18);
  const control = runtime(); control.sample(); control.frame(16); control.frame(16);
  assert.ok(Math.abs(r.state().y - control.state().y) < 0.5);
});

test('press freezes position, stationary release resumes, real drag releases ownership immediately', () => {
  const r = runtime(); r.sample(); r.frame();
  r.container.setAttribute('data-dragging', 'pending');
  r.drag('return-ball-drag-start');
  const before = r.container.style.top;
  r.frame(500);
  assert.equal(r.container.style.top, before);
  assert.equal(r.state().pointerHeld, true);
  r.container.removeAttribute('data-dragging');
  r.drag('return-ball-drag-end'); r.frame();
  assert.notEqual(r.container.style.top, before);
  r.drag('return-ball-drag-start');
  r.drag('return-ball-drag-active');
  assert.equal(r.state().phase, 'idle');
  assert.equal(r.resources().frames, 0);
  const deadline = r.window.NekoDesktopWindowInteractions.getState().cooldownUntil;
  r.container.style.left = '350px'; r.container.style.top = '200px';
  r.drag('return-ball-drag-end'); r.advance(200); r.sample();
  assert.equal(r.state().phase, 'falling', 'drop back inside resumes gravity immediately');
  assert.equal(r.window.NekoDesktopWindowInteractions.getState().cooldownUntil, deadline);
});

test('an existing edge presentation cooldown does not delay containment gravity', () => {
  const r = runtime();
  r.window.NekoDesktopWindowInteractions.completePresentation(30000);
  r.sample();
  assert.equal(r.state().phase, 'falling');
});

function dragSample(r, reason, x, y, extra = {}) {
  r.drag(`return-ball-drag-${reason}`, {
    dragSessionId: 1, screenX: x, screenY: y, timestamp: r.context.Date.now(), ...extra,
  });
}

function prepareThrow(r, dx = 40, dy = -40) {
  r.container.setAttribute('data-dragging', 'pending');
  dragSample(r, 'start', 300, 300);
  r.container.setAttribute('data-dragging', 'true');
  dragSample(r, 'active');
  r.advance(20); dragSample(r, 'motion', 300 + dx / 2, 300 + dy / 2);
  r.advance(20); dragSample(r, 'motion', 300 + dx, 300 + dy);
  r.container.style.left = '350px'; r.container.style.top = '300px';
}

function releaseThrow(r, extra = {}) {
  r.container.setAttribute('data-dragging', 'false');
  dragSample(r, 'end', undefined, undefined, extra);
}

test('release carries both throw axes into gravity, then falls and rebounds inside the window', () => {
  const r = runtime(); r.sample(); prepareThrow(r); releaseThrow(r);
  assert.equal(r.state().vx, 1000); assert.equal(r.state().vy, -1000);
  const release = r.state(); r.frame();
  assert.ok(r.state().x > release.x && r.state().y < release.y);
  assert.ok(r.state().vy > release.vy, 'gravity decelerates an upward throw immediately');
  r.settle(); contained(r.state()); assert.equal(r.state().phase, 'resting');
});

test('a drag can start outside gravity and finish inside, with capped downward throw speed', () => {
  const r = runtime({ x: 20 }); r.sample(); assert.equal(r.state().phase, 'idle');
  prepareThrow(r, -2000, 2000); releaseThrow(r);
  assert.equal(r.state().vx, -1800); assert.equal(r.state().vy, 1800);
  r.frame(); contained(r.state()); assert.ok(r.state().y > 300);
});

test('throws use recent motion and survive asynchronous viewport restoration', () => {
  const r = runtime(); r.sample(); prepareThrow(r, 100, 100);
  r.advance(500);
  dragSample(r, 'motion', 400, 400);
  r.advance(20); dragSample(r, 'motion', 380, 380);
  r.advance(20); dragSample(r, 'motion', 360, 360);
  const releasedAt = r.context.Date.now();
  r.advance(600);
  // Origin/viewport changes must not be mistaken for pointer motion.
  r.window.screenX = -100; r.window.screenY = -50;
  releaseThrow(r, { releasedAt });
  assert.equal(r.state().vx, -1000); assert.equal(r.state().vy, -1000);
  r.frame(); contained(r.state());
});

test('stopping, cancelling, or replacing a drag never replays old throw velocity', () => {
  for (const mode of ['stopped', 'cancelled', 'new-drag', 'return']) {
    const r = runtime(); r.sample(); prepareThrow(r);
    if (mode === 'stopped') r.advance(150);
    if (mode === 'new-drag') dragSample(r, 'start', 340, 260);
    if (mode === 'return') r.emit('neko:cat-return-commit');
    releaseThrow(r, { dragCancelled: mode === 'cancelled' });
    assert.equal(r.state().vx, 0, mode); assert.equal(r.state().vy, 0, mode);
  }
});

test('unrelated or stale release events cannot consume the current drag', () => {
  const r = runtime(); r.sample(); prepareThrow(r);
  r.drag('return-ball-drag-end', { container: {}, dragSessionId: 1 });
  dragSample(r, 'end', undefined, undefined, { dragSessionId: 0 });
  assert.equal(r.state().phase, 'idle');
  releaseThrow(r);
  assert.equal(r.state().vx, 1000); assert.equal(r.state().vy, -1000);
});

test('playground release velocity is transferred once without adding the ordinary drag impulse', () => {
  const r = runtime(); r.sample(); prepareThrow(r);
  const body = { id: 'cat', element: r.container, x: 350, y: 300, vx: 200, vy: -300, grounded: false };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, bodies: new Map([['cat', body]]) };
  releaseThrow(r);
  assert.equal(r.state().vx, 200); assert.equal(r.state().vy, -300);
  r.frame(); assert.ok(body.vx < 200 && body.vy > -300);
});

function installDragProducer(r, model, { fromCreation = false } = {}) {
  const containerEvents = new Map(), documentEvents = new Map();
  r.container.addEventListener = (type, callback) => containerEvents.set(type, callback);
  r.container.contains = () => true;
  r.container.querySelector = () => r.button;
  r.context.document.addEventListener = (type, callback) => documentEvents.set(type, callback);
  Object.assign(r.context, {
    AvatarButtonMixin: { methods: {} }, VRMManager: function () {},
    requestAnimationFrame: r.window.requestAnimationFrame, cancelAnimationFrame: r.window.cancelAnimationFrame,
    setTimeout: r.window.setTimeout, clearTimeout: r.window.clearTimeout,
    CustomEvent: function (type, init) { this.type = type; this.detail = init.detail; },
    _getNekoIdleReturnButtonFromContainer: () => r.button,
    _isNekoIdleThoughtBubbleEventHit: () => false,
    _getNekoDesktopVirtualViewportSize: () => ({ width: 1200, height: 900 }),
    _restoreNekoIdleCat1EdgePeekBeforeDrag: () => {},
    _applyNekoIdleCat1EdgePeekAfterDrag: () => {},
    _dispatchNekoIdleReturnBallManualMove: (container, reason, detail) => r.drag(reason, { container, ...detail }),
  });
  let manager;
  if (model === 'native') {
    const noop = () => {};
    Object.assign(r.context.document, {
      body: { style: style(), dataset: {} }, head: { appendChild: noop },
      getElementById: () => null, createElement: () => ({}),
    });
    r.context.document.documentElement.style = style();
    r.window.__NEKO_MULTI_WINDOW__ = true;
    r.window.__appUiParts = {
      MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE: 136,
      cleanupMultiWindowReturnBallDrag: noop, clearMultiWindowReturnBallDeferredWork: noop,
      clearReturnBallDragRecoveryTimer: noop, isNativeReturnBallDragDisabled: () => false,
      isIdleCat1PlaygroundActiveForReturnBallDesktopBridge: () => false,
      isNiriPhysicalCropReturnBallDragActive: () => false, isNekoIdleCat1EdgePeekEligible: () => false,
      restoreNekoIdleCat1EdgePeekBeforeDrag: noop, applyNekoIdleCat1EdgePeek: () => false,
      clearNekoIdleCat1EdgePeek: noop, scheduleIdleReturnBallDesktopBridge: noop,
      scheduleIdleReturnBallDesktopDragState: noop, getReturnBallDragScreenRect: () => ({}),
      setPendingNativeModelViewportRestoreBounds: noop,
      getReturnBallDragScreenCoordinate: (value, fallback) => Number.isFinite(value) ? value : fallback,
    };
    r.window.nekoPetDrag = {
      start: () => true, reveal: () => true,
      stop: () => new Promise(resolve => { r.restoreNativeViewport = () => resolve({ x: 0, y: 0, width: 1200, height: 900 }); }),
    };
    vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-ui/return-window-drag.js'), 'utf8'), r.context);
    r.window.__appUiParts.ensureMultiWindowReturnBallDrag(r.container);
  } else {
    vm.runInContext(fs.readFileSync(path.join(avatar, 'methods-return.js'), 'utf8'), r.context);
    if (model === 'vrm') {
      r.context.AvatarButtonMixin.methods.returnButton(r.context.VRMManager.prototype, model, {});
      const source = fs.readFileSync(path.join(root, 'static/vrm/vrm-ui-buttons.js'), 'utf8');
      vm.runInContext(source.slice(source.indexOf('VRMManager.prototype._setupReturnButtonDrag = function'),
        source.indexOf('VRMManager.prototype._addReturnButtonBreathingAnimation')), r.context);
      manager = new r.context.VRMManager();
    } else {
      manager = {};
      r.context.AvatarButtonMixin.methods.returnButton(manager, model, {});
    }
  }
  if (manager && fromCreation) {
    // Exercise the production initialization gate, not just its drag helper.
    // The button is normally constructed while hidden and revealed later.
    const savedStyle = { ...r.container.style };
    const element = () => ({ style: style(), setAttribute() {}, addEventListener() {}, appendChild() {} });
    const created = [r.container, r.button, r.art, element(), element(), element()];
    r.container.appendChild = () => {};
    r.button.addEventListener = () => {};
    r.button.appendChild = () => {};
    r.context.document.createElement = () => { assert.ok(created.length); return created.shift(); };
    r.context.document.body ||= { style: style(), dataset: {} };
    r.context.document.body.appendChild = () => {};
    Object.assign(r.context, {
      _readNekoAutoGoodbyeVisualTier: () => r.button.getAttribute('data-neko-idle-tier'),
      _NEKO_IDLE_RETURN_DEFAULT_Z_INDEX: 10,
      _NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL: 'bubble.gif',
      _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS: ['item.png'],
      _getNekoIdleReturnAssetUrl: () => 'idle.gif',
      _getNekoIdleThoughtBubbleBgAssetUrl: value => value,
      _getNekoIdleThoughtBubbleItemAssetUrl: value => value,
      _applyNekoIdleReturnPresentation() {},
      _isNekoNativeReturnBallDragDisabled: () => !!r.window.__NEKO_DESKTOP_RUNTIME__?.disableNativeReturnBallDrag,
    });
    manager._avatarPrefix = model;
    manager._avatarButtonOptions = { returnContainerId: `${model}-return-button-container`,
      returnBtnId: `${model}-btn-return`, returnBtnClass: `${model}-return-btn` };
    assert.equal(manager.createReturnButton(), r.container);
    Object.assign(r.container.style, savedStyle);
  } else if (manager) manager._setupReturnButtonDrag(r.container);
  const pointer = (x, y) => ({ button: 0, buttons: 1, target: r.container,
    clientX: x, clientY: y, screenX: x + r.window.screenX, screenY: y + r.window.screenY,
    preventDefault() {}, stopImmediatePropagation() { this.stopped = true; }, stopPropagation() {},
  });
  return {
    down: (x, y) => { const event = pointer(x, y); containerEvents.get('mousedown')(event); return event; },
    move: (x, y) => documentEvents.get('mousemove')(pointer(x, y)),
    up: () => documentEvents.get('mouseup')(pointer(400, 220)),
    cancel: () => documentEvents.get('touchcancel')({ type: 'touchcancel' }),
  };
}

test('real DOM drag producers for every model hand their release to the shared gravity layer', () => {
  for (const model of ['live2d', 'vrm', 'mmd', 'pngtuber']) {
    const r = runtime({ model }); r.sample();
    const pointer = installDragProducer(r, model);
    pointer.down(360, 260);
    r.advance(20); pointer.move(380, 240); r.frame(16);
    r.advance(20); pointer.move(400, 220); pointer.up();
    if (model !== 'vrm') { r.frame(100); r.frame(100); }
    assert.ok(r.state().vx > 500 && r.state().vy < -500, model);
    assert.equal(r.container.getAttribute('data-dragging'), 'false');
    assert.equal(r.container.getAttribute('data-neko-return-click-suppressed'), 'true');
    const before = r.state(); r.frame();
    assert.ok(r.state().x > before.x && r.state().y < before.y, model);
  }
});

test('Windows button creation wires every cat provider for continuous drag, including later appearance switches', () => {
  for (const model of ['live2d', 'vrm', 'mmd', 'pngtuber']) {
    for (const initialAppearance of ['cat', 'ball']) {
      const r = runtime({ model });
      r.window.__NEKO_DESKTOP_RUNTIME__ = { platform: 'win32' };
      r.sample();
      const native = installDragProducer(r, 'native');
      r.window.nekoPetDrag.start = () => { throw new Error('must not shrink the carrier'); };
      r.setAppearance(initialAppearance);
      const dom = installDragProducer(r, model, { fromCreation: true });
      r.setAppearance('cat');
      assert.ok(!native.down(360, 260).stopped, model);
      dom.down(360, 260);
      r.advance(20); dom.move(380, 240); r.frame();
      r.advance(20); dom.move(400, 220); dom.up();
      assert.equal(r.container.getAttribute('data-dragging'), 'false', 'no two-frame handoff wait');
      assert.ok(r.state().vx > 500 && r.state().vy < -500, model);
      const released = r.container.getBoundingClientRect();
      assert.ok(Math.abs(r.state().x - released.x) < 0.01);
      assert.ok(Math.abs(r.state().y - released.y) < 0.01);
      assert.equal(r.context._isNekoIdleReturnPending(r.button), true, 'the real click guard is still set');
      for (let i = 0; i < 8; i++) {
        assert.notEqual(r.container.style.opacity, '0');
        assert.notEqual(r.container.style.visibility, 'hidden');
        assert.equal(r.context.document.body.dataset.nekoBallDrag, undefined);
        r.frame();
        assert.notEqual(r.state().phase, 'idle', 'click suppression must not stop the following physics frame');
      }
      assert.ok(r.state().x > released.x + 10, 'the cat keeps its horizontal throw');
    }
  }
});

test('actual return transitions still interrupt a throw even while drag clicks are suppressed', () => {
  const r = runtime();
  r.window.__NEKO_DESKTOP_RUNTIME__ = { platform: 'win32' };
  r.sample();
  const pointer = installDragProducer(r, 'live2d', { fromCreation: true });
  pointer.down(360, 260);
  r.advance(20); pointer.move(380, 240);
  r.advance(20); pointer.move(400, 220); pointer.up();
  assert.equal(r.state().phase, 'falling');
  assert.equal(r.context._isNekoIdleReturnPending(r.button), true);
  r.container.setAttribute('data-neko-model-cat-transitioning', 'cat-to-model');
  r.frame();
  assert.equal(r.state().phase, 'idle');
  r.sample();
  assert.equal(r.state().phase, 'idle', 'window facts must not restart physics during return');
});

test('continuous drag is limited to real Windows cats, including entry from outside a window', () => {
  const r = runtime();
  r.window.__NEKO_DESKTOP_RUNTIME__ = { platform: 'win32' };
  const gravity = r.window.NekoDesktopWindowGravity;
  assert.equal(gravity.isActive(), false);
  assert.equal(gravity.usesContinuousDrag(r.container), true);
  r.setAppearance('ball');
  assert.equal(gravity.usesContinuousDrag(r.container), false);
  r.setAppearance('cat');
  r.window.__NEKO_DESKTOP_RUNTIME__.platform = 'linux';
  assert.equal(gravity.usesContinuousDrag(r.container), false);
});

test('real native drag completion preserves the pointer-release time across IPC and viewport waits', async () => {
  for (const cancelled of [false, true]) {
    const r = runtime(); r.sample(); const pointer = installDragProducer(r, 'native');
    pointer.down(360, 260);
    r.advance(20); pointer.move(380, 240);
    r.advance(20); pointer.move(400, 220);
    if (cancelled) pointer.cancel(); else pointer.up();
    r.advance(600); r.restoreNativeViewport();
    await new Promise(resolve => setImmediate(resolve));
    r.frame(100); r.frame(100);
    assert.equal(r.container.getAttribute('data-dragging'), 'false');
    assert.equal(r.state().vx, cancelled ? 0 : 1000);
    assert.equal(r.state().vy, cancelled ? 0 : -1000);
  }
});

test('each lifecycle interruption clears physics without restoring an obsolete position', () => {
  for (const interrupt of [
    r => r.sample(null),
    r => r.sample({}, { status: 'unavailable', reason: 'read-failed' }),
    r => r.sample(undefined, { changes: ['identity'], status: 'changed' }),
    r => r.sample(undefined, { sessionId: 'new-session' }),
    r => r.sample({ x: 200, y: 100, width: 20, height: 20 }),
    r => r.emit('neko:cat-return-commit'),
    r => r.emit('neko:goodbye-state-cleared'),
    r => { r.setAppearance('ball'); r.frame(); },
    r => r.remove(),
  ]) {
    const r = runtime(); r.sample(); r.frame();
    r.gate.action = true; // Isolate gravity cleanup from newly eligible edge presentations.
    r.container.style.left = '550px';
    interrupt(r);
    assert.equal(r.state().phase, 'idle');
    assert.equal(r.resources().frames, 0);
    assert.equal(r.resources().observers, 0);
    assert.equal(r.container.style.left, '550px');
    assert.equal(r.button.classList.contains('is-desktop-window-gravity'), false);
    assert.equal(r.button.style.getPropertyValue('--neko-window-gravity-scale-y'), '');
  }
});

test('native coordinates use DIP once and all avatar providers share the same runner', () => {
  for (const model of ['live2d', 'vrm', 'mmd', 'pngtuber']) {
    const r = runtime({ model, screenX: -1200, screenY: -100, devicePixelRatio: 2 });
    r.sample({ x: -1000, y: 0, width: 600, height: 650 }); r.settle();
    assert.equal(r.state().phase, 'resting');
    assert.ok(r.state().bounds.left < 200 && r.state().bounds.left > 170);
    contained(r.state());
  }
});

test('a renderer origin change preserves desktop position without treating it as a moving wall', () => {
  const r = runtime(); r.sample(); r.settle(); r.sample();
  const before = r.state();
  r.window.screenX = 100; r.window.screenY = 50;
  r.advance(200); r.sample();
  const after = r.state();
  assert.equal(after.x + 100, before.x);
  assert.equal(after.y + 50, before.y);
  assert.equal(after.vx, 0); assert.equal(after.vy, 0);
  contained(after);
});

test('moving the window during a stationary press resumes with containment and no stale impulse', () => {
  const r = runtime(); r.sample(); r.settle();
  r.drag('return-ball-drag-start');
  const before = r.container.style.top;
  r.advance(200); r.sample({ x: 200, y: 20, width: 600, height: 650 });
  assert.equal(r.container.style.top, before);
  r.drag('return-ball-drag-end');
  contained(r.state());
  assert.equal(r.state().vy, 0);
});

test('only missing containment, return and a held pointer prevent gravity; action gates do not', () => {
  for (const settings of [{ x: 20 }, { y: 20 }]) {
    const r = runtime(settings); r.sample(); assert.equal(r.state().phase, 'idle');
  }
  for (const key of ['transition', 'compact', 'edge', 'action', 'journey', 'playground']) {
    const r = runtime(); r.gate[key] = true; r.sample();
    assert.equal(r.state().phase, 'falling', key);
    r.frame(32);
    assert.ok(r.state().y > 200, key);
  }
  const returning = runtime(); returning.gate.return = true; returning.sample();
  assert.equal(returning.state().phase, 'idle');
  const held = runtime(); held.container.setAttribute('data-dragging', 'true'); held.sample();
  assert.equal(held.state().phase, 'idle');
});

test('hover, eating, stretching, playing and sleeping keep their art while gravity continues', () => {
  for (const tier of ['cat1', 'cat2', 'cat3']) {
    for (const action of ['hover', 'eating', 'stretching', 'playing', 'sleeping']) {
      const r = runtime();
      r.button.setAttribute('data-neko-idle-tier', tier);
      r.button.classList.add(`is-cat1-${action}`);
      r.art.src = `${action}.gif`;
      r.art.__nekoIdleHoverSrc = action === 'hover' ? r.art.src : '';
      r.gate.action = true;
      r.sample(); r.frame(32);
      assert.ok(r.state().y > 200, `${tier}/${action}`);
      assert.equal(r.art.src, `${action}.gif`);
      assert.equal(r.button.classList.contains(`is-cat1-${action}`), true);
      assert.equal(r.window.NekoDesktopWindowInteractions.isActive(), false);
      r.settle(); assert.equal(r.state().phase, 'resting');
    }
  }
});

test('tier changes and playground entry preserve position, momentum and the shared gravity lifetime', () => {
  const r = runtime(); r.sample(); r.frame(32);
  for (const tier of ['cat2', 'cat3', 'cat1']) {
    const before = r.state();
    r.button.setAttribute('data-neko-idle-tier', tier);
    r.emit('neko:auto-goodbye:state-change', { type: 'visual-tier', tier });
    assert.equal(r.state().vy, before.vy);
    r.frame(32); assert.ok(r.state().y > before.y);
  }
  r.emit('neko:idle-cat1-playground-state', { active: true });
  assert.equal(r.state().phase, 'falling');
});

test('stale movement plans cannot overwrite airborne momentum and work again after landing', () => {
  const r = runtime(); r.sample();
  const source = fs.readFileSync(path.join(avatar, 'idle-journey-and-presentation.js'), 'utf8');
  for (const name of ['_setNekoIdleCat1ContainerPosition', '_easeNekoIdleCat1PairMove', '_applyNekoIdleCat1PairMovePlan']) {
    const start = source.indexOf(`function ${name}(`);
    const end = source.indexOf('\n}', start) + 2;
    vm.runInContext(source.slice(start, end), r.context);
  }
  const plan = { container: r.container, catStartLeft: 300, catStartTop: 200, dx: 60, dy: -80 };
  for (let i = 1; i <= 30; i++) {
    r.frame();
    const before = r.state();
    r.context._applyNekoIdleCat1PairMovePlan(plan, i / 30);
    assert.ok(r.state().y >= before.y, 'the captured action start must not reset falling');
    assert.equal(r.state().x, before.x, 'walking must not override the airborne position');
    contained(r.state());
  }
  assert.ok(r.state().y > 350);
  assert.equal(r.state().x, 300);
  const target = r.window.NekoDesktopWindowGravity.constrainTarget(r.container, { left: 5000, top: 0 });
  assert.equal(target.left, r.state().bounds.right);
  assert.equal(target.top, r.state().y);
  r.settle();
  r.context._setNekoIdleCat1ContainerPosition(r.container, 360, 0);
  assert.equal(r.state().x, 360);
  r.context._setNekoIdleCat1ContainerPosition(r.container, 5000, 0);
  contained(r.state());
});

function installJourneyGravityBridge(r) {
  const source = fs.readFileSync(path.join(avatar, 'idle-journey-and-presentation.js'), 'utf8');
  for (const name of ['_pauseNekoIdleCat1JourneyForGravity', '_ensureNekoIdleReturnPresentationBridge',
    '_stepNekoIdleCat1Walk', '_startNekoIdleCat1Walk', '_scheduleNekoIdleCat1WalkStart',
    '_prepareNekoIdleCat1PairMoveStart', '_canScheduleNekoIdleCat1PairMove',
    '_stepNekoIdleCat1PairMove', '_finishNekoIdleCat1PairMove', '_syncNekoIdleCat1Journey']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), r.context);
  }
  r.journeyCancellations = []; r.journeySyncs = 0;
  r.context._cancelNekoIdleCat1Journey = (button, options) => {
    r.journeyCancellations.push(options);
    const state = button.__nekoIdleCat1Journey;
    state.substate = 'idle'; state.pairMovePlan = null; state.pairMoveFrame = 0;
    state.pendingWalkTimer = 0; state.pendingWalkReady = false; state.frame = 0;
    if (options.resetArt) r.art.src = 'idle.gif';
  };
  r.context._scheduleNekoIdleCat1JourneySync = () => { r.journeySyncs++; };
  Object.assign(r.context, {
    _readNekoAutoGoodbyeVisualTier: () => 'cat1', _NEKO_GOODBYE_IDLE_APPEARANCE_BALL: 'ball',
    _syncNekoIdleSleepSoundForTier() {}, _syncNekoIdleCat1AmbientSoundForTier() {},
  });
  r.context._ensureNekoIdleReturnPresentationBridge();
}

test('gravity interrupts locomotion on takeoff, blocks all walk entry points and resumes only after settling', () => {
  const r = runtime(); installJourneyGravityBridge(r);
  const state = r.button.__nekoIdleCat1Journey = {
    profile: { walkingSubstate: 'walking', idleSubstate: 'idle' }, substate: 'walking', frame: 7,
  };
  r.art.src = 'walk.gif';
  r.sample();
  assert.equal(r.art.src, 'idle.gif');
  assert.equal(state.frame, 0);
  assert.equal(r.journeyCancellations.length, 1);
  assert.equal(r.journeyCancellations[0].reason, 'window-gravity-moving');
  for (const name of ['_stepNekoIdleCat1Walk', '_startNekoIdleCat1Walk', '_scheduleNekoIdleCat1WalkStart',
    '_prepareNekoIdleCat1PairMoveStart', '_stepNekoIdleCat1PairMove', '_syncNekoIdleCat1Journey']) {
    r.context[name](r.button);
  }
  assert.equal(r.context._canScheduleNekoIdleCat1PairMove(r.button, state), false);
  assert.equal(r.journeySyncs, 0);
  r.settle();
  assert.equal(r.window.NekoDesktopWindowGravity.canWalk(r.button), true);
  assert.equal(r.journeySyncs, 1);
  r.sample();
  assert.equal(r.journeySyncs, 1, 'stationary samples must not restart actions');
  state.pairMovePlan = { dx: 100 }; state.pairMoveFrame = 5; r.art.src = 'walk.gif';
  r.advance(100); r.sample({ x: 200, y: 20, width: 600, height: 650 });
  assert.equal(state.pairMovePlan, null);
  assert.equal(state.pairMoveFrame, 0);
  assert.equal(r.art.src, 'idle.gif');
  assert.equal(r.window.NekoDesktopWindowGravity.canWalk(r.button), false);
  r.settle();
  assert.equal(r.journeySyncs, 2);
});

test('airborne journey checks clear delayed walks while preserving non-movement artwork', () => {
  for (const action of ['eat', 'stretch', 'play', 'sleep']) {
    const r = runtime(); installJourneyGravityBridge(r);
    r.button.__nekoIdleCat1Journey = {
      profile: { walkingSubstate: 'walking' }, substate: 'idle', pendingWalkTimer: 99,
    };
    r.art.src = `${action}.gif`;
    r.sample(); r.frame();
    assert.ok(r.state().y > 200);
    assert.equal(r.art.src, `${action}.gif`);
    assert.equal(r.button.__nekoIdleCat1Journey.pendingWalkTimer, 0);
    assert.equal(r.journeyCancellations[0].resetArt, false);
  }
});

test('a collision during a movement step cannot rearm its cancelled frame or report a completed move', () => {
  for (const finish of [false, true]) {
    const r = runtime(); installJourneyGravityBridge(r); r.sample(); r.settle(); r.sample();
    const plan = { container: r.container, chatMode: 'solo', durationMs: 1000 };
    const state = r.button.__nekoIdleCat1Journey = {
      profile: { walkingSubstate: 'walking' }, substate: 'idle', pairMovePlan: plan, pairMoveFrame: 0,
    };
    r.context._canNekoIdleCat1MoveSoloWithExpandedChat = () => true;
    r.context._applyNekoIdleCat1PairMovePlan = () => {
      r.advance(100); r.sample({ x: 200, y: 20, width: 600, height: 650 });
    };
    if (finish) r.context._finishNekoIdleCat1PairMove(r.button);
    else r.context._stepNekoIdleCat1PairMove(r.button, 1000, 1016);
    assert.equal(state.pairMovePlan, null);
    assert.equal(state.pairMoveFrame, 0);
    r.settle();
    assert.equal(r.window.NekoDesktopWindowGravity.canWalk(r.button), true);
  }
});

test('playground uses one gravity clock, preserves body impulses and shares moving window walls', () => {
  const r = runtime();
  const body = { id: 'cat', element: r.container, x: 300, y: 200, vx: 0, vy: 0,
    grounded: false, dragging: false, width: 122, height: 122 };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, bodies: new Map([['cat', body]]) };
  const control = runtime(); r.sample(); control.sample();
  for (let i = 0; i < 20; i++) {
    r.frame(); control.frame();
    assert.equal(r.window.NekoDesktopWindowGravity.stepBody(r.button, body, 1000 + (i + 1) * 16), true);
    assert.ok(Math.abs(r.state().y - control.state().y) < 0.01, 'no double gravity');
  }
  body.vx = 200; body.vy = -400;
  r.window.NekoDesktopWindowGravity.syncBody(body);
  r.frame(); assert.ok(r.state().vy < -350, 'retain toy/throw impulse');
  r.settle();
  assert.equal(body.grounded, true); assert.equal(body.floorY, r.state().bounds.bottom);
  r.sample(); r.advance(200); r.sample({ x: 200, y: 20, width: 600, height: 650 });
  assert.ok(body.vy < -400); assert.equal(body.vy, r.state().vy);
  contained(r.state());
});

test('the production playground loop delegates the contained cat and still simulates other bodies', () => {
  const r = runtime();
  const cat = { id: 'cat', element: r.container, x: 300, y: 200, vx: 0, vy: 0,
    grounded: false, width: 122, height: 122 };
  const yarn = { id: 'yarn', x: 450, y: 200, vx: 0, vy: 0, grounded: false, width: 50, height: 50 };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, token: 1, phase: 'dropping',
    bodies: new Map([['cat', cat], ['yarn', yarn]]), gravityPxPerSecond2: 2600, maxDeltaMs: 50, lastTickAt: 1000 };
  Object.assign(r.context, {
    _getNekoIdleCat1PlaygroundBodyVisibleInsetsPx: () => ({ left: 0, right: 0, bottom: 0 }),
    _getNekoIdleCat1PlaygroundViewportBottomPx: () => 900,
    _shouldNekoIdleCat1PlaygroundBodySettleRotation: () => false,
    _stepNekoIdleCat1PlaygroundBodyRotation: () => false,
    _stepNekoIdleCat1PlaygroundBodyRestRotation: () => false,
    _isNekoIdleCat1PlaygroundBodyRotating: () => false,
    _isNekoIdleCat1PlaygroundBodySettlingRotation: () => false,
    _isNekoIdleCat1PlaygroundBodyRestRotationPending: () => false,
    _resolveNekoIdleCat1PlaygroundBodyCollisions: () => false,
    _setNekoIdleCat1PlaygroundBodyPosition: () => {},
    _NEKO_IDLE_CAT1_PLAYGROUND_HORIZONTAL_DAMPING: 0.99,
    _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_DAMPING: 0.9,
    _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_STOP_VELOCITY_PX_PER_SEC: 2,
  });
  const source = fs.readFileSync(path.join(avatar, 'idle-playground.js'), 'utf8');
  for (const name of ['_stepNekoIdleCat1PlaygroundPhysics', '_updateNekoIdleCat1PlaygroundBodyBounds']) {
    const start = source.indexOf(`function ${name}(`);
    vm.runInContext(source.slice(start, source.indexOf('\n}', start) + 2), r.context);
  }
  r.sample();
  r.context._stepNekoIdleCat1PlaygroundPhysics(r.button, 1016);
  assert.ok(cat.y > 200);
  assert.ok(Math.abs(cat.vy - 1440 * 0.016) < 0.001);
  assert.ok(Math.abs(yarn.vy - 2600 * 0.016) < 0.001);
  assert.equal(cat.floorY, r.state().bounds.bottom);
  assert.equal(yarn.floorY, 850);
  cat.dragging = true;
  r.context._updateNekoIdleCat1PlaygroundBodyBounds(cat);
  assert.equal(Math.abs(cat.wallLeft), 0, 'the pointer must be able to drag a cat out of its window');
  assert.equal(cat.floorY, 778);
  cat.dragging = false;
  r.frame(16);
  assert.ok(Math.abs(cat.vy - 1440 * 0.016) < 0.001, 'same RAF time must not integrate twice');
});

test('switching playground hulls creates no shake and losing the window resumes its own physics', () => {
  const r = runtime(); r.sample(); r.settle(); r.sample(); r.advance(16);
  const body = { id: 'cat', element: r.container, x: r.state().x, y: r.state().y,
    vx: 0, vy: 0, grounded: true,
    visibleInsetRatios: { left: 0.2, right: 0.18, top: 0, bottom: 0 } };
  r.button.__nekoIdleCat1PlaygroundDropState = { active: true, bodies: new Map([['cat', body]]) };
  r.sample(); assert.equal(body.vy, 0, 'a different sprite hull is not a moving wall');
  let wakes = 0;
  r.context._startNekoIdleCat1PlaygroundPhysics = () => { wakes++; };
  r.sample(null);
  assert.equal(r.state().phase, 'idle');
  assert.equal(body.grounded, false);
  assert.equal(wakes, 1);
});

test('reduced motion keeps gravity while removing bounce deformation and rebound', () => {
  const r = runtime({ reduceMotion: true }); r.sample();
  for (let i = 0; i < 600 && r.resources().frames; i++) {
    r.frame(); assert.ok(r.state().vy >= 0);
    assert.equal(r.button.style.getPropertyValue('--neko-window-gravity-scale-y'), '1.0000');
  }
  assert.equal(r.state().phase, 'resting');
});

test('unload releases observers, frames and subscriptions; Web creates no gravity runner', () => {
  const r = runtime(); r.sample(); r.emit('pagehide');
  assert.equal(r.window.NekoDesktopWindowGravity, undefined);
  assert.deepEqual(r.resources(), { frames: 0, observers: 0, subscribers: 0 });
  const web = runtime({ web: true });
  assert.equal(web.window.NekoDesktopWindowGravity, undefined);
  assert.deepEqual(web.resources(), { frames: 0, observers: 0, subscribers: 0 });
});
