const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const avatar = path.join(root, 'static/avatar/avatar-ui-buttons');
const read = name => fs.readFileSync(path.join(avatar, name), 'utf8');

function functionSource(source, name) {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} is present`);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}

function classes() {
  const names = new Set();
  return {
    add: name => names.add(name), remove: name => names.delete(name),
    contains: name => names.has(name),
    toggle(name, value) { if (value) names.add(name); else names.delete(name); },
  };
}

// Exercise the real pointer producer, shared presentation consumer, rapid
// detector, stage controller and Cat Mind together. Only rendering, audio
// output and the separately tested physics integrator are isolated here.
async function harness(tier = 'cat1') {
  let now = 1000;
  let sequence = 0;
  const listeners = new Map(), timers = new Map(), intervals = new Map();
  const events = [], sounds = [], cancellations = [], positions = [], releases = [], eligibilitySyncs = [];
  const attrs = new Map([['data-neko-idle-tier', 'cat1']]);
  const containerAttrs = new Map();
  const art = {
    src: 'idle.cat1',
    getAttribute(name) { return this[name] || ''; },
    setAttribute(name, value) { this[name] = value; },
  };
  const container = {
    id: 'live2d-return-button-container', isConnected: true,
    classList: classes(), style: { display: 'block', left: '100px', top: '100px' },
    getAttribute: name => containerAttrs.get(name) || null,
    setAttribute: (name, value) => containerAttrs.set(name, value),
    removeAttribute: name => containerAttrs.delete(name),
    getBoundingClientRect: () => ({ left: 100, top: 100, width: 96, height: 96 }),
    setPointerCapture() {}, releasePointerCapture() {},
  };
  const button = {
    id: 'live2d-btn-return', isConnected: true, classList: classes(), style: {},
    getAttribute: name => attrs.get(name) || null,
    setAttribute: (name, value) => attrs.set(name, value),
    querySelector: () => art,
  };
  const window = {
    innerWidth: 1200, innerHeight: 900, screenX: 0, screenY: 0,
    location: { pathname: '/' }, localStorage: { getItem: () => null },
    appConst: {}, appState: { socket: { readyState: 1, send() {} } },
    live2dManager: { _goodbyeClicked: true },
    addEventListener(type, fn) {
      const bucket = listeners.get(type) || [];
      bucket.push(fn); listeners.set(type, bucket);
    },
    removeEventListener(type, fn) {
      listeners.set(type, (listeners.get(type) || []).filter(item => item !== fn));
    },
    dispatchEvent(event) {
      events.push(event);
      for (const fn of (listeners.get(event.type) || []).slice()) fn(event);
      return true;
    },
    requestAnimationFrame() { return ++sequence; }, cancelAnimationFrame() {},
    setTimeout(fn, delay = 0) { timers.set(++sequence, { fn, at: now + delay }); return sequence; },
    clearTimeout(id) { timers.delete(id); },
    setInterval(fn) { intervals.set(++sequence, fn); return sequence; },
    clearInterval(id) { intervals.delete(id); },
    NekoDesktopWindowGravity: {
      dragBody() {},
      releaseBody(_button, body, options) { releases.push({ id: body.id, ...options }); },
    },
  };
  const context = vm.createContext({
    window, console, Math, Date: { now: () => now }, performance: { now: () => now },
    CustomEvent: function(type, init = {}) { this.type = type; this.detail = init.detail || {}; },
    WebSocket: { OPEN: 1 },
    document: {
      readyState: 'complete', body: { classList: classes() },
      addEventListener() {}, querySelector: () => null,
      getElementById: id => id === 'resetSessionButton' ? {} : null,
      querySelectorAll: selector => selector === '.neko-idle-return-btn' || selector.includes('-btn-return') ? [button] : [],
    },
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    _getNekoCatAppearanceUrl: slot => slot,
    _getNekoIdleReturnButtonFromContainer: target => target === container ? button : null,
    _getNekoIdleReturnContainerFromButton: target => target === button ? container : null,
    _getNekoIdleReturnAssetVersionSuffix: () => '',
    _getNekoIdleReturnAssetUrl: value => `idle.${value}`,
    _normalizeNekoIdleReturnTier: value => value,
    _getNekoGoodbyeIdleAppearance: () => 'cat',
    _pickNekoIdleReturnDragAssetUrl: value => `drag.${value}`,
    _logNekoIdleReturnDragDebug() {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton() {},
    _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE: 'compact-top-edge',
    _NEKO_CAT_IDLE_OBSERVATION_TYPES: { RAPID_DRAG: 'rapid_drag' },
    _setNekoIdleReturnArtSource: (target, src) => { target.src = src; },
    _playNekoIdleCat1DragSound: value => sounds.push(`drag:${value}`),
    _playNekoIdleCat1RapidDragSound: value => sounds.push(`rapid:${value}`),
    _fadeOutNekoIdleCat1DragSound: () => sounds.push('fade'),
    _cancelNekoIdleCat1Journey: () => cancellations.push('journey'),
    _scheduleNekoIdleCat1JourneySyncForContainer: () => cancellations.push('journey-schedule'),
    _cancelNekoIdleCat1JourneyForContainer: () => cancellations.push('journey-container'),
    _isNekoIdleCat1EdgePeekActive: () => false,
    _updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove: () => ({ shouldSync: false }),
    _shouldRecheckNekoIdleCat1AfterManualMove: () => false,
    _forEachNekoIdleReturnButton: fn => fn(button),
    _dispatchNekoCatIdleObservationSource(type, detail) {
      window.dispatchEvent({ type: 'neko:cat-mind:observation', detail: {
        type, source: 'return-ball', tier: button.getAttribute('data-neko-idle-tier'),
        timestamp: now, detail,
      } });
    },
  });
  for (const name of ['Eat', 'Visual', 'Stretch', 'Play']) {
    context[`_cancelNekoIdleCat1${name}Action`] = () => cancellations.push(name.toLowerCase());
  }
  for (const match of read('core.js').matchAll(/^const (_NEKO_IDLE_(?:TIER|RETURN_(?:DRAG|BUTTON_SELECTOR)|CAT1_RAPID_DRAG|CAT1_PLAYGROUND)[A-Z0-9_]*) = (.+);$/gm)) {
    vm.runInContext(match[0], context);
  }
  const playground = read('idle-playground.js');
  vm.runInContext(playground.slice(0, playground.lastIndexOf("if (typeof window !== 'undefined')")), context);
  const assets = read('idle-assets-and-question.js');
  for (const name of ['_isNekoIdleThoughtBubbleEventTarget', '_isNekoIdleThoughtBubbleEventHit',
    '_isNekoIdlePresentationTransitionActive', '_isNekoIdleReturnPending', '_getNekoIdleSleepSoundConfig']) {
    vm.runInContext(functionSource(assets, name), context);
  }
  Object.assign(context, {
    _NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS: 'is-thought-bubble-active',
    _NEKO_IDLE_SLEEP_SOUND_BY_TIER: { cat2: { volume: 0.06 }, cat3: { volume: 0.06 } },
    isNekoIdleCatAudioEnabled: () => true,
    _nekoIdleCat1AmbientSoundState: { active: false, token: 0, audio: null },
    _nekoIdleSleepSoundState: { tier: 'none', token: 0, audio: null },
    // This harness records drag/rapid voice calls; generic response output and
    // results have their own complete-script tests in basic_interactions.
    _stopNekoIdleCat1AmbientSoundAudio() {}, _stopNekoIdleSleepSoundAudio() {},
    _syncNekoIdleCat1PlaygroundJourney() {},
  });
  for (const name of ['_stopNekoIdleCat1AmbientSound', '_syncNekoIdleCat1AmbientSoundForTier',
    '_stopNekoIdleSleepSound', '_syncNekoIdleSleepSoundForTier',
    '_isNekoIdleCat1EatActionActive', '_isNekoIdleCat1VisualActionActive', '_isNekoIdleCat1StretchActionActive',
    '_isNekoIdleCat1PlayActionActive',
    '_isNekoIdleCat1PlaygroundMoveActive',
    '_syncNekoIdleCat1PlaygroundStationaryActions', '_syncNekoIdleCat1PlaygroundBasicPresentation']) {
    vm.runInContext(functionSource(read('idle-actions-and-audio.js'), name), context);
  }
  const syncAmbient = context._syncNekoIdleCat1AmbientSoundForTier;
  context._syncNekoIdleCat1AmbientSoundForTier = value => { eligibilitySyncs.push(value); syncAmbient(value); };
  const drag = read('idle-drag-and-subactions.js');
  vm.runInContext(drag.slice(drag.indexOf('function _getNekoIdleReturnDragActionState('),
    drag.indexOf('function _getNekoIdleReturnSubactionProfile(')), context);
  const journey = read('idle-journey-and-presentation.js');
  vm.runInContext(functionSource(drag, '_getNekoIdleNowMs'), context);
  for (const name of ['_syncNekoIdleCat1PlaygroundMovementPause',
    '_syncNekoIdleCat1PlaygroundPointerMovementPause']) {
    vm.runInContext(functionSource(journey, name), context);
  }
  vm.runInContext(functionSource(journey, '_syncNekoIdleCat1PlaygroundSmallMove'), context);
  vm.runInContext(functionSource(journey, '_dispatchNekoIdleReturnBallManualMove'), context);
  vm.runInContext(functionSource(journey, '_getNekoIdleVisibleElementRect'), context);
  vm.runInContext(functionSource(journey, '_normalizeNekoIdleScreenRect'), context);
  const consumerStart = journey.indexOf("    window.addEventListener('neko:return-ball-manual-move'");
  vm.runInContext(journey.slice(consumerStart, journey.indexOf('\n    });', consumerStart) + 8), context);
  // Avoid integration or geometry stubs swallowing the real gesture contract.
  Object.assign(context, {
    _isNekoIdleCat1EdgePeekActive: () => false,
    _updateNekoIdleCat1PlaygroundBodyBounds() {},
    _startNekoIdleCat1PlaygroundPhysics() {},
    _setNekoIdleCat1PlaygroundBodyPosition(body, x, y) {
      body.x = x; body.y = y;
      body.element.style.left = `${x}px`; body.element.style.top = `${y}px`;
      positions.push({ x, y });
    },
    _syncNekoIdleReturnCurrentArt() {
      const currentTier = button.getAttribute('data-neko-idle-tier');
      art.src = context._isNekoIdleReturnDragActionActive(button)
        ? context._getNekoIdleReturnDragActionAssetUrl(button, currentTier) : `idle.${currentTier}`;
    },
    _getNekoIdleReturnCurrentArtUrl: () => `idle.${button.getAttribute('data-neko-idle-tier')}`,
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-auto-goodbye.js'), 'utf8'), context);
  await Promise.resolve(); await Promise.resolve();
  const tick = () => [...intervals.values()].forEach(fn => fn());
  tick();
  window.addEventListener('neko:auto-goodbye:state-change', event => {
    if (event.detail.type === 'visual-tier') button.setAttribute('data-neko-idle-tier', event.detail.tier);
  });
  if (tier !== 'cat1') {
    now += window.nekoAutoGoodbye.getState().thresholdsMs[tier];
    tick();
    assert.equal(window.nekoAutoGoodbye.getState().visualTier, tier);
  }
  button.setAttribute('data-neko-idle-tier', tier);
  vm.runInContext(fs.readFileSync(path.join(root, 'static/app/app-cat-mind.js'), 'utf8'), context);
  window.NekoCatMindActionProviders = { dryRun: () => ({ allowed: false, reason: 'test-no-actions' }) };
  window.dispatchEvent({ type: 'neko:cat-local-active-change', detail: { active: true, tier, appearance: 'cat', timestamp: now } });
  const state = context._getNekoIdleCat1PlaygroundDropState(button);
  const body = { id: 'cat', element: container, x: 100, y: 100, vx: 0, vy: 0,
    wallLeft: -1000, wallRight: 2000, ceilingY: -1000, floorY: 2000,
    grounded: true, dragging: false, rotationEnabled: false };
  Object.assign(state, { active: true, released: false, container, button, phase: 'settled', bodies: new Map([['cat', body]]) });
  events.length = sounds.length = cancellations.length = 0;
  const api = {
    window, context, state, body, button, art, container, events, sounds, cancellations, positions, releases, eligibilitySyncs,
    advance(ms) {
      now += ms;
      for (let pass = 0; pass < 20; pass++) {
        const ready = [...timers].filter(([, timer]) => timer.at <= now);
        if (!ready.length) return;
        for (const [id, timer] of ready) { if (timers.delete(id)) timer.fn(); }
      }
      throw new Error('timers must remain bounded');
    },
    down(id = 'cat', x = 100, y = 100) {
      const target = state.bodies.get(id);
      context._handleNekoIdleCat1PlaygroundPointerDownForBody(button, target, api.event(x, y));
    },
    move(x, y = 100) { context._handleNekoIdleCat1PlaygroundPointerMove(button, api.event(x, y)); },
    up(cancelled = false) { context._handleNekoIdleCat1PlaygroundPointerUp(button, { ...api.event(body.x, body.y), type: cancelled ? 'pointercancel' : 'pointerup' }); },
    event(x, y) { return { pointerId: 7, button: 0, clientX: x, clientY: y,
      screenX: x + window.screenX, screenY: y + window.screenY,
      timestamp: now, stopPropagation() {}, preventDefault() {} }; },
    facts() { return events.filter(event => event.type === 'neko:return-ball-manual-move').map(event => event.detail); },
    stage: () => window.nekoAutoGoodbye.getState(), tick,
  };
  return api;
}

test('Playground cat press stays pending at ordinary axis thresholds and interrupts once after real motion', async () => {
  const r = await harness();
  r.down();
  assert.equal(r.state.thoughtBubblePointerIds.size, 0, 'cat input leaves the separate bubble-pointer identities empty');
  assert.deepEqual(r.facts().map(fact => fact.reason), ['return-ball-drag-start']);
  assert.ok(r.container.classList.contains('is-drag-action-pending'));
  assert.deepEqual(r.cancellations, []);
  assert.deepEqual(r.sounds, []);
  r.advance(20); r.move(104, 104);
  r.advance(20); r.move(105, 105);
  assert.equal(r.state.draggingBodyId, '');
  assert.equal(r.positions.length, 0);
  assert.deepEqual(r.cancellations, []);
  r.advance(20); r.move(105.01, 105);
  assert.equal(r.state.draggingBodyId, 'cat');
  assert.deepEqual(r.cancellations.sort(), ['eat', 'play', 'stretch', 'visual']);
  assert.deepEqual(r.sounds, ['drag:cat1']);
  assert.equal(r.facts().filter(fact => fact.reason === 'return-ball-drag-active').length, 1);
  for (let i = 0; i < 4; i++) { r.advance(20); r.move(115 + i, 105); }
  assert.deepEqual(r.sounds, ['drag:cat1'], 'motion never restarts the drag sound');
  assert.equal(r.art.src, 'drag.cat1');
  r.up();
  assert.equal(r.sounds.filter(sound => sound === 'fade').length, 1);
  assert.equal(r.context._nekoIdleCat1AmbientSoundState.active, false,
    'the released airborne cat must wait for landing before regaining eligibility');
  assert.ok(!r.cancellations.some(reason => reason.startsWith('journey')), 'ordinary location side effects remain closed');
});

test('ordinary drag still owns its original journey cancellation and ambient restoration', async () => {
  const r = await harness();
  Object.assign(r.state, { active: false, released: true });
  const emit = reason => r.window.dispatchEvent({ type: 'neko:return-ball-manual-move', detail: {
    reason, container: r.container, activityId: 'ordinary-control',
    clientX: 100, clientY: 100, timestamp: 1000,
  } });
  emit('return-ball-drag-start');
  assert.ok(r.container.classList.contains('is-drag-action-pending'));
  assert.deepEqual(r.cancellations, []);
  emit('return-ball-drag-active');
  assert.deepEqual(r.cancellations, ['eat', 'visual', 'stretch', 'play', 'journey']);
  emit('return-ball-drag-end');
  assert.deepEqual(r.sounds, ['drag:cat1', 'fade']);
  assert.deepEqual(r.eligibilitySyncs, ['cat1']);
  assert.equal(r.context._nekoIdleCat1AmbientSoundState.active, true);
  assert.equal(r.art.src, 'idle.cat1');
});

test('cat activity carries one identity and full logical path through its unique terminal', async () => {
  const r = await harness();
  const initialMind = r.window.nekoCatMind.getState();
  r.down();
  r.advance(50); r.move(106, 108);
  r.advance(50); r.move(112, 108);
  r.advance(50); r.up(); r.up();
  const facts = r.facts(), end = facts.find(fact => fact.reason === 'return-ball-drag-end');
  assert.ok(end, 'a real release emits the common terminal');
  assert.ok(end.activityId);
  assert.ok(end.dragSessionId);
  assert.equal(new Set(facts.map(fact => fact.activityId)).size, 1);
  assert.equal(end.pathDistancePx, 16);
  assert.ok(Math.abs(end.displacementPx - Math.hypot(12, 8)) < 1e-9);
  assert.equal(end.durationMs, 150);
  assert.equal(end.dragCancelled, false);
  assert.equal(end.source, 'cat1-playground');
  assert.ok(facts.every(fact => fact.container === r.container));
  assert.equal(facts.filter(fact => fact.reason === 'return-ball-drag-end').length, 1);
  assert.deepEqual(r.releases, [{ id: 'cat', admit: true }]);
  const settled = r.window.nekoCatMind.getState();
  assert.equal(settled.recentCatInteractionTimes.length, 1);
  assert.ok(settled.fields.appetite > initialMind.fields.appetite);
  assert.ok(settled.fields.energy < initialMind.fields.energy);
  r.window.dispatchEvent({ type: 'neko:return-ball-manual-move', detail: end });
  assert.deepEqual(r.window.nekoCatMind.getState().fields, settled.fields);
  assert.deepEqual(r.window.nekoCatMind.getState().recentCatInteractionTimes, settled.recentCatInteractionTimes);
});

test('cat pending release is cancel-only and does not demote a sleeping cat', async () => {
  const r = await harness('cat2');
  r.down(); r.advance(50); r.move(104, 104); r.advance(50); r.up(); r.up();
  assert.equal(r.stage().visualTier, 'cat2');
  assert.deepEqual(r.facts().map(fact => fact.reason), ['return-ball-drag-start', 'return-ball-drag-cancel']);
  assert.deepEqual(r.sounds, []);
  assert.deepEqual(r.releases, [{ id: 'cat', admit: false }]);
});

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`real ${tier} drag terminal follows the existing stage demotion rules; cancel never counts as a release`, async () => {
    const r = await harness(tier);
    const drag = cancelled => {
      const x = r.body.x, y = r.body.y;
      r.down('cat', x, y); r.advance(20); r.move(x + 20, y + 10); r.advance(20); r.up(cancelled);
    };
    drag(true);
    assert.equal(r.stage().visualTier, tier);
    assert.equal(r.releases[0].admit, false);
    drag(false);
    assert.equal(r.stage().visualTier, tier === 'cat2' ? 'cat1' : tier);
    if (tier === 'cat3') { drag(false); assert.equal(r.stage().visualTier, 'cat2'); }
    assert.equal(r.state.active, true);
    assert.equal(r.state.bodies.get('cat'), r.body);
    assert.equal(r.releases.length, tier === 'cat3' ? 3 : 2);
  });
}

test('dragging non-cat objects preserves their radial threshold and produces no cat interaction', async () => {
  const r = await harness('cat2');
  const initialMind = r.window.nekoCatMind.getState();
  const yarn = { ...r.body, id: 'yarn' };
  r.state.bodies.set('yarn', yarn);
  r.down('yarn'); r.advance(20); r.move(104, 104);
  assert.equal(r.state.draggingBodyId, 'yarn', 'the old object threshold remains radial');
  r.advance(20); r.up();
  assert.deepEqual(r.facts(), []);
  assert.deepEqual(r.sounds, []);
  assert.deepEqual(r.cancellations, []);
  assert.equal(r.stage().visualTier, 'cat2');
  assert.deepEqual(r.releases, []);
  assert.deepEqual(r.window.nekoCatMind.getState().fields, initialMind.fields);
  assert.equal(r.window.nekoCatMind.getState().recentCatInteractionTimes.length, 0);
});

test('release after a natural tier change uses the existing current-tier controller rules', async () => {
  const r = await harness('cat2');
  r.down(); r.advance(20); r.move(120, 110);
  r.advance(r.stage().thresholdsMs.cat3 - r.stage().thresholdsMs.cat2);
  r.tick();
  assert.equal(r.stage().visualTier, 'cat3');
  assert.equal(r.state.draggingBodyId, 'cat');
  r.advance(20); r.up();
  assert.equal(r.stage().visualTier, 'cat3', 'first CAT3 release does not use the old CAT2 rule');
  assert.equal(r.facts().at(-1).tier, 'cat3');
  assert.equal(r.state.active, true);
});

test('logical motion facts preserve negative screen origins without multiplying by DPI', async () => {
  const r = await harness();
  Object.assign(r.window, { screenX: -1440, screenY: -900, devicePixelRatio: 2 });
  r.down(); r.advance(20); r.move(104, 104);
  assert.equal(r.state.draggingBodyId, '');
  r.advance(20); r.move(106, 108); r.advance(20); r.up();
  const terminal = r.facts().at(-1);
  assert.equal(terminal.pathDistancePx, 10);
  assert.equal(terminal.clientX, 106);
  assert.equal(terminal.clientY, 108);
  assert.equal(terminal.screenX, -1334);
  assert.equal(terminal.screenY, -792);
  assert.equal(r.body.x, 106);
  assert.equal(r.body.y, 108);
});

for (const tier of ['cat1', 'cat2', 'cat3']) {
  for (const dragging of [false, true]) {
    test(`${tier} lifecycle removal cancels a ${dragging ? 'real drag' : 'pending press'} exactly once and invalidates late callbacks`, async () => {
      const r = await harness(tier);
      r.down();
      if (dragging) { r.advance(20); r.move(160, 100); }
      r.advance(20);
      assert.equal(r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'container-removed'), true);
      assert.equal(r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'container-removed'), false);
      const terminal = r.facts().filter(fact => fact.reason === 'return-ball-drag-cancel');
      assert.equal(terminal.length, 1);
      assert.equal(terminal[0].dragCancelled, true);
      assert.equal(terminal[0].restoreArt, false);
      assert.equal(r.stage().visualTier, tier);
      assert.equal(r.state.active, false);
      assert.equal(r.state.bodies.size, 0);
      assert.equal(r.context._isNekoIdleReturnDragActionActive(r.button), false);
      const afterRemoval = r.sounds.slice();
      const eligibilityAfterRemoval = r.eligibilitySyncs.slice();
      r.up(); r.advance(6000);
      assert.deepEqual(r.sounds, afterRemoval);
      assert.equal(r.facts().filter(fact => fact.reason === 'return-ball-drag-cancel').length, 1);
      assert.equal(r.facts().filter(fact => fact.reason === 'return-ball-drag-end').length, 0);
      assert.deepEqual(r.eligibilitySyncs, eligibilityAfterRemoval);
      assert.equal(r.context._nekoIdleCat1AmbientSoundState.active, false);
      assert.equal(r.context._nekoIdleSleepSoundState.tier, 'none');
    });
  }
}

test('one rapid gesture uses the existing reaction cycle and counts demand feedback once', async () => {
  const r = await harness();
  r.down();
  for (let index = 0; index < 18; index++) {
    r.advance(80); r.move(index % 2 ? 100 : 220);
  }
  assert.equal(r.sounds.filter(sound => sound === 'rapid:cat1').length, 1);
  assert.equal(r.art.src, 'drag.rapid');
  const observations = () => r.events.filter(event => event.type === 'neko:cat-mind:observation' && event.detail.type === 'rapid_drag');
  assert.equal(observations().length, 1, 'rapid is not sent through both the observation and motion alias');
  const afterRapid = r.window.nekoCatMind.getState().fields;
  r.advance(11000);
  assert.equal(r.art.src, 'drag.cat1');
  for (let index = 0; index < 18; index++) { r.advance(80); r.move(index % 2 ? 100 : 220); }
  assert.equal(r.sounds.filter(sound => sound === 'rapid:cat1').length, 2, 'the existing reaction can play again after its cooldown');
  assert.equal(observations().length, 1, 'a long gesture never re-applies rapid feedback after the Cat Mind gesture timeout');
  assert.equal(r.window.nekoCatMind.getState().fields.social_need, afterRapid.social_need);
  assert.equal(r.window.nekoCatMind.getState().fields.stimulation_need, afterRapid.stimulation_need);
  r.up();
  assert.equal(r.sounds.filter(sound => sound === 'fade').length, 1);
  assert.deepEqual(r.releases, [{ id: 'cat', admit: true }]);
  const afterRelease = r.sounds.slice();
  r.advance(6000);
  assert.deepEqual(r.sounds, afterRelease, 'the old rapid timer never restarts sound after release');
});

test('ordinary compatibility drag without activityId records rapid once per gesture while preserving each reaction cycle', async () => {
  const r = await harness();
  Object.assign(r.state, { active: false, released: true });
  const emit = (reason, x = 100) => r.window.dispatchEvent({ type: 'neko:return-ball-manual-move', detail: {
    ...r.event(x, 100), reason, container: r.container,
  } });
  const rapid = () => {
    for (let index = 0; index < 18; index++) {
      r.advance(80);
      emit('return-ball-drag-motion', index % 2 ? 100 : 220);
    }
  };
  const observations = () => r.events.filter(event => event.type === 'neko:cat-mind:observation' && event.detail.type === 'rapid_drag');
  emit('return-ball-drag-start');
  emit('return-ball-drag-active');
  assert.ok(!r.button.__nekoIdleReturnDragActionState.activityId,
    'the compatibility producer supplies no identity to enable deduplication');
  rapid();
  assert.equal(observations().length, 1);
  assert.equal(r.art.src, 'drag.rapid');
  const afterRapid = r.window.nekoCatMind.getState().fields;
  r.advance(11000);
  assert.equal(r.art.src, 'drag.cat1');
  rapid();
  assert.equal(r.art.src, 'drag.rapid');
  assert.equal(r.sounds.filter(sound => sound === 'rapid:cat1').length, 2);
  assert.equal(observations().length, 1, 'the same long ordinary gesture contributes no second demand fact');
  assert.equal(r.window.nekoCatMind.getState().fields.social_need, afterRapid.social_need);
  assert.equal(r.window.nekoCatMind.getState().fields.stimulation_need, afterRapid.stimulation_need);
  emit('return-ball-drag-end');
  const afterRelease = r.sounds.slice();
  r.advance(6000);
  assert.deepEqual(r.sounds, afterRelease, 'the ordinary terminal invalidates the old reaction timer');
  emit('return-ball-drag-start');
  emit('return-ball-drag-active');
  rapid();
  assert.equal(observations().length, 2, 'a new ordinary gesture receives its own rapid feedback');
  assert.equal(r.sounds.filter(sound => sound === 'rapid:cat1').length, 3);
  emit('return-ball-drag-end');
});

test('synchronous release from the drag-active fact prevents the old move from overwriting restored ordinary position', async () => {
  const r = await harness();
  let released = false;
  r.window.addEventListener('neko:return-ball-manual-move', event => {
    if (event.detail.reason !== 'return-ball-drag-active') return;
    released = r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'synchronous-exit');
    Object.assign(r.container.style, { left: '500px', top: '400px' });
  });
  r.down(); r.advance(20); r.move(160, 120);
  assert.equal(released, true);
  assert.equal(r.state.active, false);
  assert.equal(r.state.bodies.size, 0);
  assert.equal(r.body.x, 100, 'a disposed body is not moved after the synchronous final exit');
  assert.equal(r.body.y, 100);
  assert.equal(r.positions.length, 0);
  assert.equal(r.container.style.left, '500px');
  assert.equal(r.container.style.top, '400px');
  assert.equal(r.container.getAttribute('data-neko-cat1-playground-dragging'), null);
  assert.equal(r.facts().filter(fact => fact.reason === 'return-ball-drag-cancel').length, 1);
});

test('synchronous cancel and new pointer session during drag-active lets only the new move continue', async () => {
  const r = await harness();
  let reentered = false, newSession, newActivityId;
  r.window.addEventListener('neko:return-ball-manual-move', event => {
    if (event.detail.reason !== 'return-ball-drag-active' || reentered) return;
    reentered = true;
    r.up(true);
    r.down('cat', 100, 100); r.advance(20); r.move(130, 110);
    newSession = r.state.pointerSampleSession;
    newActivityId = r.state.catDragActivity.activityId;
  });
  r.down();
  const oldSession = r.state.pointerSampleSession;
  const oldActivityId = r.facts()[0].activityId;
  r.advance(20); r.move(160, 120);
  assert.equal(reentered, true);
  assert.notEqual(newSession, oldSession);
  assert.equal(r.state.pointerSampleSession, newSession);
  assert.equal(r.state.catDragActivity.activityId, newActivityId);
  assert.notEqual(newActivityId, oldActivityId);
  assert.equal(r.state.active, true);
  assert.equal(r.state.draggingBodyId, 'cat');
  assert.deepEqual(r.positions, [{ x: 130, y: 110 }], 'the suspended old move cannot overwrite the new gesture position');
  assert.equal(r.body.x, 130);
  assert.equal(r.body.y, 110);
  assert.equal(r.container.getAttribute('data-neko-cat1-playground-dragging'), 'cat');
  assert.equal(r.facts().filter(fact => fact.activityId === oldActivityId && fact.reason === 'return-ball-drag-motion').length, 0);
  assert.equal(r.facts().filter(fact => fact.activityId === newActivityId && fact.reason === 'return-ball-drag-motion').length, 1);
});

for (const reenter of [false, true]) {
  test(`synchronous ${reenter ? 'cancel and new gesture' : 'lifecycle release'} during rapid observation cannot install an old timer or marker`, async () => {
    const r = await harness();
    let cancelled = false;
    r.window.addEventListener('neko:cat-mind:observation', event => {
      if (event.detail.type !== 'rapid_drag' || cancelled) return;
      cancelled = true;
      if (reenter) {
        r.up(true);
        const { x, y } = r.body;
        r.down('cat', x, y); r.advance(20); r.move(x + 20, y);
      } else {
        r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'synchronous-rapid-exit');
      }
    });
    r.down();
    for (let index = 0; index < 18 && !cancelled; index++) {
      r.advance(80); r.move(index % 2 ? 100 : 220);
    }
    assert.equal(cancelled, true);
    const drag = r.button.__nekoIdleReturnDragActionState;
    assert.equal(drag.rapidTimer, 0, 'the old activation cannot schedule after its token was invalidated');
    assert.equal(drag.rapidActive, false);
    assert.equal(drag.rapidObservationReported, false, 'the old observation cannot mark the terminal or new gesture');
    assert.equal(drag.active, reenter);
    const sounds = r.sounds.slice();
    r.advance(6000);
    assert.deepEqual(r.sounds, sounds);
  });
}
