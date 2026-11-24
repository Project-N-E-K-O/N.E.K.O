function _isNekoIdleCat1Walking(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    return !!(state &&
        state.profile &&
        state.substate === state.profile.walkingSubstate &&
        !state.pairMovePlan &&
        !state.pairMoveFrame);
}

function _pauseNekoIdleCat1JourneyForGravity(button) {
    if (window.NekoDesktopWindowGravity?.canWalk?.(button) !== false) return false;
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const moving = state && (state.substate === state.profile?.walkingSubstate
        || state.pairMovePlan || state.pairMoveFrame);
    if (moving || (state && (state.pendingWalkTimer || state.pendingWalkReady))) {
        _cancelNekoIdleCat1Journey(button, {
            resetArt: !!moving, preserveObservers: true, reason: 'window-gravity-moving',
        });
    }
    return true;
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
    // Electron 多窗口中 Pet 页里的 React Chat 只是隐藏兼容 DOM；真实毛球位于 Chat 窗口。
    if (window.__NEKO_MULTI_WINDOW__ === true) return null;
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
    if (window.__NEKO_MULTI_WINDOW__ === true) return null;
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
    if (window.__NEKO_MULTI_WINDOW__ === true) return null;
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
        if (rect) return _getNekoDesktopVirtualRect(rect);
    }
    return null;
}

function _getNekoIdleDesktopCompactSurfaceRect() {
    const state = _nekoIdleDesktopCompactSurfaceState;
    if (!state || !state.visible || !state.screenRect) return null;
    if (_nekoIdleDesktopChatMinimizedState &&
        _nekoIdleDesktopChatMinimizedState.minimized &&
        _isNekoIdleDesktopStateNewerThan(
            _nekoIdleDesktopChatMinimizedState.sourceUpdatedAt,
            _nekoIdleDesktopChatMinimizedState.lifecycleSequence,
            state
        )) {
        return null;
    }
    if (Date.now() - (state.updatedAt || 0) > _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS) return null;
    const screenRect = _normalizeNekoIdleScreenRect(state.screenRect);
    if (!screenRect) return null;
    const virtualOrigin = _getNekoDesktopVirtualViewportOrigin();
    const screenLeft = virtualOrigin.x;
    const screenTop = virtualOrigin.y;
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
        _isNekoIdleDesktopStateNewerThan(
            _nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt,
            _nekoIdleDesktopCompactSurfaceState.lifecycleSequence,
            state
        )) {
        return null;
    }
    if (Date.now() - (state.updatedAt || 0) > _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS) return null;
    const screenRect = _normalizeNekoIdleScreenRect(state.screenRect);
    if (!screenRect) return null;
    const virtualOrigin = _getNekoDesktopVirtualViewportOrigin();
    const screenLeft = virtualOrigin.x;
    const screenTop = virtualOrigin.y;
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
    return !!(
        _getNekoIdleReactChatExpandedShell() ||
        _getNekoIdleDesktopCompactSurfaceRect() ||
        _isNekoIdleDesktopChatExpandedRecent()
    );
}

function _getNekoIdleChatMinimizedRect() {
    const reactRect = _getNekoIdleReactChatMinimizedRect();
    return (reactRect ? _getNekoDesktopVirtualRect(reactRect) : null)
        || _getNekoIdleDesktopChatMinimizedRect();
}

function _clampNekoIdleCat1Position(left, top, width, height) {
    const virtualViewport = _getNekoDesktopVirtualViewportSize();
    return {
        left: Math.round(Math.max(0, Math.min(left, Math.max(0, virtualViewport.width - width)))),
        top: Math.round(Math.max(0, Math.min(top, Math.max(0, virtualViewport.height - height))))
    };
}

function _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect) {
    // The yarn ball's right side has trailing string space, so right-to-left approaches need an inward visual anchor.
    if (facingRight) return 0;
    const width = Number(chatRect && chatRect.width);
    if (!Number.isFinite(width) || width <= 0) return 0;
    const configuredOffset = _usesNekoIdleCat1NativeYarnVisualAnchor(chatRect)
        ? width * (_NEKO_IDLE_CAT1_NATIVE_YARN_ASSET_SIZE_PX -
            _NEKO_IDLE_CAT1_NATIVE_YARN_BODY_RIGHT_PX) /
            _NEKO_IDLE_CAT1_NATIVE_YARN_ASSET_SIZE_PX
        : _NEKO_IDLE_CAT1_MINIMIZED_RIGHT_TO_LEFT_APPROACH_PX;
    return Math.max(0, Math.min(width, configuredOffset));
}

function _usesNekoIdleCat1NativeYarnVisualAnchor(chatRect) {
    const width = Number(chatRect && chatRect.width);
    return _isNekoIdleCat1NativeWaylandSelfBallRuntime() &&
        Number.isFinite(width) && width > 0 && width <= 60;
}

function _getNekoIdleCat1NativeYarnSide(container, chatRect) {
    if (!_usesNekoIdleCat1NativeYarnVisualAnchor(chatRect) ||
        !container || typeof container.getBoundingClientRect !== 'function') {
        return '';
    }
    const catRect = _getNekoDesktopVirtualElementRect(container);
    const catLeft = Number(catRect && catRect.left);
    const catWidth = Number(catRect && catRect.width);
    const yarnLeft = Number(chatRect.left);
    const yarnWidth = Number(chatRect.width);
    if (!Number.isFinite(catLeft) || !Number.isFinite(catWidth) || catWidth <= 0 ||
        !Number.isFinite(yarnLeft) || !Number.isFinite(yarnWidth) || yarnWidth <= 0) {
        return '';
    }
    const catCenterX = catLeft + catWidth / 2;
    const yarnCenterX = yarnLeft + yarnWidth / 2;
    return catCenterX <= yarnCenterX
        ? _NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_LEFT
        : _NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_RIGHT;
}

function _getNekoIdleCat1NativeYarnVisualTargetLeft(rect, chatRect, facingRight) {
    if (!_usesNekoIdleCat1NativeYarnVisualAnchor(chatRect)) return NaN;
    const yarnContactRatio = facingRight
        ? _NEKO_IDLE_CAT1_NATIVE_YARN_BODY_LEFT_PX / _NEKO_IDLE_CAT1_NATIVE_YARN_ASSET_SIZE_PX
        : _NEKO_IDLE_CAT1_NATIVE_YARN_BODY_RIGHT_PX / _NEKO_IDLE_CAT1_NATIVE_YARN_ASSET_SIZE_PX;
    const catContactRatio = facingRight
        ? _NEKO_IDLE_CAT1_IDLE_VISIBLE_RIGHT_PX / _NEKO_IDLE_CAT1_ASSET_SIZE_PX
        : _NEKO_IDLE_CAT1_IDLE_VISIBLE_LEFT_PX / _NEKO_IDLE_CAT1_ASSET_SIZE_PX;
    const leftSideCorrection = facingRight
        ? _NEKO_IDLE_CAT1_NATIVE_YARN_LEFT_SIDE_CONTACT_CORRECTION_PX
        : 0;
    return chatRect.left + chatRect.width * yarnContactRatio -
        rect.width * catContactRatio + leftSideCorrection;
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
    const nativeVisualTargetLeft = _getNekoIdleCat1NativeYarnVisualTargetLeft(
        rect,
        chatRect,
        lookFacingRight
    );
    let rawLeft;
    if (Number.isFinite(nativeVisualTargetLeft)) {
        rawLeft = nativeVisualTargetLeft;
    } else if (lookFacingRight) {
        rawLeft = chatRect.left - rect.width - profile.target.gapPx;
    } else {
        rawLeft = chatRect.right + profile.target.gapPx - approachOffsetPx;
    }
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

function _getNekoIdleRectCenterDistancePx(firstRect, secondRect) {
    if (!firstRect || !secondRect) return NaN;
    const dx = Number(secondRect.left) + Number(secondRect.width) / 2 -
        (Number(firstRect.left) + Number(firstRect.width) / 2);
    const dy = Number(secondRect.top) + Number(secondRect.height) / 2 -
        (Number(firstRect.top) + Number(firstRect.height) / 2);
    return Number.isFinite(dx) && Number.isFinite(dy) ? Math.hypot(dx, dy) : NaN;
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
    const rect = _getNekoDesktopVirtualElementRect(container);
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
        !_usesNekoIdleCat1NativeYarnVisualAnchor(chatRect) &&
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
    const rect = _getNekoDesktopVirtualElementRect(container);
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
        return window.NekoDesktopWindowGravity?.constrainTarget(container, minimizedSideTarget) || minimizedSideTarget;
    }

    const compactSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const compactTarget = _getNekoIdleCat1CompactTopEdgeTarget(container, compactSurfaceRect, {
        anchorRatio: options.anchorRatio
    });
    const compactBlocked = !!(options && options.compactTopEdgeBlocked);
    if (!compactBlocked &&
        compactTarget &&
        compactTarget.distance <= _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_FOLLOW_DISTANCE_PX) {
        return window.NekoDesktopWindowGravity?.constrainTarget(container, compactTarget) || compactTarget;
    }
    return null;
}

function _setNekoIdleCat1ContainerPosition(container, left, top) {
    if (!container) return false;
    const playgroundResult = _applyNekoIdleCat1PlaygroundMovePosition(container, left, top);
    if (playgroundResult !== null) return playgroundResult;
    const gravity = window.NekoDesktopWindowGravity;
    if (gravity?.applyPosition(container, left, top)) return true;
    const button = _getNekoIdleReturnButtonFromContainer(container);
    if (button && gravity?.isActive(button)) return false;
    container.style.left = `${Math.round(left)}px`;
    container.style.top = `${Math.round(top)}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
    return true;
}

let _nekoIdleCat1WalkActivitySequence = 0;

function _beginNekoIdleCat1WalkActivity(state, rect) {
    if (!state || !rect) return;
    const left = Number(rect.left);
    const top = Number(rect.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return;
    const startedAt = Date.now();
    _nekoIdleCat1WalkActivitySequence += 1;
    state.walkActivity = {
        activityId: `cat1-walk:${startedAt}:${_nekoIdleCat1WalkActivitySequence}`,
        startedAt: startedAt,
        startX: left,
        startY: top,
        lastX: left,
        lastY: top,
        pathDistancePx: 0
    };
}

function _appendNekoIdleCat1WalkActivityPoint(state, left, top) {
    const activity = state && state.walkActivity;
    const nextX = Number(left);
    const nextY = Number(top);
    if (!activity || !Number.isFinite(nextX) || !Number.isFinite(nextY)) return;
    activity.pathDistancePx += Math.hypot(nextX - activity.lastX, nextY - activity.lastY);
    activity.lastX = nextX;
    activity.lastY = nextY;
}

function _rebaseNekoIdleCat1WalkActivity(state, left, top) {
    const activity = state && state.walkActivity;
    const nextX = Number(left);
    const nextY = Number(top);
    if (!activity || !Number.isFinite(nextX) || !Number.isFinite(nextY)) return;
    activity.lastX = nextX;
    activity.lastY = nextY;
}

function _tryStartNekoIdleCat1DoorWalk(button, target, continuation) {
    if (!target || target.kind !== _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE) return false;
    const doorWalk = typeof window !== 'undefined' ? window.NekoDesktopWindowDoorWalk : null;
    return !!(doorWalk
        && typeof doorWalk.tryStartWalk === 'function'
        && doorWalk.tryStartWalk(button, target, continuation));
}

function _getNekoIdleCat1JourneyDoorContinuation(button, state) {
    const ownedTarget = state && state.target;
    const ownedSurface = _getNekoIdleChatMinimizedRect();
    function ownsWalk() {
        const current = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
        return !!(current === state && state.target === ownedTarget
            && state.profile && !state.paused
            && state.substate === state.profile.walkingSubstate);
    }
    function sameSurface(left, right) {
        return !!(left && right
            && left.left === right.left && left.top === right.top
            && left.width === right.width && left.height === right.height);
    }
    return {
        isCurrent() {
            if (!ownsWalk()) return false;
            return sameSurface(ownedSurface, _getNekoIdleChatMinimizedRect());
        },
        canResume() {
            return ownsWalk();
        },
        recordPosition(left, top) {
            _appendNekoIdleCat1WalkActivityPoint(state, left, top);
        },
        rebasePosition(left, top) {
            _rebaseNekoIdleCat1WalkActivity(state, left, top);
        },
        resume() {
            if (ownsWalk()) _scheduleNekoIdleCat1JourneySync(button);
        }
    };
}

function _completeNekoIdleCat1WalkActivity(state) {
    const activity = state && state.walkActivity;
    if (state) state.walkActivity = null;
    if (!activity) return null;
    const heldMs = activity.pauseStartedAt == null ? 0 : Math.max(0, _getNekoIdleNowMs() - activity.pauseStartedAt);
    return {
        activityId: activity.activityId,
        pathDistancePx: Math.max(0, activity.pathDistancePx),
        displacementPx: Math.hypot(activity.lastX - activity.startX, activity.lastY - activity.startY),
        durationMs: Math.max(0, Date.now() - activity.startedAt - (activity.pausedDurationMs || 0) - heldMs)
    };
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
        const localRect = _getNekoIdleReactChatMinimizedRect();
        const rect = _getNekoDesktopVirtualRect(localRect);
        if (rect && rect.width > 0 && rect.height > 0) {
            return {
                mode: 'dom',
                shell: shell,
                rect: rect,
                localRect: localRect
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
    const virtualViewport = _getNekoDesktopVirtualViewportSize();
    const minDx = chatRect ? Math.max(-catRect.left, -chatRect.left) : -catRect.left;
    const maxDx = chatRect
        ? Math.min(virtualViewport.width - catRect.right, virtualViewport.width - chatRect.right)
        : virtualViewport.width - catRect.right;
    const minDy = chatRect ? Math.max(-catRect.top, -chatRect.top) : -catRect.top;
    const maxDy = chatRect
        ? Math.min(virtualViewport.height - catRect.bottom, virtualViewport.height - chatRect.bottom)
        : virtualViewport.height - catRect.bottom;
    const dx = Math.max(minDx, Math.min(desiredDx, maxDx));
    const dy = Math.max(minDy, Math.min(desiredDy, maxDy));
    return {
        dx: dx,
        dy: dy,
        distance: Math.hypot(dx, dy)
    };
}

function _pickNekoIdleCat1MoveVector(catRect, chatRect, distance, minUsableDistance, playgroundButton, paired = false) {
    const attempts = 10;
    const fallbackAngles = [0, Math.PI, Math.PI / 2, -Math.PI / 2, Math.PI / 4, -Math.PI / 4, Math.PI * 3 / 4, -Math.PI * 3 / 4];
    for (let i = 0; i < attempts + fallbackAngles.length; i += 1) {
        const angle = i < attempts ? Math.random() * Math.PI * 2 : fallbackAngles[i - attempts];
        const desiredDx = Math.cos(angle) * distance;
        const desiredDy = Math.sin(angle) * distance;
        const target = playgroundButton &&
            (paired ? _getNekoIdleCat1PlaygroundPairMoveTarget : _getNekoIdleCat1PlaygroundMoveTarget)(playgroundButton, {
            left: catRect.left + desiredDx, top: catRect.top + desiredDy
        });
        const vector = playgroundButton
            ? target && { dx: target.left - catRect.left, dy: target.top - catRect.top, distance: target.distance }
            : _clampNekoIdleCat1MoveVector(catRect, chatRect, desiredDx, desiredDy);
        if (!vector) continue;
        if (vector.distance >= minUsableDistance) return vector;
    }
    return null;
}

function _hasNekoIdleCat1PlaygroundMoveSpace(button, distance, minUsableDistance) {
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    const body = playground && playground.active && !playground.released && playground.bodies.get('cat');
    if (!body) return false;
    const yarnTarget = _getNekoIdleCat1PlaygroundYarnTarget(button);
    const paired = yarnTarget && yarnTarget.distance <= _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW.target.exitDistancePx;
    return [-1, 1].some((direction) => {
        const target = (paired ? _getNekoIdleCat1PlaygroundPairMoveTarget : _getNekoIdleCat1PlaygroundMoveTarget)(button, {
            left: body.x + direction * distance, top: body.y
        });
        return !!(target && target.distance >= minUsableDistance);
    });
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
    const playgroundState = button && button.__nekoIdleCat1PlaygroundDropState;
    const playground = playgroundState && playgroundState.active && !playgroundState.released
        ? playgroundState : null;
    const body = playground && playground.bodies.get('cat');
    if (playground && (!body || _isNekoIdleCat1PlaygroundEntryOrDropActive(button, 'small-move'))) return null;
    const chatTarget = playground ? null : _getNekoIdleCat1PairMoveChatTarget();
    if (chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()) return null;
    const canMoveSolo = playground ? true : chatTarget ? false : _canNekoIdleCat1MoveSoloWithExpandedChat();
    if (!container || (!chatTarget && !canMoveSolo)) return null;
    if (container.getAttribute('data-dragging') === 'true') return null;
    if (_isNekoIdleReturnDragActionActive(button)) return null;
    const catRect = playground
        ? { left: body.x, top: body.y, width: body.width, height: body.height }
        : _getNekoDesktopVirtualElementRect(container);
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
    const yarnTarget = playground && _getNekoIdleCat1PlaygroundYarnTarget(button);
    const playgroundYarn = yarnTarget && yarnTarget.distance <= profile.target.exitDistancePx ? yarnTarget.yarnBody : null;
    const moveVector = _pickNekoIdleCat1MoveVector(
        catRect, chatTarget ? chatRect : null, desiredDistance, minUsableDistance,
        playground ? button : null, !!playgroundYarn
    );
    if (!moveVector) return null;
    const speed = Math.max(1, Number(config.speedPxPerSec) || _NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC);
    const minDuration = Math.max(1, Number(config.minDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DURATION_MS);
    const maxDuration = Math.max(minDuration, Number(config.maxDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DURATION_MS);
    const durationMs = Math.max(minDuration, Math.min(maxDuration, Math.round(moveVector.distance / speed * 1000)));
    const plan = {
        chatMode: chatTarget ? chatTarget.mode : 'solo',
        shell: chatTarget ? chatTarget.shell : null,
        container: container,
        catStartLeft: catRect.left,
        catStartTop: catRect.top,
        chatStartLeft: chatTarget && chatTarget.localRect ? chatTarget.localRect.left : (chatRect ? chatRect.left : null),
        chatStartTop: chatTarget && chatTarget.localRect ? chatTarget.localRect.top : (chatRect ? chatRect.top : null),
        chatStartScreenLeft: chatTarget && chatTarget.screenRect ? chatTarget.screenRect.left : null,
        chatStartScreenTop: chatTarget && chatTarget.screenRect ? chatTarget.screenRect.top : null,
        chatWidth: chatRect ? chatRect.width : null,
        chatHeight: chatRect ? chatRect.height : null,
        dx: moveVector.dx,
        dy: moveVector.dy,
        durationMs: durationMs
    };
    if (playground) {
        Object.assign(plan, {
            button,
            playgroundState: playground,
            playgroundToken: playground.token,
            playgroundBody: body,
            entryTimer: 0,
            entryPlaybackToken: 0,
            sourceLoadCleanup: null,
            pauseStartedAt: null,
            pausedDurationMs: 0,
            playbackStarted: false,
            committingPosition: false
        });
        if (playgroundYarn) {
            plan.chatMode = 'playground';
            plan.playgroundYarnBody = playgroundYarn;
            plan.yarnStartLeft = playgroundYarn.x;
            plan.yarnStartTop = playgroundYarn.y;
        }
    }
    return plan;
}

function _easeNekoIdleCat1PairMove(progress) {
    const p = Math.max(0, Math.min(1, Number(progress) || 0));
    return p < 0.5
        ? 2 * p * p
        : 1 - Math.pow(-2 * p + 2, 2) / 2;
}

function _isNekoIdleCat1PlaygroundMovePlanCurrent(button, plan) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    return !!(plan && plan.playgroundState && state && state.pairMovePlan === plan &&
        state.pairMoveToken === plan.token && playground === plan.playgroundState &&
        playground.token === plan.playgroundToken && playground.active && !playground.released &&
        playground.bodies.get('cat') === plan.playgroundBody &&
        (!plan.playgroundYarnBody || playground.bodies.get(plan.playgroundYarnBody.id) === plan.playgroundYarnBody));
}

function _setNekoIdleCat1PlaygroundMoveFacing(button, state, facingRight) {
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    button.classList.toggle(profile.classNames.facingRight, !!facingRight);
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (container) container.setAttribute(profile.dataAttributes.facing, facingRight ? 'right' : 'left');
}

function _syncNekoIdleCat1PlaygroundMovementPause(activity, held, timestamp = _getNekoIdleNowMs()) {
    if (!activity) return;
    if (held) {
        if (activity.pauseStartedAt == null) activity.pauseStartedAt = timestamp;
    } else if (activity.pauseStartedAt != null) {
        activity.pausedDurationMs = (activity.pausedDurationMs || 0) + Math.max(0, timestamp - activity.pauseStartedAt);
        activity.pauseStartedAt = null;
    }
}

function _syncNekoIdleCat1PlaygroundPointerMovementPause(button) {
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!playground || !state) return;
    const timestamp = _getNekoIdleNowMs();
    const plan = state.pairMovePlan;
    if (plan && plan.playgroundState === playground && plan.playgroundToken === playground.token) {
        _syncNekoIdleCat1PlaygroundMovementPause(plan, playground.pointerBodyId === 'cat' ||
            !!(plan.playgroundYarnBody && playground.pointerBodyId === plan.playgroundYarnBody.id), timestamp);
    }
    const walk = state.playgroundJourney;
    if (walk && walk.playgroundState === playground && walk.playgroundToken === playground.token) {
        _syncNekoIdleCat1PlaygroundMovementPause(state.walkActivity, state.paused ||
            playground.pointerBodyId === 'cat' || playground.pointerBodyId === walk.yarnBody.id, timestamp);
    }
}

function _syncNekoIdleCat1PlaygroundSmallMove(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const plan = state && state.pairMovePlan;
    if (!plan || !plan.playgroundState) return false;
    const playground = plan.playgroundState;
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (!_isNekoIdleCat1PlaygroundMovePlanCurrent(button, plan) || state.paused || tier !== _NEKO_IDLE_TIER_CAT1 ||
        _isNekoIdleReturnDragActionActive(button) ||
        _isNekoIdleCat1PlaygroundEntryOrDropActive(button, 'small-move', {
            allowPending: true, allowOwnedMovement: true
        }) || (playground.pointerBodyId !== 'cat' &&
            window.NekoDesktopWindowGravity?.canWalk?.(button) === false)) {
        const reason = playground.released ? playground.releaseReason || 'playground-exit'
            : tier !== _NEKO_IDLE_TIER_CAT1 ? 'tier-change'
            : playground.draggingBodyId === 'cat' || _isNekoIdleReturnDragActionActive(button)
                ? 'return-ball-drag-active'
                : _isNekoIdleReturnPending(button) ? 'return-pending' : 'playground-small-move-unavailable';
        _cancelNekoIdleCat1PairMove(state, { reason });
        return false;
    }
    if (plan.playbackStarted && !plan.committingPosition && Math.hypot(
        plan.playgroundBody.x - plan.catStartLeft - plan.activityLastOffsetX,
        plan.playgroundBody.y - plan.catStartTop - plan.activityLastOffsetY
    ) > 0.5) {
        // A window or collision moved the body; retain only committed walk facts.
        _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-external-motion' });
        return false;
    }
    if (plan.playgroundYarnBody) {
        const yarn = plan.playgroundYarnBody;
        const held = playground.pointerBodyId === 'cat' || playground.pointerBodyId === yarn.id;
        const target = held
            ? _getNekoIdleCat1PlaygroundYarnContext(button, { allowPending: true, allowOwnedMovement: true })
            : _getNekoIdleCat1PlaygroundPairMoveTarget(button, {
                left: plan.playgroundBody.x, top: plan.playgroundBody.y
            }, { allowOwnedMovement: true });
        if (!target || target.yarnBody !== yarn || (plan.playbackStarted && !plan.committingPosition && Math.hypot(
            yarn.x - plan.yarnStartLeft - plan.activityLastOffsetX,
            yarn.y - plan.yarnStartTop - plan.activityLastOffsetY
        ) > 0.5)) {
            _cancelNekoIdleCat1PairMove(state, { reason: 'playground-pair-move-yarn-unavailable' });
            return false;
        }
    }
    _syncNekoIdleCat1PlaygroundMovementPause(plan, playground.pointerBodyId === 'cat' ||
        !!(plan.playgroundYarnBody && playground.pointerBodyId === plan.playgroundYarnBody.id));
    return true;
}

function _continueNekoIdleCat1PlaygroundSmallMove(button, state, plan) {
    if (!state || state.pairMovePlan !== plan) return false;
    const currentState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (currentState !== state) {
        _cancelNekoIdleCat1PairMove(state, { reason: 'tier-change', restoreArt: false });
        return false;
    }
    return _syncNekoIdleCat1PlaygroundSmallMove(button);
}

function _appendNekoIdleCat1PlaygroundMoveActivityPoint(plan) {
    const body = plan && plan.playgroundBody;
    if (!body) return;
    const offsetX = body.x - plan.catStartLeft;
    const offsetY = body.y - plan.catStartTop;
    plan.activityPathDistancePx = Math.max(0, Number(plan.activityPathDistancePx) || 0) + Math.hypot(
        offsetX - (Number(plan.activityLastOffsetX) || 0),
        offsetY - (Number(plan.activityLastOffsetY) || 0)
    );
    plan.activityLastOffsetX = offsetX;
    plan.activityLastOffsetY = offsetY;
}

function _applyNekoIdleCat1PairMovePlan(plan, progress) {
    if (!plan || !plan.container) return;
    const eased = _easeNekoIdleCat1PairMove(progress);
    const offsetX = plan.dx * eased;
    const offsetY = plan.dy * eased;
    if (plan.playgroundState) {
        if (!_isNekoIdleCat1PlaygroundMovePlanCurrent(plan.button, plan)) return false;
        plan.committingPosition = true;
        const applied = plan.playgroundYarnBody
            ? _applyNekoIdleCat1PlaygroundPairMovePosition(plan.button, plan,
                plan.catStartLeft + offsetX, plan.catStartTop + offsetY)
            : _setNekoIdleCat1ContainerPosition(plan.container,
                plan.catStartLeft + offsetX, plan.catStartTop + offsetY);
        plan.committingPosition = false;
        if (applied !== true) return false;
        _appendNekoIdleCat1PlaygroundMoveActivityPoint(plan);
        return Math.hypot(
            plan.playgroundBody.x - (plan.catStartLeft + offsetX),
            plan.playgroundBody.y - (plan.catStartTop + offsetY)
        ) <= 0.5;
    }
    if (Number.isFinite(Number(plan.activityLastOffsetX)) &&
        Number.isFinite(Number(plan.activityLastOffsetY))) {
        const previousOffsetX = Number(plan.activityLastOffsetX);
        const previousOffsetY = Number(plan.activityLastOffsetY);
        plan.activityPathDistancePx = Math.max(0, Number(plan.activityPathDistancePx) || 0) +
            Math.hypot(offsetX - previousOffsetX, offsetY - previousOffsetY);
    }
    plan.activityLastOffsetX = offsetX;
    plan.activityLastOffsetY = offsetY;
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

function _beginNekoIdleCat1PairMoveActivity(plan, run) {
    if (!plan) return;
    const startedAt = run && Number.isFinite(Number(run.startedAt))
        ? Number(run.startedAt)
        : Date.now();
    plan.activityId = run && run.runId
        ? run.runId
        : (plan.activityId || `cat1-small-move:${startedAt}`);
    plan.activityStartedAt = startedAt;
    plan.activityLastOffsetX = 0;
    plan.activityLastOffsetY = 0;
    plan.activityPathDistancePx = 0;
}

function _getNekoIdleCat1PairMoveActivityFacts(plan, endedAt) {
    if (!plan) return {};
    const hasActualActivity = Number.isFinite(Number(plan.activityStartedAt)) &&
        Number.isFinite(Number(plan.activityLastOffsetX)) &&
        Number.isFinite(Number(plan.activityLastOffsetY));
    const actualOffsetX = hasActualActivity ? Number(plan.activityLastOffsetX) : Number(plan.dx) || 0;
    const actualOffsetY = hasActualActivity ? Number(plan.activityLastOffsetY) : Number(plan.dy) || 0;
    const actualDistance = Math.hypot(actualOffsetX, actualOffsetY);
    const actualPathDistance = hasActualActivity
        ? Math.max(0, Number(plan.activityPathDistancePx) || 0)
        : actualDistance;
    const facts = {
        activityId: plan.activityId || '',
        distancePx: actualDistance,
        pathDistancePx: actualPathDistance,
        plannedDurationMs: Math.max(0, Number(plan.durationMs) || 0)
    };
    if (hasActualActivity) {
        const finishedAt = Number.isFinite(Number(endedAt)) ? Number(endedAt) : Date.now();
        facts.displacementPx = actualDistance;
        facts.durationMs = Math.max(0, finishedAt - Number(plan.activityStartedAt));
        if (plan.playgroundState) {
            const heldMs = plan.pauseStartedAt === null ? 0 : Math.max(0, _getNekoIdleNowMs() - plan.pauseStartedAt);
            facts.durationMs = plan.playbackStarted
                ? Math.max(0, facts.durationMs - plan.pausedDurationMs - heldMs) : 0;
        }
    }
    return facts;
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

function _clearNekoIdleCat1PlaygroundJourneyLoad(context) {
    if (context.sourceLoadCleanup) context.sourceLoadCleanup();
    if (context.entryTimer) clearTimeout(context.entryTimer);
    context.sourceLoadCleanup = null;
    context.entryTimer = 0;
}

function _clearNekoIdleCat1PlaygroundJourneyEntry(state) {
    const context = state && state.playgroundJourney;
    if (!context) return;
    state.playgroundJourney = null;
    _clearNekoIdleCat1PlaygroundJourneyLoad(context);
}

function _getNekoIdleCat1PlaygroundJourneyTargetFact(context) {
    const foot = body => body.y + body.height * (1 - (body.visibleInsetRatios?.bottom || 0));
    return { playgroundState: context.playgroundState, playgroundToken: context.playgroundToken,
        catBody: context.catBody, yarnBody: context.yarnBody,
        catLeft: context.catBody.x, catFoot: foot(context.catBody),
        yarnLeft: context.yarnBody.x, yarnFoot: foot(context.yarnBody) };
}

function _cancelNekoIdleCat1PlaygroundJourney(button, state, options = {}) {
    const context = state && state.playgroundJourney;
    if (!context) return;
    const facts = _completeNekoIdleCat1WalkActivity(state);
    _clearNekoIdleCat1PlaygroundJourneyEntry(state);
    _cancelNekoIdleCat1Frame(state);
    _cancelNekoIdleCat1SyncFrame(state);
    _cancelNekoIdleReturnPendingWalk(state);
    state.substate = state.profile.idleSubstate;
    state.target = null;
    state.targetKind = '';
    state.paused = false;
    state.lastStepAt = 0;
    state.facingRight = false;
    state.actionSettled = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _resetNekoIdleCat1WalkFinishResolution(state);
    const ownsState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey) === state;
    if (ownsState) {
        _setNekoIdleCat1Classes(button, state);
        if (options.resetArt !== false && _isNekoIdleCat1PlaygroundDropActive(button)) {
            _syncNekoIdleReturnCurrentArt(button);
            _syncNekoIdleCat1PlaygroundCatGeometry(button);
            _startNekoIdleCat1PlaygroundPhysics(button);
        }
    }
    state.playgroundJourneyBlockedTarget = options.reason === 'playground-journey-unavailable'
        ? null : _getNekoIdleCat1PlaygroundJourneyTargetFact(context);
    if (facts && facts.pathDistancePx > 0) {
        _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT,
            Object.assign({ source: 'cat1-journey', tier: state.profile.tier, timestamp: Date.now(),
                reason: options.reason || 'playground-journey-cancelled',
                targetKind: _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE, completed: false, cancelled: true }, facts));
    }
}

function _continueNekoIdleCat1PlaygroundJourney(button, state, context) {
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    const current = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!context || state.playgroundJourney !== context) return null;
    const held = state.paused || playground?.pointerBodyId === 'cat' ||
        playground?.pointerBodyId === context.yarnBody.id;
    const art = button && button.querySelector('.neko-idle-return-art');
    const hoverPaused = !!(state.paused && art?.__nekoIdleHoverSrc && art.__nekoIdleHoverPausedJourney);
    const target = current === state && playground === context.playgroundState &&
        playground.active && !playground.released && playground.token === context.playgroundToken &&
        playground.bodies.get('cat') === context.catBody && playground.bodies.get(context.yarnBody.id) === context.yarnBody &&
        _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) === _NEKO_IDLE_TIER_CAT1 &&
        !_isNekoIdleReturnDragActionActive(button) &&
        (held ? _getNekoIdleCat1PlaygroundYarnContext(button, {
            allowPending: true, allowOwnedMovement: true, allowOwnedHoverTransition: hoverPaused
        })
            : _getNekoIdleCat1PlaygroundYarnTarget(button, { allowOwnedMovement: true }));
    const expectedTop = hoverPaused
        ? context.lastFoot - context.catBody.height * (1 - (context.catBody.visibleInsetRatios?.bottom || 0))
        : context.lastTop;
    if (!target || target.yarnBody !== context.yarnBody || (context.playbackStarted && !context.committingPosition &&
        Math.hypot(context.catBody.x - context.lastLeft, context.catBody.y - expectedTop) > 0.5)) {
        _cancelNekoIdleCat1PlaygroundJourney(button, state, { reason: 'playground-journey-unavailable' });
        return null;
    }
    _syncNekoIdleCat1PlaygroundMovementPause(state.walkActivity, held);
    return held ? { ...state.target, ...target } : target;
}

function _applyNekoIdleCat1PlaygroundWalkPosition(button, state, context, left, top) {
    if (!_continueNekoIdleCat1PlaygroundJourney(button, state, context)) return false;
    context.committingPosition = true;
    const applied = _setNekoIdleCat1ContainerPosition(_getNekoIdleReturnContainerFromButton(button), left, top);
    context.committingPosition = false;
    if (state.playgroundJourney !== context) return false;
    context.lastLeft = context.catBody.x;
    context.lastTop = context.catBody.y;
    context.lastFoot = context.catBody.y + context.catBody.height * (1 - (context.catBody.visibleInsetRatios?.bottom || 0));
    _appendNekoIdleCat1WalkActivityPoint(state, context.lastLeft, context.lastTop);
    if (applied !== true || Math.hypot(context.lastLeft - left, context.lastTop - top) > 0.5) {
        _cancelNekoIdleCat1PlaygroundJourney(button, state, { reason: 'playground-journey-position-rejected' });
        return false;
    }
    return !!_continueNekoIdleCat1PlaygroundJourney(button, state, context);
}

function _syncNekoIdleCat1PlaygroundJourney(button) {
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    if (!playground || !playground.active) return;
    let state = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) {
        if (state?.playgroundJourney) _cancelNekoIdleCat1PlaygroundJourney(button, state, { reason: 'tier-change' });
        if (state) state.playgroundJourneyBlockedTarget = null;
        return;
    }
    const context = state && state.playgroundJourney;
    if (context) {
        const target = _continueNekoIdleCat1PlaygroundJourney(button, state, context);
        if (!target) return;
        state.target = target;
        if (state.paused || context.loading) return;
        if (state.substate === state.profile.walkingSubstate) {
            if (!context.playbackStarted) {
                _startNekoIdleCat1Walk(button, target);
                return;
            }
            if (context.playbackStarted && !state.frame) {
                state.frame = window.requestAnimationFrame(next => _stepNekoIdleCat1Walk(button, next, context));
            }
            return;
        }
        _scheduleNekoIdleCat1WalkStart(button, target);
        return;
    }
    if (playground.released || (state && (state.pairMovePlan || state.pairMoveFrame)) ||
        _isNekoIdleCat1IndependentActionActive(button, 'journey') ||
        _isNekoIdleCompactSurfaceDragging()) return;
    const target = _getNekoIdleCat1PlaygroundYarnTarget(button);
    if (!target) return;
    if (state?.playgroundJourneyBlockedTarget) {
        const previous = state.playgroundJourneyBlockedTarget;
        const next = _getNekoIdleCat1PlaygroundJourneyTargetFact(target);
        if (Object.keys(next).every(key => previous[key] === next[key])) return;
        state.playgroundJourneyBlockedTarget = null;
    }
    const art = button.querySelector('.neko-idle-return-art');
    if (!art || art.__nekoIdleHoverSrc) return;
    const centerDistance = Math.hypot(
        target.catBody.x + target.catBody.width / 2 - target.yarnBody.x - target.yarnBody.width / 2,
        target.catBody.y + target.catBody.height / 2 - target.yarnBody.y - target.yarnBody.height / 2
    );
    if (centerDistance < _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW.target.enterDistancePx) return;
    state = _getNekoIdleCat1Journey(button);
    state.playgroundJourney = Object.assign({ button, loading: false, playbackStarted: false,
        entryTimer: 0, sourceLoadCleanup: null, committingPosition: false }, target);
    state.target = target;
    state.targetKind = target.kind;
    state.actionSettled = false;
    _scheduleNekoIdleCat1WalkStart(button, target);
}

function _setNekoIdleCat1Substate(button, substate, options = {}) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    if (substate === profile.walkingSubstate) {
        _cancelNekoIdleReturnPendingWalk(state);
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
}

function _completeNekoIdleCat1JourneyStretch(button, targetKind, result) {
    if (result !== _NEKO_CAT_MIND_ACTION_RESULTS.DONE) return;
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    state.actionSettled = true;
    _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_STRETCH_DONE_NEAR_CHAT, {
        source: 'cat1-journey',
        tier: profile.tier,
        reason: 'stretch-settled',
        targetKind: targetKind || ''
    });

    if ((_getNekoIdleChatMinimizedRect() || _getNekoIdleChatCompactSurfaceRect())) {
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
        }
    }, _NEKO_IDLE_RETURN_TRANSITION_MS);
}

function _finishNekoIdleCat1Walk(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    if (_isNekoIdleCat1StretchActionActive(button)) return;
    const playgroundApproach = state.playgroundJourney;
    if (playgroundApproach && !_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundApproach)) return;
    const targetKind = state.targetKind || (state.target && state.target.kind) || '';
    // A delayed frame / observer sync can arrive after this approach has
    // already resolved. Do not re-roll the local probability or run the
    // opposite tail; a later walk start is the only reset point.
    if (targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE && state.walkFinishResolution) {
        return;
    }
    const walkActivityFacts = typeof _completeNekoIdleCat1WalkActivity === 'function'
        ? _completeNekoIdleCat1WalkActivity(state)
        : null;
    if (playgroundApproach) _clearNekoIdleCat1PlaygroundJourneyEntry(state);
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
        _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT, Object.assign({
            tier: _NEKO_IDLE_TIER_CAT1,
            source: 'cat1-walk-finish',
            timestamp: Date.now()
        }, walkActivityFacts || {}));
        if (playgroundApproach && ((button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey) !== state ||
            state.playgroundJourney || state.pairMovePlan ||
            button.__nekoIdleCat1PlaygroundDropState !== playgroundApproach.playgroundState ||
            playgroundApproach.playgroundState.token !== playgroundApproach.playgroundToken ||
            !playgroundApproach.playgroundState.active || playgroundApproach.playgroundState.released)) return;
        const localPlayStarted = walkFinishResolution === 'play' && _playNekoIdleCat1PlayAction(button, {
            source: 'cat1-journey-local',
            onTerminal(result, detail = {}) {
                const type = result === _NEKO_CAT_MIND_ACTION_RESULTS.DONE
                    ? _NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_LOCAL_PLAY_DONE
                    : _NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_LOCAL_PLAY_CANCELLED;
                _dispatchNekoCatIdleObservationSource(type, {
                    tier: _NEKO_IDLE_TIER_CAT1,
                    source: 'cat1-journey-local',
                    timestamp: Date.now(),
                    reason: detail.reason || result
                });
            }
        });
        if (localPlayStarted) {
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
    state.substate = state.profile.idleSubstate;
    state.targetKind = targetKind;
    state.actionSettled = true;
    _setNekoIdleCat1Classes(button, state);
    const localStretchStarted = _playNekoIdleCat1StretchAction(button, {
        onTerminal(result) {
            _completeNekoIdleCat1JourneyStretch(button, targetKind, result);
        }
    });
    if (localStretchStarted) return;
    _scheduleNekoIdleCat1JourneySync(button);
}

function _finishNekoIdleCat1CompactTopEdgeWalk(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const settledSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const settledTarget = state.target;
    const walkActivityFacts = typeof _completeNekoIdleCat1WalkActivity === 'function'
        ? _completeNekoIdleCat1WalkActivity(state)
        : null;
    _cancelNekoIdleCat1Frame(state);
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-compact-top-edge-walk-finish');
    _invalidateNekoIdleCat1CompactTopEdgeSettle(state);
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleCat1PairMove(state);
    const compactSettleToken = state.compactTopEdgeSettleToken || 0;
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
    _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DONE, Object.assign({
        source: 'cat1-journey',
        tier: _NEKO_IDLE_TIER_CAT1,
        reason: 'compact-top-edge-walk-finish',
        targetKind: state.targetKind
    }, walkActivityFacts || {}));

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
            latestState.compactTopEdgeSettleToken !== compactSettleToken ||
            latestState.substate !== profile.idleSubstate ||
            latestState.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE ||
            !latestState.actionSettled) {
            return;
        }
        latestState.facingRight = false;
        _setNekoIdleCat1Classes(button, latestState);
        _cancelNekoIdleCat1PairMove(latestState);
    }, _NEKO_IDLE_RETURN_TRANSITION_MS);
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
    const ownedPlaygroundWalk = arguments[2] || null;
    const currentState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if ((ownedPlaygroundWalk || currentState?.playgroundJourney) &&
        currentState?.playgroundJourney !== ownedPlaygroundWalk) return;
    if (!currentState?.playgroundJourney && _pauseNekoIdleCat1JourneyForGravity(button)) return;
    const state = _getNekoIdleCat1Journey(button);
    const playgroundWalk = state && state.playgroundJourney;
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!state || !container || state.paused || state.substate !== profile.walkingSubstate) {
        if (state) state.frame = 0;
        return;
    }
    if (playgroundWalk && !_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk)) return;
    if (playgroundWalk && (playgroundWalk.playgroundState.pointerBodyId === 'cat' ||
        playgroundWalk.playgroundState.pointerBodyId === playgroundWalk.yarnBody.id)) {
        state.lastStepAt = timestamp;
        state.frame = window.requestAnimationFrame(next => _stepNekoIdleCat1Walk(button, next, playgroundWalk));
        return;
    }

    const chatRect = _getNekoIdleChatMinimizedRect();
    const rawCompactAnchorRatio = state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
        ? state.compactFollowAnchorRatio
        : null;
    const compactAnchorRatio = rawCompactAnchorRatio === null || rawCompactAnchorRatio === undefined
        ? NaN
        : Number(rawCompactAnchorRatio);
    const target = playgroundWalk ? _continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk)
        : _getNekoIdleCat1Target(container, chatRect, {
        anchorRatio: Number.isFinite(compactAnchorRatio) ? compactAnchorRatio : null,
        compactTopEdgeBlocked: !!state.compactTopEdgeRearmRequired
    });
    if (!target) {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }

    state.target = target;
    state.targetKind = target.kind || '';
    const rect = playgroundWalk
        ? { left: playgroundWalk.catBody.x, top: playgroundWalk.catBody.y,
            width: playgroundWalk.catBody.width, height: playgroundWalk.catBody.height }
        : _getNekoDesktopVirtualElementRect(container);
    if (!rect) {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }
    state.facingRight = _resolveNekoIdleCat1TargetFacing(rect, target);
    if (playgroundWalk) _setNekoIdleCat1PlaygroundMoveFacing(button, state, state.facingRight);
    else _setNekoIdleCat1Classes(button, state);
    if (!playgroundWalk && _tryStartNekoIdleCat1DoorWalk(
        button,
        target,
        _getNekoIdleCat1JourneyDoorContinuation(button, state)
    )) {
        if (state.frame) window.cancelAnimationFrame(state.frame);
        state.frame = 0;
        return;
    }
    const speedRate = _updateNekoIdleCat1WalkSpeedRate(button, state, target.distance);
    if (target.distance <= profile.target.exitDistancePx) {
        if (playgroundWalk) {
            if (!_applyNekoIdleCat1PlaygroundWalkPosition(button, state, playgroundWalk, target.left, target.top)) return;
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _finishNekoIdleCat1Walk(button);
            return;
        }
        _appendNekoIdleCat1WalkActivityPoint(state, target.left, target.top);
        _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
        if (_pauseNekoIdleCat1JourneyForGravity(button)) return;
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
    if (playgroundWalk) {
        if (!_applyNekoIdleCat1PlaygroundWalkPosition(button, state, playgroundWalk, nextLeft, nextTop)) return;
    } else {
        _appendNekoIdleCat1WalkActivityPoint(state, nextLeft, nextTop);
        _setNekoIdleCat1ContainerPosition(container, nextLeft, nextTop);
    }
    // A wall collision can synchronously interrupt this walk through gravity's
    // state event. Do not re-arm the cancelled frame after returning from it.
    if (!playgroundWalk && _pauseNekoIdleCat1JourneyForGravity(button)) return;

    state.frame = window.requestAnimationFrame((nextTimestamp) => {
        _stepNekoIdleCat1Walk(button, nextTimestamp, playgroundWalk);
    });
}

function _startNekoIdleCat1Walk(button, target) {
    const currentState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!currentState?.playgroundJourney && _pauseNekoIdleCat1JourneyForGravity(button)) return;
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const playgroundWalk = state.playgroundJourney;
    if (playgroundWalk) {
        if (!_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk) || playgroundWalk.loading ||
            playgroundWalk.playbackStarted) return;
        const art = button.querySelector('.neko-idle-return-art');
        if (!art) {
            _cancelNekoIdleCat1PlaygroundJourney(button, state, { reason: 'playground-journey-art-missing' });
            return;
        }
        state.substate = state.profile.walkingSubstate;
        state.lastStepAt = 0;
        state.facingRight = _resolveNekoIdleCat1TargetFacing({ left: playgroundWalk.catBody.x }, target);
        _resetNekoIdleCat1WalkSpeed(state);
        _resetNekoIdleCat1WalkFinishResolution(state);
        state.walkPreviousDistance = target.distance;
        _cancelNekoIdleReturnPendingWalk(state);
        playgroundWalk.loading = true;
        playgroundWalk.entryPlaybackToken = state.playgroundWalkEntryPlaybackToken || 0;
        state.playgroundWalkEntryPlaybackToken = (playgroundWalk.entryPlaybackToken % 2) + 1;
        _setNekoIdleCat1Classes(button, state);
        _setNekoIdleCat1ActionArtForPlayback(art, state.profile.assets.walking(), playgroundWalk, {
            animate: false,
            canContinue: () => !!_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk),
            onError: () => _cancelNekoIdleCat1PlaygroundJourney(button, state, { reason: 'playground-journey-art-failed' }),
            onReady: function startPlayback() {
                if (!_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk)) return;
                if (playgroundWalk.playgroundState.pointerBodyId === 'cat' ||
                    playgroundWalk.playgroundState.pointerBodyId === playgroundWalk.yarnBody.id) {
                    state.frame = window.requestAnimationFrame(() => {
                        if (!_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk)) return;
                        state.frame = 0;
                        startPlayback();
                    });
                    return;
                }
                _syncNekoIdleCat1PlaygroundCatGeometry(button);
                if (!_continueNekoIdleCat1PlaygroundJourney(button, state, playgroundWalk)) return;
                playgroundWalk.loading = false;
                playgroundWalk.playbackStarted = true;
                playgroundWalk.lastLeft = playgroundWalk.catBody.x;
                playgroundWalk.lastTop = playgroundWalk.catBody.y;
                playgroundWalk.lastFoot = playgroundWalk.catBody.y + playgroundWalk.catBody.height *
                    (1 - (playgroundWalk.catBody.visibleInsetRatios?.bottom || 0));
                _beginNekoIdleCat1WalkActivity(state, { left: playgroundWalk.lastLeft, top: playgroundWalk.lastTop });
                _startNekoIdleCat1PlaygroundPhysics(button);
                state.frame = window.requestAnimationFrame(next => _stepNekoIdleCat1Walk(button, next, playgroundWalk));
            }
        });
        return;
    }
    if (_isNekoIdleCat1MovementAnchored(button)) {
        _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
        return;
    }
    if (_isNekoIdleReturnDragActionActive(button)) return;
    if (_isNekoIdleCat1IndependentActionActive(button)) return;
    const walkContainer = _getNekoIdleReturnContainerFromButton(button);
    const walkDragging = walkContainer && walkContainer.getAttribute('data-dragging');
    if (walkDragging && walkDragging !== 'false') return;
    const profile = state.profile;
    const currentRect = _getNekoDesktopVirtualElementRect(walkContainer);
    if (!currentRect) {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }
    state.target = target;
    state.targetKind = target && target.kind ? target.kind : '';
    state.facingRight = _resolveNekoIdleCat1TargetFacing(currentRect, target);
    if (state.substate !== profile.walkingSubstate) {
        _beginNekoIdleCat1WalkActivity(state, currentRect);
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
    const currentState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!currentState?.playgroundJourney && _pauseNekoIdleCat1JourneyForGravity(button)) return;
    const state = _getNekoIdleCat1Journey(button);
    const playgroundWalk = state && state.playgroundJourney;
    if (!state || state.paused) return;
    if (_isNekoIdleCat1IndependentActionActive(button, playgroundWalk ? 'journey' : undefined,
        playgroundWalk ? { allowOwnedMovement: true } : {})) return;
    if (_isNekoIdleCat1MovementAnchored(button)) {
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
    const rect = _getNekoDesktopVirtualElementRect(container);
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

function _prepareNekoIdleCat1PairMoveStart(button, state) {
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    if (playground && playground.active) {
        if (!state || state.paused || state.pairMovePlan || state.pairMoveFrame ||
            _isNekoIdleCat1PlaygroundEntryOrDropActive(button, 'small-move') ||
            _isNekoIdleCat1IndependentActionActive(button, 'small-move')) return;
        const art = button.querySelector('.neko-idle-return-art');
        if (art && art.__nekoIdleHoverSrc && !art.__nekoIdleHoverTimer) {
            _finishNekoIdleHoverArtAfterPlayback(art, _NEKO_IDLE_TIER_CAT1);
        }
        return;
    }
    if (_pauseNekoIdleCat1JourneyForGravity(button)) return;
    if (!button || !state || state.paused || state.pairMovePlan || state.pairMoveFrame) return;
    if (_isNekoIdleCat1MovementAnchored(button)) return;
    if (_isNekoIdleCat1IndependentActionActive(button)) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    if (state.substate !== profile.idleSubstate || !state.actionSettled) return;
    if (state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) return;
    if (state.pendingWalkTimer || state.pendingWalkReady || state.frame) return;
    if (_isNekoIdleReturnDragActionActive(button)) return;
    const art = button.querySelector('.neko-idle-return-art');
    if (art && art.__nekoIdleHoverSrc && !art.__nekoIdleHoverTimer) {
        _finishNekoIdleHoverArtAfterPlayback(art, profile.tier);
    }
}

function _canScheduleNekoIdleCat1PairMove(button, state) {
    const playground = button && button.__nekoIdleCat1PlaygroundDropState;
    if (playground && playground.active) {
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1 ||
            _isNekoIdleCat1PlaygroundEntryOrDropActive(button, 'small-move') ||
            window.NekoDesktopWindowGravity?.canWalk?.(button) === false ||
            _isNekoIdleCat1IndependentActionActive(button, 'small-move') ||
            _isNekoIdleCompactSurfaceDragging() || _isAnyNekoIdleReturnDragActionBlocking() ||
            _isAnyNekoCatMindReturnPending() || _isNekoCatMindTransitionActive(button)) return false;
        if (state && (state.paused || state.pairMovePlan || state.pairMoveFrame ||
            state.pendingWalkTimer || state.pendingWalkReady || state.frame)) return false;
        const art = button.querySelector('.neko-idle-return-art');
        if (!art || art.__nekoIdleHoverSrc) return false;
        const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
        const config = profile.pairMove || {};
        return _hasNekoIdleCat1PlaygroundMoveSpace(button,
            Math.max(1, Number(config.maxDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX),
            Math.max(1, Number(config.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX));
    }
    if (window.NekoDesktopWindowGravity?.canWalk?.(button) === false) return false;
    if (!button || !state || state.paused || state.pairMovePlan || state.pairMoveFrame) return false;
    if (_isNekoIdleCat1MovementAnchored(button)) return false;
    if (_isNekoIdleCat1IndependentActionActive(button)) return false;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    if (state.substate !== profile.idleSubstate || !state.actionSettled) return false;
    if (state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) return false;
    if (state.pendingWalkTimer || state.pendingWalkReady || state.frame) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;

    const art = button.querySelector('.neko-idle-return-art');
    if (art && art.__nekoIdleHoverSrc) return false;

    const container = _getNekoIdleReturnContainerFromButton(button);
    const chatTarget = _getNekoIdleCat1PairMoveChatTarget();
    if (chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()) return false;
    const canMoveSolo = chatTarget ? false : _canNekoIdleCat1MoveSoloWithExpandedChat();
    if (!container || (!chatTarget && !canMoveSolo)) return false;
    if (container.style.display === 'none' || container.getAttribute('data-dragging') === 'true') return false;

    const catRect = _getNekoDesktopVirtualElementRect(container);
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
    const completedPlan = state.pairMovePlan;
    if (completedPlan.playgroundState && (!_syncNekoIdleCat1PlaygroundSmallMove(button) ||
        completedPlan.pauseStartedAt !== null)) return;
    const applied = _applyNekoIdleCat1PairMovePlan(completedPlan, 1);
    if (completedPlan.playgroundState) {
        if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, completedPlan)) return;
        if (applied !== true) {
            _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-position-rejected' });
            return;
        }
        if (!_syncNekoIdleCat1PlaygroundSmallMove(button)) return;
        if (state.pairMoveFrame) window.cancelAnimationFrame(state.pairMoveFrame);
        state.pairMoveToken += 1;
    } else if (state.pairMovePlan !== completedPlan || _pauseNekoIdleCat1JourneyForGravity(button)) return;
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-pair-move-finish', completedPlan);
    state.pairMoveFrame = 0;
    state.pairMovePlan = null;
    const activityFacts = typeof _getNekoIdleCat1PairMoveActivityFacts === 'function'
        ? _getNekoIdleCat1PairMoveActivityFacts(completedPlan, Date.now())
        : {
            activityId: completedPlan.activityId || state.catMindRunId || '',
            distancePx: Math.hypot(completedPlan.dx, completedPlan.dy),
            pathDistancePx: Math.hypot(completedPlan.dx, completedPlan.dy),
            plannedDurationMs: completedPlan.durationMs
        };
    if (completedPlan.playgroundState) {
        _setNekoIdleCat1PlaygroundMoveFacing(button, state, false);
        _syncNekoIdleReturnCurrentArt(button);
        _syncNekoIdleCat1PlaygroundCatGeometry(button);
        _startNekoIdleCat1PlaygroundPhysics(button);
    } else {
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
    }
    _reportNekoCatMindStateActionResult(state, _NEKO_CAT_MIND_ACTION_RESULTS.DONE, {
        reason: 'cat1-pair-move-finish',
        durationMs: completedPlan.playgroundState ? activityFacts.durationMs : undefined,
        detail: Object.assign({ restored: true }, activityFacts)
    });
}

function _stepNekoIdleCat1PairMove(button, startedAt, timestamp) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const activePlan = state && state.pairMovePlan;
    if (activePlan && activePlan.playgroundState) {
        if (!_syncNekoIdleCat1PlaygroundSmallMove(button)) return;
    } else if (_pauseNekoIdleCat1JourneyForGravity(button)) return;
    if (!state || !state.pairMovePlan || state.paused) {
        if (state) state.pairMoveFrame = 0;
        return;
    }
    const plan = state.pairMovePlan;
    if (plan.playgroundState && plan.pauseStartedAt !== null) {
        state.pairMoveFrame = window.requestAnimationFrame((nextTimestamp) => {
            if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
            _stepNekoIdleCat1PairMove(button, startedAt, nextTimestamp);
        });
        return;
    }
    const chatAvailable = plan.playgroundState ? true : plan.chatMode === 'desktop'
        ? _getNekoIdleDesktopChatMinimizedRect()
        : (plan.chatMode === 'dom'
            ? _getNekoIdleReactChatMinimizedShell()
            : _canNekoIdleCat1MoveSoloWithExpandedChat());
    if (!chatAvailable || plan.container.getAttribute('data-dragging') === 'true') {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }
    const elapsedMs = Math.max(0, timestamp - startedAt - (plan.playgroundState ? plan.pausedDurationMs : 0));
    const progress = plan.durationMs > 0 ? Math.min(1, elapsedMs / plan.durationMs) : 1;
    if (progress >= 1) {
        // 末帧只由 _finishNekoIdleCat1PairMove 强制同步一次原生 bounds；
        // 若先在此处 apply(progress=1) 再 finish，会触发两次 force dispatch（绕过节流/去重）的重复同步。
        _finishNekoIdleCat1PairMove(button);
        return;
    }
    const applied = _applyNekoIdleCat1PairMovePlan(plan, progress);
    if (plan.playgroundState) {
        if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
        if (applied !== true) {
            _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-position-rejected' });
            return;
        }
        if (!_syncNekoIdleCat1PlaygroundSmallMove(button)) return;
    } else if (state.pairMovePlan !== plan || _pauseNekoIdleCat1JourneyForGravity(button)) return;
    state.pairMoveFrame = window.requestAnimationFrame((nextTimestamp) => {
        if (plan.playgroundState && !_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
        _stepNekoIdleCat1PairMove(button, startedAt, nextTimestamp);
    });
}

function _startNekoIdleCat1PairMove(button) {
    const catMindRunOptions = arguments[1] || {};
    const isCatMindRun = catMindRunOptions.source === 'cat_mind';
    if (!isCatMindRun) return false;
    if (!_isNekoCatActionResourceAvailable('cat1_small_move')) return false;
    const state = _getNekoIdleCat1Journey(button);
    _prepareNekoIdleCat1PairMoveStart(button, state);
    if (_isNekoIdleCat1MovementAnchored(button)) {
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
    if (plan.playgroundState) plan.token = state.pairMoveToken;
    const run = _beginNekoCatMindStateAction(state, _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE, _NEKO_IDLE_TIER_CAT1, {
        source: catMindRunOptions.source, requestId: catMindRunOptions.requestId
    });
    if (plan.playgroundState) plan.runId = run.runId;
    if (typeof _beginNekoIdleCat1PairMoveActivity === 'function') {
        _beginNekoIdleCat1PairMoveActivity(plan, run);
    } else {
        plan.activityId = run && run.runId ? run.runId : `cat1-small-move:${Date.now()}:${state.pairMoveToken}`;
    }
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);
    if (plan.playgroundState) {
        if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return true;
        _setNekoIdleCat1PlaygroundMoveFacing(button, state, plan.dx > 0);
        const art = button.querySelector('.neko-idle-return-art');
        if (!art) {
            _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-art-missing' });
            return true;
        }
        const previousEntryToken = state.pairMoveEntryPlaybackToken || 0;
        state.pairMoveEntryPlaybackToken = (previousEntryToken % 2) + 1;
        plan.entryPlaybackToken = previousEntryToken;
        _setNekoIdleCat1ActionArtForPlayback(art, state.profile.assets.walking(), plan, {
            animate: false,
            canContinue: () => _continueNekoIdleCat1PlaygroundSmallMove(button, state, plan),
            onError: () => _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-art-failed' }),
            onReady: function startPlayback() {
                if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
                if (plan.playgroundState.pointerBodyId === 'cat' ||
                    (plan.playgroundYarnBody && plan.playgroundState.pointerBodyId === plan.playgroundYarnBody.id)) {
                    state.pairMoveFrame = window.requestAnimationFrame(() => {
                        if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
                        state.pairMoveFrame = 0;
                        startPlayback();
                    });
                    return;
                }
                _syncNekoIdleCat1PlaygroundCatGeometry(button);
                if (!_syncNekoIdleCat1PlaygroundSmallMove(button)) return;
                const body = plan.playgroundBody;
                const target = (plan.playgroundYarnBody ? _getNekoIdleCat1PlaygroundPairMoveTarget
                    : _getNekoIdleCat1PlaygroundMoveTarget)(button, { left: body.x + plan.dx, top: body.y }, {
                    allowOwnedMovement: true
                });
                const config = state.profile.pairMove || {};
                const minUsableDistance = Math.max(1, Number(config.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX);
                if (!target || target.distance < minUsableDistance) {
                    _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-space-changed' });
                    return;
                }
                plan.catStartLeft = body.x;
                plan.catStartTop = body.y;
                if (plan.playgroundYarnBody) {
                    plan.yarnStartLeft = plan.playgroundYarnBody.x;
                    plan.yarnStartTop = plan.playgroundYarnBody.y;
                }
                plan.dx = target.left - body.x;
                plan.dy = target.top - body.y;
                const speed = Math.max(1, Number(config.speedPxPerSec) || _NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC);
                const minDuration = Math.max(1, Number(config.minDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DURATION_MS);
                const maxDuration = Math.max(minDuration, Number(config.maxDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DURATION_MS);
                plan.durationMs = Math.max(minDuration, Math.min(maxDuration, Math.round(target.distance / speed * 1000)));
                const startedAt = _getNekoIdleNowMs();
                run.startedAt = Date.now();
                state.catMindStartedAt = run.startedAt;
                plan.playbackStarted = true;
                plan.pausedDurationMs = 0;
                plan.pauseStartedAt = plan.playgroundState.pointerBodyId === 'cat' ? startedAt : null;
                _beginNekoIdleCat1PairMoveActivity(plan, run);
                const started = _notifyNekoCatMindRunnerStarted(catMindRunOptions, run);
                if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
                if (started === false) {
                    _cancelNekoIdleCat1PairMove(state, { reason: 'playground-small-move-start-rejected' });
                    return;
                }
                _startNekoIdleCat1PlaygroundPhysics(button);
                if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
                state.pairMoveFrame = window.requestAnimationFrame((timestamp) => {
                    if (!_continueNekoIdleCat1PlaygroundSmallMove(button, state, plan)) return;
                    _stepNekoIdleCat1PairMove(button, startedAt, timestamp);
                });
            }
        });
        return true;
    }
    state.facingRight = plan.dx > 0;
    if (plan.chatMode === 'solo' && _canNekoIdleCat1MoveSoloWithExpandedChat()) {
        _dispatchNekoIdleCat1MotionInputRegionState(state, true, 'cat1-pair-move-start', plan);
    }
    _cancelNekoIdleReturnPendingWalk(state);
    _invalidateNekoIdleCat1CompactTopEdgeSettle(state);
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
            state.containerObserver = new MutationObserver((mutations) => {
                const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
                if (!currentState || currentState.paused) return;
                const transferredAnchorChanged = mutations.some((mutation) =>
                    mutation && mutation.type === 'attributes' &&
                    mutation.attributeName === 'data-neko-live2d-peek-anchor');
                if (currentState.substate === currentState.profile.walkingSubstate && !transferredAnchorChanged) return;
                const observerDragging = container.getAttribute('data-dragging');
                if (observerDragging && observerDragging !== 'false') return;
                _scheduleNekoIdleCat1JourneySync(button);
            });
            state.containerObserver.observe(container, {
                attributes: true,
                attributeFilter: ['style', 'data-dragging', 'data-neko-live2d-peek-anchor']
            });
        }
    }
}

function _syncNekoIdleCat1Journey(button, tier) {
    if (!button) return;
    if (_isNekoIdleCat1PlaygroundDropActive(button)) {
        _syncNekoIdleCat1PlaygroundJourney(button);
        return;
    }
    if (_pauseNekoIdleCat1JourneyForGravity(button)) return;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
    if (_isNekoIdleCat1MovementAnchored(button)) {
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
    const containerRect = _getNekoDesktopVirtualElementRect(container);
    const centerDistancePx = _getNekoIdleRectCenterDistancePx(containerRect, chatRect);
    const walkStartDistancePx = !window.NekoDesktopWindowGravity?.isActive(button) && Number.isFinite(centerDistancePx)
        ? centerDistancePx
        : target.distance;
    if (compactTopEdgeTarget) {
        _cancelNekoIdleCat1PairMove(state);
        if (target.distance <= profile.target.exitDistancePx) {
            _cancelNekoIdleReturnPendingWalk(state);
        }
    }

    if (walkStartDistancePx < profile.target.enterDistancePx && state.substate !== profile.walkingSubstate && !compactTopEdgeTarget) {
        _cancelNekoIdleReturnPendingWalk(state);
    }

    if (state.substate === profile.walkingSubstate && target.distance > profile.target.exitDistancePx) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    if (walkStartDistancePx >= profile.target.enterDistancePx ||
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
            _appendNekoIdleCat1WalkActivityPoint(state, target.left, target.top);
            _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
            _finishNekoIdleCat1CompactTopEdgeWalk(button);
        } else {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _finishNekoIdleCat1Walk(button);
        }
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
    if (_isNekoIdleCat1PlaygroundDropActive(button)) {
        const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
        if (!state) { _syncNekoIdleCat1PlaygroundJourney(button); return; }
        if (state.syncFrame) return;
        const playground = button.__nekoIdleCat1PlaygroundDropState;
        const token = playground.token;
        const frame = window.requestAnimationFrame(() => {
            if (state.syncFrame !== frame || button.__nekoIdleCat1PlaygroundDropState !== playground ||
                playground.token !== token) return;
            state.syncFrame = 0;
            _syncNekoIdleCat1PlaygroundJourney(button);
        });
        state.syncFrame = frame;
        return;
    }
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
    if (_isNekoIdleCat1MovementAnchored(button)) {
        if (_isNekoIdleCat1EdgePeekActive(button)) {
            _reclampNekoIdleCat1EdgePeekToViewport(button);
        }
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
    if (!state || state.substate !== state.profile.walkingSubstate) return;
    state.paused = true;
    if (state.playgroundJourney?.loading && !state.playgroundJourney.playbackStarted) {
        _clearNekoIdleCat1PlaygroundJourneyLoad(state.playgroundJourney);
        state.playgroundJourney.loading = false;
    }
    if (state.playgroundJourney && state.walkActivity && state.walkActivity.pauseStartedAt == null) {
        state.walkActivity.pauseStartedAt = _getNekoIdleNowMs();
    }
    _cancelNekoIdleCat1Frame(state);
    _setNekoIdleCat1Classes(button, state);
}

function _resumeNekoIdleCat1Journey(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.paused) return;
    if (state.playgroundJourney && state.walkActivity && state.walkActivity.pauseStartedAt != null) {
        state.walkActivity.pausedDurationMs = (state.walkActivity.pausedDurationMs || 0) +
            Math.max(0, _getNekoIdleNowMs() - state.walkActivity.pauseStartedAt);
        state.walkActivity.pauseStartedAt = null;
    }
    state.paused = false;
    state.lastStepAt = 0;
    _setNekoIdleCat1Classes(button, state);
    _syncNekoIdleCat1Journey(button);
}

function _setNekoIdleReturnArtSource(art, nextSrc, tier, options = {}) {
    if (!art || !nextSrc) return;

    const playbackSrc = options.restartAnimation === true
        ? _buildNekoIdleAnimationRestartUrl(nextSrc, options.playbackToken)
        : nextSrc;

    const notifyTransitionEnd = () => {
        if (typeof options.onTransitionEnd !== 'function') return;
        try { options.onTransitionEnd(); } catch (_) {}
    };
    const notifyTransitionError = () => {
        if (typeof options.onTransitionError !== 'function') return;
        try { options.onTransitionError(); } catch (_) {}
    };

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
        art.src = playbackSrc;
        _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, playbackSrc);
        notifyTransitionEnd();
        return;
    }

    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, '');
    if (art.__nekoIdleTransitionTo === nextSrc) {
        return;
    }

    _cleanupNekoIdleArtTransition(art);

    const button = art.closest('.neko-idle-return-btn');
    if (!button) {
        art.src = playbackSrc;
        notifyTransitionEnd();
        return;
    }

    const nextArt = document.createElement('img');
    nextArt.className = 'neko-idle-return-art neko-idle-return-art-next';
    nextArt.src = nextSrc;
    nextArt.alt = art.alt || '';
    nextArt.draggable = false;
    nextArt.setAttribute('data-neko-idle-tier', tier);

    let playbackReady = null;
    if (playbackSrc !== nextSrc) {
        const preload = document.createElement('img');
        playbackReady = new Promise((resolve) => {
            let settled = false;
            const settle = (ready) => {
                if (settled) return;
                settled = true;
                resolve(ready);
            };
            preload.onload = () => settle(true);
            preload.onerror = () => settle(false);
            art.__nekoIdleTransitionPreloadCancel = () => settle(false);
        });
        art.__nekoIdleTransitionPreload = preload;
        preload.src = playbackSrc;
    }

    const ownsTransition = () => (
        art.__nekoIdleTransitionNext === nextArt &&
        art.__nekoIdleTransitionTo === nextSrc
    );
    const commit = () => {
        if (!ownsTransition()) return;
        _clearNekoIdleGifPlaybackSource(art);
        art.removeAttribute(_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR);
        art.src = playbackSrc;
        _cleanupNekoIdleArtTransition(art);
        _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, playbackSrc);
        notifyTransitionEnd();
    };
    const finish = () => {
        if (!playbackReady) {
            commit();
            return;
        }
        playbackReady.then((ready) => {
            if (!ownsTransition()) return;
            if (!ready) {
                _cleanupNekoIdleArtTransition(art);
                notifyTransitionError();
                return;
            }
            commit();
        });
    };

    art.__nekoIdleTransitionNext = nextArt;
    art.__nekoIdleTransitionTo = nextSrc;
    button.appendChild(nextArt);
    void nextArt.offsetWidth;
    button.classList.add('is-tier-transitioning');
    art.__nekoIdleTransitionTimer = setTimeout(finish, _NEKO_IDLE_RETURN_TRANSITION_MS);
}

function _playNekoIdleHoverArt(art, tier, options = {}) {
    if (!art || !tier || tier === _NEKO_IDLE_TIER_NONE) return;

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const button = _getNekoIdleReturnButtonFromArt(art);
    if (_isNekoIdleReturnDragActionActive(button)) return;
    if (_isNekoIdleCat1IndependentActionActive(button, 'hover')) return;
    const profile = _getNekoIdleReturnSubactionProfile(normalizedTier);
    const subactionState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const useSubactionInteractive = !!(profile
        && subactionState
        && subactionState.profile === profile
        && subactionState.substate === profile.walkingSubstate);
    const previousHoverSrc = art.__nekoIdleHoverTier === normalizedTier ? art.__nekoIdleHoverSrc : '';
    // The CAT1 idle hover art can keep looping while the pointer stays over the cat.
    const previousDurationMs = previousHoverSrc && normalizedTier === _NEKO_IDLE_TIER_CAT1 && !useSubactionInteractive
        ? _getNekoCatAppearanceDurationMs(previousHoverSrc)
        : 0;
    const hoverCycleEnded = previousDurationMs > 0 && Date.now() - art.__nekoIdleHoverStartedAt >= previousDurationMs;
    const clickSrc = useSubactionInteractive
        ? profile.assets.interactive()
        : (!hoverCycleEnded && previousHoverSrc) || _getNekoIdleReturnClickAssetUrl(normalizedTier, options);
    const dispatchHoverObservation = () => {
        // This helper is also reused by autonomous sound presentation. Only a
        // real input adapter may label the presentation as user interaction.
        if (options.userInitiated !== true) return;
        _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT_HOVER_REACTION, {
            source: 'return-ball-hover',
            tier: normalizedTier,
            reason: useSubactionInteractive ? 'subaction-interactive' : 'return-hover',
            substate: subactionState && subactionState.substate ? subactionState.substate : '',
            targetKind: subactionState && subactionState.targetKind ? subactionState.targetKind : ''
        });
    };
    if (!clickSrc) {
        // The audio/social action remains valid when its optional visual
        // reaction is unavailable.  Never assign an empty URL: that would
        // blank the cat and leave a journey paused on a missing asset.
        dispatchHoverObservation();
        return;
    }
    _cleanupNekoIdleArtTransition(art);
    if (subactionState && subactionState.profile === profile) {
        _cancelNekoIdleCat1PairMove(subactionState);
    }
    const hoverPausedJourney = !!(useSubactionInteractive && !subactionState.paused);
    if (useSubactionInteractive) {
        _pauseNekoIdleCat1Journey(button);
    }
    if (!hoverCycleEnded && previousHoverSrc === clickSrc) {
        if (art.__nekoIdleHoverTimer) {
            clearTimeout(art.__nekoIdleHoverTimer);
            art.__nekoIdleHoverTimer = 0;
        }
        art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
        _clearNekoIdleGifPlaybackSource(art);
        if ((art.getAttribute('src') || '') !== clickSrc) {
            art.src = clickSrc;
            if (_isNekoIdleCat1PlaygroundDropActive(button)) {
                _syncNekoIdleCat1PlaygroundCatGeometry(button);
                _syncNekoIdleCat1PlaygroundBasicPresentation(button);
                _startNekoIdleCat1PlaygroundPhysics(button);
            }
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
    art.__nekoIdleHoverPausedJourney = hoverPausedJourney;
    art.src = clickSrc;
    if (_isNekoIdleCat1PlaygroundDropActive(button)) {
        _syncNekoIdleCat1PlaygroundCatGeometry(button);
        _syncNekoIdleCat1PlaygroundBasicPresentation(button);
        _startNekoIdleCat1PlaygroundPhysics(button);
    }
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, normalizedTier, clickSrc);
    dispatchHoverObservation();
}

function _finishNekoIdleHoverArtAfterPlayback(art, tier) {
    if (!art || !tier || tier === _NEKO_IDLE_TIER_NONE) return;

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const button = _getNekoIdleReturnButtonFromArt(art);
    if (_isNekoIdleReturnDragActionActive(button)) return;
    if (_isNekoIdleCat1IndependentActionActive(button, 'hover', { allowPending: true })) return;
    const token = art.__nekoIdleHoverToken || 0;
    const startedAt = art.__nekoIdleHoverStartedAt || 0;
    const hoverSrc = art.__nekoIdleHoverSrc || _getNekoIdleReturnClickAssetUrl(normalizedTier);

    if (art.__nekoIdleHoverTimer) {
        clearTimeout(art.__nekoIdleHoverTimer);
        art.__nekoIdleHoverTimer = 0;
    }

    _getNekoIdleAnimationDurationMs(hoverSrc).then((durationMs) => {
        if ((art.__nekoIdleHoverToken || 0) !== token) return;
        if (art.__nekoIdleHoverTier !== normalizedTier) return;

        const elapsedMs = startedAt ? Math.max(0, Date.now() - startedAt) : durationMs;
        const delayMs = Math.max(0, durationMs - elapsedMs);
        art.__nekoIdleHoverTimer = setTimeout(() => {
            if ((art.__nekoIdleHoverToken || 0) !== token) return;
            if (art.__nekoIdleHoverTier !== normalizedTier) return;
            const resumeJourney = art.__nekoIdleHoverPausedJourney === true;
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
            if (_isNekoIdleCat1PlaygroundDropActive(button)) {
                _syncNekoIdleCat1PlaygroundCatGeometry(button);
                _syncNekoIdleCat1PlaygroundBasicPresentation(button);
                _startNekoIdleCat1PlaygroundPhysics(button);
            }
            if (resumeJourney) {
                _resumeNekoIdleCat1Journey(_getNekoIdleReturnButtonFromArt(art));
            }
            _scheduleNekoIdleCat1JourneySync(_getNekoIdleReturnButtonFromArt(art));
        }, delayMs);
    });
}

function _syncNekoIdleReturnCurrentArt(button, tier) {
    const art = button && button.querySelector('.neko-idle-return-art');
    if (!art) return false;
    const normalizedTier = _normalizeNekoIdleReturnTier(tier || button.getAttribute('data-neko-idle-tier'));
    if (normalizedTier === _NEKO_IDLE_TIER_NONE) return false;
    const playground = button.__nekoIdleCat1PlaygroundDropState;
    const playgroundActive = !!(playground && playground.active && !playground.released);
    const body = playgroundActive && playground.bodies.get('cat');
    if (body) body.presentationGrounded = body.grounded;
    const visual = _getNekoIdleCat1ActiveVisualActionProfile(button);
    const journey = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    const hoverPausedWalk = playgroundActive && journey?.playgroundJourney && journey.paused &&
        art.__nekoIdleHoverSrc && art.__nekoIdleHoverPausedJourney;
    const actionActive = _isNekoIdleCat1PlayActionActive(button) ||
        _isNekoIdleCat1EatActionActive(button) || _isNekoIdleCat1StretchActionActive(button) ||
        (playgroundActive && _isNekoIdleCat1PlaygroundMoveActive(button) && !hoverPausedWalk) ||
        (visual && _getNekoIdleVisualActionTier(visual) === normalizedTier);
    const dragging = _isNekoIdleReturnDragActionActive(button) ||
        (playgroundActive && playground.draggingBodyId === 'cat');
    const currentSrc = art.getAttribute('src') || '';
    const hoverMatchesCurrent = art.__nekoIdleHoverSrc && art.__nekoIdleHoverTier === normalizedTier &&
        !dragging && !actionActive &&
        _isNekoIdleAnimationRestartUrlForSource(currentSrc, art.__nekoIdleHoverSrc);
    if (hoverMatchesCurrent && art.__nekoIdleTransitionTo &&
        !_isNekoIdleAnimationRestartUrlForSource(art.__nekoIdleTransitionTo, art.__nekoIdleHoverSrc)) {
        _cleanupNekoIdleArtTransition(art);
    }
    const hoverOwnsArt = hoverMatchesCurrent && (!playgroundActive ||
        _isNekoIdleCat1PlaygroundCatPresentationStable(button, {
            allowPending: true, allowOwnedHoverTransition: true
        }));
    const nextSrc = hoverOwnsArt
        ? art.__nekoIdleHoverSrc
        : _getNekoIdleReturnCurrentArtUrl(button, normalizedTier);
    if (nextSrc && art.__nekoIdleTransitionTo &&
        !_isNekoIdleAnimationRestartUrlForSource(art.__nekoIdleTransitionTo, nextSrc)) {
        _cleanupNekoIdleArtTransition(art);
    }
    art.setAttribute('data-neko-idle-tier', normalizedTier);
    if (hoverOwnsArt) return false;
    if (art.__nekoIdleHoverSrc) _clearNekoIdleHoverPlayback(art);
    if (!nextSrc || _isNekoIdleAnimationRestartUrlForSource(currentSrc, nextSrc) ||
        art.__nekoIdleGifPlaybackBaseSrc === nextSrc || art.__nekoIdleTransitionTo === nextSrc) return false;
    _setNekoIdleReturnArtSource(art, nextSrc, normalizedTier, { animate: false });
    if (playgroundActive) _syncNekoIdleCat1PlaygroundCatGeometry(button);
    return true;
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
        _cancelNekoIdleCat1VisualAction(button, { restoreArt: false, reason: 'tier-change' });
        _cancelNekoIdleCat1StretchAction(button, { restoreArt: false });
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
    if (_isNekoIdleCat1PlaygroundDropActive(button) && normalizedTier === _NEKO_IDLE_TIER_NONE) {
        _releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'tier-change');
    }
    const playgroundActive = _isNekoIdleCat1PlaygroundDropActive(button);
    const previousTier = playgroundActive
        ? _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'))
        : normalizedTier;
    if (button.__nekoIdleThoughtBubbleTier && button.__nekoIdleThoughtBubbleTier !== normalizedTier) {
        _clearNekoIdleThoughtBubble(button);
    }
    const dragState = button.__nekoIdleReturnDragActionState;
    const dragActive = _isNekoIdleReturnDragActionActive(button);
    let activeVisualProfile = _getNekoIdleCat1ActiveVisualActionProfile(button);
    let visualActionMatchesTier = !!(activeVisualProfile &&
        _getNekoIdleVisualActionTier(activeVisualProfile) === normalizedTier);
    if (activeVisualProfile && !visualActionMatchesTier) {
        _cancelNekoIdleCat1VisualAction(button, { restoreArt: false, reason: 'tier-change' });
        activeVisualProfile = null;
        visualActionMatchesTier = false;
    }
    if (!playgroundActive || previousTier !== normalizedTier) {
        _syncNekoIdleSleepSoundForTier(normalizedTier);
        _syncNekoIdleCat1AmbientSoundForTier(normalizedTier);
    }
    if (normalizedTier !== _NEKO_IDLE_TIER_CAT1 && (!playgroundActive || previousTier !== normalizedTier)) {
        _clearNekoIdleCat1QuestionMark(button);
        _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
        _cancelNekoIdleCat1StretchAction(button, { restoreArt: false });
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
    const visualActionActive = _isNekoIdleCat1VisualActionActive(button);
    if (art) {
        if (playgroundActive) {
            _syncNekoIdleReturnCurrentArt(button, normalizedTier);
        } else if (dragActive && normalizedTier !== _NEKO_IDLE_TIER_NONE) {
            _setNekoIdleReturnDragActionArt(button, normalizedTier);
        } else if (visualActionActive && visualActionMatchesTier) {
            const visualActionSrc = _getNekoIdleCat1VisualActionAssetUrl(button);
            if (!_isNekoIdleAnimationRestartUrlForSource(
                art.getAttribute('src') || '',
                visualActionSrc
            )) {
                _setNekoIdleReturnArtSource(
                    art,
                    visualActionSrc,
                    normalizedTier,
                    { animate: false }
                );
            }
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
                _getNekoIdleCat1EatActionAssetUrl(button),
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
    if (playgroundActive && previousTier !== normalizedTier) {
        _syncNekoIdleCat1PlaygroundCatGeometry(button);
        _startNekoIdleCat1PlaygroundPhysics(button);
    }
    if (playgroundActive) _syncNekoIdleCat1PlaygroundBasicPresentation(button);
    if (normalizedTier === _NEKO_IDLE_TIER_CAT1 &&
        !playgroundActive && !dragActive && !eatActionActive && !playActionActive && !visualActionActive) {
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

    // Finish a bubble input even when its original Playground session ended,
    // or the pointer was released outside the cat. This never takes body input.
    ['pointerup', 'pointercancel'].forEach((type) => {
        document.addEventListener(type, (event) => {
            _forEachNekoIdleReturnButton((button) => {
                _finishNekoIdleCat1PlaygroundBubblePointer(button, event);
            });
        }, true);
    });

    window.addEventListener('neko:desktop-window-gravity-state', (event) => {
        const button = event.detail && event.detail.button;
        if (!button || button.isConnected === false) return;
        if (!_pauseNekoIdleCat1JourneyForGravity(button)) {
            _scheduleNekoIdleCat1JourneySync(button);
        }
    });

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
        const playgroundGesture = detail.source === 'cat1-playground';
        if (!playgroundGesture && _isNekoIdleCat1PlaygroundEntryOrDropActive(manualMoveButton)) return;
        if (detail.reason === 'return-ball-drag-end') {
            _finishNekoIdleReturnDragActionForContainer(detail.container);
            if (playgroundGesture) return;
            if (_isNekoIdleCat1EdgePeekActive(detail.container)) {
                const dragMoved = [
                    detail.movedDistancePx,
                    detail.displacementPx,
                    detail.pathDistancePx
                ].some((distance) => Number(distance) > 0);
                if (detail.dragCancelled !== true && dragMoved) {
                    _dispatchNekoIdleCat1EdgePeekAfterDragObservation(detail.container);
                }
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
            _finishNekoIdleReturnDragActionForContainer(detail.container, {
                restoreArt: playgroundGesture && detail.restoreArt !== false
            });
            if (playgroundGesture) return;
            _scheduleNekoIdleCat1JourneySyncForContainer(detail.container);
            return;
        }
        if (detail.reason === 'return-ball-drag-start') {
            _prepareNekoIdleReturnDragActionForContainer(detail.container, detail);
            return;
        }
        if (detail.reason === 'return-ball-drag-active') {
            _startNekoIdleReturnDragActionForContainer(detail.container, detail);
            return;
        }
        if (detail.reason === 'return-ball-drag-motion') {
            _handleNekoIdleCat1RapidDragMotionForContainer(detail.container, detail);
            return;
        }
        if (!playgroundGesture) _cancelNekoIdleCat1JourneyForContainer(detail.container);
    });

    window.addEventListener('neko:idle-chat-minimized-state', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const receivedAt = Date.now();
        const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);
        const lifecycleSequence = _getNekoIdleDesktopStateLifecycleSequence(detail);
        const targetAvailable = !(detail && detail.available === false);
        const lifecycleTerminal = !targetAvailable;
        if (_isNekoIdleDesktopStateStaleAgainst(
            sourceUpdatedAt,
            lifecycleSequence,
            lifecycleTerminal,
            _nekoIdleDesktopChatMinimizedState
        )) return;
        const screenRect = targetAvailable && detail && detail.minimized
            ? _normalizeNekoIdleScreenRect(detail.screenRect)
            : null;
        const nextMinimized = !!(targetAvailable && detail && detail.minimized && screenRect);
        if (_isAnyNekoIdleCat1PlaygroundDropLifecycleActive() &&
            _isNekoIdleCat1PlaygroundPairMoveFeedback(detail)) {
            return;
        }
        const compactSurfaceCurrentlyVisible = !!_getNekoIdleDesktopCompactSurfaceRect();
        if ((nextMinimized || !targetAvailable) &&
            _nekoIdleDesktopCompactSurfaceState &&
            _isNekoIdleDesktopStateStaleAgainst(
                sourceUpdatedAt,
                lifecycleSequence,
                lifecycleTerminal,
                _nekoIdleDesktopCompactSurfaceState
            )) {
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
            !!(targetAvailable && detail && !detail.minimized && !compactSurfaceCurrentlyVisible),
            lifecycleSequence,
            lifecycleTerminal
        );
        if (nextMinimized || !targetAvailable) {
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                false,
                null,
                receivedAt,
                sourceUpdatedAt,
                lifecycleSequence,
                lifecycleTerminal
            );
            _nekoIdleCompactSurfaceDragging = false;
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
        const lifecycleSequence = _getNekoIdleDesktopStateLifecycleSequence(detail);
        const targetAvailable = !(detail && detail.available === false);
        const lifecycleTerminal = !targetAvailable;
        if (_isNekoIdleDesktopStateStaleAgainst(
            sourceUpdatedAt,
            lifecycleSequence,
            lifecycleTerminal,
            _nekoIdleDesktopCompactSurfaceState
        )) return;
        const screenRect = targetAvailable && detail && detail.visible
            ? _normalizeNekoIdleScreenRect(detail.screenRect)
            : null;
        const heartbeat = !!(detail && detail.heartbeat);
        const nextVisible = !!(targetAvailable && detail && detail.visible && screenRect);
        if ((nextVisible || !targetAvailable) &&
            _nekoIdleDesktopChatMinimizedState &&
            _isNekoIdleDesktopStateStaleAgainst(
                sourceUpdatedAt,
                lifecycleSequence,
                lifecycleTerminal,
                _nekoIdleDesktopChatMinimizedState
            )) {
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
                sourceUpdatedAt,
                lifecycleSequence,
                lifecycleTerminal
            );
            if (nextVisible || !targetAvailable) {
                _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
                    false,
                    null,
                    receivedAt,
                    sourceUpdatedAt,
                    false,
                    lifecycleSequence,
                    lifecycleTerminal
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
                prevCompactSourceUpdatedAt,
                _nekoIdleDesktopCompactSurfaceState.lifecycleSequence,
                _nekoIdleDesktopCompactSurfaceState.lifecycleTerminal
            );
        } else if (nextVisible &&
            _nekoIdleDesktopChatMinimizedState &&
            !_nekoIdleDesktopChatMinimizedState.minimized) {
            // 首个 available:true 发布失败时，后续心跳可能是消费端收到的第一条
            // 恢复事实。仅此 catch-up 分支提升排序水位，避免迟到的恢复前消息
            // 再次清掉目标；普通可见心跳仍走上面的保序分支。
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                true,
                screenRect,
                receivedAt,
                sourceUpdatedAt,
                lifecycleSequence,
                lifecycleTerminal
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
