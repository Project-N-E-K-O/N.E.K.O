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

                // 缓存模型包围盒中心作为旋转轴心
                const mesh = this.manager.currentModel?.mesh;
                if (mesh) {
                    const box = new THREE.Box3().setFromObject(mesh);
                    this._orbitPivot = box.getCenter(new THREE.Vector3());
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
                // 绕模型中心旋转模型本身（不移动相机）
                const mesh = this.manager.currentModel?.mesh;
                if (!mesh) return;

                const pivot = this._orbitPivot || mesh.position;
                const rotateSpeed = 0.005;

                // 构建增量旋转四元数（世界空间：dx→绕Y轴，dy→绕X轴）
                const dQuat = new THREE.Quaternion();
                const dEuler = new THREE.Euler(dy * rotateSpeed, dx * rotateSpeed, 0, 'YXZ');
                dQuat.setFromEuler(dEuler);

                // 将 mesh 位置绕轴心旋转，保持模型中心不动
                const offset = mesh.position.clone().sub(pivot);
                offset.applyQuaternion(dQuat);
                mesh.position.copy(pivot).add(offset);

                // 应用旋转（premultiply = 世界空间叠加）
                mesh.quaternion.premultiply(dQuat);
            }
        };

        // 鼠标抬起
        this.mouseUpHandler = () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'default';
                this._restoreButtonPointerEvents();

                // 拖拽结束后保存位置/旋转/缩放
                this._savePositionAfterInteraction();
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

        // 鼠标悬停光标
        this.mouseHoverHandler = (e) => {
            if (this.isDragging) return;
            if (this._hitTestModel(e.clientX, e.clientY)) {
                canvas.style.cursor = 'pointer';
            } else {
                canvas.style.cursor = 'default';
            }
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
    }

    // ═══════════════════ 偏好保存 ═══════════════════

    async _savePositionAfterInteraction() {
        if (!this.manager.currentModel || !this.manager.currentModel.url) return;

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
                this.manager.currentModel.url,
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
