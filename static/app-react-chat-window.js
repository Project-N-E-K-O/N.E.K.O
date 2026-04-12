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
        onMessageAction: null,
        onComposerImportImage: null,
        onComposerScreenshot: null,
        onComposerRemoveAttachment: null,
        onComposerSubmit: null
    };

    var MOBILE_MAX_HEIGHT_RATIO = 0.5;
    var MOBILE_MESSAGE_MIN_HEIGHT = 60;
    var mobileLayoutFrame = 0;

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

    function clearMobileContentCap() {
        var shell = getShell();
        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        if (shell.dataset.mobileAutoHeight === 'true') {
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

        var topbar = root.querySelector('.window-topbar');
        var composer = root.querySelector('.composer-panel');
        var messageList = root.querySelector('.message-list');
        if (!topbar || !composer || !messageList) {
            resetMobileContentLayoutState(shell, topbar, composer, messageList);
            return;
        }

        var maxHeight = Math.max(0, Math.floor(window.innerHeight * MOBILE_MAX_HEIGHT_RATIO));
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
        return Object.assign({}, ensureViewProps(), {
            messages: state.messages,
            composerAttachments: state.composerAttachments,
            onMessageAction: handleMessageAction,
            onComposerImportImage: handleComposerImportImage,
            onComposerScreenshot: handleComposerScreenshot,
            onComposerRemoveAttachment: handleComposerRemoveAttachment,
            onComposerSubmit: handleComposerSubmit,
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
            shell.style.removeProperty('width');
            shell.style.removeProperty('height');
            shell.style.removeProperty('transform');
            return;
        }

        restoreSize();

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
        var detail = {
            text: payload && typeof payload.text === 'string' ? payload.text : ''
        };

        var hasAttachments = state.composerAttachments && state.composerAttachments.length > 0;
        if (!detail.text.trim() && !hasAttachments) return;

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
            // Prefer legacy button if it exists (index.html); absent in chat.html
            var legacyBtn = document.getElementById('avatarPreviewButton');
            if (legacyBtn) {
                legacyBtn.click();
                return;
            }
            // React-first mode or standalone chat window — capture directly
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
            var current = (appSt && typeof appSt.subtitleEnabled !== 'undefined')
                ? appSt.subtitleEnabled
                : localStorage.getItem('subtitleEnabled') === 'true';
            next = !current;
            if (appSt) appSt.subtitleEnabled = next;
            localStorage.setItem('subtitleEnabled', String(next));
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
            composerAttachments: state.composerAttachments.slice()
        };
    }

    var MINIMIZED_SIZE = 50;
    var isMinimizeTransitioning = false;
    var activeAnimationCleanup = null; // 当前进行中动画的清理函数

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

            // 2. 球的目标位置 = 对话框自身的左下角（clamp 到视口内）
            var targetLeft = Math.max(0, Math.min(rect.left, window.innerWidth - MINIMIZED_SIZE));
            var targetTop = Math.max(0, Math.min(rect.bottom - MINIMIZED_SIZE, window.innerHeight - MINIMIZED_SIZE));

            // 3. 计算缩放比（transform-origin 为 0% 100% 即左下角，无需 translate）
            var sx = rect.width > 0 ? MINIMIZED_SIZE / rect.width : 1;
            var sy = rect.height > 0 ? MINIMIZED_SIZE / rect.height : 1;

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
            // ---- 展开动画：从球位置向右上角展开 ----
            var curRect = shell.getBoundingClientRect();
            var ballLeft = curRect.left;
            var ballBottom = curRect.top + (curRect.height || MINIMIZED_SIZE);

            // 恢复保存的尺寸
            shell.classList.remove('is-minimized');
            if (savedShellSize) {
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
            // 缩放到 MINIMIZED_SIZE 时，视觉上的左下角保持不变
            var sx2 = MINIMIZED_SIZE / expandedRect.width;
            var sy2 = MINIMIZED_SIZE / expandedRect.height;

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
                    if (!window._chatAdapterActive) {
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
            button.setAttribute('aria-label', minimized ? getI18nText('chat.reactWindowRestore', '恢复新版聊天框') : getI18nText('chat.reactWindowMinimize', '最小化新版聊天框'));
            button.title = minimized ? getI18nText('chat.reactWindowRestoreShort', '恢复') : getI18nText('chat.reactWindowMinimizeShort', '最小化');
        }
        if (btnIcon) {
            btnIcon.src = minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            btnIcon.alt = minimized ? getI18nText('chat.reactWindowRestore', '恢复新版聊天框') : getI18nText('chat.reactWindowMinimize', '最小化新版聊天框');
        }
        // 重置悬浮球图标到默认态（清除可能残留的 hover 图标）
        if (ballIcon) {
            ballIcon.src = '/static/icons/expand_icon_off.png';
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
                showToast(getI18nText('chat.reactWindowLoadFailed', '新版聊天框资源加载失败'), 3500);
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
    }

    var CLICK_THRESHOLD = 5; // px – 移动距离低于此值视为点击

    function startDrag(clientX, clientY) {
        var shell = getShell();
        if (!shell) return;
        if (isMobileWidth() && !minimized) return;

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

    function stopDrag() {
        if (!dragState) return;

        var wasMoved = dragState.moved;

        var shell = getShell();
        if (shell) {
            shell.classList.remove('is-dragging');
            var rect = shell.getBoundingClientRect();
            // 最小化态下不持久化悬浮球坐标到展开态存储，
            // 否则 restorePosition 会把完整窗口放到悬浮球位置
            if (!minimized) {
                persistPosition(rect.left, rect.top);
            }
        }

        dragState = null;
        document.body.classList.remove('react-chat-window-dragging');

        // 最小化状态下，未发生拖拽移动 → 视为点击，恢复窗口
        if (minimized && !wasMoved) {
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
            updateDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

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
        if (!shell || isMobileWidth()) return;

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

        var shell = getShell();
        if (!shell) return;

        var dx = clientX - resizeState.startX;
        var dy = clientY - resizeState.startY;
        var dir = resizeState.dir;

        var newLeft = resizeState.origLeft;
        var newTop = resizeState.origTop;
        var newWidth = resizeState.origWidth;
        var newHeight = resizeState.origHeight;

        if (dir.indexOf('e') !== -1) {
            newWidth = Math.max(MIN_WIDTH, resizeState.origWidth + dx);
        }
        if (dir.indexOf('w') !== -1) {
            var proposedWidth = resizeState.origWidth - dx;
            if (proposedWidth >= MIN_WIDTH) {
                newWidth = proposedWidth;
                newLeft = resizeState.origLeft + dx;
            } else {
                newWidth = MIN_WIDTH;
                newLeft = resizeState.origLeft + resizeState.origWidth - MIN_WIDTH;
            }
        }
        if (dir.indexOf('s') !== -1) {
            newHeight = Math.max(MIN_HEIGHT, resizeState.origHeight + dy);
        }
        if (dir.indexOf('n') !== -1) {
            var proposedHeight = resizeState.origHeight - dy;
            if (proposedHeight >= MIN_HEIGHT) {
                newHeight = proposedHeight;
                newTop = resizeState.origTop + dy;
            } else {
                newHeight = MIN_HEIGHT;
                newTop = resizeState.origTop + resizeState.origHeight - MIN_HEIGHT;
            }
        }

        // Clamp to viewport
        newLeft = Math.max(0, Math.min(newLeft, window.innerWidth - 50));
        newTop = Math.max(0, Math.min(newTop, window.innerHeight - 50));
        newWidth = Math.min(newWidth, window.innerWidth);
        newHeight = Math.min(newHeight, window.innerHeight);

        shell.style.width = newWidth + 'px';
        shell.style.height = newHeight + 'px';
        shell.style.left = newLeft + 'px';
        shell.style.top = newTop + 'px';
        shell.style.transform = 'none';
    }

    function stopResize() {
        if (!resizeState) return;

        var shell = getShell();
        if (shell) {
            var rect = shell.getBoundingClientRect();
            persistPosition(rect.left, rect.top);
            persistSize(rect.width, rect.height);
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
        }, { passive: true });

        document.addEventListener('mousemove', function (event) {
            if (!resizeState) return;
            updateResize(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!resizeState || !event.touches || event.touches.length === 0) return;
            updateResize(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

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
    }

    function init() {
        var trigger = $('reactChatWindowButton');
        var closeButton = $('reactChatWindowCloseButton');
        var minimizeButton = getMinimizeButton();
        var backdrop = $('react-chat-window-backdrop');
        var avatarHeaderButton = $('avatarPreviewHeaderButton');

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
        if (avatarHeaderButton) {
            avatarHeaderButton.addEventListener('click', function (event) {
                event.stopPropagation();
                // Notify external listeners/analytics/extensions first
                dispatchHostEvent('avatar-generator-click', {});
                // Always use direct capture — the legacy avatarPreviewButton opens
                // the old-chat preview card which is hidden behind the React overlay.
                captureAvatarDirect();
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
                    // 球留在当前位置，只需确保不溢出屏幕
                    var shell = getShell();
                    if (shell) {
                        var r = shell.getBoundingClientRect();
                        var safeLeft = Math.max(0, Math.min(r.left, window.innerWidth - MINIMIZED_SIZE));
                        var safeTop = Math.max(0, Math.min(r.top, window.innerHeight - MINIMIZED_SIZE));
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
