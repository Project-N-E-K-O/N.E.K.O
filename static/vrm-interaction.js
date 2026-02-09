/**
 * VRM 交互模块
 * 负责拖拽、缩放、鼠标跟踪等交互功能
 */

// 确保 THREE 可用（只从全局对象读取，避免 TDZ ReferenceError）
// 使用 var 避免重复声明错误，或检查是否已存在
var THREE = (typeof window !== 'undefined' && window.THREE) || (typeof globalThis !== 'undefined' && globalThis.THREE) || null;
if (!THREE) {
    console.error('[VRM Interaction] THREE.js 未加载，交互功能将不可用');
}

class VRMInteraction {
    constructor(manager) {
        this.manager = manager;

        // 拖拽和缩放相关
        this.isDragging = false;
        this.dragMode = null;
        this.previousMousePosition = { x: 0, y: 0 };
        this.isLocked = false;
        this._isInitializingDragAndZoom = false;
        this._initTimerId = null;
        this._initRetryCount = 0;
        this._maxInitRetries = 50; // 最多重试50次（约5秒）

        // 拖拽相关事件处理器引用（用于清理）
        this.mouseDownHandler = null;
        this.mouseUpHandler = null;
        this.mouseLeaveHandler = null;
        this.auxClickHandler = null;
        this.mouseEnterHandler = null;
        this.dragHandler = null;
        this.wheelHandler = null;

        // 鼠标跟踪相关
        this.mouseTrackingEnabled = false;
        this.mouseMoveHandler = null;

        // 开启"始终面朝相机" 
        this.enableFaceCamera = true;

        // 浮动按钮鼠标跟踪缓存（用于性能优化）
        this._cachedBox = null;
        this._cachedCorners = null;
        this._cachedScreenBounds = null; // { minX, maxX, minY, maxY }
        this._floatingButtonsPendingFrame = null; // RAF ID，用于取消
        this._lastModelUpdateTime = 0;

        // 出界回弹配置（与聊天框风格一致）
        this._snapConfig = {
            duration: 260,
            easingType: 'easeOutBack'
        };
        this._snapAnimationFrameId = null;
        this._isSnappingModel = false;
        this._snapResolve = null;
    }


    /**
     * 使用 live2d-ui-drag.js 中的共享工具函数（按钮 pointer-events 管理）
     */
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

    /**
     * 【修改】初始化拖拽和缩放功能
     * 已移除所有导致报错的 LookAt/mouseNDC 代码
     */
    initDragAndZoom() {
        if (!this.manager.renderer) return;

        // 如果已经在等待初始化，直接返回（防止重复定时器）
        if (this._isInitializingDragAndZoom) {
            return;
        }

        // 确保 camera 已初始化
        if (!this.manager.camera) {
            // 设置标记位，防止重复触发
            this._isInitializingDragAndZoom = true;
            // 清除之前的定时器（如果存在）
            if (this._initTimerId !== null) {
                clearTimeout(this._initTimerId);
            }
            // 设置新的定时器
            this._initTimerId = setTimeout(() => {
                this._isInitializingDragAndZoom = false;
                this._initTimerId = null;
                this._initRetryCount++;
                if (this._initRetryCount >= this._maxInitRetries) {
                    console.warn('[VRM Interaction] 相机初始化超时，放弃拖拽和缩放功能');
                    return;
                }
                if (this.manager.camera) {
                    this.initDragAndZoom();
                }
            }, 100);
            return;
        }

        // camera 已就绪，清除标记位和定时器
        this._isInitializingDragAndZoom = false;
        if (this._initTimerId !== null) {
            clearTimeout(this._initTimerId);
            this._initTimerId = null;
        }

        const canvas = this.manager.renderer.domElement;
        if (!THREE) {
            console.error('[VRM Interaction] THREE.js 未加载，无法初始化拖拽和缩放');
            return;
        }

        // 先清理旧的事件监听器
        this.cleanupDragAndZoom();

        // 1. 鼠标按下
        this.mouseDownHandler = (e) => {
            if (this.checkLocked()) return;

            // 如果正在回弹动画，优先取消，避免拖拽冲突
            if (this._snapAnimationFrameId) {
                cancelAnimationFrame(this._snapAnimationFrameId);
                this._snapAnimationFrameId = null;
                if (this._snapResolve) {
                    this._snapResolve(false);
                    this._snapResolve = null;
                }
                this._isSnappingModel = false;
            }

            if (e.button === 0 || e.button === 1) { // 左键或中键
                this.isDragging = true;
                this.dragMode = 'pan';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'move';
                e.preventDefault();
                e.stopPropagation();

                // 开始拖动时，临时禁用按钮的 pointer-events
                this._disableButtonPointerEvents();
            }
        };

        // 2. 鼠标移动 (核心拖拽逻辑)
        this.dragHandler = (e) => {
            if (this.checkLocked()) {
                if (this.isDragging) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.isDragging = false;
                    this.dragMode = null;
                    canvas.style.cursor = 'grab';
                    // 恢复按钮的 pointer-events
                    this._restoreButtonPointerEvents();
                }
                return;
            }

            if (!this.isDragging || !this.manager.currentModel) return;

            const deltaX = e.clientX - this.previousMousePosition.x;
            const deltaY = e.clientY - this.previousMousePosition.y;

            if (this.dragMode === 'pan' && this.manager.currentModel && this.manager.currentModel.scene) {
                // 动态计算平移速度：根据相机距离和FOV，使鼠标移动距离与屏幕上模型移动距离同步
                // 这样无论缩放级别如何，鼠标移动100像素，模型在屏幕上也移动100像素
                const camera = this.manager.camera;
                const renderer = this.manager.renderer;

                // 计算相机到模型中心的距离
                const modelCenter = this.manager.currentModel.scene.position.clone();
                const cameraDistance = camera.position.distanceTo(modelCenter);

                // 计算在当前距离下，屏幕视口对应的世界空间尺寸
                const fov = camera.fov * (Math.PI / 180); // 转换为弧度
                const screenHeight = renderer.domElement.clientHeight;
                const screenWidth = renderer.domElement.clientWidth;

                // 在相机距离处，视口的世界空间高度
                const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
                // 根据宽高比计算世界空间宽度
                const worldWidth = worldHeight * (screenWidth / screenHeight);

                // 计算每像素对应的世界空间距离
                const pixelToWorldX = worldWidth / screenWidth;
                const pixelToWorldY = worldHeight / screenHeight;

                const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
                const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);

                // 计算新位置：鼠标移动的像素 × 每像素对应的世界空间距离
                const newPosition = this.manager.currentModel.scene.position.clone();
                newPosition.add(right.multiplyScalar(deltaX * pixelToWorldX));
                newPosition.add(up.multiplyScalar(-deltaY * pixelToWorldY));

                // 使用边界限制
                const finalPosition = this.clampModelPosition(newPosition);

                // 应用位置（按钮和锁图标位置由 _startUIUpdateLoop 自动更新）
                this.manager.currentModel.scene.position.copy(finalPosition);
            }

            this.previousMousePosition = { x: e.clientX, y: e.clientY };
        };

        // 3. 鼠标释放
        this.mouseUpHandler = async (e) => {
            if (this.isDragging) {
                e.preventDefault();
                e.stopPropagation();
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'grab';

                // 拖拽结束后恢复按钮的 pointer-events
                this._restoreButtonPointerEvents();

                // 拖拽结束后：若超出屏幕范围，执行回弹
                await this._snapModelIntoScreen({ animate: true });

                // 拖动结束后保存位置（包含回弹后的位置）
                await this._savePositionAfterInteraction();
            }
        };

        // 5. 鼠标进入
        this.mouseEnterHandler = () => {
            canvas.style.cursor = 'grab';
        };

        // 6. 滚轮缩放
        this.wheelHandler = (e) => {
            if (this.checkLocked() || !this.manager.currentModel) return;

            // 检查事件目标是否是 canvas 或其子元素，如果不是则不拦截事件（允许聊天区域正常滚动）
            const canvasEl = this.manager.renderer?.domElement;
            if (!canvasEl) return;

            const target = e.target;
            // 检查目标是否是 canvas 本身或其子元素
            const isCanvasOrDescendant = target === canvasEl || canvasEl.contains(target);

            // 只有当事件发生在 canvas 或其子元素上时，才拦截事件
            if (!isCanvasOrDescendant) {
                return; // 不拦截，允许事件继续传播到聊天区域
            }

            e.preventDefault();
            e.stopPropagation();

            if (!THREE) {
                console.error('[VRM Interaction] THREE.js 未加载，无法处理滚轮缩放');
                return;
            }

            const delta = e.deltaY;
            const zoomSpeed = 0.05;
            const zoomFactor = delta > 0 ? (1 + zoomSpeed) : (1 - zoomSpeed);

            if (this.manager.currentModel.scene && this.manager.camera) {
                const modelCenter = new THREE.Vector3();
                if (this.manager.controls) {
                    modelCenter.copy(this.manager.controls.target);
                } else {
                    this.manager.currentModel.scene.getWorldPosition(modelCenter);
                    modelCenter.y += 1.0;
                }

                const oldDistance = this.manager.camera.position.distanceTo(modelCenter);
                const minDist = 0.5;  // 限制最小距离，防止放大后移动时只能看到腿
                const maxDist = 20.0;

                let newDistance = oldDistance * zoomFactor;
                newDistance = Math.max(minDist, Math.min(maxDist, newDistance));

                const direction = new THREE.Vector3()
                    .subVectors(this.manager.camera.position, modelCenter)
                    .normalize();

                this.manager.camera.position.copy(modelCenter)
                    .add(direction.multiplyScalar(newDistance));

                if (this.manager.controls && this.manager.controls.update) {
                    this.manager.controls.update();
                }

                // 缩放结束后防抖保存位置
                this._debouncedSavePosition();
            }
        };

        this.auxClickHandler = (e) => {
            if (e.button === 1) { e.preventDefault(); e.stopPropagation(); }
        };

        // 绑定事件
        canvas.addEventListener('mousedown', this.mouseDownHandler);
        document.addEventListener('mousemove', this.dragHandler); // 绑定到 document 以支持拖出画布
        document.addEventListener('mouseup', this.mouseUpHandler);
        canvas.addEventListener('mouseenter', this.mouseEnterHandler);
        // 保存 wheel 监听器选项，确保添加和移除时使用相同的选项
        this._wheelListenerOptions = { passive: false, capture: true };
        canvas.addEventListener('wheel', this.wheelHandler, this._wheelListenerOptions);
        canvas.addEventListener('auxclick', this.auxClickHandler);


    }
    /**
     * 【新增】让模型身体始终朝向相机
     * 消除透视带来的“侧身”感，让平移看起来像 2D 移动
     */
    _updateModelFacing(delta) {
        if (!this.enableFaceCamera) return;
        if (!this.manager.currentModel || !this.manager.currentModel.scene || !this.manager.camera) return;

        const model = this.manager.currentModel.scene;
        const camera = this.manager.camera;

        // 1. 计算向量 (忽略 Y 轴)
        const dx = camera.position.x - model.position.x;
        const dz = camera.position.z - model.position.z;

        // 2. 计算目标角度
        // VRM 默认朝向 +Z，atan2(x, z) 对应 Y 轴旋转
        let targetAngle = Math.atan2(dx, dz);

        // 3. 平滑插值处理角度突变
        const currentAngle = model.rotation.y;
        let diff = targetAngle - currentAngle;

        while (diff > Math.PI) diff -= Math.PI * 2;
        while (diff < -Math.PI) diff += Math.PI * 2;

        // 4. 应用旋转 (速度可调)
        const rotateSpeed = 10.0;
        if (Math.abs(diff) > 0.001) {
            model.rotation.y += diff * rotateSpeed * delta;
        }
    }
    /**
     * 检查锁定状态（使用VRM管理器自己的锁定状态）
     * @returns {boolean} 是否锁定
     */
    checkLocked() {
        // 使用 VRM 管理器自己的锁定状态
        if (this.manager && typeof this.manager.isLocked !== 'undefined') {
            this.isLocked = this.manager.isLocked;
        }
        return this.isLocked;
    }

    /**
     * 每帧更新（由 VRMManager 驱动）
     */
    update(delta) {
        // 更新身体朝向（按钮位置由 _startUIUpdateLoop 处理）
        this._updateModelFacing(delta);
    }

    /**
     * 设置锁定状态
     */
    setLocked(locked) {
        this.isLocked = locked;
        if (this.manager) {
            this.manager.isLocked = locked;
        }

        // 不再修改 pointerEvents，改用逻辑拦截
        // 这样锁定时虽然不能移动/缩放，但依然可以点中模型弹出菜单

        if (locked && this.isDragging) {
            this.isDragging = false;
            this.dragMode = null;
            if (this.manager.renderer) {
                this.manager.renderer.domElement.style.cursor = 'grab';
            }
            // 恢复按钮的 pointer-events
            this._restoreButtonPointerEvents();
        }
    }

    /**
     * 确保模型不会完全消失 - 只在极端情况下重置位置
     * @param {THREE.Vector3} position - 目标位置
     * @returns {THREE.Vector3} - 调整后的位置
     */
    ensureModelVisibility(position) {
        if (!THREE) {
            console.error('[VRM Interaction] THREE.js 未加载，无法确保模型可见性');
            return position;
        }

        // 如果模型移动得太远（超出20个单位），重置到原点
        const maxAllowedDistance = 20;
        const distanceFromOrigin = position.length();

        if (distanceFromOrigin > maxAllowedDistance) {
            return new THREE.Vector3(0, 0, 0);
        }

        return position;
    }

    /**
     * 清理拖拽和缩放相关事件监听器
     * 注意：如果事件监听器在添加时使用了选项（如 { capture: true, passive: false }），
     * 移除时必须使用相同的选项，否则 removeEventListener 不会生效
     */
    cleanupDragAndZoom() {
        if (!this.manager.renderer) return;

        // 清理初始化定时器（如果存在）
        if (this._initTimerId !== null) {
            clearTimeout(this._initTimerId);
            this._initTimerId = null;
        }
        this._isInitializingDragAndZoom = false;

        const canvas = this.manager.renderer.domElement;

        // 移除所有事件监听器
        // 注意：这些事件在添加时没有使用选项，所以移除时也不需要选项
        if (this.mouseDownHandler) {
            canvas.removeEventListener('mousedown', this.mouseDownHandler);
            this.mouseDownHandler = null;
        }
        if (this.dragHandler) {
            document.removeEventListener('mousemove', this.dragHandler);
            this.dragHandler = null;
        }
        if (this.mouseUpHandler) {
            document.removeEventListener('mouseup', this.mouseUpHandler);
            this.mouseUpHandler = null;
        }

        if (this.auxClickHandler) {
            canvas.removeEventListener('auxclick', this.auxClickHandler);
            this.auxClickHandler = null;
        }
        if (this.mouseEnterHandler) {
            canvas.removeEventListener('mouseenter', this.mouseEnterHandler);
            this.mouseEnterHandler = null;
        }
        if (this.wheelHandler) {
            // 移除时必须使用与添加时相同的选项，否则 removeEventListener 不会生效
            canvas.removeEventListener('wheel', this.wheelHandler, this._wheelListenerOptions || { capture: true });
            this.wheelHandler = null;
            this._wheelListenerOptions = null;
        }
    }

    /**
     * 【基于可见像素的边界限制】
     * 
     * 计算模型包围盒在屏幕上的可见区域，只在可见像素小于阈值时才进行校正。
     * 这样无论模型放多大，只要屏幕上还能看到足够的部分，就不会强制限制位置。
     **/
    clampModelPosition(position) {
        if (!this.manager.camera || !this.manager.renderer || !this.manager.currentModel?.vrm) {
            return position;
        }

        if (!THREE) {
            console.error('[VRM Interaction] THREE.js 未加载，无法限制模型位置');
            return position;
        }

        const camera = this.manager.camera;
        const renderer = this.manager.renderer;
        const vrm = this.manager.currentModel.vrm;

        // 最小可见像素阈值（低于此值才会校正），与 Live2D 保持一致
        const MIN_VISIBLE_PIXELS = 50;

        try {
            // 1. 临时将模型移动到目标位置，计算包围盒
            const originalPosition = vrm.scene.position.clone();
            vrm.scene.position.copy(position);
            vrm.scene.updateMatrixWorld(true);

            // 2. 计算模型在目标位置的包围盒
            const box = new THREE.Box3().setFromObject(vrm.scene);

            // 恢复原始位置
            vrm.scene.position.copy(originalPosition);
            vrm.scene.updateMatrixWorld(true);

            // 3. 获取包围盒的 8 个顶点并投影到屏幕空间
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

            const canvasRect = renderer.domElement.getBoundingClientRect();
            const screenWidth = canvasRect.width;
            const screenHeight = canvasRect.height;

            // 计算模型在屏幕上的边界框
            let modelMinX = Infinity, modelMaxX = -Infinity;
            let modelMinY = Infinity, modelMaxY = -Infinity;

            corners.forEach(corner => {
                const projected = corner.clone().project(camera);
                const screenX = (projected.x * 0.5 + 0.5) * screenWidth;
                const screenY = (-projected.y * 0.5 + 0.5) * screenHeight;
                modelMinX = Math.min(modelMinX, screenX);
                modelMaxX = Math.max(modelMaxX, screenX);
                modelMinY = Math.min(modelMinY, screenY);
                modelMaxY = Math.max(modelMaxY, screenY);
            });

            // 4. 计算模型在屏幕内的可见区域
            const visibleMinX = Math.max(0, modelMinX);
            const visibleMaxX = Math.min(screenWidth, modelMaxX);
            const visibleMinY = Math.max(0, modelMinY);
            const visibleMaxY = Math.min(screenHeight, modelMaxY);

            const visibleWidth = Math.max(0, visibleMaxX - visibleMinX);
            const visibleHeight = Math.max(0, visibleMaxY - visibleMinY);
            const visiblePixels = visibleWidth * visibleHeight;

            // 5. 如果可见区域足够大，不做任何限制
            if (visiblePixels >= MIN_VISIBLE_PIXELS) {
                return position;
            }

            // 6. 可见区域太小，需要将模型拉回
            // 计算需要移动的方向和距离，使模型至少有 MIN_VISIBLE_PIXELS 可见
            const modelCenterX = (modelMinX + modelMaxX) / 2;
            const modelCenterY = (modelMinY + modelMaxY) / 2;
            const screenCenterX = screenWidth / 2;
            const screenCenterY = screenHeight / 2;

            // 计算需要移动的屏幕像素
            let moveX = 0, moveY = 0;

            // 如果模型完全在屏幕左侧外
            if (modelMaxX < MIN_VISIBLE_PIXELS) {
                moveX = MIN_VISIBLE_PIXELS - modelMaxX;
            }
            // 如果模型完全在屏幕右侧外
            else if (modelMinX > screenWidth - MIN_VISIBLE_PIXELS) {
                moveX = (screenWidth - MIN_VISIBLE_PIXELS) - modelMinX;
            }

            // 如果模型完全在屏幕上方外
            if (modelMaxY < MIN_VISIBLE_PIXELS) {
                moveY = MIN_VISIBLE_PIXELS - modelMaxY;
            }
            // 如果模型完全在屏幕下方外
            else if (modelMinY > screenHeight - MIN_VISIBLE_PIXELS) {
                moveY = (screenHeight - MIN_VISIBLE_PIXELS) - modelMinY;
            }

            // 7. 将屏幕像素移动距离转换为世界空间距离
            const modelCenter = position.clone();
            const cameraDistance = camera.position.distanceTo(modelCenter);
            const fov = camera.fov * (Math.PI / 180);
            const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
            const worldWidth = worldHeight * (screenWidth / screenHeight);

            const pixelToWorldX = worldWidth / screenWidth;
            const pixelToWorldY = worldHeight / screenHeight;

            const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
            const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);

            const correctedPos = position.clone();
            correctedPos.add(right.multiplyScalar(moveX * pixelToWorldX));
            correctedPos.add(up.multiplyScalar(-moveY * pixelToWorldY)); // Y 轴反向

            return correctedPos;

        } catch (error) {
            console.warn('[VRM Interaction] 边界检测失败，跳过限制:', error);
            return position;
        }
    }

    /**
     * 获取回弹缓动函数
     */
    _getSnapEasingFunction() {
        const easingType = this._snapConfig?.easingType || 'easeOutBack';

        const easingMap = {
            easeOutBack: (t) => {
                const c1 = 1.70158;
                const c3 = c1 + 1;
                return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
            },
            easeOutCubic: (t) => (--t) * t * t + 1
        };

        return easingMap[easingType] || easingMap.easeOutCubic;
    }

    /**
     * 执行模型回弹动画
     */
    _animateModelToPosition(startPosition, targetPosition) {
        if (!this.manager.currentModel?.scene) {
            return Promise.resolve(false);
        }

        if (!Number.isFinite(targetPosition?.x) || !Number.isFinite(targetPosition?.y) || !Number.isFinite(targetPosition?.z)) {
            return Promise.resolve(false);
        }

        if (this._snapAnimationFrameId) {
            cancelAnimationFrame(this._snapAnimationFrameId);
            this._snapAnimationFrameId = null;
            if (this._snapResolve) {
                this._snapResolve(false);
                this._snapResolve = null;
            }
            this._isSnappingModel = false;
        }

        const duration = this._snapConfig?.duration || 260;
        const easingFn = this._getSnapEasingFunction();
        const startTime = performance.now();
        const scene = this.manager.currentModel.scene;

        this._isSnappingModel = true;

        return new Promise((resolve) => {
            this._snapResolve = resolve;
            const animate = (currentTime) => {
                const elapsed = currentTime - startTime;
                const progress = Math.min(elapsed / duration, 1);
                const eased = easingFn(progress);

                const newX = startPosition.x + (targetPosition.x - startPosition.x) * eased;
                const newY = startPosition.y + (targetPosition.y - startPosition.y) * eased;
                const newZ = startPosition.z + (targetPosition.z - startPosition.z) * eased;

                scene.position.set(newX, newY, newZ);

                if (progress < 1) {
                    this._snapAnimationFrameId = requestAnimationFrame(animate);
                } else {
                    scene.position.copy(targetPosition);
                    this._isSnappingModel = false;
                    this._snapAnimationFrameId = null;
                    this._snapResolve = null;
                    resolve(true);
                }
            };

            this._snapAnimationFrameId = requestAnimationFrame(animate);
        });
    }

    /**
     * 出界回弹：保持原有边界检查逻辑不变，仅在需要时执行回弹动画
     */
    async _snapModelIntoScreen({ animate = true } = {}) {
        if (this._isSnappingModel) return false;
        if (!this.manager.currentModel?.scene || !this.manager.camera || !this.manager.renderer) return false;
        if (!THREE) return false;

        const scene = this.manager.currentModel.scene;
        const startPosition = scene.position.clone();

        // 使用原有的边界检查逻辑计算目标位置
        const targetPosition = this.clampModelPosition(startPosition.clone());

        if (!targetPosition || !targetPosition.isVector3) {
            return false;
        }

        const distance = startPosition.distanceTo(targetPosition);
        if (!Number.isFinite(distance) || distance < 0.0001) {
            return false;
        }

        if (!animate) {
            scene.position.copy(targetPosition);
            return true;
        }

        return await this._animateModelToPosition(startPosition, targetPosition);
    }


    /**
     * 启用/禁用鼠标跟踪（用于控制浮动按钮显示/隐藏）
     */
    enableMouseTracking(enabled) {
        this.mouseTrackingEnabled = enabled;

        // 确保拖拽和缩放功能已初始化
        if (enabled && (!this.mouseDownHandler || !this.dragHandler || !this.wheelHandler)) {
            this.initDragAndZoom();
        }

        if (enabled) {
            this.setupFloatingButtonsMouseTracking();
        } else {
            this.cleanupFloatingButtonsMouseTracking();
        }
    }

    /**
     * 更新模型包围盒和屏幕边界缓存（在模型或骨骼更新时调用）
     * 这个方法应该被外部调用，例如在模型加载、动画更新或骨骼变化时
     */
    updateModelBoundsCache() {
        if (!this.manager.currentModel?.vrm || !this.manager.camera || !this.manager.renderer || !THREE) {
            this._cachedBox = null;
            this._cachedCorners = null;
            this._cachedScreenBounds = null;
            return;
        }

        try {
            const vrm = this.manager.currentModel.vrm;
            const camera = this.manager.camera;
            const renderer = this.manager.renderer;

            // 计算模型在屏幕上的包围盒
            this._cachedBox = new THREE.Box3().setFromObject(vrm.scene);
            this._cachedCorners = [
                new THREE.Vector3(this._cachedBox.min.x, this._cachedBox.min.y, this._cachedBox.min.z),
                new THREE.Vector3(this._cachedBox.max.x, this._cachedBox.min.y, this._cachedBox.min.z),
                new THREE.Vector3(this._cachedBox.min.x, this._cachedBox.max.y, this._cachedBox.min.z),
                new THREE.Vector3(this._cachedBox.max.x, this._cachedBox.max.y, this._cachedBox.min.z),
                new THREE.Vector3(this._cachedBox.min.x, this._cachedBox.min.y, this._cachedBox.max.z),
                new THREE.Vector3(this._cachedBox.max.x, this._cachedBox.min.y, this._cachedBox.max.z),
                new THREE.Vector3(this._cachedBox.min.x, this._cachedBox.max.y, this._cachedBox.max.z),
                new THREE.Vector3(this._cachedBox.max.x, this._cachedBox.max.y, this._cachedBox.max.z),
            ];

            // 投影到屏幕空间并计算边界
            const canvasRect = renderer.domElement.getBoundingClientRect();
            let minX = Infinity, maxX = -Infinity;
            let minY = Infinity, maxY = -Infinity;

            this._cachedCorners.forEach(corner => {
                const worldPos = corner.clone();
                worldPos.project(camera);
                const screenX = (worldPos.x * 0.5 + 0.5) * canvasRect.width + canvasRect.left;
                const screenY = (-worldPos.y * 0.5 + 0.5) * canvasRect.height + canvasRect.top;
                minX = Math.min(minX, screenX);
                maxX = Math.max(maxX, screenX);
                minY = Math.min(minY, screenY);
                maxY = Math.max(maxY, screenY);
            });

            this._cachedScreenBounds = { minX, maxX, minY, maxY };
            this._lastModelUpdateTime = Date.now();
        } catch (error) {
            console.warn('[VRM Interaction] 更新模型边界缓存失败:', error);
            this._cachedBox = null;
            this._cachedCorners = null;
            this._cachedScreenBounds = null;
        }
    }

    /**
     * 设置浮动按钮的鼠标跟踪
     */
    setupFloatingButtonsMouseTracking() {
        if (!this.manager.renderer || !this.manager.currentModel) return;

        const canvas = this.manager.renderer.domElement;

        // 只查找 VRM 专用 ID
        let buttonsContainer = document.getElementById('vrm-floating-buttons');

        if (!buttonsContainer) return;

        // 初始化缓存
        this.updateModelBoundsCache();

        // 清除之前的定时器和 RAF
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }
        if (this._floatingButtonsPendingFrame !== null) {
            cancelAnimationFrame(this._floatingButtonsPendingFrame);
            this._floatingButtonsPendingFrame = null;
        }

        // 辅助函数：显示按钮并更新位置
        const showButtons = () => {
            if (this.checkLocked()) return;

            // 重新获取按钮容器（防止引用失效）
            const currentButtonsContainer = document.getElementById('vrm-floating-buttons');
            if (!currentButtonsContainer) return;

            if (window.live2dManager) {
                window.live2dManager.isFocusing = true;
            }

            // 显示浮动按钮（位置由 _startUIUpdateLoop 自动更新）
            currentButtonsContainer.style.display = 'flex';

            // 鼠标靠近时显示锁图标
            const lockIcon = document.getElementById('vrm-lock-icon');
            if (lockIcon) {
                lockIcon.style.display = 'block';
            }

            // 清除隐藏定时器（按钮显示时不需要隐藏）
            if (this._hideButtonsTimer) {
                clearTimeout(this._hideButtonsTimer);
                this._hideButtonsTimer = null;
            }
        };

        // 辅助函数：使用缓存计算鼠标到模型的距离
        const calculateDistanceToModel = (mouseX, mouseY) => {
            if (!this._cachedScreenBounds) {
                // 缓存未就绪，返回一个很大的距离
                return Infinity;
            }

            const { minX, maxX, minY, maxY } = this._cachedScreenBounds;
            // 计算鼠标到模型包围盒的距离
            const dx = Math.max(minX - mouseX, 0, mouseX - maxX);
            const dy = Math.max(minY - mouseY, 0, mouseY - maxY);
            return Math.sqrt(dx * dx + dy * dy);
        };

        // 辅助函数：启动隐藏定时器（简化版本，使用缓存）
        const startHideTimer = (delay = 1000) => {
            if (this.checkLocked()) return;

            if (this._hideButtonsTimer) {
                clearTimeout(this._hideButtonsTimer);
                this._hideButtonsTimer = null;
            }

            this._hideButtonsTimer = setTimeout(() => {
                // 检查鼠标是否在锁图标或按钮上
                const lockIcon = document.getElementById('vrm-lock-icon');
                let isMouseOverLock = false;
                if (lockIcon && lockIcon.style.display === 'block') {
                    const lockRect = lockIcon.getBoundingClientRect();
                    const mouseX = this._lastMouseX || 0;
                    const mouseY = this._lastMouseY || 0;
                    isMouseOverLock = mouseX >= lockRect.left && mouseX <= lockRect.right &&
                        mouseY >= lockRect.top && mouseY <= lockRect.bottom;
                }

                if (this._isMouseOverButtons || isMouseOverLock) {
                    this._hideButtonsTimer = null;
                    startHideTimer(delay);
                    return;
                }

                // 使用缓存计算距离（避免重复的 Box3 计算）
                const mouseX = this._lastMouseX || 0;
                const mouseY = this._lastMouseY || 0;
                const distance = calculateDistanceToModel(mouseX, mouseY);
                const threshold = 150;

                if (distance < threshold) {
                    // 鼠标仍在模型附近，重新启动定时器
                    this._hideButtonsTimer = null;
                    startHideTimer(delay);
                    return;
                }

                // 鼠标不在模型附近，隐藏按钮
                if (window.live2dManager) {
                    window.live2dManager.isFocusing = false;
                }

                const currentButtonsContainer = document.getElementById('vrm-floating-buttons');
                if (currentButtonsContainer) {
                    currentButtonsContainer.style.display = 'none';
                }

                if (lockIcon && !lockIcon.dataset.clickProtection) {
                    lockIcon.style.display = 'none';
                }

                this._hideButtonsTimer = null;
            }, delay);
        };

        const onMouseEnter = () => showButtons();


        // RAF 回调：执行昂贵的 Box3 和投影计算
        const performExpensiveCalculation = () => {
            this._floatingButtonsPendingFrame = null;

            if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;
            if (this.checkLocked()) return;
            if (!this.manager.renderer || !this.manager.camera) return;

            // 更新缓存（如果模型已更新）
            const now = Date.now();
            // 每 100ms 更新一次缓存（避免过于频繁）
            if (!this._cachedScreenBounds || (now - this._lastModelUpdateTime) > 100) {
                this.updateModelBoundsCache();
            }

            const mouseX = this._lastMouseX || 0;
            const mouseY = this._lastMouseY || 0;

            // 检查鼠标是否在按钮或锁图标上
            const currentButtonsContainer = document.getElementById('vrm-floating-buttons');
            let isOverButtons = false;
            if (currentButtonsContainer && currentButtonsContainer.style.display === 'flex') {
                const buttonsRect = currentButtonsContainer.getBoundingClientRect();
                isOverButtons = mouseX >= buttonsRect.left && mouseX <= buttonsRect.right &&
                    mouseY >= buttonsRect.top && mouseY <= buttonsRect.bottom;
            }

            let isOverLock = false;
            const lockIcon = document.getElementById('vrm-lock-icon');
            if (lockIcon && lockIcon.style.display === 'block') {
                const lockRect = lockIcon.getBoundingClientRect();
                isOverLock = mouseX >= lockRect.left && mouseX <= lockRect.right &&
                    mouseY >= lockRect.top && mouseY <= lockRect.bottom;
            }

            this._isMouseOverButtons = isOverButtons || isOverLock;

            // 如果鼠标在按钮或锁图标上，直接显示
            if (isOverButtons || isOverLock) {
                showButtons();
                return;
            }

            // 使用缓存计算距离（避免重复的 Box3 计算）
            const distance = calculateDistanceToModel(mouseX, mouseY);
            const threshold = 150;

            if (distance < threshold) {
                // 鼠标在模型附近，显示按钮
                showButtons();
            } else {
                // 鼠标不在模型附近，启动隐藏定时器
                startHideTimer();
            }
        };

        const onPointerMove = (event) => {
            if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;
            if (this.checkLocked()) return;
            if (!this.manager.renderer || !this.manager.camera) return;

            // 更新鼠标位置（轻量级操作）
            this._lastMouseX = event.clientX;
            this._lastMouseY = event.clientY;

            // 使用 RAF 节流昂贵的计算（避免每帧都计算 Box3 和投影）
            if (this._floatingButtonsPendingFrame === null) {
                this._floatingButtonsPendingFrame = requestAnimationFrame(performExpensiveCalculation);
            }
        };

        canvas.addEventListener('mouseenter', onMouseEnter);
        window.addEventListener('pointermove', onPointerMove);

        this._floatingButtonsMouseEnter = onMouseEnter;
        this._floatingButtonsPointerMove = onPointerMove;

        if (this.manager.currentModel && !this.checkLocked()) {
            setTimeout(() => {
                showButtons();
                // 不再隐藏按钮，保持一直显示
            }, 100);
        }
    }

    /**
     * 清理浮动按钮的鼠标跟踪
     */
    cleanupFloatingButtonsMouseTracking() {
        if (!this.manager.renderer) return;

        const canvas = this.manager.renderer.domElement;

        if (this._floatingButtonsMouseEnter) {
            canvas.removeEventListener('mouseenter', this._floatingButtonsMouseEnter);
            this._floatingButtonsMouseEnter = null;
        }
        if (this._floatingButtonsMouseLeave) {
            canvas.removeEventListener('mouseleave', this._floatingButtonsMouseLeave);
            this._floatingButtonsMouseLeave = null;
        }
        if (this._floatingButtonsPointerMove) {
            window.removeEventListener('pointermove', this._floatingButtonsPointerMove);
            this._floatingButtonsPointerMove = null;
        }
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }
        // 清理 RAF 标志
        if (this._floatingButtonsPendingFrame !== null) {
            cancelAnimationFrame(this._floatingButtonsPendingFrame);
            this._floatingButtonsPendingFrame = null;
        }
    }

    /**
     * 保存模型位置和状态到后端（交互结束后调用）
     */
    async _savePositionAfterInteraction() {
        if (!this.manager.currentModel || !this.manager.currentModel.url) {
            return;
        }

        const scene = this.manager.currentModel.scene;
        if (!scene) {
            return;
        }

        const position = {
            x: scene.position.x,
            y: scene.position.y,
            z: scene.position.z
        };

        const scale = {
            x: scene.scale.x,
            y: scene.scale.y,
            z: scene.scale.z
        };

        const rotation = {
            x: scene.rotation.x,
            y: scene.rotation.y,
            z: scene.rotation.z
        };

        // 验证数据有效性
        if (!Number.isFinite(position.x) || !Number.isFinite(position.y) || !Number.isFinite(position.z) ||
            !Number.isFinite(scale.x) || !Number.isFinite(scale.y) || !Number.isFinite(scale.z)) {
            console.warn('[VRM] 位置或缩放数据无效，跳过保存');
            return;
        }

        // 获取当前窗口所在显示器的信息（用于多屏幕位置恢复）
        let displayInfo = null;
        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay) {
                    let screenX = currentDisplay.screenX;
                    let screenY = currentDisplay.screenY;

                    // 如果 screenX/screenY 不存在，尝试从 bounds 获取
                    if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
                        if (currentDisplay.bounds &&
                            Number.isFinite(currentDisplay.bounds.x) &&
                            Number.isFinite(currentDisplay.bounds.y)) {
                            screenX = currentDisplay.bounds.x;
                            screenY = currentDisplay.bounds.y;
                        }
                    }

                    if (Number.isFinite(screenX) && Number.isFinite(screenY)) {
                        displayInfo = {
                            screenX: screenX,
                            screenY: screenY
                        };
                    }
                }
            } catch (error) {
                console.warn('[VRM] 获取显示器信息失败:', error);
            }
        }

        // 获取当前视口尺寸（用于跨分辨率缩放归一化）
        let viewportInfo = null;
        if (this.manager.renderer && this.manager.renderer.domElement) {
            const w = this.manager.renderer.domElement.clientWidth || window.innerWidth;
            const h = this.manager.renderer.domElement.clientHeight || window.innerHeight;
            if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
                viewportInfo = { width: w, height: h };
            }
        }

        // 异步保存，不阻塞交互
        if (this.manager.core && typeof this.manager.core.saveUserPreferences === 'function') {
            this.manager.core.saveUserPreferences(
                this.manager.currentModel.url,
                position,
                scale,
                rotation,
                displayInfo,
                viewportInfo
            ).then(success => {
                if (!success) {
                    console.warn('[VRM] 自动保存位置失败');
                }
            }).catch(error => {
                console.error('[VRM] 自动保存位置时出错:', error);
            });
        }
    }

    /**
     * 防抖动保存位置的辅助函数（用于滚轮缩放等连续操作）
     */
    _debouncedSavePosition() {
        // 清除之前的定时器
        if (this._savePositionDebounceTimer) {
            clearTimeout(this._savePositionDebounceTimer);
        }

        // 设置新的定时器，500ms后保存
        this._savePositionDebounceTimer = setTimeout(() => {
            this._savePositionAfterInteraction().catch(error => {
                console.error('[VRM] 防抖动保存位置时出错:', error);
            });
        }, 500);
    }

    /**
     * 清理交互资源
     */
    dispose() {
        this.enableMouseTracking(false);
        this.cleanupDragAndZoom();
        // 确保拖拽相关的 pointer-events 被恢复
        this._restoreButtonPointerEvents();
        // 确保初始化定时器被清理（即使 renderer 不存在）
        if (this._initTimerId !== null) {
            clearTimeout(this._initTimerId);
            this._initTimerId = null;
        }
        // 清理所有可能的定时器
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }

        // 清理位置保存防抖定时器
        if (this._savePositionDebounceTimer) {
            clearTimeout(this._savePositionDebounceTimer);
            this._savePositionDebounceTimer = null;
        }

        // 清理回弹动画
        if (this._snapAnimationFrameId) {
            cancelAnimationFrame(this._snapAnimationFrameId);
            this._snapAnimationFrameId = null;
        }
        if (this._snapResolve) {
            this._snapResolve(false);
            this._snapResolve = null;
        }
        this._isSnappingModel = false;

        // 重置状态
        this.isDragging = false;
        this.dragMode = null;
        this.isLocked = false;
    }
}

// 导出到全局
window.VRMInteraction = VRMInteraction;

