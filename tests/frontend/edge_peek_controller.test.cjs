const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
    const listeners = new Map(), timers = new Map();
    let nextTimer = 0, observerCallback, observing = false;
    const listen = (type, callback) => {
        const callbacks = listeners.get(type) || [];
        callbacks.push(callback); listeners.set(type, callbacks);
    };
    const classes = new Set(), artClasses = new Set(), attributes = new Map();
    const classList = set => ({
        add: name => set.add(name), remove: name => set.delete(name),
        contains: name => set.has(name),
        toggle: (name, enabled) => enabled ? set.add(name) : set.delete(name),
    });
    const art = { classList: classList(artClasses), getBoundingClientRect: () => ({ left: 0, right: 20, top: 0, bottom: 20 }) };
    const button = { isConnected: true, classList: classList(new Set()) };
    const container = {
        isConnected: true, style: { cursor: 'grab' }, classList: classList(classes),
        querySelector: () => art, contains: node => node === button,
        toggleAttribute: (key, value) => value ? attributes.set(key, '') : attributes.delete(key),
        getAttribute: key => attributes.get(key) ?? null,
    };
    const window = {
        edgePeekLockEnabled: true, addEventListener: listen,
        document: { documentElement: {}, addEventListener: listen },
        MutationObserver: class {
            constructor(callback) { observerCallback = callback; }
            observe() { observing = true; }
            disconnect() { observing = false; }
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/avatar/avatar-ui-buttons/edge-peek-controller.js'), 'utf8'), {
        window, setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
        clearTimeout: id => timers.delete(id),
    });
    return {
        window, button, container, artClasses, timers,
        controller: window.NekoEdgePeekController,
        emit(type, event = {}) { for (const callback of listeners.get(type) || []) callback(event); },
        fireTimers() { const callbacks = [...timers.values()]; timers.clear(); callbacks.forEach(callback => callback()); },
        remove() { button.isConnected = container.isConnected = false; observerCallback(); },
        get observing() { return observing; },
    };
}

test('ordinary and inactive model locks do not block an unanchored return ball', () => {
    const h = harness();
    const model = {};
    h.window.live2dManager = { isLocked: true, currentModel: model };
    h.window.vrmManager = { isLocked: true, interaction: { isLocked: true } };
    assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), false);
    const state = h.window.live2dManager._live2DPeekState = { active: true, phase: 'peeking', model };
    assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), true);
    for (const phase of ['revealing', 'hidden', 'idle']) {
        state.phase = phase;
        assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), false);
    }
    state.phase = 'peeking';
    h.window.live2dManager.currentModel = {};
    assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), false);
    h.window.live2dManager.currentModel = model;
    h.window.edgePeekLockEnabled = false;
    assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), false);
});

for (const event of ['blur', 'mouseleave', 'visibilitychange']) {
    for (const faded of [false, true]) {
        test(`${event} clears ${faded ? 'active' : 'pending'} hover fade`, () => {
            const h = harness();
            h.controller.begin({ button: h.button, container: h.container, phase: 'peeking' });
            h.emit('mousemove', { clientX: 10, clientY: 10 });
            assert.equal(h.timers.size, 1);
            if (faded) { h.fireTimers(); assert.equal(h.artClasses.size, 1); }
            h.emit(event); h.fireTimers();
            assert.equal(h.artClasses.size, 0);
            assert.equal(h.timers.size, 0);
            assert.equal(h.controller.isLocked(h.button), true, 'leaving clears fade, not the lock');
        });
    }
}

test('removing a locked return ball releases timers, lock state and observer', () => {
    const h = harness();
    h.controller.begin({ button: h.button, container: h.container, phase: 'peeking' });
    h.emit('mousemove', { clientX: 10, clientY: 10 });
    assert.equal(h.controller.isAnyLocked(), true);
    assert.equal(h.observing, true);
    h.remove();
    assert.equal(h.controller.isActive(), false);
    assert.equal(h.controller.isAnyLocked(), false);
    assert.equal(h.timers.size, 0);
    assert.equal(h.observing, false);
    assert.equal(h.container.style.cursor, 'grab');
});

test('a same-turn detached ball cannot block input before the observer runs', () => {
    const h = harness();
    h.controller.begin({ button: h.button, container: h.container, phase: 'peeking' });
    h.container.isConnected = false;
    assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), false);
    assert.equal(h.controller.isAnyLocked(), false);
    assert.equal(h.observing, false);
});

for (const [type, pointerType] of [['touchstart', undefined], ['pointerdown', 'touch'], ['mousedown', undefined]]) {
    test(`locked ${type}/${pointerType || 'mouse'} blocks dragging without cancelling touch activation`, () => {
        const h = harness();
        h.controller.begin({ button: h.button, container: h.container, phase: 'peeking' });
        let prevented = false, stopped = false;
        h.emit(type, {
            type, pointerType, target: { closest: () => h.button },
            preventDefault() { prevented = true; },
            stopImmediatePropagation() { stopped = true; },
        });
        assert.equal(stopped, true);
        assert.equal(prevented, type === 'mousedown');
    });
}

test('native edge placements register the controller and hide clears both native and drag-edge locks', () => {
    const h = harness();
    const art = h.container.querySelector();
    art.style = { removeProperty() {} };
    h.button.querySelector = () => art;
    h.container.querySelector = selector => selector === '.neko-idle-return-btn' ? h.button : art;
    h.container.style.removeProperty = function (name) { delete this[name]; };
    h.container.removeAttribute = () => {};
    h.container.setAttribute = () => {};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/app/app-ui/return-transitions.js'), 'utf8'), { window: h.window });
    const I = h.window.__appUiParts;
    I.scheduleIdleReturnBallDesktopBridge = () => {};
    assert.equal(I.applyNekoIdleCat1EdgePeek(h.container, { edge: 'left', left: -20, top: 30 }), true);
    assert.equal(h.controller.isLocked(h.button), true);
    assert.equal(h.controller.getActiveEdge(h.button), 'left');
    h.emit('mousemove', { clientX: 10, clientY: 10 });
    assert.equal(h.timers.size, 1);
    I.hideReturnBallContainer(h.container);
    assert.equal(h.container.isConnected, true);
    assert.equal(h.container.style.display, 'none');
    assert.equal(h.controller.isAnyLocked(), false);
    assert.equal(h.timers.size, 0);
    assert.equal(h.button.classList.contains('is-cat1-edge-peek-left'), false);
    assert.equal(h.observing, false);
    h.container.style.display = 'block';
    h.controller.begin({ button: h.button, container: h.container, mode: 'drag-edge', phase: 'peeking' });
    I.hideReturnBallContainer(h.container);
    assert.equal(h.controller.isAnyLocked(), false, 'the shared hide path also clears renderer drag-edge entries');
});

for (const reason of ['disabled', 'switch-container', 'presentation-takeover']) {
test(`native drag cleanup releases only its edge-peek state: ${reason}`, () => {
    const h = harness();
    const art = h.container.querySelector();
    art.style = { removeProperty() {} };
    h.button.querySelector = () => art;
    h.container.querySelector = selector => selector === '.neko-idle-return-btn' ? h.button : art;
    h.container.style.removeProperty = function () {};
    h.container.removeEventListener = () => {};
    h.container.removeAttribute = () => {};
    h.container.setAttribute = () => {};
    h.container.addEventListener = () => {};
    h.window.removeEventListener = () => {};
    h.window.__NEKO_MULTI_WINDOW__ = true;
    h.window.nekoPetDrag = {};
    const document = { body: { dataset: {} }, documentElement: {}, addEventListener() {}, removeEventListener() {} };
    const context = vm.createContext({ window: h.window, document });
    for (const file of ['return-transitions.js', 'return-window-drag.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '../../static/app/app-ui', file), 'utf8'), context);
    }
    const I = h.window.__appUiParts;
    I.ensureMultiWindowReturnBallDrag(h.container);
    const oldState = I.multiWindowReturnBallDragState;
    I.applyNekoIdleCat1EdgePeek(h.container, { edge: 'left', left: -20, top: 30 });
    const takenOver = reason === 'presentation-takeover';
    if (takenOver) {
        h.controller.begin({ button: h.button, container: h.container, mode: 'desktop-window', phase: 'peeking' });
    }
    h.emit('mousemove', { clientX: 10, clientY: 10 });
    assert.equal(h.timers.size, 1);
    assert.equal(h.controller.isAnyLocked(), true);
    const nextContainer = { isConnected: true, addEventListener() {} };
    if (reason !== 'switch-container') h.window.__NEKO_DISABLE_NATIVE_RETURN_BALL_DRAG__ = true;
    I.ensureMultiWindowReturnBallDrag(reason === 'switch-container' ? nextContainer : h.container);
    assert.equal(h.container.isConnected, true, 'the old DOM remains connected');
    assert.notEqual(I.multiWindowReturnBallDragState, oldState);
    assert.equal(oldState.dragSessionToken, 1);
    assert.equal(h.controller.isAnyLocked(), takenOver);
    assert.equal(h.controller.shouldBlockReturnBallDrag(h.button, h.container), takenOver);
    assert.equal(h.timers.size, takenOver ? 1 : 0);
    assert.equal(h.button.classList.contains('is-cat1-edge-peek-left'), false);
    assert.equal(h.button.querySelector('.neko-idle-return-art').style.removeProperty !== undefined, true);
    assert.equal(h.observing, takenOver);
    assert.equal(h.container.style.cursor, takenOver ? 'default' : 'grab');
    if (reason === 'switch-container') assert.equal(I.multiWindowReturnBallDragState.container, nextContainer);
    else assert.equal(I.multiWindowReturnBallDragState, null);
    h.fireTimers();
    assert.equal(h.artClasses.size, takenOver ? 1 : 0);
});
}
