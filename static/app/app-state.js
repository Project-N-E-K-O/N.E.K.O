/**
 * app-state.js — 共享状态对象 & 常量
 * 所有 app-*.js 模块通过 window.appState (S) 和 window.appConst (C) 访问
 */
(function () {
    'use strict';

    function isDesktopLinuxX11Runtime() {
        return !!(window.__NEKO_DESKTOP_RUNTIME__ && window.__NEKO_DESKTOP_RUNTIME__.isLinuxX11);
    }

    const DEFAULT_RENDER_QUALITY = isDesktopLinuxX11Runtime() ? 'low' : 'medium';

    // ======================== 常量 ========================
    window.appConst = Object.freeze({
        HEARTBEAT_INTERVAL: 30000,           // WebSocket 心跳间隔 (ms)
        DEFAULT_MIC_GAIN_DB: 0,              // 麦克风增益默认值 (dB)
        MAX_MIC_GAIN_DB: 25,                 // 麦克风增益上限 (dB ≈ 18x)
        MIN_MIC_GAIN_DB: -5,                 // 麦克风增益下限 (dB ≈ 0.56x)
        DEFAULT_SPEAKER_VOLUME: 100,         // 扬声器默认音量
        MAX_SPEAKER_VOLUME: 200,             // 扬声器音量上限（200% ≈ +6 dB 增益）
        SPEAKER_VOLUME_KNEE_RATIO: 0.75,     // 100% 锚点落在轨道 75% 处：前 3/4 给 0-100%，后 1/4 给 100-200% 增强区
        DEFAULT_SPATIAL_AUDIO_ENABLED: true, // 空间音频默认开启
        SPATIAL_AUDIO_MIN_GAIN: 0.4,         // 副屏远端最低音量保底（防止猫娘飞远后听不见）
        SPATIAL_AUDIO_MAX_PAN: 0.6,          // pan 绝对值上限（防止完全单声道，另一边留 ~31% 信号）
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
        scheduleAudioChunksTimer: null,
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
        independentAsrEnabled: false,
        independentAsrActive: false,
        independentAsrProvider: '',
        micVolumeAnimationId: null,
        silenceDetectionTimer: null,
        hasSoundDetected: false,
        isMicMuted: false,
        gameRouteActive: false,
        gameRouteGameType: '',
        gameRouteLanlanName: '',
        gameRouteSessionId: '',
        gameVoiceSttGateActive: false,
        gameVoiceSttGameType: '',
        gameVoiceSttSessionId: '',
        gameVoiceSttRecognition: null,
        gameVoiceSttListening: false,
        gameVoiceSttStopping: false,
        gameVoiceSttRestartTimer: null,
        gameVoiceSttUnsupportedNotified: false,
        proactiveChatWasStoppedByGameRoute: false,

        // --- 会话 / WebSocket ---
        socket: null,
        heartbeatInterval: null,
        autoReconnectTimeoutId: null,
        isRecording: false,
        voiceChatActive: false,
        voiceStartPending: false,
        isTextSessionActive: false,
        suppressAssistantStreamUntilNextSession: false,
        isSwitchingMode: false,
        sessionStartedResolver: null,
        sessionStartedRejecter: null,
        // 本次正在 await session_started 的启动请求模式（'audio' / 'text'）。
        // session_started 处理用它校验到达的 input_mode 是否与用户请求的一致：
        // 不一致（典型是 proactive/greeting 并发自起的 text 会话发来的 ack）时
        // 忽略，避免错误模式的 ack 收口用户的启动 promise / 翻转会话状态。
        _pendingSessionStartMode: null,
        voiceSessionStartEpoch: 0,
        assistantTurnId: null,
        assistantTurnStartedAt: 0,
        assistantPendingTurnServerId: null,
        assistantTurnAwaitingBubble: false,
        // 文本会话刚把 WS payload 发出去（text 和/或 screenshot），但 gemini_response
        // 还没回第一个 chunk 的那段空窗。用 ms 时间戳 + 15s 上限自我兜底，避免
        // 错过 clear 时永远卡 true。专门给 isAssistantTextResponseInFlight()
        // 用（_lastSubmittedRequestId 对纯截图请求会被故意清空，挡不住这段空窗）。
        pendingTextTurnSubmitAt: 0,
        assistantTurnSeq: 0,
        assistantTurnCompletedId: null,
        // 一轮干净收尾后（maybeFinalizeAssistantSpeech 成功），completedId 会被
        // clearAssistantTurnCompletion 清成 null，但 assistantTurnId 要等下条用户
        // 消息才清。没有这个 settled 标记的话，isAssistantTextResponseInFlight 的
        // turnMismatch（turnId !== completedId）在每条语音回复收尾后都恒为 true，
        // 切语音会干等满 15s。settledId 记下"这轮已收尾"，turn-start/cancel 时清。
        assistantTurnSettledId: null,
        assistantTurnCompletionSource: null,
        assistantSpeechActiveTurnId: null,
        assistantSpeechStartedTurnId: null,
        assistantSpeechPlaybackTurnId: null,
        assistantSpeechPlaybackStartAudioTime: 0,
        assistantSpeechPlaybackEndAudioTime: 0,
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
        proactiveChatEnabled: true,
        proactiveVisionEnabled: false,
        proactiveVisionChatEnabled: true,
        proactiveNewsChatEnabled: false,
        proactiveVideoChatEnabled: true,
        proactivePersonalChatEnabled: false,
        proactiveMusicEnabled: true,
        proactiveMemeEnabled: true,
        proactiveMiniGameInviteEnabled: true,
        mergeMessagesEnabled: false,
        proactiveChatTimer: null,
        proactiveChatBackoffLevel: 0,
        // 屏幕专注态（gaming / focused_work，后端 propensity=restricted_screen_only）
        // 切到「固定间隔 + 后端抖动」调度：跳过 3-tier 退避，按 baseInterval
        // 等间隔触发，后端 /proactive_chat 入口注入 [0, 0.5×base] 的 sleep
        // 把实际间隔抹成 [base, 1.5×base] 均匀分布。由 /proactive_chat 响应里的
        // next_schedule_fixed_mode 字段控制开关；默认 false（即走常规退避）。
        proactiveFixedScheduleMode: false,
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
        // 凝神（cognition focus）per-user 总开关，默认开；关掉后端进不了 focus 态。
        // 注意与上面的 focusModeEnabled（=麦克风静音/允许打断）是两回事。
        focusCognitionEnabled: true,
        avatarReactionBubbleEnabled: true,
        // 自然表达（slop reduction）总开关，默认开。promptOnly：仅改喂回模型的
        // 历史副本，用户看到的原文与持久化历史都不动（后端 utils/slop_filter.py）。
        slopFilterEnabled: true,
        renderQuality: DEFAULT_RENDER_QUALITY,
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

    window.isNekoGoodbyeModeActive = function () {
        return !!(
            (window.live2dManager && window.live2dManager._goodbyeClicked)
            || (window.vrmManager && window.vrmManager._goodbyeClicked)
            || (window.mmdManager && window.mmdManager._goodbyeClicked)
        );
    };

    window.makeNekoSessionAbortError = function (reason) {
        var error = new Error(reason || 'Session aborted');
        error.sessionStartCancelled = true;
        error.voiceStartCancelled = true;
        return error;
    };

    window.cancelPendingSessionStart = function (reason) {
        if (window.sessionTimeoutId) {
            clearTimeout(window.sessionTimeoutId);
            window.sessionTimeoutId = null;
        }
        S.voiceSessionStartEpoch += 1;
        S.voiceStartPending = false;
        window.isMicStarting = false;

        if (S.sessionStartedRejecter) {
            try {
                S.sessionStartedRejecter(window.makeNekoSessionAbortError(reason));
            } catch (_) { }
        }
        S.sessionStartedResolver = null;
        S.sessionStartedRejecter = null;
        S._pendingSessionStartMode = null;
    };

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
    /**
     * 带膝点的非线性滑块：轨道位置(0..1) → 数值。
     * knee 比例处映射到 base（标准锚点），右端到 max（增强区），左段与右段各自线性。
     */
    function kneeTrackToValue(pos, base, max, knee) {
        if (pos <= knee) return knee > 0 ? (pos / knee) * base : base;
        // knee >= 1 时无右段（增强区），整条轨道都是 [0, base]，膝点即终点
        return knee < 1 ? base + ((pos - knee) / (1 - knee)) * (max - base) : max;
    }
    /** kneeTrackToValue 的逆映射：数值 → 轨道位置(0..1)。 */
    function valueToKneeTrack(value, base, max, knee) {
        if (value <= base) return base > 0 ? (value / base) * knee : 0;
        // max <= base 时无增强区，超过 base 的值一律钉在轨道末端
        return max > base ? knee + ((value - base) / (max - base)) * (1 - knee) : 1;
    }

    window.appUtils = { dbToLinear, linearToDb, mapRenderQualityToFollowPerf, isMobile, kneeTrackToValue, valueToKneeTrack };

    // ======================== 向后兼容的全局双向绑定 ========================
    // 使用 defineProperty 使 window.xxx 始终和 S.xxx 同步
    const proactiveKeys = [
        'proactiveChatEnabled', 'proactiveVisionEnabled', 'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled', 'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled',
        'proactiveMusicEnabled', 'proactiveMemeEnabled', 'proactiveMiniGameInviteEnabled',
        'mergeMessagesEnabled', 'focusModeEnabled', 'focusCognitionEnabled',
        'proactiveChatInterval', 'proactiveVisionInterval', 'avatarReactionBubbleEnabled',
        'slopFilterEnabled',
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
