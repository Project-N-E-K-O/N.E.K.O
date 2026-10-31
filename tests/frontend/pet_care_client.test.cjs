const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadClient(fetchImpl, options = {}) {
  const context = {
    window: {},
    fetch: fetchImpl,
    setTimeout,
    clearTimeout,
    AbortController,
    console,
  };
  context.window = context;
  if (options.withTransport !== false) {
    vm.runInNewContext(fs.readFileSync('static/app/app-pet-care-api.js', 'utf8'), context);
    context.NekoPetCareApi = context.NekoPetCareApi.createTransport({
      fetch: fetchImpl,
      localCapability: false,
    });
  }
  vm.runInNewContext(fs.readFileSync('static/app/app-pet-care-client.js', 'utf8'), context);
  return context.NekoPetCareClient;
}

test('client keeps endpoint construction and transport separate', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'role/一一' });
  assert.equal(client.path('/tasks'), '/api/pet-care/pets/role%2F%E4%B8%80%E4%B8%80/tasks');
  await client.tasks('role/一一', { method: 'GET' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/pet-care/pets/role%2F%E4%B8%80%E4%B8%80/tasks');
  assert.equal(calls[0].options.method, 'GET');
});

test('client exposes binding discovery, bind, task and recovery routes without embedding business rules', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'role-a' });
  await client.context('role/一一');
  await client.bind({ method: 'POST' });
  await client.claimTask('role-a', 'small fish/1', { method: 'POST' });
  await client.completeTask('role-a', 'daily work/1', { method: 'POST' });
  await client.statusRecovery('role-a', { method: 'POST' });
  assert.deepEqual(calls.map((item) => item.url), [
    '/api/pet-care/context?character_uid=role%2F%E4%B8%80%E4%B8%80',
    '/api/pet-care/bind',
    '/api/pet-care/pets/role-a/tasks/small%20fish%2F1/claim',
    '/api/pet-care/pets/role-a/tasks/daily%20work%2F1/complete',
    '/api/pet-care/pets/role-a/status-recovery',
  ]);
});

test('client exposes account activity resume without a character route', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'must-not-appear' });
  await client.sessionResume({ method: 'POST' });
  assert.equal(calls[0].url, '/api/pet-care/session/resume');
  assert.doesNotMatch(calls[0].url, /must-not-appear|pets\//);
});

test('client exposes the two-step play lease contract on the pet route', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'role/a' });
  await client.issuePlayLease('role/a', { method: 'POST' });
  await client.completePlayLease('role/a', 'lease.v1~opaque_1', { method: 'POST' });
  assert.deepEqual(calls.map(call => call.url), [
    '/api/pet-care/pets/role%2Fa/play-leases',
    '/api/pet-care/pets/role%2Fa/play-leases/lease.v1~opaque_1/complete',
  ]);
});

test('client exposes explicit exploration routes and encodes the activity identity once', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'role/a' });
  await client.startExploration('role/a', { method: 'POST' });
  await client.explorationStatus('role/a', 'trip/一一', { method: 'GET' });
  await client.completeExploration('role/a', 'trip/一一', { method: 'POST' });
  await client.recallExploration('role/a', 'trip/一一', { method: 'POST' });
  assert.deepEqual(calls.map(call => call.url), [
    '/api/pet-care/pets/role%2Fa/explorations',
    '/api/pet-care/pets/role%2Fa/explorations/trip%2F%E4%B8%80%E4%B8%80',
    '/api/pet-care/pets/role%2Fa/explorations/trip%2F%E4%B8%80%E4%B8%80/complete',
    '/api/pet-care/pets/role%2Fa/explorations/trip%2F%E4%B8%80%E4%B8%80/recall',
  ]);
});

test('entry surface delegates task, play and exploration paths to the typed client', () => {
  const source = fs.readFileSync('static/app/app-pet-care.js', 'utf8');
  for (const method of [
    'tasks', 'claimTask', 'completeTask', 'statusRecovery',
    'issuePlayLease', 'completePlayLease',
    'startExploration', 'explorationStatus', 'completeExploration', 'recallExploration',
  ]) {
    assert.match(source, new RegExp('(?:client|petCareClient\\(\\))\\.' + method + '\\('));
  }
  assert.doesNotMatch(source,
    /['"`]\/(?:tasks|status-recovery|play-leases|explorations)(?:\/|['"`])/);
});

test('official client does not expose a local-authority fill-status route', () => {
  const clientFactory = loadClient(async () => ({ status: 200, ok: true }));
  const client = clientFactory.create({ characterUid: 'role-a' });
  assert.equal(client.debugFillStatus, undefined);
  assert.equal(client.dailyCheckIn, undefined);
  assert.equal(client.exploration, undefined);
});

test('client fails closed when the single transport adapter is unavailable', async () => {
  const rawCalls = [];
  const clientFactory = loadClient(async (...args) => {
    rawCalls.push(args);
    return { status: 200, ok: true };
  }, { withTransport: false });
  const client = clientFactory.create({ characterUid: 'role-a' });
  assert.throws(() => client.path('/tasks'), /pet-care-transport-unavailable/);
  await assert.rejects(client.request('/api/pet-care/context'), /pet-care-transport-unavailable/);
  assert.equal(rawCalls.length, 0);
});
