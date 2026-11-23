'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');
const TEMPLATE_HTML = fs.readFileSync(
    path.join(ROOT, 'templates/badminton_demo.html'),
    'utf8'
);

function extractBadmintonCsrfHelpers(html) {
    const startMarker = '  function api(path) { return \'/api/game/badminton\' + path; }';
    const endMarker = '  function _isBadmintonGameMemoryEnabled() {';
    const startIdx = html.indexOf(startMarker);
    const endIdx = html.indexOf(endMarker, startIdx);
    assert.ok(startIdx !== -1, 'expected start of badminton CSRF helper block');
    assert.ok(endIdx !== -1 && endIdx > startIdx, 'expected end of badminton CSRF helper block');
    return html.slice(startIdx, endIdx);
}

const HELPER_SOURCE = extractBadmintonCsrfHelpers(TEMPLATE_HTML);

function loadBadmintonCsrfRuntime({ fetchImpl, fastTimeoutMs = 25 } = {}) {
    const windowObj = {
        AbortController,
        nekoLocalMutationSecurity: undefined,
    };
    const context = {
        window: windowObj,
        AbortController,
        fetch: fetchImpl,
        setTimeout: (fn, ms, ...args) => setTimeout(
            fn,
            fastTimeoutMs !== null && ms === 4000 ? fastTimeoutMs : ms,
            ...args
        ),
        clearTimeout,
        console,
    };
    vm.createContext(context);
    vm.runInContext(HELPER_SOURCE, context);
    return { context, windowObj };
}

test('PR3981 #68: stalled eager CSRF bootstrap times out, aborts socket, clears inflight cache, and recovers on next post', async () => {
    let pageConfigCalls = 0;
    let firstBootstrapAborted = false;
    let failFirstGet = true;
    const postCalls = [];

    const { context } = loadBadmintonCsrfRuntime({
        fastTimeoutMs: 25,
        fetchImpl: async (url, init = {}) => {
            const target = String(url);
            if (target === '/api/config/page_config') {
                pageConfigCalls += 1;
                if (failFirstGet && pageConfigCalls === 1) {
                    return new Promise((_, reject) => {
                        init.signal?.addEventListener('abort', () => {
                            firstBootstrapAborted = true;
                            reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
                        }, { once: true });
                    });
                }
                return {
                    ok: true,
                    json: async () => ({ autostart_csrf_token: 'tok-recovered' }),
                };
            }
            if (target.startsWith('/api/game/badminton/')) {
                postCalls.push({ url: target, headers: { ...(init.headers || {}) }, body: init.body });
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ ok: true }),
                };
            }
            throw new Error(`unexpected fetch: ${target}`);
        },
    });

    // Simulate eager standalone page bootstrap: _getBadmintonMutationHeaders(false).catch(() => {})
    context._getBadmintonMutationHeaders(false).catch(() => {});

    // First route start joins stalled bootstrap -> hits 4s (25ms compressed) deadline before 22s post timeout.
    await assert.rejects(
        () => context.post('/route/start', { session_id: 's1' }, 22000),
        (err) => err && err.name === 'AbortError' && err.code === 'csrf_bootstrap_timeout'
    );
    assert.equal(firstBootstrapAborted, true, 'expected stalled bootstrap AbortController to abort socket');
    assert.equal(pageConfigCalls, 1);
    assert.equal(postCalls.length, 0);

    // Subsequent route start must issue a fresh configuration GET and succeed once upstream recovers.
    failFirstGet = false;
    const recovered = await context.post('/route/start', { session_id: 's2' }, 22000);
    assert.equal(recovered.ok, true);
    assert.equal(pageConfigCalls, 2);
    assert.equal(postCalls.length, 1);
    assert.equal(postCalls[0].headers['X-CSRF-Token'], 'tok-recovered');
});

test('PR3981 #68: stalled r.json() ignoring AbortSignal still triggers bootstrap deadline and clears inflight cache', async () => {
    let pageConfigCalls = 0;
    const postCalls = [];

    const { context } = loadBadmintonCsrfRuntime({
        fastTimeoutMs: 25,
        fetchImpl: async (url, init = {}) => {
            const target = String(url);
            if (target === '/api/config/page_config') {
                pageConfigCalls += 1;
                if (pageConfigCalls === 1) {
                    return {
                        ok: true,
                        json: () => new Promise(() => {}),
                    };
                }
                return {
                    ok: true,
                    json: async () => ({ autostart_csrf_token: 'tok-json-recovered' }),
                };
            }
            if (target.startsWith('/api/game/badminton/')) {
                postCalls.push({ url: target, headers: { ...(init.headers || {}) } });
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ ok: true }),
                };
            }
            throw new Error(`unexpected fetch: ${target}`);
        },
    });

    await assert.rejects(
        () => context.post('/route/start', { session_id: 's-json-hang' }, 22000),
        (err) => err && err.name === 'AbortError' && err.code === 'csrf_bootstrap_timeout'
    );
    assert.equal(pageConfigCalls, 1);
    assert.equal(postCalls.length, 0);

    const hb = await context.post('/route/heartbeat', { session_id: 's-json-hang' }, 5000);
    assert.equal(hb.ok, true);
    assert.equal(pageConfigCalls, 2);
    assert.equal(postCalls.length, 1);
    assert.equal(postCalls[0].headers['X-CSRF-Token'], 'tok-json-recovered');
});

test('PR3981 #68: out-of-order forceRefresh responses do not overwrite newer cached CSRF token', async () => {
    let resolveFirst;
    let resolveSecond;
    const firstGate = new Promise((resolve) => { resolveFirst = resolve; });
    const secondGate = new Promise((resolve) => { resolveSecond = resolve; });
    let pageConfigCalls = 0;

    const { context } = loadBadmintonCsrfRuntime({
        fastTimeoutMs: 5000,
        fetchImpl: async (url) => {
            assert.equal(String(url), '/api/config/page_config');
            pageConfigCalls += 1;
            const callIdx = pageConfigCalls;
            if (callIdx === 1) {
                await firstGate;
                return { ok: true, json: async () => ({ autostart_csrf_token: 'tok-old' }) };
            }
            await secondGate;
            return { ok: true, json: async () => ({ autostart_csrf_token: 'tok-new' }) };
        },
    });

    const firstPromise = context._getBadmintonMutationHeaders(false);
    const secondPromise = context._getBadmintonMutationHeaders(true);

    resolveSecond();
    const secondHeaders = await secondPromise;
    assert.equal(secondHeaders['X-CSRF-Token'], 'tok-new');

    resolveFirst();
    const firstHeaders = await firstPromise;
    assert.equal(firstHeaders['X-CSRF-Token'], undefined);
    assert.equal(context._getCachedBadmintonCsrfToken(), 'tok-new');
});
