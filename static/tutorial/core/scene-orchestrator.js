(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialSceneOrchestrator = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    function loadTutorialScriptNormalizerApi() {
        const root = typeof window !== 'undefined' ? window : globalThis;
        if (root && root.TutorialScriptNormalizer) {
            return root.TutorialScriptNormalizer;
        }
        if (typeof require === 'function') {
            try {
                return require('./script-normalizer.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialCommandRegistryApi() {
        const root = typeof window !== 'undefined' ? window : globalThis;
        if (root && root.TutorialCommandRegistry) {
            return root.TutorialCommandRegistry;
        }
        if (typeof require === 'function') {
            try {
                return require('./command-registry.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialTimelineEngineApi() {
        const root = typeof window !== 'undefined' ? window : globalThis;
        if (root && root.TutorialTimelineEngine) {
            return root.TutorialTimelineEngine;
        }
        if (typeof require === 'function') {
            try {
                return require('./timeline-engine.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialVisualRuntimeApi() {
        const root = typeof window !== 'undefined' ? window : globalThis;
        if (root && root.TutorialVisualRuntime) {
            return root.TutorialVisualRuntime;
        }
        if (typeof require === 'function') {
            try {
                return require('./visual-runtime.js');
            } catch (_) {}
        }
        return null;
    }

    class SceneOrchestrator {
        constructor(director) {
            this.director = director;
            this.scriptNormalizer = loadTutorialScriptNormalizerApi();
            this.commandRegistryApi = loadTutorialCommandRegistryApi();
            this.timelineEngineApi = loadTutorialTimelineEngineApi();
            this.visualRuntimeApi = loadTutorialVisualRuntimeApi();
        }

        normalizeSceneToTimeline(scene, options) {
            if (
                this.scriptNormalizer
                && typeof this.scriptNormalizer.normalizeTutorialScene === 'function'
            ) {
                return this.scriptNormalizer.normalizeTutorialScene(scene, options);
            }
            return null;
        }

        canPlayTimelineScene(scene) {
            return !!(
                scene
                && scene.timelinePlayback === true
                && this.commandRegistryApi
                && typeof this.commandRegistryApi.createTutorialCommandRegistry === 'function'
                && this.timelineEngineApi
                && typeof this.timelineEngineApi.createTutorialTimelineEngine === 'function'
                && this.visualRuntimeApi
                && typeof this.visualRuntimeApi.createTutorialVisualRuntime === 'function'
            );
        }

        createTimelineAudioRuntime(scene, timelineScene, context) {
            const director = this.director;
            let narrationPromise = Promise.resolve();
            const legacyScene = scene || {};
            const audio = timelineScene && timelineScene.audio ? timelineScene.audio : {};
            const resolveText = () => {
                if (typeof director.resolveAvatarFloatingSceneText === 'function') {
                    return director.resolveAvatarFloatingSceneText(legacyScene) || audio.text || legacyScene.text || '';
                }
                return audio.text || legacyScene.text || '';
            };
            const resolveVoiceKey = (fallbackVoiceKey) => {
                if (typeof director.resolveAvatarFloatingSceneVoiceKey === 'function') {
                    return director.resolveAvatarFloatingSceneVoiceKey(legacyScene)
                        || fallbackVoiceKey
                        || audio.voiceKey
                        || legacyScene.voiceKey
                        || '';
                }
                return fallbackVoiceKey || audio.voiceKey || legacyScene.voiceKey || '';
            };
            return {
                play: (voiceKey, audioOptions) => {
                    const text = resolveText();
                    const resolvedVoiceKey = resolveVoiceKey(voiceKey);
                    if (typeof director.speakGuideLine === 'function' && (text || resolvedVoiceKey)) {
                        narrationPromise = Promise.resolve(director.speakGuideLine(text, {
                            voiceKey: resolvedVoiceKey,
                            minDurationMs: Number.isFinite(audioOptions && audioOptions.minDurationMs)
                                ? Math.max(0, Math.floor(audioOptions.minDurationMs))
                                : 1800
                        })).catch((error) => {
                            console.warn('[YuiGuide] Timeline scene narration failed, continuing:', legacyScene.id, error);
                        });
                    }
                    context.narrationPromise = narrationPromise;
                    context.narrationStartedAt = Date.now();
                    return null;
                },
                waitForEnd: () => narrationPromise,
                getDurationMs: (voiceKey) => {
                    if (typeof director.getGuideVoiceDurationMs === 'function') {
                        return director.getGuideVoiceDurationMs(resolveVoiceKey(voiceKey || audio.voiceKey), '');
                    }
                    if (Number.isFinite(audio.durationMs)) {
                        return audio.durationMs;
                    }
                    return Number.isFinite(audio.minDurationMs) ? audio.minDurationMs : 0;
                },
                resolveCueMs: (voiceKey, cueName) => {
                    if (typeof director.resolveGuideVoiceCueTargetMs === 'function') {
                        return director.resolveGuideVoiceCueTargetMs(
                            resolveVoiceKey(voiceKey || audio.voiceKey),
                            cueName,
                            0,
                            resolveText()
                        );
                    }
                    return 0;
                }
            };
        }

        clearSuppressedTimelineSpotlight(scene) {
            const director = this.director;
            if (!scene || scene.spotlight !== false || !director) {
                return false;
            }

            if (director.overlay && typeof director.overlay.clearActionSpotlight === 'function') {
                director.overlay.clearActionSpotlight();
            }
            if (director.overlay && typeof director.overlay.clearPersistentSpotlight === 'function') {
                director.overlay.clearPersistentSpotlight();
            }
            if (typeof director.clearExternalizedChatSpotlightOnly === 'function') {
                director.clearExternalizedChatSpotlightOnly();
            } else if (
                director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
            ) {
                director.interactionTakeover.setExternalizedChatSpotlight('');
            }
            return true;
        }

        async playTimelineScene(scene, day, index, total, context) {
            const surfaceReady = await this.prepareGenericSceneSurface(scene, context);
            if (!surfaceReady) {
                return false;
            }
            this.clearSuppressedTimelineSpotlight(scene);
            const timelineScene = this.normalizeSceneToTimeline(scene);
            if (!timelineScene) {
                return false;
            }
            if (typeof this.director.enableInterrupts === 'function') {
                this.director.enableInterrupts(this.director.currentStep);
            }
            const commandRegistry = this.commandRegistryApi.createTutorialCommandRegistry();
            const visualRuntime = this.visualRuntimeApi.createTutorialVisualRuntime(this.director);
            visualRuntime.registerCommands(commandRegistry);
            const timelineContext = Object.assign({}, context, {
                day,
                index,
                total,
                director: this.director,
                legacyScene: scene
            });
            const audioRuntime = this.createTimelineAudioRuntime(scene, timelineScene, timelineContext);
            const timelineEngine = this.timelineEngineApi.createTutorialTimelineEngine({
                commandRegistry,
                audioRuntime,
                isPaused: () => this.director.scenePausedForResistance === true,
                waitUntilResumed: () => (
                    typeof this.director.waitUntilSceneResumed === 'function'
                        ? this.director.waitUntilSceneResumed()
                        : Promise.resolve()
                ),
                isCancelled: () => (
                    context.sceneRunId !== this.director.sceneRunId
                    || (typeof this.director.isStopping === 'function' && this.director.isStopping())
                )
            });
            const result = await timelineEngine.playScene(timelineScene, timelineContext);
            if (!result || result.completed !== true) {
                return false;
            }
            const afterSceneDelayMs = timelineScene.completion && Number.isFinite(timelineScene.completion.afterSceneDelayMs)
                ? Math.max(0, Math.floor(timelineScene.completion.afterSceneDelayMs))
                : (index >= total - 1 ? 260 : 420);
            if (afterSceneDelayMs > 0 && typeof this.director.waitForSceneDelay === 'function') {
                await this.director.waitForSceneDelay(afterSceneDelayMs);
            }
            return context.sceneRunId === this.director.sceneRunId
                && !(typeof this.director.isStopping === 'function' && this.director.isStopping());
        }

        prepareSceneNarration(scene) {
            const director = this.director;
            const text = director.resolveAvatarFloatingSceneText(scene);
            const voiceKey = director.resolveAvatarFloatingSceneVoiceKey(scene);
            const sceneButtons = director.getAvatarFloatingSceneButtons(scene);
            const canHandleSceneButtons = sceneButtons.length > 0
                ? director.installGuideMessageActionHandler()
                : false;
            const actionWaitPromise = canHandleSceneButtons
                ? director.beginGuideMessageActionWait(sceneButtons, 0)
                : null;
            if (text) {
                director.appendGuideChatMessage(text, {
                    textKey: scene.textKey || '',
                    voiceKey: voiceKey,
                    buttons: sceneButtons
                });
            }
            const sceneEmotion = director.resolveAvatarFloatingSceneEmotion(scene);
            if (sceneEmotion) {
                director.applyGuideEmotion(sceneEmotion);
            }
            return {
                text,
                voiceKey,
                sceneButtons,
                canHandleSceneButtons,
                actionWaitPromise
            };
        }

        async playScene(scene, day, index, total, roundContext = {}) {
            const director = this.director;
            if (
                director.scenePausedForResistance
                && typeof director.waitUntilSceneResumed === 'function'
            ) {
                await director.waitUntilSceneResumed();
                if (typeof director.isStopping === 'function' && director.isStopping()) {
                    return false;
                }
            }
            const sceneRunId = ++director.sceneRunId;
            const previousSceneId = director.currentSceneId;
            director.currentSceneId = scene.id;
            director.currentStep = director.getAvatarFloatingInterruptStep(scene);
            if (typeof director.syncAvatarFloatingToolbarForScene === 'function') {
                director.syncAvatarFloatingToolbarForScene(scene, scene.id || 'scene');
            }
            if (typeof director.syncDay4LockSpotlightSafeAreaForScene === 'function') {
                director.syncDay4LockSpotlightSafeAreaForScene(scene);
            }
            const isFirstDailyScene = index === 0;
            const preserveExternalizedChatGuideTarget = !!(
                director.shouldPreserveExternalizedChatCursor(previousSceneId, scene)
                || (scene && scene.preserveExternalizedChatGuideTarget === true)
            );
            const preserveIntroExternalizedChatGuideTarget = !!(
                isFirstDailyScene
                && director.isHomeChatExternalized()
                && director.shouldPreserveIntroExternalizedChatCursor(scene)
            );
            if (isFirstDailyScene) {
                director.cursor.cancel();
                director.cursorAnchorStore.clear();
            }
            const shouldClearExternalizedChatCursor = !!(
                scene && scene.clearExternalizedChatCursorOnEnter === true
            );
            director.clearSceneTimers();
            director.overlay.setAngry(false);
            if (
                shouldClearExternalizedChatCursor
                && director.isHomeChatExternalized()
                && !preserveExternalizedChatGuideTarget
                && !preserveIntroExternalizedChatGuideTarget
            ) {
                director.clearExternalizedChatGuideTarget({
                    clearCursor: true,
                    preservePcOverlayCursor: true
                });
            }
            director.clearSceneExtraSpotlights();
            director.clearAllVirtualSpotlights();
            director.clearSpotlightGeometryHints();
            director.clearSpotlightVariantHints();
            if (
                director.isHomeChatExternalized()
                && !preserveExternalizedChatGuideTarget
                && !preserveIntroExternalizedChatGuideTarget
                && !shouldClearExternalizedChatCursor
            ) {
                director.clearExternalizedChatGuideTarget({
                    clearCursor: shouldClearExternalizedChatCursor,
                    preservePcOverlayCursor: shouldClearExternalizedChatCursor
                });
            }
            const introCursorPreludeApplied = this.applyFirstDailySceneIntroCursorPrelude(scene, {
                isFirstDailyScene,
                preserveIntroExternalizedChatGuideTarget
            });

            if (this.canPlayTimelineScene(scene)) {
                return await this.playTimelineScene(scene, day, index, total, {
                    day,
                    sceneRunId,
                    previousSceneId,
                    isFirstDailyScene,
                    preserveExternalizedChatGuideTarget,
                    preserveIntroExternalizedChatGuideTarget,
                    revealPrepared: roundContext.revealPrepared
                });
            }

            if (
                director.settingsTourFlow
                && typeof director.settingsTourFlow.canHandle === 'function'
                && director.settingsTourFlow.canHandle(scene)
                && typeof director.settingsTourFlow.play === 'function'
            ) {
                return await director.settingsTourFlow.play(scene, {
                    sceneRunId,
                    previousSceneId,
                    index,
                    total
                });
            }

            return await this.playGenericScene(scene, day, index, total, {
                day,
                sceneRunId,
                previousSceneId,
                isFirstDailyScene,
                preserveExternalizedChatGuideTarget,
                preserveIntroExternalizedChatGuideTarget,
                introCursorPreludeApplied,
                revealPrepared: roundContext.revealPrepared
            });
        }

        async prepareGenericSceneSurface(scene, context) {
            const director = this.director;
            const sceneRunId = context.sceneRunId;
            const isFirstDailyScene = context.isFirstDailyScene;
            const preserveExternalizedChatGuideTarget = context.preserveExternalizedChatGuideTarget;
            if (!isFirstDailyScene) {
                await director.prepareAvatarFloatingScene(scene, {
                    preserveExternalizedChatGuideTarget
                });
                if (sceneRunId !== director.sceneRunId || director.isStopping()) {
                    return false;
                }
            }
            if (
                director
                && typeof director.scheduleAvatarStandInForScene === 'function'
                && sceneRunId === director.sceneRunId
                && !director.isStopping()
            ) {
                director.scheduleAvatarStandInForScene(scene, context.day, sceneRunId);
            }
            return true;
        }

        applyFirstDailySceneIntroCursorPrelude(scene, context) {
            const director = this.director;
            const normalizedContext = context || {};
            if (
                !normalizedContext.isFirstDailyScene
                || !scene
                || !director
                || typeof director.isAvatarFloatingInputIntroScene !== 'function'
                || !director.isAvatarFloatingInputIntroScene(scene)
            ) {
                return false;
            }

            if (
                typeof director.isHomeChatExternalized === 'function'
                && director.isHomeChatExternalized()
                && director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatCursor === 'function'
            ) {
                const introExternalizedCursorKind = typeof director.getAvatarFloatingIntroExternalizedSpotlightKind === 'function'
                    ? director.getAvatarFloatingIntroExternalizedSpotlightKind(scene)
                    : 'capsule-input';
                if (typeof director.setHomePcCursorOutputSuppressedForExternalizedChat === 'function') {
                    director.setHomePcCursorOutputSuppressedForExternalizedChat(true);
                }
                const introExternalizedCursorOptions = typeof director.getAvatarFloatingIntroExternalizedCursorOptions === 'function'
                    ? director.getAvatarFloatingIntroExternalizedCursorOptions(scene)
                    : {
                        effect: '',
                        durationMs: 0
                    };
                director.interactionTakeover.setExternalizedChatCursor(
                    introExternalizedCursorKind || 'capsule-input',
                    introExternalizedCursorOptions
                );
                if (typeof director.hideHomeCursorForExternalizedChat === 'function') {
                    director.hideHomeCursorForExternalizedChat();
                }
                return true;
            }

            const introTarget = typeof director.getAvatarFloatingIntroSpotlightTarget === 'function'
                ? director.getAvatarFloatingIntroSpotlightTarget(scene)
                : (typeof director.getChatInputTarget === 'function' ? director.getChatInputTarget() : null);
            const introRect = introTarget && typeof director.getElementRect === 'function'
                ? director.getElementRect(introTarget)
                : null;
            if (introRect && director.cursor && typeof director.cursor.showAt === 'function') {
                director.cursor.showAt(
                    introRect.left + introRect.width / 2,
                    introRect.top + introRect.height / 2
                );
                return true;
            }
            return false;
        }

        async resolveAndApplySceneSpotlight(scene, context) {
            const director = this.director;
            const isFirstDailyScene = context.isFirstDailyScene;
            const introChatSpotlightTarget = isFirstDailyScene
                ? director.getAvatarFloatingIntroSpotlightTarget(scene)
                : null;
            const introExternalizedChatSpotlightKind = (
                isFirstDailyScene
                && director.isHomeChatExternalized()
                && director.interactionTakeover
                && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
            )
                ? director.getAvatarFloatingIntroExternalizedSpotlightKind(scene)
                : '';
            const externalizedSceneTargetKind = (
                !isFirstDailyScene
                && director.isHomeChatExternalized()
            )
                ? director.getExternalizedChatTargetKind(scene.target || '', scene)
                : '';
            const externalizedSceneCursorKind = (
                !isFirstDailyScene
                && director.isHomeChatExternalized()
            )
                ? director.getExternalizedChatCursorTargetKind(scene)
                : '';
            const externalizedScenePersistentKind = (
                !isFirstDailyScene
                && director.isHomeChatExternalized()
                && scene
                && typeof scene.persistent === 'string'
            )
                ? director.getExternalizedChatTargetKind(scene.persistent, scene)
                : '';
            const shouldShowSceneSpotlight = scene.spotlight !== false;
            const sceneSpotlightVariant = scene && typeof scene.spotlightVariant === 'string'
                ? scene.spotlightVariant.trim()
                : '';
            const externalizedSpotlightOptions = {
                variant: sceneSpotlightVariant
            };
            let persistentTarget = null;
            let primaryTarget = null;
            let secondaryTarget = null;
            if (introExternalizedChatSpotlightKind) {
                if (typeof director.clearHomeSpotlightsForExternalizedChat === 'function') {
                    director.clearHomeSpotlightsForExternalizedChat();
                }
                director.interactionTakeover.setExternalizedChatSpotlight(
                    introExternalizedChatSpotlightKind,
                    externalizedSpotlightOptions
                );
                if (typeof director.setHomePcCursorOutputSuppressedForExternalizedChat === 'function') {
                    director.setHomePcCursorOutputSuppressedForExternalizedChat(true);
                }
                if (
                    !context.introCursorPreludeApplied
                    && typeof director.interactionTakeover.setExternalizedChatCursor === 'function'
                ) {
                    director.interactionTakeover.setExternalizedChatCursor(
                        introExternalizedChatSpotlightKind,
                        director.getAvatarFloatingIntroExternalizedCursorOptions(scene)
                    );
                }
                director.hideHomeCursorForExternalizedChat();
            } else if (introChatSpotlightTarget) {
                director.applyGuideHighlights({
                    key: scene.id + '-intro-chat',
                    primary: introChatSpotlightTarget
                });
                const introRect = director.getElementRect(introChatSpotlightTarget);
                if (introRect) {
                    director.rememberAvatarFloatingSceneCursorAnchor(scene.id, introChatSpotlightTarget);
                    director.cursor.showAt(
                        introRect.left + introRect.width / 2,
                        introRect.top + introRect.height / 2
                    );
                    if (scene.cursorAction !== 'move') {
                        director.cursor.wobble();
                    }
                } else {
                    const introCursorOrigin = director.getDefaultCursorOrigin();
                    director.cursor.showAt(introCursorOrigin.x, introCursorOrigin.y);
                }
            } else if (externalizedSceneTargetKind) {
                const shouldSkipExternalizedSceneCursor = !!(
                    scene
                    && scene.id === 'day2_galgame_entry'
                    && scene.operation === 'rotate-galgame-tool-into-center'
                );
                const externalizedCursorOptions = {
                    effect: director.getExternalizedChatCursorEffect(scene),
                    spotlightVariant: sceneSpotlightVariant
                };
                if (scene && scene.cursorAction === 'click') {
                    externalizedCursorOptions.effect = 'move';
                }
                const externalizedCursorMoveDurationMs = director.getExternalizedChatCursorMoveDurationMs(scene, 760);
                if (externalizedCursorMoveDurationMs > 0) {
                    externalizedCursorOptions.durationMs = externalizedCursorMoveDurationMs;
                }
                if (Number.isFinite(scene.cursorWobbleDurationMs)) {
                    externalizedCursorOptions.effectDurationMs = Math.max(0, Math.floor(scene.cursorWobbleDurationMs));
                }
                if (scene.freezeCursorAfterMove === true) {
                    externalizedCursorOptions.freezePoint = true;
                }
                const shouldPreferExternalizedPrimaryKind = !!(
                    scene
                    && typeof scene.cursorAction === 'string'
                    && scene.cursorAction
                    && scene.cursorAction !== 'hold'
                );
                const externalizedSpotlightKind = shouldPreferExternalizedPrimaryKind
                    ? (externalizedSceneTargetKind || externalizedScenePersistentKind)
                    : (externalizedScenePersistentKind || externalizedSceneTargetKind);
                let externalizedCursorAlreadySet = false;
                if (
                    !shouldSkipExternalizedSceneCursor
                    && shouldShowSceneSpotlight
                    && externalizedSpotlightKind === externalizedSceneCursorKind
                ) {
                    director.setExternalizedChatGuideTarget(externalizedSpotlightKind, externalizedCursorOptions);
                    externalizedCursorAlreadySet = true;
                } else if (shouldShowSceneSpotlight) {
                    if (
                        director.interactionTakeover
                        && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
                    ) {
                        if (typeof director.clearHomeSpotlightsForExternalizedChat === 'function') {
                            director.clearHomeSpotlightsForExternalizedChat();
                        }
                        director.interactionTakeover.setExternalizedChatSpotlight(
                            externalizedSpotlightKind,
                            externalizedSpotlightOptions
                        );
                    }
                } else {
                    director.clearExternalizedChatSpotlightOnly();
                }
                if (
                    externalizedSceneCursorKind
                    && !shouldSkipExternalizedSceneCursor
                    && !externalizedCursorAlreadySet
                    && scene.cursorAction !== 'hold'
                ) {
                    director.setExternalizedChatCursorEffect(
                        externalizedSceneCursorKind,
                        externalizedCursorOptions.effect,
                        externalizedCursorOptions
                    );
                }
            } else {
                persistentTarget = await director.resolveAvatarFloatingPersistent(scene, {
                    fallbackToChatWindow: false
                });
                primaryTarget = await director.resolveAvatarFloatingTarget(scene, 'primary');
                secondaryTarget = await director.resolveAvatarFloatingTarget(scene, 'secondary');
                const highlightConfig = {
                    key: scene.spotlightKey || scene.id,
                    persistent: shouldShowSceneSpotlight ? persistentTarget : null,
                    primary: shouldShowSceneSpotlight ? primaryTarget : null,
                    secondary: shouldShowSceneSpotlight ? secondaryTarget : null
                };
                if (typeof director.applyAvatarFloatingPersistenceOverride === 'function') {
                    director.applyAvatarFloatingPersistenceOverride(highlightConfig, scene.id);
                }
                director.applyAvatarFloatingSceneSpotlightVariant(scene, primaryTarget);
                director.applyGuideHighlights(highlightConfig);
            }
            director.enableInterrupts(director.currentStep);
            return {
                introChatSpotlightTarget,
                introExternalizedChatSpotlightKind,
                externalizedSceneTargetKind,
                persistentTarget,
                primaryTarget,
                secondaryTarget
            };
        }

        createScenePlaybackPromises(scene, context, narration) {
            const director = this.director;
            const sceneRunId = context.sceneRunId;
            const text = narration.text;
            const voiceKey = narration.voiceKey;
            const narrationStartedAt = Date.now();
            const shouldPlayNarration = !!(text || voiceKey);
            const narrationPromise = shouldPlayNarration ? director.speakGuideLine(text, {
                voiceKey: voiceKey,
                minDurationMs: 1800
            }).catch((error) => {
                console.warn('[YuiGuide] 悬浮窗教程旁白失败，继续流程:', scene.id, error);
            }) : Promise.resolve();
            const petalTransitionPromise = scene.petalTransition === true
                ? director.playAvatarFloatingPetalTransitionAtCue(
                    scene,
                    sceneRunId,
                    voiceKey,
                    text,
                    narrationStartedAt,
                    scene.petalCueBeforeAudioEndMs
                ).catch((error) => {
                    console.warn('[YuiGuide] 悬浮窗教程每日花瓣转场失败，继续流程:', scene.id, error);
                })
                : null;
            return {
                narrationStartedAt,
                narrationPromise,
                petalTransitionPromise
            };
        }

        async completeIntroSpotlightIfNeeded(scene, index, total, context, narration, playback, sceneTargets) {
            const director = this.director;
            const sceneRunId = context.sceneRunId;
            const preserveExternalizedChatGuideTarget = context.preserveExternalizedChatGuideTarget;
            const introChatSpotlightTarget = sceneTargets.introChatSpotlightTarget;
            const introExternalizedChatSpotlightKind = sceneTargets.introExternalizedChatSpotlightKind;
            if (!introChatSpotlightTarget && !introExternalizedChatSpotlightKind) {
                return sceneTargets;
            }
            const introExternalizedChatStreamPromise = introExternalizedChatSpotlightKind
                ? director.waitForSceneDelay(director.resolveGuideChatStreamDurationMs(narration.text, {
                    voiceKey: narration.voiceKey
                }))
                : null;
            if (introExternalizedChatStreamPromise) {
                await Promise.all([
                    playback.narrationPromise,
                    introExternalizedChatStreamPromise
                ]);
            } else {
                await playback.narrationPromise;
            }
            if (introExternalizedChatSpotlightKind) {
                director.rememberAvatarFloatingSceneCursorAnchorFromExternalizedChat(scene.id, 30000);
                director.interactionTakeover.setExternalizedChatSpotlight('');
            }
            director.overlay.clearActionSpotlight();
            director.overlay.clearPersistentSpotlight();
            if (narration.canHandleSceneButtons && director.pendingGuideMessageAction) {
                director.armPendingGuideMessageActionTimeout(12000);
            }
            if (narration.actionWaitPromise && sceneRunId === director.sceneRunId && !director.isStopping()) {
                await narration.actionWaitPromise;
            }
            if (sceneRunId !== director.sceneRunId || director.isStopping()) {
                return {
                    completed: true,
                    result: false
                };
            }
            await director.prepareAvatarFloatingScene(scene, {
                preserveExternalizedChatGuideTarget
            });
            if (sceneRunId !== director.sceneRunId || director.isStopping()) {
                return {
                    completed: true,
                    result: false
                };
            }
            const persistentTarget = await director.resolveAvatarFloatingPersistent(scene, {
                fallbackToChatWindow: false
            });
            const primaryTarget = await director.resolveAvatarFloatingTarget(scene, 'primary');
            const secondaryTarget = await director.resolveAvatarFloatingTarget(scene, 'secondary');
            const shouldShowcaseScene = !!(
                persistentTarget
                || primaryTarget
                || secondaryTarget
                || scene.operation
            );
            const onlyChatTarget = primaryTarget === introChatSpotlightTarget
                && !persistentTarget
                && !secondaryTarget
                && !scene.operation;
            if (!shouldShowcaseScene || onlyChatTarget) {
                await director.waitForSceneDelay(index >= total - 1 ? 260 : 420);
                return {
                    completed: true,
                    result: sceneRunId === director.sceneRunId && !director.isStopping()
                };
            }
            const highlightConfig = {
                key: scene.spotlightKey || scene.id,
                persistent: persistentTarget,
                primary: primaryTarget,
                secondary: secondaryTarget
            };
            if (typeof director.applyAvatarFloatingPersistenceOverride === 'function') {
                director.applyAvatarFloatingPersistenceOverride(highlightConfig, scene.id);
            }
            director.applyAvatarFloatingSceneSpotlightVariant(scene, primaryTarget);
            director.applyGuideHighlights(highlightConfig);
            return Object.assign({}, sceneTargets, {
                persistentTarget,
                primaryTarget,
                secondaryTarget
            });
        }

        async runSceneCursorAndOperation(scene, context, sceneTargets, narrationStartedAt, narrationPromise) {
            const director = this.director;
            const sceneRunId = context.sceneRunId;
            const previousSceneId = context.previousSceneId;
            const externalizedSceneTargetKind = sceneTargets.externalizedSceneTargetKind;
            const primaryTarget = sceneTargets.primaryTarget;
            const secondaryTarget = sceneTargets.secondaryTarget;
            let sceneOperationStarted = false;
            let sceneOperationPromise = null;
            const startSceneOperation = () => {
                if (!sceneOperationStarted) {
                    sceneOperationStarted = true;
                    try {
                        sceneOperationPromise = Promise.resolve(
                            director.runAvatarFloatingSceneOperation(
                                scene,
                                primaryTarget,
                                narrationStartedAt,
                                narrationPromise
                            )
                        );
                    } catch (error) {
                        sceneOperationPromise = Promise.reject(error);
                    }
                    sceneOperationPromise.catch(() => {});
                }
                return sceneOperationPromise;
            };
            if (scene.cursorAction === 'hold') {
                // Cursor is already at the intended target from the previous scene.
            } else if (externalizedSceneTargetKind && scene.cursorAction === 'click') {
                await director.moveExternalizedChatCursor(scene, {
                    onClickStart: startSceneOperation
                });
            } else if (externalizedSceneTargetKind && scene.cursorAction === 'move') {
                const externalizedMoveWaitMs = director.getExternalizedChatCursorMoveDurationMs(scene, 760);
                await director.waitForExternalizedChatCursorMove(
                    scene.id || '',
                    externalizedMoveWaitMs > 0 ? externalizedMoveWaitMs + 500 : undefined
                );
            } else if (!externalizedSceneTargetKind) {
                const cursorTarget = scene.cursorTarget
                    ? await director.resolveAvatarFloatingSelector(scene.cursorTarget)
                    : null;
                await director.moveAvatarFloatingCursor(scene, cursorTarget || primaryTarget, secondaryTarget, previousSceneId, {
                    onClickStart: startSceneOperation
                });
            }
            if (sceneRunId !== director.sceneRunId || director.isStopping()) {
                return false;
            }
            await startSceneOperation();
            if (!externalizedSceneTargetKind && scene.operation === 'cleanup') {
                await this.applySettledCleanupHighlight(scene);
            }
            return true;
        }

        async applySettledCleanupHighlight(scene) {
            const director = this.director;
            if (typeof director.applyAvatarFloatingSettledCleanupHighlight === 'function') {
                await director.applyAvatarFloatingSettledCleanupHighlight(scene);
            }
        }

        async finishGenericScene(scene, index, total, context, narration, playback) {
            const director = this.director;
            const sceneRunId = context.sceneRunId;
            await playback.narrationPromise;
            if (narration.canHandleSceneButtons && director.pendingGuideMessageAction) {
                director.armPendingGuideMessageActionTimeout(12000);
            }
            if (narration.actionWaitPromise && sceneRunId === director.sceneRunId && !director.isStopping()) {
                await narration.actionWaitPromise;
            }
            if (playback.petalTransitionPromise) {
                await playback.petalTransitionPromise;
            }
            if (sceneRunId !== director.sceneRunId || director.isStopping()) {
                return false;
            }
            const afterSceneDelayMs = scene && Number.isFinite(scene.afterSceneDelayMs)
                ? Math.max(0, Math.floor(scene.afterSceneDelayMs))
                : (index >= total - 1 ? 260 : 420);
            await director.waitForSceneDelay(afterSceneDelayMs);
            return sceneRunId === director.sceneRunId && !director.isStopping();
        }

        async playGenericScene(scene, day, index, total, context) {
            const director = this.director;
            const sceneRunId = context.sceneRunId;
            const surfaceReady = await this.prepareGenericSceneSurface(scene, context);
            if (!surfaceReady) {
                return false;
            }

            const narration = this.prepareSceneNarration(scene);
            let sceneTargets = await this.resolveAndApplySceneSpotlight(scene, context);
            const playback = this.createScenePlaybackPromises(scene, context, narration);
            sceneTargets = await this.completeIntroSpotlightIfNeeded(
                scene,
                index,
                total,
                context,
                narration,
                playback,
                sceneTargets
            );
            if (sceneTargets && sceneTargets.completed) {
                return sceneTargets.result;
            }

            await director.waitForSceneDelay(220);
            if (sceneRunId !== director.sceneRunId || director.isStopping()) {
                return false;
            }
            const cursorCompleted = await this.runSceneCursorAndOperation(
                scene,
                context,
                sceneTargets,
                playback.narrationStartedAt,
                playback.narrationPromise
            );
            if (!cursorCompleted) {
                return false;
            }
            return await this.finishGenericScene(scene, index, total, context, narration, playback);
        }

        async playRound(round, options) {
            const director = this.director;
            const config = director.getAvatarFloatingRoundConfig(round);
            if (!config || !Array.isArray(config.scenes) || config.scenes.length === 0) {
                return false;
            }
            const roundNumber = Number(round);
            const roundId = 'avatar_floating_day' + roundNumber;
            const startedAt = Date.now();
            director.recordExperienceMetric('avatar_floating_round_start', {
                round: roundNumber,
                source: options && options.source ? options.source : ''
            });
            const isDay1Round = roundNumber === 1;
            if (isDay1Round) {
                director.day1RoundWakeupCompleted = false;
            }
            director.overlay.hideBubble();
            if (!options || options.surfaceReady !== true) {
                await director.ensureAvatarFloatingGuideSurfaceReady(round);
            }
            director.setGuideChatInputLocked(true, 'avatar-floating-guide-day' + roundNumber);
            if (roundNumber === 2) {
                director.setCompactToolWheelIndexForGuide(0, 'avatar-floating-guide-day2-entry-reset');
            }
            if (isDay1Round) {
                director.overlay.clearPersistentSpotlight();
                director.overlay.clearActionSpotlight();
            } else if (roundNumber === 2) {
                if (director.isHomeChatExternalized()) {
                    if (
                        director.interactionTakeover
                        && typeof director.interactionTakeover.setExternalizedChatSpotlight === 'function'
                    ) {
                        director.interactionTakeover.setExternalizedChatSpotlight('');
                    }
                } else {
                    director.overlay.clearPersistentSpotlight();
                }
            } else {
                director.highlightChatWindow();
            }
            let day1LookAtHandle = null;
            let day1LookAtPromise = null;
            const startDay1LookAt = () => {
                if (!isDay1Round || day1LookAtPromise || day1LookAtHandle) {
                    return;
                }
                day1LookAtPromise = director.ensurePersistentGhostCursorLookAtPerformance({
                    isCancelled: () => director.isStopping()
                }).then((handle) => {
                    day1LookAtHandle = handle || null;
                    return day1LookAtHandle;
                }).catch((error) => {
                    console.warn('[YuiGuide] Avatar floating cursor look-at startup failed:', error);
                    return null;
                });
            };
            const stopDay1LookAt = async () => {
                if (!day1LookAtPromise && !day1LookAtHandle) {
                    return;
                }
                if (day1LookAtPromise && !day1LookAtHandle) {
                    day1LookAtHandle = await day1LookAtPromise;
                }
                if (day1LookAtHandle) {
                    await director.stopIntroVoiceCursorLookAtPerformance(day1LookAtHandle, roundId + '_complete');
                } else {
                    await director.stopPersistentGhostCursorLookAtPerformance(roundId + '_complete');
                }
            };
            const playScenes = async () => {
                try {
                    for (let index = 0; index < config.scenes.length; index += 1) {
                        if (director.isStopping()) {
                            return false;
                        }
                        if (isDay1Round && config.scenes[index].id === 'day1_capsule_drag_hint') {
                            startDay1LookAt();
                        }
                        const keepGoing = await director.playAvatarFloatingScene(
                            config.scenes[index],
                            roundNumber,
                            index,
                            config.scenes.length,
                            options || {}
                        );
                        if (!keepGoing) {
                            return false;
                        }
                    }
                    director.recordExperienceMetric('avatar_floating_round_complete', {
                        round: roundNumber,
                        durationMs: Math.max(0, Date.now() - startedAt)
                    });
                    if (
                        isDay1Round
                        && typeof director.recordAvatarFloatingGuideRoundEnd === 'function'
                    ) {
                        director.recordAvatarFloatingGuideRoundEnd(roundNumber);
                    }
                    return !director.isStopping();
                } finally {
                    if (
                        director.angryExitTriggered
                        && typeof director.waitForAngryExitPresentationCompletion === 'function'
                    ) {
                        await director.waitForAngryExitPresentationCompletion();
                    }
                    await stopDay1LookAt();
                    director.disableInterrupts();
                    director.clearAvatarStandIn({ clearPending: true, restoreModel: true });
                    director.setGuideChatInputLocked(false, 'avatar-floating-guide-day' + roundNumber + '-complete');
                    await director.closeAvatarFloatingGuidePanels({
                        clearCursor: true
                    });
                    director.clearAllVirtualSpotlights();
                    director.clearAllExtraSpotlights();
                    director.clearSpotlightGeometryHints();
                    director.clearSpotlightVariantHints();
                    if (typeof director.setDay4LockSpotlightSafeAreaActive === 'function') {
                        director.setDay4LockSpotlightSafeAreaActive(false, 'round-complete');
                    }
                    director.overlay.clearPersistentSpotlight();
                    director.overlay.clearActionSpotlight();
                    director.cursor.hide();
                    if (typeof director.setAvatarFloatingToolbarVisible === 'function') {
                        director.setAvatarFloatingToolbarVisible(true, 'round-complete');
                    }
                    if (!director.destroyed) {
                        director.setTutorialTakingOver(false);
                    }
                    director.currentSceneId = null;
                    director.currentStep = null;
                }
            };
            if (isDay1Round) {
                return await playScenes();
            }
            return await director.withLookAt({
                isCancelled: () => director.isStopping(),
                completeReason: roundId + '_complete',
                startFailureMessage: '[YuiGuide] Avatar floating cursor look-at startup failed:'
            }, playScenes);
        }
    }

    return {
        SceneOrchestrator,
        createSceneOrchestrator(director) {
            return new SceneOrchestrator(director);
        }
    };
});
