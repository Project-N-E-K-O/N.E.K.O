const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { runtime, assertPositionSynced } = require('./cat_playground_small_move.test.cjs');

function setup(options = {}) {
  const r = runtime('cat1', options);
  const yarn = r.state.bodies.get('yarn');
  if (options.playground === false || !yarn) return r;
  r.context._updateNekoIdleCat1PlaygroundBodyBounds(yarn);
  Object.assign(yarn, { x: 400, y: yarn.floorY, vx: 0, vy: 0, grounded: true });
  r.context._setNekoIdleCat1PlaygroundBodyPosition(yarn, yarn.x, yarn.y);
  const target = r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button);
  assert.ok(target, 'the real owner must provide a safe approach target');
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, target.left, target.top);
  return r;
}

function play(r, options = {}) {
  return r.context._playNekoIdleCat1PlayAction(r.button, {
    source: 'cat_mind', requestId: 'play-yarn-test', ...options,
  });
}
function playState(r) { return r.context._getNekoIdleCat1PlayActionState(r.button); }
function results(r) { return r.results().filter(value => value.actionId === 'cat1_play_yarn'); }
function asset(r) { return vm.runInContext('_NEKO_IDLE_CAT1_PLAY_ASSET_URL', r.context); }
function duration(r) { return r.duration(asset(r)); }
function visibilityMessages(r) {
  return r.events.filter(event => event.type === 'neko:idle-cat1-play-yarn-visibility');
}

test('PG play waits for the real image, keeps the full original GIF cycle and returns the same physical ball', async () => {
  const r = setup(), yarn = r.state.bodies.get('yarn'), phases = [];
  const before = { x: yarn.x, y: yarn.y, count: r.state.bodies.size, generation: r.state.yarnGeneration };
  assert.equal(play(r, { onAccepted: () => phases.push('accepted'), onStarted: () => phases.push('started') }), true);
  const lease = playState(r).playgroundYarnControl;
  assert.ok(lease); assert.equal(lease.yarnBody, yarn);
  assert.deepEqual(phases, ['accepted']); assert.equal(r.audios.length, 0);
  await r.advance(800);
  assert.equal(playState(r).timer, 0); assert.equal(results(r).length, 0);
  r.load(); await r.flush();
  assert.deepEqual(phases, ['accepted', 'started']);
  assert.ok(r.context._isNekoIdleAnimationRestartUrlForSource(r.art.src, asset(r)));
  await r.advance(duration(r) - 1); assert.equal(playState(r).active, true);
  await r.advance(1);
  assert.equal(playState(r).active, false); assert.equal(playState(r).playgroundYarnControl, null);
  assert.deepEqual(results(r).map(value => value.result), ['done']);
  assert.equal(r.state.bodies.get('yarn'), yarn); assert.equal(r.state.bodies.size, before.count);
  assert.equal(r.state.yarnGeneration, before.generation); assert.equal(yarn.x, before.x); assert.equal(yarn.y, before.y);
  assert.equal(r.state.active, true); assert.equal(r.state.released, false);
  assert.equal(visibilityMessages(r).length, 0, 'PG does not acquire the ordinary/native chat-ball visibility owner');
  assertPositionSynced(r);
});

test('real Playground physics frames preserve the active play art and held ball identity', async () => {
  const r = setup(), yarn = r.state.bodies.get('yarn');
  assert.equal(play(r), true); r.load(); await r.flush();
  const lease = playState(r).playgroundYarnControl, x = yarn.x, y = yarn.y;
  await r.runFrames(350);
  assert.equal(playState(r).active, true); assert.equal(playState(r).playgroundYarnControl, lease);
  assert.equal(r.state.bodies.get('yarn'), yarn); assert.equal(yarn.x, x); assert.equal(yarn.y, y);
  assert.equal(yarn.element.style.visibility, 'hidden');
  assert.ok(r.context._isNekoIdleAnimationRestartUrlForSource(r.art.src, asset(r)));
  assert.equal(results(r).length, 0); assertPositionSynced(r);
  r.context._cancelNekoIdleCat1PlayAction(r.button, {reason: 'test-end', animate: false});
  assert.notEqual(yarn.element.style.visibility, 'hidden');
});

for (const loaded of [false, true]) {
  test(`Visit cat-only returns the play ball styles before disposing its body (loaded: ${loaded})`, async () => {
    const r = setup(), yarn = r.state.bodies.get('yarn');
    r.context.document.removeEventListener = () => {};
    yarn.element.style.visibility = 'visible'; yarn.element.style.pointerEvents = 'auto';
    let replacementAccepted;
    r.window.addEventListener('neko:cat-mind:action-result', event => {
      if (event.detail.actionId === 'cat1_play_yarn') replacementAccepted = play(r);
    });
    assert.equal(play(r), true);
    if (loaded) { r.load(); await r.flush(); }
    assert.equal(yarn.element.style.visibility, 'hidden');
    assert.equal(r.context._switchNekoIdleCat1PlaygroundToVisitMode(), true);
    assert.equal(playState(r).active, false);
    assert.equal(yarn.actionControl, null);
    assert.equal(yarn.element.style.visibility, 'visible');
    assert.equal(yarn.element.style.pointerEvents, 'auto');
    assert.equal(r.state.bodies.has('yarn'), false);
    assert.equal(r.state.active, true);
    assert.equal(r.state.yarnEnabled, false);
    assert.equal(replacementAccepted, false, 'Visit handoff cannot acquire another play lease');
    r.load(); await r.advance(duration(r) + 100);
    assert.equal(results(r).length, 1);
    assert.notEqual(results(r)[0].result, 'done');
  });
}

for (const failure of ['error', 'timeout', 'started rejected']) {
  test(`PG play ${failure} restores its physical ball without a successful terminal`, async () => {
    const r = setup(), yarn = r.state.bodies.get('yarn'), phases = [];
    assert.equal(play(r, { onAccepted: () => phases.push('accepted'), onStarted: () => {
      phases.push('started'); return failure === 'started rejected' ? false : undefined;
    } }), true);
    if (failure === 'timeout') await r.advance(5000);
    else if (failure === 'error') { r.fail(); await r.flush(); }
    else { r.load(); await r.flush(); }
    assert.equal(playState(r).active, false); assert.equal(playState(r).sourceLoadCleanup, null);
    assert.equal(playState(r).entryTimer, 0); assert.equal(playState(r).timer, 0);
    assert.equal(playState(r).playgroundYarnControl, null); assert.equal(r.state.bodies.get('yarn'), yarn);
    assert.equal(results(r).length, 1); assert.notEqual(results(r)[0].result, 'done');
    assert.equal(results(r)[0].detail.durationMs, 0);
    assert.deepEqual(phases, failure === 'started rejected' ? ['accepted', 'started'] : ['accepted']);
    r.load(); await r.advance(duration(r) + 100);
    assert.equal(results(r).length, 1); assert.equal(r.audios.length, 0);
  });
}

for (const loaded of [false, true]) for (const [label, interrupt] of [
  ['cat dragging', r => { r.state.draggingBodyId = 'cat'; r.body.dragging = true; }],
  ['support lost', r => { r.body.grounded = false; }],
  ['tier changed', r => { r.button.setAttribute('data-neko-idle-tier', 'cat2'); }],
  ['ball removed', r => { r.state.bodies.delete('yarn'); }],
  ['ball replaced', r => { r.state.bodies.set('yarn', { ...r.state.bodies.get('yarn') }); }],
  ['session replaced', r => { r.button.__nekoIdleCat1PlaygroundDropState = { ...r.state }; }],
  ['exit', r => { r.state.released = true; r.state.releaseReason = 'test-exit'; }],
]) {
  test(`PG ${loaded ? 'playing' : 'loading'} ${label} cancels play once and old callbacks cannot restore a lost ball`, async () => {
    const r = setup(), yarn = r.state.bodies.get('yarn');
    assert.equal(play(r), true);
    if (loaded) { r.load(); await r.flush(); }
    const audio = r.audios.at(-1);
    interrupt(r); const currentBall = r.state.bodies.get('yarn');
    r.monitor(); await r.flush();
    assert.equal(playState(r).active, false); assert.equal(playState(r).playgroundYarnControl, null);
    assert.equal(results(r).length, 1); assert.notEqual(results(r)[0].result, 'done');
    assert.equal(r.state.bodies.get('yarn'), currentBall);
    if (currentBall === yarn && label !== 'session replaced') {
      assert.notEqual(yarn.element.style.visibility, 'hidden');
      assert.notEqual(yarn.element.style.pointerEvents, 'none');
    }
    if (audio) { audio.end(); assert.equal(audio.paused, true); }
    r.load(); await r.advance(duration(r) + 100);
    assert.equal(results(r).length, 1); assert.equal(r.state.bodies.get('yarn'), currentBall);
    if (label === 'ball replaced') assert.notEqual(currentBall, yarn);
  });
}

test('a routed ball pointer releases the play owner before the normal ball gesture takes over', async () => {
  const r = setup(), yarn = r.state.bodies.get('yarn');
  assert.equal(play(r), true); r.load(); await r.flush();
  assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, yarn, {
    pointerId: 51, clientX: yarn.x + 24, clientY: yarn.y + 24,
    button: 0, isPrimary: true, preventDefault() {}, stopPropagation() {},
  }), true);
  assert.equal(playState(r).active, false); assert.equal(yarn.actionControl, null);
  assert.equal(r.state.pointerBodyId, yarn.id); assert.equal(r.state.draggingBodyId, '');
  assert.notEqual(yarn.element.style.visibility, 'hidden');
  assert.equal(results(r).length, 1); assert.notEqual(results(r)[0].result, 'done');
  r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button, {type: 'pointercancel', pointerId: 51});
  assert.equal(r.state.bodies.get(yarn.id), yarn); assert.equal(results(r).length, 1);
});

test('a pending cat press preserves an existing PG play cycle and audio-off does not restart it', async () => {
  const r = setup(); assert.equal(play(r), true); r.load(); await r.flush();
  const token = playState(r).token, timer = playState(r).timer;
  r.state.pointerBodyId = 'cat'; r.body.dragging = true;
  r.monitor(); assert.equal(playState(r).active, true); assert.equal(playState(r).token, token);
  assert.equal(playState(r).timer, timer);
  r.context.setNekoIdleCatAudioEnabled(false); await r.flush();
  assert.equal(playState(r).active, true); assert.equal(playState(r).timer, timer);
  await r.advance(duration(r));
  assert.deepEqual(results(r).map(value => value.result), ['done']);
});

test('pending during PG play image loading keeps the accepted run and starts its full original cycle', async () => {
  const r = setup(), phases = [];
  assert.equal(play(r, { onAccepted: () => phases.push('accepted'), onStarted: () => phases.push('started') }), true);
  assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, r.body, {
    pointerId: 41, clientX: r.body.x + 48, clientY: r.body.y + 48,
    button: 0, isPrimary: true, preventDefault() {}, stopPropagation() {},
  }), true);
  await r.advance(700); r.load(); await r.flush();
  assert.deepEqual(phases, ['accepted', 'started']); assert.equal(playState(r).active, true);
  await r.advance(duration(r) - 1); assert.equal(results(r).length, 0);
  r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button, {type: 'pointercancel', pointerId: 41});
  await r.advance(1); assert.deepEqual(results(r).map(value => value.result), ['done']);
  assert.equal(playState(r).playgroundYarnControl, null);
});

for (const at of ['accepted', 'started']) {
  test(`PG ${at} callback may cancel and replace play without the old run owning the new lease`, async () => {
    const r = setup(); let replacement;
    const options = { [at === 'accepted' ? 'onAccepted' : 'onStarted']: () => {
      r.context._cancelNekoIdleCat1PlayAction(r.button, { reason: 'replace-play', animate: false });
      assert.equal(play(r, { requestId: 'replacement-play' }), true);
      replacement = playState(r).playgroundYarnControl;
      return false;
    } };
    assert.equal(play(r, options), true);
    if (at === 'started') { r.load(); await r.flush(); }
    assert.ok(replacement); assert.equal(playState(r).playgroundYarnControl, replacement);
    assert.equal(results(r).length, 1); assert.notEqual(results(r)[0].result, 'done');
    r.load(); await r.flush(); await r.advance(duration(r));
    assert.deepEqual(results(r).map(value => value.result), ['cancelled', 'done']);
    assert.equal(results(r).at(-1).detail.requestId, 'replacement-play');
  });
}

test('ordinary play keeps its immediate start, original ball visibility protocol and GIF completion', async () => {
  const r = setup({ playground: false }), phases = [];
  assert.equal(play(r, { onAccepted: () => phases.push('accepted'), onStarted: () => phases.push('started') }), true);
  await r.flush(); assert.deepEqual(phases, ['accepted', 'started']);
  assert.equal(playState(r).playgroundYarnControl, null); assert.equal(playState(r).entryTimer, 0);
  assert.equal(visibilityMessages(r)[0].detail.hidden, true);
  await r.advance(duration(r) - 1); assert.equal(playState(r).active, true);
  await r.advance(1); assert.deepEqual(results(r).map(value => value.result), ['done']);
  assert.equal(visibilityMessages(r).at(-1).detail.hidden, false);
});

test('catOnly and unsafe or distant PG balls cannot start the play runner', () => {
  const only = runtime('cat1', { catOnly: true }); assert.equal(play(only), false);
  const far = runtime('cat1'); assert.equal(play(far), false);
  const unstable = setup(); unstable.state.bodies.get('yarn').vx = 20;
  assert.equal(play(unstable), false); assert.equal(results(unstable).length, 0);
});

for (const handoff of ['replacement PG play', 'exit to ordinary play']) {
  test(`a social-result callback can hand off to ${handoff} before old PG play preparation completes`, async () => {
    const r = setup(), yarn = r.state.bodies.get('yarn');
    r.context._syncNekoIdleCat1AmbientSoundForTier('cat1');
    const voiceToken = vm.runInContext('_nekoIdleCat1AmbientSoundState.token', r.context);
    assert.equal(r.context._playNekoIdleCat1AmbientSound(voiceToken, {
      source: 'cat_mind', requestId: 'preparation-voice',
    }), true);
    await r.flush();
    let handedOff = false, replacementRun, replacementControl;
    r.window.addEventListener('neko:cat-mind:action-result', event => {
      if (handedOff || event.detail.actionId !== 'cat1_social_ping') return;
      handedOff = true;
      if (handoff === 'exit to ordinary play') {
        r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'test-owner-handoff');
      }
      assert.equal(play(r, { requestId: 'replacement-play' }), true);
      replacementRun = playState(r).catMindRunId;
      replacementControl = playState(r).playgroundYarnControl;
    });
    assert.equal(play(r, { requestId: 'superseded-play' }), false);
    assert.equal(handedOff, true);
    assert.equal(playState(r).active, true, 'old preparation cannot deactivate the replacement run');
    assert.equal(playState(r).catMindRunId, replacementRun);
    assert.equal(playState(r).playgroundYarnControl, replacementControl);
    if (handoff === 'replacement PG play') {
      assert.ok(replacementControl); assert.equal(yarn.actionControl, replacementControl);
      r.load(); await r.flush();
    } else {
      assert.equal(r.state.active, false); assert.equal(replacementControl, null);
      assert.equal(visibilityMessages(r).at(-1).detail.hidden, true);
    }
    await r.flush();
    await r.advance(duration(r));
    assert.equal(playState(r).active, false);
    assert.deepEqual(results(r).map(value => [value.result, value.detail.requestId]), [['done', 'replacement-play']]);
    assert.equal(yarn.actionControl || null, null);
    assert.notEqual(yarn.element.style.visibility, 'hidden');
    if (handoff === 'exit to ordinary play') assert.equal(visibilityMessages(r).at(-1).detail.hidden, false);
  });
}

test('an old yarn removal cannot dispose a new desktop generation installed by its terminal callback', async () => {
  const r = runtime(), old = r.state.bodies.get('yarn');
  // The VM host needs the standard document event-removal boundary when the
  // actual attach controller installs and retires its pointer listeners.
  r.context.document.removeEventListener = () => {};
  r.context._updateNekoIdleCat1PlaygroundBodyBounds(old);
  Object.assign(old, { x: 400, y: old.floorY, vx: 0, vy: 0, grounded: true, dragging: false });
  r.context._setNekoIdleCat1PlaygroundBodyPosition(old, old.x, old.y);
  const target = r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, target.left, target.top);
  let replacement, terminalCount = 0;
  assert.equal(r.context._playNekoIdleCat1PlayAction(r.button, { onTerminal() {
    terminalCount += 1;
    assert.equal(r.context._attachNekoIdleCat1PlaygroundYarnBody(r.button, {
      mode: 'desktop', rect: { left: 600, top: 800, width: 58, height: 58 },
      screenRect: { left: 600, top: 800, width: 58, height: 58 },
    }, { refresh: false }), true);
    replacement = r.state.bodies.get('desktop-yarn');
    assert.ok(replacement);
  } }), true);
  const oldLease = r.context._getNekoIdleCat1PlayActionState(r.button).playgroundYarnControl;
  await r.enter();
  r.context._removeNekoIdleCat1PlaygroundYarnBody(r.button);
  assert.equal(terminalCount, 1);
  assert.equal(r.state.bodies.has('yarn'), false);
  assert.equal(r.state.bodies.get('desktop-yarn'), replacement);
  assert.equal(r.state.targetMode, 'desktop');
  assert.equal(replacement.element.isConnected, true);
  assert.equal(r.context._releaseNekoIdleCat1PlaygroundYarnControl(r.button, oldLease), false);
  assert.equal(r.state.bodies.get('desktop-yarn'), replacement);
});

// Uses the action suite's existing setup/playState/results/duration helpers
// and the same production App/renderer runtime as the movement regressions.
const { attachRealMind } = require('./cat_playground_small_move.test.cjs');

function appPlayRuntime({ far = false } = {}) {
  const r = setup();
  if (far) r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 100, r.body.floorY);
  attachRealMind(r);
  r.random(.5);
  return r;
}

function offerPlayYarn(r) {
  r.window.nekoCatMind.observe({
    type: 'chat_yarn_drag_completed', tier: 'cat1', source: 'app-play-regression', timestamp: r.now(),
    detail: {
      userInitiated: true, startedFarFromCat: true, endedNearCat: true,
      startDistanceToCatPx: 320, endDistanceToCatPx: 20,
      directApproachDistancePx: 300, pathDistancePx: 310, movementThresholdPx: 24,
    },
  });
}

for (const terminal of ['done', 'cancel-before-load', 'cancel-after-start']) {
  test(`real selector and Cat Mind play protocol: ${terminal}`, async () => {
    const r = appPlayRuntime(), mind = r.window.nekoCatMind;
    offerPlayYarn(r);
    await r.advance(0);
    const accepted = mind.getState();
    assert.equal(accepted.pendingActionRequest.actionId, 'cat1_play_yarn');
    assert.ok(accepted.pendingActionRequest.runId);
    assert.equal(accepted.activeAction, null);
    assert.equal(accepted.actionCooldowns.cat1_play_yarn, undefined);
    assert.ok(mind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn);
    const fieldsBefore = accepted.fields;

    if (terminal !== 'cancel-before-load') {
      r.load(); await r.flush();
      const started = mind.getState();
      assert.equal(started.activeAction.actionId, 'cat1_play_yarn');
      assert.equal(started.pendingActionRequest, null);
      assert.equal(started.actionCooldowns.cat1_play_yarn.fullCooldownMs, 600000);
      assert.equal(mind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn, undefined);
    }
    if (terminal === 'done') await r.advance(duration(r));
    else r.context._cancelNekoIdleCat1PlayAction(r.button, { reason: 'test-cancel', animate: false });

    const ended = mind.getState(), endedDebug = mind.getDebugSnapshot();
    assert.equal(results(r).length, 1);
    assert.equal(ended.activeAction, null);
    assert.equal(ended.pendingActionRequest, null);
    if (terminal === 'done') {
      assert.equal(results(r)[0].result, 'done');
      assert.ok(ended.fields.energy < fieldsBefore.energy);
      assert.ok(endedDebug.returnEpisode.activeChapter.activityKinds.includes('cat1_play_yarn'));
    } else {
      assert.notEqual(results(r)[0].result, 'done');
      assert.deepEqual(ended.fields, fieldsBefore, 'cancellation earns no completion feedback');
      assert.ok(!endedDebug.returnEpisode.activeChapter.activityKinds.includes('cat1_play_yarn'));
      if (terminal === 'cancel-before-load') {
        assert.ok(endedDebug.actionIntentEvidence.cat1_play_yarn);
        assert.equal(ended.actionCooldowns.cat1_play_yarn, undefined);
      }
    }

    // This deliberately adds a second raw event; the real consumer must reject
    // its already settled run rather than apply the same five-dimensional dose.
    const settledFields = mind.getState().fields;
    r.window.dispatchEvent({ type: 'neko:cat-mind:action-result', detail: results(r)[0] });
    assert.deepEqual(mind.getState().fields, settledFields);
    r.load(); await r.flush();
    assert.equal(results(r).length, 2, 'one production terminal plus the explicitly injected replay only');
  });
}

for (const terminal of ['done', 'cancelled']) {
  test(`real PG journey handoff to local play: ${terminal}`, async () => {
    const r = appPlayRuntime({ far: true }), mind = r.window.nekoCatMind;
    offerPlayYarn(r);
    r.random(0); // Existing zero-delay approach and 25% local play branch.
    r.context._syncNekoIdleCat1Journey(r.button);
    assert.ok(r.journey.playgroundJourney);
    await r.enter();
    for (let index = 0; index < 800 && !r.context._isNekoIdleCat1PlayActionActive(r.button); index++) {
      await r.frame();
    }
    assert.equal(r.context._isNekoIdleCat1PlayActionActive(r.button), true);
    assert.equal(r.journey.playgroundJourney, null);
    assert.equal(r.journey.pairMovePlan, null);
    assert.equal(r.journey.walkFinishResolution, 'play');
    assert.equal(r.decision('cat1_play_yarn').allowed, false, 'the local runner retains exclusive presentation ownership');
    assert.equal(mind.getState().actionCooldowns.cat1_play_yarn, undefined);
    assert.ok(mind.getDebugSnapshot().actionIntentEvidence.cat1_play_yarn);
    const fieldsBefore = mind.getState().fields, token = playState(r).token;
    r.load(); await r.flush();
    if (terminal === 'done') await r.advance(duration(r));
    else r.context._cancelNekoIdleCat1PlayAction(r.button, { reason: 'test-local-cancel', animate: false });

    const settled = mind.getState(), settledDebug = mind.getDebugSnapshot();
    assert.equal(settled.actionCooldowns.cat1_play_yarn, undefined);
    assert.ok(!settledDebug.returnEpisode.activeChapter.activityKinds.includes('cat1_play_yarn'));
    if (terminal === 'done') {
      assert.ok(settled.fields.energy < fieldsBefore.energy);
      assert.equal(settledDebug.actionIntentEvidence.cat1_play_yarn, undefined);
    } else {
      assert.deepEqual(settled.fields, fieldsBefore);
      assert.ok(settledDebug.actionIntentEvidence.cat1_play_yarn);
    }
    r.context._finishNekoIdleCat1PlayAction(r.button, token);
    r.load(); await r.flush();
    assert.deepEqual(mind.getState().fields, settled.fields);
    const terminals = r.events.filter(event => event.type === 'neko:cat-mind:observation' &&
      event.detail.type === `cat1_local_play_${terminal}`);
    assert.equal(terminals.length, 1);
  });
}

test('play provider diagnostics read actual PG yarn facts without mutating geometry, random or scheduled work', () => {
  const r = appPlayRuntime(), yarn = r.state.bodies.get('yarn');
  const snapshot = () => JSON.stringify({
    cat: [r.body.x, r.body.y, r.body.vx, r.body.vy, r.body.grounded],
    yarn: [yarn.x, yarn.y, yarn.vx, yarn.vy, yarn.grounded],
    journey: [r.journey.substate, r.journey.targetKind, r.journey.actionSettled,
      r.journey.pendingWalkTimer, r.journey.frame],
    frames: r.frames.size, timers: r.timers.size, writes: r.writes.length, random: r.randomCalls(),
  });
  const before = snapshot();
  for (let index = 0; index < 10; index++) {
    const decision = r.decision('cat1_play_yarn');
    assert.equal(decision.allowed, true);
    assert.equal(decision.detail.facts.nearChat, true);
    assert.equal(decision.detail.facts.playYarnCapability, true);
    assert.ok(decision.detail.checks.filter(check => ['near_chat', 'play_yarn_capability'].includes(check.id))
      .every(check => check.passed));
  }
  assert.equal(snapshot(), before);
  yarn.vx = 20;
  const rejected = r.decision('cat1_play_yarn');
  assert.equal(rejected.allowed, false);
  assert.equal(rejected.detail.facts.playYarnCapability, false);
  assert.equal(rejected.detail.facts.nearChat, false);
});

for (const [label, change] of [
  ['hidden', yarn => { yarn.element.style.visibility = 'hidden'; }],
  ['rotating', yarn => { yarn.rotationEnabled = true; yarn.angularVelocity = 1; }],
]) {
  test(`an otherwise settled ${label} physical ball cannot grant a new play or pair target`, () => {
    const r = setup(), yarn = r.state.bodies.get('yarn');
    const before = { x: yarn.x, y: yarn.y, writes: r.writes.length, frames: r.frames.size, draws: r.randomCalls() };
    change(yarn);
    assert.equal(r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button), null);
    assert.equal(r.context._getNekoIdleCat1PlaygroundPairMoveTarget(r.button, {left: r.body.x + 150, top: r.body.y}), null);
    assert.equal(play(r), false);
    assert.deepEqual({x: yarn.x, y: yarn.y, writes: r.writes.length, frames: r.frames.size, draws: r.randomCalls()}, before);
  });
}
