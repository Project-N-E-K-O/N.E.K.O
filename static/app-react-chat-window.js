/**
 * app-react-chat-window.js
 * Host-side controller for the exported React chat window.
 * - Dynamically loads the React bundle if needed
 * - Owns window open/close/minimize/drag state
 * - Owns chat view props + messages state
 * - Exposes a stable bridge for host code / IPC adapters
 */
(function () {
    'use strict';

    var BUNDLE_SRC = '/static/react/neko-chat/neko-chat-window.iife.js';
    var STORAGE_LEFT_KEY = 'neko.reactChatWindow.left';
    var STORAGE_TOP_KEY = 'neko.reactChatWindow.top';
    var STORAGE_WIDTH_KEY = 'neko.reactChatWindow.width';
    var STORAGE_HEIGHT_KEY = 'neko.reactChatWindow.height';
    var EVENT_PREFIX = 'react-chat-window:';

    var loadedPromise = null;
    var mounted = false;
    var dragState = null;
    var resizeState = null;
    var minimized = false;
    var savedShellSize = null;
    var savedShellPosition = null; // {left, top} before minimize – used to fly back on expand
    var _sortKeySeq = 0; // monotonically increasing sortKey counter

    var state = {
        viewProps: null,
        messages: [],
        composerAttachments: [],
        composerHidden: false,
        onMessageAction: null,
        onComposerImportImage: null,
        onComposerScreenshot: null,
        onComposerRemoveAttachment: null,
        onComposerSubmit: null,
        onAvatarInteraction: null,
        onAvatarToolStateChange: null,
        pendingRollbackDrafts: Object.create(null),
        rollbackDraft: ''
    };

    var MOBILE_MAX_HEIGHT_RATIO = 0.85;
    var MOBILE_MESSAGE_MIN_HEIGHT = 60;
    var DESKTOP_DEFAULT_LEFT_MARGIN = 24;
    var MOBILE_MIN_HEIGHT = 150;
    var MOBILE_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.mobileHeight';
    var mobileUserHeight = 0; // 用户手动设置的手机端高度（0 = 自动）
    var mobileLayoutFrame = 0;

    function getMobileMaxHeight() {
        return Math.max(MOBILE_MIN_HEIGHT, Math.floor(window.innerHeight * MOBILE_MAX_HEIGHT_RATIO));
    }

    function $(id) {
        return document.getElementById(id);
    }

    function isElectronChatWindow() {
        // chat.html 用 <body class="electron-chat-window">；Electron 独立聊天窗口。
        // 本 PR 的所有移动端改动都必须对它无感，作为显式隔离在全局 touch 处理里用来短路。
        return !!(document.body && document.body.classList.contains('electron-chat-window'));
    }

    function isMobileWidth() {
        // chat.html 是 Electron 独立窗口，始终按 PC 行为处理（即使用户把窗口拖窄到 <768px），
        // 通过 <body class="electron-chat-window"> 从"手机端布局"中排除。
        if (isElectronChatWindow()) {
            return false;
        }
        return window.innerWidth <= 768;
    }

    function getOverlay() {
        return $('react-chat-window-overlay');
    }

    function getShell() {
        return $('react-chat-window-shell');
    }

    function getHeader() {
        return $('react-chat-window-drag-handle');
    }

    function isYuiGuideDragLocked() {
        var body = document.body;
        if (!body) return false;
        return body.classList.contains('yui-guide-home-driver-hidden')
            || body.classList.contains('yui-taking-over');
    }

    function getMinimizeButton() {
        return $('reactChatWindowMinimizeButton');
    }

    function getMinimizeIcon() {
        return $('reactChatWindowMinimizeIcon');
    }

    function getRoot() {
        return $('react-chat-window-root');
    }

    function clearMobileContentCap() {
        var shell = getShell();
        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        if (shell.dataset.mobileAutoHeight !== undefined) {
            shell.style.removeProperty('height');
            delete shell.dataset.mobileAutoHeight;
        }
    }

    function resetMobileContentLayoutState(shell, topbar, composer, messageList) {
        [topbar, composer, messageList].forEach(function (element) {
            if (!element) return;
            element.style.removeProperty('height');
            if (element.dataset && element.dataset.mobileAutoHeight) {
                delete element.dataset.mobileAutoHeight;
            }
        });

        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        shell.style.removeProperty('height');
        if (shell.dataset.mobileAutoHeight) {
            delete shell.dataset.mobileAutoHeight;
        }
    }

    function syncMobileContentLayout() {
        var overlay = getOverlay();
        var shell = getShell();
        var root = getRoot();
        if (!overlay || overlay.hidden || !shell || !root || minimized || !isMobileWidth()) {
            clearMobileContentCap();
            return;
        }

        // 正在拖拽调整高度时不覆盖，等 stopResize() 结束后再同步
        if (resizeState) return;

        // 如果用户手动设置了高度，使用用户高度，不自动计算
        if (mobileUserHeight > 0) {
            var h = Math.min(mobileUserHeight, getMobileMaxHeight());
            shell.style.height = h + 'px';
            shell.dataset.mobileAutoHeight = 'false';
            shell.classList.remove('is-mobile-content-capped');
            return;
        }

        var topbar = root.querySelector('.window-topbar');
        var composer = root.querySelector('.composer-panel');
        var messageList = root.querySelector('.message-list');
        if (!topbar || !composer || !messageList) {
            resetMobileContentLayoutState(shell, topbar, composer, messageList);
            return;
        }

        var maxHeight = getMobileMaxHeight();
        if (!maxHeight) return;

        var desiredMessageHeight = Math.max(MOBILE_MESSAGE_MIN_HEIGHT, messageList.scrollHeight);
        var desiredHeight = Math.ceil(
            topbar.getBoundingClientRect().height
            + composer.getBoundingClientRect().height
            + desiredMessageHeight
        );
        var nextHeight = Math.min(maxHeight, desiredHeight);

        shell.style.height = nextHeight + 'px';
        shell.dataset.mobileAutoHeight = 'true';
        shell.classList.toggle('is-mobile-content-capped', desiredHeight > maxHeight);
    }

    function scheduleMobileContentLayout() {
        if (mobileLayoutFrame) return;

        mobileLayoutFrame = window.requestAnimationFrame(function () {
            mobileLayoutFrame = 0;
            syncMobileContentLayout();
        });
    }

    function getI18nText(key, fallback) {
        if (typeof window.safeT === 'function') {
            return window.safeT(key, fallback);
        }

        if (typeof window.t === 'function') {
            try {
                var translated = window.t(key, fallback);
                if (translated && translated !== key) {
                    return translated;
                }
            } catch (_) {}
        }

        return fallback;
    }

    function getTextContent(node) {
        return node && node.textContent ? node.textContent.trim() : '';
    }

    function sanitizeDisplayName(value) {
        if (value == null) return '';
        return String(value).trim();
    }

    function getCurrentAssistantName() {
        return sanitizeDisplayName(
            (window.lanlan_config && window.lanlan_config.lanlan_name)
            || window._currentCatgirl
            || window.currentCatgirl
        ) || 'Neko';
    }

    function getCurrentUserName() {
        var candidates = [
            window.master_display_name,
            window.lanlan_config && window.lanlan_config.master_display_name,
            window.master_nickname,
            window.lanlan_config && window.lanlan_config.master_nickname,
            window.master_name,
            window.lanlan_config && window.lanlan_config.master_name,
            window.currentUser && (window.currentUser.nickname || window.currentUser.display_name || window.currentUser.displayName || window.currentUser.username || window.currentUser.name),
            window.userProfile && (window.userProfile.nickname || window.userProfile.display_name || window.userProfile.displayName || window.userProfile.username || window.userProfile.name),
            window.appUser && (window.appUser.nickname || window.appUser.display_name || window.appUser.displayName || window.appUser.username || window.appUser.name),
            window.username,
            window.userName,
            window.displayName,
            window.nickname
        ];

        for (var i = 0; i < candidates.length; i += 1) {
            var resolved = sanitizeDisplayName(candidates[i]);
            if (resolved) return resolved;
        }

        try {
            var storageKeys = ['nickname', 'displayName', 'userName', 'username'];
            for (var j = 0; j < storageKeys.length; j += 1) {
                var stored = sanitizeDisplayName(localStorage.getItem(storageKeys[j]));
                if (stored) return stored;
            }
        } catch (_) {}

        return 'You';
    }

    function getDefaultAuthorByRole(role) {
        return role === 'user' ? getCurrentUserName() : getCurrentAssistantName();
    }

    function createBaseViewProps() {
        var titleNode = $('chat-title');
        var textSendButton = $('textSendButton');
        var sendButtonLabelNode = textSendButton ? textSendButton.querySelector('[data-i18n="chat.send"]') : null;
        var title = getTextContent(titleNode)
            || getI18nText('chat.title', '对话')
            || '对话';
        var inputPlaceholder = getI18nText('chat.textInputPlaceholderCompact', '')
            || getI18nText('chat.textInputPlaceholderShort', '')
            || getI18nText('chat.textInputPlaceholder', '')
            || '输入消息...';
        var sendButtonLabel = getTextContent(sendButtonLabelNode)
            || getI18nText('chat.send', '发送')
            || '发送';

        return {
            title: title,
            iconSrc: '/static/icons/chat_icon.png',
            inputPlaceholder: inputPlaceholder,
            sendButtonLabel: sendButtonLabel,
            emptyText: getI18nText('chat.emptyState', '聊天内容接入后会显示在这里。'),
            chatWindowAriaLabel: getI18nText('chat.reactWindowAriaLabel', 'Neko chat window'),
            messageListAriaLabel: getI18nText('chat.messageListAriaLabel', 'Chat messages'),
            composerToolsAriaLabel: getI18nText('chat.composerToolsAriaLabel', 'Composer tools'),
            composerAttachmentsAriaLabel: getI18nText('chat.pendingImagesAriaLabel', 'Pending attachments'),
            importImageButtonLabel: getI18nText('chat.importImage', '导入图片'),
            screenshotButtonLabel: isMobileWidth()
                ? getI18nText('chat.takePhoto', '拍照')
                : getI18nText('chat.screenshot', '截图'),
            importImageButtonAriaLabel: getI18nText('chat.importImageAriaLabel', '导入图片'),
            screenshotButtonAriaLabel: isMobileWidth()
                ? getI18nText('chat.takePhotoAriaLabel', '拍照')
                : getI18nText('chat.screenshotAriaLabel', '截图'),
            removeAttachmentButtonAriaLabel: getI18nText('chat.removePendingImage', '移除图片'),
            failedStatusLabel: getI18nText('chat.messageFailed', '发送失败'),
            inputHint: getI18nText('chat.reactWindowInputHint', 'Enter 发送，Shift + Enter 换行'),
            jukeboxButtonLabel: getI18nText('chat.jukeboxLabel', '点歌台'),
            jukeboxButtonAriaLabel: getI18nText('chat.jukebox', '点歌台'),
            avatarGeneratorButtonLabel: getI18nText('chat.avatarPreviewLabel', '头像'),
            avatarGeneratorButtonAriaLabel: getI18nText('chat.avatarPreview', '生成头像'),
            translateEnabled: (window.appState && typeof window.appState.subtitleEnabled !== 'undefined')
                ? !!window.appState.subtitleEnabled
                : localStorage.getItem('subtitleEnabled') === 'true',
            translateButtonLabel: getI18nText('subtitle.enable', '字幕翻译'),
            translateButtonAriaLabel: getI18nText('subtitle.enableAriaLabel', '字幕翻译开关')
        };
    }

    function ensureViewProps() {
        if (!state.viewProps) {
            state.viewProps = createBaseViewProps();
        }
        return state.viewProps;
    }

    function cloneMessage(message) {
        if (!message || typeof message !== 'object') return null;
        return {
            id: message.id,
            role: message.role,
            author: message.author,
            time: message.time,
            createdAt: message.createdAt,
            avatarLabel: message.avatarLabel,
            avatarUrl: message.avatarUrl,
            blocks: Array.isArray(message.blocks) ? message.blocks.map(function (block) {
                if (!block || typeof block !== 'object') return null;
                if (block.type === 'buttons' && Array.isArray(block.buttons)) {
                    return {
                        type: 'buttons',
                        buttons: block.buttons.map(function (button) {
                            if (!button || typeof button !== 'object') return null;
                            return {
                                id: button.id,
                                label: button.label,
                                action: button.action,
                                variant: button.variant,
                                disabled: !!button.disabled,
                                payload: button.payload || undefined
                            };
                        }).filter(Boolean)
                    };
                }
                return Object.assign({}, block);
            }).filter(Boolean) : [],
            actions: Array.isArray(message.actions) ? message.actions.map(function (action) {
                if (!action || typeof action !== 'object') return null;
                return {
                    id: action.id,
                    label: action.label,
                    action: action.action,
                    variant: action.variant,
                    disabled: !!action.disabled,
                    payload: action.payload || undefined
                };
            }).filter(Boolean) : undefined,
            status: message.status,
            sortKey: message.sortKey
        };
    }

    function normalizeMessage(rawMessage, fallbackSortKey) {
        var message = cloneMessage(rawMessage);
        if (!message || !message.id) return null;

        var now = Date.now();
        var createdAt = typeof message.createdAt === 'number' ? message.createdAt : now;
        var time = message.time;
        if (!time) {
            try {
                time = new Date(createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            } catch (_) {
                time = '';
            }
        }

        return {
            id: String(message.id),
            role: message.role || 'assistant',
            author: sanitizeDisplayName(message.author) || getDefaultAuthorByRole(message.role || 'assistant'),
            time: time,
            createdAt: createdAt,
            avatarLabel: message.avatarLabel,
            avatarUrl: message.avatarUrl,
            blocks: Array.isArray(message.blocks) ? message.blocks : [],
            actions: Array.isArray(message.actions) ? message.actions : undefined,
            status: message.status,
            sortKey: typeof message.sortKey === 'number' ? message.sortKey : fallbackSortKey
        };
    }

    function sortMessages(messages) {
        return messages.slice().sort(function (a, b) {
            var sortA = typeof a.sortKey === 'number' ? a.sortKey : (typeof a.createdAt === 'number' ? a.createdAt : 0);
            var sortB = typeof b.sortKey === 'number' ? b.sortKey : (typeof b.createdAt === 'number' ? b.createdAt : 0);
            if (sortA !== sortB) return sortA - sortB;
            return String(a.id).localeCompare(String(b.id));
        });
    }

    function buildRenderProps() {
        if (state.rollbackDraft) {
            console.log('[ROLLBACK] buildRenderProps: rollbackDraftPresent=true length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        }
        return Object.assign({}, ensureViewProps(), {
            messages: state.messages,
            composerAttachments: state.composerAttachments,
            rollbackDraft: state.rollbackDraft || undefined,
            _rollbackKey: state._rollbackKey || undefined,
            composerHidden: state.composerHidden,
            onMessageAction: handleMessageAction,
            onComposerImportImage: handleComposerImportImage,
            onComposerScreenshot: handleComposerScreenshot,
            onComposerRemoveAttachment: handleComposerRemoveAttachment,
            onComposerSubmit: handleComposerSubmit,
            onAvatarInteraction: handleAvatarInteraction,
            onAvatarToolStateChange: handleAvatarToolStateChange,
            onJukeboxClick: handleJukeboxClick,
            onAvatarGeneratorClick: handleAvatarGeneratorClick,
            onTranslateToggle: handleTranslateToggle
        });
    }

    function showToast(message, duration) {
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, duration || 3000);
        }
    }

    function ensureBundleLoaded() {
        if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
            return Promise.resolve(window.NekoChatWindow);
        }

        if (loadedPromise) return loadedPromise;

        loadedPromise = new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[data-react-chat-window-bundle="true"]');
            if (existing) {
                // Script already finished loading but API is missing — re-create it
                if (existing.readyState === 'loaded' || existing.readyState === 'complete' || existing.dataset.loaded === 'true') {
                    if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
                        resolve(window.NekoChatWindow);
                    } else {
                        existing.parentNode.removeChild(existing);
                        // Fall through to create a fresh script element below
                    }
                } else if (existing.dataset.error === 'true') {
                    // Script previously failed to load — remove stale element and recreate
                    existing.parentNode.removeChild(existing);
                    // Fall through to create a fresh script element below
                } else {
                    existing.addEventListener('load', function () {
                        existing.dataset.loaded = 'true';
                        if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
                            resolve(window.NekoChatWindow);
                        } else {
                            reject(new Error('React chat bundle loaded but API is missing'));
                        }
                    }, { once: true });
                    existing.addEventListener('error', function () {
                        existing.dataset.error = 'true';
                        reject(new Error('React chat bundle failed to load'));
                    }, { once: true });
                    return;
                }
            }

            var script = document.createElement('script');
            script.src = BUNDLE_SRC + '?v=' + Date.now();
            script.async = true;
            script.dataset.reactChatWindowBundle = 'true';

            script.onload = function () {
                if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
                    resolve(window.NekoChatWindow);
                } else {
                    reject(new Error('React chat bundle loaded but API is missing'));
                }
            };

            script.onerror = function () {
                script.dataset.error = 'true';
                reject(new Error('React chat bundle failed to load'));
            };

            document.body.appendChild(script);
        }).catch(function (error) {
            loadedPromise = null;
            throw error;
        });

        return loadedPromise;
    }

    function getStoredPosition() {
        try {
            var rawLeft = localStorage.getItem(STORAGE_LEFT_KEY);
            var rawTop = localStorage.getItem(STORAGE_TOP_KEY);
            if (rawLeft === null || rawTop === null) return null;
            var left = Number(rawLeft);
            var top = Number(rawTop);
            if (Number.isFinite(left) && Number.isFinite(top)) {
                return { left: left, top: top };
            }
        } catch (_) {}
        return null;
    }

    function persistPosition(left, top) {
        try {
            localStorage.setItem(STORAGE_LEFT_KEY, String(Math.round(left)));
            localStorage.setItem(STORAGE_TOP_KEY, String(Math.round(top)));
        } catch (_) {}
    }

    function persistSize(width, height) {
        try {
            localStorage.setItem(STORAGE_WIDTH_KEY, String(Math.round(width)));
            localStorage.setItem(STORAGE_HEIGHT_KEY, String(Math.round(height)));
        } catch (_) {}
    }

    function getStoredSize() {
        try {
            var rawWidth = localStorage.getItem(STORAGE_WIDTH_KEY);
            var rawHeight = localStorage.getItem(STORAGE_HEIGHT_KEY);
            if (rawWidth === null || rawHeight === null) return null;
            var width = Number(rawWidth);
            var height = Number(rawHeight);
            if (Number.isFinite(width) && Number.isFinite(height) && width >= 320 && height >= 280) {
                return { width: width, height: height };
            }
        } catch (_) {}
        return null;
    }

    function restoreSize() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var stored = getStoredSize();
        if (stored) {
            shell.style.width = stored.width + 'px';
            shell.style.height = stored.height + 'px';
        }
    }

    function clampPosition(left, top) {
        var shell = getShell();
        if (!shell) {
            return { left: left, top: top };
        }

        var rect = shell.getBoundingClientRect();
        var width = rect.width || 960;
        var headerHeight = 52;
        var maxLeft = Math.max(0, window.innerWidth - width);
        var maxTop = Math.max(0, window.innerHeight - headerHeight);

        return {
            left: Math.max(0, Math.min(maxLeft, left)),
            top: Math.max(0, Math.min(maxTop, top))
        };
    }

    function applyPosition(left, top) {
        var shell = getShell();
        if (!shell) return;

        var clamped = clampPosition(left, top);
        shell.style.left = clamped.left + 'px';
        shell.style.top = clamped.top + 'px';
        shell.style.transform = 'none';
    }

    function positionWindowAtLeftMiddle() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var rect = shell.getBoundingClientRect();
        var left = Math.max(0, DESKTOP_DEFAULT_LEFT_MARGIN);
        var top = Math.max(0, Math.round((window.innerHeight - rect.height) / 2));
        applyPosition(left, top);
        persistPosition(left, top);
    }

    function restorePosition() {
        var shell = getShell();
        if (!shell) return;

        if (isMobileWidth()) {
            // 宽度由 CSS calc(100vw - 12px) 控制；transform 的 translate 会污染 applyPosition 坐标。
            shell.style.removeProperty('width');
            shell.style.removeProperty('transform');
            // 不清 height：清掉会让 shell 瞬间回到 CSS 的 `height:auto;max-height:85vh`，
            // grid `auto 1fr auto` 父容器塌缩会把 .message-list 的 clientHeight 挤到几十 px，
            // 浏览器 clamp scrollTop → 0，下一帧 syncMobileContentLayout() 恢复 height 时已经来不及。
            // 保留旧像素值，让紧随其后的 syncMobileContentLayout() 直接覆写，避免中间态。
            // 不清 left/top：手机端允许 expanded 在任意位置飘；只按新视口 clamp 一次，避免旋屏/键盘后溢出。
            if (shell.style.left || shell.style.top) {
                var rect = shell.getBoundingClientRect();
                var clampedLeft = Math.max(0, Math.min(rect.left, window.innerWidth - rect.width));
                var clampedTop = Math.max(0, Math.min(rect.top, window.innerHeight - rect.height));
                shell.style.left = clampedLeft + 'px';
                shell.style.top = clampedTop + 'px';
            }
            return;
        }

        restoreSize();

        var stored = getStoredPosition();
        if (stored) {
            applyPosition(stored.left, stored.top);
        } else {
            positionWindowAtLeftMiddle();
        }
    }

    function mountWindow() {
        var root = getRoot();
        if (!root) return false;

        var api = window.NekoChatWindow;
        var mount = api && (api.mount || api.mountChatWindow);
        if (typeof mount !== 'function') return false;

        mount(root, buildRenderProps());
        mounted = true;
        return true;
    }

    function renderWindow() {
        var overlay = getOverlay();
        if (!overlay || overlay.hidden) return;
        mountWindow();
        scheduleMobileContentLayout();
    }

    function dispatchHostEvent(name, detail) {
        window.dispatchEvent(new CustomEvent(EVENT_PREFIX + name, { detail: detail }));
    }

    function handleMessageAction(message, action) {
        var detail = {
            message: message,
            action: action
        };

        if (typeof state.onMessageAction === 'function') {
            try {
                state.onMessageAction(message, action);
            } catch (error) {
                console.error('[ReactChatWindow] onMessageAction failed:', error);
            }
        }

        dispatchHostEvent('action', detail);
    }

    function handleComposerSubmit(payload) {
        var requestId = payload && typeof payload.requestId === 'string' && payload.requestId
            ? payload.requestId
            : ('req-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8));
        var detail = {
            text: payload && typeof payload.text === 'string' ? payload.text : '',
            requestId: requestId
        };

        var hasAttachments = state.composerAttachments && state.composerAttachments.length > 0;
        if (!detail.text.trim() && !hasAttachments) return;

        // Store last submitted text for rollback on RESPONSE_TOO_LONG
        // Preserve original whitespace so rollback restores exactly what the user typed
        if (detail.text.trim()) {
            state.pendingRollbackDrafts[detail.requestId] = detail.text;
        } else {
            delete state.pendingRollbackDrafts[detail.requestId];
        }
        // Clear any stale rollback so it won't overwrite this new draft
        if (state.rollbackDraft) {
            console.log('[ROLLBACK] handleComposerSubmit: clearing rollbackDraft length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        }
        state.rollbackDraft = '';

        if (typeof state.onComposerSubmit === 'function') {
            try {
                state.onComposerSubmit(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onComposerSubmit failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.sendTextPayload === 'function') {
            window.appButtons.sendTextPayload(detail.text, { source: 'react-chat-window', requestId: detail.requestId });
        } else {
            var input = $('textInputBox');
            var sendButton = $('textSendButton');
            if (input && sendButton) {
                input.value = detail.text;
                sendButton.click();
            } else {
                console.warn('[ReactChatWindow] no composer submit handler available');
            }
        }

        dispatchHostEvent('submit', detail);
    }

    function handleAvatarInteraction(payload) {
        var detail = payload || {};

        if (typeof state.onAvatarInteraction === 'function') {
            try {
                state.onAvatarInteraction(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onAvatarInteraction failed:', error);
            }
        } else {
            console.warn('[ReactChatWindow] no avatar interaction handler registered; dispatching host event only');
        }

        dispatchHostEvent('avatar-interaction', detail);
    }

    function handleAvatarToolStateChange(payload) {
        var detail = payload || {};

        if (typeof state.onAvatarToolStateChange === 'function') {
            try {
                state.onAvatarToolStateChange(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onAvatarToolStateChange failed:', error);
            }
        }

        dispatchHostEvent('avatar-tool-state', detail);
    }

    function handleComposerImportImage() {
        if (typeof state.onComposerImportImage === 'function') {
            try {
                state.onComposerImportImage();
            } catch (error) {
                console.error('[ReactChatWindow] onComposerImportImage failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.openImageImportPicker === 'function') {
            window.appButtons.openImageImportPicker();
        } else {
            console.warn('[ReactChatWindow] no import image handler available');
        }

        dispatchHostEvent('import-image', {});
    }

    function handleComposerScreenshot() {
        if (typeof state.onComposerScreenshot === 'function') {
            try {
                state.onComposerScreenshot();
            } catch (error) {
                console.error('[ReactChatWindow] onComposerScreenshot failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.captureScreenshotToPendingList === 'function') {
            window.appButtons.captureScreenshotToPendingList();
        } else {
            console.warn('[ReactChatWindow] no screenshot handler available');
        }

        dispatchHostEvent('screenshot', {});
    }

    function handleComposerRemoveAttachment(attachmentId) {
        if (typeof state.onComposerRemoveAttachment === 'function') {
            try {
                state.onComposerRemoveAttachment(String(attachmentId || ''));
            } catch (error) {
                console.error('[ReactChatWindow] onComposerRemoveAttachment failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.removePendingAttachmentById === 'function') {
            window.appButtons.removePendingAttachmentById(String(attachmentId || ''));
        } else {
            console.warn('[ReactChatWindow] no remove attachment handler available');
        }

        dispatchHostEvent('remove-attachment', { attachmentId: attachmentId });
    }

    /**
     * Rollback last submitted text to the React composer input.
     * Called when backend discards response due to RESPONSE_TOO_LONG.
     */
    function rollbackLastDraft(requestId) {
        var rollbackText = (requestId && Object.prototype.hasOwnProperty.call(state.pendingRollbackDrafts, requestId))
            ? state.pendingRollbackDrafts[requestId]
            : '';
        if (!rollbackText) return;
        // Use a unique key each time so React useEffect can distinguish invocations
        state.rollbackDraft = rollbackText;
        state._rollbackKey = 'rb-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
        delete state.pendingRollbackDrafts[requestId];
        console.log('[ROLLBACK] rollbackLastDraft: rollbackDraftPresent=true length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        renderWindow();
    }

    function clearPendingRollbackDraft(requestId) {
        if (!requestId) return;
        delete state.pendingRollbackDrafts[requestId];
    }

    function handleJukeboxClick() {
        try {
            if (typeof window.__nekoJukeboxToggle === 'function') {
                // Electron 多窗口模式：通过 IPC 打开独立 Jukebox 窗口
                window.__nekoJukeboxToggle();
            } else if (typeof window.Jukebox !== 'undefined' && typeof window.Jukebox.toggle === 'function') {
                window.Jukebox.toggle();
            } else {
                console.warn('[ReactChatWindow] Jukebox not available');
            }
        } finally {
            dispatchHostEvent('jukebox-click', {});
        }
    }

    function captureAvatarDirect() {
        if (!window.avatarPortrait || typeof window.avatarPortrait.capture !== 'function') {
            // Electron 多窗口模式：通过 IPC 请求 Pet 窗口截取头像
            if (window.__NEKO_MULTI_WINDOW__ && typeof window.__nekoRequestAvatarPreview === 'function') {
                // 优先使用已缓存的外部头像
                if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                    var cached = window.appChatAvatar.getCurrentAvatarDataUrl();
                    if (cached) {
                        window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
                            detail: { dataUrl: cached, source: 'cached' }
                        }));
                        showToast(getI18nText('chat.avatarPreviewReady', '头像已更新'), 2500);
                        return;
                    }
                }
                showToast(getI18nText('chat.avatarPreviewGenerating', '正在生成当前头像...'), 2000);
                var finished = false;
                var timerId = null;
                var finish = function (success) {
                    if (finished) return;
                    finished = true;
                    window.removeEventListener('neko:avatar-preview-ipc-result', onResult);
                    if (timerId) { clearTimeout(timerId); timerId = null; }
                    if (success) {
                        showToast(getI18nText('chat.avatarPreviewReady', '头像已更新'), 2500);
                    } else {
                        showToast(getI18nText('chat.avatarPreviewFailed', '生成头像失败'), 3000);
                    }
                };
                var onResult = function (e) {
                    finish(!!(e.detail && e.detail.dataUrl));
                };
                window.addEventListener('neko:avatar-preview-ipc-result', onResult);
                timerId = setTimeout(function () { finish(false); }, 10000);
                try {
                    window.__nekoRequestAvatarPreview();
                } catch (err) {
                    console.error('[ReactChatWindow] __nekoRequestAvatarPreview threw:', err);
                    finish(false);
                }
                return;
            }
            showToast(getI18nText('chat.avatarPreviewUnavailable', '头像预览功能尚未就绪。'), 3000);
            return;
        }

        showToast(getI18nText('chat.avatarPreviewGenerating', '正在生成当前头像...'), 2000);

        window.avatarPortrait.capture({
            width: 320, height: 320, padding: 0.035,
            shape: 'rounded', radius: 40,
            background: 'rgba(255, 255, 255, 0.96)',
            includeDataUrl: true
        }).then(function (result) {
            if (result && result.dataUrl) {
                // Dispatch the same event that app-chat-adapter.js already listens to
                window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
                    detail: {
                        dataUrl: result.dataUrl,
                        modelType: result.modelType || '',
                        source: 'react-chat-window'
                    }
                }));
                showToast(getI18nText('chat.avatarPreviewReady', '头像已更新'), 2500);
            } else {
                console.warn('[ReactChatWindow] Avatar capture completed without dataUrl');
                showToast(getI18nText('chat.avatarPreviewFailed', '生成头像失败'), 3000);
            }
        }).catch(function (error) {
            console.error('[ReactChatWindow] Avatar capture failed:', error);
            showToast(getI18nText('chat.avatarPreviewFailed', '生成头像失败'), 3000);
        });
    }

    function handleAvatarGeneratorClick() {
        try {
            // 统一走独立头像预览弹窗；弹窗模块自行处理缓存与 IPC 回退。
            if (window.appChatAvatar && typeof window.appChatAvatar.showPopup === 'function') {
                var anchor = document.getElementById('avatarPreviewHeaderButton')
                    || document.getElementById('avatarPreviewButton')
                    || null;
                window.appChatAvatar.showPopup(anchor);
                return;
            }
            // 极端兜底：弹窗模块加载失败时仍保持原有直采逻辑。
            captureAvatarDirect();
        } finally {
            dispatchHostEvent('avatar-generator-click', {});
        }
    }

    function handleTranslateToggle() {
        var bridge = window.subtitleBridge;
        var next;

        try {
            if (bridge && typeof bridge.toggle === 'function') {
                // Use full toggle with runtime side effects (hide/show subtitle, clear timers, re-translate)
                next = bridge.toggle();
            } else {
                throw new Error('subtitleBridge.toggle unavailable');
            }
        } catch (err) {
            console.warn('[ReactChatWindow] bridge.toggle failed, using fallback:', err);
            // Fallback: flip flag manually if bridge not loaded or threw
            var appSt = window.appState;
            var subtitleStore = window.nekoSubtitleShared;
            var subtitleState = subtitleStore && typeof subtitleStore.getSettings === 'function'
                ? subtitleStore.getSettings()
                : null;
            var current = (appSt && typeof appSt.subtitleEnabled !== 'undefined')
                ? appSt.subtitleEnabled
                : (subtitleState ? !!subtitleState.subtitleEnabled : (localStorage.getItem('subtitleEnabled') === 'true'));
            next = !current;
            if (appSt) appSt.subtitleEnabled = next;
            if (subtitleStore && typeof subtitleStore.updateSettings === 'function') {
                subtitleStore.updateSettings({
                    subtitleEnabled: next
                }, {
                    source: 'react-chat-fallback-toggle'
                });
            } else {
                localStorage.setItem('subtitleEnabled', String(next));
            }
            if (window.appSettings && typeof window.appSettings.saveSettings === 'function') {
                window.appSettings.saveSettings();
            }
        }

        // Update React prop to reflect new state
        state.viewProps = Object.assign({}, ensureViewProps(), { translateEnabled: next });
        renderWindow();

        dispatchHostEvent('translate-toggle', { enabled: next });
    }

    function setViewProps(nextViewProps) {
        state.viewProps = Object.assign({}, ensureViewProps(), nextViewProps || {});
        renderWindow();
        return state.viewProps;
    }

    function setMessages(messages) {
        // Compute fallback start past any explicit sortKey in incoming batch
        var maxIncomingSortKey = Array.isArray(messages)
            ? messages.reduce(function (max, message) {
                var key = message && typeof message.sortKey === 'number' && Number.isFinite(message.sortKey)
                    ? message.sortKey : null;
                return (key !== null && key > max) ? key : max;
            }, -1)
            : -1;
        var nextSortKey = Math.max(_sortKeySeq, maxIncomingSortKey + 1);
        var normalized = Array.isArray(messages)
            ? messages.map(function (message) {
                return normalizeMessage(message, nextSortKey++);
            }).filter(Boolean)
            : [];
        state.messages = sortMessages(normalized);
        _sortKeySeq = nextSortKey;
        if (state.messages.length > MAX_MESSAGES) {
            state.messages = state.messages.slice(-MAX_MESSAGES);
        }
        renderWindow();
        return state.messages;
    }

    function setComposerHidden(hidden) {
        state.composerHidden = !!hidden;
        renderWindow();
    }

    function setComposerAttachments(attachments) {
        state.composerAttachments = Array.isArray(attachments)
            ? attachments.map(function (attachment, index) {
                if (!attachment || typeof attachment !== 'object' || !attachment.url) return null;
                return {
                    id: String(attachment.id || ('attachment-' + index)),
                    url: String(attachment.url),
                    alt: attachment.alt ? String(attachment.alt) : ''
                };
            }).filter(Boolean)
            : [];
        renderWindow();
        return state.composerAttachments;
    }

    var MAX_MESSAGES = 50;

    function appendMessage(message) {
        var normalized = normalizeMessage(message, _sortKeySeq++);
        if (!normalized) return null;

        state.messages = sortMessages(state.messages.concat([normalized]));
        if (state.messages.length > MAX_MESSAGES) {
            state.messages = state.messages.slice(-MAX_MESSAGES);
        }
        renderWindow();
        return normalized;
    }

    function updateMessage(messageId, patch) {
        var updatedMessage = null;

        state.messages = state.messages.map(function (message, index) {
            if (String(message.id) !== String(messageId)) return message;
            updatedMessage = normalizeMessage(Object.assign({}, message, patch || {}), index);
            return updatedMessage || message;
        });

        state.messages = sortMessages(state.messages);
        renderWindow();
        return updatedMessage;
    }

    function removeMessage(messageId) {
        var beforeLength = state.messages.length;
        state.messages = state.messages.filter(function (message) {
            return String(message.id) !== String(messageId);
        });
        var changed = state.messages.length !== beforeLength;
        if (changed) {
            renderWindow();
        }
        return changed;
    }

    function clearMessages() {
        state.messages = [];
        _sortKeySeq = 0;
        renderWindow();
    }

    function getStateSnapshot() {
        return {
            mounted: mounted,
            minimized: minimized,
            viewProps: Object.assign({}, ensureViewProps()),
            messages: state.messages.map(cloneMessage),
            composerAttachments: state.composerAttachments.slice(),
            composerHidden: state.composerHidden
        };
    }

    var MINIMIZED_SIZE = 50;            // 桌面/手机：圆球直径
    var isMinimizeTransitioning = false;
    var activeAnimationCleanup = null; // 当前进行中动画的清理函数

    // 返回最小化后 shell 应达到的像素几何。
    // 桌面：50x50 圆球，锚定在对话框原左下角（clamp 到视口内）。
    // 手机：全宽底部胶囊，贴屏幕底边（类似移动 App 的底栏收起态）。
    // 由于 collapse/expand 动画的 transform-origin = 0% 100%（左下角），
    // target.left 应等于 rect.left 同列，target 底边应与 rect 底边对齐
    // （即 target.top = rect.bottom - target.height），这样动画过程中底边不漂移。
    function getMinimizedTarget(rect) {
        // 桌面端和移动端统一使用 50px 圆形悬浮球
        return {
            width: MINIMIZED_SIZE,
            height: MINIMIZED_SIZE,
            left: Math.max(0, Math.min(rect.left, window.innerWidth - MINIMIZED_SIZE)),
            top: Math.max(0, Math.min(rect.bottom - MINIMIZED_SIZE, window.innerHeight - MINIMIZED_SIZE))
        };
    }

    function cancelActiveAnimation() {
        if (activeAnimationCleanup) {
            activeAnimationCleanup();
            activeAnimationCleanup = null;
        }
        isMinimizeTransitioning = false;
    }

    function ensureMinimizedBallIcon() {
        var shell = getShell();
        if (!shell) return null;
        var icon = shell.querySelector('.react-chat-minimized-icon');
        if (!icon) {
            icon = document.createElement('img');
            icon.className = 'react-chat-minimized-icon';
            icon.src = '/static/icons/expand_icon_off.png';
            icon.alt = '';
            icon.draggable = false;
            var handle = getHeader();
            if (handle) {
                handle.appendChild(icon);
            } else {
                shell.appendChild(icon);
            }
        }
        return icon;
    }

    function setMinimized(nextMinimized) {
        var shell = getShell();
        if (!shell) return;

        var wasMinimized = minimized;
        var willMinimize = !!nextMinimized;
        if (wasMinimized === willMinimize) return;
        if (isMinimizeTransitioning) return; // 防止动画期间重复触发
        isMinimizeTransitioning = true;

        minimized = willMinimize;

        if (willMinimize) {
            // ---- 折叠动画：向对话框左下角缩放 ----
            var rect = shell.getBoundingClientRect();

            // 1. 保存当前位置和尺寸，展开时用
            //    如果没有内联宽高（如 chat.html 全屏模式），
            //    使用计算后的像素值，确保展开时能正确恢复
            savedShellSize = {
                width: shell.style.width || (rect.width + 'px'),
                height: shell.style.height || (rect.height + 'px')
            };
            savedShellPosition = {
                left: rect.left,
                top: rect.top
            };

            // 1b. 锁定当前像素几何到内联样式，防止切类后尺寸跳变
            //     （chat.html 全屏规则退出后 shell 会回落到默认尺寸）
            shell.style.width = rect.width + 'px';
            shell.style.height = rect.height + 'px';
            shell.style.left = rect.left + 'px';
            shell.style.top = rect.top + 'px';

            // 2. 最小化后的目标几何：桌面=50px 圆球 / 手机=全宽底部胶囊
            var target = getMinimizedTarget(rect);
            var targetLeft = target.left;
            var targetTop = target.top;

            // 3. 计算缩放比（transform-origin 为 0% 100% 即左下角，无需 translate）
            var sx = rect.width > 0 ? target.width / rect.width : 1;
            var sy = rect.height > 0 ? target.height / rect.height : 1;

            // 4. 初始 transform = identity，添加过渡类
            shell.style.transform = 'scale(1, 1)';
            shell.classList.add('is-collapsing');
            void shell.offsetHeight; // 强制 reflow

            // 5. 设置目标 transform，触发动画
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    shell.style.transform = 'scale(' + sx + ', ' + sy + ')';
                });
            });

            // 6. 过渡结束后切换到最终的 minimized 状态
            var handled = false;
            var collapseTimer = null;
            var finishCollapse = function () {
                if (handled) return;
                handled = true;
                clearTimeout(collapseTimer);
                shell.removeEventListener('transitionend', onEnd);
                activeAnimationCleanup = null;
                shell.classList.remove('is-collapsing');
                shell.style.transform = 'none';
                // 清除内联尺寸，让 .is-minimized 的 CSS 生效
                shell.style.removeProperty('width');
                shell.style.removeProperty('height');
                shell.classList.remove('is-mobile-content-capped');
                // 将位置设为对话框左下角
                shell.style.left = targetLeft + 'px';
                shell.style.top = targetTop + 'px';
                shell.classList.add('is-minimized');
                isMinimizeTransitioning = false;
            };
            var onEnd = function (e) {
                if (e.target !== shell || e.propertyName !== 'transform') return;
                finishCollapse();
            };
            shell.addEventListener('transitionend', onEnd);
            collapseTimer = setTimeout(finishCollapse, 420); // 兜底

            // 注册清理句柄，供 closeWindow / 下次动画调用
            activeAnimationCleanup = function () {
                clearTimeout(collapseTimer);
                shell.removeEventListener('transitionend', onEnd);
                shell.classList.remove('is-collapsing');
                shell.style.transform = 'none';
                handled = true;
            };

        } else {
            // ---- 展开动画：从最小化态（桌面圆球 / 手机底部胶囊）展开 ----
            var curRect = shell.getBoundingClientRect();
            var ballLeft = curRect.left;
            // 桌面圆球的 height≈50，手机胶囊的 height≈48；curRect 直接反映真实值
            var ballBottom = curRect.top + (curRect.height || MINIMIZED_SIZE);

            // 恢复保存的尺寸
            shell.classList.remove('is-minimized');
            if (isMobileWidth()) {
                // 手机端：宽度由 CSS calc(100vw - 12px) 控制，清除内联宽度
                shell.style.removeProperty('width');
                // 高度：优先使用用户手动设置的高度，否则自动计算上限 85vh
                var mobileMaxH = getMobileMaxHeight();
                var savedHeightPx = savedShellSize ? parseFloat(savedShellSize.height) : NaN;
                var restoreHeight;
                if (mobileUserHeight > 0) {
                    restoreHeight = Math.min(mobileUserHeight, mobileMaxH);
                } else if (isFinite(savedHeightPx) && savedHeightPx > 0) {
                    restoreHeight = Math.min(savedHeightPx, mobileMaxH);
                } else {
                    restoreHeight = mobileMaxH;
                }
                if (restoreHeight > 0) shell.style.height = restoreHeight + 'px';
            } else if (savedShellSize) {
                if (savedShellSize.width) shell.style.width = savedShellSize.width;
                if (savedShellSize.height) shell.style.height = savedShellSize.height;
            }

            // 以球的位置为展开后对话框的左下角来计算展开位置
            // 先设临时位置以获取真实尺寸
            shell.style.left = '0px';
            shell.style.top = '0px';
            shell.style.transform = 'none';
            void shell.offsetHeight;
            var expandedRect = shell.getBoundingClientRect();

            // 尺寸无效时（overlay 仍隐藏等边界情况）跳过动画，直接恢复
            if (!expandedRect.width || !expandedRect.height) {
                shell.style.transform = 'none';
                // 尝试恢复到保存的位置
                if (savedShellPosition) {
                    shell.style.left = savedShellPosition.left + 'px';
                    shell.style.top = savedShellPosition.top + 'px';
                }
                savedShellSize = null;
                savedShellPosition = null;
                isMinimizeTransitioning = false;
                requestAnimationFrame(function () {
                    var r = shell.getBoundingClientRect();
                    var clamped = clampPosition(r.left, r.top);
                    if (clamped.left !== r.left || clamped.top !== r.top) {
                        applyPosition(clamped.left, clamped.top);
                    }
                });
            } else {

            // 球的左下角 = 展开后对话框的左下角
            var expandedLeft = ballLeft;
            var expandedTop = ballBottom - expandedRect.height;

            // 先不 clamp，让动画从球位置自然展开，动画结束后再 clamp
            shell.style.left = expandedLeft + 'px';
            shell.style.top = expandedTop + 'px';
            shell.style.transform = 'none';
            void shell.offsetHeight;

            // 重新获取展开后的真实 rect（位置可能已改变）
            expandedRect = shell.getBoundingClientRect();

            // 计算初始缩放：transform-origin 为左下角 (0% 100%)
            // 从当前最小化态的真实尺寸缩回（桌面 50x50 / 手机 full-width x 48），
            // 视觉上的左下角保持不变。
            var sx2 = curRect.width > 0 ? curRect.width / expandedRect.width : 1;
            var sy2 = curRect.height > 0 ? curRect.height / expandedRect.height : 1;

            // 设置初始 transform（看起来还是左下角的小圆）
            shell.style.transform = 'scale(' + sx2 + ', ' + sy2 + ')';
            shell.classList.add('is-expanding');
            void shell.offsetHeight; // 强制 reflow

            // 动画到 identity（展开到完整尺寸）
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    shell.style.transform = 'scale(1, 1)';
                });
            });

            // 动画结束后清理
            var expandHandled = false;
            var expandTimer = null;
            var finishExpand = function () {
                if (expandHandled) return;
                expandHandled = true;
                clearTimeout(expandTimer);
                shell.removeEventListener('transitionend', onExpandEnd);
                activeAnimationCleanup = null;
                shell.classList.remove('is-expanding');
                shell.style.transform = 'none';
                savedShellSize = null;
                savedShellPosition = null;
                isMinimizeTransitioning = false;
                scheduleMobileContentLayout();
                // 确保位置不溢出；全屏模式（/chat）不持久化，
                // 否则 (0,0) 会覆盖 index.html 中用户保存的窗口位置
                requestAnimationFrame(function () {
                    var r = shell.getBoundingClientRect();
                    var clamped = clampPosition(r.left, r.top);
                    applyPosition(clamped.left, clamped.top);
                    if (!window._chatAdapterActive && !isMobileWidth()) {
                        persistPosition(clamped.left, clamped.top);
                    }
                });
            };
            var onExpandEnd = function (e) {
                if (e.target !== shell || e.propertyName !== 'transform') return;
                finishExpand();
            };
            shell.addEventListener('transitionend', onExpandEnd);
            expandTimer = setTimeout(finishExpand, 420); // 兜底

            // 注册清理句柄
            activeAnimationCleanup = function () {
                clearTimeout(expandTimer);
                shell.removeEventListener('transitionend', onExpandEnd);
                shell.classList.remove('is-expanding');
                shell.style.transform = 'none';
                expandHandled = true;
            };

            } // end of else (valid dimensions)
        }

        // 更新按钮图标和 aria
        syncMinimizeUI();
    }

    function syncMinimizeUI() {
        var button = getMinimizeButton();
        var btnIcon = getMinimizeIcon();
        var ballIcon = ensureMinimizedBallIcon();
        if (button) {
            button.setAttribute('aria-label', minimized ? getI18nText('chat.reactWindowRestore', '恢复聊天框') : getI18nText('chat.reactWindowMinimize', '最小化聊天框'));
            button.title = minimized ? getI18nText('chat.reactWindowRestoreShort', '恢复') : getI18nText('chat.reactWindowMinimizeShort', '最小化');
        }
        if (btnIcon) {
            btnIcon.src = minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            btnIcon.alt = minimized ? getI18nText('chat.reactWindowRestore', '恢复聊天框') : getI18nText('chat.reactWindowMinimize', '最小化聊天框');
        }
        // 重置悬浮球图标到默认态（清除可能残留的 hover 图标）
        if (ballIcon) {
            ballIcon.src = '/static/icons/expand_icon_off.png';
        }
    }

    function toggleMinimized() {
        setMinimized(!minimized);
    }

    function prewarmUserDisplayName() {
        if (!window.appChat || typeof window.appChat.ensureUserDisplayName !== 'function') return;
        Promise.resolve(window.appChat.ensureUserDisplayName()).catch(function (error) {
            console.warn('[ReactChatWindow] preload user display name failed:', error);
        });
    }

    function openWindow() {
        var overlay = getOverlay();
        if (!overlay) return;

        prewarmUserDisplayName();
        ensureBundleLoaded()
            .then(function () {
                if (!mountWindow()) {
                    showToast(getI18nText('chat.reactWindowMountFailed', '聊天框挂载失败'), 3000);
                    return;
                }
                // closeWindow 已经会重置 minimized，所以到这里通常 minimized=false
                // 但如果外部直接调用 openWindow（未经 closeWindow），仍需处理
                var wasMinimized = minimized;
                if (wasMinimized) {
                    // overlay 可能还隐藏，先显示再做展开动画
                    overlay.hidden = false;
                    document.body.classList.add('react-chat-window-open');
                    setMinimized(false);
                    scheduleMobileContentLayout();
                } else {
                    overlay.hidden = false;
                    document.body.classList.add('react-chat-window-open');
                    restorePosition();
                    scheduleMobileContentLayout();
                }
            })
            .catch(function (error) {
                console.error('[ReactChatWindow] open failed:', error);
                showToast(getI18nText('chat.reactWindowLoadFailed', '聊天框资源加载失败'), 3500);
            });
    }

    function closeWindow() {
        var overlay = getOverlay();
        if (!overlay) return;
        cancelActiveAnimation(); // 清理进行中的折叠/展开回调

        // 如果当前处于最小化状态，恢复 shell 到正常态
        if (minimized) {
            var shell = getShell();
            if (shell) {
                shell.classList.remove('is-minimized');
                if (savedShellSize) {
                    if (savedShellSize.width) shell.style.width = savedShellSize.width;
                    if (savedShellSize.height) shell.style.height = savedShellSize.height;
                }
                if (savedShellPosition) {
                    shell.style.left = savedShellPosition.left + 'px';
                    shell.style.top = savedShellPosition.top + 'px';
                }
                shell.style.transform = 'none';
            }
            minimized = false;
            savedShellSize = null;
            savedShellPosition = null;
            syncMinimizeUI();
        }

        overlay.hidden = true;
        document.body.classList.remove('react-chat-window-open');
        clearMobileContentCap();
        handleAvatarToolStateChange({
            active: false,
            toolId: null,
            tool: null,
            timestamp: Date.now()
        });
    }

    var CLICK_THRESHOLD = 5; // px – 移动距离低于此值视为点击

    function startDrag(clientX, clientY) {
        var shell = getShell();
        if (!shell) return;
        if (isYuiGuideDragLocked()) return;

        var rect = shell.getBoundingClientRect();
        dragState = {
            pointerOffsetX: clientX - rect.left,
            pointerOffsetY: clientY - rect.top,
            startClientX: clientX,
            startClientY: clientY,
            moved: false
        };

        shell.classList.add('is-dragging');
        document.body.classList.add('react-chat-window-dragging');
    }

    function updateDrag(clientX, clientY) {
        if (!dragState) return;
        if (isYuiGuideDragLocked()) {
            // 教程接管期强制中断拖拽：抑制后续 toggleMinimized，避免最小化球被误展开
            stopDrag({ suppressClick: true });
            return;
        }

        var dx = clientX - dragState.startClientX;
        var dy = clientY - dragState.startClientY;
        if (Math.abs(dx) > CLICK_THRESHOLD || Math.abs(dy) > CLICK_THRESHOLD) {
            dragState.moved = true;
        }

        var left = clientX - dragState.pointerOffsetX;
        var top = clientY - dragState.pointerOffsetY;
        var clamped = clampPosition(left, top);
        applyPosition(clamped.left, clamped.top);
    }

    function stopDrag(options) {
        if (!dragState) return;
        var opts = options || {};

        var wasMoved = dragState.moved;

        var shell = getShell();
        if (shell) {
            shell.classList.remove('is-dragging');
            var rect = shell.getBoundingClientRect();
            // 最小化态下不持久化悬浮球坐标到展开态存储，
            // 否则 restorePosition 会把完整窗口放到悬浮球位置
            // 移动端坐标也不持久化，避免污染桌面端保存的位置
            if (!minimized && !isMobileWidth()) {
                persistPosition(rect.left, rect.top);
            }
        }

        dragState = null;
        document.body.classList.remove('react-chat-window-dragging');

        // 最小化状态下，未发生拖拽移动 → 视为点击，恢复窗口
        // 但 suppressClick=true（如教程接管强制中断）时不触发，避免误展开
        if (minimized && !wasMoved && !opts.suppressClick) {
            toggleMinimized();
        }
    }

    function bindDragging() {
        var header = getHeader();
        if (!header) return;

        header.addEventListener('mousedown', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            var minimizeButton = $('reactChatWindowMinimizeButton');
            if (minimizeButton && minimizeButton.contains(event.target)) return;
            var avatarHeaderBtn = $('avatarPreviewHeaderButton');
            if (avatarHeaderBtn && avatarHeaderBtn.contains(event.target)) return;
            startDrag(event.clientX, event.clientY);
            event.preventDefault();
        });

        // touchstart 不 preventDefault：让浏览器自行决定是滚动还是点击，
        // 真正进入拖拽后由 touchmove（passive: false）阻止滚动即可。
        header.addEventListener('touchstart', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            var minimizeButton = $('reactChatWindowMinimizeButton');
            if (minimizeButton && minimizeButton.contains(event.target)) return;
            var avatarHeaderBtn = $('avatarPreviewHeaderButton');
            if (avatarHeaderBtn && avatarHeaderBtn.contains(event.target)) return;
            if (!event.touches || event.touches.length === 0) return;
            startDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

        document.addEventListener('mousemove', function (event) {
            if (!dragState) return;
            updateDrag(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!dragState || !event.touches || event.touches.length === 0) return;
            // chat.html 不走 mobile 路径，保留原 passive: true 语义，不吞原生滚动。
            if (!isElectronChatWindow()) event.preventDefault();
            updateDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: false });

        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', stopDrag);
        document.addEventListener('touchcancel', stopDrag);
    }

    var MIN_WIDTH = 320;
    var MIN_HEIGHT = 280;
    var RESIZE_DIRECTIONS = ['n', 's', 'w', 'e', 'nw', 'ne', 'sw', 'se'];

    function createResizeEdges() {
        var shell = getShell();
        if (!shell) return;

        RESIZE_DIRECTIONS.forEach(function (dir) {
            var edge = document.createElement('div');
            edge.className = 'react-chat-resize-edge react-chat-resize-' + dir;
            edge.dataset.resizeDir = dir;
            shell.appendChild(edge);
        });
    }

    function startResize(clientX, clientY, direction) {
        var shell = getShell();
        if (!shell) return;
        // 教程接管期禁止 resize，否则用户拉伸会让教程锚点和高亮错位
        if (isYuiGuideDragLocked()) return;
        // 手机端仅允许向上拖动调整高度（北侧边缘）
        if (isMobileWidth() && direction !== 'n') return;
        if (minimized) return;

        var rect = shell.getBoundingClientRect();
        resizeState = {
            dir: direction,
            startX: clientX,
            startY: clientY,
            origLeft: rect.left,
            origTop: rect.top,
            origWidth: rect.width,
            origHeight: rect.height
        };

        document.body.classList.add('react-chat-window-resizing');
    }

    function updateResize(clientX, clientY) {
        if (!resizeState) return;
        // 教程接管期强制中断 resize，与 updateDrag 的 lock 行为对称
        if (isYuiGuideDragLocked()) {
            stopResize();
            return;
        }

        var shell = getShell();
        if (!shell) return;

        var dx = clientX - resizeState.startX;
        var dy = clientY - resizeState.startY;
        var dir = resizeState.dir;

        var newLeft = resizeState.origLeft;
        var newTop = resizeState.origTop;
        var newWidth = resizeState.origWidth;
        var newHeight = resizeState.origHeight;

        // 手机端仅处理高度变化
        var mobile = isMobileWidth();

        if (!mobile && dir.indexOf('e') !== -1) {
            newWidth = Math.max(MIN_WIDTH, resizeState.origWidth + dx);
        }
        if (!mobile && dir.indexOf('w') !== -1) {
            var proposedWidth = resizeState.origWidth - dx;
            if (proposedWidth >= MIN_WIDTH) {
                newWidth = proposedWidth;
                newLeft = resizeState.origLeft + dx;
            } else {
                newWidth = MIN_WIDTH;
                newLeft = resizeState.origLeft + resizeState.origWidth - MIN_WIDTH;
            }
        }
        if (!mobile && dir.indexOf('s') !== -1) {
            newHeight = Math.max(MIN_HEIGHT, resizeState.origHeight + dy);
        }
        if (dir.indexOf('n') !== -1) {
            var minH = mobile ? MOBILE_MIN_HEIGHT : MIN_HEIGHT;
            var proposedHeight = resizeState.origHeight - dy;
            if (proposedHeight >= minH) {
                newHeight = proposedHeight;
                newTop = resizeState.origTop + dy;
            } else {
                newHeight = minH;
                newTop = resizeState.origTop + resizeState.origHeight - minH;
            }
        }

        // Clamp to viewport
        newLeft = Math.max(0, Math.min(newLeft, window.innerWidth - 50));
        newTop = Math.max(0, Math.min(newTop, window.innerHeight - 50));
        newWidth = Math.min(newWidth, window.innerWidth);
        newHeight = Math.min(newHeight, window.innerHeight);

        if (mobile) {
            // 手机端：更新高度和 top，保持 CSS 控制的 left/width
            var maxMobileH = getMobileMaxHeight();
            var clampedH = Math.min(newHeight, maxMobileH);
            // 高度被截断时重新计算 top，保持面板底部不动
            if (clampedH < newHeight) {
                newTop = window.innerHeight - clampedH;
            }
            shell.style.height = clampedH + 'px';
            // 设置 top 并清除 bottom，使北侧拖拽正确向上扩展
            shell.style.top = newTop + 'px';
            shell.style.bottom = 'auto';
        } else {
            shell.style.width = newWidth + 'px';
            shell.style.height = newHeight + 'px';
            shell.style.left = newLeft + 'px';
            shell.style.top = newTop + 'px';
            shell.style.transform = 'none';
        }
    }

    function stopResize() {
        if (!resizeState) return;

        var shell = getShell();
        if (shell) {
            var rect = shell.getBoundingClientRect();
            if (isMobileWidth()) {
                // 手机端：保存用户设置的高度，恢复底部锚定
                mobileUserHeight = Math.round(rect.height);
                shell.style.removeProperty('top');
                shell.style.removeProperty('bottom');
                try {
                    localStorage.setItem(MOBILE_HEIGHT_STORAGE_KEY, String(mobileUserHeight));
                } catch (_) {}
            } else {
                persistPosition(rect.left, rect.top);
                persistSize(rect.width, rect.height);
            }
        }

        resizeState = null;
        document.body.classList.remove('react-chat-window-resizing');
    }

    function bindResizing() {
        var shell = getShell();
        if (!shell) return;

        shell.addEventListener('mousedown', function (event) {
            var target = event.target;
            if (!target || !target.dataset || !target.dataset.resizeDir) return;
            startResize(event.clientX, event.clientY, target.dataset.resizeDir);
            event.preventDefault();
        });

        shell.addEventListener('touchstart', function (event) {
            var target = event.target;
            if (!target || !target.dataset || !target.dataset.resizeDir) return;
            if (!event.touches || event.touches.length === 0) return;
            startResize(event.touches[0].clientX, event.touches[0].clientY, target.dataset.resizeDir);
            // chat.html 保留原 passive 语义；只在真正进入 resize（非 chat.html）才吞事件。
            if (resizeState && !isElectronChatWindow()) event.preventDefault();
        }, { passive: false });

        document.addEventListener('mousemove', function (event) {
            if (!resizeState) return;
            updateResize(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!resizeState || !event.touches || event.touches.length === 0) return;
            if (!isElectronChatWindow()) event.preventDefault();
            updateResize(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: false });

        document.addEventListener('mouseup', stopResize);
        document.addEventListener('touchend', stopResize);
        document.addEventListener('touchcancel', stopResize);
    }

    function bindBridgeEvents() {
        window.addEventListener(EVENT_PREFIX + 'set-messages', function (event) {
            setMessages(event.detail && event.detail.messages);
        });

        window.addEventListener(EVENT_PREFIX + 'append-message', function (event) {
            appendMessage(event.detail && event.detail.message);
        });

        window.addEventListener(EVENT_PREFIX + 'update-message', function (event) {
            var detail = event.detail || {};
            updateMessage(detail.messageId, detail.patch);
        });

        window.addEventListener(EVENT_PREFIX + 'remove-message', function (event) {
            removeMessage(event.detail && event.detail.messageId);
        });

        window.addEventListener(EVENT_PREFIX + 'clear-messages', function () {
            clearMessages();
        });

        window.addEventListener(EVENT_PREFIX + 'set-view-props', function (event) {
            setViewProps(event.detail && event.detail.viewProps);
        });

        window.addEventListener(EVENT_PREFIX + 'set-composer-attachments', function (event) {
            setComposerAttachments(event.detail && event.detail.attachments);
        });

        window.addEventListener(EVENT_PREFIX + 'set-composer-hidden', function (event) {
            setComposerHidden(event.detail && event.detail.hidden);
        });
    }

    function init() {
        var trigger = $('reactChatWindowButton');
        var closeButton = $('reactChatWindowCloseButton');
        var minimizeButton = getMinimizeButton();
        var backdrop = $('react-chat-window-backdrop');
        var avatarHeaderButton = $('avatarPreviewHeaderButton');

        ensureViewProps();
        prewarmUserDisplayName();

        if (trigger) {
            trigger.addEventListener('click', openWindow);
        }
        if (closeButton) {
            closeButton.addEventListener('click', closeWindow);
        }
        if (minimizeButton) {
            minimizeButton.addEventListener('click', function (event) {
                event.stopPropagation();
                toggleMinimized();
            });
        }
        // Note: the avatarPreviewHeaderButton click is bound by app-chat-avatar.js
        // (it owns the standalone avatar preview popup and toggling behavior).
        // We only fire the host event here for external listeners/analytics.
        if (avatarHeaderButton) {
            avatarHeaderButton.addEventListener('click', function () {
                dispatchHostEvent('avatar-generator-click', {});
            });
        }
        if (backdrop) {
            // When chat adapter is active (primary mode), backdrop should not
            // block interaction with the model behind it.
            if (!window._chatAdapterActive) {
                backdrop.addEventListener('click', closeWindow);
            } else {
                backdrop.style.pointerEvents = 'none';
            }
        }

        bindDragging();
        createResizeEdges();
        bindResizing();
        bindBridgeEvents();

        // 恢复手机端用户设置的高度
        try {
            var storedMobileHeight = localStorage.getItem(MOBILE_HEIGHT_STORAGE_KEY);
            if (storedMobileHeight) {
                var parsed = Number(storedMobileHeight);
                if (Number.isFinite(parsed) && parsed >= MOBILE_MIN_HEIGHT) {
                    mobileUserHeight = parsed;
                }
            }
        } catch (_) {}

        // 悬浮球 hover 效果（参考原版 #chat-container 实现）
        var header = getHeader();
        if (header) {
            header.addEventListener('mouseenter', function () {
                if (!minimized) return;
                var shell = getShell();
                var ico = shell && shell.querySelector('.react-chat-minimized-icon');
                if (ico) ico.src = '/static/icons/expand_icon_on.png';
            });
            header.addEventListener('mouseleave', function () {
                if (!minimized) return;
                var shell = getShell();
                var ico = shell && shell.querySelector('.react-chat-minimized-icon');
                if (ico) ico.src = '/static/icons/expand_icon_off.png';
            });
        }

        window.addEventListener('keydown', function (event) {
            if (window._chatAdapterActive) return;
            var overlay = getOverlay();
            if (event.key === 'Escape' && overlay && !overlay.hidden) {
                closeWindow();
            }
        });

        window.addEventListener('resize', function () {
            var overlay = getOverlay();
            if (overlay && !overlay.hidden) {
                if (minimized) {
                    // 最小化态下，根据当前布局（桌面圆球 / 手机胶囊）重新贴到视口内。
                    // 手机胶囊宽度由 CSS !important 控制（width: calc(100vw - 12px)），
                    // 这里只需修正左上角坐标，避免旋转屏或拖窗后溢出。
                    var shell = getShell();
                    if (shell) {
                        var r = shell.getBoundingClientRect();
                        var minW = r.width || MINIMIZED_SIZE;
                        var minH = r.height || MINIMIZED_SIZE;
                        var safeLeft, safeTop;
                        if (isMobileWidth()) {
                            // 圆形悬浮球：保持用户拖拽位置，仅 clamp 到视口内
                            safeLeft = Math.max(0, Math.min(r.left, window.innerWidth - minW));
                            safeTop = Math.max(0, Math.min(r.top, window.innerHeight - minH));
                        } else {
                            safeLeft = Math.max(0, Math.min(r.left, window.innerWidth - minW));
                            safeTop = Math.max(0, Math.min(r.top, window.innerHeight - minH));
                        }
                        if (safeLeft !== r.left || safeTop !== r.top) {
                            shell.style.left = safeLeft + 'px';
                            shell.style.top = safeTop + 'px';
                        }
                    }
                } else {
                    restorePosition();
                    scheduleMobileContentLayout();
                }
            }
        });

        window.addEventListener('localechange', function () {
            state.viewProps = createBaseViewProps();
            renderWindow();
        });
    }

    async function initAfterStorageBarrier() {
        if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
            try {
                await window.waitForStorageLocationStartupBarrier();
            } catch (_) {}
        } else if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            try {
                await window.__nekoStorageLocationStartupBarrier;
            } catch (_) {}
        }
        init();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAfterStorageBarrier);
    } else {
        initAfterStorageBarrier();
    }

    window.reactChatWindowHost = {
        ensureBundleLoaded: ensureBundleLoaded,
        openWindow: openWindow,
        closeWindow: closeWindow,
        setViewProps: setViewProps,
        setMessages: setMessages,
        setComposerAttachments: setComposerAttachments,
        setComposerHidden: setComposerHidden,
        appendMessage: appendMessage,
        updateMessage: updateMessage,
        removeMessage: removeMessage,
        clearMessages: clearMessages,
        getState: getStateSnapshot,
        setOnMessageAction: function (handler) {
            state.onMessageAction = typeof handler === 'function' ? handler : null;
        },
        setOnComposerImportImage: function (handler) {
            state.onComposerImportImage = typeof handler === 'function' ? handler : null;
        },
        setOnComposerScreenshot: function (handler) {
            state.onComposerScreenshot = typeof handler === 'function' ? handler : null;
        },
        setOnComposerRemoveAttachment: function (handler) {
            state.onComposerRemoveAttachment = typeof handler === 'function' ? handler : null;
        },
        setOnComposerSubmit: function (handler) {
            state.onComposerSubmit = typeof handler === 'function' ? handler : null;
        },
        setOnAvatarInteraction: function (handler) {
            state.onAvatarInteraction = typeof handler === 'function' ? handler : null;
        },
        setOnAvatarToolStateChange: function (handler) {
            state.onAvatarToolStateChange = typeof handler === 'function' ? handler : null;
        },
        rollbackLastDraft: rollbackLastDraft,
        clearPendingRollbackDraft: clearPendingRollbackDraft,
        isMounted: function () { return mounted; }
    };
})();
