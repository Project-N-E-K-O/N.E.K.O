/**
 * app-ui/surface-floating-controls.js
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
    function initFloatingButtonListeners() {
        // DOM refs from orchestrator
        const micButton = I.S.dom.micButton;
        const screenButton = I.S.dom.screenButton;
        const resetSessionButton = I.S.dom.resetSessionButton;
        const muteButton = I.S.dom.muteButton;
        const stopButton = I.S.dom.stopButton;
        const textSendButton = I.S.dom.textSendButton;
        const textInputBox = I.S.dom.textInputBox;
        const screenshotButton = I.S.dom.screenshotButton;

        // 麦克风按钮（toggle模式） — Live2D / VRM 浮动按钮共用
        function isScreenSharingActive() {
            return !!(screenButton && screenButton.classList.contains('active'));
        }

        let screenSharingStartedByVoice = false;

        async function startScreenSharingFromVoiceButton() {
            if (isScreenSharingActive()) {
                return;
            }
            if (typeof window.startScreenSharing !== 'function') {
                console.error('startScreenSharing function not found');
                return;
            }
            await window.startScreenSharing();
            // startScreenSharing handles user cancellation internally, so a
            // resolved Promise alone does not prove capture actually started.
            screenSharingStartedByVoice = isScreenSharingActive();
        }

        async function stopScreenSharingFromVoiceButton() {
            if (!screenSharingStartedByVoice) {
                return;
            }
            if (!isScreenSharingActive()) {
                screenSharingStartedByVoice = false;
                return;
            }
            if (typeof window.stopScreenSharing !== 'function') {
                console.error('stopScreenSharing function not found');
                return;
            }
            await window.stopScreenSharing();
            screenSharingStartedByVoice = false;
        }

        // 「语音时自动共享屏幕」开关：默认关（隐私安全）——只想开麦的用户不会被静默录屏
        // （Electron 下若存过采集源，startScreenSharing 会无提示直接续采）。想要旧的
        // 「语音=顺带共享屏幕」统一体验的人可显式打开；localStorage 持久化，可被设置项同步覆盖。
        function voiceAutoScreenEnabled() {
            try { return localStorage.getItem('neko_voice_auto_screen') === '1'; }
            catch (_) { return false; }
        }
        try {
            window.nekoVoiceAutoScreen = {
                get: voiceAutoScreenEnabled,
                set: function (on) {
                    try { localStorage.setItem('neko_voice_auto_screen', on ? '1' : '0'); } catch (_) {}
                },
            };
        } catch (_) {}

        function waitForVoiceRecordingReady(timeoutMs) {
            const startedAt = Date.now();
            return new Promise((resolve) => {
                const check = () => {
                    if (I.S.isRecording || Date.now() - startedAt >= timeoutMs) {
                        resolve(!!I.S.isRecording);
                        return;
                    }
                    setTimeout(check, 50);
                };
                check();
            });
        }

        window.addEventListener('live2d-mic-toggle', async (e) => {
            if (e.detail.active) {
                if (I.S.isRecording) {
                    // 已在录音：仅按需联动自动共享屏幕
                    if (voiceAutoScreenEnabled()) {
                        await startScreenSharingFromVoiceButton();
                    }
                    return;
                }
                if (I.S.voiceStartPending || window.isMicStarting) {
                    return;
                }
                if (!micButton.classList.contains('active')) {
                    micButton.click();
                    await waitForVoiceRecordingReady(5000);
                } else {
                    // 按钮卡在 active 但未真正录音：清状态后重试
                    micButton.classList.remove('active');
                    micButton.classList.remove('recording');
                    micButton.disabled = false;
                    micButton.click();
                    await waitForVoiceRecordingReady(5000);
                }
                // 仅当用户显式开启「语音时自动共享屏幕」才联动起屏；默认关 = 开麦只开麦。
                if (I.S.isRecording && voiceAutoScreenEnabled()) {
                    await startScreenSharingFromVoiceButton();
                }
            } else {
                // 只清理由本次语音流程自动开启的共享；用户之后即使关掉了自动共享
                // 设置，也仍需释放此前由语音开启的采集。
                await stopScreenSharingFromVoiceButton();
                if (!I.S.isRecording) {
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
                    screenSharingStartedByVoice = false;
                } else {
                    console.error('stopScreenSharing function not found');
                }
            }
        });

        // Agent工具按钮
        window.addEventListener('live2d-agent-click', () => {
            console.log('Agent工具按钮被点击，显示弹出框');
        });

        const SOCIAL_OPEN_DEDUPE_MS = 1200;
        const SOCIAL_OPEN_RELEASE_DELAY_MS = 800;

        function getSocialOpenState() {
            if (!window.__nekoSocialOpenState || typeof window.__nekoSocialOpenState !== 'object') {
                window.__nekoSocialOpenState = {
                    inFlight: false,
                    lastStartedAt: 0,
                    releaseTimer: null
                };
            }
            return window.__nekoSocialOpenState;
        }

        function shouldIgnoreSocialOpenRequest() {
            const now = Date.now();
            const state = getSocialOpenState();
            if (state.inFlight || (now - (state.lastStartedAt || 0)) < SOCIAL_OPEN_DEDUPE_MS) {
                console.debug('[social] duplicate open request ignored');
                return true;
            }
            if (state.releaseTimer) {
                clearTimeout(state.releaseTimer);
                state.releaseTimer = null;
            }
            state.inFlight = true;
            state.lastStartedAt = now;
            return false;
        }

        function releaseSocialOpenRequest() {
            const state = getSocialOpenState();
            if (state.releaseTimer) {
                clearTimeout(state.releaseTimer);
            }
            state.releaseTimer = setTimeout(() => {
                const latestState = getSocialOpenState();
                latestState.inFlight = false;
                latestState.releaseTimer = null;
            }, SOCIAL_OPEN_RELEASE_DELAY_MS);
        }

        // 猫娘网络（社交平台）按钮：占用原 screen 槽位。
        // 从 /api/system/social/config 拿云端 base URL，从 /api/system/client-id 拿 device 身份，
        // 然后在 Electron 内交给系统浏览器打开，避免被 setWindowOpenHandler 拦成桌面 BrowserWindow。
        window.addEventListener('live2d-social-click', async () => {
            if (shouldIgnoreSocialOpenRequest()) {
                return;
            }
            const useExternal = !!(window.electronShell && typeof window.electronShell.openExternal === 'function');
            let popupRef = null;
            const closePopup = () => {
                if (!popupRef) {
                    return;
                }
                try {
                    if (!popupRef.closed) {
                        popupRef.close();
                    }
                } catch (_) { /* ignore */ }
                popupRef = null;
            };
            try {
                if (!useExternal) {
                    // 这里必须先保留 WindowProxy，异步拿到配置/票据后才能导航预开的 tab。
                    // Chromium 在 windowFeatures 里指定 noopener 时可能直接返回 null。
                    popupRef = window.open('about:blank', '_blank');
                    if (!popupRef) {
                        if (typeof window.showStatusToast === 'function') {
                            window.showStatusToast(
                                (window.t && window.t('app.socialOpenFailed', { error: 'popup blocked' }))
                                    || '社交窗口打开失败：请允许弹窗',
                                4000
                            );
                        }
                        return;
                    }
                }
                const cfgRes = await fetch('/api/system/social/config');
                if (!cfgRes.ok) {
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(
                            (window.t && window.t('app.socialUnavailable')) || '社交服务不可用 (config fetch failed)',
                            3000
                        );
                    }
                    closePopup();
                    return;
                }
                const cfg = await cfgRes.json();
                if (cfg && cfg.enabled === false) {
                    if (typeof window.showStatusToast === 'function') {
                        window.showStatusToast(
                            (window.t && window.t('app.socialDisabled')) || '社交服务已禁用',
                            3000
                        );
                    }
                    closePopup();
                    return;
                }
                let url = (cfg && cfg.social_base_url) ? cfg.social_base_url.replace(/\/+$/, '') + '/feed' : null;
                if (!url) {
                    console.warn('[social] no social_base_url from /api/system/social/config');
                    closePopup();
                    return;
                }
                const targetUrl = new URL(url, window.location.href);
                if (targetUrl.protocol !== 'http:' && targetUrl.protocol !== 'https:') {
                    throw new Error('unsupported social URL protocol');
                }
                // 只有从本体按钮打开的页面才能拿到一次性同步票据。票据放 fragment，
                // 不进入社区服务器 access log / Referer；社区页读取后会立即从地址栏移除。
                try {
                    const ticketRes = await fetch('/api/card-drop/sync-ticket', { cache: 'no-store' });
                    if (ticketRes.ok) {
                        const ticketJson = await ticketRes.json();
                        if (ticketJson && ticketJson.sync_ticket) {
                            targetUrl.hash = new URLSearchParams({
                                native_sync: String(ticketJson.sync_ticket)
                            }).toString();
                        }
                    }
                } catch (ticketErr) {
                    console.warn('[social] native session sync ticket fetch failed (non-fatal):', ticketErr);
                }
                // 顺手把 client_id 拼进 URL（仅关联游客身份，不构成登录态同步授权）。
                try {
                    const cidRes = await fetch('/api/system/client-id');
                    if (cidRes.ok) {
                        const cidJson = await cidRes.json();
                        if (cidJson && cidJson.client_id) {
                            targetUrl.searchParams.set('cid', cidJson.client_id);
                        }
                    }
                } catch (cidErr) {
                    console.warn('[social] client_id fetch failed (non-fatal):', cidErr);
                }
                url = targetUrl.toString();
                if (useExternal) {
                    await window.electronShell.openExternal(url);
                    return;
                }
                // about:blank 仍与 opener 同源，可在跨站导航前主动切断反向引用。
                popupRef.opener = null;
                popupRef.location.replace(url);
                try { popupRef.focus && popupRef.focus(); } catch (_) { /* ignore */ }
                popupRef = null;
            } catch (err) {
                closePopup();
                console.error('[social] open failed:', err);
                if (typeof window.showStatusToast === 'function') {
                    window.showStatusToast(
                        (window.t && window.t('app.socialOpenFailed', { error: err.message }))
                            || `社交窗口打开失败：${err.message}`,
                        4000
                    );
                }
            } finally {
                releaseSocialOpenRequest();
            }
        });

        // 睡觉按钮（请她离开）
        window.addEventListener('live2d-goodbye-click', () => {
            const goodbyeTransitionToken = I.reserveNekoModelCatTransition('model-to-cat');
            if (!goodbyeTransitionToken) {
                console.log('[App] 模型/猫切换进行中，忽略本次请她离开点击');
                return;
            }
            // 第零步：在任何状态变更之前立即捕获模型位置。
            // return-ball 会出现在这个位置；后续 return 时也以它作为模型位移基准。
            const savedModelRect = I.getActiveModelTransitionRect();

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
                I.playModelGoodbyeExit(live2dContainerForGoodbye, savedGoodbyeRect);
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
            const goodbyeResourceToken = I.beginGoodbyeResourceSuspend({
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
                I.playModelGoodbyeExit(mmdContainer, savedGoodbyeRect);
            }

            if (isPngtuberActive && pngtuberContainer) {
                pngtuberContainer.style.setProperty('pointer-events', 'none', 'important');
                const pngtuberImage = pngtuberContainer.querySelector('.pngtuber-image');
                if (pngtuberImage) {
                    pngtuberImage.style.setProperty('pointer-events', 'none', 'important');
                }
            }
            if (isPngtuberActive && pngtuberContainer) {
                I.playModelGoodbyeExit(pngtuberContainer, savedGoodbyeRect);
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
                I.playModelGoodbyeExit(vrmContainer, savedGoodbyeRect);
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
                I.completeGoodbyeResourceSuspend(goodbyeResourceToken);
            }, I.NEKO_MODEL_CAT_TRANSITION_DURATION_MS);

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
                activeReturnButtonContainer = I.showReturnBallContainer(mmdReturnButtonContainer, savedGoodbyeRect, { deferReveal: true });
            } else {
                I.hideReturnBallContainer(mmdReturnButtonContainer);
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
                activeReturnButtonContainer = I.showReturnBallContainer(live2dReturnContainer, savedGoodbyeRect, { deferReveal: true });
            } else {
                I.hideReturnBallContainer(live2dReturnContainer);
            }

            if (usePngtuberReturn && !pngtuberReturnButtonContainer && window.pngtuberManager) {
                if (typeof window.pngtuberManager.setupFloatingButtons === 'function') {
                    window.pngtuberManager.setupFloatingButtons();
                    pngtuberReturnButtonContainer = document.getElementById('pngtuber-return-button-container');
                }
            }
            if (usePngtuberReturn && pngtuberReturnButtonContainer) {
                activeReturnButtonContainer = I.showReturnBallContainer(pngtuberReturnButtonContainer, savedGoodbyeRect);
            } else {
                I.hideReturnBallContainer(pngtuberReturnButtonContainer);
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
                activeReturnButtonContainer = I.showReturnBallContainer(vrmReturnButtonContainer, savedGoodbyeRect, { deferReveal: true });
            } else {
                I.hideReturnBallContainer(vrmReturnButtonContainer);
            }

            I.ensureMultiWindowReturnBallDrag(activeReturnButtonContainer);
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
                        if (I.getReturnButtonAppearance(activeReturnButtonContainer) !== I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
                            I.restartNekoModelCatRevealArt(activeReturnButtonContainer);
                        } else {
                            I.applyGoodbyeIdleAppearanceToReturnButton(activeReturnButtonContainer, I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL);
                        }
                        I.revealReturnBallContainer(activeReturnButtonContainer, reason);
                    }
                };
                requestAnimationFrame(() => {
                    if (
                        activeReturnButtonContainer &&
                        activeReturnButtonContainer.isConnected &&
                        activeReturnButtonContainer.style.display !== 'none' &&
                        activeReturnButtonContainer.getAttribute('data-neko-return-visible') === 'true'
                    ) {
                        if (I.getReturnButtonAppearance(activeReturnButtonContainer) === I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
                            I.releaseNekoModelCatTransition(goodbyeTransitionToken);
                            revealActiveReturnBall('return-ball-legacy-ball');
                            return;
                        }
                        const transitionAnchorRect = savedGoodbyeRect || activeReturnButtonContainer.getBoundingClientRect();
                        I.playNekoModelCatTransition({
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
                        I.releaseNekoModelCatTransition(goodbyeTransitionToken);
                    }
                });
            } else {
                I.releaseNekoModelCatTransition(goodbyeTransitionToken);
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
                : I.getVisibleIdleReturnBallContainer();
            if (!container) return;
            if (container.style.display === 'none') {
                I.showReturnBallContainer(container, returnRect);
            }
            I.revealReturnBallContainer(container, 'return-ball-model-viewport-blocked');
        }

        // 请她回来按钮（统一处理函数）
        const handleReturnClick = async (event) => {
            console.log('[App] 请她回来按钮被点击，开始恢复所有界面');
            if (I.isNekoModelCatTransitionActive('model-to-cat')) {
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
            const preReturnViewportReady = await I.ensureModelViewportReadyBeforeShowCurrentModel();
            if (!preReturnViewportReady.ready) {
                console.warn('[App] 请她回来已暂缓：Pet viewport 仍处于猫形态小窗口，保留 return 状态');
                restoreReturnBallAfterBlockedModelViewport(event);
                if (hadPendingGoodbyeReset) {
                    runGoodbyeResetClickIfActive('return-viewport-blocked');
                }
                return;
            }
            const isReturningToPngtuber = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';
            if (I.multiWindowReturnBallDragState) {
                I.multiWindowReturnBallDragState.dragSessionToken += 1;
                I.clearMultiWindowReturnBallDeferredWork(I.multiWindowReturnBallDragState);
            }
            // 同步 window 中的设置值到状态
            if (typeof window.focusModeEnabled !== 'undefined') {
                I.S.focusModeEnabled = window.focusModeEnabled;
                console.log('[App] 同步 focusModeEnabled:', I.S.focusModeEnabled);
            }
            if (typeof window.proactiveChatEnabled !== 'undefined') {
                I.S.proactiveChatEnabled = window.proactiveChatEnabled;
                console.log('[App] 同步 proactiveChatEnabled:', I.S.proactiveChatEnabled);
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
            I.restoreGoodbyeResourceSuspend('return-click');

            // 隐藏"请她回来"按钮
            const live2dReturnButtonContainer = document.getElementById('live2d-return-button-container');
            const vrmReturnButtonContainer = document.getElementById('vrm-return-button-container');
            const mmdReturnButtonContainer = document.getElementById('mmd-return-button-container');
            const pngtuberReturnButtonContainer = document.getElementById('pngtuber-return-button-container');
            I.hideReturnBallContainer(live2dReturnButtonContainer);
            I.hideReturnBallContainer(vrmReturnButtonContainer);
            I.hideReturnBallContainer(mmdReturnButtonContainer);
            I.hideReturnBallContainer(pngtuberReturnButtonContainer);
            I.ensureMultiWindowReturnBallDrag(null);

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
                        I.pendingPngtuberReturnConfig = I.applyPngtuberScreenDelta(screenDx, screenDy);
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
                modelDisplayReady = await I.showCurrentModel();
            } catch (error) {
                console.error('[App] showCurrentModel 失败:', error);
                I.showLive2d();
            }
            if (modelDisplayReady === false) {
                return;
            }

            await I.settleReturnedModelBounds(returnModelWasMoved);

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
            I.S.isSwitchingMode = true;

            // 清除所有语音相关的状态类
            micButton.classList.remove('recording');
            micButton.classList.remove('active');
            screenButton.classList.remove('active');

            // 确保停止录音状态
            I.S.isRecording = false;
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
            I.S.voiceChatActive = false;
            const textInputArea = document.getElementById('text-input-area');
            if (textInputArea) {
                textInputArea.classList.remove('hidden');
            }
            if (typeof window.syncVoiceChatComposerHidden === 'function') {
                window.syncVoiceChatComposerHidden(false);
            }

            // 标记文本会话为非活跃状态
            I.S.isTextSessionActive = false;

            // 显示欢迎消息
            I.showStatusToast(window.t ? window.t('app.welcomeBack', { name: lanlan_config.lanlan_name }) : `\u{1FAF4} ${lanlan_config.lanlan_name}回来了！`, 3000);

            // 恢复主动搭话与主动视觉调度
            try {
                const currentProactiveChat = typeof window.proactiveChatEnabled !== 'undefined'
                    ? window.proactiveChatEnabled
                    : I.S.proactiveChatEnabled;
                const currentProactiveVision = typeof window.proactiveVisionEnabled !== 'undefined'
                    ? window.proactiveVisionEnabled
                    : I.S.proactiveVisionEnabled;

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
                I.S.isSwitchingMode = false;
            }, 500);

            console.log('[App] 请她回来完成，未自动开始会话，等待用户主动发起对话');
        };

        // 同时监听 Live2D、VRM 和 MMD 的回来事件
        window.addEventListener('live2d-return-click', handleReturnClick);
        window.addEventListener('vrm-return-click', handleReturnClick);
        window.addEventListener('mmd-return-click', handleReturnClick);
        window.addEventListener('pngtuber-return-click', handleReturnClick);
    }

    I.mod.initFloatingButtonListeners = initFloatingButtonListeners;

    // ================================================================
    //  5. ensureHiddenElements & final UI init  (app.js lines 11354-11420)
    // ================================================================

    /** Force sidebar/sidebarbox/status to stay hidden. */

    Object.assign(window.appUi, I.mod || {});
})();
