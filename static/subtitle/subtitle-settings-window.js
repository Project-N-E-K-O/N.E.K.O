(function() {
    'use strict';

    var SubtitleShared = window.nekoSubtitleShared || null;
    var OPACITY_ACK_TIMEOUT_MS = 1000;
    var pendingOpacity = null;
    var pendingOpacityTimer = null;

    if (!SubtitleShared) {
        console.error('[SubtitleSettingsWindow] subtitle-shared.js 未加载');
        return;
    }

    function normalizeOpacity(value) {
        var numeric = Math.round(Number(value));
        if (!isFinite(numeric)) numeric = 95;
        return Math.max(0, Math.min(100, numeric));
    }

    function clearPendingOpacity() {
        pendingOpacity = null;
        if (pendingOpacityTimer !== null) {
            window.clearTimeout(pendingOpacityTimer);
            pendingOpacityTimer = null;
        }
    }

    function rememberPendingOpacity(value) {
        pendingOpacity = normalizeOpacity(value);
        if (pendingOpacityTimer !== null) {
            window.clearTimeout(pendingOpacityTimer);
        }
        pendingOpacityTimer = window.setTimeout(clearPendingOpacity, OPACITY_ACK_TIMEOUT_MS);
    }

    function shouldApplyIncomingOpacity(value) {
        if (pendingOpacity === null) return true;
        if (normalizeOpacity(value) !== pendingOpacity) return false;
        clearPendingOpacity();
        return true;
    }

    function propagateSubtitleSetting(change) {
        if (!change || !window.nekoSubtitle || typeof window.nekoSubtitle.changeSettings !== 'function') return;
        if (change.type === 'opacity') {
            rememberPendingOpacity(change.value);
        }
        window.nekoSubtitle.changeSettings({
            type: change.type,
            value: change.value
        });
    }

    function applyIncomingState(data) {
        if (!data || typeof data !== 'object') return;
        var patch = {};
        if (data.type === 'fontSize') {
            patch.subtitleFontSize = data.value;
        } else if (data.type === 'colorScheme') {
            patch.subtitleColorScheme = data.value;
        } else if (data.type === 'danmakuMode') {
            patch.subtitleDanmakuMode = !!data.value;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'language')) {
            patch.userLanguage = data.language;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'locale')) {
            patch.uiLocale = data.locale;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'opacity')) {
            patch.subtitleOpacity = data.opacity;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'fontSize')) {
            patch.subtitleFontSize = data.fontSize;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitleFontSize')) {
            patch.subtitleFontSize = data.subtitleFontSize;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'colorScheme')) {
            patch.subtitleColorScheme = data.colorScheme;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitleColorScheme')) {
            patch.subtitleColorScheme = data.subtitleColorScheme;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'locked')) {
            patch.subtitlePanelLocked = !!data.locked;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitlePanelLocked')) {
            patch.subtitlePanelLocked = !!data.subtitlePanelLocked;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'interactionPassthrough')) {
            patch.subtitleInteractionPassthrough = data.interactionPassthrough !== false;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitleInteractionPassthrough')) {
            patch.subtitleInteractionPassthrough = data.subtitleInteractionPassthrough !== false;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'danmakuMode')) {
            patch.subtitleDanmakuMode = !!data.danmakuMode;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitleDanmakuMode')) {
            patch.subtitleDanmakuMode = !!data.subtitleDanmakuMode;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'userLanguage')) {
            patch.userLanguage = data.userLanguage;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'uiLocale')) {
            patch.uiLocale = data.uiLocale;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'subtitleOpacity')) {
            patch.subtitleOpacity = data.subtitleOpacity;
        }
        if (Object.prototype.hasOwnProperty.call(patch, 'subtitleOpacity') &&
            !shouldApplyIncomingOpacity(patch.subtitleOpacity)) {
            delete patch.subtitleOpacity;
        }
        if (!Object.keys(patch).length) return;
        SubtitleShared.updateSettings(patch, {
            persist: false,
            source: 'subtitle-settings-window-sync'
        });
    }

    document.addEventListener('DOMContentLoaded', function() {
        SubtitleShared.initSubtitleUI({
            host: 'settings-window',
            windowInteractions: 'external',
            propagateSetting: propagateSubtitleSetting
        });

        window.addEventListener('neko-subtitle-state-sync', function(e) {
            applyIncomingState(e.detail || {});
        });

        if (window.__nekoSubtitleLatestState) {
            applyIncomingState(window.__nekoSubtitleLatestState);
        }
    });
})();
