const test = require('node:test');
const assert = require('node:assert/strict');
const { runtime, requestMove, moveResults, ownerSnapshot, assertPositionSynced } =
  require('./cat_playground_small_move.test.cjs');

function settleYarn(r, left = 600) {
  const yarn = r.state.bodies.get('yarn') || r.state.bodies.get('desktop-yarn');
  r.context._updateNekoIdleCat1PlaygroundBodyBounds(yarn);
  Object.assign(yarn, { vx: 0, vy: 0, angularVelocity: 0, grounded: true, dragging: false });
  r.context._setNekoIdleCat1PlaygroundBodyPosition(yarn, left, yarn.floorY);
  return yarn;
}

for (const replaced of [false, true]) {
  test(`a stale Playground journey RAF retires only its own handle (replaced: ${replaced})`, () => {
    const r = runtime();
    r.context._scheduleNekoIdleCat1JourneySync(r.button);
    const frame = r.journey.syncFrame;
    assert.ok(frame);
    const callback = r.frames.get(frame);
    assert.equal(typeof callback, 'function');
    r.state.token += 1;
    if (replaced) r.journey.syncFrame = 9999;
    callback(r.now());
    assert.equal(r.journey.syncFrame, replaced ? 9999 : 0);
  });
}

function walkFacts(r) {
  return r.events.filter(event => event.type === 'neko:cat-mind:observation' &&
    event.detail.type === 'cat1_walk_done_near_chat').map(event => event.detail.detail);
}

function startJourney(r) {
  r.random(0); // Original zero-delay choice; the movement implementation is real.
  r.context._syncNekoIdleCat1Journey(r.button);
  assert.ok(r.journey.playgroundJourney);
  return r.journey.playgroundJourney;
}

for (const direction of ['right', 'left']) {
  test(`Playground walks ${direction} to the real ball, then resolves one original local tail`, async () => {
    const r = runtime();
    if (direction === 'left') r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 800, r.body.floorY);
    const yarn = settleYarn(r, direction === 'right' ? 600 : 350);
    const initialX = r.body.x, yarnX = yarn.x;
    const approach = startJourney(r);
    await r.runFrames(300);
    assert.equal(r.body.x, initialX, 'assigning an image is not a walking first frame');
    assert.equal(r.journey.walkActivity, null);
    await r.enter();
    r.random(.9); // Existing local result chooses stretch.
    await r.runFrames(7000);
    assert.equal(r.journey.playgroundJourney, null);
    assert.equal(r.journey.walkFinishResolution, 'stretch');
    assert.equal(r.stretchState().active, true);
    assert.equal(yarn.x, yarnX, 'walking to a ball never moves that ball');
    assert.equal(walkFacts(r).length, 1);
    const facts = walkFacts(r)[0];
    assert.ok(Math.abs(facts.pathDistancePx - Math.abs(r.body.x - initialX)) < .01);
    assert.ok(facts.pathDistancePx > 200);
    assert.ok(facts.durationMs < 7000, 'image waiting is excluded');
    assert.equal(r.state.bodies.get('cat'), approach.catBody);
    assert.equal(r.state.active, true);
    r.context._finishNekoIdleCat1Walk(r.button);
    assert.equal(walkFacts(r).length, 1, 'a duplicate finish cannot repeat the local resolution');
    assertPositionSynced(r);
  });
}

test('journey preserves the original weighted start delay and does not show walking early', async () => {
  const r = runtime(); settleYarn(r);
  r.random(.75);
  r.context._syncNekoIdleCat1Journey(r.button);
  const approach = r.journey.playgroundJourney, timer = r.journey.pendingWalkTimer;
  assert.ok(approach); assert.ok(timer);
  assert.equal(approach.loading, false);
  const before = ownerSnapshot(r), source = r.art.src;
  r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
  assert.equal(r.journey.pendingWalkTimer, timer, 'rechecking physics does not re-roll the delay');
  await r.advance(r.journey.pendingWalkDelayMs - 1);
  assert.equal(r.art.src, source); assert.deepEqual(ownerSnapshot(r), before);
  await r.advance(1);
  assert.equal(approach.loading, true);
  assert.equal(r.journey.playgroundJourney, approach);
});

for (const interruptedBy of ['drag', 'tier', 'exit', 'ball-removed', 'ball-moving', 'external-cat-motion']) {
  test(`walking interruption by ${interruptedBy} settles the actual partial path once`, async () => {
    const r = runtime(); const yarn = settleYarn(r);
    const approach = startJourney(r); await r.enter(); await r.runFrames(500);
    const actualPath = r.journey.walkActivity.pathDistancePx;
    assert.ok(actualPath > 0);
    const staleFrame = r.frames.get(r.journey.frame);
    if (interruptedBy === 'drag') {
      r.context._cancelNekoIdleCat1Journey(r.button, { reason: 'return-ball-drag-active', resetArt: true });
    } else if (interruptedBy === 'tier') {
      r.button.setAttribute('data-neko-idle-tier', 'cat2');
      r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    } else if (interruptedBy === 'exit') {
      r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'test-exit');
    } else if (interruptedBy === 'ball-removed') {
      r.state.bodies.delete(yarn.id);
      r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    } else if (interruptedBy === 'ball-moving') {
      yarn.vx = 70;
      r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    } else {
      r.body.x += 20;
      r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    }
    assert.equal(r.journey.playgroundJourney, null);
    assert.equal(walkFacts(r).length, 1);
    assert.equal(walkFacts(r)[0].pathDistancePx, actualPath);
    assert.equal(walkFacts(r)[0].completed, false);
    staleFrame(r.now() + 10000); r.load(); await r.flush();
    assert.notEqual(r.journey.playgroundJourney, approach);
    assert.equal(walkFacts(r).length, 1);
  });
}

for (const phase of ['loading', 'walking']) {
  test(`a stationary cat press during ${phase} freezes the original approach and excludes held time`, async () => {
    const r = runtime(); settleYarn(r);
    const approach = startJourney(r);
    if (phase === 'walking') { await r.enter(); await r.runFrames(200); }
    r.state.pointerBodyId = 'cat';
    if (phase === 'loading') await r.enter();
    await r.frame(0);
    const heldX = r.body.x;
    await r.runFrames(1200);
    assert.equal(r.journey.playgroundJourney, approach); assert.equal(r.body.x, heldX);
    r.state.pointerBodyId = '';
    await r.frame(0); r.random(.9); await r.runFrames(7000);
    assert.equal(walkFacts(r).length, 1);
    assert.ok(walkFacts(r)[0].durationMs < r.now() - (phase === 'loading' ? 2200 : 1000) - 800);
  });
}

for (const problem of ['missing', 'airborne', 'moving', 'different-support', 'blocked-route']) {
  test(`walk-to-ball remains unavailable with ${problem} and creates no ordinary target`, () => {
    const r = runtime(); const yarn = settleYarn(r);
    if (problem === 'missing') r.state.bodies.delete(yarn.id);
    if (problem === 'airborne') yarn.grounded = false;
    if (problem === 'moving') yarn.vx = 5;
    if (problem === 'different-support') { yarn.y -= 100; }
    if (problem === 'blocked-route') {
      const obstacle = {...yarn, id: 'block', x: 400};
      r.state.bodies.set(obstacle.id, obstacle);
    }
    r.context._syncNekoIdleCat1Journey(r.button);
    assert.equal(r.journey.playgroundJourney, undefined);
    assert.equal(r.journey.pendingWalkTimer, 0); assert.equal(r.journey.frame, 0);
  });
}

for (const direction of ['right', 'left']) {
  test(`paired small move ${direction} commits the same real displacement to cat and ball`, async () => {
    const r = runtime(); const yarn = settleYarn(r, 520);
    r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 400, r.body.floorY);
    r.random(direction === 'right' ? 0 : .5);
    assert.equal(requestMove(r), true);
    const plan = r.journey.pairMovePlan;
    assert.equal(plan.chatMode, 'playground'); assert.equal(plan.playgroundYarnBody, yarn);
    assert.equal(plan.dx > 0, direction === 'right');
    await r.enter();
    const start = {cat: r.body.x, yarn: yarn.x};
    await r.runFrames(plan.durationMs / 2);
    assert.ok(Math.abs(r.body.x - start.cat - yarn.x + start.yarn) < .01);
    assert.ok(Math.abs(r.body.x - start.cat) > 50);
    await r.runFrames(plan.durationMs / 2 + 40);
    assert.equal(moveResults(r).length, 1); assert.equal(moveResults(r)[0].result, 'done');
    assert.ok(Math.abs(moveResults(r)[0].detail.pathDistancePx - Math.abs(plan.dx)) < .01);
    assert.ok(Math.abs(r.body.x - start.cat - yarn.x + start.yarn) < .01);
    assert.equal(r.state.active, true); assertPositionSynced(r);
  });
}

test('a removed ball cancels the bound pair and old callbacks cannot move its replacement', async () => {
  const r = runtime(); const yarn = settleYarn(r, 220);
  r.random(0); assert.equal(requestMove(r), true); await r.enter(); await r.runFrames(400);
  const plan = r.journey.pairMovePlan, staleFrame = r.frames.get(r.journey.pairMoveFrame);
  const replacement = {...yarn}; r.state.bodies.set(yarn.id, replacement);
  r.context._syncNekoIdleCat1PlaygroundSmallMove(r.button); await r.flush();
  assert.equal(r.journey.pairMovePlan, null); assert.equal(moveResults(r).length, 1);
  const x = replacement.x; staleFrame(r.now() + 10000); await r.flush();
  assert.equal(replacement.x, x); assert.equal(moveResults(r).length, 1);
  assert.notEqual(moveResults(r)[0].result, 'done');
  assert.ok(moveResults(r)[0].detail.pathDistancePx > 0);
  assert.ok(moveResults(r)[0].detail.pathDistancePx < Math.abs(plan.dx));
});

test('yarn and paired target probes are pure and do not create journey state', () => {
  const r = runtime(); settleYarn(r, 520);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 400, r.body.floorY);
  delete r.button.__nekoIdleReturnSubactionState; delete r.button.__nekoIdleCat1Journey;
  const snapshot = ownerSnapshot(r), draws = r.randomCalls(), frames = r.frames.size;
  const yarn = r.state.bodies.get('yarn'), yarnX = yarn.x, writes = r.writes.length;
  assert.ok(r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button));
  assert.ok(r.context._getNekoIdleCat1PlaygroundPairMoveTarget(r.button, {left: 550, top: r.body.y}));
  assert.deepEqual(ownerSnapshot(r), snapshot); assert.equal(yarn.x, yarnX);
  assert.equal(r.randomCalls(), draws); assert.equal(r.frames.size, frames); assert.equal(r.writes.length, writes);
  assert.equal(r.button.__nekoIdleReturnSubactionState, undefined);
  assert.equal(r.button.__nekoIdleCat1Journey, undefined);
});

test('desktop-yarn uses the same paired runner, current body identity and real path', async () => {
  const r = runtime(); const yarn = settleYarn(r, 520);
  r.state.bodies.delete(yarn.id); yarn.id = 'desktop-yarn'; yarn.desktop = true;
  r.state.bodies.set(yarn.id, yarn);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 400, r.body.floorY);
  r.random(0); assert.equal(requestMove(r), true);
  const plan = r.journey.pairMovePlan; assert.equal(plan.playgroundYarnBody, yarn);
  await r.enter(); await r.runFrames(plan.durationMs + 40);
  assert.equal(moveResults(r).length, 1); assert.equal(moveResults(r)[0].result, 'done');
  assert.equal(r.state.bodies.get('desktop-yarn'), yarn);
  assert.ok(Math.abs(yarn.x - 520 - plan.dx) < .01);
});

for (const failure of ['error', 'timeout']) {
  test(`walking image ${failure} cancels once and the same physics fact cannot immediately retry`, async () => {
    const r = runtime(); const yarn = settleYarn(r);
    const approach = startJourney(r), start = ownerSnapshot(r);
    if (failure === 'error') r.fail(); else await r.advance(5000);
    assert.equal(r.journey.playgroundJourney, null); assert.deepEqual(ownerSnapshot(r), start);
    assert.equal(walkFacts(r).length, 0);
    r.context._syncNekoIdleCat1PlaygroundJourney(r.button); await r.runFrames(300);
    assert.equal(r.journey.playgroundJourney, null);
    r.load(); await r.flush(); assert.equal(r.journey.playgroundJourney, null);
    assert.equal(walkFacts(r).length, 0);
    r.context._setNekoIdleCat1PlaygroundBodyPosition(yarn, yarn.x + 20, yarn.y);
    r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    assert.ok(r.journey.playgroundJourney);
    assert.notEqual(r.journey.playgroundJourney, approach, 'a genuinely new target gets a new approach');
  });
}

test('the walk-to-ball local play branch uses the original probability and completes only once', async () => {
  const r = runtime(); settleYarn(r); startJourney(r); await r.enter();
  r.random(0); await r.runFrames(7000);
  assert.equal(r.journey.walkFinishResolution, 'play');
  const play = r.context._getNekoIdleCat1PlayActionState(r.button);
  assert.equal(play.active, true); assert.equal(r.journey.playgroundJourney, null);
  await r.enter();
  const duration = await r.context._getNekoIdleAnimationDurationMs(
    r.context._NEKO_IDLE_CAT1_PLAY_ASSET_URL || r.art.src);
  await r.runFrames(duration + 40);
  assert.equal(play.active, false); assert.equal(walkFacts(r).length, 1);
  const outcomes = r.events.filter(event => event.detail.type === 'cat1_local_play_done');
  assert.equal(outcomes.length, 1);
  r.context._finishNekoIdleCat1Walk(r.button);
  assert.equal(r.events.filter(event => event.detail.type === 'cat1_local_play_done').length, 1);
});

test('a narrow native support refuses the whole paired move without a solo fallback', () => {
  const r = runtime(); const yarn = settleYarn(r, 520);
  assert.equal(r.admitWindow({x: 300, y: 100, width: 300, height: 800}), true);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 400, r.body.y);
  const before = ownerSnapshot(r), yarnX = yarn.x;
  assert.ok(r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button));
  assert.equal(requestMove(r), false);
  assert.deepEqual(ownerSnapshot(r), before); assert.equal(yarn.x, yarnX);
  assert.equal(moveResults(r).length, 0);
});

test('an old walking frame cannot advance or clear the replacement approach', async () => {
  const r = runtime(); const yarn = settleYarn(r);
  startJourney(r); await r.enter(); await r.runFrames(300);
  const oldFrame = r.frames.get(r.journey.frame);
  r.context._cancelNekoIdleCat1Journey(r.button, {reason:'test-cancelled',resetArt:true});
  r.context._setNekoIdleCat1PlaygroundBodyPosition(yarn,yarn.x+30,yarn.y);
  const replacement = startJourney(r); await r.enter();
  const x = r.body.x, frame = r.journey.frame;
  oldFrame(r.now()+10000);
  assert.equal(r.body.x,x); assert.equal(r.journey.frame,frame);
  assert.equal(r.journey.playgroundJourney,replacement);
  r.random(.9); await r.runFrames(7000);
  assert.equal(walkFacts(r).length,2);
  assert.equal(walkFacts(r)[0].cancelled,true);
  assert.ok(walkFacts(r)[1].pathDistancePx>200);
});

test('the original walking hover pauses its approach, preserves the response and resumes after the cycle', async () => {
  const r = runtime(); settleYarn(r); const approach = startJourney(r);
  await r.enter(); await r.runFrames(300); const x = r.body.x;
  r.context._playNekoIdleHoverArt(r.art,'cat1',{userInitiated:true});
  assert.equal(r.journey.paused,true); assert.ok(r.art.__nekoIdleHoverSrc);
  const hoverSource = r.art.src; r.load(); await r.runFrames(400);
  assert.equal(r.journey.playgroundJourney,approach); assert.equal(r.body.x,x);
  assert.equal(r.art.src,hoverSource,'physics must retain the original hover response');
  r.context._finishNekoIdleHoverArtAfterPlayback(r.art,'cat1');
  r.random(.9); await r.runFrames(12000);
  assert.equal(r.journey.playgroundJourney,null); assert.equal(r.journey.paused,false);
  assert.equal(walkFacts(r).length,1);
  assert.ok(Math.abs(walkFacts(r)[0].pathDistancePx-380)<.01);
});

for (const movement of ['journey', 'pair']) {
  for (const heldBody of ['cat', 'yarn']) {
    test(`${movement}: actual pending ${heldBody} pointer freezes then pointercancel resumes the same owner`, async () => {
      const r = runtime(); const yarn = settleYarn(r, movement === 'pair' ? 220 : 600);
      const owned = movement === 'pair'
        ? (r.random(0), assert.equal(requestMove(r), true), r.journey.pairMovePlan)
        : startJourney(r);
      await r.enter(); await r.runFrames(200);
      const body = heldBody === 'cat' ? r.body : yarn;
      const pointer = {type:'pointerdown',pointerId: 57,button:0,
        clientX:body.x+32,clientY:body.y+32,stopPropagation(){}};
      assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button,body,pointer),true);
      await r.frame(0);
      const heldX = r.body.x, yarnX = yarn.x;
      await r.runFrames(800);
      assert.equal(r.body.x,heldX); assert.equal(yarn.x,yarnX);
      assert.equal(movement === 'pair' ? r.journey.pairMovePlan : r.journey.playgroundJourney,owned);
      r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button,{type:'pointercancel',pointerId:57});
      await r.frame(0); r.random(.9); await r.runFrames(7000);
      if (movement === 'pair') {
        assert.equal(moveResults(r).length,1); assert.equal(moveResults(r)[0].result,'done');
        assert.ok(moveResults(r)[0].detail.durationMs < owned.durationMs + 40);
      } else assert.equal(walkFacts(r).length,1);
    });
  }
}

test('desktop pair publication cannot render an old cat after a synchronous owner handoff', async () => {
  const r = runtime(), yarn = r.state.bodies.get('yarn');
  r.context._updateNekoIdleCat1PlaygroundBodyBounds(yarn);
  Object.assign(yarn, { x: 520, y: yarn.floorY, vx: 0, vy: 0, grounded: true,
    dragging: false, desktop: true, screenOffsetX: 0, screenOffsetY: 0 });
  r.state.bodies.delete(yarn.id); yarn.id = 'desktop-yarn'; r.state.bodies.set(yarn.id, yarn);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 400, r.body.floorY);
  r.random(0); assert.equal(requestMove(r), true); await r.enter();
  const plan = r.journey.pairMovePlan;
  assert.equal(plan.chatMode, 'playground');
  let handedOff = false;
  r.window.addEventListener('neko:idle-chat-pair-move-bounds', () => {
    if (handedOff || !plan.committingPosition) return;
    handedOff = true;
    // Desktop publication is synchronous. A legitimate terminal consumer can
    // leave Playground and hand this same DOM container to ordinary positioning.
    r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'test-new-owner');
    assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, 700, 300), true);
  });
  await r.runFrames(32);
  assert.equal(handedOff, true);
  assert.equal(r.state.active, false);
  assert.equal(r.journey.pairMovePlan, null);
  assert.equal(r.container.style.left, '700px');
  assert.equal(r.container.style.top, '300px');
  assert.equal(moveResults(r).length, 1);
  assert.notEqual(moveResults(r)[0].result, 'done');
  await r.runFrames(1000);
  assert.equal(r.container.style.left, '700px');
  assert.equal(r.container.style.top, '300px');
  assert.equal(moveResults(r).length, 1);
});

test('withholding only pair movement leaves solo and basic interaction capabilities available', () => {
  const r = runtime(); const yarn = settleYarn(r, 520);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body,400,r.body.floorY);
  const before = ownerSnapshot(r), x = yarn.x;
  r.state.disabledCapabilities.add('pair-move');
  assert.equal(r.context._getNekoIdleCat1PlaygroundPairMoveTarget(r.button,{left:550,top:r.body.y}),null);
  assert.equal(requestMove(r),false);
  assert.deepEqual(ownerSnapshot(r),before); assert.equal(yarn.x,x);
  assert.equal(r.context._isNekoIdleCat1PlaygroundEntryOrDropActive(r.button,'hover'),false);
  r.state.disabledCapabilities.delete('pair-move');
  r.random(0); assert.equal(requestMove(r),true);
  assert.equal(r.journey.pairMovePlan.chatMode,'playground');
});

async function runMovementPressClockExperiment(movement, heldBody, heldMs, terminal = 'cancel') {
  const r = runtime();
  r.context._ensureNekoIdleReturnPresentationBridge();
  const yarn = r.state.bodies.get('yarn');
  r.context._updateNekoIdleCat1PlaygroundBodyBounds(yarn);
  Object.assign(yarn, { x: movement === 'pair' ? 220 : 600, y: yarn.floorY,
    vx: 0, vy: 0, grounded: true, dragging: false });
  r.context._setNekoIdleCat1PlaygroundBodyPosition(yarn, yarn.x, yarn.y);
  r.random(0);
  if (movement === 'pair') assert.equal(requestMove(r), true);
  else r.context._syncNekoIdleCat1Journey(r.button);
  await r.enter(); await r.runFrames(300);
  const body = heldBody === 'cat' ? r.body : yarn;
  const pointer = { type: 'pointerdown', pointerId: 83, button: 0,
    clientX: body.x + 32, clientY: body.y + 32, stopPropagation() {} };
  assert.equal(r.context._handleNekoIdleCat1PlaygroundPointerDownForBody(r.button, body, pointer), true);
  const x = r.body.x;
  // Host time and gesture terminals can advance before the next renderer RAF.
  await r.advance(heldMs);
  assert.equal(r.body.x, x);
  if (terminal === 'release') {
    r.context._handleNekoIdleCat1PlaygroundPointerUp(r.button, {
      type: heldBody === 'cat' ? 'pointerup' : 'pointercancel', pointerId: 83,
    });
    // A ball's no-movement pointerup opens chat; pointercancel is its existing
    // resume path. Once either hold ends, subsequent active time is no hold.
    await r.advance(200);
  } else if (terminal === 'drag') {
    r.context._handleNekoIdleCat1PlaygroundPointerMove(r.button, {
      ...pointer, type: 'pointermove', clientX: pointer.clientX + 30, clientY: pointer.clientY - 40,
      preventDefault() {},
    });
  }
  r.context._cancelNekoIdleCat1Journey(r.button, { reason: 'test-pointer-clock-end', resetArt: true });
  await r.flush();
  const facts = movement === 'pair'
    ? moveResults(r).map(event => event.detail)
    : r.events.filter(event => event.type === 'neko:cat-mind:observation' &&
      event.detail.type === 'cat1_walk_done_near_chat').map(event => event.detail.detail);
  assert.equal(facts.length, 1); assert.ok(facts[0].pathDistancePx > 0);
  if (movement === 'pair') assert.notEqual(moveResults(r)[0].result, 'done');
  else assert.equal(facts[0].cancelled, true);
  return facts[0];
}

for (const movement of ['journey', 'pair']) {
  for (const heldBody of ['cat', 'yarn']) {
    test(`${movement}: ${heldBody} press/terminal clocks exclude hold before a renderer frame`, async () => {
      const control = await runMovementPressClockExperiment(movement, heldBody, 0);
      const held = await runMovementPressClockExperiment(movement, heldBody, 1000);
      const released = await runMovementPressClockExperiment(movement, heldBody, 1000, 'release');
      assert.equal(held.pathDistancePx, control.pathDistancePx);
      assert.equal(held.durationMs, control.durationMs, 'a stationary 1000ms press is not autonomous effort');
      assert.equal(released.pathDistancePx, control.pathDistancePx);
      assert.equal(released.durationMs, control.durationMs + 200, 'the hold ends at its terminal, without waiting for RAF');
    });
  }
  test(`${movement}: real drag interrupts without billing its pending hold or user path as autonomous effort`, async () => {
    const control = await runMovementPressClockExperiment(movement, 'cat', 0, 'drag');
    const held = await runMovementPressClockExperiment(movement, 'cat', 1000, 'drag');
    assert.equal(held.pathDistancePx, control.pathDistancePx);
    assert.equal(held.durationMs, control.durationMs);
  });
}

function setupNativePairFacts(r) {
  const yarn = settleYarn(r, 520);
  Object.assign(yarn, { desktop: true, screenOffsetX: 0, screenOffsetY: 0 });
  r.state.bodies.delete(yarn.id);
  yarn.id = 'desktop-yarn';
  r.state.bodies.set(yarn.id, yarn);
  r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, 400, r.body.floorY);
  return yarn;
}

for (const callback of ['remove-ball', 'passive-cat-motion', 'owner-handoff']) {
  test(`native pair publication ${callback} retains this committed frame and excludes passive motion`, async () => {
    const r = runtime(), yarn = setupNativePairFacts(r);
    r.random(0);
    assert.equal(requestMove(r), true);
    await r.enter();
    const plan = r.journey.pairMovePlan, startedX = r.body.x;
    const staleFrame = r.frames.get(r.journey.pairMoveFrame);
    let published = false, committedDistance = 0;
    r.window.addEventListener('neko:idle-chat-pair-move-bounds', () => {
      if (published || !plan.committingPosition) return;
      published = true;
      committedDistance = Math.abs(r.body.x - startedX);
      assert.ok(committedDistance > 0);
      assert.ok(Math.abs(r.body.x - startedX - yarn.x + plan.yarnStartLeft) < .01,
        'both bodies have the same committed displacement before synchronous publication');
      if (callback === 'remove-ball') r.context._removeNekoIdleCat1PlaygroundYarnBody(r.button);
      else if (callback === 'owner-handoff') {
        r.context._releaseNekoIdleCat1PlaygroundDropLifecycle(r.button, 'test-new-owner');
        assert.equal(r.context._setNekoIdleCat1ContainerPosition(r.container, 700, 300), true);
      } else r.body.x += 30; // An external position change is not autonomous effort.
    });
    await r.frame(400);
    await r.flush();
    assert.equal(published, true);
    assert.equal(r.journey.pairMovePlan, null);
    assert.equal(moveResults(r).length, 1);
    assert.notEqual(moveResults(r)[0].result, 'done');
    assert.ok(Math.abs(moveResults(r)[0].detail.pathDistancePx - committedDistance) < .01);
    if (callback === 'passive-cat-motion') {
      assert.ok(Math.abs(r.body.x - startedX - committedDistance - 30) < .01);
    } else {
      assert.equal(r.state.bodies.has(yarn.id), false);
      if (callback === 'owner-handoff') {
        assert.equal(r.container.style.left, '700px');
        assert.equal(r.container.style.top, '300px');
      }
    }
    const catX = r.body.x, facts = moveResults(r)[0].detail.pathDistancePx;
    staleFrame(r.now() + 10000);
    await r.flush();
    assert.equal(r.body.x, catX);
    assert.equal(moveResults(r).length, 1);
    assert.equal(moveResults(r)[0].detail.pathDistancePx, facts);
    if (callback === 'owner-handoff') {
      assert.equal(r.container.style.left, '700px');
      assert.equal(r.container.style.top, '300px');
    }
  });
}

async function actualAppPairPublicationCancel(removeDuringPublication) {
  const r = runtime();
  setupNativePairFacts(r);
  // Hold unrelated presentation providers closed so the actual selector can
  // express this movement as its needs develop; do not replace the selector.
  for (const capability of ['play', 'eat', 'visual-actions']) r.state.disabledCapabilities.add(capability);
  r.controls.audioEnabled = false;
  require('./cat_playground_small_move.test.cjs').attachRealMind(r);
  r.random(.99);
  await r.advance(0);
  for (let index = 0; index < 20 && !r.journey.pairMovePlan; index++) await r.advance(30000);
  const plan = r.journey.pairMovePlan;
  assert.ok(plan);
  assert.equal(plan.chatMode, 'playground');
  await r.enter();
  assert.equal(r.window.nekoCatMind.getState().activeAction.actionId, 'cat1_small_move');
  const fieldsBefore = r.window.nekoCatMind.getState().fields, startedX = r.body.x;
  let published = false, committedDistance = 0;
  if (removeDuringPublication) {
    r.window.addEventListener('neko:idle-chat-pair-move-bounds', () => {
      if (published || !plan.committingPosition) return;
      published = true;
      committedDistance = Math.abs(r.body.x - startedX);
      r.context._removeNekoIdleCat1PlaygroundYarnBody(r.button);
    });
  }
  await r.frame(400);
  if (!removeDuringPublication) {
    committedDistance = Math.abs(r.body.x - startedX);
    r.context._cancelNekoIdleCat1PairMove(r.journey, { reason: 'test-normal-cancel' });
  } else assert.equal(published, true);
  await r.flush();
  const terminal = moveResults(r)[0], ended = r.window.nekoCatMind.getState();
  assert.equal(moveResults(r).length, 1);
  assert.notEqual(terminal.result, 'done');
  assert.ok(Math.abs(terminal.detail.pathDistancePx - committedDistance) < .01);
  assert.equal(ended.activeAction, null);
  assert.ok(ended.fields.energy < fieldsBefore.energy);
  assert.equal(ended.fields.stimulation_need, fieldsBefore.stimulation_need, 'cancel earns no completion reward');
  r.window.dispatchEvent({ type: 'neko:cat-mind:action-result', detail: terminal });
  assert.deepEqual(r.window.nekoCatMind.getState().fields, ended.fields, 'a replay cannot charge the same activity again');
  return { committedDistance, facts: terminal.detail, fields: ended.fields };
}

test('actual App gives synchronous ball removal the same physical load as normal partial cancellation', async () => {
  const control = await actualAppPairPublicationCancel(false);
  const removed = await actualAppPairPublicationCancel(true);
  assert.equal(removed.committedDistance, control.committedDistance);
  assert.equal(removed.facts.pathDistancePx, control.facts.pathDistancePx);
  assert.equal(removed.facts.durationMs, control.facts.durationMs);
  for (const field of Object.keys(control.fields)) {
    assert.equal(removed.fields[field], control.fields[field], `same physical load for ${field}`);
  }
});

for (const condition of ['obstacle', 'moving-ball']) {
  test(`journey resumes after temporary ${condition} clears, without retrying while unavailable`, async () => {
    const r = runtime(), yarn = settleYarn(r), originalYarnX = yarn.x;
    const old = startJourney(r); await r.enter(); await r.runFrames(300);
    let obstacle;
    if (condition === 'obstacle') {
      obstacle = { ...yarn, id: 'question-block', x: 300, y: r.body.y,
        width: 96, height: 96, visibleInsetRatios: { left: 0, top: 0, right: 0, bottom: 0 },
        bodyCollisionInsetRatios: null };
      r.state.bodies.set(obstacle.id, obstacle);
    } else yarn.vx = 70;
    r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    assert.equal(r.journey.playgroundJourney, null);
    assert.equal(walkFacts(r).length, 1); assert.equal(walkFacts(r)[0].cancelled, true);
    const walkingSource = r.context._getNekoIdleCat1WalkingAssetUrl().split('?')[0];
    const walkingWrites = () => r.writes.filter(source => source.split('?')[0] === walkingSource).length;
    const writes = walkingWrites();
    for (let frame = 0; frame < 20; frame += 1) {
      if (condition === 'moving-ball') yarn.vx = 70;
      await r.frame(16);
      r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
      assert.equal(r.journey.playgroundJourney, null);
      assert.equal(r.journey.pendingWalkTimer, 0); assert.equal(r.journey.frame, 0);
    }
    assert.equal(walkingWrites(), writes, 'unavailable support/path must not start image retry loops');
    assert.equal(walkFacts(r).length, 1);
    if (condition === 'obstacle') {
      r.context._setNekoIdleCat1PlaygroundBodyPosition(obstacle, 900, obstacle.floorY);
    } else {
      Object.assign(yarn, { vx: 0, vy: 0, grounded: true });
      r.context._setNekoIdleCat1PlaygroundBodyPosition(yarn, originalYarnX, yarn.floorY);
    }
    assert.ok(r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button));
    r.random(0); r.context._syncNekoIdleCat1PlaygroundJourney(r.button);
    assert.ok(r.journey.playgroundJourney, 'the same target becomes eligible when its real path/support recovers');
    assert.notEqual(r.journey.playgroundJourney, old);
    await r.enter(); r.random(.9); await r.runFrames(7000);
    assert.equal(walkFacts(r).length, 2);
    assert.notEqual(walkFacts(r)[1].activityId, walkFacts(r)[0].activityId);
    assert.ok(walkFacts(r)[1].pathDistancePx > 200);
    assert.equal(r.journey.playgroundJourney, null);
  });
}

test('hover during the walking first-image load resumes the same approach with a fresh owned image cycle', async () => {
  const r = runtime(); settleYarn(r);
  r.context._ensureNekoIdleReturnPresentationBridge();
  const approach = startJourney(r), x = r.body.x;
  assert.equal(approach.loading, true); assert.equal(approach.playbackStarted, false);
  r.context._playNekoIdleHoverArt(r.art, 'cat1', { userInitiated: true });
  assert.equal(r.journey.playgroundJourney, approach);
  assert.equal(r.journey.paused, true); assert.ok(r.art.__nekoIdleHoverSrc);
  r.load(); await r.flush();
  const hoverDuration = await r.context._getNekoIdleAnimationDurationMs(r.art.__nekoIdleHoverSrc);
  r.context._finishNekoIdleHoverArtAfterPlayback(r.art, 'cat1'); await r.flush();
  await r.runFrames(hoverDuration + 32);
  assert.equal(r.journey.playgroundJourney, approach);
  assert.equal(r.journey.paused, false);
  assert.equal(r.body.x, x); assert.equal(walkFacts(r).length, 0);
  // The hover owned a different source. Its elapsed cycle must not consume
  // the resumed walking source's bounded first-image wait or leave an old token.
  await r.runFrames(3000);
  assert.equal(r.journey.playgroundJourney, approach);
  assert.equal(approach.playbackStarted, false);
  assert.equal(r.body.x, x); assert.equal(walkFacts(r).length, 0);
  await r.enter();
  assert.equal(r.journey.playgroundJourney, approach); assert.equal(approach.playbackStarted, true);
  r.random(.9); await r.runFrames(7000);
  assert.equal(r.journey.playgroundJourney, null);
  assert.equal(walkFacts(r).length, 1);
  assert.ok(walkFacts(r)[0].pathDistancePx > 200);
  assert.ok(walkFacts(r)[0].durationMs < 7000, 'the hover and both source waits are not walking effort');
});

function attachDesktopTestYarn(r, profile) {
  r.window.__NEKO_MULTI_WINDOW__ = profile !== 'web';
  r.window.__NEKO_DESKTOP_RUNTIME__ = {
    isWayland: profile === 'wayland' || profile === 'niri', isNiriWayland: profile === 'niri',
  };
  r.document.removeEventListener = () => {};
  assert.equal(r.context._attachNekoIdleCat1PlaygroundYarnBody(r.button, {
    mode: 'desktop', rect: { left: 600, top: 800, width: 58, height: 58 },
    screenRect: { left: 600, top: 800, width: 58, height: 58 },
  }, { refresh: false }), true);
  return settleYarn(r);
}

for (const profile of ['web', 'windows', 'wayland', 'niri']) {
  for (const facingRight of [true, false]) {
    test(`${profile}: physical desktop yarn has a reachable target from ${facingRight ? 'left' : 'right'}`, async () => {
      const r = runtime(), yarn = attachDesktopTestYarn(r, profile);
      r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, facingRight ? 100 : 800, r.body.floorY);
      assert.ok(r.context._getNekoIdleCat1PlaygroundYarnContext(r.button));
      const target = r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button);
      assert.ok(target, 'the ordinary native-ball calibration cannot push the target inside a physics hull');
      assert.equal(target.facingRight, facingRight);
      const allowed = r.context._getNekoIdleCat1PlaygroundMoveTarget(r.button, target);
      assert.equal(allowed.left, target.left);
      const originalBallX = yarn.x;
      startJourney(r);
      await r.enter(); r.random(.9); await r.runFrames(7000);
      assert.equal(r.journey.playgroundJourney, null);
      assert.ok(Math.abs(r.body.x - target.left) < .5);
      assert.equal(yarn.x, originalBallX);
      assert.equal(walkFacts(r).length, 1);
    });

    test(`${profile}: physical desktop yarn admits play and paired movement from ${facingRight ? 'left' : 'right'}`, () => {
      const r = runtime(), yarn = attachDesktopTestYarn(r, profile);
      r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, facingRight ? 100 : 800, r.body.floorY);
      const target = r.context._getNekoIdleCat1PlaygroundYarnTarget(r.button);
      assert.ok(target);
      r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, target.left, target.top);
      assert.ok(r.context._getNekoIdleCat1PlaygroundPairMoveTarget(r.button,
        { left: r.body.x + 20, top: r.body.y }));
      assert.equal(r.context._playNekoIdleCat1PlayAction(r.button), true);
      assert.equal(r.context._getNekoIdleCat1PlayActionState(r.button).playgroundYarnControl.yarnBody, yarn);
    });
  }
}

for (const facingRight of [true, false]) {
  test(`Wayland Playground walk/stretch ignores the native ball image anchor (right=${facingRight})`, async () => {
    const r = runtime(); attachDesktopTestYarn(r, 'wayland');
    r.context._setNekoIdleCat1PlaygroundBodyPosition(r.body, facingRight ? 100 : 800, r.body.floorY);
    const anchorAttrs = () => [
      r.button.getAttribute('data-neko-cat1-native-yarn-visual-anchor'),
      r.button.getAttribute('data-neko-cat1-native-yarn-side'),
    ];
    const rememberMinimizedBall = () => r.context._rememberNekoIdleDesktopChatPairMoveRect(
      { left: 600, top: 842, width: 58, height: 58 });
    rememberMinimizedBall();
    assert.ok(r.context._getNekoIdleChatMinimizedRect(), 'a fresh native minimized ball rect is present');
    startJourney(r);
    await r.enter(); await r.runFrames(100);
    assert.equal(r.journey.substate, r.journey.profile.walkingSubstate);
    assert.equal(r.journey.targetKind, 'minimized-side');
    assert.deepEqual(anchorAttrs(), [null, null]);
    r.random(.9); await r.runFrames(7000);
    assert.equal(r.journey.walkFinishResolution, 'stretch');
    rememberMinimizedBall();
    r.context._setNekoIdleCat1Classes(r.button, r.journey);
    assert.deepEqual(anchorAttrs(), [null, null]);
  });

  test(`ordinary Wayland retains its native ball image calibration (right=${facingRight})`, () => {
    const r = runtime(); attachDesktopTestYarn(r, 'wayland');
    const cat = { left: facingRight ? 100 : 800, top: r.body.y, width: 96, height: 96 };
    const ball = { left: 600, top: 842, right: 658, bottom: 900, width: 58, height: 58 };
    assert.equal(r.context._usesNekoIdleCat1NativeYarnVisualAnchor(ball), true);
    const calibrated = r.context._getNekoIdleCat1NativeYarnVisualTargetLeft(cat, ball, facingRight);
    assert.equal(r.context._computeNekoIdleCat1SideTargetForLook(cat, ball, facingRight).left,
      Math.round(calibrated));
    if (facingRight) assert.equal(Math.round(calibrated), 563);
  });
}
