const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const coordinatorPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-desktop-window-interactions.js'
);
const templatePath = path.join(projectRoot, 'templates/index.html');
const cssPath = path.join(projectRoot, 'static/css/index.css');
const journeyPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js'
);
const actionsPath = path.join(
  projectRoot,
  'static/avatar/avatar-ui-buttons/idle-actions-and-audio.js'
);

function sourceBetween(filePath, startMarker, endMarker) {
  const source = fs.readFileSync(filePath, 'utf8');
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, `${path.basename(filePath)} slice not found`);
  return source.slice(start, end);
}

class FakeClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const active = force === undefined ? !this.values.has(name) : !!force;
    if (active) this.values.add(name); else this.values.delete(name);
    return active;
  }
}

function createStyle(initial = {}) {
  return Object.assign({
    setProperty(name, value) { this[name] = String(value); },
    removeProperty(name) { delete this[name]; },
    getPropertyValue(name) { return this[name] || ''; },
  }, initial);
}

function createRuntime(options = {}) {
  const listeners = new Map();
  const sharedListeners = new Set();
  const rafs = new Map();
  const timers = new Map();
  const mutationObservers = new Set();
  const bodyChildren = [];
  const attributes = new Map([['data-neko-idle-tier', 'cat1']]);
  const containerAttributes = new Map();
  const artAttributes = new Map([['src', 'idle.gif']]);
  let appearance = options.appearance || 'cat';
  const recordedPositions = [];
  const rebasedPositions = [];
  let current = null;
  let now = 1000;
  let nextRaf = 1;
  let nextTimer = 1;

  function makeElement(tagName) {
    const elementAttributes = new Map();
    return {
      tagName: String(tagName).toUpperCase(),
      className: '',
      classList: new FakeClassList(),
      style: createStyle(),
      children: [],
      parentNode: null,
      appendChild(child) {
        child.parentNode = this;
        this.children.push(child);
        return child;
      },
      removeChild(child) {
        const index = this.children.indexOf(child);
        if (index >= 0) this.children.splice(index, 1);
        child.parentNode = null;
      },
      remove() { if (this.parentNode) this.parentNode.removeChild(this); },
      setAttribute(name, value) { elementAttributes.set(name, String(value)); },
      getAttribute(name) { return elementAttributes.get(name) || null; },
    };
  }

  const art = {
    src: 'idle.gif',
    __nekoIdleHoverSrc: '',
    getAttribute(name) { return artAttributes.get(name) || ''; },
    setAttribute(name, value) {
      artAttributes.set(name, String(value));
      if (name === 'src') this.src = String(value);
    },
  };
  const catParent = makeElement('div');
  const container = {
    id: 'live2d-return-button-container',
    isConnected: true,
    style: createStyle({
      display: 'block',
      left: `${options.catLeft ?? 100}px`,
      top: `${options.catTop ?? 300}px`,
      right: '',
      bottom: '',
      transform: 'none',
    }),
    classList: new FakeClassList(),
    parentNode: catParent,
    getAttribute(name) { return containerAttributes.get(name) || null; },
    setAttribute(name, value) { containerAttributes.set(name, String(value)); },
    removeAttribute(name) { containerAttributes.delete(name); },
    getBoundingClientRect() {
      const left = Number.parseFloat(this.style.left);
      const top = Number.parseFloat(this.style.top);
      const width = options.catWidth ?? 100;
      const height = options.catHeight ?? 100;
      return { left, top, right: left + width, bottom: top + height, width, height };
    },
  };
  const button = {
    id: 'live2d-btn-return',
    isConnected: true,
    classList: new FakeClassList(),
    style: createStyle(),
    getAttribute(name) { return attributes.get(name) || null; },
    setAttribute(name, value) { attributes.set(name, String(value)); },
    querySelector(selector) { return selector === '.neko-idle-return-art' ? art : null; },
  };
  button.__nekoIdleCat1Journey = {
    profile: { idleSubstate: 'idle', walkingSubstate: 'walking' },
    substate: 'walking',
  };
  let resumedJourneys = 0;
  const body = makeElement('body');
  body.children = bodyChildren;
  let doorMounts = 0;
  const appendBodyChild = body.appendChild.bind(body);
  body.appendChild = (child) => {
    doorMounts += 1;
    if (options.failDoorMount === true || doorMounts === options.failDoorMountAt) {
      throw new Error('door mount failed');
    }
    return appendBodyChild(child);
  }
  const shared = options.bridge === false ? null : {
    getCurrent: () => current,
    subscribe(listener) {
      sharedListeners.add(listener);
      return () => sharedListeners.delete(listener);
    },
  };
  const window = {
    innerWidth: options.innerWidth ?? 1000,
    innerHeight: options.innerHeight ?? 800,
    screenX: options.screenX ?? 0,
    screenY: options.screenY ?? 0,
    screenLeft: options.screenX ?? 0,
    screenTop: options.screenY ?? 0,
    devicePixelRatio: options.devicePixelRatio ?? 1,
    nekoDesktopWindowSensingContext: shared || undefined,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    dispatchEvent(event) {
      Array.from(listeners.get(event.type) || []).forEach((listener) => listener(event));
      return true;
    },
    requestAnimationFrame(callback) {
      const id = nextRaf++;
      rafs.set(id, callback);
      return id;
    },
    cancelAnimationFrame(id) { rafs.delete(id); },
    setTimeout(callback, delay) {
      const id = nextTimer++;
      timers.set(id, { callback, delay: Number(delay) || 0 });
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  };
  const document = {
    body,
    createElement: makeElement,
    querySelectorAll(selector) {
      return selector === '.neko-idle-return-btn' ? [button] : [];
    },
  };
  const context = {
    window,
    document,
    Math,
    Object,
    Number,
    Map,
    Set,
    String,
    Date: { now: () => now },
    performance: { now: () => now },
    _NEKO_IDLE_TIER_CAT1: 'cat1',
    _NEKO_GOODBYE_IDLE_APPEARANCE_CAT: 'cat',
    _getNekoGoodbyeIdleAppearance: () => appearance,
    _normalizeNekoIdleReturnTier: (tier) => tier || 'cat1',
    _getNekoIdleReturnContainerFromButton: (candidate) => candidate === button ? container : null,
    _scheduleNekoIdleCat1JourneySync: () => { resumedJourneys += 1; },
    _getNekoIdleReturnCurrentArtUrl: () => 'idle.gif',
    _getNekoIdleCat1WalkingAssetUrl: () => 'walk.gif',
    _setNekoIdleReturnArtSource: (candidate, src) => candidate.setAttribute('src', src),
    _getActiveNekoIdleReturnTier: () => 'cat1',
    _isNekoIdleReturnDragActionBlocking: () => false,
    _isAnyNekoIdleReturnDragActionBlocking: () => false,
    _isNekoIdleReturnPending: () => false,
    _isAnyNekoIdleReturnPending: () => false,
    _isNekoIdlePresentationTransitionActive: () => false,
    _isNekoIdleCompactSurfaceDragging: () => false,
    _isNekoIdleCat1EdgePeekActive: () => false,
    _isAnyNekoIdleCat1IndependentActionActive: () => options.independentActive === true,
    _isNekoIdleCat1PositionPresentationBusy: () => false,
    _isNekoIdleCat1PlaygroundEntryOrDropActive: () => false,
    MutationObserver: class MutationObserver {
      constructor(callback) { this.callback = callback; }
      observe() { mutationObservers.add(this); }
      disconnect() { mutationObservers.delete(this); }
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(coordinatorPath, 'utf8'), context);
  if (window.NekoDesktopWindowInteractions) {
    ['desktop-window-top-edge', 'desktop-window-edge-peek'].forEach((kind) => {
      window.NekoDesktopWindowInteractions.register({
        kind,
        priority: 0,
        handleSensingResult: () => false,
        getCandidate: () => null,
        startCandidate: () => false,
        consumeOpportunity: () => false,
        rearmOpportunity: () => false,
        isActive: () => false,
        cancel: () => false,
      });
    });
  }
  const startupCallbacks = Array.from(timers.values()).map((entry) => entry.callback);
  timers.clear();
  startupCallbacks.forEach((callback) => callback());

  return {
    context,
    window,
    button,
    container,
    art,
    bodyChildren,
    setShared(value) {
      current = value;
      Array.from(sharedListeners).forEach((listener) => listener(value));
    },
    setSharedSilently(value) { current = value; },
    startWalk(target = { left: 800, top: 300, kind: 'minimized-side' }, continuation = null) {
      return window.NekoDesktopWindowDoorWalk.tryStartWalk(button, target, continuation || {
        isCurrent() { return true; },
        canResume() { return true; },
        recordPosition(left, top) { recordedPositions.push({ left, top }); },
        rebasePosition(left, top) { rebasedPositions.push({ left, top }); },
        resume() { resumedJourneys += 1; },
      });
    },
    resumedJourneys() { return resumedJourneys; },
    recordedPositions() { return recordedPositions.slice(); },
    rebasedPositions() { return rebasedPositions.slice(); },
    setAppearance(value) { appearance = value; },
    stepFrame() {
      const callbacks = Array.from(rafs.values());
      rafs.clear();
      now += 48;
      callbacks.forEach((callback) => callback(now));
      return callbacks.length;
    },
    runUntil(predicate, limit = 1000) {
      let count = 0;
      while (!predicate() && rafs.size && count < limit) {
        this.stepFrame();
        count += 1;
      }
      return predicate();
    },
    flushRafs(limit = 1000) {
      let count = 0;
      while (rafs.size && count < limit) {
        this.stepFrame();
        count += 1;
      }
      return count;
    },
    emit(type, detail) { window.dispatchEvent({ type, detail }); },
    doorCount() { return bodyChildren.filter((child) => /neko-desktop-window-door/.test(child.className)).length; },
    doorSide() {
      const door = bodyChildren.find((child) => /neko-desktop-window-door\b/.test(child.className));
      const match = door && String(door.className).match(/\bis-(left|right|top|bottom)\b/);
      return match ? match[1] : '';
    },
    activeRafs() { return rafs.size; },
    activeTimers() { return timers.size; },
    activeObservers() { return mutationObservers.size; },
    removeContainer() {
      container.isConnected = false;
      Array.from(mutationObservers).forEach((observer) => observer.callback([]));
    },
  };
}

function sensingResult(revision, rect, extra = {}) {
  return Object.freeze({
    status: extra.status || (revision === 1 ? 'current' : 'changed'),
    sessionId: extra.sessionId || 'session-1',
    revision,
    changes: extra.changes || [],
    movement: extra.movement || null,
    rect: rect ? Object.freeze(rect) : undefined,
    reason: extra.reason,
  });
}

const horizontalWindow = Object.freeze({ x: 400, y: 200, width: 300, height: 300 });

test('door walk is desktop-only and stays inside the desktop interaction module', () => {
  const template = fs.readFileSync(templatePath, 'utf8');
  const source = fs.readFileSync(coordinatorPath, 'utf8');
  const css = fs.readFileSync(cssPath, 'utf8');
  const coordinatorIndex = template.indexOf('/static/avatar/avatar-ui-buttons/idle-desktop-window-interactions.js');

  assert.ok(coordinatorIndex >= 0);
  assert.doesNotMatch(template, /idle-desktop-window-door-walk\.js/);
  assert.match(source, /(?:TARGET_KIND|KIND)\s*=\s*'desktop-window-door-walk'/);
  assert.doesNotMatch(source, /NekoCatMind/);
  const journeySource = fs.readFileSync(
    path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-journey-and-presentation.js'),
    'utf8'
  );
  assert.match(journeySource, /function _tryStartNekoIdleCat1DoorWalk/);
  assert.match(journeySource, /_tryStartNekoIdleCat1DoorWalk\([\s\S]*?button,[\s\S]*?target/);
  const pairStepSource = sourceBetween(
    journeyPath,
    'function _stepNekoIdleCat1PairMove(button, startedAt, timestamp)',
    'function _startNekoIdleCat1PairMove'
  );
  assert.doesNotMatch(pairStepSource, /_tryStartNekoIdleCat1DoorWalk|solo-small-move/);
  const pairSource = sourceBetween(
    journeyPath,
    'function _prepareNekoIdleCat1PairMoveStart',
    'function _refreshNekoIdleCat1Observer'
  );
  assert.doesNotMatch(pairSource, /_tryStartNekoIdleCat1DoorWalk|NekoDesktopWindowDoorWalk/);
  const cancelSource = fs.readFileSync(
    path.join(projectRoot, 'static/avatar/avatar-ui-buttons/idle-drag-and-subactions.js'),
    'utf8'
  );
  assert.match(cancelSource, /function _cancelNekoIdleCat1Journey[\s\S]*?NekoDesktopWindowDoorWalk[\s\S]*?recover:\s*false/);
  assert.match(css, /\.neko-desktop-window-door/);
  assert.match(css, /is-cat1-desktop-window-door-clipped/);
  assert.match(css, /\.neko-desktop-window-door-layer\s*\{[^}]*display:\s*contents/);
  assert.match(css, /\.neko-desktop-window-door-opening\s*\{[^}]*z-index:\s*99998/);
  assert.match(css, /\.neko-desktop-window-door-frame\s*\{[^}]*z-index:\s*100000/);

  const web = createRuntime({ bridge: false });
  assert.equal(web.window.NekoDesktopWindowDoorWalk, undefined);
});

test('the door adapter only accepts the existing minimized-yarn walk', () => {
  let starts = 0;
  const context = {
    window: {
      NekoDesktopWindowDoorWalk: {
        tryStartWalk() {
          starts += 1;
          return true;
        },
      },
    },
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE: 'minimized-side',
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _tryStartNekoIdleCat1DoorWalk(button, target, continuation)',
    'function _getNekoIdleCat1JourneyDoorContinuation'
  ), context);

  assert.equal(context._tryStartNekoIdleCat1DoorWalk(
    {},
    { kind: 'compact-top-edge' },
    {}
  ), false);
  assert.equal(starts, 0);
  assert.equal(context._tryStartNekoIdleCat1DoorWalk(
    {},
    { kind: 'minimized-side' },
    {}
  ), true);
  assert.equal(starts, 1);
  assert.equal(context._tryStartNekoIdleCat1DoorWalk(
    {},
    { kind: 'ordinary-existing-walk' },
    {}
  ), false);
  assert.equal(starts, 1);
});

test('door ownership remains visible through the shared independent-action gate', () => {
  const context = {
    window: {
      NekoDesktopWindowInteractions: { isActive: () => false },
      NekoDesktopWindowTopEdgePerch: { isActive: () => false },
      NekoDesktopWindowEdgePeek: { isActive: () => false },
      NekoDesktopWindowDoorWalk: { isActive: () => true },
    },
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    actionsPath,
    'function _isNekoIdleDesktopWindowTopEdgeActionActive',
    'function _clearNekoIdleCat1PlayActionTimers'
  ), context);

  assert.equal(context._isNekoIdleDesktopWindowInteractionActionActive({}), true);
});

test('a new CAT1 minimized-yarn walk enters the ordinary step where the door is checked once', () => {
  const profile = { walkingSubstate: 'walking' };
  const state = { profile, substate: 'idle', frame: 0 };
  const container = { getAttribute: () => 'false' };
  const button = {};
  const target = { kind: 'minimized-side', left: 800, top: 300, distance: 700 };
  let ordinaryWalkSteps = 0;
  let substateFacingRight = null;
  const context = {
    window: {},
    performance: { now: () => 1000 },
    _getNekoIdleCat1Journey: () => state,
    _isNekoIdleCat1MovementAnchored: () => false,
    _isNekoIdleCat1EdgePeekActive: () => false,
    _isNekoIdleReturnDragActionActive: () => false,
    _isNekoIdleCat1IndependentActionActive: () => false,
    _getNekoIdleReturnContainerFromButton: () => container,
    _getNekoDesktopVirtualElementRect: () => ({ left: 100, top: 300, width: 100, height: 100 }),
    _resolveNekoIdleCat1TargetFacing: () => true,
    _beginNekoIdleCat1WalkActivity: () => {},
    _resetNekoIdleCat1WalkSpeed: () => {},
    _resetNekoIdleCat1WalkFinishResolution: () => {},
    _setNekoIdleCat1Substate: (_button, substate, options) => {
      state.substate = substate;
      substateFacingRight = options.facingRight;
    },
    _setNekoIdleCat1Classes: () => {},
    _dispatchNekoIdleCat1MotionInputRegionState: () => {},
    _stepNekoIdleCat1Walk: () => { ordinaryWalkSteps += 1; },
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _startNekoIdleCat1Walk(button, target)',
    'function _scheduleNekoIdleCat1WalkStart'
  ), context);

  context._startNekoIdleCat1Walk(button, target);
  assert.equal(ordinaryWalkSteps, 1);
  assert.equal(state.facingRight, true);
  assert.equal(substateFacingRight, true);
});

test('an existing pending walk frame remains the only door-check owner', () => {
  const profile = { walkingSubstate: 'walking' };
  const state = { profile, substate: 'walking', frame: 37, paused: false };
  const container = { getAttribute: () => 'false' };
  const button = {};
  const target = { kind: 'minimized-side', left: 800, top: 300, distance: 700 };
  let ordinaryWalkSteps = 0;
  const context = {
    window: {},
    performance: { now: () => 1000 },
    _getNekoIdleCat1Journey: () => state,
    _isNekoIdleCat1MovementAnchored: () => false,
    _isNekoIdleCat1EdgePeekActive: () => false,
    _isNekoIdleReturnDragActionActive: () => false,
    _isNekoIdleCat1IndependentActionActive: () => false,
    _getNekoIdleReturnContainerFromButton: () => container,
    _getNekoDesktopVirtualElementRect: () => ({ left: 100, top: 300, width: 100, height: 100 }),
    _resolveNekoIdleCat1TargetFacing: () => true,
    _setNekoIdleCat1Classes: () => {},
    _dispatchNekoIdleCat1MotionInputRegionState: () => {},
    _stepNekoIdleCat1Walk: () => { ordinaryWalkSteps += 1; },
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _startNekoIdleCat1Walk(button, target)',
    'function _scheduleNekoIdleCat1WalkStart'
  ), context);

  context._startNekoIdleCat1Walk(button, target);
  assert.equal(state.frame, 37);
  assert.equal(ordinaryWalkSteps, 0);
});

test('an existing walk retries the door on the next frame after the first check rejects', () => {
  const profile = {
    walkingSubstate: 'walking',
    target: {
      exitDistancePx: 14,
      minStepMs: 12,
      maxStepMs: 48,
      speedPxPerSec: 82,
    },
  };
  const state = {
    profile,
    substate: 'walking',
    paused: false,
    frame: 0,
    lastStepAt: 0,
  };
  const container = {};
  const button = {};
  const target = { kind: 'minimized-side', left: 800, top: 300, distance: 700 };
  let rectLeft = 100;
  let pendingFrame = null;
  let attempts = 0;
  let ordinarySteps = 0;
  const context = {
    window: {
      requestAnimationFrame(callback) {
        pendingFrame = callback;
        return 41;
      },
      cancelAnimationFrame() {},
    },
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _getNekoIdleCat1Journey: () => state,
    _getNekoIdleReturnContainerFromButton: () => container,
    _getNekoIdleChatMinimizedRect: () => ({}),
    _getNekoIdleCat1Target: () => target,
    _getNekoDesktopVirtualElementRect: () => ({ left: rectLeft, top: 300, width: 100, height: 100 }),
    _resolveNekoIdleCat1TargetFacing: () => true,
    _setNekoIdleCat1Classes: () => {},
    _getNekoIdleCat1JourneyDoorContinuation: () => ({}),
    _tryStartNekoIdleCat1DoorWalk: () => {
      attempts += 1;
      return attempts === 2;
    },
    _updateNekoIdleCat1WalkSpeedRate: () => 1,
    _appendNekoIdleCat1WalkActivityPoint: () => { ordinarySteps += 1; },
    _setNekoIdleCat1ContainerPosition: (_container, left) => { rectLeft = left; },
    _cancelNekoIdleCat1Journey: () => {},
    _finishNekoIdleCat1CompactTopEdgeWalk: () => {},
    _resolveNekoIdleCat1FinalTargetFacing: () => true,
    _finishNekoIdleCat1Walk: () => {},
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _stepNekoIdleCat1Walk(button, timestamp)',
    'function _startNekoIdleCat1Walk(button, target)'
  ), context);

  context._stepNekoIdleCat1Walk(button, 1000);
  assert.equal(attempts, 1);
  assert.equal(ordinarySteps, 1);
  assert.equal(typeof pendingFrame, 'function');

  pendingFrame(1048);
  assert.equal(attempts, 2);
  assert.equal(ordinarySteps, 1, 'the accepted retry must stop the ordinary walking frame');
  assert.equal(state.frame, 0);
});

test('the real door runner rechecks a changed blocking window on the next existing-walk frame', () => {
  const runtime = createRuntime();
  const target = { kind: 'minimized-side', left: 800, top: 300, distance: 700 };
  const surface = { left: 800, top: 300, width: 100, height: 100 };
  const state = runtime.button.__nekoIdleCat1Journey;
  Object.assign(state.profile, {
    target: {
      exitDistancePx: 14,
      minStepMs: 12,
      maxStepMs: 48,
      speedPxPerSec: 82,
    },
  });
  Object.assign(state, {
    target,
    targetKind: target.kind,
    paused: false,
    frame: 0,
    lastStepAt: 0,
  });
  Object.assign(runtime.context, {
    _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE: 'minimized-side',
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _getNekoIdleCat1Journey: () => state,
    _getNekoIdleChatMinimizedRect: () => surface,
    _getNekoIdleChatCompactSurfaceRect: () => null,
    _getNekoIdleCat1Target: () => target,
    _getNekoDesktopVirtualElementRect: (candidate) => candidate.getBoundingClientRect(),
    _resolveNekoIdleCat1TargetFacing: () => true,
    _setNekoIdleCat1Classes: () => {},
    _updateNekoIdleCat1WalkSpeedRate: () => 1,
    _appendNekoIdleCat1WalkActivityPoint: () => {},
    _rebaseNekoIdleCat1WalkActivity: () => {},
    _setNekoIdleCat1ContainerPosition: (_container, left, top) => {
      runtime.container.style.left = `${left}px`;
      runtime.container.style.top = `${top}px`;
    },
    _cancelNekoIdleCat1Journey: () => {},
    _finishNekoIdleCat1CompactTopEdgeWalk: () => {},
    _resolveNekoIdleCat1FinalTargetFacing: () => true,
    _finishNekoIdleCat1Walk: () => {},
  });
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _tryStartNekoIdleCat1DoorWalk(button, target, continuation)',
    'function _completeNekoIdleCat1WalkActivity'
  ), runtime.context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _stepNekoIdleCat1Walk(button, timestamp)',
    'function _startNekoIdleCat1Walk(button, target)'
  ), runtime.context);

  runtime.setSharedSilently(sensingResult(1, { x: 400, y: 600, width: 300, height: 150 }));
  runtime.context._stepNekoIdleCat1Walk(runtime.button, 1000);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.activeRafs(), 1);

  runtime.setSharedSilently(sensingResult(2, horizontalWindow, {
    status: 'changed',
    changes: ['position'],
  }));
  runtime.stepFrame();
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'walking-to-entry');
  assert.equal(state.frame, 0);
  assert.equal(runtime.doorCount(), 1);
});

test('journey continuation remains owned only while the same walk and target surface survive', () => {
  const profile = { walkingSubstate: 'walking' };
  const target = { kind: 'minimized-side', left: 800, top: 300 };
  const surface = { left: 820, top: 320, width: 64, height: 64 };
  const state = { profile, substate: 'walking', paused: false, target };
  const button = { __nekoIdleCat1Journey: state };
  let currentSurface = surface;
  let syncs = 0;
  const context = {
    Math,
    Number,
    window: {},
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE: 'minimized-side',
    _getNekoIdleChatMinimizedRect: () => currentSurface,
    _getNekoIdleChatCompactSurfaceRect: () => null,
    _appendNekoIdleCat1WalkActivityPoint: () => {},
    _rebaseNekoIdleCat1WalkActivity: () => {},
    _scheduleNekoIdleCat1JourneySync: () => { syncs += 1; },
  };
  vm.createContext(context);
  vm.runInContext(sourceBetween(
    journeyPath,
    'function _getNekoIdleCat1JourneyDoorContinuation(button, state)',
    'function _completeNekoIdleCat1WalkActivity'
  ), context);
  const continuation = context._getNekoIdleCat1JourneyDoorContinuation(button, state);

  assert.equal(continuation.isCurrent(target), true);
  currentSurface = { ...surface, left: surface.left + 20 };
  assert.equal(continuation.isCurrent(target), false);
  state.target = { ...target };
  assert.equal(continuation.canResume(), false);
  continuation.resume();
  assert.equal(syncs, 0);
});

test('a door mount failure returns false and leaves the original walk untouched', () => {
  const runtime = createRuntime({ failDoorMount: true });
  runtime.setShared(sensingResult(1, horizontalWindow));

  assert.equal(runtime.startWalk(), false);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.art.src, 'idle.gif');
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('door walk only accepts a complete existing-walk continuation', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.tryStartWalk(
    runtime.button,
    { left: 800, top: 300, kind: 'minimized-side' }
  ), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
  const source = fs.readFileSync(coordinatorPath, 'utf8');
  const doorSource = source.slice(source.indexOf('/**\n * Door walk'));
  assert.doesNotMatch(doorSource, /__nekoIdleReturnSubactionState|__nekoIdleCat1Journey/);
});

test('an exit door mount failure reveals the cat on the committed side and resumes once', () => {
  const runtime = createRuntime({ failDoorMountAt: 2 });
  runtime.setShared(sensingResult(1, horizontalWindow));
  assert.equal(runtime.startWalk(), true);
  runtime.flushRafs();

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
  assert.equal(runtime.resumedJourneys(), 1);
  assert.ok(Number.parseFloat(runtime.container.style.left) > horizontalWindow.x + horizontalWindow.width);
});

test('another independent action keeps ownership and the door does not start', () => {
  const runtime = createRuntime({ independentActive: true });
  runtime.setShared(sensingResult(1, horizontalWindow));
  assert.equal(runtime.startWalk(), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('a planned walk crosses one window, relocates only while hidden, then resumes its original journey', () => {
  const runtime = createRuntime();
  runtime.art.setAttribute('src', 'existing-walk.gif');
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();

  const first = runtime.window.NekoDesktopWindowDoorWalk.getState();
  assert.equal(first.phase, 'walking-to-entry');
  assert.equal(first.entrySide, 'left');
  assert.equal(first.exitSide, 'right');
  assert.equal(runtime.art.src, 'existing-walk.gif');
  assert.equal(runtime.doorCount(), 1);

  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'exiting'
  )), true);
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), true);
  assert.equal(runtime.doorCount(), 1);

  runtime.flushRafs();
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.art.src, 'existing-walk.gif');
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.activeRafs(), 0);
  assert.equal(runtime.resumedJourneys(), 1);
  assert.ok(Number.parseFloat(runtime.container.style.left) > horizontalWindow.x + horizontalWindow.width);
  assert.ok(runtime.recordedPositions().length > 0);
  assert.equal(runtime.rebasedPositions().length, 1);
  assert.ok(runtime.rebasedPositions()[0].left > horizontalWindow.x);
});

test('the resumed original walk reaches its existing target instead of starting a second door', () => {
  const runtime = createRuntime();
  const target = { left: 800, top: 300, kind: 'minimized-side' };
  let resumes = 0;
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk(target, {
    isCurrent: () => true,
    canResume: () => true,
    recordPosition() {},
    rebasePosition() {},
    resume() {
      resumes += 1;
      assert.equal(runtime.startWalk(target), false,
        'the cat is already on the target side, so this route no longer crosses the window');
      runtime.container.style.left = `${target.left}px`;
      runtime.container.style.top = `${target.top}px`;
    },
  });
  runtime.flushRafs();

  assert.equal(resumes, 1);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(Number.parseFloat(runtime.container.style.left), target.left);
  assert.equal(Number.parseFloat(runtime.container.style.top), target.top);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('window change while entering restores a complete cat on the entry side and removes the door', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);

  runtime.stepFrame();
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), true);
  runtime.setShared(sensingResult(2, { x: 420, y: 200, width: 300, height: 300 }, {
    changes: ['position'],
  }));

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
  assert.ok(Number.parseFloat(runtime.container.style.left) < horizontalWindow.x);
  assert.equal(runtime.startWalk(), true, 'the latest blocking window must be checked again');
});

test('window change while still walking to the entry cancels before any clipping starts', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'walking-to-entry');

  runtime.setShared(sensingResult(2, { x: 420, y: 200, width: 300, height: 300 }, {
    changes: ['position'],
  }));

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('replacement window at the same rectangle immediately cancels the locked door action', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);

  runtime.setShared(sensingResult(2, horizontalWindow, { changes: ['identity'] }));

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('hidden relocation rechecks a silently replaced same-rectangle window before moving the cat', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);

  runtime.setSharedSilently(sensingResult(2, horizontalWindow, { changes: ['identity'] }));
  runtime.flushRafs();

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.ok(Number.parseFloat(runtime.container.style.left) < horizontalWindow.x);
});

test('window close after hidden relocation restores a complete cat on the exit side without crossing back', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'exiting'
  )), true);

  runtime.setShared(sensingResult(2, null, {
    status: 'unavailable',
    reason: 'window-closed',
  }));

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.ok(Number.parseFloat(runtime.container.style.left) > horizontalWindow.x + horizontalWindow.width);
});

test('window loss restores the committed side and returns control to the original journey', () => {
  const beforeRelocation = createRuntime();
  beforeRelocation.setShared(sensingResult(1, horizontalWindow));
  beforeRelocation.startWalk();
  assert.equal(beforeRelocation.runUntil(() => (
    beforeRelocation.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);
  beforeRelocation.setShared(sensingResult(2, null, {
    status: 'unavailable',
    reason: 'window-closed',
  }));
  assert.equal(beforeRelocation.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.ok(Number.parseFloat(beforeRelocation.container.style.left) < horizontalWindow.x);
  assert.equal(beforeRelocation.resumedJourneys(), 1);

  const afterDoor = createRuntime();
  afterDoor.setShared(sensingResult(1, horizontalWindow));
  afterDoor.startWalk();
  assert.equal(afterDoor.runUntil(() => (
    afterDoor.window.NekoDesktopWindowDoorWalk.getState().phase === 'exiting'
  )), true);
  afterDoor.setShared(sensingResult(2, null, {
    status: 'unavailable',
    reason: 'window-closed',
  }));
  assert.equal(afterDoor.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.ok(Number.parseFloat(afterDoor.container.style.left) > horizontalWindow.x + horizontalWindow.width);
  assert.equal(afterDoor.resumedJourneys(), 1);
});

test('owner clear while hidden only removes door state and never resumes the old walk', () => {
  const runtime = createRuntime();
  let resumes = 0;
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk(undefined, {
    isCurrent: () => true,
    canResume: () => true,
    resume: () => { resumes += 1; },
  });
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'exiting'
  )), true);
  const leftBeforeClear = runtime.container.style.left;
  const topBeforeClear = runtime.container.style.top;

  runtime.setShared(null);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
  assert.equal(runtime.container.style.left, leftBeforeClear);
  assert.equal(runtime.container.style.top, topBeforeClear);
  assert.equal(resumes, 0);
});

test('drag-start during a partial door transition reveals the whole cat and leaves no owned animation', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);
  runtime.stepFrame();
  const leftBeforeDrag = runtime.container.style.left;
  const topBeforeDrag = runtime.container.style.top;

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start',
    container: runtime.container,
  });

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
  assert.equal(runtime.container.style.left, leftBeforeDrag);
  assert.equal(runtime.container.style.top, topBeforeDrag);
});

test('drag-cancel after revealing a door transition resumes the still-owned walk once', () => {
  const runtime = createRuntime();
  let resumes = 0;
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk(undefined, {
    isCurrent: () => true,
    canResume: () => true,
    resume: () => { resumes += 1; },
  });
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start',
    container: runtime.container,
  });
  assert.equal(resumes, 0);
  assert.equal(runtime.activeRafs(), 0);

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-cancel',
    container: runtime.container,
  });

  assert.equal(resumes, 1);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('a new owner clears a pending door drag resume', () => {
  const runtime = createRuntime();
  let resumes = 0;
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk(undefined, {
    isCurrent: () => true,
    canResume: () => true,
    resume: () => { resumes += 1; },
  });
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);

  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-start',
    container: runtime.container,
  });
  runtime.window.NekoDesktopWindowDoorWalk.cancel(runtime.button, {
    reason: 'return-click',
    recover: false,
  });
  runtime.emit('neko:return-ball-manual-move', {
    reason: 'return-ball-drag-cancel',
    container: runtime.container,
  });

  assert.equal(resumes, 0);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.activeRafs(), 0);
});

test('an invalidated original walk cleans the door without resuming the stale target', () => {
  const runtime = createRuntime();
  let current = true;
  let resumes = 0;
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk(undefined, {
    isCurrent: () => current,
    canResume: () => current,
    resume: () => { resumes += 1; },
  });
  current = false;
  runtime.stepFrame();

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
  assert.equal(resumes, 0);
});

test('the sensing owner stopping CAT1 clears a partially hidden cat immediately', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  assert.equal(runtime.runUntil(() => (
    runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
  )), true);
  runtime.stepFrame();

  runtime.setShared(null);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('playground, page unload, and container removal each clear clipping, doors, frames, and observers', () => {
  const cases = [
    (runtime) => runtime.emit('neko:idle-cat1-playground-state', { active: true }),
    (runtime) => runtime.emit('pagehide'),
    (runtime) => runtime.removeContainer(),
  ];
  cases.forEach((interrupt) => {
    const runtime = createRuntime();
    runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
    assert.equal(runtime.runUntil(() => (
      runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
    )), true);
    interrupt(runtime);

    if (runtime.window.NekoDesktopWindowDoorWalk) {
      assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
    }
    assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
    assert.equal(runtime.doorCount(), 0);
    assert.equal(runtime.activeRafs(), 0);
    assert.equal(runtime.activeObservers(), 0);
  });
});

test('top, bottom, and right starts use the matching straight crossing axis', () => {
  const cases = [
    { options: { catLeft: 800, catTop: 300 }, entry: 'right', exit: 'left' },
    { options: { catLeft: 450, catTop: 20 }, entry: 'top', exit: 'bottom' },
    { options: { catLeft: 450, catTop: 650 }, entry: 'bottom', exit: 'top' },
  ];
  cases.forEach((entry) => {
    const runtime = createRuntime(entry.options);
    runtime.setShared(sensingResult(1, horizontalWindow));
    const targets = {
      right: { left: 100, top: 300, kind: 'minimized-side' },
      top: { left: 450, top: 650, kind: 'minimized-side' },
      bottom: { left: 450, top: 20, kind: 'minimized-side' },
    };
    runtime.startWalk(targets[entry.entry]);
    const state = runtime.window.NekoDesktopWindowDoorWalk.getState();
    assert.equal(state.phase, 'walking-to-entry');
    assert.equal(state.entrySide, entry.entry);
    assert.equal(state.exitSide, entry.exit);
  });
});

test('a diagonal original route that crosses the window uses its actual entry and exit edges', () => {
  const runtime = createRuntime({ catLeft: 0, catTop: 0 });
  runtime.setShared(sensingResult(1, horizontalWindow));
  runtime.startWalk();
  const state = runtime.window.NekoDesktopWindowDoorWalk.getState();

  assert.equal(state.phase, 'walking-to-entry');
  assert.equal(state.entrySide, 'left');
});

test('a diagonal original route that misses the window remains an ordinary walk', () => {
  const runtime = createRuntime({ catLeft: 0, catTop: 0 });
  runtime.setShared(sensingResult(1, horizontalWindow));
  assert.equal(runtime.startWalk({ left: 800, top: 0, kind: 'minimized-side' }), false);
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
});

test('negative desktop origins use local DIP coordinates and ignore device pixel ratio', () => {
  const runtime = createRuntime({
    catLeft: 100,
    catTop: 300,
    screenX: -1000,
    screenY: -200,
    devicePixelRatio: 2,
  });
  runtime.setShared(sensingResult(1, { x: -600, y: 0, width: 300, height: 300 }));
  runtime.startWalk();
  const state = runtime.window.NekoDesktopWindowDoorWalk.getState();

  assert.equal(state.phase, 'walking-to-entry');
  assert.equal(state.entrySide, 'left');
  assert.equal(state.exitSide, 'right');
});

test('a window with no safe outside target does not start a door action', () => {
  const runtime = createRuntime({ catLeft: 0, catTop: 0, innerWidth: 800, innerHeight: 600 });
  runtime.setShared(sensingResult(1, { x: 100, y: 50, width: 650, height: 500 }));
  runtime.startWalk();

  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
  assert.equal(runtime.doorCount(), 0);
  assert.equal(runtime.activeRafs(), 0);
});

test('a thin window can still hide the clipped cat and use the opposite edge', () => {
  const horizontal = createRuntime({ catLeft: 100, catTop: 300 });
  horizontal.setShared(sensingResult(1, { x: 400, y: 200, width: 110, height: 300 }));
  horizontal.startWalk();
  assert.equal(horizontal.window.NekoDesktopWindowDoorWalk.getState().phase, 'walking-to-entry');

  const vertical = createRuntime({ catLeft: 450, catTop: 20 });
  vertical.setShared(sensingResult(1, { x: 400, y: 300, width: 300, height: 110 }));
  vertical.startWalk({ left: 450, top: 650, kind: 'minimized-side' });
  assert.equal(vertical.window.NekoDesktopWindowDoorWalk.getState().phase, 'walking-to-entry');
});

test('all opposite and adjacent entry-to-exit directions follow the original route', () => {
  const cases = [
    { start: [100, 300], target: [800, 300], entry: 'left', exit: 'right' },
    { start: [800, 300], target: [100, 300], entry: 'right', exit: 'left' },
    { start: [450, 20], target: [450, 650], entry: 'top', exit: 'bottom' },
    { start: [450, 650], target: [450, 20], entry: 'bottom', exit: 'top' },
    { start: [100, 300], target: [500, 650], entry: 'left', exit: 'bottom' },
    { start: [500, 650], target: [100, 300], entry: 'bottom', exit: 'left' },
    { start: [100, 300], target: [500, 20], entry: 'left', exit: 'top' },
    { start: [500, 20], target: [100, 300], entry: 'top', exit: 'left' },
    { start: [800, 300], target: [400, 650], entry: 'right', exit: 'bottom' },
    { start: [400, 650], target: [800, 300], entry: 'bottom', exit: 'right' },
    { start: [800, 300], target: [400, 20], entry: 'right', exit: 'top' },
    { start: [400, 20], target: [800, 300], entry: 'top', exit: 'right' },
  ];
  cases.forEach(({ start, target, entry, exit }) => {
    const runtime = createRuntime({ catLeft: start[0], catTop: start[1] });
    runtime.setShared(sensingResult(1, horizontalWindow));
    assert.equal(runtime.startWalk({
      left: target[0],
      top: target[1],
      kind: 'minimized-side',
    }), true, `${entry}->${exit}`);
    const state = runtime.window.NekoDesktopWindowDoorWalk.getState();
    assert.equal(state.entrySide, entry);
    assert.equal(state.exitSide, exit);
    assert.equal(runtime.doorSide(), entry, `${entry}->${exit} mounts the entry door on the entry edge`);

    assert.equal(runtime.runUntil(() => (
      runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'entering'
    )), true, `${entry}->${exit} reaches entry`);
    const enteringBefore = {
      left: Number.parseFloat(runtime.container.style.left),
      top: Number.parseFloat(runtime.container.style.top),
    };
    runtime.stepFrame();
    const enteringAfter = {
      left: Number.parseFloat(runtime.container.style.left),
      top: Number.parseFloat(runtime.container.style.top),
    };
    const entryDelta = entry === 'left'
      ? enteringAfter.left - enteringBefore.left
      : entry === 'right'
        ? enteringBefore.left - enteringAfter.left
        : entry === 'top'
          ? enteringAfter.top - enteringBefore.top
          : enteringBefore.top - enteringAfter.top;
    assert.ok(entryDelta > 0, `${entry}->${exit} moves inward from entry`);
    const enteringClip = runtime.button.style.getPropertyValue('--neko-desktop-window-door-clip');
    const clippedFromEntry = {
      left: /^inset\(0 (?!0(?:\.0+)?%)[\d.]+% 0 0\)$/,
      right: /^inset\(0 0 0 (?!0(?:\.0+)?%)[\d.]+%\)$/,
      top: /^inset\(0 0 (?!0(?:\.0+)?%)[\d.]+% 0\)$/,
      bottom: /^inset\((?!0(?:\.0+)?%)[\d.]+% 0 0 0\)$/,
    };
    assert.match(enteringClip, clippedFromEntry[entry], `${entry}->${exit} clips behind entry edge`);

    assert.equal(runtime.runUntil(() => (
      runtime.window.NekoDesktopWindowDoorWalk.getState().phase === 'exiting'
    )), true, `${entry}->${exit} relocates only after hiding`);
    assert.equal(runtime.doorSide(), exit, `${entry}->${exit} replaces it with the exit door on the exit edge`);
    const exitingBefore = {
      left: Number.parseFloat(runtime.container.style.left),
      top: Number.parseFloat(runtime.container.style.top),
    };
    runtime.stepFrame();
    const exitingAfter = {
      left: Number.parseFloat(runtime.container.style.left),
      top: Number.parseFloat(runtime.container.style.top),
    };
    const exitDelta = exit === 'left'
      ? exitingBefore.left - exitingAfter.left
      : exit === 'right'
        ? exitingAfter.left - exitingBefore.left
        : exit === 'top'
          ? exitingBefore.top - exitingAfter.top
          : exitingAfter.top - exitingBefore.top;
    assert.ok(exitDelta > 0, `${entry}->${exit} moves outward from exit`);
    const exitingClip = runtime.button.style.getPropertyValue('--neko-desktop-window-door-clip');
    const revealedFromExit = {
      left: /^inset\(0 (?!0(?:\.0+)?%)[\d.]+% 0 0\)$/,
      right: /^inset\(0 0 0 (?!0(?:\.0+)?%)[\d.]+%\)$/,
      top: /^inset\(0 0 (?!0(?:\.0+)?%)[\d.]+% 0\)$/,
      bottom: /^inset\((?!0(?:\.0+)?%)[\d.]+% 0 0 0\)$/,
    };
    assert.match(exitingClip, revealedFromExit[exit], `${entry}->${exit} reveals from exit edge`);

    runtime.flushRafs();
    assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');
    assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
    assert.equal(runtime.doorCount(), 0);
    assert.equal(runtime.resumedJourneys(), 1);
  });
});

test('sensing alone never starts a door and a later crossing walk may use the same window again', () => {
  const runtime = createRuntime();
  runtime.setShared(sensingResult(1, horizontalWindow));
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');

  runtime.startWalk();
  runtime.flushRafs();
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'idle');

  runtime.container.style.left = '800px';
  runtime.setShared(sensingResult(2, horizontalWindow, { status: 'current' }));
  runtime.startWalk({ left: 50, top: 300, kind: 'minimized-side' });
  assert.equal(runtime.window.NekoDesktopWindowDoorWalk.getState().phase, 'walking-to-entry');
});

test('repeated completion and cancellation leave no owned door nodes or animation frames', () => {
  const runtime = createRuntime();
  for (let revision = 1; revision <= 30; revision += 1) {
    runtime.container.style.left = '100px';
    runtime.container.style.top = '300px';
    const rect = { x: 400 + revision, y: 200, width: 300, height: 300 };
    runtime.setShared(sensingResult(revision, rect, {
      status: revision === 1 ? 'current' : 'changed',
      changes: revision === 1 ? [] : ['identity'],
    }));
    runtime.startWalk();
    assert.equal(runtime.window.NekoDesktopWindowDoorWalk.isActive(runtime.button), true);
    if (revision % 2 === 0) {
      runtime.window.NekoDesktopWindowDoorWalk.cancel(runtime.button, { reason: 'test-cancel' });
    } else {
      runtime.flushRafs();
    }
    assert.equal(runtime.doorCount(), 0);
    assert.equal(runtime.activeRafs(), 0);
    assert.equal(runtime.activeTimers(), 0);
    assert.equal(runtime.activeObservers(), 0);
    assert.equal(runtime.button.classList.contains('is-cat1-desktop-window-door-clipped'), false);
  }
});
