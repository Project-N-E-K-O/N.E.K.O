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

    // 智能事件传播管理 - 在拖动过程中临时禁用按钮事件拦截
    const enableButtonEventPropagation = () => {
        // 收集所有按钮元素
        const buttons = document.querySelectorAll('.live2d-floating-btn, [id^="live2d-btn-"]');
        buttons.forEach(btn => {
            if (btn) {
                // 保存当前的pointerEvents值
                const currentValue = btn.style.pointerEvents || '';
                btn.setAttribute('data-prev-pointer-events', currentValue);
                btn.style.pointerEvents = 'none';
            }
        });
        
        // 收集并处理所有按钮包装器元素
        const wrappers = new Set();
        buttons.forEach(btn => {
            if (btn && btn.parentElement) {
                wrappers.add(btn.parentElement);
            }
        });
        
        wrappers.forEach(wrapper => {
            const currentValue = wrapper.style.pointerEvents || '';
            wrapper.setAttribute('data-prev-pointer-events', currentValue);
            wrapper.style.pointerEvents = 'none';
        });
    };

    const disableButtonEventPropagation = () => {
        const elementsToRestore = document.querySelectorAll('[data-prev-pointer-events]');
        elementsToRestore.forEach(element => {
            if (element) {
                const prevValue = element.getAttribute('data-prev-pointer-events');
                if (prevValue === '') {
                    element.style.pointerEvents = '';
                } else {
                    element.style.pointerEvents = prevValue;
                }
                element.removeAttribute('data-prev-pointer-events');
            }
        });
    };

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
        
        // 开始拖动时，临时禁用按钮的事件拦截
        enableButtonEventPropagation();
    });

    const onDragEnd = async () => {
        if (isDragging) {
            isDragging = false;
            document.getElementById('live2d-canvas').style.cursor = 'grab';
            
            // 拖拽结束后恢复按钮的事件拦截
            disableButtonEventPropagation();
            
            // 检测是否需要切换屏幕（多屏幕支持）
            // _checkAndSwitchDisplay returns true if a display switch occurred (and saved internally)
            const displaySwitched = await this._checkAndSwitchDisplay(model);
            
            // 拖拽结束后自动保存位置（仅当没有发生屏幕切换时）
            if (!displaySwitched) {
                await this._savePositionAfterInteraction();
            }
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
        
        // 使用防抖动保存缩放，避免滚轮过程中频繁保存
        this._debouncedSavePosition();
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
    
    const onTouchEnd = async (event) => {
        // 当手指数量小于2时，停止缩放
        if (event.touches.length < 2) {
            if (isTouchZooming) {
                // 触摸缩放结束后自动保存位置和缩放
                await this._savePositionAfterInteraction();
            }
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
        // 锁定状态下不显示浮动菜单
        if (floatingButtons && !this.isLocked) floatingButtons.style.display = 'flex';
        
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
        // 检查模型是否存在，防止切换模型时出现错误
        if (!model) {
            return;
        }
        
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

        try {
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
        } catch (error) {
            console.error('Live2D 交互错误:', error);
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

// 交互后保存位置和缩放的辅助函数
Live2DManager.prototype._savePositionAfterInteraction = async function() {
    if (!this.currentModel || !this._lastLoadedModelPath) {
        console.debug('无法保存位置：模型或路径未设置');
        return;
    }
    
    const position = { x: this.currentModel.x, y: this.currentModel.y };
    const scale = { x: this.currentModel.scale.x, y: this.currentModel.scale.y };
    
    // 验证数据有效性
    if (!Number.isFinite(position.x) || !Number.isFinite(position.y) ||
        !Number.isFinite(scale.x) || !Number.isFinite(scale.y)) {
        console.warn('位置或缩放数据无效，跳过保存');
        return;
    }
    
    // 获取当前窗口所在显示器的信息（用于多屏幕位置恢复）
    let displayInfo = null;
    if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
        try {
            const currentDisplay = await window.electronScreen.getCurrentDisplay();
            console.debug('currentDisplay', currentDisplay);
            if (currentDisplay) {
                // 优先使用 screenX/screenY，兜底使用 bounds.x/bounds.y
                let screenX = currentDisplay.screenX;
                let screenY = currentDisplay.screenY;
                
                // 如果 screenX/screenY 不存在，尝试从 bounds 获取
                if (!Number.isFinite(screenX) || !Number.isFinite(screenY)) {
                    if (currentDisplay.bounds && 
                        Number.isFinite(currentDisplay.bounds.x) && 
                        Number.isFinite(currentDisplay.bounds.y)) {
                        screenX = currentDisplay.bounds.x;
                        screenY = currentDisplay.bounds.y;
                        console.debug('使用 bounds 作为显示器位置');
                    }
                }
                
                if (Number.isFinite(screenX) && Number.isFinite(screenY)) {
                    displayInfo = {
                        screenX: screenX,
                        screenY: screenY
                    };
                    console.debug('保存显示器位置:', displayInfo);
                }
            }
        } catch (error) {
            console.warn('获取显示器信息失败:', error);
        }
    }
    
    // 异步保存，不阻塞交互
    this.saveUserPreferences(this._lastLoadedModelPath, position, scale, null, displayInfo)
        .then(success => {
            if (success) {
                console.debug('模型位置和缩放已自动保存');
            } else {
                console.warn('自动保存位置失败');
            }
        })
        .catch(error => {
            console.error('自动保存位置时出错:', error);
        });
};

// 防抖动保存位置的辅助函数（用于滚轮缩放等连续操作）
Live2DManager.prototype._debouncedSavePosition = function() {
    // 清除之前的定时器
    if (this._savePositionDebounceTimer) {
        clearTimeout(this._savePositionDebounceTimer);
    }
    
    // 设置新的定时器，500ms后保存
    this._savePositionDebounceTimer = setTimeout(() => {
        this._savePositionAfterInteraction().catch(error => {
            // 错误已在 _savePositionAfterInteraction 内部记录，这里只是确保 Promise 被处理
            console.error('防抖动保存位置时出错:', error);
        });
    }, 500);
};

// 多屏幕支持：检测模型是否移出当前屏幕并切换到新屏幕
// Returns true if a display switch occurred (and position was saved internally), false otherwise
Live2DManager.prototype._checkAndSwitchDisplay = async function(model) {
    // 仅在 Electron 环境下执行
    if (!window.electronScreen || !window.electronScreen.moveWindowToDisplay) {
        return false;
    }
    
    try {
        // 获取模型中心点的窗口坐标
        const bounds = model.getBounds();
        const modelCenterX = (bounds.left + bounds.right) / 2;
        const modelCenterY = (bounds.top + bounds.bottom) / 2;
        
        // 获取所有屏幕信息
        const displays = await window.electronScreen.getAllDisplays();
        if (!displays || displays.length <= 1) {
            // 只有一个屏幕，不需要切换
            return false;
        }
        
        // 检查模型是否在当前窗口范围内
        const windowWidth = window.innerWidth;
        const windowHeight = window.innerHeight;
        
        // 如果模型大部分还在当前窗口内，不切换
        if (modelCenterX >= 0 && modelCenterX < windowWidth &&
            modelCenterY >= 0 && modelCenterY < windowHeight) {
            return false;
        }
        
        // 模型移出了当前窗口，查找目标屏幕
        // 需要转换为屏幕坐标（相对于屏幕的绝对坐标）
        
        // 首先获取当前窗口所在的显示器
        const currentDisplay = await window.electronScreen.getCurrentDisplay();
        if (!currentDisplay) {
            console.warn('[Live2D] 无法获取当前显示器信息');
            return false;
        }
        
        // 计算当前窗口左上角在屏幕上的绝对位置
        const windowScreenX = currentDisplay.screenX;
        const windowScreenY = currentDisplay.screenY;
        
        // 计算模型中心点的屏幕绝对坐标
        const modelScreenX = windowScreenX + modelCenterX;
        const modelScreenY = windowScreenY + modelCenterY;
        
        // 遍历所有显示器，找到包含模型中心点的显示器
        let targetDisplay = null;
        for (const display of displays) {
            // 检查模型中心点是否在这个显示器内
            if (modelScreenX >= display.screenX && 
                modelScreenX < display.screenX + display.width &&
                modelScreenY >= display.screenY && 
                modelScreenY < display.screenY + display.height) {
                targetDisplay = display;
                break;
            }
        }
        
        if (targetDisplay) {
            console.log('[Live2D] 检测到模型移出当前屏幕，准备切换到屏幕:', targetDisplay.id);
            
            // 使用之前已经计算好的模型屏幕绝对坐标调用切换屏幕
            const result = await window.electronScreen.moveWindowToDisplay(modelScreenX, modelScreenY);
            
            if (result && result.success && !result.sameDisplay) {
                console.log('[Live2D] 屏幕切换成功:', result);
                
                // 计算模型在新窗口中的位置
                // 新窗口左上角是 targetDisplay.screenX, targetDisplay.screenY
                // 模型新的窗口坐标 = 模型屏幕坐标 - 新窗口屏幕坐标
                const newModelX = modelScreenX - targetDisplay.screenX;
                const newModelY = modelScreenY - targetDisplay.screenY;
                
                // 考虑缩放因子变化
                if (result.scaleRatio && result.scaleRatio !== 1) {
                    // 如果不同屏幕有不同的缩放，可能需要调整模型大小
                    // 但通常保持模型原大小更合理，只调整位置
                    console.log('[Live2D] 屏幕缩放比变化:', result.scaleRatio);
                }
                
                // 从中心点转换到锚点位置
                // newModelX/newModelY 是模型视觉中心的坐标
                // PIXI 的 x/y 是锚点位置，需要根据锚点偏离中心的距离调整
                model.x = newModelX + (model.anchor.x - 0.5) * model.width * model.scale.x;
                model.y = newModelY + (model.anchor.y - 0.5) * model.height * model.scale.y;
                
                console.log('[Live2D] 模型新位置:', model.x, model.y);
                
                // 切换屏幕后保存位置和新的显示器信息
                await this._savePositionAfterInteraction();
                return true;  // Display switch occurred
            }
        }
        return false;  // No display switch occurred
    } catch (error) {
        console.error('[Live2D] 检测/切换屏幕时出错:', error);
        return false;
    }
};

// 监听屏幕切换事件，更新相关状态
if (typeof window !== 'undefined') {
    window.addEventListener('electron-display-changed', (event) => {
        console.log('[Live2D] 收到屏幕切换事件:', event.detail);
        // 可以在这里做额外的处理，比如重新计算UI位置等
    });
}
