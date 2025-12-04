/**
 * Live2D Interaction - 拖拽、缩放、鼠标跟踪等交互功能
 */

// 设置拖拽功能
Live2DManager.prototype.setupDragAndDrop = function(model) {
    model.interactive = true;
    // 移除 stage.hitArea = screen，避免阻挡背景点击
    // this.pixi_app.stage.interactive = true;
    // this.pixi_app.stage.hitArea = this.pixi_app.screen;

    let isDragging = false;
    let dragStartPos = new PIXI.Point();

    model.on('pointerdown', (event) => {
        if (this.isLocked) return;
        
        // 检测是否为触摸事件，且是多点触摸（双指缩放）
        const originalEvent = event.data.originalEvent;
        if (originalEvent && originalEvent.touches && originalEvent.touches.length > 1) {
            // 多点触摸时不启动拖拽
            return;
        }
        
        isDragging = true;
        this.isFocusing = false; // 拖拽时禁用聚焦
        const globalPos = event.data.global;
        dragStartPos.x = globalPos.x - model.x;
        dragStartPos.y = globalPos.y - model.y;
        document.getElementById('live2d-canvas').style.cursor = 'grabbing';
    });

    const onDragEnd = () => {
        if (isDragging) {
            isDragging = false;
            document.getElementById('live2d-canvas').style.cursor = 'grab';
        }
    };

    const onDragMove = (event) => {
        if (isDragging) {
            // 再次检查是否变成多点触摸
            if (event.touches && event.touches.length > 1) {
                // 如果变成多点触摸，停止拖拽
                isDragging = false;
                document.getElementById('live2d-canvas').style.cursor = 'grab';
                return;
            }
            
            // 将 window 坐标转换为 Pixi 全局坐标 (通常在全屏下是一样的，但为了保险)
            // 这里假设 canvas 是全屏覆盖的
            const x = event.clientX;
            const y = event.clientY;
            
            model.x = x - dragStartPos.x;
            model.y = y - dragStartPos.y;
        }
    };

    // 清理旧的监听器
    if (this._dragEndListener) {
        window.removeEventListener('pointerup', this._dragEndListener);
        window.removeEventListener('pointercancel', this._dragEndListener);
    }
    if (this._dragMoveListener) {
        window.removeEventListener('pointermove', this._dragMoveListener);
    }

    // 保存新的监听器引用
    this._dragEndListener = onDragEnd;
    this._dragMoveListener = onDragMove;

    // 使用 window 监听拖拽结束和移动，确保即使移出 canvas 也能响应
    window.addEventListener('pointerup', onDragEnd);
    window.addEventListener('pointercancel', onDragEnd);
    window.addEventListener('pointermove', onDragMove);
};

// 设置滚轮缩放
Live2DManager.prototype.setupWheelZoom = function(model) {
    const onWheelScroll = (event) => {
        if (this.isLocked || !this.currentModel) return;
        event.preventDefault();
        const scaleFactor = 1.1;
        const oldScale = this.currentModel.scale.x;
        let newScale = event.deltaY < 0 ? oldScale * scaleFactor : oldScale / scaleFactor;
        this.currentModel.scale.set(newScale);
    };

    const view = this.pixi_app.view;
    if (view.lastWheelListener) {
        view.removeEventListener('wheel', view.lastWheelListener);
    }
    view.addEventListener('wheel', onWheelScroll, { passive: false });
    view.lastWheelListener = onWheelScroll;
};

// 设置触摸缩放（双指捏合）
Live2DManager.prototype.setupTouchZoom = function(model) {
    const view = this.pixi_app.view;
    let initialDistance = 0;
    let initialScale = 1;
    let isTouchZooming = false;
    
    const getTouchDistance = (touch1, touch2) => {
        const dx = touch2.clientX - touch1.clientX;
        const dy = touch2.clientY - touch1.clientY;
        return Math.sqrt(dx * dx + dy * dy);
    };
    
    const onTouchStart = (event) => {
        if (this.isLocked || !this.currentModel) return;
        
        // 检测双指触摸
        if (event.touches.length === 2) {
            event.preventDefault();
            isTouchZooming = true;
            initialDistance = getTouchDistance(event.touches[0], event.touches[1]);
            initialScale = this.currentModel.scale.x;
        }
    };
    
    const onTouchMove = (event) => {
        if (this.isLocked || !this.currentModel || !isTouchZooming) return;
        
        // 双指缩放
        if (event.touches.length === 2) {
            event.preventDefault();
            const currentDistance = getTouchDistance(event.touches[0], event.touches[1]);
            const scaleChange = currentDistance / initialDistance;
            let newScale = initialScale * scaleChange;
            
            // 限制缩放范围，避免过大或过小
            newScale = Math.max(0.1, Math.min(2.0, newScale));
            
            this.currentModel.scale.set(newScale);
        }
    };
    
    const onTouchEnd = (event) => {
        // 当手指数量小于2时，停止缩放
        if (event.touches.length < 2) {
            isTouchZooming = false;
        }
    };
    
    // 移除旧的监听器（如果存在）
    if (view.lastTouchStartListener) {
        view.removeEventListener('touchstart', view.lastTouchStartListener);
    }
    if (view.lastTouchMoveListener) {
        view.removeEventListener('touchmove', view.lastTouchMoveListener);
    }
    if (view.lastTouchEndListener) {
        view.removeEventListener('touchend', view.lastTouchEndListener);
    }
    
    // 添加新的监听器
    view.addEventListener('touchstart', onTouchStart, { passive: false });
    view.addEventListener('touchmove', onTouchMove, { passive: false });
    view.addEventListener('touchend', onTouchEnd, { passive: false });
    
    // 保存监听器引用，便于清理
    view.lastTouchStartListener = onTouchStart;
    view.lastTouchMoveListener = onTouchMove;
    view.lastTouchEndListener = onTouchEnd;
};

// 启用鼠标跟踪以检测与模型的接近度
Live2DManager.prototype.enableMouseTracking = function(model, options = {}) {
    const { threshold = 70 } = options;
    
    // 使用实例属性保存定时器，便于在其他地方访问
    if (this._hideButtonsTimer) {
        clearTimeout(this._hideButtonsTimer);
        this._hideButtonsTimer = null;
    }

    // 辅助函数：显示按钮
    const showButtons = () => {
        const lockIcon = document.getElementById('live2d-lock-icon');
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        
        // 如果已经点击了"请她离开"，不显示锁按钮，但保持显示"请她回来"按钮
        if (this._goodbyeClicked) {
            if (lockIcon) {
                lockIcon.style.setProperty('display', 'none', 'important');
            }
            return;
        }
        
        this.isFocusing = true;
        if (lockIcon) lockIcon.style.display = 'block';
        if (floatingButtons) floatingButtons.style.display = 'flex';
        
        // 清除隐藏定时器
        if (this._hideButtonsTimer) {
            clearTimeout(this._hideButtonsTimer);
            this._hideButtonsTimer = null;
        }
    };
    
    // 辅助函数：启动隐藏定时器
    const startHideTimer = (delay = 1000) => {
        const lockIcon = document.getElementById('live2d-lock-icon');
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        
        if (this._goodbyeClicked) return;
        
        // 如果已有定时器，不重复创建
        if (this._hideButtonsTimer) return;
        
        this._hideButtonsTimer = setTimeout(() => {
            // 再次检查鼠标是否在按钮区域内
            if (this._isMouseOverButtons) {
                // 鼠标在按钮上，不隐藏，重新启动定时器
                this._hideButtonsTimer = null;
                startHideTimer(delay);
                return;
            }
            
            this.isFocusing = false;
            if (lockIcon) lockIcon.style.display = 'none';
            if (floatingButtons && !this._goodbyeClicked) {
                floatingButtons.style.display = 'none';
            }
            this._hideButtonsTimer = null;
        }, delay);
    };

    // 方法1：监听 PIXI 模型的 pointerover/pointerout 事件（适用于 Electron 透明窗口）
    model.on('pointerover', () => {
        showButtons();
    });
    
    model.on('pointerout', () => {
        // 鼠标离开模型，启动隐藏定时器
        startHideTimer();
    });
    
    // 方法2：同时保留 window 的 pointermove 监听（适用于普通浏览器）
    const onPointerMove = (event) => {
        // 使用 clientX/Y 作为全局坐标
        const pointer = { x: event.clientX, y: event.clientY };
        
        // 在拖拽期间不执行任何操作
        if (model.interactive && model.dragging) {
            return;
        }
        
        // 如果已经点击了"请她离开"，特殊处理
        if (this._goodbyeClicked) {
            const lockIcon = document.getElementById('live2d-lock-icon');
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            const returnButtonContainer = document.getElementById('live2d-return-button-container');
            
            if (lockIcon) {
                lockIcon.style.setProperty('display', 'none', 'important');
            }
            // 隐藏浮动按钮容器，显示"请她回来"按钮
            if (floatingButtons) {
                floatingButtons.style.display = 'none';
            }
            if (returnButtonContainer) {
                returnButtonContainer.style.display = 'block';
            }
            return;
        }

        const bounds = model.getBounds();
        const dx = Math.max(bounds.left - pointer.x, 0, pointer.x - bounds.right);
        const dy = Math.max(bounds.top - pointer.y, 0, pointer.y - bounds.bottom);
        const distance = Math.sqrt(dx * dx + dy * dy);

        if (distance < threshold) {
            showButtons();
            // 只有当鼠标在模型附近时才调用 focus，避免 Electron 透明窗口中的全局跟踪问题
            if (this.isFocusing) {
                model.focus(pointer.x, pointer.y);
            }
        } else {
            // 鼠标离开模型区域，启动隐藏定时器
            this.isFocusing = false;
            const lockIcon = document.getElementById('live2d-lock-icon');
            if (lockIcon) lockIcon.style.display = 'none';
            startHideTimer();
        }
    };

    // 清理旧的监听器
    if (this._mouseTrackingListener) {
        window.removeEventListener('pointermove', this._mouseTrackingListener);
    }

    // 保存新的监听器引用
    this._mouseTrackingListener = onPointerMove;

    // 使用 window 监听鼠标移动
    window.addEventListener('pointermove', onPointerMove);
    
    // 监听浮动按钮容器的鼠标进入/离开事件
    // 延迟设置，因为按钮容器可能还没创建
    setTimeout(() => {
        const floatingButtons = document.getElementById('live2d-floating-buttons');
        if (floatingButtons) {
            floatingButtons.addEventListener('mouseenter', () => {
                this._isMouseOverButtons = true;
                // 鼠标进入按钮区域，清除隐藏定时器
                if (this._hideButtonsTimer) {
                    clearTimeout(this._hideButtonsTimer);
                    this._hideButtonsTimer = null;
                }
            });
            
            floatingButtons.addEventListener('mouseleave', () => {
                this._isMouseOverButtons = false;
                // 鼠标离开按钮区域，启动隐藏定时器
                startHideTimer();
            });
        }
        
        // 同样处理锁图标
        const lockIcon = document.getElementById('live2d-lock-icon');
        if (lockIcon) {
            lockIcon.addEventListener('mouseenter', () => {
                this._isMouseOverButtons = true;
                if (this._hideButtonsTimer) {
                    clearTimeout(this._hideButtonsTimer);
                    this._hideButtonsTimer = null;
                }
            });
            
            lockIcon.addEventListener('mouseleave', () => {
                this._isMouseOverButtons = false;
                startHideTimer();
            });
        }
    }, 100);
};

