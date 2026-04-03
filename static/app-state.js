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
        assistantTurnSeq: 0,
        assistantSpeechActiveTurnId: null,

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
        isProactiveChatRunning: false,
        _proactiveSchedulerInitialized: false,
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
        subtitleCheckDebounceTimer: null,
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
