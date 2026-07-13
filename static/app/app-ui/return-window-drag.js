/**
 * app-ui/return-window-drag.js
 * UI display helpers extracted from app.js.
 *
 * Exposed as window.appUi.
 * Dependencies:
 * - window.appState (S) - shared mutable state
 * - window.appConst (C) - frozen constants
 * - window.appUtils - utility helpers
 * - window.t / window.safeT - i18n
 * - window.lanlan_config - character config
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.appUi = window.appUi || {};
    const I = window.__appUiParts || (window.__appUiParts = {});
    I.ensureMultiWindowReturnBallDrag = function ensureMultiWindowReturnBallDrag(container) {
        if (!window.__NEKO_MULTI_WINDOW__ || I.isNativeReturnBallDragDisabled() || !window.nekoPetDrag || !container) {
            I.cleanupMultiWindowReturnBallDrag();
            return;
        }

        if (I.multiWindowReturnBallDragState &&
            I.multiWindowReturnBallDragState.container === container &&
            container.isConnected) {
            return;
        }

        I.cleanupMultiWindowReturnBallDrag();

        const CLICK_THRESHOLD = 5;
        const state = {
            container,
            isDragging: false,
            hasMoved: false,
            startScreenX: 0,
            startScreenY: 0,
            releaseScreenX: 0,
            releaseScreenY: 0,
            savedWindowW: 0,
            savedWindowH: 0,
            savedBallStyle: null,
            savedBallWidth: 64,
            savedBallHeight: 64,
            niriPhysicalCropDrag: false,
            viewportWaitOnResize: null,
            viewportWaitFallbackTimer: null,
            transitionCleanupTimer: null,
            dragSessionToken: 0,
            dragRecoveryTimer: null,
            lastPointerEventAt: 0,
            suppressDomClickTimer: null,
            handleClick: null,
            handleMouseDown: null,
            handleMouseMove: null,
            handleMouseUp: null,
            handlePointerMove: null,
            handlePointerUp: null,
            handlePointerCancel: null,
            handleTouchStart: null,
            handleTouchMove: null,
            handleTouchEnd: null,
            handleWindowBlur: null,
            handlePageHide: null,
            handleVisibilityChange: null,
        };

        function getTouchScreenPoint(touch) {
            if (!touch) return null;
            return {
                x: typeof touch.screenX === 'number' ? touch.screenX : window.screenX + touch.clientX,
                y: typeof touch.screenY === 'number' ? touch.screenY : window.screenY + touch.clientY,
            };
        }

        function restoreSavedBallStyle() {
            I.restoreSavedReturnBallStyle(container, state);
        }

        function dispatchReturnBallRevealFailed(reason, error) {
            I.scheduleIdleReturnBallDesktopBridge('return-ball-reveal-failed', container);
            window.dispatchEvent(new CustomEvent('neko:return-ball-reveal-failed', {
                detail: {
                    reason: reason || 'unknown',
                    container: container,
                    errorMessage: error && (error.message || String(error))
                }
            }));
        }

        function revealReturnBallDragWindow() {
            if (!window.nekoPetDrag || typeof window.nekoPetDrag.reveal !== 'function') {
                dispatchReturnBallRevealFailed('bridge-unavailable');
                return false;
            }
            let settled = false;
            const fallbackTimer = setTimeout(() => {
                if (settled) return;
                dispatchReturnBallRevealFailed('reveal-timeout');
            }, I.MULTI_WINDOW_RETURN_BALL_REVEAL_FALLBACK_MS);
            try {
                const revealResult = window.nekoPetDrag.reveal();
                Promise.resolve(revealResult).then((ok) => {
                    settled = true;
                    clearTimeout(fallbackTimer);
                    if (ok === false) {
                        dispatchReturnBallRevealFailed('reveal-failed');
                    }
                }).catch((error) => {
                    settled = true;
                    clearTimeout(fallbackTimer);
                    console.warn('[App] 返回球拖拽渲染完成后恢复窗口显示失败:', error);
                    dispatchReturnBallRevealFailed('reveal-rejected', error);
                });
                return true;
            } catch (error) {
                settled = true;
                clearTimeout(fallbackTimer);
                console.warn('[App] 返回球拖拽渲染完成后恢复窗口显示失败:', error);
                dispatchReturnBallRevealFailed('reveal-threw', error);
                return false;
            }
        }

        function getSavedBallStyleValue(key) {
            return state.savedBallStyle ? state.savedBallStyle[key] : '';
        }

        function normalizeWindowBounds(bounds) {
            if (!bounds) return null;
            const source = bounds.requestedBounds || (
                bounds.bounds && !Number.isFinite(Number(bounds.x))
                    ? bounds.bounds
                    : bounds
            );
            const x = Number(source.x);
            const y = Number(source.y);
            const width = Number(source.width);
            const height = Number(source.height);
            if (!Number.isFinite(x) || !Number.isFinite(y) ||
                !Number.isFinite(width) || !Number.isFinite(height)) {
                return null;
            }
            if (width <= 0 || height <= 0) {
                return null;
            }
            const normalized = { x, y, width, height };
            const offset = bounds.actualBoundsOffset || source.actualBoundsOffset;
            const offsetX = Number(offset && offset.x);
            const offsetY = Number(offset && offset.y);
            if (Number.isFinite(offsetX) && Number.isFinite(offsetY)) {
                normalized.actualBoundsOffset = {
                    x: offsetX,
                    y: offsetY
                };
            }
            return normalized;
        }

        function isActiveDragToken(token) {
            return I.multiWindowReturnBallDragState === state && state.dragSessionToken === token;
        }

        function dispatchReturnBallClick() {
            if (
                container.getAttribute('data-neko-model-cat-transitioning') === 'cat-to-model' ||
                I.isNekoModelCatTransitionActive()
            ) {
                return;
            }
            const id = String(container.id || '');
            const match = id.match(/^([a-z0-9-]+)-return-button-container$/i);
            if (!match || !match[1]) {
                console.warn('[dispatchReturnBallClick] container id does not match expected pattern, return-click event not dispatched. id:', id);
                return;
            }

            const rect = container.getBoundingClientRect();
            const dispatchClickEvent = () => {
                window.dispatchEvent(new CustomEvent(`${match[1]}-return-click`, {
                    detail: {
                        returnButtonRect: {
                            left: rect.left,
                            top: rect.top,
                            width: rect.width,
                            height: rect.height
                        }
                    }
                }));
            };
            if (I.getReturnButtonAppearance(container) === I.NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
                dispatchClickEvent();
                return;
            }
            I.playNekoModelCatTransition({
                direction: 'cat-to-model',
                anchorRect: rect,
                coverRect: window._savedGoodbyeRect || I.getActiveModelTransitionRect(),
                container: container
            }).catch(() => {});
            dispatchClickEvent();
        }

        function setReturnBallDomClickSuppressed(suppressed, ttlMs) {
            if (state.suppressDomClickTimer) {
                clearTimeout(state.suppressDomClickTimer);
                state.suppressDomClickTimer = null;
            }
            if (!suppressed) {
                container.removeAttribute('data-neko-return-click-suppressed');
                return;
            }
            container.setAttribute('data-neko-return-click-suppressed', 'true');
            state.suppressDomClickTimer = setTimeout(() => {
                state.suppressDomClickTimer = null;
                container.removeAttribute('data-neko-return-click-suppressed');
            }, Number.isFinite(ttlMs) ? ttlMs : 500);
        }

        function markDragPointerActivity() {
            state.lastPointerEventAt = Date.now();
        }

        function cancelActiveDrag(reason) {
            if (!state.isDragging) return;
            const screenX = I.getReturnBallDragScreenCoordinate(state.releaseScreenX, state.startScreenX);
            const screenY = I.getReturnBallDragScreenCoordinate(state.releaseScreenY, state.startScreenY);
            void finishDrag(screenX, screenY, {
                reason: reason || 'return-ball-drag-cancel',
                suppressClick: true
            });
        }

        function scheduleReturnBallDragRecoveryCheck() {
            I.clearReturnBallDragRecoveryTimer(state);
            if (!state.isDragging) return;
            state.dragRecoveryTimer = setTimeout(() => {
                state.dragRecoveryTimer = null;
                if (!state.isDragging) return;
                if (document.hidden) {
                    cancelActiveDrag('document-hidden');
                    return;
                }
                if (Date.now() - state.lastPointerEventAt > I.RETURN_BALL_DRAG_STALE_RECOVERY_MS) {
                    cancelActiveDrag('stale-pointer-timeout');
                    return;
                }
                scheduleReturnBallDragRecoveryCheck();
            }, I.RETURN_BALL_DRAG_RECOVERY_POLL_MS);
        }

        function finishDragIfMouseButtonReleased(event, reason) {
            if (!state.isDragging || !event || (event.pointerType && event.pointerType !== 'mouse')) {
                return false;
            }
            if (!Number.isFinite(event.buttons) || event.buttons !== 0) {
                return false;
            }
            void finishDrag(event.screenX, event.screenY, {
                reason: reason || 'buttons-released'
            });
            return true;
        }

        function isViewportRestored(expectedWidth, expectedHeight) {
            if (!Number.isFinite(expectedWidth) || !Number.isFinite(expectedHeight)) {
                return true;
            }
            const tolerance = 2;
            return Math.abs(window.innerWidth - expectedWidth) <= tolerance &&
                Math.abs(window.innerHeight - expectedHeight) <= tolerance;
        }

        function waitForViewportSize(dragToken, expectedWidth, expectedHeight, onReady, options) {
            if (!isActiveDragToken(dragToken)) return;
            I.clearMultiWindowReturnBallDeferredWork(state);
            const fallbackMs = options && Number.isFinite(options.fallbackMs)
                ? options.fallbackMs
                : 600;
            const fallbackDeadline = Date.now() + Math.max(0, fallbackMs);
            const hardFallbackDeadline = fallbackDeadline + Math.max(1000, Math.max(0, fallbackMs) * 2);
            const continueOnFallback = !!(options && options.continueOnFallback);

            const runWhenStable = (meta) => {
                requestAnimationFrame(() => {
                    requestAnimationFrame(() => {
                        if (!isActiveDragToken(dragToken)) return;
                        onReady(meta || {});
                    });
                });
            };

            const tryFinish = () => {
                if (!isActiveDragToken(dragToken)) return false;
                if (!isViewportRestored(expectedWidth, expectedHeight)) return false;
                I.clearMultiWindowReturnBallDeferredWork(state);
                runWhenStable();
                return true;
            };

            if (tryFinish()) {
                return;
            }

            state.viewportWaitOnResize = () => {
                void tryFinish();
            };
            window.addEventListener('resize', state.viewportWaitOnResize);
            let timeoutWarned = false;
            const pollViewportRestore = () => {
                if (!isActiveDragToken(dragToken)) return;
                if (tryFinish()) return;

                const remainingMs = fallbackDeadline - Date.now();
                if (remainingMs <= 0) {
                    if (continueOnFallback) {
                        console.warn(
                            '[pollViewportRestore] waitForViewportSize timed out; continuing best-effort cleanup.',
                            'dragToken:', state.dragSessionToken,
                            'fallbackMs:', fallbackMs,
                            'fallbackDeadline:', fallbackDeadline
                        );
                        I.clearMultiWindowReturnBallDeferredWork(state);
                        runWhenStable({ timedOut: true });
                        return;
                    }
                    if (Date.now() >= hardFallbackDeadline) {
                        console.warn(
                            '[pollViewportRestore] waitForViewportSize hard timeout; continuing best-effort cleanup.',
                            'dragToken:', state.dragSessionToken,
                            'fallbackMs:', fallbackMs,
                            'fallbackDeadline:', fallbackDeadline
                        );
                        I.clearMultiWindowReturnBallDeferredWork(state);
                        runWhenStable();
                        return;
                    }
                    if (!timeoutWarned) {
                        timeoutWarned = true;
                        console.warn(
                            '[pollViewportRestore] waitForViewportSize timed out; keeping return-ball hidden until viewport is restored.',
                            'dragToken:', state.dragSessionToken,
                            'fallbackMs:', fallbackMs,
                            'fallbackDeadline:', fallbackDeadline
                        );
                    }
                    state.viewportWaitFallbackTimer = setTimeout(pollViewportRestore, 50);
                    return;
                }

                state.viewportWaitFallbackTimer = setTimeout(
                    pollViewportRestore,
                    Math.min(remainingMs, 16)
                );
            };
            state.viewportWaitFallbackTimer = setTimeout(
                pollViewportRestore,
                Math.min(Math.max(0, fallbackMs), 16)
            );
        }

        async function resolveFinalWindowBounds(screenX, screenY, dragToken) {
            let bounds = null;
            try {
                bounds = normalizeWindowBounds(await window.nekoPetDrag.stop(screenX, screenY));
            } catch (error) {
                console.warn('[App] 返回球停止拖拽后获取窗口边界失败:', error);
            }

            if (!isActiveDragToken(dragToken)) return null;
            if (bounds) return bounds;

            if (!window.nekoPetDrag || typeof window.nekoPetDrag.getBounds !== 'function') {
                return null;
            }

            try {
                bounds = normalizeWindowBounds(await window.nekoPetDrag.getBounds());
            } catch (error) {
                console.warn('[App] 返回球 fallback 获取窗口边界失败:', error);
            }

            if (!isActiveDragToken(dragToken)) return null;
            return bounds;
        }

        function beginDrag(screenX, screenY, event) {
            if (I.isIdleCat1PlaygroundActiveForReturnBallDesktopBridge()) return;
            I.clearMultiWindowReturnBallDeferredWork(state);
            state.dragSessionToken += 1;
            const dragToken = state.dragSessionToken;

            const dragStarted = window.nekoPetDrag.start(screenX, screenY);
            if (dragStarted === false) {
                container.setAttribute('data-dragging', 'false');
                setReturnBallDomClickSuppressed(false);
                return;
            }

            I.restoreNekoIdleCat1EdgePeekBeforeDrag(container);
            window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                detail: {
                    reason: 'return-ball-drag-start',
                    container: container
                }
            }));
            state.isDragging = true;
            state.hasMoved = false;
            state.startScreenX = screenX;
            state.startScreenY = screenY;
            state.releaseScreenX = screenX;
            state.releaseScreenY = screenY;
            state.savedWindowW = window.innerWidth;
            state.savedWindowH = window.innerHeight;
            state.niriPhysicalCropDrag = I.isNiriPhysicalCropReturnBallDragActive();
            markDragPointerActivity();

            const rect = container.getBoundingClientRect();
            state.savedBallWidth = Math.round(rect.width) || 64;
            state.savedBallHeight = Math.round(rect.height) || 64;
            state.savedBallStyle = {
                left: container.style.left,
                top: container.style.top,
                right: container.style.right,
                bottom: container.style.bottom,
                transform: container.style.transform,
                opacity: container.style.opacity,
                visibility: container.style.visibility,
                transition: container.style.transition,
                willChange: container.style.willChange,
            };

            const centeredLeft = Math.max(0, Math.round((I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE - state.savedBallWidth) / 2));
            const centeredTop = Math.max(0, Math.round((I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE - state.savedBallHeight) / 2));

            container.style.transition = 'none';
            container.style.setProperty('--neko-ball-drag-size', `${state.savedBallWidth}px`);
            container.setAttribute('data-dragging', 'false');
            if (state.niriPhysicalCropDrag) {
                container.style.opacity = getSavedBallStyleValue('opacity');
                container.style.visibility = getSavedBallStyleValue('visibility');
                container.style.willChange = 'transform';
            } else {
                // 先隐藏球再移动到居中位置，防止闪烁
                container.style.opacity = '0';
                container.style.left = `${centeredLeft}px`;
                container.style.top = `${centeredTop}px`;
                container.style.right = '';
                container.style.bottom = '';
                container.style.transform = 'none';

                document.documentElement.style.setProperty('background', 'transparent', 'important');
                document.body.style.setProperty('background', 'transparent', 'important');
                if (!document.getElementById('_neko-ball-drag-style')) {
                    const styleEl = document.createElement('style');
                    styleEl.id = '_neko-ball-drag-style';
                    styleEl.textContent = [
                        'body[data-neko-ball-drag], body[data-neko-ball-drag] * { background:transparent!important; background-color:transparent!important; box-shadow:none!important; }',
                        'body[data-neko-ball-drag] > *:not([id$="-return-button-container"]) { display:none!important; }',
                        'body[data-neko-ball-drag] * { transition:none!important; animation:none!important; }',
                        'body[data-neko-ball-drag] .neko-idle-return-btn { --neko-idle-return-size:var(--neko-ball-drag-size)!important; width:var(--neko-ball-drag-size)!important; height:var(--neko-ball-drag-size)!important; min-width:var(--neko-ball-drag-size)!important; min-height:var(--neko-ball-drag-size)!important; max-width:var(--neko-ball-drag-size)!important; max-height:var(--neko-ball-drag-size)!important; }',
                        'body[data-neko-ball-drag] .neko-idle-return-art, body[data-neko-ball-drag] .neko-idle-return-art-next { width:100%!important; height:100%!important; object-fit:contain!important; object-position:center!important; }',
                        'body[data-neko-ball-drag] .neko-idle-return-btn.is-cat1-playing > .neko-idle-return-art, body[data-neko-ball-drag] .neko-idle-return-art[data-neko-cat1-play-finishing="true"] { width:175%!important; min-width:175%!important; max-width:none!important; height:100%!important; object-fit:contain!important; object-position:center!important; }',
                    ].join('\n');
                    document.head.appendChild(styleEl);
                }
                document.body.dataset.nekoBallDrag = '1';

                // dragStart 的 shrink 通过异步 IPC 落到主进程，不能再靠固定帧数猜测
                // 拖拽视口已经生效；否则返回球会按临时 left/top 在原窗口左侧闪一帧。
                waitForViewportSize(
                    dragToken,
                    I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE,
                    I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_SIZE,
                    () => {
                        if (!state.isDragging || !isActiveDragToken(dragToken)) return;
                        container.style.opacity = getSavedBallStyleValue('opacity');
                        container.style.visibility = getSavedBallStyleValue('visibility');
                        container.style.willChange = 'opacity';
                    },
                    {
                        fallbackMs: I.MULTI_WINDOW_RETURN_BALL_DRAG_SHRINK_FALLBACK_MS,
                        continueOnFallback: true
                    }
                );
            }
            scheduleReturnBallDragRecoveryCheck();

            if (event) {
                event.preventDefault();
                event.stopImmediatePropagation();
            }
        }

        function sendReturnBallNativeDragMove(screenX, screenY) {
            if (!state.niriPhysicalCropDrag ||
                !window.nekoPetDrag ||
                typeof window.nekoPetDrag.move !== 'function') {
                return;
            }
            if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) return;
            try {
                window.nekoPetDrag.move(screenX, screenY);
            } catch (_) {}
        }

        function updateDrag(screenX, screenY, sourcePoint = null) {
            if (!state.isDragging) return;
            markDragPointerActivity();
            state.releaseScreenX = screenX;
            state.releaseScreenY = screenY;
            sendReturnBallNativeDragMove(screenX, screenY);

            const dx = screenX - state.startScreenX;
            const dy = screenY - state.startScreenY;
            const movedPastClickThreshold = Math.abs(dx) > CLICK_THRESHOLD || Math.abs(dy) > CLICK_THRESHOLD;
            if (!state.hasMoved && movedPastClickThreshold) {
                state.hasMoved = true;
                container.setAttribute('data-dragging', 'true');
                window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                    detail: {
                        reason: 'return-ball-drag-active',
                        container: container
                    }
                }));
            }
            if (state.hasMoved) {
                window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                    detail: {
                        reason: 'return-ball-drag-motion',
                        container: container,
                        clientX: sourcePoint && Number.isFinite(sourcePoint.clientX) ? sourcePoint.clientX : screenX,
                        clientY: sourcePoint && Number.isFinite(sourcePoint.clientY) ? sourcePoint.clientY : screenY,
                        screenX: screenX,
                        screenY: screenY,
                        deltaX: dx,
                        deltaY: dy,
                        timestamp: Date.now()
                    }
                }));
                I.scheduleIdleReturnBallDesktopDragState(
                    container,
                    I.getReturnBallDragScreenRect(
                        screenX,
                        screenY,
                        state.savedBallWidth,
                        state.savedBallHeight
                    )
                );
            }
        }

        async function finishDrag(screenX, screenY) {
            if (!state.isDragging) return;

            const options = arguments[2] && typeof arguments[2] === 'object' ? arguments[2] : {};
            const suppressClick = options.suppressClick === true;
            const suppressNoMoveClick = suppressClick;
            state.isDragging = false;
            state.releaseScreenX = screenX;
            state.releaseScreenY = screenY;
            const dragToken = state.dragSessionToken;
            I.clearReturnBallDragRecoveryTimer(state);
            I.clearMultiWindowReturnBallDeferredWork(state);

            container.style.transition = 'none';
            if (!state.niriPhysicalCropDrag) {
                // 先瞬间隐藏球，防止恢复 UI 时球在 (8,8) 闪烁
                container.style.opacity = '0';
                container.style.visibility = 'hidden';
                void container.offsetWidth;
            }

            if (!state.hasMoved) {
                container.setAttribute('data-dragging', 'true');
                let restoreBounds = null;
                try {
                    restoreBounds = normalizeWindowBounds(await window.nekoPetDrag.stop(screenX, screenY));
                } catch (error) {
                    console.warn('[App] 返回球点击结束时恢复窗口失败:', error);
                }
                const pendingRestoreBounds = restoreBounds || {
                    width: state.savedWindowW,
                    height: state.savedWindowH
                };
                if (!isActiveDragToken(dragToken)) return;
                I.setPendingNativeModelViewportRestoreBounds(pendingRestoreBounds);
                const expectedWidth = restoreBounds ? restoreBounds.width : state.savedWindowW;
                const expectedHeight = restoreBounds ? restoreBounds.height : state.savedWindowH;
                const completeNoMoveDrag = () => {
                    restoreSavedBallStyle();
                    delete document.body.dataset.nekoBallDrag;
                    container.setAttribute('data-dragging', 'false');
                    I.scheduleIdleReturnBallDesktopBridge(
                        suppressNoMoveClick ? 'return-ball-drag-cancel' : 'return-ball-drag-click',
                        container
                    );
                    revealReturnBallDragWindow();
                    if (suppressNoMoveClick) {
                        window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                            detail: {
                                reason: 'return-ball-drag-end',
                                container: container,
                                movedDistancePx: 0,
                                dragCancelled: true
                            }
                        }));
                    } else {
                        setReturnBallDomClickSuppressed(true, 500);
                        dispatchReturnBallClick();
                    }
                };
                if (state.niriPhysicalCropDrag) {
                    completeNoMoveDrag();
                    return;
                }
                waitForViewportSize(dragToken, expectedWidth, expectedHeight, completeNoMoveDrag, {
                    fallbackMs: I.MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS,
                    continueOnFallback: true
                });
                return;
            }
            const finalBounds = await resolveFinalWindowBounds(screenX, screenY, dragToken);
            if (!isActiveDragToken(dragToken)) return;
            I.setPendingNativeModelViewportRestoreBounds(finalBounds || {
                width: state.savedWindowW,
                height: state.savedWindowH
            });
            const movedDistancePx = Math.hypot(
                state.releaseScreenX - state.startScreenX,
                state.releaseScreenY - state.startScreenY
            );

            let shouldRestoreSavedBallStyle = false;
            if (finalBounds) {
                const width = state.savedBallWidth || container.offsetWidth || 64;
                const height = state.savedBallHeight || container.offsetHeight || 64;
                const rawLeft = screenX - finalBounds.x - width / 2;
                const rawTop = screenY - finalBounds.y - height / 2;
                const maxLeft = Math.max(0, finalBounds.width - width);
                const maxTop = Math.max(0, finalBounds.height - height);
                const newLeft = Math.max(0, Math.min(Math.round(rawLeft), maxLeft));
                const newTop = Math.max(0, Math.min(Math.round(rawTop), maxTop));
                const placement = I.isNekoIdleCat1EdgePeekEligible(container)
                    ? I.getNekoIdleCat1EdgePeekPlacement(
                        newLeft,
                        newTop,
                        width,
                        height,
                        finalBounds.width,
                        finalBounds.height
                    )
                    : null;
                if (placement && placement.edge && placement.edge.includes('bottom')) {
                    placement.visualShiftY = I._getNekoIdleCat1EdgePeekVisualShiftY(finalBounds, height);
                }

                if (!I.applyNekoIdleCat1EdgePeek(container, placement)) {
                    I.clearNekoIdleCat1EdgePeek(container);
                    container.style.left = `${newLeft}px`;
                    container.style.top = `${newTop}px`;
                    container.style.right = '';
                    container.style.bottom = '';
                    container.style.transform = 'none';
                }
            } else {
                shouldRestoreSavedBallStyle = true;
            }

            const expectedWidth = finalBounds ? finalBounds.width : state.savedWindowW;
            const expectedHeight = finalBounds ? finalBounds.height : state.savedWindowH;
            const completeMovedDrag = () => {
                if (shouldRestoreSavedBallStyle) {
                    restoreSavedBallStyle();
                    container.setAttribute('data-dragging', 'false');
                    delete document.body.dataset.nekoBallDrag;
                    I.scheduleIdleReturnBallDesktopBridge('return-ball-drag-end', container);
                    window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                        detail: {
                            reason: 'return-ball-drag-end',
                            container: container,
                            movedDistancePx: movedDistancePx,
                            dragCancelled: suppressClick
                        }
                    }));
                    revealReturnBallDragWindow();
                    return;
                }
                // 先同步恢复球 opacity，再删除 nekoBallDrag 显示页面内容，
                // 避免 1 帧"页面可见但球不可见"的闪烁
                container.style.opacity = getSavedBallStyleValue('opacity');
                container.style.visibility = getSavedBallStyleValue('visibility');
                container.style.willChange = getSavedBallStyleValue('willChange');
                container.setAttribute('data-dragging', 'false');
                delete document.body.dataset.nekoBallDrag;
                I.scheduleIdleReturnBallDesktopBridge('return-ball-drag-end', container);
                window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
                    detail: {
                        reason: 'return-ball-drag-end',
                        container: container,
                        movedDistancePx: movedDistancePx,
                        dragCancelled: suppressClick
                    }
                }));
                revealReturnBallDragWindow();
                // 延迟恢复 transition，避免恢复瞬间触发动画
                state.transitionCleanupTimer = setTimeout(() => {
                    state.transitionCleanupTimer = null;
                    if (!isActiveDragToken(dragToken)) return;
                    container.style.transition = getSavedBallStyleValue('transition');
                    state.savedBallStyle = null;
                }, 180);
            };
            if (state.niriPhysicalCropDrag) {
                completeMovedDrag();
                return;
            }
            waitForViewportSize(dragToken, expectedWidth, expectedHeight, completeMovedDrag, {
                fallbackMs: I.MULTI_WINDOW_RETURN_BALL_DRAG_RESTORE_FALLBACK_MS,
                continueOnFallback: true
            });
        }

        function isThoughtBubbleEventTarget(event) {
            const target = event && event.target;
            if (!target || typeof target.closest !== 'function') return false;
            const bubble = target.closest('.neko-idle-thought-bubble');
            return !!(bubble && bubble.closest('.neko-idle-return-btn.is-thought-bubble-active'));
        }

        state.handleMouseDown = (event) => {
            if (event.button !== 0) {
                event.preventDefault();
                event.stopImmediatePropagation();
                return;
            }
            if (isThoughtBubbleEventTarget(event)) return;
            beginDrag(event.screenX, event.screenY, event);
        };
        state.handleMouseMove = (event) => {
            if (finishDragIfMouseButtonReleased(event, 'mousemove-buttons-released')) return;
            updateDrag(event.screenX, event.screenY, event);
        };
        state.handleMouseUp = (event) => {
            void finishDrag(event.screenX, event.screenY);
        };
        state.handlePointerMove = (event) => {
            if (finishDragIfMouseButtonReleased(event, 'pointermove-buttons-released')) return;
            if (event && event.pointerType === 'mouse') {
                updateDrag(event.screenX, event.screenY, event);
            }
        };
        state.handlePointerUp = (event) => {
            void finishDrag(event.screenX, event.screenY);
        };
        state.handlePointerCancel = () => {
            cancelActiveDrag('pointercancel');
        };
        state.handleTouchStart = (event) => {
            if (isThoughtBubbleEventTarget(event)) return;
            const point = getTouchScreenPoint(event.touches[0]);
            if (!point) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            beginDrag(point.x, point.y, event);
        };
        state.handleTouchMove = (event) => {
            if (!state.isDragging) return;
            const point = getTouchScreenPoint(event.touches[0]);
            if (!point) return;
            event.preventDefault();
            updateDrag(point.x, point.y, event.touches[0]);
        };
        state.handleTouchEnd = (event) => {
            const point = getTouchScreenPoint(event.changedTouches && event.changedTouches[0]);
            void finishDrag(
                point ? point.x : state.releaseScreenX,
                point ? point.y : state.releaseScreenY
            );
        };
        state.handleWindowBlur = () => {
            if (!state.isDragging) return;
            // Native return-ball dragging may legitimately blur the Pet window when
            // the companion chat layer has focus; stale recovery handles lost release.
            scheduleReturnBallDragRecoveryCheck();
        };
        state.handlePageHide = () => {
            cancelActiveDrag('pagehide');
        };
        state.handleVisibilityChange = () => {
            if (document.hidden) {
                cancelActiveDrag('visibility-hidden');
            }
        };
        state.handleClick = (event) => {
            const isSuppressed = container.getAttribute('data-neko-return-click-suppressed') === 'true';
            const isNativeDragActive = container.getAttribute('data-dragging') === 'true' ||
                container.getAttribute('data-dragging') === 'pending';
            if (!isSuppressed && !isNativeDragActive) return;
            event.preventDefault();
            event.stopImmediatePropagation();
            if (!isNativeDragActive) {
                setReturnBallDomClickSuppressed(false);
            }
        };

        container.addEventListener('mousedown', state.handleMouseDown, true);
        container.addEventListener('touchstart', state.handleTouchStart, true);
        container.addEventListener('click', state.handleClick, true);
        document.addEventListener('mousemove', state.handleMouseMove);
        document.addEventListener('mouseup', state.handleMouseUp);
        document.addEventListener('pointermove', state.handlePointerMove, true);
        document.addEventListener('pointerup', state.handlePointerUp, true);
        document.addEventListener('pointercancel', state.handlePointerCancel, true);
        document.addEventListener('touchmove', state.handleTouchMove, { passive: false });
        document.addEventListener('touchend', state.handleTouchEnd);
        document.addEventListener('touchcancel', state.handleTouchEnd);
        window.addEventListener('blur', state.handleWindowBlur);
        window.addEventListener('pagehide', state.handlePageHide);
        document.addEventListener('visibilitychange', state.handleVisibilityChange);

        I.multiWindowReturnBallDragState = state;
    }

    window.hideAllNekoReturnBallContainers = function(reason = 'return-ball-hide') {
        I.hideReturnBallContainer(document.getElementById('live2d-return-button-container'), reason);
        I.hideReturnBallContainer(document.getElementById('vrm-return-button-container'), reason);
        I.hideReturnBallContainer(document.getElementById('mmd-return-button-container'), reason);
        I.ensureMultiWindowReturnBallDrag(null);
    };

    /**
     * Wire up floating-button event listeners.
     * Must be called once after DOM elements are available (from init_app).
     * Receives refs to DOM buttons that still live in app.js's init_app scope.
     */

    Object.assign(window.appUi, I.mod || {});
})();
