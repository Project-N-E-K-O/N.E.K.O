const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-prompt-shared.js'), 'utf8');
const tick = () => new Promise(setImmediate);
function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((r, j) => { resolve = r; reject = j; });
    return { promise, resolve, reject };
}
function setup(storageBarrier, fetch) {
    const timers = new Map();
    let now = 0;
    let nextTimer = 0;
    const window = {
        __nekoStorageLocationStartupBarrier: storageBarrier,
        pageConfigReady: new Promise(() => {}),
        location: { search: '?lanlan_name=Fixture+Role' },
        addEventListener() {},
    };
    vm.runInNewContext(source, {
        window, fetch, URLSearchParams, AbortController,
        console: { log() {}, debug() {}, warn() {} },
        document: { addEventListener() {}, visibilityState: 'visible' },
        setTimeout(f, ms) { const id = ++nextTimer; timers.set(id, { f, at: now + ms }); return id; },
        clearTimeout(id) { timers.delete(id); },
    });
    return {
        token: window.nekoLocalMutationSecurity,
        tools: window.nekoPromptShared.createPromptTools(),
        get pendingTimers() { return timers.size; },
        async advance(ms) {
            const end = now + ms;
            await tick();
            while (true) {
                const due = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
                if (!due || due[1].at > end) break;
                now = due[1].at;
                timers.delete(due[0]);
                due[1].f();
                await tick();
            }
            now = end;
        },
    };
}
const success = token => ({ ok: true, json: async () => ({ autostart_csrf_token: token }) });
const header = response => response['X-CSRF-Token'];
const configUrl = '/api/config/page_config?lanlan_name=Fixture%20Role';

test('local mutation token: held storage blocks both first read and refresh; after release renderer readiness is irrelevant', async () => {
    const storage = deferred(), requests = [];
    const lane = setup(storage.promise, async (url, init) => {
        requests.push([url, init.method || 'GET', init.headers?.['X-CSRF-Token']]);
        return url === configUrl ? success('fresh-fixture') : { ok: true, status: 200, json: async () => ({ accepted: true }) };
    });
    const action = lane.tools.requestJson('/api/autostart-prompt/decision', { method: 'POST', json: { decision: 'later' } });
    await lane.advance(3500);
    assert.deepEqual(requests, [], 'former initial-wait threshold is not permission to read');
    const refresh = lane.token.refreshToken();
    await lane.advance(10001);
    assert.deepEqual(requests, [], 'direct refresh also waits for storage');
    assert.equal(lane.pendingTimers, 0, 'HTTP deadline does not run during storage selection');
    storage.resolve({ canContinue: true });
    assert.equal(await refresh, 'fresh-fixture');
    assert.equal((await action).accepted, true);
    assert.deepEqual(requests, [[configUrl, 'GET', undefined], ['/api/autostart-prompt/decision', 'POST', 'fresh-fixture']]);
});

test('local mutation token: non-home pages without the storage barrier fetch immediately and cache the valid token', async () => {
    let gets = 0;
    const lane = setup(undefined, async () => { gets++; return success('cached-fixture'); });
    assert.equal(header(await lane.token.getMutationHeaders()), 'cached-fixture');
    assert.equal(header(await lane.token.getMutationHeaders()), 'cached-fixture');
    assert.equal(gets, 1);
});

test('local mutation token: an unsuccessful storage barrier never sends a config read', async () => {
    for (const reject of [false, true]) {
        const storage = deferred();
        let reads = 0;
        const lane = setup(storage.promise, async () => { reads++; return success('unused-fixture'); });
        const headers = lane.token.getMutationHeaders();
        if (reject) storage.reject(new Error('fixture storage error'));
        else storage.resolve({ canContinue: false });
        assert.equal(header(await headers), undefined);
        assert.equal(reads, 0);
        assert.equal(lane.pendingTimers, 0);
    }
});

test('local mutation token: transient config failure is retriable and parallel calls share each fetch', async () => {
    let gets = 0;
    const lane = setup(undefined, async () => ++gets === 1 ? { ok: false, status: 503 } : success('recovered-fixture'));
    const pair = () => Promise.all([lane.token.getMutationHeaders(), lane.token.getMutationHeaders()]);
    assert.deepEqual((await pair()).map(header), [undefined, undefined]);
    assert.equal(gets, 1);
    assert.deepEqual((await pair()).map(header), ['recovered-fixture', 'recovered-fixture']);
    assert.equal(header(await lane.token.getMutationHeaders()), 'recovered-fixture');
    assert.equal(gets, 2);
});

for (const state of ['headers', 'body']) test('local mutation token: stalled ' + state + ' times out and cannot poison the new token', async () => {
    const stale = deferred(), storage = deferred();
    let gets = 0, oldSignal;
    const lane = setup(storage.promise, async (url, init) => {
        assert.equal(url, configUrl);
        if (++gets === 1) { oldSignal = init.signal; return state === 'headers' ? stale.promise : { ok: true, json: () => stale.promise }; }
        return success('new-fixture');
    });
    const first = lane.token.getMutationHeaders(), parallel = lane.token.getMutationHeaders();
    await lane.advance(11000);
    assert.equal(gets, 0);
    storage.resolve({ canContinue: true });
    await lane.advance(9999);
    assert.equal(gets, 1);
    assert.equal(oldSignal.aborted, false);
    await lane.advance(1);
    assert.equal(oldSignal.aborted, true);
    assert.deepEqual((await Promise.all([first, parallel])).map(header), [undefined, undefined]);
    assert.equal(header(await lane.token.getMutationHeaders()), 'new-fixture');
    stale.resolve(state === 'headers' ? success('old-network-fixture') : { autostart_csrf_token: 'old-network-fixture' });
    await tick();
    assert.equal(lane.token.peekCachedToken(), 'new-fixture');
    assert.equal(gets, 2);
});

test('local mutation token: explicit refresh joins an already in-flight direct read', async () => {
    let gets = 0;
    const response = deferred(), lane = setup(undefined, async () => { gets++; return response.promise; });
    const first = lane.token.getMutationHeaders();
    await tick();
    const refresh = lane.token.refreshToken();
    assert.equal(gets, 1);
    response.resolve(success('one-fixture'));
    assert.equal(await refresh, 'one-fixture');
    assert.equal(header(await first), 'one-fixture');
    assert.equal(gets, 1);
});
