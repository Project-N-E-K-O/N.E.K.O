const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');

function dom() {
    class Target {
        constructor() { this.listeners = new Map(); this.style = {}; }
        addEventListener(type, fn) {
            if (!this.listeners.has(type)) this.listeners.set(type, new Set());
            this.listeners.get(type).add(fn);
        }
        removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
        dispatchEvent(event) { this.fire(event.type, event); return true; }
        fire(type, event = {}) {
            event.type = type;
            event.preventDefault ||= () => {};
            event.stopPropagation ||= () => {};
            for (const fn of [...(this.listeners.get(type) || [])]) fn(event);
        }
    }
    const window = new Target();
    window.CustomEvent = class CustomEvent {
        constructor(type, init = {}) { this.type = type; this.detail = init.detail; }
    };
    const document = new Target();
    const canvas = new Target();
    const shakes = [];
    canvas.animate = (keyframes, options) => {
        const animation = {
            keyframes, options, cancelled: false,
            cancel() { this.cancelled = true; this.oncancel?.(); }
        };
        shakes.push(animation);
        return animation;
    };
    canvas.setPointerCapture = () => {};
    canvas.releasePointerCapture = () => {};
    canvas.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 800 });
    canvas.clientWidth = 1000;
    canvas.clientHeight = 800;
    canvas.ownerDocument = document;
    document.defaultView = window;
    document.body = { classList: { contains: () => false } };
    document.getElementById = () => canvas;
    return { window, document, canvas, shakes };
}

function pointer(pointerId, clientX, clientY = 200, pointerType = 'touch') {
    return { pointerId, pointerType, clientX, clientY, screenX: clientX, screenY: clientY,
        button: 0, preventDefault() {}, stopPropagation() {} };
}

function load(harness, filename, extra = {}) {
    const context = vm.createContext({ ...harness, console, performance, setTimeout, clearTimeout, ...extra });
    vm.runInContext(fs.readFileSync(path.join(root, filename), 'utf8'), context);
    return context;
}

function helper() {
    const h = dom();
    h.window.window = h.window;
    load(h, 'static/avatar/avatar-click-shake.js', { window: h.window });
    return { ...h, shake: h.window.NekoAvatarClickShake };
}

function live2d() {
    const h = dom();
    load(h, 'static/avatar/avatar-click-shake.js');
    load(h, 'static/avatar/avatar-touch-gestures.js');
    class Live2DManager {}
    const context = load(h, 'static/live2d/live2d-interaction.js', { Live2DManager });
    vm.runInContext('clearLive2DPeek = () => {};', context);
    const model = {
        x: 100, y: 100, scale: { x: 1, set(value) { this.x = value; } },
        on() {},
        toLocal(p) { return { x: (p.x - this.x) / this.scale.x, y: (p.y - this.y) / this.scale.x }; },
        toGlobal(p) { return { x: this.x + p.x * this.scale.x, y: this.y + p.y * this.scale.x }; },
        containsPoint: () => true, hitTest: () => ['head']
    };
    h.window.DragHelpers = { disableButtonPointerEvents() {}, restoreButtonPointerEvents() {} };
    const manager = new Live2DManager();
    Object.assign(manager, {
        _isModelReadyForInteraction: true, currentModel: model,
        pixi_app: { view: h.canvas, renderer: { screen: { width: 1000, height: 800 } } },
        isLive2DPeekActive: () => false,
        // 点击 300ms 后的动作/表情分支不在本用例范围内
        _canTriggerTouchSetArea: () => false,
        _savePositionAfterInteraction: async () => {},
        _settleLive2DDragTerminal: async () => {}
    });
    h.window.live2dManager = manager;
    manager.setupDragAndDrop(model);
    manager.setupTouchZoom(model);
    return { ...h, manager, model };
}

async function humanoid(kind) {
    const THREE = await import(pathToFileURL(path.join(root, 'static/libs/three.module.js')).href);
    const h = dom();
    h.window.THREE = THREE;
    load(h, 'static/avatar/avatar-click-shake.js');
    load(h, 'static/avatar/avatar-touch-gestures.js');
    const context = load(h, `static/${kind}/${kind}-interaction.js`);
    const model = new THREE.Object3D();
    const camera = new THREE.PerspectiveCamera(40, 1.25, 0.1, 100);
    camera.position.z = 10;
    camera.updateMatrixWorld();
    const manager = {
        camera, renderer: { domElement: h.canvas }, _isModelReadyForInteraction: true,
        currentModel: { scene: model, mesh: model },
        setModelScaleScalar(value) { model.scale.setScalar(value); }
    };
    const Klass = vm.runInContext(kind === 'vrm' ? 'VRMInteraction' : 'MMDInteraction', context);
    const interaction = new Klass(manager);
    interaction._hitTestModel = () => true;
    interaction.checkLocked = () => false;
    interaction._rememberPanDragPointer = () => {};
    interaction._rememberDragHintPanPointer = () => {};
    interaction._recordDragHintPointerEdgeRelease = async () => {};
    interaction._savePositionAfterInteraction = async () => {};
    interaction._checkAndSwitchDisplay = async () => false;
    interaction._snapModelIntoScreen = async () => false;
    interaction.initDragAndZoom();
    return { ...h, interaction };
}

function mouse(clientX, clientY = 400, button = 0) {
    return { ...pointer(1, clientX, clientY, 'mouse'), button };
}

test('helper shakes horizontally via the translate property and restarts on rapid clicks', () => {
    const h = helper();
    const first = h.shake.play(h.canvas);
    assert.equal(h.shakes.length, 1);
    assert.ok(first.keyframes.every(frame => /^-?\d+(\.\d+)?px 0$/.test(frame.translate)),
        'must not write transform, which would drop the canvas translateZ(0) layer');
    assert.equal(first.keyframes[0].translate, '0px 0');
    assert.equal(first.keyframes.at(-1).translate, '0px 0');
    h.shake.play(h.canvas);
    assert.equal(first.cancelled, true);
    assert.equal(h.shakes.length, 2);
});

test('helper respects the animation settings toggle and missing elements', () => {
    const h = helper();
    h.window.modelClickShakeEnabled = false;
    assert.equal(h.shake.play(h.canvas), null);
    assert.equal(h.shakes.length, 0);
    h.window.modelClickShakeEnabled = true;
    assert.equal(h.shake.play(null), null);
    assert.ok(h.shake.play(h.canvas));
});

test('helper falls back to saved settings on pages without app-settings.js', () => {
    for (const [saved, expected] of [
        [{ modelClickShakeEnabled: false }, false],
        [{ modelClickShakeEnabled: 'false' }, false],
        [{ modelClickShakeEnabled: true }, true],
        [null, true],
        ['{not json', true]
    ]) {
        const h = helper();
        h.window.localStorage = {
            getItem: key => (key === 'project_neko_settings' && saved !== null
                ? (typeof saved === 'string' ? saved : JSON.stringify(saved))
                : null)
        };
        assert.equal(h.shake.isEnabled(), expected, JSON.stringify(saved));
        assert.equal(!!h.shake.play(h.canvas), expected);
        // 本页运行时的取值优先于存储
        h.window.modelClickShakeEnabled = !expected;
        assert.equal(h.shake.isEnabled(), !expected);
    }
});

test('helper skips the shake when the system asks for reduced motion', () => {
    const h = helper();
    let reduce = true;
    h.window.matchMedia = query => ({ matches: reduce && query === '(prefers-reduced-motion: reduce)' });
    assert.equal(h.shake.play(h.canvas), null);
    reduce = false;
    assert.ok(h.shake.play(h.canvas));
});

test('helper tap detection matches the Live2D click thresholds', () => {
    const h = helper();
    const start = h.shake.tapStart({ clientX: 100, clientY: 100 });
    assert.equal(h.shake.isTap(start, { clientX: 106, clientY: 108 }), true);
    assert.equal(h.shake.isTap(start, { clientX: 111, clientY: 100 }), false);
    assert.equal(h.shake.isTap(null, { clientX: 100, clientY: 100 }), false);
    assert.equal(h.shake.isTap({ ...start, time: Date.now() - 300 }, { clientX: 100, clientY: 100 }), false);
});

test('Live2D tap shakes immediately, before the delayed motion; a drag does not', async () => {
    const h = live2d();
    h.canvas.fire('pointerdown', pointer(1, 200));
    h.document.fire('pointerup', pointer(1, 200));
    assert.equal(h.shakes.length, 1, 'shake must not wait for the 300ms motion delay');

    h.canvas.fire('pointerdown', pointer(2, 200));
    h.document.fire('pointermove', pointer(2, 260));
    h.document.fire('pointerup', pointer(2, 260));
    await new Promise(setImmediate);
    assert.equal(h.shakes.length, 1);

    h.window.modelClickShakeEnabled = false;
    h.canvas.fire('pointerdown', pointer(3, 260));
    h.document.fire('pointerup', pointer(3, 260));
    assert.equal(h.shakes.length, 1);
});

test('VRM and MMD shake on a left-click or one-finger tap, not on drag, orbit or pinch', async () => {
    for (const kind of ['vrm', 'mmd']) {
        const h = await humanoid(kind);
        h.canvas.fire('mousedown', mouse(400));
        h.document.fire('mouseup', mouse(403));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} click`);

        h.canvas.fire('mousedown', mouse(400));
        h.document.fire('mousemove', mouse(460));
        h.document.fire('mouseup', mouse(460));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} drag`);

        // 拖出点击范围再拖回原处，仍是拖拽
        h.canvas.fire('mousedown', mouse(400));
        h.document.fire('mousemove', mouse(430));
        h.document.fire('mousemove', mouse(401));
        h.document.fire('mouseup', mouse(401));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} drag out and back`);

        h.canvas.fire('pointerdown', pointer(9, 400, 400));
        h.document.fire('pointermove', pointer(9, 440, 400));
        h.document.fire('pointermove', pointer(9, 402, 400));
        h.document.fire('pointerup', pointer(9, 402, 400));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} touch drag out and back`);

        h.canvas.fire('mousedown', mouse(400, 400, 2));
        h.document.fire('mouseup', mouse(400, 400, 2));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 1, `${kind} orbit`);

        h.canvas.fire('pointerdown', pointer(10, 400, 400));
        h.document.fire('pointerup', pointer(10, 400, 400));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 2, `${kind} tap`);

        h.canvas.fire('pointerdown', pointer(11, 400, 400));
        h.canvas.fire('pointerdown', pointer(12, 600, 400));
        h.document.fire('pointerup', pointer(12, 600, 400));
        h.document.fire('pointerup', pointer(11, 400, 400));
        await new Promise(setImmediate);
        assert.equal(h.shakes.length, 2, `${kind} pinch leftover finger`);

        h.interaction.cleanupDragAndZoom();
    }
});

// ---- 连点规则 ----

function comboHelper(extraWindow = {}) {
    const h = dom();
    h.window.window = h.window;
    let now = 1_000_000;
    let nextTimer = 1;
    const timers = new Map();
    const clock = {
        advance(ms) {
            now += ms;
            for (const [id, timer] of [...timers].sort((a, b) => a[1].at - b[1].at)) {
                if (timer.at <= now && timers.has(id)) {
                    timers.delete(id);
                    timer.fn();
                }
            }
        },
        pending: () => timers.size
    };
    class FakeDate extends Date {
        static now() { return now; }
    }
    const sent = [];
    const emotions = [];
    Object.assign(h.window, {
        appButtons: { sendAvatarInteractionPayload: async payload => { sent.push(payload); return true; } },
        applyEmotion: emotion => emotions.push(emotion)
    }, extraWindow);
    load(h, 'static/avatar/avatar-click-shake.js', {
        window: h.window,
        Date: FakeDate,
        setTimeout: (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, at: now + ms }); return id; },
        clearTimeout: id => timers.delete(id)
    });
    const shake = h.window.NekoAvatarClickShake;
    return { ...h, shake, clock, sent, emotions, gap: Math.floor(shake.COMBO_WINDOW_MS / 2) };
}

function peak(animation) {
    const values = animation.keyframes.map(frame => frame.translate.split(' ').map(parseFloat));
    return {
        x: Math.max(...values.map(([x]) => Math.abs(x || 0))),
        y: Math.max(...values.map(([, y]) => Math.abs(y || 0)))
    };
}

test('combo grows the shake 1x -> 1.5x -> 2x with happy then surprised, and the 4th tap shakes up and down and gets angry', () => {
    const h = comboHelper();
    const results = [];
    for (let i = 0; i < 4; i++) {
        results.push(h.shake.tap(h.canvas));
        h.clock.advance(h.gap);
    }
    assert.deepEqual(results.map(r => r.count), [1, 2, 3, 4]);
    assert.deepEqual(results.map(r => r.angry), [false, false, false, true]);
    // 只有第 1 下照常播点击动作/表情，第 2 下起表情归连点管
    assert.deepEqual(results.map(r => r.allowTouchSet), [true, false, false, false]);
    assert.deepEqual(h.shakes.map(peak), [
        { x: 4, y: 0 }, { x: 6, y: 0 }, { x: 8, y: 0 }, { x: 0, y: 10 }
    ]);
    assert.ok(h.shakes[3].keyframes.every(f => /^0 -?\d+(\.\d+)?px$/.test(f.translate)));
    assert.ok(h.shakes[3].options.duration > h.shakes[0].options.duration);
    assert.deepEqual(h.emotions, ['happy', 'surprised', 'angry']);
    // 第 4 下立刻出声，第 2/3 下排着的语音已作废
    assert.deepEqual(h.sent.map(p => p.intensity), ['angry']);
    assert.equal(h.sent[0].tool_id, 'tap');
    assert.equal(h.sent[0].action_id, 'poke');
    assert.equal(h.sent[0].target, 'avatar');
    h.clock.advance(h.shake.COMBO_WINDOW_MS * 2);
    assert.equal(h.sent.length, 1, 'the cancelled 2nd/3rd-tap lines must never be sent afterwards');
});

test('for 30 seconds after the angry tap, clicks give no feedback at all', () => {
    const h = comboHelper();
    for (let i = 0; i < 4; i++) {
        h.shake.tap(h.canvas);
        h.clock.advance(h.gap);
    }
    const shakesAtAngry = h.shakes.length;
    for (let i = 0; i < 6; i++) {
        const result = h.shake.tap(h.canvas);
        assert.equal(result.allowTouchSet, false);
        h.clock.advance(h.gap);
    }
    h.clock.advance(h.shake.ANGRY_COOLDOWN_MS - 6 * h.gap - 2 * h.gap - 1);
    assert.equal(h.shake.tap(h.canvas).allowTouchSet, false, 'still inside the cooldown');
    assert.equal(h.shakes.length, shakesAtAngry, 'no shake during the cooldown');
    assert.deepEqual(h.emotions, ['happy', 'surprised', 'angry']);
    assert.equal(h.sent.length, 1);

    h.clock.advance(h.gap + 1);
    const fresh = h.shake.tap(h.canvas);
    assert.deepEqual([fresh.count, fresh.angry, fresh.allowTouchSet], [1, false, true]);
    assert.equal(peak(h.shakes.at(-1)).x, 4);
});

test('a single tap is silent; the 2nd tap speaks only after the combo ends', () => {
    const h = comboHelper();
    h.shake.tap(h.canvas);
    h.clock.advance(h.shake.COMBO_WINDOW_MS * 2);
    assert.equal(h.sent.length, 0);
    assert.deepEqual(h.emotions, []);

    h.shake.tap(h.canvas);
    h.clock.advance(h.gap);
    h.shake.tap(h.canvas);
    assert.equal(h.sent.length, 0, 'voice waits until the clicking stops');
    h.clock.advance(h.shake.COMBO_WINDOW_MS);
    assert.deepEqual(h.sent.map(p => p.intensity), ['normal']);
    assert.deepEqual(h.emotions, ['happy']);
});

test('the 3rd tap interrupts the 2nd tap: only one rapid line is sent', () => {
    const h = comboHelper();
    h.shake.tap(h.canvas);
    h.clock.advance(h.gap);
    h.shake.tap(h.canvas);
    h.clock.advance(h.gap);
    h.shake.tap(h.canvas);
    h.clock.advance(h.shake.COMBO_WINDOW_MS);
    assert.deepEqual(h.sent.map(p => p.intensity), ['rapid']);
    assert.equal(h.clock.pending(), 0);
});

test('a pause longer than the combo window starts over from the first tap', () => {
    const h = comboHelper();
    h.shake.tap(h.canvas);
    h.clock.advance(h.gap);
    h.shake.tap(h.canvas);
    h.clock.advance(h.shake.COMBO_WINDOW_MS + 1);
    assert.deepEqual(h.sent.map(p => p.intensity), ['normal']);
    const next = h.shake.tap(h.canvas);
    assert.equal(next.count, 1);
    assert.equal(next.allowTouchSet, true);
    assert.equal(peak(h.shakes.at(-1)).x, 4);
});

test('combo still speaks and changes expression when the shake itself is turned off', () => {
    const h = comboHelper({ modelClickShakeEnabled: false });
    for (let i = 0; i < 4; i++) {
        h.shake.tap(h.canvas);
        h.clock.advance(h.gap);
    }
    assert.equal(h.shakes.length, 0);
    assert.deepEqual(h.emotions, ['happy', 'surprised', 'angry']);
    assert.deepEqual(h.sent.map(p => p.intensity), ['angry']);
});

test('tutorial and a held avatar tool keep the plain shake without combo voice, expressions or cooldown', () => {
    const tutorial = comboHelper({ isInTutorial: true });
    for (let i = 0; i < 6; i++) {
        const result = tutorial.shake.tap(tutorial.canvas);
        assert.equal(result.angry, false);
        assert.equal(result.allowTouchSet, true);
        tutorial.clock.advance(tutorial.gap);
    }
    tutorial.clock.advance(5000);
    assert.equal(tutorial.sent.length, 0);
    assert.deepEqual(tutorial.emotions, []);
    assert.equal(tutorial.shakes.length, 6);
    assert.ok(tutorial.shakes.every(a => peak(a).x === 4 && peak(a).y === 0));

    const tool = comboHelper();
    tool.shake.tap(tool.canvas);
    tool.clock.advance(tool.gap);
    tool.shake.tap(tool.canvas);
    tool.window.fire('react-chat-window:avatar-tool-state', { detail: { active: true, toolId: 'hammer' } });
    tool.clock.advance(5000);
    assert.equal(tool.sent.length, 0, 'picking up a tool drops the pending combo line');
    for (let i = 0; i < 4; i++) tool.shake.tap(tool.canvas);
    assert.deepEqual(tool.emotions, ['happy']);
    tool.window.fire('neko:deactivate-avatar-tool');
    for (let i = 0; i < 4; i++) tool.shake.tap(tool.canvas);
    assert.deepEqual(tool.emotions, ['happy', 'happy', 'surprised', 'angry']);
});

test('theater keeps the shake but suppresses combo voice, expressions and the angry cooldown', () => {
    let theater = true;
    const h = comboHelper({ nekoTheaterRuntime: { blocksOrdinaryChat: () => theater } });
    for (let i = 0; i < 4; i++) {
        h.shake.tap(h.canvas);
        h.clock.advance(h.gap);
    }
    assert.equal(peak(h.shakes[3]).y, 10);
    // 第 5 下仍然要晃，不能被冷却吞掉
    const fifth = h.shake.tap(h.canvas);
    assert.equal(fifth.count, 1);
    assert.equal(h.shakes.length, 5);
    h.clock.advance(5000);
    assert.equal(h.sent.length, 0);
    assert.deepEqual(h.emotions, []);

    // 退出剧场后立刻点，也是正常的第 1 下
    theater = false;
    const after = h.shake.tap(h.canvas);
    assert.deepEqual([after.count, after.angry, after.allowTouchSet], [1, false, true]);
    assert.equal(h.shakes.length, 6);
});

test('Live2D plays the delayed touch motion for a lone tap, but not once the next tap joins the combo', async () => {
    const h = live2d();
    let touchChecks = 0;
    h.manager._canTriggerTouchSetArea = () => { touchChecks += 1; return false; };
    h.window.applyEmotion = () => {};
    h.canvas.fire('pointerdown', pointer(30, 200));
    h.document.fire('pointerup', pointer(30, 200));
    await new Promise(resolve => setTimeout(resolve, 360));
    assert.equal(touchChecks, 1, 'a single tap keeps the configured touch motion');

    // 第 2 下落在首击的 300ms 等待期里：首击的点击动作也不能再播，否则会盖掉 happy
    const h2 = live2d();
    let touchChecks2 = 0;
    h2.manager._canTriggerTouchSetArea = () => { touchChecks2 += 1; return false; };
    h2.window.applyEmotion = () => {};
    for (let i = 0; i < 2; i++) {
        h2.canvas.fire('pointerdown', pointer(40 + i, 200));
        h2.document.fire('pointerup', pointer(40 + i, 200));
    }
    await new Promise(resolve => setTimeout(resolve, 360));
    assert.equal(touchChecks2, 0);
});

test('Live2D never plays touch motions during the angry cooldown', async () => {
    const h = live2d();
    let touchChecks = 0;
    h.manager._canTriggerTouchSetArea = () => { touchChecks += 1; return false; };
    h.window.applyEmotion = () => {};
    for (let i = 0; i < 6; i++) {
        h.canvas.fire('pointerdown', pointer(20 + i, 200));
        h.document.fire('pointerup', pointer(20 + i, 200));
    }
    assert.equal(h.shakes.length, 4, 'taps 5 and 6 fall in the cooldown and do not shake');
    assert.equal(peak(h.shakes[3]).y, 10);
    await new Promise(resolve => setTimeout(resolve, 360));
    assert.equal(touchChecks, 0);
});

test('Live2D clears running click motions and emotion changes before each hard-coded combo expression', () => {
    const h = live2d();
    const calls = [];
    Object.assign(h.manager, {
        _cancelTouchSetExpressionRestore: () => calls.push('cancelRestore'),
        _stopClickEffectAction: () => calls.push('stopClickEffect'),
        _cancelAvatarOperations: () => calls.push('cancelOperations'),
        clearEmotionEffects: () => calls.push('clearEffects'),
        isAvatarPerformanceCapabilityLocked: () => false
    });
    h.window.applyEmotion = emotion => calls.push(`emotion:${emotion}`);
    for (let i = 0; i < 2; i++) {
        h.canvas.fire('pointerdown', pointer(50 + i, 200));
        h.document.fire('pointerup', pointer(50 + i, 200));
    }
    assert.deepEqual(calls, ['cancelRestore', 'stopClickEffect', 'cancelOperations', 'clearEffects', 'emotion:happy']);

    // 演出锁住表情/动作时不抢占
    calls.length = 0;
    h.manager.isAvatarPerformanceCapabilityLocked = () => true;
    h.canvas.fire('pointerdown', pointer(52, 200));
    h.document.fire('pointerup', pointer(52, 200));
    assert.deepEqual(calls, ['emotion:surprised']);

    // 点在别的画布上（VRM/MMD）不碰 Live2D
    calls.length = 0;
    h.manager.isAvatarPerformanceCapabilityLocked = () => false;
    h.window.dispatchEvent(new h.window.CustomEvent('neko:avatar-click-combo-emotion', {
        detail: { element: {}, count: 2, emotion: 'happy' }
    }));
    assert.deepEqual(calls, []);
});

test('pending combo lines stay with the clicked character', () => {
    const switched = comboHelper({ lanlan_config: { lanlan_name: 'A' } });
    switched.shake.tap(switched.canvas);
    switched.clock.advance(switched.gap);
    switched.shake.tap(switched.canvas);
    switched.window.lanlan_config.lanlan_name = 'B';
    switched.clock.advance(switched.shake.COMBO_WINDOW_MS);
    assert.equal(switched.sent.length, 0, 'B must not answer a click on A');

    const switching = comboHelper({ lanlan_config: { lanlan_name: 'A' }, appState: { isSwitchingCatgirl: false } });
    switching.shake.tap(switching.canvas);
    switching.clock.advance(switching.gap);
    switching.shake.tap(switching.canvas);
    switching.window.appState.isSwitchingCatgirl = true;
    switching.clock.advance(switching.shake.COMBO_WINDOW_MS);
    assert.equal(switching.sent.length, 0, 'nothing is sent while a switch is in progress');

    // A 生气的冷却不算到 B 头上；换角色后连点从头算
    const cooled = comboHelper({ lanlan_config: { lanlan_name: 'A' } });
    for (let i = 0; i < 4; i++) {
        cooled.shake.tap(cooled.canvas);
        cooled.clock.advance(cooled.gap);
    }
    cooled.window.lanlan_config.lanlan_name = 'B';
    const first = cooled.shake.tap(cooled.canvas);
    assert.equal(first.count, 1);
    assert.equal(first.allowTouchSet, true);
});

test('reloading the model (character switch) clears the angry cooldown, so A -> B -> A starts fresh', () => {
    const h = comboHelper({ lanlan_config: { lanlan_name: 'A' } });
    for (let i = 0; i < 4; i++) {
        h.shake.tap(h.canvas);
        h.clock.advance(h.gap);
    }
    assert.equal(h.shake.tap(h.canvas).allowTouchSet, false, 'same character, no reload: still cooling down');

    h.window.lanlan_config.lanlan_name = 'B';
    h.window.fire('live2d-model-ready', { detail: {} });
    h.window.lanlan_config.lanlan_name = 'A';
    h.window.fire('live2d-model-ready', { detail: {} });
    const back = h.shake.tap(h.canvas);
    assert.deepEqual([back.count, back.angry, back.allowTouchSet], [1, false, true]);

    for (const name of ['vrm-model-loaded', 'mmd-model-loaded', 'pngtuber-model-loaded']) {
        const other = comboHelper({ lanlan_config: { lanlan_name: 'A' } });
        for (let i = 0; i < 4; i++) other.shake.tap(other.canvas);
        other.window.fire(name, { detail: {} });
        assert.equal(other.shake.tap(other.canvas).count, 1, name);
    }
});

test('leaving the theater mid-combo starts counting again from the first tap', () => {
    let theater = true;
    const h = comboHelper({ nekoTheaterRuntime: { blocksOrdinaryChat: () => theater } });
    for (let i = 0; i < 5; i++) {
        h.shake.tap(h.canvas);
        h.clock.advance(h.gap);
    }
    // 第 5 下在剧场里开了新的一串；不等连点窗口过去就离场再点
    theater = false;
    const after = h.shake.tap(h.canvas);
    assert.deepEqual([after.count, after.angry, after.allowTouchSet], [1, false, true]);
    assert.equal(peak(h.shakes.at(-1)).x, 4);
    h.clock.advance(h.shake.COMBO_WINDOW_MS * 2);
    assert.deepEqual(h.emotions, []);
    assert.equal(h.sent.length, 0);
});

test('taps during the theater never leave a line queued for after it ends', () => {
    let theater = true;
    const h = comboHelper({ nekoTheaterRuntime: { blocksOrdinaryChat: () => theater } });
    h.shake.tap(h.canvas);
    h.clock.advance(h.gap);
    h.shake.tap(h.canvas);
    theater = false;
    h.clock.advance(h.shake.COMBO_WINDOW_MS);
    assert.equal(h.sent.length, 0, 'a double tap in the theater must not speak after leaving it');

    // 第 2 下在剧场外排上了，第 3 下进剧场：那句也作废
    const h2 = comboHelper({ nekoTheaterRuntime: { blocksOrdinaryChat: () => theater } });
    theater = false;
    h2.shake.tap(h2.canvas);
    h2.clock.advance(h2.gap);
    h2.shake.tap(h2.canvas);
    h2.clock.advance(h2.gap);
    theater = true;
    h2.shake.tap(h2.canvas);
    theater = false;
    h2.clock.advance(h2.shake.COMBO_WINDOW_MS);
    assert.equal(h2.sent.length, 0);
    assert.equal(h2.clock.pending(), 0);
});

test('Live2D first-tap click effect does not fall back to an expression once a combo preempts its motion', async () => {
    const h = live2d();
    const expressions = [];
    Object.assign(h.manager, {
        _cancelTouchSetExpressionRestore() {},
        _stopClickEffectAction() {},
        _cancelAvatarOperations() {},
        clearEmotionEffects() {},
        isAvatarPerformanceCapabilityLocked: () => false,
        getRandomElement: list => list[0],
        fileReferences: { Motions: { happy: [{ File: 'happy.motion3.json' }] }, Expressions: [] },
        emotionMapping: { expressions: { happy: ['happy.exp3.json'] } },
        playExpression: async (...args) => { expressions.push(args); return true; },
        // 第 2 下在首击动作加载期间抢占：动作被中止，返回 false
        playActionMotion: async () => { h.manager.preemptForClickComboEmotion(); return false; }
    });
    const before = h.manager._clickComboPreemptGeneration || 0;
    assert.equal(await h.manager._playTemporaryClickEffect('happy'), false);
    assert.deepEqual(expressions, []);
    assert.equal(h.manager._clickComboPreemptGeneration, before + 1);

    // 没被抢占时，动作失败仍照常用表情兜底
    h.manager.playActionMotion = async () => false;
    await h.manager._playTemporaryClickEffect('happy');
    assert.equal(expressions.length, 1);
    assert.equal(typeof expressions[0][2].isCurrent, 'function');
});
