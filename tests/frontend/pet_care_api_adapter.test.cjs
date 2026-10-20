const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

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
