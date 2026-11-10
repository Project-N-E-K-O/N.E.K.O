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
            style: {},
            attributes: {},
            get isConnected() { return roots.includes(this); },
            getAttribute(k) { return this.attributes[k]; },
            getBoundingClientRect() { return { left: 100, top: 200, right: 110, bottom: 210, width: 10, height: 10 }; },
            children: [],
            setAttribute(k, v) {
                this.attributes[k] = String(v);
            },
            appendChild(n) {
                this.children.push(n);
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
    runtime.context.document.body.appendChild = (n) => roots.push(n);
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
        f.setRect({ left: -100, top: -100, width: 45, height: 45 });
        assert.equal(resource.performance.snapshot().visibleCue, null);
        f.setRect({ left: 100, top: 200, width: 45, height: 45 });
        assert.equal(f.roots.length, 1);
        assert.equal(f.roots[0].attributes['data-neko-expression-cue'], 'tears');
        const drops = f.roots[0].children;
        assert.equal(drops.length, 6);
        assert.ok(drops.some((n) => Number(n.attributes.opacity) > 0.5));
        const original = drops[0].attributes.transform;
        f.setRect({ left: 350, top: 450, width: 90, height: 90 });
        r.context.clock += 200;
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
