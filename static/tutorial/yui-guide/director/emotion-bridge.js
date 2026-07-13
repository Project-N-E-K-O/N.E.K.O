(function (namespace) {
    'use strict';

    const {
        YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY,
        YUI_GUIDE_CHAT_BRIDGE_QUEUE_LIMIT,
        TutorialVisualControllers,
        TutorialResistanceControllers,
        ResistanceController,
        SidebarPauseController,
        PauseCoordinator,
        TutorialTerminationRouter,
        TutorialOperationRegistry,
        OperationRegistry,
        TutorialSceneOrchestrator,
        TutorialSettingsTourFlow,
        createYuiGuideChatBridgeCommandBus,
        createYuiGuideTargetGeometryRegistry,
        createYuiGuideChatWindowAdapter,
        createYuiGuideScopedTutorialResources,
        readYuiGuideChatBridgeQueue,
        enqueueYuiGuideChatBridgeMessage,
        postYuiGuideChatBridgeMessage,
        translateGuideText,
        normalizeGuideLocale,
        resolveGuidePreferredLanguage,
        isGuideI18nReady,
        waitForGuideI18nReady,
        resolveGuideLocale,
        guideSpeechLang,
        resolveGuideAudioLocale,
        AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY,
        YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY,
        readAvatarFloatingGuideUsageState,
        writeAvatarFloatingGuideUsageState,
        normalizeAvatarFloatingGuideUsageTimestamp,
        getAvatarFloatingGuideActiveRound,
        recordAvatarFloatingGuideRoundStart,
        recordAvatarFloatingGuideRoundEnd,
        markAvatarFloatingGuideUsage,
        hasAvatarFloatingGuideUsage,
        hasAvatarFloatingGuideVoiceUsedAfterRoundStart,
        hasAvatarFloatingGuideVoiceUsedAfterDay1EndBeforeRoundStart,
        DEFAULT_USER_CURSOR_REVEAL_DISTANCE,
        DEFAULT_USER_CURSOR_REVEAL_INTERVAL_MS,
        DEFAULT_USER_CURSOR_REVEAL_MOVES,
        DEFAULT_INTERRUPT_COUNT_CURSOR_REVEAL_MS,
        DEFAULT_STEP_DELAY_MS,
        DEFAULT_SCENE_SETTLE_MS,
        DEFAULT_CURSOR_DURATION_MS,
        DEFAULT_CURSOR_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_AGENT_PANEL_CURSOR_MOVE_MS,
        DAY6_PLUGIN_AGENT_PANEL_CURSOR_START_DELAY_MS,
        DAY6_PLUGIN_AGENT_PANEL_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_CAT_PAW_CURSOR_OFFSET_Y,
        DAY6_PLUGIN_SIDE_PANEL_CURSOR_MOVE_MS,
        DAY6_PLUGIN_SIDE_PANEL_CURSOR_START_DELAY_MS,
        DAY6_PLUGIN_SIDE_PANEL_CLICK_VISIBLE_MS,
        DAY6_PLUGIN_SIDE_PANEL_ACTION_TIMEOUT_MS,
        DAY6_PLUGIN_SIDE_PANEL_DASHBOARD_WAIT_MS,
        DAY6_PLUGIN_DASHBOARD_DONE_GRACE_MS,
        INTRO_GREETING_REPLY_TEXT,
        INTRO_GREETING_REPLY_TEXT_KEY,
        TAKEOVER_PLUGIN_DASHBOARD_TEXT,
        TAKEOVER_PLUGIN_DASHBOARD_TEXT_KEY,
        PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT,
        PLUGIN_DASHBOARD_POPUP_BLOCKED_TEXT_KEY,
        TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1,
        TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2,
        TAKEOVER_SETTINGS_DETAIL_TEXT,
        TAKEOVER_SETTINGS_DETAIL_TEXT_KEY,
        TAKEOVER_SETTINGS_DETAIL_TEXT_PART_1_KEY,
        TAKEOVER_SETTINGS_DETAIL_TEXT_PART_2_KEY,
        INTRO_ACTIVATION_HINT_KEY,
        INTRO_ACTIVATION_HINT,
        INTRO_ACTIVATION_AUTO_ADVANCE_MS,
        INTRO_ACTIVATION_REDUCED_MOTION_AUTO_ADVANCE_MS,
        DEFAULT_SPOTLIGHT_PADDING,
        PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_X,
        PLUGIN_MANAGEMENT_ENTRY_SPOTLIGHT_EXTRA_Y,
        NARRATION_RESUME_BACKTRACK_MS,
        NARRATION_RESUME_MIN_REMAINING_MS,
        PLUGIN_DASHBOARD_WINDOW_NAME,
        PLUGIN_DASHBOARD_HANDOFF_EVENT,
        PLUGIN_DASHBOARD_READY_EVENT,
        PLUGIN_DASHBOARD_DONE_EVENT,
        PLUGIN_DASHBOARD_TERMINATE_EVENT,
        PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
        PLUGIN_DASHBOARD_INTERRUPT_REQUEST_EVENT,
        PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
        PLUGIN_DASHBOARD_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_INTERRUPT_ACK_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_NARRATION_FINISHED_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_SYSTEM_CURSOR_TEMPORARY_REVEAL_EVENT,
        DESKTOP_PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT,
        PLUGIN_DASHBOARD_SKIP_REQUEST_EVENT,
        DEFAULT_TUTORIAL_MODEL_MANAGER_LANLAN_NAME,
        GUIDE_AUDIO_BASE_URL,
        RETURN_PETAL_SEQUENCE_URL,
        getYuiGuideDailyGuide,
        collectGuideAudioFilesByKey,
        DAY1_HOME_GUIDE,
        GUIDE_AUDIO_FILES_BY_KEY,
        GUIDE_AUDIO_FILE_OVERRIDES_BY_KEY,
        GUIDE_AUDIO_VERSION_BY_KEY,
        guideAudioSrc,
        shouldGuideAudioDriveMouth,
        TAKEOVER_CAPTURE_SELECTORS,
        AVATAR_FLOATING_GUIDE_INTERRUPT_STEP,
        wait,
        fetchWithTimeout,
        resolveWithTimeout,
        clamp,
        DAY4_LOCK_SPOTLIGHT_SAFE_BOTTOM_PX,
        HOME_TUTORIAL_PLATFORM_PROFILES,
        detectHomeTutorialPlatform,
        createHomeTutorialPlatformCapabilities,
        HOME_TUTORIAL_PLATFORM_CAPABILITIES_API,
        HOME_TUTORIAL_EXPERIENCE_METRICS_STORAGE_KEY,
        HOME_TUTORIAL_EXPERIENCE_METRICS_LIMIT,
        readHomeTutorialExperienceMetrics,
        writeHomeTutorialExperienceMetrics,
        createHomeTutorialExperienceMetrics,
        GUIDE_NARRATION_TIMELINES_BY_KEY,
        GUIDE_AUDIO_DURATIONS_BY_KEY,
        getGuideAudioCueConfig,
        getGuideAudioDurationConfig,
        formatGuideDebugText,
        unionRects,
        estimateSpeechDurationMs,
        estimateGuideChatStreamDurationMs,
        normalizeVoiceLang,
        scoreSpeechVoice
    } = namespace;

    class YuiGuideEmotionBridge {
        constructor() {
            this.live2dApplySequence = Promise.resolve();
            this.live2dExpressionSequence = Promise.resolve();
            this.pendingLive2DEmotion = '';
            this.pendingLive2DExpressionFile = '';
            this.activeLive2DExpressionFile = '';
        }

        normalizeModelType(modelType) {
            const normalizedType = String(modelType || '').toLowerCase();
            if (normalizedType === 'vrm' || normalizedType === 'mmd') {
                return normalizedType;
            }
            if (normalizedType === 'live2d') {
                return 'live2d';
            }
            return '';
        }

        getStoredValue(key) {
            try {
                return (
                    (window.sessionStorage && window.sessionStorage.getItem(key))
                    || (window.localStorage && window.localStorage.getItem(key))
                    || ''
                );
            } catch (_) {
                return '';
            }
        }

        resolveStoredModelType() {
            const modelType = String(this.getStoredValue('modelType') || '').toLowerCase();
            if (modelType === 'live3d') {
                const subType = String(
                    this.getStoredValue('live3dSubType') || this.getStoredValue('live3d_sub_type')
                ).toLowerCase();
                if (subType === 'mmd' || subType === 'vrm') {
                    return subType;
                }
                return 'vrm';
            }
            return this.normalizeModelType(modelType);
        }

        getActiveModelType() {
            const runtimeType = this.normalizeModelType(
                typeof window.getActiveModelType === 'function' ? window.getActiveModelType() : ''
            );
            if (runtimeType) {
                return runtimeType;
            }

            const cfg = window.lanlan_config;
            if (cfg) {
                const modelType = String(cfg.model_type || '').toLowerCase();
                if (modelType === 'live3d') {
                    const subType = String(cfg.live3d_sub_type || '').toLowerCase();
                    if (subType === 'mmd' || subType === 'vrm') {
                        return subType;
                    }
                    return 'live2d';
                }

                if (modelType === 'vrm' || modelType === 'mmd') {
                    return modelType;
                }
                return 'live2d';
            }

            const storedType = this.resolveStoredModelType();
            if (storedType) {
                return storedType;
            }
            return 'live2d';
        }

        handleAsyncFailure(result, ...warningArgs) {
            if (result && typeof result.catch === 'function') {
                result.catch((error) => {
                    console.warn(...warningArgs, error);
                });
            }
        }

        async waitForLive2DMotionTail(manager, timeoutMs) {
            const maxWaitMs = Math.max(0, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 0));
            if (!manager || typeof manager.hasActiveMotionPlayback !== 'function' || maxWaitMs <= 0) {
                return;
            }

            const startedAt = Date.now();
            while ((Date.now() - startedAt) < maxWaitMs) {
                if (!manager.currentModel) {
                    return;
                }
                if (!manager.hasActiveMotionPlayback()) {
                    return;
                }
                await new Promise((resolve) => window.setTimeout(resolve, 48));
            }
        }

        async waitForLive2DMotionCompletion(manager, timeoutMs) {
            const maxWaitMs = Math.max(0, Math.round(Number.isFinite(timeoutMs) ? timeoutMs : 0));
            if (!manager || typeof manager.hasActiveMotionPlayback !== 'function' || maxWaitMs <= 0) {
                return;
            }

            const startedAt = Date.now();
            while ((Date.now() - startedAt) < maxWaitMs) {
                if (!manager.currentModel) {
                    return;
                }
                if (!manager.hasActiveMotionPlayback()) {
                    return;
                }
                await new Promise((resolve) => window.setTimeout(resolve, 48));
            }
        }

        queueLive2DEmotionApply(emotion) {
            const normalizedEmotion = typeof emotion === 'string' ? emotion.trim() : '';
            if (!normalizedEmotion) {
                return Promise.resolve();
            }

            this.pendingLive2DEmotion = normalizedEmotion;
            const run = async () => {
                const targetEmotion = this.pendingLive2DEmotion;
                const manager = window.live2dManager;
                if (!manager || !manager.currentModel) {
                    return;
                }

                if (this.activeLive2DExpressionFile) {
                    this.clearLive2DGuideExpression(manager);
                }

                await this.waitForLive2DMotionCompletion(manager, 2200);
                if (this.pendingLive2DEmotion !== targetEmotion) {
                    return;
                }

                if (typeof manager.setEmotion === 'function') {
                    await manager.setEmotion(targetEmotion);
                    return;
                }
                if (typeof manager.playMotion === 'function') {
                    await manager.playMotion(targetEmotion);
                }
            };

            this.live2dApplySequence = this.live2dApplySequence
                .catch(() => {})
                .then(run);
            return this.live2dApplySequence;
        }

        getActiveGuideExpressionFile() {
            return this.activeLive2DExpressionFile || '';
        }

        clearLive2DGuideExpression(managerOverride) {
            const manager = managerOverride || window.live2dManager;
            this.pendingLive2DExpressionFile = '';
            this.activeLive2DExpressionFile = '';

            if (!manager) {
                return false;
            }

            let handled = false;
            if (typeof manager._removeManualExpressionOverride === 'function') {
                try {
                    manager._removeManualExpressionOverride();
                    handled = true;
                } catch (error) {
                    console.warn('[YuiGuide] 清理教程临时表情失败:', error);
                }
            }

            if (Object.prototype.hasOwnProperty.call(manager, '_activeExpressionParamIds')) {
                manager._activeExpressionParamIds = null;
                handled = true;
            }

            return handled;
        }

        buildLive2DExpressionCandidates(manager, expressionFile) {
            const normalizedExpressionFile = typeof expressionFile === 'string'
                ? expressionFile.trim()
                : '';
            if (!manager || !normalizedExpressionFile) {
                return [];
            }

            const candidateFiles = [];
            const pushCandidate = (filePath) => {
                if (!filePath || typeof filePath !== 'string') {
                    return;
                }
                const normalizedPath = filePath.replace(/\\/g, '/').trim();
                if (normalizedPath && !candidateFiles.includes(normalizedPath)) {
                    candidateFiles.push(normalizedPath);
                }
            };

            pushCandidate(normalizedExpressionFile);
            const resolvedRef = typeof manager.resolveExpressionReferenceByFile === 'function'
                ? manager.resolveExpressionReferenceByFile(normalizedExpressionFile)
                : null;
            if (resolvedRef && resolvedRef.file) {
                pushCandidate(resolvedRef.file);
            }

            const baseName = normalizedExpressionFile.split('/').pop() || '';
            if (baseName) {
                pushCandidate(baseName);
                pushCandidate('expressions/' + baseName);
            }

            return candidateFiles;
        }

        async loadLive2DExpressionData(manager, expressionFile) {
            const candidateFiles = this.buildLive2DExpressionCandidates(manager, expressionFile);
            if (candidateFiles.length === 0) {
                return null;
            }

            let lastFetchError = null;
            for (const candidateFile of candidateFiles) {
                try {
                    const response = await fetch(manager.resolveAssetPath(candidateFile));
                    if (!response.ok) {
                        lastFetchError = new Error('Failed to load expression: ' + response.statusText);
                        continue;
                    }

                    return {
                        expressionData: await response.json(),
                        loadedExpressionFile: candidateFile
                    };
                } catch (error) {
                    lastFetchError = error;
                }
            }

            if (typeof manager.markExpressionFileMissing === 'function') {
                candidateFiles.forEach((candidateFile) => {
                    manager.markExpressionFileMissing(candidateFile);
                });
            }

            if (lastFetchError) {
                throw lastFetchError;
            }
            return null;
        }

        queueLive2DExpressionApply(expressionFile, options) {
            const normalizedExpressionFile = typeof expressionFile === 'string'
                ? expressionFile.trim()
                : '';
            if (!normalizedExpressionFile) {
                return Promise.resolve(false);
            }

            const normalizedOptions = options || {};
            const fadeInMs = Math.max(
                60,
                Math.min(
                    1600,
                    Math.round(Number.isFinite(normalizedOptions.fadeInMs) ? normalizedOptions.fadeInMs : 220)
                )
            );
            this.pendingLive2DExpressionFile = normalizedExpressionFile;

            const previousEmotionSequence = this.live2dApplySequence;
            const previousExpressionSequence = this.live2dExpressionSequence;
            const run = async () => {
                const targetExpressionFile = this.pendingLive2DExpressionFile;
                const manager = window.live2dManager;
                if (!manager || !manager.currentModel || targetExpressionFile !== normalizedExpressionFile) {
                    return false;
                }

                const loadedExpression = await this.loadLive2DExpressionData(manager, targetExpressionFile);
                if (!loadedExpression || this.pendingLive2DExpressionFile !== targetExpressionFile) {
                    return false;
                }

                const expressionParams = Array.isArray(loadedExpression.expressionData && loadedExpression.expressionData.Parameters)
                    ? loadedExpression.expressionData.Parameters
                    : [];
                if (expressionParams.length === 0 || typeof manager._installManualExpressionOverride !== 'function') {
                    return false;
                }

                manager._activeExpressionParamIds = new Set(
                    expressionParams
                        .map((param) => param && param.Id)
                        .filter(Boolean)
                );
                manager._installManualExpressionOverride(expressionParams, fadeInMs);
                this.activeLive2DExpressionFile = loadedExpression.loadedExpressionFile;
                return true;
            };

            this.live2dExpressionSequence = Promise.all([
                previousEmotionSequence.catch(() => {}),
                previousExpressionSequence.catch(() => {})
            ]).then(run);
            return this.live2dExpressionSequence;
        }

        applyExpressionFile(expressionFile, options) {
            const activeModelType = this.getActiveModelType();
            if (activeModelType !== 'live2d') {
                return;
            }

            if (!window.live2dManager || !window.live2dManager.currentModel) {
                return;
            }

            try {
                const applyPromise = this.queueLive2DExpressionApply(expressionFile, options);
                this.handleAsyncFailure(applyPromise, '[YuiGuide] 播放教程临时表情失败:', expressionFile);
            } catch (error) {
                console.warn('[YuiGuide] 播放教程临时表情失败:', expressionFile, error);
            }
        }

        apply(emotion) {
            if (!emotion) {
                return;
            }

            const activeModelType = this.getActiveModelType();
            if (activeModelType === 'live2d') {
                if (!window.live2dManager || !window.live2dManager.currentModel) {
                    return;
                }

                try {
                    const applyPromise = this.queueLive2DEmotionApply(emotion);
                    this.handleAsyncFailure(applyPromise, '[YuiGuide] 播放教程动作失败:', emotion);
                } catch (error) {
                    console.warn('[YuiGuide] 播放教程动作失败:', emotion, error);
                }
                return;
            }

            try {
                if (activeModelType === 'mmd') {
                    if (window.mmdManager && typeof window.mmdManager.setEmotion === 'function') {
                        window.mmdManager.setEmotion(emotion);
                    } else if (
                        window.mmdManager
                        && window.mmdManager.expression
                        && typeof window.mmdManager.expression.setEmotion === 'function'
                    ) {
                        window.mmdManager.expression.setEmotion(emotion);
                    }
                    return;
                }

                if (activeModelType === 'vrm') {
                    if (window.vrmManager && typeof window.vrmManager.setEmotion === 'function') {
                        window.vrmManager.setEmotion(emotion);
                    } else if (
                        window.vrmManager
                        && window.vrmManager.expression
                        && typeof window.vrmManager.expression.setMood === 'function'
                    ) {
                        window.vrmManager.expression.setMood(emotion);
                    }
                    return;
                }
            } catch (error) {
                console.warn('[YuiGuide] 设置教程情绪失败:', emotion, error);
            }
        }

        clearLive2DGuidePresentation() {
            const manager = window.live2dManager;
            if (!manager) {
                return false;
            }

            let handled = this.clearLive2DGuideExpression(manager);

            if (typeof manager.softClearEmotionEffects === 'function') {
                this.handleAsyncFailure(
                    manager.softClearEmotionEffects({
                        preserveExpression: true
                    }),
                    '[YuiGuide] 平滑清理 Live2D 教程动作失败:'
                );
                handled = true;
            } else if (typeof manager.clearEmotionEffects === 'function') {
                this.handleAsyncFailure(
                    manager.clearEmotionEffects(),
                    '[YuiGuide] 清理 Live2D 教程动作失败:'
                );
                handled = true;
            }

            if (typeof manager.smoothResetToInitialState === 'function') {
                this.handleAsyncFailure(
                    manager.smoothResetToInitialState(220),
                    '[YuiGuide] 平滑清理 Live2D 表情失败:'
                );
                handled = true;
            } else if (typeof manager.clearExpression === 'function') {
                this.handleAsyncFailure(
                    manager.clearExpression(),
                    '[YuiGuide] 清理 Live2D 表情失败:'
                );
                handled = true;
            }

            return handled;
        }

        clearMmdGuidePresentation() {
            const manager = window.mmdManager;
            if (!manager) {
                return false;
            }

            if (typeof manager.setEmotion === 'function') {
                this.handleAsyncFailure(
                    manager.setEmotion('neutral'),
                    '[YuiGuide] 清理 MMD 教程情绪失败:'
                );
                return true;
            }

            const expression = manager.expression;
            if (expression && typeof expression.setEmotion === 'function') {
                this.handleAsyncFailure(
                    expression.setEmotion('neutral'),
                    '[YuiGuide] 清理 MMD 教程情绪失败:'
                );
                return true;
            }

            if (expression && typeof expression.resetAllMorphs === 'function') {
                this.handleAsyncFailure(
                    expression.resetAllMorphs(),
                    '[YuiGuide] 清理 MMD 教程 morph 失败:'
                );
                return true;
            }

            return false;
        }

        clearVrmGuidePresentation() {
            const manager = window.vrmManager;
            if (!manager) {
                return false;
            }

            if (typeof manager.setEmotion === 'function') {
                this.handleAsyncFailure(
                    manager.setEmotion('neutral'),
                    '[YuiGuide] 清理 VRM 教程情绪失败:'
                );
                return true;
            }

            const expression = manager.expression;
            if (expression && typeof expression.setMood === 'function') {
                this.handleAsyncFailure(
                    expression.setMood('neutral'),
                    '[YuiGuide] 清理 VRM 教程情绪失败:'
                );
                return true;
            }

            return false;
        }

        clearViaActiveModelType() {
            const activeModelType = this.getActiveModelType();
            if (activeModelType === 'live2d') {
                return this.clearLive2DGuidePresentation();
            }
            if (activeModelType === 'mmd') {
                return this.clearMmdGuidePresentation();
            }
            if (activeModelType === 'vrm') {
                return this.clearVrmGuidePresentation();
            }
            return false;
        }

        clearWithLegacyBridge() {
            if (window.LanLan1 && typeof window.LanLan1.clearEmotionEffects === 'function') {
                try {
                    window.LanLan1.clearEmotionEffects();
                    return true;
                } catch (error) {
                    console.warn('[YuiGuide] 清理情绪失败:', error);
                }
            }

            if (window.LanLan1 && typeof window.LanLan1.clearExpression === 'function') {
                try {
                    window.LanLan1.clearExpression();
                    return true;
                } catch (error) {
                    console.warn('[YuiGuide] 清理表情失败:', error);
                }
            }

            return false;
        }

        clear() {
            try {
                if (this.clearViaActiveModelType()) {
                    return;
                }
            } catch (error) {
                console.warn('[YuiGuide] 按模型类型清理教程情绪失败:', error);
            }

            this.clearWithLegacyBridge();
        }
    }

    namespace.YuiGuideEmotionBridge = YuiGuideEmotionBridge;
})(window.__YuiGuideDirector);
