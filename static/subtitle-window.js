(function() {
    'use strict';

    var SubtitleShared = window.nekoSubtitleShared || null;
    var subtitleWindowController = null;
    var currentTranscript = '';

    if (!SubtitleShared) {
        console.error('[SubtitleWindow] subtitle-shared.js 未加载');
        return;
    }

    function resizeWindowToTranscript() {
        if (!subtitleWindowController || !subtitleWindowController.refs) return;
        var refs = subtitleWindowController.refs;
        var api = window.nekoSubtitle;
        var state = SubtitleShared.getSettings();
        var bounds = SubtitleShared.getPanelBounds(state.subtitlePanelBounds);
        var settingsPanelOpen = refs.settingsPanel && !refs.settingsPanel.classList.contains('hidden');
        var panelHeight = settingsPanelOpen && refs.settingsPanel
            ? refs.settingsPanel.offsetHeight
            : 0;
        var panelGap = settingsPanelOpen ? 8 : 0;

        SubtitleShared.applySubtitlePanelBounds(refs.display, bounds, { host: 'window' });
        refs.display.style.maxHeight = 'none';

        if (!refs.text) return;
        if (!currentTranscript.trim()) {
            refs.text.style.fontSize = '';
            if (api && typeof api.setSize === 'function') {
                api.setSize(bounds.width, bounds.height + panelGap + panelHeight);
            }
            return;
        }

        var layout = SubtitleShared.measureSubtitleLayout({
            mode: 'window',
            text: currentTranscript,
            panelBounds: bounds,
            maxWidth: bounds.width,
            minHeight: bounds.height,
            maxHeight: bounds.height,
            baseFont: 17
        });
        refs.text.style.fontSize = layout.fontSize < 17 ? layout.fontSize + 'px' : '';
        if (api && typeof api.setSize === 'function') {
            api.setSize(bounds.width, bounds.height + panelGap + panelHeight);
        }
    }

    function applyTranscript(text) {
        currentTranscript = String(text || '');
        if (subtitleWindowController && subtitleWindowController.refs && subtitleWindowController.refs.text) {
            subtitleWindowController.refs.text.textContent = currentTranscript;
        }
        resizeWindowToTranscript();
    }

    function applyTranslatedTranscript(data) {
        if (!data || data.translated !== true) return;
        applyTranscript(data.transcript || '');
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
        if (Object.prototype.hasOwnProperty.call(data, 'bounds')) {
            patch.subtitlePanelBounds = data.bounds;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitlePanelBounds')) {
            patch.subtitlePanelBounds = data.subtitlePanelBounds;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'locked')) {
            patch.subtitlePanelLocked = !!data.locked;
        } else if (Object.prototype.hasOwnProperty.call(data, 'panelLocked')) {
            patch.subtitlePanelLocked = !!data.panelLocked;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitlePanelLocked')) {
            patch.subtitlePanelLocked = !!data.subtitlePanelLocked;
        }
        if (Object.prototype.hasOwnProperty.call(data, 'interactionPassthrough')) {
            patch.subtitleInteractionPassthrough = data.interactionPassthrough !== false;
        } else if (Object.prototype.hasOwnProperty.call(data, 'subtitleInteractionPassthrough')) {
            patch.subtitleInteractionPassthrough = data.subtitleInteractionPassthrough !== false;
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
        if (/linux/i.test((navigator.platform || '') + ' ' + (navigator.userAgent || ''))) {
            document.body.classList.add('subtitle-linux-host');
        }

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
                SubtitleShared.applySubtitlePanelBounds(refs.display, state.subtitlePanelBounds, { host: 'window' });
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
            applyTranslatedTranscript(data);
        });

        if (window.__nekoSubtitleLatestState) {
            applyStateSync(window.__nekoSubtitleLatestState);
        }
        if (window.__nekoSubtitleLatestTranscript) {
            applyTranslatedTranscript(window.__nekoSubtitleLatestTranscript);
        }

        resizeWindowToTranscript();
    });
})();
