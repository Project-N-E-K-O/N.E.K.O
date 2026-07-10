/**
 * app-interpage.js — Inter-page / cross-tab communication
 *
 * Handles:
 *   - BroadcastChannel setup and message dispatch
 *   - postMessage listeners (memory_edited, model_saved/reload_model)
 *   - Model hot-reload (Live2D / VRM switching)
 *   - UI hide/show commands from other tabs
 *   - Overlay cleanup helpers
 *
 * Dependencies (loaded before this file):
 *   - app-state.js          -> window.appState, window.appConst
 *
 * Runtime dependencies (available by the time handlers fire):
 *   - window.showStatusToast
 *   - window.stopMicCapture   (will be exposed by app.js or future app-mic.js)
 *   - window.clearAudioQueue  (will be exposed by app.js or future app-audio.js)
 *   - window.live2dManager, window.vrmManager
 *   - initLive2DModel / initVRMModel  (global functions from live2d-init.js / vrm-init.js)
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    // const C = window.appConst;  // not used in this module currently
    const MAIN_UI_HIDDEN_BY_MODEL_MANAGER_KEY = '__NEKO_MAIN_UI_HIDDEN_BY_MODEL_MANAGER';
    const ICEBREAKER_BRIDGE_STORAGE_KEY = 'neko_new_user_icebreaker_bridge_event';
    const YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY = 'neko_yui_guide_chat_bridge_queue_v1';

    // =====================================================================
    // Message deduplication (BC + postMessage deliver the same message twice)
    // =====================================================================
    var _processedMsgKeys = {};
    var CROSS_WINDOW_IDLE_ACTIVITY_MIN_INTERVAL_MS = 250;
    var _lastCrossWindowIdleActivityAt = 0;
    var yuiGuideTargetGeometryRegistry = null;
    var yuiGuideBridgeCommandBus = null;

    function createAppInterpageScopedResources() {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createScopedTutorialResources === 'function'
        ) {
            try {
                return window.YuiGuideCommon.createScopedTutorialResources({ window: window });
            } catch (error) {
                console.warn('[YuiGuide] scoped resource helper unavailable; using local fallback:', error);
            }
        }

        var destroyed = false;
        var timers = [];
        var intervals = [];
        var listeners = [];

        function removeFrom(list, value) {
            var index = list.indexOf(value);
            if (index !== -1) {
                list.splice(index, 1);
            }
        }

        function addEventListener(target, type, handler, options) {
            if (destroyed || !target || typeof target.addEventListener !== 'function') {
                return null;
            }
            target.addEventListener(type, handler, options);
            listeners.push({
                target: target,
                type: type,
                handler: handler,
                options: options
            });
            return handler;
        }

        function setScopedTimeout(callback, delayMs) {
            if (destroyed || typeof window.setTimeout !== 'function') {
                return 0;
            }
            var timerId = window.setTimeout(function scopedTimeoutCallback() {
                removeFrom(timers, timerId);
                if (typeof callback === 'function') {
                    callback();
                }
            }, delayMs);
            timers.push(timerId);
            return timerId;
        }

        function clearScopedTimeout(timerId) {
            if (!timerId || typeof window.clearTimeout !== 'function') {
                return;
            }
            removeFrom(timers, timerId);
            window.clearTimeout(timerId);
        }

        function setScopedInterval(callback, delayMs) {
            if (destroyed || typeof window.setInterval !== 'function') {
                return 0;
            }
            var intervalId = window.setInterval(function scopedIntervalCallback() {
                if (typeof callback === 'function') {
                    callback();
                }
            }, delayMs);
            intervals.push(intervalId);
            return intervalId;
        }

        function clearScopedInterval(intervalId) {
            if (!intervalId || typeof window.clearInterval !== 'function') {
                return;
            }
            removeFrom(intervals, intervalId);
            window.clearInterval(intervalId);
        }

        function destroy() {
            destroyed = true;
            timers.slice().forEach(clearScopedTimeout);
            intervals.slice().forEach(clearScopedInterval);
            listeners.splice(0).forEach(function (entry) {
                try {
                    entry.target.removeEventListener(entry.type, entry.handler, entry.options);
                } catch (_) {}
            });
        }

        return {
            addEventListener: addEventListener,
            setTimeout: setScopedTimeout,
            clearTimeout: clearScopedTimeout,
            setInterval: setScopedInterval,
            clearInterval: clearScopedInterval,
            destroy: destroy,
            isDestroyed: function () { return destroyed; }
        };
    }

    var yuiGuideInterpageResources = createAppInterpageScopedResources();
    var yuiGuideChatSpotlightResources = createAppInterpageScopedResources();

    /**
     * Returns true if this action+timestamp was already processed (duplicate).
     * First call for a given key returns false and registers it.
     */
    function isDuplicateMessage(action, timestamp) {
        if (!timestamp) return false;  // no timestamp → cannot deduplicate
        var key = action + '_' + timestamp;
        if (_processedMsgKeys[key]) return true;
        _processedMsgKeys[key] = true;
        setTimeout(function () { delete _processedMsgKeys[key]; }, 5000);
        return false;
    }

    function getYuiGuideBridgeMessageTimestamp(message) {
        var timestamp = Number(message && message.timestamp);
        return Number.isFinite(timestamp) && timestamp > 0 ? timestamp : Date.now();
    }

    function shouldBypassYuiGuideMessageDedup(action, message) {
        return (message && message.bypassDedup === true)
            || action === 'yui_guide_set_chat_spotlight'
            || action === 'yui_guide_set_chat_cursor'
            || action === 'yui_guide_rotate_compact_tool_wheel'
            || action === 'yui_guide_set_chat_buttons_disabled'
            || action === 'yui_guide_set_chat_input_locked'
            || action === 'yui_guide_set_compact_chat_fixed_layout'
            || action === 'yui_guide_set_compact_history_open'
            || action === 'yui_guide_set_avatar_tool_menu_open'
            || action === 'yui_guide_set_compact_tool_fan_open'
            || action === 'yui_guide_set_compact_tool_wheel_index';
    }

    function isHighVolumeBroadcastChannelAction(action) {
        return action === 'idle_return_ball_state'
            || action === 'idle_chat_minimized_state'
            || action === 'idle_chat_compact_surface_state'
            || action === 'idle_cat1_compact_mirror_state'
            || action === 'idle_chat_pair_move_bounds';
    }

    function isMainUIHiddenByModelManager() {
        return window[MAIN_UI_HIDDEN_BY_MODEL_MANAGER_KEY] === true;
    }

    function ensureMainUIHiddenStyle() {
        if (document.getElementById('neko-main-ui-hidden-by-model-manager-style')) return;
        var style = document.createElement('style');
        style.id = 'neko-main-ui-hidden-by-model-manager-style';
        style.textContent = [
            'body.neko-main-ui-hidden-by-model-manager #live2d-container,',
            'body.neko-main-ui-hidden-by-model-manager #vrm-container,',
            'body.neko-main-ui-hidden-by-model-manager #mmd-container,',
            'body.neko-main-ui-hidden-by-model-manager #pngtuber-container,',
            'body.neko-main-ui-hidden-by-model-manager #pngtuber-container .pngtuber-image,',
            'body.neko-main-ui-hidden-by-model-manager #live2d-canvas,',
            'body.neko-main-ui-hidden-by-model-manager #vrm-canvas,',
            'body.neko-main-ui-hidden-by-model-manager #mmd-canvas,',
            'body.neko-main-ui-hidden-by-model-manager #react-chat-window-overlay,',
            'body.neko-main-ui-hidden-by-model-manager #live2d-floating-buttons,',
            'body.neko-main-ui-hidden-by-model-manager #vrm-floating-buttons,',
            'body.neko-main-ui-hidden-by-model-manager #mmd-floating-buttons,',
            'body.neko-main-ui-hidden-by-model-manager #pngtuber-floating-buttons,',
            'body.neko-main-ui-hidden-by-model-manager #live2d-lock-icon,',
            'body.neko-main-ui-hidden-by-model-manager #vrm-lock-icon,',
            'body.neko-main-ui-hidden-by-model-manager #mmd-lock-icon,',
            'body.neko-main-ui-hidden-by-model-manager #pngtuber-lock-icon,',
            'body.neko-main-ui-hidden-by-model-manager #live2d-return-button-container,',
            'body.neko-main-ui-hidden-by-model-manager #vrm-return-button-container,',
            'body.neko-main-ui-hidden-by-model-manager #mmd-return-button-container,',
            'body.neko-main-ui-hidden-by-model-manager #pngtuber-return-button-container {',
            '  display: none !important;',
            '  visibility: hidden !important;',
            '  pointer-events: none !important;',
            '}'
        ].join('\n');
        (document.head || document.documentElement).appendChild(style);
    }

    function setMainUIHiddenByModelManager(hidden) {
        window[MAIN_UI_HIDDEN_BY_MODEL_MANAGER_KEY] = !!hidden;
        ensureMainUIHiddenStyle();
        if (document.body) {
            document.body.classList.toggle('neko-main-ui-hidden-by-model-manager', !!hidden);
        }
        try {
            window.dispatchEvent(new CustomEvent('neko:main-ui-hidden-by-model-manager-changed', {
                detail: { hidden: !!hidden }
            }));
        } catch (_) {}
    }

    function applyTutorialChatIdentityOverride(payload) {
        var detail = payload || {};
        if (detail.active) {
            window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__ = {
                active: true,
                displayName: detail.displayName || 'YUI',
                avatarDataUrl: detail.avatarDataUrl || '',
                modelType: detail.modelType || ''
            };
            window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__ = detail.displayName || 'YUI';
            if (window.appChatAvatar && typeof window.appChatAvatar.setTutorialAvatarOverride === 'function') {
                window.appChatAvatar.setTutorialAvatarOverride(detail.avatarDataUrl || '', detail.modelType || '');
            } else {
                window.__nekoPendingTutorialChatIdentity = {
                    active: true,
                    avatarDataUrl: detail.avatarDataUrl || '',
                    modelType: detail.modelType || ''
                };
            }
        } else {
            delete window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__;
            delete window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__;
            if (window.appChatAvatar && typeof window.appChatAvatar.clearTutorialAvatarOverride === 'function') {
                window.appChatAvatar.clearTutorialAvatarOverride();
            } else {
                window.__nekoPendingTutorialChatIdentity = { active: false };
            }
        }
        window.dispatchEvent(new CustomEvent('neko:tutorial-chat-identity-changed', {
            detail: {
                active: !!detail.active,
                displayName: detail.displayName || '',
                avatarDataUrl: detail.avatarDataUrl || '',
                modelType: detail.modelType || ''
            }
        }));
    }

    // =====================================================================
    // Overlay cleanup helpers
    // =====================================================================

    /**
     * Remove Live2D overlay UI elements (floating buttons, lock icon, etc.)
     */
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
            .forEach(function (el) {
                if (window._removeNekoFloatingButtonsElement) {
                    window._removeNekoFloatingButtonsElement(el);
                } else {
                    el.remove();
                }
            });
    }

    /**
     * Remove VRM overlay UI elements.
     */
    function cleanupVRMOverlayUI() {
        if (window.vrmManager && typeof window.vrmManager.cleanupUI === 'function') {
            window.vrmManager.cleanupUI();
            return;
        }
        document.querySelectorAll('#vrm-floating-buttons, #vrm-lock-icon, #vrm-return-button-container')
            .forEach(function (el) {
                if (window._removeNekoFloatingButtonsElement) {
                    window._removeNekoFloatingButtonsElement(el);
                } else {
                    el.remove();
                }
            });
    }

    /**
     * Remove MMD overlay UI elements.
     */
    function cleanupMMDOverlayUI() {
        if (window.mmdManager && typeof window.mmdManager.cleanupFloatingButtons === 'function') {
            window.mmdManager.cleanupFloatingButtons();
            return;
        }
        document.querySelectorAll('#mmd-floating-buttons, #mmd-lock-icon, #mmd-return-button-container')
            .forEach(function (el) {
                if (window._removeNekoFloatingButtonsElement) {
                    window._removeNekoFloatingButtonsElement(el);
                } else {
                    el.remove();
                }
            });
    }

    function cleanupPNGTuberOverlayUI() {
        if (window.pngtuberManager && typeof window.pngtuberManager.cleanupFloatingButtons === 'function') {
            window.pngtuberManager.cleanupFloatingButtons();
            return;
        }
        document.querySelectorAll('#pngtuber-floating-buttons, #pngtuber-lock-icon, #pngtuber-return-button-container')
            .forEach(function (el) {
                if (window._removeNekoFloatingButtonsElement) {
                    window._removeNekoFloatingButtonsElement(el);
                } else {
                    el.remove();
                }
            });
    }

    function markMMDCanvasLoadingSession(canvas, loadingSessionId) {
        if (!canvas) return;
        canvas.dataset.mmdLoadingSessionId = String(loadingSessionId);
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
    }

    function restoreMMDCanvasForLoadingSession(canvas, loadingSessionId) {
        if (!canvas) return false;
        if (canvas.dataset.mmdLoadingSessionId !== String(loadingSessionId)) {
            return false;
        }
        delete canvas.dataset.mmdLoadingSessionId;
        canvas.style.visibility = 'visible';
        canvas.style.pointerEvents = 'auto';
        return true;
    }

    function isMMDLoadingSessionActive(canvas, loadingSessionId) {
        return !!canvas && canvas.dataset.mmdLoadingSessionId === String(loadingSessionId);
    }

    function clearMMDCanvasLoadingSession(canvas) {
        if (!canvas) return;
        delete canvas.dataset.mmdLoadingSessionId;
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
    }

    // =====================================================================
    // Shared: memory-edited session reset logic
    // =====================================================================

    /**
     * Common handler for memory_edited events (used by both BroadcastChannel
     * and postMessage code paths).
     *
     * @param {string} catgirlName  - name of the character whose memory was edited
     */
    async function handleMemoryEdited(catgirlName) {
        console.log(
            window.t('console.memoryEditedRefreshContext'),
            catgirlName
        );

        // Was the user in voice mode before the edit?
        var wasRecording = S.isRecording;

        // Stop current mic capture
        if (S.isRecording && typeof window.stopMicCapture === 'function') {
            window.stopMicCapture();
        }

        // Tell backend to drop old context
        if (S.socket && S.socket.readyState === WebSocket.OPEN) {
            S.socket.send(JSON.stringify({ action: 'end_session' }));
            console.log('[Memory] 已向后端发送 end_session');
        }

        // Reset text session so next message reloads context
        if (S.isTextSessionActive) {
            S.isTextSessionActive = false;
            console.log('[Memory] 文本会话已重置，下次发送将重新加载上下文');
        }

        // Stop any playing AI audio (wait for decoder reset to avoid races)
        if (typeof window.clearAudioQueue === 'function') {
            try {
                await window.clearAudioQueue();
            } catch (e) {
                console.error('[Memory] clearAudioQueue 失败:', e);
            }
        }

        // If was in voice mode, wait for session teardown then re-connect
        if (wasRecording) {
            window.showStatusToast(
                window.t ? window.t('memory.refreshingContext') : '正在刷新上下文...',
                3000
            );
            // Wait for backend session to fully end
            await new Promise(function (resolve) { setTimeout(resolve, 1500); });
            // Trigger full startup flow via micButton click
            try {
                var micButton = document.getElementById('micButton');
                if (micButton) micButton.click();
            } catch (e) {
                console.error('[Memory] 自动重连语音失败:', e);
            }
        } else {
            window.showStatusToast(
                window.t ? window.t('memory.refreshed') : '记忆已更新，下次对话将使用新记忆',
                4000
            );
        }
    }

    // =====================================================================
    // Model hot-reload
    // =====================================================================

    /**
     * Capability check: does the current page host the full model UI?
     * index.html (served at / and /{lanlan_name}) has live2d-container,
     * vrm-container AND mmd-container. Other pages (chat, subtitle, etc.)
     * lack the complete set and must not run model reload / hide / show.
     */
    function _isModelHostPage() {
        return !!(document.getElementById('live2d-container')
              && document.getElementById('vrm-container')
              && document.getElementById('mmd-container'));
    }

    async function _waitForLive2DManagerIdle(timeoutMs) {
        var manager = window.live2dManager;
        if (!manager || !manager._isLoadingModel) {
            return;
        }

        var waitMs = Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 30000;
        var startedAt = Date.now();
        console.log('[Model] Live2D 模型仍在加载，等待空闲后继续热切换');

        while (manager && manager._isLoadingModel) {
            if (Date.now() - startedAt >= waitMs) {
                console.warn('[Model] 等待 Live2D 模型加载空闲超时，继续尝试热切换');
                return;
            }
            await new Promise(function (resolve) {
                setTimeout(resolve, 80);
            });
            manager = window.live2dManager;
        }
    }

    /**
     * Handle model hot-swap triggered from another tab (model_manager).
     *
     * Concurrency-safe: if a reload is already in flight, the new request
     * is queued and executed once the current one finishes.
     *
     * @param {string} [targetLanlanName='']  - optional character name filter
     * @param {object} [reloadOptions]        - runtime-only reload options
     */
    async function handleModelReload(targetLanlanName, reloadOptions) {
        targetLanlanName = targetLanlanName || '';
        reloadOptions = reloadOptions || {};
        var temporaryConfig = reloadOptions.temporaryConfig && typeof reloadOptions.temporaryConfig === 'object'
            ? reloadOptions.temporaryConfig
            : null;
        var suppressToast = !!reloadOptions.suppressToast;
        var skipIdleRestore = !!reloadOptions.skipIdleRestore;
        var skipPersistentExpressions = !!reloadOptions.skipPersistentExpressions;
        var deferRevealPrepared = !!reloadOptions.deferRevealPrepared;
        var throwOnError = !!reloadOptions.throwOnError;
        var reloadKey = JSON.stringify({
            lanlan_name: targetLanlanName,
            temporaryConfig: temporaryConfig || null,
            skipIdleRestore: skipIdleRestore,
            skipPersistentExpressions: skipPersistentExpressions,
            deferRevealPrepared: deferRevealPrepared
        });

        if (window._lastModelReloadKey === reloadKey && Date.now() - (window._lastModelReloadAt || 0) < 1000) {
            console.log('[Model] 忽略短时间内重复的模型重载请求');
            return window._lastModelReloadResult;
        }

        // 只有承载完整模型 UI 的页面才处理重载；Chat 等子窗口缺少渲染容器，
        // 执行会导致异常并弹出误导性的"模型切换失败"toast。
        if (!_isModelHostPage()) {
            console.log('[Model] 当前页面无模型容器，跳过模型重载');
            return;
        }

        // If the message targets a different character, ignore it
        var currentLanlanName = window.lanlan_config?.lanlan_name || '';
        if (targetLanlanName && currentLanlanName && targetLanlanName !== currentLanlanName) {
            console.log('[Model] 忽略来自其它角色的模型重载请求:', { targetLanlanName: targetLanlanName, currentLanlanName: currentLanlanName });
            return;
        }

        // Concurrency: wait if another reload is in-flight
        if (window._modelReloadInFlight) {
            console.log('[Model] 模型重载已在进行中，等待完成后重试');
            if (window._modelReloadKey === reloadKey) {
                console.log('[Model] 模型重载已在进行，复用当前重载请求');
                return window._modelReloadPromise;
            }
            console.log('[Model] 模型重载已在进行，记录最后一次不同的重载请求');
            var pendingResolve;
            var pendingReject;
            var pendingPromise = new Promise(function (resolve, reject) {
                pendingResolve = resolve;
                pendingReject = reject;
            });
            if (window._pendingModelReload && typeof window._pendingModelReload.resolve === 'function') {
                window._pendingModelReload.resolve(false);
            }
            window._pendingModelReload = {
                targetLanlanName: targetLanlanName,
                reloadOptions: reloadOptions,
                resolve: pendingResolve,
                reject: pendingReject
            };
            return pendingPromise;
        }

        // Mark in-flight
        window._modelReloadInFlight = true;
        window._modelReloadKey = reloadKey;
        window._pendingModelReload = null;

        var resolveReload;
        window._modelReloadPromise = new Promise(function (resolve) {
            resolveReload = resolve;
        });

        console.log('[Model] 开始热切换模型');
        let mmdRequestSessionId = '';
        let activeMmdLoadingSessionId = '';
        var reloadSucceeded = false;

        function ensureLive2DRenderActive(reason) {
            try {
                var preserveAvatarCornerPeekOpacity = window.nekoYuiGuideAvatarCornerPeekActive === true;
                var manager = window.live2dManager || null;
                var app = manager && manager.pixi_app;
                var ticker = app && app.ticker;
                var currentModel = manager && typeof manager.getCurrentModel === 'function'
                    ? manager.getCurrentModel()
                    : manager && manager.currentModel;

                if (currentModel) {
                    currentModel.visible = true;
                    if (!preserveAvatarCornerPeekOpacity) {
                        currentModel.alpha = 1;
                    }
                    if (currentModel.renderable !== undefined) {
                        currentModel.renderable = true;
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
                if (app && app.renderer && typeof app.renderer.render === 'function' && app.stage) {
                    app.renderer.render(app.stage);
                }
            } catch (error) {
                console.warn('[Model] Live2D render activation failed:', reason || 'unknown', error);
            }
        }

        function scheduleLive2DRenderActivation(reason) {
            [80, 300].forEach(function (delayMs) {
                window.setTimeout(function () {
                    ensureLive2DRenderActive((reason || 'model-reload-live2d') + ':delay-' + delayMs);
                }, delayMs);
            });
        }

        try {
            // 1. Re-fetch page config, or use a caller-provided temporary runtime config.
            var nameForConfig = targetLanlanName || currentLanlanName;
            var data;
            if (temporaryConfig) {
                data = Object.assign({ success: true }, temporaryConfig);
            } else {
                var pageConfigUrl = nameForConfig
                    ? '/api/config/page_config?lanlan_name=' + encodeURIComponent(nameForConfig)
                    : '/api/config/page_config';
                var response = await fetch(pageConfigUrl);
                data = await response.json();
            }

            if (data.success) {
                var newModelPath = data.model_path || '';
                var newModelType = (data.model_type || 'live2d').toLowerCase();
                var live3dSubType = (data.live3d_sub_type || '').toLowerCase();
                var oldModelType = window.lanlan_config?.model_type || 'live2d';
                var nextLighting = (temporaryConfig && !Object.prototype.hasOwnProperty.call(data, 'lighting'))
                    ? (window.lanlan_config?.lighting || null)
                    : ((data.lighting && typeof data.lighting === 'object')
                        ? Object.assign({}, data.lighting)
                        : null);

                window.lanlan_config = window.lanlan_config || {};
                window.lanlan_config.lighting = nextLighting;

                console.log('[Model] 模型切换:', {
                    oldType: oldModelType,
                    newType: newModelType,
                    newPath: newModelPath
                });

                // Empty model path -> fall back to default for VRM/Live3D-VRM
                if (!newModelPath) {
                    if (newModelType === 'vrm' || (newModelType === 'live3d' && live3dSubType === 'vrm')) {
                        newModelPath = '/static/vrm/sister1.0.vrm';
                        console.info('[Model] VRM模型路径为空，使用默认模型:', newModelPath);
                    } else {
                        console.warn('[Model] 模型路径为空，仍然执行模型类型切换');
                    }
                }

                // Cross-type switch: clean up the old overlay
                var oldLive3dSubType = (window.lanlan_config?.live3d_sub_type || '').toLowerCase();
                var typeChanged = oldModelType !== newModelType ||
                    (newModelType === 'live3d' && oldLive3dSubType !== live3dSubType);

                // 提前更新 config，防止异步间隙中其他代码基于过时类型重建按钮
                if (typeChanged && window.lanlan_config) {
                    window.lanlan_config.model_type = newModelType;
                    window.lanlan_config.live3d_sub_type = live3dSubType;
                }

                if (typeChanged) {
                    if (oldModelType === 'live2d') cleanupLive2DOverlayUI();
                    if (oldModelType === 'vrm') cleanupVRMOverlayUI();
                    if (oldModelType === 'live3d') {
                        cleanupVRMOverlayUI();
                        cleanupMMDOverlayUI();
                    }
                    if (oldModelType === 'pngtuber') {
                        if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
                            window.pngtuberManager.hide();
                        }
                        cleanupPNGTuberOverlayUI();
                        var oldPngtuberContainer = document.getElementById('pngtuber-container');
                        if (oldPngtuberContainer) {
                            oldPngtuberContainer.style.display = 'none';
                            oldPngtuberContainer.style.visibility = 'hidden';
                            oldPngtuberContainer.classList.add('hidden');
                            oldPngtuberContainer.style.pointerEvents = 'none';
                        }
                    }
                }

                // 3. Switch based on model type
                if (newModelType === 'vrm' || (newModelType === 'live3d' && live3dSubType === 'vrm')) {
                    window.vrmModel = newModelPath;
                    window.cubism4Model = '';

                    // Hide Live2D
                    console.log('[Model] 隐藏 Live2D 模型');
                    var live2dContainer = document.getElementById('live2d-container');
                    if (live2dContainer) {
                        live2dContainer.style.display = 'none';
                        live2dContainer.classList.add('hidden');
                    }

                    // Hide MMD
                    var mmdContainer = document.getElementById('mmd-container');
                    if (mmdContainer) {
                        mmdContainer.style.display = 'none';
                        mmdContainer.classList.add('hidden');
                    }
                    var mmdCanvas = document.getElementById('mmd-canvas');
                    if (mmdCanvas) {
                        mmdCanvas.style.visibility = 'hidden';
                        mmdCanvas.style.pointerEvents = 'none';
                    }
                    if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                        window.mmdManager.pauseRendering();
                    }
                    if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                        window.live2dManager.pauseRendering();
                    }
                    // 清空 Live2D 画布残留像素，避免透明窗口穿透
                    if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.renderer) {
                        window.live2dManager.pixi_app.renderer.clear();
                    }

                    // Show & reload VRM
                    console.log('[Model] 加载 VRM 模型:', newModelPath);
                    var vrmContainer = document.getElementById('vrm-container');
                    if (vrmContainer) {
                        vrmContainer.classList.remove('hidden');
                        vrmContainer.style.display = 'block';
                        vrmContainer.style.visibility = 'visible';
                        vrmContainer.style.removeProperty('pointer-events');
                    }

                    var vrmCanvas = document.getElementById('vrm-canvas');
                    if (vrmCanvas) {
                        vrmCanvas.style.visibility = 'visible';
                        vrmCanvas.style.pointerEvents = 'auto';
                    }

                    // Ensure VRM manager is initialised
                    if (!window.vrmManager) {
                        console.log('[Model] VRM 管理器未初始化，等待初始化完成');
                        if (typeof initVRMModel === 'function') {
                            await initVRMModel();
                        }
                    }

                    // Load the new model
                    if (window.vrmManager) {
                        // 【关键修复】确保容器和 canvas 存在，并恢复 Three.js 场景可见性。
                        // 角色切换的清理逻辑会将 renderer.domElement 设为 display:none，
                        // 而 loadModel 内部在 scene/camera/renderer 已存在时不会调用
                        // ensureThreeReady（也就不会恢复 canvas 可见性），导致从 Live2D
                        // 切换到 VRM 时模型加载成功但不可见。
                        // initThreeJS 在已初始化时是幂等的，但会无条件恢复容器/canvas 可见性。
                        {
                            var vrmContainerEl = document.getElementById('vrm-container');
                            if (vrmContainerEl && !vrmContainerEl.querySelector('canvas')) {
                                var newCanvas = document.createElement('canvas');
                                newCanvas.id = 'vrm-canvas';
                                vrmContainerEl.appendChild(newCanvas);
                            }
                        }
                        await window.vrmManager.initThreeJS('vrm-canvas', 'vrm-container', nextLighting);

                        // 停止旧的待机轮换
                        if (typeof window._stopVrmIdleRotation === 'function') window._stopVrmIdleRotation();
                        if (typeof window._stopMmdIdleRotation === 'function') window._stopMmdIdleRotation();

                        // 【修复】在 loadModel 之前获取角色待机动作列表，
                        // 更新 lanlan_config 使 loadModel 内部读取到正确的待机动作 URL，
                        // 避免使用初始页面加载时的过时值导致动画加载失败进入 T-pose。
                        // 先清空旧值，确保 fetch 失败时 loadModel 回退到安全的硬编码默认值
                        // 而非残留的上一个角色的待机动作 URL。
                        var vrmIdleList = [];
                        window.lanlan_config.vrmIdleAnimation = '';
                        window.lanlan_config.vrmIdleAnimations = [];
                        if (nameForConfig) {
                            try {
                                var charResVrm = await fetch('/api/characters');
                                if (charResVrm.ok) {
                                    var charDataVrm = await charResVrm.json();
                                    var catDataVrm = charDataVrm?.['猫娘']?.[nameForConfig];
                                    // 【修复】兼容新旧版字段，穿透 _reserved 读取 VRM 待机动作
                                    var rawVrmIdle = catDataVrm?._reserved?.avatar?.vrm?.idle_animation
                                                  || catDataVrm?.idle_animation
                                                  || catDataVrm?.idleAnimations
                                                  || catDataVrm?.idleAnimation;
                                    vrmIdleList = Array.isArray(rawVrmIdle) ? rawVrmIdle : (rawVrmIdle ? [rawVrmIdle] : []);
                                    window.lanlan_config.vrmIdleAnimation = vrmIdleList[0] || '';
                                    window.lanlan_config.vrmIdleAnimations = vrmIdleList;
                                }
                            } catch (e) {
                                console.warn('[Model] 获取VRM待机动作列表失败:', e);
                            }
                        }

                        await window.vrmManager.loadModel(newModelPath);

                        // 启动待机动作轮换（多个动作时自动切换）
                        if (vrmIdleList.length > 0 && typeof window._startVrmIdleRotation === 'function') {
                            window._startVrmIdleRotation(vrmIdleList);
                        }

                        // 重新应用打光/曝光/描边；若角色未保存自定义光照，则回退到默认值，避免沿用上一个角色的灯光状态。
                        var effectiveLighting = window.lanlan_config?.lighting || window.VRM_DEFAULT_LIGHTING || null;
                        if (effectiveLighting && typeof window.applyVRMLighting === 'function') {
                            window.applyVRMLighting(effectiveLighting, window.vrmManager);
                            if (typeof window.applyVRMOutlineWidth === 'function') {
                                var currentModelRef = window.vrmManager?.currentModel;
                                var outlineScale = effectiveLighting.outlineWidthScale;
                                requestAnimationFrame(function () {
                                    if (window.vrmManager?.currentModel !== currentModelRef) {
                                        return;
                                    }
                                    if (outlineScale !== undefined) {
                                        window.applyVRMOutlineWidth(outlineScale, window.vrmManager);
                                    }
                                });
                            }
                        }

                        // 重启 UI 更新循环（被 handleHideMainUI 停止）。
                        // handleShowMainUI 在 _modelReloadInFlight 为 true 时会跳过，
                        // 因此必须在模型加载完成后手动重启，否则悬浮按钮不会重新出现。
                        if (window.vrmManager && window.vrmManager._uiUpdateLoopId == null
                            && typeof window.vrmManager._startUIUpdateLoop === 'function') {
                            window.vrmManager._snapUIPosition = true;
                            window.vrmManager._startUIUpdateLoop();
                        }
                    } else {
                        console.error('[Model] VRM 管理器初始化失败');
                    }
                } else if (newModelType === 'live3d' && live3dSubType === 'mmd') {
                    // MMD mode (Live3D sub-type)
                    window.cubism4Model = '';
                    window.vrmModel = '';

                    // Hide Live2D
                    console.log('[Model] 隐藏 Live2D 模型');
                    var live2dContainerMmd = document.getElementById('live2d-container');
                    if (live2dContainerMmd) {
                        live2dContainerMmd.style.display = 'none';
                        live2dContainerMmd.classList.add('hidden');
                    }

                    // Hide VRM
                    var vrmContainerMmd = document.getElementById('vrm-container');
                    if (vrmContainerMmd) {
                        vrmContainerMmd.style.display = 'none';
                        vrmContainerMmd.classList.add('hidden');
                    }
                    var vrmCanvasMmd = document.getElementById('vrm-canvas');
                    if (vrmCanvasMmd) {
                        vrmCanvasMmd.style.visibility = 'hidden';
                        vrmCanvasMmd.style.pointerEvents = 'none';
                    }
                    if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                        window.vrmManager.pauseRendering();
                    }
                    if (window.vrmManager && window.vrmManager.renderer) {
                        window.vrmManager.renderer.clear();
                    }
                    if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                        window.live2dManager.pauseRendering();
                    }
                    if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.renderer) {
                        window.live2dManager.pixi_app.renderer.clear();
                    }

                    // Show MMD container
                    console.log('[Model] 加载 MMD 模型:', newModelPath);
                    var mmdContainerShow = document.getElementById('mmd-container');
                    if (mmdContainerShow) {
                        mmdContainerShow.classList.remove('hidden');
                        mmdContainerShow.style.display = 'block';
                        mmdContainerShow.style.visibility = 'visible';
                        mmdContainerShow.style.removeProperty('pointer-events');
                    }
                    var mmdCanvasShow = document.getElementById('mmd-canvas');
                    const loadingSessionId = window._createMMDLoadingSessionId
                        ? window._createMMDLoadingSessionId('mmd-interpage')
                        : `mmd-interpage-${Date.now()}`;
                    if (mmdCanvasShow) {
                        // 先隐藏 canvas，避免旧帧或加载中的模型透过半透明 overlay 露出。
                        markMMDCanvasLoadingSession(mmdCanvasShow, loadingSessionId);
                    }
                    mmdRequestSessionId = loadingSessionId;
                    activeMmdLoadingSessionId = loadingSessionId;
                    window.MMDLoadingOverlay?.begin(loadingSessionId, { stage: 'engine' });

                    // Ensure MMD manager is initialised
                    if (!window.mmdManager) {
                        console.log('[Model] MMD 管理器未初始化，等待初始化完成');
                        if (typeof initMMDModel === 'function') {
                            const initializedManager = await initMMDModel();
                            if (!initializedManager || !window.mmdManager || window.mmdManager._isDisposed) {
                                throw new Error('MMD 管理器初始化失败');
                            }
                        }
                    }

                    // Load MMD model
                    if (window.mmdManager) {
                        // 提前获取设置并预置物理开关
                        let savedSettings = null;
                        try {
                            window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'settings' });
                            var settingsRes = await fetch('/api/characters/catgirl/' + encodeURIComponent(nameForConfig) + '/mmd_settings');
                            var settingsData = await settingsRes.json();
                            if (settingsData.success && settingsData.settings) {
                                savedSettings = settingsData.settings;
                                if (savedSettings.physics?.enabled != null) {
                                    window.mmdManager.enablePhysics = !!savedSettings.physics.enabled;
                                }
                            }
                        } catch (settingsErr) {
                            console.warn('[Model] 获取MMD设置失败:', settingsErr);
                        }
                        // 停止旧的待机轮换
                        if (typeof window._stopVrmIdleRotation === 'function') window._stopVrmIdleRotation();
                        if (typeof window._stopMmdIdleRotation === 'function') window._stopMmdIdleRotation();

                        window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'model' });
                        await window.mmdManager.loadModel(newModelPath, { loadingSessionId });

                        // 应用完整设置（光照、渲染、物理、鼠标跟踪）
                        if (savedSettings) {
                            window.mmdManager.applySettings(savedSettings);
                        }

                        // 播放待机动作 & 启动轮换
                        if (nameForConfig) {
                            try {
                                const charRes = await fetch('/api/characters');
                                if (charRes.ok) {
                                    const charData = await charRes.json();
                                    const catData = charData?.['猫娘']?.[nameForConfig];
                                    // 【修复】兼容新旧版字段，穿透 _reserved 读取 MMD 待机动作
                                    let rawMmdIdle = catData?._reserved?.avatar?.mmd?.idle_animation
                                                  || catData?.mmd_idle_animations
                                                  || catData?.mmd_idle_animation;
                                    let idleList = Array.isArray(rawMmdIdle) ? rawMmdIdle : (rawMmdIdle ? [rawMmdIdle] : []);
                                    if (idleList.length > 0) {
                                        try {
                                            window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'idle' });
                                            await window.mmdManager.loadAnimation(idleList[0]);
                                            window.mmdManager.playAnimation();
                                            console.log('[Model] 已播放待机动作:', idleList[0]);
                                            if (typeof window._startMmdIdleRotation === 'function') {
                                                window._startMmdIdleRotation(idleList);
                                            }
                                        } catch (idleErr) {
                                            console.warn('[Model] 播放待机动作失败:', idleErr);
                                        }
                                    }
                                }
                            } catch (idleErr) {
                                console.warn('[Model] 获取角色待机动作失败:', idleErr);
                            }
                        }
                        window.MMDLoadingOverlay?.update(loadingSessionId, { stage: 'done' });
                        if (window._waitForMMDRenderFrame) {
                            await window._waitForMMDRenderFrame(window.mmdManager);
                        }
                        var mmdCanvasReady = document.getElementById('mmd-canvas');
                        if (mmdRequestSessionId === loadingSessionId && isMMDLoadingSessionActive(mmdCanvasReady, loadingSessionId)) {
                            window.MMDLoadingOverlay?.end(loadingSessionId);
                            restoreMMDCanvasForLoadingSession(mmdCanvasReady, loadingSessionId);
                            mmdRequestSessionId = '';
                            activeMmdLoadingSessionId = '';
                        }

                        // 重启 UI 更新循环（被 handleHideMainUI 停止）。
                        // handleShowMainUI 在 _modelReloadInFlight 为 true 时会跳过，
                        // 因此必须在模型加载完成后手动重启，否则悬浮按钮不会重新出现。
                        if (window.mmdManager && window.mmdManager._uiUpdateLoopId == null
                            && typeof window.mmdManager._startUIUpdateLoop === 'function') {
                            window.mmdManager._snapUIPosition = true;
                            window.mmdManager._startUIUpdateLoop();
                        }
                    } else {
                        console.error('[Model] MMD 管理器初始化失败');
                        throw new Error('MMD 管理器初始化失败');
                    }
                } else if (newModelType === 'pngtuber') {
                    window.cubism4Model = '';
                    window.vrmModel = '';
                    window.mmdModel = '';

                    var live2dContainerPng = document.getElementById('live2d-container');
                    if (live2dContainerPng) {
                        live2dContainerPng.style.display = 'none';
                        live2dContainerPng.classList.add('hidden');
                    }
                    var vrmContainerPng = document.getElementById('vrm-container');
                    if (vrmContainerPng) {
                        vrmContainerPng.style.display = 'none';
                        vrmContainerPng.classList.add('hidden');
                    }
                    var mmdContainerPng = document.getElementById('mmd-container');
                    if (mmdContainerPng) {
                        mmdContainerPng.style.display = 'none';
                        mmdContainerPng.classList.add('hidden');
                    }

                    if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                        window.live2dManager.pauseRendering();
                    }
                    if (window.live2dManager && window.live2dManager.pixi_app && window.live2dManager.pixi_app.renderer) {
                        window.live2dManager.pixi_app.renderer.clear();
                    }
                    if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                        window.vrmManager.pauseRendering();
                    }
                    if (window.vrmManager && window.vrmManager.renderer) {
                        window.vrmManager.renderer.clear();
                    }
                    if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                        window.mmdManager.pauseRendering();
                    }

                    var pngtuberConfig = (data.pngtuber && typeof data.pngtuber === 'object')
                        ? Object.assign({}, data.pngtuber)
                        : { idle_image: newModelPath };
                    if (window.lanlan_config) {
                        window.lanlan_config.pngtuber = Object.assign({}, pngtuberConfig);
                    }
                    if (typeof window.loadPNGTuberAvatar !== 'function') {
                        throw new Error('PNGTuber runtime not loaded');
                    }
                    await window.loadPNGTuberAvatar(pngtuberConfig);
                } else {
                    // Live2D mode
                    window.cubism4Model = newModelPath;
                    window.vrmModel = '';

                    // Hide VRM
                    console.log('[Model] 隐藏 VRM 模型');
                    var vrmContainer2 = document.getElementById('vrm-container');
                    if (vrmContainer2) {
                        vrmContainer2.style.display = 'none';
                        vrmContainer2.classList.add('hidden');
                    }
                    var vrmCanvas2 = document.getElementById('vrm-canvas');
                    if (vrmCanvas2) {
                        vrmCanvas2.style.visibility = 'hidden';
                        vrmCanvas2.style.pointerEvents = 'none';
                    }

                    // Hide MMD
                    var mmdContainer2 = document.getElementById('mmd-container');
                    if (mmdContainer2) {
                        mmdContainer2.style.display = 'none';
                        mmdContainer2.classList.add('hidden');
                    }
                    var mmdCanvas2 = document.getElementById('mmd-canvas');
                    if (mmdCanvas2) {
                        clearMMDCanvasLoadingSession(mmdCanvas2);
                    }
                    if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                        window.vrmManager.pauseRendering();
                    }
                    // 清空VRM画布残留像素，避免透明窗口穿透
                    if (window.vrmManager && window.vrmManager.renderer) {
                        window.vrmManager.renderer.clear();
                    }
                    if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                        window.mmdManager.pauseRendering();
                    }

                    // Hide PNGTuber when returning to Live2D.
                    if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
                        window.pngtuberManager.hide();
                    }
                    cleanupPNGTuberOverlayUI();
                    var pngtuberContainer2 = document.getElementById('pngtuber-container');
                    if (pngtuberContainer2) {
                        pngtuberContainer2.style.display = 'none';
                        pngtuberContainer2.classList.add('hidden');
                    }

                    // Show & reload Live2D
                    var live2dContainer2 = document.getElementById('live2d-container');
                    if (live2dContainer2) {
                        live2dContainer2.classList.remove('hidden');
                        live2dContainer2.style.display = 'block';
                        live2dContainer2.style.visibility = 'visible';
                        live2dContainer2.style.setProperty('pointer-events', 'none', 'important');
                    }
                    var live2dCanvas2 = document.getElementById('live2d-canvas');
                    if (live2dCanvas2) {
                        live2dCanvas2.style.visibility = 'visible';
                        live2dCanvas2.style.pointerEvents = 'auto';
                    }

                    if (newModelPath) {
                        console.log('[Model] 加载 Live2D 模型:', newModelPath);

                        // Ensure Live2D manager is initialised
                        if (!window.live2dManager) {
                            console.log('[Model] Live2D 管理器未初始化，等待初始化完成');
                            if (temporaryConfig && typeof window.Live2DManager === 'function') {
                                window.live2dManager = new window.Live2DManager();
                            } else if (typeof initLive2DModel === 'function') {
                                await initLive2DModel();
                            }
                        }

                        // Load the new model
                        if (window.live2dManager) {
                            // Ensure PIXI app is initialised
                            if (!window.live2dManager.pixi_app) {
                                // 安全网：如果 canvas 被 PIXI.destroy(true) 从 DOM 移除，重新创建
                                var live2dCanvasEl = document.getElementById('live2d-canvas');
                                if (!live2dCanvasEl) {
                                    console.log('[Model] live2d-canvas 不存在，重新创建');
                                    live2dCanvasEl = document.createElement('canvas');
                                    live2dCanvasEl.id = 'live2d-canvas';
                                    var live2dContainerEl = document.getElementById('live2d-container');
                                    if (live2dContainerEl) {
                                        live2dContainerEl.appendChild(live2dCanvasEl);
                                    }
                                }
                                console.log('[Model] PIXI 应用未初始化，正在初始化...');
                                await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
                            }
                            await _waitForLive2DManagerIdle(30000);

                            // Apply saved user preferences to avoid "reset" on return from model manager
                            var modelPreferences = null;
                            try {
                                var preferences = await window.live2dManager.loadUserPreferences();
                                modelPreferences = preferences ? preferences.find(function (p) { return p && p.model_path === newModelPath; }) : null;
                            } catch (prefError) {
                                console.warn('[Model] 读取 Live2D 用户偏好失败，将继续加载模型:', prefError);
                            }

                            await window.live2dManager.loadModel(newModelPath, {
                                preferences: modelPreferences,
                                isMobile: typeof window.isMobileWidth === 'function' ? window.isMobileWidth() : (window.innerWidth <= 768),
                                suppressInitialIdle: skipIdleRestore,
                                suppressPersistentExpressions: skipPersistentExpressions
                            });
                            if (live2dContainer2) {
                                live2dContainer2.classList.remove('hidden');
                                live2dContainer2.style.display = 'block';
                                live2dContainer2.style.visibility = 'visible';
                                if (!deferRevealPrepared) {
                                    live2dContainer2.style.removeProperty('opacity');
                                }
                                live2dContainer2.style.setProperty('pointer-events', 'none', 'important');
                            }
                            if (live2dCanvas2) {
                                live2dCanvas2.style.display = 'block';
                                live2dCanvas2.style.visibility = 'visible';
                                if (!deferRevealPrepared) {
                                    live2dCanvas2.style.removeProperty('opacity');
                                }
                                live2dCanvas2.style.pointerEvents = 'auto';
                            }
                            if (window.lanlan_config) {
                                window.lanlan_config.model_type = newModelType;
                                window.lanlan_config.live3d_sub_type = live3dSubType;
                            }
                            if (typeof window.showLive2d === 'function') {
                                window.showLive2d();
                            }
                            if (window.live2dManager && typeof window.live2dManager.resumeRendering === 'function') {
                                window.live2dManager.resumeRendering();
                            }
                            ensureLive2DRenderActive('model-reload-live2d');
                            scheduleLive2DRenderActivation('model-reload-live2d');

                            // Sync legacy global references
                            if (window.LanLan1) {
                                window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
                                window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
                            }

                            // 恢复 Live2D 待机动作。教程临时模型不读取用户模型的待机动作，避免把不匹配的动作套到 yui-origin。
                            if (!skipIdleRestore) {
                                restoreLive2DIdleAnimationOnMainPage();
                            }
                        } else {
                            console.error('[Model] Live2D 管理器初始化失败');
                        }
                    } else {
                        console.warn('[Model] Live2D 模型路径为空，已切换容器但跳过模型加载');
                        window.showStatusToast(
                            window.t ? window.t('app.modelPathEmpty') : '模型路径为空',
                            2000
                        );
                    }
                }

                // 4. Commit config only after successful switch
                if (window.lanlan_config) {
                    window.lanlan_config.model_type = newModelType;
                    window.lanlan_config.live3d_sub_type = live3dSubType;
                }

                // 5. Success toast
                if (!suppressToast) {
                    window.showStatusToast(
                        window.t ? window.t('app.modelSwitched') : '模型已切换',
                        2000
                    );
                }
                reloadSucceeded = true;
            } else {
                console.error('[Model] 获取页面配置失败:', data.error);
                if (!suppressToast) {
                    window.showStatusToast(
                        window.t ? window.t('app.modelSwitchFailed') : '模型切换失败',
                        3000
                    );
                }
                if (throwOnError) {
                    throw new Error(data.error || 'page_config_failed');
                }
            }
        } catch (error) {
            console.error('[Model] 模型热切换失败:', error);
            if (activeMmdLoadingSessionId) {
                window.MMDLoadingOverlay?.fail(activeMmdLoadingSessionId, { detail: error?.message || String(error) });
                if (mmdRequestSessionId === activeMmdLoadingSessionId) {
                    mmdRequestSessionId = '';
                }
                activeMmdLoadingSessionId = '';
            }
            // 回滚提前写入的 config，防止残留错误的模型类型
            if (typeChanged && window.lanlan_config) {
                window.lanlan_config.model_type = oldModelType;
                window.lanlan_config.live3d_sub_type = oldLive3dSubType || '';
                console.warn('[Model] 已回滚 config:', { model_type: oldModelType, live3d_sub_type: oldLive3dSubType });
            }
            if (typeChanged && oldModelType === 'pngtuber') {
                try {
                    if (window.lanlan_config && window.lanlan_config.pngtuber && typeof window.loadPNGTuberAvatar === 'function') {
                        await window.loadPNGTuberAvatar(window.lanlan_config.pngtuber);
                    } else if (window.pngtuberManager && typeof window.pngtuberManager.show === 'function') {
                        window.pngtuberManager.show();
                    }
                    var restoredPngtuberContainer = document.getElementById('pngtuber-container');
                    if (restoredPngtuberContainer) {
                        restoredPngtuberContainer.classList.remove('hidden');
                        restoredPngtuberContainer.style.display = 'block';
                        restoredPngtuberContainer.style.visibility = 'visible';
                        restoredPngtuberContainer.style.pointerEvents = 'none';
                        var restoredPngtuberImage = restoredPngtuberContainer.querySelector('.pngtuber-image');
                        if (restoredPngtuberImage) {
                            restoredPngtuberImage.style.visibility = 'visible';
                            restoredPngtuberImage.style.pointerEvents = 'auto';
                        }
                    }
                } catch (restoreError) {
                    console.warn('[Model] PNGTuber restore after failed switch failed:', restoreError);
                }
            }
            if (!suppressToast) {
                window.showStatusToast(
                    window.t ? window.t('app.modelSwitchFailed') : '模型切换失败',
                    3000
                );
            }
            if (throwOnError) {
                throw error;
            }
        } finally {
            // Clear in-flight flag
            window._modelReloadInFlight = false;
            if (reloadSucceeded) {
                window._lastModelReloadKey = reloadKey;
                window._lastModelReloadAt = Date.now();
                window._lastModelReloadResult = true;
            } else {
                window._lastModelReloadResult = false;
            }
            window._modelReloadKey = '';
            resolveReload(window._lastModelReloadResult);

            // If the model manager is still open, keep the Pet UI hidden even
            // though the reload path briefly re-created containers/buttons.
            if (isMainUIHiddenByModelManager()) {
                console.log('[Model] 主界面处于模型管理隐藏状态，模型重载完成后重新隐藏 UI');
                handleHideMainUI({ preserveHiddenState: true });
            }

            // Process any queued reload request
            if (window._pendingModelReload) {
                console.log('[Model] 执行待处理的模型重载请求');
                var pendingReload = window._pendingModelReload;
                window._pendingModelReload = null;
                setTimeout(function () {
                    handleModelReload(pendingReload.targetLanlanName, pendingReload.reloadOptions)
                        .then(function (result) {
                            if (typeof pendingReload.resolve === 'function') pendingReload.resolve(result);
                        })
                        .catch(function (error) {
                            if (typeof pendingReload.reject === 'function') pendingReload.reject(error);
                        });
                }, 100);
            }
        }
    }

    /**
     * [HACK/WORKAROUND] 动态向已加载的 Live2D 模型实例注入动作组。
     * 注意：这里直接修改了 pixi-live2d-display SDK 的内部私有/只读数据结构。
     * @deprecated-if-sdk-upgraded 如果未来升级了 live2d SDK，此函数极易崩溃，请优先寻找官方 API 替代。
     */
    function _injectMotionGroupSafely(live2dModel, groupName, motionFiles) {
        if (!live2dModel || !live2dModel.internalModel || !live2dModel.internalModel.motionManager) {
            console.warn('[_injectMotionGroup] 模型结构不完整，注入失败');
            return false;
        }

        const internalModel = live2dModel.internalModel;
        const motionManager = internalModel.motionManager;
        const motionsList = motionFiles.map(file => ({ File: file }));

        try {
            console.debug(`[_injectMotionGroup] 正在向内部结构注入动作组: ${groupName}`);

            // 1. 注入 MotionManager 配置
            if (!motionManager.definitions) motionManager.definitions = {};
            motionManager.definitions[groupName] = motionsList;

            // 2. 初始化实例缓存数组（关键：必须为空数组，避免跳过实际加载）
            if (!motionManager.motionGroups) motionManager.motionGroups = {};
            if (!motionManager.motionGroups[groupName]) motionManager.motionGroups[groupName] = [];

            // 3. 同步 fallback 的 Settings 树
            if (!internalModel.settings) internalModel.settings = {};
            if (!internalModel.settings.motions) internalModel.settings.motions = {};
            internalModel.settings.motions[groupName] = motionsList;

            // 4. 同步最外层的文件引用树
            if (!live2dModel.fileReferences) live2dModel.fileReferences = {};
            if (!live2dModel.fileReferences.Motions) live2dModel.fileReferences.Motions = {};
            live2dModel.fileReferences.Motions[groupName] = motionsList;

            console.debug('[_injectMotionGroup] 注入完成');
            return true;
        } catch (err) {
            console.error('[_injectMotionGroup] 篡改 SDK 内部结构时崩溃，可能是 SDK 已升级:', err);
            return false;
        }
    }

    // =====================================================================
    // Live2D 待机动作恢复功能
    //
    // 功能说明：
    // - 主页加载时自动读取 characters.json 中保存的 live2d_idle_animation
    // - 从 API 获取当前模型的动作文件列表
    // - 手动构建 motionManager.definitions 和 motionGroups（主页没有 PreviewAll 组）
    // - 加载并循环播放保存的待机动作
    //
    // 注意：motionGroups 必须初始化为空数组 []，不能放入配置对象！
    // 原因：SDK 会检查 motionGroups 是否已有内容来判断动作是否已加载。
    // 如果放入 JSON 配置对象，SDK 会误认为动作已加载，跳过网络请求和解析。
    // =====================================================================
    async function restoreLive2DIdleAnimationOnMainPage(options = {}) {
        try {
            const shouldContinue = options && typeof options.shouldContinue === 'function'
                ? options.shouldContinue
                : null;
            const canContinue = () => {
                if (!shouldContinue) return true;
                try {
                    return shouldContinue() !== false;
                } catch (guardError) {
                    console.warn('[Live2D Main] 待机动作恢复 guard 失败，跳过恢复:', guardError);
                    return false;
                }
            };

            // 1. 获取当前角色名称，并作为当前任务的标识（防竞态）
            const initialLanlanName = window.lanlan_config?.lanlan_name;
            if (!initialLanlanName) {
                console.log('[Live2D Main] 没有 lanlan_name，跳过恢复待机动作');
                return;
            }
            if (!canContinue()) {
                console.log('[Live2D Main] 待机动作恢复已被新的交互取消');
                return;
            }

            // 2. 从 characters.json 获取保存的待机动作路径
            const response = await fetch('/api/characters');
            const data = await response.json();

            // 【竞态防护】如果中途角色被切换了，立刻中止
            if (window.lanlan_config?.lanlan_name !== initialLanlanName) return;
            if (!canContinue()) {
                console.log('[Live2D Main] 待机动作恢复已被新的交互取消');
                return;
            }

            const charData = data['猫娘']?.[initialLanlanName];
            // 【修复】兼容新旧版字段，穿透 _reserved 读取 Live2D 待机动作
            const hasOwn = (obj, key) => !!obj && Object.prototype.hasOwnProperty.call(obj, key);
            const reservedLive2D = charData?._reserved?.avatar?.live2d;
            const avatarLive2D = charData?.avatar?.live2d;
            let live2dIdleAnimation;
            let hasExplicitIdleAnimation = false;
            if (hasOwn(reservedLive2D, 'idle_animation')) {
                live2dIdleAnimation = reservedLive2D.idle_animation;
                hasExplicitIdleAnimation = true;
            } else if (hasOwn(charData, 'live2d_idle_animation')) {
                live2dIdleAnimation = charData.live2d_idle_animation;
                hasExplicitIdleAnimation = true;
            } else if (hasOwn(avatarLive2D, 'idle_animation')) {
                live2dIdleAnimation = avatarLive2D.idle_animation;
                hasExplicitIdleAnimation = true;
            }
            const live2dModelName = charData?.live2d;

            if (!live2dModelName) {
                console.log('[Live2D Main] 没有模型名称');
                return;
            }

            // 3. 从 API 获取模型的动作文件列表（主页没有初始化 PreviewAll 组）
            let modelFilesData;
            try {
                const filesResponse = await fetch('/api/live2d/model_files/' + encodeURIComponent(live2dModelName));
                modelFilesData = await filesResponse.json();
            } catch (e) {
                console.warn('[Live2D Main] 获取模型文件列表失败:', e);
                return;
            }

            // 【竞态防护】如果中途角色被切换了，立刻中止
            if (window.lanlan_config?.lanlan_name !== initialLanlanName) return;
            if (!canContinue()) {
                console.log('[Live2D Main] 待机动作恢复已被新的交互取消');
                return;
            }

            const motionFiles = modelFilesData?.motion_files || [];
            if (!live2dIdleAnimation) {
                if (hasExplicitIdleAnimation) {
                    console.log('[Live2D Main] 待机动作已明确清空');
                    return;
                }
                if (motionFiles.length === 1) {
                    const singleMotion = typeof motionFiles[0] === 'string' ? motionFiles[0].trim() : '';
                    if (!singleMotion) {
                        console.log('[Live2D Main] 唯一的 motion 文件名为空，跳过恢复');
                        return;
                    }
                    live2dIdleAnimation = singleMotion;
                    console.log('[Live2D Main] 没有保存的待机动作，使用唯一 motion 作为默认待机动作:', live2dIdleAnimation);
                } else {
                    console.log('[Live2D Main] 没有保存的待机动作');
                    return;
                }
            }

            console.log('[Live2D Main] 开始恢复待机动作:', live2dIdleAnimation);

            const motionIndex = motionFiles.indexOf(live2dIdleAnimation);
            if (motionIndex < 0) {
                console.log('[Live2D Main] 待机动作不在当前模型的动作列表中:', live2dIdleAnimation);
                return;
            }

            // 4. 获取 Live2D 模型和 motionManager
            const live2dManager = window.live2dManager;
            const live2dModel = live2dManager?.getCurrentModel();
            if (!live2dModel) {
                console.log('[Live2D Main] Live2D 模型未加载，跳过恢复');
                return;
            }

            const internalModel = live2dModel.internalModel;
            if (!internalModel?.motionManager) {
                console.log('[Live2D Main] motionManager 不存在');
                return;
            }

            const motionManager = internalModel.motionManager;
            const groupName = 'PreviewAll';

            // 5. 使用隔离的 Helper 函数注入动作组配置
            const injectSuccess = _injectMotionGroupSafely(live2dModel, groupName, motionFiles);
            if (!injectSuccess) {
                console.log('[Live2D Main] 注入动作组失败，跳过动作恢复');
                return;
            }

            // 6. 加载动作（耗时操作）
            await motionManager.loadMotion(groupName, motionIndex);

            // 【最终竞态防护】加载完成后，确保角色没切走，且当前的 Live2D 模型实例还是我之前拿到的那个
            if (window.lanlan_config?.lanlan_name !== initialLanlanName || live2dManager?.getCurrentModel() !== live2dModel) {
                console.log('[Live2D Main] 模型或角色已切换，中止待机动作播放');
                return;
            }
            if (!canContinue()) {
                console.log('[Live2D Main] 待机动作恢复已被新的交互取消');
                return;
            }

            // 7. 设置循环播放
            const motionInstance = motionManager.motionGroups?.[groupName]?.[motionIndex];
            if (motionInstance) {
                if (typeof motionInstance.setIsLoop === 'function') {
                    motionInstance.setIsLoop(true);
                } else if (motionInstance._loop !== undefined) {
                    motionInstance._loop = true;
                }
            }

            // 8. 停止当前动作并播放保存的待机动作
            motionManager.stopAllMotions();
            live2dModel.motion(groupName, motionIndex, 3);
            console.log('[Live2D Main] 已恢复待机动作并循环播放:', live2dIdleAnimation);

        } catch (error) {
            console.error('[Live2D Main] 恢复待机动作失败:', error);
        }
    }

    // 暴露给全局作用域，供 live2d-init.js 调用
    window.restoreLive2DIdleAnimationOnMainPage = restoreLive2DIdleAnimationOnMainPage;

    // =====================================================================
    // Hide / Show main UI (called when entering/leaving model manager)
    // =====================================================================

    /**
     * Hide main-page model rendering (entering model manager).
     */
    function handleHideMainUI(options) {
        if (!_isModelHostPage()) return;
        options = options || {};
        var skipHiddenStateUpdate = options.skipHiddenStateUpdate || options.preserveHiddenState;
        if (!skipHiddenStateUpdate) {
            setMainUIHiddenByModelManager(true);
        }
        console.log('[UI] 隐藏主界面并暂停渲染');

        try {
            // Hide Live2D
            var live2dContainer = document.getElementById('live2d-container');
            if (live2dContainer) {
                live2dContainer.style.display = 'none';
                live2dContainer.classList.add('hidden');
            }

            var live2dCanvas = document.getElementById('live2d-canvas');
            if (live2dCanvas) {
                live2dCanvas.style.visibility = 'hidden';
                live2dCanvas.style.pointerEvents = 'none';
            }

            // Hide VRM
            var vrmContainer = document.getElementById('vrm-container');
            if (vrmContainer) {
                vrmContainer.style.display = 'none';
                vrmContainer.classList.add('hidden');
            }

            var vrmCanvas = document.getElementById('vrm-canvas');
            if (vrmCanvas) {
                vrmCanvas.style.visibility = 'hidden';
                vrmCanvas.style.pointerEvents = 'none';
            }

            // Hide MMD
            var mmdContainer = document.getElementById('mmd-container');
            if (mmdContainer) {
                mmdContainer.style.display = 'none';
                mmdContainer.classList.add('hidden');
            }

            var mmdCanvas = document.getElementById('mmd-canvas');
            if (mmdCanvas) {
                clearMMDCanvasLoadingSession(mmdCanvas);
            }

            // Pause render loops to save resources
            if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
                window.vrmManager.pauseRendering();
            }

            if (window.live2dManager && typeof window.live2dManager.pauseRendering === 'function') {
                window.live2dManager.pauseRendering();
            }

            if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
                window.mmdManager.pauseRendering();
            }

            // 停止 UI 更新循环（独立于渲染循环，pauseRendering 不会停止它们）
            // 如果不停止，UI 循环每帧会覆盖下面设置的 display: none，导致按钮重新出现
            if (window.vrmManager && window.vrmManager._uiUpdateLoopId != null) {
                cancelAnimationFrame(window.vrmManager._uiUpdateLoopId);
                window.vrmManager._uiUpdateLoopId = null;
            }
            if (window.mmdManager && window.mmdManager._uiUpdateLoopId != null) {
                cancelAnimationFrame(window.mmdManager._uiUpdateLoopId);
                window.mmdManager._uiUpdateLoopId = null;
            }

            // 隐藏所有悬浮按钮、锁图标和返回按钮（它们挂载在 document.body 上，不随容器隐藏）。
            // 记录隐藏前的 display，避免恢复时清空 display 导致容器短暂按默认 block 布局显示，
            // 出现“语音控制/屏幕分享/猫爪/设置/请她离开”先挤在一起再分开的闪烁。
            document.querySelectorAll(
                '#live2d-floating-buttons, #vrm-floating-buttons, #mmd-floating-buttons, #pngtuber-floating-buttons, ' +
                '#live2d-lock-icon, #vrm-lock-icon, #mmd-lock-icon, #pngtuber-lock-icon, ' +
                '#live2d-return-button-container, #vrm-return-button-container, #mmd-return-button-container, #pngtuber-return-button-container'
            ).forEach(function (el) {
                if (!el.dataset.nekoPreHideDisplay) {
                    var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));
                    var computedDisplay = '';
                    try {
                        computedDisplay = window.getComputedStyle(el).display || '';
                    } catch (_) {}
                    el.dataset.nekoPreHideDisplay = isFloatingButtons && !el.style.display && computedDisplay === 'none'
                        ? 'flex'
                        : (computedDisplay && computedDisplay !== 'none'
                            ? computedDisplay
                            : (el.style.display || 'none'));
                }
                el.style.display = 'none';
            });
        } catch (error) {
            console.error('[UI] 隐藏主界面失败:', error);
        }
    }

    /**
     * Show main-page model rendering (returning to main page).
     */
    function handleShowMainUI() {
        if (!_isModelHostPage()) return;
        setMainUIHiddenByModelManager(false);
        // 模型重载进行中时跳过：handleModelReload 自己会正确切换容器，
        // 此时 lanlan_config.model_type 尚未更新，handleShowMainUI 会
        // 错误地恢复旧模型类型的容器，导致需要切换两次才能成功。
        if (window._modelReloadInFlight) {
            console.log('[UI] 模型重载进行中，跳过显示主界面（避免覆盖正在切换的容器）');
            return;
        }
        console.log('[UI] 显示主界面并恢复渲染');

        try {
            var currentModelType = window.lanlan_config?.model_type || 'live2d';
            var activeUiPrefix = currentModelType === 'pngtuber'
                ? 'pngtuber'
                : (currentModelType === 'live3d'
                    ? (((window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase() === 'mmd') ? 'mmd' : 'vrm')
                    : (currentModelType === 'vrm' ? 'vrm' : 'live2d'));

            function hideInactiveAvatarRuntime(prefix) {
                if (prefix === 'live2d') {
                    var live2dContainerToHide = document.getElementById('live2d-container');
                    if (live2dContainerToHide) {
                        live2dContainerToHide.style.display = 'none';
                        live2dContainerToHide.classList.add('hidden');
                    }
                    var live2dCanvasToHide = document.getElementById('live2d-canvas');
                    if (live2dCanvasToHide) {
                        live2dCanvasToHide.style.visibility = 'hidden';
                        live2dCanvasToHide.style.pointerEvents = 'none';
                    }
                } else if (prefix === 'vrm') {
                    var vrmContainerToHide = document.getElementById('vrm-container');
                    if (vrmContainerToHide) {
                        vrmContainerToHide.style.display = 'none';
                        vrmContainerToHide.classList.add('hidden');
                    }
                    var vrmCanvasToHide = document.getElementById('vrm-canvas');
                    if (vrmCanvasToHide) {
                        vrmCanvasToHide.style.visibility = 'hidden';
                        vrmCanvasToHide.style.pointerEvents = 'none';
                    }
                } else if (prefix === 'mmd') {
                    var mmdContainerToHide = document.getElementById('mmd-container');
                    if (mmdContainerToHide) {
                        mmdContainerToHide.style.display = 'none';
                        mmdContainerToHide.classList.add('hidden');
                    }
                    var mmdCanvasToHide = document.getElementById('mmd-canvas');
                    if (mmdCanvasToHide) {
                        mmdCanvasToHide.style.visibility = 'hidden';
                        mmdCanvasToHide.style.pointerEvents = 'none';
                    }
                } else if (prefix === 'pngtuber') {
                    if (window.pngtuberManager && typeof window.pngtuberManager.hide === 'function') {
                        window.pngtuberManager.hide();
                    }
                    if (typeof cleanupPNGTuberOverlayUI === 'function') {
                        cleanupPNGTuberOverlayUI();
                    }
                    var pngtuberContainerToHide = document.getElementById('pngtuber-container');
                    if (pngtuberContainerToHide) {
                        pngtuberContainerToHide.style.display = 'none';
                        pngtuberContainerToHide.style.visibility = 'hidden';
                        pngtuberContainerToHide.classList.add('hidden');
                        pngtuberContainerToHide.style.pointerEvents = 'none';
                    }
                    var pngtuberImageToHide = pngtuberContainerToHide ? pngtuberContainerToHide.querySelector('.pngtuber-image') : null;
                    if (pngtuberImageToHide) {
                        pngtuberImageToHide.style.visibility = 'hidden';
                        pngtuberImageToHide.style.pointerEvents = 'none';
                    }
                }
                document.querySelectorAll('#' + prefix + '-floating-buttons, #' + prefix + '-lock-icon, #' + prefix + '-return-button-container')
                    .forEach(function (el) {
                        el.style.display = 'none';
                        el.style.visibility = 'hidden';
                        el.style.opacity = '0';
                        delete el.dataset.nekoPreHideDisplay;
                    });
            }

            ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach(function (prefix) {
                if (prefix !== activeUiPrefix) hideInactiveAvatarRuntime(prefix);
            });
            console.log('[UI] 当前模型类型:', currentModelType);

            if (currentModelType === 'vrm') {
                // Show VRM
                var vrmContainer = document.getElementById('vrm-container');
                if (vrmContainer) {
                    vrmContainer.style.display = 'block';
                    vrmContainer.classList.remove('hidden');
                    console.log('[UI] VRM 容器已显示，display:', vrmContainer.style.display);
                }

                var vrmCanvas = document.getElementById('vrm-canvas');
                if (vrmCanvas) {
                    vrmCanvas.style.visibility = 'visible';
                    vrmCanvas.style.pointerEvents = 'auto';
                    console.log('[UI] VRM canvas 已显示，visibility:', vrmCanvas.style.visibility);
                }

                // Resume VRM rendering
                if (window.vrmManager && typeof window.vrmManager.resumeRendering === 'function') {
                    window.vrmManager.resumeRendering();
                }
                // 重启 VRM UI 更新循环（被 handleHideMainUI 停止）
                if (window.vrmManager && window.vrmManager._uiUpdateLoopId == null
                    && typeof window.vrmManager._startUIUpdateLoop === 'function') {
                    window.vrmManager._snapUIPosition = true;
                    window.vrmManager._startUIUpdateLoop();
                }
            } else if (currentModelType === 'live3d') {
                // Live3D: determine sub-type from config
                var live3dSubType = (window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
                
                if (live3dSubType === 'mmd') {
                    var mmdContainerR = document.getElementById('mmd-container');
                    if (mmdContainerR) {
                        mmdContainerR.style.display = 'block';
                        mmdContainerR.classList.remove('hidden');
                    }
                    var mmdCanvasR = document.getElementById('mmd-canvas');
                    var hasActiveLoadingSession = mmdCanvasR && !!mmdCanvasR.dataset.mmdLoadingSessionId;
                    if (mmdCanvasR && !hasActiveLoadingSession) {
                        mmdCanvasR.style.visibility = 'visible';
                        mmdCanvasR.style.pointerEvents = 'auto';
                    }
                    if (window.mmdManager && typeof window.mmdManager.resumeRendering === 'function') {
                        window.mmdManager.resumeRendering();
                    }
                    // 重启 MMD UI 更新循环（被 handleHideMainUI 停止）
                    // UI 循环会自动管理浮动按钮和锁图标的显示/定位
                    if (window.mmdManager && window.mmdManager._uiUpdateLoopId == null
                        && typeof window.mmdManager._startUIUpdateLoop === 'function') {
                        window.mmdManager._snapUIPosition = true;
                        window.mmdManager._startUIUpdateLoop();
                    }
                } else {
                    var vrmContainerR = document.getElementById('vrm-container');
                    if (vrmContainerR) {
                        vrmContainerR.style.display = 'block';
                        vrmContainerR.classList.remove('hidden');
                    }
                    var vrmCanvasR = document.getElementById('vrm-canvas');
                    if (vrmCanvasR) {
                        vrmCanvasR.style.visibility = 'visible';
                        vrmCanvasR.style.pointerEvents = 'auto';
                    }
                    if (window.vrmManager && typeof window.vrmManager.resumeRendering === 'function') {
                        window.vrmManager.resumeRendering();
                    }
                    if (window.vrmManager && window.vrmManager._uiUpdateLoopId == null
                        && typeof window.vrmManager._startUIUpdateLoop === 'function') {
                        window.vrmManager._snapUIPosition = true;
                        window.vrmManager._startUIUpdateLoop();
                    }
                }
            } else if (currentModelType === 'pngtuber') {
                var pngtuberContainer = document.getElementById('pngtuber-container');
                if (pngtuberContainer) {
                    pngtuberContainer.style.display = 'block';
                    pngtuberContainer.style.visibility = 'visible';
                    pngtuberContainer.classList.remove('hidden');
                    pngtuberContainer.style.pointerEvents = 'none';
                }
                var pngtuberImage = pngtuberContainer ? pngtuberContainer.querySelector('.pngtuber-image') : null;
                if (pngtuberImage) {
                    pngtuberImage.style.visibility = 'visible';
                    pngtuberImage.style.pointerEvents = 'auto';
                }
                if (window.pngtuberManager && typeof window.pngtuberManager.show === 'function') {
                    window.pngtuberManager.show();
                } else if (window.pngtuberManager && typeof window.pngtuberManager.resumeRendering === 'function') {
                    window.pngtuberManager.resumeRendering();
                }
            } else {
                // Show Live2D
                var live2dContainer = document.getElementById('live2d-container');
                if (live2dContainer) {
                    live2dContainer.style.display = 'block';
                    live2dContainer.classList.remove('hidden');
                    console.log('[UI] Live2D 容器已显示，display:', live2dContainer.style.display);
                }

                var live2dCanvas = document.getElementById('live2d-canvas');
                if (live2dCanvas) {
                    live2dCanvas.style.visibility = 'visible';
                    live2dCanvas.style.pointerEvents = 'auto';
                    console.log('[UI] Live2D canvas 已显示，visibility:', live2dCanvas.style.visibility);
                }

                // Resume Live2D rendering
                if (window.live2dManager && typeof window.live2dManager.resumeRendering === 'function') {
                    window.live2dManager.resumeRendering();
                }
            }

            // 只恢复常规悬浮按钮与锁图标。
            // “请她回来”按钮默认由“请她离开”流程显示；从角色外形/模型管理窗口返回时
            // 如果在这里把 return-button-container 的 display 从 none 清空，会凭空多出一个返回按钮。
            // 恢复为隐藏前的 display（如 flex/block），并让浮动按钮首两帧保持不可见，
            // 等 UI 更新循环完成定位后再显露，避免按钮先挤在一起再分开。
            var restoringFloatingEls = Array.from(document.querySelectorAll(
                '#live2d-floating-buttons, #vrm-floating-buttons, #mmd-floating-buttons, #pngtuber-floating-buttons, ' +
                '#live2d-lock-icon, #vrm-lock-icon, #mmd-lock-icon, #pngtuber-lock-icon'
            )).filter(function (el) {
                return el && el.id && el.id.indexOf(activeUiPrefix + '-') === 0;
            });
            var hiddenFloatingButtonEls = [];
            restoringFloatingEls.forEach(function (el) {
                var restoreDisplay = el.dataset.nekoPreHideDisplay || '';
                var isFloatingButtons = !!(el.id && /-floating-buttons$/.test(el.id));
                if (restoreDisplay && restoreDisplay !== 'none') {
                    if (isFloatingButtons) {
                        el.style.visibility = 'hidden';
                        hiddenFloatingButtonEls.push(el);
                    }
                    el.style.display = isFloatingButtons ? 'flex' : restoreDisplay;
                }
                delete el.dataset.nekoPreHideDisplay;
            });
            if (hiddenFloatingButtonEls.length > 0) {
                requestAnimationFrame(function () {
                    requestAnimationFrame(function () {
                        hiddenFloatingButtonEls.forEach(function (el) {
                            if (!el || !el.isConnected || el.style.display === 'none') return;
                            el.style.removeProperty('visibility');
                        });
                    });
                });
            }
            document.querySelectorAll(
                '#live2d-return-button-container, #vrm-return-button-container, #mmd-return-button-container, #pngtuber-return-button-container'
            ).forEach(function (el) {
                if (!el || !el.id || el.id.indexOf(activeUiPrefix + '-') !== 0) {
                    if (el) el.style.display = 'none';
                    return;
                }
                if (!el.getAttribute('data-neko-return-visible')) {
                    el.style.display = 'none';
                }
            });
        } catch (error) {
            console.error('[UI] 显示主界面失败:', error);
        }
    }

    // =====================================================================
    // Voice chat composer sync (cross-window)
    // =====================================================================

    var VOICE_CONFIG_SWITCH_STALE_MS = 45000;
    var _voiceConfigSwitchOps = {};
    var _voiceConfigSwitchWaiters = [];
    var _pendingVoiceChatComposerHiddenByLanlan = {};
    var VOICE_CHAT_COMPOSER_PENDING_STALE_MS = 30000;

    function getCurrentLanlanName() {
        try {
            if (window.appState && typeof window.appState.lanlan_name === 'string' && window.appState.lanlan_name) {
                return window.appState.lanlan_name;
            }
        } catch (_) {}
        return (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
    }

    function isVoiceChatDesktopLayout() {
        return !(window.appUtils && typeof window.appUtils.isMobile === 'function' && window.appUtils.isMobile());
    }

    function shouldKeepVoiceComposerHidden() {
        return isVoiceChatDesktopLayout() && !!(
            (S && (S.isRecording || S.voiceChatActive || S.voiceStartPending)) ||
            window.isMicStarting
        );
    }

    function applyVoiceChatComposerHidden(hidden) {
        hidden = !!hidden;
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setComposerHidden === 'function') {
            window.reactChatWindowHost.setComposerHidden(hidden);
        }
        var textInputArea = document.getElementById('text-input-area');
        if (textInputArea) {
            if (hidden) {
                textInputArea.classList.add('hidden');
            } else {
                textInputArea.classList.remove('hidden');
            }
        }
    }

    function getVoiceChatComposerHiddenElectronBridge() {
        var bridge = window.nekoElectronVoiceChatComposerHidden;
        return bridge && typeof bridge.send === 'function' ? bridge : null;
    }

    function postVoiceChatComposerHiddenElectron(payload) {
        var bridge = getVoiceChatComposerHiddenElectronBridge();
        if (!bridge) return false;
        try {
            bridge.send(payload || {});
            return true;
        } catch (err) {
            console.warn('[VoiceChat] Electron composer hidden bridge failed:', err);
            return false;
        }
    }

    function postVoiceChatComposerHiddenPayload(payload) {
        postInterpageMessage(payload);
        postVoiceChatComposerHiddenElectron(payload);
    }

    function getVoiceChatComposerHiddenMessageTimestamp(data) {
        var timestamp = Number(data && data.timestamp);
        return Number.isFinite(timestamp) ? timestamp : Date.now();
    }

    function prunePendingVoiceChatComposerHiddenMessages(now) {
        now = now || Date.now();
        Object.keys(_pendingVoiceChatComposerHiddenByLanlan).forEach(function (lanlanName) {
            var data = _pendingVoiceChatComposerHiddenByLanlan[lanlanName];
            if (!data || now - getVoiceChatComposerHiddenMessageTimestamp(data) > VOICE_CHAT_COMPOSER_PENDING_STALE_MS) {
                delete _pendingVoiceChatComposerHiddenByLanlan[lanlanName];
            }
        });
    }

    function rememberPendingVoiceChatComposerHiddenMessage(data) {
        if (!data || !data.lanlan_name) return false;
        var lanlanName = String(data.lanlan_name);
        var timestamp = getVoiceChatComposerHiddenMessageTimestamp(data);
        prunePendingVoiceChatComposerHiddenMessages(timestamp);
        var previous = _pendingVoiceChatComposerHiddenByLanlan[lanlanName];
        if (!previous || timestamp >= getVoiceChatComposerHiddenMessageTimestamp(previous)) {
            _pendingVoiceChatComposerHiddenByLanlan[lanlanName] = Object.assign({}, data, {
                timestamp: timestamp
            });
        }
        return true;
    }

    function applyVoiceComposerHiddenFromActive(active) {
        var requestedHidden = !!active;
        if (S) {
            S.voiceChatActive = requestedHidden;
        }
        var effectiveHidden = requestedHidden || (!requestedHidden && shouldKeepVoiceComposerHidden());
        if (S) {
            S.voiceChatActive = effectiveHidden;
        }
        applyVoiceChatComposerHidden(effectiveHidden);
        return effectiveHidden;
    }

    function isVoiceChatComposerHiddenMessageForCurrentLanlan(data) {
        if (!data || !data.lanlan_name) return true;
        var currentName = getCurrentLanlanName();
        return !!currentName && data.lanlan_name === currentName;
    }

    function handleVoiceChatComposerHiddenMessage(data) {
        if (!data || data.action !== 'voice_chat_active') return false;
        if (data.lanlan_name && !getCurrentLanlanName()) {
            rememberPendingVoiceChatComposerHiddenMessage(data);
            return true;
        }
        if (!isVoiceChatComposerHiddenMessageForCurrentLanlan(data)) return true;
        applyVoiceComposerHiddenFromActive(data.active);
        return true;
    }

    function consumePendingVoiceChatComposerHiddenMessage(lanlanName) {
        var currentName = lanlanName || getCurrentLanlanName();
        if (!currentName) return false;
        prunePendingVoiceChatComposerHiddenMessages(Date.now());
        var data = _pendingVoiceChatComposerHiddenByLanlan[currentName];
        if (!data) return false;
        delete _pendingVoiceChatComposerHiddenByLanlan[currentName];
        applyVoiceComposerHiddenFromActive(data.active);
        return true;
    }

    function readGoodbyeChatComposerHidden() {
        try {
            if (typeof window.isNekoGoodbyeModeActive === 'function'
                && window.isNekoGoodbyeModeActive()) {
                return true;
            }
        } catch (_) {}
        if (window.__nekoGoodbyeChatComposerHidden
            && typeof window.__nekoGoodbyeChatComposerHidden === 'object'
            && window.__nekoGoodbyeChatComposerHidden.hidden === true) {
            return true;
        }
        return !!(
            (window.live2dManager && window.live2dManager._goodbyeClicked)
            || (window.vrmManager && window.vrmManager._goodbyeClicked)
            || (window.mmdManager && window.mmdManager._goodbyeClicked)
            || (window.__nekoGoodbyeSilentState && window.__nekoGoodbyeSilentState.active === true)
        );
    }

    function applyGoodbyeChatComposerHidden(hidden, reason) {
        hidden = !!hidden;
        var detail = {
            hidden: hidden,
            reason: reason || (hidden ? 'goodbye' : 'return'),
            timestamp: Date.now()
        };
        window.__nekoGoodbyeChatComposerHidden = detail;
        if (window.reactChatWindowHost && typeof window.reactChatWindowHost.setGoodbyeComposerHidden === 'function') {
            window.reactChatWindowHost.setGoodbyeComposerHidden(hidden, detail.reason);
        } else {
            try {
                window.dispatchEvent(new CustomEvent('react-chat-window:set-goodbye-composer-hidden', {
                    detail: detail
                }));
            } catch (_) {}
        }
    }

    function getGoodbyeChatComposerHiddenElectronBridge() {
        var bridge = window.nekoElectronGoodbyeChatComposerHidden;
        return bridge && typeof bridge.send === 'function' ? bridge : null;
    }

    function postGoodbyeChatComposerHiddenElectron(payload) {
        var bridge = getGoodbyeChatComposerHiddenElectronBridge();
        if (!bridge) return false;
        try {
            bridge.send(payload || {});
            return true;
        } catch (err) {
            console.warn('[Goodbye] Electron composer hidden bridge failed:', err);
            return false;
        }
    }

    function postGoodbyeChatComposerHiddenPayload(payload) {
        if (nekoBroadcastChannel) {
            nekoBroadcastChannel.postMessage(payload);
        }
        postGoodbyeChatComposerHiddenElectron(payload);
    }

    function requestGoodbyeChatComposerHiddenState(reason) {
        var lanlanName = getCurrentLanlanName();
        if (!lanlanName) return false;
        postGoodbyeChatComposerHiddenPayload({
            action: 'request_goodbye_chat_composer_hidden',
            reason: reason || 'request-goodbye-chat-composer-hidden',
            lanlan_name: lanlanName,
            timestamp: Date.now()
        });
        return true;
    }

    function isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data) {
        if (!data || !data.lanlan_name) return false;
        var currentName = getCurrentLanlanName();
        return !!currentName && data.lanlan_name === currentName;
    }

    function handleGoodbyeChatComposerHiddenMessage(data, via) {
        if (!data || !data.action) return false;
        if (data.action === 'goodbye_chat_composer_hidden') {
            if (!isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data)) return true;
            applyGoodbyeChatComposerHidden(!!data.hidden, data.reason || via || 'broadcast');
            return true;
        }
        if (data.action === 'request_goodbye_chat_composer_hidden') {
            if (isStandaloneChatPage()) return true;
            if (!isGoodbyeChatComposerHiddenMessageForCurrentLanlan(data)) return true;
            postGoodbyeChatComposerHiddenState(undefined, 'request-goodbye-chat-composer-hidden');
            return true;
        }
        return false;
    }

    function postGoodbyeChatComposerHiddenState(hidden, reason) {
        var lanlanName = getCurrentLanlanName();
        var nextHidden = hidden === undefined ? readGoodbyeChatComposerHidden() : !!hidden;
        var nextReason = reason || (nextHidden ? 'goodbye' : 'return');
        applyGoodbyeChatComposerHidden(nextHidden, nextReason);
        if (!lanlanName) return;
        postGoodbyeChatComposerHiddenPayload({
            action: 'goodbye_chat_composer_hidden',
            hidden: nextHidden,
            reason: nextReason,
            lanlan_name: lanlanName,
            timestamp: Date.now()
        });
    }

    function pruneVoiceConfigSwitchOps(now) {
        now = now || Date.now();
        Object.keys(_voiceConfigSwitchOps).forEach(function (opId) {
            var op = _voiceConfigSwitchOps[opId];
            if (!op || now - (op.updatedAt || op.startedAt || 0) > VOICE_CONFIG_SWITCH_STALE_MS) {
                delete _voiceConfigSwitchOps[opId];
            }
        });
    }

    function isVoiceConfigSwitching() {
        pruneVoiceConfigSwitchOps(Date.now());
        return Object.keys(_voiceConfigSwitchOps).length > 0;
    }

    function notifyVoiceConfigSwitchWaiters() {
        _voiceConfigSwitchWaiters.slice().forEach(function (waiter) {
            try { waiter(); } catch (_) { /* 等待器异常不影响状态同步 */ }
        });
    }

    function isVoiceConfigMessageForCurrentLanlan(data) {
        var currentName = getCurrentLanlanName();
        // 没带 lanlan_name 的广播视为通用通知，所有窗口都接受。
        // 带了 lanlan_name 但本窗口 config 还没注入（currentName 空）时拒绝：
        // 否则别的角色的 op 会被存入 _voiceConfigSwitchOps，配好后又收不到对应的
        // active=false（被 lanlan_name mismatch 滤掉），导致 waitForVoiceConfigSwitchReady
        // 在最长 30s 超时前一直阻塞，触发误报的"音色切换超时"。
        if (!data.lanlan_name) return true;
        return !!currentName && data.lanlan_name === currentName;
    }

    function handleVoiceConfigSwitchingMessage(data) {
        if (!data || !isVoiceConfigMessageForCurrentLanlan(data)) return;
        var now = Date.now();
        var opId = String(data.op_id || data.operation_id || data.lanlan_name || 'voice_config_switch');
        var active = !!data.active;

        if (active) {
            pruneVoiceConfigSwitchOps(now);
            _voiceConfigSwitchOps[opId] = {
                lanlanName: data.lanlan_name || '',
                startedAt: _voiceConfigSwitchOps[opId]?.startedAt || now,
                updatedAt: now
            };
        } else if (data.op_id || data.operation_id) {
            delete _voiceConfigSwitchOps[opId];
        } else {
            Object.keys(_voiceConfigSwitchOps).forEach(function (knownOpId) {
                var op = _voiceConfigSwitchOps[knownOpId];
                if (!data.lanlan_name || !op || !op.lanlanName || op.lanlanName === data.lanlan_name) {
                    delete _voiceConfigSwitchOps[knownOpId];
                }
            });
        }

        notifyVoiceConfigSwitchWaiters();
        window.dispatchEvent(new CustomEvent('neko:voice-config-switching-changed', {
            detail: { active: isVoiceConfigSwitching(), lanlan_name: data.lanlan_name || '' }
        }));
    }

    function waitForVoiceConfigSwitchReady(options) {
        options = options || {};
        var timeoutMs = Number.isFinite(options.timeoutMs) ? Math.max(0, options.timeoutMs) : 30000;
        var stableMs = Number.isFinite(options.stableMs) ? Math.max(0, options.stableMs) : 0;
        var onWaiting = typeof options.onWaiting === 'function' ? options.onWaiting : null;
        var waitingNotified = false;

        return new Promise(function (resolve) {
            var done = false;
            var stableTimer = null;
            var timeoutTimer = null;

            function cleanup() {
                done = true;
                if (stableTimer) clearTimeout(stableTimer);
                if (timeoutTimer) clearTimeout(timeoutTimer);
                stableTimer = null;
                timeoutTimer = null;
                _voiceConfigSwitchWaiters = _voiceConfigSwitchWaiters.filter(function (waiter) {
                    return waiter !== evaluate;
                });
            }

            function resolveReady(timedOut) {
                cleanup();
                resolve({ timedOut: !!timedOut });
            }

            function notifyWaitingOnce() {
                if (!waitingNotified && onWaiting) {
                    waitingNotified = true;
                    try { onWaiting(); } catch (_) { /* 提示失败不影响启动等待 */ }
                }
            }

            function evaluate() {
                if (done) return;
                if (stableTimer) {
                    clearTimeout(stableTimer);
                    stableTimer = null;
                }
                if (isVoiceConfigSwitching()) {
                    notifyWaitingOnce();
                    return;
                }
                if (stableMs <= 0) {
                    resolveReady(false);
                    return;
                }
                stableTimer = setTimeout(function () {
                    stableTimer = null;
                    if (isVoiceConfigSwitching()) {
                        notifyWaitingOnce();
                        return;
                    }
                    resolveReady(false);
                }, stableMs);
            }

            _voiceConfigSwitchWaiters.push(evaluate);
            if (timeoutMs > 0) {
                timeoutTimer = setTimeout(function () {
                    resolveReady(true);
                }, timeoutMs);
            }
            evaluate();
        });
    }

    /**
     * 同步本地聊天输入栏状态，并广播给其它窗口。
     * app-buttons.js / app-audio-capture.js 会在语音开始和结束时调用。
     *
     * @param {boolean} hidden - true 表示收起输入栏；false 表示允许展开输入栏
     */
    function syncVoiceChatComposerHidden(hidden) {
        var effectiveHidden = applyVoiceComposerHiddenFromActive(hidden);
        // 同步给其它页面（chat.html ↔ index.html）
        postVoiceChatComposerHiddenPayload({
            action: 'voice_chat_active',
            active: effectiveHidden,
            lanlan_name: getCurrentLanlanName(),
            timestamp: Date.now()
        });
    }

    // =====================================================================
    // BroadcastChannel initialisation
    // =====================================================================

    var nekoBroadcastChannel = null;
    var _isRelayingYuiGuideHandoffSent = false;
    var _pendingYuiGuideChatMessages = [];
    var _yuiGuideChatFlushTimer = null;
    var _yuiGuideChatFlushAttempts = 0;
    var YUI_GUIDE_CHAT_FLUSH_MAX_ATTEMPTS = 50;
    var IDLE_CHAT_COMPACT_SURFACE_HEARTBEAT_MS = 1000;
    var idleChatCompactSurfaceHeartbeatTimer = 0;
    var idleChatCompactSurfaceLastPayload = null;

    function postInterpageMessage(message, options) {
        if (!message || typeof message !== 'object') {
            return false;
        }
        var normalizedOptions = options || {};
        if (nekoBroadcastChannel && typeof nekoBroadcastChannel.postMessage === 'function') {
            try {
                nekoBroadcastChannel.postMessage(message);
                return true;
            } catch (_) {
                // Fall through to opener fallback when BroadcastChannel is temporarily unavailable.
            }
        }
        if (
            normalizedOptions.openerFallback === true
            && window.opener
            && !window.opener.closed
            && typeof window.opener.postMessage === 'function'
        ) {
            try {
                window.opener.postMessage(message, window.location.origin);
                return true;
            } catch (_) {
                return false;
            }
        }
        return false;
    }

    function stopIdleChatCompactSurfaceHeartbeat() {
        if (!idleChatCompactSurfaceHeartbeatTimer) return;
        yuiGuideInterpageResources.clearInterval(idleChatCompactSurfaceHeartbeatTimer);
        idleChatCompactSurfaceHeartbeatTimer = 0;
    }

    function startIdleChatCompactSurfaceHeartbeat() {
        if (idleChatCompactSurfaceHeartbeatTimer) return;
        idleChatCompactSurfaceHeartbeatTimer = yuiGuideInterpageResources.setInterval(function () {
            if (!nekoBroadcastChannel ||
                !idleChatCompactSurfaceLastPayload ||
                !idleChatCompactSurfaceLastPayload.visible ||
                !idleChatCompactSurfaceLastPayload.screenRect) {
                stopIdleChatCompactSurfaceHeartbeat();
                return;
            }
            postInterpageMessage(Object.assign({}, idleChatCompactSurfaceLastPayload, {
                lanlan_name: getCurrentLanlanName(),
                timestamp: Date.now(),
                heartbeat: true
            }));
        }, IDLE_CHAT_COMPACT_SURFACE_HEARTBEAT_MS);
    }

    function syncIdleChatCompactSurfaceHeartbeat(payload) {
        idleChatCompactSurfaceLastPayload = payload || null;
        if (payload && payload.visible && payload.screenRect) {
            startIdleChatCompactSurfaceHeartbeat();
            return;
        }
        stopIdleChatCompactSurfaceHeartbeat();
    }

    function postIdleChatCompactSurfaceState(detail) {
        var screenRect = detail && detail.screenRect ? detail.screenRect : null;
        var payload = {
            action: 'idle_chat_compact_surface_state',
            source: 'chat-window',
            lanlan_name: getCurrentLanlanName(),
            visible: !!screenRect,
            screenRect: screenRect,
            resizeActive: !!(detail && detail.resizeActive),
            dragging: !!(detail && detail.dragging),
            timestamp: Date.now()
        };
        postInterpageMessage(payload);
        syncIdleChatCompactSurfaceHeartbeat(payload);
    }

    function scheduleYuiGuideChatMessageFlush(delay) {
        if (_yuiGuideChatFlushTimer) return;
        _yuiGuideChatFlushTimer = yuiGuideInterpageResources.setTimeout(
            flushPendingYuiGuideChatMessages,
            typeof delay === 'number' ? delay : 0
        );
    }

    function clearYuiGuideChatFlushTimer() {
        if (!_yuiGuideChatFlushTimer) return;
        yuiGuideInterpageResources.clearTimeout(_yuiGuideChatFlushTimer);
        _yuiGuideChatFlushTimer = null;
    }

    function flushPendingYuiGuideChatMessages() {
        _yuiGuideChatFlushTimer = null;
        if (!_pendingYuiGuideChatMessages.length) {
            _yuiGuideChatFlushAttempts = 0;
            return;
        }

        var host = window.reactChatWindowHost;
        if (!host || typeof host.appendMessage !== 'function') {
            if (_yuiGuideChatFlushAttempts < YUI_GUIDE_CHAT_FLUSH_MAX_ATTEMPTS) {
                _yuiGuideChatFlushAttempts += 1;
                scheduleYuiGuideChatMessageFlush(100);
            } else {
                console.warn('[YuiGuide] Chat host was not ready; dropped guide chat messages:', _pendingYuiGuideChatMessages.length);
                _pendingYuiGuideChatMessages = [];
                _yuiGuideChatFlushAttempts = 0;
            }
            return;
        }

        _yuiGuideChatFlushAttempts = 0;
        var batch = _pendingYuiGuideChatMessages.splice(0);
        batch.forEach(function (message) {
            try {
                host.appendMessage(message);
            } catch (error) {
                console.warn('[YuiGuide] Failed to append guide chat message:', error);
            }
        });

        if (typeof host.openWindow === 'function') {
            try {
                host.openWindow();
            } catch (error) {
                console.warn('[YuiGuide] Failed to open guide chat window:', error);
            }
        }
    }

    function appendYuiGuideChatMessage(message) {
        if (!isStandaloneChatPage()) return;
        if (!message || typeof message !== 'object') return;
        _pendingYuiGuideChatMessages.push(message);
        scheduleYuiGuideChatMessageFlush(0);
    }

    function updatePendingYuiGuideChatMessage(messageId, patch) {
        var targetId = String(messageId || '');
        if (!targetId || !patch || typeof patch !== 'object') {
            return false;
        }

        var updated = false;
        _pendingYuiGuideChatMessages = _pendingYuiGuideChatMessages.map(function (message) {
            if (!message || String(message.id) !== targetId) {
                return message;
            }
            updated = true;
            return Object.assign({}, message, patch);
        });
        return updated;
    }

    function updateYuiGuideChatMessage(messageId, patch) {
        if (!isStandaloneChatPage()) return;
        if (!messageId || !patch || typeof patch !== 'object') return;

        var host = window.reactChatWindowHost;
        if (host && typeof host.updateMessage === 'function') {
            try {
                var handled = host.updateMessage(messageId, patch);
                if (handled) {
                    return;
                }
            } catch (error) {
                console.warn('[YuiGuide] Failed to update guide chat message:', error);
            }
        }

        if (updatePendingYuiGuideChatMessage(messageId, patch)) {
            scheduleYuiGuideChatMessageFlush(0);
        }
    }

    function clearYuiGuideChatMessages() {
        if (!isStandaloneChatPage()) return;
        _pendingYuiGuideChatMessages = [];
        if (_yuiGuideChatFlushTimer) {
            clearYuiGuideChatFlushTimer();
        }
        var host = window.reactChatWindowHost;
        if (host && typeof host.clearGuideMessages === 'function') {
            try {
                host.clearGuideMessages();
            } catch (error) {
                console.warn('[YuiGuide] Failed to clear guide chat messages:', error);
            }
        }
    }
    function handleYuiGuideChatBridgeData(data) {
        if (!data || !data.action) return false;
        switch (data.action) {
            case 'yui_guide_append_chat_message':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                appendYuiGuideChatMessage(data.message);
                return true;
            case 'yui_guide_update_chat_message':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                updateYuiGuideChatMessage(data.messageId, data.patch);
                return true;
            case 'yui_guide_clear_chat_messages':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                clearYuiGuideChatMessages();
                return true;
            case 'yui_guide_set_chat_input_locked':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                applyYuiGuideChatInputLocked(data.locked === true, data.reason || '');
                return true;
            case 'tutorial_chat_identity_override':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                applyTutorialChatIdentityOverride(data);
                return true;
            default:
                return false;
        }
    }

    function drainPendingYuiGuideChatBridgeQueue() {
        if (!isStandaloneChatPage()) return;
        var queue = [];
        try {
            var raw = localStorage.getItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
            var parsed = raw ? JSON.parse(raw) : [];
            queue = Array.isArray(parsed) ? parsed.filter(Boolean) : [];
            localStorage.removeItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
        } catch (error) {
            console.warn('[YuiGuide] 读取教程聊天消息缓存失败:', error);
            try {
                localStorage.removeItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
            } catch (_) {}
        }
        queue.forEach(function (message) {
            handleYuiGuideChatBridgeData(message);
        });
    }

    function handleYuiGuideChatBridgeStorageEvent(event) {
        if (!event || event.key !== YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY || !event.newValue) return;
        drainPendingYuiGuideChatBridgeQueue();
    }

    var _pendingIcebreakerBridgeActions = [];
    var _icebreakerBridgeFlushTimer = null;
    var _icebreakerBridgeFlushAttempts = 0;
    var ICEBREAKER_BRIDGE_FLUSH_MAX_ATTEMPTS = 50;

    function scheduleIcebreakerBridgeFlush(delay) {
        if (_icebreakerBridgeFlushTimer) return;
        _icebreakerBridgeFlushTimer = yuiGuideInterpageResources.setTimeout(
            flushPendingIcebreakerBridgeActions,
            typeof delay === 'number' ? delay : 0
        );
    }

    function clearIcebreakerBridgeFlushTimer() {
        if (!_icebreakerBridgeFlushTimer) return;
        yuiGuideInterpageResources.clearTimeout(_icebreakerBridgeFlushTimer);
        _icebreakerBridgeFlushTimer = null;
    }

    function queueIcebreakerBridgeAction(action) {
        if (!action || !action.type) return;
        _pendingIcebreakerBridgeActions.push(action);
        scheduleIcebreakerBridgeFlush(0);
    }

    function flushPendingIcebreakerBridgeActions() {
        _icebreakerBridgeFlushTimer = null;
        if (!_pendingIcebreakerBridgeActions.length) {
            _icebreakerBridgeFlushAttempts = 0;
            return;
        }

        var host = window.reactChatWindowHost;
        if (!host || typeof host.appendMessage !== 'function') {
            if (_icebreakerBridgeFlushAttempts < ICEBREAKER_BRIDGE_FLUSH_MAX_ATTEMPTS) {
                _icebreakerBridgeFlushAttempts += 1;
                scheduleIcebreakerBridgeFlush(100);
            } else {
                console.warn('[NewUserIcebreaker] Chat host was not ready; dropped bridge actions:', _pendingIcebreakerBridgeActions.length);
                _pendingIcebreakerBridgeActions = [];
                _icebreakerBridgeFlushAttempts = 0;
            }
            return;
        }

        _icebreakerBridgeFlushAttempts = 0;
        var batch = _pendingIcebreakerBridgeActions.splice(0);
        var shouldOpenHost = false;
        batch.forEach(function (action) {
            try {
                if (action.type === 'append' && action.message) {
                    shouldOpenHost = true;
                    return Promise.resolve(host.appendMessage(action.message)).then(function (result) {
                        if (!result) return result;
                        return waitForIcebreakerChatHostMounted(host).then(function () {
                            syncIcebreakerAssistantCompactCaption(action.message);
                            finalizeIcebreakerAssistantSubtitleTranslation(action.message);
                            return result;
                        });
                    }).catch(function (error) {
                        console.warn('[NewUserIcebreaker] Failed to append bridge message:', error);
                    });
                } else if (action.type === 'set_prompt' && action.prompt && typeof host.setIcebreakerChoicePrompt === 'function') {
                    host.setIcebreakerChoicePrompt(action.prompt);
                    shouldOpenHost = true;
                } else if (action.type === 'clear_prompt' && action.sessionId && typeof host.clearIcebreakerChoicePrompt === 'function') {
                    host.clearIcebreakerChoicePrompt(action.sessionId);
                } else if (action.type === 'clear_prompt_source'
                        && action.source === 'new_user_icebreaker'
                        && typeof host.clearChoicePromptBySource === 'function') {
                    host.clearChoicePromptBySource(action.source, action.reason || 'icebreaker-bridge');
                }
            } catch (error) {
                console.warn('[NewUserIcebreaker] Failed to apply bridge action:', action.type, error);
            }
        });
        if (shouldOpenHost && typeof host.openWindow === 'function') {
            try {
                host.openWindow();
            } catch (error) {
                console.warn('[NewUserIcebreaker] Failed to open chat host for bridge action:', error);
            }
        }
    }

    function appendIcebreakerChatMessage(message) {
        if (!isStandaloneChatPage()) return;
        queueIcebreakerBridgeAction({ type: 'append', message: message });
    }

    function setIcebreakerChoicePromptFromBroadcast(prompt) {
        if (!isStandaloneChatPage()) return;
        queueIcebreakerBridgeAction({ type: 'set_prompt', prompt: prompt });
    }

    function clearIcebreakerChoicePromptFromBroadcast(sessionId) {
        if (!isStandaloneChatPage()) return;
        queueIcebreakerBridgeAction({ type: 'clear_prompt', sessionId: String(sessionId || '') });
    }

    function clearIcebreakerChoicePromptSourceFromBroadcast(source, reason) {
        if (!isStandaloneChatPage()) return;
        if (String(source || '') !== 'new_user_icebreaker') return;
        queueIcebreakerBridgeAction({
            type: 'clear_prompt_source',
            source: String(source || ''),
            reason: String(reason || '')
        });
    }

    // Also defined in new-user-icebreaker.js for the local chat host path.
    function getIcebreakerMessageText(message) {
        var blocks = message && Array.isArray(message.blocks) ? message.blocks : [];
        for (var i = 0; i < blocks.length; i++) {
            if (blocks[i] && blocks[i].type === 'text') {
                var text = String(blocks[i].text || '').trim();
                if (text) return text;
            }
        }
        return '';
    }

    function syncIcebreakerAssistantCompactCaption(message) {
        if (!isStandaloneChatPage() || !message || message.role !== 'assistant') return;
        var line = getIcebreakerMessageText(message);
        if (!line) return;
        var turnId = String(message.turnId || message.id || ('icebreaker-turn-' + Date.now()));
        try {
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: {
                    turnId: turnId,
                    source: 'new_user_icebreaker'
                }
            }));
            window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
                detail: {
                    turnId: turnId,
                    segmentId: turnId + ':icebreaker',
                    text: line,
                    source: 'new_user_icebreaker'
                }
            }));
        } catch (error) {
            console.warn('[NewUserIcebreaker] compact caption sync failed:', error);
        }
    }

    function finalizeIcebreakerAssistantSubtitleTranslation(message) {
        if (!isStandaloneChatPage() || !message || message.role !== 'assistant') return;
        var line = getIcebreakerMessageText(message);
        if (!line) return;
        try {
            var bridge = window.subtitleBridge;
            if (!bridge || typeof bridge.finalizeTurnWithTranslation !== 'function') {
                return;
            }
            if (typeof bridge.beginTurn === 'function') {
                bridge.beginTurn({ latch: false });
            }
            var result = bridge.finalizeTurnWithTranslation(line);
            if (result && typeof result.catch === 'function') {
                result.catch(function (error) {
                    console.warn('[NewUserIcebreaker] subtitle translation failed:', error);
                });
            }
        } catch (error) {
            console.warn('[NewUserIcebreaker] subtitle translation failed:', error);
        }
    }

    function waitForIcebreakerChatHostMounted(host) {
        return new Promise(function (resolve) {
            var attempts = 0;
            function checkMounted() {
                var isMounted = false;
                try {
                    isMounted = !!(host && typeof host.isMounted === 'function' && host.isMounted());
                } catch (_) {}
                if (isMounted || attempts >= 100) {
                    yuiGuideInterpageResources.setTimeout(resolve, 0);
                    return;
                }
                attempts += 1;
                yuiGuideInterpageResources.setTimeout(checkMounted, 50);
            }
            checkMounted();
        });
    }

    function isIcebreakerBridgeAction(action) {
        return action === 'icebreaker_append_chat_message'
            || action === 'icebreaker_set_choice_prompt'
            || action === 'icebreaker_clear_choice_prompt'
            || action === 'icebreaker_clear_choice_prompt_source'
            || action === 'icebreaker_choice_selected'
            || action === 'icebreaker_free_text_submitted';
    }

    function isIcebreakerBridgeForCurrentLanlan(data) {
        if (!data || !data.lanlan_name) return false;
        var currentName = getCurrentLanlanName();
        return !!currentName && data.lanlan_name === currentName;
    }

    function handleIcebreakerBridgeData(data) {
        if (!data || !data.action) return false;
        if (!isIcebreakerBridgeForCurrentLanlan(data)) return false;
        switch (data.action) {
            case 'icebreaker_append_chat_message':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                appendIcebreakerChatMessage(data.message);
                return true;
            case 'icebreaker_set_choice_prompt':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                setIcebreakerChoicePromptFromBroadcast(data.prompt);
                return true;
            case 'icebreaker_clear_choice_prompt':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                clearIcebreakerChoicePromptFromBroadcast(data.sessionId);
                return true;
            case 'icebreaker_clear_choice_prompt_source':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                clearIcebreakerChoicePromptSourceFromBroadcast(data.source, data.reason);
                return true;
            case 'icebreaker_choice_selected':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                if (!isStandaloneChatPage()) {
                    window.dispatchEvent(new CustomEvent('neko:icebreaker-choice-selected', {
                        detail: data.detail || data
                    }));
                }
                return true;
            case 'icebreaker_free_text_submitted':
                if (isDuplicateMessage(data.action, data.timestamp)) return true;
                if (!isStandaloneChatPage()) {
                    window.dispatchEvent(new CustomEvent('neko:icebreaker-free-text-submitted', {
                        detail: data.detail || data
                    }));
                }
                return true;
            default:
                return false;
        }
    }

    function handleIcebreakerStorageBridgeEvent(event) {
        if (!event || event.key !== ICEBREAKER_BRIDGE_STORAGE_KEY || !event.newValue) return;
        try {
            handleIcebreakerBridgeData(JSON.parse(event.newValue));
        } catch (error) {
            console.warn('[NewUserIcebreaker] storage bridge parse failed:', error);
        }
    }

    function postIcebreakerBridgeEvent(action, payload) {
        var message = Object.assign({
            action: action,
            lanlan_name: getCurrentLanlanName(),
            timestamp: Date.now()
        }, payload || {});
        if (nekoBroadcastChannel && typeof nekoBroadcastChannel.postMessage === 'function') {
            try {
                nekoBroadcastChannel.postMessage(message);
            } catch (error) {
                console.warn('[NewUserIcebreaker] BroadcastChannel bridge post failed:', action, error);
            }
        }
        try {
            localStorage.setItem(ICEBREAKER_BRIDGE_STORAGE_KEY, JSON.stringify(message));
            setTimeout(function () {
                try {
                    localStorage.removeItem(ICEBREAKER_BRIDGE_STORAGE_KEY);
                } catch (_) {}
            }, 0);
        } catch (error) {
            console.warn('[NewUserIcebreaker] storage bridge post failed:', action, error);
        }
    }

    function postIcebreakerChoiceSelected(payload) {
        postIcebreakerBridgeEvent('icebreaker_choice_selected', payload || {});
    }

    function postIcebreakerFreeTextSubmitted(payload) {
        postIcebreakerBridgeEvent('icebreaker_free_text_submitted', payload || {});
    }

    function relayYuiGuideMessageToNative(target, message) {
        var bridge = window.nekoTutorialOverlay;
        if (!bridge || !message || typeof message !== 'object') {
            return false;
        }

        try {
            if (target === 'pet' && typeof bridge.relayToPet === 'function') {
                bridge.relayToPet(message);
                return true;
            }
            if (target === 'chat' && typeof bridge.relayToChat === 'function') {
                bridge.relayToChat(message);
                return true;
            }
        } catch (_) {}
        return false;
    }

    function getYuiGuideBridgeCommandBus() {
        if (yuiGuideBridgeCommandBus) {
            return yuiGuideBridgeCommandBus;
        }
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createTutorialBridgeCommandBus === 'function'
        ) {
            yuiGuideBridgeCommandBus = window.YuiGuideCommon.createTutorialBridgeCommandBus({
                window: window,
                channelProvider: function () {
                    return nekoBroadcastChannel || null;
                },
                nativeRelayProvider: function () {
                    return window.nekoTutorialOverlay || null;
                }
            });
        }
        return yuiGuideBridgeCommandBus;
    }

    function normalizeYuiGuideBridgeMessage(action, payload) {
        var message = action && typeof action === 'object'
            ? Object.assign({}, action)
            : Object.assign({}, payload || {}, {
                action: action
            });
        if (!message || !message.action) {
            return null;
        }
        if (!Number.isFinite(message.timestamp)) {
            message.timestamp = Date.now();
        }
        try {
            var canonicalRunId = resolveCanonicalYuiGuideBridgeRunId(message);
            if (canonicalRunId) {
                message.tutorialRunId = canonicalRunId;
                message.pcOverlayRunId = canonicalRunId;
            }
        } catch (_) {}
        return message;
    }

    function postYuiGuideMessageToChat(action, payload, options) {
        var bus = getYuiGuideBridgeCommandBus();
        var message = normalizeYuiGuideBridgeMessage(action, payload);
        if (!message) {
            return false;
        }
        if (bus && typeof bus.post === 'function') {
            return bus.post(message, options || {});
        }

        var posted = false;
        try {
            if (nekoBroadcastChannel && typeof nekoBroadcastChannel.postMessage === 'function') {
                nekoBroadcastChannel.postMessage(message);
                posted = true;
            }
        } catch (_) {}
        try {
            if (relayYuiGuideMessageToNative('chat', message)) {
                posted = true;
            }
        } catch (_) {}
        return posted;
    }

    function postYuiGuideMessageToPet(action, payload, options) {
        var bus = getYuiGuideBridgeCommandBus();
        if (bus && typeof bus.postToPet === 'function') {
            return bus.postToPet(action, payload, options || {});
        }

        var message = normalizeYuiGuideBridgeMessage(action, payload);
        if (!message) {
            return false;
        }
        var posted = false;
        try {
            if (nekoBroadcastChannel && typeof nekoBroadcastChannel.postMessage === 'function') {
                nekoBroadcastChannel.postMessage(message);
                posted = true;
            }
        } catch (_) {}
        try {
            if (relayYuiGuideMessageToNative('pet', message)) {
                posted = true;
            }
        } catch (_) {}
        return posted;
    }

    function handleYuiGuideRelayedMessage(message) {
        if (!message || !message.action) {
            return false;
        }
        if (isYuiGuideLifecycleStartAction(message.action)) {
            openYuiGuidePcOverlayLifecycle(message);
        }
        if (
            yuiGuidePcOverlayLifecycleClosed
            && isYuiGuideLifecycleScopedAction(message.action)
        ) {
            return true;
        }
        if (!isYuiGuideMessageForCurrentLifecycle(message)) {
            return true;
        }
        if (
            message.action !== 'yui_guide_tutorial_lifecycle_ended'
            && isYuiGuideLifecycleScopedAction(message.action)
            && isYuiGuidePcOverlayRunEnded(message.tutorialRunId)
        ) {
            clearYuiGuidePcOverlayBridgeState('stale-after-lifecycle-ended', message.tutorialRunId || '');
            return true;
        }
        if (message.tutorialRunId && message.action !== 'yui_guide_tutorial_lifecycle_ended') {
            rememberYuiGuidePcOverlayRunId(message.tutorialRunId);
        }

        switch (message.action) {
            case 'yui_guide_append_chat_message': {
                appendYuiGuideChatMessage(message.message);
                return true;
            }
            case 'yui_guide_update_chat_message': {
                updateYuiGuideChatMessage(message.messageId, message.patch);
                return true;
            }
            case 'yui_guide_clear_chat_messages': {
                clearYuiGuideChatMessages();
                return true;
            }
            case 'yui_guide_tutorial_lifecycle_ended': {
                clearYuiGuidePcOverlayBridgeState(message.reason || 'tutorial-ended', message.tutorialRunId || '');
                return true;
            }
            case 'yui_guide_set_chat_buttons_disabled': {
                if (!isStandaloneChatPage() || !document.body) return true;
                applyYuiGuideChatLockState(message.disabled !== false);
                return true;
            }
            case 'yui_guide_set_chat_input_locked': {
                if (!isStandaloneChatPage()) return true;
                applyYuiGuideChatInputLocked(message.locked === true, message.reason || '');
                return true;
            }
            case 'yui_guide_set_compact_chat_fixed_layout': {
                if (!isStandaloneChatPage()) return true;
                applyYuiGuideCompactChatFixedLayout(message.fixed === true);
                return true;
            }
            case 'yui_guide_set_chat_spotlight': {
                if (!isStandaloneChatPage() || !document.body) return true;
                ensureYuiGuideExternalChatExpanded();
                var preserveSpotlightDuringResistance = message.preserveDuringResistance === true;
                applyYuiGuideChatSpotlight(message.kind || '', {
                    variant: typeof message.variant === 'string' ? message.variant : '',
                    preserveDuringResistance: preserveSpotlightDuringResistance,
                    pcOverlayRunId: getYuiGuidePcOverlayRunIdFromMessage(message)
                });
                scheduleYuiGuideChatInputSpotlightRetry(message.kind || '', getYuiGuidePcOverlayRunIdFromMessage(message));
                return true;
            }
            case 'yui_guide_set_chat_cursor': {
                if (!isStandaloneChatPage() || !document.body) return true;
                var expandedForCursor = ensureYuiGuideExternalChatExpanded();
                var cursorRequestToken = ++yuiGuideChatCursorRequestToken;
                var cursorKind = message.kind || '';
                var cursorOptions = {
                    effect: message.effect || '',
                    effectDurationMs: Number.isFinite(message.effectDurationMs)
                        ? Math.max(0, Math.floor(message.effectDurationMs))
                        : 0,
                    durationMs: Number.isFinite(message.durationMs)
                        ? Math.max(0, Math.floor(message.durationMs))
                        : null,
                    targetIndex: Number.isFinite(message.targetIndex)
                        ? message.targetIndex
                        : 0,
                    freezePoint: message.freezePoint === true,
                    preservePcOverlayCursor: message.preservePcOverlayCursor === true,
                    pcOverlayRunId: getYuiGuidePcOverlayRunIdFromMessage(message),
                    timestamp: getYuiGuideBridgeMessageTimestamp(message)
                };
                applyYuiGuideChatCursor(cursorKind, cursorOptions);
                if (expandedForCursor && cursorOptions.freezePoint !== true) {
                    window.setTimeout(function () {
                        if (cursorRequestToken !== yuiGuideChatCursorRequestToken) {
                            return;
                        }
                        applyYuiGuideChatCursor(cursorKind, cursorOptions);
                    }, 720);
                }
                return true;
            }
            case 'yui_guide_chat_cursor_anchor': {
                if (isStandaloneChatPage()) return true;
                var anchorX = Number(message.x);
                var anchorY = Number(message.y);
                if (!Number.isFinite(anchorX) || !Number.isFinite(anchorY)) {
                    return true;
                }
                try {
                    window.localStorage.setItem(YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY, JSON.stringify({
                        x: anchorX,
                        y: anchorY,
                        kind: typeof message.kind === 'string' ? message.kind : '',
                        effect: typeof message.effect === 'string' ? message.effect : '',
                        source: message.source || 'external-chat',
                        settled: message.settled === true,
                        at: message.timestamp || Date.now()
                    }));
                } catch (_) {}
                window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                    detail: {
                        x: anchorX,
                        y: anchorY,
                        kind: typeof message.kind === 'string' ? message.kind : '',
                        effect: typeof message.effect === 'string' ? message.effect : '',
                        source: message.source || 'external-chat',
                        settled: message.settled === true,
                        timestamp: message.timestamp || Date.now()
                    }
                }));
                return true;
            }
            case 'yui_guide_set_avatar_tool_menu_open': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideAvatarToolMenuOpen(message.open === true, message.reason || '');
                return true;
            }
            case 'yui_guide_click_avatar_tool_button': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                clickYuiGuideAvatarToolButton(message.reason || '');
                return true;
            }
            case 'yui_guide_set_compact_history_open': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideCompactHistoryOpen(message.open === true, message.reason || '');
                return true;
            }
            case 'yui_guide_set_compact_tool_fan_open': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideCompactToolFanOpen(message.open === true, message.reason || '');
                return true;
            }
            case 'yui_guide_rotate_compact_tool_wheel': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideCompactToolWheelRotate(message);
                return true;
            }
            case 'yui_guide_set_compact_tool_wheel_index': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideCompactToolWheelIndex(message);
                return true;
            }
            case 'yui_guide_drag_chat_cursor': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideChatCursorDrag(message.kind || '', {
                    deltaX: Number(message.deltaX || 0),
                    deltaY: Number(message.deltaY || 0),
                    durationMs: Number.isFinite(Number(message.durationMs)) ? Number(message.durationMs) : undefined,
                    effect: message.effect || '',
                    effectDurationMs: Number(message.effectDurationMs || 0),
                    targetIndex: Number(message.targetIndex || 0)
                });
                return true;
            }
            case 'yui_guide_arc_chat_cursor': {
                if (!isStandaloneChatPage()) return true;
                ensureYuiGuideExternalChatExpanded();
                applyYuiGuideChatCursorArc(message.kind || '', {
                    direction: Number(message.direction) < 0 ? -1 : 1,
                    fraction: Number.isFinite(Number(message.fraction)) ? Number(message.fraction) : 0.2,
                    durationMs: Number.isFinite(Number(message.durationMs)) ? Number(message.durationMs) : undefined,
                    effect: message.effect || '',
                    effectDurationMs: Number(message.effectDurationMs || 0),
                    targetIndex: Number(message.targetIndex || 0),
                    timestamp: getYuiGuideBridgeMessageTimestamp(message)
                });
                return true;
            }
            case 'yui_guide_chat_ready': {
                if (isStandaloneChatPage()) return true;
                window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-ready', {
                    detail: {
                        timestamp: message.timestamp || Date.now()
                    }
                }));
                return true;
            }
            case 'yui_guide_request_termination': {
                window.dispatchEvent(new CustomEvent('neko:yui-guide:remote-termination-request', {
                    detail: {
                        sourcePage: message.sourcePage || '',
                        targetPage: message.targetPage || '',
                        reason: message.reason || 'skip',
                        tutorialReason: message.tutorialReason || message.reason || 'skip',
                        timestamp: message.timestamp || Date.now()
                    }
                }));
                return true;
            }
            default:
                return false;
        }
    }

    yuiGuideInterpageResources.addEventListener(window, 'neko:tutorial-overlay-relay', function (event) {
        var message = event && event.detail;
        if (
            message
            && !shouldBypassYuiGuideMessageDedup(message.action, message)
            && isDuplicateMessage(message.action, message.timestamp)
        ) {
            return;
        }
        handleYuiGuideRelayedMessage(message);
    });

    yuiGuideInterpageResources.addEventListener(window, 'message', function (event) {
        var data = event && event.data;
        if (!data || data.__nekoTutorialOverlayRelay !== true) {
            return;
        }
        if (event.origin !== window.location.origin) {
            return;
        }
        var message = data.payload;
        if (
            message
            && !shouldBypassYuiGuideMessageDedup(message.action, message)
            && isDuplicateMessage(message.action, message.timestamp)
        ) {
            return;
        }
        handleYuiGuideRelayedMessage(message);
    });

    yuiGuideInterpageResources.addEventListener(window, 'storage', handleIcebreakerStorageBridgeEvent);
    yuiGuideInterpageResources.addEventListener(window, 'storage', handleYuiGuideChatBridgeStorageEvent);

    try {
        if (typeof BroadcastChannel !== 'undefined') {
            nekoBroadcastChannel = new BroadcastChannel('neko_page_channel');
            console.log('[BroadcastChannel] 主页面 BroadcastChannel 已初始化');

            nekoBroadcastChannel.onmessage = async function (event) {
                var message = event.data;
                if (!message || !message.action) {
                    return;
                }

                // Deduplicate: same message arrives via both BC and postMessage
                if (
                    !isIcebreakerBridgeAction(message.action)
                    &&
                    !shouldBypassYuiGuideMessageDedup(message.action, message)
                    && isDuplicateMessage(message.action, message.timestamp)
                ) {
                    console.log('[BroadcastChannel] 跳过重复消息:', message.action);
                    return;
                }

                if (isYuiGuideLifecycleStartAction(message.action)) {
                    openYuiGuidePcOverlayLifecycle(message);
                }
                if (
                    yuiGuidePcOverlayLifecycleClosed
                    && isYuiGuideLifecycleScopedAction(message.action)
                ) {
                    return;
                }
                if (!isYuiGuideMessageForCurrentLifecycle(message)) {
                    return;
                }

                if (
                    message.action !== 'yui_guide_tutorial_lifecycle_ended'
                    && isYuiGuideLifecycleScopedAction(message.action)
                    && isYuiGuidePcOverlayRunEnded(message.tutorialRunId)
                ) {
                    clearYuiGuidePcOverlayBridgeState('stale-after-lifecycle-ended', message.tutorialRunId || '');
                    return;
                }

                if (!isHighVolumeBroadcastChannelAction(message.action)) {
                    console.log('[BroadcastChannel] 收到消息:', message.action);
                }

                switch (event.data.action) {
                    case 'reload_model':
                        await handleModelReload(event.data?.lanlan_name, event.data?.reloadOptions);
                        break;
                    case 'catgirl_switched': {
                        // 兜底：character_card_manager 切角色后用 BroadcastChannel 通知主窗口热切换。
                        // 后端的 catgirl_switched WebSocket 只送到有活跃 session 的连接，
                        // 主窗口未启动 session 时会沉默；这里独立兜底。handleCatgirlSwitch 自带去重。
                        const newCatgirl = event.data.new_catgirl || '';
                        const oldCatgirl = event.data.old_catgirl || '';
                        if (!newCatgirl) break;
                        const currentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (newCatgirl === currentName) break;
                        if (typeof window.handleCatgirlSwitch === 'function') {
                            window.handleCatgirlSwitch(newCatgirl, oldCatgirl);
                        }
                        break;
                    }
                    case 'memory_edited':
                        await handleMemoryEdited(event.data.catgirl_name);
                        break;
                    case 'voice_chat_active': {
                        // 来自另一个窗口的语音对话状态变更，同步本地 React composer 隐藏状态
                        // 校验 lanlan_name：多角色场景下避免串状态
                        handleVoiceChatComposerHiddenMessage(event.data);
                        break;
                    }
                    case 'goodbye_chat_composer_hidden': {
                        handleGoodbyeChatComposerHiddenMessage(event.data, 'broadcast');
                        break;
                    }
                    case 'request_goodbye_chat_composer_hidden': {
                        handleGoodbyeChatComposerHiddenMessage(event.data, 'broadcast-request');
                        break;
                    }
                    case 'idle_activity': {
                        var idleCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!idleCurrentName || event.data.lanlan_name !== idleCurrentName)) break;
                        dispatchCrossWindowIdleActivity({
                            source: event.data.source || 'interaction',
                            kind: event.data.kind === 'conversation' ? 'conversation' : 'interaction',
                            via: 'broadcast-channel',
                            timestamp: event.data.timestamp || Date.now()
                        });
                        break;
                    }
                    case 'idle_return_ball_state': {
                        var idleReturnCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!idleReturnCurrentName || event.data.lanlan_name !== idleReturnCurrentName)) break;
                        dispatchIdleReturnBallState(event.data);
                        break;
                    }
                    case 'idle_chat_minimized_state': {
                        var idleChatCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!idleChatCurrentName || event.data.lanlan_name !== idleChatCurrentName)) break;
                        dispatchIdleChatMinimizedState(event.data);
                        break;
                    }
                    case 'idle_chat_compact_surface_state': {
                        var compactSurfaceCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!compactSurfaceCurrentName || event.data.lanlan_name !== compactSurfaceCurrentName)) break;
                        dispatchIdleChatCompactSurfaceState(event.data);
                        break;
                    }
                    case 'idle_cat1_compact_mirror_state': {
                        var cat1MirrorCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!cat1MirrorCurrentName || event.data.lanlan_name !== cat1MirrorCurrentName)) break;
                        dispatchIdleCat1CompactMirrorState(event.data);
                        break;
                    }
                    case 'idle_cat1_play_yarn_visibility': {
                        var cat1PlayYarnCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!cat1PlayYarnCurrentName || event.data.lanlan_name !== cat1PlayYarnCurrentName)) break;
                        dispatchIdleCat1PlayYarnVisibility(event.data);
                        break;
                    }
                    case 'idle_chat_pair_move_bounds': {
                        var pairMoveChatCurrentName = getCurrentLanlanName();
                        if (event.data.lanlan_name && (!pairMoveChatCurrentName || event.data.lanlan_name !== pairMoveChatCurrentName)) break;
                        dispatchIdleChatPairMoveBounds(event.data);
                        break;
                    }
                    case 'voice_config_switching': {
                        handleVoiceConfigSwitchingMessage(event.data);
                        break;
                    }
                    case 'icebreaker_append_chat_message':
                    case 'icebreaker_set_choice_prompt':
                    case 'icebreaker_clear_choice_prompt':
                    case 'icebreaker_choice_selected':
                    case 'icebreaker_free_text_submitted': {
                        handleIcebreakerBridgeData(event.data);
                        break;
                    }
                    case 'yui_guide_append_chat_message': {
                        appendYuiGuideChatMessage(event.data.message);
                        break;
                    }
                    case 'yui_guide_update_chat_message': {
                        updateYuiGuideChatMessage(event.data.messageId, event.data.patch);
                        break;
                    }
                    case 'yui_guide_clear_chat_messages': {
                        clearYuiGuideChatMessages();
                        break;
                    }
                    case 'avatar_updated': {
                        // 从 Pet 窗口接收头像数据，注入到 Chat 窗口
                        // 校验 lanlan_name：多角色场景下避免串头像
                        // 本地角色名未就绪时也跳过，等 config 注入后由 request_avatar 回填
                        const currentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!currentName || event.data.lanlan_name !== currentName)) break;
                        const incomingDataUrl = event.data.dataUrl || '';
                        const incomingModelType = event.data.modelType || '';
                        if (window.appChatAvatar && typeof window.appChatAvatar.setExternalAvatar === 'function') {
                            window.appChatAvatar.setExternalAvatar(incomingDataUrl, incomingModelType);
                        } else if (incomingDataUrl) {
                            window.__nekoPendingAvatar = { dataUrl: incomingDataUrl, modelType: incomingModelType };
                        }
                        break;
                    }
                    case 'tutorial_chat_identity_override': {
                        applyTutorialChatIdentityOverride(event.data);
                        break;
                    }
                    case 'request_tutorial_chat_identity': {
                        if (isStandaloneChatPage()) break;
                        if (window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__) {
                            postYuiGuideMessageToChat(
                                'tutorial_chat_identity_override',
                                window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__
                            );
                        }
                        break;
                    }
                    case 'request_avatar': {
                        // 仅 Pet 主窗口（/index）应答，Chat 窗口不回传
                        if (isStandaloneChatPage()) break;
                        // 校验 lanlan_name：与 avatar_updated 对称，本地名未就绪或不匹配时不回包
                        const reqCurrentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!reqCurrentName || event.data.lanlan_name !== reqCurrentName)) break;
                        if (window.appChatAvatar && typeof window.appChatAvatar.getCachedPreview === 'function') {
                            const cached = window.appChatAvatar.getCachedPreview();
                            if (cached && cached.dataUrl) {
                                postYuiGuideMessageToChat('avatar_updated', {
                                    lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
                                    dataUrl: cached.dataUrl,
                                    modelType: cached.modelType || ''
                                });
                            }
                        }
                        break;
                    }
                    case 'handoff_consumed': {
                        // 目标页面消费了 handoff token，转发为 DOM 事件
                        window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-consumed', {
                            detail: event.data.detail || {}
                        }));
                        break;
                    }
                    case 'handoff_sent': {
                        // 其他标签页发出了 handoff-sent，转发为本地 DOM 事件
                        _isRelayingYuiGuideHandoffSent = true;
                        try {
                            window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-sent', {
                                detail: event.data.detail || {}
                            }));
                        } finally {
                            _isRelayingYuiGuideHandoffSent = false;
                        }
                        break;
                    }
                    case 'yui_guide_set_chat_buttons_disabled': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideChatLockState(event.data.disabled !== false);
                        break;
                    }
                    case 'yui_guide_set_chat_input_locked': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideChatInputLocked(event.data.locked === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_set_compact_chat_fixed_layout': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideCompactChatFixedLayout(event.data.fixed === true);
                        break;
                    }
                    case 'yui_guide_set_chat_cursor':
                    case 'yui_guide_drag_chat_cursor':
                    case 'yui_guide_arc_chat_cursor': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        var cursorRunId = getYuiGuidePcOverlayRunIdFromMessage(event.data);
                        relayYuiGuideChatCommand(Object.assign({}, event.data, {
                            pcOverlayRunId: cursorRunId
                        }));
                        break;
                    }
                    case 'yui_guide_set_compact_history_open': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideCompactHistoryOpen(event.data.open === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_set_chat_spotlight': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        var preserveSpotlightDuringResistance = event.data.preserveDuringResistance === true;
                        var spotlightRunId = getYuiGuidePcOverlayRunIdFromMessage(event.data);
                        applyYuiGuideChatSpotlight(event.data.kind || '', {
                            variant: typeof event.data.variant === 'string' ? event.data.variant : '',
                            preserveDuringResistance: preserveSpotlightDuringResistance,
                            pcOverlayRunId: spotlightRunId
                        });
                        scheduleYuiGuideChatInputSpotlightRetry(event.data.kind || '', spotlightRunId);
                        break;
                    }
                    case 'yui_guide_set_avatar_tool_menu_open': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideAvatarToolMenuOpen(event.data.open === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_set_compact_tool_fan_open': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideCompactToolFanOpen(event.data.open === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_rotate_compact_tool_wheel': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideCompactToolWheelRotate(event.data);
                        break;
                    }
                    case 'yui_guide_set_compact_tool_wheel_index': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        applyYuiGuideCompactToolWheelIndex(event.data);
                        break;
                    }
                    case 'yui_guide_chat_ready': {
                        if (isStandaloneChatPage()) break;
                        window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-ready', {
                            detail: {
                                timestamp: event.data.timestamp || Date.now()
                            }
                        }));
                        break;
                    }
                    case 'yui_guide_request_termination': {
                        window.dispatchEvent(new CustomEvent('neko:yui-guide:remote-termination-request', {
                            detail: {
                                sourcePage: event.data.sourcePage || '',
                                targetPage: event.data.targetPage || '',
                                reason: event.data.reason || 'skip',
                                tutorialReason: event.data.tutorialReason || 'skip',
                                timestamp: event.data.timestamp || Date.now()
                            }
                        }));
                        break;
                    }
                    case 'yui_guide_tutorial_lifecycle_ended': {
                        if (!isStandaloneChatPage() || !document.body) break;
                        clearYuiGuidePcOverlayBridgeState(event.data.reason || '', event.data.tutorialRunId || '');
                        break;
                    }
                    case 'request_avatar_capture': {
                        if (isStandaloneChatPage()) break;
                        var captureLanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!captureLanlanName || event.data.lanlan_name !== captureLanlanName)) break;
                        var captureRequestId = event.data.requestId || '';
                        var includeSource = !!event.data.includeSourceDataUrl;
                        if (window.avatarPortrait && typeof window.avatarPortrait.capture === 'function') {
                            window.avatarPortrait.capture({
                                width: 320, height: 320, padding: 0.035,
                                shape: 'rounded', radius: 40,
                                background: 'rgba(255, 255, 255, 0.96)',
                                includeDataUrl: true,
                                includeSourceDataUrl: includeSource
	                            }).then(function (result) {
	                                postYuiGuideMessageToChat('avatar_capture_result', {
	                                    requestId: captureRequestId,
	                                    dataUrl: result.dataUrl || '',
	                                    modelType: result.modelType || '',
	                                    sourceDataUrl: includeSource ? (result.sourceDataUrl || '') : '',
	                                    cropRectPixels: result.cropRectPixels || null
	                                });
	                            }).catch(function (err) {
	                                console.error('[BroadcastChannel] avatar capture failed:', err);
	                                postYuiGuideMessageToChat('avatar_capture_result', {
	                                    requestId: captureRequestId,
	                                    error: true
	                                });
	                            });
	                        } else {
	                            postYuiGuideMessageToChat('avatar_capture_result', {
	                                requestId: captureRequestId,
	                                error: true
	                            });
	                        }
                        break;
                    }
                }
            };
        }
    } catch (e) {
        console.log('[BroadcastChannel] 初始化失败，将使用 postMessage 后备方案:', e);
    }

    bindStandaloneChatIdleActivityRelay();
    drainPendingYuiGuideChatBridgeQueue();

    var yuiGuideStandaloneInteractionShield = null;
    var yuiGuideStandaloneInteractionShieldBlocker = null;
    var yuiGuideStandaloneGlobalInteractionShieldInstalled = false;
    var yuiGuideStandaloneInteractionShieldEvents = [
        'pointerdown',
        'pointerup',
        'pointermove',
        'mousedown',
        'mouseup',
        'mousemove',
        'click',
        'dblclick',
        'contextmenu',
        'touchstart',
        'touchmove',
        'touchend',
        'wheel',
        'dragstart'
    ];

    function isYuiGuideStandaloneSkipTarget(target) {
        var element = target && typeof target.closest === 'function'
            ? target
            : target && target.parentElement && typeof target.parentElement.closest === 'function'
            ? target.parentElement
            : null;
        return !!(
            element
            && element.closest('#neko-tutorial-skip-btn, [data-yui-skip-control], [data-yui-emergency-exit]')
        );
    }

    function isYuiGuideStandaloneMovementEvent(event) {
        return !!(
            event
            && (
                event.type === 'pointermove'
                || event.type === 'mousemove'
                || event.type === 'touchmove'
            )
        );
    }

    function blockYuiGuideStandaloneInteraction(event) {
        if (!event || isYuiGuideStandaloneSkipTarget(event.target || null)) {
            return;
        }
        if (isYuiGuideStandaloneMovementEvent(event)) {
            return;
        }
        if (event.isTrusted === false) {
            return;
        }
        if (typeof event.preventDefault === 'function' && event.cancelable !== false) {
            event.preventDefault();
        }
        if (typeof event.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        }
        if (typeof event.stopPropagation === 'function') {
            event.stopPropagation();
        }
    }

    function setYuiGuideStandaloneGlobalInteractionShieldEnabled(enabled) {
        var shouldEnable = enabled === true;
        if (shouldEnable && yuiGuideStandaloneGlobalInteractionShieldInstalled) {
            return;
        }
        if (!shouldEnable && !yuiGuideStandaloneGlobalInteractionShieldInstalled) {
            return;
        }
        if (!yuiGuideStandaloneInteractionShieldBlocker) {
            yuiGuideStandaloneInteractionShieldBlocker = blockYuiGuideStandaloneInteraction;
        }
        yuiGuideStandaloneInteractionShieldEvents.forEach(function (type) {
            var options = type.indexOf('touch') === 0 || type === 'wheel'
                ? { capture: true, passive: false }
                : true;
            if (shouldEnable) {
                window.addEventListener(type, yuiGuideStandaloneInteractionShieldBlocker, options);
            } else {
                window.removeEventListener(type, yuiGuideStandaloneInteractionShieldBlocker, options);
            }
        });
        yuiGuideStandaloneGlobalInteractionShieldInstalled = shouldEnable;
    }

    function ensureYuiGuideStandaloneInteractionShield() {
        if (yuiGuideStandaloneInteractionShield && yuiGuideStandaloneInteractionShield.isConnected) {
            return yuiGuideStandaloneInteractionShield;
        }
        if (!document.body) {
            return null;
        }

        var shield = document.getElementById('yui-guide-standalone-interaction-shield');
        if (!shield) {
            shield = document.createElement('div');
            shield.id = 'yui-guide-standalone-interaction-shield';
            shield.setAttribute('aria-hidden', 'true');
            shield.setAttribute('data-yui-cursor-hidden', 'true');
            shield.style.position = 'fixed';
            shield.style.inset = '0';
            shield.style.zIndex = '2147483001';
            shield.style.background = 'transparent';
            shield.style.pointerEvents = 'auto';
            shield.style.touchAction = 'none';
            shield.style.userSelect = 'none';
            document.body.appendChild(shield);
        }

        if (!yuiGuideStandaloneInteractionShieldBlocker) {
            yuiGuideStandaloneInteractionShieldBlocker = blockYuiGuideStandaloneInteraction;
        }
        if (!shield.__yuiGuideStandaloneInteractionShieldInstalled) {
            yuiGuideStandaloneInteractionShieldEvents.forEach(function (type) {
                var options = type.indexOf('touch') === 0 || type === 'wheel'
                    ? { capture: true, passive: false }
                    : true;
                shield.addEventListener(type, yuiGuideStandaloneInteractionShieldBlocker, options);
            });
            shield.__yuiGuideStandaloneInteractionShieldInstalled = true;
        }
        yuiGuideStandaloneInteractionShield = shield;
        return shield;
    }

    function setYuiGuideStandaloneInteractionShieldEnabled(enabled) {
        var shouldEnable = enabled === true;
        if (!shouldEnable) {
            if (yuiGuideStandaloneInteractionShield) {
                yuiGuideStandaloneInteractionShield.hidden = true;
                yuiGuideStandaloneInteractionShield.style.pointerEvents = 'none';
            }
            if (document.body) {
                document.body.classList.remove('yui-guide-standalone-input-shield-active');
            }
            setYuiGuideStandaloneGlobalInteractionShieldEnabled(false);
            return;
        }

        var shield = ensureYuiGuideStandaloneInteractionShield();
        if (!shield) {
            return;
        }
        shield.hidden = false;
        shield.style.pointerEvents = 'auto';
        if (document.body) {
            document.body.classList.add('yui-guide-standalone-input-shield-active');
        }
        setYuiGuideStandaloneGlobalInteractionShieldEnabled(true);
    }

    function applyYuiGuideChatLockState(disabled) {
        if (!document.body) {
            return;
        }

        var locked = disabled !== false;
        document.body.classList.remove('yui-guide-chat-buttons-disabled');
        setYuiGuideStandaloneInteractionShieldEnabled(locked);

        var activeElement = document.activeElement;
        if (
            locked
            && activeElement
            && typeof activeElement.closest === 'function'
            && activeElement.closest('#react-chat-window-shell, #text-input-area')
            && typeof activeElement.blur === 'function'
        ) {
            activeElement.blur();
        }
    }

    function getReactChatWindowHost() {
        return window.reactChatWindowHost || null;
    }

    function ensureYuiGuideExternalChatExpanded() {
        var host = getReactChatWindowHost();
        if (!host || typeof host.openWindow !== 'function') {
            return false;
        }
        try {
            host.openWindow();
            return true;
        } catch (error) {
            console.warn('[YuiGuide] Failed to open external chat host:', error);
            return false;
        }
    }

    function relayYuiGuideChatCommand(data) {
        var detail = data && typeof data === 'object' ? Object.assign({}, data) : {};
        window.dispatchEvent(new CustomEvent('neko:tutorial-overlay-relay', { detail: detail }));
        if (window.parent && window.parent !== window) {
            try {
                window.parent.postMessage({
                    action: '__nekoTutorialOverlayRelay',
                    detail: detail
                }, window.location.origin);
            } catch (e) {
                // Parent relay is best-effort; the local DOM event is the primary path.
            }
        }
    }

    function applyYuiGuideChatInputLocked(locked, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setHomeTutorialInputLocked === 'function') {
            host.setHomeTutorialInputLocked(locked === true, reason || 'externalized-chat-guide');
        }
    }

    function applyYuiGuideCompactChatFixedLayout(fixed) {
        if (!document.body) {
            return;
        }
        document.body.classList.toggle('yui-guide-compact-chat-fixed', fixed === true);
    }

    function applyYuiGuideCompactHistoryOpen(open, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setCompactHistoryOpen === 'function') {
            host.setCompactHistoryOpen(open === true, reason || 'external-yui-guide');
        }
    }

    function applyYuiGuideAvatarToolMenuOpen(open, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setAvatarToolMenuOpen === 'function') {
            host.setAvatarToolMenuOpen(open === true, reason || 'externalized-chat-guide');
        }
    }

    function applyYuiGuideCompactToolFanOpen(open, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setCompactToolFanOpen === 'function') {
            host.setCompactToolFanOpen(open === true, reason || 'externalized-chat-guide');
        }
    }

    function applyYuiGuideCompactToolWheelRotate(payload) {
        var host = getReactChatWindowHost();
        if (!host || typeof host.rotateCompactToolWheel !== 'function') return;
        host.rotateCompactToolWheel(payload && payload.direction, payload && payload.stepCount, {
            reason: payload && payload.reason,
            forceFast: !payload || payload.forceFast !== false
        });
    }

    function applyYuiGuideCompactToolWheelIndex(payload) {
        var host = getReactChatWindowHost();
        if (!host || typeof host.setCompactToolWheelIndex !== 'function') return;
        host.setCompactToolWheelIndex(payload && payload.index, payload && payload.reason);
    }

    function isStandaloneChatPage() {
        var pathname = (window.location && window.location.pathname) || '';
        return pathname === '/chat' || pathname === '/chat/' || pathname === '/chat_full' || pathname === '/chat_full/';
    }

    function dispatchCrossWindowIdleActivity(detail) {
        window.dispatchEvent(new CustomEvent('neko:cross-window-user-activity', {
            detail: Object.assign({
                source: '',
                kind: 'interaction',
                via: 'broadcast-channel',
                timestamp: Date.now()
            }, detail || {})
        }));
    }

    function dispatchIdleReturnBallState(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-return-ball-state', {
            detail: Object.assign({
                action: 'idle_return_ball_state',
                source: '',
                reason: '',
                visible: false,
                tier: 'none',
                screenRect: null,
                timestamp: Date.now()
            }, detail || {})
        }));
    }

    function dispatchIdleChatMinimizedState(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-chat-minimized-state', {
            detail: Object.assign({
                action: 'idle_chat_minimized_state',
                source: '',
                reason: '',
                minimized: false,
                screenRect: null,
                timestamp: Date.now(),
                via: 'broadcast-channel'
            }, detail || {}, {
                via: 'broadcast-channel'
            })
        }));
    }

    function dispatchIdleChatCompactSurfaceState(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-chat-compact-surface-state', {
            detail: Object.assign({
                action: 'idle_chat_compact_surface_state',
                source: '',
                reason: '',
                visible: false,
                screenRect: null,
                timestamp: Date.now(),
                via: 'broadcast-channel'
            }, detail || {}, {
                via: 'broadcast-channel'
            })
        }));
    }

    function dispatchIdleCat1CompactMirrorState(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-compact-mirror-state', {
            detail: Object.assign({
                action: 'idle_cat1_compact_mirror_state',
                source: '',
                reason: '',
                active: false,
                surfaceScreenRect: null,
                anchorRatio: null,
                catRect: null,
                timestamp: Date.now(),
                via: 'broadcast-channel'
            }, detail || {}, {
                via: 'broadcast-channel'
            })
        }));
    }

    function dispatchIdleCat1PlayYarnVisibility(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-play-yarn-visibility', {
            detail: Object.assign({
                action: 'idle_cat1_play_yarn_visibility',
                source: '',
                hidden: false,
                timestamp: Date.now(),
                via: 'broadcast-channel'
            }, detail || {}, {
                via: 'broadcast-channel'
            })
        }));
    }

    function dispatchIdleChatPairMoveBounds(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-chat-pair-move-bounds', {
            detail: Object.assign({
                action: 'idle_chat_pair_move_bounds',
                source: '',
                screenRect: null,
                timestamp: Date.now(),
                via: 'broadcast-channel'
            }, detail || {}, {
                via: 'broadcast-channel'
            })
        }));
    }

    function broadcastCrossWindowIdleActivity(source, kind) {
        if (!isStandaloneChatPage()) return;

        var now = Date.now();
        if (now - _lastCrossWindowIdleActivityAt < CROSS_WINDOW_IDLE_ACTIVITY_MIN_INTERVAL_MS) {
            return;
        }
        _lastCrossWindowIdleActivityAt = now;

        var payload = {
            action: 'idle_activity',
            source: source || 'interaction',
            kind: kind === 'conversation' ? 'conversation' : 'interaction',
            lanlan_name: getCurrentLanlanName(),
            timestamp: now
        };

        postInterpageMessage(payload, { openerFallback: true });
    }

    function bindStandaloneChatIdleActivityRelay() {
        if (!isStandaloneChatPage()) return;

        document.addEventListener('pointerdown', function () {
            broadcastCrossWindowIdleActivity('pointerdown');
        }, true);
        document.addEventListener('keydown', function () {
            broadcastCrossWindowIdleActivity('keydown');
        }, true);
        document.addEventListener('touchstart', function () {
            broadcastCrossWindowIdleActivity('touchstart');
        }, { capture: true, passive: true });
        document.addEventListener('wheel', function () {
            broadcastCrossWindowIdleActivity('wheel');
        }, { capture: true, passive: true });
        window.addEventListener('neko:user-content-sent', function () {
            broadcastCrossWindowIdleActivity('user-content-sent', 'conversation');
        });
        window.addEventListener('neko:voice-session-started', function () {
            broadcastCrossWindowIdleActivity('voice-session-started', 'conversation');
        });
    }

    var yuiGuideChatSpotlightKind = '';
    var yuiGuideChatSpotlightTimer = 0;
    var YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY = 'neko_yui_guide_external_chat_cursor_screen_point_v1';
    var YUI_GUIDE_PC_OVERLAY_SEQUENCE_KEY = 'yuiGuidePcOverlaySequence';
    var yuiGuidePcOverlayActive = false;
    var yuiGuidePcOverlayReady = false;
    var yuiGuidePcOverlayRunIdOverride = '';
    var yuiGuidePcOverlayEndedRunId = '';
    var yuiGuidePcOverlayLifecycleEpoch = 0;
    var yuiGuidePcOverlayLifecycleClosed = false;
    var yuiGuidePcOverlayLifecycleRunId = '';
    var yuiGuidePcOverlaySequence = 0;
    var yuiGuidePcOverlaySpotlights = [];
    var yuiGuidePcOverlayCursor = null;
    var yuiGuideChatSpotlightLastPcKind = '';
    var yuiGuideChatSpotlightLastPcVariant = '';
    var yuiGuideChatSpotlightLastPcRects = [];
    var yuiGuideChatSpotlightPcOverlayRunId = '';
    var yuiGuideChatSpotlightVariant = '';
    var yuiGuideChatCursorRequestToken = 0;
    var yuiGuideChatCursorArcRequestToken = 0;
    var yuiGuideChatCursorPoint = null;
    var yuiGuideChatCursorFrozenScreenPoints = Object.create(null);
    var yuiGuideCompactToolWheelRotateRetryToken = 0;

    function getYuiGuideChatSpotlightElement(createIfMissing) {
        return getYuiGuideChatSpotlightElementWithCreateFlag(createIfMissing);
    }

    function getYuiGuideChatSpotlightElementWithCreateFlag(createIfMissing) {
        var existing = document.getElementById('yui-guide-chat-spotlight');
        if (!existing && isYuiGuidePcOverlayAvailable() && createIfMissing !== true) {
            return null;
        }
        if (existing || createIfMissing === false || typeof document === 'undefined' || !document.body) {
            return existing;
        }
        var spotlight = document.createElement('div');
        spotlight.id = 'yui-guide-chat-spotlight';
        spotlight.hidden = true;
        spotlight.setAttribute('aria-hidden', 'true');
        spotlight.style.position = 'fixed';
        spotlight.style.pointerEvents = 'none';
        spotlight.style.zIndex = '2147483000';
        spotlight.style.boxSizing = 'border-box';
        spotlight.style.border = '2px solid rgba(39, 89, 228, 0.98)';
        spotlight.style.boxShadow = '0 0 0 9999px rgba(8, 12, 28, 0.18), 0 0 24px rgba(39, 89, 228, 0.38)';
        spotlight.style.transition = 'left 120ms ease, top 120ms ease, width 120ms ease, height 120ms ease';
        document.body.appendChild(spotlight);
        return spotlight;
    }

    function getYuiGuidePcOverlayHost() {
        var host = window.nekoTutorialOverlay;
        if (!host || typeof host.update !== 'function' || typeof host.getWindowMetricsSync !== 'function') {
            return null;
        }
        return host;
    }

    function isYuiGuidePcOverlayAvailable() {
        return !!getYuiGuidePcOverlayHost();
    }

    function getYuiGuidePcOverlayRunId() {
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (storedRunId) {
            if (storedRunId !== yuiGuidePcOverlayRunIdOverride) {
                yuiGuidePcOverlayRunIdOverride = storedRunId;
                yuiGuidePcOverlayActive = false;
                yuiGuidePcOverlayReady = false;
            }
            return storedRunId;
        }
        if (yuiGuidePcOverlayRunIdOverride) {
            if (isYuiGuidePcOverlayRunEnded(yuiGuidePcOverlayRunIdOverride)) {
                yuiGuidePcOverlayRunIdOverride = '';
            } else {
                return yuiGuidePcOverlayRunIdOverride;
            }
        }
        try {
            var nextRunId = 'yui-guide-chat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
            window.localStorage.setItem('yuiGuidePcOverlayRunId', nextRunId);
            yuiGuidePcOverlayRunIdOverride = nextRunId;
            return nextRunId;
        } catch (_) {
            yuiGuidePcOverlayRunIdOverride = 'yui-guide-chat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
            return yuiGuidePcOverlayRunIdOverride;
        }
    }

    function getExistingYuiGuidePcOverlayRunId() {
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (storedRunId) {
            if (storedRunId !== yuiGuidePcOverlayRunIdOverride) {
                yuiGuidePcOverlayRunIdOverride = storedRunId;
                yuiGuidePcOverlayActive = false;
                yuiGuidePcOverlayReady = false;
            }
            return storedRunId;
        }
        if (yuiGuidePcOverlayRunIdOverride) {
            if (!isYuiGuidePcOverlayRunEnded(yuiGuidePcOverlayRunIdOverride)) {
                return yuiGuidePcOverlayRunIdOverride;
            }
            yuiGuidePcOverlayRunIdOverride = '';
        }
        return '';
    }

    function isYuiGuidePcOverlayRunEnded(runId) {
        return !!runId && !!yuiGuidePcOverlayEndedRunId && runId === yuiGuidePcOverlayEndedRunId;
    }

    function isYuiGuideChatOwnedPcOverlayRunId(runId) {
        return typeof runId === 'string' && runId.indexOf('yui-guide-chat-') === 0;
    }

    function readStoredYuiGuidePcOverlayRunId() {
        try {
            var storedRunId = window.localStorage.getItem('yuiGuidePcOverlayRunId') || '';
            if (storedRunId && isYuiGuidePcOverlayRunEnded(storedRunId)) {
                window.localStorage.removeItem('yuiGuidePcOverlayRunId');
                return '';
            }
            return storedRunId;
        } catch (_) {
            return '';
        }
    }

    function syncYuiGuidePcOverlayRunIdFromStorage() {
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (!storedRunId || storedRunId === yuiGuidePcOverlayRunIdOverride) {
            return false;
        }
        yuiGuidePcOverlayRunIdOverride = storedRunId;
        yuiGuidePcOverlayActive = false;
        yuiGuidePcOverlayReady = false;
        return true;
    }

    function resolveCanonicalYuiGuideBridgeRunId(message) {
        var tutorialRunId = message && typeof message.tutorialRunId === 'string' && message.tutorialRunId
            ? message.tutorialRunId
            : '';
        if (tutorialRunId) {
            return rememberYuiGuidePcOverlayRunId(tutorialRunId);
        }
        var existingRunId = getExistingYuiGuidePcOverlayRunId();
        if (existingRunId) {
            return rememberYuiGuidePcOverlayRunId(existingRunId);
        }
        var pcOverlayRunId = message && typeof message.pcOverlayRunId === 'string' && message.pcOverlayRunId
            ? message.pcOverlayRunId
            : '';
        if (pcOverlayRunId) {
            return rememberYuiGuidePcOverlayRunId(pcOverlayRunId);
        }
        return '';
    }

    function rememberYuiGuidePcOverlayRunId(runId) {
        var normalizedRunId = typeof runId === 'string' && runId ? runId : '';
        if (!normalizedRunId) {
            return '';
        }
        if (isYuiGuidePcOverlayRunEnded(normalizedRunId)) {
            if (yuiGuidePcOverlayRunIdOverride === normalizedRunId) {
                yuiGuidePcOverlayRunIdOverride = '';
            }
            try {
                if (window.localStorage.getItem('yuiGuidePcOverlayRunId') === normalizedRunId) {
                    window.localStorage.removeItem('yuiGuidePcOverlayRunId');
                }
            } catch (_) {}
            return '';
        }
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (
            storedRunId
            && storedRunId !== normalizedRunId
            && yuiGuidePcOverlayRunIdOverride
            && yuiGuidePcOverlayRunIdOverride !== normalizedRunId
        ) {
            yuiGuidePcOverlayRunIdOverride = storedRunId;
            yuiGuidePcOverlayActive = false;
            yuiGuidePcOverlayReady = false;
            return storedRunId;
        }
        try {
            window.localStorage.setItem('yuiGuidePcOverlayRunId', normalizedRunId);
        } catch (_) {}
        yuiGuidePcOverlayRunIdOverride = normalizedRunId;
        return normalizedRunId;
    }

    function getYuiGuidePcOverlayRunIdFromMessage(message) {
        return resolveCanonicalYuiGuideBridgeRunId(message);
    }

    function isYuiGuideLifecycleScopedAction(action) {
        switch (action) {
            case 'yui_guide_set_chat_buttons_disabled':
            case 'yui_guide_set_chat_input_locked':
            case 'yui_guide_set_compact_chat_fixed_layout':
            case 'yui_guide_set_chat_spotlight':
            case 'yui_guide_set_chat_cursor':
            case 'yui_guide_drag_chat_cursor':
            case 'yui_guide_arc_chat_cursor':
            case 'yui_guide_set_avatar_tool_menu_open':
            case 'yui_guide_set_compact_history_open':
            case 'yui_guide_set_compact_tool_fan_open':
            case 'yui_guide_rotate_compact_tool_wheel':
            case 'yui_guide_set_compact_tool_wheel_index':
                return true;
            default:
                return false;
        }
    }

    function isYuiGuideLifecycleStartAction(action) {
        return action === 'yui_guide_tutorial_lifecycle_started'
            || action === 'yui_guide_tutorial_started'
            || action === 'avatar_floating_guide_started';
    }

    function openYuiGuidePcOverlayLifecycle(message) {
        var runId = message && typeof message.tutorialRunId === 'string'
            ? message.tutorialRunId
            : '';
        if (
            (runId && isYuiGuidePcOverlayRunEnded(runId))
            || (yuiGuidePcOverlayLifecycleClosed && !runId)
        ) {
            return false;
        }
        if (
            yuiGuidePcOverlayLifecycleEpoch === 0
            || yuiGuidePcOverlayLifecycleClosed
            || (runId && runId !== yuiGuidePcOverlayLifecycleRunId)
        ) {
            yuiGuidePcOverlayLifecycleEpoch += 1;
        }
        yuiGuidePcOverlayLifecycleClosed = false;
        yuiGuidePcOverlayLifecycleRunId = runId || yuiGuidePcOverlayLifecycleRunId;
        yuiGuidePcOverlayEndedRunId = '';
        return true;
    }

    function closeYuiGuidePcOverlayLifecycle() {
        yuiGuidePcOverlayLifecycleEpoch += 1;
        yuiGuidePcOverlayLifecycleClosed = true;
        yuiGuidePcOverlayLifecycleRunId = '';
    }

    function isYuiGuideMessageForCurrentLifecycle(message) {
        if (
            !message
            || (
                message.action !== 'yui_guide_tutorial_lifecycle_ended'
                && !isYuiGuideLifecycleScopedAction(message.action)
            )
        ) {
            return true;
        }
        var runId = typeof message.tutorialRunId === 'string'
            ? message.tutorialRunId
            : '';
        return !runId
            || !yuiGuidePcOverlayLifecycleRunId
            || runId === yuiGuidePcOverlayLifecycleRunId;
    }

    function resetYuiGuidePcOverlayRunForRetry() {
        yuiGuidePcOverlayActive = false;
        yuiGuidePcOverlayReady = false;
        yuiGuidePcOverlayRunIdOverride = '';
        yuiGuidePcOverlaySequence = 0;
        try {
            window.localStorage.removeItem('yuiGuidePcOverlayRunId');
        } catch (_) {}
    }

    function handleYuiGuidePcOverlayStaleResult(result, patch, attemptedRunId, retried, attemptedLifecycleEpoch) {
        var isStaleResponse = !!(result && result.stale === true);
        var alreadyRetried = retried === true;
        if (
            yuiGuidePcOverlayLifecycleClosed
            || attemptedLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
        ) {
            return;
        }
        var attemptedCurrentRun = !!(attemptedRunId && attemptedRunId === yuiGuidePcOverlayRunIdOverride);
        var attemptedChatOwnedRun = isYuiGuideChatOwnedPcOverlayRunId(attemptedRunId);
        var storedCanonicalRunId = readStoredYuiGuidePcOverlayRunId();
        var attemptedCanonicalRun = !!(
            storedCanonicalRunId
            && attemptedRunId
            && attemptedRunId === storedCanonicalRunId
            && !attemptedChatOwnedRun
        );

        if (!isStaleResponse || alreadyRetried || !attemptedRunId) {
            return;
        }
        if (attemptedCanonicalRun) {
            if (syncYuiGuidePcOverlayRunIdFromStorage()) {
                sendYuiGuidePcOverlayPatch(patch || {}, true);
                return;
            }
        } else if (!attemptedCurrentRun || !attemptedChatOwnedRun) {
            return;
        }
        if (syncYuiGuidePcOverlayRunIdFromStorage()) {
            sendYuiGuidePcOverlayPatch(patch || {}, true);
            return;
        }
        resetYuiGuidePcOverlayRunForRetry();
        sendYuiGuidePcOverlayPatch(patch || {}, true);
    }

    function resolveYuiGuidePcOverlayRunIdForSend(requestedRunId, allowCreateRun) {
        var normalizedRequestedRunId = typeof requestedRunId === 'string' && requestedRunId
            ? requestedRunId
            : '';
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (storedRunId && storedRunId !== normalizedRequestedRunId) {
            yuiGuidePcOverlayRunIdOverride = storedRunId;
            yuiGuidePcOverlayActive = false;
            yuiGuidePcOverlayReady = false;
            return storedRunId;
        }
        if (normalizedRequestedRunId) {
            return rememberYuiGuidePcOverlayRunId(normalizedRequestedRunId);
        }
        return allowCreateRun === false
            ? getExistingYuiGuidePcOverlayRunId()
            : getYuiGuidePcOverlayRunId();
    }

    function nextYuiGuidePcOverlaySequence() {
        var wallSequence = Date.now() * 1000;
        var storedSequence = 0;
        try {
            storedSequence = Math.max(
                0,
                Math.floor(Number(window.localStorage.getItem(YUI_GUIDE_PC_OVERLAY_SEQUENCE_KEY)) || 0)
            );
        } catch (_) {
            storedSequence = 0;
        }

        yuiGuidePcOverlaySequence = Math.max(
            yuiGuidePcOverlaySequence + 1,
            storedSequence + 1,
            wallSequence
        );
        try {
            window.localStorage.setItem(YUI_GUIDE_PC_OVERLAY_SEQUENCE_KEY, String(yuiGuidePcOverlaySequence));
        } catch (_) {}
        return yuiGuidePcOverlaySequence;
    }

    function getYuiGuideWindowMetrics() {
        var host = getYuiGuidePcOverlayHost();
        if (host) {
            try {
                var metrics = host.getWindowMetricsSync();
                if (metrics && (metrics.contentBounds || metrics.bounds)) {
                    return metrics;
                }
            } catch (_) {}
        }
        return {
            bounds: {
                x: Number.isFinite(window.screenX) ? window.screenX : 0,
                y: Number.isFinite(window.screenY) ? window.screenY : 0
            }
        };
    }

    function getYuiGuideScreenCoordinateBounds(metrics) {
        return metrics && (metrics.bounds || metrics.contentBounds) || { x: 0, y: 0 };
    }

    function normalizeYuiGuideNiriPetPhysicalCropBounds(bounds) {
        if (!bounds || typeof bounds !== 'object') {
            return null;
        }
        var x = Number(bounds.x);
        var y = Number(bounds.y);
        var width = Number(bounds.width);
        var height = Number(bounds.height);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return null;
        }
        return {
            x: Math.round(x),
            y: Math.round(y),
            width: Math.max(1, Math.round(width)),
            height: Math.max(1, Math.round(height))
        };
    }

    function normalizeYuiGuideNiriPetPhysicalCropPoint(point) {
        if (!point || typeof point !== 'object') {
            return null;
        }
        var x = Number(point.x);
        var y = Number(point.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x: x, y: y } : null;
    }

    function normalizeYuiGuideNiriPetPhysicalCropRect(rect) {
        if (!rect || typeof rect !== 'object') {
            return null;
        }
        var x = Number(Object.prototype.hasOwnProperty.call(rect, 'x') ? rect.x : rect.left);
        var y = Number(Object.prototype.hasOwnProperty.call(rect, 'y') ? rect.y : rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return null;
        }
        return {
            x: x,
            y: y,
            width: width,
            height: height
        };
    }

    function getYuiGuideNiriPetPhysicalCropApi() {
        try {
            var api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
            if (!api || typeof api !== 'object') {
                return null;
            }
            if (typeof api.isActive === 'function' && !api.isActive()) {
                return null;
            }
            return api;
        } catch (_) {
            return null;
        }
    }

    function areYuiGuideNiriPetPhysicalCropBoundsEquivalent(first, second) {
        return !!(first && second
            && Math.abs(Number(first.x || 0) - Number(second.x || 0)) <= 1
            && Math.abs(Number(first.y || 0) - Number(second.y || 0)) <= 1
            && Math.abs(Number(first.width || 0) - Number(second.width || 0)) <= 1
            && Math.abs(Number(first.height || 0) - Number(second.height || 0)) <= 1);
    }

    function hasYuiGuideNiriPetPhysicalCropVirtualizedMetrics(metrics) {
        if (!metrics || metrics.niriPetPhysicalCrop !== true) {
            return false;
        }
        if (metrics.niriPetPhysicalCropMetricsVirtualized === true) {
            return true;
        }
        var screenBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(metrics.contentBounds || metrics.bounds);
        var virtualBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(metrics.niriPetPhysicalCropVirtualBounds);
        return areYuiGuideNiriPetPhysicalCropBoundsEquivalent(screenBounds, virtualBounds);
    }

    function getYuiGuideNiriPetPhysicalCropState(metrics) {
        if (metrics && metrics.niriPetPhysicalCrop === true) {
            var metricCropBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(
                metrics.niriPetPhysicalCropBounds || metrics.contentBounds || metrics.bounds
            );
            var metricVirtualBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(metrics.niriPetPhysicalCropVirtualBounds);
            var metricOffsetX = Number(metrics.niriPetPhysicalCropOffsetX);
            var metricOffsetY = Number(metrics.niriPetPhysicalCropOffsetY);
            return metricCropBounds ? {
                cropBounds: metricCropBounds,
                virtualBounds: metricVirtualBounds,
                offsetX: Number.isFinite(metricOffsetX) ? Math.round(metricOffsetX) : 0,
                offsetY: Number.isFinite(metricOffsetY) ? Math.round(metricOffsetY) : 0,
                metricsVirtualized: hasYuiGuideNiriPetPhysicalCropVirtualizedMetrics(metrics)
            } : null;
        }

        try {
            var api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
            if (!api || typeof api !== 'object') {
                return null;
            }
            if (typeof api.isActive === 'function' && !api.isActive()) {
                return null;
            }
            var state = typeof api.getState === 'function' ? api.getState() : null;
            var cropBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(state && state.cropBounds);
            var virtualBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(state && state.virtualBounds);
            if (!cropBounds) {
                return null;
            }
            var offsetX = Number(state && state.offsetX);
            var offsetY = Number(state && state.offsetY);
            if (!Number.isFinite(offsetX) && virtualBounds) {
                offsetX = cropBounds.x - virtualBounds.x;
            }
            if (!Number.isFinite(offsetY) && virtualBounds) {
                offsetY = cropBounds.y - virtualBounds.y;
            }
            return {
                cropBounds: cropBounds,
                virtualBounds: virtualBounds,
                offsetX: Number.isFinite(offsetX) ? Math.round(offsetX) : 0,
                offsetY: Number.isFinite(offsetY) ? Math.round(offsetY) : 0
            };
        } catch (_) {
            return null;
        }
    }

    function toYuiGuideNiriPetPhysicalCropVirtualPoint(x, y) {
        var api = getYuiGuideNiriPetPhysicalCropApi();
        if (!api || typeof api.toVirtualPoint !== 'function') {
            return null;
        }
        try {
            return normalizeYuiGuideNiriPetPhysicalCropPoint(api.toVirtualPoint({
                x: Number(x || 0),
                y: Number(y || 0)
            }));
        } catch (_) {
            return null;
        }
    }

    function toYuiGuideNiriPetPhysicalCropVirtualRect(rect) {
        var api = getYuiGuideNiriPetPhysicalCropApi();
        if (!api || typeof api.toVirtualRect !== 'function') {
            return null;
        }
        try {
            var virtualRect = normalizeYuiGuideNiriPetPhysicalCropRect(api.toVirtualRect({
                x: Number(rect.left || 0),
                y: Number(rect.top || 0),
                width: Number(rect.width || 0),
                height: Number(rect.height || 0)
            }));
            return virtualRect ? {
                left: virtualRect.x,
                top: virtualRect.y,
                width: virtualRect.width,
                height: virtualRect.height
            } : null;
        } catch (_) {
            return null;
        }
    }

    function toYuiGuideNiriPetPhysicalCropVirtualPointWithState(x, y, cropState) {
        if (cropState && cropState.metricsVirtualized) {
            return {
                x: Number(x || 0),
                y: Number(y || 0)
            };
        }
        return toYuiGuideNiriPetPhysicalCropVirtualPoint(x, y) || {
            x: Number(x || 0) + Number(cropState && cropState.offsetX || 0),
            y: Number(y || 0) + Number(cropState && cropState.offsetY || 0)
        };
    }

    function toYuiGuideNiriPetPhysicalCropVirtualRectWithState(rect, cropState) {
        if (cropState && cropState.metricsVirtualized) {
            return {
                left: Number(rect.left || 0),
                top: Number(rect.top || 0),
                width: rect.width,
                height: rect.height
            };
        }
        return toYuiGuideNiriPetPhysicalCropVirtualRect(rect) || {
            left: Number(rect.left || 0) + Number(cropState && cropState.offsetX || 0),
            top: Number(rect.top || 0) + Number(cropState && cropState.offsetY || 0),
            width: rect.width,
            height: rect.height
        };
    }

    function shouldApplyYuiGuideVisualViewportOffset(metrics) {
        return !getYuiGuideNiriPetPhysicalCropState(metrics);
    }

    function toYuiGuideScreenVirtualPoint(x, y, cropState) {
        var screenBounds = cropState.virtualBounds || cropState.cropBounds;
        return {
            x: Number(screenBounds.x || 0) + Number(x || 0),
            y: Number(screenBounds.y || 0) + Number(y || 0)
        };
    }

    function toYuiGuideScreenPoint(x, y) {
        var metrics = getYuiGuideWindowMetrics();
        var cropState = getYuiGuideNiriPetPhysicalCropState(metrics);
        if (cropState && cropState.cropBounds) {
            var virtualPoint = toYuiGuideNiriPetPhysicalCropVirtualPointWithState(x, y, cropState);
            return toYuiGuideScreenVirtualPoint(
                virtualPoint.x,
                virtualPoint.y,
                cropState
            );
        }
        var bounds = getYuiGuideScreenCoordinateBounds(metrics);
        var viewport = shouldApplyYuiGuideVisualViewportOffset(metrics) ? (window.visualViewport || null) : null;
        var offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
        var offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
        return {
            x: Number(bounds.x || 0) + Number(x || 0) + offsetLeft,
            y: Number(bounds.y || 0) + Number(y || 0) + offsetTop
        };
    }

    function toYuiGuideScreenRect(rect, kind, variant) {
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        var metrics = getYuiGuideWindowMetrics();
        var cropState = getYuiGuideNiriPetPhysicalCropState(metrics);
        var cropRect = cropState && cropState.cropBounds
            ? toYuiGuideNiriPetPhysicalCropVirtualRectWithState(rect, cropState)
            : rect;
        var point = cropState && cropState.cropBounds
            ? toYuiGuideScreenVirtualPoint(cropRect.left, cropRect.top, cropState)
            : toYuiGuideScreenPoint(rect.left, rect.top);
        var isCircle = getYuiGuideChatTargetShape(kind) === 'circle';
        var radius = kind === 'window' ? 26 : Math.min(34, Math.max(18, Math.round((cropRect.height + 16) / 2)));
        if (isCircle) {
            radius = 999;
        }
        return {
            id: 'external-chat-' + (kind || 'target'),
            kind: kind || '',
            shape: isCircle ? 'circle' : 'rounded-rect',
            variant: variant || '',
            x: point.x,
            y: point.y,
            width: cropRect.width,
            height: cropRect.height,
            radius: radius
        };
    }

    function withoutTransientYuiGuideCursorEffect(cursor) {
        if (!cursor) {
            return cursor;
        }
        var nextCursor = Object.assign({}, cursor);
        delete nextCursor.effect;
        delete nextCursor.effectDurationMs;
        return nextCursor;
    }

    function sendYuiGuidePcOverlayPatch(patch, retried, options) {
        var host = getYuiGuidePcOverlayHost();
        if (!host || yuiGuidePcOverlayLifecycleClosed) {
            return false;
        }
        var sendLifecycleEpoch = yuiGuidePcOverlayLifecycleEpoch;
        var sendOptions = options || {};
        if (isYuiGuidePcOverlayRunEnded(sendOptions.tutorialRunId)) {
            return false;
        }
        if (patch && Object.prototype.hasOwnProperty.call(patch, 'spotlights')) {
            yuiGuidePcOverlaySpotlights = Array.isArray(patch.spotlights) ? patch.spotlights : [];
        }
        if (patch && Object.prototype.hasOwnProperty.call(patch, 'cursor')) {
            yuiGuidePcOverlayCursor = withoutTransientYuiGuideCursorEffect(patch.cursor);
        }
        var payload = {
            spotlights: yuiGuidePcOverlaySpotlights
        };
        if (patch && Object.prototype.hasOwnProperty.call(patch, 'cursor')) {
            payload.cursor = patch.cursor || null;
        } else if (yuiGuidePcOverlayCursor) {
            payload.cursor = yuiGuidePcOverlayCursor;
        }
        var runId = resolveYuiGuidePcOverlayRunIdForSend(
            sendOptions.tutorialRunId,
            sendOptions.allowCreateRun
        );
        if (!runId || isYuiGuidePcOverlayRunEnded(runId)) {
            return false;
        }
        if (!yuiGuidePcOverlayActive && sendOptions.skipBegin !== true && typeof host.begin === 'function') {
            try {
                Promise.resolve(host.begin({ tutorialRunId: runId })).then(function (result) {
                    if (
                        yuiGuidePcOverlayLifecycleClosed
                        || sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
                    ) {
                        return;
                    }
                    if (result && result.stale === true) {
                        handleYuiGuidePcOverlayStaleResult(
                            result,
                            patch,
                            runId,
                            retried === true,
                            sendLifecycleEpoch
                        );
                        return;
                    }
                    if (result && result.ok === false) {
                        yuiGuidePcOverlayActive = false;
                        yuiGuidePcOverlayReady = false;
                    }
                }).catch(function () {
                    if (sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch) {
                        return;
                    }
                    yuiGuidePcOverlayActive = false;
                    yuiGuidePcOverlayReady = false;
                });
                yuiGuidePcOverlayActive = true;
            } catch (_) {}
        }
        yuiGuidePcOverlaySequence = nextYuiGuidePcOverlaySequence();
        try {
            Promise.resolve(host.update({
                tutorialRunId: runId,
                sceneId: 'external-chat',
                sequence: yuiGuidePcOverlaySequence,
                payload: payload
            })).then(function (result) {
                if (
                    yuiGuidePcOverlayLifecycleClosed
                    || sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
                ) {
                    return;
                }
                if (result && result.stale === true) {
                    handleYuiGuidePcOverlayStaleResult(
                        result,
                        patch,
                        runId,
                        retried === true,
                        sendLifecycleEpoch
                    );
                    return;
                }
                if (result && result.ok === false) {
                    yuiGuidePcOverlayReady = false;
                    return;
                }
                yuiGuidePcOverlayReady = true;
            }).catch(function () {
                if (sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch) {
                    return;
                }
                yuiGuidePcOverlayReady = false;
            });
            yuiGuidePcOverlayReady = true;
            return true;
        } catch (_) {
            yuiGuidePcOverlayReady = false;
            return false;
        }
    }

    function isYuiGuidePcCursorOnlyMode() {
        return isYuiGuidePcOverlayAvailable();
    }

    function createYuiGuideTargetGeometryRegistry() {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.createTutorialTargetGeometryRegistry === 'function'
        ) {
            return window.YuiGuideCommon.createTutorialTargetGeometryRegistry();
        }
        return null;
    }

    function getYuiGuideTargetGeometryRegistry() {
        if (!yuiGuideTargetGeometryRegistry) {
            yuiGuideTargetGeometryRegistry = createYuiGuideTargetGeometryRegistry();
        }
        return yuiGuideTargetGeometryRegistry;
    }

    function getYuiGuideChatTargetRegistryEntryByExternalKind(kind) {
        var registry = getYuiGuideTargetGeometryRegistry();
        if (!registry || typeof registry.getByExternalKind !== 'function') {
            return null;
        }
        return registry.getByExternalKind(kind);
    }

    function getYuiGuideChatTargetShape(kind) {
        if (
            kind === 'avatar-tools'
            || kind === 'galgame'
            || kind === 'tool-toggle'
            || kind === 'avatar-tool-items'
            || kind === 'avatar-tools-and-items'
            || kind === 'mini-game-choices'
        ) {
            return 'circle';
        }
        var entry = getYuiGuideChatTargetRegistryEntryByExternalKind(kind);
        return entry && entry.shape ? entry.shape : 'rounded-rect';
    }

    function shouldAlignYuiGuideChatSpotlightToCapsuleText(kind, variant) {
        return kind === 'input' && variant === 'plain-capsule';
    }

    var YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO = 0.6;

    function getYuiGuideChatSpotlightTarget(kind) {
        if (!kind || typeof document === 'undefined') {
            return null;
        }

        var entry = getYuiGuideChatTargetRegistryEntryByExternalKind(kind);
        if (entry && Array.isArray(entry.localSelectors)) {
            var registryTarget = null;
            entry.localSelectors.some(function (selector) {
                registryTarget = getYuiGuideChatVisibleElement(selector);
                return !!registryTarget;
            });
            if (registryTarget) {
                return registryTarget;
            }
        }

        if (kind === 'input') {
            return document.querySelector('#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]')
                || document.querySelector('#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]')
                || document.querySelector('#react-chat-window-root .compact-chat-surface-frame')
                || document.querySelector('#react-chat-window-root .compact-chat-surface-shell')
                || document.querySelector('#react-chat-window-root .composer-panel')
                || document.querySelector('#react-chat-window-root .composer-input-shell')
                || document.getElementById('text-input-area');
        }

        if (kind === 'window') {
            return document.getElementById('react-chat-window-shell');
        }

        var itemTargets = getYuiGuideChatSpotlightItemTargets(kind);
        if (itemTargets.length > 0) {
            return itemTargets[0];
        }

        return null;
    }

    function getYuiGuideChatCapsuleTextAnchor() {
        var anchor = getYuiGuideChatVisibleElement('#react-chat-window-root [data-compact-hit-region-id="capsule:text"]')
            || getYuiGuideChatVisibleElement('#react-chat-window-root .compact-chat-capsule-button');
        if (!anchor || typeof anchor.getBoundingClientRect !== 'function') {
            return null;
        }
        var rect = anchor.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        return {
            element: anchor,
            rect: rect
        };
    }

    function getYuiGuideChatSpotlightSourceRect(kind, variant, rect) {
        var sourceRect = {
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height
        };
        if (shouldAlignYuiGuideChatSpotlightToCapsuleText(kind, variant)) {
            var anchor = getYuiGuideChatCapsuleTextAnchor();
            var anchorRect = anchor && anchor.rect;
            if (
                anchorRect
                && anchorRect.left > rect.left
                && anchorRect.left < rect.left + rect.width
            ) {
                var anchorOffsetX = anchorRect.left - rect.left;
                sourceRect.left = rect.left + anchorOffsetX * YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO;
            }
        }
        return { rect: sourceRect };
    }

    function getYuiGuideChatVisibleElement(selector, root) {
        var scope = root || document;
        var elements = Array.prototype.slice.call(scope.querySelectorAll(selector));
        for (var index = 0; index < elements.length; index += 1) {
            var element = elements[index];
            var rect = element && typeof element.getBoundingClientRect === 'function'
                ? element.getBoundingClientRect()
                : null;
            if (rect && rect.width > 0 && rect.height > 0) {
                return element;
            }
        }
        return null;
    }

    function getYuiGuideChatVisibleElements(selector, root) {
        var scope = root || document;
        return Array.prototype.slice.call(scope.querySelectorAll(selector)).filter(function (element, index, array) {
            var rect = element && typeof element.getBoundingClientRect === 'function'
                ? element.getBoundingClientRect()
                : null;
            return element && array.indexOf(element) === index && rect && rect.width > 0 && rect.height > 0;
        });
    }

    function getYuiGuideChatVisibleElementsFromSelectors(selectors) {
        var results = [];
        (Array.isArray(selectors) ? selectors : []).forEach(function (selector) {
            getYuiGuideChatVisibleElements(selector).forEach(function (element) {
                if (results.indexOf(element) < 0) {
                    results.push(element);
                }
            });
        });
        return results;
    }

    function getYuiGuideChatSpotlightItemTargets(kind) {
        var popover = document.getElementById('composer-tool-popover-compact');
        var quickbar = document.getElementById('composer-avatar-tool-quickbar');
        if (kind === 'tool-toggle') {
            return [getYuiGuideChatVisibleElement('.compact-input-tool-toggle')].filter(Boolean);
        }
        if (kind === 'history') {
            return [
                getYuiGuideChatVisibleElement('.compact-export-history-anchor'),
                getYuiGuideChatVisibleElement('.compact-input-tool-item-history', popover)
            ].filter(Boolean);
        }
        if (kind === 'avatar-tools') {
            return [
                getYuiGuideChatVisibleElement('.compact-input-tool-item-avatar', popover),
                getYuiGuideChatVisibleElement('[data-avatar-tool-id]', quickbar)
            ].filter(Boolean);
        }
        if (kind === 'avatar-tool-items') {
            return getYuiGuideChatVisibleElements('#composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id]')
                .concat(getYuiGuideChatVisibleElements('#composer-avatar-tool-quickbar .composer-icon-button[data-avatar-tool-id]'))
                .concat(getYuiGuideChatVisibleElements('.composer-icon-button[data-avatar-tool-id]'))
                .filter(function (element, index, array) {
                    return array.indexOf(element) === index;
                });
        }
        if (kind === 'galgame') {
            return [
                getYuiGuideChatVisibleElement('.compact-input-tool-item-galgame', popover),
                getYuiGuideChatVisibleElement('.composer-galgame-btn', popover)
            ].filter(Boolean);
        }
        return [];
    }

    function getYuiGuideChatCursorTarget(kind, options) {
        var normalizedOptions = options || {};
        var targetIndex = Number.isFinite(Number(normalizedOptions.targetIndex))
            ? Math.max(0, Math.floor(Number(normalizedOptions.targetIndex)))
            : 0;
        var entry = getYuiGuideChatTargetRegistryEntryByExternalKind(kind);
        var targets = entry && Array.isArray(entry.localSelectors)
            ? getYuiGuideChatVisibleElementsFromSelectors(entry.localSelectors)
            : getYuiGuideChatSpotlightItemTargets(kind);
        if (targets.length > 0) {
            return targets[Math.min(targetIndex, targets.length - 1)];
        }
        return getYuiGuideChatSpotlightTarget(kind);
    }

    function getYuiGuideChatCursorTargetPoint(kind, options) {
        var target = getYuiGuideChatCursorTarget(kind, options);
        var rect = target && typeof target.getBoundingClientRect === 'function'
            ? target.getBoundingClientRect()
            : null;
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        return {
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2
        };
    }

    function getYuiGuideCompactToolWheelCenterPoint() {
        var fan = getYuiGuideChatVisibleElement('#react-chat-window-root .compact-input-tool-fan')
            || getYuiGuideChatVisibleElement('.compact-input-tool-fan');
        var rect = fan && typeof fan.getBoundingClientRect === 'function'
            ? fan.getBoundingClientRect()
            : null;
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        var style = window.getComputedStyle ? window.getComputedStyle(fan) : null;
        var readPixelVar = function (name, fallback) {
            var rawValue = style ? String(style.getPropertyValue(name) || '').trim() : '';
            var parsedValue = Number.parseFloat(rawValue);
            return Number.isFinite(parsedValue) ? parsedValue : fallback;
        };
        return {
            x: rect.left + readPixelVar('--compact-tool-wheel-center-x', 116),
            y: rect.top + readPixelVar('--compact-tool-wheel-center-y', 116)
        };
    }

    function buildYuiGuideChatCursorArcMotion(kind, options) {
        var normalizedOptions = options || {};
        var start = yuiGuideChatCursorPoint || getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
        var center = kind === 'galgame'
            ? getYuiGuideCompactToolWheelCenterPoint()
            : getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
        if (!center) {
            center = getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
        }
        if (!start || !center) {
            return null;
        }
        var radius = Math.hypot(start.x - center.x, start.y - center.y);
        if (!Number.isFinite(radius) || radius < 4) {
            return null;
        }
        var direction = Number(normalizedOptions.direction) < 0 ? -1 : 1;
        var fraction = Number.isFinite(Number(normalizedOptions.fraction))
            ? Math.max(0, Math.min(1, Number(normalizedOptions.fraction)))
            : 0.2;
        var totalAngle = direction * Math.PI * 2 * fraction;
        var startAngle = Math.atan2(start.y - center.y, start.x - center.x);
        var stepCount = Number.isFinite(Number(normalizedOptions.stepCount))
            ? Math.max(2, Math.floor(Number(normalizedOptions.stepCount)))
            : 8;
        var points = [];
        for (var index = 1; index <= stepCount; index += 1) {
            var progress = index / stepCount;
            var angle = startAngle + totalAngle * progress;
            points.push({
                x: center.x + Math.cos(angle) * radius,
                y: center.y + Math.sin(angle) * radius
            });
        }
        return {
            start: start,
            points: points,
            finalPoint: points[points.length - 1]
        };
    }

    function ensureYuiGuideChatCursorElement() {
        var cursor = document.getElementById('yui-guide-chat-cursor');
        if (cursor) return cursor;
        if (!document.body) return null;
        cursor = document.createElement('div');
        cursor.id = 'yui-guide-chat-cursor';
        cursor.setAttribute('aria-hidden', 'true');
        cursor.style.position = 'fixed';
        cursor.style.left = '0';
        cursor.style.top = '0';
        cursor.style.width = '18px';
        cursor.style.height = '18px';
        cursor.style.borderRadius = '999px';
        cursor.style.background = 'rgba(80, 140, 255, 0.78)';
        cursor.style.boxShadow = '0 0 0 4px rgba(80, 140, 255, 0.22), 0 8px 18px rgba(0, 0, 0, 0.24)';
        cursor.style.pointerEvents = 'none';
        cursor.style.zIndex = '2147483600';
        cursor.style.opacity = '0';
        cursor.style.transform = 'translate3d(-9999px, -9999px, 0)';
        cursor.style.transitionProperty = 'transform, opacity, box-shadow, background-color';
        cursor.style.transitionTimingFunction = 'cubic-bezier(0.22, 1, 0.36, 1)';
        document.body.appendChild(cursor);
        return cursor;
    }

    function hideYuiGuideChatCursorElement() {
        var cursor = document.getElementById('yui-guide-chat-cursor');
        if (!cursor) return;
        cursor.style.opacity = '0';
        cursor.hidden = true;
    }

    function reportYuiGuideChatCursorAnchor(screenPoint, kind, effect, effectDurationMs, options) {
        if (!screenPoint) {
            return;
        }
        var message = {
            x: screenPoint.x,
            y: screenPoint.y,
            kind: kind || '',
            effect: typeof effect === 'string' ? effect : '',
            effectDurationMs: Math.max(0, Math.floor(Number(effectDurationMs) || 0)),
            source: 'external-chat',
            settled: !!(options && options.settled),
            timestamp: Date.now(),
            bypassDedup: true
        };
        postYuiGuideMessageToPet('yui_guide_chat_cursor_anchor', message);
    }

    function publishYuiGuideChatCursorAnchor(kind, point, options, settled) {
        if (!point) {
            return;
        }
        reportYuiGuideChatCursorAnchor(point, kind, options && options.effect, options && options.effectDurationMs, {
            settled: settled === true
        });
    }

    function rememberYuiGuideChatCursorScreenPoint(point, kind, options, settled) {
        if (!point) {
            return;
        }
        publishYuiGuideChatCursorAnchor(kind || '', point, options || {}, settled === true);
    }

    function moveYuiGuideChatCursor(kind, point, options) {
        if (!point) return false;
        var normalizedOptions = options || {};
        var duration = normalizedOptions && Number.isFinite(Number(normalizedOptions.durationMs))
            ? Math.max(0, Math.floor(Number(options.durationMs)))
            : 240;
        if (isYuiGuidePcCursorOnlyMode()) {
            var screenPoint = toYuiGuideScreenPoint(point.x, point.y);
            yuiGuideChatCursorPoint = { x: point.x, y: point.y };
            sendYuiGuidePcOverlayPatch({
                cursor: {
                    visible: true,
                    x: screenPoint.x,
                    y: screenPoint.y,
                    durationMs: duration,
                    effect: normalizedOptions.effect || '',
                    effectDurationMs: Math.max(0, Math.floor(Number(normalizedOptions.effectDurationMs) || 0))
                }
            }, false, {
                tutorialRunId: normalizedOptions.pcOverlayRunId
            });
            publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, duration === 0);
            if (duration > 0) {
                var anchorToken = yuiGuideChatCursorRequestToken;
                window.setTimeout(function () {
                    if (anchorToken !== yuiGuideChatCursorRequestToken) {
                        return;
                    }
                    publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, true);
                }, duration);
            }
            return true;
        }
        var cursor = ensureYuiGuideChatCursorElement();
        if (!cursor) return false;
        cursor.style.transitionDuration = duration + 'ms, 120ms, 120ms, 120ms';
        cursor.hidden = false;
        cursor.style.opacity = '1';
        cursor.style.transform = 'translate3d(' + Math.round(point.x - 9) + 'px, ' + Math.round(point.y - 9) + 'px, 0)';
        yuiGuideChatCursorPoint = { x: point.x, y: point.y };
        return true;
    }

    function applyYuiGuideChatCursor(kind, options) {
        var normalizedOptions = options || {};
        var freezePoint = normalizedOptions.freezePoint === true;
        if (freezePoint) {
            yuiGuideChatCursorRequestToken += 1;
            if (kind === 'galgame') {
                yuiGuideChatCursorArcRequestToken += 1;
            }
        } else {
            yuiGuideChatCursorRequestToken = yuiGuideChatCursorRequestToken + 1;
        }
        if (!kind) {
            if (isYuiGuidePcCursorOnlyMode() && normalizedOptions.preservePcOverlayCursor !== true) {
                sendYuiGuidePcOverlayPatch({
                    cursor: { visible: false }
                }, false, {
                    tutorialRunId: normalizedOptions.pcOverlayRunId,
                    allowCreateRun: !(normalizedOptions.allowCreatePcOverlayRun === false),
                    skipBegin: normalizedOptions.skipPcOverlayBegin === true
                });
            } else if (isYuiGuidePcCursorOnlyMode()) {
                yuiGuidePcOverlayCursor = null;
            }
            hideYuiGuideChatCursorElement();
            yuiGuideChatCursorPoint = null;
            return true;
        }
        if (isYuiGuidePcCursorOnlyMode()) {
            var targetPoint = getYuiGuideChatCursorTargetPoint(kind, normalizedOptions);
            var freezeKey = kind + ':' + (normalizedOptions.timestamp || '');
            var frozenScreenPoint = freezePoint ? yuiGuideChatCursorFrozenScreenPoints[freezeKey] : null;
            if (!targetPoint && !frozenScreenPoint) return false;
            var screenPoint = frozenScreenPoint || toYuiGuideScreenPoint(targetPoint.x, targetPoint.y);
            if (freezePoint && yuiGuideChatCursorFrozenScreenPoints[freezeKey]) {
                screenPoint = yuiGuideChatCursorFrozenScreenPoints[freezeKey];
            } else if (freezePoint) {
                yuiGuideChatCursorFrozenScreenPoints[freezeKey] = screenPoint;
            }
            var duration = Number.isFinite(Number(normalizedOptions.durationMs))
                ? Math.max(0, Math.floor(Number(normalizedOptions.durationMs)))
                : 240;
            if (targetPoint) {
                yuiGuideChatCursorPoint = { x: targetPoint.x, y: targetPoint.y };
            }
            sendYuiGuidePcOverlayPatch({
                cursor: {
                    visible: true,
                    x: screenPoint.x,
                    y: screenPoint.y,
                    durationMs: duration,
                    effect: normalizedOptions.effect || '',
                    effectDurationMs: Math.max(0, Math.floor(Number(normalizedOptions.effectDurationMs) || 0))
                }
            }, false, {
                tutorialRunId: normalizedOptions.pcOverlayRunId
            });
            publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, duration === 0);
            if (duration > 0) {
                var anchorToken = yuiGuideChatCursorRequestToken;
                window.setTimeout(function () {
                    if (anchorToken !== yuiGuideChatCursorRequestToken) {
                        return;
                    }
                    publishYuiGuideChatCursorAnchor(kind, screenPoint, normalizedOptions, true);
                }, duration);
            }
            return true;
        }
        return moveYuiGuideChatCursor(kind, getYuiGuideChatCursorTargetPoint(kind, normalizedOptions), normalizedOptions);
    }

    function applyYuiGuideChatCursorDrag(kind, options) {
        yuiGuideChatCursorRequestToken = yuiGuideChatCursorRequestToken + 1;
        var start = yuiGuideChatCursorPoint || getYuiGuideChatCursorTargetPoint(kind, options || {});
        if (!start) return false;
        return moveYuiGuideChatCursor(kind, {
            x: start.x + (Number.isFinite(Number(options && options.deltaX)) ? Number(options.deltaX) : 0),
            y: start.y + (Number.isFinite(Number(options && options.deltaY)) ? Number(options.deltaY) : 0)
        }, options || {});
    }

    function applyYuiGuideChatCursorArc(kind, options) {
        yuiGuideChatCursorRequestToken = yuiGuideChatCursorRequestToken + 1;
        var cursorRequestToken = yuiGuideChatCursorRequestToken;
        var arcRequestToken = ++yuiGuideChatCursorArcRequestToken;
        var motion = buildYuiGuideChatCursorArcMotion(kind, options || {});
        if (!motion || !motion.finalPoint || motion.points.length === 0) return false;
        var duration = options && Number.isFinite(Number(options.durationMs))
            ? Math.max(0, Math.floor(Number(options.durationMs)))
            : 240;
        var effectDurationMs = options && Number.isFinite(Number(options.effectDurationMs))
            ? Math.max(0, Math.floor(Number(options.effectDurationMs)))
            : duration;
        var startMoved = moveYuiGuideChatCursor(kind, motion.start, Object.assign({}, options || {}, {
            durationMs: 0,
            effect: options && typeof options.effect === 'string' ? options.effect : '',
            effectDurationMs: effectDurationMs
        }));
        if (!startMoved) {
            return false;
        }
        var segmentDuration = Math.max(0, Math.round(duration / motion.points.length));
        motion.points.forEach(function (point, index) {
            window.setTimeout(function () {
                if (
                    arcRequestToken !== yuiGuideChatCursorArcRequestToken
                    || cursorRequestToken !== yuiGuideChatCursorRequestToken
                ) {
                    return;
                }
                moveYuiGuideChatCursor(kind, point, Object.assign({}, options || {}, {
                    durationMs: segmentDuration,
                    effectDurationMs: effectDurationMs
                }));
            }, index * segmentDuration);
        });
        var finalScreenPoint = toYuiGuideScreenPoint(motion.finalPoint.x, motion.finalPoint.y);
        window.setTimeout(function () {
            if (
                arcRequestToken !== yuiGuideChatCursorArcRequestToken
                || cursorRequestToken !== yuiGuideChatCursorRequestToken
            ) {
                return;
            }
            rememberYuiGuideChatCursorScreenPoint(finalScreenPoint, kind, options || {}, true);
        }, duration);
        return true;
    }

    function clearYuiGuideChatSpotlightTracking() {
        if (yuiGuideChatSpotlightTimer) {
            yuiGuideChatSpotlightResources.clearInterval(yuiGuideChatSpotlightTimer);
            yuiGuideChatSpotlightTimer = 0;
        }
        yuiGuideChatSpotlightResources.destroy();
        yuiGuideChatSpotlightResources = createAppInterpageScopedResources();
    }

    function rememberYuiGuideChatPcSpotlightRects(kind, rects, variant) {
        yuiGuideChatSpotlightLastPcKind = kind || '';
        yuiGuideChatSpotlightLastPcVariant = typeof variant === 'string' ? variant : '';
        yuiGuideChatSpotlightLastPcRects = Array.isArray(rects)
            ? rects.map(function (rect) {
                return Object.assign({}, rect);
            })
            : [];
    }

    function clearYuiGuideChatPcSpotlightRects() {
        yuiGuideChatSpotlightLastPcKind = '';
        yuiGuideChatSpotlightLastPcVariant = '';
        yuiGuideChatSpotlightLastPcRects = [];
    }

    function cloneYuiGuideChatPcSpotlightRects() {
        return yuiGuideChatSpotlightLastPcRects.map(function (pcRect) {
            return Object.assign({}, pcRect);
        });
    }

    function preserveYuiGuideChatSpotlightDuringResistance(kind, pcOverlayRunId) {
        var preservedKind = typeof kind === 'string' && kind ? kind : yuiGuideChatSpotlightKind;
        if (!preservedKind) {
            return false;
        }
        if (
            yuiGuideChatSpotlightLastPcKind === preservedKind
            && yuiGuideChatSpotlightLastPcVariant === yuiGuideChatSpotlightVariant
            && yuiGuideChatSpotlightLastPcRects.length > 0
        ) {
            sendYuiGuidePcOverlayPatch({
                spotlights: cloneYuiGuideChatPcSpotlightRects()
            }, false, {
                tutorialRunId: pcOverlayRunId || yuiGuideChatSpotlightPcOverlayRunId
            });
            return true;
        }
        updateYuiGuideChatSpotlight(preservedKind, pcOverlayRunId || yuiGuideChatSpotlightPcOverlayRunId);
        return true;
    }

    function isYuiGuideChatInputSpotlightKind(kind) {
        return kind === 'input' || kind === 'capsule-input';
    }

    function scheduleYuiGuideChatInputSpotlightRetry(kind, pcOverlayRunId) {
        var retryKind = typeof kind === 'string' && kind ? kind : yuiGuideChatSpotlightKind;
        var retryRunId = typeof pcOverlayRunId === 'string' && pcOverlayRunId
            ? pcOverlayRunId
            : yuiGuideChatSpotlightPcOverlayRunId;
        if (!isYuiGuideChatInputSpotlightKind(retryKind)) {
            return;
        }

        [80, 180, 360, 720, 1200].forEach(function (delayMs) {
            yuiGuideChatSpotlightResources.setTimeout(function () {
                if (yuiGuideChatSpotlightKind === retryKind) {
                    updateYuiGuideChatSpotlight(retryKind, retryRunId);
                }
            }, delayMs);
        });
    }

    function ensureYuiGuideChatSpotlightTracking(pcOverlayRunId) {
        if (typeof pcOverlayRunId === 'string' && pcOverlayRunId) {
            yuiGuideChatSpotlightPcOverlayRunId = pcOverlayRunId;
        }
        if (!yuiGuideChatSpotlightKind || yuiGuideChatSpotlightTimer) {
            return;
        }
        yuiGuideChatSpotlightTimer = yuiGuideChatSpotlightResources.setInterval(function () {
            updateYuiGuideChatSpotlight(yuiGuideChatSpotlightKind, yuiGuideChatSpotlightPcOverlayRunId);
        }, 120);
    }

    function updateYuiGuideChatSpotlight(kind, pcOverlayRunId) {
        var pcOverlayAvailable = isYuiGuidePcOverlayAvailable();
        var spotlight = getYuiGuideChatSpotlightElement(!pcOverlayAvailable);
        var patchOptions = {
            tutorialRunId: pcOverlayRunId || yuiGuideChatSpotlightPcOverlayRunId
        };

        var target = getYuiGuideChatSpotlightTarget(kind);
        var rect = target && typeof target.getBoundingClientRect === 'function'
            ? target.getBoundingClientRect()
            : null;
        var sourceRectInfo = rect ? getYuiGuideChatSpotlightSourceRect(kind, yuiGuideChatSpotlightVariant, rect) : null;
        var sourceRect = sourceRectInfo ? sourceRectInfo.rect : rect;

        if (!sourceRect || sourceRect.width <= 0 || sourceRect.height <= 0) {
            if (pcOverlayAvailable) {
                if (
                    kind
                    && yuiGuideChatSpotlightLastPcKind === kind
                    && yuiGuideChatSpotlightLastPcVariant === yuiGuideChatSpotlightVariant
                    && yuiGuideChatSpotlightLastPcRects.length > 0
                ) {
                    sendYuiGuidePcOverlayPatch({
                        spotlights: yuiGuideChatSpotlightLastPcRects.map(function (pcRect) {
                            return Object.assign({}, pcRect);
                        })
                    }, false, patchOptions);
                }
            }
            if (spotlight) {
                spotlight.hidden = true;
                spotlight.classList.remove('is-visible', 'is-window', 'is-input');
            }
            return;
        }

        var padding = kind === 'window' ? 10 : 8;
        var radius = kind === 'window' ? 26 : Math.min(34, Math.max(18, Math.round((sourceRect.height + padding * 2) / 2)));
        if (pcOverlayAvailable) {
            var pcRects = [toYuiGuideScreenRect({
                left: sourceRect.left - padding,
                top: sourceRect.top - padding,
                width: sourceRect.width + padding * 2,
                height: sourceRect.height + padding * 2
            }, kind, yuiGuideChatSpotlightVariant)].filter(Boolean);
            rememberYuiGuideChatPcSpotlightRects(kind, pcRects, yuiGuideChatSpotlightVariant);
            sendYuiGuidePcOverlayPatch({ spotlights: pcRects }, false, patchOptions);
            if (spotlight) {
                spotlight.hidden = true;
                spotlight.classList.remove('is-visible', 'is-window', 'is-input');
            }
            return;
        }
        if (!spotlight) {
            return;
        }
        spotlight.hidden = false;
        spotlight.classList.remove('is-window', 'is-input');
        spotlight.classList.add(kind === 'window' ? 'is-window' : 'is-input');
        spotlight.classList.add('is-visible');
        spotlight.style.left = Math.round(sourceRect.left - padding) + 'px';
        spotlight.style.top = Math.round(sourceRect.top - padding) + 'px';
        spotlight.style.width = Math.round(sourceRect.width + padding * 2) + 'px';
        spotlight.style.height = Math.round(sourceRect.height + padding * 2) + 'px';
        spotlight.style.borderRadius = radius + 'px';
    }

    function applyYuiGuideChatSpotlight(kind, options) {
        var normalizedKind = typeof kind === 'string' ? kind : '';
        var pcOverlayRunId = options && typeof options.pcOverlayRunId === 'string'
            ? options.pcOverlayRunId
            : '';
        var hasVariantOption = options && Object.prototype.hasOwnProperty.call(options, 'variant');
        var normalizedVariant = hasVariantOption && typeof options.variant === 'string'
            ? options.variant.trim()
            : (normalizedKind && normalizedKind === yuiGuideChatSpotlightKind ? yuiGuideChatSpotlightVariant : '');
        if (pcOverlayRunId) {
            yuiGuideChatSpotlightPcOverlayRunId = pcOverlayRunId;
        }
        if (normalizedKind && options && options.preserveDuringResistance === true) {
            if (yuiGuideChatSpotlightKind && yuiGuideChatSpotlightKind !== normalizedKind) {
                clearYuiGuideChatSpotlightTracking();
            }
            yuiGuideChatSpotlightKind = normalizedKind;
            yuiGuideChatSpotlightVariant = normalizedVariant;
            preserveYuiGuideChatSpotlightDuringResistance(normalizedKind, pcOverlayRunId);
            scheduleYuiGuideChatInputSpotlightRetry(normalizedKind, pcOverlayRunId);
            ensureYuiGuideChatSpotlightTracking(pcOverlayRunId);
            return;
        }
        if (
            !normalizedKind
            && options
            && options.preserveDuringResistance === true
            && yuiGuideChatSpotlightKind
            && yuiGuideChatSpotlightLastPcKind === yuiGuideChatSpotlightKind
            && yuiGuideChatSpotlightLastPcVariant === yuiGuideChatSpotlightVariant
            && yuiGuideChatSpotlightLastPcRects.length > 0
        ) {
            updateYuiGuideChatSpotlight(yuiGuideChatSpotlightKind, pcOverlayRunId);
            ensureYuiGuideChatSpotlightTracking(pcOverlayRunId);
            return;
        }
        yuiGuideChatSpotlightKind = normalizedKind;
        yuiGuideChatSpotlightVariant = normalizedKind ? normalizedVariant : '';
        clearYuiGuideChatSpotlightTracking();

        if (!yuiGuideChatSpotlightKind) {
            var clearSpotlightRunId = pcOverlayRunId || yuiGuideChatSpotlightPcOverlayRunId;
            clearYuiGuideChatPcSpotlightRects();
            yuiGuideChatSpotlightPcOverlayRunId = '';
            yuiGuideChatSpotlightVariant = '';
            sendYuiGuidePcOverlayPatch({ spotlights: [] }, false, {
                tutorialRunId: clearSpotlightRunId,
                allowCreateRun: !(options && options.allowCreatePcOverlayRun === false),
                skipBegin: options && options.skipPcOverlayBegin === true
            });
            var spotlight = getYuiGuideChatSpotlightElement(false);
            if (spotlight) {
                spotlight.hidden = true;
                spotlight.classList.remove('is-visible', 'is-window', 'is-input');
            }
            return;
        }

        updateYuiGuideChatSpotlight(yuiGuideChatSpotlightKind, pcOverlayRunId);
        ensureYuiGuideChatSpotlightTracking(pcOverlayRunId);
    }

    function applyYuiGuideChatCursorRelay(message) {
        if (!message || typeof message !== 'object' || !isStandaloneChatPage() || !document.body) return false;
        if (yuiGuidePcOverlayLifecycleClosed) {
            return false;
        }
        if (isYuiGuidePcOverlayRunEnded(message.tutorialRunId)) {
            return false;
        }
        var action = message.action || '';
        if (!isYuiGuideMessageForCurrentLifecycle(message)) {
            return false;
        }
        var cursorOptions = Object.assign({}, message, {
            pcOverlayRunId: message.pcOverlayRunId || getYuiGuidePcOverlayRunIdFromMessage(message)
        });
        if (action === 'yui_guide_drag_chat_cursor') {
            return applyYuiGuideChatCursorDrag(message.kind || '', cursorOptions);
        }
        if (action === 'yui_guide_arc_chat_cursor') {
            return applyYuiGuideChatCursorArc(message.kind || '', cursorOptions);
        }
        if (action === 'yui_guide_set_chat_cursor') {
            return applyYuiGuideChatCursor(message.kind || '', cursorOptions);
        }
        return false;
    }

    yuiGuideInterpageResources.addEventListener(window, 'neko:tutorial-overlay-relay', function (event) {
        applyYuiGuideChatCursorRelay(event && event.detail);
    });

    yuiGuideInterpageResources.addEventListener(window, 'message', function (event) {
        if (event.origin !== window.location.origin) return;
        var data = event.data || {};
        if (!data || data.action !== '__nekoTutorialOverlayRelay') return;
        applyYuiGuideChatCursorRelay(Object.assign({}, data.detail || {}, {
            freezePoint: event.data.freezePoint === true || (data.detail && data.detail.freezePoint === true)
        }));
    });

    function clearYuiGuidePcOverlayBridgeState(reason, tutorialRunId) {
        var rawReason = typeof reason === 'string' && reason ? reason : 'lifecycle-ended';
        var endedRunId = typeof tutorialRunId === 'string' && tutorialRunId
            ? tutorialRunId
            : getExistingYuiGuidePcOverlayRunId();
        if (endedRunId) {
            yuiGuidePcOverlayEndedRunId = endedRunId;
        }
        closeYuiGuidePcOverlayLifecycle();
        yuiGuidePcOverlayActive = false;
        yuiGuidePcOverlayReady = false;
        yuiGuidePcOverlayRunIdOverride = '';
        yuiGuidePcOverlaySpotlights = [];
        yuiGuidePcOverlayCursor = null;
        clearYuiGuideChatPcSpotlightRects();
        try {
            window.localStorage.removeItem('yuiGuidePcOverlayRunId');
        } catch (_) {}
        yuiGuideChatCursorRequestToken += 1;
        yuiGuideChatCursorArcRequestToken += 1;
        yuiGuideCompactToolWheelRotateRetryToken += 1;
        applyYuiGuideChatSpotlight('', {
            pcOverlayRunId: endedRunId,
            allowCreatePcOverlayRun: false,
            skipPcOverlayBegin: true
        });
        applyYuiGuideChatCursor('', {
            pcOverlayRunId: endedRunId,
            allowCreatePcOverlayRun: false,
            skipPcOverlayBegin: true
        });
        applyYuiGuideChatLockState(false);
        applyYuiGuideChatInputLocked(false, rawReason);
        applyYuiGuideCompactChatFixedLayout(false);
        applyYuiGuideAvatarToolMenuOpen(false, rawReason);
        applyYuiGuideCompactHistoryOpen(false, rawReason);
        applyYuiGuideCompactToolFanOpen(false, rawReason);
        clearYuiGuideChatMessages();
        if (
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.clear === 'function'
        ) {
            try {
                var clearResult = window.nekoTutorialOverlay.clear({
                    reason: rawReason,
                    tutorialRunId: endedRunId
                });
                Promise.resolve(clearResult).then(function (result) {
                    if (result && (result.stale === true || result.ok === false)) {
                        window.nekoTutorialOverlay.clear({ reason: rawReason });
                    }
                }).catch(function () {
                    try {
                        window.nekoTutorialOverlay.clear({ reason: rawReason });
                    } catch (_) {}
                });
            } catch (_) {}
        }
    }

    // =====================================================================
    // Cross-window handoff event forwarding via BroadcastChannel
    // =====================================================================

    // 首页发出 handoff-sent DOM 事件时，转发到 BC 让其他标签页感知
    function cleanupAppInterpageTransientResources() {
        clearYuiGuideChatFlushTimer();
        clearIcebreakerBridgeFlushTimer();
        stopIdleChatCompactSurfaceHeartbeat();
        clearYuiGuideChatSpotlightTracking();
    }

    yuiGuideInterpageResources.addEventListener(window, 'pagehide', cleanupAppInterpageTransientResources);

    yuiGuideInterpageResources.addEventListener(window, 'neko:yui-guide:handoff-sent', function (evt) {
        if (_isRelayingYuiGuideHandoffSent) return;
        postInterpageMessage({
            action: 'handoff_sent',
            detail: evt.detail || {},
            timestamp: Date.now()
        });
    });

    // =====================================================================
    // Cross-window avatar forwarding via BroadcastChannel
    // =====================================================================

    // Pet 窗口（/index）捕获头像后，通过 BC 广播给 Chat 窗口
    yuiGuideInterpageResources.addEventListener(window, 'chat-avatar-preview-updated', function (evt) {
        // source === 'ipc' 表示此事件来自 BC 注入（setExternalAvatar），不回传避免循环
        var eventSource = evt.detail && evt.detail.source;
        if (eventSource === 'ipc' || eventSource === 'tutorial_override' || eventSource === 'tutorial_override_clear') return;
        var dataUrl = evt.detail && evt.detail.dataUrl;
        if (!dataUrl) return;
        postYuiGuideMessageToChat('avatar_updated', {
            lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
            dataUrl: dataUrl,
            modelType: (evt.detail && evt.detail.modelType) || ''
        });
    });

    yuiGuideInterpageResources.addEventListener(window, 'neko:idle-chat-minimized-state', function (evt) {
        var detail = evt && evt.detail && typeof evt.detail === 'object' ? evt.detail : null;
        if (!detail || detail.via === 'broadcast-channel') return;
        postInterpageMessage(Object.assign({
            action: 'idle_chat_minimized_state',
            source: 'chat-window',
            lanlan_name: getCurrentLanlanName(),
            timestamp: Date.now()
        }, detail));
    });

    yuiGuideInterpageResources.addEventListener(window, 'neko:compact-surface-layout-change', function (evt) {
        var detail = evt && evt.detail && typeof evt.detail === 'object' ? evt.detail : null;
        postIdleChatCompactSurfaceState(detail);
    });

    // Chat 窗口初始化时，向 Pet 窗口请求当前已缓存的头像
    if (isStandaloneChatPage()) {
        var GOODBYE_COMPOSER_REQUEST_RETRY_DELAYS_MS = [100, 300, 700, 1500, 3000, 5000];
        var goodbyeComposerRequestRetryIndex = 0;
        var goodbyeComposerRequestTimer = 0;
        var postAvatarRequest = function () {
            postYuiGuideMessageToPet('request_avatar', {
                lanlan_name: getCurrentLanlanName()
            });
        };
        var scheduleGoodbyeComposerRequest = function (delayMs) {
            yuiGuideInterpageResources.clearTimeout(goodbyeComposerRequestTimer);
            goodbyeComposerRequestTimer = yuiGuideInterpageResources.setTimeout(function () {
                goodbyeComposerRequestTimer = 0;
                postGoodbyeComposerRequest();
            }, Math.max(0, delayMs || 0));
        };
        var postGoodbyeComposerRequest = function () {
            if (requestGoodbyeChatComposerHiddenState('standalone-chat-state-request')) {
                goodbyeComposerRequestRetryIndex = 0;
                return;
            }
            if (goodbyeComposerRequestRetryIndex < GOODBYE_COMPOSER_REQUEST_RETRY_DELAYS_MS.length) {
                scheduleGoodbyeComposerRequest(
                    GOODBYE_COMPOSER_REQUEST_RETRY_DELAYS_MS[goodbyeComposerRequestRetryIndex++]
                );
            }
        };
        var postStandaloneChatStateRequests = function () {
            postAvatarRequest();
            scheduleGoodbyeComposerRequest(0);
        };
        if (nekoBroadcastChannel || getGoodbyeChatComposerHiddenElectronBridge()) {
            postAvatarRequest();
            postGoodbyeComposerRequest();
            postYuiGuideMessageToPet('request_tutorial_chat_identity');
            postYuiGuideMessageToPet('yui_guide_chat_ready');
            yuiGuideInterpageResources.setTimeout(drainPendingYuiGuideChatBridgeQueue, 0);
            // 配置注入后统一重新请求状态（postStandaloneChatStateRequests 内部已含头像与 goodbye composer 隐藏状态请求，避免重复补发）
            yuiGuideInterpageResources.addEventListener(window, 'neko:config-injected', postStandaloneChatStateRequests);
            yuiGuideInterpageResources.addEventListener(window, 'neko:request-goodbye-chat-composer-hidden-state', function () {
                scheduleGoodbyeComposerRequest(0);
            });
            yuiGuideInterpageResources.addEventListener(window, 'focus', function () {
                scheduleGoodbyeComposerRequest(0);
            });
            yuiGuideInterpageResources.addEventListener(document, 'visibilitychange', function () {
                if (!document.hidden) {
                    scheduleGoodbyeComposerRequest(0);
                }
            });
        }
    }

    // =====================================================================
    // postMessage listeners (fallback for memory_edited & model_saved)
    // =====================================================================

    // Memory-edited from iframe (postMessage fallback)
    window.addEventListener('message', async function (event) {
        // Security: same-origin check
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的 memory_edited 消息:', event.origin);
            return;
        }

        if (event.data && event.data.type === 'memory_edited') {
            await handleMemoryEdited(event.data.catgirl_name);
        }
    });

    // Model-saved / reload_model from model_manager window (postMessage fallback)
    window.addEventListener('message', async function (event) {
        // Security: same-origin check
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的消息:', event.origin);
            return;
        }

        // Verify source is a known window (opener or child)
        if (event.source && event.source !== window.opener && !event.source.parent) {
            console.warn('[Security] 拒绝来自未知窗口的消息');
            return;
        }

        if (event.data && (event.data.action === 'model_saved' || event.data.action === 'reload_model')) {
            // Deduplicate: same message arrives via both BC and postMessage
            if (
                !shouldBypassYuiGuideMessageDedup(event.data.action, event.data)
                && isDuplicateMessage(event.data.action, event.data.timestamp)
            ) {
                console.log('[Model] 跳过重复 postMessage:', event.data.action);
                return;
            }
            console.log('[Model] 通过 postMessage 收到模型重载通知');
            await handleModelReload(event.data?.lanlan_name, event.data?.reloadOptions);
        }
    });

    // 音色应用页的后备通道：没有 BroadcastChannel 时使用 postMessage 同步准备态
    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的音色切换消息:', event.origin);
            return;
        }
        var data = event.data || {};
        if (data.action !== 'voice_config_switching' && data.type !== 'voice_config_switching') {
            return;
        }
        handleVoiceConfigSwitchingMessage(data);
    });

    window.addEventListener('neko:electron-goodbye-chat-composer-hidden', function (event) {
        handleGoodbyeChatComposerHiddenMessage((event && event.detail) || {}, 'electron-ipc');
    });

    window.addEventListener('neko:electron-voice-chat-composer-hidden', function (event) {
        handleVoiceChatComposerHiddenMessage((event && event.detail) || {});
    });

    window.addEventListener('neko:config-injected', function (event) {
        var detail = (event && event.detail) || {};
        consumePendingVoiceChatComposerHiddenMessage(
            getCurrentLanlanName() || detail.lanlan_name || ''
        );
    });

    window.addEventListener('message', function (event) {
        if (event.origin !== window.location.origin) {
            console.warn('[Security] 拒绝来自不同源的 idle_activity 消息:', event.origin);
            return;
        }
        var data = event.data || {};
        if (data.action !== 'idle_activity' && data.type !== 'idle_activity') {
            return;
        }
        if (isDuplicateMessage('idle_activity', data.timestamp)) {
            return;
        }
        var idleCurrentName = getCurrentLanlanName();
        if (data.lanlan_name && (!idleCurrentName || data.lanlan_name !== idleCurrentName)) {
            return;
        }
        dispatchCrossWindowIdleActivity({
            source: data.source || 'interaction',
            kind: data.kind === 'conversation' ? 'conversation' : 'interaction',
            via: 'post-message',
            timestamp: data.timestamp || Date.now()
        });
    });

    // N.E.K.O.-PC 多窗口兜底：由 Electron 主进程广播音色切换准备态
    window.addEventListener('neko:electron-voice-config-switching', function (event) {
        handleVoiceConfigSwitchingMessage((event && event.detail) || {});
    });

    // =====================================================================
    // Reset current avatar to the built-in default Live2D model
    //
    // Triggered from the Electron tray "Advanced Settings → Reset to Default
    // Avatar" menu via the `reset-to-default-model` IPC. Persists the change
    // through the standard PUT /api/characters/catgirl/l2d/<name> endpoint so
    // the choice survives a reload, then triggers handleModelReload to swap
    // the current MMD/VRM/Live2D model live.
    // =====================================================================
    var DEFAULT_LIVE2D_MODEL_NAME = 'yui-origin';
    var _resetToDefaultModelInFlight = false;

    async function resetToDefaultModel() {
        if (_resetToDefaultModelInFlight) {
            console.log('[Model] resetToDefaultModel 已在执行中，忽略重复请求');
            return { success: false, error: 'already_in_flight' };
        }
        _resetToDefaultModelInFlight = true;

        var lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
        try {
            // Fail-fast when there is no character context. This happens if the
            // tray IPC fires before `neko:config-injected`, or on a sub-window
            // that never received the injection. Without lanlan_name we cannot
            // PUT the persistence change, and handleModelReload('') would
            // simply re-fetch the unchanged config — masking a no-op as success.
            if (!lanlanName) {
                console.warn('[Model] resetToDefaultModel: 当前没有 lanlan_name，无法持久化默认模型设置');
                throw new Error('missing_lanlan_name');
            }

            // Persist the change so that future reloads keep the default avatar.
            var putUrl = '/api/characters/catgirl/l2d/' + encodeURIComponent(lanlanName);
            var putResp = await fetch(putUrl, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    model_type: 'live2d',
                    live2d: DEFAULT_LIVE2D_MODEL_NAME,
                    live2d_idle_animation: null
                })
            });
            if (!putResp.ok) {
                var errText = '';
                try { errText = await putResp.text(); } catch (_) {}
                throw new Error('HTTP ' + putResp.status + (errText ? (': ' + errText) : ''));
            }

            // Trigger the live model swap. handleModelReload re-fetches the
            // page_config, so it will pick up the freshly-saved default Live2D
            // model and recycle the VRM/MMD overlays as needed.
            // suppressToast: this caller owns the success/failure toast.
            // throwOnError: handleModelReload's own catch swallows errors; we
            // need them surfaced so the reset doesn't report success after a
            // failed hot-swap.
            var reloadOpts = { suppressToast: true, throwOnError: true };
            if (typeof handleModelReload === 'function') {
                await handleModelReload(lanlanName, reloadOpts);
            } else if (typeof window.handleModelReload === 'function') {
                await window.handleModelReload(lanlanName, reloadOpts);
            } else {
                console.warn('[Model] handleModelReload 不可用，跳过热切换');
            }

            try {
                if (typeof window.showStatusToast === 'function') {
                    window.showStatusToast(
                        (window.t && window.t('model.resetToDefaultSuccess')) || '已恢复默认模型',
                        3000
                    );
                }
            } catch (_) {}

            return { success: true };
        } catch (e) {
            console.error('[Model] 恢复默认模型失败:', e);
            try {
                if (typeof window.showStatusToast === 'function') {
                    window.showStatusToast(
                        (window.t && window.t('model.resetToDefaultFailed')) || '恢复默认模型失败',
                        4000
                    );
                }
            } catch (_) {}
            return { success: false, error: (e && e.message) || String(e) };
        } finally {
            _resetToDefaultModelInFlight = false;
        }
    }

    // =====================================================================
    // Public API
    // =====================================================================

    mod.nekoBroadcastChannel = nekoBroadcastChannel;
    mod.handleModelReload = handleModelReload;
    mod.resetToDefaultModel = resetToDefaultModel;
    mod.handleHideMainUI = handleHideMainUI;
    mod.handleShowMainUI = handleShowMainUI;
    mod.isMainUIHiddenByModelManager = isMainUIHiddenByModelManager;
    mod.handleMemoryEdited = handleMemoryEdited;
    mod.cleanupLive2DOverlayUI = cleanupLive2DOverlayUI;
    mod.cleanupVRMOverlayUI = cleanupVRMOverlayUI;
    mod.cleanupMMDOverlayUI = cleanupMMDOverlayUI;
    mod.cleanupPNGTuberOverlayUI = cleanupPNGTuberOverlayUI;
    mod.syncVoiceChatComposerHidden = syncVoiceChatComposerHidden;
    mod.shouldKeepVoiceComposerHidden = shouldKeepVoiceComposerHidden;
    mod.applyVoiceComposerHiddenFromActive = applyVoiceComposerHiddenFromActive;
    mod.postVoiceChatComposerHiddenElectron = postVoiceChatComposerHiddenElectron;
    mod.handleVoiceChatComposerHiddenMessage = handleVoiceChatComposerHiddenMessage;
    mod.consumePendingVoiceChatComposerHiddenMessage = consumePendingVoiceChatComposerHiddenMessage;
    mod.applyGoodbyeChatComposerHidden = applyGoodbyeChatComposerHidden;
    mod.postGoodbyeChatComposerHiddenElectron = postGoodbyeChatComposerHiddenElectron;
    mod.handleGoodbyeChatComposerHiddenMessage = handleGoodbyeChatComposerHiddenMessage;
    mod.postGoodbyeChatComposerHiddenState = postGoodbyeChatComposerHiddenState;
    mod.requestGoodbyeChatComposerHiddenState = requestGoodbyeChatComposerHiddenState;
    mod.postIcebreakerBridgeEvent = postIcebreakerBridgeEvent;
    mod.postIcebreakerChoiceSelected = postIcebreakerChoiceSelected;
    mod.postIcebreakerFreeTextSubmitted = postIcebreakerFreeTextSubmitted;
    mod.isVoiceConfigSwitching = isVoiceConfigSwitching;
    mod.waitForVoiceConfigSwitchReady = waitForVoiceConfigSwitchReady;
    mod.applyTutorialChatIdentityOverride = applyTutorialChatIdentityOverride;

    // Backward-compatible window globals
    window.handleModelReload = handleModelReload;
    window.resetToDefaultModel = resetToDefaultModel;
    window.handleHideMainUI = handleHideMainUI;
    window.handleShowMainUI = handleShowMainUI;
    window.isMainUIHiddenByModelManager = isMainUIHiddenByModelManager;
    window.cleanupLive2DOverlayUI = cleanupLive2DOverlayUI;
    window.cleanupVRMOverlayUI = cleanupVRMOverlayUI;
    window.cleanupMMDOverlayUI = cleanupMMDOverlayUI;
    window.cleanupPNGTuberOverlayUI = cleanupPNGTuberOverlayUI;
    window.syncVoiceChatComposerHidden = syncVoiceChatComposerHidden;
    window.shouldKeepVoiceComposerHidden = shouldKeepVoiceComposerHidden;
    window.applyGoodbyeChatComposerHidden = applyGoodbyeChatComposerHidden;
    window.postGoodbyeChatComposerHiddenState = postGoodbyeChatComposerHiddenState;
    window.requestGoodbyeChatComposerHiddenState = requestGoodbyeChatComposerHiddenState;
    window.isVoiceConfigSwitching = isVoiceConfigSwitching;
    window.waitForVoiceConfigSwitchReady = waitForVoiceConfigSwitchReady;

    window.appInterpage = mod;
})();
