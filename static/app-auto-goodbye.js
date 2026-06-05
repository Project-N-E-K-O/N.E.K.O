(function () {
    'use strict';

    const AUTO_GOODBYE_MS = 10 * 60 * 1000;
    const CAT2_MS = 15 * 60 * 1000;
    const CAT3_MS = 18 * 60 * 1000;
    const CONVERSATION_GRACE_MS = 15 * 1000;
    const TICK_INTERVAL_MS = 500;
    const CAT3_DRAG_RELEASES_BEFORE_CAT2 = 3;

    const TIER_NONE = 'none';
    const TIER_CAT1 = 'cat1';
    const TIER_CAT2 = 'cat2';
    const TIER_CAT3 = 'cat3';
    const VALID_TIERS = new Set([TIER_NONE, TIER_CAT1, TIER_CAT2, TIER_CAT3]);
    const DRAGGING_BODY_CLASSES = Object.freeze([
        'neko-model-dragging',
        'react-chat-window-dragging',
    ]);
    const NON_CHARACTER_PATH_SEGMENTS = new Set([
        'agenthud',
        'api',
        'api_key',
        'card_maker',
        'character_card_manager',
        'chara_manager',
        'chat',
        'cloudsave_manager',
        'cookies_login',
        'focus',
        'jukebox',
        'l2d',
        'live2d_emotion_manager',
        'live2d_parameter_editor',
        'memory_browser',
        'mmd_emotion_manager',
        'model_manager',
        'soccer_demo',
        'static',
        'subtitle',
        'templates',
        'toast',
        'voice_clone',
        'vrm_emotion_manager',
    ]);

    const state = {
        started: false,
        infrastructurePrimed: false,
        lastInteractionAt: Date.now(),
        autoGoodbyeTriggered: false,
        visualTier: TIER_NONE,
        timerId: 0,
        lastReason: '',
        lastInteractionSource: '',
        lastTierSource: '',
        lastTierChangedAt: 0,
        idleSuppressed: false,
        idleSuppressionReasons: [],
        lastSuppressionChangedAt: 0,
        conversationGraceUntil: 0,
        lastConversationSource: '',
        cat3DragReleaseCount: 0,
        dragDemotionTier: TIER_NONE,
        dragDemotionStartedAt: 0,
        // 变猫时刻 + 入口（自动 idle / 手动请离开），供"变回时"猫咪专属问候独立计时
        goodbyeEnteredAt: 0,
        goodbyeWasAuto: false,
    };

    function nowMs() {
        return Date.now();
    }

    function getPathname() {
        try {
            return String((window.location && window.location.pathname) || '').trim() || '/';
        } catch (_) {
            return '/';
        }
    }

    function isNamedCharacterPath(pathname) {
        const pathParts = String(pathname || '').split('/').filter(Boolean);
        if (pathParts.length !== 1) {
            return false;
        }

        let pathName = '';
        try {
            pathName = decodeURIComponent(pathParts[0]).trim();
        } catch (_) {
            return false;
        }

        if (NON_CHARACTER_PATH_SEGMENTS.has(pathName.toLowerCase())) {
            return false;
        }

        return !!(pathName && pathName.indexOf('/') < 0 && pathName.indexOf('.') < 0);
    }

    function isEligiblePage() {
        const pathname = getPathname();
        return pathname === '/' || pathname === '/index.html' || isNamedCharacterPath(pathname);
    }

    function waitForStorageLocationStartupBarrier() {
        if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
            try {
                return Promise.resolve(window.waitForStorageLocationStartupBarrier()).catch(() => {});
            } catch (_) {
                return Promise.resolve();
            }
        }
        if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            return Promise.resolve(window.__nekoStorageLocationStartupBarrier).catch(() => {});
        }
        return Promise.resolve();
    }

    function isTutorialGuardActive() {
        try {
            const controller = window.NekoHomeTutorialFeatureController;
            if (controller && typeof controller.isActive === 'function' && controller.isActive()) {
                return true;
            }
        } catch (_) {}

        try {
            if (typeof window.isNekoHomeTutorialInteractionLocked === 'function'
                && window.isNekoHomeTutorialInteractionLocked() === true) {
                return true;
            }
        } catch (_) {}

        const body = document.body;
        return !!(body && body.classList.contains('yui-taking-over'));
    }

    function hasBlockingActiveWork() {
        const taskMap = window._agentTaskMap;
        if (!taskMap || typeof taskMap.forEach !== 'function') {
            return false;
        }

        let blocked = false;
        taskMap.forEach((task) => {
            if (blocked || !task) {
                return;
            }
            const status = typeof task.status === 'string' ? task.status.trim().toLowerCase() : '';
            if (status === 'queued' || status === 'running') {
                blocked = true;
            }
        });
        return blocked;
    }

    function isGoodbyeActive() {
        return !!(
            (window.live2dManager && window.live2dManager._goodbyeClicked)
            || (window.vrmManager && window.vrmManager._goodbyeClicked)
            || (window.mmdManager && window.mmdManager._goodbyeClicked)
        );
    }

    function hasCoreInfrastructure() {
        if (!document.body) {
            return false;
        }
        if (!document.getElementById('resetSessionButton')) {
            return false;
        }
        if (!window.appState || !window.appConst) {
            return false;
        }
        return true;
    }

    function hasOpenSocket() {
        const socket = window.appState && window.appState.socket;
        return !!(socket && socket.readyState === WebSocket.OPEN);
    }

    function markIdleBaseline(source) {
        state.lastInteractionAt = nowMs();
        state.lastInteractionSource = typeof source === 'string' ? source : 'baseline-reset';
    }

    function extendConversationGrace(source) {
        state.conversationGraceUntil = nowMs() + CONVERSATION_GRACE_MS;
        state.lastConversationSource = typeof source === 'string' ? source : '';
    }

    function clearConversationGrace() {
        state.conversationGraceUntil = 0;
        state.lastConversationSource = '';
    }

    function hasConversationGrace() {
        return state.conversationGraceUntil > nowMs();
    }

    function isElementVisible(element) {
        if (!element || element.hidden) {
            return false;
        }

        let computedStyle = null;
        try {
            if (typeof window.getComputedStyle === 'function') {
                computedStyle = window.getComputedStyle(element);
            }
        } catch (_) {}

        if (computedStyle
            && (computedStyle.display === 'none'
                || computedStyle.visibility === 'hidden'
                || computedStyle.opacity === '0')) {
            return false;
        }

        try {
            if (typeof element.getBoundingClientRect === 'function') {
                const rect = element.getBoundingClientRect();
                if (rect && rect.width <= 0 && rect.height <= 0) {
                    return false;
                }
            }
        } catch (_) {}

        return true;
    }

    function hasVisibleElements(selectors) {
        if (!document || typeof document.querySelectorAll !== 'function' || !Array.isArray(selectors)) {
            return false;
        }

        for (let i = 0; i < selectors.length; i += 1) {
            const selector = selectors[i];
            if (typeof selector !== 'string' || !selector) {
                continue;
            }

            let nodes = [];
            try {
                nodes = document.querySelectorAll(selector);
            } catch (_) {
                nodes = [];
            }

            for (let j = 0; j < nodes.length; j += 1) {
                if (isElementVisible(nodes[j])) {
                    return true;
                }
            }
        }

        return false;
    }

    function hasActiveAssistantTurn() {
        const appState = window.appState;
        if (!appState) {
            return false;
        }

        return !!(
            appState.assistantTurnAwaitingBubble
            || appState.assistantSpeechActiveTurnId
            || (appState.assistantTurnId
                && appState.assistantTurnId !== appState.assistantTurnCompletedId)
        );
    }

    function hasActiveConversationState() {
        const appState = window.appState;
        if (!appState) {
            return false;
        }

        return !!(
            appState.isRecording
            || appState.voiceStartPending
            || appState.isPlaying
            || hasConversationGrace()
            || hasActiveAssistantTurn()
        );
    }

    function hasActiveSystemExecutionState() {
        const appState = window.appState;
        if (!appState) {
            return false;
        }

        const screenButton = document && typeof document.getElementById === 'function'
            ? document.getElementById('screenButton')
            : null;
        const manualScreenShareActive = !!(
            (screenButton && screenButton.classList && screenButton.classList.contains('active'))
            || appState.videoSenderInterval
        );

        return !!(
            appState.isSwitchingMode
            || appState.isSwitchingCatgirl
            || appState.gameRouteActive
            || appState.gameVoiceSttGateActive
            || appState.gameVoiceSttListening
            || manualScreenShareActive
        );
    }

    function hasActiveDragInteraction() {
        const body = document && document.body;
        if (body && body.classList) {
            for (let i = 0; i < DRAGGING_BODY_CLASSES.length; i += 1) {
                if (body.classList.contains(DRAGGING_BODY_CLASSES[i])) {
                    return true;
                }
            }
        }
        return hasVisibleElements(['[data-dragging="true"]']);
    }

    function getIdleBlockReasons() {
        const reasons = [];
        if (isTutorialGuardActive()) {
            reasons.push('tutorial-guard');
        }
        if (hasBlockingActiveWork()) {
            reasons.push('active-task');
        }
        if (hasActiveConversationState()) {
            reasons.push('active-conversation');
        }
        if (hasActiveSystemExecutionState()) {
            reasons.push('active-system');
        }
        if (hasActiveDragInteraction()) {
            reasons.push('dragging');
        }
        return reasons;
    }

    function syncIdleSuppressionState(source) {
        const reasons = getIdleBlockReasons();
        const blocked = reasons.length > 0;
        const previousSignature = state.idleSuppressionReasons.join('|');
        const nextSignature = reasons.join('|');

        if (!blocked) {
            if (state.idleSuppressed) {
                state.idleSuppressed = false;
                state.idleSuppressionReasons = [];
                state.lastSuppressionChangedAt = nowMs();
                if (!isGoodbyeActive()) {
                    markIdleBaseline('idle-suppression-cleared');
                }
                emitStateChange('idle-suppression', {
                    active: false,
                    source: source || 'tick',
                    reasons: [],
                });
            }
            return false;
        }

        if (!state.idleSuppressed) {
            state.idleSuppressed = true;
            state.idleSuppressionReasons = reasons.slice();
            state.lastSuppressionChangedAt = nowMs();
            if (!isGoodbyeActive()) {
                markIdleBaseline('idle-suppression-entered');
            }
            emitStateChange('idle-suppression', {
                active: true,
                source: source || 'tick',
                reasons: reasons.slice(),
            });
            return true;
        }

        if (previousSignature !== nextSignature) {
            state.idleSuppressionReasons = reasons.slice();
            state.lastSuppressionChangedAt = nowMs();
            emitStateChange('idle-suppression', {
                active: true,
                source: source || 'tick',
                reasons: reasons.slice(),
            });
        }

        return true;
    }

    function ensureInfrastructurePrimed() {
        if (!hasCoreInfrastructure()) {
            return false;
        }
        if (!hasOpenSocket()) {
            if (state.infrastructurePrimed) {
                state.infrastructurePrimed = false;
                state.lastReason = 'websocket-closed';
                emitStateChange('infrastructure-unprimed', {
                    reason: 'websocket-closed',
                });
            }
            return false;
        }
        if (state.infrastructurePrimed) {
            return true;
        }

        state.infrastructurePrimed = true;
        markIdleBaseline('infrastructure-primed');
        state.lastReason = 'infrastructure-primed';
        emitStateChange('infrastructure-primed', {
            reason: 'websocket-open',
        });
        return true;
    }

    function isInfrastructureReady() {
        if (!hasCoreInfrastructure()) {
            return false;
        }
        return ensureInfrastructurePrimed();
    }

    function getElapsedSinceLastInteraction() {
        return Math.max(0, nowMs() - state.lastInteractionAt);
    }

    function getTargetTierForElapsed(elapsedMs) {
        if (elapsedMs >= CAT3_MS) return TIER_CAT3;
        if (elapsedMs >= CAT2_MS) return TIER_CAT2;
        if (elapsedMs >= AUTO_GOODBYE_MS) return TIER_CAT1;
        return TIER_NONE;
    }

    function getDragDemotionElapsedStartMs(tier) {
        if (tier === TIER_CAT2) return CAT2_MS;
        if (tier === TIER_CAT1) return AUTO_GOODBYE_MS;
        return 0;
    }

    function clearDragTierDemotion() {
        state.dragDemotionTier = TIER_NONE;
        state.dragDemotionStartedAt = 0;
    }

    function clearDragTierMemory() {
        state.cat3DragReleaseCount = 0;
        clearDragTierDemotion();
    }

    function getVisualTierElapsedForCurrentState() {
        if (state.dragDemotionTier !== TIER_NONE && state.dragDemotionStartedAt > 0) {
            return getDragDemotionElapsedStartMs(state.dragDemotionTier)
                + Math.max(0, nowMs() - state.dragDemotionStartedAt);
        }
        return getElapsedSinceLastInteraction();
    }

    function emitStateChange(type, detail) {
        try {
            window.dispatchEvent(new CustomEvent('neko:auto-goodbye:state-change', {
                detail: Object.assign({
                    type: type,
                    timestamp: nowMs(),
                    state: getState(),
                }, detail || {}),
            }));
        } catch (_) {}
    }

    function setVisualTier(tier, meta) {
        const normalizedTier = VALID_TIERS.has(tier) ? tier : TIER_NONE;
        if (state.visualTier === normalizedTier) {
            return false;
        }

        state.visualTier = normalizedTier;
        state.lastTierChangedAt = nowMs();
        state.lastTierSource = meta && typeof meta.source === 'string' ? meta.source : '';
        if (normalizedTier !== TIER_CAT3) {
            state.cat3DragReleaseCount = 0;
        }
        emitStateChange('visual-tier', {
            tier: normalizedTier,
            source: state.lastTierSource,
            reason: meta && typeof meta.reason === 'string' ? meta.reason : '',
        });
        return true;
    }

    function syncVisualTierFromCurrentState(source) {
        if (!isGoodbyeActive()) {
            setVisualTier(TIER_NONE, {
                source: source || 'goodbye-cleared',
            });
            state.autoGoodbyeTriggered = false;
            clearDragTierMemory();
            return;
        }

        const nextTier = getTargetTierForElapsed(getVisualTierElapsedForCurrentState());
        setVisualTier(nextTier === TIER_NONE ? TIER_CAT1 : nextTier, {
            source: source || 'goodbye-active',
        });
        if (state.dragDemotionTier !== TIER_NONE && nextTier === TIER_CAT3) {
            clearDragTierDemotion();
        }
    }

    function demoteVisualTierAfterReturnBallDrag(targetTier) {
        state.dragDemotionTier = targetTier;
        state.dragDemotionStartedAt = nowMs();
        setVisualTier(targetTier, {
            source: 'return-ball-drag-demotion',
            reason: 'return-ball-drag-end',
        });
    }

    function handleReturnBallDragEnd() {
        if (!isGoodbyeActive()) {
            clearDragTierMemory();
            return;
        }

        const currentTier = state.visualTier;
        if (currentTier === TIER_CAT3) {
            state.cat3DragReleaseCount += 1;
            if (state.cat3DragReleaseCount >= CAT3_DRAG_RELEASES_BEFORE_CAT2) {
                state.cat3DragReleaseCount = 0;
                demoteVisualTierAfterReturnBallDrag(TIER_CAT2);
            }
            return;
        }

        state.cat3DragReleaseCount = 0;
        if (currentTier === TIER_CAT2) {
            demoteVisualTierAfterReturnBallDrag(TIER_CAT1);
        }
    }

    function noteUserInteraction(source) {
        const normalizedSource = typeof source === 'string' ? source : 'interaction';

        if (isGoodbyeActive() && normalizedSource !== 'return-click') {
            state.lastInteractionSource = normalizedSource;
            return;
        }

        markIdleBaseline(normalizedSource);

        if (!isGoodbyeActive()) {
            if (state.visualTier !== TIER_NONE) {
                setVisualTier(TIER_NONE, {
                    source: 'interaction-reset',
                });
            }
            state.autoGoodbyeTriggered = false;
        }
    }

    function tryAutoGoodbye(reason) {
        if (!state.started) {
            return false;
        }
        if (state.autoGoodbyeTriggered) {
            return false;
        }
        if (!isEligiblePage()) {
            return false;
        }
        if (!isInfrastructureReady()) {
            return false;
        }
        if (isGoodbyeActive()) {
            syncVisualTierFromCurrentState('already-goodbye');
            return false;
        }
        if (getIdleBlockReasons().length > 0) {
            return false;
        }

        state.autoGoodbyeTriggered = true;
        state.lastReason = typeof reason === 'string' ? reason : 'idle-timeout';
        setVisualTier(TIER_CAT1, {
            source: 'auto-goodbye',
            reason: state.lastReason,
        });

        window.dispatchEvent(new CustomEvent('live2d-goodbye-click', {
            detail: {
                autoGoodbye: true,
                source: 'auto-goodbye',
                reason: state.lastReason,
            },
        }));
        emitStateChange('auto-goodbye-triggered', {
            reason: state.lastReason,
        });
        return true;
    }

    function clearTimers(reason) {
        if (state.timerId) {
            window.clearInterval(state.timerId);
            state.timerId = 0;
        }
        if (typeof reason === 'string' && reason) {
            state.lastReason = reason;
        }
    }

    function getState() {
        return {
            started: state.started,
            infrastructurePrimed: state.infrastructurePrimed,
            lastInteractionAt: state.lastInteractionAt,
            autoGoodbyeTriggered: state.autoGoodbyeTriggered,
            visualTier: state.visualTier,
            lastReason: state.lastReason,
            lastInteractionSource: state.lastInteractionSource,
            lastTierSource: state.lastTierSource,
            lastTierChangedAt: state.lastTierChangedAt,
            idleSuppressed: state.idleSuppressed,
            idleSuppressionReasons: state.idleSuppressionReasons.slice(),
            lastSuppressionChangedAt: state.lastSuppressionChangedAt,
            conversationGraceUntil: state.conversationGraceUntil,
            lastConversationSource: state.lastConversationSource,
            thresholdsMs: {
                cat1: AUTO_GOODBYE_MS,
                cat2: CAT2_MS,
                cat3: CAT3_MS,
            },
        };
    }

    function tick() {
        if (!state.started) {
            return;
        }

        ensureInfrastructurePrimed();

        const goodbyeActive = isGoodbyeActive();
        const idleSuppressed = syncIdleSuppressionState('tick');

        if (goodbyeActive) {
            syncVisualTierFromCurrentState('tick-goodbye');
            return;
        }

        if (idleSuppressed) {
            return;
        }

        const elapsedMs = getElapsedSinceLastInteraction();
        if (elapsedMs >= AUTO_GOODBYE_MS) {
            tryAutoGoodbye('idle-timeout');
        }
    }

    function bindInteractionListeners() {
        const onPointerDown = () => noteUserInteraction('pointerdown');
        const onKeyDown = () => noteUserInteraction('keydown');
        const onTouchStart = () => noteUserInteraction('touchstart');
        const onWheel = () => noteUserInteraction('wheel');

        document.addEventListener('pointerdown', onPointerDown, true);
        document.addEventListener('keydown', onKeyDown, true);
        document.addEventListener('touchstart', onTouchStart, { capture: true, passive: true });
        document.addEventListener('wheel', onWheel, { capture: true, passive: true });
        window.addEventListener('neko:voice-session-started', () => {
            extendConversationGrace('voice-session-started');
            noteUserInteraction('voice-session-started');
        });
        window.addEventListener('neko:user-content-sent', () => {
            extendConversationGrace('user-content-sent');
            noteUserInteraction('user-content-sent');
        });
        window.addEventListener('neko:cross-window-user-activity', (event) => {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            const source = detail.source ? `cross-window:${detail.source}` : 'cross-window';
            if (detail.kind === 'conversation') {
                extendConversationGrace(source);
            }
            noteUserInteraction(source);
        });

        window.addEventListener('live2d-goodbye-click', (event) => {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            clearConversationGrace();
            // 记录"变成猫咪"的时刻与入口，供变回时按猫咪停留时长触发专属问候。
            // goodbye-click 只在真正进入猫咪态时派发（手动按钮 / 自动 idle-timeout），
            // 直接覆盖即可：旧值会在 handleReturn 清零，跨角色切换等异常残留会被下次进入覆盖。
            state.goodbyeEnteredAt = nowMs();
            state.goodbyeWasAuto = detail.autoGoodbye === true;
            if (detail.autoGoodbye === true) {
                state.autoGoodbyeTriggered = true;
                state.lastReason = typeof detail.reason === 'string' ? detail.reason : 'idle-timeout';
                syncVisualTierFromCurrentState('goodbye-event');
            } else {
                state.autoGoodbyeTriggered = false;
                state.lastReason = 'manual-goodbye';
                clearDragTierMemory();
                setVisualTier(TIER_CAT1, {
                    source: 'manual-goodbye',
                    reason: state.lastReason,
                });
            }
        });

        const handleReturn = () => {
            // 变回猫娘前，按"作为猫咪待了多久 + 此刻所处 tier（清醒/打盹/熟睡）"
            // 请求一次专属问候。tier 必须在 setVisualTier(NONE) 清空之前读取。
            if (state.goodbyeEnteredAt > 0) {
                const durationSeconds = Math.max(0, Math.floor((nowMs() - state.goodbyeEnteredAt) / 1000));
                try {
                    window.dispatchEvent(new CustomEvent('neko:cat-greeting-check', {
                        detail: {
                            durationSeconds: durationSeconds,
                            tier: state.visualTier,
                            wasAuto: !!state.goodbyeWasAuto,
                        },
                    }));
                } catch (_) {}
            }
            state.goodbyeEnteredAt = 0;
            state.goodbyeWasAuto = false;
            clearConversationGrace();
            state.autoGoodbyeTriggered = false;
            clearDragTierMemory();
            setVisualTier(TIER_NONE, {
                source: 'return-event',
            });
            noteUserInteraction('return-click');
        };
        window.addEventListener('live2d-return-click', handleReturn);
        window.addEventListener('vrm-return-click', handleReturn);
        window.addEventListener('mmd-return-click', handleReturn);
        window.addEventListener('neko:return-ball-manual-move', (event) => {
            const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.reason === 'return-ball-drag-end') {
                handleReturnBallDragEnd();
            }
        });
    }

    function start() {
        if (state.started) {
            return;
        }
        if (!isEligiblePage()) {
            return;
        }

        state.started = true;
        state.lastInteractionAt = nowMs();
        state.lastReason = 'started';
        syncVisualTierFromCurrentState('start');
        state.timerId = window.setInterval(tick, TICK_INTERVAL_MS);
        emitStateChange('started', {
            reason: 'startup',
        });
    }

    async function init() {
        if (!isEligiblePage()) {
            return;
        }

        await waitForStorageLocationStartupBarrier();
        start();
    }

    window.nekoAutoGoodbye = {
        noteUserInteraction: noteUserInteraction,
        hasBlockingActiveWork: hasBlockingActiveWork,
        hasActiveConversationState: hasActiveConversationState,
        hasActiveSystemExecutionState: hasActiveSystemExecutionState,
        getIdleBlockReasons: getIdleBlockReasons,
        tryAutoGoodbye: tryAutoGoodbye,
        setVisualTier: setVisualTier,
        clearTimers: clearTimers,
        getState: getState,
    };

    bindInteractionListeners();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
