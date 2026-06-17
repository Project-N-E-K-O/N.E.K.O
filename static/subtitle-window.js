(function() {
    'use strict';

    var SubtitleShared = window.nekoSubtitleShared || null;
    var subtitleWindowController = null;
    var currentTranscript = '';
    var lastSizePayload = null;
    var interactionPollTimer = null;
    var nativeInteractionIgnored = false;
    var interactionPollInFlight = false;
    var desktopWindowInteractionsCleanup = null;
    var INTERACTION_PASSTHROUGH_POLL_MS = 16;
    var DESKTOP_WINDOW_EDGE_INSET = 6;
    var DESKTOP_RESIZE_HIT_ZONE = 10;
    var DESKTOP_MIN_PANEL_WIDTH = 48;
    var DESKTOP_MIN_PANEL_HEIGHT = 28;
    var activeNativeResizeState = null;

    if (!SubtitleShared) {
        console.error('[SubtitleWindow] subtitle-shared.js 未加载');
        return;
    }

    function isNativeWindowResizing(refs) {
        return !!(refs && refs.display && refs.display.dataset.subtitleNativeResizing === 'true');
    }

    function getRenderedPanelBounds(refs, fallbackBounds) {
        if (!refs || !refs.display || !isNativeWindowResizing(refs)) {
            return fallbackBounds;
        }
        var rect = refs.display.getBoundingClientRect ? refs.display.getBoundingClientRect() : null;
        return SubtitleShared.getPanelBounds({
            width: rect && rect.width ? rect.width : window.innerWidth,
            height: rect && rect.height ? rect.height : window.innerHeight
        });
    }

    function resizeWindowToTranscript() {
        if (!subtitleWindowController || !subtitleWindowController.refs) return;
        var refs = subtitleWindowController.refs;
        var api = window.nekoSubtitle;
        var state = SubtitleShared.getSettings();
        var bounds = SubtitleShared.getPanelBounds(state.subtitlePanelBounds);
        var nativeResizing = isNativeWindowResizing(refs);

        if (!nativeResizing) {
            SubtitleShared.applySubtitlePanelBounds(refs.display, bounds, { host: 'window' });
        }
        bounds = getRenderedPanelBounds(refs, bounds);
        refs.display.style.maxHeight = 'none';

        function getInlineSettingsHeightReserve() {
            if (hasExternalSettingsBridge()) return 0;
            if (!refs.settingsPanel || refs.settingsPanel.classList.contains('hidden')) return 0;
            var panelRect = refs.settingsPanel.getBoundingClientRect ? refs.settingsPanel.getBoundingClientRect() : null;
            var panelHeight = panelRect && panelRect.height ? panelRect.height : refs.settingsPanel.offsetHeight;
            return Math.max(0, Math.ceil(Number(panelHeight) || 0) + 8);
        }

        function setWindowSizeOnce() {
            if (nativeResizing || !api || typeof api.setSize !== 'function') return;
            var inlineSettingsHeight = getInlineSettingsHeightReserve();
            var payload = {
                width: bounds.width + DESKTOP_WINDOW_EDGE_INSET * 2,
                height: bounds.height + inlineSettingsHeight + DESKTOP_WINDOW_EDGE_INSET * 2,
                panelWidth: bounds.width,
                panelHeight: bounds.height,
                inlineSettingsHeight: inlineSettingsHeight
            };
            if (lastSizePayload &&
                lastSizePayload.width === payload.width &&
                lastSizePayload.height === payload.height &&
                lastSizePayload.panelWidth === payload.panelWidth &&
                lastSizePayload.panelHeight === payload.panelHeight &&
                lastSizePayload.inlineSettingsHeight === payload.inlineSettingsHeight) {
                return;
            }
            lastSizePayload = payload;
            api.setSize(payload.width, payload.height, {
                panelBounds: bounds
            });
        }

        if (!refs.text) return;
        if (!currentTranscript.trim()) {
            refs.text.style.fontSize = '';
            setWindowSizeOnce();
            return;
        }

        refs.text.style.fontSize = '';
        setWindowSizeOnce();
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

    function isDisplayVisible() {
        var refs = subtitleWindowController && subtitleWindowController.refs;
        return !!(refs && refs.display && !refs.display.classList.contains('hidden'));
    }

    function normalizeRect(rect) {
        if (!rect) return null;
        var left = Number(rect.left);
        var top = Number(rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0) {
            return null;
        }
        return {
            left: left,
            top: top,
            right: left + width,
            bottom: top + height
        };
    }

    function inflateRect(rect, padding) {
        var normalized = normalizeRect(rect);
        var grow = Math.max(0, Number(padding) || 0);
        if (!normalized) return null;
        return {
            left: normalized.left - grow,
            top: normalized.top - grow,
            width: (normalized.right - normalized.left) + grow * 2,
            height: (normalized.bottom - normalized.top) + grow * 2
        };
    }

    function isVisibleElement(el) {
        if (!el || !el.getBoundingClientRect) return false;
        if (el.classList && el.classList.contains('hidden')) return false;
        var style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none')) {
            return false;
        }
        var rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    }

    function pushElementRect(rects, el, padding) {
        if (!isVisibleElement(el)) return;
        var rect = inflateRect(el.getBoundingClientRect(), padding);
        if (rect) rects.push(rect);
    }

    function getDesktopResizeHitZone(rect) {
        var shortest = Math.min(Number(rect && rect.width) || 0, Number(rect && rect.height) || 0);
        if (shortest <= 0) return DESKTOP_RESIZE_HIT_ZONE;
        return Math.max(6, Math.min(DESKTOP_RESIZE_HIT_ZONE, Math.floor(shortest / 3)));
    }

    function pushDesktopResizeHitRects(rects, display) {
        if (!display || !display.getBoundingClientRect) return;
        var rect = display.getBoundingClientRect();
        if (!(rect.width > 0 && rect.height > 0)) return;
        var zone = getDesktopResizeHitZone(rect);
        rects.push({ left: rect.left, top: rect.top, width: rect.width, height: zone });
        rects.push({ left: rect.left, top: rect.bottom - zone, width: rect.width, height: zone });
        rects.push({ left: rect.left, top: rect.top, width: zone, height: rect.height });
        rects.push({ left: rect.right - zone, top: rect.top, width: zone, height: rect.height });
    }

    function collectInteractiveRects() {
        var refs = subtitleWindowController && subtitleWindowController.refs;
        var rects = [];
        if (!refs || !refs.display || refs.display.classList.contains('hidden')) return rects;

        pushElementRect(rects, refs.text, 10);

        if (refs.display.dataset.subtitlePanelState === 'controls' ||
            refs.display.dataset.subtitlePanelState === 'settings') {
            pushElementRect(rects, refs.panelControls, 8);
            pushElementRect(rects, refs.settingsBtn, 8);
            pushElementRect(rects, refs.lockBtn, 8);
            pushElementRect(rects, refs.closeBtn, 8);
        }

        if (refs.settingsPanel && !refs.settingsPanel.classList.contains('hidden')) {
            pushElementRect(rects, refs.settingsPanel, 8);
        }

        if (!SubtitleShared.getSettings().subtitlePanelLocked &&
            refs.resizeHandles && refs.resizeHandles.length) {
            pushDesktopResizeHitRects(rects, refs.display);
            refs.resizeHandles.forEach(function(handle) {
                pushElementRect(rects, handle, 8);
            });
        }

        return rects;
    }

    function isPointInRect(x, y, rect) {
        var normalized = normalizeRect(rect);
        return !!(normalized &&
            x >= normalized.left && x < normalized.right &&
            y >= normalized.top && y < normalized.bottom);
    }

    function isPointInRects(x, y, rects) {
        return (Array.isArray(rects) ? rects : []).some(function(rect) {
            return isPointInRect(x, y, rect);
        });
    }

    function cursorPointToPagePoint(point, bounds) {
        if (!point) return null;
        var screenX = Number(point.screenX);
        var screenY = Number(point.screenY);
        if (bounds && Number.isFinite(screenX) && Number.isFinite(screenY)) {
            return {
                x: screenX - Number(bounds.x || 0),
                y: screenY - Number(bounds.y || 0)
            };
        }
        var x = Number(point.x);
        var y = Number(point.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        return { x: x, y: y };
    }

    function shouldIgnoreAtPoint(point, bounds) {
        var pagePoint = cursorPointToPagePoint(point, bounds);
        if (!pagePoint) return false;
        var width = bounds && Number.isFinite(Number(bounds.width)) ? Number(bounds.width) : window.innerWidth;
        var height = bounds && Number.isFinite(Number(bounds.height)) ? Number(bounds.height) : window.innerHeight;
        if (pagePoint.x < 0 || pagePoint.y < 0 || pagePoint.x >= width || pagePoint.y >= height) {
            return true;
        }
        return !isPointInRects(pagePoint.x, pagePoint.y, collectInteractiveRects());
    }

    function setNativeInteractionIgnored(ignored) {
        var api = window.nekoSubtitle;
        var nextIgnored = !!ignored;
        if (nativeInteractionIgnored === nextIgnored) return;
        nativeInteractionIgnored = nextIgnored;
        if (!api) return;
        if (nextIgnored && typeof api.disableInteraction === 'function') {
            api.disableInteraction();
        } else if (!nextIgnored && typeof api.enableInteraction === 'function') {
            api.enableInteraction();
        }
    }

    function stopInteractionPoll() {
        if (!interactionPollTimer) return;
        clearInterval(interactionPollTimer);
        interactionPollTimer = null;
    }

    function shouldUseNativePassthrough() {
        var refs = subtitleWindowController && subtitleWindowController.refs;
        var state = SubtitleShared.getSettings();
        if (!state.subtitleInteractionPassthrough) return false;
        if (!isDisplayVisible()) return false;
        if (refs && refs.display && refs.display.classList.contains('dragging')) return false;
        if (refs && refs.display && refs.display.classList.contains('resizing')) return false;
        return true;
    }

    function updateNativeInteractionPassthrough() {
        var api = window.nekoSubtitle;
        if (!api || typeof api.getBounds !== 'function' || typeof api.getCursorPoint !== 'function') {
            return;
        }
        if (!shouldUseNativePassthrough()) {
            stopInteractionPoll();
            setNativeInteractionIgnored(false);
            return;
        }
        if (!interactionPollTimer) {
            interactionPollTimer = setInterval(updateNativeInteractionPassthrough, INTERACTION_PASSTHROUGH_POLL_MS);
        }
        if (interactionPollInFlight) return;
        interactionPollInFlight = true;
        Promise.all([api.getBounds(), api.getCursorPoint()]).then(function(values) {
            if (!shouldUseNativePassthrough()) {
                setNativeInteractionIgnored(false);
                return;
            }
            setNativeInteractionIgnored(shouldIgnoreAtPoint(values[1], values[0]));
        }).catch(function() {
            setNativeInteractionIgnored(false);
        }).finally(function() {
            interactionPollInFlight = false;
        });
    }

    function propagateSubtitleSetting(change) {
        if (!change || !window.nekoSubtitle || typeof window.nekoSubtitle.changeSettings !== 'function') return;
        window.nekoSubtitle.changeSettings({
            type: change.type,
            value: change.value
        });
        syncExternalSettingsWindow();
    }

    function syncExternalSettingsWindow() {
        var api = window.nekoSubtitle;
        if (!api || typeof api.updateSettingsWindow !== 'function') return;
        api.updateSettingsWindow(SubtitleShared.getSettings());
    }

    function hasExternalSettingsBridge() {
        var api = window.nekoSubtitle;
        return !!(api &&
            typeof api.openSettings === 'function' &&
            typeof api.closeSettings === 'function');
    }

    function openExternalSettingsWindow() {
        var api = window.nekoSubtitle;
        if (!api || typeof api.openSettings !== 'function') return;
        var refs = subtitleWindowController && subtitleWindowController.refs;
        var rect = refs && refs.display && refs.display.getBoundingClientRect
            ? refs.display.getBoundingClientRect()
            : null;
        api.openSettings({
            state: SubtitleShared.getSettings(),
            anchor: rect ? {
                screenX: Math.round(window.screenX + rect.left),
                screenY: Math.round(window.screenY + rect.top),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            } : null
        });
    }

    function closeExternalSettingsWindow() {
        var api = window.nekoSubtitle;
        if (!api || typeof api.closeSettings !== 'function') return;
        api.closeSettings();
    }

    function getResizeDirectionFromTarget(target, display) {
        if (!target || !display) return '';
        if (target.closest) {
            var edge = target.closest('[data-resize-dir]');
            if (edge && display.contains(edge) && edge.dataset) {
                return edge.dataset.resizeDir || '';
            }
        }
        return '';
    }

    function isEventInsideSubtitlePanel(refs, e) {
        if (!refs || !refs.display || !refs.display.getBoundingClientRect || !e) return false;
        var rect = refs.display.getBoundingClientRect();
        return e.clientX >= rect.left && e.clientX <= rect.right &&
            e.clientY >= rect.top && e.clientY <= rect.bottom;
    }

    function isDesktopInteractiveTarget(target, refs) {
        if (!target) return false;
        if (refs.settingsPanel && refs.settingsPanel.contains(target)) return true;
        if (refs.panelControls && refs.panelControls.contains(target)) return true;
        if (refs.settingsBtn && refs.settingsBtn.contains(target)) return true;
        if (target.closest && target.closest('button,input,select,textarea,a,label')) return true;
        return false;
    }

    function resolveDesktopResizeDirection(refs, e) {
        if (!refs || !e || SubtitleShared.getSettings().subtitlePanelLocked) return '';
        if (isDesktopInteractiveTarget(e.target, refs)) return '';
        return getResizeDirectionFromTarget(e.target, refs.display);
    }

    function getResizeCursor(dir) {
        if (dir === 'n' || dir === 's') return 'ns-resize';
        if (dir === 'e' || dir === 'w') return 'ew-resize';
        if (dir === 'ne' || dir === 'sw') return 'nesw-resize';
        return 'nwse-resize';
    }

    function attachDesktopWindowInteractions(controller) {
        var refs = controller && controller.refs ? controller.refs : controller;
        var api = window.nekoSubtitle;
        if (!refs || !refs.display || !api) return function() {};

        var resizeActive = false;
        var dragActive = false;
        var pendingDrag = null;
        var suppressNextClick = false;

        function isPanelLocked() {
            return !!SubtitleShared.getSettings().subtitlePanelLocked;
        }

        function closeSettingsForNativeInteraction() {
            var inlineSettingsOpen = !!(refs.settingsPanel && !refs.settingsPanel.classList.contains('hidden'));
            if (controller && typeof controller.closeSettingsForExternalInteraction === 'function') {
                controller.closeSettingsForExternalInteraction('controls');
            }
            if (!inlineSettingsOpen) return false;
            refs.settingsPanel.classList.add('hidden');
            refs.display.dataset.subtitlePanelState = 'controls';
            if (refs.panelControls) {
                refs.panelControls.setAttribute('aria-hidden', 'false');
            }
            if (refs.settingsBtn) {
                refs.settingsBtn.setAttribute('aria-expanded', 'false');
            }
            SubtitleShared.updateRenderState({ subtitlePanelState: 'controls' }, {
                source: 'subtitle-window-resize-close-settings'
            });
            return true;
        }

        function getViewportPanelBounds() {
            return SubtitleShared.getPanelBounds({
                width: Math.max(DESKTOP_MIN_PANEL_WIDTH, window.innerWidth - DESKTOP_WINDOW_EDGE_INSET * 2),
                height: Math.max(DESKTOP_MIN_PANEL_HEIGHT, window.innerHeight - DESKTOP_WINDOW_EDGE_INSET * 2)
            });
        }

        function getCurrentDisplayPanelBounds() {
            var rect = refs.display && refs.display.getBoundingClientRect
                ? refs.display.getBoundingClientRect()
                : null;
            return SubtitleShared.getPanelBounds({
                width: Math.max(DESKTOP_MIN_PANEL_WIDTH, rect && rect.width ? rect.width : DESKTOP_MIN_PANEL_WIDTH),
                height: Math.max(DESKTOP_MIN_PANEL_HEIGHT, rect && rect.height ? rect.height : DESKTOP_MIN_PANEL_HEIGHT)
            });
        }

        function applyNativePanelBounds(bounds) {
            refs.display.style.setProperty('--subtitle-native-resize-width', bounds.width + 'px');
            refs.display.style.setProperty('--subtitle-native-resize-height', bounds.height + 'px');
            refs.display.style.setProperty('--subtitle-panel-width', bounds.width + 'px');
            refs.display.style.setProperty('--subtitle-panel-height', bounds.height + 'px');
            refs.display.style.setProperty('--subtitle-content-max-height', Math.max(24, bounds.height - 24) + 'px');
            return bounds;
        }

        function applyViewportPanelBounds() {
            return applyNativePanelBounds(getViewportPanelBounds());
        }

        function restoreSubtitleOnlyWindowSizeForResize(bounds) {
            if (!api || typeof api.setSize !== 'function') return;
            var nextBounds = SubtitleShared.getPanelBounds(bounds || getCurrentDisplayPanelBounds());
            var payload = {
                width: nextBounds.width + DESKTOP_WINDOW_EDGE_INSET * 2,
                height: nextBounds.height + DESKTOP_WINDOW_EDGE_INSET * 2,
                panelWidth: nextBounds.width,
                panelHeight: nextBounds.height
            };
            lastSizePayload = payload;
            api.setSize(payload.width, payload.height, {
                panelBounds: nextBounds
            });
        }

        function canStartDrag(target, e) {
            if (resizeActive || dragActive || isPanelLocked()) return false;
            if (isDesktopInteractiveTarget(target, refs)) return false;
            if (!isEventInsideSubtitlePanel(refs, e)) return false;
            return true;
        }

        function clearResizeState() {
            resizeActive = false;
            activeNativeResizeState = null;
            refs.display.classList.remove('resizing');
            delete refs.display.dataset.subtitleNativeResizing;
            refs.display.style.removeProperty('--subtitle-native-resize-width');
            refs.display.style.removeProperty('--subtitle-native-resize-height');
            refs.display.style.transition = '';
            document.documentElement.classList.remove('neko-resizing');
            document.body.style.userSelect = '';
            document.body.style.cursor = '';
            document.removeEventListener('mousemove', onNativeResizeMove);
            document.removeEventListener('mouseup', endResize);
            document.removeEventListener('touchmove', onNativeTouchResizeMove, { passive: false });
            document.removeEventListener('touchend', endResize);
            document.removeEventListener('touchcancel', endResize);
        }

        function onNativeResizeMove(e) {
            if (!resizeActive || !activeNativeResizeState) return;
            pushNativeResizeCursor(e);
            if (e.preventDefault) e.preventDefault();
        }

        function onNativeTouchResizeMove(e) {
            if (!resizeActive || !activeNativeResizeState || !e.touches || !e.touches.length) return;
            pushNativeResizeCursor(e.touches[0]);
            if (e.preventDefault) e.preventDefault();
        }

        function getEventScreenPoint(e) {
            if (!e) return null;
            var screenX = Number(e.screenX);
            var screenY = Number(e.screenY);
            if (Number.isFinite(screenX) && Number.isFinite(screenY)) {
                return { x: screenX, y: screenY };
            }
            var clientX = Number(e.clientX);
            var clientY = Number(e.clientY);
            if (Number.isFinite(clientX) && Number.isFinite(clientY)) {
                return { x: clientX, y: clientY };
            }
            return null;
        }

        function pushNativeResizeCursor(e) {
            if (!api || typeof api.resizeMove !== 'function') return;
            var point = getEventScreenPoint(e);
            if (point) api.resizeMove(point);
        }

        function beginResize(e, dir) {
            if (!dir || resizeActive || isPanelLocked()) return;
            if (!api || typeof api.resizeStart !== 'function' || typeof api.resizeStop !== 'function') return;
            var startPanelBounds = getCurrentDisplayPanelBounds();
            var closedSettings = closeSettingsForNativeInteraction();
            if (closedSettings) {
                restoreSubtitleOnlyWindowSizeForResize(startPanelBounds);
            }
            resizeActive = true;
            setNativeInteractionIgnored(false);
            refs.display.classList.remove('dragging');
            refs.display.classList.add('resizing');
            refs.display.dataset.subtitleNativeResizing = 'true';
            refs.display.style.transition = 'none';
            document.documentElement.classList.add('neko-resizing');
            document.body.style.userSelect = 'none';
            document.body.style.cursor = getResizeCursor(dir);
            if (e.preventDefault) e.preventDefault();
            if (e.stopImmediatePropagation) e.stopImmediatePropagation();
            if (e.stopPropagation) e.stopPropagation();
            activeNativeResizeState = { dir: dir };
            applyNativePanelBounds(startPanelBounds);
            api.resizeStart(dir, {
                minWidth: DESKTOP_MIN_PANEL_WIDTH + DESKTOP_WINDOW_EDGE_INSET * 2,
                minHeight: DESKTOP_MIN_PANEL_HEIGHT + DESKTOP_WINDOW_EDGE_INSET * 2,
                cursor: getEventScreenPoint(e)
            });
            document.addEventListener('mousemove', onNativeResizeMove);
            document.addEventListener('mouseup', endResize);
            document.addEventListener('touchmove', onNativeTouchResizeMove, { passive: false });
            document.addEventListener('touchend', endResize);
            document.addEventListener('touchcancel', endResize);
            updateNativeInteractionPassthrough();
        }

        function endResize(e) {
            if (!resizeActive) return;
            if (e && e.preventDefault) e.preventDefault();
            if (api && typeof api.resizeStop === 'function') {
                api.resizeStop();
            }
            requestAnimationFrame(function() {
                requestAnimationFrame(function() {
                    if (!resizeActive) return;
                    var nextBounds = applyViewportPanelBounds();
                    SubtitleShared.applySubtitlePanelBounds(refs.display, nextBounds, { host: 'window' });
                    var nextState = SubtitleShared.updateSettings({ subtitlePanelBounds: nextBounds }, {
                        source: 'subtitle-window-native-resize'
                    });
                    propagateSubtitleSetting({
                        type: 'bounds',
                        value: nextBounds,
                        patch: { subtitlePanelBounds: nextBounds },
                        state: nextState
                    });
                    clearResizeState();
                });
            });
        }

        function clearPendingDrag() {
            pendingDrag = null;
            document.removeEventListener('mousemove', onPendingDragMove);
            document.removeEventListener('mouseup', cancelPendingDrag);
            document.removeEventListener('touchmove', onPendingTouchMove, { passive: false });
            document.removeEventListener('touchend', cancelPendingDrag);
            document.removeEventListener('touchcancel', cancelPendingDrag);
        }

        function beginDrag(e) {
            if (!pendingDrag && !canStartDrag(e.target, e)) return;
            if (controller && typeof controller.closeSettingsForExternalInteraction === 'function') {
                controller.closeSettingsForExternalInteraction('controls');
            }
            dragActive = true;
            suppressNextClick = true;
            setNativeInteractionIgnored(false);
            refs.display.classList.add('dragging');
            document.body.style.userSelect = 'none';
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
            if (typeof api.dragStart === 'function') {
                api.dragStart();
            }
            clearPendingDrag();
            document.addEventListener('mouseup', endDrag);
            document.addEventListener('touchend', endDrag);
            document.addEventListener('touchcancel', endDrag);
            updateNativeInteractionPassthrough();
        }

        function endDrag() {
            if (!dragActive) return;
            dragActive = false;
            refs.display.classList.remove('dragging');
            document.body.style.userSelect = '';
            document.removeEventListener('mouseup', endDrag);
            document.removeEventListener('touchend', endDrag);
            document.removeEventListener('touchcancel', endDrag);
            if (api && typeof api.dragStop === 'function') {
                api.dragStop();
            }
            updateNativeInteractionPassthrough();
        }

        function queuePendingDrag(e) {
            if (!canStartDrag(e.target, e)) return;
            pendingDrag = {
                target: e.target,
                startX: e.clientX,
                startY: e.clientY
            };
            document.addEventListener('mousemove', onPendingDragMove);
            document.addEventListener('mouseup', cancelPendingDrag);
        }

        function queuePendingTouchDrag(e, touch) {
            var synthetic = {
                target: e.target,
                clientX: touch.clientX,
                clientY: touch.clientY
            };
            if (!canStartDrag(e.target, synthetic)) return;
            pendingDrag = {
                target: e.target,
                startX: touch.clientX,
                startY: touch.clientY
            };
            document.addEventListener('touchmove', onPendingTouchMove, { passive: false });
            document.addEventListener('touchend', cancelPendingDrag);
            document.addEventListener('touchcancel', cancelPendingDrag);
        }

        function maybeStartPendingDrag(e) {
            if (!pendingDrag || dragActive || resizeActive) return;
            var dx = e.clientX - pendingDrag.startX;
            var dy = e.clientY - pendingDrag.startY;
            if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
            beginDrag({
                target: pendingDrag.target,
                button: 0,
                clientX: e.clientX,
                clientY: e.clientY,
                preventDefault: function() {
                    if (e.preventDefault) e.preventDefault();
                },
                stopPropagation: function() {
                    if (e.stopPropagation) e.stopPropagation();
                }
            });
        }

        function onPendingDragMove(e) {
            maybeStartPendingDrag(e);
        }

        function onPendingTouchMove(e) {
            if (!e.touches || !e.touches.length) return;
            var touch = e.touches[0];
            maybeStartPendingDrag({
                target: e.target,
                clientX: touch.clientX,
                clientY: touch.clientY,
                preventDefault: function() { e.preventDefault(); },
                stopPropagation: function() { e.stopPropagation(); }
            });
        }

        function cancelPendingDrag() {
            clearPendingDrag();
        }

        function onSuppressClick(e) {
            if (!suppressNextClick) return;
            suppressNextClick = false;
            if (e.preventDefault) e.preventDefault();
            if (e.stopImmediatePropagation) e.stopImmediatePropagation();
            if (e.stopPropagation) e.stopPropagation();
        }

        function onPointerDown(e) {
            if (typeof e.button === 'number' && e.button !== 0) return;
            var dir = resolveDesktopResizeDirection(refs, e);
            if (dir) {
                beginResize(e, dir);
                return;
            }
            queuePendingDrag(e);
        }

        function onTouchStart(e) {
            if (!e.touches || !e.touches.length) return;
            var touch = e.touches[0];
            var synthetic = {
                target: e.target,
                button: 0,
                clientX: touch.clientX,
                clientY: touch.clientY,
                preventDefault: function() { e.preventDefault(); },
                stopPropagation: function() { e.stopPropagation(); },
                stopImmediatePropagation: function() {
                    if (e.stopImmediatePropagation) e.stopImmediatePropagation();
                }
            };
            var dir = resolveDesktopResizeDirection(refs, synthetic);
            if (dir) {
                beginResize(synthetic, dir);
                return;
            }
            queuePendingTouchDrag(e, touch);
        }

        refs.display.addEventListener('mousedown', onPointerDown, true);
        refs.display.addEventListener('touchstart', onTouchStart, { passive: false, capture: true });
        document.addEventListener('click', onSuppressClick, true);
        function onNativeWindowResize() {
            if (resizeActive && activeNativeResizeState) {
                applyViewportPanelBounds();
            }
        }

        window.addEventListener('resize', onNativeWindowResize);

        return function detachDesktopWindowInteractions() {
            refs.display.removeEventListener('mousedown', onPointerDown, true);
            refs.display.removeEventListener('touchstart', onTouchStart, { passive: false, capture: true });
            document.removeEventListener('click', onSuppressClick, true);
            window.removeEventListener('resize', onNativeWindowResize);
            clearPendingDrag();
            if (resizeActive) {
                if (api && typeof api.resizeStop === 'function') api.resizeStop();
                clearResizeState();
            }
            if (dragActive) {
                endDrag();
            }
        };
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
            syncExternalSettingsWindow();
        }

        if (subtitleWindowController &&
            subtitleWindowController.refs &&
            subtitleWindowController.refs.display &&
            Object.prototype.hasOwnProperty.call(data, 'visible')) {
            subtitleWindowController.refs.display.classList.toggle('hidden', !data.visible);
        }
        updateNativeInteractionPassthrough();
    }

    document.addEventListener('DOMContentLoaded', function() {
        if (/linux/i.test((navigator.platform || '') + ' ' + (navigator.userAgent || ''))) {
            document.body.classList.add('subtitle-linux-host');
        }

        var uiOptions = {
            host: 'window',
            api: window.nekoSubtitle,
            windowEdgeInset: DESKTOP_WINDOW_EDGE_INSET,
            propagateSetting: propagateSubtitleSetting,
            onSettingsApplied: function(state, refs, detail) {
                var changedKeys = detail && Array.isArray(detail.changedKeys) ? detail.changedKeys : [];
                var panelStateOnly = changedKeys.length === 1 && changedKeys[0] === 'subtitlePanelState';
                SubtitleShared.applySubtitlePanelBounds(refs.display, state.subtitlePanelBounds, { host: 'window' });
                if (!panelStateOnly || !hasExternalSettingsBridge()) {
                    resizeWindowToTranscript();
                }
                syncExternalSettingsWindow();
                updateNativeInteractionPassthrough();
            }
        };
        if (hasExternalSettingsBridge()) {
            uiOptions.windowInteractions = 'external';
            uiOptions.openExternalSettings = openExternalSettingsWindow;
            uiOptions.closeExternalSettings = closeExternalSettingsWindow;
        }

        subtitleWindowController = SubtitleShared.initSubtitleUI(uiOptions);

        if (!subtitleWindowController || !subtitleWindowController.refs) {
            return;
        }

        if (uiOptions.windowInteractions === 'external') {
            desktopWindowInteractionsCleanup = attachDesktopWindowInteractions(subtitleWindowController);
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

        window.addEventListener('pointerenter', updateNativeInteractionPassthrough);
        window.addEventListener('pointerleave', updateNativeInteractionPassthrough);
        window.addEventListener('focus', updateNativeInteractionPassthrough);
        window.addEventListener('blur', updateNativeInteractionPassthrough);
        window.addEventListener('resize', function() {
            if (activeNativeResizeState) {
                updateNativeInteractionPassthrough();
                return;
            }
            resizeWindowToTranscript();
            updateNativeInteractionPassthrough();
        });
        resizeWindowToTranscript();
        updateNativeInteractionPassthrough();
    });
})();
