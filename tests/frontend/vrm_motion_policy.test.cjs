const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const root = process.cwd();
const motionRoot = path.join(root, 'static/vrm/motion');
const manifest = JSON.parse(fs.readFileSync(path.join(motionRoot, 'manifest.json'), 'utf8'));
const requiredLocales = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'];

assert.equal(manifest.policy.distribution, 'public-release');
assert.equal(manifest.policy.localTestEnabled, false);
assert.equal(manifest.policy.previewUnrated, false);
assert.equal(manifest.assets.length, 75);
assert.equal(manifest.counts.files, 75);
assert.equal(manifest.counts.official, 13);

manifest.assets.forEach(function (asset) {
    assert.equal(asset.ok, true, asset.id);
    assert.equal(asset.license, 'Apache-2.0', asset.id);
    assert.equal(asset.compression, 'gzip', asset.id);
    assert.equal(asset.card.descriptionStatus, 'human-verified', asset.id);
    assert.equal(['a', 'b', 'l', 'r'].includes(asset.h), true, asset.id + ':handedness');
    assert.equal(asset.src[0], 'static/vrm/' + asset.f + '.gz', asset.id);
    requiredLocales.forEach(function (locale) {
        assert.equal(typeof asset.names[locale], 'string', asset.id + ':' + locale);
        assert.notEqual(asset.names[locale].trim(), '', asset.id + ':' + locale);
    });

    const source = path.join(root, asset.src[0]);
    const packed = fs.readFileSync(source);
    const decoded = zlib.gunzipSync(packed);
    const packedDigest = crypto.createHash('sha256').update(packed).digest('hex');
    const decodedDigest = crypto.createHash('sha256').update(decoded).digest('hex');
    assert.equal(packed.length, asset.packedBytes, asset.id);
    assert.equal(decoded.length, asset.decodedBytes, asset.id);
    assert.equal(packedDigest, asset.packedSha, asset.id);
    assert.equal(decodedDigest, asset.decodedSha, asset.id);
});

function walk(directory) {
    return fs.readdirSync(directory, { withFileTypes: true }).flatMap(function (entry) {
        const fullPath = path.join(directory, entry.name);
        return entry.isDirectory() ? walk(fullPath) : [fullPath];
    });
}

const relativeFiles = walk(motionRoot).map(function (filename) {
    return path.relative(motionRoot, filename).split(path.sep).join('/');
}).sort();

assert.deepEqual(relativeFiles.filter(function (name) { return !name.endsWith('.vrma.gz'); }), [
    'core.js',
    'manifest.json',
    'player.js',
    'runtime.js',
    'semantics.json'
]);
assert.equal(relativeFiles.filter(function (name) { return name.endsWith('.vrma.gz'); }).length, 62);

const allVrmFiles = walk(path.join(root, 'static/vrm'));
assert.equal(allVrmFiles.filter(function (name) { return name.endsWith('.vrma.gz'); }).length, 75);
assert.equal(allVrmFiles.some(function (name) { return name.endsWith('.vrma'); }), false);

const websocketSource = fs.readFileSync(path.join(root, 'static/app/app-websocket.js'), 'utf8');
const relaySource = fs.readFileSync(
    path.join(root, 'static/app/app-interpage/guide-message-relay.js'),
    'utf8'
);
const runtimeSource = fs.readFileSync(path.join(motionRoot, 'runtime.js'), 'utf8');
assert.equal(websocketSource.includes("new BroadcastChannel('neko_motion_lifecycle')"), false);
assert.match(websocketSource, /appInterpage\.nekoBroadcastChannel/);
assert.match(relaySource, /case 'motion_lifecycle'/);
assert.match(relaySource, /neko:motion-lifecycle-relay/);
assert.match(relaySource, /!motionCurrentName \|\| motionDetail\.lanlan_name !== motionCurrentName/);
assert.equal(runtimeSource.includes("new BroadcastChannel('neko_motion_lifecycle')"), false);
assert.match(runtimeSource, /neko:motion-lifecycle-relay/);
assert.match(runtimeSource, /window\.__nekoMotionOwnsVrmPlayback = false/);
assert.match(runtimeSource, /releasePlaybackOwnership\(\)/);
assert.match(runtimeSource, /activeTurn === turn/);
assert.match(runtimeSource, /ignored stale assistant turn end/);
assert.equal(runtimeSource.includes("window.dispatchEvent(new CustomEvent(message.eventName"), false);
assert.match(runtimeSource, /await initialize\(\)/);
assert.match(runtimeSource, /processUnseenStagesDirect\(turn\)/);
assert.match(runtimeSource, /casualTalkPending/);

const modelManagerSource = fs.readFileSync(
    path.join(root, 'static/js/model_manager/page-controller.js'),
    'utf8'
);
const modelManagerTemplate = fs.readFileSync(path.join(root, 'templates/model_manager.html'), 'utf8');
assert.match(modelManagerSource, /new window\.NekoMotionPlayer\(\)/);
assert.match(modelManagerSource, /mergeVrmAnimationLists/);
assert.match(modelManagerSource, /data-motion-asset-id/);
assert.match(modelManagerSource, /playSelectedVrmAnimationOption/);
assert.match(modelManagerSource, /vrmMotionCatalogLoadPromise/);
assert.match(modelManagerSource, /cancel\('model_manager_stop', \{ resume: false \}\)/);
assert.match(modelManagerSource, /normalizeBundledVrmAnimationUrl/);
assert.match(modelManagerTemplate, /static\/vrm\/motion\/player\.js/);

console.log('VRM motion policy and source integrity: OK (75 gzip assets)');
