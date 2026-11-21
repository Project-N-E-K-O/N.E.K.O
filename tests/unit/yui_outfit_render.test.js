const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');
const { EventEmitter } = require('node:events');
const { loadRuntime, read } = require('./fixtures/live2d_runtime_fixture.cjs');

const repo = path.resolve(__dirname, '../..');
const profileRoot = path.join(repo, 'local-desktop-ui/models/yui-common');
const profiles = fs.readdirSync(profileRoot).filter(name => name.endsWith('.exp3.json'));

for (const name of ['yui-origin', 'yui-lolita']) {
    test(`${name}: all shared profiles reach real Core rendering without sleep or stuck hands`, async () => {
        const runtime = await loadRuntime();
        vm.runInContext(read('static/avatar/avatar-expression-performance.js'), runtime.context);
        vm.runInContext(read('static/live2d/live2d-emotion.js'), runtime.context);
        // Tracked official archives, real MOC/Core; no private models or test-only parameter table.
        const bytes = execFileSync('tar', ['-xOf', path.join(repo, 'assets', `${name}.tar.gz`), `${name}/${name}.moc3`],
            { maxBuffer: 16 * 1024 * 1024 });
        const moc = runtime.context.Live2DCubismCore.Moc.fromArrayBuffer(
            bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
        const raw = runtime.context.Live2DCubismCore.Model.fromMoc(moc);
        const core = new runtime.sdk.Model(raw);
        const internalModel = new EventEmitter();
        Object.assign(internalModel, { coreModel: core, motionManager: {
            state: { currentPriority: 0 }, update() {}, stopAllMotions() {}
        }});
        const model = { internalModel, deltaTime: 1000 / 30, x: 0, y: 0, scale: { x: 1, y: 1 } };
        const manager = new runtime.context.Live2DManager();
        Object.assign(manager, { currentModel: model, modelName: name, initialParameters: {},
            _mouseTrackingEnabled: true });
        // DOM accents are covered separately; exercise the model's final draw here.
        manager.getHeadScreenRectInfo = () => null;
        manager.installMouthOverride();
        manager._autoEyeBlinkEnabled = true;
        manager._eyeBlinkParams = ['ParamEyeLOpen', 'ParamEyeROpen'].map(id => ({
            id, idx: core.getParameterIndex(id), openValue: 1, closedValue: 0
        }));
        const draw = raw.update;
        let rendered;
        raw.update = function () {
            rendered = Object.fromEntries(Array.from(raw.parameters.ids).map((id, index) =>
                [id, raw.parameters.values[index]]));
            return draw.call(this);
        };
        try {
            for (const file of profiles) {
                const profile = JSON.parse(fs.readFileSync(path.join(profileRoot, file)));
                for (const param of profile.Parameters) {
                    const index = core.getParameterIndex(param.Id);
                    assert.ok(index >= 0 && index < core.getParameterCount(), `${file}: missing ${param.Id}`);
                    assert.ok(param.Value >= core.getParameterMinimumValue(index)
                        && param.Value <= core.getParameterMaximumValue(index), `${file}: range ${param.Id}`);
                }
                assert.equal(manager._installManualExpressionOverride(profile.Parameters, 100, {
                    blinkScale: profile.NekoBlinkScale, mouthRest: profile.NekoMouthRest,
                    performance: profile.NekoPerformance
                }), true);
                manager._eyeBlinkState = 0;
                manager._eyeBlinkTimer = 0;
                manager._eyeBlinkNextTime = 100;
                runtime.context.clock += 1000;
                internalModel.emit('beforeModelUpdate');
                core.update();
                for (const id of ['Param74', 'Param75', 'Param76', 'Param77', 'Param86']) {
                    const expected = profile.Parameters.find(param => param.Id === id)?.Value;
                    assert.equal(rendered[id], expected, `${file}: final ${id}`);
                }
                assert.ok(rendered.ParamEyeLOpen >= 0.8, `${file}: awake`);
                manager._eyeBlinkState = 1;
                manager._eyeBlinkTimer = 0.079;
                internalModel.emit('beforeModelUpdate');
                core.update();
                assert.equal(rendered.ParamEyeLOpen, 0, `${file}: natural blink still closes`);
            }
            manager._removeManualExpressionOverride();
            assert.equal(manager._manualExpressionResource, null);
        } finally {
            core.release();
            moc._release();
        }
    });
}
