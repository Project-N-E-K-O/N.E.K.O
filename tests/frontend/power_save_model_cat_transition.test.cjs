const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-ui/return-transitions.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(enabled = true) {
    const elements = new Map();
    let prepare;
    const ready = new Promise(resolve => { prepare = resolve; });
    const targets = [];
    const window = {
        appUi: {}, __appUiParts: { nekoModelCatTransitionToken: 0 },
        innerWidth: 1920, innerHeight: 1080, addEventListener() {},
        nekoPetPowerSave: { isEnabled: () => enabled, prepareFormChange(target) { targets.push(target); return ready; } },
    };
    const context = {
        window, document: { getElementById: id => elements.get(id) || null,
            querySelectorAll: () => [], createElement() { assert.fail('power-save form changes must not allocate a GIF overlay'); } },
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
        const el = { id: type + '-container', isConnected: true,
            classList: { add: name => classes.add(name), remove: name => classes.delete(name), contains: name => classes.has(name) },
            setAttribute: (name, value) => attributes.set(name, value),
            getAttribute: name => attributes.get(name), removeAttribute: name => attributes.delete(name),
            style: { removeProperty(name) { delete this[name]; } },
        };
        elements.set(el.id, el);
        return el;
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
    test(`${type}: an early display return still hides and prepares the model exactly once`, async () => {
        const h = harness();
        const model = h.element(type);
        const sequence = h.I.modelReturnEnterSequence;
        const ready = h.I.ensureModelReturnFormReady(sequence);
        assert.deepEqual(h.targets, ['model']);
        assert.equal(model.getAttribute('data-neko-model-form-pending'), 'true');
        assert.equal(model.style.opacity, '0');
        h.prepare(true);
        await ready;
        assert.equal(model.getAttribute('data-neko-model-form-pending'), undefined);
        await h.I.ensureModelReturnFormReady(sequence);
        assert.deepEqual(h.targets, ['model'], 'a completed entrance must not prepare a second time');
    });

    test(`${type}: a missing return rect still prepares an instant form`, async () => {
        const h = harness();
        const model = h.element(type);
        assert.equal(h.I.playModelReturnEnter(model, null), true);
        const entrance = h.window._nekoModelReturnEnterPromise;
        assert.deepEqual(h.targets, ['model']);
        assert.equal(model.getAttribute('data-neko-model-form-pending'), 'true');
        h.prepare(true);
        await entrance;
        assert.equal(model.getAttribute('data-neko-model-form-pending'), undefined);
        const normal = harness(false);
        assert.equal(normal.I.playModelReturnEnter(normal.element(type), null), false);
        assert.deepEqual(normal.targets, []);
    });

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
        h.I.playModelReturnEnter(model, null);
        h.I.cancelModelReturnEnter('new-goodbye');
        h.I.playModelGoodbyeExit(model, null);
        h.prepare(true);
        await flush();
        assert.equal(model.style.visibility, 'hidden');
        assert.equal(model.style.opacity, '0');
        assert.equal(h.window._nekoModelReturnEnterPromise, null);
    });

    test(`${type}: replacing a pending entrance releases the old model styles`, async () => {
        const h = harness();
        const finishes = [];
        h.window.nekoPetPowerSave.prepareFormChange = () => new Promise(resolve => finishes.push(resolve));
        const oldModel = h.element(type);
        const nextModel = h.element('replacement');
        h.I.playModelReturnEnter(oldModel, null);
        const oldEntrance = h.window._nekoModelReturnEnterPromise;
        h.I.playModelReturnEnter(nextModel, null);
        const nextEntrance = h.window._nekoModelReturnEnterPromise;
        await oldEntrance;
        assert.equal(oldModel.getAttribute('data-neko-model-form-pending'), undefined);
        assert.equal(oldModel.style.opacity, undefined);
        assert.equal(oldModel.style.transition, undefined);
        assert.equal(oldModel.style.transform, undefined);
        assert.equal(nextModel.getAttribute('data-neko-model-form-pending'), 'true');
        assert.equal(nextModel.style.opacity, '0');
        let nextSettled = false;
        nextEntrance.then(() => { nextSettled = true; });
        finishes[0](true);
        await flush();
        assert.equal(nextModel.getAttribute('data-neko-model-form-pending'), 'true');
        assert.equal(nextModel.style.opacity, '0');
        assert.equal(h.window._nekoModelReturnEnterPromise, nextEntrance);
        assert.equal(nextSettled, false, 'the old bridge reply must not settle the new entrance');
        finishes[1](true);
        await nextEntrance;
        assert.equal(nextModel.getAttribute('data-neko-model-form-pending'), undefined);
        assert.equal(nextModel.style.opacity, undefined);
        assert.equal(nextModel.style.transition, undefined);
        assert.equal(nextModel.style.transform, undefined);
    });
}

test('a stalled form bridge cannot leave the model return hidden indefinitely', { timeout: 1000 }, async () => {
    const h = harness();
    h.I.NEKO_MODEL_FORM_PREPARE_TIMEOUT_MS = 5;
    const model = h.element('live2d');
    h.I.playModelReturnEnter(model, null);
    await h.window._nekoModelReturnEnterPromise;
    assert.equal(model.getAttribute('data-neko-model-form-pending'), undefined);
    assert.equal(h.window._nekoModelReturnEnterPromise, null);
    assert.equal(model.style.opacity, undefined);
    h.prepare(true);
    await flush();
    assert.equal(model.style.opacity, undefined, 'a late reply does not replay the reveal');
});

test('a stalled form bridge cannot leave the cat departure token held', { timeout: 1000 }, async () => {
    const h = harness();
    h.I.NEKO_MODEL_FORM_PREPARE_TIMEOUT_MS = 5;
    let reveals = 0;
    await h.I.playNekoModelCatTransition({ direction: 'model-to-cat', onBeforeOverlayCleanup: () => reveals++ });
    assert.equal(reveals, 1);
    assert.equal(h.I.isNekoModelCatTransitionActive(), false);
    h.prepare(true);
});

test('an instant cleanup callback failure releases the transition token', async () => {
    const h = harness();
    h.prepare(true);
    const result = await h.I.playNekoModelCatTransition({ direction: 'model-to-cat',
        onBeforeOverlayCleanup() { throw new Error('cleanup unavailable'); } });
    assert.equal(result.completed, true);
    assert.equal(h.I.isNekoModelCatTransitionActive(), false);
});

test('instant cat-to-model cleanup keeps the cat hidden until hide or recovery', async () => {
    const h = harness();
    const cat = h.element('cat');
    await h.I.playNekoModelCatTransition({ direction: 'cat-to-model', container: cat });
    assert.equal(cat.style.visibility, 'hidden');
    assert.equal(cat.style.pointerEvents, 'none');
    assert.equal(cat.style.opacity, undefined);
    assert.equal(cat.style.transition, undefined);
    assert.equal(h.I.isNekoModelCatTransitionActive(), false);
    h.I.scheduleIdleReturnBallDesktopBridge = () => {};
    h.I.revealReturnBallContainer(cat, 'aborted-return');
    assert.equal(cat.style.visibility, 'visible');
    assert.equal(cat.style.pointerEvents, 'auto');
    assert.equal(cat.style.opacity, '1');
});

test('return preparation ignores inactive model containers hidden by stylesheets', async () => {
    const h = harness();
    const inactive = h.element('pngtuber');
    const model = h.element('live2d');
    h.window.getComputedStyle = el => ({ display: el === inactive ? 'none' : 'block', visibility: 'visible' });
    const ready = h.I.ensureModelReturnFormReady(h.I.modelReturnEnterSequence);
    assert.equal(inactive.getAttribute('data-neko-model-form-pending'), undefined);
    assert.equal(model.getAttribute('data-neko-model-form-pending'), 'true');
    h.prepare(true);
    await ready;
});

for (const throws of [true, false]) {
    test(`the canonical return prepares its model after ${throws ? 'display failure' : 'an early display return'}`, async () => {
        const h = harness();
        const model = h.element('live2d');
        let fallbackOptions;
        h.I.showCurrentModel = async () => {
            if (throws) throw new Error('character config unavailable');
        };
        h.I.showLive2d = options => { fallbackOptions = options; };
        const surface = fs.readFileSync(path.resolve(__dirname, '../../static/app/app-ui/surface-floating-controls.js'), 'utf8');
        const from = surface.indexOf('// 使用 showCurrentModel() 做最终裁决');
        const to = surface.indexOf('await I.settleReturnedModelBounds(', from);
        assert.ok(from >= 0 && to > from);
        const display = vm.runInNewContext('(async () => {' + surface.slice(from, to) + '})', {
            I: h.I, window: h.window, isCurrentReturn: () => true, console: { error() {} },
        });
        const ready = display();
        await flush();
        if (throws) assert.equal(fallbackOptions.prepareModelForm, true);
        assert.deepEqual(h.targets, ['model']);
        assert.equal(model.style.opacity, '0');
        h.prepare(true);
        await ready;
        assert.equal(model.getAttribute('data-neko-model-form-pending'), undefined);
    });
}
