const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const read = name => fs.readFileSync(path.join(root, 'static/app/app-ui', name), 'utf8');
const surfaceSource = read('surface-floating-controls.js');
const types = ['live2d', 'vrm', 'mmd', 'pngtuber'];
const flush = () => new Promise(resolve => setImmediate(resolve));

test('Live2D starts a new snap while a cancelled paced frame is still pending', async () => {
    const source = fs.readFileSync(path.join(root, 'static/live2d/live2d-interaction.js'), 'utf8');
    const start = source.indexOf('Live2DManager.prototype._performSnapAnimation =');
    const end = source.indexOf('function getLive2DRendererPointer', start);
    const frames = [];
    const context = {
        Live2DManager: function () {},
        performance: { now: () => 0 },
        SNAP_CONFIG: { animationDuration: 100, easingType: 'linear' },
        EasingFunctions: { linear: value => value },
        console: { log() {}, debug() {} },
        scheduleLive2DSnapFrame: callback => frames.push(callback),
    };
    vm.runInNewContext(source.slice(start, end), context);
    const manager = new context.Live2DManager();
    manager._isModelReadyForInteraction = true;
    manager._savePositionAfterInteraction = async () => {};
    manager._checkSnapRequired = async () => ({ startX: 0, startY: 0, targetX: 200, targetY: 200 });
    const model = { x: 0, y: 0 };
    let current = true;
    const previous = manager._performSnapAnimation(model, { startX: 0, startY: 0, targetX: 100, targetY: 100 }, {
        isCurrentSettlement: () => current
    });
    current = false;
    const next = manager._checkAndPerformSnap(model);
    await flush();
    assert.equal(frames.length, 2);
    assert.equal(await previous, false);
    frames[0](100);
    assert.deepEqual(model, { x: 0, y: 0 });
    assert.equal(manager._isSnapping, true);
    frames[1](100);
    assert.equal(await next, true);
    assert.deepEqual(model, { x: 200, y: 200 });
    assert.equal(manager._isSnapping, false);
});

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function harness(type = 'live2d') {
    const elements = new Map();
    const events = [];
    const saves = [];
    const snaps = [];
    const model = { destroyed: false };
    const window = {
        appUi: {}, __appUiParts: { mod: {}, S: {} },
        innerWidth: 1706, innerHeight: 1066, screenX: 1, screenY: 1,
        lanlan_config: { model_type: type, lanlan_name: 'test' },
        addEventListener() {}, removeEventListener() {},
        dispatchEvent: event => { events.push(event); return true; },
        setTimeout, clearTimeout, t: key => key,
        showStatusToast() {},
    };
    const manager = {
        currentModel: model, config: {}, getCurrentModel: () => model,
        _savePositionAfterInteraction: async () => saves.push(type),
        saveCurrentConfig: async () => saves.push(type),
    };
    const snap = async options => { snaps.push(options); return false; };
    manager._checkAndPerformSnap = (_model, options) => snap(options);
    manager.interaction = { _snapModelIntoScreen: snap, _savePositionAfterInteraction: manager._savePositionAfterInteraction };
    window[`${type}Manager`] = manager;
    elements.set(`${type}-container`, {
        id: `${type}-container`, isConnected: true, offsetWidth: 64,
        style: {
            display: 'block', visibility: 'visible',
            setProperty(name, value) { this[name] = value; },
            removeProperty(name) { delete this[name]; }
        },
        classList: { contains: () => false, remove() {} },
        getBoundingClientRect: () => ({ left: 500, top: 300, width: 200, height: 300 }),
    });
    const document = {
        documentElement: {},
        getElementById: id => elements.get(id) || null,
        querySelector: () => null, querySelectorAll: () => [],
    };
    class CustomEvent {
        constructor(type, options = {}) { this.type = type; this.detail = options.detail; }
    }
    let frameTime = 0;
    const context = vm.createContext({
        window, document, CustomEvent, setTimeout, clearTimeout,
        console: { log() {}, warn() {}, error() {} },
        requestAnimationFrame(callback) { queueMicrotask(() => callback(frameTime += 300)); return frameTime; },
        getComputedStyle: element => element.style,
        lanlan_config: window.lanlan_config,
        isGoodbyeRuntimeActive: () => manager._goodbyeClicked === true,
    });
    for (const name of ['micButton', 'screenButton', 'textSendButton', 'textInputBox',
        'screenshotButton', 'resetSessionButton', 'muteButton', 'stopButton']) {
        context[name] = { disabled: false, classList: { remove() {} } };
    }
    for (const file of ['model-display.js', 'return-transitions.js', 'surface-floating-controls.js']) {
        vm.runInContext(read(file), context, { filename: file });
    }
    const I = window.__appUiParts;
    I.NEKO_MODEL_RETURN_ENTER_CLEANUP_MS = 1160;
    I.NEKO_MODEL_RETURN_ENTER_SETTLE_BUFFER_MS = 180;
    I.NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE = 0.38;
    I.publishCatLocalActive = () => {};
    I.restoreGoodbyeResourceSuspend = () => {};
    I.hideReturnBallContainer = () => {};
    I.ensureMultiWindowReturnBallDrag = () => {};
    I.getVisibleIdleReturnBallContainer = () => null;
    I.isNekoModelCatTransitionActive = () => false;
    // Exercise the production handler, including its finally and terminal events.
    const start = surfaceSource.indexOf('function restoreReturnBallAfterBlockedModelViewport(event)');
    const end = surfaceSource.indexOf('// 统一监听各模型类型的回来事件', start);
    vm.runInContext(`${surfaceSource.slice(start, end)}\nwindow.handleReturnClick = handleReturnClick;`,
        Object.assign(context, { I }));
    const dispatchReturn = () => window.handleReturnClick(new CustomEvent(`${type}-return-click`));
    // Use the real departure entry through cancellation; the remaining departure
    // UI is irrelevant to the stale-return race under test.
    const goodbyeStart = surfaceSource.indexOf("window.addEventListener('live2d-goodbye-click', (event) => {");
    const goodbyeEnd = surfaceSource.indexOf('// 第零步：', goodbyeStart);
    const goodbyeBody = surfaceSource.slice(goodbyeStart, goodbyeEnd)
        .replace("window.addEventListener('live2d-goodbye-click', (event) => {", 'window.beginGoodbye = (event = {}) => {');
    I.reserveNekoModelCatTransition = () => 1;
    vm.runInContext(`${goodbyeBody}\n};`, context);
    return { window, I, manager, model, elements, events, snaps, saves, context, dispatchReturn,
        depart() { window.beginGoodbye(); manager._goodbyeClicked = true; } };
}

for (const type of types) {
    test(`${type}: a departure cancels a return waiting for entrance before it can snap/save`, async () => {
        const h = harness(type);
        const entrance = deferred();
        h.window._nekoModelReturnEnterPromise = entrance.promise;
        let current = true;
        const settlement = h.I.settleReturnedModelBounds(true, { isCurrentSettlement: () => current });
        h.depart();
        current = false;
        entrance.resolve();
        await settlement;
        assert.equal(h.snaps.length, 0);
        assert.equal(h.saves.length, 0);
    });

    test(`${type}: return settlement does not use a 160x160 ball viewport`, async () => {
        const h = harness(type);
        h.window.innerWidth = h.window.innerHeight = 160;
        await h.I.settleReturnedModelBounds(true);
        assert.equal(h.snaps.length, 0);
        assert.equal(h.saves.length, 0);
    });

    test(`${type}: an old return cannot clear the new return's lock or publish completion`, async () => {
        const h = harness(type);
        const firstDisplay = deferred();
        const secondDisplay = deferred();
        let displays = 0;
        let settlements = 0;
        h.I.ensureModelViewportReadyBeforeShowCurrentModel = async () => ({ ready: true });
        h.I.showCurrentModel = async () => (++displays === 1 ? firstDisplay.promise : secondDisplay.promise);
        h.I.settleReturnedModelBounds = async () => { settlements++; };
        const firstReturn = h.dispatchReturn();
        await flush();
        h.depart();
        assert.equal(h.I.nekoCatReturnInProgress, false, 'new departure must release the stale lock immediately');
        const secondReturn = h.dispatchReturn();
        await flush();
        const newOperation = h.I.nekoCatReturnOperation;
        assert.ok(newOperation);
        firstDisplay.resolve(true);
        await firstReturn;
        assert.equal(h.I.nekoCatReturnOperation, newOperation);
        assert.equal(h.I.nekoCatReturnInProgress, true);
        assert.equal(settlements, 0);
        assert.equal(h.events.filter(event => event.type === 'neko:cat-return-complete').length, 0);
        assert.equal(h.events.filter(event => event.type === 'neko:cat-return-abort').length, 1);
        secondDisplay.resolve(true);
        await secondReturn;
        assert.equal(settlements, 1);
        assert.equal(h.I.nekoCatReturnInProgress, false);
        assert.equal(h.events.filter(event => event.type === 'neko:cat-return-complete').length, 1);
    });
}

test('a cancelled entrance RAF cannot overwrite the next departure styles', async () => {
    const h = harness();
    const container = h.elements.get('live2d-container');
    h.I.playModelReturnEnter(container, { left: 500, top: 300, width: 72, height: 72 });
    h.depart();
    container.style.opacity = '0.2';
    container.style.transform = 'translateX(9px)';
    await flush();
    assert.equal(container.style.opacity, '0.2');
    assert.equal(container.style.transform, 'translateX(9px)');
    assert.equal(h.window._nekoModelReturnEnterTimer, undefined);
});

test('a stale model-config response cannot reveal the model after a newer departure', async () => {
    const h = harness();
    const response = deferred();
    let current = true;
    let reveals = 0;
    h.context.fetch = () => response.promise;
    h.I.showLive2d = () => { reveals++; };
    const display = h.I.showCurrentModel({ isCurrent: () => current });
    await flush();
    h.depart();
    current = false;
    response.resolve({ ok: false });
    assert.equal(await display, false);
    assert.equal(reveals, 0);
});

test('a cancelled viewport restore cannot consume a newer restore target', async () => {
    const h = harness();
    const reveal = deferred();
    let current = true;
    h.window.innerWidth = h.window.innerHeight = 160;
    h.window.nekoPetDrag = { reveal: () => reveal.promise };
    h.I.setPendingNativeModelViewportRestoreBounds({ width: 1706, height: 1066 });
    const restore = h.I.ensureModelViewportReadyBeforeShowCurrentModel({ isCurrent: () => current });
    current = false;
    h.I.setPendingNativeModelViewportRestoreBounds({ width: 1920, height: 1080 });
    h.window.innerWidth = 1706;
    h.window.innerHeight = 1066;
    reveal.resolve(true);
    const result = await restore;
    assert.equal(result.cancelled, true);
    assert.equal(h.I.getPendingModelViewportRestoreBounds().width, 1920);
    assert.equal(h.I.getPendingModelViewportRestoreBounds().height, 1080);
});

for (const type of ['live2d', 'mmd']) {
    test(`${type}: cancelling during display lookup prevents a no-snap return from saving stale position`, async () => {
        const h = harness(type);
        const display = deferred();
        const persisted = [];
        let current = true;
        h.window.electronScreen = { getCurrentDisplay: () => display.promise };
        h.window.screen = { width: 1706, height: 1066 };
        h.context.console.debug = () => {};
        Object.assign(h.model, {
            x: 500, y: 300, scale: { x: 1, y: 1 }, url: 'fixture-model',
            mesh: { position: { x: 500, y: 300, z: 0 }, scale: { x: 1, y: 1, z: 1 }, rotation: { x: 0, y: 0, z: 0 } }
        });
        const persist = (...args) => { persisted.push(args); return Promise.resolve(true); };
        const source = fs.readFileSync(path.join(root, `static/${type}/${type}-interaction.js`), 'utf8');
        if (type === 'live2d') {
            const start = source.indexOf('Live2DManager.prototype._savePositionAfterInteraction = async function');
            const end = source.indexOf('// 防抖动保存位置的辅助函数', start);
            h.context.Live2DManager = { prototype: {} };
            vm.runInContext(source.slice(start, end), h.context);
            h.manager._savePositionAfterInteraction = h.context.Live2DManager.prototype._savePositionAfterInteraction;
            h.manager._lastLoadedModelPath = 'fixture-model';
            h.manager.saveUserPreferences = persist;
        } else {
            const start = source.indexOf('    async _savePositionAfterInteraction(');
            const end = source.indexOf('    _debouncedSavePosition()', start);
            vm.runInContext(`window.saveMmdPosition = ({ ${source.slice(start, end)} })._savePositionAfterInteraction;`, h.context);
            h.manager.interaction._savePositionAfterInteraction = h.window.saveMmdPosition;
            h.manager.interaction.manager = h.manager;
            h.manager.core = { saveUserPreferences: persist };
        }
        const settlement = h.I.settleReturnedModelBounds(true, { isCurrentSettlement: () => current });
        await flush();
        h.depart();
        current = false;
        h.model.x = h.model.mesh.position.x = 900;
        display.resolve({ screenX: 1, screenY: 1 });
        await settlement;
        assert.equal(persisted.length, 0);
    });
}
