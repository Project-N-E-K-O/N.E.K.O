const assert = require('node:assert/strict');
const test = require('node:test');

const { SettingsTourFlow } = require('./tutorial/core/settings-tour-flow.js');

test('SettingsTourFlow resolves every settings tour scene id', async () => {
    const flow = new SettingsTourFlow({});

    assert.equal(flow.canHandle({ id: 'day2_personalization_detail' }), true);
    assert.equal(flow.canHandle({ id: 'day4_chat_settings' }), true);
    assert.equal(flow.canHandle({ id: 'day4_model_behavior' }), true);
    assert.equal(flow.canHandle({ id: 'day4_gaze_follow' }), true);
    assert.equal(flow.canHandle({ id: 'day4_privacy_mode' }), true);
    assert.equal(flow.canHandle({ id: 'day5_character_settings' }), true);
    assert.equal(flow.canHandle({ id: 'day5_character_panic' }), true);
    assert.equal(flow.canHandle({ id: 'unknown_scene' }), false);
    assert.equal(flow.resolveSceneMethodName({ id: 'day5_character_panic' }), 'playDay5CharacterPanicScene');
    assert.equal(await flow.play({ id: 'unknown_scene' }, {}), false);
});

test('SettingsTourFlow exposes declarative schemas for day four panel tours', () => {
    const flow = new SettingsTourFlow({});

    assert.deepEqual(flow.getPanelTourSchema({ id: 'day4_chat_settings' }), {
        panelId: 'chat-settings',
        waitForPanelBeforeOpening: false,
        settingsButtonHighlightSuffix: 'settings-button',
        anchorHighlightSuffix: 'chat-settings-button',
        panelHighlightSuffix: 'chat-settings-panel',
        cursorMoveDurationMs: 620,
        openWithSettingsCursor: true,
        settingsCursorIdSuffix: '_settings_button',
        settingsCursorMoveDurationMs: 760,
        openFailureMessage: '[YuiGuide] 第4天对话设置打开设置面板失败:'
    });
    assert.deepEqual(flow.getPanelTourSchema({ id: 'day4_model_behavior' }), {
        panelId: 'animation-settings',
        waitForPanelBeforeOpening: true,
        settingsButtonHighlightSuffix: '',
        anchorHighlightSuffix: 'animation-settings-button',
        panelHighlightSuffix: 'animation-settings-panel',
        cursorMoveDurationMs: 620,
        collapseBeforeAnchorHighlight: true
    });
    assert.equal(flow.getPanelTourSchema({ id: 'day5_character_settings' }), null);
});

test('SettingsTourFlow owns day two personalization detail scene body', async () => {
    const calls = [];
    const characterSettingsButton = { id: 'character-settings-button' };
    const characterSettingsPanel = { id: 'character-settings-panel' };
    const director = {
        sceneRunId: 11,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        currentStep: 'day2-personalization',
        overlay: {
            clearActionSpotlight() {
                calls.push(['clear-action']);
            },
            clearPersistentSpotlight() {
                calls.push(['clear-persistent']);
            }
        },
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: true, actionWaitPromise: 'wait' };
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        createNarrationPromise(scene, text, voiceKey) {
            calls.push(['narration', scene.id, text, voiceKey]);
            return Promise.resolve();
        },
        openSettingsPanel() {
            calls.push(['open-settings']);
            return Promise.resolve(true);
        },
        isStopping() {
            return false;
        },
        getCharacterSettingsSidePanel() {
            return null;
        },
        getDay5CharacterSettingsButtonTarget() {
            return characterSettingsButton;
        },
        getSettingsMenuElement() {
            return null;
        },
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key, config.primary.id, config.persistent && config.persistent.id]);
        },
        moveCursorToElement(element, durationMs) {
            calls.push(['move', element.id, durationMs]);
            return Promise.resolve(true);
        },
        clickCursorAndWait(durationMs) {
            calls.push(['click', durationMs]);
            return Promise.resolve(true);
        },
        ensureAvatarFloatingSettingsSidePanel(panelId) {
            calls.push(['ensure-panel', panelId]);
            return Promise.resolve(characterSettingsPanel);
        },
        getElementRect(target) {
            return target === characterSettingsPanel ? { left: 10, top: 20, width: 100, height: 200 } : null;
        },
        cursor: {
            runPauseAwareEllipse(centerX, centerY, radiusX, radiusY, durationMs) {
                calls.push(['ellipse', centerX, centerY, radiusX, radiusY, durationMs]);
                return Promise.resolve(false);
            }
        },
        collapseCharacterSettingsSidePanel() {
            calls.push(['collapse-character']);
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.canHandleSceneButtons, options.actionWaitPromise, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day2_personalization_detail' }, {
        sceneRunId: 11,
        previousSceneId: 'day2_personalization_space',
        index: 2,
        total: 5
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day2_personalization_detail'],
        ['interrupts', 'day2-personalization'],
        ['narration', 'day2_personalization_detail', 'line', 'voice'],
        ['open-settings'],
        ['highlight', 'day2_personalization_detail-character-settings-button', 'character-settings-button', 'character-settings-button'],
        ['move', 'character-settings-button', 620],
        ['click', 420],
        ['ensure-panel', 'character-settings'],
        ['highlight', 'day2_personalization_detail-character-settings-panel', 'character-settings-panel', undefined],
        ['move', 'character-settings-panel', 620],
        ['ellipse', 60, 120, 36, 72, 5600],
        ['collapse-character'],
        ['clear-action'],
        ['clear-persistent'],
        ['finalize', 11, true, 'wait', 2, 5]
    ]);
});

test('SettingsTourFlow stops day two panel tour if skip happens while opening side panel', async () => {
    const calls = [];
    let stopping = false;
    const characterSettingsButton = { id: 'character-settings-button' };
    const characterSettingsPanel = { id: 'character-settings-panel' };
    const director = {
        sceneRunId: 11,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        currentStep: 'day2-personalization',
        overlay: {
            clearActionSpotlight() {
                calls.push(['clear-action']);
            },
            clearPersistentSpotlight() {
                calls.push(['clear-persistent']);
            }
        },
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        createNarrationPromise(scene, text, voiceKey) {
            calls.push(['narration', scene.id, text, voiceKey]);
            return Promise.resolve();
        },
        openSettingsPanel() {
            calls.push(['open-settings']);
            return Promise.resolve(true);
        },
        isStopping() {
            return stopping;
        },
        getCharacterSettingsSidePanel() {
            return null;
        },
        getDay5CharacterSettingsButtonTarget() {
            return characterSettingsButton;
        },
        getSettingsMenuElement() {
            return null;
        },
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key, config.primary.id]);
        },
        moveCursorToElement(element, durationMs) {
            calls.push(['move', element.id, durationMs]);
            return Promise.resolve(true);
        },
        clickCursorAndWait(durationMs) {
            calls.push(['click', durationMs]);
            return Promise.resolve(true);
        },
        async ensureAvatarFloatingSettingsSidePanel(panelId) {
            calls.push(['ensure-panel', panelId]);
            stopping = true;
            return characterSettingsPanel;
        },
        getElementRect() {
            return { left: 10, top: 20, width: 100, height: 200 };
        },
        cursor: {
            runPauseAwareEllipse() {
                calls.push(['ellipse']);
                return Promise.resolve(false);
            }
        },
        collapseCharacterSettingsSidePanel() {
            calls.push(['collapse-character']);
        },
        finalizeScene() {
            calls.push(['finalize']);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day2_personalization_detail' }, {
        sceneRunId: 11,
        previousSceneId: 'day2_personalization_space',
        index: 2,
        total: 5
    });

    assert.equal(result, false);
    assert.deepEqual(calls, [
        ['prepare', 'day2_personalization_detail'],
        ['interrupts', 'day2-personalization'],
        ['narration', 'day2_personalization_detail', 'line', 'voice'],
        ['open-settings'],
        ['highlight', 'day2_personalization_detail-character-settings-button', 'character-settings-button'],
        ['move', 'character-settings-button', 620],
        ['click', 420],
        ['ensure-panel', 'character-settings']
    ]);
});

test('SettingsTourFlow owns linear day four gaze follow scene body', async () => {
    const calls = [];
    const mouseTrackingToggle = { id: 'mouse-tracking-toggle' };
    const settingsButton = { id: 'settings-button' };
    const director = {
        sceneRunId: 5,
        currentStep: 'day4-gaze',
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        getDay4SettingsButtonSpotlightTarget() {
            return settingsButton;
        },
        getDay4MouseTrackingTarget() {
            return mouseTrackingToggle;
        },
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key, config.primary.id, config.persistent.id]);
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        createNarrationPromise(scene, text, voiceKey) {
            calls.push(['narration', scene.id, text, voiceKey]);
            return Promise.resolve();
        },
        waitForSceneDelay(delayMs) {
            calls.push(['delay', delayMs]);
            return Promise.resolve(true);
        },
        isStopping() {
            return false;
        },
        moveCursorToElement(element, durationMs) {
            calls.push(['move', element.id, durationMs]);
            return Promise.resolve(true);
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day4_gaze_follow' }, {
        sceneRunId: 5,
        previousSceneId: 'day4_model_behavior',
        index: 3,
        total: 8
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day4_gaze_follow'],
        ['highlight', 'day4_gaze_follow-mouse-tracking-toggle', 'mouse-tracking-toggle', 'settings-button'],
        ['interrupts', 'day4-gaze'],
        ['narration', 'day4_gaze_follow', 'line', 'voice'],
        ['delay', 220],
        ['move', 'mouse-tracking-toggle', 620],
        ['finalize', 5, 3, 8]
    ]);
});

test('SettingsTourFlow refreshes visible day five character panel before panic narration', async () => {
    const calls = [];
    const characterSettingsPanel = { id: 'character-settings-panel' };
    const characterSettingsButton = { id: 'character-settings-button' };
    const director = {
        sceneRunId: 17,
        destroyed: false,
        angryExitTriggered: false,
        currentStep: 'day5-panic',
        overlay: {
            clearActionSpotlight() {
                calls.push(['clear-action']);
            },
            clearPersistentSpotlight() {
                calls.push(['clear-persistent']);
            }
        },
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        getCharacterSettingsSidePanel() {
            calls.push(['get-panel']);
            return characterSettingsPanel;
        },
        isElementVisible(panel) {
            calls.push(['visible', panel.id]);
            return true;
        },
        ensureAvatarFloatingSettingsSidePanel(panelId) {
            calls.push(['ensure-panel', panelId]);
            return Promise.resolve(characterSettingsPanel);
        },
        getDay5CharacterSettingsButtonTarget() {
            calls.push(['get-button']);
            return characterSettingsButton;
        },
        refreshAvatarFloatingSettingsPanelLayout(panel) {
            calls.push(['refresh-layout', panel.id]);
        },
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key, config.primary.id, config.persistent.id]);
        },
        moveCursorToElement(element, durationMs, options) {
            const normalizedOptions = options || {};
            calls.push(['move', element.id, durationMs, normalizedOptions.exactDuration]);
            return Promise.resolve(true);
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        createNarrationPromise(scene, text, voiceKey) {
            calls.push(['narration', scene.id, text, voiceKey]);
            return Promise.resolve();
        },
        getAvatarFloatingNarrationDurationMs(voiceKey, text) {
            calls.push(['duration', voiceKey, text]);
            return 900;
        },
        getElementRect(target) {
            calls.push(['rect', target.id]);
            return { left: 10, top: 20, width: 100, height: 200 };
        },
        runSettingsPeekPanicPerformance(options) {
            calls.push(['panic', options.targetRect.width, options.totalDurationMs, options.runId]);
            return Promise.resolve();
        },
        isStopping() {
            return false;
        },
        collapseCharacterSettingsSidePanel() {
            calls.push(['collapse-character']);
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day5_character_panic' }, {
        sceneRunId: 17,
        index: 2,
        total: 4
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day5_character_panic'],
        ['get-panel'],
        ['visible', 'character-settings-panel'],
        ['get-button'],
        ['refresh-layout', 'character-settings-panel'],
        ['highlight', 'day5_character_panic-character-settings-panel', 'character-settings-panel', 'character-settings-button'],
        ['move', 'character-settings-panel', 0, true],
        ['interrupts', 'day5-panic'],
        ['narration', 'day5_character_panic', 'line', 'voice'],
        ['duration', 'voice', 'line'],
        ['rect', 'character-settings-panel'],
        ['panic', 100, 900, 17],
        ['clear-action'],
        ['clear-persistent'],
        ['collapse-character'],
        ['finalize', 17, 2, 4]
    ]);
    assert.equal(calls.some((call) => call[0] === 'ensure-panel'), false);
});

test('SettingsTourFlow stops day five panic scene after stale async panel ensure', async () => {
    const calls = [];
    const characterSettingsPanel = { id: 'character-settings-panel' };
    const director = {
        sceneRunId: 17,
        destroyed: false,
        angryExitTriggered: false,
        currentStep: 'day5-panic',
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        getCharacterSettingsSidePanel() {
            calls.push(['get-panel']);
            return null;
        },
        ensureAvatarFloatingSettingsSidePanel(panelId) {
            calls.push(['ensure-panel', panelId]);
            this.sceneRunId = 18;
            return Promise.resolve(characterSettingsPanel);
        },
        getDay5CharacterSettingsButtonTarget() {
            calls.push(['get-button']);
            return { id: 'character-settings-button' };
        },
        refreshAvatarFloatingSettingsPanelLayout(panel) {
            calls.push(['refresh-layout', panel && panel.id]);
        },
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key]);
        },
        isStopping() {
            return false;
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day5_character_panic' }, {
        sceneRunId: 17,
        index: 2,
        total: 4
    });

    assert.equal(result, false);
    assert.deepEqual(calls, [
        ['prepare', 'day5_character_panic'],
        ['get-panel'],
        ['ensure-panel', 'character-settings']
    ]);
});

test('SettingsTourFlow delegates narration and finalize to the director', async () => {
    const calls = [];
    const director = {
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: true, actionWaitPromise: 'wait' };
        },
        createNarrationPromise(scene, text, voiceKey, options) {
            calls.push(['narration', scene.id, text, voiceKey, options.minDurationMs]);
            return Promise.resolve('spoken');
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);
    const scene = { id: 'day4_chat_settings' };
    const narration = flow.prepareNarration(scene);
    await flow.createNarrationPromise(scene, narration, { minDurationMs: 2200 });
    const result = await flow.finalize(9, {
        canHandleSceneButtons: narration.canHandleSceneButtons,
        actionWaitPromise: narration.actionWaitPromise,
        index: 1,
        total: 3
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day4_chat_settings'],
        ['narration', 'day4_chat_settings', 'line', 'voice', 2200],
        ['finalize', 9, 1, 3]
    ]);
});

test('SettingsTourFlow runs pause-aware panel ellipse until narration settles', async () => {
    const calls = [];
    let resumeWaits = 0;
    const panel = {
        getBoundingClientRect() {
            return { left: 10, top: 20, width: 100, height: 200 };
        }
    };
    const director = {
        sceneRunId: 4,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: true,
        isStopping() {
            return false;
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        waitUntilSceneResumed() {
            resumeWaits += 1;
            this.scenePausedForResistance = false;
            return Promise.resolve();
        },
        cursor: {
            runPauseAwareEllipse(centerX, centerY, radiusX, radiusY, durationMs) {
                calls.push([centerX, centerY, radiusX, radiusY, durationMs]);
                return Promise.resolve(false);
            }
        }
    };
    const flow = new SettingsTourFlow(director);

    await flow.runPanelNarrationEllipse(4, panel, Promise.resolve(), { durationMs: 1200 });

    assert.deepEqual(calls, [[60, 120, 36, 72, 1200]]);
    assert.equal(resumeWaits, 1);
});
