const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadHub(options = {}) {
  const listeners = new Map();
  const storage = new Map();
  const intervals = [];
  const window = {
    addEventListener(name, handler) { listeners.set(name, handler); },
    dispatchEvent() {},
    nekoSocialHubHost: null,
    nekoSocial: null,
    nekoVisitChat: options.visitChat || null,
  };
  const document = {
    addEventListener() {},
    querySelector() { return null; },
    body: { appendChild() {} },
  };
  const context = vm.createContext({
    window,
    document,
    CustomEvent: function CustomEvent(name, options) { this.type = name; this.detail = options && options.detail; },
    sessionStorage: {
      getItem(key) { return storage.get(key) || null; },
      setItem(key, value) { storage.set(key, value); },
    },
    setInterval(callback, delay) { intervals.push({ callback, delay }); return intervals.length; },
    setTimeout(callback) { callback(); return 1; },
    clearTimeout() {},
    requestAnimationFrame(callback) { callback(); },
    fetch: options.fetch || (async () => ({ ok: true, json: async () => (options.hubBody || {}) })),
    innerWidth: 800,
    innerHeight: 600,
  });
  const source = fs.readFileSync(path.join(__dirname, '../../static/social-hub.js'), 'utf8');
  vm.runInContext(source, context);
  return { hub: window.nekoSocialHub, listeners, intervals };
}

test('friend invitation follows the visit and clears when it ends', () => {
  const { hub, listeners } = loadHub();
  hub._state.accountId = 'account-1';
  hub._state.invitations.set('friend-1', {
    visitId: 'visit-1', phase: 'invite_ready', expiresAt: 0,
  });

  listeners.get('neko-visit-status-change')({
    detail: { visitId: 'visit-1', action: 'started', phase: 'active' },
  });
  assert.equal(hub._state.invitations.get('friend-1').phase, 'active');

  listeners.get('neko-visit-status-change')({
    detail: { visitId: 'visit-1', action: 'ended', phase: 'ended' },
  });
  assert.equal(hub._state.invitations.has('friend-1'), false);
});

test('an active visit remains visible after its invitation deadline', () => {
  const { hub, intervals } = loadHub({
    visitChat: {
      getState() { return { visitId: 'visit-1', phase: 'active' }; },
    },
  });
  hub._state.accountId = 'account-1';
  hub._state.invitations.set('friend-1', {
    visitId: 'visit-1', phase: 'active', expiresAt: 1,
  });

  intervals.find((entry) => entry.delay === 5000).callback();

  assert.equal(hub._state.invitations.get('friend-1').phase, 'active');
});

test('an incoming direct invitation is delivered to chat while the hub stays closed', async () => {
  const delivered = [];
  loadHub({
    visitChat: {
      presentDirectInvitation(invitation) {
        delivered.push(invitation);
        return true;
      },
    },
    hubBody: {
      logged_in: true,
      account: { id: 'account-1' },
      friends: [],
      incoming_invite: {
        id: 'notice-1',
        actor_id: 'friend-1',
        actor_display_name: 'Community Test',
        visit_id: 'visit-1',
        expires_at: 123,
      },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(JSON.stringify(delivered), JSON.stringify([{
    id: 'notice-1',
    actor_id: 'friend-1',
    display_name: 'Community Test',
    visit_id: 'visit-1',
    expires_at: 123,
  }]));
});

test('an unrelated visit does not clear this friend invitation', () => {
  const { hub } = loadHub();
  hub._state.accountId = 'account-1';
  hub._state.invitations.set('friend-1', {
    visitId: 'visit-1', phase: 'invite_ready', expiresAt: 0,
  });

  hub._applyVisitStatus({ visitId: 'visit-2', action: 'ended', phase: 'ended' });
  assert.equal(hub._state.invitations.get('friend-1').phase, 'invite_ready');
});

function loadHubWithFailedSend(cancelResult) {
  const cancelled = [];
  const loaded = loadHub({
    visitChat: {
      async createDirectInvitation() { return { visit_id: 'visit-1', expires_at: 456 }; },
      async cancelDirectInvitation(visitId) {
        cancelled.push(visitId);
        if (cancelResult instanceof Error) throw cancelResult;
        return cancelResult;
      },
    },
    fetch: async (url) => (String(url).includes('/api/community/visit-invitations')
      ? { ok: false, json: async () => ({ detail: 'community_unavailable' }) }
      : { ok: true, json: async () => ({}) }),
  });
  loaded.hub._state.accountId = 'account-1';
  loaded.hub._state.friends = [{
    id: 'friend-1', displayName: 'Community Test', avatarUrl: 'https://example.test/avatar.png',
  }];
  return { ...loaded, cancelled };
}

for (const [label, cancelResult] of [['returns false', false], ['throws', new Error('offline')]]) {
  test(`a failed send keeps the cancellable invitation when cancelling ${label}`, async () => {
    const { hub, cancelled } = loadHubWithFailedSend(cancelResult);
    await hub._inviteFriend('friend-1');

    assert.deepEqual(cancelled, ['visit-1']);
    assert.equal(JSON.stringify(hub._state.invitations.get('friend-1')), JSON.stringify({
      visitId: 'visit-1', phase: 'invite_ready', expiresAt: 456,
      displayName: 'Community Test', avatarUrl: 'https://example.test/avatar.png',
    }));
    assert.equal(hub._state.toastError, true);
  });
}

test('a failed send clears the invitation once its room is cancelled', async () => {
  const { hub, cancelled } = loadHubWithFailedSend(true);
  await hub._inviteFriend('friend-1');

  assert.deepEqual(cancelled, ['visit-1']);
  assert.equal(hub._state.invitations.has('friend-1'), false);
  assert.equal(hub._state.toastError, true);
});

test('an invitation remains cancellable after its friend leaves the current list', () => {
  const { hub } = loadHub();
  hub._state.friends = [];
  hub._state.invitations.set('friend-1', {
    visitId: 'visit-1', phase: 'invite_ready', expiresAt: 0,
    displayName: 'Community Test', avatarUrl: 'https://example.test/avatar.png',
  });

  assert.equal(JSON.stringify(hub._orphanInvitationFriends()), JSON.stringify([{
    id: 'friend-1', displayName: 'Community Test', avatarUrl: 'https://example.test/avatar.png',
    online: false, presenceStatus: 'offline', lastSeenAt: '',
  }]));
});

test('an invitation still attached to a friend is not rendered twice', () => {
  const { hub } = loadHub();
  hub._state.friends = [{ id: 'friend-1', displayName: 'Community Test' }];
  hub._state.invitations.set('friend-1', {
    visitId: 'visit-1', phase: 'invite_ready', expiresAt: 0,
  });

  assert.equal(hub._orphanInvitationFriends().length, 0);
});

test('a realtime presence event updates the friend without a full refresh', () => {
  const { hub } = loadHub();
  hub._state.friends = [{
    id: 'friend-1', displayName: 'Community Test', online: false,
    presenceStatus: 'offline', lastSeenAt: '2026-10-28T04:00:00Z',
  }];

  const applied = hub._applyPresenceChanged({
    kind: 'presence_changed',
    payload: {
      user_id: 'friend-1', presence_status: 'away', online: true,
      last_seen_at: '2026-10-28T06:00:00Z',
    },
  });

  assert.equal(applied, true);
  assert.equal(JSON.stringify(hub._state.friends[0]), JSON.stringify({
    id: 'friend-1', displayName: 'Community Test', online: true,
    presenceStatus: 'away', lastSeenAt: '2026-10-28T06:00:00Z',
  }));
});

test('open hub reconciles friend presence without refreshing hub content', async () => {
  const calls = [];
  const { hub } = loadHub({
    fetch: async (url) => {
      calls.push(String(url));
      if (String(url).includes('/api/community/friend-presence')) {
        return { ok: true, json: async () => ({ friends: [{
          id: 'friend-1', presence_status: 'away', online: true,
          last_seen_at: '2026-10-28T06:00:00Z',
        }] }) };
      }
      return { ok: true, json: async () => ({
        logged_in: true,
        account: { id: 'account-1' },
        friends: [{
          id: 'friend-1', display_name: 'Community Test',
          presence_status: 'offline', online: false,
          last_seen_at: '2026-10-28T04:00:00Z',
        }],
        recommendations: [{ id: 'recommendation-1', display_name: 'Recommended' }],
      }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  hub._state.open = true;

  const changed = await hub._refreshPresence();

  assert.equal(changed, true);
  assert.equal(hub._state.friends[0].presenceStatus, 'away');
  assert.equal(hub._state.friends[0].online, true);
  assert.equal(hub._state.recommendations[0].id, 'recommendation-1');
  assert.equal(calls.filter((url) => url.includes('/api/community/visit-hub')).length, 1);
  assert.equal(calls.filter((url) => url.includes('/api/community/friend-presence')).length, 1);
});
