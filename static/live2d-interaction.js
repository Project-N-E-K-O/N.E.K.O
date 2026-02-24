/**
 * Live2D Interaction - 拖拽、缩放、鼠标跟踪等交互功能
 */

// ===== 自动吸附功能配置 =====
const SNAP_CONFIG = {
    // 吸附阈值：模型在屏幕内剩余的像素小于此值时触发吸附（即模型绝大部分超出屏幕）
    threshold: 50,
    // 吸附边距：吸附后距离屏幕边缘的最小距离
    margin: 5,
    // 动画持续时间（毫秒）
    animationDuration: 260,
    // 动画缓动函数类型
    easingType: 'easeOutBack'
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

    const { afterDisplaySwitch = false } = options;

    try {
        const bounds = model.getBounds();
        const modelLeft = bounds.left;
        const modelRight = bounds.right;
        const modelTop = bounds.top;
        const modelBottom = bounds.bottom;
        const modelWidth = bounds.width;
        const modelHeight = bounds.height;

        // 获取当前屏幕边界
        let screenLeft = 0;
        let screenTop = 0;
        let screenRight = window.innerWidth;
        let screenBottom = window.innerHeight;

        // 在 Electron 环境下，尝试获取更精确的屏幕信息
        if (window.electronScreen && window.electronScreen.getCurrentDisplay) {
            try {
                const currentDisplay = await window.electronScreen.getCurrentDisplay();
                if (currentDisplay && currentDisplay.workArea) {
                    // workArea 是排除任务栏后的可用区域
                    screenRight = currentDisplay.workArea.width || window.innerWidth;
                    screenBottom = currentDisplay.workArea.height || window.innerHeight;
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
        const threshold = SNAP_CONFIG.threshold;
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
    // 如果正在执行吸附动画，跳过
    if (this._isSnapping) {
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

    let isDragging = false;
    let dragStartPos = new PIXI.Point();

    // 点击检测相关变量
    let clickStartTime = 0;
    let clickStartX = 0;
    let clickStartY = 0;
    let hasMoved = false;
    const CLICK_THRESHOLD_DISTANCE = 10; // 移动距离阈值（像素）
    const CLICK_THRESHOLD_TIME = 300; // 时间阈值（毫秒）

    // 使用 live2d-ui-drag.js 中的共享工具函数（按钮 pointer-events 管理）
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

    const playTutorialMotion = async () => {
        if (!this.currentModel || !this.currentModel.motion) {
            return false;
        }

        const fileRefMotions = this.fileReferences && this.fileReferences.Motions;
        let motionGroups = [];

        if (fileRefMotions && typeof fileRefMotions === 'object') {
            motionGroups = Object.keys(fileRefMotions)
                .filter(group => Array.isArray(fileRefMotions[group]) && fileRefMotions[group].length > 0);
        }

        if (motionGroups.length === 0 &&
            this.currentModel.internalModel &&
            this.currentModel.internalModel.motionManager &&
            this.currentModel.internalModel.motionManager.definitions) {
            const defs = this.currentModel.internalModel.motionManager.definitions;
            motionGroups = Object.keys(defs)
                .filter(group => Array.isArray(defs[group]) && defs[group].length > 0);
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
            // 使用低优先级 IDLE=1 播放动作，这样不会覆盖对话等高优先级动作
            // pixi-live2d-display 的 motion(group, index, priority) 支持优先级参数
            const motion = await this.currentModel.motion(group, index, CLICK_MOTION_PRIORITY);
            if (motion) {
                console.log(`[Interaction] 教程模式 - 播放动作: ${group}[${index}]（优先级: ${CLICK_MOTION_PRIORITY}）`);
                return true;
            }
        } catch (error) {
            console.warn('[Interaction] 教程模式 - 动作播放失败:', error);
        }

        return false;
    };

    // 点击触发随机表情和动作（低优先级，会自动恢复）
    // 使用最低优先级 IDLE=1，确保不会覆盖对话等高优先级动作
    const CLICK_MOTION_PRIORITY = 1; // IDLE priority
    const CLICK_EFFECT_DURATION = 3000; // 点击效果持续时间（毫秒）

    const triggerRandomEmotion = async () => {
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
                    console.log(`[Interaction] 教程模式 - 播放表情: ${randomExpression}（将在 ${CLICK_EFFECT_DURATION}ms 后恢复）`);
                    await this.currentModel.expression(randomExpression);

                    const playedMotion = await playTutorialMotion();

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
                        
                        console.log('[Interaction] 点击效果持续时间结束，恢复到默认状态');
                        this._currentClickEffectId = null;
                        // 清除表情，恢复到常驻表情或默认状态
                        if (this.clearExpression) {
                            this.clearExpression();
                        }
                    }, CLICK_EFFECT_DURATION);
                }
            } catch (error) {
                console.warn('[Interaction] 教程模式播放表情失败:', error);
            }
            return;
        }

        // 正常模式：使用情感系统（但使用临时触发，会自动恢复）
        if (!this.emotionMapping) {
            console.log('[Interaction] 没有情感映射配置，跳过点击触发');
            return;
        }

        // 获取可用的情感列表
        let availableEmotions = [];

        // 从 emotionMapping 中获取可用情感
        if (this.emotionMapping.expressions) {
            availableEmotions = Object.keys(this.emotionMapping.expressions).filter(e => e !== '常驻');
        }

        // 如果没有配置情感，使用默认列表
        if (availableEmotions.length === 0) {
            availableEmotions = ['happy', 'sad', 'angry', 'neutral'];
        }

        // 随机选择一个情感
        const randomEmotion = availableEmotions[Math.floor(Math.random() * availableEmotions.length)];
        console.log(`[Interaction] 点击触发随机情感: ${randomEmotion}（低优先级，将自动恢复）`);

        // 触发临时情感效果
        try {
            // 播放低优先级的表情和动作
            await this._playTemporaryClickEffect(randomEmotion, CLICK_MOTION_PRIORITY, CLICK_EFFECT_DURATION);
        } catch (error) {
            console.warn('[Interaction] 触发情感失败:', error);
        }
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
        if (isDragging) {
            isDragging = false;
            document.getElementById('live2d-canvas').style.cursor = '';

            // 拖拽结束后恢复按钮的 pointer-events
            restoreButtonPointerEvents();

            // 检测是否为点击（非拖拽）
            const clickDuration = Date.now() - clickStartTime;
            if (!hasMoved && clickDuration < CLICK_THRESHOLD_TIME) {
                // 这是一个点击，触发随机表情和动作
                console.log(`[Interaction] 检测到点击（时长: ${clickDuration}ms）`);
                await triggerRandomEmotion();
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
        if (isDragging) {
            // 再次检查是否变成多点触摸
            if (event.touches && event.touches.length > 1) {
                // 如果变成多点触摸，停止拖拽
                isDragging = false;
                document.getElementById('live2d-canvas').style.cursor = '';
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
            if (this._isMouseOverButtons || isPointerNearLock()) {
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
    let lockedHoverFadeActive = false;
    const setLockedHoverFade = (shouldFade) => {
        if (!live2dContainer) return;
        if (lockedHoverFadeActive === shouldFade) return;
        lockedHoverFadeActive = shouldFade;
        live2dContainer.classList.toggle('locked-hover-fade', shouldFade);
    };

    // 跟踪 Ctrl 键状态（作为备用，主要从事件中直接读取）
    let isCtrlPressed = false;

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
            // Ctrl/Cmd 键释放时，如果正在变淡，立即取消变淡效果
            if (lockedHoverFadeActive) {
                setLockedHoverFade(false);
            }
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
            setLockedHoverFade(false);
            return;
        }

        // 检查模型是否已被销毁或不在舞台上
        if (model.destroyed || !model.parent || !this.pixi_app || !this.pixi_app.stage) {
            setLockedHoverFade(false);
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
            setLockedHoverFade(false);
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
                const ellipseRadiusX = width * 0.35;
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

            // 额外检查：鼠标必须在模型可见区域附近
            const isPointerNearVisibleModel = pointer.x >= bounds.left - threshold && pointer.x <= bounds.right + threshold &&
                                              pointer.y >= Math.max(bounds.top, 0) - threshold && pointer.y <= Math.min(bounds.bottom, window.innerHeight) + threshold;
            
            // 如果鼠标不在屏幕内或不在模型可见区域附近，视为远离模型
            if (!isPointerNearVisibleModel) {
                this.isFocusing = false;
                startHideTimer();
                setLockedHoverFade(false);
                return;
            }
            // 只有在锁定、按住 Ctrl 键且鼠标在模型附近时才变淡
            const shouldFade = this.isLocked && ctrlKeyPressed && distance < HoverFadethreshold;
            setLockedHoverFade(shouldFade);

            const canvasEl = document.getElementById('live2d-canvas');
            if (distance < threshold) {
                showButtons();
                if (canvasEl && !this.isLocked && !(model.interactive && model.dragging)) {
                    canvasEl.style.cursor = 'grab';
                }
                // 只有当鼠标在模型附近时才调用 focus，避免 Electron 透明窗口中的全局跟踪问题
                if (this.isFocusing) {
                    model.focus(pointer.x, pointer.y);
                }
            } else {
                // 鼠标离开模型区域，启动隐藏定时器
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

    // 窗口失去焦点时重置 Ctrl 键状态和变淡效果
    const onBlur = () => {
        isCtrlPressed = false;
        if (lockedHoverFadeActive) {
            setLockedHoverFade(false);
        }
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
        
        // 兼容旧结构
        if (expressionFiles.length === 0 && this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
            const candidates = this.fileReferences.Expressions.filter(e => (e.Name || '').startsWith(emotion));
            expressionFiles = candidates.map(e => e.File).filter(Boolean);
        }

        if (expressionFiles.length > 0) {
            const choiceFile = this.getRandomElement(expressionFiles);
            if (choiceFile) {
                // 在 FileReferences 中查找匹配的表情名称
                let expressionName = null;
                if (this.fileReferences && this.fileReferences.Expressions) {
                    for (const expr of this.fileReferences.Expressions) {
                        if (expr.File === choiceFile) {
                            expressionName = expr.Name;
                            break;
                        }
                    }
                }
                
                if (!expressionName) {
                    const base = String(choiceFile).split('/').pop() || '';
                    expressionName = base.replace('.exp3.json', '');
                }

                console.log(`[ClickEffect] 播放临时表情: ${expressionName}`);
                await this.currentModel.expression(expressionName);
            }
        }

        // 2. 播放低优先级动作
        let motions = null;
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

        if (motions && motions.length > 0) {
            // 使用低优先级播放动作
            // pixi-live2d-display 的 motion(group, index, priority) 支持优先级参数
            try {
                const motion = await this.currentModel.motion(emotion, undefined, priority);
                if (motion) {
                    console.log(`[ClickEffect] 播放临时动作: ${emotion}（优先级: ${priority}）`);
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
            
            console.log('[ClickEffect] 临时效果结束，恢复到默认状态');
            this._currentClickEffectId = null;
            
            const motionToStop = this._clickEffectMotion;
            this._clickEffectMotion = null;
            if (motionToStop && typeof motionToStop.stop === 'function') {
                try { motionToStop.stop(); } catch (e) {}
            }
            
            try {
                const expressionManager = this.currentModel &&
                    this.currentModel.internalModel &&
                    this.currentModel.internalModel.motionManager &&
                    this.currentModel.internalModel.motionManager.expressionManager;
                if (expressionManager && typeof expressionManager.stopAllExpressions === 'function') {
                    expressionManager.stopAllExpressions();
                }
            } catch (e) {
                console.warn('[ClickEffect] Failed to stop expressions on currentModel:', e);
            }

            if (typeof this.applyPersistentExpressionsNative === 'function') {
                try { this.applyPersistentExpressionsNative(true); } catch (e) {}
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

    // 获取当前屏幕尺寸（用于跨分辨率位置和缩放归一化）
    // 使用 screen.width/height 而非 renderer 尺寸，避免临时视口变化（F12、输入法等）污染保存数据
    let viewportInfo = null;
    const screenW = window.screen.width;
    const screenH = window.screen.height;
    if (Number.isFinite(screenW) && Number.isFinite(screenH) && screenW > 0 && screenH > 0) {
        viewportInfo = { width: screenW, height: screenH };
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

                // 屏幕切换后，延迟一帧再检测是否需要吸附
                // 这是因为窗口大小可能还未更新完成
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
        }
        return false;  // No display switch occurred
    } catch (error) {
        console.error('[Live2D] 检测/切换屏幕时出错:', error);
        return false;
    }
};

/**
 * 设置窗口大小改变时的自动吸附检测
 * 当窗口/屏幕大小改变时，检测模型是否超出边界并执行吸附
 * 注意：不在 resize 时调整模型 scale/position，避免临时视口变化
 *（F12 DevTools、移动端输入法弹出等）导致模型不可逆地缩小
 */
Live2DManager.prototype.setupResizeSnapDetection = function () {
    // 防止重复绑定
    if (this._resizeSnapHandler) {
        window.removeEventListener('resize', this._resizeSnapHandler);
    }

    // 防抖动的 resize 处理函数
    let resizeTimeout = null;

    this._resizeSnapHandler = () => {
        // 如果正在拖拽或吸附，跳过
        if (this._isSnapping) return;

        // 清除之前的定时器
        if (resizeTimeout) {
            clearTimeout(resizeTimeout);
        }

        // 延迟执行，避免频繁触发
        resizeTimeout = setTimeout(async () => {
            if (!this.currentModel || !this.pixi_app) return;

            // 仅执行吸附检测（防止模型超出屏幕边界）
            await this._checkAndPerformSnap(this.currentModel);
        }, 300);
    };

    window.addEventListener('resize', this._resizeSnapHandler);

    console.debug('[Live2D] 已启用窗口大小改变时的自动吸附检测');
};

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

    // 清理 resize 监听器
    if (this._resizeSnapHandler) {
        window.removeEventListener('resize', this._resizeSnapHandler);
        this._resizeSnapHandler = null;
    }

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
