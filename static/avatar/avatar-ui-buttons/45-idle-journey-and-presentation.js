function _isNekoIdleCat1Walking(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    return !!(state &&
        state.profile &&
        state.substate === state.profile.walkingSubstate &&
        !state.pairMovePlan &&
        !state.pairMoveFrame);
}

function _getNekoIdleCurrentLanlanName() {
    return (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
}

function _dispatchNekoIdleReturnBallManualMove(container, reason, extraDetail = {}) {
    _logNekoIdleReturnDragDebug('dispatch', {
        reason: reason,
        containerId: container && container.id,
        dragging: container && container.getAttribute && container.getAttribute('data-dragging'),
        movedDistancePx: extraDetail.movedDistancePx
    });
    window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
        detail: Object.assign({
            reason: reason,
            container: container
        }, extraDetail)
    }));
}

function _getNekoIdleReactChatMinimizedRect() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList || !shell.classList.contains('is-minimized')) return null;
    if (shell.classList.contains('is-collapsing') || shell.classList.contains('is-expanding')) return null;
    if (typeof shell.querySelector !== 'function') return null;
    const icon = shell.querySelector('.react-chat-minimized-icon');
    const iconRect = _getNekoIdleVisibleElementRect(icon);
    if (iconRect) return iconRect;
    if (typeof shell.getBoundingClientRect !== 'function') return null;
    const shellRect = _normalizeNekoIdleScreenRect(shell.getBoundingClientRect());
    if (!shellRect) return null;
    const left = shellRect.left + Math.max(0, (shellRect.width - _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX) / 2);
    const top = shellRect.top + Math.max(0, (shellRect.height - _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX) / 2);
    return {
        left: left,
        top: top,
        width: _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX,
        height: _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX,
        right: left + _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX,
        bottom: top + _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX
    };
}

function _getNekoIdleReactChatMinimizedShell() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList || !shell.classList.contains('is-minimized')) return null;
    if (shell.classList.contains('is-collapsing') ||
        shell.classList.contains('is-expanding') ||
        shell.classList.contains('is-dragging') ||
        shell.classList.contains('is-idle-docked')) {
        return null;
    }
    if (!_getNekoIdleReactChatMinimizedRect()) return null;
    return shell;
}

function _getNekoIdleReactChatExpandedShell() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList || shell.classList.contains('is-minimized')) return null;
    if (shell.classList.contains('is-collapsing') ||
        shell.classList.contains('is-expanding') ||
        shell.classList.contains('is-dragging') ||
        shell.classList.contains('is-idle-docked')) {
        return null;
    }
    if (typeof shell.getBoundingClientRect !== 'function') return null;
    const rect = shell.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return shell;
}

function _normalizeNekoIdleScreenRect(rect) {
    if (!rect || typeof rect !== 'object') return null;
    const left = Number.isFinite(Number(rect.left)) ? Number(rect.left) : Number(rect.x);
    const top = Number.isFinite(Number(rect.top)) ? Number(rect.top) : Number(rect.y);
    const width = Number(rect.width);
    const height = Number(rect.height);
    if (!Number.isFinite(left) || !Number.isFinite(top) ||
        !Number.isFinite(width) || !Number.isFinite(height) ||
        width <= 0 || height <= 0) {
        return null;
    }
    return {
        left: left,
        top: top,
        width: width,
        height: height,
        right: left + width,
        bottom: top + height
    };
}

function _getNekoIdleVisibleElementRect(element) {
    if (!element || element.hidden || typeof element.getBoundingClientRect !== 'function') return null;
    try {
        const style = typeof window.getComputedStyle === 'function'
            ? window.getComputedStyle(element)
            : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) <= 0.01)) {
            return null;
        }
    } catch (_) {}
    return _normalizeNekoIdleScreenRect(element.getBoundingClientRect());
}

function _getNekoIdleReactChatCompactSurfaceRect() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList) return null;
    if (shell.getAttribute('data-chat-surface-mode') !== 'compact') return null;
    if (shell.classList.contains('is-minimized') ||
        shell.classList.contains('is-collapsing') ||
        shell.classList.contains('is-expanding') ||
        shell.classList.contains('is-idle-docked')) {
        return null;
    }

    const root = document.getElementById('react-chat-window-root');
    if (!root || !shell.contains(root)) return null;
    const candidates = [];
    const surfaceShell = root.querySelector('.compact-chat-surface-shell');
    if (surfaceShell) candidates.push(surfaceShell);
    root.querySelectorAll(
        '[data-compact-geometry-owner="surface"][data-compact-geometry-item="input"], ' +
        '[data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]'
    ).forEach((element) => {
        candidates.push(element);
    });

    for (let i = 0; i < candidates.length; i += 1) {
        const rect = _getNekoIdleVisibleElementRect(candidates[i]);
        if (rect) return rect;
    }
    return null;
}

function _getNekoIdleDesktopCompactSurfaceRect() {
    const state = _nekoIdleDesktopCompactSurfaceState;
    if (!state || !state.visible || !state.screenRect) return null;
    if (_nekoIdleDesktopChatMinimizedState &&
        _nekoIdleDesktopChatMinimizedState.minimized &&
        _isNekoIdleDesktopStateNewerThan(_nekoIdleDesktopChatMinimizedState.sourceUpdatedAt, state)) {
        return null;
    }
    if (Date.now() - (state.updatedAt || 0) > _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS) return null;
    const screenRect = _normalizeNekoIdleScreenRect(state.screenRect);
    if (!screenRect) return null;
    const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
    const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
    return {
        left: screenRect.left - screenLeft,
        top: screenRect.top - screenTop,
        width: screenRect.width,
        height: screenRect.height,
        right: screenRect.right - screenLeft,
        bottom: screenRect.bottom - screenTop,
        screenLeft: screenRect.left,
        screenTop: screenRect.top,
        screenRight: screenRect.right,
        screenBottom: screenRect.bottom
    };
}

function _getNekoIdleChatCompactSurfaceRect() {
    return _getNekoIdleReactChatCompactSurfaceRect()
        || _getNekoIdleDesktopCompactSurfaceRect();
}

function _getNekoIdleDesktopChatMinimizedRect() {
    const state = _nekoIdleDesktopChatMinimizedState;
    if (!state || !state.minimized || !state.screenRect) return null;
    if (_nekoIdleDesktopCompactSurfaceState &&
        _nekoIdleDesktopCompactSurfaceState.visible &&
        _isNekoIdleDesktopStateNewerThan(_nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt, state)) {
        return null;
    }
    if (Date.now() - (state.updatedAt || 0) > _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS) return null;
    const screenRect = _normalizeNekoIdleScreenRect(state.screenRect);
    if (!screenRect) return null;
    const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
    const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
    return {
        left: screenRect.left - screenLeft,
        top: screenRect.top - screenTop,
        width: screenRect.width,
        height: screenRect.height,
        right: screenRect.right - screenLeft,
        bottom: screenRect.bottom - screenTop,
        screenLeft: screenRect.left,
        screenTop: screenRect.top,
        screenRight: screenRect.right,
        screenBottom: screenRect.bottom
    };
}

function _isNekoIdleDesktopChatExpandedRecent() {
    const state = _nekoIdleDesktopChatMinimizedState;
    if (!state || state.minimized) return false;
    if (state.expandedRecent === false) return false;
    return Date.now() - (state.updatedAt || 0) <= _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS;
}

function _canNekoIdleCat1MoveSoloWithExpandedChat() {
    return !!(_getNekoIdleReactChatExpandedShell() || _isNekoIdleDesktopChatExpandedRecent());
}

function _getNekoIdleChatMinimizedRect() {
    return _getNekoIdleReactChatMinimizedRect()
        || _getNekoIdleDesktopChatMinimizedRect();
}

function _clampNekoIdleCat1Position(left, top, width, height) {
    return {
        left: Math.round(Math.max(0, Math.min(left, Math.max(0, window.innerWidth - width)))),
        top: Math.round(Math.max(0, Math.min(top, Math.max(0, window.innerHeight - height))))
    };
}

function _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect) {
    // The yarn ball's right side has trailing string space, so right-to-left approaches need an inward visual anchor.
    if (facingRight) return 0;
    const width = Number(chatRect && chatRect.width);
    if (!Number.isFinite(width) || width <= 0) return 0;
    return Math.max(0, Math.min(width, _NEKO_IDLE_CAT1_MINIMIZED_RIGHT_TO_LEFT_APPROACH_PX));
}

function _getNekoIdleCat1TargetMoveDirection(rect, targetLeft) {
    if (!rect || !Number.isFinite(Number(targetLeft))) return null;
    const dx = Number(targetLeft) - rect.left;
    if (Math.abs(dx) <= _NEKO_IDLE_CAT1_MINIMIZED_BACKWARD_RETREAT_TOLERANCE_PX) return null;
    return dx > 0;
}

function _getNekoIdleCat1YarnLookX(chatRect) {
    const left = Number(chatRect && chatRect.left);
    const width = Number(chatRect && chatRect.width);
    if (!Number.isFinite(left) || !Number.isFinite(width) || width <= 0) return NaN;
    const trailingStringPx = _getNekoIdleCat1MinimizedSideApproachOffsetPx(false, chatRect);
    return left + Math.max(1, width - trailingStringPx) / 2;
}

function _resolveNekoIdleCat1StretchFacing(rect, chatRect, fallbackFacingRight) {
    const rectLeft = Number(rect && rect.left);
    const rectWidth = Number(rect && rect.width);
    const yarnLookX = _getNekoIdleCat1YarnLookX(chatRect);
    if (Number.isFinite(rectLeft) && Number.isFinite(rectWidth) && rectWidth > 0 && Number.isFinite(yarnLookX)) {
        const rectCenterX = rectLeft + rectWidth / 2;
        if (Math.abs(yarnLookX - rectCenterX) > _NEKO_IDLE_CAT1_MINIMIZED_BACKWARD_RETREAT_TOLERANCE_PX) {
            return yarnLookX > rectCenterX;
        }
    }
    return !!fallbackFacingRight;
}

function _resolveNekoIdleCat1TargetFacing(rect, target) {
    if (!target) return false;
    const moveFacingRight = _getNekoIdleCat1TargetMoveDirection(rect, target.left);
    if (moveFacingRight !== null) return moveFacingRight;
    if (Object.prototype.hasOwnProperty.call(target, 'lookFacingRight')) {
        return !!target.lookFacingRight;
    }
    return !!target.facingRight;
}

function _resolveNekoIdleCat1FinalTargetFacing(target) {
    if (!target) return false;
    if (Object.prototype.hasOwnProperty.call(target, 'stretchFacingRight')) {
        return !!target.stretchFacingRight;
    }
    if (Object.prototype.hasOwnProperty.call(target, 'lookFacingRight')) {
        return !!target.lookFacingRight;
    }
    return !!target.facingRight;
}

function _makeNekoIdleCat1SideTarget(rect, chatRect, options) {
    const facingRight = !!(options && options.facingRight);
    const rawLeft = Number(options && options.rawLeft);
    const approachOffsetPx = Number(options && options.approachOffsetPx) || 0;
    if (!Number.isFinite(rawLeft)) return null;
    const rawTop = chatRect.top + (chatRect.height - rect.height) / 2;
    const clamped = _clampNekoIdleCat1Position(rawLeft, rawTop, rect.width, rect.height);
    const targetCenterX = clamped.left + rect.width / 2;
    const targetCenterY = clamped.top + rect.height / 2;
    const currentCenterX = rect.left + rect.width / 2;
    const currentCenterY = rect.top + rect.height / 2;
    const dx = targetCenterX - currentCenterX;
    const dy = targetCenterY - currentCenterY;
    const moveFacingRight = _getNekoIdleCat1TargetMoveDirection(rect, clamped.left);
    const stretchFacingRight = _resolveNekoIdleCat1StretchFacing({
        left: clamped.left,
        top: clamped.top,
        width: rect.width,
        height: rect.height
    }, chatRect, facingRight);
    return {
        left: clamped.left,
        top: clamped.top,
        distance: Math.hypot(dx, dy),
        facingRight: facingRight,
        lookFacingRight: facingRight,
        stretchFacingRight: stretchFacingRight,
        moveFacingRight: moveFacingRight,
        approachOffsetPx: approachOffsetPx,
        kind: _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE
    };
}

function _computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight) {
    const profile = _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const approachOffsetPx = _getNekoIdleCat1MinimizedSideApproachOffsetPx(lookFacingRight, chatRect);
    const rawLeft = lookFacingRight
        ? chatRect.left - rect.width - profile.target.gapPx
        : chatRect.right + profile.target.gapPx - approachOffsetPx;
    return _makeNekoIdleCat1SideTarget(rect, chatRect, {
        facingRight: lookFacingRight,
        rawLeft: rawLeft,
        approachOffsetPx: approachOffsetPx
    });
}

// #1749 的本意：在毛球两侧站位点里挑“朝毛球前进即可到达”的那个，避免明显倒退。
// 仅用于本次走路“首次”决定接近侧；之后由提交侧 + 滞回保持，避免每帧重判导致横跳。
function _pickNekoIdleCat1ForwardSideTarget(rect, chatRect) {
    const catCenterX = rect.left + rect.width / 2;
    const chatCenterX = chatRect.left + chatRect.width / 2;
    const lookFacingRight = chatCenterX > catCenterX;
    const sideTarget = _computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight);
    if (!sideTarget || sideTarget.moveFacingRight === null || sideTarget.moveFacingRight === lookFacingRight) {
        return sideTarget;
    }
    const alternateTarget = _computeNekoIdleCat1SideTargetForLook(rect, chatRect, !lookFacingRight);
    if (alternateTarget &&
        (alternateTarget.moveFacingRight === null || alternateTarget.moveFacingRight === lookFacingRight)) {
        return alternateTarget;
    }
    return sideTarget;
}

function _clearNekoIdleCat1WalkApproachSide(container) {
    if (container && _NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP in container) {
        delete container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP];
    }
}

// #1754：判定毛球中心是否已落进猫体 rect（猫已贴上球），贴球后据此避免再朝倒退方向取侧而前后蹭动。
function _isNekoIdleRectCenterInsideRect(innerRect, outerRect) {
    if (!innerRect || !outerRect) return false;
    const innerLeft = Number(innerRect.left);
    const innerTop = Number(innerRect.top);
    const innerWidth = Number(innerRect.width);
    const innerHeight = Number(innerRect.height);
    const outerLeft = Number(outerRect.left);
    const outerTop = Number(outerRect.top);
    const outerWidth = Number(outerRect.width);
    const outerHeight = Number(outerRect.height);
    if (!Number.isFinite(innerLeft) || !Number.isFinite(innerTop) ||
        !Number.isFinite(innerWidth) || !Number.isFinite(innerHeight) ||
        !Number.isFinite(outerLeft) || !Number.isFinite(outerTop) ||
        !Number.isFinite(outerWidth) || !Number.isFinite(outerHeight) ||
        innerWidth <= 0 || innerHeight <= 0 || outerWidth <= 0 || outerHeight <= 0) {
        return false;
    }
    const outerRight = Number.isFinite(Number(outerRect.right)) ? Number(outerRect.right) : outerLeft + outerWidth;
    const outerBottom = Number.isFinite(Number(outerRect.bottom)) ? Number(outerRect.bottom) : outerTop + outerHeight;
    const innerCenterX = innerLeft + innerWidth / 2;
    const innerCenterY = innerTop + innerHeight / 2;
    return innerCenterX >= outerLeft && innerCenterX <= outerRight &&
        innerCenterY >= outerTop && innerCenterY <= outerBottom;
}

// #1754：贴球后“原地以当前朝向站住”的侧目标（distance 0、moveFacingRight null，不再走动）。
function _makeNekoIdleCat1CurrentSideTarget(rect, chatRect, options) {
    const facingRight = !!(options && options.facingRight);
    return {
        left: rect.left,
        top: rect.top,
        distance: 0,
        facingRight: facingRight,
        lookFacingRight: facingRight,
        stretchFacingRight: _resolveNekoIdleCat1StretchFacing(rect, chatRect, facingRight),
        moveFacingRight: null,
        approachOffsetPx: _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect),
        kind: _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE
    };
}

function _getNekoIdleCat1SideTarget(container, chatRect) {
    if (!container || !chatRect || typeof container.getBoundingClientRect !== 'function') return null;
    const rect = container.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;

    // 提交本次走路的接近侧，且只在“猫已整体越到毛球另一侧”时才重选。
    // 若像旧实现那样每帧用 catCenter vs chatCenter 重判接近侧：两侧站位点都落在毛球“对侧”，
    // 猫一旦进入两站位点之间的区间，每帧目标都被指到对面 → 跨过球心就翻面、永不收敛，
    // 表现为返回猫贴着毛球一直抽搐（#1749 残留）。提交侧 + 滞回即可根除该横跳。
    const catCenterX = rect.left + rect.width / 2;
    const committed = container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP];
    const hasCommitted = committed === true || committed === false;
    let lookFacingRight = null;
    if (hasCommitted) {
        if (catCenterX >= chatRect.left && catCenterX <= chatRect.right) {
            lookFacingRight = committed; // 仍在毛球水平跨度内：保持提交侧，不在球心附近翻面
        } else if (committed === true && catCenterX > chatRect.right) {
            lookFacingRight = false; // 已整体越到毛球右侧 → 重选接近侧
        } else if (committed === false && catCenterX < chatRect.left) {
            lookFacingRight = true; // 已整体越到毛球左侧 → 重选接近侧
        } else {
            lookFacingRight = committed; // 在毛球外、且就处于提交侧 → 保持
        }
    }

    const target = lookFacingRight === null
        ? _pickNekoIdleCat1ForwardSideTarget(rect, chatRect)
        : _computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight);

    // #1754：毛球中心已落进猫体 rect（猫已贴上球），且到该侧位点仍需倒退（moveFacingRight 与朝向
    // 相反）时就别再走过去——原地以当前朝向站住，避免贴球时反复前后蹭动抽搐。提交侧随之钉在当前朝向。
    if (target &&
        _isNekoIdleRectCenterInsideRect(chatRect, rect) &&
        target.moveFacingRight !== null &&
        target.moveFacingRight !== target.lookFacingRight) {
        const currentSideTarget = _makeNekoIdleCat1CurrentSideTarget(rect, chatRect, {
            facingRight: target.lookFacingRight
        });
        if (currentSideTarget) {
            container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP] = !!currentSideTarget.lookFacingRight;
            return currentSideTarget;
        }
    }

    if (target) {
        container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP] = !!target.lookFacingRight;
    }
    return target;
}

function _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect) {
    if (!surfaceRect) return null;
    const capInset = Math.max(
        _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX,
        surfaceRect.height / 2 + _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX
    );
    const edgePadding = Math.min(capInset, Math.max(0, surfaceRect.width / 2));
    const minEdgeCenterX = surfaceRect.left + edgePadding;
    const maxEdgeCenterX = surfaceRect.right - edgePadding;
    return {
        minEdgeCenterX: minEdgeCenterX,
        maxEdgeCenterX: maxEdgeCenterX,
        fallbackCenterX: surfaceRect.left + surfaceRect.width / 2
    };
}

function _getNekoIdleCat1CompactTopEdgeAnchorRatio(surfaceRect, targetEdgeCenterX) {
    const bounds = _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect);
    if (!bounds) return null;
    const span = bounds.maxEdgeCenterX - bounds.minEdgeCenterX;
    if (span <= 0) return 0.5;
    const ratio = (Number(targetEdgeCenterX) - bounds.minEdgeCenterX) / span;
    if (!Number.isFinite(ratio)) return null;
    return Math.max(0, Math.min(1, ratio));
}

function _getNekoIdleCat1CompactTopEdgeCenterFromAnchor(surfaceRect, anchorRatio) {
    const bounds = _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect);
    if (!bounds) return null;
    if (anchorRatio === null || anchorRatio === undefined || anchorRatio === '') return null;
    const ratio = Number(anchorRatio);
    if (!Number.isFinite(ratio)) return null;
    const span = bounds.maxEdgeCenterX - bounds.minEdgeCenterX;
    if (span <= 0) return bounds.fallbackCenterX;
    return bounds.minEdgeCenterX + Math.max(0, Math.min(1, ratio)) * span;
}

function _getNekoIdleCat1CompactTopEdgeTarget(container, surfaceRect, options = {}) {
    if (!container || !surfaceRect || typeof container.getBoundingClientRect !== 'function') return null;
    const rect = container.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;

    const catCenterX = rect.left + rect.width / 2;
    const bounds = _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect);
    if (!bounds) return null;
    const anchoredCenterX = _getNekoIdleCat1CompactTopEdgeCenterFromAnchor(surfaceRect, options.anchorRatio);
    const targetEdgeCenterX = Number.isFinite(anchoredCenterX)
        ? anchoredCenterX
        : (bounds.maxEdgeCenterX >= bounds.minEdgeCenterX
            ? Math.max(bounds.minEdgeCenterX, Math.min(catCenterX, bounds.maxEdgeCenterX))
            : bounds.fallbackCenterX);
    const anchorRatio = _getNekoIdleCat1CompactTopEdgeAnchorRatio(surfaceRect, targetEdgeCenterX);
    const rawLeft = targetEdgeCenterX - rect.width / 2;

    const rawTop = surfaceRect.top - rect.height + _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_OVERLAP_PX;
    const clamped = _clampNekoIdleCat1Position(rawLeft, rawTop, rect.width, rect.height);
    const targetCenterX = clamped.left + rect.width / 2;
    const targetCenterY = clamped.top + rect.height / 2;
    const currentCenterX = rect.left + rect.width / 2;
    const currentCenterY = rect.top + rect.height / 2;
    const dx = targetCenterX - currentCenterX;
    const dy = targetCenterY - currentCenterY;
    return {
        left: clamped.left,
        top: clamped.top,
        distance: Math.hypot(dx, dy),
        facingRight: targetEdgeCenterX > catCenterX,
        anchorRatio: anchorRatio,
        kind: _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
    };
}

function _getNekoIdleCat1Target(container, chatRect, options = {}) {
    const minimizedSideTarget = _getNekoIdleCat1SideTarget(container, chatRect);
    if (minimizedSideTarget) {
        return minimizedSideTarget;
    }

    const compactSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const compactTarget = _getNekoIdleCat1CompactTopEdgeTarget(container, compactSurfaceRect, {
        anchorRatio: options.anchorRatio
    });
    const compactBlocked = !!(options && options.compactTopEdgeBlocked);
    if (!compactBlocked &&
        compactTarget &&
        compactTarget.distance <= _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_FOLLOW_DISTANCE_PX) {
        return compactTarget;
    }
    return null;
}

function _setNekoIdleCat1ContainerPosition(container, left, top) {
    if (!container) return;
    container.style.left = `${Math.round(left)}px`;
    container.style.top = `${Math.round(top)}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
}

function _setNekoIdleCat1PairMoveChatPosition(shell, left, top) {
    if (!shell) return;
    shell.style.left = `${Math.round(left)}px`;
    shell.style.top = `${Math.round(top)}px`;
    shell.style.right = '';
    shell.style.bottom = '';
    shell.style.transform = 'none';
}

function _rememberNekoIdleDesktopChatPairMoveRect(screenRect) {
    const normalized = _normalizeNekoIdleScreenRect(screenRect);
    if (!normalized) return null;
    const updatedAt = Date.now();
    _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
        true,
        normalized,
        updatedAt,
        updatedAt,
        false
    );
    _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
        false,
        null,
        updatedAt,
        updatedAt
    );
    return normalized;
}

function _getNekoIdleDesktopChatPairMoveSignature(screenRect) {
    const normalized = _normalizeNekoIdleScreenRect(screenRect);
    if (!normalized) return '';
    return [
        normalized.left,
        normalized.top,
        normalized.width,
        normalized.height
    ].join(':');
}

function _dispatchNekoIdleDesktopChatPairMoveBounds(screenRect, options = {}) {
    const force = !!(options && options.force);
    if (_isNekoDesktopLinuxRuntime() && !force) return false;
    const normalized = _rememberNekoIdleDesktopChatPairMoveRect(screenRect);
    if (!normalized) return false;
    const now = Date.now();
    const signature = _getNekoIdleDesktopChatPairMoveSignature(normalized);
    if (!force) {
        if (signature && signature === _nekoIdleDesktopChatPairMoveLastDispatchSignature) return false;
        if (now - _nekoIdleDesktopChatPairMoveLastDispatchAt < _NEKO_IDLE_CAT1_DESKTOP_PAIR_MOVE_SYNC_MIN_MS) return false;
    }
    _nekoIdleDesktopChatPairMoveLastDispatchAt = now;
    _nekoIdleDesktopChatPairMoveLastDispatchSignature = signature;
    const source = typeof options.source === 'string' && options.source ? options.source : 'cat1-pair-move';
    const reason = typeof options.reason === 'string' && options.reason ? options.reason : source;
    const message = {
        action: 'idle_chat_pair_move_bounds',
        source: source,
        reason: reason,
        lanlan_name: _getNekoIdleCurrentLanlanName(),
        screenRect: {
            left: normalized.left,
            top: normalized.top,
            width: normalized.width,
            height: normalized.height
        },
        force: force,
        timestamp: now
    };
    let dispatched = false;
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-chat-pair-move-bounds', {
            detail: message
        }));
        dispatched = true;
    } catch (_) {}
    const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
    if (channel && typeof channel.postMessage === 'function') {
        try {
            channel.postMessage(message);
            dispatched = true;
        } catch (_) {}
    }
    return dispatched;
}

function _getNekoIdleCat1PairMoveChatTarget() {
    const shell = _getNekoIdleReactChatMinimizedShell();
    if (shell) {
        const rect = _getNekoIdleReactChatMinimizedRect();
        if (rect && rect.width > 0 && rect.height > 0) {
            return {
                mode: 'dom',
                shell: shell,
                rect: rect
            };
        }
    }
    const desktopRect = _getNekoIdleDesktopChatMinimizedRect();
    if (desktopRect && desktopRect.width > 0 && desktopRect.height > 0) {
        return {
            mode: 'desktop',
            shell: null,
            rect: desktopRect,
            screenRect: {
                left: desktopRect.screenLeft,
                top: desktopRect.screenTop,
                width: desktopRect.width,
                height: desktopRect.height
            }
        };
    }
    return null;
}

function _clampNekoIdleCat1MoveVector(catRect, chatRect, desiredDx, desiredDy) {
    const minDx = chatRect ? Math.max(-catRect.left, -chatRect.left) : -catRect.left;
    const maxDx = chatRect
        ? Math.min(window.innerWidth - catRect.right, window.innerWidth - chatRect.right)
        : window.innerWidth - catRect.right;
    const minDy = chatRect ? Math.max(-catRect.top, -chatRect.top) : -catRect.top;
    const maxDy = chatRect
        ? Math.min(window.innerHeight - catRect.bottom, window.innerHeight - chatRect.bottom)
        : window.innerHeight - catRect.bottom;
    const dx = Math.max(minDx, Math.min(desiredDx, maxDx));
    const dy = Math.max(minDy, Math.min(desiredDy, maxDy));
    return {
        dx: dx,
        dy: dy,
        distance: Math.hypot(dx, dy)
    };
}

function _pickNekoIdleCat1MoveVector(catRect, chatRect, distance, minUsableDistance) {
    const attempts = 10;
    const fallbackAngles = [0, Math.PI, Math.PI / 2, -Math.PI / 2, Math.PI / 4, -Math.PI / 4, Math.PI * 3 / 4, -Math.PI * 3 / 4];
    for (let i = 0; i < attempts + fallbackAngles.length; i += 1) {
        const angle = i < attempts ? Math.random() * Math.PI * 2 : fallbackAngles[i - attempts];
        const vector = _clampNekoIdleCat1MoveVector(
            catRect,
            chatRect,
            Math.cos(angle) * distance,
            Math.sin(angle) * distance
        );
        if (vector.distance >= minUsableDistance) return vector;
    }
    return null;
}

function _hasNekoIdleCat1MoveVectorSpace(catRect, chatRect, distance, minUsableDistance) {
    const angles = [0, Math.PI, Math.PI / 2, -Math.PI / 2, Math.PI / 4, -Math.PI / 4, Math.PI * 3 / 4, -Math.PI * 3 / 4];
    for (let i = 0; i < angles.length; i += 1) {
        const angle = angles[i];
        const vector = _clampNekoIdleCat1MoveVector(
            catRect,
            chatRect,
            Math.cos(angle) * distance,
            Math.sin(angle) * distance
        );
        if (vector.distance >= minUsableDistance) return true;
    }
    return false;
}

function _getNekoIdleCat1PairMovePlan(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const config = profile.pairMove || {};
    const container = _getNekoIdleReturnContainerFromButton(button);
    const chatTarget = _getNekoIdleCat1PairMoveChatTarget();
    if (chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()) return null;
    const canMoveSolo = chatTarget ? false : _canNekoIdleCat1MoveSoloWithExpandedChat();
    if (!container || (!chatTarget && !canMoveSolo)) return null;
    if (container.getAttribute('data-dragging') === 'true') return null;
    if (_isNekoIdleReturnDragActionActive(button)) return null;
    const catRect = container.getBoundingClientRect();
    const chatRect = chatTarget ? chatTarget.rect : null;
    if (!catRect || catRect.width <= 0 || catRect.height <= 0) {
        return null;
    }
    if (chatTarget) {
        if (!chatRect || chatRect.width <= 0 || chatRect.height <= 0) return null;
        const target = _getNekoIdleCat1Target(container, chatRect, {
            compactTopEdgeBlocked: !!(state && state.compactTopEdgeRearmRequired)
        });
        if (!target || target.distance > profile.target.exitDistancePx) return null;
    }

    const minDistance = Math.max(1, Number(config.minDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DISTANCE_PX);
    const maxDistance = Math.max(minDistance, Number(config.maxDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX);
    const minUsableDistance = Math.max(1, Number(config.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX);
    const desiredDistance = minDistance + Math.random() * (maxDistance - minDistance);
    const moveVector = _pickNekoIdleCat1MoveVector(catRect, chatTarget ? chatRect : null, desiredDistance, minUsableDistance);
    if (!moveVector) return null;
    const speed = Math.max(1, Number(config.speedPxPerSec) || _NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC);
    const minDuration = Math.max(1, Number(config.minDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DURATION_MS);
    const maxDuration = Math.max(minDuration, Number(config.maxDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DURATION_MS);
    const durationMs = Math.max(minDuration, Math.min(maxDuration, Math.round(moveVector.distance / speed * 1000)));
    return {
        chatMode: chatTarget ? chatTarget.mode : 'solo',
        shell: chatTarget ? chatTarget.shell : null,
        container: container,
        catStartLeft: catRect.left,
        catStartTop: catRect.top,
        chatStartLeft: chatRect ? chatRect.left : null,
        chatStartTop: chatRect ? chatRect.top : null,
        chatStartScreenLeft: chatTarget && chatTarget.screenRect ? chatTarget.screenRect.left : null,
        chatStartScreenTop: chatTarget && chatTarget.screenRect ? chatTarget.screenRect.top : null,
        chatWidth: chatRect ? chatRect.width : null,
        chatHeight: chatRect ? chatRect.height : null,
        dx: moveVector.dx,
        dy: moveVector.dy,
        durationMs: durationMs
    };
}

function _easeNekoIdleCat1PairMove(progress) {
    const p = Math.max(0, Math.min(1, Number(progress) || 0));
    return p < 0.5
        ? 2 * p * p
        : 1 - Math.pow(-2 * p + 2, 2) / 2;
}

function _applyNekoIdleCat1PairMovePlan(plan, progress) {
    if (!plan || !plan.container) return;
    const eased = _easeNekoIdleCat1PairMove(progress);
    const offsetX = plan.dx * eased;
    const offsetY = plan.dy * eased;
    _setNekoIdleCat1ContainerPosition(plan.container, plan.catStartLeft + offsetX, plan.catStartTop + offsetY);
    if (plan.chatMode === 'desktop') {
        _dispatchNekoIdleDesktopChatPairMoveBounds({
            left: plan.chatStartScreenLeft + offsetX,
            top: plan.chatStartScreenTop + offsetY,
            width: plan.chatWidth,
            height: plan.chatHeight
        }, {
            force: progress >= 1
        });
    } else if (plan.chatMode === 'dom') {
        _setNekoIdleCat1PairMoveChatPosition(plan.shell, plan.chatStartLeft + offsetX, plan.chatStartTop + offsetY);
    }
}

function _dispatchNekoIdleCat1MotionInputRegionState(state, active, reason, plan) {
    if (!state || !_isNekoDesktopLinuxRuntime()) return;
    if (active) {
        const shouldSuppress = (plan && plan.chatMode === 'solo' && _canNekoIdleCat1MoveSoloWithExpandedChat()) ||
            state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
        if (!shouldSuppress) return;
        state.inputRegionMotionSuppressed = true;
        if (plan) plan.inputRegionSuppressed = true;
    } else if (!state.inputRegionMotionSuppressed && !(plan && plan.inputRegionSuppressed)) {
        return;
    }
    if (!active) state.inputRegionMotionSuppressed = false;
    const container = plan && plan.container ? plan.container : null;
    window.dispatchEvent(new CustomEvent('neko:idle-cat1-motion-input-region-state', {
        detail: {
            active: !!active,
            reason: reason || 'cat1-motion',
            containerId: container && container.id ? container.id : '',
            chatMode: plan && plan.chatMode ? plan.chatMode : ''
        }
    }));
}

function _setNekoIdleCat1Substate(button, substate, options = {}) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const previousSubstate = state.substate;
    if (substate === profile.walkingSubstate) {
        _cancelNekoIdleReturnPendingWalk(state);
    }
    if (substate !== profile.finishingSubstate) {
        _cancelNekoIdleReturnSubactionSettleTimer(state);
    }
    if (substate === profile.walkingSubstate) {
        state.actionSettled = false;
    }
    state.substate = substate;
    if (Object.prototype.hasOwnProperty.call(options, 'facingRight')) {
        state.facingRight = !!options.facingRight;
    }
    _setNekoIdleCat1Classes(button, state);
    if (state.paused) return;
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            _getNekoIdleCat1ArtSource(button),
            profile.tier,
            { animate: options.animate !== false }
        );
    }
    if (
        substate === profile.finishingSubstate &&
        previousSubstate !== profile.finishingSubstate &&
        !state.paused
    ) {
        _scheduleNekoIdleReturnSubactionSettle(button);
    }
}

function _finishNekoIdleCat1Walk(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const targetKind = state.targetKind || (state.target && state.target.kind) || '';
    // A delayed frame / observer sync can arrive after this approach has
    // already resolved. Do not re-roll the local probability or run the
    // opposite tail; a later walk start is the only reset point.
    if (targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE && state.walkFinishResolution) {
        return;
    }
    _cancelNekoIdleCat1Frame(state);
    _clearNekoIdleCat1WalkApproachSide(_getNekoIdleReturnContainerFromButton(button));
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-walk-finish');
    state.target = null;
    state.lastStepAt = 0;
    state.actionSettled = false;
    _resetNekoIdleCat1WalkSpeed(state);
    if (targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE) {
        const walkFinishResolution = Math.random() < _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY
            ? 'play'
            : 'stretch';
        state.walkFinishResolution = walkFinishResolution;
        _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT, {
            tier: _NEKO_IDLE_TIER_CAT1, source: 'cat1-walk-finish', timestamp: Date.now()
        });
        if (walkFinishResolution === 'play' && _playNekoIdleCat1PlayAction(button)) {
            state.substate = state.profile.idleSubstate;
            state.targetKind = targetKind;
            state.actionSettled = true;
            _setNekoIdleCat1Classes(button, state);
            return;
        }
        // The visual runner can still reject because the local presentation
        // changed meanwhile. Resolve that same approach deterministically to
        // stretch instead of retrying the random branch on a later callback.
        state.walkFinishResolution = 'stretch';
    }
    _setNekoIdleCat1Substate(button, state.profile.finishingSubstate, { animate: true });
}

function _finishNekoIdleCat1CompactTopEdgeWalk(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const settledSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const settledTarget = state.target;
    _cancelNekoIdleCat1Frame(state);
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-compact-top-edge-walk-finish');
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleCat1PairMove(state);
    const settleToken = state.settleToken || 0;
    state.substate = profile.idleSubstate;
    state.target = null;
    state.lastStepAt = 0;
    state.targetKind = _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    state.actionSettled = true;
    state.compactTopEdgeRearmRequired = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _rememberNekoIdleCat1CompactFollowAnchor(state, settledSurfaceRect, settledTarget);
    _rememberNekoIdleCat1CompactFollowSurface(state, settledSurfaceRect, _getNekoIdleNowMs());
    _setNekoIdleCat1Classes(button, state);
    _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DONE, {
        source: 'cat1-journey',
        tier: _NEKO_IDLE_TIER_CAT1,
        reason: 'compact-top-edge-walk-finish',
        targetKind: state.targetKind
    });

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            profile.assets.idle(),
            profile.tier,
            { animate: true }
        );
    }
    setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState ||
            latestState.settleToken !== settleToken ||
            latestState.substate !== profile.idleSubstate ||
            latestState.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE ||
            !latestState.actionSettled) {
            return;
        }
        latestState.facingRight = false;
        _setNekoIdleCat1Classes(button, latestState);
        _cancelNekoIdleCat1PairMove(latestState);
    }, profile.settle.resetFacingAfterMs);
}

function _settleNekoIdleReturnSubactionToIdle(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.substate !== state.profile.finishingSubstate || state.paused) return;
    const profile = state.profile;
    const shouldRecheckTargetAfterSettle = !!(state.target ||
        state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE ||
        state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    state.substate = profile.idleSubstate;
    state.target = null;
    state.lastStepAt = 0;
    state.actionSettled = true;
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Classes(button, state);

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            profile.assets.idle(),
            profile.tier,
            { animate: true }
        );
    }
    _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_STRETCH_DONE_NEAR_CHAT, {
        source: 'cat1-journey',
        tier: profile.tier,
        reason: 'stretch-settled',
        targetKind: state.targetKind || ''
    });

    if (shouldRecheckTargetAfterSettle &&
        (_getNekoIdleChatMinimizedRect() || _getNekoIdleChatCompactSurfaceRect())) {
        _scheduleNekoIdleCat1JourneySync(button);
    }

    setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState ||
            latestState.substate !== profile.idleSubstate ||
            !latestState.actionSettled) {
            return;
        }
        latestState.facingRight = false;
        _setNekoIdleCat1Classes(button, latestState);
        if (latestState.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) {
            _cancelNekoIdleCat1PairMove(latestState);
            return;
        }
    }, profile.settle.resetFacingAfterMs);
}

function _scheduleNekoIdleReturnSubactionSettle(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.paused || state.substate !== state.profile.finishingSubstate) return;
    if (state.settleTimer) return;

    const profile = state.profile;
    const token = (state.settleToken || 0) + 1;
    state.settleToken = token;
    const startedAt = Date.now();
    const finishingSrc = profile.assets.finishing();
    _getNekoIdleGifDurationMs(finishingSrc).then((durationMs) => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState || latestState.settleToken !== token) return;
        if (state.substate !== profile.finishingSubstate || state.paused) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, durationMs - elapsedMs) + profile.settle.finalHoldMs;
        state.settleTimer = setTimeout(() => {
            const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
            if (!currentState || currentState.settleToken !== token) return;
            state.settleTimer = 0;
            _settleNekoIdleReturnSubactionToIdle(button);
        }, delayMs);
    });
}

function _pickNekoIdleWeightedDelayMs(choices) {
    if (!choices || choices.length === 0) return 0;

    const totalWeight = choices.reduce((sum, choice) => {
        const weight = Number(choice && choice.weight);
        return sum + (Number.isFinite(weight) && weight > 0 ? weight : 0);
    }, 0);
    if (totalWeight <= 0) return 0;

    let cursor = Math.random() * totalWeight;
    for (const choice of choices) {
        const weight = Number(choice && choice.weight);
        if (!Number.isFinite(weight) || weight <= 0) continue;
        cursor -= weight;
        if (cursor > 0) continue;

        const minMs = Math.max(0, Math.round(Number(choice.minMs) || 0));
        const maxMs = Math.max(minMs, Math.round(Number(choice.maxMs) || minMs));
        if (maxMs <= minMs) return minMs;
        return minMs + Math.round(Math.random() * (maxMs - minMs));
    }
    return 0;
}

function _pickNekoIdleReturnSubactionStartDelayMs(profile) {
    const choices = profile && profile.startDelay && Array.isArray(profile.startDelay.choices)
        ? profile.startDelay.choices
        : null;
    return _pickNekoIdleWeightedDelayMs(choices);
}

function _pickNekoIdleCat1PairMoveDelayMs(profile) {
    const choices = profile && profile.pairMove && Array.isArray(profile.pairMove.intervalChoices)
        ? profile.pairMove.intervalChoices
        : null;
    return _pickNekoIdleWeightedDelayMs(choices);
}

function _updateNekoIdleCat1WalkSpeedRate(button, state, distance) {
    if (!state) return 1;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const targetConfig = profile.target || {};
    const maxRate = Math.max(1, Number(targetConfig.maxSpeedRate) || 1);
    const previousDistance = Number(state.walkPreviousDistance) || 0;
    const currentDistance = Math.max(0, Number(distance) || 0);
    const threshold = Math.max(0, Number(targetConfig.distanceIncreaseThresholdPx) || 0);
    const growthForMaxRate = Math.max(1, Number(targetConfig.distanceGrowthForMaxRatePx) || 1);

    if (previousDistance > 0) {
        if (currentDistance > previousDistance + threshold) {
            // 落后了（毛球被移远）：累计落后量，提升追赶倍率
            state.walkDistanceGrowthPx = Math.max(
                0,
                (Number(state.walkDistanceGrowthPx) || 0) + (currentDistance - previousDistance)
            );
        } else if (currentDistance < previousDistance) {
            // 正在收敛：回落累计落后量，避免一次瞬时变远把倍率永久钉死在 maxRate
            state.walkDistanceGrowthPx = Math.max(
                0,
                (Number(state.walkDistanceGrowthPx) || 0) - (previousDistance - currentDistance)
            );
        }
        const progress = Math.min(1, (Number(state.walkDistanceGrowthPx) || 0) / growthForMaxRate);
        const nextRate = Math.min(maxRate, 1 + (maxRate - 1) * progress);
        if (nextRate !== state.walkSpeedRate) {
            state.walkSpeedRate = nextRate;
            _setNekoIdleCat1Classes(button, state);
        }
    }

    state.walkPreviousDistance = currentDistance;
    return Math.max(1, Number(state.walkSpeedRate) || 1);
}

function _stepNekoIdleCat1Walk(button, timestamp) {
    const state = _getNekoIdleCat1Journey(button);
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!state || !container || state.paused || state.substate !== profile.walkingSubstate) {
        if (state) state.frame = 0;
        return;
    }

    const chatRect = _getNekoIdleChatMinimizedRect();
    const rawCompactAnchorRatio = state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
        ? state.compactFollowAnchorRatio
        : null;
    const compactAnchorRatio = rawCompactAnchorRatio === null || rawCompactAnchorRatio === undefined
        ? NaN
        : Number(rawCompactAnchorRatio);
    const target = _getNekoIdleCat1Target(container, chatRect, {
        anchorRatio: Number.isFinite(compactAnchorRatio) ? compactAnchorRatio : null,
        compactTopEdgeBlocked: !!state.compactTopEdgeRearmRequired
    });
    if (!target) {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }

    state.target = target;
    state.targetKind = target.kind || '';
    const rect = container.getBoundingClientRect();
    state.facingRight = _resolveNekoIdleCat1TargetFacing(rect, target);
    _setNekoIdleCat1Classes(button, state);
    const speedRate = _updateNekoIdleCat1WalkSpeedRate(button, state, target.distance);
    if (target.distance <= profile.target.exitDistancePx) {
        _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
        if (target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) {
            _finishNekoIdleCat1CompactTopEdgeWalk(button);
        } else {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _finishNekoIdleCat1Walk(button);
        }
        return;
    }

    const lastStepAt = state.lastStepAt || timestamp;
    const elapsedMs = Math.max(
        profile.target.minStepMs,
        Math.min(timestamp - lastStepAt, profile.target.maxStepMs)
    );
    state.lastStepAt = timestamp;
    const stepDistance = (profile.target.speedPxPerSec * speedRate * elapsedMs) / 1000;
    const ratio = target.distance > 0 ? Math.min(1, stepDistance / target.distance) : 1;
    const nextLeft = rect.left + (target.left - rect.left) * ratio;
    const nextTop = rect.top + (target.top - rect.top) * ratio;
    _setNekoIdleCat1ContainerPosition(container, nextLeft, nextTop);

    state.frame = window.requestAnimationFrame((nextTimestamp) => {
        _stepNekoIdleCat1Walk(button, nextTimestamp);
    });
}

function _startNekoIdleCat1Walk(button, target) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    if (_isNekoIdleCat1EdgePeekActive(button)) {
        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
        return;
    }
    if (_isNekoIdleReturnDragActionActive(button)) return;
    if (_isNekoIdleCat1IndependentActionActive(button)) return;
    const walkContainer = _getNekoIdleReturnContainerFromButton(button);
    const walkDragging = walkContainer && walkContainer.getAttribute('data-dragging');
    if (walkDragging && walkDragging !== 'false') return;
    const profile = state.profile;
    const currentRect = walkContainer && walkContainer.getBoundingClientRect
        ? walkContainer.getBoundingClientRect()
        : null;
    state.target = target;
    state.targetKind = target && target.kind ? target.kind : '';
    state.facingRight = _resolveNekoIdleCat1TargetFacing(currentRect, target);
    if (state.substate !== profile.walkingSubstate) {
        state.lastStepAt = 0;
        _resetNekoIdleCat1WalkSpeed(state);
        _resetNekoIdleCat1WalkFinishResolution(state);
        state.walkPreviousDistance = Math.max(0, Number(target && target.distance) || 0);
        _setNekoIdleCat1Substate(button, profile.walkingSubstate, { animate: false, facingRight: state.facingRight });
    } else {
        _setNekoIdleCat1Classes(button, state);
    }
    _dispatchNekoIdleCat1MotionInputRegionState(state, true, 'cat1-walk-start');
    if (!state.frame && !state.paused) {
        const timestamp = (typeof performance !== 'undefined' && typeof performance.now === 'function')
            ? performance.now()
            : Date.now();
        _stepNekoIdleCat1Walk(button, timestamp);
    }
}

function _scheduleNekoIdleCat1WalkStart(button, target) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.paused) return;
    if (_isNekoIdleCat1IndependentActionActive(button)) return;
    if (_isNekoIdleCat1EdgePeekActive(button)) {
        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
        return;
    }
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    _cancelNekoIdleCat1PairMove(state);
    if (state.substate === profile.walkingSubstate) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    state.target = target;
    state.targetKind = target && target.kind ? target.kind : '';
    const container = _getNekoIdleReturnContainerFromButton(button);
    const rect = container && container.getBoundingClientRect ? container.getBoundingClientRect() : null;
    state.facingRight = _resolveNekoIdleCat1TargetFacing(rect, target);
    _setNekoIdleCat1Classes(button, state);
    const art = button.querySelector('.neko-idle-return-art');
    if (art && art.__nekoIdleHoverSrc) {
        state.pendingWalkReady = true;
        state.pendingWalkDelayMs = 0;
        if (!art.__nekoIdleHoverTimer) {
            _finishNekoIdleHoverArtAfterPlayback(art, profile.tier);
        }
        return;
    }
    if (state.pendingWalkReady) {
        state.pendingWalkReady = false;
        _startNekoIdleCat1Walk(button, target);
        return;
    }
    if (state.pendingWalkTimer) return;

    const compactTopEdgeTarget = target && target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    const delayMs = compactTopEdgeTarget ? 0 : _pickNekoIdleReturnSubactionStartDelayMs(profile);
    state.pendingWalkDelayMs = delayMs;
    if (delayMs <= 0) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    const token = (state.pendingWalkToken || 0) + 1;
    state.pendingWalkToken = token;
    state.pendingWalkTimer = setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState || latestState.pendingWalkToken !== token) return;
        latestState.pendingWalkTimer = 0;
        latestState.pendingWalkDelayMs = 0;
        latestState.pendingWalkReady = true;
        _syncNekoIdleCat1Journey(button);
    }, delayMs);
}

function _canScheduleNekoIdleCat1PairMove(button, state) {
    if (!button || !state || state.paused || state.pairMovePlan || state.pairMoveFrame) return false;
    if (_isNekoIdleCat1EdgePeekActive(button)) return false;
    if (_isNekoIdleCat1IndependentActionActive(button)) return false;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    if (state.substate !== profile.idleSubstate || !state.actionSettled) return false;
    if (state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) return false;
    if (state.pendingWalkTimer || state.pendingWalkReady || state.frame || state.settleTimer) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;

    const art = button.querySelector('.neko-idle-return-art');
    if (art && art.__nekoIdleHoverSrc) {
        if (!art.__nekoIdleHoverTimer) {
            _finishNekoIdleHoverArtAfterPlayback(art, profile.tier);
        }
        return false;
    }

    const container = _getNekoIdleReturnContainerFromButton(button);
    const chatTarget = _getNekoIdleCat1PairMoveChatTarget();
    if (chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()) return false;
    const canMoveSolo = chatTarget ? false : _canNekoIdleCat1MoveSoloWithExpandedChat();
    if (!container || (!chatTarget && !canMoveSolo)) return false;
    if (container.style.display === 'none' || container.getAttribute('data-dragging') === 'true') return false;

    const catRect = container.getBoundingClientRect();
    const chatRect = chatTarget ? chatTarget.rect : null;
    if (!catRect || catRect.width <= 0 || catRect.height <= 0) {
        return false;
    }

    if (chatTarget) {
        if (!chatRect || chatRect.width <= 0 || chatRect.height <= 0) return false;
        const target = _getNekoIdleCat1Target(container, chatRect, {
            compactTopEdgeBlocked: !!(state && state.compactTopEdgeRearmRequired)
        });
        if (!target || target.distance > profile.target.exitDistancePx) return false;
    }

    const config = profile.pairMove || {};
    const minUsableDistance = Math.max(1, Number(config.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX);
    const maxDistance = Math.max(1, Number(config.maxDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX);
    return _hasNekoIdleCat1MoveVectorSpace(
        catRect,
        chatTarget ? chatRect : null,
        maxDistance,
        minUsableDistance
    );
}

function _finishNekoIdleCat1PairMove(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.pairMovePlan) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    _applyNekoIdleCat1PairMovePlan(state.pairMovePlan, 1);
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-pair-move-finish', state.pairMovePlan);
    state.pairMoveFrame = 0;
    state.pairMovePlan = null;
    state.substate = profile.idleSubstate;
    state.target = null;
    state.targetKind = '';
    state.actionSettled = true;
    state.facingRight = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Classes(button, state);
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(art, profile.assets.idle(), profile.tier, { animate: false });
    }
    _reportNekoCatMindStateActionResult(state, _NEKO_CAT_MIND_ACTION_RESULTS.DONE, {
        reason: 'cat1-pair-move-finish', detail: { restored: true }
    });
}

function _stepNekoIdleCat1PairMove(button, startedAt, timestamp) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.pairMovePlan || state.paused) {
        if (state) state.pairMoveFrame = 0;
        return;
    }
    const plan = state.pairMovePlan;
    const chatAvailable = plan.chatMode === 'desktop'
        ? _getNekoIdleDesktopChatMinimizedRect()
        : (plan.chatMode === 'dom'
            ? _getNekoIdleReactChatMinimizedShell()
            : _canNekoIdleCat1MoveSoloWithExpandedChat());
    if (!chatAvailable || plan.container.getAttribute('data-dragging') === 'true') {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }
    const elapsedMs = Math.max(0, timestamp - startedAt);
    const progress = plan.durationMs > 0 ? Math.min(1, elapsedMs / plan.durationMs) : 1;
    if (progress >= 1) {
        // 末帧只由 _finishNekoIdleCat1PairMove 强制同步一次原生 bounds；
        // 若先在此处 apply(progress=1) 再 finish，会触发两次 force dispatch（绕过节流/去重）的重复同步。
        _finishNekoIdleCat1PairMove(button);
        return;
    }
    _applyNekoIdleCat1PairMovePlan(plan, progress);
    state.pairMoveFrame = window.requestAnimationFrame((nextTimestamp) => {
        _stepNekoIdleCat1PairMove(button, startedAt, nextTimestamp);
    });
}

function _startNekoIdleCat1PairMove(button) {
    const catMindRunOptions = arguments[1] || {};
    const isCatMindRun = catMindRunOptions.source === 'cat_mind';
    if (!isCatMindRun) return false;
    const state = _getNekoIdleCat1Journey(button);
    if (_isNekoIdleCat1EdgePeekActive(button)) {
        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
        return false;
    }
    if (!state || !_canScheduleNekoIdleCat1PairMove(button, state)) {
        return false;
    }
    const plan = _getNekoIdleCat1PairMovePlan(button);
    if (!plan) {
        return false;
    }
    state.pairMoveToken += 1;
    state.pairMoveTimer = 0;
    state.pairMovePlan = plan;
    const run = _beginNekoCatMindStateAction(state, _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE, _NEKO_IDLE_TIER_CAT1, {
        source: catMindRunOptions.source, requestId: catMindRunOptions.requestId
    });
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);
    state.facingRight = plan.dx > 0;
    if (plan.chatMode === 'solo' && _canNekoIdleCat1MoveSoloWithExpandedChat()) {
        _dispatchNekoIdleCat1MotionInputRegionState(state, true, 'cat1-pair-move-start', plan);
    }
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Classes(button, state);
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(art, state.profile.assets.walking(), state.profile.tier, { animate: false });
    }
    const startedAt = (typeof performance !== 'undefined' && typeof performance.now === 'function')
        ? performance.now()
        : Date.now();
    state.pairMoveFrame = window.requestAnimationFrame((timestamp) => {
        _stepNekoIdleCat1PairMove(button, startedAt, timestamp);
    });
    _notifyNekoCatMindRunnerStarted(catMindRunOptions, run);
    return true;
}

function _refreshNekoIdleCat1Observer(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || typeof MutationObserver !== 'function') return;

    if (!state.observer) {
        const shell = document.getElementById('react-chat-window-shell');
        if (shell) {
            state.observer = new MutationObserver(() => {
                _scheduleNekoIdleCat1JourneySync(button);
            });
            state.observer.observe(shell, {
                attributes: true,
                attributeFilter: ['class', 'style']
            });
        }
    }

    if (!state.containerObserver) {
        const container = _getNekoIdleReturnContainerFromButton(button);
        if (container) {
            state.containerObserver = new MutationObserver(() => {
                const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
                if (!currentState || currentState.paused) return;
                if (currentState.substate === currentState.profile.walkingSubstate) return;
                const observerDragging = container.getAttribute('data-dragging');
                if (observerDragging && observerDragging !== 'false') return;
                _scheduleNekoIdleCat1JourneySync(button);
            });
            state.containerObserver.observe(container, {
                attributes: true,
                attributeFilter: ['style', 'data-dragging']
            });
        }
    }
}

function _syncNekoIdleCat1Journey(button, tier) {
    if (!button) return;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
    if (_isNekoIdleCat1EdgePeekActive(button)) {
        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
        return;
    }
    if (_isNekoIdleCompactSurfaceDragging()) return;
    const initialContainer = _getNekoIdleReturnContainerFromButton(button);
    const initialDragging = initialContainer && initialContainer.getAttribute('data-dragging');
    if (initialDragging && initialDragging !== 'false') return;
    const normalizedTier = _normalizeNekoIdleReturnTier(tier || button.getAttribute('data-neko-idle-tier'));
    if (normalizedTier === _NEKO_IDLE_TIER_CAT1 && _isNekoIdleCat1IndependentActionActive(button)) return;
    const profile = _getNekoIdleReturnSubactionProfile(normalizedTier);
    const state = _getNekoIdleReturnSubactionState(button, profile);
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!profile || !state || !container || container.style.display === 'none') {
        _cancelNekoIdleCat1Journey(button);
        return;
    }

    _refreshNekoIdleCat1Observer(button);
    if (state.paused) return;
    if (state.pairMovePlan || state.pairMoveFrame) return;

    const chatRect = _getNekoIdleChatMinimizedRect();
    const rawCompactAnchorRatio = state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
        ? state.compactFollowAnchorRatio
        : null;
    const compactAnchorRatio = rawCompactAnchorRatio === null || rawCompactAnchorRatio === undefined
        ? NaN
        : Number(rawCompactAnchorRatio);
    const target = _getNekoIdleCat1Target(container, chatRect, {
        anchorRatio: Number.isFinite(compactAnchorRatio) ? compactAnchorRatio : null,
        compactTopEdgeBlocked: !!state.compactTopEdgeRearmRequired
    });
    if (!target) {
        state.targetKind = '';
        _cancelNekoIdleReturnPendingWalk(state);
        if (state.substate === profile.idleSubstate) {
            state.target = null;
            state.facingRight = false;
            state.actionSettled = true;
            _resetNekoIdleCat1WalkSpeed(state);
            _setNekoIdleCat1Classes(button, state);
            return;
        }
        _cancelNekoIdleCat1PairMove(state);
        if (state.substate !== profile.idleSubstate) {
            _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        }
        return;
    }

    const compactDropUntil = Number(state.compactTopEdgeDropUntil) || 0;
    if (target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE &&
        compactDropUntil &&
        _getNekoIdleNowMs() < compactDropUntil) {
        state.target = null;
        state.targetKind = '';
        state.facingRight = false;
        _cancelNekoIdleReturnPendingWalk(state);
        _cancelNekoIdleCat1PairMove(state);
        _setNekoIdleCat1Classes(button, state);
        return;
    }
    if (compactDropUntil) {
        state.compactTopEdgeDropUntil = 0;
    }

    const previousTargetKind = state.targetKind || '';
    state.target = target;
    state.targetKind = target.kind || '';
    const compactTopEdgeTarget = target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    const switchingFromCompactTopEdgeToMinimizedSide =
        previousTargetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE &&
        target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE;
    if (compactTopEdgeTarget) {
        _cancelNekoIdleCat1PairMove(state);
        if (target.distance <= profile.target.exitDistancePx) {
            _cancelNekoIdleReturnPendingWalk(state);
        }
    }

    if (target.distance < profile.target.enterDistancePx && state.substate !== profile.walkingSubstate && !compactTopEdgeTarget) {
        _cancelNekoIdleReturnPendingWalk(state);
    }

    if (state.substate === profile.walkingSubstate && target.distance > profile.target.exitDistancePx) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    if (target.distance >= profile.target.enterDistancePx ||
        (compactTopEdgeTarget && target.distance > profile.target.exitDistancePx) ||
        (switchingFromCompactTopEdgeToMinimizedSide && target.distance > profile.target.exitDistancePx)) {
        state.actionSettled = false;
        _cancelNekoIdleCat1PairMove(state);
        if (switchingFromCompactTopEdgeToMinimizedSide) {
            state.pendingWalkReady = true;
            state.pendingWalkDelayMs = 0;
        }
        _scheduleNekoIdleCat1WalkStart(button, target);
        return;
    }

    if (state.substate === profile.walkingSubstate) {
        _cancelNekoIdleReturnPendingWalk(state);
        if (compactTopEdgeTarget) {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
            _finishNekoIdleCat1CompactTopEdgeWalk(button);
        } else {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _finishNekoIdleCat1Walk(button);
        }
        return;
    }

    if (state.substate === profile.finishingSubstate) {
        state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
        _setNekoIdleCat1Classes(button, state);
        _scheduleNekoIdleReturnSubactionSettle(button);
        return;
    }

    if (state.substate === profile.idleSubstate && !state.actionSettled) {
        if (compactTopEdgeTarget && target.distance <= profile.target.exitDistancePx) {
            _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
        }
        state.target = null;
        state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
        state.actionSettled = true;
        _resetNekoIdleCat1WalkSpeed(state);
        _setNekoIdleCat1Classes(button, state);
    }

    if (state.substate === profile.idleSubstate && state.actionSettled) {
        if (compactTopEdgeTarget) {
            _cancelNekoIdleCat1PairMove(state);
            state.compactTopEdgeRearmRequired = false;
            if (target.distance <= profile.target.exitDistancePx) {
                _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
                const compactSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
                _rememberNekoIdleCat1CompactFollowAnchor(state, compactSurfaceRect, target);
                _rememberNekoIdleCat1CompactFollowSurface(state, compactSurfaceRect, _getNekoIdleNowMs());
                _setNekoIdleCat1Classes(button, state);
            }
            return;
        }
    }
}

function _scheduleNekoIdleCat1JourneySync(button) {
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
    if (_isNekoIdleCat1EdgePeekActive(button)) {
        _reclampNekoIdleCat1EdgePeekToViewport(button);
        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
        return;
    }
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.syncFrame) return;
    if (_isNekoIdleCompactSurfaceDragging() || _nekoIdleCompactSurfaceSettleTimer) return;
    state.syncFrame = window.requestAnimationFrame(() => {
        state.syncFrame = 0;
        _syncNekoIdleCat1Journey(button);
    });
}

function _pauseNekoIdleCat1Journey(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || (
        state.substate !== state.profile.walkingSubstate &&
        state.substate !== state.profile.finishingSubstate
    )) {
        return;
    }
    state.paused = true;
    _cancelNekoIdleCat1Frame(state);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _setNekoIdleCat1Classes(button, state);
}

function _resumeNekoIdleCat1Journey(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.paused) return;
    state.paused = false;
    state.lastStepAt = 0;
    _setNekoIdleCat1Classes(button, state);
    _syncNekoIdleCat1Journey(button);
    if (state.substate === state.profile.finishingSubstate) {
        _scheduleNekoIdleReturnSubactionSettle(button);
    }
}

function _setNekoIdleReturnArtSource(art, nextSrc, tier, options = {}) {
    if (!art || !nextSrc) return;

    if (!options.keepHoverPlayback) {
        _clearNekoIdleHoverPlayback(art);
    }
    art.setAttribute('data-neko-idle-tier', tier);

    const currentSrc = art.getAttribute('src') || '';
    const shouldAnimate = options.animate !== false
        && currentSrc
        && currentSrc !== nextSrc
        && !_shouldReduceNekoIdleMotion();

    if (!shouldAnimate) {
        _cleanupNekoIdleArtTransition(art);
        _clearNekoIdleGifPlaybackSource(art);
        art.removeAttribute(_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR);
        art.src = nextSrc;
        _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, nextSrc);
        return;
    }

    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, '');
    if (art.__nekoIdleTransitionTo === nextSrc) {
        return;
    }

    _cleanupNekoIdleArtTransition(art);

    const button = art.closest('.neko-idle-return-btn');
    if (!button) {
        art.src = nextSrc;
        return;
    }

    const nextArt = document.createElement('img');
    nextArt.className = 'neko-idle-return-art neko-idle-return-art-next';
    nextArt.src = nextSrc;
    nextArt.alt = art.alt || '';
    nextArt.draggable = false;
    nextArt.setAttribute('data-neko-idle-tier', tier);

    const finish = () => {
        _clearNekoIdleGifPlaybackSource(art);
        art.removeAttribute(_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR);
        art.src = nextSrc;
        _cleanupNekoIdleArtTransition(art);
        _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, nextSrc);
    };

    art.__nekoIdleTransitionNext = nextArt;
    art.__nekoIdleTransitionTo = nextSrc;
    button.appendChild(nextArt);
    void nextArt.offsetWidth;
    button.classList.add('is-tier-transitioning');
    art.__nekoIdleTransitionTimer = setTimeout(finish, _NEKO_IDLE_RETURN_TRANSITION_MS);
}

function _playNekoIdleHoverArt(art, tier) {
    if (!art || !tier || tier === _NEKO_IDLE_TIER_NONE) return;
    _cleanupNekoIdleArtTransition(art);

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const button = _getNekoIdleReturnButtonFromArt(art);
    if (_isNekoIdleReturnDragActionActive(button)) return;
    if (_isNekoIdleCat1IndependentActionActive(button)) return;
    const profile = _getNekoIdleReturnSubactionProfile(normalizedTier);
    const subactionState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (subactionState && subactionState.profile === profile) {
        _cancelNekoIdleCat1PairMove(subactionState);
    }
    const useSubactionInteractive = !!(profile
        && subactionState
        && subactionState.profile === profile
        && (subactionState.substate === profile.walkingSubstate ||
            subactionState.substate === profile.finishingSubstate));
    if (useSubactionInteractive) {
        _pauseNekoIdleCat1Journey(button);
    }
    const clickSrc = useSubactionInteractive
        ? profile.assets.interactive()
        : _getNekoIdleReturnClickAssetUrl(normalizedTier);
    const dispatchHoverObservation = () => {
        _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT_HOVER_REACTION, {
            source: 'return-ball-hover',
            tier: normalizedTier,
            reason: useSubactionInteractive ? 'subaction-interactive' : 'return-hover',
            substate: subactionState && subactionState.substate ? subactionState.substate : '',
            targetKind: subactionState && subactionState.targetKind ? subactionState.targetKind : ''
        });
    };
    if (art.__nekoIdleHoverSrc === clickSrc) {
        if (art.__nekoIdleHoverTimer) {
            clearTimeout(art.__nekoIdleHoverTimer);
            art.__nekoIdleHoverTimer = 0;
        }
        art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
        _clearNekoIdleGifPlaybackSource(art);
        if ((art.getAttribute('src') || '') !== clickSrc) {
            art.src = clickSrc;
        }
        _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, normalizedTier, clickSrc);
        dispatchHoverObservation();
        return;
    }

    _clearNekoIdleHoverPlayback(art);
    _clearNekoIdleGifPlaybackSource(art);
    art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
    art.__nekoIdleHoverSrc = clickSrc;
    art.__nekoIdleHoverTier = normalizedTier;
    art.__nekoIdleHoverStartedAt = Date.now();
    art.src = clickSrc;
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, normalizedTier, clickSrc);
    dispatchHoverObservation();
}

function _finishNekoIdleHoverArtAfterPlayback(art, tier) {
    if (!art || !tier || tier === _NEKO_IDLE_TIER_NONE) return;

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (_isNekoIdleReturnDragActionActive(_getNekoIdleReturnButtonFromArt(art))) return;
    if (_isNekoIdleCat1IndependentActionActive(_getNekoIdleReturnButtonFromArt(art))) return;
    const token = art.__nekoIdleHoverToken || 0;
    const startedAt = art.__nekoIdleHoverStartedAt || 0;
    const hoverSrc = art.__nekoIdleHoverSrc || _getNekoIdleReturnClickAssetUrl(normalizedTier);

    if (art.__nekoIdleHoverTimer) {
        clearTimeout(art.__nekoIdleHoverTimer);
        art.__nekoIdleHoverTimer = 0;
    }

    _getNekoIdleGifDurationMs(hoverSrc).then((durationMs) => {
        if ((art.__nekoIdleHoverToken || 0) !== token) return;
        if (art.__nekoIdleHoverTier !== normalizedTier) return;

        const elapsedMs = startedAt ? Math.max(0, Date.now() - startedAt) : durationMs;
        const delayMs = Math.max(0, durationMs - elapsedMs);
        art.__nekoIdleHoverTimer = setTimeout(() => {
            if ((art.__nekoIdleHoverToken || 0) !== token) return;
            if (art.__nekoIdleHoverTier !== normalizedTier) return;
            art.__nekoIdleHoverTimer = 0;
            art.__nekoIdleHoverSrc = '';
            art.__nekoIdleHoverTier = '';
            art.__nekoIdleHoverStartedAt = 0;
            _setNekoIdleReturnArtSource(
                art,
                _getNekoIdleReturnCurrentArtUrl(_getNekoIdleReturnButtonFromArt(art), normalizedTier),
                normalizedTier,
                { animate: false, keepHoverPlayback: true }
            );
            _clearNekoIdleHoverPlayback(art);
            _resumeNekoIdleCat1Journey(_getNekoIdleReturnButtonFromArt(art));
            _scheduleNekoIdleCat1JourneySync(_getNekoIdleReturnButtonFromArt(art));
        }, delayMs);
    });
}

function _applyNekoIdleReturnPresentation(button, tier) {
    if (!button) return;
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (_isNekoGoodbyeIdleBallButton(button)) {
        _setNekoGoodbyeIdleAppearanceForButton(button, _NEKO_GOODBYE_IDLE_APPEARANCE_BALL);
        button.setAttribute('data-neko-idle-tier', _NEKO_IDLE_TIER_NONE);
        _clearNekoIdleThoughtBubble(button);
        _clearNekoIdleCat1QuestionMark(button);
        _setNekoIdleCat1QuestionMarkKeyboardTarget(null);
        _stopNekoGoodbyeIdleBallCatSounds();
        const ballArt = button.querySelector('.neko-idle-return-art');
        if (ballArt && !ballArt.dataset.nekoGoodbyeIdleCatSrc && normalizedTier !== _NEKO_IDLE_TIER_NONE) {
            // 先于 app-ui 存下该 tier 的规范待机图快照：此刻 DOM src 可能是
            // hover/进食/玩耍的一次性 GIF，且下方 cancel 会抹掉瞬态标记，
            // app-ui 侧无法再分辨；带 tier 标签供恢复猫形态时校验
            ballArt.dataset.nekoGoodbyeIdleCatSrc = _getNekoIdleReturnAssetUrl(normalizedTier);
            ballArt.dataset.nekoGoodbyeIdleCatSrcTier = normalizedTier;
        }
        if (_isNekoIdleCat1PlaygroundEntryPending(button)) {
            _cancelNekoIdleCat1PlaygroundPendingEntry(button);
            _clearNekoIdleCat1PlaygroundQuestionBlockClone(button);
        }
        if (_isNekoIdleCat1PlaygroundDropActive(button)) {
            _releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'tier-change');
        }
        _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
        _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
        _cancelNekoIdleCat1Journey(button);
        const container = button.closest('[id$="-return-button-container"]');
        if (container) {
            container.setAttribute('data-neko-idle-tier', _NEKO_IDLE_TIER_NONE);
            _clearNekoIdleCat1EdgePeekForTierExit(container);
        }
        return;
    }
    _setNekoGoodbyeIdleAppearanceForButton(button, _NEKO_GOODBYE_IDLE_APPEARANCE_CAT);
    if (_isNekoIdleCat1PlaygroundEntryPending(button) && normalizedTier !== _NEKO_IDLE_TIER_CAT1) {
        _cancelNekoIdleCat1PlaygroundPendingEntry(button);
        _clearNekoIdleCat1PlaygroundQuestionBlockClone(button);
    }
    if (_isNekoIdleCat1PlaygroundDropActive(button)) {
        if (normalizedTier !== _NEKO_IDLE_TIER_CAT1) {
            _releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'tier-change');
        } else {
            return;
        }
    }
    if (button.__nekoIdleThoughtBubbleTier && button.__nekoIdleThoughtBubbleTier !== normalizedTier) {
        _clearNekoIdleThoughtBubble(button);
    }
    const dragState = button.__nekoIdleReturnDragActionState;
    const dragActive = _isNekoIdleReturnDragActionActive(button);
    _syncNekoIdleSleepSoundForTier(normalizedTier);
    _syncNekoIdleCat1AmbientSoundForTier(normalizedTier);
    if (normalizedTier !== _NEKO_IDLE_TIER_CAT1) {
        _clearNekoIdleCat1QuestionMark(button);
        _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
        _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
        _cancelNekoIdleCat1Journey(button);
    }
    if (dragActive && normalizedTier !== _NEKO_IDLE_TIER_CAT1) {
        const wasCat1Drag = dragState && dragState.tier === _NEKO_IDLE_TIER_CAT1;
        _clearNekoIdleCat1RapidDragReaction(button);
        if (wasCat1Drag) _fadeOutNekoIdleCat1DragSound();
    }
    button.setAttribute('data-neko-idle-tier', normalizedTier);

    const container = button.closest('[id$="-return-button-container"]');
    if (container) {
        container.setAttribute('data-neko-idle-tier', normalizedTier);
        if (normalizedTier !== _NEKO_IDLE_TIER_CAT1) {
            _clearNekoIdleCat1EdgePeekForTierExit(container);
        }
    }

    const art = button.querySelector('.neko-idle-return-art');
    const eatActionActive = _isNekoIdleCat1EatActionActive(button);
    const playActionActive = _isNekoIdleCat1PlayActionActive(button);
    if (art) {
        if (dragActive && normalizedTier !== _NEKO_IDLE_TIER_NONE) {
            _setNekoIdleReturnDragActionArt(button, normalizedTier);
        } else if (playActionActive && normalizedTier === _NEKO_IDLE_TIER_CAT1) {
            _setNekoIdleReturnArtSource(
                art,
                _NEKO_IDLE_CAT1_PLAY_ASSET_URL,
                normalizedTier,
                { animate: false }
            );
        } else if (eatActionActive && normalizedTier === _NEKO_IDLE_TIER_CAT1) {
            _setNekoIdleReturnArtSource(
                art,
                _NEKO_IDLE_CAT1_EAT_ASSET_URL,
                normalizedTier,
                { animate: false }
            );
        } else {
            if (normalizedTier === _NEKO_IDLE_TIER_NONE) {
                _finishNekoIdleReturnDragAction(button, { restoreArt: false });
            }
            _setNekoIdleReturnArtSource(art, _getNekoIdleReturnAssetUrl(normalizedTier), normalizedTier);
        }
    }
    if (normalizedTier === _NEKO_IDLE_TIER_CAT1 && !dragActive && !eatActionActive && !playActionActive) {
        _scheduleNekoIdleCat1JourneySync(button);
    }
}

function _readNekoAutoGoodbyeVisualTier() {
    try {
        if (window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.getState === 'function') {
            const currentState = window.nekoAutoGoodbye.getState();
            return _normalizeNekoIdleReturnTier(currentState && currentState.visualTier);
        }
    } catch (_) {}
    return _NEKO_IDLE_TIER_NONE;
}

function _syncAllNekoIdleReturnButtons(tier) {
    document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
        _applyNekoIdleReturnPresentation(button, tier);
    });
}

function _ensureNekoIdleReturnPresentationBridge() {
    if (window.__nekoIdleReturnPresentationBridgeBound) return;
    window.__nekoIdleReturnPresentationBridgeBound = true;

    window.addEventListener('neko:auto-goodbye:state-change', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (!detail || detail.type !== 'visual-tier') {
            return;
        }
        if (_getNekoGoodbyeIdleAppearance() === _NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
            _stopNekoGoodbyeIdleBallCatSounds();
            _syncAllNekoIdleReturnButtons(detail.tier);
            return;
        }
        _syncNekoIdleSleepSoundForTier(detail.tier);
        _syncNekoIdleCat1AmbientSoundForTier(detail.tier);
        _syncAllNekoIdleReturnButtons(detail.tier);
    });

    window.addEventListener('neko:goodbye-idle-appearance', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const appearance = _normalizeNekoGoodbyeIdleAppearance(detail && detail.mode);
        document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
            _setNekoGoodbyeIdleAppearanceForButton(button, appearance);
        });
        if (appearance === _NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
            _stopNekoGoodbyeIdleBallCatSounds();
        }
        _syncAllNekoIdleReturnButtons(_readNekoAutoGoodbyeVisualTier());
    });

    window.addEventListener('resize', () => {
        document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
            _scheduleNekoIdleCat1JourneySync(button);
        });
    });

    window.addEventListener('neko:compact-surface-layout-change', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        _handleNekoIdleCompactSurfaceMoveState(detail);
    });

    window.addEventListener('neko:return-ball-manual-move', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (!detail || !detail.container) return;
        const manualMoveButton = _getNekoIdleReturnButtonFromContainer(detail.container);
        if (_isNekoIdleCat1PlaygroundEntryOrDropActive(manualMoveButton)) return;
        if (detail.reason === 'return-ball-drag-end') {
            _finishNekoIdleReturnDragActionForContainer(detail.container);
            if (_isNekoIdleCat1EdgePeekActive(detail.container)) {
                _cancelNekoIdleCat1JourneyForContainer(detail.container, {
                    resetArt: false,
                    preserveObservers: true
                });
                return;
            }
            const compactTopEdgeRearmState = _updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove(detail.container);
            if (compactTopEdgeRearmState.shouldSync || _shouldRecheckNekoIdleCat1AfterManualMove(detail)) {
                _scheduleNekoIdleCat1JourneySyncForContainer(detail.container);
            }
            return;
        }
        if (detail.reason === 'return-ball-drag-cancel') {
            _finishNekoIdleReturnDragActionForContainer(detail.container, { restoreArt: false });
            return;
        }
        if (detail.reason === 'return-ball-drag-start') {
            _prepareNekoIdleReturnDragActionForContainer(detail.container);
            return;
        }
        if (detail.reason === 'return-ball-drag-active') {
            _startNekoIdleReturnDragActionForContainer(detail.container);
            return;
        }
        if (detail.reason === 'return-ball-drag-motion') {
            _handleNekoIdleCat1RapidDragMotionForContainer(detail.container, detail);
            return;
        }
        _cancelNekoIdleCat1JourneyForContainer(detail.container);
    });

    window.addEventListener('neko:idle-chat-minimized-state', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const receivedAt = Date.now();
        const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);
        if (_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopChatMinimizedState)) return;
        const screenRect = detail && detail.minimized
            ? _normalizeNekoIdleScreenRect(detail.screenRect)
            : null;
        const nextMinimized = !!(detail && detail.minimized && screenRect);
        if (_isAnyNekoIdleCat1PlaygroundDropLifecycleActive() &&
            _isNekoIdleCat1PlaygroundPairMoveFeedback(detail)) {
            return;
        }
        const compactSurfaceCurrentlyVisible = !!_getNekoIdleDesktopCompactSurfaceRect();
        if (nextMinimized &&
            _nekoIdleDesktopCompactSurfaceState &&
            _nekoIdleDesktopCompactSurfaceState.visible &&
            _isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopCompactSurfaceState)) {
            return;
        }
        const previousState = _nekoIdleDesktopChatMinimizedState;
        const previousScreenRect = previousState && previousState.minimized
            ? previousState.screenRect
            : null;
        const desktopChatMoveDistance = _getNekoIdleRectCenterMoveDistance(previousScreenRect, screenRect);
        const isSmallDesktopChatMove = !!(previousScreenRect && screenRect) &&
            desktopChatMoveDistance < _NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX;
        _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
            nextMinimized,
            screenRect,
            receivedAt,
            sourceUpdatedAt,
            !!(detail && !detail.minimized && !compactSurfaceCurrentlyVisible)
        );
        if (nextMinimized) {
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                false,
                null,
                receivedAt,
                sourceUpdatedAt
            );
        }
        const pairMoveFeedback = _isNekoIdleCat1PlaygroundPairMoveFeedback(detail);
        document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
            const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
            if (currentState && (currentState.pairMovePlan || currentState.pairMoveFrame)) {
                if (pairMoveFeedback) return;
                const interrupted = _interruptNekoIdleCat1PairMoveForRetarget(button, currentState);
                if (isSmallDesktopChatMove && !interrupted && !_isNekoIdleCat1Walking(button)) return;
                _scheduleNekoIdleCat1JourneySync(button);
                return;
            }
            const settledMinimizedSide = _isNekoIdleCat1SettledOnMinimizedSide(
                currentState,
                currentState && currentState.profile
            );
            if (isSmallDesktopChatMove && !_isNekoIdleCat1Walking(button) && !settledMinimizedSide) return;
            _scheduleNekoIdleCat1JourneySync(button);
        });
    });

    window.addEventListener('neko:idle-chat-compact-surface-state', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const receivedAt = Date.now();
        const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);
        if (_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopCompactSurfaceState)) return;
        const screenRect = detail && detail.visible
            ? _normalizeNekoIdleScreenRect(detail.screenRect)
            : null;
        const heartbeat = !!(detail && detail.heartbeat);
        const nextVisible = !!(detail && detail.visible && screenRect);
        if (nextVisible &&
            _nekoIdleDesktopChatMinimizedState &&
            _nekoIdleDesktopChatMinimizedState.minimized &&
            _isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopChatMinimizedState)) {
            return;
        }
        // heartbeat 只用于维持 compact-top-edge 贴附位置同步，不得改变可见性状态：
        // - 禁止通过 heartbeat 覆写 minimized state（聊天框最小化后心跳仍广播可见态，
        //   清掉 minimized 会导致 CAT1 在最小化后 1s 内失去毛线球步行目标）。
        // - 但 compact surface 可见时，心跳必须刷新缓存时间戳，防止 _NEKO_IDLE_DESKTOP_
        //   COMPACT_SURFACE_RECT_STALE_MS (10s) 过期后 _getNekoIdleDesktopCompactSurfaceRect
        //   返回 null，导致 CAT1 失去 compact-top-edge 目标。
        // - 心跳用 receivedAt 刷新 updatedAt 防过期，但保留原 sourceUpdatedAt 避免扰乱
        //   跨状态时间戳排序（心跳自身的新鲜时间戳会让 isStaleAgainst 永远判不出旧）。
        if (!heartbeat) {
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                nextVisible,
                screenRect,
                receivedAt,
                sourceUpdatedAt
            );
            if (nextVisible) {
                _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
                    false,
                    null,
                    receivedAt,
                    sourceUpdatedAt,
                    false
                );
            }
        } else if (nextVisible &&
            _nekoIdleDesktopCompactSurfaceState &&
            _nekoIdleDesktopCompactSurfaceState.visible) {
            // 最小化时 compact state 已被 minimized listener 清为 visible:false，
            // 此处不会进入——心跳不会把 minimized 态的「不可见 compact」刷回可见。
            var prevCompactSourceUpdatedAt = _nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt;
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                true,
                screenRect || _nekoIdleDesktopCompactSurfaceState.screenRect,
                receivedAt,
                prevCompactSourceUpdatedAt
            );
        } else if (nextVisible &&
            _nekoIdleDesktopChatMinimizedState &&
            !_nekoIdleDesktopChatMinimizedState.minimized) {
            // 还原后来的心跳 catch-up：Electron setMinimized(false) 早退不发布
            // compact-surface-state，compact 缓存仍为 minimize 时写下的
            // visible:false。心跳说 visible + minimized 已 false → 信任心跳
            // 恢复 compact 可用性，保留原 sourceUpdatedAt 不乱排序。
            var prevCompactSourceUpdatedAt = _nekoIdleDesktopCompactSurfaceState
                ? _nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt
                : sourceUpdatedAt;
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                true,
                screenRect,
                receivedAt,
                prevCompactSourceUpdatedAt
            );
        }
        _handleNekoIdleCompactSurfaceMoveState(detail);
    });

    const currentTier = _readNekoAutoGoodbyeVisualTier();
    if (_getNekoGoodbyeIdleAppearance() === _NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
        _stopNekoGoodbyeIdleBallCatSounds();
        _syncAllNekoIdleReturnButtons(currentTier);
        return;
    }
    _syncNekoIdleSleepSoundForTier(currentTier);
    _syncNekoIdleCat1AmbientSoundForTier(currentTier);
}

_ensureNekoIdleReturnPresentationBridge();
