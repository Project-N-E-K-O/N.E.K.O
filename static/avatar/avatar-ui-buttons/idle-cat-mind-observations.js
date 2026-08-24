/**
 * Cat Mind observation adapter for avatar-side interaction facts.
 *
 * This layer may combine renderer geometry and a complete user gesture into a
 * structured observation. It never scores an action, changes journey state,
 * or starts a runner. Cat Mind remains the only layer that interprets these
 * facts as short-lived intent evidence.
 */

const _NEKO_CAT_MIND_YARN_DRAG_EVENT = 'neko:chat-yarn-user-drag';
const _NEKO_CAT_MIND_YARN_DRAG_COMPLETED = 'chat_yarn_drag_completed';
const _NEKO_CAT_MIND_YARN_DRAG_STALE_MS = 8 * 1000;
const _NEKO_CAT_MIND_YARN_SETTLE_FALLBACK_MS = 250;
const _NEKO_CAT_MIND_YARN_TERMINAL_DUPLICATE_MS = 700;
let _nekoCatMindYarnDragSequence = 0;
let _nekoCatMindYarnSettleSequence = 0;
let _nekoCatMindYarnDragSession = null;
let _nekoCatMindYarnDragStaleTimer = 0;
let _nekoCatMindYarnSettleTimer = 0;
let _nekoCatMindYarnDragActive = false;
let _nekoCatMindYarnSettling = false;
let _nekoCatMindYarnSettlingSessionId = '';
let _nekoCatMindStableYarnRectBySpace = Object.create(null);
let _nekoCatMindLastYarnTerminalSignature = '';
let _nekoCatMindLastYarnTerminalAt = 0;

function _getNekoCatMindYarnObservationEventName() {
    const contract = window.NekoCatMindContract;
    const name = contract && contract.EVENT_NAMES && contract.EVENT_NAMES.OBSERVATION;
    return typeof name === 'string' && name ? name : _NEKO_CAT_IDLE_OBSERVATION_SOURCE_EVENT;
}

function _getNekoCatMindYarnDragPhase(detail) {
    const explicitPhase = detail && typeof detail.phase === 'string'
        ? detail.phase.trim().toLowerCase()
        : '';
    if (explicitPhase === 'start' || explicitPhase === 'move' || explicitPhase === 'end') {
        return explicitPhase;
    }
    if (explicitPhase === 'cancel' || explicitPhase === 'blur') return 'cancel';

    const reason = detail && typeof detail.reason === 'string'
        ? detail.reason.trim().toLowerCase()
        : '';
    if (reason === 'ball-drag-start') return 'start';
    if (reason === 'ball-drag-move' ||
        reason === 'self-ball-drag-move' ||
        reason === 'self-ball-wayland-virtual-drag-move') {
        return 'move';
    }
    if (reason === 'ball-drag-end' ||
        reason === 'self-ball-drag-stop' ||
        reason === 'self-ball-wayland-drag-stop' ||
        reason === 'self-ball-wayland-virtual-drag-stop' ||
        reason === 'self-ball-force-release') {
        return 'end';
    }
    if (reason === 'ball-drag-cancel' ||
        reason === 'self-ball-wayland-virtual-drag-cancel' ||
        reason === 'self-ball-wayland-virtual-drag-blur') {
        return 'cancel';
    }
    return '';
}

function _hasNekoCatMindYarnSessionMismatch(detail) {
    const incomingSessionId = detail && typeof detail.sessionId === 'string'
        ? detail.sessionId
        : '';
    const activeSessionId = _nekoCatMindYarnDragSession && _nekoCatMindYarnDragSession.sessionId;
    return !!(incomingSessionId && activeSessionId && incomingSessionId !== activeSessionId);
}

function _getNekoCatMindYarnCoordinateSpace(detail, fallback = 'screen') {
    return detail && detail.coordinateSpace === 'viewport' ? 'viewport' : fallback;
}

function _getNekoCatMindYarnRect(detail) {
    return _normalizeNekoIdleScreenRect(detail && detail.screenRect);
}

function _getNekoCatMindYarnRectCenterDistance(previousRect, nextRect) {
    const previous = _normalizeNekoIdleScreenRect(previousRect);
    const next = _normalizeNekoIdleScreenRect(nextRect);
    if (!previous || !next) return 0;
    const previousX = previous.left + previous.width / 2;
    const previousY = previous.top + previous.height / 2;
    const nextX = next.left + next.width / 2;
    const nextY = next.top + next.height / 2;
    return Math.hypot(nextX - previousX, nextY - previousY);
}

function _getNekoCatMindYarnLocalRect(rect, coordinateSpace) {
    const normalized = _normalizeNekoIdleScreenRect(rect);
    if (!normalized) return null;
    if (coordinateSpace !== 'screen') return normalized;
    const screenLeft = Number.isFinite(Number(window.screenX))
        ? Number(window.screenX)
        : Number(window.screenLeft) || 0;
    const screenTop = Number.isFinite(Number(window.screenY))
        ? Number(window.screenY)
        : Number(window.screenTop) || 0;
    return _normalizeNekoIdleScreenRect({
        left: normalized.left - screenLeft,
        top: normalized.top - screenTop,
        width: normalized.width,
        height: normalized.height
    });
}

function _getNekoCatMindRectGap(firstRect, secondRect) {
    const first = _normalizeNekoIdleScreenRect(firstRect);
    const second = _normalizeNekoIdleScreenRect(secondRect);
    if (!first || !second) return null;
    const firstRight = first.left + first.width;
    const firstBottom = first.top + first.height;
    const secondRight = second.left + second.width;
    const secondBottom = second.top + second.height;
    const horizontalGap = Math.max(0, first.left - secondRight, second.left - firstRight);
    const verticalGap = Math.max(0, first.top - secondBottom, second.top - firstBottom);
    return Math.hypot(horizontalGap, verticalGap);
}

function _getNekoCatMindYarnCatGeometry(rect, coordinateSpace) {
    const button = _findNekoCatMindVisibleButtonForTier(_NEKO_IDLE_TIER_CAT1);
    const container = button && _getNekoIdleReturnContainerFromButton(button);
    const localYarnRect = _getNekoCatMindYarnLocalRect(rect, coordinateSpace);
    const localCatRect = container && typeof container.getBoundingClientRect === 'function'
        ? _normalizeNekoIdleScreenRect(container.getBoundingClientRect())
        : null;
    if (!localCatRect || !localYarnRect || localCatRect.width <= 0 || localCatRect.height <= 0) return null;
    const rectGap = _getNekoCatMindRectGap(localCatRect, localYarnRect);
    if (!Number.isFinite(rectGap)) return null;
    return {
        // Thresholds intentionally use the established journey constants, but
        // geometry remains a read-only rectangle measurement. In particular,
        // do not call _getNekoIdleCat1Target: it commits journey approach side.
        distancePx: Math.max(0, rectGap),
        centerDistancePx: _getNekoCatMindYarnRectCenterDistance(localCatRect, localYarnRect),
        nearThresholdPx: Math.max(1, Number(_NEKO_IDLE_CAT1_WALK_ENTER_DISTANCE_PX) || 180),
        settledThresholdPx: Math.max(0, Number(_NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX) || 14),
        movementThresholdPx: Math.max(1, Number(_NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX) || 24)
    };
}

function _getNekoCatMindYarnTerminalSignature(phase, detail, rect, coordinateSpace) {
    return [
        phase,
        coordinateSpace,
        detail && typeof detail.reason === 'string' ? detail.reason : '',
        detail && typeof detail.sessionId === 'string' ? detail.sessionId : '',
        rect && rect.left,
        rect && rect.top,
        rect && rect.width,
        rect && rect.height
    ].join(':');
}

function _isNekoCatMindDuplicateYarnTerminal(signature, timestamp) {
    const now = Number(timestamp) || Date.now();
    if (signature && signature === _nekoCatMindLastYarnTerminalSignature &&
        now - _nekoCatMindLastYarnTerminalAt <= _NEKO_CAT_MIND_YARN_TERMINAL_DUPLICATE_MS) {
        return true;
    }
    _nekoCatMindLastYarnTerminalSignature = signature;
    _nekoCatMindLastYarnTerminalAt = now;
    return false;
}

function _clearNekoCatMindYarnDragStaleTimer() {
    if (!_nekoCatMindYarnDragStaleTimer) return;
    window.clearTimeout(_nekoCatMindYarnDragStaleTimer);
    _nekoCatMindYarnDragStaleTimer = 0;
}

function _clearNekoCatMindYarnSettleTimer() {
    if (!_nekoCatMindYarnSettleTimer) return;
    window.clearTimeout(_nekoCatMindYarnSettleTimer);
    _nekoCatMindYarnSettleTimer = 0;
}

function _releaseNekoCatMindYarnGate() {
    _clearNekoCatMindYarnDragStaleTimer();
    _clearNekoCatMindYarnSettleTimer();
    _nekoCatMindYarnDragSession = null;
    _nekoCatMindYarnDragActive = false;
    _nekoCatMindYarnSettling = false;
    _nekoCatMindYarnSettlingSessionId = '';
}

function _armNekoCatMindYarnDragStaleTimer(session) {
    _clearNekoCatMindYarnDragStaleTimer();
    _nekoCatMindYarnDragStaleTimer = window.setTimeout(() => {
        _nekoCatMindYarnDragStaleTimer = 0;
        if (_nekoCatMindYarnDragSession !== session) return;
        // Lost pointer-up / cross-window terminal events must never leave the
        // provider gate closed indefinitely. A stale release is not intent.
        _nekoCatMindYarnSettleSequence += 1;
        _releaseNekoCatMindYarnGate();
    }, _NEKO_CAT_MIND_YARN_DRAG_STALE_MS);
}

function _beginNekoCatMindYarnDragSession(detail, rect, coordinateSpace, geometry) {
    _nekoCatMindYarnSettleSequence += 1;
    _clearNekoCatMindYarnSettleTimer();
    _nekoCatMindYarnDragSequence += 1;
    const timestamp = Number(detail && detail.timestamp) || Date.now();
    const previousRect = _nekoCatMindStableYarnRectBySpace[coordinateSpace];
    const startRect = rect || previousRect || null;
    const startGeometry = geometry || _getNekoCatMindYarnCatGeometry(startRect, coordinateSpace);
    _nekoCatMindYarnSettling = false;
    _nekoCatMindYarnSettlingSessionId = '';
    _nekoCatMindYarnDragActive = true;
    _nekoCatMindYarnDragSession = {
        sessionId: typeof detail.sessionId === 'string' && detail.sessionId
            ? detail.sessionId
            : `yarn-drag:${timestamp}:${_nekoCatMindYarnDragSequence}`,
        source: typeof detail.source === 'string' && detail.source ? detail.source : 'chat-yarn',
        coordinateSpace: coordinateSpace,
        startedAt: timestamp,
        startRect: startRect,
        lastRect: startRect,
        endRect: startRect,
        startDistanceToCatPx: startGeometry ? startGeometry.distancePx : null,
        startCenterDistanceToCatPx: startGeometry ? startGeometry.centerDistancePx : null,
        minDistanceToCatPx: startGeometry ? startGeometry.distancePx : null,
        maxDistanceToCatPx: startGeometry ? startGeometry.distancePx : null,
        nearThresholdPx: startGeometry ? startGeometry.nearThresholdPx : null,
        settledThresholdPx: startGeometry ? startGeometry.settledThresholdPx : null,
        movementThresholdPx: startGeometry
            ? startGeometry.movementThresholdPx
            : Math.max(1, Number(_NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX) || 24),
        pathDistancePx: 0,
        sampleCount: startRect ? 1 : 0
    };
    _armNekoCatMindYarnDragStaleTimer(_nekoCatMindYarnDragSession);
    return _nekoCatMindYarnDragSession;
}

function _sampleNekoCatMindYarnDragSession(session, rect, geometry) {
    if (!session || !rect) return;
    session.pathDistancePx += _getNekoCatMindYarnRectCenterDistance(session.lastRect, rect);
    session.lastRect = rect;
    session.endRect = rect;
    session.sampleCount += 1;
    if (!geometry) return;
    const distance = Math.max(0, Number(geometry.distancePx));
    session.minDistanceToCatPx = Number.isFinite(Number(session.minDistanceToCatPx))
        ? Math.min(Number(session.minDistanceToCatPx), distance)
        : distance;
    session.maxDistanceToCatPx = Number.isFinite(Number(session.maxDistanceToCatPx))
        ? Math.max(Number(session.maxDistanceToCatPx), distance)
        : distance;
    session.nearThresholdPx = geometry.nearThresholdPx;
    session.settledThresholdPx = geometry.settledThresholdPx;
    session.movementThresholdPx = geometry.movementThresholdPx;
}

function _makeNekoCatMindYarnDragCompletedObservation(session, detail, geometry) {
    if (!session || !geometry) return null;
    const startDistance = Number(session.startDistanceToCatPx);
    const endDistance = Math.max(0, Number(geometry.distancePx));
    const maxDistance = Number.isFinite(Number(session.maxDistanceToCatPx))
        ? Math.max(Number(session.maxDistanceToCatPx), endDistance)
        : endDistance;
    const directApproachDistance = Number.isFinite(startDistance)
        ? startDistance - endDistance
        : 0;
    const maxApproachDistance = Math.max(0, maxDistance - endDistance);
    const movementThreshold = Math.max(
        1,
        Number(session.movementThresholdPx) || Number(_NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX) || 24
    );
    const meaningfulMovement = Math.max(
        Math.abs(directApproachDistance),
        maxApproachDistance,
        Number(session.pathDistancePx) || 0
    );
    if (meaningfulMovement < movementThreshold) return null;
    const timestamp = Number(detail && detail.timestamp) || Date.now();
    const round = (value) => Math.round(Number(value) * 100) / 100;
    return {
        type: _NEKO_CAT_MIND_YARN_DRAG_COMPLETED,
        source: 'cat-yarn-observation-adapter',
        tier: _NEKO_IDLE_TIER_CAT1,
        timestamp: timestamp,
        detail: {
            sessionId: session.sessionId,
            originSource: session.source,
            userInitiated: true,
            coordinateSpace: session.coordinateSpace,
            startedAt: session.startedAt,
            durationMs: Math.max(0, timestamp - session.startedAt),
            startRect: session.startRect,
            endRect: session.endRect,
            pathDistancePx: round(session.pathDistancePx),
            startDistanceToCatPx: Number.isFinite(startDistance) ? round(startDistance) : null,
            startCenterDistanceToCatPx: Number.isFinite(Number(session.startCenterDistanceToCatPx))
                ? round(session.startCenterDistanceToCatPx)
                : null,
            maxDistanceToCatPx: round(maxDistance),
            endDistanceToCatPx: round(endDistance),
            endCenterDistanceToCatPx: round(geometry.centerDistancePx),
            // The direct start-to-end change is the primary offer signal.
            // Maximum excursion and path length remain auxiliary evidence for
            // repeated near-to-near offering, never a far-to-near substitute.
            directApproachDistancePx: round(directApproachDistance),
            approachDistancePx: round(Math.max(0, directApproachDistance)),
            maxApproachDistancePx: round(maxApproachDistance),
            nearThresholdPx: geometry.nearThresholdPx,
            settledThresholdPx: geometry.settledThresholdPx,
            movementThresholdPx: movementThreshold,
            startedFarFromCat: Number.isFinite(startDistance) && startDistance >= geometry.nearThresholdPx,
            movedFarFromCatDuringDrag: maxDistance >= geometry.nearThresholdPx,
            endedNearCat: endDistance < geometry.nearThresholdPx,
            endedSettledNearCat: endDistance <= geometry.settledThresholdPx
        }
    };
}

function _dispatchNekoCatMindYarnDragCompleted(observation) {
    if (!observation) return false;
    try {
        window.dispatchEvent(new CustomEvent(_getNekoCatMindYarnObservationEventName(), {
            detail: observation
        }));
        return true;
    } catch (_) {
        return false;
    }
}

function _afterNekoCatMindYarnJourneyFrame(callback) {
    if (typeof window.requestAnimationFrame === 'function') {
        // The journey listener is registered before this adapter and queues its
        // own RAF from the same minimized-state event. A second RAF ensures its
        // geometry/state synchronization has run before the gate can reopen.
        window.requestAnimationFrame(() => {
            window.requestAnimationFrame(callback);
        });
        return;
    }
    window.setTimeout(callback, 0);
}

function _settleNekoCatMindYarnDragSession(session, detail, geometry, shouldDispatch) {
    _clearNekoCatMindYarnDragStaleTimer();
    _nekoCatMindYarnDragSession = null;
    _nekoCatMindYarnDragActive = false;
    _nekoCatMindYarnSettling = true;
    _nekoCatMindYarnSettlingSessionId = session ? session.sessionId : '';
    const settleSequence = ++_nekoCatMindYarnSettleSequence;
    const observation = shouldDispatch
        ? _makeNekoCatMindYarnDragCompletedObservation(session, detail, geometry)
        : null;
    let finalized = false;
    const finalize = () => {
        if (finalized) return;
        if (settleSequence !== _nekoCatMindYarnSettleSequence || _nekoCatMindYarnDragActive) return;
        finalized = true;
        _clearNekoCatMindYarnSettleTimer();
        _nekoCatMindYarnSettling = false;
        _nekoCatMindYarnSettlingSessionId = '';
        _dispatchNekoCatMindYarnDragCompleted(observation);
    };
    // Hidden/background pages may pause RAF. Let the normal two-frame journey
    // ordering win, but never leave the selector gate closed indefinitely.
    _nekoCatMindYarnSettleTimer = window.setTimeout(
        finalize,
        _NEKO_CAT_MIND_YARN_SETTLE_FALLBACK_MS
    );
    _afterNekoCatMindYarnJourneyFrame(finalize);
}

function _handleNekoCatMindYarnDragPhase(detail, fallbackCoordinateSpace = 'screen') {
    if (!detail || typeof detail !== 'object') return;
    const phase = _getNekoCatMindYarnDragPhase(detail);
    const coordinateSpace = _getNekoCatMindYarnCoordinateSpace(detail, fallbackCoordinateSpace);
    const rect = _getNekoCatMindYarnRect(detail);
    if (!phase) {
        // Poll/resize/pair-move/dock messages are stable renderer facts only;
        // they never synthesize a user gesture or queue an intent observation.
        if (rect) _nekoCatMindStableYarnRectBySpace[coordinateSpace] = rect;
        return;
    }
    const timestamp = Number(detail.timestamp) || Date.now();
    // Explicit ids belong to the embedded Web producer. Once a newer start has
    // replaced the active gesture, delayed phases from the old gesture must not
    // sample or settle the replacement. Desktop/Wayland phases intentionally
    // omit ids and retain their existing end-only compatibility path.
    if (phase !== 'start' && _hasNekoCatMindYarnSessionMismatch(detail)) return;
    if (phase === 'end' || phase === 'cancel') {
        const signature = _getNekoCatMindYarnTerminalSignature(phase, detail, rect, coordinateSpace);
        if (_isNekoCatMindDuplicateYarnTerminal(signature, timestamp)) {
            if (rect) _nekoCatMindStableYarnRectBySpace[coordinateSpace] = rect;
            return;
        }
    }
    const geometry = _getNekoCatMindYarnCatGeometry(rect, coordinateSpace);

    if (phase === 'start') {
        _beginNekoCatMindYarnDragSession(detail, rect, coordinateSpace, geometry);
        return;
    }
    if (phase === 'move') {
        let session = _nekoCatMindYarnDragSession;
        if (!session || session.coordinateSpace !== coordinateSpace) {
            const stableRect = _nekoCatMindStableYarnRectBySpace[coordinateSpace];
            session = _beginNekoCatMindYarnDragSession(
                detail,
                stableRect || rect,
                coordinateSpace,
                _getNekoCatMindYarnCatGeometry(stableRect || rect, coordinateSpace)
            );
        }
        _sampleNekoCatMindYarnDragSession(session, rect, geometry);
        _armNekoCatMindYarnDragStaleTimer(session);
        return;
    }

    let session = _nekoCatMindYarnDragSession;
    if (!session || session.coordinateSpace !== coordinateSpace) {
        const stableRect = _nekoCatMindStableYarnRectBySpace[coordinateSpace];
        // Native Wayland may provide only a settled/end state. The last stable
        // yarn rectangle is the factual start point; the terminal rect is end.
        session = _beginNekoCatMindYarnDragSession(
            detail,
            stableRect || rect,
            coordinateSpace,
            _getNekoCatMindYarnCatGeometry(stableRect || rect, coordinateSpace)
        );
    }
    _sampleNekoCatMindYarnDragSession(session, rect, geometry);
    if (rect) _nekoCatMindStableYarnRectBySpace[coordinateSpace] = rect;
    const shouldDispatch = phase === 'end' && detail.cancelled !== true && detail.moved !== false;
    _settleNekoCatMindYarnDragSession(session, detail, geometry, shouldDispatch);
}

function _getNekoCatMindYarnGateSnapshot() {
    const snapshot = {
        yarnDragActive: _nekoCatMindYarnDragActive === true,
        yarnSettling: _nekoCatMindYarnSettling === true
    };
    const session = _nekoCatMindYarnDragSession;
    const sessionId = session && session.sessionId
        ? session.sessionId
        : _nekoCatMindYarnSettlingSessionId;
    if (sessionId) snapshot.sessionId = sessionId;
    return snapshot;
}

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.NekoCatMindYarnObservationAdapter = Object.freeze({
        getGateSnapshot: _getNekoCatMindYarnGateSnapshot
    });
    window.addEventListener(_NEKO_CAT_MIND_YARN_DRAG_EVENT, (event) => {
        _handleNekoCatMindYarnDragPhase(event && event.detail, 'viewport');
    });
    window.addEventListener('neko:idle-chat-minimized-state', (event) => {
        _handleNekoCatMindYarnDragPhase(
            event && event.detail && typeof event.detail === 'object' ? event.detail : null,
            'screen'
        );
    });
}
