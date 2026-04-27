/**
 * app-state.js — 共享状态对象 & 常量
 * 所有 app-*.js 模块通过 window.appState (S) 和 window.appConst (C) 访问
 */
(function () {
    'use strict';

    // ======================== 常量 ========================
    window.appConst = Object.freeze({
        HEARTBEAT_INTERVAL: 30000,           // WebSocket 心跳间隔 (ms)
        DEFAULT_MIC_GAIN_DB: 0,              // 麦克风增益默认值 (dB)
        MAX_MIC_GAIN_DB: 25,                 // 麦克风增益上限 (dB ≈ 18x)
        MIN_MIC_GAIN_DB: -5,                 // 麦克风增益下限 (dB ≈ 0.56x)
        DEFAULT_SPEAKER_VOLUME: 100,         // 扬声器默认音量
        DEFAULT_SPATIAL_AUDIO_ENABLED: true, // 空间音频默认开启
        SPATIAL_AUDIO_MIN_GAIN: 0.4,         // 副屏远端最低音量保底（防止猫娘飞远后听不见）
        SPATIAL_AUDIO_FALLOFF_RATE: 0.35,    // 超出主屏后每个 refDist 衰减比例
        SPATIAL_AUDIO_RAMP_SECONDS: 0.12,    // pan/gain 平滑过渡时长，避免突变 click
        SPATIAL_AUDIO_POLL_MS: 500,          // 位置轮询周期（兜底，事件驱动为主）
        DEFAULT_PROACTIVE_CHAT_INTERVAL: 15, // 默认搭话间隔 (秒)
        DEFAULT_PROACTIVE_VISION_INTERVAL: 10, // 默认视觉间隔 (秒)
        MAX_SCREENSHOT_WIDTH: 1280,
        MAX_SCREENSHOT_HEIGHT: 720,
        VOICE_TRANSCRIPT_MERGE_WINDOW: 3000, // 语音转录合并时间窗 (ms)
        SCREEN_IDLE_TIMEOUT: 5 * 60 * 1000, // 屏幕流闲置超时 (ms)
        SCREEN_CHECK_INTERVAL: 60 * 1000,    // 屏幕流检查间隔 (ms)
    });

    // ======================== 共享状态 ========================
    const S = {
        // --- DOM 元素引用 (init 时填充) ---
        dom: {},

        // --- Audio (播放) ---
        audioPlayerContext: null,
        globalAnalyser: null,
        speakerGainNode: null,
        audioBufferQueue: [],
        scheduledSources: [],
        isPlaying: false,
        scheduleAudioChunksRunning: false,
        audioStartTime: 0,
        nextChunkTime: 0,
        lipSyncActive: false,
        animationFrameId: null,
        seqCounter: 0,
        speakerVolume: 100,

        // --- Audio (空间音频，多屏立体声 + 距离衰减) ---
        spatialAudioEnabled: true,
        spatialPannerNode: null,         // StereoPannerNode：水平 L/R 定位
        spatialDistanceGainNode: null,   // GainNode：距离衰减
        spatialPollTimer: null,          // 位置轮询 timer 句柄
        spatialPrimaryDisplay: null,     // 缓存的主屏信息 { bounds, workArea }

        // --- Audio (打断/解码) ---
        interruptedSpeechId: null,
        currentPlayingSpeechId: null,
        pendingDecoderReset: false,
        skipNextAudioBlob: false,
        incomingAudioBlobQueue: [],
        pendingAudioChunkMetaQueue: [],
        incomingAudioEpoch: 0,
        isProcessingIncomingAudioBlob: false,
        decoderResetPromise: null,

        // --- Audio (录音/麦克风) ---
        audioContext: null,
        workletNode: null,
        stream: null,
        micGainNode: null,
        inputAnalyser: null,
        selectedMicrophoneId: null,
        microphoneGainDb: 0,
        noiseReductionEnabled: true,
        micVolumeAnimationId: null,
        silenceDetectionTimer: null,
        hasSoundDetected: false,
        isMicMuted: false,

        // --- 会话 / WebSocket ---
        socket: null,
        heartbeatInterval: null,
        autoReconnectTimeoutId: null,
        isRecording: false,
        isTextSessionActive: false,
        isSwitchingMode: false,
        sessionStartedResolver: null,
        sessionStartedRejecter: null,
        assistantTurnId: null,
        assistantTurnStartedAt: 0,
        assistantPendingTurnServerId: null,
        assistantTurnAwaitingBubble: false,
        assistantTurnSeq: 0,
        assistantTurnCompletedId: null,
        assistantTurnCompletionSource: null,
        assistantSpeechActiveTurnId: null,
        assistantSpeechStartedTurnId: null,
        // 最近一次本地麦克风 RMS 超过语音阈值的时间戳（ms epoch）。
        // 由 app-audio-capture.js 里的 monitorInputVolume 持续写入；
        // app-proactive.js 在 voice 模式 tick 时用它判断"用户最近是否在发声"，
        // 与后端 _user_recent_activity_time 形成对称防线。
        userRecentSpeechTime: 0,

        // --- 屏幕共享 ---
        screenCaptureStream: null,
        screenCaptureStreamLastUsed: null,
        screenCaptureStreamIdleTimer: null,
        screenCaptureAutoPromptFailed: false,
        screenRecordingPermissionHintShown: false,
        selectedScreenSourceId: null,
        videoTrack: null,
        videoSenderInterval: null,

        // --- 主动搭话 ---
        proactiveChatEnabled: false,
        proactiveVisionEnabled: false,
        proactiveVisionChatEnabled: true,
        proactiveNewsChatEnabled: false,
        proactiveVideoChatEnabled: true,
        proactivePersonalChatEnabled: false,
        proactiveMusicEnabled: true,
        proactiveMemeEnabled: true,
        mergeMessagesEnabled: false,
        proactiveChatTimer: null,
        proactiveChatBackoffLevel: 0,
        _voiceProactiveNoResponseCount: 0,
        _voiceSessionInitialTimer: null,
        isProactiveChatRunning: false,
        _proactiveSchedulerInitialized: false,
        _proactiveStartupDelayApplied: false,
        proactiveChatInterval: 15,
        proactiveVisionFrameTimer: null,
        proactiveVisionInterval: 10,
        _lastProactiveChatScreenTime: 0,

        // --- 角色切换 ---
        isSwitchingCatgirl: false,

        // --- UI / 杂项 ---
        focusModeEnabled: false,
        avatarReactionBubbleEnabled: true,
        renderQuality: 'medium',
        targetFrameRate: 60,
        screenshotCounter: 0,
        statusToastTimeout: null,
        _statusToastPriority: 0,
        lastVoiceUserMessage: null,
        lastVoiceUserMessageTime: 0,

        // --- Agent ---
        agentMasterCheckbox: null,
        agentStateMachine: null,
    };

    window.appState = S;

    // ======================== 工具函数 ========================
    /** 分贝转线性增益 */
    function dbToLinear(db) {
        return Math.pow(10, db / 20);
    }
    /** 线性增益转分贝 */
    function linearToDb(linear) {
        return 20 * Math.log10(linear);
    }
    /** 画质 → 鼠标追踪性能等级映射 */
    function mapRenderQualityToFollowPerf(quality) {
        return quality === 'high' ? 'medium' : 'low';
    }
    /** 移动端检测 */
    function isMobile() {
        return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
    }

    window.appUtils = { dbToLinear, linearToDb, mapRenderQualityToFollowPerf, isMobile };

    // ======================== 向后兼容的全局双向绑定 ========================
    // 使用 defineProperty 使 window.xxx 始终和 S.xxx 同步
    const proactiveKeys = [
        'proactiveChatEnabled', 'proactiveVisionEnabled', 'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled', 'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled',
        'proactiveMusicEnabled', 'proactiveMemeEnabled', 'mergeMessagesEnabled', 'focusModeEnabled',
        'proactiveChatInterval', 'proactiveVisionInterval', 'avatarReactionBubbleEnabled',
        'renderQuality', 'targetFrameRate', 'isRecording',
    ];

    proactiveKeys.forEach(function (key) {
        // 先删除已有的简单赋值（如 window.proactiveChatEnabled = false）
        // 再用 getter/setter 桥接
        try { delete window[key]; } catch (_) { /* noop */ }
        Object.defineProperty(window, key, {
            get: function () { return S[key]; },
            set: function (v) { S[key] = v; },
            configurable: true,
            enumerable: true,
        });
    });

    // cursorFollowPerformanceLevel 由 renderQuality 派生
    Object.defineProperty(window, 'cursorFollowPerformanceLevel', {
        get: function () { return mapRenderQualityToFollowPerf(S.renderQuality); },
        set: function () { /* ignore — derived from renderQuality */ },
        configurable: true,
        enumerable: true,
    });

    // 音频全局同步辅助
    window.syncAudioGlobals = function () {
        window.audioPlayerContext = S.audioPlayerContext;
        window.globalAnalyser = S.globalAnalyser;
    };

    // 初始同步
    window.syncAudioGlobals();
})();
