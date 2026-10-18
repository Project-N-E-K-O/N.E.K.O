const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

const projectRoot = path.resolve(__dirname, '..', '..', '..');
const read = relative => fs.readFileSync(path.join(projectRoot, relative), 'utf8');

// Export only the real bundled SDK constructors needed by the tests. The
// parser, motion queue, physics, model wrapper and Core are not mocked.
async function loadRuntime({ transformModelSource = source => source } = {}) {
    const warnings = [];
    const timers = [];
    const context = {
        console: { log() {}, warn(...args) { warnings.push(args); }, error() {},
            debug() {}, info() {}, groupCollapsed() {}, groupEnd() {},
            assert(value, message) { assert.ok(value, message); } },
        setTimeout, clearTimeout, setInterval, clearInterval, performance,
        TextDecoder, TextEncoder, atob, btoa, AbortController,
        document: { currentScript: { src: 'http://localhost/static/libs/live2dcubismcore.min.js' },
            body: { classList: { contains() { return false; } } }, getElementById() { return null; } },
        navigator: { userAgent: 'node' }, addEventListener() {}, removeEventListener() {},
        dispatchEvent() {}, location: { pathname: '/', origin: 'http://localhost' },
        innerWidth: 1000, innerHeight: 800, screen: { width: 1000, height: 800 },
        PIXI: { utils: { EventEmitter }, Transform: class {}, Container: class extends EventEmitter {},
            Point: class {}, Matrix: class {}, ObservablePoint: class {} },
        PhysicsHair: { Src: { SRC_TO_X: 0, SRC_TO_Y: 1, SRC_TO_G_ANGLE: 2 } },
        Live2D: {}, Live2DMotion: class { updateParam() {} }, AMotion: class {},
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        fetch: async () => { throw new Error('unexpected network request'); }
    };
    context.window = context;
    context.self = context;
    vm.createContext(context);
    vm.runInContext(read('static/libs/live2dcubismcore.min.js'), context,
        { filename: path.join(projectRoot, 'static/libs/live2dcubismcore.min.js') });
    await new Promise(resolve => setImmediate(resolve));
    const sdkSource = read('static/libs/index.min.js');
    const marker = 't.VERSION="v0.5.0-ls-6"';
    assert.equal(sdkSource.split(marker).length, 2, 'review test exports when updating the SDK');
    vm.runInContext(sdkSource.replace(marker,
        't.__savedParameterAudit={Model:je,Motion:ae,Queue:Gt,Physics:ti},' + marker), context,
        { filename: path.join(projectRoot, 'static/libs/index.min.js') });
    vm.runInContext(read('static/live2d/live2d-core.js'), context,
        { filename: path.join(projectRoot, 'static/live2d/live2d-core.js') });
    const modelSource = read('static/live2d/live2d-model.js');
    const evaluatedModelSource = transformModelSource(modelSource);
    vm.runInContext(evaluatedModelSource, context, { filename: path.join(projectRoot,
        evaluatedModelSource === modelSource ? 'static/live2d/live2d-model.js' : 'live2d-model.mutation.js') });
    context.clock = 100;
    context.performance = { now() { return context.clock; } };
    context.setTimeout = (callback, delay) => {
        const handle = { callback, delay, cancelled: false };
        timers.push(handle);
        return handle;
    };
    context.clearTimeout = handle => { if (handle) handle.cancelled = true; };
    context.requestAnimationFrame = callback => { callback(context.clock); return 1; };
    return { context, sdk: context.PIXI.live2d.__savedParameterAudit, warnings, timers };
}

function createMao(runtime) {
    const root = path.join(projectRoot, 'static/mao_pro');
    const settings = JSON.parse(fs.readFileSync(path.join(root, 'mao_pro.model3.json'), 'utf8'));
    const bytes = fs.readFileSync(path.join(root, settings.FileReferences.Moc));
    const moc = runtime.context.Live2DCubismCore.Moc.fromArrayBuffer(
        bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    assert.ok(moc, 'tracked Mao MOC must load');
    const raw = runtime.context.Live2DCubismCore.Model.fromMoc(moc);
    const core = new runtime.sdk.Model(raw);
    const fixture = { runtime, root, settings, raw, core, moc,
        defaultReads: 0, motionCalls: 0, coreCalls: 0, writes: {}, defaultOverrides: new Map() };
    const getDefault = core.getParameterDefaultValue.bind(core);
    core.getParameterDefaultValue = index => {
        fixture.defaultReads++;
        return fixture.defaultOverrides.has(index) ? fixture.defaultOverrides.get(index) : getDefault(index);
    };
    const updateCore = core.update.bind(core);
    core.update = () => { fixture.coreCalls++; updateCore(); };
    fixture.motionManager = {
        state: { currentPriority: 2 },
        update(time) {
            fixture.motionCalls++;
            if (fixture.queue) fixture.queue.doUpdateMotion(core, time);
            else for (const [id, value] of Object.entries(fixture.writes)) core.setParameterValueById(id, value);
            if (fixture.afterMotion) fixture.afterMotion();
        },
        stopAllMotions() { fixture.queue?.stopAllMotions(); }
    };
    fixture.model = { internalModel: { coreModel: core, motionManager: fixture.motionManager },
        deltaTime: 1000 / 30, x: 0, y: 0, scale: { x: 1, y: 1 },
        destroy() { fixture.destroyCalled = true; } };
    fixture.manager = new runtime.context.Live2DManager();
    Object.assign(fixture.manager, { currentModel: fixture.model, modelName: 'mao_pro',
        _lastLoadedModelPath: '/static/mao_pro/mao_pro.model3.json',
        _mouseTrackingEnabled: true, initialParameters: {} });
    fixture.apply = saved => fixture.manager._applyEffectiveModelParameters(fixture.model, {}, saved);
    fixture.value = id => core.getParameterValueByIndex(core.getParameterIndex(id));
    fixture.preferences = parameters => [{ model_path: fixture.manager._lastLoadedModelPath, parameters }];
    fixture.release = () => { fixture.queue?.stopAllMotions(); core.release(); moc._release(); };
    return fixture;
}

function deferred() {
    let resolve, reject;
    const promise = new Promise((accept, fail) => { resolve = accept; reject = fail; });
    return { promise, resolve, reject };
}

function installAvatarOperationRuntime(context) {
    const source = read('static/live2d/live2d-core.js');
    const start = source.indexOf('// Avatar operation lifecycle');
    const end = source.indexOf('// End avatar operation lifecycle', start);
    assert.ok(start >= 0 && end > start, 'operation runtime source markers');
    vm.runInContext(source.slice(start, end), context, {
        filename: path.join(projectRoot, 'fixtures/avatar-operation-runtime.js')
    });
}

function compareModels(actual, reference, label) {
    const equal = (a, b, name) => assert.ok(Buffer.from(a.buffer, a.byteOffset, a.byteLength).equals(
        Buffer.from(b.buffer, b.byteOffset, b.byteLength)), `${label}: ${name}`);
    equal(actual.raw.parameters.values, reference.raw.parameters.values, 'parameters');
    equal(actual.raw.parts.opacities, reference.raw.parts.opacities, 'parts');
    equal(actual.raw.drawables.opacities, reference.raw.drawables.opacities, 'drawables');
    for (let index = 0; index < actual.raw.drawables.count; index++) {
        equal(actual.raw.drawables.vertexPositions[index], reference.raw.drawables.vertexPositions[index],
            `vertices ${index}`);
    }
}

module.exports = { read, loadRuntime, createMao, deferred, compareModels, installAvatarOperationRuntime };
