/**
 * 全局窗口管理函数
 */

// 【防崩溃兜底】确保 window.t 始终是一个可调用的函数
if (typeof window.t !== 'function') {
    window.t = function (key, fallback) {
        // 如果 fallback 是字符串，直接返回
        if (typeof fallback === 'string') return fallback;
        // 如果 fallback 是 i18next 格式的对象，且包含 defaultValue，则回退到该默认值
        if (fallback && typeof fallback === 'object' && fallback.defaultValue) {
            return fallback.defaultValue;
        }
        // 实在没办法了才返回 key
        return key;
    };
}
// 定义全局安全的翻译函数 safeT，供内部直接调用
window.safeT = function (key, fallback) {
    if (window.t && typeof window.t === 'function') {
        const translated = window.t(key, fallback);
        // 【修改】确保翻译库返回的确实是字符串，否则退回安全值
        if (typeof translated === 'string') {
            return translated;
        }
    }
    return typeof fallback === 'string' ? fallback : key;
};

// 【新增】定义一个全局或模块级的门闩变量
let currentMusicSearchEpoch = 0;

// 【新增】统一失效在途音乐搜索的工具函数
window.invalidatePendingMusicSearch = function () {
    currentMusicSearchEpoch++;
    window._pendingMusicCommand = '';
    console.log(`[Music] 搜索纪元更新至: ${currentMusicSearchEpoch}, 已失效所有在途请求`);
};

// 上次用户输入时间（毫秒级）
let lastUserInputTime = 0;
// 关闭所有已打开的设置窗口（弹窗）
window.closeAllSettingsWindows = function () {
    // 关闭 app.js 中跟踪的窗口
    if (window._openSettingsWindows) {
        Object.keys(window._openSettingsWindows).forEach(url => {
            const winRef = window._openSettingsWindows[url];
            try {
                if (winRef && !winRef.closed) {
                    winRef.close();
                }
            } catch (_) {
                // 忽略跨域导致的 close 异常
            }
            delete window._openSettingsWindows[url];
        });
    }

    // 关闭 live2d-ui-popup.js 中跟踪的窗口（如果有 Live2DManager 实例）
    if (window.live2dManager && window.live2dManager._openSettingsWindows) {
        Object.keys(window.live2dManager._openSettingsWindows).forEach(url => {
            const winRef = window.live2dManager._openSettingsWindows[url];
            try {
                if (winRef && !winRef.closed) {
                    winRef.close();
                }
            } catch (_) {
                // 忽略跨域导致的 close 异常
            }
            delete window.live2dManager._openSettingsWindows[url];
        });
    }
};

// 应用初始化
function init_app() {
    const micButton = document.getElementById('micButton');
    const muteButton = document.getElementById('muteButton');
    const screenButton = document.getElementById('screenButton');
    const stopButton = document.getElementById('stopButton');
    const resetSessionButton = document.getElementById('resetSessionButton');
    const returnSessionButton = document.getElementById('returnSessionButton');
    const statusElement = document.getElementById('status');
    const statusToast = document.getElementById('status-toast');

    // Status 气泡框显示函数
    let statusToastTimeout = null;
    function showStatusToast(message, duration = 3000) {
        console.log(window.t('console.statusToastShow'), message, window.t('console.statusToastDuration'), duration);

        if (!message || message.trim() === '') {
            // 如果消息为空，隐藏气泡框
            if (statusToast) {
                statusToast.classList.remove('show');
                statusToast.classList.add('hide');
                setTimeout(() => {
                    statusToast.textContent = '';
                }, 300);
            }
            return;
        }

        if (!statusToast) {
            console.error(window.t('console.statusToastNotFound'));
            return;
        }

        // 清除之前的定时器
        if (statusToastTimeout) {
            clearTimeout(statusToastTimeout);
            statusToastTimeout = null;
        }

        // 更新内容
        statusToast.textContent = message;

        // 确保元素可见
        statusToast.style.display = 'block';
        statusToast.style.visibility = 'visible';

        // 显示气泡框
        statusToast.classList.remove('hide');
        // 使用 setTimeout 确保样式更新
        setTimeout(() => {
            statusToast.classList.add('show');
            console.log(window.t('console.statusToastClassAdded'), statusToast, window.t('console.statusToastClassList'), statusToast.classList);
        }, 10);

        // 自动隐藏
        statusToastTimeout = setTimeout(() => {
            statusToast.classList.remove('show');
            statusToast.classList.add('hide');
            setTimeout(() => {
                statusToast.textContent = '';
            }, 300);
        }, duration);

        // 同时更新隐藏的 status 元素（保持兼容性）
        if (statusElement) {
            statusElement.textContent = message || '';
        }
    }

    // 将 showStatusToast 暴露到全局作用域，方便调试和测试
    window.showStatusToast = showStatusToast;
    const chatContainer = document.getElementById('chatContainer');
    const textInputBox = document.getElementById('textInputBox');
    const textInputArea = document.getElementById('text-input-area');
    const textSendButton = document.getElementById('textSendButton');
    const screenshotButton = document.getElementById('screenshotButton');
    const screenshotThumbnailContainer = document.getElementById('screenshot-thumbnail-container');
    const screenshotsList = document.getElementById('screenshots-list');
    const screenshotCount = document.getElementById('screenshot-count');
    const clearAllScreenshots = document.getElementById('clear-all-screenshots');
    // ==========================================
    // 【将初始化代码移动到这里，确保只执行一次】
    // 初始化音乐消息提示词模块
    if (typeof window.MusicPrompt !== 'undefined') {
        try {
            window.MusicPrompt.initMusicPromptModule(textInputBox, textInputArea);
            console.log('[MusicPrompt] 模块已初始化');
        } catch (e) {
            console.error('[MusicPrompt] 初始化失败:', e);
        }
    }
    // ==========================================
    let audioContext;
    let workletNode;
    let stream;
    let isRecording = false;
    // 暴露 isRecording 到全局，供其他模块检查
    window.isRecording = false;
    // 麦克风启动中标志，用于区分"正在启动"和"已录音"两个阶段
    window.isMicStarting = false;
    let socket;
    // 将 currentGeminiMessage 改为全局变量，供字幕模块使用
    window.currentGeminiMessage = null;
    // 追踪本轮 AI 回复的所有气泡（用于改写时删除）
    window.currentTurnGeminiBubbles = [];
    // 拟真输出队列版本号，用于取消旧任务
    window._realisticGeminiVersion = 0;
    let audioPlayerContext = null;
    let videoTrack, videoSenderInterval;
    let audioBufferQueue = [];
    let screenshotCounter = 0; // 截图计数器
    let isPlaying = false;
    let scheduleAudioChunksRunning = false;
    let audioStartTime = 0;
    let nextChunkTime = 0;
    let scheduledSources = [];
    let animationFrameId;
    let seqCounter = 0;
    let globalAnalyser = null;
    let speakerGainNode = null;  // 扬声器音量增益节点
    let lipSyncActive = false;
    let screenCaptureStream = null; // 暂存屏幕共享stream，不再需要每次都弹窗选择共享区域，方便自动重连
    let screenCaptureStreamLastUsed = null; // 记录屏幕流最后使用时间，用于闲置自动释放
    let screenCaptureStreamIdleTimer = null; // 闲置释放定时器
    let screenCaptureAutoPromptFailed = false; // 防止无用户手势时 getDisplayMedia 反复弹窗（刷新后保护）
    let screenRecordingPermissionHintShown = false; // 403 屏幕录制权限提示只显示一次

    // 【补充声明】修复未声明变量导致的隐式全局或 ReferenceError
    let subtitleCheckDebounceTimer = null;

    // 屏幕流闲置释放的统一 helper 函数
    function scheduleScreenCaptureIdleCheck() {
        // 清除现有定时器
        if (screenCaptureStreamIdleTimer) {
            clearTimeout(screenCaptureStreamIdleTimer);
            screenCaptureStreamIdleTimer = null;
        }

        // 如果没有屏幕流，不需要调度
        if (!screenCaptureStream || !screenCaptureStreamLastUsed) {
            return;
        }

        const IDLE_TIMEOUT = 5 * 60 * 1000; // 5分钟
        const CHECK_INTERVAL = 60 * 1000; // 每分钟检查一次

        screenCaptureStreamIdleTimer = setTimeout(async () => {
            if (screenCaptureStream && screenCaptureStreamLastUsed) {
                const idleTime = Date.now() - screenCaptureStreamLastUsed;
                if (idleTime >= IDLE_TIMEOUT) {
                    // 主动视觉活跃时，不释放屏幕流（避免 macOS 反复弹窗 getDisplayMedia）
                    const proactiveVisionActive = proactiveVisionEnabled ||
                        (proactiveVisionChatEnabled && proactiveChatEnabled);
                    const isManualScreenShare = screenButton && screenButton.classList.contains('active');
                    if (proactiveVisionActive && !isManualScreenShare) {
                        console.log('[屏幕流闲置] 主动视觉活跃中，跳过释放并续约定时器');
                        screenCaptureStreamLastUsed = Date.now();
                        scheduleScreenCaptureIdleCheck();
                        return;
                    }

                    // 达到闲置阈值，调用 stopScreenSharing 统一释放资源并同步 UI
                    console.log(safeT('console.screenShareIdleDetected', 'Screen share idle detected, releasing resources'));
                    try {
                        await stopScreenSharing();
                    } catch (e) {
                        console.warn(safeT('console.screenShareAutoReleaseFailed', 'Screen share auto-release failed'), e);
                        // stopScreenSharing 失败时，手动清理残留状态防止 double-teardown
                        if (screenCaptureStream) {
                            try {
                                if (typeof screenCaptureStream.getTracks === 'function') {
                                    screenCaptureStream.getTracks().forEach(track => {
                                        try { track.stop(); } catch (err) { }
                                    });
                                }
                            } catch (err) {
                                console.warn('Failed to stop tracks in catch block', err);
                            }
                        }
                        screenCaptureStream = null;
                        screenCaptureStreamLastUsed = null;
                        screenCaptureStreamIdleTimer = null;
                    }
                } else {
                    // 未达到阈值，继续调度下一次检查
                    scheduleScreenCaptureIdleCheck();
                }
            }
        }, CHECK_INTERVAL);
    }
    // 新增：当前选择的麦克风设备ID
    let selectedMicrophoneId = null;

    // 麦克风增益控制相关变量（使用分贝单位）
    let microphoneGainDb = 0;           // 麦克风增益值（分贝），0dB为原始音量
    let micGainNode = null;             // GainNode 实例，用于实时调整增益
    const DEFAULT_MIC_GAIN_DB = 0;      // 默认增益（0dB = 原始音量）
    const MAX_MIC_GAIN_DB = 25;         // 最大增益（25dB ≈ 18倍放大）
    const MIN_MIC_GAIN_DB = -5;         // 最小增益（-5dB ≈ 0.56倍）
    let micVolumeAnimationId = null;    // 音量可视化动画帧ID

    // 扬声器音量控制相关变量
    let speakerVolume = 100;                // 扬声器音量 (0~100)
    const DEFAULT_SPEAKER_VOLUME = 100;     // 默认音量 100%

    // 分贝转线性增益：linear = 10^(dB/20)
    function dbToLinear(db) {
        return Math.pow(10, db / 20);
    }

    // 线性增益转分贝：dB = 20 * log10(linear)
    function linearToDb(linear) {
        return 20 * Math.log10(linear);
    }

    // Speech ID 精确打断控制相关变量
    let interruptedSpeechId = null;      // 被打断的 speech_id
    let currentPlayingSpeechId = null;   // 当前正在播放的 speech_id
    let pendingDecoderReset = false;     // 是否需要在下一个新 speech_id 时重置解码器
    let skipNextAudioBlob = false;       // 是否跳过下一个音频 blob（被打断的旧音频）
    let incomingAudioBlobQueue = [];     // 二进制音频包队列（串行消费，避免并发解码竞态）
    let pendingAudioChunkMetaQueue = []; // 与二进制包一一对应的 header 元数据队列
    let incomingAudioEpoch = 0;          // 音频代际号：用于淘汰打断前在途包
    let isProcessingIncomingAudioBlob = false;
    let decoderResetPromise = null;      // speech 切换时的解码器重置任务

    // 麦克风静音检测相关变量
    let silenceDetectionTimer = null;
    let hasSoundDetected = false;
    let inputAnalyser = null;

    // 模式管理
    let isTextSessionActive = false;
    let isSwitchingMode = false; // 新增：模式切换标志
    let sessionStartedResolver = null; // 用于等待 session_started 消息
    let sessionStartedRejecter = null; // 用于等待 session_failed / timeout 消息

    // 语音模式下用户 transcript 合并相关变量（兜底机制，防止 Gemini 等模型返回碎片化转录造成刷屏）
    let lastVoiceUserMessage = null;       // 上一个用户消息 DOM 元素
    let lastVoiceUserMessageTime = 0;      // 上一个用户消息的时间戳
    const VOICE_TRANSCRIPT_MERGE_WINDOW = 3000; // 合并时间窗口（毫秒），3秒内的连续转录会合并

    // 主动搭话功能相关
    let proactiveChatEnabled = false;
    let proactiveVisionEnabled = false;
    let proactiveVisionChatEnabled = true;
    let proactiveNewsChatEnabled = false;
    let proactiveVideoChatEnabled = false;
    let mergeMessagesEnabled = false;
    let proactivePersonalChatEnabled = false;
    let proactiveMusicEnabled = false;
    let proactiveChatTimer = null;
    let proactiveChatBackoffLevel = 0; // 退避级别：0=30s, 1=75s, 2=187.5s, etc.
    let isProactiveChatRunning = false; // 锁：防止主动搭话执行期间重复触发
    // 主动搭话时间间隔（可自定义，默认30秒）
    const DEFAULT_PROACTIVE_CHAT_INTERVAL = 30; // 默认30秒
    let proactiveChatInterval = DEFAULT_PROACTIVE_CHAT_INTERVAL;
    // 主动视觉在语音时的单帧推送（当同时开启主动视觉 && 语音对话时）
    let proactiveVisionFrameTimer = null;
    // 主动视觉时间间隔（可自定义，默认15秒）
    const DEFAULT_PROACTIVE_VISION_INTERVAL = 15; // 默认15秒
    let proactiveVisionInterval = DEFAULT_PROACTIVE_VISION_INTERVAL;

    // 截图最大尺寸（720p，用于节流数据传输）
    const MAX_SCREENSHOT_WIDTH = 1280;
    const MAX_SCREENSHOT_HEIGHT = 720;

    function syncAudioGlobals() {
        window.audioPlayerContext = audioPlayerContext;
        window.globalAnalyser = globalAnalyser;
    }

    syncAudioGlobals();

    /**
     * 统一的截图辅助函数：从video元素捕获一帧到canvas，统一720p节流和JPEG压缩
     * @param {HTMLVideoElement} video - 视频源元素
     * @param {number} jpegQuality - JPEG压缩质量 (0-1)，默认0.8
     * @returns {{dataUrl: string, width: number, height: number}} 返回dataUrl和实际尺寸
     */
    function captureCanvasFrame(video, jpegQuality = 0.8) {
        // 流无效时 videoWidth/videoHeight 为 0，直接返回 null 避免生成空图
        if (!video.videoWidth || !video.videoHeight) {
            return null;
        }

        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        // 计算缩放后的尺寸（保持宽高比，限制到720p）
        let targetWidth = video.videoWidth;
        let targetHeight = video.videoHeight;

        if (targetWidth > MAX_SCREENSHOT_WIDTH || targetHeight > MAX_SCREENSHOT_HEIGHT) {
            const widthRatio = MAX_SCREENSHOT_WIDTH / targetWidth;
            const heightRatio = MAX_SCREENSHOT_HEIGHT / targetHeight;
            const scale = Math.min(widthRatio, heightRatio);
            targetWidth = Math.round(targetWidth * scale);
            targetHeight = Math.round(targetHeight * scale);
        }

        canvas.width = targetWidth;
        canvas.height = targetHeight;

        // 绘制视频帧到canvas（缩放绘制）并转换为JPEG
        ctx.drawImage(video, 0, 0, targetWidth, targetHeight);
        const dataUrl = canvas.toDataURL('image/jpeg', jpegQuality);

        return { dataUrl, width: targetWidth, height: targetHeight };
    }

    /**
     * 后端截图兜底：当前端所有屏幕捕获 API 均失败时，请求后端用 pyautogui 截取本机屏幕。
     * 安全限制：仅当页面来自 localhost / 127.0.0.1 / 0.0.0.0 时才调用（确保后端与用户在同一台机器）。
     * @returns {Promise<{dataUrl: string|null, status: number|null}>} 包含 dataUrl 和 HTTP status
     */
    async function fetchBackendScreenshot() {
        const h = window.location.hostname;
        if (h !== 'localhost' && h !== '127.0.0.1' && h !== '0.0.0.0') {
            return { dataUrl: null, status: null };
        }
        try {
            const resp = await fetch('/api/screenshot');
            if (!resp.ok) return { dataUrl: null, status: resp.status };
            const json = await resp.json();
            if (json.success && json.data) {
                console.log('[截图] 后端 pyautogui 截图成功,', json.size, 'bytes');
                return { dataUrl: json.data, status: 200 };
            }
            return { dataUrl: null, status: resp.status };
        } catch (e) {
            console.warn('[截图] 后端截图请求失败:', e);
            return { dataUrl: null, status: null };
        }
    }

    // Focus模式为true时，AI播放语音时会自动静音麦克风（不允许打断）
    let focusModeEnabled = false;

    // 动画设置：画质和帧率
    let renderQuality = 'medium';   // 'low' | 'medium' | 'high'
    let targetFrameRate = 60;       // 30 | 45 | 60
    const mapRenderQualityToFollowPerf = (quality) => (quality === 'high' ? 'medium' : 'low');

    // 暴露到全局作用域，供 live2d.js 等其他模块访问和修改
    window.proactiveChatEnabled = proactiveChatEnabled;
    window.proactiveVisionEnabled = proactiveVisionEnabled;
    window.proactiveVisionChatEnabled = proactiveVisionChatEnabled;
    window.proactiveNewsChatEnabled = proactiveNewsChatEnabled;
    window.proactiveVideoChatEnabled = proactiveVideoChatEnabled;
    window.proactivePersonalChatEnabled = proactivePersonalChatEnabled;
    window.proactiveMusicEnabled = proactiveMusicEnabled;
    window.mergeMessagesEnabled = mergeMessagesEnabled;
    window.focusModeEnabled = focusModeEnabled;
    window.proactiveChatInterval = proactiveChatInterval;
    window.proactiveVisionInterval = proactiveVisionInterval;
    window.renderQuality = renderQuality;
    window.targetFrameRate = targetFrameRate;
    window.cursorFollowPerformanceLevel = mapRenderQualityToFollowPerf(renderQuality);

    // WebSocket心跳保活
    let heartbeatInterval = null;
    const HEARTBEAT_INTERVAL = 30000; // 30秒发送一次心跳

    // WebSocket自动重连定时器ID（用于在切换角色时取消之前的重连）
    let autoReconnectTimeoutId = null;

    function isMobile() {
        return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
            navigator.userAgent
        );
    }

    /**
     * 等待 WebSocket 连接就绪（OPEN 状态）。
     * - 已 OPEN → 立即返回
     * - CONNECTING → 通过 addEventListener('open') 等待（不覆盖 onopen）
     * - CLOSED/CLOSING 或 socket 不存在 → 取消排队的自动重连，触发 connectWebSocket() 后等待
     * @param {number} timeoutMs 超时毫秒数，默认 5000
     * @returns {Promise<void>}
     */
    function ensureWebSocketOpen(timeoutMs = 5000) {
        return new Promise((resolve, reject) => {
            // 已 OPEN，直接返回
            if (socket && socket.readyState === WebSocket.OPEN) {
                return resolve();
            }

            let settled = false;
            let timer = null;

            const settle = (fn, arg) => {
                if (settled) return;
                settled = true;
                if (timer) { clearTimeout(timer); timer = null; }
                fn(arg);
            };

            // 超时处理
            timer = setTimeout(() => {
                settle(reject, new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接'));
            }, timeoutMs);

            // 监听当前或即将创建的 socket 的 open 事件
            const attachOpenListener = (ws) => {
                if (!ws || settled) return;
                if (ws.readyState === WebSocket.OPEN) {
                    settle(resolve); return;
                }
                if (ws.readyState === WebSocket.CONNECTING) {
                    // 用 addEventListener 而非覆写 onopen，不干扰 connectWebSocket 的 onopen handler
                    ws.addEventListener('open', () => settle(resolve), { once: true });
                    ws.addEventListener('error', () => {
                        // socket 连接失败，等新的 connectWebSocket 重建
                    }, { once: true });
                    return;
                }
                // CLOSING/CLOSED — 等待新 socket 被创建后重新挂载
            };

            if (socket && socket.readyState === WebSocket.CONNECTING) {
                // 乐观路径：直接挂 listener，不触发重连
                attachOpenListener(socket);
                // 不 return — 下方轮询兜底：若此 socket 失败被替换，轮询自动挂到新 socket
            } else {
                // socket 不存在或已 CLOSED/CLOSING → 触发重建
                // ★ 先取消排队的自动重连定时器，避免 3 秒后再多建一个重复连接
                if (autoReconnectTimeoutId) {
                    clearTimeout(autoReconnectTimeoutId);
                    autoReconnectTimeoutId = null;
                }
                connectWebSocket();
            }

            // 轮询兜底：追踪 socket 引用，在 socket 被替换后自动重挂 listener
            // 初始化为 null：确保首次轮询时一定会对当前 socket 调用 attachOpenListener
            // （若初始化为 socket，connectWebSocket() 刚创建的 socket 会被跳过）
            let lastAttachedWs = null;
            const waitForNewSocket = () => {
                if (settled) return;
                if (socket) {
                    if (socket !== lastAttachedWs) {
                        lastAttachedWs = socket;
                        attachOpenListener(socket);
                    }
                    if (!settled) {
                        setTimeout(waitForNewSocket, socket.readyState === WebSocket.CONNECTING ? 200 : 50);
                    }
                } else {
                    setTimeout(waitForNewSocket, 50);
                }
            };
            setTimeout(waitForNewSocket, 10);
        });
    }

    // 建立WebSocket连接
    function connectWebSocket() {
        const currentLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name)
            ? window.lanlan_config.lanlan_name
            : '';
        if (!currentLanlanName) {
            console.warn('[WebSocket] lanlan_name is empty, wait for page config and retry');
            if (autoReconnectTimeoutId) {
                clearTimeout(autoReconnectTimeoutId);
            }
            autoReconnectTimeoutId = setTimeout(connectWebSocket, 500);
            return;
        }

        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${protocol}://${window.location.host}/ws/${currentLanlanName}`;
        console.log(window.t('console.websocketConnecting'), currentLanlanName, window.t('console.websocketUrl'), wsUrl);
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            console.log(window.t('console.websocketConnected'));
            // Warm up Agent snapshot once websocket is ready.
            Promise.all([
                fetch('/api/agent/health').then(r => r.ok).catch(() => false),
                fetch('/api/agent/flags').then(r => r.ok ? r.json() : null).catch(() => null),
                fetch('/api/agent/state').then(r => r.ok ? r.json() : null).catch(() => null)
            ]).then(([healthOk, flagsResp, stateResp]) => {
                if (flagsResp && flagsResp.success) {
                    window._agentStatusSnapshot = {
                        server_online: !!healthOk,
                        analyzer_enabled: !!flagsResp.analyzer_enabled,
                        flags: flagsResp.agent_flags || {},
                        agent_api_gate: flagsResp.agent_api_gate || {},
                        capabilities: (window._agentStatusSnapshot && window._agentStatusSnapshot.capabilities) || {},
                        updated_at: new Date().toISOString()
                    };
                    if (window.agentStateMachine && typeof window.agentStateMachine.updateCache === 'function') {
                        const warmFlags = flagsResp.agent_flags || {};
                        warmFlags.agent_enabled = !!flagsResp.analyzer_enabled;
                        window.agentStateMachine.updateCache(!!healthOk, warmFlags);
                    }
                }
                // Restore active tasks from state snapshot (covers page refresh / reconnect)
                if (stateResp && stateResp.success && stateResp.snapshot) {
                    const currentLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                    const activeTasks = stateResp.snapshot.active_tasks || [];
                    // Filter by current session/role (lanlan_name) to avoid cross-session task leakage
                    const filteredTasks = currentLanlanName
                        ? activeTasks.filter(t => !t.lanlan_name || t.lanlan_name === currentLanlanName)
                        : activeTasks;
                    // Replace map contents with snapshot (clear stale tasks)
                    window._agentTaskMap = new Map();
                    filteredTasks.forEach(t => { if (t && t.id) window._agentTaskMap.set(t.id, t); });
                    const tasks = Array.from(window._agentTaskMap.values());
                    const hasRunning = tasks.some(t => t.status === 'running' || t.status === 'queued');
                    // Show HUD if there are any tasks (including terminal in linger window)
                    // But only start timer for running/queued tasks
                    if (tasks.length > 0 && window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                        window.AgentHUD.showAgentTaskHUD();
                        window.AgentHUD.updateAgentTaskHUD({
                            success: true, tasks,
                            running_count: tasks.filter(t => t.status === 'running').length,
                            queued_count: tasks.filter(t => t.status === 'queued').length,
                        });
                        if (hasRunning && !agentTaskTimeUpdateInterval) {
                            agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
                        }
                    } else if (typeof window.checkAndToggleTaskHUD === 'function') {
                        // Re-evaluate HUD visibility from enablement state instead of
                        // force-hiding on an empty task snapshot.
                        window.checkAndToggleTaskHUD();
                    } else if (window.AgentHUD && typeof window.AgentHUD.hideAgentTaskHUD === 'function') {
                        window.AgentHUD.hideAgentTaskHUD();
                    }
                }
            }).catch(() => { });

            // 启动心跳保活机制
            if (heartbeatInterval) {
                clearInterval(heartbeatInterval);
            }
            heartbeatInterval = setInterval(() => {
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'ping'
                    }));
                }
            }, HEARTBEAT_INTERVAL);
            console.log(window.t('console.heartbeatStarted'));
        };

        socket.onmessage = (event) => {
            // 调试：记录所有收到的消息类型
            if (event.data instanceof Blob) {
                // 处理二进制音频数据
                if (window.DEBUG_AUDIO) {
                    console.log(window.t('console.audioBinaryReceived'), event.data.size, window.t('console.audioBinaryBytes'));
                }
                enqueueIncomingAudioBlob(event.data);
                return;
            }

            try {
                const response = JSON.parse(event.data);
                // 调试：记录所有收到的WebSocket消息类型
                if (response.type === 'catgirl_switched') {
                    console.log(window.t('console.catgirlSwitchedReceived'), response);
                }


                if (response.type === 'gemini_response') {
                    // 检查是否是新消息的开始
                    const isNewMessage = response.isNewMessage || false;

                    // AI 开始新回复时，重置用户转录合并追踪（避免跨轮次合并）
                    if (isNewMessage) {
                        lastVoiceUserMessage = null;
                        lastVoiceUserMessageTime = 0;
                    }

                    appendMessage(response.text, 'gemini', isNewMessage);
                } else if (response.type === 'response_discarded') {
                    window.invalidatePendingMusicSearch();
                    const attempt = response.attempt || 0;
                    const maxAttempts = response.max_attempts || 0;
                    console.log(`[Discard] AI回复被丢弃 reason=${response.reason} attempt=${attempt}/${maxAttempts} retry=${response.will_retry}`);

                    window._realisticGeminiQueue = [];
                    window._realisticGeminiBuffer = '';
                    window._pendingMusicCommand = '';
                    window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;

                    if (window.currentTurnGeminiBubbles && window.currentTurnGeminiBubbles.length > 0) {
                        window.currentTurnGeminiBubbles.forEach(bubble => {
                            if (bubble && bubble.parentNode) {
                                bubble.parentNode.removeChild(bubble);
                            }
                        });
                        window.currentTurnGeminiBubbles = [];
                    }

                    // 兜底：清除未被追踪但残留在聊天底部的 gemini 气泡，
                    // 确保下一轮 AI 回复能正确出现在玩家气泡下方
                    if ((!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0) &&
                        chatContainer && chatContainer.children && chatContainer.children.length > 0) {
                        const toRemove = [];
                        for (let i = chatContainer.children.length - 1; i >= 0; i--) {
                            const el = chatContainer.children[i];
                            if (el.classList && el.classList.contains('message') && el.classList.contains('gemini')) {
                                toRemove.push(el);
                            } else {
                                break;
                            }
                        }
                        toRemove.forEach(el => {
                            if (el && el.parentNode) {
                                el.parentNode.removeChild(el);
                            }
                        });
                    }

                    window._geminiTurnFullText = '';
                    window._pendingMusicCommand = '';

                    (async () => { await clearAudioQueue(); })();

                    const retryMsg = window.t ? window.t('console.aiRetrying') : '猫娘链接出现异常，校准中…';
                    const failMsg = window.t ? window.t('console.aiFailed') : '猫娘链接出现异常';
                    showStatusToast(response.will_retry ? retryMsg : failMsg, 2500);

                    if (!response.will_retry && response.message) {
                        const messageDiv = document.createElement('div');
                        messageDiv.classList.add('message', 'gemini');
                        messageDiv.textContent = "[" + getCurrentTimeString() + "] 🎀 " + response.message;
                        chatContainer.appendChild(messageDiv);
                        window.currentGeminiMessage = messageDiv;
                        window.currentTurnGeminiBubbles = [messageDiv];
                    }

                    chatContainer.scrollTop = chatContainer.scrollHeight;
                } else if (response.type === 'user_transcript') {
                    // 语音模式下的用户转录合并机制（兜底，防止 Gemini 等模型碎片化转录刷屏）
                    const now = Date.now();
                    const shouldMerge = isRecording &&
                        lastVoiceUserMessage &&
                        lastVoiceUserMessage.isConnected &&
                        (now - lastVoiceUserMessageTime) < VOICE_TRANSCRIPT_MERGE_WINDOW;

                    if (shouldMerge) {
                        // 合并到上一个用户消息气泡（流式追加）
                        lastVoiceUserMessage.textContent += response.text;
                        lastVoiceUserMessageTime = now; // 更新时间戳，延续合并窗口
                    } else {
                        // 创建新消息
                        appendMessage(response.text, 'user', true);

                        // 在语音模式下追踪这个消息，以便后续合并
                        if (isRecording) {
                            // 获取刚创建的用户消息元素（chatContainer 的最后一个 .user 消息）
                            const userMessages = chatContainer.querySelectorAll('.message.user');
                            if (userMessages.length > 0) {
                                lastVoiceUserMessage = userMessages[userMessages.length - 1];
                                lastVoiceUserMessageTime = now;
                            }
                        }
                    }
                } else if (response.type === 'user_activity') {
                    interruptedSpeechId = response.interrupted_speech_id || null;
                    pendingDecoderReset = true;  // 标记需要在新 speech_id 到来时重置
                    skipNextAudioBlob = false;   // 重置跳过标志
                    incomingAudioEpoch += 1;     // 让当前代际之前的在途包全部失效
                    incomingAudioBlobQueue = []; // 丢弃尚未处理的旧音频包
                    pendingAudioChunkMetaQueue = []; // 丢弃尚未消费的旧 header

                    // 只清空播放队列，不重置解码器（避免丢失新音频的头信息）
                    clearAudioQueueWithoutDecoderReset();
                } else if (response.type === 'audio_chunk') {
                    if (window.DEBUG_AUDIO) {
                        console.log(window.t('console.audioChunkHeaderReceived'), response);
                    }
                    // 精确打断控制：根据 speech_id 决定是否接收此音频
                    const speechId = response.speech_id;
                    let shouldSkip = false;

                    // 检查是否是被打断的旧音频，如果是则丢弃
                    if (speechId && interruptedSpeechId && speechId === interruptedSpeechId) {
                        if (window.DEBUG_AUDIO) {
                            console.log(window.t('console.discardInterruptedAudio'), speechId);
                        }
                        shouldSkip = true;
                    } else if (speechId && speechId !== currentPlayingSpeechId) {
                        // 检查是否是新的 speech_id（新轮对话开始）
                        // 新轮对话开始，在此时重置解码器（确保有新的头信息）
                        if (pendingDecoderReset) {
                            console.log(window.t('console.newConversationResetDecoder'), speechId);
                            decoderResetPromise = (async () => {
                                await resetOggOpusDecoder();
                                pendingDecoderReset = false;
                            })();
                        } else {
                            pendingDecoderReset = false;
                        }
                        currentPlayingSpeechId = speechId;
                        interruptedSpeechId = null;  // 清除旧的打断记录
                    }

                    // 记录该 header 对应的 blob 处理策略，后续二进制包按顺序消费
                    pendingAudioChunkMetaQueue.push({
                        speechId: speechId || currentPlayingSpeechId || null,
                        shouldSkip: shouldSkip,
                        epoch: incomingAudioEpoch
                    });
                    skipNextAudioBlob = false;  // 兼容旧逻辑：重置标志
                } else if (response.type === 'cozy_audio') {
                    // 处理音频响应
                    console.log(window.t('console.newAudioHeaderReceived'))
                    const isNewMessage = response.isNewMessage || false;

                    if (isNewMessage) {
                        // 如果是新消息，清空当前音频队列
                        (async () => {
                            await clearAudioQueue();
                        })();
                    }

                    // 根据数据格式选择处理方法
                    if (response.format === 'base64') {
                        handleBase64Audio(response.audioData, isNewMessage);
                    }
                } else if (response.type === 'screen_share_error') {
                    // 屏幕分享/截图错误，复位按钮状态
                    const translatedMessage = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    showStatusToast(translatedMessage, 4000);

                    // 停止屏幕分享
                    stopScreening();

                    // 清理屏幕捕获流
                    if (screenCaptureStream) {
                        screenCaptureStream.getTracks().forEach(track => track.stop());
                        screenCaptureStream = null;
                    }

                    // 复位按钮状态
                    if (isRecording) {
                        // 在语音模式下（屏幕分享）
                        micButton.disabled = true;
                        muteButton.disabled = false;
                        screenButton.disabled = false;
                        stopButton.disabled = true;
                        resetSessionButton.disabled = false;
                    } else if (isTextSessionActive) {
                        // 在文本模式下（截图）
                        screenshotButton.disabled = false;
                    }
                } else if (response.type === 'catgirl_switched') {
                    // 处理猫娘切换通知（从后端WebSocket推送）
                    const newCatgirl = response.new_catgirl;
                    const oldCatgirl = response.old_catgirl;
                    console.log(window.t('console.catgirlSwitchNotification'), oldCatgirl, window.t('console.catgirlSwitchTo'), newCatgirl);
                    console.log(window.t('console.currentFrontendCatgirl'), lanlan_config.lanlan_name);
                    handleCatgirlSwitch(newCatgirl, oldCatgirl);
                } else if (response.type === 'status') {
                    // 尝试解析结构化消息
                    let statusCode = null;
                    try {
                        const parsed = JSON.parse(response.message);
                        if (parsed && parsed.code) statusCode = parsed.code;
                    } catch (_) { }

                    // 如果正在切换模式且收到"已离开"消息，则忽略
                    if (isSwitchingMode && (statusCode === 'CHARACTER_LEFT' || response.message.includes('已离开'))) {
                        console.log(window.t('console.modeSwitchingIgnoreLeft'));
                        return;
                    }

                    // 检测严重错误，自动隐藏准备提示（兜底机制）
                    const criticalErrorCodes = ['SESSION_START_CRITICAL', 'MEMORY_SERVER_CRASHED', 'API_KEY_REJECTED', 'API_RATE_LIMIT_SESSION', 'ERROR_1007_ARREARS', 'AGENT_QUOTA_EXCEEDED', 'RESPONSE_TIMEOUT', 'CONNECTION_TIMEOUT'];
                    if (statusCode && criticalErrorCodes.includes(statusCode)) {
                        console.log(window.t('console.seriousErrorHidePreparing'));
                        hideVoicePreparingToast();
                    }

                    // 翻译后端发送的状态消息
                    const translatedMessage = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    showStatusToast(translatedMessage, 4000);
                    if (statusCode === 'CHARACTER_DISCONNECTED') {
                        if (isRecording === false && !isTextSessionActive) {
                            showStatusToast(window.t ? window.t('app.catgirlResting', { name: lanlan_config.lanlan_name }) : `${lanlan_config.lanlan_name}正在打盹...`, 5000);
                        } else if (isTextSessionActive) {
                            showStatusToast(window.t ? window.t('app.textChatting') : `正在文本聊天中...`, 5000);
                        } else {
                            stopRecording();
                            // 同步浮动按钮状态
                            syncFloatingMicButtonState(false);
                            syncFloatingScreenButtonState(false);
                            if (socket.readyState === WebSocket.OPEN) {
                                socket.send(JSON.stringify({
                                    action: 'end_session'
                                }));
                            }
                            hideLive2d();
                            micButton.disabled = true;
                            muteButton.disabled = true;
                            screenButton.disabled = true;
                            stopButton.disabled = true;
                            resetSessionButton.disabled = true;
                            returnSessionButton.disabled = true;

                            setTimeout(async () => {
                                try {
                                    // 创建一个 Promise 来等待 session_started 消息
                                    const sessionStartPromise = new Promise((resolve, reject) => {
                                        sessionStartedResolver = resolve;
                                        sessionStartedRejecter = reject; //  保存 reject 函数

                                        if (window.sessionTimeoutId) {
                                            clearTimeout(window.sessionTimeoutId);
                                            window.sessionTimeoutId = null;
                                        }
                                    });

                                    // 发送start session事件（确保 WebSocket 已连接）
                                    await ensureWebSocketOpen();
                                    socket.send(JSON.stringify({
                                        action: 'start_session',
                                        input_type: 'audio'
                                    }));

                                    // 在发送消息后才开始超时计时（自动重启场景）
                                    window.sessionTimeoutId = setTimeout(() => {
                                        if (sessionStartedRejecter) {
                                            const rejecter = sessionStartedRejecter;
                                            sessionStartedResolver = null;
                                            sessionStartedRejecter = null; //  同时清理 rejecter
                                            window.sessionTimeoutId = null;

                                            // 超时时向后端发送 end_session 消息
                                            if (socket && socket.readyState === WebSocket.OPEN) {
                                                socket.send(JSON.stringify({
                                                    action: 'end_session'
                                                }));
                                                console.log(window.t('console.autoRestartTimeoutEndSession'));
                                            }

                                            rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                                        }
                                    }, 15000); // 15秒（略大于后端12秒，对冲网络延迟）

                                    // 等待session真正启动成功
                                    await sessionStartPromise;

                                    await showCurrentModel(); // 智能显示当前模型
                                    await startMicCapture();
                                    if (screenCaptureStream != null) {
                                        await startScreenSharing();
                                    }

                                    // 同步更新Live2D浮动按钮状态
                                    if (window.live2dManager && window.live2dManager._floatingButtons) {
                                        // 更新麦克风按钮状态
                                        syncFloatingMicButtonState(true);

                                        // 更新屏幕分享按钮状态（如果屏幕共享已开启）
                                        if (screenCaptureStream != null) {
                                            syncFloatingScreenButtonState(true);
                                        }
                                    }

                                    showStatusToast(window.t ? window.t('app.restartComplete', { name: lanlan_config.lanlan_name }) : `重启完成，${lanlan_config.lanlan_name}回来了！`, 4000);
                                } catch (error) {
                                    console.error(window.t('console.restartError'), error);

                                    // 清除超时定时器和 Promise 状态（与 mic button catch 对齐）
                                    if (window.sessionTimeoutId) {
                                        clearTimeout(window.sessionTimeoutId);
                                        window.sessionTimeoutId = null;
                                    }
                                    sessionStartedResolver = null;
                                    sessionStartedRejecter = null;

                                    // 重启失败时向后端发送 end_session 消息
                                    if (socket && socket.readyState === WebSocket.OPEN) {
                                        socket.send(JSON.stringify({
                                            action: 'end_session'
                                        }));
                                        console.log(window.t('console.autoRestartFailedEndSession'));
                                    }

                                    hideVoicePreparingToast(); // 确保重启失败时隐藏准备提示
                                    showStatusToast(window.t ? window.t('app.restartFailed', { error: error.message }) : `重启失败: ${error.message}`, 5000);

                                    // 完整的状态清理逻辑：确保重启失败时正确恢复到待机状态
                                    // 1. 移除按钮状态类
                                    micButton.classList.remove('recording');
                                    micButton.classList.remove('active');
                                    screenButton.classList.remove('active');

                                    // 2. 重置录音标志
                                    isRecording = false;
                                    window.isRecording = false;

                                    // 3. 同步Live2D浮动按钮状态
                                    syncFloatingMicButtonState(false);
                                    syncFloatingScreenButtonState(false);

                                    // 4. 重新启用基本输入按钮（切换到文本模式）
                                    micButton.disabled = false;
                                    textSendButton.disabled = false;
                                    textInputBox.disabled = false;
                                    screenshotButton.disabled = false;
                                    resetSessionButton.disabled = false;

                                    // 5. 禁用语音控制按钮
                                    muteButton.disabled = true;
                                    screenButton.disabled = true;
                                    stopButton.disabled = true;

                                    // 6. 显示文本输入区
                                    const textInputArea = document.getElementById('text-input-area');
                                    if (textInputArea) {
                                        textInputArea.classList.remove('hidden');
                                    }
                                }
                            }, 7500); // 7.5秒后执行
                        }
                    }
                } else if (response.type === 'expression') {
                    const lanlan = window.LanLan1;
                    const registry = lanlan && lanlan.registered_expressions;
                    const fn = registry && registry[response.message];
                    if (typeof fn === 'function') {
                        fn();
                    } else {
                        console.warn(window.t('console.unknownExpressionCommand'), response.message);
                    }
                } else if (response.type === 'agent_status_update') {
                    const snapshot = response.snapshot || {};
                    window._agentStatusSnapshot = snapshot;
                    const serverOnline = snapshot.server_online !== false;
                    const flags = snapshot.flags || {};
                    // agent_enabled lives in snapshot.analyzer_enabled, not in flags — normalize it
                    if (!('agent_enabled' in flags) && snapshot.analyzer_enabled !== undefined) {
                        flags.agent_enabled = !!snapshot.analyzer_enabled;
                    }
                    if (window.agentStateMachine && typeof window.agentStateMachine.updateCache === 'function') {
                        window.agentStateMachine.updateCache(serverOnline, flags);
                    }
                    if (typeof window.applyAgentStatusSnapshotToUI === 'function') {
                        window.applyAgentStatusSnapshotToUI(snapshot);
                    }
                    // Restore task HUD on page refresh: use snapshot flags
                    // even when popup checkboxes don't exist yet
                    try {
                        const masterOn = !!flags.agent_enabled;
                        const anyChildOn = !!(flags.computer_use_enabled || flags.browser_use_enabled || flags.user_plugin_enabled);
                        if (masterOn && anyChildOn && typeof window.startAgentTaskPolling === 'function') {
                            window.startAgentTaskPolling();
                        }
                        // Restore active tasks from snapshot (covers page refresh / reconnect)
                        const currentLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        const snapshotTasks = snapshot.active_tasks || [];
                        // Filter by current session/role (lanlan_name) to avoid cross-session task leakage
                        const filteredSnapshotTasks = currentLanlanName
                            ? snapshotTasks.filter(t => !t.lanlan_name || t.lanlan_name === currentLanlanName)
                            : snapshotTasks;
                        // Merge snapshot with existing tasks: preserve terminal tasks still in linger window
                        if (!window._agentTaskMap) window._agentTaskMap = new Map();
                        const now = Date.now();
                        const LINGER_MS = 10000; // Same as MIN_DISPLAY_MS in common-ui-hud.js
                        // Build new map: filtered snapshot tasks + lingering terminal tasks
                        const newMap = new Map();
                        filteredSnapshotTasks.forEach(t => {
                            if (t && t.id) newMap.set(t.id, t);
                        });
                        // Preserve terminal tasks still within linger window (also filter by role)
                        window._agentTaskMap.forEach((t, id) => {
                            if (!newMap.has(id)) {
                                // Skip tasks from other roles
                                if (currentLanlanName && t.lanlan_name && t.lanlan_name !== currentLanlanName) {
                                    return;
                                }
                                const isTerminal = t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled';
                                if (isTerminal && t.terminal_at && (now - t.terminal_at < LINGER_MS)) {
                                    newMap.set(id, t);
                                }
                            }
                        });
                        window._agentTaskMap = newMap;
                        const tasks = Array.from(window._agentTaskMap.values());
                        if (tasks.length > 0) {
                            if (window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                                window.AgentHUD.updateAgentTaskHUD({
                                    success: true,
                                    tasks,
                                    total_count: tasks.length,
                                    running_count: tasks.filter(t => t.status === 'running').length,
                                    queued_count: tasks.filter(t => t.status === 'queued').length,
                                    completed_count: tasks.filter(t => t.status === 'completed').length,
                                    failed_count: tasks.filter(t => t.status === 'failed').length,
                                    timestamp: new Date().toISOString()
                                });
                            }
                        } else if (typeof window.checkAndToggleTaskHUD === 'function') {
                            // An empty snapshot should not hide the HUD while any
                            // agent capability is still enabled.
                            window.checkAndToggleTaskHUD();
                        } else if (window.AgentHUD && typeof window.AgentHUD.hideAgentTaskHUD === 'function') {
                            window.AgentHUD.hideAgentTaskHUD();
                        }
                    } catch (_e) { /* ignore */ }
                } else if (response.type === 'agent_notification') {
                    const msg = typeof response.text === 'string' ? response.text : '';
                    if (msg) {
                        setFloatingAgentStatus(msg, response.status || 'completed');
                        maybeShowAgentQuotaExceededModal(msg);
                        maybeShowContentFilterModal(msg);
                        if (response.error_message) maybeShowContentFilterModal(response.error_message);
                    }
                } else if (response.type === 'agent_task_update') {
                    try {
                        if (!window._agentTaskMap) window._agentTaskMap = new Map();
                        if (!window._agentTaskRemoveTimers) window._agentTaskRemoveTimers = new Map();
                        const task = response.task || {};
                        if (task.id) {
                            // Merge with existing task data to preserve params from earlier updates
                            const existing = window._agentTaskMap.get(task.id);
                            const merged = existing ? { ...existing, ...task } : task;
                            // Preserve params: only fallback to existing.params if task.params is undefined (not just empty)
                            if (existing && existing.params && typeof task.params === 'undefined') {
                                merged.params = existing.params;
                            }
                            // Set terminal_at when task transitions to terminal state (for linger logic)
                            if (['completed', 'failed', 'cancelled'].includes(task.status)) {
                                if (!existing || !['completed', 'failed', 'cancelled'].includes(existing.status)) {
                                    merged.terminal_at = Date.now();
                                }
                            }
                            window._agentTaskMap.set(task.id, merged);
                            if (['completed', 'failed', 'cancelled'].includes(task.status)) {
                                if (window._agentTaskRemoveTimers.has(task.id)) clearTimeout(window._agentTaskRemoveTimers.get(task.id));
                                window._agentTaskRemoveTimers.set(task.id, setTimeout(() => {
                                    const current = window._agentTaskMap.get(task.id);
                                    if (current && ['completed', 'failed', 'cancelled'].includes(current.status)) {
                                        window._agentTaskMap.delete(task.id);
                                    }
                                    window._agentTaskRemoveTimers.delete(task.id);
                                    const remaining = Array.from(window._agentTaskMap.values());
                                    if (window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                                        window.AgentHUD.updateAgentTaskHUD({ success: true, tasks: remaining, total_count: remaining.length, running_count: remaining.filter(t => t.status === 'running').length, queued_count: remaining.filter(t => t.status === 'queued').length, completed_count: remaining.filter(t => t.status === 'completed').length, failed_count: remaining.filter(t => t.status === 'failed').length, timestamp: new Date().toISOString() });
                                    }
                                }, 10000));
                            } else if (window._agentTaskRemoveTimers.has(task.id)) {
                                clearTimeout(window._agentTaskRemoveTimers.get(task.id));
                                window._agentTaskRemoveTimers.delete(task.id);
                            }
                        }
                        const tasks = Array.from(window._agentTaskMap.values());
                        const hasRunning = tasks.some(t => t.status === 'running' || t.status === 'queued');
                        // Show HUD if there are any tasks (including terminal tasks in linger window)
                        // But only start the time-update interval for running/queued tasks
                        if (tasks.length > 0 && window.AgentHUD) {
                            if (typeof window.AgentHUD.showAgentTaskHUD === 'function') {
                                window.AgentHUD.showAgentTaskHUD();
                            }
                            // Only start timer when there are active (running/queued) tasks
                            if (hasRunning && !agentTaskTimeUpdateInterval) {
                                agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
                            }
                        }
                        if (window.AgentHUD && typeof window.AgentHUD.updateAgentTaskHUD === 'function') {
                            window.AgentHUD.updateAgentTaskHUD({
                                success: true,
                                tasks,
                                total_count: tasks.length,
                                running_count: tasks.filter(t => t.status === 'running').length,
                                queued_count: tasks.filter(t => t.status === 'queued').length,
                                completed_count: tasks.filter(t => t.status === 'completed').length,
                                failed_count: tasks.filter(t => t.status === 'failed').length,
                                timestamp: new Date().toISOString()
                            });
                        }
                        if (task && task.status === 'failed') {
                            const errMsg = task.error || task.reason || '';
                            if (errMsg) {
                                maybeShowAgentQuotaExceededModal(errMsg);
                                maybeShowContentFilterModal(errMsg);
                            }
                        }
                    } catch (e) {
                        console.warn('[App] 处理 agent_task_update 失败:', e);
                    }
                } else if (response.type === 'request_screenshot') {
                    (async () => {
                        try {
                            const dataUrl = await captureProactiveChatScreenshot();
                            if (dataUrl && socket && socket.readyState === WebSocket.OPEN) {
                                socket.send(JSON.stringify({ action: 'screenshot_response', data: dataUrl }));
                            }
                        } catch (e) {
                            console.warn('[App] request_screenshot capture failed:', e);
                        }
                    })();
                } else if (response.type === 'system' && response.data === 'turn end') {
                    console.log(window.t('console.turnEndReceived'));
                    // 合并消息关闭（分句模式）时：兜底 flush 未以标点结尾的最后缓冲，避免最后一段永远不显示
                    try {
                        // 【补充修复】在 flush 最后缓冲区前，不仅要清空 pending，
                        // 还要确保 rest 里的半截指令被彻底正则抹除，防止“打字机”最后蹦出个 [play_...
                        window._pendingMusicCommand = '';

                        let rest = typeof window._realisticGeminiBuffer === 'string'
                            ? window._realisticGeminiBuffer.replace(/\[play_music:[^\]]*(\]|$)/g, '')
                            : '';

                        // 统一清理可能残留的完整或半截指令内容
                        rest = rest.replace(/\[play_music:[^\]]*(\]|$)/g, '');

                        const trimmed = rest.replace(/^\s+/, '').replace(/\s+$/, '');
                        if (trimmed) {
                            window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                            window._realisticGeminiQueue.push(trimmed);
                            window._realisticGeminiBuffer = '';
                            processRealisticQueue(window._realisticGeminiVersion || 0);
                        }
                    } catch (e) {
                        console.warn(window.t('console.turnEndFlushFailed'), e);
                    }
                    // 消息完成时进行情感分析和翻译
                    {
                        const bufferedFullText = typeof window._geminiTurnFullText === 'string'
                            ? window._geminiTurnFullText
                            : '';
                        const fallbackFromBubble = (window.currentGeminiMessage &&
                            window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
                            window.currentGeminiMessage.isConnected &&
                            typeof window.currentGeminiMessage.textContent === 'string')
                            ? window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '')
                            : '';

                        let fullText = (bufferedFullText && bufferedFullText.trim()) ? bufferedFullText : fallbackFromBubble;
                        // 1. 触发音乐气泡生成
                        if (typeof window.processMusicCommands === 'function' && fullText) {
                            window.processMusicCommands(fullText);
                        }

                        // 2. 剔除音乐指令，避免影响后续的情感分析和字幕翻译
                        fullText = fullText.replace(/\[play_music:[^\]]*(\]|$)/g, '').trim();

                        if (!fullText || !fullText.trim()) {
                            return;
                        }

                        // 情感分析（5秒超时保护）
                        setTimeout(async () => {
                            try {
                                const emotionPromise = analyzeEmotion(fullText);
                                const timeoutPromise = new Promise((_, reject) =>
                                    setTimeout(() => reject(new Error('情感分析超时')), 5000)
                                );

                                const emotionResult = await Promise.race([emotionPromise, timeoutPromise]);
                                if (emotionResult && emotionResult.emotion) {
                                    console.log(window.t('console.emotionAnalysisComplete'), emotionResult);
                                    applyEmotion(emotionResult.emotion);
                                }
                            } catch (error) {
                                if (error.message === '情感分析超时') {
                                    console.warn(window.t('console.emotionAnalysisTimeout'));
                                } else {
                                    console.warn(window.t('console.emotionAnalysisFailed'), error);
                                }
                            }
                        }, 100);

                        // 前端翻译处理
                        (async () => {
                            try {
                                if (userLanguage === null) {
                                    await getUserLanguage();
                                }

                                // 用户要求：不要自动翻译聊天框内的文本
                                // if (userLanguage && userLanguage !== 'zh') {
                                //     await translateMessageBubble(fullText, window.currentGeminiMessage);
                                // }

                                // 用户要求：只在开启字幕翻译开关后才进行翻译
                                if (subtitleEnabled) {
                                    await translateAndShowSubtitle(fullText);
                                }
                            } catch (error) {
                                console.error(window.t('console.translationProcessFailed'), {
                                    error: error.message,
                                    stack: error.stack,
                                    fullText: fullText.substring(0, 50) + '...',
                                    userLanguage: userLanguage
                                });
                                if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
                                    console.warn(window.t('console.translationUnavailable'));
                                }
                            }
                        })();
                    }

                    // AI回复完成后，重置主动搭话计时器（如果已开启且在文本模式）
                    // 先调用 hasAnyChatModeEnabled() 确保同步状态
                    const hasChatMode = hasAnyChatModeEnabled();
                    if (proactiveChatEnabled && hasChatMode && !isRecording) {
                        resetProactiveChatBackoff();
                    }
                } else if (response.type === 'session_preparing') {
                    console.log(window.t('console.sessionPreparingReceived'), response.input_mode);
                    // 显示持续性的准备中提示（仅语音模式，文本模式用 statusToast 即可）
                    if (response.input_mode !== 'text') {
                        const preparingMessage = window.t ? window.t('app.voiceSystemPreparing') : '语音系统准备中，请稍候...';
                        showVoicePreparingToast(preparingMessage);
                    }
                } else if (response.type === 'session_started') {
                    console.log(window.t('console.sessionStartedReceived'), response.input_mode);
                    // 延迟 500ms 以确保准备中提示不会消失得太快
                    setTimeout(() => {
                        // 隐藏准备中提示
                        hideVoicePreparingToast();
                        // 解析 session_started Promise
                        if (sessionStartedResolver) {
                            // 清除可能存在的超时定时器（通过全局变量）
                            if (window.sessionTimeoutId) {
                                clearTimeout(window.sessionTimeoutId);
                                window.sessionTimeoutId = null;
                            }
                            sessionStartedResolver(response.input_mode);
                            sessionStartedResolver = null;
                            sessionStartedRejecter = null; //  同时清理 rejecter
                        }
                    }, 500);
                } else if (response.type === 'session_failed') {
                    // Session启动失败（由后端发送）
                    console.log(window.t('console.sessionFailedReceived'), response.input_mode);
                    // 立即隐藏准备中提示
                    hideVoicePreparingToast();
                    // 清除超时定时器
                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                    // Reject Promise 让等待的代码能处理失败情况，避免 Promise 永远 pending
                    if (sessionStartedRejecter) {
                        sessionStartedRejecter(new Error(response.message || (window.t ? window.t('app.sessionFailed') : 'Session启动失败')));
                    } else {
                        // 兜底：如果 Promise 已被消费（超时或其他原因），直接重置 UI 状态
                        micButton.classList.remove('active');
                        micButton.classList.remove('recording');
                        micButton.disabled = false;
                        muteButton.disabled = true;
                        screenButton.disabled = true;
                        stopButton.disabled = true;
                        resetSessionButton.disabled = false;
                        syncFloatingMicButtonState(false);
                        syncFloatingScreenButtonState(false);
                        window.isMicStarting = false;
                        isSwitchingMode = false;
                        const _textInputArea = document.getElementById('text-input-area');
                        if (_textInputArea) _textInputArea.classList.remove('hidden');
                    }
                    sessionStartedResolver = null;
                    sessionStartedRejecter = null;
                } else if (response.type === 'session_ended_by_server') {
                    // 后端 session 被服务器终止（如API断连），重置前端会话状态
                    console.log('[App] Session ended by server, input_mode:', response.input_mode);

                    isTextSessionActive = false;

                    // 清理可能存在的 session Promise
                    if (sessionStartedRejecter) {
                        try {
                            sessionStartedRejecter(new Error('Session ended by server'));
                        } catch (e) { /* ignore */ }
                    }
                    sessionStartedResolver = null;
                    sessionStartedRejecter = null;

                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }

                    // 如果当前处于语音模式，停止录音
                    if (isRecording) {
                        stopRecording();
                    }

                    // 无条件清理音频队列，防止残留播放
                    (async () => { await clearAudioQueue(); })();

                    hideVoicePreparingToast();

                    // 恢复 UI 到空闲状态
                    micButton.classList.remove('active');
                    micButton.classList.remove('recording');
                    screenButton.classList.remove('active');
                    micButton.disabled = false;
                    textSendButton.disabled = false;
                    textInputBox.disabled = false;
                    screenshotButton.disabled = false;
                    muteButton.disabled = true;
                    screenButton.disabled = true;
                    stopButton.disabled = true;
                    resetSessionButton.disabled = true;
                    returnSessionButton.disabled = true;

                    const textInputArea = document.getElementById('text-input-area');
                    if (textInputArea) {
                        textInputArea.classList.remove('hidden');
                    }

                    syncFloatingMicButtonState(false);
                    syncFloatingScreenButtonState(false);

                    window.isMicStarting = false;
                    isSwitchingMode = false;
                } else if (response.type === 'reload_page') {
                    console.log(window.t('console.reloadPageReceived'), response.message);
                    // 显示提示信息
                    const reloadMsg = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    showStatusToast(reloadMsg || (window.t ? window.t('app.configUpdated') : '配置已更新，页面即将刷新'), 3000);

                    // 延迟2.5秒后刷新页面，让后端有足够时间完成session关闭和配置重新加载
                    setTimeout(() => {
                        console.log(window.t('console.reloadPageStarting'));
                        // 在刷新前关闭所有已打开的设置窗口，避免窗口引用丢失导致重复打开
                        if (window.closeAllSettingsWindows) {
                            window.closeAllSettingsWindows();
                        }
                        window.location.reload();
                    }, 2500);
                } else if (response.type === 'auto_close_mic') {
                    console.log(window.t('console.autoCloseMicReceived'));
                    // 长时间无语音输入，模拟用户手动关闭语音会话
                    if (isRecording) {
                        // 直接触发闭麦按钮点击，走完整的关闭流程（包括通知后端）
                        muteButton.click();

                        // 显示提示信息
                        showStatusToast(response.message || (window.t ? window.t('app.autoMuteTimeout') : '长时间无语音输入，已自动关闭麦克风'), 4000);
                    } else {
                        // isRecording 为 false 时，也需要同步按钮状态
                        micButton.classList.remove('active');
                        micButton.classList.remove('recording');
                        syncFloatingMicButtonState(false);
                        showStatusToast(response.message || (window.t ? window.t('app.autoMuteTimeout') : '长时间无语音输入，已自动关闭麦克风'), 4000);
                    }
                } else if (response.action === 'music') {
                    const searchTerm = response.search_term;
                    if (searchTerm) {
                        console.log(`[Music] Received music action with search term: ${searchTerm}`);
                        if (window.showStatusToast) {
                            const searchMsg = window.t('music.searching', { query: searchTerm, defaultValue: '正在为您搜索: ' + searchTerm });
                            window.showStatusToast(searchMsg, 2000);
                        }

                        // 【新增】每次搜索前纪元+1，并记录下当前请求的纪元
                        currentMusicSearchEpoch++;
                        const myEpoch = currentMusicSearchEpoch;

                        fetch(`/api/music/search?query=${encodeURIComponent(searchTerm)}`)
                            .then(res => res.json())
                            .then(result => {
                                // 【新增】检查门闩，丢弃过期请求
                                if (typeof myEpoch !== 'undefined' && typeof currentMusicSearchEpoch !== 'undefined') {
                                    if (myEpoch !== currentMusicSearchEpoch) {
                                        console.log(`[Music] 丢弃过期的搜索结果: ${searchTerm}`);
                                        return;
                                    }
                                }

                                // 【修改】将 success 为 false 的情况单独拆分出来
                                if (result.success) {
                                    if (result.data && result.data.length > 0) {
                                        const track = result.data[0];
                                        window.dispatchMusicPlay(track);
                                    } else {
                                        console.warn(`[Music] API did not find a song for: ${searchTerm}`);
                                        if (window.showStatusToast) {
                                            const notFoundMsg = window.t('music.notFound', { query: searchTerm, defaultValue: '找不到歌曲: ' + searchTerm });
                                            window.showStatusToast(notFoundMsg, 3000);
                                        }
                                    }
                                } else {
                                    console.error(`[Music] Music search API returned error:`, result.message || result.error);
                                    if (window.showStatusToast) {
                                        const failMsg = window.safeT ? window.safeT('music.searchFailed', '音乐搜索失败') : '音乐搜索失败';
                                        // 优先显示后端给出的具体错误信息，如果没有再用通用提示
                                        const detailMsg = result.message || result.error || failMsg;
                                        window.showStatusToast(detailMsg, 3000);
                                    }
                                }
                            })
                            .catch(e => {
                                // 【新增】检查门闩，如果是过期请求直接忽略
                                if (typeof myEpoch !== 'undefined' && typeof currentMusicSearchEpoch !== 'undefined') {
                                    if (myEpoch !== currentMusicSearchEpoch) return;
                                }

                                console.error(`[Music] Music search API call failed:`, e);
                                if (window.showStatusToast) {
                                    const failMsg = window.safeT ? window.safeT('music.searchFailed', '音乐搜索失败') : '音乐搜索失败';
                                    window.showStatusToast(failMsg, 3000);
                                }
                            });
                    }
                } else if (response.type === 'repetition_warning') {
                    // 处理高重复度对话警告
                    console.log(window.t('console.repetitionWarningReceived'), response.name);
                    const warningMessage = window.t
                        ? window.t('app.repetitionDetected', { name: response.name })
                        : `检测到高重复度对话。建议您终止对话，让${response.name}休息片刻。`;
                    showStatusToast(warningMessage, 8000);
                }

            } catch (error) {
                console.error(window.t('console.messageProcessingFailed'), error);
            }
        };

        socket.onclose = () => {
            console.log(window.t('console.websocketClosed'));

            // 清理心跳定时器
            if (heartbeatInterval) {
                clearInterval(heartbeatInterval);
                heartbeatInterval = null;
                console.log(window.t('console.heartbeatStopped'));
            }

            // 重置文本session状态，因为后端会清理session
            if (isTextSessionActive) {
                isTextSessionActive = false;
                console.log(window.t('console.websocketDisconnectedResetText'));
            }

            // 重置语音录制状态和资源（包括录制中或麦克风启动中的情况）
            if (isRecording || window.isMicStarting) {
                console.log('WebSocket断开时重置语音录制状态');
                isRecording = false;
                window.isRecording = false;
                window.isMicStarting = false;
                window.currentGeminiMessage = null;
                lastVoiceUserMessage = null;
                lastVoiceUserMessageTime = 0;

                // 停止静音检测
                stopSilenceDetection();

                // 清理输入analyser
                inputAnalyser = null;

                // 停止所有音频轨道
                if (stream) {
                    stream.getTracks().forEach(track => track.stop());
                    stream = null;
                }

                // 关闭AudioContext
                if (audioContext && audioContext.state !== 'closed') {
                    audioContext.close();
                    audioContext = null;
                    workletNode = null;
                }
            }

            // 重置模式切换标志
            if (isSwitchingMode) {
                console.log('WebSocket断开时重置模式切换标志');
                isSwitchingMode = false;
            }

            // 清理 session Promise resolver/rejecter，防止后续操作永远等待
            if (sessionStartedResolver || sessionStartedRejecter) {
                console.log('WebSocket断开时清理session Promise');
                if (sessionStartedRejecter) {
                    try {
                        sessionStartedRejecter(new Error('WebSocket连接断开'));
                    } catch (e) {
                        // 忽略已经处理的reject
                    }
                }
                sessionStartedResolver = null;
                sessionStartedRejecter = null;
            }

            // 清理session超时定时器
            if (window.sessionTimeoutId) {
                clearTimeout(window.sessionTimeoutId);
                window.sessionTimeoutId = null;
            }

            // 清理音频队列
            (async () => {
                await clearAudioQueue();
            })();

            // 隐藏语音准备提示
            hideVoicePreparingToast();

            // 移除按钮的active/recording类
            micButton.classList.remove('active');
            micButton.classList.remove('recording');
            screenButton.classList.remove('active');

            // 恢复按钮状态，确保用户可以继续操作
            micButton.disabled = false;
            textSendButton.disabled = false;
            textInputBox.disabled = false;
            screenshotButton.disabled = false;

            // 禁用语音控制按钮（因为没有活跃的语音会话）
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = true;
            returnSessionButton.disabled = true;

            // 确保文本输入区可见
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
            }

            // 同步浮动按钮状态
            syncFloatingMicButtonState(false);
            syncFloatingScreenButtonState(false);

            // 如果不是正在切换猫娘，才自动重连（避免与手动重连冲突）
            if (!isSwitchingCatgirl) {
                // 保存 setTimeout ID，以便在 handleCatgirlSwitch 中取消
                autoReconnectTimeoutId = setTimeout(connectWebSocket, 3000);
            }
        };

        socket.onerror = (error) => {
            console.error(window.t('console.websocketError'), error);
        };
    }

    connectWebSocket();

    // 初始化 BroadcastChannel 用于跨页面通信（与 model_manager 通信）
    let nekoBroadcastChannel = null;
    try {
        if (typeof BroadcastChannel !== 'undefined') {
            nekoBroadcastChannel = new BroadcastChannel('neko_page_channel');
            console.log('[BroadcastChannel] 主页面 BroadcastChannel 已初始化');

            nekoBroadcastChannel.onmessage = async function (event) {
                if (!event.data || !event.data.action) {
                    return;
                }

                console.log('[BroadcastChannel] 收到消息:', event.data.action);

                switch (event.data.action) {
                    case 'reload_model':
                        await handleModelReload(event.data?.lanlan_name);
                        break;
                    case 'hide_main_ui':
                        handleHideMainUI();
                        break;
                    case 'show_main_ui':
                        handleShowMainUI();
                        break;
                    case 'memory_edited':
                        console.log(window.t('console.memoryEditedRefreshContext'), event.data.catgirl_name);
                        (async () => {
                            // 记录之前是否在语音模式
                            const wasRecording = isRecording;

                            // 停止当前语音捕获
                            if (isRecording) {
                                stopMicCapture();
                            }

                            // 向后端发送 end_session，确保服务器丢弃旧上下文
                            if (socket && socket.readyState === WebSocket.OPEN) {
                                socket.send(JSON.stringify({ action: 'end_session' }));
                                console.log('[Memory] 已向后端发送 end_session');
                            }

                            // 如果是文本模式，重置会话状态，下次发送文本时会重新获取上下文
                            if (isTextSessionActive) {
                                isTextSessionActive = false;
                                console.log('[Memory] 文本会话已重置，下次发送将重新加载上下文');
                            }
                            // 停止正在播放的AI语音回复（等待音频解码/重置完成，避免与后续重连流程竞争）
                            if (typeof clearAudioQueue === 'function') {
                                try {
                                    await clearAudioQueue();
                                } catch (e) {
                                    console.error('[Memory] clearAudioQueue 失败:', e);
                                }
                            }

                            // 如果之前是语音模式，等待 session 结束后通过完整启动流程重新连接
                            if (wasRecording) {
                                showStatusToast(window.t ? window.t('memory.refreshingContext') : '正在刷新上下文...', 3000);
                                // 等待后端 session 完全结束
                                await new Promise(resolve => setTimeout(resolve, 1500));
                                // 通过 micButton.click() 触发完整启动流程
                                // （发送 start_session、等待 session_started、再初始化麦克风）
                                try {
                                    micButton.click();
                                } catch (e) {
                                    console.error('[Memory] 自动重连语音失败:', e);
                                }
                            } else {
                                // 显示提示
                                showStatusToast(window.t ? window.t('memory.refreshed') : '记忆已更新，下次对话将使用新记忆', 4000);
                            }
                        })();
                        break;
                }
            };
        }
    } catch (e) {
        console.log('[BroadcastChannel] 初始化失败，将使用 postMessage 后备方案:', e);
    }

    function cleanupLive2DOverlayUI() {
        const live2dManager = window.live2dManager;

        if (live2dManager) {
            if (live2dManager._lockIconTicker && live2dManager.pixi_app?.ticker) {
                try {
                    live2dManager.pixi_app.ticker.remove(live2dManager._lockIconTicker);
                } catch (_) {
                    // ignore
                }
                live2dManager._lockIconTicker = null;
            }
            if (live2dManager._floatingButtonsTicker && live2dManager.pixi_app?.ticker) {
                try {
                    live2dManager.pixi_app.ticker.remove(live2dManager._floatingButtonsTicker);
                } catch (_) {
                    // ignore
                }
                live2dManager._floatingButtonsTicker = null;
            }
            if (live2dManager._floatingButtonsResizeHandler) {
                window.removeEventListener('resize', live2dManager._floatingButtonsResizeHandler);
                live2dManager._floatingButtonsResizeHandler = null;
            }
            if (live2dManager.tutorialProtectionTimer) {
                clearInterval(live2dManager.tutorialProtectionTimer);
                live2dManager.tutorialProtectionTimer = null;
            }
            live2dManager._floatingButtonsContainer = null;
            live2dManager._returnButtonContainer = null;
            live2dManager._lockIconElement = null;
            live2dManager._lockIconImages = null;
        }

        document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container')
            .forEach(el => el.remove());
    }

    function cleanupVRMOverlayUI() {
        if (window.vrmManager && typeof window.vrmManager.cleanupUI === 'function') {
            window.vrmManager.cleanupUI();
            return;
        }
        document.querySelectorAll('#vrm-floating-buttons, #vrm-lock-icon, #vrm-return-button-container')
            .forEach(el => el.remove());
    }

    // 模型重载处理函数
    async function handleModelReload(targetLanlanName = '') {
        // 如果消息携带了 lanlan_name，且与当前页面角色不一致，则忽略（避免配置其它角色时影响当前主界面）
        const currentLanlanName = window.lanlan_config?.lanlan_name || '';
        if (targetLanlanName && currentLanlanName && targetLanlanName !== currentLanlanName) {
            console.log('[Model] 忽略来自其它角色的模型重载请求:', { targetLanlanName, currentLanlanName });
            return;
        }

        // 并发控制：如果已有重载正在进行，记录待处理的请求并等待
        if (window._modelReloadInFlight) {
            console.log('[Model] 模型重载已在进行中，等待完成后重试');
            window._pendingModelReload = true;
            await window._modelReloadPromise;
            return;
        }

        // 设置重载标志
        window._modelReloadInFlight = true;
        window._pendingModelReload = false;

        // 创建 Promise 供其他调用者等待
        let resolveReload;
        window._modelReloadPromise = new Promise(resolve => {
            resolveReload = resolve;
        });

        console.log('[Model] 开始热切换模型');

        try {
            // 1. 重新获取页面配置
            const nameForConfig = targetLanlanName || currentLanlanName;
            const pageConfigUrl = nameForConfig
                ? `/api/config/page_config?lanlan_name=${encodeURIComponent(nameForConfig)}`
                : '/api/config/page_config';
            const response = await fetch(pageConfigUrl);
            const data = await response.json();

            if (data.success) {
                const newModelPath = data.model_path || '';
                const newModelType = (data.model_type || 'live2d').toLowerCase();
                const oldModelType = window.lanlan_config?.model_type || 'live2d';

                console.log('[Model] 模型切换:', {
                    oldType: oldModelType,
                    newType: newModelType,
                    newPath: newModelPath
                });

                // 验证模型路径：如果为空，保持当前状态不变
                if (!newModelPath) {
                    console.warn('[Model] 模型路径为空，保持当前模型不变');
                    showStatusToast(window.t ? window.t('app.modelPathEmpty') : '模型路径为空', 2000);
                    return;
                }

                if (oldModelType !== newModelType) {
                    if (newModelType === 'vrm') {
                        cleanupLive2DOverlayUI();
                    } else {
                        cleanupVRMOverlayUI();
                    }
                }

                // 2. 更新全局配置
                if (window.lanlan_config) {
                    window.lanlan_config.model_type = newModelType;
                }

                // 3. 根据模型类型切换
                if (newModelType === 'vrm') {
                    window.vrmModel = newModelPath;
                    window.cubism4Model = '';

                    // 隐藏 Live2D
                    console.log('[Model] 隐藏 Live2D 模型');
                    const live2dContainer = document.getElementById('live2d-container');
                    if (live2dContainer) {
                        live2dContainer.style.display = 'none';
                        live2dContainer.classList.add('hidden');
                    }

                    // 显示并重新加载 VRM 模型
                    console.log('[Model] 加载 VRM 模型:', newModelPath);

                    // 显示 VRM 容器
                    const vrmContainer = document.getElementById('vrm-container');
                    if (vrmContainer) {
                        vrmContainer.classList.remove('hidden');
                        vrmContainer.style.display = 'block';
                        vrmContainer.style.visibility = 'visible';
                        vrmContainer.style.removeProperty('pointer-events');
                    }

                    // 显示 VRM canvas
                    const vrmCanvas = document.getElementById('vrm-canvas');
                    if (vrmCanvas) {
                        vrmCanvas.style.visibility = 'visible';
                        vrmCanvas.style.pointerEvents = 'auto';
                    }

                    // 检查 VRM 管理器是否已初始化
                    if (!window.vrmManager) {
                        console.log('[Model] VRM 管理器未初始化，等待初始化完成');
                        // 等待 VRM 初始化完成
                        if (typeof initVRMModel === 'function') {
                            await initVRMModel();
                        }
                    }

                    // 加载新模型
                    if (window.vrmManager) {
                        await window.vrmManager.loadModel(newModelPath);

                        // 应用光照配置（如果有）
                        if (window.lanlan_config?.lighting && typeof window.applyVRMLighting === 'function') {
                            window.applyVRMLighting(window.lanlan_config.lighting, window.vrmManager);
                        }
                    } else {
                        console.error('[Model] VRM 管理器初始化失败');
                    }
                } else {
                    // Live2D 模式
                    window.cubism4Model = newModelPath;
                    window.vrmModel = '';

                    // 隐藏 VRM
                    console.log('[Model] 隐藏 VRM 模型');
                    const vrmContainer = document.getElementById('vrm-container');
                    if (vrmContainer) {
                        vrmContainer.style.display = 'none';
                        vrmContainer.classList.add('hidden');
                    }
                    const vrmCanvas = document.getElementById('vrm-canvas');
                    if (vrmCanvas) {
                        vrmCanvas.style.visibility = 'hidden';
                        vrmCanvas.style.pointerEvents = 'none';
                    }

                    // 显示并重新加载 Live2D 模型
                    if (newModelPath) {
                        console.log('[Model] 加载 Live2D 模型:', newModelPath);

                        // 显示 Live2D 容器
                        const live2dContainer = document.getElementById('live2d-container');
                        if (live2dContainer) {
                            live2dContainer.classList.remove('hidden');
                            live2dContainer.style.display = 'block';
                        }

                        // 检查 Live2D 管理器是否已初始化
                        if (!window.live2dManager) {
                            console.log('[Model] Live2D 管理器未初始化，等待初始化完成');
                            // 等待 Live2D 初始化完成
                            if (typeof initLive2DModel === 'function') {
                                await initLive2DModel();
                            }
                        }

                        // 加载新模型
                        if (window.live2dManager) {
                            // 确保 PIXI 应用已初始化
                            if (!window.live2dManager.pixi_app) {
                                console.log('[Model] PIXI 应用未初始化，正在初始化...');
                                await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
                            }

                            // 关键修复：应用用户已保存的偏好（位置/缩放/参数等），避免从模型管理页返回后“复位”
                            let modelPreferences = null;
                            try {
                                const preferences = await window.live2dManager.loadUserPreferences();
                                modelPreferences = preferences ? preferences.find(p => p && p.model_path === newModelPath) : null;
                            } catch (prefError) {
                                console.warn('[Model] 读取 Live2D 用户偏好失败，将继续加载模型:', prefError);
                            }

                            // loadModel 支持直接传入模型路径字符串（与 live2d-init.js 一致）
                            await window.live2dManager.loadModel(newModelPath, {
                                preferences: modelPreferences,
                                isMobile: window.innerWidth <= 768
                            });

                            // 同步全局引用，保持兼容旧接口
                            if (window.LanLan1) {
                                window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                                window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                            }
                        } else {
                            console.error('[Model] Live2D 管理器初始化失败');
                        }
                    }
                }

                // 4. 显示成功提示
                showStatusToast(window.t ? window.t('app.modelSwitched') : '模型已切换', 2000);
            } else {
                console.error('[Model] 获取页面配置失败:', data.error);
                showStatusToast(window.t ? window.t('app.modelSwitchFailed') : '模型切换失败', 3000);
            }
        } catch (error) {
            console.error('[Model] 模型热切换失败:', error);
            showStatusToast(window.t ? window.t('app.modelSwitchFailed') : '模型切换失败', 3000);
        } finally {
            // 清理重载标志
            window._modelReloadInFlight = false;
            resolveReload();

            // 如果有待处理的重载请求，执行一次
            if (window._pendingModelReload) {
                console.log('[Model] 执行待处理的模型重载请求');
                window._pendingModelReload = false;
                // 使用 setTimeout 避免递归调用栈过深
                setTimeout(() => handleModelReload(), 100);
            }
        }
    }

    // 隐藏主界面模型渲染（进入模型管理界面时调用）
    function handleHideMainUI() {
        console.log('[UI] 隐藏主界面并暂停渲染');

        try {
            // 隐藏 Live2D
            const live2dContainer = document.getElementById('live2d-container');
            if (live2dContainer) {
                live2dContainer.style.display = 'none';
                live2dContainer.classList.add('hidden');
            }

            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                live2dCanvas.style.visibility = 'hidden';
                live2dCanvas.style.pointerEvents = 'none';
            }

            // 隐藏 VRM
            const vrmContainer = document.getElementById('vrm-container');
            if (vrmContainer) {
                vrmContainer.style.display = 'none';
                vrmContainer.classList.add('hidden');
            }

            const vrmCanvas = document.getElementById('vrm-canvas');
            if (vrmCanvas) {
                vrmCanvas.style.visibility = 'hidden';
                vrmCanvas.style.pointerEvents = 'none';
            }

            // 暂停渲染循环以节省资源
            if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                window.vrmManager.pauseRendering();
            }

            if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                window.live2dManager.pauseRendering();
            }
        } catch (error) {
            console.error('[UI] 隐藏主界面失败:', error);
        }
    }

    // 显示主界面模型渲染（返回主页时调用）
    function handleShowMainUI() {
        console.log('[UI] 显示主界面并恢复渲染');

        try {
            const currentModelType = window.lanlan_config?.model_type || 'live2d';
            console.log('[UI] 当前模型类型:', currentModelType);

            if (currentModelType === 'vrm') {
                // 显示 VRM
                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'block';
                    vrmContainer.classList.remove('hidden');
                    console.log('[UI] VRM 容器已显示，display:', vrmContainer.style.display);
                }

                const vrmCanvas = document.getElementById('vrm-canvas');
                if (vrmCanvas) {
                    vrmCanvas.style.visibility = 'visible';
                    vrmCanvas.style.pointerEvents = 'auto';
                    console.log('[UI] VRM canvas 已显示，visibility:', vrmCanvas.style.visibility);
                }

                // 恢复 VRM 渲染循环
                if (window.vrmManager && typeof window.vrmManager.resumeRendering === 'function') {
                    window.vrmManager.resumeRendering();
                }
            } else {
                // 显示 Live2D
                const live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'block';
                    live2dContainer.classList.remove('hidden');
                    console.log('[UI] Live2D 容器已显示，display:', live2dContainer.style.display);
                }

                const live2dCanvas = document.getElementById('live2d-canvas');
                if (live2dCanvas) {
                    live2dCanvas.style.visibility = 'visible';
                    live2dCanvas.style.pointerEvents = 'auto';
                    console.log('[UI] Live2D canvas 已显示，visibility:', live2dCanvas.style.visibility);
                }

                // 恢复 Live2D 渲染循环
                if (window.live2dManager && typeof window.live2dManager.resumeRendering === 'function') {
                    window.live2dManager.resumeRendering();
                }
            }
        } catch (error) {
            console.error('[UI] 显示主界面失败:', error);
        }
    }

    // 监听记忆编辑通知（从 memory_browser iframe 发送 - postMessage 后备方案）
    window.addEventListener('message', async function (event) {
        // 安全检查：验证消息来源
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的 memory_edited 消息:', event.origin);
            return;
        }

        if (event.data && event.data.type === 'memory_edited') {
            console.log(window.t('console.memoryEditedRefreshContext'), event.data.catgirl_name);

            // 记录之前是否在语音模式
            const wasRecording = isRecording;

            // 停止当前语音捕获
            if (isRecording) {
                stopMicCapture();
            }
            // 向后端发送 end_session，确保服务器丢弃旧上下文
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({ action: 'end_session' }));
                console.log('[Memory] 已向后端发送 end_session');
            }
            // 如果是文本模式，重置会话状态，下次发送文本时会重新获取上下文
            if (isTextSessionActive) {
                isTextSessionActive = false;
                console.log('[Memory] 文本会话已重置，下次发送将重新加载上下文');
            }
            // 停止正在播放的AI语音回复（等待完成，避免竞态条件）
            if (typeof clearAudioQueue === 'function') {
                try {
                    await clearAudioQueue();
                } catch (e) {
                    console.error('[Memory] clearAudioQueue 失败:', e);
                }
            }

            // 如果之前是语音模式，等待 session 结束后自动重新连接
            if (wasRecording) {
                showStatusToast(window.t ? window.t('memory.refreshingContext') : '正在刷新上下文...', 3000);
                // 等待后端 session 完全结束
                await new Promise(resolve => setTimeout(resolve, 1500));
                // 通过 micButton.click() 触发完整启动流程
                try {
                    micButton.click();
                } catch (e) {
                    console.error('[Memory] 自动重连语音失败:', e);
                }
            } else {
                // 显示提示
                showStatusToast(window.t ? window.t('memory.refreshed') : '记忆已更新，下次对话将使用新记忆', 4000);
            }
        }
    });

    // 监听模型保存通知（从 model_manager 窗口发送 - postMessage 后备方案）
    window.addEventListener('message', async function (event) {
        // 安全检查：验证消息来源
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的消息:', event.origin);
            return;
        }

        // 验证消息来源是否为预期的窗口（opener 或其他已知窗口）
        if (event.source && event.source !== window.opener && !event.source.parent) {
            console.warn('[Security] 拒绝来自未知窗口的消息');
            return;
        }

        if (event.data && (event.data.action === 'model_saved' || event.data.action === 'reload_model')) {
            console.log('[Model] 通过 postMessage 收到模型重载通知');
            await handleModelReload(event.data?.lanlan_name);
        }
    });

    function getCurrentTimeString() {
        return new Date().toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    function createGeminiBubble(sentence) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', 'gemini');
        const cleanSentence = (sentence || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
        messageDiv.textContent = "[" + getCurrentTimeString() + "] 🎀 " + cleanSentence;
        chatContainer.appendChild(messageDiv);
        window.currentGeminiMessage = messageDiv;

        // ========== 新增：追踪本轮气泡 ==========
        window.currentTurnGeminiBubbles.push(messageDiv);
        // ========== 追踪结束 ==========

        // 检测AI消息的语言，如果与用户语言不同，显示字幕提示框
        checkAndShowSubtitlePrompt(cleanSentence);

        // 如果是AI第一次回复，更新状态并检查成就
        if (isFirstAIResponse) {
            isFirstAIResponse = false;
            console.log(window.t('console.aiFirstReplyDetected'));
            checkAndUnlockFirstDialogueAchievement();
        }
    }

    async function processRealisticQueue(queueVersion = window._realisticGeminiVersion || 0) {
        if (window._isProcessingRealisticQueue) return;
        window._isProcessingRealisticQueue = true;

        try {
            while (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                if ((window._realisticGeminiVersion || 0) !== queueVersion) {
                    break;
                }
                // 基于时间戳的延迟：确保每句之间至少间隔2秒
                const now = Date.now();
                const timeSinceLastBubble = now - (window._lastBubbleTime || 0);
                if (window._lastBubbleTime > 0 && timeSinceLastBubble < 2000) {
                    await new Promise(resolve => setTimeout(resolve, 2000 - timeSinceLastBubble));
                }

                if ((window._realisticGeminiVersion || 0) !== queueVersion) {
                    break;
                }

                const s = window._realisticGeminiQueue.shift();
                if (s && (window._realisticGeminiVersion || 0) === queueVersion) {
                    createGeminiBubble(s);
                    chatContainer.scrollTop = chatContainer.scrollHeight;
                    window._lastBubbleTime = Date.now();
                }
            }
        } finally {
            window._isProcessingRealisticQueue = false;
            // 兜底检查：如果在循环结束到重置标志位之间又有新消息进入队列，递归触发
            if (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                processRealisticQueue(window._realisticGeminiVersion || 0);
            }
        }
    }

    let _lastPlayedMusicUrl = null;
    let _lastMusicPlayTime = 0;

    window.dispatchMusicPlay = function (trackInfo) {
        if (!trackInfo || !trackInfo.url) {
            console.warn('[MusicDispatch] 无效的音乐信息，跳过播放');
            return;
        }

        const now = Date.now();
        const musicUrl = trackInfo.url;

        if (_lastPlayedMusicUrl === musicUrl && (now - _lastMusicPlayTime) < 5000) {
            console.log('[MusicDispatch] 5秒内相同音乐，跳过播放:', trackInfo.name);
            return;
        }

        if (window.sendMusicMessage) {
            const accepted = window.sendMusicMessage(trackInfo);
            if (accepted) {
                _lastPlayedMusicUrl = musicUrl;
                _lastMusicPlayTime = now;
                if (window.showStatusToast) {
                    const playMsg = window.t('music.nowPlaying', { name: trackInfo.name, defaultValue: '为您播放: ' + trackInfo.name });
                    window.showStatusToast(playMsg, 3000);
                }
            }
        } else {
            console.warn('[MusicDispatch] sendMusicMessage 尚未就绪，启动双保险等待...');

            // 逻辑：每 500ms 检查一次，或者监听到事件时触发
            const retryPlay = () => {
                if (window.sendMusicMessage) {
                    console.log('[MusicDispatch] 检测到接口已就绪，正在补发播放请求...');
                    clearInterval(pollTimer);
                    window.removeEventListener('music-ui-ready', retryPlay);
                    // 重新执行播放逻辑
                    window.dispatchMusicPlay(trackInfo);
                    return true;
                }
                return false;
            };

            // 保险 1：定时轮询 (防止错过事件)
            const pollTimer = setInterval(retryPlay, 500);

            // 保险 2：事件监听
            window.addEventListener('music-ui-ready', retryPlay, { once: true });

            // 5秒后如果还没加载出来，放弃轮询防止内存泄漏
            setTimeout(() => clearInterval(pollTimer), 5000);
        }
    };

    /**
     * 解析并处理 AI 文本中的音乐播放指令
     * 
     * 【当前状态 - 预留功能】
     * 此函数目前未被调用。当前主动搭话音乐功能走的是另一条路径：
     *   后端 proactive_chat_prompt_music → 返回搜索关键词 → 后端搜索 → source_links → 前端播放
     * 
     * 【未来用途】
     * 当需要在普通对话中让 AI 主动触发音乐播放时，需要在角色系统提示词中添加指令说明，
     * 让 AI 输出 [play_music: {"name": "歌曲名", "artist": "歌手名"}] 格式的指令。
     * 届时在消息处理流程中调用此函数即可解析并播放音乐。
     * 
     * 【指令格式】
     * [play_music: {"name": "歌曲名", "artist": "歌手名"}]
     * - name: 必填，歌曲名称
     * - artist: 可选，歌手名称
     * 
     * @param {string} text - 可能包含音乐指令的文本
     */
    window.processMusicCommands = async function (text) {
        if (!text) return;
        const musicRegex = /\[play_music:\s*({[\s\S]*?})\]/g;
        let match;

        while ((match = musicRegex.exec(text)) !== null) {
            try {
                // 1. 解析 AI 传来的意图信息（通常只有 name 和 artist）
                const aiTrackInfo = JSON.parse(match[1]);

                // 校验 name 字段是否存在
                if (!aiTrackInfo.name) {
                    console.warn('[Music Parser] 缺少 name 字段，跳过:', match[1]);
                    continue;
                }

                const query = `${aiTrackInfo.name} ${aiTrackInfo.artist || ''}`.trim();

                if (query) {
                    // 【核心修复1】在发出请求前增加并锁定当前纪元
                    const myEpoch = ++currentMusicSearchEpoch;

                    const response = await fetch(`/api/music/search?query=${encodeURIComponent(query)}`);
                    const result = await response.json();

                    // 【核心修复2】门闩校验：如果纪元对不上（说明期间切猫或打断了），直接丢弃该结果
                    if (myEpoch !== currentMusicSearchEpoch) {
                        console.log(`[Music] 指令搜索结果过时，已丢弃: "${query}"`);
                        continue; // 注意：如果这里不是 for 循环内部而是回调，请改为 return;
                    }

                    // 【核心修复3】细化错误区分：服务报错 vs 没搜到
                    if (!result.success) {
                        console.error('[Music] Search API failed:', result.error);
                        if (window.showStatusToast) {
                            const failMsg = window.safeT ? window.safeT('music.searchFailed', '音乐搜索失败') : '音乐搜索失败';
                            window.showStatusToast(result.message || result.error || failMsg, 3000);
                        }
                        continue; // 或者 return;
                    }

                    // 正常命中结果
                    if (result.data && result.data.length > 0) {
                        const realTrack = result.data[0];
                        console.log('[Music] 指令搜歌命中:', realTrack.name);

                        // 调用主分支统一的播放调度逻辑
                        if (typeof window.dispatchMusicPlay === 'function') {
                            window.dispatchMusicPlay(realTrack);
                        } else {
                            console.warn('[Music] dispatchMusicPlay 不可用，尝试直接发送');
                            window.sendMusicMessage(realTrack);
                        }
                    } else {
                        // 【修复】直接使用 window.t 并传入 query 参数，配合你新改的 JSON 占位符
                        if (window.showStatusToast) {
                            const notFoundMsg = window.t('music.notFound', {
                                query: aiTrackInfo.name,
                                defaultValue: `找不到歌曲: ${aiTrackInfo.name}`
                            });
                            window.showStatusToast(notFoundMsg, 3000);
                        }
                    }
                }
            } catch (e) {
                console.error('[Music Parser] 音乐指令解析或请求失败:', e);
            }
        }
    }

    // 添加消息到聊天界面
    function appendMessage(text, sender, isNewMessage = true) {
        function isMergeMessagesEnabled() {
            if (typeof window.mergeMessagesEnabled !== 'undefined') return window.mergeMessagesEnabled;
            return mergeMessagesEnabled;
        }

        function normalizeGeminiText(s) {
            return (s || '').replace(/\r\n/g, '\n');
        }

        function cleanMusicFromChunk(rawText) {
            let s = normalizeGeminiText(rawText);
            if (window._pendingMusicCommand) {
                s = window._pendingMusicCommand + s;
                window._pendingMusicCommand = '';
            }
            const m = s.match(/\[[^\]]*$/);
            if (m) {
                const partial = m[0].toLowerCase();
                const target = "[play_music:";
                if (partial.startsWith(target) || target.startsWith(partial)) {
                    window._pendingMusicCommand = m[0];
                    s = s.slice(0, m.index);
                }
            }
            return s.replace(/\[play_music:[^\]]*(\]|$)/g, '');
        }

        function splitIntoSentences(buffer) {
            // 逐字符扫描，尽量兼容中英文标点与流式输入
            const sentences = [];
            const s = normalizeGeminiText(buffer);
            let start = 0;

            const isPunctForBoundary = (ch) => {
                return ch === '。' || ch === '！' || ch === '？' || ch === '!' || ch === '?' || ch === '.' || ch === '…';
            };

            const isBoundary = (ch, next) => {
                if (ch === '\n') return true;
                // 连续标点只在最后一个标点处分段，避免 "！？"、"..." 被拆开
                if (isPunctForBoundary(ch) && next && isPunctForBoundary(next)) return false;
                if (ch === '。' || ch === '！' || ch === '？') return true;
                if (ch === '!' || ch === '?') return true;
                if (ch === '…') return true;
                if (ch === '.') {
                    // 英文句点：尽量避免把小数/缩写切断，要求后面是空白/换行/结束/常见结束符
                    if (!next) return true;
                    return /\s|\n|["')\]]/.test(next);
                }
                return false;
            };

            for (let i = 0; i < s.length; i++) {
                const ch = s[i];
                const next = i + 1 < s.length ? s[i + 1] : '';
                if (isBoundary(ch, next)) {
                    const piece = s.slice(start, i + 1);
                    const trimmed = piece.replace(/^\s+/, '').replace(/\s+$/, '');
                    if (trimmed) sentences.push(trimmed);
                    start = i + 1;
                }
            }

            const rest = s.slice(start);
            return { sentences, rest };
        }

        // 维护“本轮 AI 回复”的完整文本（用于 turn end 时整段翻译/情感分析）
        if (sender === 'gemini') {
            if (isNewMessage) {
                window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
                window._geminiTurnFullText = '';
                window._pendingMusicCommand = '';
                // ========== 新增：重置本轮气泡追踪 ==========
                window.currentTurnGeminiBubbles = [];
                // ========== 重置结束 ==========
            }
            const prevFull = typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '';
            window._geminiTurnFullText = prevFull + normalizeGeminiText(text);
        }

        if (sender === 'gemini' && !isMergeMessagesEnabled()) {
            // 拟真输出（合并消息关闭）：流式内容先缓冲，按句号/问号/感叹号/换行等切分，每句一个气泡
            if (isNewMessage) {
                window._realisticGeminiBuffer = '';
                window._realisticGeminiQueue = []; // 新一轮开始时，清空队列
                window._lastBubbleTime = 0; // 重置时间戳，第一句立即显示
                window._pendingMusicCommand = ''; // 新一轮开始时，清空待闭合的音乐指令
            }

            let incoming = normalizeGeminiText(text);

            // 处理未闭合的音乐指令片段
            if (window._pendingMusicCommand) {
                incoming = window._pendingMusicCommand + incoming;
                window._pendingMusicCommand = '';
            }

            // 捕获字符串末尾尚未闭合的任意中括号块（防止 JSON 片段泄漏到聊天气泡）
            const openBracketMatch = incoming.match(/\[[^\]]*$/);
            if (openBracketMatch) {
                const partialText = openBracketMatch[0];
                const normalizedPartial = normalizeGeminiText(partialText).toLowerCase();

                // 这样即使只收到 "[" 或 "[pl"，或者已经包含了部分 JSON 体
                const targetPrefix = "[play_music:";
                const isPlayMusicPrefix =
                    normalizedPartial.startsWith(targetPrefix) ||
                    targetPrefix.startsWith(normalizedPartial);

                if (isPlayMusicPrefix) {
                    window._pendingMusicCommand = partialText;
                    incoming = incoming.slice(0, openBracketMatch.index);
                    console.log(`[Music] 拦截到不完整指令片段: ${partialText}`);
                }
            }

            const prev = typeof window._realisticGeminiBuffer === 'string' ? window._realisticGeminiBuffer : '';
            let combined = prev + incoming;
            combined = combined.replace(/\[play_music:[^\]]*(\]|$)/g, '');

            const { sentences, rest } = splitIntoSentences(combined);
            window._realisticGeminiBuffer = rest;

            if (sentences.length > 0) {
                window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                window._realisticGeminiQueue.push(...sentences);
                processRealisticQueue(window._realisticGeminiVersion || 0);
            }
        } else if (sender === 'gemini' && isMergeMessagesEnabled() && isNewMessage) {
            // 合并消息开启：新一轮开始时，清空拟真缓冲，防止残留
            window._realisticGeminiBuffer = '';
            window._realisticGeminiQueue = [];
            window._lastBubbleTime = 0;

            // 1. 清洗文本（含未闭合指令片段的拦截）
            const cleanNewText = cleanMusicFromChunk(text);

            // 2. 只有当清洗后还有实质性文本时，才去创建气泡 DOM；否则清空指针以避免误追加
            if (cleanNewText.trim()) {
                const messageDiv = document.createElement('div');
                messageDiv.classList.add('message', 'gemini');
                messageDiv.textContent = "[" + getCurrentTimeString() + "] 🎀 " + cleanNewText;

                chatContainer.appendChild(messageDiv);
                window.currentGeminiMessage = messageDiv;

                // ========== 新增：追踪本轮气泡 ==========
                window.currentTurnGeminiBubbles.push(messageDiv);
                // ========== 追踪结束 ==========
            } else {
                window.currentGeminiMessage = null;
            }

            // 3. 移除多余的旧代码，只对干净的文本调用字幕检测
            checkAndShowSubtitlePrompt(cleanNewText);

            if (isFirstAIResponse) {
                isFirstAIResponse = false;
                console.log(window.t('console.aiFirstReplyDetected'));
                checkAndUnlockFirstDialogueAchievement();
            }
        } else if (sender === 'gemini' && isMergeMessagesEnabled()) {
            // 【核心重构】不再依赖 isNewMessage 标志，而是根据“本轮是否已有气泡”来决策。
            // 解决首个 chunk 被清洗为空（纯指令）时导致的渲染坠落 Bug
            const cleanText = cleanMusicFromChunk(text);

            // 场景 A: 本轮尚未创建气泡（可能是首个带文本的块，也可能是被指令切断后的后续块）
            if (!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0) {
                if (cleanText.trim()) {
                    const messageDiv = document.createElement('div');
                    messageDiv.classList.add('message', 'gemini');
                    messageDiv.textContent = "[" + getCurrentTimeString() + "] 🎀 " + cleanText;
                    chatContainer.appendChild(messageDiv);

                    window.currentGeminiMessage = messageDiv;
                    window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
                    window.currentTurnGeminiBubbles.push(messageDiv);

                    checkAndShowSubtitlePrompt(cleanText);
                } else {
                    // 仅有指令无文本，继续保持指针为空，直到出现有意义的文本块
                    window.currentGeminiMessage = null;
                }
            }
            // 场景 B: 气泡已存在，执行平滑追加
            else if (window.currentGeminiMessage && window.currentGeminiMessage.isConnected) {
                const fullText = window._geminiTurnFullText.replace(/\[play_music:[^\]]*(\]|$)/g, '');
                const timePrefix = window.currentGeminiMessage.textContent.match(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /) || [""];
                window.currentGeminiMessage.textContent = timePrefix[0] + fullText;

                // 触发原有的字幕检测逻辑
                if (subtitleCheckDebounceTimer) {
                    clearTimeout(subtitleCheckDebounceTimer);
                }

                subtitleCheckDebounceTimer = setTimeout(() => {
                    if (!window.currentGeminiMessage ||
                        window.currentGeminiMessage.nodeType !== Node.ELEMENT_NODE ||
                        !window.currentGeminiMessage.isConnected) {
                        subtitleCheckDebounceTimer = null;
                        return;
                    }

                    const currentFullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
                    if (currentFullText && currentFullText.trim()) {
                        if (userLanguage === null) {
                            getUserLanguage().then(() => {
                                if (window.currentGeminiMessage && window.currentGeminiMessage.isConnected) {
                                    const detectedLang = detectLanguage(currentFullText);
                                    if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                                        showSubtitlePrompt();
                                    }
                                }
                            }).catch(err => console.warn('[i18n] Stream error:', err));
                        } else {
                            const detectedLang = detectLanguage(currentFullText);
                            if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                                showSubtitlePrompt();
                            }
                        }
                    }
                    subtitleCheckDebounceTimer = null;
                }, 300);
            }
        } else {
            // 创建新消息
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('message', sender);

            // 根据sender设置不同的图标
            const icon = sender === 'user' ? '💬' : '🎀';
            const cleanText = (text || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
            messageDiv.textContent = "[" + getCurrentTimeString() + "] " + icon + " " + cleanText;
            chatContainer.appendChild(messageDiv);

            // 如果是Gemini消息，更新当前消息引用
            if (sender === 'gemini') {
                window.currentGeminiMessage = messageDiv;
                // ========== 新增：追踪本轮气泡 ==========
                window.currentTurnGeminiBubbles.push(messageDiv);
                // ========== 追踪结束 ==========

                // 检测AI消息的语言，如果与用户语言不同，显示字幕提示框
                checkAndShowSubtitlePrompt(cleanText);

                // 注意：翻译现在在消息完成时（turn end事件）立即执行，不再使用延迟机制

                // 如果是AI第一次回复，更新状态并检查成就
                if (isFirstAIResponse) {
                    isFirstAIResponse = false;
                    console.log('检测到AI第一次回复');
                    checkAndUnlockFirstDialogueAchievement();
                }
            }
        }
        chatContainer.scrollTop = chatContainer.scrollHeight;
    }


    // 全局变量用于缓存麦克风列表和缓存时间戳
    let cachedMicrophones = null;
    let cacheTimestamp = 0;
    const CACHE_DURATION = 30000; // 缓存30秒

    // 首次交互跟踪
    let isFirstUserInput = true; // 跟踪是否为用户第一次输入
    let isFirstAIResponse = true; // 跟踪是否为AI第一次回复

    // 检查并解锁首次对话成就
    async function checkAndUnlockFirstDialogueAchievement() {
        // 当用户和AI都完成首次交互后调用API
        if (!isFirstUserInput && !isFirstAIResponse) {
            try {
                console.log(window.t('console.firstConversationUnlockAchievement'));
                const response = await fetch('/api/steam/set-achievement-status/ACH_FIRST_DIALOGUE', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                });

                if (response.ok) {
                    console.log(window.t('console.achievementUnlockSuccess'));
                } else {
                    console.error(window.t('console.achievementUnlockFailed'));
                }
            } catch (error) {
                console.error(window.t('console.achievementUnlockError'), error);
            }
        }
    }

    // 麦克风选择器UI已移除（旧sidebar系统），保留核心函数供live2d.js浮动按钮系统使用

    // 选择麦克风
    async function selectMicrophone(deviceId) {
        selectedMicrophoneId = deviceId;

        // 获取设备名称用于状态提示
        let deviceName = '系统默认麦克风';
        if (deviceId) {
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                const audioInputs = devices.filter(device => device.kind === 'audioinput');
                const selectedDevice = audioInputs.find(device => device.deviceId === deviceId);
                if (selectedDevice) {
                    deviceName = selectedDevice.label || `麦克风 ${audioInputs.indexOf(selectedDevice) + 1}`;
                }
            } catch (error) {
                console.error(window.t('console.getDeviceNameFailed'), error);
            }
        }

        // 更新UI选中状态
        const options = document.querySelectorAll('.mic-option');
        options.forEach(option => {
            if ((option.classList.contains('default') && deviceId === null) ||
                (option.dataset.deviceId === deviceId && deviceId !== null)) {
                option.classList.add('selected');
            } else {
                option.classList.remove('selected');
            }
        });

        // 保存选择到服务器
        await saveSelectedMicrophone(deviceId);

        // 如果正在录音，先显示选择提示，然后延迟重启录音
        if (isRecording) {
            const wasRecording = isRecording;
            // 先显示选择提示
            showStatusToast(window.t ? window.t('app.deviceSelected', { device: deviceName }) : `已选择 ${deviceName}`, 3000);
            // 延迟重启录音，让用户看到选择提示

            // 保存需要恢复的状态
            const shouldRestartProactiveVision = proactiveVisionEnabled && isRecording;
            const shouldRestartScreening = videoSenderInterval !== undefined && videoSenderInterval !== null;

            // 防止并发切换导致状态混乱
            if (window._isSwitchingMicDevice) {
                console.warn(window.t('console.deviceSwitchingWait'));
                showStatusToast(window.t ? window.t('app.deviceSwitching') : '设备切换中...', 2000);
                return;
            }
            window._isSwitchingMicDevice = true;

            try {
                // 停止语音期间主动视觉定时
                stopProactiveVisionDuringSpeech();
                // 停止屏幕共享
                stopScreening();
                // 停止静音检测
                stopSilenceDetection();
                // 清理输入analyser
                inputAnalyser = null;
                // 停止所有轨道
                if (stream instanceof MediaStream) {
                    stream.getTracks().forEach(track => track.stop());
                    stream = null;
                }
                // 清理 AudioContext 本地资源
                if (audioContext) {
                    if (audioContext.state !== 'closed') {
                        await audioContext.close().catch((e) => console.warn(window.t('console.audioContextCloseFailed'), e));
                    }
                    audioContext = null;
                }
                workletNode = null;

                // 等待一小段时间，确保选择提示显示出来
                await new Promise(resolve => setTimeout(resolve, 500));

                if (wasRecording) {
                    await startMicCapture();

                    // 重启屏幕共享（如果之前正在共享）
                    if (shouldRestartScreening) {
                        if (typeof startScreenSharing === 'function') {
                            try {
                                await startScreenSharing();
                            } catch (e) {
                                console.warn(window.t('console.restartScreenShareFailed'), e);
                            }
                        }
                    }
                    // 重启主动视觉（如果之前已启用）
                    // 先检查并重新获取屏幕流（切换麦克风期间流可能已丢失）
                    if (shouldRestartProactiveVision) {
                        if (typeof acquireProactiveVisionStream === 'function') {
                            await acquireProactiveVisionStream();
                        }
                        startProactiveVisionDuringSpeech();
                    }
                }
            } catch (e) {
                console.error(window.t('console.switchMicrophoneFailed'), e);
                showStatusToast(window.t ? window.t('app.deviceSwitchFailed') : '设备切换失败', 3000);

                // 完整清理：重置状态
                isRecording = false;
                window.isRecording = false;

                // 重置所有按钮状态（参考 stopMicCapture 逻辑）
                micButton.classList.remove('recording', 'active');
                muteButton.classList.remove('recording', 'active');
                screenButton.classList.remove('active');
                if (stopButton) stopButton.classList.remove('recording', 'active');

                // 同步浮动按钮状态
                syncFloatingMicButtonState(false);
                syncFloatingScreenButtonState(false);

                // 启用/禁用按钮状态
                micButton.disabled = false;
                muteButton.disabled = true;
                screenButton.disabled = true;
                if (stopButton) stopButton.disabled = true;

                // 显示文本输入区域
                const textInputArea = document.getElementById('text-input-area');
                if (textInputArea) {
                    textInputArea.classList.remove('hidden');
                }

                // 清理资源
                stopScreening();
                stopSilenceDetection();
                inputAnalyser = null;

                if (stream instanceof MediaStream) {
                    stream.getTracks().forEach(track => track.stop());
                    stream = null;
                }

                if (audioContext) {
                    if (audioContext.state !== 'closed') {
                        await audioContext.close().catch((err) => console.warn('AudioContext close 失败:', err));
                    }
                    audioContext = null;
                }
                workletNode = null;

                // 通知后端
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ action: 'pause_session' }));
                }

                // 如果主动搭话已启用且选择了搭话方式，重置并开始定时
                if (proactiveChatEnabled && hasAnyChatModeEnabled()) {
                    lastUserInputTime = Date.now();
                    resetProactiveChatBackoff();
                }

                window._isSwitchingMicDevice = false;
                return;
            } finally {
                window._isSwitchingMicDevice = false;
            }
        } else {
            // 如果不在录音，直接显示选择提示
            showStatusToast(window.t ? window.t('app.deviceSelected', { device: deviceName }) : `已选择 ${deviceName}`, 3000);
        }
    }

    // 保存选择的麦克风到服务器和 localStorage
    async function saveSelectedMicrophone(deviceId) {
        try {
            if (deviceId) {
                localStorage.setItem('neko_selected_microphone', deviceId);
            } else {
                localStorage.removeItem('neko_selected_microphone');
            }
        } catch (e) { }

        try {
            const response = await fetch('/api/characters/set_microphone', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    microphone_id: deviceId
                })
            });

            if (!response.ok) {
                console.error(window.t('console.saveMicrophoneSelectionFailed'));
            }
        } catch (err) {
            console.error(window.t('console.saveMicrophoneSelectionError'), err);
        }
    }

    // 加载上次选择的麦克风（优先从 localStorage 加载，快速恢复）
    function loadSelectedMicrophone() {
        try {
            const saved = localStorage.getItem('neko_selected_microphone');
            if (saved) {
                selectedMicrophoneId = saved;
                console.log(`已加载麦克风设置: ${saved}`);
            }
        } catch (e) {
            selectedMicrophoneId = null;
        }
    }

    // 保存麦克风增益设置到 localStorage（保存分贝值）
    function saveMicGainSetting() {
        try {
            localStorage.setItem('neko_mic_gain_db', String(microphoneGainDb));
            console.log(`麦克风增益设置已保存: ${microphoneGainDb}dB`);
        } catch (err) {
            console.error('保存麦克风增益设置失败:', err);
        }
    }

    // 从 localStorage 加载麦克风增益设置
    function loadMicGainSetting() {
        try {
            const savedGainDb = localStorage.getItem('neko_mic_gain_db');
            if (savedGainDb !== null) {
                const gainDb = parseFloat(savedGainDb);
                // 验证增益值在有效范围内
                if (!isNaN(gainDb) && gainDb >= MIN_MIC_GAIN_DB && gainDb <= MAX_MIC_GAIN_DB) {
                    microphoneGainDb = gainDb;
                    console.log(`已加载麦克风增益设置: ${microphoneGainDb}dB`);
                } else {
                    console.warn(`无效的增益值 ${savedGainDb}dB，使用默认值 ${DEFAULT_MIC_GAIN_DB}dB`);
                    microphoneGainDb = DEFAULT_MIC_GAIN_DB;
                }
            } else {
                console.log(`未找到麦克风增益设置，使用默认值 ${DEFAULT_MIC_GAIN_DB}dB`);
            }
        } catch (err) {
            console.error('加载麦克风增益设置失败:', err);
            microphoneGainDb = DEFAULT_MIC_GAIN_DB;
        }
    }

    // 更新麦克风增益（供外部调用，参数为分贝值）
    window.setMicrophoneGain = function (gainDb) {
        if (gainDb >= MIN_MIC_GAIN_DB && gainDb <= MAX_MIC_GAIN_DB) {
            microphoneGainDb = gainDb;
            if (micGainNode) {
                micGainNode.gain.value = dbToLinear(gainDb);
            }
            saveMicGainSetting();
            // 更新 UI 滑块（如果存在）
            const slider = document.getElementById('mic-gain-slider');
            const valueDisplay = document.getElementById('mic-gain-value');
            if (slider) slider.value = String(gainDb);
            if (valueDisplay) valueDisplay.textContent = formatGainDisplay(gainDb);
            console.log(`麦克风增益已设置: ${gainDb}dB`);
        }
    };

    // 获取当前麦克风增益（返回分贝值）
    window.getMicrophoneGain = function () {
        return microphoneGainDb;
    };

    // 格式化增益显示（带正负号）
    function formatGainDisplay(db) {
        if (db > 0) {
            return `+${db}dB`;
        } else if (db === 0) {
            return '0dB';
        } else {
            return `${db}dB`;
        }
    }

    // ========== 扬声器音量控制 ==========

    // 保存扬声器音量到 localStorage
    function saveSpeakerVolumeSetting() {
        try {
            localStorage.setItem('neko_speaker_volume', String(speakerVolume));
            console.log(`扬声器音量设置已保存: ${speakerVolume}%`);
        } catch (err) {
            console.error('保存扬声器音量设置失败:', err);
        }
    }

    // 从 localStorage 加载扬声器音量设置
    function loadSpeakerVolumeSetting() {
        try {
            const saved = localStorage.getItem('neko_speaker_volume');
            if (saved !== null) {
                const vol = parseInt(saved, 10);
                if (!isNaN(vol) && vol >= 0 && vol <= 100) {
                    speakerVolume = vol;
                    console.log(`已加载扬声器音量设置: ${speakerVolume}%`);
                } else {
                    console.warn(`无效的扬声器音量值 ${saved}，使用默认值 ${DEFAULT_SPEAKER_VOLUME}%`);
                    speakerVolume = DEFAULT_SPEAKER_VOLUME;
                }
            } else {
                console.log(`未找到扬声器音量设置，使用默认值 ${DEFAULT_SPEAKER_VOLUME}%`);
                speakerVolume = DEFAULT_SPEAKER_VOLUME;
            }

            // 立即应用到音频管道（如果已初始化）
            if (speakerGainNode) {
                speakerGainNode.gain.setTargetAtTime(speakerVolume / 100, speakerGainNode.context.currentTime, 0.05);
            }
        } catch (err) {
            console.error('加载扬声器音量设置失败:', err);
            speakerVolume = DEFAULT_SPEAKER_VOLUME;
        }
    }

    // 设置扬声器音量（供外部调用，参数为 0~100）
    window.setSpeakerVolume = function (vol) {
        if (vol >= 0 && vol <= 100) {
            speakerVolume = vol;
            if (speakerGainNode) {
                speakerGainNode.gain.setTargetAtTime(vol / 100, speakerGainNode.context.currentTime, 0.05);
            }
            saveSpeakerVolumeSetting();
            // 更新 UI 滑块（如果存在）
            const slider = document.getElementById('speaker-volume-slider');
            const valueDisplay = document.getElementById('speaker-volume-value');
            if (slider) slider.value = String(vol);
            if (valueDisplay) valueDisplay.textContent = `${vol}%`;
            console.log(`扬声器音量已设置: ${vol}%`);
        }
    };

    // 获取当前扬声器音量
    window.getSpeakerVolume = function () {
        return speakerVolume;
    };

    // 启动麦克风音量可视化
    function startMicVolumeVisualization() {
        // 先停止现有的动画
        stopMicVolumeVisualization();

        // 缓存 DOM 引用，仅在元素被销毁时重新查询
        let cachedBarFill = document.getElementById('mic-volume-bar-fill');
        let cachedStatus = document.getElementById('mic-volume-status');
        let cachedHint = document.getElementById('mic-volume-hint');
        let cachedPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic');

        function updateVolumeDisplay() {
            // 仅当缓存元素被移出 DOM 时才重新查询（popup 重建场景）
            if (!cachedBarFill || !cachedBarFill.isConnected) {
                cachedBarFill = document.getElementById('mic-volume-bar-fill');
                cachedStatus = document.getElementById('mic-volume-status');
                cachedHint = document.getElementById('mic-volume-hint');
                cachedPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic');
            }

            if (!cachedBarFill) {
                // DOM 元素已销毁（popup 被重建），停止旧的动画循环
                // renderFloatingMicList 会启动新的动画循环
                stopMicVolumeVisualization();
                return;
            }

            // 检查弹出框是否仍然可见（兼容 Live2D 和 VRM）
            // 注意：父容器隐藏时 offsetParent 为 null，但 popup 本身并未销毁
            // 此时仅跳过本帧更新，保持动画循环存活，鼠标回来时恢复显示
            if (!cachedPopup || cachedPopup.style.display === 'none' || !cachedPopup.offsetParent) {
                // popup 不可见，跳过本帧但继续循环
                micVolumeAnimationId = requestAnimationFrame(updateVolumeDisplay);
                return;
            }

            // 检查是否正在录音且有 analyser
            if (isRecording && inputAnalyser) {
                // 获取音频数据
                const dataArray = new Uint8Array(inputAnalyser.frequencyBinCount);
                inputAnalyser.getByteFrequencyData(dataArray);

                // 计算平均音量 (0-255)
                let sum = 0;
                for (let i = 0; i < dataArray.length; i++) {
                    sum += dataArray[i];
                }
                const average = sum / dataArray.length;

                // 转换为百分比 (0-100)，使用对数缩放使显示更自然
                const volumePercent = Math.min(100, (average / 128) * 100);

                // 更新音量条
                cachedBarFill.style.width = `${volumePercent}%`;

                // 根据音量设置颜色
                if (volumePercent < 5) {
                    cachedBarFill.style.backgroundColor = '#dc3545'; // 红色 - 无声音
                } else if (volumePercent < 20) {
                    cachedBarFill.style.backgroundColor = '#ffc107'; // 黄色 - 音量偏低
                } else if (volumePercent > 90) {
                    cachedBarFill.style.backgroundColor = '#fd7e14'; // 橙色 - 音量过高
                } else {
                    cachedBarFill.style.backgroundColor = '#28a745'; // 绿色 - 正常
                }

                // 更新状态文字
                if (cachedStatus) {
                    if (volumePercent < 5) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeNoSound') : '无声音';
                        cachedStatus.style.color = '#dc3545';
                    } else if (volumePercent < 20) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeLow') : '音量偏低';
                        cachedStatus.style.color = '#ffc107';
                    } else if (volumePercent > 90) {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeHigh') : '音量较高';
                        cachedStatus.style.color = '#fd7e14';
                    } else {
                        cachedStatus.textContent = window.t ? window.t('microphone.volumeNormal') : '正常';
                        cachedStatus.style.color = '#28a745';
                    }
                }

                // 更新提示文字
                if (cachedHint) {
                    if (volumePercent < 5) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintNoSound') : '检测不到声音，请检查麦克风';
                    } else if (volumePercent < 20) {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintLow') : '音量较低，建议调高增益';
                    } else {
                        cachedHint.textContent = window.t ? window.t('microphone.volumeHintOk') : '麦克风工作正常';
                    }
                }
            } else {
                // 未录音状态
                cachedBarFill.style.width = '0%';
                cachedBarFill.style.backgroundColor = '#4f8cff';
                if (cachedStatus) {
                    cachedStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
                    cachedStatus.style.color = 'var(--neko-popup-text-sub)';
                }
                if (cachedHint) {
                    cachedHint.textContent = window.t ? window.t('microphone.volumeHint') : '开始录音后可查看音量';
                }
            }

            // 继续下一帧
            micVolumeAnimationId = requestAnimationFrame(updateVolumeDisplay);
        }

        // 启动动画循环
        micVolumeAnimationId = requestAnimationFrame(updateVolumeDisplay);
    }

    // 停止麦克风音量可视化
    function stopMicVolumeVisualization() {
        if (micVolumeAnimationId) {
            cancelAnimationFrame(micVolumeAnimationId);
            micVolumeAnimationId = null;
        }
    }

    // 立即更新音量显示状态（用于录音状态变化时立即反映）
    function updateMicVolumeStatusNow(recording) {
        const volumeBarFill = document.getElementById('mic-volume-bar-fill');
        const volumeStatus = document.getElementById('mic-volume-status');
        const volumeHint = document.getElementById('mic-volume-hint');

        if (recording) {
            // 刚开始录音，显示正在检测状态
            if (volumeStatus) {
                volumeStatus.textContent = window.t ? window.t('microphone.volumeDetecting') : '检测中...';
                volumeStatus.style.color = '#4f8cff';
            }
            if (volumeHint) {
                volumeHint.textContent = window.t ? window.t('microphone.volumeHintDetecting') : '正在检测麦克风输入...';
            }
            if (volumeBarFill) {
                volumeBarFill.style.backgroundColor = '#4f8cff';
            }
        } else {
            // 停止录音，重置为未录音状态
            if (volumeBarFill) {
                volumeBarFill.style.width = '0%';
                volumeBarFill.style.backgroundColor = '#4f8cff';
            }
            if (volumeStatus) {
                volumeStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
                volumeStatus.style.color = 'var(--neko-popup-text-sub)';
            }
            if (volumeHint) {
                volumeHint.textContent = window.t ? window.t('microphone.volumeHint') : '开始录音后可查看音量';
            }
        }
    }

    // 暴露函数供外部调用
    window.startMicVolumeVisualization = startMicVolumeVisualization;
    window.stopMicVolumeVisualization = stopMicVolumeVisualization;
    window.updateMicVolumeStatusNow = updateMicVolumeStatusNow;

    // 开麦，按钮on click
    async function startMicCapture() {
        try {
            // 开始录音前添加录音状态类到两个按钮
            micButton.classList.add('recording');

            // 隐藏文本输入区（仅非移动端），确保语音/文本互斥
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea && !isMobile()) {
                textInputArea.classList.add('hidden');
            }

            if (!audioPlayerContext) {
                audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
                syncAudioGlobals();
            }

            if (audioPlayerContext.state === 'suspended') {
                await audioPlayerContext.resume();
            }

            // 获取麦克风流，使用选择的麦克风设备ID
            // 注意：不在此处指定 sampleRate，因为 getUserMedia 的 sampleRate 只是偏好设置
            // 实际采样率由 AudioContext 强制为 48kHz（见 startAudioWorklet）
            const baseAudioConstraints = {
                noiseSuppression: false,
                echoCancellation: true,
                autoGainControl: true,
                channelCount: 1
            };

            const constraints = {
                audio: selectedMicrophoneId
                    ? { ...baseAudioConstraints, deviceId: { exact: selectedMicrophoneId } }
                    : baseAudioConstraints
            };


            stream = await navigator.mediaDevices.getUserMedia(constraints);

            // 检查音频轨道状态
            const audioTracks = stream.getAudioTracks();
            console.log(window.t('console.audioTrackCount'), audioTracks.length);
            console.log(window.t('console.audioTrackStatus'), audioTracks.map(track => ({
                label: track.label,
                enabled: track.enabled,
                muted: track.muted,
                readyState: track.readyState
            })));

            if (audioTracks.length === 0) {
                console.error(window.t('console.noAudioTrackAvailable'));
                showStatusToast(window.t ? window.t('app.micAccessDenied') : '无法访问麦克风', 4000);
                // 移除已添加的类
                micButton.classList.remove('recording');
                micButton.classList.remove('active');
                // 抛出错误，让外层 catch 块处理按钮状态恢复
                throw new Error('没有可用的音频轨道');
            }

            await startAudioWorklet(stream);

            micButton.disabled = true;
            muteButton.disabled = false;
            screenButton.disabled = false;
            stopButton.disabled = true;
            resetSessionButton.disabled = false;
            showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);

            // 确保active类存在（已经在点击时添加，这里确保存在）
            if (!micButton.classList.contains('active')) {
                micButton.classList.add('active');
            }
            syncFloatingMicButtonState(true);

            // 立即更新音量显示状态（显示"检测中"）
            updateMicVolumeStatusNow(true);

            // 开始录音时，停止主动搭话定时器
            stopProactiveChatSchedule();
        } catch (err) {
            console.error(window.t('console.getMicrophonePermissionFailed'), err);
            showStatusToast(window.t ? window.t('app.micAccessDenied') : '无法访问麦克风', 4000);

            // 失败时恢复文本输入区
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
            }

            // 失败时移除录音状态类
            micButton.classList.remove('recording');
            // 移除active类
            micButton.classList.remove('active');
            // 抛出错误，让外层 catch 块处理按钮状态恢复
            throw err;
        }
    }

    async function stopMicCapture() { // 闭麦，按钮on click
        isSwitchingMode = true; // 开始模式切换（从语音切换到待机/文本模式）

        // 隐藏语音准备提示（防止残留）
        hideVoicePreparingToast();

        // 清理 session Promise 相关状态（防止影响后续会话）
        if (window.sessionTimeoutId) {
            clearTimeout(window.sessionTimeoutId);
            window.sessionTimeoutId = null;
        }
        if (sessionStartedRejecter) {
            try {
                sessionStartedRejecter(new Error('Session aborted'));
            } catch (e) { /* ignore already handled */ }
            sessionStartedRejecter = null;
        }
        if (sessionStartedResolver) {
            sessionStartedResolver = null;
        }

        // 停止录音时移除录音状态类
        micButton.classList.remove('recording');

        // 移除active类
        micButton.classList.remove('active');
        screenButton.classList.remove('active');

        // 同步浮动按钮状态
        syncFloatingMicButtonState(false);
        syncFloatingScreenButtonState(false);

        // 立即更新音量显示状态（显示"未录音"）
        updateMicVolumeStatusNow(false);

        stopRecording();
        micButton.disabled = false;
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;
        resetSessionButton.disabled = false;

        // 显示文本输入区
        const textInputArea = document.getElementById('text-input-area');
        textInputArea.classList.remove('hidden');

        // 停止录音后，重置主动搭话退避级别并开始定时
        if (proactiveChatEnabled && hasAnyChatModeEnabled()) {
            lastUserInputTime = Date.now();
            resetProactiveChatBackoff();
        }

        // 如果是从语音模式切换回来，显示待机状态
        showStatusToast(window.t ? window.t('app.standby', { name: lanlan_config.lanlan_name }) : `${lanlan_config.lanlan_name}待机中...`, 2000);

        // 延迟重置模式切换标志，确保"已离开"消息已经被忽略
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);
    }

    async function getMobileCameraStream() {
        const makeConstraints = (facing) => ({
            video: {
                facingMode: facing,
                frameRate: { ideal: 1, max: 1 },
            },
            audio: false,
        });

        const attempts = [
            { label: 'rear', constraints: makeConstraints({ ideal: 'environment' }) },
            { label: 'front', constraints: makeConstraints('user') },
            { label: 'any', constraints: { video: { frameRate: { ideal: 1, max: 1 } }, audio: false } },
        ];

        let lastError;

        for (const attempt of attempts) {
            try {
                console.log(`${window.t('console.tryingCamera')} ${attempt.label} ${window.t('console.cameraLabel')} ${1}${window.t('console.cameraFps')}`);
                return await navigator.mediaDevices.getUserMedia(attempt.constraints);
            } catch (err) {
                console.warn(`${attempt.label} ${window.t('console.cameraFailed')}`, err);
                lastError = err;
            }
        }

        if (lastError) {
            showStatusToast(lastError.toString(), 4000);
            throw lastError;
        }
    }

    async function startScreenSharing() { // 分享屏幕，按钮on click
        // 检查是否在录音状态
        if (!isRecording) {
            showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
            return;
        }

        try {
            // 初始化音频播放上下文
            await showCurrentModel(); // 智能显示当前模型
            if (!audioPlayerContext) {
                audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
                syncAudioGlobals();
            }

            // 如果上下文被暂停，则恢复它
            if (audioPlayerContext.state === 'suspended') {
                await audioPlayerContext.resume();
            }

            if (screenCaptureStream == null) {
                if (isMobile()) {
                    // 移动端使用摄像头
                    const tmp = await getMobileCameraStream();
                    if (tmp instanceof MediaStream) {
                        screenCaptureStream = tmp;
                    } else {
                        // 保持原有错误处理路径：让 catch 去接手
                        throw (tmp instanceof Error ? tmp : new Error('无法获取摄像头流'));
                    }
                } else {

                    // Desktop/laptop: capture the user's chosen screen / window / tab.
                    // 检查是否有选中的特定屏幕源（仅Electron环境）
                    let selectedSourceId = window.getSelectedScreenSourceId ? window.getSelectedScreenSourceId() : null;

                    if (selectedSourceId && window.electronDesktopCapturer) {
                        // 验证选中的源是否仍然存在（窗口可能已关闭）
                        try {
                            const currentSources = await window.electronDesktopCapturer.getSources({
                                types: ['window', 'screen'],
                                thumbnailSize: { width: 1, height: 1 }
                            });
                            const sourceStillExists = currentSources.some(s => s.id === selectedSourceId);

                            if (!sourceStillExists) {
                                console.warn('[屏幕源] 选中的源已不可用 (ID:', selectedSourceId, ')，自动回退到全屏');
                                showStatusToast(
                                    safeT('app.screenSource.sourceLost', '屏幕分享无法找到之前选择窗口，已切换为全屏分享'),
                                    3000
                                );
                                // 查找第一个全屏源作为回退
                                const screenSources = currentSources.filter(s => s.id.startsWith('screen:'));
                                if (screenSources.length > 0) {
                                    selectedSourceId = screenSources[0].id;
                                    selectedScreenSourceId = selectedSourceId;
                                    try { localStorage.setItem('selectedScreenSourceId', selectedSourceId); } catch (e) { }
                                    updateScreenSourceListSelection();
                                } else {
                                    // 连全屏源都拿不到，清空选择让下面走 getDisplayMedia
                                    selectedSourceId = null;
                                    selectedScreenSourceId = null;
                                    try { localStorage.removeItem('selectedScreenSourceId'); } catch (e) { }
                                }
                            }
                        } catch (validateErr) {
                            console.warn('[屏幕源] 验证源可用性失败，继续尝试使用保存的源:', validateErr);
                        }
                    }

                    if (selectedSourceId && window.electronDesktopCapturer) {
                        // 在Electron中使用选中的特定屏幕/窗口源
                        // 使用 chromeMediaSourceId 约束来指定源
                        try {
                            screenCaptureStream = await navigator.mediaDevices.getUserMedia({
                                audio: false,
                                video: {
                                    mandatory: {
                                        chromeMediaSource: 'desktop',
                                        chromeMediaSourceId: selectedSourceId,
                                        maxFrameRate: 1
                                    }
                                }
                            });
                        } catch (captureErr) {
                            console.warn('[屏幕源] 指定源捕获失败，尝试回退:', captureErr);
                            let fallbackSucceeded = false;

                            // 回退策略1: 尝试其他全屏源（chromeMediaSource 方式）
                            try {
                                const fallbackSources = await window.electronDesktopCapturer.getSources({
                                    types: ['screen'],
                                    thumbnailSize: { width: 1, height: 1 }
                                });
                                if (fallbackSources.length > 0) {
                                    screenCaptureStream = await navigator.mediaDevices.getUserMedia({
                                        audio: false,
                                        video: {
                                            mandatory: {
                                                chromeMediaSource: 'desktop',
                                                chromeMediaSourceId: fallbackSources[0].id,
                                                maxFrameRate: 1
                                            }
                                        }
                                    });
                                    selectedScreenSourceId = fallbackSources[0].id;
                                    try { localStorage.setItem('selectedScreenSourceId', fallbackSources[0].id); } catch (e) { }
                                    showStatusToast(
                                        safeT('app.screenSource.sourceLost', '屏幕分享无法找到之前选择窗口，已切换为全屏分享'),
                                        3000
                                    );
                                    fallbackSucceeded = true;
                                }
                            } catch (fallback1Err) {
                                console.warn('[屏幕源] chromeMediaSource 全屏回退也失败:', fallback1Err);
                            }

                            // 回退策略2: chromeMediaSource 在该系统上完全不可用，降级到 getDisplayMedia
                            if (!fallbackSucceeded) {
                                try {
                                    console.log('[屏幕源] chromeMediaSource 不可用，降级到 getDisplayMedia');
                                    screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
                                        video: { cursor: 'always', frameRate: 1 },
                                        audio: false,
                                    });
                                    selectedScreenSourceId = null;
                                    try { localStorage.removeItem('selectedScreenSourceId'); } catch (e) { }
                                    fallbackSucceeded = true;
                                } catch (fallback2Err) {
                                    console.warn('[屏幕源] getDisplayMedia 回退也失败:', fallback2Err);
                                }
                            }

                            if (!fallbackSucceeded) {
                                console.warn('[屏幕源] 所有前端流方式均失败，将尝试后端轮询兜底');
                            }
                        }
                        if (screenCaptureStream) {
                            console.log(window.t('console.screenShareUsingSource'), selectedSourceId);
                        }
                    } else {
                        // 使用标准的getDisplayMedia（显示系统选择器）
                        try {
                            screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
                                video: {
                                    cursor: 'always',
                                    frameRate: 1,
                                },
                                audio: false,
                            });
                        } catch (displayErr) {
                            // 用户主动取消则直接抛出，不兜底
                            if (displayErr.name === 'NotAllowedError') throw displayErr;
                            console.warn('[屏幕源] getDisplayMedia 失败，将尝试后端轮询兜底:', displayErr);
                        }
                    }
                }
            }

            if (screenCaptureStream) {
                // 用户手势成功获取了流，重置自动弹窗失败标记
                screenCaptureAutoPromptFailed = false;
                // 正常流模式
                screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();

                startScreenVideoStreaming(screenCaptureStream, isMobile() ? 'camera' : 'screen');

                // 当用户停止共享屏幕时
                screenCaptureStream.getVideoTracks()[0].onended = () => {
                    stopScreening();
                    screenButton.classList.remove('active');
                    syncFloatingScreenButtonState(false);

                    if (screenCaptureStream && typeof screenCaptureStream.getTracks === 'function') {
                        screenCaptureStream.getTracks().forEach(track => {
                            try { track.stop(); } catch (e) { }
                        });
                    }
                    screenCaptureStream = null;
                    screenCaptureStreamLastUsed = null;

                    if (typeof screenCaptureStreamIdleTimer !== 'undefined' && screenCaptureStreamIdleTimer) {
                        clearTimeout(screenCaptureStreamIdleTimer);
                        screenCaptureStreamIdleTimer = null;
                    }
                };
            } else {
                // 回退策略3: 后端 pyautogui 轮询模式（所有前端流方式均失败）
                const { dataUrl: backendTest } = await fetchBackendScreenshot();
                if (!backendTest) {
                    throw new Error('所有屏幕捕获方式均失败（含后端兜底）');
                }
                console.log('[屏幕源] 进入后端 pyautogui 轮询模式');

                // 立即发送第一帧
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({ action: 'stream_data', data: backendTest, input_type: 'screen' }));
                }

                // 复用 videoSenderInterval，stopScreening() 可统一清理
                videoSenderInterval = setInterval(async () => {
                    try {
                        const { dataUrl: frame } = await fetchBackendScreenshot();
                        if (frame && socket && socket.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({ action: 'stream_data', data: frame, input_type: 'screen' }));
                        }
                    } catch (e) {
                        console.warn('[屏幕源] 后端轮询帧失败:', e);
                    }
                }, 1000);
            }

            micButton.disabled = true;
            muteButton.disabled = false;
            screenButton.disabled = true;
            stopButton.disabled = false;
            resetSessionButton.disabled = false;

            screenButton.classList.add('active');
            syncFloatingScreenButtonState(true);

            if (window.unlockAchievement) {
                window.unlockAchievement('ACH_SEND_IMAGE').catch(err => {
                    console.error('解锁发送图片成就失败:', err);
                });
            }

            try {
                stopProactiveVisionDuringSpeech();
            } catch (e) {
                console.warn(window.t('console.stopVoiceActiveVisionFailed'), e);
            }

            if (!isRecording) showStatusToast(window.t ? window.t('app.micNotOpen') : '没开麦啊喂！', 3000);
        } catch (err) {
            console.error(isMobile() ? window.t('console.cameraAccessFailed') : window.t('console.screenShareFailed'), err);
            console.error(window.t('console.startupFailed'), err);
            let hint = '';
            const isDesktop = !isMobile();
            switch (err.name) {
                case 'NotAllowedError':
                    hint = isDesktop
                        ? '用户取消了屏幕共享，或系统未授予屏幕录制权限'
                        : '请检查 iOS 设置 → Safari → 摄像头 权限是否为"允许"';
                    break;
                case 'NotFoundError':
                    hint = isDesktop ? '未检测到可用的屏幕源' : '未检测到摄像头设备';
                    break;
                case 'NotReadableError':
                case 'AbortError':
                    hint = isDesktop
                        ? '屏幕捕获启动失败，可能与显卡驱动或系统权限有关，请尝试重启应用'
                        : '摄像头被其它应用占用？关闭扫码/拍照应用后重试';
                    break;
            }
            showStatusToast(`${err.name}: ${err.message}${hint ? `\n${hint}` : ''}`, 5000);
        }
    }

    async function stopScreenSharing() { // 停止共享，按钮on click
        stopScreening();

        // 停止所有 tracks 并清除回调，防止隐私/资源泄漏
        try {
            if (screenCaptureStream && typeof screenCaptureStream.getTracks === 'function') {
                // 清除 onended 回调，防止重复触发
                const vt = screenCaptureStream.getVideoTracks?.()?.[0];
                if (vt) {
                    vt.onended = null;
                }
                // 停止所有 tracks（包括视频和音频）
                screenCaptureStream.getTracks().forEach(track => {
                    try {
                        track.stop();
                    } catch (e) {
                        // 忽略已经停止的 track
                    }
                });
            }
        } catch (e) {
            console.warn(window.t('console.screenShareStopTracksFailed'), e);
        } finally {
            // 确保引用被清空，即使出错也能释放
            screenCaptureStream = null;
            screenCaptureStreamLastUsed = null;
            // 清除闲置定时器
            if (screenCaptureStreamIdleTimer) {
                clearTimeout(screenCaptureStreamIdleTimer);
                screenCaptureStreamIdleTimer = null;
            }
        }

        // 仅在主动录像/语音连接分享时更新 UI 状态，防止闲置释放导致 UI 错误锁定
        if (isRecording) {
            micButton.disabled = true;
            muteButton.disabled = false;
            screenButton.disabled = false;
            stopButton.disabled = true;
            resetSessionButton.disabled = false;
            showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);

            // 移除active类
            screenButton.classList.remove('active');
            syncFloatingScreenButtonState(false);
        } else {
            // 即使未录音，也确保按钮重置为正常状态
            screenButton.classList.remove('active');
            syncFloatingScreenButtonState(false);
        }

        // 停止手动屏幕共享后，如果满足条件则恢复语音期间主动视觉定时
        try {
            if (proactiveVisionEnabled && isRecording) {
                startProactiveVisionDuringSpeech();
            }
        } catch (e) {
            console.warn(window.t('console.resumeVoiceActiveVisionFailed'), e);
        }
    }

    window.switchMicCapture = async () => {
        if (muteButton.disabled) {
            await startMicCapture();
        } else {
            await stopMicCapture();
        }
    }
    window.switchScreenSharing = async () => {
        if (stopButton.disabled) {
            // 检查是否在录音状态
            if (!isRecording) {
                showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
                return;
            }
            await startScreenSharing();
        } else {
            await stopScreenSharing();
        }
    }

    // 显示语音准备提示框
    function showVoicePreparingToast(message) {
        // 检查是否已存在提示框，避免重复创建
        let toast = document.getElementById('voice-preparing-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'voice-preparing-toast';
            document.body.appendChild(toast);
        }

        // 确保样式始终一致（每次更新时都重新设置）
        toast.style.cssText = `
            position: fixed;
            bottom: 18%;
            left: 50%;
            transform: translateX(-50%);
            background-image: url('/static/icons/reminder_blue.png');
            background-size: 100% 100%;
            background-position: center;
            background-repeat: no-repeat;
            background-color: transparent;
            color: white;
            padding: 20px 32px;
            border-radius: 16px;
            font-size: 16px;
            font-weight: 600;
            z-index: 10000;
            display: flex;
            align-items: center;
            gap: 12px;
            animation: voiceToastFadeIn 0.3s ease;
            pointer-events: none;
            width: 320px;
            box-sizing: border-box;
            justify-content: center;
        `;

        // 添加动画样式（只添加一次）
        if (!document.querySelector('style[data-voice-toast-animation]')) {
            const style = document.createElement('style');
            style.setAttribute('data-voice-toast-animation', 'true');
            style.textContent = `
                @keyframes voiceToastFadeIn {
                    from {
                        opacity: 0;
                        transform: translateX(-50%) scale(0.8);
                    }
                    to {
                        opacity: 1;
                        transform: translateX(-50%) scale(1);
                    }
                }
                @keyframes voiceToastPulse {
                    0%, 100% {
                        transform: scale(1);
                    }
                    50% {
                        transform: scale(1.1);
                    }
                }
            `;
            document.head.appendChild(style);
        }

        // 更新消息内容
        toast.innerHTML = `
            <div style="
                width: 20px;
                height: 20px;
                border: 3px solid rgba(255, 255, 255, 0.3);
                border-top-color: white;
                border-radius: 50%;
                animation: spin 1s linear infinite;
            "></div>
            <span>${message}</span>
        `;

        // 添加旋转动画
        const spinStyle = document.createElement('style');
        spinStyle.textContent = `
            @keyframes spin {
                to { transform: rotate(360deg); }
            }
        `;
        if (!document.querySelector('style[data-spin-animation]')) {
            spinStyle.setAttribute('data-spin-animation', 'true');
            document.head.appendChild(spinStyle);
        }

        toast.style.display = 'flex';
    }

    // 隐藏语音准备提示框
    function hideVoicePreparingToast() {
        const toast = document.getElementById('voice-preparing-toast');
        if (toast) {
            toast.style.animation = 'voiceToastFadeIn 0.3s ease reverse';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 300);
        }
    }

    // 重要通知模态框（全屏遮罩 + 居中弹窗，用户必须点确认才能关闭）
    // 接受字符串或通知对象 {code, message, message_en, details}
    // 返回 Promise，在用户确认后 resolve（用于串行展示多条通知）
    //
    // 实现：内部维护一条 FIFO 队列；若当前已有遮罩，新调用入队等待，
    // 不会丢失内容也不会同时弹出两个遮罩。
    const _prominentNoticeQueue = [];
    let _prominentNoticeActive = false;

    function _drainProminentNoticeQueue() {
        if (_prominentNoticeActive || _prominentNoticeQueue.length === 0) return;
        const { notice, resolve } = _prominentNoticeQueue.shift();
        _prominentNoticeActive = true;
        _renderProminentNotice(notice, () => {
            resolve();
            _prominentNoticeActive = false;
            _drainProminentNoticeQueue();
        });
    }

    function _renderProminentNotice(notice, onDismiss) {
        // 回退文本优先级：按用户 locale 选择语言，避免 i18n 未就绪时展示错误语种。
        const _isChinese = (typeof _isUserRegionChina === 'function' && _isUserRegionChina())
            || /^zh/i.test(navigator.language || '');
        const localeFallback = _isChinese
            ? (notice.message || notice.message_en || '')
            : (notice.message_en || notice.message || '');
        const displayText = (notice.code && typeof safeT === 'function')
            ? safeT(notice.code, localeFallback)
            : localeFallback;

        const overlay = document.createElement('div');
        overlay.id = 'prominent-notice-overlay';
        overlay.style.cssText = `
            position: fixed; inset: 0;
            background: rgba(0,0,0,0.55);
            z-index: 2147483647;
            display: flex; align-items: center; justify-content: center;
            pointer-events: auto;
            animation: pnOverlayIn 0.25s ease;
        `;

        const box = document.createElement('div');
        box.setAttribute('role', 'dialog');
        box.setAttribute('aria-modal', 'true');
        box.setAttribute('aria-label', displayText || 'Notice');
        box.tabIndex = -1;
        box.style.cssText = `
            position: relative;
            background: #1e293b;
            color: #f1f5f9;
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 16px;
            padding: 32px 28px 24px;
            width: 370px; max-width: 88vw;
            box-shadow: 0 12px 40px rgba(0,0,0,0.5);
            text-align: center;
            pointer-events: auto;
            animation: pnBoxIn 0.3s ease;
        `;

        const btn = document.createElement('button');
        btn.textContent = (typeof safeT === 'function') ? safeT('common.confirm', '确认') : '确认';
        btn.style.cssText = `
            background: #3b82f6; color: #fff; border: none;
            border-radius: 10px; padding: 10px 48px;
            font-size: 15px; font-weight: 600; cursor: pointer;
            pointer-events: auto;
            transition: background 0.15s;
        `;

        const icon = document.createElement('img');
        icon.src = '/static/icons/exclamation.png';
        icon.alt = '';
        icon.style.cssText = 'width:36px;height:36px;margin-bottom:14px;';

        const textDiv = document.createElement('div');
        textDiv.style.cssText = 'font-size:16px;font-weight:600;line-height:1.7;margin-bottom:22px;';
        textDiv.textContent = displayText;

        box.appendChild(icon);
        box.appendChild(textDiv);
        box.appendChild(btn);
        overlay.appendChild(box);
        const prevActive = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        let dismissed = false;
        document.body.appendChild(overlay);
        if (!dismissed) {
            btn.focus();
        }

        if (!document.querySelector('style[data-prominent-notice-animation]')) {
            const s = document.createElement('style');
            s.setAttribute('data-prominent-notice-animation', 'true');
            s.textContent = `
                @keyframes pnOverlayIn { from{opacity:0} to{opacity:1} }
                @keyframes pnBoxIn    { from{opacity:0;transform:scale(0.85)} to{opacity:1;transform:scale(1)} }
                @keyframes pnOverlayOut { from{opacity:1} to{opacity:0} }
            `;
            document.head.appendChild(s);
        }

        const dismiss = () => {
            if (dismissed) return;
            dismissed = true;
            btn.removeEventListener('click', dismiss);
            overlay.style.animation = 'pnOverlayOut 0.2s ease forwards';
            setTimeout(() => {
                overlay.remove();
                if (prevActive && document.contains(prevActive)) {
                    prevActive.focus();
                }
                onDismiss();
            }, 200);
        };
        btn.addEventListener('click', dismiss);
    }

    function showProminentNotice(noticeOrMessage) {
        let notice;
        if (typeof noticeOrMessage === 'string') {
            notice = { message: noticeOrMessage };
        } else if (noticeOrMessage && typeof noticeOrMessage === 'object') {
            notice = noticeOrMessage;
        } else {
            // null / undefined / unexpected type — wrap defensively so
            // _renderProminentNotice never throws and blocks the queue.
            notice = { message: String(noticeOrMessage ?? '') };
        }
        return new Promise((resolve) => {
            _prominentNoticeQueue.push({ notice, resolve });
            _drainProminentNoticeQueue();
        });
    }
    window.showProminentNotice = showProminentNotice;

    // 显示"可以说话了"提示
    function showReadyToSpeakToast() {
        let toast = document.getElementById('voice-ready-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'voice-ready-toast';
            document.body.appendChild(toast);
        }

        // 确保样式始终一致（和前两个弹窗一样的大小）
        toast.style.cssText = `
            position: fixed;
            bottom: 18%;
            left: 50%;
            transform: translateX(-50%);
            background-image: url('/static/icons/reminder_midori.png');
            background-size: 100% 100%;
            background-position: center;
            background-repeat: no-repeat;
            background-color: transparent;
            color: white;
            padding: 20px 32px;
            border-radius: 16px;
            font-size: 16px;
            font-weight: 600;
            box-shadow: none;
            z-index: 10000;
            display: flex;
            align-items: center;
            gap: 12px;
            animation: voiceToastFadeIn 0.3s ease;
            pointer-events: none;
            width: 320px;
            box-sizing: border-box;
            justify-content: center;
        `;

        toast.innerHTML = `
            <img src="/static/icons/ready_to_talk.png" style="width: 36px; height: 36px; object-fit: contain; display: block; flex-shrink: 0;" alt="ready">
            <span style="display: flex; align-items: center;">${window.t ? window.t('app.readyToSpeak') : '可以开始说话了！'}</span>
        `;

        // 2秒后自动消失
        setTimeout(() => {
            toast.style.animation = 'voiceToastFadeIn 0.3s ease reverse';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 300);
        }, 2000);
    }

    // 同步浮动麦克风按钮状态的辅助函数
    function syncFloatingMicButtonState(isActive) {
        // 更新所有存在的 manager 的按钮状态
        const managers = [window.live2dManager, window.vrmManager];

        for (const manager of managers) {
            if (manager && manager._floatingButtons && manager._floatingButtons.mic) {
                const { button, imgOff, imgOn } = manager._floatingButtons.mic;
                if (button) {
                    button.dataset.active = isActive ? 'true' : 'false';
                    if (imgOff && imgOn) {
                        imgOff.style.opacity = isActive ? '0' : '1';
                        imgOn.style.opacity = isActive ? '1' : '0';
                    }
                }
            }
        }
    }

    // 同步浮动屏幕分享按钮状态的辅助函数
    function syncFloatingScreenButtonState(isActive) {
        // 更新所有存在的 manager 的按钮状态
        const managers = [window.live2dManager, window.vrmManager];

        for (const manager of managers) {
            if (manager && manager._floatingButtons && manager._floatingButtons.screen) {
                const { button, imgOff, imgOn } = manager._floatingButtons.screen;
                if (button) {
                    button.dataset.active = isActive ? 'true' : 'false';
                    if (imgOff && imgOn) {
                        imgOff.style.opacity = isActive ? '0' : '1';
                        imgOn.style.opacity = isActive ? '1' : '0';
                    }
                }
            }
        }
    }

    // 开始麦克风录音
    micButton.addEventListener('click', async () => {
        // 如果按钮已禁用或正在录音，直接返回
        if (micButton.disabled || isRecording) {
            return;
        }

        // 如果已经有 active 类，说明正在处理中，直接返回（防止重复点击）
        if (micButton.classList.contains('active')) {
            return;
        }

        // 立即添加激活状态类，保持常亮状态
        micButton.classList.add('active');

        // 同步更新浮动按钮状态，防止浮动按钮状态不同步导致图标变灰
        syncFloatingMicButtonState(true);

        // 标记麦克风正在启动中
        window.isMicStarting = true;

        // 立即禁用按钮，锁定直到连接成功或失败
        micButton.disabled = true;

        // 立即显示准备提示
        showVoicePreparingToast(window.t ? window.t('app.voiceSystemPreparing') : '语音系统准备中...');

        // 如果有活跃的文本会话，先结束它
        if (isTextSessionActive) {
            isSwitchingMode = true; // 开始模式切换
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'end_session'
                }));
            }
            isTextSessionActive = false;
            showStatusToast(window.t ? window.t('app.switchingToVoice') : '正在切换到语音模式...', 3000);
            showVoicePreparingToast(window.t ? window.t('app.switchingToVoice') : '正在切换到语音模式...');
            // 增加等待时间，确保后端完全清理资源
            await new Promise(resolve => setTimeout(resolve, 1500)); // 从500ms增加到1500ms
        }

        // 隐藏文本输入区（仅非移动端）
        const textInputArea = document.getElementById('text-input-area');
        if (!isMobile()) {
            textInputArea.classList.add('hidden');
        }

        // 禁用所有语音按钮（micButton 已在函数开始处禁用）
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;
        resetSessionButton.disabled = true;
        returnSessionButton.disabled = true;

        showStatusToast(window.t ? window.t('app.initializingVoice') : '正在初始化语音对话...', 3000);
        showVoicePreparingToast(window.t ? window.t('app.connectingToServer') : '正在连接服务器...');

        try {
            // 创建一个 Promise 来等待 session_started 消息
            const sessionStartPromise = new Promise((resolve, reject) => {
                sessionStartedResolver = resolve;
                sessionStartedRejecter = reject; // 保存 reject 函数

                // 清除之前的超时定时器（如果存在）
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
            });

            // 发送start session事件（确保 WebSocket 已连接）
            await ensureWebSocketOpen();
            socket.send(JSON.stringify({
                action: 'start_session',
                input_type: 'audio'
            }));

            // 设置超时（15秒，略大于后端12秒以对冲网络延迟）
            window.sessionTimeoutId = setTimeout(() => {
                if (sessionStartedRejecter) {
                    const rejecter = sessionStartedRejecter;
                    sessionStartedResolver = null; // 先清除，防止重复触发
                    sessionStartedRejecter = null; // 同时清理 rejecter
                    window.sessionTimeoutId = null; // 清除全局定时器ID

                    // 超时时向后端发送 end_session 消息
                    if (socket && socket.readyState === WebSocket.OPEN) {
                        socket.send(JSON.stringify({
                            action: 'end_session'
                        }));
                        console.log(window.t('console.sessionTimeoutEndSession'));
                    }

                    // 更新提示信息，显示超时
                    showVoicePreparingToast(window.t ? window.t('app.sessionTimeout') || '连接超时' : '连接超时，请检查网络连接');
                    rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                } else {
                    window.sessionTimeoutId = null; // 即使 rejecter 不存在也清除
                }
            }, 15000);

            // 等待session真正启动成功 AND 麦克风初始化完成（并行执行以减少等待时间）
            // 并行执行：
            // 1. 等待后端Session准备就绪 (sessionStartPromise)
            // 2. 初始化前端麦克风 (startMicCapture)
            try {
                // 显示当前模型 (提前显示，优化观感)
                await showCurrentModel(); // 智能显示当前模型

                showStatusToast(window.t ? window.t('app.initializingMic') : '正在初始化麦克风...', 3000);

                // 并行等待
                await Promise.all([
                    sessionStartPromise,
                    startMicCapture()
                ]);

                // 成功时清除超时定时器
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
            } catch (error) {
                // 超时或错误时清除超时定时器
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
                throw error; // 重新抛出错误，让外层 catch 处理
            }

            // 启动语音期间的主动视觉定时（如果已开启主动视觉）
            try {
                if (proactiveVisionEnabled) {
                    // 刷新页面后 screenCaptureStream 会丢失，需要在用户手势上下文中重新获取
                    // 录音按钮点击是一个有效的用户手势上下文
                    if (typeof acquireProactiveVisionStream === 'function') {
                        await acquireProactiveVisionStream();
                    }
                    startProactiveVisionDuringSpeech();
                }
            } catch (e) {
                console.warn(window.t('console.startVoiceActiveVisionFailed'), e);
            }

            // 录音启动成功后，隐藏准备提示，显示"可以说话了"提示
            hideVoicePreparingToast();

            // 延迟1秒显示"可以说话了"提示，确保系统真正准备好
            // 同时启动麦克风静音检测，此时服务器已准备就绪
            setTimeout(() => {
                showReadyToSpeakToast();
                // 服务器准备就绪后才启动静音检测，避免过早计时
                startSilenceDetection();
                monitorInputVolume();
            }, 1000);

            // 麦克风启动完成
            window.isMicStarting = false;
            isSwitchingMode = false; // 模式切换完成
        } catch (error) {
            console.error(window.t('console.startVoiceSessionFailed'), error);

            // 清除所有超时定时器和状态
            if (window.sessionTimeoutId) {
                clearTimeout(window.sessionTimeoutId);
                window.sessionTimeoutId = null;
            }
            if (sessionStartedResolver) {
                sessionStartedResolver = null;
            }
            if (sessionStartedRejecter) {
                sessionStartedRejecter = null; //  同时清理 rejecter
            }

            // 确保后端清理资源，避免前后端状态不一致
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'end_session'
                }));
                console.log(window.t('console.sessionStartFailedEndSession'));
            }

            // 隐藏准备提示
            hideVoicePreparingToast();

            // 停止可能已启动的录音（startMicCapture 与 session 并行，可能已经开始）
            stopRecording();

            // 失败时：移除激活状态（按钮变暗），恢复按钮（允许再次点击）
            micButton.classList.remove('active');
            micButton.classList.remove('recording');

            // 重置录音标志
            isRecording = false;
            window.isRecording = false;

            // 同步更新浮动按钮状态，确保浮动按钮也变灰
            syncFloatingMicButtonState(false);
            syncFloatingScreenButtonState(false);

            micButton.disabled = false;
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = false;
            textInputArea.classList.remove('hidden');
            showStatusToast(window.t ? window.t('app.startFailed', { error: error.message }) : `启动失败: ${error.message}`, 5000);
            // 麦克风启动失败，重置标志
            window.isMicStarting = false;
            isSwitchingMode = false; // 切换失败，重置标志

            // 移除其他按钮的active类
            screenButton.classList.remove('active');
        }
    });

    // 开始屏幕共享
    screenButton.addEventListener('click', startScreenSharing);

    // 停止屏幕共享
    stopButton.addEventListener('click', stopScreenSharing);

    // 停止对话
    muteButton.addEventListener('click', stopMicCapture);

    resetSessionButton.addEventListener('click', () => {
        console.log(window.t('console.resetButtonClicked'));
        isSwitchingMode = true; // 开始重置会话（也是一种模式切换）

        // 检查是否是"请她离开"触发的
        const isGoodbyeMode = window.live2dManager && window.live2dManager._goodbyeClicked;
        console.log(window.t('console.checkingGoodbyeMode'), isGoodbyeMode, window.t('console.goodbyeClicked'), window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');

        // 检查 hideLive2d 前的容器状态
        const live2dContainer = document.getElementById('live2d-container');
        console.log(window.t('console.hideLive2dBeforeStatus'), {
            存在: !!live2dContainer,
            当前类: live2dContainer ? live2dContainer.className : 'undefined',
            classList: live2dContainer ? live2dContainer.classList.toString() : 'undefined',
            display: live2dContainer ? getComputedStyle(live2dContainer).display : 'undefined'
        });

        hideLive2d()

        // 检查 hideLive2d 后的容器状态
        console.log(window.t('console.hideLive2dAfterStatus'), {
            存在: !!live2dContainer,
            当前类: live2dContainer ? live2dContainer.className : 'undefined',
            classList: live2dContainer ? live2dContainer.classList.toString() : 'undefined',
            display: live2dContainer ? getComputedStyle(live2dContainer).display : 'undefined'
        });
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                action: 'end_session'
            }));
        }
        stopRecording();
        // 使用立即执行的异步函数等待清空完成，避免竞态条件
        (async () => {
            await clearAudioQueue();
        })();

        // 重置所有状态
        isTextSessionActive = false;

        // 移除所有按钮的active类
        micButton.classList.remove('active');
        screenButton.classList.remove('active');

        // 清除所有截图
        screenshotsList.innerHTML = '';
        screenshotThumbnailContainer.classList.remove('show');
        updateScreenshotCount();
        screenshotCounter = 0;

        // 根据模式执行不同逻辑
        console.log(window.t('console.executingBranchJudgment'), isGoodbyeMode);
        if (!isGoodbyeMode) {
            // 非"请她离开"模式：显示文本输入区并启用按钮
            console.log(window.t('console.executingNormalEndSession'));

            // 结束会话后，重置主动搭话计时器（如果已开启）
            if (proactiveChatEnabled && hasAnyChatModeEnabled()) {
                resetProactiveChatBackoff();
            }
            // 显示文本输入区
            const textInputArea = document.getElementById('text-input-area');
            textInputArea.classList.remove('hidden');

            // 启用所有输入
            micButton.disabled = false;
            textSendButton.disabled = false;
            textInputBox.disabled = false;
            screenshotButton.disabled = false;

            // 禁用语音控制按钮
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = true;
            returnSessionButton.disabled = true;  // 禁用"请她回来"按钮

            showStatusToast(window.t ? window.t('app.sessionEnded') : '会话已结束', 3000);
        } else {
            // "请她离开"模式：隐藏所有内容
            console.log(window.t('console.executingGoodbyeMode'));
            console.log('[App] 执行"请她离开"模式逻辑');

            // "请她离开"模式：隐藏所有内容
            const textInputArea = document.getElementById('text-input-area');
            textInputArea.classList.add('hidden');

            // 禁用所有按钮
            micButton.disabled = true;
            textSendButton.disabled = true;
            textInputBox.disabled = true;
            screenshotButton.disabled = true;
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            resetSessionButton.disabled = true;
            returnSessionButton.disabled = false;  // 启用"请她回来"按钮

            // "请她离开"时，停止主动搭话定时器
            stopProactiveChatSchedule();

            showStatusToast('', 0);
        }

        // 延迟重置模式切换标志，确保"已离开"消息已经被忽略
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);
    });

    // "请她回来"按钮事件（重构版：复用 sessionStartedResolver + timeout 模式，统一使用 showCurrentModel）
    returnSessionButton.addEventListener('click', async () => {
        isSwitchingMode = true; // 开始模式切换

        try {
            // 清除 goodbyeClicked 标志
            if (window.live2dManager) {
                window.live2dManager._goodbyeClicked = false;
            }
            if (window.vrmManager) {
                window.vrmManager._goodbyeClicked = false;
            }

            // 清除所有语音相关的状态类
            micButton.classList.remove('recording');
            micButton.classList.remove('active');
            screenButton.classList.remove('active');

            // 确保停止录音状态
            isRecording = false;
            window.isRecording = false;

            // 显示文本输入区
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
            }

            // 显示准备中提示
            showStatusToast(window.t ? window.t('app.initializingText') : '正在初始化文本对话...', 3000);

            // 创建一个 Promise 来等待 session_started 消息（复用已有模式）
            const sessionStartPromise = new Promise((resolve, reject) => {
                sessionStartedResolver = resolve;
                sessionStartedRejecter = reject; //  保存 reject 函数

                // 清除之前的超时定时器（如果存在）
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }

                // 设置超时（15秒，略大于后端12秒以对冲网络延迟）
                window.sessionTimeoutId = setTimeout(() => {
                    if (sessionStartedRejecter) {
                        const rejecter = sessionStartedRejecter;
                        sessionStartedResolver = null; // 先清除，防止重复触发
                        sessionStartedRejecter = null; //  同时清理 rejecter
                        window.sessionTimeoutId = null; // 清除全局定时器ID

                        // 超时时向后端发送 end_session 消息
                        if (socket && socket.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({
                                action: 'end_session'
                            }));
                            console.log(window.t('console.returnSessionTimeoutEndSession'));
                        }

                        rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                    }
                }, 15000);
            });

            // 启动文本session（确保 WebSocket 已连接）
            await ensureWebSocketOpen();
            socket.send(JSON.stringify({
                action: 'start_session',
                input_type: 'text',
                new_session: true
            }));

            // 等待session真正启动成功
            await sessionStartPromise;

            // 只有在 session_started 确认后才设置状态
            isTextSessionActive = true;

            // 使用 showCurrentModel() 统一处理模型显示（避免重复分叉）
            await showCurrentModel();

            // 恢复对话区
            const chatContainerEl = document.getElementById('chat-container');
            if (chatContainerEl && (chatContainerEl.classList.contains('minimized') || chatContainerEl.classList.contains('mobile-collapsed'))) {
                console.log('[App] 自动恢复对话区');
                chatContainerEl.classList.remove('minimized');
                chatContainerEl.classList.remove('mobile-collapsed');

                // 恢复子元素可见性
                const chatContentWrapper = chatContainerEl.querySelector('.chat-content-wrapper');
                const chatHeader = chatContainerEl.querySelector('.chat-header');
                const textInputArea = document.getElementById('text-input-area');
                if (chatContentWrapper) {
                    chatContentWrapper.style.display = '';
                }
                if (chatHeader) {
                    chatHeader.style.display = '';
                }
                if (textInputArea) {
                    textInputArea.style.display = '';
                }

                // 同步更新切换按钮的状态（图标和标题）
                const toggleChatBtn = document.getElementById('toggle-chat-btn');
                if (toggleChatBtn) {
                    const iconImg = toggleChatBtn.querySelector('img');
                    if (iconImg) {
                        iconImg.src = '/static/icons/expand_icon_off.png';
                        iconImg.alt = window.t ? window.t('common.minimize') : '最小化';
                    }
                    toggleChatBtn.title = window.t ? window.t('common.minimize') : '最小化';

                    // 还原后滚动到底部
                    if (typeof scrollToBottom === 'function') {
                        setTimeout(scrollToBottom, 300);
                    }
                }
            }

            // 启用所有基本输入按钮
            micButton.disabled = false;
            textSendButton.disabled = false;
            textInputBox.disabled = false;
            screenshotButton.disabled = false;
            resetSessionButton.disabled = false;

            // 禁用语音控制按钮
            muteButton.disabled = true;
            screenButton.disabled = true;
            stopButton.disabled = true;
            returnSessionButton.disabled = true;

            // 重置主动搭话定时器
            if (proactiveChatEnabled && hasAnyChatModeEnabled()) {
                resetProactiveChatBackoff();
            }

            showStatusToast(window.t ? window.t('app.returning', { name: lanlan_config.lanlan_name }) : `🫴 ${lanlan_config.lanlan_name}回来了！`, 3000);

        } catch (error) {
            console.error(window.t('console.askHerBackFailed'), error);
            hideVoicePreparingToast(); // 确保失败时隐藏准备提示
            showStatusToast(window.t ? window.t('app.startFailed', { error: error.message }) : `回来失败: ${error.message}`, 5000);

            // 清除所有超时定时器和状态
            if (window.sessionTimeoutId) {
                clearTimeout(window.sessionTimeoutId);
                window.sessionTimeoutId = null;
            }
            if (sessionStartedResolver) {
                sessionStartedResolver = null;
            }
            if (sessionStartedRejecter) {
                sessionStartedRejecter = null; // 同时清理 rejecter
            }

            // 重新启用按钮，允许用户重试
            returnSessionButton.disabled = false;
        } finally {
            // 延迟重置模式切换标志
            setTimeout(() => {
                isSwitchingMode = false;
            }, 500);
        }
    });

    // 文本发送按钮事件
    textSendButton.addEventListener('click', async () => {
        const text = textInputBox.value.trim();
        const hasScreenshots = screenshotsList.children.length > 0;

        // 如果既没有文本也没有截图，静默返回
        if (!text && !hasScreenshots) {
            return;
        }

        // 用户主动发送文本时，记录时间戳并重置主动搭话计时器
        lastUserInputTime = Date.now();
        resetProactiveChatBackoff();

        // 如果还没有启动session，先启动
        if (!isTextSessionActive) {
            // 临时禁用文本输入
            textSendButton.disabled = true;
            textInputBox.disabled = true;
            screenshotButton.disabled = true;
            resetSessionButton.disabled = false;

            showStatusToast(window.t ? window.t('app.initializingText') : '正在初始化文本对话...', 3000);

            try {
                // 创建一个 Promise 来等待 session_started 消息
                const sessionStartPromise = new Promise((resolve, reject) => {
                    sessionStartedResolver = resolve;
                    sessionStartedRejecter = reject;

                    // 清除之前的超时定时器（如果存在），防止旧 attempt 的 rejecter 影响新 attempt
                    if (window.sessionTimeoutId) {
                        clearTimeout(window.sessionTimeoutId);
                        window.sessionTimeoutId = null;
                    }
                });

                // 启动文本session（确保 WebSocket 已连接）
                await ensureWebSocketOpen();
                socket.send(JSON.stringify({
                    action: 'start_session',
                    input_type: 'text',
                    new_session: false
                }));

                // 在 WebSocket 确认连接后才开始超时计时（与 mic button 流程对齐）
                window.sessionTimeoutId = setTimeout(() => {
                    if (sessionStartedRejecter) {
                        const rejecter = sessionStartedRejecter;
                        sessionStartedResolver = null;
                        sessionStartedRejecter = null;
                        window.sessionTimeoutId = null;

                        if (socket && socket.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({ action: 'end_session' }));
                            console.log('[TextSession] timeout → sent end_session');
                        }

                        rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                    }
                }, 15000);

                // 等待session真正启动成功
                await sessionStartPromise;

                isTextSessionActive = true;
                await showCurrentModel(); // 智能显示当前模型（VRM或Live2D）

                // 重新启用文本输入
                textSendButton.disabled = false;
                textInputBox.disabled = false;
                screenshotButton.disabled = false;

                showStatusToast(window.t ? window.t('app.textChattingShort') : '正在文本聊天中', 2000);
            } catch (error) {
                console.error(window.t('console.startTextSessionFailed'), error);
                hideVoicePreparingToast(); // 确保失败时隐藏准备提示
                showStatusToast(window.t ? window.t('app.startFailed', { error: error.message }) : `启动失败: ${error.message}`, 5000);

                // 清除超时定时器和 Promise 状态，防止跨 attempt 污染
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                    window.sessionTimeoutId = null;
                }
                sessionStartedResolver = null;
                sessionStartedRejecter = null;

                // 重新启用按钮，允许用户重试
                textSendButton.disabled = false;
                textInputBox.disabled = false;
                screenshotButton.disabled = false;

                return; // 启动失败，不继续发送消息
            }
        }

        // 发送消息
        if (socket.readyState === WebSocket.OPEN) {
            // 先发送所有截图
            if (hasScreenshots) {
                const screenshotItems = Array.from(screenshotsList.children);
                for (const item of screenshotItems) {
                    const img = item.querySelector('.screenshot-thumbnail');
                    if (img && img.src) {
                        socket.send(JSON.stringify({
                            action: 'stream_data',
                            data: img.src,
                            input_type: isMobile() ? 'camera' : 'screen'
                        }));
                    }
                }

                // 在聊天界面显示截图提示
                const screenshotCount = screenshotItems.length;
                appendMessage(`📸 [已发送${screenshotCount}张截图]`, 'user', true);

                // 【成就】解锁发送图片成就
                if (window.unlockAchievement) {
                    window.unlockAchievement('ACH_SEND_IMAGE').catch(err => {
                        console.error('解锁发送图片成就失败:', err);
                    });
                }

                // 清空截图列表
                screenshotsList.innerHTML = '';
                screenshotThumbnailContainer.classList.remove('show');
                updateScreenshotCount();
            }

            // 再发送文本（如果有）
            if (text) {
                socket.send(JSON.stringify({
                    action: 'stream_data',
                    data: text,
                    input_type: 'text'
                }));

                // 清空输入框
                textInputBox.value = '';

                // 在聊天界面显示用户消息
                appendMessage(text, 'user', true);

                // 【成就】检测"喵"相关内容
                if (window.incrementAchievementCounter) {
                    const meowPattern = /喵|miao|meow|nya|にゃ/i;
                    if (meowPattern.test(text)) {
                        try {
                            window.incrementAchievementCounter('meowCount');
                        } catch (error) {
                            console.debug('增加喵喵计数失败:', error);
                        }
                    }
                }

                // 如果是用户第一次输入，更新状态并检查成就
                if (isFirstUserInput) {
                    isFirstUserInput = false;
                    console.log(window.t('console.userFirstInputDetected'));
                    checkAndUnlockFirstDialogueAchievement();
                }
            }

            // 文本聊天后，重置主动搭话计时器（如果已开启）
            if (proactiveChatEnabled && hasAnyChatModeEnabled()) {
                resetProactiveChatBackoff();
            }

            showStatusToast(window.t ? window.t('app.textChattingShort') : '正在文本聊天中', 2000);
        } else {
            showStatusToast(window.t ? window.t('app.websocketNotConnected') : 'WebSocket未连接！', 4000);
        }
    });

    // 支持Enter键发送（Shift+Enter换行）
    textInputBox.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            textSendButton.click();
        }
    });

    // 截图按钮事件
    screenshotButton.addEventListener('click', async () => {
        let captureStream = null;

        try {
            screenshotButton.disabled = true;
            showStatusToast(window.t ? window.t('app.capturing') : '正在截图...', 2000);

            let dataUrl = null;
            let width = 0, height = 0;

            if (isMobile()) {
                captureStream = await getMobileCameraStream();
            } else {
                // 桌面端：尝试 getDisplayMedia
                try {
                    if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
                        captureStream = await navigator.mediaDevices.getDisplayMedia({
                            video: { cursor: 'always' },
                            audio: false,
                        });
                    } else {
                        throw new Error('UNSUPPORTED_API');
                    }
                } catch (displayErr) {
                    // 用户取消不做兜底
                    if (displayErr.name === 'NotAllowedError') throw displayErr;

                    console.warn('[截图] getDisplayMedia 失败，尝试后端截图:', displayErr);
                    const { dataUrl: backendDataUrl } = await fetchBackendScreenshot();
                    if (backendDataUrl) {
                        dataUrl = backendDataUrl;
                    } else {
                        throw displayErr;
                    }
                }
            }

            // 如果通过流获取（前端路径），从流中提取帧
            if (!dataUrl && captureStream) {
                const video = document.createElement('video');
                video.srcObject = captureStream;
                video.autoplay = true;
                video.muted = true;
                await video.play();
                const frame = captureCanvasFrame(video);
                if (frame) {
                    dataUrl = frame.dataUrl;
                    width = frame.width;
                    height = frame.height;
                }
                video.srcObject = null;
                video.remove();
            }

            if (!dataUrl) {
                throw new Error('所有截图方式均失败');
            }

            if (width && height) {
                console.log(window.t('console.screenshotSuccess'), `${width}x${height}`);
            }

            addScreenshotToList(dataUrl);
            showStatusToast(window.t ? window.t('app.screenshotAdded') : '截图已添加，点击发送一起发送', 3000);

        } catch (err) {
            console.error(window.t('console.screenshotFailed'), err);

            let errorMsg = window.t ? window.t('app.screenshotFailed') : '截图失败';
            if (err.message === 'UNSUPPORTED_API') {
                errorMsg = window.t ? window.t('app.screenshotUnsupported') : '当前浏览器不支持屏幕截图功能';
            } else if (err.name === 'NotAllowedError') {
                errorMsg = window.t ? window.t('app.screenshotCancelled') : '用户取消了截图';
            } else if (err.name === 'NotFoundError') {
                errorMsg = window.t ? window.t('app.deviceNotFound') : '未找到可用的媒体设备';
            } else if (err.name === 'NotReadableError') {
                errorMsg = window.t ? window.t('app.deviceNotAccessible') : '无法访问媒体设备';
            } else if (err.message) {
                errorMsg = window.t ? window.t('app.screenshotFailed') + ': ' + err.message : `截图失败: ${err.message}`;
            }

            showStatusToast(errorMsg, 5000);
        } finally {
            if (captureStream instanceof MediaStream) {
                captureStream.getTracks().forEach(track => track.stop());
            }
            screenshotButton.disabled = false;
        }
    });

    // 添加截图到列表
    function addScreenshotToList(dataUrl) {
        screenshotCounter++;

        // 创建截图项容器
        const item = document.createElement('div');
        item.className = 'screenshot-item';
        item.dataset.index = screenshotCounter;

        // 创建缩略图
        const img = document.createElement('img');
        img.className = 'screenshot-thumbnail';
        img.src = dataUrl;
        img.alt = window.t ? window.t('chat.screenshotAlt', { index: screenshotCounter }) : `截图 ${screenshotCounter}`;
        img.title = window.t ? window.t('chat.screenshotTitle', { index: screenshotCounter }) : `点击查看截图 ${screenshotCounter}`;

        // 点击缩略图可以在新标签页查看大图
        img.addEventListener('click', () => {
            window.open(dataUrl, '_blank');
        });

        // 创建删除按钮
        const removeBtn = document.createElement('button');
        removeBtn.className = 'screenshot-remove';
        removeBtn.innerHTML = '×';
        removeBtn.title = window.t ? window.t('chat.removeScreenshot') : '移除此截图';
        removeBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            removeScreenshotFromList(item);
        });

        // 创建索引标签
        const indexLabel = document.createElement('span');
        indexLabel.className = 'screenshot-index';
        indexLabel.textContent = `#${screenshotCounter}`;

        // 组装元素
        item.appendChild(img);
        item.appendChild(removeBtn);
        item.appendChild(indexLabel);

        // 添加到列表
        screenshotsList.appendChild(item);

        // 更新计数和显示容器
        updateScreenshotCount();
        screenshotThumbnailContainer.classList.add('show');

        // 自动滚动到最新的截图
        setTimeout(() => {
            screenshotsList.scrollLeft = screenshotsList.scrollWidth;
        }, 100);
    }

    // 从列表中移除截图
    function removeScreenshotFromList(item) {
        item.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => {
            item.remove();
            updateScreenshotCount();

            // 如果没有截图了，隐藏容器
            if (screenshotsList.children.length === 0) {
                screenshotThumbnailContainer.classList.remove('show');
            }
        }, 300);
    }

    // 更新截图计数
    function updateScreenshotCount() {
        const count = screenshotsList.children.length;
        screenshotCount.textContent = count;
    }

    // 清空所有截图
    clearAllScreenshots.addEventListener('click', async () => {
        if (screenshotsList.children.length === 0) return;

        if (await showConfirm(
            window.t ? window.t('dialogs.clearScreenshotsConfirm') : '确定要清空所有待发送的截图吗？',
            window.t ? window.t('dialogs.clearScreenshots') : '清空截图',
            { danger: true }
        )) {
            screenshotsList.innerHTML = '';
            screenshotThumbnailContainer.classList.remove('show');
            updateScreenshotCount();
        }
    });

    // 情感分析功能
    async function analyzeEmotion(text) {
        console.log(window.t('console.analyzeEmotionCalled'), text);
        try {
            const response = await fetch('/api/emotion/analysis', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    text: text,
                    lanlan_name: lanlan_config.lanlan_name
                })
            });

            if (!response.ok) {
                console.warn(window.t('console.emotionAnalysisRequestFailed'), response.status);
                return null;
            }

            const result = await response.json();
            console.log(window.t('console.emotionAnalysisApiResult'), result);

            if (result.error) {
                console.warn(window.t('console.emotionAnalysisError'), result.error);
                return null;
            }

            return result;
        } catch (error) {
            console.error(window.t('console.emotionAnalysisException'), error);
            return null;
        }
    }

    // 应用情感到Live2D模型
    function applyEmotion(emotion) {
        if (window.LanLan1 && window.LanLan1.setEmotion) {
            console.log('调用window.LanLan1.setEmotion:', emotion);
            window.LanLan1.setEmotion(emotion);
        } else {
            console.warn('情感功能未初始化');
        }
    }

    // 启动麦克风静音检测
    function startSilenceDetection() {
        // 重置检测状态
        hasSoundDetected = false;

        // 清除之前的定时器(如果有)
        if (silenceDetectionTimer) {
            clearTimeout(silenceDetectionTimer);
        }

        // 启动5秒定时器
        silenceDetectionTimer = setTimeout(() => {
            if (!hasSoundDetected && isRecording) {
                showStatusToast(window.t ? window.t('app.micNoSound') : '⚠️ 麦克风无声音，请检查麦克风设置', 5000);
                console.warn('麦克风静音检测：5秒内未检测到声音');
            }
        }, 5000);
    }

    // 停止麦克风静音检测
    function stopSilenceDetection() {
        if (silenceDetectionTimer) {
            clearTimeout(silenceDetectionTimer);
            silenceDetectionTimer = null;
        }
        hasSoundDetected = false;
    }

    // 监测音频输入音量
    function monitorInputVolume() {
        if (!inputAnalyser || !isRecording) {
            return;
        }

        const dataArray = new Uint8Array(inputAnalyser.fftSize);
        inputAnalyser.getByteTimeDomainData(dataArray);

        // 计算音量(RMS)
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            const val = (dataArray[i] - 128) / 128.0;
            sum += val * val;
        }
        const rms = Math.sqrt(sum / dataArray.length);

        // 如果音量超过阈值(0.01),认为检测到声音
        if (rms > 0.01) {
            if (!hasSoundDetected) {
                hasSoundDetected = true;
                console.log('麦克风静音检测：检测到声音，RMS =', rms);

                // 如果之前显示了无声音警告，现在检测到声音了，恢复正常状态显示
                // 检查隐藏的 status 元素是否包含无声音警告（保持兼容性）
                const noSoundText = window.t ? window.t('voiceControl.noSound') : '麦克风无声音';
                if (statusElement && statusElement.textContent.includes(noSoundText)) {
                    showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);
                    console.log('麦克风静音检测：检测到声音，已清除警告');
                }
            }
        }

        // 持续监测
        if (isRecording) {
            requestAnimationFrame(monitorInputVolume);
        }
    }

    // 使用AudioWorklet开始音频处理
    async function startAudioWorklet(stream) {
        // 先清理旧的音频上下文，防止多个 worklet 同时发送数据导致 QPS 超限
        if (audioContext) {
            // 只有在未关闭状态下才尝试关闭
            if (audioContext.state !== 'closed') {
                try {
                    await audioContext.close();
                } catch (e) {
                    console.warn('关闭旧音频上下文时出错:', e);
                    // 强制复位所有状态，防止状态不一致
                    micButton.classList.remove('recording', 'active');
                    syncFloatingMicButtonState(false);
                    syncFloatingScreenButtonState(false);
                    micButton.disabled = false;
                    muteButton.disabled = true;
                    screenButton.disabled = true;
                    stopButton.disabled = true;
                    showStatusToast(window.t ? window.t('app.audioContextError') : '音频系统异常，请重试', 3000);
                    throw e; // 重新抛出错误，阻止后续执行
                }
            }
            audioContext = null;
            workletNode = null;
        }

        // 创建音频上下文，强制使用 48kHz 采样率
        // 这确保无论设备原生采样率如何，RNNoise 都能正确处理
        // Chromium 会在必要时进行软件重采样
        audioContext = new AudioContext({ sampleRate: 48000 });
        console.log("音频上下文采样率 (强制48kHz):", audioContext.sampleRate);

        // 创建媒体流源
        const source = audioContext.createMediaStreamSource(stream);

        // 创建增益节点用于麦克风音量放大
        micGainNode = audioContext.createGain();
        const linearGain = dbToLinear(microphoneGainDb);
        micGainNode.gain.value = linearGain;
        console.log(`麦克风增益已设置: ${microphoneGainDb}dB (${linearGain.toFixed(2)}x)`);

        // 创建analyser节点用于监测输入音量
        inputAnalyser = audioContext.createAnalyser();
        inputAnalyser.fftSize = 2048;
        inputAnalyser.smoothingTimeConstant = 0.8;

        // 连接 source → gainNode → analyser（用于音量检测，检测增益后的音量）
        source.connect(micGainNode);
        micGainNode.connect(inputAnalyser);

        try {
            // 加载AudioWorklet处理器
            await audioContext.audioWorklet.addModule('/static/audio-processor.js');

            // 根据连接类型确定目标采样率：
            // - 手机端直连API服务器：16kHz（API要求）
            // - 电脑端本地浏览：48kHz（RNNoise处理后后端降采样）
            // - 手机端连接电脑端：使用WebRTC（浏览器处理）
            const targetSampleRate = isMobile() ? 16000 : 48000;
            console.log(`音频采样率配置: 原始=${audioContext.sampleRate}Hz, 目标=${targetSampleRate}Hz, 移动端=${isMobile()}`);

            // 创建AudioWorkletNode
            workletNode = new AudioWorkletNode(audioContext, 'audio-processor', {
                processorOptions: {
                    originalSampleRate: audioContext.sampleRate,
                    targetSampleRate: targetSampleRate
                }
            });

            // 监听处理器发送的消息
            workletNode.port.onmessage = (event) => {
                const audioData = event.data;

                // Focus模式：focusModeEnabled为true且AI正在播放语音时，自动静音麦克风（不回传麦克风音频）
                if (focusModeEnabled === true && isPlaying === true) {
                    // 处于focus模式且AI语音播放中，跳过回传麦克风音频，实现自动静音
                    return;
                }

                if (isRecording && socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: Array.from(audioData),
                        input_type: 'audio'
                    }));
                }
            };

            // 连接节点：gainNode → workletNode（音频经过增益处理后发送）
            micGainNode.connect(workletNode);
            // 不需要连接到destination，因为我们不需要听到声音
            // workletNode.connect(audioContext.destination);
            // 所有初始化成功后，才标记为录音状态
            isRecording = true;
            window.isRecording = true;

        } catch (err) {
            console.error('加载AudioWorklet失败:', err);
            console.dir(err); // <--- 使用 console.dir()
            showStatusToast(window.t ? window.t('app.audioWorkletFailed') : 'AudioWorklet加载失败', 5000);
            stopSilenceDetection();
        }
    }


    // 停止录屏
    function stopScreening() {
        if (videoSenderInterval) {
            clearInterval(videoSenderInterval);
            videoSenderInterval = null;
        }
    }

    // 停止录音
    function stopRecording() {
        // 停止语音期间主动视觉定时
        stopProactiveVisionDuringSpeech();
        // 【新增】输入结束/打断时重置搜歌任务
        window.invalidatePendingMusicSearch();

        stopScreening();
        if (!isRecording) return;

        isRecording = false;
        window.isRecording = false;
        window.currentGeminiMessage = null;

        // 重置语音模式用户转录合并追踪
        lastVoiceUserMessage = null;
        lastVoiceUserMessageTime = 0;

        // 清理 AI 回复相关的队列和缓冲区（防止影响后续会话）
        window._realisticGeminiQueue = [];
        window._realisticGeminiBuffer = '';
        window._geminiTurnFullText = '';
        window._pendingMusicCommand = '';
        window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
        window.currentTurnGeminiBubbles = [];
        window._isProcessingRealisticQueue = false;

        // 停止静音检测
        stopSilenceDetection();

        // 清理输入analyser
        inputAnalyser = null;

        // 停止所有轨道
        if (stream) {
            stream.getTracks().forEach(track => track.stop());
        }

        // 关闭AudioContext
        if (audioContext) {
            // 只有在未关闭状态下才关闭，防止重复关闭导致错误
            if (audioContext.state !== 'closed') {
                audioContext.close();
            }
            audioContext = null;
            workletNode = null;
        }

        // 通知服务器暂停会话
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                action: 'pause_session'
            }));
        }
        // statusElement.textContent = '录制已停止';
    }

    // 清空音频队列并停止所有播放
    async function clearAudioQueue() {
        scheduledSources.forEach(source => {
            try { source.stop(); } catch (_) { }
        });
        scheduledSources = [];
        audioBufferQueue = [];
        isPlaying = false;
        audioStartTime = 0;
        nextChunkTime = 0;

        await resetOggOpusDecoder();
    }

    // 清空音频队列但不重置解码器（用于精确打断控制）
    function clearAudioQueueWithoutDecoderReset() {
        scheduledSources.forEach(source => {
            try { source.stop(); } catch (_) { }
        });
        scheduledSources = [];
        audioBufferQueue = [];
        isPlaying = false;
        audioStartTime = 0;
        nextChunkTime = 0;

        // 注意：不调用 resetOggOpusDecoder()！
        // 解码器将在收到新 speech_id 时才重置，避免丢失头信息
    }


    function scheduleAudioChunks() {
        if (scheduleAudioChunksRunning) return;
        scheduleAudioChunksRunning = true;

        try {

            const scheduleAheadTime = 5;

            initializeGlobalAnalyser();
            // 若初始化仍失败，兜底直接将后续 source 连接到 destination，避免静音
            const hasAnalyser = !!globalAnalyser;

            // 关键：预调度所有在lookahead时间内的chunk
            while (nextChunkTime < audioPlayerContext.currentTime + scheduleAheadTime) {
                if (audioBufferQueue.length > 0) {
                    const { buffer: nextBuffer } = audioBufferQueue.shift();
                    if (window.DEBUG_AUDIO) {
                        console.log('ctx', audioPlayerContext.sampleRate,
                            'buf', nextBuffer.sampleRate);
                    }

                    const source = audioPlayerContext.createBufferSource();
                    source.buffer = nextBuffer;
                    if (hasAnalyser) {
                        source.connect(globalAnalyser);
                    } else {
                        source.connect(audioPlayerContext.destination);
                    }

                    if (hasAnalyser && !lipSyncActive) {
                        if (window.DEBUG_AUDIO) {
                            console.log('[Audio] 尝试启动口型同步:', {
                                hasLanLan1: !!window.LanLan1,
                                hasLive2dModel: !!(window.LanLan1 && window.LanLan1.live2dModel),
                                hasVrmManager: !!window.vrmManager,
                                hasVrmModel: !!(window.vrmManager && window.vrmManager.currentModel)
                            });
                        }
                        if (window.LanLan1 && window.LanLan1.live2dModel) {
                            startLipSync(window.LanLan1.live2dModel, globalAnalyser);
                            lipSyncActive = true;
                        } else if (window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
                            // VRM模型的口型同步
                            if (typeof window.vrmManager.animation.startLipSync === 'function') {
                                window.vrmManager.animation.startLipSync(globalAnalyser);
                                lipSyncActive = true;
                            }
                        } else {
                            if (window.DEBUG_AUDIO) {
                                console.warn('[Audio] 无法启动口型同步：没有可用的模型');
                            }
                        }
                    }

                    // 精确时间调度
                    source.start(nextChunkTime);
                    // console.log(`调度chunk在时间: ${nextChunkTime.toFixed(3)}`);

                    // 设置结束回调处理lipSync停止
                    source.onended = () => {
                        // if (window.LanLan1 && window.LanLan1.live2dModel) {
                        //     stopLipSync(window.LanLan1.live2dModel);
                        // }
                        const index = scheduledSources.indexOf(source);
                        if (index !== -1) {
                            scheduledSources.splice(index, 1);
                        }

                        if (scheduledSources.length === 0 && audioBufferQueue.length === 0) {
                            if (window.LanLan1 && window.LanLan1.live2dModel) {
                                stopLipSync(window.LanLan1.live2dModel);
                            } else if (window.vrmManager && window.vrmManager.currentModel && window.vrmManager.animation) {
                                // VRM模型停止口型同步
                                if (typeof window.vrmManager.animation.stopLipSync === 'function') {
                                    window.vrmManager.animation.stopLipSync();
                                }
                            }
                            lipSyncActive = false;
                            isPlaying = false; // 新增：所有音频播放完毕，重置isPlaying
                        }
                    };

                    // // 更新下一个chunk的时间
                    nextChunkTime += nextBuffer.duration;

                    scheduledSources.push(source);
                } else {
                    break;
                }
            }

            // 继续调度循环
            setTimeout(scheduleAudioChunks, 25); // 25ms间隔检查

        } finally {
            scheduleAudioChunksRunning = false;
        }
    }


    async function handleAudioBlob(blob, expectedEpoch = incomingAudioEpoch) {
        const arrayBuffer = await blob.arrayBuffer();
        if (expectedEpoch !== incomingAudioEpoch) {
            return;
        }
        if (!arrayBuffer || arrayBuffer.byteLength === 0) {
            console.warn('收到空的音频数据，跳过处理');
            return;
        }

        if (!audioPlayerContext) {
            audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
            syncAudioGlobals();
        }

        if (audioPlayerContext.state === 'suspended') {
            await audioPlayerContext.resume();
            if (expectedEpoch !== incomingAudioEpoch) {
                return;
            }
        }

        // 检测是否是 OGG 格式 (魔数 "OggS" = 0x4F 0x67 0x67 0x53)
        const header = new Uint8Array(arrayBuffer, 0, 4);
        const isOgg = header[0] === 0x4F && header[1] === 0x67 && header[2] === 0x67 && header[3] === 0x53;

        let float32Data;
        let sampleRate = 48000;

        if (isOgg) {
            // OGG OPUS 格式，用 WASM 流式解码
            try {
                const result = await decodeOggOpusChunk(new Uint8Array(arrayBuffer));
                if (expectedEpoch !== incomingAudioEpoch) {
                    return;
                }
                if (!result) {
                    // 数据不足，等待更多
                    return;
                }
                float32Data = result.float32Data;
                sampleRate = result.sampleRate;
            } catch (e) {
                console.error('OGG OPUS 解码失败:', e);
                return;
            }
        } else {
            // PCM Int16 格式，直接转换
            const int16Array = new Int16Array(arrayBuffer);
            float32Data = new Float32Array(int16Array.length);
            for (let i = 0; i < int16Array.length; i++) {
                float32Data[i] = int16Array[i] / 32768.0;
            }
        }

        if (!float32Data || float32Data.length === 0) {
            return;
        }
        if (expectedEpoch !== incomingAudioEpoch) {
            return;
        }

        const audioBuffer = audioPlayerContext.createBuffer(1, float32Data.length, sampleRate);
        audioBuffer.copyToChannel(float32Data, 0);

        const bufferObj = { seq: seqCounter++, buffer: audioBuffer };
        audioBufferQueue.push(bufferObj);

        let i = audioBufferQueue.length - 1;
        while (i > 0 && audioBufferQueue[i].seq < audioBufferQueue[i - 1].seq) {
            [audioBufferQueue[i], audioBufferQueue[i - 1]] =
                [audioBufferQueue[i - 1], audioBufferQueue[i]];
            i--;
        }

        if (!isPlaying) {
            const gap = (seqCounter <= 1) ? 0.03 : 0;
            nextChunkTime = Math.max(
                audioPlayerContext.currentTime + gap,
                nextChunkTime
            );
            isPlaying = true;
            scheduleAudioChunks();
        }
        // When isPlaying is already true the scheduler loop is already running via
        // its own setTimeout; no need to spawn an extra call.
    }

    function enqueueIncomingAudioBlob(blob) {
        const meta = pendingAudioChunkMetaQueue.shift();
        if (!meta) {
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] 收到无匹配 header 的音频 blob，已丢弃');
            }
            return;
        }
        if (!meta.speechId) {
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] 收到 speechId 为空的音频 blob，已丢弃');
            }
            return;
        }
        incomingAudioBlobQueue.push({
            blob,
            shouldSkip: !!meta.shouldSkip,
            speechId: meta.speechId,
            epoch: meta.epoch
        });
        if (!isProcessingIncomingAudioBlob) {
            void processIncomingAudioBlobQueue();
        }
    }

    async function processIncomingAudioBlobQueue() {
        if (isProcessingIncomingAudioBlob) return;
        isProcessingIncomingAudioBlob = true;

        try {
            while (incomingAudioBlobQueue.length > 0) {
                const item = incomingAudioBlobQueue.shift();
                if (!item) continue;
                if (item.epoch !== incomingAudioEpoch) {
                    continue;
                }

                if (item.shouldSkip) {
                    if (window.DEBUG_AUDIO) {
                        console.log('[Audio] 跳过被打断的音频 blob', item.speechId);
                    }
                    continue;
                }

                if (decoderResetPromise) {
                    const resetTask = decoderResetPromise;
                    try {
                        await resetTask;
                    } catch (e) {
                        console.warn('等待 OGG OPUS 解码器重置失败:', e);
                    } finally {
                        // 仅清理当前等待的任务，避免覆盖并发写入的新任务
                        if (decoderResetPromise === resetTask) {
                            decoderResetPromise = null;
                        }
                    }
                }
                if (item.epoch !== incomingAudioEpoch) {
                    continue;
                }

                await handleAudioBlob(item.blob, item.epoch);
            }
        } finally {
            isProcessingIncomingAudioBlob = false;
            if (incomingAudioBlobQueue.length > 0) {
                void processIncomingAudioBlobQueue();
            }
        }
    }

    function startScreenVideoStreaming(stream, input_type) {
        // 更新最后使用时间并调度闲置检查
        if (stream === screenCaptureStream) {
            screenCaptureStreamLastUsed = Date.now();
            scheduleScreenCaptureIdleCheck();
        }

        const video = document.createElement('video');
        // console.log('Ready for sharing 1')

        video.srcObject = stream;
        video.autoplay = true;
        video.muted = true;
        // console.log('Ready for sharing 2')

        videoTrack = stream.getVideoTracks()[0];
        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');

        // 定时抓取当前帧并编码为jpeg
        video.play().then(() => {
            // 计算缩放后的尺寸（保持宽高比，限制到720p）
            let targetWidth = video.videoWidth;
            let targetHeight = video.videoHeight;

            if (targetWidth > MAX_SCREENSHOT_WIDTH || targetHeight > MAX_SCREENSHOT_HEIGHT) {
                const widthRatio = MAX_SCREENSHOT_WIDTH / targetWidth;
                const heightRatio = MAX_SCREENSHOT_HEIGHT / targetHeight;
                const scale = Math.min(widthRatio, heightRatio);
                targetWidth = Math.round(targetWidth * scale);
                targetHeight = Math.round(targetHeight * scale);
                console.log(`屏幕共享：原尺寸 ${video.videoWidth}x${video.videoHeight} -> 缩放到 ${targetWidth}x${targetHeight}`);
            }

            canvas.width = targetWidth;
            canvas.height = targetHeight;

            videoSenderInterval = setInterval(() => {
                ctx.drawImage(video, 0, 0, targetWidth, targetHeight);
                const dataUrl = canvas.toDataURL('image/jpeg', 0.8); // base64 jpeg

                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: dataUrl,
                        input_type: input_type,
                    }));

                    // 刷新最后使用时间，防止活跃屏幕分享被误释放
                    if (stream === screenCaptureStream) {
                        screenCaptureStreamLastUsed = Date.now();
                    }
                }
            }, 1000);
        } // 每1000ms一帧
        )
    }

    function initializeGlobalAnalyser() {
        if (audioPlayerContext) {
            if (audioPlayerContext.state === 'suspended') {
                audioPlayerContext.resume().catch(err => {
                    console.warn('[Audio] resume() failed:', err);
                });
            }
            if (!globalAnalyser) {
                try {
                    globalAnalyser = audioPlayerContext.createAnalyser();
                    globalAnalyser.fftSize = 2048;
                    // 插入扬声器音量增益节点: source → analyser → gainNode → destination
                    speakerGainNode = audioPlayerContext.createGain();
                    const vol = (typeof window.getSpeakerVolume === 'function')
                        ? window.getSpeakerVolume() : 100;
                    speakerGainNode.gain.value = vol / 100;
                    globalAnalyser.connect(speakerGainNode);
                    speakerGainNode.connect(audioPlayerContext.destination);
                    console.log('[Audio] 全局分析器和扬声器增益节点已创建并连接');
                } catch (e) {
                    console.error('[Audio] 创建分析器失败:', e);
                }
            }
            // 无论是否新建，都同步一次全局引用
            syncAudioGlobals();

            if (window.DEBUG_AUDIO) {
                console.debug('[Audio] globalAnalyser 状态:', !!globalAnalyser);
            }
        } else {
            if (window.DEBUG_AUDIO) {
                console.warn('[Audio] audioPlayerContext 未初始化，无法创建分析器');
            }
        }
    }

    // 口型平滑状态闭包变量
    let _lastMouthOpen = 0;

    let _lipSyncSkipCounter = 0;
    const LIP_SYNC_EVERY_N_FRAMES = 2;

    function startLipSync(model, analyser) {
        console.log('[LipSync] 开始口型同步', { hasModel: !!model, hasAnalyser: !!analyser });
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
        }

        _lastMouthOpen = 0;
        _lipSyncSkipCounter = 0;

        const dataArray = new Uint8Array(analyser.fftSize);

        function animate() {
            if (!analyser) return;
            animationFrameId = requestAnimationFrame(animate);

            if (++_lipSyncSkipCounter < LIP_SYNC_EVERY_N_FRAMES) return;
            _lipSyncSkipCounter = 0;

            analyser.getByteTimeDomainData(dataArray);

            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) {
                const val = (dataArray[i] - 128) / 128;
                sum += val * val;
            }
            const rms = Math.sqrt(sum / dataArray.length);

            let mouthOpen = Math.min(1, rms * 10);
            mouthOpen = _lastMouthOpen * 0.5 + mouthOpen * 0.5;
            _lastMouthOpen = mouthOpen;

            if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
                window.LanLan1.setMouth(mouthOpen);
            }
        }

        animate();
    }

    function stopLipSync(model) {
        console.log('[LipSync] 停止口型同步');
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
            animationFrameId = null;
        }
        if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
            window.LanLan1.setMouth(0);
        } else if (model && model.internalModel && model.internalModel.coreModel) {
            // 兜底
            try { model.internalModel.coreModel.setParameterValueById("ParamMouthOpenY", 0); } catch (_) { }
        }
        lipSyncActive = false;
    }

    // 隐藏live2d函数
    function hideLive2d() {
        console.log('[App] hideLive2d函数被调用');
        const container = document.getElementById('live2d-container');
        console.log('[App] hideLive2d调用前，容器类列表:', container.classList.toString());

        // 首先清除任何可能干扰动画的强制显示样式（包括 showLive2d 设置的内联 transform）
        container.style.removeProperty('visibility');
        container.style.removeProperty('display');
        container.style.removeProperty('opacity');
        container.style.removeProperty('transform');

        // 取消 return 渐入的清理定时器（防止与退出动画冲突）
        if (window._returnFadeTimer) {
            clearTimeout(window._returnFadeTimer);
            window._returnFadeTimer = null;
        }
        // 重置 PIXI model alpha 到 1（确保退出动画时模型不透明）
        if (window.live2dManager) {
            const fadeModel = window.live2dManager.getCurrentModel();
            if (fadeModel && !fadeModel.destroyed) {
                fadeModel.alpha = 1;
            }
        }
        // 清除 canvas 上的渐入动画残留样式
        const live2dCanvasForHide = document.getElementById('live2d-canvas');
        if (live2dCanvasForHide) {
            live2dCanvasForHide.style.transition = '';
            live2dCanvasForHide.style.opacity = '';
        }

        // 添加minimized类，触发CSS过渡动画
        container.classList.add('minimized');
        console.log('[App] hideLive2d调用后，容器类列表:', container.classList.toString());

        // 添加一个延迟检查，确保类被正确添加
        setTimeout(() => {
            console.log('[App] 延迟检查容器类列表:', container.classList.toString());
        }, 100);
    }

    // 显示live2d函数
    function showLive2d() {
        console.log('[App] showLive2d函数被调用');

        // 检查是否处于"请她离开"状态，如果是则直接返回，不执行显示逻辑
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[App] showLive2d: 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }

        const container = document.getElementById('live2d-container');
        console.log('[App] showLive2d调用前，容器类列表:', container.classList.toString());

        // 【关键修复】检查Live2D浮动按钮是否存在，如果不存在则重新创建（防止切换后按钮丢失）
        let floatingButtons = document.getElementById('live2d-floating-buttons');
        console.log('[showLive2d] 检查浮动按钮 - 存在:', !!floatingButtons, 'live2dManager:', !!window.live2dManager);

        if (!floatingButtons && window.live2dManager) {
            console.log('[showLive2d] Live2D浮动按钮不存在，准备重新创建');
            const currentModel = window.live2dManager.getCurrentModel();
            console.log('[showLive2d] currentModel:', !!currentModel, 'setupFloatingButtons:', typeof window.live2dManager.setupFloatingButtons);

            if (currentModel && typeof window.live2dManager.setupFloatingButtons === 'function') {
                console.log('[showLive2d] 调用 setupFloatingButtons');
                window.live2dManager.setupFloatingButtons(currentModel);
                floatingButtons = document.getElementById('live2d-floating-buttons');
                console.log('[showLive2d] 创建后按钮存在:', !!floatingButtons);
            } else {
                console.warn('[showLive2d] 无法重新创建按钮 - currentModel或setupFloatingButtons不可用');
            }
        }

        // 确保浮动按钮显示（使用 !important 强制显示，覆盖所有其他逻辑）
        if (floatingButtons) {
            // 直接设置 !important 样式，不先清除（避免被鼠标跟踪逻辑覆盖）
            floatingButtons.style.setProperty('display', 'flex', 'important');
            floatingButtons.style.setProperty('visibility', 'visible', 'important');
            floatingButtons.style.setProperty('opacity', '1', 'important');
        }

        const lockIcon = document.getElementById('live2d-lock-icon');
        if (lockIcon) {
            lockIcon.style.removeProperty('display');
            lockIcon.style.removeProperty('visibility');
            lockIcon.style.removeProperty('opacity');
        }

        // 原生按钮和status栏应该永不出现，保持隐藏状态
        const sidebar = document.getElementById('sidebar');
        const sidebarbox = document.getElementById('sidebarbox');

        if (sidebar) {
            sidebar.style.setProperty('display', 'none', 'important');
            sidebar.style.setProperty('visibility', 'hidden', 'important');
            sidebar.style.setProperty('opacity', '0', 'important');
        }

        if (sidebarbox) {
            sidebarbox.style.setProperty('display', 'none', 'important');
            sidebarbox.style.setProperty('visibility', 'hidden', 'important');
            sidebarbox.style.setProperty('opacity', '0', 'important');
        }

        const sideButtons = document.querySelectorAll('.side-btn');
        sideButtons.forEach(btn => {
            btn.style.setProperty('display', 'none', 'important');
            btn.style.setProperty('visibility', 'hidden', 'important');
            btn.style.setProperty('opacity', '0', 'important');
        });

        const statusElement = document.getElementById('status');
        if (statusElement) {
            statusElement.style.setProperty('display', 'none', 'important');
            statusElement.style.setProperty('visibility', 'hidden', 'important');
            statusElement.style.setProperty('opacity', '0', 'important');
        }

        // 取消"请她离开"的延迟隐藏定时器（如果正在倒计时中）
        if (window._goodbyeHideTimerId) {
            clearTimeout(window._goodbyeHideTimerId);
            window._goodbyeHideTimerId = null;
            console.log('[App] showLive2d: 已取消 goodbye 延迟隐藏定时器');
        }

        // 取消上一次 return 渐入的清理定时器
        if (window._returnFadeTimer) {
            clearTimeout(window._returnFadeTimer);
            window._returnFadeTimer = null;
        }

        // ═══════════════════════════════════════════════════════════════
        // 【渐入动画 - 完全复刻 _configureLoadedModel 的 CSS 揭示机制】
        //
        // 这套机制在模型首次加载时已被证明有效。核心原理：
        // 1. 先用 CSS opacity:0 隐藏画布（canvas 可 visibility:visible 但不可见）
        // 2. PIXI 在 opacity:0 的画布中正常渲染（framebuffer 内容正确）
        // 3. 通过 CSS transition 将 opacity 从 0 过渡到 1 → 用户看到平滑淡入
        //
        // 之前失败的原因：
        // - 尝试1-3: 动画作用在 container 而非 canvas（被 container 的退出 transition 干扰）
        // - 尝试4: setInterval 在 canvas 上改 opacity，但没有 forced reflow 锁定初始 0 状态
        // - 尝试5-6: model.alpha 方式，但 canvas 可见一瞬间旧帧就被合成到屏幕
        // ═══════════════════════════════════════════════════════════════

        // 确保 model.alpha = 1（WebGL 层面完全不透明，与加载代码一致）
        const fadeModel = window.live2dManager ? window.live2dManager.getCurrentModel() : null;
        if (fadeModel && !fadeModel.destroyed) {
            fadeModel.alpha = 1;
        }

        // 第一步：在 canvas 变得可见之前，先将 CSS opacity 设为近 0
        // 用 '0.001' 而非 '0' 可避免 loadModel finally 安全网干扰（它只检查 === '0'）
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.transition = 'none';   // 禁止过渡，确保立即生效
            live2dCanvas.style.opacity = '0.001';      // CSS 层面隐藏
        }

        // 第二步：让容器立即可见（禁用 CSS 过渡，避免"离开动画倒放"）
        container.style.transition = 'none';
        container.classList.remove('hidden');
        container.classList.remove('minimized');
        container.style.visibility = 'visible';
        container.style.display = 'block';
        container.style.opacity = '1';
        container.style.transform = 'none';

        // 第三步：让 canvas 的 visibility 恢复（用 !important 覆盖 goodbye 定时器的 hidden）
        // 此时 canvas 虽然 visibility:visible，但 CSS opacity:0.001 使其对用户不可见
        if (live2dCanvas) {
            live2dCanvas.style.setProperty('visibility', 'visible', 'important');
            live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
        }

        // 第四步：强制浏览器刷新布局 —— 确保浏览器已经"看到"了 opacity:0.001 状态
        // 这是让 CSS transition 能正确从 0 开始到 1 的关键!!!
        // 如果不做 reflow，浏览器可能把 opacity:0.001 和后续的 opacity:1 合并，跳过动画
        if (live2dCanvas) {
            void live2dCanvas.offsetWidth;
        }

        // 第五步：恢复容器的 CSS 过渡（为后续"请她离开"做准备）
        container.style.transition = '';

        // 确保 PIXI ticker 在运行（长时间 hidden 后 rAF 可能被 Chromium 暂停）
        const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
        if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
            pixiApp.ticker.start();
        }

        // 第六步：触发 CSS transition 淡入（与 _configureLoadedModel 相同的机制）
        if (live2dCanvas) {
            live2dCanvas.style.transition = 'opacity 0.5s ease-out';
            live2dCanvas.style.opacity = '1';

            // 过渡完成后清除内联样式，避免干扰后续功能
            window._returnFadeTimer = setTimeout(() => {
                if (live2dCanvas) {
                    live2dCanvas.style.transition = '';
                    live2dCanvas.style.opacity = '';
                }
                window._returnFadeTimer = null;
            }, 550);
        }

        // 如果容器没有其他类，完全移除class属性以避免显示为class=""
        if (container.classList.length === 0) {
            container.removeAttribute('class');
        }

        console.log('[App] showLive2d调用后，容器类列表:', container.classList.toString());
    }

    // 智能显示当前模型（根据角色配置自动判断VRM或Live2D）
    async function showCurrentModel() {
        // 检查"请她离开"状态，如果处于该状态则直接返回，不执行显示逻辑
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }
        if (window.vrmManager && window.vrmManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态（VRM），跳过显示逻辑');
            return;
        }

        // 在显示模型前，明确重置 goodbye 标志（防止标志持久化导致模型无法显示）
        if (window.live2dManager) {
            window.live2dManager._goodbyeClicked = false;
        }
        if (window.vrmManager) {
            window.vrmManager._goodbyeClicked = false;
        }

        try {
            const charResponse = await fetch('/api/characters');
            if (!charResponse.ok) {
                console.warn('[showCurrentModel] 无法获取角色配置，默认显示Live2D');
                showLive2d();
                return;
            }

            const charactersData = await charResponse.json();
            const currentCatgirl = lanlan_config.lanlan_name;
            const catgirlConfig = charactersData['猫娘']?.[currentCatgirl];

            if (!catgirlConfig) {
                console.warn('[showCurrentModel] 未找到角色配置，默认显示Live2D');
                showLive2d();
                return;
            }

            const modelType = catgirlConfig.model_type || (catgirlConfig.vrm ? 'vrm' : 'live2d');
            console.log('[showCurrentModel] 当前角色模型类型:', modelType);

            if (modelType === 'vrm') {
                console.log('[showCurrentModel] 开始显示VRM模型');

                // 显示 VRM 模型
                const vrmContainer = document.getElementById('vrm-container');
                console.log('[showCurrentModel] vrmContainer存在:', !!vrmContainer);
                if (vrmContainer) {
                    // 取消"请她离开"的延迟隐藏定时器
                    if (window._goodbyeHideTimerId) {
                        clearTimeout(window._goodbyeHideTimerId);
                        window._goodbyeHideTimerId = null;
                    }
                    // 取消上一次 VRM canvas 渐入动画
                    if (window._vrmCanvasFadeInId) {
                        clearTimeout(window._vrmCanvasFadeInId);
                        window._vrmCanvasFadeInId = null;
                    }
                    if (window._vrmCanvasFadeInListener) {
                        const prevCanvas = document.getElementById('vrm-canvas');
                        if (prevCanvas) {
                            prevCanvas.removeEventListener('transitionend', window._vrmCanvasFadeInListener);
                        }
                        window._vrmCanvasFadeInListener = null;
                    }

                    // 【第一步】在容器可见之前，先将 VRM canvas opacity 设为 0（防止旧帧闪烁）
                    const vrmCanvasInner = document.getElementById('vrm-canvas');
                    if (vrmCanvasInner) {
                        vrmCanvasInner.style.transition = 'none';
                        vrmCanvasInner.style.opacity = '0';
                    }

                    // 禁用过渡，避免出现"离开动画倒放"效果
                    vrmContainer.style.transition = 'none';
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.classList.remove('minimized');
                    vrmContainer.style.display = 'block';
                    vrmContainer.style.visibility = 'visible';
                    vrmContainer.style.transform = 'none';
                    vrmContainer.style.opacity = '1'; // 容器直接完全可见
                    vrmContainer.style.removeProperty('pointer-events');

                    // 强制浏览器刷新样式
                    void vrmContainer.offsetWidth;
                    // 立即恢复 CSS 过渡（以便后续退出动画正常播放）
                    vrmContainer.style.transition = '';

                    // 【第二步】恢复 VRM canvas 可见性并启动 CSS transition 渐入动画
                    if (vrmCanvasInner) {
                        vrmCanvasInner.style.setProperty('visibility', 'visible', 'important');
                        vrmCanvasInner.style.setProperty('pointer-events', 'auto', 'important');

                        // 强制浏览器刷新 canvas 的 opacity:0 状态（确保 transition 从 0 开始）
                        void vrmCanvasInner.offsetWidth;

                        // 使用 CSS transition 渐入（与 Live2D 一致，GPU 加速更流畅）
                        vrmCanvasInner.style.transition = 'opacity 0.5s ease-out';
                        vrmCanvasInner.style.opacity = '1';

                        // 过渡完成后清除内联样式，避免干扰后续功能
                        const cleanupFadeIn = () => {
                            vrmCanvasInner.removeEventListener('transitionend', window._vrmCanvasFadeInListener);
                            window._vrmCanvasFadeInListener = null;
                            if (window._vrmCanvasFadeInId) {
                                clearTimeout(window._vrmCanvasFadeInId);
                                window._vrmCanvasFadeInId = null;
                            }
                            vrmCanvasInner.style.transition = '';
                            vrmCanvasInner.style.opacity = '';
                        };
                        window._vrmCanvasFadeInListener = (e) => {
                            if (e.propertyName === 'opacity') cleanupFadeIn();
                        };
                        vrmCanvasInner.addEventListener('transitionend', window._vrmCanvasFadeInListener);
                        // 安全超时兜底，防止 transitionend 不触发
                        window._vrmCanvasFadeInId = setTimeout(cleanupFadeIn, 1000);
                    }
                    console.log('[showCurrentModel] 已设置vrmContainer可见（带canvas渐入动画）');
                }

                // 恢复 VRM canvas 的可见性（确保 handleReturnClick 后续不会再干扰）
                const vrmCanvas = document.getElementById('vrm-canvas');
                console.log('[showCurrentModel] vrmCanvas存在:', !!vrmCanvas);
                if (vrmCanvas) {
                    vrmCanvas.style.setProperty('visibility', 'visible', 'important');
                    vrmCanvas.style.setProperty('pointer-events', 'auto', 'important');
                    console.log('[showCurrentModel] 已设置vrmCanvas可见');
                }

                // 确保Live2D隐藏
                const live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'none';
                    live2dContainer.classList.add('hidden');
                }

                // 【关键修复】检查VRM浮动按钮是否存在，如果不存在则重新创建（防止cleanupUI后按钮丢失）
                let vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                console.log('[showCurrentModel] VRM浮动按钮存在:', !!vrmFloatingButtons, 'vrmManager存在:', !!window.vrmManager);

                if (!vrmFloatingButtons && window.vrmManager && typeof window.vrmManager.setupFloatingButtons === 'function') {
                    console.log('[showCurrentModel] VRM浮动按钮不存在，重新创建');
                    window.vrmManager.setupFloatingButtons();
                    vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                    console.log('[showCurrentModel] 创建后VRM浮动按钮存在:', !!vrmFloatingButtons);
                }

                // VRM 浮动按钮交给 vrm-ui-buttons 内部显隐逻辑管理（避免强制常显）
                if (vrmFloatingButtons) {
                    vrmFloatingButtons.style.removeProperty('display');
                    vrmFloatingButtons.style.removeProperty('visibility');
                    vrmFloatingButtons.style.removeProperty('opacity');
                }

                // VRM 锁图标同样交给 vrm-ui-buttons 自主判定显示
                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    vrmLockIcon.style.removeProperty('display');
                    vrmLockIcon.style.removeProperty('visibility');
                    vrmLockIcon.style.removeProperty('opacity');
                }

                // 设置VRM解锁状态（统一使用 core.setLocked API）
                if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                    window.vrmManager.core.setLocked(false);
                }

                //  隐藏Live2D浮动按钮和锁图标
                const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
                if (live2dFloatingButtons && !window.isInTutorial) {
                    live2dFloatingButtons.style.display = 'none';
                }
                const live2dLockIcon = document.getElementById('live2d-lock-icon');
                if (live2dLockIcon) {
                    live2dLockIcon.style.display = 'none';
                }

                //  隐藏原生按钮和status栏（与 showLive2d 保持一致）
                const sidebar = document.getElementById('sidebar');
                const sidebarbox = document.getElementById('sidebarbox');
                if (sidebar) {
                    sidebar.style.setProperty('display', 'none', 'important');
                    sidebar.style.setProperty('visibility', 'hidden', 'important');
                    sidebar.style.setProperty('opacity', '0', 'important');
                }
                if (sidebarbox) {
                    sidebarbox.style.setProperty('display', 'none', 'important');
                    sidebarbox.style.setProperty('visibility', 'hidden', 'important');
                    sidebarbox.style.setProperty('opacity', '0', 'important');
                }
                const sideButtons = document.querySelectorAll('.side-btn');
                sideButtons.forEach(btn => {
                    btn.style.setProperty('display', 'none', 'important');
                    btn.style.setProperty('visibility', 'hidden', 'important');
                    btn.style.setProperty('opacity', '0', 'important');
                });
                const statusElement = document.getElementById('status');
                if (statusElement) {
                    statusElement.style.setProperty('display', 'none', 'important');
                    statusElement.style.setProperty('visibility', 'hidden', 'important');
                    statusElement.style.setProperty('opacity', '0', 'important');
                }
            } else {
                // 显示 Live2D 模型（showLive2d 内部已有 goodbye 检查和完整的 UI 同步）
                showLive2d();

                // 确保VRM隐藏
                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'none';
                    vrmContainer.classList.add('hidden');
                }
                const vrmCanvas = document.getElementById('vrm-canvas');
                if (vrmCanvas) {
                    vrmCanvas.style.visibility = 'hidden';
                    vrmCanvas.style.pointerEvents = 'none';
                }

                // 隐藏VRM浮动按钮和锁图标
                const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                if (vrmFloatingButtons) {
                    vrmFloatingButtons.style.display = 'none';
                }
                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    vrmLockIcon.style.display = 'none';
                }
            }
        } catch (error) {
            console.error('[showCurrentModel] 失败:', error);
            showLive2d(); // 出错时默认显示Live2D
        }
    }

    window.startScreenSharing = startScreenSharing;
    window.stopScreenSharing = stopScreenSharing;
    window.screen_share = startScreenSharing;

    // 连接浮动按钮到原有功能

    // 麦克风按钮（toggle模式） — Live2D / VRM 浮动按钮共用
    window.addEventListener('live2d-mic-toggle', async (e) => {
        if (e.detail.active) {
            if (window.isRecording) {
                return;
            }
            // 如果没有活跃的语音会话，走完整的 session 启动流程（与主面板按钮一致）
            if (!micButton.classList.contains('active')) {
                micButton.click();
                return;
            }
            // 会话已建立（按钮 active），仅恢复麦克风采集（mute → unmute）
            if (typeof startMicCapture === 'function') {
                await startMicCapture();
            }
        } else {
            if (!window.isRecording) {
                return;
            }
            if (typeof stopMicCapture === 'function') {
                await stopMicCapture();
            }
        }
    });

    // 屏幕分享按钮（toggle模式）
    // 屏幕分享按钮（toggle模式）
    window.addEventListener('live2d-screen-toggle', async (e) => {
        if (e.detail.active) {
            // 开启屏幕分享
            // screenButton不存在，直接调用函数
            if (typeof startScreenSharing === 'function') {
                await startScreenSharing();
            } else {
                console.error('startScreenSharing function not found');
            }
        } else {
            // 关闭屏幕分享
            // stopButton会停止整个会话（包括语音），这里只应该停止屏幕分享
            if (typeof stopScreenSharing === 'function') {
                await stopScreenSharing();
            } else {
                console.error('stopScreenSharing function not found');
            }
        }
    });

    // Agent工具按钮（只展开弹出框，不执行操作）
    window.addEventListener('live2d-agent-click', () => {
        // 不执行任何操作，只是展开弹出框
        console.log('Agent工具按钮被点击，显示弹出框');
    });

    // 睡觉按钮（请她离开）
    window.addEventListener('live2d-goodbye-click', () => {
        console.log('[App] 请她离开按钮被点击，开始隐藏所有按钮');
        console.log('[App] 当前 goodbyeClicked 状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');

        // 第一步：立即设置标志位，防止任何后续逻辑显示按钮
        if (window.live2dManager) {
            window.live2dManager._goodbyeClicked = true;
        }
        // 为VRM管理器也设置标志位
        if (window.vrmManager) {
            window.vrmManager._goodbyeClicked = true;
        }
        console.log('[App] 设置 goodbyeClicked 为 true，当前状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined', 'VRM:', window.vrmManager ? window.vrmManager._goodbyeClicked : 'undefined');

        //  立即关闭所有弹窗，防止遗留的弹窗区域阻塞鼠标事件
        // 这里直接操作 DOM，不使用动画延迟，确保弹窗立即完全隐藏
        const allLive2dPopups = document.querySelectorAll('[id^="live2d-popup-"]');
        allLive2dPopups.forEach(popup => {
            popup.style.setProperty('display', 'none', 'important');
            popup.style.setProperty('visibility', 'hidden', 'important');
            popup.style.setProperty('opacity', '0', 'important');
            popup.style.setProperty('pointer-events', 'none', 'important');
        });
        // 关闭VRM的弹窗
        const allVrmPopups = document.querySelectorAll('[id^="vrm-popup-"]');
        allVrmPopups.forEach(popup => {
            popup.style.setProperty('display', 'none', 'important');
            popup.style.setProperty('visibility', 'hidden', 'important');
            popup.style.setProperty('opacity', '0', 'important');
            popup.style.setProperty('pointer-events', 'none', 'important');
        });
        // 同时清除所有弹窗定时器
        if (window.live2dManager && window.live2dManager._popupTimers) {
            Object.values(window.live2dManager._popupTimers).forEach(timer => {
                if (timer) clearTimeout(timer);
            });
            window.live2dManager._popupTimers = {};
        }
        console.log('[App] 已关闭所有弹窗，Live2D数量:', allLive2dPopups.length, 'VRM数量:', allVrmPopups.length);

        // 使用统一的状态管理方法重置所有浮动按钮
        if (window.live2dManager && typeof window.live2dManager.resetAllButtons === 'function') {
            window.live2dManager.resetAllButtons();
        }
        // 重置VRM的浮动按钮状态（使用统一的状态管理方法）
        if (window.vrmManager && typeof window.vrmManager.resetAllButtons === 'function') {
            window.vrmManager.resetAllButtons();
        }

        // 使用统一的 setLocked 方法设置锁定状态（同时更新图标和 canvas）
        if (window.live2dManager && typeof window.live2dManager.setLocked === 'function') {
            window.live2dManager.setLocked(true, { updateFloatingButtons: false });
        }
        // 设置VRM的锁定状态
        if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
            window.vrmManager.core.setLocked(true);
        }

        // 【修复】不立即隐藏 canvas，而是先仅禁用交互，让 CSS 过渡动画（slide + fade）完成后再隐藏
        // 之前的做法是立即设置 visibility: hidden，导致模型瞬间消失而非平滑退场
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.setProperty('pointer-events', 'none', 'important');
            console.log('[App] 已禁用 live2d-canvas 交互（pointer-events: none），等待过渡动画完成后再隐藏');
        }

        // 【关键修复】在隐藏按钮之前，先判断当前激活的模型类型
        // 通过检查容器的可见性来判断，而不是按钮的可见性（因为按钮即将被隐藏）
        const vrmContainer = document.getElementById('vrm-container');
        const live2dContainer = document.getElementById('live2d-container');
        const isVrmActive = vrmContainer &&
            vrmContainer.style.display !== 'none' &&
            !vrmContainer.classList.contains('hidden');
        console.log('[App] 判断当前模型类型 - isVrmActive:', isVrmActive);

        // 【修复】VRM 也先仅禁用交互，延迟隐藏，让过渡动画正常播放
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmContainer) {
            vrmContainer.style.setProperty('pointer-events', 'none', 'important');
            console.log('[App] 已禁用 vrm-container 交互，等待过渡动画完成后再隐藏');
        }
        if (vrmCanvas) {
            vrmCanvas.style.setProperty('pointer-events', 'none', 'important');
            console.log('[App] 已禁用 vrm-canvas 交互');
        }

        // 【修复】为 VRM 容器添加 minimized 类，触发 slide+fade 退出动画（与 Live2D 一致）
        if (isVrmActive && vrmContainer) {
            // 清除可能冲突的内联样式（由 showCurrentModel 设置）
            vrmContainer.style.removeProperty('visibility');
            vrmContainer.style.removeProperty('display');
            vrmContainer.style.removeProperty('opacity');
            vrmContainer.style.removeProperty('transform');
            // 取消 VRM canvas 渐入动画
            if (window._vrmCanvasFadeInId) {
                clearInterval(window._vrmCanvasFadeInId);
                window._vrmCanvasFadeInId = null;
            }
            // 清除 VRM canvas 上可能残留的内联 opacity
            const vrmCanvasForHide = document.getElementById('vrm-canvas');
            if (vrmCanvasForHide) {
                vrmCanvasForHide.style.opacity = '';
            }
            vrmContainer.classList.add('minimized');
            console.log('[App] 已为 vrm-container 添加 minimized 类，触发退出动画');
        }

        // 在过渡动画完成（1s）后，彻底隐藏 canvas / container，使 Electron alpha 检测认为透明
        // 保存 setTimeout ID，以便"请她回来"时取消
        if (window._goodbyeHideTimerId) clearTimeout(window._goodbyeHideTimerId);
        window._goodbyeHideTimerId = setTimeout(() => {
            window._goodbyeHideTimerId = null;
            if (live2dCanvas) {
                live2dCanvas.style.setProperty('visibility', 'hidden', 'important');
                console.log('[App] 过渡完成，已隐藏 live2d-canvas（visibility: hidden）');
            }
            if (vrmContainer) {
                vrmContainer.style.setProperty('visibility', 'hidden', 'important');
                vrmContainer.style.setProperty('display', 'none', 'important');
                console.log('[App] 过渡完成，已隐藏 vrm-container');
            }
            if (vrmCanvas) {
                vrmCanvas.style.setProperty('visibility', 'hidden', 'important');
                console.log('[App] 过渡完成，已隐藏 vrm-canvas');
            }
        }, 1100); // 比 CSS transition 的 1s 稍长，确保动画完全结束

        // 在隐藏 DOM 之前先读取 "请她离开" 按钮的位置（避免隐藏后 getBoundingClientRect 返回异常）
        // 优先读取当前激活模型的按钮位置（Live2D 或 VRM）
        const live2dGoodbyeButton = document.getElementById('live2d-btn-goodbye');
        const vrmGoodbyeButton = document.getElementById('vrm-btn-goodbye');
        let savedGoodbyeRect = null;

        // 优先使用当前显示的模型的按钮位置
        if (vrmGoodbyeButton && vrmGoodbyeButton.offsetParent !== null) {
            try {
                savedGoodbyeRect = vrmGoodbyeButton.getBoundingClientRect();
                console.log('[App] 使用VRM按钮位置');
            } catch (e) {
                savedGoodbyeRect = null;
            }
        } else if (live2dGoodbyeButton && live2dGoodbyeButton.offsetParent !== null) {
            try {
                savedGoodbyeRect = live2dGoodbyeButton.getBoundingClientRect();
                console.log('[App] 使用Live2D按钮位置');
            } catch (e) {
                savedGoodbyeRect = null;
            }
        }

        // 第二步：立即隐藏所有浮动按钮和锁按钮
        const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
        if (live2dFloatingButtons) {
            live2dFloatingButtons.style.setProperty('display', 'none', 'important');
            live2dFloatingButtons.style.setProperty('visibility', 'hidden', 'important');
            live2dFloatingButtons.style.setProperty('opacity', '0', 'important');
        }
        // 隐藏VRM的浮动按钮
        const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
        if (vrmFloatingButtons) {
            vrmFloatingButtons.style.setProperty('display', 'none', 'important');
            vrmFloatingButtons.style.setProperty('visibility', 'hidden', 'important');
            vrmFloatingButtons.style.setProperty('opacity', '0', 'important');
        }

        const live2dLockIcon = document.getElementById('live2d-lock-icon');
        if (live2dLockIcon) {
            live2dLockIcon.style.setProperty('display', 'none', 'important');
            live2dLockIcon.style.setProperty('visibility', 'hidden', 'important');
            live2dLockIcon.style.setProperty('opacity', '0', 'important');
        }
        // 隐藏VRM的锁图标
        const vrmLockIcon = document.getElementById('vrm-lock-icon');
        if (vrmLockIcon) {
            vrmLockIcon.style.setProperty('display', 'none', 'important');
            vrmLockIcon.style.setProperty('visibility', 'hidden', 'important');
            vrmLockIcon.style.setProperty('opacity', '0', 'important');
        }

        // 第三步：显示独立的"请她回来"按钮（显示在原来"请她离开"按钮的位置）
        // 优先显示当前激活模型的返回按钮
        const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
        const vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');

        // 【关键修复】使用之前判断的 isVrmActive 来决定显示哪个返回按钮
        // 不再检查按钮可见性，因为按钮已经被隐藏了
        const useVrmReturn = isVrmActive;

        // 显示Live2D的返回按钮（仅在非VRM模式时显示）
        if (!useVrmReturn && live2dReturnButtonContainer) {
            if (savedGoodbyeRect) {
                const containerWidth = live2dReturnButtonContainer.offsetWidth || 64;
                const containerHeight = live2dReturnButtonContainer.offsetHeight || 64;
                const left = Math.round(savedGoodbyeRect.left + (savedGoodbyeRect.width - containerWidth) / 2 + window.scrollX);
                const top = Math.round(savedGoodbyeRect.top + (savedGoodbyeRect.height - containerHeight) / 2 + window.scrollY);
                live2dReturnButtonContainer.style.left = `${Math.max(0, Math.min(left, window.innerWidth - containerWidth))}px`;
                live2dReturnButtonContainer.style.top = `${Math.max(0, Math.min(top, window.innerHeight - containerHeight))}px`;
                live2dReturnButtonContainer.style.transform = 'none';
            } else {
                const fallbackRight = 16;
                const fallbackBottom = 116;
                live2dReturnButtonContainer.style.right = `${fallbackRight}px`;
                live2dReturnButtonContainer.style.bottom = `${fallbackBottom}px`;
                live2dReturnButtonContainer.style.left = '';
                live2dReturnButtonContainer.style.top = '';
                live2dReturnButtonContainer.style.transform = 'none';
            }
            live2dReturnButtonContainer.style.display = 'flex';
            live2dReturnButtonContainer.style.pointerEvents = 'auto';
        } else if (live2dReturnButtonContainer) {
            // 隐藏Live2D返回按钮（如果VRM是激活的）
            live2dReturnButtonContainer.style.display = 'none';
        }

        // 显示VRM的返回按钮（仅在VRM模式时显示）
        console.log('[App] VRM返回按钮检查 - useVrmReturn:', useVrmReturn, 'vrmReturnButtonContainer存在:', !!vrmReturnButtonContainer);

        // 【关键修复】如果VRM返回按钮不存在，重新创建整个浮动按钮系统
        if (useVrmReturn && !vrmReturnButtonContainer && window.vrmManager) {
            console.log('[App] VRM返回按钮不存在，重新创建浮动按钮系统');
            if (typeof window.vrmManager.setupFloatingButtons === 'function') {
                window.vrmManager.setupFloatingButtons();
                // 重新获取返回按钮引用
                vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
                console.log('[App] 重新创建后VRM返回按钮存在:', !!vrmReturnButtonContainer);
            }
        }

        if (useVrmReturn && vrmReturnButtonContainer) {
            if (savedGoodbyeRect) {
                const containerWidth = vrmReturnButtonContainer.offsetWidth || 64;
                const containerHeight = vrmReturnButtonContainer.offsetHeight || 64;
                const left = Math.round(savedGoodbyeRect.left + (savedGoodbyeRect.width - containerWidth) / 2 + window.scrollX);
                const top = Math.round(savedGoodbyeRect.top + (savedGoodbyeRect.height - containerHeight) / 2 + window.scrollY);
                vrmReturnButtonContainer.style.left = `${Math.max(0, Math.min(left, window.innerWidth - containerWidth))}px`;
                vrmReturnButtonContainer.style.top = `${Math.max(0, Math.min(top, window.innerHeight - containerHeight))}px`;
                vrmReturnButtonContainer.style.transform = 'none';
            } else {
                const fallbackRight = 16;
                const fallbackBottom = 116;
                vrmReturnButtonContainer.style.right = `${fallbackRight}px`;
                vrmReturnButtonContainer.style.bottom = `${fallbackBottom}px`;
                vrmReturnButtonContainer.style.left = '';
                vrmReturnButtonContainer.style.top = '';
                vrmReturnButtonContainer.style.transform = 'none';
            }
            vrmReturnButtonContainer.style.display = 'flex';
            vrmReturnButtonContainer.style.pointerEvents = 'auto';
        } else if (vrmReturnButtonContainer) {
            // 隐藏VRM返回按钮（如果Live2D是激活的）
            vrmReturnButtonContainer.style.display = 'none';
        }

        // 第四步：立即隐藏所有 side-btn 按钮和侧边栏
        const sidebar = document.getElementById('sidebar');
        const sidebarbox = document.getElementById('sidebarbox');

        if (sidebar) {
            sidebar.style.setProperty('display', 'none', 'important');
            sidebar.style.setProperty('visibility', 'hidden', 'important');
            sidebar.style.setProperty('opacity', '0', 'important');
        }

        if (sidebarbox) {
            sidebarbox.style.setProperty('display', 'none', 'important');
            sidebarbox.style.setProperty('visibility', 'hidden', 'important');
            sidebarbox.style.setProperty('opacity', '0', 'important');
        }

        const sideButtons = document.querySelectorAll('.side-btn');
        sideButtons.forEach(btn => {
            btn.style.setProperty('display', 'none', 'important');
            btn.style.setProperty('visibility', 'hidden', 'important');
            btn.style.setProperty('opacity', '0', 'important');
        });

        // 第五步：自动折叠对话区
        const chatContainerEl = document.getElementById('chat-container');
        const isMobile = typeof window.isMobileWidth === 'function' ? window.isMobileWidth() : (window.innerWidth <= 768);
        const collapseClass = isMobile ? 'mobile-collapsed' : 'minimized';

        console.log('[App] 请他离开 - 检查对话区状态 - 存在:', !!chatContainerEl, '当前类列表:', chatContainerEl ? chatContainerEl.className : 'N/A', '将添加类:', collapseClass);

        if (chatContainerEl && !chatContainerEl.classList.contains(collapseClass)) {
            console.log('[App] 自动折叠对话区');
            chatContainerEl.classList.add(collapseClass);
            console.log('[App] 折叠后类列表:', chatContainerEl.className);

            // 移动端还需要隐藏内容区和输入区
            if (isMobile) {
                const chatContentWrapper = document.getElementById('chat-content-wrapper');
                const chatHeader = document.getElementById('chat-header');
                const textInputArea = document.getElementById('text-input-area');
                if (chatContentWrapper) chatContentWrapper.style.display = 'none';
                if (chatHeader) chatHeader.style.display = 'none';
                if (textInputArea) textInputArea.style.display = 'none';
            }

            // 同步更新切换按钮的状态（图标和标题）
            const toggleChatBtn = document.getElementById('toggle-chat-btn');
            if (toggleChatBtn) {
                const iconImg = toggleChatBtn.querySelector('img');
                if (iconImg) {
                    iconImg.src = '/static/icons/expand_icon_off.png';
                    iconImg.alt = window.t ? window.t('common.expand') : '展开';
                }
                toggleChatBtn.title = window.t ? window.t('common.expand') : '展开';

                // 移动端确保切换按钮可见
                if (isMobile) {
                    toggleChatBtn.style.display = 'block';
                    toggleChatBtn.style.visibility = 'visible';
                    toggleChatBtn.style.opacity = '1';
                }
            }
        }

        // 第六步：触发原有的离开逻辑（关闭会话并让live2d消失）
        if (resetSessionButton) {
            // 延迟一点点执行，确保隐藏操作已经生效
            setTimeout(() => {
                console.log('[App] 触发 resetSessionButton.click()，当前 goodbyeClicked 状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');
                resetSessionButton.click();
            }, 10);
        } else {
            console.error('[App] ❌ resetSessionButton 未找到！');
        }
    });

    // 请她回来按钮（统一处理函数，同时支持 Live2D 和 VRM）
    const handleReturnClick = async () => {
        console.log('[App] 请她回来按钮被点击，开始恢复所有界面');

        // 立即取消"请她离开"的延迟隐藏定时器，防止在恢复后被意外隐藏
        if (window._goodbyeHideTimerId) {
            clearTimeout(window._goodbyeHideTimerId);
            window._goodbyeHideTimerId = null;
            console.log('[App] handleReturnClick: 已取消 goodbye 延迟隐藏定时器');
        }

        // 第一步：同步 window 中的设置值到局部变量（防止从 l2d 页面返回时值丢失）
        if (typeof window.focusModeEnabled !== 'undefined') {
            focusModeEnabled = window.focusModeEnabled;
            console.log('[App] 同步 focusModeEnabled:', focusModeEnabled);
        }
        if (typeof window.proactiveChatEnabled !== 'undefined') {
            proactiveChatEnabled = window.proactiveChatEnabled;
            console.log('[App] 同步 proactiveChatEnabled:', proactiveChatEnabled);
        }

        // 第二步：清除"请她离开"标志
        if (window.live2dManager) {
            console.log('[App] 清除 live2dManager._goodbyeClicked，之前值:', window.live2dManager._goodbyeClicked);
            window.live2dManager._goodbyeClicked = false;
        }
        if (window.live2d) {
            window.live2d._goodbyeClicked = false;
        }
        //  清除VRM的"请她离开"标志
        if (window.vrmManager) {
            console.log('[App] 清除 vrmManager._goodbyeClicked，之前值:', window.vrmManager._goodbyeClicked);
            window.vrmManager._goodbyeClicked = false;
        }

        // 确认标志已清除
        console.log('[App] 标志清除后 - live2dManager._goodbyeClicked:', window.live2dManager?._goodbyeClicked);
        console.log('[App] 标志清除后 - vrmManager._goodbyeClicked:', window.vrmManager?._goodbyeClicked);

        // 第三步：隐藏独立的"请她回来"按钮
        const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
        if (live2dReturnButtonContainer) {
            live2dReturnButtonContainer.style.display = 'none';
            live2dReturnButtonContainer.style.pointerEvents = 'none';
        }
        //隐藏VRM的"请她回来"按钮
        const vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
        if (vrmReturnButtonContainer) {
            vrmReturnButtonContainer.style.display = 'none';
            vrmReturnButtonContainer.style.pointerEvents = 'none';
        }

        // 第四步：使用 showCurrentModel() 做最终裁决（根据角色配置决定显示哪个模型）
        // showCurrentModel 内部会处理容器显示/隐藏和按钮/锁图标同步
        try {
            await showCurrentModel();
        } catch (error) {
            console.error('[App] showCurrentModel 失败:', error);
            // 出错时默认显示 Live2D
            showLive2d();
        }

        // 恢复 VRM canvas 的可见性（如果存在）
        // 注意：如果 return 渐入动画正在播放（_vrmCanvasFadeInId 存在），
        // 不要用 removeProperty 扰动 canvas 的内联样式，否则可能中断 CSS transition
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas && !window._vrmCanvasFadeInId) {
            vrmCanvas.style.removeProperty('visibility');
            vrmCanvas.style.removeProperty('pointer-events');
            vrmCanvas.style.visibility = 'visible';
            console.log('[App] 已恢复 vrm-canvas 的可见性');
        }

        // 【关键修复】恢复 Live2D canvas 的可见性（如果存在）
        // 注意：如果 return 渐入动画正在播放（_returnFadeTimer 存在），
        // 不要用 removeProperty 扰动 canvas 的内联样式，否则可能中断 CSS transition
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas && !window._returnFadeTimer) {
            live2dCanvas.style.removeProperty('visibility');
            live2dCanvas.style.removeProperty('pointer-events');
            live2dCanvas.style.visibility = 'visible';
            live2dCanvas.style.pointerEvents = 'auto';
            console.log('[App] 已恢复 live2d-canvas 的可见性');
        }

        // 第五步：恢复锁按钮，并设置为解锁状态（用户可以拖动模型）
        const live2dLockIcon = document.getElementById('live2d-lock-icon');
        if (live2dLockIcon) {
            live2dLockIcon.style.display = 'block';
            live2dLockIcon.style.removeProperty('visibility');
            live2dLockIcon.style.removeProperty('opacity');
        }
        // 恢复VRM的锁图标
        const vrmLockIcon = document.getElementById('vrm-lock-icon');
        if (vrmLockIcon) {
            vrmLockIcon.style.removeProperty('display');
            vrmLockIcon.style.removeProperty('visibility');
            vrmLockIcon.style.removeProperty('opacity');
        }
        // 使用统一的 setLocked 方法设置解锁状态（同时更新图标和 canvas）
        if (window.live2dManager && typeof window.live2dManager.setLocked === 'function') {
            window.live2dManager.setLocked(false, { updateFloatingButtons: false });
        }
        //设置VRM的解锁状态
        if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
            window.vrmManager.core.setLocked(false);
        }

        // 第六步：恢复浮动按钮系统（使用 !important 强制显示，覆盖之前的隐藏样式）
        const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
        if (live2dFloatingButtons) {
            // 先清除所有可能的隐藏样式
            live2dFloatingButtons.style.removeProperty('display');
            live2dFloatingButtons.style.removeProperty('visibility');
            live2dFloatingButtons.style.removeProperty('opacity');

            // 使用 !important 强制显示，确保覆盖之前的隐藏样式
            live2dFloatingButtons.style.setProperty('display', 'flex', 'important');
            live2dFloatingButtons.style.setProperty('visibility', 'visible', 'important');
            live2dFloatingButtons.style.setProperty('opacity', '1', 'important');

            // 恢复所有按钮的显示状态（清除之前"请她离开"时设置的 display: 'none'）
            if (window.live2dManager && window.live2dManager._floatingButtons) {
                Object.keys(window.live2dManager._floatingButtons).forEach(btnId => {
                    const buttonData = window.live2dManager._floatingButtons[btnId];
                    if (buttonData && buttonData.button) {
                        // 清除 display 样式，让按钮正常显示
                        buttonData.button.style.removeProperty('display');
                    }
                });
            }

            // 恢复所有弹窗的交互能力（清除"请她离开"时设置的 pointer-events: none 等样式）
            const allLive2dPopups = document.querySelectorAll('[id^="live2d-popup-"]');
            allLive2dPopups.forEach(popup => {
                // 清除之前设置的 !important 样式
                popup.style.removeProperty('pointer-events');
                popup.style.removeProperty('visibility');
                // 恢复正常的 pointer-events，弹窗应当能够接收鼠标事件
                popup.style.pointerEvents = 'auto';
                // display 和 opacity 保持隐藏状态，等待用户点击按钮时再显示
            });
            console.log('[App] 已恢复所有Live2D弹窗的交互能力，数量:', allLive2dPopups.length);
        }

        // 恢复VRM浮动按钮系统：仅清理强制隐藏样式，不强制设为常显
        const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
        if (vrmFloatingButtons) {
            // 先清除所有可能的隐藏样式
            vrmFloatingButtons.style.removeProperty('display');
            vrmFloatingButtons.style.removeProperty('visibility');
            vrmFloatingButtons.style.removeProperty('opacity');

            // 恢复所有按钮的显示状态
            if (window.vrmManager && window.vrmManager._floatingButtons) {
                Object.keys(window.vrmManager._floatingButtons).forEach(btnId => {
                    const buttonData = window.vrmManager._floatingButtons[btnId];
                    if (buttonData && buttonData.button) {
                        buttonData.button.style.removeProperty('display');
                    }
                });
            }

            // 恢复VRM弹窗的交互能力（清除"请她离开"时设置的 pointer-events: none 等样式）
            const allVrmPopups = document.querySelectorAll('[id^="vrm-popup-"]');
            allVrmPopups.forEach(popup => {
                // 清除之前设置的 !important 样式
                popup.style.removeProperty('pointer-events');
                popup.style.removeProperty('visibility');
                // 恢复正常的 pointer-events，弹窗应当能够接收鼠标事件
                popup.style.pointerEvents = 'auto';
                // display 和 opacity 保持隐藏状态，等待用户点击按钮时再显示
            });
            console.log('[App] 已恢复所有VRM弹窗的交互能力，数量:', allVrmPopups.length);
        }

        // 第七步：恢复对话区
        const chatContainerEl = document.getElementById('chat-container');
        const isMobile = typeof window.isMobileWidth === 'function' ? window.isMobileWidth() : (window.innerWidth <= 768);
        const collapseClass = isMobile ? 'mobile-collapsed' : 'minimized';

        console.log('[App] 检查对话区状态 - 存在:', !!chatContainerEl, '类列表:', chatContainerEl ? chatContainerEl.className : 'N/A', '目标类:', collapseClass);

        if (chatContainerEl && (chatContainerEl.classList.contains('minimized') || chatContainerEl.classList.contains('mobile-collapsed'))) {
            console.log('[App] 自动恢复对话区');
            chatContainerEl.classList.remove('minimized');
            chatContainerEl.classList.remove('mobile-collapsed');
            console.log('[App] 恢复后类列表:', chatContainerEl.className);

            // 移动端恢复内容区
            if (isMobile) {
                const chatContentWrapper = document.getElementById('chat-content-wrapper');
                const chatHeader = document.getElementById('chat-header');
                const textInputArea = document.getElementById('text-input-area');
                if (chatContentWrapper) chatContentWrapper.style.removeProperty('display');
                if (chatHeader) chatHeader.style.removeProperty('display');
                if (textInputArea) textInputArea.style.removeProperty('display');
            }

            // 同步更新切换按钮的状态（图标和标题）
            const toggleChatBtn = document.getElementById('toggle-chat-btn');
            if (toggleChatBtn) {
                const iconImg = toggleChatBtn.querySelector('img');
                if (iconImg) {
                    iconImg.src = '/static/icons/expand_icon_off.png';
                    iconImg.alt = window.t ? window.t('common.minimize') : '最小化';
                }
                toggleChatBtn.title = window.t ? window.t('common.minimize') : '最小化';

                // 还原后滚动到底部
                if (typeof scrollToBottom === 'function') {
                    setTimeout(scrollToBottom, 300);
                }

                // 移动端恢复切换按钮样式
                if (isMobile) {
                    toggleChatBtn.style.removeProperty('display');
                    toggleChatBtn.style.removeProperty('visibility');
                    toggleChatBtn.style.removeProperty('opacity');
                }
            }
        } else {
            console.log('[App] ⚠️ 对话区未恢复 - 条件不满足');
        }

        // 第八步：恢复基本的按钮状态（但不自动开始新会话）
        // 注意：不再触发 returnSessionButton.click()，因为那会自动发送 start_session 消息
        // 用户只是想让形象回来，不需要自动开始语音或文本对话

        // 设置模式切换标志
        isSwitchingMode = true;

        // 清除所有语音相关的状态类（确保按钮不会显示为激活状态）
        micButton.classList.remove('recording');
        micButton.classList.remove('active');
        screenButton.classList.remove('active');

        // 确保停止录音状态
        isRecording = false;
        window.isRecording = false;

        // 同步更新Live2D浮动按钮的状态
        if (window.live2dManager && window.live2dManager._floatingButtons) {
            ['mic', 'screen'].forEach(buttonId => {
                const buttonData = window.live2dManager._floatingButtons[buttonId];
                if (buttonData && buttonData.button) {
                    buttonData.button.dataset.active = 'false';
                    if (buttonData.imgOff) {
                        buttonData.imgOff.style.opacity = '1';
                    }
                    if (buttonData.imgOn) {
                        buttonData.imgOn.style.opacity = '0';
                    }
                }
            });
        }

        // 启用所有基本输入按钮
        micButton.disabled = false;
        textSendButton.disabled = false;
        textInputBox.disabled = false;
        screenshotButton.disabled = false;
        resetSessionButton.disabled = false;

        // 禁用语音控制按钮（文本模式下不需要）
        muteButton.disabled = true;
        screenButton.disabled = true;
        stopButton.disabled = true;

        // 显示文本输入区
        const textInputArea = document.getElementById('text-input-area');
        if (textInputArea) {
            textInputArea.classList.remove('hidden');
        }

        // 标记文本会话为非活跃状态（用户需要手动发送消息才会开始会话）
        isTextSessionActive = false;

        // 显示欢迎消息，提示用户可以开始对话
        showStatusToast(window.t ? window.t('app.welcomeBack', { name: lanlan_config.lanlan_name }) : `🫴 ${lanlan_config.lanlan_name}回来了！`, 3000);

        // 恢复主动搭话与主动视觉调度（即使不自动开启会话）
        try {
            const currentProactiveChat = typeof window.proactiveChatEnabled !== 'undefined'
                ? window.proactiveChatEnabled
                : proactiveChatEnabled;
            const currentProactiveVision = typeof window.proactiveVisionEnabled !== 'undefined'
                ? window.proactiveVisionEnabled
                : proactiveVisionEnabled;

            if (currentProactiveChat || currentProactiveVision) {
                // 重置退避并安排下一次（scheduleProactiveChat 会检查 isRecording）
                resetProactiveChatBackoff();
            }
        } catch (e) {
            console.warn('恢复主动搭话/主动视觉失败:', e);
        }

        // 延迟重置模式切换标志
        setTimeout(() => {
            isSwitchingMode = false;
        }, 500);

        console.log('[App] 请她回来完成，未自动开始会话，等待用户主动发起对话');
    };

    // 同时监听 Live2D 和 VRM 的回来事件
    window.addEventListener('live2d-return-click', handleReturnClick);
    window.addEventListener('vrm-return-click', handleReturnClick);

    // Agent控制逻辑

    // Agent弹窗状态机
    // 状态定义：
    // - IDLE: 空闲状态，弹窗未打开
    // - CHECKING: 正在检查服务器状态（弹窗刚打开或用户操作后）
    // - ONLINE: 服务器在线，可交互
    // - OFFLINE: 服务器离线
    // - PROCESSING: 正在处理用户操作（开关切换中）
    const AgentPopupState = {
        IDLE: 'IDLE',
        CHECKING: 'CHECKING',
        ONLINE: 'ONLINE',
        OFFLINE: 'OFFLINE',
        PROCESSING: 'PROCESSING'
    };

    // 状态机实例
    const agentStateMachine = {
        _state: AgentPopupState.IDLE,
        _operationSeq: 0,           // 操作序列号，用于取消过期操作
        _checkSeq: 0,               // 检查序列号，用于防止轮询竞态
        _lastCheckTime: 0,          // 上次检查时间
        _cachedServerOnline: null,  // 缓存服务器在线状态
        _cachedFlags: null,         // 缓存的flags状态
        _popupOpen: false,          // 弹窗是否打开
        _checkLock: false,          // 防止并发检查

        // 最小检查间隔（毫秒）- 严格限制请求频率
        MIN_CHECK_INTERVAL: 3000,

        // 获取当前状态
        getState() { return this._state; },

        // 获取新的操作序列号
        nextSeq() { return ++this._operationSeq; },

        // 检查操作是否过期
        isSeqExpired(seq) { return seq !== this._operationSeq; },

        // 获取新的检查序列号
        nextCheckSeq() { return ++this._checkSeq; },

        // 获取当前检查序列号
        getCheckSeq() { return this._checkSeq; },

        // 检查检查序列号是否过期
        isCheckSeqExpired(seq) { return seq !== this._checkSeq; },

        // 状态转换（带日志）
        transition(newState, reason) {
            const oldState = this._state;
            if (oldState === newState) return;
            this._state = newState;
            console.log(`[AgentStateMachine] ${oldState} -> ${newState} (${reason})`);
            this._updateUI();
        },

        // 标记弹窗打开
        openPopup() {
            this._popupOpen = true;
            // 弹窗打开时从IDLE转为CHECKING
            if (this._state === AgentPopupState.IDLE) {
                this.transition(AgentPopupState.CHECKING, 'popup opened');
            }
        },

        // 标记弹窗关闭
        closePopup() {
            this._popupOpen = false;
            // 弹窗关闭时，如果不在处理中且总开关未开启，回到IDLE
            const masterCheckbox = document.getElementById('live2d-agent-master');
            if (this._state !== AgentPopupState.PROCESSING && (!masterCheckbox || !masterCheckbox.checked)) {
                this.transition(AgentPopupState.IDLE, 'popup closed');
                window.stopAgentAvailabilityCheck();
            }
        },

        // 开始用户操作
        startOperation() {
            this.transition(AgentPopupState.PROCESSING, 'user operation started');
            return this.nextSeq();
        },

        // 结束用户操作
        endOperation(success, serverOnline = true) {
            if (this._state !== AgentPopupState.PROCESSING) return;
            if (serverOnline) {
                this.transition(AgentPopupState.ONLINE, success ? 'operation success' : 'operation failed');
            } else {
                this.transition(AgentPopupState.OFFLINE, 'server offline');
            }
        },

        // 检查是否可以发起请求（节流）
        canCheck() {
            if (this._checkLock) return false;
            const now = Date.now();
            return (now - this._lastCheckTime) >= this.MIN_CHECK_INTERVAL;
        },

        // 记录检查时间并加锁
        recordCheck() {
            this._checkLock = true;
            this._lastCheckTime = Date.now();
        },

        // 释放检查锁
        releaseCheckLock() {
            this._checkLock = false;
        },

        // 更新缓存
        updateCache(serverOnline, flags) {
            this._cachedServerOnline = serverOnline;
            if (flags) this._cachedFlags = flags;
        },

        // Whether the master+child flags indicate agent is active
        isAgentActive() {
            const f = this._cachedFlags;
            if (!f) return false;
            const master = !!f.agent_enabled;
            const child = !!(f.computer_use_enabled || f.browser_use_enabled || f.user_plugin_enabled);
            return master && child;
        },

        // 根据状态更新所有按钮UI
        _updateUI() {
            const master = document.getElementById('live2d-agent-master');
            const keyboard = document.getElementById('live2d-agent-keyboard');
            const browser = document.getElementById('live2d-agent-browser');
            const userPlugin = document.getElementById('live2d-agent-user-plugin');
            const status = document.getElementById('live2d-agent-status');

            const syncUI = (cb) => {
                if (cb && typeof cb._updateStyle === 'function') cb._updateStyle();
            };

            switch (this._state) {
                case AgentPopupState.IDLE:
                    // 空闲：所有按钮禁用
                    if (master) { master.disabled = true; master.title = ''; syncUI(master); }
                    if (keyboard) { keyboard.disabled = true; keyboard.checked = false; keyboard.title = ''; syncUI(keyboard); }
                    if (browser) { browser.disabled = true; browser.checked = false; browser.title = ''; syncUI(browser); }
                    if (userPlugin) { userPlugin.disabled = true; userPlugin.checked = false; userPlugin.title = ''; syncUI(userPlugin); }
                    break;

                case AgentPopupState.CHECKING:
                    // 检查中：所有按钮禁用，显示查询中
                    if (master) {
                        master.disabled = true;
                        master.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                        syncUI(master);
                    }
                    if (keyboard) {
                        keyboard.disabled = true;
                        keyboard.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                        syncUI(keyboard);
                    }
                    if (browser) {
                        browser.disabled = true;
                        browser.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                        syncUI(browser);
                    }
                    if (userPlugin) {
                        userPlugin.disabled = true;
                        userPlugin.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                        syncUI(userPlugin);
                    }
                    if (status) status.textContent = window.t ? window.t('agent.status.connecting') : 'Agent服务器连接中...';
                    break;

                case AgentPopupState.ONLINE:
                    // 在线：总开关可用，子开关根据总开关和能力可用性决定
                    if (master) {
                        master.disabled = false;
                        master.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncUI(master);
                    }
                    // 子开关状态由专门的函数更新
                    break;

                case AgentPopupState.OFFLINE:
                    // 离线：总开关禁用并取消选中，子开关禁用
                    if (master) {
                        master.disabled = true;
                        master.checked = false;
                        master.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                        syncUI(master);
                    }
                    if (keyboard) { keyboard.disabled = true; keyboard.checked = false; syncUI(keyboard); }
                    if (browser) { browser.disabled = true; browser.checked = false; syncUI(browser); }
                    if (status) status.textContent = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                    if (userPlugin) { userPlugin.disabled = true; userPlugin.checked = false; syncUI(userPlugin); }
                    break;

                case AgentPopupState.PROCESSING:
                    // 处理中：所有按钮禁用，防止重复操作
                    if (master) { master.disabled = true; syncUI(master); }
                    if (keyboard) { keyboard.disabled = true; syncUI(keyboard); }
                    if (browser) { browser.disabled = true; syncUI(browser); }
                    if (userPlugin) { userPlugin.disabled = true; syncUI(userPlugin); }
                    break;
            }
        }
    };

    // 暴露状态机给外部使用
    window.agentStateMachine = agentStateMachine;
    window._agentStatusSnapshot = window._agentStatusSnapshot || null;

    // Agent 定时检查器
    let agentCheckInterval = null;
    let lastFlagsSyncTime = 0;
    const FLAGS_SYNC_INTERVAL = 3000; // 3秒同步一次后端flags状态
    let connectionFailureCount = 0; // 连接失败计数

    // 【改用状态机】追踪 Agent 弹窗是否打开
    let isAgentPopupOpen = false;

    // 检查 Agent 能力（供轮询使用）- 使用状态机控制
    const checkAgentCapabilities = async () => {
        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentBrowserCheckbox = document.getElementById('live2d-agent-browser');
        const agentUserPluginCheckbox = document.getElementById('live2d-agent-user-plugin');

        // 【状态机控制】如果正在处理用户操作，跳过轮询
        if (agentStateMachine.getState() === AgentPopupState.PROCESSING) {
            console.log('[App] 状态机处于PROCESSING状态，跳过轮询');
            return;
        }

        // 只有当总开关关闭 且 弹窗未打开时，才停止轮询
        if (!agentMasterCheckbox || (!agentMasterCheckbox.checked && !agentStateMachine._popupOpen)) {
            console.log('[App] Agent总开关未开启且弹窗已关闭，停止可用性轮询');
            window.stopAgentAvailabilityCheck();
            return;
        }

        // 如果总开关未开启，跳过能力检查和flags同步，只在需要时进行连通性检查
        if (!agentMasterCheckbox.checked) {
            // 弹窗打开但总开关未开启时，使用状态机缓存判断，减少请求
            if (!agentStateMachine.canCheck()) {
                // 使用缓存状态通过状态机统一更新UI
                if (agentStateMachine._cachedServerOnline === true) {
                    agentStateMachine.transition(AgentPopupState.ONLINE, 'cached online');
                } else if (agentStateMachine._cachedServerOnline === false) {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'cached offline');
                }
                return;
            }

            // 执行连通性检查
            agentStateMachine.recordCheck();
            try {
                const healthOk = await checkToolServerHealth();
                agentStateMachine.updateCache(healthOk, null);

                // 【竞态保护】检查完成后，如果弹窗已关闭，跳过UI更新
                if (!agentStateMachine._popupOpen) {
                    console.log('[App] 轮询检查完成但弹窗已关闭，跳过UI更新');
                    return;
                }

                // 通过状态机统一更新UI
                if (healthOk) {
                    const wasOffline = agentStateMachine.getState() !== AgentPopupState.ONLINE;
                    agentStateMachine.transition(AgentPopupState.ONLINE, 'server online');
                    if (wasOffline) {
                        setFloatingAgentStatus(window.t ? window.t('agent.status.ready') : 'Agent服务器就绪');
                    }
                    // 连接恢复，重置失败计数
                    connectionFailureCount = 0;
                } else {
                    setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动');
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'server offline');
                }
            } catch (e) {
                agentStateMachine.updateCache(false, null);
                // 【竞态保护】弹窗已关闭时不更新UI，通过状态机统一更新
                if (agentStateMachine._popupOpen) {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'check failed');
                }
            } finally {
                // 确保释放检查锁
                agentStateMachine.releaseCheckLock();
            }
            return;
        }

        // 存储能力检查结果，用于后续 flags 同步时的判断
        const capabilityResults = {};
        let capabilityCheckFailed = false;

        // 【减少能力检查频率】只在必要时检查子功能可用性
        const checks = [
            { id: 'live2d-agent-keyboard', capability: 'computer_use', flagKey: 'computer_use_enabled', nameKey: 'keyboardControl' },
            { id: 'live2d-agent-browser', capability: 'browser_use', flagKey: 'browser_use_enabled', nameKey: 'browserUse' },
            { id: 'live2d-agent-user-plugin', capability: 'user_plugin', flagKey: 'user_plugin_enabled', nameKey: 'userPlugin' }
        ];
        for (const { id, capability, flagKey, nameKey } of checks) {
            const cb = document.getElementById(id);
            if (!cb) continue;

            const name = window.t ? window.t(`settings.toggles.${nameKey}`) : nameKey;

            // 如果在处理中，跳过
            if (cb._processing) continue;

            // 再次检查总开关
            if (!agentMasterCheckbox.checked) {
                cb.disabled = true;
                if (typeof cb._updateStyle === 'function') cb._updateStyle();
                continue;
            }

            try {
                const available = await checkCapability(capability, false);
                capabilityResults[flagKey] = available;

                // 检查完成后再次确认总开关仍然开启
                if (!agentMasterCheckbox.checked) {
                    cb.disabled = true;
                    if (typeof cb._updateStyle === 'function') cb._updateStyle();
                    continue;
                }

                cb.disabled = !available;
                cb.title = available ? name : (window.t ? window.t('settings.toggles.unavailable', { name: name }) : `${name}不可用`);
                if (typeof cb._updateStyle === 'function') cb._updateStyle();

                // 如果不可用但开关是开的，需要关闭它并通知后端
                if (!available && cb.checked) {
                    console.log(`[App] ${name}变为不可用，自动关闭`);
                    cb.checked = false;
                    cb._autoDisabled = true;
                    cb.dispatchEvent(new Event('change', { bubbles: true }));
                    cb._autoDisabled = false;
                    try {
                        await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name,
                                flags: { [flagKey]: false }
                            })
                        });
                    } catch (e) {
                        console.warn(`[App] 通知后端关闭${name}失败:`, e);
                    }
                    setFloatingAgentStatus(`${name}已断开`);
                }
            } catch (e) {
                capabilityCheckFailed = true;
                console.warn(`[App] 检查${name}能力失败:`, e);
            }
        }

        // 如果能力检查因网络问题失败，增加失败计数
        if (capabilityCheckFailed) {
            connectionFailureCount++;
        }

        // 【严格节流】定期从后端同步 flags 状态
        // 【修复竞态】将 flag 同步移到能力检查之后，并结合能力检查结果
        const now = Date.now();
        if (now - lastFlagsSyncTime >= FLAGS_SYNC_INTERVAL) {
            lastFlagsSyncTime = now;
            try {
                const resp = await fetch('/api/agent/flags');
                if (resp.ok) {
                    // 连接成功，重置失败计数
                    connectionFailureCount = 0;

                    const data = await resp.json();
                    if (data.success) {
                        const analyzerEnabled = data.analyzer_enabled || false;
                        const flags = data.agent_flags || {};
                        flags.agent_enabled = !!analyzerEnabled;
                        // 处理后端推送的通知（如果有）
                        const notification = data.notification;
                        if (notification) {
                            console.log('[App] 收到后端通知:', notification);
                            // notification 是 JSON 字符串，通过 translateStatusMessage 解析并翻译
                            const translatedNotification = window.translateStatusMessage ? window.translateStatusMessage(notification) : notification;
                            setFloatingAgentStatus(translatedNotification);
                            maybeShowContentFilterModal(notification);
                            // 检查是否包含错误/失败类通知（基于结构化 code 或回退到文本匹配）
                            let isErrorNotification = false;
                            try {
                                const parsed = JSON.parse(notification);
                                if (parsed && parsed.code) {
                                    const errorCodes = ['AGENT_AUTO_DISABLED_COMPUTER', 'AGENT_AUTO_DISABLED_BROWSER', 'AGENT_LLM_CHECK_ERROR', 'AGENT_CU_UNAVAILABLE', 'AGENT_CU_ENABLE_FAILED', 'AGENT_CU_CAPABILITY_LOST'];
                                    isErrorNotification = errorCodes.includes(parsed.code);
                                }
                            } catch (_) {
                                isErrorNotification = notification.includes('失败') || notification.includes('断开') || notification.includes('错误');
                            }
                            if (isErrorNotification) {
                                showStatusToast(translatedNotification, 3000);
                            }
                        }

                        agentStateMachine.updateCache(true, flags);

                        // 如果后端 analyzer 被关闭，同步关闭前端总开关
                        if (!analyzerEnabled && agentMasterCheckbox.checked && !agentMasterCheckbox._processing) {
                            console.log('[App] 后端 analyzer 已关闭，同步关闭前端总开关');
                            agentMasterCheckbox.checked = false;
                            agentMasterCheckbox._autoDisabled = true;
                            agentMasterCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                            agentMasterCheckbox._autoDisabled = false;
                            if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();
                            [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                                if (cb) {
                                    cb.checked = false;
                                    cb.disabled = true;
                                    if (typeof cb._updateStyle === 'function') cb._updateStyle();
                                }
                            });
                            // 如果有特定通知则显示，否则显示默认关闭消息
                            if (!notification) {
                                setFloatingAgentStatus(window.t ? window.t('agent.status.disabled') : 'Agent模式已关闭');
                            }

                            if (!agentStateMachine._popupOpen) {
                                window.stopAgentAvailabilityCheck();
                            }
                            window.stopAgentTaskPolling();
                            return;
                        }

                        // 同步子开关的 checked 状态（如果后端状态与前端不一致且不在处理中）
                        // 【修复竞态】只有当功能实际可用时，才允许根据 flag 自动开启
                        if (agentKeyboardCheckbox && !agentKeyboardCheckbox._processing) {
                            const flagEnabled = flags.computer_use_enabled || false;
                            // 如果未检查(undefined)或可用(true)则允许，但此处已确保检查过
                            // 注意：如果 capabilityCheckFailed 为 true，capabilityResults 可能不完整，保守起见不改变状态
                            const isAvailable = capabilityCheckFailed ? agentKeyboardCheckbox.checked : (capabilityResults['computer_use_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentKeyboardCheckbox.checked !== shouldBeChecked) {
                                // 只在确实需要改变状态时操作
                                if (shouldBeChecked) {
                                    // 开启
                                    agentKeyboardCheckbox.checked = true;
                                    agentKeyboardCheckbox._autoDisabled = true;
                                    agentKeyboardCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentKeyboardCheckbox._autoDisabled = false;
                                    if (typeof agentKeyboardCheckbox._updateStyle === 'function') agentKeyboardCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    // 仅当 flag 明确为 false 时才关闭（flag=true但unavailable的情况已在能力检查循环中处理）
                                    agentKeyboardCheckbox.checked = false;
                                    agentKeyboardCheckbox._autoDisabled = true;
                                    agentKeyboardCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentKeyboardCheckbox._autoDisabled = false;
                                    if (typeof agentKeyboardCheckbox._updateStyle === 'function') agentKeyboardCheckbox._updateStyle();
                                }
                            }
                        }



                        // 浏览器控制 flag 同步
                        if (agentBrowserCheckbox && !agentBrowserCheckbox._processing) {
                            const flagEnabled = flags.browser_use_enabled || false;
                            const isAvailable = capabilityCheckFailed
                                ? agentBrowserCheckbox.checked
                                : (capabilityResults['browser_use_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentBrowserCheckbox.checked !== shouldBeChecked) {
                                if (shouldBeChecked) {
                                    agentBrowserCheckbox.checked = true;
                                    agentBrowserCheckbox._autoDisabled = true;
                                    agentBrowserCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentBrowserCheckbox._autoDisabled = false;
                                    if (typeof agentBrowserCheckbox._updateStyle === 'function') agentBrowserCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    agentBrowserCheckbox.checked = false;
                                    agentBrowserCheckbox._autoDisabled = true;
                                    agentBrowserCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentBrowserCheckbox._autoDisabled = false;
                                    if (typeof agentBrowserCheckbox._updateStyle === 'function') agentBrowserCheckbox._updateStyle();
                                }
                            }
                        }

                        // 用户插件 flag 同步独立处理，避免依赖 MCP 分支
                        if (agentUserPluginCheckbox && !agentUserPluginCheckbox._processing) {
                            const flagEnabled = flags.user_plugin_enabled || false;
                            const isAvailable = capabilityCheckFailed
                                ? agentUserPluginCheckbox.checked
                                : (capabilityResults['user_plugin_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentUserPluginCheckbox.checked !== shouldBeChecked) {
                                if (shouldBeChecked) {
                                    agentUserPluginCheckbox.checked = true;
                                    agentUserPluginCheckbox._autoDisabled = true;
                                    agentUserPluginCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentUserPluginCheckbox._autoDisabled = false;
                                    if (typeof agentUserPluginCheckbox._updateStyle === 'function') agentUserPluginCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    agentUserPluginCheckbox.checked = false;
                                    agentUserPluginCheckbox._autoDisabled = true;
                                    agentUserPluginCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentUserPluginCheckbox._autoDisabled = false;
                                    if (typeof agentUserPluginCheckbox._updateStyle === 'function') agentUserPluginCheckbox._updateStyle();
                                }
                            }
                        }
                    }
                } else {
                    // 响应不OK，视为连接失败
                    throw new Error(`Status ${resp.status}`);
                }
            } catch (e) {
                console.warn('[App] 轮询同步 flags 失败:', e);
                connectionFailureCount++;
            }
        }

        // 如果连续多次连接失败，判定为服务器失联，主动关闭总开关
        if (connectionFailureCount >= 3) {
            console.error('[App] Agent服务器连续连接失败，判定为失联，自动关闭');
            if (agentMasterCheckbox.checked && !agentMasterCheckbox._processing) {
                agentMasterCheckbox.checked = false;
                agentMasterCheckbox._autoDisabled = true;
                agentMasterCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                agentMasterCheckbox._autoDisabled = false;
                if (typeof agentMasterCheckbox._updateStyle === 'function') agentMasterCheckbox._updateStyle();

                [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                    if (cb) {
                        cb.checked = false;
                        cb.disabled = true;
                        if (typeof cb._updateStyle === 'function') cb._updateStyle();
                    }
                });

                setFloatingAgentStatus(window.t ? window.t('agent.status.disconnected') : '服务器连接已断开');
                showStatusToast(window.t ? window.t('agent.status.agentDisconnected') : 'Agent 服务器连接已断开', 3000);

                agentStateMachine.transition(AgentPopupState.OFFLINE, 'connection lost');
                window.stopAgentTaskPolling();

                // 重置计数，避免重复触发
                connectionFailureCount = 0;
            }
        }
    };

    // 启动 Agent 可用性定时检查（由 Agent 总开关打开时调用）
    window.startAgentAvailabilityCheck = function () {
        // 事件驱动：不做轮询，仅做一次性检查。
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
            agentCheckInterval = null;
        }

        // 重置 flags 同步时间，确保立即同步一次
        lastFlagsSyncTime = 0;
        // 重置连接失败计数
        connectionFailureCount = 0;

        // 立即检查一次
        checkAgentCapabilities();
    };

    // 停止 Agent 可用性定时检查（由 Agent 总开关关闭时调用）
    window.stopAgentAvailabilityCheck = function () {
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
            agentCheckInterval = null;
        }
    };

    // 浮动Agent status更新函数
    function setFloatingAgentStatus(msg, taskStatus) {
        ['live2d-agent-status', 'vrm-agent-status'].forEach(id => {
            const statusEl = document.getElementById(id);
            if (statusEl) {
                statusEl.textContent = msg || '';
                // Apply status-specific color for task result notifications
                const colorMap = {
                    completed: '#52c41a',  // green
                    partial: '#faad14',  // amber
                    failed: '#ff4d4f',  // red
                };
                if (taskStatus && colorMap[taskStatus]) {
                    statusEl.style.color = colorMap[taskStatus];
                    // Auto-reset to theme blue after 6 seconds
                    clearTimeout(statusEl._statusResetTimer);
                    statusEl._statusResetTimer = setTimeout(() => {
                        statusEl.style.color = 'var(--neko-popup-accent, #2a7bc4)';
                    }, 6000);
                } else {
                    clearTimeout(statusEl._statusResetTimer);
                    statusEl.style.color = 'var(--neko-popup-accent, #2a7bc4)';
                }
            }
        });
    }

    let _agentQuotaModalOpen = false;
    let _agentQuotaModalCooldownUntil = 0;

    function _isAgentQuotaExceededMessage(text) {
        if (!text) return false;
        const s = String(text).toLowerCase();
        return (
            s.includes('免费 agent 模型今日试用次数已达上限') ||
            s.includes('agent quota exceeded') ||
            (s.includes('agent') && s.includes('上限') && s.includes('试用'))
        );
    }

    function maybeShowAgentQuotaExceededModal(rawMessage) {
        if (!_isAgentQuotaExceededMessage(rawMessage)) return;
        if (typeof window.showAlert !== 'function') return;

        const now = Date.now();
        if (_agentQuotaModalOpen || now < _agentQuotaModalCooldownUntil) return;

        _agentQuotaModalOpen = true;
        _agentQuotaModalCooldownUntil = now + 3000;

        const title = window.t ? window.t('common.alert') : '提示';
        const msg = window.t
            ? window.t('agent.quotaExceeded', { limit: 300 })
            : '免费 Agent 模型今日试用次数已达上限（300次），请明日再试。';

        Promise.resolve(window.showAlert(msg, title))
            .catch(() => { /* ignore */ })
            .finally(() => {
                _agentQuotaModalOpen = false;
            });
    }

    let _contentFilterModalOpen = false;
    let _contentFilterModalCooldownUntil = 0;

    function _isContentFilterError(text) {
        if (!text) return false;
        const s = String(text).toLowerCase();
        return (
            s.includes('content_filter') ||
            s.includes('data_inspection_failed') ||
            s.includes('datainspectionfailed') ||
            s.includes('inappropriate content') ||
            s.includes('content filter') ||
            s.includes('responsible ai policy') ||
            s.includes('content management policy')
        );
    }

    function maybeShowContentFilterModal(rawMessage) {
        if (!_isContentFilterError(rawMessage)) return;
        if (typeof window.showAlert !== 'function') return;

        const now = Date.now();
        if (_contentFilterModalOpen || now < _contentFilterModalCooldownUntil) return;

        _contentFilterModalOpen = true;
        _contentFilterModalCooldownUntil = now + 5000;

        const title = window.t ? window.t('common.alert') : '提示';
        const msg = window.t
            ? window.t('agent.contentFilterError')
            : 'Agent 浏览的网页内容触发了 AI 模型的安全审查过滤，任务已中止。这通常发生在页面包含敏感话题时，请尝试其他关键词或网站。';

        Promise.resolve(window.showAlert(msg, title))
            .catch(() => { /* ignore */ })
            .finally(() => {
                _contentFilterModalOpen = false;
            });
    }

    // 检查Agent服务器健康状态
    async function checkToolServerHealth() {
        // 兼容服务启动竞态：首次失败时做短重试，避免必须手动刷新。
        for (let i = 0; i < 3; i++) {
            try {
                const resp = await fetch(`/api/agent/health`);
                if (resp.ok) return true;
            } catch (e) {
                // continue retry
            }
            if (i < 2) {
                await new Promise(resolve => setTimeout(resolve, 350));
            }
        }
        return false;
    }

    // 检查Agent能力
    async function checkCapability(kind, showError = true) {
        const apis = {
            computer_use: { url: '/api/agent/computer_use/availability', nameKey: 'keyboardControl' },
            browser_use: { url: '/api/agent/browser_use/availability', nameKey: 'browserUse' },
            user_plugin: { url: '/api/agent/user_plugin/availability', nameKey: 'userPlugin' }
        };
        const config = apis[kind];
        if (!config) return false;

        try {
            const r = await fetch(config.url);
            if (!r.ok) return false;
            const j = await r.json();
            if (!j.ready) {
                if (showError) {
                    const name = window.t ? window.t(`settings.toggles.${config.nameKey}`) : config.nameKey;
                    setFloatingAgentStatus(j.reasons?.[0] || (window.t ? window.t('settings.toggles.unavailable', { name }) : `${name}不可用`));
                }
                return false;
            }
            return true;
        } catch (e) {
            return false;
        }
    }

    // 连接Agent弹出框中的开关到Agent控制逻辑
    // 使用事件监听替代固定延迟，确保在浮动按钮创建完成后才绑定事件
    const setupAgentCheckboxListeners = () => {
        // Agent UI v2: fully event-driven single-store controller.
        // Keep legacy logic as fallback only when v2 is unavailable.
        if (typeof window.initAgentUiV2 === 'function') {
            try {
                window.initAgentUiV2();
                return;
            } catch (e) {
                console.warn('[App] initAgentUiV2 failed, fallback to legacy agent UI:', e);
            }
        }

        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentBrowserCheckbox = document.getElementById('live2d-agent-browser');
        const agentUserPluginCheckbox = document.getElementById('live2d-agent-user-plugin');

        if (!agentMasterCheckbox) {
            console.warn('[App] Agent开关元素未找到，跳过绑定');
            return;
        }

        console.log('[App] Agent开关元素已找到，开始绑定事件监听器');

        // 【状态机】操作序列号由状态机管理，子开关保留独立序列号
        let keyboardOperationSeq = 0;
        let browserOperationSeq = 0;
        let userPluginOperationSeq = 0;

        // 标记这些 checkbox 有外部处理器
        agentMasterCheckbox._hasExternalHandler = true;
        if (agentKeyboardCheckbox) agentKeyboardCheckbox._hasExternalHandler = true;
        if (agentBrowserCheckbox) agentBrowserCheckbox._hasExternalHandler = true;
        if (agentUserPluginCheckbox) agentUserPluginCheckbox._hasExternalHandler = true;


        // 辅助函数：同步更新 checkbox 的 UI 样式
        const syncCheckboxUI = (checkbox) => {
            if (checkbox && typeof checkbox._updateStyle === 'function') {
                checkbox._updateStyle();
            }
        };

        const applyAgentStatusSnapshotToUI = (snapshot) => {
            if (!snapshot || agentStateMachine.getState() === AgentPopupState.PROCESSING) return;
            const serverOnline = snapshot.server_online !== false;
            const flags = snapshot.flags || {};
            if (!('agent_enabled' in flags) && snapshot.analyzer_enabled !== undefined) {
                flags.agent_enabled = !!snapshot.analyzer_enabled;
            }
            const analyzerEnabled = !!snapshot.analyzer_enabled;
            const caps = snapshot.capabilities || {};

            agentStateMachine.updateCache(serverOnline, flags);

            if (!serverOnline) {
                agentStateMachine.transition(AgentPopupState.OFFLINE, 'snapshot offline');
                if (agentMasterCheckbox) {
                    agentMasterCheckbox.checked = false;
                    agentMasterCheckbox.disabled = true;
                    syncCheckboxUI(agentMasterCheckbox);
                }
                resetSubCheckboxes();
                setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动');
                return;
            }

            agentStateMachine.transition(AgentPopupState.ONLINE, 'snapshot online');
            if (agentMasterCheckbox) {
                agentMasterCheckbox.disabled = false;
                agentMasterCheckbox.checked = analyzerEnabled;
                agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                syncCheckboxUI(agentMasterCheckbox);
            }

            if (!analyzerEnabled) {
                resetSubCheckboxes();
                setFloatingAgentStatus(window.t ? window.t('agent.status.ready') : 'Agent服务器就绪');
                return;
            }

            const applySub = (cb, enabled, ready, name) => {
                if (!cb) return;
                const hasReady = typeof ready === 'boolean';
                cb.disabled = hasReady ? !ready : false;
                cb.checked = !!enabled && (hasReady ? !!ready : true);
                cb.title = cb.disabled
                    ? (window.t ? window.t('settings.toggles.unavailable', { name }) : `${name}不可用`)
                    : name;
                syncCheckboxUI(cb);
            };

            applySub(
                agentKeyboardCheckbox,
                flags.computer_use_enabled,
                caps.computer_use_ready,
                window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制'
            );

            applySub(
                agentBrowserCheckbox,
                flags.browser_use_enabled,
                caps.browser_use_ready,
                window.t ? window.t('settings.toggles.browserUse') : 'Browser Control'
            );

            applySub(
                agentUserPluginCheckbox,
                flags.user_plugin_enabled,
                caps.user_plugin_ready,
                window.t ? window.t('settings.toggles.userPlugin') : '用户插件'
            );
            setFloatingAgentStatus(window.t ? window.t('agent.status.enabled') : 'Agent模式已开启');
        };
        window.applyAgentStatusSnapshotToUI = applyAgentStatusSnapshotToUI;

        // 辅助函数：重置子开关状态和 UI
        const resetSubCheckboxes = () => {
            const names = {
                'live2d-agent-keyboard': window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制',
                'live2d-agent-browser': window.t ? window.t('settings.toggles.browserUse') : 'Browser Control',
                'live2d-agent-user-plugin': window.t ? window.t('settings.toggles.userPlugin') : '用户插件'
            };
            [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                if (cb) {
                    cb.disabled = true;
                    cb.checked = false;
                    const name = names[cb.id] || '';
                    cb.title = window.t ? window.t('settings.toggles.masterRequired', { name: name }) : `请先开启Agent总开关`;
                    syncCheckboxUI(cb);
                }
            });
        };

        // 初始化时，确保键鼠控制和MCP工具默认禁用（除非Agent总开关已开启）
        if (!agentMasterCheckbox.checked) {
            resetSubCheckboxes();
        }

        // Agent总开关逻辑 - 使用状态机控制
        agentMasterCheckbox.addEventListener('change', async () => {
            // 【状态机控制】开始用户操作
            const currentSeq = agentStateMachine.startOperation();
            const isChecked = agentMasterCheckbox.checked;
            console.log('[App] Agent总开关状态变化:', isChecked, '序列号:', currentSeq);

            // 辅助函数：检查当前操作是否已过期
            const isExpired = () => {
                if (agentStateMachine.isSeqExpired(currentSeq)) {
                    console.log('[App] 总开关操作已过期，序列号:', currentSeq, '当前:', agentStateMachine._operationSeq);
                    return true;
                }
                return false;
            };

            // _processing 标志已在 live2d-ui-popup.js 的点击处理中设置
            if (!agentMasterCheckbox._processing) {
                agentMasterCheckbox._processing = true;
            }

            try {
                if (isChecked) {
                    // 【状态机】保持PROCESSING状态，所有按钮已被禁用
                    setFloatingAgentStatus(window.t ? window.t('agent.status.connecting') : 'Agent服务器连接中...');

                    let healthOk = false;
                    try {
                        healthOk = await checkToolServerHealth();
                        if (!healthOk) throw new Error('tool server down');
                        agentStateMachine.updateCache(true, null);
                    } catch (e) {
                        if (isExpired()) return;
                        agentStateMachine.updateCache(false, null);
                        agentStateMachine.endOperation(false, false);
                        setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动');
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncCheckboxUI(agentMasterCheckbox);
                        return;
                    }

                    if (isExpired()) return;

                    // 查询成功，恢复总开关可交互状态
                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                    syncCheckboxUI(agentMasterCheckbox);
                    setFloatingAgentStatus(window.t ? window.t('agent.status.enabled') : 'Agent模式已开启');

                    // 【状态机】子开关保持禁用，等待能力检查
                    if (agentKeyboardCheckbox) {
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                        syncCheckboxUI(agentKeyboardCheckbox);
                    }

                    if (agentBrowserCheckbox) {
                        agentBrowserCheckbox.disabled = true;
                        agentBrowserCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                        syncCheckboxUI(agentBrowserCheckbox);
                    }

                    if (agentUserPluginCheckbox) {
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                        syncCheckboxUI(agentUserPluginCheckbox);
                    }

                    // 检查键鼠控制和MCP工具的可用性
                    await Promise.all([
                        (async () => {
                            if (!agentKeyboardCheckbox) return;
                            const available = await checkCapability('computer_use', false);
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentKeyboardCheckbox.disabled = true;
                                agentKeyboardCheckbox.checked = false;
                                syncCheckboxUI(agentKeyboardCheckbox);
                                return;
                            }
                            agentKeyboardCheckbox.disabled = !available;
                            agentKeyboardCheckbox.title = available ? (window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.keyboardControl') }) : '键鼠控制不可用');
                            syncCheckboxUI(agentKeyboardCheckbox);
                        })(),

                        (async () => {
                            if (!agentBrowserCheckbox) return;
                            const available = await checkCapability('browser_use', false);
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentBrowserCheckbox.disabled = true;
                                agentBrowserCheckbox.checked = false;
                                syncCheckboxUI(agentBrowserCheckbox);
                                return;
                            }
                            agentBrowserCheckbox.disabled = !available;
                            agentBrowserCheckbox.title = available ? (window.t ? window.t('settings.toggles.browserUse') : 'Browser Control') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.browserUse') }) : 'Browser Control不可用');
                            syncCheckboxUI(agentBrowserCheckbox);
                        })(),

                        (async () => {
                            if (!agentUserPluginCheckbox) return;
                            const available = await checkCapability('user_plugin', false);
                            // 【防竞态】检查操作序列号和总开关状态
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentUserPluginCheckbox.disabled = true;
                                agentUserPluginCheckbox.checked = false;
                                syncCheckboxUI(agentUserPluginCheckbox);
                                return;
                            }
                            agentUserPluginCheckbox.disabled = !available;
                            agentUserPluginCheckbox.title = available ? (window.t ? window.t('settings.toggles.userPlugin') : '用户插件') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.userPlugin') }) : '用户插件不可用');
                            syncCheckboxUI(agentUserPluginCheckbox);
                        })()
                    ]);

                    if (isExpired()) return;

                    try {
                        const r = await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name,
                                flags: { agent_enabled: true, computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false }
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');
                        const flagsResult = await r.json();

                        if (isExpired()) {
                            console.log('[App] flags API 完成后操作已过期');
                            return;
                        }

                        // 启用 analyzer
                        await fetch('/api/agent/admin/control', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ action: 'enable_analyzer' })
                        });

                        if (isExpired() || !agentMasterCheckbox.checked) {
                            console.log('[App] API请求完成后操作已过期或总开关已关闭，不启动轮询');
                            resetSubCheckboxes();
                            return;
                        }

                        // 【状态机】操作成功完成，转换到ONLINE状态
                        agentStateMachine.endOperation(true, true);

                        // 启动定时检查器
                        window.startAgentAvailabilityCheck();
                    } catch (e) {
                        if (isExpired()) return;
                        agentStateMachine.endOperation(false, true);
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncCheckboxUI(agentMasterCheckbox);
                        resetSubCheckboxes();
                        window.stopAgentTaskPolling();
                        setFloatingAgentStatus(window.t ? window.t('agent.status.enableFailed') : '开启失败');
                    }
                } else {
                    // 关闭操作：立即停止相关检查和轮询
                    window.stopAgentAvailabilityCheck();
                    window.stopAgentTaskPolling();
                    resetSubCheckboxes();
                    setFloatingAgentStatus(window.t ? window.t('agent.status.disabled') : 'Agent模式已关闭');
                    syncCheckboxUI(agentMasterCheckbox);

                    // 禁用 analyzer 并停止所有任务
                    try {
                        await fetch('/api/agent/admin/control', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ action: 'disable_analyzer' })
                        });

                        if (isExpired()) {
                            console.log('[App] 关闭操作已过期，跳过后续API调用');
                            return;
                        }

                        await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name,
                                flags: { agent_enabled: false, computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false }
                            })
                        });

                        // 【防竞态】检查操作序列号，防止过期操作覆盖新状态
                        if (isExpired()) {
                            console.log('[App] 关闭flags API完成后操作已过期，跳过状态转换');
                            return;
                        }

                        // 【状态机】关闭操作成功完成
                        agentStateMachine.endOperation(true, true);
                    } catch (e) {
                        if (!isExpired()) {
                            agentStateMachine.endOperation(false, true);
                            setFloatingAgentStatus(window.t ? window.t('agent.status.disabledError') : 'Agent模式已关闭（部分清理失败）');
                        }
                    }
                }
            } finally {
                // 清除处理中标志
                agentMasterCheckbox._processing = false;
            }
        });

        // 子开关通用处理函数（使用闭包捕获对应的序列号变量）
        const setupSubCheckbox = (checkbox, capability, flagKey, nameKey, getSeq, setSeq) => {
            if (!checkbox) return;
            checkbox.addEventListener('change', async () => {
                // 【修复频繁开关竞态】每次操作递增序列号
                const currentSeq = setSeq();
                const isChecked = checkbox.checked;

                // 获取翻译后的名称
                const getName = () => window.t ? window.t(`settings.toggles.${nameKey}`) : nameKey;
                const name = getName();

                // 辅助函数：检查当前操作是否已过期
                const isExpired = () => {
                    if (currentSeq !== getSeq()) {
                        console.log(`[App] ${name}开关操作已过期，序列号:`, currentSeq, '当前:', getSeq());
                        return true;
                    }
                    return false;
                };

                // 如果是自动禁用触发的change事件，跳过处理（避免重复发送请求）
                if (checkbox._autoDisabled) {
                    console.log(`[App] ${name}开关自动关闭，跳过change处理`);
                    return;
                }

                console.log(`[App] ${name}开关状态变化:`, isChecked, '序列号:', currentSeq);
                if (!agentMasterCheckbox?.checked) {
                    checkbox.checked = false;
                    syncCheckboxUI(checkbox);
                    checkbox._processing = false;
                    return;
                }

                // 确保处理中标志存在
                if (!checkbox._processing) {
                    checkbox._processing = true;
                }

                try {
                    const enabled = isChecked;
                    if (enabled) {
                        const ok = await checkCapability(capability);

                        // 【防竞态】检查操作序列号和总开关状态
                        if (isExpired() || !agentMasterCheckbox?.checked) {
                            console.log(`[App] ${name}检查期间操作已过期或总开关已关闭，取消操作`);
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            syncCheckboxUI(checkbox);
                            return;
                        }

                        if (!ok) {
                            setFloatingAgentStatus(window.t ? window.t('settings.toggles.unavailable', { name }) : `${name}不可用`);
                            checkbox.checked = false;
                            syncCheckboxUI(checkbox);
                            return;
                        }
                    }
                    // 注：enabled=true时上面已检查；enabled=false时无await，入口检查已足够

                    try {
                        const r = await fetch('/api/agent/flags', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                lanlan_name: lanlan_config.lanlan_name,
                                flags: { [flagKey]: enabled }
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');

                        // 【防竞态】请求完成后检查操作序列号和总开关状态
                        if (isExpired() || !agentMasterCheckbox?.checked) {
                            console.log(`[App] ${name}请求完成后操作已过期或总开关已关闭，强制关闭`);
                            checkbox.checked = false;
                            checkbox.disabled = true;
                            syncCheckboxUI(checkbox);
                            return;
                        }

                        if (window.t) {
                            setFloatingAgentStatus(enabled ? window.t('settings.toggles.enabled', { name }) : window.t('settings.toggles.disabled', { name }));
                        } else {
                            setFloatingAgentStatus(enabled ? `${name}已开启` : `${name}已关闭`);
                        }
                        // 关闭成功时也需要同步 UI
                        if (!enabled) {
                            syncCheckboxUI(checkbox);
                        }
                    } catch (e) {
                        // 【竞态检查】错误处理前检查操作是否过期
                        if (isExpired()) return;
                        if (enabled) {
                            checkbox.checked = false;
                            syncCheckboxUI(checkbox);
                            setFloatingAgentStatus(window.t ? window.t('settings.toggles.enableFailed', { name }) : `${name}开启失败`);
                        }
                    }
                } finally {
                    // 清除处理中标志
                    checkbox._processing = false;
                    checkbox._processingChangeId = null;
                }
            });
        };

        // 键鼠控制开关逻辑（传入序列号的getter和setter）
        setupSubCheckbox(
            agentKeyboardCheckbox,
            'computer_use',
            'computer_use_enabled',
            'keyboardControl',
            () => keyboardOperationSeq,
            () => ++keyboardOperationSeq
        );

        // 浏览器控制开关逻辑（传入序列号的getter和setter）
        setupSubCheckbox(
            agentBrowserCheckbox,
            'browser_use',
            'browser_use_enabled',
            'browserUse',
            () => browserOperationSeq,
            () => ++browserOperationSeq
        );

        // 用户插件开关逻辑（传入序列号的getter和setter）
        setupSubCheckbox(
            agentUserPluginCheckbox,
            'user_plugin',
            'user_plugin_enabled',
            'userPlugin',
            () => userPluginOperationSeq,
            () => ++userPluginOperationSeq
        );

        // 刷新后若 Agent 总开关已开启，自动打开 Agent 状态弹窗（与开关状态一致）
        function openAgentStatusPopupWhenEnabled() {
            if (agentStateMachine._popupOpen) return;
            const master = document.getElementById('live2d-agent-master');
            if (!master || !master.checked) return;
            const popup = master.closest('[id="live2d-popup-agent"], [id="vrm-popup-agent"]');
            if (!popup) return;
            const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
            if (isVisible) return;
            const manager = popup.id === 'live2d-popup-agent' ? window.live2dManager : window.vrmManager;
            if (!manager || typeof manager.showPopup !== 'function') return;
            manager.showPopup('agent', popup);
        }
        window.openAgentStatusPopupWhenEnabled = openAgentStatusPopupWhenEnabled;

        // 从后端同步 flags 状态到前端开关（完整同步，处理所有情况）
        // 【重要】此函数只同步总开关状态，子开关保持禁用等待能力检查
        async function syncFlagsFromBackend() {
            try {
                const resp = await fetch('/api/agent/flags');
                if (!resp.ok) return false;
                const data = await resp.json();
                if (!data.success) return false;

                const flags = data.agent_flags || {};
                const analyzerEnabled = data.analyzer_enabled || false;
                flags.agent_enabled = !!analyzerEnabled;

                console.log('[App] 从后端获取 flags 状态:', { analyzerEnabled, flags });

                // 缓存后端flags供后续能力检查使用
                agentStateMachine.updateCache(true, flags);

                // 同步总开关状态
                if (agentMasterCheckbox) {
                    // 强制根据后端状态更新前端，确保同步
                    if (agentMasterCheckbox.checked !== analyzerEnabled && !agentMasterCheckbox._processing) {
                        console.log('[App] 强制同步总开关状态:', analyzerEnabled);
                        agentMasterCheckbox.checked = analyzerEnabled;

                        // 如果总开关被动开启，需要触发相关逻辑（如显示HUD）
                        if (analyzerEnabled) {
                            // 只有在非弹窗操作期间才自动启动检查
                            if (!agentStateMachine._popupOpen) {
                                window.startAgentAvailabilityCheck();
                            }
                        } else {
                            // 如果总开关被动关闭，停止所有活动
                            window.stopAgentAvailabilityCheck();
                            window.stopAgentTaskPolling();
                        }
                    }

                    agentMasterCheckbox.disabled = false;
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                    syncCheckboxUI(agentMasterCheckbox);
                }

                // 【修复竞态】子开关不在这里设置 checked 状态
                // 子开关保持禁用和未选中，由 checkAgentCapabilities 根据能力检查结果来设置
                if (agentKeyboardCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，但子开关保持禁用等待能力检查
                        agentKeyboardCheckbox.checked = false;
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                    } else {
                        // Agent 未开启，复位子开关
                        agentKeyboardCheckbox.checked = false;
                        agentKeyboardCheckbox.disabled = true;
                        agentKeyboardCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制' }) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentKeyboardCheckbox);
                }
                // 同步 浏览器控制子开关
                if (agentBrowserCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，但子开关保持禁用等待能力检查
                        agentBrowserCheckbox.checked = false;
                        agentBrowserCheckbox.disabled = true;
                        agentBrowserCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                    } else {
                        agentBrowserCheckbox.checked = false;
                        agentBrowserCheckbox.disabled = true;
                        agentBrowserCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.browserUse') : 'Browser Control' }) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentBrowserCheckbox);
                }
                // 同步 用户插件子开关
                if (agentUserPluginCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，但子开关保持禁用等待能力检查
                        agentUserPluginCheckbox.checked = false;
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                    } else {
                        // Agent 未开启，复位子开关
                        agentUserPluginCheckbox.checked = false;
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.userPlugin') : '用户插件' }) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentUserPluginCheckbox);
                }


                if (analyzerEnabled) {
                    setTimeout(() => openAgentStatusPopupWhenEnabled(), 0);
                }
                return analyzerEnabled;
            } catch (e) {
                console.warn('[App] 同步 flags 状态失败:', e);
                return false;
            }
        }

        // 暴露同步函数供外部调用（如定时轮询）
        window.syncAgentFlagsFromBackend = syncFlagsFromBackend;

        // 监听 Agent 弹窗打开事件 - 使用状态机控制
        window.addEventListener('live2d-agent-popup-opening', async () => {
            // 使用状态机管理弹窗状态
            agentStateMachine.openPopup();
            isAgentPopupOpen = true;

            // 优先使用后端推送快照秒开渲染，避免每次先卡在“连接中”。
            if (window._agentStatusSnapshot) {
                applyAgentStatusSnapshotToUI(window._agentStatusSnapshot);
                setTimeout(() => {
                    if (agentStateMachine._popupOpen) {
                        checkAgentCapabilities();
                    }
                }, 0);
                return;
            }

            // 【状态机控制】如果正在处理用户操作，不进行检查
            if (agentStateMachine.getState() === AgentPopupState.PROCESSING) {
                console.log('[App] 弹窗打开时状态机处于PROCESSING，跳过检查');
                return;
            }

            // 【状态机控制】转换到CHECKING状态，自动禁用所有按钮
            agentStateMachine.transition(AgentPopupState.CHECKING, 'popup opened');

            // 生成本次检查的唯一序列号，防止竞态（如打开->关闭->立即打开）
            const currentCheckSeq = agentStateMachine.nextCheckSeq();

            // 1. 极端策略：强制禁用所有按钮并提示连接中
            if (agentMasterCheckbox) {
                agentMasterCheckbox.disabled = true;
                agentMasterCheckbox.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                syncCheckboxUI(agentMasterCheckbox);
            }
            [agentKeyboardCheckbox, agentBrowserCheckbox, agentUserPluginCheckbox].forEach(cb => {
                if (cb) {
                    cb.disabled = true;
                    cb.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                    syncCheckboxUI(cb);
                }
            });

            // 2. 执行第一次轮询（Gather模式）
            try {
                agentStateMachine.recordCheck();

                // 并行请求所有状态
                const [healthOk, flagsData, keyboardAvailable, browserAvailable, userPluginAvailable] = await Promise.all([
                    checkToolServerHealth(),
                    fetch('/api/agent/flags').then(r => r.ok ? r.json() : { success: false }),
                    checkCapability('computer_use', false),
                    checkCapability('browser_use', false),
                    checkCapability('user_plugin', false)
                ]);

                // 【竞态保护 1】检查序列号是否过期（防止旧请求覆盖新请求）
                if (agentStateMachine.isCheckSeqExpired(currentCheckSeq)) {
                    console.log('[App] 检查请求已过期（可能是快速重新打开），跳过UI更新');
                    return;
                }

                // 【竞态保护 2】检查完成后，验证弹窗仍打开且状态仍是CHECKING
                if (!agentStateMachine._popupOpen || agentStateMachine.getState() !== AgentPopupState.CHECKING) {
                    console.log('[App] 弹窗已关闭或状态已改变，跳过UI更新');
                    return;
                }

                // 3. 统一处理逻辑
                const analyzerEnabled = flagsData.success ? (flagsData.analyzer_enabled || false) : false;
                const flags = flagsData.success ? (flagsData.agent_flags || {}) : {};
                flags.agent_enabled = !!analyzerEnabled;

                // 更新缓存
                agentStateMachine.updateCache(healthOk, flags);

                if (healthOk) {
                    // 服务器在线
                    agentStateMachine.transition(AgentPopupState.ONLINE, 'server online');

                    // 只有总开关开启状态下才允许其他两个开关打开
                    if (analyzerEnabled) {
                        // 总开关开启
                        agentMasterCheckbox.checked = true;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncCheckboxUI(agentMasterCheckbox);

                        // 处理子开关
                        // 键鼠控制
                        if (agentKeyboardCheckbox) {
                            const shouldEnable = flags.computer_use_enabled && keyboardAvailable;
                            agentKeyboardCheckbox.checked = shouldEnable;
                            agentKeyboardCheckbox.disabled = !keyboardAvailable; // 仅当能力不可用时禁用
                            agentKeyboardCheckbox.title = keyboardAvailable ? (window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.keyboardControl') }) : '键鼠控制不可用');
                            syncCheckboxUI(agentKeyboardCheckbox);
                        }

                        // 浏览器控制
                        if (agentBrowserCheckbox) {
                            const shouldEnable = flags.browser_use_enabled && browserAvailable;
                            agentBrowserCheckbox.checked = shouldEnable;
                            agentBrowserCheckbox.disabled = !browserAvailable;
                            agentBrowserCheckbox.title = browserAvailable ? (window.t ? window.t('settings.toggles.browserUse') : 'Browser Control') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.browserUse') }) : 'Browser Control不可用');
                            syncCheckboxUI(agentBrowserCheckbox);
                        }

                        // 用户插件
                        if (agentUserPluginCheckbox) {
                            const shouldEnable = flags.user_plugin_enabled && userPluginAvailable;
                            agentUserPluginCheckbox.checked = shouldEnable;
                            agentUserPluginCheckbox.disabled = !userPluginAvailable;
                            agentUserPluginCheckbox.title = userPluginAvailable ? (window.t ? window.t('settings.toggles.userPlugin') : '用户插件') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.userPlugin') }) : '用户插件不可用');
                            syncCheckboxUI(agentUserPluginCheckbox);
                        }



                        setFloatingAgentStatus(window.t ? window.t('agent.status.enabled') : 'Agent模式已开启');

                        // 只有子开关开启时才显示HUD
                        checkAndToggleTaskHUD();
                    } else {
                        // 总开关关闭
                        agentMasterCheckbox.checked = false;
                        agentMasterCheckbox.disabled = false;
                        agentMasterCheckbox.title = window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关';
                        syncCheckboxUI(agentMasterCheckbox);

                        // 强制关闭所有子开关
                        resetSubCheckboxes();

                        setFloatingAgentStatus(window.t ? window.t('agent.status.ready') : 'Agent服务器就绪');

                        // 确保HUD隐藏
                        window.stopAgentTaskPolling();

                        // 立即通知后台关闭全部flags（如果后端状态不一致）
                        if (flags.computer_use_enabled || flags.browser_use_enabled || flags.user_plugin_enabled) {
                            console.log('[App] 总开关关闭但检测到子flag开启，强制同步关闭');
                            fetch('/api/agent/flags', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    lanlan_name: lanlan_config.lanlan_name,
                                    flags: { agent_enabled: false, computer_use_enabled: false, browser_use_enabled: false, user_plugin_enabled: false }
                                })
                            }).catch(e => console.warn('[App] 强制关闭flags失败:', e));
                        }
                    }

                    // 4. 恢复原异步逻辑
                    window.startAgentAvailabilityCheck();

                } else {
                    // 服务器离线
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'server offline');
                    agentMasterCheckbox.checked = false;
                    agentMasterCheckbox.disabled = true; // 离线时禁用总开关
                    agentMasterCheckbox.title = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                    syncCheckboxUI(agentMasterCheckbox);

                    resetSubCheckboxes();

                    setFloatingAgentStatus(window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动');

                    // 离线也需要轮询（检查服务器何时上线）
                    window.startAgentAvailabilityCheck();
                }

            } catch (e) {
                console.error('[App] Agent 初始检查失败:', e);
                agentStateMachine.updateCache(false, null);

                if (agentStateMachine._popupOpen) {
                    agentStateMachine.transition(AgentPopupState.OFFLINE, 'check failed');
                    agentMasterCheckbox.checked = false;
                    resetSubCheckboxes();
                    window.startAgentAvailabilityCheck();
                }
            } finally {
                agentStateMachine.releaseCheckLock();
            }
        });

        // 监听 Agent 弹窗关闭事件 - 使用状态机控制
        window.addEventListener('live2d-agent-popup-closed', () => {
            isAgentPopupOpen = false;
            agentStateMachine.closePopup();
            console.log('[App] Agent弹窗已关闭');

            // 如果总开关未开启，停止轮询
            if (!agentMasterCheckbox || !agentMasterCheckbox.checked) {
                window.stopAgentAvailabilityCheck();
            }
        });

        console.log('[App] Agent开关事件监听器绑定完成');
    };

    // Agent 任务 HUD 轮询逻辑
    let agentTaskPollingInterval = null;
    let agentTaskTimeUpdateInterval = null;

    // 启动任务状态轮询
    window.startAgentTaskPolling = function () {
        console.trace('[App] startAgentTaskPolling');
        // Always attempt to show HUD
        if (window.AgentHUD && window.AgentHUD.createAgentTaskHUD) {
            window.AgentHUD.createAgentTaskHUD();
            window.AgentHUD.showAgentTaskHUD();
        }

        if (agentTaskPollingInterval) return;

        console.log('[App] 启动 Agent 任务状态轮询');

        agentTaskPollingInterval = true;

        // 每秒更新运行时间显示
        agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
    };

    // 停止任务状态轮询
    window.stopAgentTaskPolling = function () {
        console.log('[App] 停止 Agent 任务状态轮询');
        console.trace('[App] stopAgentTaskPolling caller trace');

        if (agentTaskPollingInterval) {
            if (typeof agentTaskPollingInterval !== 'boolean') {
                clearInterval(agentTaskPollingInterval);
            }
            agentTaskPollingInterval = null;
        }

        if (agentTaskTimeUpdateInterval) {
            clearInterval(agentTaskTimeUpdateInterval);
            agentTaskTimeUpdateInterval = null;
        }

        // 隐藏 HUD
        if (window.AgentHUD && window.AgentHUD.hideAgentTaskHUD) {
            window.AgentHUD.hideAgentTaskHUD();
        }
    };

    // 推送架构中任务状态由 WebSocket 事件驱动

    // 更新运行中任务的时间显示
    function updateTaskRunningTimes() {
        const taskList = document.getElementById('agent-task-list');
        if (!taskList) {
            // DOM gone (HUD might be rebuilding) — just skip this update, don't stop the interval
            return;
        }

        // Auto-stop when no running/queued tasks remain
        const hasRunning = window._agentTaskMap && Array.from(window._agentTaskMap.values())
            .some(t => t.status === 'running' || t.status === 'queued');
        if (!hasRunning) {
            if (agentTaskTimeUpdateInterval) {
                clearInterval(agentTaskTimeUpdateInterval);
                agentTaskTimeUpdateInterval = null;
            }
            return;
        }

        const timeElements = taskList.querySelectorAll('[id^="task-time-"]');
        timeElements.forEach(timeEl => {
            const taskId = timeEl.id.replace('task-time-', '');
            const card = document.querySelector(`.task-card[data-task-id="${taskId}"]`);
            if (!card) return;

            // 从原始 start_time 重新计算（存储在 data 属性中）
            const startTimeStr = card.dataset.startTime;
            if (startTimeStr) {
                const startTime = new Date(startTimeStr);
                const elapsed = Math.floor((Date.now() - startTime.getTime()) / 1000);
                const minutes = Math.floor(elapsed / 60);
                const seconds = elapsed % 60;
                timeEl.innerHTML = `<span style="color: #64748b;">⏱️</span> ${minutes}:${seconds.toString().padStart(2, '0')}`;
            }
        });
    }

    function checkAndToggleTaskHUD() {
        const getEl = (ids) => {
            for (let id of ids) {
                const el = document.getElementById(id);
                if (el) return el;
            }
            return null;
        };

        const masterCheckbox = getEl(['live2d-agent-master', 'vrm-agent-master']);
        const keyboardCheckbox = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);
        const browserCheckbox = getEl(['live2d-agent-browser', 'vrm-agent-browser']);
        const userPlugin = getEl(['live2d-agent-user-plugin', 'vrm-agent-user-plugin']);

        // Extract DOM states
        const domMaster = masterCheckbox ? masterCheckbox.checked : false;
        const domChild = (keyboardCheckbox && keyboardCheckbox.checked)
            || (browserCheckbox && browserCheckbox.checked)
            || (userPlugin && userPlugin.checked);

        // Extract backend/cached state
        const snap = window._agentStatusSnapshot;
        const machineFlags = window.agentStateMachine ? window.agentStateMachine._cachedFlags : null;

        // We prefer snapshot flags if they exist and are populated, else fallback to machine cached flags
        const flags = (snap && snap.flags && Object.keys(snap.flags).length > 0) ? snap.flags : machineFlags;

        // Extract optimistic state from agent_ui_v2 if available
        let optMaster = undefined;
        let optChild = undefined;
        if (window.agent_ui_v2_state && window.agent_ui_v2_state.optimistic) {
            const opt = window.agent_ui_v2_state.optimistic;
            if ('agent_enabled' in opt) optMaster = !!opt.agent_enabled;
            if ('computer_use_enabled' in opt || 'browser_use_enabled' in opt || 'user_plugin_enabled' in opt) {
                optChild = !!opt.computer_use_enabled || !!opt.browser_use_enabled || !!opt.user_plugin_enabled;
            }
        }

        let isMasterOn = false;
        let isChildOn = false;

        // Is the UI fully interactive? If masterCheckbox is missing or disabled, it usually means we are loading/syncing
        const isUiInteractive = masterCheckbox && !masterCheckbox.disabled;

        if (!isUiInteractive) {
            // UI is loading, trust optimistic state first, then backend flags
            isMasterOn = optMaster !== undefined ? optMaster : (flags && !!flags.agent_enabled);
            isChildOn = optChild !== undefined ? optChild : (flags && !!(flags.computer_use_enabled || flags.browser_use_enabled || flags.user_plugin_enabled));
        } else {
            // UI is interactive. We strictly trust the explicit DOM state, plus any optimistic overrides.
            isMasterOn = optMaster !== undefined ? optMaster : domMaster;
            isChildOn = optChild !== undefined ? optChild : domChild;
        }

        if (isMasterOn && isChildOn) {
            console.log('[DEBUG HUD] Starting polling. Master:', isMasterOn, 'Child:', isChildOn, 'DOM:', domMaster, domChild, 'Flag:', flags?.agent_enabled, 'Opt:', optMaster, optChild);
            window.startAgentTaskPolling();
        } else {
            // Don't hide HUD if there are still active tasks being tracked via WebSocket
            // Include tasks in linger window (10s after completion)
            const LINGER_MS = 10000;
            const now = Date.now();
            const hasActiveTasks = window._agentTaskMap && window._agentTaskMap.size > 0 &&
                Array.from(window._agentTaskMap.values()).some(t => {
                    if (t.status === 'running' || t.status === 'queued') return true;
                    // Check linger window for terminal tasks
                    const isTerminal = t.status === 'completed' || t.status === 'failed' || t.status === 'cancelled';
                    return isTerminal && t.terminal_at && (now - t.terminal_at < LINGER_MS);
                });
            if (hasActiveTasks) {
                console.log('[DEBUG HUD] Flags off but active tasks exist, keeping HUD visible. Master:', isMasterOn, 'Child:', isChildOn);
            } else {
                console.log('[DEBUG HUD] Stopping polling. Master:', isMasterOn, 'Child:', isChildOn, 'DOM:', domMaster, domChild, 'Flag:', flags?.agent_enabled, 'Opt:', optMaster, optChild);
                window.stopAgentTaskPolling();
            }
        }
    }


    // 暴露给其他模块使用
    window.checkAndToggleTaskHUD = checkAndToggleTaskHUD;

    // 监听 Agent 子开关变化来控制 HUD 显示
    window.addEventListener('live2d-floating-buttons-ready', () => {
        // 等待 agent_ui_v2 初始化或者直接靠 DOM
        const bindHUD = () => {
            const getEl = (ids) => {
                for (let id of ids) {
                    const el = document.getElementById(id);
                    if (el) return el;
                }
                return null;
            };

            const keyboardCheckbox = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);
            const browserCheckbox = getEl(['live2d-agent-browser', 'vrm-agent-browser']);
            const userPluginCheckbox = getEl(['live2d-agent-user-plugin', 'vrm-agent-user-plugin']);

            if (!keyboardCheckbox || !browserCheckbox) {
                // 如果还不存在，稍后再试（应对动态创建的情况，比如 VRM 模式下的懒加载 popup）
                setTimeout(bindHUD, 500);
                return;
            }

            keyboardCheckbox.removeEventListener('change', checkAndToggleTaskHUD);
            keyboardCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            browserCheckbox.removeEventListener('change', checkAndToggleTaskHUD);
            browserCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            if (userPluginCheckbox) {
                userPluginCheckbox.removeEventListener('change', checkAndToggleTaskHUD);
                userPluginCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }

            checkAndToggleTaskHUD();
            console.log('[App] Agent 任务 HUD 控制已绑定');
        };

        // 由于不同模型(Live2D/VRM)构建 popup DOM 的时机不同，这里采用递归轮询直到元素出现为止
        setTimeout(bindHUD, 100);
    });
    // Agent 任务 HUD 轮询逻辑结束

    // 监听浮动按钮创建完成事件
    window.addEventListener('live2d-floating-buttons-ready', () => {
        console.log('[App] 收到浮动按钮就绪事件，开始绑定Agent开关');
        setupAgentCheckboxListeners();
        // Agent 已开启时刷新页面后自动打开状态弹窗（等 V2/legacy 恢复开关状态后再试）
        setTimeout(() => {
            if (typeof window.openAgentStatusPopupWhenEnabled === 'function') {
                window.openAgentStatusPopupWhenEnabled();
            }
        }, 400);
    }, { once: true });  // 只执行一次

    // 麦克风权限和设备列表预加载（修复 UI 2.0 中权限请求时机导致的bug）
    let micPermissionGranted = false;
    let cachedMicDevices = null;

    // 预先请求麦克风权限并缓存设备列表
    async function ensureMicrophonePermission() {
        if (micPermissionGranted && cachedMicDevices) {
            return cachedMicDevices;
        }

        try {
            // 方法1：先请求一次短暂的麦克风访问来触发权限请求
            // 这样后续 enumerateDevices() 才能返回带 label 的设备信息
            const tempStream = await navigator.mediaDevices.getUserMedia({
                audio: true
            });

            // 立即释放流，我们只是为了触发权限
            tempStream.getTracks().forEach(track => track.stop());

            micPermissionGranted = true;
            console.log('麦克风权限已获取');

            // 现在可以获取完整的设备列表（带 label）
            const devices = await navigator.mediaDevices.enumerateDevices();
            cachedMicDevices = devices.filter(device => device.kind === 'audioinput');

            return cachedMicDevices;
        } catch (error) {
            console.warn('请求麦克风权限失败:', error);
            // 即使权限失败，也尝试获取设备列表（可能没有 label）
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                cachedMicDevices = devices.filter(device => device.kind === 'audioinput');
                return cachedMicDevices;
            } catch (enumError) {
                console.error('获取设备列表失败:', enumError);
                return [];
            }
        }
    }

    // 监听设备变化，更新缓存
    if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
        navigator.mediaDevices.addEventListener('devicechange', async () => {
            console.log('检测到设备变化，刷新麦克风列表...');
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                cachedMicDevices = devices.filter(device => device.kind === 'audioinput');
                // 如果弹出框当前是显示的，刷新它
                const micPopup = document.getElementById('live2d-popup-mic');
                if (micPopup && micPopup.style.display === 'flex') {
                    await window.renderFloatingMicList();
                }
            } catch (error) {
                console.error('设备变化后更新列表失败:', error);
            }
        });
    }

    // 为浮动弹出框渲染麦克风列表（修复版本：确保有权限后再渲染）
    window.renderFloatingMicList = async (popupArg) => {
        const micPopup = popupArg || document.getElementById('live2d-popup-mic');
        if (!micPopup) {
            return false;
        }

        try {
            // 确保已经有麦克风权限，并获取设备列表
            const audioInputs = await ensureMicrophonePermission();

            micPopup.innerHTML = '';

            if (audioInputs.length === 0) {
                const noMicItem = document.createElement('div');
                noMicItem.textContent = window.t ? window.t('microphone.noDevices') : '没有检测到麦克风设备';
                noMicItem.style.padding = '8px 12px';
                noMicItem.style.color = 'var(--neko-popup-text-sub)';
                noMicItem.style.fontSize = '13px';
                micPopup.appendChild(noMicItem);
                return false;
            }

            // ===== 双栏布局容器 =====
            const leftColumn = document.createElement('div');
            Object.assign(leftColumn.style, {
                flex: '1',
                minWidth: '180px',
                display: 'flex',
                flexDirection: 'column',
                overflowY: 'auto'
            });

            const rightColumn = document.createElement('div');
            Object.assign(rightColumn.style, {
                flex: '1',
                minWidth: '160px',
                display: 'flex',
                flexDirection: 'column',
                overflowY: 'auto'
            });

            // ========== 左栏 1. 扬声器音量控制 ==========
            const speakerContainer = document.createElement('div');
            speakerContainer.className = 'speaker-volume-container';
            Object.assign(speakerContainer.style, {
                padding: '8px 12px'
            });

            // 扬声器音量标签和当前值显示
            const speakerHeader = document.createElement('div');
            Object.assign(speakerHeader.style, {
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '8px'
            });

            const speakerLabel = document.createElement('span');
            speakerLabel.textContent = window.t ? window.t('speaker.volumeLabel') : '扬声器音量';
            speakerLabel.setAttribute('data-i18n', 'speaker.volumeLabel');
            speakerLabel.style.fontSize = '13px';
            speakerLabel.style.color = 'var(--neko-popup-text)';
            speakerLabel.style.fontWeight = '500';

            const speakerValue = document.createElement('span');
            speakerValue.id = 'speaker-volume-value';
            speakerValue.textContent = `${speakerVolume}%`;
            speakerValue.style.fontSize = '12px';
            speakerValue.style.color = '#4f8cff';
            speakerValue.style.fontWeight = '500';

            speakerHeader.appendChild(speakerLabel);
            speakerHeader.appendChild(speakerValue);
            speakerContainer.appendChild(speakerHeader);

            // 扬声器音量滑块
            const speakerSlider = document.createElement('input');
            speakerSlider.type = 'range';
            speakerSlider.id = 'speaker-volume-slider';
            speakerSlider.min = '0';
            speakerSlider.max = '100';
            speakerSlider.step = '1';
            speakerSlider.value = String(speakerVolume);
            Object.assign(speakerSlider.style, {
                width: '100%',
                height: '6px',
                borderRadius: '3px',
                cursor: 'pointer',
                accentColor: '#4f8cff'
            });

            // 滑块事件：实时更新音量
            speakerSlider.addEventListener('input', (e) => {
                const newVol = parseInt(e.target.value, 10);
                speakerVolume = newVol;
                speakerValue.textContent = `${newVol}%`;

                // 实时更新扬声器增益节点
                if (speakerGainNode) {
                    speakerGainNode.gain.setTargetAtTime(newVol / 100, speakerGainNode.context.currentTime, 0.05);
                }
            });

            // 滑块松开时保存设置
            speakerSlider.addEventListener('change', () => {
                saveSpeakerVolumeSetting();
            });

            speakerContainer.appendChild(speakerSlider);

            // 扬声器音量提示文字
            const speakerHint = document.createElement('div');
            speakerHint.textContent = window.t ? window.t('speaker.volumeHint') : '调节AI语音的播放音量';
            speakerHint.setAttribute('data-i18n', 'speaker.volumeHint');
            Object.assign(speakerHint.style, {
                fontSize: '11px',
                color: 'var(--neko-popup-text-sub)',
                marginTop: '6px'
            });
            speakerContainer.appendChild(speakerHint);

            leftColumn.appendChild(speakerContainer);

            // 添加分隔线
            const speakerSeparator = document.createElement('div');
            speakerSeparator.style.height = '1px';
            speakerSeparator.style.backgroundColor = 'var(--neko-popup-separator)';
            speakerSeparator.style.margin = '8px 0';
            leftColumn.appendChild(speakerSeparator);

            // ========== 左栏 2. 麦克风增益控制 ==========
            const gainContainer = document.createElement('div');
            gainContainer.className = 'mic-gain-container';
            Object.assign(gainContainer.style, {
                padding: '8px 12px'
            });

            // 增益标签和当前值显示
            const gainHeader = document.createElement('div');
            Object.assign(gainHeader.style, {
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '8px'
            });

            const gainLabel = document.createElement('span');
            gainLabel.textContent = window.t ? window.t('microphone.gainLabel') : '麦克风增益';
            gainLabel.style.fontSize = '13px';
            gainLabel.style.color = 'var(--neko-popup-text)';
            gainLabel.style.fontWeight = '500';

            const gainValue = document.createElement('span');
            gainValue.id = 'mic-gain-value';
            gainValue.textContent = formatGainDisplay(microphoneGainDb);
            gainValue.style.fontSize = '12px';
            gainValue.style.color = '#4f8cff';
            gainValue.style.fontWeight = '500';

            gainHeader.appendChild(gainLabel);
            gainHeader.appendChild(gainValue);
            gainContainer.appendChild(gainHeader);

            // 增益滑块（使用分贝单位）
            const gainSlider = document.createElement('input');
            gainSlider.type = 'range';
            gainSlider.id = 'mic-gain-slider';
            gainSlider.min = String(MIN_MIC_GAIN_DB);
            gainSlider.max = String(MAX_MIC_GAIN_DB);
            gainSlider.step = '1';  // 1dB 步进
            gainSlider.value = String(microphoneGainDb);
            Object.assign(gainSlider.style, {
                width: '100%',
                height: '6px',
                borderRadius: '3px',
                cursor: 'pointer',
                accentColor: '#4f8cff'
            });

            // 滑块事件：实时更新增益
            gainSlider.addEventListener('input', (e) => {
                const newGainDb = parseFloat(e.target.value);
                microphoneGainDb = newGainDb;
                gainValue.textContent = formatGainDisplay(newGainDb);

                // 实时更新 GainNode（如果正在录音）
                if (micGainNode) {
                    micGainNode.gain.value = dbToLinear(newGainDb);
                    console.log(`麦克风增益已实时更新: ${newGainDb}dB`);
                }
            });

            // 滑块松开时保存设置
            gainSlider.addEventListener('change', () => {
                saveMicGainSetting();
            });

            gainContainer.appendChild(gainSlider);

            // 增益提示文字
            const gainHint = document.createElement('div');
            gainHint.textContent = window.t ? window.t('microphone.gainHint') : '如果麦克风声音太小，可以调高增益';
            Object.assign(gainHint.style, {
                fontSize: '11px',
                color: 'var(--neko-popup-text-sub)',
                marginTop: '6px'
            });
            gainContainer.appendChild(gainHint);

            leftColumn.appendChild(gainContainer);

            // 添加分隔线（音量可视化区域前）
            const volumeSeparator = document.createElement('div');
            volumeSeparator.style.height = '1px';
            volumeSeparator.style.backgroundColor = 'var(--neko-popup-separator)';
            volumeSeparator.style.margin = '8px 0';
            leftColumn.appendChild(volumeSeparator);

            // ========== 左栏 3. 麦克风音量可视化区域 ==========
            const volumeContainer = document.createElement('div');
            volumeContainer.className = 'mic-volume-container';
            Object.assign(volumeContainer.style, {
                padding: '8px 12px'
            });

            // 音量标签
            const volumeLabel = document.createElement('div');
            Object.assign(volumeLabel.style, {
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '8px'
            });

            const volumeLabelText = document.createElement('span');
            volumeLabelText.textContent = window.t ? window.t('microphone.volumeLabel') : '实时麦克风音量';
            volumeLabelText.style.fontSize = '13px';
            volumeLabelText.style.color = 'var(--neko-popup-text)';
            volumeLabelText.style.fontWeight = '500';

            const volumeStatus = document.createElement('span');
            volumeStatus.id = 'mic-volume-status';
            volumeStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
            volumeStatus.style.fontSize = '11px';
            volumeStatus.style.color = 'var(--neko-popup-text-sub)';

            volumeLabel.appendChild(volumeLabelText);
            volumeLabel.appendChild(volumeStatus);
            volumeContainer.appendChild(volumeLabel);

            // 音量条背景
            const volumeBarBg = document.createElement('div');
            volumeBarBg.id = 'mic-volume-bar-bg';
            Object.assign(volumeBarBg.style, {
                width: '100%',
                height: '8px',
                backgroundColor: 'var(--neko-mic-volume-bg, #e9ecef)',
                borderRadius: '4px',
                overflow: 'hidden',
                position: 'relative'
            });

            // 音量条填充
            const volumeBarFill = document.createElement('div');
            volumeBarFill.id = 'mic-volume-bar-fill';
            Object.assign(volumeBarFill.style, {
                width: '0%',
                height: '100%',
                backgroundColor: '#4f8cff',
                borderRadius: '4px',
                transition: 'width 0.05s ease-out, background-color 0.1s ease'
            });

            volumeBarBg.appendChild(volumeBarFill);
            volumeContainer.appendChild(volumeBarBg);

            // 音量提示（录音时会显示）
            const volumeHint = document.createElement('div');
            volumeHint.id = 'mic-volume-hint';
            volumeHint.textContent = window.t ? window.t('microphone.volumeHint') : '开始录音后可查看音量';
            Object.assign(volumeHint.style, {
                fontSize: '11px',
                color: 'var(--neko-popup-text-sub)',
                marginTop: '6px'
            });
            volumeContainer.appendChild(volumeHint);

            leftColumn.appendChild(volumeContainer);

            // ========== 右栏：麦克风设备选择列表 ==========
            // 标题
            const deviceTitle = document.createElement('div');
            Object.assign(deviceTitle.style, {
                padding: '8px 12px 6px',
                fontSize: '13px',
                fontWeight: '600',
                color: '#4f8cff',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                borderBottom: '1px solid var(--neko-popup-separator)',
                marginBottom: '4px'
            });
            const deviceTitleIcon = document.createElement('span');
            deviceTitleIcon.textContent = '🎙️';
            deviceTitleIcon.style.fontSize = '14px';
            const deviceTitleText = document.createElement('span');
            deviceTitleText.textContent = window.t ? window.t('microphone.deviceTitle') : '选择麦克风设备';
            deviceTitleText.setAttribute('data-i18n', 'microphone.deviceTitle');
            deviceTitle.appendChild(deviceTitleIcon);
            deviceTitle.appendChild(deviceTitleText);
            rightColumn.appendChild(deviceTitle);

            // 添加默认麦克风选项
            const defaultOption = document.createElement('button');
            defaultOption.className = 'mic-option';
            // 不设置 dataset.deviceId，让它保持 undefined（表示默认）
            defaultOption.textContent = window.t ? window.t('microphone.defaultDevice') : '系统默认麦克风';
            if (selectedMicrophoneId === null) {
                defaultOption.classList.add('selected');
            }
            Object.assign(defaultOption.style, {
                padding: '8px 12px',
                cursor: 'pointer',
                border: 'none',
                background: selectedMicrophoneId === null ? 'var(--neko-popup-selected-bg)' : 'transparent',
                borderRadius: '6px',
                transition: 'background 0.2s ease',
                fontSize: '13px',
                width: '100%',
                textAlign: 'left',
                color: selectedMicrophoneId === null ? '#4f8cff' : 'var(--neko-popup-text)',
                fontWeight: selectedMicrophoneId === null ? '500' : '400'
            });
            defaultOption.addEventListener('mouseenter', () => {
                if (selectedMicrophoneId !== null) {
                    defaultOption.style.background = 'var(--neko-popup-hover)';
                }
            });
            defaultOption.addEventListener('mouseleave', () => {
                if (selectedMicrophoneId !== null) {
                    defaultOption.style.background = 'transparent';
                }
            });
            defaultOption.addEventListener('click', async () => {
                await selectMicrophone(null);
                // 只更新选中状态，不重新渲染整个列表
                updateMicListSelection();
            });
            rightColumn.appendChild(defaultOption);

            // 添加分隔线
            const separator = document.createElement('div');
            separator.style.height = '1px';
            separator.style.backgroundColor = 'var(--neko-popup-separator)';
            separator.style.margin = '5px 0';
            rightColumn.appendChild(separator);

            // 添加各个麦克风设备选项
            audioInputs.forEach(device => {
                const option = document.createElement('button');
                option.className = 'mic-option';
                option.dataset.deviceId = device.deviceId; // 存储设备ID用于更新选中状态
                const micIndex = audioInputs.indexOf(device) + 1;
                option.textContent = device.label || (window.t ? window.t('microphone.deviceLabel', { index: micIndex }) : `麦克风 ${micIndex}`);
                if (selectedMicrophoneId === device.deviceId) {
                    option.classList.add('selected');
                }

                Object.assign(option.style, {
                    padding: '8px 12px',
                    cursor: 'pointer',
                    border: 'none',
                    background: selectedMicrophoneId === device.deviceId ? 'var(--neko-popup-selected-bg)' : 'transparent',
                    borderRadius: '6px',
                    transition: 'background 0.2s ease',
                    fontSize: '13px',
                    width: '100%',
                    textAlign: 'left',
                    color: selectedMicrophoneId === device.deviceId ? '#4f8cff' : 'var(--neko-popup-text)',
                    fontWeight: selectedMicrophoneId === device.deviceId ? '500' : '400'
                });

                option.addEventListener('mouseenter', () => {
                    if (selectedMicrophoneId !== device.deviceId) {
                        option.style.background = 'var(--neko-popup-hover)';
                    }
                });
                option.addEventListener('mouseleave', () => {
                    if (selectedMicrophoneId !== device.deviceId) {
                        option.style.background = 'transparent';
                    }
                });

                option.addEventListener('click', async () => {
                    await selectMicrophone(device.deviceId);
                    // 只更新选中状态，不重新渲染整个列表
                    updateMicListSelection();
                });

                rightColumn.appendChild(option);
            });

            // ===== 组装双栏布局 =====
            micPopup.appendChild(leftColumn);

            // 垂直分隔线
            const verticalDivider = document.createElement('div');
            Object.assign(verticalDivider.style, {
                width: '1px',
                backgroundColor: 'var(--neko-popup-separator)',
                alignSelf: 'stretch',
                margin: '8px 0'
            });
            micPopup.appendChild(verticalDivider);
            micPopup.appendChild(rightColumn);

            // 启动音量可视化更新
            startMicVolumeVisualization();

            return true;
        } catch (error) {
            console.error('渲染麦克风列表失败:', error);
            micPopup.innerHTML = '';
            const errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('microphone.loadFailed') : '获取麦克风列表失败';
            errorItem.style.padding = '8px 12px';
            errorItem.style.color = '#dc3545';
            errorItem.style.fontSize = '13px';
            micPopup.appendChild(errorItem);
            return false;
        }
    };

    // 轻量级更新：仅更新麦克风列表的选中状态（不重新渲染整个列表）
    function updateMicListSelection() {
        const micPopup = document.getElementById('live2d-popup-mic') || document.getElementById('vrm-popup-mic');
        if (!micPopup) return;

        // 更新所有选项的选中状态
        const options = micPopup.querySelectorAll('.mic-option');
        options.forEach(option => {
            const deviceId = option.dataset.deviceId;
            const isSelected = (deviceId === undefined && selectedMicrophoneId === null) ||
                (deviceId === selectedMicrophoneId);

            if (isSelected) {
                option.classList.add('selected');
                option.style.background = 'var(--neko-popup-selected-bg)';
                option.style.color = '#4f8cff';
                option.style.fontWeight = '500';
            } else {
                option.classList.remove('selected');
                option.style.background = 'transparent';
                option.style.color = 'var(--neko-popup-text)';
                option.style.fontWeight = '400';
            }
        });
    }

    // 页面加载后预先请求麦克风权限（修复核心bug：确保权限在用户点击前就已获取）
    setTimeout(async () => {
        console.log('[麦克风] 页面加载，预先请求麦克风权限...');
        try {
            await ensureMicrophonePermission();
            console.log('[麦克风] 权限预请求完成，设备列表已缓存');
            // 触发事件通知权限已准备好（兼容可能依赖此事件的其他代码）
            window.dispatchEvent(new CustomEvent('mic-permission-ready'));
        } catch (error) {
            console.warn('[麦克风] 预请求权限失败（用户可能拒绝）:', error);
        }
    }, 500); // 页面加载后半秒开始预请求

    // 延迟渲染麦克风列表到弹出框（确保弹出框DOM已创建）
    setTimeout(() => {
        window.renderFloatingMicList();
    }, 1500);

    // 屏幕源选择功能（仅Electron环境）
    // 当前选中的屏幕源ID（从 localStorage 恢复）
    let selectedScreenSourceId = (() => {
        try {
            const saved = localStorage.getItem('selectedScreenSourceId');
            return saved || null;
        } catch (e) {
            return null;
        }
    })();

    // 选择屏幕源
    async function selectScreenSource(sourceId, sourceName) {
        selectedScreenSourceId = sourceId;

        // 持久化到 localStorage
        try {
            if (sourceId) {
                localStorage.setItem('selectedScreenSourceId', sourceId);
            } else {
                localStorage.removeItem('selectedScreenSourceId');
            }
        } catch (e) {
            console.warn('[屏幕源] 无法保存到 localStorage:', e);
        }

        // 更新UI选中状态
        updateScreenSourceListSelection();

        // 显示选择提示
        showStatusToast(window.t ? window.t('app.screenSource.selected', { source: sourceName }) : `已选择 ${sourceName}`, 3000);

        console.log('[屏幕源] 已选择:', sourceName, '(ID:', sourceId, ')');

        // 智能刷新：如果当前正在屏幕分享中，自动重启以应用新的屏幕源
        // 检查屏幕分享状态：stopButton 可用表示正在分享
        const stopBtn = document.getElementById('stopButton');
        const isScreenSharingActive = stopBtn && !stopBtn.disabled;

        if (isScreenSharingActive && window.switchScreenSharing) {
            console.log('[屏幕源] 检测到正在屏幕分享中，将自动重启以应用新源');
            // 先停止当前分享
            await stopScreenSharing();
            // 等待一小段时间
            await new Promise(resolve => setTimeout(resolve, 300));
            // 重新开始分享（使用新选择的源）
            await startScreenSharing();
        }
    }

    // 暴露给window对象，供VRM使用
    window.selectScreenSource = selectScreenSource;

    // 更新屏幕源列表的选中状态
    function updateScreenSourceListSelection() {
        const screenPopup = document.getElementById('live2d-popup-screen');
        if (!screenPopup) return;

        const options = screenPopup.querySelectorAll('.screen-source-option');
        options.forEach(option => {
            const sourceId = option.dataset.sourceId;
            const isSelected = sourceId === selectedScreenSourceId;

            if (isSelected) {
                option.classList.add('selected');
                option.style.background = 'var(--neko-popup-selected-bg)';
                option.style.borderColor = '#4f8cff';
            } else {
                option.classList.remove('selected');
                option.style.background = 'transparent';
                option.style.borderColor = 'transparent';
            }
        });
    }

    // 为浮动弹出框渲染屏幕源列表（仅Electron环境）
    window.renderFloatingScreenSourceList = async () => {
        const screenPopup = document.getElementById('live2d-popup-screen');
        if (!screenPopup) {
            console.warn('[屏幕源] 弹出框不存在');
            return false;
        }

        // 检查是否在Electron环境
        if (!window.electronDesktopCapturer || !window.electronDesktopCapturer.getSources) {
            screenPopup.innerHTML = '';
            const notAvailableItem = document.createElement('div');
            notAvailableItem.textContent = window.t ? window.t('app.screenSource.notAvailable') : '仅在桌面版可用';
            notAvailableItem.style.padding = '12px';
            notAvailableItem.style.color = 'var(--neko-popup-text-sub)';
            notAvailableItem.style.fontSize = '13px';
            notAvailableItem.style.textAlign = 'center';
            screenPopup.appendChild(notAvailableItem);
            return false;
        }

        try {
            // 显示加载中
            screenPopup.innerHTML = '';
            const loadingItem = document.createElement('div');
            loadingItem.textContent = window.t ? window.t('app.screenSource.loading') : '加载中...';
            loadingItem.style.padding = '12px';
            loadingItem.style.color = 'var(--neko-popup-text-sub)';
            loadingItem.style.fontSize = '13px';
            loadingItem.style.textAlign = 'center';
            screenPopup.appendChild(loadingItem);

            // 获取屏幕源
            const sources = await window.electronDesktopCapturer.getSources({
                types: ['window', 'screen'],
                thumbnailSize: { width: 160, height: 100 }
            });

            screenPopup.innerHTML = '';

            if (!sources || sources.length === 0) {
                const noSourcesItem = document.createElement('div');
                noSourcesItem.textContent = window.t ? window.t('app.screenSource.noSources') : '没有可用的屏幕源';
                noSourcesItem.style.padding = '12px';
                noSourcesItem.style.color = 'var(--neko-popup-text-sub)';
                noSourcesItem.style.fontSize = '13px';
                noSourcesItem.style.textAlign = 'center';
                screenPopup.appendChild(noSourcesItem);
                return false;
            }

            // 分组：屏幕和窗口
            const screens = sources.filter(s => s.id.startsWith('screen:'));
            const windows = sources.filter(s => s.id.startsWith('window:'));

            // 创建网格容器的辅助函数
            function createGridContainer() {
                const grid = document.createElement('div');
                Object.assign(grid.style, {
                    display: 'grid',
                    gridTemplateColumns: 'repeat(4, 1fr)',
                    gap: '6px',
                    padding: '4px',
                    width: '100%',
                    boxSizing: 'border-box'
                });
                return grid;
            }

            // 创建屏幕源选项元素（网格样式：垂直布局，名字在下）
            function createSourceOption(source) {
                const option = document.createElement('div');
                option.className = 'screen-source-option';
                option.dataset.sourceId = source.id;
                Object.assign(option.style, {
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    padding: '4px',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    border: '2px solid transparent',
                    transition: 'all 0.2s ease',
                    background: 'transparent',
                    boxSizing: 'border-box',
                    minWidth: '0'  // 允许收缩
                });

                if (selectedScreenSourceId === source.id) {
                    option.classList.add('selected');
                    option.style.background = 'var(--neko-popup-selected-bg)';
                    option.style.borderColor = '#4f8cff';
                }

                // 缩略图（带异常处理和占位图回退）
                if (source.thumbnail) {
                    const thumb = document.createElement('img');
                    let thumbnailDataUrl = '';
                    try {
                        // NativeImage 对象需要转换为 dataURL 字符串
                        if (typeof source.thumbnail === 'string') {
                            thumbnailDataUrl = source.thumbnail;
                        } else if (source.thumbnail && typeof source.thumbnail.toDataURL === 'function') {
                            thumbnailDataUrl = source.thumbnail.toDataURL();
                        }
                        // 检查是否为空字符串或无效值
                        if (!thumbnailDataUrl || thumbnailDataUrl.trim() === '') {
                            throw new Error('thumbnail.toDataURL() 返回空值');
                        }
                    } catch (e) {
                        console.warn('[屏幕源] 缩略图转换失败，使用占位图:', e);
                        // 使用占位图（1x1 透明像素的 dataURL）
                        thumbnailDataUrl = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
                    }
                    thumb.src = thumbnailDataUrl;
                    // 添加错误处理，如果图片加载失败也使用占位图
                    thumb.onerror = () => {
                        thumb.src = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
                    };
                    Object.assign(thumb.style, {
                        width: '100%',
                        maxWidth: '90px',
                        height: '56px',
                        objectFit: 'cover',
                        borderRadius: '4px',
                        border: '1px solid var(--neko-popup-separator)',
                        marginBottom: '4px'
                    });
                    option.appendChild(thumb);
                } else {
                    // 无缩略图时显示图标
                    const iconPlaceholder = document.createElement('div');
                    iconPlaceholder.textContent = source.id.startsWith('screen:') ? '🖥️' : '🪟';
                    Object.assign(iconPlaceholder.style, {
                        width: '100%',
                        maxWidth: '90px',
                        height: '56px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '24px',
                        background: 'var(--neko-screen-placeholder-bg, #f5f5f5)',
                        borderRadius: '4px',
                        marginBottom: '4px'
                    });
                    option.appendChild(iconPlaceholder);
                }

                // 名称（在缩略图下方，允许多行）
                const label = document.createElement('span');
                label.textContent = source.name;
                Object.assign(label.style, {
                    fontSize: '10px',
                    color: 'var(--neko-popup-text)',
                    width: '100%',
                    textAlign: 'center',
                    lineHeight: '1.3',
                    wordBreak: 'break-word',
                    display: '-webkit-box',
                    WebkitLineClamp: '2',
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    height: '26px'
                });
                option.appendChild(label);

                option.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    await selectScreenSource(source.id, source.name);
                });

                option.addEventListener('mouseenter', () => {
                    if (!option.classList.contains('selected')) {
                        option.style.background = 'var(--neko-popup-hover)';
                    }
                });
                option.addEventListener('mouseleave', () => {
                    if (!option.classList.contains('selected')) {
                        option.style.background = 'transparent';
                    }
                });

                return option;
            }

            // 添加屏幕列表（网格布局）
            if (screens.length > 0) {
                const screenLabel = document.createElement('div');
                screenLabel.textContent = window.t ? window.t('app.screenSource.screens') : '屏幕';
                Object.assign(screenLabel.style, {
                    padding: '4px 8px',
                    fontSize: '11px',
                    color: 'var(--neko-popup-text-sub)',
                    fontWeight: '600',
                    textTransform: 'uppercase'
                });
                screenPopup.appendChild(screenLabel);

                const screenGrid = createGridContainer();
                screens.forEach(source => {
                    screenGrid.appendChild(createSourceOption(source));
                });
                screenPopup.appendChild(screenGrid);
            }

            // 添加窗口列表（网格布局）
            if (windows.length > 0) {
                const windowLabel = document.createElement('div');
                windowLabel.textContent = window.t ? window.t('app.screenSource.windows') : '窗口';
                Object.assign(windowLabel.style, {
                    padding: '4px 8px',
                    fontSize: '11px',
                    color: 'var(--neko-popup-text-sub)',
                    fontWeight: '600',
                    textTransform: 'uppercase',
                    marginTop: '8px'
                });
                screenPopup.appendChild(windowLabel);

                const windowGrid = createGridContainer();
                windows.forEach(source => {
                    windowGrid.appendChild(createSourceOption(source));
                });
                screenPopup.appendChild(windowGrid);
            }

            return true;
        } catch (error) {
            console.error('[屏幕源] 获取屏幕源失败:', error);
            screenPopup.innerHTML = '';
            const errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('app.screenSource.loadFailed') : '获取屏幕源失败';
            errorItem.style.padding = '12px';
            errorItem.style.color = '#dc3545';
            errorItem.style.fontSize = '13px';
            errorItem.style.textAlign = 'center';
            screenPopup.appendChild(errorItem);
            return false;
        }
    };

    // 暴露选中的屏幕源ID给其他模块使用
    window.getSelectedScreenSourceId = () => selectedScreenSourceId;

    // 同步 proactive 相关的全局变量到模块作用域
    function syncProactiveFlags() {
        proactiveChatEnabled = typeof window.proactiveChatEnabled !== 'undefined' ? window.proactiveChatEnabled : proactiveChatEnabled;
        proactiveVisionEnabled = typeof window.proactiveVisionEnabled !== 'undefined' ? window.proactiveVisionEnabled : proactiveVisionEnabled;
        proactiveVisionChatEnabled = typeof window.proactiveVisionChatEnabled !== 'undefined' ? window.proactiveVisionChatEnabled : proactiveVisionChatEnabled;
        proactiveNewsChatEnabled = typeof window.proactiveNewsChatEnabled !== 'undefined' ? window.proactiveNewsChatEnabled : proactiveNewsChatEnabled;
        proactiveVideoChatEnabled = typeof window.proactiveVideoChatEnabled !== 'undefined' ? window.proactiveVideoChatEnabled : proactiveVideoChatEnabled;
        proactivePersonalChatEnabled = typeof window.proactivePersonalChatEnabled !== 'undefined' ? window.proactivePersonalChatEnabled : proactivePersonalChatEnabled;
        proactiveMusicEnabled = typeof window.proactiveMusicEnabled !== 'undefined' ? window.proactiveMusicEnabled : proactiveMusicEnabled;
    }

    // 检查是否有任何搭话方式被选中
    function hasAnyChatModeEnabled() {
        syncProactiveFlags();
        return proactiveVisionChatEnabled || proactiveNewsChatEnabled || proactiveVideoChatEnabled || proactivePersonalChatEnabled || proactiveMusicEnabled;
    }

    // 检查主动搭话前置条件是否满足
    function canTriggerProactively() {
        syncProactiveFlags();

        // 必须开启主动搭话
        if (!proactiveChatEnabled) {
            return false;
        }

        // 必须选择至少一种搭话方式
        if (!proactiveVisionChatEnabled && !proactiveNewsChatEnabled && !proactiveVideoChatEnabled && !proactivePersonalChatEnabled && !proactiveMusicEnabled) {
            return false;
        }

        // 如果只选择了视觉搭话，需要同时开启自主视觉
        if (proactiveVisionChatEnabled && !proactiveNewsChatEnabled && !proactiveVideoChatEnabled && !proactivePersonalChatEnabled && !proactiveMusicEnabled) {
            return proactiveVisionEnabled;
        }

        // 如果只选择了个人动态搭话，需要同时开启个人动态
        if (!proactiveVisionChatEnabled && !proactiveNewsChatEnabled && !proactiveVideoChatEnabled && proactivePersonalChatEnabled && !proactiveMusicEnabled) {
            return proactivePersonalChatEnabled;
        }

        // 音乐搭话不需要额外条件，总是允许
        return true;
    }

    // 主动搭话定时触发功能
    function scheduleProactiveChat() {
        syncProactiveFlags();

        // 清除现有定时器
        if (proactiveChatTimer) {
            clearTimeout(proactiveChatTimer);
            proactiveChatTimer = null;
        }

        // 必须开启主动搭话且选择至少一种搭话方式才启动调度
        if (!proactiveChatEnabled || !hasAnyChatModeEnabled()) {
            proactiveChatBackoffLevel = 0;
            return;
        }

        // 前置条件检查：如果不满足触发条件，不启动调度器并重置退避
        if (!canTriggerProactively()) {
            console.log('主动搭话前置条件不满足，不启动调度器');
            proactiveChatBackoffLevel = 0;
            return;
        }

        // 如果主动搭话正在执行中，不安排新的定时器（等当前执行完成后自动安排）
        if (isProactiveChatRunning) {
            console.log('主动搭话正在执行中，延迟安排下一次');
            return;
        }

        // 只在非语音模式下执行（语音模式下不触发主动搭话）
        // 文本模式或待机模式都可以触发主动搭话
        if (isRecording) {
            console.log('语音模式中，不安排主动搭话');
            return;
        }

        // 计算延迟时间（指数退避，倍率2.5）
        const delay = (proactiveChatInterval * 1000) * Math.pow(2.5, proactiveChatBackoffLevel);
        console.log(`主动搭话：${delay / 1000}秒后触发（基础间隔：${proactiveChatInterval}秒，退避级别：${proactiveChatBackoffLevel}）`);

        proactiveChatTimer = setTimeout(async () => {
            // 双重检查锁：定时器触发时再次检查是否正在执行
            if (isProactiveChatRunning) {
                console.log('主动搭话定时器触发时发现正在执行中，跳过本次');
                return;
            }

            console.log('触发主动搭话...');
            isProactiveChatRunning = true; // 加锁

            try {
                await triggerProactiveChat();
            } finally {
                isProactiveChatRunning = false; // 解锁
            }

            // 增加退避级别（最多到约7分钟，即level 3：30s * 2.5^3 = 7.5min）
            if (proactiveChatBackoffLevel < 3) {
                proactiveChatBackoffLevel++;
            }

            // 安排下一次
            scheduleProactiveChat();
        }, delay);
    }

    // 获取个人媒体cookies所有可用平台的函数
    async function getAvailablePersonalPlatforms() {
        try {
            const response = await fetch('/api/auth/cookies/status');
            if (!response.ok) return [];

            const result = await response.json();
            let availablePlatforms = [];

            if (result.success && result.data) {
                for (const [platform, info] of Object.entries(result.data)) {
                    if (platform !== 'platforms' && info.has_cookies) {
                        availablePlatforms.push(platform);
                    }
                }
            }
            return availablePlatforms;
        } catch (error) {
            console.error('获取可用平台列表失败:', error);
            return [];
        }
    }

    async function triggerProactiveChat() {
        try {
            syncProactiveFlags();

            let availableModes = [];
            // 收集所有启用的搭话方式
            // 视觉搭话：需要同时开启主动搭话和自主视觉
            // 同时触发 vision 和 window 模式
            if (proactiveVisionChatEnabled && proactiveChatEnabled && proactiveVisionEnabled) {
                availableModes.push('vision');
                availableModes.push('window');
            }

            // 新闻搭话：使用微博热议话题
            if (proactiveNewsChatEnabled && proactiveChatEnabled) {
                availableModes.push('news');
            }

            // 视频搭话：使用B站首页视频
            if (proactiveVideoChatEnabled && proactiveChatEnabled) {
                availableModes.push('video');
            }

            // 个人动态搭话：使用B站和微博个人动态
            if (proactivePersonalChatEnabled && proactiveChatEnabled) {
                // 检查是否有可用的 Cookie 凭证
                const platforms = await getAvailablePersonalPlatforms();
                if (platforms.length > 0) {
                    availableModes.push('personal');
                    console.log(`[个人动态] 模式已启用，平台: ${platforms.join(', ')}`);
                } else {
                    // 如果开关开了但没登录，不把 personal 发给后端，避免后端抓取失败报错
                    console.warn('[个人动态] 开关已开启但未检测到登录凭证，已忽略此模式');
                }
            }

            // 音乐搭话
            console.log(`[ProactiveChat] 检查音乐模式: proactiveMusicEnabled=${proactiveMusicEnabled}, proactiveChatEnabled=${proactiveChatEnabled}`);
            if (proactiveMusicEnabled && proactiveChatEnabled) {
                console.log('[ProactiveChat] 音乐模式已启用');
                availableModes.push('music');
            }

            // 如果没有选择任何搭话方式，跳过本次搭话
            if (availableModes.length === 0) {
                console.log('未选择任何搭话方式，跳过本次搭话');
                return;
            }

            console.log(`主动搭话：启用模式 [${availableModes.join(', ')}]，将并行获取所有信息源`);

            let requestBody = {
                lanlan_name: lanlan_config.lanlan_name,
                enabled_modes: availableModes
            };

            // 如果包含 vision 模式，需要在前端获取截图和窗口标题
            if (availableModes.includes('vision') || availableModes.includes('window')) {
                const fetchTasks = [];
                let screenshotIndex = -1;
                let windowTitleIndex = -1;

                if (availableModes.includes('vision')) {
                    screenshotIndex = fetchTasks.length;
                    fetchTasks.push(captureProactiveChatScreenshot());
                }

                if (availableModes.includes('window')) {
                    windowTitleIndex = fetchTasks.length;
                    fetchTasks.push(fetch('/api/get_window_title')
                        .then(r => r.json())
                        .catch(() => ({ success: false })));
                }

                const results = await Promise.all(fetchTasks);

                // await 期间检查状态
                if (!canTriggerProactively()) {
                    console.log('功能已关闭或前置条件不满足，取消本次搭话');
                    return;
                }

                // await 期间用户可能切换模式，重新同步并过滤可用模式
                syncProactiveFlags();
                const latestModes = [];
                if (proactiveVisionChatEnabled && proactiveChatEnabled && proactiveVisionEnabled) {
                    latestModes.push('vision', 'window');
                }
                if (proactiveNewsChatEnabled && proactiveChatEnabled) {
                    latestModes.push('news');
                }
                if (proactiveVideoChatEnabled && proactiveChatEnabled) {
                    latestModes.push('video');
                }
                // 个人动态搭话：需要同时开启个人动态
                if (proactivePersonalChatEnabled && proactiveChatEnabled) {
                    latestModes.push('personal');
                }
                // 音乐搭话
                if (proactiveMusicEnabled && proactiveChatEnabled) {
                    latestModes.push('music');
                }
                availableModes = availableModes.filter(m => latestModes.includes(m));
                requestBody.enabled_modes = availableModes;
                if (availableModes.length === 0) {
                    console.log('await后无可用模式，取消本次搭话');
                    return;
                }

                if (screenshotIndex !== -1 && availableModes.includes('vision')) {
                    const screenshotDataUrl = results[screenshotIndex];
                    if (screenshotDataUrl) {
                        requestBody.screenshot_data = screenshotDataUrl;
                        if (window.unlockAchievement) {
                            window.unlockAchievement('ACH_SEND_IMAGE').catch(err => {
                                console.error('解锁发送图片成就失败:', err);
                            });
                        }
                    } else {
                        // 截图失败，从 enabled_modes 中移除 vision
                        console.log('截图失败，移除 vision 模式');
                        availableModes = availableModes.filter(m => m !== 'vision');
                        requestBody.enabled_modes = availableModes;
                    }
                }

                if (windowTitleIndex !== -1 && availableModes.includes('window')) {
                    const windowTitleResult = results[windowTitleIndex];
                    if (windowTitleResult && windowTitleResult.success && windowTitleResult.window_title) {
                        requestBody.window_title = windowTitleResult.window_title;
                        console.log('视觉搭话附加窗口标题:', windowTitleResult.window_title);
                    } else {
                        // 窗口标题获取失败，从 enabled_modes 中移除 window
                        console.log('窗口标题获取失败，移除 window 模式');
                        availableModes = availableModes.filter(m => m !== 'window');
                        requestBody.enabled_modes = availableModes;
                    }
                }

                if (availableModes.length === 0) {
                    console.log('所有附加模式均失败，移除后无其他可用模式，跳过本次搭话');
                    return;
                }
            }

            // 发送请求前最终检查：确保功能状态未在 await 期间改变
            if (!canTriggerProactively()) {
                console.log('发送请求前检查失败，取消本次搭话');
                return;
            }

            // 检测用户是否在20秒内有过输入，有过输入则作废本次主动搭话
            const timeSinceLastInput = Date.now() - lastUserInputTime;
            if (timeSinceLastInput < 20000) {
                console.log(`主动搭话作废：用户在${Math.round(timeSinceLastInput / 1000)}秒前有过输入`);
                return;
            }

            const response = await fetch('/api/proactive_chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            const result = await response.json();

            if (result.success) {
                if (result.action === 'chat') {
                    console.log('主动搭话已发送:', result.message, result.source_mode ? `(来源: ${result.source_mode})` : '');

                    // 【核心修复】解除链接与音乐的互斥逻辑。
                    // 无论 source_mode 是什么，只要有链接就尝试显示（除了纯音乐模式下可能重复显示的链接）。
                    // 在 BOTH 模式下（后端现已归类为 music），我们需要既展示 Web 链接又播放音乐喵！
                    if (result.source_links && result.source_links.length > 0) {
                        setTimeout(() => {
                            _showProactiveChatSourceLinks(result.source_links);
                        }, 3000);
                    }

                    // 如果模式包含音乐信号，尝试播放第一条音轨
                    if ((result.source_mode === 'music' || result.source_mode === 'both') && result.source_links && result.source_links.length > 0) {
                        // 优先寻找有 artist 字段或标记为音乐推荐的真实音轨
                        const musicLink = result.source_links.find(link => link.artist || link.source === '音乐推荐') || result.source_links[0];
                        console.log('[ProactiveChat] 收到音乐链接:', musicLink);
                        if (musicLink.url) {
                            const track = {
                                name: musicLink.title || '未知曲目',
                                artist: musicLink.artist || '未知艺术家',
                                url: musicLink.url
                            };
                            console.log('[ProactiveChat] 发送音乐消息:', track);
                            window.dispatchMusicPlay(track);
                        } else {
                            console.warn('[ProactiveChat] 音乐链接缺少URL:', musicLink);
                        }
                    }

                    // 后端会直接通过session发送消息和TTS，前端无需处理显示
                } else if (result.action === 'pass') {
                    console.log('AI选择不搭话');
                }
            } else {
                console.warn('主动搭话失败:', result.error);
            }
        } catch (error) {
            console.error('主动搭话触发失败:', error);
        }
    }

    /**
     * 在聊天区域临时显示来源链接卡片（旁路，不进入 AI 记忆）
     */
    function _showProactiveChatSourceLinks(links) {
        try {
            const chatContent = document.getElementById('chat-content-wrapper');
            if (!chatContent) return;

            const validLinks = [];
            for (const link of links) {
                let safeUrl = null;
                try {
                    const u = new URL(String(link.url || ''), window.location.origin);
                    if (u.protocol === 'http:' || u.protocol === 'https:') {
                        safeUrl = u.href;
                    }
                } catch (e) {
                    console.warn('解析链接失败:', e);
                }
                if (safeUrl) {
                    validLinks.push({ ...link, safeUrl });
                }
            }

            if (validLinks.length === 0) return;

            // 超过 3 个旧卡片时，移除最早的
            const MAX_LINK_CARDS = 3;
            const existingCards = chatContent.querySelectorAll('.proactive-source-link-card');
            const overflow = existingCards.length - MAX_LINK_CARDS + 1;
            if (overflow > 0) {
                for (let i = 0; i < overflow; i++) {
                    existingCards[i].remove();
                }
            }

            const linkCard = document.createElement('div');
            linkCard.className = 'proactive-source-link-card';
            linkCard.style.cssText = `
                margin: 6px 12px;
                padding: 8px 14px;
                background: var(--bg-secondary, rgba(255,255,255,0.08));
                border-left: 3px solid var(--accent-color, #6c8cff);
                border-radius: 8px;
                font-size: 12px;
                opacity: 0;
                transition: opacity 0.4s ease;
                max-width: 320px;
                position: relative;
            `;

            const closeBtn = document.createElement('span');
            closeBtn.textContent = '✕';
            closeBtn.style.cssText = `
                position: absolute;
                top: 6px;
                right: 6px;
                cursor: pointer;
                color: var(--text-secondary, rgba(200,200,200,0.8));
                font-size: 14px;
                font-weight: bold;
                line-height: 1;
                width: 20px;
                height: 20px;
                display: flex;
                align-items: center;
                justify-content: center;
                border-radius: 50%;
                background: rgba(255,255,255,0.08);
                transition: color 0.2s, background 0.2s;
                z-index: 1;
            `;
            closeBtn.addEventListener('mouseenter', () => {
                closeBtn.style.color = '#fff';
                closeBtn.style.background = 'rgba(255,255,255,0.2)';
            });
            closeBtn.addEventListener('mouseleave', () => {
                closeBtn.style.color = 'var(--text-secondary, rgba(200,200,200,0.8))';
                closeBtn.style.background = 'rgba(255,255,255,0.08)';
            });
            closeBtn.addEventListener('click', () => {
                linkCard.style.opacity = '0';
                setTimeout(() => { linkCard.remove(); }, 300);
            });
            linkCard.appendChild(closeBtn);

            for (const link of validLinks) {
                const a = document.createElement('a');
                a.href = link.safeUrl;
                a.textContent = `🔗 ${link.source ? `[${link.source}] ` : ''}${link.title || link.url}`;
                a.style.cssText = `
                    display: block;
                    color: var(--accent-color, #6c8cff);
                    text-decoration: none;
                    padding: 3px 0;
                    padding-right: 20px;
                    word-break: break-all;
                    font-size: 12px;
                    cursor: pointer;
                `;
                a.addEventListener('mouseenter', () => { a.style.textDecoration = 'underline'; });
                a.addEventListener('mouseleave', () => { a.style.textDecoration = 'none'; });
                a.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (window.electronShell && window.electronShell.openExternal) {
                        window.electronShell.openExternal(link.safeUrl);
                    } else {
                        window.open(link.safeUrl, '_blank', 'noopener,noreferrer');
                    }
                });
                linkCard.appendChild(a);
            }

            chatContent.appendChild(linkCard);
            chatContent.scrollTop = chatContent.scrollHeight;

            requestAnimationFrame(() => { linkCard.style.opacity = '1'; });

            setTimeout(() => {
                linkCard.style.opacity = '0';
                setTimeout(() => { linkCard.remove(); }, 500);
            }, 5 * 60 * 1000);

            console.log('已显示主动搭话来源链接:', validLinks.length, '条');
        } catch (e) {
            console.warn('显示来源链接失败:', e);
        }
    }

    function resetProactiveChatBackoff() {
        // 重置退避级别
        proactiveChatBackoffLevel = 0;
        // 重新安排定时器
        scheduleProactiveChat();
    }

    // 发送单帧屏幕数据（优先已存在的 screenCaptureStream → captureProactiveChatScreenshot → 后端兜底）
    async function sendOneProactiveVisionFrame() {
        try {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;

            let dataUrl = null;
            let usedCachedStream = false;

            if (screenCaptureStream) {
                screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();
                usedCachedStream = true;

                // 先检查 tracks 是否还活着
                const videoTracks = screenCaptureStream.getVideoTracks();
                const hasLiveTrack = videoTracks.length > 0 && videoTracks.some(t => t.readyState === 'live');

                if (!hasLiveTrack) {
                    console.warn('[ProactiveVision] 缓存流的 tracks 已结束，废弃该流');
                    screenCaptureStream = null;
                    screenCaptureStreamLastUsed = null;
                    usedCachedStream = false;
                } else {
                    const video = document.createElement('video');
                    video.srcObject = screenCaptureStream;
                    video.autoplay = true;
                    video.muted = true;
                    try {
                        await video.play();
                    } catch (e) {
                        // 某些情况下不需要 play() 成功也能读取帧
                    }
                    // 等待视频元数据/首帧就绪，避免 play() 后立即读帧时尺寸为 0×0
                    if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
                        await new Promise(resolve => {
                            video.addEventListener('loadeddata', resolve, { once: true });
                        });
                    }
                    const frame = captureCanvasFrame(video, 0.8);
                    dataUrl = frame && frame.dataUrl ? frame.dataUrl : null;
                    video.srcObject = null;
                    video.remove();

                    // 如果流 active 但提取帧失败（0×0），说明流是空壳，主动废弃
                    if (!dataUrl) {
                        console.warn('[ProactiveVision] 缓存流提取帧失败（可能是刷新后的空壳流），废弃该流');
                        try {
                            screenCaptureStream.getTracks().forEach(t => { try { t.stop(); } catch (e) { } });
                        } catch (e) { }
                        screenCaptureStream = null;
                        screenCaptureStreamLastUsed = null;
                        usedCachedStream = false;
                    }
                }
            }

            // 如果缓存流提取帧失败，或无缓存流
            if (!dataUrl) {
                if (isWindowsOS()) {
                    // Windows 不需要用户手势即可调用 getDisplayMedia，可以走完整的截图流程
                    dataUrl = await captureProactiveChatScreenshot();
                } else {
                    // macOS/Linux: 定时器（非用户手势）上下文中调用 getDisplayMedia 会反复弹窗
                    // 仅走后端 pyautogui 截图兜底；403 表示缺少屏幕录制权限，给用户一次性提示
                    const { dataUrl: backendDataUrl, status: backendStatus } = await fetchBackendScreenshot();
                    if (backendStatus === 403 && !screenRecordingPermissionHintShown) {
                        screenRecordingPermissionHintShown = true;
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast('⚠️ 屏幕录制权限未授权，请在系统设置中允许屏幕录制', 6000);
                        }
                        console.warn('[ProactiveVision] 后端截图返回 403，请在"系统设置 → 隐私与安全性 → 屏幕录制"中授权 N.E.K.O');
                    }
                    dataUrl = backendDataUrl;
                }
            }

            if (dataUrl && socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'stream_data',
                    data: dataUrl,
                    input_type: isMobile() ? 'camera' : 'screen'
                }));
                console.log('[ProactiveVision] 发送单帧屏幕数据');

                // 再次刷新最后使用时间，防止在发送过程中被误释放
                if (usedCachedStream && screenCaptureStream) {
                    screenCaptureStreamLastUsed = Date.now();
                }
            }
        } catch (e) {
            console.error('sendOneProactiveVisionFrame 失败:', e);
        }
    }

    function startProactiveVisionDuringSpeech() {
        // 如果已有定时器先清理
        if (proactiveVisionFrameTimer) {
            clearInterval(proactiveVisionFrameTimer);
            proactiveVisionFrameTimer = null;
        }

        // 仅在条件满足时启动：已开启主动视觉 && 正在录音 && 未手动屏幕共享
        if (!proactiveVisionEnabled || !isRecording) return;
        if (screenButton && screenButton.classList.contains('active')) return; // 手动共享时不启动

        proactiveVisionFrameTimer = setInterval(async () => {
            // 在每次执行前再做一次检查，避免竞态
            if (!proactiveVisionEnabled || !isRecording) {
                stopProactiveVisionDuringSpeech();
                return;
            }

            // 如果手动开启了屏幕共享，重置计数器（即跳过发送）
            if (screenButton && screenButton.classList.contains('active')) {
                // do nothing this tick, just wait for next interval
                return;
            }

            await sendOneProactiveVisionFrame();
        }, proactiveVisionInterval * 1000);
    }

    function stopProactiveVisionDuringSpeech() {
        if (proactiveVisionFrameTimer) {
            clearInterval(proactiveVisionFrameTimer);
            proactiveVisionFrameTimer = null;
        }
    }

    function stopProactiveChatSchedule() {
        if (proactiveChatTimer) {
            clearTimeout(proactiveChatTimer);
            proactiveChatTimer = null;
        }
    }

    /**
     * 安全的Windows系统检测函数
     * 优先使用 navigator.userAgentData，然后 fallback 到 navigator.userAgent，最后才用已弃用的 navigator.platform
     * @returns {boolean} 是否为Windows系统
     */
    function isWindowsOS() {
        try {
            // 优先使用现代 API（如果支持）
            if (navigator.userAgentData && navigator.userAgentData.platform) {
                const platform = navigator.userAgentData.platform.toLowerCase();
                return platform.includes('win');
            }

            // Fallback 到 userAgent 字符串检测
            if (navigator.userAgent) {
                const ua = navigator.userAgent.toLowerCase();
                return ua.includes('win');
            }

            // 最后的兼容方案：使用已弃用的 platform API
            if (navigator.platform) {
                const platform = navigator.platform.toLowerCase();
                return platform.includes('win');
            }

            // 如果所有方法都不可用，默认返回false
            return false;
        } catch (error) {
            console.error('Windows检测失败:', error);
            return false;
        }
    }

    // 主动搭话截图函数（优先后端 pyautogui 静默截图 → 前端 getDisplayMedia 缓存流复用）
    async function captureProactiveChatScreenshot() {
        // 策略1: 后端 pyautogui 优先（本地运行时完全静默，无弹窗）
        const { dataUrl: backendDataUrl } = await fetchBackendScreenshot();
        if (backendDataUrl) {
            console.log('[主动搭话截图] 后端截图成功');
            return backendDataUrl;
        }

        // 策略2: 前端 getDisplayMedia（远程服务器等后端不可用时的备选）
        // 复用缓存的 screenCaptureStream，仅在无有效流时才请求新流
        // 注意：如果之前从非用户手势上下文调用 getDisplayMedia 失败过，不再重试（防止刷新后反复弹窗）
        if (!screenCaptureAutoPromptFailed && navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
            try {
                let captureStream = screenCaptureStream;

                // 检查缓存流的 tracks 是否还活着
                if (captureStream && captureStream.active) {
                    const videoTracks = captureStream.getVideoTracks();
                    const hasLiveTrack = videoTracks.length > 0 && videoTracks.some(t => t.readyState === 'live');
                    if (!hasLiveTrack) {
                        console.warn('[主动搭话截图] 缓存流 tracks 已结束，废弃');
                        captureStream = null;
                        screenCaptureStream = null;
                        screenCaptureStreamLastUsed = null;
                    }
                }

                if (!captureStream || !captureStream.active) {
                    captureStream = await navigator.mediaDevices.getDisplayMedia({
                        video: { cursor: 'always', frameRate: { max: 1 } },
                        audio: false,
                    });

                    screenCaptureStream = captureStream;

                    captureStream.getVideoTracks().forEach(track => {
                        track.addEventListener('ended', () => {
                            console.log('[ProactiveVision] 屏幕共享流被用户终止');
                            if (screenCaptureStream === captureStream) {
                                screenCaptureStream = null;
                                screenCaptureStreamLastUsed = null;
                                if (screenCaptureStreamIdleTimer) {
                                    clearTimeout(screenCaptureStreamIdleTimer);
                                    screenCaptureStreamIdleTimer = null;
                                }
                            }
                        });
                    });
                }

                screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();

                const video = document.createElement('video');
                video.srcObject = captureStream;
                video.autoplay = true;
                video.muted = true;
                await video.play();

                // 等待视频元数据/首帧就绪，避免立即读帧时尺寸为 0×0
                if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
                    await new Promise(resolve => {
                        video.addEventListener('loadeddata', resolve, { once: true });
                    });
                }
                const frame = captureCanvasFrame(video, 0.85);
                video.srcObject = null;
                video.remove();

                if (!frame || !frame.dataUrl) {
                    // 流看似活着但提取帧失败，废弃空壳流
                    console.warn('[主动搭话截图] 缓存流提取帧失败（空壳流），废弃');
                    try {
                        captureStream.getTracks().forEach(t => { try { t.stop(); } catch (e) { } });
                    } catch (e) { }
                    if (screenCaptureStream === captureStream) {
                        screenCaptureStream = null;
                        screenCaptureStreamLastUsed = null;
                    }
                    // 空壳流说明 getDisplayMedia 已不可用（如刷新后旧流残留），
                    // 标记 autoPromptFailed 防止后续 timer/非手势上下文再次弹窗
                    screenCaptureAutoPromptFailed = true;
                    console.log('[主动搭话截图] 已标记 screenCaptureAutoPromptFailed，后续不再自动弹窗请求屏幕共享');
                    return null;
                }

                console.log(`[主动搭话截图] 前端截图成功（流已缓存），尺寸: ${frame.width}x${frame.height}`);
                return frame.dataUrl;
            } catch (err) {
                console.warn('[主动搭话截图] getDisplayMedia 失败:', err);
                screenCaptureAutoPromptFailed = true;
                console.log('[主动搭话截图] 已标记 screenCaptureAutoPromptFailed，后续不再自动弹窗请求屏幕共享');
            }
        }

        console.warn('[主动搭话截图] 所有截图方式均失败');
        return null;
    }

    // 主动视觉开关切换时的流生命周期管理
    // 开启时：测试后端 pyautogui 是否可用，不可用则通过 getDisplayMedia 获取前端流（此时处于用户手势上下文）
    // 关闭时：释放前端流（如果不是手动屏幕共享）
    async function acquireProactiveVisionStream() {
        // 策略1: 测试后端 pyautogui 是否可用（静默，无弹窗）
        const { dataUrl: backendOk } = await fetchBackendScreenshot();
        if (backendOk) {
            console.log('[主动视觉] 后端 pyautogui 可用，无需前端流');
            return true;
        }

        // 策略2: 已有可用的前端流，无需重新获取
        if (screenCaptureStream && screenCaptureStream.active) {
            const videoTracks = screenCaptureStream.getVideoTracks();
            const hasLiveTrack = videoTracks.length > 0 && videoTracks.some(t => t.readyState === 'live');
            if (hasLiveTrack) {
                console.log('[主动视觉] 已有可用的屏幕流，复用');
                screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();
                return true;
            }
            // tracks 已结束，废弃流，继续走策略3
            console.warn('[主动视觉] 缓存流 tracks 已结束，废弃并重新获取');
            screenCaptureStream = null;
            screenCaptureStreamLastUsed = null;
        }

        // 策略3: 通过 getDisplayMedia 获取流（当前处于用户点击开关的手势上下文中）
        if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
            try {
                const stream = await navigator.mediaDevices.getDisplayMedia({
                    video: { cursor: 'always', frameRate: { max: 1 } },
                    audio: false,
                });

                screenCaptureStream = stream;
                screenCaptureStreamLastUsed = Date.now();
                screenCaptureAutoPromptFailed = false; // 用户手势成功，重置标记
                scheduleScreenCaptureIdleCheck();

                // 监听用户手动停止共享
                stream.getVideoTracks().forEach(track => {
                    track.addEventListener('ended', () => {
                        console.log('[主动视觉] 屏幕共享流被用户终止');
                        if (screenCaptureStream === stream) {
                            screenCaptureStream = null;
                            screenCaptureStreamLastUsed = null;
                            if (screenCaptureStreamIdleTimer) {
                                clearTimeout(screenCaptureStreamIdleTimer);
                                screenCaptureStreamIdleTimer = null;
                            }
                        }
                    });
                });

                console.log('[主动视觉] 前端屏幕流获取成功');
                return true;
            } catch (err) {
                console.warn('[主动视觉] getDisplayMedia 失败（用户可能取消了选择）:', err);
                return false;
            }
        }

        console.warn('[主动视觉] 无可用的截图方式');
        return false;
    }

    function releaseProactiveVisionStream() {
        // 如果用户手动开启了屏幕共享，不要释放流
        if (screenButton && screenButton.classList.contains('active')) {
            console.log('[主动视觉] 手动屏幕共享活跃中，不释放流');
            return;
        }

        // 如果正在录音（语音模式），不释放流（语音模式可能也在用）
        if (isRecording) {
            console.log('[主动视觉] 语音模式活跃中，不释放流');
            return;
        }

        if (screenCaptureStream) {
            try {
                if (typeof screenCaptureStream.getTracks === 'function') {
                    screenCaptureStream.getTracks().forEach(track => {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) {
                console.warn('[主动视觉] 停止 tracks 失败:', e);
            }
            screenCaptureStream = null;
            screenCaptureStreamLastUsed = null;
            if (screenCaptureStreamIdleTimer) {
                clearTimeout(screenCaptureStreamIdleTimer);
                screenCaptureStreamIdleTimer = null;
            }
            console.log('[主动视觉] 屏幕流已释放');
        }
    }

    // 暴露函数到全局作用域，供 live2d.js 调用
    window.resetProactiveChatBackoff = resetProactiveChatBackoff;
    window.stopProactiveChatSchedule = stopProactiveChatSchedule;
    window.startProactiveVisionDuringSpeech = startProactiveVisionDuringSpeech;
    window.stopProactiveVisionDuringSpeech = stopProactiveVisionDuringSpeech;
    window.acquireProactiveVisionStream = acquireProactiveVisionStream;
    window.releaseProactiveVisionStream = releaseProactiveVisionStream;

    // 保存设置到localStorage
    function saveSettings() {
        // 从全局变量读取最新值（确保同步 live2d.js 中的更改）
        const currentProactive = typeof window.proactiveChatEnabled !== 'undefined'
            ? window.proactiveChatEnabled
            : proactiveChatEnabled;
        const currentVision = typeof window.proactiveVisionEnabled !== 'undefined'
            ? window.proactiveVisionEnabled
            : proactiveVisionEnabled;
        const currentVisionChat = typeof window.proactiveVisionChatEnabled !== 'undefined'
            ? window.proactiveVisionChatEnabled
            : proactiveVisionChatEnabled;
        const currentNewsChat = typeof window.proactiveNewsChatEnabled !== 'undefined'
            ? window.proactiveNewsChatEnabled
            : proactiveNewsChatEnabled;
        const currentVideoChat = typeof window.proactiveVideoChatEnabled !== 'undefined'
            ? window.proactiveVideoChatEnabled
            : proactiveVideoChatEnabled;
        const currentMerge = typeof window.mergeMessagesEnabled !== 'undefined'
            ? window.mergeMessagesEnabled
            : mergeMessagesEnabled;
        const currentFocus = typeof window.focusModeEnabled !== 'undefined'
            ? window.focusModeEnabled
            : focusModeEnabled;
        const currentProactiveChatInterval = typeof window.proactiveChatInterval !== 'undefined'
            ? window.proactiveChatInterval
            : proactiveChatInterval;
        const currentProactiveVisionInterval = typeof window.proactiveVisionInterval !== 'undefined'
            ? window.proactiveVisionInterval
            : proactiveVisionInterval;
        const currentPersonalChat = typeof window.proactivePersonalChatEnabled !== 'undefined'
            ? window.proactivePersonalChatEnabled
            : proactivePersonalChatEnabled;
        const currentMusicChat = typeof window.proactiveMusicEnabled !== 'undefined'
            ? window.proactiveMusicEnabled
            : proactiveMusicEnabled;
        const currentRenderQuality = typeof window.renderQuality !== 'undefined'
            ? window.renderQuality
            : renderQuality;
        const currentTargetFrameRate = typeof window.targetFrameRate !== 'undefined'
            ? window.targetFrameRate
            : targetFrameRate;
        const currentMouseTracking = typeof window.mouseTrackingEnabled !== 'undefined'
            ? window.mouseTrackingEnabled
            : true;

        const settings = {
            proactiveChatEnabled: currentProactive,
            proactiveVisionEnabled: currentVision,
            proactiveVisionChatEnabled: currentVisionChat,
            proactiveNewsChatEnabled: currentNewsChat,
            proactiveVideoChatEnabled: currentVideoChat,
            proactivePersonalChatEnabled: currentPersonalChat,
            proactiveMusicEnabled: currentMusicChat,
            mergeMessagesEnabled: currentMerge,
            focusModeEnabled: currentFocus,
            proactiveChatInterval: currentProactiveChatInterval,
            proactiveVisionInterval: currentProactiveVisionInterval,
            renderQuality: currentRenderQuality,
            targetFrameRate: currentTargetFrameRate,
            mouseTrackingEnabled: currentMouseTracking
        };
        localStorage.setItem('project_neko_settings', JSON.stringify(settings));

        // 同步回局部变量，保持一致性
        proactiveChatEnabled = currentProactive;
        proactiveVisionEnabled = currentVision;
        proactiveVisionChatEnabled = currentVisionChat;
        proactiveNewsChatEnabled = currentNewsChat;
        proactiveVideoChatEnabled = currentVideoChat;
        proactivePersonalChatEnabled = currentPersonalChat;
        proactiveMusicEnabled = currentMusicChat;
        mergeMessagesEnabled = currentMerge;
        focusModeEnabled = currentFocus;
        proactiveChatInterval = currentProactiveChatInterval;
        proactiveVisionInterval = currentProactiveVisionInterval;
        renderQuality = currentRenderQuality;
        targetFrameRate = currentTargetFrameRate;
    }

    // 暴露到全局作用域，供 live2d.js 等其他模块调用
    window.saveNEKOSettings = saveSettings;

    function _isUserRegionChina() {
        try {
            const tz = (Intl.DateTimeFormat().resolvedOptions().timeZone || '').toLowerCase();
            if (/^asia\/(shanghai|chongqing|urumqi|harbin|kashgar)$/.test(tz)) return true;
            const lang = (navigator.language || '').toLowerCase();
            if (lang === 'zh' || lang.startsWith('zh-cn') || lang.startsWith('zh-hans')) return true;
        } catch (_) { }
        return false;
    }

    // 从localStorage加载设置
    function loadSettings() {
        try {
            const saved = localStorage.getItem('project_neko_settings');
            if (saved) {
                const settings = JSON.parse(saved);

                // 迁移逻辑：检测旧版设置并迁移到新字段
                // 如果旧版 proactiveChatEnabled=true 但新字段未定义，则迁移
                let needsSave = false;
                if (settings.proactiveChatEnabled === true) {
                    const hasNewFlags = settings.proactiveVisionChatEnabled !== undefined ||
                        settings.proactiveNewsChatEnabled !== undefined ||
                        settings.proactiveVideoChatEnabled !== undefined ||
                        settings.proactivePersonalChatEnabled !== undefined ||
                        settings.proactiveMusicEnabled !== undefined;
                    if (!hasNewFlags) {
                        // 根据旧的视觉偏好决定迁移策略
                        if (settings.proactiveVisionEnabled === false) {
                            // 用户之前禁用了视觉，保留偏好并默认启用新闻搭话
                            settings.proactiveVisionEnabled = false;
                            settings.proactiveVisionChatEnabled = false;
                            settings.proactiveNewsChatEnabled = true;
                            settings.proactivePersonalChatEnabled = false;
                            settings.proactiveMusicEnabled = false;
                            console.log('迁移旧版设置：保留禁用的视觉偏好，已启用新闻搭话');
                        } else {
                            // 视觉偏好为 true 或 undefined，默认启用视觉搭话
                            settings.proactiveVisionEnabled = true;
                            settings.proactiveVisionChatEnabled = true;
                            settings.proactivePersonalChatEnabled = false;
                            settings.proactiveMusicEnabled = false;
                            console.log('迁移旧版设置：已启用视觉搭话和自主视觉');
                        }
                        needsSave = true;
                    }
                }

                // 如果进行了迁移，持久化更新后的设置
                if (needsSave) {
                    localStorage.setItem('project_neko_settings', JSON.stringify(settings));
                }

                // 使用 ?? 运算符提供更好的默认值处理（避免将 false 误判为需要使用默认值）
                proactiveChatEnabled = settings.proactiveChatEnabled ?? false;
                window.proactiveChatEnabled = proactiveChatEnabled; // 同步到全局
                // 主动视觉：从localStorage加载设置
                proactiveVisionEnabled = settings.proactiveVisionEnabled ?? false;
                window.proactiveVisionEnabled = proactiveVisionEnabled; // 同步到全局
                // 视觉搭话：从localStorage加载设置（默认开启，用户可手动关闭）
                proactiveVisionChatEnabled = settings.proactiveVisionChatEnabled ?? true;
                window.proactiveVisionChatEnabled = proactiveVisionChatEnabled; // 同步到全局
                // 新闻搭话：从localStorage加载设置
                proactiveNewsChatEnabled = settings.proactiveNewsChatEnabled ?? false;
                window.proactiveNewsChatEnabled = proactiveNewsChatEnabled; // 同步到全局
                // 视频搭话：从localStorage加载设置
                proactiveVideoChatEnabled = settings.proactiveVideoChatEnabled ?? false;
                window.proactiveVideoChatEnabled = proactiveVideoChatEnabled; // 同步到全局
                // 个人动态搭话：从localStorage加载设置
                proactivePersonalChatEnabled = settings.proactivePersonalChatEnabled ?? false;
                window.proactivePersonalChatEnabled = proactivePersonalChatEnabled; // 同步到全局
                // 音乐搭话：从localStorage加载设置
                proactiveMusicEnabled = settings.proactiveMusicEnabled ?? false;
                window.proactiveMusicEnabled = proactiveMusicEnabled; // 同步到全局
                // 合并消息：从localStorage加载设置
                mergeMessagesEnabled = settings.mergeMessagesEnabled ?? false;
                window.mergeMessagesEnabled = mergeMessagesEnabled; // 同步到全局
                // Focus模式：从localStorage加载设置
                focusModeEnabled = settings.focusModeEnabled ?? false;
                window.focusModeEnabled = focusModeEnabled; // 同步到全局
                // 主动搭话时间间隔：从localStorage加载设置
                proactiveChatInterval = settings.proactiveChatInterval ?? DEFAULT_PROACTIVE_CHAT_INTERVAL;
                window.proactiveChatInterval = proactiveChatInterval; // 同步到全局
                // 主动视觉时间间隔：从localStorage加载设置
                proactiveVisionInterval = settings.proactiveVisionInterval ?? DEFAULT_PROACTIVE_VISION_INTERVAL;
                window.proactiveVisionInterval = proactiveVisionInterval; // 同步到全局
                // 画质设置
                renderQuality = settings.renderQuality ?? 'medium';
                window.renderQuality = renderQuality;
                window.cursorFollowPerformanceLevel = mapRenderQualityToFollowPerf(renderQuality);
                // 帧率设置
                targetFrameRate = settings.targetFrameRate ?? 60;
                window.targetFrameRate = targetFrameRate;
                // 鼠标跟踪设置（严格转换为布尔值）
                if (typeof settings.mouseTrackingEnabled === 'boolean') {
                    window.mouseTrackingEnabled = settings.mouseTrackingEnabled;
                } else if (typeof settings.mouseTrackingEnabled === 'string') {
                    window.mouseTrackingEnabled = settings.mouseTrackingEnabled === 'true';
                } else {
                    window.mouseTrackingEnabled = true;
                }

                console.log('已加载设置:', {
                    proactiveChatEnabled: proactiveChatEnabled,
                    proactiveVisionEnabled: proactiveVisionEnabled,
                    proactiveVisionChatEnabled: proactiveVisionChatEnabled,
                    proactiveNewsChatEnabled: proactiveNewsChatEnabled,
                    proactiveVideoChatEnabled: proactiveVideoChatEnabled,
                    proactivePersonalChatEnabled: proactivePersonalChatEnabled,
                    mergeMessagesEnabled: mergeMessagesEnabled,
                    focusModeEnabled: focusModeEnabled,
                    proactiveChatInterval: proactiveChatInterval,
                    proactiveVisionInterval: proactiveVisionInterval,
                    focusModeDesc: focusModeEnabled ? 'AI说话时自动静音麦克风（不允许打断）' : '允许打断AI说话'
                });
            } else {
                // 首次启动：检查用户地区，中国用户自动开启自主视觉
                if (_isUserRegionChina()) {
                    proactiveVisionEnabled = true;
                    console.log('首次启动：检测到中国地区用户，已自动开启自主视觉');
                }

                console.log('未找到保存的设置，使用默认值');
                window.proactiveChatEnabled = proactiveChatEnabled;
                window.proactiveVisionEnabled = proactiveVisionEnabled;
                window.proactiveVisionChatEnabled = proactiveVisionChatEnabled;
                window.proactiveNewsChatEnabled = proactiveNewsChatEnabled;
                window.proactiveVideoChatEnabled = proactiveVideoChatEnabled;
                window.proactivePersonalChatEnabled = proactivePersonalChatEnabled;
                window.mergeMessagesEnabled = mergeMessagesEnabled;
                window.focusModeEnabled = focusModeEnabled;
                window.proactiveChatInterval = proactiveChatInterval;
                window.proactiveVisionInterval = proactiveVisionInterval;
                window.renderQuality = renderQuality;
                window.cursorFollowPerformanceLevel = mapRenderQualityToFollowPerf(renderQuality);
                window.targetFrameRate = targetFrameRate;
                window.mouseTrackingEnabled = true;

                // 持久化首次启动设置，避免每次重新检测
                saveSettings();
            }
        } catch (error) {
            console.error('加载设置失败:', error);
            // 出错时也要确保全局变量被初始化
            window.proactiveChatEnabled = proactiveChatEnabled;
            window.proactiveVisionEnabled = proactiveVisionEnabled;
            window.proactiveVisionChatEnabled = proactiveVisionChatEnabled;
            window.proactiveNewsChatEnabled = proactiveNewsChatEnabled;
            window.proactiveVideoChatEnabled = proactiveVideoChatEnabled;
            window.proactivePersonalChatEnabled = proactivePersonalChatEnabled;
            window.mergeMessagesEnabled = mergeMessagesEnabled;
            window.focusModeEnabled = focusModeEnabled;
            window.proactiveChatInterval = proactiveChatInterval;
            window.proactiveVisionInterval = proactiveVisionInterval;
            window.renderQuality = renderQuality;
            window.cursorFollowPerformanceLevel = mapRenderQualityToFollowPerf(renderQuality);
            window.targetFrameRate = targetFrameRate;
            window.mouseTrackingEnabled = true;
        }
    }

    // 加载设置
    loadSettings();

    // 加载麦克风设备选择
    loadSelectedMicrophone();

    // 加载麦克风增益设置
    loadMicGainSetting();

    // 加载扬声器音量设置
    loadSpeakerVolumeSetting();

    // 如果已开启主动搭话且选择了搭话方式，立即启动定时器
    if (proactiveChatEnabled && (proactiveVisionChatEnabled || proactiveNewsChatEnabled || proactiveVideoChatEnabled || proactivePersonalChatEnabled || proactiveMusicEnabled)) {
        // 主动搭话启动自检
        console.log('========== 主动搭话启动自检 ==========');
        console.log(`[自检] proactiveChatEnabled: ${proactiveChatEnabled}`);
        console.log(`[自检] proactiveVisionChatEnabled: ${proactiveVisionChatEnabled}`);
        console.log(`[自检] proactiveNewsChatEnabled: ${proactiveNewsChatEnabled}`);
        console.log(`[自检] proactiveVideoChatEnabled: ${proactiveVideoChatEnabled}`);
        console.log(`[自检] proactivePersonalChatEnabled: ${proactivePersonalChatEnabled}`);
        console.log(`[自检] proactiveMusicEnabled: ${proactiveMusicEnabled}`);
        console.log(`[自检] localStorage设置: ${localStorage.getItem('project_neko_settings') ? '已存在' : '不存在'}`);

        // 检查WebSocket连接状态
        const wsStatus = socket ? socket.readyState : undefined;
        console.log(`[自检] WebSocket状态: ${wsStatus} (1=OPEN, 0=CONNECTING, 2=CLOSING, 3=CLOSED)`);

        scheduleProactiveChat();
        console.log('========== 主动搭话启动自检完成 ==========');
    } else {
        console.log('[App] 主动搭话未满足启动条件，跳过调度器启动:');
        console.log(`  - proactiveChatEnabled: ${proactiveChatEnabled}`);
        console.log(`  - 任意搭话模式启用: ${proactiveVisionChatEnabled || proactiveNewsChatEnabled || proactiveVideoChatEnabled || proactivePersonalChatEnabled || proactiveMusicEnabled}`);
    }

    // 猫娘切换处理函数（通过WebSocket推送触发）
    let isSwitchingCatgirl = false;  // 标记是否正在切换猫娘，防止自动重连冲突

    // 处理猫娘切换的逻辑（支持 VRM 和 Live2D 双模型类型热切换）
    async function handleCatgirlSwitch(newCatgirl, oldCatgirl) {
        // 【新增】切换猫娘必须清空上一任的搜歌任务
        window.invalidatePendingMusicSearch();
        console.log('[猫娘切换] ========== 开始切换 ==========');
        console.log('[猫娘切换] 从', oldCatgirl, '切换到', newCatgirl);
        console.log('[猫娘切换] isSwitchingCatgirl:', isSwitchingCatgirl);

        if (isSwitchingCatgirl) {
            console.log('[猫娘切换] 正在切换中，忽略本次请求');
            return;
        }
        if (!newCatgirl) {
            console.log('[猫娘切换] newCatgirl为空，返回');
            return;
        }
        isSwitchingCatgirl = true;
        console.log('[猫娘切换] 设置 isSwitchingCatgirl = true');

        try {
            // 0. 紧急制动：立即停止所有渲染循环
            // 停止 Live2D Ticker
            if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.ticker) {
                window.live2dManager.pixi_app.ticker.stop();
            }

            // 停止 VRM 渲染循环
            if (window.vrmManager && window.vrmManager._animationFrameId) {
                cancelAnimationFrame(window.vrmManager._animationFrameId);
                window.vrmManager._animationFrameId = null;
            }

            // 1. 获取新角色的配置（包括 model_type）
            const charResponse = await fetch('/api/characters');
            if (!charResponse.ok) {
                throw new Error('无法获取角色配置');
            }
            const charactersData = await charResponse.json();
            const catgirlConfig = charactersData['猫娘']?.[newCatgirl];

            if (!catgirlConfig) {
                throw new Error(`未找到角色 ${newCatgirl} 的配置`);
            }

            const modelType = catgirlConfig.model_type || (catgirlConfig.vrm ? 'vrm' : 'live2d');

            // 2. 清理旧模型资源（温和清理，保留基础设施）

            // 清理 VRM 资源（参考 index.html 的清理逻辑）
            try {

                // 隐藏容器
                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'none';
                    vrmContainer.classList.add('hidden');
                }

                // 【关键修复】调用 cleanupUI 来完全清理 VRM UI 资源（包括浮动按钮、锁图标和"请她回来"按钮）
                if (window.vrmManager && typeof window.vrmManager.cleanupUI === 'function') {
                    window.vrmManager.cleanupUI();
                }

                if (window.vrmManager) {
                    // 1. 停止动画循环
                    if (window.vrmManager._animationFrameId) {
                        cancelAnimationFrame(window.vrmManager._animationFrameId);
                        window.vrmManager._animationFrameId = null;
                    }

                    // 2. 停止VRM动画并立即清理状态（用于角色切换）
                    if (window.vrmManager.animation) {
                        // 立即重置动画状态，不等待淡出完成
                        if (typeof window.vrmManager.animation.reset === 'function') {
                            window.vrmManager.animation.reset();
                        } else {
                            window.vrmManager.animation.stopVRMAAnimation();
                        }
                    }

                    // 3. 清理模型（从场景中移除，但不销毁scene）
                    if (window.vrmManager.currentModel && window.vrmManager.currentModel.vrm) {
                        const vrm = window.vrmManager.currentModel.vrm;
                        if (vrm.scene) {
                            vrm.scene.visible = false;
                            if (window.vrmManager.scene) {
                                window.vrmManager.scene.remove(vrm.scene);
                            }
                        }
                    }

                    // 4. 清理动画混合器
                    if (window.vrmManager.animationMixer) {
                        window.vrmManager.animationMixer.stopAllAction();
                        window.vrmManager.animationMixer = null;
                    }

                    // 5. 清理场景中剩余的模型对象（但保留光照、相机和控制器）
                    // 注意：vrm.scene 已经在上面（步骤3）从场景中移除了
                    // 这里只需要清理可能残留的其他模型对象
                    if (window.vrmManager.scene) {
                        const childrenToRemove = [];
                        window.vrmManager.scene.children.forEach((child) => {
                            // 只移除模型相关的对象，保留光照、相机和控制器
                            if (!child.isLight && !child.isCamera) {
                                // 检查是否是VRM模型场景（通过检查是否有 SkinnedMesh）
                                if (child.type === 'Group' || child.type === 'Object3D') {
                                    let hasMesh = false;
                                    child.traverse((obj) => {
                                        if (obj.isSkinnedMesh || obj.isMesh) {
                                            hasMesh = true;
                                        }
                                    });
                                    if (hasMesh) {
                                        childrenToRemove.push(child);
                                    }
                                }
                            }
                        });
                        // 移除模型对象
                        childrenToRemove.forEach(child => {
                            window.vrmManager.scene.remove(child);
                        });
                    }

                    // 6. 隐藏渲染器（但不销毁）
                    if (window.vrmManager.renderer && window.vrmManager.renderer.domElement) {
                        window.vrmManager.renderer.domElement.style.display = 'none';
                    }

                    // 7. 重置模型引用
                    window.vrmManager.currentModel = null;
                    // 不在这里设置 _goodbyeClicked = true，因为这会永久短路 showCurrentModel
                    // 标志会在 finally 块中统一重置，或在加载新模型时清除
                }

            } catch (e) {
                console.warn('[猫娘切换] VRM 清理出错:', e);
            }

            // 清理 Live2D 资源（参考 index.html 的清理逻辑）
            try {

                // 隐藏容器
                const live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'none';
                    live2dContainer.classList.add('hidden');
                }

                // 【关键修复】手动清理 Live2D UI 资源（Live2D没有cleanupUI方法）
                // 只有在切换到非Live2D模型时才清理UI
                if (modelType !== 'live2d') {
                    // 移除浮动按钮
                    const live2dButtons = document.getElementById('live2d-floating-buttons');
                    if (live2dButtons) live2dButtons.remove();

                    // 移除"请她回来"按钮
                    const live2dReturnBtn = document.getElementById('live2d-return-button-container');
                    if (live2dReturnBtn) live2dReturnBtn.remove();

                    // 清理所有可能残留的 Live2D 锁图标
                    document.querySelectorAll('#live2d-lock-icon').forEach(el => el.remove());
                }

                if (window.live2dManager) {
                    // 1. 清理模型
                    if (window.live2dManager.currentModel) {
                        if (typeof window.live2dManager.currentModel.destroy === 'function') {
                            window.live2dManager.currentModel.destroy();
                        }
                        window.live2dManager.currentModel = null;
                    }

                    // 2. 停止ticker（但保留 pixi_app，以便后续重启）
                    if (window.live2dManager.pixi_app && window.live2dManager.pixi_app.ticker) {
                        // 只有在切换到非 Live2D 模型时才停止 ticker
                        // 如果切换到 Live2D，ticker 会在加载新模型后重启
                        if (modelType !== 'live2d') {
                            window.live2dManager.pixi_app.ticker.stop();
                        }
                    }

                    // 3. 清理舞台（但不销毁pixi_app）
                    if (window.live2dManager.pixi_app && window.live2dManager.pixi_app.stage) {
                        window.live2dManager.pixi_app.stage.removeChildren();
                    }
                }

            } catch (e) {
                console.warn('[猫娘切换] Live2D 清理出错:', e);
            }

            // 3. 准备新环境
            showStatusToast(window.t ? window.t('app.switchingCatgirl', { name: newCatgirl }) : `正在切换到 ${newCatgirl}...`, 3000);

            // 清空聊天记录和相关全局状态
            const chatContainer = document.getElementById('chatContainer');
            if (chatContainer) {
                chatContainer.innerHTML = '';
            }
            // 重置聊天相关的全局状态
            window.currentGeminiMessage = null;
            window._geminiTurnFullText = '';
            // 清空realistic synthesis队列和缓冲区，防止旧角色的语音继续播放
            window._realisticGeminiQueue = [];
            window._realisticGeminiBuffer = '';
            window._pendingMusicCommand = '';
            window._realisticGeminiTimestamp = null;
            window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
            // 重置语音模式用户转录合并追踪
            lastVoiceUserMessage = null;
            lastVoiceUserMessageTime = 0;

            // 清理连接与状态
            if (autoReconnectTimeoutId) clearTimeout(autoReconnectTimeoutId);
            if (isRecording) {
                stopRecording();
                syncFloatingMicButtonState(false);
                syncFloatingScreenButtonState(false);
            }
            //  等待清空音频队列完成，避免竞态条件
            if (typeof clearAudioQueue === 'function') {
                await clearAudioQueue();
            }
            if (isTextSessionActive) isTextSessionActive = false;

            if (socket) socket.close();
            if (heartbeatInterval) clearInterval(heartbeatInterval);

            lanlan_config.lanlan_name = newCatgirl;

            await new Promise(resolve => setTimeout(resolve, 100));
            connectWebSocket();
            document.title = `${newCatgirl} Terminal - Project N.E.K.O.`;

            // 4. 根据模型类型加载相应的模型
            console.log('[猫娘切换] 检测到模型类型:', modelType);
            if (modelType === 'vrm') {
                // 加载 VRM 模型
                console.log('[猫娘切换] 进入VRM加载分支');

                // 安全获取 VRM 模型路径，处理各种边界情况
                let vrmModelPath = null;
                // 检查 vrm 字段是否存在且有效
                const hasVrmField = catgirlConfig.hasOwnProperty('vrm');
                const vrmValue = catgirlConfig.vrm;

                // 检查 vrmValue 是否是有效的值（排除字符串 "undefined" 和 "null"）
                let isVrmValueInvalid = false;
                if (hasVrmField && vrmValue !== undefined && vrmValue !== null) {
                    const rawValue = vrmValue;
                    if (typeof rawValue === 'string') {
                        const trimmed = rawValue.trim();
                        const lowerTrimmed = trimmed.toLowerCase();
                        // 检查是否是无效的字符串值（包括 "undefined", "null" 等）
                        isVrmValueInvalid = trimmed === '' ||
                            lowerTrimmed === 'undefined' ||
                            lowerTrimmed === 'null' ||
                            lowerTrimmed.includes('undefined') ||
                            lowerTrimmed.includes('null');
                        if (!isVrmValueInvalid) {
                            vrmModelPath = trimmed;
                        }
                    } else {
                        // 非字符串类型，转换为字符串后也要验证
                        const strValue = String(rawValue);
                        const lowerStr = strValue.toLowerCase();
                        isVrmValueInvalid = lowerStr === 'undefined' || lowerStr === 'null' || lowerStr.includes('undefined');
                        if (!isVrmValueInvalid) {
                            vrmModelPath = strValue;
                        }
                    }
                }

                // 如果路径无效，使用默认模型或抛出错误
                if (!vrmModelPath) {
                    // 如果配置中明确指定了 model_type 为 'vrm'，静默使用默认模型
                    if (catgirlConfig.model_type === 'vrm') {
                        vrmModelPath = '/static/vrm/sister1.0.vrm';

                        // 如果 vrmValue 是字符串 "undefined" 或 "null"，视为"未配置"，不显示警告
                        // 只有在 vrm 字段存在且值不是字符串 "undefined"/"null" 时才显示警告
                        if (hasVrmField && vrmValue !== undefined && vrmValue !== null && !isVrmValueInvalid) {
                            // 这种情况不应该发生，因为 isVrmValueInvalid 为 false 时应该已经设置了 vrmModelPath
                            const vrmValueStr = typeof vrmValue === 'string' ? `"${vrmValue}"` : String(vrmValue);
                            console.warn(`[猫娘切换] VRM 模型路径无效 (${vrmValueStr})，使用默认模型`);
                        } else {
                            // vrmValue 是字符串 "undefined"、"null" 或未配置，视为正常情况，只显示 info
                            console.info('[猫娘切换] VRM 模型路径未配置或无效，使用默认模型');

                            // 如果 vrmValue 是字符串 "undefined"，尝试自动修复后端配置
                            if (hasVrmField && isVrmValueInvalid && typeof vrmValue === 'string') {
                                try {
                                    const fixResponse = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(newCatgirl)}`, {
                                        method: 'PUT',
                                        headers: { 'Content-Type': 'application/json' },
                                        body: JSON.stringify({
                                            model_type: 'vrm',
                                            vrm: vrmModelPath  // 使用默认模型路径
                                        })
                                    });
                                    if (fixResponse.ok) {
                                        const fixResult = await fixResponse.json();
                                        if (fixResult.success) {
                                            console.log(`[猫娘切换] 已自动修复角色 ${newCatgirl} 的 VRM 模型路径配置（从 "undefined" 修复为默认模型）`);
                                        }
                                    }
                                } catch (fixError) {
                                    console.warn('[猫娘切换] 自动修复配置时出错:', fixError);
                                }
                            }
                        }
                        console.info('[猫娘切换] 使用默认 VRM 模型:', vrmModelPath);
                    } else {
                        // model_type 不是 'vrm'，抛出错误
                        const vrmValueStr = hasVrmField && vrmValue !== undefined && vrmValue !== null
                            ? (typeof vrmValue === 'string' ? `"${vrmValue}"` : String(vrmValue))
                            : '(未配置)';
                        throw new Error(`VRM 模型路径无效: ${vrmValueStr}`);
                    }
                }

                // 确保 VRM 管理器已初始化
                console.log('[猫娘切换] 检查VRM管理器 - 存在:', !!window.vrmManager, '已初始化:', window.vrmManager?._isInitialized);
                if (!window.vrmManager || !window.vrmManager._isInitialized) {
                    console.log('[猫娘切换] VRM管理器需要初始化');

                    // 等待 VRM 模块加载（双保险：事件 + 轮询）
                    if (typeof window.VRMManager === 'undefined') {
                        await new Promise((resolve, reject) => {
                            // 先检查是否已经就绪（事件可能已经发出）
                            if (window.VRMManager) {
                                return resolve();
                            }

                            let resolved = false;
                            const timeoutId = setTimeout(() => {
                                if (!resolved) {
                                    resolved = true;
                                    reject(new Error('VRM 模块加载超时'));
                                }
                            }, 5000);

                            // 方法1：监听事件
                            const eventHandler = () => {
                                if (!resolved && window.VRMManager) {
                                    resolved = true;
                                    clearTimeout(timeoutId);
                                    window.removeEventListener('vrm-modules-ready', eventHandler);
                                    resolve();
                                }
                            };
                            window.addEventListener('vrm-modules-ready', eventHandler, { once: true });

                            // 方法2：轮询检查（双保险）
                            const pollInterval = setInterval(() => {
                                if (window.VRMManager) {
                                    if (!resolved) {
                                        resolved = true;
                                        clearTimeout(timeoutId);
                                        clearInterval(pollInterval);
                                        window.removeEventListener('vrm-modules-ready', eventHandler);
                                        resolve();
                                    }
                                }
                            }, 100); // 每100ms检查一次

                            // 清理轮询（在超时或成功时）
                            const originalResolve = resolve;
                            const originalReject = reject;
                            resolve = (...args) => {
                                clearInterval(pollInterval);
                                originalResolve(...args);
                            };
                            reject = (...args) => {
                                clearInterval(pollInterval);
                                originalReject(...args);
                            };
                        });
                    }

                    if (!window.vrmManager) {
                        window.vrmManager = new window.VRMManager();
                        // 初始化时确保 _goodbyeClicked 为 false
                        window.vrmManager._goodbyeClicked = false;
                    } else {
                        // 如果 vrmManager 已存在，也清除 goodbyeClicked 标志，确保新模型可以正常显示
                        window.vrmManager._goodbyeClicked = false;
                    }

                    // 确保容器和 canvas 存在
                    const vrmContainer = document.getElementById('vrm-container');
                    if (vrmContainer && !vrmContainer.querySelector('canvas')) {
                        const canvas = document.createElement('canvas');
                        canvas.id = 'vrm-canvas';
                        vrmContainer.appendChild(canvas);
                    }

                    // 初始化 Three.js 场景，传入光照配置（如果存在）
                    const lightingConfig = catgirlConfig.lighting || null;
                    await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', lightingConfig);
                }

                // 转换路径为 URL（基本格式处理，vrm-core.js 会处理备用路径）
                // 再次验证 vrmModelPath 的有效性
                if (!vrmModelPath ||
                    vrmModelPath === 'undefined' ||
                    vrmModelPath === 'null' ||
                    (typeof vrmModelPath === 'string' && (vrmModelPath.trim() === '' || vrmModelPath.includes('undefined')))) {
                    console.error('[猫娘切换] vrmModelPath 在路径转换前无效，使用默认模型:', vrmModelPath);
                    vrmModelPath = '/static/vrm/sister1.0.vrm';
                }

                let modelUrl = vrmModelPath;

                // 确保 modelUrl 是有效的字符串
                if (typeof modelUrl !== 'string' || !modelUrl) {
                    console.error('[猫娘切换] modelUrl 不是有效字符串，使用默认模型:', modelUrl);
                    modelUrl = '/static/vrm/sister1.0.vrm';
                }

                // 处理 Windows 路径：提取文件名并转换为 Web 路径
                if (modelUrl.includes('\\') || modelUrl.includes(':')) {
                    const filename = modelUrl.split(/[\\/]/).pop();
                    if (filename && filename !== 'undefined' && filename !== 'null' && !filename.includes('undefined')) {
                        modelUrl = `/user_vrm/${filename}`;
                    } else {
                        console.error('[猫娘切换] Windows 路径提取的文件名无效，使用默认模型:', filename);
                        modelUrl = '/static/vrm/sister1.0.vrm';
                    }
                } else if (!modelUrl.startsWith('http') && !modelUrl.startsWith('/')) {
                    // 相对路径，添加 /user_vrm/ 前缀
                    // 再次验证 modelUrl 的有效性
                    if (modelUrl !== 'undefined' && modelUrl !== 'null' && !modelUrl.includes('undefined')) {
                        modelUrl = `/user_vrm/${modelUrl}`;
                    } else {
                        console.error('[猫娘切换] 相对路径无效，使用默认模型:', modelUrl);
                        modelUrl = '/static/vrm/sister1.0.vrm';
                    }
                } else {
                    // 确保路径格式正确（统一使用正斜杠）
                    modelUrl = modelUrl.replace(/\\/g, '/');
                }

                // 最终验证：确保 modelUrl 不包含 "undefined" 或 "null"
                if (typeof modelUrl !== 'string' ||
                    modelUrl.includes('undefined') ||
                    modelUrl.includes('null') ||
                    modelUrl.trim() === '') {
                    console.error('[猫娘切换] 路径转换后仍包含无效值，使用默认模型:', modelUrl);
                    modelUrl = '/static/vrm/sister1.0.vrm';
                }

                // 加载 VRM 模型（vrm-core.js 内部已实现备用路径机制，会自动尝试 /user_vrm/ 和 /static/vrm/）
                console.log('[猫娘切换] 开始加载VRM模型:', modelUrl);
                await window.vrmManager.loadModel(modelUrl);
                console.log('[猫娘切换] VRM模型加载完成');

                // 【关键修复】确保VRM渲染循环已启动（loadModel内部会调用startAnimation，但为了保险再次确认）
                if (!window.vrmManager._animationFrameId) {
                    console.log('[猫娘切换] VRM渲染循环未启动，手动启动');
                    if (typeof window.vrmManager.startAnimation === 'function') {
                        window.vrmManager.startAnimation();
                    }
                } else {
                    console.log('[猫娘切换] VRM渲染循环已启动，ID:', window.vrmManager._animationFrameId);
                }

                // 应用角色的光照配置
                if (catgirlConfig.lighting && window.vrmManager) {
                    const lighting = catgirlConfig.lighting;

                    // 确保光照已初始化，如果没有则等待（添加最大重试次数和切换取消条件）
                    let applyLightingRetryCount = 0;
                    const MAX_RETRY_COUNT = 50; // 最多重试50次（5秒）
                    let applyLightingTimerId = null;
                    const currentSwitchId = Symbol(); // 用于标识当前切换，防止旧切换的定时器继续执行
                    window._currentCatgirlSwitchId = currentSwitchId;

                    const applyLighting = () => {
                        // 检查是否切换已被取消（新的切换已开始）
                        if (window._currentCatgirlSwitchId !== currentSwitchId) {
                            if (applyLightingTimerId) {
                                clearTimeout(applyLightingTimerId);
                                applyLightingTimerId = null;
                            }
                            return;
                        }

                        if (window.vrmManager?.ambientLight && window.vrmManager?.mainLight &&
                            window.vrmManager?.fillLight && window.vrmManager?.rimLight) {
                            // VRoid Hub 风格：极高环境光，柔和主光，无辅助光
                            const defaultLighting = {
                                ambient: 1.0,      // 极高环境光，消除所有暗部
                                main: 0.6,         // 适中主光，配合跟随相机
                                fill: 0.0,         // 不需要补光
                                rim: 0.0,          // 不需要外部轮廓光
                                top: 0.0,          // 不需要顶光
                                bottom: 0.0        // 不需要底光
                            };

                            if (window.vrmManager.ambientLight) {
                                window.vrmManager.ambientLight.intensity = lighting.ambient ?? defaultLighting.ambient;
                            }
                            if (window.vrmManager.mainLight) {
                                window.vrmManager.mainLight.intensity = lighting.main ?? defaultLighting.main;
                            }
                            if (window.vrmManager.fillLight) {
                                window.vrmManager.fillLight.intensity = lighting.fill ?? defaultLighting.fill;
                            }
                            if (window.vrmManager.rimLight) {
                                window.vrmManager.rimLight.intensity = lighting.rim ?? defaultLighting.rim;
                            }
                            if (window.vrmManager.topLight) {
                                window.vrmManager.topLight.intensity = lighting.top ?? defaultLighting.top;
                            }
                            if (window.vrmManager.bottomLight) {
                                window.vrmManager.bottomLight.intensity = lighting.bottom ?? defaultLighting.bottom;
                            }

                            // 强制渲染一次，确保光照立即生效
                            if (window.vrmManager.renderer && window.vrmManager.scene && window.vrmManager.camera) {
                                window.vrmManager.renderer.render(window.vrmManager.scene, window.vrmManager.camera);
                            }

                            // 成功应用，清理定时器
                            if (applyLightingTimerId) {
                                clearTimeout(applyLightingTimerId);
                                applyLightingTimerId = null;
                            }
                        } else {
                            // 光照未初始化，延迟重试（但限制重试次数）
                            applyLightingRetryCount++;
                            if (applyLightingRetryCount < MAX_RETRY_COUNT) {
                                applyLightingTimerId = setTimeout(applyLighting, 100);
                            } else {
                                console.warn('[猫娘切换] 光照应用失败：已达到最大重试次数');
                                if (applyLightingTimerId) {
                                    clearTimeout(applyLightingTimerId);
                                    applyLightingTimerId = null;
                                }
                            }
                        }
                    };

                    applyLighting();
                }

                if (window.LanLan1) {
                    window.LanLan1.live2dModel = null;
                    window.LanLan1.currentModel = null;
                }

                // 显示 VRM 容器

                const vrmContainer = document.getElementById('vrm-container');
                const live2dContainer = document.getElementById('live2d-container');

                console.log('[猫娘切换] 显示VRM容器 - vrmContainer存在:', !!vrmContainer, 'live2dContainer存在:', !!live2dContainer);

                if (vrmContainer) {
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.style.display = 'block';
                    vrmContainer.style.visibility = 'visible';
                    vrmContainer.style.pointerEvents = 'auto';
                    console.log('[猫娘切换] VRM容器已设置为可见');

                    // 检查容器的实际状态
                    const computedStyle = window.getComputedStyle(vrmContainer);
                    console.log('[猫娘切换] VRM容器状态 - display:', computedStyle.display, 'visibility:', computedStyle.visibility, 'opacity:', computedStyle.opacity, 'zIndex:', computedStyle.zIndex);
                    console.log('[猫娘切换] VRM容器子元素数量:', vrmContainer.children.length);
                }

                if (live2dContainer) {
                    live2dContainer.style.display = 'none';
                    live2dContainer.classList.add('hidden');
                }

                // 确保 VRM 渲染器可见
                if (window.vrmManager && window.vrmManager.renderer && window.vrmManager.renderer.domElement) {
                    window.vrmManager.renderer.domElement.style.display = 'block';
                    window.vrmManager.renderer.domElement.style.visibility = 'visible';
                    window.vrmManager.renderer.domElement.style.opacity = '1';
                    console.log('[猫娘切换] VRM渲染器已设置为可见');

                    // 检查canvas的实际状态
                    const canvas = window.vrmManager.renderer.domElement;
                    const computedStyle = window.getComputedStyle(canvas);
                    console.log('[猫娘切换] VRM Canvas状态 - display:', computedStyle.display, 'visibility:', computedStyle.visibility, 'opacity:', computedStyle.opacity, 'zIndex:', computedStyle.zIndex);
                } else {
                    console.warn('[猫娘切换] ⚠️ VRM渲染器不存在或未初始化');
                }

                const chatContainer = document.getElementById('chat-container');
                const textInputArea = document.getElementById('text-input-area');
                console.log('[猫娘切换] VRM - 恢复对话框 - chatContainer存在:', !!chatContainer, '当前类:', chatContainer ? chatContainer.className : 'N/A');
                if (chatContainer) chatContainer.classList.remove('minimized');
                if (textInputArea) textInputArea.classList.remove('hidden');
                console.log('[猫娘切换] VRM - 对话框已恢复，当前类:', chatContainer ? chatContainer.className : 'N/A');

                // 确保 VRM 按钮和锁图标可见
                setTimeout(() => {
                    const vrmButtons = document.getElementById('vrm-floating-buttons');
                    console.log('[猫娘切换] VRM按钮检查 - 存在:', !!vrmButtons);
                    if (vrmButtons) {
                        vrmButtons.style.removeProperty('display');
                        vrmButtons.style.removeProperty('visibility');
                        vrmButtons.style.removeProperty('opacity');
                        console.log('[猫娘切换] VRM按钮已设置为可见');
                    } else {
                        console.warn('[猫娘切换] ⚠️ VRM浮动按钮不存在，尝试重新创建');
                        if (window.vrmManager && typeof window.vrmManager.setupFloatingButtons === 'function') {
                            window.vrmManager.setupFloatingButtons();
                            const newVrmButtons = document.getElementById('vrm-floating-buttons');
                            console.log('[猫娘切换] 重新创建后VRM按钮存在:', !!newVrmButtons);
                        }
                    }

                    // 【关键】显示 VRM 锁图标
                    const vrmLockIcon = document.getElementById('vrm-lock-icon');
                    if (vrmLockIcon) {
                        vrmLockIcon.style.removeProperty('display');
                        vrmLockIcon.style.removeProperty('visibility');
                        vrmLockIcon.style.removeProperty('opacity');
                    }
                }, 300);

            } else {
                // 加载 Live2D 模型

                // 重置goodbyeClicked标志（包括 VRM 的，避免快速切换时遗留）
                if (window.live2dManager) {
                    window.live2dManager._goodbyeClicked = false;
                }
                if (window.vrmManager) {
                    window.vrmManager._goodbyeClicked = false;
                }

                const modelResponse = await fetch(`/api/characters/current_live2d_model?catgirl_name=${encodeURIComponent(newCatgirl)}`);
                const modelData = await modelResponse.json();

                // 确保 Manager 存在
                if (!window.live2dManager && typeof Live2DManager === 'function') {
                    window.live2dManager = new Live2DManager();
                }

                // 初始化或重用 PIXI
                if (window.live2dManager) {
                    if (!window.live2dManager.pixi_app || !window.live2dManager.pixi_app.renderer) {
                        await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
                    }
                }

                // 加载新模型
                if (modelData.success && modelData.model_info) {
                    const modelConfigRes = await fetch(modelData.model_info.path);
                    if (modelConfigRes.ok) {
                        const modelConfig = await modelConfigRes.json();
                        modelConfig.url = modelData.model_info.path;

                        const preferences = await window.live2dManager.loadUserPreferences();
                        const modelPreferences = preferences ? preferences.find(p => p.model_path === modelConfig.url) : null;

                        await window.live2dManager.loadModel(modelConfig, {
                            preferences: modelPreferences,
                            isMobile: window.innerWidth <= 768
                        });

                        if (window.LanLan1) {
                            window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                            window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                        }

                        // 确保所有 VRM 锁图标已完全移除（loadModel 内部会调用 setupHTMLLockIcon）
                        // 清理所有可能残留的 VRM 锁图标
                        document.querySelectorAll('#vrm-lock-icon, #vrm-lock-icon-hidden').forEach(el => el.remove());

                        // 【关键修复】确保 PIXI ticker 在模型加载完成后立即启动
                        if (window.live2dManager?.pixi_app?.ticker) {
                            try {
                                if (!window.live2dManager.pixi_app.ticker.started) {
                                    window.live2dManager.pixi_app.ticker.start();
                                    console.log('[猫娘切换] Live2D ticker 已启动');
                                }
                                // 强制触发一次更新以确保模型正常渲染
                                const currentModel = window.live2dManager.getCurrentModel();
                                if (currentModel && currentModel.internalModel && currentModel.internalModel.coreModel) {
                                    window.live2dManager.pixi_app.ticker.update();
                                }
                            } catch (tickerError) {
                                console.error('[猫娘切换] Ticker 启动失败:', tickerError);
                            }
                        }
                    }
                }

                // 显示 Live2D 容器

                if (typeof showLive2d === 'function') {
                    showLive2d();
                } else {
                    const l2dContainer = document.getElementById('live2d-container');
                    if (l2dContainer) {
                        l2dContainer.classList.remove('minimized');
                        l2dContainer.classList.remove('hidden');
                        l2dContainer.style.display = 'block';
                        l2dContainer.style.visibility = 'visible';
                    }
                }

                const vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'none';
                    vrmContainer.classList.add('hidden');
                }

                const chatContainer = document.getElementById('chat-container');
                const textInputArea = document.getElementById('text-input-area');
                if (chatContainer) chatContainer.classList.remove('minimized');
                if (textInputArea) textInputArea.classList.remove('hidden');

                // 延时重启 Ticker 和显示按钮（双重保险）
                setTimeout(() => {

                    window.dispatchEvent(new Event('resize'));

                    // 确保 PIXI ticker 正确启动（双重保险）
                    if (window.live2dManager?.pixi_app?.ticker) {
                        // 强制启动 ticker（即使已经启动也重新启动以确保正常）
                        try {
                            if (!window.live2dManager.pixi_app.ticker.started) {
                                window.live2dManager.pixi_app.ticker.start();
                                console.log('[猫娘切换] Live2D ticker 延迟启动（双重保险）');
                            }
                            // 确保模型更新循环正在运行
                            const currentModel = window.live2dManager.getCurrentModel();
                            if (currentModel && currentModel.internalModel && currentModel.internalModel.coreModel) {
                                // 强制触发一次更新以确保模型正常渲染
                                if (window.live2dManager.pixi_app.ticker) {
                                    window.live2dManager.pixi_app.ticker.update();
                                }
                            } else {
                                console.warn('[猫娘切换] Live2D 模型未完全加载，ticker 可能无法正常工作');
                            }
                        } catch (tickerError) {
                            console.error('[猫娘切换] Ticker 启动失败:', tickerError);
                        }
                    } else {
                        console.warn('[猫娘切换] Live2D pixi_app 或 ticker 不存在');
                    }

                    const l2dCanvas = document.getElementById('live2d-canvas');
                    if (l2dCanvas) l2dCanvas.style.pointerEvents = 'auto';

                    const l2dButtons = document.getElementById('live2d-floating-buttons');
                    if (l2dButtons) {
                        l2dButtons.style.setProperty('display', 'flex', 'important');
                        l2dButtons.style.visibility = 'visible';
                        l2dButtons.style.opacity = '1';
                    }

                    // 【关键】显示 Live2D 锁图标（loadModel 内部已调用 setupHTMLLockIcon）
                    const live2dLockIcon = document.getElementById('live2d-lock-icon');
                    if (live2dLockIcon) {
                        //  使用 setProperty 移除之前的 !important 样式，确保能够正常显示
                        live2dLockIcon.style.removeProperty('display');
                        live2dLockIcon.style.removeProperty('visibility');
                        live2dLockIcon.style.setProperty('display', 'block', 'important');
                        live2dLockIcon.style.setProperty('visibility', 'visible', 'important');
                        live2dLockIcon.style.setProperty('opacity', '1', 'important');
                    } else {
                        // 如果锁图标不存在，尝试重新创建
                        // 这可能发生在快速切换模型类型时，锁图标创建被阻止的情况
                        const currentModel = window.live2dManager?.getCurrentModel();
                        if (currentModel && window.live2dManager?.setupHTMLLockIcon) {
                            console.log('[锁图标] 锁图标不存在，尝试重新创建');
                            window.live2dManager.setupHTMLLockIcon(currentModel);
                            // 再次尝试显示
                            const newLockIcon = document.getElementById('live2d-lock-icon');
                            if (newLockIcon) {
                                newLockIcon.style.removeProperty('display');
                                newLockIcon.style.removeProperty('visibility');
                                newLockIcon.style.setProperty('display', 'block', 'important');
                                newLockIcon.style.setProperty('visibility', 'visible', 'important');
                                newLockIcon.style.setProperty('opacity', '1', 'important');
                            }
                        }
                    }
                }, 300);
            }

            showStatusToast(window.t ? window.t('app.switchedCatgirl', { name: newCatgirl }) : `已切换到 ${newCatgirl}`, 3000);

            // 【成就】解锁换肤成就
            if (window.unlockAchievement) {
                try {
                    await window.unlockAchievement('ACH_CHANGE_SKIN');
                } catch (err) {
                    console.error('解锁换肤成就失败:', err);
                }
            }

        } catch (error) {
            console.error('[猫娘切换] 失败:', error);
            showStatusToast(`切换失败: ${error.message}`, 4000);
        } finally {
            isSwitchingCatgirl = false;
            // 清理切换标识，取消所有 pending 的 applyLighting 定时器
            window._currentCatgirlSwitchId = null;

            // 重置 goodbyeClicked 标志，确保 showCurrentModel 可以正常运行
            if (window.live2dManager) {
                window.live2dManager._goodbyeClicked = false;
            }
            if (window.vrmManager) {
                window.vrmManager._goodbyeClicked = false;
            }
        }
    }

    // 确保特定元素始终保持隐藏
    function ensureHiddenElements() {
        const elementsToHide = [
            document.getElementById('sidebar'),
            document.getElementById('sidebarbox'),
            document.getElementById('status')
        ].filter(Boolean);

        elementsToHide.forEach(element => {
            if (element) {
                element.style.setProperty('display', 'none', 'important');
                element.style.setProperty('visibility', 'hidden', 'important');
            }
        });
    }

    // 立即执行一次
    ensureHiddenElements();

    // 使用MutationObserver监听特定元素的样式变化，确保这些元素始终保持隐藏
    const observerCallback = (mutations) => {
        // 避免递归调用：只在元素变为可见时才强制隐藏
        let needsHiding = false;
        mutations.forEach(mutation => {
            if (mutation.type === 'attributes' && mutation.attributeName === 'style') {
                const target = mutation.target;
                const computedStyle = window.getComputedStyle(target);
                if (computedStyle.display !== 'none' || computedStyle.visibility !== 'hidden') {
                    needsHiding = true;
                }
            }
        });

        if (needsHiding) {
            ensureHiddenElements();
        }
    };

    const observer = new MutationObserver(observerCallback);

    // 只监听sidebar、sidebarbox和status元素的样式变化
    const elementsToObserve = [
        document.getElementById('sidebar'),
        document.getElementById('sidebarbox'),
        document.getElementById('status')
    ].filter(Boolean);

    elementsToObserve.forEach(element => {
        observer.observe(element, {
            attributes: true,
            attributeFilter: ['style']
        });
    });

    // 页面卸载前清理屏幕捕获流，避免 macOS 上旧捕获会话残留导致刷新后 getDisplayMedia 冲突
    // 必须在 init_app() 内注册，才能正确闭合到 let screenCaptureStream 局部变量
    window.addEventListener("beforeunload", () => {
        try {
            if (typeof screenCaptureStream !== 'undefined' && screenCaptureStream &&
                typeof screenCaptureStream.getTracks === 'function') {
                screenCaptureStream.getTracks().forEach(track => {
                    try { track.stop(); } catch (e) { }
                });
            }
        } catch (e) { }
    });
} // 兼容老按钮

const ready = async () => {
    if (ready._called) return;
    ready._called = true;
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        const PAGE_CONFIG_READY_TIMEOUT = Symbol('page-config-ready-timeout');
        const PAGE_CONFIG_READY_TIMEOUT_MS = 3000;
        let timeoutId = null;
        try {
            const waitResult = await Promise.race([
                window.pageConfigReady,
                new Promise(resolve => {
                    timeoutId = setTimeout(() => resolve(PAGE_CONFIG_READY_TIMEOUT), PAGE_CONFIG_READY_TIMEOUT_MS);
                })
            ]);
            if (waitResult === PAGE_CONFIG_READY_TIMEOUT) {
                console.warn(`[Init] pageConfigReady pending over ${PAGE_CONFIG_READY_TIMEOUT_MS}ms, continue with fallback config`);
            }
        } catch (error) {
            console.warn('[Init] pageConfigReady rejected, continue with fallback config', error);
        } finally {
            if (timeoutId !== null) {
                clearTimeout(timeoutId);
            }
        }
    }
    init_app();
};

// 检查页面加载状态，如果已加载完成则直接执行
if (document.readyState === "complete" || document.readyState === "interactive") {
    setTimeout(ready, 1); // 使用setTimeout确保异步执行，避免阻塞当前脚本执行
} else {
    document.addEventListener("DOMContentLoaded", ready);
    window.addEventListener("load", ready);
}

// 页面加载后显示启动提示
window.addEventListener("load", () => {
    setTimeout(() => {
        if (typeof window.showStatusToast === 'function' && typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(window.t ? window.t('app.started', { name: lanlan_config.lanlan_name }) : `${lanlan_config.lanlan_name}已启动`, 3000);
        }
    }, 1000);

    // 拉取待弹重要通知（由后端启动阶段缓冲，前端页面加载后串行展示）
    // 使用游标确认：只 ack 本次拉取到的通知，避免 peek→ack 之间新入队的通知被误删。
    setTimeout(async () => {
        try {
            const r = await fetch('/api/pending-notices');
            const data = await r.json();
            const notices = Array.isArray(data) ? data : (data.notices || []);
            const cursor = (data && typeof data.cursor === 'number') ? data.cursor : 0;
            if (notices.length > 0 && typeof window.showProminentNotice === 'function') {
                for (const n of notices) {
                    if (n) await window.showProminentNotice(n);
                }
                await fetch('/api/pending-notices/ack', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cursor }),
                }).catch(() => { });
            }
        } catch (_) { }
    }, 2000);
});

// 监听voice_id更新消息和VRM表情预览消息
window.addEventListener('message', function (event) {
    // 安全检查：验证消息来源
    if (event.origin !== window.location.origin) {
        return;
    }

    // 防御性检查：确保 event.data 存在且有 type 属性
    if (!event || !event.data || typeof event.data.type === 'undefined') {
        return;
    }

    if (event.data.type === 'voice_id_updated') {
        console.log('[Voice Clone] 收到voice_id更新消息:', event.data.voice_id);
        if (typeof window.showStatusToast === 'function' && typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(window.t ? window.t('app.voiceUpdated', { name: lanlan_config.lanlan_name }) : `${lanlan_config.lanlan_name}的语音已更新`, 3000);
        }
    }

    // VRM 表情预览（从 vrm_emotion_manager 页面发送）
    if (event.data.type === 'vrm-preview-expression') {
        // 防御性检查：确保 expression 属性存在
        if (typeof event.data.expression === 'undefined') {
            return;
        }
        console.log('[VRM] 收到表情预览请求:', event.data.expression);
        if (window.vrmManager && window.vrmManager.expression) {
            window.vrmManager.expression.setBaseExpression(event.data.expression);
        }
    }

    // VRM 实际表情列表请求（从 vrm_emotion_manager 页面发送）
    if (event.data.type === 'vrm-get-expressions') {
        console.log('[VRM] 收到表情列表请求');
        let expressions = [];
        if (window.vrmManager && window.vrmManager.expression) {
            expressions = window.vrmManager.expression.getExpressionList();
        }
        // 发送回复
        if (event.source) {
            event.source.postMessage({
                type: 'vrm-expressions-response',
                expressions: expressions
            }, window.location.origin);
        }
    }

    // 旧的模型热切换代码已移至前面的 handleModelReload 函数
    // 不再需要这里的重复监听器
});
