/**
 * Live2D Core - 核心类结构和基础功能
 * 功能包括:
 * - PIXI 应用初始化和管理
 * - Live2D 模型加载和管理
 * - 表情映射和转换
 * - 动作和表情控制
 * - 模型偏好设置
 * - 模型偏好验证
 * - 口型同步参数列表
 * - 全局状态管理（如锁定状态、按钮状态等）
 * - 事件监听（如帧率变更、画质变更等）
 * - 触摸事件处理（如点击、拖动等）
 */

window.PIXI = PIXI;
const { Live2DModel } = PIXI.live2d;

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

// 口型同步参数列表常量
// 这些参数用于控制模型的嘴部动作，在处理表情和常驻表情时需要跳过，以避免覆盖实时的口型同步
window.LIPSYNC_PARAMS = [
    'ParamMouthOpenY',
    'ParamMouthForm',
    'ParamMouthOpen',
    'ParamA',
    'ParamI',
    'ParamU',
    'ParamE',
    'ParamO'
];

// 模型偏好验证常量
const MODEL_PREFERENCES = {
    SCALE_MIN: 0.005,
    SCALE_MAX: 10,
    POSITION_MAX: 100000
};

const LIVE2D_BUBBLE_GEOMETRY_OVERRIDES = Object.freeze({});

// 验证模型偏好是否有效
function isValidModelPreferences(scale, position) {
    if (!scale || !position) return false;
    const scaleX = scale.x;
    const scaleY = scale.y;
    const posX = position.x;
    const posY = position.y;
    const isValidScale = Number.isFinite(scaleX) && scaleX >= MODEL_PREFERENCES.SCALE_MIN && scaleX < MODEL_PREFERENCES.SCALE_MAX &&
                        Number.isFinite(scaleY) && scaleY >= MODEL_PREFERENCES.SCALE_MIN && scaleY < MODEL_PREFERENCES.SCALE_MAX;
    const isValidPosition = Number.isFinite(posX) && Number.isFinite(posY) &&
                           Math.abs(posX) < MODEL_PREFERENCES.POSITION_MAX && Math.abs(posY) < MODEL_PREFERENCES.POSITION_MAX;
    return isValidScale && isValidPosition;
}

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
        this.touchSetFilter = {};       // 点击/触摸滤波计数器（touch-config.js 会覆盖）
        this.touchSetHitEventLock = false; // hit 事件锁（touch-config.js 会覆盖）
        this.modelName = null; // 记录当前模型目录名
        this.modelRootPath = null; // 记录当前模型根路径，如 /static/<modelName>
        this.savedModelParameters = null; // 保存的模型参数（从parameters.json加载），供定时器定期应用
        this._shouldApplySavedParams = false; // 是否应该应用保存的参数
        this._savedParamsTimer = null; // 保存参数应用的定时器
        this._mouseTrackingEnabled = window.mouseTrackingEnabled !== false; // 鼠标跟踪启用状态
        this._fullscreenTrackingEnabled = window.live2dFullscreenTrackingEnabled === true; // 全屏跟踪启用状态
        
        // 模型加载锁，防止并发加载导致重复模型叠加
        this._isLoadingModel = false;
        this._activeLoadToken = 0;
        this._modelLoadState = 'idle';
        this._isModelReadyForInteraction = false;
        this._initPIXIPromise = null;
        this._lastPIXIContext = { canvasId: null, containerId: null };
        this._displayInfo = null;
        this._autoNamedHitAreaIds = new Set();
        this._bubbleGeometryCache = null;

        // 常驻表情：使用官方 expression 播放并在清理后自动重放
        this.persistentExpressionNames = [];
        this.persistentExpressionParamsByName = {};

        // UI/Ticker 资源句柄（便于在切换模型时清理）
        this._lockIconTicker = null;
        this._lockIconElement = null;

        // 口型同步
        this.mouthValue = 0; // 0~1 (嘴巴开合值)
        this.mouthParameterId = null; // 例如 'ParamMouthOpenY' 或 'ParamO'
        this._mouthOverrideInstalled = false;
        this._origMotionManagerUpdate = null; // 保存原始的 motionManager.update 方法
        this._origCoreModelUpdate = null; // 保存原始的 coreModel.update 方法
        this._mouthTicker = null;

        // 记录最后一次加载模型的原始路径（用于保存偏好时使用）
        this._lastLoadedModelPath = null;

        // 防抖定时器（用于滚轮缩放等连续操作后保存位置）
        this._savePositionDebounceTimer = null;

        // 口型覆盖重新安装标志（防止重复安装）
        this._reinstallScheduled = false;

        // 记录已确认不存在的 expression 文件，避免重复 404 请求
        this._missingExpressionFiles = new Set();
        
        
    }

    // 从 FileReferences 推导 EmotionMapping（用于兼容历史数据）
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

            // 推导 expressions（按 Name 前缀分组）
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
        if (this._initPIXIPromise) {
            return await this._initPIXIPromise;
        }

        if (this.isInitialized && this.pixi_app && this.pixi_app.stage) {
            console.warn('Live2D 管理器已经初始化');
            return this.pixi_app;
        }

        // 如果已初始化但 stage 不存在，重置状态
        if (this.isInitialized && (!this.pixi_app || !this.pixi_app.stage)) {
            console.warn('Live2D 管理器标记为已初始化，但 pixi_app 或 stage 不存在，重置状态');
            if (this.pixi_app && this.pixi_app.destroy) {
                if (this._screenChangeHandler) {
                    window.removeEventListener('resize', this._screenChangeHandler);
                    this._screenChangeHandler = null;
                }
                if (this._displayChangeHandler) {
                    window.removeEventListener('electron-display-changed', this._displayChangeHandler);
                    this._displayChangeHandler = null;
                }
                try {
                    this.pixi_app.destroy(true);
                } catch (e) {
                    console.warn('销毁旧的 pixi_app 时出错:', e);
                }
            }
            this.pixi_app = null;
            this.isInitialized = false;
        }

        const canvas = document.getElementById(canvasId);
        const container = document.getElementById(containerId);
        
        if (!canvas) {
            throw new Error(`找不到 canvas 元素: ${canvasId}`);
        }
        if (!container) {
            throw new Error(`找不到容器元素: ${containerId}`);
        }

        const defaultOptions = {
            autoStart: true,
            transparent: true,
            backgroundAlpha: 0,
            resolution: window.devicePixelRatio || 1,
            autoDensity: true
        };

        this._initPIXIPromise = (async () => {
            try {
                // 使用 window.screen 全屏尺寸初始化渲染器，画布始终覆盖整个屏幕区域
                // 任务栏/DevTools/键盘等造成的视口缩小只会裁切画布边缘（overflow:hidden），
                // 不会导致缝隙或模型位移
                const initW = Math.max(window.screen.width || 1, 1);
                const initH = Math.max(window.screen.height || 1, 1);
                this.pixi_app = new PIXI.Application({
                    view: canvas,
                    width: initW,
                    height: initH,
                    ...defaultOptions,
                    ...options
                });

                if (!this.pixi_app) {
                    throw new Error('PIXI.Application 创建失败：返回值为 null 或 undefined');
                }

                if (!this.pixi_app.stage) {
                    throw new Error('PIXI.Application 创建失败：stage 属性不存在');
                }

                this.isInitialized = true;
                this._lastPIXIContext = { canvasId, containerId };
                if (typeof window.targetFrameRate === 'number' && this.pixi_app.ticker) {
                    this.pixi_app.ticker.maxFPS = window.targetFrameRate;
                }

                // Resize 渲染器并等比调整模型坐标/尺寸
                // 触发时机：
                //  1) 系统屏幕分辨率变化（window.screen.width/height 变化）—— 原有逻辑
                //  2) Electron 跨屏切换 / 显示器 hotplug —— 通过 'electron-display-changed' 事件触发
                //     （在 Electron 里 window.screen.width/height 不会随 BrowserWindow 跨屏而变，
                //      所以单靠 screen 比较无法感知跨屏，canvas 会保持主屏初始尺寸被窗口边界裁切）
                // 任务栏、DevTools、输入法等视口变化不会触发（幂等判定跳过）
                let lastScreenW = window.screen.width;
                let lastScreenH = window.screen.height;

                const doResize = (reason) => {
                    if (!this.pixi_app || !this.pixi_app.renderer) return;
                    const prevW = this.pixi_app.renderer.screen.width;
                    const prevH = this.pixi_app.renderer.screen.height;
                    // 以 CSS 像素为准（= BrowserWindow 当前像素尺寸），这是模型真正可见的区域
                    const newW = Math.max(window.innerWidth || window.screen.width || 1, 1);
                    const newH = Math.max(window.innerHeight || window.screen.height || 1, 1);
                    if (prevW === newW && prevH === newH) return;

                    this.pixi_app.renderer.resize(newW, newH);

                    // 跨屏切换路径（Live2DManager._checkAndSwitchDisplay）已在 moveWindowToDisplay 之后
                    // 主动把 model.x/y 设置为新屏窗口坐标。若这里再按 (newW/prevW, newH/prevH) 缩放，
                    // 会对同一个值双重作用，导致模型偏移。通过 _pendingDisplaySwitch 跳过缩放，
                    // 仅 resize renderer（renderer 尺寸必须更新，否则 canvas 仍是旧尺寸裁切模型）。
                    if (this._pendingDisplaySwitch) {
                        console.log('[Live2D Core] renderer 已 resize（跨屏切换中，跳过模型缩放）:', { reason, prevW, prevH, newW, newH });
                        return;
                    }

                    if (this.currentModel && prevW > 0 && prevH > 0) {
                        const wRatio = newW / prevW;
                        const hRatio = newH / prevH;
                        this.currentModel.x *= wRatio;
                        this.currentModel.y *= hRatio;
                        const areaRatio = Math.sqrt(wRatio * hRatio);
                        this.currentModel.scale.x *= areaRatio;
                        this.currentModel.scale.y *= areaRatio;
                    }
                    console.log('[Live2D Core] renderer 已 resize:', { reason, prevW, prevH, newW, newH });
                };

                this._screenChangeHandler = () => {
                    const sw = window.screen.width;
                    const sh = window.screen.height;
                    if (sw === lastScreenW && sh === lastScreenH) return;
                    lastScreenW = sw;
                    lastScreenH = sh;
                    doResize('window.screen changed');
                };
                // 跨屏切换信号：主进程 setBounds 后广播；这里等一帧让 innerWidth/Height 落地再 resize
                this._displayChangeHandler = () => {
                    requestAnimationFrame(() => doResize('electron-display-changed'));
                };

                window.addEventListener('resize', this._screenChangeHandler);
                window.addEventListener('electron-display-changed', this._displayChangeHandler);

                console.log('[Live2D Core] PIXI.Application 初始化成功，stage 已创建');
                return this.pixi_app;
            } catch (error) {
                console.error('[Live2D Core] PIXI.Application 初始化失败:', error);
                this.pixi_app = null;
                this.isInitialized = false;
                throw error;
            }
        })();

        try {
            return await this._initPIXIPromise;
        } finally {
            this._initPIXIPromise = null;
        }
    }

    async ensurePIXIReady(canvasId, containerId, options = {}) {
        const lastContext = this._lastPIXIContext || {};
        const contextMatches = (
            lastContext.canvasId === canvasId &&
            lastContext.containerId === containerId
        );

        if (this.isInitialized && this.pixi_app && this.pixi_app.stage && contextMatches) {
            return this.pixi_app;
        }
        if (this.isInitialized && !contextMatches) {
            if (this._screenChangeHandler) {
                window.removeEventListener('resize', this._screenChangeHandler);
                this._screenChangeHandler = null;
            }
            if (this._displayChangeHandler) {
                window.removeEventListener('electron-display-changed', this._displayChangeHandler);
                this._displayChangeHandler = null;
            }
            if (this.pixi_app && this.pixi_app.destroy) {
                try {
                    this.pixi_app.destroy(true);
                } catch (e) {
                    console.warn('[Live2D Core] ensurePIXIReady 销毁旧 PIXI 失败:', e);
                }
            }
            this.pixi_app = null;
            this.isInitialized = false;
        }
        const app = await this.initPIXI(canvasId, containerId, options);
        if (app && app.stage) {
            this._lastPIXIContext = { canvasId, containerId };
        }
        return app;
    }

    async rebuildPIXI(canvasId, containerId, options = {}) {
        if (this._initPIXIPromise) {
            try {
                await this._initPIXIPromise;
            } catch (e) {
                console.warn('[Live2D Core] 忽略旧初始化失败，继续重建 PIXI:', e);
            }
        }
        if (this._screenChangeHandler) {
            window.removeEventListener('resize', this._screenChangeHandler);
            this._screenChangeHandler = null;
        }
        if (this._displayChangeHandler) {
            window.removeEventListener('electron-display-changed', this._displayChangeHandler);
            this._displayChangeHandler = null;
        }
        if (this.pixi_app && this.pixi_app.destroy) {
            try {
                this.pixi_app.destroy(true);
            } catch (e) {
                console.warn('[Live2D Core] 重建时销毁旧 PIXI 失败:', e);
            }
        }
        this.pixi_app = null;
        this.isInitialized = false;
        return await this.initPIXI(canvasId, containerId, options);
    }

    /**
     * 暂停渲染循环（用于节省资源，例如进入模型管理界面时）
     */
    pauseRendering() {
        if (this.pixi_app && this.pixi_app.ticker) {
            this.pixi_app.ticker.stop();
            console.log('[Live2D Core] 渲染循环已暂停');
        }
    }

    /**
     * 恢复渲染循环（从暂停状态恢复）
     */
    resumeRendering() {
        if (this.pixi_app && this.pixi_app.ticker) {
            this.pixi_app.ticker.start();
            console.log('[Live2D Core] 渲染循环已恢复');
        }
    }

    /**
     * 设置目标帧率
     * @param {number} fps - 目标帧率，0 表示不限帧（跟随 VSync）
     */
    setTargetFPS(fps) {
        if (this.pixi_app && this.pixi_app.ticker) {
            this.pixi_app.ticker.maxFPS = fps;
            console.log(`[Live2D Core] 目标帧率设置为 ${fps === 0 ? 'VSync (无限制)' : fps + 'fps'}`);
        }
    }

    // 加载用户偏好
    async loadUserPreferences() {
        try {
            const response = await fetch('/api/config/preferences');
            if (response.ok) {
                return await response.json();
            }
        } catch (error) {
            console.warn('加载用户偏好失败:', error);
        }
        return [];
    }

    // 保存用户偏好
    async saveUserPreferences(modelPath, position, scale, parameters, display, viewport) {
        try {
            // 验证位置和缩放值是否为有效的有限数值
            if (!isValidModelPreferences(scale, position)) {
                console.error('位置或缩放值无效:', { scale, position });
                return false;
            }

            const preferences = {
                model_path: modelPath,
                position: position,
                scale: scale
            };

            // 如果有参数，添加到偏好中
            if (parameters && typeof parameters === 'object') {
                preferences.parameters = parameters;
            }

            // 如果有显示器信息，添加到偏好中（用于多屏幕位置恢复）
            if (display && typeof display === 'object' &&
                Number.isFinite(display.screenX) && Number.isFinite(display.screenY)) {
                preferences.display = {
                    screenX: display.screenX,
                    screenY: display.screenY
                };
            }

            // 如果有视口信息，添加到偏好中（用于跨分辨率位置和缩放归一化）
            if (viewport && typeof viewport === 'object' &&
                Number.isFinite(viewport.width) && Number.isFinite(viewport.height) &&
                viewport.width > 0 && viewport.height > 0) {
                preferences.viewport = {
                    width: viewport.width,
                    height: viewport.height
                };
            }

            const response = await fetch('/api/config/preferences', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(preferences)
            });
            const result = await response.json();
            return result.success;
        } catch (error) {
            console.error("保存偏好失败:", error);
            return false;
        }
    }

    // 随机选择数组中的一个元素
    getRandomElement(array) {
        if (!array || array.length === 0) return null;
        return array[Math.floor(Math.random() * array.length)];
    }

    // 解析资源相对路径（基于当前模型根目录）
    resolveAssetPath(relativePath) {
        if (!relativePath) return '';
        let rel = String(relativePath).replace(/^[\\/]+/, '');
        if (rel.startsWith('static/')) {
            return `/${rel}`;
        }
        if (rel.startsWith('/static/')) {
            return rel;
        }
        return `${this.modelRootPath}/${rel}`;
    }

    // 规范化资源路径，用于宽松比较（忽略斜杠差异与大小写）
    normalizeAssetPathForCompare(assetPath) {
        if (!assetPath) return '';
        const decoded = String(assetPath).trim();
        const unified = decoded.replace(/\\/g, '/').replace(/^\/+/, '').replace(/^\.\//, '');
        return unified.toLowerCase();
    }

    // 通过表达文件路径解析 expression name（兼容 "expressions/a.exp3.json" 与 "a.exp3.json"）
    resolveExpressionNameByFile(expressionFile) {
        const ref = this.resolveExpressionReferenceByFile(expressionFile);
        return ref ? ref.name : null;
    }

    normalizeExpressionFileKey(expressionFile) {
        if (!expressionFile || typeof expressionFile !== 'string') return '';
        return expressionFile.replace(/\\/g, '/').trim().toLowerCase();
    }

    markExpressionFileMissing(expressionFile) {
        const key = this.normalizeExpressionFileKey(expressionFile);
        if (!key) return;
        if (!this._missingExpressionFiles) this._missingExpressionFiles = new Set();
        this._missingExpressionFiles.add(key);
        const base = key.split('/').pop();
        if (base) this._missingExpressionFiles.add(base);
    }

    isExpressionFileMissing(expressionFile) {
        const key = this.normalizeExpressionFileKey(expressionFile);
        if (!key || !this._missingExpressionFiles) return false;
        if (this._missingExpressionFiles.has(key)) return true;
        const base = key.split('/').pop();
        return !!base && this._missingExpressionFiles.has(base);
    }

    clearMissingExpressionFiles() {
        if (this._missingExpressionFiles) this._missingExpressionFiles.clear();
    }

    // 通过 expression 文件路径解析出标准引用（Name + File）
    resolveExpressionReferenceByFile(expressionFile) {
        if (!expressionFile || !this.fileReferences || !Array.isArray(this.fileReferences.Expressions)) {
            return null;
        }

        const targetNorm = this.normalizeAssetPathForCompare(expressionFile);
        const targetBase = targetNorm.split('/').pop() || '';

        // 1) 优先精确匹配规范化后的 File 路径
        for (const expr of this.fileReferences.Expressions) {
            if (!expr || typeof expr !== 'object' || !expr.Name || !expr.File) continue;
            const fileNorm = this.normalizeAssetPathForCompare(expr.File);
            if (fileNorm === targetNorm) {
                return { name: expr.Name, file: expr.File };
            }
        }

        // 2) 兜底按文件名匹配（处理映射只给 basename 的情况）
        if (targetBase) {
            for (const expr of this.fileReferences.Expressions) {
                if (!expr || typeof expr !== 'object' || !expr.Name || !expr.File) continue;
                const fileBase = this.normalizeAssetPathForCompare(expr.File).split('/').pop() || '';
                if (fileBase === targetBase) {
                    return { name: expr.Name, file: expr.File };
                }
            }
        }

        return null;
    }

    // 获取当前模型
    getCurrentModel() {
        return this.currentModel;
    }

    // 获取当前情感映射
    getEmotionMapping() {
        return this.emotionMapping;
    }

    // 获取 PIXI 应用
    getPIXIApp() {
        return this.pixi_app;
    }

    _isFiniteMatrix2D(matrix) {
        return !!(matrix &&
            Number.isFinite(matrix.a) &&
            Number.isFinite(matrix.b) &&
            Number.isFinite(matrix.c) &&
            Number.isFinite(matrix.d) &&
            Number.isFinite(matrix.tx) &&
            Number.isFinite(matrix.ty));
    }

    _applyMatrixToPoint(matrix, x, y) {
        if (!this._isFiniteMatrix2D(matrix) || !Number.isFinite(x) || !Number.isFinite(y)) {
            return null;
        }

        return {
            x: matrix.a * x + matrix.c * y + matrix.tx,
            y: matrix.b * x + matrix.d * y + matrix.ty
        };
    }

    _ensureModelWorldTransform(model = this.currentModel) {
        if (!model) {
            return;
        }

        try {
            if (typeof model._recursivePostUpdateTransform === 'function') {
                model._recursivePostUpdateTransform();
            }

            if (typeof model.displayObjectUpdateTransform === 'function') {
                if (model.parent) {
                    model.displayObjectUpdateTransform();
                } else if (model._tempDisplayObjectParent) {
                    const originalParent = model.parent;
                    model.parent = model._tempDisplayObjectParent;
                    model.displayObjectUpdateTransform();
                    model.parent = originalParent || null;
                }
            }
        } catch (_) {}
    }

    _getDrawableVertexSequence(drawableIndex) {
        const internalModel = this.currentModel?.internalModel;
        if (!internalModel || typeof internalModel.getDrawableVertices !== 'function') {
            return null;
        }

        let vertices = null;
        try {
            vertices = internalModel.getDrawableVertices(drawableIndex);
        } catch (_) {
            return null;
        }

        return vertices && typeof vertices.length === 'number' && vertices.length >= 4
            ? vertices
            : null;
    }

    _isDrawableRenderable(coreModel, drawableIndex) {
        if (!coreModel || !Number.isInteger(drawableIndex) || drawableIndex < 0) {
            return false;
        }

        try {
            const visible = coreModel.getDrawableDynamicFlagIsVisible?.(drawableIndex);
            if (typeof visible === 'boolean' && !visible) {
                return false;
            }
        } catch (_) {}

        try {
            const opacity = coreModel.getDrawableOpacity?.(drawableIndex);
            if (Number.isFinite(opacity) && opacity <= 0.01) {
                return false;
            }
        } catch (_) {}

        return true;
    }

    _getDrawableLogicalRect(drawableIndex) {
        const internalModel = this.currentModel?.internalModel;
        if (!internalModel || typeof internalModel.getDrawableBounds !== 'function') {
            return null;
        }

        const rect = internalModel.getDrawableBounds(drawableIndex, {});
        if (!rect || !Number.isFinite(rect.x) || !Number.isFinite(rect.y) ||
            !Number.isFinite(rect.width) || !Number.isFinite(rect.height)) {
            return null;
        }

        return {
            x: rect.x,
            y: rect.y,
            width: Math.max(1, rect.width),
            height: Math.max(1, rect.height)
        };
    }

    _getDrawableDirectScreenRect(drawableIndex, skipTransformSync = false) {
        const model = this.currentModel;
        const internalModel = model?.internalModel;
        const vertices = this._getDrawableVertexSequence(drawableIndex);
        const localTransform = internalModel?.localTransform;
        const worldTransform = model?.worldTransform;
        if (!model || !internalModel || !vertices ||
            !this._isFiniteMatrix2D(localTransform) ||
            !this._isFiniteMatrix2D(worldTransform)) {
            return null;
        }

        if (!skipTransformSync) {
            this._ensureModelWorldTransform(model);
        }

        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;

        for (let index = 0; index < vertices.length; index += 2) {
            const vx = Number(vertices[index]);
            const vy = Number(vertices[index + 1]);
            const localPoint = this._applyMatrixToPoint(localTransform, vx, vy);
            const screenPoint = localPoint
                ? this._applyMatrixToPoint(worldTransform, localPoint.x, localPoint.y)
                : null;
            if (!screenPoint) {
                continue;
            }

            minX = Math.min(minX, screenPoint.x);
            maxX = Math.max(maxX, screenPoint.x);
            minY = Math.min(minY, screenPoint.y);
            maxY = Math.max(maxY, screenPoint.y);
        }

        return this._createScreenRect(minX, minY, maxX, maxY);
    }

    _getModelLogicalRect() {
        const internalModel = this.currentModel?.internalModel;
        const coreModel = internalModel?.coreModel;
        const drawableCount = coreModel?.getDrawableCount?.();
        if (!internalModel || !coreModel || !Number.isInteger(drawableCount) || drawableCount <= 0) {
            return null;
        }
        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;

        for (let index = 0; index < drawableCount; index += 1) {
            const rect = this._getDrawableLogicalRect(index);
            if (!rect) continue;
            minX = Math.min(minX, rect.x);
            maxX = Math.max(maxX, rect.x + rect.width);
            minY = Math.min(minY, rect.y);
            maxY = Math.max(maxY, rect.y + rect.height);
        }

        if (!Number.isFinite(minX) || !Number.isFinite(maxX) ||
            !Number.isFinite(minY) || !Number.isFinite(maxY)) {
            return null;
        }

        return {
            x: minX,
            y: minY,
            width: Math.max(1, maxX - minX),
            height: Math.max(1, maxY - minY)
        };
    }

    _mapLogicalRectToScreen(logicalRect, modelLogicalRect, modelBounds) {
        if (!logicalRect || !modelLogicalRect || !modelBounds) {
            return null;
        }

        const logicalWidth = Math.max(1, modelLogicalRect.width);
        const logicalHeight = Math.max(1, modelLogicalRect.height);

        const relLeft = (logicalRect.x - modelLogicalRect.x) / logicalWidth;
        const relTop = (logicalRect.y - modelLogicalRect.y) / logicalHeight;
        const relWidth = logicalRect.width / logicalWidth;
        const relHeight = logicalRect.height / logicalHeight;

        return {
            left: modelBounds.left + modelBounds.width * relLeft,
            top: modelBounds.top + modelBounds.height * relTop,
            width: modelBounds.width * relWidth,
            height: modelBounds.height * relHeight
        };
    }

    _screenRectToLogical(screenRect, modelBounds, modelLogicalRect) {
        if (!screenRect || !modelBounds || !modelLogicalRect) { return null; }
        const bw = Math.max(1, modelBounds.width);
        const bh = Math.max(1, modelBounds.height);
        const lw = Math.max(1, modelLogicalRect.width);
        const lh = Math.max(1, modelLogicalRect.height);
        return {
            x: modelLogicalRect.x + (screenRect.left - modelBounds.left) / bw * lw,
            y: modelLogicalRect.y + (screenRect.top - modelBounds.top) / bh * lh,
            width: screenRect.width / bw * lw,
            height: screenRect.height / bh * lh
        };
    }

    _logicalRectToScreenRect(logicalRect, modelLogicalRect, modelBounds) {
        const mapped = this._mapLogicalRectToScreen(logicalRect, modelLogicalRect, modelBounds);
        if (!mapped) { return null; }
        return this._createScreenRect(mapped.left, mapped.top, mapped.left + mapped.width, mapped.top + mapped.height);
    }

    _screenPointToLogical(point, modelBounds, modelLogicalRect) {
        if (!point || !modelBounds || !modelLogicalRect) { return null; }
        const bw = Math.max(1, modelBounds.width);
        const bh = Math.max(1, modelBounds.height);
        return {
            x: modelLogicalRect.x + (point.x - modelBounds.left) / bw * modelLogicalRect.width,
            y: modelLogicalRect.y + (point.y - modelBounds.top) / bh * modelLogicalRect.height
        };
    }

    _logicalPointToScreen(logicalPoint, modelLogicalRect, modelBounds) {
        if (!logicalPoint || !modelLogicalRect || !modelBounds) { return null; }
        const lw = Math.max(1, modelLogicalRect.width);
        const lh = Math.max(1, modelLogicalRect.height);
        return {
            x: modelBounds.left + (logicalPoint.x - modelLogicalRect.x) / lw * modelBounds.width,
            y: modelBounds.top + (logicalPoint.y - modelLogicalRect.y) / lh * modelBounds.height
        };
    }

    _createScreenRect(left, top, right, bottom) {
        const width = right - left;
        const height = bottom - top;
        if (!Number.isFinite(left) || !Number.isFinite(top) ||
            !Number.isFinite(right) || !Number.isFinite(bottom) ||
            width <= 0 || height <= 0) {
            return null;
        }

        return {
            left,
            right,
            top,
            bottom,
            width,
            height,
            centerX: left + width * 0.5,
            centerY: top + height * 0.5
        };
    }

    _createRectInfoFromScreenRect(screenRect, mode, source = null) {
        if (!screenRect) {
            return null;
        }

        return {
            rect: {
                left: screenRect.left,
                right: screenRect.right,
                top: screenRect.top,
                bottom: screenRect.bottom,
                width: screenRect.width,
                height: screenRect.height,
                centerX: screenRect.centerX,
                centerY: screenRect.centerY
            },
            mode,
            source
        };
    }

    _cacheBubbleGeometryResult(result, modelBounds, modelLogicalRect) {
        if (!result || !result.reliableHeadRect || !modelBounds || !modelLogicalRect) { return; }
        const headRect = result.headRect ? this._screenRectToLogical(result.headRect, modelBounds, modelLogicalRect) : null;
        const bubbleHeadRect = result.bubbleHeadRect ? this._screenRectToLogical(result.bubbleHeadRect, modelBounds, modelLogicalRect) : null;
        const bodyRect = result.bodyRect ? this._screenRectToLogical(result.bodyRect, modelBounds, modelLogicalRect) : null;
        const rawHeadAnchor = result.rawHeadAnchor ? this._screenPointToLogical(result.rawHeadAnchor, modelBounds, modelLogicalRect) : null;
        const headAnchor = result.headAnchor ? this._screenPointToLogical(result.headAnchor, modelBounds, modelLogicalRect) : null;
        if (!bubbleHeadRect && !headRect) { return; }
        this._bubbleGeometryCache = {
            modelPath: this.modelRootPath,
            modelLogicalRect: { x: modelLogicalRect.x, y: modelLogicalRect.y, width: modelLogicalRect.width, height: modelLogicalRect.height },
            headMode: result.headMode,
            headSource: result.headSource,
            bodySource: result.bodySource,
            reliableHeadRect: result.reliableHeadRect,
            preciseDisplayInfoRect: result.preciseDisplayInfoRect || false,
            coarseHitAreaHeadRect: result.coarseHitAreaHeadRect || false,
            overrideSignature: this._getBubbleGeometryOverrideSignature(),
            headRect,
            bubbleHeadRect,
            bodyRect,
            rawHeadAnchor,
            headAnchor
        };
    }

    _getCachedBubbleGeometryResult() {
        const cache = this._bubbleGeometryCache;
        if (!cache) { return null; }
        if (cache.modelPath !== this.modelRootPath) {
            this._bubbleGeometryCache = null;
            return null;
        }
        if (cache.overrideSignature !== this._getBubbleGeometryOverrideSignature()) {
            this._bubbleGeometryCache = null;
            return null;
        }
        const bounds = this.getModelScreenBounds();
        if (!bounds) { return null; }
        const mlr = cache.modelLogicalRect;
        const headRect = cache.headRect ? this._logicalRectToScreenRect(cache.headRect, mlr, bounds) : null;
        const bubbleHeadRect = cache.bubbleHeadRect ? this._logicalRectToScreenRect(cache.bubbleHeadRect, mlr, bounds) : null;
        const bodyRect = cache.bodyRect ? this._logicalRectToScreenRect(cache.bodyRect, mlr, bounds) : null;
        const rawHeadAnchor = cache.rawHeadAnchor ? this._logicalPointToScreen(cache.rawHeadAnchor, mlr, bounds) : null;
        const headAnchor = cache.headAnchor ? this._logicalPointToScreen(cache.headAnchor, mlr, bounds) : null;
        return {
            bounds,
            rawHeadAnchor,
            headAnchor,
            headRect,
            bubbleHeadRect: bubbleHeadRect || headRect,
            headMode: cache.headMode,
            headSource: cache.headSource,
            bodyRect,
            bodySource: cache.bodySource,
            reliableHeadRect: cache.reliableHeadRect,
            preciseDisplayInfoRect: cache.preciseDisplayInfoRect,
            coarseHitAreaHeadRect: cache.coarseHitAreaHeadRect
        };
    }

    _invalidateBubbleGeometryCache() {
        this._bubbleGeometryCache = null;
    }

    _getDrawableScreenRect(drawableIndex, modelLogicalRect = null, modelBounds = null, skipTransformSync = false) {
        const directScreenRect = this._getDrawableDirectScreenRect(drawableIndex, skipTransformSync);
        if (directScreenRect) {
            return directScreenRect;
        }

        const logicalRect = this._getDrawableLogicalRect(drawableIndex);
        const resolvedModelLogicalRect = modelLogicalRect || this._getModelLogicalRect();
        const resolvedModelBounds = modelBounds || this.getModelScreenBounds();
        const mappedRect = this._mapLogicalRectToScreen(logicalRect, resolvedModelLogicalRect, resolvedModelBounds);
        if (!mappedRect) {
            return null;
        }

        return this._createScreenRect(
            mappedRect.left,
            mappedRect.top,
            mappedRect.left + mappedRect.width,
            mappedRect.top + mappedRect.height
        );
    }

    _mergeScreenRects(rects) {
        if (!Array.isArray(rects) || rects.length === 0) {
            return null;
        }

        let minX = Infinity;
        let maxX = -Infinity;
        let minY = Infinity;
        let maxY = -Infinity;

        for (const rect of rects) {
            if (!rect) continue;
            minX = Math.min(minX, rect.left);
            maxX = Math.max(maxX, rect.right);
            minY = Math.min(minY, rect.top);
            maxY = Math.max(maxY, rect.bottom);
        }

        if (!Number.isFinite(minX) || !Number.isFinite(maxX) ||
            !Number.isFinite(minY) || !Number.isFinite(maxY)) {
            return null;
        }

        return this._createScreenRect(minX, minY, maxX, maxY);
    }

    _getRenderableDrawableScreenRects(modelBounds = null, modelLogicalRect = null, includeIndex = false) {
        const internalModel = this.currentModel?.internalModel;
        const coreModel = internalModel?.coreModel;
        const drawableCount = coreModel?.getDrawableCount?.();
        const resolvedModelBounds = modelBounds || this.getModelScreenBounds();
        const resolvedModelLogicalRect = modelLogicalRect || this._getModelLogicalRect();
        if (!internalModel || !coreModel || !Number.isInteger(drawableCount) || drawableCount <= 0 ||
            !resolvedModelBounds || !resolvedModelLogicalRect) {
            return [];
        }

        this._ensureModelWorldTransform();

        const rects = [];
        for (let index = 0; index < drawableCount; index += 1) {
            if (!this._isDrawableRenderable(coreModel, index)) {
                continue;
            }

            const rect = this._getDrawableScreenRect(
                index,
                resolvedModelLogicalRect,
                resolvedModelBounds,
                true
            );
            if (rect) {
                rects.push(includeIndex ? { rect, index } : rect);
            }
        }

        return rects;
    }

    _expandScreenRect(rect, paddingX = 0, paddingY = 0) {
        if (!rect) {
            return null;
        }

        return this._createScreenRect(
            rect.left - paddingX,
            rect.top - paddingY,
            rect.right + paddingX,
            rect.bottom + paddingY
        );
    }

    _getScreenRectArea(rect) {
        if (!rect) {
            return 0;
        }

        const width = Number(rect.width);
        const height = Number(rect.height);
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return 0;
        }

        return width * height;
    }

    _getWeightedValueQuantile(samples, quantile = 0.5) {
        if (!Array.isArray(samples) || samples.length === 0) {
            return null;
        }

        const resolvedQuantile = Math.max(0, Math.min(1, Number.isFinite(quantile) ? quantile : 0.5));
        const validSamples = samples
            .map((sample) => ({
                value: Number(sample?.value),
                weight: Number(sample?.weight)
            }))
            .filter((sample) => Number.isFinite(sample.value) && Number.isFinite(sample.weight) && sample.weight > 0)
            .sort((left, right) => left.value - right.value);
        if (validSamples.length === 0) {
            return null;
        }

        const totalWeight = validSamples.reduce((sum, sample) => sum + sample.weight, 0);
        if (!(totalWeight > 0)) {
            return validSamples[Math.min(
                validSamples.length - 1,
                Math.max(0, Math.round((validSamples.length - 1) * resolvedQuantile))
            )].value;
        }

        const targetWeight = totalWeight * resolvedQuantile;
        let accumulatedWeight = 0;
        for (const sample of validSamples) {
            accumulatedWeight += sample.weight;
            if (accumulatedWeight >= targetWeight) {
                return sample.value;
            }
        }

        return validSamples[validSamples.length - 1].value;
    }

    _createContributorCoreScreenRect(contributors, quantiles = {}) {
        if (!Array.isArray(contributors) || contributors.length === 0) {
            return null;
        }

        const resolvedContributors = contributors
            .map((contributor) => {
                const rect = contributor?.rect || contributor;
                const area = this._getScreenRectArea(rect);
                const scoreWeight = Number.isFinite(contributor?.score) ? contributor.score : 1;
                const weight = area * Math.max(0.25, scoreWeight);
                return rect && weight > 0
                    ? { rect, weight }
                    : null;
            })
            .filter(Boolean);
        if (resolvedContributors.length === 0) {
            return null;
        }

        const left = this._getWeightedValueQuantile(
            resolvedContributors.map((contributor) => ({
                value: contributor.rect.left,
                weight: contributor.weight
            })),
            quantiles.left ?? 0.1
        );
        const top = this._getWeightedValueQuantile(
            resolvedContributors.map((contributor) => ({
                value: contributor.rect.top,
                weight: contributor.weight
            })),
            quantiles.top ?? 0.1
        );
        const right = this._getWeightedValueQuantile(
            resolvedContributors.map((contributor) => ({
                value: contributor.rect.right,
                weight: contributor.weight
            })),
            quantiles.right ?? 0.9
        );
        const bottom = this._getWeightedValueQuantile(
            resolvedContributors.map((contributor) => ({
                value: contributor.rect.bottom,
                weight: contributor.weight
            })),
            quantiles.bottom ?? 0.9
        );

        return this._createScreenRect(left, top, right, bottom);
    }

    _extractDrawableHeadContributorCoreScreenRect(rect, modelBounds, bodyRectHint, contributors = null) {
        const bodyRect = bodyRectHint?.rect || bodyRectHint;
        if (!rect || !modelBounds || !bodyRect ||
            !Array.isArray(contributors) || contributors.length < 6) {
            return rect;
        }

        const headLooksAccessoryInflated = rect.width >= modelBounds.width * 0.42 &&
            rect.height >= modelBounds.height * 0.2 &&
            rect.top <= modelBounds.top + modelBounds.height * 0.18 &&
            rect.bottom <= modelBounds.top + modelBounds.height * 0.58 &&
            (rect.width / Math.max(1, rect.height)) >= 1.32 &&
            Math.abs(rect.centerX - bodyRect.centerX) >= Math.max(40, modelBounds.width * 0.08) &&
            (
                bodyRect.top >= rect.top + rect.height * 0.38 ||
                (bodyRect.width >= modelBounds.width * 0.68 &&
                    bodyRect.height >= modelBounds.height * 0.68)
            );
        if (!headLooksAccessoryInflated) {
            return rect;
        }

        const contributorCoreRect = this._createContributorCoreScreenRect(contributors, {
            // Later left trimming helps reject tiny top-left accessories/hair
            // fragments without disturbing the main head mass.
            left: 0.18,
            top: 0.1,
            right: 0.94,
            bottom: 0.96
        });
        if (!contributorCoreRect) {
            return rect;
        }

        const widthRatio = contributorCoreRect.width / Math.max(1, rect.width);
        const heightRatio = contributorCoreRect.height / Math.max(1, rect.height);
        const bodyDeltaBefore = Math.abs(rect.centerX - bodyRect.centerX);
        const bodyDeltaAfter = Math.abs(contributorCoreRect.centerX - bodyRect.centerX);
        const staysInUpperHeadBand = contributorCoreRect.top <= modelBounds.top + modelBounds.height * 0.24 &&
            contributorCoreRect.bottom <= modelBounds.top + modelBounds.height * 0.62;
        if (!staysInUpperHeadBand ||
            widthRatio < 0.6 ||
            heightRatio < 0.68 ||
            bodyDeltaAfter > bodyDeltaBefore * 0.86) {
            return rect;
        }

        return contributorCoreRect;
    }

    _extractDrawableBodyContributorCoreScreenRect(rect, modelBounds, headRectHint, contributors = null) {
        const headRect = headRectHint?.rect || headRectHint;
        if (!rect || !modelBounds || !headRect ||
            !Array.isArray(contributors) || contributors.length < 9) {
            return rect;
        }

        const bodyLooksAccessoryInflated = rect.width >= modelBounds.width * 0.72 &&
            rect.height >= modelBounds.height * 0.48 &&
            rect.width >= headRect.width * 1.48 &&
            rect.top <= headRect.bottom + Math.max(36, headRect.height * 0.42);
        if (!bodyLooksAccessoryInflated) {
            return rect;
        }

        const contributorCoreRect = this._createContributorCoreScreenRect(contributors, {
            left: 0.12,
            top: 0.08,
            right: 0.93,
            bottom: 0.96
        });
        if (!contributorCoreRect) {
            return rect;
        }

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const minWidth = Math.max(headRect.width * 1.18, modelBounds.width * 0.24);
        const minHeight = Math.max(headRect.height * 1.15, modelBounds.height * 0.24);
        let left = Math.max(rect.left, contributorCoreRect.left);
        let right = Math.min(rect.right, contributorCoreRect.right);
        let bottom = Math.min(rect.bottom, Math.max(rect.top + minHeight, contributorCoreRect.bottom));

        if (right - left < minWidth) {
            const preferredCenterX = Number.isFinite(contributorCoreRect.centerX)
                ? contributorCoreRect.centerX
                : rect.centerX;
            left = clamp(preferredCenterX - minWidth * 0.5, rect.left, rect.right - minWidth);
            right = left + minWidth;
        }

        if (bottom - rect.top < minHeight) {
            bottom = Math.min(rect.bottom, rect.top + minHeight);
        }

        const normalizedRect = this._createScreenRect(
            left,
            rect.top,
            right,
            bottom
        );
        if (!normalizedRect) {
            return rect;
        }

        const widthRatio = normalizedRect.width / Math.max(1, rect.width);
        const heightRatio = normalizedRect.height / Math.max(1, rect.height);
        if (widthRatio < 0.55 || heightRatio < 0.68) {
            return rect;
        }

        return normalizedRect;
    }

    _shouldIgnoreBodyRectHintForHeadNormalization(rect, modelBounds, bodyRectHint) {
        if (!rect || !modelBounds || !bodyRectHint) {
            return false;
        }

        const bodyStartsNearHeadTop = bodyRectHint.top <= rect.top + Math.max(24, rect.height * 0.16);
        const bodyHintCoversMostModel = bodyRectHint.width >= modelBounds.width * 0.68 &&
            bodyRectHint.height >= modelBounds.height * 0.68;
        const headClusterAlreadyLarge = rect.width >= modelBounds.width * 0.34 &&
            rect.height >= modelBounds.height * 0.24;
        const headClusterLivesInUpperBand = rect.top <= modelBounds.top + modelBounds.height * 0.16 &&
            rect.bottom <= modelBounds.top + modelBounds.height * 0.54;
        const bodyHintIsMuchLargerThanHead = bodyRectHint.width >= rect.width * 1.45 &&
            bodyRectHint.height >= rect.height * 1.6;

        return bodyStartsNearHeadTop &&
            bodyHintCoversMostModel &&
            headClusterAlreadyLarge &&
            headClusterLivesInUpperBand &&
            bodyHintIsMuchLargerThanHead;
    }

    _normalizeDrawableHeadScreenRect(rect, modelBounds, bodyRectHint = null, headRectHint = null, contributors = null) {
        if (!rect || !modelBounds) {
            return rect;
        }

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const effectiveBodyRectHint = this._shouldIgnoreBodyRectHintForHeadNormalization(
            rect,
            modelBounds,
            bodyRectHint
        )
            ? null
            : bodyRectHint;
        let normalizedRect = rect;

        let bottomCap = modelBounds.top + modelBounds.height * 0.56;
        if (effectiveBodyRectHint) {
            bottomCap = Math.min(bottomCap, effectiveBodyRectHint.top + effectiveBodyRectHint.height * 0.36);
        }

        const minHeadHeight = Math.max(24, modelBounds.height * 0.08);
        if (normalizedRect.bottom > bottomCap && bottomCap > normalizedRect.top + minHeadHeight) {
            normalizedRect = this._createScreenRect(
                normalizedRect.left,
                normalizedRect.top,
                normalizedRect.right,
                bottomCap
            ) || normalizedRect;
        }

        const finalWidthRatio = normalizedRect.width / Math.max(1, modelBounds.width);
        const finalHeightRatio = normalizedRect.height / Math.max(1, modelBounds.height);
        const finalAspectRatio = normalizedRect.width / Math.max(1, normalizedRect.height);
        const stillLooksLikeWideBand = finalWidthRatio >= 0.44 &&
            finalHeightRatio <= 0.24 &&
            finalAspectRatio >= 2.4;
        if (stillLooksLikeWideBand && effectiveBodyRectHint) {
            const normalizedHeight = clamp(
                Math.max(
                    normalizedRect.height,
                    effectiveBodyRectHint.height * 0.18
                ),
                Math.max(32, modelBounds.height * 0.1),
                effectiveBodyRectHint.height * 0.34
            );
            const normalizedWidth = clamp(
                Math.max(
                    normalizedHeight * 1.05,
                    effectiveBodyRectHint.width * 0.26
                ),
                Math.max(56, modelBounds.width * 0.14),
                effectiveBodyRectHint.width * 0.42
            );
            const clampNormalizedCenterX = (value) => clamp(
                value,
                effectiveBodyRectHint.left + normalizedWidth * 0.5,
                effectiveBodyRectHint.right - normalizedWidth * 0.5
            );
            const hintedCenterX = Number.isFinite(headRectHint?.centerX)
                ? clampNormalizedCenterX(headRectHint.centerX)
                : null;
            const bodyBiasThreshold = modelBounds.width * 0.04;
            const bodyBias = effectiveBodyRectHint.centerX >= (
                (Number.isFinite(modelBounds.centerX) ? modelBounds.centerX : modelBounds.left + modelBounds.width * 0.5) +
                bodyBiasThreshold
            )
                ? 'right'
                : effectiveBodyRectHint.centerX <= (
                    (Number.isFinite(modelBounds.centerX) ? modelBounds.centerX : modelBounds.left + modelBounds.width * 0.5) -
                    bodyBiasThreshold
                )
                    ? 'left'
                    : 'center';
            let normalizedCenterX = clampNormalizedCenterX(normalizedRect.centerX);
            const shouldUseHeadHint = Number.isFinite(hintedCenterX) &&
                Math.abs(hintedCenterX - normalizedRect.centerX) >= normalizedWidth * 0.38;
            if (shouldUseHeadHint) {
                normalizedCenterX = hintedCenterX;
            } else if (bodyBias === 'right') {
                normalizedCenterX = clampNormalizedCenterX(
                    Math.min(
                        normalizedRect.right - normalizedWidth * 0.45,
                        effectiveBodyRectHint.right - normalizedWidth * 0.48
                    )
                );
            } else if (bodyBias === 'left') {
                normalizedCenterX = clampNormalizedCenterX(
                    Math.max(
                        normalizedRect.left + normalizedWidth * 0.45,
                        effectiveBodyRectHint.left + normalizedWidth * 0.48
                    )
                );
            }

            const normalizedTop = Math.min(
                normalizedRect.top,
                effectiveBodyRectHint.top + effectiveBodyRectHint.height * 0.16
            );
            normalizedRect = this._createScreenRect(
                normalizedCenterX - normalizedWidth * 0.5,
                normalizedTop,
                normalizedCenterX + normalizedWidth * 0.5,
                normalizedTop + normalizedHeight
            ) || normalizedRect;
        }

        if (effectiveBodyRectHint) {
            const bodyWidthRatio = normalizedRect.width / Math.max(1, effectiveBodyRectHint.width);
            const bodyHeightRatio = normalizedRect.height / Math.max(1, effectiveBodyRectHint.height);
            const bodyBottomProgress = (normalizedRect.bottom - effectiveBodyRectHint.top) / Math.max(1, effectiveBodyRectHint.height);
            const boundsWidthRatio = normalizedRect.width / Math.max(1, modelBounds.width);
            const aspectRatio = normalizedRect.width / Math.max(1, normalizedRect.height);
            const looksLikeOversizedBodySlice = bodyBottomProgress >= 0.3 && (
                bodyWidthRatio >= 0.56 ||
                bodyHeightRatio >= 0.38 ||
                boundsWidthRatio >= 0.46 ||
                (aspectRatio >= 1.55 && bodyWidthRatio >= 0.44)
            );

            if (looksLikeOversizedBodySlice) {
                const minNormalizedHeight = Math.max(
                    64,
                    modelBounds.height * 0.1,
                    effectiveBodyRectHint.height * 0.18
                );
                const maxNormalizedHeight = Math.max(
                    minNormalizedHeight + 8,
                    effectiveBodyRectHint.height * 0.32
                );
                const normalizedHeight = clamp(
                    Math.max(
                        effectiveBodyRectHint.height * 0.2,
                        normalizedRect.height * 0.58
                    ),
                    minNormalizedHeight,
                    maxNormalizedHeight
                );
                const minNormalizedWidth = Math.max(
                    76,
                    modelBounds.width * 0.12,
                    effectiveBodyRectHint.width * 0.22
                );
                const maxNormalizedWidth = Math.max(
                    minNormalizedWidth + 12,
                    effectiveBodyRectHint.width * 0.44
                );
                let normalizedWidth = Math.max(
                    effectiveBodyRectHint.width * 0.28,
                    normalizedRect.width * 0.4,
                    normalizedHeight * 0.82
                );
                if (aspectRatio >= 1.55) {
                    normalizedWidth = Math.min(normalizedWidth, normalizedHeight * 1.22);
                }
                normalizedWidth = clamp(
                    normalizedWidth,
                    minNormalizedWidth,
                    maxNormalizedWidth
                );

                const normalizedCenterX = clamp(
                    Number.isFinite(headRectHint?.centerX) ? headRectHint.centerX : normalizedRect.centerX,
                    effectiveBodyRectHint.left + normalizedWidth * 0.5,
                    effectiveBodyRectHint.right - normalizedWidth * 0.5
                );
                const normalizedTop = clamp(
                    Math.min(
                        normalizedRect.top,
                        effectiveBodyRectHint.top + effectiveBodyRectHint.height * 0.08
                    ),
                    modelBounds.top,
                    effectiveBodyRectHint.top + effectiveBodyRectHint.height * 0.14
                );

                normalizedRect = this._createScreenRect(
                    normalizedCenterX - normalizedWidth * 0.5,
                    normalizedTop,
                    normalizedCenterX + normalizedWidth * 0.5,
                    normalizedTop + normalizedHeight
                ) || normalizedRect;
            }
        }

        const looksLikeTinyFragment = effectiveBodyRectHint &&
            Number.isFinite(headRectHint?.centerX) &&
            Number.isFinite(headRectHint?.centerY) &&
            (
                normalizedRect.width <= Math.max(40, effectiveBodyRectHint.width * 0.14) ||
                normalizedRect.height <= Math.max(40, effectiveBodyRectHint.height * 0.14)
            ) &&
            headRectHint.centerY >= normalizedRect.bottom + Math.max(28, normalizedRect.height * 0.55);
        if (looksLikeTinyFragment) {
            const normalizedHeight = clamp(
                Math.max(
                    normalizedRect.height * 2.2,
                    effectiveBodyRectHint.height * 0.18
                ),
                Math.max(56, modelBounds.height * 0.11),
                effectiveBodyRectHint.height * 0.32
            );
            const normalizedWidth = clamp(
                Math.max(
                    normalizedHeight * 0.9,
                    normalizedRect.width * 2.4,
                    effectiveBodyRectHint.width * 0.16
                ),
                Math.max(64, modelBounds.width * 0.12),
                effectiveBodyRectHint.width * 0.28
            );
            const normalizedCenterX = clamp(
                headRectHint.centerX,
                effectiveBodyRectHint.left + normalizedWidth * 0.5,
                effectiveBodyRectHint.right - normalizedWidth * 0.5
            );
            const normalizedCenterY = clamp(
                headRectHint.centerY - normalizedHeight * 0.18,
                modelBounds.top + normalizedHeight * 0.5,
                modelBounds.bottom - normalizedHeight * 0.5
            );
            normalizedRect = this._createScreenRect(
                normalizedCenterX - normalizedWidth * 0.5,
                normalizedCenterY - normalizedHeight * 0.5,
                normalizedCenterX + normalizedWidth * 0.5,
                normalizedCenterY + normalizedHeight * 0.5
            ) || normalizedRect;
        }

        normalizedRect = this._extractDrawableHeadContributorCoreScreenRect(
            normalizedRect,
            modelBounds,
            bodyRectHint || effectiveBodyRectHint,
            contributors
        );

        return normalizedRect;
    }

    _normalizeDrawableBodyScreenRect(rect, modelBounds, headRectHint = null, contributors = null) {
        if (!rect || !modelBounds || !headRectHint) {
            return rect;
        }

        const headRect = headRectHint?.rect || headRectHint;
        if (!headRect) {
            return rect;
        }

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        let normalizedRect = rect;
        const bodyStartsInsideHead = rect.top <= headRect.top + Math.max(24, headRect.height * 0.18);
        const bodySpansMostOfModel = rect.width >= modelBounds.width * 0.58 &&
            rect.height >= modelBounds.height * 0.62;
        const bodyClearlyLargerThanHead = rect.width >= headRect.width * 1.18 &&
            rect.height >= headRect.height * 1.45;

        if (bodyStartsInsideHead && bodySpansMostOfModel && bodyClearlyLargerThanHead) {
            const minHeight = Math.max(72, rect.height * 0.24, headRect.height * 0.9);
            const nextTop = clamp(
                headRect.top + headRect.height * 0.58,
                rect.top,
                rect.bottom - minHeight
            );

            if (nextTop > rect.top + Math.max(20, headRect.height * 0.12)) {
                normalizedRect = this._createScreenRect(
                    rect.left,
                    nextTop,
                    rect.right,
                    rect.bottom
                ) || rect;
            }
        }

        normalizedRect = this._extractDrawableBodyContributorCoreScreenRect(
            normalizedRect,
            modelBounds,
            headRect,
            contributors
        );

        return normalizedRect;
    }

    _inferDrawableRegionScreenRectInfo(kind, modelBounds = null, modelLogicalRect = null, bodyRectHint = null, headRectHint = null) {
        const resolvedModelBounds = modelBounds || this.getModelScreenBounds();
        const resolvedModelLogicalRect = modelLogicalRect || this._getModelLogicalRect();
        const drawableEntries = this._getRenderableDrawableScreenRects(
            resolvedModelBounds,
            resolvedModelLogicalRect,
            true
        );
        if (!resolvedModelBounds || drawableEntries.length === 0) {
            return null;
        }

        const boundsCenterX = Number.isFinite(resolvedModelBounds.centerX)
            ? resolvedModelBounds.centerX
            : resolvedModelBounds.left + resolvedModelBounds.width * 0.5;
        const modelArea = Math.max(1, Number(resolvedModelBounds.width) * Number(resolvedModelBounds.height));
        const targetRect = kind === 'head'
            ? this._createScreenRect(
                boundsCenterX - resolvedModelBounds.width * 0.34,
                resolvedModelBounds.top - resolvedModelBounds.height * 0.02,
                boundsCenterX + resolvedModelBounds.width * 0.34,
                resolvedModelBounds.top + resolvedModelBounds.height * 0.52
            )
            : this._createScreenRect(
                boundsCenterX - resolvedModelBounds.width * 0.38,
                resolvedModelBounds.top + resolvedModelBounds.height * 0.16,
                boundsCenterX + resolvedModelBounds.width * 0.38,
                resolvedModelBounds.top + resolvedModelBounds.height * 0.88
            );
        if (!targetRect) {
            return null;
        }

        const rectArea = (rect) => Math.max(1, Number(rect?.width) * Number(rect?.height));
        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const clamp01 = (value) => clamp(value, 0, 1);
        const tokenizePartId = (text) => {
            if (!text) {
                return [];
            }

            return String(text)
                .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
                .replace(/[^A-Za-z0-9]+/g, ' ')
                .toLowerCase()
                .trim()
                .split(/\s+/)
                .filter(Boolean);
        };
        const tokenMatchesKeyword = (token, keyword) => {
            if (!token || !keyword) {
                return false;
            }

            if (token === keyword) {
                return true;
            }

            if (token.startsWith(keyword) || token.endsWith(keyword)) {
                return token.length <= keyword.length + 4;
            }

            return false;
        };
        const matchesAnyPartKeyword = (text, keywords) => {
            if (!text || !Array.isArray(keywords) || keywords.length === 0) {
                return false;
            }

            const normalizedText = String(text).toLowerCase();
            const tokens = tokenizePartId(text);

            for (const keyword of keywords) {
                if (!keyword) {
                    continue;
                }

                const normalizedKeyword = String(keyword).toLowerCase();
                if (!normalizedKeyword) {
                    continue;
                }

                // CJK (or other non-latin) labels are typically not tokenized; use direct contains.
                if (!/^[a-z0-9]+$/.test(normalizedKeyword)) {
                    if (normalizedText.includes(normalizedKeyword)) {
                        return true;
                    }
                    continue;
                }

                for (const token of tokens) {
                    if (tokenMatchesKeyword(token, normalizedKeyword)) {
                        return true;
                    }
                }
            }

            return false;
        };

        let drawableParentPartIds = null;
        if (kind === 'head') {
            const internalModel = this.currentModel?.internalModel;
            const coreModel = internalModel?.coreModel;
            drawableParentPartIds = this._getDrawableParentPartIdLookup(coreModel);
        }

        const nonHeadPartKeywords = [
            'arm', 'hand', 'leg', 'foot', 'shoe',
            'tuba', 'trumpet', 'flute', 'drum', 'guitar', 'violin', 'piano', 'horn',
            'sword', 'gun', 'staff', 'wand', 'shield', 'spear', 'bow', 'axe',
            'weapon', 'instrument',
            'tail', 'wing'
        ];
        const facePartKeywords = [
            'face', 'head', 'eye', 'eyebrow', 'brow', 'mouth', 'lip', 'nose', 'ear', 'cheek', 'blush', 'tear',
            '脸', '脸部', '面', '头', '眼', '眉', '嘴', '口', '鼻', '耳',
            '顔', '頭', '目', '眉', '口', '鼻', '耳'
        ];
        const hairPartKeywords = [
            'hair', 'bang', 'fringe', 'ahoge', 'ponytail', 'braid', 'curl', 'sidehair', 'fronthair', 'backhair',
            '发', '髮', '刘海', '耳发', '髪', '前髪', '後ろ髪', '横髪'
        ];
        const headCorePartKeywords = [
            'head', 'face', 'neck', 'chin', 'jaw', 'forehead',
            '头', '脸', '颈', '脖',
            '頭', '顔', '首'
        ];
        const accessoryPartKeywords = [
            'halo', 'horn', 'crown', 'hat', 'cap', 'ribbon', 'bow', 'ornament', 'clip', 'flower', 'veil', 'headdress',
            '光环', '角', '冠', '发饰', '饰品', '蝴蝶结', '花',
            '輪', '角', '冠', '飾', 'リボン', '花'
        ];

        const candidates = drawableEntries
            .map((entry) => {
                const rect = entry?.rect || entry;
                if (!rect) {
                    return null;
                }
                const drawableIndex = Number.isInteger(entry?.index) ? entry.index : null;
                const area = rectArea(rect);
                const overlapArea = this._getRectIntersectionArea(rect, targetRect);
                const overlapRatio = overlapArea / area;
                const widthRatio = rect.width / Math.max(1, resolvedModelBounds.width);
                const heightRatio = rect.height / Math.max(1, resolvedModelBounds.height);
                const aspectRatio = rect.width / Math.max(1, rect.height);
                const centerBias = clamp01(1 - Math.abs(rect.centerX - boundsCenterX) / Math.max(1, resolvedModelBounds.width * 0.48));
                const verticalTargetY = kind === 'head'
                    ? resolvedModelBounds.top + resolvedModelBounds.height * 0.24
                    : resolvedModelBounds.top + resolvedModelBounds.height * 0.53;
                const verticalBand = kind === 'head'
                    ? resolvedModelBounds.height * 0.26
                    : resolvedModelBounds.height * 0.34;
                const verticalBias = clamp01(1 - Math.abs(rect.centerY - verticalTargetY) / Math.max(1, verticalBand));
                const areaRatio = area / modelArea;
                const partId = Array.isArray(drawableParentPartIds) && Number.isInteger(drawableIndex)
                    ? (drawableParentPartIds[drawableIndex] || '')
                    : '';

                if (kind === 'head') {
                    const wideShallowBand = widthRatio >= 0.44 &&
                        heightRatio <= 0.24 &&
                        aspectRatio >= 2.4;
                    if (areaRatio < 0.001 || areaRatio > 0.26 ||
                        overlapRatio < 0.16 ||
                        rect.centerY > resolvedModelBounds.top + resolvedModelBounds.height * 0.54 ||
                        rect.bottom > resolvedModelBounds.top + resolvedModelBounds.height * 0.68 ||
                        rect.height > resolvedModelBounds.height * 0.48 ||
                        rect.width > resolvedModelBounds.width * 0.82 ||
                        wideShallowBand) {
                        return null;
                    }

                    if (partId && matchesAnyPartKeyword(partId, nonHeadPartKeywords)) {
                        return null;
                    }
                } else if (areaRatio < 0.002 || areaRatio > 0.7 ||
                    overlapRatio < 0.08 ||
                    rect.centerY < resolvedModelBounds.top + resolvedModelBounds.height * 0.22 ||
                    rect.centerY > resolvedModelBounds.top + resolvedModelBounds.height * 0.88 ||
                    rect.height > resolvedModelBounds.height * 0.82) {
                    return null;
                }

                const widthBias = kind === 'head'
                    ? clamp01(1 - Math.max(0, widthRatio - 0.32) / 0.34)
                    : 1;
                const aspectBias = kind === 'head'
                    ? clamp01(1 - Math.max(0, aspectRatio - 1.9) / 1.4)
                    : 1;

                let score = overlapRatio * 4.2 +
                    centerBias * 1.8 +
                    verticalBias * 1.9 +
                    widthBias * (kind === 'head' ? 1.4 : 0) +
                    aspectBias * (kind === 'head' ? 1.3 : 0);
                if (kind === 'head' && partId) {
                    if (matchesAnyPartKeyword(partId, facePartKeywords)) {
                        score += 2.4;
                    }
                    if (matchesAnyPartKeyword(partId, hairPartKeywords)) {
                        score += 1.05;
                    }
                    if (matchesAnyPartKeyword(partId, headCorePartKeywords)) {
                        score += 0.85;
                    }
                    if (matchesAnyPartKeyword(partId, accessoryPartKeywords)) {
                        score -= 0.75;
                    }
                }

                return { rect, score, drawableIndex };
            })
            .filter(Boolean);

        if (kind === 'head' && candidates.length > 1) {
            const semanticAnchors = [];
            if (Array.isArray(drawableParentPartIds) && drawableParentPartIds.length > 0) {
                for (const candidate of candidates) {
                    const partId = drawableParentPartIds[candidate.drawableIndex] || '';
                    if (!partId) {
                        continue;
                    }

                    let semanticWeight = 0;
                    if (matchesAnyPartKeyword(partId, facePartKeywords)) {
                        semanticWeight += 2.6;
                    }
                    if (matchesAnyPartKeyword(partId, hairPartKeywords)) {
                        semanticWeight += 1.2;
                    }
                    if (matchesAnyPartKeyword(partId, headCorePartKeywords)) {
                        semanticWeight += 0.9;
                    }
                    if (matchesAnyPartKeyword(partId, accessoryPartKeywords)) {
                        semanticWeight -= 1.0;
                    }

                    if (semanticWeight > 0.15) {
                        semanticAnchors.push({ rect: candidate.rect, weight: semanticWeight });
                    }
                }
            }

            if (semanticAnchors.length > 0) {
                const totalWeight = semanticAnchors.reduce((sum, item) => sum + item.weight, 0);
                const semanticCenterX = semanticAnchors.reduce((sum, item) => sum + item.rect.centerX * item.weight, 0) / Math.max(0.001, totalWeight);
                const semanticCenterY = semanticAnchors.reduce((sum, item) => sum + item.rect.centerY * item.weight, 0) / Math.max(0.001, totalWeight);
                const maxDistance = Math.max(resolvedModelBounds.width, resolvedModelBounds.height) * 0.18;

                for (const candidate of candidates) {
                    const partId = Array.isArray(drawableParentPartIds)
                        ? (drawableParentPartIds[candidate.drawableIndex] || '')
                        : '';
                    let roleWeight = 1;
                    if (partId && matchesAnyPartKeyword(partId, facePartKeywords)) {
                        roleWeight = 1.35;
                    } else if (partId && matchesAnyPartKeyword(partId, hairPartKeywords)) {
                        roleWeight = 1.12;
                    } else if (partId && matchesAnyPartKeyword(partId, headCorePartKeywords)) {
                        roleWeight = 1.0;
                    }
                    if (partId && matchesAnyPartKeyword(partId, accessoryPartKeywords)) {
                        roleWeight *= 0.72;
                    }

                    const deltaX = candidate.rect.centerX - semanticCenterX;
                    const deltaY = candidate.rect.centerY - semanticCenterY;
                    const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);
                    if (distance < maxDistance) {
                        candidate.score += 2.1 * roleWeight * (1 - distance / maxDistance);
                    }
                }
            }
        }

        candidates.sort((left, right) => right.score - left.score);
        if (candidates.length === 0) {
            return null;
        }

        let orderedCandidates = candidates;
        if (kind === 'head' && candidates.length > 1) {
            const boundsTop = resolvedModelBounds.top;
            const boundsHeight = Math.max(1, resolvedModelBounds.height);
            const bestScore = Math.max(0.01, candidates[0].score);
            const isPrimaryHeadBand = (candidateRect) =>
                candidateRect.centerY >= boundsTop + boundsHeight * 0.16 &&
                candidateRect.centerY <= boundsTop + boundsHeight * 0.56;

            // Prefer a face-tagged candidate in the central head band when available.
            let stableAnchor = null;
            if (Array.isArray(drawableParentPartIds) && drawableParentPartIds.length > 0) {
                for (const candidate of candidates) {
                    const partId = drawableParentPartIds[candidate.drawableIndex] || '';
                    if (!partId ||
                        !matchesAnyPartKeyword(partId, facePartKeywords) ||
                        !isPrimaryHeadBand(candidate.rect) ||
                        candidate.score < bestScore * 0.58) {
                        continue;
                    }

                    if (!stableAnchor || candidate.score > stableAnchor.score) {
                        stableAnchor = candidate;
                    }
                }
            }

            const topCandidate = candidates[0];
            const topLooksLikeAccessoryFragment = topCandidate.rect.centerY <= boundsTop + boundsHeight * 0.18 &&
                topCandidate.rect.height <= boundsHeight * 0.2 &&
                topCandidate.rect.width <= resolvedModelBounds.width * 0.38;
            if (!stableAnchor && topLooksLikeAccessoryFragment) {
                for (const candidate of candidates) {
                    if (!isPrimaryHeadBand(candidate.rect) || candidate.score < bestScore * 0.62) {
                        continue;
                    }

                    if (!stableAnchor ||
                        candidate.score > stableAnchor.score ||
                        (candidate.score === stableAnchor.score &&
                            rectArea(candidate.rect) > rectArea(stableAnchor.rect))) {
                        stableAnchor = candidate;
                    }
                }
            }

            if (stableAnchor && stableAnchor !== candidates[0]) {
                orderedCandidates = [stableAnchor, ...candidates.filter((candidate) => candidate !== stableAnchor)];
            }
        }

        let mergedRect = orderedCandidates[0].rect;
        const mergedCandidates = [orderedCandidates[0]];
        const bestScore = Math.max(0.01, orderedCandidates[0].score);
        const mergePaddingX = resolvedModelBounds.width * (kind === 'head' ? 0.05 : 0.1);
        const mergePaddingY = resolvedModelBounds.height * (kind === 'head' ? 0.03 : 0.08);
        const anchorCenterX = kind === 'head' ? orderedCandidates[0].rect.centerX : null;
        const anchorCenterY = kind === 'head' ? orderedCandidates[0].rect.centerY : null;

        for (const candidate of orderedCandidates.slice(1)) {
            if (kind === 'head' && candidate.score < bestScore * 0.72) {
                continue;
            }

            if (kind === 'head' &&
                Number.isFinite(anchorCenterX) &&
                Number.isFinite(anchorCenterY)) {
                const deltaXFromAnchor = Math.abs(candidate.rect.centerX - anchorCenterX);
                const deltaYFromAnchor = Math.abs(candidate.rect.centerY - anchorCenterY);
                if (deltaXFromAnchor > resolvedModelBounds.width * 0.18 ||
                    deltaYFromAnchor > resolvedModelBounds.height * 0.14) {
                    continue;
                }
            }

            const expandedMergedRect = this._expandScreenRect(mergedRect, mergePaddingX, mergePaddingY);
            const overlapsMerged = this._getRectIntersectionArea(candidate.rect, expandedMergedRect) > 0;
            const verticallyAdjacent = candidate.rect.top <= mergedRect.bottom + mergePaddingY &&
                candidate.rect.bottom >= mergedRect.top - mergePaddingY;
            const centerReferenceX = kind === 'head' && Number.isFinite(anchorCenterX)
                ? anchorCenterX
                : mergedRect.centerX;
            const centeredEnough = Math.abs(candidate.rect.centerX - centerReferenceX) <=
                resolvedModelBounds.width * (kind === 'head' ? 0.14 : 0.28);
            if (!overlapsMerged && !(verticallyAdjacent && centeredEnough)) {
                continue;
            }

            const nextMergedRect = this._mergeScreenRects([mergedRect, candidate.rect]);
            if (!nextMergedRect) {
                continue;
            }

            if (kind === 'head') {
                if (nextMergedRect.width > resolvedModelBounds.width * 0.62 ||
                    nextMergedRect.height > resolvedModelBounds.height * 0.42 ||
                    nextMergedRect.bottom > resolvedModelBounds.top + resolvedModelBounds.height * 0.64) {
                    continue;
                }
            } else if (nextMergedRect.width > resolvedModelBounds.width * 0.82 ||
                nextMergedRect.height > resolvedModelBounds.height * 0.86) {
                continue;
            }

            mergedRect = nextMergedRect;
            mergedCandidates.push(candidate);
        }

        if (kind === 'head') {
            mergedRect = this._normalizeDrawableHeadScreenRect(
                mergedRect,
                resolvedModelBounds,
                bodyRectHint,
                headRectHint,
                mergedCandidates
            );
        } else if (kind === 'body') {
            mergedRect = this._normalizeDrawableBodyScreenRect(
                mergedRect,
                resolvedModelBounds,
                headRectHint,
                mergedCandidates
            );
        }

        return this._createRectInfoFromScreenRect(
            mergedRect,
            kind === 'head' ? 'face' : 'body',
            'drawableHeuristic'
        );
    }

    _getCoreModelSequence(coreModel, methodNames = [], propertyNames = []) {
        if (!coreModel) {
            return [];
        }

        for (const methodName of methodNames) {
            const getter = coreModel?.[methodName];
            if (typeof getter !== 'function') {
                continue;
            }

            try {
                const value = getter.call(coreModel);
                if (value && typeof value.length === 'number') {
                    return value;
                }
            } catch (_) {}
        }

        for (const propertyName of propertyNames) {
            const value = coreModel?.[propertyName];
            if (value && typeof value.length === 'number') {
                return value;
            }
        }

        return [];
    }

    _getCoreModelSequenceFromIndexedGetter(coreModel, countMethodNames = [], countPropertyNames = [], itemMethodNames = []) {
        if (!coreModel || !Array.isArray(itemMethodNames) || itemMethodNames.length === 0) {
            return [];
        }

        let count = null;

        for (const methodName of countMethodNames) {
            const getter = coreModel?.[methodName];
            if (typeof getter !== 'function') {
                continue;
            }

            try {
                const value = Number(getter.call(coreModel));
                if (Number.isInteger(value) && value > 0) {
                    count = value;
                    break;
                }
            } catch (_) {}
        }

        if (!Number.isInteger(count) || count <= 0) {
            for (const propertyName of countPropertyNames) {
                const value = Number(coreModel?.[propertyName]);
                if (Number.isInteger(value) && value > 0) {
                    count = value;
                    break;
                }
            }
        }

        if (!Number.isInteger(count) || count <= 0) {
            return [];
        }

        for (const methodName of itemMethodNames) {
            const getter = coreModel?.[methodName];
            if (typeof getter !== 'function') {
                continue;
            }

            const values = [];
            let succeeded = true;

            for (let index = 0; index < count; index += 1) {
                try {
                    values.push(getter.call(coreModel, index));
                } catch (_) {
                    succeeded = false;
                    break;
                }
            }

            if (succeeded && values.length === count) {
                return values;
            }
        }

        return [];
    }

    _getCoreModelPartIds(coreModel) {
        const directPartIds = this._getCoreModelSequence(coreModel, ['getPartIds'], [
            '_partIds',
            'partIds'
        ]);
        if (directPartIds.length > 0) {
            return directPartIds;
        }

        const indexedPartIds = this._getCoreModelSequenceFromIndexedGetter(
            coreModel,
            ['getPartCount'],
            [],
            ['getPartId']
        );
        if (indexedPartIds.length > 0) {
            return indexedPartIds;
        }

        const nestedPartIds = coreModel?._model?.parts?.ids;
        return nestedPartIds && typeof nestedPartIds.length === 'number'
            ? nestedPartIds
            : [];
    }

    _getCoreModelPartParentPartIndices(coreModel) {
        const directParentIndices = this._getCoreModelSequence(coreModel, ['getPartParentPartIndices'], [
            '_partParentPartIndices',
            'partParentPartIndices'
        ]);
        if (directParentIndices.length > 0) {
            return directParentIndices;
        }

        const indexedParentIndices = this._getCoreModelSequenceFromIndexedGetter(
            coreModel,
            ['getPartCount'],
            [],
            ['getPartParentPartIndex']
        );
        if (indexedParentIndices.length > 0) {
            return indexedParentIndices;
        }

        const nestedParentIndices = coreModel?._model?.parts?.parentPartIndices;
        return nestedParentIndices && typeof nestedParentIndices.length === 'number'
            ? nestedParentIndices
            : [];
    }

    _getCoreModelDrawableParentPartIndices(coreModel) {
        const directParentIndices = this._getCoreModelSequence(coreModel, ['getDrawableParentPartIndices'], [
            '_drawableParentPartIndices',
            'drawableParentPartIndices'
        ]);
        if (directParentIndices.length > 0) {
            return directParentIndices;
        }

        const indexedParentIndices = this._getCoreModelSequenceFromIndexedGetter(
            coreModel,
            ['getDrawableCount'],
            [],
            ['getDrawableParentPartIndex']
        );
        if (indexedParentIndices.length > 0) {
            return indexedParentIndices;
        }

        const nestedParentIndices = coreModel?._model?.drawables?.parentPartIndices;
        return nestedParentIndices && typeof nestedParentIndices.length === 'number'
            ? nestedParentIndices
            : [];
    }

    _getDrawableParentPartIdLookup(coreModel) {
        if (!coreModel) {
            return null;
        }

        const partIds = this._getCoreModelPartIds(coreModel);
        const drawableParentPartIndices = this._getCoreModelDrawableParentPartIndices(coreModel);
        if (partIds.length === 0 || drawableParentPartIndices.length === 0) {
            return null;
        }

        const normalizedPartIds = partIds.map((partId) => String(partId || '').toLowerCase());
        const lookup = new Array(drawableParentPartIndices.length);
        for (let drawableIndex = 0; drawableIndex < drawableParentPartIndices.length; drawableIndex += 1) {
            const parentPartIndex = Number(drawableParentPartIndices[drawableIndex]);
            lookup[drawableIndex] = Number.isInteger(parentPartIndex) &&
                parentPartIndex >= 0 &&
                parentPartIndex < normalizedPartIds.length
                ? normalizedPartIds[parentPartIndex]
                : '';
        }

        return lookup;
    }

    _partIndexMatchesTargetIds(partIndex, partIds, partParentIndices, targetPartIdSet) {
        if (!Number.isInteger(partIndex) || partIndex < 0 || partIndex >= partIds.length || !(targetPartIdSet instanceof Set)) {
            return false;
        }

        let currentPartIndex = partIndex;
        let depth = 0;

        while (Number.isInteger(currentPartIndex) &&
            currentPartIndex >= 0 &&
            currentPartIndex < partIds.length &&
            depth <= partIds.length) {
            const currentPartId = String(partIds[currentPartIndex] || '');
            if (currentPartId && targetPartIdSet.has(currentPartId)) {
                return true;
            }

            const nextPartIndex = Number(partParentIndices?.[currentPartIndex]);
            if (!Number.isInteger(nextPartIndex) || nextPartIndex < 0 || nextPartIndex === currentPartIndex) {
                break;
            }

            currentPartIndex = nextPartIndex;
            depth += 1;
        }

        return false;
    }

    _findDisplayInfoPartIds(patterns) {
        const displayParts = this._displayInfo?.Parts;
        if (!Array.isArray(displayParts) || !Array.isArray(patterns) || patterns.length === 0) {
            return [];
        }

        return displayParts
            .filter((part) => {
                const label = String(part?.Name || part?.Id || '');
                return patterns.some((pattern) => pattern.test(label));
            })
            .map((part) => String(part?.Id || ''))
            .filter(Boolean);
    }

    _collectDisplayInfoPartScreenRectInfo(targetPartIds, mode) {
        const internalModel = this.currentModel?.internalModel;
        const coreModel = internalModel?.coreModel;
        const drawableCount = coreModel?.getDrawableCount?.();
        const modelBounds = this.getModelScreenBounds();
        const modelLogicalRect = this._getModelLogicalRect();
        if (!internalModel || !coreModel || !Number.isInteger(drawableCount) || drawableCount <= 0 ||
            !modelBounds || !modelLogicalRect || !Array.isArray(targetPartIds) || targetPartIds.length === 0) {
            return null;
        }

        const partIds = this._getCoreModelPartIds(coreModel);
        const drawableParentPartIndices = this._getCoreModelDrawableParentPartIndices(coreModel);
        const partParentPartIndices = this._getCoreModelPartParentPartIndices(coreModel);
        if (partIds.length === 0 || drawableParentPartIndices.length === 0) {
            return null;
        }

        const targetPartIdSet = new Set(targetPartIds);
        const rects = [];

        for (let index = 0; index < drawableCount; index += 1) {
            const parentPartIndex = Number(drawableParentPartIndices[index]);
            if (!this._partIndexMatchesTargetIds(parentPartIndex, partIds, partParentPartIndices, targetPartIdSet)) {
                continue;
            }

            if (!this._isDrawableRenderable(coreModel, index)) {
                continue;
            }

            const rect = this._getDrawableScreenRect(index, modelLogicalRect, modelBounds);
            if (rect) {
                rects.push(rect);
            }
        }

        return this._createRectInfoFromScreenRect(this._mergeScreenRects(rects), mode, 'displayInfo');
    }

    _buildDisplayInfoEyeFaceRectInfo(eyeInfo, modelBounds = null) {
        const eyeRect = eyeInfo?.rect;
        const bounds = modelBounds || this.getModelScreenBounds();
        if (!eyeRect || !bounds) {
            return null;
        }

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const boundsRight = Number.isFinite(bounds.right)
            ? bounds.right
            : bounds.left + bounds.width;
        const boundsBottom = Number.isFinite(bounds.bottom)
            ? bounds.bottom
            : bounds.top + bounds.height;
        const expandedWidth = clamp(
            Math.max(
                eyeRect.width * 2.35,
                bounds.width * 0.22
            ),
            Math.max(48, eyeRect.width * 1.8),
            bounds.width * 0.56
        );
        const expandedHeight = clamp(
            Math.max(
                eyeRect.height * 3.25,
                expandedWidth * 0.86,
                bounds.height * 0.18
            ),
            Math.max(64, eyeRect.height * 2.6),
            bounds.height * 0.46
        );
        const centerX = clamp(
            Number.isFinite(eyeRect.centerX) ? eyeRect.centerX : eyeRect.left + eyeRect.width * 0.5,
            bounds.left + expandedWidth * 0.5,
            boundsRight - expandedWidth * 0.5
        );
        const top = clamp(
            eyeRect.top - Math.max(
                eyeRect.height * 1.85,
                expandedHeight * 0.38
            ),
            bounds.top,
            boundsBottom - expandedHeight
        );

        return Object.assign(
            this._createRectInfoFromScreenRect(
                this._createScreenRect(
                    centerX - expandedWidth * 0.5,
                    top,
                    centerX + expandedWidth * 0.5,
                    top + expandedHeight
                ),
                'face',
                'displayInfo'
            ),
            {
                derivedFromEyes: true,
                displayInfoSynthetic: true
            }
        );
    }

    _getDisplayInfoPartScreenRectInfo(kind) {
        if (kind === 'head') {
            const facePartIds = this._findDisplayInfoPartIds([/(^|[^a-z])face([^a-z]|$)|顔|脸/i]);
            const neckPartIds = this._findDisplayInfoPartIds([/(^|[^a-z])neck([^a-z]|$)|首/i]);
            const headPartIds = this._findDisplayInfoPartIds([/(^|[^a-z])head([^a-z]|$)|頭|头/i]);

            const faceInfo = this._collectDisplayInfoPartScreenRectInfo(
                [...new Set([...facePartIds, ...neckPartIds])],
                'face'
            );
            if (faceInfo) {
                return faceInfo;
            }

            const headInfo = this._collectDisplayInfoPartScreenRectInfo(
                [...new Set([...headPartIds, ...neckPartIds])],
                'head'
            );
            if (headInfo) {
                return headInfo;
            }

            const eyePartIds = this._findDisplayInfoPartIds([/(^|[^a-z])eye([^a-z]|$)|目|眼|瞳/i]);
            const eyeInfo = this._collectDisplayInfoPartScreenRectInfo(eyePartIds, 'face');
            if (eyeInfo) {
                return this._buildDisplayInfoEyeFaceRectInfo(
                    eyeInfo,
                    this.getModelScreenBounds()
                );
            }

            return null;
        }

        if (kind === 'body') {
            const bodyPartIds = this._findDisplayInfoPartIds([
                /(^|[^a-z])body([^a-z]|$)|身体|身體|体|胴|胴体|胸|torso|chest|upperbody|upper_body|bust/i
            ]);
            const bodyInfo = this._collectDisplayInfoPartScreenRectInfo(bodyPartIds, 'body');
            if (bodyInfo) {
                return bodyInfo;
            }

            // Some models leave "body" parts empty and attach visible torso meshes to
            // outfit parts instead. Use upper-body clothing as a fallback body proxy.
            const outfitPartIds = this._findDisplayInfoPartIds([
                /(^|[^a-z])dress([^a-z]|$)|(^|[^a-z])clothes([^a-z]|$)|(^|[^a-z])costume([^a-z]|$)|(^|[^a-z])coat([^a-z]|$)|(^|[^a-z])jacket([^a-z]|$)|(^|[^a-z])shirt([^a-z]|$)|(^|[^a-z])uniform([^a-z]|$)|(^|[^a-z])hoodie([^a-z]|$)|(^|[^a-z])jersey([^a-z]|$)|(^|[^a-z])onepiece([^a-z]|$)|(^|[^a-z])one_piece([^a-z]|$)|ワンピース|ジャージ|服|衣|上着/i
            ]);
            return this._collectDisplayInfoPartScreenRectInfo(
                [...new Set([...bodyPartIds, ...outfitPartIds])],
                'body'
            );
        }

        return null;
    }

    _normalizeHitAreaMatchKey(value) {
        return String(value || '')
            .toLowerCase()
            .replace(/[^a-z0-9\u3040-\u30ff\u3400-\u9fff]/g, '');
    }

    _looksLikeAccessoryHeadHitAreaLabel(value) {
        const rawLabel = String(value || '');
        if (!rawLabel) {
            return false;
        }

        const rawLowerLabel = rawLabel.toLowerCase();
        const normalizedLabel = this._normalizeHitAreaMatchKey(rawLabel);

        return /ornament|accessory|ribbon|bow|hair|bang|fringe|ear|horn|hat|clip|flower|headwear|headdress|hairpin|hairclip|hairband/.test(rawLowerLabel) ||
            /頭飾|头饰|发饰|發飾|髪飾|刘海|瀏海|前髪|耳|角|帽|蝴蝶结|蝴蝶結|发卡|發卡|髮卡|髮夾/.test(rawLabel) ||
            /頭飾|头饰|发饰|發飾|髪飾|刘海|瀏海|前髪|蝴蝶结|蝴蝶結/.test(normalizedLabel);
    }

    _findBestHitAreaLogicalRectInfo(matchInfoFn) {
        const model = this.currentModel;
        const internalModel = model?.internalModel;
        const hitAreaDefs = internalModel?.settings?.hitAreas;
        const hitAreas = internalModel?.hitAreas;
        if (!Array.isArray(hitAreaDefs) || !hitAreas || typeof matchInfoFn !== 'function') {
            return null;
        }

        let bestRect = null;
        let bestScore = -1;
        let bestHitArea = null;

        for (const hitAreaDef of hitAreaDefs) {
            if (!hitAreaDef) continue;

            const name = String(hitAreaDef.Name || '');
            const id = String(hitAreaDef.Id || '');
            const nameMatch = matchInfoFn(name) || {};
            const idMatch = matchInfoFn(id) || {};
            const matchInfo = Number(nameMatch.score) >= Number(idMatch.score) ? nameMatch : idMatch;
            const score = Number(matchInfo.score);
            if (!Number.isFinite(score) || score < 0) continue;

            const hitArea = hitAreas[name] || hitAreas[id];
            const drawableIndex = Number.isInteger(hitArea?.index)
                ? hitArea.index
                : internalModel.coreModel?.getDrawableIndex?.(id);
            if (!Number.isInteger(drawableIndex) || drawableIndex < 0) {
                continue;
            }

            const rect = this._getDrawableLogicalRect(drawableIndex);
            if (!rect) continue;

            if (!bestRect || score > bestScore) {
                bestRect = rect;
                bestScore = score;
                bestHitArea = {
                    id,
                    name,
                    autoNamed: this._autoNamedHitAreaIds instanceof Set && this._autoNamedHitAreaIds.has(id)
                };
            }
        }

        if (!bestRect) {
            return null;
        }

        return {
            rect: bestRect,
            id: bestHitArea?.id || null,
            name: bestHitArea?.name || null,
            autoNamed: !!bestHitArea?.autoNamed
        };
    }

    _getHeadHitAreaLogicalRectInfo() {
        return this._findBestHitAreaLogicalRectInfo((value) => {
            const key = this._normalizeHitAreaMatchKey(value);
            if (!key) return { score: -1 };
            if (this._looksLikeAccessoryHeadHitAreaLabel(value)) {
                return { score: -1 };
            }
            if (key === 'face' || key === 'hitareaface' || key === '顔' || key === '脸' || key === '脸部' || key === '面') {
                return { score: 4 };
            }
            if (key.indexOf('face') !== -1 || key.indexOf('顔') !== -1 || key.indexOf('脸') !== -1 || key.indexOf('面') !== -1) {
                return { score: 3 };
            }
            if (key === 'head' || key === 'hitareahead' || key === '頭' || key === '头') {
                return { score: 2 };
            }
            if (key.indexOf('head') !== -1 || key.indexOf('頭') !== -1 || key.indexOf('头') !== -1) {
                return { score: 1 };
            }
            return { score: -1 };
        });
    }

    _getBodyHitAreaLogicalRectInfo() {
        return this._findBestHitAreaLogicalRectInfo((value) => {
            const key = this._normalizeHitAreaMatchKey(value);
            if (!key) return { score: -1 };
            if (key === 'body' || key === 'hitareabody' || key === '身体' || key === '身體' || key === '体' || key === 'torso') {
                return { score: 4 };
            }
            if (key.indexOf('body') !== -1 || key.indexOf('身体') !== -1 || key.indexOf('身體') !== -1 ||
                key.indexOf('体') !== -1 || key.indexOf('torso') !== -1 || key.indexOf('chest') !== -1) {
                return { score: 3 };
            }
            return { score: -1 };
        });
    }

    _createHitAreaScreenRectInfo(logicalInfo, mode, modelBounds = null, modelLogicalRect = null) {
        const resolvedModelBounds = modelBounds || this.getModelScreenBounds();
        const resolvedModelLogicalRect = modelLogicalRect || this._getModelLogicalRect();
        const screenRect = this._mapLogicalRectToScreen(logicalInfo?.rect, resolvedModelLogicalRect, resolvedModelBounds);
        if (!screenRect) {
            return null;
        }

        return Object.assign(
            this._createRectInfoFromScreenRect(
                this._createScreenRect(
                    screenRect.left,
                    screenRect.top,
                    screenRect.left + screenRect.width,
                    screenRect.top + screenRect.height
                ),
                mode,
                'hitArea'
            ),
            {
                hitAreaId: logicalInfo?.id || null,
                hitAreaName: logicalInfo?.name || null,
                autoNamed: !!logicalInfo?.autoNamed
            }
        );
    }

    _getHeadHitAreaScreenRectInfo(modelBounds = null, modelLogicalRect = null) {
        return this._createHitAreaScreenRectInfo(
            this._getHeadHitAreaLogicalRectInfo(),
            'face',
            modelBounds,
            modelLogicalRect
        );
    }

    _getBodyHitAreaScreenRectInfo(modelBounds = null, modelLogicalRect = null) {
        return this._createHitAreaScreenRectInfo(
            this._getBodyHitAreaLogicalRectInfo(),
            'body',
            modelBounds,
            modelLogicalRect
        );
    }

    _getRectArea(rectInfo) {
        const rect = rectInfo?.rect || rectInfo;
        const width = Number(rect?.width);
        const height = Number(rect?.height);
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return 0;
        }
        return width * height;
    }

    _getRectIntersectionArea(rectAInfo, rectBInfo) {
        const rectA = rectAInfo?.rect || rectAInfo;
        const rectB = rectBInfo?.rect || rectBInfo;
        if (!rectA || !rectB) {
            return 0;
        }

        const left = Math.max(Number(rectA.left), Number(rectB.left));
        const top = Math.max(Number(rectA.top), Number(rectB.top));
        const right = Math.min(Number(rectA.right), Number(rectB.right));
        const bottom = Math.min(Number(rectA.bottom), Number(rectB.bottom));
        const width = right - left;
        const height = bottom - top;
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return 0;
        }

        return width * height;
    }

    _isRectInfoPlausibleWithinModel(rectInfo, modelBounds, options = {}) {
        const rect = rectInfo?.rect;
        if (!rect || !modelBounds) {
            return false;
        }

        const boundsRight = Number.isFinite(modelBounds.right)
            ? modelBounds.right
            : Number(modelBounds.left) + Number(modelBounds.width);
        const boundsBottom = Number.isFinite(modelBounds.bottom)
            ? modelBounds.bottom
            : Number(modelBounds.top) + Number(modelBounds.height);
        const toleranceX = Number.isFinite(options.toleranceX)
            ? options.toleranceX
            : Math.max(18, Number(modelBounds.width) * 0.12);
        const toleranceY = Number.isFinite(options.toleranceY)
            ? options.toleranceY
            : Math.max(18, Number(modelBounds.height) * 0.12);
        const maxWidthRatio = Number.isFinite(options.maxWidthRatio) ? options.maxWidthRatio : 1.02;
        const maxHeightRatio = Number.isFinite(options.maxHeightRatio) ? options.maxHeightRatio : 1.02;

        return rect.left >= Number(modelBounds.left) - toleranceX &&
            rect.right <= boundsRight + toleranceX &&
            rect.top >= Number(modelBounds.top) - toleranceY &&
            rect.bottom <= boundsBottom + toleranceY &&
            rect.width <= Number(modelBounds.width) * maxWidthRatio &&
            rect.height <= Number(modelBounds.height) * maxHeightRatio;
    }

    _shouldPreferDisplayInfoRect(kind, hitAreaInfo, displayInfoInfo, modelBounds) {
        if (!displayInfoInfo) {
            return false;
        }

        if (!hitAreaInfo) {
            return true;
        }

        const displayPlausible = this._isRectInfoPlausibleWithinModel(
            displayInfoInfo,
            modelBounds,
            kind === 'head'
                ? { maxWidthRatio: 0.98, maxHeightRatio: 0.88 }
                : { maxWidthRatio: 1.04, maxHeightRatio: 1.02 }
        );
        if (!displayPlausible) {
            return false;
        }

        if (hitAreaInfo.autoNamed) {
            return true;
        }

        const hitAreaArea = this._getRectArea(hitAreaInfo);
        const displayArea = this._getRectArea(displayInfoInfo);
        if (!(hitAreaArea > 0 && displayArea > 0)) {
            return false;
        }

        const overlapArea = this._getRectIntersectionArea(hitAreaInfo, displayInfoInfo);
        const overlapRatio = overlapArea / Math.max(1, Math.min(hitAreaArea, displayArea));
        const areaOversizeRatio = hitAreaArea / Math.max(displayArea, 1);
        const hitRect = hitAreaInfo.rect;
        const displayRect = displayInfoInfo.rect;
        const widthOversizeRatio = hitRect.width / Math.max(displayRect.width, 1);
        const heightOversizeRatio = hitRect.height / Math.max(displayRect.height, 1);
        const displayDerivedFromEyes = kind === 'head' && displayInfoInfo.derivedFromEyes === true;

        if (kind === 'head') {
            if (displayDerivedFromEyes) {
                const hitAreaLabelKey = this._normalizeHitAreaMatchKey(
                    `${hitAreaInfo.hitAreaName || hitAreaInfo.name || ''} ${hitAreaInfo.hitAreaId || hitAreaInfo.id || ''}`
                );
                const hitAreaLooksAccessory = /ornament|accessory|ribbon|bow|hair|ear|horn|hat|clip|flower|bang|fringe|headwear|頭飾|头饰|蝴蝶结|蝴蝶結|发饰|髪飾|耳|角|帽/.test(hitAreaLabelKey);
                const displayContainsHitAreaCenter = Number.isFinite(hitRect.centerX) &&
                    Number.isFinite(hitRect.centerY) &&
                    hitRect.centerX >= displayRect.left - 12 &&
                    hitRect.centerX <= displayRect.right + 12 &&
                    hitRect.centerY >= displayRect.top - 12 &&
                    hitRect.centerY <= displayRect.bottom + 12;
                const hitAreaClearlySmallerThanFace = hitAreaArea <= displayArea * 0.72 ||
                    hitRect.width <= displayRect.width * 0.82 ||
                    hitRect.height <= displayRect.height * 0.82;
                const hitAreaLivesInUpperFaceBand = hitRect.centerY <= displayRect.top + displayRect.height * 0.56 ||
                    hitRect.bottom <= displayRect.top + displayRect.height * 0.72;
                if (displayContainsHitAreaCenter &&
                    (hitAreaLooksAccessory || (hitAreaClearlySmallerThanFace && hitAreaLivesInUpperFaceBand))) {
                    return true;
                }
            }

            const displayClearlyInsideHitArea = overlapRatio >= 0.68 ||
                (displayRect.left >= hitRect.left - 12 &&
                    displayRect.right <= hitRect.right + 12 &&
                    displayRect.top >= hitRect.top - 12 &&
                    displayRect.bottom <= hitRect.bottom + 12);
            const hitAreaLooksCoarse = areaOversizeRatio >= 1.5 ||
                widthOversizeRatio >= 1.3 ||
                heightOversizeRatio >= 1.3 ||
                displayRect.top >= hitRect.top + Math.max(18, displayRect.height * 0.16);
            return displayClearlyInsideHitArea && hitAreaLooksCoarse;
        }

        return overlapRatio >= 0.5 && (
            areaOversizeRatio >= 1.45 ||
            widthOversizeRatio >= 1.25 ||
            heightOversizeRatio >= 1.25
        );
    }

    _isLikelyChibiRectPair(headRect, bodyRect, modelBounds) {
        const head = headRect?.rect || headRect;
        const body = bodyRect?.rect || bodyRect;
        const bounds = modelBounds?.rect || modelBounds;
        if (!head || !body || !bounds) {
            return false;
        }

        const rectsLookValid = [head, body, bounds].every((rect) =>
            Number.isFinite(rect.left) &&
            Number.isFinite(rect.top) &&
            Number.isFinite(rect.width) &&
            Number.isFinite(rect.height) &&
            rect.width > 0 &&
            rect.height > 0
        );
        if (!rectsLookValid) {
            return false;
        }

        const headCenterY = Number.isFinite(head.centerY) ? head.centerY : head.top + head.height * 0.5;
        const bodyCenterY = Number.isFinite(body.centerY) ? body.centerY : body.top + body.height * 0.5;
        if (headCenterY >= bodyCenterY) {
            return false;
        }

        const headWidthRatio = head.width / Math.max(1, bounds.width);
        const headHeightRatio = head.height / Math.max(1, bounds.height);
        const bodyWidthRatio = body.width / Math.max(1, bounds.width);
        const bodyHeightRatio = body.height / Math.max(1, bounds.height);
        const headToBodyWidthRatio = head.width / Math.max(1, body.width);
        const headToBodyHeightRatio = head.height / Math.max(1, body.height);
        const verticalGap = body.top - head.bottom;
        const maxVerticalGap = Math.max(72, head.height * 1.05, body.height * 0.34);
        const unionRect = this._mergeScreenRects([head, body]);

        return headWidthRatio >= 0.16 &&
            headWidthRatio <= 0.76 &&
            headHeightRatio >= 0.1 &&
            headHeightRatio <= 0.54 &&
            bodyWidthRatio >= 0.1 &&
            bodyWidthRatio <= 0.72 &&
            bodyHeightRatio >= 0.12 &&
            bodyHeightRatio <= 0.68 &&
            headToBodyWidthRatio >= 0.42 &&
            headToBodyHeightRatio >= 0.26 &&
            body.width <= head.width * 2.18 &&
            body.height <= head.height * 3.5 &&
            verticalGap <= maxVerticalGap &&
            body.top <= head.top + head.height * 1.55 &&
            unionRect &&
            unionRect.height <= bounds.height * 0.84;
    }

    _shouldPreferOversizedHitAreaRect(kind, hitAreaInfo, inferredInfo, modelBounds, counterpartInfo = null) {
        if (!hitAreaInfo || !inferredInfo || !modelBounds) {
            return false;
        }

        const hitRect = hitAreaInfo.rect;
        const inferredRect = inferredInfo.rect;
        if (!hitRect || !inferredRect) {
            return false;
        }

        const counterpartRect = counterpartInfo?.rect || null;
        const inferredLooksChibi = kind === 'head'
            ? this._isLikelyChibiRectPair(inferredRect, counterpartRect, modelBounds)
            : this._isLikelyChibiRectPair(counterpartRect, inferredRect, modelBounds);
        const overlapArea = this._getRectIntersectionArea(hitAreaInfo, inferredInfo);
        const overlapRatio = overlapArea / Math.max(1, Math.min(
            this._getRectArea(hitAreaInfo),
            this._getRectArea(inferredInfo)
        ));
        if (overlapRatio < (inferredLooksChibi ? 0.42 : 0.54)) {
            return false;
        }

        const areaCoverageRatio = this._getRectArea(hitAreaInfo) / Math.max(this._getRectArea(inferredInfo), 1);
        const widthCoverageRatio = hitRect.width / Math.max(inferredRect.width, 1);
        const heightCoverageRatio = hitRect.height / Math.max(inferredRect.height, 1);
        const coarseAreaThreshold = inferredLooksChibi || hitAreaInfo.autoNamed ? 1.42 : 1.9;
        const coarseWidthThreshold = inferredLooksChibi || hitAreaInfo.autoNamed ? 1.16 : 1.34;
        const coarseHeightThreshold = inferredLooksChibi || hitAreaInfo.autoNamed ? 1.16 : 1.34;
        const hitAreaClearlyTooLarge = areaCoverageRatio >= coarseAreaThreshold ||
            widthCoverageRatio >= coarseWidthThreshold ||
            heightCoverageRatio >= coarseHeightThreshold;
        if (!hitAreaClearlyTooLarge) {
            return false;
        }

        if (kind === 'head') {
            const hitAreaStartsTooHigh = hitRect.top <= inferredRect.top - Math.max(16, inferredRect.height * 0.14);
            const hitAreaEndsTooLow = hitRect.bottom >= inferredRect.bottom + Math.max(20, inferredRect.height * 0.18);
            const hitAreaCenterTooLow = hitRect.centerY >= inferredRect.centerY + Math.max(14, inferredRect.height * 0.12);
            return hitAreaStartsTooHigh || hitAreaEndsTooLow || hitAreaCenterTooLow;
        }

        const hitAreaStartsTooHigh = hitRect.top <= inferredRect.top - Math.max(20, inferredRect.height * 0.16);
        const hitAreaEndsTooLow = hitRect.bottom >= inferredRect.bottom + Math.max(24, inferredRect.height * 0.18);
        const bodyCenterTooHigh = hitRect.centerY <= inferredRect.centerY - Math.max(16, inferredRect.height * 0.12);
        const hitAreaAbsorbsHead = counterpartRect &&
            hitRect.top <= counterpartRect.top + counterpartRect.height * 0.18;
        return hitAreaStartsTooHigh || hitAreaEndsTooLow || bodyCenterTooHigh || hitAreaAbsorbsHead;
    }

    _shouldPreferInferredRect(kind, hitAreaInfo, inferredInfo, modelBounds, counterpartInfo = null) {
        if (!inferredInfo) {
            return false;
        }

        if (!hitAreaInfo) {
            return true;
        }

        const inferredPlausible = this._isRectInfoPlausibleWithinModel(
            inferredInfo,
            modelBounds,
            kind === 'head'
                ? { maxWidthRatio: 0.86, maxHeightRatio: 0.64 }
                : { maxWidthRatio: 0.9, maxHeightRatio: 0.92 }
        );
        if (!inferredPlausible) {
            return false;
        }

        const hitAreaArea = this._getRectArea(hitAreaInfo);
        const inferredArea = this._getRectArea(inferredInfo);
        if (!(hitAreaArea > 0 && inferredArea > 0)) {
            return false;
        }

        const hitRect = hitAreaInfo.rect;
        const inferredRect = inferredInfo.rect;
        const areaCoverageRatio = hitAreaArea / Math.max(inferredArea, 1);
        const widthCoverageRatio = hitRect.width / Math.max(inferredRect.width, 1);
        const heightCoverageRatio = hitRect.height / Math.max(inferredRect.height, 1);
        const hitName = String(hitAreaInfo.hitAreaName || hitAreaInfo.name || '').toLowerCase();
        const hitId = String(hitAreaInfo.hitAreaId || hitAreaInfo.id || '').toLowerCase();
        const looksLikeTouchHotspot = /touch|tap|click/.test(hitName) || /touch|tap|click/.test(hitId);

        if (kind === 'head') {
            const hitAreaClearlyTooSmall = areaCoverageRatio <= 0.26 ||
                widthCoverageRatio <= 0.48 ||
                heightCoverageRatio <= 0.42;
            const hitAreaSitsTooLow = hitRect.top >= inferredRect.top + Math.max(18, inferredRect.height * 0.28) ||
                hitRect.centerY >= inferredRect.top + inferredRect.height * 0.72;
            return hitAreaClearlyTooSmall ||
                (looksLikeTouchHotspot && hitAreaSitsTooLow) ||
                this._shouldPreferOversizedHitAreaRect(
                    kind,
                    hitAreaInfo,
                    inferredInfo,
                    modelBounds,
                    counterpartInfo
                );
        }

        const hitAreaClearlyTooSmall = areaCoverageRatio <= 0.22 ||
            widthCoverageRatio <= 0.42 ||
            heightCoverageRatio <= 0.34;
        return hitAreaClearlyTooSmall ||
            looksLikeTouchHotspot ||
            this._shouldPreferOversizedHitAreaRect(
                kind,
                hitAreaInfo,
                inferredInfo,
                modelBounds,
                counterpartInfo
            );
    }

    _shouldPreferInferredBodyRectOverDisplayInfo(displayInfoInfo, inferredInfo, headInfo, modelBounds) {
        if (!displayInfoInfo || !inferredInfo || !headInfo || !modelBounds) {
            return false;
        }

        const displayPlausible = this._isRectInfoPlausibleWithinModel(
            displayInfoInfo,
            modelBounds,
            { maxWidthRatio: 1.04, maxHeightRatio: 1.02 }
        );
        const inferredPlausible = this._isRectInfoPlausibleWithinModel(
            inferredInfo,
            modelBounds,
            { maxWidthRatio: 0.9, maxHeightRatio: 0.92 }
        );
        if (!displayPlausible || !inferredPlausible) {
            return false;
        }

        const displayRect = displayInfoInfo.rect;
        const inferredRect = inferredInfo.rect;
        const headRect = headInfo.rect;
        if (!displayRect || !inferredRect || !headRect) {
            return false;
        }

        const displayTinyVsBounds = displayRect.width <= modelBounds.width * 0.24 &&
            displayRect.height <= modelBounds.height * 0.18;
        const displaySmallerThanHead = displayRect.width <= headRect.width * 0.96 &&
            displayRect.height <= headRect.height * 0.9;
        const inferredClearlyLarger = inferredRect.width >= displayRect.width * 2.1 &&
            inferredRect.height >= displayRect.height * 2.4;
        const displaySitsNearHead = displayRect.top <= headRect.bottom + Math.max(24, headRect.height * 1.15);

        return displayTinyVsBounds &&
            displaySmallerThanHead &&
            inferredClearlyLarger &&
            displaySitsNearHead;
    }

    _hasValidBubbleScreenRect(rect) {
        return !!(rect &&
            Number.isFinite(rect.left) &&
            Number.isFinite(rect.top) &&
            Number.isFinite(rect.width) &&
            Number.isFinite(rect.height) &&
            rect.width > 0 &&
            rect.height > 0);
    }

    _getBubbleHeadAnchorFromRect(headRect, headMode, headSource) {
        if (!this._hasValidBubbleScreenRect(headRect)) {
            return null;
        }

        const faceAnchorRatio = headSource === 'displayInfo' ? 0.36 : 0.42;
        const headAnchorRatio = headSource === 'displayInfo' ? 0.42 : 0.5;

        return {
            x: Number.isFinite(headRect.centerX) ? headRect.centerX : headRect.left + headRect.width * 0.5,
            y: headRect.top + headRect.height * (headMode === 'face' ? faceAnchorRatio : headAnchorRatio)
        };
    }

    _isReliableBubbleHeadRect(headRect, bounds, bodyRect, headSource) {
        if (!this._hasValidBubbleScreenRect(headRect) || !bounds) {
            return false;
        }

        const headCenterY = Number.isFinite(headRect.centerY)
            ? headRect.centerY
            : headRect.top + headRect.height * 0.5;
        const boundsRight = Number.isFinite(bounds.right) ? bounds.right : bounds.left + bounds.width;
        const boundsBottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;

        if (headSource === 'displayInfo') {
            const toleranceX = Math.max(18, bounds.width * 0.08);
            const toleranceY = Math.max(18, bounds.height * 0.08);
            if (headRect.left < bounds.left - toleranceX ||
                headRect.right > boundsRight + toleranceX ||
                headRect.top < bounds.top - toleranceY ||
                headRect.bottom > boundsBottom + toleranceY ||
                headRect.width > bounds.width * 0.98 ||
                headRect.height > bounds.height * 0.88) {
                return false;
            }

            if (!this._hasValidBubbleScreenRect(bodyRect)) {
                return true;
            }

            const bodyCenterY = Number.isFinite(bodyRect.centerY)
                ? bodyRect.centerY
                : bodyRect.top + bodyRect.height * 0.5;
            return headCenterY <= bodyRect.bottom &&
                headRect.top <= bodyCenterY &&
                headRect.height <= bodyRect.height * 1.12;
        }

        const maxHeadTop = bounds.top + bounds.height * 0.54;
        const maxHeadCenterY = bounds.top + bounds.height * 0.52;
        if (headRect.width > bounds.width * 0.76 ||
            headRect.height > bounds.height * 0.62 ||
            headRect.top > maxHeadTop ||
            headCenterY > maxHeadCenterY) {
            return false;
        }

        if (!this._hasValidBubbleScreenRect(bodyRect)) {
            return true;
        }

        return headRect.width <= bodyRect.width * 1.52 &&
            headRect.height <= bodyRect.height * 0.94 &&
            headCenterY <= bodyRect.top + bodyRect.height * 0.42;
    }

    _createBubbleBodyProxyRect(headRect, bounds, bodyRect = null) {
        if (!this._hasValidBubbleScreenRect(headRect) || !bounds) {
            return null;
        }

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const boundsRight = Number.isFinite(bounds.right) ? bounds.right : bounds.left + bounds.width;
        const boundsBottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;
        const headCenterX = Number.isFinite(headRect.centerX)
            ? headRect.centerX
            : headRect.left + headRect.width * 0.5;
        const bodyWidthHint = this._hasValidBubbleScreenRect(bodyRect) ? bodyRect.width : 0;
        const bodyHeightHint = this._hasValidBubbleScreenRect(bodyRect) ? bodyRect.height : 0;
        const width = clamp(
            Math.max(
                headRect.width * 1.28,
                bodyWidthHint * 0.42,
                bounds.width * 0.16
            ),
            Math.max(56, headRect.width * 1.12),
            Math.max(72, Math.min(bounds.width * 0.44, headRect.width * 2.05))
        );
        const height = clamp(
            Math.max(
                headRect.height * 1.6,
                bodyHeightHint * 0.36,
                bounds.height * 0.18
            ),
            Math.max(72, headRect.height * 1.24),
            Math.max(96, Math.min(bounds.height * 0.42, headRect.height * 3.0))
        );
        const centerX = clamp(
            headCenterX,
            bounds.left + width * 0.5,
            boundsRight - width * 0.5
        );
        const top = clamp(
            headRect.bottom - Math.min(headRect.height * 0.12, height * 0.1),
            bounds.top,
            boundsBottom - height
        );

        return this._createScreenRect(
            centerX - width * 0.5,
            top,
            centerX + width * 0.5,
            top + height
        );
    }

    _isReliableBubbleBodyRect(bodyRect, bounds, headRect, reliableHeadRect, bodySource) {
        if (!this._hasValidBubbleScreenRect(bodyRect) || !bounds) {
            return false;
        }

        const boundsRight = Number.isFinite(bounds.right) ? bounds.right : bounds.left + bounds.width;
        const boundsBottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;
        const toleranceX = Math.max(24, bounds.width * 0.1);
        const toleranceY = Math.max(24, bounds.height * 0.1);
        if (bodyRect.left < bounds.left - toleranceX ||
            bodyRect.right > boundsRight + toleranceX ||
            bodyRect.top < bounds.top - toleranceY ||
            bodyRect.bottom > boundsBottom + toleranceY ||
            bodyRect.width > bounds.width * 1.04 ||
            bodyRect.height > bounds.height * 1.04) {
            return false;
        }

        const bodyCenterY = Number.isFinite(bodyRect.centerY)
            ? bodyRect.centerY
            : bodyRect.top + bodyRect.height * 0.5;
        if (!reliableHeadRect || !this._hasValidBubbleScreenRect(headRect)) {
            return bodyRect.width >= bounds.width * 0.08 &&
                bodyRect.height >= bounds.height * 0.12 &&
                bodyCenterY >= bounds.top + bounds.height * 0.2 &&
                bodyCenterY <= bounds.top + bounds.height * 0.9;
        }

        const headCenterX = Number.isFinite(headRect.centerX)
            ? headRect.centerX
            : headRect.left + headRect.width * 0.5;
        const bodyCenterX = Number.isFinite(bodyRect.centerX)
            ? bodyRect.centerX
            : bodyRect.left + bodyRect.width * 0.5;
        const widthRatio = bodyRect.width / Math.max(1, headRect.width);
        const heightRatio = bodyRect.height / Math.max(1, headRect.height);
        const centerDrift = Math.abs(bodyCenterX - headCenterX);
        const maxCenterDrift = Math.max(
            32,
            headRect.width * 0.9,
            bodyRect.width * 0.16,
            bounds.width * 0.08
        );
        const gapFromHeadBottom = bodyRect.top - headRect.bottom;
        const bodyStartsTooHigh = bodyRect.top < headRect.top - Math.max(24, headRect.height * 0.24);
        const bodyStartsTooLow = gapFromHeadBottom > Math.max(64, headRect.height * 0.95);
        const bodyTooTiny = widthRatio < 0.6 || heightRatio < 0.56;
        const bodyTooWide = widthRatio > 2.7 || bodyRect.width > bounds.width * 0.88;
        const bodyTooTall = heightRatio > 3.4 || bodyRect.height > bounds.height * 0.88;
        const bodyEndsTooHigh = bodyRect.bottom < headRect.bottom + Math.max(32, headRect.height * 0.32);
        const bodyCenterNotBelowHead = bodyCenterY <= (
            (Number.isFinite(headRect.centerY) ? headRect.centerY : headRect.top + headRect.height * 0.5) +
            Math.max(18, headRect.height * 0.12)
        );

        if (bodySource === 'drawableHeuristic') {
            return !bodyStartsTooHigh &&
                !bodyStartsTooLow &&
                !bodyTooTiny &&
                !bodyTooWide &&
                !bodyTooTall &&
                !bodyEndsTooHigh &&
                !bodyCenterNotBelowHead &&
                centerDrift <= maxCenterDrift;
        }

        return !bodyStartsTooLow &&
            !bodyTooTiny &&
            !bodyTooWide &&
            !bodyTooTall &&
            !bodyEndsTooHigh &&
            !bodyCenterNotBelowHead &&
            centerDrift <= maxCenterDrift * 1.1;
    }

    _normalizeBubbleBodyRect(bodyRect, bounds, headRect, reliableHeadRect, bodySource) {
        if (bodySource === 'bubbleBodyProxy') {
            return this._createBubbleBodyProxyRect(headRect, bounds, bodyRect) || bodyRect;
        }

        if (!this._hasValidBubbleScreenRect(bodyRect)) {
            return reliableHeadRect
                ? this._createBubbleBodyProxyRect(headRect, bounds, null)
                : bodyRect;
        }

        if (!reliableHeadRect || !this._hasValidBubbleScreenRect(headRect)) {
            return this._isReliableBubbleBodyRect(bodyRect, bounds, headRect, false, bodySource)
                ? bodyRect
                : null;
        }

        return this._isReliableBubbleBodyRect(bodyRect, bounds, headRect, true, bodySource)
            ? bodyRect
            : this._createBubbleBodyProxyRect(headRect, bounds, bodyRect);
    }

    _normalizeBubbleHeadRect(headRect, bounds, bodyRect, headSource, headMode) {
        if (!this._hasValidBubbleScreenRect(headRect) || !bounds) {
            return headRect;
        }

        if (headSource === 'drawableHeuristic') {
            return this._normalizeDrawableHeadScreenRect(
                headRect,
                bounds,
                this._hasValidBubbleScreenRect(bodyRect) ? bodyRect : null,
                null
            ) || headRect;
        }

        if (headMode === 'face') {
            return this._expandFaceModeRectToFullHead(
                headRect, bounds, bodyRect
            ) || headRect;
        }

        return headRect;
    }

    _expandFaceModeRectToFullHead(rect, bounds, bodyRect) {
        if (!rect || !bounds) {
            return rect;
        }

        const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
        const widthRatio = rect.width / Math.max(1, bounds.width);
        const heightRatio = rect.height / Math.max(1, bounds.height);

        if (widthRatio >= 0.48 || heightRatio >= 0.38) {
            return rect;
        }

        const expandedWidth = clamp(
            rect.width * 1.3,
            rect.width * 1.2,
            Math.min(bounds.width * 0.58, rect.width * 1.45)
        );
        const topExtension = rect.height * 0.3;
        let newTop = rect.top - topExtension;
        let newBottom = rect.bottom;

        if (this._hasValidBubbleScreenRect(bodyRect)) {
            const bodyCap = bodyRect.top + bodyRect.height * 0.15;
            if (newBottom > bodyCap && bodyCap > newTop + expandedWidth * 0.4) {
                newBottom = bodyCap;
            }
        }

        newTop = Math.max(bounds.top, newTop);
        newBottom = Math.min(bounds.top + bounds.height, newBottom);
        if (newBottom - newTop < rect.height * 1.15) {
            newBottom = newTop + rect.height * 1.25;
            newBottom = Math.min(bounds.top + bounds.height, newBottom);
        }

        const centerX = Number.isFinite(rect.centerX)
            ? rect.centerX
            : rect.left + rect.width * 0.5;

        return this._createScreenRect(
            centerX - expandedWidth * 0.5,
            newTop,
            centerX + expandedWidth * 0.5,
            newBottom
        ) || rect;
    }

    _shouldUseBubbleDrawableHeadProxy(headRect, bounds, bodyRect, headSource) {
        if (headSource !== 'drawableHeuristic' ||
            !this._hasValidBubbleScreenRect(headRect) ||
            !bounds) {
            return false;
        }

        const headCenterX = Number.isFinite(headRect.centerX)
            ? headRect.centerX
            : headRect.left + headRect.width * 0.5;
        const boundsCenterX = Number.isFinite(bounds.centerX)
            ? bounds.centerX
            : bounds.left + bounds.width * 0.5;
        const headOccupiesLargeUpperBand = headRect.top <= bounds.top + bounds.height * 0.16 &&
            headRect.bottom <= bounds.top + bounds.height * 0.58 &&
            headRect.width >= bounds.width * 0.34 &&
            headRect.height >= bounds.height * 0.22;
        const headLooksWideForFaceAnchor = headRect.width >= bounds.width * 0.28 &&
            (headRect.width / Math.max(1, headRect.height)) >= 1.25;
        const bodyStartsBelowHeadCore = !this._hasValidBubbleScreenRect(bodyRect) ||
            bodyRect.top >= headRect.top + headRect.height * 0.38;
        const headBiasesAwayFromBoundsCenter = Math.abs(headCenterX - boundsCenterX) >= bounds.width * 0.04;

        return (headOccupiesLargeUpperBand || headLooksWideForFaceAnchor) &&
            (bodyStartsBelowHeadCore || headBiasesAwayFromBoundsCenter);
    }

    _createBubbleDrawableHeadProxyRect(headRect, bounds, bodyRect = null) {
        if (!this._hasValidBubbleScreenRect(headRect) || !bounds) {
            return null;
        }

        const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
        const boundsRight = Number.isFinite(bounds.right) ? bounds.right : bounds.left + bounds.width;
        const boundsBottom = Number.isFinite(bounds.bottom) ? bounds.bottom : bounds.top + bounds.height;
        const headCenterX = Number.isFinite(headRect.centerX)
            ? headRect.centerX
            : headRect.left + headRect.width * 0.5;
        const bodyCenterX = this._hasValidBubbleScreenRect(bodyRect) && Number.isFinite(bodyRect.centerX)
            ? bodyRect.centerX
            : headCenterX;
        const horizontalShift = clamp(
            (bodyCenterX - headCenterX) * 0.82,
            -headRect.width * 0.18,
            headRect.width * 0.18
        );
        const width = clamp(
            Math.max(
                headRect.width * 0.4,
                this._hasValidBubbleScreenRect(bodyRect) ? bodyRect.width * 0.24 : 0,
                bounds.width * 0.17
            ),
            Math.max(92, headRect.width * 0.32),
            Math.min(bounds.width * 0.42, headRect.width * 0.58)
        );
        const height = clamp(
            Math.max(
                headRect.height * 0.48,
                width * 0.68,
                bounds.height * 0.16
            ),
            Math.max(72, headRect.height * 0.42),
            Math.min(bounds.height * 0.34, headRect.height * 0.7)
        );
        const centerX = clamp(
            headCenterX + horizontalShift,
            Math.max(bounds.left, headRect.left) + width * 0.5,
            Math.min(boundsRight, headRect.right) - width * 0.5
        );
        const top = clamp(
            headRect.top + headRect.height * 0.1,
            headRect.top,
            Math.min(boundsBottom, headRect.bottom) - height
        );

        return this._createScreenRect(
            centerX - width * 0.5,
            top,
            centerX + width * 0.5,
            top + height
        );
    }

    _getBubbleGeometryOverride() {
        const runtimeOverrides = window.NEKO_LIVE2D_BUBBLE_OVERRIDES;
        const overrideMap = (runtimeOverrides && typeof runtimeOverrides === 'object')
            ? runtimeOverrides
            : LIVE2D_BUBBLE_GEOMETRY_OVERRIDES;
        if (!overrideMap || typeof overrideMap !== 'object') {
            return null;
        }

        return overrideMap[this.modelRootPath] ||
            overrideMap[this.modelName] ||
            null;
    }

    _getBubbleGeometryOverrideSignature() {
        const override = this._getBubbleGeometryOverride();
        if (!override || typeof override !== 'object') {
            return 'none';
        }

        const readFinite = (key) => {
            const value = Number(override[key]);
            return Number.isFinite(value) ? value : null;
        };

        return JSON.stringify({
            headScaleX: readFinite('headScaleX'),
            headScaleY: readFinite('headScaleY'),
            headOffsetX: readFinite('headOffsetX'),
            headOffsetY: readFinite('headOffsetY'),
            anchorOffsetX: readFinite('anchorOffsetX'),
            anchorOffsetY: readFinite('anchorOffsetY')
        });
    }

    _applyBubbleGeometryOverride(geometryInfo) {
        const override = this._getBubbleGeometryOverride();
        if (!override || typeof override !== 'object' || !geometryInfo) {
            return geometryInfo;
        }

        const nextGeometryInfo = Object.assign({}, geometryInfo);
        const rawHeadRect = geometryInfo.headRect;
        const bubbleHeadRect = geometryInfo.bubbleHeadRect || geometryInfo.headRect || null;
        if (this._hasValidBubbleScreenRect(bubbleHeadRect)) {
            let left = bubbleHeadRect.left;
            let top = bubbleHeadRect.top;
            let width = bubbleHeadRect.width;
            let height = bubbleHeadRect.height;
            const centerX = Number.isFinite(bubbleHeadRect.centerX) ? bubbleHeadRect.centerX : bubbleHeadRect.left + bubbleHeadRect.width * 0.5;
            const centerY = Number.isFinite(bubbleHeadRect.centerY) ? bubbleHeadRect.centerY : bubbleHeadRect.top + bubbleHeadRect.height * 0.5;
            const widthScale = Number.isFinite(override.headScaleX) ? override.headScaleX : 1;
            const heightScale = Number.isFinite(override.headScaleY) ? override.headScaleY : 1;
            const offsetX = Number.isFinite(override.headOffsetX) ? override.headOffsetX : 0;
            const offsetY = Number.isFinite(override.headOffsetY) ? override.headOffsetY : 0;

            width = Math.max(1, width * widthScale);
            height = Math.max(1, height * heightScale);
            left = centerX - width * 0.5 + offsetX;
            top = centerY - height * 0.5 + offsetY;
            nextGeometryInfo.bubbleHeadRect = {
                left,
                top,
                right: left + width,
                bottom: top + height,
                width,
                height,
                centerX: left + width * 0.5,
                centerY: top + height * 0.5
            };
        }

        const resolvedHeadRect = nextGeometryInfo.bubbleHeadRect || bubbleHeadRect || null;
        const resolvedHeadSource = nextGeometryInfo.headSource || geometryInfo.headSource || null;
        const rawBodyRect = nextGeometryInfo.bodyRect || geometryInfo.bodyRect || null;
        const headPlausibleWithoutBody = this._isReliableBubbleHeadRect(
            resolvedHeadRect,
            nextGeometryInfo.bounds || geometryInfo.bounds || null,
            null,
            resolvedHeadSource
        );
        nextGeometryInfo.bodyRect = this._normalizeBubbleBodyRect(
            rawBodyRect,
            nextGeometryInfo.bounds || geometryInfo.bounds || null,
            resolvedHeadRect,
            headPlausibleWithoutBody,
            nextGeometryInfo.bodySource || geometryInfo.bodySource || null
        ) || null;
        if (nextGeometryInfo.bodyRect !== rawBodyRect && nextGeometryInfo.bodyRect) {
            nextGeometryInfo.bodySource = 'bubbleBodyProxy';
        }
        const reliableHeadRect = this._isReliableBubbleHeadRect(
            resolvedHeadRect,
            nextGeometryInfo.bounds || geometryInfo.bounds || null,
            nextGeometryInfo.bodyRect || null,
            resolvedHeadSource
        );
        const preciseDisplayInfoRect = reliableHeadRect && resolvedHeadSource === 'displayInfo';
        const coarseHitAreaHeadRect = resolvedHeadSource === 'hitArea' &&
            this._hasValidBubbleScreenRect(resolvedHeadRect) &&
            geometryInfo.rawHeadAnchor &&
            Number.isFinite(geometryInfo.rawHeadAnchor.y) &&
            geometryInfo.rawHeadAnchor.y >= resolvedHeadRect.top + resolvedHeadRect.height * 0.82;
        let baseAnchor = reliableHeadRect
            ? (this._getBubbleHeadAnchorFromRect(
                resolvedHeadRect,
                nextGeometryInfo.headMode || geometryInfo.headMode || null,
                resolvedHeadSource
            ) || geometryInfo.rawHeadAnchor)
            : null;

        if (coarseHitAreaHeadRect && geometryInfo.rawHeadAnchor) {
            baseAnchor = geometryInfo.rawHeadAnchor;
        }

        if (baseAnchor) {
            nextGeometryInfo.headAnchor = {
                x: baseAnchor.x + (Number.isFinite(override.anchorOffsetX) ? override.anchorOffsetX : 0),
                y: baseAnchor.y + (Number.isFinite(override.anchorOffsetY) ? override.anchorOffsetY : 0)
            };
        } else {
            nextGeometryInfo.headAnchor = geometryInfo.rawHeadAnchor || null;
        }

        nextGeometryInfo.reliableHeadRect = reliableHeadRect;
        nextGeometryInfo.preciseDisplayInfoRect = preciseDisplayInfoRect;
        nextGeometryInfo.coarseHitAreaHeadRect = coarseHitAreaHeadRect;
        nextGeometryInfo.headRect = rawHeadRect || null;
        nextGeometryInfo.bubbleHeadRect = resolvedHeadRect || null;

        return nextGeometryInfo;
    }

    getHeadScreenRectInfo() {
        const modelBounds = this.getModelScreenBounds();
        const modelLogicalRect = this._getModelLogicalRect();
        const hitAreaInfo = this._getHeadHitAreaScreenRectInfo(modelBounds, modelLogicalRect);
        const displayInfoInfo = this._getDisplayInfoPartScreenRectInfo('head');
        const inferredBodyInfo = this._inferDrawableRegionScreenRectInfo('body', modelBounds, modelLogicalRect);
        const useHitAreaAsHeadHint = this._isRectInfoPlausibleWithinModel(
            hitAreaInfo,
            modelBounds,
            { maxWidthRatio: 0.78, maxHeightRatio: 0.56 }
        );
        const inferredInfo = this._inferDrawableRegionScreenRectInfo(
            'head',
            modelBounds,
            modelLogicalRect,
            inferredBodyInfo?.rect || null,
            useHitAreaAsHeadHint ? (hitAreaInfo?.rect || null) : null
        );
        const headHitRect = hitAreaInfo?.rect || null;
        const hitAreaLooksCoarseAgainstModel = !!(
            headHitRect &&
            modelBounds &&
            headHitRect.width >= modelBounds.width * 0.8 &&
            headHitRect.height >= modelBounds.height * 0.46 &&
            headHitRect.top <= modelBounds.top + modelBounds.height * 0.12
        );
        if (this._shouldPreferDisplayInfoRect('head', hitAreaInfo, displayInfoInfo, modelBounds)) {
            return displayInfoInfo;
        }

        if (hitAreaLooksCoarseAgainstModel &&
            this._isRectInfoPlausibleWithinModel(
                inferredInfo,
                modelBounds,
                { maxWidthRatio: 0.86, maxHeightRatio: 0.64 }
            )) {
            return inferredInfo;
        }

        if (this._shouldPreferInferredRect('head', hitAreaInfo, inferredInfo, modelBounds, inferredBodyInfo)) {
            return inferredInfo;
        }

        return hitAreaInfo || displayInfoInfo || inferredInfo;
    }

    getBodyScreenRectInfo(headInfo = undefined) {
        const modelBounds = this.getModelScreenBounds();
        const modelLogicalRect = this._getModelLogicalRect();
        const hitAreaInfo = this._getBodyHitAreaScreenRectInfo(modelBounds, modelLogicalRect);
        const displayInfoInfo = this._getDisplayInfoPartScreenRectInfo('body');
        const resolvedHeadInfo = headInfo === undefined
            ? this.getHeadScreenRectInfo()
            : headInfo;
        const inferredInfo = this._inferDrawableRegionScreenRectInfo(
            'body',
            modelBounds,
            modelLogicalRect,
            null,
            resolvedHeadInfo?.rect || null
        );
        if (this._shouldPreferInferredBodyRectOverDisplayInfo(displayInfoInfo, inferredInfo, resolvedHeadInfo, modelBounds)) {
            return inferredInfo;
        }
        if (this._shouldPreferDisplayInfoRect('body', hitAreaInfo, displayInfoInfo, modelBounds)) {
            return displayInfoInfo;
        }

        if (this._shouldPreferInferredRect('body', hitAreaInfo, inferredInfo, modelBounds, resolvedHeadInfo)) {
            return inferredInfo;
        }

        return hitAreaInfo || displayInfoInfo || inferredInfo;
    }

    getHeadDetectionGeometryInfo() {
        const bounds = this.getModelScreenBounds();
        if (!bounds) {
            return null;
        }

        const headInfo = this.getHeadScreenRectInfo();
        const bodyInfo = this.getBodyScreenRectInfo(headInfo);
        const rawHeadAnchor = this.getHeadScreenAnchor(headInfo);
        const headRect = this._normalizeBubbleHeadRect(
            headInfo?.rect || null,
            bounds,
            bodyInfo?.rect || null,
            headInfo?.source || null,
            headInfo?.mode || null
        );
        const rawBodyRect = bodyInfo?.rect || null;
        const headMode = headInfo?.mode || null;
        const headSource = headInfo?.source || null;
        let bodySource = bodyInfo?.source || null;

        const headPlausibleWithoutBody = this._isReliableBubbleHeadRect(headRect, bounds, null, headSource);
        const bodyRect = this._normalizeBubbleBodyRect(
            rawBodyRect,
            bounds,
            headRect,
            headPlausibleWithoutBody,
            bodySource
        ) || null;
        if (bodyRect && bodyRect !== rawBodyRect) {
            bodySource = 'bubbleBodyProxy';
        }

        const reliableHeadRect = this._isReliableBubbleHeadRect(headRect, bounds, bodyRect, headSource);
        const preciseDisplayInfoRect = reliableHeadRect && headSource === 'displayInfo';
        const coarseHitAreaHeadRect = headSource === 'hitArea' &&
            this._hasValidBubbleScreenRect(headRect) &&
            rawHeadAnchor &&
            Number.isFinite(rawHeadAnchor.y) &&
            rawHeadAnchor.y >= headRect.top + headRect.height * 0.82;
        let headAnchor = reliableHeadRect
            ? (this._getBubbleHeadAnchorFromRect(headRect, headMode, headSource) || rawHeadAnchor)
            : null;

        if (coarseHitAreaHeadRect && rawHeadAnchor) {
            headAnchor = rawHeadAnchor;
        }

        return {
            type: 'live2d',
            bounds,
            rawHeadAnchor: rawHeadAnchor || null,
            headAnchor: headAnchor || rawHeadAnchor || null,
            headRect: headRect || null,
            headMode,
            headSource,
            bodyRect,
            bodySource,
            reliableHeadRect,
            preciseDisplayInfoRect,
            coarseHitAreaHeadRect
        };
    }

    getBubbleAnchorGeometryInfo() {
        const cached = this._getCachedBubbleGeometryResult();
        if (cached) {
            return cached;
        }

        const detectionInfo = this.getHeadDetectionGeometryInfo();
        if (!detectionInfo || !detectionInfo.bounds) {
            return null;
        }

        const bounds = detectionInfo.bounds;
        const rawHeadAnchor = detectionInfo.rawHeadAnchor || null;
        const headRect = detectionInfo.headRect || null;
        const headMode = detectionInfo.headMode || null;
        const headSource = detectionInfo.headSource || null;
        const bodyRect = detectionInfo.bodyRect || null;
        const bodySource = detectionInfo.bodySource || null;
        const bubbleHeadRect = this._shouldUseBubbleDrawableHeadProxy(headRect, bounds, bodyRect, headSource)
            ? (this._createBubbleDrawableHeadProxyRect(headRect, bounds, bodyRect) || headRect)
            : headRect;
        const reliableHeadRect = this._isReliableBubbleHeadRect(bubbleHeadRect, bounds, bodyRect, headSource);
        const preciseDisplayInfoRect = reliableHeadRect && headSource === 'displayInfo';
        const coarseHitAreaHeadRect = headSource === 'hitArea' &&
            this._hasValidBubbleScreenRect(bubbleHeadRect) &&
            rawHeadAnchor &&
            Number.isFinite(rawHeadAnchor.y) &&
            rawHeadAnchor.y >= bubbleHeadRect.top + bubbleHeadRect.height * 0.82;
        let headAnchor = reliableHeadRect
            ? (this._getBubbleHeadAnchorFromRect(bubbleHeadRect, headMode, headSource) || rawHeadAnchor)
            : null;

        if (coarseHitAreaHeadRect && rawHeadAnchor) {
            headAnchor = rawHeadAnchor;
        }

        const result = this._applyBubbleGeometryOverride({
            bounds,
            rawHeadAnchor: rawHeadAnchor || null,
            headAnchor: headAnchor || rawHeadAnchor || null,
            headRect: headRect || null,
            bubbleHeadRect: bubbleHeadRect || headRect || null,
            headMode,
            headSource,
            bodyRect,
            bodySource,
            reliableHeadRect,
            preciseDisplayInfoRect,
            coarseHitAreaHeadRect
        });

        if (result.reliableHeadRect) {
            const modelLogicalRect = this._getModelLogicalRect();
            if (modelLogicalRect) {
                this._cacheBubbleGeometryResult(result, bounds, modelLogicalRect);
            }
        }

        return result;
    }

    getHeadScreenAnchor(headScreenInfo = undefined) {
        const resolvedHeadScreenInfo = headScreenInfo === undefined
            ? this.getHeadScreenRectInfo()
            : headScreenInfo;
        return this.getHeadScreenAnchorFromInfo(resolvedHeadScreenInfo);
    }

    getHeadScreenAnchorFromInfo(headScreenInfo) {
        const headScreenRect = headScreenInfo?.rect;
        if (!headScreenRect) {
            return null;
        }

        return {
            x: headScreenRect.centerX,
            y: headScreenRect.top + headScreenRect.height * (headScreenInfo.mode === 'face' ? 0.42 : 0.5)
        };
    }

    getBubbleAnchorDebugInfo() {
        const settings = this.currentModel?.internalModel?.settings;
        const settingsJson = settings?.json;
        const hitAreaDefs = settings?.hitAreas;
        const headInfo = this.getHeadScreenRectInfo();
        const bodyInfo = this.getBodyScreenRectInfo(headInfo);
        const geometryInfo = this.getBubbleAnchorGeometryInfo();

        return {
            modelName: this.modelName || null,
            modelRootPath: this.modelRootPath || null,
            displayInfoLoaded: !!this._displayInfo,
            displayInfoPath: settingsJson?.FileReferences?.DisplayInfo || null,
            hitAreas: Array.isArray(hitAreaDefs)
                ? hitAreaDefs.map((hitArea) => ({
                    id: String(hitArea?.Id || ''),
                    name: String(hitArea?.Name || '')
                }))
                : [],
            bounds: geometryInfo?.bounds || this.getModelScreenBounds(),
            headInfo: headInfo || null,
            bodyInfo: bodyInfo || null,
            geometryInfo
        };
    }

    /**
     * 获取 Live2D 模型在屏幕上的边界
     * @returns {Object|null} 边界对象 { left, right, top, bottom, width, height, centerX, centerY } 或 null
     */
    getModelScreenBounds() {
        const model = this.currentModel;
        if (!model) {
            return null;
        }

        if (typeof model.getBounds !== 'function') {
            return null;
        }

        let bounds = null;
        try {
            bounds = model.getBounds();
        } catch (error) {
            console.warn('[Live2D] 获取模型屏幕边界失败:', error);
            return null;
        }

        if (!bounds) {
            return null;
        }

        const left = Number(bounds.left);
        const right = Number(bounds.right);
        const top = Number(bounds.top);
        const bottom = Number(bounds.bottom);

        if (!Number.isFinite(left) || !Number.isFinite(right) ||
            !Number.isFinite(top) || !Number.isFinite(bottom)) {
            return null;
        }

        const width = right - left;
        const height = bottom - top;
        if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
            return null;
        }

        const stableBounds = {
            left: left,
            right: right,
            top: top,
            bottom: bottom,
            width: width,
            height: height,
            centerX: left + width / 2,
            centerY: top + height / 2
        };

        return stableBounds;
    }

    // 复位模型位置和缩放到初始状态
    async resetModelPosition() {
        if (!this.currentModel || !this.pixi_app) {
            console.warn('无法复位：模型或PIXI应用未初始化');
            return;
        }

        try {
            if (isMobileWidth()) {
                this.currentModel.anchor.set(0.5, 0.1);
                const scale = Math.min(
                    0.5,
                    window.innerHeight * 1.3 / 4000,
                    window.innerWidth * 1.2 / 2000
                );
                this.currentModel.scale.set(scale);
                this.currentModel.x = this.pixi_app.renderer.screen.width * 0.5;
                this.currentModel.y = this.pixi_app.renderer.screen.height * 0.28;
            } else {
                this.currentModel.anchor.set(0.65, 0.75);
                const scale = Math.min(
                    0.5,
                    (window.innerHeight * 0.75) / 7000,
                    (window.innerWidth * 0.6) / 7000
                );
                this.currentModel.scale.set(scale);
                this.currentModel.x = this.pixi_app.renderer.screen.width;
                this.currentModel.y = this.pixi_app.renderer.screen.height;
            }

            console.log('模型位置已复位到初始状态');

            // 复位后自动保存位置（viewport 基准与 applyModelSettings / _savePositionAfterInteraction 一致，使用 renderer.screen）
            if (this._lastLoadedModelPath) {
                const viewport = {
                    width: this.pixi_app.renderer.screen.width,
                    height: this.pixi_app.renderer.screen.height
                };
                const saveSuccess = await this.saveUserPreferences(
                    this._lastLoadedModelPath,
                    { x: this.currentModel.x, y: this.currentModel.y },
                    { x: this.currentModel.scale.x, y: this.currentModel.scale.y },
                    null, null, viewport
                );
                if (saveSuccess) {
                    console.log('模型位置已保存');
                } else {
                    console.warn('模型位置保存失败');
                }
            }

        } catch (error) {
            console.error('复位模型位置时出错:', error);
        }
    }

    /**
     * 【统一状态管理】设置锁定状态并同步更新所有相关 UI
     * @param {boolean} locked - 是否锁定
     * @param {Object} options - 可选配置
     * @param {boolean} options.updateFloatingButtons - 是否同时控制浮动按钮显示（默认 true）
     */
    setLocked(locked, options = {}) {
        const { updateFloatingButtons = true } = options;

        // 1. 更新状态
        this.isLocked = locked;

        // 2. 更新锁图标样式（使用存储的引用，避免每次 querySelector）
        if (this._lockIconImages) {
            const { locked: imgLocked, unlocked: imgUnlocked } = this._lockIconImages;
            if (imgLocked) imgLocked.style.opacity = locked ? '1' : '0';
            if (imgUnlocked) imgUnlocked.style.opacity = locked ? '0' : '1';
        }

        // 3. 更新 canvas 的 pointerEvents
        const container = document.getElementById('live2d-canvas');
        if (container) {
            container.style.pointerEvents = locked ? 'none' : 'auto';
        }

        if (!locked) {
            const live2dContainer = document.getElementById('live2d-container');
            if (live2dContainer) {
                live2dContainer.classList.remove('locked-hover-fade');
            }
        }

        // 4. 控制浮动按钮显示（可选）
        if (updateFloatingButtons) {
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            if (floatingButtons) {
                floatingButtons.style.display = locked ? 'none' : 'flex';
            }
        }
    }

    /**
     * 【统一状态管理】更新浮动按钮的激活状态和图标
     * @param {string} buttonId - 按钮ID（如 'mic', 'screen', 'agent' 等）
     * @param {boolean} active - 是否激活
     */
    setButtonActive(buttonId, active) {
        const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
        if (!buttonData || !buttonData.button) return;

        // 更新 dataset
        buttonData.button.dataset.active = active ? 'true' : 'false';

        // 更新背景色（使用 CSS 变量，确保暗色模式正确）
        buttonData.button.style.background = active
            ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
            : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

        // 更新图标
        if (buttonData.imgOff) {
            buttonData.imgOff.style.opacity = active ? '0' : '0.75';
        }
        if (buttonData.imgOn) {
            buttonData.imgOn.style.opacity = active ? '1' : '0';
        }
    }

    /**
     * 【统一状态管理】重置所有浮动按钮到默认状态
     */
    resetAllButtons() {
        if (!this._floatingButtons) return;

        Object.keys(this._floatingButtons).forEach(btnId => {
            this.setButtonActive(btnId, false);
        });
    }

    /**
     * 【统一状态管理】根据全局状态同步浮动按钮状态
     * 用于模型重新加载后恢复按钮状态（如画质变更后）
     */
    _syncButtonStatesWithGlobalState() {
        if (!this._floatingButtons) return;

        // 同步语音按钮状态
        const isRecording = window.isRecording || false;
        if (this._floatingButtons.mic) {
            this.setButtonActive('mic', isRecording);
        }

        // 同步屏幕分享按钮状态
        // 屏幕分享状态通过 DOM 元素判断（screenButton 的 active class 或 stopButton 的 disabled 状态）
        let isScreenSharing = false;
        const screenButton = document.getElementById('screenButton');
        const stopButton = document.getElementById('stopButton');
        if (screenButton && screenButton.classList.contains('active')) {
            isScreenSharing = true;
        } else if (stopButton && !stopButton.disabled) {
            isScreenSharing = true;
        }
        if (this._floatingButtons.screen) {
            this.setButtonActive('screen', isScreenSharing);
        }
    }

    /**
     * 设置鼠标跟踪是否启用
     * @param {boolean} enabled - 是否启用鼠标跟踪
     */
    setMouseTrackingEnabled(enabled) {
        this._mouseTrackingEnabled = enabled;
        window.mouseTrackingEnabled = enabled;

        if (enabled) {
            // 重新启用时，如果模型存在且没有鼠标跟踪监听器，则启用
            if (this.currentModel && !this._mouseTrackingListener) {
                this.enableMouseTracking(this.currentModel);
            }
        } else {
            this.isFocusing = false;
            // 清除 focusController 的外部输入，使头部不受鼠标/拖拽等外部因素影响
            // 自主运动（updateNaturalMovements：呼吸、轻微摆动）通过独立管线叠加，不受影响
            // 注意：不能用 model.focus(center) — 它经过 toModelPosition + atan2 + 单位圆投影，
            // 永远产生非零值（如 targetX=1），无法真正归零
            if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.focusController) {
                const fc = this.currentModel.internalModel.focusController;
                fc.targetX = 0;
                fc.targetY = 0;
            }
        }
    }

    /**
     * 获取鼠标跟踪是否启用
     * @returns {boolean}
     */
    isMouseTrackingEnabled() {
        return this._mouseTrackingEnabled !== false;
    }

    /**
     * 设置全屏跟踪是否启用
     * @param {boolean} enabled - 是否启用全屏跟踪
     */
    setFullscreenTrackingEnabled(enabled) {
        this._fullscreenTrackingEnabled = enabled;
        window.live2dFullscreenTrackingEnabled = enabled;
        console.log(`[Live2D] 全屏跟踪已${enabled ? '开启' : '关闭'}`);
    }

    /**
     * 获取全屏跟踪是否启用
     * @returns {boolean}
     */
    isFullscreenTrackingEnabled() {
        return this._fullscreenTrackingEnabled === true;
    }
}

// 导出
window.Live2DModel = Live2DModel;
window.Live2DManager = Live2DManager;
window.isMobileWidth = isMobileWidth;

// 监听帧率变更事件
window.addEventListener('neko-frame-rate-changed', (e) => {
    const fps = e.detail?.fps;
    if (fps != null && window.live2dManager) {
        window.live2dManager.setTargetFPS(fps);
    }
});

// 监听画质变更事件：需要重新加载模型以应用新的纹理降采样
let _qualityChangePending = false;
let _qualityChangeQueued = null;

window.addEventListener('neko-render-quality-changed', (e) => {
    const quality = e.detail?.quality;
    if (!quality || !window.live2dManager) return;
    
    _qualityChangeQueued = quality;
    
    if (_qualityChangePending) {
        console.log(`[Live2D] 画质变更请求排队中: ${quality}`);
        return;
    }
    
    const processQualityChange = async () => {
        const mgr = window.live2dManager;
        if (!mgr || !mgr.currentModel) return;
        
        const currentQuality = _qualityChangeQueued;
        _qualityChangeQueued = null;
        
        if (!currentQuality) return;
        
        if (!mgr.currentModel) return;
        
        _qualityChangePending = true;
        
        try {
            if (mgr._isLoadingModel) {
                console.log('[Live2D] 等待当前模型加载完成后重新加载...');
                await new Promise((resolve) => {
                    const checkInterval = setInterval(() => {
                        if (!mgr._isLoadingModel) {
                            clearInterval(checkInterval);
                            clearTimeout(waitTimeout);
                            resolve();
                        }
                    }, 100);
                    const waitTimeout = setTimeout(() => {
                        clearInterval(checkInterval);
                        console.warn('[Live2D] 等待模型加载超时(30秒)，继续执行...');
                        resolve();
                    }, 30000);
                });
            }
            
            if (!mgr.currentModel) return;
            
            const modelPath = mgr._lastLoadedModelPath;
            if (!modelPath) return;
            
            console.log(`[Live2D] 画质变更为 ${currentQuality}，重新加载模型以应用纹理降采样`);
            
            const modelForSave = mgr.currentModel;
            
            try {
                const textures = modelForSave.textures;
                if (textures) {
                    textures.forEach(tex => {
                        if (tex?.baseTexture) {
                            tex.baseTexture.destroy();
                        }
                    });
                }
            } catch (err) {
                console.warn('[Live2D] 清理纹理缓存时出错:', err);
            }
            
            const scaleX = modelForSave.scale.x;
            const scaleY = modelForSave.scale.y;
            const posX = modelForSave.x;
            const posY = modelForSave.y;
            
            const scaleObj = { x: scaleX, y: scaleY };
            const positionObj = { x: posX, y: posY };
            let savedPreferences = null;
            
            if (isValidModelPreferences(scaleObj, positionObj)) {
                savedPreferences = {
                    scale: scaleObj,
                    position: positionObj
                };
            } else {
                console.warn('[Live2D] 当前模型的 scale/position 无效，跳过保存偏好:', {
                    scaleX, scaleY, posX, posY
                });
            }
            
            if (mgr._lastLoadedModelPath !== modelPath) {
                console.warn('[Live2D] 模型已切换，跳过此次画质变更加载');
                return;
            }
            
            await mgr.loadModel(modelPath, savedPreferences ? { preferences: savedPreferences } : undefined);
        } catch (err) {
            console.warn('[Live2D] 画质变更后重新加载模型失败:', err);
        } finally {
            _qualityChangePending = false;
            if (_qualityChangeQueued) {
                setTimeout(processQualityChange, 50);
            }
        }
    };
    
    processQualityChange();
});
