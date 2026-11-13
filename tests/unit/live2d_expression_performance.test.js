const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');
const { loadRuntime, createMao, read } = require('./fixtures/live2d_runtime_fixture.cjs');

function dom(runtime) {
    const roots = [];
    function element(name) {
        return {
            name,
            style: { opacity: '1' },
            parentElement: null,
            attributes: {},
            get isConnected() { return roots.includes(this) || !!this.parentElement?.isConnected; },
            getAttribute(k) { return this.attributes[k]; },
            getBoundingClientRect() { return { left: 100, top: 200, right: 110, bottom: 210, width: 10, height: 10 }; },
            children: [],
            setAttribute(k, v) {
                this.attributes[k] = String(v);
            },
            appendChild(n) {
                this.children.push(n);
                n.parentElement = this;
            },
            remove() {
                const i = roots.indexOf(this);
                if (i >= 0) roots.splice(i, 1);
            }
        };
    }
    runtime.context.document.createElementNS = (_, name) => element(name);
    runtime.context.document.visibilityState = 'visible';
    runtime.context.innerWidth = 1000;
    runtime.context.innerHeight = 1000;
    runtime.context.getComputedStyle = node => ({ visibility: 'visible', ...node.style });
    const container = element('div');
    Object.defineProperty(container, 'isConnected', { value: true });
    container.appendChild = n => { roots.push(n); n.parentElement = container; };
    runtime.context.document.getElementById = id => id === 'live2d-container' ? container : null;
    runtime.context.document.body.appendChild = () => { throw new Error('cue must not mount on body'); };
    return roots;
}
async function fixture(reduced = false) {
    const runtime = await loadRuntime();
    const roots = dom(runtime);
    runtime.context.matchMedia = () => ({ matches: reduced });
    vm.runInContext(read('static/avatar/avatar-expression-performance.js'), runtime.context);
    vm.runInContext(read('static/live2d/live2d-emotion.js'), runtime.context);
    const f = createMao(runtime);
    f.model.worldAlpha = 1;
    Object.setPrototypeOf(f.model.internalModel, EventEmitter.prototype);
    EventEmitter.call(f.model.internalModel);
    f.motionManager.state.currentPriority = 0;
    f.manager.installMouthOverride();
    let rect = { left: 100, top: 200, width: 45, height: 45 };
    f.manager.getHeadScreenRectInfo = () => ({ rect });
    return { ...f, roots, setRect: (r) => (rect = r) };
}

test('observation reads the final pose after later runtime overrides', async () => {
    const f = await fixture();
    try {
        f.manager._installManualExpressionOverride([{ Id: 'ParamAngleZ', Value: 12 }], 50);
        f.manager._applyTemporaryPoseOverride = core => core.setParameterValueById('ParamAngleZ', -7);
        f.runtime.context.clock += 500;
        f.model.internalModel.emit('beforeModelUpdate');
        f.core.update();
        assert.equal(f.manager._manualExpressionResource.observedFrame.parameters.ParamAngleZ, -7);
        assert.equal(f.value('ParamAngleZ'), -7);
    } finally { f.release(); }
});

test('authored performance reaches real Cubism draw, preserves blink, follows face and retires on clear', async () => {
    const f = await fixture();
    const { manager: m, runtime: r, core, raw } = f;
    try {
        m._autoEyeBlinkEnabled = true;
        m._eyeBlinkParams = ['ParamEyeLOpen', 'ParamEyeROpen'].map((id) => ({
            id,
            idx: core.getParameterIndex(id),
            openValue: 1,
            closedValue: 0
        }));
        m._eyeBlinkState = 0;
        m._eyeBlinkTimer = 0;
        m._eyeBlinkNextTime = 100;
        // Exercise the actual file-loading path, not just the performance helper.
        r.context.fetch = async () => ({
            ok: true,
            json: async () => ({
                Type: 'Live2D Expression',
                FadeInTime: 0.1,
                NekoApplyAfterPhysics: true,
                NekoBlinkScale: 1.2,
                Parameters: [{ Id: 'ParamAngleZ', Value: 10 }],
                NekoPerformance: {
                    cue: 'tears',
                    motion: [{ Id: 'ParamAngleZ', Value: 10, Amplitude: 2, Period: 2 }]
                }
            })
        });
        m.resolveAssetPath = (p) => p;
        assert.equal(await m.playExpression('sad', 'test.exp3.json'), true);
        const resource = m._manualExpressionResource;
        const draw = raw.update;
        let rendered;
        raw.update = function () {
            rendered = { angle: f.value('ParamAngleZ'), eye: f.value('ParamEyeLOpen') };
            return draw.call(this);
        };
        r.context.clock += 500;
        f.model.internalModel.emit('beforeModelUpdate');
        core.update();
        assert.ok(Math.abs(rendered.angle - 12) < 0.001);
        assert.ok(Math.abs(rendered.eye - 1.2) < 0.001);
        assert.ok(Math.abs(resource.observedFrame.parameters.ParamAngleZ - rendered.angle) < 0.001);
        assert.equal(resource.performance.snapshot().visibleCue, 'tears');
        r.context.document.visibilityState = 'hidden';
        assert.equal(resource.performance.snapshot().visibleCue, null);
        r.context.document.visibilityState = 'visible';

        assert.equal(f.roots.length, 1);
        assert.equal(f.roots[0].attributes['data-neko-expression-cue'], 'tears');
        const drops = f.roots[0].children;
        assert.equal(drops.length, 6);
        assert.ok(drops.some((n) => Number(n.attributes.opacity) > 0.5));
        const original = drops[0].attributes.transform;
        f.setRect({ left: 350, top: 450, width: 90, height: 90 });
        r.context.clock += 350;
        f.model.internalModel.emit('beforeModelUpdate');
        core.update();
        assert.equal(f.roots[0].style.transform, 'translate(350px, 450px)');
        assert.equal(f.roots[0].style.width, '90px');
        assert.notEqual(drops[0].attributes.transform, original);
        m._eyeBlinkState = 1;
        m._eyeBlinkTimer = 0.079;
        f.model.internalModel.emit('beforeModelUpdate');
        core.update();
        assert.equal(rendered.eye, 0);
        await m.clearExpression();
        assert.equal(f.roots.length, 0);
        assert.equal(resource.performance.snapshot().active, false);
        const after = f.value('ParamAngleZ');
        resource.applyRuntime(core);
        assert.equal(f.value('ParamAngleZ'), after);
        assert.equal(f.roots.length, 0);
    } finally {
        f.release();
    }
});

test('replacement, retirement and model mismatch cannot leave old cues or gestures alive', async () => {
    const f = await fixture();
    const { manager: m, runtime: r, core } = f;
    try {
        const install = (cue) =>
            m._installManualExpressionOverride([{ Id: 'ParamAngleZ', Value: 0 }], 100, {
                performance: { cue, motion: [] }
            });
        install('tears');
        const old = m._manualExpressionResource;
        r.context.clock += 500;
        old.applyRuntime(core);
        assert.equal(f.roots.length, 1);
        install('anger');
        const next = m._manualExpressionResource;
        r.context.clock += 500;
        next.applyRuntime(core);
        assert.equal(f.roots.length, 1);
        assert.equal(f.roots[0].attributes['data-neko-expression-cue'], 'anger');
        old.applyRuntime(core);
        assert.equal(f.roots.length, 1);
        assert.equal(old.performance.snapshot().active, false);
        m._retireManualExpressionResource(next);
        assert.equal(f.roots.length, 0);
        next.applyRuntime(core);
        assert.equal(f.roots.length, 0);
        install('tears');
        const moved = m._manualExpressionResource;
        r.context.clock += 500;
        moved.applyRuntime(core);
        assert.equal(f.roots.length, 1);
        m.currentModel = {};
        moved.performance.apply(core, 1, 2000);
        assert.equal(f.roots.length, 0);
    } finally {
        f.release();
    }
});

test('reduced motion stays stable; invalid or lip/eye motion channels are excluded and values clamped', async () => {
    const f = await fixture(true);
    const { manager: m, runtime: r, core } = f;
    try {
        const performance = r.context.nekoExpressionPerformance.create(m, f.model, core, {
            cue: 'anger',
            motion: [
                null,
                { Id: 'ParamEyeLOpen', Value: 0, Amplitude: 1, Period: 2 },
                { Id: 'ParamMouthOpenY', Value: 1, Amplitude: 1, Period: 2 },
                { Id: 'ParamAngleZ', Value: 1000, Amplitude: 3, Period: 2 },
                { Id: 'ParamAngleX', Value: 0, Amplitude: NaN, Period: 0 }
            ]
        });
        performance.apply(core, 1, 500);
        assert.equal(
            f.value('ParamAngleZ'),
            core.getParameterMaximumValue(core.getParameterIndex('ParamAngleZ'))
        );
        assert.deepEqual(Array.from(performance.snapshot().motionIds), ['ParamAngleZ']);
        const mark = f.roots[0].children.at(-1).attributes.transform;
        performance.apply(core, 1, 5000);
        assert.equal(f.roots[0].children.at(-1).attributes.transform, mark);
        f.model.visible = false;
        performance.apply(core, 1, 6000);
        assert.equal(f.roots[0].style.opacity, '0');
        performance.dispose();
        assert.equal(f.roots.length, 0);
    } finally {
        f.release();
    }
});

for (const cue of ['tears', 'anger']) {
    test(`${cue} inherits container hiding/fading without another render and observations never read geometry`, async () => {
        const f = await fixture();
        try {
            let reads = 0;
            f.manager.getHeadScreenRectInfo = () => { reads++; return { rect: { left: 10, top: 20, width: 50, height: 60 } }; };
            const effect = f.runtime.context.nekoExpressionPerformance.create(f.manager, f.model, f.core, { cue });
            effect.apply(f.core, 1, 500);
            const host = f.runtime.context.document.getElementById('live2d-container');
            assert.equal(f.roots[0].parentElement, host);
            assert.equal(f.roots[0].style.position, 'absolute');
            assert.equal(effect.snapshot().visibleCue, cue);
            for (const style of [{ opacity: '0' }, { display: 'none' }, { visibility: 'hidden' }]) {
                host.style = { opacity: '1', ...style };
                assert.equal(effect.snapshot().visibleCue, null);
            }
            host.style = { opacity: '0.12' };
            effect.apply(f.core, 0.55, 550);
            assert.equal(effect.snapshot().visibleCue, null, 'combined hover fade is below visibility threshold');
            host.style = { opacity: '1' };
            assert.equal(effect.snapshot().visibleCue, cue);
            assert.equal(reads, 1);
            effect.dispose();
        } finally { f.release(); }
    });
}

test('head geometry is sampled at 300ms, including invalid results; animation and snapshots reuse it', async () => {
    const f = await fixture();
    try {
        let reads = 0;
        f.manager.getHeadScreenRectInfo = () => { reads++; return { rect: { left: 10, top: 20, width: 50, height: 60 } }; };
        const effect = f.runtime.context.nekoExpressionPerformance.create(f.manager, f.model, f.core, { cue: 'tears' });
        for (let elapsed = 0; elapsed < 1000; elapsed += 16) {
            effect.apply(f.core, 1, elapsed);
            effect.snapshot();
        }
        assert.equal(reads, 4, 'roughly 4 geometry queries/sec, not 30 or one per observation');
        f.model.x = 15;
        effect.apply(f.core, 1, 1024);
        assert.equal(reads, 5, 'drag refreshes early');
        f.model.scale = { x: 2, y: 2 };
        effect.apply(f.core, 1, 1056);
        assert.equal(reads, 6, 'zoom refreshes early');
        f.manager.getHeadScreenRectInfo = () => { reads++; return { rect: { left: NaN, top: 20, width: 50, height: 60 } }; };
        effect.apply(f.core, 1, 1400);
        assert.equal(f.roots[0].style.opacity, '0');
        effect.apply(f.core, 1, 1440);
        assert.equal(reads, 7, 'invalid bounds are cached too');
        effect.dispose();
    } finally { f.release(); }
});

test('cue uses canvas layout coordinates before container transforms', async () => {
    const f = await fixture();
    try {
        const host = f.runtime.context.document.getElementById('live2d-container');
        f.manager.pixi_app = { view: { parentElement: host, clientWidth: 500, clientHeight: 400, offsetLeft: 30, offsetTop: 40 },
            renderer: { screen: { width: 1000, height: 800 } } };
        const effect = f.runtime.context.nekoExpressionPerformance.create(f.manager, f.model, f.core, { cue: 'anger' });
        effect.apply(f.core, 1, 500);
        assert.equal(f.roots[0].style.transform, 'translate(80px, 140px)');
        assert.equal(f.roots[0].style.width, '22.5px');
        effect.dispose();
    } finally { f.release(); }
});

for (const flag of ['_isEyeDrivenByMotion', '_suspendEyeBlinkOverride', 'disabled']) {
    test(`blink scaling yields to ${flag} and resumes after release`, async () => {
        const f = await fixture();
        try {
            const m = f.manager;
            m._autoEyeBlinkEnabled = flag !== 'disabled';
            if (flag !== 'disabled') m[flag] = true;
            m._installManualExpressionOverride([{ Id: 'ParamAngleZ', Value: 2 }], 50, { blinkScale: 1.5 });
            f.runtime.context.clock += 500;
            for (const id of ['ParamEyeLOpen', 'ParamEyeROpen']) f.core.setParameterValueById(id, 0.4);
            f.core.update();
            for (const id of ['ParamEyeLOpen', 'ParamEyeROpen']) assert.ok(Math.abs(f.value(id) - 0.4) < 1e-6);
            m._autoEyeBlinkEnabled = true;
            m._isEyeDrivenByMotion = m._suspendEyeBlinkOverride = false;
            m._manualExpressionResource.applyRuntime(f.core);
            for (const id of ['ParamEyeLOpen', 'ParamEyeROpen']) assert.ok(Math.abs(f.value(id) - 0.6) < 1e-6);
        } finally { f.release(); }
    });
}

for (const failure of ['create', 'geometry', 'motion', 'cleanup']) {
    test(`optional ${failure} failure preserves final pose/observation and does not retry`, async () => {
        const f = await fixture();
        try {
            let calls = 0;
            const fail = () => { calls++; throw new Error('optional failure'); };
            const api = f.runtime.context.nekoExpressionPerformance;
            if (failure === 'create') api.create = fail;
            else if (failure === 'motion') f.core.getParameterMinimumValue = fail;
            else if (failure === 'cleanup') api.create = () => ({ apply: fail, dispose: () => { throw new Error('cleanup failure'); } });
            assert.equal(f.manager._installManualExpressionOverride([{ Id: 'ParamAngleZ', Value: 2 }], 50, {
                performance: { cue: 'anger', motion: [{ Id: 'ParamAngleZ', Value: 10, Amplitude: 1, Period: 2 }] }
            }), true);
            f.manager._applyTemporaryPoseOverride = core => core.setParameterValueById('ParamAngleZ', -7);
            const resource = f.manager._manualExpressionResource;
            if (failure === 'geometry') {
                f.runtime.context.clock += 500;
                f.core.update();
                assert.equal(f.roots.length, 1, 'failure cleanup must remove an already mounted cue');
                f.manager.getHeadScreenRectInfo = fail;
            }
            for (let i = 0; i < 3; i++) {
                f.runtime.context.clock += 500;
                f.core.update();
                assert.equal(f.value('ParamAngleZ'), -7);
                assert.equal(resource.observedFrame.parameters.ParamAngleZ, -7);
                assert.equal(resource.appliedWeight, 1);
            }
            assert.equal(calls, 1);
            assert.equal(resource.performance, null);
            assert.equal(f.roots.length, 0);
            assert.equal(f.runtime.warnings.filter(args => String(args[0]).includes('Optional performance')).length, 1);
        } finally { f.release(); }
    });
}

for (const [label, duration, override, expected] of [
    ['missing', undefined, undefined, 300], ['null', null, undefined, 300],
    ['string', 'bad', undefined, 300], ['NaN', NaN, undefined, 300], ['infinite', Infinity, undefined, 300],
    ['zero', 0, undefined, 300], ['negative', -1, undefined, 300],
    ['valid', 0.6, undefined, 600], ['minimum', 0.001, undefined, 50], ['maximum', 9, undefined, 5000],
    ['caller wins', 0.6, 400, 400], ['invalid caller', 0.6, -1, 300]
]) {
    test(`manual profile fade: ${label}`, async () => {
        const f = await fixture();
        try {
            f.runtime.context.fetch = async () => ({ ok: true, json: async () => ({
                NekoApplyAfterPhysics: true, FadeInTime: duration, Parameters: [{ Id: 'ParamAngleZ', Value: 10 }]
            }) });
            f.manager.resolveAssetPath = p => p;
            assert.equal(await f.manager.playExpression('test', 'test.exp3.json', { fadeInDuration: override }), true);
            f.runtime.context.clock += expected / 2;
            f.manager._manualExpressionResource.applyRuntime(f.core);
            assert.ok(Math.abs(f.manager._manualExpressionResource.appliedWeight - 0.5) < 1e-6);
        } finally { f.release(); }
    });
}
