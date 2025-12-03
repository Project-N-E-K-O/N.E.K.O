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
        
        // 已打开的设置窗口引用映射（URL -> Window对象）
        this._openSettingsWindows = {};

        // 口型同步控制
        this.mouthValue = 0; // 0~1
        this.mouthParameterId = null; // 例如 'ParamMouthOpenY' 或 'ParamO'
        this._mouthOverrideInstalled = false;
        this._origUpdateParameters = null;
        this._origExpressionUpdateParameters = null;
        this._mouthTicker = null;
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

    // 清除expression到默认状态（使用官方API）
    clearExpression() {
        if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.motionManager && this.currentModel.internalModel.motionManager.expressionManager) {
            try {
                this.currentModel.internalModel.motionManager.expressionManager.stopAllExpressions();
                this.currentModel.internalModel.motionManager.expressionManager.resetExpression();
                console.log('expression已使用官方API清除到默认状态');
            } catch (resetError) {
                console.warn('使用官方API清除expression失败:', resetError);
            }
        } else {
            console.warn('无法访问expressionManager，expression清除失败');
        }

        // 如存在常驻表情，清除后立即重放常驻，保证不被清掉
        this.applyPersistentExpressionsNative();
    }

    // 播放表情（优先使用 EmotionMapping.expressions）
    async playExpression(emotion) {
        if (!this.currentModel || !this.emotionMapping) {
            console.warn('无法播放表情：模型或映射配置未加载');
            return;
        }

        // EmotionMapping.expressions 规范：{ emotion: ["expressions/xxx.exp3.json", ...] }
        let expressionFiles = (this.emotionMapping.expressions && this.emotionMapping.expressions[emotion]) || [];

        // 兼容旧结构：从 FileReferences.Expressions 里按前缀分组
        if ((!expressionFiles || expressionFiles.length === 0) && this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
            const candidates = this.fileReferences.Expressions.filter(e => (e.Name || '').startsWith(emotion));
            expressionFiles = candidates.map(e => e.File).filter(Boolean);
        }

        if (!expressionFiles || expressionFiles.length === 0) {
            console.log(`未找到情感 ${emotion} 对应的表情，将跳过表情播放`);
            return;
        }

        const choiceFile = this.getRandomElement(expressionFiles);
        if (!choiceFile) return;
        
        try {
            // 计算表达文件路径（相对模型根目录）
            const expressionPath = this.resolveAssetPath(choiceFile);
            const response = await fetch(expressionPath);
            if (!response.ok) {
                throw new Error(`Failed to load expression: ${response.statusText}`);
            }
            
            const expressionData = await response.json();
            console.log(`加载表情文件: ${choiceFile}`, expressionData);
            
            // 方法1: 尝试使用原生expression API
            if (this.currentModel.expression) {
                try {
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
                    
                    // 如果找不到，回退到使用文件名
                    if (!expressionName) {
                        const base = String(choiceFile).split('/').pop() || '';
                        expressionName = base.replace('.exp3.json', '');
                    }
                    
                    console.log(`尝试使用原生API播放expression: ${expressionName} (file: ${choiceFile})`);
                    
                    const expression = await this.currentModel.expression(expressionName);
                    if (expression) {
                        console.log(`成功使用原生API播放expression: ${expressionName}`);
                        return; // 成功播放，直接返回
                    } else {
                        console.warn(`原生expression API未返回有效结果 (name: ${expressionName})，回退到手动参数设置`);
                    }
                } catch (error) {
                    console.warn('原生expression API出错:', error);
                }
            }
            
            // 方法2: 回退到手动参数设置
            console.log('使用手动参数设置播放expression');
            if (expressionData.Parameters) {
                for (const param of expressionData.Parameters) {
                    try {
                        this.currentModel.internalModel.coreModel.setParameterValueById(param.Id, param.Value);
                    } catch (paramError) {
                        console.warn(`设置参数 ${param.Id} 失败:`, paramError);
                    }
                }
            }
            
            console.log(`手动设置表情: ${choiceFile}`);
        } catch (error) {
            console.error('播放表情失败:', error);
        }

        // 重放常驻表情，确保不被覆盖
        try { await this.applyPersistentExpressionsNative(); } catch (e) {}
    }

    // 播放动作
    async playMotion(emotion) {
        if (!this.currentModel) {
            console.warn('无法播放动作：模型未加载');
            return;
        }

        // 优先使用 Cubism 原生 Motion Group（FileReferences.Motions）
        let motions = null;
        if (this.fileReferences && this.fileReferences.Motions && this.fileReferences.Motions[emotion]) {
            motions = this.fileReferences.Motions[emotion]; // 形如 [{ File: "motions/xxx.motion3.json" }, ...]
        } else if (this.emotionMapping && this.emotionMapping.motions && this.emotionMapping.motions[emotion]) {
            // 兼容 EmotionMapping.motions: ["motions/xxx.motion3.json", ...]
            motions = this.emotionMapping.motions[emotion].map(f => ({ File: f }));
        }
        if (!motions || motions.length === 0) {
            console.warn(`未找到情感 ${emotion} 对应的动作，但将保持表情`);
            // 如果没有找到对应的motion，设置一个短定时器以确保expression能够显示
            // 并且不设置回调来清除效果，让表情一直持续
            this.motionTimer = setTimeout(() => {
                this.motionTimer = null;
            }, 500); // 500ms应该足够让expression稳定显示
            return;
        }
        
        const choice = this.getRandomElement(motions);
        if (!choice || !choice.File) return;
        
        try {
            // 清除之前的动作定时器
            if (this.motionTimer) {
                console.log('检测到前一个motion正在播放，正在停止...');
                
                if (this.motionTimer.type === 'animation') {
                    cancelAnimationFrame(this.motionTimer.id);
                } else if (this.motionTimer.type === 'timeout') {
                    clearTimeout(this.motionTimer.id);
                } else if (this.motionTimer.type === 'motion') {
                    // 停止motion播放
                    try {
                        if (this.motionTimer.id && this.motionTimer.id.stop) {
                            this.motionTimer.id.stop();
                        }
                    } catch (motionError) {
                        console.warn('停止motion失败:', motionError);
                    }
                } else {
                    clearTimeout(this.motionTimer);
                }
                this.motionTimer = null;
                console.log('前一个motion已停止');
            }
            
            // 尝试使用Live2D模型的原生motion播放功能
            try {
                // 构建完整的motion路径（相对模型根目录）
                const motionPath = this.resolveAssetPath(choice.File);
                console.log(`尝试播放motion: ${motionPath}`);
                
                // 方法1: 直接使用模型的motion播放功能
                if (this.currentModel.motion) {
                    try {
                        console.log(`尝试播放motion: ${choice.File}`);
                        
                        // 使用情感名称作为motion组名，这样可以确保播放正确的motion
                        console.log(`尝试使用情感组播放motion: ${emotion}`);
                        
                const motion = await this.currentModel.motion(emotion);
                        
                        if (motion) {
                    console.log(`成功开始播放motion（情感组: ${emotion}，预期文件: ${choice.File}）`);
                            
                            // 获取motion的实际持续时间
                            let motionDuration = 5000; // 默认5秒
                            
                            // 尝试从motion文件获取持续时间
                            try {
                                const response = await fetch(motionPath);
                                if (response.ok) {
                                    const motionData = await response.json();
                                    if (motionData.Meta && motionData.Meta.Duration) {
                                        motionDuration = motionData.Meta.Duration * 1000;
                                    }
                                }
                            } catch (error) {
                                console.warn('无法获取motion持续时间，使用默认值');
                            }
                            
                            console.log(`预期motion持续时间: ${motionDuration}ms`);
                            
                            // 设置定时器在motion结束后清理motion参数（但保留expression）
                            this.motionTimer = setTimeout(() => {
                            console.log(`motion播放完成（预期文件: ${choice.File}），清除motion参数但保留expression`);
                                this.motionTimer = null;
                                this.clearEmotionEffects(); // 只清除motion参数，不清除expression
                            }, motionDuration);
                            
                            return; // 成功播放，直接返回
                        } else {
                            console.warn('motion播放失败');
                        }
                    } catch (error) {
                        console.warn('模型motion方法失败:', error);
                    }
                }
                
                // 方法2: 备用方案 - 如果方法1失败，尝试其他方法
                if (!this.motionTimer) {
                    console.log('方法1失败，尝试备用方案');
                    
                    // 这里可以添加其他备用方案，但目前方法1已经工作
                    console.warn('所有motion播放方法都失败，回退到简单动作');
                    this.playSimpleMotion(emotion);
                }
                
                // 如果所有方法都失败，回退到简单动作
                console.warn(`无法播放motion: ${choice.File}，回退到简单动作`);
                this.playSimpleMotion(emotion);
                
            } catch (error) {
                console.error('motion播放过程中出错:', error);
                this.playSimpleMotion(emotion);
            }
            
        } catch (error) {
            console.error('播放动作失败:', error);
            // 回退到简单动作
            this.playSimpleMotion(emotion);
        }
    }

    // 播放简单动作（回退方案）
    playSimpleMotion(emotion) {
        try {
            switch (emotion) {
                case 'happy':
                    // 轻微点头
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 8);
                    const happyTimer = setTimeout(() => {
                        this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 0);
                        this.motionTimer = null;
                        // motion完成后清除motion参数，但保留expression
                        this.clearEmotionEffects();
                    }, 1000);
                    this.motionTimer = { type: 'timeout', id: happyTimer };
                    break;
                case 'sad':
                    // 轻微低头
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', -5);
                    const sadTimer = setTimeout(() => {
                        this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 0);
                        this.motionTimer = null;
                        // motion完成后清除motion参数，但保留expression
                        this.clearEmotionEffects();
                    }, 1200);
                    this.motionTimer = { type: 'timeout', id: sadTimer };
                    break;
                case 'angry':
                    // 轻微摇头
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleX', 5);
                    setTimeout(() => {
                        this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleX', -5);
                    }, 400);
                    const angryTimer = setTimeout(() => {
                        this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleX', 0);
                        this.motionTimer = null;
                        // motion完成后清除motion参数，但保留expression
                        this.clearEmotionEffects();
                    }, 800);
                    this.motionTimer = { type: 'timeout', id: angryTimer };
                    break;
                case 'surprised':
                    // 轻微后仰
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', -8);
                    const surprisedTimer = setTimeout(() => {
                        this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 0);
                        this.motionTimer = null;
                        // motion完成后清除motion参数，但保留expression
                        this.clearEmotionEffects();
                    }, 800);
                    this.motionTimer = { type: 'timeout', id: surprisedTimer };
                    break;
                default:
                    // 中性状态，重置角度
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleX', 0);
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 0);
                    break;
            }
            console.log(`播放简单动作: ${emotion}`);
        } catch (paramError) {
            console.warn('设置简单动作参数失败:', paramError);
        }
    }

    // 清理当前情感效果（清除motion参数，但保留expression）
    clearEmotionEffects() {
        let hasCleared = false;
        
        console.log('开始清理motion效果（保留expression）...');
        
        // 清除动作定时器
        if (this.motionTimer) {
            console.log(`清除motion定时器，类型: ${this.motionTimer.type || 'unknown'}`);
            
            if (this.motionTimer.type === 'animation') {
                // 取消动画帧
                cancelAnimationFrame(this.motionTimer.id);
            } else if (this.motionTimer.type === 'timeout') {
                // 清除普通定时器
                clearTimeout(this.motionTimer.id);
            } else if (this.motionTimer.type === 'motion') {
                // 停止motion播放
                try {
                    if (this.motionTimer.id && this.motionTimer.id.stop) {
                        this.motionTimer.id.stop();
                    }
                } catch (motionError) {
                    console.warn('停止motion失败:', motionError);
                }
            } else {
                // 兼容旧的定时器格式
                clearTimeout(this.motionTimer);
            }
            this.motionTimer = null;
            hasCleared = true;
        }
        
        // 停止所有motion并重置所有参数到默认值
        if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.motionManager) {
            try {
                // 使用官方API停止所有motion
                if (this.currentModel.internalModel.motionManager.stopAllMotions) {
                    this.currentModel.internalModel.motionManager.stopAllMotions();
                    console.log('已停止所有motion');
                    hasCleared = true;
                }
            } catch (motionError) {
                console.warn('停止motion失败:', motionError);
            }
        }
        
        // 重置所有参数到默认值（关键步骤）
        if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.coreModel) {
            try {
                const coreModel = this.currentModel.internalModel.coreModel;
                const paramCount = coreModel.getParameterCount();
                
                console.log(`开始重置${paramCount}个参数到默认值...`);
                
                // 遍历所有参数，将其重置为默认值
                for (let i = 0; i < paramCount; i++) {
                    try {
                        const paramId = coreModel.getParameterId(i);
                        const defaultValue = coreModel.getParameterDefaultValueByIndex(i);
                        
                        // 跳过嘴巴相关参数（这些由口型同步控制）
                        if (paramId === 'ParamMouthOpenY' || paramId === 'ParamO') {
                            continue;
                        }
                        
                        // 重置参数到默认值
                        coreModel.setParameterValueByIndex(i, defaultValue);
                    } catch (e) {
                        // 单个参数重置失败不影响其他参数
                    }
                }
                try {
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleX', 0);
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 0);
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleZ', 0);
                    console.log('已使用备用方案重置角度参数');
                } catch (e) {}
                
                console.log('所有motion参数已重置到默认值');
            } catch (paramError) {
                console.warn('重置参数失败，使用备用方案:', paramError);
                // 备用方案：至少重置角度参数
                try {
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleX', 0);
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleY', 0);
                    this.currentModel.internalModel.coreModel.setParameterValueById('ParamAngleZ', 0);
                    console.log('已使用备用方案重置角度参数');
                } catch (e) {}
            }
        }
        
        // 重新应用当前的expression（这样expression会覆盖需要修改的参数）
        if (this.currentEmotion && this.currentEmotion !== 'neutral') {
            try {
                console.log(`重新应用当前emotion的expression: ${this.currentEmotion}`);
                this.playExpression(this.currentEmotion);
            } catch (e) {
                console.warn('重新应用expression失败:', e);
            }
        }
        
        // 重新应用常驻表情
        try {
            this.applyPersistentExpressionsNative();
        } catch (e) {
            console.warn('重新应用常驻表情失败:', e);
        }
        
        console.log('motion效果清理完成，所有参数已重置，expression已重新应用');
    }

    // 设置情感并播放对应的表情和动作
    async setEmotion(emotion) {
        // 如果情感相同，有一定概率随机播放motion（不改变expression）
        if (this.currentEmotion === emotion) {
            // 50% 的概率随机播放motion（不清除和重播expression）
            if (Math.random() < 0.5) {
                console.log(`情感相同 (${emotion})，随机播放motion（保留当前expression）`);
                await this.playMotion(emotion);
            } else {
                console.log(`情感相同 (${emotion})，跳过播放`);
                return;
            }
        }
        
        // 防止快速连续点击
        if (this.isEmotionChanging) {
            console.log('情感切换中，忽略新的情感请求');
            return;
        }
        
        console.log(`新情感触发: ${emotion}，当前情感: ${this.currentEmotion}`);
        
        // 设置标志，防止快速连续点击
        this.isEmotionChanging = true;
        
        try {
            console.log(`开始设置新情感: ${emotion}`);
            
            // 清理之前的情感效果（包括定时器等）
            this.clearEmotionEffects();
            
            // 使用官方API清除expression到默认状态
            this.clearExpression();
            
            this.currentEmotion = emotion;
            console.log(`情感已更新为: ${emotion}`);
            
            // 暂停idle动画，防止覆盖我们的动作
            if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.motionManager) {
                try {
                    // 尝试停止所有正在播放的动作
                    if (this.currentModel.internalModel.motionManager.stopAllMotions) {
                        this.currentModel.internalModel.motionManager.stopAllMotions();
                        console.log('已停止idle动画');
                    }
                } catch (motionError) {
                    console.warn('停止idle动画失败:', motionError);
                }
            }
            
            // 播放表情
            await this.playExpression(emotion);
            
            // 播放动作
            await this.playMotion(emotion);
            
            console.log(`情感 ${emotion} 设置完成`);
        } catch (error) {
            console.error(`设置情感 ${emotion} 失败:`, error);
        } finally {
            // 重置标志
            this.isEmotionChanging = false;
        }
    }

    // 加载模型
    async loadModel(modelPath, options = {}) {
        if (!this.pixi_app) {
            throw new Error('PIXI 应用未初始化，请先调用 initPIXI()');
        }

        // 移除当前模型
        if (this.currentModel) {
            // 先清空常驻表情记录
            this.teardownPersistentExpressions();

            // 尝试还原之前覆盖的 updateParameters，避免旧引用在新模型上报错
            try {
                const mm = this.currentModel.internalModel && this.currentModel.internalModel.motionManager;
                if (mm) {
                    if (this._mouthOverrideInstalled && typeof this._origUpdateParameters === 'function') {
                        try { mm.updateParameters = this._origUpdateParameters; } catch (_) {}
                    }
                    if (mm && mm.expressionManager && this._mouthOverrideInstalled && typeof this._origExpressionUpdateParameters === 'function') {
                        try { mm.expressionManager.updateParameters = this._origExpressionUpdateParameters; } catch (_) {}
                    }
                }
            } catch (_) {}
            this._mouthOverrideInstalled = false;
            this._origUpdateParameters = null;
            this._origExpressionUpdateParameters = null;
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
            const model = await Live2DModel.from(modelPath, { autoInteract: false });
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
                console.log('已安装口型覆盖');
            } catch (e) {
                console.warn('安装口型覆盖失败:', e);
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
                    console.log('已加载情绪映射:', this.emotionMapping);
                } else {
                    console.warn('模型配置中未找到 settings.json，无法加载情绪映射');
                }
            }

            // 先从服务器同步映射（覆盖“常驻”），再设置常驻表情
            try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
            // 设置常驻表情（根据 EmotionMapping.expressions.常驻 或 FileReferences 前缀推导）
            await this.setupPersistentExpressions();

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
                    const model = await Live2DModel.from(defaultModelPath, { autoInteract: false });
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

                    // 先从服务器同步映射（覆盖"常驻"），再设置常驻表情
                    try { await this.syncEmotionMappingWithServer({ replacePersistentOnly: true }); } catch(_) {}
                    // 设置常驻表情（根据 EmotionMapping.expressions.常驻 或 FileReferences 前缀推导）
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
    }

    // 不再需要预解析嘴巴参数ID，保留占位以兼容旧代码调用
    resolveMouthParameterId() { return null; }

    // 安装覆盖：在 motion 参数更新后强制写入口型参数
    installMouthOverride() {
        if (!this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.motionManager) {
            throw new Error('模型未就绪，无法安装口型覆盖');
        }

        const mm = this.currentModel.internalModel.motionManager;

        // 如果之前装过在其他模型上，先尝试还原
        try {
            if (this._mouthOverrideInstalled) {
                if (typeof this._origUpdateParameters === 'function') {
                    try { mm.updateParameters = this._origUpdateParameters; } catch (_) {}
                }
                if (mm.expressionManager && typeof this._origExpressionUpdateParameters === 'function') {
                    try { mm.expressionManager.updateParameters = this._origExpressionUpdateParameters; } catch (_) {}
                }
                this._mouthOverrideInstalled = false;
                this._origUpdateParameters = null;
                this._origExpressionUpdateParameters = null;
            }
        } catch (_) {}

        if (typeof mm.updateParameters !== 'function') {
            throw new Error('motionManager.updateParameters 不可用');
        }

        // 绑定原函数并覆盖
        const orig = mm.updateParameters.bind(mm);
        mm.updateParameters = (coreModel, now) => {
            const updated = orig(coreModel, now);
            try {
                const mouthIds = ['ParamMouthOpenY', 'ParamO'];
                for (const id of mouthIds) {
                    try {
                        if (coreModel.getParameterIndex(id) !== -1) {
                            coreModel.setParameterValueById(id, this.mouthValue, 1);
                        }
                    } catch (_) {}
                }
            } catch (_) {}
            return updated;
        };
        this._origUpdateParameters = orig; // 保存可还原的实现（已绑定）

        // 也覆盖 expressionManager.updateParameters，防止表情参数覆盖嘴巴
        if (mm.expressionManager && typeof mm.expressionManager.updateParameters === 'function') {
            const origExp = mm.expressionManager.updateParameters.bind(mm.expressionManager);
            mm.expressionManager.updateParameters = (coreModel, now) => {
                const updated = origExp(coreModel, now);
                try {
                    const mouthIds = ['ParamMouthOpenY', 'ParamO'];
                    for (const id of mouthIds) {
                        try {
                            if (coreModel.getParameterIndex(id) !== -1) {
                                coreModel.setParameterValueById(id, this.mouthValue, 1);
                            }
                        } catch (_) {}
                    }
                } catch (_) {}
                return updated;
            };
            this._origExpressionUpdateParameters = origExp;
        } else {
            this._origExpressionUpdateParameters = null;
        }

        // 若此前使用了 ticker 覆盖，确保移除
        if (this._mouthTicker && this.pixi_app && this.pixi_app.ticker) {
            try { this.pixi_app.ticker.remove(this._mouthTicker); } catch (_) {}
            this._mouthTicker = null;
        }

        this._mouthOverrideInstalled = true;
    }

    // 设置嘴巴开合值（0~1）
    setMouth(value) {
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

    // 应用模型设置
    applyModelSettings(model, options) {
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
                // 使用默认设置
                const scale = Math.min(
                    0.5,
                    (window.innerHeight * 0.75) / 7000,
                    (window.innerWidth * 0.6) / 7000
                );
                model.scale.set(scale);
                model.x = this.pixi_app.renderer.width * 0.5;
                model.y = this.pixi_app.renderer.height * 0.5;
            }
            model.anchor.set(0.65, 0.75);
        }
    }

    // 设置拖拽功能
    setupDragAndDrop(model) {
        model.interactive = true;
        this.pixi_app.stage.interactive = true;
        this.pixi_app.stage.hitArea = this.pixi_app.screen;

        let isDragging = false;
        let dragStartPos = new PIXI.Point();

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
        });

        const onDragEnd = () => {
            if (isDragging) {
                isDragging = false;
                document.getElementById('live2d-canvas').style.cursor = 'grab';
            }
        };

        this.pixi_app.stage.on('pointerup', onDragEnd);
        this.pixi_app.stage.on('pointerupoutside', onDragEnd);

        this.pixi_app.stage.on('pointermove', (event) => {
            if (isDragging) {
                // 再次检查是否变成多点触摸
                const originalEvent = event.data.originalEvent;
                if (originalEvent && originalEvent.touches && originalEvent.touches.length > 1) {
                    // 如果变成多点触摸，停止拖拽
                    isDragging = false;
                    document.getElementById('live2d-canvas').style.cursor = 'grab';
                    return;
                }
                
                const newPosition = event.data.global;
                model.x = newPosition.x - dragStartPos.x;
                model.y = newPosition.y - dragStartPos.y;
            }
        });
    }

    // 设置滚轮缩放
    setupWheelZoom(model) {
        const onWheelScroll = (event) => {
            if (this.isLocked || !this.currentModel) return;
            event.preventDefault();
            const scaleFactor = 1.1;
            const oldScale = this.currentModel.scale.x;
            let newScale = event.deltaY < 0 ? oldScale * scaleFactor : oldScale / scaleFactor;
            this.currentModel.scale.set(newScale);
        };

        const view = this.pixi_app.view;
        if (view.lastWheelListener) {
            view.removeEventListener('wheel', view.lastWheelListener);
        }
        view.addEventListener('wheel', onWheelScroll, { passive: false });
        view.lastWheelListener = onWheelScroll;
    }
    
    // 设置触摸缩放（双指捏合）
    setupTouchZoom(model) {
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
        
        const onTouchEnd = (event) => {
            // 当手指数量小于2时，停止缩放
            if (event.touches.length < 2) {
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
    }
    
    // 设置 HTML 锁形图标（保留用于兼容）
    setupHTMLLockIcon(model) {
        const container = document.getElementById('live2d-canvas');
        
        // 在 l2d_manager 等页面，默认解锁并可交互
        if (!document.getElementById('chat-container')) {
            this.isLocked = false;
            container.style.pointerEvents = 'auto';
            return;
        }

        const lockIcon = document.createElement('div');
        lockIcon.id = 'live2d-lock-icon';
        lockIcon.innerText = this.isLocked ? '🔒' : '🔓';
        Object.assign(lockIcon.style, {
            position: 'fixed',
            zIndex: '30',
            fontSize: '24px',
            cursor: 'pointer',
            userSelect: 'none',
            textShadow: '0 0 4px black',
            pointerEvents: 'auto',
            display: 'none' // 默认隐藏
        });

        document.body.appendChild(lockIcon);
        this._lockIconElement = lockIcon;

        lockIcon.addEventListener('click', (e) => {
            e.stopPropagation();
            this.isLocked = !this.isLocked;
            lockIcon.innerText = this.isLocked ? '🔒' : '🔓';

            if (this.isLocked) {
                container.style.pointerEvents = 'none';
            } else {
                container.style.pointerEvents = 'auto';
            }
        });

        // 初始状态
        container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

        // 持续更新图标位置（保存回调用于移除）
        const tick = () => {
            try {
                if (!model || !model.parent) {
                    // 模型可能已被销毁或从舞台移除
                    if (lockIcon) lockIcon.style.display = 'none';
                    return;
                }
                const bounds = model.getBounds();
                const screenWidth = window.innerWidth;
                const screenHeight = window.innerHeight;

                const targetX = bounds.right * 0.7 + bounds.left * 0.3;
                const targetY = bounds.top * 0.3 + bounds.bottom * 0.7;

                lockIcon.style.left = `${Math.min(targetX, screenWidth - 40)}px`;
                lockIcon.style.top = `${Math.min(targetY, screenHeight - 40)}px`;
            } catch (_) {
                // 忽略单帧异常
            }
        };
        this._lockIconTicker = tick;
        this.pixi_app.ticker.add(tick);
    }

    // 设置浮动按钮系统（新的控制面板）
    setupFloatingButtons(model) {
        const container = document.getElementById('live2d-canvas');
        
        // 在 l2d_manager 等页面不显示
        if (!document.getElementById('chat-container')) {
            this.isLocked = false;
            container.style.pointerEvents = 'auto';
            return;
        }

        // 创建按钮容器
        const buttonsContainer = document.createElement('div');
        buttonsContainer.id = 'live2d-floating-buttons';
        Object.assign(buttonsContainer.style, {
            position: 'fixed',
            zIndex: '30',
            pointerEvents: 'none',
            display: 'none', // 初始隐藏，鼠标靠近时才显示
            flexDirection: 'column',
            gap: '12px'
        });
        document.body.appendChild(buttonsContainer);
        this._floatingButtonsContainer = buttonsContainer;

		// 响应式：小屏时固定在右下角并横向排列（使用全局 isMobileWidth）
		const applyResponsiveFloatingLayout = () => {
			if (isMobileWidth()) {
				// 移动端：固定在右下角，纵向排布，整体上移100px
				buttonsContainer.style.flexDirection = 'column';
				buttonsContainer.style.bottom = '116px';
				buttonsContainer.style.right = '16px';
				buttonsContainer.style.left = '';
				buttonsContainer.style.top = '';
			} else {
				// 桌面端：恢复纵向排布，由 ticker 动态定位
				buttonsContainer.style.flexDirection = 'column';
				buttonsContainer.style.bottom = '';
				buttonsContainer.style.right = '';
			}
		};
		applyResponsiveFloatingLayout();
		window.addEventListener('resize', applyResponsiveFloatingLayout);

        // 定义按钮配置（从上到下：麦克风、显示屏、锤子、设置、睡觉）
        // 添加版本号防止缓存（更新图标时修改这个版本号）
        const iconVersion = '?v=' + Date.now();
        
        const buttonConfigs = [
            { id: 'mic', emoji: '🎤', title: window.t ? window.t('buttons.voiceControl') : '语音控制', titleKey: 'buttons.voiceControl', hasPopup: true, toggle: true, separatePopupTrigger: true, iconOff: '/static/icons/mic_icon_off.png' + iconVersion, iconOn: '/static/icons/mic_icon_on.png' + iconVersion },
            { id: 'screen', emoji: '🖥️', title: window.t ? window.t('buttons.screenShare') : '屏幕分享', titleKey: 'buttons.screenShare', hasPopup: false, toggle: true, iconOff: '/static/icons/screen_icon_off.png' + iconVersion, iconOn: '/static/icons/screen_icon_on.png' + iconVersion },
            { id: 'agent', emoji: '🔨', title: window.t ? window.t('buttons.agentTools') : 'Agent工具', titleKey: 'buttons.agentTools', hasPopup: true, popupToggle: true, exclusive: 'settings', iconOff: '/static/icons/Agent_off.png' + iconVersion, iconOn: '/static/icons/Agent_on.png' + iconVersion },
            { id: 'settings', emoji: '⚙️', title: window.t ? window.t('buttons.settings') : '设置', titleKey: 'buttons.settings', hasPopup: true, popupToggle: true, exclusive: 'agent', iconOff: '/static/icons/set_off.png' + iconVersion, iconOn: '/static/icons/set_on.png' + iconVersion },
            { id: 'goodbye', emoji: '💤', title: window.t ? window.t('buttons.leave') : '请她离开', titleKey: 'buttons.leave', hasPopup: false, iconOff: '/static/icons/rest_off.png' + iconVersion, iconOn: '/static/icons/rest_on.png' + iconVersion }
        ];

        // 创建主按钮
        buttonConfigs.forEach(config => {
			// 移动端隐藏 agent 和 goodbye 按钮
			if (isMobileWidth() && (config.id === 'agent' || config.id === 'goodbye')) {
				return;
			}
            const btnWrapper = document.createElement('div');
            btnWrapper.style.position = 'relative';
            btnWrapper.style.display = 'flex';
            btnWrapper.style.alignItems = 'center';
            btnWrapper.style.gap = '8px';

            const btn = document.createElement('div');
            btn.id = `live2d-btn-${config.id}`;
            btn.className = 'live2d-floating-btn';
            btn.title = config.title;
            if (config.titleKey) {
                btn.setAttribute('data-i18n-title', config.titleKey);
            }
            
            let imgOff = null; // off状态图片
            let imgOn = null;  // on状态图片
            
            // 优先使用带off/on的PNG图标，如果有iconOff和iconOn则使用叠加方式实现淡入淡出
            if (config.iconOff && config.iconOn) {
                // 创建图片容器，用于叠加两张图片
                const imgContainer = document.createElement('div');
                Object.assign(imgContainer.style, {
                    position: 'relative',
                    width: '48px',
                    height: '48px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                });
                
                // 创建off状态图片（默认显示）
                imgOff = document.createElement('img');
                imgOff.src = config.iconOff;
                imgOff.alt = config.title;
                Object.assign(imgOff.style, {
                    position: 'absolute',
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    pointerEvents: 'none',
                    opacity: '1',
                    transition: 'opacity 0.3s ease'
                });
                
                // 创建on状态图片（默认隐藏）
                imgOn = document.createElement('img');
                imgOn.src = config.iconOn;
                imgOn.alt = config.title;
                Object.assign(imgOn.style, {
                    position: 'absolute',
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    pointerEvents: 'none',
                    opacity: '0',
                    transition: 'opacity 0.3s ease'
                });
                
                imgContainer.appendChild(imgOff);
                imgContainer.appendChild(imgOn);
                btn.appendChild(imgContainer);
            } else if (config.icon) {
                // 兼容单图标配置
                const img = document.createElement('img');
                img.src = config.icon;
                img.alt = config.title;
                Object.assign(img.style, {
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    pointerEvents: 'none'
                });
                btn.appendChild(img);
            } else if (config.emoji) {
                // 备用方案：使用emoji
                btn.innerText = config.emoji;
            }
            
            Object.assign(btn.style, {
                width: '48px',
                height: '48px',
                borderRadius: '50%',
                background: 'rgba(255, 255, 255, 0.7)',  // 白色背景，70透明度（30透明度）
                backdropFilter: 'blur(10px)',  // 保留模糊效果
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                cursor: 'pointer',
                userSelect: 'none',
                boxShadow: '0 2px 8px rgba(0, 0, 0, 0.2)',  // 保留阴影
                transition: 'all 0.2s ease',
                pointerEvents: 'auto'
            });

            // 鼠标悬停效果：通过opacity切换图标，实现淡入淡出
            btn.addEventListener('mouseenter', () => {
                btn.style.transform = 'scale(1.1)';
                btn.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.3)';
                // 淡出off图标，淡入on图标
                if (imgOff && imgOn) {
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                }
            });
            btn.addEventListener('mouseleave', () => {
                btn.style.transform = 'scale(1)';
                btn.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.2)';
                // 恢复原始背景色（根据按钮状态）
                const isActive = btn.dataset.active === 'true';
                const popup = document.getElementById(`live2d-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                
                if (isActive || isPopupVisible) {
                    // 保持和悬停时一样的背景色（白色）
                    btn.style.background = 'rgba(255, 255, 255, 0.7)';
                } else {
                    btn.style.background = 'rgba(255, 255, 255, 0.7)';
                }
                
                // 根据按钮激活状态决定显示哪个图标
                // 如果按钮已激活，保持显示on图标；否则显示off图标
                if (imgOff && imgOn) {
                    if (isActive || isPopupVisible) {
                        // 激活状态：保持on图标
                        imgOff.style.opacity = '0';
                        imgOn.style.opacity = '1';
                    } else {
                        // 未激活状态：显示off图标
                        imgOff.style.opacity = '1';
                        imgOn.style.opacity = '0';
                    }
                }
            });

            // popupToggle: 按钮点击切换弹出框显示，弹出框显示时按钮变蓝
            if (config.popupToggle) {
                const popup = this.createPopup(config.id);
                btnWrapper.appendChild(btn);
                
                // 直接将弹出框添加到btnWrapper，这样定位更准确
                btnWrapper.appendChild(popup);
                
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    
                    // 检查弹出框当前状态
                    const isPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                    
                    // 实现互斥逻辑：如果有exclusive配置，关闭对方
                    if (!isPopupVisible && config.exclusive) {
                        this.closePopupById(config.exclusive);
                    }
                    
                    // 切换弹出框
                    this.showPopup(config.id, popup);
                    
                    // 等待弹出框状态更新后更新图标状态
                    setTimeout(() => {
                        const newPopupVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
                        // 根据弹出框状态更新图标
                        if (imgOff && imgOn) {
                            if (newPopupVisible) {
                                // 弹出框显示：显示on图标
                                imgOff.style.opacity = '0';
                                imgOn.style.opacity = '1';
                            } else {
                                // 弹出框隐藏：显示off图标
                                imgOff.style.opacity = '1';
                                imgOn.style.opacity = '0';
                            }
                        }
                    }, 50);
                });
                
            } else if (config.toggle) {
                // Toggle 状态（可能同时有弹出框）
                btn.dataset.active = 'false';
                
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    
                    // 对于麦克风按钮，在计算状态之前就检查 micButton 的状态
                    if (config.id === 'mic') {
                        const micButton = document.getElementById('micButton');
                        if (micButton && micButton.classList.contains('active')) {
                            // 检查是否正在录音：如果 isRecording 为 true，说明已经启动成功，允许点击退出
                            // 如果 isRecording 为 false，说明正在启动过程中，阻止点击
                            const isRecording = window.isRecording || false; // 从全局获取 isRecording 状态
                            
                            if (!isRecording) {
                                // 正在启动过程中，强制保持激活状态，不切换
                                // 确保浮动按钮状态与 micButton 同步
                                if (btn.dataset.active !== 'true') {
                                    btn.dataset.active = 'true';
                                    if (imgOff && imgOn) {
                                        imgOff.style.opacity = '0';
                                        imgOn.style.opacity = '1';
                                    }
                                }
                                return; // 直接返回，不执行任何状态切换或事件触发
                            }
                            // 如果 isRecording 为 true，说明已经启动成功，允许继续执行（可以退出）
                        }
                    }
                    
                    const isActive = btn.dataset.active === 'true';
                    const newActive = !isActive;
                    
                    btn.dataset.active = newActive.toString();
                    
                    // 更新图标状态
                    if (imgOff && imgOn) {
                        if (newActive) {
                            // 激活：显示on图标
                            imgOff.style.opacity = '0';
                            imgOn.style.opacity = '1';
                        } else {
                            // 未激活：显示off图标
                            imgOff.style.opacity = '1';
                            imgOn.style.opacity = '0';
                        }
                    }
                    
                    // 触发自定义事件
                    const event = new CustomEvent(`live2d-${config.id}-toggle`, {
                        detail: { active: newActive }
                    });
                    window.dispatchEvent(event);
                });
                
                // 先添加主按钮到包装器
                btnWrapper.appendChild(btn);
                
                // 如果有弹出框且需要独立的触发器（仅麦克风）
				if (config.hasPopup && config.separatePopupTrigger) {
					// 手机模式下移除麦克风弹窗与触发器
					if (isMobileWidth() && config.id === 'mic') {
						buttonsContainer.appendChild(btnWrapper);
						this._floatingButtons[config.id] = { 
							button: btn, 
							wrapper: btnWrapper,
							imgOff: imgOff,
							imgOn: imgOn
						};
						return;
					}
                    const popup = this.createPopup(config.id);
                    
                    // 创建三角按钮（用于触发弹出框）
                    const triggerBtn = document.createElement('div');
                    triggerBtn.innerText = '▶';
                    Object.assign(triggerBtn.style, {
                        width: '24px',
                        height: '24px',
                        borderRadius: '50%',
                        background: 'rgba(255, 255, 255, 0.7)',  // 与其他按钮一致的不透明度
                        backdropFilter: 'blur(10px)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '13px',
                        color: '#44b7fe',  // 设置图标颜色
                        cursor: 'pointer',
                        userSelect: 'none',
                        boxShadow: '0 2px 8px rgba(0, 0, 0, 0.2)',
                        transition: 'all 0.2s ease',
                        pointerEvents: 'auto',
                        marginLeft: '-10px'
                    });
                    
                    triggerBtn.addEventListener('mouseenter', () => {
                        triggerBtn.style.transform = 'scale(1.1)';
                        triggerBtn.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.3)';
                    });
                    triggerBtn.addEventListener('mouseleave', () => {
                        triggerBtn.style.transform = 'scale(1)';
                        triggerBtn.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.2)';
                    });
                    
                    triggerBtn.addEventListener('click', async (e) => {
                        e.stopPropagation();
                        
                        // 如果是麦克风弹出框，先加载麦克风列表
                        if (config.id === 'mic' && window.renderFloatingMicList) {
                            await window.renderFloatingMicList();
                        }
                        
                        this.showPopup(config.id, popup);
                    });
                    
                    // 创建包装器用于三角按钮和弹出框（相对定位）
                    const triggerWrapper = document.createElement('div');
                    triggerWrapper.style.position = 'relative';
                    triggerWrapper.appendChild(triggerBtn);
                    triggerWrapper.appendChild(popup);
                    
                    btnWrapper.appendChild(triggerWrapper);
                }
            } else {
                // 普通点击按钮
                btnWrapper.appendChild(btn);
                btn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const event = new CustomEvent(`live2d-${config.id}-click`);
                    window.dispatchEvent(event);
                });
            }

            buttonsContainer.appendChild(btnWrapper);
            this._floatingButtons[config.id] = { 
                button: btn, 
                wrapper: btnWrapper,
                imgOff: imgOff,  // 保存图标引用
                imgOn: imgOn      // 保存图标引用
            };
        });

        console.log('[Live2D] 所有浮动按钮已创建完成');

        // 创建独立的"请她回来"按钮（固定在页面中间）
        const returnButtonContainer = document.createElement('div');
        returnButtonContainer.id = 'live2d-return-button-container';
        Object.assign(returnButtonContainer.style, {
            position: 'fixed',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            zIndex: '30',
            pointerEvents: 'none',
            display: 'none' // 初始隐藏，只在点击"请她离开"后显示
        });

        const returnBtn = document.createElement('div');
        returnBtn.id = 'live2d-btn-return';
        returnBtn.className = 'live2d-return-btn';
        returnBtn.title = window.t ? window.t('buttons.return') : '请她回来';
        returnBtn.setAttribute('data-i18n-title', 'buttons.return');
        
        // 使用与"请她离开"相同的图标
        const imgOff = document.createElement('img');
        imgOff.src = '/static/icons/rest_off.png' + iconVersion;
        imgOff.alt = window.t ? window.t('buttons.return') : '请她回来';
        Object.assign(imgOff.style, {
            width: '64px',
            height: '64px',
            objectFit: 'contain',
            pointerEvents: 'none',
            opacity: '1',
            transition: 'opacity 0.3s ease'
        });
        
        const imgOn = document.createElement('img');
        imgOn.src = '/static/icons/rest_on.png' + iconVersion;
        imgOn.alt = window.t ? window.t('buttons.return') : '请她回来';
        Object.assign(imgOn.style, {
            position: 'absolute',
            width: '64px',
            height: '64px',
            objectFit: 'contain',
            pointerEvents: 'none',
            opacity: '0',
            transition: 'opacity 0.3s ease'
        });
        
        Object.assign(returnBtn.style, {
            width: '64px',
            height: '64px',
            borderRadius: '50%',
            background: 'rgba(255, 255, 255, 0.8)',
            backdropFilter: 'blur(10px)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            userSelect: 'none',
            boxShadow: '0 4px 16px rgba(0, 0, 0, 0.3)',
            transition: 'all 0.3s ease',
            pointerEvents: 'auto',
            position: 'relative'
        });

        // 悬停效果
        returnBtn.addEventListener('mouseenter', () => {
            returnBtn.style.transform = 'scale(1.1)';
            returnBtn.style.boxShadow = '0 6px 20px rgba(0, 0, 0, 0.4)';
            imgOff.style.opacity = '0';
            imgOn.style.opacity = '1';
        });

        returnBtn.addEventListener('mouseleave', () => {
            returnBtn.style.transform = 'scale(1)';
            returnBtn.style.boxShadow = '0 4px 16px rgba(0, 0, 0, 0.3)';
            imgOff.style.opacity = '1';
            imgOn.style.opacity = '0';
        });

        returnBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const event = new CustomEvent('live2d-return-click');
            window.dispatchEvent(event);
        });

        returnBtn.appendChild(imgOff);
        returnBtn.appendChild(imgOn);
        returnButtonContainer.appendChild(returnBtn);
        document.body.appendChild(returnButtonContainer);
        this._returnButtonContainer = returnButtonContainer;

        // 初始状态
        container.style.pointerEvents = this.isLocked ? 'none' : 'auto';

        // 持续更新按钮位置（在角色腰部右侧，垂直居中）
        const tick = () => {
            try {
                if (!model || !model.parent) {
                    return;
                }
				// 移动端固定位置，不随模型移动
				if (isMobileWidth()) {
					return;
				}
                const bounds = model.getBounds();
                const screenWidth = window.innerWidth;
                const screenHeight = window.innerHeight;

                // X轴：定位在角色右侧（与锁按钮类似的横向位置）
                const targetX = bounds.right * 0.8 + bounds.left * 0.2;
                
                // Y轴：工具栏下边缘对齐模型腰部（中间位置）
                const modelCenterY = (bounds.top + bounds.bottom) / 2;
                // 估算工具栏高度：5个按钮(48px) + 4个间隔(12px) = 288px
                const estimatedToolbarHeight = 200;
                // 让工具栏的下边缘位于模型中间，所以top = 中间 - 高度
                const targetY = modelCenterY - estimatedToolbarHeight;

                buttonsContainer.style.left = `${Math.min(targetX, screenWidth - 80)}px`;
                // 确保工具栏不会超出屏幕顶部
                buttonsContainer.style.top = `${Math.max(targetY, 20)}px`;
                // 不要在这里设置 display，让鼠标检测逻辑来控制显示/隐藏
            } catch (_) {
                // 忽略单帧异常
            }
        };
        this._floatingButtonsTicker = tick;
        this.pixi_app.ticker.add(tick);
        
        // 页面加载时先显示5秒
        setTimeout(() => {
            // 显示浮动按钮容器
            buttonsContainer.style.display = 'flex';
            
            setTimeout(() => {
                // 5秒后的隐藏逻辑：如果鼠标不在附近就隐藏
                if (!this.isFocusing) {
                    buttonsContainer.style.display = 'none';
                }
            }, 5000);
        }, 100); // 延迟100ms确保位置已计算
    }

    // 创建弹出框
    createPopup(buttonId) {
        const popup = document.createElement('div');
        popup.id = `live2d-popup-${buttonId}`;
        popup.className = 'live2d-popup';
        
        Object.assign(popup.style, {
            position: 'absolute',
            left: '100%',
            top: '0',
            marginLeft: '8px',
            background: 'rgba(255, 255, 255, 0.7)',  // 与按钮一致的70%不透明度
            backdropFilter: 'blur(10px)',
            borderRadius: '12px',
            padding: '8px',
            boxShadow: '0 2px 12px rgba(0, 0, 0, 0.2)',
            display: 'none',
            flexDirection: 'column',
            gap: '6px',
            minWidth: '180px',
            maxHeight: '200px',
            overflowY: 'auto',
            pointerEvents: 'auto',
            opacity: '0',
            transform: 'translateX(-10px)',
            transition: 'opacity 0.2s ease, transform 0.2s ease'
        });

        // 根据不同按钮创建不同的弹出内容
        if (buttonId === 'mic') {
            // 麦克风选择列表（将从页面中获取）
            popup.id = 'live2d-mic-popup';
        } else if (buttonId === 'agent') {
            // Agent工具开关组
            
            // 添加状态显示栏
            const statusDiv = document.createElement('div');
            statusDiv.id = 'live2d-agent-status';
            Object.assign(statusDiv.style, {
                fontSize: '12px',
                color: '#4f8cff',
                padding: '6px 8px',
                borderRadius: '6px',
                background: 'rgba(79, 140, 255, 0.05)',
                marginBottom: '8px',
                minHeight: '20px',
                textAlign: 'center'
            });
            statusDiv.textContent = ''; // 初始为空
            popup.appendChild(statusDiv);
            
            const agentToggles = [
                { id: 'agent-master', label: window.t ? window.t('settings.toggles.agentMaster') : 'Agent总开关', labelKey: 'settings.toggles.agentMaster' },
                { id: 'agent-keyboard', label: window.t ? window.t('settings.toggles.keyboardControl') : '键鼠控制', labelKey: 'settings.toggles.keyboardControl' },
                { id: 'agent-mcp', label: window.t ? window.t('settings.toggles.mcpTools') : 'MCP工具', labelKey: 'settings.toggles.mcpTools' }, 
                { id: 'agent-user-plugin', label: window.t ? window.t('settings.toggles.userPlugin') : '用户插件', labelKey: 'settings.toggles.userPlugin' }
            ];
            
            agentToggles.forEach(toggle => {
                const toggleItem = document.createElement('div');
                Object.assign(toggleItem.style, {
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '6px 8px',
                    cursor: 'pointer',
                    borderRadius: '6px',
                    transition: 'background 0.2s ease',
                    fontSize: '13px',
                    whiteSpace: 'nowrap'
                });
                
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.id = `live2d-${toggle.id}`;
                // 隐藏原生 checkbox
                Object.assign(checkbox.style, {
                    display: 'none'
                });
                
                // 创建自定义圆形指示器
                const indicator = document.createElement('div');
                Object.assign(indicator.style, {
                    width: '20px',
                    height: '20px',
                    borderRadius: '50%',
                    border: '2px solid #ccc',
                    backgroundColor: 'transparent',
                    cursor: 'pointer',
                    flexShrink: '0',
                    transition: 'all 0.2s ease',
                    position: 'relative',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                });
                
                // 创建对勾图标（初始隐藏）
                const checkmark = document.createElement('div');
                checkmark.innerHTML = '✓';
                Object.assign(checkmark.style, {
                    color: '#fff',
                    fontSize: '13px',
                    fontWeight: 'bold',
                    lineHeight: '1',
                    opacity: '0',
                    transition: 'opacity 0.2s ease',
                    pointerEvents: 'none',
                    userSelect: 'none'
                });
                indicator.appendChild(checkmark);
                
                const label = document.createElement('label');
                label.innerText = toggle.label;
                if (toggle.labelKey) {
                    label.setAttribute('data-i18n', toggle.labelKey);
                }
                label.htmlFor = `live2d-${toggle.id}`;
                label.style.cursor = 'pointer';
                label.style.userSelect = 'none';
                label.style.fontSize = '13px';
                label.style.color = '#333';  // 文本始终为深灰色，不随选中状态改变
                
                // 更新标签文本的函数
                const updateLabelText = () => {
                    if (toggle.labelKey && window.t) {
                        label.innerText = window.t(toggle.labelKey);
                    }
                };
                
                // 同步 title 属性
                const updateTitle = () => {
                    const title = checkbox.title || '';
                    label.title = toggleItem.title = title;
                };
                
                // 根据 checkbox 状态更新指示器颜色和对勾显示
                const updateStyle = () => {
                    if (checkbox.checked) {
                        // 选中状态：蓝色填充，显示对勾
                        indicator.style.backgroundColor = '#44b7fe';
                        indicator.style.borderColor = '#44b7fe';
                        checkmark.style.opacity = '1';
                    } else {
                        // 未选中状态：灰色边框，透明填充，隐藏对勾
                        indicator.style.backgroundColor = 'transparent';
                        indicator.style.borderColor = '#ccc';
                        checkmark.style.opacity = '0';
                    }
                };
                
                // 更新禁用状态的视觉反馈
                const updateDisabledStyle = () => {
                    const disabled = checkbox.disabled;
                    const cursor = disabled ? 'default' : 'pointer';
                    [toggleItem, label, indicator].forEach(el => el.style.cursor = cursor);
                    toggleItem.style.opacity = disabled ? '0.5' : '1';
                };
                
                // 监听 checkbox 的 disabled 和 title 属性变化
                const disabledObserver = new MutationObserver(() => {
                    updateDisabledStyle();
                    if (checkbox.hasAttribute('title')) updateTitle();
                });
                disabledObserver.observe(checkbox, { attributes: true, attributeFilter: ['disabled', 'title'] });
                
                // 监听 checkbox 状态变化
                checkbox.addEventListener('change', updateStyle);
                
                // 初始化样式
                updateStyle();
                updateDisabledStyle();
                updateTitle();
                
                toggleItem.appendChild(checkbox);
                toggleItem.appendChild(indicator);
                toggleItem.appendChild(label);
                popup.appendChild(toggleItem);
                
                // 存储更新函数
                if (toggle.labelKey) {
                    toggleItem._updateLabelText = updateLabelText;
                }
                
                // 鼠标悬停效果
                toggleItem.addEventListener('mouseenter', () => {
                    if (checkbox.disabled && checkbox.title?.includes('不可用')) {
                        const statusEl = document.getElementById('live2d-agent-status');
                        if (statusEl) statusEl.textContent = checkbox.title;
                    } else if (!checkbox.disabled) {
                        toggleItem.style.background = 'rgba(79, 140, 255, 0.1)';
                    }
                });
                toggleItem.addEventListener('mouseleave', () => {
                    toggleItem.style.background = 'transparent';
                });
                
                // 点击切换（点击整个项目都可以切换）
                toggleItem.addEventListener('click', (e) => {
                    if (checkbox.disabled) return;
                    checkbox.checked = !checkbox.checked;
                    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                    updateStyle();
                });
            });
        } else if (buttonId === 'settings') {
            // 设置菜单
            
            // 先添加 Focus 模式和主动搭话开关（在最上面）
            const settingsToggles = [
                { id: 'focus-mode', label: window.t ? window.t('settings.toggles.allowInterrupt') : '允许打断', labelKey: 'settings.toggles.allowInterrupt', storageKey: 'focusModeEnabled', inverted: true }, // inverted表示值与focusModeEnabled相反
                { id: 'proactive-chat', label: window.t ? window.t('settings.toggles.proactiveChat') : '主动搭话', labelKey: 'settings.toggles.proactiveChat', storageKey: 'proactiveChatEnabled' }
            ];
            
            settingsToggles.forEach(toggle => {
                const toggleItem = document.createElement('div');
                Object.assign(toggleItem.style, {
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 12px',  // 统一padding，与下方菜单项一致
                    cursor: 'pointer',
                    borderRadius: '6px',
                    transition: 'background 0.2s ease',
                    fontSize: '13px',
                    whiteSpace: 'nowrap',
                    borderBottom: '1px solid rgba(0,0,0,0.05)'
                });
                
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.id = `live2d-${toggle.id}`;
                // 隐藏原生 checkbox
                Object.assign(checkbox.style, {
                    display: 'none'
                });
                
                // 从 window 获取当前状态（如果 app.js 已经初始化）
                if (toggle.id === 'focus-mode' && typeof window.focusModeEnabled !== 'undefined') {
                    // inverted: 允许打断 = !focusModeEnabled（focusModeEnabled为true表示关闭打断）
                    checkbox.checked = toggle.inverted ? !window.focusModeEnabled : window.focusModeEnabled;
                } else if (toggle.id === 'proactive-chat' && typeof window.proactiveChatEnabled !== 'undefined') {
                    checkbox.checked = window.proactiveChatEnabled;
                }
                
                // 创建自定义圆形指示器
                const indicator = document.createElement('div');
                Object.assign(indicator.style, {
                    width: '20px',  // 稍微增大，与下方图标更协调
                    height: '20px',
                    borderRadius: '50%',
                    border: '2px solid #ccc',
                    backgroundColor: 'transparent',
                    cursor: 'pointer',
                    flexShrink: '0',
                    transition: 'all 0.2s ease',
                    position: 'relative',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                });
                
                // 创建对勾图标（初始隐藏）
                const checkmark = document.createElement('div');
                checkmark.innerHTML = '✓';
                Object.assign(checkmark.style, {
                    color: '#fff',
                    fontSize: '13px',  // 稍微增大，与指示器大小更协调
                    fontWeight: 'bold',
                    lineHeight: '1',
                    opacity: '0',
                    transition: 'opacity 0.2s ease',
                    pointerEvents: 'none',
                    userSelect: 'none'
                });
                indicator.appendChild(checkmark);
                
                const label = document.createElement('label');
                label.innerText = toggle.label;
                label.htmlFor = `live2d-${toggle.id}`;
                // 添加 data-i18n 属性以便自动更新
                if (toggle.labelKey) {
                    label.setAttribute('data-i18n', toggle.labelKey);
                }
                label.style.cursor = 'pointer';
                label.style.userSelect = 'none';
                label.style.fontSize = '13px';
                label.style.color = '#333';  // 文本始终为深灰色，不随选中状态改变
                label.style.display = 'flex';
                label.style.alignItems = 'center';
                label.style.lineHeight = '1';
                label.style.height = '20px';  // 与指示器高度一致，确保垂直居中
                
                // 根据 checkbox 状态更新指示器颜色（文本颜色保持不变）
                const updateStyle = () => {
                    if (checkbox.checked) {
                        // 选中状态：蓝色填充，蓝色边框，显示对勾，背景颜色突出
                        indicator.style.backgroundColor = '#44b7fe';
                        indicator.style.borderColor = '#44b7fe';
                        checkmark.style.opacity = '1';
                        toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';  // 浅蓝色背景
                    } else {
                        // 未选中状态：灰色边框，透明填充，隐藏对勾，无背景
                        indicator.style.backgroundColor = 'transparent';
                        indicator.style.borderColor = '#ccc';
                        checkmark.style.opacity = '0';
                        toggleItem.style.background = 'transparent';
                    }
                };
                
                // 初始化样式（根据当前状态）
                updateStyle();
                
                toggleItem.appendChild(checkbox);
                toggleItem.appendChild(indicator);
                toggleItem.appendChild(label);
                popup.appendChild(toggleItem);
                
                toggleItem.addEventListener('mouseenter', () => {
                    // 如果已选中，使用更深的背景色；如果未选中，使用浅色背景
                    if (checkbox.checked) {
                        toggleItem.style.background = 'rgba(68, 183, 254, 0.15)';
                    } else {
                        toggleItem.style.background = 'rgba(79, 140, 255, 0.1)';
                    }
                });
                toggleItem.addEventListener('mouseleave', () => {
                    // 恢复选中状态的背景色
                    updateStyle();
                });
                
                // 点击切换（直接更新全局状态并保存）
                checkbox.addEventListener('change', (e) => {
                    e.stopPropagation();
                    const isChecked = checkbox.checked;
                    
                    // 更新样式
                    updateStyle();
                    
                    // 同步到 app.js 中的对应开关（这样会触发 app.js 的完整逻辑）
                    if (toggle.id === 'focus-mode') {
                        // inverted: "允许打断"的值需要取反后赋给 focusModeEnabled
                        // 勾选"允许打断" = focusModeEnabled为false（允许打断）
                        // 取消勾选"允许打断" = focusModeEnabled为true（focus模式，AI说话时静音麦克风）
                        const actualValue = toggle.inverted ? !isChecked : isChecked;
                        window.focusModeEnabled = actualValue;
                        
                        // 保存到localStorage
                        if (typeof window.saveXiao8Settings === 'function') {
                            window.saveXiao8Settings();
                        }
                    } else if (toggle.id === 'proactive-chat') {
                        window.proactiveChatEnabled = isChecked;
                        
                        // 保存到localStorage
                        if (typeof window.saveXiao8Settings === 'function') {
                            window.saveXiao8Settings();
                        }
                        
                        if (isChecked && typeof window.resetProactiveChatBackoff === 'function') {
                            window.resetProactiveChatBackoff();
                        } else if (!isChecked && typeof window.stopProactiveChatSchedule === 'function') {
                            window.stopProactiveChatSchedule();
                        }
                        console.log(`主动搭话已${isChecked ? '开启' : '关闭'}`);
                    }
                });
                
                // 点击整行也能切换
                toggleItem.addEventListener('click', (e) => {
                    if (e.target !== checkbox) {
                        checkbox.checked = !checkbox.checked;
                        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                        updateStyle();  // 更新样式
                    }
                });
                
                // 点击指示器也可以切换
                indicator.addEventListener('click', (e) => {
                    e.stopPropagation();
                    checkbox.checked = !checkbox.checked;
                    checkbox.dispatchEvent(new Event('change', { bubbles: true }));
                    updateStyle();
                });
            });
            
			// 手机仅保留两个开关；桌面端追加导航菜单
			if (!isMobileWidth()) {
				// 添加分隔线
				const separator = document.createElement('div');
				Object.assign(separator.style, {
					height: '1px',
					background: 'rgba(0,0,0,0.1)',
					margin: '4px 0'
				});
				popup.appendChild(separator);
				
				// 然后添加导航菜单项
				const settingsItems = [
					{ id: 'live2d-manage', label: window.t ? window.t('settings.menu.live2dSettings') : 'Live2D设置', labelKey: 'settings.menu.live2dSettings', icon: '/static/icons/live2d_settings_icon.png', action: 'navigate', urlBase: '/l2d' },
					{ id: 'api-keys', label: window.t ? window.t('settings.menu.apiKeys') : 'API密钥', labelKey: 'settings.menu.apiKeys', icon: '/static/icons/api_key_icon.png', action: 'navigate', url: '/api_key' },
					{ id: 'character', label: window.t ? window.t('settings.menu.characterManage') : '角色管理', labelKey: 'settings.menu.characterManage', icon: '/static/icons/character_icon.png', action: 'navigate', url: '/chara_manager' },
					{ id: 'voice-clone', label: window.t ? window.t('settings.menu.voiceClone') : '声音克隆', labelKey: 'settings.menu.voiceClone', icon: '/static/icons/voice_clone_icon.png', action: 'navigate', url: '/voice_clone' },
					{ id: 'memory', label: window.t ? window.t('settings.menu.memoryBrowser') : '记忆浏览', labelKey: 'settings.menu.memoryBrowser', icon: '/static/icons/memory_icon.png', action: 'navigate', url: '/memory_browser' },
                { id: 'steam-workshop', label: window.t ? window.t('steam.workshop') : '创意工坊', labelKey: 'steam.workshop', icon: '/static/icons/Steam_icon_logo.png', action: 'navigate', url: '/steam_workshop_manager' },
				];
				
				settingsItems.forEach(item => {
					const menuItem = document.createElement('div');
					Object.assign(menuItem.style, {
						display: 'flex',
						alignItems: 'center',
						gap: '8px',
						padding: '8px 12px',
						cursor: 'pointer',
						borderRadius: '6px',
						transition: 'background 0.2s ease',
						fontSize: '13px',
						whiteSpace: 'nowrap',
						color: '#333'  // 文本颜色为深灰色
					});
					
					// 添加图标（如果有）
					if (item.icon) {
						const iconImg = document.createElement('img');
						iconImg.src = item.icon;
						iconImg.alt = item.label;
						Object.assign(iconImg.style, {
							width: '24px',
							height: '24px',
							objectFit: 'contain',
							flexShrink: '0'
						});
						menuItem.appendChild(iconImg);
					}
					
					// 添加文本
					const labelText = document.createElement('span');
					labelText.textContent = item.label;
					if (item.labelKey) {
						labelText.setAttribute('data-i18n', item.labelKey);
					}
					Object.assign(labelText.style, {
						display: 'flex',
						alignItems: 'center',
						lineHeight: '1',
						height: '24px'  // 与图标高度一致，确保垂直居中
					});
					menuItem.appendChild(labelText);
					
					// 存储更新函数
					if (item.labelKey) {
						const updateLabelText = () => {
							if (window.t) {
								labelText.textContent = window.t(item.labelKey);
								// 同时更新图标 alt 属性
								if (item.icon && menuItem.querySelector('img')) {
									menuItem.querySelector('img').alt = window.t(item.labelKey);
								}
							}
						};
						menuItem._updateLabelText = updateLabelText;
					}
					
					menuItem.addEventListener('mouseenter', () => {
						menuItem.style.background = 'rgba(79, 140, 255, 0.1)';
					});
					menuItem.addEventListener('mouseleave', () => {
						menuItem.style.background = 'transparent';
					});
					
					menuItem.addEventListener('click', (e) => {
						e.stopPropagation();
						if (item.action === 'navigate') {
							// 动态构建 URL（点击时才获取 lanlan_name）
							let finalUrl = item.url || item.urlBase;
							if (item.id === 'live2d-manage' && item.urlBase) {
								// 从 window.lanlan_config 动态获取 lanlan_name
								const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
								finalUrl = `${item.urlBase}?lanlan_name=${encodeURIComponent(lanlanName)}`;
								// Live2D设置页直接跳转
								window.location.href = finalUrl;
							} else if (item.id === 'voice-clone' && item.url) {
								// 声音克隆页面也需要传递 lanlan_name
								const lanlanName = (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
								finalUrl = `${item.url}?lanlan_name=${encodeURIComponent(lanlanName)}`;
								
								// 检查是否已有该URL的窗口打开
								if (this._openSettingsWindows[finalUrl]) {
									const existingWindow = this._openSettingsWindows[finalUrl];
									if (existingWindow && !existingWindow.closed) {
										existingWindow.focus();
										return;
									} else {
										delete this._openSettingsWindows[finalUrl];
									}
								}
								
								// 打开新的弹窗前关闭其他已打开的设置窗口，实现全局互斥
								this.closeAllSettingsWindows();
								
								// 打开新窗口并保存引用
								const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
								if (newWindow) {
									this._openSettingsWindows[finalUrl] = newWindow;
								}
							} else {
								// 其他页面弹出新窗口，但检查是否已打开
								// 检查是否已有该URL的窗口打开
								if (this._openSettingsWindows[finalUrl]) {
									const existingWindow = this._openSettingsWindows[finalUrl];
									// 检查窗口是否仍然打开
									if (existingWindow && !existingWindow.closed) {
										// 聚焦到已存在的窗口
										existingWindow.focus();
										return;
									} else {
										// 窗口已关闭，清除引用
										delete this._openSettingsWindows[finalUrl];
									}
								}
								
								// 打开新的弹窗前关闭其他已打开的设置窗口，实现全局互斥
								this.closeAllSettingsWindows();
								
								// 打开新窗口并保存引用
								const newWindow = window.open(finalUrl, '_blank', 'width=1000,height=800,menubar=no,toolbar=no,location=no,status=no');
								if (newWindow) {
									this._openSettingsWindows[finalUrl] = newWindow;
									
									// 监听窗口关闭事件，清除引用
									const checkClosed = setInterval(() => {
										if (newWindow.closed) {
											delete this._openSettingsWindows[finalUrl];
											clearInterval(checkClosed);
										}
									}, 500);
								}
							}
						}
					});
					
					popup.appendChild(menuItem);
				});
			}
        }

        return popup;
    }

    // 关闭指定按钮对应的弹出框，并恢复按钮状态
    closePopupById(buttonId) {
        if (!buttonId) return false;
        const popup = document.getElementById(`live2d-popup-${buttonId}`);
        if (!popup || popup.style.display !== 'flex') {
            return false;
        }

        popup.style.opacity = '0';
        popup.style.transform = 'translateX(-10px)';
        setTimeout(() => {
            popup.style.display = 'none';
        }, 200);

        const buttonEntry = this._floatingButtons[buttonId];
        if (buttonEntry && buttonEntry.button) {
            buttonEntry.button.dataset.active = 'false';
            buttonEntry.button.style.background = 'rgba(255, 255, 255, 0.7)';

            if (buttonEntry.imgOff && buttonEntry.imgOn) {
                buttonEntry.imgOff.style.opacity = '1';
                buttonEntry.imgOn.style.opacity = '0';
            }
        }

        if (this._popupTimers[buttonId]) {
            clearTimeout(this._popupTimers[buttonId]);
            this._popupTimers[buttonId] = null;
        }

        return true;
    }

    // 关闭除当前按钮之外的所有弹出框
    closeAllPopupsExcept(currentButtonId) {
        const popups = document.querySelectorAll('[id^="live2d-popup-"]');
        popups.forEach(popup => {
            const popupId = popup.id.replace('live2d-popup-', '');
            if (popupId !== currentButtonId && popup.style.display === 'flex') {
                this.closePopupById(popupId);
            }
        });
    }

    // 关闭所有通过 window.open 打开的设置窗口，可选保留特定 URL
    closeAllSettingsWindows(exceptUrl = null) {
        if (!this._openSettingsWindows) return;
        Object.keys(this._openSettingsWindows).forEach(url => {
            if (exceptUrl && url === exceptUrl) return;
            const winRef = this._openSettingsWindows[url];
            try {
                if (winRef && !winRef.closed) {
                    winRef.close();
                }
            } catch (_) {
                // 忽略跨域导致的 close 异常
            }
            delete this._openSettingsWindows[url];
        });
    }

    // 显示弹出框（1秒后自动隐藏），支持点击切换
    showPopup(buttonId, popup) {
        // 检查当前状态
        const isVisible = popup.style.display === 'flex' && popup.style.opacity === '1';
        
        // 清除之前的定时器
        if (this._popupTimers[buttonId]) {
            clearTimeout(this._popupTimers[buttonId]);
            this._popupTimers[buttonId] = null;
        }
        
        // 如果是设置弹出框，每次显示时更新开关状态（确保与 app.js 同步）
        if (buttonId === 'settings') {
            const focusCheckbox = popup.querySelector('#live2d-focus-mode');
            const proactiveChatCheckbox = popup.querySelector('#live2d-proactive-chat');
            
            // 辅助函数：更新 checkbox 的视觉样式
            const updateCheckboxStyle = (checkbox) => {
                if (!checkbox) return;
                // toggleItem 是 checkbox 的父元素
                const toggleItem = checkbox.parentElement;
                if (!toggleItem) return;
                
                // indicator 是 toggleItem 的第二个子元素（第一个是 checkbox，第二个是 indicator）
                const indicator = toggleItem.children[1];
                if (!indicator) return;
                
                // checkmark 是 indicator 的第一个子元素
                const checkmark = indicator.firstElementChild;
                
                if (checkbox.checked) {
                    // 选中状态：蓝色填充，蓝色边框，显示对勾，背景颜色突出
                    indicator.style.backgroundColor = '#44b7fe';
                    indicator.style.borderColor = '#44b7fe';
                    if (checkmark) checkmark.style.opacity = '1';
                    toggleItem.style.background = 'rgba(68, 183, 254, 0.1)';
                } else {
                    // 未选中状态：灰色边框，透明填充，隐藏对勾，无背景
                    indicator.style.backgroundColor = 'transparent';
                    indicator.style.borderColor = '#ccc';
                    if (checkmark) checkmark.style.opacity = '0';
                    toggleItem.style.background = 'transparent';
                }
            };
            
            // 更新 focus mode checkbox 状态和视觉样式
            if (focusCheckbox && typeof window.focusModeEnabled !== 'undefined') {
                // "允许打断"按钮值与 focusModeEnabled 相反
                const newChecked = !window.focusModeEnabled;
                // 只在状态改变时更新，避免不必要的 DOM 操作
                if (focusCheckbox.checked !== newChecked) {
                    focusCheckbox.checked = newChecked;
                    // 使用 requestAnimationFrame 确保 DOM 已更新后再更新样式
                    requestAnimationFrame(() => {
                        updateCheckboxStyle(focusCheckbox);
                    });
                } else {
                    // 即使状态相同，也确保视觉样式正确（处理概率性问题）
                    requestAnimationFrame(() => {
                        updateCheckboxStyle(focusCheckbox);
                    });
                }
            }
            
            // 更新 proactive chat checkbox 状态和视觉样式
            if (proactiveChatCheckbox && typeof window.proactiveChatEnabled !== 'undefined') {
                const newChecked = window.proactiveChatEnabled;
                // 只在状态改变时更新，避免不必要的 DOM 操作
                if (proactiveChatCheckbox.checked !== newChecked) {
                    proactiveChatCheckbox.checked = newChecked;
                    requestAnimationFrame(() => {
                        updateCheckboxStyle(proactiveChatCheckbox);
                    });
                } else {
                    // 即使状态相同，也确保视觉样式正确（处理概率性问题）
                    requestAnimationFrame(() => {
                        updateCheckboxStyle(proactiveChatCheckbox);
                    });
                }
            }
        }
        
        if (isVisible) {
            // 如果已经显示，则隐藏
            popup.style.opacity = '0';
            popup.style.transform = 'translateX(-10px)';
            setTimeout(() => {
                popup.style.display = 'none';
                // 重置位置和样式
                popup.style.left = '100%';
                popup.style.right = 'auto';
                popup.style.top = '0';
                popup.style.marginLeft = '8px';
                popup.style.marginRight = '0';
                // 重置高度限制，确保下次打开时状态一致
                if (buttonId === 'settings' || buttonId === 'agent') {
                    popup.style.maxHeight = '200px';
                    popup.style.overflowY = 'auto';
                }
            }, 200);
        } else {
            // 全局互斥：打开前关闭其他弹出框
            this.closeAllPopupsExcept(buttonId);

            // 如果隐藏，则显示
            popup.style.display = 'flex';
            // 先让弹出框可见但透明，以便计算尺寸
            popup.style.opacity = '0';
            popup.style.visibility = 'visible';
            
            // 关键：在计算位置之前，先移除高度限制，确保获取真实尺寸
            if (buttonId === 'settings' || buttonId === 'agent') {
                popup.style.maxHeight = 'none';
                popup.style.overflowY = 'visible';
            }
            
            // 等待popup内的所有图片加载完成，确保尺寸准确
            const images = popup.querySelectorAll('img');
            const imageLoadPromises = Array.from(images).map(img => {
                if (img.complete) {
                    return Promise.resolve();
                }
                return new Promise(resolve => {
                    img.onload = resolve;
                    img.onerror = resolve; // 即使加载失败也继续
                    // 超时保护：最多等待100ms
                    setTimeout(resolve, 100);
                });
            });
            
            Promise.all(imageLoadPromises).then(() => {
                // 强制触发reflow，确保布局完全更新
                void popup.offsetHeight;
                
                // 再次使用RAF确保布局稳定
                requestAnimationFrame(() => {
                    const popupRect = popup.getBoundingClientRect();
                    const screenWidth = window.innerWidth;
                    const screenHeight = window.innerHeight;
                    const rightMargin = 20; // 距离屏幕右侧的安全边距
                    const bottomMargin = 60; // 距离屏幕底部的安全边距（考虑系统任务栏，Windows任务栏约40-48px）
                    
                    // 检查是否超出屏幕右侧
                    const popupRight = popupRect.right;
                    if (popupRight > screenWidth - rightMargin) {
                        // 超出右边界，改为向左弹出
                        // 获取按钮的实际宽度来计算正确的偏移
                        const button = document.getElementById(`live2d-btn-${buttonId}`);
                        const buttonWidth = button ? button.offsetWidth : 48;
                        const gap = 8;
                        
                        // 让弹出框完全移到按钮左侧，不遮挡按钮
                        popup.style.left = 'auto';
                        popup.style.right = '0';
                        popup.style.marginLeft = '0';
                        popup.style.marginRight = `${buttonWidth + gap}px`;
                        popup.style.transform = 'translateX(10px)'; // 反向动画
                    }
                    
                    // 检查是否超出屏幕底部（设置弹出框或其他较高的弹出框）
                    if (buttonId === 'settings' || buttonId === 'agent') {
                        const popupBottom = popupRect.bottom;
                        if (popupBottom > screenHeight - bottomMargin) {
                            // 计算需要向上移动的距离
                            const overflow = popupBottom - (screenHeight - bottomMargin);
                            const currentTop = parseInt(popup.style.top) || 0;
                            const newTop = currentTop - overflow;
                            popup.style.top = `${newTop}px`;
                        }
                    }
                    
                    // 显示弹出框
                    popup.style.visibility = 'visible';
                    popup.style.opacity = '1';
                    popup.style.transform = 'translateX(0)';
                });
            });
            
            // 设置、agent、麦克风弹出框不自动隐藏，其他的1秒后隐藏
            if (buttonId !== 'settings' && buttonId !== 'agent' && buttonId !== 'mic') {
                this._popupTimers[buttonId] = setTimeout(() => {
                    popup.style.opacity = '0';
                    popup.style.transform = popup.style.right === '100%' ? 'translateX(10px)' : 'translateX(-10px)';
                    setTimeout(() => {
                        popup.style.display = 'none';
                        // 重置位置
                        popup.style.left = '100%';
                        popup.style.right = 'auto';
                        popup.style.top = '0';
                    }, 200);
                    this._popupTimers[buttonId] = null;
                }, 1000);
            }
        }
    }

    // 启用鼠标跟踪以检测与模型的接近度
    enableMouseTracking(model, options = {}) {
        const { threshold = 70 } = options;
        let hideButtonsTimer = null;

        this.pixi_app.stage.on('pointermove', (event) => {
            const lockIcon = document.getElementById('live2d-lock-icon');
            const floatingButtons = document.getElementById('live2d-floating-buttons');
            const pointer = event.data.global;
            
            // 在拖拽期间不执行任何操作
            if (model.interactive && model.dragging) {
                this.isFocusing = false;
                if (lockIcon) lockIcon.style.display = 'none';
                if (floatingButtons) floatingButtons.style.display = 'none';
                return;
            }
            
            // 如果已经点击了"请她离开"，不显示锁按钮，但保持显示"请她回来"按钮
            if (this._goodbyeClicked) {
                if (lockIcon) {
                    lockIcon.style.setProperty('display', 'none', 'important');
                }
                // 保持浮动按钮容器显示，但只显示"请她回来"按钮
                if (floatingButtons) {
                    floatingButtons.style.display = 'flex';
                    // 隐藏所有其他按钮，只显示"请她回来"按钮
                    Object.keys(this._floatingButtons).forEach(btnId => {
                        if (btnId !== 'return') {
                            const btn = this._floatingButtons[btnId].button;
                            if (btn) btn.style.display = 'none';
                        }
                    });
                    // 确保"请她回来"按钮显示
                    const returnBtn = this._floatingButtons['return'];
                    if (returnBtn && returnBtn.button) {
                        returnBtn.button.style.display = 'flex';
                    }
                }
                return;
            }

            const bounds = model.getBounds();
            const dx = Math.max(bounds.left - pointer.x, 0, pointer.x - bounds.right);
            const dy = Math.max(bounds.top - pointer.y, 0, pointer.y - bounds.bottom);
            const distance = Math.sqrt(dx * dx + dy * dy);

            if (distance < threshold) {
                this.isFocusing = true;
                if (lockIcon) lockIcon.style.display = 'block';
                // 只有在未点击"请她离开"时才显示浮动按钮
                if (floatingButtons && !this._goodbyeClicked) {
                    floatingButtons.style.display = 'flex';
                }
                
                // 清除之前的隐藏定时器
                if (hideButtonsTimer) {
                    clearTimeout(hideButtonsTimer);
                    hideButtonsTimer = null;
                }
            } else {
                this.isFocusing = false;
                if (lockIcon) lockIcon.style.display = 'none';
                
                // 鼠标离开后，1秒后自动隐藏浮动按钮
                if (floatingButtons && !this._goodbyeClicked && !hideButtonsTimer) {
                    hideButtonsTimer = setTimeout(() => {
                        if (floatingButtons && !this._goodbyeClicked) {
                            floatingButtons.style.display = 'none';
                        }
                        hideButtonsTimer = null;
                    }, 1000);
                }
            }

            if (this.isFocusing) {
                model.focus(pointer.x, pointer.y);
            }
        });
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
}

// 同步服务器端的情绪映射（可仅替换“常驻”表情组）
Live2DManager.prototype.syncEmotionMappingWithServer = async function(options = {}) {
    const { replacePersistentOnly = true } = options;
    try {
        if (!this.modelName) return;
        const resp = await fetch(`/api/live2d/emotion_mapping/${encodeURIComponent(this.modelName)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data || !data.success || !data.config) return;

        const serverMapping = data.config || { motions: {}, expressions: {} };
        if (!this.emotionMapping) this.emotionMapping = { motions: {}, expressions: {} };
        if (!this.emotionMapping.expressions) this.emotionMapping.expressions = {};

        if (replacePersistentOnly) {
            if (serverMapping.expressions && Array.isArray(serverMapping.expressions['常驻'])) {
                this.emotionMapping.expressions['常驻'] = [...serverMapping.expressions['常驻']];
            }
        } else {
            this.emotionMapping = serverMapping;
        }
    } catch (_) {
        // 静默失败，保持现有映射
    }
};

// ========== 常驻表情：实现 ==========
Live2DManager.prototype.collectPersistentExpressionFiles = function() {
    // 1) EmotionMapping.expressions.常驻
    const filesFromMapping = (this.emotionMapping && this.emotionMapping.expressions && this.emotionMapping.expressions['常驻']) || [];

    // 2) 兼容：从 FileReferences.Expressions 里按前缀 "常驻_" 推导
    let filesFromRefs = [];
    if ((!filesFromMapping || filesFromMapping.length === 0) && this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
        filesFromRefs = this.fileReferences.Expressions
            .filter(e => (e.Name || '').startsWith('常驻_'))
            .map(e => e.File)
            .filter(Boolean);
    }

    const all = [...filesFromMapping, ...filesFromRefs];
    // 去重
    return Array.from(new Set(all));
};

Live2DManager.prototype.setupPersistentExpressions = async function() {
    try {
        this.persistentExpressionNames = [];
        this.persistentExpressionParamsByName = {};
        const files = this.collectPersistentExpressionFiles();
        if (!files || files.length === 0) {
            this.teardownPersistentExpressions();
            console.log('未配置常驻表情');
            return;
        }

        for (const file of files) {
            try {
                const url = this.resolveAssetPath(file);
                const resp = await fetch(url);
                if (!resp.ok) continue;
                const data = await resp.json();
                const params = Array.isArray(data.Parameters) ? data.Parameters : [];
                const base = String(file).split('/').pop() || '';
                const name = base.replace('.exp3.json', '');
                // 只有包含参数的表达才加入播放队列
                if (params.length > 0) {
                    this.persistentExpressionNames.push(name);
                    this.persistentExpressionParamsByName[name] = params;
                }
            } catch (e) {
                console.warn('加载常驻表情失败:', file, e);
            }
        }

        // 使用官方 expression API 依次播放一次（若支持），并记录名称
        await this.applyPersistentExpressionsNative();
        console.log('常驻表情已启用，数量:', this.persistentExpressionNames.length);
    } catch (e) {
        console.warn('设置常驻表情失败:', e);
    }
};

Live2DManager.prototype.teardownPersistentExpressions = function() {
    this.persistentExpressionNames = [];
    this.persistentExpressionParamsByName = {};
};

Live2DManager.prototype.applyPersistentExpressionsNative = async function() {
    if (!this.currentModel) return;
    if (typeof this.currentModel.expression !== 'function') return;
    for (const name of this.persistentExpressionNames || []) {
        try {
            const maybe = await this.currentModel.expression(name);
            if (!maybe && this.persistentExpressionParamsByName && Array.isArray(this.persistentExpressionParamsByName[name])) {
                // 回退：手动设置参数
                try {
                    const params = this.persistentExpressionParamsByName[name];
                    const core = this.currentModel.internalModel && this.currentModel.internalModel.coreModel;
                    if (core) {
                        for (const p of params) {
                            try { core.setParameterValueById(p.Id, p.Value); } catch (_) {}
                        }
                    }
                } catch (_) {}
            }
        } catch (e) {
            // 名称可能未注册，尝试回退到手动设置
            try {
                if (this.persistentExpressionParamsByName && Array.isArray(this.persistentExpressionParamsByName[name])) {
                    const params = this.persistentExpressionParamsByName[name];
                    const core = this.currentModel.internalModel && this.currentModel.internalModel.coreModel;
                    if (core) {
                        for (const p of params) {
                            try { core.setParameterValueById(p.Id, p.Value); } catch (_) {}
                        }
                    }
                }
            } catch (_) {}
        }
    }
};

// 创建全局 Live2D 管理器实例
window.Live2DManager = Live2DManager;
window.live2dManager = new Live2DManager();


// 兼容性：保持原有的全局变量和函数
window.LanLan1 = window.LanLan1 || {};
window.LanLan1.setEmotion = (emotion) => window.live2dManager.setEmotion(emotion);
window.LanLan1.playExpression = (emotion) => window.live2dManager.playExpression(emotion);
window.LanLan1.playMotion = (emotion) => window.live2dManager.playMotion(emotion);
window.LanLan1.clearEmotionEffects = () => window.live2dManager.clearEmotionEffects();
window.LanLan1.clearExpression = () => window.live2dManager.clearExpression();
window.LanLan1.setMouth = (value) => window.live2dManager.setMouth(value);

// 自动初始化（如果存在 cubism4Model 变量）
const targetModelPath = (typeof cubism4Model !== 'undefined' ? cubism4Model : (window.cubism4Model || ''));
if (targetModelPath) {
    (async function() {
        try {
            // 初始化 PIXI 应用
            await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
            
            // 加载用户偏好
            const preferences = await window.live2dManager.loadUserPreferences();
            
            // 根据模型路径找到对应的偏好设置
            let modelPreferences = null;
            if (preferences && preferences.length > 0) {
                modelPreferences = preferences.find(p => p && p.model_path === targetModelPath);
                if (modelPreferences) {
                    console.log('找到模型偏好设置:', modelPreferences);
                } else {
                    console.log('未找到模型偏好设置，将使用默认设置');
                }
            }
            
            // 加载模型
            await window.live2dManager.loadModel(targetModelPath, {
                preferences: modelPreferences,
                isMobile: window.innerWidth <= 768
            });

            // 设置全局引用（兼容性）
            window.LanLan1.live2dModel = window.live2dManager.getCurrentModel();
            window.LanLan1.currentModel = window.live2dManager.getCurrentModel();
            window.LanLan1.emotionMapping = window.live2dManager.getEmotionMapping();

            console.log('Live2D 管理器自动初始化完成');
        } catch (error) {
            console.error('Live2D 管理器自动初始化失败:', error);
        }
    })();
}
