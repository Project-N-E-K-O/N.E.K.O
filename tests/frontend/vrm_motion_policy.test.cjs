const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const root = process.cwd();
const motionRoot = path.join(root, 'static/vrm/motion');
const manifest = JSON.parse(fs.readFileSync(path.join(motionRoot, 'manifest.json'), 'utf8'));
const requiredLocales = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'];

assert.equal(manifest.policy.distribution, 'public-release');
assert.equal(manifest.policy.localTestEnabled, false);
assert.equal(manifest.policy.previewUnrated, false);
assert.equal(manifest.assets.length, 13);
assert.equal(manifest.counts.files, 13);

manifest.assets.forEach(function (asset) {
    assert.equal(asset.ok, true, asset.id);
    assert.equal(asset.license, 'Apache-2.0', asset.id);
    assert.equal(asset.compression, 'none', asset.id);
    assert.equal(asset.card.descriptionStatus, 'human-verified', asset.id);
    assert.equal(asset.f, asset.src[0].replace(/^static\/vrm\//, ''), asset.id);
    requiredLocales.forEach(function (locale) {
        assert.equal(typeof asset.names[locale], 'string', asset.id + ':' + locale);
        assert.notEqual(asset.names[locale].trim(), '', asset.id + ':' + locale);
    });

    const source = path.join(root, asset.src[0]);
    const bytes = fs.readFileSync(source);
    const digest = crypto.createHash('sha256').update(bytes).digest('hex');
    assert.equal(bytes.length, asset.packedBytes, asset.id);
    assert.equal(bytes.length, asset.decodedBytes, asset.id);
    assert.equal(digest, asset.packedSha, asset.id);
    assert.equal(digest, asset.decodedSha, asset.id);
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

assert.deepEqual(relativeFiles, [
    'core.js',
    'manifest.json',
    'player.js',
    'runtime.js',
    'semantics.json'
]);
assert.equal(relativeFiles.some(function (name) { return name.endsWith('.vrma.gz'); }), false);

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
assert.equal(runtimeSource.includes("new BroadcastChannel('neko_motion_lifecycle')"), false);
assert.match(runtimeSource, /neko:motion-lifecycle-relay/);

const modelManagerSource = fs.readFileSync(
    path.join(root, 'static/js/model_manager/page-controller.js'),
    'utf8'
);
const modelManagerTemplate = fs.readFileSync(path.join(root, 'templates/model_manager.html'), 'utf8');
assert.match(modelManagerSource, /new window\.NekoMotionPlayer\(\)/);
assert.match(modelManagerSource, /mergeVrmAnimationLists/);
assert.match(modelManagerSource, /data-motion-asset-id/);
assert.match(modelManagerSource, /playSelectedVrmAnimationOption/);
assert.match(modelManagerTemplate, /static\/vrm\/motion\/player\.js/);

console.log('VRM motion policy and source integrity: OK (13 localized official assets)');
