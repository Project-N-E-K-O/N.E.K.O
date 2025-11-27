/**
 * Live2D Core - 核心类结构和基础功能
 */

window.PIXI = PIXI;
const {Live2DModel} = PIXI.live2d;

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
        this.pixi_app = null;
        this.isInitialized = false;
        this.motionTimer = null;
        this.isEmotionChanging = false;
        this.dragEnabled = false;
        this.isFocusing = false;
        this.isLocked = true;
        this.onModelLoaded = null;
        this.onStatusUpdate = null;
        this.modelName = null; // 记录当前模型目录名
        this.modelRootPath = null; // 记录当前模型根路径，如 /static/<modelName>
        
        // 常驻表情：使用官方 expression 播放并在清理后自动重放
        this.persistentExpressionNames = [];

        // UI/Ticker 资源句柄（便于在切换模型时清理）
        this._lockIconTicker = null;
        this._lockIconElement = null;
        
        // 浮动按钮系统
        this._floatingButtonsTicker = null;
        this._floatingButtonsContainer = null;
        this._floatingButtons = {}; // 存储所有按钮元素
        this._popupTimers = {}; // 存储弹出框的定时器
        this._goodbyeClicked = false; // 标记是否点击了"请她离开"
        this._returnButtonContainer = null; // "请她回来"按钮容器
        
        // 已打开的设置窗口引用映射（URL -> Window对象）
        this._openSettingsWindows = {};

        // 口型同步控制
        this.mouthValue = 0; // 0~1
        this.mouthParameterId = null; // 例如 'ParamMouthOpenY' 或 'ParamO'
        this._mouthOverrideInstalled = false;
        this._origUpdateParameters = null;
        this._origExpressionUpdateParameters = null;
        this._mouthTicker = null;
        
        // 记录最后一次加载模型的原始路径（用于关闭时保存偏好）
        this._lastLoadedModelPath = null;

        // 在窗口关闭/刷新时尝试保存当前模型位置（使用 sendBeacon 以增加成功率）
        try {
            window.addEventListener('beforeunload', (e) => {
                try {
                    if (!this._lastLoadedModelPath || !this.currentModel) return;
                    
                    // 验证位置和缩放值是否为有效的有限数值
                    const posX = this.currentModel.x;
                    const posY = this.currentModel.y;
                    const scaleX = this.currentModel.scale ? this.currentModel.scale.x : 1;
                    const scaleY = this.currentModel.scale ? this.currentModel.scale.y : 1;
                    
                    // 检查所有值是否为有限数值（排除 NaN、Infinity、-Infinity）
                    if (!Number.isFinite(posX) || !Number.isFinite(posY) || 
                        !Number.isFinite(scaleX) || !Number.isFinite(scaleY)) {
                        console.warn('模型位置或缩放值无效，跳过保存:', { posX, posY, scaleX, scaleY });
                        return;
                    }
                    
                    // 额外验证：缩放值应该为正数（避免保存0或负数缩放）
                    if (scaleX <= 0 || scaleY <= 0) {
                        console.warn('模型缩放值必须为正数，跳过保存:', { scaleX, scaleY });
                        return;
                    }
                    
                    const payload = {
                        model_path: this._lastLoadedModelPath,
                        position: { x: posX, y: posY },
                        scale: { x: scaleX, y: scaleY }
                    };
                    const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
                    // 使用 navigator.sendBeacon 保证在页面卸载时尽可能发送数据
                    if (navigator && navigator.sendBeacon) {
                        navigator.sendBeacon('/api/preferences', blob);
                    } else {
                        // 作为回退，发起同步 XMLHttpRequest（尽量少用）
                        try {
                            const xhr = new XMLHttpRequest();
                            xhr.open('POST', '/api/preferences', false); // false -> 同步
                            xhr.setRequestHeader('Content-Type', 'application/json');
                            xhr.send(JSON.stringify(payload));
                        } catch (_) {}
                    }
                } catch (_) {}
            });
        } catch (_) {}
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
        if (this.isInitialized) {
            console.warn('Live2D 管理器已经初始化');
            return this.pixi_app;
        }

        const defaultOptions = {
            autoStart: true,
            transparent: true,
            backgroundAlpha: 0
        };

        this.pixi_app = new PIXI.Application({
            view: document.getElementById(canvasId),
            resizeTo: document.getElementById(containerId),
            ...defaultOptions,
            ...options
        });

        this.isInitialized = true;
        return this.pixi_app;
    }

    // 加载用户偏好
    async loadUserPreferences() {
        try {
            const response = await fetch('/api/preferences');
            if (response.ok) {
                return await response.json();
            }
        } catch (error) {
            console.warn('加载用户偏好失败:', error);
        }
        return [];
    }

    // 保存用户偏好
    async saveUserPreferences(modelPath, position, scale) {
        try {
            // 验证位置和缩放值是否为有效的有限数值
            if (!position || typeof position !== 'object' || 
                !Number.isFinite(position.x) || !Number.isFinite(position.y)) {
                console.error('位置值无效:', position);
                return false;
            }
            
            if (!scale || typeof scale !== 'object' || 
                !Number.isFinite(scale.x) || !Number.isFinite(scale.y)) {
                console.error('缩放值无效:', scale);
                return false;
            }
            
            // 验证缩放值必须为正数
            if (scale.x <= 0 || scale.y <= 0) {
                console.error('缩放值必须为正数:', scale);
                return false;
            }
            
            const preferences = {
                model_path: modelPath,
                position: position,
                scale: scale
            };
            const response = await fetch('/api/preferences', {
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
    resetModelPosition() {
        if (!this.currentModel || !this.pixi_app) {
            console.warn('无法复位：模型或PIXI应用未初始化');
            return;
        }

        try {
            model.anchor.set(0.65, 0.75);
            // 根据移动端/桌面端重置到默认位置和缩放
            if (isMobileWidth()) {
                // 移动端默认设置
                const scale = Math.min(
                    0.5,
                    window.innerHeight * 1.3 / 4000,
                    window.innerWidth * 1.2 / 2000
                );
                this.currentModel.scale.set(scale);
                this.currentModel.x = this.pixi_app.renderer.width * 0.5;
                this.currentModel.y = this.pixi_app.renderer.height * 0.28;
            } else {
                // 桌面端默认设置（靠右下）
                const scale = Math.min(
                    0.5,
                    (window.innerHeight * 0.75) / 7000,
                    (window.innerWidth * 0.6) / 7000
                );
                this.currentModel.scale.set(scale);
                this.currentModel.x = this.pixi_app.renderer.width;
                this.currentModel.y = this.pixi_app.renderer.height;
            }

            console.log('模型位置已复位到初始状态');

            // 保存复位后的位置（如果有模型路径）
            if (this._lastLoadedModelPath) {
                this.saveUserPreferences(
                    this._lastLoadedModelPath,
                    { x: this.currentModel.x, y: this.currentModel.y },
                    { x: this.currentModel.scale.x, y: this.currentModel.scale.y }
                );
            }
        } catch (error) {
            console.error('复位模型位置时出错:', error);
        }
    }
}

// 导出
window.Live2DModel = Live2DModel;
window.Live2DManager = Live2DManager;
window.isMobileWidth = isMobileWidth;

