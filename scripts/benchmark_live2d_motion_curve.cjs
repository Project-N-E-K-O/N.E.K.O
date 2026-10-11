#!/usr/bin/env node
'use strict';

/**
 * CPU-only benchmark for pixi-live2d-display v0.5.0-ls-6's curve slice fix.
 * Current evaluators consume at most points[0..3]; production caps the slice at
 * basePointIndex + 4. The original baseline is reconstructed ONLY in a VM string.
 * SDK aliases below expose real private implementations without changing files.
 *
 * node scripts/benchmark_live2d_motion_curve.cjs
 * node scripts/benchmark_live2d_motion_curve.cjs --models-root C:/path/N.E.K.O/static --models mao_pro,yui-lolita,yui-origin
 *
 * 30 fps is a simulated timestep, not a scheduler or measured rendering fps.
 * Includes real motion queue, breath, blink, physics, pose, Core and app parameter
 * wrappers, with stationary mouse focus, no saved parameters, expression or audio.
 * Excludes texture loading, WebGL/Pixi rendering, DOM and Electron compositing.
 * Correctness/copy checks are separate from timing; timing uses isolated child
 * processes in original / bounded / bounded / original order, each warmed anew.
 * Missing explicitly requested models are errors, never silent skips.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const os = require('node:os');
const { EventEmitter } = require('node:events');
const { spawnSync } = require('node:child_process');
const { performance, PerformanceObserver } = require('node:perf_hooks');

const ROOT = path.resolve(__dirname, '..');
const ORIGINAL = 't.points.slice(a.basePointIndex)';
const BOUNDED = 't.points.slice(a.basePointIndex,a.basePointIndex+4)';
const EXPORT_ANCHOR = 't.VERSION="v0.5.0-ls-6"';
const EXPORTS = 't.__bench={Model:je,Motion:ae,Queue:Gt,Physics:ti,Internal:Fe,Focus:C,Breath:he,BreathParameter:ue,EyeBlink:ce,Pose:Ue},';

function options(argv) {
    const opts = { modelsRoot: path.join(ROOT, 'static'), models: ['mao_pro'], warmup: 600, frames: 1800, fps: 30 };
    const names = { '--models-root': 'modelsRoot', '--models': 'models', '--warmup': 'warmup', '--frames': 'frames', '--fps': 'fps', '--worker': 'worker', '--variant': 'variant' };
    for (let i = 0; i < argv.length; i++) {
        if (argv[i] === '--help') {
            console.log('Usage: node scripts/benchmark_live2d_motion_curve.cjs [--models-root DIRECTORY] [--models mao_pro,yui-lolita,yui-origin] [--warmup 600] [--frames 1800] [--fps 30]');
            process.exit(0);
        }
        const key = names[argv[i]];
        if (!key || !argv[i + 1] || argv[i + 1].startsWith('--')) throw Error(`Unknown or incomplete option: ${argv[i]}`);
        opts[key] = argv[++i];
    }
    opts.modelsRoot = path.resolve(opts.modelsRoot);
    opts.models = typeof opts.models === 'string' ? opts.models.split(',') : opts.models;
    for (const key of ['warmup', 'frames', 'fps']) {
        opts[key] = Number(opts[key]);
        if (!Number.isSafeInteger(opts[key]) || opts[key] < (key === 'warmup' ? 0 : 1)) throw Error(`Invalid ${key}`);
    }
    if (opts.models.some(name => !name || path.basename(name) !== name || name === '.' || name === '..')) throw Error('Model names must be directory basenames');
    return opts;
}

function json(file) { return JSON.parse(fs.readFileSync(file, 'utf8')); }
function replaceOnce(source, from, to) {
    assert.equal(source.split(from).length - 1, 1, `Expected exactly one SDK anchor: ${from}`);
    return source.replace(from, to);
}

async function runtime(variant) {
    const diagnostics = { warnings: [], errors: [] };
    const quiet = { log() {}, info() {}, debug() {}, warn(...args) { diagnostics.warnings.push(args.map(String).join(' ')); }, error(...args) { diagnostics.errors.push(args.map(String).join(' ')); }, assert(value, message) { assert.ok(value, message); } };
    const ctx = {
        console: quiet, setTimeout, clearTimeout, performance, TextDecoder, TextEncoder, atob, btoa,
        document: { currentScript: { src: 'http://localhost/static/libs/live2dcubismcore.min.js' } },
        navigator: { userAgent: 'node-cpu-benchmark' }, addEventListener() {}, removeEventListener() {},
    };
    ctx.window = ctx;
    ctx.self = ctx;
    ctx.PIXI = { utils: { EventEmitter }, Transform: class {}, Container: class extends EventEmitter {}, Point: class {}, Matrix: class {}, ObservablePoint: class {} };
    ctx.PhysicsHair = { Src: { SRC_TO_X: 0, SRC_TO_Y: 1, SRC_TO_G_ANGLE: 2 } };
    ctx.Live2D = {};
    ctx.Live2DMotion = class { updateParam() {} };
    ctx.AMotion = class {};
    vm.createContext(ctx);
    vm.runInContext('Math.random = () => 0.5', ctx); // Reproducible blink timing in each independent realm.
    vm.runInContext(fs.readFileSync(path.join(ROOT, 'static/libs/live2dcubismcore.min.js'), 'utf8'), ctx);
    await new Promise(resolve => setImmediate(resolve));
    let sdk = fs.readFileSync(path.join(ROOT, 'static/libs/index.min.js'), 'utf8');
    assert.equal(sdk.includes(ORIGINAL), false, 'Benchmark requires the patched production SDK');
    assert.equal(sdk.split(BOUNDED).length - 1, 1, 'Bounded production expression changed; review benchmark aliases and baseline');
    if (variant === 'original') sdk = replaceOnce(sdk, BOUNDED, ORIGINAL);
    else assert.equal(variant, 'bounded');
    sdk = replaceOnce(sdk, EXPORT_ANCHOR, EXPORTS + EXPORT_ANCHOR);
    vm.runInContext(sdk, ctx);
    for (const file of ['static/live2d/live2d-core.js', 'static/live2d/live2d-model.js']) vm.runInContext(fs.readFileSync(path.join(ROOT, file), 'utf8'), ctx, { filename: file });
    return { ctx, api: ctx.PIXI.live2d.__bench, diagnostics };
}

async function fixture(opts, name, variant, countCopies = false) {
    const env = await runtime(variant);
    const { api, ctx } = env;
    const directory = path.join(opts.modelsRoot, name);
    const settings = json(path.join(directory, `${name}.model3.json`));
    const references = settings.FileReferences;
    const idle = references.Motions?.Idle?.[0] || references.Motions?.idle?.[0];
    assert.ok(idle?.File, `No Idle motion defined for ${name}`);
    const motionJson = json(path.join(directory, idle.File));
    const mocBytes = fs.readFileSync(path.join(directory, references.Moc));
    const moc = ctx.Live2DCubismCore.Moc.fromArrayBuffer(mocBytes.buffer.slice(mocBytes.byteOffset, mocBytes.byteOffset + mocBytes.byteLength));
    assert.ok(moc, `Failed to load moc: ${name}`);
    const raw = ctx.Live2DCubismCore.Model.fromMoc(moc);
    assert.ok(raw, `Failed to create Core model: ${name}`);
    const model = new api.Model(raw);
    const motion = api.Motion.create(motionJson);
    const eyeIds = settings.Groups?.find(group => group.Name === 'EyeBlink' && group.Target === 'Parameter')?.Ids || [];
    const lipIds = settings.Groups?.find(group => group.Name === 'LipSync' && group.Target === 'Parameter')?.Ids || [];
    motion.setEffectIds(eyeIds, lipIds);
    motion.setIsLoop(true);
    const copyCounts = { calls: 0, references: 0, maxLength: 0 };
    if (countCopies) {
        // Instrument this instance's points array only; never used in timing runs.
        const points = motion._motionData.points;
        points.slice = function (...args) {
            const result = Array.prototype.slice.apply(this, args);
            copyCounts.calls++;
            copyCounts.references += result.length;
            copyCounts.maxLength = Math.max(copyCounts.maxLength, result.length);
            return result;
        };
    }
    const queue = new api.Queue();
    queue.setEventCallback(() => {});
    queue.startMotion(motion, false, 0);
    const breath = api.Breath.create();
    breath.setParameters([
        new api.BreathParameter('ParamAngleX', 0, 15, 6.5345, 0.5),
        new api.BreathParameter('ParamAngleY', 0, 8, 3.5345, 0.5),
        new api.BreathParameter('ParamAngleZ', 0, 10, 5.5345, 0.5),
        new api.BreathParameter('ParamBodyAngleX', 0, 4, 15.5345, 0.5),
        new api.BreathParameter('ParamBreath', 0, 0.5, 3.2345, 0.5),
    ]);
    // Construct only CPU update state; skip the Internal constructor's renderer
    // and async asset loading. update() and every CPU component remain real SDK.
    const internal = Object.create(api.Internal.prototype);
    EventEmitter.call(internal);
    Object.assign(internal, {
        coreModel: model, focusController: new api.Focus(), breath, lipSync: false,
        eyeBlink: eyeIds.length ? api.EyeBlink.create({ getEyeBlinkParameters: () => eyeIds }) : undefined,
        physics: references.Physics ? api.Physics.create(json(path.join(directory, references.Physics))) : undefined,
        pose: references.Pose ? api.Pose.create(json(path.join(directory, references.Pose))) : undefined,
        motionManager: { update(core, elapsed) { return queue.doUpdateMotion(core, elapsed); }, state: { currentPriority: 1, currentGroup: 'Idle', currentIndex: 0 } },
    });
    for (const id of ['AngleX', 'AngleY', 'AngleZ', 'EyeBallX', 'EyeBallY', 'BodyAngleX', 'Breath', 'MouthForm']) internal[`idParam${id}`] = `Param${id}`;
    const manager = new ctx.Live2DManager();
    manager._resetDerivedModelMetadata();
    manager.modelName = name;
    manager.currentModel = { internalModel: internal, deltaTime: 1000 / opts.fps };
    manager._validateEyeBlinkGroup(settings, manager.currentModel);
    manager.installMouthOverride();
    let frame = 0;
    const result = {
        ...env, raw, model, motion, queue, copyCounts,
        metadata: { name, motion: idle.File, curves: motionJson.Meta.CurveCount, points: motion._motionData.points.length, physics: !!internal.physics, pose: !!internal.pose },
        update() { internal.update(1000 / opts.fps, ++frame * 1000 / opts.fps); },
        check() { assert.deepEqual(env.diagnostics.errors, [], 'SDK or business wrapper logged errors'); assert.equal(manager._mouthOverrideInstalled, true, 'Business wrapper unexpectedly uninstalled'); },
        release() { queue.stopAllMotions(); model.release(); moc._release(); },
    };
    result.check();
    return result;
}

function summarize(samples) {
    const sorted = [...samples].sort((a, b) => a - b);
    return { meanMs: samples.reduce((sum, item) => sum + item, 0) / samples.length, medianMs: sorted[Math.floor(sorted.length / 2)], p95Ms: sorted[Math.ceil(sorted.length * 0.95) - 1] };
}

async function timing(opts) {
    const run = await fixture(opts, opts.models[0], opts.variant);
    const gc = [];
    const observer = new PerformanceObserver(list => { for (const entry of list.getEntries()) gc.push({ startTime: entry.startTime, durationMs: entry.duration, kind: entry.detail?.kind }); });
    observer.observe({ entryTypes: ['gc'] });
    for (let i = 0; i < opts.warmup; i++) run.update();
    run.check();
    await new Promise(resolve => setImmediate(resolve));
    const samples = new Array(opts.frames);
    const start = performance.now();
    for (let i = 0; i < opts.frames; i++) {
        const before = performance.now();
        run.update();
        samples[i] = performance.now() - before;
    }
    const end = performance.now();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    observer.disconnect();
    run.check();
    const inSample = gc.filter(entry => entry.startTime >= start && entry.startTime < end);
    const result = { variant: opts.variant, ...summarize(samples), sampleWallMs: end - start, gc: { count: inSample.length, durationMs: inSample.reduce((sum, item) => sum + item.durationMs, 0), events: inSample }, warnings: run.diagnostics.warnings };
    run.release();
    return result;
}

function compareNumbers(left, right, label) {
    assert.equal(left.length, right.length, label);
    for (let i = 0; i < left.length; i++) {
        if (!Object.is(left[i], right[i])) assert.equal(left[i], right[i], `${label}[${i}]`);
    }
    return left.length;
}

async function verify(opts) {
    const original = await fixture(opts, opts.models[0], 'original', true);
    const bounded = await fixture(opts, opts.models[0], 'bounded', true);
    assert.notEqual(original.ctx, bounded.ctx);
    assert.notEqual(original.api.Motion, bounded.api.Motion, 'A/B SDK exports must be independent');
    let comparedValues = 0;
    for (let frame = 0; frame < opts.warmup + opts.frames; frame++) {
        original.update(); bounded.update();
        for (const [label, left, right] of [
            ['parameters', original.raw.parameters.values, bounded.raw.parameters.values],
            ['parts', original.raw.parts.opacities, bounded.raw.parts.opacities],
            ['drawables', original.raw.drawables.opacities, bounded.raw.drawables.opacities],
        ]) comparedValues += compareNumbers(left, right, `frame ${frame} ${label}`);
        for (let drawable = 0; drawable < original.raw.drawables.count; drawable++) comparedValues += compareNumbers(original.raw.drawables.vertexPositions[drawable], bounded.raw.drawables.vertexPositions[drawable], `frame ${frame} vertices ${drawable}`);
    }
    original.check(); bounded.check();
    assert.ok(original.copyCounts.calls > 0, 'No curve evaluations reached');
    assert.equal(original.copyCounts.calls, bounded.copyCounts.calls);
    assert.ok(bounded.copyCounts.maxLength <= 4);
    assert.ok(original.copyCounts.references > bounded.copyCounts.references, 'A/B must exercise different copy paths');
    const total = opts.warmup + opts.frames;
    const counts = run => ({ ...run.copyCounts, callsPerFrame: run.copyCounts.calls / total, referencesPerFrame: run.copyCounts.references / total });
    const result = { metadata: original.metadata, exactEquality: true, comparedValues, frames: total, original: counts(original), bounded: counts(bounded), warnings: [...original.diagnostics.warnings, ...bounded.diagnostics.warnings] };
    original.release(); bounded.release();
    return result;
}

function child(opts, model, worker, variant) {
    const args = [__filename, '--worker', worker, '--models-root', opts.modelsRoot, '--models', model, '--warmup', String(opts.warmup), '--frames', String(opts.frames), '--fps', String(opts.fps)];
    if (variant) args.push('--variant', variant);
    const result = spawnSync(process.execPath, args, { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024, windowsHide: true });
    if (result.error) throw result.error;
    if (result.status !== 0) throw Error(`${model} ${worker} ${variant || ''} failed (${result.status}):\n${result.stderr}\n${result.stdout}`);
    return JSON.parse(result.stdout);
}

async function main() {
    const opts = options(process.argv.slice(2));
    if (opts.worker) {
        assert.equal(opts.models.length, 1);
        assert.ok(['timing', 'verify'].includes(opts.worker), 'Unknown worker phase');
        console.log(JSON.stringify(await (opts.worker === 'timing' ? timing(opts) : verify(opts))));
        return;
    }
    const report = {
        environment: { node: process.version, platform: process.platform, arch: process.arch, cpu: os.cpus()[0]?.model, sdk: 'v0.5.0-ls-6', modelsRoot: opts.modelsRoot },
        conditions: { warmupPerRun: opts.warmup, sampleFramesPerRun: opts.frames, simulatedFps: opts.fps, order: ['original', 'bounded', 'bounded', 'original'], scope: 'CPU update only; excludes WebGL, DOM, Electron; no saved parameters, expressions or audio' },
        models: [],
    };
    for (const model of opts.models) {
        process.stderr.write(`Verifying ${model} outputs and copy counts (outside timing)...\n`);
        const verification = child(opts, model, 'verify');
        const runs = report.conditions.order.map(variant => {
            process.stderr.write(`Timing ${model} ${variant}, warmup ${opts.warmup}, samples ${opts.frames}...\n`);
            return child(opts, model, 'timing', variant);
        });
        const variantMeans = variant => runs.filter(run => run.variant === variant).reduce((sum, run) => sum + run.meanMs, 0) / 2;
        const originalMeanMs = variantMeans('original');
        const boundedMeanMs = variantMeans('bounded');
        report.models.push({ name: model, verification, runs, comparison: { originalMeanMs, boundedMeanMs, reductionPercent: (originalMeanMs - boundedMeanMs) / originalMeanMs * 100 } });
    }
    console.log(JSON.stringify(report, null, 2));
}

main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
