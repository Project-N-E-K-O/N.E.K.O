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
            } else if (e.button === 2) { // 右键 - 相机旋转
                this.isDragging = true;
                this.dragMode = 'orbit';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'crosshair';
                e.preventDefault();
                e.stopPropagation();
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
                // 平移模型
                const mesh = this.manager.currentModel?.mesh;
                if (!mesh) return;

                const moveFactor = 0.05;
                mesh.position.x += dx * moveFactor;
                mesh.position.y -= dy * moveFactor;
            } else if (this.dragMode === 'orbit') {
                // 旋转相机
                const orbitSpeed = 0.005;
                const camera = this.manager.camera;
                const mesh = this.manager.currentModel?.mesh;
                if (!camera || !mesh) return;

                // 绕模型中心旋转
                const target = new THREE.Vector3();
                const box = new THREE.Box3().setFromObject(mesh);
                box.getCenter(target);

                const offset = camera.position.clone().sub(target);
                const spherical = new THREE.Spherical().setFromVector3(offset);
                spherical.theta -= dx * orbitSpeed;
                spherical.phi -= dy * orbitSpeed;
                spherical.phi = Math.max(0.1, Math.min(Math.PI - 0.1, spherical.phi));

                offset.setFromSpherical(spherical);
                camera.position.copy(target).add(offset);
                camera.lookAt(target);
            }
        };

        // 鼠标抬起
        this.mouseUpHandler = () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'default';
                this._restoreButtonPointerEvents();
            }
        };

        // 鼠标离开
        this.mouseLeaveHandler = () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'default';
                this._restoreButtonPointerEvents();
            }
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
        canvas.addEventListener('mousedown', this.mouseDownHandler);
        canvas.addEventListener('mousemove', this.dragHandler);
        canvas.addEventListener('mousemove', this.mouseHoverHandler);
        canvas.addEventListener('mouseup', this.mouseUpHandler);
        canvas.addEventListener('mouseleave', this.mouseLeaveHandler);
        canvas.addEventListener('wheel', this.wheelHandler, { passive: false });

        // 禁用右键菜单
        canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    }

    // ═══════════════════ 清理 ═══════════════════

    cleanupDragAndZoom() {
        const canvas = this.manager.renderer?.domElement;
        if (!canvas) return;

        if (this.mouseDownHandler) canvas.removeEventListener('mousedown', this.mouseDownHandler);
        if (this.dragHandler) canvas.removeEventListener('mousemove', this.dragHandler);
        if (this.mouseHoverHandler) canvas.removeEventListener('mousemove', this.mouseHoverHandler);
        if (this.mouseUpHandler) canvas.removeEventListener('mouseup', this.mouseUpHandler);
        if (this.mouseLeaveHandler) canvas.removeEventListener('mouseleave', this.mouseLeaveHandler);
        if (this.wheelHandler) canvas.removeEventListener('wheel', this.wheelHandler);

        this.mouseDownHandler = null;
        this.dragHandler = null;
        this.mouseHoverHandler = null;
        this.mouseUpHandler = null;
        this.mouseLeaveHandler = null;
        this.wheelHandler = null;
    }

    dispose() {
        this.cleanupDragAndZoom();

        if (this._snapAnimationFrameId) {
            cancelAnimationFrame(this._snapAnimationFrameId);
            this._snapAnimationFrameId = null;
        }

        this._cachedScreenBounds = null;
    }
}
