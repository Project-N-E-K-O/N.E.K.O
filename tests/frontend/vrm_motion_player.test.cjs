const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zlib = require('node:zlib');

const root = process.cwd();
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'static/vrm/motion/manifest.json'), 'utf8'));
const packedSource = fs.readFileSync(path.join(root, manifest.assets[0].src[0]));
const decodedSource = zlib.gunzipSync(packedSource);
const sourceBuffer = packedSource.buffer.slice(
    packedSource.byteOffset,
    packedSource.byteOffset + packedSource.byteLength
);

global.window = global;
global.location = { hostname: 'localhost' };
global.document = { baseURI: 'http://localhost/' };
global.crypto = crypto.webcrypto;
global.CustomEvent = class CustomEvent {
    constructor(type, init) {
        this.type = type;
        this.detail = init && init.detail;
    }
};
global.dispatchEvent = function () {};
global.addEventListener = function () {};
global.removeEventListener = function () {};

let blobSequence = 0;
global.URL.createObjectURL = function () { blobSequence += 1; return 'blob:test-' + blobSequence; };
global.URL.revokeObjectURL = function () {};

vm.runInThisContext(
    fs.readFileSync(path.join(root, 'static/vrm/motion/player.js'), 'utf8'),
    { filename: 'static/vrm/motion/player.js' }
);
vm.runInThisContext(
    fs.readFileSync(path.join(root, 'static/vrm/vrm-animation.js'), 'utf8'),
    { filename: 'static/vrm/vrm-animation.js' }
);
vm.runInThisContext(
    fs.readFileSync(path.join(root, 'static/vrm/vrm-expression.js'), 'utf8'),
    { filename: 'static/vrm/vrm-expression.js' }
);
vm.runInThisContext(
    fs.readFileSync(path.join(root, 'static/vrm/vrm-interaction.js'), 'utf8'),
    { filename: 'static/vrm/vrm-interaction.js' }
);

function response(body, status) {
    return {
        ok: !status || status < 400,
        status: status || 200,
        async json() { return JSON.parse(JSON.stringify(body)); },
        async arrayBuffer() { return body; }
    };
}

(async function () {
    global.fetch = async function (url) {
        assert.equal(String(url), '/static/vrm/motion/manifest.json');
        return response(manifest);
    };
    const player = await new global.NekoMotionPlayer().load();
    assert.equal(player.assets.length, 75);
    assert.equal(player.assets.every(function (asset) { return asset.compression === 'gzip'; }), true);

    const zhCatalog = player.catalog('zh-CN');
    const enCatalog = player.catalog('en-US');
    assert.equal(zhCatalog.length, 74);
    assert.equal(zhCatalog[0].name, '开心地回应喜欢和亲近');
    assert.equal(enCatalog[0].name, 'Happily respond with affection');
    assert.equal(enCatalog[0].path, '/static/vrm/animation/liked.vrma.gz');
    assert.equal(enCatalog[0].systemMotion, true);
    assert.equal(zhCatalog.some(function (asset) { return asset.id === 'cheer_01'; }), false);

    let previewPlan = null;
    player.playPlan = async function (plan) {
        previewPlan = plan;
        return true;
    };
    await player.playAsset('official_liked');
    assert.equal(previewPlan.length, 1);
    assert.equal(previewPlan[0].intent, 'like');
    assert.equal(previewPlan[0].evidence.assetId, 'official_liked');

    const unlicensed = JSON.parse(JSON.stringify(manifest));
    unlicensed.assets[0].license = '?';
    global.fetch = async function () { return response(unlicensed); };
    await assert.rejects(new global.NekoMotionPlayer().load(), /unapproved or unlicensed/);

    let requested = '';
    global.fetch = async function (url) {
        requested = String(url);
        return response(sourceBuffer);
    };
    assert.match(await player._assetUrl(player.assets[0]), /^blob:test-/);
    assert.equal(requested, '/static/vrm/' + player.assets[0].f + '.gz');

    const corrupted = Buffer.from(packedSource);
    corrupted[2] ^= 0xff;
    global.fetch = async function () {
        return response(corrupted.buffer.slice(corrupted.byteOffset, corrupted.byteOffset + corrupted.byteLength));
    };
    await assert.rejects(player._assetUrl(player.assets[0]), /packed SHA-256 mismatch/);

    const packed = zlib.gzipSync(decodedSource);
    const gzipAsset = Object.assign({}, player.assets[0], {
        compression: 'gzip',
        f: 'motion-pack/example.vrma',
        packedSha: crypto.createHash('sha256').update(packed).digest('hex'),
        decodedSha: crypto.createHash('sha256').update(decodedSource).digest('hex')
    });
    global.DecompressionStream = require('node:stream/web').DecompressionStream;
    global.fetch = async function (url) {
        requested = String(url);
        return response(packed.buffer.slice(packed.byteOffset, packed.byteOffset + packed.byteLength));
    };
    assert.match(await player._assetUrl(gzipAsset), /^blob:test-/);
    assert.equal(requested, '/static/vrm/motion-pack/example.vrma.gz');

    global.fetch = async function () {
        return response(decodedSource.buffer.slice(
            decodedSource.byteOffset,
            decodedSource.byteOffset + decodedSource.byteLength
        ));
    };
    assert.match(await player._assetUrl(gzipAsset), /^blob:test-/);

    const corruptedDecoded = Buffer.from(decodedSource);
    corruptedDecoded[0] ^= 0xff;
    global.fetch = async function () {
        return response(corruptedDecoded.buffer.slice(
            corruptedDecoded.byteOffset,
            corruptedDecoded.byteOffset + corruptedDecoded.byteLength
        ));
    };
    await assert.rejects(player._assetUrl(gzipAsset), /decoded SHA-256 mismatch/);

    let parsedBytes = null;
    let parsedResourcePath = '';
    const directLoader = {
        async parseAsync(bytes, resourcePath) {
            parsedBytes = Buffer.from(bytes);
            parsedResourcePath = resourcePath;
            return { userData: { vrmAnimations: [{}] } };
        }
    };
    global.fetch = async function () {
        return response(sourceBuffer);
    };
    const animation = Object.create(global.VRMAnimation.prototype);
    await animation._loadVRMAGltf(directLoader, '/static/vrm/animation/liked.vrma.gz');
    assert.deepEqual(parsedBytes, decodedSource);
    assert.equal(parsedResourcePath, 'http://localhost/static/vrm/animation/');

    const lowPosePlayer = new global.NekoMotionPlayer();
    lowPosePlayer.assets = [
        { id: 'sit', m: 'sit', in: 'stand', out: 'sit', i: 2, s: ['upright'], card: { styles: ['upright'] } },
        { id: 'lie-side', m: 'lie', in: 'stand', out: 'lie', i: 2, s: ['side'], card: { styles: ['side'] } },
        { id: 'lie-prone', m: 'lie', in: 'stand', out: 'lie', i: 2, s: ['prone'], card: { styles: ['prone'] } },
        { id: 'recover-side', m: 'recover', in: 'lie', out: 'stand', i: 2, s: ['side'], card: { styles: ['side'] } },
        { id: 'recover-prone', m: 'recover', in: 'lie', out: 'stand', i: 2, s: ['prone'], card: { styles: ['prone'] } }
    ];
    const playedLowPoseIds = [];
    lowPosePlayer._playAsset = async function (asset) {
        playedLowPoseIds.push(asset.id);
        return true;
    };
    lowPosePlayer._playTransient = async function (asset) {
        playedLowPoseIds.push(asset.id);
        return true;
    };
    await lowPosePlayer._enterLowPose({
        intent: 'lie', style: 'side', intensity: 2, evidence: { canonicalZh: '侧身躺下' }
    }, lowPosePlayer.queueGeneration, 'side');
    await lowPosePlayer._enterLowPose({
        intent: 'lie', style: 'prone', intensity: 2, evidence: { canonicalZh: '俯身趴下' }
    }, lowPosePlayer.queueGeneration, 'prone');
    assert.equal(lowPosePlayer.state.poseStyle, 'prone');
    assert.equal(lowPosePlayer.state.poseAsset.id, 'lie-prone');
    assert.deepEqual(playedLowPoseIds.slice(0, 2), ['lie-side', 'lie-prone']);

    lowPosePlayer.state.posture = 'lie';
    lowPosePlayer.state.poseAsset = lowPosePlayer.assets[2];
    lowPosePlayer.state.poseStyle = 'prone';
    await lowPosePlayer._enterLowPose({
        intent: 'sit', style: 'upright', intensity: 2, evidence: { canonicalZh: '坐起来' }
    }, lowPosePlayer.queueGeneration, 'sit-after-prone');
    assert.deepEqual(playedLowPoseIds.slice(-2), ['recover-prone', 'sit']);
    assert.equal(lowPosePlayer.state.posture, 'sit');

    const cancelPlayer = new global.NekoMotionPlayer();
    let resumedAfterCancel = 0;
    cancelPlayer._resumeBase = async function () { resumedAfterCancel += 1; return true; };
    cancelPlayer.cancel('manual-stop', { resume: false });
    await Promise.resolve();
    assert.equal(resumedAfterCancel, 0);

    const staleCatalogPlayer = new global.NekoMotionPlayer();
    const staleCatalogAsset = { id: 'stale-catalog', m: 'wave', i: 2 };
    let finishCatalogLoad;
    let catalogShouldApply;
    staleCatalogPlayer._assetUrl = async function () { return 'blob:test-stale-catalog'; };
    staleCatalogPlayer._manager = function () {
        return {
            playVRMAAnimation(url, options) {
                assert.equal(url, 'blob:test-stale-catalog');
                catalogShouldApply = options.shouldApply;
                return new Promise(function (resolve) { finishCatalogLoad = resolve; });
            }
        };
    };
    const staleCatalogRequest = staleCatalogPlayer._playAsset(
        staleCatalogAsset,
        staleCatalogPlayer.queueGeneration,
        {}
    );
    await Promise.resolve();
    await Promise.resolve();
    staleCatalogPlayer.cancel('model_manager_pause', { resume: false });
    assert.equal(catalogShouldApply(), false);
    finishCatalogLoad(false);
    assert.equal(await staleCatalogRequest, false);
    assert.equal(staleCatalogPlayer.state.currentAsset, null);
    assert.equal(staleCatalogPlayer.metrics.played, 0);

    const expression = new global.VRMExpression({});
    assert.deepEqual(expression._resolveMoodWeights('shy', ['relaxed', 'happy']), {
        relaxed: 0.55,
        happy: 0.18
    });
    expression.setMoodMap({ shy: ['custom_blush'] });
    assert.deepEqual(expression._resolveMoodWeights('shy', ['custom_blush', 'happy']), {
        custom_blush: 1
    });

    const framing = global.NekoVRMSafeFraming;
    assert.equal(framing.calculateFramingRatio({
        minX: 100, maxX: 900, minY: 100, maxY: 900
    }, 1000, 1000, 50) < 1, true);
    assert.equal(framing.calculateExpandedFov(30, {
        minX: -100, maxX: 1100, minY: 0, maxY: 1000
    }, 1000, 1000, 50, 44) > 30, true);

    const failingAnimation = Object.create(global.VRMAnimation.prototype);
    const failingVrm = {
        scene: { uuid: 'failure-scene', traverse() {} },
        humanoid: { autoUpdateHumanBones: true }
    };
    failingAnimation.manager = { currentModel: { vrm: failingVrm } };
    failingAnimation._playRequestGeneration = 0;
    failingAnimation._skinnedMeshes = [];
    failingAnimation._cachedSceneUuid = null;
    failingAnimation._fadeTimer = null;
    failingAnimation.currentAction = null;
    failingAnimation._cleanupOldMixer = function () {};
    failingAnimation._initLoader = async function () { return {}; };
    failingAnimation._loadVRMAGltf = async function () { throw new Error('fixture load failure'); };
    await assert.rejects(
        failingAnimation.playVRMAAnimation('/broken.vrma'),
        /fixture load failure/
    );
    assert.equal(failingVrm.humanoid.autoUpdateHumanBones, true);

    let finishDelayedLoad;
    let delayedRequestCurrent = true;
    let staleRequestPlayed = false;
    const staleAnimation = Object.create(global.VRMAnimation.prototype);
    const staleVrm = {
        scene: { uuid: 'stale-scene', traverse() {} },
        humanoid: { autoUpdateHumanBones: true }
    };
    staleAnimation.manager = { currentModel: { vrm: staleVrm } };
    staleAnimation._playRequestGeneration = 0;
    staleAnimation._skinnedMeshes = [];
    staleAnimation._cachedSceneUuid = null;
    staleAnimation._fadeTimer = null;
    staleAnimation.currentAction = null;
    staleAnimation._cleanupOldMixer = function () {};
    staleAnimation._initLoader = async function () { return {}; };
    staleAnimation._loadVRMAGltf = function () {
        return new Promise(function (resolve) { finishDelayedLoad = resolve; });
    };
    staleAnimation._playAction = function () { staleRequestPlayed = true; };
    const staleRequest = staleAnimation.playVRMAAnimation('/delayed.vrma', {
        shouldApply() { return delayedRequestCurrent; }
    });
    await Promise.resolve();
    await Promise.resolve();
    delayedRequestCurrent = false;
    finishDelayedLoad({ userData: { vrmAnimations: [{}] } });
    assert.equal(await staleRequest, false);
    assert.equal(staleRequestPlayed, false);
    assert.equal(staleVrm.humanoid.autoUpdateHumanBones, true);

    console.log('VRM motion player: OK (integrity and low-pose transitions)');
})().catch(function (error) {
    console.error(error.stack || error);
    process.exitCode = 1;
});
