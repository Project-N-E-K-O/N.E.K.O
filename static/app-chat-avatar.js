/**
 * app-chat-avatar.js — 聊天框内的当前头像预览
 * 依赖：avatar-portrait.js
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;

    let isCapturing = false;
    let activeCaptureToken = 0;

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

    async function renderAvatarPreview() {
        if (isCapturing) return;
        if (!isInlinePreviewAvailable()) {
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translateLabel('chat.avatarPreviewInputHidden', '当前输入区已隐藏，请回到文字聊天界面后再查看头像。'),
                    3500
                );
            }
            return;
        }

        isCapturing = true;
        const token = ++activeCaptureToken;
        setPreviewVisible(true);
        setLoadingState(true);
        setPreviewStatus(translateLabel('chat.avatarPreviewGenerating', '正在生成当前头像...'));
        setPreviewNote(translateLabel('chat.avatarPreviewHint', '将基于当前显示中的 Live2D / VRM / MMD 模型生成头像。'));

        try {
            const result = await captureAvatarPreview();
            if (token !== activeCaptureToken) return;

            setPreviewImage(result.dataUrl);
            setPreviewStatus(
                translateLabel('chat.avatarPreviewReady', '头像已更新') + ' · ' + normalizeModelLabel(result.modelType)
            );
            setPreviewNote(translateLabel('chat.avatarPreviewReadyHint', '这是从当前模型画布实时提取的头像预览。'));
        } catch (error) {
            if (token !== activeCaptureToken) return;

            setPreviewImage('');
            setPreviewStatus(translateLabel('chat.avatarPreviewFailed', '生成头像失败'));
            setPreviewNote(getErrorMessage(error));
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast(
                    translateLabel('chat.avatarPreviewFailed', '生成头像失败') + ': ' + getErrorMessage(error),
                    4500
                );
            }
        } finally {
            if (token === activeCaptureToken) {
                isCapturing = false;
                setLoadingState(false);
            }
        }
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
            renderAvatarPreview();
        });

        closeButton.addEventListener('click', function () {
            setPreviewVisible(false);
        });

        document.addEventListener('pointerdown', handleOutsidePointer, true);
    };

    window.appChatAvatar = mod;
})();
