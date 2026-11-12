const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const source = fs.readFileSync(path.join(root, 'static/forge-avatar-reaction.js'), 'utf8');
const eventId = '11111111-1111-4111-8111-111111111111';

function harness() {
  let now = 1000;
  let nextTimer = 0;
  const timers = new Map();
  const listeners = new Map();
  const requests = [];
  let failures = 0;
  const window = {
    appState: { lanlan_name: 'Lan' },
    forgeDropEffectsEnabled: true,
    nekoLocalMutationSecurity: { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'test' }) },
    addEventListener(name, callback) {
      if (!listeners.has(name)) listeners.set(name, []);
      listeners.get(name).push(callback);
    },
  };
  const context = vm.createContext({
    window, document: { body: null }, AbortController,
    Date: { now: () => now },
    setTimeout(callback, delay) {
      const id = ++nextTimer;
      timers.set(id, { callback, due: now + delay });
      return id;
    },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      requests.push({ url, ...options, body: JSON.parse(options.body) });
      if (failures-- > 0) throw new Error('connection reset');
      return { ok: true, status: 200, json: async () => ({ ok: true, queued: true }) };
    },
  });
  vm.runInContext(source, context);
  function emit(name, detail = {}) {
    for (const listener of listeners.get(name) || []) listener({ detail });
  }
  return {
    window, requests, emit,
    failNext: () => { failures = 1; },
    drop: (detail = {}) => emit('neko-forge-credit-drop', {
      event_id: eventId, rarity: 'N', reason: 'emotion_combo', ...detail,
    }),
    async advance(ms = 500) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.due <= now) { timers.delete(id); timer.callback(); }
      }
      for (let i = 0; i < 12; i++) await Promise.resolve();
    },
  };
}

test('only confirmed drops enter chat; duplicate events and balance snapshots stay silent', async () => {
  const h = harness();
  h.drop({ event_id: undefined });
  h.emit('neko-forge-credit-state', { active_count: 5 });
  await h.advance();
  assert.equal(h.requests.length, 0);
  h.drop();
  h.drop();
  await h.advance();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].body.event_id, eventId);
  assert.equal(h.requests[0].headers['X-CSRF-Token'], 'test');
});

test('drop facts enter the normal chat scheduler even during playback', async () => {
  const h = harness();
  h.window.appState.isPlaying = true;
  h.drop({ rarity: 'SSR', reason: '5rounds', active_count: 4, is_career_first: true });
  await h.advance();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].body.rarity, 'SSR');
  assert.equal(h.requests[0].body.reason, '5rounds');
  assert.equal(h.requests[0].body.active_count, 4);
  assert.equal(h.requests[0].body.is_career_first, true);
});

test('the animation switch does not hide drop facts from normal chat', async () => {
  const h = harness();
  h.window.forgeDropEffectsEnabled = false;
  h.drop();
  await h.advance();
  assert.equal(h.requests.length, 1);
});

test('transient failure retries the same event without submitting a new drop', async () => {
  const h = harness();
  h.failNext();
  h.drop();
  await h.advance();
  assert.equal(h.requests.length, 1);
  await h.advance(500);
  assert.equal(h.requests.length, 1);
  await h.advance(500);
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].body.event_id, eventId);
  await h.advance(1000);
  assert.equal(h.requests.length, 2);
});

for (const reason of ['character_changed', 'expired']) {
  test(`unsent drop is discarded when ${reason}`, async () => {
    const h = harness();
    h.drop();
    if (reason === 'character_changed') h.window.appState.lanlan_name = 'Other';
    await h.advance(reason === 'expired' ? 46000 : 500);
    assert.equal(h.requests.length, 0);
  });
}
