(function (namespace) {
    'use strict';

    const {
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

    class CursorAnchorStore {
        constructor() {
            this.scenePoints = Object.create(null);
            this.latestExternalizedPoint = null;
        }

        rememberScenePoint(sceneId, point) {
            const normalizedSceneId = typeof sceneId === 'string' ? sceneId.trim() : '';
            if (
                !normalizedSceneId
                || !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return false;
            }
            this.scenePoints[normalizedSceneId] = {
                x: point.x,
                y: point.y
            };
            return true;
        }

        getScenePoint(sceneIds) {
            const candidates = Array.isArray(sceneIds) ? sceneIds : [sceneIds];
            for (let index = 0; index < candidates.length; index += 1) {
                const sceneId = typeof candidates[index] === 'string' ? candidates[index].trim() : '';
                const point = sceneId ? this.scenePoints[sceneId] : null;
                if (point && Number.isFinite(point.x) && Number.isFinite(point.y)) {
                    return {
                        x: point.x,
                        y: point.y
                    };
                }
            }
            return null;
        }

        rememberLatestExternalizedPoint(point) {
            if (
                !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return false;
            }
            this.latestExternalizedPoint = {
                x: point.x,
                y: point.y,
                at: Number(point.at) || Date.now(),
                kind: typeof point.kind === 'string' ? point.kind : '',
                effect: typeof point.effect === 'string' ? point.effect : '',
                effectDurationMs: Number.isFinite(point.effectDurationMs)
                    ? Math.max(0, Math.floor(point.effectDurationMs))
                    : 0,
                settled: point.settled === true
            };
            return true;
        }

        getLatestExternalizedPoint(maxAgeMs) {
            const point = this.latestExternalizedPoint;
            if (
                !point
                || !Number.isFinite(point.x)
                || !Number.isFinite(point.y)
            ) {
                return null;
            }
            const latestAt = Number(point.at);
            const ageLimit = Number.isFinite(maxAgeMs) ? maxAgeMs : 30000;
            if (Number.isFinite(latestAt) && Date.now() - latestAt > ageLimit) {
                return null;
            }
            return {
                x: point.x,
                y: point.y
            };
        }

        clear() {
            this.scenePoints = Object.create(null);
            this.latestExternalizedPoint = null;
        }
    }

    namespace.CursorAnchorStore = CursorAnchorStore;
})(window.__YuiGuideDirector);
