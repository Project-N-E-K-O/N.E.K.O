const assert = require('node:assert/strict');
const test = require('node:test');

const { CommandRegistry } = require('./tutorial/core/command-registry.js');
const { createTutorialVisualRuntime } = require('./tutorial/core/visual-runtime.js');

test('VisualRuntime registers timeline command handlers against director APIs', async () => {
    const calls = [];
    const director = {
        resolveAvatarFloatingSceneText(scene) {
            return scene.text || '';
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
        clickCursorAndWait(durationMs) {
            calls.push(['click', durationMs]);
            return Promise.resolve(true);
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push([
                'operation',
                scene.operation,
                primaryTarget && primaryTarget.id,
                scene.preserveExternalizedChatGuideTarget === true
            ]);
            return Promise.resolve(true);
        },
        cursor: {
            wobble(durationMs) {
                calls.push(['wobble', durationMs]);
            }
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve();
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    assert.equal(runtime.registerCommands(registry), true);
    await registry.dispatch({ command: 'chat.message', text: 'hello', voiceKey: 'voice-a' }, {
        scene: { id: 'scene-a', legacyScene: { text: 'hello' } },
        director
    });
    await registry.dispatch({ command: 'emotion.set', emotion: 'happy' }, { director });
    await registry.dispatch({ command: 'spotlight.show', key: 'scene-a', target: 'chat-input' }, { director });
    await registry.dispatch({ command: 'cursor.move', target: 'chat-input', durationMs: 320 }, { director });
    await registry.dispatch({ command: 'cursor.click', effectDurationMs: 180 }, { director });
    await registry.dispatch({ command: 'cursor.wobble', durationMs: 120 }, { director });
    await registry.dispatch({
        command: 'operation.run',
        operation: 'cleanup',
        target: 'chat-input',
        preserveExternalizedChatGuideTarget: true
    }, {
        scene: { id: 'scene-a' },
        director
    });

    assert.deepEqual(calls, [
        ['chat', 'hello', 'voice-a'],
        ['emotion', 'happy'],
        ['resolve', 'chat-input'],
        ['spotlight', 'scene-a', 'chat-input'],
        ['resolve', 'chat-input'],
        ['move', 'chat-input', 320],
        ['click', 180],
        ['wobble', 120],
        ['delay', 120],
        ['resolve', 'chat-input'],
        ['operation', 'cleanup', 'chat-input', true]
    ]);
});

test('VisualRuntime resolves timeline chat voice key and emotion through director hooks', async () => {
    const calls = [];
    const legacyScene = {
        id: 'day3_intro_context',
        text: '昨天默认台词',
        voiceKey: 'avatar_floating_day3_intro',
        emotion: 'happy'
    };
    const director = {
        resolveAvatarFloatingSceneText(scene) {
            calls.push(['text', scene.id]);
            return '嘿嘿分支台词';
        },
        resolveAvatarFloatingSceneVoiceKey(scene) {
            calls.push(['voice', scene.id]);
            return 'avatar_floating_day3_intro_voice_used';
        },
        resolveAvatarFloatingSceneEmotion(scene) {
            calls.push(['emotion:resolve', scene.id]);
            return 'sad';
        },
        appendGuideChatMessage(text, options) {
            calls.push(['chat', text, options.voiceKey]);
        },
        applyGuideEmotion(emotion) {
            calls.push(['emotion', emotion]);
        }
    };
    const runtime = createTutorialVisualRuntime(director);

    runtime.handleChatMessage(
        { command: 'chat.message', text: '昨天默认台词', voiceKey: 'avatar_floating_day3_intro' },
        {
            director,
            legacyScene,
            scene: {
                audio: {
                    text: '昨天默认台词',
                    voiceKey: 'avatar_floating_day3_intro'
                }
            }
        }
    );
    runtime.handleEmotionSet(
        { command: 'emotion.set', emotion: 'happy' },
        { director, legacyScene }
    );

    assert.deepEqual(calls, [
        ['text', 'day3_intro_context'],
        ['voice', 'day3_intro_context'],
        ['chat', '嘿嘿分支台词', 'avatar_floating_day3_intro_voice_used'],
        ['emotion:resolve', 'day3_intro_context'],
        ['emotion', 'sad']
    ]);
});

test('VisualRuntime keeps explicit event emotion ahead of legacy scene emotion', async () => {
    const calls = [];
    const director = {
        resolveAvatarFloatingSceneEmotion(scene) {
            calls.push(['emotion:resolve', scene.id]);
            return scene.emotion || '';
        },
        applyGuideEmotion(emotion) {
            calls.push(['emotion', emotion]);
        }
    };
    const runtime = createTutorialVisualRuntime(director);

    runtime.handleEmotionSet(
        { command: 'emotion.set', emotion: 'surprised' },
        {
            director,
            legacyScene: {
                id: 'timeline-override',
                emotion: 'happy'
            }
        }
    );

    assert.deepEqual(calls, [
        ['emotion', 'surprised']
    ]);
});

test('VisualRuntime resolves day4 model lock timeline commands through scene target resolver', async () => {
    const calls = [];
    const lockTarget = { id: 'vrm-lock-icon' };
    const director = {
        resolveAvatarFloatingTarget(scene, role) {
            calls.push(['scene-resolve', scene.id, role]);
            return lockTarget;
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['selector-resolve', target]);
            return { id: 'selector-target' };
        },
        applyGuideHighlights(config) {
            calls.push(['spotlight', config.key, config.primary && config.primary.id]);
        },
        moveCursorToElement(target, durationMs, options) {
            calls.push(['move', target && target.id, durationMs, options && options.exactDuration]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);
    const context = {
        scene: {
            id: 'day4_model_lock',
            legacyScene: {
                id: 'day4_model_lock',
                target: '#${p}-lock-icon',
                cursorAction: 'move'
            }
        },
        director
    };

    runtime.registerCommands(registry);
    await registry.dispatch({
        command: 'spotlight.show',
        key: 'day4_model_lock',
        target: '#${p}-lock-icon'
    }, context);
    await registry.dispatch({
        command: 'cursor.move',
        target: '#${p}-lock-icon',
        durationMs: 760
    }, context);

    assert.deepEqual(calls, [
        ['scene-resolve', 'day4_model_lock', 'primary'],
        ['spotlight', 'day4_model_lock', 'vrm-lock-icon'],
        ['scene-resolve', 'day4_model_lock', 'primary'],
        ['move', 'vrm-lock-icon', 760, true]
    ]);
});

test('VisualRuntime keeps non-lock timeline targets on the selector resolver path', async () => {
    const calls = [];
    const director = {
        resolveAvatarFloatingTarget(scene, role) {
            calls.push(['scene-resolve', scene.id, role]);
            return { id: 'scene-target' };
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['selector-resolve', target]);
            return { id: target };
        },
        moveCursorToElement(target, durationMs, options) {
            calls.push(['move', target && target.id, durationMs, options && options.exactDuration]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    await registry.dispatch({
        command: 'cursor.move',
        target: 'chat-galgame',
        durationMs: 640
    }, {
        scene: {
            id: 'day2_galgame_entry',
            legacyScene: {
                id: 'day2_galgame_entry',
                target: 'chat-galgame',
                cursorAction: 'move'
            }
        },
        director
    });

    assert.deepEqual(calls, [
        ['selector-resolve', 'chat-galgame'],
        ['move', 'chat-galgame', 640, true]
    ]);
});

test('VisualRuntime uses the capsule input spotlight for first daily timeline intro scenes', async () => {
    const calls = [];
    const capsuleTarget = { id: 'capsule-input' };
    const director = {
        isAvatarFloatingInputIntroScene(scene) {
            calls.push(['intro?', scene.id]);
            return scene.id === 'day2_tool_toggle_intro';
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return false;
        },
        getAvatarFloatingIntroSpotlightTarget(scene) {
            calls.push(['intro-target', scene.id]);
            return capsuleTarget;
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        applyGuideHighlights(config) {
            calls.push(['spotlight', config.key, config.primary && config.primary.id]);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'spotlight.show',
        key: 'day2_tool_toggle_intro',
        target: 'chat-input'
    }, {
        isFirstDailyScene: true,
        scene: {
            id: 'day2_tool_toggle_intro',
            legacyScene: {
                id: 'day2_tool_toggle_intro',
                target: 'chat-input'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['intro?', 'day2_tool_toggle_intro'],
        'externalized?',
        ['intro-target', 'day2_tool_toggle_intro'],
        ['spotlight', 'day2_tool_toggle_intro', 'capsule-input']
    ]);
});

test('VisualRuntime routes externalized first daily timeline intro spotlights to the capsule input', async () => {
    const calls = [];
    const director = {
        isAvatarFloatingInputIntroScene(scene) {
            calls.push(['intro?', scene.id]);
            return true;
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return true;
        },
        getAvatarFloatingIntroExternalizedSpotlightKind(scene) {
            calls.push(['kind', scene.id]);
            return 'capsule-input';
        },
        getAvatarFloatingIntroExternalizedCursorOptions(scene) {
            calls.push(['options', scene.id]);
            return { effect: '', durationMs: 0 };
        },
        interactionTakeover: {
            setExternalizedChatSpotlight(kind) {
                calls.push(['spotlight', kind]);
            },
            setExternalizedChatCursor(kind, options) {
                calls.push(['cursor', kind, options.effect, options.durationMs]);
            }
        },
        hideHomeCursorForExternalizedChat() {
            calls.push('hide-home-cursor');
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'spotlight.show',
        key: 'day4_intro_companion',
        target: 'chat-input'
    }, {
        isFirstDailyScene: true,
        scene: {
            id: 'day4_intro_companion',
            legacyScene: {
                id: 'day4_intro_companion',
                target: 'chat-input'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['intro?', 'day4_intro_companion'],
        'externalized?',
        ['kind', 'day4_intro_companion'],
        ['spotlight', 'capsule-input'],
        ['options', 'day4_intro_companion'],
        ['cursor', 'capsule-input', '', 0],
        'hide-home-cursor'
    ]);
});

test('VisualRuntime applies externalized spotlight at the start of non-intro timeline scenes', async () => {
    const calls = [];
    const director = {
        isAvatarFloatingInputIntroScene(scene) {
            calls.push(['intro?', scene.id]);
            return false;
        },
        isHomeChatExternalized() {
            calls.push('externalized?');
            return true;
        },
        getExternalizedChatTargetKind(target, scene) {
            calls.push(['kind', target, scene.id]);
            if (target === 'chat-tool-toggle') {
                return 'tool-toggle';
            }
            if (target === 'chat-avatar-tools') {
                return 'avatar-tools';
            }
            return '';
        },
        interactionTakeover: {
            setExternalizedChatSpotlight(kind) {
                calls.push(['spotlight', kind]);
            }
        },
        applyGuideHighlights(config) {
            calls.push(['local-spotlight', config.key]);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'spotlight.show',
        key: 'day2_avatar_tools_props',
        target: 'chat-avatar-tools',
        persistent: 'chat-tool-toggle'
    }, {
        isFirstDailyScene: false,
        scene: {
            id: 'day2_avatar_tools_props',
            legacyScene: {
                id: 'day2_avatar_tools_props',
                persistent: 'chat-tool-toggle',
                target: 'chat-avatar-tools',
                cursorAction: 'click'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        'externalized?',
        ['kind', 'chat-tool-toggle', 'day2_avatar_tools_props'],
        ['kind', 'chat-avatar-tools', 'day2_avatar_tools_props'],
        ['spotlight', 'avatar-tools']
    ]);
});

test('VisualRuntime routes externalized chat cursor moves through settled anchor wait', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        getExternalizedChatCursorTargetKind(scene) {
            calls.push(['kind', scene.id, scene.target, scene.cursorTarget, scene.cursorAction]);
            return scene.cursorTarget === 'chat-capsule-input' || scene.target === 'chat-capsule-input'
                ? 'capsule-input'
                : 'input';
        },
        setExternalizedChatCursorEffect(kind, effect, options) {
            calls.push(['effect', kind, effect, options.durationMs]);
            return true;
        },
        waitForExternalizedChatCursorMove(sceneId, maxWaitMs) {
            calls.push(['wait', sceneId, maxWaitMs]);
            return Promise.resolve(true);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        moveCursorToElement(target, durationMs) {
            calls.push(['move', target.id, durationMs]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.move',
        target: 'chat-capsule-input',
        durationMs: 640
    }, {
        scene: {
            id: 'day1_takeover_return_control',
            legacyScene: {
                id: 'day1_takeover_return_control',
                target: 'chat-capsule-input',
                cursorTarget: 'chat-capsule-input',
                cursorAction: 'move'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['kind', 'day1_takeover_return_control', 'chat-capsule-input', 'chat-capsule-input', 'move'],
        ['effect', 'capsule-input', 'move', 640],
        ['wait', 'day1_takeover_return_control', 1140]
    ]);
});

test('VisualRuntime waits for externalized cursor move before afterCursorMove operation', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        waitForExternalizedChatCursorMove(sceneId, maxWaitMs) {
            calls.push(['wait', sceneId, maxWaitMs]);
            return Promise.resolve(true);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'operation.run',
        operation: 'cleanup',
        trigger: 'afterCursorMove'
    }, {
        scene: {
            id: 'day1_takeover_return_control',
            legacyScene: {
                id: 'day1_takeover_return_control',
                target: 'chat-capsule-input',
                cursorMoveDurationMs: 900
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['wait', 'day1_takeover_return_control', 1400],
        ['resolve', 'chat-capsule-input'],
        ['operation', 'cleanup', 'chat-capsule-input']
    ]);
});

test('VisualRuntime treats cursor hold as preserving the existing cursor state', async () => {
    const calls = [];
    const director = {
        moveCursorToElement(target, durationMs) {
            calls.push(['move', target && target.id, durationMs]);
            return Promise.resolve(true);
        },
        clickCursorAndWait(durationMs) {
            calls.push(['click', durationMs]);
            return Promise.resolve(true);
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene && scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.hold',
        target: 'chat-galgame'
    }, {
        scene: {
            id: 'day2_galgame_choices',
            legacyScene: {
                id: 'day2_galgame_choices',
                target: 'chat-galgame',
                cursorAction: 'hold'
            }
        },
        director,
        commandRegistry: registry
    });

    assert.equal(result, true);
    assert.deepEqual(calls, []);
});

test('VisualRuntime pins externalized cursor holds to the requested target without movement', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        getExternalizedChatCursorTargetKind(scene) {
            calls.push(['kind', scene.id, scene.target, scene.cursorTarget, scene.cursorAction]);
            return scene.cursorTarget === 'chat-galgame' ? 'galgame' : '';
        },
        setExternalizedChatCursorEffect(kind, effect, options) {
            calls.push(['effect', kind, effect, options.durationMs, options.effectDurationMs]);
            return true;
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.hold',
        target: 'chat-galgame'
    }, {
        scene: {
            id: 'day2_galgame_choices',
            legacyScene: {
                id: 'day2_galgame_choices',
                target: 'chat-galgame',
                cursorTarget: 'chat-galgame',
                cursorAction: 'hold'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['kind', 'day2_galgame_choices', 'chat-galgame', 'chat-galgame', 'hold'],
        ['effect', 'galgame', '', 0, 0]
    ]);
});

test('VisualRuntime freezes externalized cursor holds with a single sampled point', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        getExternalizedChatCursorTargetKind(scene) {
            calls.push(['kind', scene.id, scene.target, scene.cursorTarget, scene.cursorAction]);
            return scene.cursorTarget === 'chat-galgame' ? 'galgame' : '';
        },
        setExternalizedChatCursorEffect(kind, effect, options) {
            calls.push(['effect', kind, effect, options.durationMs, options.effectDurationMs, options.freezePoint]);
            return true;
        },
        waitForSceneDelay(delayMs) {
            calls.push(['delay', delayMs]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.hold',
        target: 'chat-galgame',
        freezePoint: true
    }, {
        scene: {
            id: 'day2_galgame_choices',
            legacyScene: {
                id: 'day2_galgame_choices',
                target: 'chat-galgame',
                cursorTarget: 'chat-galgame',
                cursorAction: 'hold'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['kind', 'day2_galgame_choices', 'chat-galgame', 'chat-galgame', 'hold'],
        ['effect', 'galgame', '', 0, 0, true]
    ]);
});

test('VisualRuntime runs click onStart commands as part of the click command', async () => {
    const calls = [];
    const director = {
        clickCursorAndWait(durationMs) {
            calls.push(['click', durationMs]);
            return Promise.resolve(true);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.click',
        target: 'chat-history-handle',
        effectDurationMs: 210,
        onStart: [{
            command: 'operation.run',
            operation: 'open-compact-history-during-narration',
            trigger: 'onClickStart'
        }]
    }, {
        scene: {
            id: 'day1_history_handle',
            legacyScene: {
                id: 'day1_history_handle',
                target: 'chat-input',
                cursorTarget: 'chat-history-handle'
            }
        },
        director,
        commandRegistry: registry
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['click', 210],
        ['resolve', 'chat-input'],
        ['operation', 'open-compact-history-during-narration', 'chat-input']
    ]);
});

test('VisualRuntime routes externalized click onStart through settled anchor and PC overlay effect', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        waitForExternalizedChatCursorMove(sceneId, maxWaitMs) {
            calls.push(['wait', sceneId, maxWaitMs]);
            return Promise.resolve(true);
        },
        getExternalizedChatCursorTargetKind(scene) {
            calls.push(['kind', scene.id, scene.target, scene.cursorTarget, scene.cursorAction]);
            return 'history';
        },
        setExternalizedChatCursorEffect(kind, effect, options) {
            calls.push(['effect', kind, effect, options.effectDurationMs]);
            return true;
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve();
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.click',
        target: 'chat-history-handle',
        effectDurationMs: 260,
        onStart: [{
            command: 'operation.run',
            operation: 'open-compact-history-during-narration',
            trigger: 'onClickStart'
        }]
    }, {
        scene: {
            id: 'day1_history_handle',
            legacyScene: {
                id: 'day1_history_handle',
                target: 'chat-input',
                cursorTarget: 'chat-history-handle',
                cursorMoveDurationMs: 760
            }
        },
        director,
        commandRegistry: registry
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['wait', 'day1_history_handle', 1260],
        ['kind', 'day1_history_handle', 'chat-history-handle', 'chat-history-handle', 'click'],
        ['effect', 'history', 'click', 260],
        ['resolve', 'chat-input'],
        ['delay', 260],
        ['operation', 'open-compact-history-during-narration', 'chat-input']
    ]);
});

test('VisualRuntime routes day3 externalized clicks through the PC overlay cursor effect', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        waitForExternalizedChatCursorMove(sceneId, maxWaitMs) {
            calls.push(['wait', sceneId, maxWaitMs]);
            return Promise.resolve(true);
        },
        getExternalizedChatCursorTargetKind(scene) {
            calls.push(['kind', scene.id, scene.target, scene.cursorTarget, scene.cursorAction]);
            return 'tool-toggle';
        },
        setExternalizedChatCursorEffect(kind, effect, options) {
            calls.push(['external-click', kind, effect, options.effectDurationMs]);
            return true;
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve(true);
        },
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        runAvatarFloatingSceneOperation(scene, primaryTarget) {
            calls.push(['operation', scene.operation, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.click',
        target: 'chat-tool-toggle',
        effectDurationMs: 420,
        onStart: [{
            command: 'operation.run',
            operation: 'open-compact-tool-fan',
            trigger: 'onClickStart'
        }]
    }, {
        scene: {
            id: 'day2_avatar_tools',
            legacyScene: {
                id: 'day2_avatar_tools',
                persistent: 'chat-tool-toggle',
                target: 'chat-tool-toggle',
                cursorMoveDurationMs: 760
            }
        },
        director,
        commandRegistry: registry
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['wait', 'day2_avatar_tools', 1260],
        ['kind', 'day2_avatar_tools', 'chat-tool-toggle', 'chat-tool-toggle', 'click'],
        ['external-click', 'tool-toggle', 'click', 420],
        ['resolve', 'chat-tool-toggle'],
        ['delay', 420],
        ['operation', 'open-compact-tool-fan', 'chat-tool-toggle']
    ]);
});

test('VisualRuntime routes externalized wobble through PC overlay cursor effect', async () => {
    const calls = [];
    const director = {
        isHomeChatExternalized() {
            return true;
        },
        getExternalizedChatCursorTargetKind(scene) {
            calls.push(['kind', scene.id, scene.target, scene.cursorTarget, scene.cursorAction]);
            return scene.cursorTarget === 'chat-capsule-input' || scene.target === 'chat-capsule-input'
                ? 'capsule-input'
                : 'input';
        },
        setExternalizedChatCursorEffect(kind, effect, options) {
            calls.push(['effect', kind, effect, options.effectDurationMs]);
            return true;
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve();
        },
        cursor: {
            wobble(durationMs) {
                calls.push(['local-wobble', durationMs]);
            }
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'cursor.wobble',
        target: 'chat-capsule-input',
        durationMs: 2000
    }, {
        scene: {
            id: 'day1_capsule_drag_hint',
            legacyScene: {
                id: 'day1_capsule_drag_hint',
                target: 'chat-capsule-input',
                cursorAction: 'wobble'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['kind', 'day1_capsule_drag_hint', 'chat-capsule-input', 'chat-capsule-input', 'wobble'],
        ['effect', 'capsule-input', 'wobble', 2000],
        ['delay', 2000]
    ]);
});

test('VisualRuntime routes day3 galgame wheel rotation command to the existing director performance', async () => {
    const calls = [];
    const director = {
        resolveAvatarFloatingSelector(target) {
            calls.push(['resolve', target]);
            return { id: target };
        },
        runDay3GalgameWheelDragScene(scene, primaryTarget) {
            calls.push(['galgame-wheel', scene.id, primaryTarget && primaryTarget.id]);
            return Promise.resolve(true);
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'compactToolWheel.rotateGalgameIntoCenter',
        target: 'chat-galgame'
    }, {
        scene: {
            id: 'day2_galgame_entry',
            legacyScene: {
                id: 'day2_galgame_entry',
                target: 'chat-galgame',
                cursorAction: 'move',
                operation: 'rotate-galgame-tool-into-center'
            }
        },
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['resolve', 'chat-galgame'],
        ['galgame-wheel', 'day2_galgame_entry', 'chat-galgame']
    ]);
});

test('VisualRuntime clears day four externalized chat cursor before settings tour playback', async () => {
    const calls = [];
    const director = {
        clearExternalizedChatGuideTarget(options) {
            calls.push(['clear-externalized-chat', options.clearCursor]);
        },
        settingsTourFlow: {
            play(scene, context) {
                calls.push([
                    'settings-tour',
                    scene.id,
                    context.sceneRunId,
                    context.previousSceneId,
                    context.index,
                    context.total
                ]);
                return Promise.resolve(true);
            }
        },
        operationRegistry: {
            run() {
                calls.push(['operation-registry']);
                return Promise.resolve(false);
            }
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'settingsTour.play'
    }, {
        scene: {
            id: 'day4_chat_settings',
            legacyScene: {
                id: 'day4_chat_settings',
                target: 'settings-button'
            }
        },
        director,
        sceneRunId: 41,
        previousSceneId: 'day4_intro_companion',
        index: 1,
        total: 6
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['clear-externalized-chat', true],
        ['settings-tour', 'day4_chat_settings', 41, 'day4_intro_companion', 1, 6]
    ]);
});

test('VisualRuntime leaves non-day-four settings tours externalized cursor state intact', async () => {
    const calls = [];
    const director = {
        clearExternalizedChatGuideTarget(options) {
            calls.push(['clear-externalized-chat', options.clearCursor]);
        },
        settingsTourFlow: {
            play(scene, context) {
                calls.push(['settings-tour', scene.id, context.sceneRunId]);
                return Promise.resolve(true);
            }
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'settingsTour.play'
    }, {
        scene: {
            id: 'day5_character_settings',
            legacyScene: {
                id: 'day5_character_settings'
            }
        },
        director,
        sceneRunId: 51
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['settings-tour', 'day5_character_settings', 51]
    ]);
});

test('VisualRuntime closes settings panel without clearing cursor for timeline lifecycle commands', async () => {
    const calls = [];
    const director = {
        forceHideManagedPanel(panelId) {
            calls.push(['hide-panel', panelId]);
        },
        collapseAvatarFloatingSidePanelsExcept(panelId) {
            calls.push(['collapse-side-panels', panelId]);
        },
        cursor: {
            hide() {
                calls.push(['cursor-hide']);
            }
        }
    };
    const registry = new CommandRegistry();
    const runtime = createTutorialVisualRuntime(director);

    runtime.registerCommands(registry);
    const result = await registry.dispatch({
        command: 'settingsPanel.close',
        panel: 'settings',
        collapseSidePanels: true
    }, {
        director
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['hide-panel', 'settings'],
        ['collapse-side-panels', null]
    ]);
});
