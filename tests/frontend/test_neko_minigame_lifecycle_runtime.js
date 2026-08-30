const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createEnvironment() {
  let nextTimerId = 0;
  const intervals = new Map();
  const timeouts = new Map();
  const documentListeners = new Map();
  const windowListeners = new Map();
  const consoleErrors = [];
  const documentImpl = {
    visibilityState: 'visible',
    hidden: false,
    addEventListener(type, handler) {
      let handlers = documentListeners.get(type);
      if (!handlers) {
        handlers = new Set();
        documentListeners.set(type, handlers);
      }
      handlers.add(handler);
    },
    removeEventListener(type, handler) {
      const handlers = documentListeners.get(type);
      handlers?.delete(handler);
      if (!handlers?.size) documentListeners.delete(type);
    },
    dispatch(type) {
      for (const handler of Array.from(documentListeners.get(type) || [])) handler();
    },
  };
  const windowImpl = {
    console: { error(...args) { consoleErrors.push(args); } },
    AbortController,
    setInterval(handler, intervalMs) {
      nextTimerId += 1;
      intervals.set(nextTimerId, { handler, intervalMs });
      return nextTimerId;
    },
    clearInterval(timerId) { intervals.delete(timerId); },
    setTimeout(handler, delayMs) {
      nextTimerId += 1;
      timeouts.set(nextTimerId, { handler, delayMs });
      return nextTimerId;
    },
    clearTimeout(timerId) { timeouts.delete(timerId); },
    addEventListener(type, handler) {
      let handlers = windowListeners.get(type);
      if (!handlers) {
        handlers = new Set();
        windowListeners.set(type, handlers);
      }
      handlers.add(handler);
    },
    removeEventListener(type, handler) {
      const handlers = windowListeners.get(type);
      handlers?.delete(handler);
      if (!handlers?.size) windowListeners.delete(type);
    },
    dispatch(type) {
      for (const handler of Array.from(windowListeners.get(type) || [])) handler({ type });
    },
  };
  return {
    windowImpl, documentImpl, intervals, timeouts,
    documentListeners, windowListeners, consoleErrors,
  };
}

function logger() {
  return {
    log() {}, info() {}, warn() {}, error() {},
    async enable() { return { ok: true }; },
    async enableAfterRouteStart() { return { ok: true }; },
    async flush() { return { ok: true }; },
    reset() {},
  };
}

async function main() {
  global.window = { console: { error() {} } };
  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });

  const environment = createEnvironment();
  let sessionNumber = 1;
  let runtimeState = { sessionId: 'session-1', characterName: '' };
  let heartbeatCalls = 0;
  let drainCalls = 0;
  let disposed = 0;
  const outputs = [
    { type: 'game_external_input', text: 'hello' },
    { type: 'game_llm_result', result: { line: 'hi', metadata: { source: 'host' } } },
  ];
  const transport = {
    logger: logger(),
    connectGame(request) {
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'lifecycle-test-host',
        registration: {
          mode: 'registered',
          gameId: request.manifest.id,
          version: request.manifest.version,
        },
        grantedCapabilities: [
          ...request.manifest.requiredCapabilities,
          ...request.manifest.optionalCapabilities,
        ],
      };
    },
    configureLogger() {},
    resetRuntime({ newSession }) {
      if (newSession) {
        sessionNumber += 1;
        runtimeState = { sessionId: `session-${sessionNumber}`, characterName: '' };
      } else {
        runtimeState = { ...runtimeState, characterName: '' };
      }
      return runtimeState;
    },
    getRuntimeState() { return runtimeState; },
    applyRuntimeState(state) {
      runtimeState = {
        ...runtimeState,
        characterName: String(state?.lanlan_name || runtimeState.characterName || ''),
      };
      return runtimeState;
    },
    async start(payload) {
      return { ok: true, state: { game_route_active: true, lanlan_name: 'Yui' }, payload };
    },
    async heartbeat(payload) {
      heartbeatCalls += 1;
      this.lastHeartbeatPayload = payload;
      return { ok: true, active: true, payload };
    },
    async drain(payload) {
      drainCalls += 1;
      this.lastDrainPayload = payload;
      return { ok: true, outputs: drainCalls === 1 ? outputs : [], payload };
    },
    async end(payload) { return { ok: true, payload }; },
    dispose() { disposed += 1; },
  };

  const game = await window.NekoMiniGame.connect({
    id: 'lifecycle-test',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport,
    windowImpl: environment.windowImpl,
    documentImpl: environment.documentImpl,
  });

  assert(game.runtime.state === 'idle', 'runtime did not start idle');
  assert(game.runtime.session.id === 'session-1', 'initial host session was not exposed');
  assert(typeof game.events?.on === 'function', 'public event subscription API is missing');
  assert(typeof game.runtime.configure === 'function', 'managed lifecycle configuration is missing');
  let unsupportedEventError = null;
  try { game.events.on('third-party-custom-event', () => {}); }
  catch (error) { unsupportedEventError = error; }
  assert(unsupportedEventError?.code === 'invalid_event',
    'unregistered event types must not grow the listener registry');

  game.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
  assert(!environment.windowListeners.has('pagehide')
    && !environment.windowListeners.has('beforeunload'),
    'pageExit=false must be accepted without installing page-exit listeners');

  let nestedMutationBlocked = false;
  game.events.on('runtime-output', (event) => {
    if (!event.payload.result) return;
    event.payload.result.metadata.source = 'listener-mutated';
    nestedMutationBlocked = event.payload.result.metadata.source === 'host';
  });
  const envelopes = [];
  const unsubscribeOutput = game.events.on('runtime-output', (event) => envelopes.push(event));
  const stateEvents = [];
  game.events.on('runtime-state', (event) => stateEvents.push(event));
  game.events.on('runtime-state', (event) => (
    event.payload.current === 'running'
      ? Promise.reject(new Error('async listener failed'))
      : undefined
  ));
  game.runtime.configure({
    payload: () => ({ score: 1 }),
    heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
    outputs: { intervalMs: 700, timeoutMs: 8000, limit: 50 },
    pageExit: true,
  });

  const resetState = game.runtime.reset({ newSession: true });
  assert(resetState.id === 'session-2', 'runtime reset did not rotate the host session');
  const started = await game.runtime.start({ mode: 'default' });
  assert(started.ok && started.data.ok, 'runtime start response was not normalized');
  const firstRouteInstanceId = started.data.payload.sdk_route_instance_id;
  assert(typeof firstRouteInstanceId === 'string' && firstRouteInstanceId.length > 0,
    'runtime start did not receive an SDK-owned route generation');
  assert(game.runtime.state === 'running', 'successful runtime start did not enter running');
  assert(game.runtime.session.characterName === 'Yui', 'host route state was not applied');
  assert(environment.intervals.size === 2, 'heartbeat and output polling did not have two bounded timers');
  assert(environment.documentListeners.get('visibilitychange')?.size === 1,
    'managed lifecycle did not own exactly one visibility listener');
  assert(environment.windowListeners.get('pagehide')?.size === 1,
    'managed lifecycle did not own exactly one pagehide listener');
  assert(environment.windowListeners.get('beforeunload')?.size === 1,
    'managed lifecycle did not own exactly one beforeunload listener');
  await Promise.resolve();
  await Promise.resolve();
  assert(environment.consoleErrors.some((args) => (
    String(args[0]).includes('runtime-state listener failed')
      && args[1]?.message === 'async listener failed'
  )), 'fire-and-forget runtime listener rejection was not observed');

  await game.runtime.pulse(true);
  await game.runtime.pollOutputs();
  assert(heartbeatCalls >= 1, 'manual heartbeat did not use the host transport');
  assert(transport.lastHeartbeatPayload?.sdk_route_instance_id === firstRouteInstanceId,
    'runtime heartbeat was not bound to the active route generation');
  assert(transport.lastDrainPayload?.limit === 50,
    'runtime drain did not delegate its bounded output limit to the host');
  assert(transport.lastDrainPayload?.sdk_route_instance_id === firstRouteInstanceId,
    'runtime drain was not bound to the active route generation');
  assert(envelopes.length === 2, 'runtime outputs were not delivered as events');
  assert(envelopes[0].type === 'runtime-output', 'runtime output event type is invalid');
  assert(envelopes[0].sequence > 0, 'runtime event sequence was not assigned');
  assert(envelopes[1].sequence === envelopes[0].sequence + 1,
    'runtime event sequence did not increase');
  assert(envelopes[0].sessionId === 'session-2', 'runtime event lost session ownership');
  assert(envelopes[0].payload.text === 'hello', 'runtime event payload was not preserved');
  assert(Object.isFrozen(envelopes[0]), 'runtime event envelope must be immutable');
  assert(Object.isFrozen(envelopes[1].payload)
    && Object.isFrozen(envelopes[1].payload.result)
    && Object.isFrozen(envelopes[1].payload.result.metadata),
  'runtime event payload must be recursively immutable');
  assert(nestedMutationBlocked && envelopes[1].payload.result.metadata.source === 'host',
    'one runtime event listener mutated nested payload observed by another listener');

  let activeResetError = null;
  try { game.runtime.reset({ newSession: true }); }
  catch (error) { activeResetError = error; }
  assert(activeResetError?.code === 'invalid_state' && game.runtime.state === 'running',
    'runtime reset abandoned an active host route instead of requiring runtime.end()');

  const heartbeatBeforeVisibility = heartbeatCalls;
  environment.documentImpl.visibilityState = 'hidden';
  environment.documentImpl.hidden = true;
  environment.documentImpl.dispatch('visibilitychange');
  await Promise.resolve();
  await Promise.resolve();
  assert(heartbeatCalls > heartbeatBeforeVisibility,
    'visibility change did not force a managed heartbeat');

  unsubscribeOutput();
  const ended = await game.runtime.end({ reason: 'completed' });
  assert(ended.ok && ended.data.ok, 'runtime end response was not normalized');
  assert(ended.data.payload.sdk_route_instance_id === firstRouteInstanceId,
    'runtime end did not retain the matching route generation');
  assert(ended.data.payload.sdk_route_instance_ids?.length === 1
    && ended.data.payload.sdk_route_instance_ids[0] === firstRouteInstanceId,
  'runtime end did not include its bounded route generation candidates');
  assert(game.runtime.state === 'ended', 'runtime end did not enter ended');
  assert(environment.intervals.size === 0, 'runtime end did not release lifecycle timers');
  assert(!environment.documentListeners.has('visibilitychange'),
    'runtime end did not release the visibility listener');
  assert(!environment.windowListeners.has('pagehide') && !environment.windowListeners.has('beforeunload'),
    'runtime end did not release page-exit listeners');
  assert(stateEvents.some((event) => event.payload.current === 'running'),
    'runtime state transitions were not emitted');

  const restarted = await game.runtime.start({ mode: 'restart-same-session' });
  const secondRouteInstanceId = restarted.data.payload.sdk_route_instance_id;
  assert(secondRouteInstanceId && secondRouteInstanceId !== firstRouteInstanceId,
    'a new route start reused the previous route generation');
  const reended = await game.runtime.end({ reason: 'restart-completed' });
  assert(reended.data.payload.sdk_route_instance_id === secondRouteInstanceId,
    'restarted route end did not retain its own generation');

  const retryEnvironment = createEnvironment();
  const retryStartPayloads = [];
  let retryEndPayload = null;
  const retryTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'retry-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'retry-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'retry-session', characterName: '' }; },
    async start(payload) {
      retryStartPayloads.push(payload);
      throw new Error(retryStartPayloads.length === 1 ? 'response lost' : 'request not delivered');
    },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end(payload) { retryEndPayload = payload; return { ok: true }; },
    dispose() {},
  };
  const retryGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-route-retry',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: retryTransport,
    windowImpl: retryEnvironment.windowImpl,
    documentImpl: retryEnvironment.documentImpl,
  });
  retryGame.runtime.configure({ heartbeat: false, outputs: false, pageExit: true });
  for (let attempt = 0; attempt < 4; attempt += 1) {
    let retryError = null;
    try { await retryGame.runtime.start({ attempt }); } catch (error) { retryError = error; }
    assert(retryError?.code === 'request_failed', 'failed runtime start was not normalized');
  }
  const unresolvedIds = retryStartPayloads.map((item) => item.sdk_route_instance_id);
  assert(new Set(unresolvedIds).size === 4, 'runtime retry did not retain four distinct route generations');
  let retryCapacityError = null;
  try { await retryGame.runtime.start({ attempt: 5 }); } catch (error) { retryCapacityError = error; }
  assert(retryCapacityError?.code === 'busy' && retryStartPayloads.length === 4,
    'runtime retry discarded an unresolved generation instead of enforcing its bound');
  await retryGame.runtime.end({ reason: 'retry-cleanup' });
  assert(retryEndPayload.sdk_route_instance_id === unresolvedIds[3]
    && retryEndPayload.sdk_route_instance_ids?.length === 4
    && unresolvedIds.every((id) => retryEndPayload.sdk_route_instance_ids.includes(id)),
  'runtime end discarded a possibly committed route generation after retry failure');
  retryGame.dispose();

  // The runtime lifecycle payload is the one SDK egress path that had no byte
  // bound: a game stuffing a replay buffer or a base64 frame into
  // configure({payload}) or into runtime.start() shipped it whole, at the
  // heartbeat and drain cadence. The check sits BEFORE the generation is
  // minted -- routing it through the transport catch instead would leave the
  // generation unresolved (correct for a network throw, wrong for a payload
  // that never left the browser) and wedge start() on `busy` after four tries.
  const rejectedPayloadEnvironment = createEnvironment();
  const rejectedPayloadStarts = [];
  const rejectedPayloadTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'rejected-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'rejected-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'rejected-session', characterName: '' }; },
    async start(payload) {
      rejectedPayloadStarts.push(payload);
      return { ok: true, state: { game_route_active: true, lanlan_name: 'Yui' }, payload };
    },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end() { return { ok: true }; },
    dispose() {},
  };
  const rejectedPayloadGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-rejected-payload',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: rejectedPayloadTransport,
    windowImpl: rejectedPayloadEnvironment.windowImpl,
    documentImpl: rejectedPayloadEnvironment.documentImpl,
  });
  rejectedPayloadGame.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
  for (let attempt = 0; attempt < 6; attempt += 1) {
    let rejectedError = null;
    try { await rejectedPayloadGame.runtime.start({ replay: 'x'.repeat(300 * 1024) }); }
    catch (error) { rejectedError = error; }
    assert(rejectedError?.code === 'invalid_request',
      'an oversized runtime lifecycle payload was accepted for dispatch');
  }
  assert(rejectedPayloadStarts.length === 0,
    'an oversized runtime lifecycle payload reached the transport');
  assert(rejectedPayloadGame.runtime.state === 'idle',
    'a payload rejected before dispatch still moved the runtime state machine');
  const acceptedStart = await rejectedPayloadGame.runtime.start({ replay: 'x'.repeat(200 * 1024) });
  assert(acceptedStart.ok && rejectedPayloadStarts.length === 1,
    'a runtime payload within the 256 KiB budget was rejected');
  await rejectedPayloadGame.runtime.end({ reason: 'bounded-payload' });
  rejectedPayloadGame.dispose();

  let reentrantDisposeError = null;
  game.events.on('runtime-state', (event) => {
    if (event.payload.current !== 'disposed') return;
    try { game.runtime.pollOutputs(); }
    catch (error) { reentrantDisposeError = error; }
  });
  game.dispose();
  assert(disposed === 1, 'runtime client did not release the transport');
  assert(reentrantDisposeError?.code === 'disposed',
    'a synchronous dispose listener could reopen a host request during cleanup');

  const inactiveHeartbeats = [];
  const inactiveEnvironment = createEnvironment();
  const inactiveTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'inactive-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'inactive-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'inactive-session', characterName: '' }; },
    async heartbeat(payload) { inactiveHeartbeats.push(payload); return { ok: true, active: false, reason: 'route-gone' }; },
    async drain() { return { ok: true, outputs: [] }; },
    dispose() {},
  };
  const inactiveGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-inactive',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: inactiveTransport,
    windowImpl: inactiveEnvironment.windowImpl,
    documentImpl: inactiveEnvironment.documentImpl,
  });
  inactiveGame.runtime.configure({
    heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
    outputs: { intervalMs: 700, timeoutMs: 8000 },
    pageExit: true,
  });
  await inactiveGame.runtime.start({});
  assert(inactiveEnvironment.intervals.size === 2,
    'inactive lifecycle fixture did not start both monitoring timers');
  await inactiveGame.runtime.pulse(true);
  assert(inactiveGame.runtime.state === 'inactive' && inactiveEnvironment.intervals.size === 0,
    'inactive heartbeat did not stop heartbeat and output monitoring together');
  assert(inactiveEnvironment.windowListeners.size === 0
    && inactiveEnvironment.documentListeners.size === 0,
  'inactive heartbeat left lifecycle listeners resident');

  // Losing the route through a heartbeat must retire its generation too.
  // Capabilities that are allowed before a route exists would otherwise keep
  // asserting a dead sdk_route_instance_id, and the host rejects
  // "no active route + caller asserts a generation" as route_instance_id_mismatch
  // instead of serving the pre-route call.
  inactiveHeartbeats.length = 0;
  await inactiveGame.runtime.pulse(true);
  // every() on an empty array is true, so prove a request was actually sent:
  // otherwise a pulse that stops issuing heartbeats would silently turn the
  // generation check below into a no-op that passes forever.
  assert(inactiveHeartbeats.length > 0,
    'no request was sent after the route loss, so the generation check proves nothing');
  assert(inactiveHeartbeats.every((payload) => !payload?.sdk_route_instance_id),
    'a request after a heartbeat-detected route loss still asserted the dead generation');

  inactiveGame.runtime.reset({ newSession: true });
  inactiveHeartbeats.length = 0;
  await inactiveGame.runtime.pulse(true);
  assert(inactiveHeartbeats.length > 0,
    'no request was sent after reset, so the generation check proves nothing');
  assert(inactiveHeartbeats.every((payload) => !payload?.sdk_route_instance_id),
    'reset() left the previous route generation attached to later requests');
  inactiveGame.dispose();

  const failedEndEnvironment = createEnvironment();
  let rejectEnd = true;
  const failedEndTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'failed-end-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'failed-end-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'failed-end-session', characterName: '' }; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end() {
      if (rejectEnd) throw Object.assign(new Error('network failed'), { code: 'request_failed' });
      return { ok: true };
    },
    dispose() {},
  };
  const failedEndGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-failed-end',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: failedEndTransport,
    windowImpl: failedEndEnvironment.windowImpl,
    documentImpl: failedEndEnvironment.documentImpl,
  });
  failedEndGame.runtime.configure({
    heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
    outputs: { intervalMs: 700, timeoutMs: 8000 },
    pageExit: false,
  });
  await failedEndGame.runtime.start({});
  let failedEndError = null;
  try { await failedEndGame.runtime.end({}); }
  catch (error) { failedEndError = error; }
  assert(failedEndError?.code === 'request_failed'
    && failedEndGame.runtime.state === 'degraded'
    && failedEndEnvironment.intervals.size === 2,
  'failed runtime end was treated as ended instead of retryable and monitored');
  rejectEnd = false;
  await failedEndGame.runtime.end({});
  assert(failedEndGame.runtime.state === 'ended' && failedEndEnvironment.intervals.size === 0,
    'retrying a failed runtime end did not close the route lifecycle');
  failedEndGame.dispose();

  const cancellationEnvironment = createEnvironment();
  const blockedHeartbeat = deferred();
  let heartbeatAborted = false;
  const cancellationTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'cancel-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'cancel-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'cancel-session', characterName: '' }; },
    heartbeat(_payload, options = {}) {
      options.signal?.addEventListener('abort', () => {
        heartbeatAborted = true;
        blockedHeartbeat.reject(Object.assign(new Error('aborted'), { code: 'cancelled' }));
      }, { once: true });
      return blockedHeartbeat.promise;
    },
    async drain() { return { ok: true, outputs: [] }; },
    dispose() {},
  };
  const cancellationGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-cancel',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: cancellationTransport,
    windowImpl: cancellationEnvironment.windowImpl,
    documentImpl: cancellationEnvironment.documentImpl,
  });
  cancellationGame.runtime.configure({
    payload: () => ({}),
    heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
    outputs: { intervalMs: 700, timeoutMs: 8000 },
  });
  await cancellationGame.runtime.start({});
  await Promise.resolve();
  cancellationGame.dispose();
  await Promise.resolve();
  assert(heartbeatAborted, 'dispose did not abort an in-flight managed heartbeat');
  assert(cancellationEnvironment.intervals.size === 0,
    'dispose did not release managed lifecycle timers');
  assert(!cancellationEnvironment.documentListeners.has('visibilitychange'),
    'dispose did not release managed lifecycle listeners');

  const transitionEnvironment = createEnvironment();
  const blockedTransitionStart = deferred();
  const blockedTransitionEnd = deferred();
  let transitionStartAborted = false;
  let transitionEndCalls = 0;
  const transitionTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'transition-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'transition-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'transition-session', characterName: '' }; },
    start(_payload, options = {}) {
      options.signal?.addEventListener('abort', () => {
        transitionStartAborted = true;
        blockedTransitionStart.reject(Object.assign(new Error('aborted'), { code: 'cancelled' }));
      }, { once: true });
      return blockedTransitionStart.promise;
    },
    end() { transitionEndCalls += 1; return blockedTransitionEnd.promise; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    dispose() {},
  };
  const transitionGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-transition',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: transitionTransport,
    windowImpl: transitionEnvironment.windowImpl,
    documentImpl: transitionEnvironment.documentImpl,
  });
  transitionGame.runtime.configure({
    payload: () => ({}),
    heartbeat: false,
    outputs: { intervalMs: 700, timeoutMs: 8000 },
    pageExit: false,
  });
  const supersededStart = transitionGame.runtime.start({});
  await Promise.resolve();
  const transitionEnd = transitionGame.runtime.end({});
  await Promise.resolve();
  assert(!transitionStartAborted && transitionEndCalls === 0,
    'runtime end overtook or cancelled the in-flight start request');
  blockedTransitionStart.resolve({
    ok: true,
    state: { game_route_active: true, session_id: 'transition-session' },
  });
  await supersededStart;
  await new Promise((resolve) => setImmediate(resolve));
  assert(transitionGame.runtime.state === 'ending',
    'runtime end did not begin after the start request settled');
  assert(transitionEndCalls === 1,
    'runtime end was not sent exactly once after start settlement');
  assert(transitionEnvironment.intervals.size === 0,
    'cancelled start completion restarted runtime monitoring');
  blockedTransitionEnd.resolve({ ok: true });
  await transitionEnd;
  assert(transitionGame.runtime.state === 'ended' && transitionEnvironment.intervals.size === 0,
    'runtime end left monitoring resident after superseding start');
  transitionGame.dispose();

  const staleSuccessEnvironment = createEnvironment();
  const blockedStaleStart = deferred();
  const blockedStaleEnd = deferred();
  const staleSuccessTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'stale-success', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'stale-success', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'stale-success', characterName: 'stale' }; },
    start() { return blockedStaleStart.promise; },
    end() { return blockedStaleEnd.promise; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    dispose() {},
  };
  const staleSuccessGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-stale-success',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: staleSuccessTransport,
    windowImpl: staleSuccessEnvironment.windowImpl,
    documentImpl: staleSuccessEnvironment.documentImpl,
  });
  staleSuccessGame.runtime.configure({
    heartbeat: false,
    outputs: { intervalMs: 700, timeoutMs: 8000 },
  });
  const staleStart = staleSuccessGame.runtime.start({});
  await Promise.resolve();
  const staleEnd = staleSuccessGame.runtime.end({});
  blockedStaleStart.resolve({ ok: true, state: { game_route_active: true, lanlan_name: 'stale' } });
  await staleStart;
  await new Promise((resolve) => setImmediate(resolve));
  assert(staleSuccessGame.runtime.state === 'ending' && staleSuccessEnvironment.intervals.size === 0,
    'stale successful start completion replaced the newer lifecycle state');
  blockedStaleEnd.resolve({ ok: true });
  await staleEnd;
  assert(staleSuccessGame.runtime.state === 'ended' && staleSuccessEnvironment.intervals.size === 0,
    'stale successful start completion left monitoring resident after end');
  staleSuccessGame.dispose();

  const exitEnvironment = createEnvironment();
  let exitEndCalls = 0;
  let exitDisposed = 0;
  let exitPreserved = false;
  const exitTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'exit-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'exit-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'exit-session', characterName: '' }; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end(payload, options = {}) {
      exitEndCalls += 1;
      assert(payload.reason === 'pagehide', 'page-exit payload factory was not used');
      assert(payload.sdk_route_instance_id
        && payload.sdk_route_instance_ids?.includes(payload.sdk_route_instance_id),
      'page exit did not include the active route generation candidate');
      assert(options.useBeacon === true, 'page exit did not request beacon delivery');
      return { ok: true };
    },
    dispose(options = {}) {
      exitDisposed += 1;
      exitPreserved = options.preservePendingOperations?.includes('route_end') === true;
    },
  };
  const exitGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-page-exit',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: exitTransport,
    windowImpl: exitEnvironment.windowImpl,
    documentImpl: exitEnvironment.documentImpl,
  });
  const pageExitEvents = [];
  exitGame.events.on('page-exit', (event) => pageExitEvents.push(event));
  exitGame.runtime.configure({
    payload: () => ({}),
    heartbeat: { intervalMs: 2500, timeoutMs: 4500 },
    outputs: { intervalMs: 700, timeoutMs: 8000 },
    pageExit: {
      payload: ({ type }) => ({ reason: type }),
    },
  });
  assert(exitEnvironment.windowListeners.get('pagehide')?.size === 1,
    'page-exit lifecycle was not installed before runtime start');
  await exitGame.runtime.start({});
  exitEnvironment.windowImpl.dispatch('pagehide');
  exitEnvironment.windowImpl.dispatch('beforeunload');
  await Promise.resolve();
  await Promise.resolve();
  assert(pageExitEvents.length === 1, 'page exit was not emitted exactly once');
  assert(exitEndCalls === 1, 'page exit did not end the runtime exactly once');
  assert(exitDisposed === 1 && exitPreserved, 'page exit did not preserve route-end during disposal');
  assert(exitGame.disposed, 'page exit did not dispose the SDK client');
  assert(exitEnvironment.intervals.size === 0 && exitEnvironment.windowListeners.size === 0,
    'page exit left managed timers or listeners resident');

  const startingExitEnvironment = createEnvironment();
  const blockedStart = deferred();
  let startAborted = false;
  let startingExitEndCalls = 0;
  let startingExitDisposed = 0;
  const startingExitTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'starting-exit', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'starting-exit', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'starting-exit', characterName: '' }; },
    start(_payload, options = {}) {
      options.signal?.addEventListener('abort', () => {
        startAborted = true;
        blockedStart.reject(Object.assign(new Error('aborted'), { code: 'cancelled' }));
      }, { once: true });
      return blockedStart.promise;
    },
    async end(_payload, options = {}) {
      startingExitEndCalls += 1;
      assert(options.useBeacon === true, 'starting page exit did not request beacon delivery');
      return { ok: true };
    },
    dispose() { startingExitDisposed += 1; },
  };
  const startingExitGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-starting-exit',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: startingExitTransport,
    windowImpl: startingExitEnvironment.windowImpl,
    documentImpl: startingExitEnvironment.documentImpl,
  });
  startingExitGame.runtime.configure({
    payload: () => ({ reason: 'starting-exit' }),
    heartbeat: false,
    outputs: false,
    pageExit: true,
  });
  const pageExitStart = startingExitGame.runtime.start({})
    .then(() => null, (error) => error);
  await Promise.resolve();
  startingExitEnvironment.windowImpl.dispatch('pagehide');
  await Promise.resolve();
  assert(startAborted && startingExitEndCalls === 1 && startingExitDisposed === 1,
    'page exit did not synchronously dispatch end before cancelling the in-flight start');
  assert(startingExitGame.disposed,
    'page exit did not dispose immediately after the synchronous end dispatch');
  blockedStart.resolve({
    ok: true,
    state: { game_route_active: true, session_id: 'starting-exit' },
  });
  const pageExitStartError = await pageExitStart;
  await new Promise((resolve) => setImmediate(resolve));
  assert(pageExitStartError?.code === 'cancelled',
    'page exit did not settle the abandoned runtime start as cancelled');
  assert(startingExitEndCalls === 1 && startingExitDisposed === 1,
    'page exit during runtime start did not end and dispose exactly once');
  assert(startingExitEnvironment.windowListeners.size === 0,
    'page exit during runtime start left listeners resident');

  const routeTruthEnvironment = createEnvironment();
  let routeStartMode = 'reject';
  let routeStartCalls = 0;
  let routeTruthLastStartPayload = null;
  let dialogueCalls = 0;
  let quickLineCalls = 0;
  let speechCalls = 0;
  let speechMirrorCalls = 0;
  const speechPayloads = [];
  const speechMirrorPayloads = [];
  let memorySubmitCalls = 0;
  const routeTruthTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'route-truth-session', characterName: 'Yui' }; },
    getRuntimeState() { return { sessionId: 'route-truth-session', characterName: 'Yui' }; },
    applyRuntimeState() { return { sessionId: 'route-truth-session', characterName: 'Yui' }; },
    async start(payload) {
      routeStartCalls += 1;
      routeTruthLastStartPayload = payload;
      if (routeStartMode === 'throw') throw Object.assign(new Error('start failed'), { code: 'network_error' });
      if (routeStartMode === 'inactive') {
        return { ok: true, state: { game_route_active: false, session_id: 'route-truth-session' } };
      }
      return routeStartMode === 'active'
        ? { ok: true, state: { game_route_active: true, session_id: 'route-truth-session', lanlan_name: 'Yui' } }
        : { ok: false, reason: 'start-rejected' };
    },
    async end() { return { ok: false, reason: 'end-rejected' }; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async requestDialogue() { dialogueCalls += 1; return { ok: true, line: 'route active' }; },
    async getQuickLines() { quickLineCalls += 1; return { ok: true, lines: {} }; },
    async configureGameMemoryConsent() { return { ok: true }; },
    async submitGameMemory() { memorySubmitCalls += 1; return { ok: true, accepted: true }; },
    startSpeechOutputBridge() { return true; },
    stopSpeechOutputBridge() {},
    async preloadSpeechOutput() { return { ok: true, results: [] }; },
    async requestSpeechOutput(payload) {
      speechCalls += 1;
      speechPayloads.push(payload);
      return { ok: true, speech_id: 'route-truth-speech' };
    },
    async mirrorSpeechOutput(payload) {
      speechMirrorCalls += 1;
      speechMirrorPayloads.push(payload);
      return { ok: true, mirrored: true };
    },
    dispose() {},
  };
  const routeTruthGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-route-truth',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'dialogue', 'quick-lines', 'speech-output', 'memory'],
  }, {
    transport: routeTruthTransport,
    windowImpl: routeTruthEnvironment.windowImpl,
    documentImpl: routeTruthEnvironment.documentImpl,
  });
  await routeTruthGame.memory.configureConsent(true);
  await routeTruthGame.runtime.start({});
  const activeOnlyCalls = [
    () => routeTruthGame.dialogue.request({ event: 'after-rejected-start' }),
    () => routeTruthGame.memory.submit({ summary: 'after rejected start' }),
  ];
  for (const invoke of activeOnlyCalls) {
    let routeError = null;
    try { await invoke(); } catch (error) { routeError = error; }
    assert(routeError?.code === 'invalid_state',
      'a rejected runtime start permitted an active-route capability');
  }
  await routeTruthGame.dialogue.quickLines({ kind: 'pregame-after-rejected-start' });
  await routeTruthGame.speech.speak({ text: 'after rejected start' });
  await routeTruthGame.speech.mirror({ text: 'after rejected start' });
  assert(dialogueCalls === 0 && quickLineCalls === 1 && speechCalls === 1
    && speechMirrorCalls === 1 && memorySubmitCalls === 0,
  'a rejected runtime start leaked route-bound work or blocked pre-route capabilities');
  assert(speechPayloads[0].sdk_route_instance_id === undefined
    && speechMirrorPayloads[0].sdk_route_instance_id === undefined,
  'pre-route speech after a rejected start invented a route generation');

  routeStartMode = 'throw';
  let failedStartError = null;
  try { await routeTruthGame.runtime.start({}); } catch (error) { failedStartError = error; }
  assert(failedStartError?.code === 'network_error', 'failed start did not preserve its transport error');
  for (const invoke of activeOnlyCalls) {
    let routeError = null;
    try { await invoke(); } catch (error) { routeError = error; }
    assert(routeError?.code === 'invalid_state',
      'a failed runtime start permitted an active-route capability');
  }
  await routeTruthGame.dialogue.quickLines({ kind: 'pregame-after-failed-start' });
  await routeTruthGame.speech.speak({ text: 'after failed start' });
  await routeTruthGame.speech.mirror({ text: 'after failed start' });
  assert(speechPayloads[1].sdk_route_instance_id === routeTruthLastStartPayload.sdk_route_instance_id
    && speechMirrorPayloads[1].sdk_route_instance_id === routeTruthLastStartPayload.sdk_route_instance_id,
  'speech after an uncertain start did not preserve the bounded unresolved generation');

  routeStartMode = 'active';
  await routeTruthGame.runtime.start({});
  await routeTruthGame.runtime.end({});
  assert(routeTruthGame.runtime.state === 'degraded',
    'a rejected runtime end did not remain retryable');
  await routeTruthGame.dialogue.request({ event: 'after-rejected-end' });
  await routeTruthGame.dialogue.quickLines({ kind: 'goal' });
  await routeTruthGame.speech.speak({ text: 'after rejected end' });
  await routeTruthGame.speech.mirror({ text: 'after rejected end' });
  await routeTruthGame.memory.submit({ summary: 'after rejected end' });
  assert(dialogueCalls === 1 && quickLineCalls === 3 && speechCalls === 3
    && speechMirrorCalls === 3 && memorySubmitCalls === 1,
    'a failed runtime end discarded a route that may still be active');
  assert(speechPayloads[2].sdk_route_instance_id
    && speechMirrorPayloads[2].sdk_route_instance_id,
  'established-route speech omitted the active route generation');
  const startsBeforeInvalidRetry = routeStartCalls;
  let establishedStartError = null;
  try { await routeTruthGame.runtime.start({}); } catch (error) { establishedStartError = error; }
  assert(establishedStartError?.code === 'invalid_state'
    && routeStartCalls === startsBeforeInvalidRetry,
  'a failed-end established route permitted a second start');
  routeTruthGame.dispose();

  const inactiveStartEnvironment = createEnvironment();
  routeStartMode = 'inactive';
  const inactiveStartGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-route-inactive-start',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'dialogue'],
  }, {
    transport: routeTruthTransport,
    windowImpl: inactiveStartEnvironment.windowImpl,
    documentImpl: inactiveStartEnvironment.documentImpl,
  });
  await inactiveStartGame.runtime.start({});
  assert(inactiveStartGame.runtime.state === 'inactive',
    'an inactive successful start response entered running');
  let inactiveDialogueError = null;
  try { await inactiveStartGame.dialogue.request({ event: 'inactive-start' }); }
  catch (error) { inactiveDialogueError = error; }
  assert(inactiveDialogueError?.code === 'invalid_state',
    'an inactive successful start response established a route');
  inactiveStartGame.dispose();

  // A runtime-output handler that never settles used to pin
  // `outputLifecycle.inFlight` at true forever: every later poll returned null
  // with no error, no event and no log, so declared controls stopped arriving
  // (drain is the only control-bridge producer) and the backend's bounded
  // pending ring silently dropped everything past its cap. Only an explicit
  // stopMonitoring/end/reset cleared it.
  const stuckEnvironment = createEnvironment();
  const stuckGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-stuck-handler',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: {
      ...transport,
      logger: logger(),
      async drain(payload) {
        return { ok: true, outputs: outputs.map((output) => ({ ...output })), payload };
      },
    },
    windowImpl: stuckEnvironment.windowImpl,
    documentImpl: stuckEnvironment.documentImpl,
  });
  // A truthy non-numeric limit used to survive as NaN: Number() produces it and
  // Math.min/Math.max preserve it, so every poll payload carried a non-finite
  // value that the trusted host's clone rejects before /route/drain -- one error
  // per tick and no output or control ever delivered.
  const malformedLimitConfig = stuckGame.runtime.configure({
    heartbeat: false,
    outputs: { intervalMs: 700, limit: 'fifty' },
    pageExit: false,
  });
  assert(malformedLimitConfig.outputs.limit === 50,
    'a malformed runtime output limit was stored instead of falling back');
  assert(stuckGame.runtime.configure({
    heartbeat: false, outputs: { intervalMs: 700, limit: 500 }, pageExit: false,
  }).outputs.limit === 50, 'an over-large runtime output limit was not clamped');
  assert(stuckGame.runtime.configure({
    heartbeat: false, outputs: { intervalMs: 700, limit: 7 }, pageExit: false,
  }).outputs.limit === 7, 'a valid runtime output limit was not honoured');
  stuckGame.runtime.configure({ heartbeat: false, outputs: { intervalMs: 700 }, pageExit: false });
  await stuckGame.runtime.start({ mode: 'stuck' });
  for (let tick = 0; tick < 20; tick += 1) await Promise.resolve();
  const stuckForever = new Promise(() => {});
  const releaseStuckHandler = stuckGame.events.on('runtime-output', () => stuckForever);
  const stuckPoll = stuckGame.runtime.pollOutputs();
  let stuckPollSettled = false;
  void stuckPoll.then(() => { stuckPollSettled = true; }, () => { stuckPollSettled = true; });
  let budgetTimersFired = 0;
  for (let round = 0; round < 40 && !stuckPollSettled; round += 1) {
    for (let tick = 0; tick < 5; tick += 1) await Promise.resolve();
    for (const [timerId, timer] of Array.from(stuckEnvironment.timeouts.entries())) {
      if (timer.delayMs !== 60000) continue;
      stuckEnvironment.timeouts.delete(timerId);
      budgetTimersFired += 1;
      timer.handler();
    }
  }
  // Asserted BEFORE awaiting: an unbounded await never settles, and with only
  // mock timers pending Node would drain its event loop and exit 0 -- a hang
  // that reads as a pass.
  assert(stuckPollSettled,
    'output polling stayed pinned in flight while a handler refused to settle');
  await stuckPoll;
  assert(budgetTimersFired === 2,
    'a stuck runtime-output handler was awaited without a per-handler deadlock budget');
  assert(stuckEnvironment.consoleErrors.some((args) => (
    String(args[0]).includes('runtime-output listener exceeded the handler budget')
  )), 'abandoning a stuck runtime-output handler was silent');
  releaseStuckHandler();
  const recoveredEnvelopes = [];
  stuckGame.events.on('runtime-output', (event) => {
    recoveredEnvelopes.push(event);
    // A thenable, so the budget path runs for a handler that settles normally.
    return Promise.resolve();
  });
  const recoveredPoll = await stuckGame.runtime.pollOutputs();
  assert(recoveredPoll !== null && recoveredEnvelopes.length === 2,
    'output polling stayed wedged after a stuck handler was abandoned');
  assert(stuckEnvironment.timeouts.size === 0,
    'handler budget timers were left armed after their handlers settled');
  assert(stuckEnvironment.consoleErrors.filter((args) => (
    String(args[0]).includes('exceeded the handler budget')
  )).length === 2, 'a handler that settled normally was reported as exceeding the budget');
  stuckGame.dispose();

  // Without crypto.randomUUID the generation id falls back to
  // `${Date.now()}-${sequence}`, and every SDK client starts its sequence at 1.
  // Two windows opening in the same millisecond therefore minted the SAME
  // generation, and the server cannot tell them apart -- one route silently
  // supersedes or answers for the other.
  const realDateNow = Date.now;
  const collisionStarts = [];
  const collisionTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'collision-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'collision-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'collision-session', characterName: '' }; },
    async start(payload) {
      collisionStarts.push(payload.sdk_route_instance_id);
      return { ok: true, state: { game_route_active: true, lanlan_name: 'Yui' }, payload };
    },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end() { return { ok: true }; },
    dispose() {},
  };
  try {
    // Same millisecond for every client, and no randomUUID anywhere.
    Date.now = () => 1700000000000;
    for (let client = 0; client < 4; client += 1) {
      const collisionEnvironment = createEnvironment();
      const collisionGame = await window.NekoMiniGame.connect({
        id: 'lifecycle-generation-collision',
        version: '1.0.0',
        requiredCapabilities: ['runtime', 'logging'],
      }, {
        transport: collisionTransport,
        windowImpl: collisionEnvironment.windowImpl,
        documentImpl: collisionEnvironment.documentImpl,
      });
      collisionGame.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
      await collisionGame.runtime.start({ client });
      await collisionGame.runtime.end({ reason: 'collision-probe' });
      collisionGame.dispose();
    }
  } finally {
    Date.now = realDateNow;
  }
  assert(collisionStarts.length === 4 && collisionStarts.every(Boolean),
    'the generation collision probe did not mint four ids');
  assert(new Set(collisionStarts).size === 4,
    `separate SDK clients minted colliding route generations: ${JSON.stringify(collisionStarts)}`);

  // The backend deletes a drained batch at response-construction time, so one
  // output that `publishRuntimeEvent` cannot represent used to take every
  // remaining output in that batch with it -- the payload validation runs BEFORE
  // the per-handler try/catch and THROWS. The host feeds this from the game's own
  // state snapshot, which the backend never bounds, so it is a deterministic
  // every-poll failure rather than a rare one.
  const isolationEnvironment = createEnvironment();
  const isolationOutputs = [
    { type: 'game_llm_result', text: 'first' },
    { type: 'game_llm_result', blob: 'y'.repeat(300 * 1024) },
    { type: 'game_llm_result', text: 'third' },
  ];
  const isolationGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-output-isolation',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: {
      ...transport,
      logger: logger(),
      async drain(payload) {
        return { ok: true, outputs: isolationOutputs.map((item) => ({ ...item })), payload };
      },
    },
    windowImpl: isolationEnvironment.windowImpl,
    documentImpl: isolationEnvironment.documentImpl,
  });
  isolationGame.runtime.configure({ heartbeat: false, outputs: { intervalMs: 700 }, pageExit: false });
  await isolationGame.runtime.start({ mode: 'isolation' });
  // start() kicks an initial poll; let it settle or pollOutputs() returns null.
  for (let tick = 0; tick < 20; tick += 1) await Promise.resolve();
  const isolationDelivered = [];
  isolationGame.events.on('runtime-output', (event) => isolationDelivered.push(event.payload.text));
  const isolationPoll = await isolationGame.runtime.pollOutputs();
  assert(isolationPoll !== null, 'the isolation probe never drained');
  assert(isolationDelivered.join(',') === 'first,third',
    `one unrepresentable output took the rest of the batch with it: ${JSON.stringify(isolationDelivered)}`);
  assert(isolationEnvironment.consoleErrors.some((args) => (
    String(args[0]).includes('runtime-output could not be published')
  )), 'an output that could not be published was dropped silently');
  isolationGame.dispose();

  // A generation-mismatch heartbeat is a REJECTED request that still carries
  // authoritative news: another client superseded this route while keeping the
  // same session_id. Skipping it left this client locally `running` forever.
  const mismatchEnvironment = createEnvironment();
  const mismatchGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-generation-mismatch',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: {
      ...transport,
      logger: logger(),
      async heartbeat() {
        return { ok: false, active: false, reason: 'route_instance_id_mismatch' };
      },
    },
    windowImpl: mismatchEnvironment.windowImpl,
    documentImpl: mismatchEnvironment.documentImpl,
  });
  mismatchGame.runtime.configure({ heartbeat: { intervalMs: 2500 }, outputs: false, pageExit: false });
  await mismatchGame.runtime.start({ mode: 'mismatch' });
  // Asserted before the flush: start() establishes the route, and the managed
  // lifecycle's first heartbeat is what carries the supersede news.
  assert(mismatchGame.runtime.state === 'running', 'the mismatch probe did not start running');
  for (let tick = 0; tick < 20; tick += 1) await Promise.resolve();
  assert(mismatchGame.runtime.state === 'inactive',
    `a superseded generation left the client running: ${mismatchGame.runtime.state}`);
  mismatchGame.dispose();

  // `heartbeat`/`outputs` are `false | object` in the published .d.ts. A truthy
  // non-object -- `heartbeat: 'disabled'`, `outputs: 'off'` -- used to fall
  // through as "enabled with defaults": the author's intent inverted in silence,
  // which is the one outcome a typo must never produce.
  const monitoringEnvironment = createEnvironment();
  const monitoringGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-monitoring-shape',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: { ...transport, logger: logger() },
    windowImpl: monitoringEnvironment.windowImpl,
    documentImpl: monitoringEnvironment.documentImpl,
  });
  // `0` and `null` are the same footgun as the strings, not "absent": today they
  // all take the `|| {}` fallback and come out as "enabled with defaults", so an
  // author writing `heartbeat: 0` to mean "off" gets it ON. Only `undefined`
  // means absent.
  for (const [key, badValue] of [
    ['heartbeat', 'disabled'],
    ['outputs', 'off'],
    ['heartbeat', 0],
    ['outputs', null],
    ['outputs', true],
  ]) {
    let monitoringError = null;
    try { monitoringGame.runtime.configure({ [key]: badValue, pageExit: false }); }
    catch (error) { monitoringError = error; }
    assert(monitoringError?.code === 'invalid_request',
      `a non-object ${key} (${JSON.stringify(badValue)}) was silently treated as defaults`);
  }
  // pageExit had its own shape check but skipped `null` (and let any object
  // through): `pageExit: null` normalized into "disabled" and `new Date()` into
  // "enabled with defaults" -- both the opposite of what a typo meant to say.
  for (const badPageExit of [null, new Date(), [], 'off', 0]) {
    let pageExitError = null;
    try { monitoringGame.runtime.configure({ pageExit: badPageExit }); }
    catch (error) { pageExitError = error; }
    assert(pageExitError?.code === 'invalid_request',
      `a malformed pageExit (${Object.prototype.toString.call(badPageExit)}) was accepted`);
  }
  // Absent is still absent.
  monitoringGame.runtime.configure({ pageExit: false });
  // ...and the declared shapes still pass, so the loop above cannot be
  // satisfied by an implementation that rejects pageExit outright.
  monitoringGame.runtime.configure({ pageExit: true });
  monitoringGame.runtime.configure({ pageExit: {} });
  // The declared shapes still work.
  monitoringGame.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
  monitoringGame.runtime.configure({
    heartbeat: { intervalMs: 2500 }, outputs: { intervalMs: 700 }, pageExit: false,
  });
  monitoringGame.dispose();

  // runtime.end(..., { timeoutMs }) used to observe only the caller's abort
  // signal while start was still pending, so a built-in start that spends many
  // seconds building pregame context made the advertised deadline describe a
  // request that had not been issued yet.
  const pendingStartEnvironment = createEnvironment();
  const pendingStartTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'pending-start-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'pending-start-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'pending-start-session', characterName: '' }; },
    start() { return new Promise(() => {}); },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end() { return { ok: true }; },
    dispose() {},
  };
  const pendingStartGame = await window.NekoMiniGame.connect({
    id: 'lifecycle-pending-start-end',
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging'],
  }, {
    transport: pendingStartTransport,
    windowImpl: pendingStartEnvironment.windowImpl,
    documentImpl: pendingStartEnvironment.documentImpl,
  });
  pendingStartGame.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
  const neverSettlingStart = pendingStartGame.runtime.start({});
  void neverSettlingStart.then(() => {}, () => {});
  for (let tick = 0; tick < 20; tick += 1) await Promise.resolve();
  let pendingEndError = null;
  let pendingEndSettled = false;
  const pendingEnd = pendingStartGame.runtime.end({}, { timeoutMs: 1000 });
  void pendingEnd.then(
    () => { pendingEndSettled = true; },
    (error) => { pendingEndSettled = true; pendingEndError = error; },
  );
  for (let round = 0; round < 40 && !pendingEndSettled; round += 1) {
    for (let tick = 0; tick < 5; tick += 1) await Promise.resolve();
    for (const [timerId, timer] of Array.from(pendingStartEnvironment.timeouts.entries())) {
      if (timer.delayMs !== 1000) continue;
      pendingStartEnvironment.timeouts.delete(timerId);
      timer.handler();
    }
  }
  // Asserted BEFORE awaiting: without the deadline nothing ever settles this,
  // and with only mock timers pending Node drains its event loop and exits 0 --
  // a hang that reads as a pass.
  assert(pendingEndSettled,
    'runtime.end ignored its timeout while the start it waits on stayed pending');
  await pendingEnd.catch(() => {});
  assert(pendingEndError?.code === 'timeout',
    'the start-settlement wait did not fail with the end request timeout');
  assert(pendingStartEnvironment.timeouts.size === 0,
    'the start-settlement deadline timer was left armed');
  pendingStartGame.dispose();

  // ...and a start that DOES settle is charged to the same budget, so the whole
  // end() honours the advertised deadline instead of spending it twice.
  const budgetEnvironment = createEnvironment();
  const budgetEndOptions = [];
  let releaseBudgetStart = null;
  const budgetTransport = {
    ...transport,
    logger: logger(),
    resetRuntime() { return { sessionId: 'budget-session', characterName: '' }; },
    getRuntimeState() { return { sessionId: 'budget-session', characterName: '' }; },
    applyRuntimeState() { return { sessionId: 'budget-session', characterName: '' }; },
    start(payload) {
      return new Promise((resolve) => {
        releaseBudgetStart = () => resolve({
          ok: true,
          state: { game_route_active: true, lanlan_name: 'Yui' },
          payload,
        });
      });
    },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    async end(payload, options) {
      budgetEndOptions.push(options);
      return { ok: true };
    },
    dispose() {},
  };
  const budgetRealDateNow = Date.now;
  let budgetFakeNow = 1700000000000;
  try {
    Date.now = () => budgetFakeNow;
    const budgetGame = await window.NekoMiniGame.connect({
      id: 'lifecycle-end-budget',
      version: '1.0.0',
      requiredCapabilities: ['runtime', 'logging'],
    }, {
      transport: budgetTransport,
      windowImpl: budgetEnvironment.windowImpl,
      documentImpl: budgetEnvironment.documentImpl,
    });
    budgetGame.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
    const budgetStart = budgetGame.runtime.start({});
    void budgetStart.then(() => {}, () => {});
    for (let tick = 0; tick < 20; tick += 1) await Promise.resolve();
    const budgetEnd = budgetGame.runtime.end({}, { timeoutMs: 1000 });
    for (let tick = 0; tick < 20; tick += 1) await Promise.resolve();
    budgetFakeNow += 600;
    releaseBudgetStart();
    await budgetStart;
    await budgetEnd;
    assert(budgetEndOptions.length === 1,
      'the end request was not issued after the pending start settled');
    assert(budgetEndOptions[0].timeoutMs === 400,
      'the start-settlement wait was not charged to the end request timeout budget');
    budgetGame.dispose();
  } finally {
    Date.now = budgetRealDateNow;
  }

  process.stdout.write('mini-game lifecycle runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
