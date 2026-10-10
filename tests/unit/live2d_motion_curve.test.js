const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

const projectRoot = path.resolve(__dirname, '..', '..');
const sdkSource = fs.readFileSync(path.join(projectRoot, 'static/libs/index.min.js'), 'utf8');
const coreSource = fs.readFileSync(path.join(projectRoot, 'static/libs/live2dcubismcore.min.js'), 'utf8');
const boundedSlice = 't.points.slice(a.basePointIndex,a.basePointIndex+4)';
const originalSlice = 't.points.slice(a.basePointIndex)';

// Vendor patch contract: pixi-live2d-display v0.5.0-ls-6, function ne.
// ee/re/oe read points[0..1]; restricted ie and unrestricted se read [0..3].
// Recheck these readers when upgrading the vendor. Only test exports are injected;
// the evaluators, motion parser/queue, physics and Cubism Core stay unchanged.
async function loadSdk({ original = false } = {}) {
    assert.equal(sdkSource.split(boundedSlice).length - 1, 1, 'expected one bounded vendor slice');
    const quiet = { log() {}, warn() {}, error() {}, debug() {}, info() {},
        assert(value, message) { assert.ok(value, message); } };
    const context = {
        console: quiet, setTimeout, clearTimeout, performance, TextDecoder, TextEncoder, atob, btoa,
        document: { currentScript: { src: 'http://localhost/static/libs/live2dcubismcore.min.js' } },
        navigator: { userAgent: 'node' }, addEventListener() {},
        PIXI: { utils: { EventEmitter }, Transform: class {}, Container: class extends EventEmitter {},
            Point: class {}, Matrix: class {}, ObservablePoint: class {} },
        PhysicsHair: { Src: { SRC_TO_X: 0, SRC_TO_Y: 1, SRC_TO_G_ANGLE: 2 } },
        Live2D: {}, Live2DMotion: class { updateParam() {} }, AMotion: class {}
    };
    context.window = context;
    context.self = context;
    vm.createContext(context);
    vm.runInContext(coreSource, context, { filename: 'live2dcubismcore.min.js' });
    await new Promise(resolve => setImmediate(resolve));
    let source = original ? sdkSource.replace(boundedSlice, originalSlice) : sdkSource;
    const marker = 't.VERSION="v0.5.0-ls-6"';
    assert.equal(source.split(marker).length - 1, 1, 'vendor version changed: review test exports');
    source = source.replace(marker,
        't.__motionCurveAudit={evaluate:ne,evaluators:[ee,ie,se,re,oe],Motion:ae,Queue:Gt,Model:je,Physics:ti},' + marker);
    vm.runInContext(source, context, { filename: 'index.min.js' });
    return { context, sdk: context.PIXI.live2d.__motionCurveAudit, core: context.Live2DCubismCore };
}

const sdkPair = Promise.all([loadSdk(), loadSdk({ original: true })]);

function motionJson(curves, { restricted = true, duration = 1, events = [] } = {}) {
    let segments = 0;
    let points = 0;
    for (const curve of curves) {
        points++;
        for (let offset = 2; offset < curve.Segments.length;) {
            const bezier = curve.Segments[offset] === 1;
            segments++;
            points += bezier ? 3 : 1;
            offset += bezier ? 7 : 3;
        }
    }
    return { Version: 3, Meta: { Duration: duration, Loop: false, Fps: 30,
        AreBeziersRestricted: restricted, CurveCount: curves.length,
        TotalSegmentCount: segments, TotalPointCount: points, FadeInTime: 0.2, FadeOutTime: 0.2,
        UserDataCount: events.length, TotalUserDataSize: events.reduce((n, event) => n + event.Value.length, 0) },
    Curves: curves, UserData: events };
}

function syntheticMotion(count = 12, options = {}) {
    const curves = Array.from({ length: count }, (_, index) => {
        const kind = index % 4;
        return { Target: 'Parameter', Id: index === 0 ? 'ParamAngleX' : `AuditParam${index}`,
            Segments: kind === 1 ? [0, -1, 1, 0.1, 2, 0.9, -2, 1, 1]
                : [0, -1, kind, 0.5, 1, kind, 1, -1] };
    });
    return motionJson(curves, options);
}

function createMotion(runtime, json, { loop = false, finished = undefined } = {}) {
    const motion = runtime.sdk.Motion.create(json, finished);
    motion.setEffectIds([], []);
    motion.setIsLoop(loop);
    return motion;
}

function countCopies(motion) {
    const points = motion._motionData.points;
    const slice = points.slice;
    const stats = { calls: 0, references: 0, max: 0 };
    points.slice = function(...args) {
        const copied = slice.apply(this, args);
        stats.calls++;
        stats.references += copied.length;
        stats.max = Math.max(stats.max, copied.length);
        return copied;
    };
    return stats;
}

function assertBoundedCopies(stats) {
    assert.ok(stats.calls > 0, 'must reach the real curve-copy path');
    assert.ok(stats.max <= 4, `curve copied ${stats.max} references; maximum is 4`);
}

function cubic(values, amount) {
    const inverse = 1 - amount;
    return inverse ** 3 * values[0] + 3 * inverse ** 2 * amount * values[1]
        + 3 * inverse * amount ** 2 * values[2] + amount ** 3 * values[3];
}

test('five actual evaluators agree with independent interpolation references', async () => {
    const [bounded, original] = await sdkPair;
    assert.notEqual(bounded.context, original.context);
    assert.notEqual(bounded.sdk.Motion, original.sdk.Motion, 'independent SDK exports must survive both loads');
    assert.notEqual(bounded.sdk.evaluate, original.sdk.evaluate);
    const points = [{ time: 0, value: -2 }, { time: 0.1, value: 5 },
        { time: 0.9, value: -4 }, { time: 1, value: 3 }];
    for (const time of [0, 0.0000001, 0.125, 0.5, 0.875, 0.9999999, 1]) {
        let low = 0;
        let high = 1;
        for (let iteration = 0; iteration < 80; iteration++) {
            const middle = (low + high) / 2;
            if (cubic(points.map(point => point.time), middle) < time) low = middle;
            else high = middle;
        }
        const expected = [
            points[0].value + (points[1].value - points[0].value) * (time / points[1].time),
            cubic(points.map(point => point.value), time),
            cubic(points.map(point => point.value), (low + high) / 2),
            points[0].value, points[1].value
        ];
        for (let kind = 0; kind < 5; kind++) {
            const result = bounded.sdk.evaluators[kind](points, time);
            assert.ok(Math.abs(result - expected[kind]) < 1e-7, `evaluator ${kind}, t=${time}`);
            assert.equal(result, original.sdk.evaluators[kind](points, time));
        }
    }
});

test('shared point arrays retain exact segment and motion boundary results', async () => {
    const [bounded, original] = await sdkPair;
    for (const restricted of [true, false]) {
        const json = syntheticMotion(24, { restricted });
        const actual = createMotion(bounded, json);
        const reference = createMotion(original, json);
        const stats = countCopies(actual);
        const evaluatorKinds = new Set(actual._motionData.segments.map(segment => segment.evaluate));
        assert.equal(evaluatorKinds.size, 4, 'all four segment types must be parsed');
        for (let curve = 0; curve < json.Curves.length; curve++) {
            for (const time of [-1, -1e-7, 0, 1e-7, 0.1, 0.5 - 1e-7, 0.5,
                0.5 + 1e-7, 0.9, 1 - 1e-7, 1, 1 + 1e-7, 2]) {
                const value = bounded.sdk.evaluate(actual._motionData, curve, time);
                assert.ok(Number.isFinite(value), `finite curve ${curve}, t=${time}`);
                assert.equal(value, original.sdk.evaluate(reference._motionData, curve, time),
                    `curve ${curve}, t=${time}`);
            }
        }
        assertBoundedCopies(stats);
    }
});

test('large motion copies at most four references per curve; original mutation fails the guard', async () => {
    const [bounded, original] = await sdkPair;
    const json = syntheticMotion(512);
    const actual = createMotion(bounded, json);
    const reference = createMotion(original, json);
    const actualStats = countCopies(actual);
    const originalStats = countCopies(reference);
    for (let curve = 0; curve < json.Curves.length; curve++) {
        assert.equal(bounded.sdk.evaluate(actual._motionData, curve, 0.25),
            original.sdk.evaluate(reference._motionData, curve, 0.25));
    }
    assert.equal(actualStats.calls, json.Curves.length);
    assertBoundedCopies(actualStats);
    assert.throws(() => assertBoundedCopies(originalStats), /maximum is 4/);
    assert.ok(originalStats.references > actualStats.references * 100,
        'reference VM must really execute the unbounded path');
});

function createMao(runtime) {
    const root = path.join(projectRoot, 'static/mao_pro');
    const settings = JSON.parse(fs.readFileSync(path.join(root, 'mao_pro.model3.json'), 'utf8'));
    const bytes = fs.readFileSync(path.join(root, settings.FileReferences.Moc));
    const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    const moc = runtime.core.Moc.fromArrayBuffer(buffer);
    assert.ok(moc, 'tracked Mao MOC must load');
    const raw = runtime.core.Model.fromMoc(moc);
    assert.ok(raw);
    const model = new runtime.sdk.Model(raw);
    const physics = runtime.sdk.Physics.create(JSON.parse(fs.readFileSync(
        path.join(root, settings.FileReferences.Physics), 'utf8')));
    const queue = new runtime.sdk.Queue();
    const events = [];
    queue.setEventCallback((manager, value) => events.push(value));
    return { model, raw, moc, physics, queue, events, root, settings,
        release() { queue.stopAllMotions(); model.release(); moc._release(); } };
}

function valuesEqual(actual, reference, label) {
    assert.equal(actual.length, reference.length, `${label} length`);
    assert.ok(Buffer.from(actual.buffer, actual.byteOffset, actual.byteLength).equals(
        Buffer.from(reference.buffer, reference.byteOffset, reference.byteLength)), label);
}

function compareModels(actual, reference, frame) {
    valuesEqual(actual.raw.parameters.values, reference.raw.parameters.values, `parameters frame ${frame}`);
    valuesEqual(actual.raw.parts.opacities, reference.raw.parts.opacities, `parts frame ${frame}`);
    valuesEqual(actual.raw.drawables.opacities, reference.raw.drawables.opacities, `drawables frame ${frame}`);
    for (let drawable = 0; drawable < actual.raw.drawables.count; drawable++) {
        valuesEqual(actual.raw.drawables.vertexPositions[drawable], reference.raw.drawables.vertexPositions[drawable],
            `vertices frame ${frame}, drawable ${drawable}`);
    }
}

function update(model, seconds) {
    const active = model.queue.doUpdateMotion(model.model, seconds);
    model.model.saveParameters();
    model.physics.evaluate(model.model, 1 / 30);
    model.model.update();
    return active;
}

function queueSnapshot(queue) {
    return Array.from(queue._motions, entry => ({ started: entry.isStarted(), finished: entry.isFinished(),
        start: entry.getStartTime(), end: entry.getEndTime(), fade: entry.getFadeInStartTime(),
        lastEvent: entry.getLastCheckEventSeconds(), fadeOut: entry.isTriggeredFadeOut() }));
}

test('real Mao Core preserves loops, cross-fades, events, natural finish and stop/restart', async () => {
    const runtimes = await sdkPair;
    const models = runtimes.map(createMao);
    const completions = [[], []];
    const copyStats = [];
    let sawCrossFade = false;
    let sawLoopWrap = false;
    let sawNaturalFinish = false;
    const partValues = new Set();
    const angleValues = new Set();
    const angleIndex = models[0].model.getParameterIndex('ParamAngleX');
    const opacityFlags = runtimes.map(runtime => runtime.context.PIXI.live2d.config.cubism4.setOpacityFromMotion);
    runtimes.forEach(runtime => { runtime.context.PIXI.live2d.config.cubism4.setOpacityFromMotion = true; });
    const json = syntheticMotion(12, { events: [{ Time: 0.25, Value: 'quarter' }] });
    // Exercise a real part-opacity curve too, so Core output comparison includes it.
    json.Curves.push({ Target: 'PartOpacity', Id: models[0].raw.parts.ids[0], Segments: [0, 0.2, 0, 1, 0.8] });
    json.Meta.CurveCount++;
    json.Meta.TotalSegmentCount++;
    json.Meta.TotalPointCount += 2;
    try {
        for (let frame = 0; frame < 300; frame++) {
            for (let index = 0; index < models.length; index++) {
                const fixture = models[index];
                if ([0, 60, 90, 145, 200].includes(frame)) {
                    const loop = frame !== 90;
                    const motion = createMotion(runtimes[index], json, {
                        loop, finished: () => completions[index].push(frame)
                    });
                    if (index === 0) copyStats.push(countCopies(motion));
                    fixture.queue.startMotion(motion, false, frame / 30);
                }
                if (frame === 170) fixture.queue.stopAllMotions();
            }
            const active = models.map(model => update(model, frame / 30));
            assert.equal(active[0], active[1], `active frame ${frame}`);
            assert.deepEqual(queueSnapshot(models[0].queue), queueSnapshot(models[1].queue));
            assert.deepEqual(models[0].events, models[1].events);
            assert.deepEqual(completions[0], completions[1]);
            compareModels(models[0], models[1], frame);
            partValues.add(models[0].raw.parts.opacities[0]);
            angleValues.add(models[0].raw.parameters.values[angleIndex]);
            for (const model of models) model.model.loadParameters();
            if (frame === 40) sawLoopWrap = models[0].queue._motions[0].getStartTime() > 0;
            if (frame === 61) sawCrossFade = models[0].queue._motions.length === 2;
            if (frame === 130) sawNaturalFinish = models[0].queue.isFinished();
            if (frame === 170 || frame === 199) assert.equal(active[0], false, 'stopped queue stays idle');
            if (frame === 200) assert.equal(active[0], true, 'restart must update');
        }
        assert.ok(sawLoopWrap, 'loop must pass its duration');
        assert.ok(sawCrossFade, 'two real queued motions must overlap');
        assert.ok(sawNaturalFinish, 'one-shot must naturally leave the queue');
        assert.ok(completions[0].length > 0, 'finished callback must fire');
        assert.ok(models[0].events.includes('quarter'), 'motion event must fire');
        assert.ok(partValues.size > 10, 'actual part-opacity curve must change real Core parts');
        assert.ok(angleValues.size > 10, 'actual parameter curve must change real Core parameters');
        for (const stats of copyStats) assertBoundedCopies(stats);
    } finally {
        models.forEach(model => model.release());
        runtimes.forEach((runtime, index) => {
            runtime.context.PIXI.live2d.config.cubism4.setOpacityFromMotion = opacityFlags[index];
        });
    }
});

test('every registered tracked Mao motion preserves parameters, parts, opacity and vertices', async () => {
    const runtimes = await sdkPair;
    const models = runtimes.map(createMao);
    const files = [...new Set(Object.values(models[0].settings.FileReferences.Motions)
        .flat().map(motion => motion.File))];
    assert.ok(files.length >= 7, 'registered Mao motion fixture unexpectedly shrank');
    let frame = 0;
    try {
        for (const file of files) {
            const json = JSON.parse(fs.readFileSync(path.join(models[0].root, file), 'utf8'));
            const motions = runtimes.map(runtime => createMotion(runtime, json, { loop: true }));
            const stats = countCopies(motions[0]);
            models.forEach((model, index) => {
                model.queue.stopAllMotions();
                model.queue.startMotion(motions[index], false, frame / 30);
            });
            // Cover the full authored duration, including its end and one loop wrap.
            const frames = Math.ceil(json.Meta.Duration * 30) + 2;
            for (let localFrame = 0; localFrame < frames; localFrame++, frame++) {
                assert.equal(update(models[0], frame / 30), update(models[1], frame / 30));
                compareModels(models[0], models[1], frame);
                models.forEach(model => model.model.loadParameters());
            }
            assertBoundedCopies(stats);
        }
    } finally {
        models.forEach(model => model.release());
    }
});
