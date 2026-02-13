// OGG OPUS 流式解码器 (WASM)
// 使用 @wasm-audio-decoders/ogg-opus-decoder
// https://github.com/eshaz/wasm-audio-decoders/tree/main/src/ogg-opus-decoder
// 库已在 index.html 中预加载，全局变量为 window["ogg-opus-decoder"]

// [Performance] 全局调试开关
window.DEBUG_AUDIO = typeof window.DEBUG_AUDIO !== 'undefined' ? window.DEBUG_AUDIO : false;
window.DEBUG_LIPSYNC = typeof window.DEBUG_LIPSYNC !== 'undefined' ? window.DEBUG_LIPSYNC : false;

let oggOpusDecoder = null;
let oggOpusDecoderReady = null;

// 安全的翻译函数，如果 window.t 不可用或翻译缺失则返回回退文本
function safeT(key, fallback, params) {
    if (!window.t) {
        console.error(`[safeT] window.t is not available, using fallback for key: ${key}`);
        return fallback;
    }
    try {
        const result = params ? window.t(key, params) : window.t(key);
        // 如果翻译结果等于 key 本身，说明翻译缺失，使用回退文本
        if (result === key) {
            console.error(`[safeT] Translation missing for key: ${key}, using fallback`);
            return fallback;
        }
        return result;
    } catch (e) {
        console.error(`[safeT] Error translating key: ${key}`, e);
        return fallback;
    }
}

async function getOggOpusDecoder() {
    if (oggOpusDecoder) return oggOpusDecoder;
    if (oggOpusDecoderReady) {
        try {
            const result = await oggOpusDecoderReady;
            if (result !== null) return result;
        } catch (e) {
            console.warn(safeT('console.oggOpusInitFailed', 'Ogg Opus decoder initialization failed'), e);
        }
        oggOpusDecoderReady = null;
    }

    oggOpusDecoderReady = (async () => {
        const module = window["ogg-opus-decoder"];
        if (!module || !module.OggOpusDecoder) {
            console.error(safeT('console.oggOpusNotLoaded', 'Ogg Opus decoder not loaded'));
            return null;
        }

        try {
            const decoder = new module.OggOpusDecoder();
            await decoder.ready;
            console.log(safeT('console.oggOpusReady', 'Ogg Opus decoder ready'));
            oggOpusDecoder = decoder;
            return decoder;
        } catch (e) {
            console.error(safeT('console.oggOpusCreateFailed', 'Failed to create Ogg Opus decoder'), e);
            return null;
        }
    })();

    try {
        const result = await oggOpusDecoderReady;
        if (result === null) oggOpusDecoderReady = null;
        return result;
    } catch (e) {
        // Promise reject 会"毒化缓存"，需要清空缓存允许重试
        oggOpusDecoderReady = null;
        oggOpusDecoder = null;
        console.warn(safeT('console.oggOpusInitRejected', 'Ogg Opus decoder initialization rejected'), e);
        return null;
    }
}

// 重置解码器（在新的音频流开始时调用）
// 使用 reset() 而非 free()：reset() 是为新的音频流做状态重置，实例仍可复用
async function resetOggOpusDecoder() {
    if (oggOpusDecoder) {
        try {
            // reset() 是异步的，用于重置解码器状态以处理新的音频流
            await oggOpusDecoder.reset();
        } catch (e) {
            console.warn(safeT('console.oggOpusResetFailed', 'Failed to reset Ogg Opus decoder'), e);
            oggOpusDecoder = null;
            oggOpusDecoderReady = null;
        }
    }
}

async function decodeOggOpusChunk(uint8Array) {
    const decoder = await getOggOpusDecoder();
    if (!decoder) {
        throw new Error('OGG OPUS 解码器不可用');
    }

    // decode() 用于流式解码
    const { channelData, samplesDecoded, sampleRate } = await decoder.decode(uint8Array);
    if (channelData && channelData[0] && channelData[0].length > 0) {
        return { float32Data: channelData[0], sampleRate: sampleRate || 48000 };
    }
    return null; // 数据不足，等待更多
}

// 全局窗口管理函数
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
    const textSendButton = document.getElementById('textSendButton');
    const screenshotButton = document.getElementById('screenshotButton');
    const screenshotThumbnailContainer = document.getElementById('screenshot-thumbnail-container');
    const screenshotsList = document.getElementById('screenshots-list');
    const screenshotCount = document.getElementById('screenshot-count');
    const clearAllScreenshots = document.getElementById('clear-all-screenshots');

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
    let audioStartTime = 0;
    let scheduledSources = [];
    let animationFrameId;
    let seqCounter = 0;
    let globalAnalyser = null;
    let speakerGainNode = null;  // 扬声器音量增益节点
    let lipSyncActive = false;
    let screenCaptureStream = null; // 暂存屏幕共享stream，不再需要每次都弹窗选择共享区域，方便自动重连
    let screenCaptureStreamLastUsed = null; // 记录屏幕流最后使用时间，用于闲置自动释放
    let screenCaptureStreamIdleTimer = null; // 闲置释放定时器

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
                    // 达到闲置阈值，调用 stopScreenSharing 统一释放资源并同步 UI
                    console.log(safeT('console.screenShareIdleDetected', 'Screen share idle detected, releasing resources'));
                    try {
                        await stopScreenSharing();
                    } catch (e) {
                        console.warn(safeT('console.screenShareAutoReleaseFailed', 'Screen share auto-release failed'), e);
                        // stopScreenSharing 失败时，手动清理残留状态防止 double-teardown
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
    const VOICE_TRANSCRIPT_MERGE_WINDOW = 5000; // 合并时间窗口（毫秒），5秒内的连续转录会合并

    // 主动搭话功能相关
    let proactiveChatEnabled = false;
    let proactiveVisionEnabled = false;
    let mergeMessagesEnabled = false;
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

    // Focus模式为true时，AI播放语音时会自动静音麦克风（不允许打断）
    let focusModeEnabled = false;

    // 暴露到全局作用域，供 live2d.js 等其他模块访问和修改
    window.proactiveChatEnabled = proactiveChatEnabled;
    window.proactiveVisionEnabled = proactiveVisionEnabled;
    window.mergeMessagesEnabled = mergeMessagesEnabled;
    window.focusModeEnabled = focusModeEnabled;
    window.proactiveChatInterval = proactiveChatInterval;
    window.proactiveVisionInterval = proactiveVisionInterval;

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

    // 建立WebSocket连接
    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss" : "ws";
        const wsUrl = `${protocol}://${window.location.host}/ws/${lanlan_config.lanlan_name}`;
        console.log(window.t('console.websocketConnecting'), lanlan_config.lanlan_name, window.t('console.websocketUrl'), wsUrl);
        socket = new WebSocket(wsUrl);

        socket.onopen = () => {
            console.log(window.t('console.websocketConnected'));

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
                // [Performance] 减少高频二进制数据的日志输出
                if (window.DEBUG_AUDIO) {
                    console.log(window.t('console.audioBinaryReceived'), event.data.size, window.t('console.audioBinaryBytes'));
                }
                handleAudioBlob(event.data);
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
                    const attempt = response.attempt || 0;
                    const maxAttempts = response.max_attempts || 0;
                    console.log(`[Discard] AI回复被丢弃 reason=${response.reason} attempt=${attempt}/${maxAttempts} retry=${response.will_retry}`);
                    
                    window._realisticGeminiQueue = [];
                    window._realisticGeminiBuffer = '';
                    window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
                    
                    if (window.currentTurnGeminiBubbles && window.currentTurnGeminiBubbles.length > 0) {
                        window.currentTurnGeminiBubbles.forEach(bubble => {
                            if (bubble && bubble.parentNode) {
                                bubble.parentNode.removeChild(bubble);
                            }
                        });
                        window.currentTurnGeminiBubbles = [];
                    }

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
                    
                    // 只清空播放队列，不重置解码器（避免丢失新音频的头信息）
                    clearAudioQueueWithoutDecoderReset();
                } else if (response.type === 'audio_chunk') {
                    if (window.DEBUG_AUDIO) {
                        console.log(window.t('console.audioChunkHeaderReceived'), response);
                    }
                    // 精确打断控制：根据 speech_id 决定是否接收此音频
                    const speechId = response.speech_id;
                    
                    // 检查是否是被打断的旧音频，如果是则丢弃
                    if (speechId && interruptedSpeechId && speechId === interruptedSpeechId) {
                        console.log(window.t('console.discardInterruptedAudio'), speechId);
                        skipNextAudioBlob = true;  // 标记跳过后续的二进制数据
                        return;
                    }
                    
                    // 检查是否是新的 speech_id（新轮对话开始）
                    if (speechId && speechId !== currentPlayingSpeechId) {
                        // 新轮对话开始，在此时重置解码器（确保有新的头信息）
                        if (pendingDecoderReset) {
                            console.log(window.t('console.newConversationResetDecoder'), speechId);
                            // 使用立即执行的异步函数等待重置完成，避免竞态条件
                            (async () => {
                                await resetOggOpusDecoder();
                                pendingDecoderReset = false;
                            })();
                        } else {
                            pendingDecoderReset = false;
                        }
                        currentPlayingSpeechId = speechId;
                        interruptedSpeechId = null;  // 清除旧的打断记录
                    }
                    
                    skipNextAudioBlob = false;  // 允许接收后续的二进制数据
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
                    // 如果正在切换模式且收到"已离开"消息，则忽略
                    if (isSwitchingMode && response.message.includes('已离开')) {
                        console.log(window.t('console.modeSwitchingIgnoreLeft'));
                        return;
                    }

                    // 检测严重错误，自动隐藏准备提示（兜底机制）
                    const criticalErrorKeywords = ['连续失败', '已停止', '自动重试', '崩溃', '欠费', 'API Key被'];
                    if (criticalErrorKeywords.some(keyword => response.message.includes(keyword))) {
                        console.log(window.t('console.seriousErrorHidePreparing'));
                        hideVoicePreparingToast();
                    }

                    // 翻译后端发送的状态消息
                    const translatedMessage = window.translateStatusMessage ? window.translateStatusMessage(response.message) : response.message;
                    showStatusToast(translatedMessage, 4000);
                    if (response.message === `${lanlan_config.lanlan_name}失联了，即将重启！`) {
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
                                    let autoRestartTimeoutId = null;
                                    const sessionStartPromise = new Promise((resolve, reject) => {
                                        sessionStartedResolver = resolve;
                                        sessionStartedRejecter = reject; //  保存 reject 函数
                                    });

                                    // 发送start session事件
                                    socket.send(JSON.stringify({
                                        action: 'start_session',
                                        input_type: 'audio'
                                    }));
                                    
                                    // 在发送消息后才开始超时计时（自动重启场景）
                                    autoRestartTimeoutId = setTimeout(() => {
                                        if (sessionStartedRejecter) {
                                            const rejecter = sessionStartedRejecter;
                                            sessionStartedResolver = null;
                                            sessionStartedRejecter = null; //  同时清理 rejecter
                                            
                                            // 超时时向后端发送 end_session 消息
                                            if (socket.readyState === WebSocket.OPEN) {
                                                socket.send(JSON.stringify({
                                                    action: 'end_session'
                                                }));
                                                console.log(window.t('console.autoRestartTimeoutEndSession'));
                                            }
                                            
                                            rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                                        }
                                    }, 10000);

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
                                    
                                    // 重启失败时向后端发送 end_session 消息
                                    if (socket.readyState === WebSocket.OPEN) {
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
                } else if (response.type === 'system' && response.data === 'turn end') {
                    console.log(window.t('console.turnEndReceived'));
                    // 合并消息关闭（分句模式）时：兜底 flush 未以标点结尾的最后缓冲，避免最后一段永远不显示
                    try {
                        const rest = typeof window._realisticGeminiBuffer === 'string'
                            ? window._realisticGeminiBuffer
                            : '';
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
                        const fullText = (bufferedFullText && bufferedFullText.trim()) ? bufferedFullText : fallbackFromBubble;
                        
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
                    if ((proactiveChatEnabled || proactiveVisionEnabled) && !isRecording) {
                        resetProactiveChatBackoff();
                    }
                } else if (response.type === 'session_preparing') {
                    console.log(window.t('console.sessionPreparingReceived'), response.input_mode);
                    // 显示持续性的准备中提示
                    const preparingMessage = response.input_mode === 'text'
                        ? (window.t ? window.t('app.textSystemPreparing') : '文本系统准备中，请稍候...')
                        : (window.t ? window.t('app.voiceSystemPreparing') : '语音系统准备中，请稍候...');
                    showVoicePreparingToast(preparingMessage);
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
                    }
                    sessionStartedResolver = null;
                    sessionStartedRejecter = null;
                } else if (response.type === 'reload_page') {
                    console.log(window.t('console.reloadPageReceived'), response.message);
                    // 显示提示信息
                    showStatusToast(response.message || (window.t ? window.t('app.configUpdated') : '配置已更新，页面即将刷新'), 3000);

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

    // 初始化连接
    connectWebSocket();

    // 初始化 BroadcastChannel 用于跨页面通信（与 model_manager 通信）
    let nekoBroadcastChannel = null;
    try {
        if (typeof BroadcastChannel !== 'undefined') {
            nekoBroadcastChannel = new BroadcastChannel('neko_page_channel');
            console.log('[BroadcastChannel] 主页面 BroadcastChannel 已初始化');

            nekoBroadcastChannel.onmessage = async function(event) {
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

    // 监听记忆编辑通知（从 memory_browser iframe 发送）
    window.addEventListener('message', function (event) {
        if (event.data && event.data.type === 'memory_edited') {
            console.log(window.t('console.memoryEditedRefreshContext'), event.data.catgirl_name);
            // 停止当前语音捕获，用户再次开麦时会自动刷新上下文
            if (isRecording) {
                stopMicCapture();
            }
            // 显示提示
            showStatusToast(window.t ? window.t('memory.refreshed') : '记忆已更新，下次对话将使用新记忆', 4000);
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
        messageDiv.textContent = "[" + getCurrentTimeString() + "] 🎀 " + sentence;
        chatContainer.appendChild(messageDiv);
        window.currentGeminiMessage = messageDiv;
        
        // ========== 新增：追踪本轮气泡 ==========
        window.currentTurnGeminiBubbles.push(messageDiv);
        // ========== 追踪结束 ==========

        // 检测AI消息的语言，如果与用户语言不同，显示字幕提示框
        checkAndShowSubtitlePrompt(sentence);

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

    // 添加消息到聊天界面
    function appendMessage(text, sender, isNewMessage = true) {
        function isMergeMessagesEnabled() {
            if (typeof window.mergeMessagesEnabled !== 'undefined') return window.mergeMessagesEnabled;
            return mergeMessagesEnabled;
        }

        function normalizeGeminiText(s) {
            return (s || '').replace(/\r\n/g, '\n');
        }

        function splitIntoSentences(buffer) {
            // 逐字符扫描，尽量兼容中英文标点与流式输入
            const sentences = [];
            const s = normalizeGeminiText(buffer);
            let start = 0;

            const isBoundary = (ch, next) => {
                if (ch === '\n') return true;
                if (ch === '。' || ch === '！' || ch === '？') return true;
                if (ch === '!' || ch === '?') return true;
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
            }
            const prev = typeof window._realisticGeminiBuffer === 'string' ? window._realisticGeminiBuffer : '';
            const combined = prev + normalizeGeminiText(text);
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
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('message', 'gemini');
            messageDiv.textContent = "[" + getCurrentTimeString() + "] 🎀 " + (text || '');
            chatContainer.appendChild(messageDiv);
            window.currentGeminiMessage = messageDiv;
            // ========== 新增：追踪本轮气泡 ==========
            window.currentTurnGeminiBubbles.push(messageDiv);
            // ========== 追踪结束 ==========

            checkAndShowSubtitlePrompt(text);

            if (isFirstAIResponse) {
                isFirstAIResponse = false;
                console.log(window.t('console.aiFirstReplyDetected'));
                checkAndUnlockFirstDialogueAchievement();
            }
        } else if (sender === 'gemini' && isMergeMessagesEnabled() && !isNewMessage && window.currentGeminiMessage &&
            window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
            window.currentGeminiMessage.isConnected) {
            // 追加到现有消息（使用 textContent 避免 XSS 风险）
            window.currentGeminiMessage.textContent += text;

            // 防抖机制优化流式输出时的语言检测
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

                const fullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
                if (fullText && fullText.trim()) {
                    if (userLanguage === null) {
                        getUserLanguage().then(() => {
                            if (window.currentGeminiMessage &&
                                window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
                                window.currentGeminiMessage.isConnected) {
                                const detectedLang = detectLanguage(fullText);
                                if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                                    showSubtitlePrompt();
                                }
                            }
                        }).catch(err => {
                            console.warn(window.t('console.getUserLanguageFailedStream'), err);
                        });
                    } else {
                        const detectedLang = detectLanguage(fullText);
                        if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                            showSubtitlePrompt();
                        }
                    }
                }
                subtitleCheckDebounceTimer = null;
            }, 300);
        } else {
            // 创建新消息
            const messageDiv = document.createElement('div');
            messageDiv.classList.add('message', sender);

            // 根据sender设置不同的图标
            const icon = sender === 'user' ? '💬' : '🎀';
            messageDiv.textContent = "[" + getCurrentTimeString() + "] " + icon + " " + text;
            chatContainer.appendChild(messageDiv);

            // 如果是Gemini消息，更新当前消息引用
            if (sender === 'gemini') {
                window.currentGeminiMessage = messageDiv;
                // ========== 新增：追踪本轮气泡 ==========
                window.currentTurnGeminiBubbles.push(messageDiv);
                // ========== 追踪结束 ==========

                // 检测AI消息的语言，如果与用户语言不同，显示字幕提示框
                checkAndShowSubtitlePrompt(text);
                
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
                    if (shouldRestartProactiveVision) {
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

                // 如果主动搭话/主动视觉已启用，重置并开始定时
                if (proactiveChatEnabled || proactiveVisionEnabled) {
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

    // 保存选择的麦克风到服务器
    async function saveSelectedMicrophone(deviceId) {
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

    // 加载上次选择的麦克风
    async function loadSelectedMicrophone() {
        try {
            const response = await fetch('/api/characters/get_microphone');
            if (response.ok) {
                const data = await response.json();
                selectedMicrophoneId = data.microphone_id || null;
            }
        } catch (err) {
            console.error(window.t('console.loadMicrophoneSelectionFailed'), err);
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
    window.setMicrophoneGain = function(gainDb) {
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
    window.getMicrophoneGain = function() {
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
    window.setSpeakerVolume = function(vol) {
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
    window.getSpeakerVolume = function() {
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
                    cachedStatus.style.color = '#888';
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
                volumeStatus.style.color = '#888';
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
        if (proactiveChatEnabled || proactiveVisionEnabled) {
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
                    const selectedSourceId = window.getSelectedScreenSourceId ? window.getSelectedScreenSourceId() : null;
                    
                    if (selectedSourceId && window.electronDesktopCapturer) {
                        // 在Electron中使用选中的特定屏幕/窗口源
                        // 使用 chromeMediaSourceId 约束来指定源
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
                        console.log(window.t('console.screenShareUsingSource'), selectedSourceId);
                    } else {
                        // 使用标准的getDisplayMedia（显示系统选择器）
                        screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
                            video: {
                                cursor: 'always',
                                frameRate: 1,
                            },
                            audio: false,
                        });
                    }
                }
            }

            // 更新最后使用时间并调度闲置检查
            screenCaptureStreamLastUsed = Date.now();
            scheduleScreenCaptureIdleCheck();

            startScreenVideoStreaming(screenCaptureStream, isMobile() ? 'camera' : 'screen');

            micButton.disabled = true;
            muteButton.disabled = false;
            screenButton.disabled = true;
            stopButton.disabled = false;
            resetSessionButton.disabled = false;

            // 添加active类以保持激活状态的颜色
            screenButton.classList.add('active');
            syncFloatingScreenButtonState(true);

            // 手动开始屏幕共享时，重置/停止语音期间的主动视觉定时，避免双重触发
            try {
                stopProactiveVisionDuringSpeech();
            } catch (e) {
                console.warn(window.t('console.stopVoiceActiveVisionFailed'), e);
            }

            // 当用户停止共享屏幕时
            screenCaptureStream.getVideoTracks()[0].onended = () => {
                stopScreening();
                screenButton.classList.remove('active');
                syncFloatingScreenButtonState(false);
            };

            // 获取麦克风流
            if (!isRecording) showStatusToast(window.t ? window.t('app.micNotOpen') : '没开麦啊喂！', 3000);
        } catch (err) {
            console.error(isMobile() ? window.t('console.cameraAccessFailed') : window.t('console.screenShareFailed'), err);
            console.error(window.t('console.startupFailed'), err);
            let hint = '';
            switch (err.name) {
                case 'NotAllowedError':
                    hint = '请检查 iOS 设置 → Safari → 摄像头 权限是否为"允许"';
                    break;
                case 'NotFoundError':
                    hint = '未检测到摄像头设备';
                    break;
                case 'NotReadableError':
                case 'AbortError':
                    hint = '摄像头被其它应用占用？关闭扫码/拍照应用后重试';
                    break;
            }
            showStatusToast(`${err.name}: ${err.message}${hint ? `\n${hint}` : ''}`, 5000);
        }
    }

    async function stopScreenSharing() { // 停止共享，按钮on click
        stopScreening();
        
        // 停止所有 tracks 并清除回调，防止隐私/资源泄漏
        try {
            if (screenCaptureStream instanceof MediaStream) {
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
        
        micButton.disabled = true;
        muteButton.disabled = false;
        screenButton.disabled = false;
        stopButton.disabled = true;
        resetSessionButton.disabled = false;
        showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);

        // 移除active类
        screenButton.classList.remove('active');
        syncFloatingScreenButtonState(false);

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
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
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
                        transform: translate(-50%, -50%) scale(0.8);
                    }
                    to {
                        opacity: 1;
                        transform: translate(-50%, -50%) scale(1);
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
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
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
        if (window.live2dManager && window.live2dManager._floatingButtons && window.live2dManager._floatingButtons.mic) {
            const floatingMicBtn = window.live2dManager._floatingButtons.mic.button;
            if (floatingMicBtn) {
                floatingMicBtn.dataset.active = isActive ? 'true' : 'false';
                const imgOff = window.live2dManager._floatingButtons.mic.imgOff;
                const imgOn = window.live2dManager._floatingButtons.mic.imgOn;
                if (imgOff && imgOn) {
                    imgOff.style.opacity = isActive ? '0' : '1';
                    imgOn.style.opacity = isActive ? '1' : '0';
                }
            }
        }
    }

    // 同步浮动屏幕分享按钮状态的辅助函数
    function syncFloatingScreenButtonState(isActive) {
        if (window.live2dManager && window.live2dManager._floatingButtons && window.live2dManager._floatingButtons.screen) {
            const floatingScreenBtn = window.live2dManager._floatingButtons.screen.button;
            if (floatingScreenBtn) {
                floatingScreenBtn.dataset.active = isActive ? 'true' : 'false';
                const imgOff = window.live2dManager._floatingButtons.screen.imgOff;
                const imgOn = window.live2dManager._floatingButtons.screen.imgOn;
                if (imgOff && imgOn) {
                    imgOff.style.opacity = isActive ? '0' : '1';
                    imgOn.style.opacity = isActive ? '1' : '0';
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
            let timeoutId = null; // 在外部作用域定义，以便清除
            const sessionStartPromise = new Promise((resolve, reject) => {
                sessionStartedResolver = resolve;
                sessionStartedRejecter = reject; // 保存 reject 函数

                // 清除之前的超时定时器（如果存在）
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                }
            });

            // 发送start session事件
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'start_session',
                    input_type: 'audio'
                }));
                
                // 设置超时（10秒），如果超时则拒绝
                timeoutId = setTimeout(() => {
                    if (sessionStartedRejecter) {
                        const rejecter = sessionStartedRejecter;
                        sessionStartedResolver = null; // 先清除，防止重复触发
                        sessionStartedRejecter = null; // 同时清理 rejecter
                        window.sessionTimeoutId = null; // 清除全局定时器ID
                        
                        // 超时时向后端发送 end_session 消息
                        if (socket.readyState === WebSocket.OPEN) {
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
                }, 10000); // 10秒超时

                // 保存到全局变量，以便在 session_started 事件中清除
                window.sessionTimeoutId = timeoutId;
            } else {
                // WebSocket未连接，清除超时定时器和状态
                if (timeoutId) {
                    clearTimeout(timeoutId);
                }
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
                throw new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接');
            }

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
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
            } catch (error) {
                // 超时或错误时清除超时定时器
                if (timeoutId) {
                    clearTimeout(timeoutId);
                    timeoutId = null;
                }
                throw error; // 重新抛出错误，让外层 catch 处理
            }

            // 启动语音期间的主动视觉定时（如果已开启主动视觉）
            try {
                if (proactiveVisionEnabled) {
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
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'end_session'
                }));
                console.log(window.t('console.sessionStartFailedEndSession'));
            }

            // 隐藏准备提示
            hideVoicePreparingToast();

            // 失败时：移除激活状态（按钮变暗），恢复按钮（允许再次点击）
            micButton.classList.remove('active');

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
            if (proactiveChatEnabled || proactiveVisionEnabled) {
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
            showVoicePreparingToast(window.t ? window.t('app.textSystemPreparing') : '文本系统准备中，请稍候...');

            // 创建一个 Promise 来等待 session_started 消息（复用已有模式）
            let timeoutId = null;
            const sessionStartPromise = new Promise((resolve, reject) => {
                sessionStartedResolver = resolve;
                sessionStartedRejecter = reject; //  保存 reject 函数

                // 清除之前的超时定时器（如果存在）
                if (window.sessionTimeoutId) {
                    clearTimeout(window.sessionTimeoutId);
                }

                // 设置超时（15秒），如果超时则拒绝
                timeoutId = setTimeout(() => {
                    if (sessionStartedRejecter) {
                        const rejecter = sessionStartedRejecter;
                        sessionStartedResolver = null; // 先清除，防止重复触发
                        sessionStartedRejecter = null; //  同时清理 rejecter
                        window.sessionTimeoutId = null; // 清除全局定时器ID
                        
                        // 超时时向后端发送 end_session 消息
                        if (socket.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({
                                action: 'end_session'
                            }));
                            console.log(window.t('console.returnSessionTimeoutEndSession'));
                        }
                        
                        rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                    }
                }, 15000); // 15秒超时

                // 保存到全局变量，以便在 session_started 事件中清除
                window.sessionTimeoutId = timeoutId;
            });

            // 启动文本session
            if (socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'start_session',
                    input_type: 'text',
                    new_session: true
                }));
            } else {
                // WebSocket未连接，清除超时定时器和状态
                if (timeoutId) {
                    clearTimeout(timeoutId);
                }
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
                hideVoicePreparingToast();
                throw new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接');
            }

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
                if (chatContentWrapper) {
                    chatContentWrapper.style.display = '';
                }
                if (chatHeader) {
                    chatHeader.style.display = '';
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
            if (proactiveChatEnabled || proactiveVisionEnabled) {
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
                    sessionStartedRejecter = reject; // 保存 reject 函数

                    // 设置超时（15秒），如果超时则拒绝
                    setTimeout(() => {
                        if (sessionStartedRejecter) {
                            const rejecter = sessionStartedRejecter;
                            sessionStartedResolver = null;
                            sessionStartedRejecter = null; // 同时清理 rejecter
                            rejecter(new Error(window.t ? window.t('app.sessionTimeout') : 'Session启动超时'));
                        }
                    }, 15000);
                });

                // 启动文本session
                if (socket.readyState === WebSocket.OPEN) {
                    socket.send(JSON.stringify({
                        action: 'start_session',
                        input_type: 'text',
                        new_session: false
                    }));
                } else {
                    throw new Error(window.t ? window.t('app.websocketNotConnectedError') : 'WebSocket未连接');
                }

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
            if (proactiveChatEnabled || proactiveVisionEnabled) {
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
            // 临时禁用截图按钮，防止重复点击
            screenshotButton.disabled = true;
            showStatusToast(window.t ? window.t('app.capturing') : '正在截图...', 2000);

            // 获取屏幕或摄像头流
            if (isMobile()) {
                // 移动端使用摄像头
                captureStream = await getMobileCameraStream();
            } else {
                // API 兼容性检测（桌面端）
                if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
                    throw new Error('UNSUPPORTED_API');
                }
                // 桌面端使用屏幕共享
                captureStream = await navigator.mediaDevices.getDisplayMedia({
                    video: {
                        cursor: 'always',
                    },
                    audio: false,
                });
            }

            // 创建video元素来加载流
            const video = document.createElement('video');
            video.srcObject = captureStream;
            video.autoplay = true;
            video.muted = true;

            // 等待视频加载完成
            await video.play();

            // 使用统一的截图辅助函数进行截取
            const { dataUrl, width, height } = captureCanvasFrame(video);

            // 清理 video 元素释放资源
            video.srcObject = null;
            video.remove();

            console.log(window.t('console.screenshotSuccess'), `${width}x${height}`);

            // 添加截图到待发送列表（不立即发送）
            addScreenshotToList(dataUrl);

            showStatusToast(window.t ? window.t('app.screenshotAdded') : '截图已添加，点击发送一起发送', 3000);

        } catch (err) {
            console.error(window.t('console.screenshotFailed'), err);

            // 根据错误类型显示不同提示
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
            // 确保流被正确关闭，防止资源泄漏
            if (captureStream instanceof MediaStream) {
                captureStream.getTracks().forEach(track => track.stop());
            }
            // 重新启用截图按钮
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

        stopScreening();
        if (!isRecording) return;

        isRecording = false;
        window.isRecording = false;
        window.currentGeminiMessage = null;
        
        // 重置语音模式用户转录合并追踪
        lastVoiceUserMessage = null;
        lastVoiceUserMessageTime = 0;

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
        // 停止所有计划的音频源
        scheduledSources.forEach(source => {
            try {
                source.stop();
            } catch (e) {
                // 忽略已经停止的源
            }
        });

        // 清空队列和计划源列表
        scheduledSources = [];
        audioBufferQueue = [];
        isPlaying = false;
        audioStartTime = 0;
        nextStartTime = 0; // 新增：重置预调度时间

        // 重置 OGG OPUS 流式解码器（等待重置完成，避免竞态条件）
        await resetOggOpusDecoder();
    }

    // 清空音频队列但不重置解码器（用于精确打断控制）
    // 解码器将在收到新 speech_id 的第一个音频包时才重置
    function clearAudioQueueWithoutDecoderReset() {
        // 停止所有计划的音频源
        scheduledSources.forEach(source => {
            try {
                source.stop();
            } catch (e) {
                // 忽略已经停止的源
            }
        });

        // 清空队列和计划源列表
        scheduledSources = [];
        audioBufferQueue = [];
        isPlaying = false;
        audioStartTime = 0;
        nextStartTime = 0;

        // 注意：不调用 resetOggOpusDecoder()！
        // 解码器将在收到新 speech_id 时才重置，避免丢失头信息
    }


    function scheduleAudioChunks() {
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
    }


    async function handleAudioBlob(blob) {
        // 精确打断控制：检查是否应跳过此音频（属于被打断的旧音频）
        if (skipNextAudioBlob) {
            console.log('跳过被打断的音频 blob');
            return;
        }

        const arrayBuffer = await blob.arrayBuffer();
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

        // 如果是第一次，初始化调度
        if (!isPlaying) {
            nextChunkTime = audioPlayerContext.currentTime + 0.1;
            isPlaying = true;
            scheduleAudioChunks(); // 开始调度循环
        } else {
            // 若已经在播放，立即尝试补调度，避免卡住
            setTimeout(() => {
                try {
                    scheduleAudioChunks();
                } catch (e) {
                    // 静默兜底，避免控制台噪声
                }
            }, 0);
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

    function startLipSync(model, analyser) {
        console.log('[LipSync] 开始口型同步', { hasModel: !!model, hasAnalyser: !!analyser });
        if (animationFrameId) {
            cancelAnimationFrame(animationFrameId);
        }
        
        // 重置平滑状态
        _lastMouthOpen = 0;
        
        // 使用时域数据计算 RMS，对干声足够了
        const dataArray = new Uint8Array(analyser.fftSize);

        function animate() {
            if (!analyser) return;
            
            analyser.getByteTimeDomainData(dataArray);
            
            // 计算 RMS
            let sum = 0;
            for (let i = 0; i < dataArray.length; i++) {
                const val = (dataArray[i] - 128) / 128; // 归一化到 -1~1
                sum += val * val;
            }
            const rms = Math.sqrt(sum / dataArray.length);
            
            // 映射到 0~1
            let mouthOpen = Math.min(1, rms * 10);
            
            
            // 柔化处理：大幅增加平滑度，让动作更“肉”一点，避免快速开合
            mouthOpen = _lastMouthOpen * 0.5 + mouthOpen * 0.5;
            _lastMouthOpen = mouthOpen;

            
            if (window.LanLan1 && typeof window.LanLan1.setMouth === 'function') {
                window.LanLan1.setMouth(mouthOpen);
            }

            animationFrameId = requestAnimationFrame(animate);
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

        // 首先清除任何可能干扰动画的强制显示样式
        container.style.removeProperty('visibility');
        container.style.removeProperty('display');
        container.style.removeProperty('opacity');

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

        // 强制显示live2d容器
        container.classList.remove('hidden'); // 先移除hidden类
        container.classList.remove('minimized'); // 移除minimized类
        container.style.visibility = 'visible';
        container.style.display = 'block';
        container.style.opacity = '1';

        // 强制浏览器重新计算样式，确保过渡效果正常
        void container.offsetWidth;

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
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.style.display = 'block';
                    vrmContainer.style.visibility = 'visible';
                    vrmContainer.style.removeProperty('pointer-events');
                    console.log('[showCurrentModel] 已设置vrmContainer可见');
                }

                // 恢复 VRM canvas 的可见性
                const vrmCanvas = document.getElementById('vrm-canvas');
                console.log('[showCurrentModel] vrmCanvas存在:', !!vrmCanvas);
                if (vrmCanvas) {
                    vrmCanvas.style.removeProperty('visibility');
                    vrmCanvas.style.removeProperty('pointer-events');
                    vrmCanvas.style.visibility = 'visible';
                    vrmCanvas.style.pointerEvents = 'auto';
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

                // 显示VRM浮动按钮（与 showLive2d 保持一致的处理方式）
                if (vrmFloatingButtons) {
                    vrmFloatingButtons.style.setProperty('display', 'flex', 'important');
                    vrmFloatingButtons.style.setProperty('visibility', 'visible', 'important');
                    vrmFloatingButtons.style.setProperty('opacity', '1', 'important');
                }

                //  显示VRM锁图标（与 showLive2d 保持一致的处理方式）
                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    vrmLockIcon.style.removeProperty('display');
                    vrmLockIcon.style.removeProperty('visibility');
                    vrmLockIcon.style.removeProperty('opacity');
                    vrmLockIcon.style.display = 'block';
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

    // 麦克风按钮（toggle模式）
    // 麦克风按钮（toggle模式）
    window.addEventListener('live2d-mic-toggle', async (e) => {
        if (e.detail.active) {
            // 想要开启语音：如果已经在录音，直接返回
            if (window.isRecording) {
                return;
            }
            // 开始语音
            if (typeof startMicCapture === 'function') {
                await startMicCapture();
            } else {
                console.error('startMicCapture function not found');
            }
        } else {
            // 想要关闭语音
            // 如果已经停止录音，直接返回
            if (!window.isRecording) {
                return;
            }
            // 关闭语音
            if (typeof stopMicCapture === 'function') {
                await stopMicCapture();
            } else {
                console.error('stopMicCapture function not found');
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

        // 隐藏 Live2D canvas，使 Electron 的 alpha 检测认为该区域完全透明
        // 仅设置 pointer-events: none 不够，因为 Electron 根据像素 alpha 值来决定事件转发
        // 必须设置 visibility: hidden 来确保 canvas 不渲染任何像素
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.setProperty('visibility', 'hidden', 'important');
            live2dCanvas.style.setProperty('pointer-events', 'none', 'important');
            console.log('[App] 已隐藏 live2d-canvas（visibility: hidden），Electron 将认为该区域透明');
        }

        // 【关键修复】在隐藏按钮之前，先判断当前激活的模型类型
        // 通过检查容器的可见性来判断，而不是按钮的可见性（因为按钮即将被隐藏）
        const vrmContainer = document.getElementById('vrm-container');
        const live2dContainer = document.getElementById('live2d-container');
        const isVrmActive = vrmContainer &&
                           vrmContainer.style.display !== 'none' &&
                           !vrmContainer.classList.contains('hidden');
        console.log('[App] 判断当前模型类型 - isVrmActive:', isVrmActive);

        // 隐藏 VRM 容器和 canvas
        if (vrmContainer) {
            vrmContainer.style.setProperty('visibility', 'hidden', 'important');
            vrmContainer.style.setProperty('pointer-events', 'none', 'important');
            vrmContainer.style.setProperty('display', 'none', 'important');
            console.log('[App] 已隐藏 vrm-container（visibility: hidden），Electron 将认为该区域透明');
        }
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas) {
            vrmCanvas.style.setProperty('visibility', 'hidden', 'important');
            vrmCanvas.style.setProperty('pointer-events', 'none', 'important');
            console.log('[App] 已隐藏 vrm-canvas（visibility: hidden）');
        }

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
            
            // 移动端还需要隐藏内容区
            if (isMobile) {
                const chatContentWrapper = document.getElementById('chat-content-wrapper');
                const chatHeader = document.getElementById('chat-header');
                if (chatContentWrapper) chatContentWrapper.style.display = 'none';
                if (chatHeader) chatHeader.style.display = 'none';
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
        const vrmCanvas = document.getElementById('vrm-canvas');
        if (vrmCanvas) {
            vrmCanvas.style.removeProperty('visibility');
            vrmCanvas.style.removeProperty('pointer-events');
            vrmCanvas.style.visibility = 'visible';
            console.log('[App] 已恢复 vrm-canvas 的可见性');
        }

        // 【关键修复】恢复 Live2D canvas 的可见性（如果存在）
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
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
            vrmLockIcon.style.display = 'block';
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
        
        // 恢复VRM的浮动按钮系统（使用 !important 强制显示，覆盖之前的隐藏样式）
        const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
        if (vrmFloatingButtons) {
            // 先清除所有可能的隐藏样式
            vrmFloatingButtons.style.removeProperty('display');
            vrmFloatingButtons.style.removeProperty('visibility');
            vrmFloatingButtons.style.removeProperty('opacity');

            // 使用 !important 强制显示，确保覆盖之前的隐藏样式（与Live2D保持一致）
            vrmFloatingButtons.style.setProperty('display', 'flex', 'important');
            vrmFloatingButtons.style.setProperty('visibility', 'visible', 'important');
            vrmFloatingButtons.style.setProperty('opacity', '1', 'important');
            
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
                if (chatContentWrapper) chatContentWrapper.style.removeProperty('display');
                if (chatHeader) chatHeader.style.removeProperty('display');
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

        // 根据状态更新所有按钮UI
        _updateUI() {
            const master = document.getElementById('live2d-agent-master');
            const keyboard = document.getElementById('live2d-agent-keyboard');
            const mcp = document.getElementById('live2d-agent-mcp');
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
                    if (mcp) { mcp.disabled = true; mcp.checked = false; mcp.title = ''; syncUI(mcp); }
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
                    if (mcp) {
                        mcp.disabled = true;
                        mcp.title = window.t ? window.t('settings.toggles.checking') : '查询中...';
                        syncUI(mcp);
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
                    if (mcp) { mcp.disabled = true; mcp.checked = false; syncUI(mcp); }
                    if (status) status.textContent = window.t ? window.t('settings.toggles.serverOffline') : 'Agent服务器未启动';
                    if (userPlugin) { userPlugin.disabled = true; userPlugin.checked = false; syncUI(userPlugin); }
                    break;

                case AgentPopupState.PROCESSING:
                    // 处理中：所有按钮禁用，防止重复操作
                    if (master) { master.disabled = true; syncUI(master); }
                    if (keyboard) { keyboard.disabled = true; syncUI(keyboard); }
                    if (mcp) { mcp.disabled = true; syncUI(mcp); }
                    if (userPlugin) { userPlugin.disabled = true; syncUI(userPlugin); }
                    break;
            }
        }
    };

    // 暴露状态机给外部使用
    window.agentStateMachine = agentStateMachine;

    // Agent 定时检查器（暴露到 window 供 live2d-ui-hud.js 调用）
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
        const agentMcpCheckbox = document.getElementById('live2d-agent-mcp');
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
            { id: 'live2d-agent-mcp', capability: 'mcp', flagKey: 'mcp_enabled', nameKey: 'mcpTools' },
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
                        // 处理后端推送的通知（如果有）
                        const notification = data.notification;
                        if (notification) {
                            console.log('[App] 收到后端通知:', notification);
                            setFloatingAgentStatus(notification);
                            // 如果是错误通知，也可以考虑弹窗
                            if (notification.includes('失败') || notification.includes('断开') || notification.includes('错误')) {
                                showStatusToast(notification, 3000);
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
                            [agentKeyboardCheckbox, agentMcpCheckbox, agentUserPluginCheckbox].forEach(cb => {
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

                        if (agentMcpCheckbox && !agentMcpCheckbox._processing) {
                            const flagEnabled = flags.mcp_enabled || false;
                            const isAvailable = capabilityCheckFailed ? agentMcpCheckbox.checked : (capabilityResults['mcp_enabled'] !== false);
                            const shouldBeChecked = flagEnabled && isAvailable;

                            if (agentMcpCheckbox.checked !== shouldBeChecked) {
                                if (shouldBeChecked) {
                                    agentMcpCheckbox.checked = true;
                                    agentMcpCheckbox._autoDisabled = true;
                                    agentMcpCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentMcpCheckbox._autoDisabled = false;
                                    if (typeof agentMcpCheckbox._updateStyle === 'function') agentMcpCheckbox._updateStyle();
                                } else if (!flagEnabled) {
                                    agentMcpCheckbox.checked = false;
                                    agentMcpCheckbox._autoDisabled = true;
                                    agentMcpCheckbox.dispatchEvent(new Event('change', { bubbles: true }));
                                    agentMcpCheckbox._autoDisabled = false;
                                    if (typeof agentMcpCheckbox._updateStyle === 'function') agentMcpCheckbox._updateStyle();
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

                [agentKeyboardCheckbox, agentMcpCheckbox, agentUserPluginCheckbox].forEach(cb => {
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
        // 清除之前的定时器
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
        }

        // 重置 flags 同步时间，确保立即同步一次
        lastFlagsSyncTime = 0;
        // 重置连接失败计数
        connectionFailureCount = 0;

        // 立即检查一次
        checkAgentCapabilities();

        // 每1秒检查一次
        agentCheckInterval = setInterval(checkAgentCapabilities, 1000);
    };

    // 停止 Agent 可用性定时检查（由 Agent 总开关关闭时调用）
    window.stopAgentAvailabilityCheck = function () {
        if (agentCheckInterval) {
            clearInterval(agentCheckInterval);
            agentCheckInterval = null;
        }
    };

    // 浮动Agent status更新函数
    function setFloatingAgentStatus(msg) {
        const statusEl = document.getElementById('live2d-agent-status');
        if (statusEl) {
            statusEl.textContent = msg || '';
        }
    }

    // 检查Agent服务器健康状态
    async function checkToolServerHealth() {
        try {
            const resp = await fetch(`/api/agent/health`);
            if (!resp.ok) throw new Error('not ok');
            return true;
        } catch (e) {
            return false;
        }
    }

    // 检查Agent能力
    async function checkCapability(kind, showError = true) {
        const apis = {
            computer_use: { url: '/api/agent/computer_use/availability', nameKey: 'keyboardControl' },
            mcp: { url: '/api/agent/mcp/availability', nameKey: 'mcpTools' },
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
        const agentMasterCheckbox = document.getElementById('live2d-agent-master');
        const agentKeyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const agentMcpCheckbox = document.getElementById('live2d-agent-mcp');
        const agentUserPluginCheckbox = document.getElementById('live2d-agent-user-plugin');

        if (!agentMasterCheckbox) {
            console.warn('[App] Agent开关元素未找到，跳过绑定');
            return;
        }

        console.log('[App] Agent开关元素已找到，开始绑定事件监听器');

        // 【状态机】操作序列号由状态机管理，子开关保留独立序列号
        let keyboardOperationSeq = 0;
        let mcpOperationSeq = 0;
        let userPluginOperationSeq = 0;

        // 标记这些 checkbox 有外部处理器
        agentMasterCheckbox._hasExternalHandler = true;
        if (agentKeyboardCheckbox) agentKeyboardCheckbox._hasExternalHandler = true;
        if (agentMcpCheckbox) agentMcpCheckbox._hasExternalHandler = true;
        if (agentUserPluginCheckbox) agentUserPluginCheckbox._hasExternalHandler = true;


        // 辅助函数：同步更新 checkbox 的 UI 样式
        const syncCheckboxUI = (checkbox) => {
            if (checkbox && typeof checkbox._updateStyle === 'function') {
                checkbox._updateStyle();
            }
        };

        // 辅助函数：重置子开关状态和 UI
        const resetSubCheckboxes = () => {
            const names = {
                'live2d-agent-keyboard': window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制',
                'live2d-agent-mcp': window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具',
                'live2d-agent-user-plugin': window.t ? window.t('settings.toggles.userPlugin') : '用户插件'
            };
            [agentKeyboardCheckbox, agentMcpCheckbox, agentUserPluginCheckbox].forEach(cb => {
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
                    if (agentMcpCheckbox) {
                        agentMcpCheckbox.disabled = true;
                        agentMcpCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                        syncCheckboxUI(agentMcpCheckbox);
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
                            if (!agentMcpCheckbox) return;
                            const available = await checkCapability('mcp', false);
                            // 【防竞态】检查操作序列号和总开关状态
                            if (isExpired() || !agentMasterCheckbox.checked) {
                                agentMcpCheckbox.disabled = true;
                                agentMcpCheckbox.checked = false;
                                syncCheckboxUI(agentMcpCheckbox);
                                return;
                            }
                            agentMcpCheckbox.disabled = !available;
                            agentMcpCheckbox.title = available ? (window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.mcpTools') }) : 'MCP工具不可用');
                            syncCheckboxUI(agentMcpCheckbox);

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
                                flags: { agent_enabled: true, computer_use_enabled: false, mcp_enabled: false, user_plugin_enabled: false }
                            })
                        });
                        if (!r.ok) throw new Error('main_server rejected');

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
                                flags: { agent_enabled: false, computer_use_enabled: false, mcp_enabled: false, user_plugin_enabled: false }
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

        // MCP工具开关逻辑（传入序列号的getter和setter）
        setupSubCheckbox(
            agentMcpCheckbox,
            'mcp',
            'mcp_enabled',
            'mcpTools',
            () => mcpOperationSeq,
            () => ++mcpOperationSeq
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
                // 同步 用户插件子开关
                if (agentUserPluginCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，根据后端状态设置
                        agentUserPluginCheckbox.checked = flags.user_plugin_enabled || false;
                        agentUserPluginCheckbox.disabled = true; // 先设为可用，后续可用性检查会更新
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                    } else {
                        // Agent 未开启，复位子开关
                        agentUserPluginCheckbox.checked = false;
                        agentUserPluginCheckbox.disabled = true;
                        agentUserPluginCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.userPlugin') : '用户插件' }) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentUserPluginCheckbox);
                }
                if (agentMcpCheckbox) {
                    if (analyzerEnabled) {
                        // Agent 已开启，但子开关保持禁用等待能力检查
                        agentMcpCheckbox.checked = false;
                        agentMcpCheckbox.disabled = true;
                        agentMcpCheckbox.title = window.t ? window.t('settings.toggles.checking') : '检查中...';
                    } else {
                        // Agent 未开启，复位子开关
                        agentMcpCheckbox.checked = false;
                        agentMcpCheckbox.disabled = true;
                        agentMcpCheckbox.title = window.t ? window.t('settings.toggles.masterRequired', { name: window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具' }) : '请先开启Agent总开关';
                    }
                    syncCheckboxUI(agentMcpCheckbox);
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
            [agentKeyboardCheckbox, agentMcpCheckbox, agentUserPluginCheckbox].forEach(cb => {
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
                const [healthOk, flagsData, keyboardAvailable, mcpAvailable, userPluginAvailable] = await Promise.all([
                    checkToolServerHealth(),
                    fetch('/api/agent/flags').then(r => r.ok ? r.json() : { success: false }),
                    checkCapability('computer_use', false),
                    checkCapability('mcp', false),
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

                        // MCP工具
                        if (agentMcpCheckbox) {
                            const shouldEnable = flags.mcp_enabled && mcpAvailable;
                            agentMcpCheckbox.checked = shouldEnable;
                            agentMcpCheckbox.disabled = !mcpAvailable; // 仅当能力不可用时禁用
                            agentMcpCheckbox.title = mcpAvailable ? (window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具') : (window.t ? window.t('settings.toggles.unavailable', { name: window.t('settings.toggles.mcpTools') }) : 'MCP工具不可用');
                            syncCheckboxUI(agentMcpCheckbox);
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
                        if (flags.computer_use_enabled || flags.mcp_enabled || flags.user_plugin_enabled) {
                            console.log('[App] 总开关关闭但检测到子flag开启，强制同步关闭');
                            fetch('/api/agent/flags', {
                                method: 'POST',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({
                                    lanlan_name: lanlan_config.lanlan_name,
                                    flags: { agent_enabled: false, computer_use_enabled: false, mcp_enabled: false, user_plugin_enabled: false }
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
        if (agentTaskPollingInterval) return; // 已经在运行

        console.log('[App] 启动 Agent 任务状态轮询');

        // 确保 HUD 已创建并显示
        if (window.live2dManager) {
            window.live2dManager.createAgentTaskHUD();
            window.live2dManager.showAgentTaskHUD();
        }

        // 立即执行一次
        fetchAndUpdateTaskStatus();

        // 每 2 秒轮询一次任务状态
        agentTaskPollingInterval = setInterval(fetchAndUpdateTaskStatus, 2000);

        // 每秒更新运行时间显示
        agentTaskTimeUpdateInterval = setInterval(updateTaskRunningTimes, 1000);
    };

    // 停止任务状态轮询
    window.stopAgentTaskPolling = function () {
        console.log('[App] 停止 Agent 任务状态轮询');

        if (agentTaskPollingInterval) {
            clearInterval(agentTaskPollingInterval);
            agentTaskPollingInterval = null;
        }

        if (agentTaskTimeUpdateInterval) {
            clearInterval(agentTaskTimeUpdateInterval);
            agentTaskTimeUpdateInterval = null;
        }

        // 隐藏 HUD
        if (window.live2dManager) {
            window.live2dManager.hideAgentTaskHUD();
        }
    };

    // 获取并更新任务状态
    async function fetchAndUpdateTaskStatus() {
        try {
            const response = await fetch('/api/agent/task_status');
            if (!response.ok) {
                console.warn('[App] 获取任务状态失败:', response.status);
                return;
            }

            const data = await response.json();
            if (data.success && window.live2dManager) {
                window.live2dManager.updateAgentTaskHUD(data);
            }
        } catch (error) {
            console.warn('[App] 任务状态轮询出错:', error);
        }
    }

    // 更新运行中任务的时间显示
    function updateTaskRunningTimes() {
        const taskList = document.getElementById('agent-task-list');
        if (!taskList) return;

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

    // 检查是否需要显示任务 HUD（总开关开启 且 键鼠或MCP任一开启）
    function checkAndToggleTaskHUD() {
        const masterCheckbox = document.getElementById('live2d-agent-master');
        const keyboardCheckbox = document.getElementById('live2d-agent-keyboard');
        const mcpCheckbox = document.getElementById('live2d-agent-mcp');
        const userPlugin = document.getElementById('live2d-agent-user-plugin');

        const masterEnabled = masterCheckbox && masterCheckbox.checked;
        const keyboardEnabled = keyboardCheckbox && keyboardCheckbox.checked;
        const mcpEnabled = mcpCheckbox && mcpCheckbox.checked;
        const userPluginEnabled = userPlugin && userPlugin.checked;

        //  只有总开关开启 且 子开关任一开启时才显示HUD
        if (masterEnabled && (keyboardEnabled || mcpEnabled || userPluginEnabled)) {
            window.startAgentTaskPolling();
        } else {
            window.stopAgentTaskPolling();
        }
    }

    // 暴露给其他模块使用
    window.checkAndToggleTaskHUD = checkAndToggleTaskHUD;

    // 监听 Agent 子开关变化来控制 HUD 显示
    window.addEventListener('live2d-floating-buttons-ready', () => {
        // 延迟确保元素已创建
        setTimeout(() => {
            const keyboardCheckbox = document.getElementById('live2d-agent-keyboard');
            const mcpCheckbox = document.getElementById('live2d-agent-mcp');
            const userPluginCheckbox = document.getElementById('live2d-agent-user-plugin');

            if (keyboardCheckbox) {
                keyboardCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }
            if (mcpCheckbox) {
                mcpCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }
            if (userPluginCheckbox) {
                userPluginCheckbox.addEventListener('change', checkAndToggleTaskHUD);
            }
            console.log('[App] Agent 任务 HUD 控制已绑定');
        }, 100);
    });
    // Agent 任务 HUD 轮询逻辑结束

    // 监听浮动按钮创建完成事件
    window.addEventListener('live2d-floating-buttons-ready', () => {
        console.log('[App] 收到浮动按钮就绪事件，开始绑定Agent开关');
        setupAgentCheckboxListeners();
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
                noMicItem.style.color = '#666';
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
            speakerLabel.style.color = '#333';
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
                color: '#888',
                marginTop: '6px'
            });
            speakerContainer.appendChild(speakerHint);

            leftColumn.appendChild(speakerContainer);

            // 添加分隔线
            const speakerSeparator = document.createElement('div');
            speakerSeparator.style.height = '1px';
            speakerSeparator.style.backgroundColor = '#eee';
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
            gainLabel.style.color = '#333';
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
                color: '#888',
                marginTop: '6px'
            });
            gainContainer.appendChild(gainHint);

            leftColumn.appendChild(gainContainer);

            // 添加分隔线（音量可视化区域前）
            const volumeSeparator = document.createElement('div');
            volumeSeparator.style.height = '1px';
            volumeSeparator.style.backgroundColor = '#eee';
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
            volumeLabelText.style.color = '#333';
            volumeLabelText.style.fontWeight = '500';

            const volumeStatus = document.createElement('span');
            volumeStatus.id = 'mic-volume-status';
            volumeStatus.textContent = window.t ? window.t('microphone.volumeIdle') : '未录音';
            volumeStatus.style.fontSize = '11px';
            volumeStatus.style.color = '#888';

            volumeLabel.appendChild(volumeLabelText);
            volumeLabel.appendChild(volumeStatus);
            volumeContainer.appendChild(volumeLabel);

            // 音量条背景
            const volumeBarBg = document.createElement('div');
            volumeBarBg.id = 'mic-volume-bar-bg';
            Object.assign(volumeBarBg.style, {
                width: '100%',
                height: '8px',
                backgroundColor: '#e9ecef',
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
                color: '#888',
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
                borderBottom: '1px solid rgba(79, 140, 255, 0.15)',
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
                background: selectedMicrophoneId === null ? '#e6f0ff' : 'transparent',
                borderRadius: '6px',
                transition: 'background 0.2s ease',
                fontSize: '13px',
                width: '100%',
                textAlign: 'left',
                color: selectedMicrophoneId === null ? '#4f8cff' : '#333',
                fontWeight: selectedMicrophoneId === null ? '500' : '400'
            });
            defaultOption.addEventListener('mouseenter', () => {
                if (selectedMicrophoneId !== null) {
                    defaultOption.style.background = 'rgba(79, 140, 255, 0.1)';
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
            separator.style.backgroundColor = '#eee';
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
                    background: selectedMicrophoneId === device.deviceId ? '#e6f0ff' : 'transparent',
                    borderRadius: '6px',
                    transition: 'background 0.2s ease',
                    fontSize: '13px',
                    width: '100%',
                    textAlign: 'left',
                    color: selectedMicrophoneId === device.deviceId ? '#4f8cff' : '#333',
                    fontWeight: selectedMicrophoneId === device.deviceId ? '500' : '400'
                });

                option.addEventListener('mouseenter', () => {
                    if (selectedMicrophoneId !== device.deviceId) {
                        option.style.background = 'rgba(79, 140, 255, 0.1)';
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
                backgroundColor: '#eee',
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
                option.style.background = '#e6f0ff';
                option.style.color = '#4f8cff';
                option.style.fontWeight = '500';
            } else {
                option.classList.remove('selected');
                option.style.background = 'transparent';
                option.style.color = '#333';
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
                option.style.background = '#e6f0ff';
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
            notAvailableItem.style.color = '#666';
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
            loadingItem.style.color = '#666';
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
                noSourcesItem.style.color = '#666';
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
                    option.style.background = '#e6f0ff';
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
                        border: '1px solid #ddd',
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
                        background: '#f5f5f5',
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
                    color: '#333',
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
                        option.style.background = 'rgba(79, 140, 255, 0.1)';
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
                    color: '#555',
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
                    color: '#555',
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

    // 主动搭话定时触发功能
    function scheduleProactiveChat() {
        // 清除现有定时器
        if (proactiveChatTimer) {
            clearTimeout(proactiveChatTimer);
            proactiveChatTimer = null;
        }

        // 主动搭话关闭时跳过（定时主动搭话只需要proactiveChatEnabled）
        if (!proactiveChatEnabled) {
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

    async function triggerProactiveChat() {
        try {
            // 根据模式决定使用哪种搭话方式
            // Windows系统下支持三种模式：截图、窗口标题搜索、热门内容
            // 非Windows系统下只支持截图和热门内容
            let useScreenshot = false;
            let useWindowTitle = false;
            const isWindows = isWindowsOS();

            if (proactiveChatEnabled && proactiveVisionEnabled) {
                // 两个都开启时：
                // Windows: 1/3截图, 1/3窗口标题, 1/3热门内容
                // 非Windows: 50%截图, 50%热门内容
                if (isWindows) {
                    const rand = Math.random();
                    if (rand < 0.33) {
                        useScreenshot = true;
                        console.log('主动搭话模式：双开模式(Windows)，使用截图搭话');
                    } else if (rand < 0.66) {
                        useWindowTitle = true;
                        console.log('主动搭话模式：双开模式(Windows)，使用窗口标题搭话');
                    } else {
                        console.log('主动搭话模式：双开模式(Windows)，使用热门内容');
                    }
                } else {
                    useScreenshot = Math.random() < 0.5;
                    console.log(`主动搭话模式：双开模式，使用${useScreenshot ? '截图搭话' : '热门内容'}`);
                }
            } else if (proactiveVisionEnabled) {
                // 只开启主动视觉时：
                // Windows和非Windows都是100%截图
                useScreenshot = true;
                console.log('主动搭话模式：仅视觉模式，使用截图搭话');
            } else if (proactiveChatEnabled && isWindows) {
                // 只开启主动搭话时(Windows)：50%窗口标题, 50%热门内容
                if (Math.random() < 0.5) {
                    useWindowTitle = true;
                    console.log('主动搭话模式：仅搭话模式(Windows)，使用窗口标题搭话');
                } else {
                    console.log('主动搭话模式：仅搭话模式(Windows)，使用热门内容');
                }
            } else if (proactiveChatEnabled) {
                // 只开启主动搭话时(非Windows)：100%热门内容
                useScreenshot = false;
                useWindowTitle = false;
                console.log('主动搭话模式：仅搭话模式，使用热门内容');
            } else {
                // 两个都关闭，不执行搭话
                console.log('主动搭话模式：两个功能都关闭，跳过本次搭话');
                return;
            }

            let requestBody = {
                lanlan_name: lanlan_config.lanlan_name
            };

            if (useScreenshot) {
                // 使用截图搭话
                const screenshotDataUrl = await captureProactiveChatScreenshot();

                if (!screenshotDataUrl) {
                    console.log('主动搭话截图失败，退回使用其他方式');
                    // 截图失败时的回退策略
                    if (isWindows && proactiveChatEnabled) {
                        // Windows下回退到窗口标题
                        useScreenshot = false;
                        useWindowTitle = true;
                        console.log('已切换到窗口标题搭话模式');
                    } else if (proactiveChatEnabled) {
                        // 非Windows或不支持窗口标题时回退到热门内容
                        useScreenshot = false;
                        console.log('已切换到热门内容搭话模式');
                    } else {
                        // 如果只开启了主动视觉，没有开启主动搭话，则跳过本次搭话
                        console.log('主动视觉截图失败且未开启主动搭话，跳过本次搭话');
                        return;
                    }
                } else {
                    requestBody.screenshot_data = screenshotDataUrl;
                }
            }

            if (useWindowTitle && !useScreenshot) {
                // 使用窗口标题搭话（Windows only）
                try {
                    const titleResponse = await fetch('/api/get_window_title');
                    const titleResult = await titleResponse.json();

                    // await 期间用户可能关闭了功能，避免继续执行
                    if (!proactiveChatEnabled && !proactiveVisionEnabled) {
                        console.log('功能已关闭，取消本次搭话');
                        return;
                    }

                    if (titleResult.success && titleResult.window_title) {
                        requestBody.window_title = titleResult.window_title;
                        console.log('成功获取窗口标题:', titleResult.window_title);
                    } else {
                        console.log('获取窗口标题失败，退回使用热门内容');
                        if (proactiveChatEnabled) {
                            useWindowTitle = false;
                            console.log('已切换到热门内容搭话模式');
                        } else {
                            console.log('获取窗口标题失败且未开启主动搭话，跳过本次搭话');
                            return;
                        }
                    }
                } catch (error) {
                    console.error('获取窗口标题时出错:', error);
                    if (proactiveChatEnabled) {
                        useWindowTitle = false;
                        console.log('已切换到热门内容搭话模式');
                    } else {
                        console.log('获取窗口标题失败且未开启主动搭话，跳过本次搭话');
                        return;
                    }
                }
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
                    // 检测用户是否在20秒内有过输入
                    const timeSinceLastInput = Date.now() - lastUserInputTime;
                    if (timeSinceLastInput < 20000) {
                        console.log(`主动搭话作废：用户在${Math.round(timeSinceLastInput / 1000)}秒前有过输入`);
                        return;
                    }

                    console.log('主动搭话已发送:', result.message);
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

    function resetProactiveChatBackoff() {
        // 重置退避级别
        proactiveChatBackoffLevel = 0;
        // 重新安排定时器
        scheduleProactiveChat();
    }

    // 发送单帧屏幕数据（优先使用已存在的 screenCaptureStream，否则临时调用 getDisplayMedia）
    async function sendOneProactiveVisionFrame() {
        try {
            if (!socket || socket.readyState !== WebSocket.OPEN) return;

            let dataUrl = null;

            if (screenCaptureStream) {
                const video = document.createElement('video');
                video.srcObject = screenCaptureStream;
                video.autoplay = true;
                video.muted = true;
                try {
                    await video.play();
                } catch (e) {
                    // 某些情况下不需要 play() 成功也能读取帧
                }
                const frame = captureCanvasFrame(video, 0.8);
                dataUrl = frame && frame.dataUrl ? frame.dataUrl : null;
                // 清理 video 元素释放资源
                video.srcObject = null;
                video.remove();
            } else {
                // 临时调用捕获函数（会弹出授权），函数内部会关闭流
                dataUrl = await captureProactiveChatScreenshot();
            }

            if (dataUrl && socket && socket.readyState === WebSocket.OPEN) {
                socket.send(JSON.stringify({
                    action: 'stream_data',
                    data: dataUrl,
                    input_type: isMobile() ? 'camera' : 'screen'
                }));
                console.log('[ProactiveVision] 发送单帧屏幕数据');
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

    // 主动搭话截图函数
    async function captureProactiveChatScreenshot() {
        // API 兼容性检测
        if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
            console.warn('主动搭话截图失败：当前浏览器不支持 getDisplayMedia API');
            return null;
        }

        let captureStream = null;

        try {
            // 使用屏幕共享API进行截图
            captureStream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    cursor: 'always',
                },
                audio: false,
            });

            // 创建video元素来加载流
            const video = document.createElement('video');
            video.srcObject = captureStream;
            video.autoplay = true;
            video.muted = true;

            // 等待视频加载完成
            await video.play();

            // 使用统一的截图辅助函数进行截取（使用0.85质量）
            const { dataUrl, width, height } = captureCanvasFrame(video, 0.85);

            // 清理 video 元素释放资源
            video.srcObject = null;
            video.remove();

            console.log(`主动搭话截图成功，尺寸: ${width}x${height}`);
            return dataUrl;

        } catch (err) {
            console.error('主动搭话截图失败:', err);
            return null;
        } finally {
            // 确保流被正确关闭，防止资源泄漏
            if (captureStream) {
                captureStream.getTracks().forEach(track => track.stop());
            }
        }
    }

    // 暴露函数到全局作用域，供 live2d.js 调用
    window.resetProactiveChatBackoff = resetProactiveChatBackoff;
    window.stopProactiveChatSchedule = stopProactiveChatSchedule;
    window.startProactiveVisionDuringSpeech = startProactiveVisionDuringSpeech;
    window.stopProactiveVisionDuringSpeech = stopProactiveVisionDuringSpeech;

    // 保存设置到localStorage
    function saveSettings() {
        // 从全局变量读取最新值（确保同步 live2d.js 中的更改）
        const currentProactive = typeof window.proactiveChatEnabled !== 'undefined'
            ? window.proactiveChatEnabled
            : proactiveChatEnabled;
        const currentVision = typeof window.proactiveVisionEnabled !== 'undefined'
            ? window.proactiveVisionEnabled
            : proactiveVisionEnabled;
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

        const settings = {
            proactiveChatEnabled: currentProactive,
            proactiveVisionEnabled: currentVision,
            mergeMessagesEnabled: currentMerge,
            focusModeEnabled: currentFocus,
            proactiveChatInterval: currentProactiveChatInterval,
            proactiveVisionInterval: currentProactiveVisionInterval
        };
        localStorage.setItem('project_neko_settings', JSON.stringify(settings));

        // 同步回局部变量，保持一致性
        proactiveChatEnabled = currentProactive;
        proactiveVisionEnabled = currentVision;
        mergeMessagesEnabled = currentMerge;
        focusModeEnabled = currentFocus;
        proactiveChatInterval = currentProactiveChatInterval;
        proactiveVisionInterval = currentProactiveVisionInterval;
    }

    // 暴露到全局作用域，供 live2d.js 等其他模块调用
    window.saveNEKOSettings = saveSettings;

    // 从localStorage加载设置
    function loadSettings() {
        try {
            const saved = localStorage.getItem('project_neko_settings');
            if (saved) {
                const settings = JSON.parse(saved);
                // 使用 ?? 运算符提供更好的默认值处理（避免将 false 误判为需要使用默认值）
                proactiveChatEnabled = settings.proactiveChatEnabled ?? false;
                window.proactiveChatEnabled = proactiveChatEnabled; // 同步到全局
                // 主动视觉：从localStorage加载设置
                proactiveVisionEnabled = settings.proactiveVisionEnabled ?? false;
                window.proactiveVisionEnabled = proactiveVisionEnabled; // 同步到全局
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

                console.log('已加载设置:', {
                    proactiveChatEnabled: proactiveChatEnabled,
                    proactiveVisionEnabled: proactiveVisionEnabled,
                    mergeMessagesEnabled: mergeMessagesEnabled,
                    focusModeEnabled: focusModeEnabled,
                    proactiveChatInterval: proactiveChatInterval,
                    proactiveVisionInterval: proactiveVisionInterval,
                    focusModeDesc: focusModeEnabled ? 'AI说话时自动静音麦克风（不允许打断）' : '允许打断AI说话'
                });
            } else {
                // 如果没有保存的设置，也要确保全局变量被初始化
                console.log('未找到保存的设置，使用默认值');
                window.proactiveChatEnabled = proactiveChatEnabled;
                window.mergeMessagesEnabled = mergeMessagesEnabled;
                window.focusModeEnabled = focusModeEnabled;
                window.proactiveChatInterval = proactiveChatInterval;
                window.proactiveVisionInterval = proactiveVisionInterval;
            }
        } catch (error) {
            console.error('加载设置失败:', error);
            // 出错时也要确保全局变量被初始化
            window.proactiveChatEnabled = proactiveChatEnabled;
            window.mergeMessagesEnabled = mergeMessagesEnabled;
            window.focusModeEnabled = focusModeEnabled;
            window.proactiveChatInterval = proactiveChatInterval;
            window.proactiveVisionInterval = proactiveVisionInterval;
        }
    }

    // 加载设置
    loadSettings();

    // 加载麦克风增益设置
    loadMicGainSetting();

    // 加载扬声器音量设置
    loadSpeakerVolumeSetting();

    // 如果已开启主动搭话，立即启动定时器
    if (proactiveChatEnabled) {
        scheduleProactiveChat();
    }

    // 猫娘切换处理函数（通过WebSocket推送触发）
    let isSwitchingCatgirl = false;  // 标记是否正在切换猫娘，防止自动重连冲突

    // 处理猫娘切换的逻辑（支持 VRM 和 Live2D 双模型类型热切换）
    async function handleCatgirlSwitch(newCatgirl, oldCatgirl) {
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
                        vrmButtons.style.setProperty('display', 'flex', 'important');
                        vrmButtons.style.visibility = 'visible';
                        vrmButtons.style.opacity = '1';
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
} // 兼容老按钮

const ready = () => {
    if (ready._called) return;
    ready._called = true;
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
});

// 监听voice_id更新消息
window.addEventListener('message', function (event) {
    if (event.data.type === 'voice_id_updated') {
        console.log('[Voice Clone] 收到voice_id更新消息:', event.data.voice_id);
        if (typeof window.showStatusToast === 'function' && typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(window.t ? window.t('app.voiceUpdated', { name: lanlan_config.lanlan_name }) : `${lanlan_config.lanlan_name}的语音已更新`, 3000);
        }
    }

    // 旧的模型热切换代码已移至前面的 handleModelReload 函数
    // 不再需要这里的重复监听器
});

// 字幕提示框功能

// 归一化语言代码：将 BCP-47 格式（如 'zh-CN', 'en-US'）归一化为简单代码（'zh', 'en', 'ja', 'ko'）
// 与 detectLanguage() 返回的格式保持一致，避免误判
function normalizeLanguageCode(lang) {
    if (!lang) return 'zh'; // 默认中文
    const langLower = lang.toLowerCase();
    if (langLower.startsWith('zh')) {
        return 'zh';
    } else if (langLower.startsWith('ja')) {
        return 'ja';
    } else if (langLower.startsWith('en')) {
        return 'en';
    } else if (langLower.startsWith('ko')) {
        return 'ko';
    }
    return 'zh'; // 默认中文
}

// 字幕开关状态
let subtitleEnabled = localStorage.getItem('subtitleEnabled') === 'true';
// 用户语言（延迟初始化，避免使用 localStorage 旧值）
// 初始化为 null，确保在使用前从 API 获取最新值
let userLanguage = null;
// Google 翻译失败标记（会话级，页面刷新后重置）
let googleTranslateFailed = false;
// 用户语言初始化 Promise（用于确保只初始化一次）
let userLanguageInitPromise = null;

// 获取用户语言（支持语言代码归一化，延迟初始化）
async function getUserLanguage() {
    // 如果已经初始化过，直接返回
    if (userLanguage !== null) {
        return userLanguage;
    }
    
    // 如果正在初始化，等待初始化完成
    if (userLanguageInitPromise) {
        return await userLanguageInitPromise;
    }
    
    // 开始初始化
    userLanguageInitPromise = (async () => {
        try {
            // 优先从API获取最新值
            const response = await fetch('/api/config/user_language');
            const data = await response.json();
            if (data.success && data.language) {
                // 归一化语言代码：将 BCP-47 格式（如 'zh-CN', 'en-US'）归一化为简单代码（'zh', 'en', 'ja', 'ko'）
                // 与 detectLanguage() 返回的格式保持一致，避免误判
                userLanguage = normalizeLanguageCode(data.language);
                localStorage.setItem('userLanguage', userLanguage);
                return userLanguage;
            }
        } catch (error) {
            console.warn('从API获取用户语言失败，尝试使用缓存或浏览器语言:', error);
        }
        
        // API失败时，尝试从localStorage获取（作为回退）
        const cachedLang = localStorage.getItem('userLanguage');
        if (cachedLang) {
            userLanguage = normalizeLanguageCode(cachedLang);
            return userLanguage;
        }
        
        // 最后回退到浏览器语言
        const browserLang = navigator.language || navigator.userLanguage;
        userLanguage = normalizeLanguageCode(browserLang);
        localStorage.setItem('userLanguage', userLanguage);
        return userLanguage;
    })();
    
    return await userLanguageInitPromise;
}

// 简单的语言检测函数（客户端）
function detectLanguage(text) {
    if (!text || !text.trim()) {
        return 'unknown';
    }
    
    // 中文检测
    const chinesePattern = /[\u4e00-\u9fff]/g;
    // 日文检测（平假名、片假名）
    const japanesePattern = /[\u3040-\u309f\u30a0-\u30ff]/g;
    // 韩文检测（谚文）
    const koreanPattern = /[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/g;
    // 英文检测
    const englishPattern = /[a-zA-Z]/g;
    
    const chineseCount = (text.match(chinesePattern) || []).length;
    const japaneseCount = (text.match(japanesePattern) || []).length;
    const koreanCount = (text.match(koreanPattern) || []).length;
    const englishCount = (text.match(englishPattern) || []).length;
    
    // 如果包含日文假名，优先判断为日语
    if (japaneseCount > 0) {
        return 'ja';
    }
    
    // 如果包含韩文，优先判断为韩语
    if (koreanCount > 0) {
        return 'ko';
    }
    
    // 判断主要语言
    if (chineseCount > englishCount && chineseCount > 0) {
        return 'zh';
    } else if (englishCount > 0) {
        return 'en';
    } else {
        return 'unknown';
    }
}

// 字幕显示相关变量
let subtitleTimeout = null;
let currentTranslateAbortController = null;
let pendingTranslation = null;
// 流式输出时字幕语言检测的防抖计时器
let subtitleCheckDebounceTimer = null;

// 翻译消息气泡（如果用户语言不是中文）
async function translateMessageBubble(text, messageElement) {
    if (!text || !text.trim() || !messageElement) {
        return;
    }
    
    if (userLanguage === null) {
        await getUserLanguage();
    }
    
    if (!userLanguage || userLanguage === 'zh') {
        return;
    }
    
    try {
        const response = await fetch('/api/translate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: text,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'),
                source_lang: 'zh',
                skip_google: googleTranslateFailed
            })
        });
        
        if (!response.ok) {
            console.warn('翻译消息气泡失败:', response.status);
            return;
        }
        
        const result = await response.json();
        
        if (result.google_failed === true) {
            googleTranslateFailed = true;
            console.log('Google 翻译失败，本次会话中将跳过 Google 翻译');
        }
        
        if (result.success && result.translated_text && result.translated_text !== text) {
            const timestampMatch = messageElement.textContent.match(/^\[(\d{2}:\d{2}:\d{2})\] 🎀 /);
            if (timestampMatch) {
                messageElement.textContent = `[${timestampMatch[1]}] 🎀 ${result.translated_text}`;
                console.log('消息气泡已翻译:', result.translated_text.substring(0, 50) + '...');
            }
        }
    } catch (error) {
        console.error('翻译消息气泡异常:', error);
    }
}

// 检查并显示字幕提示框
async function checkAndShowSubtitlePrompt(text) {
    if (userLanguage === null) {
        await getUserLanguage();
    }
    
    const allGeminiMessages = document.querySelectorAll('.message.gemini');
    let hasNonUserLanguage = false;
    let latestNonUserLanguageText = '';
    
    if (allGeminiMessages.length > 0) {
        for (const msg of allGeminiMessages) {
            const msgText = msg.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
            if (msgText && msgText.trim()) {
                const detectedLang = detectLanguage(msgText);
                if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                    hasNonUserLanguage = true;
                    latestNonUserLanguageText = msgText;
                }
            }
        }
    }
    
    if (hasNonUserLanguage) {
        showSubtitlePrompt();
    } else {
        hideSubtitlePrompt();
        hideSubtitle();
    }
}

// 翻译并显示字幕
async function translateAndShowSubtitle(text) {
    if (!text || !text.trim()) {
        return;
    }
    
    // 即使开关关闭，也需要检测语言来决定是否隐藏提示
    if (userLanguage === null) {
        await getUserLanguage();
    }
    
    const currentTranslationText = text;
    pendingTranslation = currentTranslationText;
    
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
    }
    
    currentTranslateAbortController = new AbortController();
    
    try {
        const subtitleDisplay = document.getElementById('subtitle-display');
        if (!subtitleDisplay) {
            console.warn('字幕显示元素不存在');
            return;
        }
        
        // 调用翻译API
        const response = await fetch('/api/translate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                text: text,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'), // 确保已初始化
                source_lang: null, // 自动检测
                skip_google: googleTranslateFailed // 如果 Google 翻译失败过，跳过它
            }),
            signal: currentTranslateAbortController.signal
        });
        
        if (!response.ok) {
            console.warn('翻译请求失败:', response.status);
            if (pendingTranslation === currentTranslationText) {
                pendingTranslation = null;
            }
            console.error('字幕翻译API请求失败:', {
                status: response.status,
                statusText: response.statusText,
                text: text.substring(0, 50) + '...',
                userLanguage: userLanguage
            });
            return;
        }
        
        const result = await response.json();
        
        if (pendingTranslation !== currentTranslationText) {
            console.log('检测到更新的翻译请求，忽略旧的翻译结果');
            return;
        }
        pendingTranslation = null;
        
        if (result.google_failed === true) {
            googleTranslateFailed = true;
            console.log('Google 翻译失败，本次会话中将跳过 Google 翻译');
        }
        
        const frontendDetectedLang = detectLanguage(text);
        const isNonUserLanguage = frontendDetectedLang !== 'unknown' && frontendDetectedLang !== userLanguage;
        
        const subtitleDisplayAfter = document.getElementById('subtitle-display');
        if (!subtitleDisplayAfter) {
            console.warn('字幕显示元素在异步操作后不存在，可能已被移除');
            return;
        }
        
        if (result.success && result.translated_text && 
            result.source_lang && result.target_lang && 
            result.source_lang !== result.target_lang && 
            result.source_lang !== 'unknown') {
            showSubtitlePrompt();
            
            if (subtitleEnabled) {
                subtitleDisplayAfter.textContent = result.translated_text;
                subtitleDisplayAfter.classList.add('show');
                subtitleDisplayAfter.classList.remove('hidden');
                subtitleDisplayAfter.style.opacity = '1';
                console.log('字幕已更新（已翻译）:', result.translated_text.substring(0, 50) + '...');
                
                if (subtitleTimeout) {
                    clearTimeout(subtitleTimeout);
                    subtitleTimeout = null;
                }
                
                subtitleTimeout = setTimeout(() => {
                    const subtitleDisplayForTimeout = document.getElementById('subtitle-display');
                    if (subtitleDisplayForTimeout && subtitleDisplayForTimeout.classList.contains('show')) {
                        hideSubtitle();
                        console.log('字幕30秒后自动隐藏');
                    }
                }, 30000);
            } else {
                subtitleDisplayAfter.textContent = '';
                subtitleDisplayAfter.classList.remove('show');
                subtitleDisplayAfter.classList.add('hidden');
                subtitleDisplayAfter.style.opacity = '0';
                console.log('开关已关闭，不显示字幕');
            }
        } else {
            if (isNonUserLanguage) {
                showSubtitlePrompt();
                subtitleDisplayAfter.textContent = '';
                subtitleDisplayAfter.classList.remove('show');
                subtitleDisplayAfter.classList.add('hidden');
                subtitleDisplayAfter.style.opacity = '0';
                console.log('前端检测到非用户语言，显示提示框');
            } else {
                hideSubtitlePrompt();
                subtitleDisplayAfter.textContent = '';
                subtitleDisplayAfter.classList.remove('show');
                subtitleDisplayAfter.classList.add('hidden');
                subtitleDisplayAfter.style.opacity = '0';
                console.log('对话已是用户语言，自动隐藏字幕提示');
            }
            if (subtitleTimeout) {
                clearTimeout(subtitleTimeout);
                subtitleTimeout = null;
            }
        }
    } catch (error) {
        if (error.name === 'AbortError') {
            if (pendingTranslation === currentTranslationText) {
                pendingTranslation = null;
            }
            return;
        }
        
        console.error('字幕翻译异常:', {
            error: error.message,
            stack: error.stack,
            name: error.name,
            text: text.substring(0, 50) + '...',
            userLanguage: userLanguage
        });
        
        if (pendingTranslation === currentTranslationText) {
            pendingTranslation = null;
        }
        
        if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            console.warn('提示：字幕翻译功能暂时不可用，但对话可以正常进行');
        }
    } finally {
        currentTranslateAbortController = null;
    }
}

// 隐藏字幕
function hideSubtitle() {
    const subtitleDisplay = document.getElementById('subtitle-display');
    if (!subtitleDisplay) return;
    
    // 清除定时器
    if (subtitleTimeout) {
        clearTimeout(subtitleTimeout);
        subtitleTimeout = null;
    }
    
    subtitleDisplay.classList.remove('show');
    subtitleDisplay.style.opacity = '0';
    
    // 延迟隐藏，让淡出动画完成
    setTimeout(() => {
        const subtitleDisplayForTimeout = document.getElementById('subtitle-display');
        if (subtitleDisplayForTimeout && subtitleDisplayForTimeout.style.opacity === '0') {
            subtitleDisplayForTimeout.classList.add('hidden');
        }
    }, 300);
}

// 显示字幕提示框（参考Xiao8项目，改为系统消息形式）
function showSubtitlePrompt() {
    // 检查是否已经显示过提示（避免重复显示）
    const existingPrompt = document.getElementById('subtitle-prompt-message');
    if (existingPrompt) {
        return;
    }
    
    const textInputArea = document.getElementById('text-input-area');
    const chatContainer = document.getElementById('chat-container');
    
    // 检测是否处于语音模式（text-input-area 被隐藏）
    const isVoiceMode = textInputArea && textInputArea.classList.contains('hidden');
    
    // 确定父容器：语音模式下使用 chat-container，否则使用 text-input-area
    let parentContainer;
    if (isVoiceMode) {
        parentContainer = chatContainer;
    } else {
        parentContainer = textInputArea;
    }
    
    if (!parentContainer) {
        return;
    }
    
    // 创建提示消息（放在输入框区域中）
    const promptDiv = document.createElement('div');
    promptDiv.id = 'subtitle-prompt-message';
    promptDiv.classList.add('subtitle-prompt-message');
    
    // 如果是语音模式，添加特殊样式类
    if (isVoiceMode) {
        promptDiv.classList.add('voice-mode');
    }
    
    // 创建提示内容
    const promptContent = document.createElement('div');
    promptContent.classList.add('subtitle-prompt-content');
    
    // 创建开关容器
    const toggleWrapper = document.createElement('div');
    toggleWrapper.classList.add('subtitle-toggle-wrapper');
    
    // 创建圆形指示器
    const indicator = document.createElement('div');
    indicator.classList.add('subtitle-toggle-indicator');
    if (subtitleEnabled) {
        indicator.classList.add('active');
    }
    
    // 创建标签文本
    const labelText = document.createElement('span');
    labelText.classList.add('subtitle-toggle-label');
    labelText.setAttribute('data-i18n', 'subtitle.enable');
    // 使用i18n翻译，如果i18n未加载或翻译不存在则根据浏览器语言提供fallback
    const browserLang = normalizeLanguageCode(navigator.language);
    const fallbacks = {
        'zh': '开启字幕翻译',
        'en': 'Enable Subtitle Translation',
        'ja': '字幕翻訳を有効にする',
        'ko': '자막 번역 켜기'
    };
    if (window.t) {
        const translated = window.t('subtitle.enable');
        // 如果翻译返回的是key本身（说明翻译不存在），使用浏览器语言的fallback
        labelText.textContent = (translated && translated !== 'subtitle.enable') ? translated : (fallbacks[browserLang] || fallbacks['en']);
    } else {
        // i18n未加载时，使用浏览器语言的fallback
        labelText.textContent = fallbacks[browserLang] || fallbacks['en'];
    }
    
    toggleWrapper.appendChild(indicator);
    toggleWrapper.appendChild(labelText);
    
    promptContent.appendChild(toggleWrapper);
    promptDiv.appendChild(promptContent);
    
    // 根据模式插入到不同位置
    if (isVoiceMode) {
        // 语音模式：插入到 chat-container 底部（在 text-input-area 之前）
        if (textInputArea) {
            chatContainer.insertBefore(promptDiv, textInputArea);
        } else {
            chatContainer.appendChild(promptDiv);
        }
    } else {
        // 文本模式：插入到输入框区域的最后（在text-input-row之后）
        const textInputRow = textInputArea.querySelector('#text-input-row');
        if (textInputRow && textInputRow.nextSibling) {
            textInputArea.insertBefore(promptDiv, textInputRow.nextSibling);
        } else {
            textInputArea.appendChild(promptDiv);
        }
    }

    
    // 如果i18next已加载，监听语言变化事件
    if (window.i18next) {
        window.i18next.on('languageChanged', () => {
            if (labelText && window.t) {
                const translated = window.t('subtitle.enable');
                // 如果翻译返回的是key本身（说明翻译不存在），使用当前语言的fallback
                if (translated && translated !== 'subtitle.enable') {
                    labelText.textContent = translated;
                } else {
                    // 使用与初始渲染相同的fallback逻辑
                    const currentLang = normalizeLanguageCode(window.i18next.language || navigator.language);
                    labelText.textContent = fallbacks[currentLang] || fallbacks['en'];
                }
            }
        });
    }
    
    // 更新指示器状态
    const updateIndicator = () => {
        if (subtitleEnabled) {
            indicator.classList.add('active');
        } else {
            indicator.classList.remove('active');
        }
    };
    
    // 切换开关的函数
    const handleToggle = (e) => {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }
        subtitleEnabled = !subtitleEnabled;
        localStorage.setItem('subtitleEnabled', subtitleEnabled.toString());
        updateIndicator();
        console.log('字幕开关:', subtitleEnabled ? '开启' : '关闭');
        
        if (!subtitleEnabled) {
            const subtitleDisplay = document.getElementById('subtitle-display');
            if (subtitleDisplay) {
                subtitleDisplay.textContent = '';
                subtitleDisplay.classList.remove('show');
                subtitleDisplay.classList.add('hidden');
                subtitleDisplay.style.opacity = '0';
            }
            if (subtitleTimeout) {
                clearTimeout(subtitleTimeout);
                subtitleTimeout = null;
            }
        } else {
            // 如果开启，重新翻译并显示字幕
            if (currentTranslateAbortController) {
                currentTranslateAbortController.abort();
                currentTranslateAbortController = null;
            }
            pendingTranslation = null;
            
            if (window.currentGeminiMessage && 
                window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
                window.currentGeminiMessage.isConnected &&
                typeof window.currentGeminiMessage.textContent === 'string') {
                const fullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
                if (fullText && fullText.trim()) {
                    const subtitleDisplay = document.getElementById('subtitle-display');
                    if (!subtitleDisplay) {
                        console.error('字幕显示元素不存在，无法显示字幕');
                        return;
                    }
                    subtitleDisplay.classList.remove('hidden');
                    translateAndShowSubtitle(fullText);
                }
            } else {
                if (window.currentGeminiMessage) {
                    console.warn('currentGeminiMessage存在但不是有效的DOM元素，无法翻译字幕');
                }
            }
        }
    };
    
    // 绑定点击事件
    toggleWrapper.addEventListener('click', handleToggle);
    indicator.addEventListener('click', handleToggle);
    labelText.addEventListener('click', handleToggle);
}

// 隐藏字幕提示框
function hideSubtitlePrompt() {
    const existingPrompt = document.getElementById('subtitle-prompt-message');
    if (existingPrompt) {
        existingPrompt.remove();
        console.log('已隐藏字幕提示消息');
    }
}

// 初始化字幕开关（DOM加载完成后）
document.addEventListener('DOMContentLoaded', async function() {
    // 初始化用户语言（等待完成，确保使用最新值）
    await getUserLanguage();

    // 检查当前消息中是否有非用户语言
    // 增强null安全检查：确保currentGeminiMessage是有效的DOM元素
    if (window.currentGeminiMessage &&
        window.currentGeminiMessage.nodeType === Node.ELEMENT_NODE &&
        window.currentGeminiMessage.isConnected &&
        typeof window.currentGeminiMessage.textContent === 'string') {
        const fullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] 🎀 /, '');
        if (fullText && fullText.trim()) {
            checkAndShowSubtitlePrompt(fullText);
        }
    }

    // 初始化通用引导管理器（幂等性保护）
    if (!window.__universalTutorialManagerInitialized && typeof initUniversalTutorialManager === 'function') {
        try {
            initUniversalTutorialManager();
            window.__universalTutorialManagerInitialized = true;
            console.log('[App] 通用引导管理器已初始化');
        } catch (error) {
            console.error('[App] 通用引导管理器初始化失败:', error);
        }
    }
});
