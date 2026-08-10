(function installModelManagerBackgroundDrag() {
    'use strict';

    const DRAG_THRESHOLD_PX = 4;
    let dragSession = null;

    function isModelManagerPage() {
        return !!(
            document.body && document.body.classList.contains('model-manager-page')
        );
    }

    function isGuideBlockingDrag() {
        const body = document.body;
        return !!(body && (
            body.classList.contains('yui-guide-home-ui-suppressed') ||
            body.classList.contains('yui-taking-over')
        ));
    }

    function isVisible(element) {
        if (!element || element.classList.contains('hidden')) return false;
        if (element.style.display === 'none' || element.style.visibility === 'hidden') return false;
        try {
            const style = window.getComputedStyle(element);
            return style.display !== 'none' && style.visibility !== 'hidden';
        } catch (_) {
            return true;
        }
    }

    function getLive3DSubType() {
        const declared = window._modelManagerCurrentLive3dSubType;
        if (declared === 'vrm' || declared === 'mmd') return declared;
        if (isVisible(document.getElementById('mmd-container'))) return 'mmd';
        return 'vrm';
    }

    function getActiveModelType() {
        const type = window._modelManagerCurrentAvatarType;
        if (type === 'pngtuber' || type === 'live3d' || type === 'live2d') return type;
        if (isVisible(document.getElementById('pngtuber-container'))) return 'pngtuber';
        if (isVisible(document.getElementById('mmd-container')) ||
            isVisible(document.getElementById('vrm-container'))) {
            return 'live3d';
        }
        return 'live2d';
    }

    function getLive2DCanvasPoint(manager, clientX, clientY) {
        const canvas = manager && manager.pixi_app && manager.pixi_app.view;
        const rendererScreen = manager && manager.pixi_app && manager.pixi_app.renderer
            ? manager.pixi_app.renderer.screen
            : null;
        if (!canvas || typeof canvas.getBoundingClientRect !== 'function') return null;
        const rect = canvas.getBoundingClientRect();
        if (!(rect.width > 0) || !(rect.height > 0)) return null;
        const rendererWidth = Number(rendererScreen && rendererScreen.width) || rect.width;
        const rendererHeight = Number(rendererScreen && rendererScreen.height) || rect.height;
        return {
            canvas,
            x: (clientX - rect.left) * (rendererWidth / rect.width),
            y: (clientY - rect.top) * (rendererHeight / rect.height),
            scaleX: rendererWidth / rect.width,
            scaleY: rendererHeight / rect.height
        };
    }

    function isPointOnLive2DModel(manager, clientX, clientY) {
        const model = manager && manager.currentModel;
        const point = getLive2DCanvasPoint(manager, clientX, clientY);
        if (!model || !point || typeof model.getBounds !== 'function') return false;

        try {
            const bounds = model.getBounds();
            const left = Number.isFinite(bounds.left) ? bounds.left : bounds.x;
            const top = Number.isFinite(bounds.top) ? bounds.top : bounds.y;
            const right = Number.isFinite(bounds.right) ? bounds.right : left + bounds.width;
            const bottom = Number.isFinite(bounds.bottom) ? bounds.bottom : top + bounds.height;
            return Number.isFinite(left) && Number.isFinite(top) &&
                Number.isFinite(right) && Number.isFinite(bottom) &&
                point.x >= left && point.x <= right &&
                point.y >= top && point.y <= bottom;
        } catch (_) {
            return false;
        }
    }

    function createLive2DAdapter(event) {
        const manager = window.live2dManager;
        const model = manager && manager.currentModel;
        const point = getLive2DCanvasPoint(manager, event.clientX, event.clientY);
        if (!manager || !model || !point || event.target !== point.canvas) return null;
        if (!manager._isModelReadyForInteraction || manager.isLocked) return null;
        if (isPointOnLive2DModel(manager, event.clientX, event.clientY)) return null;

        return {
            type: 'live2d',
            surface: point.canvas,
            move(deltaX, deltaY) {
                if (manager.currentModel !== model || model.destroyed) return false;
                model.x += deltaX * point.scaleX;
                model.y += deltaY * point.scaleY;
                manager.isFocusing = false;
                if (typeof manager.boostLinuxX11InteractiveFPS === 'function') {
                    manager.boostLinuxX11InteractiveFPS(1400);
                }
                return true;
            },
            async finish(moved) {
                if (!moved || manager.currentModel !== model || model.destroyed) return;
                let snapped = false;
                if (typeof manager._checkAndPerformSnap === 'function') {
                    snapped = await manager._checkAndPerformSnap(model);
                }
                if (!snapped && typeof manager._savePositionAfterInteraction === 'function') {
                    await manager._savePositionAfterInteraction();
                }
            }
        };
    }

    function createThreeDAdapter(event, type) {
        const manager = type === 'mmd' ? window.mmdManager : window.vrmManager;
        const interaction = manager && manager.interaction;
        const canvas = manager && manager.renderer && manager.renderer.domElement;
        if (!manager || !interaction || !canvas || event.target !== canvas) return null;
        if (!manager._isModelReadyForInteraction ||
            (typeof interaction.checkLocked === 'function' && interaction.checkLocked())) {
            return null;
        }
        if (typeof interaction._hitTestModel === 'function' &&
            interaction._hitTestModel(event.clientX, event.clientY)) {
            return null;
        }

        return {
            type,
            surface: canvas,
            move(deltaX, deltaY) {
                const center = typeof interaction._getProjectedModelCenterInWindow === 'function'
                    ? interaction._getProjectedModelCenterInWindow()
                    : null;
                if (!center || typeof interaction._moveModelCenterToWindowPoint !== 'function') {
                    return false;
                }
                if (type === 'mmd' && manager.core &&
                    typeof manager.core._boostInteractiveFPS === 'function') {
                    manager.core._boostInteractiveFPS();
                } else if (type === 'vrm' && typeof manager._boostInteractiveFPS === 'function') {
                    manager._boostInteractiveFPS();
                }
                return interaction._moveModelCenterToWindowPoint(
                    center.x + deltaX,
                    center.y + deltaY
                );
            },
            async finish(moved) {
                if (!moved) return;
                let snapped = false;
                if (typeof interaction._snapModelIntoScreen === 'function') {
                    snapped = await interaction._snapModelIntoScreen({ animate: true });
                }
                // VRM 的回弹只负责改位置，MMD 的回弹内部已经保存；保持两者原有语义。
                if ((type === 'vrm' || !snapped) &&
                    typeof interaction._savePositionAfterInteraction === 'function') {
                    await interaction._savePositionAfterInteraction();
                }
            }
        };
    }

    function createPNGTuberAdapter(event) {
        const manager = window.pngtuberManager;
        const container = manager && manager.container;
        if (!manager || !container || event.target !== container || manager.isLocked) return null;
        if (!manager.image || !isVisible(container)) return null;

        return {
            type: 'pngtuber',
            surface: container,
            move(deltaX, deltaY) {
                if (typeof manager.beginModelManagerPositionEditing === 'function') {
                    manager.beginModelManagerPositionEditing();
                }
                const placement = manager.getActivePlacement();
                manager.setActiveOffsets(
                    placement.offsetX + deltaX,
                    placement.offsetY + deltaY
                );
                manager.applyTransform();
                if (manager.isLayeredActive()) manager.drawLayeredState();
                manager.syncGlobalConfig();
                if (typeof manager.updateFloatingButtonsPosition === 'function') {
                    manager.updateFloatingButtonsPosition();
                }
                if (typeof manager.updateLockIconPosition === 'function') {
                    manager.updateLockIconPosition();
                }
                return true;
            },
            async finish(moved) {
                if (!moved) return;
                if (typeof manager.restartLayeredAnimationLoop === 'function') {
                    manager.restartLayeredAnimationLoop();
                }
                if (typeof window.stageModelManagerPNGTuberPlacement === 'function') {
                    window.stageModelManagerPNGTuberPlacement(manager.config);
                }
            }
        };
    }

    function createBackgroundAdapter(event) {
        const modelType = getActiveModelType();
        if (modelType === 'pngtuber') return createPNGTuberAdapter(event);
        if (modelType === 'live3d') {
            return createThreeDAdapter(event, getLive3DSubType());
        }
        return createLive2DAdapter(event);
    }

    function setDraggingUi(active) {
        if (!document.body) return;
        document.body.classList.toggle('model-manager-background-dragging', active);
        if (window.DragHelpers) {
            const method = active ? 'disableButtonPointerEvents' : 'restoreButtonPointerEvents';
            if (typeof window.DragHelpers[method] === 'function') {
                window.DragHelpers[method]();
            }
        }
    }

    function onPointerDown(event) {
        if (!isModelManagerPage() || dragSession || isGuideBlockingDrag()) return;
        if (event.button !== 0 || event.isPrimary === false) return;

        const adapter = createBackgroundAdapter(event);
        if (!adapter) return;

        dragSession = {
            adapter,
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            lastX: event.clientX,
            lastY: event.clientY,
            moved: false
        };

        if (adapter.surface && typeof adapter.surface.setPointerCapture === 'function') {
            try { adapter.surface.setPointerCapture(event.pointerId); } catch (_) {}
        }
        setDraggingUi(true);
        event.preventDefault();
        event.stopPropagation();
    }

    function onPointerMove(event) {
        const session = dragSession;
        if (!session || (session.pointerId !== undefined && event.pointerId !== session.pointerId)) {
            return;
        }

        const totalX = event.clientX - session.startX;
        const totalY = event.clientY - session.startY;
        if (!session.moved && Math.hypot(totalX, totalY) < DRAG_THRESHOLD_PX) {
            event.preventDefault();
            return;
        }

        const deltaX = event.clientX - session.lastX;
        const deltaY = event.clientY - session.lastY;
        session.lastX = event.clientX;
        session.lastY = event.clientY;
        session.moved = session.adapter.move(deltaX, deltaY) || session.moved;
        event.preventDefault();
        event.stopPropagation();
    }

    function finishDrag(event) {
        const session = dragSession;
        if (!session) return;
        if (event && session.pointerId !== undefined && event.pointerId !== session.pointerId) return;
        dragSession = null;

        if (session.adapter.surface &&
            typeof session.adapter.surface.releasePointerCapture === 'function' &&
            session.pointerId !== undefined) {
            try { session.adapter.surface.releasePointerCapture(session.pointerId); } catch (_) {}
        }
        setDraggingUi(false);
        Promise.resolve(session.adapter.finish(session.moved)).catch((error) => {
            console.warn('[ModelManager] 背景拖动结束处理失败:', error);
        });
    }

    function install() {
        if (!isModelManagerPage() || window.__modelManagerBackgroundDragInstalled) return;
        window.__modelManagerBackgroundDragInstalled = true;
        document.addEventListener('pointerdown', onPointerDown, true);
        window.addEventListener('pointermove', onPointerMove, true);
        window.addEventListener('pointerup', finishDrag, true);
        window.addEventListener('pointercancel', finishDrag, true);
        window.addEventListener('blur', () => finishDrag(null));
    }

    window.ModelManagerBackgroundDragController = {
        createBackgroundAdapter,
        finishDrag,
        getActiveModelType,
        install,
        isPointOnLive2DModel,
        onPointerDown,
        onPointerMove
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', install, { once: true });
    } else {
        install();
    }
})();
