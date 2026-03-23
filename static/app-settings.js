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
            proactivePersonalChatEnabled: S.proactivePersonalChatEnabled,
            proactiveMusicEnabled: S.proactiveMusicEnabled,
            mergeMessagesEnabled: S.mergeMessagesEnabled,
            focusModeEnabled: S.focusModeEnabled,
            proactiveChatInterval: S.proactiveChatInterval,
            proactiveVisionInterval: S.proactiveVisionInterval,
            subtitleEnabled: S.subtitleEnabled
        };
        // 只有在 S 上存在 userLanguage 属性时才包含（含 null，支持显式清除语义）
        if ('userLanguage' in S) {
            settings.userLanguage = S.userLanguage;
        }
        return settings;
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
            if (data.success && data.settings && Object.keys(data.settings).length > 0) {
                return data.settings;
            }
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
     */
    function startPeriodicSync() {
        if (_syncTimerId !== null) return; // 防止重复启动
        _syncTimerId = setInterval(() => {
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
     */
    function saveSettings() {
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
        const currentMerge = typeof window.mergeMessagesEnabled !== 'undefined'
            ? window.mergeMessagesEnabled
            : S.mergeMessagesEnabled;
        const currentFocus = typeof window.focusModeEnabled !== 'undefined'
            ? window.focusModeEnabled
            : S.focusModeEnabled;
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
        const currentRenderQuality = typeof window.renderQuality !== 'undefined'
            ? window.renderQuality
            : S.renderQuality;
        const currentTargetFrameRate = typeof window.targetFrameRate !== 'undefined'
            ? window.targetFrameRate
            : S.targetFrameRate;
        const currentMouseTracking = typeof window.mouseTrackingEnabled !== 'undefined'
            ? window.mouseTrackingEnabled
            : true;

        // 读取字幕设置（从 S 读取，因为 subtitle.js 会写入 S）
        const currentSubtitleEnabled = typeof S.subtitleEnabled !== 'undefined' ? S.subtitleEnabled : (localStorage.getItem('subtitleEnabled') === 'true');
        const currentUserLanguage = S.hasOwnProperty('userLanguage') ? S.userLanguage : (localStorage.getItem('userLanguage') || null);

        const settings = {
            proactiveChatEnabled: currentProactive,
            proactiveVisionEnabled: currentVision,
            proactiveVisionChatEnabled: currentVisionChat,
            proactiveNewsChatEnabled: currentNewsChat,
            proactiveVideoChatEnabled: currentVideoChat,
            proactivePersonalChatEnabled: currentPersonalChat,
            proactiveMusicEnabled: currentMusicChat,
            proactiveMemeEnabled: currentMemeChat,
            mergeMessagesEnabled: currentMerge,
            focusModeEnabled: currentFocus,
            proactiveChatInterval: currentProactiveChatInterval,
            proactiveVisionInterval: currentProactiveVisionInterval,
            renderQuality: currentRenderQuality,
            targetFrameRate: currentTargetFrameRate,
            mouseTrackingEnabled: currentMouseTracking,
            subtitleEnabled: currentSubtitleEnabled,
            userLanguage: currentUserLanguage
        };
        localStorage.setItem('project_neko_settings', JSON.stringify(settings));
        // 同时保存字幕设置到独立 key（兼容 subtitle.js）
        localStorage.setItem('subtitleEnabled', currentSubtitleEnabled.toString());
        if (currentUserLanguage != null) {
            localStorage.setItem('userLanguage', currentUserLanguage);
        } else {
            localStorage.removeItem('userLanguage');
        }

        // 同步回共享状态，保持一致性
        S.proactiveChatEnabled = currentProactive;
        S.proactiveVisionEnabled = currentVision;
        S.proactiveVisionChatEnabled = currentVisionChat;
        S.proactiveNewsChatEnabled = currentNewsChat;
        S.proactiveVideoChatEnabled = currentVideoChat;
        S.proactivePersonalChatEnabled = currentPersonalChat;
        S.proactiveMusicEnabled = currentMusicChat;
        S.proactiveMemeEnabled = currentMemeChat;
        S.mergeMessagesEnabled = currentMerge;
        S.focusModeEnabled = currentFocus;
        S.proactiveChatInterval = currentProactiveChatInterval;
        S.proactiveVisionInterval = currentProactiveVisionInterval;
        S.renderQuality = currentRenderQuality;
        S.targetFrameRate = currentTargetFrameRate;
        // 同步字幕设置到共享状态
        S.subtitleEnabled = currentSubtitleEnabled;
        S.userLanguage = currentUserLanguage;
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
                    settings.proactivePersonalChatEnabled !== undefined ||
                    settings.proactiveMusicEnabled !== undefined ||
                    settings.proactiveMemeEnabled !== undefined;
                    if (!hasNewFlags) {
                        // 根据旧的视觉偏好决定迁移策略
                        if (settings.proactiveVisionEnabled === false) {
                            // 用户之前禁用了视觉，保留偏好并默认启用新闻搭话
                            settings.proactiveVisionEnabled = false;
                            settings.proactiveVisionChatEnabled = false;
                            settings.proactiveNewsChatEnabled = true;
                            settings.proactivePersonalChatEnabled = false;
                            settings.proactiveMusicEnabled = false;
                            settings.proactiveMemeEnabled = false;
                            console.log('迁移旧版设置：保留禁用的视觉偏好，已启用新闻搭话');
                        } else {
                            // 视觉偏好为 true 或 undefined，默认启用视觉搭话
                            settings.proactiveVisionEnabled = true;
                            settings.proactiveVisionChatEnabled = true;
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
                S.proactiveChatEnabled = settings.proactiveChatEnabled ?? false;
                S.proactiveVisionEnabled = settings.proactiveVisionEnabled ?? false;
                S.proactiveVisionChatEnabled = settings.proactiveVisionChatEnabled ?? true;
                S.proactiveNewsChatEnabled = settings.proactiveNewsChatEnabled ?? false;
                S.proactiveVideoChatEnabled = settings.proactiveVideoChatEnabled ?? false;
                S.proactivePersonalChatEnabled = settings.proactivePersonalChatEnabled ?? false;
                S.proactiveMusicEnabled = settings.proactiveMusicEnabled ?? false;
                S.proactiveMemeEnabled = settings.proactiveMemeEnabled ?? false;
                S.mergeMessagesEnabled = settings.mergeMessagesEnabled ?? false;
                S.focusModeEnabled = settings.focusModeEnabled ?? false;
                S.proactiveChatInterval = settings.proactiveChatInterval ?? C.DEFAULT_PROACTIVE_CHAT_INTERVAL;
                S.proactiveVisionInterval = settings.proactiveVisionInterval ?? C.DEFAULT_PROACTIVE_VISION_INTERVAL;
                // 画质设置
                S.renderQuality = settings.renderQuality ?? 'medium';
                window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
                // 帧率设置
                S.targetFrameRate = settings.targetFrameRate ?? 60;
                // 鼠标跟踪设置（严格转换为布尔值）
                if (typeof settings.mouseTrackingEnabled === 'boolean') {
                    window.mouseTrackingEnabled = settings.mouseTrackingEnabled;
                } else if (typeof settings.mouseTrackingEnabled === 'string') {
                    window.mouseTrackingEnabled = settings.mouseTrackingEnabled === 'true';
                } else {
                    window.mouseTrackingEnabled = true;
                }

                console.log('已加载设置:', {
                    proactiveChatEnabled: S.proactiveChatEnabled,
                    proactiveVisionEnabled: S.proactiveVisionEnabled,
                    proactiveVisionChatEnabled: S.proactiveVisionChatEnabled,
                    proactiveNewsChatEnabled: S.proactiveNewsChatEnabled,
                    proactiveVideoChatEnabled: S.proactiveVideoChatEnabled,
                    proactivePersonalChatEnabled: S.proactivePersonalChatEnabled,
                    mergeMessagesEnabled: S.mergeMessagesEnabled,
                    focusModeEnabled: S.focusModeEnabled,
                    proactiveChatInterval: S.proactiveChatInterval,
                    proactiveVisionInterval: S.proactiveVisionInterval,
                    focusModeDesc: S.focusModeEnabled ? 'AI说话时自动静音麦克风（不允许打断）' : '允许打断AI说话'
                });
            } else {
                // 首次启动：检查用户地区，中国用户自动开启自主视觉
                if (_isUserRegionChina()) {
                    S.proactiveVisionEnabled = true;
                    console.log('首次启动：检测到中国地区用户，已自动开启自主视觉');
                }

                console.log('未找到保存的设置，使用默认值');
                window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
                window.mouseTrackingEnabled = true;

                // 持久化首次启动设置，避免每次重新检测
                saveSettings();
            }

        } catch (error) {
            console.error('加载本地设置失败:', error);
            // 出错时也要确保全局变量被初始化
            window.cursorFollowPerformanceLevel = U.mapRenderQualityToFollowPerf(S.renderQuality);
            window.mouseTrackingEnabled = true;
        }

        // 以下逻辑不依赖本地 JSON 解析结果，始终执行

        // 加载字幕设置（从 localStorage 读取，因为 subtitle.js 也用同一份）
        const savedSubtitleEnabled = localStorage.getItem('subtitleEnabled');
        S.subtitleEnabled = savedSubtitleEnabled === 'true';
        const savedUserLanguage = localStorage.getItem('userLanguage');
        S.userLanguage = savedUserLanguage ? savedUserLanguage : null;

        // 异步：从服务器加载对话设置并合并（不阻塞 UI）
        try {
            loadSettingsFromServer().then(serverSettings => {
                if (serverSettings) {
                    // 用服务器设置覆盖本地设置
                    let hasUpdate = false;
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
                    if (hasUpdate) {
                        console.log('[app-settings] 已从服务器合并对话设置');
                        // 同步 window 镜像变量，防止 saveSettings() 回滚
                        window.proactiveChatEnabled = S.proactiveChatEnabled;
                        window.proactiveVisionEnabled = S.proactiveVisionEnabled;
                        window.proactiveVisionChatEnabled = S.proactiveVisionChatEnabled;
                        window.proactiveNewsChatEnabled = S.proactiveNewsChatEnabled;
                        window.proactiveVideoChatEnabled = S.proactiveVideoChatEnabled;
                        window.proactivePersonalChatEnabled = S.proactivePersonalChatEnabled;
                        window.proactiveMusicEnabled = S.proactiveMusicEnabled;
                        window.mergeMessagesEnabled = S.mergeMessagesEnabled;
                        window.focusModeEnabled = S.focusModeEnabled;
                        window.proactiveChatInterval = S.proactiveChatInterval;
                        window.proactiveVisionInterval = S.proactiveVisionInterval;
                        // 同步回 localStorage
                        saveSettings();
                        // 重新初始化主动搭话调度器（使用最新标志）
                        if (typeof window.appProactive !== 'undefined' && window.appProactive.scheduleProactiveChat) {
                            window.appProactive.scheduleProactiveChat();
                        } else if (typeof window.scheduleProactiveChat === 'function') {
                            window.scheduleProactiveChat();
                        }
                    }
                }
            });

            // 启动定期同步到服务器
            startPeriodicSync();
        } catch (error) {
            console.error('服务器设置同步启动失败:', error);
        }
    }

    // ======================== 初始化调用 ========================

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
        if (typeof window.appAudio !== 'undefined' && window.appAudio.loadSelectedMicrophone) {
            window.appAudio.loadSelectedMicrophone();
        } else if (typeof window.loadSelectedMicrophone === 'function') {
            window.loadSelectedMicrophone();
        }

        // 加载麦克风增益设置
        if (typeof window.appAudio !== 'undefined' && window.appAudio.loadMicGainSetting) {
            window.appAudio.loadMicGainSetting();
        } else if (typeof window.loadMicGainSetting === 'function') {
            window.loadMicGainSetting();
        }

        // 加载扬声器音量设置
        if (typeof window.appAudio !== 'undefined' && window.appAudio.loadSpeakerVolumeSetting) {
            window.appAudio.loadSpeakerVolumeSetting();
        } else if (typeof window.loadSpeakerVolumeSetting === 'function') {
            window.loadSpeakerVolumeSetting();
        }

        // 如果已开启主动搭话且选择了搭话方式，立即启动定时器
        if (S.proactiveChatEnabled && (S.proactiveVisionChatEnabled || S.proactiveNewsChatEnabled || S.proactiveVideoChatEnabled || S.proactivePersonalChatEnabled || S.proactiveMusicEnabled || S.proactiveMemeEnabled)) {
            // 主动搭话启动自检
            console.log('========== 主动搭话启动自检 ==========');
            console.log('[自检] proactiveChatEnabled: ' + S.proactiveChatEnabled);
            console.log('[自检] proactiveVisionChatEnabled: ' + S.proactiveVisionChatEnabled);
            console.log('[自检] proactiveNewsChatEnabled: ' + S.proactiveNewsChatEnabled);
            console.log('[自检] proactiveVideoChatEnabled: ' + S.proactiveVideoChatEnabled);
            console.log('[自检] proactivePersonalChatEnabled: ' + S.proactivePersonalChatEnabled);
            console.log('[自检] proactiveMusicEnabled: ' + S.proactiveMusicEnabled);
            console.log('[自检] proactiveMemeEnabled: ' + S.proactiveMemeEnabled);
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
            console.log('  - 任意搭话模式启用: ' + (S.proactiveVisionChatEnabled || S.proactiveNewsChatEnabled || S.proactiveVideoChatEnabled || S.proactivePersonalChatEnabled || S.proactiveMusicEnabled));
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
