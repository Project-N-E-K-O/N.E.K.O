/**
 * app-interpage/listeners-and-api.js
 * Inter-page / cross-tab communication.
 *
 * Handles BroadcastChannel dispatch, postMessage listeners, model hot-reload, UI commands, and overlay cleanup.
 * Dependencies loaded before these parts:
 * - app-state.js -> window.appState, window.appConst
 * Runtime dependencies available by the time handlers fire:
 * - window.showStatusToast
 * - window.stopMicCapture / window.clearAudioQueue
 * - window.live2dManager / window.vrmManager
 * - initLive2DModel / initVRMModel globals
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appInterpage = window.appInterpage || {};
    const I = window.__appInterpageParts || (window.__appInterpageParts = {});

    // The former single IIFE could not receive cross-window relay events until
    // all hoisted lifecycle helpers were ready. Bind only in the final part to
    // preserve that ordering across parser-blocking external scripts.
    if (I.nekoBroadcastChannel && typeof I.handleNekoBroadcastMessage === 'function') {
        I.nekoBroadcastChannel.onmessage = I.handleNekoBroadcastMessage;
    }
    I.yuiGuideInterpageResources.addEventListener(
        window,
        'neko:tutorial-overlay-relay',
        I.handleYuiGuideRelayedCustomEvent
    );
    I.yuiGuideInterpageResources.addEventListener(
        window,
        'message',
        I.handleYuiGuideRelayedWindowMessage
    );

    function cleanupAppInterpageTransientResources() {
        I.clearYuiGuideChatFlushTimer();
        I.clearIcebreakerBridgeFlushTimer();
        I.stopIdleChatCompactSurfaceHeartbeat();
        I.clearYuiGuideChatSpotlightTracking();
    }

    I.yuiGuideInterpageResources.addEventListener(window, 'pagehide', cleanupAppInterpageTransientResources);

    I.yuiGuideInterpageResources.addEventListener(window, 'neko:yui-guide:handoff-sent', function (evt) {
        if (I._isRelayingYuiGuideHandoffSent) return;
        I.postInterpageMessage({
            action: 'handoff_sent',
            detail: evt.detail || {},
            timestamp: Date.now()
        });
    });

    // =====================================================================
    // Cross-window avatar forwarding via BroadcastChannel
    // =====================================================================

    // Pet 窗口（/index）捕获头像后，通过 BC 广播给 Chat 窗口
    I.yuiGuideInterpageResources.addEventListener(window, 'chat-avatar-preview-updated', function (evt) {
        // source === 'ipc' 表示此事件来自 BC 注入（setExternalAvatar），不回传避免循环
        var eventSource = evt.detail && evt.detail.source;
        if (eventSource === 'ipc' || eventSource === 'tutorial_override' || eventSource === 'tutorial_override_clear') return;
        var dataUrl = evt.detail && evt.detail.dataUrl;
        if (!dataUrl) return;
        I.postYuiGuideMessageToChat('avatar_updated', {
            lanlan_name: (window.lanlan_config && window.lanlan_config.lanlan_name) || '',
            dataUrl: dataUrl,
            modelType: (evt.detail && evt.detail.modelType) || ''
        });
    });

    I.yuiGuideInterpageResources.addEventListener(window, 'neko:idle-chat-minimized-state', function (evt) {
        var detail = evt && evt.detail && typeof evt.detail === 'object' ? evt.detail : null;
        if (!detail || detail.via === 'broadcast-channel') return;
        I.postInterpageMessage(Object.assign({
            action: 'idle_chat_minimized_state',
            source: 'chat-window',
            lanlan_name: I.getCurrentLanlanName(),
            timestamp: Date.now()
        }, detail));
    });

    I.yuiGuideInterpageResources.addEventListener(window, 'neko:compact-surface-layout-change', function (evt) {
        var detail = evt && evt.detail && typeof evt.detail === 'object' ? evt.detail : null;
        I.postIdleChatCompactSurfaceState(detail);
    });

    // Chat 窗口初始化时，向 Pet 窗口请求当前已缓存的头像
    if (I.isStandaloneChatPage()) {
        var GOODBYE_COMPOSER_REQUEST_RETRY_DELAYS_MS = [100, 300, 700, 1500, 3000, 5000];
        var goodbyeComposerRequestRetryIndex = 0;
        var goodbyeComposerRequestTimer = 0;
        var postAvatarRequest = function () {
            I.postYuiGuideMessageToPet('request_avatar', {
                lanlan_name: I.getCurrentLanlanName()
            });
        };
        var scheduleGoodbyeComposerRequest = function (delayMs) {
            I.yuiGuideInterpageResources.clearTimeout(goodbyeComposerRequestTimer);
            goodbyeComposerRequestTimer = I.yuiGuideInterpageResources.setTimeout(function () {
                goodbyeComposerRequestTimer = 0;
                postGoodbyeComposerRequest();
            }, Math.max(0, delayMs || 0));
        };
        var postGoodbyeComposerRequest = function () {
            if (I.requestGoodbyeChatComposerHiddenState('standalone-chat-state-request')) {
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
        if (I.nekoBroadcastChannel || I.getGoodbyeChatComposerHiddenElectronBridge()) {
            postAvatarRequest();
            postGoodbyeComposerRequest();
            I.postYuiGuideMessageToPet('request_tutorial_chat_identity');
            I.postYuiGuideMessageToPet('yui_guide_chat_ready');
            I.yuiGuideInterpageResources.setTimeout(I.drainPendingYuiGuideChatBridgeQueue, 0);
            // 配置注入后统一重新请求状态（postStandaloneChatStateRequests 内部已含头像与 goodbye composer 隐藏状态请求，避免重复补发）
            I.yuiGuideInterpageResources.addEventListener(window, 'neko:config-injected', postStandaloneChatStateRequests);
            I.yuiGuideInterpageResources.addEventListener(window, 'neko:request-goodbye-chat-composer-hidden-state', function () {
                scheduleGoodbyeComposerRequest(0);
            });
            I.yuiGuideInterpageResources.addEventListener(window, 'focus', function () {
                scheduleGoodbyeComposerRequest(0);
            });
            I.yuiGuideInterpageResources.addEventListener(document, 'visibilitychange', function () {
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
            await I.handleMemoryEdited(event.data.catgirl_name);
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

        if (event.data && (
            event.data.action === 'model_saved'
            || event.data.action === 'reload_model'
            || event.data.action === 'reload_model_parameters'
        )) {
            // Deduplicate: same message arrives via both BC and postMessage
            if (
                !I.shouldBypassYuiGuideMessageDedup(event.data.action, event.data)
                && I.isDuplicateMessage(event.data.action, event.data.timestamp)
            ) {
                console.log('[Model] 跳过重复 postMessage:', event.data.action);
                return;
            }
            if (event.data.action === 'reload_model_parameters') {
                await I.handleReloadModelParametersMessage(event.data);
                return;
            }
            console.log('[Model] 通过 postMessage 收到模型重载通知');
            await I.handleModelReload(event.data?.lanlan_name, event.data?.reloadOptions);
        }
    });

    // 参数编辑器在 BroadcastChannel 不可用时使用 localStorage 触发跨窗口消息。
    window.addEventListener('storage', async function (event) {
        if (event.key !== 'nekopage_message' || !event.newValue) return;
        var message;
        try {
            message = JSON.parse(event.newValue);
        } catch (_) {
            return;
        }
        if (!message || message.action !== 'reload_model_parameters') return;
        if (I.isDuplicateMessage(message.action, message.timestamp)) return;
        await I.handleReloadModelParametersMessage(message);
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
        I.handleVoiceConfigSwitchingMessage(data);
    });

    window.addEventListener('neko:electron-goodbye-chat-composer-hidden', function (event) {
        I.handleGoodbyeChatComposerHiddenMessage((event && event.detail) || {}, 'electron-ipc');
    });

    window.addEventListener('neko:electron-voice-chat-composer-hidden', function (event) {
        I.handleVoiceChatComposerHiddenMessage((event && event.detail) || {});
    });

    window.addEventListener('neko:config-injected', function (event) {
        var detail = (event && event.detail) || {};
        var lanlanName = I.getCurrentLanlanName() || detail.lanlan_name || '';
        I.consumePendingVoiceChatComposerHiddenMessage(lanlanName);
        I.consumePendingGoodbyeChatComposerHiddenMessage(lanlanName);
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
        if (I.isDuplicateMessage('idle_activity', data.timestamp)) {
            return;
        }
        var idleCurrentName = I.getCurrentLanlanName();
        if (data.lanlan_name && (!idleCurrentName || data.lanlan_name !== idleCurrentName)) {
            return;
        }
        I.dispatchCrossWindowIdleActivity({
            source: data.source || 'interaction',
            kind: data.kind === 'conversation' ? 'conversation' : 'interaction',
            via: 'post-message',
            timestamp: data.timestamp || Date.now()
        });
    });

    // N.E.K.O.-PC 多窗口兜底：由 Electron 主进程广播音色切换准备态
    window.addEventListener('neko:electron-voice-config-switching', function (event) {
        I.handleVoiceConfigSwitchingMessage((event && event.detail) || {});
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
            if (typeof I.handleModelReload === 'function') {
                await I.handleModelReload(lanlanName, reloadOpts);
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

    I.mod.nekoBroadcastChannel = I.nekoBroadcastChannel;
    I.mod.handleModelReload = I.handleModelReload;
    I.mod.resetToDefaultModel = resetToDefaultModel;
    I.mod.handleHideMainUI = I.handleHideMainUI;
    I.mod.handleShowMainUI = I.handleShowMainUI;
    I.mod.isMainUIHiddenByModelManager = I.isMainUIHiddenByModelManager;
    I.mod.handleMemoryEdited = I.handleMemoryEdited;
    I.mod.cleanupLive2DOverlayUI = I.cleanupLive2DOverlayUI;
    I.mod.cleanupVRMOverlayUI = I.cleanupVRMOverlayUI;
    I.mod.cleanupMMDOverlayUI = I.cleanupMMDOverlayUI;
    I.mod.cleanupPNGTuberOverlayUI = I.cleanupPNGTuberOverlayUI;
    I.mod.syncVoiceChatComposerHidden = I.syncVoiceChatComposerHidden;
    I.mod.shouldKeepVoiceComposerHidden = I.shouldKeepVoiceComposerHidden;
    I.mod.applyVoiceComposerHiddenFromActive = I.applyVoiceComposerHiddenFromActive;
    I.mod.postVoiceChatComposerHiddenElectron = I.postVoiceChatComposerHiddenElectron;
    I.mod.handleVoiceChatComposerHiddenMessage = I.handleVoiceChatComposerHiddenMessage;
    I.mod.consumePendingVoiceChatComposerHiddenMessage = I.consumePendingVoiceChatComposerHiddenMessage;
    I.mod.applyGoodbyeChatComposerHidden = I.applyGoodbyeChatComposerHidden;
    I.mod.postGoodbyeChatComposerHiddenElectron = I.postGoodbyeChatComposerHiddenElectron;
    I.mod.handleGoodbyeChatComposerHiddenMessage = I.handleGoodbyeChatComposerHiddenMessage;
    I.mod.postGoodbyeChatComposerHiddenState = I.postGoodbyeChatComposerHiddenState;
    I.mod.requestGoodbyeChatComposerHiddenState = I.requestGoodbyeChatComposerHiddenState;
    I.mod.postCatLocalTextSubmit = I.postCatLocalTextSubmit;
    I.mod.postIcebreakerBridgeEvent = I.postIcebreakerBridgeEvent;
    I.mod.postIcebreakerChoiceSelected = I.postIcebreakerChoiceSelected;
    I.mod.postIcebreakerFreeTextSubmitted = I.postIcebreakerFreeTextSubmitted;
    I.mod.isVoiceConfigSwitching = I.isVoiceConfigSwitching;
    I.mod.waitForVoiceConfigSwitchReady = I.waitForVoiceConfigSwitchReady;
    I.mod.applyTutorialChatIdentityOverride = I.applyTutorialChatIdentityOverride;

    // Backward-compatible window globals
    window.handleModelReload = I.handleModelReload;
    window.resetToDefaultModel = resetToDefaultModel;
    window.handleHideMainUI = I.handleHideMainUI;
    window.handleShowMainUI = I.handleShowMainUI;
    window.isMainUIHiddenByModelManager = I.isMainUIHiddenByModelManager;
    window.cleanupLive2DOverlayUI = I.cleanupLive2DOverlayUI;
    window.cleanupVRMOverlayUI = I.cleanupVRMOverlayUI;
    window.cleanupMMDOverlayUI = I.cleanupMMDOverlayUI;
    window.cleanupPNGTuberOverlayUI = I.cleanupPNGTuberOverlayUI;
    window.syncVoiceChatComposerHidden = I.syncVoiceChatComposerHidden;
    window.shouldKeepVoiceComposerHidden = I.shouldKeepVoiceComposerHidden;
    window.applyGoodbyeChatComposerHidden = I.applyGoodbyeChatComposerHidden;
    window.postGoodbyeChatComposerHiddenState = I.postGoodbyeChatComposerHiddenState;
    window.requestGoodbyeChatComposerHiddenState = I.requestGoodbyeChatComposerHiddenState;
    window.postCatLocalTextSubmit = I.postCatLocalTextSubmit;
    window.isVoiceConfigSwitching = I.isVoiceConfigSwitching;
    window.waitForVoiceConfigSwitchReady = I.waitForVoiceConfigSwitchReady;

    Object.assign(window.appInterpage, I.mod || {});
    delete window.__appInterpageParts;
})();
