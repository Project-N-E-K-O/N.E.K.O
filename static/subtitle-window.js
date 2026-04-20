(function() {
    'use strict';

    var SubtitleShared = window.nekoSubtitleShared || null;
    var subtitleWindowController = null;
    var currentTranscript = '';
    var WINDOW_DROPDOWN_HEIGHT = 230;

    if (!SubtitleShared) {
        console.error('[SubtitleWindow] subtitle-shared.js 未加载');
        return;
    }

    function resizeWindowToTranscript() {
        if (!subtitleWindowController || !subtitleWindowController.refs) return;
        var refs = subtitleWindowController.refs;
        var api = window.nekoSubtitle;
        var state = SubtitleShared.getSettings();
        var preset = SubtitleShared.getSizePreset(state.subtitleSize);

        SubtitleShared.applySubtitlePreset(refs.display, state.subtitleSize, { host: 'window' });

        if (!refs.text) return;
        if (!currentTranscript.trim()) {
            refs.text.style.fontSize = '';
            if (api && typeof api.setSize === 'function') {
                api.setSize(preset.width, Math.max(preset.minHeight, WINDOW_DROPDOWN_HEIGHT));
            }
            return;
        }

        var layout = SubtitleShared.measureSubtitleLayout({
            mode: 'window',
            text: currentTranscript,
            presetKey: state.subtitleSize,
            maxWidth: preset.width,
            minHeight: preset.minHeight,
            maxHeight: 280
        });
        refs.text.style.fontSize = layout.fontSize < 17 ? layout.fontSize + 'px' : '';
        if (api && typeof api.setSize === 'function') {
            api.setSize(layout.width, Math.max(layout.height, WINDOW_DROPDOWN_HEIGHT));
        }
    }

    function applyTranscript(text) {
        currentTranscript = String(text || '');
        if (subtitleWindowController && subtitleWindowController.refs && subtitleWindowController.refs.text) {
            subtitleWindowController.refs.text.textContent = currentTranscript;
        }
        resizeWindowToTranscript();
    }

    function applyStateSync(data) {
        var patch = {};

        if (!data) return;
        if (Object.prototype.hasOwnProperty.call(data, 'enabled')) {
            patch.subtitleEnabled = !!data.enabled;
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
        if (Object.prototype.hasOwnProperty.call(data, 'dragAnywhere')) {
            patch.subtitleDragAnywhere = !!data.dragAnywhere;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'size')) {
            patch.subtitleSize = data.size;
        }

        if (Object.keys(patch).length) {
            SubtitleShared.updateSettings(patch, {
                persist: false,
                source: 'subtitle-window-sync'
            });
        }

        if (subtitleWindowController &&
            subtitleWindowController.refs &&
            subtitleWindowController.refs.display &&
            Object.prototype.hasOwnProperty.call(data, 'visible')) {
            subtitleWindowController.refs.display.classList.toggle('hidden', !data.visible);
        }
    }

    document.addEventListener('DOMContentLoaded', function() {
        subtitleWindowController = SubtitleShared.initSubtitleUI({
            host: 'window',
            api: window.nekoSubtitle,
            propagateSetting: function(change) {
                if (!change || !window.nekoSubtitle || typeof window.nekoSubtitle.changeSettings !== 'function') return;
                window.nekoSubtitle.changeSettings({
                    type: change.type,
                    value: change.value
                });
            },
            onSettingsApplied: function(state, refs) {
                SubtitleShared.applySubtitlePreset(refs.display, state.subtitleSize, { host: 'window' });
                resizeWindowToTranscript();
            }
        });

        if (!subtitleWindowController || !subtitleWindowController.refs) {
            return;
        }

        window.addEventListener('neko-subtitle-state-sync', function(e) {
            applyStateSync(e.detail || {});
        });

        window.addEventListener('neko-ws-transcript', function(e) {
            var data = e.detail || {};
            applyTranscript(data.transcript || '');
        });

        if (window.__nekoSubtitleLatestState) {
            applyStateSync(window.__nekoSubtitleLatestState);
        }
        if (window.__nekoSubtitleLatestTranscript) {
            applyTranscript(window.__nekoSubtitleLatestTranscript.transcript || '');
        }

        resizeWindowToTranscript();
    });
})();
