/**
 * app-ui.js — UI display helpers extracted from app.js
 *
 * Exposed as  window.appUi
 *
 * Dependencies:
 *   - window.appState  (S)  — shared mutable state
 *   - window.appConst  (C)  — frozen constants
 *   - window.appUtils       — utility helpers
 *   - window.t / window.safeT — i18n
 *   - window.lanlan_config  — character config
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    const C = window.appConst;
    const NEKO_MODEL_CAT_TRANSITION_ASSET = '/static/assets/neko-idle/cat_model_change.gif';
    const NEKO_MODEL_CAT_TRANSITION_DURATION_MS = 850;
    const NEKO_MODEL_CAT_TRANSITION_LOOP_GUARD_MS = 70;
    const NEKO_MODEL_CAT_REVEAL_BEFORE_SMOKE_HIDE_MS = 48;
    const NEKO_MODEL_CAT_TRANSITION_LOAD_FALLBACK_MS = 1200;
    const NEKO_MODEL_CAT_TO_MODEL_LOCK_MS = 1120;
    const NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE = 0.38;
    const NEKO_MODEL_CAT_TRANSITION_MIN_SIZE = 260;
    const NEKO_MODEL_CAT_TRANSITION_MAX_SIZE = 680;
    const NEKO_MODEL_CAT_TRANSITION_SIZE_FACTOR = 0.86;
    const NEKO_MODEL_CAT_TRANSITION_EDGE_MASK = 'radial-gradient(circle at center, #000 0%, #000 44%, rgba(0,0,0,0.72) 58%, rgba(0,0,0,0.18) 72%, rgba(0,0,0,0) 88%, rgba(0,0,0,0) 100%)';
    const NEKO_MODEL_RETURN_ENTER_TRANSITION = 'opacity 1120ms ease-out, transform 1080ms cubic-bezier(0.22, 1, 0.36, 1)';
    const NEKO_MODEL_RETURN_ENTER_CLEANUP_MS = 1160;
    const NEKO_MODEL_RETURN_ENTER_SETTLE_BUFFER_MS = 180;
    const NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION = 'opacity 1.12s ease-out';
    const NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS = 1160;
    const NEKO_MODEL_GOODBYE_VISUAL_FADE_TRANSITION = 'opacity 240ms ease-in';
    const NEKO_GOODBYE_IDLE_APPEARANCE_CAT = 'cat';
    const NEKO_GOODBYE_IDLE_APPEARANCE_BALL = 'ball';
    const NEKO_GOODBYE_IDLE_APPEARANCE_ATTR = 'data-neko-goodbye-idle-appearance';
    const NEKO_GOODBYE_IDLE_BALL_ASSET = '/static/icons/expand_icon_off_ball.png';
    const NEKO_MODEL_CAT_TRANSITION_VERSION = (() => {
        try {
            const currentScript = document.currentScript;
            if (currentScript && currentScript.src) {
                return new URL(currentScript.src, window.location.href).searchParams.get('v') || '';
            }
        } catch (_) {}
        return '';
    })();
    let nekoModelCatTransitionToken = 0;
    let nekoModelCatTransitionActive = null;
    let nekoModelCatRevealPlaybackToken = 0;
    let nekoGoodbyeIdleAppearance = normalizeNekoGoodbyeIdleAppearance(window.__nekoGoodbyeIdleAppearance);
    window.__nekoGoodbyeIdleAppearance = nekoGoodbyeIdleAppearance;
    const GOODBYE_RESOURCE_SUSPEND_STORAGE_KEY = 'neko-goodbye-resource-suspended';
    let goodbyeResourceSuspendToken = 0;

    function getGoodbyeResourceSnapshot() {
        return S && S.goodbyeResourceSuspendSnapshot ? S.goodbyeResourceSuspendSnapshot : null;
    }

    function publishGoodbyeResourceState(snapshot, source) {
        const suspended = !!(snapshot && snapshot.suspended);
        const pending = !!(snapshot && snapshot.pending);
        if (S) {
            S.goodbyeResourceSuspended = suspended;
            S.goodbyeResourceSuspendPending = pending;
            S.goodbyeResourceSuspendSnapshot = snapshot || null;
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
        return !!(S && S.goodbyeResourceSuspended);
    };

    window.isNekoGoodbyeResourceSuspendingOrSuspended = function () {
        return !!(S && (S.goodbyeResourceSuspended || S.goodbyeResourceSuspendPending));
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
        if (type === 'live2d') {
            const ticker = manager.pixi_app && manager.pixi_app.ticker;
            return !!(ticker && ticker.started !== false);
        }
        if (type === 'vrm' || type === 'mmd') {
            return !!manager._animationFrameId;
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

    function beginGoodbyeResourceSuspend(options = {}) {
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

    function completeGoodbyeResourceSuspend(token) {
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

    function restoreGoodbyeResourceSuspend(reason) {
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

    mod.restoreGoodbyeResourceSuspend = restoreGoodbyeResourceSuspend;
    mod.completeGoodbyeResourceSuspend = completeGoodbyeResourceSuspend;
    window.addEventListener('neko:goodbye-state-cleared', (event) => {
        const detail = event && event.detail ? event.detail : {};
        const reason = detail.reason || 'goodbye-state-cleared';
        restoreGoodbyeResourceSuspend(reason);
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
    function showStatusToast(message, duration = 3000, options = {}) {
        const priority = options.important ? 100 : (options.priority || 0);
        
        if (!message || message.trim() === '') {
            const statusToast = S.dom.statusToast;
            if (statusToast) {
                if (S._statusToastCleanupTimer) {
                    clearTimeout(S._statusToastCleanupTimer);
                    S._statusToastCleanupTimer = null;
                }
                statusToast.classList.remove('show');
                statusToast.classList.add('hide');
                S._statusToastCleanupTimer = setTimeout(() => {
                    statusToast.textContent = '';
                    S._statusToastCleanupTimer = null;
                }, 300);
            }
            S._statusToastPriority = 0;
            return;
        }

        if (priority < S._statusToastPriority) {
            console.log('[StatusToast] Ignored lower priority message:', priority, '<', S._statusToastPriority);
            return;
        }

        console.log(window.t('console.statusToastShow'), message, window.t('console.statusToastDuration'), duration);

        const statusToast = S.dom.statusToast || document.getElementById('status-toast');
        const statusElement = S.dom.statusElement || document.getElementById('status');

        if (!statusToast) {
            console.error(window.t('console.statusToastNotFound'));
            return;
        }
        S.dom.statusToast = statusToast;
        if (statusElement) {
            S.dom.statusElement = statusElement;
        }

        // 清除之前的定时器
        if (S.statusToastTimeout) {
            clearTimeout(S.statusToastTimeout);
            S.statusToastTimeout = null;
        }
        if (S._statusToastCleanupTimer) {
            clearTimeout(S._statusToastCleanupTimer);
            S._statusToastCleanupTimer = null;
        }

        // 更新内容
        statusToast.textContent = message;
        S._statusToastPriority = priority;

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
        S.statusToastTimeout = setTimeout(() => {
            statusToast.classList.remove('show');
            statusToast.classList.add('hide');
            S._statusToastCleanupTimer = setTimeout(() => {
                statusToast.textContent = '';
                S._statusToastPriority = 0;
                S._statusToastCleanupTimer = null;
            }, 300);
        }, duration);

        // 同时更新隐藏的 status 元素（保持兼容性）
        if (statusElement) {
            statusElement.textContent = message || '';
        }
    }

    mod.showStatusToast = showStatusToast;
    // 全局兼容
    window.showStatusToast = showStatusToast;

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

    mod.showVoicePreparingToast = showVoicePreparingToast;

    // --- hideVoicePreparingToast ---
    function hideVoicePreparingToast() {
        const toast = document.getElementById('voice-preparing-toast');
        if (toast) {
            toast.style.animation = 'voiceToastFadeIn 0.3s ease reverse';
            setTimeout(() => {
                toast.style.display = 'none';
            }, 300);
        }
    }

    mod.hideVoicePreparingToast = hideVoicePreparingToast;

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

    mod.showProminentNotice = showProminentNotice;
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

    mod.showSurveyModal = showSurveyModal;
    window.showSurveyModal = showSurveyModal;

    // --- showReadyToSpeakToast ---
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

    mod.showReadyToSpeakToast = showReadyToSpeakToast;

    function syncFloatingMicMuteButtonVisibility(manager, isActive) {
        const muteButtonData = manager._floatingButtons && manager._floatingButtons['mic-mute'];
        if (muteButtonData && typeof muteButtonData.updateVisibility === 'function') {
            muteButtonData.updateVisibility(isActive);
        } else if (muteButtonData && muteButtonData.button) {
            muteButtonData.button.style.display = isActive ? 'flex' : 'none';
        }
    }

    // --- syncFloatingMicButtonState ---
    function syncFloatingMicButtonState(isActive) {
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

    mod.syncFloatingMicButtonState = syncFloatingMicButtonState;

    // --- syncFloatingScreenButtonState ---
    function syncFloatingScreenButtonState(isActive) {
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

    mod.syncFloatingScreenButtonState = syncFloatingScreenButtonState;

    // ================================================================
    //  3. Model display / hide  (app.js lines 5590-5830)
    // ================================================================

    // --- hideLive2d ---
    function hideLive2d() {
        console.log('[App] hideLive2d函数被调用');
        const container = document.getElementById('live2d-container');
        console.log('[App] hideLive2d调用前，容器类列表:', container.classList.toString());

        // 首先清除任何可能干扰动画的强制显示样式
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
        const isGoodbyeExiting = container.getAttribute('data-neko-model-goodbye-exiting') === 'true';
        // 清除 canvas 上的渐入动画残留样式。model-to-cat 退出过程中不要清除，
        // 否则 resetSessionButton 触发的 hideLive2d 会打断提前透明。
        const live2dCanvasForHide = document.getElementById('live2d-canvas');
        if (live2dCanvasForHide && !isGoodbyeExiting) {
            live2dCanvasForHide.style.transition = '';
            live2dCanvasForHide.style.opacity = '';
        }

        // 添加minimized类，触发CSS过渡动画
        playModelGoodbyeExit(container, getActiveModelTransitionRect());
        console.log('[App] hideLive2d调用后，容器类列表:', container.classList.toString());

        // 添加一个延迟检查，确保类被正确添加
        setTimeout(() => {
            console.log('[App] 延迟检查容器类列表:', container.classList.toString());
        }, 100);
    }

    mod.hideLive2d = hideLive2d;

    function shouldPreserveYuiGuideLive2DPreparing() {
        return window.nekoYuiGuideLive2dPreparing === true
            || (
                window.isInTutorial === true
                && typeof document !== 'undefined'
                && document.body
                && document.body.classList
                && document.body.classList.contains('yui-guide-live2d-preparing')
            );
    }

    function hideYuiGuideLive2DPreparingControls() {
        [
            'live2d-floating-buttons',
            'live2d-lock-icon',
            'live2d-return-button-container'
        ].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || !element.style || typeof element.style.removeProperty !== 'function') {
                return;
            }
            element.style.setProperty('display', 'none', 'important');
            element.style.setProperty('visibility', 'hidden', 'important');
            element.style.setProperty('opacity', '0', 'important');
            element.style.setProperty('pointer-events', 'none', 'important');
        });
    }

    function restoreYuiGuideLive2DPreparingControls() {
        [
            'live2d-floating-buttons',
            'live2d-lock-icon'
        ].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || !element.style || typeof element.style.removeProperty !== 'function') {
                return;
            }
            element.style.removeProperty('display');
            element.style.removeProperty('visibility');
            element.style.removeProperty('opacity');
            element.style.removeProperty('pointer-events');
        });
    }

    function keepAvatarRootContainerPassthrough(container) {
        if (!container || !container.id || !container.style) return false;
        if (container.id !== 'live2d-container' && container.id !== 'pngtuber-container') return false;
        container.style.setProperty('pointer-events', 'none', 'important');
        return true;
    }

    function restoreLive2DDisplaySurface(reason) {
        const preserveAvatarCornerPeekOpacity = window.nekoYuiGuideAvatarCornerPeekActive === true;
        const preserveYuiGuidePreparing = shouldPreserveYuiGuideLive2DPreparing();
        if (!preserveYuiGuidePreparing) {
            restoreYuiGuideLive2DPreparingControls();
        }
        if (document.body && document.body.classList) {
            if (!preserveYuiGuidePreparing) {
                document.body.classList.remove('yui-guide-live2d-preparing');
            }
            document.body.classList.remove('yui-guide-return-petal-fade');
        }
        if (document.body && document.body.style && typeof document.body.style.removeProperty === 'function') {
            document.body.style.removeProperty('--yui-guide-return-avatar-opacity');
        }

        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.classList.remove('hidden');
            live2dContainer.classList.remove('minimized');
            live2dContainer.removeAttribute('data-neko-model-goodbye-exiting');
            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            live2dContainer.style.removeProperty('transition');
            if (preserveYuiGuidePreparing) {
                // 新手教程开场演出会在首句动作起点统一 reveal。
            } else if (!preserveAvatarCornerPeekOpacity) {
                live2dContainer.style.removeProperty('opacity');
            }
            keepAvatarRootContainerPassthrough(live2dContainer);
        }

        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.classList.remove('minimized');
            live2dCanvas.style.display = 'block';
            live2dCanvas.style.removeProperty('transition');
            if (preserveYuiGuidePreparing) {
                live2dCanvas.style.removeProperty('pointer-events');
            } else if (!preserveAvatarCornerPeekOpacity) {
                live2dCanvas.style.setProperty('opacity', '1', 'important');
                live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
            }
            live2dCanvas.style.setProperty('visibility', 'visible', 'important');
        }
    }

    function activateLive2DRenderForDisplay(reason) {
        const preserveAvatarCornerPeekOpacity = window.nekoYuiGuideAvatarCornerPeekActive === true;
        const manager = window.live2dManager || null;
        const app = manager && manager.pixi_app;
        const ticker = app && app.ticker;
        const model = manager && (typeof manager.getCurrentModel === 'function'
            ? manager.getCurrentModel()
            : manager.currentModel);

        try {
            if (model) {
                model.visible = true;
                if (!preserveAvatarCornerPeekOpacity) {
                    model.alpha = 1;
                }
                if (model.renderable !== undefined) {
                    model.renderable = true;
                }
            }
            if (app && app.stage) {
                app.stage.visible = true;
                if (!preserveAvatarCornerPeekOpacity) {
                    app.stage.alpha = 1;
                }
                if (app.stage.renderable !== undefined) {
                    app.stage.renderable = true;
                }
            }
            if (ticker) {
                if (!ticker.started && typeof ticker.start === 'function') {
                    ticker.start();
                }
                if (typeof ticker.update === 'function') {
                    ticker.update();
                }
            }
            if (app && app.renderer && app.stage && typeof app.renderer.render === 'function') {
                app.renderer.render(app.stage);
            }
        } catch (error) {
            console.warn('[App] Live2D render activation failed:', reason || 'show-live2d', error);
        }
    }

    function scheduleLive2DDisplayActivation(reason) {
        activateLive2DRenderForDisplay(reason || 'show-live2d');
        [80, 300].forEach((delayMs) => {
            window.setTimeout(() => {
                activateLive2DRenderForDisplay((reason || 'show-live2d') + ':delay-' + delayMs);
            }, delayMs);
        });
    }

    // --- showLive2d ---
    function showLive2d() {
        console.log('[App] showLive2d函数被调用');

        if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber') {
            console.log('[App] showLive2d: 当前为 PNGTuber 模式，跳过 Live2D 显示');
            const live2dContainerForPngtuber = document.getElementById('live2d-container');
            if (live2dContainerForPngtuber) {
                live2dContainerForPngtuber.style.display = 'none';
                live2dContainerForPngtuber.classList.add('hidden');
            }
            const live2dCanvasForPngtuber = document.getElementById('live2d-canvas');
            if (live2dCanvasForPngtuber) {
                live2dCanvasForPngtuber.style.visibility = 'hidden';
                live2dCanvasForPngtuber.style.pointerEvents = 'none';
            }
            document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container')
                .forEach(el => el.remove());
            return;
        }

        // 检查是否处于"请她离开"状态
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[App] showLive2d: 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }

        if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
            window.pngtuberManager.hide();
        }
        if (window.cleanupPNGTuberOverlayUI && typeof window.cleanupPNGTuberOverlayUI === 'function') {
            window.cleanupPNGTuberOverlayUI();
        } else {
            document.querySelectorAll('#pngtuber-floating-buttons, #pngtuber-lock-icon, #pngtuber-return-button-container')
                .forEach(el => el.remove());
        }
        const pngtuberContainerForLive2d = document.getElementById('pngtuber-container');
        if (pngtuberContainerForLive2d) {
            pngtuberContainerForLive2d.style.display = 'none';
            pngtuberContainerForLive2d.classList.add('hidden');
        }

        const container = document.getElementById('live2d-container');
        console.log('[App] showLive2d调用前，容器类列表:', container.classList.toString());
        const preserveYuiGuidePreparing = shouldPreserveYuiGuideLive2DPreparing();
        if (preserveYuiGuidePreparing) {
            hideYuiGuideLive2DPreparingControls();
        }

        // 检测模型是否已经可见（避免不必要的淡入动画导致闪烁）
        const isAlreadyVisible = container &&
            !container.classList.contains('minimized') &&
            !container.classList.contains('hidden') &&
            container.style.display !== 'none' &&
            getComputedStyle(container).display !== 'none';

        // 检查Live2D浮动按钮是否存在，如果不存在则重新创建
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

        // 确保浮动按钮显示
        if (!preserveYuiGuidePreparing && floatingButtons) {
            floatingButtons.style.setProperty('display', 'flex', 'important');
            floatingButtons.style.setProperty('visibility', 'visible', 'important');
            floatingButtons.style.setProperty('opacity', '1', 'important');
            floatingButtons.style.setProperty('pointer-events', 'auto', 'important');
        }

        const lockIcon = document.getElementById('live2d-lock-icon');
        if (!preserveYuiGuidePreparing && lockIcon) {
            lockIcon.style.removeProperty('display');
            lockIcon.style.removeProperty('visibility');
            lockIcon.style.removeProperty('opacity');
            lockIcon.style.removeProperty('pointer-events');
        } else if (preserveYuiGuidePreparing) {
            hideYuiGuideLive2DPreparingControls();
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

        // 取消"请她离开"的延迟隐藏定时器
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

        // 如果模型已经可见，跳过淡入动画
        if (isAlreadyVisible) {
            console.log('[App] showLive2d: 模型已可见，跳过淡入动画');
            const fadeModel = window.live2dManager ? window.live2dManager.getCurrentModel() : null;
            if (fadeModel && !fadeModel.destroyed) {
                fadeModel.alpha = 1;
            }
            restoreLive2DDisplaySurface('show-live2d-fast-path');
            const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
            if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
                pixiApp.ticker.start();
            }
            scheduleLive2DDisplayActivation('show-live2d-fast-path');
            console.log('[App] showLive2d调用后（快速路径），容器类列表:', container.classList.toString());
            return;
        }

        // 渐入动画 - 复刻 _configureLoadedModel 的 CSS 揭示机制
        const fadeModel = window.live2dManager ? window.live2dManager.getCurrentModel() : null;
        if (fadeModel && !fadeModel.destroyed) {
            fadeModel.alpha = 1;
        }

        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.transition = 'none';
            live2dCanvas.style.opacity = '0.001';
        }
        const modelReturnEnterRect = consumeModelReturnEnterRect();

        prepareModelReturnContainer(container, modelReturnEnterRect);

        if (live2dCanvas) {
            live2dCanvas.style.setProperty('visibility', 'visible', 'important');
            live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
        }

        // 强制浏览器刷新布局
        if (live2dCanvas) {
            void live2dCanvas.offsetWidth;
        }

        container.style.transition = '';
        if (modelReturnEnterRect) {
            playModelReturnEnter(container, modelReturnEnterRect);
        }

        // 确保 PIXI ticker 在运行
        const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
        if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
            pixiApp.ticker.start();
        }

        // 触发 CSS transition 淡入
        if (live2dCanvas) {
            scheduleLive2DDisplayActivation('show-live2d');
            live2dCanvas.style.transition = NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION;
            live2dCanvas.style.opacity = '1';

            window._returnFadeTimer = setTimeout(() => {
                if (live2dCanvas) {
                    live2dCanvas.style.transition = '';
                    live2dCanvas.style.opacity = '';
                }
                // 清除容器的内联 opacity，使 CSS class（如 locked-hover-fade）能正常生效
                container.style.removeProperty('opacity');
                window._returnFadeTimer = null;
            }, NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS);
        }

        if (container.classList.length === 0) {
            container.removeAttribute('class');
        }

        console.log('[App] showLive2d调用后，容器类列表:', container.classList.toString());
    }

    mod.showLive2d = showLive2d;

    // --- viewport helpers ---
    function isMobileViewport() {
        return typeof window.isMobileWidth === 'function'
            ? window.isMobileWidth()
            : (window.innerWidth <= 768);
    }

    const NEKO_MODEL_VIEWPORT_RESTORE_FALLBACK_MS = 900;
    const NEKO_MODEL_VIEWPORT_RESTORE_RETRY_MS = 300;
    const NEKO_NATIVE_RETURN_BALL_SHRINK_VIEWPORT_SIZE = 160;
    let pendingNativeModelViewportRestoreBounds = null;

    function normalizeModelViewportBounds(bounds) {
        const candidate = bounds && typeof bounds === 'object'
            ? (bounds.requestedBounds || bounds.bounds || bounds)
            : null;
        if (!candidate) return null;
        const x = Number.isFinite(Number(candidate.x))
            ? Math.round(Number(candidate.x))
            : (Number.isFinite(Number(window.screenX)) ? Math.round(Number(window.screenX)) : 0);
        const y = Number.isFinite(Number(candidate.y))
            ? Math.round(Number(candidate.y))
            : (Number.isFinite(Number(window.screenY)) ? Math.round(Number(window.screenY)) : 0);
        const width = Math.round(Number(candidate.width));
        const height = Math.round(Number(candidate.height));
        if (![x, y, width, height].every(Number.isFinite) || width <= 1 || height <= 1) {
            return null;
        }
        if (isNativeReturnBallViewportSize(width, height)) {
            return null;
        }
        return { x, y, width, height };
    }

    function setPendingNativeModelViewportRestoreBounds(bounds) {
        pendingNativeModelViewportRestoreBounds = normalizeModelViewportBounds(bounds);
        return pendingNativeModelViewportRestoreBounds;
    }

    function isNativeReturnBallViewportSize(width, height) {
        const w = Math.round(Number(width));
        const h = Math.round(Number(height));
        if (!Number.isFinite(w) || !Number.isFinite(h)) return false;
        return Math.abs(w - NEKO_NATIVE_RETURN_BALL_SHRINK_VIEWPORT_SIZE) <= 2
            && Math.abs(h - NEKO_NATIVE_RETURN_BALL_SHRINK_VIEWPORT_SIZE) <= 2;
    }

    function isModelViewportRestored(bounds) {
        const target = normalizeModelViewportBounds(bounds);
        if (!target) return true;
        const tolerance = 2;
        return Math.abs((window.innerWidth || 0) - target.width) <= tolerance &&
            Math.abs((window.innerHeight || 0) - target.height) <= tolerance;
    }

    function waitForModelViewportRestore(bounds, options = {}) {
        const target = normalizeModelViewportBounds(bounds);
        if (!target || isModelViewportRestored(target)) {
            return waitForAnimationFrames(2).then(() => ({ restored: true, skipped: !target }));
        }

        const timeoutMs = Number.isFinite(options.timeoutMs)
            ? Math.max(0, Number(options.timeoutMs))
            : NEKO_MODEL_VIEWPORT_RESTORE_FALLBACK_MS;
        const deadline = Date.now() + timeoutMs;

        return new Promise((resolve) => {
            let timerId = null;
            let finished = false;
            const finish = (restored, timedOut) => {
                if (finished) return;
                finished = true;
                if (timerId) {
                    clearTimeout(timerId);
                    timerId = null;
                }
                window.removeEventListener('resize', check);
                waitForAnimationFrames(2).then(() => resolve({
                    restored: !!restored,
                    timedOut: !!timedOut
                }));
            };
            const check = () => {
                if (isModelViewportRestored(target)) {
                    finish(true, false);
                    return;
                }
                if (Date.now() >= deadline) {
                    finish(false, true);
                    return;
                }
                timerId = setTimeout(check, 16);
            };
            window.addEventListener('resize', check);
            timerId = setTimeout(check, 16);
        });
    }

    function recoverLive2DRendererFromReturnBallViewport(reason) {
        try {
            if (!window.live2dManager ||
                typeof window.live2dManager.recoverRendererFromReturnBallViewport !== 'function') {
                return false;
            }
            const recovered = window.live2dManager.recoverRendererFromReturnBallViewport(reason);
            return !!recovered;
        } catch (error) {
            console.warn('[showCurrentModel] recover Live2D renderer from return-ball viewport failed:', error);
            return false;
        }
    }

    function getPendingModelViewportRestoreBounds() {
        const pending = normalizeModelViewportBounds(pendingNativeModelViewportRestoreBounds);
        if (pending) return pending;
        if (multiWindowReturnBallDragState) {
            const width = Math.round(Number(multiWindowReturnBallDragState.savedWindowW));
            const height = Math.round(Number(multiWindowReturnBallDragState.savedWindowH));
            if (Number.isFinite(width) && Number.isFinite(height) && width > 1 && height > 1) {
                return {
                    x: Number.isFinite(Number(window.screenX)) ? Math.round(Number(window.screenX)) : 0,
                    y: Number.isFinite(Number(window.screenY)) ? Math.round(Number(window.screenY)) : 0,
                    width,
                    height
                };
            }
        }
        return null;
    }

    async function ensureModelViewportReadyBeforeShowCurrentModel() {
        const restoreBounds = getPendingModelViewportRestoreBounds();
        if (!restoreBounds) {
            if (isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight)) {
                return {
                    ready: false,
                    restored: false,
                    missingRestoreBounds: true,
                    returnBallViewport: true
                };
            }
            recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:no-restore-bounds');
            return { ready: true, skipped: true };
        }
        if (isModelViewportRestored(restoreBounds)) {
            pendingNativeModelViewportRestoreBounds = null;
            recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:already-restored');
            return { ready: true, restored: true };
        }

        if (window.nekoPetDrag && typeof window.nekoPetDrag.reveal === 'function') {
            try {
                const revealResult = await Promise.resolve(window.nekoPetDrag.reveal());
                if (revealResult === false) {
                    await waitForModelViewportRestore(restoreBounds, {
                        timeoutMs: NEKO_MODEL_VIEWPORT_RESTORE_RETRY_MS
                    });
                }
            } catch (error) {
                console.warn('[showCurrentModel] restore model viewport reveal retry failed:', error);
            }
        }

        const viewportWait = await waitForModelViewportRestore(restoreBounds);
        if (viewportWait.restored) {
            pendingNativeModelViewportRestoreBounds = null;
            recoverLive2DRendererFromReturnBallViewport('ensure-model-viewport-ready:after-wait');
            return { ready: true, restored: true, viewportWait };
        }

        console.warn('[showCurrentModel] blocked model display because Pet viewport is still return-ball sized:', {
            target: restoreBounds,
            current: {
                width: window.innerWidth,
                height: window.innerHeight
            },
            returnBallViewport: isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight)
        });
        return { ready: false, restored: false, viewportWait, restoreBounds };
    }

    // --- showCurrentModel ---
    async function showCurrentModel() {
        // 检查"请她离开"状态
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }
        if (window.vrmManager && window.vrmManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态（VRM），跳过显示逻辑');
            return;
        }
        if (window.mmdManager && window.mmdManager._goodbyeClicked) {
            console.log('[showCurrentModel] 当前处于"请她离开"状态（MMD），跳过显示逻辑');
            return;
        }

        const modelViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();
        if (!modelViewportReady.ready) {
            return false;
        }

        // 重置 goodbye 标志
        if (window.live2dManager) {
            window.live2dManager._goodbyeClicked = false;
        }
        if (window.vrmManager) {
            window.vrmManager._goodbyeClicked = false;
        }
        if (window.mmdManager) {
            window.mmdManager._goodbyeClicked = false;
        }

        try {
            // 运行时检测当前已加载且可见的模型，用于 API 失败时的回退
            // 需同时检查模型引用和容器可见性（goodbye 流程中模型引用存在但容器已隐藏）
            const _vrmEl = document.getElementById('vrm-container');
            const _mmdEl = document.getElementById('mmd-container');
            const isVrmCurrentlyActive = window.vrmManager && window.vrmManager.currentModel
                && _vrmEl && _vrmEl.style.display !== 'none' && !_vrmEl.classList.contains('hidden');
            const isMmdCurrentlyActive = window.mmdManager && window.mmdManager.currentModel
                && _mmdEl && _mmdEl.style.display !== 'none' && !_mmdEl.classList.contains('hidden');

            const charResponse = await fetch('/api/characters');
            if (!charResponse.ok) {
                console.warn('[showCurrentModel] 无法获取角色配置');
                // 如果当前已有 VRM/MMD 模型在运行，保持当前状态而非回退到 Live2D
                if (isVrmCurrentlyActive || isMmdCurrentlyActive) {
                    console.log('[showCurrentModel] 保持当前已加载的模型');
                    return;
                }
                showLive2d();
                return;
            }

            const charactersData = await charResponse.json();
            const currentCatgirl = lanlan_config.lanlan_name;
            const catgirlConfig = charactersData['猫娘']?.[currentCatgirl];

            if (!catgirlConfig) {
                console.warn('[showCurrentModel] 未找到角色配置');
                if (isVrmCurrentlyActive || isMmdCurrentlyActive) {
                    console.log('[showCurrentModel] 保持当前已加载的模型');
                    return;
                }
                showLive2d();
                return;
            }

            const modelType = catgirlConfig.model_type || (catgirlConfig.vrm ? 'vrm' : 'live2d');

            // 解析 live3d 子类型
            // 优先使用 live3d_sub_type（后端权威来源），与 vrm-init.js / live2d-init.js 保持一致
            // 旧逻辑仅通过 mmd/vrm 路径字段猜测，当两个字段同时存在时会误判
            let effectiveModelType = modelType;
            if (modelType === 'live3d') {
                const subType = (
                    window.lanlan_config?.live3d_sub_type
                    || catgirlConfig._reserved?.avatar?.live3d_sub_type
                    || catgirlConfig.live3d_sub_type
                    || ''
                ).toString().trim().toLowerCase();

                if (subType === 'vrm') {
                    effectiveModelType = 'vrm';
                } else if (subType === 'mmd') {
                    effectiveModelType = 'mmd';
                } else {
                    // sub_type 缺失时回退到路径探测
                    const _sanitize = (v) => {
                        if (v === undefined || v === null) return '';
                        const s = String(v).trim();
                        const lower = s.toLowerCase();
                        if (!s || lower === 'undefined' || lower === 'null') return '';
                        return s;
                    };
                    const mmdPath = _sanitize(catgirlConfig.mmd)
                        || _sanitize(catgirlConfig._reserved?.avatar?.mmd?.model_path)
                        || '';
                    const vrmPath = _sanitize(catgirlConfig.vrm)
                        || _sanitize(catgirlConfig._reserved?.avatar?.vrm?.model_path)
                        || '';
                    if (mmdPath && !vrmPath) {
                        effectiveModelType = 'mmd';
                    } else if (vrmPath) {
                        effectiveModelType = 'vrm';
                    }
                }
            }
            console.log('[showCurrentModel] 当前角色模型类型:', modelType, '有效类型:', effectiveModelType);

            if (effectiveModelType === 'vrm') {
                console.log('[showCurrentModel] 开始显示VRM模型');

                const vrmContainer = document.getElementById('vrm-container');
                console.log('[showCurrentModel] vrmContainer存在:', !!vrmContainer);
                if (vrmContainer) {
                    // 取消延迟隐藏定时器
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

                    const isVrmAlreadyVisible =
                        !vrmContainer.classList.contains('minimized') &&
                        !vrmContainer.classList.contains('hidden') &&
                        vrmContainer.style.display !== 'none' &&
                        getComputedStyle(vrmContainer).display !== 'none';
                    const modelReturnEnterRect = !isVrmAlreadyVisible ? consumeModelReturnEnterRect() : null;

                    const vrmCanvasInner = document.getElementById('vrm-canvas');
                    if (!isVrmAlreadyVisible) {
                        if (vrmCanvasInner) {
                            vrmCanvasInner.style.transition = 'none';
                            vrmCanvasInner.style.opacity = '0';
                        }
                    }

                    prepareModelReturnContainer(vrmContainer, modelReturnEnterRect, { clearPointerEvents: true });

                    void vrmContainer.offsetWidth;
                    vrmContainer.style.transition = '';
                    if (modelReturnEnterRect) {
                        playModelReturnEnter(vrmContainer, modelReturnEnterRect);
                    }

                    if (vrmCanvasInner) {
                        vrmCanvasInner.style.setProperty('visibility', 'visible', 'important');
                        vrmCanvasInner.style.setProperty('pointer-events', 'auto', 'important');

                        if (!isVrmAlreadyVisible) {
                            void vrmCanvasInner.offsetWidth;

                            vrmCanvasInner.style.transition = NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION;
                            vrmCanvasInner.style.opacity = '1';

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
                            window._vrmCanvasFadeInId = setTimeout(cleanupFadeIn, NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS);
                        }
                    }
                    console.log('[showCurrentModel] 已设置vrmContainer可见', isVrmAlreadyVisible ? '（跳过淡入动画）' : '（带canvas渐入动画）');
                }

                // 恢复 VRM canvas 的可见性
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

                // 检查VRM浮动按钮是否存在
                let vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                console.log('[showCurrentModel] VRM浮动按钮存在:', !!vrmFloatingButtons, 'vrmManager存在:', !!window.vrmManager);

                if (!vrmFloatingButtons && window.vrmManager && typeof window.vrmManager.setupFloatingButtons === 'function') {
                    console.log('[showCurrentModel] VRM浮动按钮不存在，重新创建');
                    window.vrmManager.setupFloatingButtons();
                    vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
                    console.log('[showCurrentModel] 创建后VRM浮动按钮存在:', !!vrmFloatingButtons);
                }

                if (vrmFloatingButtons) {
                    if (isMobileViewport()) {
                        vrmFloatingButtons.style.removeProperty('display');
                        vrmFloatingButtons.style.removeProperty('visibility');
                        vrmFloatingButtons.style.removeProperty('opacity');
                    } else {
                        vrmFloatingButtons.style.display = 'none';
                        vrmFloatingButtons.style.visibility = 'hidden';
                        vrmFloatingButtons.style.opacity = '0';
                    }
                }

                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    if (isMobileViewport()) {
                        vrmLockIcon.style.removeProperty('display');
                        vrmLockIcon.style.removeProperty('visibility');
                        vrmLockIcon.style.removeProperty('opacity');
                    } else {
                        vrmLockIcon.style.display = 'none';
                        vrmLockIcon.style.visibility = 'hidden';
                        vrmLockIcon.style.opacity = '0';
                    }
                }

                if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                    window.vrmManager.core.setLocked(false);
                }

                // 隐藏Live2D浮动按钮和锁图标
                const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
                if (live2dFloatingButtons && !window.isInTutorial) {
                    live2dFloatingButtons.style.display = 'none';
                }
                const live2dLockIcon = document.getElementById('live2d-lock-icon');
                if (live2dLockIcon) {
                    live2dLockIcon.style.display = 'none';
                }

                // 隐藏原生按钮和status栏
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

                // 隐藏 MMD 容器和按钮
                const mmdContainerVrm = document.getElementById('mmd-container');
                if (mmdContainerVrm) { mmdContainerVrm.style.display = 'none'; mmdContainerVrm.classList.add('hidden'); }
                const mmdCanvasVrm = document.getElementById('mmd-canvas');
                if (mmdCanvasVrm) { mmdCanvasVrm.style.visibility = 'hidden'; mmdCanvasVrm.style.pointerEvents = 'none'; }
                const mmdFloatingButtonsVrm = document.getElementById('mmd-floating-buttons');
                if (mmdFloatingButtonsVrm) { mmdFloatingButtonsVrm.style.display = 'none'; }
                const mmdLockIconVrm = document.getElementById('mmd-lock-icon');
                if (mmdLockIconVrm) { mmdLockIconVrm.style.display = 'none'; }

            } else if (effectiveModelType === 'mmd') {
                // ═══════════════ 显示 MMD 模型 ═══════════════
                console.log('[showCurrentModel] 开始显示MMD模型');

                // 显示 MMD 容器
                const mmdContainer = document.getElementById('mmd-container');
                const modelReturnEnterRect = mmdContainer ? consumeModelReturnEnterRect() : null;
                if (mmdContainer) {
                    prepareModelReturnContainer(mmdContainer, modelReturnEnterRect, { clearPointerEvents: true });
                    mmdContainer.style.transition = '';
                    if (modelReturnEnterRect) {
                        playModelReturnEnter(mmdContainer, modelReturnEnterRect);
                    }
                }
                const mmdCanvas = document.getElementById('mmd-canvas');
                if (mmdCanvas) {
                    mmdCanvas.style.setProperty('visibility', 'visible', 'important');
                    mmdCanvas.style.setProperty('pointer-events', 'auto', 'important');
                    // 渐入动画
                    mmdCanvas.style.transition = 'none';
                    mmdCanvas.style.opacity = '0';
                    void mmdCanvas.offsetWidth;
                    mmdCanvas.style.transition = NEKO_MODEL_RETURN_CANVAS_FADE_TRANSITION;
                    mmdCanvas.style.opacity = '1';
                    if (window._mmdCanvasFadeInId) clearTimeout(window._mmdCanvasFadeInId);
                    window._mmdCanvasFadeInId = setTimeout(() => {
                        if (mmdCanvas) {
                            mmdCanvas.style.transition = '';
                            mmdCanvas.style.opacity = '';
                        }
                        window._mmdCanvasFadeInId = null;
                    }, NEKO_MODEL_RETURN_CANVAS_FADE_CLEANUP_MS);
                }

                // 隐藏 VRM
                const vrmContainerMmd = document.getElementById('vrm-container');
                if (vrmContainerMmd) { vrmContainerMmd.style.display = 'none'; vrmContainerMmd.classList.add('hidden'); }
                const vrmCanvasMmd = document.getElementById('vrm-canvas');
                if (vrmCanvasMmd) { vrmCanvasMmd.style.visibility = 'hidden'; vrmCanvasMmd.style.pointerEvents = 'none'; }

                // 隐藏 Live2D
                const live2dContainerMmd = document.getElementById('live2d-container');
                if (live2dContainerMmd) { live2dContainerMmd.style.display = 'none'; live2dContainerMmd.classList.add('hidden'); }

                // 显示 MMD 浮动按钮
                let mmdFloatingButtons = document.getElementById('mmd-floating-buttons');
                if (!mmdFloatingButtons && window.mmdManager && typeof window.mmdManager.setupFloatingButtons === 'function') {
                    window.mmdManager.setupFloatingButtons();
                    mmdFloatingButtons = document.getElementById('mmd-floating-buttons');
                }
                if (mmdFloatingButtons) {
                    const isMmdMobile = typeof window.isMobileWidth === 'function'
                        ? window.isMobileWidth()
                        : (window.innerWidth <= 768);
                    if (isMmdMobile) {
                        mmdFloatingButtons.style.removeProperty('display');
                        mmdFloatingButtons.style.removeProperty('visibility');
                        mmdFloatingButtons.style.removeProperty('opacity');
                    } else {
                        mmdFloatingButtons.style.display = 'none';
                        mmdFloatingButtons.style.visibility = 'hidden';
                        mmdFloatingButtons.style.opacity = '0';
                    }
                }

                // 隐藏 VRM / Live2D 浮动按钮
                const vrmFloatingButtonsMmd = document.getElementById('vrm-floating-buttons');
                if (vrmFloatingButtonsMmd) { vrmFloatingButtonsMmd.style.display = 'none'; }
                const live2dFloatingButtonsMmd = document.getElementById('live2d-floating-buttons');
                if (live2dFloatingButtonsMmd) { live2dFloatingButtonsMmd.style.display = 'none'; }
                const vrmLockIconMmd = document.getElementById('vrm-lock-icon');
                if (vrmLockIconMmd) { vrmLockIconMmd.style.display = 'none'; }
                const live2dLockIconMmd = document.getElementById('live2d-lock-icon');
                if (live2dLockIconMmd) { live2dLockIconMmd.style.display = 'none'; }

                // 隐藏原生按钮和status栏
                const sidebarMmd = document.getElementById('sidebar');
                const sidebarboxMmd = document.getElementById('sidebarbox');
                if (sidebarMmd) {
                    sidebarMmd.style.setProperty('display', 'none', 'important');
                    sidebarMmd.style.setProperty('visibility', 'hidden', 'important');
                    sidebarMmd.style.setProperty('opacity', '0', 'important');
                }
                if (sidebarboxMmd) {
                    sidebarboxMmd.style.setProperty('display', 'none', 'important');
                    sidebarboxMmd.style.setProperty('visibility', 'hidden', 'important');
                    sidebarboxMmd.style.setProperty('opacity', '0', 'important');
                }
                document.querySelectorAll('.side-btn').forEach(btn => {
                    btn.style.setProperty('display', 'none', 'important');
                    btn.style.setProperty('visibility', 'hidden', 'important');
                    btn.style.setProperty('opacity', '0', 'important');
                });
                const statusElementMmd = document.getElementById('status');
                if (statusElementMmd) {
                    statusElementMmd.style.setProperty('display', 'none', 'important');
                    statusElementMmd.style.setProperty('visibility', 'hidden', 'important');
                    statusElementMmd.style.setProperty('opacity', '0', 'important');
                }

            } else if (effectiveModelType === 'pngtuber') {
                const pngtuberContainer = document.getElementById('pngtuber-container');
                const basePngtuberConfig = catgirlConfig.pngtuber || catgirlConfig._reserved?.avatar?.pngtuber || window.lanlan_config?.pngtuber || {};
                const pngtuberConfig = pendingPngtuberReturnConfig
                    ? Object.assign({}, basePngtuberConfig, pendingPngtuberReturnConfig)
                    : basePngtuberConfig;
                pendingPngtuberReturnConfig = null;

                if (window.loadPNGTuberAvatar) {
                    await window.loadPNGTuberAvatar(pngtuberConfig);
                }
                if (window.pngtuberManager && typeof window.pngtuberManager.show === 'function') {
                    window.pngtuberManager.show();
                } else if (pngtuberContainer) {
                    pngtuberContainer.classList.remove('hidden');
                    pngtuberContainer.style.removeProperty('display');
                    pngtuberContainer.style.display = 'block';
                    pngtuberContainer.style.visibility = 'visible';
                }

                const modelReturnEnterRect = pngtuberContainer ? consumeModelReturnEnterRect() : null;
                if (pngtuberContainer) {
                    prepareModelReturnContainer(pngtuberContainer, modelReturnEnterRect, { clearPointerEvents: true });
                    if (modelReturnEnterRect) {
                        playModelReturnEnter(pngtuberContainer, modelReturnEnterRect);
                    }
                    pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');
                    pngtuberContainer.querySelectorAll('.pngtuber-image').forEach((pngtuberImage) => {
                        pngtuberImage.style.removeProperty('transition');
                        pngtuberImage.style.removeProperty('opacity');
                        pngtuberImage.style.setProperty('visibility', 'visible', 'important');
                        pngtuberImage.style.setProperty('pointer-events', 'auto', 'important');
                    });
                }

                const live2dContainerPngtuber = document.getElementById('live2d-container');
                if (live2dContainerPngtuber) { live2dContainerPngtuber.style.display = 'none'; live2dContainerPngtuber.classList.add('hidden'); }
                const vrmContainerPngtuber = document.getElementById('vrm-container');
                if (vrmContainerPngtuber) { vrmContainerPngtuber.style.display = 'none'; vrmContainerPngtuber.classList.add('hidden'); }
                const vrmCanvasPngtuber = document.getElementById('vrm-canvas');
                if (vrmCanvasPngtuber) { vrmCanvasPngtuber.style.visibility = 'hidden'; vrmCanvasPngtuber.style.pointerEvents = 'none'; }
                const mmdContainerPngtuber = document.getElementById('mmd-container');
                if (mmdContainerPngtuber) { mmdContainerPngtuber.style.display = 'none'; mmdContainerPngtuber.classList.add('hidden'); }
                const mmdCanvasPngtuber = document.getElementById('mmd-canvas');
                if (mmdCanvasPngtuber) { mmdCanvasPngtuber.style.visibility = 'hidden'; mmdCanvasPngtuber.style.pointerEvents = 'none'; }

                ['live2d', 'vrm', 'mmd'].forEach(prefix => {
                    const floatingButtons = document.getElementById(`${prefix}-floating-buttons`);
                    if (floatingButtons) floatingButtons.style.display = 'none';
                    const lockIcon = document.getElementById(`${prefix}-lock-icon`);
                    if (lockIcon) lockIcon.style.display = 'none';
                });

                if (window.pngtuberManager && typeof window.pngtuberManager.setupFloatingButtons === 'function') {
                    window.pngtuberManager.setupFloatingButtons();
                }
            } else {
                // 显示 Live2D 模型
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

                // 隐藏MMD容器和按钮
                const mmdContainerL2d = document.getElementById('mmd-container');
                if (mmdContainerL2d) { mmdContainerL2d.style.display = 'none'; mmdContainerL2d.classList.add('hidden'); }
                const mmdCanvasL2d = document.getElementById('mmd-canvas');
                if (mmdCanvasL2d) { mmdCanvasL2d.style.visibility = 'hidden'; mmdCanvasL2d.style.pointerEvents = 'none'; }
                const mmdFloatingButtonsL2d = document.getElementById('mmd-floating-buttons');
                if (mmdFloatingButtonsL2d) { mmdFloatingButtonsL2d.style.display = 'none'; }
                const mmdLockIconL2d = document.getElementById('mmd-lock-icon');
                if (mmdLockIconL2d) { mmdLockIconL2d.style.display = 'none'; }
            }
        } catch (error) {
            console.error('[showCurrentModel] 失败:', error);
            // 出错时检查是否有 VRM/MMD 正在运行且可见，如果有则保持当前状态
            const vrmEl = document.getElementById('vrm-container');
            const mmdEl = document.getElementById('mmd-container');
            const isVrmRunning = window.vrmManager && window.vrmManager.currentModel
                && vrmEl && vrmEl.style.display !== 'none' && !vrmEl.classList.contains('hidden');
            const isMmdRunning = window.mmdManager && window.mmdManager.currentModel
                && mmdEl && mmdEl.style.display !== 'none' && !mmdEl.classList.contains('hidden');
            if (isVrmRunning || isMmdRunning) {
                console.log('[showCurrentModel] 保持当前已加载的模型');
                return;
            }
            showLive2d();
        }
    }

    mod.showCurrentModel = showCurrentModel;

    // ================================================================
    //  4. Floating button sync, goodbye/return, event listeners
    //     (app.js lines 6078-6785)
    // ================================================================

    const MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE = 160;
    const RETURN_BALL_DRAG_RECOVERY_POLL_MS = 250;
    const RETURN_BALL_DRAG_STALE_RECOVERY_MS = 12000;
    const MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_FALLBACK_MS = 220;
    const MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS = 600;
    const MULTI_WINDOW_RETURN_BALL_REVEAL_FALLBACK_MS = 600;
    let multiWindowReturnBallDragState = null;
    let idleReturnBallDesktopDragStateFrame = 0;
    let idleReturnBallDesktopDragStatePending = null;
    let pendingPngtuberReturnConfig = null;

    function resolveModelReturnEnter(reason) {
        const resolve = window._nekoModelReturnEnterResolve;
        window._nekoModelReturnEnterResolve = null;
        window._nekoModelReturnEnterPromise = null;
        window._nekoModelReturnEnterContainer = null;
        if (typeof resolve === 'function') {
            try {
                resolve({ reason });
            } catch (_) {}
        }
    }

    function startModelReturnEnterWait(container) {
        resolveModelReturnEnter('replaced');
        let resolveWait = null;
        const promise = new Promise(resolve => {
            resolveWait = resolve;
        });
        window._nekoModelReturnEnterContainer = container || null;
        window._nekoModelReturnEnterResolve = resolveWait;
        window._nekoModelReturnEnterPromise = promise;
        return promise;
    }

    async function waitForModelReturnEnterToSettle() {
        const promise = window._nekoModelReturnEnterPromise;
        if (promise && typeof promise.then === 'function') {
            let timeoutId = null;
            await Promise.race([
                promise,
                new Promise(resolve => {
                    timeoutId = setTimeout(resolve, NEKO_MODEL_RETURN_ENTER_CLEANUP_MS + NEKO_MODEL_RETURN_ENTER_SETTLE_BUFFER_MS);
                })
            ]);
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        }
        await waitForAnimationFrames(2);
    }

    function waitForAnimationFrames(count) {
        const remaining = Math.max(1, Number(count) || 1);
        return new Promise(resolve => {
            const step = (left) => {
                if (left <= 0) {
                    resolve();
                    return;
                }
                requestAnimationFrame(() => step(left - 1));
            };
            step(remaining);
        });
    }

    function isModelContainerVisible(containerId) {
        const el = document.getElementById(containerId);
        if (!el || el.classList.contains('hidden')) return false;
        const style = window.getComputedStyle ? getComputedStyle(el) : el.style;
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function clampPngtuberOffset(value) {
        return Math.max(-5000, Math.min(5000, Number(value) || 0));
    }

    function getPngtuberManager() {
        return window.pngtuberManager || null;
    }

    function syncPngtuberReturnConfig(config) {
        if (!config) return null;
        const manager = getPngtuberManager();
        if (manager && manager.config) {
            manager.config = Object.assign({}, manager.config, config);
            if (typeof manager.applyTransform === 'function') {
                manager.applyTransform();
            }
            if (typeof manager.syncGlobalConfig === 'function') {
                manager.syncGlobalConfig();
            } else if (window.lanlan_config && typeof window.lanlan_config === 'object') {
                window.lanlan_config.pngtuber = Object.assign({}, manager.config);
            }
            if (typeof manager.updateFloatingButtonsPosition === 'function') {
                manager.updateFloatingButtonsPosition();
            }
            if (typeof manager.updateLockIconPosition === 'function') {
                manager.updateLockIconPosition();
            }
            return Object.assign({}, manager.config);
        }
        if (window.lanlan_config && typeof window.lanlan_config === 'object') {
            window.lanlan_config.pngtuber = Object.assign({}, window.lanlan_config.pngtuber || {}, config);
            return Object.assign({}, window.lanlan_config.pngtuber);
        }
        return Object.assign({}, config);
    }

    function applyPngtuberScreenDelta(screenDx, screenDy) {
        const manager = getPngtuberManager();
        const baseConfig = Object.assign(
            {},
            window.lanlan_config && window.lanlan_config.pngtuber ? window.lanlan_config.pngtuber : {},
            manager && manager.config ? manager.config : {}
        );
        baseConfig.offset_x = clampPngtuberOffset((Number(baseConfig.offset_x) || 0) + screenDx);
        baseConfig.offset_y = clampPngtuberOffset((Number(baseConfig.offset_y) || 0) + screenDy);
        return syncPngtuberReturnConfig(baseConfig);
    }

    function getPngtuberScreenRect() {
        const manager = getPngtuberManager();
        const candidates = [
            manager && manager.image,
            manager && manager.canvasElement,
            manager && manager.imageElement,
            document.querySelector('#pngtuber-container .pngtuber-image'),
            document.getElementById('pngtuber-container')
        ];
        for (const candidate of candidates) {
            if (!candidate || typeof candidate.getBoundingClientRect !== 'function') continue;
            try {
                const rect = normalizeNekoScreenRect(candidate.getBoundingClientRect());
                if (rect) return rect;
            } catch (_) {}
        }
        return null;
    }

    function getPngtuberSnapDelta(rect) {
        const normalized = normalizeNekoScreenRect(rect);
        if (!normalized) return null;
        const viewportWidth = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
        const viewportHeight = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1);
        const margin = 12;

        let dx = 0;
        let dy = 0;
        if (normalized.width <= viewportWidth - margin * 2) {
            if (normalized.left < margin) dx = margin - normalized.left;
            if (normalized.right + dx > viewportWidth - margin) dx = viewportWidth - margin - normalized.right;
        } else {
            dx = viewportWidth / 2 - (normalized.left + normalized.width / 2);
        }
        if (normalized.height <= viewportHeight - margin * 2) {
            if (normalized.top < margin) dy = margin - normalized.top;
            if (normalized.bottom + dy > viewportHeight - margin) dy = viewportHeight - margin - normalized.bottom;
        } else {
            dy = viewportHeight / 2 - (normalized.top + normalized.height / 2);
        }

        if (Math.abs(dx) < 1) dx = 0;
        if (Math.abs(dy) < 1) dy = 0;
        return { dx, dy };
    }

    async function snapPngtuberIntoScreen() {
        const manager = getPngtuberManager();
        if (!manager || !manager.config || !isModelContainerVisible('pngtuber-container')) {
            return false;
        }
        const delta = getPngtuberSnapDelta(getPngtuberScreenRect());
        if (!delta || (!delta.dx && !delta.dy)) return false;
        applyPngtuberScreenDelta(delta.dx, delta.dy);
        await waitForAnimationFrames(1);
        return true;
    }

    async function saveReturnModelPosition(modelType) {
        try {
            if (modelType === 'mmd') {
                const interaction = window.mmdManager && window.mmdManager.interaction;
                if (interaction && typeof interaction._savePositionAfterInteraction === 'function') {
                    await interaction._savePositionAfterInteraction();
                }
                return;
            }
            if (modelType === 'vrm') {
                const interaction = window.vrmManager && window.vrmManager.interaction;
                if (interaction && typeof interaction._savePositionAfterInteraction === 'function') {
                    await interaction._savePositionAfterInteraction();
                }
                return;
            }
            if (modelType === 'pngtuber') {
                const manager = getPngtuberManager();
                if (manager && typeof manager.saveCurrentConfig === 'function') {
                    await manager.saveCurrentConfig();
                }
                return;
            }
            if (window.live2dManager && typeof window.live2dManager._savePositionAfterInteraction === 'function') {
                await window.live2dManager._savePositionAfterInteraction();
            }
        } catch (error) {
            console.warn('[App] 保存回来后的模型位置失败:', error);
        }
    }

    async function settleReturnedModelBounds(shouldSaveWhenUnchanged) {
        // showCurrentModel 会恢复容器和 canvas；等布局提交后再读边界，避免拿到隐藏态尺寸。
        await waitForModelReturnEnterToSettle();
        await waitForAnimationFrames(2);

        let activeModelType = null;
        try {
            if (window.mmdManager && window.mmdManager.currentModel && isModelContainerVisible('mmd-container')) {
                activeModelType = 'mmd';
                const interaction = window.mmdManager.interaction;
                if (interaction && typeof interaction._snapModelIntoScreen === 'function') {
                    const snapped = await interaction._snapModelIntoScreen({ animate: true });
                    if (!snapped && shouldSaveWhenUnchanged) {
                        await saveReturnModelPosition('mmd');
                    }
                } else if (shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('mmd');
                }
                return;
            }

            if (window.vrmManager && window.vrmManager.currentModel && isModelContainerVisible('vrm-container')) {
                activeModelType = 'vrm';
                const interaction = window.vrmManager.interaction;
                if (interaction && typeof interaction._snapModelIntoScreen === 'function') {
                    const snapped = await interaction._snapModelIntoScreen({ animate: true });
                    if (snapped) {
                        // VRM 的回弹方法只负责动画，最终位置需要由外层保存。
                        await saveReturnModelPosition('vrm');
                    } else if (shouldSaveWhenUnchanged) {
                        await saveReturnModelPosition('vrm');
                    }
                } else if (shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('vrm');
                }
                return;
            }

            if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'
                && getPngtuberManager()
                && isModelContainerVisible('pngtuber-container')) {
                activeModelType = 'pngtuber';
                const snapped = await snapPngtuberIntoScreen();
                if (snapped || shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('pngtuber');
                }
                return;
            }

            if (window.live2dManager) {
                activeModelType = 'live2d';
                const liveModel = typeof window.live2dManager.getCurrentModel === 'function'
                    ? window.live2dManager.getCurrentModel() : null;
                if (liveModel && !liveModel.destroyed && typeof window.live2dManager._checkAndPerformSnap === 'function') {
                    const snapped = await window.live2dManager._checkAndPerformSnap(liveModel, { allowWhenNotReady: true });
                    if (!snapped && shouldSaveWhenUnchanged) {
                        await saveReturnModelPosition('live2d');
                    }
                } else if (shouldSaveWhenUnchanged) {
                    await saveReturnModelPosition('live2d');
                }
            }
        } catch (error) {
            console.warn('[App] 回来后的边界回弹计算失败:', error);
            if (shouldSaveWhenUnchanged && activeModelType) {
                await saveReturnModelPosition(activeModelType);
            }
        }
    }

    function cancelReturnBallReveal(container) {
        if (!container) return;
        const revealFrameId = container.__nekoReturnBallRevealFrame;
        if (typeof revealFrameId === 'number') {
            cancelAnimationFrame(revealFrameId);
        }
        container.__nekoReturnBallRevealFrame = null;
    }

    function restoreSavedReturnBallStyle(container, state) {
        if (!container) return false;
        const dragState = state ||
            (multiWindowReturnBallDragState && multiWindowReturnBallDragState.container === container
                ? multiWindowReturnBallDragState
                : null);
        const savedStyle = dragState && dragState.savedBallStyle;
        if (!savedStyle) return false;

        container.style.left = savedStyle.left;
        container.style.top = savedStyle.top;
        container.style.right = savedStyle.right;
        container.style.bottom = savedStyle.bottom;
        container.style.transform = savedStyle.transform;
        container.style.opacity = savedStyle.opacity;
        container.style.visibility = savedStyle.visibility;
        container.style.transition = savedStyle.transition;
        container.style.willChange = savedStyle.willChange;
        container.style.removeProperty('--neko-ball-drag-size');
        dragState.savedBallStyle = null;
        return true;
    }

    function buildNekoModelCatTransitionAssetUrl(playbackToken = '') {
        const params = [];
        if (NEKO_MODEL_CAT_TRANSITION_VERSION) {
            params.push(`v=${encodeURIComponent(NEKO_MODEL_CAT_TRANSITION_VERSION)}`);
        }
        if (playbackToken) {
            params.push(`play=${encodeURIComponent(String(playbackToken))}`);
        }
        if (!params.length) {
            return NEKO_MODEL_CAT_TRANSITION_ASSET;
        }
        const separator = NEKO_MODEL_CAT_TRANSITION_ASSET.includes('?') ? '&' : '?';
        return `${NEKO_MODEL_CAT_TRANSITION_ASSET}${separator}${params.join('&')}`;
    }

    function buildNekoModelCatRevealPlaybackUrl(src, playbackToken) {
        if (!src) return '';
        try {
            const url = new URL(src, window.location.href);
            if (!/\/static\/assets\/neko-idle\/.+\.gif$/i.test(url.pathname)) {
                return src;
            }
            url.searchParams.set('reveal', String(playbackToken || Date.now()));
            return url.href;
        } catch (_) {
            return src;
        }
    }

    function restartNekoModelCatRevealArt(container) {
        const art = container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-art:not(.neko-idle-return-art-next)')
            : null;
        if (!art) return false;
        const currentSrc = art.getAttribute('src') || art.currentSrc || '';
        if (!currentSrc) return false;
        const nextSrc = buildNekoModelCatRevealPlaybackUrl(
            currentSrc,
            ++nekoModelCatRevealPlaybackToken
        );
        art.__nekoIdleGifPlaybackToken = (art.__nekoIdleGifPlaybackToken || 0) + 1;
        art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
        art.removeAttribute('src');
        void art.offsetWidth;
        art.src = nextSrc || currentSrc;
        return true;
    }

    function normalizeNekoScreenRect(rect) {
        if (!rect) return null;
        const left = Number(rect.left);
        const top = Number(rect.top);
        const width = Number(rect.width);
        const height = Number(rect.height);
        const right = Number.isFinite(Number(rect.right)) ? Number(rect.right) : left + width;
        const bottom = Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : top + height;
        const normalizedWidth = Number.isFinite(width) && width > 0 ? width : right - left;
        const normalizedHeight = Number.isFinite(height) && height > 0 ? height : bottom - top;
        if (![left, top, normalizedWidth, normalizedHeight].every(Number.isFinite)) return null;
        if (normalizedWidth <= 1 || normalizedHeight <= 1) return null;
        return {
            left: left,
            top: top,
            right: left + normalizedWidth,
            bottom: top + normalizedHeight,
            width: normalizedWidth,
            height: normalizedHeight
        };
    }

    function getModelRectFromManager(manager) {
        if (!manager) return null;
        if (typeof manager.getModelScreenBounds === 'function') {
            try {
                const rect = normalizeNekoScreenRect(manager.getModelScreenBounds());
                if (rect) return rect;
            } catch (_) {}
        }

        if (manager.image && typeof manager.image.getBoundingClientRect === 'function') {
            try {
                const rect = normalizeNekoScreenRect(manager.image.getBoundingClientRect());
                if (rect) return rect;
            } catch (_) {}
        }

        const model = typeof manager.getCurrentModel === 'function'
            ? manager.getCurrentModel()
            : manager.currentModel;
        if (model && typeof model.getBounds === 'function') {
            try {
                const rect = normalizeNekoScreenRect(model.getBounds());
                if (rect) return rect;
            } catch (_) {}
        }
        return null;
    }

    function isModelContainerActive(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return false;
        return container.style.display !== 'none' && !container.classList.contains('hidden');
    }

    function getActiveModelTransitionRect() {
        const isPngtuberModelActive = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'
            && isModelContainerActive('pngtuber-container');
        const candidates = [
            { active: isPngtuberModelActive, manager: window.pngtuberManager },
            { active: isModelContainerActive('mmd-container'), manager: window.mmdManager },
            { active: isModelContainerActive('vrm-container'), manager: window.vrmManager },
            { active: true, manager: window.live2dManager }
        ];

        for (const candidate of candidates) {
            if (!candidate.active) continue;
            const rect = getModelRectFromManager(candidate.manager);
            if (rect) return rect;
        }
        return null;
    }

    function setModelExitTransformOrigin(container, rect) {
        if (!container || !rect) return;
        const normalizedRect = normalizeNekoScreenRect(rect);
        if (!normalizedRect) return;
        const originX = normalizedRect.left + normalizedRect.width / 2;
        const originY = normalizedRect.top + normalizedRect.height / 2;
        container.style.setProperty('--neko-model-exit-origin-x', `${Math.round(originX)}px`);
        container.style.setProperty('--neko-model-exit-origin-y', `${Math.round(originY)}px`);
    }

    function getNekoTransitionNowMs() {
        try {
            return window.performance && typeof window.performance.now === 'function'
                ? window.performance.now()
                : Date.now();
        } catch (_) {
            return Date.now();
        }
    }

    function getModelCatTransitionScaleTransform() {
        return `scale(${NEKO_MODEL_CAT_TRANSITION_MODEL_SCALE}) translateZ(0)`;
    }

    function applyNekoTransitionMask(element) {
        if (!element || !element.style) return;
        Object.assign(element.style, {
            maskImage: NEKO_MODEL_CAT_TRANSITION_EDGE_MASK,
            maskRepeat: 'no-repeat',
            maskPosition: 'center',
            maskSize: '100% 100%',
        });
        element.style.webkitMaskImage = NEKO_MODEL_CAT_TRANSITION_EDGE_MASK;
        element.style.webkitMaskRepeat = 'no-repeat';
        element.style.webkitMaskPosition = 'center';
        element.style.webkitMaskSize = '100% 100%';
        element.style.setProperty('-webkit-mask-image', NEKO_MODEL_CAT_TRANSITION_EDGE_MASK);
        element.style.setProperty('-webkit-mask-repeat', 'no-repeat');
        element.style.setProperty('-webkit-mask-position', 'center');
        element.style.setProperty('-webkit-mask-size', '100% 100%');
    }

    function prepareModelReturnContainer(container, rect, options = {}) {
        if (!container) return false;
        const hasReturnRect = !!rect;
        container.style.transition = 'none';
        if (options.removeHidden !== false) {
            container.classList.remove('hidden');
        }
        container.classList.remove('minimized');
        container.removeAttribute('data-neko-model-goodbye-exiting');
        container.style.visibility = 'visible';
        container.style.display = options.display || 'block';
        container.style.opacity = hasReturnRect ? '0' : '1';
        container.style.transform = hasReturnRect ? getModelCatTransitionScaleTransform() : 'none';
        if (options.clearPointerEvents) {
            if (!keepAvatarRootContainerPassthrough(container)) {
                container.style.removeProperty('pointer-events');
            }
        }
        return true;
    }

    function applyModelGoodbyeVisualFade(container, options = {}) {
        const visualLayer = container && typeof container.querySelector === 'function'
            ? container.querySelector('canvas')
            : null;
        if (!visualLayer) return false;
        visualLayer.style.transition = NEKO_MODEL_GOODBYE_VISUAL_FADE_TRANSITION;
        if (options.restart !== false) {
            visualLayer.style.opacity = '1';
            void visualLayer.offsetWidth;
        }
        visualLayer.style.opacity = '0';
        return true;
    }

    function playModelGoodbyeExit(container, rect) {
        if (!container) return;
        if (container.getAttribute('data-neko-model-goodbye-exiting') === 'true') {
            applyModelGoodbyeVisualFade(container, { restart: false });
            return;
        }
        setModelExitTransformOrigin(container, rect);
        container.setAttribute('data-neko-model-goodbye-exiting', 'true');
        container.classList.remove('minimized');
        container.style.removeProperty('visibility');
        container.style.removeProperty('display');
        container.style.removeProperty('transition');
        container.style.removeProperty('opacity');
        container.style.removeProperty('transform');
        void container.offsetWidth;
        container.classList.add('minimized');
        applyModelGoodbyeVisualFade(container, { restart: true });
    }

    function consumeModelReturnEnterRect() {
        const rect = normalizeNekoScreenRect(window._nekoModelReturnEnterRect);
        window._nekoModelReturnEnterRect = null;
        return rect;
    }

    function playModelReturnEnter(container, rect) {
        if (!container || !rect) return false;
        setModelExitTransformOrigin(container, rect);
        if (window._nekoModelReturnEnterTimer) {
            clearTimeout(window._nekoModelReturnEnterTimer);
            window._nekoModelReturnEnterTimer = null;
            resolveModelReturnEnter('timer-cleared');
        }
        startModelReturnEnterWait(container);

        container.style.transition = 'none';
        container.style.opacity = '0';
        container.style.transform = getModelCatTransitionScaleTransform();
        void container.offsetWidth;

        requestAnimationFrame(() => {
            if (!container || !container.isConnected) {
                resolveModelReturnEnter('disconnected-before-raf');
                return;
            }
            container.style.transition = NEKO_MODEL_RETURN_ENTER_TRANSITION;
            container.style.opacity = '1';
            container.style.transform = 'scale(1) translateZ(0)';
            window._nekoModelReturnEnterTimer = setTimeout(() => {
                if (container && container.isConnected) {
                    container.style.removeProperty('transition');
                    container.style.removeProperty('opacity');
                    container.style.removeProperty('transform');
                }
                window._nekoModelReturnEnterTimer = null;
                resolveModelReturnEnter('cleanup');
            }, NEKO_MODEL_RETURN_ENTER_CLEANUP_MS);
        });
        return true;
    }

    function mergeNekoTransitionAnchorRect(anchorRect, coverRect) {
        const anchor = normalizeNekoScreenRect(anchorRect);
        const cover = normalizeNekoScreenRect(coverRect);
        if (!anchor || !cover) return anchor || cover || null;
        return {
            left: anchor.left + anchor.width / 2 - cover.width / 2,
            top: anchor.top + anchor.height / 2 - cover.height / 2,
            right: anchor.left + anchor.width / 2 + cover.width / 2,
            bottom: anchor.top + anchor.height / 2 + cover.height / 2,
            width: cover.width,
            height: cover.height
        };
    }

    function normalizeNekoTransitionRect(anchorRect, container, coverRect) {
        let rect = anchorRect || null;
        if (!rect && container && typeof container.getBoundingClientRect === 'function') {
            try {
                rect = container.getBoundingClientRect();
            } catch (_) {
                rect = null;
            }
        }
        rect = mergeNekoTransitionAnchorRect(rect, coverRect);

        const width = Math.max(1, Number(rect && rect.width) || 0);
        const height = Math.max(1, Number(rect && rect.height) || 0);
        const centerX = Number.isFinite(Number(rect && rect.left))
            ? Number(rect.left) + width / 2
            : window.innerWidth - 80;
        const centerY = Number.isFinite(Number(rect && rect.top))
            ? Number(rect.top) + height / 2
            : window.innerHeight - 160;
        const basis = Math.max(width, height, NEKO_MODEL_CAT_TRANSITION_MIN_SIZE);
        const size = Math.max(
            NEKO_MODEL_CAT_TRANSITION_MIN_SIZE,
            Math.min(NEKO_MODEL_CAT_TRANSITION_MAX_SIZE, Math.round(basis * NEKO_MODEL_CAT_TRANSITION_SIZE_FACTOR))
        );

        return {
            left: Math.round(centerX - size / 2),
            top: Math.round(centerY - size / 2),
            width: size,
            height: size,
        };
    }

    function clearNekoModelCatTransitionOverlay(keepToken = '') {
        document.querySelectorAll('#neko-model-cat-transition').forEach((existing) => {
            if (keepToken && existing.getAttribute('data-neko-model-cat-transition-token') === String(keepToken)) {
                return;
            }
            if (existing.parentNode) {
                existing.parentNode.removeChild(existing);
            }
        });
    }

    function getNekoModelCatOverlayVisibleMs() {
        return Math.max(0, NEKO_MODEL_CAT_TRANSITION_DURATION_MS - NEKO_MODEL_CAT_TRANSITION_LOOP_GUARD_MS);
    }

    function getNekoModelCatSettleMs(direction) {
        return direction === 'cat-to-model'
            ? Math.max(NEKO_MODEL_CAT_TRANSITION_DURATION_MS, NEKO_MODEL_CAT_TO_MODEL_LOCK_MS)
            : NEKO_MODEL_CAT_TRANSITION_DURATION_MS;
    }

    function createNekoModelCatTransitionOverlay(rect, direction, token) {
        const overlay = document.createElement('div');
        overlay.id = 'neko-model-cat-transition';
        overlay.setAttribute('data-neko-model-cat-transition-direction', direction);
        overlay.setAttribute('data-neko-model-cat-transition-token', String(token || ''));
        Object.assign(overlay.style, {
            position: 'fixed',
            left: `${rect.left}px`,
            top: `${rect.top}px`,
            width: `${rect.width}px`,
            height: `${rect.height}px`,
            zIndex: '100080',
            pointerEvents: 'none',
            overflow: 'hidden',
            borderRadius: '50%',
            opacity: '1',
            transform: 'translateZ(0)',
            willChange: 'opacity, transform',
        });
        applyNekoTransitionMask(overlay);

        const image = document.createElement('img');
        image.alt = '';
        image.draggable = false;
        Object.assign(image.style, {
            width: '100%',
            height: '100%',
            display: 'block',
            objectFit: 'contain',
            objectPosition: 'center',
            pointerEvents: 'none',
            userSelect: 'none',
        });
        applyNekoTransitionMask(image);
        overlay.appendChild(image);
        return { overlay, image };
    }

    function isNekoModelCatTransitionActive(direction = '') {
        if (!nekoModelCatTransitionActive) return false;
        if (!direction) return true;
        return nekoModelCatTransitionActive.direction === direction;
    }

    function reserveNekoModelCatTransition(direction) {
        if (nekoModelCatTransitionActive) return null;
        const token = ++nekoModelCatTransitionToken;
        nekoModelCatTransitionActive = {
            token,
            direction: direction || 'model-to-cat',
            reserved: true,
            promise: null
        };
        return token;
    }

    function releaseNekoModelCatTransition(token) {
        if (nekoModelCatTransitionActive && nekoModelCatTransitionActive.token === token) {
            nekoModelCatTransitionActive = null;
        }
    }

    function isNekoModelCatTransitionTokenCurrent(token) {
        return !!(
            nekoModelCatTransitionActive &&
            nekoModelCatTransitionActive.token === token
        );
    }

    function shouldBlockCatToModelTransitionForModelViewport(direction) {
        if (direction !== 'cat-to-model') return false;
        const restoreBounds = getPendingModelViewportRestoreBounds();
        if (restoreBounds && !isModelViewportRestored(restoreBounds)) {
            return true;
        }
        const blockRawShrink = isNativeReturnBallViewportSize(window.innerWidth, window.innerHeight);
        return blockRawShrink;
    }

    function playNekoModelCatTransition(options = {}) {
        const container = options.container || null;
        const anchorRect = options.anchorRect || null;
        const coverRect = options.coverRect || null;
        const direction = options.direction || 'model-to-cat';
        const transitionToken = options.transitionToken || null;
        const onBeforeOverlayCleanup = typeof options.onBeforeOverlayCleanup === 'function'
            ? options.onBeforeOverlayCleanup
            : null;
        const beforeOverlayCleanupMs = Number.isFinite(Number(options.beforeOverlayCleanupMs))
            ? Math.max(0, Number(options.beforeOverlayCleanupMs))
            : NEKO_MODEL_CAT_REVEAL_BEFORE_SMOKE_HIDE_MS;
        let token = transitionToken;
        if (shouldBlockCatToModelTransitionForModelViewport(direction)) {
            return Promise.resolve({
                blocked: true,
                direction,
                reason: 'model-viewport-not-restored'
            });
        }
        if (nekoModelCatTransitionActive) {
            const ownsActiveTransition = transitionToken &&
                nekoModelCatTransitionActive.token === transitionToken &&
                nekoModelCatTransitionActive.direction === direction;
            if (!ownsActiveTransition) {
                return Promise.resolve({
                    blocked: true,
                    direction: nekoModelCatTransitionActive.direction
                });
            }
        } else {
            token = reserveNekoModelCatTransition(direction);
        }
        if (!token) {
            return Promise.resolve({
                blocked: true,
                direction: nekoModelCatTransitionActive ? nekoModelCatTransitionActive.direction : direction
            });
        }
        const rect = normalizeNekoTransitionRect(anchorRect, container, coverRect);
        const src = buildNekoModelCatTransitionAssetUrl(token);

        if (container) {
            container.setAttribute('data-neko-model-cat-transitioning', direction);
            container.style.pointerEvents = 'none';
            if (direction === 'cat-to-model') {
                container.style.opacity = '0';
                container.style.visibility = 'hidden';
            }
        }

        clearNekoModelCatTransitionOverlay(token);
        const { overlay, image } = createNekoModelCatTransitionOverlay(rect, direction, token);
        let playbackStartedAt = getNekoTransitionNowMs();
        let overlayCleanupTimer = null;
        let beforeOverlayCleanupTimer = null;
        let finishTimer = null;
        let imageLoadFallbackTimer = null;
        let didImageLoad = false;
        let didStartPlayback = false;
        let didSchedulePlayback = false;
        let didCallBeforeOverlayCleanup = false;
        let didCleanupOverlay = false;
        let didFinish = false;
        const isCurrentTransition = () => isNekoModelCatTransitionTokenCurrent(token);
        const runBeforeOverlayCleanup = () => {
            if (!isCurrentTransition()) return;
            if (didFinish || didCallBeforeOverlayCleanup || !onBeforeOverlayCleanup) return;
            didCallBeforeOverlayCleanup = true;
            try {
                onBeforeOverlayCleanup();
            } catch (error) {
                console.warn('[App] model/cat transition before-overlay-cleanup callback failed:', error);
            }
        };
        const cleanupOverlay = () => {
            if (!isCurrentTransition()) return;
            if (didCleanupOverlay) return;
            didCleanupOverlay = true;
            if (overlay.parentNode) {
                overlay.parentNode.removeChild(overlay);
            }
        };
        const finishTransition = (resolve) => {
            if (didFinish) return;
            didFinish = true;
            if (imageLoadFallbackTimer) {
                clearTimeout(imageLoadFallbackTimer);
                imageLoadFallbackTimer = null;
            }
            if (isCurrentTransition()) {
                cleanupOverlay();
                if (container && container.isConnected) {
                    container.removeAttribute('data-neko-model-cat-transitioning');
                    if (direction === 'cat-to-model') {
                        container.style.removeProperty('visibility');
                    }
                }
                releaseNekoModelCatTransition(token);
            }
            resolve({ completed: true, direction });
        };
        const scheduleTransitionTimers = (resolve) => {
            if (didFinish) return;
            if (didSchedulePlayback) return;
            didSchedulePlayback = true;
            if (imageLoadFallbackTimer) {
                clearTimeout(imageLoadFallbackTimer);
                imageLoadFallbackTimer = null;
            }
            if (overlayCleanupTimer) clearTimeout(overlayCleanupTimer);
            if (beforeOverlayCleanupTimer) clearTimeout(beforeOverlayCleanupTimer);
            if (finishTimer) clearTimeout(finishTimer);
            const elapsedMs = Math.max(0, getNekoTransitionNowMs() - playbackStartedAt);
            const visibleDurationMs = getNekoModelCatOverlayVisibleMs();
            const settleDurationMs = getNekoModelCatSettleMs(direction);
            const overlayRemainingMs = Math.max(0, visibleDurationMs - elapsedMs);
            const finishRemainingMs = Math.max(0, settleDurationMs - elapsedMs);
            if (onBeforeOverlayCleanup && didImageLoad && !didCallBeforeOverlayCleanup) {
                const revealRemainingMs = Math.max(0, overlayRemainingMs - beforeOverlayCleanupMs);
                beforeOverlayCleanupTimer = setTimeout(runBeforeOverlayCleanup, revealRemainingMs);
            }
            overlayCleanupTimer = setTimeout(cleanupOverlay, overlayRemainingMs);
            finishTimer = setTimeout(() => {
                finishTransition(resolve);
            }, finishRemainingMs);
        };
        const transitionPromise = new Promise((resolve) => {
            const ensureOverlayVisible = () => {
                if (!overlay.parentNode) {
                    document.body.appendChild(overlay);
                }
            };
            const startVisibleSmokePlayback = () => {
                if (didFinish || didStartPlayback) return;
                didStartPlayback = true;
                if (!isCurrentTransition()) return;
                image.src = src;
                playbackStartedAt = getNekoTransitionNowMs();
                ensureOverlayVisible();
            };
            const startTransitionPlayback = () => {
                if (didFinish || didSchedulePlayback) return;
                if (imageLoadFallbackTimer) {
                    clearTimeout(imageLoadFallbackTimer);
                    imageLoadFallbackTimer = null;
                }
                if (!isCurrentTransition()) {
                    finishTransition(resolve);
                    return;
                }
                startVisibleSmokePlayback();
                scheduleTransitionTimers(resolve);
            };
            const preloadImage = new Image();
            preloadImage.addEventListener('load', () => {
                didImageLoad = true;
                startTransitionPlayback();
            }, { once: true });
            preloadImage.addEventListener('error', () => {
                didImageLoad = true;
                startTransitionPlayback();
            }, { once: true });
            startVisibleSmokePlayback();
            preloadImage.src = src;
            imageLoadFallbackTimer = setTimeout(() => {
                didImageLoad = true;
                startTransitionPlayback();
            }, NEKO_MODEL_CAT_TRANSITION_LOAD_FALLBACK_MS);
        });
        if (nekoModelCatTransitionActive && nekoModelCatTransitionActive.token === token) {
            nekoModelCatTransitionActive.promise = transitionPromise;
            nekoModelCatTransitionActive.reserved = false;
        }
        return transitionPromise;
    }

    window.isNekoModelCatTransitionActive = isNekoModelCatTransitionActive;
    window.playNekoModelCatTransition = playNekoModelCatTransition;

    function normalizeNekoGoodbyeIdleAppearance(mode) {
        return mode === NEKO_GOODBYE_IDLE_APPEARANCE_BALL
            ? NEKO_GOODBYE_IDLE_APPEARANCE_BALL
            : NEKO_GOODBYE_IDLE_APPEARANCE_CAT;
    }

    function getNekoGoodbyeIdleAppearance() {
        return normalizeNekoGoodbyeIdleAppearance(window.__nekoGoodbyeIdleAppearance || nekoGoodbyeIdleAppearance);
    }

    window.getNekoGoodbyeIdleAppearance = getNekoGoodbyeIdleAppearance;

    function getReturnButtonElement(container) {
        return container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-btn')
            : null;
    }

    function getReturnButtonArtElement(container) {
        return container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-art:not(.neko-idle-return-art-next)')
            : null;
    }

    function getReturnButtonAppearance(container) {
        const button = getReturnButtonElement(container);
        const raw = (container && container.getAttribute(NEKO_GOODBYE_IDLE_APPEARANCE_ATTR)) ||
            (button && button.getAttribute(NEKO_GOODBYE_IDLE_APPEARANCE_ATTR)) ||
            getNekoGoodbyeIdleAppearance();
        return normalizeNekoGoodbyeIdleAppearance(raw);
    }

    function getCurrentNekoIdleReturnTier() {
        try {
            if (window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.getState === 'function') {
                const state = window.nekoAutoGoodbye.getState();
                const tier = state && state.visualTier;
                return (tier === 'cat2' || tier === 'cat3' || tier === 'none') ? tier : 'cat1';
            }
        } catch (_) {}
        return 'cat1';
    }

    function normalizeRestorableNekoIdleReturnTier(tier) {
        return (tier === 'cat1' || tier === 'cat2' || tier === 'cat3') ? tier : '';
    }

    function getRestorableNekoIdleReturnTier(fallbackTier = '') {
        const currentTier = normalizeRestorableNekoIdleReturnTier(getCurrentNekoIdleReturnTier());
        return currentTier || normalizeRestorableNekoIdleReturnTier(fallbackTier) || 'cat1';
    }

    function clearReturnButtonBallOnlyVisualState(container) {
        const button = getReturnButtonElement(container);
        if (!button || !button.classList) return;
        [
            'is-tier-transitioning',
            'is-thought-bubble-active',
            'is-thought-bubble-sleeping',
            'is-thought-bubble-popping',
            'is-cat1-facing-right',
            'is-cat1-walking',
            'is-cat1-stretching',
            'is-cat1-playing',
            'is-cat1-eating',
            'is-cat1-hover-paused',
            'is-drag-action',
            'is-drag-action-pending'
        ].forEach((className) => button.classList.remove(className));
        if (typeof clearNekoIdleCat1EdgePeek === 'function') {
            clearNekoIdleCat1EdgePeek(container);
        }
    }

    function setGoodbyeIdleAppearanceAttributes(container, appearance) {
        const button = getReturnButtonElement(container);
        const nextAppearance = normalizeNekoGoodbyeIdleAppearance(appearance);
        if (container) {
            container.setAttribute(NEKO_GOODBYE_IDLE_APPEARANCE_ATTR, nextAppearance);
        }
        if (button) {
            button.setAttribute(NEKO_GOODBYE_IDLE_APPEARANCE_ATTR, nextAppearance);
        }
    }

    function applyGoodbyeIdleAppearanceToReturnButton(container, appearance = getNekoGoodbyeIdleAppearance()) {
        if (!container) return;
        const nextAppearance = normalizeNekoGoodbyeIdleAppearance(appearance);
        const button = getReturnButtonElement(container);
        const art = getReturnButtonArtElement(container);
        setGoodbyeIdleAppearanceAttributes(container, nextAppearance);

        if (nextAppearance === NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
            // 必须在 clearReturnButtonBallOnlyVisualState 抹掉状态类之前判定：
            // hover/进食/玩耍/行走/拖拽期间 art.src 是一次性 GIF，快照它会在
            // 恢复猫形态时把按钮卡在该帧上（相关 timer/token 已被清掉不会自愈）
            const hasTransientReturnArt = !!(
                (art && art.__nekoIdleHoverSrc) ||
                (button && button.classList && [
                    'is-cat1-eating',
                    'is-cat1-playing',
                    'is-cat1-walking',
                    'is-cat1-stretching',
                    'is-drag-action',
                    'is-drag-action-pending'
                ].some((className) => button.classList.contains(className)))
            );
            clearReturnButtonBallOnlyVisualState(container);
            if (button) {
                const previousTier = normalizeRestorableNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
                button.dataset.nekoGoodbyeIdleCatTier = getRestorableNekoIdleReturnTier(
                    previousTier || button.dataset.nekoGoodbyeIdleCatTier
                );
                button.setAttribute('data-neko-idle-tier', 'none');
            }
            container.setAttribute('data-neko-idle-tier', 'none');
            if (art) {
                const currentSrc = art.getAttribute('src') || art.currentSrc || '';
                if (currentSrc && currentSrc.indexOf(NEKO_GOODBYE_IDLE_BALL_ASSET) === -1 &&
                    !hasTransientReturnArt && !art.dataset.nekoGoodbyeIdleCatSrc) {
                    art.dataset.nekoGoodbyeIdleCatSrc = currentSrc;
                }
                if ((art.getAttribute('src') || '') !== NEKO_GOODBYE_IDLE_BALL_ASSET) {
                    art.src = NEKO_GOODBYE_IDLE_BALL_ASSET;
                }
                art.setAttribute('data-neko-idle-tier', 'none');
                art.setAttribute('aria-hidden', 'true');
                if (art.__nekoIdleTransitionTimer) {
                    clearTimeout(art.__nekoIdleTransitionTimer);
                    art.__nekoIdleTransitionTimer = 0;
                }
                if (art.__nekoIdleHoverTimer) {
                    clearTimeout(art.__nekoIdleHoverTimer);
                    art.__nekoIdleHoverTimer = 0;
                }
                // 必须同时递增 token 并清掉 hover tier，否则挂起中的 gif 时长 promise
                // 仍能通过 token/tier 校验，把猫图写回来盖掉球图
                art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
                delete art.__nekoIdleHoverSrc;
                delete art.__nekoIdleHoverTier;
                delete art.__nekoIdleHoverStartedAt;
                delete art.__nekoIdleTransitionTo;
            }
            if (button && typeof button.querySelectorAll === 'function') {
                button.querySelectorAll('.neko-idle-return-art-next').forEach((nextArt) => nextArt.remove());
            }
            return;
        }

        const savedCatTier = normalizeRestorableNekoIdleReturnTier(button && button.dataset.nekoGoodbyeIdleCatTier);
        const restoredTier = getRestorableNekoIdleReturnTier(button && button.dataset.nekoGoodbyeIdleCatTier);
        if (button) {
            button.setAttribute('data-neko-idle-tier', restoredTier);
            delete button.dataset.nekoGoodbyeIdleCatTier;
        }
        container.setAttribute('data-neko-idle-tier', restoredTier);
        if (art && art.dataset.nekoGoodbyeIdleCatSrc) {
            // 快照 src 只对进球形态时的 tier 有效；球形态期间 tier 推进过就不能回写，
            // 保留 avatar 侧监听器已按当前 tier 重画的图，避免图与 data-neko-idle-tier 不一致
            if (savedCatTier === restoredTier) {
                art.src = art.dataset.nekoGoodbyeIdleCatSrc;
            }
            delete art.dataset.nekoGoodbyeIdleCatSrc;
        }
        if (art) {
            art.setAttribute('data-neko-idle-tier', restoredTier);
            art.removeAttribute('aria-hidden');
        }
    }

    function syncGoodbyeIdleAppearanceForReturnButtons(reason = 'goodbye-idle-appearance') {
        const appearance = getNekoGoodbyeIdleAppearance();
        document.querySelectorAll('[id$="-return-button-container"]').forEach((container) => {
            applyGoodbyeIdleAppearanceToReturnButton(container, appearance);
        });
        scheduleIdleReturnBallDesktopBridge(reason);
    }

    function resetReturnBallTemporaryStyle(container) {
        if (!container) return;
        container.style.removeProperty('opacity');
        container.style.removeProperty('visibility');
        container.style.removeProperty('transition');
        container.style.removeProperty('will-change');
        container.style.removeProperty('--neko-ball-drag-size');
        container.setAttribute('data-dragging', 'false');
    }

    function hideReturnBallContainer(container, reason = 'return-ball-hide') {
        if (!container) return;
        cancelReturnBallReveal(container);
        restoreSavedReturnBallStyle(container);
        resetReturnBallTemporaryStyle(container);
        container.removeAttribute('data-neko-return-visible');
        container.style.display = 'none';
        container.style.pointerEvents = 'none';
        container.style.removeProperty('visibility');
        scheduleIdleReturnBallDesktopBridge(reason || 'return-ball-hide', container);
    }

    window.hideNekoReturnBallContainer = hideReturnBallContainer;

    function positionReturnBallContainer(container, anchorRect) {
        if (!container) return;

        container.style.left = '';
        container.style.top = '';
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';

        if (anchorRect) {
            const containerWidth = Math.round(container.offsetWidth) || 64;
            const containerHeight = Math.round(container.offsetHeight) || 64;
            const maxLeft = Math.max(0, window.innerWidth - containerWidth);
            const maxTop = Math.max(0, window.innerHeight - containerHeight);
            const left = Math.round(anchorRect.left + (anchorRect.width - containerWidth) / 2);
            const top = Math.round(anchorRect.top + (anchorRect.height - containerHeight) / 2);
            container.style.left = `${Math.max(0, Math.min(left, maxLeft))}px`;
            container.style.top = `${Math.max(0, Math.min(top, maxTop))}px`;
            return;
        }

        container.style.right = '16px';
        container.style.bottom = '116px';
    }

    function revealReturnBallContainer(container, reason = 'return-ball-revealed') {
        if (!container || container.style.display === 'none') return;
        container.__nekoReturnBallRevealFrame = null;
        container.removeAttribute('data-neko-model-cat-transitioning');
        container.style.visibility = 'visible';
        container.style.pointerEvents = 'auto';
        container.style.opacity = '1';
        container.style.transform = 'none';
        scheduleIdleReturnBallDesktopBridge(reason, container);
    }

    function showReturnBallContainer(container, anchorRect, options = {}) {
        if (!container) return null;

        cancelReturnBallReveal(container);
        restoreSavedReturnBallStyle(container);
        resetReturnBallTemporaryStyle(container);
        container.setAttribute('data-neko-return-visible', 'true');
        container.style.display = 'flex';
        container.style.visibility = 'hidden';
        container.style.pointerEvents = 'none';
        applyGoodbyeIdleAppearanceToReturnButton(container);
        positionReturnBallContainer(container, anchorRect);
        container.style.opacity = '0';
        container.style.transform = 'translate3d(0, 8px, 0) scale(0.94)';
        container.style.transition = 'opacity 325ms cubic-bezier(0.22, 1, 0.36, 1), transform 400ms cubic-bezier(0.22, 1, 0.36, 1)';
        container.style.willChange = 'opacity, transform';

        void container.offsetWidth;

        if (!options.deferReveal) {
            const revealFrameId = requestAnimationFrame(() => {
                if (container.__nekoReturnBallRevealFrame !== revealFrameId) return;
                revealReturnBallContainer(container, 'return-ball-revealed');
            });
            container.__nekoReturnBallRevealFrame = revealFrameId;
            scheduleIdleReturnBallDesktopBridge('return-ball-show', container);
        }
        return container;
    }

    function getVisibleIdleReturnBallContainer() {
        return document.querySelector('[id$="-return-button-container"][data-neko-return-visible="true"]');
    }

    function getIdleReturnBallScreenRect(container) {
        if (!container || typeof container.getBoundingClientRect !== 'function') return null;
        const rect = container.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
        const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
        return {
            left: Math.round(screenLeft + rect.left),
            top: Math.round(screenTop + rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            right: Math.round(screenLeft + rect.left + rect.width),
            bottom: Math.round(screenTop + rect.top + rect.height)
        };
    }

    function isIdleCat1PlaygroundActiveForReturnBallDesktopBridge() {
        const buttons = document.querySelectorAll('.neko-idle-return-btn');
        for (let i = 0; i < buttons.length; i += 1) {
            const state = buttons[i].__nekoIdleCat1PlaygroundDropState;
            if (state && state.active && !state.released) return true;
            if (buttons[i].__nekoIdleCat1PlaygroundPendingEntry) return true;
        }
        return false;
    }

    function canPostIdleReturnBallDesktopState() {
        const body = document.body;
        return !(body && body.classList && body.classList.contains('electron-chat-window')) &&
            !isIdleCat1PlaygroundActiveForReturnBallDesktopBridge();
    }

    function postIdleReturnBallDesktopState(reason, container, overrideScreenRect) {
        if (!canPostIdleReturnBallDesktopState()) return;
        const target = container || getVisibleIdleReturnBallContainer();
        const visible = !!(target &&
            target.getAttribute('data-neko-return-visible') === 'true' &&
            target.style.display !== 'none' &&
            target.style.visibility !== 'hidden' &&
            target.style.opacity !== '0');
        const tier = visible
            ? (target.getAttribute('data-neko-idle-tier') || 'none')
            : 'none';
        const appearance = visible
            ? getReturnButtonAppearance(target)
            : getNekoGoodbyeIdleAppearance();
        const screenRect = overrideScreenRect || (visible ? getIdleReturnBallScreenRect(target) : null);
        const payload = {
            action: 'idle_return_ball_state',
            source: 'pet-window',
            reason: reason || 'sync',
            visible: visible,
            tier: tier,
            appearance: appearance,
            screenRect: visible ? screenRect : null,
            lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
            timestamp: Date.now()
        };

        window.dispatchEvent(new CustomEvent('neko:idle-return-ball-state', { detail: payload }));

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage(payload);
            } catch (_) {}
        }
    }

    function scheduleIdleReturnBallDesktopBridge(reason, container, overrideScreenRect) {
        if (!canPostIdleReturnBallDesktopState()) return;
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                postIdleReturnBallDesktopState(reason, container, overrideScreenRect);
            });
        });
    }

    function clearIdleReturnBallDesktopDragStateFrame() {
        if (idleReturnBallDesktopDragStateFrame) {
            cancelAnimationFrame(idleReturnBallDesktopDragStateFrame);
            idleReturnBallDesktopDragStateFrame = 0;
        }
        idleReturnBallDesktopDragStatePending = null;
    }

    function scheduleIdleReturnBallDesktopDragState(container, overrideScreenRect) {
        if (!canPostIdleReturnBallDesktopState()) return;
        idleReturnBallDesktopDragStatePending = {
            container: container,
            overrideScreenRect: overrideScreenRect
        };
        if (idleReturnBallDesktopDragStateFrame) return;
        idleReturnBallDesktopDragStateFrame = requestAnimationFrame(() => {
            idleReturnBallDesktopDragStateFrame = 0;
            const pending = idleReturnBallDesktopDragStatePending;
            idleReturnBallDesktopDragStatePending = null;
            if (!pending) return;
            postIdleReturnBallDesktopState(
                'return-ball-dragging',
                pending.container,
                pending.overrideScreenRect
            );
        });
    }

    function getReturnBallDragScreenRect(screenX, screenY, width, height) {
        const w = Math.max(1, Math.round(width || 64));
        const h = Math.max(1, Math.round(height || 64));
        const left = Math.round(screenX - w / 2);
        const top = Math.round(screenY - h / 2);
        return {
            left: left,
            top: top,
            width: w,
            height: h,
            right: left + w,
            bottom: top + h
        };
    }

    function syncIdleReturnBallDesktopStateFromManualMove(detail) {
        if (!detail || typeof detail !== 'object') return;
        const reason = typeof detail.reason === 'string' ? detail.reason : '';
        if (!reason || !reason.startsWith('return-ball-drag-')) return;
        const container = detail.container || getVisibleIdleReturnBallContainer();
        if (!container) return;
        if (reason === 'return-ball-drag-motion') {
            const sx = Number(detail.screenX);
            const sy = Number(detail.screenY);
            const width = container.offsetWidth || Number(detail.width) || 64;
            const height = container.offsetHeight || Number(detail.height) || 64;
            const screenRect = Number.isFinite(sx) && Number.isFinite(sy)
                ? getReturnBallDragScreenRect(sx, sy, width, height)
                : null;
            scheduleIdleReturnBallDesktopDragState(container, screenRect);
            scheduleIdleReturnBallDesktopBridge('return-ball-dragging', container);
            return;
        }
        scheduleIdleReturnBallDesktopBridge(reason, container);
    }

    window.addEventListener('neko:return-ball-manual-move', (event) => {
        syncIdleReturnBallDesktopStateFromManualMove(event && event.detail);
    });

    window.addEventListener('neko:auto-goodbye:state-change', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (!detail || detail.type !== 'visual-tier') return;
        if (getNekoGoodbyeIdleAppearance() === NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
            syncGoodbyeIdleAppearanceForReturnButtons('goodbye-idle-appearance-visual-tier');
            return;
        }
        scheduleIdleReturnBallDesktopBridge(
            detail.source === 'return-ball-drag-demotion' ? 'return-ball-drag-demotion' : 'visual-tier'
        );
    });
    window.addEventListener('neko:goodbye-idle-appearance', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const mode = detail && typeof detail.mode === 'string' ? detail.mode : '';
        nekoGoodbyeIdleAppearance = normalizeNekoGoodbyeIdleAppearance(mode);
        window.__nekoGoodbyeIdleAppearance = nekoGoodbyeIdleAppearance;
        syncGoodbyeIdleAppearanceForReturnButtons(
            detail && detail.reason ? `goodbye-idle-appearance-${detail.reason}` : 'goodbye-idle-appearance'
        );
    });
    window.addEventListener('resize', () => {
        scheduleIdleReturnBallDesktopBridge('viewport-resize');
    });

    function clearMultiWindowReturnBallDeferredWork(state) {
        clearIdleReturnBallDesktopDragStateFrame();
        if (!state) return;
        if (state.viewportWaitFallbackTimer) {
            clearTimeout(state.viewportWaitFallbackTimer);
            state.viewportWaitFallbackTimer = null;
        }
        if (state.transitionCleanupTimer) {
            clearTimeout(state.transitionCleanupTimer);
            state.transitionCleanupTimer = null;
        }
        if (state.viewportWaitOnResize) {
            window.removeEventListener('resize', state.viewportWaitOnResize);
            state.viewportWaitOnResize = null;
        }
    }

    function clearReturnBallDragRecoveryTimer(state) {
        if (!state || !state.dragRecoveryTimer) return;
        clearTimeout(state.dragRecoveryTimer);
        state.dragRecoveryTimer = null;
    }

    function getReturnBallDragScreenCoordinate(value, fallback) {
        return Number.isFinite(value) ? value : fallback;
    }

    const NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO = 0.025;
    const NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO = 0.4;
    const NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES = [
        'is-cat1-edge-peek-left',
        'is-cat1-edge-peek-right',
        'is-cat1-edge-peek-top',
        'is-cat1-edge-peek-bottom',
        'is-cat1-edge-peek-top-left',
        'is-cat1-edge-peek-top-right',
        'is-cat1-edge-peek-bottom-left',
        'is-cat1-edge-peek-bottom-right'
    ];

    function clampNekoIdleCat1EdgePeekCoordinate(value, minValue, maxValue) {
        const normalized = Number(value);
        const min = Number(minValue);
        const max = Number(maxValue);
        if (!Number.isFinite(normalized)) return Number.isFinite(min) ? min : 0;
        if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return normalized;
        return Math.max(min, Math.min(normalized, max));
    }

    function getNekoIdleCat1EdgePeekButton(container) {
        return container && typeof container.querySelector === 'function'
            ? container.querySelector('.neko-idle-return-btn')
            : null;
    }

    function clearNekoIdleCat1EdgePeek(container) {
        const button = getNekoIdleCat1EdgePeekButton(container);
        if (!button) return;
        NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES.forEach((className) => {
            button.classList.remove(className);
        });
        const art = button.querySelector('.neko-idle-return-art');
        if (art) {
            art.style.removeProperty('--neko-idle-return-edge-visual-shift-y');
        }
    }

    function isNekoIdleCat1EdgePeekEligible(container) {
        const button = getNekoIdleCat1EdgePeekButton(container);
        return (button && button.getAttribute('data-neko-idle-tier')) === 'cat1';
    }

    function getNekoIdleCat1EdgePeekPlacement(left, top, width, height, viewportWidth, viewportHeight) {
        const w = Math.max(1, Number(width) || 0);
        const h = Math.max(1, Number(height) || 0);
        const viewportW = Math.max(w, Number(viewportWidth) || 0);
        const viewportH = Math.max(h, Number(viewportHeight) || 0);
        const currentLeft = Number(left);
        const currentTop = Number(top);
        if (!Number.isFinite(currentLeft) || !Number.isFinite(currentTop)) return null;

        const nearLeft = currentLeft <= w * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        const nearRight = viewportW - (currentLeft + w) <= w * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        const nearTop = currentTop <= h * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        const nearBottom = viewportH - (currentTop + h) <= h * NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
        if (!nearLeft && !nearRight && !nearTop && !nearBottom) return null;

        let edge = '';
        const centerX = currentLeft + w / 2;
        if (nearTop) {
            if (nearLeft || centerX <= w) edge = 'top-left';
            else if (nearRight || centerX >= viewportW - w) edge = 'top-right';
            else edge = 'top';
        } else if (nearBottom) {
            if (nearLeft || centerX <= w) edge = 'bottom-left';
            else if (nearRight || centerX >= viewportW - w) edge = 'bottom-right';
            else edge = 'bottom';
        } else if (nearLeft) {
            edge = 'left';
        } else if (nearRight) {
            edge = 'right';
        }

        const hiddenX = w * NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
        const hiddenY = h * NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
        const nextLeft = edge === 'left' || edge === 'top-left' || edge === 'bottom-left'
            ? -hiddenX
            : (edge === 'right' || edge === 'top-right' || edge === 'bottom-right'
                ? viewportW - w + hiddenX
                : clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w));
        const nextTop = edge === 'top' || edge === 'top-left' || edge === 'top-right'
            ? -hiddenY
            : (edge === 'bottom' || edge === 'bottom-left' || edge === 'bottom-right'
                ? viewportH - h + hiddenY
                : clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h));

        return {
            edge,
            left: Math.round(nextLeft),
            top: Math.round(nextTop)
        };
    }

    function applyNekoIdleCat1EdgePeek(container, placement) {
        const button = getNekoIdleCat1EdgePeekButton(container);
        if (!container || !button || !placement || !placement.edge) return false;
        clearNekoIdleCat1EdgePeek(container);
        button.classList.add(`is-cat1-edge-peek-${placement.edge}`);
        const visualShiftY = Number(placement.visualShiftY);
        if (Number.isFinite(visualShiftY) && visualShiftY !== 0) {
            const art = button.querySelector('.neko-idle-return-art');
            if (art) {
                art.style.setProperty('--neko-idle-return-edge-visual-shift-y', `${Math.round(visualShiftY)}px`);
            }
        }
        container.style.left = `${placement.left}px`;
        container.style.top = `${placement.top}px`;
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';
        return true;
    }

    function _getNekoIdleCat1EdgePeekVisualShiftY(bounds, height) {
        const offsetY = Number(bounds && bounds.actualBoundsOffset && bounds.actualBoundsOffset.y);
        if (!Number.isFinite(offsetY) || offsetY <= 0) return 0;
        const maxShift = Math.max(0, Math.round(Number(height) || 0));
        return -Math.min(Math.round(offsetY), maxShift || Math.round(offsetY));
    }

    function restoreNekoIdleCat1EdgePeekBeforeDrag(container) {
        if (!container) return;
        clearNekoIdleCat1EdgePeek(container);
        if (!isNekoIdleCat1EdgePeekEligible(container)) return;
        const w = container.offsetWidth || 64;
        const h = container.offsetHeight || 64;
        const rect = container.getBoundingClientRect && container.getBoundingClientRect();
        const rawLeft = parseFloat(container.style.left);
        const rawTop = parseFloat(container.style.top);
        const currentLeft = Number.isFinite(rawLeft) ? rawLeft : (rect ? rect.left : 0);
        const currentTop = Number.isFinite(rawTop) ? rawTop : (rect ? rect.top : 0);
        container.style.left = `${Math.round(clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, (window.innerWidth || w) - w))}px`;
        container.style.top = `${Math.round(clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, (window.innerHeight || h) - h))}px`;
        container.style.right = '';
        container.style.bottom = '';
        container.style.transform = 'none';
    }

    function isNativeReturnBallDragDisabled() {
        const runtime = window.__NEKO_DESKTOP_RUNTIME__ || {};
        return !!(
            window.__NEKO_DISABLE_NATIVE_RETURN_BALL_DRAG__ ||
            runtime.disableNativeReturnBallDrag
        );
    }

    function isNiriPhysicalCropReturnBallDragActive() {
        const cropApi = window.__nekoNiriPetPhysicalCrop;
        if (!cropApi) return false;
        try {
            if (typeof cropApi.isActive === 'function' && cropApi.isActive()) return true;
        } catch (_) {}
        try {
            const cropState = typeof cropApi.getState === 'function' ? cropApi.getState() : null;
            if (cropState && cropState.enabled) return true;
        } catch (_) {}
        try {
            if (document.documentElement &&
                document.documentElement.classList.contains('neko-niri-pet-physical-crop')) {
                return true;
            }
        } catch (_) {}
        return false;
    }

    function cleanupMultiWindowReturnBallDrag() {
        const state = multiWindowReturnBallDragState;
        if (!state) return;

        const shouldStopNativeDrag = state.isDragging;
        const stopScreenX = getReturnBallDragScreenCoordinate(state.releaseScreenX, state.startScreenX);
        const stopScreenY = getReturnBallDragScreenCoordinate(state.releaseScreenY, state.startScreenY);

        state.dragSessionToken += 1;
        state.isDragging = false;
        clearReturnBallDragRecoveryTimer(state);
        clearMultiWindowReturnBallDeferredWork(state);
        if (state.container) {
            state.container.removeEventListener('mousedown', state.handleMouseDown, true);
            state.container.removeEventListener('touchstart', state.handleTouchStart, true);
            state.container.removeEventListener('click', state.handleClick, true);
        }
        document.removeEventListener('mousemove', state.handleMouseMove);
        document.removeEventListener('mouseup', state.handleMouseUp);
        document.removeEventListener('pointermove', state.handlePointerMove, true);
        document.removeEventListener('pointerup', state.handlePointerUp, true);
        document.removeEventListener('pointercancel', state.handlePointerCancel, true);
        document.removeEventListener('touchmove', state.handleTouchMove);
        document.removeEventListener('touchend', state.handleTouchEnd);
        document.removeEventListener('touchcancel', state.handleTouchEnd);
        window.removeEventListener('blur', state.handleWindowBlur);
        window.removeEventListener('pagehide', state.handlePageHide);
        document.removeEventListener('visibilitychange', state.handleVisibilityChange);
        if (state.suppressDomClickTimer) {
            clearTimeout(state.suppressDomClickTimer);
            state.suppressDomClickTimer = null;
        }

        if (state.container) {
            restoreSavedReturnBallStyle(state.container, state);
            resetReturnBallTemporaryStyle(state.container);
            state.container.setAttribute('data-dragging', 'false');
            state.container.removeAttribute('data-neko-return-click-suppressed');
        }
        delete document.body.dataset.nekoBallDrag;
        multiWindowReturnBallDragState = null;

        if (shouldStopNativeDrag && window.nekoPetDrag && typeof window.nekoPetDrag.stop === 'function') {
            Promise.resolve()
                .then(() => window.nekoPetDrag.stop(stopScreenX, stopScreenY))
                .finally(() => {
                    if (window.nekoPetDrag && typeof window.nekoPetDrag.reveal === 'function') {
                        return window.nekoPetDrag.reveal();
                    }
                    return null;
                })
                .catch(() => {});
        }
    }

    function ensureMultiWindowReturnBallDrag(container) {
        if (!window.__NEKO_MULTI_WINDOW__ || isNativeReturnBallDragDisabled() || !window.nekoPetDrag || !container) {
            cleanupMultiWindowReturnBallDrag();
            return;
        }

        if (multiWindowReturnBallDragState &&
            multiWindowReturnBallDragState.container === container &&
            container.isConnected) {
            return;
        }

        cleanupMultiWindowReturnBallDrag();

        const CLICK_THRESHOLD = 5;
        const state = {
            container,
            isDragging: false,
            hasMoved: false,
            startScreenX: 0,
            startScreenY: 0,
            releaseScreenX: 0,
            releaseScreenY: 0,
            savedWindowW: 0,
            savedWindowH: 0,
            savedBallStyle: null,
            savedBallWidth: 64,
            savedBallHeight: 64,
            niriPhysicalCropDrag: false,
            viewportWaitOnResize: null,
            viewportWaitFallbackTimer: null,
            transitionCleanupTimer: null,
            dragSessionToken: 0,
            dragRecoveryTimer: null,
            lastPointerEventAt: 0,
            suppressDomClickTimer: null,
            handleClick: null,
            handleMouseDown: null,
            handleMouseMove: null,
            handleMouseUp: null,
            handlePointerMove: null,
            handlePointerUp: null,
            handlePointerCancel: null,
            handleTouchStart: null,
            handleTouchMove: null,
            handleTouchEnd: null,
            handleWindowBlur: null,
            handlePageHide: null,
            handleVisibilityChange: null,
        };

        function getTouchScreenPoint(touch) {
            if (!touch) return null;
            return {
                x: typeof touch.screenX === 'number' ? touch.screenX : window.screenX + touch.clientX,
                y: typeof touch.screenY === 'number' ? touch.screenY : window.screenY + touch.clientY,
            };
        }

        function restoreSavedBallStyle() {
            restoreSavedReturnBallStyle(container, state);
        }

        function dispatchReturnBallRevealFailed(reason, error) {
            scheduleIdleReturnBallDesktopBridge('return-ball-reveal-failed', container);
            window.dispatchEvent(new CustomEvent('neko:return-ball-reveal-failed', {
                detail: {
                    reason: reason || 'unknown',
                    container: container,
                    errorMessage: error && (error.message || String(error))
                }
            }));
        }

        function revealReturnBallDragWindow() {
            if (!window.nekoPetDrag || typeof window.nekoPetDrag.reveal !== 'function') {
                dispatchReturnBallRevealFailed('bridge-unavailable');
                return false;
            }
            let settled = false;
            const fallbackTimer = setTimeout(() => {
                if (settled) return;
                dispatchReturnBallRevealFailed('reveal-timeout');
            }, MULTI_WINDOW_RETURN_BALL_REVEAL_FALLBACK_MS);
            try {
                const revealResult = window.nekoPetDrag.reveal();
                Promise.resolve(revealResult).then((ok) => {
                    settled = true;
                    clearTimeout(fallbackTimer);
                    if (ok === false) {
                        dispatchReturnBallRevealFailed('reveal-failed');
                    }
                }).catch((error) => {
                    settled = true;
                    clearTimeout(fallbackTimer);
                    console.warn('[App] 返回球拖拽渲染完成后恢复窗口显示失败:', error);
                    dispatchReturnBallRevealFailed('reveal-rejected', error);
                });
                return true;
            } catch (error) {
                settled = true;
                clearTimeout(fallbackTimer);
                console.warn('[App] 返回球拖拽渲染完成后恢复窗口显示失败:', error);
                dispatchReturnBallRevealFailed('reveal-threw', error);
                return false;
            }
        }

        function getSavedBallStyleValue(key) {
            return state.savedBallStyle ? state.savedBallStyle[key] : '';
        }

        function normalizeWindowBounds(bounds) {
            if (!bounds) return null;
            const source = bounds.requestedBounds || (
                bounds.bounds && !Number.isFinite(Number(bounds.x))
                    ? bounds.bounds
                    : bounds
            );
            const x = Number(source.x);
            const y = Number(source.y);
            const width = Number(source.width);
            const height = Number(source.height);
            if (!Number.isFinite(x) || !Number.isFinite(y) ||
                !Number.isFinite(width) || !Number.isFinite(height)) {
                return null;
            }
            if (width <= 0 || height <= 0) {
                return null;
            }
            const normalized = { x, y, width, height };
            const offset = bounds.actualBoundsOffset || source.actualBoundsOffset;
            const offsetX = Number(offset && offset.x);
            const offsetY = Number(offset && offset.y);
            if (Number.isFinite(offsetX) && Number.isFinite(offsetY)) {
                normalized.actualBoundsOffset = {
                    x: offsetX,
                    y: offsetY
                };
            }
            return normalized;
        }

        function isActiveDragToken(token) {
            return multiWindowReturnBallDragState === state && state.dragSessionToken === token;
        }

        function dispatchReturnBallClick() {
            if (
                container.getAttribute('data-neko-model-cat-transitioning') === 'cat-to-model' ||
                isNekoModelCatTransitionActive()
            ) {
                return;
            }
            const id = String(container.id || '');
            const match = id.match(/^([a-z0-9-]+)-return-button-container$/i);
            if (!match || !match[1]) {
                console.warn('[dispatchReturnBallClick] container id does not match expected pattern, return-click event not dispatched. id:', id);
                return;
            }

            const rect = container.getBoundingClientRect();
            const dispatchClickEvent = () => {
                window.dispatchEvent(new CustomEvent(`${match[1]}-return-click`, {
                    detail: {
                        returnButtonRect: {
                            left: rect.left,
                            top: rect.top,
                            width: rect.width,
                            height: rect.height
                        }
                    }
                }));
            };
            if (getReturnButtonAppearance(container) === NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
                dispatchClickEvent();
                return;
            }
            playNekoModelCatTransition({
                direction: 'cat-to-model',
                anchorRect: rect,
                coverRect: window._savedGoodbyeRect || getActiveModelTransitionRect(),
                container: container
            }).catch(() => {});
            dispatchClickEvent();
        }

        function setReturnBallDomClickSuppressed(suppressed, ttlMs) {
            if (state.suppressDomClickTimer) {
                clearTimeout(state.suppressDomClickTimer);
                state.suppressDomClickTimer = null;
            }
            if (!suppressed) {
                container.removeAttribute('data-neko-return-click-suppressed');
                return;
            }
            container.setAttribute('data-neko-return-click-suppressed', 'true');
            state.suppressDomClickTimer = setTimeout(() => {
                state.suppressDomClickTimer = null;
                container.removeAttribute('data-neko-return-click-suppressed');
            }, Number.isFinite(ttlMs) ? ttlMs : 500);
        }

        function markDragPointerActivity() {
            state.lastPointerEventAt = Date.now();
        }

        function cancelActiveDrag(reason) {
            if (!state.isDragging) return;
            const screenX = getReturnBallDragScreenCoordinate(state.releaseScreenX, state.startScreenX);
            const screenY = getReturnBallDragScreenCoordinate(state.releaseScreenY, state.startScreenY);
            void finishDrag(screenX, screenY, {
                reason: reason || 'return-ball-drag-cancel',
                suppressClick: true
            });
        }

        function scheduleReturnBallDragRecoveryCheck() {
            clearReturnBallDragRecoveryTimer(state);
            if (!state.isDragging) return;
            state.dragRecoveryTimer = setTimeout(() => {
                state.dragRecoveryTimer = null;
                if (!state.isDragging) return;
                if (document.hidden) {
                    cancelActiveDrag('document-hidden');
                    return;
                }
                if (Date.now() - state.lastPointerEventAt > RETURN_BALL_DRAG_STALE_RECOVERY_MS) {
                    cancelActiveDrag('stale-pointer-timeout');
                    return;
                }
                scheduleReturnBallDragRecoveryCheck();
            }, RETURN_BALL_DRAG_RECOVERY_POLL_MS);
        }

        function finishDragIfMouseButtonReleased(event, reason) {
            if (!state.isDragging || !event || (event.pointerType && event.pointerType !== 'mouse')) {
                return false;
            }
            if (!Number.isFinite(event.buttons) || event.buttons !== 0) {
                return false;
            }
            void finishDrag(event.screenX, event.screenY, {
                reason: reason || 'buttons-released'
            });
            return true;
        }

        function isViewportRestored(expectedWidth, expectedHeight) {
            if (!Number.isFinite(expectedWidth) || !Number.isFinite(expectedHeight)) {
                return true;
            }
            const tolerance = 2;
            return Math.abs(window.innerWidth - expectedWidth) <= tolerance &&
                Math.abs(window.innerHeight - expectedHeight) <= tolerance;
        }

        function waitForViewportSize(dragToken, expectedWidth, expectedHeight, onReady, options) {
            if (!isActiveDragToken(dragToken)) return;
            clearMultiWindowReturnBallDeferredWork(state);
            const fallbackMs = options && Number.isFinite(options.fallbackMs)
                ? options.fallbackMs
                : 600;
            const fallbackDeadline = Date.now() + Math.max(0, fallbackMs);
            const hardFallbackDeadline = fallbackDeadline + Math.max(1000, Math.max(0, fallbackMs) * 2);
            const continueOnFallback = !!(options && options.continueOnFallback);

            const runWhenStable = (meta) => {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        if (!isActiveDragToken(dragToken)) return;
                        onReady(meta || {});
                    });
                });
            };

            const tryFinish = () => {
                if (!isActiveDragToken(dragToken)) return false;
                if (!isViewportRestored(expectedWidth, expectedHeight)) return false;
                clearMultiWindowReturnBallDeferredWork(state);
                runWhenStable();
                return true;
            };

            if (tryFinish()) {
                return;
            }

            state.viewportWaitOnResize = () => {
                void tryFinish();
            };
            window.addEventListener('resize', state.viewportWaitOnResize);
            let timeoutWarned = false;
            const pollViewportRestore = () => {
                if (!isActiveDragToken(dragToken)) return;
                if (tryFinish()) return;

                const remainingMs = fallbackDeadline - Date.now();
                if (remainingMs <= 0) {
                    if (continueOnFallback) {
                        console.warn(
                            '[pollViewportRestore] waitForViewportSize timed out; continuing best-effort cleanup.',
                            'dragToken:', state.dragSessionToken,
                            'fallbackMs:', fallbackMs,
                            'fallbackDeadline:', fallbackDeadline
                        );
                        clearMultiWindowReturnBallDeferredWork(state);
                        runWhenStable({ timedOut: true });
                        return;
                    }
                    if (Date.now() >= hardFallbackDeadline) {
                        console.warn(
                            '[pollViewportRestore] waitForViewportSize hard timeout; continuing best-effort cleanup.',
                            'dragToken:', state.dragSessionToken,
                            'fallbackMs:', fallbackMs,
                            'fallbackDeadline:', fallbackDeadline
                        );
                        clearMultiWindowReturnBallDeferredWork(state);
                        runWhenStable();
                        return;
                    }
                    if (!timeoutWarned) {
                        timeoutWarned = true;
                        console.warn(
                            '[pollViewportRestore] waitForViewportSize timed out; keeping return-ball hidden until viewport is restored.',
                            'dragToken:', state.dragSessionToken,
                            'fallbackMs:', fallbackMs,
                            'fallbackDeadline:', fallbackDeadline
                        );
                    }
                    state.viewportWaitFallbackTimer = setTimeout(pollViewportRestore, 50);
                    return;
                }

                state.viewportWaitFallbackTimer = setTimeout(
                    pollViewportRestore,
                    Math.min(remainingMs, 16)
                );
            };
            state.viewportWaitFallbackTimer = setTimeout(
                pollViewportRestore,
                Math.min(Math.max(0, fallbackMs), 16)
            );
        }

        async function resolveFinalWindowBounds(screenX, screenY, dragToken) {
            let bounds = null;
            try {
                bounds = normalizeWindowBounds(await window.nekoPetDrag.stop(screenX, screenY));
            } catch (error) {
                console.warn('[App] 返回球停止拖拽后获取窗口边界失败:', error);
            }

            if (!isActiveDragToken(dragToken)) return null;
            if (bounds) return bounds;

            if (!window.nekoPetDrag || typeof window.nekoPetDrag.getBounds !== 'function') {
                return null;
            }

            try {
                bounds = normalizeWindowBounds(await window.nekoPetDrag.getBounds());
            } catch (error) {
                console.warn('[App] 返回球 fallback 获取窗口边界失败:', error);
            }

            if (!isActiveDragToken(dragToken)) return null;
            return bounds;
        }

        function beginDrag(screenX, screenY, event) {
            if (isIdleCat1PlaygroundActiveForReturnBallDesktopBridge()) return;
            clearMultiWindowReturnBallDeferredWork(state);
            state.dragSessionToken += 1;
            const dragToken = state.dragSessionToken;

            const dragStarted = window.nekoPetDrag.start(screenX, screenY);
            if (dragStarted === false) {
                container.setAttribute('data-dragging', 'false');
                setReturnBallDomClickSuppressed(false);
                return;
            }

            restoreNekoIdleCat1EdgePeekBeforeDrag(container);
            window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                detail: {
                    reason: 'return-ball-drag-start',
                    container: container
                }
            }));
            state.isDragging = true;
            state.hasMoved = false;
            state.startScreenX = screenX;
            state.startScreenY = screenY;
            state.releaseScreenX = screenX;
            state.releaseScreenY = screenY;
            state.savedWindowW = window.innerWidth;
            state.savedWindowH = window.innerHeight;
            state.niriPhysicalCropDrag = isNiriPhysicalCropReturnBallDragActive();
            markDragPointerActivity();

            const rect = container.getBoundingClientRect();
            state.savedBallWidth = Math.round(rect.width) || 64;
            state.savedBallHeight = Math.round(rect.height) || 64;
            state.savedBallStyle = {
                left: container.style.left,
                top: container.style.top,
                right: container.style.right,
                bottom: container.style.bottom,
                transform: container.style.transform,
                opacity: container.style.opacity,
                visibility: container.style.visibility,
                transition: container.style.transition,
                willChange: container.style.willChange,
            };

            const centeredLeft = Math.max(0, Math.round((MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE - state.savedBallWidth) / 2));
            const centeredTop = Math.max(0, Math.round((MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE - state.savedBallHeight) / 2));

            container.style.transition = 'none';
            container.style.setProperty('--neko-ball-drag-size', `${state.savedBallWidth}px`);
            container.setAttribute('data-dragging', 'false');
            if (state.niriPhysicalCropDrag) {
                container.style.opacity = getSavedBallStyleValue('opacity');
                container.style.visibility = getSavedBallStyleValue('visibility');
                container.style.willChange = 'transform';
            } else {
                // 先隐藏球再移动到居中位置，防止闪烁
                container.style.opacity = '0';
                container.style.left = `${centeredLeft}px`;
                container.style.top = `${centeredTop}px`;
                container.style.right = '';
                container.style.bottom = '';
                container.style.transform = 'none';

                document.documentElement.style.setProperty('background', 'transparent', 'important');
                document.body.style.setProperty('background', 'transparent', 'important');
                if (!document.getElementById('_neko-ball-drag-style')) {
                    const styleEl = document.createElement('style');
                    styleEl.id = '_neko-ball-drag-style';
                    styleEl.textContent = [
                        'body[data-neko-ball-drag], body[data-neko-ball-drag] * { background:transparent!important; background-color:transparent!important; box-shadow:none!important; }',
                        'body[data-neko-ball-drag] > *:not([id$="-return-button-container"]) { display:none!important; }',
                        'body[data-neko-ball-drag] * { transition:none!important; animation:none!important; }',
                        'body[data-neko-ball-drag] .neko-idle-return-btn { --neko-idle-return-size:var(--neko-ball-drag-size)!important; width:var(--neko-ball-drag-size)!important; height:var(--neko-ball-drag-size)!important; min-width:var(--neko-ball-drag-size)!important; min-height:var(--neko-ball-drag-size)!important; max-width:var(--neko-ball-drag-size)!important; max-height:var(--neko-ball-drag-size)!important; }',
                        'body[data-neko-ball-drag] .neko-idle-return-art, body[data-neko-ball-drag] .neko-idle-return-art-next { width:100%!important; height:100%!important; object-fit:contain!important; object-position:center!important; }',
                        'body[data-neko-ball-drag] .neko-idle-return-btn.is-cat1-playing > .neko-idle-return-art, body[data-neko-ball-drag] .neko-idle-return-art[data-neko-cat1-play-finishing="true"] { width:175%!important; min-width:175%!important; max-width:none!important; height:100%!important; object-fit:contain!important; object-position:center!important; }',
                    ].join('\n');
                    document.head.appendChild(styleEl);
                }
                document.body.dataset.nekoBallDrag = '1';

                // dragStart 的 shrink 通过异步 IPC 落到主进程，不能再靠固定帧数猜测
                // 拖拽视口已经生效；否则返回球会按临时 left/top 在原窗口左侧闪一帧。
                waitForViewportSize(
                    dragToken,
                    MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE,
                    MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE,
                    () => {
                        if (!state.isDragging || !isActiveDragToken(dragToken)) return;
                        container.style.opacity = getSavedBallStyleValue('opacity');
                        container.style.visibility = getSavedBallStyleValue('visibility');
                        container.style.willChange = 'opacity';
                    },
                    {
                        fallbackMs: MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_FALLBACK_MS,
                        continueOnFallback: true
                    }
                );
            }
            scheduleReturnBallDragRecoveryCheck();

            if (event) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }

        function sendReturnBallNativeDragMove(screenX, screenY) {
            if (!state.niriPhysicalCropDrag ||
                !window.nekoPetDrag ||
                typeof window.nekoPetDrag.move !== 'function') {
                return;
            }
            if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;
            try {
                window.nekoPetDrag.move(screenX, screenY);
            } catch (_) {}
        }

        function updateDrag(screenX, screenY, sourcePoint = null) {
            if (!state.isDragging) return;
            markDragPointerActivity();
            state.releaseScreenX = screenX;
            state.releaseScreenY = screenY;
            sendReturnBallNativeDragMove(screenX, screenY);

            const dx = screenX - state.startScreenX;
            const dy = screenY - state.startScreenY;
            const movedPastClickThreshold = Math.abs(dx) > CLICK_THRESHOLD || Math.abs(dy) > CLICK_THRESHOLD;
            if (!state.hasMoved && movedPastClickThreshold) {
                state.hasMoved = true;
                container.setAttribute('data-dragging', 'true');
                window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                    detail: {
                        reason: 'return-ball-drag-active',
                        container: container
                    }
                }));
            }
            if (state.hasMoved) {
                window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                    detail: {
                        reason: 'return-ball-drag-motion',
                        container: container,
                        clientX: sourcePoint && Number.isFinite(sourcePoint.clientX) ? sourcePoint.clientX : screenX,
                        clientY: sourcePoint && Number.isFinite(sourcePoint.clientY) ? sourcePoint.clientY : screenY,
                        screenX: screenX,
                        screenY: screenY,
                        deltaX: dx,
                        deltaY: dy,
                        timestamp: Date.now()
                    }
                }));
                scheduleIdleReturnBallDesktopDragState(
                    container,
                    getReturnBallDragScreenRect(
                        screenX,
                        screenY,
                        state.savedBallWidth,
                        state.savedBallHeight
                    )
                );
            }
        }

        async function finishDrag(screenX, screenY) {
            if (!state.isDragging) return;

            const options = arguments[2] && typeof arguments[2] === 'object' ? arguments[2] : {};
            const suppressClick = options.suppressClick === true;
            const suppressNoMoveClick = suppressClick;
            state.isDragging = false;
            state.releaseScreenX = screenX;
            state.releaseScreenY = screenY;
            const dragToken = state.dragSessionToken;
            clearReturnBallDragRecoveryTimer(state);
            clearMultiWindowReturnBallDeferredWork(state);

            container.style.transition = 'none';
            if (!state.niriPhysicalCropDrag) {
                // 先瞬间隐藏球，防止恢复 UI 时球在 (8,8) 闪烁
                container.style.opacity = '0';
                container.style.visibility = 'hidden';
                void container.offsetWidth;
            }

            if (!state.hasMoved) {
                container.setAttribute('data-dragging', 'true');
                let restoreBounds = null;
                try {
                    restoreBounds = normalizeWindowBounds(await window.nekoPetDrag.stop(screenX, screenY));
                } catch (error) {
                    console.warn('[App] 返回球点击结束时恢复窗口失败:', error);
                }
                const pendingRestoreBounds = restoreBounds || {
                    width: state.savedWindowW,
                    height: state.savedWindowH
                };
                if (!isActiveDragToken(dragToken)) return;
                setPendingNativeModelViewportRestoreBounds(pendingRestoreBounds);
                const expectedWidth = restoreBounds ? restoreBounds.width : state.savedWindowW;
                const expectedHeight = restoreBounds ? restoreBounds.height : state.savedWindowH;
                const completeNoMoveDrag = () => {
                    restoreSavedBallStyle();
                    delete document.body.dataset.nekoBallDrag;
                    container.setAttribute('data-dragging', 'false');
                    scheduleIdleReturnBallDesktopBridge(
                        suppressNoMoveClick ? 'return-ball-drag-cancel' : 'return-ball-drag-click',
                        container
                    );
                    revealReturnBallDragWindow();
                    if (suppressNoMoveClick) {
                        window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                            detail: {
                                reason: 'return-ball-drag-end',
                                container: container,
                                movedDistancePx: 0,
                                dragCancelled: true
                            }
                        }));
                    } else {
                        setReturnBallDomClickSuppressed(true, 500);
                        dispatchReturnBallClick();
                    }
                };
                if (state.niriPhysicalCropDrag) {
                    completeNoMoveDrag();
                    return;
                }
                waitForViewportSize(dragToken, expectedWidth, expectedHeight, completeNoMoveDrag, {
                    fallbackMs: MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS,
                    continueOnFallback: true
                });
                return;
            }
            const finalBounds = await resolveFinalWindowBounds(screenX, screenY, dragToken);
            if (!isActiveDragToken(dragToken)) return;
            setPendingNativeModelViewportRestoreBounds(finalBounds || {
                width: state.savedWindowW,
                height: state.savedWindowH
            });
            const movedDistancePx = Math.hypot(
                state.releaseScreenX - state.startScreenX,
                state.releaseScreenY - state.startScreenY
            );

            let shouldRestoreSavedBallStyle = false;
            if (finalBounds) {
                const width = state.savedBallWidth || container.offsetWidth || 64;
                const height = state.savedBallHeight || container.offsetHeight || 64;
                const rawLeft = screenX - finalBounds.x - width / 2;
                const rawTop = screenY - finalBounds.y - height / 2;
                const maxLeft = Math.max(0, finalBounds.width - width);
                const maxTop = Math.max(0, finalBounds.height - height);
                const newLeft = Math.max(0, Math.min(Math.round(rawLeft), maxLeft));
                const newTop = Math.max(0, Math.min(Math.round(rawTop), maxTop));
                const placement = isNekoIdleCat1EdgePeekEligible(container)
                    ? getNekoIdleCat1EdgePeekPlacement(
                        newLeft,
                        newTop,
                        width,
                        height,
                        finalBounds.width,
                        finalBounds.height
                    )
                    : null;
                if (placement && placement.edge && placement.edge.includes('bottom')) {
                    placement.visualShiftY = _getNekoIdleCat1EdgePeekVisualShiftY(finalBounds, height);
                }

                if (!applyNekoIdleCat1EdgePeek(container, placement)) {
                    clearNekoIdleCat1EdgePeek(container);
                    container.style.left = `${newLeft}px`;
                    container.style.top = `${newTop}px`;
                    container.style.right = '';
                    container.style.bottom = '';
                    container.style.transform = 'none';
                }
            } else {
                shouldRestoreSavedBallStyle = true;
            }

            const expectedWidth = finalBounds ? finalBounds.width : state.savedWindowW;
            const expectedHeight = finalBounds ? finalBounds.height : state.savedWindowH;
            const completeMovedDrag = () => {
                if (shouldRestoreSavedBallStyle) {
                    restoreSavedBallStyle();
                    container.setAttribute('data-dragging', 'false');
                    delete document.body.dataset.nekoBallDrag;
                    scheduleIdleReturnBallDesktopBridge('return-ball-drag-end', container);
                    window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                        detail: {
                            reason: 'return-ball-drag-end',
                            container: container,
                            movedDistancePx: movedDistancePx,
                            dragCancelled: suppressClick
                        }
                    }));
                    revealReturnBallDragWindow();
                    return;
                }
                // 先同步恢复球 opacity，再删除 nekoBallDrag 显示页面内容，
                // 避免 1 帧"页面可见但球不可见"的闪烁
                container.style.opacity = getSavedBallStyleValue('opacity');
                container.style.visibility = getSavedBallStyleValue('visibility');
                container.style.willChange = getSavedBallStyleValue('willChange');
                container.setAttribute('data-dragging', 'false');
                delete document.body.dataset.nekoBallDrag;
                scheduleIdleReturnBallDesktopBridge('return-ball-drag-end', container);
                window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                    detail: {
                        reason: 'return-ball-drag-end',
                        container: container,
                        movedDistancePx: movedDistancePx,
                        dragCancelled: suppressClick
                    }
                }));
                revealReturnBallDragWindow();
                // 延迟恢复 transition，避免恢复瞬间触发动画
                state.transitionCleanupTimer = setTimeout(() => {
                    state.transitionCleanupTimer = null;
                    if (!isActiveDragToken(dragToken)) return;
                    container.style.transition = getSavedBallStyleValue('transition');
                    state.savedBallStyle = null;
                }, 180);
            };
            if (state.niriPhysicalCropDrag) {
                completeMovedDrag();
                return;
            }
            waitForViewportSize(dragToken, expectedWidth, expectedHeight, completeMovedDrag, {
                fallbackMs: MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS,
                continueOnFallback: true
            });
        }

        function isThoughtBubbleEventTarget(event) {
            const target = event && event.target;
            if (!target || typeof target.closest !== 'function') return false;
            const bubble = target.closest('.neko-idle-thought-bubble');
            return !!(bubble && bubble.closest('.neko-idle-return-btn.is-thought-bubble-active'));
        }

        state.handleMouseDown = (event) => {
            if (event.button !== 0) {
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            if (isThoughtBubbleEventTarget(event)) return;
            beginDrag(event.screenX, event.screenY, event);
        };
        state.handleMouseMove = (event) => {
            if (finishDragIfMouseButtonReleased(event, 'mousemove-buttons-released')) return;
            updateDrag(event.screenX, event.screenY, event);
        };
        state.handleMouseUp = (event) => {
            void finishDrag(event.screenX, event.screenY);
        };
        state.handlePointerMove = (event) => {
            if (finishDragIfMouseButtonReleased(event, 'pointermove-buttons-released')) return;
            if (event && event.pointerType === 'mouse') {
                updateDrag(event.screenX, event.screenY, event);
            }
        };
        state.handlePointerUp = (event) => {
            void finishDrag(event.screenX, event.screenY);
        };
        state.handlePointerCancel = () => {
            cancelActiveDrag('pointercancel');
        };
        state.handleTouchStart = (event) => {
            if (isThoughtBubbleEventTarget(event)) return;
            const point = getTouchScreenPoint(event.touches[0]);
            if (!point) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            beginDrag(point.x, point.y, event);
        };
        state.handleTouchMove = (event) => {
            if (!state.isDragging) return;
            const point = getTouchScreenPoint(event.touches[0]);
            if (!point) return;
            event.preventDefault();
            updateDrag(point.x, point.y, event.touches[0]);
        };
        state.handleTouchEnd = (event) => {
            const point = getTouchScreenPoint(event.changedTouches && event.changedTouches[0]);
            void finishDrag(
                point ? point.x : state.releaseScreenX,
                point ? point.y : state.releaseScreenY
            );
        };
        state.handleWindowBlur = () => {
            if (!state.isDragging) return;
            // Native return-ball dragging may legitimately blur the Pet window when
            // the companion chat layer has focus; stale recovery handles lost release.
            scheduleReturnBallDragRecoveryCheck();
        };
        state.handlePageHide = () => {
            cancelActiveDrag('pagehide');
        };
        state.handleVisibilityChange = () => {
            if (document.hidden) {
                cancelActiveDrag('visibility-hidden');
            }
        };
        state.handleClick = (event) => {
            const isSuppressed = container.getAttribute('data-neko-return-click-suppressed') === 'true';
            const isNativeDragActive = container.getAttribute('data-dragging') === 'true' ||
                container.getAttribute('data-dragging') === 'pending';
            if (!isSuppressed && !isNativeDragActive) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            if (!isNativeDragActive) {
                setReturnBallDomClickSuppressed(false);
            }
        };

        container.addEventListener('mousedown', state.handleMouseDown, true);
        container.addEventListener('touchstart', state.handleTouchStart, true);
        container.addEventListener('click', state.handleClick, true);
        document.addEventListener('mousemove', state.handleMouseMove);
        document.addEventListener('mouseup', state.handleMouseUp);
        document.addEventListener('pointermove', state.handlePointerMove, true);
        document.addEventListener('pointerup', state.handlePointerUp, true);
        document.addEventListener('pointercancel', state.handlePointerCancel, true);
        document.addEventListener('touchmove', state.handleTouchMove, { passive: false });
        document.addEventListener('touchend', state.handleTouchEnd);
        document.addEventListener('touchcancel', state.handleTouchEnd);
        window.addEventListener('blur', state.handleWindowBlur);
        window.addEventListener('pagehide', state.handlePageHide);
        document.addEventListener('visibilitychange', state.handleVisibilityChange);

        multiWindowReturnBallDragState = state;
    }

    window.hideAllNekoReturnBallContainers = function(reason = 'return-ball-hide') {
        hideReturnBallContainer(document.getElementById('live2d-return-button-container'), reason);
        hideReturnBallContainer(document.getElementById('vrm-return-button-container'), reason);
        hideReturnBallContainer(document.getElementById('mmd-return-button-container'), reason);
        ensureMultiWindowReturnBallDrag(null);
    };

    /**
     * Wire up floating-button event listeners.
     * Must be called once after DOM elements are available (from init_app).
     * Receives refs to DOM buttons that still live in app.js's init_app scope.
     */
    function initFloatingButtonListeners() {
        // DOM refs from orchestrator
        const micButton = S.dom.micButton;
        const screenButton = S.dom.screenButton;
        const resetSessionButton = S.dom.resetSessionButton;
        const muteButton = S.dom.muteButton;
        const stopButton = S.dom.stopButton;
        const textSendButton = S.dom.textSendButton;
        const textInputBox = S.dom.textInputBox;
        const screenshotButton = S.dom.screenshotButton;

        // 麦克风按钮（toggle模式） — Live2D / VRM 浮动按钮共用
        window.addEventListener('live2d-mic-toggle', async (e) => {
            if (e.detail.active) {
                if (S.isRecording) {
                    return;
                }
                if (S.voiceStartPending || window.isMicStarting) {
                    return;
                }
                if (!micButton.classList.contains('active')) {
                    micButton.click();
                    return;
                }
                micButton.classList.remove('active');
                micButton.classList.remove('recording');
                micButton.disabled = false;
                micButton.click();
                return;
            } else {
                if (!S.isRecording) {
                    return;
                }
                if (typeof window.stopMicCapture === 'function') {
                    await window.stopMicCapture();
                }
            }
        });

        // 屏幕分享按钮（toggle模式）
        window.addEventListener('live2d-screen-toggle', async (e) => {
            if (e.detail.active) {
                if (typeof window.startScreenSharing === 'function') {
                    await window.startScreenSharing();
                } else {
                    console.error('startScreenSharing function not found');
                }
            } else {
                if (typeof window.stopScreenSharing === 'function') {
                    await window.stopScreenSharing();
                } else {
                    console.error('stopScreenSharing function not found');
                }
            }
        });

        // Agent工具按钮
        window.addEventListener('live2d-agent-click', () => {
            console.log('Agent工具按钮被点击，显示弹出框');
        });

        // 睡觉按钮（请她离开）
        window.addEventListener('live2d-goodbye-click', () => {
            const goodbyeTransitionToken = reserveNekoModelCatTransition('model-to-cat');
            if (!goodbyeTransitionToken) {
                console.log('[App] 模型/猫切换进行中，忽略本次请她离开点击');
                return;
            }
            // 第零步：在任何状态变更之前立即捕获模型位置。
            // return-ball 会出现在这个位置；后续 return 时也以它作为模型位移基准。
            const savedModelRect = getActiveModelTransitionRect();

            // 按钮位置只作为模型 bounds 不可用时的兜底。
            // 其他 handler（VRM/MMD goodbyeHandler）可能先于此处执行并隐藏按钮容器，
            // 所以必须在最前面读取位置。
            const _live2dGoodbyeBtn = document.getElementById('live2d-btn-goodbye');
            const _vrmGoodbyeBtn = document.getElementById('vrm-btn-goodbye');
            const _mmdGoodbyeBtn = document.getElementById('mmd-btn-goodbye');
            const _pngtuberGoodbyeBtn = document.getElementById('pngtuber-btn-goodbye');
            let savedGoodbyeRect = null;
            for (const btn of [_mmdGoodbyeBtn, _vrmGoodbyeBtn, _pngtuberGoodbyeBtn, _live2dGoodbyeBtn]) {
                if (!btn) continue;
                try {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        savedGoodbyeRect = r;
                        break;
                    }
                } catch (_) { /* ignore */ }
            }
            savedGoodbyeRect = savedModelRect || savedGoodbyeRect;
            console.log('[App] 请她离开按钮被点击，savedGoodbyeRect:', savedGoodbyeRect ? `${Math.round(savedGoodbyeRect.left)},${Math.round(savedGoodbyeRect.top)}` : 'null', 'source:', savedModelRect ? 'model' : 'button-fallback');

            window._savedGoodbyeRect = savedGoodbyeRect ? {
                left: savedGoodbyeRect.left,
                top: savedGoodbyeRect.top,
                width: savedGoodbyeRect.width,
                height: savedGoodbyeRect.height
            } : null;

            // 第一步：立即设置标志位
            if (window.live2dManager) {
                window.live2dManager._goodbyeClicked = true;
            }
            if (window.vrmManager) {
                window.vrmManager._goodbyeClicked = true;
            }
            if (window.mmdManager) {
                window.mmdManager._goodbyeClicked = true;
            }
            if (window.appInterpage && typeof window.appInterpage.postGoodbyeChatComposerHiddenState === 'function') {
                window.appInterpage.postGoodbyeChatComposerHiddenState(true, 'live2d-goodbye-click');
            } else if (typeof window.postGoodbyeChatComposerHiddenState === 'function') {
                window.postGoodbyeChatComposerHiddenState(true, 'live2d-goodbye-click');
            }
            console.log('[App] 设置 goodbyeClicked 为 true，当前状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined', 'VRM:', window.vrmManager ? window.vrmManager._goodbyeClicked : 'undefined');

            // 立即关闭所有弹窗
            const allLive2dPopups = document.querySelectorAll('[id^="live2d-popup-"]');
            allLive2dPopups.forEach(popup => {
                popup.style.setProperty('display', 'none', 'important');
                popup.style.setProperty('visibility', 'hidden', 'important');
                popup.style.setProperty('opacity', '0', 'important');
                popup.style.setProperty('pointer-events', 'none', 'important');
            });
            const allVrmPopups = document.querySelectorAll('[id^="vrm-popup-"]');
            allVrmPopups.forEach(popup => {
                popup.style.setProperty('display', 'none', 'important');
                popup.style.setProperty('visibility', 'hidden', 'important');
                popup.style.setProperty('opacity', '0', 'important');
                popup.style.setProperty('pointer-events', 'none', 'important');
            });
            const allPngtuberPopups = document.querySelectorAll('[id^="pngtuber-popup-"]');
            allPngtuberPopups.forEach(popup => {
                popup.style.setProperty('display', 'none', 'important');
                popup.style.setProperty('visibility', 'hidden', 'important');
                popup.style.setProperty('opacity', '0', 'important');
                popup.style.setProperty('pointer-events', 'none', 'important');
            });
            // 关闭 MMD 弹窗
            document.querySelectorAll('[id^="mmd-popup-"]').forEach(popup => {
                popup.style.setProperty('display', 'none', 'important');
            });
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
            if (window.vrmManager && typeof window.vrmManager.resetAllButtons === 'function') {
                window.vrmManager.resetAllButtons();
            }
            if (window.pngtuberManager && typeof window.pngtuberManager.resetAllButtons === 'function') {
                window.pngtuberManager.resetAllButtons();
            }

            // 判断当前 PNGTuber 是否激活，告别态只锁定正在使用的 2D 图片模型。
            const pngtuberContainerForState = document.getElementById('pngtuber-container');
            const isPngtuberActiveForState = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber'
                && pngtuberContainerForState
                && pngtuberContainerForState.style.display !== 'none'
                && !pngtuberContainerForState.classList.contains('hidden');

            // 设置锁定状态
            if (window.live2dManager && typeof window.live2dManager.setLocked === 'function') {
                window.live2dManager.setLocked(true, { updateFloatingButtons: false });
            }
            if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                window.vrmManager.core.setLocked(true);
            }
            if (window.mmdManager && window.mmdManager.core && typeof window.mmdManager.core.setLocked === 'function') {
                window.mmdManager.core.setLocked(true);
            }
            if (isPngtuberActiveForState && window.pngtuberManager && typeof window.pngtuberManager.setLocked === 'function') {
                window.pngtuberManager.setLocked(true, { updateFloatingButtons: false });
            }

            // 不立即隐藏 canvas，先仅禁用交互
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                live2dCanvas.style.setProperty('pointer-events', 'none', 'important');
                console.log('[App] 已禁用 live2d-canvas 交互（pointer-events: none），等待过渡动画完成后再隐藏');
            }

            // 语音启动中 resetSessionButton 会短暂 disabled；先在 goodbye 事件内让 Live2D
            // 立即进入退出态，避免旧 reset click 被浏览器吞掉时模型停在原位。
            const live2dContainerForGoodbye = document.getElementById('live2d-container');
            if (live2dContainerForGoodbye) {
                playModelGoodbyeExit(live2dContainerForGoodbye, savedGoodbyeRect);
                console.log('[App] goodbye 事件已立即最小化 live2d-container');
            }

            // 判断当前激活的模型类型
            const vrmContainer = document.getElementById('vrm-container');
            const live2dContainer = document.getElementById('live2d-container');
            const mmdContainer = document.getElementById('mmd-container');
            const pngtuberContainer = document.getElementById('pngtuber-container');
            const isVrmActive = vrmContainer &&
                vrmContainer.style.display !== 'none' &&
                !vrmContainer.classList.contains('hidden');
            const isMmdActive = mmdContainer &&
                mmdContainer.style.display !== 'none' &&
                !mmdContainer.classList.contains('hidden');
            const isPngtuberActive = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber' && pngtuberContainer &&
                pngtuberContainer.style.display !== 'none' &&
                !pngtuberContainer.classList.contains('hidden');
            console.log('[App] 判断当前模型类型 - isVrmActive:', isVrmActive, 'isMmdActive:', isMmdActive);
            const activeGoodbyeModelType = isMmdActive
                ? 'mmd'
                : (isVrmActive ? 'vrm' : (isPngtuberActive ? 'pngtuber' : 'live2d'));
            const goodbyeResourceToken = beginGoodbyeResourceSuspend({
                activeModelType: activeGoodbyeModelType
            });

            // VRM 也先仅禁用交互
            const vrmCanvas = document.getElementById('vrm-canvas');
            if (vrmContainer) {
                vrmContainer.style.setProperty('pointer-events', 'none', 'important');
                console.log('[App] 已禁用 vrm-container 交互，等待过渡动画完成后再隐藏');
            }
            if (vrmCanvas) {
                vrmCanvas.style.setProperty('pointer-events', 'none', 'important');
                console.log('[App] 已禁用 vrm-canvas 交互');
            }

            // MMD：禁用交互 + 立即停物理；容器退场统一走 playModelGoodbyeExit。
            const mmdCanvas = document.getElementById('mmd-canvas');
            if (mmdContainer) {
                mmdContainer.style.setProperty('pointer-events', 'none', 'important');
            }
            if (mmdCanvas) {
                mmdCanvas.style.setProperty('pointer-events', 'none', 'important');
            }
            if (window._mmdCanvasFadeInId) {
                clearTimeout(window._mmdCanvasFadeInId);
                window._mmdCanvasFadeInId = null;
            }
            if (isMmdActive && window.mmdManager) {
                window.mmdManager.enablePhysics = false;
            }
            if (isMmdActive && mmdContainer) {
                playModelGoodbyeExit(mmdContainer, savedGoodbyeRect);
            }

            if (isPngtuberActive && pngtuberContainer) {
                pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');
                const pngtuberImage = pngtuberContainer.querySelector('.pngtuber-image');
                if (pngtuberImage) {
                    pngtuberImage.style.setProperty('pointer-events', 'none', 'important');
                }
            }
            if (isPngtuberActive && pngtuberContainer) {
                playModelGoodbyeExit(pngtuberContainer, savedGoodbyeRect);
            }

            // 为 VRM 容器添加 minimized 类
            if (isVrmActive && vrmContainer) {
                if (window._vrmCanvasFadeInId) {
                    clearInterval(window._vrmCanvasFadeInId);
                    window._vrmCanvasFadeInId = null;
                }
                const vrmCanvasForHide = document.getElementById('vrm-canvas');
                if (vrmCanvasForHide) {
                    vrmCanvasForHide.style.opacity = '';
                }
                playModelGoodbyeExit(vrmContainer, savedGoodbyeRect);
                console.log('[App] 已为 vrm-container 添加 minimized 类，触发退出动画');
            }

            // 延迟隐藏 canvas / container
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
                if (mmdContainer) {
                    mmdContainer.style.setProperty('visibility', 'hidden', 'important');
                    mmdContainer.style.setProperty('display', 'none', 'important');
                }
                if (mmdCanvas) {
                    mmdCanvas.style.setProperty('visibility', 'hidden', 'important');
                    mmdCanvas.style.transition = '';
                }
                if (isPngtuberActive && pngtuberContainer) {
                    pngtuberContainer.style.setProperty('visibility', 'hidden', 'important');
                    pngtuberContainer.style.setProperty('display', 'none', 'important');
                }
                completeGoodbyeResourceSuspend(goodbyeResourceToken);
            }, NEKO_MODEL_CAT_TRANSITION_DURATION_MS);

            // 隐藏所有浮动按钮和锁按钮
            const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
            if (live2dFloatingButtons) {
                live2dFloatingButtons.style.setProperty('display', 'none', 'important');
                live2dFloatingButtons.style.setProperty('visibility', 'hidden', 'important');
                live2dFloatingButtons.style.setProperty('opacity', '0', 'important');
            }
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
            const vrmLockIcon = document.getElementById('vrm-lock-icon');
            if (vrmLockIcon) {
                vrmLockIcon.style.setProperty('display', 'none', 'important');
                vrmLockIcon.style.setProperty('visibility', 'hidden', 'important');
                vrmLockIcon.style.setProperty('opacity', '0', 'important');
            }
            const mmdFloatingButtons = document.getElementById('mmd-floating-buttons');
            if (mmdFloatingButtons) {
                mmdFloatingButtons.style.setProperty('display', 'none', 'important');
                mmdFloatingButtons.style.setProperty('visibility', 'hidden', 'important');
                mmdFloatingButtons.style.setProperty('opacity', '0', 'important');
            }
            const mmdLockIcon = document.getElementById('mmd-lock-icon');
            if (mmdLockIcon) {
                mmdLockIcon.style.setProperty('display', 'none', 'important');
                mmdLockIcon.style.setProperty('visibility', 'hidden', 'important');
                mmdLockIcon.style.setProperty('opacity', '0', 'important');
            }
            const pngtuberFloatingButtons = document.getElementById('pngtuber-floating-buttons');
            if (pngtuberFloatingButtons) {
                pngtuberFloatingButtons.style.setProperty('display', 'none', 'important');
                pngtuberFloatingButtons.style.setProperty('visibility', 'hidden', 'important');
                pngtuberFloatingButtons.style.setProperty('opacity', '0', 'important');
            }
            const isReturningToPngtuber = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';
            const pngtuberLockIcon = document.getElementById('pngtuber-lock-icon');
            if (isReturningToPngtuber && pngtuberLockIcon) {
                pngtuberLockIcon.style.setProperty('display', 'none', 'important');
                pngtuberLockIcon.style.setProperty('visibility', 'hidden', 'important');
                pngtuberLockIcon.style.setProperty('opacity', '0', 'important');
            }

            // 显示独立的"请她回来"按钮
            const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
            let vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
            let mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');
            let pngtuberReturnButtonContainer = document.getElementById('pngtuber-return-button-container');

            const useMmdReturn = isMmdActive;
            const useVrmReturn = isVrmActive && !isMmdActive;
            const usePngtuberReturn = isPngtuberActive && !isVrmActive && !isMmdActive;

            let activeReturnButtonContainer = null;

            // MMD 返回按钮
            if (useMmdReturn && !mmdReturnButtonContainer && window.mmdManager) {
                if (typeof window.mmdManager.setupFloatingButtons === 'function') {
                    window.mmdManager.setupFloatingButtons();
                    mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');
                }
            }
            if (useMmdReturn && mmdReturnButtonContainer) {
                activeReturnButtonContainer = showReturnBallContainer(mmdReturnButtonContainer, savedGoodbyeRect, { deferReveal: true });
            } else {
                hideReturnBallContainer(mmdReturnButtonContainer);
            }

            // 显示Live2D的返回按钮（仅在非VRM/非MMD/非PNGTuber模式时显示）
            const useLive2dReturn = !useVrmReturn && !useMmdReturn && !usePngtuberReturn;
            let live2dReturnContainer = live2dReturnButtonContainer;
            // 与 VRM/MMD/PNGTuber 分支对齐：返回球容器缺失时（模型切换 / 打开过模型管理 / 上一次告别拆除
            // 了浮动按钮）用 setupFloatingButtons 重建，否则 Live2D 会"自动变猫后直接消失"——模型已最小化，
            // 却没有任何可点的毛线球留下，且无法点回来。这是四种模型里唯一漏掉自愈重建的分支。
            if (useLive2dReturn && !live2dReturnContainer && window.live2dManager
                && typeof window.live2dManager.setupFloatingButtons === 'function') {
                const live2dModelForReturn = typeof window.live2dManager.getCurrentModel === 'function'
                    ? window.live2dManager.getCurrentModel()
                    : window.live2dManager.currentModel;
                if (live2dModelForReturn && !live2dModelForReturn.destroyed) {
                    window.live2dManager.setupFloatingButtons(live2dModelForReturn);
                    live2dReturnContainer = document.getElementById('live2d-return-button-container');
                    // setupFloatingButtons 会重新显示主浮动按钮工具栏与锁图标；告别态需再次隐藏，
                    // 并恢复上面 setLocked(true) 的锁定，保持与本 handler 既有隐藏逻辑一致。
                    const rebuiltFloatingButtons = document.getElementById('live2d-floating-buttons');
                    if (rebuiltFloatingButtons) {
                        rebuiltFloatingButtons.style.setProperty('display', 'none', 'important');
                        rebuiltFloatingButtons.style.setProperty('visibility', 'hidden', 'important');
                        rebuiltFloatingButtons.style.setProperty('opacity', '0', 'important');
                    }
                    const rebuiltLockIcon = document.getElementById('live2d-lock-icon');
                    if (rebuiltLockIcon) {
                        rebuiltLockIcon.style.setProperty('display', 'none', 'important');
                        rebuiltLockIcon.style.setProperty('visibility', 'hidden', 'important');
                        rebuiltLockIcon.style.setProperty('opacity', '0', 'important');
                    }
                    if (typeof window.live2dManager.setLocked === 'function') {
                        window.live2dManager.setLocked(true, { updateFloatingButtons: false });
                    }
                }
            }
            if (useLive2dReturn && live2dReturnContainer) {
                activeReturnButtonContainer = showReturnBallContainer(live2dReturnContainer, savedGoodbyeRect, { deferReveal: true });
            } else {
                hideReturnBallContainer(live2dReturnContainer);
            }

            if (usePngtuberReturn && !pngtuberReturnButtonContainer && window.pngtuberManager) {
                if (typeof window.pngtuberManager.setupFloatingButtons === 'function') {
                    window.pngtuberManager.setupFloatingButtons();
                    pngtuberReturnButtonContainer = document.getElementById('pngtuber-return-button-container');
                }
            }
            if (usePngtuberReturn && pngtuberReturnButtonContainer) {
                activeReturnButtonContainer = showReturnBallContainer(pngtuberReturnButtonContainer, savedGoodbyeRect);
            } else {
                hideReturnBallContainer(pngtuberReturnButtonContainer);
            }

            // 显示VRM的返回按钮
            console.log('[App] VRM返回按钮检查 - useVrmReturn:', useVrmReturn, 'vrmReturnButtonContainer存在:', !!vrmReturnButtonContainer);

            if (useVrmReturn && !vrmReturnButtonContainer && window.vrmManager) {
                console.log('[App] VRM返回按钮不存在，重新创建浮动按钮系统');
                if (typeof window.vrmManager.setupFloatingButtons === 'function') {
                    window.vrmManager.setupFloatingButtons();
                    vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
                    console.log('[App] 重新创建后VRM返回按钮存在:', !!vrmReturnButtonContainer);
                }
            }

            if (useVrmReturn && vrmReturnButtonContainer) {
                activeReturnButtonContainer = showReturnBallContainer(vrmReturnButtonContainer, savedGoodbyeRect, { deferReveal: true });
            } else {
                hideReturnBallContainer(vrmReturnButtonContainer);
            }

            ensureMultiWindowReturnBallDrag(activeReturnButtonContainer);
            if (activeReturnButtonContainer) {
                let didRevealActiveReturnBall = false;
                const revealActiveReturnBall = (reason) => {
                    if (didRevealActiveReturnBall) return;
                    if (
                        activeReturnButtonContainer &&
                        activeReturnButtonContainer.isConnected &&
                        activeReturnButtonContainer.style.display !== 'none' &&
                        activeReturnButtonContainer.getAttribute('data-neko-return-visible') === 'true'
                    ) {
                        didRevealActiveReturnBall = true;
                        if (getReturnButtonAppearance(activeReturnButtonContainer) !== NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
                            restartNekoModelCatRevealArt(activeReturnButtonContainer);
                        } else {
                            applyGoodbyeIdleAppearanceToReturnButton(activeReturnButtonContainer, NEKO_GOODBYE_IDLE_APPEARANCE_BALL);
                        }
                        revealReturnBallContainer(activeReturnButtonContainer, reason);
                    }
                };
                requestAnimationFrame(() => {
                    if (
                        activeReturnButtonContainer &&
                        activeReturnButtonContainer.isConnected &&
                        activeReturnButtonContainer.style.display !== 'none' &&
                        activeReturnButtonContainer.getAttribute('data-neko-return-visible') === 'true'
                    ) {
                        if (getReturnButtonAppearance(activeReturnButtonContainer) === NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
                            releaseNekoModelCatTransition(goodbyeTransitionToken);
                            revealActiveReturnBall('return-ball-legacy-ball');
                            return;
                        }
                        const transitionAnchorRect = savedGoodbyeRect || activeReturnButtonContainer.getBoundingClientRect();
                        playNekoModelCatTransition({
                            direction: 'model-to-cat',
                            anchorRect: transitionAnchorRect,
                            transitionToken: goodbyeTransitionToken,
                            container: activeReturnButtonContainer,
                            onBeforeOverlayCleanup: () => {
                                revealActiveReturnBall('return-ball-model-cat-transition-smoke-cover');
                            }
                        }).then((transitionResult) => {
                            if (transitionResult && transitionResult.blocked) return;
                            revealActiveReturnBall('return-ball-model-cat-transition-done');
                        }).catch(() => {
                            revealActiveReturnBall('return-ball-model-cat-transition-fallback');
                        });
                    } else {
                        releaseNekoModelCatTransition(goodbyeTransitionToken);
                    }
                });
            } else {
                releaseNekoModelCatTransition(goodbyeTransitionToken);
            }

            // 隐藏 side-btn 按钮和侧边栏
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

            // 自动折叠对话区
            const chatContainerEl = document.getElementById('chat-container');
            const isMobile = typeof window.isMobileWidth === 'function' ? window.isMobileWidth() : (window.innerWidth <= 768);
            const collapseClass = isMobile ? 'mobile-collapsed' : 'minimized';

            console.log('[App] 请他离开 - 检查对话区状态 - 存在:', !!chatContainerEl, '当前类列表:', chatContainerEl ? chatContainerEl.className : 'N/A', '将添加类:', collapseClass);

            if (chatContainerEl && !chatContainerEl.classList.contains(collapseClass)) {
                console.log('[App] 自动折叠对话区');
                chatContainerEl.classList.add(collapseClass);
                console.log('[App] 折叠后类列表:', chatContainerEl.className);

                if (isMobile) {
                    const chatContentWrapper = document.getElementById('chat-content-wrapper');
                    const chatHeader = document.getElementById('chat-header');
                    const textInputArea = document.getElementById('text-input-area');
                    if (chatContentWrapper) chatContentWrapper.style.display = 'none';
                    if (chatHeader) chatHeader.style.display = 'none';
                    if (textInputArea) textInputArea.style.display = 'none';
                }

                const toggleChatBtn = document.getElementById('toggle-chat-btn');
                if (toggleChatBtn) {
                    const iconImg = toggleChatBtn.querySelector('img');
                    if (iconImg) {
                        iconImg.src = '/static/assets/neko-idle/chat-minimized-yarn-ball-116.png';
                        iconImg.srcset = '/static/assets/neko-idle/chat-minimized-yarn-ball-116.png 1x, /static/assets/neko-idle/chat-minimized-yarn-ball-232.png 2x';
                        iconImg.style.imageRendering = 'auto';
                        iconImg.alt = window.t ? window.t('common.expand') : '展开';
                    }
                    toggleChatBtn.title = window.t ? window.t('common.expand') : '展开';

                    if (isMobile) {
                        toggleChatBtn.style.display = 'block';
                        toggleChatBtn.style.visibility = 'visible';
                        toggleChatBtn.style.opacity = '1';
                    }
                }
            }

            // 触发原有的离开逻辑
            const runGoodbyeResetClickIfActive = (reason) => {
                const goodbyeStillActive = !!(
                    (window.live2dManager && window.live2dManager._goodbyeClicked) ||
                    (window.vrmManager && window.vrmManager._goodbyeClicked) ||
                    (window.mmdManager && window.mmdManager._goodbyeClicked)
                );
                if (!goodbyeStillActive) {
                    console.log('[App] 跳过过期的 resetSessionButton.click()：当前已不在 goodbye 状态', reason || '');
                    return false;
                }
                console.log('[App] 触发 resetSessionButton.click()，当前 goodbyeClicked 状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined', 'reason:', reason || 'delayed-goodbye-reset');
                // 语音启动会把侧栏离开按钮置为 disabled；程序化 click 需要先恢复，
                // 后续最终按钮状态仍交给 reset handler 统一收口。
                resetSessionButton.disabled = false;
                resetSessionButton.click();
                return true;
            };
            if (resetSessionButton) {
                if (window._goodbyeResetClickTimerId) {
                    clearTimeout(window._goodbyeResetClickTimerId);
                }
                window._goodbyeResetClickTimerId = setTimeout(() => {
                    window._goodbyeResetClickTimerId = null;
                    runGoodbyeResetClickIfActive('delayed-goodbye-reset');
                }, 10);
            } else {
                console.error('[App] resetSessionButton 未找到！');
        }
    });

        function restoreReturnBallAfterBlockedModelViewport(event) {
            const eventType = String(event && event.type || '');
            const match = eventType.match(/^([a-z0-9-]+)-return-click$/i);
            const returnRect = event && event.detail && event.detail.returnButtonRect;
            const container = match && match[1]
                ? document.getElementById(`${match[1]}-return-button-container`)
                : getVisibleIdleReturnBallContainer();
            if (!container) return;
            if (container.style.display === 'none') {
                showReturnBallContainer(container, returnRect);
            }
            revealReturnBallContainer(container, 'return-ball-model-viewport-blocked');
        }

        // 请她回来按钮（统一处理函数）
        const handleReturnClick = async (event) => {
            console.log('[App] 请她回来按钮被点击，开始恢复所有界面');
            if (isNekoModelCatTransitionActive('model-to-cat')) {
                console.log('[App] 模型正在切换为猫形态，忽略本次请她回来事件');
                return;
            }
            const hadPendingGoodbyeReset = !!window._goodbyeResetClickTimerId;
            if (hadPendingGoodbyeReset) {
                clearTimeout(window._goodbyeResetClickTimerId);
                window._goodbyeResetClickTimerId = null;
            }
            if (window._goodbyeHideTimerId) {
                clearTimeout(window._goodbyeHideTimerId);
                window._goodbyeHideTimerId = null;
                console.log('[App] handleReturnClick: 已取消 goodbye 延迟隐藏定时器');
            }
            const preReturnViewportReady = await ensureModelViewportReadyBeforeShowCurrentModel();
            if (!preReturnViewportReady.ready) {
                console.warn('[App] 请她回来已暂缓：Pet viewport 仍处于猫形态小窗口，保留 return 状态');
                restoreReturnBallAfterBlockedModelViewport(event);
                if (hadPendingGoodbyeReset) {
                    runGoodbyeResetClickIfActive('return-viewport-blocked');
                }
                return;
            }
            const isReturningToPngtuber = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';
            if (multiWindowReturnBallDragState) {
                multiWindowReturnBallDragState.dragSessionToken += 1;
                clearMultiWindowReturnBallDeferredWork(multiWindowReturnBallDragState);
            }
            // 同步 window 中的设置值到状态
            if (typeof window.focusModeEnabled !== 'undefined') {
                S.focusModeEnabled = window.focusModeEnabled;
                console.log('[App] 同步 focusModeEnabled:', S.focusModeEnabled);
            }
            if (typeof window.proactiveChatEnabled !== 'undefined') {
                S.proactiveChatEnabled = window.proactiveChatEnabled;
                console.log('[App] 同步 proactiveChatEnabled:', S.proactiveChatEnabled);
            }

            // 清除"请她离开"标志
            if (window.live2dManager) {
                console.log('[App] 清除 live2dManager._goodbyeClicked，之前值:', window.live2dManager._goodbyeClicked);
                window.live2dManager._goodbyeClicked = false;
            }
            if (window.live2d) {
                window.live2d._goodbyeClicked = false;
            }
            if (window.vrmManager) {
                console.log('[App] 清除 vrmManager._goodbyeClicked，之前值:', window.vrmManager._goodbyeClicked);
                window.vrmManager._goodbyeClicked = false;
            }
            if (window.mmdManager) {
                window.mmdManager._goodbyeClicked = false;
            }
            if (window.appInterpage && typeof window.appInterpage.postGoodbyeChatComposerHiddenState === 'function') {
                window.appInterpage.postGoodbyeChatComposerHiddenState(false, 'return-click');
            } else if (typeof window.postGoodbyeChatComposerHiddenState === 'function') {
                window.postGoodbyeChatComposerHiddenState(false, 'return-click');
            }

            console.log('[App] 标志清除后 - live2dManager._goodbyeClicked:', window.live2dManager?._goodbyeClicked);
            console.log('[App] 标志清除后 - vrmManager._goodbyeClicked:', window.vrmManager?._goodbyeClicked);
            restoreGoodbyeResourceSuspend('return-click');

            // 隐藏"请她回来"按钮
            const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
            const vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
            const mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');
            const pngtuberReturnButtonContainer = document.getElementById('pngtuber-return-button-container');
            hideReturnBallContainer(live2dReturnButtonContainer);
            hideReturnBallContainer(vrmReturnButtonContainer);
            hideReturnBallContainer(mmdReturnButtonContainer);
            hideReturnBallContainer(pngtuberReturnButtonContainer);
            ensureMultiWindowReturnBallDrag(null);

            // 如果返回按钮被拖拽到新位置，先偏移模型再显示，避免闪烁
            const returnRect = event && event.detail && event.detail.returnButtonRect;
            const savedRect = window._savedGoodbyeRect;
            window._nekoModelReturnEnterRect = returnRect || savedRect || null;
            let returnModelWasMoved = false;
            if (returnRect && savedRect) {
                const returnCenterX = returnRect.left + returnRect.width / 2;
                const returnCenterY = returnRect.top + returnRect.height / 2;
                const savedCenterX = savedRect.left + savedRect.width / 2;
                const savedCenterY = savedRect.top + savedRect.height / 2;
                const screenDx = returnCenterX - savedCenterX;
                const screenDy = returnCenterY - savedCenterY;

                if (Math.abs(screenDx) > 5 || Math.abs(screenDy) > 5) {
                    console.log('[App] 返回按钮被拖拽，应用屏幕偏移:', Math.round(screenDx), Math.round(screenDy));
                    if (window.vrmManager && typeof window.vrmManager.applyScreenDelta === 'function') {
                        window.vrmManager.applyScreenDelta(screenDx, screenDy, { clamp: false });
                    }
                    if (window.mmdManager && typeof window.mmdManager.applyScreenDelta === 'function') {
                        window.mmdManager.applyScreenDelta(screenDx, screenDy, { clamp: false });
                    }
                    if (window.live2dManager) {
                        const liveModel = typeof window.live2dManager.getCurrentModel === 'function'
                            ? window.live2dManager.getCurrentModel() : null;
                        if (liveModel && !liveModel.destroyed) {
                            liveModel.x += screenDx;
                            liveModel.y += screenDy;
                        }
                    }
                    if (isReturningToPngtuber && window.pngtuberManager && window.pngtuberManager.config) {
                        pendingPngtuberReturnConfig = applyPngtuberScreenDelta(screenDx, screenDy);
                    }
                    returnModelWasMoved = true;
                }
            }
            window._savedGoodbyeRect = null;
            const isMobileViewport = typeof window.isMobileWidth === 'function'
                ? window.isMobileWidth()
                : (window.innerWidth <= 768);

            // 使用 showCurrentModel() 做最终裁决
            let modelDisplayReady = true;
            try {
                modelDisplayReady = await showCurrentModel();
            } catch (error) {
                console.error('[App] showCurrentModel 失败:', error);
                showLive2d();
            }
            if (modelDisplayReady === false) {
                return;
            }

            await settleReturnedModelBounds(returnModelWasMoved);

            // 恢复 VRM canvas 的可见性
            const vrmCanvas = document.getElementById('vrm-canvas');
            if (vrmCanvas && !window._vrmCanvasFadeInId) {
                vrmCanvas.style.removeProperty('visibility');
                vrmCanvas.style.removeProperty('pointer-events');
                vrmCanvas.style.visibility = 'visible';
                console.log('[App] 已恢复 vrm-canvas 的可见性');
            }

            // 恢复 Live2D canvas 的可见性
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas && !window._returnFadeTimer) {
                live2dCanvas.style.removeProperty('visibility');
                live2dCanvas.style.removeProperty('pointer-events');
                live2dCanvas.style.visibility = 'visible';
                live2dCanvas.style.pointerEvents = 'auto';
                console.log('[App] 已恢复 live2d-canvas 的可见性');
            }

            // 恢复锁按钮
            const live2dLockIcon = document.getElementById('live2d-lock-icon');
            if (live2dLockIcon) {
                live2dLockIcon.style.display = 'block';
                live2dLockIcon.style.removeProperty('visibility');
                live2dLockIcon.style.removeProperty('opacity');
            }
            const vrmLockIcon = document.getElementById('vrm-lock-icon');
            if (vrmLockIcon) {
                if (isMobileViewport) {
                    vrmLockIcon.style.removeProperty('display');
                    vrmLockIcon.style.removeProperty('visibility');
                    vrmLockIcon.style.removeProperty('opacity');
                } else {
                    vrmLockIcon.style.display = 'none';
                    vrmLockIcon.style.visibility = 'hidden';
                    vrmLockIcon.style.opacity = '0';
                }
            }
            const mmdLockIcon = document.getElementById('mmd-lock-icon');
            if (mmdLockIcon) {
                if (isMobileViewport) {
                    mmdLockIcon.style.removeProperty('display');
                    mmdLockIcon.style.removeProperty('visibility');
                    mmdLockIcon.style.removeProperty('opacity');
                } else {
                    mmdLockIcon.style.display = 'none';
                    mmdLockIcon.style.visibility = 'hidden';
                    mmdLockIcon.style.opacity = '0';
                }
            }
            // 回来后统一清理锁定状态，不回放离开前的锁定快照，避免 UI、拖拽和穿透状态分叉。
            const pngtuberLockIcon = document.getElementById('pngtuber-lock-icon');
            if (pngtuberLockIcon) {
                pngtuberLockIcon.style.removeProperty('display');
                pngtuberLockIcon.style.removeProperty('visibility');
                pngtuberLockIcon.style.removeProperty('opacity');
            }
            if (window.live2dManager && typeof window.live2dManager.setLocked === 'function') {
                window.live2dManager.setLocked(false, { updateFloatingButtons: false });
            }
            if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                window.vrmManager.core.setLocked(false);
            }
            if (window.mmdManager && window.mmdManager.core && typeof window.mmdManager.core.setLocked === 'function') {
                window.mmdManager.core.setLocked(false);
            }
            if (window.pngtuberManager && typeof window.pngtuberManager.setLocked === 'function') {
                window.pngtuberManager.setLocked(false, { updateFloatingButtons: false });
            }

            // 恢复浮动按钮系统
            const live2dFloatingButtons = document.getElementById('live2d-floating-buttons');
            if (live2dFloatingButtons) {
                live2dFloatingButtons.style.removeProperty('display');
                live2dFloatingButtons.style.removeProperty('visibility');
                live2dFloatingButtons.style.removeProperty('opacity');

                live2dFloatingButtons.style.setProperty('display', 'flex', 'important');
                live2dFloatingButtons.style.setProperty('visibility', 'visible', 'important');
                live2dFloatingButtons.style.setProperty('opacity', '1', 'important');

                if (window.live2dManager && window.live2dManager._floatingButtons) {
                    Object.keys(window.live2dManager._floatingButtons).forEach(btnId => {
                        const buttonData = window.live2dManager._floatingButtons[btnId];
                        if (buttonData && buttonData.button) {
                            buttonData.button.style.removeProperty('display');
                        }
                    });
                }

                const allLive2dPopups = document.querySelectorAll('[id^="live2d-popup-"]');
                allLive2dPopups.forEach(popup => {
                    popup.style.removeProperty('pointer-events');
                    popup.style.removeProperty('visibility');
                    popup.style.pointerEvents = 'auto';
                });
                console.log('[App] 已恢复所有Live2D弹窗的交互能力，数量:', allLive2dPopups.length);
            }

            // 恢复VRM浮动按钮系统
            const vrmFloatingButtons = document.getElementById('vrm-floating-buttons');
            if (vrmFloatingButtons) {
                if (isMobileViewport) {
                    vrmFloatingButtons.style.removeProperty('display');
                    vrmFloatingButtons.style.removeProperty('visibility');
                    vrmFloatingButtons.style.removeProperty('opacity');
                } else {
                    vrmFloatingButtons.style.display = 'none';
                    vrmFloatingButtons.style.visibility = 'hidden';
                    vrmFloatingButtons.style.opacity = '0';
                }

                if (window.vrmManager && window.vrmManager._floatingButtons) {
                    Object.keys(window.vrmManager._floatingButtons).forEach(btnId => {
                        const buttonData = window.vrmManager._floatingButtons[btnId];
                        if (buttonData && buttonData.button) {
                            buttonData.button.style.removeProperty('display');
                        }
                    });
                }

                const allVrmPopups = document.querySelectorAll('[id^="vrm-popup-"]');
                allVrmPopups.forEach(popup => {
                    popup.style.removeProperty('pointer-events');
                    popup.style.removeProperty('visibility');
                    popup.style.pointerEvents = 'auto';
                });
                console.log('[App] 已恢复所有VRM弹窗的交互能力，数量:', allVrmPopups.length);
            }

            // 恢复MMD浮动按钮系统
            const mmdFloatingButtons = document.getElementById('mmd-floating-buttons');
            if (mmdFloatingButtons) {
                if (isMobileViewport) {
                    mmdFloatingButtons.style.removeProperty('display');
                    mmdFloatingButtons.style.removeProperty('visibility');
                    mmdFloatingButtons.style.removeProperty('opacity');
                } else {
                    mmdFloatingButtons.style.display = 'none';
                    mmdFloatingButtons.style.visibility = 'hidden';
                    mmdFloatingButtons.style.opacity = '0';
                }

                if (window.mmdManager && window.mmdManager._floatingButtons) {
                    Object.keys(window.mmdManager._floatingButtons).forEach(btnId => {
                        const buttonData = window.mmdManager._floatingButtons[btnId];
                        if (buttonData && buttonData.button) {
                            buttonData.button.style.removeProperty('display');
                        }
                    });
                }

                const allMmdPopups = document.querySelectorAll('[id^="mmd-popup-"]');
                allMmdPopups.forEach(popup => {
                    popup.style.removeProperty('pointer-events');
                    popup.style.removeProperty('visibility');
                    popup.style.pointerEvents = 'auto';
                });
                console.log('[App] 已恢复所有MMD弹窗的交互能力，数量:', allMmdPopups.length);
            }

            // 恢复对话区
            const chatContainerEl = document.getElementById('chat-container');
            const pngtuberFloatingButtons = document.getElementById('pngtuber-floating-buttons');
            if (isReturningToPngtuber && pngtuberFloatingButtons) {
                pngtuberFloatingButtons.style.removeProperty('display');
                pngtuberFloatingButtons.style.removeProperty('visibility');
                pngtuberFloatingButtons.style.removeProperty('opacity');
                pngtuberFloatingButtons.style.setProperty('display', 'flex', 'important');
                pngtuberFloatingButtons.style.setProperty('visibility', 'visible', 'important');
                pngtuberFloatingButtons.style.setProperty('opacity', '1', 'important');

                const allPngtuberPopups = document.querySelectorAll('[id^="pngtuber-popup-"]');
                allPngtuberPopups.forEach(popup => {
                    popup.style.removeProperty('pointer-events');
                    popup.style.removeProperty('visibility');
                    popup.style.pointerEvents = 'auto';
                });
            }

            const isMobile = isMobileViewport;
            const collapseClass = isMobile ? 'mobile-collapsed' : 'minimized';

            console.log('[App] 检查对话区状态 - 存在:', !!chatContainerEl, '类列表:', chatContainerEl ? chatContainerEl.className : 'N/A', '目标类:', collapseClass);

            if (chatContainerEl && (chatContainerEl.classList.contains('minimized') || chatContainerEl.classList.contains('mobile-collapsed'))) {
                console.log('[App] 自动恢复对话区');
                chatContainerEl.classList.remove('minimized');
                chatContainerEl.classList.remove('mobile-collapsed');
                console.log('[App] 恢复后类列表:', chatContainerEl.className);

                if (isMobile) {
                    const chatContentWrapper = document.getElementById('chat-content-wrapper');
                    const chatHeader = document.getElementById('chat-header');
                    const textInputArea = document.getElementById('text-input-area');
                    if (chatContentWrapper) chatContentWrapper.style.removeProperty('display');
                    if (chatHeader) chatHeader.style.removeProperty('display');
                    if (textInputArea) textInputArea.style.removeProperty('display');
                }

                const toggleChatBtn = document.getElementById('toggle-chat-btn');
                if (toggleChatBtn) {
                    const iconImg = toggleChatBtn.querySelector('img');
                    if (iconImg) {
                        iconImg.src = '/static/icons/expand_icon_off.png';
                        iconImg.removeAttribute('srcset');
                        iconImg.style.imageRendering = '';
                        iconImg.alt = window.t ? window.t('common.minimize') : '最小化';
                    }
                    toggleChatBtn.title = window.t ? window.t('common.minimize') : '最小化';

                    if (typeof scrollToBottom === 'function') {
                        setTimeout(scrollToBottom, 300);
                    }

                    if (isMobile) {
                        toggleChatBtn.style.removeProperty('display');
                        toggleChatBtn.style.removeProperty('visibility');
                        toggleChatBtn.style.removeProperty('opacity');
                    }
                }
            } else {
                console.log('[App] 对话区未恢复 - 条件不满足');
            }

            // 恢复基本的按钮状态
            S.isSwitchingMode = true;

            // 清除所有语音相关的状态类
            micButton.classList.remove('recording');
            micButton.classList.remove('active');
            screenButton.classList.remove('active');

            // 确保停止录音状态
            S.isRecording = false;
            window.isRecording = false;

            // 同步更新Live2D浮动按钮的状态
            if (window.live2dManager && window.live2dManager._floatingButtons) {
                ['mic', 'screen'].forEach(buttonId => {
                    const buttonData = window.live2dManager._floatingButtons[buttonId];
                    if (buttonData && buttonData.button) {
                        buttonData.button.dataset.active = 'false';
                        if (buttonData.imgOff) {
                            buttonData.imgOff.style.opacity = '0.75';
                        }
                        if (buttonData.imgOn) {
                            buttonData.imgOn.style.opacity = '0';
                        }
                    }
                });
                // 隐藏静音按钮（语音功能未开启时不显示）
                const muteButtonData = window.live2dManager._floatingButtons['mic-mute'];
                if (muteButtonData && muteButtonData.button) {
                    muteButtonData.button.style.display = 'none';
                }
            }
            // 同步更新VRM浮动按钮的状态
            if (window.vrmManager && window.vrmManager._floatingButtons) {
                ['mic', 'screen'].forEach(buttonId => {
                    const buttonData = window.vrmManager._floatingButtons[buttonId];
                    if (buttonData && buttonData.button) {
                        buttonData.button.dataset.active = 'false';
                        if (buttonData.imgOff) {
                            buttonData.imgOff.style.opacity = '0.75';
                        }
                        if (buttonData.imgOn) {
                            buttonData.imgOn.style.opacity = '0';
                        }
                    }
                });
                // 隐藏静音按钮（语音功能未开启时不显示）
                const vrmMuteButtonData = window.vrmManager._floatingButtons['mic-mute'];
                if (vrmMuteButtonData && vrmMuteButtonData.button) {
                    vrmMuteButtonData.button.style.display = 'none';
                }
            }
            // 同步更新MMD浮动按钮的状态
            if (window.mmdManager && window.mmdManager._floatingButtons) {
                ['mic', 'screen'].forEach(buttonId => {
                    const buttonData = window.mmdManager._floatingButtons[buttonId];
                    if (buttonData && buttonData.button) {
                        buttonData.button.dataset.active = 'false';
                        if (buttonData.imgOff) {
                            buttonData.imgOff.style.opacity = '0.75';
                        }
                        if (buttonData.imgOn) {
                            buttonData.imgOn.style.opacity = '0';
                        }
                    }
                });
                // 隐藏静音按钮（语音功能未开启时不显示）
                const mmdMuteButtonData = window.mmdManager._floatingButtons['mic-mute'];
                if (mmdMuteButtonData && mmdMuteButtonData.button) {
                    mmdMuteButtonData.button.style.display = 'none';
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

            // 显示文本输入区
            S.voiceChatActive = false;
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
            }
            if (typeof window.syncVoiceChatComposerHidden === 'function') {
                window.syncVoiceChatComposerHidden(false);
            }

            // 标记文本会话为非活跃状态
            S.isTextSessionActive = false;

            // 显示欢迎消息
            showStatusToast(window.t ? window.t('app.welcomeBack', { name: lanlan_config.lanlan_name }) : `\u{1FAF4} ${lanlan_config.lanlan_name}回来了！`, 3000);

            // 恢复主动搭话与主动视觉调度
            try {
                const currentProactiveChat = typeof window.proactiveChatEnabled !== 'undefined'
                    ? window.proactiveChatEnabled
                    : S.proactiveChatEnabled;
                const currentProactiveVision = typeof window.proactiveVisionEnabled !== 'undefined'
                    ? window.proactiveVisionEnabled
                    : S.proactiveVisionEnabled;

                if (currentProactiveChat || currentProactiveVision) {
                    if (typeof window.resetProactiveChatBackoff === 'function') {
                        window.resetProactiveChatBackoff();
                    }
                }
            } catch (e) {
                console.warn('恢复主动搭话/主动视觉失败:', e);
            }

            // 延迟重置模式切换标志
            setTimeout(() => {
                S.isSwitchingMode = false;
            }, 500);

            console.log('[App] 请她回来完成，未自动开始会话，等待用户主动发起对话');
        };

        // 同时监听 Live2D、VRM 和 MMD 的回来事件
        window.addEventListener('live2d-return-click', handleReturnClick);
        window.addEventListener('vrm-return-click', handleReturnClick);
        window.addEventListener('mmd-return-click', handleReturnClick);
        window.addEventListener('pngtuber-return-click', handleReturnClick);
    }

    mod.initFloatingButtonListeners = initFloatingButtonListeners;

    // ================================================================
    //  5. ensureHiddenElements & final UI init  (app.js lines 11354-11420)
    // ================================================================

    /** Force sidebar/sidebarbox/status to stay hidden. */
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

    mod.ensureHiddenElements = ensureHiddenElements;

    /**
     * Set up MutationObserver to keep sidebar/sidebarbox/status hidden,
     * and register beforeunload cleanup.
     * Called once during init.
     */
    function initFinalUiGuards() {
        // 立即执行一次
        ensureHiddenElements();

        // MutationObserver
        const observerCallback = (mutations) => {
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

        // beforeunload cleanup 已在 app.js orchestrator 中注册，此处不再重复
    }

    mod.initFinalUiGuards = initFinalUiGuards;

    // ================================================================
    //  向后兼容 window.xxx 全局导出
    // ================================================================
    // showStatusToast / showProminentNotice 已在上方直接赋值
    window.showVoicePreparingToast = showVoicePreparingToast;
    window.hideVoicePreparingToast = hideVoicePreparingToast;
    window.showReadyToSpeakToast = showReadyToSpeakToast;
    window.syncFloatingMicButtonState = syncFloatingMicButtonState;
    window.syncFloatingScreenButtonState = syncFloatingScreenButtonState;
    window.hideLive2d = hideLive2d;
    window.showLive2d = showLive2d;
    window.showCurrentModel = showCurrentModel;
    window.ensureHiddenElements = ensureHiddenElements;

    // ================================================================
    //  Publish module
    // ================================================================
    window.appUi = mod;
})();
