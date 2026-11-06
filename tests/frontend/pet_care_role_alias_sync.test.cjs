const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care-role-alias-sync.js'),
  'utf8',
);
const uid = 'a'.repeat(32);
const otherUid = 'b'.repeat(32);
const accountId = '11111111-1111-4111-8111-111111111111';
const otherAccountId = '22222222-2222-4222-8222-222222222222';
const storageKey = 'neko.pet-care.role-alias-retirements.v1';
const ownerStorageKey = 'neko.pet-care.role-alias-owners.v1';

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

function memoryStorage() {
  const values = new Map();
  return {
    values,
    getItem(key) { return values.has(key) ? values.get(key) : null; },
    setItem(key, value) { values.set(key, String(value)); },
  };
}

function response(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    async json() { return body; },
  };
}

function load(apiRequest, storage = memoryStorage(), authRequest = apiRequest,
  localIntegrationRequest = async (url) => {
    if (url.endsWith('/role-owners')) return response(200, { status: 'remembered' });
    if (url.endsWith('/role-alias-retirements')) return response(200, { events: [] });
    if (url.endsWith('/role-alias-retirements/ack')) {
      return response(200, { status: 'acknowledged' });
    }
    throw new Error('unexpected local integration request: ' + url);
  }) {
  const listeners = new Map();
  const window = {
    localStorage: storage,
    navigator: storage.locksUnavailable === true
      ? {} : { locks: storage.lockManager || new FakeLockManager() },
    NekoPetCareApi: { request: apiRequest },
    fetch(url, options) {
      if (String(url).startsWith('/api/local-integrations/pet-care/')) {
        return localIntegrationRequest(url, options);
      }
      return authRequest(url, options);
    },
    document: {
      readyState: 'loading',
      addEventListener(type, listener) { listeners.set(type, listener); },
    },
    addEventListener(type, listener) { listeners.set(type, listener); },
  };
  const context = vm.createContext({
    window,
    console,
    Promise,
    Object,
    JSON,
    String,
    Number,
    Error,
  });
  vm.runInContext(source, context, { filename: 'app-pet-care-role-alias-sync.js' });
  return { api: window.NekoPetCareRoleAliasSync, listeners, storage };
}

function retirement(characterUid = uid) {
  return {
    character_uid: characterUid,
    source_revision: `delete:${characterUid}`,
    status: 'retired',
  };
}

test('checks the durable backend outbox when browser retirement state is empty', async () => {
  const storage = memoryStorage();
  const sent = [];
  const localCalls = [];
  const { api } = load(
    async (url, options) => {
      sent.push({ url, options });
      return response(200, { ...retirement(), replayed: true });
    },
    storage,
    async (url) => {
      assert.equal(url, '/api/card-drop/oauth/status');
      return response(200, { logged_in: true, local_user_id: accountId });
    },
    async (url, options) => {
      localCalls.push({ url, options });
      if (url.endsWith('/role-alias-retirements')) {
        assert.equal(options.headers['X-Neko-Expected-Account'], accountId);
        return response(200, { events: [retirement()] });
      }
      if (url.endsWith('/role-alias-retirements/ack')) {
        return response(200, { status: 'acknowledged' });
      }
      throw new Error('unexpected local integration request: ' + url);
    },
  );

  assert.equal(storage.getItem(storageKey), null);
  assert.equal(storage.getItem(ownerStorageKey), null);
  const result = await api.flush();

  assert.equal(result.status, 'processed');
  assert.equal(result.attempted, 1);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, '/api/pet-care/role-aliases/sync');
  assert.deepEqual(JSON.parse(sent[0].options.body), retirement());
  assert.ok(localCalls.some(call => call.url.endsWith('/role-alias-retirements/ack')));
  assert.deepEqual(JSON.parse(storage.getItem(storageKey)).accounts, {});
});

test('reads account state from the local N.E.K.O origin, outside the PetCare gateway', async () => {
  let petCalls = 0;
  let authCalls = 0;
  const { api } = load(
    async () => {
      petCalls += 1;
      throw new Error('PetCare transport must not own local auth status');
    },
    memoryStorage(),
    async (url) => {
      authCalls += 1;
      assert.equal(url, '/api/card-drop/oauth/status');
      return response(200, { logged_in: true, local_user_id: accountId });
    },
  );

  assert.equal(await api.captureAccount(), accountId);
  assert.equal(authCalls, 1);
  assert.equal(petCalls, 0);
});

test('mirrors verified ownership and rehydrates a committed backend retirement after renderer loss', async () => {
  const storage = memoryStorage();
  const localCalls = [];
  let retirementReady = false;
  const sent = [];
  const { api, listeners } = load(
    async (url, options) => {
      sent.push({ url, options });
      return response(200, { ...retirement(), replayed: true });
    },
    storage,
    async (url) => {
      assert.equal(url, '/api/card-drop/oauth/status');
      return response(200, { logged_in: true, local_user_id: accountId });
    },
    async (url, options) => {
      localCalls.push({ url, options });
      if (url.endsWith('/role-owners')) return response(200, { status: 'remembered' });
      if (url.endsWith('/role-alias-retirements')) {
        assert.equal(options.headers['X-Neko-Expected-Account'], accountId);
        return response(200, { events: retirementReady ? [retirement()] : [] });
      }
      if (url.endsWith('/role-alias-retirements/ack')) {
        return response(200, { status: 'acknowledged' });
      }
      throw new Error('unexpected local request: ' + url);
    },
  );

  await api.rememberOwner(uid, accountId);
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(localCalls.some(call => call.url.endsWith('/role-owners')));

  // Simulate backend deletion commit plus a lost HTTP response: the browser
  // retirement queue is empty, but its verified owner ledger remains.
  retirementReady = true;
  listeners.get('pageshow')();
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent.length, 1);
  assert.equal(sent[0].url, '/api/pet-care/role-aliases/sync');
  assert.deepEqual(JSON.parse(sent[0].options.body), retirement());
  assert.ok(localCalls.some(call => call.url.endsWith('/role-alias-retirements/ack')));
});

test('owner registration fails closed when the crash-safe backend mirror rejects it', async () => {
  const local = [];
  const { api, storage } = load(
    async () => response(200, {}),
    memoryStorage(),
    async () => response(200, {}),
    async (url, options = {}) => {
      local.push({ url, options });
      if (url.endsWith('/role-owners')) {
        return response(503, { code: 'pet_care_retirement_outbox_unavailable' });
      }
      if (url.endsWith('/role-alias-retirements')) return response(200, { events: [] });
      throw new Error('unexpected local integration request: ' + url);
    },
  );

  await assert.rejects(
    api.rememberOwner(uid, accountId),
    /pet_care_retirement_outbox_unavailable/,
  );
  assert.equal(local.filter(call => call.url.endsWith('/role-owners')).length, 1);
  assert.deepEqual(JSON.parse(storage.getItem(ownerStorageKey)), { [uid]: [accountId] });
});

test('owner registry rejects a new character before exceeding its readable limit', async () => {
  const storage = memoryStorage();
  const owners = {};
  for (let index = 0; index < 128; index += 1) {
    owners[index.toString(16).padStart(32, '0')] = [accountId];
  }
  storage.setItem(ownerStorageKey, JSON.stringify(owners));
  let localCalls = 0;
  const { api } = load(
    async () => { throw new Error('service transport must not run'); },
    storage,
    async () => response(200, { logged_in: true, local_user_id: accountId }),
    async () => { localCalls += 1; throw new Error('local transport must not run'); },
  );

  await assert.rejects(
    api.rememberOwner('f'.repeat(32), accountId),
    /owner_storage_full/,
  );
  assert.equal(localCalls, 0);
  assert.equal(Object.keys(JSON.parse(storage.getItem(ownerStorageKey))).length, 128);
});

test('ignores a deleted UID that never entered a verified bind and rejects invented fields', async () => {
  const calls = [];
  const { api, storage } = load(async (...args) => {
    calls.push(args);
    throw new Error('unexpected request');
  });

  const loggedOut = await api.retire(retirement(), '');
  const loggedIn = await api.retire(retirement(), accountId);
  assert.equal(loggedOut.status, 'alias-untracked');
  assert.equal(loggedOut.queued, false);
  assert.equal(loggedIn.status, 'alias-untracked');
  assert.equal(loggedIn.queued, false);
  await assert.rejects(
    api.retire({ ...retirement(), role_id: 'client-role' }, accountId),
    /retirement_invalid/,
  );
  assert.equal(calls.length, 0);
  assert.equal(storage.getItem(storageKey), null);
});

test('persists before sending the fixed retirement shape and clears confirmed evidence', async () => {
  const calls = [];
  const storage = memoryStorage();
  const { api } = load(async (url, options) => {
    calls.push({ url, options, stored: storage.getItem(storageKey) });
    if (url.endsWith('/oauth/status')) {
      return response(200, { logged_in: true, local_user_id: accountId });
    }
    return response(200, { ...retirement(), replayed: false });
  }, storage);

  const captured = await api.captureAccount();
  assert.equal(captured, accountId);
  await api.rememberOwner(uid, accountId);
  const result = await api.retire(retirement(), captured);

  assert.equal(result.status, 'retired');
  const syncCall = calls.find(call => call.url === '/api/pet-care/role-aliases/sync');
  assert.ok(syncCall);
  assert.deepEqual(JSON.parse(syncCall.options.body), retirement());
  assert.equal(syncCall.options.headers['X-Neko-Expected-Account'], accountId);
  assert.ok(syncCall.stored.includes(`delete:${uid}`));
  assert.deepEqual(JSON.parse(storage.getItem(storageKey)).accounts, {});
  assert.deepEqual(JSON.parse(storage.getItem(ownerStorageKey)), {});
});

test('keeps ambiguous failures for the same account and retries only on lifecycle events', async () => {
  let serviceAvailable = false;
  let syncCalls = 0;
  const storage = memoryStorage();
  const { api, listeners } = load(async (url) => {
    if (url.endsWith('/oauth/status')) {
      return response(200, { logged_in: true, local_user_id: accountId });
    }
    syncCalls += 1;
    if (!serviceAvailable) throw new Error('network unavailable');
    return response(200, { ...retirement(), replayed: true });
  }, storage);

  await api.rememberOwner(uid, accountId);
  await assert.rejects(api.retire(retirement(), accountId), /network unavailable/);
  assert.ok(storage.getItem(storageKey).includes(`delete:${uid}`));
  assert.equal(syncCalls, 1);

  serviceAvailable = true;
  listeners.get('focus')();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(syncCalls, 2);
  assert.deepEqual(JSON.parse(storage.getItem(storageKey)).accounts, {});
});

test('clears a legacy confirmed-absent alias instead of retrying it forever', async () => {
  const storage = memoryStorage();
  let syncCalls = 0;
  const { api, listeners } = load(async (url) => {
    if (url.endsWith('/oauth/status')) {
      return response(200, { logged_in: true, local_user_id: accountId });
    }
    syncCalls += 1;
    return response(404, { detail: 'pet_care_role_alias_unknown' });
  }, storage);

  await api.rememberOwner(uid, accountId);
  const result = await api.retire(retirement(), accountId);
  assert.equal(result.status, 'absent');
  assert.equal(result.character_uid, uid);
  assert.deepEqual(JSON.parse(storage.getItem(storageKey)).accounts, {});

  listeners.get('focus')();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(syncCalls, 1);
  assert.deepEqual(JSON.parse(storage.getItem(storageKey)).accounts, {});
});

test('remembered ownership queues a logged-out deletion for the next verified session', async () => {
  const storage = memoryStorage();
  let loggedIn = false;
  let syncCalls = 0;
  const { api, listeners } = load(async (url) => {
    if (url.endsWith('/oauth/status')) return response(200, loggedIn
      ? { logged_in: true, local_user_id: accountId }
      : { logged_in: false, local_user_id: null });
    syncCalls += 1;
    return response(200, { ...retirement(), replayed: true });
  }, storage);

  await api.rememberOwner(uid, accountId);
  const queued = await api.retire(retirement(), '');
  assert.equal(queued.status, 'queued');
  assert.equal(queued.queued, true);
  assert.equal(syncCalls, 0);
  assert.deepEqual(JSON.parse(storage.getItem(ownerStorageKey))[uid], [accountId]);
  assert.ok(storage.getItem(storageKey).includes(`delete:${uid}`));

  loggedIn = true;
  listeners.get('pageshow')();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(syncCalls, 1);
  assert.deepEqual(JSON.parse(storage.getItem(storageKey)).accounts, {});
});

test('queues every remembered owner but immediately sends only the live verified account', async () => {
  const storage = memoryStorage();
  const sentAccounts = [];
  const { api } = load(async (url, options) => {
    if (url.endsWith('/oauth/status')) {
      return response(200, { logged_in: false, local_user_id: null });
    }
    sentAccounts.push(options.headers['X-Neko-Expected-Account']);
    return response(200, { ...retirement(), replayed: false });
  }, storage);

  await api.rememberOwner(uid, accountId);
  await api.rememberOwner(uid, otherAccountId);
  const result = await api.retire(retirement(), otherAccountId);
  assert.equal(result.status, 'retired');
  assert.deepEqual(sentAccounts, [otherAccountId]);
  const accounts = JSON.parse(storage.getItem(storageKey)).accounts;
  assert.equal(accounts[accountId].length, 1);
  assert.equal(Object.hasOwn(accounts, otherAccountId), false);
});

test('shared Web Lock preserves concurrent retirements for different accounts and character UIDs', async () => {
  const storage = memoryStorage();
  storage.lockManager = new FakeLockManager();
  const first = load(async () => { throw new Error('offline-a'); }, storage);
  const second = load(async () => { throw new Error('offline-b'); }, storage);
  await Promise.all([
    first.api.rememberOwner(uid, accountId),
    second.api.rememberOwner(otherUid, otherAccountId),
  ]);

  const results = await Promise.allSettled([
    first.api.retire(retirement(uid), accountId),
    second.api.retire(retirement(otherUid), otherAccountId),
  ]);
  assert.deepEqual(results.map(result => result.status), ['rejected', 'rejected']);
  const accounts = JSON.parse(storage.getItem(storageKey)).accounts;
  assert.deepEqual(accounts[accountId], [retirement(uid)]);
  assert.deepEqual(accounts[otherAccountId], [retirement(otherUid)]);
});

test('shared Web Lock preserves an enqueue while another renderer removes a confirmed retirement', async () => {
  const storage = memoryStorage();
  storage.lockManager = new FakeLockManager();
  let markSecondSent;
  const secondSent = new Promise(resolve => { markSecondSent = resolve; });
  const first = load(async () => {
    await secondSent;
    return response(200, retirement(uid));
  }, storage);
  const second = load(async () => {
    markSecondSent();
    throw new Error('offline-b');
  }, storage);
  await Promise.all([
    first.api.rememberOwner(uid, accountId),
    second.api.rememberOwner(otherUid, accountId),
  ]);

  const results = await Promise.allSettled([
    first.api.retire(retirement(uid), accountId),
    second.api.retire(retirement(otherUid), accountId),
  ]);
  assert.equal(results[0].status, 'fulfilled');
  assert.equal(results[1].status, 'rejected');
  const accounts = JSON.parse(storage.getItem(storageKey)).accounts;
  assert.deepEqual(accounts[accountId], [retirement(otherUid)]);
});

test('shared Web Lock merges concurrent remembered owners without dropping either account', async () => {
  const storage = memoryStorage();
  storage.lockManager = new FakeLockManager();
  const first = load(async () => { throw new Error('unexpected request'); }, storage);
  const second = load(async () => { throw new Error('unexpected request'); }, storage);

  await Promise.all([
    first.api.rememberOwner(uid, accountId),
    second.api.rememberOwner(uid, otherAccountId),
  ]);
  const owners = JSON.parse(storage.getItem(ownerStorageKey));
  assert.deepEqual(owners[uid].slice().sort(), [accountId, otherAccountId].sort());
});

test('persistent changes fail closed when Web Locks are unavailable', async () => {
  const storage = memoryStorage();
  storage.locksUnavailable = true;
  const { api } = load(async () => { throw new Error('transport must not run'); }, storage);

  await assert.rejects(api.rememberOwner(uid, accountId), /role_alias_lock_unavailable/);
  await assert.rejects(api.retire(retirement(), accountId), /role_alias_lock_unavailable/);
  assert.equal(storage.getItem(ownerStorageKey), null);
  assert.equal(storage.getItem(storageKey), null);
});

test('character manager loads and consumes the retirement bridge without changing delete success', () => {
  const template = fs.readFileSync(
    path.join(root, 'templates', 'character_card_manager.html'),
    'utf8',
  );
  const handler = fs.readFileSync(
    path.join(root, 'static', 'js', 'character_card_manager', 'card-form-and-actions.js'),
    'utf8',
  );
  const subscriptions = fs.readFileSync(
    path.join(root, 'static', 'js', 'character_card_manager', 'subscriptions-and-scan.js'),
    'utf8',
  );
  assert.ok(template.indexOf('app-pet-care-api.js') < template.indexOf('app-pet-care-role-alias-sync.js'));
  assert.ok(template.indexOf('app-pet-care-role-alias-sync.js') < template.indexOf('card-form-and-actions.js'));
  assert.match(handler, /pet_care_role_alias_retirement/);
  assert.match(handler, /NekoPetCareRoleAliasSync\.captureAccount/);
  assert.match(handler, /NekoPetCareRoleAliasSync\.retire/);
  assert.doesNotMatch(handler, /await window\.NekoPetCareRoleAliasSync\.retire/);
  assert.doesNotMatch(handler, /petCareAccountId\s*&&\s*window\.NekoPetCareRoleAliasSync\.retire/);
  assert.match(subscriptions, /pet_care_role_alias_retirements/);
  assert.match(subscriptions, /NekoPetCareRoleAliasSync\.captureAccount/);
  assert.match(subscriptions, /NekoPetCareRoleAliasSync\.retire/);
  assert.doesNotMatch(subscriptions, /petCareAccountId\s*&&\s*window\.NekoPetCareRoleAliasSync\.retire/);
});
