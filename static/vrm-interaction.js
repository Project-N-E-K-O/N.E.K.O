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

        // 开启“始终面朝相机” 
        this.enableFaceCamera = true;
    }
    
    
    /**
     * 【修改】初始化拖拽和缩放功能
     * 已移除所有导致报错的 LookAt/mouseNDC 代码
     */
    initDragAndZoom() {
        if (!this.manager.renderer) return;
        
        // 确保 camera 已初始化
        if (!this.manager.camera) {
            setTimeout(() => { if (this.manager.camera) this.initDragAndZoom(); }, 100);
            return;
        }
        
        const canvas = this.manager.renderer.domElement;
        const THREE = window.THREE;

        // 先清理旧的事件监听器
        this.cleanupDragAndZoom();

        // 1. 鼠标按下
        this.mouseDownHandler = (e) => {
            if (this.checkLocked()) return;

            if (e.button === 0 || e.button === 1) { // 左键或中键
                this.isDragging = true;
                this.dragMode = 'pan';
                this.previousMousePosition = { x: e.clientX, y: e.clientY };
                canvas.style.cursor = 'move';
                e.preventDefault();
                e.stopPropagation();
            }
        };

        // 2. 鼠标移动 (核心拖拽逻辑)
        this.dragHandler = (e) => {
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
                // 平移速度
                const panSpeed = 0.01; 
                const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.manager.camera.quaternion);
                const up = new THREE.Vector3(0, 1, 0).applyQuaternion(this.manager.camera.quaternion);

                // 计算新位置
                const newPosition = this.manager.currentModel.scene.position.clone();
                newPosition.add(right.multiplyScalar(deltaX * panSpeed));
                newPosition.add(up.multiplyScalar(-deltaY * panSpeed));

                // 使用边界限制
                const finalPosition = this.clampModelPosition(newPosition);

                // 应用位置
                this.manager.currentModel.scene.position.copy(finalPosition);

                // 更新UI位置
                this.updateFloatingButtonsPosition();
                if (this.manager.core && typeof this.manager.core.updateLockIconPosition === 'function') {
                    this.manager.core.updateLockIconPosition();
                }
            }

            this.previousMousePosition = { x: e.clientX, y: e.clientY };
            e.preventDefault();
            e.stopPropagation();
        };

        // 3. 鼠标释放
        this.mouseUpHandler = async (e) => {
            if (this.isDragging) {
                this.isDragging = false;
                this.dragMode = null;
                canvas.style.cursor = 'grab';

                // 拖动结束后保存位置
                await this._savePositionAfterInteraction();

                e.preventDefault();
                e.stopPropagation();
            }
        };
        
        // 5. 鼠标进入
        this.mouseEnterHandler = () => {
            canvas.style.cursor = 'grab';
        };

        // 6. 滚轮缩放
        this.wheelHandler = (e) => {
            if (this.checkLocked() || !this.manager.currentModel) return;
            
            e.preventDefault();
            e.stopPropagation();
            
            const THREE = window.THREE;
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
                const minDist = 2.0;  // 【修复】限制最小距离，防止放大后移动时只能看到腿
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
        canvas.addEventListener('wheel', this.wheelHandler, { passive: false, capture: true });
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
        // 1. 浮动按钮跟随
        this.updateFloatingButtonsPosition();

        // 2. 更新身体朝向
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
     * 【视锥体中心点限制 + 非对称边界】
     **/
    clampModelPosition(position) {
        if (!this.manager.camera || !this.manager.renderer) {
            return position;
        }
        
        const THREE = window.THREE;
        const camera = this.manager.camera;
        
        // 1. 将目标位置(世界坐标)投影到屏幕空间(NDC)
        const ndc = position.clone().project(camera);
        
        // 2. 设定边界
        // X轴 (左右)：对称，保留 5% 边距
        const limitX = 0.95;
        
        // Y轴 (上下)：非对称设置
        // -1.2: 放宽底部限制，允许脚底稍微移出屏幕下方，手感更自由
        //  0.2: 顶部依然保持严格，防止头飞出去
        const limitYBottom = -1.6; 
        const limitYTop = 0.2;
        
        
        let clampedX = ndc.x;
        let clampedY = ndc.y;

        // 执行限制
        if (clampedX < -limitX) clampedX = -limitX;
        if (clampedX > limitX) clampedX = limitX;
        if (clampedY < limitYBottom) clampedY = limitYBottom; // 底部限制
        if (clampedY > limitYTop) clampedY = limitYTop;       // 顶部限制 (防飞出)

        // 3. 如果没有超出范围，直接返回
        if (Math.abs(clampedX - ndc.x) < 0.0001 && Math.abs(clampedY - ndc.y) < 0.0001) {
            return position;
        }

        // 4. 【核心数学】计算偏移量并反解
        // 计算当前深度下，屏幕视平面的物理尺寸
        const distance = camera.position.distanceTo(position);
        const vFov = camera.fov * Math.PI / 180;
        const planeHeightAtDistance = 2 * Math.tan(vFov / 2) * distance; 
        const planeWidthAtDistance = planeHeightAtDistance * camera.aspect; 
        
        // 计算 NDC 的差值
        const deltaNdcX = clampedX - ndc.x;
        const deltaNdcY = clampedY - ndc.y;
        
        // 转换为世界坐标偏移量
        const worldOffsetX = (deltaNdcX / 2.0) * planeWidthAtDistance;
        const worldOffsetY = (deltaNdcY / 2.0) * planeHeightAtDistance;
        
        // 获取相机的右向量和上向量
        const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion);
        const up = new THREE.Vector3(0, 1, 0).applyQuaternion(camera.quaternion);
        
        // 应用偏移
        const correctedPos = position.clone();
        correctedPos.add(right.multiplyScalar(worldOffsetX));
        correctedPos.add(up.multiplyScalar(worldOffsetY));
        
        return correctedPos;
    }
    
    /**
     * 更新浮动按钮位置，使其跟随VRM模型
     */
    updateFloatingButtonsPosition() {
        // 1. 获取容器 - 只查找VRM的按钮
        const buttonsContainer = document.getElementById('vrm-floating-buttons');

        // 【修改】只要容器存在就计算位置，不再检查 display === 'none'
        // 这样确保菜单在显示出的那一瞬间，位置已经是正确的跟随位置了
        if (!buttonsContainer) return;

        if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;

        try {
            const vrm = this.manager.currentModel.vrm;
            const camera = this.manager.camera;
            const renderer = this.manager.renderer;
            
            if (!camera || !renderer) return;

            const canvasRect = renderer.domElement.getBoundingClientRect();
            
            let targetObj = vrm.scene; 
            
            if (vrm.humanoid) {
                if (typeof vrm.humanoid.getNormalizedBoneNode === 'function') {
                    const head = vrm.humanoid.getNormalizedBoneNode('head');
                    if (head) targetObj = head;
                } 
                else if (typeof vrm.humanoid.getBoneNode === 'function') {
                    const head = vrm.humanoid.getBoneNode('head');
                    if (head) targetObj = head;
                }
            }

            // 【修改 2】强制更新世界矩阵，确保获取到最新位置（解决拖拽延迟问题）
            targetObj.updateWorldMatrix(true, false);

            // 计算屏幕坐标
            const targetWorldPos = new THREE.Vector3();
            targetObj.getWorldPosition(targetWorldPos);
            
            const worldVector = targetWorldPos.clone();
            worldVector.project(camera);

            const canvasX = (worldVector.x * 0.5 + 0.5) * canvasRect.width;
            const canvasY = (-worldVector.y * 0.5 + 0.5) * canvasRect.height;

            const screenX = canvasRect.left + canvasX;
            const screenY = canvasRect.top + canvasY;

            // 应用偏移
            const buttonX = screenX - 80; 
            const buttonY = screenY; 

            // 获取按钮容器的实际尺寸（用于边界限制）
            // 如果容器是隐藏的，使用 getBoundingClientRect 或回退到默认值
            let containerWidth = buttonsContainer.offsetWidth;
            let containerHeight = buttonsContainer.offsetHeight;
            
            // 如果尺寸为 0（可能是隐藏状态），尝试使用 getBoundingClientRect
            if (containerWidth === 0 || containerHeight === 0) {
                const rect = buttonsContainer.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    containerWidth = rect.width;
                    containerHeight = rect.height;
                } else {
                    // 回退到默认值（基于按钮配置：5个按钮，每个48px，间距12px）
                    containerWidth = 48;  // 按钮宽度
                    containerHeight = 48 * 5 + 12 * 4;  // 5个按钮 + 4个间距 = 288px
                }
            }

            // 屏幕边缘限制（参考 Live2D 的实现）
            const screenWidth = window.innerWidth;
            const screenHeight = window.innerHeight;
            const minMargin = 10;  // 最小边距
            
            // X轴边界限制：确保按钮容器不超出屏幕右边界
            const maxX = screenWidth - containerWidth - minMargin;
            const clampedX = Math.max(minMargin, Math.min(buttonX, maxX));
            
            // Y轴边界限制：确保按钮容器不超出屏幕上下边界
            const minY = minMargin;
            const maxY = screenHeight - containerHeight - minMargin;
            const clampedY = Math.max(minY, Math.min(buttonY, maxY));

            buttonsContainer.style.left = `${clampedX}px`;
            buttonsContainer.style.top = `${clampedY}px`;

        } catch (error) {
            // 静默失败
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
    /**
     * 设置浮动按钮的鼠标跟踪
     */
    setupFloatingButtonsMouseTracking() {
        if (!this.manager.renderer || !this.manager.currentModel) return;

        const canvas = this.manager.renderer.domElement;

        // 【修改】只查找 VRM 专用 ID
        let buttonsContainer = document.getElementById('vrm-floating-buttons');

        if (!buttonsContainer) return;
        
        // 清除之前的定时器
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
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

            // 显示浮动按钮
            currentButtonsContainer.style.display = 'flex';
            
            // 更新按钮位置（显示后再更新，确保位置计算正确）
            // 使用 requestAnimationFrame 确保 DOM 更新后再计算位置
            requestAnimationFrame(() => {
                this.updateFloatingButtonsPosition();
            });

            // 【只控制锁图标】鼠标靠近时显示锁图标
            const lockIcon = document.getElementById('vrm-lock-icon');
            if (lockIcon) {
                lockIcon.style.display = 'block';
            }

            if (this._hideButtonsTimer) {
                clearTimeout(this._hideButtonsTimer);
                this._hideButtonsTimer = null;
            }
        };
        
        // 辅助函数：启动隐藏定时器
        const startHideTimer = (delay = 1000) => {
            if (this.checkLocked()) return;

            if (this._hideButtonsTimer) {
                clearTimeout(this._hideButtonsTimer);
                this._hideButtonsTimer = null;
            }

            this._hideButtonsTimer = setTimeout(() => {
                // 【新增】检查鼠标是否在锁图标上
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

                // 检查鼠标是否仍在模型附近（使用与 onPointerMove 相同的距离检测逻辑）
                try {
                    const vrm = this.manager.currentModel?.vrm;
                    const camera = this.manager.camera;
                    const renderer = this.manager.renderer;
                    
                    if (vrm && camera && renderer) {
                        const mouseX = this._lastMouseX || 0;
                        const mouseY = this._lastMouseY || 0;
                        
                        // 计算模型在屏幕上的包围盒
                        const box = new THREE.Box3().setFromObject(vrm.scene);
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
                        let minX = Infinity, maxX = -Infinity;
                        let minY = Infinity, maxY = -Infinity;
                        
                        corners.forEach(corner => {
                            const worldPos = corner.clone();
                            worldPos.project(camera);
                            const screenX = (worldPos.x * 0.5 + 0.5) * canvasRect.width + canvasRect.left;
                            const screenY = (-worldPos.y * 0.5 + 0.5) * canvasRect.height + canvasRect.top;
                            minX = Math.min(minX, screenX);
                            maxX = Math.max(maxX, screenX);
                            minY = Math.min(minY, screenY);
                            maxY = Math.max(maxY, screenY);
                        });
                        
                        // 计算鼠标到模型包围盒的距离
                        const dx = Math.max(minX - mouseX, 0, mouseX - maxX);
                        const dy = Math.max(minY - mouseY, 0, mouseY - maxY);
                        const distance = Math.sqrt(dx * dx + dy * dy);
                        
                        // 阈值：150px（与 onPointerMove 保持一致）
                        const threshold = 150;
                        
                        if (distance < threshold) {
                            // 鼠标仍在模型附近，重新启动定时器
                            this._hideButtonsTimer = null;
                            startHideTimer(delay);
                            return;
                        }
                    }
                } catch (error) {
                    // 如果计算失败，使用简单的 canvas 检测作为后备
                    const canvas = this.manager.renderer?.domElement;
                    if (canvas) {
                        const rect = canvas.getBoundingClientRect();
                        const mouseX = this._lastMouseX || 0;
                        const mouseY = this._lastMouseY || 0;
                        const isInCanvas = mouseX >= rect.left && mouseX <= rect.right &&
                                           mouseY >= rect.top && mouseY <= rect.bottom;
                        if (isInCanvas) {
                            this._hideButtonsTimer = null;
                            startHideTimer(delay);
                            return;
                        }
                    }
                }

                if (window.live2dManager) {
                    window.live2dManager.isFocusing = false;
                }

                // 隐藏浮动按钮（与 Live2D 行为一致）
                // 重新获取按钮容器（防止引用失效）
                const currentButtonsContainer = document.getElementById('vrm-floating-buttons');
                if (currentButtonsContainer) {
                    currentButtonsContainer.style.display = 'none';
                }

                // 【只控制锁图标】鼠标离开时隐藏锁图标
                if (lockIcon) {
                    // 【新增】检查是否有点击保护标记，如果有则不隐藏
                    if (!lockIcon.dataset.clickProtection) {
                        lockIcon.style.display = 'none';
                    }
                }

                this._hideButtonsTimer = null;
            }, delay);
        };
        
        const onMouseEnter = () => showButtons();
        const onMouseLeave = () => {
            // 不再隐藏按钮，保持一直显示
            // 只更新位置（如果需要）
        };
        
        const onPointerMove = (event) => {
            if (!this.manager.currentModel || !this.manager.currentModel.vrm) return;
            if (this.checkLocked()) return;
            if (!this.manager.renderer || !this.manager.camera) return;

            const canvas = this.manager.renderer.domElement;
            const rect = canvas.getBoundingClientRect();
            const mouseX = event.clientX;
            const mouseY = event.clientY;

            this._lastMouseX = mouseX;
            this._lastMouseY = mouseY;

            // 检查鼠标是否在按钮或锁图标上
            // 重新获取按钮容器（防止引用失效）
            const currentButtonsContainer = document.getElementById('vrm-floating-buttons');
            let isOverButtons = false;
            if (currentButtonsContainer && currentButtonsContainer.style.display === 'flex') {
                const buttonsRect = currentButtonsContainer.getBoundingClientRect();
                isOverButtons = mouseX >= buttonsRect.left && mouseX <= buttonsRect.right &&
                                mouseY >= buttonsRect.top && mouseY <= buttonsRect.bottom;
            }

            // 检查鼠标是否在锁图标上
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

            // 计算鼠标到模型的距离（屏幕空间，类似 Live2D 的实现）
            try {
                const vrm = this.manager.currentModel.vrm;
                const camera = this.manager.camera;
                const renderer = this.manager.renderer;
                
                if (!camera || !renderer) {
                    startHideTimer();
                    return;
                }

                // 计算模型在屏幕上的包围盒
                const box = new THREE.Box3().setFromObject(vrm.scene);
                const size = new THREE.Vector3();
                box.getSize(size);
                
                // 获取模型的中心点和边界点
                const center = new THREE.Vector3();
                box.getCenter(center);
                
                // 将 3D 点投影到屏幕空间
                const screenPoints = [];
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
                let minX = Infinity, maxX = -Infinity;
                let minY = Infinity, maxY = -Infinity;
                
                corners.forEach(corner => {
                    const worldPos = corner.clone();
                    worldPos.project(camera);
                    const screenX = (worldPos.x * 0.5 + 0.5) * canvasRect.width + canvasRect.left;
                    const screenY = (-worldPos.y * 0.5 + 0.5) * canvasRect.height + canvasRect.top;
                    minX = Math.min(minX, screenX);
                    maxX = Math.max(maxX, screenX);
                    minY = Math.min(minY, screenY);
                    maxY = Math.max(maxY, screenY);
                });
                
                // 计算鼠标到模型包围盒的距离（类似 Live2D 的实现）
                const dx = Math.max(minX - mouseX, 0, mouseX - maxX);
                const dy = Math.max(minY - mouseY, 0, mouseY - maxY);
                const distance = Math.sqrt(dx * dx + dy * dy);
                
                // 阈值：150px（与 Live2D 保持一致）
                const threshold = 150;
                
                // 按钮一直显示，只更新位置
                if (distance < threshold) {
                    // 鼠标在模型附近，更新按钮位置
                    showButtons();
                }
                // 不再隐藏按钮，保持一直显示
            } catch (error) {
                // 如果计算失败，使用简单的 canvas 检测作为后备
                const isInCanvas = mouseX >= rect.left && mouseX <= rect.right &&
                                   mouseY >= rect.top && mouseY <= rect.bottom;
                if (isInCanvas) {
                    showButtons();
                }
                // 不再隐藏按钮，保持一直显示
            }
        };
        
        canvas.addEventListener('mouseenter', onMouseEnter);
        canvas.addEventListener('mouseleave', onMouseLeave);
        window.addEventListener('pointermove', onPointerMove);
        
        this._floatingButtonsMouseEnter = onMouseEnter;
        this._floatingButtonsMouseLeave = onMouseLeave;
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
    }

    /**
     * 保存模型位置和状态到后端（交互结束后调用）
     */
    async _savePositionAfterInteraction() {
        if (!this.manager.currentModel || !this.manager.currentModel.url) {
            console.debug('[VRM] 无法保存位置：模型或路径未设置');
            return;
        }

        const scene = this.manager.currentModel.scene;
        if (!scene) {
            console.debug('[VRM] 无法保存位置：场景未设置');
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

        // 异步保存，不阻塞交互
        if (this.manager.core && typeof this.manager.core.saveUserPreferences === 'function') {
            this.manager.core.saveUserPreferences(
                this.manager.currentModel.url,
                position,
                scale,
                rotation,
                displayInfo
            ).then(success => {
                if (success) {
                    console.debug('[VRM] 模型位置和缩放已自动保存');
                } else {
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

        // 重置状态
        this.isDragging = false;
        this.dragMode = null;
        this.isLocked = false;
    }
}

// 导出到全局
window.VRMInteraction = VRMInteraction;

