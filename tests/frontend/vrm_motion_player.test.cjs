const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zlib = require('node:zlib');

const root = process.cwd();
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'static/vrm/motion/manifest.json'), 'utf8'));
const source = fs.readFileSync(path.join(root, manifest.assets[0].src[0]));
const sourceBuffer = source.buffer.slice(source.byteOffset, source.byteOffset + source.byteLength);

global.window = global;
global.location = { hostname: 'localhost' };
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
    assert.equal(player.assets.length, 13);
    assert.equal(player.assets.every(function (asset) { return asset.compression === 'none'; }), true);

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
    assert.equal(requested, '/static/vrm/' + player.assets[0].f);

    const corrupted = Buffer.from(source);
    corrupted[0] ^= 0xff;
    global.fetch = async function () {
        return response(corrupted.buffer.slice(corrupted.byteOffset, corrupted.byteOffset + corrupted.byteLength));
    };
    await assert.rejects(player._assetUrl(player.assets[0]), /packed SHA-256 mismatch/);

    const packed = zlib.gzipSync(source);
    const gzipAsset = Object.assign({}, player.assets[0], {
        compression: 'gzip',
        f: 'motion-pack/example.vrma',
        packedSha: crypto.createHash('sha256').update(packed).digest('hex'),
        decodedSha: crypto.createHash('sha256').update(source).digest('hex')
    });
    global.DecompressionStream = require('node:stream/web').DecompressionStream;
    global.fetch = async function (url) {
        requested = String(url);
        return response(packed.buffer.slice(packed.byteOffset, packed.byteOffset + packed.byteLength));
    };
    assert.match(await player._assetUrl(gzipAsset), /^blob:test-/);
    assert.equal(requested, '/static/vrm/motion-pack/example.vrma.gz');

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

    console.log('VRM motion player: OK (integrity and low-pose transitions)');
})().catch(function (error) {
    console.error(error.stack || error);
    process.exitCode = 1;
});
