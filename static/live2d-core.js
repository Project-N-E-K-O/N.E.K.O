/**
 * Live2D Core - 核心类结构和基础功能 (修复合并版)
 * 包含原 live2d-core.js 和 live2d-model.js 的所有功能
 */

window.PIXI = PIXI;
const { Live2DModel } = PIXI.live2d;

// --- 常量定义 (直接内联，避免 import 导致脚本报错) ---
const LIPSYNC_PARAMS = [
    'ParamMouthOpenY',
    'ParamMouthForm',
    'ParamMouthOpen',
    'ParamA',
    'ParamI',
    'ParamU',
    'ParamE',
    'ParamO'
];

// 全局变量
let currentModel = null;
let emotionMapping = null;
let currentEmotion = 'neutral';
let pixi_app = null;
let isInitialized = false;

let motionTimer = null; // 动作持续时间定时器
let isEmotionChanging = false; // 防止快速连续点击的标志

// 全局：判断是否为移动端宽度
const isMobileWidth = () => window.innerWidth <= 768;

// Live2D 管理器类
class Live2DManager {
    constructor() {
        this.currentModel = null;
        this.emotionMapping = null; // { motions: {emotion: [string]}, expressions: {emotion: [string]} }
        this.fileReferences = null; // 保存原始 FileReferences（含 Motions/Expressions）
        this.currentEmotion = 'neutral';
        this.currentExpressionFile = null; // 当前使用的表情文件（用于精确比较）
        this.pixi_app = null;
        this.isInitialized = false;
        this.motionTimer = null;
        this.isEmotionChanging = false;
        this.dragEnabled = false;
        this.isFocusing = false;
        this.isLocked = false;
        this.onModelLoaded = null;
        this.onStatusUpdate = null;
        this.modelName = null; // 记录当前模型目录名
        this.modelRootPath = null; // 记录当前模型根路径
        this.savedModelParameters = null; // 保存的模型参数
        this._shouldApplySavedParams = false;
        this._savedParamsTimer = null;
        
        // 模型加载锁
        this._isLoadingModel = false;

        // 常驻表情
        this.persistentExpressionNames = [];
        this.persistentExpressionParamsByName = {};

        // UI/Ticker 资源句柄
        this._lockIconTicker = null;
        this._lockIconElement = null;

        // 浮动按钮系统
        this._floatingButtonsTicker = null;
        this._floatingButtonsContainer = null;
        this._floatingButtons = {}; 
        this._popupTimers = {}; 
        this._goodbyeClicked = false; 
        this._returnButtonContainer = null; 

        // 已打开的设置窗口
        this._openSettingsWindows = {};

        // 口型同步控制
        this.mouthValue = 0; 
        this.mouthParameterId = null; 
        this._mouthOverrideInstalled = false;
        this._origMotionManagerUpdate = null; 
        this._origCoreModelUpdate = null; 
        this._mouthTicker = null;

        // 记录最后一次加载模型的原始路径
        this._lastLoadedModelPath = null;

        // 防抖定时器
        this._savePositionDebounceTimer = null;
    }

    // ================= 原 Core 方法 =================

    // 从 FileReferences 推导 EmotionMapping
    deriveEmotionMappingFromFileRefs(fileRefs) {
        const result = { motions: {}, expressions: {} };
        try {
            // 推导 motions
            const motions = (fileRefs && fileRefs.Motions) || {};
            Object.keys(motions).forEach(group => {
                const items = motions[group] || [];
                const files = items
                    .map(item => (item && item.File) ? String(item.File) : null)
                    .filter(Boolean);
                result.motions[group] = files;
            });
            // 推导 expressions
            const expressions = (fileRefs && Array.isArray(fileRefs.Expressions)) ? fileRefs.Expressions : [];
            expressions.forEach(item => {
                if (!item || typeof item !== 'object') return;
                const name = String(item.Name || '');
                const file = String(item.File || '');
                if (!file) return;
                const group = name.includes('_') ? name.split('_', 1)[0] : 'neutral';
                if (!result.expressions[group]) result.expressions[group] = [];
                result.expressions[group].push(file);
            });
        } catch (e) {
            console.warn('从 FileReferences 推导 EmotionMapping 失败:', e);
        }
        return result;
    }

    // 初始化 PIXI 应用
    async initPIXI(canvasId, containerId, options = {}) {
        if (this.isInitialized && this.pixi_app && this.pixi_app.stage) {
            console.warn('Live2D 管理器已经初始化');
            return this.pixi_app;
        }

        if (this.isInitialized && (!this.pixi_app || !this.pixi_app.stage)) {
            console.warn('Live2D 管理器重置状态');
            if (this.pixi_app && this.pixi_app.destroy) {
                try { this.pixi_app.destroy(true); } catch (e) {}
            }
            this.pixi_app = null;
            this.isInitialized = false;
        }

        const canvas = document.getElementById(canvasId);
        const container = document.getElementById(containerId);
        
        if (!canvas) throw new Error(`找不到 canvas 元素: ${canvasId}`);
        if (!container) throw new Error(`找不到容器元素: ${containerId}`);

        const defaultOptions = {
            autoStart: true,
            transparent: true,
            backgroundAlpha: 0
        };

        try {
            this.pixi_app = new PIXI.Application({
                view: canvas,
                resizeTo: container,
                ...defaultOptions,
                ...options
            });

            if (!this.pixi_app || !this.pixi_app.stage) {
                throw new Error('PIXI.Application 创建失败');
            }

            this.isInitialized = true;
            console.log('[Live2D Core] PIXI.Application 初始化成功');
            return this.pixi_app;
        } catch (error) {
            console.error('[Live2D Core] PIXI.Application 初始化失败:', error);
            this.pixi_app = null;
            this.isInitialized = false;
            throw error;
        }
    }

    // 加载用户偏好
    async loadUserPreferences() {
        try {
            const response = await fetch('/api/config/preferences');
            if (response.ok) return await response.json();
        } catch (error) {
            console.warn('加载用户偏好失败:', error);
        }
        return [];
    }

    // 保存用户偏好
    async saveUserPreferences(modelPath, position, scale, parameters, display) {
        try {
            if (!position || typeof position !== 'object' || !Number.isFinite(position.x)) return false;
            if (!scale || typeof scale !== 'object' || !Number.isFinite(scale.x) || scale.x <= 0) return false;

            const preferences = { model_path: modelPath, position: position, scale: scale };
            if (parameters && typeof parameters === 'object') preferences.parameters = parameters;
            if (display && typeof display === 'object') preferences.display = display;

            const response = await fetch('/api/config/preferences', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(preferences)
            });
            const result = await response.json();
            return result.success;
        } catch (error) {
            console.error("保存偏好失败:", error);
            return false;
        }
    }

    getRandomElement(array) {
        if (!array || array.length === 0) return null;
        return array[Math.floor(Math.random() * array.length)];
    }

    resolveAssetPath(relativePath) {
        if (!relativePath) return '';
        let rel = String(relativePath).replace(/^[\\/]+/, '');
        if (rel.startsWith('static/')) return `/${rel}`;
        if (rel.startsWith('/static/')) return rel;
        return `${this.modelRootPath}/${rel}`;
    }

    getCurrentModel() { return this.currentModel; }
    getEmotionMapping() { return this.emotionMapping; }
    getPIXIApp() { return this.pixi_app; }

    async resetModelPosition() {
        if (!this.currentModel || !this.pixi_app) return;
        try {
            this.currentModel.anchor.set(0.65, 0.75);
            if (isMobileWidth()) {
                const scale = Math.min(0.5, window.innerHeight * 1.3 / 4000, window.innerWidth * 1.2 / 2000);
                this.currentModel.scale.set(scale);
                this.currentModel.x = this.pixi_app.renderer.width * 0.5;
                this.currentModel.y = this.pixi_app.renderer.height * 0.28;
            } else {
                const scale = Math.min(0.5, (window.innerHeight * 0.75) / 7000, (window.innerWidth * 0.6) / 7000);
                this.currentModel.scale.set(scale);
                this.currentModel.x = this.pixi_app.renderer.width;
                this.currentModel.y = this.pixi_app.renderer.height;
            }
            console.log('模型位置已复位');
            if (this._lastLoadedModelPath) {
                await this.saveUserPreferences(
                    this._lastLoadedModelPath,
                    { x: this.currentModel.x, y: this.currentModel.y },
                    { x: this.currentModel.scale.x, y: this.currentModel.scale.y }
                );
            }
        } catch (error) {
            console.error('复位模型位置时出错:', error);
        }
    }

    setLocked(locked, options = {}) {
        const { updateFloatingButtons = true } = options;
        this.isLocked = locked;

        if (this._lockIconImages) {
            const { locked: imgLocked, unlocked: imgUnlocked } = this._lockIconImages;
            if (imgLocked) imgLocked.style.opacity = locked ? '1' : '0';
            if (imgUnlocked) imgUnlocked.style.opacity = locked ? '0' : '1';
        }

        const container = document.getElementById('live2d-canvas');
        if (container) container.style.pointerEvents = locked ? 'none' : 'auto';

        if (!locked) {
            const live2dContainer = document.getElementById('live2d-container');
            if (live2dContainer) live2dContainer.classList.remove('locked-hover-fade');
        }

        if (updateFloatingButtons) {
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            if (floatingButtons) floatingButtons.style.display = locked ? 'none' : 'flex';
        }
    }

    setButtonActive(buttonId, active) {
        const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
        if (!buttonData || !buttonData.button) return;
        buttonData.button.dataset.active = active ? 'true' : 'false';
        buttonData.button.style.background = active ? 'rgba(68, 183, 254, 0.3)' : 'rgba(255, 255, 255, 0.65)';
        if (buttonData.imgOff) buttonData.imgOff.style.opacity = active ? '0' : '1';
        if (buttonData.imgOn) buttonData.imgOn.style.opacity = active ? '1' : '0';
    }

    resetAllButtons() {
        if (!this._floatingButtons) return;
        Object.keys(this._floatingButtons).forEach(btnId => this.setButtonActive(btnId, false));
    }

    // ================= 原 live2d-model.js 移植的方法 =================

    // 加载模型
    async loadModel(modelPath, options = {}) {
        if (!this.pixi_app) throw new Error('PIXI 应用未初始化，请先调用 initPIXI()');

        if (this._isLoadingModel) {
            console.warn('模型正在加载中，跳过重复加载请求:', modelPath);
            return Promise.reject(new Error('Model is already loading.'));
        }
        
        this._isLoadingModel = true;

        try {
            // 移除当前模型
            if (this.currentModel) {
                if (window.closeAllSettingsWindows && !options.skipCloseWindows) window.closeAllSettingsWindows();
                if (this._savedParamsTimer) { clearInterval(this._savedParamsTimer); this._savedParamsTimer = null; }
                
                this.teardownPersistentExpressions && this.teardownPersistentExpressions();
                this.initialParameters = {};

                // 还原核心覆盖
                try {
                    const coreModel = this.currentModel.internalModel && this.currentModel.internalModel.coreModel;
                    if (coreModel && this._mouthOverrideInstalled && typeof this._origCoreModelUpdate === 'function') {
                        coreModel.update = this._origCoreModelUpdate;
                    }
                } catch (_) {}
                this._mouthOverrideInstalled = false;
                this._origCoreModelUpdate = null;
                this._coreModelRef = null;
                
                if (this._mouthTicker && this.pixi_app && this.pixi_app.ticker) {
                    try { this.pixi_app.ticker.remove(this._mouthTicker); } catch (_) {}
                    this._mouthTicker = null;
                }

                // 清理监听器和UI
                try {
                    if (this._mouseTrackingListener) { window.removeEventListener('pointermove', this._mouseTrackingListener); this._mouseTrackingListener = null; }
                    if (this._lockIconTicker) { this.pixi_app.ticker.remove(this._lockIconTicker); this._lockIconTicker = null; }
                    if (this._lockIconElement) { this._lockIconElement.remove(); this._lockIconElement = null; }
                    if (this._floatingButtonsTicker) { this.pixi_app.ticker.remove(this._floatingButtonsTicker); this._floatingButtonsTicker = null; }
                    if (this._floatingButtonsContainer) { this._floatingButtonsContainer.remove(); this._floatingButtonsContainer = null; }
                    this._floatingButtons = {};
                    if (this._returnButtonContainer) { this._returnButtonContainer.remove(); this._returnButtonContainer = null; }
                    Object.values(this._popupTimers).forEach(timer => clearTimeout(timer));
                    this._popupTimers = {};
                } catch (_) {}

                try { this.pixi_app.stage.removeChild(this.currentModel); } catch (_) {}
                try { this.currentModel.destroy({ children: true }); } catch (_) {}
            }

            // 防御性清理舞台残留
            try {
                const stage = this.pixi_app.stage;
                const childrenToRemove = stage.children.filter(c => c && c.internalModel);
                childrenToRemove.forEach(child => {
                    stage.removeChild(child);
                    child.destroy({ children: true });
                });
            } catch (e) {}

            const model = await Live2DModel.from(modelPath, { autoFocus: false });
            this.currentModel = model;

            await this._configureLoadedModel(model, modelPath, options);
            return model;
        } catch (error) {
            console.error('加载模型失败:', error);
            // 回退逻辑
            if (modelPath !== '/static/mao_pro/mao_pro.model3.json') {
                console.warn('尝试回退到默认模型: mao_pro');
                try {
                    const defaultModelPath = '/static/mao_pro/mao_pro.model3.json';
                    const model = await Live2DModel.from(defaultModelPath, { autoFocus: false });
                    this.currentModel = model;
                    await this._configureLoadedModel(model, defaultModelPath, options);
                    return model;
                } catch (fallbackError) {
                    throw new Error(`原始模型加载失败: ${error.message}，且回退模型也失败: ${fallbackError.message}`);
                }
            } else {
                throw error;
            }
        } finally {
            this._isLoadingModel = false;
        }
    }

    resolveMouthParameterId() { return null; }

    async _configureLoadedModel(model, modelPath, options) {
        try {
            let urlString = (typeof modelPath === 'string') ? modelPath : (modelPath?.url);
            if (typeof urlString !== 'string') throw new TypeError('modelPath/url is not a string');

            try { this._lastLoadedModelPath = urlString; } catch (_) {}

            const cleanPath = urlString.split('#')[0].split('?')[0];
            const lastSlash = cleanPath.lastIndexOf('/');
            const rootDir = lastSlash >= 0 ? cleanPath.substring(0, lastSlash) : '/static';
            this.modelRootPath = rootDir;
            const parts = rootDir.split('/').filter(Boolean);
            this.modelName = parts.length > 0 ? parts[parts.length - 1] : null;
        } catch (e) {
            this.modelRootPath = '/static';
            this.modelName = null;
        }

        if (model.internalModel?.renderer?._clippingManager) {
            model.internalModel.renderer._clippingManager._renderTextureCount = 3;
            // 尝试初始化 ClippingManager (如果 API 支持)
        }

        this.applyModelSettings(model, options);
        this.pixi_app.stage.addChild(model);

        if (options.dragEnabled !== false) {
            this.setupDragAndDrop && this.setupDragAndDrop(model);
            this.setupResizeSnapDetection && this.setupResizeSnapDetection();
        }
        if (options.wheelEnabled !== false) this.setupWheelZoom && this.setupWheelZoom(model);
        if (options.touchZoomEnabled !== false) this.setupTouchZoom && this.setupTouchZoom(model);
        if (options.mouseTracking !== false) this.enableMouseTracking && this.enableMouseTracking(model);
        this.setupFloatingButtons && this.setupFloatingButtons(model);
        this.setupHTMLLockIcon && this.setupHTMLLockIcon(model);

        if (options.loadEmotionMapping !== false) {
            const settings = model.internalModel?.settings?.json;
            if (settings) {
                this.fileReferences = settings.FileReferences || null;
                if (settings.EmotionMapping && (settings.EmotionMapping.expressions || settings.EmotionMapping.motions)) {
                    this.emotionMapping = settings.EmotionMapping;
                } else {
                    this.emotionMapping = this.deriveEmotionMappingFromFileRefs(this.fileReferences || {});
                }
            }
        }

        try { await this.syncEmotionMappingWithServer && this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
        await this.setupPersistentExpressions && this.setupPersistentExpressions();
        this.recordInitialParameters && this.recordInitialParameters();
        
        // 加载模型目录下的参数
        if (this.modelName && model.internalModel?.coreModel) {
            try {
                const response = await fetch(`/api/live2d/load_model_parameters/${encodeURIComponent(this.modelName)}`);
                const data = await response.json();
                if (data.success && data.parameters) {
                    this.savedModelParameters = data.parameters;
                    this._shouldApplySavedParams = true;
                    this.applyModelParameters(model, data.parameters);
                } else {
                    this.savedModelParameters = null;
                    this._shouldApplySavedParams = false;
                }
            } catch (error) {
                this.savedModelParameters = null;
                this._shouldApplySavedParams = false;
            }
        }
        
        try { this.installMouthOverride(); } catch (e) { console.error('安装口型覆盖失败:', e); }

        // 应用用户偏好参数（优先级最低，最后应用）
        if (options.preferences && options.preferences.parameters && model.internalModel) {
            this.applyModelParameters(model, options.preferences.parameters);
        }

        if (this.onModelLoaded) this.onModelLoaded(model, modelPath);
    }

    installMouthOverride() {
        if (!this.currentModel || !this.currentModel.internalModel) throw new Error('模型未就绪');
        const internalModel = this.currentModel.internalModel;
        const coreModel = internalModel.coreModel;
        const motionManager = internalModel.motionManager;
        
        if (!coreModel) throw new Error('coreModel 不可用');

        if (this._mouthOverrideInstalled) {
            if (this._origMotionManagerUpdate && motionManager) motionManager.update = this._origMotionManagerUpdate;
            if (this._origCoreModelUpdate && coreModel) coreModel.update = this._origCoreModelUpdate;
            this._origMotionManagerUpdate = null;
            this._origCoreModelUpdate = null;
        }

        const lipSyncParams = LIPSYNC_PARAMS;
        const visibilityParams = ['ParamOpacity', 'ParamVisibility'];
        const mouthParamIndices = {};
        for (const id of ['ParamMouthOpenY', 'ParamO']) {
            try { const idx = coreModel.getParameterIndex(id); if (idx >= 0) mouthParamIndices[id] = idx; } catch (_) {}
        }
        
        // 覆盖 motionManager.update
        if (motionManager && typeof motionManager.update === 'function') {
            const origMotionManagerUpdate = motionManager.update.bind(motionManager);
            this._origMotionManagerUpdate = origMotionManagerUpdate;
        
            motionManager.update = (...args) => {
                if (!coreModel || !this.currentModel?.internalModel?.coreModel) return;

                // 捕获更新前参数
                const preUpdateParams = {};
                if (this.savedModelParameters && this._shouldApplySavedParams) {
                    for (const paramId of Object.keys(this.savedModelParameters)) {
                        try {
                            const idx = coreModel.getParameterIndex(paramId);
                            if (idx >= 0) preUpdateParams[paramId] = coreModel.getParameterValueByIndex(idx);
                        } catch (_) {}
                    }
                }
                
                try { origMotionManagerUpdate(...args); } catch (e) { if (!coreModel) return; }
                
                if (!coreModel || !this.currentModel?.internalModel?.coreModel) return;
                
                // 叠加参数
                try {
                    if (this.savedModelParameters && this._shouldApplySavedParams) {
                        const persistentParamIds = this.getPersistentExpressionParamIds();
                        for (const [paramId, value] of Object.entries(this.savedModelParameters)) {
                            if (lipSyncParams.includes(paramId) || visibilityParams.includes(paramId) || persistentParamIds.has(paramId)) continue;
                            try {
                                const idx = coreModel.getParameterIndex(paramId);
                                if (idx >= 0 && Number.isFinite(value)) {
                                    const currentVal = coreModel.getParameterValueByIndex(idx);
                                    const preVal = preUpdateParams[paramId] !== undefined ? preUpdateParams[paramId] : currentVal;
                                    const offset = value - coreModel.getParameterDefaultValueByIndex(idx);
                                    
                                    if (Math.abs(currentVal - preVal) > 0.001) {
                                        coreModel.setParameterValueByIndex(idx, currentVal + offset);
                                    } else {
                                        coreModel.setParameterValueByIndex(idx, value);
                                    }
                                }
                            } catch (_) {}
                        }
                    }
                    // 写入嘴型和常驻表情
                    for (const [id, idx] of Object.entries(mouthParamIndices)) {
                        try { coreModel.setParameterValueByIndex(idx, this.mouthValue); } catch (_) {}
                    }
                    if (this.persistentExpressionParamsByName) {
                        for (const name in this.persistentExpressionParamsByName) {
                            const params = this.persistentExpressionParamsByName[name];
                            if (Array.isArray(params)) {
                                for (const p of params) {
                                    if (LIPSYNC_PARAMS.includes(p.Id)) continue;
                                    try { coreModel.setParameterValueById(p.Id, p.Value); } catch (_) {}
                                }
                            }
                        }
                    }
                } catch (_) {}
            };
        }
        
        // 覆盖 coreModel.update
        const origCoreModelUpdate = coreModel.update ? coreModel.update.bind(coreModel) : null;
        this._origCoreModelUpdate = origCoreModelUpdate;
        this._coreModelRef = coreModel;
        
        coreModel.update = () => {
            if (!this._mouthOverrideInstalled || !this._coreModelRef) return;
            if (!this.currentModel?.internalModel?.coreModel) {
                this._mouthOverrideInstalled = false; this._origCoreModelUpdate = null; this._coreModelRef = null; return;
            }
            
            const currentCoreModel = this.currentModel.internalModel.coreModel;
            if (currentCoreModel !== coreModel && currentCoreModel !== this._coreModelRef) {
                this._mouthOverrideInstalled = false; this._origCoreModelUpdate = null; this._coreModelRef = null; return;
            }
            
            try {
                for (const [id, idx] of Object.entries(mouthParamIndices)) {
                    try { currentCoreModel.setParameterValueByIndex(idx, this.mouthValue); } catch (_) {}
                }
                if (this.persistentExpressionParamsByName) {
                    for (const name in this.persistentExpressionParamsByName) {
                        const params = this.persistentExpressionParamsByName[name];
                        if (Array.isArray(params)) {
                            for (const p of params) {
                                if (LIPSYNC_PARAMS.includes(p.Id)) continue;
                                try { currentCoreModel.setParameterValueById(p.Id, p.Value); } catch (_) {}
                            }
                        }
                    }
                }
            } catch (e) {}
            
            if (currentCoreModel === coreModel && origCoreModelUpdate) {
                try { origCoreModelUpdate(); } catch (e) {
                     // 尝试直接调用内部模型的 update 作为最后手段
                     if (currentCoreModel.internalModel?.update && currentCoreModel.internalModel.update !== currentCoreModel.update) {
                         currentCoreModel.internalModel.update();
                     }
                }
            }
        };

        this._mouthOverrideInstalled = true;
        console.log('已安装双重参数覆盖');
    }

    setMouth(value) {
        const v = Math.max(0, Math.min(1, Number(value) || 0));
        this.mouthValue = v;
        try {
            if (this.currentModel?.internalModel?.coreModel) {
                const coreModel = this.currentModel.internalModel.coreModel;
                ['ParamMouthOpenY', 'ParamO'].forEach(id => {
                    try { coreModel.setParameterValueById(id, this.mouthValue, 1); } catch (_) {}
                });
            }
        } catch (_) {}
    }

    applyModelSettings(model, options) {
        const { preferences, isMobile = false } = options;
        if (isMobile) {
            const scale = Math.min(0.5, window.innerHeight * 1.3 / 4000, window.innerWidth * 1.2 / 2000);
            model.scale.set(scale);
            model.x = this.pixi_app.renderer.width * 0.5;
            model.y = this.pixi_app.renderer.height * 0.28;
            model.anchor.set(0.5, 0.1);
        } else {
            if (preferences && preferences.scale && preferences.position) {
                const sX = Number(preferences.scale.x), sY = Number(preferences.scale.y);
                const pX = Number(preferences.position.x), pY = Number(preferences.position.y);
                
                if (Number.isFinite(sX) && sX > 0) model.scale.set(sX, sY);
                else {
                    const defScale = Math.min(0.5, (window.innerHeight * 0.75) / 7000, (window.innerWidth * 0.6) / 7000);
                    model.scale.set(defScale);
                }
                
                if (Number.isFinite(pX)) { model.x = pX; model.y = pY; }
                else { model.x = this.pixi_app.renderer.width; model.y = this.pixi_app.renderer.height; }
            } else {
                const scale = Math.min(0.5, (window.innerHeight * 0.75) / 7000, (window.innerWidth * 0.6) / 7000);
                model.scale.set(scale);
                model.x = this.pixi_app.renderer.width;
                model.y = this.pixi_app.renderer.height;
            }
            model.anchor.set(0.65, 0.75);
        }
    }

    applyModelParameters(model, parameters) {
        if (!model?.internalModel?.coreModel || !parameters) return;
        
        const coreModel = model.internalModel.coreModel;
        const persistentParamIds = this.getPersistentExpressionParamIds();
        const visibilityParams = ['ParamOpacity', 'ParamVisibility'];

        for (const [paramId, value] of Object.entries(parameters)) {
            try {
                if (typeof value !== 'number' || !Number.isFinite(value)) continue;
                if (persistentParamIds.has(paramId) || visibilityParams.includes(paramId)) continue;
                
                let idx = -1;
                if (paramId.startsWith('param_')) {
                    const parsedIndex = parseInt(paramId.replace('param_', ''), 10);
                    if (!isNaN(parsedIndex) && parsedIndex >= 0) idx = parsedIndex;
                } else {
                    idx = coreModel.getParameterIndex(paramId);
                }
                
                if (idx >= 0) coreModel.setParameterValueByIndex(idx, value);
            } catch (e) {}
        }
    }

    getPersistentExpressionParamIds() {
        const paramIds = new Set();
        if (this.persistentExpressionParamsByName) {
            Object.values(this.persistentExpressionParamsByName).forEach(params => {
                if (Array.isArray(params)) params.forEach(p => { if (p && p.Id) paramIds.add(p.Id); });
            });
        }
        return paramIds;
    }
}

// 导出
window.Live2DModel = Live2DModel;
window.Live2DManager = Live2DManager;
window.isMobileWidth = isMobileWidth;