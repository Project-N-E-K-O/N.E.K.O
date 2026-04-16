(function () {
    'use strict';

    const S = window.appState || {};

    const TIMING = Object.freeze({
        minVisibleMs: 360,
        minThinkingVisibleMs: 220,
        fadeDurationMs: 220,
        maxVisibleMs: 10000,
        maxThinkingMs: 10000,
        textOnlyHoldMs: 600,
        textOnlyFallbackMs: 3200,
        emotionFallbackMs: 1200,
        speechStartNeutralGraceMs: 220,
        speechEndHoldMs: 360,
        edgeMarginPx: 12,
        anchorGapPx: 10,
        positionSnapPx: 3,
        sizeSnapPx: 2,
        live2dMicroMoveDeadzonePx: 8,
        live2dSizeDeadzonePx: 10,
        live2dSizeDeadzoneRatio: 0.08,
        horizontalMoveLockThresholdPx: 10,
        verticalNoiseTolerancePx: 8,
        horizontalMoveMaxVerticalDriftPx: 18,
        verticalMoveLockThresholdPx: 10,
        horizontalNoiseTolerancePx: 8,
        verticalMoveMaxHorizontalDriftPx: 18,
        headBubbleScaleMultiplier: 1.77,
        live2dReliableHeadBubbleScaleMultiplier: 1.18,
        live2dDrawableHeadBubbleScaleMultiplier: 1.28,
        baseWidthShrinkPx: 92,
        baseHeightShrinkPx: 66,
        verticalOffsetPx: 0,
        modelOverlapRatio: 0.28,
        compactModelAspectRatio: 1.15,
        tallModelAspectRatio: 1.8,
        headHeightFromModelRatio: 0.28,
        headHeightFromWidthRatio: 0.56,
        accessoryTrimRatio: 2.1,
        accessoryTrimMaxPx: 96,
        shortHeadAnchorRatio: 0.7,
        tallHeadAnchorRatio: 0.42,
        shortModelOffsetRatio: 0.12,
        tallModelOffsetRatio: -0.4,
        accessoryDropBasePx: 0,
        accessoryDropRatio: 1.2,
        accessoryDropMaxPx: 56,
        headAnchorCorrectionDeadzonePx: 16,
        headAnchorCorrectionRatio: 0.82,
        headAnchorCorrectionMaxPx: 72,
        live2dDisplayInfoFaceAnchorRatio: 0.36,
        live2dDisplayInfoHeadAnchorRatio: 0.44,
        live2dDisplayInfoTopOffsetRatio: 0.24,
        live2dDisplayInfoHeadTopOffsetRatio: 0.22,
        live2dDisplayInfoGapHeadRatio: 0.1,
        live2dDisplayInfoGapBubbleRatio: 0.04,
        live2dDisplayInfoGapBodyRatio: 0.03,
        live2dBodyProxyBubbleLiftRatio: 0.72,
        live2dBodyProxyBodyLiftRatio: 0.42,
        live2dBodyProxyHeadLiftRatio: 0.45,
        live2dHeadTopOffsetRatio: 0.24,
        live2dFaceTopOffsetRatio: 0.26,
        live2dHeadMaxBoundsWidthRatio: 0.82,
        live2dHeadMaxBoundsHeightRatio: 0.58,
        live2dHeadMaxBoundsCenterYRatio: 0.62,
        live2dHeadMaxBodyWidthRatio: 1.52,
        live2dHeadMaxBodyHeightRatio: 0.94,
        live2dHeadMaxBodyCenterYRatio: 0.42,
        live2dBodyAwareHeadWidthRatio: 0.54,
        live2dBodyAwareHeadHeightRatio: 0.52,
        live2dBodyAwareHeadSpanRatio: 0.46,
        live2dDisplayInfoBodyAwareHeadSpanMaxMultiplier: 1.72,
        live2dDrawableHeadWidthRatio: 0.28,
        live2dDrawableHeadHeightRatio: 0.26,
        live2dDrawableHeadSpanRatio: 0.24,
        live2dDrawableHeadSpanMaxMultiplier: 1.32,
        live2dDrawableBodyHeadSpanMaxRatio: 0.38,
        live2dHeadSideOffsetMinHeadWidthRatio: 0.38,
        live2dHeadSideOffsetMaxHeadWidthRatio: 0.66,
        showFollowWindowMs: 360,
        moveFollowWindowMs: 120,
        moveSettleWindowMs: 420,
        wheelFollowWindowMs: 960,
        wheelResyncDelayMs: 96,
        visibleFollowWindowMs: 10000
    });

    const THINKING_CONTENT = '。。。';

    const state = {
        enabled: false,
        visible: false,
        turnId: null,
        phase: 'idle',
        theme: 'thinking',
        emotion: null,
        showEmotionArt: false,
        content: '',
        side: 'right',
        anchorX: 0,
        anchorY: 0,
        shownAt: 0,
        turnEndedAt: 0,
        speechStartedAt: 0,
        followRafId: 0,
        followUntilAt: 0,
        hideTimerId: 0,
        timeoutTimerId: 0,
        maxVisibleTimerId: 0,
        textFallbackTimerId: 0,
        emotionFallbackTimerId: 0,
        emotionSwapTimerId: 0,
        interactionSyncTimerId: 0,
        interactionSyncRafId: 0,
        isAvatarPointerActive: false,
        lastPositionDebugSignature: null,
        lastRenderX: null,
        lastRenderY: null,
        lastRenderWidth: null,
        lastRenderHeight: null,
        lastAnchorType: null,
        lastAnchorBounds: null,
        lastHeadAnchor: null,
        lastLive2dHeadAnchor: null,
        lastHeadRect: null,
        lastHeadMode: null,
        lastHeadSource: null,
        lastBodyRect: null,
        lastBodySource: null,
        lastReliableLive2dHeadRect: null,
        lastPreciseLive2dDisplayInfoRect: null,
        lastCoarseHitAreaHeadRect: null,
        lastBoundsCenterX: null,
        lastBoundsCenterY: null,
        debugRafId: 0
    };

    let bubbleEl = null;
    let frameEl = null;
    let shellEl = null;
    let stageEl = null;
    let mascotEl = null;
    let contentEl = null;
    let debugOverlayEl = null;
    let debugPanelEl = null;
    let debugPanelBodyEl = null;
    let debugAnchorEl = null;
    let debugGuideLineEl = null;
    let debugBoundsRectEl = null;
    let debugHeadRectEl = null;
    let debugBodyRectEl = null;
    let debugBubbleRectEl = null;

    function resolveInitialDebugOverlayEnabled() {
        if (window.NEKO_DEBUG_BUBBLE_OVERLAY === true) {
            return true;
        }

        if (!window.location || !window.location.search) {
            return false;
        }

        try {
            var params = new URLSearchParams(window.location.search);
            var value = params.get('bubbleDebug');
            return value === '1' || value === 'true';
        } catch (_) {
            return false;
        }
    }

    state.debugOverlayEnabled = resolveInitialDebugOverlayEnabled();
    state.lastDebugSnapshot = null;

    function normalizeTurnId(turnId) {
        if (turnId === undefined || turnId === null || turnId === '') {
            return null;
        }
        return String(turnId);
    }

    function now() {
        return Date.now();
    }

    function perfNow() {
        if (window.performance && typeof window.performance.now === 'function') {
            return window.performance.now();
        }
        return now();
    }

    function bubbleTraceEnabled() {
        return window.NEKO_DEBUG_BUBBLE_LIFECYCLE === true;
    }

    function logBubbleLifecycle(label, extra) {
        if (!bubbleTraceEnabled()) {
            return;
        }
        console.log('[BubbleTrace]', label, Object.assign({
            turnId: state.turnId,
            visible: state.visible,
            phase: state.phase,
            theme: state.theme,
            emotion: state.emotion,
            speechStartedAt: state.speechStartedAt,
            turnEndedAt: state.turnEndedAt,
            shownAt: state.shownAt
        }, extra || {}));
    }

    function bubblePositionDebugEnabled() {
        return window.NEKO_DEBUG_BUBBLE_POSITION === true || bubbleTraceEnabled();
    }

    function debugOverlayEnabled() {
        return state.debugOverlayEnabled === true;
    }

    function roundDebugNumber(value) {
        return Number.isFinite(value)
            ? Math.round(value * 10) / 10
            : null;
    }

    function createDebugRect(rect) {
        if (!rect) {
            return null;
        }

        return {
            left: roundDebugNumber(rect.left),
            top: roundDebugNumber(rect.top),
            right: roundDebugNumber(
                Number.isFinite(rect.right) ? rect.right : rect.left + rect.width
            ),
            bottom: roundDebugNumber(
                Number.isFinite(rect.bottom) ? rect.bottom : rect.top + rect.height
            ),
            width: roundDebugNumber(rect.width),
            height: roundDebugNumber(rect.height),
            centerX: roundDebugNumber(rect.centerX),
            centerY: roundDebugNumber(rect.centerY)
        };
    }

    function createDebugPoint(point) {
        if (!point) {
            return null;
        }

        return {
            x: roundDebugNumber(point.x),
            y: roundDebugNumber(point.y)
        };
    }

    function logBubblePosition(snapshot) {
        if (!bubblePositionDebugEnabled()) {
            return;
        }

        var signature = JSON.stringify(snapshot);
        if (signature === state.lastPositionDebugSignature) {
            return;
        }

        state.lastPositionDebugSignature = signature;
        console.log('[BubblePosition]', snapshot);
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function lerp(start, end, progress) {
        return start + (end - start) * progress;
    }

    function clearTimer(timerKey) {
        if (state[timerKey]) {
            clearTimeout(state[timerKey]);
            state[timerKey] = 0;
        }
    }

    function clearTurnTimers() {
        clearTimer('hideTimerId');
        clearTimer('timeoutTimerId');
        clearTimer('maxVisibleTimerId');
        clearTimer('textFallbackTimerId');
        clearTimer('emotionFallbackTimerId');
        clearTimer('emotionSwapTimerId');
    }

    function stopFollowLoop() {
        if (state.followRafId) {
            cancelAnimationFrame(state.followRafId);
            state.followRafId = 0;
        }
        state.followUntilAt = 0;
        state.isAvatarPointerActive = false;
    }

    function stopDebugOverlayLoop() {
        if (state.debugRafId) {
            cancelAnimationFrame(state.debugRafId);
            state.debugRafId = 0;
        }
    }

    function clearInteractionSync() {
        if (state.interactionSyncRafId) {
            cancelAnimationFrame(state.interactionSyncRafId);
            state.interactionSyncRafId = 0;
        }
        clearTimer('interactionSyncTimerId');
    }

    function resetPositionTracking() {
        clearInteractionSync();
        state.lastPositionDebugSignature = null;
        state.lastRenderX = null;
        state.lastRenderY = null;
        state.lastRenderWidth = null;
        state.lastRenderHeight = null;
        state.lastAnchorType = null;
        state.lastAnchorBounds = null;
        state.lastHeadAnchor = null;
        state.lastLive2dHeadAnchor = null;
        state.lastHeadRect = null;
        state.lastHeadMode = null;
        state.lastHeadSource = null;
        state.lastBodyRect = null;
        state.lastBodySource = null;
        state.lastReliableLive2dHeadRect = null;
        state.lastPreciseLive2dDisplayInfoRect = null;
        state.lastCoarseHitAreaHeadRect = null;
        state.lastBoundsCenterX = null;
        state.lastBoundsCenterY = null;

        if (bubbleEl) {
            bubbleEl.style.left = '-9999px';
            bubbleEl.style.top = '-9999px';
        }
    }

    function ensureDom() {
        if (bubbleEl) {
            return;
        }

        bubbleEl = document.createElement('div');
        bubbleEl.id = 'avatar-reaction-bubble';
        bubbleEl.className = 'avatar-reaction-bubble is-hidden';
        bubbleEl.dataset.theme = 'thinking';
        bubbleEl.dataset.phase = 'idle';
        bubbleEl.dataset.side = 'right';
        bubbleEl.setAttribute('aria-hidden', 'true');

        frameEl = document.createElement('div');
        frameEl.className = 'avatar-reaction-bubble-frame';

        shellEl = document.createElement('div');
        shellEl.className = 'avatar-reaction-bubble-shell';
        shellEl.setAttribute('aria-hidden', 'true');

        stageEl = document.createElement('div');
        stageEl.className = 'avatar-reaction-bubble-stage';

        mascotEl = document.createElement('div');
        mascotEl.className = 'avatar-reaction-bubble-mascot';
        mascotEl.setAttribute('aria-hidden', 'true');

        contentEl = document.createElement('span');
        contentEl.className = 'avatar-reaction-bubble-content';
        contentEl.textContent = '。。。';

        stageEl.appendChild(mascotEl);
        stageEl.appendChild(contentEl);
        frameEl.appendChild(shellEl);
        frameEl.appendChild(stageEl);
        bubbleEl.appendChild(frameEl);
        document.body.appendChild(bubbleEl);

        debugOverlayEl = document.createElement('div');
        debugOverlayEl.id = 'avatar-reaction-bubble-debug-overlay';
        debugOverlayEl.className = 'avatar-reaction-bubble-debug-overlay is-hidden';
        debugOverlayEl.setAttribute('aria-hidden', 'true');

        debugGuideLineEl = document.createElement('div');
        debugGuideLineEl.className = 'avatar-reaction-bubble-debug-guide';

        debugAnchorEl = document.createElement('div');
        debugAnchorEl.className = 'avatar-reaction-bubble-debug-anchor';

        debugBoundsRectEl = document.createElement('div');
        debugBoundsRectEl.className = 'avatar-reaction-bubble-debug-rect is-bounds';

        debugHeadRectEl = document.createElement('div');
        debugHeadRectEl.className = 'avatar-reaction-bubble-debug-rect is-head';

        debugBodyRectEl = document.createElement('div');
        debugBodyRectEl.className = 'avatar-reaction-bubble-debug-rect is-body';

        debugBubbleRectEl = document.createElement('div');
        debugBubbleRectEl.className = 'avatar-reaction-bubble-debug-rect is-bubble';

        debugPanelEl = document.createElement('aside');
        debugPanelEl.className = 'avatar-reaction-bubble-debug-panel';

        var debugPanelTitleEl = document.createElement('div');
        debugPanelTitleEl.className = 'avatar-reaction-bubble-debug-title';
        debugPanelTitleEl.textContent = 'Bubble Debug';

        var debugPanelHintEl = document.createElement('div');
        debugPanelHintEl.className = 'avatar-reaction-bubble-debug-hint';
        debugPanelHintEl.textContent = 'Shift+B to toggle';

        debugPanelBodyEl = document.createElement('pre');
        debugPanelBodyEl.className = 'avatar-reaction-bubble-debug-body';
        debugPanelBodyEl.textContent = 'No debug data yet.';

        debugPanelEl.appendChild(debugPanelTitleEl);
        debugPanelEl.appendChild(debugPanelHintEl);
        debugPanelEl.appendChild(debugPanelBodyEl);

        debugOverlayEl.appendChild(debugGuideLineEl);
        debugOverlayEl.appendChild(debugAnchorEl);
        debugOverlayEl.appendChild(debugBoundsRectEl);
        debugOverlayEl.appendChild(debugHeadRectEl);
        debugOverlayEl.appendChild(debugBodyRectEl);
        debugOverlayEl.appendChild(debugBubbleRectEl);
        debugOverlayEl.appendChild(debugPanelEl);
        document.body.appendChild(debugOverlayEl);

        syncDebugOverlayVisibility();
    }

    function syncEnabledFromSettings() {
        state.enabled = !!(window.avatarReactionBubbleEnabled === true || S.avatarReactionBubbleEnabled === true);
        if (!state.enabled) {
            forceHide(true);
        }
        return state.enabled;
    }

    function applyVisualState() {
        ensureDom();

        bubbleEl.dataset.theme = state.theme || 'thinking';
        bubbleEl.dataset.phase = state.phase || 'idle';
        bubbleEl.dataset.side = state.side || 'right';
        contentEl.textContent = state.content || '';

        bubbleEl.classList.toggle('is-hidden', !state.visible);
        bubbleEl.classList.toggle('is-visible', state.visible && state.phase !== 'fading');
        bubbleEl.classList.toggle('is-fading', state.visible && state.phase === 'fading');
        bubbleEl.classList.toggle('has-emotion-art', !!state.showEmotionArt);
        bubbleEl.setAttribute('aria-hidden', state.visible ? 'false' : 'true');

        if (!state.visible) {
            bubbleEl.style.left = '-9999px';
            bubbleEl.style.top = '-9999px';
        }
        syncDebugOverlayVisibility();
        renderDebugOverlay();
    }

    function setDebugRectElement(element, rect, label) {
        if (!element) {
            return;
        }

        if (!rect || !Number.isFinite(rect.left) || !Number.isFinite(rect.top) ||
            !Number.isFinite(rect.width) || !Number.isFinite(rect.height) ||
            rect.width <= 0 || rect.height <= 0) {
            element.classList.add('is-hidden');
            element.removeAttribute('data-label');
            return;
        }

        element.classList.remove('is-hidden');
        element.style.left = Math.round(rect.left) + 'px';
        element.style.top = Math.round(rect.top) + 'px';
        element.style.width = Math.round(rect.width) + 'px';
        element.style.height = Math.round(rect.height) + 'px';
        element.setAttribute('data-label', label || '');
    }

    function setDebugAnchorElement(point) {
        if (!debugAnchorEl) {
            return;
        }

        if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) {
            debugAnchorEl.classList.add('is-hidden');
            return;
        }

        debugAnchorEl.classList.remove('is-hidden');
        debugAnchorEl.style.left = Math.round(point.x) + 'px';
        debugAnchorEl.style.top = Math.round(point.y) + 'px';
    }

    function setDebugGuideLine(startPoint, endPoint) {
        if (!debugGuideLineEl) {
            return;
        }

        if (!startPoint || !endPoint ||
            !Number.isFinite(startPoint.x) || !Number.isFinite(startPoint.y) ||
            !Number.isFinite(endPoint.x) || !Number.isFinite(endPoint.y)) {
            debugGuideLineEl.classList.add('is-hidden');
            return;
        }

        var dx = endPoint.x - startPoint.x;
        var dy = endPoint.y - startPoint.y;
        var distance = Math.sqrt(dx * dx + dy * dy);
        if (!Number.isFinite(distance) || distance <= 0) {
            debugGuideLineEl.classList.add('is-hidden');
            return;
        }

        debugGuideLineEl.classList.remove('is-hidden');
        debugGuideLineEl.style.left = Math.round(startPoint.x) + 'px';
        debugGuideLineEl.style.top = Math.round(startPoint.y) + 'px';
        debugGuideLineEl.style.width = Math.round(distance) + 'px';
        debugGuideLineEl.style.transform = 'rotate(' + Math.atan2(dy, dx) + 'rad)';
    }

    function syncDebugOverlayVisibility() {
        if (!debugOverlayEl) {
            return;
        }

        var enabled = debugOverlayEnabled();
        debugOverlayEl.classList.toggle('is-hidden', !enabled);
        debugOverlayEl.setAttribute('aria-hidden', enabled ? 'false' : 'true');
    }

    function formatDebugSnapshot(snapshot) {
        if (!snapshot) {
            return 'No debug data yet.';
        }

        var lines = [];
        lines.push('model: ' + (snapshot.model || 'unknown'));
        lines.push('source: ' + (snapshot.headSource || 'n/a'));
        lines.push('mode: ' + (snapshot.headMode || 'n/a'));
        lines.push('head rect reliable: ' + (snapshot.reliableLive2dHeadRect ? 'yes' : 'no'));
        lines.push('displayInfo precise: ' + (snapshot.preciseLive2dDisplayInfoRect ? 'yes' : 'no'));
        lines.push('final side: ' + (snapshot.final && snapshot.final.side ? snapshot.final.side : 'n/a'));
        lines.push('anchor: ' + (snapshot.anchor ? Math.round(snapshot.anchor.x) + ', ' + Math.round(snapshot.anchor.y) : 'n/a'));
        lines.push('bubble: ' + (snapshot.bubbleRect
            ? Math.round(snapshot.bubbleRect.left) + ', ' + Math.round(snapshot.bubbleRect.top) +
                '  ' + Math.round(snapshot.bubbleRect.width) + 'x' + Math.round(snapshot.bubbleRect.height)
            : 'n/a'));
        lines.push('bounds: ' + (snapshot.bounds
            ? Math.round(snapshot.bounds.left) + ', ' + Math.round(snapshot.bounds.top) +
                '  ' + Math.round(snapshot.bounds.width) + 'x' + Math.round(snapshot.bounds.height)
            : 'n/a'));
        lines.push('headRect: ' + (snapshot.headRect
            ? Math.round(snapshot.headRect.left) + ', ' + Math.round(snapshot.headRect.top) +
                '  ' + Math.round(snapshot.headRect.width) + 'x' + Math.round(snapshot.headRect.height)
            : 'n/a'));
        lines.push('bodyRect: ' + (snapshot.bodyRect
            ? Math.round(snapshot.bodyRect.left) + ', ' + Math.round(snapshot.bodyRect.top) +
                '  ' + Math.round(snapshot.bodyRect.width) + 'x' + Math.round(snapshot.bodyRect.height)
            : 'n/a'));
        return lines.join('\n');
    }

    function clearDebugOverlayShapes() {
        setDebugRectElement(debugBoundsRectEl, null);
        setDebugRectElement(debugHeadRectEl, null);
        setDebugRectElement(debugBodyRectEl, null);
        setDebugRectElement(debugBubbleRectEl, null);
        setDebugAnchorElement(null);
        setDebugGuideLine(null, null);
    }

    function renderDebugOverlay(snapshot) {
        if (!debugOverlayEl || !debugPanelBodyEl) {
            return;
        }

        var resolvedSnapshot = snapshot || state.lastDebugSnapshot || null;
        debugPanelBodyEl.textContent = formatDebugSnapshot(resolvedSnapshot);

        if (!debugOverlayEnabled()) {
            clearDebugOverlayShapes();
            return;
        }

        if (!resolvedSnapshot) {
            clearDebugOverlayShapes();
            return;
        }

        setDebugRectElement(debugBoundsRectEl, resolvedSnapshot.bounds, 'model bounds');
        setDebugRectElement(debugHeadRectEl, resolvedSnapshot.headRect, 'head rect');
        setDebugRectElement(debugBodyRectEl, resolvedSnapshot.bodyRect, 'body rect');
        setDebugRectElement(debugBubbleRectEl, resolvedSnapshot.bubbleRect, 'bubble box');
        setDebugAnchorElement(resolvedSnapshot.anchor);

        var guideEndPoint = resolvedSnapshot.bubbleRect
            ? {
                x: resolvedSnapshot.final && resolvedSnapshot.final.side === 'left'
                    ? resolvedSnapshot.bubbleRect.right
                    : resolvedSnapshot.bubbleRect.left,
                y: resolvedSnapshot.bubbleRect.top + resolvedSnapshot.bubbleRect.height * 0.5
            }
            : null;
        setDebugGuideLine(resolvedSnapshot.anchor, guideEndPoint);
    }

    function buildPassiveDebugSnapshot() {
        var anchorInfo = getActiveAvatarBubbleAnchor();
        if (!anchorInfo || !anchorInfo.bounds) {
            return null;
        }

        var bounds = anchorInfo.bounds;
        var headRect = anchorInfo.headRect || null;
        var bodyRect = anchorInfo.bodyRect || null;
        var headMode = anchorInfo.headMode || null;
        var headSource = anchorInfo.headSource || null;
        var reliableLive2dHeadRect = anchorInfo.type === 'live2d'
            ? (typeof anchorInfo.reliableLive2dHeadRect === 'boolean'
                ? anchorInfo.reliableLive2dHeadRect
                : isReliableLive2dHeadRect(headRect, bounds, bodyRect, headSource))
            : false;
        var live2dHeadAnchor = anchorInfo.type === 'live2d'
            ? (anchorInfo.live2dHeadAnchor || (
                reliableLive2dHeadRect
                    ? (getLive2dHeadAnchorFromRect(headRect, headMode, headSource) || anchorInfo.head)
                    : anchorInfo.head
            ))
            : (anchorInfo.head || null);
        var preciseLive2dDisplayInfoRect = anchorInfo.type === 'live2d'
            ? (typeof anchorInfo.preciseLive2dDisplayInfoRect === 'boolean'
                ? anchorInfo.preciseLive2dDisplayInfoRect
                : (reliableLive2dHeadRect && headSource === 'displayInfo'))
            : false;

        return {
            model: anchorInfo.type || 'unknown',
            headSource: headSource || null,
            headMode: headMode || null,
            reliableLive2dHeadRect: !!reliableLive2dHeadRect,
            preciseLive2dDisplayInfoRect: !!preciseLive2dDisplayInfoRect,
            bounds: createDebugRect(bounds),
            headRect: createDebugRect(headRect),
            bodyRect: createDebugRect(bodyRect),
            anchor: createDebugPoint(live2dHeadAnchor || anchorInfo.head || null),
            bubbleRect: state.visible && Number.isFinite(state.lastRenderX) && Number.isFinite(state.lastRenderY) &&
                Number.isFinite(state.lastRenderWidth) && Number.isFinite(state.lastRenderHeight)
                ? createDebugRect({
                    left: state.lastRenderX,
                    top: state.lastRenderY,
                    right: state.lastRenderX + state.lastRenderWidth,
                    bottom: state.lastRenderY + state.lastRenderHeight,
                    width: state.lastRenderWidth,
                    height: state.lastRenderHeight,
                    centerX: state.lastRenderX + state.lastRenderWidth * 0.5,
                    centerY: state.lastRenderY + state.lastRenderHeight * 0.5
                })
                : null,
            final: state.visible
                ? {
                    side: state.side || 'right',
                    x: roundDebugNumber(state.lastRenderX),
                    y: roundDebugNumber(state.lastRenderY)
                }
                : null
        };
    }

    function syncDebugOverlaySnapshot() {
        if (!debugOverlayEnabled()) {
            renderDebugOverlay();
            return;
        }

        if (state.visible && state.lastDebugSnapshot) {
            renderDebugOverlay(state.lastDebugSnapshot);
            return;
        }

        state.lastDebugSnapshot = buildPassiveDebugSnapshot();
        renderDebugOverlay(state.lastDebugSnapshot);
    }

    function ensureDebugOverlayLoop() {
        stopDebugOverlayLoop();
        if (!debugOverlayEnabled()) {
            return;
        }

        var tick = function () {
            state.debugRafId = 0;
            syncDebugOverlaySnapshot();
            if (debugOverlayEnabled()) {
                state.debugRafId = requestAnimationFrame(tick);
            }
        };

        state.debugRafId = requestAnimationFrame(tick);
    }

    function getThemeContent(theme) {
        return theme === 'thinking' ? THINKING_CONTENT : '';
    }

    function normalizeTheme(emotion) {
        switch (String(emotion || '').toLowerCase()) {
            case 'happy':
            case 'joy':
            case 'excited':
                return 'happy';
            case 'sad':
            case 'down':
                return 'sad';
            case 'angry':
            case 'mad':
                return 'angry';
            case 'surprised':
            case 'surprise':
                return 'surprised';
            case 'neutral':
            case 'calm':
                return 'neutral';
            default:
                return 'neutral';
        }
    }

    function isContainerVisible(containerId) {
        var el = document.getElementById(containerId);
        if (!el) {
            return false;
        }
        var style = window.getComputedStyle(el);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function getBoundsFromManager(manager, methodName) {
        if (!manager || typeof manager[methodName] !== 'function') {
            return null;
        }
        try {
            return manager[methodName]();
        } catch (_) {
            return null;
        }
    }

    function getHeadAnchorFromManager(manager) {
        var anchor = getBoundsFromManager(manager, 'getHeadScreenAnchor');
        if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.y)) {
            return null;
        }
        return anchor;
    }

    function cloneBounds(bounds) {
        if (!bounds) {
            return null;
        }
        return Object.assign({}, bounds);
    }

    function clonePoint(point) {
        if (!point) {
            return null;
        }
        return {
            x: point.x,
            y: point.y
        };
    }

    function getHeadRectInfoFromManager(manager) {
        var info = getBoundsFromManager(manager, 'getHeadScreenRectInfo');
        if (!info || !info.rect) {
            return null;
        }

        var rect = info.rect;
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
            rect: {
                left: left,
                right: Number.isFinite(rect.right) ? Number(rect.right) : left + width,
                top: top,
                bottom: Number.isFinite(rect.bottom) ? Number(rect.bottom) : top + height,
                width: width,
                height: height,
                centerX: Number.isFinite(rect.centerX) ? Number(rect.centerX) : left + width * 0.5,
                centerY: Number.isFinite(rect.centerY) ? Number(rect.centerY) : top + height * 0.5
            },
            mode: info.mode === 'head' ? 'head' : 'face',
            source: typeof info.source === 'string' && info.source
                ? info.source
                : 'hitArea'
        };
    }

    function getBodyRectInfoFromManager(manager) {
        var info = getBoundsFromManager(manager, 'getBodyScreenRectInfo');
        if (!info || !info.rect) {
            return null;
        }

        var rect = info.rect;
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
            rect: {
                left: left,
                right: Number.isFinite(rect.right) ? Number(rect.right) : left + width,
                top: top,
                bottom: Number.isFinite(rect.bottom) ? Number(rect.bottom) : top + height,
                width: width,
                height: height,
                centerX: Number.isFinite(rect.centerX) ? Number(rect.centerX) : left + width * 0.5,
                centerY: Number.isFinite(rect.centerY) ? Number(rect.centerY) : top + height * 0.5
            },
            mode: 'body',
            source: typeof info.source === 'string' && info.source
                ? info.source
                : null
        };
    }

    function getLive2dBubbleDebugInfoFromManager(manager) {
        var info = getBoundsFromManager(manager, 'getBubbleAnchorDebugInfo');
        return info || null;
    }

    function normalizeRectLike(rect) {
        if (!rect) {
            return null;
        }

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
            right: Number.isFinite(rect.right) ? Number(rect.right) : left + width,
            top: top,
            bottom: Number.isFinite(rect.bottom) ? Number(rect.bottom) : top + height,
            width: width,
            height: height,
            centerX: Number.isFinite(rect.centerX) ? Number(rect.centerX) : left + width * 0.5,
            centerY: Number.isFinite(rect.centerY) ? Number(rect.centerY) : top + height * 0.5
        };
    }

    function normalizePointLike(point) {
        if (!point) {
            return null;
        }

        var x = Number(point.x);
        var y = Number(point.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) {
            return null;
        }

        return { x: x, y: y };
    }

    function getLive2dBubbleGeometryInfoFromManager(manager) {
        var info = getBoundsFromManager(manager, 'getBubbleAnchorGeometryInfo');
        if (!info || !info.bounds) {
            return null;
        }

        return {
            bounds: normalizeRectLike(info.bounds),
            rawHeadAnchor: normalizePointLike(info.rawHeadAnchor),
            headAnchor: normalizePointLike(info.headAnchor),
            headRect: normalizeRectLike(info.headRect),
            headMode: info.headMode === 'head' ? 'head' : 'face',
            headSource: typeof info.headSource === 'string' && info.headSource
                ? info.headSource
                : null,
            bodyRect: normalizeRectLike(info.bodyRect),
            bodySource: typeof info.bodySource === 'string' && info.bodySource
                ? info.bodySource
                : null,
            reliableLive2dHeadRect: info.reliableHeadRect === true,
            preciseLive2dDisplayInfoRect: info.preciseDisplayInfoRect === true,
            coarseHitAreaHeadRect: info.coarseHitAreaHeadRect === true
        };
    }

    function hasValidRect(rect) {
        return !!(rect &&
            Number.isFinite(rect.left) &&
            Number.isFinite(rect.top) &&
            Number.isFinite(rect.width) &&
            Number.isFinite(rect.height) &&
            rect.width > 0 &&
            rect.height > 0);
    }

    function rectBottom(rect) {
        if (!rect) {
            return null;
        }
        if (Number.isFinite(rect.bottom)) {
            return rect.bottom;
        }
        if (Number.isFinite(rect.top) && Number.isFinite(rect.height)) {
            return rect.top + rect.height;
        }
        return null;
    }

    function rectRight(rect) {
        if (!rect) {
            return null;
        }
        if (Number.isFinite(rect.right)) {
            return rect.right;
        }
        if (Number.isFinite(rect.left) && Number.isFinite(rect.width)) {
            return rect.left + rect.width;
        }
        return null;
    }

    function isPlausibleLive2dAnchorInfo(anchorInfo) {
        if (!anchorInfo || anchorInfo.type !== 'live2d') {
            return false;
        }
        if (!hasValidRect(anchorInfo.bounds)) {
            return false;
        }

        var bounds = anchorInfo.bounds;
        var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        if (viewportWidth > 0 && viewportHeight > 0) {
            var maxBoundsWidth = Math.max(480, viewportWidth * 2.6);
            var maxBoundsHeight = Math.max(480, viewportHeight * 2.6);
            if (bounds.width > maxBoundsWidth || bounds.height > maxBoundsHeight) {
                return false;
            }

            var marginX = Math.max(viewportWidth, bounds.width) * 1.4;
            var marginY = Math.max(viewportHeight, bounds.height) * 1.4;
            var boundsRight = rectRight(bounds);
            var boundsBottom = rectBottom(bounds);
            if (Number.isFinite(boundsRight) && Number.isFinite(boundsBottom) &&
                (boundsRight < -marginX ||
                    bounds.left > viewportWidth + marginX ||
                    boundsBottom < -marginY ||
                    bounds.top > viewportHeight + marginY)) {
                return false;
            }
        }

        var headRect = hasValidRect(anchorInfo.headRect) ? anchorInfo.headRect : null;
        if (headRect) {
            var headRight = rectRight(headRect);
            var headBottom = rectBottom(headRect);
            var boundsRightForHead = rectRight(bounds);
            var boundsBottomForHead = rectBottom(bounds);
            var toleranceX = Math.max(32, bounds.width * 0.2);
            var toleranceY = Math.max(32, bounds.height * 0.2);
            if (!Number.isFinite(headRight) || !Number.isFinite(headBottom) ||
                !Number.isFinite(boundsRightForHead) || !Number.isFinite(boundsBottomForHead)) {
                return false;
            }
            if (headRect.left < bounds.left - toleranceX ||
                headRight > boundsRightForHead + toleranceX ||
                headRect.top < bounds.top - toleranceY ||
                headBottom > boundsBottomForHead + toleranceY ||
                headRect.width > bounds.width * 1.1 ||
                headRect.height > bounds.height * 1.02) {
                return false;
            }
        }

        if (state.visible && hasValidRect(state.lastAnchorBounds)) {
            var widthRatio = bounds.width / Math.max(1, state.lastAnchorBounds.width);
            var heightRatio = bounds.height / Math.max(1, state.lastAnchorBounds.height);
            if (widthRatio > 2.8 || widthRatio < 0.24 ||
                heightRatio > 2.8 || heightRatio < 0.24) {
                return false;
            }
        }

        return true;
    }

    function createRectUnion(rects) {
        if (!Array.isArray(rects) || rects.length === 0) {
            return null;
        }

        var minLeft = Infinity;
        var minTop = Infinity;
        var maxRight = -Infinity;
        var maxBottom = -Infinity;

        rects.forEach(function (rect) {
            if (!hasValidRect(rect)) {
                return;
            }
            var right = rectRight(rect);
            var bottom = rectBottom(rect);
            if (!Number.isFinite(right) || !Number.isFinite(bottom)) {
                return;
            }

            minLeft = Math.min(minLeft, rect.left);
            minTop = Math.min(minTop, rect.top);
            maxRight = Math.max(maxRight, right);
            maxBottom = Math.max(maxBottom, bottom);
        });

        if (!Number.isFinite(minLeft) || !Number.isFinite(minTop) ||
            !Number.isFinite(maxRight) || !Number.isFinite(maxBottom) ||
            maxRight <= minLeft || maxBottom <= minTop) {
            return null;
        }

        var width = maxRight - minLeft;
        var height = maxBottom - minTop;
        return {
            left: minLeft,
            top: minTop,
            right: maxRight,
            bottom: maxBottom,
            width: width,
            height: height,
            centerX: minLeft + width * 0.5,
            centerY: minTop + height * 0.5
        };
    }

    function getLive2dLayoutMetrics(bounds, headRect, bodyRect, reliableHeadRect, headSource) {
        var effectiveBounds = bounds;
        var bodyAwareLayout = false;

        if (hasValidRect(bodyRect)) {
            var unionRect = createRectUnion([
                reliableHeadRect ? headRect : null,
                bodyRect
            ]);
            if (unionRect) {
                effectiveBounds = unionRect;
                bodyAwareLayout = true;
            } else {
                effectiveBounds = bodyRect;
                bodyAwareLayout = true;
            }
        }

        if (!effectiveBounds) {
            return null;
        }

        var effectiveTop = effectiveBounds.top;
        var effectiveHeight = effectiveBounds.height;
        var effectiveWidth = effectiveBounds.width;
        var headWidth = effectiveWidth * 0.34;
        var headHeight = Math.min(
            effectiveHeight * TIMING.headHeightFromModelRatio,
            effectiveWidth * TIMING.headHeightFromWidthRatio
        );
        var headSpan = Math.max(headWidth, headHeight);

        if (reliableHeadRect && hasValidRect(headRect)) {
            // Bubble size should track the resolved head box. Body geometry can still
            // help with placement, but should not inflate or shrink the bubble itself.
            headWidth = headRect.width;
            headHeight = headRect.height;
            headSpan = Math.max(headWidth, headHeight);
        } else if (bodyAwareLayout) {
            headWidth = Math.max(effectiveWidth * 0.46, effectiveHeight * 0.22);
            headHeight = Math.max(effectiveHeight * 0.36, effectiveWidth * 0.34);
            headSpan = Math.max(headWidth, headHeight);
        }

        return {
            effectiveBounds: effectiveBounds,
            effectiveTop: effectiveTop,
            effectiveHeight: effectiveHeight,
            effectiveWidth: effectiveWidth,
            headWidth: headWidth,
            headHeight: headHeight,
            headSpan: headSpan,
            bodyAwareLayout: bodyAwareLayout
        };
    }

    function getLive2dHeadAnchorFromRect(headRect, headMode, headSource) {
        if (!hasValidRect(headRect)) {
            return null;
        }

        var faceAnchorRatio = headSource === 'displayInfo'
            ? TIMING.live2dDisplayInfoFaceAnchorRatio
            : 0.42;
        var headAnchorRatio = headSource === 'displayInfo'
            ? TIMING.live2dDisplayInfoHeadAnchorRatio
            : 0.5;

        return {
            x: Number.isFinite(headRect.centerX) ? headRect.centerX : headRect.left + headRect.width * 0.5,
            y: headRect.top + headRect.height * (headMode === 'face' ? faceAnchorRatio : headAnchorRatio)
        };
    }

    function isReliableLive2dHeadRect(headRect, bounds, bodyRect, headSource) {
        if (!hasValidRect(headRect) || !bounds) {
            return false;
        }

        var boundsCenterY = Number.isFinite(headRect.centerY) ? headRect.centerY : headRect.top + headRect.height * 0.5;
        var boundsRight = Number.isFinite(bounds.right) ? bounds.right : bounds.left + bounds.width;
        var boundsBottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;
        if (headSource === 'displayInfo') {
            var toleranceX = Math.max(18, bounds.width * 0.08);
            var toleranceY = Math.max(18, bounds.height * 0.08);
            if (headRect.left < bounds.left - toleranceX ||
                headRect.right > boundsRight + toleranceX ||
                headRect.top < bounds.top - toleranceY ||
                headRect.bottom > boundsBottom + toleranceY ||
                headRect.width > bounds.width * 0.98 ||
                headRect.height > bounds.height * 0.88) {
                return false;
            }

            if (!hasValidRect(bodyRect)) {
                return true;
            }

            var bodyCenterY = Number.isFinite(bodyRect.centerY) ? bodyRect.centerY : bodyRect.top + bodyRect.height * 0.5;
            return boundsCenterY <= bodyRect.bottom &&
                headRect.top <= bodyCenterY &&
                headRect.height <= bodyRect.height * 1.12;
        }

        if (headRect.width > bounds.width * TIMING.live2dHeadMaxBoundsWidthRatio ||
            headRect.height > bounds.height * TIMING.live2dHeadMaxBoundsHeightRatio ||
            boundsCenterY > bounds.top + bounds.height * TIMING.live2dHeadMaxBoundsCenterYRatio) {
            return false;
        }

        if (!hasValidRect(bodyRect)) {
            return true;
        }

        return headRect.width <= bodyRect.width * TIMING.live2dHeadMaxBodyWidthRatio &&
            headRect.height <= bodyRect.height * TIMING.live2dHeadMaxBodyHeightRatio &&
            boundsCenterY <= bodyRect.top + bodyRect.height * TIMING.live2dHeadMaxBodyCenterYRatio;
    }

    function getActiveAvatarBubbleAnchor() {
        var mmdBounds = isContainerVisible('mmd-container')
            ? getBoundsFromManager(window.mmdManager, 'getModelScreenBounds')
            : null;
        if (mmdBounds) {
            return {
                type: 'mmd',
                bounds: mmdBounds,
                head: getHeadAnchorFromManager(window.mmdManager)
            };
        }

        var vrmBounds = isContainerVisible('vrm-container')
            ? getBoundsFromManager(window.vrmManager, 'getModelScreenBounds')
            : null;
        if (vrmBounds) {
            return {
                type: 'vrm',
                bounds: vrmBounds,
                head: getHeadAnchorFromManager(window.vrmManager)
            };
        }

        var live2dBounds = isContainerVisible('live2d-container')
            ? getBoundsFromManager(window.live2dManager, 'getModelScreenBounds')
            : null;
        if (live2dBounds) {
            var live2dGeometryInfo = getLive2dBubbleGeometryInfoFromManager(window.live2dManager);
            if (live2dGeometryInfo && live2dGeometryInfo.bounds) {
                return {
                    type: 'live2d',
                    bounds: live2dGeometryInfo.bounds,
                    head: live2dGeometryInfo.rawHeadAnchor || live2dGeometryInfo.headAnchor || null,
                    live2dHeadAnchor: live2dGeometryInfo.headAnchor || null,
                    headRect: live2dGeometryInfo.headRect,
                    headMode: live2dGeometryInfo.headMode,
                    headSource: live2dGeometryInfo.headSource,
                    bodyRect: live2dGeometryInfo.bodyRect,
                    bodySource: live2dGeometryInfo.bodySource,
                    reliableLive2dHeadRect: live2dGeometryInfo.reliableLive2dHeadRect === true,
                    preciseLive2dDisplayInfoRect: live2dGeometryInfo.preciseLive2dDisplayInfoRect === true,
                    coarseHitAreaHeadRect: live2dGeometryInfo.coarseHitAreaHeadRect === true,
                    hasNormalizedLive2dGeometry: true
                };
            }

            var live2dHeadRectInfo = getHeadRectInfoFromManager(window.live2dManager);
            var live2dBodyRectInfo = getBodyRectInfoFromManager(window.live2dManager);
            return {
                type: 'live2d',
                bounds: live2dBounds,
                head: getHeadAnchorFromManager(window.live2dManager),
                headRect: live2dHeadRectInfo ? live2dHeadRectInfo.rect : null,
                headMode: live2dHeadRectInfo ? live2dHeadRectInfo.mode : null,
                headSource: live2dHeadRectInfo ? live2dHeadRectInfo.source : null,
                bodyRect: live2dBodyRectInfo ? live2dBodyRectInfo.rect : null,
                bodySource: live2dBodyRectInfo ? live2dBodyRectInfo.source : null
            };
        }

        return null;
    }

    function getActiveAvatarContainer() {
        if (isContainerVisible('mmd-container')) {
            return document.getElementById('mmd-container');
        }
        if (isContainerVisible('vrm-container')) {
            return document.getElementById('vrm-container');
        }
        if (isContainerVisible('live2d-container')) {
            return document.getElementById('live2d-container');
        }
        return null;
    }

    function isEventInsideActiveAvatar(event) {
        var container = getActiveAvatarContainer();
        var target = event && event.target;
        return !!(container && target && typeof container.contains === 'function' && container.contains(target));
    }

    function updatePosition() {
        if (!state.visible) {
            syncDebugOverlaySnapshot();
            return;
        }

        var anchorInfo = getActiveAvatarBubbleAnchor();
        if (anchorInfo && anchorInfo.type === 'live2d' && !isPlausibleLive2dAnchorInfo(anchorInfo)) {
            anchorInfo = null;
        }
        if (anchorInfo && anchorInfo.bounds) {
            state.lastAnchorType = anchorInfo.type || null;
            state.lastAnchorBounds = cloneBounds(anchorInfo.bounds);
            state.lastHeadAnchor = clonePoint(anchorInfo.head);
            state.lastLive2dHeadAnchor = clonePoint(anchorInfo.live2dHeadAnchor);
            state.lastHeadRect = cloneBounds(anchorInfo.headRect);
            state.lastHeadMode = anchorInfo.headMode || null;
            state.lastHeadSource = anchorInfo.headSource || null;
            state.lastBodyRect = cloneBounds(anchorInfo.bodyRect);
            state.lastBodySource = anchorInfo.bodySource || null;
            state.lastReliableLive2dHeadRect = typeof anchorInfo.reliableLive2dHeadRect === 'boolean'
                ? anchorInfo.reliableLive2dHeadRect
                : null;
            state.lastPreciseLive2dDisplayInfoRect = typeof anchorInfo.preciseLive2dDisplayInfoRect === 'boolean'
                ? anchorInfo.preciseLive2dDisplayInfoRect
                : null;
            state.lastCoarseHitAreaHeadRect = typeof anchorInfo.coarseHitAreaHeadRect === 'boolean'
                ? anchorInfo.coarseHitAreaHeadRect
                : null;
        } else if (!state.lastAnchorBounds) {
            return;
        }

        ensureDom();

        var avatarType = anchorInfo && anchorInfo.bounds ? anchorInfo.type : state.lastAnchorType;
        var bounds = anchorInfo && anchorInfo.bounds ? anchorInfo.bounds : state.lastAnchorBounds;
        var headAnchor = anchorInfo && anchorInfo.bounds ? anchorInfo.head : state.lastHeadAnchor;
        var live2dHeadAnchor = anchorInfo && anchorInfo.bounds ? anchorInfo.live2dHeadAnchor : state.lastLive2dHeadAnchor;
        var headRect = anchorInfo && anchorInfo.bounds ? anchorInfo.headRect : state.lastHeadRect;
        var headMode = anchorInfo && anchorInfo.bounds ? anchorInfo.headMode : state.lastHeadMode;
        var headSource = anchorInfo && anchorInfo.bounds ? anchorInfo.headSource : state.lastHeadSource;
        var bodyRect = anchorInfo && anchorInfo.bounds ? anchorInfo.bodyRect : state.lastBodyRect;
        var bodySource = anchorInfo && anchorInfo.bounds ? anchorInfo.bodySource : state.lastBodySource;
        var reliableLive2dHeadRect = avatarType === 'live2d'
            ? ((anchorInfo && anchorInfo.bounds && typeof anchorInfo.reliableLive2dHeadRect === 'boolean')
                ? anchorInfo.reliableLive2dHeadRect
                : (state.lastReliableLive2dHeadRect !== null
                    ? state.lastReliableLive2dHeadRect
                    : isReliableLive2dHeadRect(headRect, bounds, bodyRect, headSource)))
            : false;
        var preciseLive2dDisplayInfoRect = avatarType === 'live2d'
            ? ((anchorInfo && anchorInfo.bounds && typeof anchorInfo.preciseLive2dDisplayInfoRect === 'boolean')
                ? anchorInfo.preciseLive2dDisplayInfoRect
                : (state.lastPreciseLive2dDisplayInfoRect !== null
                    ? state.lastPreciseLive2dDisplayInfoRect
                    : (reliableLive2dHeadRect && headSource === 'displayInfo')))
            : false;
        var coarseHitAreaHeadRect = avatarType === 'live2d'
            ? ((anchorInfo && anchorInfo.bounds && typeof anchorInfo.coarseHitAreaHeadRect === 'boolean')
                ? anchorInfo.coarseHitAreaHeadRect
                : (state.lastCoarseHitAreaHeadRect !== null
                    ? state.lastCoarseHitAreaHeadRect
                    : (headSource === 'hitArea' &&
                        hasValidRect(headRect) &&
                        headAnchor &&
                        Number.isFinite(headAnchor.y) &&
                        headAnchor.y >= headRect.top + headRect.height * 0.82)))
            : false;
        if (avatarType === 'live2d' && !live2dHeadAnchor && reliableLive2dHeadRect) {
            live2dHeadAnchor = getLive2dHeadAnchorFromRect(headRect, headMode, headSource) || headAnchor;
        }
        if (coarseHitAreaHeadRect && headAnchor) {
            live2dHeadAnchor = headAnchor;
        }
        var live2dLayoutMetrics = avatarType === 'live2d'
            ? getLive2dLayoutMetrics(bounds, headRect, bodyRect, reliableLive2dHeadRect, headSource)
            : null;
        var layoutBounds = live2dLayoutMetrics && live2dLayoutMetrics.effectiveBounds
            ? live2dLayoutMetrics.effectiveBounds
            : bounds;
        var boundsCenterX = Number.isFinite(bounds.centerX) ? bounds.centerX : (bounds.left + bounds.right) * 0.5;
        var boundsCenterY = Number.isFinite(bounds.centerY) ? bounds.centerY : (bounds.top + bounds.bottom) * 0.5;
        var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        var margin = TIMING.edgeMarginPx;
        var rawHeadHeight = layoutBounds.height * TIMING.headHeightFromModelRatio;
        var cappedHeadHeight = layoutBounds.width * TIMING.headHeightFromWidthRatio;
        var accessoryOvershootPx = Math.max(0, rawHeadHeight - cappedHeadHeight);
        var accessoryTrimPx = Math.min(
            TIMING.accessoryTrimMaxPx,
            accessoryOvershootPx * TIMING.accessoryTrimRatio
        );
        var effectiveTop = (live2dLayoutMetrics ? live2dLayoutMetrics.effectiveTop : layoutBounds.top) + accessoryTrimPx;
        var effectiveHeight = Math.max(
            (live2dLayoutMetrics ? live2dLayoutMetrics.effectiveHeight : layoutBounds.height) - accessoryTrimPx,
            cappedHeadHeight * 2
        );
        var headWidth = live2dLayoutMetrics ? live2dLayoutMetrics.headWidth : layoutBounds.width * 0.34;
        var headHeight = live2dLayoutMetrics ? live2dLayoutMetrics.headHeight : Math.min(effectiveHeight * TIMING.headHeightFromModelRatio, cappedHeadHeight);
        var headSpan = live2dLayoutMetrics ? live2dLayoutMetrics.headSpan : Math.max(headWidth, headHeight);
        if (avatarType === 'live2d' &&
            preciseLive2dDisplayInfoRect &&
            reliableLive2dHeadRect &&
            hasValidRect(headRect) &&
            bodySource === 'drawableHeuristic') {
            headSpan = Math.min(
                headSpan,
                Math.max(headRect.width, headRect.height) * 1.24
            );
        } else if (avatarType === 'live2d' &&
            preciseLive2dDisplayInfoRect &&
            reliableLive2dHeadRect &&
            hasValidRect(headRect) &&
            hasValidRect(bodyRect)) {
            headSpan = Math.min(
                headSpan,
                Math.max(headRect.width, headRect.height) * TIMING.live2dDisplayInfoBodyAwareHeadSpanMaxMultiplier
            );
        } else if (avatarType === 'live2d' &&
            preciseLive2dDisplayInfoRect &&
            reliableLive2dHeadRect &&
            hasValidRect(headRect) &&
            !hasValidRect(bodyRect)) {
            headSpan = Math.min(
                headSpan,
                Math.max(headRect.width, headRect.height) * 2.1
            );
        }
        var viewportCap = Math.round(Math.min(viewportWidth, viewportHeight) * 0.42);
        var headBubbleScaleMultiplier = TIMING.headBubbleScaleMultiplier;
        if (avatarType === 'live2d' &&
            reliableLive2dHeadRect &&
            hasValidRect(headRect)) {
            headBubbleScaleMultiplier = headSource === 'drawableHeuristic'
                ? TIMING.live2dDrawableHeadBubbleScaleMultiplier
                : TIMING.live2dReliableHeadBubbleScaleMultiplier;
        }
        var headSize = Math.max(
            96,
            Math.min(
                viewportCap,
                Math.round(headSpan * 1.38 * headBubbleScaleMultiplier)
            )
        );
        var width = Math.max(96, Math.round(headSize * 1.34) - TIMING.baseWidthShrinkPx);
        var height = Math.max(74, Math.round(headSize * 1.02) - TIMING.baseHeightShrinkPx);
        var useReliableLive2dHeadCenterX = avatarType === 'live2d' &&
            reliableLive2dHeadRect &&
            hasValidRect(headRect) &&
            headSource !== 'hitArea' &&
            Number.isFinite(headRect.centerX);
        var headCenterX = useReliableLive2dHeadCenterX
            ? headRect.centerX
            : bounds.left + bounds.width * 0.5;
        var sideOffsetPx = layoutBounds.width * 0.13;
        if (useReliableLive2dHeadCenterX) {
            sideOffsetPx = clamp(
                sideOffsetPx,
                headRect.width * TIMING.live2dHeadSideOffsetMinHeadWidthRatio,
                headRect.width * TIMING.live2dHeadSideOffsetMaxHeadWidthRatio
            );
        }
        var rightAnchorX = headCenterX + sideOffsetPx;
        var leftAnchorX = headCenterX - sideOffsetPx;
        var modelAspectRatio = effectiveHeight / Math.max(layoutBounds.width, 1);
        var modelShapeProgress = clamp(
            (modelAspectRatio - TIMING.compactModelAspectRatio) / (TIMING.tallModelAspectRatio - TIMING.compactModelAspectRatio),
            0,
            1
        );
        var headAnchorRatio = lerp(
            TIMING.shortHeadAnchorRatio,
            TIMING.tallHeadAnchorRatio,
            modelShapeProgress
        );
        var modelOffsetRatio = lerp(
            TIMING.shortModelOffsetRatio,
            TIMING.tallModelOffsetRatio,
            modelShapeProgress
        );
        if (avatarType === 'live2d' && live2dLayoutMetrics && live2dLayoutMetrics.bodyAwareLayout) {
            modelOffsetRatio = Math.max(modelOffsetRatio, -0.12);
        }
        if (avatarType === 'live2d' &&
            preciseLive2dDisplayInfoRect &&
            reliableLive2dHeadRect &&
            hasValidRect(headRect) &&
            !hasValidRect(bodyRect)) {
            modelOffsetRatio = Math.max(modelOffsetRatio, -0.06);
        }
        if (coarseHitAreaHeadRect || (avatarType === 'live2d' &&
            !reliableLive2dHeadRect &&
            headSource === 'hitArea' &&
            live2dHeadAnchor &&
            Number.isFinite(live2dHeadAnchor.y))) {
            modelOffsetRatio = Math.max(modelOffsetRatio, 0);
        }
        var fallbackAnchorY = effectiveTop + headHeight * headAnchorRatio;
        var headAnchorCorrectionPx = 0;
        var headAnchorForCorrection = live2dHeadAnchor || headAnchor;
        if (headAnchorForCorrection) {
            var anchorDelta = headAnchorForCorrection.y - fallbackAnchorY;
            if (preciseLive2dDisplayInfoRect) {
                if (Math.abs(anchorDelta) > TIMING.headAnchorCorrectionDeadzonePx) {
                    headAnchorCorrectionPx = clamp(
                        anchorDelta,
                        -TIMING.headAnchorCorrectionMaxPx,
                        TIMING.headAnchorCorrectionMaxPx
                    ) * TIMING.headAnchorCorrectionRatio;
                }
            } else {
                headAnchorCorrectionPx = clamp(
                    Math.max(0, anchorDelta) - TIMING.headAnchorCorrectionDeadzonePx,
                    0,
                    TIMING.headAnchorCorrectionMaxPx
                ) * TIMING.headAnchorCorrectionRatio;
            }
        }
        var anchorY = fallbackAnchorY + headAnchorCorrectionPx;
        if (preciseLive2dDisplayInfoRect && live2dHeadAnchor && Number.isFinite(live2dHeadAnchor.y)) {
            anchorY = live2dHeadAnchor.y;
        } else if (live2dHeadAnchor && Number.isFinite(live2dHeadAnchor.y)) {
            anchorY = Math.max(anchorY, live2dHeadAnchor.y);
        }

        if (avatarType === 'live2d' &&
            reliableLive2dHeadRect &&
            Number.isFinite(state.lastRenderWidth) &&
            Number.isFinite(state.lastRenderHeight)) {
            var widthDeadzonePx = Math.max(
                TIMING.live2dSizeDeadzonePx,
                state.lastRenderWidth * TIMING.live2dSizeDeadzoneRatio
            );
            var heightDeadzonePx = Math.max(
                TIMING.live2dSizeDeadzonePx,
                state.lastRenderHeight * TIMING.live2dSizeDeadzoneRatio
            );
            if (Math.abs(width - state.lastRenderWidth) < widthDeadzonePx) {
                width = state.lastRenderWidth;
            }
            if (Math.abs(height - state.lastRenderHeight) < heightDeadzonePx) {
                height = state.lastRenderHeight;
            }
        }

        if (state.lastRenderWidth === null || Math.abs(state.lastRenderWidth - width) >= TIMING.sizeSnapPx) {
            bubbleEl.style.setProperty('--bubble-width', width + 'px');
            state.lastRenderWidth = width;
        }
        if (state.lastRenderHeight === null || Math.abs(state.lastRenderHeight - height) >= TIMING.sizeSnapPx) {
            bubbleEl.style.setProperty('--bubble-height', height + 'px');
            state.lastRenderHeight = height;
        }

        var tailInset = Math.round(width * -0.06);
        var overlapPx = Math.round(width * TIMING.modelOverlapRatio);
        var preferredRightX = rightAnchorX - tailInset - overlapPx;
        var preferredLeftX = leftAnchorX - width + tailInset + overlapPx;
        var rightFits = preferredRightX + width <= viewportWidth - margin;
        var leftFits = preferredLeftX >= margin;
        var accessoryDropPx = Math.min(
            TIMING.accessoryDropMaxPx,
            TIMING.accessoryDropBasePx + accessoryOvershootPx * TIMING.accessoryDropRatio
        );
        var topY = anchorY - height * 0.5 + headSize * modelOffsetRatio + accessoryDropPx + TIMING.verticalOffsetPx;
        var live2dTopTargetY = null;
        var live2dTopTargetActsAsCeiling = false;
        if (avatarType === 'live2d') {
            if (reliableLive2dHeadRect) {
                if (preciseLive2dDisplayInfoRect) {
                    var live2dDisplayInfoGapPx = Math.max(
                        headRect.height * TIMING.live2dDisplayInfoGapHeadRatio,
                        height * TIMING.live2dDisplayInfoGapBubbleRatio,
                        hasValidRect(bodyRect) ? bodyRect.height * TIMING.live2dDisplayInfoGapBodyRatio : 0
                    );
                    live2dTopTargetY = headRect.top - height * (headMode === 'face'
                        ? TIMING.live2dDisplayInfoTopOffsetRatio
                        : TIMING.live2dDisplayInfoHeadTopOffsetRatio) - live2dDisplayInfoGapPx;
                    live2dTopTargetActsAsCeiling = true;
                } else {
                    live2dTopTargetY = headRect.top - height * (headMode === 'face'
                        ? TIMING.live2dFaceTopOffsetRatio
                        : TIMING.live2dHeadTopOffsetRatio);
                    if (headSource === 'drawableHeuristic') {
                        live2dTopTargetActsAsCeiling = true;
                    }
                }
            } else if (hasValidRect(bodyRect)) {
                live2dTopTargetY = bodyRect.top - Math.max(
                    height * TIMING.live2dBodyProxyBubbleLiftRatio,
                    bodyRect.height * TIMING.live2dBodyProxyBodyLiftRatio,
                    headSize * TIMING.live2dBodyProxyHeadLiftRatio
                );
                live2dTopTargetActsAsCeiling = true;
            }
            if (Number.isFinite(live2dTopTargetY)) {
                topY = live2dTopTargetActsAsCeiling
                    ? Math.min(topY, live2dTopTargetY)
                    : Math.max(topY, live2dTopTargetY);
            }
        }
        var y = Math.max(margin, Math.min(topY, viewportHeight - height - margin));
        var side = 'right';
        var x = preferredRightX;

        if (!rightFits && leftFits) {
            side = 'left';
            x = preferredLeftX;
        }

        if (side === 'right' && !rightFits && leftFits) {
            side = 'left';
            x = preferredLeftX;
        } else if (side === 'left' && !leftFits && rightFits) {
            side = 'right';
            x = preferredRightX;
        }

        if (!rightFits && !leftFits) {
            var rightOverflow = Math.max(0, preferredRightX + width - (viewportWidth - margin));
            var leftOverflow = Math.max(0, margin - preferredLeftX);
            if (leftOverflow < rightOverflow) {
                side = 'left';
                x = preferredLeftX;
            } else {
                side = 'right';
                x = preferredRightX;
            }
        }

        x = Math.max(margin, Math.min(x, viewportWidth - width - margin));
        state.side = side;
        state.anchorX = side === 'left' ? leftAnchorX : rightAnchorX;
        state.anchorY = anchorY;

        var roundedX = Math.round(x);
        var roundedY = Math.round(y);
        var shouldLockHorizontalDrift = state.lastBoundsCenterX !== null &&
            state.lastBoundsCenterY !== null &&
            state.lastRenderX !== null &&
            Math.abs(boundsCenterY - state.lastBoundsCenterY) >= TIMING.verticalMoveLockThresholdPx &&
            Math.abs(boundsCenterX - state.lastBoundsCenterX) <= TIMING.horizontalNoiseTolerancePx &&
            Math.abs(roundedX - state.lastRenderX) <= TIMING.verticalMoveMaxHorizontalDriftPx;
        var shouldLockVerticalDrift = state.lastBoundsCenterX !== null &&
            state.lastBoundsCenterY !== null &&
            state.lastRenderY !== null &&
            Math.abs(boundsCenterX - state.lastBoundsCenterX) >= TIMING.horizontalMoveLockThresholdPx &&
            Math.abs(boundsCenterY - state.lastBoundsCenterY) <= TIMING.verticalNoiseTolerancePx &&
            Math.abs(roundedY - state.lastRenderY) <= TIMING.horizontalMoveMaxVerticalDriftPx;

        if (shouldLockHorizontalDrift) {
            roundedX = state.lastRenderX;
        }
        if (shouldLockVerticalDrift) {
            roundedY = state.lastRenderY;
        }
        if (avatarType === 'live2d' &&
            reliableLive2dHeadRect &&
            state.lastRenderX !== null &&
            Math.abs(roundedX - state.lastRenderX) < TIMING.live2dMicroMoveDeadzonePx) {
            roundedX = state.lastRenderX;
        }
        if (avatarType === 'live2d' &&
            reliableLive2dHeadRect &&
            state.lastRenderY !== null &&
            Math.abs(roundedY - state.lastRenderY) < TIMING.live2dMicroMoveDeadzonePx) {
            roundedY = state.lastRenderY;
        }

        if (avatarType === 'live2d' && bubblePositionDebugEnabled()) {
            var live2dDebugInfo = getLive2dBubbleDebugInfoFromManager(window.live2dManager);
            var live2dDebugSnapshot = {
                model: live2dDebugInfo ? (live2dDebugInfo.modelName || live2dDebugInfo.modelRootPath || 'live2d') : 'live2d',
                headSource: headSource || null,
                headMode: headMode || null,
                reliableLive2dHeadRect: reliableLive2dHeadRect,
                preciseLive2dDisplayInfoRect: preciseLive2dDisplayInfoRect,
                coarseHitAreaHeadRect: coarseHitAreaHeadRect,
                bounds: createDebugRect(bounds),
                headRect: createDebugRect(headRect),
                bodyRect: createDebugRect(bodyRect),
                layoutBounds: createDebugRect(layoutBounds),
                headAnchor: createDebugPoint(headAnchor),
                live2dHeadAnchor: createDebugPoint(live2dHeadAnchor),
                anchorY: roundDebugNumber(anchorY),
                live2dTopTargetY: roundDebugNumber(live2dTopTargetY),
                bubbleSize: {
                    width: roundDebugNumber(width),
                    height: roundDebugNumber(height)
                },
                layout: {
                    bodyAware: !!(live2dLayoutMetrics && live2dLayoutMetrics.bodyAwareLayout),
                    headSpan: roundDebugNumber(headSpan)
                },
                preferred: {
                    left: roundDebugNumber(preferredLeftX),
                    right: roundDebugNumber(preferredRightX)
                },
                fits: {
                    left: !!leftFits,
                    right: !!rightFits
                },
                final: {
                    side: side,
                    x: roundDebugNumber(roundedX),
                    y: roundDebugNumber(roundedY)
                },
                anchor: createDebugPoint({
                    x: state.anchorX,
                    y: state.anchorY
                }),
                bubbleRect: createDebugRect({
                    left: roundedX,
                    top: roundedY,
                    right: roundedX + width,
                    bottom: roundedY + height,
                    width: width,
                    height: height,
                    centerX: roundedX + width * 0.5,
                    centerY: roundedY + height * 0.5
                }),
                manager: live2dDebugInfo ? {
                    displayInfoLoaded: !!live2dDebugInfo.displayInfoLoaded,
                    displayInfoPath: live2dDebugInfo.displayInfoPath || null,
                    headInfo: live2dDebugInfo.headInfo ? {
                        source: live2dDebugInfo.headInfo.source || null,
                        mode: live2dDebugInfo.headInfo.mode || null,
                        rect: createDebugRect(live2dDebugInfo.headInfo.rect)
                    } : null,
                    bodyInfo: live2dDebugInfo.bodyInfo ? {
                        source: live2dDebugInfo.bodyInfo.source || null,
                        mode: live2dDebugInfo.bodyInfo.mode || null,
                        rect: createDebugRect(live2dDebugInfo.bodyInfo.rect)
                        } : null,
                    hitAreas: Array.isArray(live2dDebugInfo.hitAreas) ? live2dDebugInfo.hitAreas : []
                } : null
            };
            state.lastDebugSnapshot = live2dDebugSnapshot;
            logBubblePosition(live2dDebugSnapshot);
            renderDebugOverlay(live2dDebugSnapshot);
        } else {
            state.lastDebugSnapshot = {
                model: avatarType || 'unknown',
                headSource: headSource || null,
                headMode: headMode || null,
                reliableLive2dHeadRect: !!reliableLive2dHeadRect,
                preciseLive2dDisplayInfoRect: !!preciseLive2dDisplayInfoRect,
                coarseHitAreaHeadRect: !!coarseHitAreaHeadRect,
                bounds: createDebugRect(bounds),
                headRect: createDebugRect(headRect),
                bodyRect: createDebugRect(bodyRect),
                layoutBounds: createDebugRect(layoutBounds),
                anchor: createDebugPoint({
                    x: state.anchorX,
                    y: state.anchorY
                }),
                bubbleRect: createDebugRect({
                    left: roundedX,
                    top: roundedY,
                    right: roundedX + width,
                    bottom: roundedY + height,
                    width: width,
                    height: height,
                    centerX: roundedX + width * 0.5,
                    centerY: roundedY + height * 0.5
                }),
                final: {
                    side: side,
                    x: roundDebugNumber(roundedX),
                    y: roundDebugNumber(roundedY)
                }
            };
            renderDebugOverlay(state.lastDebugSnapshot);
        }

        bubbleEl.dataset.side = side;
        if (state.lastRenderX === null || Math.abs(state.lastRenderX - roundedX) >= TIMING.positionSnapPx) {
            bubbleEl.style.left = roundedX + 'px';
            state.lastRenderX = roundedX;
        }
        if (state.lastRenderY === null || Math.abs(state.lastRenderY - roundedY) >= TIMING.positionSnapPx) {
            bubbleEl.style.top = roundedY + 'px';
            state.lastRenderY = roundedY;
        }
        state.lastBoundsCenterX = boundsCenterX;
        state.lastBoundsCenterY = boundsCenterY;
    }

    function extendFollowLoop(durationMs) {
        if (!state.visible) {
            return;
        }

        var duration = Math.max(0, Number(durationMs) || 0);
        if (duration <= 0) {
            return;
        }

        state.followUntilAt = Math.max(state.followUntilAt, perfNow() + duration);
        if (state.followRafId) {
            return;
        }

        var tick = function () {
            state.followRafId = 0;
            if (!state.visible) {
                state.followUntilAt = 0;
                return;
            }

            updatePosition();
            if (state.followUntilAt > perfNow()) {
                state.followRafId = requestAnimationFrame(tick);
            } else {
                state.followUntilAt = 0;
            }
        };

        state.followRafId = requestAnimationFrame(tick);
    }

    function syncPositionOnce() {
        if (!state.visible) {
            return;
        }
        updatePosition();
    }

    function keepFollowingWhileVisible() {
        extendFollowLoop(TIMING.visibleFollowWindowMs);
    }

    function scheduleInteractionSyncBurst(delayMs) {
        if (!state.visible) {
            return;
        }

        clearInteractionSync();
        syncPositionOnce();
        state.interactionSyncRafId = requestAnimationFrame(function () {
            state.interactionSyncRafId = 0;
            syncPositionOnce();
        });
        state.interactionSyncTimerId = window.setTimeout(function () {
            state.interactionSyncTimerId = 0;
            syncPositionOnce();
        }, Math.max(0, Number(delayMs) || TIMING.wheelResyncDelayMs));
    }

    function handleAvatarPointerDown(event) {
        if (!state.visible || !isEventInsideActiveAvatar(event)) {
            return;
        }
        state.isAvatarPointerActive = true;
        syncPositionOnce();
        extendFollowLoop(TIMING.moveFollowWindowMs);
    }

    function handleAvatarPointerMove() {
        if (!state.visible || !state.isAvatarPointerActive) {
            return;
        }
        extendFollowLoop(TIMING.moveFollowWindowMs);
    }

    function handleAvatarPointerEnd() {
        if (!state.isAvatarPointerActive) {
            return;
        }
        state.isAvatarPointerActive = false;
        if (state.visible) {
            extendFollowLoop(TIMING.moveSettleWindowMs);
        }
    }

    function handleAvatarWheel(event) {
        if (!state.visible || !isEventInsideActiveAvatar(event)) {
            return;
        }
        scheduleInteractionSyncBurst(TIMING.wheelResyncDelayMs);
        extendFollowLoop(Math.max(TIMING.moveSettleWindowMs, TIMING.wheelFollowWindowMs));
    }

    function handleModelLoaded() {
        if (!state.visible) {
            return;
        }
        syncPositionOnce();
        keepFollowingWhileVisible();
    }

    function forceHide(resetTurn) {
        logBubbleLifecycle('forceHide:enter', { resetTurn: resetTurn });
        clearTurnTimers();
        clearInteractionSync();
        stopFollowLoop();
        state.visible = false;
        state.phase = 'idle';
        state.theme = 'thinking';
        state.emotion = null;
        state.showEmotionArt = false;
        state.content = '';
        state.turnEndedAt = 0;
        state.speechStartedAt = 0;
        state.lastRenderX = null;
        state.lastRenderY = null;
        state.lastRenderWidth = null;
        state.lastRenderHeight = null;
        state.lastAnchorType = null;
        state.lastAnchorBounds = null;
        state.lastHeadAnchor = null;
        state.lastHeadRect = null;
        state.lastHeadMode = null;
        state.lastHeadSource = null;
        state.lastBodyRect = null;
        state.lastBoundsCenterX = null;
        state.lastBoundsCenterY = null;
        state.lastDebugSnapshot = null;
        if (resetTurn !== false) {
            state.turnId = null;
        }
        applyVisualState();
        syncDebugOverlaySnapshot();
    }

    function scheduleThinkingTimeout(turnId) {
        clearTimer('timeoutTimerId');
        state.timeoutTimerId = window.setTimeout(function () {
            if (state.turnId !== turnId || state.speechStartedAt > 0 || state.phase !== 'thinking') {
                return;
            }
            beginHide(turnId, 0);
        }, TIMING.maxThinkingMs);
    }

    function scheduleMaxVisibleFallback(turnId) {
        clearTimer('maxVisibleTimerId');
        if (!turnId || !state.visible) {
            return;
        }

        var remainingMs = Math.max(0, TIMING.maxVisibleMs - Math.max(0, now() - state.shownAt));
        if (remainingMs <= 0) {
            logBubbleLifecycle('scheduleMaxVisibleFallback:force_hide_immediate', {
                requestedTurnId: turnId,
                elapsedMs: now() - state.shownAt
            });
            forceHide(true);
            return;
        }

        state.maxVisibleTimerId = window.setTimeout(function () {
            if (state.turnId !== turnId || !state.visible) {
                return;
            }
            logBubbleLifecycle('scheduleMaxVisibleFallback:force_hide', {
                requestedTurnId: turnId,
                elapsedMs: now() - state.shownAt
            });
            forceHide(true);
        }, remainingMs);
    }

    function beginHide(turnId, extraHoldMs) {
        var normalizedTurnId = normalizeTurnId(turnId);
        logBubbleLifecycle('beginHide:enter', {
            requestedTurnId: normalizedTurnId,
            extraHoldMs: extraHoldMs || 0
        });
        if (normalizedTurnId && state.turnId !== normalizedTurnId) {
            logBubbleLifecycle('beginHide:skip_turn_mismatch', {
                requestedTurnId: normalizedTurnId
            });
            return;
        }

        clearTimer('hideTimerId');
        clearTimer('textFallbackTimerId');
        clearTimer('timeoutTimerId');
        clearTimer('maxVisibleTimerId');

        if (!state.visible) {
            forceHide(true);
            return;
        }

        var elapsed = now() - state.shownAt;
        var preFadeDelay = Math.max(0, TIMING.minVisibleMs - elapsed) + Math.max(0, extraHoldMs || 0);

        state.hideTimerId = window.setTimeout(function () {
            if (normalizedTurnId && state.turnId !== normalizedTurnId) {
                logBubbleLifecycle('beginHide:skip_pre_fade_turn_mismatch', {
                    requestedTurnId: normalizedTurnId
                });
                return;
            }
            state.phase = 'fading';
            logBubbleLifecycle('beginHide:phase_fading', {
                requestedTurnId: normalizedTurnId
            });
            applyVisualState();

            state.hideTimerId = window.setTimeout(function () {
                if (normalizedTurnId && state.turnId !== normalizedTurnId) {
                    logBubbleLifecycle('beginHide:skip_force_hide_turn_mismatch', {
                        requestedTurnId: normalizedTurnId
                    });
                    return;
                }
                logBubbleLifecycle('beginHide:force_hide', {
                    requestedTurnId: normalizedTurnId
                });
                forceHide(true);
            }, TIMING.fadeDurationMs);
        }, preFadeDelay);
    }

    function scheduleTextFallbackHide(turnId) {
        clearTimer('textFallbackTimerId');
        logBubbleLifecycle('scheduleTextFallbackHide:scheduled', {
            requestedTurnId: turnId,
            delayMs: TIMING.textOnlyFallbackMs
        });
        state.textFallbackTimerId = window.setTimeout(function () {
            logBubbleLifecycle('scheduleTextFallbackHide:fired', {
                requestedTurnId: turnId
            });
            if (state.turnId !== turnId || state.speechStartedAt > 0) {
                logBubbleLifecycle('scheduleTextFallbackHide:skip', {
                    requestedTurnId: turnId
                });
                return;
            }
            beginHide(turnId, 0);
        }, TIMING.textOnlyFallbackMs);
    }

    function applyEmotionFallback(turnId) {
        if (state.turnId !== turnId || !state.visible || state.phase === 'fading') {
            logBubbleLifecycle('applyEmotionFallback:skip', {
                requestedTurnId: turnId
            });
            return;
        }

        if (state.theme !== 'thinking') {
            logBubbleLifecycle('applyEmotionFallback:skip_non_thinking', {
                requestedTurnId: turnId,
                currentTheme: state.theme
            });
            return;
        }

        state.emotion = state.emotion || 'neutral';
        state.theme = 'neutral';
        state.showEmotionArt = true;
        state.phase = 'emotion-ready';
        state.content = getThemeContent(state.theme);
        applyVisualState();
        syncPositionOnce();
        logBubbleLifecycle('applyEmotionFallback:applied', {
            requestedTurnId: turnId
        });
    }

    function scheduleEmotionFallback(turnId, delayMs) {
        var resolvedDelayMs = Math.max(0, Number(delayMs) || TIMING.emotionFallbackMs);
        clearTimer('emotionFallbackTimerId');
        logBubbleLifecycle('scheduleEmotionFallback:scheduled', {
            requestedTurnId: turnId,
            delayMs: resolvedDelayMs
        });
        state.emotionFallbackTimerId = window.setTimeout(function () {
            logBubbleLifecycle('scheduleEmotionFallback:fired', {
                requestedTurnId: turnId
            });
            applyEmotionFallback(turnId);
        }, resolvedDelayMs);
    }

    function showThinking(turnId) {
        if (!syncEnabledFromSettings()) {
            return;
        }

        logBubbleLifecycle('showThinking:enter', {
            requestedTurnId: turnId
        });
        clearTurnTimers();
        stopFollowLoop();
        ensureDom();
        resetPositionTracking();

        state.turnId = turnId;
        state.visible = true;
        state.phase = 'thinking';
        state.theme = 'thinking';
        state.emotion = null;
        state.showEmotionArt = false;
        state.content = getThemeContent('thinking');
        state.side = 'right';
        state.shownAt = now();
        state.turnEndedAt = 0;
        state.speechStartedAt = 0;

        applyVisualState();
        syncPositionOnce();
        keepFollowingWhileVisible();
        scheduleMaxVisibleFallback(turnId);
        scheduleThinkingTimeout(turnId);
        logBubbleLifecycle('showThinking:applied', {
            requestedTurnId: turnId
        });
    }

    function handleTurnStart(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        logBubbleLifecycle('handleTurnStart', {
            detailTurnId: turnId
        });
        if (!turnId) {
            return;
        }
        showThinking(turnId);
    }

    function handleEmotionReady(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        logBubbleLifecycle('handleEmotionReady:enter', {
            detailTurnId: turnId,
            detailEmotion: detail && detail.emotion ? String(detail.emotion) : null
        });
        if (!turnId || state.turnId !== turnId || !state.visible || state.phase === 'fading') {
            logBubbleLifecycle('handleEmotionReady:skip', {
                detailTurnId: turnId
            });
            return;
        }

        clearTimer('timeoutTimerId');
        clearTimer('emotionFallbackTimerId');
        clearTimer('emotionSwapTimerId');

        var applyEmotionState = function () {
            if (state.turnId !== turnId || !state.visible || state.phase === 'fading') {
                logBubbleLifecycle('handleEmotionReady:apply_skip', {
                    detailTurnId: turnId
                });
                return;
            }

            state.emotion = detail && detail.emotion ? String(detail.emotion) : null;
            state.theme = normalizeTheme(state.emotion);
            state.showEmotionArt = state.theme !== 'thinking';
            state.phase = 'emotion-ready';
            state.content = getThemeContent(state.theme);
            applyVisualState();
            syncPositionOnce();
            keepFollowingWhileVisible();
            logBubbleLifecycle('handleEmotionReady:applied', {
                detailTurnId: turnId
            });

            if (state.turnEndedAt > 0 && state.speechStartedAt <= 0) {
                beginHide(turnId, TIMING.textOnlyHoldMs);
            }
        };

        var thinkingElapsed = now() - state.shownAt;
        var delay = Math.max(0, TIMING.minThinkingVisibleMs - thinkingElapsed);
        if (delay > 0) {
            state.emotionSwapTimerId = window.setTimeout(applyEmotionState, delay);
            return;
        }

        applyEmotionState();
    }

    function handleSpeechStart(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        logBubbleLifecycle('handleSpeechStart:enter', {
            detailTurnId: turnId
        });
        if (!turnId) {
            return;
        }

        if (!syncEnabledFromSettings()) {
            return;
        }

        clearTimer('hideTimerId');
        clearTimer('textFallbackTimerId');
        clearTimer('timeoutTimerId');

        if (state.turnId !== turnId || !state.visible) {
            showThinking(turnId);
        } else if (state.phase === 'fading') {
            // 同一轮语音在淡出期间恢复时，保留既有表情态并切回可见阶段。
            state.phase = state.theme === 'thinking' ? 'thinking' : 'emotion-ready';
        }

        state.speechStartedAt = now();
        if (state.theme === 'thinking') {
            scheduleEmotionFallback(
                turnId,
                Math.min(TIMING.emotionFallbackMs, TIMING.speechStartNeutralGraceMs)
            );
        } else {
            clearTimer('emotionFallbackTimerId');
        }
        applyVisualState();
        syncPositionOnce();
        keepFollowingWhileVisible();
        scheduleMaxVisibleFallback(turnId);
        logBubbleLifecycle('handleSpeechStart:applied', {
            detailTurnId: turnId
        });
    }

    function handleTurnEnd(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        logBubbleLifecycle('handleTurnEnd:enter', {
            detailTurnId: turnId
        });
        if (!turnId || state.turnId !== turnId || !state.visible) {
            logBubbleLifecycle('handleTurnEnd:skip', {
                detailTurnId: turnId
            });
            return;
        }

        state.turnEndedAt = now();
        logBubbleLifecycle('handleTurnEnd:applied', {
            detailTurnId: turnId
        });
        if (state.speechStartedAt <= 0) {
            scheduleEmotionFallback(turnId, Math.min(TIMING.emotionFallbackMs, TIMING.textOnlyFallbackMs));
            scheduleTextFallbackHide(turnId);
        }
    }

    function handleSpeechEnd(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        logBubbleLifecycle('handleSpeechEnd:enter', {
            detailTurnId: turnId
        });
        if (!turnId || state.turnId !== turnId || !state.visible) {
            logBubbleLifecycle('handleSpeechEnd:skip', {
                detailTurnId: turnId
            });
            return;
        }
        beginHide(turnId, TIMING.speechEndHoldMs);
    }

    function handleSpeechCancel(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        logBubbleLifecycle('handleSpeechCancel:enter', {
            detailTurnId: turnId
        });
        if (!turnId || state.turnId !== turnId || !state.visible) {
            logBubbleLifecycle('handleSpeechCancel:skip', {
                detailTurnId: turnId
            });
            return;
        }
        forceHide(true);
    }

    function handleSettingChanged(detail) {
        state.enabled = !!(detail && detail.enabled === true);
        if (!state.enabled) {
            forceHide(true);
        }
    }

    function handleResize() {
        if (state.visible) {
            syncPositionOnce();
            extendFollowLoop(TIMING.showFollowWindowMs);
        } else {
            renderDebugOverlay();
        }
    }

    function setDebugOverlayEnabled(enabled) {
        state.debugOverlayEnabled = enabled === true;
        window.NEKO_DEBUG_BUBBLE_OVERLAY = state.debugOverlayEnabled;
        syncDebugOverlayVisibility();
        ensureDebugOverlayLoop();
        syncDebugOverlaySnapshot();
    }

    function toggleDebugOverlay() {
        setDebugOverlayEnabled(!debugOverlayEnabled());
    }

    function handleDebugToggleKeydown(event) {
        if (!event || event.defaultPrevented) {
            return;
        }

        if (event.key !== 'B' && event.key !== 'b') {
            return;
        }

        if (!event.shiftKey || event.metaKey || event.ctrlKey || event.altKey) {
            return;
        }

        event.preventDefault();
        toggleDebugOverlay();
    }

    function init() {
        ensureDom();
        syncEnabledFromSettings();
        applyVisualState();
        ensureDebugOverlayLoop();

        window.addEventListener('neko-assistant-turn-start', function (event) {
            handleTurnStart(event.detail || {});
        });
        window.addEventListener('neko-assistant-turn-end', function (event) {
            handleTurnEnd(event.detail || {});
        });
        window.addEventListener('neko-assistant-emotion-ready', function (event) {
            handleEmotionReady(event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-start', function (event) {
            handleSpeechStart(event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-end', function (event) {
            handleSpeechEnd(event.detail || {});
        });
        window.addEventListener('neko-assistant-speech-cancel', function (event) {
            handleSpeechCancel(event.detail || {});
        });
        window.addEventListener('neko-avatar-reaction-bubble-setting-changed', function (event) {
            handleSettingChanged(event.detail || {});
        });
        window.addEventListener('resize', handleResize);
        window.addEventListener('vrm-model-loaded', handleModelLoaded);
        window.addEventListener('mmd-model-loaded', handleModelLoaded);
        window.addEventListener('live2d-model-loaded', handleModelLoaded);
        window.addEventListener('keydown', handleDebugToggleKeydown);
        document.addEventListener('pointerdown', handleAvatarPointerDown, true);
        document.addEventListener('mousedown', handleAvatarPointerDown, true);
        document.addEventListener('pointermove', handleAvatarPointerMove, true);
        document.addEventListener('mousemove', handleAvatarPointerMove, true);
        document.addEventListener('pointerup', handleAvatarPointerEnd, true);
        document.addEventListener('mouseup', handleAvatarPointerEnd, true);
        document.addEventListener('pointercancel', handleAvatarPointerEnd, true);
        window.addEventListener('blur', handleAvatarPointerEnd);
        document.addEventListener('wheel', handleAvatarWheel, { capture: true, passive: true });
        document.addEventListener('visibilitychange', function () {
            if (document.hidden) {
                forceHide(false);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }

    window.avatarReactionBubble = {
        forceHide: function () { forceHide(true); },
        getState: function () {
            return Object.assign({}, state);
        },
        getActiveAvatarBubbleAnchor: getActiveAvatarBubbleAnchor,
        setDebugOverlayEnabled: setDebugOverlayEnabled,
        toggleDebugOverlay: toggleDebugOverlay,
        isDebugOverlayEnabled: debugOverlayEnabled
    };
})();
