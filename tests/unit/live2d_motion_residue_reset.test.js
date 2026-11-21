const assert = require('node:assert/strict');
const test = require('node:test');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { read, loadRuntime, createMao } = require('./fixtures/live2d_runtime_fixture.cjs');

// 动作末帧把参数停在非默认值：ParamCheek 停在 1、ParamArmLA01 停在 12、
// ParamBrowLY（用作常驻表情参数）停在 0.6、ParamA（嘴部振幅）停在 0.8。
const RESIDUE_CURVES = { ParamCheek: 1, ParamArmLA01: 12, ParamBrowLY: 0.6, ParamA: 0.8 };
const RESIDUE_MOTION = {
    Version: 3,
    Meta: { Duration: 0.5, Fps: 30, FadeInTime: 0, FadeOutTime: 0, Loop: false, AreBeziersRestricted: true,
        CurveCount: 4, TotalSegmentCount: 4, TotalPointCount: 8, UserDataCount: 0, TotalUserDataSize: 0 },
    Curves: Object.entries(RESIDUE_CURVES).map(([Id, end]) => ({
        Target: 'Parameter', Id, FadeInTime: 0, FadeOutTime: 0, Segments: [0, 0, 0, 0.5, end]
    }))
};

async function fixture() {
    const runtime = await loadRuntime();
    runtime.context.document.body.classList.remove = () => {};
    vm.runInContext(read('static/live2d/live2d-emotion.js'), runtime.context,
        { filename: path.resolve(__dirname, '../../static/live2d/live2d-emotion.js') });
    const f = createMao(runtime), api = runtime.context.PIXI.live2d;
    const settings = new api.Cubism4ModelSettings({ ...f.settings, url: 'http://localhost/mao/model.model3.json',
        FileReferences: { ...f.settings.FileReferences,
            Motions: { PreviewAll: [{ File: 'residue.motion3.json' }] }, Expressions: [] } });
    const native = new api.Cubism4MotionManager(settings, { motionPreload: api.MotionPreloadStrategy.NONE });
    native._loadMotion = async () => runtime.sdk.Motion.create(RESIDUE_MOTION);
    const events = new EventEmitter();
    Object.assign(f.model.internalModel, { motionManager: native, events,
        on: events.on.bind(events), off: events.off.bind(events) });
    f.model.motion = (...args) => native.startMotion(...args);
    f.manager.fileReferences = settings.json.FileReferences;
    f.manager.emotionMapping = { expressions: {} };
    f.manager.resolveAssetPath = file => file;
    f.manager.recordInitialParameters();
    f.manager.installMouthOverride();
    f.native = native;
    f.events = events;
    f.defaultOf = id => f.core.getParameterDefaultValue(f.core.getParameterIndex(id));
    // 按打包 SDK 的帧顺序：motion → afterMotionUpdate → saveParameters → … → core.update → loadParameters。
    f.frame = (ms = 1000 / 30, { rawMotion = false } = {}) => {
        runtime.context.clock += ms;
        const seconds = runtime.context.clock / 1000;
        if (rawMotion) f.manager._origMotionManagerUpdate(f.core, seconds);
        else native.update(f.core, seconds);
        events.emit('afterMotionUpdate');
        f.core.saveParameters();
        events.emit('beforeModelUpdate');
        f.core.update();
        f.core.loadParameters();
    };
    f.frames = (count, options) => { for (let i = 0; i < count; i++) f.frame(1000 / 30, options); };
    f.playToEnd = async (options) => {
        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        f.frames(20, options);
        assert.equal(native.queueManager._motions.length, 0, 'the residue motion finished');
    };
    const releaseCore = f.release;
    f.release = () => { f.manager._cancelAvatarOperations?.(); native.destroy(); releaseCore(); };
    return f;
}

test('the loaded SDK motion exposes its curve parameter ids synchronously', async () => {
    const f = await fixture();
    try {
        await f.native.loadMotion('PreviewAll', 0);
        const ids = f.manager._getLoadedMotionParameterIds('PreviewAll', 0);
        assert.deepEqual(Array.from(ids).sort(), Object.keys(RESIDUE_CURVES).sort());
        assert.equal(f.manager._getLoadedMotionParameterIds('PreviewAll', 1), null);
    } finally { f.release(); }
});

test('a direct model.motion preview (model manager / saved idle) fades its residue back to the moc defaults', async () => {
    const f = await fixture();
    try {
        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        f.frames(14);
        assert.ok(f.value('ParamArmLA01') > 6, 'the motion is driving the curve');
        f.frames(3);
        const residue = f.value('ParamArmLA01');
        f.frames(2);
        const midFade = f.value('ParamArmLA01');
        assert.ok(midFade < residue && midFade > f.defaultOf('ParamArmLA01'), 'the reset is smooth, not a jump');
        f.frames(12);
        assert.equal(f.value('ParamArmLA01'), f.defaultOf('ParamArmLA01'));
        assert.equal(f.value('ParamCheek'), f.defaultOf('ParamCheek'));
        assert.equal(f.manager._motionResidueFade.touched.size, 0, 'persistent ids are skipped, not left pending');
    } finally { f.release(); }
});

test('a model whose moc default is not zero returns to that default', async () => {
    const f = await fixture();
    try {
        const index = f.core.getParameterIndex('ParamCheek');
        f.defaultOverrides.set(index, 0.25);
        await f.playToEnd();
        f.frames(12);
        assert.equal(f.value('ParamCheek'), Math.fround(0.25));
    } finally { f.release(); }
});

test('playActionMotion without tracked ids (TouchSet) and an interrupted motion both return to the defaults', async () => {
    const f = await fixture();
    try {
        assert.equal(await f.manager.playActionMotion('PreviewAll', 0), true);
        assert.equal(f.manager._activeMotionParamIds, null, 'TouchSet records no ids itself');
        f.frames(20);
        f.frames(12);
        assert.equal(f.value('ParamArmLA01'), f.defaultOf('ParamArmLA01'));

        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        f.frames(8);
        assert.ok(f.value('ParamArmLA01') > 1);
        f.native.stopAllMotions();
        f.frames(12);
        assert.equal(f.value('ParamArmLA01'), f.defaultOf('ParamArmLA01'));
        assert.equal(f.value('ParamCheek'), f.defaultOf('ParamCheek'));
    } finally { f.release(); }
});

test('a saved user parameter returns to its saved value instead of the moc default', async () => {
    const f = await fixture();
    try {
        f.manager.savedModelParameters = { ParamArmLA01: 4 };
        f.manager._shouldApplySavedParams = true;
        // 用未包装的 SDK motion 更新，只看淡出层自己的目标值。
        await f.playToEnd({ rawMotion: true });
        f.frames(12, { rawMotion: true });
        assert.equal(f.value('ParamArmLA01'), 4);
        assert.equal(f.value('ParamCheek'), f.defaultOf('ParamCheek'));
    } finally { f.release(); }
});

test('a saved parameter fades from motion-plus-offset to the saved value through the saved-parameter layer', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamArmLA01: 4 });
        assert.equal(f.manager._shouldApplySavedParams, true);
        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        const trace = [];
        for (let i = 0; i < 40; i++) {
            f.frame();
            trace.push({ value: f.value('ParamArmLA01'), playing: f.native.queueManager._motions.length > 0 });
        }
        const end = trace.findIndex(item => !item.playing);
        assert.ok(end > 0, 'the motion finished');
        const peak = trace[end - 1].value;
        assert.ok(peak > 14, 'the saved offset is composed onto the motion value while it plays');
        const after = trace.slice(end).map(item => item.value);
        assert.ok(after.filter(value => value > 4.01 && value < peak - 0.01).length >= 5,
            `the reset passes through several intermediate values: ${after.map(v => v.toFixed(2)).join(' ')}`);
        for (let i = 1; i < after.length; i++) {
            assert.ok(after[i] <= after[i - 1] + 1e-6, 'the saved parameter never jumps back up');
            assert.ok(after[i - 1] - after[i] <= 5, `no single-frame jump: ${after.map(v => v.toFixed(2)).join(' ')}`);
        }
        assert.ok(Math.abs(after[0] - peak) < 3, 'no jump to the saved value on the hand-off frame');
        assert.equal(after[after.length - 1], 4);

        // 中途停止：队列在两帧之间被清空，下一帧包装层先于淡出层看到这个参数。
        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        f.frames(10);
        const beforeStop = f.value('ParamArmLA01');
        assert.ok(beforeStop > 10);
        f.native.stopAllMotions();
        const stopped = [];
        for (let i = 0; i < 14; i++) {
            f.frame();
            stopped.push(f.value('ParamArmLA01'));
        }
        assert.ok(Math.abs(stopped[0] - beforeStop) < 3, 'the hand-off frame keeps motion plus offset');
        for (let i = 1; i < stopped.length; i++) {
            assert.ok(stopped[i - 1] - stopped[i] <= 5, `no single-frame jump after stop: ${stopped.map(v => v.toFixed(2)).join(' ')}`);
        }
        assert.equal(stopped[stopped.length - 1], 4);
    } finally { f.release(); }
});

test('a named saved key wins over a param_N alias of the same parameter, as in the saved-parameter layer', async () => {
    const f = await fixture();
    try {
        const index = f.core.getParameterIndex('ParamArmLA01');
        for (const saved of [{ ParamArmLA01: 0, [`param_${index}`]: 4 }, { [`param_${index}`]: 4, ParamArmLA01: 0 }]) {
            f.manager.savedModelParameters = saved;
            f.manager._shouldApplySavedParams = true;
            assert.equal(f.manager._resolveParameterResetTarget(f.core, index), 0);
        }
        f.manager.savedModelParameters = { [`param_${index}`]: 4 };
        assert.equal(f.manager._resolveParameterResetTarget(f.core, index), 4, 'an index-only key is still honoured');
    } finally { f.release(); }
});

test('the saved-parameter layer stops deferring when the fade layer is not running', async () => {
    const f = await fixture();
    try {
        f.apply({ ParamArmLA01: 4 });
        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        f.frames(10);
        f.native.stopAllMotions();
        f.frame();
        assert.ok(f.value('ParamArmLA01') > 10, 'hand-off keeps motion plus offset');
        // 不再发 afterMotionUpdate：淡出层停摆时，保存参数层下一帧就照旧写回保存值。
        for (let i = 0; i < 2; i++) {
            f.runtime.context.clock += 33;
            f.native.update(f.core, f.runtime.context.clock / 1000);
            f.core.saveParameters();
            f.core.update();
            f.core.loadParameters();
        }
        assert.equal(f.value('ParamArmLA01'), 4);
    } finally { f.release(); }
});

test('an expression smooth reset does not re-add a residue fade that finished between its two samples', async () => {
    const f = await fixture();
    try {
        const index = f.core.getParameterIndex('ParamArmLA01');
        assert.equal(await f.model.motion('PreviewAll', 0, 3), true);
        while (f.native.queueManager._motions.length > 0) f.frame();
        while (!f.manager._isMotionResidueFading(index)) f.frame();
        const start = f.runtime.context.clock;
        while (f.runtime.context.clock - start < 250) f.frame(25);
        assert.equal(f.manager._isMotionResidueFading(index), true);
        const rendered = [];
        const reset = f.manager.smoothResetToInitialState(800);
        f.events.on('beforeModelUpdate', () => rendered.push(f.value('ParamArmLA01')));
        f.frame(30);    // Phase 0：仍在淡出
        assert.equal(f.manager._isMotionResidueFading(index), true);
        f.frame(30);    // 淡出结束，Phase 1 计算差分
        assert.equal(f.manager._isMotionResidueFading(index), false);
        for (let i = 0; i < 30; i++) f.frame(30);
        await reset;
        assert.equal(rendered[1], f.defaultOf('ParamArmLA01'), 'the finished fade is not counted as an expression delta');
        assert.ok(rendered.slice(1).every(value => value === f.defaultOf('ParamArmLA01')), 'no rebound while the reset fades out');
    } finally { f.release(); }
});

test('persistent expression, active expression and mouth amplitude parameters are skipped', async () => {
    const f = await fixture();
    try {
        f.manager.persistentExpressionParamsByName = { resident: [{ Id: 'ParamBrowLY', Value: 0.6 }] };
        // 用 vm 里的 Set，管理器按 instanceof Set 认表情参数。
        f.manager._activeExpressionParamIds = vm.runInContext("new Set(['ParamCheek'])", f.runtime.context);
        await f.playToEnd({ rawMotion: true });
        const residue = { brow: f.value('ParamBrowLY'), cheek: f.value('ParamCheek'), mouth: f.value('ParamA') };
        assert.ok(residue.brow > 0.5 && residue.cheek > 0.9 && residue.mouth > 0.7, 'the motion left residue');
        f.frames(12, { rawMotion: true });
        assert.equal(f.value('ParamBrowLY'), residue.brow, 'persistent expression parameter untouched');
        assert.equal(f.value('ParamCheek'), residue.cheek, 'active expression parameter untouched');
        assert.equal(f.value('ParamA'), residue.mouth, 'mouth amplitude parameter untouched');
        assert.equal(f.value('ParamArmLA01'), f.defaultOf('ParamArmLA01'));
    } finally { f.release(); }
});

test('a foreign writer takes over a parameter mid-fade', async () => {
    const f = await fixture();
    try {
        await f.playToEnd();
        f.frames(2);
        f.core.setParameterValueById('ParamArmLA01', 7);
        f.frames(12);
        assert.equal(f.value('ParamArmLA01'), 7);
    } finally { f.release(); }
});

for (const [label, own] of [
    ['the tutorial', f => { f.runtime.context.isInTutorial = true; }],
    ['the home tutorial lock', f => { f.runtime.context.isNekoHomeTutorialInteractionLocked = () => true; }],
    ['a running universal tutorial', f => { f.runtime.context.universalTutorialManager = { isTutorialRunning: true }; }],
    ['an avatar performance session', f => { f.manager._avatarPerformanceSessionOwners = new Map([['motion', {}]]); }],
    ['an avatar performance bypass call', f => { f.manager._avatarPerformanceBypassLocks = true; }],
    ['a temporary motion suspension', f => { f.manager._temporaryMotionSuspendToken = 'performance'; }],
]) {
    test(`no reset while ${label} owns the model`, async () => {
        const f = await fixture();
        try {
            own(f);
            // 演出暂停期间包装层不推进 motion，这里直接推进 SDK 只看淡出层。
            await f.playToEnd({ rawMotion: true });
            const residue = f.value('ParamArmLA01');
            assert.ok(residue > 11);
            f.frames(12, { rawMotion: true });
            assert.equal(f.value('ParamArmLA01'), residue, 'the residue is left to the owner');
            assert.ok(f.value('ParamCheek') > 0.9);
            assert.equal(f.manager._motionResidueFade.touched.size, 0);
        } finally { f.release(); }
    });
}

test('a fade in progress stops as soon as the tutorial takes the model', async () => {
    const f = await fixture();
    try {
        await f.playToEnd();
        f.frames(2);
        const frozen = f.value('ParamArmLA01');
        f.runtime.context.isInTutorial = true;
        f.frames(12);
        assert.equal(f.value('ParamArmLA01'), frozen);
        f.runtime.context.isInTutorial = false;
        f.frames(12);
        assert.equal(f.value('ParamArmLA01'), frozen, 'nothing touched under the tutorial is reset afterwards');
    } finally { f.release(); }
});

test('clearing an expression resets its parameters to the moc default, except under the tutorial', async () => {
    for (const tutorial of [false, true]) {
        const f = await fixture();
        try {
            f.runtime.context.isInTutorial = tutorial;
            f.core.setParameterValueById('ParamCheek', 0.5);
            f.manager.recordInitialParameters();
            f.manager._activeExpressionParamIds = new Set(['ParamCheek']);
            f.core.setParameterValueById('ParamCheek', 0.9);
            await f.manager.clearExpression();
            assert.equal(f.value('ParamCheek'), tutorial ? 0.5 : f.defaultOf('ParamCheek'),
                tutorial ? 'tutorial keeps the recorded snapshot target' : 'snapshot residue is not the target');
        } finally { f.release(); }
    }
});

test('emotion cleanup hands motion residue to the fade and keeps the snapshot path under the tutorial', async () => {
    for (const tutorial of [false, true]) {
        const f = await fixture();
        try {
            f.runtime.context.isInTutorial = tutorial;
            f.manager._setActiveMotionParamIds(['ParamArmLA01']);
            f.core.setParameterValueById('ParamArmLA01', 9);
            f.manager.clearEmotionEffects();
            if (tutorial) {
                assert.equal(f.value('ParamArmLA01'), 0, 'tutorial keeps the immediate snapshot reset');
            } else {
                assert.equal(f.value('ParamArmLA01'), 9, 'outside the tutorial the reset is smooth');
                f.frames(12);
                assert.equal(f.value('ParamArmLA01'), f.defaultOf('ParamArmLA01'));
            }
        } finally { f.release(); }
    }
});
