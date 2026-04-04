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
    var EVENT_PREFIX = 'react-chat-window:';

    var loadedPromise = null;
    var mounted = false;
    var dragState = null;
    var minimized = false;

    var state = {
        viewProps: null,
        messages: [],
        composerAttachments: [],
        onMessageAction: null,
        onComposerImportImage: null,
        onComposerScreenshot: null,
        onComposerRemoveAttachment: null,
        onComposerSubmit: null
    };

    function $(id) {
        return document.getElementById(id);
    }

    function isMobileWidth() {
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

    function getMinimizeButton() {
        return $('reactChatWindowMinimizeButton');
    }

    function getMinimizeIcon() {
        return $('reactChatWindowMinimizeIcon');
    }

    function getRoot() {
        return $('react-chat-window-root');
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
            streamingStatusLabel: getI18nText('chat.messageStreaming', '生成中'),
            failedStatusLabel: getI18nText('chat.messageFailed', '发送失败'),
            inputHint: getI18nText('chat.reactWindowInputHint', 'Enter 发送，Shift + Enter 换行')
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
        return Object.assign({}, ensureViewProps(), {
            messages: state.messages,
            composerAttachments: state.composerAttachments,
            onMessageAction: handleMessageAction,
            onComposerImportImage: handleComposerImportImage,
            onComposerScreenshot: handleComposerScreenshot,
            onComposerRemoveAttachment: handleComposerRemoveAttachment,
            onComposerSubmit: handleComposerSubmit
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
            var left = Number(localStorage.getItem(STORAGE_LEFT_KEY));
            var top = Number(localStorage.getItem(STORAGE_TOP_KEY));
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
        if (!shell || isMobileWidth()) return;

        var clamped = clampPosition(left, top);
        shell.style.left = clamped.left + 'px';
        shell.style.top = clamped.top + 'px';
        shell.style.transform = 'none';
    }

    function centerWindow() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var rect = shell.getBoundingClientRect();
        var left = Math.max(0, Math.round((window.innerWidth - rect.width) / 2));
        var top = Math.max(0, Math.round((window.innerHeight - rect.height) / 2));
        applyPosition(left, top);
        persistPosition(left, top);
    }

    function restorePosition() {
        var shell = getShell();
        if (!shell) return;

        if (isMobileWidth()) {
            shell.style.removeProperty('left');
            shell.style.removeProperty('top');
            shell.style.removeProperty('transform');
            return;
        }

        var stored = getStoredPosition();
        if (stored) {
            applyPosition(stored.left, stored.top);
        } else {
            centerWindow();
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
                state.onMessageAction(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onMessageAction failed:', error);
            }
        }

        dispatchHostEvent('action', detail);
    }

    function handleComposerSubmit(payload) {
        var detail = {
            text: payload && typeof payload.text === 'string' ? payload.text : ''
        };

        if (!detail.text.trim()) return;

        if (typeof state.onComposerSubmit === 'function') {
            try {
                state.onComposerSubmit(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onComposerSubmit failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.sendTextPayload === 'function') {
            window.appButtons.sendTextPayload(detail.text, { source: 'react-chat-window' });
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

    function setViewProps(nextViewProps) {
        state.viewProps = Object.assign({}, ensureViewProps(), nextViewProps || {});
        renderWindow();
        return state.viewProps;
    }

    function setMessages(messages) {
        var normalized = Array.isArray(messages)
            ? messages.map(function (message, index) {
                return normalizeMessage(message, index);
            }).filter(Boolean)
            : [];
        state.messages = sortMessages(normalized);
        renderWindow();
        return state.messages;
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

    function appendMessage(message) {
        var normalized = normalizeMessage(message, state.messages.length);
        if (!normalized) return null;

        state.messages = sortMessages(state.messages.concat([normalized]));
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
        renderWindow();
    }

    function getStateSnapshot() {
        return {
            mounted: mounted,
            minimized: minimized,
            viewProps: Object.assign({}, ensureViewProps()),
            messages: state.messages.map(cloneMessage),
            composerAttachments: state.composerAttachments.slice()
        };
    }

    function setMinimized(nextMinimized) {
        var shell = getShell();
        if (!shell) return;

        minimized = !!nextMinimized;
        shell.classList.toggle('is-minimized', minimized);

        var button = getMinimizeButton();
        var icon = getMinimizeIcon();
        if (button) {
            button.setAttribute('aria-label', minimized ? getI18nText('chat.reactWindowRestore', '恢复新版聊天框') : getI18nText('chat.reactWindowMinimize', '最小化新版聊天框'));
            button.title = minimized ? getI18nText('chat.reactWindowRestoreShort', '恢复') : getI18nText('chat.reactWindowMinimizeShort', '最小化');
        }
        if (icon) {
            icon.src = minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            icon.alt = minimized ? getI18nText('chat.reactWindowRestore', '恢复新版聊天框') : getI18nText('chat.reactWindowMinimize', '最小化新版聊天框');
        }
    }

    function toggleMinimized() {
        setMinimized(!minimized);
    }

    function openWindow() {
        var overlay = getOverlay();
        if (!overlay) return;

        ensureBundleLoaded()
            .then(function () {
                if (!mountWindow()) {
                    showToast(getI18nText('chat.reactWindowMountFailed', '新版聊天框挂载失败'), 3000);
                    return;
                }
                setMinimized(false);
                overlay.hidden = false;
                document.body.classList.add('react-chat-window-open');
                restorePosition();
            })
            .catch(function (error) {
                console.error('[ReactChatWindow] open failed:', error);
                showToast(getI18nText('chat.reactWindowLoadFailed', '新版聊天框资源加载失败'), 3500);
            });
    }

    function closeWindow() {
        var overlay = getOverlay();
        if (!overlay) return;
        overlay.hidden = true;
        document.body.classList.remove('react-chat-window-open');
    }

    function startDrag(clientX, clientY) {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var rect = shell.getBoundingClientRect();
        dragState = {
            pointerOffsetX: clientX - rect.left,
            pointerOffsetY: clientY - rect.top
        };

        shell.classList.add('is-dragging');
        document.body.classList.add('react-chat-window-dragging');
    }

    function updateDrag(clientX, clientY) {
        if (!dragState) return;

        var left = clientX - dragState.pointerOffsetX;
        var top = clientY - dragState.pointerOffsetY;
        var clamped = clampPosition(left, top);
        applyPosition(clamped.left, clamped.top);
    }

    function stopDrag() {
        if (!dragState) return;

        var shell = getShell();
        if (shell) {
            shell.classList.remove('is-dragging');
            var rect = shell.getBoundingClientRect();
            persistPosition(rect.left, rect.top);
        }

        dragState = null;
        document.body.classList.remove('react-chat-window-dragging');
    }

    function bindDragging() {
        var header = getHeader();
        if (!header) return;

        header.addEventListener('mousedown', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            var minimizeButton = $('reactChatWindowMinimizeButton');
            if (minimizeButton && minimizeButton.contains(event.target)) return;
            startDrag(event.clientX, event.clientY);
            event.preventDefault();
        });

        header.addEventListener('touchstart', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            var minimizeButton = $('reactChatWindowMinimizeButton');
            if (minimizeButton && minimizeButton.contains(event.target)) return;
            if (!event.touches || event.touches.length === 0) return;
            startDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

        document.addEventListener('mousemove', function (event) {
            if (!dragState) return;
            updateDrag(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!dragState || !event.touches || event.touches.length === 0) return;
            updateDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', stopDrag);
        document.addEventListener('touchcancel', stopDrag);
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
    }

    function init() {
        var trigger = $('reactChatWindowButton');
        var closeButton = $('reactChatWindowCloseButton');
        var minimizeButton = getMinimizeButton();
        var backdrop = $('react-chat-window-backdrop');

        ensureViewProps();

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
        if (backdrop) {
            backdrop.addEventListener('click', closeWindow);
        }

        bindDragging();
        bindBridgeEvents();

        window.addEventListener('keydown', function (event) {
            var overlay = getOverlay();
            if (event.key === 'Escape' && overlay && !overlay.hidden) {
                closeWindow();
            }
        });

        window.addEventListener('resize', function () {
            var overlay = getOverlay();
            if (overlay && !overlay.hidden) {
                restorePosition();
            }
        });

        window.addEventListener('localechange', function () {
            state.viewProps = createBaseViewProps();
            renderWindow();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.reactChatWindowHost = {
        ensureBundleLoaded: ensureBundleLoaded,
        openWindow: openWindow,
        closeWindow: closeWindow,
        setViewProps: setViewProps,
        setMessages: setMessages,
        setComposerAttachments: setComposerAttachments,
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
        isMounted: function () { return mounted; }
    };
})();
