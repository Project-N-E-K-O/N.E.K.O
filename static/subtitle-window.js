(function() {
    'use strict';

    var SubtitleShared = window.nekoSubtitleShared || null;
    var subtitleWindowController = null;
    var currentTranscript = '';
    var WINDOW_PANEL_GAP = 8;
    var WINDOW_PANEL_MARGIN = 8;
    var WINDOW_RESIZE_EPSILON = 2;
    var lastRequestedWindowWidth = 0;
    var lastRequestedWindowHeight = 0;
    var userViewportWidth = 0;
    var userViewportHeight = 0;
    var windowSettingsLayer = null;
    var resizeIdleTimer = 0;

    if (!SubtitleShared) {
        console.error('[SubtitleWindow] subtitle-shared.js 未加载');
        return;
    }

    function ensureWindowSettingsLayer(refs) {
        if (!refs || !refs.settingsPanel || !document.body) return;
        if (!windowSettingsLayer) {
            windowSettingsLayer = document.getElementById('subtitle-settings-layer');
            if (!windowSettingsLayer) {
                windowSettingsLayer = document.createElement('div');
                windowSettingsLayer.id = 'subtitle-settings-layer';
                windowSettingsLayer.className = 'subtitle-window-settings-layer';
                document.body.appendChild(windowSettingsLayer);
            }
        }
        if (refs.settingsPanel.parentElement !== windowSettingsLayer) {
            windowSettingsLayer.appendChild(refs.settingsPanel);
        }
    }

    function resizeWindowToTranscript(options) {
        if (!subtitleWindowController || !subtitleWindowController.refs) return;
        options = options || {};
        var refs = subtitleWindowController.refs;
        var api = window.nekoSubtitle;
        var state = SubtitleShared.getSettings();
        var preset = SubtitleShared.getSizePreset(state.subtitleSize);
        var isLinuxHost = document.body && document.body.classList.contains('subtitle-linux-host');

        ensureWindowSettingsLayer(refs);

        var settingsPanelOpen = refs.settingsPanel && !refs.settingsPanel.classList.contains('hidden');
        var panelRect = settingsPanelOpen && refs.settingsPanel
            ? refs.settingsPanel.getBoundingClientRect()
            : null;
        var panelHeight = panelRect ? Math.ceil(panelRect.height) : 0;
        var panelWidth = panelRect ? Math.ceil(panelRect.width) : 0;
        var panelGap = settingsPanelOpen ? WINDOW_PANEL_GAP : 0;
        var displayWidth = preset.width;
        var displayHeight = preset.minHeight;
        var displayRenderWidth;
        var displayRenderHeight;
        var displayTop;
        var contentWidth;
        var contentHeight;
        var displayLeft;
        var panelLeft;
        var panelTop;

        SubtitleShared.applySubtitlePreset(refs.display, state.subtitleSize, { host: 'window' });
        refs.display.style.maxHeight = preset.maxHeight + 'px';

        if (refs.text && currentTranscript.trim()) {
            var layout = SubtitleShared.measureSubtitleLayout({
                mode: 'window',
                text: currentTranscript,
                presetKey: state.subtitleSize,
                maxWidth: preset.width,
                minHeight: preset.minHeight,
                maxHeight: preset.maxHeight,
                baseFont: preset.fontSize
            });
            displayWidth = layout.width;
            displayHeight = layout.height;
            refs.text.style.fontSize = layout.fontSize < preset.fontSize ? layout.fontSize + 'px' : '';
        } else if (refs.text) {
            refs.text.style.fontSize = '';
        }

        document.body.style.setProperty('--subtitle-window-display-width', displayWidth + 'px');
        document.body.style.removeProperty('--subtitle-window-display-height');
        refs.display.style.removeProperty('--subtitle-content-height');
        var displayRect = refs.display.getBoundingClientRect();
        displayWidth = Math.ceil(Math.max(displayWidth, displayRect.width));
        displayHeight = Math.ceil(Math.max(displayHeight, displayRect.height));

        displayRenderWidth = userViewportWidth
            ? Math.max(displayWidth, userViewportWidth)
            : displayWidth;
        displayRenderHeight = userViewportHeight
            ? Math.max(displayHeight, userViewportHeight)
            : displayHeight;
        displayTop = isLinuxHost && settingsPanelOpen ? panelHeight + panelGap : 0;
        contentWidth = Math.ceil(Math.max(
            displayRenderWidth,
            panelWidth ? panelWidth + WINDOW_PANEL_MARGIN : 0,
            userViewportWidth ? 0 : preset.width
        ));
        contentHeight = Math.ceil(Math.max(
            displayTop + displayRenderHeight,
            settingsPanelOpen && !isLinuxHost ? displayRenderHeight + panelGap + panelHeight : 0,
            panelHeight,
            userViewportHeight ? 0 : preset.minHeight
        )) +
            (settingsPanelOpen ? 1 : 0);
        displayLeft = Math.max(0, Math.round((contentWidth - displayRenderWidth) / 2));
        panelLeft = settingsPanelOpen && panelWidth
            ? Math.max(0, Math.round((contentWidth - panelWidth) / 2))
            : displayLeft;
        panelTop = isLinuxHost && settingsPanelOpen ? 0 : displayTop + displayRenderHeight + panelGap;

        document.body.style.setProperty('--subtitle-window-display-width', displayRenderWidth + 'px');
        if (userViewportHeight) {
            document.body.style.setProperty('--subtitle-window-display-height', displayRenderHeight + 'px');
            refs.display.style.maxHeight = 'none';
            refs.display.style.setProperty('--subtitle-content-height', Math.max(24, displayRenderHeight - 40) + 'px');
            refs.display.style.setProperty('--subtitle-content-max-height', Math.max(24, displayRenderHeight - 40) + 'px');
        }
        document.body.style.setProperty('--subtitle-window-display-left', displayLeft + 'px');
        document.body.style.setProperty(
            '--subtitle-window-display-top',
            displayTop + 'px'
        );
        document.body.style.setProperty('--subtitle-window-panel-left', panelLeft + 'px');
        document.body.style.setProperty(
            '--subtitle-window-panel-top',
            panelTop + 'px'
        );

        if (!options.skipWindowResize && api && typeof api.setSize === 'function') {
            lastRequestedWindowWidth = contentWidth;
            lastRequestedWindowHeight = contentHeight;
            api.setSize(contentWidth, contentHeight);
        }
    }

    function rememberUserViewportSizeFromResize() {
        var viewportWidth = Math.ceil(window.innerWidth || document.documentElement.clientWidth || 0);
        var viewportHeight = Math.ceil(window.innerHeight || document.documentElement.clientHeight || 0);
        var differsFromRequested = !lastRequestedWindowWidth ||
            Math.abs(viewportWidth - lastRequestedWindowWidth) > WINDOW_RESIZE_EPSILON ||
            Math.abs(viewportHeight - lastRequestedWindowHeight) > WINDOW_RESIZE_EPSILON;

        if (differsFromRequested) {
            userViewportWidth = viewportWidth;
            userViewportHeight = viewportHeight;
        }
        if (document.body) {
            document.body.classList.add('subtitle-window-resizing');
            if (resizeIdleTimer) clearTimeout(resizeIdleTimer);
            resizeIdleTimer = setTimeout(function() {
                resizeIdleTimer = 0;
                if (document.body) {
                    document.body.classList.remove('subtitle-window-resizing');
                }
            }, 160);
        }
        resizeWindowToTranscript({ skipWindowResize: true });
    }

    function attachCloseSettingsBeforeContentDrag(refs) {
        if (!refs || !refs.display || !refs.settingsPanel || !refs.settingsBtn) {
            return function() {};
        }

        function shouldCloseForDrag(target) {
            if (refs.settingsPanel.classList.contains('hidden')) return false;
            if (!refs.display.classList.contains('drag-anywhere')) return false;
            if (refs.settingsPanel.contains(target)) return false;
            if (refs.settingsBtn.contains(target)) return false;
            return refs.display.contains(target) || (refs.dragHandle && refs.dragHandle.contains(target));
        }

        function closeForDrag(event) {
            if (!shouldCloseForDrag(event.target)) return;
            refs.settingsPanel.classList.add('hidden');
            resizeWindowToTranscript();
        }

        refs.display.addEventListener('mousedown', closeForDrag, true);
        refs.display.addEventListener('touchstart', closeForDrag, true);
        if (refs.dragHandle) {
            refs.dragHandle.addEventListener('mousedown', closeForDrag, true);
            refs.dragHandle.addEventListener('touchstart', closeForDrag, true);
        }

        return function detachCloseSettingsBeforeContentDrag() {
            refs.display.removeEventListener('mousedown', closeForDrag, true);
            refs.display.removeEventListener('touchstart', closeForDrag, true);
            if (refs.dragHandle) {
                refs.dragHandle.removeEventListener('mousedown', closeForDrag, true);
                refs.dragHandle.removeEventListener('touchstart', closeForDrag, true);
            }
        };
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
            // settings panel 已移到 body 级 settings-layer，不再是 display 的子元素，
            // 需要同步隐藏/显示，否则字幕隐藏时 settings panel 仍可见。
            if (!data.visible && subtitleWindowController.refs.settingsPanel) {
                subtitleWindowController.refs.settingsPanel.classList.add('hidden');
            } else if (data.visible && subtitleWindowController.refs.settingsPanel) {
                // 不主动移除 hidden —— 面板的显示状态应由用户操作控制，
                // 只在字幕恢复时确保 settings-layer 容器本身可见
                if (windowSettingsLayer) {
                    windowSettingsLayer.classList.remove('hidden');
                }
            }
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
                SubtitleShared.applySubtitlePreset(refs.display, state.subtitleSize, { host: 'window' });
                resizeWindowToTranscript();
            }
        });

        if (!subtitleWindowController || !subtitleWindowController.refs) {
            return;
        }

        attachCloseSettingsBeforeContentDrag(subtitleWindowController.refs);

        window.addEventListener('resize', rememberUserViewportSizeFromResize);

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
