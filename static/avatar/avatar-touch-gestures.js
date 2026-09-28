/* Touch ownership shared by the Live2D, VRM and MMD interaction adapters. */
(function (global) {
    'use strict';

    function midpoint(points) {
        return {
            clientX: (points[0].clientX + points[1].clientX) / 2,
            clientY: (points[0].clientY + points[1].clientY) / 2
        };
    }

    function distance(points) {
        return Math.hypot(points[0].clientX - points[1].clientX, points[0].clientY - points[1].clientY);
    }

    function install(canvas, callbacks) {
        const document = canvas.ownerDocument;
        const win = document.defaultView;
        const pointers = new Map();
        const previousTouchAction = canvas.style.touchAction;
        let pinched = false;
        let moved = false;
        let baseline = null;
        let finishing = false;
        canvas.style.touchAction = 'none';

        function consume(event) {
            event.preventDefault();
            event.stopPropagation();
        }

        function rebase() {
            const points = Array.from(pointers.values()).slice(0, 2);
            if (points.length === 2) pinched = true;
            baseline = points.length === 2
                ? { points, center: midpoint(points), distance: distance(points) }
                : { points };
            callbacks.begin(points, { pinched });
        }

        function release(id) {
            try { canvas.releasePointerCapture(id); } catch (_) { /* Already released by the browser. */ }
        }

        function finish(event, cancelled) {
            if (!pointers.size || finishing) return;
            finishing = true;
            const ids = Array.from(pointers.keys());
            pointers.clear();
            ids.forEach(release);
            baseline = null;
            try { callbacks.end(event, { cancelled, pinched, moved }); }
            finally { finishing = false; pinched = false; moved = false; }
        }

        function onDown(event) {
            if (event.pointerType !== 'touch' || finishing) return;
            if (!callbacks.enabled()) return;
            if (!pointers.size && !callbacks.hitTest(event)) return;
            consume(event);
            pointers.set(event.pointerId, event);
            try { canvas.setPointerCapture(event.pointerId); } catch (_) { /* Document listeners remain active. */ }
            rebase();
        }

        function onMove(event) {
            if (event.pointerType !== 'touch') return;
            if (!pointers.has(event.pointerId)) return;
            consume(event);
            if (!callbacks.enabled()) { finish(event, true); return; }
            pointers.set(event.pointerId, event);
            const points = Array.from(pointers.values()).slice(0, 2);
            if (points.length === 1) {
                moved = moved || Math.hypot(points[0].clientX - baseline.points[0].clientX,
                    points[0].clientY - baseline.points[0].clientY) > 3;
                callbacks.move(points, baseline);
            } else if (baseline.distance > 0) {
                moved = true;
                callbacks.move(points, { ...baseline, ratio: distance(points) / baseline.distance, center: midpoint(points) });
            } else {
                // Coincident contacts have no scale baseline. Wait until they separate.
                rebase();
            }
        }

        function onUp(event) {
            if (event.pointerType && event.pointerType !== 'touch') return;
            if (!pointers.has(event.pointerId)) return;
            consume(event);
            if (event.type !== 'pointerup' || !callbacks.enabled()) { finish(event, true); return; }
            onMove(event);
            if (!pointers.has(event.pointerId)) return;
            if (pointers.size === 1) { finish(event, false); return; }
            pointers.delete(event.pointerId);
            release(event.pointerId);
            rebase();
        }

        const onBlur = () => finish(null, true);
        const onVisibility = () => { if (document.hidden) onBlur(); };
        canvas.addEventListener('pointerdown', onDown, { capture: true, passive: false });
        document.addEventListener('pointermove', onMove, { capture: true, passive: false });
        document.addEventListener('pointerup', onUp, true);
        document.addEventListener('pointercancel', onUp, true);
        canvas.addEventListener('lostpointercapture', onUp, true);
        win.addEventListener('blur', onBlur);
        document.addEventListener('visibilitychange', onVisibility);
        return {
            get active() { return pointers.size > 0; },
            dispose() {
                onBlur();
                canvas.removeEventListener('pointerdown', onDown, true);
                document.removeEventListener('pointermove', onMove, true);
                document.removeEventListener('pointerup', onUp, true);
                document.removeEventListener('pointercancel', onUp, true);
                canvas.removeEventListener('lostpointercapture', onUp, true);
                win.removeEventListener('blur', onBlur);
                document.removeEventListener('visibilitychange', onVisibility);
                canvas.style.touchAction = previousTouchAction;
            }
        };
    }

    function installThree(interaction, { getModel, setScale, enabled }) {
        const manager = interaction.manager;
        const canvas = manager.renderer.domElement;
        const THREE = global.THREE;
        let pinch = null;
        function clearDrag() {
            interaction.isDragging = false;
            interaction.dragMode = null;
            canvas.style.cursor = 'default';
        }
        function planePoint(point, plane) {
            const rect = canvas.getBoundingClientRect();
            if (!(rect.width > 0 && rect.height > 0)) return null;
            const raycaster = new THREE.Raycaster();
            raycaster.setFromCamera(new THREE.Vector2(
                (point.clientX - rect.left) / rect.width * 2 - 1,
                1 - (point.clientY - rect.top) / rect.height * 2
            ), manager.camera);
            return raycaster.ray.intersectPlane(plane, new THREE.Vector3());
        }
        return install(canvas, {
            enabled,
            hitTest: event => interaction._hitTestModel(event.clientX, event.clientY),
            begin(points) {
                const model = getModel();
                pinch = null;
                if (points.length === 1) {
                    // Resume from the remaining contact without another hit test: it may
                    // now lie outside the shrunken model, but still owns this gesture.
                    interaction.isDragging = true;
                    interaction.dragMode = 'pan';
                    interaction.previousMousePosition = { x: points[0].clientX, y: points[0].clientY };
                    interaction._rememberPanDragPointer(points[0], { captureOffset: true });
                    interaction._rememberDragHintPanPointer(points[0], { start: true });
                    interaction._dragHintApproachShown = false;
                } else {
                    clearDrag();
                    model.updateWorldMatrix(true, false);
                    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(
                        manager.camera.getWorldDirection(new THREE.Vector3()), model.getWorldPosition(new THREE.Vector3())
                    );
                    const point = planePoint(midpoint(points), plane);
                    if (point) pinch = { model, plane, local: model.worldToLocal(point), scale: model.scale.x };
                }
                if (interaction._snapCancelFrame) {
                    interaction._snapCancelFrame();
                    interaction._snapCancelFrame = null;
                    if (interaction._snapResolve) {
                        interaction._snapResolve(false);
                        interaction._snapResolve = null;
                    }
                    interaction._isSnappingModel = false;
                }
                const engine = manager.core || manager;
                engine._boostInteractiveFPS?.();
                interaction._disableButtonPointerEvents();
            },
            move(points, gesture) {
                if (points.length === 1) { interaction.dragHandler(points[0]); return; }
                if (!pinch || pinch.model !== getModel()) return;
                const desired = planePoint(gesture.center, pinch.plane);
                if (!desired) return;
                setScale(Math.max(0.1, Math.min(50, pinch.scale * gesture.ratio)));
                const model = pinch.model;
                model.updateWorldMatrix(true, false);
                const current = model.localToWorld(pinch.local.clone());
                if (model.parent) {
                    model.parent.worldToLocal(desired);
                    model.parent.worldToLocal(current);
                }
                model.position.add(desired.sub(current));
                manager._lastInteractionBoostTs = performance.now();
            },
            end(event, state) {
                pinch = null;
                if (!state.cancelled && event && interaction.isDragging) {
                    void interaction.mouseUpHandler(event);
                } else {
                    clearDrag();
                    interaction._restoreButtonPointerEvents();
                    if (!state.cancelled && state.moved) void interaction._savePositionAfterInteraction();
                }
            }
        });
    }

    global.NekoModelTouchGestures = Object.freeze({ install, installThree, midpoint });
})(window);
