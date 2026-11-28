const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-prompt-shared.js'), 'utf8');
const tick = () => new Promise(setImmediate);
function deferred() {
    let resolve;
    const promise = new Promise(r => { resolve = r; });
    return { promise, resolve };
}
function setup(ready, fetch) {
    const timers = new Map();
    let nextTimer = 0;
    const window = { pageConfigReady: ready, location: { search: '?lanlan_name=Fixture+Role' }, addEventListener() {} };
    vm.runInNewContext(source, {
        window, fetch, URLSearchParams, AbortController,
        console: { log() {}, debug() {}, warn() {} },
        document: { addEventListener() {}, visibilityState: 'visible' },
        setTimeout(f, ms) { const id = ++nextTimer; timers.set(id, { f, ms }); return id; },
        clearTimeout(id) { timers.delete(id); },
    });
    return {
        token: window.nekoLocalMutationSecurity,
        tools: window.nekoPromptShared.createPromptTools(),
        async fire(ms) {
            const found = [...timers].find(([, value]) => value.ms === ms);
            assert.ok(found, 'expected bounded timer ' + ms);
            timers.delete(found[0]); found[1].f();
            await tick();
        },
    };
}
const success = token => ({ ok: true, json: async () => ({ autostart_csrf_token: token }) });
const header = response => response['X-CSRF-Token'];
const configUrl = '/api/config/page_config?lanlan_name=Fixture%20Role';

test('a usable ready token stays a no-fetch fast path', async () => {
    const lane = setup(Promise.resolve({ autostart_csrf_token: 'fast-fixture' }), () => assert.fail('no direct config read'));
    assert.equal(header(await lane.token.getMutationHeaders()), 'fast-fixture');
});

test('a protected first user action is sent while unrelated page readiness is pending', async () => {
    const held = deferred(), requests = [];
    const lane = setup(held.promise, async (url, init) => {
        requests.push([url, init.method || 'GET', init.headers?.['X-CSRF-Token']]);
        return url === configUrl ? success('fresh-fixture') : { ok: true, status: 200, json: async () => ({ accepted: true }) };
    });
    const first = lane.tools.requestJson('/api/autostart-prompt/decision', { method: 'POST', json: { action: 'skip' } });
    assert.deepEqual(requests, []);
    await lane.fire(3000);
    assert.equal((await first).accepted, true);
    assert.deepEqual(requests, [[configUrl, 'GET', undefined], ['/api/autostart-prompt/decision', 'POST', 'fresh-fixture']]);
    held.resolve({ autostart_csrf_token: 'retired-fixture' });
    await tick();
    assert.equal(lane.token.peekCachedToken(), 'fresh-fixture');
});

test('a transient config failure is retriable and parallel calls share one direct fetch', async () => {
    let gets = 0;
    const lane = setup(Promise.resolve(true), async () => ++gets === 1 ? { ok: false, status: 503 } : success('recovered-fixture'));
    const pair = () => Promise.all([lane.token.getMutationHeaders(), lane.token.getMutationHeaders()]);
    assert.deepEqual((await pair()).map(header), [undefined, undefined]);
    assert.equal(gets, 1);
    assert.deepEqual((await pair()).map(header), ['recovered-fixture', 'recovered-fixture']);
    assert.equal(header(await lane.token.getMutationHeaders()), 'recovered-fixture');
    assert.equal(gets, 2);
});

for (const state of ['headers', 'body']) test('a direct ' + state + ' stall is aborted, retried, and cannot poison the new token', async () => {
    const stale = deferred(), held = deferred();
    let gets = 0, oldSignal;
    const lane = setup(held.promise, async (url, init) => {
        assert.equal(url, configUrl);
        if (++gets === 1) { oldSignal = init.signal; return state === 'headers' ? stale.promise : { ok: true, json: () => stale.promise }; }
        return success('new-fixture');
    });
    const first = lane.token.getMutationHeaders(), parallel = lane.token.getMutationHeaders();
    await lane.fire(3000);
    assert.equal(gets, 1);
    await lane.fire(10000);
    assert.equal(oldSignal.aborted, true);
    assert.deepEqual((await Promise.all([first, parallel])).map(header), [undefined, undefined]);
    assert.equal(header(await lane.token.getMutationHeaders()), 'new-fixture');
    held.resolve({ autostart_csrf_token: 'old-ready-fixture' });
    stale.resolve(state === 'headers' ? success('old-network-fixture') : { autostart_csrf_token: 'old-network-fixture' });
    await tick();
    assert.equal(lane.token.peekCachedToken(), 'new-fixture');
    assert.equal(gets, 2);
});

test('a completed explicit refresh wins over a later ready snapshot', async () => {
    const held = deferred(), response = deferred();
    let gets = 0;
    const lane = setup(held.promise, async () => { gets++; return response.promise; });
    const first = lane.token.getMutationHeaders(), refresh = lane.token.refreshToken();
    response.resolve(success('latest-fixture'));
    assert.equal(await refresh, 'latest-fixture');
    held.resolve({ autostart_csrf_token: 'old-fixture' });
    assert.equal(header(await first), 'latest-fixture');
    assert.equal(lane.token.peekCachedToken(), 'latest-fixture');
    assert.equal(gets, 1);
});

test('explicit refresh joins an existing first fallback request', async () => {
    let gets = 0;
    const response = deferred(), lane = setup(new Promise(() => {}), async () => { gets++; return response.promise; });
    const first = lane.token.getMutationHeaders();
    await lane.fire(3000);
    const refresh = lane.token.refreshToken();
    assert.equal(gets, 1);
    response.resolve(success('one-fixture'));
    assert.equal(await refresh, 'one-fixture');
    assert.equal(header(await first), 'one-fixture');
    assert.equal(gets, 1);
});
