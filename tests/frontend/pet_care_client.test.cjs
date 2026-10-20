const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadClient(fetchImpl) {
  const context = {
    window: {},
    fetch: fetchImpl,
    setTimeout,
    clearTimeout,
    AbortController,
    console,
  };
  context.window = context;
  vm.runInNewContext(fs.readFileSync('static/app/app-pet-care-api.js', 'utf8'), context);
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

test('client exposes bind and claim routes without embedding business rules', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'role-a' });
  await client.bind({ method: 'POST' });
  await client.claimTask('role-a', 'small fish/1', { method: 'POST' });
  assert.deepEqual(calls.map((item) => item.url), [
    '/api/pet-care/bind',
    '/api/pet-care/pets/role-a/tasks/small%20fish%2F1/claim',
  ]);
});

test('client exposes the development fill-status route without local value logic', async () => {
  const calls = [];
  const clientFactory = loadClient(async (url, options) => {
    calls.push({ url, options });
    return { status: 200, ok: true };
  });
  const client = clientFactory.create({ characterUid: 'role-a' });
  await client.debugFillStatus('role-a', { method: 'POST' });
  assert.equal(calls[0].url, '/api/pet-care/pets/role-a/debug/fill-status');
  assert.equal(calls[0].options.method, 'POST');
});
