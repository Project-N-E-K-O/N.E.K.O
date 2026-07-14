(function (namespace) {
    'use strict';

    const {
        YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY,
        TutorialVisualControllers,
        ResistanceController,
        SidebarPauseController,
        PauseCoordinator,
        TutorialTerminationRouter,
        OperationRegistry,
        TutorialSceneOrchestrator,
        TutorialSettingsTourFlow,
        createYuiGuideChatBridgeCommandBus,
        createYuiGuideTargetGeometryRegistry,
        createYuiGuideChatWindowAdapter,
        createYuiGuideScopedTutorialResources,
        translateGuideText,
        resolveGuideLocale,
        resolveGuideAudioLocale,
        YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY,
        hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart,
        INTRO_ACTIVATION_AUTO_ADVANCE_MS,
        INTRO_ACTIVATION_REDUCED_MOTION_AUTO_ADVANCE_MS,
        DEFAULT_SPOTLIGHT_PADDING,
        PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
        PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y,
        NARRATION_RESUME_BACKTRACK_MS,
        NARRATION_RESUME_MIN_REMAINING_MS,
        PLUGIN_DASHBOARD_WINDOW_NAME,
        DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT,
        DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME,
        RETURN_PETAL_SEQUENCE_URL,
        wait,
        clamp,
        DAY4_LOCK_SPOTLIGHT_SAFE_BOTTOM_PX,
        createHomeTutorialPlatformCapabilities,
        createHomeTutorialExperienceMetrics,
        getGuideAudioCueConfig,
        getGuideAudioDurationConfig,
        YuiGuideVoiceQueue,
        YuiGuideEmotionBridge,
        CursorAnchorStore
    } = namespace;

    class YuiGuideDirector {
        constructor(options) {
            this.options = options || {};
            this.tutorialManager = this.options.tutorialManager || null;
            this.page = this.options.page || 'home';
            this.registry = this.options.registry || null;
            this.overlay = new window.YuiGuideOverlay(document);
            this.voiceQueue = new YuiGuideVoiceQueue();
            this.emotionBridge = new YuiGuideEmotionBridge();
            this.currentSceneId = null;
            this.currentStep = null;
            this.currentContext = null;
            this.sceneRunId = 0;
            this.sceneTimers = new Set();
            this.guideChatStreamTimers = new Set();
            this.sceneResources = createYuiGuideScopedTutorialResources();
            this.guideChatStreamResources = createYuiGuideScopedTutorialResources();
            this.interruptsEnabled = false;
            this.interruptCount = 0;
            this.interruptQualifyingMoveStreak = 0;
            this.lastInterruptAt = 0;
            this.lastPointerPoint = null;
            this.angryExitTriggered = false;
            this.destroyed = false;
            this.lastTutorialEndReason = null;
            this.introFlowStarted = false;
            this.introFlowCompleted = false;
            this.introGreetingChatHighlightCleared = false;
            this.awaitingIntroActivation = false;
            this._introActivationResolve = null;
            this.terminationRequested = false;
            this.angryExitPresentationPromise = null;
            this.activeNarration = null;
            this.narrationResumeTimer = null;
            this.scenePausedForResistance = false;
            this.scenePausedAt = 0;
            this.scenePauseResolvers = [];
            if (typeof TutorialVisualControllers.createHighlightController !== 'function') {
                throw new Error('TutorialVisualControllers.createHighlightController must be loaded before YuiGuideDirector');
            }
            if (typeof TutorialSettingsTourFlow.SettingsTourFlow !== 'function') {
                throw new Error('TutorialSettingsTourFlow.SettingsTourFlow must be loaded before YuiGuideDirector');
            }
            this.targetGeometryRegistry = createYuiGuideTargetGeometryRegistry();
            this.cursor = new TutorialVisualControllers.GhostCursorController(new TutorialVisualControllers.YuiGuideGhostCursor(this.overlay), {
                registry: this.targetGeometryRegistry
            });
            this.spotlightController = new TutorialVisualControllers.SpotlightController(TutorialVisualControllers.createHighlightController({
                document: document,
                window: window,
                overlay: this.overlay,
                defaultPadding: DEFAULT_SPOTLIGHT_PADDING,
                resolveElement: (selector) => this.resolveElement(selector)
            }), {
                registry: this.targetGeometryRegistry
            });
            this.pauseCoordinator = new PauseCoordinator({
                cursor: this.cursor,
                spotlightController: this.spotlightController,
                getResistancePaused: () => this.scenePausedForResistance,
                setResistancePaused: (active) => {
                    this.scenePausedForResistance = active === true;
                },
                setPausedAt: (pausedAt) => {
                    this.scenePausedAt = Number.isFinite(pausedAt) ? pausedAt : 0;
                },
                beginInterruptPresentation: () => this.beginGuideInterruptPresentation(),
                endInterruptPresentation: () => this.endGuideInterruptPresentation(),
                takeScenePauseResolvers: () => {
                    const resolvers = this.scenePauseResolvers.slice();
                    this.scenePauseResolvers = [];
                    return resolvers;
                }
            });
            this.sidebarPauseController = new SidebarPauseController({
                document: document
            });
            this.pauseCoordinator.registerPauseToken('sidebar', this.sidebarPauseController.getPauseToken());
            this.resistanceController = new ResistanceController(this);
            this.activeGuideEmotion = '';
            this.guideInterruptPresentationActive = false;
            this.pluginDashboardHandoff = null;
            this.pluginDashboardLastInterruptRequestId = '';
            this.pluginDashboardWindowCreatedByGuide = false;
            this.manualPluginDashboardOpenAllowed = false;
            this.manualPluginDashboardOpenTarget = null;
            this.manualPluginDashboardOpenUserClicked = false;
            this.customSecondarySpotlightTarget = null;
            this.persistentGhostCursorLookAtHandle = null;
            this.preTakeoverGhostCursorLookAtHandle = null;
            this.guideIdleSwayHandle = null;
            this.takeoverTopPeekHandle = null;
            this.takeoverOriginalAgentSwitches = null;
            this.takeoverAgentSwitchRestorePromise = null;
            this.returnPetalTransitionActive = false;
            this.avatarFloatingGuideSuppressionActive = false;
            this.avatarFloatingGuideTutorialModeActive = false;
            this.avatarFloatingGuidePreviousIsInTutorial = false;
            this.day4LockSpotlightSafeAreaActive = false;
            this.avatarStandInShowTimer = null;
            this.avatarStandInHideTimer = null;
            this.avatarStandInPerformanceHandle = null;
            this.avatarStandInActive = false;
            this.avatarStandInToken = 0;
            this.avatarStandInController = new TutorialVisualControllers.AvatarStandInController(this);
            this.petalTransitionController = new TutorialVisualControllers.PetalTransitionController(this);
            this.cursorAnchorStore = new CursorAnchorStore();
            this.operationRegistry = new OperationRegistry(this, {
                registry: this.targetGeometryRegistry,
                pluginDashboardWindowName: PLUGIN_DASHBOARD_WINDOW_NAME,
                resolveGuideLocale: resolveGuideLocale
            });
            this.settingsTourFlow = new TutorialSettingsTourFlow.SettingsTourFlow(this);
            this.sceneOrchestrator = new TutorialSceneOrchestrator.SceneOrchestrator(this);
            this.terminationRouter = new TutorialTerminationRouter(this);
            this.latestExternalizedChatCursorMoveSceneId = '';
            this.latestExternalizedChatCursorMovePromise = null;
            this.latestGuideChatMessageRetainId = '';
            this.latestGuideChatMessageRetainUntilMs = 0;
            this.latestGuideChatMessageRetainTimer = null;
            this.keydownHandler = this.onKeyDown.bind(this);
            this.pointerMoveHandler = this.onPointerMove.bind(this);
            this.pointerDownHandler = this.onPointerDown.bind(this);
            this.resistanceCursorTimer = null;
            this.userCursorRevealMoveCount = 0;
            this.userCursorRevealSuppressed = false;
            this.lastUserCursorRevealMoveAt = 0;
            this.pageHideHandler = this.onPageHide.bind(this);
            this.tutorialEndHandler = this.onTutorialEndEvent.bind(this);
            this.externalChatReadyHandler = this.onExternalChatReady.bind(this);
            this.externalChatCursorAnchorHandler = this.onExternalChatCursorAnchor.bind(this);
            this.remoteTerminationRequestHandler = this.onRemoteTerminationRequest.bind(this);
            this.desktopPluginDashboardSkipHandler = this.handleDesktopYuiGuideSkipRequest.bind(this);
            this.desktopPluginDashboardInterruptHandler = this.onDesktopPluginDashboardInterruptRequest.bind(this);
            this.messageHandler = this.onWindowMessage.bind(this);
            this.guideMessageActionHandler = this.handleGuideMessageAction.bind(this);
            this.guideMessageActionHandlerInstalled = false;
            this.pendingGuideMessageAction = null;
            this.chatBridgeCommandBus = createYuiGuideChatBridgeCommandBus({
                channelProvider: () => {
                    return window.appInterpage && window.appInterpage.nekoBroadcastChannel
                        ? window.appInterpage.nekoBroadcastChannel
                        : null;
                },
                nativeRelayProvider: () => window.nekoTutorialOverlay || null
            });
            const capabilityApi = window.homeTutorialPlatformCapabilities;
            this.platformCapabilities = capabilityApi && typeof capabilityApi.create === 'function'
                ? capabilityApi.create()
                : createHomeTutorialPlatformCapabilities();
            this.experienceMetrics = window.homeTutorialExperienceMetrics || createHomeTutorialExperienceMetrics();
            this.wakeup = window.YuiGuideWakeup && typeof window.YuiGuideWakeup.create === 'function'
                ? window.YuiGuideWakeup.create({
                    metrics: this.experienceMetrics
                })
                : null;
            this.interactionTakeover = window.TutorialInteractionTakeover
                && typeof window.TutorialInteractionTakeover.createController === 'function'
                ? window.TutorialInteractionTakeover.createController({
                    page: this.page,
                    overlay: this.overlay,
                    isDestroyed: () => this.destroyed,
                    isResistancePaused: () => this.scenePausedForResistance === true,
                    externalizedChatDetector: () => this.isHomeChatExternalized(),
                    externalChatChannelProvider: () => {
                        return window.appInterpage && window.appInterpage.nekoBroadcastChannel
                            ? window.appInterpage.nekoBroadcastChannel
                            : null;
                    }
                })
                : null;
            this.chatWindowAdapter = createYuiGuideChatWindowAdapter({
                mode: this.isHomeChatExternalized() ? 'externalized' : 'local',
                registry: this.targetGeometryRegistry,
                interactionTakeover: this.interactionTakeover,
                beforeExternalizedSpotlight: () => this.clearHomeSpotlightsForExternalizedChat(),
                resolveLocalTarget: (targetKey) => this.resolveAvatarFloatingSelector(targetKey)
            });
            if (this.interactionTakeover && typeof this.interactionTakeover.enableFaceForwardLock === 'function') {
                this.interactionTakeover.enableFaceForwardLock();
            }

            if (this.page === 'home') {
                document.body.classList.add('yui-guide-home-ui-suppressed');
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatButtonsDisabled === 'function') {
                    this.interactionTakeover.setExternalizedChatButtonsDisabled(true);
                }
            }

            window.addEventListener('keydown', this.keydownHandler, true);
            window.addEventListener('pagehide', this.pageHideHandler, true);
            window.addEventListener('neko:yui-guide:external-chat-ready', this.externalChatReadyHandler, true);
            window.addEventListener('neko:yui-guide:external-chat-cursor-anchor', this.externalChatCursorAnchorHandler, true);
            window.addEventListener('neko:yui-guide:remote-termination-request', this.remoteTerminationRequestHandler, true);
            window.addEventListener(DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT, this.desktopPluginDashboardSkipHandler, true);
            window.addEventListener('neko:yui-guide:desktop-interrupt-request', this.desktopPluginDashboardInterruptHandler, true);
            window.addEventListener('neko:yui-guide:tutorial-end', this.tutorialEndHandler, true);
            window.addEventListener('message', this.messageHandler, true);
        }

        isStopping() {
            return !!(this.destroyed || this.angryExitTriggered || this.terminationRequested);
        }

        isGuardFailed(runId) {
            const hasRunId = runId !== undefined && runId !== null;
            return !!((hasRunId && runId !== this.sceneRunId) || this.isStopping());
        }

        prepareNarration(scene) {
            const text = this.resolveAvatarFloatingSceneText(scene);
            const voiceKey = scene.voiceKey || '';
            const sceneButtons = this.getAvatarFloatingSceneButtons(scene);
            const canHandleSceneButtons = sceneButtons.length > 0
                ? this.installGuideMessageActionHandler()
                : false;
            const actionWaitPromise = canHandleSceneButtons
                ? this.beginGuideMessageActionWait(sceneButtons, 0)
                : null;
            if (text) {
                this.appendGuideChatMessage(text, {
                    textKey: scene.textKey || '',
                    voiceKey: voiceKey,
                    buttons: sceneButtons
                });
            }
            const sceneEmotion = this.resolveAvatarFloatingSceneEmotion(scene);
            if (sceneEmotion) {
                this.applyGuideEmotion(sceneEmotion);
            }
            return {
                text,
                voiceKey,
                sceneButtons,
                canHandleSceneButtons,
                actionWaitPromise
            };
        }

        createSceneScaler(voiceKey) {
            const timingScale = this.getGuideVoiceTimingScale(voiceKey);
            return (value, minValue, maxValue) => {
                const baseValue = Number.isFinite(value) ? value : 0;
                const scaledValue = Math.round(baseValue * timingScale);
                return clamp(
                    scaledValue,
                    Number.isFinite(minValue) ? minValue : 40,
                    Number.isFinite(maxValue) ? maxValue : Math.max(
                        Number.isFinite(minValue) ? minValue : 40,
                        scaledValue
                    )
                );
            };
        }

        createNarrationPromise(scene, text, voiceKey, options) {
            const normalizedOptions = options || {};
            if (!text && !voiceKey) {
                return Promise.resolve();
            }
            return this.speakGuideLine(text, {
                voiceKey: voiceKey,
                minDurationMs: Number.isFinite(normalizedOptions.minDurationMs)
                    ? normalizedOptions.minDurationMs
                    : 1800
            }).catch((error) => {
                console.warn('[YuiGuide] 悬浮窗教程旁白失败，继续流程:', scene && scene.id, error);
            });
        }

        async finalizeScene(runId, options) {
            const normalizedOptions = options || {};
            if (normalizedOptions.canHandleSceneButtons && this.pendingGuideMessageAction) {
                this.armPendingGuideMessageActionTimeout(12000);
            }
            if (
                normalizedOptions.actionWaitPromise
                && !this.isGuardFailed(runId)
            ) {
                await normalizedOptions.actionWaitPromise;
            }
            if (this.isGuardFailed(runId)) {
                return false;
            }
            const index = Number.isFinite(normalizedOptions.index)
                ? normalizedOptions.index
                : 0;
            const total = Number.isFinite(normalizedOptions.total)
                ? normalizedOptions.total
                : 0;
            await this.waitForSceneDelay(index >= total - 1 ? 260 : 420);
            return !this.isGuardFailed(runId);
        }

        performFullCleanup(options) {
            const normalizedOptions = options || {};
            this.setHomePcCursorOutputSuppressedForExternalizedChat(false);
            this.overlay.hidePluginPreview();
            this.overlay.hideBubble();
            this.overlay.setAngry(false);
            this.setTutorialTakingOver(false);
            if (
                normalizedOptions.destroyInteractionTakeover
                && this.interactionTakeover
                && typeof this.interactionTakeover.destroy === 'function'
            ) {
                this.interactionTakeover.destroy();
            }
            if (normalizedOptions.destroyOverlay) {
                this.overlay.destroy();
            }
        }

        async withLookAt(options, run) {
            const normalizedOptions = options || {};
            const completeReason = normalizedOptions.completeReason || 'look_at_complete';
            const isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : () => this.isStopping();
            let lookAtHandle = null;
            const lookAtPromise = this.ensurePersistentGhostCursorLookAtPerformance({
                isCancelled
            }).then((handle) => {
                lookAtHandle = handle || null;
                return lookAtHandle;
            }).catch((error) => {
                console.warn(
                    normalizedOptions.startFailureMessage || '[YuiGuide] Cursor look-at startup failed:',
                    error
                );
                return null;
            });

            try {
                return await run();
            } finally {
                if (lookAtPromise && !lookAtHandle) {
                    lookAtHandle = await lookAtPromise;
                }
                if (lookAtHandle) {
                    await this.stopIntroVoiceCursorLookAtPerformance(lookAtHandle, completeReason);
                } else {
                    await this.stopPersistentGhostCursorLookAtPerformance(completeReason);
                }
            }
        }

        setTutorialTakingOver(active, options) {
            const isActive = active === true;
            const shouldSyncCursor = !(options && options.syncSystemCursor === false);
            if (isActive && shouldSyncCursor) {
                this.syncSystemCursorHidden(true, 'taking_over_started');
            }
            this.setAvatarFloatingGuideTutorialMode(isActive);
            const featureController = window.NekoHomeTutorialFeatureController;
            if (
                featureController
                && typeof featureController.begin === 'function'
                && typeof featureController.end === 'function'
            ) {
                if (isActive && !this.avatarFloatingGuideSuppressionActive) {
                    featureController.begin('avatar-floating-guide');
                    this.avatarFloatingGuideSuppressionActive = true;
                } else if (!isActive && this.avatarFloatingGuideSuppressionActive) {
                    featureController.end('avatar-floating-guide');
                    this.avatarFloatingGuideSuppressionActive = false;
                }
            }
            try {
                window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
                    detail: {
                        active: isActive,
                        source: 'yui-guide-director',
                        sceneId: this.currentSceneId || ''
                    }
                }));
            } catch (error) {
                console.warn('[YuiGuide] 同步教程期功能暂停状态失败:', error);
            }
            if (this.interactionTakeover && typeof this.interactionTakeover.setActive === 'function') {
                this.interactionTakeover.setActive(isActive);
                return;
            }
            this.overlay.setTakingOver(isActive);
        }

        getAvatarStandInCue(day, sceneId) {
            return this.avatarStandInController.getCue(day, sceneId);
        }

        scheduleAvatarStandInForScene(scene, day, sceneRunId) {
            return this.avatarStandInController.schedule(scene, day, sceneRunId);
        }

        showAvatarStandIn(cue, token) {
            if (!cue || token !== this.avatarStandInToken || this.isStopping() || this.destroyed) {
                return;
            }
            this.clearAvatarStandIn({ clearPending: false, restoreModel: true, preserveToken: true });
            this.avatarStandInActive = true;
            Promise.resolve(this.startAvatarCornerPeekPerformance({
                position: cue.position,
                isCancelled: () => token !== this.avatarStandInToken
                    || this.isStopping()
                    || this.destroyed
            })).then((handle) => {
                if (
                    token !== this.avatarStandInToken
                    || this.isStopping()
                    || this.destroyed
                ) {
                    this.stopAvatarCornerPeekPerformance(handle, 'avatar_standin_cancelled').catch(() => {});
                    return;
                }
                if (!handle) {
                    this.avatarStandInActive = false;
                    return;
                }
                this.avatarStandInPerformanceHandle = handle;
                const rawDurationMs = Number.isFinite(Number(cue.duration))
                    ? Number(cue.duration)
                    : Number(cue.durationMs);
                const durationMs = Math.max(0, Number.isFinite(rawDurationMs) ? rawDurationMs : 0);
                this.avatarStandInHideTimer = window.setTimeout(() => {
                    if (token === this.avatarStandInToken) {
                        this.clearAvatarStandIn({ clearPending: false, restoreModel: true });
                    }
                }, durationMs);
            }).catch((error) => {
                console.warn('[YuiGuide] Live2D 探身动作启动失败:', error);
                this.avatarStandInActive = false;
            });
        }

        clearAvatarStandIn(options) {
            return this.avatarStandInController.clear(options);
        }

        setGuideChatInputLocked(locked, reason) {
            const isLocked = locked === true;
            const lockReason = typeof reason === 'string' && reason
                ? reason
                : 'avatar-floating-guide';
            if (this.chatWindowAdapter && typeof this.chatWindowAdapter.lockInput === 'function') {
                try {
                    this.chatWindowAdapter.lockInput(isLocked, lockReason);
                } catch (error) {
                    console.warn('[YuiGuide] 同步聊天输入锁定状态失败:', error);
                }
            }
            try {
                window.dispatchEvent(new CustomEvent('neko:yui-guide:chat-input-lock-change', {
                    detail: {
                        locked: isLocked,
                        reason: lockReason,
                        timestamp: Date.now()
                    }
                }));
            } catch (_) {}
        }

        setAvatarFloatingGuideTutorialMode(active) {
            const isActive = active === true;
            try {
                if (this.overlay && typeof this.overlay.setTutorialInputShieldActive === 'function') {
                    this.overlay.setTutorialInputShieldActive(isActive);
                }
                if (isActive) {
                    if (!this.avatarFloatingGuideTutorialModeActive) {
                        this.avatarFloatingGuidePreviousIsInTutorial = window.isInTutorial === true;
                        this.avatarFloatingGuideTutorialModeActive = true;
                    }
                    window.isInTutorial = true;
                    return;
                }
                if (this.avatarFloatingGuideTutorialModeActive) {
                    window.isInTutorial = this.avatarFloatingGuidePreviousIsInTutorial === true;
                    this.avatarFloatingGuideTutorialModeActive = false;
                    this.avatarFloatingGuidePreviousIsInTutorial = false;
                }
            } catch (error) {
                console.warn('[YuiGuide] 同步全局教程状态失败:', error);
            }
        }

        enforceAvatarFloatingGuideFeatureSuppression(reason) {
            const featureController = window.NekoHomeTutorialFeatureController;
            if (featureController && typeof featureController.enforce === 'function') {
                try {
                    featureController.enforce(reason || 'avatar-floating-guide');
                    return;
                } catch (error) {
                    console.warn('[YuiGuide] failed to enforce tutorial feature suppression:', error);
                }
            }

            const reactChatHost = window.reactChatWindowHost;
            if (reactChatHost && typeof reactChatHost.setGalgameModeEnabled === 'function') {
                try {
                    reactChatHost.setGalgameModeEnabled(false, {
                        persist: false,
                        suppressRefetch: true
                    });
                } catch (error) {
                    console.warn('[YuiGuide] failed to force-disable GalGame during guide:', error);
                }
            }
            const proactiveKeys = [
                'proactiveChatEnabled',
                'proactiveVisionEnabled',
                'proactiveVisionChatEnabled',
                'proactiveNewsChatEnabled',
                'proactiveVideoChatEnabled',
                'proactiveXhhChatEnabled',
                'proactivePersonalChatEnabled',
                'proactiveMusicEnabled',
                'proactiveMemeEnabled',
                'proactiveMiniGameInviteEnabled'
            ];
            const appState = window.appState || null;
            proactiveKeys.forEach((key) => {
                window[key] = false;
                if (appState && typeof appState[key] !== 'undefined') {
                    appState[key] = false;
                }
            });
            [
                'stopProactiveChatSchedule',
                'stopProactiveVisionDuringSpeech',
                'releaseProactiveVisionStream'
            ].forEach((methodName) => {
                if (typeof window[methodName] === 'function') {
                    try {
                        window[methodName]();
                    } catch (error) {
                        console.warn('[YuiGuide] failed to stop proactive feature during guide:', methodName, error);
                    }
                }
            });
            try {
                window.dispatchEvent(new CustomEvent('neko:home-tutorial-features-suppressed', {
                    detail: {
                        active: true,
                        enforced: true,
                        source: 'yui-guide-director',
                        reason: reason || 'avatar-floating-guide',
                        sceneId: this.currentSceneId || ''
                    }
                }));
            } catch (error) {
                console.warn('[YuiGuide] failed to broadcast enforced tutorial feature suppression:', error);
            }
        }

        async ensureAvatarFloatingGuideSurfaceReady(round) {
            try {
                await this.ensureChatVisible();
            } catch (error) {
                console.warn('[YuiGuide] failed to ensure chat window before avatar floating guide:', error);
            }
            this.enforceAvatarFloatingGuideFeatureSuppression(
                'avatar-floating-day' + Number(round) + '-surface-ready'
            );
        }

        isIntroActivationTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            return !!(
                target.closest('#react-chat-window-root .composer-input')
                || target.closest('#react-chat-window-root .composer-input-shell')
                || target.closest('#react-chat-window-root .composer-panel')
                || target.closest('#text-input-area')
                || target.closest('#textInputBox')
            );
        }

        isGuideMessageActionTarget(target) {
            if (!target || typeof target.closest !== 'function') {
                return false;
            }

            return !!target.closest('[data-guide-message="true"] .message-action-button');
        }

        waitForIntroActivationTransition() {
            this.awaitingIntroActivation = false;
            this._introActivationResolve = null;
            const waitMs = this.shouldReduceTutorialMotion()
                ? INTRO_ACTIVATION_REDUCED_MOTION_AUTO_ADVANCE_MS
                : INTRO_ACTIVATION_AUTO_ADVANCE_MS;
            return wait(waitMs);
        }

        shouldReduceTutorialMotion() {
            try {
                return !!(
                    window.matchMedia
                    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
                );
            } catch (_) {
                return false;
            }
        }

        getStep(stepId) {
            if (!stepId) {
                return null;
            }

            if (this.registry && typeof this.registry.getStep === 'function') {
                return this.registry.getStep(stepId) || null;
            }

            return null;
        }

        getHomePresentationSceneOrder() {
            if (!this.registry || !this.registry.sceneOrder || !Array.isArray(this.registry.sceneOrder.home)) {
                return [];
            }

            return this.registry.sceneOrder.home.filter(function (sceneId) {
                return (
                    typeof sceneId === 'string'
                    && sceneId.indexOf('interrupt_') !== 0
                    && sceneId.indexOf('handoff_') !== 0
                );
            });
        }

        getBubbleMetaForScene(sceneId) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            if (this.page !== 'home') {
                return '';
            }

            if (normalizedSceneId === 'intro_activation') {
                return this.resolveGuideCopy('tutorial.yuiGuide.bubbleMeta.ready', '准备开始');
            }

            const order = this.getHomePresentationSceneOrder();
            const index = order.indexOf(normalizedSceneId);
            if (index === -1 || order.length <= 0) {
                return '';
            }

            const current = index + 1;
            const total = order.length;
            const progressFallback = '主页引导 ' + current + '/' + total;
            return this.resolveGuideCopy('tutorial.yuiGuide.bubbleMeta.homeProgress', progressFallback, {
                current: current,
                total: total
            });
        }

        showGuideBubble(text, options, sceneId) {
            const normalizedOptions = Object.assign({}, options || {});
            const bubbleVariant = typeof normalizedOptions.bubbleVariant === 'string'
                ? normalizedOptions.bubbleVariant.trim()
                : '';
            const hidesMeta = bubbleVariant === 'intro-activation' || bubbleVariant === 'plugin-manual-open';
            if (hidesMeta) {
                normalizedOptions.meta = '';
            } else if (!normalizedOptions.meta) {
                normalizedOptions.meta = this.getBubbleMetaForScene(sceneId || this.currentSceneId);
            }
            this.overlay.showBubble(text, normalizedOptions);
        }

        recordExperienceMetric(type, detail) {
            if (!this.experienceMetrics || typeof this.experienceMetrics.record !== 'function') {
                return null;
            }

            const payload = Object.assign({
                page: this.page || '',
                sceneId: this.currentSceneId || ''
            }, detail && typeof detail === 'object' ? detail : {});

            try {
                return this.experienceMetrics.record(type, payload);
            } catch (_) {
                return null;
            }
        }

        resolveModelPrefix() {
            if (this.tutorialManager && this.tutorialManager._tutorialModelPrefix) {
                return this.tutorialManager._tutorialModelPrefix;
            }

            if (this.tutorialManager && this.tutorialManager.constructor && typeof this.tutorialManager.constructor.detectModelPrefix === 'function') {
                return this.tutorialManager.constructor.detectModelPrefix();
            }

            if (window.universalTutorialManager &&
                window.universalTutorialManager.constructor &&
                typeof window.universalTutorialManager.constructor.detectModelPrefix === 'function') {
                return window.universalTutorialManager.constructor.detectModelPrefix();
            }

            return 'live2d';
        }

        getAvatarFloatingActiveModelType() {
            const normalize = (value) => {
                const modelType = String(value || '').trim().toLowerCase();
                return modelType === 'live2d' || modelType === 'vrm' || modelType === 'mmd' || modelType === 'pngtuber'
                    ? modelType
                    : '';
            };
            const runtimeType = normalize(
                typeof window.getActiveModelType === 'function' ? window.getActiveModelType() : ''
            );
            if (runtimeType) {
                return runtimeType;
            }

            const cfg = window.lanlan_config;
            if (cfg) {
                const modelType = String(cfg.model_type || '').toLowerCase();
                if (modelType === 'live3d') {
                    const subType = normalize(cfg.live3d_sub_type);
                    return subType || 'vrm';
                }
                return normalize(modelType) || 'live2d';
            }
            return '';
        }

        expandSelector(selector) {
            if (typeof selector !== 'string' || !selector.trim()) {
                return '';
            }

            return selector.replace(/\$\{p\}/g, this.resolveModelPrefix());
        }

        resolveElement(selector) {
            const expanded = this.expandSelector(selector);
            if (!expanded) {
                return null;
            }

            try {
                return document.querySelector(expanded);
            } catch (error) {
                console.warn('[YuiGuide] 查询元素失败:', expanded, error);
                return null;
            }
        }

        queryDocumentSelector(selector) {
            const normalizedSelector = typeof selector === 'string' ? selector.trim() : '';
            if (!normalizedSelector) {
                return null;
            }

            try {
                return document.querySelector(normalizedSelector);
            } catch (error) {
                console.warn('[YuiGuide] document.querySelector 查询失败:', normalizedSelector, error);
                return null;
            }
        }

        resolveRect(selector) {
            if (selector === 'body') {
                return {
                    left: 0,
                    top: 0,
                    right: window.innerWidth,
                    bottom: window.innerHeight,
                    width: window.innerWidth,
                    height: window.innerHeight
                };
            }

            const element = this.resolveElement(selector);
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            return element.getBoundingClientRect();
        }

        getDefaultCursorOrigin() {
            const chatInputTarget = this.getChatInputTarget();
            const chatInputRect = chatInputTarget && typeof chatInputTarget.getBoundingClientRect === 'function'
                ? chatInputTarget.getBoundingClientRect()
                : null;
            if (chatInputRect && chatInputRect.width > 0 && chatInputRect.height > 0) {
                return {
                    x: chatInputRect.left + (chatInputRect.width / 2),
                    y: chatInputRect.top + (chatInputRect.height / 2)
                };
            }

            const prefix = this.resolveModelPrefix();
            const modelRect = this.resolveRect('#' + prefix + '-container');
            if (modelRect) {
                return {
                    x: modelRect.left + (modelRect.width / 2),
                    y: modelRect.top + Math.min(modelRect.height * 0.55, modelRect.height - 16)
                };
            }

            return {
                x: Math.max(120, window.innerWidth * 0.72),
                y: Math.max(120, window.innerHeight * 0.45)
            };
        }

        getViewportCenter() {
            return {
                x: window.innerWidth / 2,
                y: window.innerHeight / 2
            };
        }

        getReturnPetalTransitionOrigin() {
            const prefix = this.resolveModelPrefix();
            const manager = prefix === 'live2d'
                ? window.live2dManager
                : (prefix === 'vrm' ? window.vrmManager : window.mmdManager);
            try {
                if (manager && typeof manager.getModelScreenBounds === 'function') {
                    const bounds = manager.getModelScreenBounds();
                    if (
                        bounds
                        && Number.isFinite(Number(bounds.centerX))
                        && Number.isFinite(Number(bounds.centerY))
                    ) {
                        return {
                            x: Number(bounds.centerX),
                            y: Number(bounds.centerY)
                        };
                    }
                }
            } catch (_) {}

            const modelRect = this.resolveRect('#' + prefix + '-container');
            if (modelRect) {
                return {
                    x: modelRect.left + modelRect.width / 2,
                    y: modelRect.top + modelRect.height / 2
                };
            }

            return this.getViewportCenter();
        }

        getReturnPetalTransitionModel() {
            return this.petalTransitionController.getReturnModel();
        }

        collectReturnPetalTransitionManagers() {
            return this.petalTransitionController.collectReturnManagers();
        }

        getReturnPetalTransitionOpacityElements() {
            return this.petalTransitionController.getReturnOpacityElements();
        }

        prepareReturnPetalTransitionOpacityTargets(model) {
            return this.petalTransitionController.prepareReturnOpacityTargets(model);
        }

        restoreReturnPetalTransitionOpacityTargets() {
            return this.petalTransitionController.restoreOpacityTargets();
        }

        getReturnPetalSequenceUrl() {
            return RETURN_PETAL_SEQUENCE_URL;
        }

        loadReturnPetalSequence() {
            return this.petalTransitionController.preloadReturnPetalSequence();
        }

        getReturnPetalTransitionRemainingMs(voiceKey, fallbackText) {
            const playbackSnapshot = this.voiceQueue && typeof this.voiceQueue.capturePlaybackSnapshot === 'function'
                ? this.voiceQueue.capturePlaybackSnapshot()
                : null;
            if (
                playbackSnapshot
                && playbackSnapshot.voiceKey === voiceKey
                && Number.isFinite(playbackSnapshot.durationMs)
                && playbackSnapshot.durationMs > 0
            ) {
                return Math.max(
                    0,
                    Math.round(playbackSnapshot.durationMs - Math.max(0, playbackSnapshot.currentTimeMs || 0))
                );
            }

            const fullDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                || 0;
            const cueMs = this.resolveGuideVoiceCueTargetMs(
                voiceKey,
                'returnPetalTransition',
                fullDurationMs,
                fallbackText || ''
            );
            return Math.max(0, Math.round(fullDurationMs - cueMs));
        }

        fadeReturnPetalTransitionModelOut(durationMs) {
            return this.petalTransitionController.fadeReturnModelOut(durationMs);
        }

        createReturnPetalTransition(origin, options) {
            return this.petalTransitionController.createReturnPetalTransition(origin, options);
        }

        async restoreTutorialAvatarForReturnPetalTransition() {
            return this.petalTransitionController.restoreTutorialAvatarForReturn();
        }

        async playReturnPetalTransition(options) {
            return this.petalTransitionController.playReturn(options);
        }

        resolveGuideCopy(textKey, fallbackText, interpolation) {
            return translateGuideText(textKey, fallbackText, interpolation);
        }

        resolveAvatarFloatingSceneText(scene) {
            if (scene && scene.id === 'day3_intro_context') {
                const voiceUsedAfterDay1End = hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart(3);
                return voiceUsedAfterDay1End
                    ? this.resolveGuideCopy('tutorial.avatarFloating.day3.introVoiceUsed', scene.text || '')
                    : this.resolveGuideCopy(scene.textKey || 'tutorial.avatarFloating.day3.intro', scene.text || '');
            }
            return this.resolveGuideCopy(scene.textKey || '', scene.text || '');
        }

        resolveAvatarFloatingSceneVoiceKey(scene) {
            if (
                scene
                && scene.id === 'day3_intro_context'
                && hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart(3)
            ) {
                return 'avatar_floating_day3_intro_voice_used';
            }
            return scene && typeof scene.voiceKey === 'string' ? scene.voiceKey : '';
        }

        resolveAvatarFloatingSceneEmotion(scene) {
            if (scene && scene.id === 'day3_intro_context') {
                return hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart(3) ? 'happy' : 'sad';
            }
            return scene && typeof scene.emotion === 'string' ? scene.emotion : '';
        }

        getAvatarFloatingSceneButtons(scene) {
            return [];
        }

        installGuideMessageActionHandler() {
            const host = window.reactChatWindowHost;
            if (!host || typeof host.setOnMessageAction !== 'function') {
                return false;
            }

            host.setOnMessageAction(this.guideMessageActionHandler);
            this.guideMessageActionHandlerInstalled = true;
            return true;
        }

        uninstallGuideMessageActionHandler() {
            if (!this.guideMessageActionHandlerInstalled) {
                return;
            }

            const host = window.reactChatWindowHost;
            if (host && typeof host.setOnMessageAction === 'function') {
                host.setOnMessageAction(null);
            }
            this.guideMessageActionHandlerInstalled = false;
        }

        beginGuideMessageActionWait(buttons, timeoutMs) {
            const guideButtons = Array.isArray(buttons) ? buttons : [];
            if (guideButtons.length === 0) {
                return null;
            }

            this.clearPendingGuideMessageAction();
            const normalizedTimeoutMs = Number.isFinite(timeoutMs)
                ? Math.max(0, Math.round(timeoutMs))
                : 12000;
            return new Promise((resolve) => {
                const actionNames = new Set(guideButtons.map((button) => String(button.action || button.id || '')));
                const pending = {
                    actionNames: actionNames,
                    resolve: resolve,
                    timeoutId: 0
                };
                this.pendingGuideMessageAction = pending;
                if (normalizedTimeoutMs > 0) {
                    this.armPendingGuideMessageActionTimeout(normalizedTimeoutMs);
                }
            });
        }

        armPendingGuideMessageActionTimeout(timeoutMs) {
            const pending = this.pendingGuideMessageAction;
            if (!pending || pending.timeoutId) {
                return false;
            }

            const delay = Number.isFinite(timeoutMs) ? Math.max(0, Math.round(timeoutMs)) : 12000;
            pending.timeoutId = window.setTimeout(() => {
                if (this.pendingGuideMessageAction !== pending) {
                    return;
                }
                this.pendingGuideMessageAction = null;
                pending.resolve({
                    action: 'avatar-floating-guide-timeout',
                    timedOut: true
                });
            }, delay);
            return true;
        }

        clearPendingGuideMessageAction() {
            const pending = this.pendingGuideMessageAction;
            if (!pending) {
                return;
            }

            if (pending.timeoutId) {
                window.clearTimeout(pending.timeoutId);
            }
            this.pendingGuideMessageAction = null;
            if (typeof pending.resolve === 'function') {
                pending.resolve({
                    action: 'avatar-floating-guide-cancelled'
                });
            }
        }

        resolveGuideMessageAction(action) {
            const pending = this.pendingGuideMessageAction;
            if (!pending || !action) {
                return false;
            }

            const actionName = String(action.action || action.id || '');
            if (!pending.actionNames || !pending.actionNames.has(actionName)) {
                return false;
            }

            if (pending.timeoutId) {
                window.clearTimeout(pending.timeoutId);
            }
            this.pendingGuideMessageAction = null;
            pending.resolve(action);
            return true;
        }

        disableGuideMessageButtons(message) {
            if (!message || !message.id || !Array.isArray(message.blocks)) {
                return;
            }

            const blocks = message.blocks.map((block) => {
                if (!block || block.type !== 'buttons' || !Array.isArray(block.buttons)) {
                    return block;
                }

                return Object.assign({}, block, {
                    buttons: block.buttons.map((button) => Object.assign({}, button, {
                        disabled: true
                    }))
                });
            });
            this.updateGuideChatMessage(message.id, {
                blocks: blocks
            });
        }

        handleGuideMessageAction(message, action) {
            if (!this.pendingGuideMessageAction) {
                return;
            }

            this.disableGuideMessageButtons(message);
            this.resolveGuideMessageAction(action);
        }

        applyGuideEmotion(emotion, options) {
            const normalizedEmotion = typeof emotion === 'string' ? emotion.trim() : '';
            if (!normalizedEmotion) {
                return;
            }

            const normalizedOptions = options || {};
            const allowDuringInterrupt = !!normalizedOptions.allowDuringInterrupt;

            if (this.guideInterruptPresentationActive && !allowDuringInterrupt) {
                return;
            }

            this.activeGuideEmotion = normalizedEmotion;
            this.emotionBridge.apply(normalizedEmotion);
        }

        clearGuidePresentation() {
            if (this.guideInterruptPresentationActive) {
                return;
            }
            this.activeGuideEmotion = '';
            this.emotionBridge.clear();
        }

        clearQueuedGuideChatBridgeMessages() {
            if (
                this.chatBridgeCommandBus
                && typeof this.chatBridgeCommandBus.clearQueue === 'function'
            ) {
                this.chatBridgeCommandBus.clearQueue();
                return;
            }
            try {
                if (window.localStorage) {
                    window.localStorage.removeItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
                }
            } catch (_) {}
        }

        beginGuideInterruptPresentation() {
            this.guideInterruptPresentationActive = true;
            this.voiceQueue.stopGuideMouthMotion();
            this.activeGuideEmotion = '';
            this.emotionBridge.clear();
        }

        endGuideInterruptPresentation() {
            this.guideInterruptPresentationActive = false;
        }

        captureCurrentGuidePresentationSnapshot() {
            const activeExpressionFile = this.emotionBridge && typeof this.emotionBridge.getActiveGuideExpressionFile === 'function'
                ? this.emotionBridge.getActiveGuideExpressionFile()
                : '';

            if (this.activeGuideEmotion || activeExpressionFile) {
                return {
                    emotion: this.activeGuideEmotion,
                    expressionFile: activeExpressionFile
                };
            }

            return null;
        }

        restoreGuidePresentationSnapshot(snapshot) {
            if (!snapshot) {
                return false;
            }

            let restored = false;
            if (snapshot.emotion) {
                this.applyGuideEmotion(snapshot.emotion);
                restored = true;
            }

            if (snapshot.expressionFile && this.emotionBridge && typeof this.emotionBridge.applyExpressionFile === 'function') {
                this.emotionBridge.applyExpressionFile(snapshot.expressionFile);
                restored = true;
            }

            if (restored) {
                return true;
            }

            this.clearGuidePresentation();
            return true;
        }

        async speakGuideLine(text, options) {
            const content = typeof text === 'string' ? text.trim() : '';

            if (!content) {
                return;
            }

            await this.speakLineAndWait(content, options || {});
        }

        resolvePerformanceBubbleText(performance) {
            const normalizedPerformance = performance || {};
            return this.resolveGuideCopy(
                normalizedPerformance.bubbleTextKey || '',
                normalizedPerformance.bubbleText || ''
            );
        }

        resolvePerformanceResistanceVoices(performance) {
            const normalizedPerformance = performance || {};
            const fallbacks = Array.isArray(normalizedPerformance.resistanceVoices)
                ? normalizedPerformance.resistanceVoices
                : [];
            const keys = Array.isArray(normalizedPerformance.resistanceVoiceKeys)
                ? normalizedPerformance.resistanceVoiceKeys
                : [];

            return fallbacks.map((fallbackText, index) => {
                return this.resolveGuideCopy(keys[index] || '', fallbackText);
            });
        }

        getElementRect(element) {
            return this.spotlightController.getElementRect(element);
        }

        createVirtualSpotlight(key, rect, options) {
            return this.spotlightController.createVirtualSpotlight(key, rect, options);
        }

        createPluginManagementEntrySpotlight(button) {
            const rect = this.getElementRect(button);
            if (!rect) {
                return button || null;
            }

            return this.createVirtualSpotlight('plugin-management-entry', {
                left: rect.left - PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
                top: rect.top - PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y,
                right: rect.right + PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
                bottom: rect.bottom + PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y
            }, {
                padding: 0,
                radius: 18
            }) || button;
        }

        createUnionSpotlight(key, elements, options) {
            return this.spotlightController.createUnionSpotlight(key, elements, options);
        }

        clearVirtualSpotlight(key) {
            this.spotlightController.clearVirtualSpotlight(key);
        }

        clearAllVirtualSpotlights() {
            this.spotlightController.clearAllVirtualSpotlights();
        }

        clearSpotlightVariantHints() {
            this.spotlightController.clearSpotlightVariantHints();
        }

        clearSpotlightGeometryHints() {
            this.spotlightController.clearSpotlightGeometryHints();
        }

        setSpotlightGeometryHint(element, options) {
            this.spotlightController.setSpotlightGeometryHint(element, options);
        }

        setSpotlightVariantHints(entries) {
            this.spotlightController.setSpotlightVariantHints(entries);
        }

        syncExtraSpotlights() {
            this.spotlightController.syncExtraSpotlights();
        }

        addRetainedExtraSpotlight(element) {
            this.spotlightController.addRetainedExtraSpotlight(element);
        }

        replaceRetainedExtraSpotlight(matcher, element) {
            this.spotlightController.replaceRetainedExtraSpotlight(matcher, element);
        }

        removeRetainedExtraSpotlight(matcher) {
            this.spotlightController.removeRetainedExtraSpotlight(matcher);
        }

        clearRetainedExtraSpotlights() {
            this.spotlightController.clearRetainedExtraSpotlights();
        }

        setSceneExtraSpotlights(elements) {
            this.spotlightController.setSceneExtraSpotlights(elements);
        }

        clearSceneExtraSpotlights() {
            this.spotlightController.clearSceneExtraSpotlights();
        }

        clearAllExtraSpotlights() {
            this.spotlightController.clearAllExtraSpotlights();
        }

        cleanupTutorialReturnButtons() {
            [
                '#live2d-btn-return',
                '#live2d-return-button-container',
                '#vrm-btn-return',
                '#vrm-return-button-container',
                '#mmd-btn-return',
                '#mmd-return-button-container'
            ].forEach((selector) => {
                document.querySelectorAll(selector).forEach((element) => {
                    if (element && typeof element.remove === 'function') {
                        element.remove();
                    }
                });
            });
        }

        getAgentToggleElement(toggleId) {
            if (!toggleId) {
                return null;
            }

            return this.resolveElement('#${p}-toggle-' + toggleId);
        }

        getAgentToggleCheckbox(toggleId) {
            if (!toggleId) {
                return null;
            }

            return this.resolveElement('#${p}-' + toggleId);
        }

        getAgentSidePanelButton(toggleId, actionId) {
            if (!toggleId || !actionId) {
                return null;
            }

            return document.getElementById('neko-sidepanel-action-' + toggleId + '-' + actionId);
        }

        getAgentSidePanel(toggleId) {
            if (!toggleId) {
                return null;
            }

            return document.querySelector('[data-neko-sidepanel-type="' + toggleId + '-actions"]');
        }

        isAgentSidePanelVisible(toggleId) {
            const sidePanel = this.getAgentSidePanel(toggleId);
            return !!(sidePanel && sidePanel.style.display === 'flex' && sidePanel.style.opacity !== '0');
        }

        async waitForAgentSidePanelLayoutStable(toggleId, timeoutMs) {
            const sidePanel = await this.waitForElement(() => {
                const panel = this.getAgentSidePanel(toggleId);
                return panel && this.isAgentSidePanelVisible(toggleId) ? panel : null;
            }, Number.isFinite(timeoutMs) ? Math.max(260, timeoutMs) : 900);
            if (!sidePanel) {
                return null;
            }

            // AvatarPopupUI may run an edge-overlap self-correction after the expand
            // animation starts. Wait through that correction window before sampling.
            if (!(await this.waitForSceneDelay(380))) {
                return null;
            }

            return this.waitForStableElementRect(
                sidePanel,
                Number.isFinite(timeoutMs) ? timeoutMs : 560
            );
        }

        collapseAgentSidePanel(toggleId) {
            const sidePanel = this.getAgentSidePanel(toggleId);
            if (!sidePanel) {
                return false;
            }

            if (sidePanel._hoverCollapseTimer) {
                window.clearTimeout(sidePanel._hoverCollapseTimer);
                sidePanel._hoverCollapseTimer = null;
            }

            if (sidePanel._collapseTimeout) {
                window.clearTimeout(sidePanel._collapseTimeout);
                sidePanel._collapseTimeout = null;
            }

            if (typeof sidePanel._collapse === 'function') {
                sidePanel._collapse();
                return true;
            }

            sidePanel.style.transition = 'none';
            sidePanel.style.opacity = '0';
            sidePanel.style.display = 'none';
            sidePanel.style.pointerEvents = 'none';
            sidePanel.style.transition = '';
            return true;
        }

        getCharacterAppearanceMenuId() {
            const prefix = this.resolveModelPrefix();
            if (prefix === 'vrm') {
                return 'vrm-manage';
            }
            if (prefix === 'mmd') {
                return 'mmd-manage';
            }
            return 'live2d-manage';
        }

        getTutorialModelManagerLanlanName() {
            const explicitName = typeof window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME === 'string'
                ? window.NEKO_YUI_GUIDE_MODEL_MANAGER_LANLAN_NAME.trim()
                : '';
            if (explicitName) {
                return explicitName;
            }

            return DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME;
        }

        getModelManagerWindowName(lanlanName, appearanceMenuId) {
            const name = typeof lanlanName === 'string' && lanlanName.trim()
                ? lanlanName.trim()
                : this.getTutorialModelManagerLanlanName();
            const menuId = appearanceMenuId || this.getCharacterAppearanceMenuId();
            if (menuId === 'vrm-manage') {
                return 'vrm-manage_' + encodeURIComponent(name);
            }
            if (menuId === 'mmd-manage') {
                return 'mmd-manage_' + encodeURIComponent(name);
            }
            return 'live2d-manage_' + encodeURIComponent(name);
        }

        getCharacterMenuElement(menuId) {
            if (!menuId) {
                return null;
            }

            return this.resolveElement('#${p}-sidepanel-' + menuId);
        }

        getCharacterSettingsSidePanel() {
            return document.querySelector('[data-neko-sidepanel-type="character-settings"]');
        }

        getFloatingButtonShell(element) {
            return this.spotlightController.getFloatingButtonShell(element);
        }

        isCircularFloatingButtonSpotlight(element) {
            return this.spotlightController.isCircularFloatingButtonSpotlight(element);
        }

        applyCircularFloatingButtonSpotlightHint(element) {
            return this.spotlightController.applyCircularFloatingButtonSpotlightHint(element);
        }

        getSettingsPeekTargets() {
            const appearanceMenuId = this.getCharacterAppearanceMenuId();
            return {
                characterMenu: this.getSettingsMenuElement('character'),
                appearanceItem: this.getCharacterMenuElement(appearanceMenuId),
                voiceCloneItem: this.getCharacterMenuElement('voice-clone')
            };
        }

        getDay4SettingsButtonSpotlightTarget() {
            return this.getFloatingButtonShell(
                this.getFallbackFloatingButton('settings')
                || this.resolveElement('#${p}-btn-settings')
            );
        }

        getDay4SettingsButtonPersistenceTarget(sceneId) {
            if ([
                'day4_chat_settings',
                'day4_model_behavior',
                'day4_gaze_follow',
                'day4_privacy_mode'
            ].includes(sceneId)) {
                return this.getDay4SettingsButtonSpotlightTarget();
            }
            if (sceneId === 'day4_model_lock' || sceneId === 'day4_return_home' || sceneId === 'day4_wrap') {
                return null;
            }
            return undefined;
        }

        getDay4MouseTrackingTarget() {
            const checkbox = this.resolveElement('#${p}-mouse-tracking-toggle');
            if (!checkbox) {
                return null;
            }
            const switchRow = typeof checkbox.closest === 'function'
                ? checkbox.closest('[role="switch"]')
                : null;
            const target = switchRow || checkbox.parentElement || checkbox;
            return target && this.isElementVisible(target) ? target : null;
        }

        setDay4LockSpotlightSafeAreaActive(active, reason) {
            const shouldActivate = active === true;
            if (this.day4LockSpotlightSafeAreaActive === shouldActivate) {
                return shouldActivate;
            }
            this.day4LockSpotlightSafeAreaActive = shouldActivate;
            try {
                window.nekoYuiGuideLockSpotlightSafeAreaActive = shouldActivate;
                if (shouldActivate) {
                    window.nekoYuiGuideLockSpotlightSafeAreaBottomPx = DAY4_LOCK_SPOTLIGHT_SAFE_BOTTOM_PX;
                } else {
                    delete window.nekoYuiGuideLockSpotlightSafeAreaBottomPx;
                }
            } catch (error) {
                console.warn('[YuiGuide] 同步 Day4 锁按钮安全区状态失败:', reason || 'scene', error);
            }
            this.refreshAvatarFloatingLockIconPosition();
            return shouldActivate;
        }

        syncDay4LockSpotlightSafeAreaForScene(scene) {
            const sceneId = scene && typeof scene.id === 'string' ? scene.id : '';
            return this.setDay4LockSpotlightSafeAreaActive(sceneId === 'day4_model_lock', sceneId || 'scene');
        }

        refreshAvatarFloatingLockIconPosition() {
            [
                window.live2dManager,
                window.vrmManager,
                window.mmdManager,
                window.pngtuberManager
            ].forEach((manager) => {
                if (!manager) {
                    return;
                }
                [
                    '_updateFloatingButtonsPositionNow',
                    'updateFloatingButtonsPosition',
                    'updateLockIconPosition',
                    '_floatingButtonsTicker'
                ].forEach((methodName) => {
                    if (typeof manager[methodName] !== 'function') {
                        return;
                    }
                    try {
                        manager[methodName]();
                    } catch (_) {}
                });
            });
        }

        adjustDay4LockSpotlightTarget(lockIcon) {
            if (!lockIcon || this.day4LockSpotlightSafeAreaActive !== true) {
                return false;
            }
            const rect = this.getElementRect(lockIcon);
            if (!rect || rect.height <= 0 || !Number.isFinite(rect.top)) {
                return false;
            }
            const fallbackMaxTop = Math.max(0, window.innerHeight - rect.height);
            const maxTop = typeof window.getNekoYuiGuideLockIconMaxTop === 'function'
                ? window.getNekoYuiGuideLockIconMaxTop(fallbackMaxTop, rect.height)
                : Math.max(0, window.innerHeight - rect.height - DAY4_LOCK_SPOTLIGHT_SAFE_BOTTOM_PX);
            if (!Number.isFinite(maxTop) || rect.top <= maxTop) {
                return false;
            }
            const currentTop = Number.parseFloat(lockIcon.style.top);
            if (!Number.isFinite(currentTop)) {
                return false;
            }
            lockIcon.style.top = Math.max(0, currentTop - (rect.top - maxTop)) + 'px';
            return true;
        }

        getAvatarFloatingLockIconElement() {
            const prefixes = [];
            const addPrefix = (value) => {
                const prefix = typeof value === 'string' ? value.trim().toLowerCase() : '';
                if (prefix && !prefixes.includes(prefix)) {
                    prefixes.push(prefix);
                }
            };
            addPrefix(this.getAvatarFloatingActiveModelType());
            addPrefix(this.resolveModelPrefix());
            ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach(addPrefix);

            for (let index = 0; index < prefixes.length; index += 1) {
                const lockIcon = document.getElementById(prefixes[index] + '-lock-icon');
                if (lockIcon) {
                    return lockIcon;
                }
            }
            return null;
        }

        getDay4LockButtonSpotlightTarget() {
            this.setDay4LockSpotlightSafeAreaActive(true, 'day4_model_lock');
            const lockIcon = this.getAvatarFloatingLockIconElement();
            if (!lockIcon) {
                return null;
            }
            lockIcon.style.setProperty('display', 'block', 'important');
            lockIcon.style.setProperty('visibility', 'visible', 'important');
            lockIcon.style.setProperty('opacity', '1', 'important');
            this.adjustDay4LockSpotlightTarget(lockIcon);
            return this.getFloatingButtonShell(lockIcon) || lockIcon;
        }

        getDay4PrivacyModeButtonTarget() {
            const privacyPanel = this.getAvatarFloatingSidePanel('interval-proactive-vision');
            const anchor = privacyPanel && privacyPanel._anchorElement
                ? privacyPanel._anchorElement
                : null;
            if (anchor && this.isElementVisible(anchor)) {
                return anchor;
            }

            const toggle = this.resolveElement('#${p}-toggle-proactive-vision');
            const switchRow = toggle && typeof toggle.closest === 'function'
                ? toggle.closest('[role="switch"]')
                : null;
            const target = switchRow || (toggle ? toggle.parentElement : null) || toggle;
            return target && this.isElementVisible(target) ? target : null;
        }

        getDay5CharacterSettingsButtonTarget() {
            const characterPanel = this.getAvatarFloatingSidePanel('character-settings');
            const anchor = characterPanel && characterPanel._anchorElement
                ? characterPanel._anchorElement
                : null;
            return anchor && this.isElementVisible(anchor) ? anchor : null;
        }

        getDay5CharacterSettingsPersistenceTarget(sceneId) {
            if (sceneId === 'day5_character_settings' || sceneId === 'day5_character_panic') {
                return this.getDay4SettingsButtonSpotlightTarget()
                    || this.getDay5CharacterSettingsButtonTarget();
            }
            if (sceneId === 'day5_memory_entry' || sceneId === 'day5_wrap') {
                return null;
            }
            return undefined;
        }

        getDay3CharacterSettingsPersistenceTarget(sceneId) {
            if (sceneId === 'day3_personalization_detail') {
                return this.getDay5CharacterSettingsButtonTarget()
                    || this.getSettingsMenuElement('character');
            }
            return undefined;
        }

        applyAvatarFloatingPersistenceOverride(highlightConfig, sceneId) {
            if (!highlightConfig) {
                return highlightConfig;
            }
            const persistentTargetGetters = [
                this.getDay3CharacterSettingsPersistenceTarget,
                this.getDay4SettingsButtonPersistenceTarget,
                this.getDay5CharacterSettingsPersistenceTarget
            ];
            persistentTargetGetters.forEach((getPersistentTarget) => {
                const persistentTarget = getPersistentTarget.call(this, sceneId);
                if (typeof persistentTarget !== 'undefined') {
                    highlightConfig.persistent = persistentTarget;
                }
            });
            return highlightConfig;
        }

        refreshSettingsPeekSpotlights(settingsButton) {
            const targets = this.getSettingsPeekTargets();
            const normalizeVisibleTarget = (element) => this.isElementVisible(element) ? element : null;
            const settingsButtonTarget = normalizeVisibleTarget(
                this.getFloatingButtonShell(
                    settingsButton
                    || this.getFallbackFloatingButton('settings')
                    || this.resolveElement('#${p}-btn-settings')
                )
            );
            const characterMenu = normalizeVisibleTarget(targets.characterMenu);
            const appearanceItem = normalizeVisibleTarget(targets.appearanceItem);
            const voiceCloneItem = normalizeVisibleTarget(targets.voiceCloneItem);
            const sidePanel = this.getCharacterSettingsSidePanel();
            const sidePanelVisible = sidePanel && this.isElementVisible(sidePanel) ? sidePanel : null;
            const characterChildrenBundle = sidePanelVisible
                ? this.createUnionSpotlight(
                    'settings-character-children-bundle',
                    [sidePanelVisible],
                    {
                        padding: DEFAULT_SPOTLIGHT_PADDING,
                        radius: 18
                    }
                )
                : (appearanceItem && voiceCloneItem)
                    ? this.createUnionSpotlight(
                        'settings-character-children-bundle',
                        [appearanceItem, voiceCloneItem],
                        {
                            padding: DEFAULT_SPOTLIGHT_PADDING,
                            radius: 18
                        }
                    )
                    : null;
            this.setSceneExtraSpotlights([
                settingsButtonTarget,
                characterMenu,
                characterChildrenBundle
            ].filter(Boolean));

            return {
                settingsButton: settingsButtonTarget,
                characterMenu: characterMenu,
                appearanceItem: appearanceItem,
                voiceCloneItem: voiceCloneItem,
                characterChildrenBundle: characterChildrenBundle
            };
        }

        async ensureCharacterSettingsSidePanelVisible() {
            const sidePanel = this.getCharacterSettingsSidePanel();
            const anchor = this.getSettingsMenuElement('character');
            if (!sidePanel || !anchor) {
                return false;
            }

            this.sidebarPauseController.trackPanel(sidePanel);
            this.collapseAvatarFloatingSidePanelsExcept(sidePanel);
            if (typeof sidePanel._expand === 'function') {
                sidePanel._expand();
            } else {
                anchor.dispatchEvent(new MouseEvent('mouseenter', {
                    bubbles: true,
                    cancelable: true,
                    view: window
                }));
            }

            const visiblePanel = await this.waitForVisibleElement(() => this.getCharacterSettingsSidePanel(), 1600);
            return !!visiblePanel;
        }

        collapseCharacterSettingsSidePanel() {
            const sidePanel = this.getCharacterSettingsSidePanel();
            if (!sidePanel) {
                return;
            }

            this.sidebarPauseController.trackPanel(sidePanel);
            if (sidePanel._hoverCollapseTimer) {
                window.clearTimeout(sidePanel._hoverCollapseTimer);
                sidePanel._hoverCollapseTimer = null;
            }

            if (typeof sidePanel._collapse === 'function') {
                sidePanel._collapse();
            } else {
                if (sidePanel._collapseTimeout) {
                    window.clearTimeout(sidePanel._collapseTimeout);
                    sidePanel._collapseTimeout = null;
                }
                sidePanel.style.transition = 'none';
                sidePanel.style.opacity = '0';
                sidePanel.style.display = 'none';
                sidePanel.style.pointerEvents = 'none';
                sidePanel.style.transition = '';
            }
        }

        normalizeHighlightTarget(target, fallbackKey) {
            return this.spotlightController.normalizeHighlightTarget(target, fallbackKey);
        }

        applyGuideHighlights(config) {
            const highlights = this.spotlightController.applyGuideHighlights(config);
            if (Object.prototype.hasOwnProperty.call(config || {}, 'secondary')) {
                this.customSecondarySpotlightTarget = highlights.secondary || null;
            }
            return highlights;
        }

        clearIntroFlow() {
            this.overlay.clearSpotlight();
        }

        waitForElement(resolveElement, timeoutMs) {
            const resolver = typeof resolveElement === 'function' ? resolveElement : function () { return null; };
            const timeout = Number.isFinite(timeoutMs) ? timeoutMs : 4000;

            return new Promise((resolve) => {
                const startedAt = Date.now();
                let pausedAt = 0;
                let pausedTotalMs = 0;
                const tick = () => {
                    if (this.isStopping()) {
                        resolve(null);
                        return;
                    }

                    const now = Date.now();
                    if (this.scenePausedForResistance) {
                        if (!pausedAt) {
                            pausedAt = now;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, now - pausedAt);
                        pausedAt = 0;
                    }

                    const element = resolver();
                    if (element) {
                        resolve(element);
                        return;
                    }

                    if ((now - startedAt - pausedTotalMs) >= timeout) {
                        resolve(null);
                        return;
                    }

                    window.setTimeout(tick, 80);
                };

                tick();
            });
        }

        isElementVisible(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return false;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return false;
            }

            if (element.offsetParent !== null) {
                return true;
            }

            try {
                return window.getComputedStyle(element).position === 'fixed';
            } catch (_) {
                return false;
            }
        }

        waitForVisibleElement(resolveElement, timeoutMs) {
            return this.waitForElement(() => {
                const element = typeof resolveElement === 'function' ? resolveElement() : null;
                return (this.getElementRect(element) || this.isElementVisible(element)) ? element : null;
            }, timeoutMs);
        }

        waitForDocumentSelector(selector, timeoutMs, requireVisible) {
            const normalizedSelector = this.expandSelector(typeof selector === 'string' ? selector.trim() : '');
            if (!normalizedSelector) {
                return Promise.resolve(null);
            }

            const shouldRequireVisible = requireVisible !== false;
            return this.waitForElement(() => {
                const element = this.queryDocumentSelector(normalizedSelector);
                if (!element) {
                    return null;
                }

                if (!shouldRequireVisible) {
                    return element;
                }

                return this.isElementVisible(element) ? element : null;
            }, timeoutMs);
        }

        waitForAnyDocumentSelector(selectors, timeoutMs, requireVisible) {
            const normalizedSelectors = (Array.isArray(selectors) ? selectors : [])
                .map((selector) => this.expandSelector(typeof selector === 'string' ? selector.trim() : ''))
                .filter(Boolean);
            if (normalizedSelectors.length === 0) {
                return Promise.resolve(null);
            }

            const shouldRequireVisible = requireVisible !== false;
            return this.waitForElement(() => {
                for (let index = 0; index < normalizedSelectors.length; index += 1) {
                    const element = this.queryDocumentSelector(normalizedSelectors[index]);
                    if (!element) {
                        continue;
                    }

                    if (!shouldRequireVisible || this.isElementVisible(element)) {
                        return element;
                    }
                }

                return null;
            }, timeoutMs);
        }

        waitForVisibleTarget(targets, timeoutMs) {
            const normalizedTargets = Array.isArray(targets) ? targets.slice() : [];
            if (normalizedTargets.length === 0) {
                return Promise.resolve(null);
            }

            return this.waitForElement(() => {
                for (let index = 0; index < normalizedTargets.length; index += 1) {
                    const target = normalizedTargets[index];
                    let element = null;

                    if (typeof target === 'function') {
                        try {
                            element = target.call(this);
                        } catch (error) {
                            console.warn('[YuiGuide] 解析目标元素失败:', error);
                            element = null;
                        }
                    } else if (typeof target === 'string') {
                        element = this.queryDocumentSelector(target);
                    }

                    if (this.isElementVisible(element)) {
                        return element;
                    }
                }

                return null;
            }, timeoutMs);
        }

        waitForStableElementRect(element, timeoutMs) {
            const normalizedTimeoutMs = Number.isFinite(timeoutMs) ? timeoutMs : 900;
            if (!element) {
                return Promise.resolve(null);
            }

            return new Promise((resolve) => {
                const startedAt = Date.now();
                let pausedAt = 0;
                let pausedTotalMs = 0;
                let lastRect = null;
                let stableCount = 0;

                const tick = () => {
                    if (this.destroyed) {
                        resolve(null);
                        return;
                    }

                    const now = Date.now();
                    if (this.scenePausedForResistance) {
                        if (!pausedAt) {
                            pausedAt = now;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, now - pausedAt);
                        pausedAt = 0;
                    }

                    if (!this.isElementVisible(element)) {
                        if ((now - startedAt - pausedTotalMs) >= normalizedTimeoutMs) {
                            resolve(null);
                            return;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    const rect = element.getBoundingClientRect();
                    if (!rect || rect.width <= 0 || rect.height <= 0) {
                        if ((now - startedAt - pausedTotalMs) >= normalizedTimeoutMs) {
                            resolve(null);
                            return;
                        }
                        window.setTimeout(tick, 80);
                        return;
                    }

                    if (lastRect) {
                        const delta = Math.max(
                            Math.abs(rect.left - lastRect.left),
                            Math.abs(rect.top - lastRect.top),
                            Math.abs(rect.width - lastRect.width),
                            Math.abs(rect.height - lastRect.height)
                        );
                        stableCount = delta <= 1 ? (stableCount + 1) : 0;
                    }
                    lastRect = {
                        left: rect.left,
                        top: rect.top,
                        width: rect.width,
                        height: rect.height
                    };

                    if (stableCount >= 2) {
                        resolve(element);
                        return;
                    }

                    if ((now - startedAt - pausedTotalMs) >= normalizedTimeoutMs) {
                        resolve(element);
                        return;
                    }

                    window.setTimeout(tick, 80);
                };

                tick();
            });
        }

        getChatIntroTarget() {
            return this.getChatInputTarget() || this.getChatWindowTarget();
        }

        getChatInputTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-part="inputBody"]',
                '#react-chat-window-root .compact-chat-surface-frame[data-compact-chat-state="input"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-geometry-part="capsuleBody"]',
                '#react-chat-window-root [data-compact-drag-surface="true"]',
                '#react-chat-window-root .compact-chat-surface-frame',
                '#react-chat-window-root .compact-chat-surface-shell',
                '#react-chat-window-root .composer-input',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return null;
        }

        getChatCapsuleInputTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-part="capsuleBody"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-geometry-part="inputBody"]',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root .composer-panel',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return this.getChatInputTarget();
        }

        getChatWindowTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-drag-surface="true"]',
                '#react-chat-window-root .compact-chat-surface-frame',
                '#react-chat-window-root .compact-chat-surface-shell',
                '#react-chat-window-shell',
                '#react-chat-window-root .chat-window',
                '#react-chat-window-root',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return null;
        }

        shouldNarrateInChat(stepId) {
            if (this.page !== 'home' || typeof stepId !== 'string' || !stepId) {
                return false;
            }
            return true;
        }

        isHomeChatExternalized() {
            if (typeof document === 'undefined') {
                return false;
            }
            if (window.__NEKO_MULTI_WINDOW__ === true) {
                return true;
            }
            const overlay = document.getElementById('react-chat-window-overlay');
            if (!overlay) {
                return false;
            }
            // CSS [hidden] 规则用 !important 控制可见性，不会写 inline style。
            // 内联 display:none 仅由外部 preload（如 preload-pet.js）设置以永久
            // 隐藏 Pet 窗口里嵌着的 React 聊天 overlay。
            return overlay.style.display === 'none';
        }

        getRecentExternalizedChatCursorScreenPoint(maxAgeMs) {
            try {
                const raw = window.localStorage && window.localStorage.getItem(YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY);
                const parsed = raw ? JSON.parse(raw) : null;
                if (!parsed || !Number.isFinite(parsed.x) || !Number.isFinite(parsed.y)) {
                    return null;
                }
                const at = Number(parsed.at);
                const ageLimit = Number.isFinite(maxAgeMs) ? maxAgeMs : 30000;
                if (Number.isFinite(at) && Date.now() - at > ageLimit) {
                    return null;
                }
                return { x: parsed.x, y: parsed.y };
            } catch (_) {
                return null;
            }
        }

        normalizeNiriPetPhysicalCropBounds(bounds) {
            if (!bounds || typeof bounds !== 'object') {
                return null;
            }

            const x = Number(bounds.x);
            const y = Number(bounds.y);
            const width = Number(bounds.width);
            const height = Number(bounds.height);
            if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
                return null;
            }

            return {
                x: Math.round(x),
                y: Math.round(y),
                width: Math.max(1, Math.round(width)),
                height: Math.max(1, Math.round(height))
            };
        }

        normalizeNiriPetPhysicalCropPoint(point) {
            if (!point || typeof point !== 'object') {
                return null;
            }

            const x = Number(point.x);
            const y = Number(point.y);
            return Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
        }

        getNiriPetPhysicalCropApi() {
            try {
                const api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
                if (!api || typeof api !== 'object') {
                    return null;
                }
                if (typeof api.isActive === 'function' && !api.isActive()) {
                    return null;
                }
                return api;
            } catch (_) {
                return null;
            }
        }

        areNiriPetPhysicalCropBoundsEquivalent(first, second) {
            return !!(first && second
                && Math.abs(Number(first.x || 0) - Number(second.x || 0)) <= 1
                && Math.abs(Number(first.y || 0) - Number(second.y || 0)) <= 1
                && Math.abs(Number(first.width || 0) - Number(second.width || 0)) <= 1
                && Math.abs(Number(first.height || 0) - Number(second.height || 0)) <= 1);
        }

        hasNiriPetPhysicalCropVirtualizedMetrics(metrics) {
            if (!metrics || metrics.niriPetPhysicalCrop !== true) {
                return false;
            }
            if (metrics.niriPetPhysicalCropMetricsVirtualized === true) {
                return true;
            }
            const screenBounds = this.normalizeNiriPetPhysicalCropBounds(metrics.contentBounds || metrics.bounds);
            const virtualBounds = this.normalizeNiriPetPhysicalCropBounds(metrics.niriPetPhysicalCropVirtualBounds);
            return this.areNiriPetPhysicalCropBoundsEquivalent(screenBounds, virtualBounds);
        }

        getNiriPetPhysicalCropState(metrics) {
            if (metrics && metrics.niriPetPhysicalCrop === true) {
                const cropBounds = this.normalizeNiriPetPhysicalCropBounds(
                    metrics.niriPetPhysicalCropBounds || metrics.contentBounds || metrics.bounds
                );
                const virtualBounds = this.normalizeNiriPetPhysicalCropBounds(metrics.niriPetPhysicalCropVirtualBounds);
                const offsetX = Number(metrics.niriPetPhysicalCropOffsetX);
                const offsetY = Number(metrics.niriPetPhysicalCropOffsetY);
                return cropBounds ? {
                    cropBounds,
                    virtualBounds,
                    offsetX: Number.isFinite(offsetX) ? Math.round(offsetX) : 0,
                    offsetY: Number.isFinite(offsetY) ? Math.round(offsetY) : 0,
                    metricsVirtualized: this.hasNiriPetPhysicalCropVirtualizedMetrics(metrics)
                } : null;
            }

            try {
                const api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
                if (!api || typeof api !== 'object') {
                    return null;
                }
                if (typeof api.isActive === 'function' && !api.isActive()) {
                    return null;
                }
                const state = typeof api.getState === 'function' ? api.getState() : null;
                const cropBounds = this.normalizeNiriPetPhysicalCropBounds(state && state.cropBounds);
                const virtualBounds = this.normalizeNiriPetPhysicalCropBounds(state && state.virtualBounds);
                if (!cropBounds) {
                    return null;
                }
                let offsetX = Number(state && state.offsetX);
                let offsetY = Number(state && state.offsetY);
                if (!Number.isFinite(offsetX) && virtualBounds) {
                    offsetX = cropBounds.x - virtualBounds.x;
                }
                if (!Number.isFinite(offsetY) && virtualBounds) {
                    offsetY = cropBounds.y - virtualBounds.y;
                }
                return {
                    cropBounds,
                    virtualBounds,
                    offsetX: Number.isFinite(offsetX) ? Math.round(offsetX) : 0,
                    offsetY: Number.isFinite(offsetY) ? Math.round(offsetY) : 0
                };
            } catch (_) {
                return null;
            }
        }

        toNiriPetPhysicalCropVirtualPoint(point) {
            const api = this.getNiriPetPhysicalCropApi();
            if (!api || typeof api.toVirtualPoint !== 'function') {
                return null;
            }
            try {
                return this.normalizeNiriPetPhysicalCropPoint(api.toVirtualPoint(point));
            } catch (_) {
                return null;
            }
        }

        toNiriPetPhysicalCropLocalPoint(point) {
            const api = this.getNiriPetPhysicalCropApi();
            if (!api || typeof api.toLocalPoint !== 'function') {
                return null;
            }
            try {
                return this.normalizeNiriPetPhysicalCropPoint(api.toLocalPoint(point));
            } catch (_) {
                return null;
            }
        }

        toNiriPetPhysicalCropVirtualPointWithState(point, cropState) {
            if (cropState && cropState.metricsVirtualized) {
                return {
                    x: Number(point && point.x || 0),
                    y: Number(point && point.y || 0)
                };
            }
            return this.toNiriPetPhysicalCropVirtualPoint(point) || {
                x: Number(point && point.x || 0) + Number(cropState && cropState.offsetX || 0),
                y: Number(point && point.y || 0) + Number(cropState && cropState.offsetY || 0)
            };
        }

        toNiriPetPhysicalCropLocalPointWithState(point, cropState) {
            if (cropState && cropState.metricsVirtualized) {
                return {
                    x: Number(point && point.x || 0),
                    y: Number(point && point.y || 0)
                };
            }
            return this.toNiriPetPhysicalCropLocalPoint(point) || {
                x: Number(point && point.x || 0) - Number(cropState && cropState.offsetX || 0),
                y: Number(point && point.y || 0) - Number(cropState && cropState.offsetY || 0)
            };
        }

        getGuideWindowMetricsSync() {
            try {
                const host = window.nekoTutorialOverlay;
                return host && typeof host.getWindowMetricsSync === 'function'
                    ? host.getWindowMetricsSync()
                    : null;
            } catch (_) {
                return null;
            }
        }

        getGuideScreenCoordinateBounds(metrics) {
            return metrics && (metrics.bounds || metrics.contentBounds) || null;
        }

        screenPointToLocalPoint(point) {
            if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
                return null;
            }

            const metrics = this.getGuideWindowMetricsSync();
            const cropState = this.getNiriPetPhysicalCropState(metrics);
            if (cropState && cropState.cropBounds) {
                const screenBounds = cropState.virtualBounds || cropState.cropBounds;
                const virtualPoint = {
                    x: point.x - Number(screenBounds.x || 0),
                    y: point.y - Number(screenBounds.y || 0)
                };
                const localPoint = this.toNiriPetPhysicalCropLocalPointWithState(virtualPoint, cropState);
                return {
                    x: localPoint.x,
                    y: localPoint.y
                };
            }
            let bounds = this.getGuideScreenCoordinateBounds(metrics);
            if (!bounds) {
                bounds = {
                    x: Number.isFinite(window.screenX) ? window.screenX : 0,
                    y: Number.isFinite(window.screenY) ? window.screenY : 0
                };
            }

            const viewport = window.visualViewport || null;
            const offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
            const offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
            return {
                x: point.x - Number(bounds.x || 0) - offsetLeft,
                y: point.y - Number(bounds.y || 0) - offsetTop
            };
        }

        localPointToScreenPoint(point) {
            if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
                return null;
            }

            const metrics = this.getGuideWindowMetricsSync();
            const cropState = this.getNiriPetPhysicalCropState(metrics);
            if (cropState && cropState.cropBounds) {
                const screenBounds = cropState.virtualBounds || cropState.cropBounds;
                const virtualPoint = this.toNiriPetPhysicalCropVirtualPointWithState(point, cropState);
                return {
                    x: Number(screenBounds.x || 0) + virtualPoint.x,
                    y: Number(screenBounds.y || 0) + virtualPoint.y
                };
            }
            let bounds = this.getGuideScreenCoordinateBounds(metrics);
            if (!bounds) {
                bounds = {
                    x: Number.isFinite(window.screenX) ? window.screenX : 0,
                    y: Number.isFinite(window.screenY) ? window.screenY : 0
                };
            }

            const viewport = window.visualViewport || null;
            const offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
            const offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
            return {
                x: Number(bounds.x || 0) + point.x + offsetLeft,
                y: Number(bounds.y || 0) + point.y + offsetTop
            };
        }

        rememberExternalizedChatCursorHandoffPoint(kind, effect) {
            const localPoint = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const screenPoint = this.localPointToScreenPoint(localPoint);
            if (!screenPoint) {
                return false;
            }
            try {
                window.localStorage.setItem(YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY, JSON.stringify({
                    x: screenPoint.x,
                    y: screenPoint.y,
                    kind: typeof kind === 'string' ? kind : '',
                    effect: typeof effect === 'string' ? effect : '',
                    source: 'home-director-handoff',
                    at: Date.now()
                }));
                return true;
            } catch (_) {
                return false;
            }
        }

        restoreCursorFromExternalizedChatAnchor(maxAgeMs) {
            if (!this.isHomeChatExternalized() || this.cursor.hasPosition()) {
                return false;
            }
            const screenPoint = this.getRecentExternalizedChatCursorScreenPoint(maxAgeMs);
            const localPoint = this.screenPointToLocalPoint(screenPoint);
            if (!localPoint || !Number.isFinite(localPoint.x) || !Number.isFinite(localPoint.y)) {
                return false;
            }
            this.cursor.showAt(localPoint.x, localPoint.y);
            return true;
        }

        getExternalizedChatCursorAnchorPoint(maxAgeMs) {
            if (!this.isHomeChatExternalized()) {
                return null;
            }
            const latestPoint = this.cursorAnchorStore.getLatestExternalizedPoint(maxAgeMs);
            if (latestPoint) {
                return latestPoint;
            }
            const screenPoint = this.getRecentExternalizedChatCursorScreenPoint(maxAgeMs);
            const localPoint = this.screenPointToLocalPoint(screenPoint);
            if (!localPoint || !Number.isFinite(localPoint.x) || !Number.isFinite(localPoint.y)) {
                return null;
            }
            return {
                x: localPoint.x,
                y: localPoint.y
            };
        }

        rememberAvatarFloatingSceneCursorAnchorFromExternalizedChat(sceneId, maxAgeMs) {
            const localPoint = this.getExternalizedChatCursorAnchorPoint(maxAgeMs);
            if (!localPoint) {
                return false;
            }
            this.rememberAvatarFloatingSceneCursorAnchorPoint(sceneId, localPoint);
            return true;
        }

        getExternalizedChatAnchorMoveDurationMs(fromPoint, toPoint) {
            if (
                !fromPoint
                || !toPoint
                || !Number.isFinite(fromPoint.x)
                || !Number.isFinite(fromPoint.y)
                || !Number.isFinite(toPoint.x)
                || !Number.isFinite(toPoint.y)
            ) {
                return 0;
            }
            const distance = Math.hypot(toPoint.x - fromPoint.x, toPoint.y - fromPoint.y);
            return distance < 2 ? 0 : 760;
        }

        moveHomeCursorToExternalizedChatAnchor(localPoint, detail) {
            const anchorDetail = detail || {};
            const currentPoint = this.overlay && typeof this.overlay.getCursorPosition === 'function'
                ? this.overlay.getCursorPosition()
                : null;
            const hasCurrentPoint = !!(
                currentPoint
                && Number.isFinite(currentPoint.x)
                && Number.isFinite(currentPoint.y)
            );
            const hasVisibleCursor = typeof this.cursor.hasVisiblePosition === 'function'
                ? this.cursor.hasVisiblePosition()
                : this.cursor.hasPosition();
            const runCursorEffect = () => {
                if (anchorDetail.effect === 'wobble') {
                    const effectDurationMs = Number.isFinite(anchorDetail.effectDurationMs)
                        ? Math.max(0, Math.floor(anchorDetail.effectDurationMs))
                        : 0;
                    this.cursor.wobble(effectDurationMs);
                }
            };
            const rememberMovePromise = (movePromise) => {
                this.latestExternalizedChatCursorMoveSceneId = this.currentSceneId || '';
                this.latestExternalizedChatCursorMovePromise = Promise.resolve(movePromise)
                    .then(() => true)
                    .catch(() => false);
                this.resolveExternalizedChatCursorMoveWaiters(
                    this.latestExternalizedChatCursorMoveSceneId,
                    this.latestExternalizedChatCursorMovePromise
                );
                return this.latestExternalizedChatCursorMovePromise;
            };
            const isSettledPcAnchor = !!(
                anchorDetail.settled === true
                && this.overlay
                && typeof this.overlay.isPcOverlayActive === 'function'
                && this.overlay.isPcOverlayActive()
                && typeof this.overlay.syncCursorPosition === 'function'
            );

            if (isSettledPcAnchor) {
                const movePromise = Promise.resolve(
                    this.overlay.syncCursorPosition(localPoint.x, localPoint.y, true)
                );
                rememberMovePromise(movePromise);
                movePromise.then(runCursorEffect).catch(() => {});
                return;
            }

            if (!hasVisibleCursor && hasCurrentPoint) {
                this.cursor.showAt(currentPoint.x, currentPoint.y);
            }

            if (hasVisibleCursor || hasCurrentPoint) {
                const durationMs = this.getExternalizedChatAnchorMoveDurationMs(currentPoint, localPoint);
                const movePromise = durationMs > 0
                    ? this.cursor.moveToPoint(localPoint.x, localPoint.y, {
                        durationMs: durationMs,
                        cancelCheck: () => this.isStopping()
                    })
                    : Promise.resolve(true);
                rememberMovePromise(movePromise);
                movePromise
                    .then(() => {
                        if (durationMs <= 0 && !hasVisibleCursor) {
                            this.cursor.showAt(localPoint.x, localPoint.y);
                        }
                        runCursorEffect();
                    })
                    .catch(() => {});
                return;
            }

            this.cursor.showAt(localPoint.x, localPoint.y);
            rememberMovePromise(Promise.resolve(true));
            runCursorEffect();
        }

        resolveExternalizedChatCursorMoveWaiters(sceneId, movePromise) {
            const waiters = Array.isArray(this.pendingExternalizedChatCursorMoveWaiters)
                ? this.pendingExternalizedChatCursorMoveWaiters
                : [];
            if (!waiters.length) {
                return;
            }
            const actualSceneId = typeof sceneId === 'string' ? sceneId : '';
            const remaining = [];
            waiters.forEach((waiter) => {
                if (!waiter || typeof waiter.finish !== 'function') {
                    return;
                }
                if (waiter.sceneId && actualSceneId && waiter.sceneId !== actualSceneId) {
                    remaining.push(waiter);
                    return;
                }
                if (waiter.sceneId && !actualSceneId) {
                    remaining.push(waiter);
                    return;
                }
                Promise.resolve(movePromise).then(
                    () => waiter.finish(true),
                    () => waiter.finish(false)
                );
            });
            this.pendingExternalizedChatCursorMoveWaiters = remaining;
        }

        waitForExternalizedChatCursorMove(sceneId, maxWaitMs) {
            const movePromise = this.latestExternalizedChatCursorMovePromise;
            const expectedSceneId = typeof sceneId === 'string' ? sceneId : '';
            const actualSceneId = this.latestExternalizedChatCursorMoveSceneId || '';
            const timeoutMs = Number.isFinite(maxWaitMs)
                ? Math.max(0, Math.floor(maxWaitMs))
                : 1600;
            const waitForPromise = (promise) => new Promise((resolve) => {
                let settled = false;
                let timer = 0;
                const finish = (value) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (timer) {
                        window.clearTimeout(timer);
                    }
                    resolve(!!value);
                };
                Promise.resolve(promise).then(
                    () => finish(true),
                    () => finish(false)
                );
                if (timeoutMs > 0) {
                    timer = window.setTimeout(() => finish(false), timeoutMs);
                }
            });
            if (movePromise && !(expectedSceneId && actualSceneId && actualSceneId !== expectedSceneId)) {
                return waitForPromise(movePromise);
            }
            if (timeoutMs <= 0) {
                return Promise.resolve(false);
            }
            this.pendingExternalizedChatCursorMoveWaiters = Array.isArray(this.pendingExternalizedChatCursorMoveWaiters)
                ? this.pendingExternalizedChatCursorMoveWaiters
                : [];
            return new Promise((resolve) => {
                const waiter = {
                    sceneId: expectedSceneId,
                    timer: 0,
                    settled: false,
                    finish: (value) => {
                        if (waiter.settled) {
                            return;
                        }
                        waiter.settled = true;
                        if (waiter.timer) {
                            window.clearTimeout(waiter.timer);
                            waiter.timer = 0;
                        }
                        resolve(!!value);
                    }
                };
                waiter.timer = window.setTimeout(() => {
                    this.pendingExternalizedChatCursorMoveWaiters = (this.pendingExternalizedChatCursorMoveWaiters || [])
                        .filter((candidate) => candidate !== waiter);
                    waiter.finish(false);
                }, timeoutMs);
                this.pendingExternalizedChatCursorMoveWaiters.push(waiter);
            });
        }

        onExternalChatCursorAnchor(event) {
            if (this.destroyed || !this.isHomeChatExternalized()) {
                return;
            }
            const detail = event && event.detail ? event.detail : {};
            const screenPoint = {
                x: Number(detail.x),
                y: Number(detail.y)
            };
            if (!Number.isFinite(screenPoint.x) || !Number.isFinite(screenPoint.y)) {
                return;
            }
            const localPoint = this.screenPointToLocalPoint(screenPoint);
            if (!localPoint || !Number.isFinite(localPoint.x) || !Number.isFinite(localPoint.y)) {
                return;
            }
            this.cursorAnchorStore.rememberLatestExternalizedPoint({
                x: localPoint.x,
                y: localPoint.y,
                at: Number(detail.timestamp) || Date.now(),
                kind: typeof detail.kind === 'string' ? detail.kind : '',
                effect: typeof detail.effect === 'string' ? detail.effect : '',
                effectDurationMs: Number.isFinite(detail.effectDurationMs)
                    ? Math.max(0, Math.floor(detail.effectDurationMs))
                    : 0,
                settled: detail.settled === true
            });
            if (this.currentSceneId) {
                this.rememberAvatarFloatingSceneCursorAnchorPoint(this.currentSceneId, localPoint);
            }
            if (
                detail.kind
                && this.overlay
                && typeof this.overlay.isPcOverlayActive === 'function'
                && this.overlay.isPcOverlayActive()
            ) {
                this.moveHomeCursorToExternalizedChatAnchor(localPoint, detail);
            }
        }

        onExternalChatReady() {
            if (this.destroyed) {
                return;
            }

            if (this.interactionTakeover && typeof this.interactionTakeover.onExternalChatReady === 'function') {
                this.interactionTakeover.onExternalChatReady();
            }
        }

        postExternalChatGuideMessage(message) {
            if (!message || typeof message !== 'object') {
                return false;
            }

            const outgoingMessage = Object.assign({}, message);
            return !!(
                this.chatBridgeCommandBus
                && typeof this.chatBridgeCommandBus.post === 'function'
                && this.chatBridgeCommandBus.post(outgoingMessage)
            );
        }

        getSceneSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'day1_intro_greeting' || stepId === 'day1_takeover_return_control') {
                return this.getChatCapsuleInputTarget() || this.getChatInputTarget() || this.getChatWindowTarget() || null;
            }

            if (stepId === 'day1_intro_activation') {
                return this.getChatInputTarget() || this.getChatWindowTarget() || null;
            }

            if (stepId === 'day1_takeover_capture_cursor') {
                return fallbackTarget;
            }

            if (this.shouldNarrateInChat(stepId)) {
                return this.introGreetingChatHighlightCleared
                    ? (this.getChatInputTarget() || this.getChatWindowTarget() || fallbackTarget)
                    : (this.getChatWindowTarget() || fallbackTarget);
            }

            return fallbackTarget;
        }

        getActionSpotlightTarget(stepId, performance) {
            const selector = (performance && (performance.cursorTarget || this.currentStep && this.currentStep.anchor))
                || (this.currentStep && this.currentStep.anchor)
                || '';
            const fallbackTarget = selector ? this.resolveElement(selector) : null;
            if (this.page !== 'home') {
                return fallbackTarget;
            }

            if (stepId === 'day1_takeover_capture_cursor') {
                return this.getFloatingButtonShell(fallbackTarget) || fallbackTarget;
            }

            return null;
        }

        highlightChatWindow() {
            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.clearHomeSpotlightsForExternalizedChat();
                    this.interactionTakeover.setExternalizedChatSpotlight('input');
                }
                return;
            }

            const target = this.getChatWindowTarget() || this.getChatInputTarget();
            if (!target) {
                return;
            }

            if (typeof target.scrollIntoView === 'function') {
                try {
                    target.scrollIntoView({
                        behavior: 'smooth',
                        block: 'center',
                        inline: 'nearest'
                    });
                } catch (_) {
                    target.scrollIntoView();
                }
            }

            this.setSpotlightGeometryHint(target, {
                padding: DEFAULT_SPOTLIGHT_PADDING + 3
            });
            this.overlay.setPersistentSpotlight(target);
        }

        clearIntroGreetingChatHighlight() {
            this.introGreetingChatHighlightCleared = true;

            if (this.isHomeChatExternalized()) {
                if (this.interactionTakeover && typeof this.interactionTakeover.setExternalizedChatSpotlight === 'function') {
                    this.interactionTakeover.setExternalizedChatSpotlight('');
                }
                return;
            }

            this.overlay.clearPersistentSpotlight();
        }

        getChatIntroActivationTarget() {
            const preferredSelectors = [
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-drag-surface="true"]',
                '#react-chat-window-root .composer-input-shell',
                '#react-chat-window-root .composer-panel',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ];

            for (let index = 0; index < preferredSelectors.length; index += 1) {
                const element = this.resolveElement(preferredSelectors[index]);
                if (!element) {
                    continue;
                }

                const rect = typeof element.getBoundingClientRect === 'function'
                    ? element.getBoundingClientRect()
                    : null;
                if (!rect || rect.width <= 0 || rect.height <= 0) {
                    continue;
                }

                return element;
            }

            return this.getChatIntroTarget();
        }

        clearSceneTimers() {
            if (this.sceneResources && typeof this.sceneResources.destroy === 'function') {
                this.sceneResources.destroy();
            } else {
                this.sceneTimers.forEach(function (timerId) {
                    window.clearTimeout(timerId);
                });
            }
            this.sceneTimers.clear();
            this.sceneResources = createYuiGuideScopedTutorialResources();
        }

        clearGuideChatStreamTimers() {
            if (this.guideChatStreamResources && typeof this.guideChatStreamResources.destroy === 'function') {
                this.guideChatStreamResources.destroy();
            } else {
                this.guideChatStreamTimers.forEach(function (timerId) {
                    window.clearTimeout(timerId);
                });
            }
            this.guideChatStreamTimers.clear();
            this.guideChatStreamResources = createYuiGuideScopedTutorialResources();
        }

        scheduleGuideChatStream(callback, delayMs) {
            const timerId = this.guideChatStreamResources.setTimeout(() => {
                this.guideChatStreamTimers.delete(timerId);
                callback();
            }, delayMs);
            this.guideChatStreamTimers.add(timerId);
            return timerId;
        }

        schedule(callback, delayMs) {
            const timerId = this.sceneResources.setTimeout(() => {
                this.sceneTimers.delete(timerId);
                callback();
            }, delayMs);
            this.sceneTimers.add(timerId);
            return timerId;
        }

        clearNarrationResumeTimer() {
            if (this.narrationResumeTimer) {
                window.clearTimeout(this.narrationResumeTimer);
                this.narrationResumeTimer = null;
            }
        }

        pauseCurrentSceneForResistance() {
            this.pauseCoordinator.pauseForResistance();
            if (
                this.interactionTakeover
                && typeof this.interactionTakeover.preserveExternalizedChatSpotlightDuringResistance === 'function'
            ) {
                this.interactionTakeover.preserveExternalizedChatSpotlightDuringResistance();
            }
        }

        resumeCurrentSceneAfterResistance() {
            this.pauseCoordinator.resumeAfterResistance();
        }

        waitUntilSceneResumed() {
            if (!this.scenePausedForResistance) {
                return Promise.resolve();
            }

            return new Promise((resolve) => {
                this.scenePauseResolvers.push(resolve);
            });
        }

        async waitForSceneDelay(delayMs, options) {
            const totalMs = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 0;
            const shouldContinue = options && typeof options.shouldContinue === 'function'
                ? options.shouldContinue
                : null;
            if (totalMs <= 0) {
                return true;
            }

            let remainingMs = totalMs;
            let lastTickAt = Date.now();

            while (remainingMs > 0) {
                if (this.isStopping() || (shouldContinue && !shouldContinue())) {
                    return false;
                }

                if (this.scenePausedForResistance) {
                    await this.waitUntilSceneResumed();
                    lastTickAt = Date.now();
                    continue;
                }

                const sliceMs = Math.min(remainingMs, 80);
                await wait(sliceMs);
                if (this.isStopping() || (shouldContinue && !shouldContinue())) {
                    return false;
                }

                const now = Date.now();
                remainingMs -= Math.max(0, now - lastTickAt);
                lastTickAt = now;
            }

            return true;
        }

        getGuideTimelineCueConfig(voiceKey, cueName) {
            const normalizedVoiceKey = typeof voiceKey === 'string' ? voiceKey.trim() : '';
            const normalizedCueName = typeof cueName === 'string' ? cueName.trim() : '';
            if (!normalizedVoiceKey || !normalizedCueName) {
                return null;
            }

            const steps = this.registry && this.registry.steps && typeof this.registry.steps === 'object'
                ? this.registry.steps
                : {};
            const stepIds = Object.keys(steps);
            for (let index = 0; index < stepIds.length; index += 1) {
                const step = steps[stepIds[index]];
                const performance = step && step.performance ? step.performance : {};
                const timeline = Array.isArray(performance.timeline) ? performance.timeline : [];
                for (let timelineIndex = 0; timelineIndex < timeline.length; timelineIndex += 1) {
                    const cue = timeline[timelineIndex];
                    if (!cue || cue.action !== normalizedCueName || !Number.isFinite(cue.at)) {
                        continue;
                    }

                    const cueVoiceKey = typeof cue.voiceKey === 'string' && cue.voiceKey.trim()
                        ? cue.voiceKey.trim()
                        : (typeof performance.voiceKey === 'string' ? performance.voiceKey.trim() : '');
                    if (cueVoiceKey !== normalizedVoiceKey) {
                        continue;
                    }

                    return {
                        at: clamp(cue.at, 0, 1),
                        fallbackDurationMs: this.getGuideVoiceDurationMs(normalizedVoiceKey, 'zh')
                    };
                }
            }

            const fallbackConfig = getGuideAudioCueConfig(normalizedVoiceKey);
            const fallbackCue = fallbackConfig && fallbackConfig.cues
                ? fallbackConfig.cues[normalizedCueName]
                : null;
            if (!fallbackCue || !Number.isFinite(fallbackCue.at)) {
                return null;
            }
            const fallbackCueLocale = resolveGuideAudioLocale();
            const localeCueAt = fallbackCue.atByLocale && Number.isFinite(fallbackCue.atByLocale[fallbackCueLocale])
                ? fallbackCue.atByLocale[fallbackCueLocale]
                : fallbackCue.at;

            return {
                at: clamp(localeCueAt, 0, 1),
                fallbackDurationMs: Number.isFinite(fallbackConfig.fallbackDurationMs)
                    ? Math.max(1, fallbackConfig.fallbackDurationMs)
                    : 0
            };
        }

        resolveGuideVoiceCueTargetMs(voiceKey, cueName, playbackDurationMs, fallbackText) {
            const cueConfig = this.getGuideTimelineCueConfig(voiceKey, cueName);
            if (!cueConfig) {
                return 0;
            }

            const fallbackDurationMs = Number.isFinite(cueConfig.fallbackDurationMs)
                ? Math.max(1, cueConfig.fallbackDurationMs)
                : 0;
            if (cueConfig.at <= 0) {
                return 0;
            }

            const targetDurationMs = Number.isFinite(playbackDurationMs) && playbackDurationMs > 0
                ? playbackDurationMs
                : this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale())
                    || fallbackDurationMs;
            return clamp(Math.round(targetDurationMs * cueConfig.at), 0, targetDurationMs);
        }

        async waitForNarrationCue(voiceKey, cueName) {
            const activeNarrationAtStart = this.activeNarration;
            const fallbackText = activeNarrationAtStart && activeNarrationAtStart.voiceKey === voiceKey
                ? activeNarrationAtStart.text
                : '';
            const fallbackTargetMs = this.resolveGuideVoiceCueTargetMs(voiceKey, cueName, 0, fallbackText);
            if (fallbackTargetMs <= 0) {
                return true;
            }

            const startedAt = Date.now();
            const maxActiveWaitMs = clamp(fallbackTargetMs + 4500, 1800, 18000);
            let fallbackElapsedMs = 0;
            let pausedAt = 0;
            let pausedTotalMs = 0;
            let lastTickAt = Date.now();
            let sawAudioPlayback = false;

            while (!this.isStopping()) {
                if (this.scenePausedForResistance) {
                    if (!pausedAt) {
                        pausedAt = Date.now();
                    }
                    await this.waitUntilSceneResumed();
                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, Date.now() - pausedAt);
                        pausedAt = 0;
                    }
                    lastTickAt = Date.now();
                    continue;
                }

                if (pausedAt) {
                    pausedTotalMs += Math.max(0, Date.now() - pausedAt);
                    pausedAt = 0;
                }

                if ((Date.now() - startedAt - pausedTotalMs) >= maxActiveWaitMs) {
                    console.warn('[YuiGuide] 旁白 cue 等待超时，继续流程:', voiceKey, cueName);
                    return true;
                }

                const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
                if (playbackSnapshot && playbackSnapshot.voiceKey === voiceKey) {
                    sawAudioPlayback = true;
                    const cueTargetMs = this.resolveGuideVoiceCueTargetMs(
                        voiceKey,
                        cueName,
                        playbackSnapshot.durationMs,
                        fallbackText
                    );
                    if (playbackSnapshot.currentTimeMs >= cueTargetMs) {
                        return true;
                    }

                    await wait(60);
                    lastTickAt = Date.now();
                    continue;
                }

                const activeNarration = this.activeNarration;
                if (sawAudioPlayback && (!activeNarration || activeNarration.voiceKey !== voiceKey)) {
                    return true;
                }

                const sliceMs = Math.min(Math.max(40, fallbackTargetMs - fallbackElapsedMs), 80);
                await wait(sliceMs);
                if (this.isStopping()) {
                    return false;
                }

                const now = Date.now();
                if (!sawAudioPlayback && (!activeNarration || !activeNarration.interrupted)) {
                    fallbackElapsedMs += Math.max(0, now - lastTickAt);
                    if (fallbackElapsedMs >= fallbackTargetMs) {
                        return true;
                    }
                }
                lastTickAt = now;
            }

            return false;
        }

        getGuideVoiceDurationMs(voiceKey, locale) {
            const durationConfig = getGuideAudioDurationConfig(voiceKey);
            if (!durationConfig) {
                return 0;
            }

            const normalizedLocale = resolveGuideAudioLocale(locale || resolveGuideLocale());
            const exactDurationMs = Number.isFinite(durationConfig[normalizedLocale])
                ? durationConfig[normalizedLocale]
                : 0;
            if (exactDurationMs > 0) {
                return exactDurationMs;
            }

            const fallbackDurationMs = Number.isFinite(durationConfig.en)
                ? durationConfig.en
                : (Number.isFinite(durationConfig.zh) ? durationConfig.zh : 0);
            return fallbackDurationMs > 0 ? fallbackDurationMs : 0;
        }

        getGuideVoiceTimingScale(voiceKey) {
            const baseDurationMs = this.getGuideVoiceDurationMs(voiceKey, 'zh');
            if (baseDurationMs <= 0) {
                return 1;
            }

            const currentDurationMs = this.getGuideVoiceDurationMs(voiceKey, resolveGuideLocale());
            if (currentDurationMs <= 0) {
                return 1;
            }

            return clamp(currentDurationMs / baseDurationMs, 0.75, 2.5);
        }

        cancelActiveNarration() {
            const narration = this.activeNarration;
            this.activeNarration = null;
            this.clearNarrationResumeTimer();

            if (narration) {
                narration.cancelled = true;
            }
            this.voiceQueue.stop();
            if (narration && typeof narration.resolve === 'function') {
                narration.resolve();
            }
        }

        async runNarration(narration) {
            if (!narration || narration.cancelled || this.destroyed) {
                return;
            }

            if (narration.running) {
                return;
            }

            const playbackStartIndex = clamp(
                Number.isFinite(narration.resumeIndex) ? narration.resumeIndex : 0,
                0,
                narration.text.length
            );
            const playbackText = narration.text.slice(playbackStartIndex);

            if (!playbackText.trim()) {
                narration.resumeIndex = narration.text.length;
                narration.resumeAudioOffsetMs = 0;
                if (this.activeNarration === narration) {
                    this.activeNarration = null;
                }
                if (typeof narration.resolve === 'function') {
                    narration.resolve();
                }
                return;
            }

            narration.running = true;
            narration.playbackStartIndex = playbackStartIndex;
            narration.playbackStartAt = Date.now();
            await this.voiceQueue.speak(playbackText, {
                voiceKey: narration.voiceKey,
                startAtMs: Number.isFinite(narration.resumeAudioOffsetMs) ? narration.resumeAudioOffsetMs : 0,
                minDurationMs: Number.isFinite(narration.minDurationMs)
                    ? narration.minDurationMs
                    : 0,
                onBoundary: (event) => {
                    const charIndex = event && Number.isFinite(event.charIndex) ? event.charIndex : 0;
                    const absoluteCharIndex = clamp(
                        narration.playbackStartIndex + charIndex,
                        narration.playbackStartIndex,
                        narration.text.length
                    );
                    narration.resumeIndex = absoluteCharIndex;
                    if (typeof narration.onBoundary === 'function') {
                        try {
                            narration.onBoundary(Object.assign({}, event, {
                                absoluteCharIndex: absoluteCharIndex,
                                fullText: narration.text
                            }));
                        } catch (error) {
                            console.warn('[YuiGuide] 旁白边界扩展回调失败:', error);
                        }
                    }
                }
            });
            narration.running = false;

            if (this.destroyed || narration.cancelled) {
                if (this.activeNarration === narration) {
                    this.activeNarration = null;
                }
                if (typeof narration.resolve === 'function') {
                    narration.resolve();
                }
                return;
            }

            if (narration.interrupted) {
                return;
            }

            narration.resumeIndex = narration.text.length;
            narration.resumeAudioOffsetMs = 0;
            if (this.activeNarration === narration) {
                this.activeNarration = null;
            }
            if (typeof narration.resolve === 'function') {
                narration.resolve();
            }
        }

        async speakLineAndWait(text, options) {
            const content = typeof text === 'string' ? text.trim() : '';
            if (!content || this.destroyed) {
                return;
            }

            this.cancelActiveNarration();
            const normalizedOptions = options || {};

            await new Promise((resolve) => {
                const narration = {
                    text: content,
                    voiceKey: typeof normalizedOptions.voiceKey === 'string' ? normalizedOptions.voiceKey : '',
                    resumeIndex: 0,
                    resumeAudioOffsetMs: 0,
                    playbackStartIndex: 0,
                    playbackStartAt: 0,
                    minDurationMs: Number.isFinite(normalizedOptions.minDurationMs)
                        ? normalizedOptions.minDurationMs
                        : 0,
                    onBoundary: typeof normalizedOptions.onBoundary === 'function' ? normalizedOptions.onBoundary : null,
                    resolve: resolve,
                    interrupted: false,
                    cancelled: false,
                    running: false
                };
                this.activeNarration = narration;
                this.runNarration(narration).catch((error) => {
                    console.warn('[YuiGuide] 等待语音结束失败:', error);
                    if (this.activeNarration === narration) {
                        this.activeNarration = null;
                    }
                    resolve();
                });
            });
        }

        interruptNarrationForResistance() {
            const narration = this.activeNarration;
            if (!narration || narration.cancelled) {
                const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
                if (!playbackSnapshot) {
                    return false;
                }

                this.clearNarrationResumeTimer();
                this.voiceQueue.stop();
                return true;
            }

            if (narration.interrupted) {
                return true;
            }

            if (narration.running) {
                const playbackStartIndex = Number.isFinite(narration.playbackStartIndex) ? narration.playbackStartIndex : 0;
                const playbackStartAt = Number.isFinite(narration.playbackStartAt) ? narration.playbackStartAt : 0;
                const elapsedMs = playbackStartAt > 0 ? Math.max(0, Date.now() - playbackStartAt) : 0;
                const estimatedChars = Math.floor(elapsedMs / 280);
                const estimatedIndex = clamp(
                    playbackStartIndex + estimatedChars,
                    playbackStartIndex,
                    narration.text.length
                );
                narration.resumeIndex = Math.max(
                    Number.isFinite(narration.resumeIndex) ? narration.resumeIndex : playbackStartIndex,
                    estimatedIndex
                );
            }

            const playbackSnapshot = this.voiceQueue.capturePlaybackSnapshot();
            this.applyNarrationResumePoint(narration, playbackSnapshot);

            narration.interrupted = true;
            this.clearNarrationResumeTimer();
            this.voiceQueue.stop();
            return true;
        }

        applyNarrationResumePoint(narration, playbackSnapshot) {
            if (!narration || !playbackSnapshot || !Number.isFinite(playbackSnapshot.currentTimeMs)) {
                if (narration) {
                    narration.resumeAudioOffsetMs = 0;
                }
                return;
            }

            const textLength = typeof narration.text === 'string' ? narration.text.length : 0;
            const configuredDurationMs = this.getGuideVoiceDurationMs(narration.voiceKey, resolveGuideLocale());
            const durationMs = Number.isFinite(playbackSnapshot.durationMs) && playbackSnapshot.durationMs > 0
                ? Math.round(playbackSnapshot.durationMs)
                : (Number.isFinite(configuredDurationMs) && configuredDurationMs > 0 ? Math.round(configuredDurationMs) : 0);
            const rawOffsetMs = Math.max(0, Math.round(playbackSnapshot.currentTimeMs));
            const maxResumeOffsetMs = durationMs > 0
                ? Math.max(0, durationMs - NARRATION_RESUME_MIN_REMAINING_MS)
                : rawOffsetMs;
            const resumeAudioOffsetMs = clamp(
                rawOffsetMs - NARRATION_RESUME_BACKTRACK_MS,
                0,
                maxResumeOffsetMs
            );
            narration.resumeAudioOffsetMs = resumeAudioOffsetMs;

            if (durationMs <= 0 || textLength <= 0) {
                return;
            }

            const audioProgressIndex = clamp(
                Math.floor((resumeAudioOffsetMs / durationMs) * textLength),
                0,
                Math.max(0, textLength - 1)
            );
            narration.resumeIndex = audioProgressIndex;
        }

        scheduleNarrationResume(options) {
            this.clearNarrationResumeTimer();
            const resumeOptions = options || {};

            const attemptResume = () => {
                const narration = this.activeNarration;
                if (!narration || narration.cancelled || this.destroyed) {
                    this.restoreCurrentScenePresentation({
                        skipEmotion: !!resumeOptions.skipEmotion,
                        preserveSpotlights: !!resumeOptions.preserveSpotlights
                    });
                    return;
                }

                if (!narration.interrupted) {
                    return;
                }

                const lastMotionAt = this.lastPointerPoint && Number.isFinite(this.lastPointerPoint.t)
                    ? this.lastPointerPoint.t
                    : 0;
                if ((Date.now() - lastMotionAt) < 720) {
                    this.narrationResumeTimer = window.setTimeout(attemptResume, 240);
                    return;
                }

                narration.interrupted = false;
                this.restoreCurrentScenePresentation({
                    skipEmotion: !!resumeOptions.skipEmotion,
                    preserveSpotlights: !!resumeOptions.preserveSpotlights
                });
                this.runNarration(narration).catch((error) => {
                    console.warn('[YuiGuide] 恢复教程语音失败:', error);
                });
            };

            this.narrationResumeTimer = window.setTimeout(attemptResume, 720);
        }

        setCurrentScene(stepId, context) {
            this.currentSceneId = stepId || null;
            this.currentStep = stepId ? this.getStep(stepId) : null;
            this.currentContext = context || null;
        }

        restoreCurrentScenePresentation(options) {
            if (this.destroyed || this.angryExitTriggered || !this.currentStep) {
                return;
            }

            if (this.guideInterruptPresentationActive) {
                return;
            }

            const performance = this.currentStep.performance || {};
            const bubbleText = this.resolvePerformanceBubbleText(performance);
            if (!(options && options.preserveSpotlights)) {
                const spotlightTarget = this.getSceneSpotlightTarget(this.currentSceneId, performance);
                if (spotlightTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(spotlightTarget);
                    this.overlay.setPersistentSpotlight(spotlightTarget);
                } else {
                    this.overlay.clearPersistentSpotlight();
                }

                const actionSpotlightTarget = this.getActionSpotlightTarget(this.currentSceneId, performance);
                const dedupedActionSpotlightTarget = actionSpotlightTarget === spotlightTarget
                    ? null
                    : actionSpotlightTarget;
                if (dedupedActionSpotlightTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(dedupedActionSpotlightTarget);
                    this.overlay.activateSpotlight(dedupedActionSpotlightTarget);
                } else {
                    this.overlay.clearActionSpotlight();
                }

                if (this.customSecondarySpotlightTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(this.customSecondarySpotlightTarget);
                    this.overlay.activateSecondarySpotlight(this.customSecondarySpotlightTarget);
                }
            }

            if (this.shouldNarrateInChat(this.currentSceneId)) {
                this.overlay.hideBubble();
            } else if (bubbleText) {
                this.showGuideBubble(bubbleText, {
                    title: 'Yui',
                    emotion: performance.emotion || 'neutral',
                    anchorRect: this.resolveRect(this.currentStep.anchor)
                }, this.currentSceneId);
            } else {
                this.overlay.hideBubble();
            }

            if (!(options && options.skipEmotion)) {
                if (performance.emotion) {
                    this.applyGuideEmotion(performance.emotion);
                }
            }
        }

        shouldUsePersistentGhostCursorLookAt(stepId) {
            return /^day[1-7]_/.test(String(stepId || ''));
        }

        async syncPersistentGhostCursorLookAtForScene(stepId, runId) {
            if (this.shouldUsePersistentGhostCursorLookAt(stepId)) {
                this.adoptPreTakeoverGhostCursorLookAtHandle();
                return this.ensurePersistentGhostCursorLookAtPerformance({
                    isCancelled: () => this.isStopping()
                });
            }
            const stopReason = stepId === 'takeover_return_control'
                ? 'handoff'
                : 'scene_follow_not_required';
            if (this.preTakeoverGhostCursorLookAtHandle) {
                await this.stopIntroVoiceCursorLookAtPerformance(
                    this.preTakeoverGhostCursorLookAtHandle,
                    stopReason
                );
            }
            await this.stopPersistentGhostCursorLookAtPerformance(
                stopReason
            );
            return null;
        }
    }

    namespace.YuiGuideDirector = YuiGuideDirector;
})(window.__YuiGuideDirector);
