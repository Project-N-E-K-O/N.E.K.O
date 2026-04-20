/**
 * Live2D Interaction - 拖拽、缩放、鼠标跟踪等交互功能
 */

// ===== 自动吸附功能配置 =====
const SNAP_CONFIG = {
    // 吸附阈值：模型在屏幕内剩余的像素小于此值时触发吸附（即模型绝大部分超出屏幕）
    threshold: 200,
    // 吸附边距：吸附后距离屏幕边缘的最小距离
    margin: 5,
    // 动画持续时间（毫秒）
    animationDuration: 260,
    // 动画缓动函数类型
    easingType: 'easeOutBack'
};

// ===== 缩放限制配置 =====
const SCALE_LIMITS = {
    MIN: 0.005, // 最小缩放比例
    MAX: 5.0     // 最大缩放比例（暂不实施，保留供后续使用）
};

// 缓动函数集合
const EasingFunctions = {
    // 线性
    linear: t => t,
    // 缓出二次方
    easeOutQuad: t => t * (2 - t),
    // 缓出三次方（更自然）
    easeOutCubic: t => (--t) * t * t + 1,
    // 缓出回弹（与聊天框一致）
    easeOutBack: t => {
        const c1 = 1.70158;
        const c3 = c1 + 1;
        return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
    },
    // 缓出弹性
    easeOutElastic: t => {
        const p = 0.3;
        return Math.pow(2, -10 * t) * Math.sin((t - p / 4) * (2 * Math.PI) / p) + 1;
    },
    // 缓入缓出
    easeInOutQuad: t => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t
};

/**
 * 检测模型是否超出当前屏幕边界，并计算吸附目标位置
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 * @param {Object} options - 可选参数
 * @param {boolean} options.afterDisplaySwitch - 是否为屏幕切换后的吸附（使用更宽松的条件：超出即吸附）
 * @returns {Object|null} 返回吸附信息，如果不需要吸附则返回 null
 */
Live2DManager.prototype._checkSnapRequired = async function (model, options = {}) {
    if (!model) return null;

    const { afterDisplaySwitch = false, threshold: customThreshold } = options;

    try {
        const bounds = model.getBounds();
        const modelLeft = bounds.left;
        const modelRight = bounds.right;
        const modelTop = bounds.top;
        const modelBottom = bounds.bottom;
        const modelWidth = bounds.width;
        const modelHeight = bounds.height;

        // 获取当前屏幕边界
        // 吸附 clamp 范围必须等同于真实可渲染像素（即 Pet 窗口的 CSS 像素尺寸）。
        // 多屏下 currentDisplay.workArea 可能大于当前窗口 innerHeight（窗口还未 resize 到新屏，或屏幕比主屏高），
        // 若直接拿 workArea 作边界，模型会被吸附到窗口像素外、被窗口边界裁成一条水平切割线。
        let screenLeft = 0;
        let screenTop = 0;
        let screenRight = window.innerWidth;
        let screenBottom = window.innerHeight;

        // 可选：读 workArea 做二次保险（取更小值），但绝不能超过 innerWidth/innerHeight
        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay && currentDisplay.workArea) {
                    const waW = currentDisplay.workArea.width;
                    const waH = currentDisplay.workArea.height;
                    if (Number.isFinite(waW) && waW > 0) screenRight = Math.min(screenRight, waW);
                    if (Number.isFinite(waH) && waH > 0) screenBottom = Math.min(screenBottom, waH);
                }
            } catch (e) {
                console.debug('获取屏幕工作区域失败，使用窗口尺寸');
            }
        }

        // 计算超出边界的距离
        let overflowLeft = screenLeft - modelLeft;       // 左边超出（正值表示超出）
        let overflowRight = modelRight - screenRight;    // 右边超出
        let overflowTop = screenTop - modelTop;          // 上边超出
        let overflowBottom = modelBottom - screenBottom; // 下边超出

        // 检查是否有任何边超出阈值
        // 新逻辑：只有当模型在屏幕内剩余的部分小于 threshold 时才触发吸附
        // 即模型绝大部分都超出屏幕时才吸附
        const threshold = customThreshold ?? SNAP_CONFIG.threshold;
        const margin = SNAP_CONFIG.margin;

        // 计算模型在屏幕内剩余的像素数
        // 水平方向：模型在屏幕内的宽度
        const visibleLeft = Math.max(modelLeft, screenLeft);
        const visibleRight = Math.min(modelRight, screenRight);
        const visibleWidth = Math.max(0, visibleRight - visibleLeft);

        // 垂直方向：模型在屏幕内的高度
        const visibleTop = Math.max(modelTop, screenTop);
        const visibleBottom = Math.min(modelBottom, screenBottom);
        const visibleHeight = Math.max(0, visibleBottom - visibleTop);

        // 判断是否需要吸附
        // 屏幕切换后：只要超出边界就吸附（更宽松）
        // 当前屏幕：屏幕内剩余的像素小于阈值时才吸附（即模型绝大部分超出屏幕）
        let needsSnapLeft, needsSnapRight, needsSnapTop, needsSnapBottom;

        if (afterDisplaySwitch) {
            // 屏幕切换后，只要超出边界就吸附
            needsSnapLeft = overflowLeft > margin;
            needsSnapRight = overflowRight > margin;
            needsSnapTop = overflowTop > margin;
            needsSnapBottom = overflowBottom > margin;
        } else {
            // 当前屏幕：只有模型绝大部分超出（屏幕内剩余小于 threshold）才吸附
            const needsSnapHorizontal = visibleWidth < threshold && (overflowLeft > 0 || overflowRight > 0);
            const needsSnapVertical = visibleHeight < threshold && (overflowTop > 0 || overflowBottom > 0);

            needsSnapLeft = overflowLeft > 0 && needsSnapHorizontal;
            needsSnapRight = overflowRight > 0 && needsSnapHorizontal;
            needsSnapTop = overflowTop > 0 && needsSnapVertical;
            needsSnapBottom = overflowBottom > 0 && needsSnapVertical;
        }

        if (!needsSnapLeft && !needsSnapRight && !needsSnapTop && !needsSnapBottom) {
            return null; // 不需要吸附
        }

        // 计算目标位置
        let targetX = model.x;
        let targetY = model.y;

        // 水平方向吸附
        if (needsSnapLeft && needsSnapRight) {
            // 模型比屏幕还宽，居中显示
            targetX = model.x + (screenRight - screenLeft) / 2 - (modelLeft + modelWidth / 2);
        } else if (needsSnapLeft) {
            // 左边超出，向右移动
            targetX = model.x + overflowLeft + margin;
        } else if (needsSnapRight) {
            // 右边超出，向左移动
            targetX = model.x - overflowRight - margin;
        }

        // 垂直方向吸附
        if (needsSnapTop && needsSnapBottom) {
            // 模型比屏幕还高，居中显示
            targetY = model.y + (screenBottom - screenTop) / 2 - (modelTop + modelHeight / 2);
        } else if (needsSnapTop) {
            // 上边超出，向下移动
            targetY = model.y + overflowTop + margin;
        } else if (needsSnapBottom) {
            // 下边超出，向上移动
            targetY = model.y - overflowBottom - margin;
        }

        // 验证目标位置
        if (!Number.isFinite(targetX) || !Number.isFinite(targetY)) {
            console.warn('计算的吸附目标位置无效');
            return null;
        }

        // 如果位置变化太小，不执行吸附
        const dx = Math.abs(targetX - model.x);
        const dy = Math.abs(targetY - model.y);
        if (dx < 1 && dy < 1) {
            return null;
        }

        return {
            startX: model.x,
            startY: model.y,
            targetX: targetX,
            targetY: targetY,
            overflow: {
                left: overflowLeft,
                right: overflowRight,
                top: overflowTop,
                bottom: overflowBottom
            }
        };
    } catch (error) {
        console.error('检测吸附时出错:', error);
        return null;
    }
};

/**
 * 执行平滑吸附动画
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 * @param {Object} snapInfo - 吸附信息（由 _checkSnapRequired 返回）
 * @returns {Promise<boolean>} 动画完成后返回 true
 */
Live2DManager.prototype._performSnapAnimation = function (model, snapInfo) {
    return new Promise((resolve) => {
        if (!model || !snapInfo) {
            resolve(false);
            return;
        }

        const { startX, startY, targetX, targetY } = snapInfo;
        const duration = SNAP_CONFIG.animationDuration;
        const easingFn = EasingFunctions[SNAP_CONFIG.easingType] || EasingFunctions.easeOutCubic;

        const startTime = performance.now();

        // 标记正在执行吸附动画，防止其他操作干扰
        this._isSnapping = true;

        const animate = (currentTime) => {
            // 检查模型是否仍然有效
            if (!model || model.destroyed) {
                this._isSnapping = false;
                resolve(false);
                return;
            }

            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            const easedProgress = easingFn(progress);

            // 计算当前位置
            model.x = startX + (targetX - startX) * easedProgress;
            model.y = startY + (targetY - startY) * easedProgress;

            if (progress < 1) {
                requestAnimationFrame(animate);
            } else {
                // 确保最终位置精确
                model.x = targetX;
                model.y = targetY;
                this._isSnapping = false;

                console.debug('[Live2D] 吸附动画完成，最终位置:', targetX, targetY);
                resolve(true);
            }
        };

        console.debug('[Live2D] 开始吸附动画:', { from: { x: startX, y: startY }, to: { x: targetX, y: targetY } });
        requestAnimationFrame(animate);
    });
};

/**
 * 检测并执行自动吸附（主入口函数）
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 * @param {Object} options - 可选参数
 * @param {boolean} options.afterDisplaySwitch - 是否为屏幕切换后的吸附（使用更宽松的条件）
 * @returns {Promise<boolean>} 是否执行了吸附
 */
Live2DManager.prototype._checkAndPerformSnap = async function (model, options = {}) {
    if (!this._isModelReadyForInteraction && !options.allowWhenNotReady) {
        return false;
    }
    // 如果正在执行吸附动画，跳过
    if (this._isSnapping) {
        return false;
    }
    // 跨屏切换期间跳过吸附：窗口 setBounds 与 innerWidth/innerHeight 更新之间有一帧延迟，
    // 中间读到的 clamp 边界会是旧值，触发误吸附。afterDisplaySwitch 路径自己会清标志后再 snap。
    if (this._pendingDisplaySwitch && !options.afterDisplaySwitch) {
        return false;
    }

    const snapInfo = await this._checkSnapRequired(model, options);

    if (!snapInfo) {
        return false;
    }

    console.log('[Live2D] 检测到模型超出屏幕边界，执行自动吸附');
    console.debug('[Live2D] 超出信息:', snapInfo.overflow);

    const animated = await this._performSnapAnimation(model, snapInfo);

    if (animated) {
        // 吸附完成后保存位置
        await this._savePositionAfterInteraction();
    }

    return animated;
};

// 设置拖拽功能
Live2DManager.prototype.setupDragAndDrop = function (model) {
    model.interactive = true;
    // 移除 stage.hitArea = screen，避免阻挡背景点击
    // this.pixi_app.stage.interactive = true;
    // this.pixi_app.stage.hitArea = this.pixi_app.screen;

    this._isDraggingModel = false;
    let dragStartPos = new PIXI.Point();

    // 点击检测相关变量
    let clickStartTime = 0;
    let clickStartX = 0;
    let clickStartY = 0;
    let hasMoved = false;
    const CLICK_THRESHOLD_DISTANCE = 10; // 移动距离阈值（像素）
    const CLICK_THRESHOLD_TIME = 300; // 时间阈值（毫秒）

    // 使用 avatar-ui-drag.js 中的共享工具函数（按钮 pointer-events 管理）
    const disableButtonPointerEvents = () => {
        if (window.DragHelpers) {
            window.DragHelpers.disableButtonPointerEvents();
        }
    };

    const restoreButtonPointerEvents = () => {
        if (window.DragHelpers) {
            window.DragHelpers.restoreButtonPointerEvents();
        }
    };



    // 点击触发随机表情和动作（低优先级，会自动恢复）
    // 使用最低优先级 IDLE=1，确保不会覆盖对话等高优先级动作
    window.live2dManager.CLICK_MOTION_PRIORITY = 2; // IDLE priority
    window.live2dManager.CLICK_EFFECT_DURATION = 5000; // 点击效果持续时间（毫秒）

   

    model.on('pointerdown', (event) => {
        if (!this._isModelReadyForInteraction) return;
        if (this.isLocked) return;

        // 检测是否为触摸事件，且是多点触摸（双指缩放）
        const originalEvent = event.data.originalEvent;
        if (originalEvent && originalEvent.touches && originalEvent.touches.length > 1) {
            // 多点触摸时不启动拖拽
            return;
        }

        this._isDraggingModel = true;
        this.isFocusing = false; // 拖拽时禁用聚焦
        const globalPos = event.data.global;
        dragStartPos.x = globalPos.x - model.x;
        dragStartPos.y = globalPos.y - model.y;

        // 记录点击开始信息
        clickStartTime = Date.now();
        clickStartX = globalPos.x;
        clickStartY = globalPos.y;
        hasMoved = false;

        document.getElementById('live2d-canvas').style.cursor = 'grabbing';

        // 开始拖动时，临时禁用按钮的 pointer-events
        disableButtonPointerEvents();
    });

    const onDragEnd = async () => {
        if (this._isDraggingModel) {
            this._isDraggingModel = false;
            document.getElementById('live2d-canvas').style.cursor = '';
            restoreButtonPointerEvents();

            if (!this._isModelReadyForInteraction) return;

            // 检测是否为点击（非拖拽）
            const clickDuration = Date.now() - clickStartTime;
            if (!hasMoved && clickDuration < CLICK_THRESHOLD_TIME) {
                // 这是一个点击
                console.log(`[Interaction] 检测到点击（时长: ${clickDuration}ms）`);
                
                // 只在教程模式下，通过点击检测触发随机动画
                // 非教程模式下，通过 hit 事件处理
                await new Promise(resolve => setTimeout(resolve, 300));

                if(window.live2dManager.touchSetHitEventLock){
                    window.live2dManager.touchSetHitEventLock = false;
                    return;
                }
                const UseBlock = "default";

                // 滤波 毫秒
                window.live2dManager.touchSetFilter = window.live2dManager.touchSetFilter || {};
                if(!window.live2dManager.touchSetFilter[UseBlock]){
                    window.live2dManager.touchSetFilter[UseBlock]= Date.now();
                }else{
                    let timenow = Date.now();
                    if(timenow - window.live2dManager.touchSetFilter[UseBlock] > 500){
                        window.live2dManager.touchSetFilter[UseBlock]= timenow;
                    }else{
                        // 似乎按下和松开都算一次触发?
                        // console.error(timenow - window.live2dManager.touchSetFilter[UseBlock])
                        return;
                    }
                }




                const modelName = window.live2dManager.modelName;
                const touchSet = window.live2dManager.touchSet && window.live2dManager.touchSet[modelName];

                const d = touchSet && touchSet[UseBlock];

                if (!d || (d.expressions.length == 0 && d.motions.length == 0)) {
                    // 无配置或配置为空 → 随机播放表情+动作
                    await window.live2dManager.triggerRandomEmotion();
                } else {
                    await window.live2dManager._playTouchSetAnimation(UseBlock);
                }
                
                return; // 点击不需要保存位置
            }

            // 检测是否需要切换屏幕（多屏幕支持）
            // _checkAndSwitchDisplay returns true if a display switch occurred (and saved internally)
            const displaySwitched = await this._checkAndSwitchDisplay(model);

            // 如果没有发生屏幕切换，检测并执行自动吸附
            if (!displaySwitched) {
                // 执行自动吸附检测和动画
                const snapped = await this._checkAndPerformSnap(model);

                // 如果没有执行吸附，则正常保存位置
                if (!snapped) {
                    await this._savePositionAfterInteraction();
                }
                // 如果执行了吸附，_checkAndPerformSnap 内部会保存位置
            }
        }
    };

    const onDragMove = (event) => {
        if (!this._isModelReadyForInteraction) return;
        if (this._isDraggingModel) {
            // 再次检查是否变成多点触摸
            if (event.touches && event.touches.length > 1) {
                // 如果变成多点触摸，停止拖拽
                this._isDraggingModel = false;
                document.getElementById('live2d-canvas').style.cursor = '';
                // 【维护注意】所有退出拖拽的路径都必须调用 restoreButtonPointerEvents，
                //  否则 body 上的 neko-model-dragging class 不会被移除，按钮将永久失效。
                restoreButtonPointerEvents();
                return;
            }

            // 将 window 坐标转换为 Pixi 全局坐标 (通常在全屏下是一样的，但为了保险)
            // 这里假设 canvas 是全屏覆盖的
            const x = event.clientX;
            const y = event.clientY;

            // 检测是否移动超过阈值
            const moveDistance = Math.sqrt(
                Math.pow(x - clickStartX, 2) + Math.pow(y - clickStartY, 2)
            );
            if (moveDistance > CLICK_THRESHOLD_DISTANCE) {
                hasMoved = true;
            }

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
Live2DManager.prototype.setupWheelZoom = function (model) {
    const onWheelScroll = (event) => {
        if (this.isLocked || !this.currentModel) return;
        event.preventDefault();

        // 根据 deltaY 大小动态计算缩放因子，避免固定倍率导致缩放过快
        // 鼠标滚轮通常 deltaY ≈ ±100，触控板 deltaY ≈ ±1~30
        const absDelta = Math.abs(event.deltaY);
        // 将 deltaY 映射到 0~0.08 的缩放增量（最大约 8%）
        const zoomStep = Math.min(absDelta / 1000, 0.08);
        const scaleFactor = 1 + zoomStep;

        const oldScale = this.currentModel.scale.x;
        let newScale = event.deltaY < 0 ? oldScale * scaleFactor : oldScale / scaleFactor;

        // 钳制缩放下限（MAX 暂不实施）
        newScale = Math.max(SCALE_LIMITS.MIN, newScale);

        this.currentModel.scale.set(newScale);

        // 缩放后触发分级恢复检测（含保存），替代原 _debouncedSavePosition
        this._debouncedSnapCheck();
    };

    const view = this.pixi_app.view;
    if (view.lastWheelListener) {
        view.removeEventListener('wheel', view.lastWheelListener);
    }
    view.addEventListener('wheel', onWheelScroll, { passive: false });
    view.lastWheelListener = onWheelScroll;
};

// 设置触摸缩放（双指捏合）
Live2DManager.prototype.setupTouchZoom = function (model) {
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

            // 限制缩放范围，与滚轮缩放保持一致
            newScale = Math.max(SCALE_LIMITS.MIN, Math.min(SCALE_LIMITS.MAX, newScale));

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
Live2DManager.prototype.enableMouseTracking = function (model, options = {}) {
    const { threshold = 70, HoverFadethreshold = 40 } = options; // 增加默认变淡阈值，从 5px 增加到 40px

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

        // isFocusing 用于控制眼睛跟踪，悬浮菜单显示不受影响
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
        const hasOpenOverlay = () => {
            const popupUi = window.AvatarPopupUI || null;
            return !!(popupUi && typeof popupUi.hasVisibleOverlay === 'function' && popupUi.hasVisibleOverlay('live2d'));
        };
        const isPointerNearLock = () => {
            if (!lockIcon || lockIcon.style.display !== 'block') return false;
            const x = this._lastMouseX;
            const y = this._lastMouseY;
            if (!Number.isFinite(x) || !Number.isFinite(y)) return false;
            const rect = lockIcon.getBoundingClientRect();
            const expandPx = 8;
            return x >= rect.left - expandPx && x <= rect.right + expandPx &&
                y >= rect.top - expandPx && y <= rect.bottom + expandPx;
        };

        if (this._goodbyeClicked) return;

        // 引导模式下不隐藏浮动按钮
        if (window.isInTutorial === true) return;

        // 如果已有定时器，不重复创建
        if (this._hideButtonsTimer) return;

        this._hideButtonsTimer = setTimeout(() => {
            // 引导模式下不隐藏
            if (window.isInTutorial === true) {
                this._hideButtonsTimer = null;
                return;
            }

            // 再次检查鼠标是否在按钮区域内
            if (this._isMouseOverButtons || isPointerNearLock() || hasOpenOverlay()) {
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

    const live2dContainer = document.getElementById('live2d-container');
    let ctrlFadeActive = false;      // Ctrl 按住淡化
    let stationaryFadeActive = false; // 静止1秒淡化
    const applyFade = () => {
        if (!live2dContainer) return;
        const shouldFade = (ctrlFadeActive || stationaryFadeActive) && window.lockedHoverFadeEnabled !== false;
        live2dContainer.classList.toggle('locked-hover-fade', shouldFade);
    };

    // 监听锁定悬停淡化设置变更
    const onLockedHoverFadeChanged = () => {
        if (window.lockedHoverFadeEnabled === false) {
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
        }
    };
    if (this._lockedHoverFadeChangedListener) {
        window.removeEventListener('neko-locked-hover-fade-changed', this._lockedHoverFadeChangedListener);
    }
    this._lockedHoverFadeChangedListener = onLockedHoverFadeChanged;
    window.addEventListener('neko-locked-hover-fade-changed', onLockedHoverFadeChanged);

    // 跟踪 Ctrl 键状态（作为备用，主要从事件中直接读取）
    let isCtrlPressed = false;

    // 静止自动淡化：鼠标在模型范围内静止1秒后自动淡化
    this._stationaryFadeTimer = null;
    this._hasEnteredHoverRange = false; // 是否已进入过模型范围
    const STATIONARY_FADE_DELAY = 1000;

    const clearStationaryFadeTimer = () => {
        if (this._stationaryFadeTimer !== null) {
            clearTimeout(this._stationaryFadeTimer);
            this._stationaryFadeTimer = null;
        }
    };
    this._clearStationaryFadeTimer = clearStationaryFadeTimer;

    // 清理旧的键盘监听器（在添加新监听器之前）
    if (this._ctrlKeyDownListener) {
        window.removeEventListener('keydown', this._ctrlKeyDownListener);
    }
    if (this._ctrlKeyUpListener) {
        window.removeEventListener('keyup', this._ctrlKeyUpListener);
    }

    // 监听 Ctrl 键按下/释放事件（用于在鼠标不在窗口内时也能检测）
    const onKeyDown = (event) => {
        // 检查是否按下 Ctrl 或 Cmd 键
        if (event.ctrlKey || event.metaKey) {
            isCtrlPressed = true;
        }
    };

    const onKeyUp = (event) => {
        // 检查 Ctrl 或 Cmd 键是否释放
        if (!event.ctrlKey && !event.metaKey) {
            isCtrlPressed = false;
            // Ctrl 释放时重新计算淡化状态，让 stationaryFadeActive 有机会生效
            ctrlFadeActive = false;
            applyFade();
        }
    };

    // 添加全局键盘事件监听
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);

    // 保存监听器引用以便清理
    this._ctrlKeyDownListener = onKeyDown;
    this._ctrlKeyUpListener = onKeyUp;

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
        if (!this._isModelReadyForInteraction) return;
        // 更新 Ctrl 键状态：综合事件中的状态和本地状态
        // 如果是真实事件，更新本地状态；如果是模拟事件，本地状态保持不变（除非事件里带了 Ctrl）
        if (event.isTrusted) {
            isCtrlPressed = event.ctrlKey || event.metaKey;
        } else if (event.ctrlKey || event.metaKey) {
            // 如果模拟事件带了 Ctrl 键，也更新本地状态以供后续逻辑使用
            isCtrlPressed = true;
        }

        // 最终用于变淡判断的 Ctrl 状态
        const ctrlKeyPressed = event.ctrlKey || event.metaKey || isCtrlPressed;

        // 检查模型是否存在，防止切换模型时出现错误
        if (!model) {
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
            return;
        }

        // 检查模型是否已被销毁或不在舞台上
        if (model.destroyed || !model.parent || !this.pixi_app || !this.pixi_app.stage) {
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
            return;
        }
        
        // 检查当前模型是否仍然是传入的模型（防止模型切换后使用旧的模型引用）
        if (this.currentModel !== model) {
            // 模型已切换，清理监听器
            if (this._mouseTrackingListener) {
                window.removeEventListener('pointermove', this._mouseTrackingListener);
                this._mouseTrackingListener = null;
            }
            return;
        }
        
        // 检查模型是否仍在舞台上（防止模型被销毁或移除后仍然调用）
        if (!model.parent) {
            // 模型已被从舞台移除，清理监听器
            if (this._mouseTrackingListener) {
                window.removeEventListener('pointermove', this._mouseTrackingListener);
                this._mouseTrackingListener = null;
            }
            return;
        }
        
        // 检查模型是否已被销毁（检查关键属性是否存在）
        // 注意：某些PIXI版本可能没有destroyed属性，所以使用可选链
        if (model.destroyed === true) {
            return;
        }
        
        // 使用 clientX/Y 作为全局坐标
        const pointer = { x: event.clientX, y: event.clientY };
        this._lastMouseX = pointer.x;
        this._lastMouseY = pointer.y;

        // 在拖拽期间不执行任何操作
        if ((model.interactive && model.dragging) || this._isDraggingModel) {
            return;
        }
        // 如果已经点击了"请她离开"，特殊处理
        if (this._goodbyeClicked) {
            const lockIcon = document.getElementById('live2d-lock-icon');
            const floatingButtons = document.getElementById('live2d-floating-buttons');

            if (lockIcon) {
                lockIcon.style.setProperty('display', 'none', 'important');
            }
            // goodbye 状态下这里只维护锁图标/浮动按钮可见性。
            // 返回球必须由 app-ui.js 在完成定位后再显示，避免先以默认 (0, 0) 闪现。
            if (floatingButtons) {
                floatingButtons.style.display = 'none';
            }
            ctrlFadeActive = false;
            stationaryFadeActive = false;
            applyFade();
            return;
        }

        try {
            // 在调用 getBounds 前再次检查模型是否有效
            if (!model.parent || model.destroyed) {
                return;
            }
            const bounds = model.getBounds();

            // 使用椭圆近似检测（基于完整模型边界，椭圆可以部分在屏幕外）
            const centerX = (bounds.left + bounds.right) / 2;
            const centerY = (bounds.top + bounds.bottom) / 2;
            const width = bounds.right - bounds.left;
            const height = bounds.bottom - bounds.top;

            let distance;
            // 防止除零：当宽度或高度接近零时，回退到矩形距离计算
            if (width < 1 || height < 1) {
                const dx = Math.max(bounds.left - pointer.x, 0, pointer.x - bounds.right);
                const dy = Math.max(bounds.top - pointer.y, 0, pointer.y - bounds.bottom);
                distance = Math.sqrt(dx * dx + dy * dy);
            } else {
                // 椭圆半径比例（相对于边界框）
                const ellipseRadiusX = width * 0.3;
                const ellipseRadiusY = height * 0.45;

                // 计算点到椭圆的归一化距离
                const normalizedX = (pointer.x - centerX) / ellipseRadiusX;
                const normalizedY = (pointer.y - centerY) / ellipseRadiusY;
                const ellipseDistance = Math.sqrt(normalizedX * normalizedX + normalizedY * normalizedY);

                // 将椭圆距离转换为像素距离（用于阈值比较）
                // ellipseDistance <= 1 表示在椭圆内部，distance = 0
                // ellipseDistance > 1 表示在椭圆外部，distance 为超出椭圆边缘的等效像素距离
                distance = ellipseDistance <= 1 ? 0 : (ellipseDistance - 1) * Math.min(ellipseRadiusX, ellipseRadiusY);
            }

            // 检查是否启用了全屏跟踪
            const isFullscreenTracking = this.isFullscreenTrackingEnabled ? this.isFullscreenTrackingEnabled() : false;

            // 额外检查：鼠标必须在模型可见区域附近（除非启用全屏跟踪）
            const isPointerNearVisibleModel = pointer.x >= bounds.left - threshold && pointer.x <= bounds.right + threshold &&
                                              pointer.y >= Math.max(bounds.top, 0) - threshold && pointer.y <= Math.min(bounds.bottom, window.innerHeight) + threshold;

            // 如果鼠标不在屏幕内或不在模型可见区域附近，且未启用全屏跟踪，则视为远离模型
            if (!isPointerNearVisibleModel && !isFullscreenTracking) {
                this.isFocusing = false;
                startHideTimer();
                clearStationaryFadeTimer();
                ctrlFadeActive = false;
                stationaryFadeActive = false;
                applyFade();
                return;
            }

            const isNearModel = distance < HoverFadethreshold;

            // 鼠标在 UI 元素（锁图标 / 浮动按钮）上时，重置淡化状态，
            // 防止离开 UI 后残留的 stationaryFadeActive 立即重新触发淡化
            const live2dLockIcon = document.getElementById('live2d-lock-icon');
            const live2dFloatingBtns = document.getElementById('live2d-floating-buttons');
            let isOverUi = false;
            if (live2dLockIcon && live2dLockIcon.style.display !== 'none') {
                const lr = live2dLockIcon.getBoundingClientRect();
                if (pointer.x >= lr.left && pointer.x <= lr.right && pointer.y >= lr.top && pointer.y <= lr.bottom) isOverUi = true;
            }
            if (!isOverUi && live2dFloatingBtns && live2dFloatingBtns.style.display !== 'none') {
                const br = live2dFloatingBtns.getBoundingClientRect();
                if (pointer.x >= br.left && pointer.x <= br.right && pointer.y >= br.top && pointer.y <= br.bottom) isOverUi = true;
            }
            if (isOverUi) {
                clearStationaryFadeTimer();
                ctrlFadeActive = false;
                stationaryFadeActive = false;
                this._hasEnteredHoverRange = false;
                applyFade();
            }

            // 静止时启动定时器，移出范围时清除（移动端无鼠标悬停，跳过）
            const isMobileDevice = (window.appUtils && typeof window.appUtils.isMobile === 'function' && window.appUtils.isMobile()) || /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
            if (!isMobileDevice && this.isLocked && isNearModel && !isOverUi) {
                // 首次进入范围：设置标志并启动定时器
                if (!this._hasEnteredHoverRange) {
                    this._hasEnteredHoverRange = true;
                    if (this._stationaryFadeTimer === null && !stationaryFadeActive) {
                        this._stationaryFadeTimer = setTimeout(() => {
                            stationaryFadeActive = true;
                            applyFade();
                        }, STATIONARY_FADE_DELAY);
                    }
                }
                // 已在范围内：移动时不重启定时器，只更新位置
            } else {
                // 移出范围：清除定时器并重置标志
                if (this._stationaryFadeTimer !== null || stationaryFadeActive) {
                    clearStationaryFadeTimer();
                    stationaryFadeActive = false;
                    applyFade();
                }
                this._hasEnteredHoverRange = false;
            }

            // Ctrl 淡化：锁定 + Ctrl + 在模型范围内（独立于静止淡化，移动端跳过，UI 上时跳过）
            ctrlFadeActive = !isMobileDevice && this.isLocked && ctrlKeyPressed && isNearModel && !isOverUi;
            applyFade();

            const canvasEl = document.getElementById('live2d-canvas');

            if (distance < threshold) {
                showButtons();
                if (canvasEl && !this.isLocked && !(model.interactive && model.dragging)) {
                    // hitTest + 椭圆内部判定（0.3w × 0.45h），不外扩
                    let isOnModel = false;
                    try {
                        const hitAreas = model.hitTest(pointer.x, pointer.y);
                        if (hitAreas && hitAreas.length > 0) isOnModel = true;
                    } catch (_) {}
                    if (!isOnModel) isOnModel = distance === 0;
                    canvasEl.style.cursor = isOnModel ? 'grab' : '';
                }
                const isMouseTrackingEnabled = this.isMouseTrackingEnabled ? this.isMouseTrackingEnabled() : (window.mouseTrackingEnabled !== false);
                if (this.isFocusing) {
                    if (isMouseTrackingEnabled) {
                        model.focus(pointer.x, pointer.y);
                    } else {
                        if (model.internalModel && model.internalModel.focusController) {
                            const fc = model.internalModel.focusController;
                            fc.targetX = 0;
                            fc.targetY = 0;
                        }
                    }
                }
            } else if (isFullscreenTracking) {
                if (canvasEl && !this.isLocked && !(model.interactive && model.dragging)) {
                    canvasEl.style.cursor = 'grab';
                }
                const isMouseTrackingEnabled = this.isMouseTrackingEnabled ? this.isMouseTrackingEnabled() : (window.mouseTrackingEnabled !== false);
                if (isMouseTrackingEnabled) {
                    model.focus(pointer.x, pointer.y);
                } else {
                    if (model.internalModel && model.internalModel.focusController) {
                        const fc = model.internalModel.focusController;
                        fc.targetX = 0;
                        fc.targetY = 0;
                    }
                }
            } else {
                this.isFocusing = false;
                if (canvasEl && !(model.interactive && model.dragging)) {
                    canvasEl.style.cursor = '';
                }
                startHideTimer();
            }
        } catch (error) {
            // 静默处理错误，避免控制台刷屏
            // 只在开发模式下输出详细错误信息
            if (window.DEBUG || window.location.hostname === 'localhost') {
                console.error('Live2D 交互错误:', error);
            }
        }
    };

    // 窗口失去焦点时，只重置淡化效果，不重置 Ctrl 键状态
    // 这样窗口重新获得焦点后，如果 Ctrl 仍被按住，淡化功能可以恢复
    const onBlur = () => {
        // blur 时 Ctrl 键事件无法到达，必须主动清除 Ctrl 状态
        isCtrlPressed = false;
        ctrlFadeActive = false;
        clearStationaryFadeTimer();
        // blur 时清除定时器和淡化状态，焦点恢复后需重新触发
        if (stationaryFadeActive) {
            stationaryFadeActive = false;
        }
        applyFade();
        this._hasEnteredHoverRange = false;
    };

    // 清理旧的监听器
    if (this._mouseTrackingListener) {
        window.removeEventListener('pointermove', this._mouseTrackingListener);
    }
    if (this._windowBlurListener) {
        window.removeEventListener('blur', this._windowBlurListener);
    }

    // 保存新的监听器引用
    this._mouseTrackingListener = onPointerMove;
    this._windowBlurListener = onBlur;

    // 使用 window 监听鼠标移动和窗口失去焦点
    window.addEventListener('pointermove', onPointerMove);
    window.addEventListener('blur', onBlur);

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

/**
 * 播放临时点击效果（低优先级，会自动恢复）
 * @param {string} emotion - 情感名称
 * @param {number} priority - 动作优先级 (1=IDLE, 2=NORMAL, 3=FORCE)
 * @param {number} duration - 效果持续时间（毫秒）
 */
Live2DManager.prototype._playTemporaryClickEffect = async function(emotion, priority = 1, duration = 3000) {
    if (!this.currentModel) {
        console.warn('[ClickEffect] 无法播放：模型未加载');
        return;
    }

    // 清除之前的点击效果恢复定时器
    if (this._clickEffectRestoreTimer) {
        clearTimeout(this._clickEffectRestoreTimer);
        this._clickEffectRestoreTimer = null;
    }
    
    if (this._clickEffectMotion && typeof this._clickEffectMotion.stop === 'function') {
        try { this._clickEffectMotion.stop(); } catch (e) {}
    }
    this._clickEffectMotion = null;

    try {
        // 1. 播放表情（如果有配置）
        let expressionFiles = [];
        if (this.emotionMapping && this.emotionMapping.expressions && this.emotionMapping.expressions[emotion]) {
            expressionFiles = this.emotionMapping.expressions[emotion];
        }
        
        // 兼容旧结构：按 emotion 前缀匹配
        if (expressionFiles.length === 0 && this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
            const candidates = this.fileReferences.Expressions.filter(e => (e.Name || '').startsWith(emotion));
            expressionFiles = candidates.map(e => e.File).filter(Boolean);
        }

        // 最终兜底：如果仍然没有匹配到，使用全部可用表情随机播放
        if (expressionFiles.length === 0 && this.fileReferences && Array.isArray(this.fileReferences.Expressions) && this.fileReferences.Expressions.length > 0) {
            expressionFiles = this.fileReferences.Expressions.map(e => e.File).filter(Boolean);
        }

        if (expressionFiles.length > 0) {
            // 跳过已确认失效的 expression，避免每次点击都重复 404

            if (typeof this.isExpressionFileMissing === 'function') {
                expressionFiles = expressionFiles.filter(file => !this.isExpressionFileMissing(file));
            }

            const choiceFile = this.getRandomElement(expressionFiles);
            if (choiceFile && typeof this.playExpression === 'function') {
                console.log(`[ClickEffect] 播放临时表情: ${choiceFile}`);
                await this.playExpression(emotion, choiceFile);
            }
        } else {
            console.log("[ClickEffect] 没找到可用表情")
        }

        // 2. 播放低优先级动作
        let motions = null;
        let motionGroup = emotion; // 用于 this.currentModel.motion(group, index, priority)
        if (this.fileReferences && this.fileReferences.Motions && this.fileReferences.Motions[emotion]) {
            motions = this.fileReferences.Motions[emotion];
        } else if (this.emotionMapping && this.emotionMapping.motions && this.emotionMapping.motions[emotion]) {
            const emotionMotions = this.emotionMapping.motions[emotion];
            if (Array.isArray(emotionMotions) && emotionMotions.length > 0) {
                if (typeof emotionMotions[0] === 'string') {
                    motions = emotionMotions.map(f => ({ File: f }));
                } else {
                    motions = emotionMotions;
                }
            }
        }

        // 兜底：emotion 对不上任何 motion group 时，从所有可用 group 随机选一个
        if ((!motions || motions.length === 0) && this.fileReferences && this.fileReferences.Motions) {
            const allGroups = Object.keys(this.fileReferences.Motions).filter(
                g => Array.isArray(this.fileReferences.Motions[g]) && this.fileReferences.Motions[g].length > 0
            );
            if (allGroups.length > 0) {
                motionGroup = allGroups[Math.floor(Math.random() * allGroups.length)];
                motions = this.fileReferences.Motions[motionGroup];
            }
        }

        if (motions && motions.length > 0) {
            // 使用低优先级播放动作
            // pixi-live2d-display 的 motion(group, index, priority) 支持优先级参数
            try {
                const motion = await this.currentModel.motion(motionGroup, undefined, priority);
                if (motion) {
                    console.log(`[ClickEffect] 播放临时动作: ${motionGroup}（优先级: ${priority}）`);
                    this._clickEffectMotion = motion;
                }
            } catch (motionError) {
                console.warn('[ClickEffect] 动作播放失败:', motionError);
            }
        }

        // 3. 设置恢复定时器
        // 使用唯一 ID 标记此次点击效果，用于判断是否应该恢复
        const clickEffectId = Date.now();
        this._currentClickEffectId = clickEffectId;
        
        this._clickEffectRestoreTimer = setTimeout(() => {
            this._clickEffectRestoreTimer = null;
            
            // 检查是否仍然是此次点击效果（没有被新的情感/点击覆盖）
            if (this._currentClickEffectId !== clickEffectId) {
                console.log('[ClickEffect] 临时效果已被新的情感覆盖，跳过恢复');
                return;
            }
            
            console.log('[ClickEffect] 临时效果结束，平滑恢复到默认状态');
            this._currentClickEffectId = null;
            this._clickEffectMotion = null;

            // 使用平滑过渡恢复到初始状态
            // smoothResetToInitialState 会在第一帧 beforeModelUpdate 中捕获快照后，
            // 再停止 motion/expression，确保过渡起点与屏幕一致，无视觉跳变。
            if (typeof this.smoothResetToInitialState === 'function') {
                this.smoothResetToInitialState().catch(e => {
                    console.warn('[ClickEffect] 平滑恢复失败，回退到即时恢复:', e);
                    if (typeof this.clearExpression === 'function') {
                        this.clearExpression();
                    }
                });
            } else if (typeof this.clearExpression === 'function') {
                this.clearExpression();
            }
        }, duration);

        console.log(`[ClickEffect] 临时效果将在 ${duration}ms 后恢复`);

    } catch (error) {
        console.error('[ClickEffect] 播放临时效果失败:', error);
    }
};

// 交互后保存位置和缩放的辅助函数
Live2DManager.prototype._savePositionAfterInteraction = async function () {
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

    // 使用渲染器逻辑尺寸作为归一化基准（renderer 不再自动 resize，尺寸与稳定屏幕分辨率等价）
    let viewportInfo = null;
    if (this.pixi_app && this.pixi_app.renderer) {
        const rw = this.pixi_app.renderer.screen.width;
        const rh = this.pixi_app.renderer.screen.height;
        if (Number.isFinite(rw) && Number.isFinite(rh) && rw > 0 && rh > 0) {
            viewportInfo = { width: rw, height: rh };
        }
    }

    // 异步保存，不阻塞交互
    this.saveUserPreferences(this._lastLoadedModelPath, position, scale, null, displayInfo, viewportInfo)
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
Live2DManager.prototype._debouncedSavePosition = function () {
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

// 防抖分级恢复检测（用于滚轮缩放后的边界检查 + 位置保存）
Live2DManager.prototype._debouncedSnapCheck = function () {
    if (this._snapCheckTimer) clearTimeout(this._snapCheckTimer);
    // 同时取消可能残留的保存定时器，避免在吸附动画完成前保存中间状态
    if (this._savePositionDebounceTimer) {
        clearTimeout(this._savePositionDebounceTimer);
    }
    this._snapCheckTimer = setTimeout(async () => {
        if (!this.currentModel || this._isSnapping) return;

        // 统一复用现有吸附流程（含守卫、动画、保存）
        // _checkSnapRequired 会根据 overflow 方向计算最近边缘，
        // 无论模型是部分出界还是完全消失都能正确处理
        const snapped = await this._checkAndPerformSnap(this.currentModel);
        if (!snapped) {
            // 未触发吸附（模型在合理范围内），仅保存缩放后的位置
            await this._savePositionAfterInteraction();
        }
    }, 300);  // 300ms 防抖，等待连续滚轮操作结束
};

// 多屏幕支持：检测模型是否移出当前屏幕并切换到新屏幕
// Returns true if a display switch occurred (and position was saved internally), false otherwise
Live2DManager.prototype._checkAndSwitchDisplay = async function (model) {
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

            // 切换期间屏蔽常规吸附，防止中间态用旧窗口尺寸做 clamp 导致误吸附
            this._pendingDisplaySwitch = true;
            try {
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

                    // 屏幕切换后，延迟两帧再检测是否需要吸附
                    // 两帧：一帧给 setBounds 落地，一帧给 resize 事件刷新 innerWidth/Height
                    await new Promise(resolve => requestAnimationFrame(resolve));
                    await new Promise(resolve => requestAnimationFrame(resolve));

                    // 检测并执行自动吸附（切换到新屏幕后模型可能仍超出边界）
                    // 屏幕切换后使用更宽松的吸附条件（只要超出就吸附）
                    const snapped = await this._checkAndPerformSnap(model, { afterDisplaySwitch: true });

                    // 如果没有执行吸附，保存位置
                    if (!snapped) {
                        await this._savePositionAfterInteraction();
                    }
                    // 如果执行了吸附，_checkAndPerformSnap 内部会保存位置

                    return true;  // Display switch occurred
                }
            } finally {
                this._pendingDisplaySwitch = false;
            }
        }
        return false;  // No display switch occurred
    } catch (error) {
        this._pendingDisplaySwitch = false;
        console.error('[Live2D] 检测/切换屏幕时出错:', error);
        return false;
    }
};

// setupResizeSnapDetection 已移除：渲染器仅在真实屏幕分辨率变化时 resize，不再需要吸附检测

/**
 * 手动触发吸附检测（供外部调用）
 * @returns {Promise<boolean>} 是否执行了吸附
 */
Live2DManager.prototype.snapToScreen = async function () {
    if (!this.currentModel) {
        console.warn('[Live2D] 无法执行吸附：模型未加载');
        return false;
    }

    return await this._checkAndPerformSnap(this.currentModel);
};

/**
 * 更新吸附配置
 * @param {Object} config - 配置对象
 * @param {number} [config.threshold] - 吸附阈值（像素）
 * @param {number} [config.margin] - 吸附边距（像素）
 * @param {number} [config.animationDuration] - 动画持续时间（毫秒）
 * @param {string} [config.easingType] - 缓动函数类型
 */
Live2DManager.prototype.setSnapConfig = function (config) {
    if (!config) return;

    if (typeof config.threshold === 'number' && config.threshold >= 0) {
        SNAP_CONFIG.threshold = config.threshold;
    }
    if (typeof config.margin === 'number' && config.margin >= 0) {
        SNAP_CONFIG.margin = config.margin;
    }
    if (typeof config.animationDuration === 'number' && config.animationDuration > 0) {
        SNAP_CONFIG.animationDuration = config.animationDuration;
    }
    if (typeof config.easingType === 'string' && EasingFunctions[config.easingType]) {
        SNAP_CONFIG.easingType = config.easingType;
    }

    console.debug('[Live2D] 吸附配置已更新:', SNAP_CONFIG);
};

/**
 * 获取当前吸附配置
 * @returns {Object} 当前配置
 */
Live2DManager.prototype.getSnapConfig = function () {
    return { ...SNAP_CONFIG };
};

/**
 * 清理所有全局事件监听器
 * 在 Live2DManager 销毁或页面卸载时调用此方法，防止内存泄漏
 */
Live2DManager.prototype.cleanupEventListeners = function () {
    console.debug('[Live2D] 开始清理全局事件监听器...');

    // 清理拖拽相关的监听器
    if (this._dragEndListener) {
        window.removeEventListener('pointerup', this._dragEndListener);
        window.removeEventListener('pointercancel', this._dragEndListener);
        this._dragEndListener = null;
    }
    if (this._dragMoveListener) {
        window.removeEventListener('pointermove', this._dragMoveListener);
        this._dragMoveListener = null;
    }

    // 清理鼠标跟踪监听器
    if (this._mouseTrackingListener) {
        window.removeEventListener('pointermove', this._mouseTrackingListener);
        this._mouseTrackingListener = null;
    }

    // 清理键盘事件监听器
    if (this._ctrlKeyDownListener) {
        window.removeEventListener('keydown', this._ctrlKeyDownListener);
        this._ctrlKeyDownListener = null;
    }
    if (this._ctrlKeyUpListener) {
        window.removeEventListener('keyup', this._ctrlKeyUpListener);
        this._ctrlKeyUpListener = null;
    }

    // 清理窗口失去焦点监听器
    if (this._windowBlurListener) {
        window.removeEventListener('blur', this._windowBlurListener);
        this._windowBlurListener = null;
    }

    // 清理锁定悬停淡化监听器
    if (this._lockedHoverFadeChangedListener) {
        window.removeEventListener('neko-locked-hover-fade-changed', this._lockedHoverFadeChangedListener);
        this._lockedHoverFadeChangedListener = null;
    }

    // 清理静止淡化定时器
    if (this._clearStationaryFadeTimer) {
        this._clearStationaryFadeTimer();
        this._clearStationaryFadeTimer = null;
    }

    // resize 吸附监听器已移除（setupResizeSnapDetection 不再存在）

    // 清理 canvas 上的滚轮和触摸监听器
    if (this.pixi_app && this.pixi_app.view) {
        const view = this.pixi_app.view;
        if (view.lastWheelListener) {
            view.removeEventListener('wheel', view.lastWheelListener);
            view.lastWheelListener = null;
        }
        if (view.lastTouchStartListener) {
            view.removeEventListener('touchstart', view.lastTouchStartListener);
            view.lastTouchStartListener = null;
        }
        if (view.lastTouchMoveListener) {
            view.removeEventListener('touchmove', view.lastTouchMoveListener);
            view.lastTouchMoveListener = null;
        }
        if (view.lastTouchEndListener) {
            view.removeEventListener('touchend', view.lastTouchEndListener);
            view.lastTouchEndListener = null;
        }
    }

    // 清理隐藏按钮定时器
    if (this._hideButtonsTimer) {
        clearTimeout(this._hideButtonsTimer);
        this._hideButtonsTimer = null;
    }

    // 清理防抖动保存定时器
    if (this._savePositionDebounceTimer) {
        clearTimeout(this._savePositionDebounceTimer);
        this._savePositionDebounceTimer = null;
    }

    // 清理缩放后吸附检测定时器
    if (this._snapCheckTimer) {
        clearTimeout(this._snapCheckTimer);
        this._snapCheckTimer = null;
    }

    // 清理点击效果恢复定时器和 ID
    if (this._clickEffectRestoreTimer) {
        clearTimeout(this._clickEffectRestoreTimer);
        this._clickEffectRestoreTimer = null;
    }
    this._currentClickEffectId = null;

    // 清理页面卸载监听器（如果存在）
    if (this._unloadListener) {
        window.removeEventListener('beforeunload', this._unloadListener);
        this._unloadListener = null;
    }

    console.debug('[Live2D] 全局事件监听器清理完成');
};

/**
 * 设置页面卸载时的自动清理
 * 在初始化 Live2DManager 后调用此方法，确保页面关闭时清理资源
 */
Live2DManager.prototype.setupUnloadCleanup = function () {
    // 避免重复绑定
    if (this._unloadListener) {
        window.removeEventListener('beforeunload', this._unloadListener);
    }

    this._unloadListener = () => {
        this.cleanupEventListeners();
    };

    window.addEventListener('beforeunload', this._unloadListener);

    console.debug('[Live2D] 已设置页面卸载时的自动清理');
};

/**
 * 销毁 Live2DManager 实例
 * 清理所有资源，包括事件监听器、模型、PIXI 应用等
 */
Live2DManager.prototype.destroy = function () {
    console.log('[Live2D] 正在销毁 Live2DManager 实例...');

    // 首先清理所有事件监听器
    this.cleanupEventListeners();

    // 销毁当前模型
    if (this.currentModel) {
        if (this.currentModel.destroy) {
            this.currentModel.destroy();
        }
        this.currentModel = null;
    }

    // 销毁 PIXI 应用
    if (this.pixi_app) {
        this.pixi_app.destroy(true, { children: true, texture: true, baseTexture: true });
        this.pixi_app = null;
    }

    console.log('[Live2D] Live2DManager 实例已销毁');
};



/**
 * 播放教程模式的随机动作
 * @returns {Promise<boolean>} 是否成功播放动作
 */
Live2DManager.prototype.playTutorialMotion = async function() {
    if (!this.currentModel || !this.currentModel.motion) {
        return false;
    }

    const fileRefMotions = this.fileReferences && this.fileReferences.Motions;
    let motionGroups = [];

    if (fileRefMotions && typeof fileRefMotions === 'object') {
        motionGroups = Object.keys(fileRefMotions)
            .filter(group => group !== 'PreviewAll' && Array.isArray(fileRefMotions[group]) && fileRefMotions[group].length > 0);
    }

    if (motionGroups.length === 0 &&
        this.currentModel.internalModel &&
        this.currentModel.internalModel.motionManager &&
        this.currentModel.internalModel.motionManager.definitions) {
        const defs = this.currentModel.internalModel.motionManager.definitions;
        motionGroups = Object.keys(defs)
            .filter(group => group !== 'PreviewAll' && Array.isArray(defs[group]) && defs[group].length > 0);
    }

    if (motionGroups.length === 0) {
        return false;
    }

    const group = this.getRandomElement(motionGroups);
    if (!group) return false;

    const groupList =
        (fileRefMotions && fileRefMotions[group]) ||
        (this.currentModel.internalModel &&
            this.currentModel.internalModel.motionManager &&
            this.currentModel.internalModel.motionManager.definitions &&
            this.currentModel.internalModel.motionManager.definitions[group]) ||
        [];

    if (!Array.isArray(groupList) || groupList.length === 0) {
        return false;
    }

    const index = Math.floor(Math.random() * groupList.length);

    try {
        const motion = await this.currentModel.motion(group, index, window.live2dManager.CLICK_MOTION_PRIORITY);
        // const motion = await this.currentModel.motion(group, index, 2);
        if (motion) {
            console.log(`[Interaction] 教程模式 - 播放动作: ${group}[${index}]（优先级: ${window.live2dManager.CLICK_MOTION_PRIORITY}）`);
            // console.log(`[Interaction] 教程模式 - 播放动作: ${group}[${index}]（优先级: ${2}）`);
            return true;
        }
    } catch (error) {
        console.warn('[Interaction] 教程模式 - 动作播放失败:', error);
    }

    return false;
};

/**
 * 触发随机表情和动作（用于教程模式和点击空白区域）
 */
Live2DManager.prototype.triggerRandomEmotion = async function() {
    // 清除之前的点击效果恢复定时器
    if (this._clickEffectRestoreTimer) {
        clearTimeout(this._clickEffectRestoreTimer);
        this._clickEffectRestoreTimer = null;
    }

    // 教程模式：直接随机播放表情
    if (window.isInTutorial) {
        console.log('[Interaction] 教程模式 - 随机播放表情（低优先级，将自动恢复）');
        try {
            // 获取表情列表
            let expressionNames = [];
            if (this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
                expressionNames = this.fileReferences.Expressions.map(e => e.Name).filter(Boolean);
            }

            // 随机播放表情
            if (expressionNames.length > 0) {
                const randomExpression = expressionNames[Math.floor(Math.random() * expressionNames.length)];
                console.log(`[Interaction] 教程模式 - 播放表情: ${randomExpression}（将在 ${window.live2dManager.CLICK_EFFECT_DURATION}ms 后恢复）`);
                await this.currentModel.expression(randomExpression);

                const playedMotion = await this.playTutorialMotion();

                if (!playedMotion) {
                    // 动作不可用时，回退到参数动画模拟效果
                    const model = this.currentModel.internalModel;
                    if (model && model.coreModel) {
                        // 随机晃动头部
                        const angleXIndex = model.coreModel.getParameterIndex('ParamAngleX');
                        const angleYIndex = model.coreModel.getParameterIndex('ParamAngleY');
                        const bodyAngleXIndex = model.coreModel.getParameterIndex('ParamBodyAngleX');

                        const duration = 1000 + Math.random() * 1000; // 1-2秒
                        const startTime = Date.now();

                        const setParamByIndex = (index, value) => {
                            if (index < 0) return;
                            if (typeof model.coreModel.setParameterValueByIndex === 'function') {
                                model.coreModel.setParameterValueByIndex(index, value);
                            } else {
                                model.coreModel.setParameterValueById(index, value);
                            }
                        };

                        const animate = () => {
                            const elapsed = Date.now() - startTime;
                            const progress = Math.min(elapsed / duration, 1);
                            const t = progress * Math.PI * 2; // 一个完整周期

                            setParamByIndex(angleXIndex, Math.sin(t) * 15); // -15 到 15 度
                            setParamByIndex(angleYIndex, Math.cos(t) * 10); // -10 到 10 度
                            setParamByIndex(bodyAngleXIndex, Math.sin(t * 0.5) * 5); // 更慢的身体晃动

                            if (progress < 1) {
                                requestAnimationFrame(animate);
                            } else {
                                // 动画结束，恢复默认值
                                setParamByIndex(angleXIndex, 0);
                                setParamByIndex(angleYIndex, 0);
                                setParamByIndex(bodyAngleXIndex, 0);
                            }
                        };

                        animate();
                        console.log('[Interaction] 教程模式 - 播放参数动画');
                    }
                }
            }
        } catch (error) {
            console.warn('[Interaction] 教程模式播放表情失败:', error);
        }
    } else {
        // 正常模式：使用情感系统
        // 获取可用的情感列表
        let availableEmotions = [];

        // 从 emotionMapping 中获取可用情感
        if (this.emotionMapping && this.emotionMapping.expressions) {
            availableEmotions = Object.keys(this.emotionMapping.expressions).filter(e => e !== '常驻');
        }

        // 如果没有配置情感，使用 _playTemporaryClickEffect 内部的兜底逻辑
        // 传一个占位 emotion，兜底会从 fileReferences 中随机选取
        if (availableEmotions.length === 0) {
            availableEmotions = ['_random_fallback'];
        }

        // 随机选择一个情感
        const randomEmotion = availableEmotions[Math.floor(Math.random() * availableEmotions.length)];
        console.log(`[Interaction] 点击触发随机情感: ${randomEmotion}（低优先级，将自动恢复）`);

        // 触发临时情感效果
        try {
            // 播放低优先级的表情和动作
            const result = await this._playTemporaryClickEffect(randomEmotion, 2, window.live2dManager.CLICK_EFFECT_DURATION);
        } catch (error) {
            console.warn('[Interaction] 触发情感失败:', error);
        }
    }

    // 设置恢复定时器：在效果持续时间后清除表情，恢复到常驻/默认状态
    // 使用唯一 ID 标记此次点击效果，用于判断是否应该恢复
    const clickEffectId = Date.now();
    this._currentClickEffectId = clickEffectId;
    
    this._clickEffectRestoreTimer = setTimeout(() => {
        this._clickEffectRestoreTimer = null;
        
        // 检查是否仍然是此次点击效果（没有被新的情感/点击覆盖）
        if (this._currentClickEffectId !== clickEffectId) {
            console.log('[Interaction] 点击效果已被新的情感覆盖，跳过恢复');
            return;
        }
        
        console.log('[Interaction] 点击效果持续时间结束，平滑恢复到默认状态');
        this._currentClickEffectId = null;
        // 使用平滑过渡恢复到常驻表情或默认状态（smoothReset 内部会在快照后停止 motion/expression）
        if (typeof this.smoothResetToInitialState === 'function') {
            this.smoothResetToInitialState().catch(e => {
                console.warn('[Interaction] 平滑恢复失败，回退到即时恢复:', e);
                if (typeof this.clearExpression === 'function') this.clearExpression();
            });
        } else if (typeof this.clearExpression === 'function') {
            this.clearExpression();
        }
    }, window.live2dManager.CLICK_EFFECT_DURATION);
};

/**
 * 设置 触摸/点击 交互
 * 使用 pixi-live2d-display 的 'hit' 事件来检测 HitArea 点击
 * @param {PIXI.DisplayObject} model - Live2D 模型对象
 */
Live2DManager.prototype.setupHitAreaInteraction = function(model) {
    if (!model) {
        console.error('[HitArea] 模型不存在，无法设置 HitArea 交互');
        return;
    }

    // 监听模型的 hit 事件
    function dd(hitAreas) {
        // 只在非教程模式下处理 hit 事件
        // 教程模式下，通过 setupDragAndDrop 的点击检测处理
        if (window.isInTutorial) {
            return;
        }

        window.live2dManager.touchSetHitEventLock = true

        // 滤波 毫秒
        window.live2dManager.touchSetFilter = window.live2dManager.touchSetFilter || {};
        if(!window.live2dManager.touchSetFilter[hitAreas]){
            window.live2dManager.touchSetFilter[hitAreas]= Date.now();
        }else{
            let timenow = Date.now();
            if(timenow - window.live2dManager.touchSetFilter[hitAreas] > 500){
                window.live2dManager.touchSetFilter[hitAreas]= timenow;
            }else{
                // 似乎按下和松开都算一次触发?
                // console.error(timenow - window.live2dManager.touchSetFilter[hitAreas])
                return;
            }
        }
        console.log('[HitArea] 命中的区域:', hitAreas);
        const modelName = window.live2dManager.modelName;
        const touchSet = window.live2dManager.touchSet && window.live2dManager.touchSet[modelName];

        // touchSet 未设置或当前模型无配置 → 直接随机播放
        if (!touchSet) {
            console.log('[HitArea] touchSet 未配置，播放随机动画');
            window.live2dManager.triggerRandomEmotion();
            return;
        }

        const UseBlock = touchSet[hitAreas[0]] ? hitAreas[0] : "default";
        let d = touchSet[UseBlock];

        if (UseBlock == "default") {
            // 命中的是 default 区域
            if (d && (d.motions.length > 0 || d.expressions.length > 0)) {
                // default 有配置，播放 default 动画
                console.log('[HitArea] 使用 default 配置');
                window.live2dManager._playTouchSetAnimation("default");
            } else {
                // default 也没有配置，播放随机动画
                console.log('[HitArea] default 无配置，播放随机动画');
                window.live2dManager.triggerRandomEmotion();
            }
            return;

        } else if (!d || (d.expressions.length == 0 && d.motions.length == 0)) {
            // HitArea区点击 该区域无配置动画
            if (touchSet["default"] && (touchSet["default"].motions.length > 0 || touchSet["default"].expressions.length > 0)) {
                // 使用default配置
                console.log('[HitArea] 区域未绑定touchSet，使用default配置');
                window.live2dManager._playTouchSetAnimation("default");
                return;
            } else {
                // default 也没有配置，播放随机动画
                console.log('[HitArea] 区域未绑定 touchSet，播放随机动画');
                window.live2dManager.triggerRandomEmotion();
                return;
            }
        }

        // 遍历所有命中的 HitArea，播放对应的动画
        hitAreas.forEach(hitAreaId => {
            window.live2dManager._playTouchSetAnimation(hitAreaId);
        });
    }

    model.on('hit',(hitAreas)=>{dd(hitAreas)});
    
    console.log(`[HitArea] HitArea 交互已设置 : ${window.live2dManager.modelName}`);
};

/**
 * 根据 touchSet 配置播放 HitArea 对应的动画
 * @param {string} hitAreaId - HitArea ID
 */
Live2DManager.prototype._playTouchSetAnimation = async function(hitAreaId) {

    // ↓只是debug用
    // const live2d的touch = window.live2dManager.touchSet


    if ( hitAreaId ==null || !this.currentModel) {
        return;
    }
    let faceHoldingTime = window.live2dManager.CLICK_EFFECT_DURATION;
    let AnimHoldingTime = null;
    // 获取当前模型的 touchSet 配置

    const modelName = this.modelName;
    const touchSet = this.touchSet && this.touchSet[modelName];
    
    if (!touchSet || !touchSet[hitAreaId]) {
        console.log(`[TouchSet] 没有找到 ${hitAreaId} 的配置`);
        return;
    }

    const config = touchSet[hitAreaId];
    const { motions = [], expressions = [] } = config;

    console.log(`[TouchSet] 播放 ${hitAreaId} 的动画:`, { motions, expressions });

    try {
        // 播放动作
        if (motions.length > 0) {
            const randomMotion = motions[Math.floor(Math.random() * motions.length)];
            
            // 优先使用 motionManager.definitions，回退到 fileReferences.Motions
            const motionDefs = this.currentModel.internalModel?.motionManager?.definitions;
            const fileRefs = this.fileReferences?.Motions;
            
            const motionSources = [
                motionDefs,
                fileRefs
            ].filter(Boolean);
            
            for (const motionSource of motionSources) {
                for (const [groupName, motionList] of Object.entries(motionSource)) {
                    if (Array.isArray(motionList)) {
                        const motion = motionList.find(m => {
                            if (!m || !m.File) return false;
                            const fileName = m.File.split("motions/")[1]?.replace(".motion3","").replace(".json","");
                            return fileName === randomMotion;
                        });
                        if (motion) {
                            const index = motionList.indexOf(motion);
                            console.log(`[TouchSet] 准备播放动作: ${groupName}[${index}], 文件: ${motion.File}`);
                            
                            // 获取motion的实际持续时间
                            try {
                                let motionPath = motion.File;
                                if (!motionPath.startsWith('http') && !motionPath.startsWith('/')) {
                                    motionPath = `${this.modelRootPath}/${motionPath}`;
                                }
                                const response = await fetch(motionPath);
                                if (response.ok) {
                                    const motionData = await response.json();
                                    if (motionData.Meta && motionData.Meta.Duration) {
                                        AnimHoldingTime = motionData.Meta.Duration * 1000;
                                        faceHoldingTime = AnimHoldingTime;
                                        console.log(`[TouchSet] 动作持续时间: ${AnimHoldingTime}ms, 表情持续时间将同步`);
                                    }
                                }
                            } catch (error) {
                                console.warn(`[TouchSet] 无法获取motion持续时间:`, error);
                            }
                            
                            try {
                                const result = await this.currentModel.motion(groupName, index, 2);
                                if (result) {
                                    console.log(`[TouchSet] 成功播放动作: ${groupName}[${index}]`);
                                } else {
                                    console.warn(`[TouchSet] 动作播放返回空值: ${groupName}[${index}]`);
                                }
                            } catch (motionError) {
                                console.warn(`[TouchSet] 动作播放异常: ${groupName}[${index}]`, motionError);
                            }
                            break;
                        }
                    }
                }
            }
        }

        // 播放表情
        if (expressions.length > 0) {
            const randomExpressionName = expressions[Math.floor(Math.random() * expressions.length)];
            const faceInfo = this.fileReferences?.Expressions?.find(e => e.Name === randomExpressionName);
            if (!faceInfo || !faceInfo.File) {
                console.warn(`[TouchSet] 表情文件不存在: ${randomExpressionName}`);
                
            }else {
                console.log(`[TouchSet] 尝试播放表情: ${faceInfo.File}`);
                try {
                    await this.playExpression(randomExpressionName, faceInfo.File);
                    console.log(`[TouchSet] 播放表情成功: ${randomExpressionName}, 持续时间: ${faceHoldingTime}ms`);
                    
                    clearTimeout(this.expressionTimer);
                    this.expressionTimer = setTimeout(() => {
                        this.clearExpression?.();
                    }, faceHoldingTime);
                } catch (e) {
                    console.warn(`[TouchSet] 播放表情失败: ${randomExpressionName}`, e);
                }
            }

        }
    } catch (error) {
        console.warn(`[TouchSet] 播放动画失败:`, error);
    }
};
