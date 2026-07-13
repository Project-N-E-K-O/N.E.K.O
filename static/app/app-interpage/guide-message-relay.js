/**
 * app-interpage/guide-message-relay.js
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

    // Hoisted in the former single-file IIFE. Keep it available before the
    // BroadcastChannel setup and eager standalone relay binding below.
    I.isStandaloneChatPage = function isStandaloneChatPage() {
        var pathname = (window.location && window.location.pathname) || '';
        return pathname === '/chat' || pathname === '/chat/' || pathname === '/chat_full' || pathname === '/chat_full/';
    };

    try {
        if (typeof BroadcastChannel !== 'undefined') {
            I.nekoBroadcastChannel = new BroadcastChannel('neko_page_channel');
            console.log('[BroadcastChannel] 主页面 BroadcastChannel 已初始化');

            I.handleNekoBroadcastMessage = async function (event) {
                var message = event.data;
                if (!message || !message.action) {
                    return;
                }

                // Deduplicate: same message arrives via both BC and postMessage
                if (
                    !I.isIcebreakerBridgeAction(message.action)
                    &&
                    !I.shouldBypassYuiGuideMessageDedup(message.action, message)
                    && I.isDuplicateMessage(message.action, message.timestamp)
                ) {
                    console.log('[BroadcastChannel] 跳过重复消息:', message.action);
                    return;
                }

                if (I.isYuiGuideLifecycleStartAction(message.action)) {
                    I.openYuiGuidePcOverlayLifecycle(message);
                }
                if (
                    I.yuiGuidePcOverlayLifecycleClosed
                    && I.isYuiGuideLifecycleScopedAction(message.action)
                ) {
                    return;
                }
                if (!I.isYuiGuideMessageForCurrentLifecycle(message)) {
                    return;
                }

                if (
                    message.action !== 'yui_guide_tutorial_lifecycle_ended'
                    && I.isYuiGuideLifecycleScopedAction(message.action)
                    && I.isYuiGuidePcOverlayRunEnded(message.tutorialRunId)
                ) {
                    I.clearYuiGuidePcOverlayBridgeState('stale-after-lifecycle-ended', message.tutorialRunId || '');
                    return;
                }

                if (!I.isHighVolumeBroadcastChannelAction(message.action)) {
                    console.log('[BroadcastChannel] 收到消息:', message.action);
                }

                switch (event.data.action) {
                    case 'reload_model':
                        await I.handleModelReload(event.data?.lanlan_name, event.data?.reloadOptions);
                        break;
                    case 'reload_model_parameters':
                        await I.handleReloadModelParametersMessage(event.data);
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
                        await I.handleMemoryEdited(event.data.catgirl_name);
                        break;
                    case 'voice_chat_active': {
                        // 来自另一个窗口的语音对话状态变更，同步本地 React composer 隐藏状态
                        // 校验 lanlan_name：多角色场景下避免串状态
                        I.handleVoiceChatComposerHiddenMessage(event.data);
                        break;
                    }
                    case 'goodbye_chat_composer_hidden': {
                        I.handleGoodbyeChatComposerHiddenMessage(event.data, 'broadcast');
                        break;
                    }
                    case 'request_goodbye_chat_composer_hidden': {
                        I.handleGoodbyeChatComposerHiddenMessage(event.data, 'broadcast-request');
                        break;
                    }
                    case 'idle_activity': {
                        var idleCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!idleCurrentName || event.data.lanlan_name !== idleCurrentName)) break;
                        I.dispatchCrossWindowIdleActivity({
                            source: event.data.source || 'interaction',
                            kind: event.data.kind === 'conversation' ? 'conversation' : 'interaction',
                            via: 'broadcast-channel',
                            timestamp: event.data.timestamp || Date.now()
                        });
                        break;
                    }
                    case 'idle_return_ball_state': {
                        var idleReturnCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!idleReturnCurrentName || event.data.lanlan_name !== idleReturnCurrentName)) break;
                        dispatchIdleReturnBallState(event.data);
                        break;
                    }
                    case 'idle_chat_minimized_state': {
                        var idleChatCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!idleChatCurrentName || event.data.lanlan_name !== idleChatCurrentName)) break;
                        dispatchIdleChatMinimizedState(event.data);
                        break;
                    }
                    case 'idle_chat_compact_surface_state': {
                        var compactSurfaceCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!compactSurfaceCurrentName || event.data.lanlan_name !== compactSurfaceCurrentName)) break;
                        dispatchIdleChatCompactSurfaceState(event.data);
                        break;
                    }
                    case 'idle_cat1_compact_mirror_state': {
                        var cat1MirrorCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!cat1MirrorCurrentName || event.data.lanlan_name !== cat1MirrorCurrentName)) break;
                        dispatchIdleCat1CompactMirrorState(event.data);
                        break;
                    }
                    case 'idle_cat1_play_yarn_visibility': {
                        var cat1PlayYarnCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!cat1PlayYarnCurrentName || event.data.lanlan_name !== cat1PlayYarnCurrentName)) break;
                        dispatchIdleCat1PlayYarnVisibility(event.data);
                        break;
                    }
                    case 'idle_cat1_playground_yarn_request': {
                        var cat1PlaygroundYarnCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!cat1PlaygroundYarnCurrentName || event.data.lanlan_name !== cat1PlaygroundYarnCurrentName)) break;
                        dispatchIdleCat1PlaygroundYarnRequest(event.data);
                        break;
                    }
                    case 'idle_chat_pair_move_bounds': {
                        var pairMoveChatCurrentName = I.getCurrentLanlanName();
                        if (event.data.lanlan_name && (!pairMoveChatCurrentName || event.data.lanlan_name !== pairMoveChatCurrentName)) break;
                        dispatchIdleChatPairMoveBounds(event.data);
                        break;
                    }
                    case 'voice_config_switching': {
                        I.handleVoiceConfigSwitchingMessage(event.data);
                        break;
                    }
                    case 'icebreaker_append_chat_message':
                    case 'icebreaker_set_choice_prompt':
                    case 'icebreaker_clear_choice_prompt':
                    case 'icebreaker_choice_selected':
                    case 'icebreaker_free_text_submitted': {
                        I.handleIcebreakerBridgeData(event.data);
                        break;
                    }
                    case 'yui_guide_append_chat_message': {
                        I.appendYuiGuideChatMessage(event.data.message);
                        break;
                    }
                    case 'yui_guide_update_chat_message': {
                        I.updateYuiGuideChatMessage(event.data.messageId, event.data.patch);
                        break;
                    }
                    case 'yui_guide_clear_chat_messages': {
                        I.clearYuiGuideChatMessages();
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
                        I.applyTutorialChatIdentityOverride(event.data);
                        break;
                    }
                    case 'request_tutorial_chat_identity': {
                        if (I.isStandaloneChatPage()) break;
                        if (window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__) {
                            I.postYuiGuideMessageToChat(
                                'tutorial_chat_identity_override',
                                window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__
                            );
                        }
                        break;
                    }
                    case 'request_avatar': {
                        // 仅 Pet 主窗口（/index）应答，Chat 窗口不回传
                        if (I.isStandaloneChatPage()) break;
                        // 校验 lanlan_name：与 avatar_updated 对称，本地名未就绪或不匹配时不回包
                        const reqCurrentName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
                        if (event.data.lanlan_name && (!reqCurrentName || event.data.lanlan_name !== reqCurrentName)) break;
                        if (window.appChatAvatar && typeof window.appChatAvatar.getCachedPreview === 'function') {
                            const cached = window.appChatAvatar.getCachedPreview();
                            if (cached && cached.dataUrl) {
                                I.postYuiGuideMessageToChat('avatar_updated', {
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
                        I._isRelayingYuiGuideHandoffSent = true;
                        try {
                            window.dispatchEvent(new CustomEvent('neko:yui-guide:handoff-sent', {
                                detail: event.data.detail || {}
                            }));
                        } finally {
                            I._isRelayingYuiGuideHandoffSent = false;
                        }
                        break;
                    }
                    case 'yui_guide_set_chat_buttons_disabled': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideChatLockState(event.data.disabled !== false);
                        break;
                    }
                    case 'yui_guide_set_chat_input_locked': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideChatInputLocked(event.data.locked === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_set_compact_chat_fixed_layout': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideCompactChatFixedLayout(event.data.fixed === true);
                        break;
                    }
                    case 'yui_guide_set_chat_cursor':
                    case 'yui_guide_drag_chat_cursor':
                    case 'yui_guide_arc_chat_cursor': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        var cursorRunId = I.getYuiGuidePcOverlayRunIdFromMessage(event.data);
                        relayYuiGuideChatCommand(Object.assign({}, event.data, {
                            pcOverlayRunId: cursorRunId
                        }));
                        break;
                    }
                    case 'yui_guide_set_compact_history_open': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideCompactHistoryOpen(event.data.open === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_set_chat_spotlight': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        var preserveSpotlightDuringResistance = event.data.preserveDuringResistance === true;
                        var spotlightRunId = I.getYuiGuidePcOverlayRunIdFromMessage(event.data);
                        I.applyYuiGuideChatSpotlight(event.data.kind || '', {
                            variant: typeof event.data.variant === 'string' ? event.data.variant : '',
                            preserveDuringResistance: preserveSpotlightDuringResistance,
                            pcOverlayRunId: spotlightRunId
                        });
                        I.scheduleYuiGuideChatInputSpotlightRetry(event.data.kind || '', spotlightRunId);
                        break;
                    }
                    case 'yui_guide_set_avatar_tool_menu_open': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideAvatarToolMenuOpen(event.data.open === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_set_compact_tool_fan_open': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideCompactToolFanOpen(event.data.open === true, event.data.reason || '');
                        break;
                    }
                    case 'yui_guide_rotate_compact_tool_wheel': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideCompactToolWheelRotate(event.data);
                        break;
                    }
                    case 'yui_guide_set_compact_tool_wheel_index': {
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.applyYuiGuideCompactToolWheelIndex(event.data);
                        break;
                    }
                    case 'yui_guide_chat_ready': {
                        if (I.isStandaloneChatPage()) break;
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
                        if (!I.isStandaloneChatPage() || !document.body) break;
                        I.clearYuiGuidePcOverlayBridgeState(event.data.reason || '', event.data.tutorialRunId || '');
                        break;
                    }
                    case 'request_avatar_capture': {
                        if (I.isStandaloneChatPage()) break;
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
	                                I.postYuiGuideMessageToChat('avatar_capture_result', {
	                                    requestId: captureRequestId,
	                                    dataUrl: result.dataUrl || '',
	                                    modelType: result.modelType || '',
	                                    sourceDataUrl: includeSource ? (result.sourceDataUrl || '') : '',
	                                    cropRectPixels: result.cropRectPixels || null
	                                });
	                            }).catch(function (err) {
	                                console.error('[BroadcastChannel] avatar capture failed:', err);
	                                I.postYuiGuideMessageToChat('avatar_capture_result', {
	                                    requestId: captureRequestId,
	                                    error: true
	                                });
	                            });
	                        } else {
	                            I.postYuiGuideMessageToChat('avatar_capture_result', {
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
    I.drainPendingYuiGuideChatBridgeQueue();

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

    I.applyYuiGuideChatLockState = function applyYuiGuideChatLockState(disabled) {
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

    I.ensureYuiGuideExternalChatExpanded = function ensureYuiGuideExternalChatExpanded() {
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

    I.applyYuiGuideChatInputLocked = function applyYuiGuideChatInputLocked(locked, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setHomeTutorialInputLocked === 'function') {
            host.setHomeTutorialInputLocked(locked === true, reason || 'externalized-chat-guide');
        }
    }

    I.applyYuiGuideCompactChatFixedLayout = function applyYuiGuideCompactChatFixedLayout(fixed) {
        if (!document.body) {
            return;
        }
        document.body.classList.toggle('yui-guide-compact-chat-fixed', fixed === true);
    }

    I.applyYuiGuideCompactHistoryOpen = function applyYuiGuideCompactHistoryOpen(open, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setCompactHistoryOpen === 'function') {
            host.setCompactHistoryOpen(open === true, reason || 'external-yui-guide');
        }
    }

    I.applyYuiGuideAvatarToolMenuOpen = function applyYuiGuideAvatarToolMenuOpen(open, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setAvatarToolMenuOpen === 'function') {
            host.setAvatarToolMenuOpen(open === true, reason || 'externalized-chat-guide');
        }
    }

    I.applyYuiGuideCompactToolFanOpen = function applyYuiGuideCompactToolFanOpen(open, reason) {
        var host = getReactChatWindowHost();
        if (host && typeof host.setCompactToolFanOpen === 'function') {
            host.setCompactToolFanOpen(open === true, reason || 'externalized-chat-guide');
        }
    }

    I.applyYuiGuideCompactToolWheelRotate = function applyYuiGuideCompactToolWheelRotate(payload) {
        var host = getReactChatWindowHost();
        if (!host || typeof host.rotateCompactToolWheel !== 'function') return;
        host.rotateCompactToolWheel(payload && payload.direction, payload && payload.stepCount, {
            reason: payload && payload.reason,
            forceFast: !payload || payload.forceFast !== false
        });
    }

    I.applyYuiGuideCompactToolWheelIndex = function applyYuiGuideCompactToolWheelIndex(payload) {
        var host = getReactChatWindowHost();
        if (!host || typeof host.setCompactToolWheelIndex !== 'function') return;
        host.setCompactToolWheelIndex(payload && payload.index, payload && payload.reason);
    }

    I.dispatchCrossWindowIdleActivity = function dispatchCrossWindowIdleActivity(detail) {
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

    function dispatchIdleCat1PlaygroundYarnRequest(detail) {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-playground-yarn-request', {
            detail: Object.assign({
                action: 'idle_cat1_playground_yarn_request',
                reason: 'cat1-playground-entry',
                source: '',
                trigger: 'cat1-question-mark',
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
        if (!I.isStandaloneChatPage()) return;

        var now = Date.now();
        if (now - I._lastCrossWindowIdleActivityAt < I.CROSS_WINDOW_IDLE_ACTIVITY_MIN_INTERVAL_MS) {
            return;
        }
        I._lastCrossWindowIdleActivityAt = now;

        var payload = {
            action: 'idle_activity',
            source: source || 'interaction',
            kind: kind === 'conversation' ? 'conversation' : 'interaction',
            lanlan_name: I.getCurrentLanlanName(),
            timestamp: now
        };

        I.postInterpageMessage(payload, { openerFallback: true });
    }

    function bindStandaloneChatIdleActivityRelay() {
        if (!I.isStandaloneChatPage()) return;

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

    Object.assign(window.appInterpage, I.mod || {});
})();
