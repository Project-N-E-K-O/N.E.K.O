/**
 * Live2D Model - 模型加载、口型同步相关功能
 * 依赖: live2d-core.js (提供 Live2DManager 类和 window.LIPSYNC_PARAMS)
 */

// 加载模型
Live2DManager.prototype.loadModel = async function(modelPath, options = {}) {
    if (!this.pixi_app) {
        throw new Error('PIXI 应用未初始化，请先调用 initPIXI()');
    }

    // 检查是否正在加载模型，防止并发加载导致重复模型叠加；如果已有加载操作正在进行，拒绝新的加载请求并明确返回错误
    if (this._isLoadingModel) {
        console.warn('模型正在加载中，跳过重复加载请求:', modelPath);
        return Promise.reject(new Error('Model is already loading. Please wait for the current operation to complete.'));
    }
    
    // 设置加载锁
    this._isLoadingModel = true;
    const loadToken = ++this._activeLoadToken;
    this._modelLoadState = 'preparing';
    this._isModelReadyForInteraction = false;

    // 清除上一次加载遗留的画布揭示定时器
    if (this._canvasRevealTimer) {
        clearTimeout(this._canvasRevealTimer);
        this._canvasRevealTimer = null;
    }

    try {
        // 移除当前模型
        if (this.currentModel) {
            // 关闭所有已打开的设置窗口（防御性检查）；可通过 options.skipCloseWindows 跳过此操作（例如从设置窗口返回时重新加载模型）
            if (window.closeAllSettingsWindows && !options.skipCloseWindows) {
                window.closeAllSettingsWindows();
            }
            // 清除保存参数的定时器
            if (this._savedParamsTimer) {
                clearInterval(this._savedParamsTimer);
                this._savedParamsTimer = null;
            }
            
            // 清除延迟重新安装覆盖的定时器
            if (this._reinstallTimer) {
                clearTimeout(this._reinstallTimer);
                this._reinstallTimer = null;
                this._reinstallScheduled = false;
            }
            // 重置重装计数（切换模型时）
            this._reinstallAttempts = 0;
            // 先清空常驻表情记录和初始参数
            this.teardownPersistentExpressions();
            this.initialParameters = {};

            // 还原 coreModel.update 覆盖
            try {
                const coreModel = this.currentModel.internalModel && this.currentModel.internalModel.coreModel;
                if (coreModel && this._mouthOverrideInstalled && typeof this._origCoreModelUpdate === 'function') {
                    coreModel.update = this._origCoreModelUpdate;
                }
            } catch (_) {}
            this._mouthOverrideInstalled = false;
            this._origCoreModelUpdate = null;
            this._coreModelRef = null;
            // 同时移除 mouthTicker（若曾启用过 ticker 模式）
            if (this._mouthTicker && this.pixi_app && this.pixi_app.ticker) {
                try { this.pixi_app.ticker.remove(this._mouthTicker); } catch (_) {}
                this._mouthTicker = null;
            }

            // 移除由 HTML 锁图标或交互注册的监听，避免访问已销毁的显示对象
            try {
                // 清理鼠标跟踪监听器
                if (this._mouseTrackingListener) {
                    window.removeEventListener('pointermove', this._mouseTrackingListener);
                    this._mouseTrackingListener = null;
                }
                
                // 先移除锁图标的 ticker 回调
                if (this._lockIconTicker && this.pixi_app && this.pixi_app.ticker) {
                    this.pixi_app.ticker.remove(this._lockIconTicker);
                }
                this._lockIconTicker = null;
                // 移除锁图标元素
                if (this._lockIconElement && this._lockIconElement.parentNode) {
                    this._lockIconElement.parentNode.removeChild(this._lockIconElement);
                }
                this._lockIconElement = null;
                
                // 清理浮动按钮系统
                if (this._floatingButtonsTicker && this.pixi_app && this.pixi_app.ticker) {
                    this.pixi_app.ticker.remove(this._floatingButtonsTicker);
                }
                this._floatingButtonsTicker = null;
                if (this._floatingButtonsContainer && this._floatingButtonsContainer.parentNode) {
                    this._floatingButtonsContainer.parentNode.removeChild(this._floatingButtonsContainer);
                }
                this._floatingButtonsContainer = null;
                this._floatingButtons = {};
                // 清理"请她回来"按钮容器
                if (this._returnButtonContainer && this._returnButtonContainer.parentNode) {
                    this._returnButtonContainer.parentNode.removeChild(this._returnButtonContainer);
                }
                this._returnButtonContainer = null;
                // 清理所有弹出框定时器
                Object.values(this._popupTimers).forEach(timer => clearTimeout(timer));
                this._popupTimers = {};
                
                // 暂停 ticker，期间做销毁，随后恢复
                this.pixi_app.ticker && this.pixi_app.ticker.stop();
            } catch (_) {}
            try {
                this.pixi_app.stage.removeAllListeners && this.pixi_app.stage.removeAllListeners();
            } catch (_) {}
            try {
                this.currentModel.removeAllListeners && this.currentModel.removeAllListeners();
            } catch (_) {}

            // 从舞台移除并销毁旧模型
            try { this.pixi_app.stage.removeChild(this.currentModel); } catch (_) {}
            try { this.currentModel.destroy({ children: true }); } catch (_) {}
            try { this.pixi_app.ticker && this.pixi_app.ticker.start(); } catch (_) {}
        }

        // 防御性清理：确保舞台上没有残留的 Live2D 模型
        // 这可以防止由于并发问题或其他原因导致的模型叠加
        try {
            const stage = this.pixi_app.stage;
            const childrenToRemove = [];
            for (let i = stage.children.length - 1; i >= 0; i--) {
                const child = stage.children[i];
                // 检查是否是 Live2D 模型（通过检查 internalModel 属性）
                if (child && child.internalModel) {
                    childrenToRemove.push(child);
                }
            }
            for (const child of childrenToRemove) {
                console.warn('发现舞台上残留的 Live2D 模型，正在清理...');
                try { stage.removeChild(child); } catch (_) {}
                try { child.destroy({ children: true }); } catch (_) {}
            }
        } catch (e) {
            console.warn('清理舞台残留模型时出错:', e);
        }

        const model = await Live2DModel.from(modelPath, { autoFocus: false });
        this.currentModel = model;

        // 使用统一的模型配置方法
        await this._configureLoadedModel(model, modelPath, options, loadToken);

        return model;
    } catch (error) {
        console.error('加载模型失败:', error);
        
        // 尝试回退到默认模型
        if (modelPath !== '/static/mao_pro/mao_pro.model3.json') {
            console.warn('模型加载失败，尝试回退到默认模型: mao_pro');
            try {
                const defaultModelPath = '/static/mao_pro/mao_pro.model3.json';
                const model = await Live2DModel.from(defaultModelPath, { autoFocus: false });
                this.currentModel = model;

                // 使用统一的模型配置方法
                await this._configureLoadedModel(model, defaultModelPath, options, loadToken);

                console.log('成功回退到默认模型: mao_pro');
                return model;
            } catch (fallbackError) {
                console.error('回退到默认模型也失败:', fallbackError);
                throw new Error(`原始模型加载失败: ${error.message}，且回退模型也失败: ${fallbackError.message}`);
            }
        } else {
            // 如果已经是默认模型，直接抛出错误
            throw error;
        }
    } finally {
        // 无论成功还是失败，都要释放加载锁
        this._isLoadingModel = false;
        if (this._activeLoadToken === loadToken && this._modelLoadState !== 'ready') {
            this._modelLoadState = 'idle';
            this._isModelReadyForInteraction = false;
        }
        // 安全网：如果加载失败导致画布仍处于 CSS 隐藏状态，强制恢复可见性
        try {
            if (this.pixi_app && this.pixi_app.view && this.pixi_app.view.style.opacity === '0') {
                this.pixi_app.view.style.transition = '';
                this.pixi_app.view.style.opacity = '';
            }
        } catch (_) {}
    }
};

Live2DManager.prototype._isLoadTokenActive = function(loadToken) {
    return this._activeLoadToken === loadToken;
};

Live2DManager.prototype._waitForModelVisualStability = function(model, loadToken, options = {}) {
    const requiredStableFrames = options.requiredStableFrames || 6;
    const maxFrames = options.maxFrames || 60;
    const minDimension = options.minDimension || 2;
    const deltaThreshold = options.deltaThreshold || 2;
    const minElapsedMs = options.minElapsedMs || 350;

    return new Promise((resolve) => {
        let frameCount = 0;
        let stableFrames = 0;
        let prevW = null;
        let prevH = null;
        const startTs = performance.now();

        const tick = () => {
            if (!this._isLoadTokenActive(loadToken) || !model || model.destroyed || !model.parent) {
                resolve(false);
                return;
            }

            frameCount += 1;
            let width = 0;
            let height = 0;

            try {
                const bounds = model.getBounds();
                width = Number(bounds.width) || 0;
                height = Number(bounds.height) || 0;
            } catch (_) {
                width = 0;
                height = 0;
            }

            const hasValidSize = Number.isFinite(width) && Number.isFinite(height) && width > minDimension && height > minDimension;
            const sizeStable = hasValidSize &&
                prevW !== null &&
                prevH !== null &&
                Math.abs(width - prevW) <= deltaThreshold &&
                Math.abs(height - prevH) <= deltaThreshold;

            if (sizeStable) {
                stableFrames += 1;
            } else {
                stableFrames = 0;
            }

            prevW = width;
            prevH = height;

            const elapsed = performance.now() - startTs;
            const hasWaitedLongEnough = elapsed >= minElapsedMs;
            if ((hasValidSize && stableFrames >= requiredStableFrames && hasWaitedLongEnough) || frameCount >= maxFrames) {
                resolve(hasValidSize);
                return;
            }

            requestAnimationFrame(tick);
        };

        requestAnimationFrame(tick);
    });
};

/**
 * 平滑淡入模型（替代瞬间 alpha=1 切换，避免首帧渲染变形）
 * 
 * 原理：即使经过稳定性检查，模型在首帧完全可见时仍可能存在
 * 微小的渲染抖动（裁剪蒙版纹理刷新、变形器输出延迟等）。
 * 通过 ~200ms 的 ease-out 淡入，前几帧 alpha 极低（肉眼不可见），
 * 为渲染流水线提供额外的缓冲帧，确保模型在视觉上可辨识时
 * 已经完全稳定。
 * 
 * @param {Object} model - Live2D 模型对象
 * @param {number} loadToken - 加载令牌（用于取消检查）
 * @param {number} duration - 淡入持续时间（毫秒），默认 200ms
 * @returns {Promise<boolean>} - 是否成功完成淡入
 */
Live2DManager.prototype._fadeInModel = function(model, loadToken, duration = 200) {
    return new Promise((resolve) => {
        if (!model || model.destroyed || !this._isLoadTokenActive(loadToken)) {
            resolve(false);
            return;
        }

        const startAlpha = model.alpha; // 通常为 0.001
        const startTime = performance.now();

        const animate = () => {
            if (!model || model.destroyed || !this._isLoadTokenActive(loadToken)) {
                resolve(false);
                return;
            }

            const elapsed = performance.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);
            // ease-out (cubic): 快速上升，尾部平缓 —— 模型快速出现，最后阶段柔和过渡
            const eased = 1 - Math.pow(1 - progress, 2.5);
            model.alpha = startAlpha + (1 - startAlpha) * eased;

            if (progress >= 1) {
                model.alpha = 1;
                resolve(true);
            } else {
                requestAnimationFrame(animate);
            }
        };

        requestAnimationFrame(animate);
    });
};

/**
 * 预跑物理模拟，让弹簧/钟摆系统在虚拟时间中提前收敛到平衡态。
 * 
 * Live2D 模型的物理系统（头发、衣物等）在首次加载时从默认状态开始，
 * 需要数百毫秒的模拟才能达到自然静止姿态。
 * _waitForModelVisualStability 只检查 getBounds() 包围盒尺寸，
 * 无法感知网格内部的物理变形（弹簧振荡、钟摆摆动）。
 * 
 * 本方法通过直接调用 internalModel.update() 多次小步进，
 * 在模型不可见期间（alpha=0.001）快速模拟物理时间，
 * 等到模型淡入时物理已完全收敛，不会出现任何变形。
 * 
 * 兼容 Cubism 2 和 Cubism 4（两者的 internalModel.update 签名相同）。
 * 
 * @param {Object} model - Live2DModel 对象（PIXI Container）
 * @param {number} simulatedMs - 要模拟的虚拟时间（毫秒），默认 2000
 * @param {number} stepMs - 每步时间（毫秒），默认 16（~60fps）
 */
Live2DManager.prototype._preTickPhysics = async function(model, simulatedMs, stepMs, loadToken) {
    if (!model || !model.internalModel) return;

    const internalModel = model.internalModel;

    // 只有存在物理系统时才需要预跑
    if (!internalModel.physics) {
        console.log('[Live2D] 模型无物理数据，跳过物理预跑');
        return;
    }

    // 默认参数
    if (typeof simulatedMs !== 'number' || simulatedMs <= 0) simulatedMs = 2000;
    if (typeof stepMs !== 'number' || stepMs <= 0) stepMs = 16;

    const totalSteps = Math.ceil(simulatedMs / stepMs);
    // 每批次运行的步数：在流畅性与延迟之间取平衡
    // 20步 × 16ms = ~0.3ms CPU 时间，足够轻量不会卡顿主线程
    const BATCH_SIZE = 20;
    console.log(`[Live2D] 开始物理预跑: ${simulatedMs}ms / ${stepMs}ms步长 = ${totalSteps}步，分批${BATCH_SIZE}步/帧`);

    let completed = 0;

    try {
        while (completed < totalSteps) {
            // 在每批次开始前检查 loadToken 是否仍有效
            if (loadToken != null && !this._isLoadTokenActive(loadToken)) {
                console.log('[Live2D] 物理预跑中止（loadToken 已过期）');
                return;
            }
            if (model.destroyed) {
                console.log('[Live2D] 物理预跑中止（模型已销毁）');
                return;
            }

            const batchEnd = Math.min(completed + BATCH_SIZE, totalSteps);
            for (let i = completed; i < batchEnd; i++) {
                internalModel.update(stepMs, model.elapsedTime);
                model.elapsedTime += stepMs;
            }
            completed = batchEnd;

            // 如果还有剩余步数，让出事件循环以避免主线程卡顿
            if (completed < totalSteps) {
                await new Promise(r => requestAnimationFrame(r));
            }
        }
    } catch (e) {
        console.warn('[Live2D] 物理预跑过程中出错:', e);
    }

    // 重置 deltaTime 累加器，确保下一次 _render() 的 internalModel.update
    // 使用正常的帧间增量，而非包含预跑时间的巨大值
    model.deltaTime = 0;

    console.log('[Live2D] 物理预跑完成');
};

// 不再需要预解析嘴巴参数ID，保留占位以兼容旧代码调用
Live2DManager.prototype.resolveMouthParameterId = function() { return null; };

// 配置已加载的模型（私有方法，用于消除主路径和回退路径的重复代码）
Live2DManager.prototype._configureLoadedModel = async function(model, modelPath, options, loadToken) {
    if (!this._isLoadTokenActive(loadToken)) return;
    this._modelLoadState = 'applying';

    // 解析模型目录名与根路径，供资源解析使用
    try {
        let urlString = null;
        if (typeof modelPath === 'string') {
            urlString = modelPath;
        } else if (modelPath && typeof modelPath === 'object' && typeof modelPath.url === 'string') {
            urlString = modelPath.url;
        }

        if (typeof urlString !== 'string') throw new TypeError('modelPath/url is not a string');

        // 记录用于保存偏好的原始模型路径（供 beforeunload 使用）
        try { this._lastLoadedModelPath = urlString; } catch (_) {}

        const cleanPath = urlString.split('#')[0].split('?')[0];
        const lastSlash = cleanPath.lastIndexOf('/');
        const rootDir = lastSlash >= 0 ? cleanPath.substring(0, lastSlash) : '/static';
        this.modelRootPath = rootDir; // e.g. /static/mao_pro or /static/some/deeper/dir
        const parts = rootDir.split('/').filter(Boolean);
        const rawName = parts.length > 0 ? parts[parts.length - 1] : null;
        try { this.modelName = rawName ? decodeURIComponent(rawName) : null; } catch (_) { this.modelName = rawName; }
        console.log('模型根路径解析:', { modelUrl: urlString, modelName: this.modelName, modelRootPath: this.modelRootPath });
    } catch (e) {
        console.warn('解析模型根路径失败，将使用默认值', e);
        this.modelRootPath = '/static';
        this.modelName = null;
    }

    // 配置渲染纹理数量以支持更多蒙版
    if (model.internalModel && model.internalModel.renderer && model.internalModel.renderer._clippingManager) {
        model.internalModel.renderer._clippingManager._renderTextureCount = 3;
        if (typeof model.internalModel.renderer._clippingManager.initialize === 'function') {
            model.internalModel.renderer._clippingManager.initialize(
                model.internalModel.coreModel,
                model.internalModel.coreModel.getDrawableCount(),
                model.internalModel.coreModel.getDrawableMasks(),
                model.internalModel.coreModel.getDrawableMaskCounts(),
                3
            );
        }
        console.log('渲染纹理数量已设置为3');
    }

    // 根据画质设置降低纹理分辨率
    this._applyTextureQuality(model);

    // 应用位置和缩放设置
    this.applyModelSettings(model, options);
    // 使用极小但非零的 alpha 值隐藏模型（而非 alpha=0）
    // 原因：PIXI 在 worldAlpha<=0 时会跳过 _render() 调用，
    // 导致 Live2D 裁剪蒙版纹理和变形器输出未被初始化，
    // 当 alpha 切换为 1 时首帧会出现变形。
    // alpha=0.001 在 8-bit 显示上不可见（0.001*255≈0.26 → 0），
    // 但能让 PIXI 正常执行渲染流水线，预热 GPU 资源。
    model.alpha = 0.001;

    // ★ CSS 合成器层级隐藏：在浏览器合成阶段（WebGL 之后）彻底隐藏画布
    // 这是多层防护中最外层也是最可靠的一层：无论 WebGL 内部渲染管线
    // 发生任何中间态（裁剪蒙版纹理填充、变形器首帧输出、物理振荡），
    // CSS opacity=0 都能绝对保证用户看不到任何渲染瑕疵。
    // 画布仍然正常渲染（不同于 display:none），GL 资源得以完整预热。
    if (this.pixi_app.view) {
        this.pixi_app.view.style.transition = 'none';
        this.pixi_app.view.style.opacity = '0';
    }
    
    // 注意：用户偏好参数的应用延迟到模型目录参数加载完成后，
    // 以确保正确的优先级顺序（模型目录参数 > 用户偏好参数）

    // 添加到舞台
    this.pixi_app.stage.addChild(model);

    // 设置交互性
    if (options.dragEnabled !== false) {
        this.setupDragAndDrop(model);
    }

    // 设置滚轮缩放
    if (options.wheelEnabled !== false) {
        this.setupWheelZoom(model);
    }
    
    // 设置触摸缩放（双指捏合）
    if (options.touchZoomEnabled !== false) {
        this.setupTouchZoom(model);
    }

    // 启用鼠标跟踪（始终启用监听器，内部根据设置决定是否执行眼睛跟踪）
    // enableMouseTracking 包含悬浮菜单显示/隐藏逻辑，必须始终启用
    this.enableMouseTracking(model);
    // 同步内部状态（眼睛跟踪是否启用）
    this._mouseTrackingEnabled = window.mouseTrackingEnabled !== false;
    console.log(`[Live2D] 鼠标跟踪初始化: window.mouseTrackingEnabled=${window.mouseTrackingEnabled}, _mouseTrackingEnabled=${this._mouseTrackingEnabled}`);

    // 设置浮动按钮系统（在模型完全就绪后再绑定ticker回调）
    this.setupFloatingButtons(model);
    
    // 设置原来的锁按钮
    this.setupHTMLLockIcon(model);

    // 加载 FileReferences 与 EmotionMapping
    if (options.loadEmotionMapping !== false) {
        const settings = model.internalModel && model.internalModel.settings && model.internalModel.settings.json;
        if (settings) {
            // 保存原始 FileReferences
            this.fileReferences = settings.FileReferences || null;

            // 从服务器 API 获取经过验证的表情/动作文件路径
            // model_manager 页面在加载前已手动注入；此处为 index 等其他页面补齐相同逻辑
            let verifiedExpressionBasenames = null;
            try {
                const rootParts = this.modelRootPath.split('/').filter(Boolean);
                let filesApiUrl = null;
                if (rootParts[0] === 'workshop' && rootParts.length >= 2 && /^\d+$/.test(rootParts[1])) {
                    filesApiUrl = `/api/live2d/model_files_by_id/${rootParts[1]}`;
                } else if (this.modelName) {
                    filesApiUrl = `/api/live2d/model_files/${encodeURIComponent(this.modelName)}`;
                }
                if (filesApiUrl) {
                    const filesResp = await fetch(filesApiUrl);
                    if (filesResp.ok) {
                        const filesData = await filesResp.json();
                        if (filesData.success !== false && Array.isArray(filesData.expression_files)) {
                            if (!this.fileReferences) this.fileReferences = {};
                            this.fileReferences.Expressions = filesData.expression_files.map(file => ({
                                Name: file.split('/').pop().replace('.exp3.json', ''),
                                File: file
                            }));
                            verifiedExpressionBasenames = new Set(
                                filesData.expression_files.map(f => f.split('/').pop().toLowerCase())
                            );
                            console.log('已从服务器更新表情文件引用:', this.fileReferences.Expressions.length, '个表情');
                        }
                        if (filesData.success !== false && Array.isArray(filesData.motion_files)) {
                            if (!this.fileReferences) this.fileReferences = {};
                            if (!this.fileReferences.Motions) this.fileReferences.Motions = {};
                            this.fileReferences.Motions.PreviewAll = filesData.motion_files.map(file => ({ File: file }));
                        }
                    }
                }
            } catch (e) {
                console.warn('获取服务器端表情文件列表失败，将使用模型配置中的路径:', e);
            }

            // 优先使用顶层 EmotionMapping，否则从 FileReferences 推导
            if (settings.EmotionMapping && (settings.EmotionMapping.expressions || settings.EmotionMapping.motions)) {
                this.emotionMapping = settings.EmotionMapping;
            } else {
                this.emotionMapping = this.deriveEmotionMappingFromFileRefs(this.fileReferences || {});
            }

            // 用服务器验证过的表情文件集过滤 emotionMapping，剔除磁盘上不存在的条目
            if (verifiedExpressionBasenames && this.emotionMapping && this.emotionMapping.expressions) {
                for (const emotion of Object.keys(this.emotionMapping.expressions)) {
                    const before = this.emotionMapping.expressions[emotion];
                    if (!Array.isArray(before)) continue;
                    this.emotionMapping.expressions[emotion] = before.filter(f => {
                        const base = String(f).split('/').pop().toLowerCase();
                        return verifiedExpressionBasenames.has(base);
                    });
                }
                console.log('已根据服务器验证结果过滤 emotionMapping');
            }
            console.log('已加载情绪映射:', this.emotionMapping);
        } else {
            console.warn('模型配置中未找到 settings.json，无法加载情绪映射');
        }
    }

    // 切换模型后清空失效 expression 缓存，避免污染其他模型
    if (typeof this.clearMissingExpressionFiles === 'function') {
        this.clearMissingExpressionFiles();
    }

    // 记录模型的初始参数（用于expression重置）
    // 必须在应用常驻表情之前记录，否则记录的是已应用常驻表情后的状态
    this.recordInitialParameters();

    // 设置常驻表情
    try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
    await this.setupPersistentExpressions();
    
    // 调用常驻表情应用完成的回调（事件驱动方式，替代不可靠的 setTimeout）
    if (options.onResidentExpressionApplied && typeof options.onResidentExpressionApplied === 'function') {
        try {
            options.onResidentExpressionApplied(model);
        } catch (callbackError) {
            console.warn('[Live2D Model] 常驻表情应用完成回调执行失败:', callbackError);
        }
    }
    
    // 加载并应用模型目录中的parameters.json文件（优先级最高）
    // 先加载参数，然后再安装口型覆盖（这样coreModel.update就能访问到savedModelParameters）
    if (this.modelName && model.internalModel && model.internalModel.coreModel) {
        try {
            const response = await fetch(`/api/live2d/load_model_parameters/${encodeURIComponent(this.modelName)}`);
            const data = await response.json();
            if (data.success && data.parameters && Object.keys(data.parameters).length > 0) {
                // 保存参数到实例变量，供定时器定期应用
                this.savedModelParameters = data.parameters;
                this._shouldApplySavedParams = true;
                
                // 立即应用一次
                this.applyModelParameters(model, data.parameters);
            } else {
                // 如果没有参数文件，清空保存的参数
                this.savedModelParameters = null;
                this._shouldApplySavedParams = false;
            }
        } catch (error) {
            console.error('加载模型参数失败:', error);
            this.savedModelParameters = null;
            this._shouldApplySavedParams = false;
        }
    } else {
        this.savedModelParameters = null;
        this._shouldApplySavedParams = false;
    }
    
    // 重新安装口型覆盖（这也包括了用户保存参数的应用逻辑）
    try {
        this.installMouthOverride();
    } catch (e) {
        console.error('安装口型覆盖失败:', e);
    }
    
    // 移除原本的 setInterval 定时器逻辑，改用 installMouthOverride 中的逐帧叠加逻辑
    if (this.savedModelParameters && this._shouldApplySavedParams) {
        // 清除之前的定时器（如果存在）
        if (this._savedParamsTimer) {
            clearInterval(this._savedParamsTimer);
            this._savedParamsTimer = null;
        }
        console.log('已启用参数叠加模式');
    }
    
    // 在模型目录参数加载完成后，应用用户偏好参数（如果有）
    // 此时所有异步操作（常驻表情、模型目录参数）都已完成，
    // 可以安全地应用用户偏好参数而不需要使用 setTimeout 延迟
    if (options.preferences && options.preferences.parameters && model.internalModel && model.internalModel.coreModel) {
        this.applyModelParameters(model, options.preferences.parameters);
        console.log('已应用用户偏好参数');
    }

    // 确保 PIXI ticker 正在运行（防止从VRM切换后卡住）
    // 无条件调用 start()，因为它是幂等的（如果已在运行则不会有影响）
    if (this.pixi_app && this.pixi_app.ticker) {
        this.pixi_app.ticker.start();
        console.log('[Live2D Model] Ticker 已确保启动');
    }

    // 检测是否有 Idle 情绪配置（兼容新旧两种格式）
    // - 新格式: EmotionMapping.motions['Idle'] / EmotionMapping.expressions['Idle']
    // - 旧格式: FileReferences.Motions['Idle'] / FileReferences.Expressions 中的 Idle 前缀
    const hasIdleInEmotionMapping = this.emotionMapping && 
        (this.emotionMapping.motions?.['Idle'] || this.emotionMapping.expressions?.['Idle']);
    const hasIdleInFileReferences = this.fileReferences && 
        (this.fileReferences.Motions?.['Idle'] || 
         (Array.isArray(this.fileReferences.Expressions) && 
          this.fileReferences.Expressions.some(e => (e.Name || '').startsWith('Idle'))));
    // 注意：Idle 情绪播放已移至模型淡入完成后触发，
    // 避免在加载过程中独立 setTimeout 可能导致的变形/抖动

    // ★ 预跑物理模拟：在模型仍不可见（alpha=0.001）时，
    // 通过虚拟时间步进让弹簧/钟摆系统收敛到平衡态。
    // 这是解决"加载变形"的核心手段——getBounds() 稳定性检查无法
    // 感知网格内部的物理变形，只有让物理实际跑完才能彻底消除。
    // 先检查 loadToken 是否仍然有效，避免对过期模型执行昂贵的物理预跑
    if (!this._isLoadTokenActive(loadToken) || !model || model.destroyed) {
        return;
    }
    await this._preTickPhysics(model, 2000, 16, loadToken);

    this._modelLoadState = 'settling';
    if (this._isLoadTokenActive(loadToken)) {
        await this._waitForModelVisualStability(model, loadToken);
    }
    if (!this._isLoadTokenActive(loadToken) || !model || model.destroyed) {
        return;
    }
    // 在隐藏状态下先做一次边界校正，避免“先出现再瞬移”
    if (typeof this._checkSnapRequired === 'function') {
        try {
            const snapInfo = await this._checkSnapRequired(model, { threshold: 300 });
            if (snapInfo && Number.isFinite(snapInfo.targetX) && Number.isFinite(snapInfo.targetY)) {
                model.x = snapInfo.targetX;
                model.y = snapInfo.targetY;
            }
        } catch (e) {
            console.warn('[Live2D Model] 初次加载边界校正失败:', e);
        }
    }
    // ★ CSS 合成器层级揭示（替代原 GL alpha 淡入）
    // 先在 GL 层面设为完全不透明（仍被 CSS opacity:0 隐藏），
    // 等渲染管线在 alpha=1 下输出若干完全稳定的帧后，
    // 再通过 CSS transition 平滑揭示画布——用户只会看到最终稳定态。
    model.alpha = 1;
    // 等待 3 帧：让渲染器在 alpha=1 下输出完全稳定的画面
    // （含裁剪蒙版纹理刷新、变形器最终输出、物理末帧收敛）
    await new Promise(r => requestAnimationFrame(() =>
        requestAnimationFrame(() => requestAnimationFrame(r))));
    if (!this._isLoadTokenActive(loadToken) || !model || model.destroyed) {
        return;
    }
    // CSS 平滑过渡揭示画布
    if (this.pixi_app && this.pixi_app.view) {
        const cv = this.pixi_app.view;
        cv.style.transition = 'opacity 0.28s ease-out';
        cv.style.opacity = '1';
        // 过渡完成后清除内联样式，避免干扰后续功能
        if (this._canvasRevealTimer) clearTimeout(this._canvasRevealTimer);
        this._canvasRevealTimer = setTimeout(() => {
            cv.style.transition = '';
            cv.style.opacity = '';
            this._canvasRevealTimer = null;
        }, 320);
    }
    this._isModelReadyForInteraction = true;
    this._modelLoadState = 'ready';

    // 模型完全可见后播放 Idle 情绪（替代原来的独立 setTimeout）
    if (hasIdleInEmotionMapping || hasIdleInFileReferences) {
        try {
            console.log('[Live2D Model] 模型淡入完成，开始播放Idle情绪');
            this.setEmotion('Idle').catch(error => {
                console.warn('[Live2D Model] 播放Idle情绪失败:', error);
            });
        } catch (error) {
            console.warn('[Live2D Model] 播放Idle情绪失败:', error);
        }
    }

    // 调用回调函数
    if (this.onModelLoaded) {
        this.onModelLoaded(model, modelPath);
    }
};



// 根据画质设置降低模型纹理分辨率
// 必须在模型首次渲染前调用，这样 PIXI 上传到 GPU 的就是降采样后的纹理
Live2DManager.prototype._applyTextureQuality = function (model) {
    const quality = window.renderQuality || 'medium';
    if (quality === 'high') return;

    const maxSize = quality === 'low' ? 1024 : 2048;

    try {
        // pixi-live2d-display 的纹理存储在 model.textures (PIXI.Texture[])
        // _render 时通过 model.textures[i].baseTexture 上传到 GPU
        const textures = model.textures;
        if (!textures || !textures.length) {
            console.warn('[Live2D] 未找到 model.textures，跳过纹理降采样');
            return;
        }

        let downscaledCount = 0;
        const renderer = this.pixi_app?.renderer;

        textures.forEach((tex, i) => {
            if (!tex || !tex.baseTexture) return;
            const bt = tex.baseTexture;
            const resource = bt.resource;
            const source = resource?.source;
            if (!source) return;

            const w = source.width || source.naturalWidth || 0;
            const h = source.height || source.naturalHeight || 0;
            if (w <= maxSize && h <= maxSize) return;

            const scale = maxSize / Math.max(w, h);
            const nw = Math.round(w * scale);
            const nh = Math.round(h * scale);

            const canvas = document.createElement('canvas');
            canvas.width = nw;
            canvas.height = nh;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(source, 0, 0, nw, nh);

            // 替换 BaseTexture 的 source，PIXI 下次上传到 GPU 时会用降采样后的 canvas
            resource.source = canvas;
            if (typeof resource.resize === 'function') {
                resource.resize(nw, nh);
            }
            bt.setRealSize(nw, nh);
            bt.update();

            // 如果 GL 纹理已经存在（理论上首次渲染前不会），手动重新上传
            if (renderer) {
                const contextUID = renderer.CONTEXT_UID;
                const glTex = bt._glTextures?.[contextUID];
                if (glTex && glTex.texture) {
                    const gl = renderer.gl;
                    gl.bindTexture(gl.TEXTURE_2D, glTex.texture);
                    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
                    glTex.width = nw;
                    glTex.height = nh;
                    glTex.dirtyId = bt.dirtyId;
                }
            }

            downscaledCount++;
            console.log(`[Live2D] 纹理 #${i} 从 ${w}x${h} 降采样到 ${nw}x${nh} (画质: ${quality})`);
        });

        if (downscaledCount > 0) {
            console.log(`[Live2D] 纹理降采样完成: ${downscaledCount} 张纹理已处理`);
        }
    } catch (e) {
        console.warn('[Live2D] 纹理画质调整失败:', e);
    }
};

// 延迟重新安装覆盖的默认超时时间（毫秒）
const REINSTALL_OVERRIDE_DELAY_MS = 100;
// 最大重装尝试次数
const MAX_REINSTALL_ATTEMPTS = 3;

Live2DManager.prototype._scheduleReinstallOverride = function() {
    if (this._reinstallScheduled) return;
    
    // 初始化重装计数（如果尚未初始化）
    if (typeof this._reinstallAttempts === 'undefined') {
        this._reinstallAttempts = 0;
    }
    if (typeof this._maxReinstallAttempts === 'undefined') {
        this._maxReinstallAttempts = MAX_REINSTALL_ATTEMPTS;
    }
    
    // 检查是否超过最大重装次数
    if (this._reinstallAttempts >= this._maxReinstallAttempts) {
        console.error('覆盖重装已达最大尝试次数，放弃重装');
        return;
    }
    
    this._reinstallScheduled = true;
    this._reinstallTimer = setTimeout(() => {
        this._reinstallScheduled = false;
        this._reinstallTimer = null;
        this._reinstallAttempts++;
        if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.coreModel) {
            try {
                this.installMouthOverride();
            } catch (reinstallError) {
                console.warn('延迟重新安装覆盖失败:', reinstallError);
            }
        }
    }, REINSTALL_OVERRIDE_DELAY_MS);
};

Live2DManager.prototype.installMouthOverride = function() {
    if (!this.currentModel || !this.currentModel.internalModel) {
        throw new Error('模型未就绪，无法安装口型覆盖');
    }

    const internalModel = this.currentModel.internalModel;
    const coreModel = internalModel.coreModel;
    const motionManager = internalModel.motionManager;
    
    if (!coreModel) {
        throw new Error('coreModel 不可用');
    }

    // 如果之前装过，先还原
    if (this._mouthOverrideInstalled) {
        if (typeof this._origMotionManagerUpdate === 'function' && motionManager) {
            try { motionManager.update = this._origMotionManagerUpdate; } catch (_) {}
        }
        if (typeof this._origCoreModelUpdate === 'function') {
            try { coreModel.update = this._origCoreModelUpdate; } catch (_) {}
        }
        this._origMotionManagerUpdate = null;
        this._origCoreModelUpdate = null;
    }

    // 口型参数列表（这些参数不会被常驻表情覆盖）- 使用文件顶部定义的 LIPSYNC_PARAMS 常量
    const lipSyncParams = window.LIPSYNC_PARAMS || ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
    const visibilityParams = ['ParamOpacity', 'ParamVisibility'];
    
    // 缓存参数索引，避免每帧查询
    const mouthParamIndices = {};
    for (const id of lipSyncParams) {
        try {
            const idx = coreModel.getParameterIndex(id);
            if (idx >= 0) mouthParamIndices[id] = idx;
        } catch (_) {}
    }
    console.log('[Live2D MouthOverride] 找到的口型参数:', Object.keys(mouthParamIndices).join(', ') || '无');
    
    // 覆盖 1: motionManager.update - 在动作更新后立即覆盖参数
    if (internalModel.motionManager && typeof internalModel.motionManager.update === 'function') {
        // 确保在绑定之前，motionManager 和 coreModel 都已准备好
        if (!internalModel.motionManager || !coreModel) {
            console.warn('motionManager 或 coreModel 未准备好，跳过 motionManager.update 覆盖');
        } else {
            const origMotionManagerUpdate = internalModel.motionManager.update.bind(internalModel.motionManager);
            this._origMotionManagerUpdate = origMotionManagerUpdate;
        
        internalModel.motionManager.update = (...args) => {
            // 检查 coreModel 是否仍然有效（在调用原始方法之前检查）
            if (!coreModel || !this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
                return; // 如果模型已销毁，直接返回
            }

            // 1. 捕获更新前的参数值（用于检测 Motion 是否修改了参数）
            const preUpdateParams = {};
            if (this.savedModelParameters && this._shouldApplySavedParams) {
                for (const paramId of Object.keys(this.savedModelParameters)) {
                    try {
                        const idx = coreModel.getParameterIndex(paramId);
                        if (idx >= 0) {
                            preUpdateParams[paramId] = coreModel.getParameterValueByIndex(idx);
                        }
                    } catch (_) {}
                }
            }
            
            // 先调用原始的 motionManager.update（添加错误处理）
            if (origMotionManagerUpdate) {
                try {
                    origMotionManagerUpdate(...args);
                } catch (e) {
                    // SDK 内部 motion 在异步加载期间可能会抛出 getParameterIndex 错误
                    // 这是 pixi-live2d-display 的已知问题，静默忽略即可
                    // 当 motion 加载完成后错误会自动消失
                    if (!coreModel || !this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
                        return;
                    }
                }
            }
            
            // 再次检查 coreModel 是否仍然有效（调用原始方法后）
            if (!coreModel || !this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
                return; // 如果模型已销毁，直接返回
            }
            
            // 然后在动作更新后立即覆盖参数
            try {
                // === 点击效果平滑过渡处理 ===
                // 当 _clickFadeState 存在时，说明点击效果正在平滑恢复中
                // 此时跳过 savedModelParameters 和 persistentExpression 的强制写入
                // 改为执行插值过渡
                const fadeState = this._clickFadeState;
                if (fadeState) {
                    const now = performance.now();
                    const elapsed = now - fadeState.startTime;
                    // 防御性校验：确保 duration 为有限正数，否则视为立即完成
                    const safeDuration = (Number.isFinite(fadeState.duration) && fadeState.duration > 0) ? fadeState.duration : 1;
                    const linearProgress = Math.min(Math.max(elapsed / safeDuration, 0), 1);
                    // cubic ease-out: 快进慢出
                    const t = 1 - Math.pow(1 - linearProgress, 3);

                    for (const [paramId, target] of Object.entries(fadeState.targetValues)) {
                        const start = fadeState.startValues[paramId];
                        if (start === undefined) continue;
                        try {
                            const interpolated = start + (target - start) * t;
                            coreModel.setParameterValueById(paramId, interpolated);
                        } catch (_) {}
                    }

                    // 口型参数不受过渡影响，照常写入
                    for (const [id, idx] of Object.entries(mouthParamIndices)) {
                        try {
                            coreModel.setParameterValueByIndex(idx, this.mouthValue);
                        } catch (_) {}
                    }

                    // 过渡完成：清除 fade 状态，恢复正常覆写逻辑
                    if (linearProgress >= 1) {
                        this._clickFadeState = null;
                        console.log('[ClickEffect] 平滑过渡完成');
                        // 确保常驻表情最终精确应用
                        if (typeof this.applyPersistentExpressionsNative === 'function') {
                            try { this.applyPersistentExpressionsNative(true); } catch (_) {}
                        }
                    }
                    // 跳过下方的正常覆写逻辑
                } else {
                // === 正常帧：应用保存参数 + 常驻表情 ===
                // 1. 应用保存的模型参数（智能叠加模式）
                if (this.savedModelParameters && this._shouldApplySavedParams) {
                    const persistentParamIds = this.getPersistentExpressionParamIds();
                    
                    for (const [paramId, value] of Object.entries(this.savedModelParameters)) {
                        // 跳过口型参数
                        if (lipSyncParams.includes(paramId)) continue;
                        // 跳过可见性参数
                        if (visibilityParams.includes(paramId)) continue;
                        // 跳过常驻表情已设置的参数
                        if (persistentParamIds.has(paramId)) continue;
                        
                        try {
                            const idx = coreModel.getParameterIndex(paramId);
                            if (idx >= 0 && typeof value === 'number' && Number.isFinite(value)) {
                                const currentVal = coreModel.getParameterValueByIndex(idx);
                                const preVal = preUpdateParams[paramId] !== undefined ? preUpdateParams[paramId] : currentVal;
                                const defaultVal = coreModel.getParameterDefaultValueByIndex(idx);
                                const offset = value - defaultVal;

                                // 策略：比较当前值(Motion更新后)与上一帧的值(preVal)
                                // 如果值变了(Math.abs > 0.001)，说明 Motion/Physics 正在控制它 -> 叠加 Offset
                                // 如果值没变，说明 Motion 没动它 -> 强制设为 UserValue (静态覆盖)
                                
                                if (Math.abs(currentVal - preVal) > 0.001) {
                                    // Motion 正在控制，使用叠加
                                    // 注意：这里 currentVal 已经是 Motion 的新值了
                                    coreModel.setParameterValueByIndex(idx, currentVal + offset);
                                } else {
                                    // Motion 没动它（或者静止），强制设为用户设定值
                                    // 这样可以防止无限叠加（因为没有叠加在上一帧的 Offset 上）
                                    // 同时也保证了静态参数也能生效
                                    coreModel.setParameterValueByIndex(idx, value);
                                }
                            }
                        } catch (_) {}
                    }
                }

                // 2. 写入口型参数（覆盖模式，优先级高）
                for (const [id, idx] of Object.entries(mouthParamIndices)) {
                    try {
                        coreModel.setParameterValueByIndex(idx, this.mouthValue);
                    } catch (_) {}
                }
                // 3. 写入常驻表情参数（覆盖模式，优先级最高）
                if (this.persistentExpressionParamsByName) {
                    for (const name in this.persistentExpressionParamsByName) {
                        const params = this.persistentExpressionParamsByName[name];
                        if (Array.isArray(params)) {
                            for (const p of params) {
                                if (lipSyncParams.includes(p.Id)) continue;
                                try {
                                    coreModel.setParameterValueById(p.Id, p.Value);
                                } catch (_) {}
                            }
                        }
                    }
                }
                } // 结束 else（正常帧覆写逻辑）
            } catch (_) {}
        };
        } // 结束 else 块（确保 motionManager 和 coreModel 都已准备好）
    }
    
    // 覆盖 coreModel.update - 在调用原始 update 之前写入参数
    // 先保存原始的 update 方法（使用更安全的方式保存引用）
    const origCoreModelUpdate = coreModel.update ? coreModel.update.bind(coreModel) : null;
    this._origCoreModelUpdate = origCoreModelUpdate;
    // 同时保存 coreModel 引用，用于验证
    this._coreModelRef = coreModel;
    
    // 覆盖 coreModel.update，确保在调用原始方法前写入参数
    coreModel.update = () => {
        // 首先检查覆盖是否仍然有效（防止在清理后仍然被调用）
        if (!this._mouthOverrideInstalled || !this._coreModelRef) {
            // 覆盖已被清理，但函数可能仍在运行，直接返回
            return;
        }
        
        // 验证 coreModel 是否仍然有效（防止模型切换后调用已销毁的 coreModel）
        if (!this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
            // coreModel 已无效，清理覆盖标志并返回
            this._mouthOverrideInstalled = false;
            this._origCoreModelUpdate = null;
            this._coreModelRef = null;
            return;
        }
        
        // 验证是否是同一个 coreModel（防止切换模型后调用错误的 coreModel）
        const currentCoreModel = this.currentModel.internalModel.coreModel;
        if (currentCoreModel !== this._coreModelRef) {
            // coreModel 已切换，清理覆盖标志并返回
            this._mouthOverrideInstalled = false;
            this._origCoreModelUpdate = null;
            this._coreModelRef = null;
            return;
        }
        
        try {
            // 这里的逻辑主要为了确保渲染前参数正确（防止 physics 等后续步骤重置了某些值）
            // 注意：如果 physics 运行在 motionManager.update 之后但在 coreModel.update 之前，
            // 那么这里的叠加可能已经被 physics 处理过或覆盖。
            // 通常 motion -> physics -> update.
            // 我们在 motionManager.update 里叠加，physics 应该能看到叠加后的值。
            
            // 1. 强制写入口型参数
            for (const [id, idx] of Object.entries(mouthParamIndices)) {
                try {
                    currentCoreModel.setParameterValueByIndex(idx, this.mouthValue);
                } catch (_) {}
            }
            
            // 2. 写入常驻表情参数（跳过口型参数以避免覆盖lipsync）
            // 当点击效果正在淡入淡出时，跳过常驻表情写入以避免覆盖插值
            if (this.persistentExpressionParamsByName && !this._clickFadeState) {
                for (const name in this.persistentExpressionParamsByName) {
                    const params = this.persistentExpressionParamsByName[name];
                    if (Array.isArray(params)) {
                        for (const p of params) {
                            if (lipSyncParams.includes(p.Id)) continue;
                            try {
                                currentCoreModel.setParameterValueById(p.Id, p.Value);
                            } catch (_) {}
                        }
                    }
                }
            }
        } catch (e) {
            console.error('口型覆盖参数写入失败:', e);
        }
        
        // 调用原始的 update 方法（重要：必须调用，否则模型无法渲染）
        // 检查是否是同一个 coreModel（防止切换模型后调用错误的 coreModel）
        if (currentCoreModel === coreModel && origCoreModelUpdate) {
            // 是同一个 coreModel，可以安全调用保存的原始方法
            try {
                // 在调用前再次验证 coreModel 是否仍然有效
                if (!currentCoreModel || typeof currentCoreModel.setParameterValueByIndex !== 'function') {
                    console.warn('coreModel 已无效，跳过 update 调用');
                    return;
                }
                origCoreModelUpdate();
            } catch (e) {
                // 立即清理覆盖，避免无限递归
                console.warn('调用保存的原始 update 方法失败，清理覆盖:', e.message || e);
                
                // 立即清理覆盖标志，防止无限递归
                this._mouthOverrideInstalled = false;
                this._origCoreModelUpdate = null;
                this._coreModelRef = null;
                
                // 临时恢复原始的 update 方法（如果可能），避免无限递归
                try {
                    // 尝试从原型链获取原始方法
                    const CoreModelProto = Object.getPrototypeOf(currentCoreModel);
                    if (CoreModelProto && CoreModelProto.update && typeof CoreModelProto.update === 'function') {
                        console.log('[Live2D Model] 从原型链成功恢复原始 update 方法');
                        // 临时恢复原始方法，避免无限递归
                        currentCoreModel.update = CoreModelProto.update;
                        // 调用一次原始方法
                        CoreModelProto.update.call(currentCoreModel);
                    } else {
                        console.warn('[Live2D Model] 原型链上未找到 update 方法，CoreModelProto:', CoreModelProto);
                        // 如果无法恢复，至少让模型继续运行（虽然可能没有口型同步）
                        console.warn('无法恢复原始 update 方法，模型将继续运行但可能没有口型同步');
                    }
                } catch (recoverError) {
                    console.error('恢复原始 update 方法失败:', recoverError);
                    // 即使恢复失败，也要继续，避免完全卡住
                }
                
                // 延迟重新安装覆盖（避免在 update 循环中直接调用导致问题）
                this._scheduleReinstallOverride();
                
                return;
            }
        } else {
            // 如果 origCoreModelUpdate 不存在，说明原始方法丢失
            // 延迟重新安装覆盖（避免在 update 循环中直接调用导致问题）
            console.warn('原始 coreModel.update 方法不可用或 coreModel 状态异常，延迟重新安装覆盖');
            this._mouthOverrideInstalled = false;
            this._origCoreModelUpdate = null;
            this._coreModelRef = null;
            this._scheduleReinstallOverride();
            return;
        }
    };

    this._mouthOverrideInstalled = true;
    // 重置重装计数（安装成功时）
    this._reinstallAttempts = 0;
    console.log('已安装双重参数覆盖（motionManager.update 后 + coreModel.update 前）');
};

// 设置嘴巴开合值（0~1）
Live2DManager.prototype.setMouth = function(value) {
    const v = Math.max(0, Math.min(1, Number(value) || 0));
    this.mouthValue = v;
    
    // 调试日志（每100次调用输出一次）
    if (typeof this._setMouthCallCount === 'undefined') this._setMouthCallCount = 0;
    this._setMouthCallCount++;
    const shouldLog = this._setMouthCallCount % 100 === 1;
    
    // 即时写入一次，best-effort 同步
    try {
        if (this.currentModel && this.currentModel.internalModel) {
            const coreModel = this.currentModel.internalModel.coreModel;
            // 使用完整的 LIPSYNC_PARAMS 列表，确保覆盖所有可能的口型参数
            const mouthIds = window.LIPSYNC_PARAMS || ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
            let paramsSet = [];
            for (const id of mouthIds) {
                try {
                    const idx = coreModel.getParameterIndex(id);
                    if (idx !== -1) {
                        // 对于 ParamMouthForm，通常表示嘴型（-1到1），不需要设置为 mouthValue
                        // ParamMouthOpenY, ParamMouthOpen, ParamA, ParamI, ParamU, ParamE, ParamO 都与张嘴程度相关
                        if (id === 'ParamMouthForm') {
                            // ParamMouthForm 保持不变或设置为中性值
                            continue;
                        }
                        coreModel.setParameterValueById(id, this.mouthValue, 1);
                        paramsSet.push(id);
                    }
                } catch (_) {}
            }
            if (shouldLog) {
                console.log('[Live2D setMouth] value:', v.toFixed(3), 'params set:', paramsSet.join(', '));
            }
        } else if (shouldLog) {
            console.warn('[Live2D setMouth] 模型未就绪');
        }
    } catch (e) {
        if (shouldLog) console.error('[Live2D setMouth] 错误:', e);
    }
};

// 应用模型设置
Live2DManager.prototype.applyModelSettings = function(model, options) {
    const { preferences, isMobile = false } = options;

    if (isMobile) {
        model.anchor.set(0.5, 0.1);
        const scale = Math.min(
            0.5,
            window.innerHeight * 1.3 / 4000,
            window.innerWidth * 1.2 / 2000
        );
        model.scale.set(scale);
        model.x = this.pixi_app.renderer.screen.width * 0.5;
        model.y = this.pixi_app.renderer.screen.height * 0.28;
    } else {
        model.anchor.set(0.65, 0.75);
        if (preferences && preferences.scale && preferences.position) {
            const scaleX = Number(preferences.scale.x);
            const scaleY = Number(preferences.scale.y);
            const posX = Number(preferences.position.x);
            const posY = Number(preferences.position.y);

            // 当前渲染器尺寸
            const rendererWidth = this.pixi_app.renderer.screen.width;
            const rendererHeight = this.pixi_app.renderer.screen.height;

            // 使用渲染器逻辑尺寸做归一化（renderer 不再自动 resize，尺寸等价于稳定的屏幕分辨率）
            const currentScreenW = this.pixi_app.renderer.screen.width;
            const currentScreenH = this.pixi_app.renderer.screen.height;
            const hasValidScreen = Number.isFinite(currentScreenW) && Number.isFinite(currentScreenH) &&
                currentScreenW > 0 && currentScreenH > 0;

            // 检查是否有保存的视口信息（用于跨分辨率归一化）
            const savedViewport = preferences.viewport;
            const hasViewport = hasValidScreen && savedViewport &&
                Number.isFinite(savedViewport.width) && Number.isFinite(savedViewport.height) &&
                savedViewport.width > 0 && savedViewport.height > 0;

            // 计算屏幕比例（如果保存时的屏幕与当前不同，则等比缩放位置和大小）
            let wRatio = 1;
            let hRatio = 1;
            if (hasViewport) {
                wRatio = currentScreenW / savedViewport.width;
                hRatio = currentScreenH / savedViewport.height;
            }

            // 验证缩放值是否有效
            if (Number.isFinite(scaleX) && Number.isFinite(scaleY) &&
                scaleX > 0 && scaleY > 0 && scaleX < 10 && scaleY < 10) {
                // 仅在屏幕分辨率发生"跨代"级别变化时（如 1080p→4K）才归一化缩放
                // 普通跨屏移动（如 1600x900→2560x1440）不调整，避免用户调好的大小被改
                const scaleRatio = Math.min(wRatio, hRatio);
                const isExtremeChange = hasViewport && (scaleRatio > 1.8 || scaleRatio < 0.56);
                if (isExtremeChange) {
                    model.scale.set(scaleX * scaleRatio, scaleY * scaleRatio);
                    console.log('屏幕分辨率大幅变化，缩放已归一化:', { wRatio, hRatio, scaleRatio });
                } else {
                    model.scale.set(scaleX, scaleY);
                }
            } else {
                console.warn('保存的缩放设置无效，使用默认值');
                const defaultScale = Math.min(
                    0.5,
                    (window.innerHeight * 0.75) / 7000,
                    (window.innerWidth * 0.6) / 7000
                );
                model.scale.set(defaultScale);
            }

            // 验证位置值是否有效
            if (Number.isFinite(posX) && Number.isFinite(posY) &&
                Math.abs(posX) < 100000 && Math.abs(posY) < 100000) {
                if (hasViewport && (Math.abs(wRatio - 1) > 0.01 || Math.abs(hRatio - 1) > 0.01)) {
                    // 视口尺寸有变化，按比例映射位置
                    model.x = posX * wRatio;
                    model.y = posY * hRatio;
                    console.log('视口变化，位置已归一化:', { posX, posY, newX: model.x, newY: model.y });
                } else {
                    model.x = posX;
                    model.y = posY;
                }
            } else {
                console.warn('保存的位置设置无效，使用默认值');
                model.x = rendererWidth;
                model.y = rendererHeight;
            }
        } else {
            const scale = Math.min(
                0.5,
                (window.innerHeight * 0.75) / 7000,
                (window.innerWidth * 0.6) / 7000
            );
            model.scale.set(scale);
            model.x = this.pixi_app.renderer.screen.width;
            model.y = this.pixi_app.renderer.screen.height;
        }
    }
};

// 应用模型参数
Live2DManager.prototype.applyModelParameters = function(model, parameters) {
    if (!model || !model.internalModel || !model.internalModel.coreModel || !parameters) {
        return;
    }
    
    const coreModel = model.internalModel.coreModel;
    const persistentParamIds = this.getPersistentExpressionParamIds();
    const visibilityParams = ['ParamOpacity', 'ParamVisibility']; // 跳过可见性参数，防止模型被设置为不可见

    for (const paramId in parameters) {
        if (parameters.hasOwnProperty(paramId)) {
            try {
                const value = parameters[paramId];
                if (typeof value !== 'number' || !Number.isFinite(value)) {
                    continue;
                }
                
                // 跳过常驻表情已设置的参数（保护去水印等功能）
                if (persistentParamIds.has(paramId)) {
                    continue;
                }
                
                // 跳过可见性参数，防止模型被设置为不可见
                if (visibilityParams.includes(paramId)) {
                    continue;
                }
                
                let idx = -1;
                if (paramId.startsWith('param_')) {
                    const indexStr = paramId.replace('param_', '');
                    const parsedIndex = parseInt(indexStr, 10);
                    if (!isNaN(parsedIndex) && parsedIndex >= 0 && parsedIndex < coreModel.getParameterCount()) {
                        idx = parsedIndex;
                    }
                } else {
                    try {
                        idx = coreModel.getParameterIndex(paramId);
                    } catch (e) {
                        // Ignore
                    }
                }
                
                if (idx >= 0) {
                    coreModel.setParameterValueByIndex(idx, value);
                }
            } catch (e) {
                // Ignore
            }
        }
    }
    
    // 参数已应用
};

// 获取常驻表情的所有参数ID集合（用于保护去水印等常驻表情参数）
Live2DManager.prototype.getPersistentExpressionParamIds = function() {
    const paramIds = new Set();
    
    if (this.persistentExpressionParamsByName) {
        for (const name in this.persistentExpressionParamsByName) {
            const params = this.persistentExpressionParamsByName[name];
            if (Array.isArray(params)) {
                for (const p of params) {
                    if (p && p.Id) {
                        paramIds.add(p.Id);
                    }
                }
            }
        }
    }
    
    return paramIds;
};