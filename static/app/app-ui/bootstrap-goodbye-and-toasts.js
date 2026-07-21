/**
 * app-ui/bootstrap-goodbye-and-toasts.js
 * UI display helpers extracted from app.js.
 *
 * Exposed as window.appUi.
 * Dependencies:
 * - window.appState (S) - shared mutable state
 * - window.appConst (C) - frozen constants
 * - window.appUtils - utility helpers
 * - window.t / window.safeT - i18n
 * - window.lanlan_config - character config
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appUi = window.appUi || {};
    const I = window.__appUiParts || (window.__appUiParts = {});
I.mod = window.appUi;
    I.S = window.appState;
    const C = window.appConst;
    I.NEKO_MODEL_CAT_TRANSITION_ASSET = '/static/assets/neko-idle/cat_model_change.gif';
    I.NEKO_MODEL_CAT_TRANSITION_DURATION_MS = 850;
    I.NEKO_MODEL_CAT_TRANSITION_LOOP_GUARD_MS = 70;
    I.NEKO_MODEL_CAT_REVEAL_BEFORE_SMOKE_HIDE_MS = 48;
    I.NEKO_MODEL_CAT_TRANSITION_LOAD_FALLBACK_MS = 1200;
    I.NEKO_MODEL_CAT_TO_MODEL_LOCK_MS = 1120;
    I.NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE = 0.38;
    I.NEKO_MODEL_CAT_TRANSITION_MIN_SIZE = 260;
    I.NEKO_MODEL_CAT_TRANSITION_MAX_SIZE = 680;
    I.NEKO_MODEL_CAT_TRANSITION_SIZE_FACTOR = 0.86;
    I.NEKO_MODEL_CAT_TRANSITION_EDGE_MASK = 'radial-gradient(circle at center, #000 0%, #000 44%, rgba(0,0,0,0.72) 58%, rgba(0,0,0,0.18) 72%, rgba(0,0,0,0) 88%, rgba(0,0,0,0) 100%)';
    I.NEKO_MODEL_RETURN_ENTER_TRANSITION = 'opacity 1120ms ease-out, transform 1080ms cubic-bezier(0.22, 1, 0.36, 1)';
    I.NEKO_MODEL_RETURN_ENTER_CLEANUP_MS = 1160;
    I.NEKO_MODEL_RETURN_ENTER_SETTLE_BUFFER_MS = 180;
    I.NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION = 'opacity 1.12s ease-out';
    I.NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS = 1160;
    I.NEKO_MODEL_GOODBYE_VISUAL_FADE_TRANSITION = 'opacity 240ms ease-in';
    I.NEKO_GOODBYE_IDLE_APPEARANCE_CAT = 'cat';
    I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL = 'ball';
    I.NEKO_GOODBYE_IDLE_APPEARANCE_ATTR = 'data-neko-goodbye-idle-appearance';
    I.NEKO_GOODBYE_IDLE_BALL_ASSET = '/static/icons/expand_icon_off_ball.png';
    I.normalizeNekoGoodbyeIdleAppearance = function normalizeNekoGoodbyeIdleAppearance(mode) {
        return mode === I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL
            ? I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL
            : I.NEKO_GOODBYE_IDLE_APPEARANCE_CAT;
    }
    I.NEKO_MODEL_CAT_TRANSITION_VERSION = (() => {
        try {
            const currentScript = document.currentScript;
            if (currentScript && currentScript.src) {
                return new URL(currentScript.src, window.location.href).searchParams.get('v') || '';
            }
        } catch (_) {}
        return '';
    })();
    I.nekoModelCatTransitionToken = 0;
    I.nekoModelCatTransitionActive = null;
    I.nekoModelCatRevealPlaybackToken = 0;
    I.nekoGoodbyeIdleAppearance = I.normalizeNekoGoodbyeIdleAppearance(window.__nekoGoodbyeIdleAppearance);
    window.__nekoGoodbyeIdleAppearance = I.nekoGoodbyeIdleAppearance;
    const GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY = 'neko-goodbye-resource-suspended';
    let goodbyeResourceSuspendToken = 0;

    function getGoodbyeResourceSnapshot() {
        return I.S && I.S.goodbyeResourceSuspendSnapshot ? I.S.goodbyeResourceSuspendSnapshot : null;
    }

    function publishGoodbyeResourceState(snapshot, source) {
        const suspended = !!(snapshot && snapshot.suspended);
        const pending = !!(snapshot && snapshot.pending);
        if (I.S) {
            I.S.goodbyeResourceSuspended = suspended;
            I.S.goodbyeResourceSuspendPending = pending;
            I.S.goodbyeResourceSuspendSnapshot = snapshot || null;
        }
        window.goodbyeResourceSuspended = suspended;
        window.__nekoGoodbyeResourceSuspendPending = pending;
        window.__nekoGoodbyeResourceSuspendSnapshot = snapshot || null;
        try {
            localStorage.setItem(GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY, suspended ? 'true' : 'false');
        } catch (_) { /* ignore */ }
        window.dispatchEvent(new CustomEvent('neko:goodbye-resource-suspend-state', {
            detail: {
                suspended,
                pending,
                source: source || 'goodbye-resource',
                token: snapshot ? snapshot.token : goodbyeResourceSuspendToken,
                activeModelType: snapshot ? snapshot.activeModelType : ''
            }
        }));
    }

    publishGoodbyeResourceState(null, 'goodbye-resource-boot');

    window.isNekoGoodbyeResourceSuspended = function () {
        return !!(I.S && I.S.goodbyeResourceSuspended);
    };

    window.isNekoGoodbyeResourceSuspendingOrSuspended = function () {
        return !!(I.S && (I.S.goodbyeResourceSuspended || I.S.goodbyeResourceSuspendPending));
    };

    function isVisibleElement(el) {
        if (!el) return false;
        if (el.classList && el.classList.contains('hidden')) return false;
        if (el.style && el.style.display === 'none') return false;
        try {
            const style = window.getComputedStyle ? window.getComputedStyle(el) : null;
            if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
                return false;
            }
        } catch (_) { /* ignore */ }
        return true;
    }

    function isElementVisibleById(id) {
        return isVisibleElement(document.getElementById(id));
    }

    function getActiveGoodbyeModelType(fallbackType) {
        if (fallbackType) return fallbackType;
        if (isElementVisibleById('mmd-container')) return 'mmd';
        if (isElementVisibleById('vrm-container')) return 'vrm';
        const configured = (window.lanlan_config?.model_type || '').toLowerCase();
        if (configured === 'pngtuber' && isElementVisibleById('pngtuber-container')) return 'pngtuber';
        return 'live2d';
    }

    function getModelManagerByType(type) {
        if (type === 'live2d') return window.live2dManager;
        if (type === 'vrm') return window.vrmManager;
        if (type === 'mmd') return window.mmdManager;
        if (type === 'pngtuber') return window.pngtuberManager;
        return null;
    }

    function isModelRenderingActive(type, manager) {
        if (!manager) return false;
        // 各形态的空闲低频 tick 模式下，rAF id / ticker.started 都是"停"的假象
        // （渲染由 setInterval 驱动）——必须把 _idleTickMode 也算作"在渲染"，
        // 否则 goodbye 资源暂停会漏掉它们。
        if (type === 'live2d') {
            const ticker = manager.pixi_app && manager.pixi_app.ticker;
            return !!(ticker && (ticker.started !== false || manager._idleTickMode));
        }
        if (type === 'vrm') {
            return !!(manager._animationFrameId || manager._idleTickMode);
        }
        if (type === 'mmd') {
            return !!(manager._animationFrameId || (manager.core && manager.core._idleTickMode));
        }
        if (type === 'pngtuber') {
            const container = manager.container || document.getElementById('pngtuber-container');
            return !!(container && container.style.display !== 'none' &&
                !(container.classList && container.classList.contains('hidden')));
        }
        return false;
    }

    function pauseModelRenderingForGoodbye(snapshot) {
        const activeModelType = snapshot && snapshot.activeModelType;
        ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach((type) => {
            const manager = getModelManagerByType(type);
            if (!manager || typeof manager.pauseRendering !== 'function') return;
            if (activeModelType !== type && !isModelRenderingActive(type, manager)) return;
            try {
                manager.pauseRendering();
                snapshot.pausedByCat[type] = true;
            } catch (error) {
                console.warn('[GoodbyeResource] pauseRendering failed:', type, error);
            }
        });
    }

    function resumeModelRenderingFromGoodbye(snapshot) {
        if (!snapshot || !snapshot.pausedByCat) return;
        ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach((type) => {
            if (!snapshot.pausedByCat[type]) return;
            const manager = getModelManagerByType(type);
            if (!manager || typeof manager.resumeRendering !== 'function') return;
            try {
                manager.resumeRendering();
            } catch (error) {
                console.warn('[GoodbyeResource] resumeRendering failed:', type, error);
            }
        });
    }

    function isSubtitleDisplayVisible() {
        return isVisibleElement(document.getElementById('subtitle-display'));
    }

    function wasSubtitleVisibleBeforeGoodbyeSnapshot() {
        try {
            if (window.subtitleBridge && typeof window.subtitleBridge.wasVisibleBeforeGoodbye === 'function' &&
                window.subtitleBridge.wasVisibleBeforeGoodbye()) {
                return true;
            }
        } catch (_) { /* ignore */ }
        return isSubtitleDisplayVisible();
    }

    function isAgentHudVisible() {
        const hud = document.getElementById('agent-task-hud');
        return !!(hud && hud.style.display !== 'none' && hud.style.opacity !== '0');
    }

    function hideSubtitleSettingsDomForGoodbye() {
        const panel = document.getElementById('subtitle-settings-panel');
        if (panel) {
            panel.classList.add('hidden');
            panel.style.opacity = '0';
        }
        const settingsBtn = document.getElementById('subtitle-settings-btn');
        if (settingsBtn) {
            settingsBtn.setAttribute('aria-expanded', 'false');
        }
    }

    function hideGoodbyeAuxiliaryWindows(snapshot) {
        try {
            if (window.subtitleBridge && typeof window.subtitleBridge.suspendForGoodbye === 'function') {
                window.subtitleBridge.suspendForGoodbye({ source: 'goodbye-resource-suspend' });
            }
        } catch (error) {
            console.warn('[GoodbyeResource] subtitle suspend failed:', error);
        }
        hideSubtitleSettingsDomForGoodbye();
        try {
            if (window.nekoSubtitleWindow && typeof window.nekoSubtitleWindow.hide === 'function') {
                window.nekoSubtitleWindow.hide();
            }
        } catch (_) { /* ignore */ }
        try {
            if (window.AgentHUD && typeof window.AgentHUD.hideAgentTaskHUD === 'function') {
                window.AgentHUD.hideAgentTaskHUD();
            }
            if (typeof window.stopAgentTaskPolling === 'function') {
                window.stopAgentTaskPolling({ source: 'goodbye-resource-suspend' });
            }
            if (window.nekoAgentHud && typeof window.nekoAgentHud.hide === 'function') {
                window.nekoAgentHud.hide();
            }
        } catch (error) {
            console.warn('[GoodbyeResource] Agent HUD suspend failed:', error);
        }
        snapshot.subtitleWindowHiddenByCat = true;
        snapshot.agentHudHiddenByCat = true;
    }

    I.beginGoodbyeResourceSuspend = function beginGoodbyeResourceSuspend(options = {}) {
        const token = ++goodbyeResourceSuspendToken;
        const activeModelType = getActiveGoodbyeModelType(options.activeModelType);
        const snapshot = {
            token,
            pending: true,
            suspended: false,
            activeModelType,
            pausedByCat: { live2d: false, vrm: false, mmd: false, pngtuber: false },
            subtitleWindowWasVisible: wasSubtitleVisibleBeforeGoodbyeSnapshot(),
            agentHudWasVisible: isAgentHudVisible(),
            subtitleWindowHiddenByCat: false,
            agentHudHiddenByCat: false
        };
        publishGoodbyeResourceState(snapshot, 'goodbye-resource-pending');
        return token;
    }

    I.completeGoodbyeResourceSuspend = function completeGoodbyeResourceSuspend(token) {
        const snapshot = getGoodbyeResourceSnapshot();
        if (!snapshot || snapshot.token !== token) return;
        if (typeof window.isNekoGoodbyeModeActive === 'function' && !window.isNekoGoodbyeModeActive()) {
            publishGoodbyeResourceState(null, 'goodbye-resource-stale');
            return;
        }
        snapshot.pending = false;
        snapshot.suspended = true;
        publishGoodbyeResourceState(snapshot, 'goodbye-resource-suspended');
        hideGoodbyeAuxiliaryWindows(snapshot);
        pauseModelRenderingForGoodbye(snapshot);
        publishGoodbyeResourceState(snapshot, 'goodbye-resource-paused');
    }

    I.restoreGoodbyeResourceSuspend = function restoreGoodbyeResourceSuspend(reason) {
        goodbyeResourceSuspendToken += 1;
        const snapshot = getGoodbyeResourceSnapshot();
        if (!snapshot) {
            publishGoodbyeResourceState(null, reason || 'goodbye-resource-restore-empty');
            return;
        }
        resumeModelRenderingFromGoodbye(snapshot);
        publishGoodbyeResourceState(null, reason || 'goodbye-resource-restoring');
        try {
            if (window.subtitleBridge && typeof window.subtitleBridge.restoreAfterGoodbye === 'function') {
                window.subtitleBridge.restoreAfterGoodbye({
                    source: reason || 'goodbye-resource-restore',
                    restoreWindow: true
                });
            }
        } catch (error) {
            console.warn('[GoodbyeResource] subtitle restore failed:', error);
        }
        if (snapshot.subtitleWindowWasVisible) {
            try {
                if (window.nekoSubtitleWindow && typeof window.nekoSubtitleWindow.show === 'function') {
                    window.nekoSubtitleWindow.show();
                }
            } catch (_) { /* ignore */ }
        }
        if (snapshot.agentHudWasVisible) {
            try {
                if (window.AgentHUD && typeof window.AgentHUD.showAgentTaskHUD === 'function') {
                    window.AgentHUD.showAgentTaskHUD();
                }
                if (typeof window.checkAndToggleTaskHUD === 'function') {
                    window.checkAndToggleTaskHUD();
                }
            } catch (error) {
                console.warn('[GoodbyeResource] Agent HUD restore failed:', error);
            }
        }
    }

    I.mod.restoreGoodbyeResourceSuspend = I.restoreGoodbyeResourceSuspend;
    I.mod.completeGoodbyeResourceSuspend = I.completeGoodbyeResourceSuspend;
    window.addEventListener('neko:goodbye-state-cleared', (event) => {
        const detail = event && event.detail ? event.detail : {};
        const reason = detail.reason || 'goodbye-state-cleared';
        I.restoreGoodbyeResourceSuspend(reason);
    });

    // ================================================================
    //  1. Status toast  (app.js lines 86-145)
    // ================================================================

    /**
     * Show / hide the floating status toast bubble.
     * @param {string} message  Text to display (empty string hides)
     * @param {number} [duration=3000]  Auto-hide delay in ms
     * @param {object} [options]  Additional options
     * @param {number} [options.priority=0]  Priority level (higher = more important, won't be overwritten by lower priority)
     * @param {boolean} [options.important=false]  Whether this is an important message (same as priority=100)
     */
    I.showStatusToast = function showStatusToast(message, duration = 3000, options = {}) {
        const priority = options.important ? 100 : (options.priority || 0);

        if (!message || message.trim() === '') {
            const statusToast = I.S.dom.statusToast;
            if (statusToast) {
                if (I.S._statusToastCleanupTimer) {
                    clearTimeout(I.S._statusToastCleanupTimer);
                    I.S._statusToastCleanupTimer = null;
                }
                statusToast.classList.remove('show');
                statusToast.classList.add('hide');
                I.S._statusToastCleanupTimer = setTimeout(() => {
                    statusToast.textContent = '';
                    I.S._statusToastCleanupTimer = null;
                }, 300);
            }
            I.S._statusToastPriority = 0;
            return;
        }

        if (priority < I.S._statusToastPriority) {
            console.log('[StatusToast] Ignored lower priority message:', priority, '<', I.S._statusToastPriority);
            return;
        }

        console.log(window.t('console.statusToastShow'), message, window.t('console.statusToastDuration'), duration);

        const statusToast = I.S.dom.statusToast || document.getElementById('status-toast');
        const statusElement = I.S.dom.statusElement || document.getElementById('status');

        if (!statusToast) {
            console.error(window.t('console.statusToastNotFound'));
            return;
        }
        I.S.dom.statusToast = statusToast;
        if (statusElement) {
            I.S.dom.statusElement = statusElement;
        }

        // 清除之前的定时器
        if (I.S.statusToastTimeout) {
            clearTimeout(I.S.statusToastTimeout);
            I.S.statusToastTimeout = null;
        }
        if (I.S._statusToastCleanupTimer) {
            clearTimeout(I.S._statusToastCleanupTimer);
            I.S._statusToastCleanupTimer = null;
        }

        // 更新内容
        statusToast.textContent = message;
        I.S._statusToastPriority = priority;

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
        I.S.statusToastTimeout = setTimeout(() => {
            statusToast.classList.remove('show');
            statusToast.classList.add('hide');
            I.S._statusToastCleanupTimer = setTimeout(() => {
                statusToast.textContent = '';
                I.S._statusToastPriority = 0;
                I.S._statusToastCleanupTimer = null;
            }, 300);
        }, duration);

        // 同时更新隐藏的 status 元素（保持兼容性）
        if (statusElement) {
            statusElement.textContent = message || '';
        }
    }

    I.mod.showStatusToast = I.showStatusToast;
    // 全局兼容
    window.showStatusToast = I.showStatusToast;

    // ================================================================
    //  2. Voice toasts & prominent notice  (app.js lines 3674-3999)
    // ================================================================

    function normalizeVoiceToastMessage(message) {
        var fallbackKey = 'app.voiceSystemPreparing';
        var defaultFallback = '语音系统准备中...';

        function usableText(value) {
            if (typeof value !== 'string') return '';
            var text = value.trim();
            if (!text || text === '[object Module]' || text === '[object Object]') return '';
            return value;
        }
        var fallback = defaultFallback;
        if (typeof window.t === 'function') {
            var translatedFallback = usableText(window.t(fallbackKey, defaultFallback));
            if (translatedFallback && translatedFallback.trim() !== fallbackKey) {
                fallback = translatedFallback;
            }
        }

        var directText = usableText(message);
        if (directText) return directText;

        if (message && typeof message === 'object') {
            if (typeof window.translateStatusMessage === 'function') {
                var translated = usableText(window.translateStatusMessage(message));
                if (translated) return translated;
            }
            var nestedText = usableText(message.message);
            if (nestedText) return nestedText;
            var codeText = usableText(message.code);
            if (codeText) return codeText;
            console.warn('[VoiceToast] Non-string message ignored:', message);
        } else if (message !== undefined && message !== null) {
            console.warn('[VoiceToast] Unusable message ignored:', message);
        }

        return fallback;
    }

    // --- showVoicePreparingToast ---
    I.showVoicePreparingToast = function showVoicePreparingToast(message) {
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

        // 更新消息内容（使用 DOM API 避免 innerHTML 注入风险）
        toast.innerHTML = '';
        var spinner = document.createElement('div');
        spinner.style.cssText = 'width:20px;height:20px;border:3px solid rgba(255,255,255,0.3);border-top-color:white;border-radius:50%;animation:spin 1s linear infinite;';
        var msgSpan = document.createElement('span');
        msgSpan.textContent = normalizeVoiceToastMessage(message);
        toast.appendChild(spinner);
        toast.appendChild(msgSpan);

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

    I.mod.showVoicePreparingToast = I.showVoicePreparingToast;

    // --- hideVoicePreparingToast ---
    I.hideVoicePreparingToast = function hideVoicePreparingToast() {
        const toast = document.getElementById('voice-preparing-toast');
        if (toast) {
            toast.style.animation = 'voiceToastFadeIn 0.3s ease reverse';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 300);
        }
    }

    I.mod.hideVoicePreparingToast = I.hideVoicePreparingToast;

    // --- Prominent notice (modal queue) ---
    const _prominentNoticeQueue = [];
    let _prominentNoticeActive = false;

    function _prominentNoticeText(key, fallback) {
        try {
            if (typeof window.safeT === 'function') {
                const translated = window.safeT(key, fallback);
                if (typeof translated === 'string' && translated && translated !== key) {
                    return translated;
                }
            }
            if (typeof window.t === 'function') {
                const translated = window.t(key, { defaultValue: fallback });
                if (typeof translated === 'string' && translated && translated !== key) {
                    return translated;
                }
            }
        } catch (_) { }
        return fallback;
    }

    function _escapeProminentNoticeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function _stripProminentMarkdown(value) {
        return String(value || '')
            .replace(/^#{1,6}\s+/, '')
            .replace(/\*\*(.*?)\*\*/g, '$1')
            .replace(/(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)/g, '$1')
            .trim();
    }

    function _renderChangelogNoticeContent(container, notice, displayText) {
        const lines = String(displayText || '')
            .split(/\r?\n/)
            .map(line => line.trim())
            .filter(Boolean);
        const firstLine = lines[0] || '';
        const firstLineIsItem = /^[-•]\s+/.test(firstLine);
        const headingText = firstLineIsItem ? '' : _stripProminentMarkdown(firstLine);
        const versionMatch = headingText.match(/^v?([0-9]+(?:\.[0-9]+)*)(?:\s+(.+))?$/i);
        const version = notice.version || (versionMatch ? versionMatch[1] : '');
        const title = notice.title || (versionMatch ? versionMatch[2] : '') || headingText || _prominentNoticeText('notice.changelog.title', '更新内容');
        const firstLineIsHeading = !!firstLine
            && !firstLineIsItem
            && (!!versionMatch || (!notice.title && !!headingText));
        const itemLines = lines
            .slice(firstLineIsHeading ? 1 : 0)
            .filter(line => /^[-•]\s+/.test(line));

        const itemsHtml = itemLines.map(line => {
            const content = line.replace(/^[-•]\s+/, '').trim();
            const match = content.match(/^\*\*(.+?)\*\*\s*[:：]\s*(.+)$/);
            const itemTitle = match ? match[1] : '';
            const itemBody = match ? match[2] : _stripProminentMarkdown(content);
            return [
                '<li class="prominent-notice-changelog-item">',
                '<span class="prominent-notice-changelog-dot" aria-hidden="true"></span>',
                '<span class="prominent-notice-changelog-copy">',
                itemTitle ? '<strong>' + _escapeProminentNoticeHtml(itemTitle) + '</strong>' : '',
                '<span>', _escapeProminentNoticeHtml(itemBody), '</span>',
                '</span>',
                '</li>',
            ].join('');
        }).join('');

        container.innerHTML = [
            '<div class="prominent-notice-changelog-head">',
            version ? '<span class="prominent-notice-changelog-version">v' + _escapeProminentNoticeHtml(version) + '</span>' : '',
            '<h2>', _escapeProminentNoticeHtml(title), '</h2>',
            '</div>',
            '<ul class="prominent-notice-changelog-list">',
            itemsHtml || '<li class="prominent-notice-changelog-item"><span class="prominent-notice-changelog-copy"><span>' + _escapeProminentNoticeHtml(_stripProminentMarkdown(displayText)) + '</span></span></li>',
            '</ul>',
        ].join('');
    }

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
        // 回退文本优先级：按用户 locale 选择语言
        const _isChinese = (typeof _isUserRegionChina === 'function' && _isUserRegionChina())
            || /^zh/i.test(navigator.language || '');
        const localeFallback = _isChinese
            ? (notice.message || notice.message_en || '')
            : (notice.message_en || notice.message || '');
        const displayText = (notice.code && typeof safeT === 'function')
            ? safeT(notice.code, localeFallback)
            : localeFallback;
        const isChangelogNotice = notice && notice.kind === 'changelog';

        // Electron 桌面宠物模式下 body 为 pointer-events:none，
        // 导致 preload 轮询器的 elementFromPoint 无法检测到 overlay，
        // 窗口穿透不会解除，按钮点不了。显示期间临时恢复 body pointer-events。
        const bodyPE = document.body.style.pointerEvents;
        const needRestoreBodyPE = getComputedStyle(document.body).pointerEvents === 'none';
        if (needRestoreBodyPE) {
            document.body.style.pointerEvents = 'auto';
        }

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
        box.className = isChangelogNotice
            ? 'prominent-notice-box prominent-notice-box-changelog'
            : 'prominent-notice-box';
        box.style.cssText = isChangelogNotice
            ? `
                position: relative;
                background: linear-gradient(180deg, #fffafd 0%, #f1f8ff 100%);
                color: #334155;
                border: 1px solid rgba(255,255,255,0.92);
                border-radius: 26px;
                padding: 28px 30px 24px;
                width: min(640px, calc(100vw - 44px));
                max-height: min(82vh, 720px);
                box-sizing: border-box;
                box-shadow: 0 24px 70px rgba(92,132,184,0.28), inset 0 1px 0 rgba(255,255,255,0.95);
                text-align: left;
                pointer-events: auto;
                display: flex;
                flex-direction: column;
                align-items: stretch;
                animation: pnBoxIn 0.3s ease;
            `
            : `
                position: relative;
                background: #1e293b;
                color: #f1f5f9;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 16px;
                padding: 32px 28px 24px;
                width: 370px; max-width: 88vw;
                max-height: min(82vh, 720px);
                box-sizing: border-box;
                box-shadow: 0 12px 40px rgba(0,0,0,0.5);
                text-align: center;
                pointer-events: auto;
                display: flex;
                flex-direction: column;
                align-items: center;
                animation: pnBoxIn 0.3s ease;
            `;

        const btn = document.createElement('button');
        const _hasMore = _prominentNoticeQueue.length > 0;
        btn.textContent = _hasMore
            ? _prominentNoticeText('common.next', '下一个')
            : _prominentNoticeText('common.confirm', '确认');
        btn.style.cssText = isChangelogNotice
            ? `
                align-self: center;
                min-width: 150px;
                background: linear-gradient(180deg, #8dccff 0%, #65aef4 100%);
                color: #fff;
                border: none;
                border-radius: 999px;
                padding: 12px 42px;
                font-size: 15px;
                font-weight: 700;
                cursor: pointer;
                pointer-events: auto;
                transition: transform 0.15s ease, filter 0.15s ease;
                box-shadow: 0 14px 30px rgba(101,174,244,0.34), inset 0 3px 6px rgba(255,255,255,0.36);
                flex-shrink: 0;
            `
            : `
                background: #3b82f6; color: #fff; border: none;
                border-radius: 10px; padding: 10px 48px;
                font-size: 15px; font-weight: 600; cursor: pointer;
                pointer-events: auto;
                transition: background 0.15s;
                flex-shrink: 0;
            `;

        const icon = document.createElement('img');
        if (!isChangelogNotice) {
            icon.src = '/static/icons/exclamation.png';
            icon.alt = '';
            icon.style.cssText = 'width:36px;height:36px;margin-bottom:14px;flex-shrink:0;';
        }

        const textDiv = document.createElement('div');
        textDiv.className = isChangelogNotice
            ? 'prominent-notice-body prominent-notice-body-changelog'
            : 'prominent-notice-body';
        textDiv.style.cssText = [
            isChangelogNotice ? 'font-size:14px' : 'font-size:16px',
            isChangelogNotice ? 'font-weight:500' : 'font-weight:600',
            isChangelogNotice ? 'line-height:1.55' : 'line-height:1.7',
            isChangelogNotice ? 'margin-bottom:20px' : 'margin-bottom:22px',
            'text-align:left',
            'width:100%',
            'min-height:0',
            'flex:1 1 auto',
            isChangelogNotice ? 'max-height:min(56vh,460px)' : 'max-height:min(54vh,420px)',
            'overflow-y:auto',
            'overflow-x:hidden',
            'overflow-wrap:anywhere',
            'scrollbar-gutter:stable',
            'scrollbar-width:thin',
            'scrollbar-color:rgba(148,163,184,0.62) rgba(15,23,42,0.18)',
            'padding-right:8px',
            'overscroll-behavior:contain',
            'box-sizing:border-box',
        ].join(';');
        if (isChangelogNotice) {
            _renderChangelogNoticeContent(textDiv, notice, displayText);
        } else if (typeof window.renderMiniMarkdown === 'function') {
            textDiv.innerHTML = window.renderMiniMarkdown(displayText);
        } else {
            textDiv.textContent = displayText;
        }

        if (!isChangelogNotice) {
            box.appendChild(icon);
        }
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
                .prominent-notice-body::-webkit-scrollbar {
                    width: 8px;
                }
                .prominent-notice-body::-webkit-scrollbar-track {
                    background: transparent;
                }
                .prominent-notice-body::-webkit-scrollbar-thumb {
                    background: rgba(148, 163, 184, 0.42);
                    border-radius: 999px;
                    border: 2px solid transparent;
                    background-clip: padding-box;
                }
                .prominent-notice-body::-webkit-scrollbar-thumb:hover {
                    background: rgba(148, 163, 184, 0.62);
                    border: 2px solid transparent;
                    background-clip: padding-box;
                }
                .prominent-notice-box-changelog .prominent-notice-body::-webkit-scrollbar-thumb {
                    background: rgba(126, 166, 211, 0.36);
                }
                .prominent-notice-box-changelog .prominent-notice-body::-webkit-scrollbar-thumb:hover {
                    background: rgba(126, 166, 211, 0.58);
                }
                .prominent-notice-changelog-head {
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 12px;
                    margin: 0 0 18px;
                    text-align: center;
                }
                .prominent-notice-changelog-version {
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    min-width: 64px;
                    height: 32px;
                    padding: 0 12px;
                    border-radius: 999px;
                    background: rgba(107, 176, 242, 0.14);
                    color: #4f91d6;
                    font-size: 14px;
                    font-weight: 800;
                    box-shadow: inset 0 0 0 1px rgba(107,176,242,0.18);
                }
                .prominent-notice-changelog-head h2 {
                    margin: 0;
                    color: #334155;
                    font-size: 24px;
                    line-height: 1.25;
                    font-weight: 800;
                    letter-spacing: 0;
                }
                .prominent-notice-changelog-list {
                    display: grid;
                    grid-template-columns: 1fr;
                    gap: 10px;
                    margin: 0;
                    padding: 0;
                    list-style: none;
                }
                .prominent-notice-changelog-item {
                    display: grid;
                    grid-template-columns: 10px 1fr;
                    gap: 12px;
                    align-items: start;
                    margin: 0 !important;
                    padding: 12px 14px;
                    list-style: none !important;
                    border-radius: 14px;
                    background: rgba(255,255,255,0.62);
                    box-shadow: inset 0 0 0 1px rgba(148,163,184,0.13);
                    text-align: left !important;
                }
                .prominent-notice-changelog-dot {
                    width: 8px;
                    height: 8px;
                    margin-top: 7px;
                    border-radius: 999px;
                    background: #8dccff;
                    box-shadow: 0 0 0 4px rgba(141,204,255,0.18);
                }
                .prominent-notice-changelog-copy {
                    display: flex;
                    flex-direction: column;
                    gap: 3px;
                    min-width: 0;
                }
                .prominent-notice-changelog-copy strong {
                    color: #475569;
                    font-size: 15px;
                    line-height: 1.35;
                    font-weight: 800;
                }
                .prominent-notice-changelog-copy span {
                    color: #64748b;
                    font-size: 13px;
                    line-height: 1.55;
                    font-weight: 600;
                }
                .prominent-notice-box-changelog button:hover,
                .prominent-notice-box-changelog button:focus-visible {
                    transform: translateY(-2px);
                    filter: brightness(1.03);
                    outline: none;
                }
                .prominent-notice-box-changelog button:active {
                    transform: translateY(0) scale(0.98);
                }
                @media (max-width: 560px) {
                    .prominent-notice-changelog-head {
                        flex-direction: column;
                        gap: 8px;
                    }
                    .prominent-notice-changelog-head h2 {
                        font-size: 20px;
                    }
                    .prominent-notice-changelog-item {
                        padding: 11px 12px;
                    }
                }
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
                // 恢复 body pointer-events
                if (needRestoreBodyPE) {
                    document.body.style.pointerEvents = bodyPE;
                }
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
            notice = { message: String(noticeOrMessage ?? '') };
        }
        return new Promise((resolve) => {
            _prominentNoticeQueue.push({ notice, resolve });
            _drainProminentNoticeQueue();
        });
    }

    I.mod.showProminentNotice = showProminentNotice;
    window.showProminentNotice = showProminentNotice;

    // --- showSurveyModal ---
    // 版本问卷弹窗：在 changelog 确认后对老玩家弹出。题目来自后端 /api/survey
    // （已本地化），支持单选 / 多选 / 填空。返回 Promise，resolve 为
    //   { action: 'submit', answers: {qid: value|[values]|text} }  或
    //   { action: 'skip',   answers: {} }
    // 调用方据此 POST /api/survey/submit 并记 localStorage（不再重复弹）。
    function _surveyText(key, fallback) {
        try {
            if (typeof window.t === 'function') {
                const v = window.t(key);
                if (typeof v === 'string' && v && v !== key) return v;
            }
        } catch (_) { }
        return fallback;
    }

    function showSurveyModal(survey) {
        survey = survey || {};
        const questions = Array.isArray(survey.questions) ? survey.questions : [];

        return new Promise((resolve) => {
            const bodyPE = document.body.style.pointerEvents;
            const needRestoreBodyPE = getComputedStyle(document.body).pointerEvents === 'none';
            if (needRestoreBodyPE) document.body.style.pointerEvents = 'auto';

            const overlay = document.createElement('div');
            overlay.id = 'survey-modal-overlay';
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
            box.setAttribute('aria-label', survey.title || 'Survey');
            box.tabIndex = -1;
            box.className = 'survey-modal-box';
            box.style.cssText = `
                position: relative;
                background: linear-gradient(180deg, #fffafd 0%, #f1f8ff 100%);
                color: #334155;
                border: 1px solid rgba(255,255,255,0.92);
                border-radius: 26px;
                padding: 26px 28px 22px;
                width: min(560px, calc(100vw - 44px));
                max-height: min(86vh, 760px);
                box-sizing: border-box;
                box-shadow: 0 24px 70px rgba(92,132,184,0.28), inset 0 1px 0 rgba(255,255,255,0.95);
                text-align: left;
                pointer-events: auto;
                display: flex;
                flex-direction: column;
                align-items: stretch;
                animation: pnBoxIn 0.3s ease;
            `;

            // 标题 + 引导语
            const head = document.createElement('div');
            head.style.cssText = 'margin:0 0 14px;flex-shrink:0;';
            const h2 = document.createElement('h2');
            h2.textContent = survey.title || _surveyText('survey.title', '问卷调查');
            h2.style.cssText = 'margin:0;color:#334155;font-size:21px;line-height:1.3;font-weight:800;';
            head.appendChild(h2);
            if (survey.intro) {
                const intro = document.createElement('p');
                intro.textContent = survey.intro;
                intro.style.cssText = 'margin:8px 0 0;color:#64748b;font-size:13px;line-height:1.55;font-weight:600;';
                head.appendChild(intro);
            }

            // 题目滚动区
            const form = document.createElement('form');
            form.className = 'survey-modal-form';
            form.style.cssText = [
                'display:flex', 'flex-direction:column', 'gap:18px',
                'flex:1 1 auto', 'min-height:0', 'overflow-y:auto', 'overflow-x:hidden',
                'padding:4px 8px 4px 2px', 'margin:0',
                'scrollbar-width:thin',
                'scrollbar-color:rgba(148,163,184,0.55) transparent',
            ].join(';');

            // 每题状态记录：{ q, getValue, markError }
            const fields = [];
            // 联动 placeholder 用：单选题 id -> 其 input 列表 / 待联动的填空题
            const optionInputsById = {};
            const linkedPlaceholders = [];

            questions.forEach((q, idx) => {
                if (!q || typeof q !== 'object' || !q.id) return;
                const type = (q.type === 'multi' || q.type === 'text') ? q.type : 'single';
                const qid = String(q.id);

                const wrap = document.createElement('div');
                wrap.className = 'survey-q';
                wrap.style.cssText = 'display:flex;flex-direction:column;gap:9px;';

                const label = document.createElement('div');
                label.style.cssText = 'color:#475569;font-size:15px;line-height:1.4;font-weight:800;';
                label.textContent = (q.required ? '* ' : '') + (q.label || '');
                wrap.appendChild(label);

                const err = document.createElement('div');
                err.style.cssText = 'display:none;color:#e11d48;font-size:12px;font-weight:700;margin-top:-2px;';
                err.textContent = _surveyText('survey.requiredHint', '这道题需要先回答哦');

                let getValue;
                if (type === 'text') {
                    const ta = document.createElement('textarea');
                    ta.rows = 3;
                    ta.placeholder = q.placeholder || '';
                    const maxLen = (typeof q.max_length === 'number' && q.max_length > 0) ? q.max_length : 500;
                    ta.maxLength = maxLen;
                    ta.className = 'survey-input';
                    ta.style.cssText = `
                        width:100%;box-sizing:border-box;resize:vertical;
                        border:1px solid rgba(148,163,184,0.4);border-radius:12px;
                        padding:10px 12px;font-size:14px;line-height:1.5;color:#334155;
                        background:rgba(255,255,255,0.78);font-family:inherit;
                    `;
                    wrap.appendChild(ta);
                    getValue = () => ta.value.trim();
                    // 声明式联动：placeholder 跟着指定单选题的选择走，引导用户写具体方向
                    if (q.placeholder_from && q.placeholder_template) {
                        linkedPlaceholders.push({
                            ta,
                            fromId: String(q.placeholder_from),
                            template: String(q.placeholder_template),
                            fallback: q.placeholder || '',
                        });
                    }
                } else {
                    // single / multi —— 选项组
                    const optionsBox = document.createElement('div');
                    optionsBox.style.cssText = 'display:flex;flex-direction:column;gap:8px;';
                    const opts = Array.isArray(q.options) ? q.options : [];
                    const inputs = [];
                    opts.forEach((opt) => {
                        if (!opt || typeof opt !== 'object') return;
                        const optRow = document.createElement('label');
                        optRow.className = 'survey-opt';
                        optRow.style.cssText = `
                            display:flex;align-items:flex-start;gap:10px;cursor:pointer;
                            padding:10px 12px;border-radius:12px;
                            background:rgba(255,255,255,0.62);
                            box-shadow:inset 0 0 0 1px rgba(148,163,184,0.18);
                            font-size:14px;line-height:1.4;color:#475569;font-weight:600;
                            transition:box-shadow 0.15s, background 0.15s;
                        `;
                        const input = document.createElement('input');
                        input.type = (type === 'multi') ? 'checkbox' : 'radio';
                        input.name = 'survey_' + qid;
                        input.value = String(opt.value != null ? opt.value : '');
                        input.style.cssText = 'margin-top:2px;flex-shrink:0;accent-color:#65aef4;';
                        input._label = opt.label != null ? String(opt.label) : input.value;
                        const span = document.createElement('span');
                        span.textContent = opt.label || input.value;
                        optRow.appendChild(input);
                        optRow.appendChild(span);
                        const sync = () => {
                            optRow.style.background = input.checked ? 'rgba(141,204,255,0.18)' : 'rgba(255,255,255,0.62)';
                            optRow.style.boxShadow = input.checked
                                ? 'inset 0 0 0 1.5px rgba(101,174,244,0.7)'
                                : 'inset 0 0 0 1px rgba(148,163,184,0.18)';
                            if (err.style.display !== 'none') err.style.display = 'none';
                        };
                        input.addEventListener('change', () => {
                            if (type === 'single') inputs.forEach((i) => i._sync && i._sync());
                            sync();
                        });
                        input._sync = sync;
                        inputs.push(input);
                        optionsBox.appendChild(optRow);
                    });
                    wrap.appendChild(optionsBox);
                    optionInputsById[qid] = inputs;
                    getValue = () => {
                        const checked = inputs.filter((i) => i.checked).map((i) => i.value);
                        if (type === 'multi') return checked;
                        return checked.length ? checked[0] : '';
                    };
                }

                wrap.appendChild(err);
                form.appendChild(wrap);
                fields.push({
                    q, type, wrap,
                    getValue,
                    isEmpty: () => {
                        const v = getValue();
                        return Array.isArray(v) ? v.length === 0 : !v;
                    },
                    showError: () => { err.style.display = 'block'; },
                });
            });

            // 接线：填空题的 placeholder 跟着来源单选题的选择变化，未选时回退到通用提示
            linkedPlaceholders.forEach((link) => {
                const srcInputs = optionInputsById[link.fromId];
                if (!Array.isArray(srcInputs) || !srcInputs.length) return;
                const refresh = () => {
                    const picked = srcInputs.find((i) => i.checked);
                    link.ta.placeholder = picked
                        ? link.template.replaceAll('{label}', picked._label || picked.value)
                        : link.fallback;
                };
                srcInputs.forEach((i) => i.addEventListener('change', refresh));
                refresh();
            });

            // 底部按钮：跳过（次） + 提交（主）
            const footer = document.createElement('div');
            footer.style.cssText = 'display:flex;gap:12px;justify-content:flex-end;align-items:center;margin-top:18px;flex-shrink:0;';

            const skipBtn = document.createElement('button');
            skipBtn.type = 'button';
            skipBtn.textContent = _surveyText('survey.skip', '跳过');
            skipBtn.style.cssText = `
                background:transparent;color:#94a3b8;border:none;
                border-radius:999px;padding:11px 22px;font-size:14px;font-weight:700;
                cursor:pointer;pointer-events:auto;transition:color 0.15s;
            `;
            skipBtn.addEventListener('mouseenter', () => { skipBtn.style.color = '#64748b'; });
            skipBtn.addEventListener('mouseleave', () => { skipBtn.style.color = '#94a3b8'; });

            const submitBtn = document.createElement('button');
            submitBtn.type = 'submit';
            submitBtn.textContent = _surveyText('survey.submit', '提交');
            submitBtn.style.cssText = `
                min-width:130px;
                background:linear-gradient(180deg, #8dccff 0%, #65aef4 100%);
                color:#fff;border:none;border-radius:999px;
                padding:12px 36px;font-size:15px;font-weight:700;cursor:pointer;
                pointer-events:auto;transition:transform 0.15s ease, filter 0.15s ease;
                box-shadow:0 14px 30px rgba(101,174,244,0.34), inset 0 3px 6px rgba(255,255,255,0.36);
            `;
            submitBtn.addEventListener('mouseenter', () => { submitBtn.style.filter = 'brightness(1.03)'; submitBtn.style.transform = 'translateY(-2px)'; });
            submitBtn.addEventListener('mouseleave', () => { submitBtn.style.filter = ''; submitBtn.style.transform = ''; });

            footer.appendChild(skipBtn);
            footer.appendChild(submitBtn);

            box.appendChild(head);
            box.appendChild(form);
            box.appendChild(footer);
            overlay.appendChild(box);

            const prevActive = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            let done = false;

            const teardown = (result) => {
                if (done) return;
                done = true;
                overlay.style.animation = 'pnOverlayOut 0.2s ease forwards';
                setTimeout(() => {
                    overlay.remove();
                    if (needRestoreBodyPE) document.body.style.pointerEvents = bodyPE;
                    if (prevActive && document.contains(prevActive)) prevActive.focus();
                    resolve(result);
                }, 200);
            };

            const collectAnswers = () => {
                const answers = {};
                fields.forEach((f) => {
                    const v = f.getValue();
                    if (Array.isArray(v)) {
                        if (v.length) answers[String(f.q.id)] = v;
                    } else if (v) {
                        answers[String(f.q.id)] = v;
                    }
                });
                return answers;
            };

            const onSubmit = (e) => {
                if (e) e.preventDefault();
                // 必填校验：仅对 submit 生效
                let firstMissing = null;
                fields.forEach((f) => {
                    if (f.q.required && f.isEmpty()) {
                        f.showError();
                        if (!firstMissing) firstMissing = f;
                    }
                });
                if (firstMissing) {
                    // 滚到第一个未答的必填题，而不是一律回到顶部——否则底部的题报错时
                    // 视口停在上方，用户看不到红字提示。
                    try {
                        if (firstMissing.wrap && typeof firstMissing.wrap.scrollIntoView === 'function') {
                            firstMissing.wrap.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
                        } else {
                            form.scrollTop = 0;
                        }
                    } catch (_) { try { form.scrollTop = 0; } catch (_) { } }
                    return;
                }
                teardown({ action: 'submit', answers: collectAnswers() });
            };

            form.addEventListener('submit', onSubmit);
            submitBtn.addEventListener('click', onSubmit);
            skipBtn.addEventListener('click', () => teardown({ action: 'skip', answers: {} }));

            document.body.appendChild(overlay);
            try {
                const firstInput = form.querySelector('input, textarea');
                (firstInput || submitBtn).focus();
            } catch (_) { submitBtn.focus(); }
        });
    }

    I.mod.showSurveyModal = showSurveyModal;
    window.showSurveyModal = showSurveyModal;

    // --- showReadyToSpeakToast ---
    I.showReadyToSpeakToast = function showReadyToSpeakToast() {
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

    I.mod.showReadyToSpeakToast = I.showReadyToSpeakToast;

    function syncFloatingMicMuteButtonVisibility(manager, isActive) {
        const muteButtonData = manager._floatingButtons && manager._floatingButtons['mic-mute'];
        if (muteButtonData && typeof muteButtonData.updateVisibility === 'function') {
            muteButtonData.updateVisibility(isActive);
        } else if (muteButtonData && muteButtonData.button) {
            muteButtonData.button.style.display = isActive ? 'flex' : 'none';
        }
    }

    // --- syncFloatingMicButtonState ---
    I.syncFloatingMicButtonState = function syncFloatingMicButtonState(isActive) {
        const managers = [window.live2dManager, window.vrmManager, window.mmdManager];

        for (const manager of managers) {
            if (manager && manager._floatingButtons && manager._floatingButtons.mic) {
                if (typeof manager.setButtonActive === 'function') {
                    manager.setButtonActive('mic', isActive);
                } else {
                    const { button, imgOff, imgOn } = manager._floatingButtons.mic;
                    if (button) {
                        button.dataset.active = isActive ? 'true' : 'false';
                        if (imgOff && imgOn) {
                            imgOff.style.opacity = isActive ? '0' : '0.75';
                            imgOn.style.opacity = isActive ? '1' : '0';
                        }
                        if (typeof manager.updateSeparatePopupTriggerIcon === 'function') {
                            manager.updateSeparatePopupTriggerIcon('mic');
                        }
                    }
                }

                syncFloatingMicMuteButtonVisibility(manager, isActive);
            }
        }
    }

    I.mod.syncFloatingMicButtonState = I.syncFloatingMicButtonState;

    // --- syncFloatingScreenButtonState ---
    I.syncFloatingScreenButtonState = function syncFloatingScreenButtonState(isActive) {
        const managers = [window.live2dManager, window.vrmManager, window.mmdManager];

        for (const manager of managers) {
            if (manager && manager._floatingButtons && manager._floatingButtons.screen) {
                if (typeof manager.setButtonActive === 'function') {
                    manager.setButtonActive('screen', isActive);
                } else {
                    const { button, imgOff, imgOn } = manager._floatingButtons.screen;
                    if (button) {
                        button.dataset.active = isActive ? 'true' : 'false';
                        if (imgOff && imgOn) {
                            imgOff.style.opacity = isActive ? '0' : '0.75';
                            imgOn.style.opacity = isActive ? '1' : '0';
                        }
                        if (typeof manager.updateSeparatePopupTriggerIcon === 'function') {
                            manager.updateSeparatePopupTriggerIcon('screen');
                        }
                    }
                }
            }
        }
    }

    I.mod.syncFloatingScreenButtonState = I.syncFloatingScreenButtonState;

    // ================================================================
    //  3. Model display / hide  (app.js lines 5590-5830)
    // ================================================================

    // --- hideLive2d ---

    Object.assign(window.appUi, I.mod || {});
})();
