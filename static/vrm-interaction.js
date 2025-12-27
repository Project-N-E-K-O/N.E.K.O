/**
 * VRM 交互模块
 * 负责拖拽、缩放、鼠标跟踪等交互功能
 */

class VRMInteraction {
    constructor(manager) {
        this.manager = manager;
        
        // 拖拽和缩放相关
        this.isDragging = false;
        this.dragMode = null; // 'rotate' 或 'pan'
        this.previousMousePosition = { x: 0, y: 0 };
        this.isLocked = false; // 锁定状态，锁定时不响应拖拽和缩放
        
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
    }
    
    /**
     * 初始化拖拽和缩放功能
     */
    initDragAndZoom() {
        if (!this.manager.renderer) {
            console.warn('[VRM Interaction] renderer 未初始化，无法初始化拖拽和缩放');
            return;
        }
        
        // 确保 camera 已初始化（controls 可能不存在，但 camera 必须存在）
        if (!this.manager.camera) {
            console.warn('[VRM Interaction] camera 未初始化，延迟初始化拖拽和缩放');
            // 延迟重试
            setTimeout(() => {
                if (this.manager.camera) {
                    this.initDragAndZoom();
                } else {
                    console.error('[VRM Interaction] 延迟初始化失败：camera 仍未初始化');
                }
            }, 100);
            return;
        }
        
        const canvas = this.manager.renderer.domElement;
        const THREE = window.THREE;

        // 先清理旧的事件监听器（如果存在），避免重复绑定
        this.cleanupDragAndZoom();

        // 鼠标按下事件
        this.mouseDownHandler = (e) => {
            // 检查锁定状态
            if (this.checkLocked()) {
                console.log('[VRM Interaction] 模型已锁定，忽略鼠标按下事件');
                return;
            }

            if (e.button === 0) { // 左键：平移（禁用旋转）
                this.isDragging = true;
                this.dragMode = 'pan';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'move';
                e.preventDefault();
                e.stopPropagation();
            } else if (e.button === 1) { // 中键：平移（备用）
                this.isDragging = true;
                this.dragMode = 'pan';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'move';
                e.preventDefault();
                e.stopPropagation();
            }
        };

        // 鼠标移动事件
        this.dragHandler = (e) => {
            // 如果锁定，停止拖拽
            if (this.checkLocked()) {
                if (this.isDragging) {
                    this.isDragging = false;
                    this.dragMode = null;
                    canvas.style.cursor = 'grab';
                }
                return;
            }

            if (!this.isDragging || !this.manager.currentModel) return;

            const deltaX = e.clientX - this.previousMousePosition.x;
            const deltaY = e.clientY - this.previousMousePosition.y;

            if (this.dragMode === 'pan' && this.manager.currentModel && this.manager.currentModel.scene) {
                // 平移模型（禁用旋转功能）
                const panSpeed = 0.01;
                const THREE = window.THREE;
                const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.manager.camera.quaternion);
                const up = new THREE.Vector3(0, 1, 0).applyQuaternion(this.manager.camera.quaternion);

                // 计算新的位置
                const newPosition = this.manager.currentModel.scene.position.clone();
                newPosition.add(
                    right.multiplyScalar(deltaX * panSpeed)
                        .add(up.multiplyScalar(-deltaY * panSpeed))
                );

                // 使用简化的边界检查，避免复杂的屏幕投影计算
                let finalPosition = newPosition.clone();

                // 简单的距离限制（从原点）
                const maxDistance = 15.0; // 允许更大的移动范围
                if (finalPosition.length() > maxDistance) {
                    finalPosition.normalize().multiplyScalar(maxDistance);
                }

                // 轴向限制
                finalPosition.x = Math.max(-10.0, Math.min(10.0, finalPosition.x));
                finalPosition.y = Math.max(-8.0, Math.min(8.0, finalPosition.y));
                finalPosition.z = Math.max(-10.0, Math.min(10.0, finalPosition.z));

                // 应用最终位置
                this.manager.currentModel.scene.position.copy(finalPosition);

                // 模型移动时更新浮动按钮和锁图标位置
                const buttonsContainer = document.getElementById('live2d-floating-buttons');
                if (buttonsContainer && buttonsContainer.style.display === 'flex') {
                    this.updateFloatingButtonsPosition();
                }
                if (this.manager.core && typeof this.manager.core.updateLockIconPosition === 'function') {
                    this.manager.core.updateLockIconPosition();
                }
            }

            this.previousMousePosition = { x: e.clientX, y: e.clientY };
            e.preventDefault();
            e.stopPropagation();
        };

        // 鼠标释放事件
        this.mouseUpHandler = (e) => {
            if (this.isDragging) {
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'grab';
                e.preventDefault();
                e.stopPropagation();
            }
        };

        // 鼠标离开画布
        this.mouseLeaveHandler = () => {
            if (this.isDragging) {
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'grab';
            }
        };

        // 鼠标进入画布
        this.mouseEnterHandler = () => {
            canvas.style.cursor = 'grab';
        };

        // 滚轮缩放（使用 OrbitControls 的 zoom 功能）
        this.wheelHandler = (e) => {
            if (this.checkLocked() || !this.manager.currentModel) {
                e.preventDefault();
                e.stopPropagation();
                return;
            }
            
            e.preventDefault();
            e.stopPropagation();
            
            const THREE = window.THREE;
            const delta = e.deltaY;
            
            // 修复缩放方向：delta > 0 为滚轮向下，应该远离模型
            const zoomSpeed = 0.05;
            const zoomFactor = delta > 0 ? (1 + zoomSpeed) : (1 - zoomSpeed); 

            if (this.manager.currentModel.scene) {
                const modelCenter = new THREE.Vector3();
                this.manager.currentModel.scene.getWorldPosition(modelCenter);
                const oldDistance = this.manager.camera.position.distanceTo(modelCenter);
                
                // 设置合理的缩放限制 [1.0, 5.0]
                const clampedDistance = Math.max(1.0, Math.min(5.0, oldDistance * zoomFactor));

                const direction = new THREE.Vector3()
                    .subVectors(this.manager.camera.position, modelCenter)
                    .normalize();

                this.manager.camera.position.copy(modelCenter)
                    .add(direction.multiplyScalar(clampedDistance));
                
                if (this.manager.controls && this.manager.controls.update) {
                    this.manager.controls.update();
                }
            }
        };
        // 中键点击事件（防止默认行为）
        this.auxClickHandler = (e) => {
            if (e.button === 1) {
                e.preventDefault();
                e.stopPropagation();
            }
        };
        
        // 绑定事件

        // 先绑定主要事件处理器
        canvas.addEventListener('mousedown', this.mouseDownHandler);
        document.addEventListener('mousemove', this.dragHandler);
        document.addEventListener('mouseup', this.mouseUpHandler);
        canvas.addEventListener('mouseleave', this.mouseLeaveHandler);
        canvas.addEventListener('mouseenter', this.mouseEnterHandler);
        // 绑定滚轮事件 - 使用捕获阶段确保优先处理
        canvas.addEventListener('wheel', this.wheelHandler, { passive: false, capture: true });
        canvas.addEventListener('auxclick', this.auxClickHandler);


        
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
        // 如果开启了鼠标跟踪（浮动按钮跟随），则更新位置
        if (this.mouseTrackingEnabled) {
            this.updateFloatingButtonsPosition();
        }
    }

    /**
     * 设置锁定状态
     */
    setLocked(locked) {
        this.isLocked = locked;
        if (this.manager) {
            this.manager.isLocked = locked;
        }
        
        // ✅ 修复：不再修改 pointerEvents，改用逻辑拦截
        // 这样锁定时虽然不能移动/缩放，但依然可以点中模型弹出菜单
        
        if (locked && this.isDragging) {
            this.isDragging = false;
            this.dragMode = null;
            if (this.manager.renderer) {
                this.manager.renderer.domElement.style.cursor = 'grab';
            }
        }
    }
    
    /**
     * 确保模型不会完全消失 - 只在极端情况下重置位置
     * @param {THREE.Vector3} position - 目标位置
     * @returns {THREE.Vector3} - 调整后的位置
     */
    ensureModelVisibility(position) {
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
     */
    cleanupDragAndZoom() {
        if (!this.manager.renderer) return;
        
        const canvas = this.manager.renderer.domElement;
        
        // 移除所有事件监听器
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
        if (this.mouseLeaveHandler) {
            canvas.removeEventListener('mouseleave', this.mouseLeaveHandler);
            this.mouseLeaveHandler = null;
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
            canvas.removeEventListener('wheel', this.wheelHandler);
            this.wheelHandler = null;
        }
    }
    
    /**
     * 限制模型位置，确保不会移出屏幕范围
     * @param {THREE.Vector3} position - 模型的目标位置
     * @returns {THREE.Vector3} - 限制后的位置
     */
    clampModelPosition(position) {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || 
            !this.manager.camera || !this.manager.renderer) {
            return position;
        }
        
        const THREE = window.THREE;
        const vrm = this.manager.currentModel.vrm;
        const camera = this.manager.camera;
        const renderer = this.manager.renderer;
        
        // 获取模型的边界框
        const box = new THREE.Box3().setFromObject(vrm.scene);
        
        // 临时设置位置以计算屏幕坐标
        const originalPosition = vrm.scene.position.clone();
        vrm.scene.position.copy(position);
        
        // 计算模型边界框的8个顶点在屏幕上的投影
        const corners = [
            new THREE.Vector3(box.min.x, box.min.y, box.min.z),
            new THREE.Vector3(box.max.x, box.min.y, box.min.z),
            new THREE.Vector3(box.min.x, box.max.y, box.min.z),
            new THREE.Vector3(box.max.x, box.max.y, box.min.z),
            new THREE.Vector3(box.min.x, box.min.y, box.max.z),
            new THREE.Vector3(box.max.x, box.min.y, box.max.z),
            new THREE.Vector3(box.min.x, box.max.y, box.max.z),
            new THREE.Vector3(box.max.x, box.max.y, box.max.z)
        ];
        
        const screenCorners = corners.map(corner => {
            const worldCorner = corner.clone();
            worldCorner.applyMatrix4(vrm.scene.matrixWorld);
            const vector = worldCorner.project(camera);
            return {
                x: (vector.x * 0.5 + 0.5) * renderer.domElement.width,
                y: (-vector.y * 0.5 + 0.5) * renderer.domElement.height
            };
        });
        
        // 恢复原始位置
        vrm.scene.position.copy(originalPosition);
        
        // 计算屏幕边界 - 使用更宽松的限制
        const screenWidth = renderer.domElement.width;
        const screenHeight = renderer.domElement.height;
        // 使用更大的边距，允许模型在更广范围内移动
        const margin = Math.min(screenWidth, screenHeight) * 0.3; // 30%的屏幕尺寸作为边距
        
        // 找到所有顶点的最小和最大屏幕坐标
        const minX = Math.min(...screenCorners.map(c => c.x));
        const maxX = Math.max(...screenCorners.map(c => c.x));
        const minY = Math.min(...screenCorners.map(c => c.y));
        const maxY = Math.max(...screenCorners.map(c => c.y));
        
        // 计算需要调整的偏移量
        let offsetX = 0;
        let offsetY = 0;
        
        if (minX < margin) {
            offsetX = margin - minX;
        } else if (maxX > screenWidth - margin) {
            offsetX = (screenWidth - margin) - maxX;
        }
        
        if (minY < margin) {
            offsetY = margin - minY;
        } else if (maxY > screenHeight - margin) {
            offsetY = (screenHeight - margin) - maxY;
        }
        
        // 如果有偏移，需要将屏幕偏移转换回世界坐标
        if (offsetX !== 0 || offsetY !== 0) {
            // 将屏幕偏移转换为世界坐标偏移
            const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
            const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
            
            // 计算屏幕到世界的缩放因子（近似）
            const distance = camera.position.distanceTo(position);
            const fov = camera.fov * (Math.PI / 180);
            const worldScaleX = 2 * distance * Math.tan(fov / 2) / screenWidth;
            const worldScaleY = 2 * distance * Math.tan(fov / 2) / screenHeight;
            
            const worldOffset = right.multiplyScalar(offsetX * worldScaleX)
                .add(up.multiplyScalar(-offsetY * worldScaleY));
            
            return position.clone().add(worldOffset);
        }
        
        return position;
    }
    
    /**
     * 更新浮动按钮位置，使其跟随VRM模型
     */
    updateFloatingButtonsPosition() {
        const buttonsContainer = document.getElementById('live2d-floating-buttons');
        if (!buttonsContainer || !this.manager.currentModel || !this.manager.currentModel.vrm) {
            return;
        }

        try {
            const vrm = this.manager.currentModel.vrm;
            const camera = this.manager.camera;
            const renderer = this.manager.renderer;

            // 获取canvas在页面上的位置
            const canvasRect = renderer.domElement.getBoundingClientRect();

            // 获取模型的世界位置
            const modelWorldPos = new THREE.Vector3();
            vrm.scene.getWorldPosition(modelWorldPos);

            // 将世界坐标转换为屏幕坐标
            const worldVector = modelWorldPos.clone();
            worldVector.project(camera);

            // 计算在canvas上的坐标
            const canvasX = (worldVector.x * 0.5 + 0.5) * canvasRect.width;
            const canvasY = (-worldVector.y * 0.5 + 0.5) * canvasRect.height;

            // 转换为屏幕绝对坐标
            const screenX = canvasRect.left + canvasX;
            const screenY = canvasRect.top + canvasY;

            // 计算按钮位置（模型左侧）
            const buttonX = screenX - 80; // 在模型左侧80px
            const buttonY = screenY - 50; // 稍微高于模型中心

            // 边界限制（相对于整个页面）
            const clampedX = Math.max(10, Math.min(buttonX, window.innerWidth - 120));
            const clampedY = Math.max(10, Math.min(buttonY, window.innerHeight - 100));

            // 检查位置是否真正改变
            const currentLeft = parseFloat(buttonsContainer.style.left) || 0;
            const currentTop = parseFloat(buttonsContainer.style.top) || 0;
            const newLeft = clampedX;
            const newTop = clampedY;

            // 更新按钮位置
            buttonsContainer.style.left = `${clampedX}px`;
            buttonsContainer.style.top = `${clampedY}px`;

        } catch (error) {
            // 忽略位置更新错误
        }
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
     * 设置浮动按钮的鼠标跟踪（类似 Live2D 的 enableMouseTracking）
     */
    setupFloatingButtonsMouseTracking() {
        if (!this.manager.renderer || !this.manager.currentModel) return;

        const canvas = this.manager.renderer.domElement;
        let buttonsContainer = document.getElementById('live2d-floating-buttons');
        if (!buttonsContainer) return;
        
        // 清除之前的定时器
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }
        
        // 辅助函数：显示按钮并更新位置
        const showButtons = () => {
            if (this.checkLocked()) return; // 锁定时不显示

            if (window.live2dManager) {
                window.live2dManager.isFocusing = true;
            }

            // 更新按钮位置
            this.updateFloatingButtonsPosition();

            buttonsContainer.style.display = 'flex';

            // 清除隐藏定时器
            if (this._hideButtonsTimer) {
                clearTimeout(this._hideButtonsTimer);
                this._hideButtonsTimer = null;
            }
        };
        
        // 辅助函数：启动隐藏定时器
        const startHideTimer = (delay = 1000) => {
            if (this.checkLocked()) return; // 锁定时不隐藏
            
            // 清除之前的定时器
            if (this._hideButtonsTimer) {
                clearTimeout(this._hideButtonsTimer);
                this._hideButtonsTimer = null;
            }
            
            this._hideButtonsTimer = setTimeout(() => {
                // 检查鼠标是否在按钮区域内
                if (this._isMouseOverButtons) {
                    // 鼠标在按钮上，不隐藏，重新启动定时器
                    this._hideButtonsTimer = null;
                    startHideTimer(delay);
                    return;
                }
                
                // 再次检查鼠标是否在canvas上（防止在定时器执行期间鼠标移入）
                const canvas = this.manager.renderer.domElement;
                const rect = canvas.getBoundingClientRect();
                const mouseX = this._lastMouseX || 0;
                const mouseY = this._lastMouseY || 0;
                const isInCanvas = mouseX >= rect.left && mouseX <= rect.right &&
                                   mouseY >= rect.top && mouseY <= rect.bottom;
                
                if (isInCanvas) {
                    // 鼠标在canvas上，不隐藏，重新启动定时器
                    this._hideButtonsTimer = null;
                    startHideTimer(delay);
                    return;
                }
                
                if (window.live2dManager) {
                    window.live2dManager.isFocusing = false;
                }
                buttonsContainer.style.display = 'none';
                this._hideButtonsTimer = null;
            }, delay);
        };
        
        // 监听 canvas 的鼠标进入/离开事件
        const onMouseEnter = () => {
            showButtons();
        };
        
        const onMouseLeave = () => {
            startHideTimer();
        };
        
        // 监听 window 的鼠标移动，检测鼠标是否接近模型
        const onPointerMove = (event) => {
            if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;
            if (this.checkLocked()) return;
            
            const canvas = this.manager.renderer.domElement;
            const rect = canvas.getBoundingClientRect();
            const mouseX = event.clientX;
            const mouseY = event.clientY;
            
            // 保存鼠标位置，供隐藏定时器使用
            this._lastMouseX = mouseX;
            this._lastMouseY = mouseY;
            
            // 检查鼠标是否在画布区域内
            const isInCanvas = mouseX >= rect.left && mouseX <= rect.right &&
                               mouseY >= rect.top && mouseY <= rect.bottom;
            
            // 检查鼠标是否在浮动按钮区域内（仅在按钮显示时检查）
            let isOverButtons = false;
            if (buttonsContainer.style.display === 'flex') {
                const buttonsRect = buttonsContainer.getBoundingClientRect();
                isOverButtons = mouseX >= buttonsRect.left && mouseX <= buttonsRect.right &&
                                mouseY >= buttonsRect.top && mouseY <= buttonsRect.bottom;
            }
            this._isMouseOverButtons = isOverButtons;
            
            if (isInCanvas || isOverButtons) {
                // 鼠标在画布或按钮上，显示按钮
                showButtons();
            } else {
                // 鼠标不在画布或按钮区域内，启动隐藏定时器
                startHideTimer();
            }
        };
        
        // 绑定事件
        canvas.addEventListener('mouseenter', onMouseEnter);
        canvas.addEventListener('mouseleave', onMouseLeave);
        window.addEventListener('pointermove', onPointerMove);
        
        // 保存事件处理器引用，用于清理
        this._floatingButtonsMouseEnter = onMouseEnter;
        this._floatingButtonsMouseLeave = onMouseLeave;
        this._floatingButtonsPointerMove = onPointerMove;
        
        // 初始显示按钮（如果模型存在且未锁定）
        if (this.manager.currentModel && !this.checkLocked()) {
            setTimeout(() => {
                showButtons();
                // 5秒后如果鼠标不在附近就隐藏
                setTimeout(() => {
                    if (!window.live2dManager || !window.live2dManager.isFocusing) {
                        startHideTimer();
                    }
                }, 5000);
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
    }
    
    /**
     * 清理交互资源
     */
    dispose() {
        this.enableMouseTracking(false);
        this.cleanupDragAndZoom();

        // 清理所有可能的定时器
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }

        // 重置状态
        this.isDragging = false;
        this.dragMode = null;
        this.isLocked = false;
    }
}

// 导出到全局
window.VRMInteraction = VRMInteraction;
console.log('[VRM Interaction] VRMInteraction 已注册到全局对象');

