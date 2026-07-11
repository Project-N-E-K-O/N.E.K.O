(function () {
    'use strict';

    function safeInvoke(callback, args, fallbackValue) {
        if (typeof callback !== 'function') {
            return fallbackValue;
        }
        try {
            return callback.apply(null, args || []);
        } catch (error) {
            console.warn('[TutorialInteractionTakeover] callback failed:', error);
            return fallbackValue;
        }
    }

    class TutorialInteractionTakeoverController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.window = normalizedOptions.window || window;
            this.document = normalizedOptions.document || document;
            this.page = normalizedOptions.page || 'home';
            this.overlay = normalizedOptions.overlay || null;
            this.isDestroyed = normalizedOptions.isDestroyed || null;
            this.isResistancePaused = normalizedOptions.isResistancePaused || null;
            this.externalChatChannelProvider = normalizedOptions.externalChatChannelProvider || null;
            this.externalizedChatDetector = normalizedOptions.externalizedChatDetector || null;
            this.destroyed = false;
            this.active = false;
            this.externalizedChatSpotlightKind = '';
            this.externalizedChatSpotlightVariant = '';
            this.tutorialFaceForwardLockSnapshot = null;
            this.externalChatCommandBus = this.createExternalChatCommandBus();
        }

        setActive(active) {
            const nextActive = active === true;
            if (this.destroyed && nextActive) {
                return;
            }
            this.active = nextActive;
            if (this.overlay && typeof this.overlay.setTakingOver === 'function') {
                this.overlay.setTakingOver(this.active);
            }
        }

        enableFaceForwardLock() {
            if (this.tutorialFaceForwardLockSnapshot) {
                this.applyFaceForwardLock();
                return;
            }

            const live2dManager = this.window.live2dManager || null;
            const vrmManager = this.window.vrmManager || null;
            const pngtuberManager = this.window.pngtuberManager || null;
            const mmdManager = this.window.mmdManager || null;
            this.tutorialFaceForwardLockSnapshot = {
                hadWindowMouseTrackingEnabled: typeof this.window.mouseTrackingEnabled !== 'undefined',
                windowMouseTrackingEnabled: this.window.mouseTrackingEnabled,
                live2dMouseTrackingEnabled: live2dManager && typeof live2dManager.isMouseTrackingEnabled === 'function'
                    ? live2dManager.isMouseTrackingEnabled()
                    : null,
                vrmMouseTrackingEnabled: vrmManager && typeof vrmManager.isMouseTrackingEnabled === 'function'
                    ? vrmManager.isMouseTrackingEnabled()
                    : null,
                pngtuberMouseTrackingEnabled: pngtuberManager && typeof pngtuberManager.isMouseTrackingEnabled === 'function'
                    ? pngtuberManager.isMouseTrackingEnabled()
                    : null,
                mmdCursorFollowEnabled: mmdManager && mmdManager.cursorFollow
                    ? mmdManager.cursorFollow.enabled !== false
                    : null
            };
            this.window.nekoYuiGuideFaceForwardLock = true;
            this.window.mouseTrackingEnabled = false;
            this.applyFaceForwardLock();
        }

        applyFaceForwardLock() {
            this.window.nekoYuiGuideFaceForwardLock = true;
            this.window.nekoYuiGuideFaceForwardSuppressParamWrite = true;
            this.window.mouseTrackingEnabled = false;

            const live2dManager = this.window.live2dManager || null;
            if (live2dManager && typeof live2dManager.setMouseTrackingEnabled === 'function') {
                try {
                    live2dManager.setMouseTrackingEnabled(false);
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 锁定 Live2D 正脸失败:', error);
                }
            }

            const vrmManager = this.window.vrmManager || null;
            if (vrmManager && typeof vrmManager.setMouseTrackingEnabled === 'function') {
                try {
                    vrmManager.setMouseTrackingEnabled(false);
                    if (vrmManager._cursorFollow && typeof vrmManager._cursorFollow._completeDisable === 'function') {
                        vrmManager._cursorFollow._completeDisable();
                    }
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 锁定 VRM 正脸失败:', error);
                }
            }

            const pngtuberManager = this.window.pngtuberManager || null;
            if (pngtuberManager && typeof pngtuberManager.setMouseTrackingEnabled === 'function') {
                try {
                    pngtuberManager.setMouseTrackingEnabled(false);
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 锁定 PNGTuber 正脸失败:', error);
                }
            }

            const mmdCursorFollow = this.window.mmdManager && this.window.mmdManager.cursorFollow
                ? this.window.mmdManager.cursorFollow
                : null;
            if (mmdCursorFollow && typeof mmdCursorFollow.setEnabled === 'function') {
                try {
                    mmdCursorFollow.setEnabled(false);
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 锁定 MMD 正脸失败:', error);
                }
            }
        }

        releaseFaceForwardLock() {
            const snapshot = this.tutorialFaceForwardLockSnapshot;
            if (!snapshot) {
                return;
            }

            this.tutorialFaceForwardLockSnapshot = null;
            this.window.nekoYuiGuideFaceForwardLock = false;
            this.window.nekoYuiGuideFaceForwardSuppressParamWrite = false;
            if (snapshot.hadWindowMouseTrackingEnabled) {
                this.window.mouseTrackingEnabled = snapshot.windowMouseTrackingEnabled;
            } else {
                try {
                    delete this.window.mouseTrackingEnabled;
                } catch (_) {
                    this.window.mouseTrackingEnabled = undefined;
                }
            }
            const restoredMouseTrackingEnabled = this.window.mouseTrackingEnabled !== false;

            const live2dManager = this.window.live2dManager || null;
            if (live2dManager && typeof live2dManager.setMouseTrackingEnabled === 'function') {
                try {
                    live2dManager.setMouseTrackingEnabled(
                        snapshot.live2dMouseTrackingEnabled !== null
                            ? snapshot.live2dMouseTrackingEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 恢复 Live2D 鼠标跟踪失败:', error);
                }
            }

            const vrmManager = this.window.vrmManager || null;
            if (vrmManager && typeof vrmManager.setMouseTrackingEnabled === 'function') {
                try {
                    vrmManager.setMouseTrackingEnabled(
                        snapshot.vrmMouseTrackingEnabled !== null
                            ? snapshot.vrmMouseTrackingEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 恢复 VRM 鼠标跟踪失败:', error);
                }
            }

            const pngtuberManager = this.window.pngtuberManager || null;
            if (pngtuberManager && typeof pngtuberManager.setMouseTrackingEnabled === 'function') {
                try {
                    pngtuberManager.setMouseTrackingEnabled(
                        snapshot.pngtuberMouseTrackingEnabled !== null
                            ? snapshot.pngtuberMouseTrackingEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 恢复 PNGTuber 鼠标追踪失败:', error);
                }
            }

            const mmdCursorFollow = this.window.mmdManager && this.window.mmdManager.cursorFollow
                ? this.window.mmdManager.cursorFollow
                : null;
            if (mmdCursorFollow && typeof mmdCursorFollow.setEnabled === 'function') {
                try {
                    mmdCursorFollow.setEnabled(
                        snapshot.mmdCursorFollowEnabled !== null
                            ? snapshot.mmdCursorFollowEnabled
                            : restoredMouseTrackingEnabled
                    );
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 恢复 MMD 鼠标跟踪失败:', error);
                }
            }
        }

        isHomeChatExternalized() {
            if (this.page !== 'home') {
                return false;
            }

            if (typeof this.externalizedChatDetector === 'function') {
                try {
                    return this.externalizedChatDetector() === true;
                } catch (error) {
                    console.warn('[TutorialInteractionTakeover] 检查外置聊天窗状态失败:', error);
                    return false;
                }
            }

            const overlay = this.document.getElementById('react-chat-window-overlay');
            return !!(overlay && overlay.style.display === 'none');
        }

        createExternalChatCommandBus() {
            const common = this.window.YuiGuideCommon || null;
            if (common && typeof common.createTutorialBridgeCommandBus === 'function') {
                return common.createTutorialBridgeCommandBus({
                    window: this.window,
                    channelProvider: () => this.getExternalChatBroadcastChannel(),
                    nativeRelayProvider: () => this.window.nekoTutorialOverlay || null
                });
            }
            return null;
        }

        getExternalChatBroadcastChannel() {
            const broadcastChannel = this.window.appInterpage && this.window.appInterpage.nekoBroadcastChannel
                ? this.window.appInterpage.nekoBroadcastChannel
                : null;
            if (broadcastChannel) {
                return broadcastChannel;
            }

            if (typeof this.externalChatChannelProvider === 'function') {
                return this.externalChatChannelProvider() || null;
            }
            return null;
        }

        getExternalChatChannel() {
            const getTutorialRunId = () => {
                try {
                    return this.window.localStorage.getItem('yuiGuidePcOverlayRunId') || '';
                } catch (_) {
                    return '';
                }
            };
            const broadcastChannel = this.window.appInterpage && this.window.appInterpage.nekoBroadcastChannel
                ? this.window.appInterpage.nekoBroadcastChannel
                : null;
            const nativeRelay = this.window.nekoTutorialOverlay
                && typeof this.window.nekoTutorialOverlay.relayToChat === 'function'
                ? this.window.nekoTutorialOverlay
                : null;
            if (broadcastChannel || nativeRelay) {
                return {
                    postMessage(message) {
                        let delivered = false;
                        const outgoingMessage = Object.assign({}, message || {});
                        const tutorialRunId = getTutorialRunId();
                        if (tutorialRunId && !outgoingMessage.tutorialRunId) {
                            outgoingMessage.tutorialRunId = tutorialRunId;
                        }
                        if (broadcastChannel && typeof broadcastChannel.postMessage === 'function') {
                            try {
                                broadcastChannel.postMessage(outgoingMessage);
                                delivered = true;
                            } catch (error) {
                                console.warn('[TutorialInteractionTakeover] BroadcastChannel delivery failed:', error);
                            }
                        }
                        if (nativeRelay) {
                            try {
                                nativeRelay.relayToChat(outgoingMessage);
                                delivered = true;
                            } catch (error) {
                                console.warn('[TutorialInteractionTakeover] native relay delivery failed:', error);
                            }
                        }
                        return delivered;
                    }
                };
            }

            if (typeof this.externalChatChannelProvider === 'function') {
                return this.externalChatChannelProvider() || null;
            }
            return null;
        }

        resolveLanlanName() {
            const appStateName = this.window && this.window.appState && this.window.appState.lanlan_name;
            if (typeof appStateName === 'string' && appStateName) {
                return appStateName;
            }
            const configName = this.window && this.window.lanlan_config && this.window.lanlan_config.lanlan_name;
            return typeof configName === 'string' ? configName : '';
        }

        getExternalizedChatTutorialRunId() {
            try {
                const storage = this.window && this.window.localStorage;
                return storage ? String(storage.getItem('yuiGuidePcOverlayRunId') || '') : '';
            } catch (_) {
                return '';
            }
        }

        postExternalChatCommand(action, payload, options) {
            if (!this.isHomeChatExternalized()) {
                return false;
            }

            const normalizedAction = typeof action === 'string' ? action : '';
            if (!normalizedAction) {
                return false;
            }

            const normalizedOptions = options || {};
            const message = Object.assign({
                action: normalizedAction
            }, payload || {});
            if (!Number.isFinite(message.timestamp)) {
                message.timestamp = Date.now();
            }
            if (!message.lanlan_name) {
                const lanlanName = this.resolveLanlanName();
                message.lanlan_name = lanlanName;
            }
            const tutorialRunId = this.getExternalizedChatTutorialRunId();
            if (tutorialRunId && !message.tutorialRunId) {
                message.tutorialRunId = tutorialRunId;
            }
            if (tutorialRunId && !message.pcOverlayRunId) {
                message.pcOverlayRunId = tutorialRunId;
            }

            if (this.externalChatCommandBus && typeof this.externalChatCommandBus.post === 'function') {
                return this.externalChatCommandBus.post(message, normalizedOptions);
            }

            const channel = this.getExternalChatChannel();
            if (!channel || typeof channel.postMessage !== 'function') {
                return false;
            }

            try {
                return channel.postMessage(message) !== false;
            } catch (error) {
                console.warn('[TutorialInteractionTakeover] 同步独立聊天窗命令失败:', normalizedAction, error);
                return false;
            }
        }

        setExternalizedChatButtonsDisabled(disabled) {
            this.postExternalChatCommand('yui_guide_set_chat_buttons_disabled', {
                disabled: disabled !== false
            });
        }

        setExternalizedChatInputLocked(locked, reason) {
            this.postExternalChatCommand('yui_guide_set_chat_input_locked', {
                locked: locked === true,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        setExternalizedChatSpotlight(kind) {
            const options = arguments.length > 1 && arguments[1] && typeof arguments[1] === 'object'
                ? arguments[1]
                : null;
            const previousKind = this.externalizedChatSpotlightKind;
            const previousVariant = this.externalizedChatSpotlightVariant;
            const normalizedKind = typeof kind === 'string' ? kind : '';
            const hasVariantOption = options && Object.prototype.hasOwnProperty.call(options, 'variant');
            const normalizedVariant = hasVariantOption && typeof options.variant === 'string'
                ? options.variant.trim()
                : (normalizedKind && normalizedKind === previousKind ? previousVariant : '');
            this.externalizedChatSpotlightKind = normalizedKind;
            this.externalizedChatSpotlightVariant = this.externalizedChatSpotlightKind ? normalizedVariant : '';
            const message = {
                kind: this.externalizedChatSpotlightKind,
                variant: this.externalizedChatSpotlightVariant
            };
            if (
                (this.externalizedChatSpotlightKind || previousKind || previousVariant)
                && safeInvoke(this.isResistancePaused, [], false) === true
            ) {
                message.preserveDuringResistance = true;
            }
            this.postExternalChatCommand('yui_guide_set_chat_spotlight', message);
        }

        preserveExternalizedChatSpotlightDuringResistance() {
            if (!this.externalizedChatSpotlightKind) {
                return false;
            }
            return this.postExternalChatCommand('yui_guide_set_chat_spotlight', {
                kind: this.externalizedChatSpotlightKind,
                variant: this.externalizedChatSpotlightVariant,
                preserveDuringResistance: true
            });
        }

        setExternalizedChatCursor(kind, options) {
            const message = {
                kind: typeof kind === 'string' ? kind : '',
                effect: options && typeof options.effect === 'string' ? options.effect : '',
                effectDurationMs: options && Number.isFinite(options.effectDurationMs)
                    ? Math.max(0, Math.floor(options.effectDurationMs))
                    : 0,
                targetIndex: options && Number.isFinite(options.targetIndex)
                    ? Math.max(0, Math.floor(options.targetIndex))
                    : 0,
                freezePoint: !!(options && options.freezePoint === true),
                preservePcOverlayCursor: !!(options && options.preservePcOverlayCursor === true)
            };
            if (options && Number.isFinite(options.durationMs)) {
                message.durationMs = Math.max(0, Math.floor(options.durationMs));
            }
            this.postExternalChatCommand('yui_guide_set_chat_cursor', message);
        }

        setExternalizedChatAvatarToolMenuOpen(open, reason) {
            this.postExternalChatCommand('yui_guide_set_avatar_tool_menu_open', {
                open: open === true,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        clickExternalizedChatAvatarToolButton(reason) {
            this.postExternalChatCommand('yui_guide_click_avatar_tool_button', {
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        setExternalizedChatCompactHistoryOpen(open, reason) {
            this.postExternalChatCommand('yui_guide_set_compact_history_open', {
                open: open === true,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        setExternalizedChatCompactToolFanOpen(open, reason) {
            this.postExternalChatCommand('yui_guide_set_compact_tool_fan_open', {
                open: open === true,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        rotateExternalizedChatCompactToolWheel(direction, stepCount, reason) {
            this.postExternalChatCommand('yui_guide_rotate_compact_tool_wheel', {
                direction: Number(direction) < 0 ? -1 : 1,
                stepCount: Number.isFinite(Number(stepCount)) ? Math.max(1, Math.min(7, Math.floor(Number(stepCount)))) : 1,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        setExternalizedChatCompactToolWheelIndex(index, reason) {
            this.postExternalChatCommand('yui_guide_set_compact_tool_wheel_index', {
                index: Number.isFinite(Number(index)) ? Math.max(0, Math.min(6, Math.floor(Number(index)))) : 0,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        dragExternalizedChatCursor(kind, options) {
            const normalizedOptions = options || {};
            const message = {
                kind: typeof kind === 'string' ? kind : '',
                deltaX: Number.isFinite(Number(normalizedOptions.deltaX)) ? Number(normalizedOptions.deltaX) : 0,
                deltaY: Number.isFinite(Number(normalizedOptions.deltaY)) ? Number(normalizedOptions.deltaY) : 0,
                effect: typeof normalizedOptions.effect === 'string' ? normalizedOptions.effect : '',
                effectDurationMs: Number.isFinite(Number(normalizedOptions.effectDurationMs)) ? Math.max(0, Math.floor(Number(normalizedOptions.effectDurationMs))) : 0,
                targetIndex: Number.isFinite(Number(normalizedOptions.targetIndex)) ? Math.max(0, Math.floor(Number(normalizedOptions.targetIndex))) : 0
            };
            if (
                Object.prototype.hasOwnProperty.call(normalizedOptions, 'durationMs')
                && Number.isFinite(Number(normalizedOptions.durationMs))
            ) {
                message.durationMs = Math.max(0, Math.floor(Number(normalizedOptions.durationMs)));
            }
            this.postExternalChatCommand('yui_guide_drag_chat_cursor', message);
        }

        arcExternalizedChatCursor(kind, options) {
            const normalizedOptions = options || {};
            const message = {
                kind: typeof kind === 'string' ? kind : '',
                direction: Number(normalizedOptions.direction) < 0 ? -1 : 1,
                fraction: Number.isFinite(Number(normalizedOptions.fraction))
                    ? Math.max(0, Math.min(1, Number(normalizedOptions.fraction)))
                    : 0.2,
                durationMs: Number.isFinite(Number(normalizedOptions.durationMs))
                    ? Math.max(0, Math.floor(Number(normalizedOptions.durationMs)))
                    : 260,
                effect: typeof normalizedOptions.effect === 'string' ? normalizedOptions.effect : '',
                effectDurationMs: Number.isFinite(Number(normalizedOptions.effectDurationMs))
                    ? Math.max(0, Math.floor(Number(normalizedOptions.effectDurationMs)))
                    : 0,
                targetIndex: Number.isFinite(Number(normalizedOptions.targetIndex))
                    ? Math.max(0, Math.floor(Number(normalizedOptions.targetIndex)))
                    : 0
            };
            this.postExternalChatCommand('yui_guide_arc_chat_cursor', message);
        }

        setExternalizedChatCompactFixedLayout(fixed, reason) {
            this.postExternalChatCommand('yui_guide_set_compact_chat_fixed_layout', {
                fixed: fixed === true,
                reason: typeof reason === 'string' ? reason : ''
            });
        }

        clearExternalizedChatGuideMessages() {
            this.postExternalChatCommand('yui_guide_clear_chat_messages');
        }

        clearExternalizedChatFx() {
            this.externalizedChatSpotlightKind = '';
            this.externalizedChatSpotlightVariant = '';
            this.setExternalizedChatInputLocked(false, 'clear-externalized-chat-fx');
            this.setExternalizedChatSpotlight('');
            this.setExternalizedChatCursor('');
            this.setExternalizedChatAvatarToolMenuOpen(false, 'clear-externalized-chat-fx');
            this.setExternalizedChatCompactHistoryOpen(false, 'clear-externalized-chat-fx');
            this.setExternalizedChatCompactToolFanOpen(false, 'clear-externalized-chat-fx');
        }

        onExternalChatReady() {
            if (this.destroyed || !this.isHomeChatExternalized()) {
                return;
            }

            this.setExternalizedChatButtonsDisabled(true);
            this.setExternalizedChatInputLocked(true, 'external-chat-ready');
            if (
                this.document.body
                && this.document.body.classList.contains('yui-guide-compact-chat-fixed')
            ) {
                this.setExternalizedChatCompactFixedLayout(true, 'external-chat-ready');
            }
            if (this.externalizedChatSpotlightKind) {
                this.setExternalizedChatSpotlight(this.externalizedChatSpotlightKind);
            }
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.setActive(false);
            this.clearExternalizedChatFx();
            this.setExternalizedChatButtonsDisabled(false);
            if (this.externalChatCommandBus && typeof this.externalChatCommandBus.destroy === 'function') {
                this.externalChatCommandBus.destroy();
            }
            this.releaseFaceForwardLock();
            this.destroyed = true;
        }
    }

    window.TutorialInteractionTakeover = {
        createController: function (options) {
            return new TutorialInteractionTakeoverController(options);
        }
    };
})();
