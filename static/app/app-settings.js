/**
 * app-settings.js — 设置保存/加载模块
 * 负责 saveSettings / loadSettings、地区检测、设置迁移
 * 依赖: app-state.js (window.appState, window.appConst, window.appUtils)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;
    const U = window.appUtils;

    // ======================== 内部辅助 ========================

    // 定时同步到服务器的 timer ID
    let _syncTimerId = null;
    // 同步间隔（毫秒）：60秒
    const SYNC_INTERVAL_MS = 60000;
    // 「首启等 settings/telemetry 决议」专属 marker：只有 localStorage 走过首启分支才会写
    // 「1」，branch 决议后清掉。用 marker 在不在判断「是否仍在等待首次决议」，避免拿
    // 「没见过 branch 」当首启代名——升级用户也都没见过 branch，那个口径会误伤他们的
    // 既有偏好。offline 首启错过 branch 时 marker 留着，下次在线再补
    const _FIRST_LAUNCH_PENDING_KEY = '_neko_first_launch_branch_pending';
    const _SHARED_SETTINGS_KEYS = [
        'proactiveChatEnabled',
        'proactiveVisionEnabled',
        'proactiveVisionChatEnabled',
        'proactiveNewsChatEnabled',
        'proactiveVideoChatEnabled',
        'proactiveXhhChatEnabled',
        'proactivePersonalChatEnabled',
        'proactiveMusicEnabled',
        'proactiveMemeEnabled',
        'proactiveMiniGameInviteEnabled',
        'mergeMessagesEnabled',
        'focusModeEnabled',
        'focusCognitionEnabled',
        'avatarReactionBubbleEnabled',
        'proactiveChatInterval',
        'proactiveVisionInterval',
        'textGuardMaxLength',
        'renderQuality',
        'targetFrameRate'
    ];

    function getDefaultRenderQuality() {
        return S.renderQuality || 'medium';
    }

    /**
     * 获取对话相关设置（仅包含需要同步到服务器的设置）
     * 注意：不包含 renderQuality、targetFrameRate、mouseTrackingEnabled 等性能/外观设置
     */
    function getConversationSettings() {
        const settings = {
            proactiveChatEnabled: S.proactiveChatEnabled,
            proactiveVisionEnabled: S.proactiveVisionEnabled,
            proactiveVisionChatEnabled: S.proactiveVisionChatEnabled,
            proactiveNewsChatEnabled: S.proactiveNewsChatEnabled,
            proactiveVideoChatEnabled: S.proactiveVideoChatEnabled,
            proactiveXhhChatEnabled: S.proactiveXhhChatEnabled,
            proactivePersonalChatEnabled: S.proactivePersonalChatEnabled,
            proactiveMusicEnabled: S.proactiveMusicEnabled,
            proactiveMemeEnabled: S.proactiveMemeEnabled,
            proactiveMiniGameInviteEnabled: S.proactiveMiniGameInviteEnabled,
            mergeMessagesEnabled: S.mergeMessagesEnabled,
            focusModeEnabled: S.focusModeEnabled,
            focusCognitionEnabled: S.focusCognitionEnabled,
            avatarReactionBubbleEnabled: S.avatarReactionBubbleEnabled,
            proactiveChatInterval: S.proactiveChatInterval,
            proactiveVisionInterval: S.proactiveVisionInterval,
            subtitleEnabled: S.subtitleEnabled,
            textGuardMaxLength: S.textGuardMaxLength
        };
        // 只有在 S 上存在 userLanguage 属性时才包含（含 null，支持显式清除语义）
        if ('userLanguage' in S) {
            settings.userLanguage = S.userLanguage;
        }
        return settings;
    }

    function applySharedRuntimeSettings(settings) {
        if (!settings || typeof settings !== 'object') return false;
        let changed = false;
        _SHARED_SETTINGS_KEYS.forEach((key) => {
            if (!Object.prototype.hasOwnProperty.call(settings, key)) return;
            if (S[key] !== settings[key]) {
                S[key] = settings[key];
                changed = true;
            }
        });
        if (
            Object.prototype.hasOwnProperty.call(settings, 'userLanguage') &&
            S.userLanguage !== settings.userLanguage
        ) {
            S.userLanguage = settings.userLanguage;
            changed = true;
        }
        if (changed && S.renderQuality) {
            window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
        }
        return changed;
    }

    function isManualScreenShareActive() {
        try {
            const button = document.getElementById('screenButton');
            return !!(button && button.classList.contains('active'));
        } catch (_) {
            return false;
        }
    }

    function stopVisionAfterPrivacyEnabled() {
        if (S.proactiveVisionEnabled !== false) return;

        try {
            if (typeof window.stopProactiveVisionDuringSpeech === 'function') {
                window.stopProactiveVisionDuringSpeech();
            }
        } catch (error) {
            console.warn('[app-settings] 停止语音主动视觉失败:', error);
        }

        if (isManualScreenShareActive()) return;

        try {
            if (typeof window.stopScreening === 'function') {
                window.stopScreening();
            }
        } catch (error) {
            console.warn('[app-settings] 停止屏幕发送循环失败:', error);
        }

        try {
            if (S.screenCaptureStream && typeof S.screenCaptureStream.getTracks === 'function') {
                S.screenCaptureStream.getTracks().forEach((track) => {
                    try { track.stop(); } catch (_) { }
                });
            }
        } catch (error) {
            console.warn('[app-settings] 释放隐私模式屏幕流失败:', error);
        } finally {
            S.screenCaptureStream = null;
            S.screenCaptureStreamLastUsed = null;
            if (S.screenCaptureStreamIdleTimer) {
                clearTimeout(S.screenCaptureStreamIdleTimer);
                S.screenCaptureStreamIdleTimer = null;
            }
        }
    }

    /**
     * 从服务器加载对话设置（异步）
     * 成功时返回设置对象，失败时返回 null
     */
    async function loadSettingsFromServer() {
        try {
            const response = await fetch('/api/config/conversation-settings', {
                method: 'GET',
                headers: { 'Content-Type': 'application/json' }
            });
            if (!response.ok) return null;
            const data = await response.json();
            if (!data.success) return null;
            const hasSettings = data.settings && Object.keys(data.settings).length > 0;
            const telemetryBranch = (typeof data.telemetryBranch === 'string' && data.telemetryBranch) || null;
            if (!hasSettings && !telemetryBranch) return null;
            return {
                settings: hasSettings ? data.settings : null,
                telemetryBranch
            };
        } catch (e) {
            console.warn('[app-settings] 从服务器加载设置失败:', e);
        }
        return null;
    }

    /**
     * 将对话设置同步到服务器（异步，不阻塞）
     * 用于定期备份和跨会话持久化
     */
    async function syncSettingsToServer() {
        try {
            const controller = window.NekoHomeTutorialFeatureController;
            if (controller && typeof controller.isActive === 'function' && controller.isActive()) {
                console.log('[app-settings] home tutorial suppression active, skip conversation settings sync');
                return;
            }
        } catch (_) {
            // keep settings sync best-effort if the tutorial controller is unavailable
        }
        const settings = getConversationSettings();
        try {
            const response = await fetch('/api/config/conversation-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
            if (!response.ok) {
                console.error('[app-settings] 同步设置到服务器失败: HTTP', response.status);
                return;
            }
            const data = await response.json();
            if (!data.success) {
                console.error('[app-settings] 同步设置到服务器失败:', data.error || '未知错误');
            }
        } catch (err) {
            console.error('[app-settings] 同步设置到服务器失败:', err);
        }
    }

    /**
     * 启动定期同步到服务器
     *
     * 首启 settings/telemetry 决议未完成（_FIRST_LAUNCH_PENDING_KEY 还在）时跳过 periodic
     * POST：否则会把首启本地默认值抢先推到服务器，下次 GET 读到自家 echo 误判「云端已有
     * 偏好」、干扰设置合并与首启决议时序。用户主动改设置走的 saveSettings 不受影响（那条
     * 路径就是要持久化用户显式选择）。
     */
    function startPeriodicSync() {
        if (_syncTimerId !== null) return; // 防止重复启动
        _syncTimerId = setInterval(() => {
            try {
                if (localStorage.getItem(_FIRST_LAUNCH_PENDING_KEY) === '1') {
                    return;
                }
            } catch (_) { /* localStorage 不可用就当 pending 没 set，照常 sync */ }
            syncSettingsToServer();
        }, SYNC_INTERVAL_MS);
        console.log('[app-settings] 已启动定期同步到服务器，间隔', SYNC_INTERVAL_MS / 1000, '秒');
    }

    /**
     * 停止定期同步到服务器
     */
    function stopPeriodicSync() {
        if (_syncTimerId !== null) {
            clearInterval(_syncTimerId);
            _syncTimerId = null;
            console.log('[app-settings] 已停止定期同步到服务器');
        }
    }

    /**
     * 检测用户是否处于中国地区
     * 通过时区和浏览器语言判断
     */
    function _isUserRegionChina() {
        try {
            const tz = (Intl.DateTimeFormat().resolvedOptions().timeZone || '').toLowerCase();
            if (/^asia\/(shanghai|chongqing|urumqi|harbin|kashgar)$/.test(tz)) return true;
            const lang = (navigator.language || '').toLowerCase();
            if (lang === 'zh' || lang.startsWith('zh-cn') || lang.startsWith('zh-hans')) return true;
        } catch (_) { }
        return false;
    }

    // ======================== saveSettings ========================

    /**
     * 将当前设置保存到 localStorage
     * 从 window 全局变量读取最新值（确保同步 live2d.js 中的更改）
     *
     * @param {{ skipServerSync?: boolean }} [options] 传 skipServerSync 跳过 POST，
     *   首启用——避免在 loadSettingsFromServer 拿到 telemetryBranch 之前就把首启本地
     *   默认值写到服务器、回头被自己的 GET 当成「云端已有偏好」，干扰首启决议时序
     */
    function saveSettings(options) {
        const skipServerSync = !!(options && options.skipServerSync);
        // 从全局变量读取最新值（确保同步 live2d.js 中的更改）
        const currentProactive = typeof window.proactiveChatEnabled !== 'undefined'
            ? window.proactiveChatEnabled
            : S.proactiveChatEnabled;
        const currentVision = typeof window.proactiveVisionEnabled !== 'undefined'
            ? window.proactiveVisionEnabled
            : S.proactiveVisionEnabled;
        const currentVisionChat = typeof window.proactiveVisionChatEnabled !== 'undefined'
            ? window.proactiveVisionChatEnabled
            : S.proactiveVisionChatEnabled;
        const currentNewsChat = typeof window.proactiveNewsChatEnabled !== 'undefined'
            ? window.proactiveNewsChatEnabled
            : S.proactiveNewsChatEnabled;
        const currentVideoChat = typeof window.proactiveVideoChatEnabled !== 'undefined'
            ? window.proactiveVideoChatEnabled
            : S.proactiveVideoChatEnabled;
        const currentXhhChat = typeof window.proactiveXhhChatEnabled !== 'undefined'
            ? window.proactiveXhhChatEnabled
            : S.proactiveXhhChatEnabled;
        const currentMerge = typeof window.mergeMessagesEnabled !== 'undefined'
            ? window.mergeMessagesEnabled
            : S.mergeMessagesEnabled;
        const currentFocus = typeof window.focusModeEnabled !== 'undefined'
            ? window.focusModeEnabled
            : S.focusModeEnabled;
        const currentFocusCognition = typeof window.focusCognitionEnabled !== 'undefined'
            ? window.focusCognitionEnabled
            : S.focusCognitionEnabled;
        const currentProactiveChatInterval = typeof window.proactiveChatInterval !== 'undefined'
            ? window.proactiveChatInterval
            : S.proactiveChatInterval;
        const currentProactiveVisionInterval = typeof window.proactiveVisionInterval !== 'undefined'
            ? window.proactiveVisionInterval
            : S.proactiveVisionInterval;
        const currentPersonalChat = typeof window.proactivePersonalChatEnabled !== 'undefined'
            ? window.proactivePersonalChatEnabled
            : S.proactivePersonalChatEnabled;
        const currentMusicChat = typeof window.proactiveMusicEnabled !== 'undefined'
            ? window.proactiveMusicEnabled
            : S.proactiveMusicEnabled;
        const currentMemeChat = typeof window.proactiveMemeEnabled !== 'undefined'
            ? window.proactiveMemeEnabled
            : S.proactiveMemeEnabled;
        const currentMiniGameInviteChat = typeof window.proactiveMiniGameInviteEnabled !== 'undefined'
            ? window.proactiveMiniGameInviteEnabled
            : S.proactiveMiniGameInviteEnabled;
        const currentAvatarReactionBubble = typeof window.avatarReactionBubbleEnabled !== 'undefined'
            ? window.avatarReactionBubbleEnabled
            : S.avatarReactionBubbleEnabled;
        const currentTextGuardMaxLength = typeof window.textGuardMaxLength !== 'undefined'
            ? window.textGuardMaxLength
            : S.textGuardMaxLength;
        const currentRenderQuality = typeof window.renderQuality !== 'undefined'
            ? window.renderQuality
            : S.renderQuality;
        const currentTargetFrameRate = typeof window.targetFrameRate !== 'undefined'
            ? window.targetFrameRate
            : S.targetFrameRate;
        const currentMouseTracking = typeof window.mouseTrackingEnabled !== 'undefined'
            ? window.mouseTrackingEnabled
            : true;
        const currentLive2dFullscreenTracking = typeof window.live2dFullscreenTrackingEnabled !== 'undefined'
            ? window.live2dFullscreenTrackingEnabled
            : false;
        const currentHumanoidLocalTracking = typeof window.humanoidLocalTrackingEnabled !== 'undefined'
            ? window.humanoidLocalTrackingEnabled
            : false;
        const currentLockedHoverFade = typeof window.lockedHoverFadeEnabled !== 'undefined'
            ? window.lockedHoverFadeEnabled
            : true;

        // 读取字幕设置（统一走 subtitle-shared store，避免多处直接写 localStorage）
        const subtitleStore = window.nekoSubtitleShared;
        const subtitleState = subtitleStore && typeof subtitleStore.getSettings === 'function'
            ? subtitleStore.getSettings()
            : null;
        const currentSubtitleEnabled = typeof S.subtitleEnabled !== 'undefined'
            ? S.subtitleEnabled
            : (subtitleState ? !!subtitleState.subtitleEnabled : (localStorage.getItem('subtitleEnabled') === 'true'));
        const currentUserLanguage = S.hasOwnProperty('userLanguage')
            ? S.userLanguage
            : (subtitleState ? subtitleState.userLanguage : (localStorage.getItem('userLanguage') || null));

        const settings = {
            proactiveChatEnabled: currentProactive,
            proactiveVisionEnabled: currentVision,
            proactiveVisionChatEnabled: currentVisionChat,
            proactiveNewsChatEnabled: currentNewsChat,
            proactiveVideoChatEnabled: currentVideoChat,
            proactiveXhhChatEnabled: currentXhhChat,
            proactivePersonalChatEnabled: currentPersonalChat,
            proactiveMusicEnabled: currentMusicChat,
            proactiveMemeEnabled: currentMemeChat,
            proactiveMiniGameInviteEnabled: currentMiniGameInviteChat,
            mergeMessagesEnabled: currentMerge,
            focusModeEnabled: currentFocus,
            focusCognitionEnabled: currentFocusCognition,
            avatarReactionBubbleEnabled: currentAvatarReactionBubble,
            proactiveChatInterval: currentProactiveChatInterval,
            proactiveVisionInterval: currentProactiveVisionInterval,
            textGuardMaxLength: currentTextGuardMaxLength,
            renderQuality: currentRenderQuality,
            targetFrameRate: currentTargetFrameRate,
            mouseTrackingEnabled: currentMouseTracking,
            live2dFullscreenTrackingEnabled: currentLive2dFullscreenTracking,
            humanoidLocalTrackingEnabled: currentHumanoidLocalTracking,
            lockedHoverFadeEnabled: currentLockedHoverFade,
            subtitleEnabled: currentSubtitleEnabled,
            userLanguage: currentUserLanguage
        };
        localStorage.setItem('project_neko_settings', JSON.stringify(settings));

        // 同步回共享状态，保持一致性
        S.proactiveChatEnabled = currentProactive;
        S.proactiveVisionEnabled = currentVision;
        S.proactiveVisionChatEnabled = currentVisionChat;
        S.proactiveNewsChatEnabled = currentNewsChat;
        S.proactiveVideoChatEnabled = currentVideoChat;
        S.proactiveXhhChatEnabled = currentXhhChat;
        S.proactivePersonalChatEnabled = currentPersonalChat;
        S.proactiveMusicEnabled = currentMusicChat;
        S.proactiveMemeEnabled = currentMemeChat;
        S.proactiveMiniGameInviteEnabled = currentMiniGameInviteChat;
        S.mergeMessagesEnabled = currentMerge;
        S.focusModeEnabled = currentFocus;
        S.focusCognitionEnabled = currentFocusCognition;
        S.avatarReactionBubbleEnabled = currentAvatarReactionBubble;
        S.proactiveChatInterval = currentProactiveChatInterval;
        S.proactiveVisionInterval = currentProactiveVisionInterval;
        S.textGuardMaxLength = currentTextGuardMaxLength;
        S.renderQuality = currentRenderQuality;
        S.targetFrameRate = currentTargetFrameRate;
        stopVisionAfterPrivacyEnabled();
        // 同步字幕设置到共享状态
        S.subtitleEnabled = currentSubtitleEnabled;
        S.userLanguage = currentUserLanguage;
        if (subtitleStore && typeof subtitleStore.updateSettings === 'function') {
            subtitleStore.updateSettings({
                subtitleEnabled: S.subtitleEnabled,
                userLanguage: S.userLanguage
            }, {
                source: 'app-settings-save'
            });
        }

        // 同步到服务器（异步，不阻塞）；首启走 skipServerSync 等 branch 解析后再 POST
        if (!skipServerSync) {
            syncSettingsToServer();
        }
    }

    // ======================== loadSettings ========================

    /**
     * 从 localStorage 加载设置，包含迁移逻辑
     * 首次启动时检测用户地区，中国用户自动开启自主视觉
     * 加载后异步从服务器同步最新设置
     */
    function loadSettings() {
        // 内层 try：仅处理本地 JSON 解析与迁移
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
                    settings.proactiveXhhChatEnabled !== undefined ||
                    settings.proactivePersonalChatEnabled !== undefined ||
                    settings.proactiveMusicEnabled !== undefined ||
                    settings.proactiveMemeEnabled !== undefined ||
                    settings.proactiveMiniGameInviteEnabled !== undefined;
                    if (!hasNewFlags) {
                        // 根据旧的视觉偏好决定迁移策略
                        if (settings.proactiveVisionEnabled === false) {
                            // 用户之前禁用了视觉，保留偏好并默认启用新闻搭话
                            settings.proactiveVisionEnabled = false;
                            settings.proactiveVisionChatEnabled = false;
                            settings.proactiveNewsChatEnabled = true;
                            settings.proactiveXhhChatEnabled = false;
                            settings.proactivePersonalChatEnabled = false;
                            settings.proactiveMusicEnabled = false;
                            settings.proactiveMemeEnabled = false;
                            console.log('迁移旧版设置：保留禁用的视觉偏好，已启用新闻搭话');
                        } else {
                            // 视觉偏好为 true 或 undefined，默认启用视觉搭话
                            settings.proactiveVisionEnabled = true;
                            settings.proactiveVisionChatEnabled = true;
                            settings.proactiveXhhChatEnabled = false;
                            settings.proactivePersonalChatEnabled = false;
                            settings.proactiveMusicEnabled = false;
                            settings.proactiveMemeEnabled = false;
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
                S.proactiveChatEnabled = settings.proactiveChatEnabled ?? true;
                S.proactiveVisionEnabled = settings.proactiveVisionEnabled ?? false;
                S.proactiveVisionChatEnabled = settings.proactiveVisionChatEnabled ?? true;
                S.proactiveNewsChatEnabled = settings.proactiveNewsChatEnabled ?? false;
                S.proactiveVideoChatEnabled = settings.proactiveVideoChatEnabled ?? true;
                S.proactiveXhhChatEnabled = settings.proactiveXhhChatEnabled ?? false;
                S.proactivePersonalChatEnabled = settings.proactivePersonalChatEnabled ?? false;
                S.proactiveMusicEnabled = settings.proactiveMusicEnabled ?? true;
                S.proactiveMemeEnabled = settings.proactiveMemeEnabled ?? true;
                S.proactiveMiniGameInviteEnabled = settings.proactiveMiniGameInviteEnabled ?? true;
                S.mergeMessagesEnabled = settings.mergeMessagesEnabled ?? false;
                S.focusModeEnabled = settings.focusModeEnabled ?? false;
                S.focusCognitionEnabled = settings.focusCognitionEnabled ?? true;
                S.avatarReactionBubbleEnabled = settings.avatarReactionBubbleEnabled ?? true;
                S.proactiveChatInterval = settings.proactiveChatInterval ?? C.DEFAULT_PROACTIVE_CHAT_INTERVAL;
                S.proactiveVisionInterval = settings.proactiveVisionInterval ?? C.DEFAULT_PROACTIVE_VISION_INTERVAL;
                // 回复 token 上限（默认 300 tiktoken tokens；0 = 无限制）
                S.textGuardMaxLength = settings.textGuardMaxLength ?? 300;
                window.textGuardMaxLength = S.textGuardMaxLength;
                // 画质设置
                S.renderQuality = settings.renderQuality ?? getDefaultRenderQuality();
                window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
                // 帧率设置（0 = 不限帧 / VSync）
                S.targetFrameRate = settings.targetFrameRate ?? 60;
                // 鼠标跟踪设置（严格转换为布尔值）
                if (typeof settings.mouseTrackingEnabled === 'boolean') {
                    window.mouseTrackingEnabled = settings.mouseTrackingEnabled;
                } else if (typeof settings.mouseTrackingEnabled === 'string') {
                    window.mouseTrackingEnabled = settings.mouseTrackingEnabled === 'true';
                } else {
                    window.mouseTrackingEnabled = true;
                }

                // 跟踪模式设置
                if (typeof settings.live2dFullscreenTrackingEnabled === 'boolean') {
                    window.live2dFullscreenTrackingEnabled = settings.live2dFullscreenTrackingEnabled;
                } else if (typeof settings.live2dFullscreenTrackingEnabled === 'string') {
                    window.live2dFullscreenTrackingEnabled = settings.live2dFullscreenTrackingEnabled === 'true';
                }

                if (typeof settings.humanoidLocalTrackingEnabled === 'boolean') {
                    window.humanoidLocalTrackingEnabled = settings.humanoidLocalTrackingEnabled;
                } else if (typeof settings.humanoidLocalTrackingEnabled === 'string') {
                    window.humanoidLocalTrackingEnabled = settings.humanoidLocalTrackingEnabled === 'true';
                }

                // 锁定悬停淡化设置
                if (typeof settings.lockedHoverFadeEnabled === 'boolean') {
                    window.lockedHoverFadeEnabled = settings.lockedHoverFadeEnabled;
                } else if (typeof settings.lockedHoverFadeEnabled === 'string') {
                    window.lockedHoverFadeEnabled = settings.lockedHoverFadeEnabled === 'true';
                } else {
                    window.lockedHoverFadeEnabled = true;
                }

                // 同步到运行中的实例
                if (typeof window.live2dManager !== 'undefined' && window.live2dManager && typeof window.live2dManager.setFullscreenTrackingEnabled === 'function') {
                    window.live2dManager.setFullscreenTrackingEnabled(window.live2dFullscreenTrackingEnabled === true);
                }
                if (typeof window.vrmManager !== 'undefined' && window.vrmManager && window.vrmManager._cursorFollow && typeof window.vrmManager._cursorFollow.setLocalTrackingEnabled === 'function') {
                    window.vrmManager._cursorFollow.setLocalTrackingEnabled(window.humanoidLocalTrackingEnabled === true);
                }
                if (typeof window.mmdManager !== 'undefined' && window.mmdManager && window.mmdManager.cursorFollow && typeof window.mmdManager.cursorFollow.setLocalTrackingEnabled === 'function') {
                    window.mmdManager.cursorFollow.setLocalTrackingEnabled(window.humanoidLocalTrackingEnabled === true);
                }

                console.log('已加载设置:', {
                    proactiveChatEnabled: S.proactiveChatEnabled,
                    proactiveVisionEnabled: S.proactiveVisionEnabled,
                    proactiveVisionChatEnabled: S.proactiveVisionChatEnabled,
                    proactiveNewsChatEnabled: S.proactiveNewsChatEnabled,
                    proactiveVideoChatEnabled: S.proactiveVideoChatEnabled,
                    proactiveXhhChatEnabled: S.proactiveXhhChatEnabled,
                    proactivePersonalChatEnabled: S.proactivePersonalChatEnabled,
                    mergeMessagesEnabled: S.mergeMessagesEnabled,
                    focusModeEnabled: S.focusModeEnabled,
                    proactiveChatInterval: S.proactiveChatInterval,
                    proactiveVisionInterval: S.proactiveVisionInterval,
                    focusModeDesc: S.focusModeEnabled ? 'AI说话时自动静音麦克风（不允许打断）' : '允许打断AI说话'
                });
            } else {
                // 首次启动：隐私模式按用户地区分流（仅中国地区默认关闭隐私 / vision 开）。
                // 历史上这里挂过隐私默认值实验（privacy_default_off_v2，已退役）和屏幕分享
                // 来源默认值实验（vision_chat_default_off，已合并进 main、默认回到「开」），
                // 现都不再做首启覆写，仅保留地区分流。
                if (_isUserRegionChina()) {
                    S.proactiveVisionEnabled = true;
                }

                // 首次启动默认开启音乐/meme搭话 + mini-game 邀请
                S.proactiveMusicEnabled = true;
                S.proactiveMemeEnabled = true;
                S.proactiveMiniGameInviteEnabled = true;
                // 首次启动默认 token 上限 300（tiktoken o200k_base）
                S.textGuardMaxLength = 300;
                window.textGuardMaxLength = 300;

                console.log('未找到保存的设置，使用默认值');
                window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
                window.mouseTrackingEnabled = true;
                window.live2dFullscreenTrackingEnabled = false;
                window.humanoidLocalTrackingEnabled = false;
                window.lockedHoverFadeEnabled = true;

                // 首启专属 marker：告诉下方异步合并块「这次仍在等首次 settings/telemetry
                // 决议」。升级用户走的是 if (saved) 分支不会写这个，于是不会被误判成首启
                try { localStorage.setItem(_FIRST_LAUNCH_PENDING_KEY, '1'); } catch (_) {}
                // 持久化首次启动设置到 localStorage，避免每次重新检测。注意：故意跳过
                // 服务器 POST——loadSettingsFromServer GET 还没拿到 telemetryBranch，
                // 这时把首启本地默认值上行会被自家 GET 当作「云端已有偏好」回读、干扰设置
                // 合并与首启决议时序。等 branch 解析后再做一次完整 saveSettings 推送
                saveSettings({ skipServerSync: true });
            }

        } catch (error) {
            console.error('加载本地设置失败:', error);
            // 出错时也要确保全局变量被初始化
            S.textGuardMaxLength = 300;
            window.textGuardMaxLength = 300;
            window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
            window.mouseTrackingEnabled = true;
            window.live2dFullscreenTrackingEnabled = false;
            window.humanoidLocalTrackingEnabled = false;
            window.lockedHoverFadeEnabled = true;
        }

        // 以下逻辑不依赖本地 JSON 解析结果，始终执行

        // 凝神总开关镜像到 window：设置弹窗（avatar-ui-popup.js）只读 window 全局，
        // 而 window.focusCognitionEnabled 否则仅在 server-merge 命中时才赋值——用户存了
        // 关、reload 时若没触发 merge，window 会是 undefined 让弹窗误显示为开。这里在
        // 每次 loadSettings 末尾从 S 权威镜像一次兜住该时序漏洞。
        window.focusCognitionEnabled = S.focusCognitionEnabled;

        // 加载字幕设置（统一从 subtitle-shared store 读取）
        const subtitleStore = window.nekoSubtitleShared;
        const subtitleState = subtitleStore && typeof subtitleStore.getSettings === 'function'
            ? subtitleStore.getSettings()
            : null;
        S.subtitleEnabled = subtitleState ? !!subtitleState.subtitleEnabled : (localStorage.getItem('subtitleEnabled') === 'true');
        S.userLanguage = subtitleState ? subtitleState.userLanguage : (localStorage.getItem('userLanguage') || null);

        // 异步：从服务器加载对话设置并合并（不阻塞 UI）
        const _firstLaunchPending = (() => {
            try { return localStorage.getItem(_FIRST_LAUNCH_PENDING_KEY) === '1'; } catch (_) { return false; }
        })();
        try {
            loadSettingsFromServer().then(serverResult => {
                if (!serverResult) return;
                const serverSettings = serverResult.settings;
                const telemetryBranch = serverResult.telemetryBranch;
                let hasUpdate = false;

                // 只要 server 给了 branch，本次首启决议就算完成，清掉 pending marker；下次
                // 启动不再尝试。GET 失败则 marker 留着，下次在线启动重新决议。原本这里还会
                // 对实验组 vision_chat_default_off 把屏幕分享来源（proactiveVisionChatEnabled）
                // 首启默认翻成「关」，现该实验已合并进 main、默认回到控制组「开」（见
                // app-state.js / loadSettings 的 ?? true），故不再做首启覆写；仅保留 marker
                // 决议时序——情境弹窗 app-context-prompt.js 靠下方 neko:telemetry-branch-resolved
                // 广播判断 settings 已就绪。
                const branchResolutionFinalized = !!(telemetryBranch && _firstLaunchPending);
                if (branchResolutionFinalized) {
                    try { localStorage.removeItem(_FIRST_LAUNCH_PENDING_KEY); } catch (_) {}
                    // 首启决议完后强制 POST 一次：没有 server merge 时 hasUpdate 仍是 false，
                    // 若用户在 60s periodic 之前关掉 app，首启的本地默认值就永远到不了服务器。
                    // 这里 hasUpdate=true 让下方 saveSettings 走完整路径推一次
                    hasUpdate = true;
                }

                if (serverSettings) {
                    // 用服务器设置覆盖本地设置
                    for (const key of Object.keys(serverSettings)) {
                        if (serverSettings[key] !== undefined && S[key] !== serverSettings[key]) {
                            S[key] = serverSettings[key];
                            hasUpdate = true;
                        }
                    }
                    // 同步字幕设置到 subtitle.js（内部闭包变量）
                    if (serverSettings.subtitleEnabled !== undefined && window.subtitleBridge) {
                        window.subtitleBridge.setSubtitleEnabled(serverSettings.subtitleEnabled);
                    }
                    if (serverSettings.userLanguage !== undefined && window.subtitleBridge) {
                        window.subtitleBridge.setUserLanguage(serverSettings.userLanguage);
                    }
                }

                if (hasUpdate) {
                    console.log('[app-settings] 已从服务器合并对话设置');
                    // 同步 window 镜像变量，防止 saveSettings() 回滚
                    window.proactiveChatEnabled = S.proactiveChatEnabled;
                    window.proactiveVisionEnabled = S.proactiveVisionEnabled;
                    window.proactiveVisionChatEnabled = S.proactiveVisionChatEnabled;
                    window.proactiveNewsChatEnabled = S.proactiveNewsChatEnabled;
                    window.proactiveVideoChatEnabled = S.proactiveVideoChatEnabled;
                    window.proactiveXhhChatEnabled = S.proactiveXhhChatEnabled;
                    window.proactivePersonalChatEnabled = S.proactivePersonalChatEnabled;
                    window.proactiveMusicEnabled = S.proactiveMusicEnabled;
                    window.proactiveMemeEnabled = S.proactiveMemeEnabled;
                    window.proactiveMiniGameInviteEnabled = S.proactiveMiniGameInviteEnabled;
                    window.mergeMessagesEnabled = S.mergeMessagesEnabled;
                    window.focusModeEnabled = S.focusModeEnabled;
                    window.focusCognitionEnabled = S.focusCognitionEnabled;
                    window.avatarReactionBubbleEnabled = S.avatarReactionBubbleEnabled;
                    window.proactiveChatInterval = S.proactiveChatInterval;
                    window.proactiveVisionInterval = S.proactiveVisionInterval;
                    window.textGuardMaxLength = S.textGuardMaxLength;
                    // 同步回 localStorage
                    saveSettings();
                    // 重新初始化主动搭话调度器（使用最新标志）
                    if (typeof window.appProactive !== 'undefined' && window.appProactive.scheduleProactiveChat) {
                        window.appProactive.scheduleProactiveChat();
                    } else if (typeof window.scheduleProactiveChat === 'function') {
                        window.scheduleProactiveChat();
                    }
                }

                // 把 branch 暴露给情境弹窗模块（app-context-prompt.js）并广播 settings-ready
                // 信号——必须放在所有设置合并（server merge + saveSettings）之后。否则被缓存的
                // context 在重放时，_isActionable 会读到合并前的旧 proactiveVisionChatEnabled，
                // 误判该不该弹（Codex P2）。GET 失败 telemetryBranch 为 null 时不挂、不广播，弹窗
                // 模块拿不到「就绪」信号默认不弹（fail-closed，宁可漏弹也不拿未合并设置误弹）。
                if (telemetryBranch) {
                    window.nekoTelemetryBranch = telemetryBranch;
                    window.dispatchEvent(new CustomEvent('neko:telemetry-branch-resolved', {
                        detail: { branch: telemetryBranch },
                    }));
                }
            }).finally(() => {
                // 必须等 GET 解析后再起 periodic sync：否则 60s 间隔的 POST 可能比 GET 先到，
                // 把首启本地默认值写到服务器；GET 回来读到自家 echo 误判「云端已有偏好」、干扰
                // 设置合并，marker 也可能错误留存。GET 走 finally 后周期同步才安全
                startPeriodicSync();
            });
        } catch (error) {
            console.error('服务器设置同步启动失败:', error);
            // GET 链路本身就挂了，至少把 periodic sync 起来兜底，
            // 避免用户的本地修改永远上不了服务器
            startPeriodicSync();
        }
    }

    // ======================== 初始化调用 ========================

    window.addEventListener('storage', function (event) {
        if (event.key !== 'project_neko_settings' || !event.newValue) return;
        try {
            const settings = JSON.parse(event.newValue);
            const changed = applySharedRuntimeSettings(settings);
            stopVisionAfterPrivacyEnabled();
            if (changed && typeof window.scheduleProactiveChat === 'function') {
                window.scheduleProactiveChat();
            }
        } catch (error) {
            console.warn('[app-settings] 跨窗口设置同步失败:', error);
        }
    });

    // 加载设置
    loadSettings();

    // ======================== 启动后调度 ========================

    /**
     * 初始化后启动主动搭话调度器
     * 需要在其他模块加载完成后由 app.js 主调度器调用
     * 或在 DOMContentLoaded / 入口处调用
     */
    function initProactiveChatScheduler() {
        // 防止重复初始化
        if (S._proactiveSchedulerInitialized) {
            console.log('[主动搭话] 调度器已初始化，跳过重复调用');
            return;
        }
        
        // 加载麦克风设备选择
        if (typeof window.appAudioCapture !== 'undefined' && window.appAudioCapture.loadSelectedMicrophone) {
            window.appAudioCapture.loadSelectedMicrophone();
        } else if (typeof window.loadSelectedMicrophone === 'function') {
            window.loadSelectedMicrophone();
        }

        // 加载麦克风增益设置
        if (typeof window.appAudioCapture !== 'undefined' && window.appAudioCapture.loadMicGainSetting) {
            window.appAudioCapture.loadMicGainSetting();
        } else if (typeof window.loadMicGainSetting === 'function') {
            window.loadMicGainSetting();
        }

        // 加载降噪设置
        if (typeof window.appAudioCapture !== 'undefined' && window.appAudioCapture.loadNoiseReductionSetting) {
            window.appAudioCapture.loadNoiseReductionSetting();
        }

        // 加载扬声器音量设置
        if (typeof window.appAudioPlayback !== 'undefined' && window.appAudioPlayback.loadSpeakerVolumeSetting) {
            window.appAudioPlayback.loadSpeakerVolumeSetting();
        } else if (typeof window.loadSpeakerVolumeSetting === 'function') {
            window.loadSpeakerVolumeSetting();
        }

        // 如果已开启主动搭话且选择了搭话方式，立即启动定时器
        if (S.proactiveChatEnabled && (S.proactiveVisionChatEnabled || S.proactiveNewsChatEnabled || S.proactiveVideoChatEnabled || S.proactiveXhhChatEnabled || S.proactivePersonalChatEnabled || S.proactiveMusicEnabled || S.proactiveMemeEnabled || S.proactiveMiniGameInviteEnabled)) {
            // 主动搭话启动自检
            console.log('========== 主动搭话启动自检 ==========');
            console.log('[自检] proactiveChatEnabled: ' + S.proactiveChatEnabled);
            console.log('[自检] proactiveVisionChatEnabled: ' + S.proactiveVisionChatEnabled);
            console.log('[自检] proactiveNewsChatEnabled: ' + S.proactiveNewsChatEnabled);
            console.log('[自检] proactiveVideoChatEnabled: ' + S.proactiveVideoChatEnabled);
            console.log('[自检] proactiveXhhChatEnabled: ' + S.proactiveXhhChatEnabled);
            console.log('[自检] proactivePersonalChatEnabled: ' + S.proactivePersonalChatEnabled);
            console.log('[自检] proactiveMusicEnabled: ' + S.proactiveMusicEnabled);
            console.log('[自检] proactiveMemeEnabled: ' + S.proactiveMemeEnabled);
            console.log('[自检] proactiveMiniGameInviteEnabled: ' + S.proactiveMiniGameInviteEnabled);
            console.log('[自检] localStorage设置: ' + (localStorage.getItem('project_neko_settings') ? '已存在' : '不存在'));

            // 检查WebSocket连接状态
            var wsStatus = S.socket ? S.socket.readyState : undefined;
            console.log('[自检] WebSocket状态: ' + wsStatus + ' (1=OPEN, 0=CONNECTING, 2=CLOSING, 3=CLOSED)');

            if (typeof window.appProactive !== 'undefined' && window.appProactive.scheduleProactiveChat) {
                window.appProactive.scheduleProactiveChat();
            } else if (typeof window.scheduleProactiveChat === 'function') {
                window.scheduleProactiveChat();
            }
            console.log('========== 主动搭话启动自检完成 ==========');
        } else {
            console.log('[App] 主动搭话未满足启动条件，跳过调度器启动:');
            console.log('  - proactiveChatEnabled: ' + S.proactiveChatEnabled);
            console.log('  - 任意搭话模式启用: ' + (S.proactiveVisionChatEnabled || S.proactiveNewsChatEnabled || S.proactiveVideoChatEnabled || S.proactiveXhhChatEnabled || S.proactivePersonalChatEnabled || S.proactiveMusicEnabled || S.proactiveMemeEnabled || S.proactiveMiniGameInviteEnabled));
        }

        // 所有步骤完成后，最后才设置初始化成功的标志
        S._proactiveSchedulerInitialized = true;
    }

    // ======================== 导出 ========================

    mod.saveSettings = saveSettings;
    mod.loadSettings = loadSettings;
    mod.syncSettingsToServer = syncSettingsToServer;
    mod.getConversationSettings = getConversationSettings;
    mod.initProactiveChatScheduler = initProactiveChatScheduler;
    mod._isUserRegionChina = _isUserRegionChina;
    mod.stopPeriodicSync = stopPeriodicSync;

    window.appSettings = mod;

    // 暴露到全局作用域，供 live2d.js 等其他模块调用（向后兼容）
    window.saveNEKOSettings = saveSettings;
})();
