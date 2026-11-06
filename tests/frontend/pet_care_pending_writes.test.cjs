const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care-pending-writes.js'),
  'utf8',
);

function memoryStorage(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    values,
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
    removeItem(key) { values.delete(key); },
  };
}

class FakeLockManager {
  constructor() {
    this.active = new Set();
    this.queues = new Map();
  }

  request(name, options, callback) {
    if (typeof options === 'function') {
      callback = options;
      options = {};
    }
    if (options.ifAvailable === true && this.active.has(name)) {
      return Promise.resolve().then(() => callback(null));
    }
    return new Promise((resolve, reject) => {
      if (!this.queues.has(name)) this.queues.set(name, []);
      this.queues.get(name).push({ callback, resolve, reject });
      this.#drain(name);
    });
  }

  #drain(name) {
    const queue = this.queues.get(name);
    if (this.active.has(name) || !queue || queue.length === 0) return;
    const request = queue.shift();
    this.active.add(name);
    Promise.resolve()
      .then(() => request.callback({ name, mode: 'exclusive' }))
      .then(request.resolve, request.reject)
      .finally(() => {
        this.active.delete(name);
        this.#drain(name);
      });
  }
}

function loadRegistry(storage = memoryStorage(), locks = new FakeLockManager()) {
  const context = vm.createContext({
    window: {
      localStorage: storage,
      crypto: { randomUUID: () => 'client-instance-1' },
      navigator: { locks },
    },
    console,
    Math,
    JSON,
  });
  vm.runInContext(source, context, { filename: 'app-pet-care-pending-writes.js' });
  let clock = 1000;
  return {
    storage,
    registry: context.window.NekoPetCarePendingWrites.create({
      storage,
      now: () => ++clock,
    }),
    api: context.window.NekoPetCarePendingWrites,
    locks,
  };
}

function withLock(registry, accountId, callback, options = {}) {
  return registry.withAccountLock(accountId, options, callback);
}

function accountStorageKey(accountId) {
  return `neko.pet-care.pending-write-account.v1:${encodeURIComponent(accountId)}`;
}

function careInput(overrides = {}) {
  return {
    kind: 'care-action',
    account_id: 'account-a',
    character_uid: 'character-a',
    bound_role_id: 'role-a',
    pet_id: 'pet-a',
    body: {
      category: 'feeding',
      content_id: 'small_fish',
      idempotency_key: 'write-1',
      expected_state_version: 7,
      expected_config_revision: 'candidate-1',
    },
    ...overrides,
  };
}

function resumeInput(overrides = {}) {
  return {
    kind: 'session-resume',
    account_id: 'account-a',
    character_uid: '',
    bound_role_id: '',
    pet_id: '',
    target: { auth_fence: 'account-a|session-a|1' },
    body: { idempotency_key: 'resume-1' },
    ...overrides,
  };
}

test('persists and reads back one immutable mutation lane per account', async () => {
  const { registry, storage, api } = loadRegistry();
  const record = await withLock(registry, 'account-a', () => registry.begin(careInput()));
  assert.equal(record.kind, 'care-action');
  assert.equal(record.client_instance_id, 'client-instance-1');
  assert.equal(record.body.expected_state_version, 7);
  assert.equal(record.body.expected_config_revision, 'candidate-1');
  assert.equal(registry.current('account-a').idempotency_key, 'write-1');
  assert.ok(storage.values.has(`${api.ACCOUNT_STORAGE_PREFIX}${encodeURIComponent('account-a')}`));

  await assert.rejects(
    withLock(registry, 'account-a', () => registry.begin(careInput({
      body: { ...careInput().body, idempotency_key: 'write-2' },
    }))),
    /pet_care_write_pending/,
  );
  const other = await withLock(registry, 'account-b', () => registry.begin(careInput({
    account_id: 'account-b',
    character_uid: 'character-b',
    bound_role_id: 'role-b',
    pet_id: 'pet-b',
    body: { ...careInput().body, idempotency_key: 'write-b' },
  })));
  assert.equal(other.account_id, 'account-b');
  assert.equal(registry.current('account-a').idempotency_key, 'write-1');
});

test('different accounts persist in independent keys and cannot overwrite each other', async () => {
  const storage = memoryStorage();
  const locks = new FakeLockManager();
  const first = loadRegistry(storage, locks).registry;
  const second = loadRegistry(storage, locks).registry;

  await Promise.all([
    withLock(first, 'account-a', () => first.begin(careInput())),
    withLock(second, 'account-b', () => second.begin(careInput({
      account_id: 'account-b', character_uid: 'character-b', bound_role_id: 'role-b',
      pet_id: 'pet-b', body: { ...careInput().body, idempotency_key: 'write-b' },
    }))),
  ]);

  assert.notEqual(accountStorageKey('account-a'), accountStorageKey('account-b'));
  assert.equal(JSON.parse(storage.getItem(accountStorageKey('account-a'))).record.idempotency_key,
    'write-1');
  assert.equal(JSON.parse(storage.getItem(accountStorageKey('account-b'))).record.idempotency_key,
    'write-b');
  assert.equal(first.current('account-a').idempotency_key, 'write-1');
  assert.equal(second.current('account-b').idempotency_key, 'write-b');
});

test('first locked access migrates an existing v1 account record without changing its identity', async () => {
  const sourceRegistry = loadRegistry().registry;
  const legacyRecord = await withLock(sourceRegistry, 'account-a',
    () => sourceRegistry.begin(careInput()));
  const storage = memoryStorage({
    'neko.pet-care.pending-writes.v1': JSON.stringify({
      version: 1,
      accounts: { 'account-a': legacyRecord },
    }),
  });
  const { registry } = loadRegistry(storage);

  assert.equal(registry.current('account-a').idempotency_key, 'write-1');
  await withLock(registry, 'account-a', () => {
    const prepared = registry.prepare('account-a', legacyRecord.operation_id);
    assert.equal(prepared.idempotency_key, legacyRecord.idempotency_key);
    assert.deepEqual(prepared.body, legacyRecord.body);
  });
  assert.equal(storage.getItem('neko.pet-care.pending-writes.v1'), null);
  assert.equal(JSON.parse(storage.getItem(accountStorageKey('account-a'))).record.operation_id,
    legacyRecord.operation_id);
});

test('account resume shares the lane without persisting role or pet identity', async () => {
  const { registry } = loadRegistry();
  const resume = await withLock(registry, 'account-a', () => registry.begin(resumeInput()));
  assert.equal(resume.kind, 'session-resume');
  assert.equal(resume.character_uid, '');
  assert.equal(resume.bound_role_id, '');
  assert.equal(resume.pet_id, '');
  await assert.rejects(withLock(registry, 'account-a', () => registry.begin(careInput())),
    /pet_care_write_pending/);
  await assert.rejects(withLock(registry, 'account-a', () => registry.begin(
    resumeInput({ character_uid: 'local-role-a' })
  )), /pet_care_pending_write_invalid/);
});

test('exploration start requires a non-empty location before entering the durable lane', async () => {
  const { registry } = loadRegistry();
  const input = body => careInput({ kind: 'exploration-start', body });

  await assert.rejects(withLock(registry, 'account-a', () => registry.begin(input({
    idempotency_key: 'exploration-start-missing', expected_state_version: 7,
    expected_config_revision: 'candidate-1',
  }))), /pet_care_pending_write_invalid/);
  assert.equal(registry.current('account-a'), null);

  await assert.rejects(withLock(registry, 'account-a', () => registry.begin(input({
    idempotency_key: 'exploration-start-blank', expected_state_version: 7,
    expected_config_revision: 'candidate-1',
    location_id: '   ',
  }))), /pet_care_pending_write_invalid/);
  assert.equal(registry.current('account-a'), null);

  const record = await withLock(registry, 'account-a', () => registry.begin(input({
    idempotency_key: 'exploration-start-valid', expected_state_version: 7,
    expected_config_revision: 'candidate-1',
    location_id: 'window-path',
  })));
  assert.equal(record.body.location_id, 'window-path');
  assert.equal(record.body.expected_config_revision, 'candidate-1');
});

test('play lease issue persists the exact config revision and rejects an unversioned request', async () => {
  const { registry } = loadRegistry();
  const base = careInput({
    kind: 'play-lease-issue',
    body: {
      idempotency_key: 'play-lease-1', expected_state_version: 7,
      expected_config_revision: 'candidate-1', activity_id: 'activity-1',
      content_id: 'yarn', source: 'cat-yarn-observation-adapter',
    },
  });
  const record = await withLock(registry, 'account-a', () => registry.begin(base));
  assert.equal(record.body.expected_config_revision, 'candidate-1');
  await withLock(registry, 'account-a', () => registry.complete(
    'account-a', record.operation_id));

  const unversioned = careInput({
    kind: 'play-lease-issue',
    body: {
      idempotency_key: 'play-lease-2', expected_state_version: 7,
      activity_id: 'activity-2', content_id: 'yarn',
      source: 'cat-yarn-observation-adapter',
    },
  });
  await assert.rejects(
    withLock(registry, 'account-a', () => registry.begin(unversioned)),
    /pet_care_pending_write_invalid/,
  );
});

test('exploration identity stays in the fixed route target and never enters the DTO body', async () => {
  const { registry } = loadRegistry();
  const record = await withLock(registry, 'account-a', () => registry.begin(careInput({
    kind: 'exploration-recall',
    target: { exploration_id: 'exploration-a' },
    body: { idempotency_key: 'recall-1', expected_state_version: 7 },
  })));
  assert.equal(record.target.exploration_id, 'exploration-a');
  assert.equal(Object.hasOwn(record.body, 'exploration_id'), false);
  await assert.rejects(withLock(registry, 'account-b', () => registry.begin(careInput({
    account_id: 'account-b',
    kind: 'exploration-recall',
    target: { exploration_id: 'exploration-b' },
    body: {
      exploration_id: 'exploration-b', idempotency_key: 'recall-2', expected_state_version: 7,
    },
  }))), /pet_care_pending_write_invalid/);
});

test('prepare preserves body, CAS, context, instance and key exactly', async () => {
  const { registry } = loadRegistry();
  const [initial, prepared] = await withLock(registry, 'account-a', () => {
    const begun = registry.begin(careInput());
    return [begun, registry.prepare('account-a', begun.operation_id)];
  });
  assert.deepEqual(prepared.body, initial.body);
  assert.equal(prepared.account_id, initial.account_id);
  assert.equal(prepared.character_uid, initial.character_uid);
  assert.equal(prepared.bound_role_id, initial.bound_role_id);
  assert.equal(prepared.pet_id, initial.pet_id);
  assert.equal(prepared.client_instance_id, initial.client_instance_id);
  assert.equal(prepared.idempotency_key, initial.idempotency_key);
  assert.equal(prepared.attempt_count, 1);
  assert.ok(prepared.last_attempt_at > 0);
});

test('account Web Lock admits one new write and replays only the durable unknown request', async () => {
  const storage = memoryStorage();
  const locks = new FakeLockManager();
  const first = loadRegistry(storage, locks).registry;
  const second = loadRegistry(storage, locks).registry;
  const sends = [];
  let releaseFirst;
  let markStarted;
  const firstCanFinish = new Promise(resolve => { releaseFirst = resolve; });
  const firstStarted = new Promise(resolve => { markStarted = resolve; });

  const firstRun = withLock(first, 'account-a', async () => {
    const record = first.begin(careInput());
    const prepared = first.prepare('account-a', record.operation_id);
    sends.push({ key: prepared.idempotency_key, body: prepared.body });
    markStarted();
    await firstCanFinish;
    // Unknown transport result: release only the browser lock. The exact
    // durable operation remains for the next renderer to reconcile.
    return prepared;
  }, { ifAvailable: true });
  await firstStarted;

  await assert.rejects(withLock(second, 'account-a', () => {
    const record = second.begin(careInput({
      body: { ...careInput().body, idempotency_key: 'write-2' },
    }));
    const prepared = second.prepare('account-a', record.operation_id);
    sends.push({ key: prepared.idempotency_key, body: prepared.body });
  }, { ifAvailable: true }), /pet_care_write_pending/);
  assert.equal(sends.length, 1);
  assert.equal(second.current('account-a').idempotency_key, 'write-1');

  releaseFirst();
  const original = await firstRun;
  await withLock(second, 'account-a', () => {
    const durable = second.current('account-a');
    const prepared = second.prepare('account-a', durable.operation_id);
    sends.push({ key: prepared.idempotency_key, body: prepared.body });
    assert.equal(prepared.idempotency_key, original.idempotency_key);
    assert.deepEqual(prepared.body, original.body);
    assert.equal(second.complete('account-a', prepared.operation_id), true);
  });
  assert.deepEqual(sends[1], sends[0]);
  assert.equal(second.current('account-a'), null);
});

test('mutations require a held Web Lock and unavailable locks fail closed', async () => {
  const storage = memoryStorage();
  const { registry } = loadRegistry(storage, null);
  assert.throws(() => registry.begin(careInput()), /lock_required/);
  await assert.rejects(registry.withAccountLock('account-a', () => registry.begin(careInput())),
    /lock_unavailable/);
  assert.equal(registry.current('account-a'), null);
});

test('rejects arbitrary route material and sensitive or extra body fields', async () => {
  const { registry } = loadRegistry();
  await withLock(registry, 'account-a', () => {
    assert.throws(() => registry.begin({
      ...careInput(),
      url: 'https://attacker.invalid/write',
      body: { ...careInput().body, access_token: 'secret' },
    }), /pet_care_pending_write_invalid/);
    assert.throws(() => registry.begin({
      ...careInput(),
      kind: 'https://attacker.invalid/write',
    }), /pet_care_pending_write_kind_invalid/);
  });
});

test('role switch does not delete an account pending record', async () => {
  const { registry } = loadRegistry();
  await withLock(registry, 'account-a', () => registry.begin(careInput()));
  assert.equal(registry.current('account-a').character_uid, 'character-a');
  assert.equal(registry.current('other-account'), null);
  assert.equal(registry.current('account-a').character_uid, 'character-a');
});

test('classifies ambiguity, explicit rejection and idempotency isolation', () => {
  const { registry } = loadRegistry();
  assert.equal(registry.classify({ status: 504 }, {}, null), 'pending');
  assert.equal(registry.classify({ status: 200 }, {}, new SyntaxError('truncated')), 'pending');
  assert.equal(registry.classify({ status: 409 }, {}, new SyntaxError('truncated')), 'pending');
  assert.equal(registry.classify({ status: 409 }, {
    operation_result: { status: 'in_progress', code: 'operation_in_progress' },
  }, null), 'pending');
  assert.equal(registry.classify({ status: 409 }, {
    detail: 'pet_care_operation_in_progress',
  }, null), 'pending');
  assert.equal(registry.classify({ status: 422 }, { detail: 'bad request' }, null), 'rejected');
  assert.equal(registry.classify({ status: 429 }, { detail: 'rate_limited' }, null), 'rejected');
  assert.equal(registry.classify({ status: 200 }, {
    operation_result: { status: 'rejected', code: 'inventory_insufficient' },
  }, null), 'rejected');
  assert.equal(registry.classify({ status: 409 }, {
    operation_result: { status: 'rejected', code: 'idempotency_key_reused' },
  }, null), 'quarantine');
  assert.equal(registry.classify({ status: 409 }, {
    detail: 'pet_care_idempotency_key_reused',
  }, null), 'quarantine');
  assert.equal(registry.classify({ status: 200 }, {
    operation_result: { status: 'confirmed' },
  }, null), 'confirmed');
  assert.equal(registry.classify({ status: 200 }, {}, null), 'pending');
  assert.equal(registry.classify({ status: 200 }, {
    request_id: 'resume-request-1', server_time: '2026-10-21T00:00:00Z',
    resume_not_before: '2026-10-21T00:01:00Z', needs_binding: false,
    pet_refresh_required: true,
  }, null, 'session-resume'), 'confirmed');
  assert.equal(registry.classify({ status: 200 }, {
    request_id: 'resume-request-1', server_time: '2026-10-21T00:00:00Z',
    resume_not_before: '2026-10-21T00:01:00Z', pet_refresh_required: true,
  }, null, 'session-resume'), 'pending');
});

test('quarantine blocks retry and a new key while terminal completion clears only the exact record', async () => {
  const { registry } = loadRegistry();
  const record = await withLock(registry, 'account-a', () => {
    const begun = registry.begin(careInput());
    registry.quarantine('account-a', begun.operation_id, 'idempotency_key_reused');
    return begun;
  });
  assert.equal(registry.current('account-a').phase, 'quarantined');
  await withLock(registry, 'account-a', () => {
    assert.throws(() => registry.prepare('account-a', record.operation_id), /missing/);
    assert.throws(() => registry.begin(careInput({
      body: { ...careInput().body, idempotency_key: 'replacement-key' },
    })), /quarantined/);
    assert.equal(registry.complete('account-a', 'wrong-operation'), false);
  });
  assert.equal(registry.current('account-a').phase, 'quarantined');
  assert.equal(await withLock(registry, 'account-a',
    () => registry.complete('account-a', record.operation_id)), true);
  assert.equal(registry.current('account-a'), null);
});

test('refuses to send when durable write and readback cannot be proven', async () => {
  const storage = memoryStorage();
  storage.setItem = () => {};
  const { registry } = loadRegistry(storage);
  await assert.rejects(withLock(registry, 'account-a', () => registry.begin(careInput())),
    /storage_failed/);
});
