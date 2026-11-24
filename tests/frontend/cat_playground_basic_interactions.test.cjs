const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const projectRoot = process.env.NEKO_TEST_SOURCE_ROOT || path.resolve(__dirname, '../..');
const avatar = path.join(projectRoot, 'static/avatar/avatar-ui-buttons');

function classList() {
  const names = new Set();
  return {
    add(...values) { values.forEach(value => names.add(value)); },
    remove(...values) { values.forEach(value => names.delete(value)); },
    contains: value => names.has(value),
    toggle(value, active) { if (active) names.add(value); else names.delete(value); },
  };
}

// Load the same scripts as the browser. Only DOM layout, physical integration
// and the external Audio output are isolated; capability, provider, audio-run
// ownership, hover/bubble and result logic remain the production functions.
function runtime(tier = 'cat1', { playground = true, catOnly = false } = {}) {
  let now = 1000, timerSequence = 0, frameSequence = 0;
  const timers = new Map(), frames = new Map(), listeners = new Map();
  const events = [], audios = [], writes = [];
  const controls = { audioEnabled: true, delayPlay: false, collisionActive: false };
  const makeElement = () => {
    const attrs = new Map();
    return {
      isConnected: true, classList: classList(), style: {}, dataset: {}, offsetWidth: 96,
      getAttribute: name => attrs.has(name) ? attrs.get(name) : null,
      setAttribute: (name, value) => attrs.set(name, String(value)),
      removeAttribute: name => attrs.delete(name), hasAttribute: name => attrs.has(name),
      addEventListener() {}, removeEventListener() {},
      appendChild(child) { child.parentNode = this; return child; },
      removeChild(child) { child.parentNode = null; return child; },
      getBoundingClientRect: () => ({ left: 100, top: 100, right: 196, bottom: 196, width: 96, height: 96 }),
    };
  };
  const container = makeElement(), button = makeElement(), art = makeElement();
  const bubble = makeElement(), bubbleBg = makeElement(), bubbleItem = makeElement();
  container.id = 'live2d-return-button-container'; button.id = 'live2d-btn-return';
  container.style.display = 'block';
  button.setAttribute('data-neko-idle-tier', tier);
  container.setAttribute('data-neko-idle-tier', tier);
  container.setAttribute('data-neko-return-visible', 'true');
  container.querySelector = () => button;
  button.querySelector = selector => ({
    '.neko-idle-return-art': art, '.neko-idle-thought-bubble': bubble,
    '.neko-idle-thought-bubble-bg': bubbleBg, '.neko-idle-thought-bubble-item': bubbleItem,
  })[selector] || null;
  button.closest = () => container; art.closest = () => button;
  Object.defineProperty(art, 'src', {
    get: () => art.getAttribute('src') || '',
    set(value) { art.setAttribute('src', value); writes.push(value); },
  });
  const window = {
    APP_VERSION: 'basic-interactions-test', innerWidth: 1200, innerHeight: 900,
    screenX: 0, screenY: 0, location: { href: 'http://neko.test/', pathname: '/' },
    localStorage: { getItem: key => key === 'neko.idleCatAudio.enabled' && !controls.audioEnabled ? 'false' : null },
    sessionStorage: { getItem: () => null },
    addEventListener(type, callback) {
      const group = listeners.get(type) || []; group.push(callback); listeners.set(type, group);
    },
    dispatchEvent(event) {
      events.push(event);
      for (const callback of listeners.get(event.type) || []) callback(event);
      return true;
    },
    requestAnimationFrame(callback) { frames.set(++frameSequence, callback); return frameSequence; },
    cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(callback, delay = 0) { timers.set(++timerSequence, { callback, at: now + delay }); return timerSequence; },
    clearTimeout(id) { timers.delete(id); },
    matchMedia: () => ({ matches: false }),
    getComputedStyle: element => ({
      display: element.style.display || 'block', visibility: element.style.visibility || 'visible',
      opacity: element.style.opacity ?? '1',
    }),
    NekoCollisionDeformation: { isCollisionDeformationActive: () => controls.collisionActive },
    Audio: class extends EventTarget {
      constructor(src) {
        super(); this.src = src; this.volume = 1; this.currentTime = 0;
        this.duration = 7.18; this.paused = false; this.ended = false;
        audios.push(this);
      }
      play() {
        if (!controls.delayPlay) return Promise.resolve();
        return new Promise((resolve, reject) => { this.resolvePlay = resolve; this.rejectPlay = reject; });
      }
      pause() { this.paused = true; }
    },
    Image: class { set src(value) { this._src = value; } },
  };
  const document = {
    hidden: false, visibilityState: 'visible', readyState: 'complete', body: makeElement(),
    getElementById: () => null, querySelector: () => null,
    querySelectorAll: selector => selector.includes('return') ? [button] : [],
    addEventListener() {}, createElement: makeElement,
  };
  const context = vm.createContext({
    window, document, console, Math, URL, Event, EventTarget,
    Date: { now: () => now }, performance: { now: () => now },
    CustomEvent: function(type, init = {}) { this.type = type; this.detail = init.detail || {}; },
    requestAnimationFrame: window.requestAnimationFrame, cancelAnimationFrame: window.cancelAnimationFrame,
    setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
  });
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
    _getNekoGoodbyeIdleAppearance: () => 'cat', _readNekoAutoGoodbyeVisualTier: () => button.getAttribute('data-neko-idle-tier'),
    _getNekoIdleReturnContainerFromButton: value => value === button ? container : null,
    _getNekoIdleReturnButtonFromArt: value => value === art ? button : null,
    _getNekoIdleReturnButtonFromContainer: value => value === container ? button : null,
    _forEachNekoIdleReturnButton: callback => callback(button),
    _isNekoIdleDesktopWindowInteractionActionActive: no,
    _isNekoIdleDesktopWindowTopEdgeActionActive: no,
    _isNekoIdleDesktopWindowEdgePeekActionActive: no,
    _isNekoIdleDesktopWindowDoorWalkActionActive: no,
    _isNekoIdleCat1EdgePeekActive: no,
    _isNekoIdleCompactSurfaceDragging: no,
    _getNekoIdleCat1PairMoveChatTarget: () => null,
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt() {},
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton() {},
    _scheduleNekoIdleCat1JourneySync() {},
    _getNekoIdleAnimationDurationMs: async () => 900,
    _syncNekoIdleCat1PlaygroundCatGeometry() {},
    _startNekoIdleCat1PlaygroundPhysics() {},
  });
  const state = context._getNekoIdleCat1PlaygroundDropState(button);
  const body = { id: 'cat', element: container, x: 100, y: 100, vx: 0, vy: 0,
    grounded: true, dragging: false, rotationEnabled: false };
  Object.assign(state, { active: playground, released: !playground, container, button,
    phase: 'settled', frame: 41, catOnly, yarnEnabled: !catOnly,
    disabledCapabilities: vm.runInContext('new Set(Object.keys(_NEKO_IDLE_CAT1_PLAYGROUND_CAPABILITIES)' +
      '.filter(key => _NEKO_IDLE_CAT1_PLAYGROUND_CAPABILITIES[key] === false))', context),
    bodies: new Map([['cat', body], ...(!catOnly ? [['yarn', { id: 'yarn', vx: 160, vy: 30, grounded: false }]] : [])]) });
  art.src = context._getNekoIdleReturnAssetUrl(tier); writes.length = 0;
  const soundState = () => vm.runInContext(tier === 'cat1'
    ? '_nekoIdleCat1AmbientSoundState' : '_nekoIdleSleepSoundState', context);
  const actionId = tier === 'cat1' ? 'cat1_social_ping' : tier === 'cat2' ? 'cat2_nap_feedback' : 'cat3_sleep_feedback';
  const api = {
    context, state, body, button, container, art, bubble, events, audios, controls, timers, frames, writes, actionId,
    soundState,
    sync() { context._syncNekoIdleCat1AmbientSoundForTier(tier); context._syncNekoIdleSleepSoundForTier(tier); },
    decision(id = actionId) { return context._dryRunNekoCatMindActionProvider(id, { button }); },
    play(options = {}) { return tier === 'cat1'
      ? context._playNekoIdleCat1AmbientSound(soundState().token, options)
      : context._playNekoIdleSleepSound(tier, soundState().token, options); },
    results: () => events.filter(event => event.type === 'neko:cat-mind:action-result').map(event => event.detail),
    async flush() { for (let i = 0; i < 6; i++) await Promise.resolve(); },
    advance(ms) {
      now += ms;
      for (let pass = 0; pass < 20; pass++) {
        const ready = [...timers].filter(([, timer]) => timer.at <= now);
        if (!ready.length) break;
        for (const [id, timer] of ready) { timers.delete(id); timer.callback(); }
      }
      const pending = [...frames]; frames.clear();
      for (const [, callback] of pending) callback(now);
    },
  };
  return api;
}

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: settled cat allows basic interactions while another object and the Playground RAF keep moving`, () => {
    const r = runtime(tier); r.sync();
    assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button), true, 'input ownership is retained');
    for (const capability of ['hover', 'idle-feedback', 'thought-bubble']) {
      assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, capability), false, capability);
    }
    assert.equal(r.state.frame, 41);
    assert.equal(r.state.bodies.get('yarn').grounded, false);
    assert.equal(r.decision().allowed, true);
    assert.equal(r.audios.length, 0, 'eligibility and dry-run never create audio');
  });
}

for (const [label, change] of [
  ['airborne', r => { r.body.grounded = false; }],
  ['sliding', r => { r.body.vx = 20; }],
  ['vertical motion', r => { r.body.vy = 20; }],
  ['collision deformation', r => { r.controls.collisionActive = true; }],
  ['pending cat press', r => { r.state.pointerBodyId = 'cat'; r.body.dragging = true; }],
  ['active cat drag', r => { r.state.pointerBodyId = 'cat'; r.state.draggingBodyId = 'cat'; r.body.dragging = true; }],
  ['entry pending', r => { r.button.__nekoIdleCat1PlaygroundPendingEntry = {}; }],
  ['return transition', r => { r.container.setAttribute('data-neko-model-cat-transitioning', 'cat-to-model'); }],
  ['art transition', r => { r.art.__nekoIdleTransitionTo = 'old-target.webp'; }],
]) {
  test(`new basic interactions reject ${label}`, () => {
    const r = runtime(); r.sync(); change(r);
    const before = JSON.stringify({ active: r.soundState().active, token: r.soundState().token,
      body: r.body, pointer: r.state.pointerBodyId }, (key, value) => key === 'element' ? undefined : value);
    assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, 'hover'), true);
    assert.equal(r.decision().allowed, false);
    assert.equal(r.play(), false, 'runner independently rechecks current conditions');
    assert.equal(r.audios.length, 0);
    assert.equal(JSON.stringify({ active: r.soundState().active, token: r.soundState().token,
      body: r.body, pointer: r.state.pointerBodyId }, (key, value) => key === 'element' ? undefined : value), before,
    'capability and provider checks are read-only');
  });
}

test('catOnly uses the same basic capability rules and does not manufacture other bodies', () => {
  const r = runtime('cat1', { catOnly: true }); r.sync();
  assert.equal(r.decision().allowed, true);
  assert.deepEqual([...r.state.bodies.keys()], ['cat']);
  assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, 'hover'), false);
});

test('stable Playground keeps unadapted action providers and ordinary input paths closed', () => {
  const r = runtime(); r.sync();
  for (const capability of ['ordinary-drag', 'compact-follow', 'dock-return-goodbye', 'edge-peek', 'unknown']) {
    assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, capability), true, capability);
  }
  for (const action of ['cat1_play_yarn']) {
    const decision = r.decision(action);
    assert.equal(decision.allowed, false, action);
    assert.equal(decision.reason, 'near_chat_unavailable', action);
  }
});

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: repeated qualification sync neither replays audio nor changes the enabled generation`, async () => {
    const r = runtime(tier); r.sync();
    const token = r.soundState().token;
    for (let i = 0; i < 5; i++) r.sync();
    assert.equal(r.soundState().token, token);
    assert.equal(r.audios.length, 0);
    assert.equal(r.play(), true);
    await r.flush();
    assert.equal(r.audios.length, 1);
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), true);
    const audio = r.audios[0]; r.sync(); await r.flush();
    assert.equal(r.soundState().audio, audio);
    assert.equal(r.audios.length, 1);
  });
}

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: provider allowance is rechecked by the runner if support disappears before start`, () => {
    const r = runtime(tier); r.sync();
    assert.equal(r.decision().allowed, true);
    r.body.grounded = false;
    assert.equal(r.play(), false);
    assert.equal(r.audios.length, 0);
    assert.deepEqual(r.results(), []);
  });
}

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: late audio start after movement cannot show a bubble or claim a successful start`, async () => {
    const r = runtime(tier); r.sync(); r.controls.delayPlay = true;
    let started = 0;
    assert.equal(r.play({ onStarted: () => started++ }), true);
    const audio = r.audios[0]; r.body.grounded = false;
    audio.resolvePlay(); await r.flush();
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), false);
    assert.equal(started, 0);
    assert.ok(r.results().every(result => result.result !== 'done'));
  });
}

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: a pending cat press rejects the first unconfirmed audio start`, async () => {
    const r = runtime(tier); r.sync(); r.controls.delayPlay = true;
    let started = 0;
    assert.equal(r.play({ onStarted: () => started++ }), true);
    const audio = r.audios[0];
    r.state.pointerBodyId = 'cat'; r.body.dragging = true;
    audio.resolvePlay(); await r.flush();
    assert.equal(started, 0);
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), false);
    assert.equal(audio.paused, true);
    assert.equal(r.results().length, 1);
    assert.notEqual(r.results()[0].result, 'done');
  });
}

for (const tier of ['cat1', 'cat2', 'cat3']) test(`${tier}: pending press preserves an already playing response while refusing another request`, async () => {
  const r = runtime(tier); r.sync();
  assert.equal(r.play(), true); await r.flush();
  const audio = r.audios[0], token = r.soundState().token;
  r.state.pointerBodyId = 'cat'; r.body.dragging = true;
  assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, 'idle-feedback', { allowPending: true }), false);
  assert.equal(r.decision().allowed, false);
  assert.equal(r.play(), false);
  assert.equal(r.soundState().audio, audio);
  assert.equal(r.soundState().token, token);
  assert.equal(audio.paused, false);
});

for (const tier of ['cat1', 'cat2', 'cat3']) {
  test(`${tier}: active movement cancels the current response once, then landing only restores eligibility`, async () => {
    const r = runtime(tier); r.sync();
    assert.equal(r.play(), true); await r.flush();
    const oldAudio = r.audios[0];
    r.state.draggingBodyId = 'cat'; r.state.pointerBodyId = 'cat'; r.body.dragging = true;
    r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    assert.equal(oldAudio.paused, true);
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), false);
    assert.equal(r.results().length, 1);
    assert.notEqual(r.results()[0].result, 'done');
    r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    oldAudio.dispatchEvent(new Event('ended'));
    assert.equal(r.results().length, 1, 'old callbacks cannot report a second terminal');
    r.state.draggingBodyId = ''; r.state.pointerBodyId = ''; r.body.dragging = false;
    r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    assert.equal(r.decision().allowed, true);
    assert.equal(r.audios.length, 1, 'landing does not immediately replay');
    assert.equal(r.results().length, 1);
  });

  test(`${tier}: pop keeps the original 540ms audio deadline before the existing CAT1 follow-up`, async () => {
    const r = runtime(tier); r.sync();
    assert.equal(r.play(), true); await r.flush();
    const audio = r.audios[0], initialVolume = audio.volume;
    assert.equal(r.context._popNekoIdleThoughtBubble(r.button), true);
    assert.equal(r.context._popNekoIdleThoughtBubble(r.button), false);
    r.advance(270);
    assert.ok(audio.volume > 0 && audio.volume < initialVolume);
    assert.equal(audio.paused, false);
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), true);
    r.advance(270);
    assert.equal(audio.paused, true);
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), false);
    assert.equal(r.soundState().audio, null);
    assert.equal(r.events.filter(event => event.type === 'neko:thought-bubble-pop').length, 1);
    assert.equal(r.results().length, 1);
    const followUps = [r.button.__nekoIdleCat1EatActionState, r.button.__nekoIdleCat1VisualActionState]
      .filter(state => state?.active);
    assert.equal(followUps.length, tier === 'cat1' ? 1 : 0);
  });
}

test('settled Playground hover keeps its owned source until its existing playback deadline', async () => {
  const r = runtime();
  r.context._playNekoIdleHoverArt(r.art, 'cat1', { userInitiated: true });
  const source = r.art.__nekoIdleHoverSrc;
  assert.ok(source);
  r.context._syncNekoIdleReturnCurrentArt(r.button);
  assert.equal(r.art.__nekoIdleHoverSrc, source);
  r.context._finishNekoIdleHoverArtAfterPlayback(r.art, 'cat1');
  await r.flush();
  r.advance(899);
  assert.equal(r.art.__nekoIdleHoverSrc, source);
  r.advance(1);
  assert.equal(r.art.__nekoIdleHoverSrc, '');
  assert.equal(r.art.src, r.context._getNekoIdleReturnAssetUrl('cat1'));
});

test('ordinary mode keeps hover and natural audio behavior without constructing a gravity body', async () => {
  const r = runtime('cat1', { playground: false }); r.state.bodies.clear();
  r.sync();
  assert.equal(r.decision().allowed, true);
  assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, 'hover'), false);
  r.context._playNekoIdleHoverArt(r.art, 'cat1', { userInitiated: true });
  assert.ok(r.art.__nekoIdleHoverSrc);
  assert.equal(r.play(), true); await r.flush();
  assert.equal(r.button.classList.contains('is-thought-bubble-active'), true);
  assert.equal(r.state.bodies.size, 0);
});

for (const [label, hide] of [
  ['display none', r => { r.container.style.display = 'none'; }],
  ['hidden visibility', r => { r.container.style.visibility = 'hidden'; }],
  ['transparent container', r => { r.container.style.opacity = '0'; }],
  ['hidden DOM element', r => { r.container.hidden = true; }],
  ['detached button', r => { r.button.isConnected = false; }],
  ['detached container', r => { r.container.isConnected = false; }],
  ['return visibility flag', r => { r.container.setAttribute('data-neko-return-visible', 'false'); }],
]) {
  test(`basic interactions reject ${label} through the shared capability check`, () => {
    const r = runtime(); r.sync(); hide(r);
    for (const capability of ['hover', 'idle-feedback', 'thought-bubble']) {
      assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button, capability), true);
    }
    assert.equal(r.decision().allowed, false);
    assert.equal(r.play(), false);
    assert.equal(r.audios.length, 0);
  });
}

test('cancelling a pending gravity entry restores only ordinary audio eligibility', async () => {
  const r = runtime('cat1', { playground: false }); r.state.bodies.clear(); r.sync();
  r.context._playNekoIdleHoverArt(r.art, 'cat1', { userInitiated: true });
  r.context._finishNekoIdleHoverArtAfterPlayback(r.art, 'cat1'); await r.flush();
  const before = { src: r.art.src, token: r.art.__nekoIdleHoverToken,
    timer: r.art.__nekoIdleHoverTimer, startedAt: r.art.__nekoIdleHoverStartedAt };
  let journeySyncs = 0; r.context._scheduleNekoIdleCat1JourneySync = () => { journeySyncs++; };
  r.button.__nekoIdleCat1PlaygroundPendingEntry = { frame: 0, timer: 0, restoreChatOnExit: false };
  r.context._syncNekoIdleCat1AmbientSoundForTier('cat1');
  assert.equal(r.soundState().active, false);
  r.context._setNekoIdleCat1GravityCatEnabled(false);
  assert.equal(r.button.__nekoIdleCat1PlaygroundPendingEntry, null);
  assert.equal(r.soundState().active, true);
  assert.deepEqual({ src: r.art.src, token: r.art.__nekoIdleHoverToken,
    timer: r.art.__nekoIdleHoverTimer, startedAt: r.art.__nekoIdleHoverStartedAt }, before);
  assert.equal(journeySyncs, 0);
  assert.equal(r.audios.length, 0);
});

for (const tier of ['cat2', 'cat3']) {
  test(`${tier}: real Cat Mind drag interruption enables its existing cadence recovery`, async () => {
    const r = runtime(tier); r.sync();
    vm.runInContext(fs.readFileSync(path.join(projectRoot, 'static/app/app-cat-mind.js'), 'utf8'), r.context);
    r.context.window.dispatchEvent(new r.context.CustomEvent('neko:cat-local-active-change', {
      detail: { active: true, tier, appearance: 'cat', timestamp: 1000 },
    }));
    r.advance(600000);
    r.context.window.nekoCatMind.observe({ type: 'cat_elapsed', source: 'cat-mind-clock', tier,
      timestamp: 601000, detail: { elapsedMs: 600000, inactiveElapsedMs: 600000, sinceLastActionMs: 600000 } });
    r.advance(1); await r.flush();
    const mind = r.context.window.nekoCatMind;
    assert.equal(mind.getState().activeAction?.actionId, r.actionId, 'real provider and runner must reach started');
    assert.equal(mind.getState().clock.dragInterruptionRecoveryActive, false);
    r.state.draggingBodyId = 'cat'; r.state.pointerBodyId = 'cat'; r.body.dragging = true;
    r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    assert.equal(r.results().length, 1);
    assert.equal(r.results()[0].result, 'interrupted');
    assert.equal(r.results()[0].reason, 'return-ball-drag-active');
    assert.equal(mind.getState().activeAction, null);
    assert.equal(mind.getState().clock.dragInterruptionRecoveryActive, true);
    assert.ok(mind.getRecentEvents().some(event => event.type === 'action_interrupted_by_drag'));
    r.audios[0].dispatchEvent(new Event('ended'));
    assert.equal(r.results().length, 1, 'late ended cannot replace interruption with done');
    r.state.draggingBodyId = ''; r.state.pointerBodyId = ''; r.body.dragging = false;
    r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    assert.equal(r.audios.length, 1, 'eligibility recovery never bypasses the existing cadence');
    assert.equal(mind.getState().clock.dragInterruptionRecoveryActive, true);
  });
}

for (const sameTarget of [false, true]) {
  test(`Basic sync preserves an owned hover through a ${sameTarget ? 'same-target' : 'stale different-target'} real art transition`, async () => {
    const r = runtime();
    r.context._playNekoIdleHoverArt(r.art, 'cat1', { userInitiated: true });
    r.context._finishNekoIdleHoverArtAfterPlayback(r.art, 'cat1'); await r.flush();
    const src = r.art.__nekoIdleHoverSrc;
    const before = { token: r.art.__nekoIdleHoverToken, timer: r.art.__nekoIdleHoverTimer,
      startedAt: r.art.__nekoIdleHoverStartedAt };
    if (sameTarget) r.art.src = r.context._buildNekoIdleAnimationRestartUrl(src, 40);
    r.context._setNekoIdleReturnArtSource(r.art,
      sameTarget ? src : r.context._getNekoIdleReturnAssetUrl('cat1'), 'cat1',
      { keepHoverPlayback: true, restartAnimation: sameTarget, playbackToken: 41 });
    const transitionTimer = r.art.__nekoIdleTransitionTimer;
    const transitionPreload = r.art.__nekoIdleTransitionPreload;
    assert.ok(transitionTimer, 'exercise the production transition timer');
    assert.equal(r.decision().allowed, false, 'a new action cannot borrow an existing hover transition');
    r.context._syncNekoIdleCat1PlaygroundBasicPresentation(r.button);
    assert.equal(r.art.__nekoIdleHoverSrc, src);
    assert.deepEqual({ token: r.art.__nekoIdleHoverToken, timer: r.art.__nekoIdleHoverTimer,
      startedAt: r.art.__nekoIdleHoverStartedAt }, before);
    if (sameTarget) {
      assert.equal(r.art.__nekoIdleTransitionTimer, transitionTimer);
      assert.equal(r.art.__nekoIdleTransitionPreload, transitionPreload);
      assert.equal(r.decision().allowed, false);
      r.advance(820); transitionPreload.onload(); await r.flush(); r.advance(79);
    } else {
      assert.equal(r.art.__nekoIdleTransitionTimer, 0);
      assert.equal(r.art.__nekoIdleTransitionTo, '');
      r.advance(899);
    }
    assert.equal(r.art.__nekoIdleHoverSrc, src);
    r.advance(1);
    assert.equal(r.art.__nekoIdleHoverSrc, '');
    assert.equal(r.art.src, r.context._getNekoIdleReturnAssetUrl('cat1'));
  });
}

for (const tier of ['cat1', 'cat2']) for (const phase of ['Accepted', 'Started']) {
  test(`${tier}: synchronous cancellation in on${phase} cannot revive old audio or bubbles`, async () => {
    const r = runtime(tier); r.sync();
    const stop = () => tier === 'cat1'
      ? r.context._stopNekoIdleCat1AmbientSound({ reason: 'return' })
      : r.context._stopNekoIdleSleepSound({ reason: 'return' });
    const options = { [`on${phase}`]: stop };
    assert.equal(r.play(options), phase === 'Started'); await r.flush();
    assert.equal(r.soundState().audio, null);
    assert.equal(r.audios.length, phase === 'Started' ? 1 : 0);
    assert.equal(r.button.classList.contains('is-thought-bubble-active'), false);
    assert.equal(r.results().length, 1);
    r.audios[0]?.dispatchEvent(new Event('ended'));
    assert.equal(r.results().length, 1);
  });
}
