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

const talkAsset = manifest.assets.find(function (asset) { return asset.id === 'talk_03'; });
const talkGlb = zlib.gunzipSync(fs.readFileSync(path.join(root, talkAsset.src[0])));
const talkJsonLength = talkGlb.readUInt32LE(12);
const talkJson = JSON.parse(talkGlb.subarray(20, 20 + talkJsonLength).toString('utf8'));
const talkBinOffset = 20 + talkJsonLength + 8;
const hipsIndex = talkJson.nodes.findIndex(function (node) { return node.name === 'mixamorig:Hips'; });
const hipsRotationChannel = talkJson.animations[0].channels.find(function (channel) {
    return channel.target.node === hipsIndex && channel.target.path === 'rotation';
});
const hipsRotationAccessor = talkJson.accessors[
    talkJson.animations[0].samplers[hipsRotationChannel.sampler].output
];
const hipsRotationView = talkJson.bufferViews[hipsRotationAccessor.bufferView];
const hipsRotationOffset = talkBinOffset + (hipsRotationView.byteOffset || 0)
    + (hipsRotationAccessor.byteOffset || 0);
const hipsRotationStride = hipsRotationView.byteStride || 16;
let maxTalkYaw = 0;
for (let index = 0; index < hipsRotationAccessor.count; index += 1) {
    const offset = hipsRotationOffset + index * hipsRotationStride;
    const x = talkGlb.readFloatLE(offset);
    const y = talkGlb.readFloatLE(offset + 4);
    const z = talkGlb.readFloatLE(offset + 8);
    const w = talkGlb.readFloatLE(offset + 12);
    const yaw = Math.atan2(2 * (w * y + x * z), 1 - 2 * (y * y + z * z));
    maxTalkYaw = Math.max(maxTalkYaw, Math.abs(yaw));
}
assert.ok(maxTalkYaw < 5 * Math.PI / 180, 'talk_03 must remain front-facing');

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
    'bridge.js',
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
const buttonsSource = fs.readFileSync(path.join(root, 'static/app/app-buttons.js'), 'utf8');
const bridgeSource = fs.readFileSync(path.join(motionRoot, 'bridge.js'), 'utf8');
const relaySource = fs.readFileSync(
    path.join(root, 'static/app/app-interpage/guide-message-relay.js'),
    'utf8'
);
const runtimeSource = fs.readFileSync(path.join(motionRoot, 'runtime.js'), 'utf8');
assert.equal(websocketSource.includes('_nekoMotionPendingUserText'), false);
assert.equal(buttonsSource.includes('_nekoMotionPendingUserText'), false);
assert.match(buttonsSource, /requestId: requestId,\s*text: text\.slice\(0, 1000\),\s*source:/);
assert.match(
    websocketSource,
    /requestId: resolveAssistantRequestId\(response\.request_id, response\.meta\),\s*text: normalizedVoiceTranscript\.slice\(0, 1000\),\s*source: 'voice'/
);
assert.equal(bridgeSource.includes("new BroadcastChannel('neko_motion_lifecycle')"), false);
assert.match(bridgeSource, /appInterpage\.nekoBroadcastChannel/);
assert.match(bridgeSource, /function relayClosedStage\(event\)/);
assert.match(bridgeSource, /function peekUserText\(requestIdValue\)/);
assert.match(bridgeSource, /relay\('neko-assistant-turn-end', detail\);\s*finishUserText\(event\);/);
assert.match(bridgeSource, /function relaySpeechCancel\(event\)/);
assert.match(bridgeSource, /\^user_activity\(\?:_delayed\)\?\$[\s\S]*dropConsumedContexts\(\)/);
assert.equal(
    bridgeSource.includes("relayDetail('neko-assistant-speech-cancel')"),
    false,
    'a user-activity interrupt must clear the consumed user text, not only relay the cancel'
);
assert.match(bridgeSource, /event\.detail\.text/);
assert.match(bridgeSource, /detail\.structured = detail\.structured === true \|\| window\._turnIsStructured === true/);
assert.match(bridgeSource, /text: closedText,\s*structured: window\._turnIsStructured === true/);
assert.equal(bridgeSource.includes('_lastSubmittedText'), false);
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
assert.match(runtimeSource, /played = await player\.playPlan\(plan, context\)/);
assert.match(runtimeSource, /if \(!played\) \{[\s\S]*if \(!vrmReady\(\)\) \{[\s\S]*turn\.deferredUntilVrmReady = true;[\s\S]*return false;[\s\S]*return true;/);
assert.match(runtimeSource, /const played = await player\.playPlan\(plan, \{ seed: turn\.id \+ ':speech' \}\)/);
assert.match(runtimeSource, /turn\.speechProcessed = false;\s*turn\.casualTalkFinalized = false;\s*turn\.deferredUntilVrmReady = true/);
assert.match(runtimeSource, /if \(!played && isCurrentTurn\(turn\) && !vrmReady\(\)\)/);
assert.match(runtimeSource, /if \(turn\.structured\) \{[\s\S]*turn\.speechProcessed = true;/);
assert.match(runtimeSource, /if \(!turn \|\| turn\.structured \|\| !core \|\| !player/);
assert.match(runtimeSource, /activeTurn\.structured = activeTurn\.structured \|\| detail\.structured === true/);
assert.match(runtimeSource, /if \(!vrmReady\(\)\) \{\s*turn\.deferredUntilVrmReady = true;\s*return;/);
assert.match(runtimeSource, /player\.cancel\('assistant_speech_cancel', \{ resume: refreshMode\(\) === 'vrm' \}\)/);
assert.match(runtimeSource, /activeTurn = null;\s*bridgedText = ''/);
assert.match(runtimeSource, /pendingStages: new Set\(\)/);
assert.match(runtimeSource, /structured: !String\(source \|\| ''\)\.startsWith\('bridge'\)/);
assert.match(
    runtimeSource,
    /if \(!isBridgedTurn\(activeTurn\) && window\._turnIsStructured === true\) \{\s*activeTurn\.structured = true;/
);
assert.equal(
    ((runtimeSource.replace(/^\s*\/\/.*$/gm, '')).match(/window\._turnIsStructured/g) || []).length,
    2,
    'every local structured-flag read must be gated on a non-bridged turn'
);
assert.match(
    runtimeSource,
    /beginTurn\(\s*window\._nekoAssistantTurnId \|\| 'buffer-' \+ Date\.now\(\),\s*useBridgeText \? 'bridge-buffer' : 'buffer'/,
    'a turn recovered from bridged text must stay bridge-sourced'
);
assert.match(runtimeSource, /turn\.pendingStages\.has\(stage\.id\)/);
assert.match(runtimeSource, /if \(await processStage\(stage, turn\)\) turn\.seen\.add\(stage\.id\)/);
assert.match(runtimeSource, /turn\.pendingStages\.delete\(stage\.id\)/);
assert.match(runtimeSource, /if \(turn && isCurrentTurn\(turn\)\) turn\.deferredUntilVrmReady = true/);
assert.match(runtimeSource, /const duplicateStaleBuffer = \(!turnId \|\| duplicateId\)/);
const startObservedTurn = runtimeSource.split('function startObservedTurn(event)', 2)[1]
    .split('function endObservedTurn', 1)[0];
assert.ok(
    startObservedTurn.indexOf("if (activeTurn && activeTurn.ended && (duplicateId || duplicateStaleBuffer))")
        < startObservedTurn.indexOf("if (bridgeEvent) bridgedText = ''")
        && startObservedTurn.indexOf("if (bridgeEvent) bridgedText = ''")
        < startObservedTurn.indexOf('const canReuseActiveTurn =')
);
assert.match(
    startObservedTurn,
    /turnId && String\(turnId\) === activeTurn\.id[\s\S]*activeTurn\.capturedText[\s\S]*\^\(\?:buffer\|bridge-buffer\)\$[\s\S]*if \(canReuseActiveTurn\)/
);
assert.match(
    startObservedTurn,
    /else \{\s*if \(activeTurn && !activeTurn\.ended\) discardActiveTurn\(\);\s*beginTurn\(/
);
const bridgeTextUpdate = runtimeSource.split("if (message.eventName === 'neko-assistant-text-update')", 2)[1]
    .split("if (message.eventName === 'neko-assistant-turn-end'", 1)[0];
assert.match(bridgeTextUpdate, /if \(refreshMode\(\) !== 'vrm'\)/);
assert.ok(
    bridgeTextUpdate.indexOf("if (refreshMode() !== 'vrm')")
        < bridgeTextUpdate.indexOf('finishedTurnIds.has(updateTurnId)')
        && bridgeTextUpdate.indexOf('finishedTurnIds.has(updateTurnId)')
        < bridgeTextUpdate.indexOf('bridgedText = String(detail.text')
        && bridgeTextUpdate.indexOf('bridgedText = String(detail.text')
        < bridgeTextUpdate.indexOf("beginTurn(detail.turnId || 'bridge-' + Date.now(), 'bridge-buffer')")
);
const turnEndSource = runtimeSource.split('function endObservedTurn', 2)[1]
    .split('function emotionObserved', 1)[0];
assert.match(turnEndSource, /if \(refreshMode\(\) !== 'vrm'\)/);
assert.ok(
    turnEndSource.indexOf("if (refreshMode() !== 'vrm')")
        < turnEndSource.indexOf("beginTurn(turnId, source || 'lifecycle')"),
    'a turn end outside VRM mode must not create a deferred motion turn'
);
assert.ok(
    turnEndSource.indexOf('finishedTurnIds.has(String(turnId))')
        < turnEndSource.indexOf("beginTurn(turnId, source || 'lifecycle')"),
    'a stale completed turn end must not replace the current turn'
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
const waitForEmotionBlock = runtimeSource.split('function waitForOfficialEmotion(turn)', 2)[1]
    .split('function finishTurn', 1)[0];
assert.match(waitForEmotionBlock, /Promise\.resolve\(true\)/);
assert.match(waitForEmotionBlock, /resolve\(false\)/);
assert.match(runtimeSource, /function settleMissingEmotion\(turn, emotionReceived\)/);
// 每一条等待官方情绪事件的路径都必须自己处理超时，否则 emotionReady 永远是
// false，processSpeechFallback 只挂起 casualTalkPending 就返回，再没有事件来重试。
assert.equal(
    (runtimeSource.match(/await waitForOfficialEmotion\(turn\)/g) || []).length,
    (runtimeSource.match(/settleMissingEmotion\(turn, emotionReceived\);/g) || []).length,
    'every waitForOfficialEmotion() call site must settle the timeout'
);
const finishTurnBlock = runtimeSource.split('function finishTurn(turn, source)', 2)[1]
    .split('function scheduleFinishTurn', 1)[0];
assert.match(finishTurnBlock, /const emotionReceived = await waitForOfficialEmotion\(turn\)/);
assert.match(
    finishTurnBlock,
    /settleMissingEmotion\(turn, emotionReceived\);[\s\S]*await processSpeechFallback\(turn\)/
);
const cancelObservedSpeechBlock = runtimeSource.split('function cancelObservedSpeech(detail)', 2)[1]
    .split('function handleMotionLifecycleBridge', 1)[0];
assert.match(
    cancelObservedSpeechBlock,
    /const turnId = detail && detail\.turnId;[\s\S]*if \(turnId && \(!activeTurn \|\| String\(turnId\) !== activeTurn\.id\)\) return;/
);
assert.match(runtimeSource, /neko-assistant-speech-cancel'[\s\S]*cancelObservedSpeech\(detail\)/);
const modelLoadedBlock = runtimeSource.split('async function handleVrmModelLoaded()', 2)[1]
    .split("window.addEventListener('vrm-model-loaded'", 1)[0];
assert.match(
    modelLoadedBlock,
    /const emotionReceived = await waitForOfficialEmotion\(turn\)[\s\S]*settleMissingEmotion\(turn, emotionReceived\)/
);
assert.ok(
    modelLoadedBlock.indexOf("player.cancel('vrm_model_loaded', { resume: false })")
        < modelLoadedBlock.indexOf('resolveCharacterProfile()'),
    'a newly loaded model must reset stale player posture and queue state first'
);
assert.ok(
    modelLoadedBlock.indexOf('acquirePlaybackOwnership()')
        < modelLoadedBlock.indexOf('player.enterRest('),
    'a deferred VRM model load must acquire playback ownership before semantic rest'
);
const nonVrmMarker = "if (mode !== 'vrm') {";
const nonVrmParts = runtimeSource.split(nonVrmMarker);
assert.equal(nonVrmParts.length, 2, 'non-VRM turn guard must remain unique');
assert.equal(runtimeSource.includes('_nekoMotionPendingUserText'), false);
assert.equal(runtimeSource.includes('_lastSubmittedText'), false);
assert.equal(runtimeSource.includes("window.addEventListener('neko-assistant-turn-start'"), false);
assert.equal(runtimeSource.includes("window.addEventListener('neko-assistant-turn-end'"), false);
const modeSetMarker = "window.addEventListener('neko-model-manager-mode-set'";
const modeSetParts = runtimeSource.split(modeSetMarker);
assert.equal(modeSetParts.length, 2, 'mode-set listener must remain unique');
const modeSetBlock = modeSetParts[1].slice(0, 1800);
assert.match(modeSetBlock, /else \{\s*discardActiveTurn\(\);\s*releasePlaybackOwnership\(\)/);
const initializeBlock = runtimeSource.split('async function initialize()', 2)[1]
    .split('function remember', 1)[0];
assert.ok(
    initializeBlock.indexOf("if (refreshMode() !== 'vrm')")
        < initializeBlock.indexOf('acquirePlaybackOwnership()'),
    'initialization must recheck the selected mode before acquiring playback ownership'
);
assert.match(runtimeSource, /function stopMaintenanceTimers\(\)/);
assert.match(runtimeSource, /window\.addEventListener\('pagehide'/);
assert.match(runtimeSource, /window\.addEventListener\('pageshow'/);
assert.match(runtimeSource, /bindMotionLifecycleBridge\(\);\s*startMaintenanceTimers\(\)/);

const modelManagerSource = fs.readFileSync(
    path.join(root, 'static/js/model_manager/page-controller.js'),
    'utf8'
);
const indexTemplate = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
const chatTemplate = fs.readFileSync(path.join(root, 'templates/chat.html'), 'utf8');
assert.match(indexTemplate, /static\/vrm\/motion\/bridge\.js/);
assert.match(chatTemplate, /static\/vrm\/motion\/bridge\.js/);
assert.ok(
    indexTemplate.indexOf('/static/app/app-websocket.js')
        < indexTemplate.indexOf('/static/vrm/motion/bridge.js')
        && indexTemplate.indexOf('/static/vrm/motion/bridge.js')
        < indexTemplate.indexOf('/static/vrm/motion/runtime.js')
);
assert.ok(
    chatTemplate.indexOf('/static/app/app-websocket.js')
        < chatTemplate.indexOf('/static/vrm/motion/bridge.js')
        && chatTemplate.indexOf('/static/vrm/motion/bridge.js')
        < chatTemplate.indexOf('/static/app/app-buttons.js')
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
