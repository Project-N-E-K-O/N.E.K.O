const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-ui/return-transitions.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(enabled = true) {
    let prepare;
    const ready = new Promise(resolve => { prepare = resolve; });
    const targets = [];
    const window = {
        appUi: {}, __appUiParts: { nekoModelCatTransitionToken: 0 },
        innerWidth: 1920, innerHeight: 1080, addEventListener() {},
        nekoPetPowerSave: { isEnabled: () => enabled, prepareFormChange(target) { targets.push(target); return ready; } },
    };
    const context = {
        window, document: { querySelectorAll: () => [], createElement() { assert.fail('power-save form changes must not allocate a GIF overlay'); } },
        console, setTimeout, clearTimeout, requestAnimationFrame() { assert.fail('no entrance animation RAF'); },
        Image: class { constructor() { assert.fail('power-save mode must not decode the transition GIF'); } },
    };
    vm.runInNewContext(source, context);
    const I = window.__appUiParts;
    I.getPendingModelViewportRestoreBounds = () => null;
    I.isNativeReturnBallViewportSize = () => false;
    function element(type) {
        const attributes = new Map();
        const classes = new Set();
        return { id: type + '-container', isConnected: true,
            classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
            setAttribute: (name, value) => attributes.set(name, value),
            getAttribute: name => attributes.get(name), removeAttribute: name => attributes.delete(name),
            style: { removeProperty(name) { delete this[name]; } },
        };
    }
    return { I, window, element, targets, prepare };
}

test('animation policy uses the enabled setting even after the physical crop has been released', () => {
    const h = harness();
    assert.equal(h.I.shouldAnimateModelCatTransition(), false);
    assert.equal(h.window.__nekoNiriPetPhysicalCrop, undefined);
    h.window.nekoPetPowerSave.isEnabled = () => false;
    assert.equal(h.I.shouldAnimateModelCatTransition(), true);
    delete h.window.nekoPetPowerSave;
    h.window.__nekoNiriPetPhysicalCrop = { isActive: () => true };
    assert.equal(h.I.shouldAnimateModelCatTransition(), true, 'Niri and browser behavior remains animated');
});

for (const type of ['live2d', 'vrm', 'mmd', 'pngtuber']) {
    test(`${type}: instant departure hides the old model, then reveals the cat after its window is ready`, async () => {
        const h = harness();
        const model = h.element(type);
        h.I.playModelGoodbyeExit(model, null);
        assert.equal(model.style.opacity, '0');
        assert.equal(model.style.visibility, 'hidden');
        let reveals = 0;
        const departure = h.I.playNekoModelCatTransition({ direction: 'model-to-cat',
            onBeforeOverlayCleanup: () => { reveals++; } });
        await flush();
        assert.deepEqual(h.targets, ['cat']);
        assert.equal(reveals, 0);
        assert.equal(h.I.isNekoModelCatTransitionActive(), true);
        h.prepare(true);
        assert.equal((await departure).animationSkipped, true);
        assert.equal(reveals, 1);
        assert.equal(h.I.isNekoModelCatTransitionActive(), false);
    });

    test(`${type}: instant return waits for crop commit without a fade/scale timer`, async () => {
        const h = harness();
        const model = h.element(type);
        h.I.playModelReturnEnter(model, { left: 300, top: 200, width: 100, height: 200 });
        const entrance = h.window._nekoModelReturnEnterPromise;
        assert.ok(entrance);
        assert.equal(model.getAttribute('data-neko-model-form-pending'), 'true');
        assert.equal(model.style.opacity, '0');
        assert.deepEqual(h.targets, ['model']);
        h.prepare(true);
        await entrance;
        assert.equal(model.getAttribute('data-neko-model-form-pending'), undefined);
        assert.equal(model.style.opacity, undefined);
        assert.equal(model.style.transform, undefined);
        assert.equal(model.style.transition, undefined, 'hover/lock effects regain their CSS after reveal');
        assert.equal(h.window._nekoModelReturnEnterTimer, undefined);
    });

    test(`${type}: a newer departure cancels a pending instant return`, async () => {
        const h = harness();
        const model = h.element(type);
        h.I.playModelReturnEnter(model, {});
        h.I.cancelModelReturnEnter('new-goodbye');
        h.I.playModelGoodbyeExit(model, null);
        h.prepare(true);
        await flush();
        assert.equal(model.style.visibility, 'hidden');
        assert.equal(model.style.opacity, '0');
        assert.equal(h.window._nekoModelReturnEnterPromise, null);
    });
}
