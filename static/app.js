/**
 * app.js — 应用编排器 (Orchestrator)
 *
 * 仅负责：
 *   1. 全局安全函数 (window.t / safeT / closeAllSettingsWindows)
 *   2. DOM 元素初始化 → appState.dom
 *   3. 各模块 init() 调用
 *   4. WebSocket 连接 & 设置加载
 *   5. 页面生命周期（beforeunload / load / DOMContentLoaded）
 *
 * 业务逻辑已拆分到 app-*.js 模块中。
 */

// ======================== 全局安全函数 ========================

// 【防崩溃兜底】确保 window.t 始终是一个可调用的函数
if (typeof window.t !== 'function') {
    window.t = function (key, fallback) {
        if (typeof fallback === 'string') return fallback;
        if (fallback && typeof fallback === 'object' && fallback.defaultValue) {
            return fallback.defaultValue;
        }
        return key;
    };
}

// 全局安全的翻译函数
window.safeT = function (key, fallback) {
    if (window.t && typeof window.t === 'function') {
        const translated = window.t(key, fallback);
        if (typeof translated === 'string') return translated;
    }
    return typeof fallback === 'string' ? fallback : key;
};

// 音乐搜索纪元管理
let currentMusicSearchEpoch = 0;
window.invalidatePendingMusicSearch = function () {
    currentMusicSearchEpoch++;
    window._pendingMusicCommand = '';
    console.log(`[Music] 搜索纪元更新至: ${currentMusicSearchEpoch}, 已失效所有在途请求`);
};

// 上次用户输入时间
let lastUserInputTime = 0;
window.lastUserInputTime = lastUserInputTime;
Object.defineProperty(window, 'lastUserInputTime', {
    get: function () { return lastUserInputTime; },
    set: function (v) { lastUserInputTime = v; },
    configurable: true,
    enumerable: true,
});

// 关闭所有已打开的设置窗口
window.closeAllSettingsWindows = function () {
    if (window._openSettingsWindows) {
        Object.keys(window._openSettingsWindows).forEach(url => {
            try {
                const winRef = window._openSettingsWindows[url];
                if (winRef && !winRef.closed) winRef.close();
            } catch (_) { }
            delete window._openSettingsWindows[url];
        });
    }
    if (window.live2dManager && window.live2dManager._openSettingsWindows) {
        Object.keys(window.live2dManager._openSettingsWindows).forEach(url => {
            try {
                const winRef = window.live2dManager._openSettingsWindows[url];
                if (winRef && !winRef.closed) winRef.close();
            } catch (_) { }
            delete window.live2dManager._openSettingsWindows[url];
        });
    }
};

// ======================== 主初始化 ========================

function init_app() {
    const S = window.appState;

    // --- 缓存 DOM 引用 ---
    S.dom.micButton = document.getElementById('micButton');
    S.dom.muteButton = document.getElementById('muteButton');
    S.dom.screenButton = document.getElementById('screenButton');
    S.dom.stopButton = document.getElementById('stopButton');
    S.dom.resetSessionButton = document.getElementById('resetSessionButton');
    S.dom.returnSessionButton = document.getElementById('returnSessionButton');
    S.dom.statusElement = document.getElementById('status');
    S.dom.statusToast = document.getElementById('status-toast');
    S.dom.chatContainer = document.getElementById('chatContainer');
    S.dom.textInputBox = document.getElementById('textInputBox');
    S.dom.textInputArea = document.getElementById('text-input-area');
    S.dom.textSendButton = document.getElementById('textSendButton');
    S.dom.screenshotButton = document.getElementById('screenshotButton');
    S.dom.avatarPreviewButton = document.getElementById('avatarPreviewButton');
    S.dom.screenshotThumbnailContainer = document.getElementById('screenshot-thumbnail-container');
    S.dom.screenshotsList = document.getElementById('screenshots-list');
    S.dom.screenshotCount = document.getElementById('screenshot-count');
    S.dom.clearAllScreenshots = document.getElementById('clear-all-screenshots');

    // --- 初始化音乐消息提示词模块 ---
    if (typeof window.MusicPrompt !== 'undefined') {
        try {
            window.MusicPrompt.initMusicPromptModule(S.dom.textInputBox, S.dom.textInputArea);
            console.log('[MusicPrompt] 模块已初始化');
        } catch (e) {
            console.error('[MusicPrompt] 初始化失败:', e);
        }
    }

    // --- 初始化点歌台模块 ---
    if (typeof window.Jukebox !== 'undefined') {
        try {
            window.Jukebox.init();
            console.log('[Jukebox] 模块已初始化');
        } catch (e) {
            console.error('[Jukebox] 初始化失败:', e);
        }
    }

    // --- 初始化各模块 ---

    // UI 模块
    if (window.appUi && window.appUi.initFloatingButtonListeners) {
        window.appUi.initFloatingButtonListeners();
    }

    // 按钮事件绑定
    if (window.appButtons && window.appButtons.init) {
        window.appButtons.init();
    }

    if (window.appChatAvatar && window.appChatAvatar.init) {
        window.appChatAvatar.init();
    }
    // WebSocket 连接
    if (window.appWebSocket && window.appWebSocket.connectWebSocket) {
        window.appWebSocket.connectWebSocket();
    }

    // 设置加载后续初始化（mic/speaker + 主动搭话调度器）
    if (window.appSettings && window.appSettings.initProactiveChatScheduler) {
        window.appSettings.initProactiveChatScheduler();
    }

    // Agent UI 初始化
    if (window.appAgent && window.appAgent.setupAgentCheckboxListeners) {
        // Agent checkbox listeners are set up via live2d-floating-buttons-ready event
        // (already registered inside app-agent.js)
    }

    // UI guards（隐藏元素 + MutationObserver）
    if (window.appUi) {
        if (window.appUi.ensureHiddenElements) window.appUi.ensureHiddenElements();
        if (window.appUi.initFinalUiGuards) window.appUi.initFinalUiGuards();
    }

    // 页面卸载前清理屏幕捕获流
    window.addEventListener('beforeunload', () => {
        try {
            if (S.screenCaptureStream && typeof S.screenCaptureStream.getTracks === 'function') {
                S.screenCaptureStream.getTracks().forEach(track => {
                    try { track.stop(); } catch (e) { }
                });
            }
        } catch (e) { }
    });

    console.log('[App] init_app() 完成');
}

// ======================== 启动序列 ========================

const ready = async () => {
    if (ready._called) return;
    ready._called = true;

    // 等待页面配置就绪（带超时）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        const TIMEOUT = Symbol('timeout');
        const TIMEOUT_MS = 3000;
        let timeoutId = null;
        try {
            const result = await Promise.race([
                window.pageConfigReady,
                new Promise(resolve => {
                    timeoutId = setTimeout(() => resolve(TIMEOUT), TIMEOUT_MS);
                })
            ]);
            if (result === TIMEOUT) {
                console.warn(`[Init] pageConfigReady pending over ${TIMEOUT_MS}ms, continue with fallback config`);
            }
        } catch (error) {
            console.warn('[Init] pageConfigReady rejected, continue with fallback config', error);
        } finally {
            if (timeoutId !== null) clearTimeout(timeoutId);
        }
    }

    init_app();
};

if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(ready, 1);
} else {
    document.addEventListener('DOMContentLoaded', ready);
    window.addEventListener('load', ready);
}

// ======================== 页面加载后的事件 ========================

// 启动提示（chat 独立窗口不弹）
window.addEventListener('load', () => {
    const _isChatPage = window.location.pathname === '/chat';

    setTimeout(() => {
        if (_isChatPage) return;
        if (typeof window.showStatusToast === 'function' &&
            typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(
                window.t ? window.t('app.started', { name: lanlan_config.lanlan_name })
                    : `${lanlan_config.lanlan_name}已启动`,
                3000
            );
        }
    }, 1000);

    // 拉取待弹重要通知 + 版本更新日志（chat 独立窗口跳过）
    setTimeout(async () => {
        if (_isChatPage) return;
        if (typeof window.showProminentNotice !== 'function') return;
        try {
            // 1) 常规 prominent notices
            const r = await fetch('/api/pending-notices');
            const data = await r.json();
            const notices = Array.isArray(data) ? data : (data.notices || []);
            const cursor = (data && typeof data.cursor === 'number') ? data.cursor : 0;
            if (notices.length > 0) {
                // 先全部入队（不 await），让 UI 能感知队列长度以显示"下一个"按钮
                const promises = notices.filter(Boolean).map(n => window.showProminentNotice(n));
                await Promise.all(promises);
                await fetch('/api/pending-notices/ack', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ cursor }),
                }).catch(() => { });
            }
        } catch (_) { }

        // 2) 版本更新日志
        try {
            const lastVer = localStorage.getItem('neko_last_notified_version') || '';
            const lang = (window.i18next && window.i18next.language) || '';
            const cr = await fetch(`/api/changelog?since=${encodeURIComponent(lastVer)}&lang=${encodeURIComponent(lang)}`);
            const cdata = await cr.json();
            let entries = cdata.entries || [];
            // 全新用户（无历史记录）跳过版本更新弹窗，直接记录当前版本
            if (!lastVer) {
                if (cdata.current_version) {
                    localStorage.setItem('neko_last_notified_version', cdata.current_version);
                }
                entries = [];
            }
            if (entries.length > 0) {
                const changelogPromises = entries.map(entry => {
                    const title = `v${entry.version} ${window.safeT ? window.safeT('notice.changelog.title', '更新内容') : '更新内容'}`;
                    return window.showProminentNotice({
                        message: `**${title}**\n\n${(entry.content || '').trim()}`,
                    });
                });
                await Promise.all(changelogPromises);
                if (cdata.current_version) {
                    localStorage.setItem('neko_last_notified_version', cdata.current_version);
                }
            }
        } catch (_) { }
    }, 2000);
});

// 监听 voice_id 更新和 VRM 表情预览消息
window.addEventListener('message', function (event) {
    if (event.origin !== window.location.origin) return;
    if (!event || !event.data || typeof event.data.type === 'undefined') return;

    if (event.data.type === 'voice_id_updated') {
        console.log('[Voice Clone] 收到voice_id更新消息:', event.data.voice_id);
        if (typeof window.showStatusToast === 'function' &&
            typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(
                window.t ? window.t('app.voiceUpdated', { name: lanlan_config.lanlan_name })
                    : `${lanlan_config.lanlan_name}的语音已更新`,
                3000
            );
        }
    }

    if (event.data.type === 'vrm-preview-expression') {
        if (typeof event.data.expression === 'undefined') return;
        console.log('[VRM] 收到表情预览请求:', event.data.expression);
        if (window.vrmManager && window.vrmManager.expression) {
            window.vrmManager.expression.setBaseExpression(event.data.expression);
        }
    }

    if (event.data.type === 'vrm-get-expressions') {
        console.log('[VRM] 收到表情列表请求');
        let expressions = [];
        if (window.vrmManager && window.vrmManager.expression) {
            expressions = window.vrmManager.expression.getExpressionList();
        }
        if (event.source) {
            event.source.postMessage({
                type: 'vrm-expressions-response',
                expressions: expressions
            }, window.location.origin);
        }
    }

    if (event.data.type === 'mmd-get-morphs') {
        console.log('[MMD] 收到 Morph 列表请求');
        let morphs = [];
        if (window.mmdManager && window.mmdManager.expression) {
            morphs = window.mmdManager.expression.getMorphNames();
        }
        if (event.source) {
            event.source.postMessage({
                type: 'mmd-morphs-response',
                morphs: morphs
            }, window.location.origin);
        }
    }

    if (event.data.type === 'mmd-preview-morph') {
        if (typeof event.data.morph === 'undefined') return;
        console.log('[MMD] 收到 Morph 预览请求:', event.data.morph);
        if (window.mmdManager && window.mmdManager.expression) {
            clearTimeout(window._mmdMorphPreviewTimer);
            window.mmdManager.expression._clearEmotionMorphs();
            window.mmdManager.expression.setMorphWeight(event.data.morph, 1.0);
            var morphToReset = event.data.morph;
            window._mmdMorphPreviewTimer = setTimeout(() => {
                if (window.mmdManager && window.mmdManager.expression) {
                    window.mmdManager.expression.setMorphWeight(morphToReset, 0);
                }
            }, 3000);
        }
    }
});
