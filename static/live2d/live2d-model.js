/**
 * Live2D Model - 模型加载、口型同步相关功能
 * 依赖: live2d-core.js (提供 Live2DManager 类和 window.LIPSYNC_PARAMS)
 */

// lipsync 强制覆盖 motion mouth 参数的阈值：mouthValue 高于此值时强制写入，否则让位给 motion 自带的嘴部关键帧
const LIPSYNC_OVERRIDE_THRESHOLD = 0.001;

// Bundled pixi-live2d-display MotionPriority enum. Keep this local instead of
// reading window.PIXI.live2d.MotionPriority, which is not a stable runtime path.
const LIVE2D_MOTION_PRIORITY = Object.freeze({
    NONE: 0,
    IDLE: 1,
    NORMAL: 2,
    FORCE: 3
});

// 缓动函数集合（用于眨眼、口型等动画的平滑过渡）
const Easing = {
    linear: (t) => t,
    easeInQuad: (t) => t * t,
    easeInCubic: (t) => t * t * t,
    easeOutQuad: (t) => t * (2 - t),
    easeOutCubic: (t) => --t * t * t + 1,
    easeInOutQuad: (t) => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t,
    easeOutElastic: (t) => {
        const c4 = (2 * Math.PI) / 3;
        return t === 0 ? 0 : t === 1 ? 1 : Math.pow(2, -10 * t) * Math.sin((t * 10 - 0.75) * c4) + 1;
    }
};

Live2DManager.prototype.removeModel = async function(options = {}) {
    const shouldSkipCloseWindows = options.skipCloseWindows === true;
    const activeModel = this.currentModel || null;
    const stage = this.pixi_app && this.pixi_app.stage;
    const ticker = this.pixi_app && this.pixi_app.ticker;

    if (window.closeAllSettingsWindows && !shouldSkipCloseWindows) {
        try {
            window.closeAllSettingsWindows();
        } catch (_) {}
    }

    if (this._savedParamsTimer) {
        clearInterval(this._savedParamsTimer);
        this._savedParamsTimer = null;
    }

    if (this._reinstallTimer) {
        clearTimeout(this._reinstallTimer);
        this._reinstallTimer = null;
        this._reinstallScheduled = false;
    }

    if (this._canvasRevealTimer) {
        clearTimeout(this._canvasRevealTimer);
        this._canvasRevealTimer = null;
    }

    try {
        if (this.pixi_app && this.pixi_app.view && this.pixi_app.view.style) {
            this.pixi_app.view.style.transition = '';
            this.pixi_app.view.style.opacity = '';
        }
    } catch (_) {}

    if (this._idleMotionLoopTimers instanceof Set) {
        this._idleMotionLoopTimers.forEach(timer => clearTimeout(timer));
        this._idleMotionLoopTimers.clear();
    }

    if (activeModel) {
        try {
            const evts = activeModel.internalModel && activeModel.internalModel.events;
            if (evts && typeof evts.removeAllListeners === 'function') {
                evts.removeAllListeners('motionFinish');
            }
        } catch (_) {}
    }

    this._reinstallAttempts = 0;
    if (typeof this.teardownPersistentExpressions === 'function') {
        try {
            this.teardownPersistentExpressions();
        } catch (_) {}
    }
    this.initialParameters = {};
    this.motionBaselineParameters = {};
    this.appearanceBaselineParameters = {};
    this._activeExpressionParamIds = null;
    this._activeMotionParamIds = null;
    this._motionParameterTrackGeneration = (this._motionParameterTrackGeneration || 0) + 1;
    if (typeof this._nextMotionTimerGeneration === 'function') {
        this._nextMotionTimerGeneration();
    } else {
        this._motionTimerGeneration = (this._motionTimerGeneration || 0) + 1;
    }

    if (activeModel) {
        try {
            const coreModel = activeModel.internalModel && activeModel.internalModel.coreModel;
            if (coreModel && this._mouthOverrideInstalled && typeof this._origCoreModelUpdate === 'function') {
                coreModel.update = this._origCoreModelUpdate;
            }
        } catch (_) {}
    }
    this._mouthOverrideInstalled = false;
    this._origCoreModelUpdate = null;
    this._coreModelRef = null;
    this._temporaryPoseOverride = null;
    this._temporaryPoseOverrides = new Map();

    if (this._mouthTicker && ticker) {
        try {
            ticker.remove(this._mouthTicker);
        } catch (_) {}
    }
    this._mouthTicker = null;

    try {
        if (this._mouseTrackingListener) {
            window.removeEventListener('pointermove', this._mouseTrackingListener);
            this._mouseTrackingListener = null;
        }

        if (this._lockIconTicker && ticker) {
            ticker.remove(this._lockIconTicker);
        }
        this._lockIconTicker = null;
        if (this._lockIconElement && this._lockIconElement.parentNode) {
            this._lockIconElement.parentNode.removeChild(this._lockIconElement);
        }
        this._lockIconElement = null;

        if (this._floatingButtonsTicker && ticker) {
            ticker.remove(this._floatingButtonsTicker);
        }
        this._floatingButtonsTicker = null;
        if (this._floatingButtonsContainer && this._floatingButtonsContainer.parentNode) {
            this._floatingButtonsContainer.parentNode.removeChild(this._floatingButtonsContainer);
        }
        this._floatingButtonsContainer = null;
        this._floatingButtons = {};

        if (this._returnButtonContainer && this._returnButtonContainer.parentNode) {
            this._returnButtonContainer.parentNode.removeChild(this._returnButtonContainer);
        }
        this._returnButtonContainer = null;

        if (this._popupTimers) {
            Object.values(this._popupTimers).forEach(timer => clearTimeout(timer));
        }
        this._popupTimers = {};
    } catch (_) {}

    // ticker.stop 单独保护：上一段 UI 清理任意一步抛错都不能让 ticker 漏停
    try {
        ticker && ticker.stop && ticker.stop();
    } catch (_) {}

    try {
        stage && stage.removeAllListeners && stage.removeAllListeners();
    } catch (_) {}
    try {
        activeModel && activeModel.removeAllListeners && activeModel.removeAllListeners();
    } catch (_) {}

    try {
        stage && stage.removeChild && activeModel && stage.removeChild(activeModel);
    } catch (_) {}
    try {
        activeModel && activeModel.destroy && activeModel.destroy({ children: true });
    } catch (_) {}

    try {
        if (stage && Array.isArray(stage.children)) {
            const orphanedModels = [];
            for (let i = stage.children.length - 1; i >= 0; i--) {
                const child = stage.children[i];
                if (child && child.internalModel) {
                    orphanedModels.push(child);
                }
            }
            for (const child of orphanedModels) {
                try { stage.removeChild(child); } catch (_) {}
                try { child.destroy({ children: true }); } catch (_) {}
            }
        }
    } catch (e) {
        console.warn('清理舞台残留模型时出错:', e);
    }

    try {
        ticker && ticker.start && ticker.start();
    } catch (_) {}

    this.currentModel = null;
    this._lastLoadedModelPath = null;
    if (typeof this._resetDerivedModelMetadata === 'function') {
        this._resetDerivedModelMetadata();
    }
    this._isModelReadyForInteraction = false;
    if (!this._isLoadingModel) {
        this._modelLoadState = 'idle';
    }
};

Live2DManager.prototype._stringifyCoreParameterId = function(paramId) {
    if (paramId === undefined || paramId === null) return '';
    if (typeof paramId === 'string') return paramId;
    try {
        if (typeof paramId.getString === 'function') {
            const value = paramId.getString();
            if (typeof value === 'string') return value;
            if (value && typeof value.s === 'string') return value.s;
            if (value !== undefined && value !== null) return String(value);
        }
    } catch (_) {}
    try {
        const value = String(paramId);
        return value && value !== '[object Object]' ? value : '';
    } catch (_) {
        return '';
    }
};

// Cubism 2/3/4/5 wrappers expose parameter IDs through different fields.
// Keep the compatibility lookup in one place so editor/load/save paths never
// invent different keys for the same parameter index.
Live2DManager.prototype._getCoreParameterId = function(coreModel, index) {
    if (!coreModel || !Number.isInteger(index) || index < 0) return '';

    const candidates = [];
    try { candidates.push(coreModel._parameterIds?.[index]); } catch (_) {}
    try { candidates.push(coreModel._model?.parameters?.ids?.[index]); } catch (_) {}
    try { candidates.push(coreModel.model?.parameters?.ids?.[index]); } catch (_) {}
    try { candidates.push(coreModel.parameters?.ids?.[index]); } catch (_) {}
    try {
        if (typeof coreModel.getParameterId === 'function') {
            candidates.push(coreModel.getParameterId(index));
        }
    } catch (_) {}

    for (const candidate of candidates) {
        const id = this._stringifyCoreParameterId(candidate);
        if (id) return id;
    }
    return '';
};

Live2DManager.prototype._getCoreParameterDefaultValue = function(coreModel, index) {
    if (!coreModel || !Number.isInteger(index) || index < 0) return undefined;
    return this._readParameterValueByIndex(
        coreModel,
        index,
        [
            'getParameterDefaultValueByIndex',
            'getParameterDefaultValue',
            'getParamDefaultValue',
            'getParamDefault'
        ],
        ['defaultValues', 'defaults', '_parameterDefaultValues'],
        undefined
    );
};

Live2DManager.prototype._buildModelParameterCatalog = function(coreModel) {
    if (!coreModel || typeof coreModel.getParameterCount !== 'function') return [];

    const catalog = [];
    const usedKeys = new Set();
    const parameterCount = coreModel.getParameterCount();
    for (let index = 0; index < parameterCount; index++) {
        const id = this._getCoreParameterId(coreModel, index);
        let key = id || `param_${index}`;
        // Model parameter IDs should be unique. Preserve every parameter even
        // for malformed models by falling back to its stable runtime index.
        if (usedKeys.has(key)) key = `param_${index}`;
        usedKeys.add(key);
        catalog.push({
            index,
            id: id || '',
            key,
            defaultValue: this._getCoreParameterDefaultValue(coreModel, index)
        });
    }
    return catalog;
};

Live2DManager.prototype._resolveModelParameterKey = function(coreModel, paramId) {
    if (!coreModel || paramId === undefined || paramId === null) return null;

    const key = String(paramId);
    const isIndexKey = /^(?:param_)?\d+$/.test(key);
    let idx = -1;
    let resolvedId = key;
    let hasResolvedId = !isIndexKey;

    if (isIndexKey) {
        const parsedIndex = parseInt(key.replace(/^param_/, ''), 10);
        const parameterCount = typeof coreModel.getParameterCount === 'function'
            ? coreModel.getParameterCount()
            : Number.POSITIVE_INFINITY;
        if (parsedIndex >= 0 && parsedIndex < parameterCount) {
            idx = parsedIndex;
            const id = this._getCoreParameterId(coreModel, idx);
            if (id) {
                resolvedId = id;
                hasResolvedId = true;
            }
        }
    } else {
        try {
            idx = coreModel.getParameterIndex(key);
        } catch (_) {}
    }

    if (idx >= 0 && isIndexKey && !hasResolvedId) {
        resolvedId = `param_${idx}`;
    }

    return idx >= 0 ? { idx, resolvedId, hasResolvedId, isIndexKey } : null;
};

// Lazy migration for legacy parameter dictionaries. Historical param_N keys
// have no parameter identity, so they are only safe when the runtime cannot
// expose an official ID for that index. Once an official ID is available,
// discard the ambiguous alias instead of guessing across model revisions.
Live2DManager.prototype._normalizeModelParameters = function(coreModel, parameters) {
    if (!coreModel || !parameters || typeof parameters !== 'object') return {};

    const catalogByIndex = new Map(
        this._buildModelParameterCatalog(coreModel).map(parameter => [parameter.index, parameter])
    );
    const normalizedByIndex = new Map();
    for (const [paramId, value] of Object.entries(parameters)) {
        if (typeof value !== 'number' || !Number.isFinite(value)) continue;
        const resolved = this._resolveModelParameterKey(coreModel, paramId);
        if (!resolved) continue;
        const catalogEntry = catalogByIndex.get(resolved.idx);
        if (resolved.isIndexKey && catalogEntry?.id) continue;
        const canonicalKey = catalogEntry?.key
            || (resolved.hasResolvedId && resolved.resolvedId
                ? String(resolved.resolvedId)
                : `param_${resolved.idx}`);
        const priority = resolved.isIndexKey ? 0 : 1;
        const existing = normalizedByIndex.get(resolved.idx);
        if (!existing || priority >= existing.priority) {
            normalizedByIndex.set(resolved.idx, { key: canonicalKey, value, priority });
        }
    }

    const normalized = {};
    for (const { key, value } of normalizedByIndex.values()) {
        normalized[key] = value;
    }
    return normalized;
};

Live2DManager.prototype._isRuntimeManagedAppearanceParam = function(paramId, resolvedParamId, coreModel) {
    const ids = [paramId, resolvedParamId].filter(Boolean).map(id => String(id));
    if (ids.length === 0) return true;

    const hasNamedParamId = ids.some(id => !/^(?:param_)?\d+$/.test(id));
    if (!hasNamedParamId) {
        return true;
    }

    const lipSyncParams = typeof window !== 'undefined' && Array.isArray(window.LIPSYNC_PARAMS)
        ? window.LIPSYNC_PARAMS
        : ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];
    const runtimeParamIds = new Set([
        ...lipSyncParams,
        'ParamAngleX', 'ParamAngleY', 'ParamAngleZ',
        'ParamBodyAngleX', 'ParamBodyAngleY', 'ParamBodyAngleZ',
        'ParamEyeBallX', 'ParamEyeBallY',
        'ParamLookAtX', 'ParamLookAtY',
        'ParamBreath', 'ParamBreath2', 'ParamBreath3',
        'ParamShake',
        'ParamOpacity', 'ParamVisibility'
    ]);

    try {
        const breathParams = typeof this._resolveRuntimeBreathParams === 'function'
            ? this._resolveRuntimeBreathParams(coreModel)
            : [];
        breathParams.forEach(id => runtimeParamIds.add(id));
    } catch (_) {}

    try {
        if (this._parameterEditingMode !== true && typeof this.getPersistentExpressionParamIds === 'function') {
            const persistentParamIds = this.getPersistentExpressionParamIds();
            if (ids.some(id => persistentParamIds.has(id))) return true;
        }
    } catch (_) {}

    return ids.some(id => runtimeParamIds.has(id) || this._isEyeBlinkParamId(id));
};

Live2DManager.prototype.mergeAppearanceBaselineParameters = function(model, parameters) {
    if (!model || !model.internalModel || !model.internalModel.coreModel || !parameters) {
        return;
    }

    if (!this.initialParameters || Object.keys(this.initialParameters).length === 0) {
        return;
    }

    const coreModel = model.internalModel.coreModel;
    const mergeAppearanceParam = (paramId, value) => {
        if (typeof value !== 'number' || !Number.isFinite(value)) return;

        const resolved = this._resolveModelParameterKey(coreModel, paramId);
        if (!resolved) return;

        const resolvedParamId = resolved.resolvedId;
        if (this._isRuntimeManagedAppearanceParam(paramId, resolvedParamId, coreModel)) {
            return;
        }

        this.appearanceBaselineParameters[paramId] = value;
        this.appearanceBaselineParameters[resolvedParamId] = value;
        this.appearanceBaselineParameters[`param_${resolved.idx}`] = value;
    };

    if (!this.appearanceBaselineParameters || Object.keys(this.appearanceBaselineParameters).length === 0) {
        this.appearanceBaselineParameters = {};
        for (const paramId in this.initialParameters) {
            if (!Object.prototype.hasOwnProperty.call(this.initialParameters, paramId)) continue;
            mergeAppearanceParam(paramId, this.initialParameters[paramId]);
        }
    }

    for (const paramId in parameters) {
        if (!Object.prototype.hasOwnProperty.call(parameters, paramId)) continue;
        mergeAppearanceParam(paramId, parameters[paramId]);
    }
};

Live2DManager.prototype._normalizeModelPreferencePath = function(modelPath) {
    const path = modelPath && typeof modelPath === 'object' ? modelPath.url : modelPath;
    return typeof path === 'string' ? path.split('#')[0].split('?')[0] : '';
};

Live2DManager.prototype._findModelPreference = function(preferences, modelPath) {
    if (!Array.isArray(preferences)) return null;
    const normalizedPath = this._normalizeModelPreferencePath(modelPath);
    return preferences.find((preference) => (
        preference && this._normalizeModelPreferencePath(preference.model_path) === normalizedPath
    )) || null;
};

Live2DManager.prototype._mergeEffectiveModelParameters = function(modelDirectoryParameters, userPreferenceParameters, coreModel) {
    const directoryParameters = coreModel
        ? this._normalizeModelParameters(coreModel, modelDirectoryParameters)
        : (modelDirectoryParameters && typeof modelDirectoryParameters === 'object' ? modelDirectoryParameters : {});
    const preferenceParameters = coreModel
        ? this._normalizeModelParameters(coreModel, userPreferenceParameters)
        : (userPreferenceParameters && typeof userPreferenceParameters === 'object' ? userPreferenceParameters : {});
    return {
        ...directoryParameters,
        ...preferenceParameters
    };
};

Live2DManager.prototype._loadModelDirectoryParameters = async function(modelName) {
    if (!modelName) return {};
    const response = await fetch(`/api/live2d/load_model_parameters/${encodeURIComponent(modelName)}`);
    if (response.ok === false) {
        throw new Error(`加载模型目录参数失败: HTTP ${response.status}`);
    }
    const data = await response.json();
    return data.success && data.parameters && typeof data.parameters === 'object'
        ? data.parameters
        : {};
};

Live2DManager.prototype._applyEffectiveModelParameters = function(model, modelDirectoryParameters, userPreferenceParameters) {
    const coreModel = model?.internalModel?.coreModel;
    const effectiveParameters = this._mergeEffectiveModelParameters(
        modelDirectoryParameters,
        userPreferenceParameters,
        coreModel
    );
    const hasEffectiveParameters = Object.keys(effectiveParameters).length > 0;

    this.effectiveModelParameters = effectiveParameters;
    this.savedModelParameters = hasEffectiveParameters ? effectiveParameters : null;
    this._shouldApplySavedParams = hasEffectiveParameters && this._parameterEditingMode !== true;

    if (hasEffectiveParameters) {
        this.applyModelParameters(model, effectiveParameters);
    }

    // installMouthOverride 会捕获 savedModelParameters，参数变化后必须重新安装。
    this.installMouthOverride();
    return effectiveParameters;
};

Live2DManager.prototype.reloadModelParameters = async function(options = {}) {
    const model = this.currentModel;
    if (!model || !model.internalModel || !model.internalModel.coreModel) {
        throw new Error('当前 Live2D 模型尚未就绪');
    }

    const requestedPath = this._normalizeModelPreferencePath(options.modelPath || options.model_path);
    const currentPath = this._normalizeModelPreferencePath(this._lastLoadedModelPath);
    const requestedName = String(options.modelName || options.model_name || '').trim();

    if ((requestedPath && requestedPath !== currentPath)
        || (!requestedPath && requestedName && requestedName !== this.modelName)) {
        return { applied: false, reason: 'model_mismatch' };
    }

    let modelDirectoryParameters = {};
    try {
        modelDirectoryParameters = await this._loadModelDirectoryParameters(this.modelName);
    } catch (error) {
        console.warn('[Live2D] 热刷新模型目录参数失败，将继续使用用户偏好:', error);
    }

    const preferences = await this.loadUserPreferences();
    if (this.currentModel !== model) {
        return { applied: false, reason: 'model_mismatch' };
    }
    const preference = this._findModelPreference(preferences, this._lastLoadedModelPath);
    if (!preference || !preference.parameters || typeof preference.parameters !== 'object') {
        throw new Error('未找到当前模型的用户偏好参数');
    }
    const userPreferenceParameters = preference && preference.parameters;
    const effectiveParameters = this._applyEffectiveModelParameters(
        model,
        modelDirectoryParameters,
        userPreferenceParameters
    );

    return { applied: true, parameters: effectiveParameters };
};

// 加载模型
Live2DManager.prototype.loadModel = async function(modelPath, options = {}) {
    const isModelManagerPage = document.body?.classList.contains('model-manager-page')
        || window.location.pathname.includes('model_manager');
    const isPNGTuberPageMode = (window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber';
    if (isPNGTuberPageMode && !isModelManagerPage) {
        console.log('[Live2D] 当前为 PNGTuber 模式，跳过 Live2D 模型加载:', modelPath);
        const skipError = new Error('Live2D load skipped while PNGTuber mode is active.');
        skipError.name = 'PNGTuberActiveLive2DSkip';
        return Promise.reject(skipError);
    }

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
    this._resetDerivedModelMetadata();

    // 清除上一次加载遗留的画布揭示定时器
    if (this._canvasRevealTimer) {
        clearTimeout(this._canvasRevealTimer);
        this._canvasRevealTimer = null;
    }

    try {
        // 移除当前模型
        if (this.currentModel) {
            await this.removeModel({
                skipCloseWindows: !!options.skipCloseWindows
            });
        }

        const model = await Live2DModel.from(modelPath, { autoFocus: false });
        if (!this._isLoadTokenActive(loadToken)) {
            try { model && model.destroy && model.destroy({ children: true }); } catch (_) {}
            const cancelError = new Error('Live2D load superseded by a newer model request.');
            cancelError.name = 'LoadSuperseded';
            throw cancelError;
        }
        if ((window.lanlan_config?.model_type || '').toLowerCase() === 'pngtuber' && !isModelManagerPage) {
            try { model && model.destroy && model.destroy({ children: true }); } catch (_) {}
            this._activeLoadToken = (this._activeLoadToken || 0) + 1;
            this._isLoadingModel = false;
            this.currentModel = null;
            this._modelLoadState = 'idle';
            this._isModelReadyForInteraction = false;
            const cancelError = new Error('Live2D load cancelled because PNGTuber mode became active.');
            cancelError.name = 'PNGTuberActiveLive2DSkip';
            throw cancelError;
        }
        this.currentModel = model;

        // 使用统一的模型配置方法
        await this._configureLoadedModel(model, modelPath, options, loadToken);

        return model;
    } catch (error) {
        if (error && error.name === 'LoadSuperseded') {
            throw error;
        }
        if (error && error.name === 'PNGTuberActiveLive2DSkip') {
            console.log('[Live2D] PNGTuber 模式已接管，取消 Live2D 加载且不回退默认模型');
            throw error;
        }
        console.error('加载模型失败:', error);
        
        // 尝试回退到默认模型
        if (modelPath !== '/static/yui-origin/yui-origin.model3.json') {
            console.warn('模型加载失败，尝试回退到默认模型: yui-origin');
            try {
                const defaultModelPath = '/static/yui-origin/yui-origin.model3.json';
                // 主模型可能已在 _configureLoadedModel 中途写入派生状态；
                // 回退加载前先清空，避免默认模型继承失败模型的元数据。
                this._resetDerivedModelMetadata();
                const model = await Live2DModel.from(defaultModelPath, { autoFocus: false });
                this._resetDerivedModelMetadata();
                this.currentModel = model;

                // 使用统一的模型配置方法
                await this._configureLoadedModel(model, defaultModelPath, options, loadToken);

                console.log('成功回退到默认模型: yui-origin');
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
        if (this._activeLoadToken === loadToken) {
            this._isLoadingModel = false;
            if (this._modelLoadState !== 'ready') {
                this._modelLoadState = 'idle';
                this._isModelReadyForInteraction = false;
            }
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

// 检查加载令牌是否仍然有效（用于取消过期加载）
Live2DManager.prototype._isLoadTokenActive = function(loadToken) {
    return this._activeLoadToken === loadToken;
};

// 获取当前已加载的模型实例
Live2DManager.prototype.getCurrentModel = function() {
    return this.currentModel || null;
};

// 安装短期姿态覆盖。多个来源可按注册顺序组合，避免并行动作互相顶掉。
Live2DManager.prototype.setTemporaryPoseOverride = function(source, apply, options = {}) {
    if (typeof apply !== 'function') {
        return false;
    }
    const normalizedSource = String(source || 'temporary_pose');
    const entry = {
        source: normalizedSource,
        apply: apply,
        priority: Number.isFinite(Number(options.priority)) ? Number(options.priority) : 0,
        sequence: ((this._temporaryPoseOverrideSequence || 0) + 1)
    };
    this._temporaryPoseOverrideSequence = entry.sequence;
    if (!this._temporaryPoseOverrides || typeof this._temporaryPoseOverrides.set !== 'function') {
        this._temporaryPoseOverrides = new Map();
    }
    if (this._temporaryPoseOverrides.has(normalizedSource)) {
        this._temporaryPoseOverrides.delete(normalizedSource);
    }
    this._temporaryPoseOverrides.set(normalizedSource, entry);
    this._temporaryPoseOverride = entry;
    return true;
};

Live2DManager.prototype.clearTemporaryPoseOverride = function(source) {
    const overrides = this._temporaryPoseOverrides;
    const active = this._temporaryPoseOverride;
    if ((!overrides || typeof overrides.delete !== 'function' || overrides.size === 0) && !active) {
        return;
    }
    const normalizedSource = arguments.length === 0 ? '' : String(source);
    if (overrides && typeof overrides.clear === 'function' && (overrides.size > 0 || !active)) {
        if (normalizedSource === '') {
            overrides.clear();
        } else {
            overrides.delete(normalizedSource);
        }
        const remaining = Array.from(overrides.values());
        this._temporaryPoseOverride = remaining.length ? remaining[remaining.length - 1] : null;
        return;
    }
    if (normalizedSource === '' || (active && active.source === normalizedSource)) {
        this._temporaryPoseOverride = null;
    }
};

Live2DManager.prototype._applyTemporaryPoseOverride = function(coreModel) {
    if (!coreModel) {
        return;
    }
    const overrides = this._temporaryPoseOverrides && typeof this._temporaryPoseOverrides.values === 'function'
        ? Array.from(this._temporaryPoseOverrides.values())
        : [];
    const entries = overrides.length ? overrides : (this._temporaryPoseOverride ? [this._temporaryPoseOverride] : []);
    if (!entries.length) {
        return;
    }
    entries.sort((left, right) => {
        const leftPriority = Number.isFinite(Number(left && left.priority)) ? Number(left.priority) : 0;
        const rightPriority = Number.isFinite(Number(right && right.priority)) ? Number(right.priority) : 0;
        if (leftPriority !== rightPriority) {
            return leftPriority - rightPriority;
        }
        const leftSequence = Number.isFinite(Number(left && left.sequence)) ? Number(left.sequence) : 0;
        const rightSequence = Number.isFinite(Number(right && right.sequence)) ? Number(right.sequence) : 0;
        return leftSequence - rightSequence;
    });
    const context = {
        manager: this,
        model: this.currentModel || null,
        now: performance.now()
    };
    entries.forEach((entry) => {
        if (!entry || typeof entry.apply !== 'function') {
            return;
        }
        try {
            entry.apply(coreModel, context);
        } catch (error) {
            console.warn('[Live2D] 临时姿态覆盖执行失败，已清理:', error);
            if (this._temporaryPoseOverrides && typeof this._temporaryPoseOverrides.delete === 'function') {
                this._temporaryPoseOverrides.delete(entry.source);
                const remaining = Array.from(this._temporaryPoseOverrides.values());
                this._temporaryPoseOverride = remaining.length ? remaining[remaining.length - 1] : null;
            } else {
                this._temporaryPoseOverride = null;
            }
        }
    });
    if (this._temporaryPoseOverrides && this._temporaryPoseOverrides.size > 0) {
        const remaining = Array.from(this._temporaryPoseOverrides.values());
        this._temporaryPoseOverride = remaining[remaining.length - 1] || null;
    }
};

// 重置模型相关的派生元数据（用于加载新模型前清理旧状态）
Live2DManager.prototype._resetDerivedModelMetadata = function() {
    this._randomLookAtAffectsHead = false; // 是否允许随机视线影响头部角度
    this._displayInfo = null;
    this._autoNamedHitAreaIds = new Set();
    this.fileReferences = null;
    this.emotionMapping = null;
    this.savedModelParameters = null;
    this.effectiveModelParameters = {};
    this._shouldApplySavedParams = false;
    this._parameterEditingMode = false;
    this.modelName = null;
    this.modelRootPath = null;
    this._eyeBlinkParams = null;
    this._eyeBlinkState = 0;
    this._eyeBlinkTimer = 0;
    this._eyeBlinkNextTime = 2 + Math.random() * 4;
    this._autoEyeBlinkEnabled = false;
    this._suspendEyeBlinkOverride = false;
    this._isMouthDrivenByMotion = false;
    this._isEyeDrivenByMotion = false;
    this._isLookAtDrivenByMotion = false;
    this._isBreathDrivenByMotion = false;
    this._motionDrivenBreathParamIds = null;
    this._motionDrivenBreathMotionKey = null;
    this._runtimeBreathTime = 0;
    this._runtimeBreathParamIds = null;
    this._lookAtTimer = undefined;
    this._lookAtNextTime = 0;
    this._lookAtTargetX = 0;
    this._lookAtTargetY = 0;
    this._lookAtCurrentX = 0;
    this._lookAtCurrentY = 0;
    this._temporaryPoseOverride = null;
    this._temporaryPoseOverrides = new Map();
    this._temporaryPoseOverrideSequence = 0;
    this._temporaryMotionSuspendToken = null;
    this._idleMotionFinishHandler = null;
    this._idleMotionFinishModel = null;
    this._bubbleGeometryCache = null;
    this._bubbleGeometryModelReadyAt = 0;
    this._bubbleGeometryRefreshPass = 0;

    if (this._missingExpressionFiles instanceof Set) {
        this._missingExpressionFiles.clear();
    } else {
        this._missingExpressionFiles = new Set();
    }
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
    // 每批次开头从 model.elapsedTime 重新读取，确保把 await 间隙里正常 ticker 的推进吸收进来，
    // 避免本地快照覆盖外部进度导致模型时钟回退（physics/motion fade 会因此抖）
    let elapsedTime = Number.isFinite(model.elapsedTime) ? model.elapsedTime : 0;

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

            // 重新与外部时钟对齐：若 await 期间 ticker 已向前推进，跟随其前进；本地从不回退
            const externalNow = Number.isFinite(model.elapsedTime) ? model.elapsedTime : elapsedTime;
            if (externalNow > elapsedTime) elapsedTime = externalNow;

            const batchEnd = Math.min(completed + BATCH_SIZE, totalSteps);
            for (let i = completed; i < batchEnd; i++) {
                elapsedTime += stepMs;
                internalModel.update(stepMs, elapsedTime);
            }
            completed = batchEnd;
            model.elapsedTime = elapsedTime;
            model.deltaTime = 0;

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
    const finalExternal = Number.isFinite(model.elapsedTime) ? model.elapsedTime : elapsedTime;
    model.elapsedTime = Math.max(elapsedTime, finalExternal);
    model.deltaTime = 0;

    console.log('[Live2D] 物理预跑完成');
};

// 不再需要预解析嘴巴参数ID，保留占位以兼容旧代码调用
Live2DManager.prototype.resolveMouthParameterId = function() { return null; };

// 加载 DisplayInfo 文件（包含额外的模型显示信息）
Live2DManager.prototype._loadDisplayInfo = async function(settings) {
    this._displayInfo = null;

    const displayInfoPath = settings?.FileReferences?.DisplayInfo;
    if (!displayInfoPath) {
        return null;
    }

    let resolvedPath = displayInfoPath;
    if (!/^(?:[a-z]+:)?\/\//i.test(displayInfoPath) && !displayInfoPath.startsWith('/')) {
        resolvedPath = `${this.modelRootPath}/${displayInfoPath}`;
    }

    try {
        const response = await fetch(resolvedPath);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        this._displayInfo = await response.json();
        return this._displayInfo;
    } catch (error) {
        console.warn('[Live2D] 加载 DisplayInfo 失败:', displayInfoPath, error);
        this._displayInfo = null;
        return null;
    }
};

// 验证眨眼参数组配置，若未配置则自动扫描
Live2DManager.prototype._validateEyeBlinkGroup = function(settings, model) {
    const groups = settings?.Groups;
    let eyeBlinkIds = null;
    let usedFallbackScan = false;
    this._autoEyeBlinkEnabled = false;
    this._eyeBlinkParams = null;

    if (!groups || !Array.isArray(groups)) {
        console.warn(`[Live2D EyeBlink] 模型 ${this.modelName || this.modelRootPath} 的 .model3.json 中缺少 Groups 字段或 Groups 不是数组，将尝试自动扫描眨眼参数`);
    } else {
        const eyeBlinkGroup = groups.find(g => g?.Target === 'Parameter' && g?.Name === 'EyeBlink');
        if (eyeBlinkGroup) {
            const ids = eyeBlinkGroup?.Ids;
            if (ids && Array.isArray(ids) && ids.length > 0) {
                eyeBlinkIds = ids;
            }
        }
        if (!eyeBlinkIds) {
            console.warn(`[Live2D EyeBlink] 模型 ${this.modelName || this.modelRootPath} 的 .model3.json 中 EyeBlink Ids 为空或不存在，将尝试自动扫描眨眼参数`);
        }
    }

    if (!eyeBlinkIds) {
        const scanned = this._scanEyeBlinkParams(model);
        if (scanned && scanned.length > 0) {
            eyeBlinkIds = scanned;
            usedFallbackScan = true;
            console.log(`[Live2D EyeBlink] 自动扫描到眨眼参数:`, eyeBlinkIds);
        } else {
            console.warn(`[Live2D EyeBlink] 无法为模型 ${this.modelName || this.modelRootPath} 找到任何眨眼参数，眨眼功能不可用`);
            return;
        }
    } else {
        console.log(`[Live2D EyeBlink] 检测通过: EyeBlink Ids =`, eyeBlinkIds);
    }

    const coreModel = model?.internalModel?.coreModel;
    if (!coreModel) {
        console.warn(`[Live2D EyeBlink] coreModel 不可用，眨眼功能不可用`);
        return;
    }

    const resolveEyeBlinkParam = (id) => {
        try {
            const idx = coreModel.getParameterIndex(id);
            return idx >= 0 ? this._createEyeBlinkParamConfig(coreModel, id, idx) : null;
        } catch (_) {
            return null;
        }
    };
    const dedupeEyeBlinkParams = (params) => {
        const seenIndexes = new Set();
        return params.filter(param => {
            if (!param || seenIndexes.has(param.idx)) return false;
            seenIndexes.add(param.idx);
            return true;
        });
    };

    let resolvedEyeBlinkParams = dedupeEyeBlinkParams(eyeBlinkIds.map(resolveEyeBlinkParam).filter(Boolean));

    if (resolvedEyeBlinkParams.length < eyeBlinkIds.length && !usedFallbackScan) {
        const scanned = this._scanEyeBlinkParams(model);
        if (scanned && scanned.length > 0) {
            usedFallbackScan = true;
            console.warn(`[Live2D EyeBlink] EyeBlink 组参数索引不完整，合并 fallback-scan:`, scanned);
            resolvedEyeBlinkParams = dedupeEyeBlinkParams([
                ...resolvedEyeBlinkParams,
                ...scanned.map(resolveEyeBlinkParam).filter(Boolean)
            ]);
            eyeBlinkIds = resolvedEyeBlinkParams.map(param => param.id);
        }
    }

    this._eyeBlinkParams = resolvedEyeBlinkParams;

    if (this._eyeBlinkParams.length === 0) {
        console.warn(`[Live2D EyeBlink] EyeBlink 参数索引全部无效，眨眼功能不可用`);
        this._autoEyeBlinkEnabled = false;
        return;
    }

    this._autoEyeBlinkEnabled = true;
    const mode = usedFallbackScan
        ? 'fallback-scan'
        : (this._eyeBlinkParams.some(p => p.isInverted) ? 'inverted' : 'standard');
    console.log(`[Live2D EyeBlink] enabled mode=${mode}`);
    this._eyeBlinkParams.forEach(p => {
        console.log(`[Live2D EyeBlink] param id=${p.id} index=${p.idx} open=${p.openValue} closed=${p.closedValue} default=${p.defaultValue} current=${p.currentValue}`);
    });
};

Live2DManager.prototype._readParameterValueByIndex = function(coreModel, idx, methodNames, arrayNames, fallbackValue) {
    for (const methodName of methodNames) {
        try {
            if (typeof coreModel?.[methodName] === 'function') {
                const value = Number(coreModel[methodName](idx));
                if (Number.isFinite(value)) return value;
            }
        } catch (_) {}
    }

    const roots = [
        coreModel,
        coreModel?.parameters,
        coreModel?._model,
        coreModel?._model?.parameters,
        coreModel?.model,
        coreModel?.model?.parameters
    ];
    for (const root of roots) {
        if (!root) continue;
        for (const arrayName of arrayNames) {
            try {
                const values = root[arrayName];
                if (values && values[idx] !== undefined) {
                    const value = Number(values[idx]);
                    if (Number.isFinite(value)) return value;
                }
            } catch (_) {}
        }
    }

    return fallbackValue;
};

Live2DManager.prototype._createEyeBlinkParamConfig = function(coreModel, id, idx) {
    let minValue = this._readParameterValueByIndex(
        coreModel,
        idx,
        ['getParameterMinimumValueByIndex', 'getParameterMinimumValue', 'getParamMin'],
        ['minimumValues', 'minimums', 'minValues', '_parameterMinimumValues'],
        0
    );
    let maxValue = this._readParameterValueByIndex(
        coreModel,
        idx,
        ['getParameterMaximumValueByIndex', 'getParameterMaximumValue', 'getParamMax'],
        ['maximumValues', 'maximums', 'maxValues', '_parameterMaximumValues'],
        1
    );
    if (minValue > maxValue) {
        const tmp = minValue;
        minValue = maxValue;
        maxValue = tmp;
    }

    const defaultValue = this._readParameterValueByIndex(
        coreModel,
        idx,
        ['getParameterDefaultValueByIndex', 'getParameterDefaultValue', 'getParamDefault'],
        ['defaultValues', 'defaults', '_parameterDefaultValues'],
        NaN
    );
    let currentValue = NaN;
    try {
        currentValue = Number(coreModel.getParameterValueByIndex(idx));
    } catch (_) {}

    const clamp = (value) => Math.min(maxValue, Math.max(minValue, value));
    const midpoint = minValue + (maxValue - minValue) / 2;
    let openValue = Number.isFinite(defaultValue)
        ? defaultValue
        : (Number.isFinite(currentValue) ? currentValue : maxValue);
    openValue = clamp(openValue);

    const closedValue = openValue < midpoint ? maxValue : minValue;
    return {
        id,
        idx,
        minValue,
        maxValue,
        defaultValue: Number.isFinite(defaultValue) ? defaultValue : null,
        currentValue: Number.isFinite(currentValue) ? currentValue : null,
        openValue,
        closedValue,
        isInverted: openValue < closedValue
    };
};

Live2DManager.prototype._forceEyeBlinkOpen = function(coreModel) {
    if (!coreModel || !this._eyeBlinkParams || this._eyeBlinkParams.length === 0) return;
    for (const param of this._eyeBlinkParams) {
        try {
            coreModel.setParameterValueByIndex(param.idx, param.openValue);
        } catch (_) {}
    }
};

Live2DManager.prototype._isEyeBlinkParamId = function(paramId) {
    if (!paramId) return false;
    const id = String(paramId);
    if (this._eyeBlinkParams && this._eyeBlinkParams.some(param => (
        param.id === id || `param_${param.idx}` === id
    ))) {
        return true;
    }
    return this._looksLikeEyeBlinkParamId(id);
};

Live2DManager.prototype._looksLikeEyeBlinkParamId = function(paramId) {
    if (!paramId) return false;
    const id = String(paramId);
    const nonBlinkPatterns = [/mouth/i, /eyeball/i, /angle/i, /look/i, /iris/i, /pupil/i];
    if (nonBlinkPatterns.some(pattern => pattern.test(id))) return false;

    const blinkPatterns = [
        /^parameye[lr]open$/i,
        /^param(?:left|right)?eye(?:l|r)?open$/i,
        /^param.*eye.*open$/i,
        /eye.*blink/i,
        /blink.*eye/i,
        /eyeblink/i,
        /eye.*wink/i,
        /wink.*eye/i,
        /ウィンク|ｳｨﾝｸ/i,
        /まばたき|瞬き|目.*開|眼.*開/i
    ];
    return blinkPatterns.some(pattern => pattern.test(id));
};

// 自动扫描模型参数以识别眨眼相关参数
Live2DManager.prototype._scanEyeBlinkParams = function(model) {
    if (!model?.internalModel?.coreModel) return null;
    const coreModel = model.internalModel.coreModel;
    const count = coreModel.getParameterCount();
    const found = [];

    // Cubism 4/5 的 coreModel 没有 getParameterId(index)，参数 ID 列表存在
    // coreModel._parameterIds[i] 或更底层的 coreModel._model.parameters.ids[i]。
    // 旧 Cubism 2 的 coreModel 才有 getParameterId(index)，作为兜底。
    for (let i = 0; i < count; i++) {
        let paramId = null;
        try {
            paramId = coreModel._parameterIds?.[i]
                ?? coreModel._model?.parameters?.ids?.[i]
                ?? (typeof coreModel.getParameterId === 'function' ? coreModel.getParameterId(i) : null);
        } catch (_) {}
        if (!paramId) continue;
        if (this._looksLikeEyeBlinkParamId(paramId)) {
            found.push(paramId);
        }
    }

    const eyeL = found.find(id => /eye.?l/i.test(id) || /left/i.test(id) || /l$/i.test(id));
    const eyeR = found.find(id => /eye.?r/i.test(id) || /right/i.test(id) || /r$/i.test(id));
    if (eyeL && eyeR) return [eyeL, eyeR];

    return found.slice(0, 2);
};

// 更新自动眨眼状态（眨眼动画逻辑）
Live2DManager.prototype._updateEyeBlink = function(delta) {
    if (this._suspendEyeBlinkOverride) return;
    if (!this._autoEyeBlinkEnabled || !this._eyeBlinkParams || this._eyeBlinkParams.length === 0) return;
    const coreModel = this.currentModel?.internalModel?.coreModel;
    if (!coreModel) return;
    const params = this._eyeBlinkParams;
    const setEyeBlinkValue = (resolver) => {
        for (const param of params) {
            try {
                coreModel.setParameterValueByIndex(param.idx, resolver(param));
            } catch (_) {}
        }
    };

    this._eyeBlinkTimer += delta;
    if (this._eyeBlinkState === 0) {
        setEyeBlinkValue(param => param.openValue);
        if (this._eyeBlinkTimer >= this._eyeBlinkNextTime) {
            this._eyeBlinkState = 1;
            this._eyeBlinkTimer = 0;
        }
    } else if (this._eyeBlinkState === 1) {
        const CLOSE_DURATION = 0.08;
        let t = Math.min(this._eyeBlinkTimer / CLOSE_DURATION, 1);
        let p = Easing.easeInCubic(t);
        setEyeBlinkValue(param => param.openValue + (param.closedValue - param.openValue) * p);
        if (this._eyeBlinkTimer >= CLOSE_DURATION) {
            this._eyeBlinkState = 2;
            this._eyeBlinkTimer = 0;
        }
    } else if (this._eyeBlinkState === 2) {
        const OPEN_DURATION = 0.15;
        let t = Math.min(this._eyeBlinkTimer / OPEN_DURATION, 1);
        let p = Easing.easeOutCubic(t);
        setEyeBlinkValue(param => param.closedValue + (param.openValue - param.closedValue) * p);
        if (this._eyeBlinkTimer >= OPEN_DURATION) {
            this._eyeBlinkState = 0;
            this._eyeBlinkTimer = 0;
            this._eyeBlinkNextTime = 2 + Math.random() * 4;
        }
    }
};

// 更新随机视线朝向（视线微动）
Live2DManager.prototype._updateRandomLookAt = function(delta) {
    const coreModel = this.currentModel?.internalModel?.coreModel;
    if (!coreModel) return;
    if (window.nekoYuiGuideFaceForwardLock === true && window.nekoYuiGuideIntroVoiceLookAtActive !== true) {
        if (this._lookAtTargetX === undefined || !Number.isFinite(this._lookAtTargetX)) {
            this._lookAtTargetX = 0;
        }
        if (this._lookAtTargetY === undefined || !Number.isFinite(this._lookAtTargetY)) {
            this._lookAtTargetY = 0;
        }
        if (this._lookAtCurrentX === undefined || !Number.isFinite(this._lookAtCurrentX)) {
            this._lookAtCurrentX = 0;
        }
        if (this._lookAtCurrentY === undefined || !Number.isFinite(this._lookAtCurrentY)) {
            this._lookAtCurrentY = 0;
        }
        const settleFactor = Math.min(1, Math.max(0.08, 4.8 * Math.max(0, Number(delta) || 0)));
        this._lookAtTargetX += (0 - this._lookAtTargetX) * settleFactor;
        this._lookAtTargetY += (0 - this._lookAtTargetY) * settleFactor;
        this._lookAtCurrentX += (this._lookAtTargetX - this._lookAtCurrentX) * settleFactor;
        this._lookAtCurrentY += (this._lookAtTargetY - this._lookAtCurrentY) * settleFactor;
        const isCentered = Math.abs(this._lookAtTargetX) < 0.01
            && Math.abs(this._lookAtTargetY) < 0.01
            && Math.abs(this._lookAtCurrentX) < 0.01
            && Math.abs(this._lookAtCurrentY) < 0.01;
        const isParamCentered = (paramId, threshold) => {
            try {
                const idx = coreModel.getParameterIndex(paramId);
                return idx < 0 || Math.abs(coreModel.getParameterValueByIndex(idx)) < threshold;
            } catch (_) {
                return true;
            }
        };
        const areCoreLookAtParamsCentered = isParamCentered('ParamAngleX', 0.01)
            && isParamCentered('ParamAngleY', 0.01)
            && isParamCentered('ParamEyeBallX', 0.001)
            && isParamCentered('ParamEyeBallY', 0.001);
        if (window.nekoYuiGuideFaceForwardSuppressParamWrite === true && isCentered && areCoreLookAtParamsCentered) {
            return;
        }
        try {
            coreModel.setParameterValueById('ParamAngleX', this._lookAtCurrentX);
            coreModel.setParameterValueById('ParamAngleY', this._lookAtCurrentY);
            coreModel.setParameterValueById('ParamEyeBallX', this._lookAtCurrentX / 30);
            coreModel.setParameterValueById('ParamEyeBallY', this._lookAtCurrentY / 30);
        } catch (_) {}
        return;
    }
    if (this._mouseTrackingEnabled) return;
    if (this._lookAtTimer === undefined) {
        this._lookAtTimer = 0;
        this._lookAtNextTime = 1 + Math.random() * 4;
        this._lookAtTargetX = 0;
        this._lookAtTargetY = 0;
        this._lookAtCurrentX = 0;
        this._lookAtCurrentY = 0;
    }
    this._lookAtTimer += delta;
    if (this._lookAtTimer > this._lookAtNextTime) {
        this._lookAtTimer = 0;
        this._lookAtNextTime = 1 + Math.random() * 4;
        this._lookAtTargetX = (Math.random() * 2 - 1) * 15;
        this._lookAtTargetY = (Math.random() * 2 - 1) * 10;
    }
    const lerpFactor = 2.0 * delta;
    this._lookAtCurrentX += (this._lookAtTargetX - this._lookAtCurrentX) * lerpFactor;
    this._lookAtCurrentY += (this._lookAtTargetY - this._lookAtCurrentY) * lerpFactor;
    try {
        if (this._randomLookAtAffectsHead) {
            coreModel.setParameterValueById('ParamAngleX', this._lookAtCurrentX);
            coreModel.setParameterValueById('ParamAngleY', this._lookAtCurrentY);
        }
        coreModel.setParameterValueById('ParamEyeBallX', this._lookAtCurrentX / 30);
        coreModel.setParameterValueById('ParamEyeBallY', this._lookAtCurrentY / 30);
    } catch (_) {}
};

Live2DManager.prototype._resolveRuntimeBreathParams = function(coreModel) {
    if (!coreModel) return [];
    if (Array.isArray(this._runtimeBreathParamIds)) {
        return this._runtimeBreathParamIds;
    }

    const candidates = ['ParamBreath', 'ParamBreath2', 'ParamBreath3'];
    this._runtimeBreathParamIds = candidates.filter(id => {
        try {
            return coreModel.getParameterIndex(id) >= 0;
        } catch (_) {
            return false;
        }
    });
    return this._runtimeBreathParamIds;
};

Live2DManager.prototype._getNativeRuntimeBreathParamIds = function(internalModel, coreModel) {
    const nativeBreathParamIds = new Set();
    const breath = internalModel?.breath;
    if (!breath || typeof breath.updateParameters !== 'function') return nativeBreathParamIds;

    const runtimeBreathParamIds = new Set(this._resolveRuntimeBreathParams(coreModel));
    if (runtimeBreathParamIds.size === 0) return nativeBreathParamIds;

    let nativeParams = null;
    try {
        if (typeof breath.getParameters === 'function') {
            nativeParams = breath.getParameters();
        }
    } catch (_) {}
    if (!Array.isArray(nativeParams) && Array.isArray(breath._breathParameters)) {
        nativeParams = breath._breathParameters;
    }
    if (!Array.isArray(nativeParams) || nativeParams.length === 0) return nativeBreathParamIds;

    for (const param of nativeParams) {
        const id = param && (param.parameterId || param.id || param.Id);
        if (!runtimeBreathParamIds.has(id)) continue;
        const weight = Number(param.weight);
        const peak = Number(param.peak);
        const offset = Number(param.offset);
        if (Number.isFinite(weight) && weight === 0) continue;
        if (Number.isFinite(peak) && peak === 0 && Number.isFinite(offset) && offset === 0) continue;
        nativeBreathParamIds.add(id);
    }

    return nativeBreathParamIds;
};

Live2DManager.prototype._updateRuntimeBreath = function(delta, options = {}) {
    const coreModel = this.currentModel?.internalModel?.coreModel;
    if (!coreModel) return;

    const breathParamIds = this._resolveRuntimeBreathParams(coreModel);
    if (breathParamIds.length === 0) return;

    const excludedParamIds = options.excludedParamIds instanceof Set
        ? options.excludedParamIds
        : new Set(Array.isArray(options.excludedParamIds) ? options.excludedParamIds : []);
    const targetParamIds = breathParamIds.filter(id => !excludedParamIds.has(id));
    if (targetParamIds.length === 0) return;

    const safeDelta = Math.min(Math.max(Number(delta) || 0, 0), 0.1);
    this._runtimeBreathTime = (this._runtimeBreathTime || 0) + safeDelta;

    const phase = this._runtimeBreathTime * Math.PI * 2 / 3.8;
    const normalized = 0.5 + Math.sin(phase) * 0.5;

    for (const id of targetParamIds) {
        try {
            const idx = coreModel.getParameterIndex(id);
            if (idx < 0) continue;

            let min = 0;
            let max = 1;
            if (typeof coreModel.getParameterMinimumValueByIndex === 'function') {
                min = coreModel.getParameterMinimumValueByIndex(idx);
            } else if (coreModel.parameters?.minimumValues) {
                min = coreModel.parameters.minimumValues[idx];
            }
            if (typeof coreModel.getParameterMaximumValueByIndex === 'function') {
                max = coreModel.getParameterMaximumValueByIndex(idx);
            } else if (coreModel.parameters?.maximumValues) {
                max = coreModel.parameters.maximumValues[idx];
            }

            if (!Number.isFinite(min)) min = 0;
            if (!Number.isFinite(max) || max <= min) max = min + 1;

            coreModel.setParameterValueByIndex(idx, min + (max - min) * normalized);
        } catch (_) {}
    }
};

Live2DManager.prototype._clearIdleMotionLoopTimers = function() {
    if (!(this._idleMotionLoopTimers instanceof Set)) {
        this._idleMotionLoopTimers = new Set();
        return;
    }
    this._idleMotionLoopTimers.forEach(timer => clearTimeout(timer));
    this._idleMotionLoopTimers.clear();
};

Live2DManager.prototype.getAvatarPerformanceAvatarIds = function() {
    const ids = ['main-live2d'];
    if (this.avatarPerformanceAvatarId) ids.push(this.avatarPerformanceAvatarId);
    if (this.modelName) ids.push(this.modelName);
    return Array.from(new Set(ids.map(id => String(id || '').trim()).filter(Boolean)));
};

Live2DManager.prototype.isAvatarPerformanceCapabilityLocked = function(capability) {
    if (this._avatarPerformanceBypassLocks === true) return false;
    const api = window.AvatarPerformance;
    if (!api || typeof api.isCapabilityLocked !== 'function') return false;
    const ids = this.getAvatarPerformanceAvatarIds();
    return ids.some(id => {
        try {
            return api.isCapabilityLocked(id, capability);
        } catch (_) {
            return false;
        }
    });
};

Live2DManager.prototype.suspendTemporaryMotions = function(source, model) {
    const activeModel = model || this.currentModel || null;
    if (!activeModel || activeModel !== this.currentModel || !activeModel.internalModel) {
        return false;
    }

    const token = String(source || 'temporary_motion_suspend');
    this._temporaryMotionSuspendToken = token;
    this._clearIdleMotionLoopTimers();

    const motionManager = activeModel.internalModel.motionManager;
    if (motionManager && typeof motionManager.stopAllMotions === 'function') {
        try {
            motionManager.stopAllMotions();
        } catch (_) {}
    }

    return true;
};

Live2DManager.prototype.resumeTemporaryMotions = function(source) {
    const token = String(source || 'temporary_motion_suspend');
    if (this._temporaryMotionSuspendToken && this._temporaryMotionSuspendToken !== token) {
        return false;
    }

    this._temporaryMotionSuspendToken = null;
    if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.motionManager) {
        this.setupIdleMotionLoop(this.currentModel);
    }
    return true;
};

// 设置待机动作循环（Idle Motion 无限循环）
Live2DManager.prototype.setupIdleMotionLoop = function(model) {
    if (!model || !model.internalModel || !model.internalModel.motionManager) return;
    const motionManager = model.internalModel.motionManager;
    if (this._temporaryMotionSuspendToken) return;

    // 初始化定时器集合，并在重新设置时清理旧定时器
    this._clearIdleMotionLoopTimers();

    // 生成一个调度器，自带当前模型有效性校验与定时器回收
    const scheduleIdleMotion = (delay, options = {}) => {
        if (options.replace === true) {
            this._clearIdleMotionLoopTimers();
        }
        const timer = setTimeout(() => {
            this._idleMotionLoopTimers.delete(timer);
            // 如果模型已销毁，或当前挂载的模型已经不是传入的这个模型，则直接取消
            if (this.currentModel !== model || model.destroyed || window._currentMotionPreviewId != null || this._temporaryMotionSuspendToken) {
                return;
            }
            if (this.isAvatarPerformanceCapabilityLocked('motion')) {
                scheduleIdleMotion(1000, { replace: true });
                return;
            }
            if (!motionManager.playing) {
                Promise.resolve(this._playIdleMotion(motionManager)).catch((e) => {
                    console.warn('[Live2D] 播放 Idle motion 失败:', e);
                });
            }
        }, delay);
        this._idleMotionLoopTimers.add(timer);
    };

    try {
        if (
            this._idleMotionFinishHandler
            && this._idleMotionFinishModel
            && this._idleMotionFinishModel.internalModel
            && this._idleMotionFinishModel.internalModel.events
            && typeof this._idleMotionFinishModel.internalModel.events.removeListener === 'function'
        ) {
            this._idleMotionFinishModel.internalModel.events.removeListener('motionFinish', this._idleMotionFinishHandler);
        }
    } catch (_) {}

    this._idleMotionFinishModel = model;
    this._idleMotionFinishHandler = () => {
        if (this._temporaryMotionSuspendToken) {
            return;
        }
        if (this.isAvatarPerformanceCapabilityLocked('motion')) {
            scheduleIdleMotion(1000, { replace: true });
            return;
        }
        if (window._currentMotionPreviewId != null) {
            console.log('[Live2D] 预览模式中，忽略 motionFinish，不启动新 Idle');
            return;
        }
        if (typeof this.clearEmotionEffects === 'function') {
            try {
                this.clearEmotionEffects();
            } catch (e) {
                console.warn('[Live2D] motionFinish 后清理 motion 参数失败:', e);
            }
        }
        const randomDelay = 1000 + Math.random() * 2000;
        scheduleIdleMotion(randomDelay);
    };
    model.internalModel.events.on('motionFinish', this._idleMotionFinishHandler);

    scheduleIdleMotion(2000);
};

// 播放待机动作（从用户保存的 Idle 动画或默认 Idle 随机选择）
Live2DManager.prototype._playIdleMotion = async function(motionManager) {
    if (this._temporaryMotionSuspendToken || this.isAvatarPerformanceCapabilityLocked('motion')) {
        return;
    }
    const expectedModel = this.currentModel;
    const isCurrentIdleRequest = () => (
        this.currentModel === expectedModel
        && expectedModel
        && !expectedModel.destroyed
        && window._currentMotionPreviewId == null
        && !this._temporaryMotionSuspendToken
        && !this.isAvatarPerformanceCapabilityLocked('motion')
    );
    const getRandomizedIndexes = (length) => {
        const indexes = [];
        for (let i = 0; i < length; i++) indexes.push(i);
        for (let i = indexes.length - 1; i > 0; i--) {
            const j = Math.floor(Math.random() * (i + 1));
            [indexes[i], indexes[j]] = [indexes[j], indexes[i]];
        }
        return indexes;
    };
    const getMotionFile = (motion) => motion && (motion.file || motion.File);
    const getRegisteredMotionFile = (groupName, index) => {
        if (!groupName || index == null || index < 0) return null;

        const sources = [
            motionManager.definitions,
            motionManager._definitions,
            motionManager.motionGroups,
            motionManager._motionGroups
        ].filter(Boolean);

        for (const source of sources) {
            const group = source[groupName];
            if (!Array.isArray(group)) continue;
            const file = getMotionFile(group[index]);
            if (file) return file;
        }

        return null;
    };
    const trackMotionFile = (file) => {
        if (!file || typeof this._trackActiveMotionParametersFromFile !== 'function') {
            if (typeof this._clearActiveMotionParamIds === 'function') {
                this._clearActiveMotionParamIds();
            }
            return;
        }
        this._trackActiveMotionParametersFromFile(file).catch(() => {});
    };
    const startTrackedMotion = async (groupName, index, file) => {
        if (!isCurrentIdleRequest()) return false;
        try {
            const started = await motionManager.startMotion(groupName, index);
            if (!isCurrentIdleRequest()) return false;
            if (started === false) {
                console.warn(`[Live2D] 启动 ${groupName} 待机动作失败，尝试下一个 Idle 候选`);
                return false;
            }
            trackMotionFile(file);
            return true;
        } catch (e) {
            console.warn(`[Live2D] 启动 ${groupName} 待机动作失败，尝试下一个 Idle 候选:`, e);
            return false;
        }
    };
    const startTrackedDefinitionMotion = async (groupName, definitions) => {
        if (!Array.isArray(definitions) || definitions.length === 0) return false;
        for (const idx of getRandomizedIndexes(definitions.length)) {
            const definition = definitions[idx];
            const file = definition && (definition.File || definition.file);
            if (await startTrackedMotion(groupName, idx, file)) return true;
        }
        return false;
    };
    const startTrackedGroupMotion = async (groupName, group, filter) => {
        if (!Array.isArray(group) || group.length === 0) return false;
        const indexes = getRandomizedIndexes(group.length);
        for (const idx of indexes) {
            const motion = group[idx];
            if (filter && !filter(motion)) continue;
            const file = getMotionFile(motion);
            if (await startTrackedMotion(groupName, idx, file)) return true;
        }
        return false;
    };
    const idleAnimations = this._userIdleAnimations;
    if (idleAnimations && idleAnimations.length > 0) {
        // 兼容性获取 motionGroups，优先取公开属性，fallback 到私有属性
        const motionGroups = motionManager.motionGroups || motionManager._motionGroups;

        if (!motionGroups || !motionGroups.PreviewAll) {
            console.warn('[Live2D] motionGroups 不可用或 PreviewAll 组不存在，跳过用户待机动作');
        } else {
            const group = motionGroups.PreviewAll;
            const startedUserIdle = await startTrackedGroupMotion('PreviewAll', group, (motion) => {
                const motionFile = getMotionFile(motion);
                return motionFile && idleAnimations.includes(motionFile.split('/').pop());
            });
            if (startedUserIdle) {
                return;
            }
            if (Array.isArray(group) && group.length > 0) {
                const available = group.filter((motion) => {
                    const motionFile = getMotionFile(motion);
                    return motionFile && idleAnimations.includes(motionFile.split('/').pop());
                });
                if (available.length > 0) {
                    console.warn('[Live2D] 用户保存的待机动作启动失败，回退到默认 Idle');
                }
            }
        }
    }
    const definitions = motionManager.definitions || motionManager._definitions || {};
    if (await startTrackedDefinitionMotion('Idle', definitions.Idle)) {
        return;
    }

    const motionGroups = motionManager.motionGroups || motionManager._motionGroups || {};
    if (await startTrackedGroupMotion('Idle', motionGroups.Idle)) {
        return;
    }

    if (!isCurrentIdleRequest()) return;
    try {
        const started = await motionManager.startRandomMotion('Idle');
        if (!isCurrentIdleRequest()) return;
        if (started === false) {
            this._clearActiveMotionParamIds();
            return;
        }
        const startedGroup = motionManager.state?.currentGroup || 'Idle';
        const startedIndex = motionManager.state?.currentIndex;
        const startedFile = getRegisteredMotionFile(startedGroup, startedIndex);
        trackMotionFile(startedFile);
    } catch (e) {
        this._clearActiveMotionParamIds();
        console.warn('[Live2D] 随机 Idle motion 启动失败:', e);
    }
};

// 配置已加载的模型（私有方法，用于消除主路径和回退路径的重复代码）
Live2DManager.prototype._configureLoadedModel = async function(model, modelPath, options, loadToken) {
    if (!this._isLoadTokenActive(loadToken)) return;
    this._modelLoadState = 'applying';
    this._parameterEditingMode = options.parameterEditingMode === true;

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
        this.modelRootPath = rootDir; // e.g. /static/yui-origin or /static/some/deeper/dir
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

    // 根据画质设置调整渲染分辨率，不改动 Live2D 图集贴图。
    if (typeof this.applyRenderQuality === 'function') {
        this.applyRenderQuality(window.renderQuality);
    }

    if (typeof this._optimizeLinuxX11RendererProfile === 'function') {
        this._optimizeLinuxX11RendererProfile(model);
    }

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
    
    // 注意：用户偏好参数会在模型目录参数加载完成后参与统一合并，
    // 优先级顺序为：用户偏好参数 > 模型目录参数（用户偏好是权威来源）。

    // 添加到舞台
    this.pixi_app.stage.addChild(model);

    // 设置交互性
    if (options.dragEnabled !== false) {
        this.setupDragAndDrop(model);
    }

    // 修复 HitAreas 配置：如果 Name 为空，自动设置为 Id
    if (model.internalModel && model.internalModel.settings && model.internalModel.settings.hitAreas) {
        
        const hitAreas_do = model.internalModel.hitAreas;
        const hitAreas_disk = model.internalModel.settings.hitAreas;
        let fixedCount = 0;
        
        hitAreas_disk.forEach(hitArea => {
            if (!hitArea.Name || hitArea.Name === '') {
                hitArea.Name = hitArea.Id;
                if (this._autoNamedHitAreaIds instanceof Set) {
                    this._autoNamedHitAreaIds.add(hitArea.Id);
                }
                fixedCount++;
            }
        });
        
        if (fixedCount > 0) {
            delete hitAreas_do[''];
            
            hitAreas_disk.forEach(hitArea => {
                const drawableIndex = model.internalModel.coreModel.getDrawableIndex(hitArea.Id);
                hitAreas_do[hitArea.Id] = {
                    id: hitArea.Id,
                    name: hitArea.Id,
                    index: drawableIndex
                };
            });
            
            console.log(`[HitArea] 已修复 ${fixedCount} 个 HitArea 的 Name 字段（原为空字符串）`);
        }
    }

    // // 设置 HitArea 交互（点击 HitArea 播放对应动画）
    // this.setupHitAreaInteraction(model);

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

    // 应用保存的全屏跟踪设置
    this.setFullscreenTrackingEnabled(window.live2dFullscreenTrackingEnabled === true);
    
    // 设置原来的锁按钮
    this.setupHTMLLockIcon(model);

    const settings = model.internalModel && model.internalModel.settings && model.internalModel.settings.json;
    this._validateEyeBlinkGroup(settings, model);
    this._forceEyeBlinkOpen(model.internalModel?.coreModel);
    try {
        this.installMouthOverride();
        console.log('[Live2D EyeBlink] first-frame override installed');
    } catch (e) {
        console.warn('[Live2D EyeBlink] first-frame override install failed; will retry after full init:', e);
    }
    if (settings) {
        try {
            await this._loadDisplayInfo(settings);
        } catch (_) {}
    } else {
        this._displayInfo = null;
    }

    // 加载 FileReferences 与 EmotionMapping
    if (options.loadEmotionMapping !== false) {
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
                    // 【重要修复】Fetch 回来后，必须检查 Token！如果用户在此期间切了模型，直接中断！
                    if (!this._isLoadTokenActive(loadToken)) return;

                    if (filesResp.ok) {
                        const filesData = await filesResp.json();
                        if (!this._isLoadTokenActive(loadToken)) return;

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

    const suppressPersistentExpressions = options.suppressPersistentExpressions === true;

    // 设置常驻表情
    if (suppressPersistentExpressions) {
        if (typeof this.teardownPersistentExpressions === 'function') {
            try {
                this.teardownPersistentExpressions();
            } catch (_) {
                this.persistentExpressionNames = [];
                this.persistentExpressionParamsByName = {};
                this._persistentParamsBackup = {};
            }
        } else {
            this.persistentExpressionNames = [];
            this.persistentExpressionParamsByName = {};
            this._persistentParamsBackup = {};
        }
    } else {
        try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
        await this.setupPersistentExpressions();
    }

    // 调用常驻表情应用完成的回调（事件驱动方式，替代不可靠的 setTimeout）
    if (options.onResidentExpressionApplied && typeof options.onResidentExpressionApplied === 'function') {
        try {
            options.onResidentExpressionApplied(model);
        } catch (callbackError) {
            console.warn('[Live2D Model] 常驻表情应用完成回调执行失败:', callbackError);
        }
    }
    
    if (this._parameterEditingMode) {
        this._clearIdleMotionLoopTimers();
        try {
            const motionManager = model.internalModel?.motionManager;
            if (motionManager && typeof motionManager.stopAllMotions === 'function') {
                motionManager.stopAllMotions();
            }
            const expressionManager = motionManager?.expressionManager;
            if (expressionManager && typeof expressionManager.stopAllExpressions === 'function') {
                expressionManager.stopAllExpressions();
            }
        } catch (error) {
            console.warn('[Live2D] 参数编辑模式停止 motion/expression 失败:', error);
        }
        this._activeExpressionParamIds = null;
        this._clearActiveMotionParamIds();
    }

    // 用户偏好是权威来源，模型目录 parameters.json 仅作为兼容镜像。
    let modelDirectoryParameters = {};
    if (this.modelName && model.internalModel && model.internalModel.coreModel) {
        try {
            modelDirectoryParameters = await this._loadModelDirectoryParameters(this.modelName);
        } catch (error) {
            console.error('加载模型参数失败:', error);
        }
    }
    // 无论目录参数加载成功或失败，都必须阻止过期加载继续写入当前模型。
    if (!this._isLoadTokenActive(loadToken)) return;

    const userPreferenceParameters = options.preferences && options.preferences.parameters;
    try {
        this._applyEffectiveModelParameters(model, modelDirectoryParameters, userPreferenceParameters);
    } catch (e) {
        console.error('应用有效模型参数或安装口型覆盖失败:', e);
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
    
    // 确保 PIXI ticker 正在运行（防止从VRM切换后卡住）
    // 无条件调用 start()，因为它是幂等的（如果已在运行则不会有影响）
    if (this.pixi_app && this.pixi_app.ticker) {
        this.pixi_app.ticker.start();
        console.log('[Live2D Model] Ticker 已确保启动');
    }

    // 检测是否有 Idle 情绪配置（兼容新旧两种格式）
    // - 新格式: EmotionMapping.motions['Idle'] / EmotionMapping.expressions['Idle']
    // - 旧格式: FileReferences.Motions['Idle'] / FileReferences.Expressions 中的 Idle 前缀
    // 注意：PreviewAll 只是用户上传动作的临时存放组，不具备 Idle 语义，不应作为待机动作判定依据
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
    // 在隐藏状态下先做一次边界校正，避免“先出现再瞬移”。
    // 启动恢复必须与拖拽结束使用同一可见像素阈值，避免允许的半出屏位置被更严格地拉回屏内。
    if (typeof this._checkSnapRequired === 'function') {
        try {
            const snapInfo = await this._checkSnapRequired(model);
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
    this._bubbleGeometryModelReadyAt = (typeof performance !== 'undefined' && typeof performance.now === 'function')
        ? performance.now()
        : Date.now();
    this._bubbleGeometryRefreshPass = 0;
    try {
        window.dispatchEvent(new CustomEvent('neko-live2d-model-ready', {
            detail: { modelPath }
        }));
    } catch (eventError) {
        console.warn('[Live2D Model] 模型 ready 事件派发失败:', eventError);
    }
    try {
        const readyModelPath = (modelPath && typeof modelPath === 'object' && typeof modelPath.url === 'string')
            ? modelPath.url
            : modelPath;
        window.dispatchEvent(new CustomEvent('live2d-model-ready', {
            detail: { modelPath: readyModelPath }
        }));
    } catch (_) {}

    const suppressInitialIdle = options.suppressInitialIdle === true || this._parameterEditingMode;

    // 模型完全可见后播放 Idle 情绪（替代原来的独立 setTimeout）
    if (!suppressInitialIdle && (hasIdleInEmotionMapping || hasIdleInFileReferences)) {
        try {
            console.log('[Live2D Model] 模型淡入完成，开始播放Idle情绪');
            this.setEmotion('Idle').catch(error => {
                console.warn('[Live2D Model] 播放Idle情绪失败:', error);
            });
        } catch (error) {
            console.warn('[Live2D Model] 播放Idle情绪失败:', error);
        }
    }

    // 启动待机动作无限循环
    if (!suppressInitialIdle) {
        try {
            this.setupIdleMotionLoop(model);
        } catch (_) {}
    }

    // 调用回调函数
    if (this.onModelLoaded) {
        this.onModelLoaded(model, modelPath);
    }

    // 调用模型就绪回调（用于恢复待机动作等）
    if (options.onModelReady && typeof options.onModelReady === 'function') {
        try {
            options.onModelReady(model);
        } catch (callbackError) {
            console.warn('[Live2D Model] 模型就绪回调执行失败:', callbackError);
        }
    }
};



// Live2D 图集不能安全地在运行时替换 source 或缩小 BaseTexture。
// 旧实现会导致 UV、裁剪蒙版与贴图实际尺寸不同步，进而出现部件丢失。
Live2DManager.prototype._applyTextureQuality = function (model) {
    void model;
};

// 延迟重新安装覆盖的默认超时时间（毫秒）
const REINSTALL_OVERRIDE_DELAY_MS = 100;
// 最大重装尝试次数
const MAX_REINSTALL_ATTEMPTS = 3;

// 延迟重新安装覆盖（当 update 调用失败时自动重试）
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

    // 【新增】快照记录当前的 coreModel，防止定时器触发时模型已被切换
    const snapshotCoreModel = (this.currentModel && this.currentModel.internalModel) ?
                               this.currentModel.internalModel.coreModel : null;

    this._reinstallTimer = setTimeout(() => {
        this._reinstallScheduled = false;
        this._reinstallTimer = null;
        this._reinstallAttempts++;

        if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.coreModel) {
            // 【重要修复】对比 coreModel。如果已经不是同一个模型，说明切模型了，直接放弃重装
            if (this.currentModel.internalModel.coreModel !== snapshotCoreModel) {
                console.warn('[Live2D] 模型已切换，废弃旧的口型重装任务');
                return;
            }
            try {
                this.installMouthOverride();
            } catch (reinstallError) {
                console.warn('延迟重新安装覆盖失败:', reinstallError);
            }
        }
    }, REINSTALL_OVERRIDE_DELAY_MS);
};

// 安装口型同步覆盖（拦截 motionManager.update 和 coreModel.update 以实现口型、眨眼、视线微动）
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
    const getCurrentPersistentParamIds = () => {
        try {
            if (typeof this.getPersistentExpressionParamIds === 'function') {
                return this.getPersistentExpressionParamIds();
            }
        } catch (_) {}
        return new Set();
    };
    const resolveSavedParamEntry = ([paramId, value]) => {
        if (typeof value !== 'number' || !Number.isFinite(value)) return null;
        let idx = -1;
        let resolvedId = paramId;
        try {
            if (/^(?:param_)?\d+$/.test(paramId)) {
                const parsedIndex = parseInt(paramId.replace(/^param_/, ''), 10);
                const parameterCount = typeof coreModel.getParameterCount === 'function'
                    ? coreModel.getParameterCount()
                    : Number.POSITIVE_INFINITY;
                if (parsedIndex >= 0 && parsedIndex < parameterCount) {
                    idx = parsedIndex;
                    if (typeof coreModel.getParameterId === 'function') {
                        const id = coreModel.getParameterId(idx);
                        if (id) resolvedId = id;
                    }
                }
            } else {
                idx = coreModel.getParameterIndex(paramId);
            }
        } catch (_) {
            return null;
        }
        return idx >= 0 ? { id: paramId, resolvedId, idx, value } : null;
    };
    const runtimeBreathParams = this._resolveRuntimeBreathParams(coreModel);
    const runtimeBreathParamIds = new Set(runtimeBreathParams);
    const isRuntimeBreathParamId = (id) => runtimeBreathParamIds.has(id);
    const isRuntimeManagedSavedParam = (entry) => {
        if (!entry) return true;
        const ids = [entry.id, entry.resolvedId].filter(Boolean);
        return ids.some(id => this._isEyeBlinkParamId(id)) ||
            ids.some(id => lipSyncParams.includes(id)) ||
            ids.some(id => visibilityParams.includes(id)) ||
            ids.some(isRuntimeBreathParamId);
    };
    const isPersistentSavedParam = (entry, persistentIds) => {
        if (!entry || !persistentIds) return false;
        return persistentIds.has(entry.id) ||
            (entry.resolvedId && persistentIds.has(entry.resolvedId));
    };
    const savedParamEntries = (this.savedModelParameters && this._shouldApplySavedParams)
        ? Object.entries(this.savedModelParameters)
            .map(resolveSavedParamEntry)
            .filter(entry => entry && !isRuntimeManagedSavedParam(entry))
        : [];
    const shouldApplyPersistentExpressions = this._parameterEditingMode !== true;
    const lookAtParamIndices = {};
    for (const id of ['ParamAngleX', 'ParamAngleY', 'ParamEyeBallX', 'ParamEyeBallY']) {
        try {
            const idx = coreModel.getParameterIndex(id);
            if (idx >= 0) lookAtParamIndices[id] = idx;
        } catch (_) {}
    }

    // 每帧遍历用的 [id, idx] 快照：两个索引表都是上面一次性填充、之后只读的
    // const 闭包变量，在这里提升 Object.entries 结果，避免包装后的 update
    // 每帧（30-60fps）重复分配 5-6 个 entries 数组带来的 GC 压力。
    const mouthParamEntries = Object.entries(mouthParamIndices);
    const lookAtParamEntries = Object.entries(lookAtParamIndices);
    // 呼吸参数索引同理：参数索引对模型实例固定，安装期解析一次，
    // 免去每帧对 Cubism core 的 getParameterIndex 字符串查询。
    const breathParamEntries = [];
    for (const id of runtimeBreathParams) {
        try {
            const idx = coreModel.getParameterIndex(id);
            if (idx >= 0) breathParamEntries.push([id, idx]);
        } catch (_) {}
    }

    // 覆盖 1: motionManager.update
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
            // 1a. 保存参数 + 口型 + 眨眼参数快照（用于后续 Diff 检测）
            const preUpdateParams = {};
            const currentPersistentParamIds = savedParamEntries.length > 0 ? getCurrentPersistentParamIds() : null;
            if (savedParamEntries.length > 0) {
                for (const entry of savedParamEntries) {
                    if (isPersistentSavedParam(entry, currentPersistentParamIds)) continue;
                    try {
                        preUpdateParams[entry.id] = coreModel.getParameterValueByIndex(entry.idx);
                    } catch (_) {}
                }
            }
            for (const [id, idx] of mouthParamEntries) {
                try {
                    preUpdateParams[id] = coreModel.getParameterValueByIndex(idx);
                } catch (_) {}
            }
            if (this._autoEyeBlinkEnabled && this._eyeBlinkParams) {
                for (const p of this._eyeBlinkParams) {
                    try { preUpdateParams[p.id] = coreModel.getParameterValueByIndex(p.idx); } catch (_) {}
                }
            }
            for (const [id, idx] of breathParamEntries) {
                try {
                    preUpdateParams[id] = coreModel.getParameterValueByIndex(idx);
                } catch (_) {}
            }
            for (const [id, idx] of lookAtParamEntries) {
                try {
                    preUpdateParams[id] = coreModel.getParameterValueByIndex(idx);
                } catch (_) {}
            }
            
            // 先调用原始的 motionManager.update（添加错误处理）
            if (!this._temporaryMotionSuspendToken && !this.isAvatarPerformanceCapabilityLocked('motion') && origMotionManagerUpdate) {
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

            // 2. 执行后 Diff 检测：判断 Motion 是否接管了口型/眨眼/视线
            this._isMouthDrivenByMotion = false;
            this._isEyeDrivenByMotion = false;
            this._isLookAtDrivenByMotion = false;
            this._isBreathDrivenByMotion = false;
            const motionState = internalModel.motionManager?.state;
            const motionPriority = Number(motionState?.currentPriority ?? 0);
            const activeMotionKey = motionPriority > LIVE2D_MOTION_PRIORITY.NONE
                ? [motionPriority, motionState?.currentGroup ?? '', motionState?.currentIndex ?? ''].join(':')
                : null;
            if (this._motionDrivenBreathMotionKey !== activeMotionKey) {
                this._motionDrivenBreathMotionKey = activeMotionKey;
                this._motionDrivenBreathParamIds = new Set();
            } else if (!(this._motionDrivenBreathParamIds instanceof Set)) {
                this._motionDrivenBreathParamIds = new Set();
            }
            const shouldTreatEyeChangesAsAuthoritative = motionPriority > LIVE2D_MOTION_PRIORITY.IDLE;
            for (const [id, idx] of mouthParamEntries) {
                try {
                    const postVal = coreModel.getParameterValueByIndex(idx);
                    const preVal = preUpdateParams[id];
                    if (preVal !== undefined && Math.abs(postVal - preVal) > 0.001) {
                        this._isMouthDrivenByMotion = true;
                        break;
                    }
                } catch (_) {}
            }
            if (this._autoEyeBlinkEnabled && this._eyeBlinkParams) {
                for (const p of this._eyeBlinkParams) {
                    try {
                        const postVal = coreModel.getParameterValueByIndex(p.idx);
                        const preVal = preUpdateParams[p.id];
                        if (preVal !== undefined && Math.abs(postVal - preVal) > 0.001) {
                            this._isEyeDrivenByMotion = shouldTreatEyeChangesAsAuthoritative;
                            break;
                        }
                    } catch (_) {}
                }
            }
            for (const [id, idx] of breathParamEntries) {
                try {
                    const postVal = coreModel.getParameterValueByIndex(idx);
                    const preVal = preUpdateParams[id];
                    if (preVal !== undefined && Math.abs(postVal - preVal) > 0.001) {
                        this._motionDrivenBreathParamIds.add(id);
                    }
                } catch (_) {}
            }
            this._isBreathDrivenByMotion = this._motionDrivenBreathParamIds.size > 0;
            for (const [id, idx] of lookAtParamEntries) {
                try {
                    const postVal = coreModel.getParameterValueByIndex(idx);
                    const preVal = preUpdateParams[id];
                    if (preVal !== undefined && Math.abs(postVal - preVal) > 0.001) {
                        this._isLookAtDrivenByMotion = true;
                        break;
                    }
                } catch (_) {}
            }

            // === 注入点 1（物理引擎前）：视线微动与运行时呼吸 ===
            // 仅当 Motion 未接管时注入，让物理引擎能看到这些变化。
            // 呼吸 fallback 只在 SDK 原生 Breath 未覆盖同名参数时启用，避免双重叠加。
            if (!this._isLookAtDrivenByMotion && !this._mouseTrackingEnabled && !this.isAvatarPerformanceCapabilityLocked('lookAt')) {
                const delta = (this.currentModel?.deltaTime || 16.66) / 1000;
                this._updateRandomLookAt(delta);
            }
            {
                const excludedBreathParamIds = this._getNativeRuntimeBreathParamIds(internalModel, coreModel);
                if (this._motionDrivenBreathParamIds instanceof Set) {
                    for (const id of this._motionDrivenBreathParamIds) {
                        excludedBreathParamIds.add(id);
                    }
                }
                const delta = (this.currentModel?.deltaTime || 16.66) / 1000;
                this._updateRuntimeBreath(delta, { excludedParamIds: excludedBreathParamIds });
            }

            try {
                // === 点击效果平滑过渡处理 ===
                // 当 _clickFadeState 存在时，说明点击效果正在平滑恢复中
                // 此时跳过 savedModelParameters 和 persistentExpression 的强制写入
                // 改为执行插值过渡
                const fadeState = this._clickFadeState;
                if (fadeState) {
                    const now = performance.now();
                    const elapsed = now - fadeState.startTime;
                    const safeDuration = (Number.isFinite(fadeState.duration) && fadeState.duration > 0) ? fadeState.duration : 1;
                    const linearProgress = Math.min(Math.max(elapsed / safeDuration, 0), 1);
                    const t = 1 - Math.pow(1 - linearProgress, 3);

                    for (const [paramId, target] of Object.entries(fadeState.targetValues)) {
                        const start = fadeState.startValues[paramId];
                        if (start === undefined) continue;
                        try {
                            const interpolated = start + (target - start) * t;
                            coreModel.setParameterValueById(paramId, interpolated);
                        } catch (_) {}
                    }

                    // 口型参数：lipsync 在响（mouthValue > 0）时强制覆盖 motion；静默时让位给 motion 自带的嘴部动画
                    if (!this._isMouthDrivenByMotion || this.mouthValue > LIPSYNC_OVERRIDE_THRESHOLD) {
                        for (const [, idx] of mouthParamEntries) {
                            try {
                                coreModel.setParameterValueByIndex(idx, this.mouthValue);
                            } catch (_) {}
                        }
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
                // 注意：口型、眨眼在 coreModel.update 拦截器（注入点2）中写入
                // 注意：呼吸、视线微动在 motionManager.update 拦截器（注入点1）中已写入
                // 1. 应用保存的模型参数（智能叠加模式）
                if (savedParamEntries.length > 0) {
                    for (const entry of savedParamEntries) {
                        if (isPersistentSavedParam(entry, currentPersistentParamIds)) continue;
                        try {
                            const currentVal = coreModel.getParameterValueByIndex(entry.idx);
                            const preVal = preUpdateParams[entry.id] !== undefined ? preUpdateParams[entry.id] : currentVal;
                            const defaultVal = coreModel.getParameterDefaultValueByIndex(entry.idx);
                            const offset = entry.value - defaultVal;

                            if (Math.abs(currentVal - preVal) > 0.001) {
                                coreModel.setParameterValueByIndex(entry.idx, currentVal + offset);
                            } else {
                                coreModel.setParameterValueByIndex(entry.idx, entry.value);
                            }
                        } catch (_) {}
                    }
                }
                // 2. 写入常驻表情参数（覆盖模式，优先级最高）
                if (shouldApplyPersistentExpressions && this.persistentExpressionParamsByName) {
                    for (const name in this.persistentExpressionParamsByName) {
                        const params = this.persistentExpressionParamsByName[name];
                        if (Array.isArray(params)) {
                            for (const p of params) {
                                if (lipSyncParams.includes(p.Id)) continue;
                                if (this._isEyeBlinkParamId(p.Id)) continue;
                                if (isRuntimeBreathParamId(p.Id)) continue;
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
            // === 注入点 2（渲染前）：口型 + 眨眼 ===
            // 这是渲染前的最后一步，强制命令，绝对优先级
            // 口型参数：lipsync 在响（mouthValue > 0）时强制覆盖 motion；静默时让位给 motion 自带的嘴部动画
            if (!this._isMouthDrivenByMotion || this.mouthValue > LIPSYNC_OVERRIDE_THRESHOLD) {
                for (const [, idx] of mouthParamEntries) {
                    try {
                        currentCoreModel.setParameterValueByIndex(idx, this.mouthValue);
                    } catch (_) {}
                }
            }
            // 眨眼更新（仅当 Motion 未接管且未暂停时）
            if (this._autoEyeBlinkEnabled
                && !this._suspendEyeBlinkOverride
                && !this._isEyeDrivenByMotion) {
                const delta = (this.currentModel?.deltaTime || 16.66) / 1000;
                this._updateEyeBlink(delta);
            }

            // 2. 写入常驻表情参数（跳过口型参数以避免覆盖lipsync）
            // 当点击效果正在淡入淡出时，跳过常驻表情写入以避免覆盖插值
            if (shouldApplyPersistentExpressions && this.persistentExpressionParamsByName && !this._clickFadeState) {
                for (const name in this.persistentExpressionParamsByName) {
                    const params = this.persistentExpressionParamsByName[name];
                    if (Array.isArray(params)) {
                        for (const p of params) {
                            if (lipSyncParams.includes(p.Id)) continue;
                            if (this._isEyeBlinkParamId(p.Id)) continue;
                            if (isRuntimeBreathParamId(p.Id)) continue;
                            try {
                                currentCoreModel.setParameterValueById(p.Id, p.Value);
                            } catch (_) {}
                        }
                    }
                }
            }

            // 注入点 3（渲染前最后姿态覆盖）：教程苏醒等短期动作在这里写入，
            // 确保不会被正脸锁、随机视线、眨眼或常驻表情在同一帧覆盖。
            if (typeof this._applyTemporaryPoseOverride === 'function') {
                this._applyTemporaryPoseOverride(currentCoreModel);
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
                if (typeof this._applyTemporaryPoseOverride === 'function') {
                    this._applyTemporaryPoseOverride(currentCoreModel);
                }
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

// 设置嘴巴开合值（0~1），用于口型同步
Live2DManager.prototype.setMouth = function(value) {
    const v = Math.max(0, Math.min(1, Number(value) || 0));
    this.mouthValue = v;

    // 调试日志（每100次调用输出一次）
    if (typeof this._setMouthCallCount === 'undefined') this._setMouthCallCount = 0;
    this._setMouthCallCount++;
    const shouldLog = this._setMouthCallCount % 100 === 1;

    try {
        if (this.currentModel && this.currentModel.internalModel) {
            const coreModel = this.currentModel.internalModel.coreModel;

            // 【新增】延迟初始化并缓存口型参数的 Index，避免每帧进行字符串查找
            if (!this._cachedMouthIndices || this._cachedMouthIndicesModel !== coreModel) {
                this._cachedMouthIndices = [];
                this._cachedMouthIndicesModel = coreModel;
                const mouthIds = window.LIPSYNC_PARAMS || ['ParamMouthOpenY', 'ParamMouthForm', 'ParamMouthOpen', 'ParamA', 'ParamI', 'ParamU', 'ParamE', 'ParamO'];

                for (const id of mouthIds) {
                    if (id === 'ParamMouthForm') continue; // 忽略嘴型（非张合）参数
                    try {
                        const idx = coreModel.getParameterIndex(id);
                        if (idx !== -1) this._cachedMouthIndices.push(idx);
                    } catch (_) {}
                }
            }

            // 【优化】使用极速的 Index 直接写入
            for (const idx of this._cachedMouthIndices) {
                try {
                    coreModel.setParameterValueByIndex(idx, this.mouthValue, 1);
                } catch (_) {}
            }

            if (shouldLog) {
                console.log('[Live2D setMouth] value:', v.toFixed(3), 'indices:', this._cachedMouthIndices.join(', '));
            }
        } else if (shouldLog) {
            console.warn('[Live2D setMouth] 模型未就绪');
        }
    } catch (e) {
        if (shouldLog) console.error('[Live2D setMouth] 错误:', e);
    }
};

// 应用模型位置和缩放设置
Live2DManager.prototype.applyModelSettings = function(model, options) {
    const { preferences, isMobile = false } = options;

    if (isMobile) {
        model.anchor.set(0.5, 0.1);
        const viewportWidth = Math.max(window.innerWidth || this.pixi_app.renderer.screen.width || 1, 1);
        const viewportHeight = Math.max(window.innerHeight || this.pixi_app.renderer.screen.height || 1, 1);
        const scale = Math.min(
            0.5,
            viewportHeight * 1.3 / 4000,
            viewportWidth * 1.2 / 2000
        );
        model.scale.set(scale);
        model.x = viewportWidth * 0.5;
        model.y = viewportHeight * 0.28;
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
                scaleX >= MODEL_PREFERENCES.SCALE_MIN && scaleY >= MODEL_PREFERENCES.SCALE_MIN && scaleX < 10 && scaleY < 10) {
                // 仅在屏幕分辨率发生"跨代"级别变化时（如 1080p→4K）才归一化缩放
                // 普通跨屏移动（如 1600x900→2560x1440）不调整，避免用户调好的大小被改
                const scaleRatio = Math.min(wRatio, hRatio);
                const isExtremeChange = hasViewport && (scaleRatio > 1.8 || scaleRatio < 0.56);
                if (isExtremeChange) {
                    const scaledX = Math.max(MODEL_PREFERENCES.SCALE_MIN, Math.min(scaleX * scaleRatio, MODEL_PREFERENCES.SCALE_MAX));
                    const scaledY = Math.max(MODEL_PREFERENCES.SCALE_MIN, Math.min(scaleY * scaleRatio, MODEL_PREFERENCES.SCALE_MAX));
                    model.scale.set(scaledX, scaledY);
                    console.log('屏幕分辨率大幅变化，缩放已归一化:', { wRatio, hRatio, scaleRatio, scaledX, scaledY });
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

// 应用模型参数值到 Live2D 参数
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

                const resolved = this._resolveModelParameterKey(coreModel, paramId);
                if (!resolved) {
                    continue;
                }
                const resolvedParamId = resolved.resolvedId;

                // EyeBlink 参数由运行时眨眼通道接管，避免冷加载闭眼值被持久化/重放
                if (this._isEyeBlinkParamId(paramId) || this._isEyeBlinkParamId(resolvedParamId)) {
                    continue;
                }
                
                // 跳过常驻表情已设置的参数（保护去水印等功能）
                if (this._parameterEditingMode !== true
                    && (persistentParamIds.has(paramId) || persistentParamIds.has(resolvedParamId))) {
                    continue;
                }
                
                // 跳过可见性参数，防止模型被设置为不可见
                if (visibilityParams.includes(paramId) || visibilityParams.includes(resolvedParamId)) {
                    continue;
                }

                coreModel.setParameterValueByIndex(resolved.idx, value);
            } catch (e) {
                // Ignore
            }
        }
    }
    
    this.mergeAppearanceBaselineParameters(model, parameters);
    // 参数已应用
};

// 获取所有常驻表情参数 ID 集合（用于保护常驻表情参数不被覆盖）
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
