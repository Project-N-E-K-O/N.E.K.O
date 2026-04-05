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

        const statusToast = S.dom.statusToast;
        const statusElement = S.dom.statusElement;

        if (!statusToast) {
            console.error(window.t('console.statusToastNotFound'));
            return;
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
        msgSpan.textContent = message;
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
        const _hasMore = _prominentNoticeQueue.length > 0;
        btn.textContent = _hasMore
            ? ((window.t && window.t('common.next')) || '下一个')
            : ((window.t && window.t('common.confirm')) || '确认');
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
        textDiv.style.cssText = 'font-size:16px;font-weight:600;line-height:1.7;margin-bottom:22px;text-align:left;';
        if (typeof window.renderMiniMarkdown === 'function') {
            textDiv.innerHTML = window.renderMiniMarkdown(displayText);
        } else {
            textDiv.textContent = displayText;
        }

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
            notice = { message: String(noticeOrMessage ?? '') };
        }
        return new Promise((resolve) => {
            _prominentNoticeQueue.push({ notice, resolve });
            _drainProminentNoticeQueue();
        });
    }

    mod.showProminentNotice = showProminentNotice;
    window.showProminentNotice = showProminentNotice;

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

    // --- syncFloatingMicButtonState ---
    function syncFloatingMicButtonState(isActive) {
        const managers = [window.live2dManager, window.vrmManager, window.mmdManager];

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

    mod.syncFloatingMicButtonState = syncFloatingMicButtonState;

    // --- syncFloatingScreenButtonState ---
    function syncFloatingScreenButtonState(isActive) {
        const managers = [window.live2dManager, window.vrmManager, window.mmdManager];

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

    mod.hideLive2d = hideLive2d;

    // --- showLive2d ---
    function showLive2d() {
        console.log('[App] showLive2d函数被调用');

        // 检查是否处于"请她离开"状态
        if (window.live2dManager && window.live2dManager._goodbyeClicked) {
            console.log('[App] showLive2d: 当前处于"请她离开"状态，跳过显示逻辑');
            return;
        }

        const container = document.getElementById('live2d-container');
        console.log('[App] showLive2d调用前，容器类列表:', container.classList.toString());

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
        if (floatingButtons) {
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
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                live2dCanvas.style.setProperty('visibility', 'visible', 'important');
                live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
            }
            const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
            if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
                pixiApp.ticker.start();
            }
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

        container.style.transition = 'none';
        container.classList.remove('hidden');
        container.classList.remove('minimized');
        container.style.visibility = 'visible';
        container.style.display = 'block';
        container.style.opacity = '1';
        container.style.transform = 'none';

        if (live2dCanvas) {
            live2dCanvas.style.setProperty('visibility', 'visible', 'important');
            live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
        }

        // 强制浏览器刷新布局
        if (live2dCanvas) {
            void live2dCanvas.offsetWidth;
        }

        container.style.transition = '';

        // 确保 PIXI ticker 在运行
        const pixiApp = window.live2dManager ? window.live2dManager.pixi_app : null;
        if (pixiApp && pixiApp.ticker && !pixiApp.ticker.started) {
            pixiApp.ticker.start();
        }

        // 触发 CSS transition 淡入
        if (live2dCanvas) {
            live2dCanvas.style.transition = 'opacity 0.5s ease-out';
            live2dCanvas.style.opacity = '1';

            window._returnFadeTimer = setTimeout(() => {
                if (live2dCanvas) {
                    live2dCanvas.style.transition = '';
                    live2dCanvas.style.opacity = '';
                }
                // 清除容器的内联 opacity，使 CSS class（如 locked-hover-fade）能正常生效
                container.style.removeProperty('opacity');
                window._returnFadeTimer = null;
            }, 550);
        }

        if (container.classList.length === 0) {
            container.removeAttribute('class');
        }

        console.log('[App] showLive2d调用后，容器类列表:', container.classList.toString());
    }

    mod.showLive2d = showLive2d;

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

            // 解析 live3d 子类型（与 app-character.js 保持一致）
            let effectiveModelType = modelType;
            if (modelType === 'live3d') {
                const _sanitize = v => (typeof v === 'string' && v.trim() && v !== 'undefined' && v !== 'null') ? v : '';
                const mmdPath = _sanitize(catgirlConfig.mmd)
                    || _sanitize(catgirlConfig._reserved?.avatar?.mmd?.model_path)
                    || '';
                const vrmPath = _sanitize(catgirlConfig.vrm)
                    || _sanitize(catgirlConfig._reserved?.avatar?.vrm?.model_path)
                    || '';
                if (mmdPath) {
                    effectiveModelType = 'mmd';
                } else if (vrmPath) {
                    effectiveModelType = 'vrm';
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

                    const vrmCanvasInner = document.getElementById('vrm-canvas');
                    if (!isVrmAlreadyVisible) {
                        if (vrmCanvasInner) {
                            vrmCanvasInner.style.transition = 'none';
                            vrmCanvasInner.style.opacity = '0';
                        }
                    }

                    vrmContainer.style.transition = 'none';
                    vrmContainer.classList.remove('hidden');
                    vrmContainer.classList.remove('minimized');
                    vrmContainer.style.display = 'block';
                    vrmContainer.style.visibility = 'visible';
                    vrmContainer.style.transform = 'none';
                    vrmContainer.style.opacity = '1';
                    vrmContainer.style.removeProperty('pointer-events');

                    void vrmContainer.offsetWidth;
                    vrmContainer.style.transition = '';

                    if (vrmCanvasInner) {
                        vrmCanvasInner.style.setProperty('visibility', 'visible', 'important');
                        vrmCanvasInner.style.setProperty('pointer-events', 'auto', 'important');

                        if (!isVrmAlreadyVisible) {
                            void vrmCanvasInner.offsetWidth;

                            vrmCanvasInner.style.transition = 'opacity 0.5s ease-out';
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
                            window._vrmCanvasFadeInId = setTimeout(cleanupFadeIn, 1000);
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
                    vrmFloatingButtons.style.removeProperty('display');
                    vrmFloatingButtons.style.removeProperty('visibility');
                    vrmFloatingButtons.style.removeProperty('opacity');
                }

                const vrmLockIcon = document.getElementById('vrm-lock-icon');
                if (vrmLockIcon) {
                    vrmLockIcon.style.removeProperty('display');
                    vrmLockIcon.style.removeProperty('visibility');
                    vrmLockIcon.style.removeProperty('opacity');
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
                if (mmdContainer) {
                    mmdContainer.classList.remove('hidden');
                    mmdContainer.style.display = 'block';
                    mmdContainer.style.visibility = 'visible';
                    mmdContainer.style.removeProperty('pointer-events');
                }
                const mmdCanvas = document.getElementById('mmd-canvas');
                if (mmdCanvas) {
                    mmdCanvas.style.setProperty('visibility', 'visible', 'important');
                    mmdCanvas.style.setProperty('pointer-events', 'auto', 'important');
                    // 渐入动画
                    mmdCanvas.style.transition = 'none';
                    mmdCanvas.style.opacity = '0';
                    void mmdCanvas.offsetWidth;
                    mmdCanvas.style.transition = 'opacity 0.5s ease-out';
                    mmdCanvas.style.opacity = '1';
                    if (window._mmdCanvasFadeInId) clearTimeout(window._mmdCanvasFadeInId);
                    window._mmdCanvasFadeInId = setTimeout(() => {
                        if (mmdCanvas) {
                            mmdCanvas.style.transition = '';
                            mmdCanvas.style.opacity = '';
                        }
                        window._mmdCanvasFadeInId = null;
                    }, 600);
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
                    mmdFloatingButtons.style.removeProperty('display');
                    mmdFloatingButtons.style.removeProperty('visibility');
                    mmdFloatingButtons.style.removeProperty('opacity');
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
            showLive2d(); // 出错时默认显示Live2D
        }
    }

    mod.showCurrentModel = showCurrentModel;

    // ================================================================
    //  4. Floating button sync, goodbye/return, event listeners
    //     (app.js lines 6078-6785)
    // ================================================================

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
                if (!micButton.classList.contains('active')) {
                    micButton.click();
                    return;
                }
                if (typeof window.startMicCapture === 'function') {
                    await window.startMicCapture();
                }
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
            // 第零步：在任何状态变更之前立即捕获 goodbye 按钮位置
            // 其他 handler（VRM/MMD goodbyeHandler）可能先于此处执行并隐藏按钮容器，
            // 所以必须在最前面读取位置。
            const _live2dGoodbyeBtn = document.getElementById('live2d-btn-goodbye');
            const _vrmGoodbyeBtn = document.getElementById('vrm-btn-goodbye');
            const _mmdGoodbyeBtn = document.getElementById('mmd-btn-goodbye');
            let savedGoodbyeRect = null;
            for (const btn of [_mmdGoodbyeBtn, _vrmGoodbyeBtn, _live2dGoodbyeBtn]) {
                if (!btn) continue;
                try {
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        savedGoodbyeRect = r;
                        break;
                    }
                } catch (_) { /* ignore */ }
            }
            console.log('[App] 请她离开按钮被点击，savedGoodbyeRect:', savedGoodbyeRect ? `${Math.round(savedGoodbyeRect.left)},${Math.round(savedGoodbyeRect.top)}` : 'null');

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

            // 保存当前锁定状态，以便"请她回来"时恢复
            // core.setLocked() 将值写入 manager.isLocked，因此从 manager 级别读取
            window._savedLockState = {
                live2d: window.live2dManager ? window.live2dManager.isLocked : false,
                vrm: window.vrmManager ? window.vrmManager.isLocked : false,
                mmd: window.mmdManager ? window.mmdManager.isLocked : false
            };

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

            // 不立即隐藏 canvas，先仅禁用交互
            const live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                live2dCanvas.style.setProperty('pointer-events', 'none', 'important');
                console.log('[App] 已禁用 live2d-canvas 交互（pointer-events: none），等待过渡动画完成后再隐藏');
            }

            // 判断当前激活的模型类型
            const vrmContainer = document.getElementById('vrm-container');
            const live2dContainer = document.getElementById('live2d-container');
            const mmdContainer = document.getElementById('mmd-container');
            const isVrmActive = vrmContainer &&
                vrmContainer.style.display !== 'none' &&
                !vrmContainer.classList.contains('hidden');
            const isMmdActive = mmdContainer &&
                mmdContainer.style.display !== 'none' &&
                !mmdContainer.classList.contains('hidden');
            console.log('[App] 判断当前模型类型 - isVrmActive:', isVrmActive, 'isMmdActive:', isMmdActive);

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

            // MMD：禁用交互 + 立即停物理 + 渐隐动画
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
            if (isMmdActive && mmdCanvas) {
                mmdCanvas.style.transition = 'opacity 0.8s ease-out';
                void mmdCanvas.offsetWidth;
                mmdCanvas.style.opacity = '0';
            }

            // 为 VRM 容器添加 minimized 类
            if (isVrmActive && vrmContainer) {
                vrmContainer.style.removeProperty('visibility');
                vrmContainer.style.removeProperty('display');
                vrmContainer.style.removeProperty('opacity');
                vrmContainer.style.removeProperty('transform');
                if (window._vrmCanvasFadeInId) {
                    clearInterval(window._vrmCanvasFadeInId);
                    window._vrmCanvasFadeInId = null;
                }
                const vrmCanvasForHide = document.getElementById('vrm-canvas');
                if (vrmCanvasForHide) {
                    vrmCanvasForHide.style.opacity = '';
                }
                vrmContainer.classList.add('minimized');
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
            }, 1100);

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

            // 显示独立的"请她回来"按钮
            const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
            let vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
            let mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');

            const useMmdReturn = isMmdActive;
            const useVrmReturn = isVrmActive && !isMmdActive;

            // MMD 返回按钮
            if (useMmdReturn && !mmdReturnButtonContainer && window.mmdManager) {
                if (typeof window.mmdManager.setupFloatingButtons === 'function') {
                    window.mmdManager.setupFloatingButtons();
                    mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');
                }
            }
            if (useMmdReturn && mmdReturnButtonContainer) {
                if (savedGoodbyeRect) {
                    const containerWidth = mmdReturnButtonContainer.offsetWidth || 64;
                    const containerHeight = mmdReturnButtonContainer.offsetHeight || 64;
                    const left = Math.round(savedGoodbyeRect.left + (savedGoodbyeRect.width - containerWidth) / 2);
                    const top = Math.round(savedGoodbyeRect.top + (savedGoodbyeRect.height - containerHeight) / 2);
                    mmdReturnButtonContainer.style.right = '';
                    mmdReturnButtonContainer.style.bottom = '';
                    mmdReturnButtonContainer.style.left = `${Math.max(0, Math.min(left, window.innerWidth - containerWidth))}px`;
                    mmdReturnButtonContainer.style.top = `${Math.max(0, Math.min(top, window.innerHeight - containerHeight))}px`;
                    mmdReturnButtonContainer.style.transform = 'none';
                } else {
                    mmdReturnButtonContainer.style.right = '16px';
                    mmdReturnButtonContainer.style.bottom = '116px';
                    mmdReturnButtonContainer.style.left = '';
                    mmdReturnButtonContainer.style.top = '';
                    mmdReturnButtonContainer.style.transform = 'none';
                }
                mmdReturnButtonContainer.style.display = 'flex';
                mmdReturnButtonContainer.style.pointerEvents = 'auto';
            } else if (mmdReturnButtonContainer) {
                mmdReturnButtonContainer.style.display = 'none';
            }

            // 显示Live2D的返回按钮（仅在非VRM/非MMD模式时显示）
            if (!useVrmReturn && !useMmdReturn && live2dReturnButtonContainer) {
                if (savedGoodbyeRect) {
                    const containerWidth = live2dReturnButtonContainer.offsetWidth || 64;
                    const containerHeight = live2dReturnButtonContainer.offsetHeight || 64;
                    const left = Math.round(savedGoodbyeRect.left + (savedGoodbyeRect.width - containerWidth) / 2);
                    const top = Math.round(savedGoodbyeRect.top + (savedGoodbyeRect.height - containerHeight) / 2);
                    live2dReturnButtonContainer.style.right = '';
                    live2dReturnButtonContainer.style.bottom = '';
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
                live2dReturnButtonContainer.style.display = 'none';
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
                if (savedGoodbyeRect) {
                    const containerWidth = vrmReturnButtonContainer.offsetWidth || 64;
                    const containerHeight = vrmReturnButtonContainer.offsetHeight || 64;
                    const left = Math.round(savedGoodbyeRect.left + (savedGoodbyeRect.width - containerWidth) / 2);
                    const top = Math.round(savedGoodbyeRect.top + (savedGoodbyeRect.height - containerHeight) / 2);
                    vrmReturnButtonContainer.style.right = '';
                    vrmReturnButtonContainer.style.bottom = '';
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
                vrmReturnButtonContainer.style.display = 'none';
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
                        iconImg.src = '/static/icons/expand_icon_off.png';
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
            if (resetSessionButton) {
                setTimeout(() => {
                    console.log('[App] 触发 resetSessionButton.click()，当前 goodbyeClicked 状态:', window.live2dManager ? window.live2dManager._goodbyeClicked : 'undefined');
                    resetSessionButton.click();
                }, 10);
            } else {
                console.error('[App] resetSessionButton 未找到！');
            }
        });

        // 请她回来按钮（统一处理函数）
        const handleReturnClick = async (event) => {
            console.log('[App] 请她回来按钮被点击，开始恢复所有界面');

            // 取消延迟隐藏定时器
            if (window._goodbyeHideTimerId) {
                clearTimeout(window._goodbyeHideTimerId);
                window._goodbyeHideTimerId = null;
                console.log('[App] handleReturnClick: 已取消 goodbye 延迟隐藏定时器');
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

            console.log('[App] 标志清除后 - live2dManager._goodbyeClicked:', window.live2dManager?._goodbyeClicked);
            console.log('[App] 标志清除后 - vrmManager._goodbyeClicked:', window.vrmManager?._goodbyeClicked);

            // 隐藏"请她回来"按钮
            const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
            if (live2dReturnButtonContainer) {
                live2dReturnButtonContainer.style.display = 'none';
                live2dReturnButtonContainer.style.pointerEvents = 'none';
            }
            const vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
            if (vrmReturnButtonContainer) {
                vrmReturnButtonContainer.style.display = 'none';
                vrmReturnButtonContainer.style.pointerEvents = 'none';
            }
            const mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');
            if (mmdReturnButtonContainer) {
                mmdReturnButtonContainer.style.display = 'none';
                mmdReturnButtonContainer.style.pointerEvents = 'none';
            }

            // 如果返回按钮被拖拽到新位置，先偏移模型再显示，避免闪烁
            const returnRect = event && event.detail && event.detail.returnButtonRect;
            const savedRect = window._savedGoodbyeRect;
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
                        window.vrmManager.applyScreenDelta(screenDx, screenDy);
                    }
                    if (window.mmdManager && typeof window.mmdManager.applyScreenDelta === 'function') {
                        window.mmdManager.applyScreenDelta(screenDx, screenDy);
                    }
                    if (window.live2dManager) {
                        const liveModel = typeof window.live2dManager.getCurrentModel === 'function'
                            ? window.live2dManager.getCurrentModel() : null;
                        if (liveModel && !liveModel.destroyed) {
                            liveModel.x += screenDx;
                            liveModel.y += screenDy;
                        }
                    }
                }
            }
            window._savedGoodbyeRect = null;

            // 使用 showCurrentModel() 做最终裁决
            try {
                await showCurrentModel();
            } catch (error) {
                console.error('[App] showCurrentModel 失败:', error);
                showLive2d();
            }

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
                vrmLockIcon.style.removeProperty('display');
                vrmLockIcon.style.removeProperty('visibility');
                vrmLockIcon.style.removeProperty('opacity');
            }
            const mmdLockIcon = document.getElementById('mmd-lock-icon');
            if (mmdLockIcon) {
                mmdLockIcon.style.removeProperty('display');
                mmdLockIcon.style.removeProperty('visibility');
                mmdLockIcon.style.removeProperty('opacity');
            }
            // 恢复"请她离开"之前的锁定状态（而非强制解锁）
            const savedLock = window._savedLockState || { live2d: false, vrm: false, mmd: false };
            if (window.live2dManager && typeof window.live2dManager.setLocked === 'function') {
                window.live2dManager.setLocked(savedLock.live2d, { updateFloatingButtons: false });
            }
            if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                window.vrmManager.core.setLocked(savedLock.vrm);
            }
            if (window.mmdManager && window.mmdManager.core && typeof window.mmdManager.core.setLocked === 'function') {
                window.mmdManager.core.setLocked(savedLock.mmd);
            }
            window._savedLockState = null;

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
                vrmFloatingButtons.style.removeProperty('display');
                vrmFloatingButtons.style.removeProperty('visibility');
                vrmFloatingButtons.style.removeProperty('opacity');

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
                mmdFloatingButtons.style.removeProperty('display');
                mmdFloatingButtons.style.removeProperty('visibility');
                mmdFloatingButtons.style.removeProperty('opacity');

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
            const isMobile = typeof window.isMobileWidth === 'function' ? window.isMobileWidth() : (window.innerWidth <= 768);
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
                            buttonData.imgOff.style.opacity = '1';
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
                            buttonData.imgOff.style.opacity = '1';
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
                            buttonData.imgOff.style.opacity = '1';
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
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
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
