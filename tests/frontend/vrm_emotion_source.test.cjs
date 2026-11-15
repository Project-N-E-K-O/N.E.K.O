const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/js/model_manager/vrm-emotion-source.js'), 'utf8');
async function flush() { for (let i = 0; i < 15; i++) await Promise.resolve(); }
function setup(options = {}) {
    const listeners = new Map(), previews = [], loads = [];
    const window = { location: { href: 'http://localhost:48911/model_manager', origin: 'http://localhost:48911' },
        addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
    let now = 0;
    vm.runInNewContext(source, { window, URL, Date: { now: () => now } });
    let selectedUrl = options.selectedUrl || '/static/vrm/A.vrm';
    let currentTask = null;
    const manager = { currentModel: { url: selectedUrl, vrm: {} }, _activeLoadToken: 1,
        _isModelReadyForInteraction: true, expression: {
            getExpressionList: () => options.expressions || ['happy'], setBaseExpression: value => previews.push(value)
        } };
    const loadModel = async info => {
            const task = { startedAt: now, settled: false };
            currentTask = task;
            loads.push(info.url); selectedUrl = info.url;
            const token = ++manager._activeLoadToken; manager._isModelReadyForInteraction = false;
            try {
                const result = options.load ? await options.load(info, manager) : undefined;
                if (token !== manager._activeLoadToken) return;
                if (result?.error) return result;
                manager.currentModel = { url: info.url, vrm: {} }; manager._isModelReadyForInteraction = true;
                return result;
            } finally {
                task.settled = true;
                if (currentTask === task) currentTask = null;
            }
        };
    const bridge = window.createVrmEmotionSource({ getManager: () => manager,
        getSelectedUrl: () => selectedUrl,
        getLoadingTask: () => currentTask, loadModel });
    return { bridge, manager, previews, loads, listeners, advance: ms => { now += ms; },
        changeSelected: value => { selectedUrl = value; }, startExternalLoad: loadModel };
}
test('ready capability is tied to exact URL and load token, with real preview', () => {
    const s = setup(); const snapshot = s.bridge.request({ url: '/static/vrm/A.vrm' });
    assert.equal(snapshot.state, 'ready'); assert.equal(s.bridge.isCurrent(snapshot), true);
    assert.equal(s.bridge.preview(snapshot, 'happy'), true); assert.deepEqual(s.previews, ['happy']);
    s.manager._activeLoadToken++; assert.equal(s.bridge.isCurrent(snapshot), false);
    assert.equal(s.bridge.preview(snapshot, 'happy'), false);
});
test('persisted pagehide preserves source admission and actual exit still removes its lifecycle listener', async () => {
    const s = setup(); const snapshot = s.bridge.request({ url: '/static/vrm/A.vrm' });
    const hide = s.listeners.get('pagehide');
    for (let i = 0; i < 100; i++) hide({ persisted: true });
    assert.equal(s.bridge.isCurrent(snapshot), true);
    assert.equal(s.listeners.size, 1);
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/B.vrm' }).state, 'ready');
    hide({ persisted: false });
    assert.equal(s.listeners.size, 0);
    assert.equal(s.bridge.request({ url: '/static/vrm/B.vrm' }).errorCode, 'sourceUnavailable');
});
test('same filename in different locations cannot supply the other model capability', async () => {
    const s = setup(); const r = s.bridge.request({ url: '/user_vrm/A.vrm' });
    assert.equal(r.state, 'loading'); await flush();
    assert.deepEqual(s.loads, ['/user_vrm/A.vrm']);
    assert.equal(s.bridge.request({ url: '/user_vrm/A.vrm' }).modelUrl, 'http://localhost:48911/user_vrm/A.vrm');
});
test('one pending load stays bounded during repeated and switched requests', async () => {
    let resolve;
    const pending = new Promise(r => { resolve = r; });
    const s = setup({ load: () => pending });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    for (let i = 0; i < 100; i++) assert.equal(s.bridge.request({ url: `/static/vrm/${i}.vrm` }).state, 'loading');
    assert.equal(s.loads.length, 1);
    resolve(); await flush(); assert.equal(s.bridge.request({ url: '/static/vrm/C.vrm' }).state, 'loading'); await flush();
    assert.equal(s.loads.length, 2); assert.equal(s.bridge.request({ url: '/static/vrm/C.vrm' }).state, 'ready');
});
test('a model load error persists until explicit retry and then clears', async () => {
    let fails = true;
    const s = setup({ load: () => { if (fails) throw new Error('injected parse error'); } });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    const failed = s.bridge.request({ url: '/static/vrm/B.vrm' });
    assert.equal(failed.state, 'failed'); assert.equal(failed.error, 'injected parse error');
    assert.equal(s.loads.length, 1);
    fails = false; assert.equal(s.bridge.request({ url: '/static/vrm/B.vrm' }, true).state, 'loading'); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/B.vrm' }).state, 'ready');
});
test('ready-but-empty is returned distinctly from no loaded model', () => {
    const s = setup({ expressions: [] }); const r = s.bridge.request({ url: '/static/vrm/A.vrm' });
    assert.equal(r.state, 'ready'); assert.equal(r.expressions.length, 0);
});
test('foreign model URL never starts a load', () => {
    const s = setup(); assert.equal(s.bridge.request({ url: 'https://example.com/A.vrm' }).state, 'failed');
    assert.equal(s.loads.length, 0);
});
test('unsupported names cannot be previewed, neutral remains a valid reset', () => {
    const s = setup(); const r = s.bridge.request({ url: '/static/vrm/A.vrm' });
    assert.equal(s.bridge.preview(r, 'invented'), false); assert.equal(s.bridge.preview(r, 'neutral'), true);
});
test('selection veto returns failure even if the renderer still has the requested model', async () => {
    const s = setup({ load: () => ({ error: 'voice mode prevents reload' }) });
    s.bridge.request({ url: '/static/vrm/A.vrm' }, true); await flush();
    const result = s.bridge.request({ url: '/static/vrm/A.vrm' });
    assert.equal(result.state, 'failed'); assert.equal(result.error, 'voice mode prevents reload');
    assert.equal(s.loads.length, 1);
});
test('disposal removes source lifecycle listener and ignores late load completion', async () => {
    let resolve; const pending = new Promise(r => { resolve = r; });
    const s = setup({ load: () => pending }); s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    s.bridge.dispose(); assert.equal(s.listeners.size, 0); resolve(); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/B.vrm' }).state, 'failed');
});

test('explicit retry replaces a stuck load and late completion cannot revoke the new capability', async () => {
    let resolve;
    const stuck = new Promise(r => { resolve = r; });
    const s = setup({ load: info => info.url.endsWith('B.vrm') ? stuck : undefined });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    s.bridge.request({ url: '/static/vrm/C.vrm' }, true); await flush();
    const capability = s.bridge.request({ url: '/static/vrm/C.vrm' });
    assert.equal(capability.state, 'ready'); assert.equal(s.loads.length, 2);
    resolve(); await flush(); assert.equal(s.bridge.isCurrent(capability), true);
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});
test('expired load allows a new selection without forced retry', async () => {
    const s = setup({ load: info => info.url.endsWith('B.vrm') ? new Promise(() => {}) : undefined });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush(); s.advance(120001);
    s.bridge.request({ url: '/static/vrm/C.vrm' }); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/C.vrm' }).state, 'ready');
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});
test('replacement capacity is bounded across 100 forced retries and released when old load settles', async () => {
    const jobs = [];
    const s = setup({ load: () => new Promise(resolve => jobs.push(resolve)) });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    s.bridge.request({ url: '/static/vrm/C.vrm' }, true); await flush();
    for (let i = 0; i < 100; i++) s.bridge.request({ url: '/static/vrm/D.vrm' }, true);
    await flush(); assert.equal(s.loads.length, 2);
    jobs[0](); await flush();
    s.bridge.request({ url: '/static/vrm/D.vrm' }, true); await flush();
    assert.equal(s.loads.length, 3);
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
    jobs[1](); jobs[2](); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).state, 'failed');
});

test('forced retry adopts an external load under the capacity limit and releases it on settlement', async () => {
    const jobs = [];
    const s = setup({ load: () => new Promise(resolve => jobs.push(resolve)) });
    const external = s.startExternalLoad({ url: '/static/vrm/B.vrm' }); await flush();
    s.bridge.request({ url: '/static/vrm/C.vrm' }, true); await flush();
    assert.equal(s.loads.length, 2);
    for (let i = 0; i < 100; i++) s.bridge.request({ url: '/static/vrm/D.vrm' }, true);
    await flush(); assert.equal(s.loads.length, 2);
    jobs[0](); await external; await flush();
    s.bridge.request({ url: '/static/vrm/D.vrm' }, true); await flush(); assert.equal(s.loads.length, 3);
    jobs[1](); jobs[2](); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).state, 'ready');
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});
test('external load expiry permits a healthy new selection without forced retry', async () => {
    const s = setup({ load: info => info.url.endsWith('B.vrm') ? new Promise(() => {}) : undefined });
    void s.startExternalLoad({ url: '/static/vrm/B.vrm' }); await flush(); s.advance(120001);
    s.bridge.request({ url: '/static/vrm/C.vrm' }); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/C.vrm' }).state, 'ready');
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});

for (const force of [true, false]) for (const settlesFirst of [0, 1]) test(`distinct overlapping opener and source loads block replacement (force=${force}, settlesFirst=${settlesFirst})`, async () => {
    const jobs = [];
    const s = setup({ load: () => new Promise(resolve => jobs.push(resolve)) });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    const external = s.startExternalLoad({ url: '/static/vrm/C.vrm' }); await flush();
    s.advance(120001);
    for (let i = 0; i < 100; i++) {
        const result = s.bridge.request({ url: '/static/vrm/D.vrm' }, force);
        assert.equal(result.state, 'failed'); assert.equal(result.errorCode, 'recoveryBlocked');
    }
    await flush(); assert.equal(s.loads.length, 2);
    jobs[settlesFirst](); await flush();
    s.bridge.request({ url: '/static/vrm/D.vrm' }, true); await flush();
    assert.equal(s.loads.length, 3);
    jobs[1 - settlesFirst](); await external; jobs[2](); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).state, 'ready');
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});
test('a distinct opener load arriving before the replacement microtask cannot admit a third call', async () => {
    const jobs = [];
    const s = setup({ load: () => new Promise(resolve => jobs.push(resolve)) });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    s.bridge.request({ url: '/static/vrm/C.vrm' }, true);
    const external = s.startExternalLoad({ url: '/static/vrm/D.vrm' });
    await flush(); assert.equal(s.loads.length, 2);
    assert.equal(s.bridge.request({ url: '/static/vrm/C.vrm' }, true).errorCode, 'recoveryBlocked');
    jobs[0](); jobs[1](); await external; await flush();
    s.bridge.request({ url: '/static/vrm/C.vrm' }, true); await flush();
    assert.equal(s.loads.length, 3); jobs[2](); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/C.vrm' }).state, 'ready');
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});
test('late failure of admitted work cannot erase a capacity rejection or enqueue its URL', async () => {
    const jobs = [];
    const s = setup({ load: () => new Promise((resolve, reject) => jobs.push({ resolve, reject })) });
    s.bridge.request({ url: '/static/vrm/B.vrm' }); await flush();
    const external = s.startExternalLoad({ url: '/static/vrm/C.vrm' }); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).errorCode, 'recoveryBlocked');
    jobs[0].reject(new Error('late source parse failure')); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).errorCode, 'recoveryBlocked');
    jobs[1].resolve(); await external; await flush();
    for (let i = 0; i < 100; i++) assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).state, 'failed');
    await flush(); assert.equal(s.loads.length, 2);
    s.bridge.request({ url: '/static/vrm/D.vrm' }, true); await flush();
    assert.equal(s.loads.length, 3); jobs[2].resolve(); await flush();
    assert.equal(s.bridge.request({ url: '/static/vrm/D.vrm' }).state, 'ready');
    s.bridge.dispose(); assert.equal(s.listeners.size, 0);
});
