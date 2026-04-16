/**
 * MMD 交互模块 - 点击检测、拖拽、缩放、锁定
 * 参考 vrm-interaction.js 的结构
 */

var THREE = (typeof window !== 'undefined' && window.THREE) || (typeof globalThis !== 'undefined' && globalThis.THREE) || null;
if (!THREE) {
    console.error('[MMD Interaction] THREE.js 未加载，交互功能将不可用');
}

class MMDInteraction {
    constructor(manager) {
        this.manager = manager;

        // 拖拽和缩放
        this.isDragging = false;
        this.dragMode = null; // 'pan' | 'orbit'
        this.previousMousePosition = { x: 0, y: 0 };
        this.isLocked = false;

        // 事件处理器引用
        this.mouseDownHandler = null;
        this.mouseUpHandler = null;
        this.mouseLeaveHandler = null;
        this.dragHandler = null;
        this.wheelHandler = null;
        this.mouseHoverHandler = null;

        // 射线检测
        this._raycaster = THREE ? new THREE.Raycaster() : null;
        this._mouseNDC = THREE ? new THREE.Vector2() : null;

        // 屏幕空间包围盒缓存（用于 preload.js 鼠标穿透判断）
        this._cachedScreenBounds = null; // { minX, maxX, minY, maxY }
        this._lastBoundsUpdateTime = 0;
        this._boundsUpdateInterval = 200; // ms

        // 出界回弹
        this._snapConfig = {
            duration: 260,
            easingType: 'easeOutBack'
        };
        this._snapAnimationFrameId = null;
        this._isSnappingModel = false;

        // 防抖保存
        this._savePositionDebounceTimer = null;

        // 旋转轴心（右键按下时缓存）
        this._orbitPivot = null;
    }

    // ═══════════════════ 射线检测 ═══════════════════

    _hitTestModel(clientX, clientY) {
        if (!this._raycaster || !this.manager.camera) return false;

        const mesh = this.manager.currentModel?.mesh;
        if (!mesh) return false;

        const canvas = this.manager.renderer?.domElement;
        if (!canvas) return false;

        const rect = canvas.getBoundingClientRect();
        this._mouseNDC.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        this._mouseNDC.y = -((clientY - rect.top) / rect.height) * 2 + 1;

        this._raycaster.setFromCamera(this._mouseNDC, this.manager.camera);
        const intersects = this._raycaster.intersectObject(mesh, true);
        return intersects.length > 0;
    }

    /**
     * 快速 hitTest（基于屏幕空间包围盒，用于 preload.js）
     */
    hitTestBounds(clientX, clientY) {
        const bounds = this._cachedScreenBounds;
        if (!bounds) return false;

        return clientX >= bounds.minX && clientX <= bounds.maxX &&
               clientY >= bounds.minY && clientY <= bounds.maxY;
    }

    /**
     * 更新屏幕空间包围盒缓存
     */
    updateScreenBounds() {
        const now = performance.now();
        if (now - this._lastBoundsUpdateTime < this._boundsUpdateInterval) return;
        this._lastBoundsUpdateTime = now;

        const mesh = this.manager.currentModel?.mesh;
        if (!mesh || !this.manager.camera || !this.manager.renderer) {
            this._cachedScreenBounds = null;
            return;
        }

        try {
            const box = new THREE.Box3().setFromObject(mesh);
            const corners = [
                new THREE.Vector3(box.min.x, box.min.y, box.min.z),
                new THREE.Vector3(box.min.x, box.min.y, box.max.z),
                new THREE.Vector3(box.min.x, box.max.y, box.min.z),
                new THREE.Vector3(box.min.x, box.max.y, box.max.z),
                new THREE.Vector3(box.max.x, box.min.y, box.min.z),
                new THREE.Vector3(box.max.x, box.min.y, box.max.z),
                new THREE.Vector3(box.max.x, box.max.y, box.min.z),
                new THREE.Vector3(box.max.x, box.max.y, box.max.z)
            ];

            const canvas = this.manager.renderer.domElement;
            const rect = canvas.getBoundingClientRect();
            let minX = Infinity, maxX = -Infinity;
            let minY = Infinity, maxY = -Infinity;

            for (const corner of corners) {
                corner.project(this.manager.camera);
                const screenX = (corner.x * 0.5 + 0.5) * rect.width + rect.left;
                const screenY = (-corner.y * 0.5 + 0.5) * rect.height + rect.top;
                minX = Math.min(minX, screenX);
                maxX = Math.max(maxX, screenX);
                minY = Math.min(minY, screenY);
                maxY = Math.max(maxY, screenY);
            }

            this._cachedScreenBounds = { minX, maxX, minY, maxY };
        } catch (e) {
            this._cachedScreenBounds = null;
        }
    }

    /**
     * 别名：与 VRM interaction API 保持一致
     */
    updateModelBoundsCache() {
        this.updateScreenBounds();
    }

    // ═══════════════════ 按钮辅助 ═══════════════════

    _disableButtonPointerEvents() {
        if (window.DragHelpers) {
            window.DragHelpers.disableButtonPointerEvents();
        }
    }

    _restoreButtonPointerEvents() {
        if (window.DragHelpers) {
            window.DragHelpers.restoreButtonPointerEvents();
        }
    }

    // ═══════════════════ 锁定控制 ═══════════════════

    setLocked(locked) {
        this.isLocked = locked;
    }

    checkLocked() {
        return this.isLocked || this.manager.isLocked;
    }

    // ═══════════════════ 拖拽和缩放初始化 ═══════════════════

    initDragAndZoom() {
        if (!this.manager.renderer) return;
        if (!this.manager.camera) {
            setTimeout(() => this.initDragAndZoom(), 100);
            return;
        }

        const canvas = this.manager.renderer.domElement;
        if (!THREE) {
            console.error('[MMD Interaction] THREE.js 未加载，无法初始化拖拽');
            return;
        }

        this.cleanupDragAndZoom();

        // 鼠标按下
        this.mouseDownHandler = (e) => {
            if (!this.manager._isModelReadyForInteraction) return;
            if (this.checkLocked()) return;

            if (this._snapAnimationFrameId) {
                cancelAnimationFrame(this._snapAnimationFrameId);
                this._snapAnimationFrameId = null;
                this._isSnappingModel = false;
            }

            if (e.button === 0 || e.button === 1) { // 左键/中键 - 平移
                if (!this._hitTestModel(e.clientX, e.clientY)) return;

                this.isDragging = true;
                this.dragMode = 'pan';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'move';
                e.preventDefault();
                e.stopPropagation();
                this._disableButtonPointerEvents();
            } else if (e.button === 2) { // 右键 - 旋转模型
                if (!this._hitTestModel(e.clientX, e.clientY)) return;
                this.isDragging = true;
                this.dragMode = 'orbit';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'crosshair';
                e.preventDefault();
                e.stopPropagation();

                // 缓存拖拽起始状态，用于计算总旋转量（幂等）
                const mesh = this.manager.currentModel?.mesh;
                if (mesh) {
                    const box = new THREE.Box3().setFromObject(mesh);
                    this._orbitPivot = box.getCenter(new THREE.Vector3());
                    this._orbitStartQuat = mesh.quaternion.clone();
                    this._orbitStartPos = mesh.position.clone();
                    this._orbitStartMouse = { x: e.clientX, y: e.clientY };
                }

                this._disableButtonPointerEvents();
            }
        };

        // 鼠标移动（拖拽）
        this.dragHandler = (e) => {
            if (!this.isDragging || !this.manager.camera) return;

            const dx = e.clientX - this.previousMousePosition.x;
            const dy = e.clientY - this.previousMousePosition.y;
            this.previousMousePosition = { x: e.clientX, y: e.clientY };

            if (this.dragMode === 'pan') {
                // 像素精确平移模型（参考 VRM 风格，基于相机 FOV/距离）
                const mesh = this.manager.currentModel?.mesh;
                if (!mesh) return;

                const camera = this.manager.camera;
                const renderer = this.manager.renderer;

                const cameraDistance = camera.position.distanceTo(mesh.position);
                const fov = camera.fov * (Math.PI / 180);
                const screenHeight = renderer.domElement.clientHeight;
                const screenWidth = renderer.domElement.clientWidth;

                const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
                const worldWidth = worldHeight * (screenWidth / screenHeight);

                const pixelToWorldX = worldWidth / screenWidth;
                const pixelToWorldY = worldHeight / screenHeight;

                const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
                const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);

                mesh.position.add(right.multiplyScalar(dx * pixelToWorldX));
                mesh.position.add(up.multiplyScalar(-dy * pixelToWorldY));
            } else if (this.dragMode === 'orbit') {
                // 模型绕身体中心旋转（Y轴+X轴）
                const mesh = this.manager.currentModel?.mesh;
                if (!mesh || !this._orbitStartQuat || !this._orbitPivot) return;

                const rotateSpeed = 0.005;
                const totalDx = e.clientX - this._orbitStartMouse.x;
                const totalDy = e.clientY - this._orbitStartMouse.y;

                // Y轴左右 + X轴上下
                const yQuat = new THREE.Quaternion().setFromAxisAngle(
                    new THREE.Vector3(0, 1, 0), totalDx * rotateSpeed);
                const xQuat = new THREE.Quaternion().setFromAxisAngle(
                    new THREE.Vector3(1, 0, 0), totalDy * rotateSpeed);
                const totalQuat = new THREE.Quaternion();
                totalQuat.multiplyQuaternions(yQuat, xQuat);

                // 绕 bounding box 中心旋转：旋转后调整位置使中心点保持不动
                const offset = new THREE.Vector3().subVectors(this._orbitStartPos, this._orbitPivot);
                const rotatedOffset = offset.clone().applyQuaternion(totalQuat);
                mesh.position.copy(this._orbitPivot).add(rotatedOffset);
                // 从起始状态重新计算旋转（幂等）
                mesh.quaternion.copy(this._orbitStartQuat).premultiply(totalQuat);
            }
        };

        // 鼠标抬起
        this.mouseUpHandler = async () => {
            if (this.isDragging) {
                // 保留本次拖拽类型再清状态，跨屏切换只对 pan 生效
                // （orbit 是绕模型中心旋转朝向，不应触发多屏切换）
                const wasPanDrag = this.dragMode === 'pan';
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'default';
                this._restoreButtonPointerEvents();

                // 多屏幕支持：仅对平移拖拽检测是否移出当前屏幕并切换到新屏幕
                // 若发生切屏，_checkAndSwitchDisplay 内部会负责保存位置
                const displaySwitched = wasPanDrag
                    ? await this._checkAndSwitchDisplay()
                    : false;

                if (!displaySwitched) {
                    // 拖拽结束后保存位置/旋转/缩放
                    this._savePositionAfterInteraction();
                }
            }
        };

        // 鼠标离开
        this.mouseLeaveHandler = () => {
            // 拖拽进行中不取消——document.mouseup 会处理最终释放
            if (this.isDragging) return;
            canvas.style.cursor = 'default';
        };

        // 滚轮缩放
        this.wheelHandler = (e) => {
            if (!this.manager._isModelReadyForInteraction) return;
            if (this.checkLocked()) return;

            const mesh = this.manager.currentModel?.mesh;
            if (!mesh) return;

            // 只有鼠标在模型上才响应滚轮
            if (!this._hitTestModel(e.clientX, e.clientY)) return;

            e.preventDefault();
            const scaleFactor = e.deltaY > 0 ? 0.95 : 1.05;
            mesh.scale.multiplyScalar(scaleFactor);

            // 缩放结束后防抖保存
            this._debouncedSavePosition();
        };

        // 鼠标悬停光标（仅用屏幕包围盒判断，避免高频射线检测掉帧）
        let _lastHoverHitTestAt = 0;
        this.mouseHoverHandler = (e) => {
            if (this.isDragging) return;
            if (this.checkLocked()) {
                canvas.style.cursor = 'default';
                return;
            }
            const now = performance.now();
            if ((now - _lastHoverHitTestAt) < 80) return;
            _lastHoverHitTestAt = now;
            const bounds = this._cachedScreenBounds;
            if (!bounds) {
                canvas.style.cursor = 'default';
                return;
            }
            // 椭圆近似（内切于包围盒），不外扩
            const cx = (bounds.minX + bounds.maxX) / 2;
            const cy = (bounds.minY + bounds.maxY) / 2;
            const rx = (bounds.maxX - bounds.minX) / 2 * 0.6;
            const ry = (bounds.maxY - bounds.minY) / 2 * 0.95;
            const nx = rx > 0 ? (e.clientX - cx) / rx : 0;
            const ny = ry > 0 ? (e.clientY - cy) / ry : 0;
            const isNearModel = (nx * nx + ny * ny) <= 1;
            canvas.style.cursor = isNearModel ? 'grab' : 'default';
        };

        // 绑定事件
        // mousedown/hover/wheel 绑定到 canvas，mousemove/mouseup 绑定到 document
        // 防止拖拽经过悬浮按钮时被中断
        canvas.addEventListener('mousedown', this.mouseDownHandler);
        document.addEventListener('mousemove', this.dragHandler);
        canvas.addEventListener('mousemove', this.mouseHoverHandler);
        document.addEventListener('mouseup', this.mouseUpHandler);
        canvas.addEventListener('mouseleave', this.mouseLeaveHandler);
        canvas.addEventListener('wheel', this.wheelHandler, { passive: false });

        // 禁用右键菜单
        canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    }

    // ═══════════════════ 清理 ═══════════════════

    cleanupDragAndZoom() {
        // document 级监听器必须无条件移除，防止 renderer 已销毁时泄漏
        if (this.dragHandler) document.removeEventListener('mousemove', this.dragHandler);
        if (this.mouseUpHandler) document.removeEventListener('mouseup', this.mouseUpHandler);

        const canvas = this.manager.renderer?.domElement;
        if (canvas) {
            if (this.mouseDownHandler) canvas.removeEventListener('mousedown', this.mouseDownHandler);
            if (this.mouseHoverHandler) canvas.removeEventListener('mousemove', this.mouseHoverHandler);
            if (this.mouseLeaveHandler) canvas.removeEventListener('mouseleave', this.mouseLeaveHandler);
            if (this.wheelHandler) canvas.removeEventListener('wheel', this.wheelHandler);
        }

        this.mouseDownHandler = null;
        this.dragHandler = null;
        this.mouseHoverHandler = null;
        this.mouseUpHandler = null;
        this.mouseLeaveHandler = null;
        this.wheelHandler = null;

        // 重置拖拽状态，防止 cleanup 在拖拽途中被调用时卡死
        this.isDragging = false;
        this.dragMode = null;
        this._orbitPivot = null;
        this._restoreButtonPointerEvents();
    }

    // ═══════════════════ 多屏幕切换 ═══════════════════

    /**
     * 检测模型是否移出当前屏幕并切换到新屏幕
     * 返回 true 表示发生了切屏（内部已保存位置），返回 false 表示未切屏
     */
    async _checkAndSwitchDisplay() {
        // 仅在 Electron 环境下执行
        if (!window.electronScreen || !window.electronScreen.moveWindowToDisplay) {
            return false;
        }
        if (!THREE) return false;

        const mesh = this.manager.currentModel?.mesh;
        const camera = this.manager.camera;
        const renderer = this.manager.renderer;
        if (!mesh || !camera || !renderer) return false;

        try {
            // 1. 计算模型在当前窗口中的屏幕空间中心点（像素）
            mesh.updateMatrixWorld(true);
            const box = new THREE.Box3().setFromObject(mesh);
            if (!box || !Number.isFinite(box.min.x) || !Number.isFinite(box.max.x)) {
                return false;
            }

            const canvasRect = renderer.domElement.getBoundingClientRect();
            const screenWidth = canvasRect.width;
            const screenHeight = canvasRect.height;
            if (!(screenWidth > 0) || !(screenHeight > 0)) return false;

            const corners = [
                new THREE.Vector3(box.min.x, box.min.y, box.min.z),
                new THREE.Vector3(box.max.x, box.min.y, box.min.z),
                new THREE.Vector3(box.min.x, box.max.y, box.min.z),
                new THREE.Vector3(box.max.x, box.max.y, box.min.z),
                new THREE.Vector3(box.min.x, box.min.y, box.max.z),
                new THREE.Vector3(box.max.x, box.min.y, box.max.z),
                new THREE.Vector3(box.min.x, box.max.y, box.max.z),
                new THREE.Vector3(box.max.x, box.max.y, box.max.z),
            ];

            let modelMinX = Infinity, modelMaxX = -Infinity;
            let modelMinY = Infinity, modelMaxY = -Infinity;
            for (const corner of corners) {
                const projected = corner.clone().project(camera);
                const sx = (projected.x * 0.5 + 0.5) * screenWidth;
                const sy = (-projected.y * 0.5 + 0.5) * screenHeight;
                modelMinX = Math.min(modelMinX, sx);
                modelMaxX = Math.max(modelMaxX, sx);
                modelMinY = Math.min(modelMinY, sy);
                modelMaxY = Math.max(modelMaxY, sy);
            }

            // 模型中心（相对于 canvas 左上角的像素），再偏移 canvas 在窗口中的位置
            const modelCenterX = (modelMinX + modelMaxX) / 2 + canvasRect.left;
            const modelCenterY = (modelMinY + modelMaxY) / 2 + canvasRect.top;

            // 2. 多屏幕检查
            const displays = await window.electronScreen.getAllDisplays();
            if (!displays || displays.length <= 1) return false;

            const windowWidth = window.innerWidth;
            const windowHeight = window.innerHeight;
            if (modelCenterX >= 0 && modelCenterX < windowWidth &&
                modelCenterY >= 0 && modelCenterY < windowHeight) {
                return false;
            }

            // 3. 计算模型中心在整个桌面上的绝对坐标
            const currentDisplay = await window.electronScreen.getCurrentDisplay();
            if (!currentDisplay) {
                console.warn('[MMD] 无法获取当前显示器信息');
                return false;
            }
            let currentScreenX = currentDisplay.screenX;
            let currentScreenY = currentDisplay.screenY;
            if (!Number.isFinite(currentScreenX) || !Number.isFinite(currentScreenY)) {
                if (currentDisplay.bounds &&
                    Number.isFinite(currentDisplay.bounds.x) &&
                    Number.isFinite(currentDisplay.bounds.y)) {
                    currentScreenX = currentDisplay.bounds.x;
                    currentScreenY = currentDisplay.bounds.y;
                } else {
                    return false;
                }
            }

            const modelScreenX = currentScreenX + modelCenterX;
            const modelScreenY = currentScreenY + modelCenterY;

            // 4. 查找包含模型中心点的目标显示器
            let targetDisplay = null;
            for (const display of displays) {
                const dx = Number.isFinite(display.screenX) ? display.screenX
                    : (display.bounds && display.bounds.x);
                const dy = Number.isFinite(display.screenY) ? display.screenY
                    : (display.bounds && display.bounds.y);
                const dw = Number.isFinite(display.width) ? display.width
                    : (display.bounds && display.bounds.width);
                const dh = Number.isFinite(display.height) ? display.height
                    : (display.bounds && display.bounds.height);
                if (!Number.isFinite(dx) || !Number.isFinite(dy) ||
                    !Number.isFinite(dw) || !Number.isFinite(dh)) continue;
                if (modelScreenX >= dx && modelScreenX < dx + dw &&
                    modelScreenY >= dy && modelScreenY < dy + dh) {
                    targetDisplay = { ...display, screenX: dx, screenY: dy, width: dw, height: dh };
                    break;
                }
            }
            if (!targetDisplay) return false;

            console.log('[MMD] 检测到模型移出当前屏幕，准备切换到屏幕:', targetDisplay.id);

            const result = await window.electronScreen.moveWindowToDisplay(modelScreenX, modelScreenY);

            if (!(result && result.success && !result.sameDisplay)) {
                return false;
            }
            console.log('[MMD] 屏幕切换成功:', result);

            // 5. 将模型在世界坐标中偏移，使它在新窗口中仍显示在原本的屏幕绝对位置
            const deltaPxX = currentScreenX - targetDisplay.screenX;
            const deltaPxY = currentScreenY - targetDisplay.screenY;

            if (deltaPxX !== 0 || deltaPxY !== 0) {
                const cameraDistance = camera.position.distanceTo(mesh.position);
                const fov = camera.fov * (Math.PI / 180);
                const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
                const worldWidth = worldHeight * (screenWidth / screenHeight);
                const pixelToWorldX = worldWidth / screenWidth;
                const pixelToWorldY = worldHeight / screenHeight;

                const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
                const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);

                mesh.position.add(right.clone().multiplyScalar(deltaPxX * pixelToWorldX));
                mesh.position.add(up.clone().multiplyScalar(-deltaPxY * pixelToWorldY));
            }

            // 6. 等待一帧让新窗口尺寸生效，再保存位置
            await new Promise(resolve => requestAnimationFrame(resolve));

            await this._savePositionAfterInteraction();

            return true;
        } catch (error) {
            console.error('[MMD] 检测/切换屏幕时出错:', error);
            return false;
        }
    }

    // ═══════════════════ 偏好保存 ═══════════════════

    async _savePositionAfterInteraction() {
        if (!this.manager.currentModel || !this.manager.currentModel.url) return;

        const modelUrl = this.manager.currentModel.url;
        const mesh = this.manager.currentModel.mesh;
        if (!mesh) return;

        const position = { x: mesh.position.x, y: mesh.position.y, z: mesh.position.z };
        const scale = { x: mesh.scale.x, y: mesh.scale.y, z: mesh.scale.z };
        const rotation = { x: mesh.rotation.x, y: mesh.rotation.y, z: mesh.rotation.z };

        if (!Number.isFinite(position.x) || !Number.isFinite(position.y) || !Number.isFinite(position.z) ||
            !Number.isFinite(scale.x) || !Number.isFinite(scale.y) || !Number.isFinite(scale.z)) {
            console.warn('[MMD] 位置或缩放数据无效，跳过保存');
            return;
        }

        // 显示器信息（多屏幕位置恢复）
        let displayInfo = null;
        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay) {
                    let screenX = currentDisplay.screenX;
                    let screenY = currentDisplay.screenY;
                    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
                        if (currentDisplay.bounds && Number.isFinite(currentDisplay.bounds.x) && Number.isFinite(currentDisplay.bounds.y)) {
                            screenX = currentDisplay.bounds.x;
                            screenY = currentDisplay.bounds.y;
                        }
                    }
                    if (Number.isFinite(screenX) && Number.isFinite(screenY)) {
                        displayInfo = { screenX, screenY };
                    }
                }
            } catch (error) {
                console.warn('[MMD] 获取显示器信息失败:', error);
            }
        }

        // 视口信息（跨分辨率缩放归一化）
        let viewportInfo = null;
        const screenW = window.screen.width;
        const screenH = window.screen.height;
        if (Number.isFinite(screenW) && Number.isFinite(screenH) && screenW > 0 && screenH > 0) {
            viewportInfo = { width: screenW, height: screenH };
        }

        if (this.manager.core && typeof this.manager.core.saveUserPreferences === 'function') {
            this.manager.core.saveUserPreferences(
                modelUrl,
                position, scale, rotation,
                displayInfo, viewportInfo
            ).then(success => {
                if (!success) console.warn('[MMD] 自动保存位置失败');
            }).catch(error => {
                console.error('[MMD] 自动保存位置时出错:', error);
            });
        }
    }

    _debouncedSavePosition() {
        if (this._savePositionDebounceTimer) {
            clearTimeout(this._savePositionDebounceTimer);
        }
        this._savePositionDebounceTimer = setTimeout(() => {
            this._savePositionAfterInteraction().catch(error => {
                console.error('[MMD] 防抖保存位置时出错:', error);
            });
        }, 500);
    }

    dispose() {
        this.cleanupDragAndZoom();

        if (this._snapAnimationFrameId) {
            cancelAnimationFrame(this._snapAnimationFrameId);
            this._snapAnimationFrameId = null;
        }

        if (this._savePositionDebounceTimer) {
            clearTimeout(this._savePositionDebounceTimer);
            this._savePositionDebounceTimer = null;
        }

        this._cachedScreenBounds = null;
    }
}
