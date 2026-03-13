/**
 * app-proactive.js — 主动搭话（Proactive Chat）模块
 *
 * 包含：
 *   - syncProactiveFlags (no-op, 由 app-state.js defineProperty 桥接代替)
 *   - hasAnyChatModeEnabled / canTriggerProactively
 *   - scheduleProactiveChat / stopProactiveChatSchedule
 *   - triggerProactiveChat / _showProactiveChatSourceLinks
 *   - resetProactiveChatBackoff
 *   - getAvailablePersonalPlatforms
 *   - sendOneProactiveVisionFrame
 *   - startProactiveVisionDuringSpeech / stopProactiveVisionDuringSpeech
 *   - captureProactiveChatScreenshot / acquireProactiveVisionStream / releaseProactiveVisionStream
 *   - isWindowsOS (helper)
 *   - captureCanvasFrame / captureFrameFromStream / acquireOrReuseCachedStream (screen-capture helpers)
 *   - fetchBackendScreenshot / scheduleScreenCaptureIdleCheck (screen-capture helpers)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;

    // ======================== screen-capture helpers (delegate to app-screen.js) ========================

    function captureCanvasFrame(video, jpegQuality, detectBlack) {
        return window.appScreen.captureCanvasFrame(video, jpegQuality, detectBlack);
    }

    function captureFrameFromStream(stream, jpegQuality) {
        return window.appScreen.captureFrameFromStream(stream, jpegQuality);
    }

    function acquireOrReuseCachedStream(opts) {
        return window.appScreen.acquireOrReuseCachedStream(opts);
    }

    function fetchBackendScreenshot() {
        return window.appScreen.fetchBackendScreenshot();
    }

    function scheduleScreenCaptureIdleCheck() {
        return window.appScreen.scheduleScreenCaptureIdleCheck();
    }

    // ======================== syncProactiveFlags (no-op) ========================
    // app-state.js 使用 Object.defineProperty 进行双向绑定，
    // 因此不再需要手动同步 window.xxx <-> 本地变量。
    function syncProactiveFlags() {
        // no-op: bridged by app-state.js defineProperty
    }

    // ======================== proactive chat core ========================

    /**
     * 检查是否有任何搭话方式被选中
     */
    function hasAnyChatModeEnabled() {
        return S.proactiveVisionChatEnabled || S.proactiveNewsChatEnabled ||
               S.proactiveVideoChatEnabled || S.proactivePersonalChatEnabled ||
               S.proactiveMusicEnabled;
    }
    mod.hasAnyChatModeEnabled = hasAnyChatModeEnabled;

    /**
     * 检查主动搭话前置条件是否满足
     */
    function canTriggerProactively() {
        // 必须开启主动搭话
        if (!S.proactiveChatEnabled) {
            return false;
        }

        // 必须选择至少一种搭话方式
        if (!S.proactiveVisionChatEnabled && !S.proactiveNewsChatEnabled &&
            !S.proactiveVideoChatEnabled && !S.proactivePersonalChatEnabled &&
            !S.proactiveMusicEnabled) {
            return false;
        }

        // 如果只选择了视觉搭话，需要同时开启自主视觉
        if (S.proactiveVisionChatEnabled && !S.proactiveNewsChatEnabled &&
            !S.proactiveVideoChatEnabled && !S.proactivePersonalChatEnabled &&
            !S.proactiveMusicEnabled) {
            return S.proactiveVisionEnabled;
        }

        // 如果只选择了个人动态搭话，需要同时开启个人动态
        if (!S.proactiveVisionChatEnabled && !S.proactiveNewsChatEnabled &&
            !S.proactiveVideoChatEnabled && S.proactivePersonalChatEnabled &&
            !S.proactiveMusicEnabled) {
            return S.proactivePersonalChatEnabled;
        }

        // 音乐搭话不需要额外条件，总是允许
        return true;
    }
    mod.canTriggerProactively = canTriggerProactively;

    /**
     * 主动搭话定时触发功能
     */
    function scheduleProactiveChat() {
        // 清除现有定时器
        if (S.proactiveChatTimer) {
            clearTimeout(S.proactiveChatTimer);
            S.proactiveChatTimer = null;
        }

        // 必须开启主动搭话且选择至少一种搭话方式才启动调度
        if (!S.proactiveChatEnabled || !hasAnyChatModeEnabled()) {
            S.proactiveChatBackoffLevel = 0;
            return;
        }

        // 前置条件检查：如果不满足触发条件，不启动调度器并重置退避
        if (!canTriggerProactively()) {
            console.log('主动搭话前置条件不满足，不启动调度器');
            S.proactiveChatBackoffLevel = 0;
            return;
        }

        // 如果主动搭话正在执行中，不安排新的定时器（等当前执行完成后自动安排）
        if (S.isProactiveChatRunning) {
            console.log('主动搭话正在执行中，延迟安排下一次');
            return;
        }

        // 只在非语音模式下执行（语音模式下不触发主动搭话）
        // 文本模式或待机模式都可以触发主动搭话
        if (S.isRecording) {
            console.log('语音模式中，不安排主动搭话');
            return;
        }

        // 计算延迟时间（指数退避，倍率2.5）
        var delay = (S.proactiveChatInterval * 1000) * Math.pow(2.5, S.proactiveChatBackoffLevel);
        console.log('主动搭话：' + (delay / 1000) + '秒后触发（基础间隔：' + S.proactiveChatInterval + '秒，退避级别：' + S.proactiveChatBackoffLevel + '）');

        S.proactiveChatTimer = setTimeout(async function () {
            // 双重检查锁：定时器触发时再次检查是否正在执行
            if (S.isProactiveChatRunning) {
                console.log('主动搭话定时器触发时发现正在执行中，跳过本次');
                return;
            }

            console.log('触发主动搭话...');
            S.isProactiveChatRunning = true; // 加锁

            try {
                await triggerProactiveChat();
            } finally {
                S.isProactiveChatRunning = false; // 解锁
            }

            // 增加退避级别（最多到约7分钟，即level 3：30s * 2.5^3 = 7.5min）
            if (S.proactiveChatBackoffLevel < 3) {
                S.proactiveChatBackoffLevel++;
            }

            // 安排下一次
            scheduleProactiveChat();
        }, delay);
    }
    mod.scheduleProactiveChat = scheduleProactiveChat;

    // ======================== getAvailablePersonalPlatforms ========================

    /**
     * 获取个人媒体cookies所有可用平台的函数
     */
    async function getAvailablePersonalPlatforms() {
        try {
            var response = await fetch('/api/auth/cookies/status');
            if (!response.ok) return [];

            var result = await response.json();
            var availablePlatforms = [];

            if (result.success && result.data) {
                for (var _ref of Object.entries(result.data)) {
                    var platform = _ref[0];
                    var info = _ref[1];
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
    mod.getAvailablePersonalPlatforms = getAvailablePersonalPlatforms;

    // ======================== triggerProactiveChat ========================

    async function triggerProactiveChat() {
        try {
            var availableModes = [];
            // 收集所有启用的搭话方式
            // 视觉搭话：需要同时开启主动搭话和自主视觉
            // 同时触发 vision 和 window 模式
            if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled && S.proactiveVisionEnabled) {
                availableModes.push('vision');
                availableModes.push('window');
            }

            // 新闻搭话：使用微博热议话题
            if (S.proactiveNewsChatEnabled && S.proactiveChatEnabled) {
                availableModes.push('news');
            }

            // 视频搭话：使用B站首页视频
            if (S.proactiveVideoChatEnabled && S.proactiveChatEnabled) {
                availableModes.push('video');
            }

            // 个人动态搭话：使用B站和微博个人动态
            if (S.proactivePersonalChatEnabled && S.proactiveChatEnabled) {
                // 检查是否有可用的 Cookie 凭证
                var platforms = await getAvailablePersonalPlatforms();
                if (platforms.length > 0) {
                    availableModes.push('personal');
                    console.log('[个人动态] 模式已启用，平台: ' + platforms.join(', '));
                } else {
                    // 如果开关开了但没登录，不把 personal 发给后端，避免后端抓取失败报错
                    console.warn('[个人动态] 开关已开启但未检测到登录凭证，已忽略此模式');
                }
            }

            // 音乐搭话
            console.log('[ProactiveChat] 检查音乐模式: proactiveMusicEnabled=' + S.proactiveMusicEnabled + ', proactiveChatEnabled=' + S.proactiveChatEnabled);
            if (S.proactiveMusicEnabled && S.proactiveChatEnabled) {
                console.log('[ProactiveChat] 音乐模式已启用');
                availableModes.push('music');
            }

            // 如果没有选择任何搭话方式，跳过本次搭话
            if (availableModes.length === 0) {
                console.log('未选择任何搭话方式，跳过本次搭话');
                return;
            }

            console.log('主动搭话：启用模式 [' + availableModes.join(', ') + ']，将并行获取所有信息源');

            var lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
            var requestBody = {
                lanlan_name: lanlanName,
                enabled_modes: availableModes,
                is_playing_music: (typeof window.isMusicPlaying === 'function') ? window.isMusicPlaying() : false,
                current_track: (typeof window.getMusicCurrentTrack === 'function') ? window.getMusicCurrentTrack() : null
            };

            // 如果包含 vision 模式，需要在前端获取截图和窗口标题
            if (availableModes.includes('vision') || availableModes.includes('window')) {
                var fetchTasks = [];
                var screenshotIndex = -1;
                var windowTitleIndex = -1;

                if (availableModes.includes('vision')) {
                    screenshotIndex = fetchTasks.length;
                    fetchTasks.push(captureProactiveChatScreenshot());
                }

                if (availableModes.includes('window')) {
                    windowTitleIndex = fetchTasks.length;
                    fetchTasks.push(fetch('/api/get_window_title')
                        .then(function (r) { return r.json(); })
                        .catch(function () { return { success: false }; }));
                }

                var results = await Promise.all(fetchTasks);

                // await 期间检查状态
                if (!canTriggerProactively()) {
                    console.log('功能已关闭或前置条件不满足，取消本次搭话');
                    return;
                }

                // await 期间用户可能切换模式，重新过滤可用模式
                var latestModes = [];
                if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled && S.proactiveVisionEnabled) {
                    latestModes.push('vision', 'window');
                }
                if (S.proactiveNewsChatEnabled && S.proactiveChatEnabled) {
                    latestModes.push('news');
                }
                if (S.proactiveVideoChatEnabled && S.proactiveChatEnabled) {
                    latestModes.push('video');
                }
                // 个人动态搭话：需要同时开启个人动态
                if (S.proactivePersonalChatEnabled && S.proactiveChatEnabled) {
                    latestModes.push('personal');
                }
                // 音乐搭话
                if (S.proactiveMusicEnabled && S.proactiveChatEnabled) {
                    latestModes.push('music');
                }
                availableModes = availableModes.filter(function (m) { return latestModes.includes(m); });
                requestBody.enabled_modes = availableModes;
                if (availableModes.length === 0) {
                    console.log('await后无可用模式，取消本次搭话');
                    return;
                }

                if (screenshotIndex !== -1 && availableModes.includes('vision')) {
                    var screenshotDataUrl = results[screenshotIndex];
                    if (screenshotDataUrl) {
                        requestBody.screenshot_data = screenshotDataUrl;
                        if (window.unlockAchievement) {
                            window.unlockAchievement('ACH_SEND_IMAGE').catch(function (err) {
                                console.error('解锁发送图片成就失败:', err);
                            });
                        }
                    } else {
                        // 截图失败，从 enabled_modes 中移除 vision
                        console.log('截图失败，移除 vision 模式');
                        availableModes = availableModes.filter(function (m) { return m !== 'vision'; });
                        requestBody.enabled_modes = availableModes;
                    }
                }

                if (windowTitleIndex !== -1 && availableModes.includes('window')) {
                    var windowTitleResult = results[windowTitleIndex];
                    if (windowTitleResult && windowTitleResult.success && windowTitleResult.window_title) {
                        requestBody.window_title = windowTitleResult.window_title;
                        console.log('视觉搭话附加窗口标题:', windowTitleResult.window_title);
                    } else {
                        // 窗口标题获取失败，从 enabled_modes 中移除 window
                        console.log('窗口标题获取失败，移除 window 模式');
                        availableModes = availableModes.filter(function (m) { return m !== 'window'; });
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
            var timeSinceLastInput = Date.now() - (window.lastUserInputTime || 0);
            if (timeSinceLastInput < 20000) {
                console.log('主动搭话作废：用户在' + Math.round(timeSinceLastInput / 1000) + '秒前有过输入');
                return;
            }

            var response = await fetch('/api/proactive_chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(requestBody)
            });

            var result = await response.json();

            if (result.success) {
                if (result.action === 'chat') {
                    console.log('主动搭话已发送:', result.message, result.source_mode ? '(来源: ' + result.source_mode + ')' : '');

                    var dispatchedTrackUrl = null;

                    // 如果模式包含音乐信号，尝试播放第一条音轨
                    if ((result.source_mode === 'music' || result.source_mode === 'both') && result.source_links && Array.isArray(result.source_links)) {
                        // 优先寻找有 artist 字段或标记为音乐推荐的真实音轨
                        var normalizedLinks = result.source_links.filter(Boolean);
                        var musicLink = normalizedLinks.find(function (link) { return link && (link.artist || link.source === '音乐推荐'); }) || normalizedLinks[0];

                        if (musicLink && musicLink.url) {
                            console.log('[ProactiveChat] 收到音乐链接:', musicLink);
                            var track = {
                                name: musicLink.title || '未知曲目',
                                artist: musicLink.artist || '未知艺术家',
                                url: musicLink.url,
                                cover: musicLink.cover
                            };
                            console.log('[ProactiveChat] 发送音乐消息:', track);
                            var dispatchResult = window.dispatchMusicPlay(track, { source: 'proactive' });

                            // 仅在成功派发（非拦截）时标记，以便在聊天区域隐藏对应链接
                            if (dispatchResult !== false) {
                                dispatchedTrackUrl = musicLink.url;
                            }
                        } else if (musicLink) {
                            console.warn('[ProactiveChat] 音乐链接缺少URL:', musicLink);
                        }
                    }

                    // 无论 source_mode 是什么，只要有链接就尝试显示（音乐推荐链接除外）
                    if (result.source_links && result.source_links.length > 0) {
                        setTimeout(function () {
                            _showProactiveChatSourceLinks(result.source_links, dispatchedTrackUrl);
                        }, 3000);
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
    mod.triggerProactiveChat = triggerProactiveChat;

    // ======================== source link card ========================

    /**
     * 在聊天区域临时显示来源链接卡片（旁路，不进入 AI 记忆）
     */
    function _showProactiveChatSourceLinks(links, dispatchedUrl) {
        try {
            var chatContent = document.getElementById('chat-content-wrapper');
            if (!chatContent) return;

            // 鲁棒的 URL 比较函数
            var isSameUrl = function (u1, u2) {
                if (!u1 || !u2) return false;
                if (u1 === u2) return true;
                try {
                    var url1 = new URL(u1, window.location.origin);
                    var url2 = new URL(u2, window.location.origin);
                    var getRef = function (u) { return (u.hostname + u.pathname.replace(/\/$/, '') + u.search).toLowerCase(); };
                    return getRef(url1) === getRef(url2);
                } catch (e) { return u1 === u2; }
            };

            var validLinks = [];
            for (var i = 0; i < links.length; i++) {
                var link = links[i];

                // 跳过 null/undefined 条目
                if (!link) continue;

                // 所有的音乐推荐链接都不显示在聊天框中（由播放器统一处理）
                var isMusicLink = link.artist || link.source === '音乐推荐' || (dispatchedUrl && isSameUrl(link.url, dispatchedUrl));
                if (isMusicLink) continue;

                var safeUrl = null;
                try {
                    var u = new URL(String(link.url || ''), window.location.origin);
                    if (u.protocol === 'http:' || u.protocol === 'https:') {
                        safeUrl = u.href;
                    }
                } catch (e) {
                    console.warn('解析链接失败:', e);
                }
                if (safeUrl) {
                    validLinks.push(Object.assign({}, link, { safeUrl: safeUrl }));
                }
            }

            if (validLinks.length === 0) return;

            // 超过 3 个旧卡片时，移除最早的
            var MAX_LINK_CARDS = 3;
            var existingCards = chatContent.querySelectorAll('.proactive-source-link-card');
            var overflow = existingCards.length - MAX_LINK_CARDS + 1;
            if (overflow > 0) {
                for (var j = 0; j < overflow; j++) {
                    existingCards[j].remove();
                }
            }

            var linkCard = document.createElement('div');
            linkCard.className = 'proactive-source-link-card';
            linkCard.style.cssText =
                'margin: 6px 12px;' +
                'padding: 8px 14px;' +
                'background: var(--bg-secondary, rgba(255,255,255,0.08));' +
                'border-left: 3px solid var(--accent-color, #6c8cff);' +
                'border-radius: 8px;' +
                'font-size: 12px;' +
                'opacity: 0;' +
                'transition: opacity 0.4s ease;' +
                'max-width: 320px;' +
                'position: relative;';

            var closeBtn = document.createElement('span');
            closeBtn.textContent = '\u2715'; // ✕
            closeBtn.style.cssText =
                'position: absolute;' +
                'top: 6px;' +
                'right: 6px;' +
                'cursor: pointer;' +
                'color: var(--text-secondary, rgba(200,200,200,0.8));' +
                'font-size: 14px;' +
                'font-weight: bold;' +
                'line-height: 1;' +
                'width: 20px;' +
                'height: 20px;' +
                'display: flex;' +
                'align-items: center;' +
                'justify-content: center;' +
                'border-radius: 50%;' +
                'background: rgba(255,255,255,0.08);' +
                'transition: color 0.2s, background 0.2s;' +
                'z-index: 1;';
            closeBtn.addEventListener('mouseenter', function () {
                closeBtn.style.color = '#fff';
                closeBtn.style.background = 'rgba(255,255,255,0.2)';
            });
            closeBtn.addEventListener('mouseleave', function () {
                closeBtn.style.color = 'var(--text-secondary, rgba(200,200,200,0.8))';
                closeBtn.style.background = 'rgba(255,255,255,0.08)';
            });
            closeBtn.addEventListener('click', function () {
                linkCard.style.opacity = '0';
                setTimeout(function () { linkCard.remove(); }, 300);
            });
            linkCard.appendChild(closeBtn);

            for (var k = 0; k < validLinks.length; k++) {
                (function (vl) {
                    var a = document.createElement('a');
                    a.href = vl.safeUrl;
                    a.textContent = '\uD83D\uDD17 ' + (vl.source ? '[' + vl.source + '] ' : '') + (vl.title || vl.url);
                    a.style.cssText =
                        'display: block;' +
                        'color: var(--accent-color, #6c8cff);' +
                        'text-decoration: none;' +
                        'padding: 3px 0;' +
                        'padding-right: 20px;' +
                        'word-break: break-all;' +
                        'font-size: 12px;' +
                        'cursor: pointer;';
                    a.addEventListener('mouseenter', function () { a.style.textDecoration = 'underline'; });
                    a.addEventListener('mouseleave', function () { a.style.textDecoration = 'none'; });
                    a.addEventListener('click', function (e) {
                        e.preventDefault();
                        if (window.electronShell && window.electronShell.openExternal) {
                            window.electronShell.openExternal(vl.safeUrl);
                        } else {
                            window.open(vl.safeUrl, '_blank', 'noopener,noreferrer');
                        }
                    });
                    linkCard.appendChild(a);
                })(validLinks[k]);
            }

            chatContent.appendChild(linkCard);
            chatContent.scrollTop = chatContent.scrollHeight;

            requestAnimationFrame(function () { linkCard.style.opacity = '1'; });

            setTimeout(function () {
                linkCard.style.opacity = '0';
                setTimeout(function () { linkCard.remove(); }, 500);
            }, 5 * 60 * 1000);

            console.log('已显示主动搭话来源链接:', validLinks.length, '条');
        } catch (e) {
            console.warn('显示来源链接失败:', e);
        }
    }
    mod._showProactiveChatSourceLinks = _showProactiveChatSourceLinks;

    // ======================== backoff reset ========================

    function resetProactiveChatBackoff() {
        // 重置退避级别
        S.proactiveChatBackoffLevel = 0;
        // 重新安排定时器
        scheduleProactiveChat();
    }
    mod.resetProactiveChatBackoff = resetProactiveChatBackoff;

    // ======================== proactive vision during speech ========================

    /**
     * 发送单帧屏幕数据（统一使用 acquireOrReuseCachedStream → captureFrameFromStream → 后端兜底）
     */
    async function sendOneProactiveVisionFrame() {
        try {
            if (!S.socket || S.socket.readyState !== WebSocket.OPEN) return;

            var dataUrl = null;

            // 优先前端流（缓存流 → Electron源 → 不弹窗）
            var stream = await acquireOrReuseCachedStream({ allowPrompt: false });
            if (stream) {
                var frame = await captureFrameFromStream(stream, 0.8);
                if (frame && frame.dataUrl) {
                    dataUrl = frame.dataUrl;
                } else if (S.screenCaptureStream === stream) {
                    // 空帧（黑帧或空壳流），废弃缓存流
                    console.warn('[ProactiveVision] 缓存流提取帧失败，废弃该流');
                    try { stream.getTracks().forEach(function (t) { try { t.stop(); } catch (e) { } }); } catch (e) { }
                    S.screenCaptureStream = null;
                    S.screenCaptureStreamLastUsed = null;
                }
            }

            // 后端 pyautogui 兜底
            if (!dataUrl) {
                var backendResult = await fetchBackendScreenshot();
                dataUrl = backendResult.dataUrl;
                // macOS 403 权限提示
                if (backendResult.status === 403 && !S.screenRecordingPermissionHintShown) {
                    S.screenRecordingPermissionHintShown = true;
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast('\u26A0\uFE0F 屏幕录制权限未授权，请在系统设置中允许屏幕录制', 6000);
                    }
                    console.warn('[ProactiveVision] 后端截图返回 403，请在"系统设置 → 隐私与安全性 → 屏幕录制"中授权 N.E.K.O');
                }
            }

            if (dataUrl && S.socket && S.socket.readyState === WebSocket.OPEN) {
                S.socket.send(JSON.stringify({
                    action: 'stream_data',
                    data: dataUrl,
                    input_type: (window.appUtils && window.appUtils.isMobile) ? (window.appUtils.isMobile() ? 'camera' : 'screen') : 'screen'
                }));
                console.log('[ProactiveVision] 发送单帧屏幕数据');
            }
        } catch (e) {
            console.error('sendOneProactiveVisionFrame 失败:', e);
        }
    }
    mod.sendOneProactiveVisionFrame = sendOneProactiveVisionFrame;

    function startProactiveVisionDuringSpeech() {
        // 如果已有定时器先清理
        if (S.proactiveVisionFrameTimer) {
            clearInterval(S.proactiveVisionFrameTimer);
            S.proactiveVisionFrameTimer = null;
        }

        // 仅在条件满足时启动：已开启主动视觉 && 正在录音 && 未手动屏幕共享
        if (!S.proactiveVisionEnabled || !S.isRecording) return;
        var screenButton = document.getElementById('screenButton');
        if (screenButton && screenButton.classList.contains('active')) return; // 手动共享时不启动

        S.proactiveVisionFrameTimer = setInterval(async function () {
            // 在每次执行前再做一次检查，避免竞态
            if (!S.proactiveVisionEnabled || !S.isRecording) {
                stopProactiveVisionDuringSpeech();
                return;
            }

            // 如果手动开启了屏幕共享，重置计数器（即跳过发送）
            var sb = document.getElementById('screenButton');
            if (sb && sb.classList.contains('active')) {
                // do nothing this tick, just wait for next interval
                return;
            }

            await sendOneProactiveVisionFrame();
        }, S.proactiveVisionInterval * 1000);
    }
    mod.startProactiveVisionDuringSpeech = startProactiveVisionDuringSpeech;

    function stopProactiveVisionDuringSpeech() {
        if (S.proactiveVisionFrameTimer) {
            clearInterval(S.proactiveVisionFrameTimer);
            S.proactiveVisionFrameTimer = null;
        }
    }
    mod.stopProactiveVisionDuringSpeech = stopProactiveVisionDuringSpeech;

    function stopProactiveChatSchedule() {
        if (S.proactiveChatTimer) {
            clearTimeout(S.proactiveChatTimer);
            S.proactiveChatTimer = null;
        }
    }
    mod.stopProactiveChatSchedule = stopProactiveChatSchedule;

    // ======================== isWindowsOS ========================

    /**
     * 安全的Windows系统检测函数
     * 优先使用 navigator.userAgentData，然后 fallback 到 navigator.userAgent，最后才用已弃用的 navigator.platform
     * @returns {boolean} 是否为Windows系统
     */
    function isWindowsOS() {
        try {
            // 优先使用现代 API（如果支持）
            if (navigator.userAgentData && navigator.userAgentData.platform) {
                var platform = navigator.userAgentData.platform.toLowerCase();
                return platform.includes('win');
            }

            // Fallback 到 userAgent 字符串检测
            if (navigator.userAgent) {
                var ua = navigator.userAgent.toLowerCase();
                return ua.includes('win');
            }

            // 最后的兼容方案：使用已弃用的 platform API
            if (navigator.platform) {
                var plat = navigator.platform.toLowerCase();
                return plat.includes('win');
            }

            // 如果所有方法都不可用，默认返回false
            return false;
        } catch (error) {
            console.error('Windows检测失败:', error);
            return false;
        }
    }
    mod.isWindowsOS = isWindowsOS;

    // ======================== captureProactiveChatScreenshot ========================

    /**
     * 主动搭话截图函数（优先缓存流/Electron源 → getDisplayMedia → pyautogui 兜底）
     */
    async function captureProactiveChatScreenshot() {
        // 策略1: 缓存流 / Electron窗口ID / getDisplayMedia（非user gesture不弹窗）
        var stream = await acquireOrReuseCachedStream({ allowPrompt: false });
        if (stream) {
            var frame = await captureFrameFromStream(stream, 0.85);
            if (frame && frame.dataUrl) {
                console.log('[主动搭话截图] 前端截图成功');
                return frame.dataUrl;
            }
            // 黑帧或抓帧失败 → 废弃流，重试一次
            console.warn('[主动搭话截图] 帧提取失败或纯黑帧，废弃缓存流并重试');
            if (S.screenCaptureStream === stream) {
                try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
                S.screenCaptureStream = null;
                S.screenCaptureStreamLastUsed = null;
            }
            // 重试：会走 Electron sourceId 路径
            stream = await acquireOrReuseCachedStream({ allowPrompt: false });
            if (stream) {
                frame = await captureFrameFromStream(stream, 0.85);
                if (frame && frame.dataUrl) return frame.dataUrl;
                // 二次重试仍然失败，废弃这个流
                console.warn('[主动搭话截图] 二次重试仍失败，废弃流');
                if (S.screenCaptureStream === stream) {
                    try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
                    S.screenCaptureStream = null;
                    S.screenCaptureStreamLastUsed = null;
                }
            }
        }

        // 策略2: 后端 pyautogui 兜底
        var backendResult = await fetchBackendScreenshot();
        if (backendResult.dataUrl) {
            console.log('[主动搭话截图] 后端截图成功');
            return backendResult.dataUrl;
        }

        console.warn('[主动搭话截图] 所有截图方式均失败');
        return null;
    }
    mod.captureProactiveChatScreenshot = captureProactiveChatScreenshot;

    // ======================== acquireProactiveVisionStream ========================

    /**
     * 主动视觉开关切换时的流生命周期管理
     * 开启时：优先测试后端 pyautogui（静默无弹窗），不可用则通过前端流获取（用户手势上下文可弹 getDisplayMedia）
     */
    async function acquireProactiveVisionStream() {
        // 策略1: 测试后端 pyautogui 是否可用（静默，无弹窗）
        var backendResult = await fetchBackendScreenshot();
        if (backendResult.dataUrl) {
            console.log('[主动视觉] 后端 pyautogui 可用，无需前端流');
            return true;
        }

        // 策略2: 后端不可用，尝试前端流（用户手势上下文，可弹 getDisplayMedia）
        var stream = await acquireOrReuseCachedStream({ allowPrompt: true });
        if (stream) {
            console.log('[主动视觉] 前端流获取/复用成功');
            return true;
        }

        console.warn('[主动视觉] 无可用的截图方式');
        return false;
    }
    mod.acquireProactiveVisionStream = acquireProactiveVisionStream;

    // ======================== releaseProactiveVisionStream ========================

    function releaseProactiveVisionStream() {
        // 如果用户手动开启了屏幕共享，不要释放流
        var screenButton = document.getElementById('screenButton');
        if (screenButton && screenButton.classList.contains('active')) {
            console.log('[主动视觉] 手动屏幕共享活跃中，不释放流');
            return;
        }

        // 如果正在录音（语音模式），流可能正在被使用，不释放
        if (S.isRecording) {
            console.log('[主动视觉] 语音模式活跃中，不释放流');
            return;
        }

        // 如果主动搭话+主动视觉Chat仍活跃，保留流
        if (S.proactiveVisionChatEnabled && S.proactiveChatEnabled) {
            console.log('[主动视觉] 主动搭话视觉仍活跃，不释放流');
            return;
        }

        if (S.screenCaptureStream) {
            try {
                if (typeof S.screenCaptureStream.getTracks === 'function') {
                    S.screenCaptureStream.getTracks().forEach(function (track) {
                        try { track.stop(); } catch (e) { }
                    });
                }
            } catch (e) {
                console.warn('[主动视觉] 停止 tracks 失败:', e);
            }
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
            if (S.screenCaptureStreamIdleTimer) {
                clearTimeout(S.screenCaptureStreamIdleTimer);
                S.screenCaptureStreamIdleTimer = null;
            }
            console.log('[主动视觉] 屏幕流已释放');
        }
    }
    mod.releaseProactiveVisionStream = releaseProactiveVisionStream;

    // ======================== backward-compat window exports ========================

    window.hasAnyChatModeEnabled = hasAnyChatModeEnabled;
    window.resetProactiveChatBackoff = resetProactiveChatBackoff;
    window.stopProactiveChatSchedule = stopProactiveChatSchedule;
    window.startProactiveVisionDuringSpeech = startProactiveVisionDuringSpeech;
    window.stopProactiveVisionDuringSpeech = stopProactiveVisionDuringSpeech;
    window.acquireProactiveVisionStream = acquireProactiveVisionStream;
    window.releaseProactiveVisionStream = releaseProactiveVisionStream;
    window.scheduleProactiveChat = scheduleProactiveChat;
    window.captureCanvasFrame = captureCanvasFrame;
    window.fetchBackendScreenshot = fetchBackendScreenshot;
    window.scheduleScreenCaptureIdleCheck = scheduleScreenCaptureIdleCheck;
    window.captureProactiveChatScreenshot = captureProactiveChatScreenshot;
    window.isWindowsOS = isWindowsOS;
    window.getAvailablePersonalPlatforms = getAvailablePersonalPlatforms;

    // ======================== module export ========================

    window.appProactive = mod;
})();
