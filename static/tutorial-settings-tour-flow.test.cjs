const assert = require('node:assert/strict');
const test = require('node:test');

const { SettingsTourFlow } = require('./tutorial/core/settings-tour-flow.js');

test('SettingsTourFlow resolves every settings tour scene id', async () => {
    const flow = new SettingsTourFlow({});

    assert.equal(flow.canHandle({ id: 'day3_personalization_detail' }), true);
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
        panelEllipseDurationMs: 4200,
        panelMinDurationMs: 4200,
        openWithSettingsCursor: true,
        settingsCursorIdSuffix: '_settings_button',
        settingsCursorMoveDurationMs: 560,
        openFailureMessage: '[YuiGuide] 第4天对话设置打开设置面板失败:'
    });
    assert.deepEqual(flow.getPanelTourSchema({ id: 'day4_model_behavior' }), {
        panelId: 'animation-settings',
        waitForPanelBeforeOpening: true,
        settingsButtonHighlightSuffix: '',
        anchorHighlightSuffix: 'animation-settings-button',
        panelHighlightSuffix: 'animation-settings-panel',
        cursorMoveDurationMs: 620,
        collapseBeforeAnchorHighlight: true,
        openWithAnchorCursor: true,
        panelEllipseDurationMs: 3200,
        panelMinDurationMs: 3200
    });
    assert.equal(flow.getPanelTourSchema({ id: 'day5_character_settings' }), null);
});

test('SettingsTourFlow owns day three personalization detail scene body after day swap', async () => {
    const calls = [];
    const characterSettingsButton = { id: 'character-settings-button' };
    const characterSettingsPanel = { id: 'character-settings-panel' };
    const director = {
        sceneRunId: 11,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        currentStep: 'day3-personalization',
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

    const result = await flow.play({ id: 'day3_personalization_detail' }, {
        sceneRunId: 11,
        previousSceneId: 'day3_personalization_space',
        index: 2,
        total: 5
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day3_personalization_detail'],
        ['interrupts', 'day3-personalization'],
        ['narration', 'day3_personalization_detail', 'line', 'voice'],
        ['open-settings'],
        ['highlight', 'day3_personalization_detail-character-settings-button', 'character-settings-button', 'character-settings-button'],
        ['move', 'character-settings-button', 620],
        ['click', 420],
        ['ensure-panel', 'character-settings'],
        ['highlight', 'day3_personalization_detail-character-settings-panel', 'character-settings-panel', undefined],
        ['move', 'character-settings-panel', 620],
        ['ellipse', 60, 120, 36, 72, 5600],
        ['collapse-character'],
        ['clear-action'],
        ['clear-persistent'],
        ['finalize', 11, true, 'wait', 2, 5]
    ]);
});

test('SettingsTourFlow reuses day four animation panel from its anchor cursor click', async () => {
    const calls = [];
    const settingsButton = { id: 'settings-button' };
    const animationButton = { id: 'animation-settings-button' };
    const animationPanel = {
        id: 'animation-settings-panel',
        _anchorElement: animationButton,
        getBoundingClientRect() {
            return { left: 20, top: 40, width: 160, height: 120 };
        }
    };
    const director = {
        sceneRunId: 14,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        currentStep: 'day4-model',
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: true, actionWaitPromise: 'wait' };
        },
        getDay4SettingsButtonSpotlightTarget() {
            return settingsButton;
        },
        getAvatarFloatingSidePanel(panelId) {
            calls.push(['get-panel', panelId]);
            return panelId === 'animation-settings' ? animationPanel : null;
        },
        collapseAvatarFloatingSidePanelsExcept(panel) {
            calls.push(['collapse-except', panel]);
        },
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key, config.primary.id, config.persistent && config.persistent.id]);
        },
        enableInterrupts(step) {
            calls.push(['interrupts', step]);
        },
        createNarrationPromise(scene, text, voiceKey, options) {
            calls.push(['narration', scene.id, text, voiceKey, options && options.minDurationMs]);
            return Promise.resolve();
        },
        waitForSceneDelay(durationMs) {
            calls.push(['delay', durationMs]);
            return Promise.resolve(true);
        },
        isStopping() {
            return false;
        },
        moveAvatarFloatingCursor(cursorScene, anchorButton, secondaryTarget, previousSceneId, options) {
            calls.push([
                'cursor-click-anchor',
                cursorScene.id,
                cursorScene.cursorAction,
                cursorScene.cursorMoveDurationMs,
                anchorButton.id,
                previousSceneId
            ]);
            if (options && typeof options.onClickStart === 'function') {
                options.onClickStart();
            }
            return Promise.resolve(true);
        },
        ensureAvatarFloatingSettingsSidePanel(panelId) {
            calls.push(['ensure-panel', panelId]);
            return Promise.resolve(animationPanel);
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        cursor: {
            runPauseAwareEllipse(centerX, centerY, radiusX, radiusY, durationMs) {
                calls.push(['ellipse', centerX, centerY, radiusX, radiusY, durationMs]);
                return Promise.resolve(false);
            }
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.canHandleSceneButtons, options.actionWaitPromise, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day4_model_behavior' }, {
        sceneRunId: 14,
        previousSceneId: 'day4_chat_settings',
        index: 2,
        total: 8
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day4_model_behavior'],
        ['get-panel', 'animation-settings'],
        ['collapse-except', null],
        ['highlight', 'day4_model_behavior-animation-settings-button', 'animation-settings-button', 'settings-button'],
        ['interrupts', 'day4-model'],
        ['narration', 'day4_model_behavior', 'line', 'voice', 3200],
        ['delay', 220],
        ['cursor-click-anchor', 'day4_model_behavior_anchor_button', 'click', 620, 'animation-settings-button', 'day4_chat_settings'],
        ['ensure-panel', 'animation-settings'],
        ['highlight', 'day4_model_behavior-animation-settings-panel', 'animation-settings-panel', 'settings-button'],
        ['delay', 3200],
        ['ellipse', 100, 100, 51.2, 60, 3200],
        ['finalize', 14, true, 'wait', 2, 8]
    ]);
});

test('SettingsTourFlow stops day three panel tour if skip happens while opening side panel after day swap', async () => {
    const calls = [];
    let stopping = false;
    const characterSettingsButton = { id: 'character-settings-button' };
    const characterSettingsPanel = { id: 'character-settings-panel' };
    const director = {
        sceneRunId: 11,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        currentStep: 'day3-personalization',
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

    const result = await flow.play({ id: 'day3_personalization_detail' }, {
        sceneRunId: 11,
        previousSceneId: 'day3_personalization_space',
        index: 2,
        total: 5
    });

    assert.equal(result, false);
    assert.deepEqual(calls, [
        ['prepare', 'day3_personalization_detail'],
        ['interrupts', 'day3-personalization'],
        ['narration', 'day3_personalization_detail', 'line', 'voice'],
        ['open-settings'],
        ['highlight', 'day3_personalization_detail-character-settings-button', 'character-settings-button'],
        ['move', 'character-settings-button', 620],
        ['click', 420],
        ['ensure-panel', 'character-settings']
    ]);
});

test('SettingsTourFlow owns linear day four gaze follow scene body', async () => {
    const calls = [];
    const mouseTrackingToggle = {
        id: 'mouse-tracking-toggle',
        click() {
            calls.push(['target-click', 'mouse-tracking-toggle']);
        }
    };
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
        runActionWithCursorClick(durationMs, action) {
            calls.push(['cursor-click', durationMs]);
            action();
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
        ['cursor-click', 420],
        ['target-click', 'mouse-tracking-toggle'],
        ['delay', 1800],
        ['finalize', 5, 3, 8]
    ]);
});

test('SettingsTourFlow does not toggle off enabled day four gaze follow', async () => {
    const calls = [];
    const mouseTrackingToggle = {
        id: 'mouse-tracking-toggle',
        checked: true,
        click() {
            calls.push(['target-click', 'mouse-tracking-toggle']);
        }
    };
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
        runActionWithCursorClick(durationMs, action) {
            calls.push(['cursor-click', durationMs]);
            action();
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
        ['cursor-click', 420],
        ['delay', 1800],
        ['finalize', 5, 3, 8]
    ]);
});

test('SettingsTourFlow reads nested mouse tracking checkbox before stale wrapper state', async () => {
    const calls = [];
    const checkbox = { checked: false };
    const mouseTrackingToggle = {
        id: 'mouse-tracking-toggle',
        getAttribute(name) {
            return name === 'aria-checked' ? 'true' : null;
        },
        querySelector(selector) {
            if (selector === 'input[type="checkbox"]') {
                return checkbox;
            }
            return null;
        },
        click() {
            checkbox.checked = true;
            calls.push(['target-click', 'mouse-tracking-toggle']);
        }
    };
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
        runActionWithCursorClick(durationMs, action) {
            calls.push(['cursor-click', durationMs]);
            action();
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
    assert.equal(checkbox.checked, true);
    assert.deepEqual(calls, [
        ['prepare', 'day4_gaze_follow'],
        ['highlight', 'day4_gaze_follow-mouse-tracking-toggle', 'mouse-tracking-toggle', 'settings-button'],
        ['interrupts', 'day4-gaze'],
        ['narration', 'day4_gaze_follow', 'line', 'voice'],
        ['delay', 220],
        ['move', 'mouse-tracking-toggle', 620],
        ['cursor-click', 420],
        ['target-click', 'mouse-tracking-toggle'],
        ['delay', 1800],
        ['finalize', 5, 3, 8]
    ]);
});

test('SettingsTourFlow hides day four privacy panels before and after closing settings', async () => {
    const calls = [];
    const settingsButton = { id: 'settings-button' };
    const privacyButton = { id: 'privacy-toggle' };
    const director = {
        sceneRunId: 6,
        currentStep: 'day4-privacy',
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        getDay4SettingsButtonSpotlightTarget() {
            return settingsButton;
        },
        getDay4PrivacyModeButtonTarget() {
            return privacyButton;
        },
        collapseAvatarFloatingSidePanelsExcept(panel) {
            calls.push(['collapse-except', panel]);
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
        closeSettingsPanel() {
            calls.push(['close-settings']);
            return Promise.resolve(true);
        },
        forceHideManagedPanel(panelId) {
            calls.push(['force-hide', panelId]);
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day4_privacy_mode' }, {
        sceneRunId: 6,
        previousSceneId: 'day4_gaze_follow',
        index: 4,
        total: 8
    });

    assert.equal(result, true);
    assert.deepEqual(calls, [
        ['prepare', 'day4_privacy_mode'],
        ['collapse-except', null],
        ['highlight', 'day4_privacy_mode-privacy-mode-button', 'privacy-toggle', 'settings-button'],
        ['interrupts', 'day4-privacy'],
        ['narration', 'day4_privacy_mode', 'line', 'voice'],
        ['delay', 220],
        ['move', 'privacy-toggle', 620],
        ['force-hide', 'settings'],
        ['collapse-except', null],
        ['close-settings'],
        ['force-hide', 'settings'],
        ['collapse-except', null],
        ['finalize', 6, 4, 8]
    ]);
});

test('SettingsTourFlow does not hide newer panel when day four privacy close becomes stale', async () => {
    const calls = [];
    const settingsButton = { id: 'settings-button' };
    const privacyButton = { id: 'privacy-toggle' };
    const director = {
        sceneRunId: 6,
        currentStep: 'day4-privacy',
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        getDay4SettingsButtonSpotlightTarget() {
            return settingsButton;
        },
        getDay4PrivacyModeButtonTarget() {
            return privacyButton;
        },
        collapseAvatarFloatingSidePanelsExcept(panel) {
            calls.push(['collapse-except', panel]);
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
        closeSettingsPanel() {
            calls.push(['close-settings']);
            this.sceneRunId = 7;
            return Promise.resolve(true);
        },
        forceHideManagedPanel(panelId) {
            calls.push(['force-hide', panelId]);
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day4_privacy_mode' }, {
        sceneRunId: 6,
        previousSceneId: 'day4_gaze_follow',
        index: 4,
        total: 8
    });

    assert.equal(result, false);
    assert.deepEqual(calls, [
        ['prepare', 'day4_privacy_mode'],
        ['collapse-except', null],
        ['highlight', 'day4_privacy_mode-privacy-mode-button', 'privacy-toggle', 'settings-button'],
        ['interrupts', 'day4-privacy'],
        ['narration', 'day4_privacy_mode', 'line', 'voice'],
        ['delay', 220],
        ['move', 'privacy-toggle', 620],
        ['force-hide', 'settings'],
        ['collapse-except', null],
        ['close-settings']
    ]);
});

test('SettingsTourFlow continues day four privacy cleanup when hide helpers throw', async () => {
    const calls = [];
    const warnings = [];
    const originalWarn = console.warn;
    console.warn = (...args) => {
        warnings.push(args);
    };
    const settingsButton = { id: 'settings-button' };
    const privacyButton = { id: 'privacy-toggle' };
    const director = {
        sceneRunId: 6,
        currentStep: 'day4-privacy',
        prepareNarration(scene) {
            calls.push(['prepare', scene.id]);
            return { text: 'line', voiceKey: 'voice', canHandleSceneButtons: false, actionWaitPromise: null };
        },
        getDay4SettingsButtonSpotlightTarget() {
            return settingsButton;
        },
        getDay4PrivacyModeButtonTarget() {
            return privacyButton;
        },
        collapseAvatarFloatingSidePanelsExcept(panel) {
            calls.push(['collapse-except', panel]);
            if (calls.filter((call) => call[0] === 'collapse-except').length > 1) {
                throw new Error('collapse failed');
            }
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
        closeSettingsPanel() {
            calls.push(['close-settings']);
            return Promise.resolve(true);
        },
        forceHideManagedPanel(panelId) {
            calls.push(['force-hide', panelId]);
            throw new Error('hide failed');
        },
        finalizeScene(sceneRunId, options) {
            calls.push(['finalize', sceneRunId, options.index, options.total]);
            return Promise.resolve(true);
        }
    };
    const flow = new SettingsTourFlow(director);

    try {
        const result = await flow.play({ id: 'day4_privacy_mode' }, {
            sceneRunId: 6,
            previousSceneId: 'day4_gaze_follow',
            index: 4,
            total: 8
        });

        assert.equal(result, true);
        assert.equal(warnings.length, 4);
        assert.equal(calls.at(-1)[0], 'finalize');
    } finally {
        console.warn = originalWarn;
    }
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

test('SettingsTourFlow keeps panel ellipse alive for configured minimum duration', async () => {
    const delays = [];
    const panel = {
        getBoundingClientRect() {
            return { left: 10, top: 20, width: 100, height: 200 };
        }
    };
    const director = {
        sceneRunId: 4,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        isStopping() {
            return false;
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        waitForSceneDelay(durationMs) {
            delays.push(durationMs);
            return Promise.resolve(true);
        },
        cursor: {
            runPauseAwareEllipse() {
                return Promise.resolve(false);
            }
        }
    };
    const flow = new SettingsTourFlow(director);

    await flow.runPanelNarrationEllipse(4, panel, Promise.resolve(), {
        durationMs: 1200,
        minDurationMs: 900
    });

    assert.deepEqual(delays, [900]);
});

test('SettingsTourFlow stops day four panel tour when anchor click makes scene stale', async () => {
    const calls = [];
    const settingsButton = { id: 'settings-button' };
    const animationButton = { id: 'animation-settings-button' };
    const animationPanel = {
        id: 'animation-settings-panel',
        _anchorElement: animationButton,
        getBoundingClientRect() {
            return { left: 20, top: 40, width: 160, height: 120 };
        }
    };
    const director = {
        sceneRunId: 14,
        currentStep: 'day4-model',
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        prepareNarration() {
            return { text: 'line', voiceKey: 'voice' };
        },
        getDay4SettingsButtonSpotlightTarget() {
            return settingsButton;
        },
        getAvatarFloatingSidePanel() {
            return animationPanel;
        },
        collapseAvatarFloatingSidePanelsExcept() {},
        applyGuideHighlights(config) {
            calls.push(['highlight', config.key]);
        },
        enableInterrupts() {},
        createNarrationPromise() {
            return Promise.resolve();
        },
        waitForSceneDelay() {
            return Promise.resolve(true);
        },
        isStopping() {
            return false;
        },
        moveAvatarFloatingCursor(cursorScene, anchorButton, secondaryTarget, previousSceneId, options) {
            if (options && typeof options.onClickStart === 'function') {
                options.onClickStart();
            }
            this.sceneRunId = 15;
            return Promise.resolve(true);
        },
        ensureAvatarFloatingSettingsSidePanel(panelId, options) {
            calls.push(['ensure', panelId, !!(options && options.shouldContinue), !!(options && options.skipOpenSettingsPanel)]);
            return Promise.resolve(animationPanel);
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        cursor: {
            runPauseAwareEllipse() {
                return Promise.resolve(false);
            }
        }
    };
    const flow = new SettingsTourFlow(director);

    const result = await flow.play({ id: 'day4_model_behavior' }, { sceneRunId: 14 });

    assert.equal(result, false);
    assert.deepEqual(calls, [
        ['highlight', 'day4_model_behavior-animation-settings-button'],
        ['ensure', 'animation-settings', true, true]
    ]);
});

test('SettingsTourFlow passes scene guard to day four minimum panel tour delay', async () => {
    const guards = [];
    const panel = {
        getBoundingClientRect() {
            return { left: 10, top: 20, width: 100, height: 200 };
        }
    };
    const director = {
        sceneRunId: 4,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        isStopping() {
            return false;
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        waitForSceneDelay(durationMs, options) {
            guards.push(options && options.shouldContinue);
            this.sceneRunId = 5;
            return Promise.resolve(false);
        },
        cursor: {
            runPauseAwareEllipse() {
                return Promise.resolve(false);
            }
        }
    };
    const flow = new SettingsTourFlow(director);

    await flow.runPanelNarrationEllipse(4, panel, Promise.resolve(), {
        scene: { id: 'day4_chat_settings' },
        durationMs: 1200,
        minDurationMs: 900
    });

    assert.equal(typeof guards[0], 'function');
    assert.equal(guards[0](), false);
});

test('SettingsTourFlow stops day four panel tour when narration is still pending but scene becomes stale', async () => {
    const panel = {
        getBoundingClientRect() {
            return { left: 10, top: 20, width: 100, height: 200 };
        }
    };
    const director = {
        sceneRunId: 4,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        isStopping() {
            return false;
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        waitForSceneDelay(durationMs, options) {
            return Promise.resolve(!(options && options.shouldContinue && !options.shouldContinue()));
        },
        cursor: {
            runPauseAwareEllipse() {
                director.sceneRunId = 5;
                return Promise.resolve(false);
            }
        }
    };
    const flow = new SettingsTourFlow(director);
    const pendingNarration = new Promise(() => {});

    await flow.runPanelNarrationEllipse(4, panel, pendingNarration, {
        scene: { id: 'day4_chat_settings' },
        durationMs: 1200,
        minDurationMs: 900
    });

    assert.equal(director.sceneRunId, 5);
});

test('SettingsTourFlow does not pass scene guard to non-day-four panel tour delay', async () => {
    const optionsSeen = [];
    const panel = {
        getBoundingClientRect() {
            return { left: 10, top: 20, width: 100, height: 200 };
        }
    };
    const director = {
        sceneRunId: 4,
        destroyed: false,
        angryExitTriggered: false,
        scenePausedForResistance: false,
        isStopping() {
            return false;
        },
        getElementRect(target) {
            return target.getBoundingClientRect();
        },
        waitForSceneDelay(durationMs, options) {
            optionsSeen.push(options);
            this.sceneRunId = 5;
            return Promise.resolve(false);
        },
        cursor: {
            runPauseAwareEllipse() {
                return Promise.resolve(false);
            }
        }
    };
    const flow = new SettingsTourFlow(director);

    await flow.runPanelNarrationEllipse(4, panel, Promise.resolve(), {
        scene: { id: 'day5_character_settings' },
        durationMs: 1200,
        minDurationMs: 900
    });

    assert.equal(optionsSeen[0], undefined);
});
