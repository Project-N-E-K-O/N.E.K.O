/**
 * app-chat-avatar.js — 聊天框内的当前头像预览
 * 依赖：avatar-portrait.js
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;

    let isCapturing = false;
    let pendingAutoCapture = false;
    let activeCaptureToken = 0;
    let cachedPreview = null;
    let autoCaptureTimer = null;
    let lastScheduledCacheKey = '';
    // 多窗口模式：由 IPC 从 Pet 窗口注入的头像（/chat 页面无本地模型）
    let externalAvatarDataUrl = '';
    let externalAvatarModelType = '';

    function translateLabel(key, fallback) {
        if (typeof window.safeT === 'function') {
            return window.safeT(key, fallback);
        }
        if (typeof window.t === 'function') {
            return window.t(key, fallback);
        }
        return fallback;
    }

    function getErrorMessage(error) {
        return error && error.message ? error.message : String(error || '');
    }

    function normalizeModelLabel(modelType) {
        const type = String(modelType || '').toLowerCase();
        if (type === 'vrm') return 'VRM';
        if (type === 'mmd') return 'MMD';
        return 'Live2D';
    }

    function setPreviewVisible(visible) {
        const card = S.dom.chatAvatarPreviewCard;
        const button = S.dom.avatarPreviewButton;
        if (!card || !button) return;
        card.hidden = !visible;
        button.classList.toggle('is-active', visible);
    }

    function setLoadingState(loading) {
        const button = S.dom.avatarPreviewButton;
        const refreshButton = S.dom.chatAvatarPreviewRefreshButton;
        if (button) {
            button.classList.toggle('is-loading', loading);
            button.disabled = loading;
        }
        if (refreshButton) {
            refreshButton.disabled = loading;
        }
    }

    function setPreviewStatus(text) {
        if (S.dom.chatAvatarPreviewStatus) {
            S.dom.chatAvatarPreviewStatus.textContent = text;
        }
    }

    function setPreviewNote(text) {
        if (S.dom.chatAvatarPreviewNote) {
            S.dom.chatAvatarPreviewNote.textContent = text;
        }
    }

    function setPreviewImage(dataUrl) {
        const image = S.dom.chatAvatarPreviewImage;
        const placeholder = S.dom.chatAvatarPreviewPlaceholder;
        const shell = S.dom.chatAvatarPreviewImageShell;
        if (!image || !placeholder || !shell) return;

        if (dataUrl) {
            image.src = dataUrl;
            image.hidden = false;
            placeholder.hidden = true;
            shell.classList.remove('is-empty');
            return;
        }

        image.hidden = true;
        image.removeAttribute('src');
        placeholder.hidden = false;
        shell.classList.add('is-empty');
    }

    function isInlinePreviewAvailable() {
        const textInputArea = S.dom.textInputArea || document.getElementById('text-input-area');
        if (!textInputArea) return false;
        if (textInputArea.classList.contains('hidden')) return false;
        return window.getComputedStyle(textInputArea).display !== 'none';
    }

    function getCurrentModelType() {
        if (typeof window.avatarPortrait?.normalizeModelType === 'function') {
            return window.avatarPortrait.normalizeModelType();
        }
        const modelType = String(window.lanlan_config?.model_type || '').toLowerCase();
        if (modelType === 'live3d') {
            const subType = String(window.lanlan_config?.live3d_sub_type || '').toLowerCase();
            if (subType === 'mmd') return 'mmd';
            if (subType === 'vrm') return 'vrm';
        }
        if (modelType === 'vrm') return 'vrm';
        if (modelType === 'mmd') return 'mmd';
        return 'live2d';
    }

    function getCurrentModelCacheKey() {
        const modelType = getCurrentModelType();
        if (modelType === 'vrm') {
            return 'vrm:' + String(
                window.vrmManager?.currentModel?.url ||
                window.lanlan_config?.vrm ||
                ''
            );
        }
        if (modelType === 'mmd') {
            return 'mmd:' + String(
                window.mmdManager?.currentModel?.url ||
                window.mmdModel ||
                window.lanlan_config?.mmd ||
                ''
            );
        }
        return 'live2d:' + String(
            window.live2dManager?._lastLoadedModelPath ||
            window.cubism4Model ||
            ''
        );
    }

    function hasUsableCachedPreview() {
        return !!(
            cachedPreview &&
            cachedPreview.dataUrl &&
            cachedPreview.cacheKey &&
            cachedPreview.cacheKey === getCurrentModelCacheKey()
        );
    }

    function applyPreviewResult(result, cacheKey) {
        cachedPreview = {
            cacheKey,
            dataUrl: result.dataUrl,
            modelType: result.modelType || getCurrentModelType(),
            capturedAt: Date.now()
        };

        setPreviewImage(cachedPreview.dataUrl);
        setPreviewStatus(
            translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(cachedPreview.modelType)
        );
        setPreviewNote(translateLabel('chat.avatarPreviewReadyHint', '这是从当前模型画布实时提取的头像预览。'));
        window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
            detail: {
                cacheKey: cachedPreview.cacheKey,
                dataUrl: cachedPreview.dataUrl,
                modelType: cachedPreview.modelType,
                capturedAt: cachedPreview.capturedAt
            }
        }));
    }

    function clearCachedPreview() {
        cachedPreview = null;
        lastScheduledCacheKey = '';
        setPreviewImage('');
        setPreviewStatus(translateLabel('chat.avatarPreviewWaiting', '等待当前模型头像缓存生成'));
        setPreviewNote(translateLabel('chat.avatarPreviewCardNote', '将基于当前显示中的 Live2D / VRM / MMD 模型生成头像。'));
        window.dispatchEvent(new CustomEvent('chat-avatar-preview-cleared'));
    }

    async function captureAvatarPreview() {
        if (!window.avatarPortrait || typeof window.avatarPortrait.capture !== 'function') {
            throw new Error(translateLabel('chat.avatarPreviewUnavailable', '头像预览功能尚未就绪。'));
        }

        return window.avatarPortrait.capture({
            width: 320,
            height: 320,
            padding: 0.035,
            shape: 'rounded',
            radius: 40,
            background: 'rgba(255, 255, 255, 0.96)',
            includeDataUrl: true
        });
    }

    async function renderAvatarPreview(options = {}) {
        const forceRefresh = options.forceRefresh === true;
        const showCard = options.showCard !== false;
        const skipInputCheck = options.skipInputCheck === true;
        const silent = options.silent === true;

        if (isCapturing) {
            if (skipInputCheck && silent) {
                pendingAutoCapture = true;
            }
            return;
        }
        if (!skipInputCheck && !isInlinePreviewAvailable()) {
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translateLabel('chat.avatarPreviewInputHidden', '当前输入区已隐藏，请回到文字聊天界面后再查看头像。'),
                    3500
                );
            }
            return;
        }

        if (!forceRefresh && hasUsableCachedPreview()) {
            if (showCard) {
                setPreviewVisible(true);
            }
            setPreviewImage(cachedPreview.dataUrl);
            setPreviewStatus(
                translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(cachedPreview.modelType)
            );
            setPreviewNote(translateLabel('chat.avatarPreviewCachedHint', '已显示当前模型的缓存头像，点击刷新可重新生成。'));
            return;
        }

        isCapturing = true;
        const token = ++activeCaptureToken;
        const cacheKey = getCurrentModelCacheKey();
        if (showCard) {
            setPreviewVisible(true);
        }
        setLoadingState(true);
        setPreviewStatus(forceRefresh
            ? translateLabel('chat.avatarPreviewRefreshing', '正在刷新当前头像...')
            : translateLabel('chat.avatarPreviewGenerating', '正在生成当前头像...'));
        setPreviewNote(translateLabel('chat.avatarPreviewCardNote', '将基于当前显示中的 Live2D / VRM / MMD 模型生成头像。'));

        try {
            const result = await captureAvatarPreview();
            if (token !== activeCaptureToken) return;

            applyPreviewResult(result, cacheKey);
        } catch (error) {
            if (token !== activeCaptureToken) return;

            if (showCard) {
                setPreviewImage('');
                setPreviewStatus(translateLabel('chat.avatarPreviewFailed', '生成头像失败'));
                setPreviewNote(getErrorMessage(error));
            }
            if (!silent && typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translateLabel('chat.avatarPreviewFailed', '生成头像失败') + ': ' + getErrorMessage(error),
                    4500
                );
            }
        } finally {
            if (token === activeCaptureToken) {
                isCapturing = false;
                setLoadingState(false);
                if (pendingAutoCapture) {
                    pendingAutoCapture = false;
                    scheduleAutoCapture('pending-retry');
                }
            }
        }
    }

    function scheduleAutoCapture(reason) {
        const cacheKey = getCurrentModelCacheKey();
        if (!cacheKey || cacheKey.endsWith(':')) {
            return;
        }
        if (hasUsableCachedPreview()) {
            return;
        }
        if (lastScheduledCacheKey === cacheKey && isCapturing) {
            pendingAutoCapture = true;
            return;
        }

        lastScheduledCacheKey = cacheKey;
        if (autoCaptureTimer) {
            clearTimeout(autoCaptureTimer);
        }

        autoCaptureTimer = setTimeout(function () {
            autoCaptureTimer = null;
            window.requestAnimationFrame(function () {
                window.requestAnimationFrame(function () {
                    renderAvatarPreview({
                        forceRefresh: true,
                        silent: true,
                        showCard: false,
                        skipInputCheck: true,
                        reason: reason || 'model-loaded'
                    }).catch(function (error) {
                        console.warn('[app-chat-avatar] 自动缓存头像失败:', error);
                    });
                });
            });
        }, 180);
    }

    function bindModelLoadListeners() {
        const previousOnModelLoaded = window.live2dManager && typeof window.live2dManager.onModelLoaded === 'function'
            ? window.live2dManager.onModelLoaded
            : null;

        if (window.live2dManager) {
            window.live2dManager.onModelLoaded = function (model, modelPath) {
                if (previousOnModelLoaded) {
                    previousOnModelLoaded.call(window.live2dManager, model, modelPath);
                }
                clearCachedPreview();
                scheduleAutoCapture('live2d-model-loaded');
            };
        }

        window.addEventListener('vrm-model-loaded', function () {
            clearCachedPreview();
            scheduleAutoCapture('vrm-model-loaded');
        });

        window.addEventListener('mmd-model-loaded', function () {
            clearCachedPreview();
            scheduleAutoCapture('mmd-model-loaded');
        });
    }

    function handleOutsidePointer(event) {
        const card = S.dom.chatAvatarPreviewCard;
        const button = S.dom.avatarPreviewButton;
        if (!card || card.hidden) return;
        if (card.contains(event.target) || (button && button.contains(event.target))) {
            return;
        }
        setPreviewVisible(false);
    }

    mod.init = function init() {
        S.dom.avatarPreviewButton = document.getElementById('avatarPreviewButton');
        S.dom.chatAvatarPreviewCard = document.getElementById('chat-avatar-preview-card');
        S.dom.chatAvatarPreviewStatus = document.getElementById('chat-avatar-preview-status');
        S.dom.chatAvatarPreviewNote = document.getElementById('chat-avatar-preview-note');
        S.dom.chatAvatarPreviewImageShell = document.getElementById('chat-avatar-preview-image-shell');
        S.dom.chatAvatarPreviewImage = document.getElementById('chat-avatar-preview-image');
        S.dom.chatAvatarPreviewPlaceholder = document.getElementById('chat-avatar-preview-placeholder');
        S.dom.chatAvatarPreviewRefreshButton = document.getElementById('chatAvatarPreviewRefreshButton');
        S.dom.chatAvatarPreviewCloseButton = document.getElementById('chatAvatarPreviewCloseButton');

        const button = S.dom.avatarPreviewButton;
        const refreshButton = S.dom.chatAvatarPreviewRefreshButton;
        const closeButton = S.dom.chatAvatarPreviewCloseButton;

        if (!button || !refreshButton || !closeButton) {
            return;
        }

        button.addEventListener('click', function () {
            renderAvatarPreview();
        });

        refreshButton.addEventListener('click', function () {
            renderAvatarPreview({ forceRefresh: true });
        });

        closeButton.addEventListener('click', function () {
            setPreviewVisible(false);
        });

        document.addEventListener('pointerdown', handleOutsidePointer, true);
        clearCachedPreview();
        bindModelLoadListeners();
        scheduleAutoCapture('init');
    };

    mod.getCachedPreview = function getCachedPreview() {
        return cachedPreview ? {
            cacheKey: cachedPreview.cacheKey,
            dataUrl: cachedPreview.dataUrl,
            modelType: cachedPreview.modelType,
            capturedAt: cachedPreview.capturedAt
        } : null;
    };

    mod.getCurrentAvatarDataUrl = function getCurrentAvatarDataUrl() {
        if (hasUsableCachedPreview()) return cachedPreview.dataUrl || '';
        // 多窗口 fallback：使用 IPC 注入的头像
        if (externalAvatarDataUrl) return externalAvatarDataUrl;
        return '';
    };

    /**
     * 多窗口模式：由 preload / IPC 调用，设置从 Pet 窗口获取的头像
     * @param {string} dataUrl - base64 data URL
     * @param {string} [modelType] - 'live2d' | 'vrm' | 'mmd'
     */
    mod.setExternalAvatar = function setExternalAvatar(dataUrl, modelType) {
        externalAvatarDataUrl = dataUrl || '';
        externalAvatarModelType = modelType || '';
        window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
            detail: { dataUrl: externalAvatarDataUrl, modelType: externalAvatarModelType, source: 'ipc' }
        }));
    };

    mod.getExternalAvatar = function getExternalAvatar() {
        return externalAvatarDataUrl ? { dataUrl: externalAvatarDataUrl, modelType: externalAvatarModelType } : null;
    };

    window.appChatAvatar = mod;
})();
