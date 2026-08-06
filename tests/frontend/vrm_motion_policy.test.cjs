const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');

const fileRoot = path.resolve(__dirname, '..', '..');
const root = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const motionRoot = path.join(root, 'static/vrm/motion');
const manifest = JSON.parse(fs.readFileSync(path.join(motionRoot, 'manifest.json'), 'utf8'));
const requiredLocales = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'];

assert.equal(manifest.policy.distribution, 'public-release');
assert.equal(manifest.policy.localTestEnabled, false);
assert.equal(manifest.policy.previewUnrated, false);
assert.equal(manifest.assets.length, 75);
assert.equal(manifest.counts.files, 75);
assert.equal(manifest.counts.official, 13);
assert.equal(manifest.assets.find(function (asset) { return asset.id === 'sit_01'; }).label, '半躺');
assert.deepEqual(
    manifest.assets.find(function (asset) { return asset.id === 'overwhelm_01'; }).card.emotions,
    ['fearful']
);

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
assert.match(websocketSource, /function relayClosedMotionStage\(event\)/);
assert.match(websocketSource, /event\.detail\.text/);
assert.match(
    websocketSource,
    /window\.addEventListener\('pageshow',[\s\S]*window\.addEventListener\('neko-compact-caption-update', relayClosedMotionStage\)/
);
assert.match(relaySource, /case 'motion_lifecycle'/);
assert.match(relaySource, /neko:motion-lifecycle-relay/);
assert.match(relaySource, /!motionCurrentName \|\| motionDetail\.lanlan_name !== motionCurrentName/);
assert.equal(runtimeSource.includes("new BroadcastChannel('neko_motion_lifecycle')"), false);
assert.equal(
    runtimeSource.includes("window.vrmManager.currentModel.vrm) selectedMode = 'vrm'"),
    false,
    'a retained hidden VRM model must not override the configured active mode'
);
assert.match(runtimeSource, /neko:motion-lifecycle-relay/);
assert.match(runtimeSource, /window\.__nekoMotionOwnsVrmPlayback = false/);
assert.match(runtimeSource, /releasePlaybackOwnership\(\)/);
assert.match(runtimeSource, /player\.cancel\('model_mode_changed', \{ resume: false \}\)/);
assert.match(runtimeSource, /syncSavedRestAnimations\(\)/);
assert.match(runtimeSource, /if \(refreshMode\(\) === 'vrm'\) void initialize\(\)/);
assert.equal(runtimeSource.includes('\n    void initialize();\n'), false);
assert.match(runtimeSource, /activeTurn === turn/);
assert.match(runtimeSource, /ignored stale assistant turn end/);
assert.equal(runtimeSource.includes("window.dispatchEvent(new CustomEvent(message.eventName"), false);
assert.match(runtimeSource, /await initialize\(\)/);
assert.match(runtimeSource, /processUnseenStagesDirect\(turn\)/);
assert.match(runtimeSource, /turn\.cancelled = true/);
assert.match(runtimeSource, /player\.cancel\('assistant_speech_cancel', \{ resume: refreshMode\(\) === 'vrm' \}\)/);
assert.match(runtimeSource, /activeTurn = null;\s*bridgedText = ''/);
assert.match(runtimeSource, /pendingStages: new Set\(\)/);
assert.match(runtimeSource, /turn\.pendingStages\.has\(stage\.id\)/);
assert.match(runtimeSource, /if \(await processStage\(stage, turn\)\) turn\.seen\.add\(stage\.id\)/);
assert.match(runtimeSource, /turn\.pendingStages\.delete\(stage\.id\)/);
assert.match(runtimeSource, /if \(turn && isCurrentTurn\(turn\)\) turn\.deferredUntilVrmReady = true/);
assert.match(runtimeSource, /const duplicateStaleBuffer = \(!turnId \|\| duplicateId\)/);
const turnEndSource = runtimeSource.split('function endObservedTurn', 2)[1]
    .split('function emotionObserved', 1)[0];
assert.match(turnEndSource, /if \(refreshMode\(\) !== 'vrm'\)/);
assert.ok(
    turnEndSource.indexOf("if (refreshMode() !== 'vrm')")
        < turnEndSource.indexOf("beginTurn(turnId, source || 'lifecycle')"),
    'a turn end outside VRM mode must not create a deferred motion turn'
);
const emotionSource = runtimeSource.split('function emotionObserved', 2)[1]
    .split('function cancelObservedSpeech', 1)[0];
assert.match(
    emotionSource,
    /if \(turnId && \(!activeTurn \|\| String\(turnId\) !== activeTurn\.id\)\) return;/
);
assert.match(runtimeSource, /async function handleVrmModelLoaded\(\)/);
assert.match(runtimeSource, /await processUnseenStagesDirect\(turn\)/);
assert.match(runtimeSource, /function resetCharacterMotionState\(\)/);
assert.match(runtimeSource, /turn\.deferredUntilVrmReady = true/);
assert.match(runtimeSource, /turn && isCurrentTurn\(turn\) && turn\.deferredUntilVrmReady/);
assert.match(runtimeSource, /window\.vrmManager\.currentModel !== loadedModel/);
assert.match(runtimeSource, /casualTalkPending/);
const nonVrmMarker = "if (mode !== 'vrm') {";
const nonVrmParts = runtimeSource.split(nonVrmMarker);
assert.equal(nonVrmParts.length, 2, 'non-VRM turn guard must remain unique');
const nonVrmTurnBlock = nonVrmParts[1].split('}')[0];
assert.match(nonVrmTurnBlock, /window\._nekoMotionPendingUserText = ''/);
assert.ok(nonVrmTurnBlock.indexOf("window._nekoMotionPendingUserText = ''") < nonVrmTurnBlock.indexOf('return;'));
const modeSetMarker = "window.addEventListener('neko-model-manager-mode-set'";
const modeSetParts = runtimeSource.split(modeSetMarker);
assert.equal(modeSetParts.length, 2, 'mode-set listener must remain unique');
const modeSetBlock = modeSetParts[1].slice(0, 1800);
assert.match(modeSetBlock, /else \{\s*releasePlaybackOwnership\(\)/);
assert.match(runtimeSource, /function stopMaintenanceTimers\(\)/);
assert.match(runtimeSource, /window\.addEventListener\('pagehide'/);
assert.match(runtimeSource, /window\.addEventListener\('pageshow'/);
assert.match(runtimeSource, /bindMotionLifecycleBridge\(\);\s*startMaintenanceTimers\(\)/);

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
