(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialSettingsTourFlow = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const SETTINGS_TOUR_SCENE_METHODS = Object.freeze({
        day3_personalization_detail: 'playDay3PersonalizationDetailScene',
        day4_chat_settings: 'playDay4ChatSettingsScene',
        day4_model_behavior: 'playDay4ModelBehaviorScene',
        day4_gaze_follow: 'playDay4GazeFollowScene',
        day4_privacy_mode: 'playDay4PrivacyModeScene',
        day5_character_settings: 'playDay5CharacterSettingsScene',
        day5_character_panic: 'playDay5CharacterPanicScene'
    });
    const SETTINGS_PANEL_TOUR_SCHEMAS = Object.freeze({
        day4_chat_settings: Object.freeze({
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
        }),
        day4_model_behavior: Object.freeze({
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
        })
    });
    const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420;
    const DAY4_GAZE_FOLLOW_CHECKED_DISPLAY_MS = 1800;

    class SettingsTourFlow {
        constructor(director) {
            this.director = director;
        }

        resolveSceneMethodName(scene) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return SETTINGS_TOUR_SCENE_METHODS[sceneId] || '';
        }

        canHandle(scene) {
            return !!this.resolveSceneMethodName(scene);
        }

        getPanelTourSchema(scene) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            const schema = SETTINGS_PANEL_TOUR_SCHEMAS[sceneId] || null;
            return schema ? Object.assign({}, schema) : null;
        }

        async play(scene, context) {
            const director = this.director;
            const methodName = this.resolveSceneMethodName(scene);
            const normalizedContext = context || {};
            if (!methodName) {
                return false;
            }
            if (typeof this[methodName] === 'function') {
                return this[methodName](scene, normalizedContext);
            }
            if (!director || typeof director[methodName] !== 'function') {
                return false;
            }
            return director[methodName](
                scene,
                normalizedContext.sceneRunId,
                normalizedContext.previousSceneId,
                normalizedContext.index,
                normalizedContext.total
            );
        }

        async playDay3PersonalizationDetailScene(scene, context) {
            const director = this.director;
            const normalizedContext = context || {};
            const sceneRunId = normalizedContext.sceneRunId;
            const narration = this.prepareNarration(scene);

            director.enableInterrupts(director.currentStep);
            const narrationPromise = this.createNarrationPromise(scene, narration);

            await director.openSettingsPanel();
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            let characterSettingsPanel = director.getCharacterSettingsSidePanel();
            const characterSettingsButton = director.getDay5CharacterSettingsButtonTarget()
                || director.getSettingsMenuElement('character');
            if (characterSettingsButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-character-settings-button',
                    persistent: characterSettingsButton,
                    primary: characterSettingsButton
                });
                await director.moveCursorToElement(characterSettingsButton, 620);
                if (this.isSceneStale(sceneRunId)) {
                    return false;
                }
                await director.clickCursorAndWait(DEFAULT_CURSOR_CLICK_VISIBLE_MS);
                characterSettingsPanel = await director.ensureAvatarFloatingSettingsSidePanel('character-settings')
                    || characterSettingsPanel;
            } else {
                characterSettingsPanel = await director.ensureAvatarFloatingSettingsSidePanel('character-settings')
                    || characterSettingsPanel;
            }
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            await this.tourPanel(scene, sceneRunId, characterSettingsPanel, narrationPromise, {
                key: scene.id + '-character-settings-panel',
                cursorMoveDurationMs: 620
            });

            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            director.collapseCharacterSettingsSidePanel();
            director.overlay.clearActionSpotlight();
            director.overlay.clearPersistentSpotlight();
            return this.finalizeNarration(sceneRunId, narration, normalizedContext);
        }

        async playDay4ChatSettingsScene(scene, context) {
            return this.playPanelTourScene(scene, context, this.getPanelTourSchema(scene));
        }

        async playDay4ModelBehaviorScene(scene, context) {
            return this.playPanelTourScene(scene, context, this.getPanelTourSchema(scene));
        }

        async playPanelTourScene(scene, context, schema) {
            const director = this.director;
            const normalizedContext = context || {};
            const sceneRunId = normalizedContext.sceneRunId;
            const previousSceneId = normalizedContext.previousSceneId;
            const normalizedSchema = schema || {};
            const panelId = normalizedSchema.panelId || '';
            if (!panelId) {
                return false;
            }
            const narration = this.prepareNarration(scene);
            const settingsButton = director.getDay4SettingsButtonSpotlightTarget();
            let sidePanel = null;
            let openedPanelPromise = null;

            if (normalizedSchema.waitForPanelBeforeOpening) {
                sidePanel = director.getAvatarFloatingSidePanel(panelId);
            }
            if (normalizedSchema.waitForPanelBeforeOpening && !sidePanel) {
                await director.openSettingsPanel();
                sidePanel = await director.waitForElement(
                    () => director.getAvatarFloatingSidePanel(panelId),
                    1200
                );
            }

            if (normalizedSchema.settingsButtonHighlightSuffix && settingsButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-' + normalizedSchema.settingsButtonHighlightSuffix,
                    persistent: settingsButton,
                    primary: settingsButton
                });
            }
            const anchorButton = sidePanel && sidePanel._anchorElement
                ? sidePanel._anchorElement
                : null;
            if (normalizedSchema.collapseBeforeAnchorHighlight) {
                director.collapseAvatarFloatingSidePanelsExcept(null);
            }
            if (anchorButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-' + normalizedSchema.anchorHighlightSuffix,
                    persistent: settingsButton || null,
                    primary: anchorButton
                });
            }
            director.enableInterrupts(director.currentStep);

            const narrationPromise = this.createNarrationPromise(scene, narration, {
                minDurationMs: normalizedSchema.panelMinDurationMs
            });

            await director.waitForSceneDelay(220);
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            if (normalizedSchema.openWithSettingsCursor && settingsButton) {
                await director.moveAvatarFloatingCursor({
                    id: scene.id + normalizedSchema.settingsCursorIdSuffix,
                    cursorAction: 'click',
                    cursorMoveDurationMs: normalizedSchema.settingsCursorMoveDurationMs
                }, settingsButton, null, previousSceneId, {
                    onClickStart: () => director.openSettingsPanel().catch((error) => {
                        console.warn(normalizedSchema.openFailureMessage, error);
                    })
                });
            } else if (!normalizedSchema.waitForPanelBeforeOpening) {
                await director.openSettingsPanel();
            } else if (anchorButton) {
                if (
                    normalizedSchema.openWithAnchorCursor
                    && typeof director.moveAvatarFloatingCursor === 'function'
                ) {
                    await director.moveAvatarFloatingCursor({
                        id: scene.id + '_anchor_button',
                        cursorAction: 'click',
                        cursorMoveDurationMs: normalizedSchema.cursorMoveDurationMs
                    }, anchorButton, null, previousSceneId, {
                        onClickStart: () => {
                            if (!this.isSceneStale(sceneRunId)) {
                                openedPanelPromise = this.ensurePanelForScene(panelId, sceneRunId, scene, {
                                    skipOpenSettingsPanel: true
                                });
                            }
                        }
                    });
                } else {
                    await director.moveCursorToElement(anchorButton, normalizedSchema.cursorMoveDurationMs);
                }
            }
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            if (!normalizedSchema.waitForPanelBeforeOpening) {
                sidePanel = await director.waitForElement(
                    () => director.getAvatarFloatingSidePanel(panelId),
                    1200
                );
            }
            const resolvedAnchorButton = sidePanel && sidePanel._anchorElement
                ? sidePanel._anchorElement
                : null;
            if (!normalizedSchema.waitForPanelBeforeOpening && resolvedAnchorButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-' + normalizedSchema.anchorHighlightSuffix,
                    persistent: settingsButton || null,
                    primary: resolvedAnchorButton
                });
                await director.moveCursorToElement(resolvedAnchorButton, normalizedSchema.cursorMoveDurationMs);
            }
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            const openedPanel = openedPanelPromise ? await openedPanelPromise : null;
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            const touredPanel = openedPanel
                || await this.ensurePanelForScene(panelId, sceneRunId, scene)
                || sidePanel;
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            await this.tourPanel(scene, sceneRunId, touredPanel, narrationPromise, {
                key: scene.id + '-' + normalizedSchema.panelHighlightSuffix,
                persistent: settingsButton || null,
                ellipseDurationMs: normalizedSchema.panelEllipseDurationMs,
                minDurationMs: normalizedSchema.panelMinDurationMs
            });

            return this.finalizeNarration(sceneRunId, narration, normalizedContext);
        }

        async playDay4GazeFollowScene(scene, context) {
            const director = this.director;
            const normalizedContext = context || {};
            const sceneRunId = normalizedContext.sceneRunId;
            const narration = this.prepareNarration(scene);

            const settingsButton = director.getDay4SettingsButtonSpotlightTarget();
            let mouseTrackingTarget = director.getDay4MouseTrackingTarget();
            if (!mouseTrackingTarget) {
                await director.ensureAvatarFloatingSettingsSidePanel('animation-settings');
                mouseTrackingTarget = director.getDay4MouseTrackingTarget();
            }
            if (mouseTrackingTarget) {
                director.applyGuideHighlights({
                    key: scene.id + '-mouse-tracking-toggle',
                    persistent: settingsButton || null,
                    primary: mouseTrackingTarget
                });
            }
            director.enableInterrupts(director.currentStep);

            const narrationPromise = this.createNarrationPromise(scene, narration);

            await director.waitForSceneDelay(220);
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            if (mouseTrackingTarget) {
                const moved = await director.moveCursorToElement(mouseTrackingTarget, 620);
                if (moved && typeof director.runActionWithCursorClick === 'function') {
                    await director.runActionWithCursorClick(DEFAULT_CURSOR_CLICK_VISIBLE_MS, () => {
                        if (
                            this.shouldClickToggleToEnable(mouseTrackingTarget)
                            && typeof mouseTrackingTarget.click === 'function'
                        ) {
                            mouseTrackingTarget.click();
                        }
                    });
                    if (this.isSceneStale(sceneRunId)) {
                        return false;
                    }
                    await director.waitForSceneDelay(DAY4_GAZE_FOLLOW_CHECKED_DISPLAY_MS);
                }
            }

            await narrationPromise;
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            return this.finalizeNarration(sceneRunId, narration, normalizedContext);
        }

        async playDay4PrivacyModeScene(scene, context) {
            const director = this.director;
            const normalizedContext = context || {};
            const sceneRunId = normalizedContext.sceneRunId;
            const narration = this.prepareNarration(scene);

            director.collapseAvatarFloatingSidePanelsExcept(null);
            const settingsButton = director.getDay4SettingsButtonSpotlightTarget();
            const privacyButton = director.getDay4PrivacyModeButtonTarget();
            if (privacyButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-privacy-mode-button',
                    persistent: settingsButton || null,
                    primary: privacyButton
                });
            }
            director.enableInterrupts(director.currentStep);

            const narrationPromise = this.createNarrationPromise(scene, narration);

            await director.waitForSceneDelay(220);
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            if (privacyButton) {
                await director.moveCursorToElement(privacyButton, 620);
            }

            await narrationPromise;
            if (!this.isSceneStale(sceneRunId)) {
                this.safeHideSettingsPanels();
                await director.closeSettingsPanel().catch((error) => {
                    console.warn('[YuiGuide] 第4天隐私模式结束后收起设置面板失败，继续流程:', error);
                });
                if (this.isSceneStale(sceneRunId)) {
                    return false;
                }
                this.safeHideSettingsPanels();
            }
            return this.finalizeNarration(sceneRunId, narration, normalizedContext);
        }

        async playDay5CharacterSettingsScene(scene, context) {
            const director = this.director;
            const normalizedContext = context || {};
            const sceneRunId = normalizedContext.sceneRunId;
            const narration = this.prepareNarration(scene);

            const introExternalizedChatSpotlightKind = (
                director.isHomeChatExternalized()
                && director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
            )
                ? director.getAvatarFloatingIntroExternalizedSpotlightKind(scene)
                : '';
            const spotlightVariant = scene && typeof scene.spotlightVariant === 'string'
                ? scene.spotlightVariant.trim()
                : '';
            const introChatTarget = introExternalizedChatSpotlightKind
                ? null
                : director.getAvatarFloatingIntroSpotlightTarget(scene);
            if (introExternalizedChatSpotlightKind) {
                if (typeof director.clearHomeSpotlightsForExternalizedChat === 'function') {
                    director.clearHomeSpotlightsForExternalizedChat();
                }
                director.interactionTakeover.setExternalizedChatSpotlight(
                    introExternalizedChatSpotlightKind,
                    { variant: spotlightVariant }
                );
                if (typeof director.interactionTakeover.setExternalizedChatCursor === 'function') {
                    director.interactionTakeover.setExternalizedChatCursor(
                        introExternalizedChatSpotlightKind,
                        director.getAvatarFloatingIntroExternalizedCursorOptions(scene)
                    );
                }
                director.hideHomeCursorForExternalizedChat();
            } else if (introChatTarget) {
                director.applyGuideHighlights({
                    key: scene.id + '-intro-chat',
                    primary: introChatTarget
                });
                const introRect = director.getElementRect(introChatTarget);
                if (introRect) {
                    director.rememberAvatarFloatingSceneCursorAnchor(scene.id, introChatTarget);
                    director.cursor.showAt(
                        introRect.left + introRect.width / 2,
                        introRect.top + introRect.height / 2
                    );
                    director.cursor.wobble();
                }
            }
            director.enableInterrupts(director.currentStep);

            const narrationPromise = this.createNarrationPromise(scene, narration);

            await director.waitForSceneDelay(1000);
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            if (
                introExternalizedChatSpotlightKind
                && director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
            ) {
                director.interactionTakeover.setExternalizedChatSpotlight('');
            }
            director.overlay.clearActionSpotlight();
            director.overlay.clearPersistentSpotlight();

            const settingsButton = director.getDay4SettingsButtonSpotlightTarget();
            if (settingsButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-settings-button',
                    persistent: settingsButton,
                    primary: settingsButton
                });
                await director.moveCursorToElement(settingsButton, 760);
            }
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            await director.openSettingsPanel();
            if (typeof director.positionManagedPanelNow === 'function') {
                director.positionManagedPanelNow('settings');
            }
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            let characterSettingsPanel = await director.waitForElement(
                () => director.getAvatarFloatingSidePanel('character-settings'),
                1200
            );
            const characterSettingsButton = director.getDay5CharacterSettingsButtonTarget();
            if (characterSettingsButton) {
                director.applyGuideHighlights({
                    key: scene.id + '-character-settings-button',
                    persistent: settingsButton || null,
                    primary: characterSettingsButton
                });
                await director.moveCursorToElement(characterSettingsButton, 620);
            }
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }

            characterSettingsPanel = await director.ensureAvatarFloatingSettingsSidePanel('character-settings')
                || characterSettingsPanel;
            if (typeof director.refreshAvatarFloatingSettingsPanelLayout === 'function') {
                director.refreshAvatarFloatingSettingsPanelLayout(characterSettingsPanel);
            }
            await this.tourPanel(scene, sceneRunId, characterSettingsPanel, narrationPromise, {
                key: scene.id + '-character-settings-panel',
                persistent: settingsButton || null
            });

            return this.finalizeNarration(sceneRunId, narration, normalizedContext);
        }

        async playDay5CharacterPanicScene(scene, context) {
            const director = this.director;
            const normalizedContext = context || {};
            const sceneRunId = normalizedContext.sceneRunId;
            const narration = this.prepareNarration(scene);
            const { text, voiceKey } = narration;

            let characterSettingsPanel = director.getCharacterSettingsSidePanel();
            const hasVisibleCharacterPanel = characterSettingsPanel && (
                typeof director.isElementVisible !== 'function'
                || director.isElementVisible(characterSettingsPanel)
            );
            if (!hasVisibleCharacterPanel) {
                characterSettingsPanel = await director.ensureAvatarFloatingSettingsSidePanel('character-settings')
                    || characterSettingsPanel;
                if (this.isSceneStale(sceneRunId)) {
                    return false;
                }
            }
            const characterSettingsButton = director.getDay5CharacterSettingsButtonTarget();
            if (characterSettingsPanel && typeof director.refreshAvatarFloatingSettingsPanelLayout === 'function') {
                director.refreshAvatarFloatingSettingsPanelLayout(characterSettingsPanel);
            }
            if (characterSettingsPanel) {
                director.applyGuideHighlights({
                    key: scene.id + '-character-settings-panel',
                    persistent: characterSettingsButton || null,
                    primary: characterSettingsPanel
                });
                if (typeof director.moveCursorToElement === 'function') {
                    await director.moveCursorToElement(characterSettingsPanel, 0, {
                        exactDuration: true
                    });
                    if (this.isSceneStale(sceneRunId)) {
                        return false;
                    }
                }
            }
            director.enableInterrupts(director.currentStep);

            const narrationPromise = this.createNarrationPromise(scene, narration);
            const fullDurationMs = director.getAvatarFloatingNarrationDurationMs(voiceKey || '', text || '');
            const panicPromise = director.runSettingsPeekPanicPerformance({
                targetRect: director.getElementRect(characterSettingsPanel),
                totalDurationMs: Math.max(600, Math.round(fullDurationMs)),
                runId: sceneRunId
            }).catch(() => {});

            await Promise.all([narrationPromise, panicPromise]);
            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            director.overlay.clearActionSpotlight();
            director.overlay.clearPersistentSpotlight();
            director.collapseCharacterSettingsSidePanel();

            if (this.isSceneStale(sceneRunId)) {
                return false;
            }
            return this.finalizeNarration(sceneRunId, narration, normalizedContext);
        }

        prepareNarration(scene) {
            return this.director.prepareNarration(scene);
        }

        createNarrationPromise(scene, narration, options) {
            const normalizedNarration = narration || {};
            return this.director.createNarrationPromise(
                scene,
                normalizedNarration.text,
                normalizedNarration.voiceKey,
                options
            );
        }

        async finalize(sceneRunId, options) {
            return this.director.finalizeScene(sceneRunId, options);
        }

        isSceneStale(sceneRunId) {
            const director = this.director;
            return sceneRunId !== director.sceneRunId || director.isStopping();
        }

        shouldGuardPanelFlow(scene) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return sceneId.indexOf('day4_') === 0;
        }

        shouldClickToggleToEnable(target) {
            const checked = this.readToggleChecked(target);
            return checked !== true;
        }

        readToggleChecked(target) {
            if (!target) {
                return null;
            }
            const candidates = [];
            if (typeof target.querySelector === 'function') {
                const nestedToggle = target.querySelector('input[type="checkbox"]');
                if (nestedToggle && !candidates.includes(nestedToggle)) {
                    candidates.push(nestedToggle);
                }
            }
            if (!candidates.includes(target)) {
                candidates.push(target);
            }
            if (typeof target.querySelector === 'function') {
                const nestedToggle = target.querySelector('[role="switch"], [aria-checked]');
                if (nestedToggle && !candidates.includes(nestedToggle)) {
                    candidates.push(nestedToggle);
                }
            }
            for (const candidate of candidates) {
                if (!candidate) {
                    continue;
                }
                if (typeof candidate.checked === 'boolean') {
                    return !!candidate.checked;
                }
                if (typeof candidate.getAttribute === 'function') {
                    const ariaChecked = candidate.getAttribute('aria-checked');
                    if (ariaChecked === 'true') {
                        return true;
                    }
                    if (ariaChecked === 'false') {
                        return false;
                    }
                }
                if (candidate.classList && typeof candidate.classList.contains === 'function') {
                    if (
                        candidate.classList.contains('is-checked')
                        || candidate.classList.contains('checked')
                        || candidate.classList.contains('active')
                    ) {
                        return true;
                    }
                }
            }
            return null;
        }

        safeHideSettingsPanels() {
            const director = this.director;
            try {
                if (director && typeof director.forceHideManagedPanel === 'function') {
                    director.forceHideManagedPanel('settings');
                }
            } catch (error) {
                console.warn('[YuiGuide] 第4天隐私模式隐藏设置面板失败，继续流程:', error);
            }
            try {
                if (director && typeof director.collapseAvatarFloatingSidePanelsExcept === 'function') {
                    director.collapseAvatarFloatingSidePanelsExcept(null);
                }
            } catch (error) {
                console.warn('[YuiGuide] 第4天隐私模式收起侧栏失败，继续流程:', error);
            }
        }

        async ensurePanelForScene(panelId, sceneRunId, scene, options) {
            const director = this.director;
            if (this.isSceneStale(sceneRunId)) {
                return null;
            }
            const normalizedOptions = options || {};
            const ensureOptions = this.shouldGuardPanelFlow(scene)
                ? { shouldContinue: () => !this.isSceneStale(sceneRunId) }
                : undefined;
            if (normalizedOptions.skipOpenSettingsPanel) {
                return director.ensureAvatarFloatingSettingsSidePanel(panelId, Object.assign({}, ensureOptions, {
                    skipOpenSettingsPanel: true
                }));
            }
            return director.ensureAvatarFloatingSettingsSidePanel(panelId, ensureOptions);
        }

        async finalizeNarration(sceneRunId, narration, context) {
            const normalizedNarration = narration || {};
            const normalizedContext = context || {};
            return this.finalize(sceneRunId, {
                canHandleSceneButtons: normalizedNarration.canHandleSceneButtons,
                actionWaitPromise: normalizedNarration.actionWaitPromise,
                index: normalizedContext.index,
                total: normalizedContext.total
            });
        }

        async tourPanel(scene, sceneRunId, panel, narrationPromise, options) {
            const director = this.director;
            const normalizedOptions = options || {};
            if (panel) {
                const highlightConfig = {
                    key: normalizedOptions.key || scene.id + '-settings-panel',
                    primary: panel
                };
                if (Object.prototype.hasOwnProperty.call(normalizedOptions, 'persistent')) {
                    highlightConfig.persistent = normalizedOptions.persistent || null;
                }
                director.applyGuideHighlights(highlightConfig);
                if (Number.isFinite(normalizedOptions.cursorMoveDurationMs)) {
                    await director.moveCursorToElement(panel, normalizedOptions.cursorMoveDurationMs);
                }
                await this.runPanelNarrationEllipse(sceneRunId, panel, narrationPromise, {
                    scene,
                    durationMs: normalizedOptions.ellipseDurationMs,
                    minDurationMs: normalizedOptions.minDurationMs
                });
                return;
            }
            await (narrationPromise || Promise.resolve());
        }

        async runPanelNarrationEllipse(sceneRunId, panel, narrationPromise, options) {
            const director = this.director;
            const normalizedOptions = options || {};
            const resolvedNarrationPromise = narrationPromise || Promise.resolve();
            if (!panel) {
                await resolvedNarrationPromise;
                return;
            }

            const motionRect = director.getElementRect(panel);
            if (!motionRect) {
                await resolvedNarrationPromise;
                return;
            }

            const centerX = motionRect.left + motionRect.width / 2;
            const centerY = motionRect.top + motionRect.height / 2;
            const radiusX = Number.isFinite(normalizedOptions.radiusX)
                ? normalizedOptions.radiusX
                : Math.max(36, motionRect.width * 0.32);
            const radiusY = Number.isFinite(normalizedOptions.radiusY)
                ? normalizedOptions.radiusY
                : Math.max(60, motionRect.height * 0.36);
            const durationMs = Number.isFinite(normalizedOptions.durationMs)
                ? Math.max(0, normalizedOptions.durationMs)
                : 5600;
            const minDurationMs = Number.isFinite(normalizedOptions.minDurationMs)
                ? Math.max(0, normalizedOptions.minDurationMs)
                : 0;
            let narrationDone = false;
            const shouldGuardDelay = this.shouldGuardPanelFlow(normalizedOptions.scene);
            const isPanelFlowStale = () => sceneRunId !== director.sceneRunId || director.isStopping();
            const waitForSceneDelay = (delayMs) => {
                if (typeof director.waitForSceneDelay === 'function') {
                    const delayOptions = shouldGuardDelay
                        ? { shouldContinue: () => !isPanelFlowStale() }
                        : undefined;
                    return director.waitForSceneDelay(delayMs, delayOptions);
                }
                return new Promise((resolve) => {
                    const timerApi = typeof window !== 'undefined' && window.setTimeout
                        ? window
                        : globalThis;
                    timerApi.setTimeout(resolve, delayMs);
                });
            };
            const minimumDisplayPromise = minDurationMs > 0
                ? waitForSceneDelay(minDurationMs)
                : Promise.resolve();
            const guardedNarrationPromise = Promise.all([
                resolvedNarrationPromise,
                minimumDisplayPromise
            ]).finally(() => {
                narrationDone = true;
            });
            const narrationSettledPromise = guardedNarrationPromise.catch(() => {});
            const ellipseYieldMs = Math.min(160, Math.max(16, Math.round(durationMs / 60)));
            const waitForEllipseYield = async () => {
                if (narrationDone || director.isStopping()) {
                    return;
                }
                const delayPromise = waitForSceneDelay(ellipseYieldMs);
                await Promise.race([narrationSettledPromise, delayPromise]);
            };
            const ellipsePromise = (async () => {
                if (typeof director.setHomePcCursorOutputSuppressedForExternalizedChat === 'function') {
                    director.setHomePcCursorOutputSuppressedForExternalizedChat(false);
                }
                while (!isPanelFlowStale() && !narrationDone) {
                    const moved = await director.cursor.runPauseAwareEllipse(
                        centerX,
                        centerY,
                        radiusX,
                        radiusY,
                        durationMs,
                        () => (
                            narrationDone
                            || director.destroyed
                            || director.angryExitTriggered
                            || (shouldGuardDelay && isPanelFlowStale())
                        ),
                        () => director.scenePausedForResistance,
                        () => director.isStopping()
                    );
                    if (!moved && !director.scenePausedForResistance) {
                        return;
                    }
                    if (!moved) {
                        await director.waitUntilSceneResumed();
                        continue;
                    }
                    await waitForEllipseYield();
                }
            })();
            if (shouldGuardDelay) {
                const ellipseResultPromise = ellipsePromise.then(() => {
                    return isPanelFlowStale() ? false : guardedNarrationPromise;
                });
                await Promise.race([guardedNarrationPromise, ellipseResultPromise]);
                await ellipsePromise;
                return;
            }
            await Promise.all([guardedNarrationPromise, ellipsePromise]);
        }
    }

    return {
        SettingsTourFlow
    };
});
