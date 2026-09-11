const fs = require('fs');
const path = require('path');
const vm = require('vm');

function assert(condition, message) {
  if (!condition) throw new Error(message);
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

function abortError() {
  const error = new Error('aborted');
  error.name = 'AbortError';
  return error;
}

function createTransport({ server = false, shared = null } = {}) {
  const values = shared?.values || new Map();
  const lockTails = shared?.lockTails || new Map();
  const pending = new Set();
  const pendingLocks = new Set();
  const serverCalls = [];
  let storagePending = false;
  let storagePendingOperation = '';
  let storageGetFailure = null;
  let storageSetFailure = null;
  let storageDeleteFailure = null;
  let lockPending = false;
  let runtimeState = { sessionId: 'leaderboard-session', characterName: 'Yui' };

  function pendingRequest(options = {}) {
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject };
      pending.add(entry);
      const abort = () => {
        pending.delete(entry);
        reject(abortError());
      };
      if (options.signal?.aborted) abort();
      else options.signal?.addEventListener('abort', abort, { once: true });
    });
  }

  const transport = {
    logger: logger(),
    configureLogger() {},
    connectGame(request) {
      return {
        accepted: true,
        protocolVersion: '1',
        hostVersion: 'leaderboard-test-host',
        registration: {
          mode: 'registered',
          gameId: request.manifest.id,
          publisherId: 'test',
          version: request.manifest.version,
        },
        grantedCapabilities: [
          ...request.manifest.requiredCapabilities,
          ...request.manifest.optionalCapabilities,
        ],
      };
    },
    requestGameStorage(operation, payload, options = {}) {
      if (storagePending || storagePendingOperation === operation) return pendingRequest(options);
      if (operation === 'get') {
        // A transport that reports failure by RETURNING a non-OK response
        // instead of throwing -- indistinguishable from "no board yet" unless
        // the reader looks at ok/status.
        if (storageGetFailure) return Promise.resolve(storageGetFailure);
        return Promise.resolve(values.has(payload.key)
          ? { ok: true, found: true, value: values.get(payload.key) }
          : { ok: true, found: false });
      }
      if (operation === 'set') {
        // A transport that reports a failed write by RETURNING a non-OK
        // response instead of throwing.
        if (storageSetFailure) return Promise.resolve(storageSetFailure);
        values.set(payload.key, payload.value);
      }
      if (operation === 'delete') {
        if (storageDeleteFailure) return Promise.resolve(storageDeleteFailure);
        values.delete(payload.key);
      }
      return Promise.resolve({ ok: true });
    },
    async runGameStorageExclusive(lockName, callback, options = {}) {
      if (lockPending) {
        return new Promise((resolve, reject) => {
          const entry = { resolve, reject };
          pendingLocks.add(entry);
          const abort = () => {
            pendingLocks.delete(entry);
            reject(abortError());
          };
          if (options.signal?.aborted) abort();
          else options.signal?.addEventListener('abort', abort, { once: true });
        });
      }
      const previous = lockTails.get(lockName) || Promise.resolve();
      let release;
      const gate = new Promise((resolve) => { release = resolve; });
      const tail = previous.catch(() => {}).then(() => gate);
      lockTails.set(lockName, tail);
      await previous.catch(() => {});
      try { return await callback(); }
      finally {
        release();
        if (lockTails.get(lockName) === tail) lockTails.delete(lockName);
      }
    },
    getRuntimeState() { return runtimeState; },
    applyRuntimeState(state) { runtimeState = { ...runtimeState, ...state }; return runtimeState; },
    resetRuntime() { return runtimeState; },
    async start() {
      return { ok: true, state: { game_route_active: true, session_id: runtimeState.sessionId } };
    },
    async end() { return { ok: true, state: { session_id: runtimeState.sessionId } }; },
    async heartbeat() { return { ok: true, active: true }; },
    async drain() { return { ok: true, outputs: [] }; },
    dispose() {},
  };
  if (server) {
    transport.submitServerLeaderboard = async (payload) => {
      serverCalls.push({ operation: 'submit', payload });
      return { ok: true, rank: 1 };
    };
    transport.listServerLeaderboard = async (payload) => {
      serverCalls.push({ operation: 'list', payload });
      return { ok: true, entries: [] };
    };
    transport.getServerLeaderboardBest = async (payload) => {
      serverCalls.push({ operation: 'best', payload });
      return { ok: true, entry: null };
    };
  }
  return {
    transport,
    values,
    pending,
    pendingLocks,
    serverCalls,
    setStoragePending(value) { storagePending = value; },
    setStoragePendingOperation(value) { storagePendingOperation = String(value || ''); },
    setStorageGetFailure(value) { storageGetFailure = value; },
    setStorageSetFailure(value) { storageSetFailure = value; },
    setStorageDeleteFailure(value) { storageDeleteFailure = value; },
    setLockPending(value) { lockPending = value; },
  };
}

function manifest(capabilities) {
  return {
    id: 'leaderboard-test',
    version: '1.0.0',
    requiredCapabilities: ['logging', ...capabilities],
    leaderboards: {
      main: {
        scoreField: 'score',
        order: 'descending',
        maxEntries: 3,
        retention: 'recent',
      },
      bulk: {
        scoreField: 'score',
        order: 'descending',
        maxEntries: 64,
        retention: 'recent',
      },
    },
  };
}

async function main() {
  global.window = { console: { error() {} } };
  const sourcePath = path.resolve(__dirname, '../../static/game/sdk/neko-minigame-sdk.js');
  vm.runInThisContext(fs.readFileSync(sourcePath, 'utf8'), { filename: sourcePath });

  const localHost = createTransport();
  const game = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
    transport: localHost.transport,
  });
  for (const score of [10, 30, 20, 40]) {
    await game.leaderboard.local.submit('main', { score, mode: 'duel' });
  }
  const ranked = await game.leaderboard.local.list('main', { sort: 'rank', limit: 10 });
  assert(ranked.data.entries.map((entry) => entry.score).join(',') === '40,30,20',
    'local leaderboard did not rank and bound retained entries');
  const recent = await game.leaderboard.local.list('main', { sort: 'recent', limit: 10 });

  // `query` is shared with the server board, where it is forwarded to the host.
  // The local board has no matching semantics anywhere in the public surface,
  // so accepting and dropping it would hand back an unfiltered page that looks
  // filtered. It must be rejected instead.
  let localQueryError = null;
  try { await game.leaderboard.local.list('main', { query: { player: 'a' } }); }
  catch (error) { localQueryError = error; }
  assert(localQueryError?.code === 'invalid_request',
    'the local leaderboard silently accepted a query it does not implement');
  assert(recent.data.entries.map((entry) => entry.score).join(',') === '40,20,30',
    'local leaderboard did not preserve recent ordering');
  const best = await game.leaderboard.local.getBest('main');
  assert(best.data.entry.score === 40, 'local leaderboard best entry was incorrect');

  // A board trimmed to exactly its byte budget must still be listable. `list`
  // restates the same entries under a different wrapper -- {boardId, entries,
  // totalEntries, limit, offset, hasMore} instead of {version, entries} -- and
  // used to measure that wrapper against the state's own budget. A board whose
  // per-entry size parks the trimmed state within the wrapper's overhead of the
  // cap therefore became permanently unlistable, for every user of that game,
  // because entry size is a property of the game and not of the run.
  // The clone every entry passes through forbids `prototype`/`constructor` as
  // property names, so a board declared on one used to connect fine and then
  // reject every submission; omitting the property instead yields a non-finite
  // score. Reject the board at manifest time instead of at every submit.
  for (const reservedScoreField of ['prototype', 'constructor']) {
    const reservedManifest = manifest(['leaderboard-local']);
    reservedManifest.leaderboards = {
      main: {
        scoreField: reservedScoreField,
        order: 'descending',
        maxEntries: 3,
        retention: 'recent',
      },
    };
    let reservedError = null;
    try {
      await window.NekoMiniGame.connect(reservedManifest, {
        transport: createTransport().transport,
      });
    } catch (error) { reservedError = error; }
    assert(reservedError?.code === 'invalid_manifest',
      `a board declared with scoreField "${reservedScoreField}" connected but can never accept a submission`);
  }

  // The declared score field must BE a number: Number(null)/Number(false)/
  // Number('') are all 0, so malformed entries used to be persisted and ranked
  // as legitimate zeroes.
  for (const badScore of [null, false, '', '10', undefined]) {
    let badScoreError = null;
    try { await game.leaderboard.local.submit('main', { score: badScore, mode: 'duel' }); }
    catch (error) { badScoreError = error; }
    assert(badScoreError !== null,
      `a non-numeric score (${JSON.stringify(badScore)}) was coerced instead of rejected`);
    assert(['invalid_request', 'invalid_contract'].includes(badScoreError.code),
      `a non-numeric score (${JSON.stringify(badScore)}) failed with an unexpected code: ${badScoreError.code}`);
  }
  const ranksAfterBadScores = await game.leaderboard.local.list('main', { sort: 'rank', limit: 10 });
  assert(ranksAfterBadScores.data.entries.map((entry) => entry.score).join(',') === '40,30,20',
    'a rejected entry still reached the board');

  // `order` / `retention` default only when ABSENT: `|| 'descending'` also
  // swallowed an explicit null/''/false, so a schema-invalid manifest ran with
  // configuration its author never declared.
  for (const [field, badValue] of [
    ['order', null], ['order', ''], ['retention', false], ['retention', 0],
  ]) {
    const badManifest = manifest(['leaderboard-local']);
    badManifest.leaderboards = {
      main: { scoreField: 'score', order: 'descending', maxEntries: 3, retention: 'recent' },
    };
    badManifest.leaderboards.main[field] = badValue;
    let badModeError = null;
    try {
      await window.NekoMiniGame.connect(badManifest, { transport: createTransport().transport });
    } catch (error) { badModeError = error; }
    assert(badModeError?.code === 'invalid_manifest',
      `an explicitly falsey ${field} (${JSON.stringify(badValue)}) was replaced by the default`);
  }

  // A failed read is not an empty board. Treating it as one meant a subsequent
  // submit wrote a replacement holding ONLY the new entry -- one transient read
  // failure erased the whole leaderboard.
  for (const failureShape of [{ ok: false, error: 'storage_unavailable' }, { ok: true, error: 'x' }]) {
    const failHost = createTransport();
    const failGame = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
      transport: failHost.transport,
    });
    await failGame.leaderboard.local.submit('main', { score: 42, mode: 'duel' });
    const storedBefore = JSON.stringify(failHost.values.get('leaderboards/main'));
    assert(storedBefore && storedBefore.includes('42'),
      'the read-failure probe did not seed a board first');
    // `{ok:true, error}` is the control: it is NOT a failure, so it must behave
    // exactly as today and is what keeps the assertion below from passing for
    // the wrong reason.
    failHost.setStorageGetFailure(failureShape);
    let readFailureError = null;
    try { await failGame.leaderboard.local.submit('main', { score: 7, mode: 'duel' }); }
    catch (error) { readFailureError = error; }
    if (failureShape.ok === false) {
      assert(readFailureError !== null,
        'a failed leaderboard read was treated as an empty board');
      assert(JSON.stringify(failHost.values.get('leaderboards/main')) === storedBefore,
        'a failed leaderboard read let a submit overwrite the existing board');
    } else {
      assert(readFailureError === null,
        'a successful read carrying an unrelated error field was rejected');
    }
    failHost.setStorageGetFailure(null);
    failGame.dispose();
  }

  // Third of the same family: a failed DELETE reported by return value used to
  // resolve, and clear() told the game the board was cleared while it was still
  // there.
  {
    const clearFailHost = createTransport();
    const clearFailGame = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
      transport: clearFailHost.transport,
    });
    await clearFailGame.leaderboard.local.submit('main', { score: 42, mode: 'duel' });
    const storedBeforeClearFailure = JSON.stringify(clearFailHost.values.get('leaderboards/main'));
    clearFailHost.setStorageDeleteFailure({ ok: false, error: 'storage_unavailable' });
    let clearFailureError = null;
    try { await clearFailGame.leaderboard.local.clear('main', { confirm: true }); }
    catch (error) { clearFailureError = error; }
    assert(clearFailureError !== null,
      'a failed leaderboard delete was reported to the game as a cleared board');
    assert(JSON.stringify(clearFailHost.values.get('leaderboards/main')) === storedBeforeClearFailure,
      'the failed delete probe changed the stored board');
    clearFailHost.setStorageDeleteFailure(null);
    await clearFailGame.leaderboard.local.clear('main', { confirm: true });
    assert(clearFailHost.values.get('leaderboards/main') === undefined,
      'a successful clear did not remove the board');
    clearFailGame.dispose();
  }

  // Padded modes: the schema requires an exact enum member, so `' descending '`
  // is schema-invalid while trimming silently executed it as the real mode.
  for (const [field, padded] of [['order', ' descending '], ['retention', ' recent ']]) {
    const paddedManifest = manifest(['leaderboard-local']);
    paddedManifest.leaderboards = {
      main: { scoreField: 'score', order: 'descending', maxEntries: 3, retention: 'recent' },
    };
    paddedManifest.leaderboards.main[field] = padded;
    let paddedModeError = null;
    try {
      await window.NekoMiniGame.connect(paddedManifest, { transport: createTransport().transport });
    } catch (error) { paddedModeError = error; }
    assert(paddedModeError?.code === 'invalid_manifest',
      `a padded ${field} (${JSON.stringify(padded)}) was trimmed into a real mode`);
  }

  // scoreField followed the same `|| default` + trim shape one line above the
  // modes: `''` is schema-invalid yet became the default field, and
  // `' score '` was trimmed into a field the manifest never declared.
  for (const badScoreField of ['', ' score ', 'score ']) {
    const scoreFieldManifest = manifest(['leaderboard-local']);
    scoreFieldManifest.leaderboards = {
      main: {
        scoreField: badScoreField, order: 'descending', maxEntries: 3, retention: 'recent',
      },
    };
    let scoreFieldError = null;
    try {
      await window.NekoMiniGame.connect(scoreFieldManifest, {
        transport: createTransport().transport,
      });
    } catch (error) { scoreFieldError = error; }
    assert(scoreFieldError?.code === 'invalid_manifest',
      `a rewritten scoreField (${JSON.stringify(badScoreField)}) was accepted`);
  }
  // Control: ABSENT still defaults to 'score', otherwise the three assertions
  // above are satisfied by an implementation that dropped the default entirely.
  const defaultScoreFieldManifest = manifest(['leaderboard-local']);
  defaultScoreFieldManifest.leaderboards = {
    main: { order: 'descending', maxEntries: 3, retention: 'recent' },
  };
  const defaultScoreFieldGame = await window.NekoMiniGame.connect(
    defaultScoreFieldManifest, { transport: createTransport().transport },
  );
  const defaultScoreFieldResult = await defaultScoreFieldGame.leaderboard.local.submit(
    'main', { score: 11, mode: 'duel' },
  );
  assert(defaultScoreFieldResult.ok, 'an absent scoreField stopped defaulting to score');
  defaultScoreFieldGame.dispose();

  // `manifest.leaderboards` is a plain object, so an undeclared board named
  // after an Object.prototype member resolved to the INHERITED value -- truthy,
  // so the "declared by the manifest" check passed and every field was then
  // read off Object.prototype.constructor.
  for (const inheritedBoardId of ['constructor', 'toString', 'valueOf']) {
    let inheritedBoardError = null;
    try {
      await game.leaderboard.local.list(inheritedBoardId, { sort: 'rank', limit: 1 });
    } catch (error) { inheritedBoardError = error; }
    assert(inheritedBoardError?.code === 'invalid_request',
      `an inherited property (${inheritedBoardId}) passed as a declared board`);
  }

  // The dual of the read guard: a failed WRITE reported by return value used to
  // resolve, and submit() then built its success result from the in-memory
  // state -- telling the game its entry was retained and ranked while nothing
  // had been persisted.
  {
    const writeFailHost = createTransport();
    const writeFailGame = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
      transport: writeFailHost.transport,
    });
    await writeFailGame.leaderboard.local.submit('main', { score: 42, mode: 'duel' });
    const storedBeforeWriteFailure = JSON.stringify(writeFailHost.values.get('leaderboards/main'));
    writeFailHost.setStorageSetFailure({ ok: false, error: 'storage_unavailable' });
    let writeFailureError = null;
    try { await writeFailGame.leaderboard.local.submit('main', { score: 99, mode: 'duel' }); }
    catch (error) { writeFailureError = error; }
    assert(writeFailureError !== null,
      'a failed leaderboard write was reported to the game as a retained entry');
    assert(JSON.stringify(writeFailHost.values.get('leaderboards/main')) === storedBeforeWriteFailure,
      'the failed write probe changed the stored board');
    // Control: a successful write carrying an unrelated field must still succeed.
    writeFailHost.setStorageSetFailure({ ok: true, error: 'x' });
    await writeFailGame.leaderboard.local.submit('main', { score: 55, mode: 'duel' });
    writeFailHost.setStorageSetFailure(null);
    writeFailGame.dispose();
  }

  const bulkHost = createTransport();
  const bulkEntries = [];
  for (let index = 0; index < 64; index += 1) {
    bulkEntries.push({
      id: `entry-${String(index).padStart(4, '0')}`,
      submittedAt: 1700000000000 + index,
      score: index,
      data: { score: index, pad: '' },
    });
  }
  let padBudget = 65536 - JSON.stringify({ version: 1, entries: bulkEntries }).length;
  for (let index = 0; index < bulkEntries.length; index += 1) {
    const share = Math.floor(padBudget / (bulkEntries.length - index));
    bulkEntries[index].data.pad = 'x'.repeat(share);
    padBudget -= share;
  }
  const bulkState = { version: 1, entries: bulkEntries };
  assert(JSON.stringify(bulkState).length === 65536,
    'the full-board fixture was not sized to the exact state byte budget');
  bulkHost.values.set('leaderboards/bulk', bulkState);
  const bulkGame = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
    transport: bulkHost.transport,
  });
  const bulkList = await bulkGame.leaderboard.local.list('bulk', { sort: 'rank', limit: 64 });
  assert(bulkList.data.entries.length === 64,
    'a local board sitting at its exact byte budget could not be listed');
  bulkGame.dispose();

  const shared = { values: new Map(), lockTails: new Map() };
  const firstClientHost = createTransport({ shared });
  const secondClientHost = createTransport({ shared });
  const firstClient = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
    transport: firstClientHost.transport,
  });
  const secondClient = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
    transport: secondClientHost.transport,
  });
  await Promise.all([
    firstClient.leaderboard.local.submit('main', { score: 11, source: 'first' }),
    secondClient.leaderboard.local.submit('main', { score: 22, source: 'second' }),
  ]);
  const crossClient = await firstClient.leaderboard.local.list('main', { sort: 'rank', limit: 10 });
  assert(crossClient.data.entries.map((entry) => entry.score).join(',') === '22,11',
    'cross-client local leaderboard submissions overwrote the same storage snapshot');
  assert(shared.lockTails.size === 0, 'cross-client leaderboard lock remained resident after mutation');
  firstClient.dispose();
  secondClient.dispose();

  const lockWaitHost = createTransport();
  lockWaitHost.setLockPending(true);
  const lockWaitClient = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
    transport: lockWaitHost.transport,
  });
  const waitingMutation = lockWaitClient.leaderboard.local.submit('main', { score: 33 })
    .then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  assert(lockWaitHost.pendingLocks.size === 1 && lockWaitClient.leaderboard.local.pendingCount === 1,
    'waiting local leaderboard lock was not registered as pending SDK work');
  lockWaitClient.dispose();
  const disposedLockError = await waitingMutation;
  assert(disposedLockError?.code === 'disposed',
    'client disposal did not cancel a pending local leaderboard lock');
  assert(lockWaitHost.pendingLocks.size === 0,
    'disposed local leaderboard lock remained resident in the host');

  const timeoutHost = createTransport();
  timeoutHost.setStoragePendingOperation('set');
  const timeoutClient = await window.NekoMiniGame.connect(manifest(['leaderboard-local']), {
    transport: timeoutHost.transport,
  });
  const timedOutMutation = timeoutClient.leaderboard.local.submit(
    'main',
    { score: 44 },
    { timeoutMs: 250 },
  ).then(() => null, (error) => error);
  await new Promise((resolve) => setImmediate(resolve));
  assert(timeoutHost.pending.size === 1,
    'timed local leaderboard mutation did not enter its nested storage request');
  const timeoutError = await timedOutMutation;
  assert(timeoutError?.code === 'timeout',
    'local leaderboard mutation did not report its managed timeout');
  assert(timeoutHost.pending.size === 0,
    'timed-out local leaderboard mutation left its nested storage request resident');
  assert(!timeoutHost.values.has('leaderboards/main'),
    'timed-out local leaderboard mutation committed a late storage write');
  assert(timeoutClient.leaderboard.local.pendingCount === 0,
    'timed-out local leaderboard mutation left SDK pending state resident');
  timeoutHost.setStoragePendingOperation('');
  const recoveredMutation = await timeoutClient.leaderboard.local.submit('main', { score: 45 });
  assert(recoveredMutation.data.entry.score === 45,
    'local leaderboard could not mutate after a timed-out nested storage request');
  timeoutClient.dispose();

  let clearError = null;
  try { await game.leaderboard.local.clear('main'); }
  catch (error) { clearError = error; }
  assert(clearError?.code === 'invalid_request', 'local leaderboard clear did not require confirmation');

  localHost.setStoragePending(true);
  const pendingReads = Array.from({ length: 4 }, () => (
    game.leaderboard.local.list('main').then(() => null, (error) => error)
  ));
  await new Promise((resolve) => setImmediate(resolve));
  let busyError = null;
  try { await game.leaderboard.local.list('main'); }
  catch (error) { busyError = error; }
  assert(busyError?.code === 'busy', 'local leaderboard pending requests were not bounded');
  game.dispose();
  const disposedErrors = await Promise.all(pendingReads);
  assert(disposedErrors.every((error) => error?.code === 'disposed'),
    'local leaderboard dispose did not release pending requests');
  assert(localHost.pending.size === 0, 'local leaderboard host requests remained resident');

  const unavailableHost = createTransport();
  const unavailable = await window.NekoMiniGame.connect({
    ...manifest(['runtime', 'leaderboard-local']),
    optionalCapabilities: ['leaderboard-server'],
  }, { transport: unavailableHost.transport });
  assert(!unavailable.capabilities.has('leaderboard-server'),
    'server leaderboard was granted without a server transport');
  unavailable.dispose();

  const serverHost = createTransport({ server: true });
  const serverGame = await window.NekoMiniGame.connect(
    manifest(['runtime', 'leaderboard-server']),
    { transport: serverHost.transport },
  );
  let earlySubmitError = null;
  try { await serverGame.leaderboard.server.submit('main', { score: 9 }); }
  catch (error) { earlySubmitError = error; }
  assert(earlySubmitError?.code === 'session_invalid',
    'server leaderboard accepted a score before runtime end');
  await serverGame.runtime.start({ mode: 'duel' });
  await serverGame.runtime.end({ score: 9 });
  await serverGame.leaderboard.server.submit('main', { score: 9, mode: 'duel' });
  await serverGame.leaderboard.server.list('main', { limit: 10 });
  await serverGame.leaderboard.server.getMyBest('main', { mode: 'duel' });
  assert(serverHost.serverCalls.map((call) => call.operation).join(',') === 'submit,list,best',
    'server leaderboard facade did not use its reserved transport methods');
  assert(serverHost.serverCalls[0].payload.session_id === 'leaderboard-session',
    'server leaderboard submit did not inject the trusted runtime session');
  serverGame.dispose();

  process.stdout.write('mini-game leaderboard runtime test passed\n');
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
