/**
 * Live2D Model - 模型加载、口型同步相关功能
 */

// 加载模型
Live2DManager.prototype.loadModel = async function(modelPath, options = {}) {
    if (!this.pixi_app) {
        throw new Error('PIXI 应用未初始化，请先调用 initPIXI()');
    }

    // 移除当前模型
    if (this.currentModel) {
        // 清除保存参数的定时器
        if (this._savedParamsTimer) {
            clearInterval(this._savedParamsTimer);
            this._savedParamsTimer = null;
        }
        
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
        // 同时移除 mouthTicker（若曾启用过 ticker 模式）
        if (this._mouthTicker && this.pixi_app && this.pixi_app.ticker) {
            try { this.pixi_app.ticker.remove(this._mouthTicker); } catch (_) {}
            this._mouthTicker = null;
        }

        // 移除由 HTML 锁图标或交互注册的监听，避免访问已销毁的显示对象
        try {
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

    try {
        const model = await Live2DModel.from(modelPath, { autoFocus: false });
        this.currentModel = model;

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
            this.modelName = parts.length > 0 ? parts[parts.length - 1] : null;
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

        // 应用位置和缩放设置
        this.applyModelSettings(model, options);
        
        // 应用保存的模型参数（如果有）
        if (options.preferences && options.preferences.parameters && model.internalModel && model.internalModel.coreModel) {
            this.applyModelParameters(model, options.preferences.parameters);
        }

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

        // 启用鼠标跟踪
        if (options.mouseTracking !== false) {
            this.enableMouseTracking(model);
        }

        // 设置浮动按钮系统（在模型完全就绪后再绑定ticker回调）
        this.setupFloatingButtons(model);
        
        // 设置原来的锁按钮
        this.setupHTMLLockIcon(model);

        // 先不安装口型覆盖，等参数加载完成后再安装（见下方）

        // 加载 FileReferences 与 EmotionMapping
        if (options.loadEmotionMapping !== false) {
            const settings = model.internalModel && model.internalModel.settings && model.internalModel.settings.json;
            if (settings) {
                // 保存原始 FileReferences
                this.fileReferences = settings.FileReferences || null;

                // 优先使用顶层 EmotionMapping，否则从 FileReferences 推导
                if (settings.EmotionMapping && (settings.EmotionMapping.expressions || settings.EmotionMapping.motions)) {
                    this.emotionMapping = settings.EmotionMapping;
                } else {
                    this.emotionMapping = this.deriveEmotionMappingFromFileRefs(this.fileReferences || {});
                }
                console.log('已加载情绪映射:', this.emotionMapping);
            } else {
                console.warn('模型配置中未找到 settings.json，无法加载情绪映射');
            }
        }

        // 设置常驻表情
        try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
        await this.setupPersistentExpressions();

        // 记录模型的初始参数（用于expression重置）
        this.recordInitialParameters();
        
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
        
        // 重新安装口型覆盖
        try {
            this.installMouthOverride();
        } catch (e) {
            console.error('安装口型覆盖失败:', e);
        }
        
        // 如果有保存的参数，使用定时器定期应用，确保不被常驻表情覆盖
        if (this.savedModelParameters && this._shouldApplySavedParams) {
            // 清除之前的定时器（如果存在）
            if (this._savedParamsTimer) {
                clearInterval(this._savedParamsTimer);
            }
            
            // 动画相关参数列表（这些参数不应该被覆盖，让模型保持动画）
            const animationParams = ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamBodyAngleX', 'ParamBodyAngleY', 'ParamBodyAngleZ', 
                                    'ParamBreath', 'ParamEyeLOpen', 'ParamEyeROpen', 'ParamEyeBallX', 'ParamEyeBallY',
                                    'ParamArm', 'ParamHand', 'ParamShoulder', 'ParamElbow', 'ParamWrist'];
            const lipSyncParams = ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
            
            // 获取常驻表情的所有参数ID集合（用于保护去水印等常驻表情参数）
            const persistentParamIds = this.getPersistentExpressionParamIds();
            
            // 每100ms应用一次保存的参数（但跳过常驻表情已设置的参数，保护去水印等功能）
            this._savedParamsTimer = setInterval(() => {
                if (!this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
                    return;
                }
                
                const coreModel = this.currentModel.internalModel.coreModel;
                let appliedCount = 0;
                let skippedPersistentCount = 0;
                
                for (const [paramId, value] of Object.entries(this.savedModelParameters)) {
                    // 跳过口型参数（口型参数由口型同步控制）
                    if (lipSyncParams.includes(paramId)) continue;
                    // 跳过动画参数（让模型保持动画效果）
                    if (animationParams.includes(paramId)) continue;
                    // 跳过 param_${i} 格式的参数（这些可能是动画参数，不确定）
                    if (paramId.startsWith('param_')) continue;
                    // 跳过常驻表情已设置的参数（保护去水印等功能，同时允许用户设置其他参数）
                    if (persistentParamIds.has(paramId)) {
                        skippedPersistentCount++;
                        continue;
                    }
                    
                    try {
                        // 只对明确的外观参数ID进行覆盖
                        const idx = coreModel.getParameterIndex(paramId);
                        if (idx >= 0 && typeof value === 'number' && Number.isFinite(value)) {
                            coreModel.setParameterValueByIndex(idx, value);
                            appliedCount++;
                        }
                    } catch (_) {}
                }
                
                // 调试信息（仅在开发时输出）
                // if (appliedCount > 0 || skippedPersistentCount > 0) {
                //     console.log(`应用用户参数: ${appliedCount}个, 跳过常驻表情参数: ${skippedPersistentCount}个`);
                // }
            }, 100); // 每100ms应用一次
        }
        
        // 如果之前应用了保存的参数（从用户偏好），在常驻表情设置后再次应用（防止被覆盖）
        // 但模型目录的parameters.json优先级更高，所以这里只作为备用
        if (options.preferences && options.preferences.parameters && model.internalModel && model.internalModel.coreModel) {
            // 延迟一点确保常驻表情已经设置完成，并且模型目录参数已经加载
            setTimeout(() => {
                this.applyModelParameters(model, options.preferences.parameters);
            }, 600); // 在模型目录参数之后应用
        }

        // 调用回调函数
        if (this.onModelLoaded) {
            this.onModelLoaded(model, modelPath);
        }

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

                // 解析模型目录名与根路径，供资源解析使用
                try {
                    const cleanPath = defaultModelPath.split('#')[0].split('?')[0];
                    const lastSlash = cleanPath.lastIndexOf('/');
                    const rootDir = lastSlash >= 0 ? cleanPath.substring(0, lastSlash) : '/static';
                    this.modelRootPath = rootDir;
                    const parts = rootDir.split('/').filter(Boolean);
                    this.modelName = parts.length > 0 ? parts[parts.length - 1] : null;
                    console.log('回退模型根路径解析:', { modelUrl: defaultModelPath, modelName: this.modelName, modelRootPath: this.modelRootPath });
                    try { this._lastLoadedModelPath = defaultModelPath; } catch (_) {}
                } catch (e) {
                    console.warn('解析回退模型根路径失败，将使用默认值', e);
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
                    console.log('回退模型渲染纹理数量已设置为3');
                }

                // 应用位置和缩放设置
                this.applyModelSettings(model, options);

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

                // 启用鼠标跟踪
                if (options.mouseTracking !== false) {
                    this.enableMouseTracking(model);
                }

                // 设置浮动按钮系统（在模型完全就绪后再绑定ticker回调）
                this.setupFloatingButtons(model);
                
                // 设置原来的锁按钮
                this.setupHTMLLockIcon(model);

                // 安装口型覆盖逻辑（屏蔽 motion 对嘴巴的控制）
                try {
                    this.installMouthOverride();
                    console.log('回退模型已安装口型覆盖');
                } catch (e) {
                    console.warn('回退模型安装口型覆盖失败:', e);
                }

                // 加载 FileReferences 与 EmotionMapping
                if (options.loadEmotionMapping !== false) {
                    const settings = model.internalModel && model.internalModel.settings && model.internalModel.settings.json;
                    if (settings) {
                        // 保存原始 FileReferences
                        this.fileReferences = settings.FileReferences || null;

                        // 优先使用顶层 EmotionMapping，否则从 FileReferences 推导
                        if (settings.EmotionMapping && (settings.EmotionMapping.expressions || settings.EmotionMapping.motions)) {
                            this.emotionMapping = settings.EmotionMapping;
                        } else {
                            this.emotionMapping = this.deriveEmotionMappingFromFileRefs(this.fileReferences || {});
                        }
                        console.log('回退模型已加载情绪映射:', this.emotionMapping);
                    } else {
                        console.warn('回退模型配置中未找到 settings.json，无法加载情绪映射');
                    }
                }

                // 设置常驻表情
                try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
                await this.setupPersistentExpressions();

                // 调用回调函数
                if (this.onModelLoaded) {
                    this.onModelLoaded(model, defaultModelPath);
                }

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
    }
};

// 不再需要预解析嘴巴参数ID，保留占位以兼容旧代码调用
Live2DManager.prototype.resolveMouthParameterId = function() { return null; };

// 安装覆盖：同时覆盖 motionManager.update 和 coreModel.update，双重保险
// motionManager.update 会重置参数，所以在其后覆盖；coreModel.update 前再覆盖一次确保生效
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

    // 口型参数列表（这些参数不会被常驻表情覆盖）
    const lipSyncParams = ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
    
    // 缓存参数索引，避免每帧查询
    const mouthParamIndices = {};
    for (const id of ['ParamMouthOpenY', 'ParamO']) {
        try {
            const idx = coreModel.getParameterIndex(id);
            if (idx >= 0) mouthParamIndices[id] = idx;
        } catch (_) {}
    }
    
    // 覆盖 1: motionManager.update - 在动作更新后立即覆盖参数
    if (internalModel.motionManager && typeof internalModel.motionManager.update === 'function') {
        const origMotionManagerUpdate = internalModel.motionManager.update.bind(internalModel.motionManager);
        this._origMotionManagerUpdate = origMotionManagerUpdate;
        
        internalModel.motionManager.update = () => {
            // 先调用原始的 motionManager.update
            if (origMotionManagerUpdate) {
                origMotionManagerUpdate();
            }
            // 然后在动作更新后立即覆盖参数
            try {
                // 写入口型参数
                for (const [id, idx] of Object.entries(mouthParamIndices)) {
                    try {
                        coreModel.setParameterValueByIndex(idx, this.mouthValue);
                    } catch (_) {}
                }
                // 写入常驻表情参数
                if (this.persistentExpressionParamsByName) {
                    const lipSyncParams = ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
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
            } catch (_) {}
        };
    }
    
    // 覆盖 2: coreModel.update - 在调用原始 update 之前写入参数
    // 在调用原始 update 之前写入参数（因为 update 会将参数应用到模型）
    coreModel.update = () => {
        try {
            // 1. 强制写入口型参数
            for (const [id, idx] of Object.entries(mouthParamIndices)) {
                try {
                    coreModel.setParameterValueByIndex(idx, this.mouthValue);
                } catch (_) {}
            }
            
            // 2. 写入常驻表情参数（跳过口型参数以避免覆盖lipsync）
            if (this.persistentExpressionParamsByName) {
                const lipSyncParams = ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
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
        } catch (e) {}
    };
    
    // 覆盖 1: motionManager.update - 在动作更新后立即覆盖参数
    if (motionManager && typeof motionManager.update === 'function') {
        const origMotionManagerUpdate = motionManager.update.bind(motionManager);
        this._origMotionManagerUpdate = origMotionManagerUpdate;
        
        motionManager.update = (model, now) => {
            const result = origMotionManagerUpdate(model, now);
            // 动作更新后立即覆盖参数
            overwriteParams();
            return result;
        };
    }
    
    // 覆盖 2: coreModel.update - 在渲染前再次确保参数正确
    const origCoreModelUpdate = coreModel.update ? coreModel.update.bind(coreModel) : null;
    this._origCoreModelUpdate = origCoreModelUpdate;
    
    coreModel.update = () => {
        // 渲染前再次覆盖参数
        overwriteParams();
        // 调用原始的 update 方法
        if (origCoreModelUpdate) {
            origCoreModelUpdate();
        }
    };

    this._mouthOverrideInstalled = true;
    console.log('已安装双重参数覆盖（motionManager.update 后 + coreModel.update 前）');
};

// 设置嘴巴开合值（0~1）
Live2DManager.prototype.setMouth = function(value) {
    const v = Math.max(0, Math.min(1, Number(value) || 0));
    this.mouthValue = v;
    // 即时写入一次，best-effort 同步
    try {
        if (this.currentModel && this.currentModel.internalModel) {
            const coreModel = this.currentModel.internalModel.coreModel;
            const mouthIds = ['ParamMouthOpenY', 'ParamO'];
            for (const id of mouthIds) {
                try {
                    if (coreModel.getParameterIndex(id) !== -1) {
                        coreModel.setParameterValueById(id, this.mouthValue, 1);
                    }
                } catch (_) {}
            }
        }
    } catch (_) {}
};

// 应用模型设置
Live2DManager.prototype.applyModelSettings = function(model, options) {
    const { preferences, isMobile = false } = options;

    if (isMobile) {
        // 移动端设置
        const scale = Math.min(
            0.5,
            window.innerHeight * 1.3 / 4000,
            window.innerWidth * 1.2 / 2000
        );
        model.scale.set(scale);
        model.x = this.pixi_app.renderer.width * 0.5;
        model.y = this.pixi_app.renderer.height * 0.28;
        model.anchor.set(0.5, 0.1);
    } else {
        // 桌面端设置
        if (preferences && preferences.scale && preferences.position) {
            // 使用保存的偏好设置
            model.scale.set(preferences.scale.x, preferences.scale.y);
            model.x = preferences.position.x;
            model.y = preferences.position.y;
        } else {
            // 使用默认设置（改为靠屏幕右侧）
            const scale = Math.min(
                0.5,
                (window.innerHeight * 0.75) / 7000,
                (window.innerWidth * 0.6) / 7000
            );
            model.scale.set(scale);
            // 将默认 x 调整到屏幕靠右位置，使用 0.85 作为右侧偏移比例
            // 向右下角进一步偏移，靠近屏幕右下
            model.x = this.pixi_app.renderer.width;
            model.y = this.pixi_app.renderer.height;
        }
        // 增大 anchor.x 以便模型更靠近右侧边缘
        model.anchor.set(0.65, 0.75);
    }
};

// 应用模型参数
Live2DManager.prototype.applyModelParameters = function(model, parameters) {
    if (!model || !model.internalModel || !model.internalModel.coreModel || !parameters) {
        return;
    }
    
    const coreModel = model.internalModel.coreModel;
    let appliedCount = 0;
    
    // 获取常驻表情的所有参数ID集合（用于保护去水印等常驻表情参数）
    const persistentParamIds = this.getPersistentExpressionParamIds();
    
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
                
                let idx = -1;
                // 如果参数ID是 param_${i} 格式，直接解析索引
                if (paramId.startsWith('param_')) {
                    const indexStr = paramId.replace('param_', '');
                    const parsedIndex = parseInt(indexStr, 10);
                    if (!isNaN(parsedIndex) && parsedIndex >= 0 && parsedIndex < coreModel.getParameterCount()) {
                        idx = parsedIndex;
                    }
                } else {
                    // 否则尝试通过参数ID获取索引
                    try {
                        idx = coreModel.getParameterIndex(paramId);
                    } catch (e) {
                        // getParameterIndex 失败，忽略
                    }
                }
                
                if (idx >= 0) {
                    coreModel.setParameterValueByIndex(idx, value);
                    appliedCount++;
                }
            } catch (e) {
                // 忽略不存在的参数
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

