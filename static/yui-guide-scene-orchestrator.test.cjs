const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const orchestratorPath = path.join(__dirname, 'tutorial/core/scene-orchestrator.js');
const directorSource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/director.js'), 'utf8');

test('scene orchestrator exports reusable round facade', () => {
    assert.ok(fs.existsSync(orchestratorPath), 'tutorial/core/scene-orchestrator.js should exist');
    const api = require('./tutorial/core/scene-orchestrator.js');

    assert.equal(typeof api.SceneOrchestrator, 'function');
    assert.equal(typeof api.createSceneOrchestrator, 'function');
});

test('SceneOrchestrator exposes a timeline normalizer boundary for legacy scenes', () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const orchestrator = new SceneOrchestrator({});
    const timelineScene = orchestrator.normalizeSceneToTimeline({
        id: 'day1_capsule_drag_hint',
        voiceKey: 'day1_capsule_drag_hint',
        target: 'chat-capsule-input',
        cursorAction: 'wobble'
    });

    assert.equal(timelineScene.id, 'day1_capsule_drag_hint');
    assert.equal(timelineScene.audio.voiceKey, 'day1_capsule_drag_hint');
    assert.ok(timelineScene.timeline.some((event) => (
        event.command === 'spotlight.show'
        && event.target === 'chat-capsule-input'
    )));
    assert.ok(timelineScene.timeline.some((event) => event.command === 'cursor.wobble'));
});

test('SceneOrchestrator can execute explicit timeline playback without using generic scene body', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const director = {
        sceneRunId: 0,
        currentSceneId: null,
        scenePausedForResistance: false,
        getAvatarFloatingInterruptStep() {
            return null;
        },
        shouldPreserveExternalizedChatCursor() {
            return false;
        },
        shouldPreserveIntroExternalizedChatCursor() {
            return false;
        },
        isHomeChatExternalized() {
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            },
            wobble(durationMs) {
                calls.push(['cursor:wobble', durationMs]);
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchors:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('scene-extra:clear');
        },
        clearAllVirtualSpotlights() {
            calls.push('virtual:clear');
        },
        clearSpotlightGeometryHints() {
            calls.push('geometry-hints:clear');
        },
        clearSpotlightVariantHints() {
            calls.push('variant-hints:clear');
        },
        scheduleAvatarStandInForScene(scene, day, sceneRunId) {
            calls.push(['standin:schedule', scene.id, day, sceneRunId]);
        },
        resolveAvatarFloatingSceneText(scene) {
            return scene.text || '';
        },
        speakGuideLine(text, options) {
            calls.push(['speak', text, options.voiceKey]);
            return Promise.resolve();
        },
        appendGuideChatMessage(text, options) {
            calls.push(['chat', text, options.voiceKey]);
        },
        applyGuideEmotion(emotion) {
            calls.push(['emotion', emotion]);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        applyGuideHighlights(config) {
            calls.push(['spotlight', config.key, config.primary && config.primary.id]);
        },
        moveCursorToElement(target, durationMs, options) {
            calls.push(['move', target.id, durationMs, options && options.exactDuration]);
            return Promise.resolve(true);
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve();
        },
        getGuideVoiceDurationMs() {
            return 1000;
        },
        isStopping() {
            return false;
        },
        settingsTourFlow: {
            canHandle() {
                return true;
            },
            play() {
                calls.push('settings-tour');
                return Promise.resolve(false);
            }
        }
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.playGenericScene = async () => {
        calls.push('generic');
        return true;
    };
    const keepGoing = await orchestrator.playScene({
        id: 'timeline-scene',
        text: 'timeline hello',
        voiceKey: 'voice-a',
        timelinePlayback: true,
        timeline: [
            { at: 0, command: 'chat.message', text: 'timeline hello', voiceKey: 'voice-a' },
            { at: 0, command: 'emotion.set', emotion: 'happy' },
            { at: 0, command: 'spotlight.show', key: 'timeline-scene', target: 'chat-input' },
            { at: 0, command: 'cursor.move', target: 'chat-input', durationMs: 0 },
            { at: 0, command: 'operation.run', operation: 'cleanup', target: 'chat-input', blocking: true }
        ],
        completion: {
            afterSceneDelayMs: 0
        }
    }, 7, 0, 1);

    assert.equal(keepGoing, true);
    assert.ok(!calls.includes('generic'));
    assert.ok(!calls.includes('settings-tour'));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'speak'
        && entry[1] === 'timeline hello'
        && entry[2] === 'voice-a'
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'chat'
        && entry[1] === 'timeline hello'
        && entry[2] === 'voice-a'
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'emotion'
        && entry[1] === 'happy'
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'spotlight'
        && entry[1] === 'timeline-scene'
        && entry[2] === 'chat-input'
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'move'
        && entry[1] === 'chat-input'
        && entry[2] === 0
        && entry[3] === true
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'operation'
        && entry[1] === 'cleanup'
        && entry[2] === 'chat-input'
    )));
});

test('SceneOrchestrator plays day1 intro greeting chat without waiting for intro operation completion', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const revealCalls = [];
    const director = {
        sceneRunId: 0,
        currentSceneId: null,
        scenePausedForResistance: false,
        getAvatarFloatingInterruptStep() {
            return null;
        },
        shouldPreserveExternalizedChatCursor() {
            return false;
        },
        shouldPreserveIntroExternalizedChatCursor() {
            return false;
        },
        isHomeChatExternalized() {
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchors:clear');
            }
        },
        clearSceneTimers() {},
        clearSceneExtraSpotlights() {},
        clearAllVirtualSpotlights() {},
        clearSpotlightGeometryHints() {},
        clearSpotlightVariantHints() {},
        clearExternalizedChatGuideTarget() {},
        overlay: {
            setAngry() {}
        },
        enableInterrupts() {},
        resolveAvatarFloatingSceneText(scene) {
            return scene.text || '';
        },
        resolveAvatarFloatingSceneVoiceKey(scene) {
            return scene.voiceKey || '';
        },
        appendGuideChatMessage(text, options) {
            calls.push(['chat', text, options.voiceKey]);
        },
        speakGuideLine(text, options) {
            calls.push(['speak', text, options.voiceKey]);
            return Promise.resolve();
        },
        applyGuideEmotion(emotion) {
            calls.push(['emotion', emotion]);
        },
        prepareAvatarFloatingScene() {
            calls.push('scene:prepare');
            return Promise.resolve(true);
        },
        resolveAvatarFloatingSelector(target) {
            return { id: target };
        },
        setSpotlightGeometryHint() {},
        applyGuideHighlights() {},
        runAvatarFloatingSceneOperation(scene, primaryTarget, narrationStartedAt, narrationPromise, operationContext) {
            calls.push([
                'operation',
                scene.operation,
                operationContext && operationContext.isFirstDailyScene,
                typeof (operationContext && operationContext.revealPrepared)
            ]);
            if (operationContext && typeof operationContext.revealPrepared === 'function') {
                operationContext.revealPrepared('test-reveal');
            }
            calls.push('operation:pending');
            return new Promise(() => {});
        },
        waitForSceneDelay() {
            return Promise.resolve();
        },
        getGuideVoiceDurationMs() {
            return 1000;
        },
        isStopping() {
            return false;
        }
    };
    const orchestrator = new SceneOrchestrator(director);

    const keepGoingPromise = orchestrator.playScene({
        id: 'day1_intro_greeting',
        text: '微风、阳光、还有刚刚好...',
        voiceKey: 'intro_greeting_reply',
        emotion: 'happy',
        timelinePlayback: true,
        timeline: [
            { at: 0, command: 'operation.run', operation: 'day1-intro-greeting-performance', blocking: false },
            { at: 0, command: 'chat.message' },
            { at: 0, command: 'emotion.set' }
        ],
        completion: {
            afterSceneDelayMs: 0
        }
    }, 1, 1, 9, {
        revealPrepared: (reason) => revealCalls.push(reason)
    });
    const keepGoing = await Promise.race([
        keepGoingPromise,
        new Promise((resolve) => setTimeout(() => resolve('blocked-by-operation'), 30))
    ]);

    assert.equal(keepGoing, true);
    assert.deepEqual(revealCalls, ['test-reveal']);
    assert.ok(calls.indexOf('operation:pending') !== -1);
    assert.ok(calls.findIndex((entry) => Array.isArray(entry) && entry[0] === 'chat') > calls.indexOf('operation:pending'));
    assert.deepEqual(calls.filter((entry) => Array.isArray(entry) && entry[0] === 'operation'), [
        ['operation', 'day1-intro-greeting-performance', false, 'function']
    ]);
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'chat'
        && entry[1] === '微风、阳光、还有刚刚好...'
        && entry[2] === 'intro_greeting_reply'
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'speak'
        && entry[1] === '微风、阳光、还有刚刚好...'
        && entry[2] === 'intro_greeting_reply'
    )));
});

test('SceneOrchestrator timeline audio uses director-resolved narration', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const scene = {
        id: 'day2_intro_context',
        text: '昨天默认台词',
        voiceKey: 'avatar_floating_day2_intro'
    };
    const timelineScene = {
        audio: {
            text: '昨天默认台词',
            voiceKey: 'avatar_floating_day2_intro'
        }
    };
    const director = {
        resolveAvatarFloatingSceneText(inputScene) {
            calls.push(['text', inputScene.id]);
            return '嘿嘿分支台词';
        },
        resolveAvatarFloatingSceneVoiceKey(inputScene) {
            calls.push(['voice', inputScene.id]);
            return 'avatar_floating_day2_intro_voice_used';
        },
        speakGuideLine(text, options) {
            calls.push(['speak', text, options.voiceKey]);
            return Promise.resolve();
        }
    };
    const orchestrator = new SceneOrchestrator(director);
    const audioRuntime = orchestrator.createTimelineAudioRuntime(scene, timelineScene, {});

    audioRuntime.play('avatar_floating_day2_intro', timelineScene.audio);
    await audioRuntime.waitForEnd();

    assert.deepEqual(calls, [
        ['text', 'day2_intro_context'],
        ['voice', 'day2_intro_context'],
        ['speak', '嘿嘿分支台词', 'avatar_floating_day2_intro_voice_used']
    ]);
});

test('SceneOrchestrator can execute legacy scene fields through timeline playback normalizer', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const director = {
        sceneRunId: 0,
        currentSceneId: 'previous-scene',
        scenePausedForResistance: false,
        getAvatarFloatingInterruptStep() {
            return null;
        },
        shouldPreserveExternalizedChatCursor() {
            return false;
        },
        shouldPreserveIntroExternalizedChatCursor() {
            return false;
        },
        isHomeChatExternalized() {
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchors:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('scene-extra:clear');
        },
        clearAllVirtualSpotlights() {
            calls.push('virtual:clear');
        },
        clearSpotlightGeometryHints() {
            calls.push('geometry-hints:clear');
        },
        clearSpotlightVariantHints() {
            calls.push('variant-hints:clear');
        },
        prepareAvatarFloatingScene(scene) {
            calls.push(['prepare', scene.id, scene.operation]);
            return Promise.resolve();
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        resolveAvatarFloatingSceneText(scene) {
            return scene.text || '';
        },
        speakGuideLine(text, options) {
            calls.push(['speak', text, options.voiceKey]);
            return Promise.resolve();
        },
        appendGuideChatMessage(text, options) {
            calls.push(['chat', text, options.voiceKey]);
        },
        applyGuideEmotion(emotion) {
            calls.push(['emotion', emotion]);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        applyGuideHighlights(config) {
            calls.push(['spotlight', config.key, config.primary && config.primary.id]);
        },
        moveCursorToElement(target, durationMs) {
            calls.push(['move', target.id, durationMs]);
            return Promise.resolve(true);
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve();
        },
        getGuideVoiceDurationMs() {
            return 1000;
        },
        isStopping() {
            return false;
        }
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.playGenericScene = async () => {
        calls.push('generic');
        return true;
    };
    const keepGoing = await orchestrator.playScene({
        id: 'day7_memory_control',
        timelinePlayback: true,
        text: 'memory line',
        voiceKey: 'voice-memory',
        emotion: 'happy',
        target: '#${p}-menu-memory',
        cursorAction: 'move',
        cursorStartMs: 0,
        cursorMoveDurationMs: 0,
        operation: 'show-settings-menu:memory',
        afterSceneDelayMs: 0
    }, 7, 1, 3);

    assert.equal(keepGoing, true);
    assert.ok(!calls.includes('generic'));
    assert.ok(calls.some((entry) => Array.isArray(entry) && entry[0] === 'prepare' && entry[2] === 'show-settings-menu:memory'));
    assert.ok(calls.some((entry) => Array.isArray(entry) && entry[0] === 'interrupts'));
    assert.ok(calls.some((entry) => Array.isArray(entry) && entry[0] === 'chat' && entry[1] === 'memory line'));
    assert.ok(calls.some((entry) => Array.isArray(entry) && entry[0] === 'emotion' && entry[1] === 'happy'));
    assert.ok(calls.some((entry) => Array.isArray(entry) && entry[0] === 'spotlight' && entry[2] === '#${p}-menu-memory'));
    assert.ok(calls.some((entry) => Array.isArray(entry) && entry[0] === 'move' && entry[1] === '#${p}-menu-memory'));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'operation'
        && entry[1] === 'show-settings-menu:memory'
        && entry[2] === '#${p}-menu-memory'
    )));
});

test('SceneOrchestrator routes timeline graduation wrap through the petal cue handler', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const director = {
        sceneRunId: 0,
        currentSceneId: 'day7_memory_control',
        scenePausedForResistance: false,
        getAvatarFloatingInterruptStep() {
            return null;
        },
        shouldPreserveExternalizedChatCursor() {
            return false;
        },
        shouldPreserveIntroExternalizedChatCursor() {
            return false;
        },
        isHomeChatExternalized() {
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchors:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('scene-extra:clear');
        },
        clearAllVirtualSpotlights() {
            calls.push('virtual:clear');
        },
        clearSpotlightGeometryHints() {
            calls.push('geometry-hints:clear');
        },
        clearSpotlightVariantHints() {
            calls.push('variant-hints:clear');
        },
        prepareAvatarFloatingScene(scene) {
            calls.push(['prepare', scene.id, scene.operation]);
            return Promise.resolve();
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        resolveAvatarFloatingSceneText(scene) {
            return scene.text || '';
        },
        speakGuideLine(text, options) {
            calls.push(['speak', text, options.voiceKey]);
            return Promise.resolve();
        },
        appendGuideChatMessage(text, options) {
            calls.push(['chat', text, options.voiceKey]);
        },
        applyGuideEmotion(emotion) {
            calls.push(['emotion', emotion]);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        applyGuideHighlights(config) {
            calls.push(['spotlight', config.key, config.primary && config.primary.id]);
        },
        moveCursorToElement(target, durationMs) {
            calls.push(['move', target.id, durationMs]);
            return Promise.resolve(true);
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        },
        playAvatarFloatingPetalTransitionAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt) {
            calls.push(['petal', scene.id, sceneRunId, voiceKey, text, Number.isFinite(narrationStartedAt)]);
            return Promise.resolve(true);
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve();
        },
        getGuideVoiceDurationMs() {
            return 4;
        },
        isStopping() {
            return false;
        }
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.playGenericScene = async () => {
        calls.push('generic');
        return true;
    };
    const keepGoing = await orchestrator.playScene({
        id: 'day7_graduation_wrap',
        timelinePlayback: true,
        text: 'wrap line',
        voiceKey: 'voice-wrap',
        emotion: 'happy',
        target: 'chat-input',
        cursorAction: 'move',
        cursorStartMs: 1,
        cursorMoveDurationMs: 0,
        operation: 'cleanup',
        petalTransition: true,
        afterSceneDelayMs: 0
    }, 7, 2, 3);

    assert.equal(keepGoing, true);
    assert.ok(!calls.includes('generic'));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'operation'
        && entry[1] === 'cleanup'
        && entry[2] === 'chat-input'
    )));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'petal'
        && entry[1] === 'day7_graduation_wrap'
        && entry[2] === 1
        && entry[3] === 'voice-wrap'
        && entry[4] === 'wrap line'
        && entry[5] === true
    )));
});

test('SceneOrchestrator owns round setup, scene loop, metrics and cleanup', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const director = {
        destroyed: false,
        getAvatarFloatingRoundConfig(round) {
            calls.push(['config', round]);
            return {
                scenes: [
                    { id: 'a' },
                    { id: 'b' }
                ]
            };
        },
        recordExperienceMetric(name, payload) {
            calls.push(['metric', name, payload.round]);
        },
        overlay: {
            hideBubble() {
                calls.push('bubble:hide');
            },
            clearPersistentSpotlight() {
                calls.push('spotlight:persistent');
            },
            clearActionSpotlight() {
                calls.push('spotlight:action');
            }
        },
        ensureAvatarFloatingGuideSurfaceReady(round) {
            calls.push(['surface', round]);
            return Promise.resolve();
        },
        setGuideChatInputLocked(locked, reason) {
            calls.push(['input', locked, reason]);
        },
        setCompactToolWheelIndexForGuide(index, reason) {
            calls.push(['wheel', index, reason]);
        },
        isHomeChatExternalized() {
            return false;
        },
        highlightChatWindow() {
            calls.push('highlight:chat');
        },
        withLookAt(options, run) {
            calls.push(['lookAt', options.completeReason]);
            return run();
        },
        isStopping() {
            return false;
        },
        playAvatarFloatingScene(scene, round, index, total) {
            calls.push(['scene', scene.id, round, index, total]);
            return Promise.resolve(true);
        },
        disableInterrupts() {
            calls.push('interrupts:disable');
        },
        clearAvatarStandIn(options) {
            calls.push(['standIn:clear', options.clearPending, options.restoreModel]);
        },
        closeAvatarFloatingGuidePanels(options) {
            calls.push(['panels:close', options.clearCursor]);
            return Promise.resolve();
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearAllExtraSpotlights() {
            calls.push('spotlight:extra');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        cursor: {
            hide() {
                calls.push('cursor:hide');
            }
        },
        setTutorialTakingOver(active) {
            calls.push(['takeover', active]);
        }
    };
    const orchestrator = new SceneOrchestrator(director);

    const completed = await orchestrator.playRound(2, { source: 'test' });

    assert.equal(completed, true);
    assert.deepEqual(calls, [
        ['config', 2],
        ['metric', 'avatar_floating_round_start', 2],
        'bubble:hide',
        ['surface', 2],
        ['input', true, 'avatar-floating-guide-day2'],
        ['wheel', 0, 'avatar-floating-guide-day2-entry-reset'],
        'spotlight:persistent',
        ['lookAt', 'avatar_floating_day2_complete'],
        ['scene', 'a', 2, 0, 2],
        ['scene', 'b', 2, 1, 2],
        ['metric', 'avatar_floating_round_complete', 2],
        'interrupts:disable',
        ['standIn:clear', true, true],
        ['input', false, 'avatar-floating-guide-day2-complete'],
        ['panels:close', true],
        'spotlight:virtual',
        'spotlight:extra',
        'spotlight:geometry',
        'spotlight:variant',
        'spotlight:persistent',
        'spotlight:action',
        'cursor:hide',
        ['takeover', false]
    ]);
});

test('SceneOrchestrator records Day1 end only after Day1 round completes', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const director = {
        destroyed: false,
        day1RoundWakeupCompleted: false,
        getAvatarFloatingRoundConfig(round) {
            calls.push(['config', round]);
            return { scenes: [{ id: 'day1_capsule_drag_hint' }] };
        },
        recordExperienceMetric(name, payload) {
            calls.push(['metric', name, payload.round]);
        },
        overlay: {
            hideBubble() {},
            clearPersistentSpotlight() {},
            clearActionSpotlight() {}
        },
        ensureAvatarFloatingGuideSurfaceReady() {
            return Promise.resolve();
        },
        setGuideChatInputLocked() {},
        isHomeChatExternalized() {
            return false;
        },
        ensurePersistentGhostCursorLookAtPerformance() {
            return Promise.resolve(null);
        },
        stopPersistentGhostCursorLookAtPerformance() {
            return Promise.resolve();
        },
        isStopping() {
            return false;
        },
        playAvatarFloatingScene(scene, round) {
            calls.push(['scene', scene.id, round]);
            return Promise.resolve(true);
        },
        recordAvatarFloatingGuideRoundEnd(round) {
            calls.push(['round-end', round]);
        },
        disableInterrupts() {},
        clearAvatarStandIn() {},
        closeAvatarFloatingGuidePanels() {
            return Promise.resolve();
        },
        clearAllVirtualSpotlights() {},
        clearAllExtraSpotlights() {},
        clearSpotlightGeometryHints() {},
        clearSpotlightVariantHints() {},
        cursor: {
            hide() {}
        },
        setTutorialTakingOver() {}
    };
    const orchestrator = new SceneOrchestrator(director);

    const completed = await orchestrator.playRound(1, { source: 'test' });

    assert.equal(completed, true);
    assert.deepEqual(calls, [
        ['config', 1],
        ['metric', 'avatar_floating_round_start', 1],
        ['scene', 'day1_capsule_drag_hint', 1],
        ['metric', 'avatar_floating_round_complete', 1],
        ['round-end', 1]
    ]);
});

test('SceneOrchestrator waits for angry exit presentation before round cleanup', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    let releaseAngryExit;
    const angryExitPromise = new Promise((resolve) => {
        releaseAngryExit = resolve;
    });
    const director = {
        destroyed: false,
        angryExitTriggered: false,
        getAvatarFloatingRoundConfig() {
            return { scenes: [{ id: 'a' }] };
        },
        recordExperienceMetric() {},
        overlay: {
            hideBubble() {},
            clearPersistentSpotlight() {
                calls.push('spotlight:persistent');
            },
            clearActionSpotlight() {
                calls.push('spotlight:action');
            }
        },
        ensureAvatarFloatingGuideSurfaceReady() {
            return Promise.resolve();
        },
        setGuideChatInputLocked(locked) {
            calls.push(['input', locked]);
        },
        setCompactToolWheelIndexForGuide() {},
        isHomeChatExternalized() {
            return false;
        },
        withLookAt(options, run) {
            return run();
        },
        isStopping() {
            return false;
        },
        playAvatarFloatingScene() {
            this.angryExitTriggered = true;
            return Promise.resolve(false);
        },
        waitForAngryExitPresentationCompletion() {
            calls.push('angry:wait');
            return angryExitPromise;
        },
        disableInterrupts() {
            calls.push('interrupts:disable');
        },
        clearAvatarStandIn() {
            calls.push('standIn:clear');
        },
        closeAvatarFloatingGuidePanels() {
            calls.push('panels:close');
            return Promise.resolve();
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearAllExtraSpotlights() {
            calls.push('spotlight:extra');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        cursor: {
            hide() {
                calls.push('cursor:hide');
            }
        },
        setTutorialTakingOver(active) {
            calls.push(['takeover', active]);
        }
    };
    const orchestrator = new SceneOrchestrator(director);

    const roundPromise = orchestrator.playRound(2, { source: 'test' });
    await Promise.resolve();
    await Promise.resolve();

    assert.deepEqual(calls, [
        ['input', true],
        'spotlight:persistent',
        'angry:wait'
    ]);

    releaseAngryExit();
    await roundPromise;
    assert.ok(calls.indexOf('angry:wait') < calls.indexOf('interrupts:disable'));
});

test('SceneOrchestrator owns scene prelude and delegates generic scene body', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const scene = { id: 'day6_generic', target: 'chat-window' };
    const director = {
        sceneRunId: 41,
        currentSceneId: 'previous-scene',
        getAvatarFloatingInterruptStep(inputScene) {
            calls.push(['step', inputScene.id]);
            return { id: 'step' };
        },
        shouldPreserveExternalizedChatCursor(previousSceneId, inputScene) {
            calls.push(['preserve:external', previousSceneId, inputScene.id]);
            return false;
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return true;
        },
        shouldPreserveIntroExternalizedChatCursor(inputScene) {
            calls.push(['preserve:intro', inputScene.id]);
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchor:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('spotlight:extra');
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        clearExternalizedChatGuideTarget() {
            calls.push('externalized:clear');
        },
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.playGenericScene = function (inputScene, day, index, total, context) {
        calls.push([
            'core',
            inputScene.id,
            day,
            index,
            total,
            context.sceneRunId,
            context.previousSceneId,
            context.isFirstDailyScene,
            context.preserveExternalizedChatGuideTarget,
            context.preserveIntroExternalizedChatGuideTarget
        ]);
        return Promise.resolve('kept-going');
    };

    const result = await orchestrator.playScene(scene, 6, 0, 8);

    assert.equal(result, 'kept-going');
    assert.equal(director.sceneRunId, 42);
    assert.equal(director.currentSceneId, 'day6_generic');
    assert.deepEqual(director.currentStep, { id: 'step' });
    assert.deepEqual(calls, [
        ['step', 'day6_generic'],
        ['preserve:external', 'previous-scene', 'day6_generic'],
        'externalized?',
        ['preserve:intro', 'day6_generic'],
        'cursor:cancel',
        'anchor:clear',
        'timers:clear',
        ['angry', false],
        'spotlight:extra',
        'spotlight:virtual',
        'spotlight:geometry',
        'spotlight:variant',
        'externalized?',
        'externalized:clear',
        [
            'core',
            'day6_generic',
            6,
            0,
            8,
            42,
            'previous-scene',
            true,
            false,
            false
        ]
    ]);
});

test('SceneOrchestrator places the first daily guide cursor in the capsule input before the first line moves', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const scene = { id: 'day6_intro_agent', target: 'chat-window' };
    const director = {
        sceneRunId: 8,
        currentSceneId: 'previous-day',
        getAvatarFloatingInterruptStep() {
            return null;
        },
        shouldPreserveExternalizedChatCursor() {
            return false;
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return true;
        },
        shouldPreserveIntroExternalizedChatCursor() {
            return true;
        },
        isAvatarFloatingInputIntroScene(inputScene) {
            calls.push(['input-intro?', inputScene.id]);
            return true;
        },
        interactionTakeover: {
            setExternalizedChatCursor(kind, options) {
                calls.push(['cursor:externalized', kind, options.effect, options.durationMs]);
            }
        },
        hideHomeCursorForExternalizedChat() {
            calls.push('cursor:home-hide');
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchor:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('spotlight:extra');
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        clearExternalizedChatGuideTarget() {
            calls.push('externalized:clear');
        }
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.playGenericScene = function () {
        calls.push('core');
        return Promise.resolve(true);
    };

    const result = await orchestrator.playScene(scene, 6, 0, 8);

    assert.equal(result, true);
    assert.ok(calls.includes('core'));
    assert.ok(!calls.includes('externalized:clear'));
    assert.ok(calls.some((entry) => (
        Array.isArray(entry)
        && entry[0] === 'cursor:externalized'
        && entry[1] === 'capsule-input'
        && entry[2] === ''
        && entry[3] === 0
    )));
    assert.ok(calls.indexOf('cursor:home-hide') < calls.indexOf('core'));
});

test('SceneOrchestrator preserves Day1 input-origin wobble during the first externalized cursor handoff', () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const scene = { id: 'day1_intro_activation', cursorAction: 'input-origin' };
    const director = {
        isAvatarFloatingInputIntroScene() {
            return true;
        },
        isHomeChatExternalized() {
            return true;
        },
        getAvatarFloatingIntroExternalizedSpotlightKind() {
            return 'capsule-input';
        },
        getAvatarFloatingIntroExternalizedCursorOptions(inputScene) {
            calls.push(['options', inputScene.id]);
            return { effect: 'wobble', durationMs: 0 };
        },
        interactionTakeover: {
            setExternalizedChatCursor(kind, options) {
                calls.push(['cursor', kind, options.effect, options.durationMs]);
            }
        },
        hideHomeCursorForExternalizedChat() {
            calls.push('hide-home-cursor');
        }
    };
    const orchestrator = new SceneOrchestrator(director);

    const applied = orchestrator.applyFirstDailySceneIntroCursorPrelude(scene, {
        isFirstDailyScene: true
    });

    assert.equal(applied, true);
    assert.deepEqual(calls, [
        ['options', 'day1_intro_activation'],
        ['cursor', 'capsule-input', 'wobble', 0],
        'hide-home-cursor'
    ]);
});

test('SceneOrchestrator schedules avatar stand-ins after scene surface preparation', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const director = {
        sceneRunId: 21,
        prepareAvatarFloatingScene(scene, options) {
            calls.push(['prepare', scene.id, options.preserveExternalizedChatGuideTarget]);
            return Promise.resolve();
        },
        scheduleAvatarStandInForScene(scene, day, sceneRunId) {
            calls.push(['standIn:schedule', scene.id, day, sceneRunId]);
        },
        isStopping() {
            return false;
        }
    };
    const orchestrator = new SceneOrchestrator(director);

    const result = await orchestrator.prepareGenericSceneSurface({
        id: 'day2_avatar_tools'
    }, {
        day: 2,
        sceneRunId: 21,
        isFirstDailyScene: false,
        preserveExternalizedChatGuideTarget: true
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day2_avatar_tools', true],
        ['standIn:schedule', 'day2_avatar_tools', 2, 21]
    ]);
});

test('SceneOrchestrator waits for resistance resume before clearing externalized spotlight', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    let resumeScene;
    const scene = { id: 'day6_next', target: 'chat-window' };
    const director = {
        sceneRunId: 8,
        currentSceneId: 'day6_wrap_cleanup',
        scenePausedForResistance: true,
        waitUntilSceneResumed() {
            calls.push('wait:resistance');
            return new Promise((resolve) => {
                resumeScene = () => {
                    this.scenePausedForResistance = false;
                    calls.push('resume:resistance');
                    resolve();
                };
            });
        },
        isStopping() {
            return false;
        },
        getAvatarFloatingInterruptStep(inputScene) {
            calls.push(['step', inputScene.id]);
            return { id: 'step' };
        },
        shouldPreserveExternalizedChatCursor(previousSceneId, inputScene) {
            calls.push(['preserve:external', previousSceneId, inputScene.id]);
            return false;
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return true;
        },
        shouldPreserveIntroExternalizedChatCursor(inputScene) {
            calls.push(['preserve:intro', inputScene.id]);
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchor:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('spotlight:extra');
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        clearExternalizedChatGuideTarget() {
            calls.push('externalized:clear');
        },
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.playGenericScene = function (inputScene, day, index, total, context) {
        calls.push(['core', inputScene.id, day, index, total, context.sceneRunId]);
        return Promise.resolve('played');
    };

    const resultPromise = orchestrator.playScene(scene, 6, 3, 8);
    await Promise.resolve();
    await Promise.resolve();

    assert.deepEqual(calls, ['wait:resistance']);
    assert.equal(director.sceneRunId, 8);
    assert.equal(director.currentSceneId, 'day6_wrap_cleanup');

    resumeScene();
    const result = await resultPromise;

    assert.equal(result, 'played');
    assert.deepEqual(calls, [
        'wait:resistance',
        'resume:resistance',
        ['step', 'day6_next'],
        ['preserve:external', 'day6_wrap_cleanup', 'day6_next'],
        'timers:clear',
        ['angry', false],
        'spotlight:extra',
        'spotlight:virtual',
        'spotlight:geometry',
        'spotlight:variant',
        'externalized?',
        'externalized:clear',
        ['core', 'day6_next', 6, 3, 8, 9]
    ]);
});

test('SceneOrchestrator routes Day1 intro scenes through explicit timeline playback', async () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const scene = {
        id: 'day1_intro_activation',
        operation: 'day1-intro-activation-flow',
        timelinePlayback: true
    };
    const director = {
        sceneRunId: 6,
        currentSceneId: 'before-day1',
        getAvatarFloatingInterruptStep(inputScene) {
            calls.push(['step', inputScene.id]);
            return { id: 'intro-step' };
        },
        shouldPreserveExternalizedChatCursor(previousSceneId, inputScene) {
            calls.push(['preserve:external', previousSceneId, inputScene.id]);
            return false;
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return false;
        },
        shouldPreserveIntroExternalizedChatCursor(inputScene) {
            calls.push(['preserve:intro', inputScene.id]);
            return false;
        },
        cursor: {
            cancel() {
                calls.push('cursor:cancel');
            }
        },
        cursorAnchorStore: {
            clear() {
                calls.push('anchor:clear');
            }
        },
        clearSceneTimers() {
            calls.push('timers:clear');
        },
        overlay: {
            setAngry(value) {
                calls.push(['angry', value]);
            }
        },
        clearSceneExtraSpotlights() {
            calls.push('spotlight:extra');
        },
        clearAllVirtualSpotlights() {
            calls.push('spotlight:virtual');
        },
        clearSpotlightGeometryHints() {
            calls.push('spotlight:geometry');
        },
        clearSpotlightVariantHints() {
            calls.push('spotlight:variant');
        },
        clearExternalizedChatGuideTarget() {
            calls.push('externalized:clear');
        }
    };
    const orchestrator = new SceneOrchestrator(director);
    orchestrator.canPlayTimelineScene = function (inputScene) {
        calls.push(['can:timeline', inputScene.id]);
        return true;
    };
    orchestrator.playTimelineScene = function (inputScene, day, index, total, context) {
        calls.push(['timeline', inputScene.id, day, index, total, context.sceneRunId]);
        return Promise.resolve('timeline-played');
    };
    orchestrator.playGenericScene = function () {
        calls.push('generic');
        return Promise.resolve('generic');
    };

    const result = await orchestrator.playScene(scene, 1, 0, 9);

    assert.equal(result, 'timeline-played');
    assert.equal(director.sceneRunId, 7);
    assert.deepEqual(calls, [
        ['step', 'day1_intro_activation'],
        ['preserve:external', 'before-day1', 'day1_intro_activation'],
        'externalized?',
        'cursor:cancel',
        'anchor:clear',
        'timers:clear',
        ['angry', false],
        'spotlight:extra',
        'spotlight:virtual',
        'spotlight:geometry',
        'spotlight:variant',
        'externalized?',
        ['can:timeline', 'day1_intro_activation'],
        ['timeline', 'day1_intro_activation', 1, 0, 9, 7]
    ]);
});

test('SceneOrchestrator prepares generic scene narration', () => {
    const { SceneOrchestrator } = require('./tutorial/core/scene-orchestrator.js');
    const calls = [];
    const scene = {
        id: 'narration-scene',
        textKey: 'guide.line',
        buttons: [{ id: 'ok' }]
    };
    const director = {
        resolveAvatarFloatingSceneText(inputScene) {
            calls.push(['text', inputScene.id]);
            return 'hello';
        },
        resolveAvatarFloatingSceneVoiceKey(inputScene) {
            calls.push(['voice', inputScene.id]);
            return 'voice-key';
        },
        getAvatarFloatingSceneButtons(inputScene) {
            calls.push(['buttons', inputScene.id]);
            return inputScene.buttons;
        },
        installGuideMessageActionHandler() {
            calls.push('handler');
            return true;
        },
        beginGuideMessageActionWait(buttons, timeoutMs) {
            calls.push(['wait', buttons.length, timeoutMs]);
            return Promise.resolve('clicked');
        },
        appendGuideChatMessage(text, options) {
            calls.push(['message', text, options.textKey, options.voiceKey, options.buttons.length]);
        },
        resolveAvatarFloatingSceneEmotion(inputScene) {
            calls.push(['emotion', inputScene.id]);
            return 'happy';
        },
        applyGuideEmotion(emotion) {
            calls.push(['applyEmotion', emotion]);
        }
    };
    const orchestrator = new SceneOrchestrator(director);

    const narration = orchestrator.prepareSceneNarration(scene);

    assert.equal(narration.text, 'hello');
    assert.equal(narration.voiceKey, 'voice-key');
    assert.deepEqual(narration.sceneButtons, scene.buttons);
    assert.equal(narration.canHandleSceneButtons, true);
    assert.equal(narration.actionWaitPromise instanceof Promise, true);
    assert.deepEqual(calls, [
        ['text', 'narration-scene'],
        ['voice', 'narration-scene'],
        ['buttons', 'narration-scene'],
        'handler',
        ['wait', 1, 0],
        ['message', 'hello', 'guide.line', 'voice-key', 1],
        ['emotion', 'narration-scene'],
        ['applyEmotion', 'happy']
    ]);
});

test('director delegates avatar floating round playback to SceneOrchestrator', () => {
    const constructorBlock = directorSource.split('constructor(options) {')[1].split(
        'this.page = options.page',
        1
    )[0];
    const playRoundBlock = directorSource.split('        async playAvatarFloatingRound(round, options) {')[1].split(
        '        disableInterrupts() {',
        1
    )[0];

    assert.match(directorSource, /const TutorialSceneOrchestrator = window\.TutorialSceneOrchestrator \|\| \{\};/);
    assert.match(constructorBlock, /this\.sceneOrchestrator = new TutorialSceneOrchestrator\.SceneOrchestrator\(this\);/);
    assert.match(playRoundBlock, /return this\.sceneOrchestrator\.playRound\(round,\s*options\);/);
    assert.doesNotMatch(playRoundBlock, /for \(let index = 0; index < config\.scenes\.length; index \+= 1\)/);
});

test('director delegates avatar floating scene playback to SceneOrchestrator', () => {
    const orchestratorSource = fs.readFileSync(orchestratorPath, 'utf8');
    const playSceneBlock = directorSource.split('        async playAvatarFloatingScene(scene, day, index, total, roundContext) {')[1].split(
        '        async playAvatarFloatingRound(round, options) {',
        1
    )[0];
    const orchestratorPlaySceneBlock = orchestratorSource.split('        async playScene(scene, day, index, total, roundContext = {}) {')[1].split(
        '        async prepareGenericSceneSurface(scene, context) {',
        1
    )[0];

    assert.match(playSceneBlock, /return this\.sceneOrchestrator\.playScene\(scene,\s*day,\s*index,\s*total,\s*roundContext\);/);
    assert.doesNotMatch(playSceneBlock, /const sceneRunId = \+\+this\.sceneRunId;/);
    assert.doesNotMatch(playSceneBlock, /scheduleAvatarStandInForScene/);
    assert.match(orchestratorSource, /director\.scheduleAvatarStandInForScene\(scene,\s*context\.day,\s*sceneRunId\);/);
    assert.match(orchestratorPlaySceneBlock, /director\.settingsTourFlow\.canHandle\(scene\)/);
    assert.match(orchestratorPlaySceneBlock, /director\.settingsTourFlow\.play\(scene,\s*\{/);
    assert.doesNotMatch(orchestratorPlaySceneBlock, /day4_chat_settings/);
    assert.doesNotMatch(orchestratorPlaySceneBlock, /day5_character_settings/);
    assert.doesNotMatch(orchestratorPlaySceneBlock, /day2_personalization_detail/);
});

test('SceneOrchestrator owns generic scene core instead of Director', () => {
    const orchestratorSource = fs.readFileSync(orchestratorPath, 'utf8');
    assert.match(orchestratorSource, /async playGenericScene\(scene, day, index, total, context\)/);
    const playSceneBlock = orchestratorSource.split('        async playScene(scene, day, index, total, roundContext = {}) {')[1].split(
        '        async playGenericScene(scene, day, index, total, context) {',
        1
    )[0];
    const genericSceneBlock = orchestratorSource.split('        async playGenericScene(scene, day, index, total, context) {')[1].split(
        '        async playRound(round, options) {',
        1
    )[0];

    assert.doesNotMatch(directorSource, /async playAvatarFloatingSceneCore\(scene, day, index, total, context\)/);
    assert.match(playSceneBlock, /return await this\.playGenericScene\(scene,\s*day,\s*index,\s*total,\s*\{/);
    assert.match(genericSceneBlock, /const narration = this\.prepareSceneNarration\(scene\);/);
    assert.match(genericSceneBlock, /let sceneTargets = await this\.resolveAndApplySceneSpotlight\(scene,\s*context\);/);
    assert.match(genericSceneBlock, /const playback = this\.createScenePlaybackPromises\(scene,\s*context,\s*narration\);/);
    assert.match(genericSceneBlock, /sceneTargets = await this\.completeIntroSpotlightIfNeeded\([\s\S]*scene,[\s\S]*index,[\s\S]*total,[\s\S]*context,[\s\S]*narration,[\s\S]*playback,[\s\S]*sceneTargets[\s\S]*\);/);
    assert.match(genericSceneBlock, /const cursorCompleted = await this\.runSceneCursorAndOperation\([\s\S]*scene,[\s\S]*context,[\s\S]*sceneTargets,[\s\S]*playback\.narrationStartedAt[\s\S]*\);/);
    assert.match(genericSceneBlock, /return await this\.finishGenericScene\(scene,\s*index,\s*total,\s*context,\s*narration,\s*playback\);/);
    assert.doesNotMatch(genericSceneBlock, /director\.runAvatarFloatingSceneOperation\(scene,\s*primaryTarget,\s*narrationStartedAt\)/);
    assert.doesNotMatch(genericSceneBlock, /director\.moveAvatarFloatingCursor\(scene,\s*cursorTarget \|\| primaryTarget,\s*secondaryTarget,\s*previousSceneId/);
});

test('full tutorial pages load scene orchestrator before director', () => {
    for (const templatePath of [
        'templates/index.html'
    ]) {
        const source = fs.readFileSync(path.join(repoRoot, templatePath), 'utf8');
        const settingsTourFlowIndex = source.indexOf('/static/tutorial/core/settings-tour-flow.js');
        const orchestratorIndex = source.indexOf('/static/tutorial/core/scene-orchestrator.js');
        const directorIndex = source.indexOf('/static/tutorial/yui-guide/director.js');

        assert.notEqual(settingsTourFlowIndex, -1, templatePath + ' should load tutorial/core/settings-tour-flow.js');
        assert.notEqual(orchestratorIndex, -1, templatePath + ' should load tutorial/core/scene-orchestrator.js');
        assert.notEqual(directorIndex, -1, templatePath + ' should load tutorial/yui-guide/director.js');
        assert.ok(settingsTourFlowIndex < orchestratorIndex, templatePath + ' should load settings tour flow before scene orchestrator');
        assert.ok(orchestratorIndex < directorIndex, templatePath + ' should load scene orchestrator before director');
    }
});
