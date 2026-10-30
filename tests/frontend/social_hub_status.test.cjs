const assert = require('node:assert/strict');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const frontendRequire = createRequire(path.join(__dirname, '../../frontend/react-neko-chat/package.json'));
const { JSDOM } = frontendRequire('jsdom');

function loadHub(options = {}) {
  const listeners = new Map();
  const storage = new Map();
  const intervals = [];
  const window = {
    addEventListener(name, handler) { listeners.set(name, handler); },
    dispatchEvent() {},
    nekoSocialHubHost: options.host || null,
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

function loadMountedHub(options = {}) {
  const dom = new JSDOM('<!doctype html><body><div id="neko-social-hub-root"></div></body>', {
    runScripts: 'outside-only',
    url: 'http://localhost/',
  });
  const { window } = dom;
  window.__NEKO_SOCIAL_HUB_STANDALONE__ = options.standalone !== false;
  window.fetch = options.fetch;
  window.requestAnimationFrame = (callback) => { callback(); return 1; };
  window.setInterval = () => 1;
  window.clearInterval = () => {};
  const source = fs.readFileSync(path.join(__dirname, '../../static/social-hub.js'), 'utf8');
  window.eval(source);
  return { dom, hub: window.nekoSocialHub, document: window.document };
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

for (const transport of ['web', 'electron']) {
  test(`quota failure is shown in the friend panel without sending a notification (${transport})`, async () => {
    const quotaText = '今天的串门时间用完了';
    const fetches = [];
    const fail = async () => { throw new Error(quotaText); };
    const { hub } = loadHub({
      host: transport === 'electron' ? { createDirectInvite: fail } : null,
      visitChat: transport === 'web' ? { createDirectInvitation: fail } : null,
      fetch: async (url, init) => {
        fetches.push({ url: String(url), method: init && init.method || 'GET' });
        return { ok: true, json: async () => ({}) };
      },
    });
    await new Promise((resolve) => setImmediate(resolve));
    hub._state.accountId = 'account-1';
    hub._state.friends = [{ id: 'friend-1', displayName: 'Friend', avatarUrl: '' }];

    await hub._inviteFriend('friend-1');

    assert.equal(hub._state.toast, quotaText);
    assert.equal(hub._state.toastError, true);
    assert.equal(hub._state.invitations.has('friend-1'), false);
    assert.equal(fetches.some((entry) => entry.method === 'POST' && entry.url === '/api/community/visit-invitations'), false);
  });
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

test('a late presence snapshot cannot overwrite a newer pushed state', async () => {
  let resolvePresence;
  const { hub } = loadHub({
    fetch: async (url) => {
      if (String(url).includes('/api/community/friend-presence')) {
        return new Promise((resolve) => { resolvePresence = resolve; });
      }
      return { ok: true, json: async () => ({ logged_in: true, account: { id: 'account-1' } }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  hub._state.open = true;
  hub._state.loggedIn = true;
  hub._state.friends = [{
    id: 'friend-1', displayName: 'Community Test', online: false,
    presenceStatus: 'offline', lastSeenAt: '2026-10-28T04:00:00Z',
  }];

  const pending = hub._refreshPresence();
  hub._applyPresenceChanged({
    kind: 'presence_changed',
    payload: {
      user_id: 'friend-1', presence_status: 'online', online: true,
      last_seen_at: '2026-10-28T06:01:00Z',
    },
  });
  resolvePresence({ ok: true, json: async () => ({ friends: [{
    id: 'friend-1', presence_status: 'offline', online: false,
    last_seen_at: '2026-10-28T06:00:00Z',
  }] }) });
  await pending;

  assert.equal(hub._state.friends[0].presenceStatus, 'online');
  assert.equal(hub._state.friends[0].lastSeenAt, '2026-10-28T06:01:00Z');
});

test('a late presence snapshot cannot cross community accounts', async () => {
  let resolvePresence;
  const { hub } = loadHub({
    fetch: async (url) => {
      if (String(url).includes('/api/community/friend-presence')) {
        return new Promise((resolve) => { resolvePresence = resolve; });
      }
      return { ok: true, json: async () => ({ logged_in: true, account: { id: 'account-1' } }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  hub._state.open = true;
  hub._state.loggedIn = true;
  hub._state.friends = [{
    id: 'shared-friend', displayName: 'Shared Friend', online: false,
    presenceStatus: 'offline', lastSeenAt: '2026-10-28T04:00:00Z',
  }];

  const pending = hub._refreshPresence();
  hub._state.accountId = 'account-2';
  hub._state.friends = [{
    id: 'shared-friend', displayName: 'Shared Friend', online: true,
    presenceStatus: 'away', lastSeenAt: '2026-10-28T06:01:00Z',
  }];
  resolvePresence({ ok: true, json: async () => ({ friends: [{
    id: 'shared-friend', presence_status: 'online', online: true,
    last_seen_at: '2026-10-28T06:00:00Z',
  }] }) });

  assert.equal(await pending, false);
  assert.equal(hub._state.friends[0].presenceStatus, 'away');
  assert.equal(hub._state.friends[0].lastSeenAt, '2026-10-28T06:01:00Z');
});

test('a late presence snapshot cannot cross panel open generations', async () => {
  let resolvePresence;
  const hubBody = {
    logged_in: true,
    account: { id: 'account-1', display_name: 'Current User' },
    friends: [{
      id: 'shared-friend', display_name: 'Shared Friend', online: true,
      presence_status: 'away', last_seen_at: '2026-10-28T06:01:00Z',
    }],
    recommendations: [],
  };
  const { dom, hub } = loadMountedHub({
    standalone: false,
    fetch: async (url) => {
      if (String(url).includes('/api/community/friend-presence')) {
        return new Promise((resolve) => { resolvePresence = resolve; });
      }
      return { ok: true, json: async () => hubBody };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  hub.open();
  await new Promise((resolve) => setImmediate(resolve));

  const pending = hub._refreshPresence();
  hub.close();
  hub.open();
  await new Promise((resolve) => setImmediate(resolve));
  resolvePresence({ ok: true, json: async () => ({ friends: [{
    id: 'shared-friend', presence_status: 'online', online: true,
    last_seen_at: '2026-10-28T06:00:00Z',
  }] }) });

  assert.equal(await pending, false);
  assert.equal(hub._state.friends[0].presenceStatus, 'away');
  assert.equal(hub._state.friends[0].lastSeenAt, '2026-10-28T06:01:00Z');
  dom.window.close();
});

test('a late full hub refresh cannot overwrite a newer pushed presence', async () => {
  let visitHubReads = 0;
  let resolveRefresh;
  const { hub } = loadHub({
    fetch: async () => {
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true,
          account: { id: 'account-1' },
          friends: [{
            id: 'friend-1', display_name: 'Community Test', presence_status: 'offline',
            online: false, last_seen_at: '2026-10-28T04:00:00Z',
          }],
        }) };
      }
      return new Promise((resolve) => { resolveRefresh = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const pending = hub.refresh(true);
  hub._applyPresenceChanged({
    kind: 'presence_changed',
    payload: {
      user_id: 'friend-1', presence_status: 'online', online: true,
      last_seen_at: '2026-10-28T06:01:00Z',
    },
  });
  resolveRefresh({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{
      id: 'friend-1', display_name: 'Community Test', presence_status: 'offline',
      online: false, last_seen_at: '2026-10-28T06:00:00Z',
    }],
  }) });
  await pending;

  assert.equal(hub._state.friends[0].presenceStatus, 'online');
  assert.equal(hub._state.friends[0].lastSeenAt, '2026-10-28T06:01:00Z');
});

test('a newer successful hub refresh wins while the older loading owner finishes cleanly', async () => {
  let visitHubReads = 0;
  let resolveOlder;
  let resolveNewer;
  const { hub } = loadHub({
    fetch: async () => {
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true,
          account: { id: 'account-1' },
          friends: [{ id: 'friend-initial', display_name: 'Initial' }],
        }) };
      }
      if (visitHubReads === 2) return new Promise((resolve) => { resolveOlder = resolve; });
      return new Promise((resolve) => { resolveNewer = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const older = hub.refresh(false);
  const newer = hub.refresh(true);
  resolveNewer({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-2' },
    friends: [{ id: 'friend-newer', display_name: 'Newer' }],
  }) });
  await newer;
  assert.equal(hub._state.accountId, 'account-2');
  assert.equal(hub._state.loading, true);

  resolveOlder({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{ id: 'friend-older', display_name: 'Older' }],
  }) });
  await older;

  assert.equal(hub._state.accountId, 'account-2');
  assert.equal(hub._state.friends[0].id, 'friend-newer');
  assert.equal(hub._state.loading, false);
});

test('an older successful hub refresh can apply when a newer silent refresh fails', async () => {
  let visitHubReads = 0;
  let resolveOlder;
  let resolveNewer;
  const { hub } = loadHub({
    fetch: async () => {
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true,
          account: { id: 'account-initial' },
          friends: [],
        }) };
      }
      if (visitHubReads === 2) return new Promise((resolve) => { resolveOlder = resolve; });
      return new Promise((resolve) => { resolveNewer = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const older = hub.refresh(false);
  const newer = hub.refresh(true);
  resolveNewer({ ok: false, json: async () => ({ detail: 'community_unavailable' }) });
  await newer;
  assert.equal(hub._state.loading, true);

  resolveOlder({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-recovered' },
    friends: [{ id: 'friend-recovered', display_name: 'Recovered' }],
  }) });
  await older;

  assert.equal(hub._state.accountId, 'account-recovered');
  assert.equal(hub._state.friends[0].id, 'friend-recovered');
  assert.equal(hub._state.loading, false);
});

for (const failure of ['unverified-session', 'malformed-json', 'invalid-json']) {
  test(`background refresh preserves the signed-in panel during ${failure}`, async () => {
    const initial = {
      logged_in: true, account: { id: 'account-1', display_name: 'Owner' },
      friends: [{ id: 'friend-1', display_name: 'Friend', online: true }],
    };
    let reads = 0;
    const { hub, intervals } = loadHub({
      fetch: async () => {
        reads += 1;
        if (reads !== 2) return { ok: true, status: 200, json: async () => initial };
        if (failure === 'unverified-session') {
          return { ok: false, status: 503, json: async () => ({ detail: 'community_unavailable' }) };
        }
        if (failure === 'invalid-json') {
          return { ok: true, status: 200, json: async () => { throw new SyntaxError('Invalid JSON'); } };
        }
        return { ok: true, status: 200, json: async () => ({}) };
      },
    });
    await new Promise((resolve) => setImmediate(resolve));
    intervals.find((entry) => entry.delay === 60000).callback();
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(hub._state.loggedIn, true);
    assert.equal(hub._state.accountId, 'account-1');
    assert.equal(hub._state.friends[0].id, 'friend-1');
    assert.equal(hub._state.loadFailed, false);

    await hub.refresh(true);
    assert.equal(hub._state.loggedIn, true);
    assert.equal(hub._state.accountId, 'account-1');
  });
}

test('a confirmed logout still clears the signed-in panel during a background refresh', async () => {
  let reads = 0;
  const { hub } = loadHub({
    fetch: async () => ++reads === 1
      ? { ok: true, json: async () => ({
        logged_in: true, account: { id: 'account-1' }, friends: [{ id: 'friend-1' }],
      }) }
      : { ok: false, status: 401, json: async () => ({ detail: 'community_login_required' }) },
  });
  await new Promise((resolve) => setImmediate(resolve));
  await hub.refresh(true);
  assert.equal(hub._state.loggedIn, false);
  assert.equal(hub._state.accountId, '');
  assert.equal(hub._state.account, null);
  assert.equal(hub._state.friends.length, 0);
});

test('an older opening refresh can still fill recommendations after newer silent data', async () => {
  let visitHubReads = 0;
  let resolveOpening;
  let resolveSilent;
  const { hub } = loadHub({
    fetch: async () => {
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true, account: { id: 'account-1' }, friends: [], recommendations: [],
        }) };
      }
      if (visitHubReads === 2) return new Promise((resolve) => { resolveOpening = resolve; });
      return new Promise((resolve) => { resolveSilent = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const opening = hub.refresh(false);
  const silent = hub.refresh(true);
  resolveSilent({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{ id: 'friend-newer', display_name: 'Newer Friend' }],
    recommendations: [{ id: 'ignored-newer', display_name: 'Ignored Newer' }],
  }) });
  await silent;
  assert.equal(hub._state.recommendations.length, 0);

  resolveOpening({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{ id: 'friend-older', display_name: 'Older Friend' }],
    recommendations: [{ id: 'opening-recommendation', display_name: 'Opening Recommendation' }],
  }) });
  await opening;

  assert.equal(hub._state.friends[0].id, 'friend-newer');
  assert.equal(hub._state.recommendations[0].id, 'opening-recommendation');
  assert.equal(hub._state.loading, false);
});

test('an older requested refill can still replace recommendations after newer silent data', async () => {
  let visitHubReads = 0;
  let resolveRefill;
  let resolveSilent;
  const { hub } = loadHub({
    fetch: async () => {
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true,
          account: { id: 'account-1' },
          friends: [],
          recommendations: [{ id: 'recommendation-old', display_name: 'Old' }],
        }) };
      }
      if (visitHubReads === 2) return new Promise((resolve) => { resolveRefill = resolve; });
      return new Promise((resolve) => { resolveSilent = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const refill = hub.refresh(true, true);
  const silent = hub.refresh(true);
  resolveSilent({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{ id: 'friend-newer', display_name: 'Newer Friend' }],
    recommendations: [{ id: 'recommendation-ignored', display_name: 'Ignored' }],
  }) });
  await silent;

  resolveRefill({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{ id: 'friend-older', display_name: 'Older Friend' }],
    recommendations: [{ id: 'recommendation-refilled', display_name: 'Refilled' }],
  }) });
  await refill;

  assert.equal(hub._state.friends[0].id, 'friend-newer');
  assert.equal(hub._state.recommendations[0].id, 'recommendation-refilled');
});

test('an unchanged presence poll protects against an older full hub snapshot', async () => {
  let visitHubReads = 0;
  let resolveOlderRefresh;
  const currentFriend = {
    id: 'friend-1', display_name: 'Friend', presence_status: 'online',
    online: true, last_seen_at: '2026-10-28T06:01:00Z',
  };
  const { hub } = loadHub({
    fetch: async (url) => {
      if (String(url).includes('/api/community/friend-presence')) {
        return { ok: true, json: async () => ({ friends: [currentFriend] }) };
      }
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true, account: { id: 'account-1' }, friends: [currentFriend],
        }) };
      }
      return new Promise((resolve) => { resolveOlderRefresh = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  hub._state.open = true;

  const olderRefresh = hub.refresh(true);
  assert.equal(await hub._refreshPresence(), false);
  resolveOlderRefresh({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1' },
    friends: [{
      id: 'friend-1', display_name: 'Friend', presence_status: 'offline',
      online: false, last_seen_at: '2026-10-28T06:00:00Z',
    }],
  }) });
  await olderRefresh;

  assert.equal(hub._state.friends[0].presenceStatus, 'online');
  assert.equal(hub._state.friends[0].lastSeenAt, '2026-10-28T06:01:00Z');
});

test('dismissed recommendations stay hidden during an automatic follow refresh', async () => {
  const body = {
    logged_in: true,
    account: { id: 'account-1' },
    friends: [],
    recommendations: [
      { id: 'recommendation-a', display_name: 'A' },
      { id: 'recommendation-c', display_name: 'C' },
    ],
  };
  const { hub } = loadHub({ hubBody: body });
  await new Promise((resolve) => setImmediate(resolve));
  hub._state.recommendations = [
    { id: 'recommendation-a', displayName: 'A' },
    { id: 'recommendation-b', displayName: 'B' },
  ];

  hub._dismissRecommendation(hub._state.recommendations[0]);
  await hub.refresh(false);

  assert.equal(JSON.stringify(hub._state.recommendations.map((item) => item.id)), JSON.stringify([
    'recommendation-c',
  ]));
});

test('switching community accounts clears the previous account dismissals', async () => {
  let body = {
    logged_in: true,
    account: { id: 'account-1' },
    recommendations: [{ id: 'recommendation-shared', display_name: 'Shared' }],
  };
  const { hub } = loadHub({
    fetch: async () => ({ ok: true, json: async () => body }),
  });
  await new Promise((resolve) => setImmediate(resolve));

  hub._dismissRecommendation(hub._state.recommendations[0]);
  assert.equal(hub._state.recommendations.length, 0);

  body = {
    logged_in: true,
    account: { id: 'account-2' },
    recommendations: [{ id: 'recommendation-shared', display_name: 'Shared' }],
  };
  await hub.refresh(true);

  assert.equal(hub._state.accountId, 'account-2');
  assert.equal(hub._state.recommendations[0].id, 'recommendation-shared');
});

test('a successful follow keeps the current friend view when recommendation refill fails', async () => {
  let visitHubReads = 0;
  const { hub } = loadHub({
    fetch: async (url, options = {}) => {
      if (String(url).includes('/follow') && options.method === 'POST') {
        return { ok: true, json: async () => ({ following: true }) };
      }
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true,
          account: { id: 'account-1' },
          friends: [{ id: 'friend-1', display_name: 'Friend' }],
          recommendations: [{ id: 'recommendation-1', display_name: 'Recommended' }],
        }) };
      }
      return { ok: false, json: async () => ({ detail: 'community_unavailable' }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  await hub._followRecommendation(hub._state.recommendations[0]);

  assert.equal(hub._state.loggedIn, true);
  assert.equal(hub._state.friends[0].id, 'friend-1');
  assert.equal(hub._state.loadFailed, false);
  assert.equal(hub._state.recommendations.length, 0);
  assert.equal(hub._state.toastError, false);
});

test('a recommendation refill preserves another follow still in flight', async () => {
  let visitHubReads = 0;
  let resolveFollowA;
  let resolveFollowB;
  const { hub } = loadHub({
    fetch: async (url, options = {}) => {
      const text = String(url);
      if (text.includes('/recommendation-a/follow') && options.method === 'POST') {
        return new Promise((resolve) => { resolveFollowA = resolve; });
      }
      if (text.includes('/recommendation-b/follow') && options.method === 'POST') {
        return new Promise((resolve) => { resolveFollowB = resolve; });
      }
      visitHubReads += 1;
      return { ok: true, json: async () => ({
        logged_in: true,
        account: { id: 'account-1' },
        friends: [],
        recommendations: visitHubReads === 1 ? [
          { id: 'recommendation-a', display_name: 'A' },
          { id: 'recommendation-b', display_name: 'B' },
        ] : [{ id: 'recommendation-b', display_name: 'B' }],
      }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const followA = hub._followRecommendation(hub._state.recommendations[0]);
  const followB = hub._followRecommendation(hub._state.recommendations[1]);
  resolveFollowA({ ok: true, json: async () => ({ following: true }) });
  await followA;

  assert.equal(hub._state.recommendationBusy.has('recommendation-b'), true);

  resolveFollowB({ ok: true, json: async () => ({ following: true }) });
  await followB;
  assert.equal(hub._state.recommendationBusy.has('recommendation-b'), false);
});

test('keyboard focus survives the busy follow render and recommendation refill', async () => {
  let visitHubReads = 0;
  let resolveFollow;
  let resolveRefill;
  const { dom, hub, document } = loadMountedHub({
    fetch: async (url, options = {}) => {
      if (String(url).includes('/follow') && options.method === 'POST') {
        return new Promise((resolve) => { resolveFollow = resolve; });
      }
      visitHubReads += 1;
      if (visitHubReads === 1) {
        return { ok: true, json: async () => ({
          logged_in: true,
          account: { id: 'account-1', display_name: 'Current User' },
          friends: [],
          recommendations: [
            { id: 'a', display_name: 'A' },
            { id: 'b', display_name: 'B' },
          ],
        }) };
      }
      return new Promise((resolve) => { resolveRefill = resolve; });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const followButton = document.querySelector('[data-neko-focus-key="recommendation-a"]');
  assert.ok(followButton, document.body.innerHTML);
  followButton.focus();
  assert.equal(document.activeElement, followButton);

  const pending = hub._followRecommendation(hub._state.recommendations[0]);
  resolveFollow({ ok: true, json: async () => ({ following: true }) });
  await new Promise((resolve) => setImmediate(resolve));
  resolveRefill({ ok: true, json: async () => ({
    logged_in: true,
    account: { id: 'account-1', display_name: 'Current User' },
    friends: [],
    recommendations: [
      { id: 'b', display_name: 'B' },
      { id: 'c', display_name: 'C' },
    ],
  }) });
  await pending;

  assert.equal(
    document.activeElement.getAttribute('data-neko-focus-key'),
    'recommendation-b',
  );
  dom.window.close();
});

test('an old follow response cannot mutate a new account panel', async () => {
  let resolveFollow;
  const { hub } = loadHub({
    fetch: async (url) => {
      if (String(url).includes('/follow')) {
        return new Promise((resolve) => { resolveFollow = resolve; });
      }
      return { ok: true, json: async () => ({
        logged_in: true,
        account: { id: 'account-1' },
        recommendations: [{ id: 'recommendation-1', display_name: 'Recommended' }],
      }) };
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  const pending = hub._followRecommendation(hub._state.recommendations[0]);
  hub._state.accountId = 'account-2';
  hub._state.toast = 'new-account';
  hub._state.recommendations = [{ id: 'recommendation-1', displayName: 'New account item' }];
  hub._state.recommendationBusy.clear();
  hub._state.recommendationBusy.add('recommendation-1');
  resolveFollow({ ok: true, json: async () => ({ following: true }) });
  await pending;

  assert.equal(hub._state.toast, 'new-account');
  assert.equal(hub._state.recommendations[0].displayName, 'New account item');
  assert.equal(hub._state.recommendationBusy.has('recommendation-1'), true);
});
