'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const test = require('node:test');

const root = path.resolve(__dirname, '../..');
const frontendRequire = createRequire(
    process.env.NEKO_FRONTEND_PACKAGE || path.join(root, 'frontend/react-neko-chat/package.json')
);
const { JSDOM } = frontendRequire('jsdom');

function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

async function flushMicrotasks(n = 20) {
    for (let i = 0; i < n; i++) await Promise.resolve();
}

function createEmotionManagerDom(fetchHandler) {
    const html = fs.readFileSync(path.join(root, 'templates/live2d_emotion_manager.html'), 'utf8');
    const markerStart = 'const modelSelect = document.getElementById(\'model-select\');';
    const startIdx = html.indexOf(markerStart);
    const endIdx = html.indexOf('</script>', startIdx);
    assert.ok(startIdx >= 0 && endIdx > startIdx, 'must locate inline script in live2d_emotion_manager.html');
    const inlineScript = '(function () {\n'
        + html.slice(startIdx, endIdx)
        + '\nwindow.saveEmotionMapping = saveEmotionMapping;\n})();\n';

    const dom = new JSDOM(html, {
        url: 'http://127.0.0.1:48911/live2d_emotion_manager',
        runScripts: 'outside-only',
        pretendToBeVisual: true,
    });
    const w = dom.window;
    w.CSS = { escape: value => String(value).replace(/"/g, '\\"') };
    w.i18n = { isInitialized: true, t: (k, p) => (typeof p === 'string' ? p : k) };
    w.i18next = w.i18n;
    w.t = (k, p) => (typeof p === 'string' ? p : k);
    w.requestAnimationFrame = () => 0;
    w.setTimeout = () => 0;
    w.clearTimeout = () => {};
    w.setInterval = () => 0;
    w.clearInterval = () => {};
    w.console = { log() {}, warn() {}, error: (...args) => console.error('[emotion_mgr]', ...args) };
    w.fetch = fetchHandler;

    w.eval(inlineScript);
    return { dom, w, doc: w.document };
}

test('F0507: switching models hides config, disables save/reset until ready, and ignores late responses', async () => {
    const modelAFilesGate = deferred();
    const modelAMappingGate = deferred();
    const postCalls = [];

    const { w, doc } = createEmotionManagerDom(async (url, init = {}) => {
        const u = String(url);
        if (u === '/api/live2d/models') {
            return {
                ok: true,
                status: 200,
                json: async () => ([
                    { name: 'ModelA', source: 'user_mods' },
                    { name: 'ModelB', source: 'user_mods' },
                    { name: 'ModelC', source: 'user_mods' },
                ]),
            };
        }
        if (u === '/api/live2d/model_files/ModelA') {
            return {
                ok: true,
                status: 200,
                json: async () => await modelAFilesGate.promise,
            };
        }
        if (u === '/api/live2d/model_files/ModelB') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    motion_files: ['motions/b_happy.motion3.json'],
                    expression_files: ['expressions/b_smile.exp3.json'],
                }),
            };
        }
        if (u === '/api/live2d/model_files/ModelC') {
            return {
                ok: false,
                status: 500,
                statusText: 'Internal Server Error',
                json: async () => ({ success: false }),
            };
        }
        if (u === '/api/live2d/emotion_mapping/ModelA') {
            return {
                ok: true,
                status: 200,
                json: async () => await modelAMappingGate.promise,
            };
        }
        if (u === '/api/live2d/emotion_mapping/ModelB') {
            if ((init.method || 'GET') === 'POST') {
                postCalls.push({ url: u, body: JSON.parse(init.body) });
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            }
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    config: {
                        motions: { happy: ['motions/b_happy.motion3.json'] },
                        expressions: { happy: ['expressions/b_smile.exp3.json'] },
                    },
                }),
            };
        }
        if (u === '/api/live2d/model_config/ModelB') {
            if ((init.method || 'GET') === 'POST') {
                postCalls.push({ url: u, body: JSON.parse(init.body) });
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            }
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    config: {
                        FileReferences: { Motions: {}, Expressions: [] },
                    },
                }),
            };
        }
        return { ok: true, status: 200, json: async () => ({ success: true }) };
    });

    await flushMicrotasks();

    const items = Array.from(doc.querySelectorAll('.singleselect-item'));
    const itemA = items.find(el => el.dataset.value === 'ModelA');
    const itemB = items.find(el => el.dataset.value === 'ModelB');
    const itemC = items.find(el => el.dataset.value === 'ModelC');
    const emotionConfig = doc.getElementById('emotion-config');
    const saveBtn = doc.getElementById('save-btn');
    const resetBtn = doc.getElementById('reset-btn');

    // Step 1: Select ModelA (files pending) -> config hidden, save/reset disabled
    itemA.click();
    assert.equal(emotionConfig.style.display, 'none');
    assert.equal(saveBtn.disabled, true);
    assert.equal(resetBtn.disabled, true);

    // Step 2: Finish ModelA completely so checkboxes are populated & visible
    modelAFilesGate.resolve({
        success: true,
        motion_files: ['motions/a_old.motion3.json'],
        expression_files: ['expressions/a_old.exp3.json'],
    });
    modelAMappingGate.resolve({
        success: true,
        config: {
            motions: { happy: ['motions/a_old.motion3.json'] },
            expressions: {},
        },
    });
    await flushMicrotasks(30);
    assert.equal(emotionConfig.style.display, 'block');
    assert.equal(saveBtn.disabled, false);

    // Step 3: Now switch to ModelC (fails with HTTP 500) -> old ModelA checkboxes must be cleared, config hidden, save disabled, save click sends 0 POSTs
    itemC.click();
    assert.equal(emotionConfig.style.display, 'none', 'must hide config immediately on switch');
    assert.equal(saveBtn.disabled, true, 'must disable save immediately on switch');
    assert.equal(
        doc.querySelectorAll('.emotion-motion-select[data-emotion="happy"] input').length,
        0,
        'must clear old ModelA checkboxes immediately on switch'
    );
    await flushMicrotasks(30);
    assert.equal(emotionConfig.style.display, 'none', 'failed model load must keep config hidden');
    assert.equal(saveBtn.disabled, true, 'failed model load must keep save disabled');
    await w.saveEmotionMapping();
    assert.equal(postCalls.length, 0, 'saving while unready must send 0 POSTs');

    // Step 4: Race condition — select ModelB after failed ModelC (or test slow ModelA mapping vs ModelB)
    itemB.click();
    await flushMicrotasks(30);
    assert.equal(emotionConfig.style.display, 'block');
    assert.equal(saveBtn.disabled, false);
    const happyMotionB = doc.querySelector('.emotion-motion-select[data-emotion="happy"] input');
    assert.ok(happyMotionB && happyMotionB.value === 'motions/b_happy.motion3.json');
    assert.equal(happyMotionB.checked, true);

    // Clicking save now posts ModelB's own motions, never ModelA's, and emits 0 POST /model_config calls
    await w.saveEmotionMapping();
    assert.equal(postCalls.length, 1);
    assert.equal(postCalls[0].url, '/api/live2d/emotion_mapping/ModelB');
    assert.deepEqual(postCalls[0].body.motions, { happy: ['motions/b_happy.motion3.json'] });
});

test('F0507: late loadEmotionMapping response from previous selection cannot clobber newer model checkboxes', async () => {
    const slowMappingA = deferred();
    const { doc } = createEmotionManagerDom(async (url) => {
        const u = String(url);
        if (u === '/api/live2d/models') {
            return {
                ok: true,
                status: 200,
                json: async () => ([
                    { name: 'ModelA', source: 'user_mods' },
                    { name: 'ModelB', source: 'user_mods' },
                ]),
            };
        }
        if (u === '/api/live2d/model_files/ModelA') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    motion_files: ['motions/shared.motion3.json'],
                    expression_files: [],
                }),
            };
        }
        if (u === '/api/live2d/model_files/ModelB') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    motion_files: ['motions/shared.motion3.json'],
                    expression_files: [],
                }),
            };
        }
        if (u === '/api/live2d/emotion_mapping/ModelA') {
            return {
                ok: true,
                status: 200,
                json: async () => await slowMappingA.promise,
            };
        }
        if (u === '/api/live2d/emotion_mapping/ModelB') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    config: {
                        motions: { happy: ['motions/shared.motion3.json'] },
                        expressions: {},
                    },
                }),
            };
        }
        return { ok: true, status: 200, json: async () => ({ success: true }) };
    });

    await flushMicrotasks();
    const items = Array.from(doc.querySelectorAll('.singleselect-item'));
    items[0].click(); // ModelA: files resolve immediately, mapping awaits slowMappingA
    await flushMicrotasks(10);

    items[1].click(); // ModelB: finishes completely with happy checked
    await flushMicrotasks(30);

    const happyCb = doc.querySelector('.emotion-motion-select[data-emotion="happy"] input');
    assert.equal(happyCb.checked, true);

    // Late ModelA mapping resolves with happy UNCHECKED and sad CHECKED -> must be discarded!
    slowMappingA.resolve({
        success: true,
        config: {
            motions: { sad: ['motions/shared.motion3.json'] },
            expressions: {},
        },
    });
    await flushMicrotasks(30);

    assert.equal(
        doc.querySelector('.emotion-motion-select[data-emotion="happy"] input').checked,
        true,
        'late ModelA mapping must not uncheck ModelB happy'
    );
    assert.equal(
        doc.querySelector('.emotion-motion-select[data-emotion="sad"] input').checked,
        false,
        'late ModelA mapping must not check ModelB sad'
    );
});

test('F0063: saveEmotionMapping uses single atomic POST preserving non-UI motions/expressions without touching /model_config', async () => {
    const postBodies = [];
    const modelConfigCalls = [];
    let failPreSaveMappingMode = null;

    const { w, doc } = createEmotionManagerDom(async (url, init = {}) => {
        const u = String(url);
        if (u === '/api/live2d/models') {
            return {
                ok: true,
                status: 200,
                json: async () => ([{ name: 'ModelRich', source: 'user_mods' }]),
            };
        }
        if (u === '/api/live2d/model_files/ModelRich') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    motion_files: ['motions/happy1.motion3.json', 'motions/tap.motion3.json'],
                    expression_files: ['expressions/happy.exp3.json', 'expressions/perm.exp3.json'],
                }),
            };
        }
        if (u.startsWith('/api/live2d/model_config')) {
            modelConfigCalls.push(u);
            throw new Error(`Unexpected /model_config call: ${u}`);
        }
        if (u === '/api/live2d/emotion_mapping/ModelRich') {
            if ((init.method || 'GET') === 'POST') {
                postBodies.push(JSON.parse(init.body));
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            }
            if (failPreSaveMappingMode === 'http500') {
                return { ok: false, status: 500, statusText: 'Server Error', json: async () => ({}) };
            }
            if (failPreSaveMappingMode === 'biz_fail') {
                return { ok: true, status: 200, json: async () => ({ success: false, error: 'boom' }) };
            }
            if (failPreSaveMappingMode === 'network') {
                throw new Error('ECONNRESET');
            }
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    config: {
                        motions: {
                            TapBody: ['motions/tap.motion3.json'],
                            idle: ['motions/idle_lower.motion3.json'],
                            empty_group: [],
                            happy: ['motions/happy1.motion3.json'],
                        },
                        expressions: {
                            '常驻': ['exp/blush.exp3.json', 'exp/glasses.exp3.json'],
                            custom_group: ['exp/cg.exp3.json'],
                            legacy_empty: [],
                        },
                    },
                }),
            };
        }
        return { ok: true, status: 200, json: async () => ({ success: true }) };
    });

    await flushMicrotasks();
    doc.querySelector('.singleselect-item').click();
    await flushMicrotasks(30);

    // Save with happy checked -> TapBody, idle, empty_group, multi-file '常驻', custom_group preserved; legacy_empty skipped; 0 /model_config calls
    await w.saveEmotionMapping();
    assert.equal(postBodies.length, 1);
    assert.equal(modelConfigCalls.length, 0, 'Option A must not call /api/live2d/model_config*');
    assert.deepEqual(postBodies[0].motions, {
        TapBody: ['motions/tap.motion3.json'],
        idle: ['motions/idle_lower.motion3.json'],
        empty_group: [],
        happy: ['motions/happy1.motion3.json'],
    });
    assert.deepEqual(postBodies[0].expressions, {
        '常驻': ['exp/blush.exp3.json', 'exp/glasses.exp3.json'],
        custom_group: ['exp/cg.exp3.json'],
    });

    // Reset + Save -> clears UI emotion 'happy', still preserves non-UI motions & expressions
    doc.getElementById('reset-btn').click();
    await w.saveEmotionMapping();
    assert.equal(postBodies.length, 2);
    assert.deepEqual(postBodies[1].motions, {
        TapBody: ['motions/tap.motion3.json'],
        idle: ['motions/idle_lower.motion3.json'],
        empty_group: [],
    });
    assert.deepEqual(postBodies[1].expressions, {
        '常驻': ['exp/blush.exp3.json', 'exp/glasses.exp3.json'],
        custom_group: ['exp/cg.exp3.json'],
    });

    // Pre-save GET failure (HTTP/business/network) aborts POST and re-enables save button
    for (const mode of ['http500', 'biz_fail', 'network']) {
        failPreSaveMappingMode = mode;
        await w.saveEmotionMapping();
        assert.equal(postBodies.length, 2, `must abort POST when pre-save GET fails (${mode})`);
        assert.equal(doc.getElementById('save-btn').disabled, false, 'save button must re-enable after aborted save');
        assert.equal(doc.querySelector('.status-message').classList.contains('status-error'), true);
    }
});

test('F0063 + F0507: model switch mid-save aborts cleanly without emitting stale POST or status', async () => {
    const preSaveGetA = deferred();
    const postCalls = [];

    const { w, doc } = createEmotionManagerDom(async (url, init = {}) => {
        const u = String(url);
        if (u === '/api/live2d/models') {
            return {
                ok: true,
                status: 200,
                json: async () => ([
                    { name: 'ModelA', source: 'user_mods' },
                    { name: 'ModelB', source: 'user_mods' },
                ]),
            };
        }
        if (u === '/api/live2d/model_files/ModelA') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    motion_files: ['motions/a.motion3.json'],
                    expression_files: [],
                }),
            };
        }
        if (u === '/api/live2d/model_files/ModelB') {
            return {
                ok: true,
                status: 200,
                json: async () => ({
                    success: true,
                    motion_files: ['motions/b.motion3.json'],
                    expression_files: [],
                }),
            };
        }
        if (u === '/api/live2d/emotion_mapping/ModelA') {
            if ((init.method || 'GET') === 'POST') {
                postCalls.push({ url: u, body: JSON.parse(init.body) });
                return { ok: true, status: 200, json: async () => ({ success: true }) };
            }
            if (!preSaveGetA.initialDone) {
                preSaveGetA.initialDone = true;
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ success: true, config: { motions: { happy: ['motions/a.motion3.json'] }, expressions: {} } }),
                };
            }
            return await preSaveGetA.promise;
        }
        if (u === '/api/live2d/emotion_mapping/ModelB') {
            if ((init.method || 'GET') === 'POST') {
                postCalls.push({ url: u, body: JSON.parse(init.body) });
            }
            return {
                ok: true,
                status: 200,
                json: async () => ({ success: true, config: { motions: { happy: ['motions/b.motion3.json'] }, expressions: {} } }),
            };
        }
        throw new Error(`Unexpected fetch URL: ${u}`);
    });

    await flushMicrotasks();
    const items = Array.from(doc.querySelectorAll('.singleselect-item'));
    items[0].click();
    await flushMicrotasks(30);

    const savePromise = w.saveEmotionMapping();
    await flushMicrotasks(5);

    // Switch to ModelB while pre-save GET for ModelA is in flight
    items[1].click();
    await flushMicrotasks(30);

    preSaveGetA.resolve({
        ok: true,
        status: 200,
        json: async () => ({ success: true, config: { motions: { happy: ['motions/a.motion3.json'] }, expressions: {} } }),
    });
    await savePromise;
    await flushMicrotasks(10);

    assert.equal(postCalls.length, 0, 'model switch during pre-save GET must abort stale POST');
    assert.equal(doc.getElementById('save-btn').disabled, false, 'ModelB save button must remain enabled');
});
