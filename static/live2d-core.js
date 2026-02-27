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
    SCALE_MIN: 0,
    SCALE_MAX: 10,
    POSITION_MAX: 100000
};

// 验证模型偏好是否有效
function isValidModelPreferences(scale, position) {
    if (!scale || !position) return false;
    const scaleX = scale.x;
    const scaleY = scale.y;
    const posX = position.x;
    const posY = position.y;
    const isValidScale = Number.isFinite(scaleX) && scaleX > MODEL_PREFERENCES.SCALE_MIN && scaleX < MODEL_PREFERENCES.SCALE_MAX &&
                        Number.isFinite(scaleY) && scaleY > MODEL_PREFERENCES.SCALE_MIN && scaleY < MODEL_PREFERENCES.SCALE_MAX;
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
        this.modelName = null; // 记录当前模型目录名
        this.modelRootPath = null; // 记录当前模型根路径，如 /static/<modelName>
        this.savedModelParameters = null; // 保存的模型参数（从parameters.json加载），供定时器定期应用
        this._shouldApplySavedParams = false; // 是否应该应用保存的参数
        this._savedParamsTimer = null; // 保存参数应用的定时器
        
        // 模型加载锁，防止并发加载导致重复模型叠加
        this._isLoadingModel = false;
        this._activeLoadToken = 0;
        this._modelLoadState = 'idle';
        this._isModelReadyForInteraction = false;
        this._initPIXIPromise = null;
        this._lastPIXIContext = { canvasId: null, containerId: null };

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
                // 等待一帧让页面布局稳定，避免读到 CSS 未完全生效时的临时尺寸
                await new Promise(resolve => requestAnimationFrame(resolve));

                const initW = Math.max(container.clientWidth || 0, 1);
                const initH = Math.max(container.clientHeight || 0, 1);
                this.pixi_app = new PIXI.Application({
                    view: canvas,
                    width: initW,
                    height: initH,
                    ...defaultOptions,
                    ...options
                });

                // 验证 pixi_app 和 stage 是否创建成功
                if (!this.pixi_app) {
                    throw new Error('PIXI.Application 创建失败：返回值为 null 或 undefined');
                }

                if (!this.pixi_app.stage) {
                    throw new Error('PIXI.Application 创建失败：stage 属性不存在');
                }

                this.isInitialized = true;
                this._lastPIXIContext = { canvasId, containerId };
                // 应用初始帧率设置
                if (window.targetFrameRate && this.pixi_app.ticker) {
                    this.pixi_app.ticker.maxFPS = window.targetFrameRate;
                }

                // 仅在屏幕分辨率真正变化（换显示器/跨屏移动）时 resize 渲染器
                // DevTools、输入法、窗口拖拽等临时视口变化一律忽略，节省 GPU 开销
                let lastScreenW = window.screen.width;
                let lastScreenH = window.screen.height;
                this._screenChangeHandler = () => {
                    const sw = window.screen.width;
                    const sh = window.screen.height;
                    if (sw === lastScreenW && sh === lastScreenH) return;
                    lastScreenW = sw;
                    lastScreenH = sh;

                    const prevW = this.pixi_app.renderer.screen.width;
                    const prevH = this.pixi_app.renderer.screen.height;
                    const el = document.getElementById(containerId);
                    const measuredW = el ? el.clientWidth : prevW;
                    const measuredH = el ? el.clientHeight : prevH;
                    const measuredContainerIsZero = !!el && (measuredW <= 0 || measuredH <= 0);
                    const newW = Math.max(measuredW, 1);
                    const newH = Math.max(measuredH, 1);

                    this.pixi_app.renderer.resize(newW, newH);

                    if (this.currentModel && prevW > 0 && prevH > 0 && !measuredContainerIsZero) {
                        const wRatio = newW / prevW;
                        const hRatio = newH / prevH;
                        this.currentModel.x *= wRatio;
                        this.currentModel.y *= hRatio;
                        const areaRatio = Math.sqrt(wRatio * hRatio);
                        this.currentModel.scale.x *= areaRatio;
                        this.currentModel.scale.y *= areaRatio;
                    }
                    console.log('[Live2D Core] 屏幕分辨率变化，渲染器已 resize:', { prevW, prevH, newW, newH });
                };
                window.addEventListener('resize', this._screenChangeHandler);

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
     * @param {number} fps - 目标帧率（30 或 60）
     */
    setTargetFPS(fps) {
        if (this.pixi_app && this.pixi_app.ticker) {
            this.pixi_app.ticker.maxFPS = fps;
            console.log(`[Live2D Core] 目标帧率设置为 ${fps}fps`);
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

        // 更新背景色
        buttonData.button.style.background = active
            ? 'rgba(68, 183, 254, 0.3)'
            : 'rgba(255, 255, 255, 0.65)';

        // 更新图标
        if (buttonData.imgOff) {
            buttonData.imgOff.style.opacity = active ? '0' : '1';
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
}

// 导出
window.Live2DModel = Live2DModel;
window.Live2DManager = Live2DManager;
window.isMobileWidth = isMobileWidth;

// 监听帧率变更事件
window.addEventListener('neko-frame-rate-changed', (e) => {
    const fps = e.detail?.fps;
    if (fps && window.live2dManager) {
        window.live2dManager.setTargetFPS(fps);
    }
});

// 监听画质变更事件：需要重新加载模型以应用新的纹理降采样
window.addEventListener('neko-render-quality-changed', (e) => {
    const quality = e.detail?.quality;
    if (!quality || !window.live2dManager) return;
    const mgr = window.live2dManager;
    const modelPath = mgr._lastLoadedModelPath;
    if (modelPath && mgr.currentModel) {
        console.log(`[Live2D] 画质变更为 ${quality}，重新加载模型以应用纹理降采样`);
        // 显式销毁当前模型的 BaseTexture，清除 PIXI 纹理缓存
        // 否则 PIXI.BaseTexture.from(url) 会返回被降采样过的缓存纹理
        try {
            const textures = mgr.currentModel.textures;
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
        
        // 保存当前模型的 scale 和 position，以便重新加载后恢复
        const modelForSave = mgr.currentModel;
        
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
        
        mgr.loadModel(modelPath, savedPreferences ? { preferences: savedPreferences } : undefined).catch(err => {
            console.warn('[Live2D] 画质变更后重新加载模型失败:', err);
        });
    }
});