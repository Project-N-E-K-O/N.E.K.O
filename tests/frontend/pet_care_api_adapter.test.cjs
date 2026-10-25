const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createHash, webcrypto } = require('node:crypto');
const { TextEncoder } = require('node:util');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care-api.js'), 'utf8');

test('PetCare transport keeps endpoint and timeout concerns outside the panel', async () => {
  const calls = [];
  const window = {
    setTimeout(fn) { return setTimeout(fn, 1); },
    clearTimeout(id) { clearTimeout(id); },
    fetch(url, options) {
      calls.push({ url, options });
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
    },
  };
  const context = { window, AbortController, Promise, Object, Number, String };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });

  assert.equal(window.NekoPetCareApi.petPath('role/1', '/tasks'), '/api/pet-care/pets/role%2F1/tasks');
  const response = await window.NekoPetCareApi.request('/api/pet-care/pets/r1', {
    credentials: 'same-origin',
  }, { timeoutMs: 25 });
  assert.equal(response.ok, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.ok(calls[0].options.signal);
});

test('injected test fetch keeps the official same-origin gateway contract', async () => {
  const calls = [];
  const window = {
    setTimeout,
    clearTimeout,
    fetch() {
      throw new Error('default fetch should not be used');
    },
  };
  const context = { window, AbortController, Promise, Object, Number, String, JSON, Error };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });

  const transport = window.NekoPetCareApi.createTransport({
    localCapability: false,
    fetch(url, options) {
      calls.push({ url, options });
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
    },
  });
  const original = {
    method: 'POST',
    credentials: 'include',
    body: JSON.stringify({ idempotency_key: 'op-1' }),
    headers: { 'Content-Type': 'application/json' },
  };
  const response = await transport.request('/api/pet-care/pets/r1/care-actions', original);
  assert.equal(response.ok, true);
  assert.equal(calls[0].url, '/api/pet-care/pets/r1/care-actions');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.headers.Accept, 'application/json');
  assert.equal(calls[0].options.headers['Idempotency-Key'], 'op-1');
  assert.equal(original.headers['Idempotency-Key'], undefined);
});

test('test transport preserves Headers-like input while adding the JSON contract', async () => {
  const calls = [];
  const window = { setTimeout, clearTimeout, fetch() { throw new Error('default fetch should not be used'); } };
  const context = { window, AbortController, Promise, Object, Number, String, JSON, Error };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });

  const transport = window.NekoPetCareApi.createTransport({
    localCapability: false,
    fetch(url, options) {
      calls.push({ url, options });
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
    },
  });
  const headersLike = {
    forEach(callback) {
      callback('application/json', 'Content-Type');
      callback('trace-1', 'X-Trace-Id');
    },
  };
  await transport.request('/api/pet-care/context', { headers: headersLike });
  assert.equal(calls[0].url, '/api/pet-care/context');
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
  assert.equal(calls[0].options.headers['X-Trace-Id'], 'trace-1');
  assert.equal(calls[0].options.headers.Accept, 'application/json');
});

test('transport rejects cross-origin and canonical-path escapes before fetch', async () => {
  const calls = [];
  const window = { setTimeout, clearTimeout, fetch() { throw new Error('default fetch should not be used'); } };
  const context = { window, AbortController, Promise, Object, Number, String, JSON, Error };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });

  const transport = window.NekoPetCareApi.createTransport({
    localCapability: false,
    fetch(url, options) {
      calls.push({ url, options });
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
    },
  });
  await assert.rejects(
    transport.request('https://verse.example/api/pet-care/pets/role-a', {
      method: 'GET',
      credentials: 'include',
    }),
    error => error && error.code === 'pet-care-gateway-path-invalid',
  );
  await assert.rejects(
    transport.request('/api/pet-care/%2e%2e/card-drop/oauth/status', { method: 'GET' }),
    error => error && error.code === 'pet-care-gateway-path-invalid',
  );
  assert.equal(calls.length, 0);
});

test('local writes acquire one exact capability and keep the business request unchanged', async () => {
  const calls = [];
  const token = 'a'.repeat(43);
  const body = JSON.stringify({
    action: 'feed',
    idempotency_key: 'care-op-1',
  });
  const original = {
    method: 'POST',
    body,
    headers: {
      'Content-Type': 'application/json',
      'X-Neko-Pet-Care-Capability': 'caller-must-not-control-this',
    },
  };
  const window = {
    crypto: webcrypto,
    TextEncoder,
    setTimeout,
    clearTimeout,
    fetch(url, options) {
      calls.push({ url, options });
      if (String(url).startsWith('/api/pet-care/local-capability?')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ capability: token, expires_in: 30 }),
        });
      }
      return Promise.resolve({ ok: true, status: 200 });
    },
  };
  const context = {
    window, AbortController, Promise, Object, Number, String, JSON, Error,
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });

  const path = '/api/pet-care/pets/' + 'a'.repeat(32) + '/care-actions';
  const response = await window.NekoPetCareApi.request(path, original);

  assert.equal(response.ok, true);
  assert.equal(calls.length, 2);
  const capabilityUrl = new URL(calls[0].url, 'http://127.0.0.1');
  assert.equal(capabilityUrl.pathname, '/api/pet-care/local-capability');
  assert.equal(capabilityUrl.searchParams.get('method'), 'POST');
  assert.equal(capabilityUrl.searchParams.get('path'), path);
  assert.equal(
    capabilityUrl.searchParams.get('body_sha256'),
    createHash('sha256').update(body).digest('hex'),
  );
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[1].url, path);
  assert.equal(calls[1].options.body, body);
  assert.equal(calls[1].options.headers['Idempotency-Key'], 'care-op-1');
  assert.equal(calls[1].options.headers['X-Neko-Pet-Care-Capability'], token);
  assert.equal(original.headers['Idempotency-Key'], undefined);
  assert.equal(
    original.headers['X-Neko-Pet-Care-Capability'],
    'caller-must-not-control-this',
  );
});

test('unknown write outcome retries with a fresh capability and the exact body and key', async () => {
  const targetCalls = [];
  const capabilities = ['b'.repeat(43), 'c'.repeat(43)];
  let capabilityIndex = 0;
  const window = {
    crypto: webcrypto,
    TextEncoder,
    setTimeout,
    clearTimeout,
    fetch(url, options) {
      if (String(url).startsWith('/api/pet-care/local-capability?')) {
        const capability = capabilities[capabilityIndex++];
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({ capability, expires_in: 30 }),
        });
      }
      targetCalls.push({ url, options });
      if (targetCalls.length === 1) {
        return Promise.reject(new Error('network-lost-after-send'));
      }
      return Promise.resolve({ ok: true, status: 200 });
    },
  };
  const context = {
    window, AbortController, Promise, Object, Number, String, JSON, Error,
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });
  const path = '/api/pet-care/session/resume';
  const body = JSON.stringify({ idempotency_key: 'resume-op-1', reason: 'focus' });
  const requestOptions = {
    method: 'POST',
    body,
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': 'resume-op-1',
    },
  };

  await assert.rejects(
    window.NekoPetCareApi.request(path, requestOptions),
    /network-lost-after-send/,
  );
  const retried = await window.NekoPetCareApi.request(path, requestOptions);

  assert.equal(retried.ok, true);
  assert.equal(capabilityIndex, 2);
  assert.equal(targetCalls.length, 2);
  assert.deepEqual(targetCalls.map(call => call.options.body), [body, body]);
  assert.deepEqual(
    targetCalls.map(call => call.options.headers['Idempotency-Key']),
    ['resume-op-1', 'resume-op-1'],
  );
  assert.deepEqual(
    targetCalls.map(call => call.options.headers['X-Neko-Pet-Care-Capability']),
    capabilities,
  );
});

test('capability issuance failure prevents the business write', async () => {
  const calls = [];
  const window = {
    crypto: webcrypto,
    TextEncoder,
    setTimeout,
    clearTimeout,
    fetch(url, options) {
      calls.push({ url, options });
      return Promise.resolve({
        ok: false,
        status: 403,
        json: async () => ({ code: 'instance_authorization_required' }),
      });
    },
  };
  const context = {
    window, AbortController, Promise, Object, Number, String, JSON, Error,
  };
  vm.createContext(context);
  vm.runInContext(source, context, { filename: 'app-pet-care-api.js' });

  await assert.rejects(
    window.NekoPetCareApi.request('/api/pet-care/bind', {
      method: 'POST',
      body: JSON.stringify({ idempotency_key: 'bind-op-1' }),
      headers: { 'Content-Type': 'application/json' },
    }),
    error => error && error.code === 'instance_authorization_required' && error.status === 403,
  );
  assert.equal(calls.length, 1);
});
