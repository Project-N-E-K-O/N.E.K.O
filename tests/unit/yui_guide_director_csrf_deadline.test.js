'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');
const PAGE_FLOWS_SOURCE = fs.readFileSync(
    path.join(ROOT, 'static/tutorial/yui-guide/director/page-flows.js'),
    'utf8'
);

function loadDirectorPageFlows({ fetchImpl, security, compressTimeoutRange = true } = {}) {
    const warnings = [];
    const director = {
        options: { homeInteractionApi: null },
        extendDirector(methods) {
            Object.assign(director, methods);
        },
        resolveWithTimeout: (p) => Promise.resolve(p),
        wait: () => Promise.resolve(),
        clamp: (v, min, max) => Math.max(min, Math.min(max, v)),
    };

    const windowObj = {
        __YuiGuideDirector: director,
        nekoLocalMutationSecurity: security,
        setTimeout: (fn, ms, ...args) => setTimeout(
            fn,
            compressTimeoutRange && ms >= 1000 && ms <= 4000 ? 30 : ms,
            ...args
        ),
        clearTimeout,
    };

    const context = {
        window: windowObj,
        AbortController,
        fetch: fetchImpl,
        Date,
        Math,
        console: {
            log() {},
            warn(...args) { warnings.push(args.join(' ')); },
            error() {},
        },
    };
    vm.createContext(context);
    vm.runInContext(PAGE_FLOWS_SOURCE, context);
    return { director, windowObj, warnings };
}

test('PR3981 #72: stalled nekoLocalMutationSecurity.getMutationHeaders respects command deadline with zero POSTs', async () => {
    const cmdPosts = [];
    const { director, warnings } = loadDirectorPageFlows({
        security: {
            getMutationHeaders: () => new Promise(() => {}),
        },
        fetchImpl: async (url) => {
            cmdPosts.push(String(url));
            return { ok: true, status: 200, json: async () => ({ success: true }) };
        },
    });

    const ok = await director.setAgentMasterEnabled(true);
    assert.equal(ok, false);
    assert.equal(cmdPosts.length, 0, 'must not send /api/agent/command POST when CSRF helper stalls');
    assert.ok(warnings.some((w) => w.includes('超时') || w.includes('失败')), 'expected warning on timeout');
});

test('PR3981 #72: stalled refreshToken during 403 csrf_validation_failed retry respects shared deadline', async () => {
    const cmdPosts = [];
    const { director } = loadDirectorPageFlows({
        security: {
            getMutationHeaders: async () => ({ 'X-CSRF-Token': 'tok-stale' }),
            refreshToken: () => new Promise(() => {}),
        },
        fetchImpl: async (url, init = {}) => {
            if (String(url) === '/api/agent/command') {
                cmdPosts.push({ headers: { ...(init.headers || {}) } });
                return {
                    ok: false,
                    status: 403,
                    clone() {
                        return {
                            json: async () => ({ error_code: 'csrf_validation_failed' }),
                        };
                    },
                };
            }
            throw new Error(`unexpected fetch: ${url}`);
        },
    });

    const ok = await director.setAgentFlagEnabled('computer_use_enabled', true);
    assert.equal(ok, false);
    assert.equal(cmdPosts.length, 1, 'must fail on shared deadline without stalled second POST');
    assert.equal(cmdPosts[0].headers['X-CSRF-Token'], 'tok-stale');
});

test('PR3981 #72: stalled /api/config/page_config fallback aborts socket on shared deadline with zero POSTs', async () => {
    let pageConfigCalls = 0;
    let pageConfigAborted = false;
    const cmdPosts = [];

    const { director } = loadDirectorPageFlows({
        fetchImpl: async (url, init = {}) => {
            const target = String(url);
            if (target === '/api/config/page_config') {
                pageConfigCalls += 1;
                return new Promise((_, reject) => {
                    init.signal?.addEventListener('abort', () => {
                        pageConfigAborted = true;
                        reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }));
                    }, { once: true });
                });
            }
            if (target === '/api/agent/command') {
                cmdPosts.push(target);
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            }
            throw new Error(`unexpected fetch: ${target}`);
        },
    });

    const ok = await director.setAgentMasterEnabled(false);
    assert.equal(ok, false);
    assert.equal(pageConfigCalls, 1);
    assert.equal(pageConfigAborted, true, 'expected fallback page_config fetch to abort on deadline');
    assert.equal(cmdPosts.length, 0);
});

test('PR3981 #72: 403 csrf_validation_failed retry within deadline refreshes token and completes with exact 2 POSTs', async () => {
    let pageConfigCalls = 0;
    const cmdPosts = [];
    let failFirstWithCsrf = true;

    const { director } = loadDirectorPageFlows({
        fetchImpl: async (url, init = {}) => {
            const target = String(url);
            if (target === '/api/config/page_config') {
                pageConfigCalls += 1;
                return {
                    ok: true,
                    json: async () => ({ autostart_csrf_token: `tok-${pageConfigCalls}` }),
                };
            }
            if (target === '/api/agent/command') {
                cmdPosts.push({ headers: { ...(init.headers || {}) }, body: JSON.parse(init.body) });
                if (failFirstWithCsrf) {
                    failFirstWithCsrf = false;
                    return {
                        ok: false,
                        status: 403,
                        clone() {
                            return {
                                json: async () => ({ error_code: 'csrf_validation_failed' }),
                            };
                        },
                    };
                }
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            }
            throw new Error(`unexpected fetch: ${target}`);
        },
    });

    const ok = await director.setAgentMasterEnabled(true);
    assert.equal(ok, true);
    assert.equal(pageConfigCalls, 2);
    assert.equal(cmdPosts.length, 2);
    assert.equal(cmdPosts[0].headers['X-CSRF-Token'], 'tok-1');
    assert.equal(cmdPosts[1].headers['X-CSRF-Token'], 'tok-2');
});

test('PR3981 #76: stalled 403 response body aborts underlying request signal on deadline', async () => {
    const cmdPosts = [];
    let capturedSignal = null;
    let postAborted = false;

    const { director } = loadDirectorPageFlows({
        fetchImpl: async (url, init = {}) => {
            const target = String(url);
            if (target === '/api/config/page_config') {
                return {
                    ok: true,
                    json: async () => ({ autostart_csrf_token: 'tok-403-hang' }),
                };
            }
            if (target === '/api/agent/command') {
                cmdPosts.push({ headers: { ...(init.headers || {}) } });
                capturedSignal = init.signal || null;
                assert.ok(capturedSignal, 'expected /api/agent/command POST to carry AbortSignal');
                assert.equal(capturedSignal.aborted, false);
                capturedSignal.addEventListener('abort', () => {
                    postAborted = true;
                }, { once: true });
                return {
                    ok: false,
                    status: 403,
                    clone() {
                        return {
                            json: () => new Promise(() => {}),
                        };
                    },
                };
            }
            throw new Error(`unexpected fetch: ${target}`);
        },
    });

    const ok = await director.setAgentMasterEnabled(true);
    assert.equal(ok, false);
    assert.equal(cmdPosts.length, 1);
    assert.equal(postAborted, true, 'expected 403 stalled body timeout to abort underlying POST signal');
    assert.equal(capturedSignal?.aborted, true);
});

