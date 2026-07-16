/**
 * app-interpage/guide-overlay.js
 * Inter-page / cross-tab communication.
 *
 * Handles BroadcastChannel dispatch, postMessage listeners, model hot-reload, UI commands, and overlay cleanup.
 * Dependencies loaded before these parts:
 * - app-state.js -> window.appState, window.appConst
 * Runtime dependencies available by the time handlers fire:
 * - window.showStatusToast
 * - window.stopMicCapture / window.clearAudioQueue
 * - window.live2dManager / window.vrmManager
 * - initLive2DModel / initVRMModel globals
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appInterpage = window.appInterpage || {};
    const I = window.__appInterpageParts || (window.__appInterpageParts = {});
    I.yuiGuideChatSpotlightKind = '';
    I.yuiGuideChatSpotlightTimer = 0;
    I.YUI_GUIDE_EXTERNAL_CHAT_CURSOR_SCREEN_POINT_KEY = 'neko_yui_guide_external_chat_cursor_screen_point_v1';
    var YUI_GUIDE_PC_OVERLAY_SEQUENCE_KEY = 'yuiGuidePcOverlaySequence';
    var YUI_GUIDE_PC_OVERLAY_MAX_SAME_RUN_STALE_RETRIES = 3;
    var YUI_GUIDE_PC_OVERLAY_DEFERRED_RECONCILIATION_DELAY_MS = 48;
    I.yuiGuidePcOverlayActive = false;
    I.yuiGuidePcOverlayReady = false;
    I.yuiGuidePcOverlayRunIdOverride = '';
    I.yuiGuidePcOverlayEndedRunId = '';
    var yuiGuidePcOverlayLifecycleEpoch = 0;
    I.yuiGuidePcOverlayLifecycleClosed = false;
    var yuiGuidePcOverlayLifecycleRunId = '';
    var yuiGuidePcOverlaySequence = 0;
    var yuiGuidePcOverlayDeferredReconciliationTimer = 0;
    I.yuiGuidePcOverlaySpotlights = [];
    I.yuiGuidePcOverlayCursor = null;
    I.yuiGuideChatSpotlightLastPcKind = '';
    I.yuiGuideChatSpotlightLastPcVariant = '';
    I.yuiGuideChatSpotlightLastPcRects = [];
    I.yuiGuideChatSpotlightPcOverlayRunId = '';
    I.yuiGuideChatSpotlightVariant = '';
    I.yuiGuideChatCursorRequestToken = 0;
    I.yuiGuideChatCursorArcRequestToken = 0;
    I.yuiGuideChatCursorPoint = null;
    I.yuiGuideChatCursorFrozenScreenPoints = Object.create(null);
    I.yuiGuideCompactToolWheelRotateRetryToken = 0;

    I.getYuiGuideChatSpotlightElement = function getYuiGuideChatSpotlightElement(createIfMissing) {
        return getYuiGuideChatSpotlightElementWithCreateFlag(createIfMissing);
    }

    function getYuiGuideChatSpotlightElementWithCreateFlag(createIfMissing) {
        var existing = document.getElementById('yui-guide-chat-spotlight');
        if (!existing && I.isYuiGuidePcOverlayAvailable() && createIfMissing !== true) {
            return null;
        }
        if (existing || createIfMissing === false || typeof document === 'undefined' || !document.body) {
            return existing;
        }
        var spotlight = document.createElement('div');
        spotlight.id = 'yui-guide-chat-spotlight';
        spotlight.hidden = true;
        spotlight.setAttribute('aria-hidden', 'true');
        spotlight.style.position = 'fixed';
        spotlight.style.pointerEvents = 'none';
        spotlight.style.zIndex = '2147483000';
        spotlight.style.boxSizing = 'border-box';
        spotlight.style.border = '2px solid rgba(39, 89, 228, 0.98)';
        spotlight.style.boxShadow = '0 0 0 9999px rgba(8, 12, 28, 0.18), 0 0 24px rgba(39, 89, 228, 0.38)';
        spotlight.style.transition = 'left 120ms ease, top 120ms ease, width 120ms ease, height 120ms ease';
        document.body.appendChild(spotlight);
        return spotlight;
    }

    function getYuiGuidePcOverlayHost() {
        var host = window.nekoTutorialOverlay;
        if (!host || typeof host.update !== 'function' || typeof host.getWindowMetricsSync !== 'function') {
            return null;
        }
        return host;
    }

    I.isYuiGuidePcOverlayAvailable = function isYuiGuidePcOverlayAvailable() {
        return !!getYuiGuidePcOverlayHost();
    }

    function getYuiGuidePcOverlayRunId() {
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (storedRunId) {
            if (storedRunId !== I.yuiGuidePcOverlayRunIdOverride) {
                I.yuiGuidePcOverlayRunIdOverride = storedRunId;
                I.yuiGuidePcOverlayActive = false;
                I.yuiGuidePcOverlayReady = false;
            }
            return storedRunId;
        }
        if (I.yuiGuidePcOverlayRunIdOverride) {
            if (I.isYuiGuidePcOverlayRunEnded(I.yuiGuidePcOverlayRunIdOverride)) {
                I.yuiGuidePcOverlayRunIdOverride = '';
            } else {
                return I.yuiGuidePcOverlayRunIdOverride;
            }
        }
        try {
            var nextRunId = 'yui-guide-chat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
            window.localStorage.setItem('yuiGuidePcOverlayRunId', nextRunId);
            I.yuiGuidePcOverlayRunIdOverride = nextRunId;
            return nextRunId;
        } catch (_) {
            I.yuiGuidePcOverlayRunIdOverride = 'yui-guide-chat-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
            return I.yuiGuidePcOverlayRunIdOverride;
        }
    }

    I.getExistingYuiGuidePcOverlayRunId = function getExistingYuiGuidePcOverlayRunId() {
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (storedRunId) {
            if (storedRunId !== I.yuiGuidePcOverlayRunIdOverride) {
                I.yuiGuidePcOverlayRunIdOverride = storedRunId;
                I.yuiGuidePcOverlayActive = false;
                I.yuiGuidePcOverlayReady = false;
            }
            return storedRunId;
        }
        if (I.yuiGuidePcOverlayRunIdOverride) {
            if (!I.isYuiGuidePcOverlayRunEnded(I.yuiGuidePcOverlayRunIdOverride)) {
                return I.yuiGuidePcOverlayRunIdOverride;
            }
            I.yuiGuidePcOverlayRunIdOverride = '';
        }
        return '';
    }

    I.isYuiGuidePcOverlayRunEnded = function isYuiGuidePcOverlayRunEnded(runId) {
        return !!runId && !!I.yuiGuidePcOverlayEndedRunId && runId === I.yuiGuidePcOverlayEndedRunId;
    }

    function isYuiGuideChatOwnedPcOverlayRunId(runId) {
        return typeof runId === 'string' && runId.indexOf('yui-guide-chat-') === 0;
    }

    function readStoredYuiGuidePcOverlayRunId() {
        try {
            var storedRunId = window.localStorage.getItem('yuiGuidePcOverlayRunId') || '';
            if (storedRunId && I.isYuiGuidePcOverlayRunEnded(storedRunId)) {
                window.localStorage.removeItem('yuiGuidePcOverlayRunId');
                return '';
            }
            return storedRunId;
        } catch (_) {
            return '';
        }
    }

    function syncYuiGuidePcOverlayRunIdFromStorage() {
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (!storedRunId || storedRunId === I.yuiGuidePcOverlayRunIdOverride) {
            return false;
        }
        I.yuiGuidePcOverlayRunIdOverride = storedRunId;
        I.yuiGuidePcOverlayActive = false;
        I.yuiGuidePcOverlayReady = false;
        return true;
    }

    I.resolveCanonicalYuiGuideBridgeRunId = function resolveCanonicalYuiGuideBridgeRunId(message) {
        var tutorialRunId = message && typeof message.tutorialRunId === 'string' && message.tutorialRunId
            ? message.tutorialRunId
            : '';
        if (tutorialRunId) {
            return I.rememberYuiGuidePcOverlayRunId(tutorialRunId);
        }
        var existingRunId = I.getExistingYuiGuidePcOverlayRunId();
        if (existingRunId) {
            return I.rememberYuiGuidePcOverlayRunId(existingRunId);
        }
        var pcOverlayRunId = message && typeof message.pcOverlayRunId === 'string' && message.pcOverlayRunId
            ? message.pcOverlayRunId
            : '';
        if (pcOverlayRunId) {
            return I.rememberYuiGuidePcOverlayRunId(pcOverlayRunId);
        }
        return '';
    }

    I.rememberYuiGuidePcOverlayRunId = function rememberYuiGuidePcOverlayRunId(runId) {
        var normalizedRunId = typeof runId === 'string' && runId ? runId : '';
        if (!normalizedRunId) {
            return '';
        }
        if (I.isYuiGuidePcOverlayRunEnded(normalizedRunId)) {
            if (I.yuiGuidePcOverlayRunIdOverride === normalizedRunId) {
                I.yuiGuidePcOverlayRunIdOverride = '';
            }
            try {
                if (window.localStorage.getItem('yuiGuidePcOverlayRunId') === normalizedRunId) {
                    window.localStorage.removeItem('yuiGuidePcOverlayRunId');
                }
            } catch (_) {}
            return '';
        }
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (
            storedRunId
            && storedRunId !== normalizedRunId
            && I.yuiGuidePcOverlayRunIdOverride
            && I.yuiGuidePcOverlayRunIdOverride !== normalizedRunId
        ) {
            I.yuiGuidePcOverlayRunIdOverride = storedRunId;
            I.yuiGuidePcOverlayActive = false;
            I.yuiGuidePcOverlayReady = false;
            return storedRunId;
        }
        try {
            window.localStorage.setItem('yuiGuidePcOverlayRunId', normalizedRunId);
        } catch (_) {}
        I.yuiGuidePcOverlayRunIdOverride = normalizedRunId;
        return normalizedRunId;
    }

    I.getYuiGuidePcOverlayRunIdFromMessage = function getYuiGuidePcOverlayRunIdFromMessage(message) {
        return I.resolveCanonicalYuiGuideBridgeRunId(message);
    }

    I.isYuiGuideLifecycleScopedAction = function isYuiGuideLifecycleScopedAction(action) {
        switch (action) {
            // 教程准备会改写当前胶囊快照；必须和后续 fixed-layout 一样绑定当前 run，
            // 否则快速重启时迟到的旧 prepare 会覆盖新教程的恢复身份。
            case 'yui_guide_prepare_compact_chat':
            case 'yui_guide_set_chat_buttons_disabled':
            case 'yui_guide_set_chat_input_locked':
            case 'yui_guide_set_compact_chat_fixed_layout':
            case 'yui_guide_set_chat_spotlight':
            case 'yui_guide_set_chat_cursor':
            case 'yui_guide_drag_chat_cursor':
            case 'yui_guide_arc_chat_cursor':
            case 'yui_guide_set_avatar_tool_menu_open':
            case 'yui_guide_set_compact_history_open':
            case 'yui_guide_set_compact_tool_fan_open':
            case 'yui_guide_rotate_compact_tool_wheel':
            case 'yui_guide_set_compact_tool_wheel_index':
                return true;
            default:
                return false;
        }
    }

    I.isYuiGuideLifecycleStartAction = function isYuiGuideLifecycleStartAction(action) {
        return action === 'yui_guide_tutorial_lifecycle_started'
            || action === 'yui_guide_tutorial_started'
            || action === 'avatar_floating_guide_started';
    }

    I.openYuiGuidePcOverlayLifecycle = function openYuiGuidePcOverlayLifecycle(message) {
        var runId = message && typeof message.tutorialRunId === 'string'
            ? message.tutorialRunId
            : '';
        if (
            (runId && I.isYuiGuidePcOverlayRunEnded(runId))
            || (I.yuiGuidePcOverlayLifecycleClosed && !runId)
        ) {
            return false;
        }
        if (
            yuiGuidePcOverlayLifecycleEpoch === 0
            || I.yuiGuidePcOverlayLifecycleClosed
            || (runId && runId !== yuiGuidePcOverlayLifecycleRunId)
        ) {
            yuiGuidePcOverlayLifecycleEpoch += 1;
        }
        I.yuiGuidePcOverlayLifecycleClosed = false;
        yuiGuidePcOverlayLifecycleRunId = runId || yuiGuidePcOverlayLifecycleRunId;
        I.yuiGuidePcOverlayEndedRunId = '';
        return true;
    }

    I.closeYuiGuidePcOverlayLifecycle = function closeYuiGuidePcOverlayLifecycle() {
        clearYuiGuidePcOverlayDeferredReconciliation();
        yuiGuidePcOverlayLifecycleEpoch += 1;
        I.yuiGuidePcOverlayLifecycleClosed = true;
        yuiGuidePcOverlayLifecycleRunId = '';
    }

    I.isYuiGuideMessageForCurrentLifecycle = function isYuiGuideMessageForCurrentLifecycle(message) {
        if (
            !message
            || (
                message.action !== 'yui_guide_tutorial_lifecycle_ended'
                && !I.isYuiGuideLifecycleScopedAction(message.action)
            )
        ) {
            return true;
        }
        var runId = typeof message.tutorialRunId === 'string'
            ? message.tutorialRunId
            : '';
        return !runId
            || !yuiGuidePcOverlayLifecycleRunId
            || runId === yuiGuidePcOverlayLifecycleRunId;
    }

    function resetYuiGuidePcOverlayRunForRetry() {
        I.yuiGuidePcOverlayActive = false;
        I.yuiGuidePcOverlayReady = false;
        I.yuiGuidePcOverlayRunIdOverride = '';
        yuiGuidePcOverlaySequence = 0;
        try {
            window.localStorage.removeItem('yuiGuidePcOverlayRunId');
        } catch (_) {}
    }

    function clearYuiGuidePcOverlayDeferredReconciliation() {
        if (yuiGuidePcOverlayDeferredReconciliationTimer) {
            window.clearTimeout(yuiGuidePcOverlayDeferredReconciliationTimer);
            yuiGuidePcOverlayDeferredReconciliationTimer = 0;
        }
    }

    function scheduleYuiGuidePcOverlayDeferredReconciliation(
        patch,
        attemptedRunId,
        retryCount,
        attemptedLifecycleEpoch
    ) {
        clearYuiGuidePcOverlayDeferredReconciliation();
        yuiGuidePcOverlayDeferredReconciliationTimer = window.setTimeout(function () {
            yuiGuidePcOverlayDeferredReconciliationTimer = 0;
            if (
                I.yuiGuidePcOverlayLifecycleClosed
                || attemptedLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
            ) {
                return;
            }
            I.sendYuiGuidePcOverlayPatch(patch || {}, retryCount, {
                tutorialRunId: attemptedRunId
            });
        }, YUI_GUIDE_PC_OVERLAY_DEFERRED_RECONCILIATION_DELAY_MS);
    }

    function handleYuiGuidePcOverlayStaleResult(
        result,
        patch,
        attemptedRunId,
        retried,
        attemptedLifecycleEpoch,
        attemptedSequence
    ) {
        var isStaleResponse = !!(result && result.stale === true);
        var retryCount = Math.max(0, Math.floor(Number(retried) || 0));
        if (
            I.yuiGuidePcOverlayLifecycleClosed
            || attemptedLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
            || attemptedSequence !== yuiGuidePcOverlaySequence
        ) {
            return;
        }
        var activeSequence = Math.max(0, Math.floor(Number(result && result.activeSequence) || 0));
        if (
            isStaleResponse
            && result.reason === 'stale-sequence'
            && result.activeTutorialRunId === attemptedRunId
            && activeSequence > attemptedSequence
        ) {
            return;
        }
        var attemptedCurrentRun = !!(attemptedRunId && attemptedRunId === I.yuiGuidePcOverlayRunIdOverride);
        var attemptedChatOwnedRun = isYuiGuideChatOwnedPcOverlayRunId(attemptedRunId);
        var storedCanonicalRunId = readStoredYuiGuidePcOverlayRunId();
        var attemptedCanonicalRun = !!(
            storedCanonicalRunId
            && attemptedRunId
            && attemptedRunId === storedCanonicalRunId
            && !attemptedChatOwnedRun
        );
        var isSameRunSequenceStale = !!(
            isStaleResponse
            && result.reason === 'stale-sequence'
            && result.activeTutorialRunId === attemptedRunId
            && activeSequence === attemptedSequence
            && (
                attemptedCanonicalRun
                || (attemptedCurrentRun && attemptedChatOwnedRun)
            )
        );

        if (isSameRunSequenceStale) {
            if (retryCount < YUI_GUIDE_PC_OVERLAY_MAX_SAME_RUN_STALE_RETRIES) {
                yuiGuidePcOverlaySequence = nextYuiGuidePcOverlaySequence(result.activeSequence);
                I.sendYuiGuidePcOverlayPatch(patch || {}, retryCount + 1, {
                    tutorialRunId: attemptedRunId
                });
            } else if (retryCount === YUI_GUIDE_PC_OVERLAY_MAX_SAME_RUN_STALE_RETRIES) {
                scheduleYuiGuidePcOverlayDeferredReconciliation(
                    patch,
                    attemptedRunId,
                    retryCount,
                    attemptedLifecycleEpoch
                );
            }
            return;
        }

        if (!isStaleResponse || retryCount > 0 || !attemptedRunId) {
            return;
        }
        if (attemptedCanonicalRun) {
            if (syncYuiGuidePcOverlayRunIdFromStorage()) {
                I.sendYuiGuidePcOverlayPatch(patch || {}, retryCount + 1);
                return;
            }
        } else if (!attemptedCurrentRun || !attemptedChatOwnedRun) {
            return;
        }
        if (syncYuiGuidePcOverlayRunIdFromStorage()) {
            I.sendYuiGuidePcOverlayPatch(patch || {}, retryCount + 1);
            return;
        }
        resetYuiGuidePcOverlayRunForRetry();
        I.sendYuiGuidePcOverlayPatch(patch || {}, retryCount + 1);
    }

    function resolveYuiGuidePcOverlayRunIdForSend(requestedRunId, allowCreateRun) {
        var normalizedRequestedRunId = typeof requestedRunId === 'string' && requestedRunId
            ? requestedRunId
            : '';
        var storedRunId = readStoredYuiGuidePcOverlayRunId();
        if (storedRunId && storedRunId !== normalizedRequestedRunId) {
            I.yuiGuidePcOverlayRunIdOverride = storedRunId;
            I.yuiGuidePcOverlayActive = false;
            I.yuiGuidePcOverlayReady = false;
            return storedRunId;
        }
        if (normalizedRequestedRunId) {
            return I.rememberYuiGuidePcOverlayRunId(normalizedRequestedRunId);
        }
        return allowCreateRun === false
            ? I.getExistingYuiGuidePcOverlayRunId()
            : getYuiGuidePcOverlayRunId();
    }

    function nextYuiGuidePcOverlaySequence(minimumSequence) {
        var wallSequence = Date.now() * 1000;
        var sequenceFloor = Math.max(0, Math.floor(Number(minimumSequence) || 0));
        var storedSequence = 0;
        try {
            storedSequence = Math.max(
                0,
                Math.floor(Number(window.localStorage.getItem(YUI_GUIDE_PC_OVERLAY_SEQUENCE_KEY)) || 0)
            );
        } catch (_) {
            storedSequence = 0;
        }

        yuiGuidePcOverlaySequence = Math.max(
            yuiGuidePcOverlaySequence + 1,
            storedSequence + 1,
            sequenceFloor + 1,
            wallSequence
        );
        try {
            window.localStorage.setItem(YUI_GUIDE_PC_OVERLAY_SEQUENCE_KEY, String(yuiGuidePcOverlaySequence));
        } catch (_) {}
        return yuiGuidePcOverlaySequence;
    }

    function getYuiGuideWindowMetrics() {
        var host = getYuiGuidePcOverlayHost();
        if (host) {
            try {
                var metrics = host.getWindowMetricsSync();
                if (metrics && (metrics.contentBounds || metrics.bounds)) {
                    return metrics;
                }
            } catch (_) {}
        }
        return {
            bounds: {
                x: Number.isFinite(window.screenX) ? window.screenX : 0,
                y: Number.isFinite(window.screenY) ? window.screenY : 0
            }
        };
    }

    function getYuiGuideScreenCoordinateBounds(metrics) {
        return metrics && (metrics.bounds || metrics.contentBounds) || { x: 0, y: 0 };
    }

    function normalizeYuiGuideNiriPetPhysicalCropBounds(bounds) {
        if (!bounds || typeof bounds !== 'object') {
            return null;
        }
        var x = Number(bounds.x);
        var y = Number(bounds.y);
        var width = Number(bounds.width);
        var height = Number(bounds.height);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return null;
        }
        return {
            x: Math.round(x),
            y: Math.round(y),
            width: Math.max(1, Math.round(width)),
            height: Math.max(1, Math.round(height))
        };
    }

    function normalizeYuiGuideNiriPetPhysicalCropPoint(point) {
        if (!point || typeof point !== 'object') {
            return null;
        }
        var x = Number(point.x);
        var y = Number(point.y);
        return Number.isFinite(x) && Number.isFinite(y) ? { x: x, y: y } : null;
    }

    function normalizeYuiGuideNiriPetPhysicalCropRect(rect) {
        if (!rect || typeof rect !== 'object') {
            return null;
        }
        var x = Number(Object.prototype.hasOwnProperty.call(rect, 'x') ? rect.x : rect.left);
        var y = Number(Object.prototype.hasOwnProperty.call(rect, 'y') ? rect.y : rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return null;
        }
        return {
            x: x,
            y: y,
            width: width,
            height: height
        };
    }

    function getYuiGuideNiriPetPhysicalCropApi() {
        try {
            var api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
            if (!api || typeof api !== 'object') {
                return null;
            }
            if (typeof api.isActive === 'function' && !api.isActive()) {
                return null;
            }
            return api;
        } catch (_) {
            return null;
        }
    }

    function areYuiGuideNiriPetPhysicalCropBoundsEquivalent(first, second) {
        return !!(first && second
            && Math.abs(Number(first.x || 0) - Number(second.x || 0)) <= 1
            && Math.abs(Number(first.y || 0) - Number(second.y || 0)) <= 1
            && Math.abs(Number(first.width || 0) - Number(second.width || 0)) <= 1
            && Math.abs(Number(first.height || 0) - Number(second.height || 0)) <= 1);
    }

    function hasYuiGuideNiriPetPhysicalCropVirtualizedMetrics(metrics) {
        if (!metrics || metrics.niriPetPhysicalCrop !== true) {
            return false;
        }
        if (metrics.niriPetPhysicalCropMetricsVirtualized === true) {
            return true;
        }
        var screenBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(metrics.contentBounds || metrics.bounds);
        var virtualBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(metrics.niriPetPhysicalCropVirtualBounds);
        return areYuiGuideNiriPetPhysicalCropBoundsEquivalent(screenBounds, virtualBounds);
    }

    function getYuiGuideNiriPetPhysicalCropState(metrics) {
        if (metrics && metrics.niriPetPhysicalCrop === true) {
            var metricCropBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(
                metrics.niriPetPhysicalCropBounds || metrics.contentBounds || metrics.bounds
            );
            var metricVirtualBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(metrics.niriPetPhysicalCropVirtualBounds);
            var metricOffsetX = Number(metrics.niriPetPhysicalCropOffsetX);
            var metricOffsetY = Number(metrics.niriPetPhysicalCropOffsetY);
            return metricCropBounds ? {
                cropBounds: metricCropBounds,
                virtualBounds: metricVirtualBounds,
                offsetX: Number.isFinite(metricOffsetX) ? Math.round(metricOffsetX) : 0,
                offsetY: Number.isFinite(metricOffsetY) ? Math.round(metricOffsetY) : 0,
                metricsVirtualized: hasYuiGuideNiriPetPhysicalCropVirtualizedMetrics(metrics)
            } : null;
        }

        try {
            var api = typeof window !== 'undefined' ? window.__nekoNiriPetPhysicalCrop : null;
            if (!api || typeof api !== 'object') {
                return null;
            }
            if (typeof api.isActive === 'function' && !api.isActive()) {
                return null;
            }
            var state = typeof api.getState === 'function' ? api.getState() : null;
            var cropBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(state && state.cropBounds);
            var virtualBounds = normalizeYuiGuideNiriPetPhysicalCropBounds(state && state.virtualBounds);
            if (!cropBounds) {
                return null;
            }
            var offsetX = Number(state && state.offsetX);
            var offsetY = Number(state && state.offsetY);
            if (!Number.isFinite(offsetX) && virtualBounds) {
                offsetX = cropBounds.x - virtualBounds.x;
            }
            if (!Number.isFinite(offsetY) && virtualBounds) {
                offsetY = cropBounds.y - virtualBounds.y;
            }
            return {
                cropBounds: cropBounds,
                virtualBounds: virtualBounds,
                offsetX: Number.isFinite(offsetX) ? Math.round(offsetX) : 0,
                offsetY: Number.isFinite(offsetY) ? Math.round(offsetY) : 0
            };
        } catch (_) {
            return null;
        }
    }

    function toYuiGuideNiriPetPhysicalCropVirtualPoint(x, y) {
        var api = getYuiGuideNiriPetPhysicalCropApi();
        if (!api || typeof api.toVirtualPoint !== 'function') {
            return null;
        }
        try {
            return normalizeYuiGuideNiriPetPhysicalCropPoint(api.toVirtualPoint({
                x: Number(x || 0),
                y: Number(y || 0)
            }));
        } catch (_) {
            return null;
        }
    }

    function toYuiGuideNiriPetPhysicalCropVirtualRect(rect) {
        var api = getYuiGuideNiriPetPhysicalCropApi();
        if (!api || typeof api.toVirtualRect !== 'function') {
            return null;
        }
        try {
            var virtualRect = normalizeYuiGuideNiriPetPhysicalCropRect(api.toVirtualRect({
                x: Number(rect.left || 0),
                y: Number(rect.top || 0),
                width: Number(rect.width || 0),
                height: Number(rect.height || 0)
            }));
            return virtualRect ? {
                left: virtualRect.x,
                top: virtualRect.y,
                width: virtualRect.width,
                height: virtualRect.height
            } : null;
        } catch (_) {
            return null;
        }
    }

    function toYuiGuideNiriPetPhysicalCropVirtualPointWithState(x, y, cropState) {
        if (cropState && cropState.metricsVirtualized) {
            return {
                x: Number(x || 0),
                y: Number(y || 0)
            };
        }
        return toYuiGuideNiriPetPhysicalCropVirtualPoint(x, y) || {
            x: Number(x || 0) + Number(cropState && cropState.offsetX || 0),
            y: Number(y || 0) + Number(cropState && cropState.offsetY || 0)
        };
    }

    function toYuiGuideNiriPetPhysicalCropVirtualRectWithState(rect, cropState) {
        if (cropState && cropState.metricsVirtualized) {
            return {
                left: Number(rect.left || 0),
                top: Number(rect.top || 0),
                width: rect.width,
                height: rect.height
            };
        }
        return toYuiGuideNiriPetPhysicalCropVirtualRect(rect) || {
            left: Number(rect.left || 0) + Number(cropState && cropState.offsetX || 0),
            top: Number(rect.top || 0) + Number(cropState && cropState.offsetY || 0),
            width: rect.width,
            height: rect.height
        };
    }

    function shouldApplyYuiGuideVisualViewportOffset(metrics) {
        return !getYuiGuideNiriPetPhysicalCropState(metrics);
    }

    function toYuiGuideScreenVirtualPoint(x, y, cropState) {
        var screenBounds = cropState.virtualBounds || cropState.cropBounds;
        return {
            x: Number(screenBounds.x || 0) + Number(x || 0),
            y: Number(screenBounds.y || 0) + Number(y || 0)
        };
    }

    I.toYuiGuideScreenPoint = function toYuiGuideScreenPoint(x, y) {
        var metrics = getYuiGuideWindowMetrics();
        var cropState = getYuiGuideNiriPetPhysicalCropState(metrics);
        if (cropState && cropState.cropBounds) {
            var virtualPoint = toYuiGuideNiriPetPhysicalCropVirtualPointWithState(x, y, cropState);
            return toYuiGuideScreenVirtualPoint(
                virtualPoint.x,
                virtualPoint.y,
                cropState
            );
        }
        var bounds = getYuiGuideScreenCoordinateBounds(metrics);
        var viewport = shouldApplyYuiGuideVisualViewportOffset(metrics) ? (window.visualViewport || null) : null;
        var offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
        var offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
        return {
            x: Number(bounds.x || 0) + Number(x || 0) + offsetLeft,
            y: Number(bounds.y || 0) + Number(y || 0) + offsetTop
        };
    }

    I.toYuiGuideScreenRect = function toYuiGuideScreenRect(rect, kind, variant) {
        if (!rect || rect.width <= 0 || rect.height <= 0) {
            return null;
        }
        var metrics = getYuiGuideWindowMetrics();
        var cropState = getYuiGuideNiriPetPhysicalCropState(metrics);
        var cropRect = cropState && cropState.cropBounds
            ? toYuiGuideNiriPetPhysicalCropVirtualRectWithState(rect, cropState)
            : rect;
        var point = cropState && cropState.cropBounds
            ? toYuiGuideScreenVirtualPoint(cropRect.left, cropRect.top, cropState)
            : I.toYuiGuideScreenPoint(rect.left, rect.top);
        var isCircle = I.getYuiGuideChatTargetShape(kind) === 'circle';
        var radius = kind === 'window' ? 26 : Math.min(34, Math.max(18, Math.round((cropRect.height + 16) / 2)));
        if (isCircle) {
            radius = 999;
        }
        return {
            id: 'external-chat-' + (kind || 'target'),
            kind: kind || '',
            shape: isCircle ? 'circle' : 'rounded-rect',
            variant: variant || '',
            x: point.x,
            y: point.y,
            width: cropRect.width,
            height: cropRect.height,
            radius: radius
        };
    }

    function withoutTransientYuiGuideCursorEffect(cursor) {
        if (!cursor) {
            return cursor;
        }
        var nextCursor = Object.assign({}, cursor);
        delete nextCursor.effect;
        delete nextCursor.effectDurationMs;
        return nextCursor;
    }

    I.sendYuiGuidePcOverlayPatch = function sendYuiGuidePcOverlayPatch(patch, retried, options) {
        var host = getYuiGuidePcOverlayHost();
        if (!host || I.yuiGuidePcOverlayLifecycleClosed) {
            return false;
        }
        var retryCount = Math.max(0, Math.floor(Number(retried) || 0));
        if (retryCount === 0) {
            clearYuiGuidePcOverlayDeferredReconciliation();
        }
        var sendLifecycleEpoch = yuiGuidePcOverlayLifecycleEpoch;
        var sendOptions = options || {};
        if (I.isYuiGuidePcOverlayRunEnded(sendOptions.tutorialRunId)) {
            return false;
        }
        if (patch && Object.prototype.hasOwnProperty.call(patch, 'spotlights')) {
            I.yuiGuidePcOverlaySpotlights = Array.isArray(patch.spotlights) ? patch.spotlights : [];
        }
        if (patch && Object.prototype.hasOwnProperty.call(patch, 'cursor')) {
            I.yuiGuidePcOverlayCursor = withoutTransientYuiGuideCursorEffect(patch.cursor);
        }
        var payload = {
            spotlights: I.yuiGuidePcOverlaySpotlights
        };
        if (patch && Object.prototype.hasOwnProperty.call(patch, 'cursor')) {
            payload.cursor = patch.cursor || null;
        } else if (I.yuiGuidePcOverlayCursor) {
            payload.cursor = I.yuiGuidePcOverlayCursor;
        }
        var runId = resolveYuiGuidePcOverlayRunIdForSend(
            sendOptions.tutorialRunId,
            sendOptions.allowCreateRun
        );
        if (!runId || I.isYuiGuidePcOverlayRunEnded(runId)) {
            return false;
        }
        if (!I.yuiGuidePcOverlayActive && sendOptions.skipBegin !== true && typeof host.begin === 'function') {
            try {
                Promise.resolve(host.begin({ tutorialRunId: runId })).then(function (result) {
                    if (
                        I.yuiGuidePcOverlayLifecycleClosed
                        || sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
                    ) {
                        return;
                    }
                    if (result && result.stale === true) {
                        // The paired update carries the state and owns stale retries.
                        return;
                    }
                    if (result && result.ok === false) {
                        I.yuiGuidePcOverlayActive = false;
                        I.yuiGuidePcOverlayReady = false;
                    }
                }).catch(function () {
                    if (sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch) {
                        return;
                    }
                    I.yuiGuidePcOverlayActive = false;
                    I.yuiGuidePcOverlayReady = false;
                });
                I.yuiGuidePcOverlayActive = true;
            } catch (_) {}
        }
        yuiGuidePcOverlaySequence = nextYuiGuidePcOverlaySequence();
        var updateSequence = yuiGuidePcOverlaySequence;
        try {
            Promise.resolve(host.update({
                tutorialRunId: runId,
                sceneId: 'external-chat',
                sequence: updateSequence,
                payload: payload
            })).then(function (result) {
                if (
                    I.yuiGuidePcOverlayLifecycleClosed
                    || sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch
                ) {
                    return;
                }
                if (result && result.stale === true) {
                    handleYuiGuidePcOverlayStaleResult(
                        result,
                        patch,
                        runId,
                        retryCount,
                        sendLifecycleEpoch,
                        updateSequence
                    );
                    return;
                }
                if (result && result.ok === false) {
                    I.yuiGuidePcOverlayReady = false;
                    return;
                }
                I.yuiGuidePcOverlayReady = true;
            }).catch(function () {
                if (sendLifecycleEpoch !== yuiGuidePcOverlayLifecycleEpoch) {
                    return;
                }
                I.yuiGuidePcOverlayReady = false;
            });
            I.yuiGuidePcOverlayReady = true;
            return true;
        } catch (_) {
            I.yuiGuidePcOverlayReady = false;
            return false;
        }
    }

    Object.assign(window.appInterpage, I.mod || {});
})();
