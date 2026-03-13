/**
 * app-screen.js — Screen sharing, video streaming, and Electron source selector
 *
 * Extracted from the monolithic app.js.
 * Follows the IIFE + window global pattern used by all app-*.js modules.
 *
 * Exports: window.appScreen
 * Backward-compat globals:
 *   window.startScreenSharing, window.stopScreenSharing,
 *   window.switchScreenSharing, window.switchMicCapture,
 *   window.selectScreenSource, window.getSelectedScreenSourceId,
 *   window.renderFloatingScreenSourceList
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;
    const safeT = window.safeT;
    const isMobile = window.appUtils.isMobile;

    // ======================== DOM refs (lazy, filled on first use) ========================
    function dom(id) {
        return document.getElementById(id);
    }
    function screenButton()       { return dom('screenButton'); }
    function micButton()          { return dom('micButton'); }
    function muteButton()         { return dom('muteButton'); }
    function stopButton()         { return dom('stopButton'); }
    function resetSessionButton() { return dom('resetSessionButton'); }

    // ======================== Restore persisted screen source ========================
    S.selectedScreenSourceId = (function () {
        try {
            var saved = localStorage.getItem('selectedScreenSourceId');
            return saved || null;
        } catch (e) {
            return null;
        }
    })();

    // ======================== scheduleScreenCaptureIdleCheck ========================
    function scheduleScreenCaptureIdleCheck() {
        // 清除现有定时器
        if (S.screenCaptureStreamIdleTimer) {
            clearTimeout(S.screenCaptureStreamIdleTimer);
            S.screenCaptureStreamIdleTimer = null;
        }

        // 如果没有屏幕流，不需要调度
        if (!S.screenCaptureStream || !S.screenCaptureStreamLastUsed) {
            return;
        }

        var IDLE_TIMEOUT = C.SCREEN_IDLE_TIMEOUT;     // 5 min
        var CHECK_INTERVAL = C.SCREEN_CHECK_INTERVAL;  // 1 min

        S.screenCaptureStreamIdleTimer = setTimeout(async function () {
            if (S.screenCaptureStream && S.screenCaptureStreamLastUsed) {
                var idleTime = Date.now() - S.screenCaptureStreamLastUsed;
                if (idleTime >= IDLE_TIMEOUT) {
                    // 主动视觉活跃时，不释放屏幕流（避免 macOS 反复弹窗 getDisplayMedia）
                    var proactiveVisionActive = S.proactiveVisionEnabled ||
                        (S.proactiveVisionChatEnabled && S.proactiveChatEnabled);
                    var isManualScreenShare = screenButton() && screenButton().classList.contains('active');
                    if (proactiveVisionActive && !isManualScreenShare) {
                        console.log('[屏幕流闲置] 主动视觉活跃中，跳过释放并续约定时器');
                        S.screenCaptureStreamLastUsed = Date.now();
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
                        if (S.screenCaptureStream) {
                            try {
                                if (typeof S.screenCaptureStream.getTracks === 'function') {
                                    S.screenCaptureStream.getTracks().forEach(function (track) {
                                        try { track.stop(); } catch (err) { }
                                    });
                                }
                            } catch (err) {
                                console.warn('Failed to stop tracks in catch block', err);
                            }
                        }
                        S.screenCaptureStream = null;
                        S.screenCaptureStreamLastUsed = null;
                        S.screenCaptureStreamIdleTimer = null;
                    }
                } else {
                    // 未达到阈值，继续调度下一次检查
                    scheduleScreenCaptureIdleCheck();
                }
            }
        }, CHECK_INTERVAL);
    }
    mod.scheduleScreenCaptureIdleCheck = scheduleScreenCaptureIdleCheck;

    // ======================== captureCanvasFrame ========================
    /**
     * 统一的截图辅助函数：从video元素捕获一帧到canvas，统一720p节流和JPEG压缩
     * @param {HTMLVideoElement} video - 视频源元素
     * @param {number} jpegQuality - JPEG压缩质量 (0-1)，默认0.8
     * @param {boolean} detectBlack - 是否检测纯黑帧（窗口最小化等），默认false
     * @returns {{dataUrl: string, width: number, height: number}|null}
     */
    function captureCanvasFrame(video, jpegQuality, detectBlack) {
        if (jpegQuality === undefined) jpegQuality = 0.8;

        // 流无效时 videoWidth/videoHeight 为 0，直接返回 null 避免生成空图
        if (!video.videoWidth || !video.videoHeight) {
            return null;
        }

        var canvas = document.createElement('canvas');
        var ctx = canvas.getContext('2d');

        // 计算缩放后的尺寸（保持宽高比，限制到720p）
        var targetWidth = video.videoWidth;
        var targetHeight = video.videoHeight;

        if (targetWidth > C.MAX_SCREENSHOT_WIDTH || targetHeight > C.MAX_SCREENSHOT_HEIGHT) {
            var widthRatio = C.MAX_SCREENSHOT_WIDTH / targetWidth;
            var heightRatio = C.MAX_SCREENSHOT_HEIGHT / targetHeight;
            var scale = Math.min(widthRatio, heightRatio);
            targetWidth = Math.round(targetWidth * scale);
            targetHeight = Math.round(targetHeight * scale);
        }

        canvas.width = targetWidth;
        canvas.height = targetHeight;

        // 绘制视频帧到canvas（缩放绘制）并转换为JPEG
        ctx.drawImage(video, 0, 0, targetWidth, targetHeight);

        // 黑帧检测：采样中心16x16区域，全黑则返回null（窗口最小化等场景）
        if (detectBlack) {
            var sw = Math.min(16, targetWidth), sh = Math.min(16, targetHeight);
            var sx = Math.floor((targetWidth - sw) / 2);
            var sy = Math.floor((targetHeight - sh) / 2);
            var sample = ctx.getImageData(sx, sy, sw, sh);
            var allBlack = true;
            for (var i = 0; i < sample.data.length; i += 4) {
                if (sample.data[i] > 2 || sample.data[i + 1] > 2 || sample.data[i + 2] > 2) {
                    allBlack = false;
                    break;
                }
            }
            if (allBlack) return null;
        }

        var dataUrl = canvas.toDataURL('image/jpeg', jpegQuality);

        return { dataUrl: dataUrl, width: targetWidth, height: targetHeight };
    }
    mod.captureCanvasFrame = captureCanvasFrame;

    // ======================== captureFrameFromStream ========================
    /**
     * 从MediaStream提取单帧截图（创建临时video元素，用后即销毁）
     * @param {MediaStream} stream - 媒体流
     * @param {number} jpegQuality - JPEG压缩质量 (0-1)
     * @returns {Promise<{dataUrl: string, width: number, height: number}|null>}
     */
    async function captureFrameFromStream(stream, jpegQuality) {
        if (!stream || !stream.active) return null;
        var video = document.createElement('video');
        video.srcObject = stream;
        video.autoplay = true;
        video.muted = true;
        try { await video.play(); } catch (e) { /* 某些情况下不需要 play() 成功也能读取帧 */ }
        if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
            await new Promise(function (resolve) {
                video.addEventListener('loadeddata', resolve, { once: true });
            });
        }
        var frame = captureCanvasFrame(video, jpegQuality, true); // detectBlack=true
        video.srcObject = null;
        video.remove();
        return frame; // {dataUrl, width, height} or null
    }
    mod.captureFrameFromStream = captureFrameFromStream;

    // ======================== acquireOrReuseCachedStream ========================
    /**
     * 统一的流获取函数：优先缓存流 → Electron sourceId → getDisplayMedia → null
     * @param {Object} opts
     * @param {boolean} opts.allowPrompt - 是否允许 getDisplayMedia 弹窗（用户手势上下文传true）
     * @returns {Promise<MediaStream|null>}
     */
    async function acquireOrReuseCachedStream(opts) {
        if (!opts) opts = {};

        // 1. 缓存流有效且 tracks live → 直接返回（~0ms）
        if (S.screenCaptureStream && S.screenCaptureStream.active) {
            var tracks = S.screenCaptureStream.getVideoTracks();
            if (tracks.length > 0 && tracks.some(function (t) { return t.readyState === 'live'; })) {
                S.screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();
                return S.screenCaptureStream;
            }
            // tracks 已结束，废弃流
            console.warn('[acquireStream] 缓存流 tracks 已结束，废弃');
            try { S.screenCaptureStream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) { } }); } catch (e) { }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
        }

        // 2. Electron selectedScreenSourceId → getUserMedia(chromeMediaSource)
        var selectedSourceId = S.selectedScreenSourceId;
        if (selectedSourceId && window.electronDesktopCapturer) {
            try {
                var newStream = await Promise.race([
                    (async function () {
                        // 验证源存在
                        var currentSources = await window.electronDesktopCapturer.getSources({
                            types: ['window', 'screen'],
                            thumbnailSize: { width: 1, height: 1 }
                        });
                        var sourceExists = currentSources.some(function (s) { return s.id === selectedSourceId; });

                        var captureSourceId = selectedSourceId;
                        if (!sourceExists) {
                            console.warn('[acquireStream] 选中的源已不可用，尝试回退到全屏源');
                            var screenSources = currentSources.filter(function (s) { return s.id.startsWith('screen:'); });
                            if (screenSources.length > 0) {
                                captureSourceId = screenSources[0].id;
                            } else {
                                return null; // 无可用源
                            }
                        }

                        return await navigator.mediaDevices.getUserMedia({
                            audio: false,
                            video: {
                                mandatory: {
                                    chromeMediaSource: 'desktop',
                                    chromeMediaSourceId: captureSourceId,
                                    maxFrameRate: 1
                                }
                            }
                        });
                    })(),
                    new Promise(function (_, reject) {
                        setTimeout(function () { reject(new Error('Electron capture timeout')); }, 500);
                    })
                ]);

                if (newStream) {
                    S.screenCaptureStream = newStream;
                    S.screenCaptureStreamLastUsed = Date.now();
                    S.screenCaptureAutoPromptFailed = false;
                    scheduleScreenCaptureIdleCheck();

                    // 添加 ended 监听
                    newStream.getVideoTracks().forEach(function (track) {
                        track.addEventListener('ended', function () {
                            console.log('[acquireStream] 流被终止');
                            if (S.screenCaptureStream === newStream) {
                                S.screenCaptureStream = null;
                                S.screenCaptureStreamLastUsed = null;
                                if (S.screenCaptureStreamIdleTimer) {
                                    clearTimeout(S.screenCaptureStreamIdleTimer);
                                    S.screenCaptureStreamIdleTimer = null;
                                }
                            }
                        });
                    });

                    console.log('[acquireStream] Electron 源获取成功');
                    return newStream;
                }
            } catch (electronErr) {
                console.warn('[acquireStream] Electron 源获取失败:', electronErr.message);
            }
        }

        // 3. getDisplayMedia（仅 allowPrompt && !screenCaptureAutoPromptFailed）
        if (opts.allowPrompt && !S.screenCaptureAutoPromptFailed &&
            navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
            try {
                var displayStream = await navigator.mediaDevices.getDisplayMedia({
                    video: { cursor: 'always', frameRate: { max: 1 } },
                    audio: false,
                });

                S.screenCaptureStream = displayStream;
                S.screenCaptureStreamLastUsed = Date.now();
                S.screenCaptureAutoPromptFailed = false;
                scheduleScreenCaptureIdleCheck();

                displayStream.getVideoTracks().forEach(function (track) {
                    track.addEventListener('ended', function () {
                        console.log('[acquireStream] getDisplayMedia 流被用户终止');
                        if (S.screenCaptureStream === displayStream) {
                            S.screenCaptureStream = null;
                            S.screenCaptureStreamLastUsed = null;
                            if (S.screenCaptureStreamIdleTimer) {
                                clearTimeout(S.screenCaptureStreamIdleTimer);
                                S.screenCaptureStreamIdleTimer = null;
                            }
                        }
                    });
                });

                console.log('[acquireStream] getDisplayMedia 获取成功');
                return displayStream;
            } catch (displayErr) {
                console.warn('[acquireStream] getDisplayMedia 失败:', displayErr);
                if (displayErr.name !== 'NotAllowedError') {
                    S.screenCaptureAutoPromptFailed = true;
                }
            }
        }

        // 4. 返回 null，调用者自行 fallback 到 pyautogui
        return null;
    }
    mod.acquireOrReuseCachedStream = acquireOrReuseCachedStream;

    // ======================== fetchBackendScreenshot ========================
    /**
     * 后端截图兜底：当前端所有屏幕捕获 API 均失败时，请求后端用 pyautogui 截取本机屏幕。
     * 安全限制：仅当页面来自 localhost / 127.0.0.1 / 0.0.0.0 时才调用。
     * @returns {Promise<{dataUrl: string|null, status: number|null}>}
     */
    async function fetchBackendScreenshot() {
        var h = window.location.hostname;
        if (h !== 'localhost' && h !== '127.0.0.1' && h !== '0.0.0.0') {
            return { dataUrl: null, status: null };
        }
        try {
            var resp = await fetch('/api/screenshot');
            if (!resp.ok) return { dataUrl: null, status: resp.status };
            var json = await resp.json();
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
    mod.fetchBackendScreenshot = fetchBackendScreenshot;

    // ======================== stopScreening ========================
    function stopScreening() {
        if (S.videoSenderInterval) {
            clearInterval(S.videoSenderInterval);
            S.videoSenderInterval = null;
        }
    }
    mod.stopScreening = stopScreening;

    // ======================== syncFloatingScreenButtonState ========================
    function syncFloatingScreenButtonState(isActive) {
        // 更新所有存在的 manager 的按钮状态
        var managers = [window.live2dManager, window.vrmManager];

        for (var i = 0; i < managers.length; i++) {
            var manager = managers[i];
            if (manager && manager._floatingButtons && manager._floatingButtons.screen) {
                var ref = manager._floatingButtons.screen;
                var button = ref.button;
                var imgOff = ref.imgOff;
                var imgOn = ref.imgOn;
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
    mod.syncFloatingScreenButtonState = syncFloatingScreenButtonState;

    // ======================== startScreenVideoStreaming ========================
    function startScreenVideoStreaming(stream, input_type) {
        // 更新最后使用时间并调度闲置检查
        if (stream === S.screenCaptureStream) {
            S.screenCaptureStreamLastUsed = Date.now();
            scheduleScreenCaptureIdleCheck();
        }

        var video = document.createElement('video');
        video.srcObject = stream;
        video.autoplay = true;
        video.muted = true;

        S.videoTrack = stream.getVideoTracks()[0];

        // 定时抓取当前帧并编码为jpeg（使用统一的 captureCanvasFrame）
        video.play().then(function () {
            if (video.videoWidth && video.videoHeight) {
                var vw = video.videoWidth, vh = video.videoHeight;
                if (vw > C.MAX_SCREENSHOT_WIDTH || vh > C.MAX_SCREENSHOT_HEIGHT) {
                    var scale = Math.min(C.MAX_SCREENSHOT_WIDTH / vw, C.MAX_SCREENSHOT_HEIGHT / vh);
                    console.log('屏幕共享：原尺寸 ' + vw + 'x' + vh + ' -> 缩放到 ' + Math.round(vw * scale) + 'x' + Math.round(vh * scale));
                }
            }

            S.videoSenderInterval = setInterval(function () {
                var frame = captureCanvasFrame(video, 0.8);
                if (frame && frame.dataUrl && S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({
                        action: 'stream_data',
                        data: frame.dataUrl,
                        input_type: input_type,
                    }));

                    // 刷新最后使用时间，防止活跃屏幕分享被误释放
                    if (stream === S.screenCaptureStream) {
                        S.screenCaptureStreamLastUsed = Date.now();
                    }
                }
            }, 1000);
        }); // 每1000ms一帧
    }
    mod.startScreenVideoStreaming = startScreenVideoStreaming;

    // ======================== getMobileCameraStream ========================
    async function getMobileCameraStream() {
        var makeConstraints = function (facing) {
            return {
                video: {
                    facingMode: facing,
                    frameRate: { ideal: 1, max: 1 },
                },
                audio: false,
            };
        };

        var attempts = [
            { label: 'rear', constraints: makeConstraints({ ideal: 'environment' }) },
            { label: 'front', constraints: makeConstraints('user') },
            { label: 'any', constraints: { video: { frameRate: { ideal: 1, max: 1 } }, audio: false } },
        ];

        var lastError;

        for (var i = 0; i < attempts.length; i++) {
            var attempt = attempts[i];
            try {
                console.log((window.t('console.tryingCamera')) + ' ' + attempt.label + ' ' + (window.t('console.cameraLabel')) + ' 1' + (window.t('console.cameraFps')));
                return await navigator.mediaDevices.getUserMedia(attempt.constraints);
            } catch (err) {
                console.warn(attempt.label + ' ' + (window.t('console.cameraFailed')), err);
                lastError = err;
            }
        }

        if (lastError) {
            window.showStatusToast(lastError.toString(), 4000);
            throw lastError;
        }
    }
    mod.getMobileCameraStream = getMobileCameraStream;

    // ======================== startScreenSharing ========================
    async function startScreenSharing() {
        // 检查是否在录音状态
        if (!S.isRecording) {
            window.showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
            return;
        }

        try {
            // 初始化音频播放上下文
            if (window.showCurrentModel) await window.showCurrentModel(); // 智能显示当前模型
            if (!S.audioPlayerContext) {
                S.audioPlayerContext = new (window.AudioContext || window.webkitAudioContext)();
                window.syncAudioGlobals();
            }

            // 如果上下文被暂停，则恢复它
            if (S.audioPlayerContext.state === 'suspended') {
                await S.audioPlayerContext.resume();
            }

            if (S.screenCaptureStream == null) {
                if (isMobile()) {
                    // 移动端使用摄像头
                    var tmp = await getMobileCameraStream();
                    if (tmp instanceof MediaStream) {
                        S.screenCaptureStream = tmp;
                    } else {
                        // 保持原有错误处理路径：让 catch 去接手
                        throw (tmp instanceof Error ? tmp : new Error('无法获取摄像头流'));
                    }
                } else {

                    // Desktop/laptop: capture the user's chosen screen / window / tab.
                    // 检查是否有选中的特定屏幕源（仅Electron环境）
                    var selectedSourceId = window.getSelectedScreenSourceId ? window.getSelectedScreenSourceId() : null;

                    if (selectedSourceId && window.electronDesktopCapturer) {
                        // 验证选中的源是否仍然存在（窗口可能已关闭）
                        try {
                            var currentSources = await window.electronDesktopCapturer.getSources({
                                types: ['window', 'screen'],
                                thumbnailSize: { width: 1, height: 1 }
                            });
                            var sourceStillExists = currentSources.some(function (s) { return s.id === selectedSourceId; });

                            if (!sourceStillExists) {
                                console.warn('[屏幕源] 选中的源已不可用 (ID:', selectedSourceId, ')，自动回退到全屏');
                                window.showStatusToast(
                                    safeT('app.screenSource.sourceLost', '屏幕分享无法找到之前选择窗口，已切换为全屏分享'),
                                    3000
                                );
                                // 查找第一个全屏源作为回退
                                var screenSources = currentSources.filter(function (s) { return s.id.startsWith('screen:'); });
                                if (screenSources.length > 0) {
                                    selectedSourceId = screenSources[0].id;
                                    S.selectedScreenSourceId = selectedSourceId;
                                    try { localStorage.setItem('selectedScreenSourceId', selectedSourceId); } catch (e) { }
                                    updateScreenSourceListSelection();
                                } else {
                                    // 连全屏源都拿不到，清空选择让下面走 getDisplayMedia
                                    selectedSourceId = null;
                                    S.selectedScreenSourceId = null;
                                    try { localStorage.removeItem('selectedScreenSourceId'); } catch (e) { }
                                }
                            }
                        } catch (validateErr) {
                            console.warn('[屏幕源] 验证源可用性失败，继续尝试使用保存的源:', validateErr);
                        }
                    }

                    if (selectedSourceId && window.electronDesktopCapturer) {
                        // 在Electron中使用选中的特定屏幕/窗口源
                        try {
                            S.screenCaptureStream = await navigator.mediaDevices.getUserMedia({
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
                            var fallbackSucceeded = false;

                            // 回退策略1: 尝试其他全屏源（chromeMediaSource 方式）
                            try {
                                var fallbackSources = await window.electronDesktopCapturer.getSources({
                                    types: ['screen'],
                                    thumbnailSize: { width: 1, height: 1 }
                                });
                                if (fallbackSources.length > 0) {
                                    S.screenCaptureStream = await navigator.mediaDevices.getUserMedia({
                                        audio: false,
                                        video: {
                                            mandatory: {
                                                chromeMediaSource: 'desktop',
                                                chromeMediaSourceId: fallbackSources[0].id,
                                                maxFrameRate: 1
                                            }
                                        }
                                    });
                                    S.selectedScreenSourceId = fallbackSources[0].id;
                                    try { localStorage.setItem('selectedScreenSourceId', fallbackSources[0].id); } catch (e) { }
                                    window.showStatusToast(
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
                                    S.screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
                                        video: { cursor: 'always', frameRate: 1 },
                                        audio: false,
                                    });
                                    S.selectedScreenSourceId = null;
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
                        if (S.screenCaptureStream) {
                            console.log(window.t('console.screenShareUsingSource'), selectedSourceId);
                        }
                    } else {
                        // 使用标准的getDisplayMedia（显示系统选择器）
                        try {
                            S.screenCaptureStream = await navigator.mediaDevices.getDisplayMedia({
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

            if (S.screenCaptureStream) {
                // 用户手势成功获取了流，重置自动弹窗失败标记
                S.screenCaptureAutoPromptFailed = false;
                // 正常流模式
                S.screenCaptureStreamLastUsed = Date.now();
                scheduleScreenCaptureIdleCheck();

                startScreenVideoStreaming(S.screenCaptureStream, isMobile() ? 'camera' : 'screen');

                // 当用户停止共享屏幕时
                S.screenCaptureStream.getVideoTracks()[0].onended = function () {
                    stopScreening();
                    screenButton().classList.remove('active');
                    syncFloatingScreenButtonState(false);

                    if (S.screenCaptureStream && typeof S.screenCaptureStream.getTracks === 'function') {
                        S.screenCaptureStream.getTracks().forEach(function (track) {
                            try { track.stop(); } catch (e) { }
                        });
                    }
                    S.screenCaptureStream = null;
                    S.screenCaptureStreamLastUsed = null;

                    if (S.screenCaptureStreamIdleTimer) {
                        clearTimeout(S.screenCaptureStreamIdleTimer);
                        S.screenCaptureStreamIdleTimer = null;
                    }
                };
            } else {
                // 回退策略3: 后端 pyautogui 轮询模式（所有前端流方式均失败）
                var result = await fetchBackendScreenshot();
                var backendTest = result.dataUrl;
                if (!backendTest) {
                    throw new Error('所有屏幕捕获方式均失败（含后端兜底）');
                }
                console.log('[屏幕源] 进入后端 pyautogui 轮询模式');

                // 立即发送第一帧
                if (S.socket && S.socket.readyState === WebSocket.OPEN) {
                    S.socket.send(JSON.stringify({ action: 'stream_data', data: backendTest, input_type: 'screen' }));
                }

                // 复用 videoSenderInterval，stopScreening() 可统一清理
                S.videoSenderInterval = setInterval(async function () {
                    try {
                        var r = await fetchBackendScreenshot();
                        var frame = r.dataUrl;
                        if (frame && S.socket && S.socket.readyState === WebSocket.OPEN) {
                            S.socket.send(JSON.stringify({ action: 'stream_data', data: frame, input_type: 'screen' }));
                        }
                    } catch (e) {
                        console.warn('[屏幕源] 后端轮询帧失败:', e);
                    }
                }, 1000);
            }

            micButton().disabled = true;
            muteButton().disabled = false;
            screenButton().disabled = true;
            stopButton().disabled = false;
            resetSessionButton().disabled = false;

            screenButton().classList.add('active');
            syncFloatingScreenButtonState(true);

            if (window.unlockAchievement) {
                window.unlockAchievement('ACH_SEND_IMAGE').catch(function (err) {
                    console.error('解锁发送图片成就失败:', err);
                });
            }

            try {
                if (window.stopProactiveVisionDuringSpeech) {
                    window.stopProactiveVisionDuringSpeech();
                }
            } catch (e) {
                console.warn(window.t('console.stopVoiceActiveVisionFailed'), e);
            }

            if (!S.isRecording) window.showStatusToast(window.t ? window.t('app.micNotOpen') : '没开麦啊喂！', 3000);
        } catch (err) {
            console.error(isMobile() ? window.t('console.cameraAccessFailed') : window.t('console.screenShareFailed'), err);
            console.error(window.t('console.startupFailed'), err);
            var hint = '';
            var isDesktop = !isMobile();
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
            window.showStatusToast(err.name + ': ' + err.message + (hint ? '\n' + hint : ''), 5000);
        }
    }
    mod.startScreenSharing = startScreenSharing;

    // ======================== stopScreenSharing ========================
    /**
     * 停止屏幕分享。
     * @param {boolean} forceRelease - 是否强制释放流。false时若主动视觉仍活跃则保留缓存流。
     */
    async function stopScreenSharing(forceRelease) {
        stopScreening();

        // 判断主动视觉是否活跃
        var proactiveVisionActive = S.proactiveVisionEnabled ||
            (S.proactiveVisionChatEnabled && S.proactiveChatEnabled);

        // 条件释放流
        if (forceRelease || !proactiveVisionActive) {
            // 完全释放流
            try {
                if (S.screenCaptureStream && typeof S.screenCaptureStream.getTracks === 'function') {
                    var vt = S.screenCaptureStream.getVideoTracks && S.screenCaptureStream.getVideoTracks()[0];
                    if (vt) {
                        vt.onended = null;
                    }
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) {
                console.warn(window.t('console.screenShareStopTracksFailed'), e);
            } finally {
                S.screenCaptureStream = null;
                S.screenCaptureStreamLastUsed = null;
                if (S.screenCaptureStreamIdleTimer) {
                    clearTimeout(S.screenCaptureStreamIdleTimer);
                    S.screenCaptureStreamIdleTimer = null;
                }
            }
        } else {
            // 主动视觉仍活跃，保留缓存流，仅停止发送和 UI
            console.log('[屏幕分享] 主动视觉仍活跃，保留缓存流');
        }

        // 仅在主动录像/语音连接分享时更新 UI 状态，防止闲置释放导致 UI 错误锁定
        if (S.isRecording) {
            micButton().disabled = true;
            muteButton().disabled = false;
            screenButton().disabled = false;
            stopButton().disabled = true;
            resetSessionButton().disabled = false;
            window.showStatusToast(window.t ? window.t('app.speaking') : '正在语音...', 2000);

            // 移除active类
            screenButton().classList.remove('active');
            syncFloatingScreenButtonState(false);
        } else {
            // 即使未录音，也确保按钮重置为正常状态
            screenButton().classList.remove('active');
            syncFloatingScreenButtonState(false);
        }

        // 停止手动屏幕共享后，如果满足条件则恢复语音期间主动视觉定时
        try {
            if (S.proactiveVisionEnabled && S.isRecording) {
                if (window.startProactiveVisionDuringSpeech) {
                    window.startProactiveVisionDuringSpeech();
                }
            }
        } catch (e) {
            console.warn(window.t('console.resumeVoiceActiveVisionFailed'), e);
        }
    }
    mod.stopScreenSharing = stopScreenSharing;

    // ======================== switchMicCapture ========================
    window.switchMicCapture = async function () {
        if (muteButton().disabled) {
            if (window.startMicCapture) await window.startMicCapture();
        } else {
            if (window.stopMicCapture) await window.stopMicCapture();
        }
    };

    // ======================== switchScreenSharing ========================
    window.switchScreenSharing = async function () {
        if (stopButton().disabled) {
            // 检查是否在录音状态
            if (!S.isRecording) {
                window.showStatusToast(window.t ? window.t('app.micRequired') : '请先开启麦克风录音！', 3000);
                return;
            }
            await startScreenSharing();
        } else {
            await stopScreenSharing();
        }
    };

    // ======================== selectScreenSource ========================
    async function selectScreenSource(sourceId, sourceName) {
        S.selectedScreenSourceId = sourceId;

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
        window.showStatusToast(window.t ? window.t('app.screenSource.selected', { source: sourceName }) : '已选择 ' + sourceName, 3000);

        console.log('[屏幕源] 已选择:', sourceName, '(ID:', sourceId, ')');

        // 切换窗口源时，强制释放旧的缓存流（无论是否在屏幕分享中）
        // 这确保下次获取流时使用新选择的源
        if (S.screenCaptureStream) {
            console.log('[屏幕源] 窗口选择已切换，强制释放旧缓存流');
            try {
                if (typeof S.screenCaptureStream.getTracks === 'function') {
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) { }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
            if (S.screenCaptureStreamIdleTimer) {
                clearTimeout(S.screenCaptureStreamIdleTimer);
                S.screenCaptureStreamIdleTimer = null;
            }
        }

        // 智能刷新：如果当前正在屏幕分享中，自动重启以应用新的屏幕源
        var stopBtn = document.getElementById('stopButton');
        var isScreenSharingActive = stopBtn && !stopBtn.disabled;

        if (isScreenSharingActive && window.switchScreenSharing) {
            console.log('[屏幕源] 检测到正在屏幕分享中，将自动重启以应用新源');
            // 先停止当前分享（流已释放，forceRelease 无所谓）
            await stopScreenSharing(true);
            // 等待一小段时间
            await new Promise(function (resolve) { setTimeout(resolve, 300); });
            // 重新开始分享（使用新选择的源）
            await startScreenSharing();
        }
    }
    mod.selectScreenSource = selectScreenSource;

    // ======================== updateScreenSourceListSelection ========================
    function updateScreenSourceListSelection() {
        var screenPopup = document.getElementById('live2d-popup-screen');
        if (!screenPopup) return;

        var options = screenPopup.querySelectorAll('.screen-source-option');
        options.forEach(function (option) {
            var sourceId = option.dataset.sourceId;
            var isSelected = sourceId === S.selectedScreenSourceId;

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
    mod.updateScreenSourceListSelection = updateScreenSourceListSelection;

    // ======================== renderFloatingScreenSourceList ========================
    window.renderFloatingScreenSourceList = async function () {
        var screenPopup = document.getElementById('live2d-popup-screen');
        if (!screenPopup) {
            console.warn('[屏幕源] 弹出框不存在');
            return false;
        }

        // 检查是否在Electron环境
        if (!window.electronDesktopCapturer || !window.electronDesktopCapturer.getSources) {
            screenPopup.innerHTML = '';
            var notAvailableItem = document.createElement('div');
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
            var loadingItem = document.createElement('div');
            loadingItem.textContent = window.t ? window.t('app.screenSource.loading') : '加载中...';
            loadingItem.style.padding = '12px';
            loadingItem.style.color = 'var(--neko-popup-text-sub)';
            loadingItem.style.fontSize = '13px';
            loadingItem.style.textAlign = 'center';
            screenPopup.appendChild(loadingItem);

            // 获取屏幕源
            var sources = await window.electronDesktopCapturer.getSources({
                types: ['window', 'screen'],
                thumbnailSize: { width: 160, height: 100 }
            });

            screenPopup.innerHTML = '';

            if (!sources || sources.length === 0) {
                var noSourcesItem = document.createElement('div');
                noSourcesItem.textContent = window.t ? window.t('app.screenSource.noSources') : '没有可用的屏幕源';
                noSourcesItem.style.padding = '12px';
                noSourcesItem.style.color = 'var(--neko-popup-text-sub)';
                noSourcesItem.style.fontSize = '13px';
                noSourcesItem.style.textAlign = 'center';
                screenPopup.appendChild(noSourcesItem);
                return false;
            }

            // 分组：屏幕和窗口
            var screens = sources.filter(function (s) { return s.id.startsWith('screen:'); });
            var windows = sources.filter(function (s) { return s.id.startsWith('window:'); });

            // 创建网格容器的辅助函数
            function createGridContainer() {
                var grid = document.createElement('div');
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
                var option = document.createElement('div');
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

                if (S.selectedScreenSourceId === source.id) {
                    option.classList.add('selected');
                    option.style.background = 'var(--neko-popup-selected-bg)';
                    option.style.borderColor = '#4f8cff';
                }

                // 缩略图（带异常处理和占位图回退）
                if (source.thumbnail) {
                    var thumb = document.createElement('img');
                    var thumbnailDataUrl = '';
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
                    thumb.onerror = function () {
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
                    var iconPlaceholder = document.createElement('div');
                    iconPlaceholder.textContent = source.id.startsWith('screen:') ? '\uD83D\uDDA5\uFE0F' : '\uD83E\uDE9F';
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
                var label = document.createElement('span');
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

                option.addEventListener('click', async function (e) {
                    e.stopPropagation();
                    await selectScreenSource(source.id, source.name);
                });

                option.addEventListener('mouseenter', function () {
                    if (!option.classList.contains('selected')) {
                        option.style.background = 'var(--neko-popup-hover)';
                    }
                });
                option.addEventListener('mouseleave', function () {
                    if (!option.classList.contains('selected')) {
                        option.style.background = 'transparent';
                    }
                });

                return option;
            }

            // 添加屏幕列表（网格布局）
            if (screens.length > 0) {
                var screenLabel = document.createElement('div');
                screenLabel.textContent = window.t ? window.t('app.screenSource.screens') : '屏幕';
                Object.assign(screenLabel.style, {
                    padding: '4px 8px',
                    fontSize: '11px',
                    color: 'var(--neko-popup-text-sub)',
                    fontWeight: '600',
                    textTransform: 'uppercase'
                });
                screenPopup.appendChild(screenLabel);

                var screenGrid = createGridContainer();
                screens.forEach(function (source) {
                    screenGrid.appendChild(createSourceOption(source));
                });
                screenPopup.appendChild(screenGrid);
            }

            // 添加窗口列表（网格布局）
            if (windows.length > 0) {
                var windowLabel = document.createElement('div');
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

                var windowGrid = createGridContainer();
                windows.forEach(function (source) {
                    windowGrid.appendChild(createSourceOption(source));
                });
                screenPopup.appendChild(windowGrid);
            }

            return true;
        } catch (error) {
            console.error('[屏幕源] 获取屏幕源失败:', error);
            screenPopup.innerHTML = '';
            var errorItem = document.createElement('div');
            errorItem.textContent = window.t ? window.t('app.screenSource.loadFailed') : '获取屏幕源失败';
            errorItem.style.padding = '12px';
            errorItem.style.color = '#dc3545';
            errorItem.style.fontSize = '13px';
            errorItem.style.textAlign = 'center';
            screenPopup.appendChild(errorItem);
            return false;
        }
    };

    // ======================== getSelectedScreenSourceId ========================
    window.getSelectedScreenSourceId = function () { return S.selectedScreenSourceId; };

    // ======================== Backward-compat window exports ========================
    window.startScreenSharing = startScreenSharing;
    window.stopScreenSharing = stopScreenSharing;
    window.selectScreenSource = selectScreenSource;
    window.captureCanvasFrame = captureCanvasFrame;
    window.captureFrameFromStream = captureFrameFromStream;
    window.acquireOrReuseCachedStream = acquireOrReuseCachedStream;
    window.fetchBackendScreenshot = fetchBackendScreenshot;
    window.getMobileCameraStream = getMobileCameraStream;
    window.startScreenVideoStreaming = startScreenVideoStreaming;
    window.stopScreening = stopScreening;
    window.scheduleScreenCaptureIdleCheck = scheduleScreenCaptureIdleCheck;
    window.syncFloatingScreenButtonState = syncFloatingScreenButtonState;

    // ======================== Export module ========================
    window.appScreen = mod;
})();
