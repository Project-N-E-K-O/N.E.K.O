const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// The pytest entry point hands this file to node as a temp file, so __dirname
// is the system temp directory rather than tests/frontend. Fall back to the
// cwd the wrapper sets, which is the repo root. Same guard the other
// pytest-driven suites use.
const fileRoot = path.resolve(__dirname, '..', '..');
const projectRoot = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const senderPath = path.join(projectRoot, 'static/vrm/vrm-vmc-sender.js');

// Load the real module in a stubbed window so sample() can be driven directly.
// The source-shape assertions in vmc_websocket_isolation.test.cjs pin how the
// expression budget is written; this file pins what it actually emits, which
// is the only thing a VMC receiver sees.
function loadSender(options) {
  const config = options || {};
  const sentFrames = [];
  const ackResolvers = [];
  const warnings = [];
  const errors = [];
  let now = 0;

  const listeners = new Map();
  const socket = {
    readyState: 1,
    bufferedAmount: 0,
    send(raw) {
      const message = JSON.parse(raw);
      sentFrames.push(message);
    },
    close() {},
  };

  // `.bind()` makes the stub stringify as "[native code]", which is how the
  // module tells a real constructor from an Electron preload wrapper. Without
  // it every test here would land on the refuse-to-connect path. Pass
  // `wrappedWebSocket: true` to get a plainly-visible function source, i.e. to
  // impersonate the wrapper.
  function FakeWebSocketImpl() { return socket; }
  const FakeWebSocket = config.wrappedWebSocket
    ? FakeWebSocketImpl
    : FakeWebSocketImpl.bind(null);
  FakeWebSocket.CONNECTING = 0;
  FakeWebSocket.OPEN = 1;
  FakeWebSocket.CLOSING = 2;
  FakeWebSocket.CLOSED = 3;

  const windowStub = {
    WebSocket: FakeWebSocket,
    location: { origin: 'http://localhost', protocol: 'http:', host: 'localhost' },
    addEventListener(type, handler) { listeners.set(type, handler); },
    removeEventListener(type) { listeners.delete(type); },
    vrmManager: null,
  };

  // No iframe: nativeWebSocketCtor() falls back to window.WebSocket, which is
  // exactly the fallback path the isolation test documents.
  const documentStub = {
    createElement() { throw new Error('no iframe in this harness'); },
    body: null,
    documentElement: {},
  };

  const context = vm.createContext({
    window: windowStub,
    document: documentStub,
    console: {
      info() {},
      warn(...args) { warnings.push(args.join(' ')); },
      error(...args) { errors.push(args.join(' ')); },
      log() {},
    },
    performance: { now: () => now },
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    clearInterval: () => {},
    // Most tests never poll; the ones that do need a real status body, since
    // syncStatusFromBackend() swallows rejections and would otherwise be a
    // no-op that proves nothing.
    fetch: () => (config.statusResponse
      ? Promise.resolve({
        ok: true,
        json: () => Promise.resolve(config.statusResponse),
      })
      : Promise.reject(new Error('no network in this harness'))),
    JSON,
    Promise,
    Set,
    Map,
    Number,
    Math,
    Object,
    Array,
    String,
    Date,
    Error,
    Function,
    RegExp,
  });
  context.globalThis = context;

  // The module is an IIFE, so its `state` closure is unreachable from outside.
  // Inject one export line at a stable anchor near the end rather than
  // re-implementing the expression budget in the test, which would let the
  // test and the shipped logic drift apart silently.
  const rawSource = fs.readFileSync(senderPath, 'utf8');
  const anchor = 'window.vrmVmcSender = api;';
  assert.ok(
    rawSource.includes(anchor),
    `harness anchor "${anchor}" not found; the module tail was restructured`
  );
  const instrumented = rawSource.replace(
    anchor,
    `${anchor}\n    globalThis.__vmcTestState = state;`
  );

  vm.runInContext(instrumented, context, {
    filename: 'static/vrm/vrm-vmc-sender.js',
  });

  const api = windowStub.vrmVmcSender;
  assert.ok(api && typeof api.sample === 'function', 'sender API must install');

  // Reach the frame path: sample() needs enabled + an OPEN socket marked ready.
  const stateHolder = context.__vmcTestState;
  assert.ok(stateHolder, 'module state must be reachable for the harness');
  stateHolder.enabled = true;
  stateHolder.ws = socket;
  stateHolder.wsReady = true;
  stateHolder.sourceActive = true;
  stateHolder.minIntervalSec = 0;

  return {
    api,
    state: stateHolder,
    sentFrames,
    ackResolvers,
    warnings,
    errors,
    advance(ms) { now += ms; },
    // Acking a frame is what lets the module drop names from the retiring set.
    ackLastFrame() {
      const frame = sentFrames[sentFrames.length - 1];
      if (!frame || !frame.require_ack) return;
      const waiter = stateHolder.ackWaiters.get(frame.sequence);
      if (waiter) {
        stateHolder.ackWaiters.delete(frame.sequence);
        waiter.resolve(true);
      }
    },
  };
}

// Minimal three-vrm stand-in: sample() only reads humanoid bone nodes and
// expressionManager.expressions.
function makeVrm(expressionNames, options) {
  const config = options || {};
  return {
    meta: { name: config.title || 'model' },
    humanoid: {
      getRawBoneNode: () => null,
      rawRestPose: {},
    },
    expressionManager: {
      expressions: expressionNames.map((name) => ({
        expressionName: name,
        weight: 1,
      })),
    },
  };
}

function expressionsOf(frame) {
  return (frame && frame.payload && frame.payload.expressions) || [];
}

const VRM_PRESETS = [
  'happy', 'angry', 'sad', 'relaxed', 'surprised',
  'aa', 'ih', 'ou', 'ee', 'oh',
  'blink', 'blinkLeft', 'blinkRight',
  'lookUp', 'lookDown', 'lookLeft', 'lookRight', 'neutral',
];

test('a model switch that shares preset names does not permanently shrink the live budget', async () => {
  const harness = loadSender();

  // Two fat models over the frame cap, sharing the standard VRM presets. This
  // is the ordinary case: every VRM ships these names.
  const modelA = makeVrm([...VRM_PRESETS, ...Array.from({ length: 282 }, (_, i) => `a${i}`)]);
  const modelB = makeVrm([...VRM_PRESETS, ...Array.from({ length: 282 }, (_, i) => `b${i}`)]);

  harness.api.sample(modelA);
  assert.ok(harness.sentFrames.length > 0, 'the first sample must emit a frame');

  // Switch, then let retirements drain for well over the ceil(300/16) frames
  // a full retirement batch needs.
  for (let i = 0; i < 120; i++) {
    harness.advance(100);
    harness.api.sample(modelB);
    harness.ackLastFrame();
    await Promise.resolve();
  }

  const lastFrame = harness.sentFrames[harness.sentFrames.length - 1];
  const emitted = expressionsOf(lastFrame);

  // Names the new model also owns must not squat a retirement reservation:
  // they never enter a retirement frame's ack list, so a set that keeps them
  // holds back quota slots forever and silently drops live expressions.
  const stillRetiring = Array.from(harness.state.retiringExpressionNames);
  const sharedStuck = stillRetiring.filter((name) => VRM_PRESETS.includes(name));
  assert.deepEqual(
    sharedStuck,
    [],
    `shared preset names must leave the retiring set, found: ${JSON.stringify(sharedStuck)}`
  );

  // With the retiring set drained, the whole frame is available to live
  // expressions again.
  assert.equal(
    emitted.length,
    256,
    `a settled frame must use the full cap, got ${emitted.length}`
  );

  // And the presets carry the new model's real weights, not a stale zero.
  const byName = new Map(emitted.map((e) => [e.name, e.value]));
  for (const preset of VRM_PRESETS) {
    assert.equal(
      byName.get(preset),
      1,
      `${preset} must carry the live weight, got ${byName.get(preset)}`
    );
  }
});

test('an over-cap model warns once in the browser, where the truncation happens', async () => {
  const harness = loadSender();

  // The backend's warn-once overflow log can never fire for a browser
  // publisher: the sampler truncates before the frame is sent, so the array
  // the backend validates is already at the cap. Without a warning here, an
  // over-cap model drops expressions with nothing in any log.
  const overCap = makeVrm(Array.from({ length: 300 }, (_, i) => `x${i}`));

  harness.api.sample(overCap);

  const emitted = expressionsOf(harness.sentFrames[harness.sentFrames.length - 1]);
  assert.equal(emitted.length, 256, `the frame must be capped, got ${emitted.length}`);

  const overflowWarnings = harness.warnings.filter((line) => line.includes('300'));
  assert.equal(
    overflowWarnings.length,
    1,
    `truncation must warn exactly once, got ${JSON.stringify(harness.warnings)}`
  );

  // Warn-once, not warn-per-frame: at 60Hz a repeated log would flood the
  // console within seconds.
  for (let i = 0; i < 20; i++) {
    harness.advance(100);
    harness.api.sample(overCap);
    await Promise.resolve();
  }
  assert.equal(
    harness.warnings.filter((line) => line.includes('300')).length,
    1,
    'the overflow warning must not repeat every frame'
  );

  // Switching to a model inside the cap re-arms the flag, so the next
  // over-cap model is still reported.
  const withinCap = makeVrm(Array.from({ length: 10 }, (_, i) => `small${i}`));
  for (let i = 0; i < 40; i++) {
    harness.advance(100);
    harness.api.sample(withinCap);
    harness.ackLastFrame();
    await Promise.resolve();
  }
  assert.equal(
    harness.state.expressionOverflowWarned,
    false,
    'returning under the cap must re-arm the warning'
  );
});

test('genuine retirements still drain to zero through the reserved quota', async () => {
  const harness = loadSender();

  // modelB shares no names with modelA, so every modelA name is a genuine
  // retirement that must reach the receiver as an explicit zero.
  const oldNames = Array.from({ length: 40 }, (_, i) => `old${i}`);
  const modelA = makeVrm(oldNames);
  const modelB = makeVrm(Array.from({ length: 256 }, (_, i) => `new${i}`));

  harness.api.sample(modelA);

  const zeroed = new Set();
  for (let i = 0; i < 60; i++) {
    harness.advance(100);
    harness.api.sample(modelB);
    const frame = harness.sentFrames[harness.sentFrames.length - 1];
    for (const expression of expressionsOf(frame)) {
      if (oldNames.includes(expression.name)) {
        assert.equal(
          expression.value,
          0,
          `retiring ${expression.name} must be sent as zero`
        );
        zeroed.add(expression.name);
      }
    }
    harness.ackLastFrame();
    await Promise.resolve();
  }

  assert.equal(
    zeroed.size,
    oldNames.length,
    `every retired name must be zeroed, missed ${oldNames.length - zeroed.size}`
  );
  assert.equal(
    harness.state.retiringExpressionNames.size,
    0,
    'the retiring set must fully drain'
  );
});

test('a wrapped window.WebSocket with no iframe escape refuses to publish', async () => {
  // Electron Pet's preload replaces window.WebSocket and registers the newest
  // socket as the desktop chat IPC proxy target. If the same-origin iframe
  // borrow also fails, the only constructor left is that wrapper -- and
  // building a VMC socket from it would hand the chat channel to /api/vmc/ws.
  // Refusing is the correct trade: losing VMC output is recoverable, losing
  // the user's chat channel is not.
  const harness = loadSender({ wrappedWebSocket: true });

  // loadSender() forces sourceActive so the other tests reach the frame path.
  // Clear it here: whether sample() bails *before* markSourceActive() is part
  // of what this test checks, and a pre-set flag would hide the answer.
  harness.state.sourceActive = false;

  const model = makeVrm(['happy', 'blink']);
  for (let i = 0; i < 5; i++) {
    harness.advance(100);
    harness.api.sample(model);
    await Promise.resolve();
  }

  assert.equal(
    harness.sentFrames.length,
    0,
    `no frame may be published through the wrapper, got ${harness.sentFrames.length}`
  );

  const refusals = harness.errors.filter((line) => line.includes('hijacking the desktop chat channel'));
  assert.ok(
    refusals.length >= 1,
    `the refusal must be logged, got ${JSON.stringify(harness.errors)}`
  );

  // Silent-by-design on the frame path: at 60Hz a per-frame log would flood.
  assert.equal(refusals.length, 1, 'the refusal must be logged once, not per frame');

  // And no retry timer is armed, because a wrapped top-level constructor never
  // becomes native within a page lifetime.
  assert.equal(harness.state.reconnectTimer, null, 'a blocked transport must not arm reconnects');
  assert.equal(
    harness.state.sourceActive,
    false,
    'sampling must bail before marking the source active, or the idle timer keeps running'
  );
});

test('the refusal survives a model release and a status poll', async () => {
  // A review of this change argued the refusal is silently clearable:
  // releaseVrm() falls through to closeWebSocket(), the next status poll
  // closes again, and from there sample() would quietly retry at 60Hz with the
  // error re-logged -- "VMC 莫名其妙不动了" with no way to tell why. The latch
  // is a module-level closure and closeWebSocket() only resets state.* fields,
  // so it should hold; reading the code is not evidence, so this drives the
  // exact sequence. Source-shape assertions cannot catch this regression: a
  // future closeWebSocket() that resets the latch keeps every substring the
  // isolation suite checks.
  const harness = loadSender({
    wrappedWebSocket: true,
    statusResponse: { success: true, enabled: true, send_rate_hz: 60 },
  });

  // A page that genuinely refused never built a socket. loadSender() pre-wires
  // one so the budget tests can reach sendFrameEnvelope; clearing it is what
  // makes releaseVrm() take the closeWebSocket() branch the review names,
  // rather than the release-ack branch that needs a live socket.
  harness.state.ws = null;
  harness.state.wsReady = false;
  harness.state.sourceActive = false;

  const refusals = () => harness.errors
    .filter((line) => line.includes('hijacking the desktop chat channel'))
    .length;

  const model = makeVrm(['happy', 'blink']);
  for (let i = 0; i < 3; i++) {
    harness.advance(100);
    harness.api.sample(model);
    await Promise.resolve();
  }
  assert.equal(refusals(), 1, 'the refusal must be logged on the first blocked sample');

  // Step 1: the model switch.
  harness.api.releaseVrm(model);
  await Promise.resolve();
  assert.equal(refusals(), 1, 'releasing the model must not re-arm the transport');

  // Step 2: the status poll that follows it.
  await harness.api.syncStatusFromBackend();
  assert.equal(
    harness.state.enabled,
    true,
    'the poll must have actually applied a response, or this step proves nothing'
  );
  assert.equal(refusals(), 1, 'a status poll must not re-arm the transport');

  // Step 3: half a second of sampling, i.e. the 60Hz silent retry the review
  // predicts. One log and zero frames is the whole claim, tested.
  for (let i = 0; i < 30; i++) {
    harness.advance(16);
    harness.api.sample(model);
    await Promise.resolve();
  }

  assert.equal(
    refusals(),
    1,
    `the refusal must stay logged exactly once, got ${JSON.stringify(harness.errors)}`
  );
  assert.equal(
    harness.sentFrames.length,
    0,
    `no frame may be published after the refusal, got ${harness.sentFrames.length}`
  );
  assert.equal(
    harness.state.reconnectTimer,
    null,
    'no reconnect may be armed by the release/poll cycle'
  );
});

test('an unwrapped window.WebSocket fallback still publishes', async () => {
  // The mirror image of the refusal: where window.WebSocket does read as
  // native -- a browser with no preload, whose constructor is an engine
  // binding -- a CSP-blocked iframe must not disable VMC. This pins the
  // gating, not a claim that every host reads as native: one whose WebSocket
  // is written in JavaScript does not, which is the false positive the
  // looksNative() comment documents.
  const harness = loadSender();

  harness.api.sample(makeVrm(['happy', 'blink']));

  assert.ok(
    harness.sentFrames.length > 0,
    'a native top-level constructor must remain a usable fallback'
  );
  assert.equal(
    harness.errors.filter((line) => line.includes('hijacking the desktop chat channel')).length,
    0,
    'a plain browser must not be told its transport is blocked'
  );
});
