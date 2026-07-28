function _getNekoIdleReturnDragGrabOffset(point, rect, coordinateSpace = 'virtual') {
    const useLocalSpace = coordinateSpace === 'local';
    const pointX = Number(point && (useLocalSpace ? point.localX : point.virtualX));
    const pointY = Number(point && (useLocalSpace ? point.localY : point.virtualY));
    return {
        x: pointX - Number(rect && rect.left),
        y: pointY - Number(rect && rect.top)
    };
}

function _getNekoIdleReturnDragGlobalScreenPoint(screenPoint, cropState = null) {
    if (!screenPoint || typeof screenPoint !== 'object') return null;
    const readFinite = (value) => (
        value !== null && value !== undefined && Number.isFinite(Number(value))
            ? Number(value)
            : NaN
    );
    const cropBounds = cropState && cropState.cropBounds ? cropState.cropBounds : null;
    const cropX = readFinite(cropBounds && cropBounds.x);
    const cropY = readFinite(cropBounds && cropBounds.y);
    const localX = readFinite(screenPoint.x);
    const localY = readFinite(screenPoint.y);
    let screenX = readFinite(screenPoint.screenX);
    let screenY = readFinite(screenPoint.screenY);
    if (!Number.isFinite(screenX) && Number.isFinite(localX)) {
        screenX = Number.isFinite(cropX) ? localX + cropX : localX;
    }
    if (!Number.isFinite(screenY) && Number.isFinite(localY)) {
        screenY = Number.isFinite(cropY) ? localY + cropY : localY;
    }
    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return null;
    return { x: screenX, y: screenY };
}

function _canNekoIdleReturnDragUseGlobalCursor(runtime, electronScreen, cropCoordinateActive) {
    return !!(
        cropCoordinateActive &&
        runtime &&
        runtime.canReadGlobalCursorScreenPoint === true &&
        electronScreen &&
        typeof electronScreen.getCursorPoint === 'function'
    );
}

function _shouldNekoIdleReturnDragIgnoreMissingMouseButtons(runtime, pointerType, usesGlobalCursor) {
    return !!(
        pointerType === 'mouse' &&
        runtime &&
        runtime.isWayland === true &&
        !usesGlobalCursor
    );
}

function _getNekoIdleReturnDragContinuousVirtualPoint(
    previousPoint,
    absolutePoint,
    movementX,
    movementY,
    maxMovementPerEvent = 160,
    reconcileTolerance = 12
) {
    if (!absolutePoint || typeof absolutePoint !== 'object') return absolutePoint;
    const readFinite = (value) => (
        value !== null && value !== undefined && Number.isFinite(Number(value))
            ? Number(value)
            : NaN
    );
    const previousX = readFinite(previousPoint && previousPoint.virtualX);
    const previousY = readFinite(previousPoint && previousPoint.virtualY);
    const absoluteX = readFinite(absolutePoint.virtualX);
    const absoluteY = readFinite(absolutePoint.virtualY);
    if (!Number.isFinite(previousX) || !Number.isFinite(previousY)) {
        return absolutePoint;
    }

    const dx = readFinite(movementX);
    const dy = readFinite(movementY);
    const limit = Math.max(1, Number(maxMovementPerEvent) || 160);
    const tolerance = Math.max(0, Number(reconcileTolerance) || 0);
    let virtualX = previousX;
    let virtualY = previousY;
    let continuityBasis = 'hold';

    if (Number.isFinite(dx) && Number.isFinite(dy) &&
        Math.max(Math.abs(dx), Math.abs(dy)) <= limit) {
        const relativeX = previousX + dx;
        const relativeY = previousY + dy;
        const absoluteMatchesMovement = Number.isFinite(absoluteX) &&
            Number.isFinite(absoluteY) &&
            Math.max(
                Math.abs(absoluteX - relativeX),
                Math.abs(absoluteY - relativeY)
            ) <= tolerance;
        virtualX = absoluteMatchesMovement ? absoluteX : relativeX;
        virtualY = absoluteMatchesMovement ? absoluteY : relativeY;
        continuityBasis = absoluteMatchesMovement ? 'absolute-reconciled' : 'movement';
    } else if (Number.isFinite(absoluteX) && Number.isFinite(absoluteY) &&
        Math.max(
            Math.abs(absoluteX - previousX),
            Math.abs(absoluteY - previousY)
        ) <= limit) {
        virtualX = absoluteX;
        virtualY = absoluteY;
        continuityBasis = 'absolute-fallback';
    }

    return {
        ...absolutePoint,
        virtualX: virtualX,
        virtualY: virtualY,
        continuityBasis: continuityBasis
    };
}

Object.assign(AvatarButtonMixin.methods, {
    returnButton(ManagerPrototype, prefix, options) {
        ManagerPrototype.createReturnButton = function() {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;
            const currentTier = _readNekoAutoGoodbyeVisualTier();

            const returnButtonContainer = document.createElement('div');
            returnButtonContainer.id = opts.returnContainerId;
            returnButtonContainer.className = 'neko-idle-return-button-container';
            Object.assign(returnButtonContainer.style, {
                position: 'fixed',
                top: '0',
                left: '0',
                transform: 'none',
                zIndex: _NEKO_IDLE_RETURN_DEFAULT_Z_INDEX,
                pointerEvents: 'auto',
                display: 'none'
            });

            const returnBtn = document.createElement('div');
            returnBtn.id = opts.returnBtnId;
            returnBtn.className = `${opts.returnBtnClass} neko-idle-return-btn`;
            returnBtn.title = window.t ? window.t('buttons.return') : '请她回来';
            returnBtn.setAttribute('data-i18n-title', 'buttons.return');
            returnBtn.setAttribute('data-neko-idle-tier', currentTier);

            const returnArt = document.createElement('img');
            returnArt.className = 'neko-idle-return-art';
            returnArt.src = _getNekoIdleReturnAssetUrl(currentTier);
            returnArt.alt = window.t ? window.t('buttons.return') : '请她回来';
            returnArt.draggable = false;
            Object.assign(returnArt.style, {
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                pointerEvents: 'none',
                userSelect: 'none',
                display: 'block',
                transition: 'transform 0.18s ease, filter 0.18s ease, opacity 0.18s ease'
            });

            Object.assign(returnBtn.style, {
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                userSelect: 'none',
                pointerEvents: 'auto',
                position: 'relative'
            });

            returnBtn.addEventListener('mouseenter', (event) => {
                if (_isNekoIdleThoughtBubbleEventHit(returnBtn, event)) return;
                const tier = returnBtn.getAttribute('data-neko-idle-tier');
                if (tier && tier !== 'none') {
                    _playNekoIdleHoverArt(returnArt, tier, { userInitiated: true });
                }
            });

            returnBtn.addEventListener('mouseleave', () => {
                if (!returnArt.__nekoIdleHoverSrc) return;
                // The active thought bubble is a child of the return button. When its
                // pop animation disables pointer events, Chromium emits the button's
                // real mouseleave at the bubble coordinates. Reusing the bubble hit
                // guard here would suppress the only hover-completion signal.
                const tier = returnBtn.getAttribute('data-neko-idle-tier');
                if (tier && tier !== 'none') {
                    _finishNekoIdleHoverArtAfterPlayback(returnArt, tier);
                }
            });

            returnBtn.addEventListener('click', (e) => {
                if (_isNekoIdleThoughtBubbleEventHit(returnBtn, e)) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
                if (_handleNekoIdleCat1PlaygroundCatClick(returnBtn, e)) {
                    return;
                }
                if (
                    returnButtonContainer.getAttribute('data-dragging') === 'true' ||
                    returnButtonContainer.getAttribute('data-dragging') === 'pending' ||
                    returnButtonContainer.getAttribute('data-neko-return-click-suppressed') === 'true' ||
                    returnButtonContainer.getAttribute('data-neko-model-cat-transitioning') === 'cat-to-model' ||
                    (typeof window.isNekoModelCatTransitionActive === 'function' && window.isNekoModelCatTransitionActive())
                ) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
                e.stopPropagation();
                _clearNekoIdleCat1QuestionMark(returnBtn);
                _cancelNekoIdleCat1EatAction(returnBtn, { restoreArt: false });
                _cancelNekoIdleCat1StretchAction(returnBtn, { restoreArt: false });
                _cancelNekoIdleCat1PlayAction(returnBtn, { restoreArt: false });
                _finishNekoIdleReturnDragAction(returnBtn, { restoreArt: false });
                _cancelNekoIdleCat1Journey(returnBtn);
                _dispatchNekoIdleReturnClickFromButton(returnBtn);
            });

            const thoughtBubble = document.createElement('span');
            thoughtBubble.className = 'neko-idle-thought-bubble';
            thoughtBubble.setAttribute('role', 'button');
            thoughtBubble.setAttribute('tabindex', '-1');
            const thoughtBubbleAriaLabel = typeof window.t === 'function'
                ? window.t('buttons.thoughtBubblePop')
                : 'Pop thought bubble';
            thoughtBubble.setAttribute('aria-label', thoughtBubbleAriaLabel);
            thoughtBubble.setAttribute('data-i18n-aria', 'buttons.thoughtBubblePop');
            Object.assign(thoughtBubble.style, {
                position: 'absolute',
                userSelect: 'none'
            });
            const stopThoughtBubblePointerStart = (event) => {
                event.preventDefault();
                event.stopPropagation();
            };
            thoughtBubble.addEventListener('mousedown', stopThoughtBubblePointerStart);
            thoughtBubble.addEventListener('touchstart', stopThoughtBubblePointerStart, { passive: false });
            thoughtBubble.addEventListener('touchend', (event) => {
                _handleNekoIdleThoughtBubbleClick(returnBtn, event);
            }, { passive: false });
            thoughtBubble.addEventListener('click', (event) => {
                _handleNekoIdleThoughtBubbleClick(returnBtn, event);
            });
            thoughtBubble.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                _handleNekoIdleThoughtBubbleClick(returnBtn, event);
            });

            const thoughtBubbleBg = document.createElement('img');
            thoughtBubbleBg.className = 'neko-idle-thought-bubble-bg';
            thoughtBubbleBg.src = _getNekoIdleThoughtBubbleBgAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL);
            thoughtBubbleBg.alt = '';
            thoughtBubbleBg.draggable = false;

            const thoughtBubbleItem = document.createElement('img');
            thoughtBubbleItem.className = 'neko-idle-thought-bubble-item';
            thoughtBubbleItem.src = _getNekoIdleThoughtBubbleItemAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS[0]);
            thoughtBubbleItem.alt = '';
            thoughtBubbleItem.draggable = false;

            thoughtBubble.appendChild(thoughtBubbleBg);
            thoughtBubble.appendChild(thoughtBubbleItem);

            returnBtn.appendChild(returnArt);
            returnBtn.appendChild(thoughtBubble);
            returnButtonContainer.appendChild(returnBtn);
            document.body.appendChild(returnButtonContainer);
            this._returnButtonContainer = returnButtonContainer;
            _applyNekoIdleReturnPresentation(returnBtn, currentTier);
            if (!window.__NEKO_MULTI_WINDOW__ || _isNekoNativeReturnBallDragDisabled()) {
                this._setupReturnButtonDrag(returnButtonContainer);
            }

            return returnButtonContainer;
        };

        /**
         * 设置返回按钮拖拽功能
         */
        ManagerPrototype._setupReturnButtonDrag = function(container) {
            let isDragging = false;
            let dragActiveDispatched = false;
            let dragSafetyTimer = 0;
            let dragSafetyToken = 0;
            let dragPointerType = '';
            let dragStartX = 0, dragStartY = 0, containerStartX = 0, containerStartY = 0;
            let dragStartVirtualX = 0, dragStartVirtualY = 0;
            let dragGrabOffsetX = 0, dragGrabOffsetY = 0;
            let dragVisualWidth = 64, dragVisualHeight = 64;
            let dragCursorPollFrame = 0;
            let dragCursorPollInFlight = false;
            let dragCursorPollStopped = true;
            let dragCursorPollToken = 0;
            let dragActivity = null;
            let dragCropHoldPending = false;
            let dragPendingPoint = null;
            let dragReleasePending = false;
            let dragReleaseTimer = 0;
            let dragUsesGlobalCursor = false;
            let dragContinuousVirtualPoint = null;

            const getDragCropState = () => {
                try {
                    const cropApi = window.__nekoNiriPetPhysicalCrop;
                    return cropApi && typeof cropApi.getState === 'function'
                        ? cropApi.getState()
                        : null;
                } catch (_) {
                    return null;
                }
            };

            const getDragCropOffset = () => {
                const state = getDragCropState();
                let offsetX = Number(state && state.offsetX);
                let offsetY = Number(state && state.offsetY);
                if (!Number.isFinite(offsetX) || !Number.isFinite(offsetY)) {
                    try {
                        const rootStyle = document.documentElement && document.documentElement.style;
                        offsetX = Number.parseFloat(rootStyle && rootStyle.getPropertyValue('--neko-niri-pet-crop-offset-x'));
                        offsetY = Number.parseFloat(rootStyle && rootStyle.getPropertyValue('--neko-niri-pet-crop-offset-y'));
                    } catch (_) {}
                }
                return {
                    x: Number.isFinite(offsetX) ? offsetX : 0,
                    y: Number.isFinite(offsetY) ? offsetY : 0
                };
            };

            const getDragVirtualOrigin = () => {
                const state = getDragCropState();
                const virtualBounds = state && state.virtualBounds ? state.virtualBounds : null;
                const x = Number(virtualBounds && virtualBounds.x);
                const y = Number(virtualBounds && virtualBounds.y);
                return {
                    x: Number.isFinite(x) ? x : 0,
                    y: Number.isFinite(y) ? y : 0
                };
            };

            const isDragNiriCropCoordinateActive = () => {
                const state = getDragCropState();
                if (state && state.enabled) return true;
                try {
                    return !!(document.documentElement &&
                        document.documentElement.classList.contains('neko-niri-pet-physical-crop'));
                } catch (_) {
                    return false;
                }
            };

            const isNiriReturnBallFullCropReady = (
                state,
                requireVerified = false,
                expectedDragSessionId = 0
            ) => {
                if (!state || state.enabled !== true) return false;
                if (requireVerified && state.geometryVerified !== true) return false;
                if (state.geometryVerified === false) return false;
                const expectedSession = Math.max(0, Math.round(Number(expectedDragSessionId) || 0));
                const stateSession = Math.max(0, Math.round(Number(state.dragSessionId) || 0));
                if (expectedSession && stateSession !== expectedSession) return false;
                const cropBounds = state.cropBounds;
                const virtualBounds = state.virtualBounds;
                if (!cropBounds || !virtualBounds) return false;
                const close = (a, b) => (
                    Number.isFinite(Number(a)) &&
                    Number.isFinite(Number(b)) &&
                    Math.abs(Number(a) - Number(b)) <= 2
                );
                return close(cropBounds.x, virtualBounds.x) &&
                    close(cropBounds.y, virtualBounds.y) &&
                    close(cropBounds.width, virtualBounds.width) &&
                    close(cropBounds.height, virtualBounds.height);
            };

            const clearDragCropHoldPending = () => {
                dragCropHoldPending = false;
                dragPendingPoint = null;
            };

            const clearDragReleasePending = () => {
                dragReleasePending = false;
                if (dragReleaseTimer) {
                    clearTimeout(dragReleaseTimer);
                    dragReleaseTimer = 0;
                }
            };

            const getDragPoint = (sourceEvent, fallbackX, fallbackY) => {
                if (!isDragNiriCropCoordinateActive()) {
                    const localX = Number(fallbackX);
                    const localY = Number(fallbackY);
                    return {
                        x: localX,
                        y: localY,
                        localX: localX,
                        localY: localY,
                        virtualX: localX,
                        virtualY: localY,
                        offsetX: 0,
                        offsetY: 0
                    };
                }
                const offset = getDragCropOffset();
                let localX = Number(fallbackX);
                let localY = Number(fallbackY);
                let virtualX = Number.isFinite(localX) ? localX + offset.x : NaN;
                let virtualY = Number.isFinite(localY) ? localY + offset.y : NaN;
                try {
                    const cropApi = window.__nekoNiriPetPhysicalCrop;
                    const coords = cropApi && sourceEvent && typeof cropApi.getEventCoordinates === 'function'
                        ? cropApi.getEventCoordinates(sourceEvent)
                        : null;
                    const nextLocalX = Number(coords && coords.local && coords.local.x);
                    const nextLocalY = Number(coords && coords.local && coords.local.y);
                    const nextVirtualX = Number(coords && coords.virtual && coords.virtual.x);
                    const nextVirtualY = Number(coords && coords.virtual && coords.virtual.y);
                    if (Number.isFinite(nextLocalX) && Number.isFinite(nextLocalY)) {
                        localX = nextLocalX;
                        localY = nextLocalY;
                    }
                    if (Number.isFinite(nextVirtualX) && Number.isFinite(nextVirtualY)) {
                        virtualX = nextVirtualX;
                        virtualY = nextVirtualY;
                    }
                } catch (_) {}
                if ((!Number.isFinite(virtualX) || !Number.isFinite(virtualY)) &&
                    Number.isFinite(localX) && Number.isFinite(localY)) {
                    virtualX = localX + offset.x;
                    virtualY = localY + offset.y;
                }
                if ((!Number.isFinite(localX) || !Number.isFinite(localY)) &&
                    Number.isFinite(virtualX) && Number.isFinite(virtualY)) {
                    localX = virtualX - offset.x;
                    localY = virtualY - offset.y;
                }
                return {
                    x: localX,
                    y: localY,
                    localX: localX,
                    localY: localY,
                    virtualX: virtualX,
                    virtualY: virtualY,
                    offsetX: offset.x,
                    offsetY: offset.y
                };
            };

            const getDragContainerVirtualRect = () => {
                const rect = container.getBoundingClientRect && container.getBoundingClientRect();
                if (!isDragNiriCropCoordinateActive()) {
                    if (!rect) {
                        const left = Number.parseFloat(container.style.left);
                        const top = Number.parseFloat(container.style.top);
                        return {
                            left: Number.isFinite(left) ? left : 0,
                            top: Number.isFinite(top) ? top : 0,
                            width: container.offsetWidth || 64,
                            height: container.offsetHeight || 64
                        };
                    }
                    return {
                        left: Number(rect.left),
                        top: Number(rect.top),
                        width: Number(rect.width) || container.offsetWidth || 64,
                        height: Number(rect.height) || container.offsetHeight || 64
                    };
                }
                const offset = getDragCropOffset();
                if (!rect) {
                    const left = Number.parseFloat(container.style.left);
                    const top = Number.parseFloat(container.style.top);
                    return {
                        // style.left/top live in the virtual body coordinate system.
                        // Only DOMRect is local to the physically cropped viewport.
                        left: Number.isFinite(left) ? left : 0,
                        top: Number.isFinite(top) ? top : 0,
                        width: container.offsetWidth || 64,
                        height: container.offsetHeight || 64
                    };
                }
                return {
                    left: Number(rect.left) + offset.x,
                    top: Number(rect.top) + offset.y,
                    width: Number(rect.width) || container.offsetWidth || 64,
                    height: Number(rect.height) || container.offsetHeight || 64
                };
            };

            const getDragScreenPointFromVirtualPoint = (virtualX, virtualY, sourceEvent = null, fallbackX = virtualX, fallbackY = virtualY) => {
                if (!isDragNiriCropCoordinateActive()) {
                    return {
                        x: sourceEvent && Number.isFinite(sourceEvent.screenX) ? sourceEvent.screenX : Number(fallbackX),
                        y: sourceEvent && Number.isFinite(sourceEvent.screenY) ? sourceEvent.screenY : Number(fallbackY)
                    };
                }
                const origin = getDragVirtualOrigin();
                return {
                    x: Number(virtualX) + origin.x,
                    y: Number(virtualY) + origin.y
                };
            };

            const getDragPointFromScreenPoint = (screenPoint) => {
                if (!screenPoint || !isDragNiriCropCoordinateActive()) return null;
                const cropState = getDragCropState();
                const globalPoint = _getNekoIdleReturnDragGlobalScreenPoint(screenPoint, cropState);
                if (!globalPoint) return null;
                const screenX = globalPoint.x;
                const screenY = globalPoint.y;
                const origin = getDragVirtualOrigin();
                const offset = getDragCropOffset();
                const virtualX = screenX - origin.x;
                const virtualY = screenY - origin.y;
                return buildDragPointSnapshot(
                    virtualX - offset.x,
                    virtualY - offset.y,
                    virtualX,
                    virtualY
                );
            };

            const canPollNiriDragCursor = () => {
                return _canNekoIdleReturnDragUseGlobalCursor(
                    window.__NEKO_DESKTOP_RUNTIME__,
                    window.electronScreen,
                    isDragNiriCropCoordinateActive()
                );
            };

            const shouldUseGlobalCursorForMouseDrag = () => {
                return dragUsesGlobalCursor;
            };

            const shouldIgnoreMissingMouseButtons = () => {
                return _shouldNekoIdleReturnDragIgnoreMissingMouseButtons(
                    window.__NEKO_DESKTOP_RUNTIME__,
                    dragPointerType,
                    shouldUseGlobalCursorForMouseDrag()
                );
            };

            const getContinuousDomMouseDragPoint = (point, sourceEvent) => {
                if (!isUsableDragPoint(point)) return point;
                if (!shouldIgnoreMissingMouseButtons()) {
                    dragContinuousVirtualPoint = {
                        virtualX: point.virtualX,
                        virtualY: point.virtualY
                    };
                    return point;
                }
                const continuousPoint = _getNekoIdleReturnDragContinuousVirtualPoint(
                    dragContinuousVirtualPoint,
                    point,
                    sourceEvent && sourceEvent.movementX,
                    sourceEvent && sourceEvent.movementY
                );
                if (isUsableDragPoint(continuousPoint)) {
                    dragContinuousVirtualPoint = {
                        virtualX: continuousPoint.virtualX,
                        virtualY: continuousPoint.virtualY
                    };
                }
                return continuousPoint;
            };

            const stopDragCursorPolling = () => {
                dragCursorPollStopped = true;
                dragCursorPollInFlight = false;
                dragCursorPollToken += 1;
                if (dragCursorPollFrame) {
                    cancelAnimationFrame(dragCursorPollFrame);
                    dragCursorPollFrame = 0;
                }
            };

            const clearDragSafetyTimer = () => {
                if (!dragSafetyTimer) return;
                clearTimeout(dragSafetyTimer);
                dragSafetyTimer = 0;
            };

            const setReturnClickSuppressed = (suppressed) => {
                if (suppressed) {
                    container.setAttribute('data-neko-return-click-suppressed', 'true');
                } else {
                    container.removeAttribute('data-neko-return-click-suppressed');
                }
            };

            const startDragActivity = (safetyToken, left, top) => {
                const startedAt = Date.now();
                dragActivity = {
                    activityId: `return-cat-drag-dom:${startedAt}:${safetyToken}`,
                    safetyToken: safetyToken,
                    startedAt: startedAt,
                    startX: left,
                    startY: top,
                    lastX: left,
                    lastY: top,
                    pathDistancePx: 0,
                    terminalReported: false
                };
            };

            const recordDragActivityPoint = (left, top) => {
                if (!dragActivity || dragActivity.terminalReported ||
                    !Number.isFinite(left) || !Number.isFinite(top)) {
                    return;
                }
                if (Number.isFinite(dragActivity.lastX) && Number.isFinite(dragActivity.lastY)) {
                    dragActivity.pathDistancePx += Math.hypot(
                        left - dragActivity.lastX,
                        top - dragActivity.lastY
                    );
                }
                dragActivity.lastX = left;
                dragActivity.lastY = top;
            };

            const finishDragActivity = (safetyToken) => {
                if (!dragActivity || dragActivity.safetyToken !== safetyToken || dragActivity.terminalReported) {
                    return null;
                }
                dragActivity.terminalReported = true;
                return {
                    activityId: dragActivity.activityId,
                    pathDistancePx: Math.max(0, dragActivity.pathDistancePx),
                    displacementPx: Math.hypot(
                        dragActivity.lastX - dragActivity.startX,
                        dragActivity.lastY - dragActivity.startY
                    ),
                    durationMs: Math.max(0, Date.now() - dragActivity.startedAt)
                };
            };

            const finishDragState = (moved, safetyToken, suppressClick = moved) => {
                if (safetyToken !== dragSafetyToken) return;
                dragUsesGlobalCursor = false;
                clearDragReleasePending();
                clearDragCropHoldPending();
                const dragActivityFacts = finishDragActivity(safetyToken);
                if (!dragActivityFacts) return;
                if (moved) {
                    const finalLeft = parseFloat(container.style.left);
                    const finalTop = parseFloat(container.style.top);
                    const virtualViewport = _getNekoDesktopVirtualViewportSize();
                    _applyNekoIdleCat1EdgePeekAfterDrag(
                        container,
                        Number.isFinite(finalLeft) ? finalLeft : containerStartX,
                        Number.isFinite(finalTop) ? finalTop : containerStartY,
                        virtualViewport.width,
                        virtualViewport.height
                    );
                }
                container.setAttribute('data-dragging', 'false');
                if (moved) {
                    const dispatchLeft = parseFloat(container.style.left);
                    const dispatchTop = parseFloat(container.style.top);
                    _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-end', {
                        dragSessionId: safetyToken,
                        movedDistancePx: Math.hypot(
                            (Number.isFinite(dispatchLeft) ? dispatchLeft : containerStartX) - containerStartX,
                            (Number.isFinite(dispatchTop) ? dispatchTop : containerStartY) - containerStartY
                        ),
                        ...dragActivityFacts
                    });
                } else {
                    _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-cancel', {
                        dragSessionId: safetyToken,
                        movedDistancePx: 0,
                        dragCancelled: true,
                        ...dragActivityFacts
                    });
                }
                if (suppressClick) {
                    setTimeout(() => setReturnClickSuppressed(false), 120);
                } else {
                    setReturnClickSuppressed(false);
                }
            };

            const resetDragStateAfterMissingEnd = (safetyToken) => {
                if (dragSafetyToken !== safetyToken || !isDragging) return;
                const moved = container.getAttribute('data-dragging') === 'true';
                if (moved && !dragCropHoldPending) return;
                isDragging = false;
                dragActiveDispatched = false;
                dragPointerType = '';
                container.style.cursor = 'grab';
                finishDragState(false, safetyToken, moved);
            };

            const cancelDragState = () => {
                clearDragSafetyTimer();
                stopDragCursorPolling();
                if (!isDragging) return;
                const safetyToken = dragSafetyToken;
                const movedPastThreshold = container.getAttribute('data-dragging') === 'true';
                isDragging = false;
                dragActiveDispatched = false;
                dragPointerType = '';
                dragUsesGlobalCursor = false;
                clearDragReleasePending();
                clearDragCropHoldPending();
                container.style.cursor = 'grab';
                finishDragState(false, safetyToken, movedPastThreshold);
            };

            const buildDragPointSnapshot = (localX, localY, virtualX, virtualY) => ({
                x: localX,
                y: localY,
                localX: localX,
                localY: localY,
                virtualX: virtualX,
                virtualY: virtualY
            });

            const isUsableDragPoint = (point) => {
                return !!(point &&
                    Number.isFinite(point.localX) &&
                    Number.isFinite(point.localY) &&
                    Number.isFinite(point.virtualX) &&
                    Number.isFinite(point.virtualY));
            };

            const handleMove = (clientX, clientY, sourceEvent = null, movePoint = null) => {
                if (!isDragging) return;
                const point = movePoint || getDragPoint(sourceEvent, clientX, clientY);
                if (!isUsableDragPoint(point)) return;
                const deltaX = point.virtualX - dragStartVirtualX;
                const deltaY = point.virtualY - dragStartVirtualY;
                const w = dragVisualWidth;
                const h = dragVisualHeight;
                const virtualViewport = _getNekoDesktopVirtualViewportSize();
                const nextVirtualLeft = Math.max(
                    0,
                    Math.min(point.virtualX - dragGrabOffsetX, virtualViewport.width - w)
                );
                const nextVirtualTop = Math.max(
                    0,
                    Math.min(point.virtualY - dragGrabOffsetY, virtualViewport.height - h)
                );
                const movedPastThreshold = Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5;
                if (!movedPastThreshold) return;
                const screenPoint = getDragScreenPointFromVirtualPoint(nextVirtualLeft + w / 2, nextVirtualTop + h / 2, sourceEvent, clientX, clientY);
                container.setAttribute('data-dragging', 'true');
                if (!dragActiveDispatched) {
                    dragActiveDispatched = true;
                    _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-active', {
                        dragSessionId: dragSafetyToken
                    });
                }
                _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-motion', {
                    dragSessionId: dragSafetyToken,
                    clientX: point.localX,
                    clientY: point.localY,
                    screenX: Number.isFinite(screenPoint.x) ? screenPoint.x : (sourceEvent && Number.isFinite(sourceEvent.screenX) ? sourceEvent.screenX : clientX),
                    screenY: Number.isFinite(screenPoint.y) ? screenPoint.y : (sourceEvent && Number.isFinite(sourceEvent.screenY) ? sourceEvent.screenY : clientY),
                    deltaX: deltaX,
                    deltaY: deltaY,
                    timestamp: Date.now()
                });
                if (isDragNiriCropCoordinateActive() &&
                    !isNiriReturnBallFullCropReady(
                        getDragCropState(),
                        true,
                        dragSafetyToken
                    )) {
                    // Niri moves and resizes the transparent carrier asynchronously.
                    // Keep only the latest virtual cursor point until the compositor
                    // confirms both operations, otherwise the old body crop offset
                    // is applied to an already-virtual kitten position.
                    dragCropHoldPending = true;
                    dragPendingPoint = buildDragPointSnapshot(
                        point.localX,
                        point.localY,
                        point.virtualX,
                        point.virtualY
                    );
                    return;
                }
                clearDragCropHoldPending();
                recordDragActivityPoint(nextVirtualLeft, nextVirtualTop);
                // The cropped body already translates virtual coordinates by
                // -cropOffset. Subtracting the offset here would apply it twice
                // and make the kitten jump away from the pointer.
                container.style.left = `${nextVirtualLeft}px`;
                container.style.top = `${nextVirtualTop}px`;
            };

            const scheduleDragCursorPollFrame = () => {
                if (dragCursorPollStopped || dragCursorPollFrame || !isDragging) return;
                const pollToken = dragCursorPollToken;
                dragCursorPollFrame = requestAnimationFrame(() => {
                    dragCursorPollFrame = 0;
                    if (pollToken !== dragCursorPollToken ||
                        dragCursorPollStopped || !isDragging || !canPollNiriDragCursor()) {
                        if (!isDragging) stopDragCursorPolling();
                        return;
                    }
                    if (!dragCursorPollInFlight) {
                        dragCursorPollInFlight = true;
                        Promise.resolve()
                            .then(() => window.electronScreen.getCursorPoint())
                            .then((screenPoint) => {
                                dragCursorPollInFlight = false;
                                if (pollToken !== dragCursorPollToken || dragCursorPollStopped || !isDragging) return;
                                const point = getDragPointFromScreenPoint(screenPoint);
                                if (isUsableDragPoint(point)) {
                                    handleMove(point.localX, point.localY, null, point);
                                }
                                scheduleDragCursorPollFrame();
                            })
                            .catch(() => {
                                dragCursorPollInFlight = false;
                                if (pollToken !== dragCursorPollToken) return;
                                scheduleDragCursorPollFrame();
                            });
                    }
                    scheduleDragCursorPollFrame();
                });
            };

            const startDragCursorPolling = () => {
                if (!canPollNiriDragCursor()) return;
                dragCursorPollToken += 1;
                dragCursorPollStopped = false;
                scheduleDragCursorPollFrame();
            };

            const handleStart = (clientX, clientY, pointerType = 'mouse', sourceEvent = null, startPoint = null) => {
                if (isDragging) return;
                const button = _getNekoIdleReturnButtonFromContainer(container);
                if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
                clearDragSafetyTimer();
                stopDragCursorPolling();
                clearDragReleasePending();
                clearDragCropHoldPending();
                setReturnClickSuppressed(true);
                const point = startPoint || getDragPoint(sourceEvent, clientX, clientY);
                if (!isUsableDragPoint(point)) return;
                const rect = getDragContainerVirtualRect();
                const localRect = container.getBoundingClientRect && container.getBoundingClientRect();
                dragVisualWidth = Math.max(1, Number(container.offsetWidth) || Number(rect.width) || 64);
                dragVisualHeight = Math.max(1, Number(container.offsetHeight) || Number(rect.height) || 64);
                dragUsesGlobalCursor = pointerType === 'mouse' && canPollNiriDragCursor();
                const useLocalGrabAnchor = dragUsesGlobalCursor && localRect;
                const grabOffset = _getNekoIdleReturnDragGrabOffset(
                    point,
                    useLocalGrabAnchor ? localRect : rect,
                    useLocalGrabAnchor ? 'local' : 'virtual'
                );
                const safetyToken = dragSafetyToken + 1;
                startDragActivity(safetyToken, rect.left, rect.top);
                _restoreNekoIdleCat1EdgePeekBeforeDrag(container);
                _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-start', {
                    dragSessionId: safetyToken
                });
                isDragging = true;
                dragActiveDispatched = false;
                dragPointerType = pointerType;
                dragStartX = point.localX;
                dragStartY = point.localY;
                dragStartVirtualX = point.virtualX;
                dragStartVirtualY = point.virtualY;
                dragContinuousVirtualPoint = {
                    virtualX: point.virtualX,
                    virtualY: point.virtualY
                };
                containerStartX = rect.left;
                containerStartY = rect.top;
                // Keep the exact visible grab point under the cursor. The return
                // container can be larger than the kitten artwork because it also
                // carries transparent interaction/crop padding, so centring the
                // whole container creates a stable but visibly wrong offset.
                dragGrabOffsetX = grabOffset.x;
                dragGrabOffsetY = grabOffset.y;
                container.style.transform = 'none';
                container.style.right = '';
                container.style.bottom = '';
                container.style.left = `${containerStartX}px`;
                container.style.top = `${containerStartY}px`;
                container.setAttribute('data-dragging', 'pending');
                container.style.cursor = 'grabbing';
                dragSafetyToken = safetyToken;
                dragSafetyTimer = setTimeout(() => {
                    dragSafetyTimer = 0;
                    resetDragStateAfterMissingEnd(safetyToken);
                }, 5000);
                startDragCursorPolling();
            };

            const handleEnd = () => {
                clearDragSafetyTimer();
                stopDragCursorPolling();
                if (!isDragging || dragReleasePending) return;
                const safetyToken = dragSafetyToken;
                const movedPastThreshold = container.getAttribute('data-dragging') === 'true';
                if (movedPastThreshold && dragCropHoldPending) {
                    // The pointer can be released before Niri confirms the full
                    // carrier. Keep this drag session alive until the verified
                    // crop event flushes the last queued virtual point. Otherwise
                    // a real fast drag becomes a click and immediately restores
                    // the model.
                    dragReleasePending = true;
                    dragPointerType = '';
                    container.style.cursor = 'grab';
                    dragReleaseTimer = setTimeout(() => {
                        dragReleaseTimer = 0;
                        if (!dragReleasePending || safetyToken !== dragSafetyToken) return;
                        dragReleasePending = false;
                        isDragging = false;
                        dragActiveDispatched = false;
                        clearDragCropHoldPending();
                        finishDragState(false, safetyToken, true);
                    }, 600);
                    return;
                }
                isDragging = false;
                dragActiveDispatched = false;
                dragPointerType = '';
                dragUsesGlobalCursor = false;
                clearDragCropHoldPending();
                container.style.cursor = 'grab';
                if (movedPastThreshold) {
                    // Let the final virtual position paint while the full carrier
                    // is still active. Releasing the crop hold in the same frame
                    // can make shape collection observe the previous kitten rect.
                    requestAnimationFrame(() => {
                        requestAnimationFrame(() => {
                            finishDragState(true, safetyToken);
                        });
                    });
                } else {
                    finishDragState(false, safetyToken);
                }
            };

            container.addEventListener('mousedown', (e) => {
                if (e.button !== 0) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    return;
                }
                const button = _getNekoIdleReturnButtonFromContainer(container);
                if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
                if (_isNekoIdleThoughtBubbleEventHit(container.querySelector('.neko-idle-return-btn'), e)) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
                if (container.contains(e.target)) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    const point = getDragPoint(e, e.clientX, e.clientY);
                    handleStart(point.x, point.y, 'mouse', e, point);
                }
            });

            this._returnButtonDragHandlers = {
                mouseMove: (e) => {
                    // document 级 handler：非拖拽期直接返回，避免全页面鼠标移动白算坐标
                    if (!isDragging) return;
                    // Only one coordinate source may move the kitten. Native
                    // Wayland cannot read a global cursor point, so DOM movement
                    // remains authoritative there. Chromium can temporarily
                    // report buttons === 0 while Niri expands the transparent
                    // carrier; the real mouseup listener owns termination.
                    if (shouldUseGlobalCursorForMouseDrag()) return;
                    if (dragPointerType === 'mouse' &&
                        e.buttons === 0 &&
                        !shouldIgnoreMissingMouseButtons()) {
                        handleEnd();
                        return;
                    }
                    const rawPoint = getDragPoint(e, e.clientX, e.clientY);
                    const point = getContinuousDomMouseDragPoint(rawPoint, e);
                    handleMove(point.x, point.y, e, point);
                },
                mouseUp: handleEnd,
                touchMove: (e) => {
                    if (isDragging && e.touches && e.touches[0]) {
                        e.preventDefault();
                        const point = getDragPoint(e.touches[0], e.touches[0].clientX, e.touches[0].clientY);
                        handleMove(point.x, point.y, e.touches[0]);
                    }
                },
                touchEnd: handleEnd,
                touchCancel: cancelDragState,
                windowBlur: () => {
                    // Niri can briefly blur the compact Pet window while applying
                    // the full drag carrier. Once movement crossed the threshold,
                    // keep the verified drag session alive; mouseup, visibility
                    // and the existing safety paths still provide termination.
                    if (isDragging &&
                        (shouldUseGlobalCursorForMouseDrag() ||
                            (dragActiveDispatched && shouldIgnoreMissingMouseButtons()))) {
                        return;
                    }
                    cancelDragState();
                },
                visibilityChange: () => {
                    if (document.hidden) cancelDragState();
                },
                cropStateApplied: (event) => {
                    if (!isDragging || !dragActiveDispatched || !dragCropHoldPending) return;
                    const detail = event && event.detail;
                    if (!isNiriReturnBallFullCropReady(detail, true, dragSafetyToken)) return;
                    const expectedSafetyToken = dragSafetyToken;
                    const flushPoint = (point) => {
                        if (!isDragging || expectedSafetyToken !== dragSafetyToken) return;
                        if (!isNiriReturnBallFullCropReady(getDragCropState(), true, expectedSafetyToken)) return;
                        clearDragCropHoldPending();
                        if (!isUsableDragPoint(point)) return;
                        handleMove(point.localX, point.localY, null, point);
                        if (dragReleasePending && !dragCropHoldPending) {
                            const safetyToken = dragSafetyToken;
                            clearDragReleasePending();
                            isDragging = false;
                            dragActiveDispatched = false;
                            dragPointerType = '';
                            dragUsesGlobalCursor = false;
                            container.style.cursor = 'grab';
                            requestAnimationFrame(() => {
                                requestAnimationFrame(() => {
                                    finishDragState(true, safetyToken);
                                });
                            });
                        }
                    };
                    const fallbackPoint = dragPendingPoint;
                    if (shouldUseGlobalCursorForMouseDrag() &&
                        window.electronScreen &&
                        typeof window.electronScreen.getCursorPoint === 'function') {
                        Promise.resolve(window.electronScreen.getCursorPoint())
                            .then((screenPoint) => {
                                const point = getDragPointFromScreenPoint(screenPoint);
                                flushPoint(isUsableDragPoint(point) ? point : fallbackPoint);
                            })
                            .catch(() => flushPoint(fallbackPoint));
                        return;
                    }
                    flushPoint(fallbackPoint);
                }
            };

            document.addEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
            document.addEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
            container.addEventListener('touchstart', (e) => {
                const button = _getNekoIdleReturnButtonFromContainer(container);
                if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return;
                if (_isNekoIdleThoughtBubbleEventHit(container.querySelector('.neko-idle-return-btn'), e.touches && e.touches[0])) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
                if (container.contains(e.target) && e.touches && e.touches[0]) {
                    const point = getDragPoint(e.touches[0], e.touches[0].clientX, e.touches[0].clientY);
                    handleStart(point.x, point.y, 'touch', e.touches[0], point);
                }
            }, { passive: false });
            document.addEventListener('touchmove', this._returnButtonDragHandlers.touchMove, { passive: false });
            document.addEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
            document.addEventListener('touchcancel', this._returnButtonDragHandlers.touchCancel);
            window.addEventListener('blur', this._returnButtonDragHandlers.windowBlur);
            window.addEventListener(
                'neko:niri-pet-physical-crop-state-applied',
                this._returnButtonDragHandlers.cropStateApplied
            );
            document.addEventListener('visibilitychange', this._returnButtonDragHandlers.visibilityChange);
            container.style.cursor = 'grab';
        };

        /**
         * 添加返回按钮呼吸灯动画
         */
        ManagerPrototype._addReturnButtonBreathingAnimation = function() {
            // No-op: breathing animation removed, images provide visual identity.
        };

        /**
         * 创建麦克风静音按钮（附加在麦克风按钮左侧）
         * @param {HTMLElement} btnWrapper - 麦克风按钮的包装器
         * @returns {Object|null} 静音按钮数据，包含 button, updateVisibility 等
         */
    }
});
