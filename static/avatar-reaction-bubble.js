(function () {
    'use strict';

    const S = window.appState || {};

    const TIMING = Object.freeze({
        minVisibleMs: 360,
        minThinkingVisibleMs: 220,
        fadeDurationMs: 220,
        maxThinkingMs: 10000,
        textOnlyHoldMs: 600,
        textOnlyFallbackMs: 1400,
        speechEndHoldMs: 360,
        edgeMarginPx: 12,
        anchorGapPx: 10,
        positionSnapPx: 3,
        sizeSnapPx: 2,
        baseWidthShrinkPx: 92,
        baseHeightShrinkPx: 66,
        verticalOffsetPx: 0,
        modelOverlapRatio: 0.28,
        shortModelHeightPx: 360,
        tallModelHeightPx: 760,
        shortHeadAnchorRatio: 0.7,
        tallHeadAnchorRatio: 0.54,
        shortModelOffsetPx: 12,
        tallModelOffsetPx: -18
    });

    const PRESETS = Object.freeze({
        thinking: ['。。。'],
        happy: [''],
        sad: [''],
        angry: [''],
        neutral: [''],
        surprised: [''],
        default: ['']
    });

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
        hideTimerId: 0,
        timeoutTimerId: 0,
        textFallbackTimerId: 0,
        emotionSwapTimerId: 0,
        lastRenderX: null,
        lastRenderY: null,
        lastRenderWidth: null,
        lastRenderHeight: null
    };

    let bubbleEl = null;
    let frameEl = null;
    let shellEl = null;
    let stageEl = null;
    let mascotEl = null;
    let contentEl = null;

    function normalizeTurnId(turnId) {
        if (turnId === undefined || turnId === null || turnId === '') {
            return null;
        }
        return String(turnId);
    }

    function now() {
        return Date.now();
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
        clearTimer('textFallbackTimerId');
        clearTimer('emotionSwapTimerId');
    }

    function stopFollowLoop() {
        if (state.followRafId) {
            cancelAnimationFrame(state.followRafId);
            state.followRafId = 0;
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

        bubbleEl.dataset.theme = state.theme || 'default';
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
    }

    function hashString(input) {
        var hash = 0;
        var str = String(input || '');
        for (var i = 0; i < str.length; i++) {
            hash = ((hash << 5) - hash) + str.charCodeAt(i);
            hash |= 0;
        }
        return Math.abs(hash);
    }

    function pickContent(theme, turnId) {
        var preset = PRESETS[theme] || PRESETS.default;
        if (!preset || preset.length === 0) {
            return '';
        }
        var index = hashString(String(turnId || '') + ':' + String(theme || 'default')) % preset.length;
        return preset[index];
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
                return 'default';
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

    function getActiveAvatarBubbleAnchor() {
        var mmdBounds = isContainerVisible('mmd-container')
            ? getBoundsFromManager(window.mmdManager, 'getModelScreenBounds')
            : null;
        if (mmdBounds) {
            return {
                type: 'mmd',
                bounds: mmdBounds
            };
        }

        var vrmBounds = isContainerVisible('vrm-container')
            ? getBoundsFromManager(window.vrmManager, 'getModelScreenBounds')
            : null;
        if (vrmBounds) {
            return {
                type: 'vrm',
                bounds: vrmBounds
            };
        }

        var live2dBounds = isContainerVisible('live2d-container')
            ? getBoundsFromManager(window.live2dManager, 'getModelScreenBounds')
            : null;
        if (live2dBounds) {
            return {
                type: 'live2d',
                bounds: live2dBounds
            };
        }

        return null;
    }

    function updatePosition() {
        if (!state.visible) {
            return;
        }

        var anchorInfo = getActiveAvatarBubbleAnchor();
        if (!anchorInfo || !anchorInfo.bounds) {
            forceHide(false);
            return;
        }

        ensureDom();

        var bounds = anchorInfo.bounds;
        var viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        var viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        var margin = TIMING.edgeMarginPx;
        var headWidth = bounds.width * 0.34;
        var headHeight = bounds.height * 0.28;
        var headSpan = Math.max(headWidth, headHeight);
        var viewportCap = Math.round(Math.min(viewportWidth, viewportHeight) * 0.46);
        var headSize = Math.max(84, Math.min(viewportCap, Math.round(headSpan * 1.38)));
        var width = Math.max(96, Math.round(headSize * 1.34) - TIMING.baseWidthShrinkPx);
        var height = Math.max(74, Math.round(headSize * 1.02) - TIMING.baseHeightShrinkPx);
        var headCenterX = bounds.left + bounds.width * 0.5;
        var rightAnchorX = headCenterX + bounds.width * 0.13;
        var leftAnchorX = headCenterX - bounds.width * 0.13;
        var modelHeightProgress = clamp(
            (bounds.height - TIMING.shortModelHeightPx) / (TIMING.tallModelHeightPx - TIMING.shortModelHeightPx),
            0,
            1
        );
        var headAnchorRatio = lerp(
            TIMING.shortHeadAnchorRatio,
            TIMING.tallHeadAnchorRatio,
            modelHeightProgress
        );
        var modelOffsetPx = lerp(
            TIMING.shortModelOffsetPx,
            TIMING.tallModelOffsetPx,
            modelHeightProgress
        );
        var anchorY = bounds.top + headHeight * headAnchorRatio;

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
        var topY = anchorY - height * 0.5 + modelOffsetPx + TIMING.verticalOffsetPx;
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

        bubbleEl.dataset.side = side;
        if (state.lastRenderX === null || Math.abs(state.lastRenderX - roundedX) >= TIMING.positionSnapPx) {
            bubbleEl.style.left = roundedX + 'px';
            state.lastRenderX = roundedX;
        }
        if (state.lastRenderY === null || Math.abs(state.lastRenderY - roundedY) >= TIMING.positionSnapPx) {
            bubbleEl.style.top = roundedY + 'px';
            state.lastRenderY = roundedY;
        }
    }

    function startFollowLoop() {
        if (state.followRafId) {
            return;
        }

        var tick = function () {
            state.followRafId = 0;
            if (!state.visible) {
                return;
            }
            updatePosition();
            state.followRafId = requestAnimationFrame(tick);
        };

        updatePosition();
        state.followRafId = requestAnimationFrame(tick);
    }

    function forceHide(resetTurn) {
        clearTurnTimers();
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
        if (resetTurn !== false) {
            state.turnId = null;
        }
        applyVisualState();
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

    function beginHide(turnId, extraHoldMs) {
        var normalizedTurnId = normalizeTurnId(turnId);
        if (normalizedTurnId && state.turnId !== normalizedTurnId) {
            return;
        }

        clearTimer('hideTimerId');
        clearTimer('textFallbackTimerId');
        clearTimer('timeoutTimerId');

        if (!state.visible) {
            forceHide(true);
            return;
        }

        var elapsed = now() - state.shownAt;
        var preFadeDelay = Math.max(0, TIMING.minVisibleMs - elapsed) + Math.max(0, extraHoldMs || 0);

        state.hideTimerId = window.setTimeout(function () {
            if (normalizedTurnId && state.turnId !== normalizedTurnId) {
                return;
            }
            state.phase = 'fading';
            applyVisualState();

            state.hideTimerId = window.setTimeout(function () {
                if (normalizedTurnId && state.turnId !== normalizedTurnId) {
                    return;
                }
                forceHide(true);
            }, TIMING.fadeDurationMs);
        }, preFadeDelay);
    }

    function scheduleTextFallbackHide(turnId) {
        clearTimer('textFallbackTimerId');
        state.textFallbackTimerId = window.setTimeout(function () {
            if (state.turnId !== turnId || state.speechStartedAt > 0 || state.phase === 'emotion-ready') {
                return;
            }
            beginHide(turnId, 0);
        }, TIMING.textOnlyFallbackMs);
    }

    function showThinking(turnId) {
        if (!syncEnabledFromSettings()) {
            return;
        }

        clearTurnTimers();
        stopFollowLoop();

        state.turnId = turnId;
        state.visible = true;
        state.phase = 'thinking';
        state.theme = 'thinking';
        state.emotion = null;
        state.showEmotionArt = false;
        state.content = pickContent('thinking', turnId);
        state.side = 'right';
        state.shownAt = now();
        state.turnEndedAt = 0;
        state.speechStartedAt = 0;

        applyVisualState();
        startFollowLoop();
        scheduleThinkingTimeout(turnId);
    }

    function handleTurnStart(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        if (!turnId) {
            return;
        }
        showThinking(turnId);
    }

    function handleEmotionReady(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        if (!turnId || state.turnId !== turnId || !state.visible) {
            return;
        }

        clearTimer('timeoutTimerId');
        clearTimer('emotionSwapTimerId');

        var applyEmotionState = function () {
            if (state.turnId !== turnId || !state.visible) {
                return;
            }

            state.emotion = detail && detail.emotion ? String(detail.emotion) : null;
            state.theme = normalizeTheme(state.emotion);
            state.showEmotionArt = state.theme !== 'thinking' && state.theme !== 'default';
            state.phase = 'emotion-ready';
            state.content = pickContent(state.theme, turnId);
            applyVisualState();
            updatePosition();

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
        }

        state.speechStartedAt = now();
        applyVisualState();
        startFollowLoop();
    }

    function handleTurnEnd(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        if (!turnId || state.turnId !== turnId || !state.visible) {
            return;
        }

        state.turnEndedAt = now();
        if (state.speechStartedAt <= 0) {
            scheduleTextFallbackHide(turnId);
        }
    }

    function handleSpeechEnd(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        if (!turnId || state.turnId !== turnId || !state.visible) {
            return;
        }
        beginHide(turnId, TIMING.speechEndHoldMs);
    }

    function handleSpeechCancel(detail) {
        var turnId = normalizeTurnId(detail && detail.turnId);
        if (!turnId || state.turnId === turnId || state.turnId === null) {
            forceHide(true);
        }
    }

    function handleSettingChanged(detail) {
        state.enabled = !!(detail && detail.enabled === true);
        if (!state.enabled) {
            forceHide(true);
        }
    }

    function handleResize() {
        if (state.visible) {
            updatePosition();
        }
    }

    function init() {
        ensureDom();
        syncEnabledFromSettings();
        applyVisualState();

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
        getActiveAvatarBubbleAnchor: getActiveAvatarBubbleAnchor
    };
})();
