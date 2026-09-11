const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function logger() {
  return {
    log() {},
    info() {},
    warn() {},
    error() {},
    async enable() { return { ok: true }; },
    async enableAfterRouteStart() { return { ok: true }; },
    async flush() { return { ok: true }; },
    reset() {},
  };
}

function abortError() {
  const error = new Error('aborted');
  error.name = 'AbortError';
  return error;
}

function createTransport(options = {}) {
  let runtimeState = { sessionId: options.sessionId || 'context-memory-session', characterName: 'Yui' };
  const contextCalls = [];
  const consentCalls = [];
  const memoryCalls = [];
  const pendingContext = new Set();
  const pendingConsent = new Set();
  const pendingStorage = new Set();
  const storageValues = new Map();
  const storageCalls = [];
  let disposed = 0;
  let lastStartPayload = null;
  let contextPendingMode = false;
  let consentPendingMode = false;
  let storagePendingMode = false;
  let storageEnvelopeExtra = null;

  function pendingRequest(bucket, requestOptions) {
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject };
      bucket.add(entry);
      const rejectOnAbort = () => {
        bucket.delete(entry);
        reject(abortError());
      };
      if (requestOptions.signal?.aborted) rejectOnAbort();
      else requestOptions.signal?.addEventListener('abort', rejectOnAbort, { once: true });
    });
  }

  const transport = {
    logger: logger(),
    configureLogger() {},
    connectGame(request) {
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'context-memory-test-host',
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
    getRuntimeState() { return runtimeState; },
    applyRuntimeState(state) {
      runtimeState = { ...runtimeState, ...state };
      return runtimeState;
    },
    resetRuntime({ newSession } = {}) {
      runtimeState = {
        sessionId: newSession ? `${runtimeState.sessionId}-next` : runtimeState.sessionId,
        characterName: runtimeState.characterName,
      };
      return runtimeState;
    },
    async start(payload) {
      lastStartPayload = payload;
      return { ok: true, state: { game_route_active: true }, payload };
    },
    async end(payload) { return { ok: true, payload }; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    readGameContext(payload, requestOptions = {}) {
      contextCalls.push({ payload, requestOptions });
      if (contextPendingMode) return pendingRequest(pendingContext, requestOptions);
      return Promise.resolve({ ok: true, scopes: payload.scopes, context: { public: 'bounded' } });
    },
    configureGameMemoryConsent(payload, requestOptions = {}) {
      consentCalls.push({ payload, requestOptions });
      if (consentPendingMode) return pendingRequest(pendingConsent, requestOptions);
      return Promise.resolve({ ok: true, enabled: payload.enabled });
    },
    submitGameMemory(payload, requestOptions = {}) {
      memoryCalls.push({ payload, requestOptions });
      return Promise.resolve({ ok: true, accepted: true });
    },
    requestGameStorage(operation, payload, requestOptions = {}) {
      storageCalls.push({ operation, payload, requestOptions });
      if (storagePendingMode) return pendingRequest(pendingStorage, requestOptions);
      if (operation === 'set') storageValues.set(payload.key, payload.value);
      if (operation === 'delete') storageValues.delete(payload.key);
      if (operation === 'clear') storageValues.clear();
      if (operation === 'get') {
        const extra = storageEnvelopeExtra;
        storageEnvelopeExtra = null;
        return Promise.resolve({
          ok: true,
          found: storageValues.has(payload.key),
          value: storageValues.get(payload.key),
          ...(extra || {}),
        });
      }
      if (operation === 'list') {
        const keys = [...storageValues.keys()]
          .filter((key) => key.startsWith(payload.prefix))
          .slice(0, payload.limit);
        return Promise.resolve({ ok: true, keys });
      }
      return Promise.resolve({ ok: true });
    },
    dispose() { disposed += 1; },
  };
  return {
    transport,
    contextCalls,
    consentCalls,
    memoryCalls,
    pendingContext,
    pendingConsent,
    pendingStorage,
    storageCalls,
    setContextPending(value) { contextPendingMode = value; },
    setConsentPending(value) { consentPendingMode = value; },
    setStoragePending(value) { storagePendingMode = value; },
    setNextStorageEnvelopeExtra(value) { storageEnvelopeExtra = value; },
    get disposed() { return disposed; },
    get lastStartPayload() { return lastStartPayload; },
  };
}

async function connectClient(host, id = 'context-memory-test') {
  return window.NekoMiniGame.connect({
    id,
    version: '1.0.0',
    requiredCapabilities: ['runtime', 'logging', 'context-read', 'memory', 'storage'],
  }, { transport: host.transport });
}

async function main() {
  global.window = { console: { error() {} } };
  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });

  const host = createTransport();
  const game = await connectClient(host);
  const contextResponse = await game.context.read([
    'character-public',
    'recent-chat-summary',
    'character-public',
  ]);
  assert(contextResponse.ok, 'context read failed');
  assert(host.contextCalls.length === 1, 'context request did not use the trusted host');
  assert(host.contextCalls[0].payload.scopes.join(',') === 'character-public,recent-chat-summary',
    'context scopes were not bounded and deduplicated');
  assert(host.contextCalls[0].payload.session_id === 'context-memory-session',
    'context request did not bind the runtime session');
  assert(Object.isFrozen(contextResponse.data), 'context response was not cloned as immutable data');

  await game.storage.set('settings/difficulty', { level: 3 });
  const stored = await game.storage.get('settings/difficulty');
  assert(stored.data.found && stored.data.value.level === 3,
    'namespaced storage did not round-trip bounded JSON');
  const listed = await game.storage.list({ prefix: 'settings/', limit: 10 });
  assert(listed.data.keys.join(',') === 'settings/difficulty',
    'namespaced storage list did not apply its prefix');
  await game.storage.delete('settings/difficulty');

  // Whatever the write path accepted, the read path must give back. The read
  // used to measure the host's whole {ok, found, value} envelope against the
  // value's own budget, so the wrapper was charged to the value: 33 bytes of
  // envelope against the 64 KiB cap, three clone nodes against the fixed 2048,
  // and one extra level against the fixed depth of 16. A save blob sized to the
  // round budget, or nested to the depth limit, stored fine and was then
  // permanently unreadable -- and for a game that is every user, forever,
  // because the size is a property of the game, not of the run.
  const maximalBlob = { blob: 'x'.repeat(65525) };
  await game.storage.set('saves/maximal', maximalBlob);
  const readBackBlob = await game.storage.get('saves/maximal');
  assert(readBackBlob.data.value?.blob?.length === 65525,
    'a value accepted at its exact byte budget could not be read back');
  let deepValue = { leaf: true };
  for (let level = 0; level < 15; level += 1) deepValue = { nested: deepValue };
  await game.storage.set('saves/deep', deepValue);
  const readBackDeep = await game.storage.get('saves/deep');
  assert(readBackDeep.data.found === true && readBackDeep.data.value?.nested,
    'a value accepted at its exact depth budget could not be read back');
  // Splitting the value out of the envelope must not leave the rest of the
  // envelope unmeasured: a buggy or hostile host could otherwise ship megabytes
  // through a sibling field.
  await game.storage.set('saves/small', { level: 1 });
  host.setNextStorageEnvelopeExtra({ junk: 'x'.repeat(70000) });
  let envelopeOverflowError = null;
  try { await game.storage.get('saves/small'); }
  catch (error) { envelopeOverflowError = error; }
  assert(envelopeOverflowError?.code === 'invalid_request',
    'an oversized sibling field bypassed the storage response bound');
  // And the nested value keeps exactly its own 64 KiB bound: the fix moves the
  // wrapper out of the budget, it does not widen the budget.
  host.setNextStorageEnvelopeExtra({ value: 'x'.repeat(70000) });
  let oversizedValueError = null;
  try { await game.storage.get('saves/small'); }
  catch (error) { oversizedValueError = error; }
  assert(oversizedValueError?.code === 'invalid_request',
    'an oversized nested value bypassed the storage response bound');
  await game.storage.delete('saves/small');
  await game.storage.delete('saves/maximal');
  await game.storage.delete('saves/deep');

  // The local leaderboard persists through this same namespace under a
  // reserved prefix, and the public storage path takes none of the Web Locks
  // that serialise leaderboard read-modify-write, so a public write here could
  // silently clobber a board or drop a concurrent submission.
  for (const reservedOp of ['get', 'set', 'delete']) {
    let reservedError = null;
    try {
      await (reservedOp === 'set'
        ? game.storage.set('leaderboards/main', { forged: true })
        : game.storage[reservedOp]('leaderboards/main'));
    } catch (error) { reservedError = error; }
    assert(reservedError?.code === 'invalid_request',
      `storage.${reservedOp} accepted a key in the reserved leaderboard prefix`);
  }
  // list keeps its whole-namespace meaning on purpose.
  await game.storage.list({ prefix: 'leaderboards/' });
  let clearConfirmationError = null;
  try { await game.storage.clear(); }
  catch (error) { clearConfirmationError = error; }
  assert(clearConfirmationError?.code === 'invalid_request',
    'storage namespace clear did not require explicit confirmation');

  let preStartSubmitError = null;
  try { await game.memory.submit({ summary: 'too early' }); }
  catch (error) { preStartSubmitError = error; }
  assert(preStartSubmitError?.code === 'session_invalid',
    'memory submission before runtime start was not rejected');

  await game.memory.configureConsent(true);
  assert(game.memory.consent.enabled && game.memory.consent.configured && !game.memory.consent.locked,
    'memory consent was not configured before runtime start');
  assert(host.consentCalls[0].payload.enabled === true,
    'memory consent did not use the trusted host');
  await game.runtime.start({ mode: 'test' });
  const routeInstanceId = host.lastStartPayload.sdk_route_instance_id;
  assert(game.memory.consent.locked, 'memory consent did not lock at runtime start');

  await game.context.read(['current-state']);
  assert(host.contextCalls.at(-1).payload.sdk_route_instance_id === routeInstanceId,
    'active context read was not bound to the route generation');

  let lockedConsentError = null;
  try { await game.memory.configureConsent(false); }
  catch (error) { lockedConsentError = error; }
  assert(lockedConsentError?.code === 'consent_locked',
    'memory consent remained mutable after runtime start');

  const memoryResponse = await game.memory.submit({
    events: [{ type: 'round-ended', visible: true }],
    state: { score: [2, 1] },
    result: { winner: 'player' },
    summary: '玩家完成了一局测试。',
  });
  assert(memoryResponse.ok && host.memoryCalls.length === 1,
    'consented memory submission did not reach the trusted host');

  // The advertised byte limit is measured on the input, but the payload that
  // actually ships is a clone built from own enumerable properties. Anything
  // whose two observations disagree used to slip a far larger payload past the
  // gate. Strings have no length cap of their own inside the clone, so this
  // byte check is the only thing bounding them.
  const callsBeforeOversize = host.memoryCalls.length;
  const hugeString = 'x'.repeat(1024 * 1024);
  const toJsonDecoy = { summary: hugeString };
  Object.defineProperty(toJsonDecoy, 'toJSON', {
    enumerable: false,
    value: () => ({ summary: 'tiny' }),
  });
  let toJsonSizeError = null;
  try { await game.memory.submit(toJsonDecoy); }
  catch (error) { toJsonSizeError = error; }
  assert(toJsonSizeError?.code === 'invalid_request',
    'a non-enumerable toJSON hid an oversized memory submission from the size limit');

  // Not specific to toJSON: an enumerable getter that answers differently on
  // the second read does the same thing, so the fix must measure the clone
  // rather than blocking one serialisation hook.
  let reads = 0;
  const driftingDecoy = {};
  Object.defineProperty(driftingDecoy, 'summary', {
    enumerable: true,
    get() { reads += 1; return reads === 1 ? 'tiny' : hugeString; },
  });
  let driftSizeError = null;
  try { await game.memory.submit(driftingDecoy); }
  catch (error) { driftSizeError = error; }
  assert(driftSizeError?.code === 'invalid_request',
    'a drifting getter hid an oversized memory submission from the size limit');
  assert(host.memoryCalls.length === callsBeforeOversize,
    'an oversized memory submission still reached the trusted host');
  assert(host.memoryCalls[0].payload.session_id === 'context-memory-session',
    'memory submission did not bind the runtime session');
  assert(host.memoryCalls[0].payload.sdk_route_instance_id === routeInstanceId,
    'memory submission was not bound to the route generation');
  assert(Object.keys(host.memoryCalls[0].payload.submission).sort().join(',')
    === 'events,result,state,summary',
  'memory submission contained fields outside the visible event/state/result/summary contract');

  await game.runtime.end({ reason: 'memory-test-complete' });
  game.runtime.reset({ newSession: true });
  assert(!game.memory.consent.enabled && !game.memory.consent.configured && !game.memory.consent.locked,
    'new runtime session did not reset memory consent to disabled');
  await game.memory.configureConsent(false);
  await game.runtime.start({ mode: 'no-memory' });
  let consentRequiredError = null;
  try { await game.memory.submit({ result: { winner: 'player' } }); }
  catch (error) { consentRequiredError = error; }
  assert(consentRequiredError?.code === 'consent_required',
    'disabled memory consent did not block submission');
  game.dispose();

  const pendingHost = createTransport({ sessionId: 'pending-session' });
  pendingHost.setContextPending(true);
  pendingHost.setConsentPending(true);
  pendingHost.setStoragePending(true);
  const pendingGame = await connectClient(pendingHost, 'pending-context-memory-test');
  const pendingContexts = [
    pendingGame.context.read(['character-public']).then(() => null, (error) => error),
    pendingGame.context.read(['current-state']).then(() => null, (error) => error),
  ];
  await new Promise((resolve) => setImmediate(resolve));
  let contextBusyError = null;
  try { await pendingGame.context.read(['current-result']); }
  catch (error) { contextBusyError = error; }
  assert(contextBusyError?.code === 'busy', 'context pending request growth was not bounded');

  const pendingConsent = pendingGame.memory.configureConsent(true)
    .then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  let consentBusyError = null;
  try { await pendingGame.memory.configureConsent(false); }
  catch (error) { consentBusyError = error; }
  assert(consentBusyError?.code === 'busy', 'memory consent requests were not serialized');
  let startBusyError = null;
  try { await pendingGame.runtime.start({}); }
  catch (error) { startBusyError = error; }
  assert(startBusyError?.code === 'busy', 'runtime started while memory consent was unresolved');
  pendingGame.runtime.reset({ newSession: true });
  const resetSessionErrors = await Promise.all([...pendingContexts, pendingConsent]);
  assert(resetSessionErrors.every((error) => error?.code === 'cancelled'),
    'runtime reset did not cancel requests bound to the previous session');
  assert(pendingHost.pendingContext.size === 0 && pendingHost.pendingConsent.size === 0,
    'runtime reset left previous-session context or memory requests resident');
  assert(!pendingGame.memory.consent.enabled && !pendingGame.memory.consent.configured,
    'a cancelled previous-session consent request changed the new session');

  const preInvokeStorageCalls = pendingHost.storageCalls.length;
  const preInvokeAbort = new AbortController();
  const preInvokeRequest = pendingGame.storage.set(
    'cancel-before-host',
    { forbidden: true },
    { signal: preInvokeAbort.signal },
  ).then(() => null, (error) => error);
  preInvokeAbort.abort();
  assert((await preInvokeRequest)?.code === 'cancelled',
    'pre-invoke storage cancellation did not reject publicly');
  assert(pendingHost.storageCalls.length === preInvokeStorageCalls,
    'cancelled managed request invoked the host after public cancellation');

  const pendingStorageRequests = Array.from({ length: 4 }, (_, index) => (
    pendingGame.storage.get(`pending/${index}`).then(() => null, (error) => error)
  ));
  await new Promise((resolve) => setImmediate(resolve));
  let storageBusyError = null;
  try { await pendingGame.storage.get('pending/fifth'); }
  catch (error) { storageBusyError = error; }
  assert(storageBusyError?.code === 'busy', 'storage pending request growth was not bounded');

  pendingGame.dispose();
  const pendingErrors = await Promise.all(pendingStorageRequests);
  assert(pendingErrors.every((error) => error?.code === 'disposed'),
    'dispose did not cancel storage requests as disposed');
  assert(
    pendingHost.pendingStorage.size === 0,
    'disposed storage host requests remained resident',
  );

  let dependencyError = null;
  try {
    await window.NekoMiniGame.connect({
      id: 'memory-without-runtime',
      version: '1.0.0',
      requiredCapabilities: ['logging', 'memory'],
    }, { transport: createTransport().transport });
  } catch (error) { dependencyError = error; }
  assert(dependencyError?.code === 'invalid_manifest',
    'memory capability without runtime was not rejected at manifest validation');

  process.stdout.write('mini-game context and memory runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
