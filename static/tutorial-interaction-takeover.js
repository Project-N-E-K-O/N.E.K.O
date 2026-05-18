(function () {
    'use strict';

    let lastTouchTime = 0;

    function noop() {}

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
            this.allowTarget = normalizedOptions.allowTarget || null;
            this.isSystemDialogTarget = normalizedOptions.isSystemDialogTarget || null;
            this.allowTouchPassthrough = normalizedOptions.allowTouchPassthrough || null;
            this.allowWindowPassthrough = normalizedOptions.allowWindowPassthrough === true;
            this.isDestroyed = normalizedOptions.isDestroyed || null;
            this.externalChatChannelProvider = normalizedOptions.externalChatChannelProvider || null;
            this.externalizedChatDetector = normalizedOptions.externalizedChatDetector || null;
            this.destroyed = false;
            this.active = false;
            this.externalizedChatSpotlightKind = '';
            this.tutorialFaceForwardLockSnapshot = null;

            this.interactionGuardHandler = this.onInteractionGuard.bind(this);

            this.document.addEventListener('pointerdown', this.interactionGuardHandler, true);
            this.document.addEventListener('pointerup', this.interactionGuardHandler, true);
            this.document.addEventListener('mousedown', this.interactionGuardHandler, true);
            this.document.addEventListener('mouseup', this.interactionGuardHandler, true);
            this.document.addEventListener('touchstart', this.interactionGuardHandler, true);
            this.document.addEventListener('touchend', this.interactionGuardHandler, true);
            this.document.addEventListener('click', this.interactionGuardHandler, true);
            this.document.addEventListener('dblclick', this.interactionGuardHandler, true);
            this.document.addEventListener('contextmenu', this.interactionGuardHandler, true);
        }

        setActive(active) {
            const nextActive = active === true;
            if (this.destroyed && nextActive) {
                return;
            }
            this.active = nextActive;
            if (this.overlay && typeof this.overlay.setInteractionShieldSuppressed === 'function') {
                this.overlay.setInteractionShieldSuppressed(this.active && this.allowWindowPassthrough);
            }
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

        getExternalChatChannel() {
            if (typeof this.externalChatChannelProvider === 'function') {
                return this.externalChatChannelProvider() || null;
            }
            return this.window.appInterpage && this.window.appInterpage.nekoBroadcastChannel
                ? this.window.appInterpage.nekoBroadcastChannel
                : null;
        }

        setExternalizedChatButtonsDisabled(disabled) {
            if (!this.isHomeChatExternalized()) {
                return;
            }

            const channel = this.getExternalChatChannel();
            if (!channel || typeof channel.postMessage !== 'function') {
                return;
            }

            try {
                channel.postMessage({
                    action: 'yui_guide_set_chat_buttons_disabled',
                    disabled: disabled !== false,
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('[TutorialInteractionTakeover] 同步独立聊天窗按钮禁用状态失败:', error);
            }
        }

        setExternalizedChatSpotlight(kind) {
            if (!this.isHomeChatExternalized()) {
                return;
            }

            this.externalizedChatSpotlightKind = typeof kind === 'string' ? kind : '';
            const channel = this.getExternalChatChannel();
            if (!channel || typeof channel.postMessage !== 'function') {
                return;
            }

            try {
                channel.postMessage({
                    action: 'yui_guide_set_chat_spotlight',
                    kind: this.externalizedChatSpotlightKind,
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('[TutorialInteractionTakeover] 同步独立聊天窗高亮失败:', error);
            }
        }

        clearExternalizedChatFx() {
            this.externalizedChatSpotlightKind = '';
            this.setExternalizedChatSpotlight('');
        }

        onExternalChatReady() {
            if (this.destroyed || !this.isHomeChatExternalized()) {
                return;
            }

            this.setExternalizedChatButtonsDisabled(true);
            if (this.externalizedChatSpotlightKind) {
                this.setExternalizedChatSpotlight(this.externalizedChatSpotlightKind);
            }
        }

        isTouchInteractionEvent(event) {
            if (!event || typeof event.type !== 'string') {
                return false;
            }

            if (event.type.indexOf('touch') === 0) {
                lastTouchTime = Date.now();
                return true;
            }

            if (event.pointerType === 'touch') {
                lastTouchTime = Date.now();
                return true;
            }

            if (/^(click|mousedown|mouseup)$/.test(event.type) && Date.now() - lastTouchTime < 500) {
                return true;
            }

            return false;
        }

        onInteractionGuard(event) {
            if (this.destroyed || !this.active || this.page !== 'home' || !event || event.isTrusted === false) {
                return;
            }
            if (safeInvoke(this.isDestroyed, [], false) === true) {
                return;
            }

            const target = event.target || null;
            const isAllowedTarget = safeInvoke(this.allowTarget, [target, event, this], false) === true;
            if (isAllowedTarget || safeInvoke(this.isSystemDialogTarget, [target, event, this], false) === true) {
                return;
            }

            if (
                this.isTouchInteractionEvent(event)
                && safeInvoke(this.allowTouchPassthrough, [event, this], false) === true
            ) {
                return;
            }

            if (typeof event.preventDefault === 'function') {
                event.preventDefault();
            }
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            if (typeof event.stopPropagation === 'function') {
                event.stopPropagation();
            }
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.setActive(false);
            this.clearExternalizedChatFx();
            this.setExternalizedChatButtonsDisabled(false);
            this.releaseFaceForwardLock();
            this.destroyed = true;

            this.document.removeEventListener('pointerdown', this.interactionGuardHandler, true);
            this.document.removeEventListener('pointerup', this.interactionGuardHandler, true);
            this.document.removeEventListener('mousedown', this.interactionGuardHandler, true);
            this.document.removeEventListener('mouseup', this.interactionGuardHandler, true);
            this.document.removeEventListener('touchstart', this.interactionGuardHandler, true);
            this.document.removeEventListener('touchend', this.interactionGuardHandler, true);
            this.document.removeEventListener('click', this.interactionGuardHandler, true);
            this.document.removeEventListener('dblclick', this.interactionGuardHandler, true);
            this.document.removeEventListener('contextmenu', this.interactionGuardHandler, true);
        }
    }

    window.TutorialInteractionTakeover = {
        createController: function (options) {
            return new TutorialInteractionTakeoverController(options);
        }
    };
})();
