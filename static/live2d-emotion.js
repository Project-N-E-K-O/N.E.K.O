/**
 * Live2D Emotion - 情感/表情/动作相关功能
 * 依赖: live2d-core.js (提供 Live2DManager 类和 window.LIPSYNC_PARAMS)
 */

// 记录模型的初始参数（用于expression重置，跳过位置参数）
Live2DManager.prototype.recordInitialParameters = function() {
    if (!this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
        console.warn('无法记录初始参数：模型未加载');
        return;
    }

    try {
        const coreModel = this.currentModel.internalModel.coreModel;
        this.initialParameters = {};
        
        const paramCount = coreModel.getParameterCount();
        console.log(`开始记录${paramCount}个初始参数...`);
        
        // 创建可折叠的详细日志组（默认折叠状态）
        console.groupCollapsed(`参数记录详情 (${paramCount}个参数)`);
        
        // 需要跳过的位置相关参数
        const skipParams = ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamMouthOpenY', 'ParamO'];
        
        // 使用与clearEmotionEffects相同的逻辑，但改为记录值而不是重置
        for (let i = 0; i < paramCount; i++) {
            try {
                // 首先尝试使用getParameterId
                let paramId = null;
                try {
                    paramId = coreModel.getParameterId(i);
                    console.log(`使用getParameterId获取参数 ${i}: ${paramId}`);
                } catch (e1) {
                     // getParameterId方法不存在，使用备用方案（这是正常的）
                     paramId = `param_${i}`;
                     console.log(`getParameterId不可用，使用索引参数名: ${paramId}`);
                 }
                
                const currentValue = coreModel.getParameterValueByIndex(i);
                
                // 跳过位置和嘴巴相关参数
                if (skipParams.includes(paramId)) {
                    console.log(`跳过位置/嘴巴参数: ${paramId} = ${currentValue}`);
                    continue;
                }
                
                // 使用索引作为参数名的备用方案
                const paramKey = paramId || `param_${i}`;
                this.initialParameters[paramKey] = currentValue;
                console.log(`记录参数: ${paramKey} = ${currentValue}`);
            } catch (e) {
                console.warn(`记录参数 ${i} 失败:`, e);
            }
        }
        
        // 结束可折叠日志组
        console.groupEnd();
        
        console.log(`已成功记录${Object.keys(this.initialParameters).length}个初始参数 (跳过${paramCount - Object.keys(this.initialParameters).length}个位置/嘴巴参数)`);
        console.log(`记录的参数列表:`, Object.keys(this.initialParameters));
    } catch (error) {
        console.warn('记录初始参数失败:', error);
        this.initialParameters = {};
    }
};

// 清除expression到默认状态（使用保存的初始参数）
Live2DManager.prototype.clearExpression = function() {
    try {
        if (!this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
            console.warn('无法清除expression：模型未加载');
            return;
        }

        // 检查初始参数是否存在，如果不存在则视为硬错误
        if (!this.initialParameters || Object.keys(this.initialParameters).length === 0) {
            console.error('严重错误：未找到初始参数记录！expression清除失败。');
            console.error('请确保在模型加载完成后立即调用recordInitialParameters()初始化参数基准');
            return;
        }

        // 尝试使用官方API停止expression（可选，不依赖其结果）
        if (this.currentModel.internalModel.motionManager && this.currentModel.internalModel.motionManager.expressionManager) {
            try {
                this.currentModel.internalModel.motionManager.expressionManager.stopAllExpressions();
            } catch (e) {
                console.warn('停止expression失败（忽略）:', e);
            }
        }

        const coreModel = this.currentModel.internalModel.coreModel;
        console.log(`开始重置expression到初始状态，共${Object.keys(this.initialParameters).length}个参数`);
        
        // 创建可折叠的参数重置详情日志（默认折叠状态）
        console.groupCollapsed(`参数重置详情 (${Object.keys(this.initialParameters).length}个参数)`);
        
        // 重置所有记录的初始参数
        for (const [paramId, initialValue] of Object.entries(this.initialParameters)) {
            try {
                if (paramId.startsWith('param_')) {
                    // 如果是使用索引作为参数名的情况，提取索引
                    const paramIndex = parseInt(paramId.substring(6));
                    if (!isNaN(paramIndex)) {
                        coreModel.setParameterValueByIndex(paramIndex, initialValue);
                        console.log(`使用索引重置参数 ${paramId} (索引${paramIndex}) = ${initialValue}`);
                    } else {
                        console.warn(`无效的参数索引: ${paramId}`);
                    }
                } else {
                    // 正常使用参数ID重置
                    coreModel.setParameterValueById(paramId, initialValue);
                    console.log(`重置参数 ${paramId} = ${initialValue}`);
                }
            } catch (e) {
                console.warn(`重置参数 ${paramId} 失败:`, e);
            }
        }
        
        // 结束可折叠日志组
        console.groupEnd();
        
        console.log('expression已使用初始参数重置');

    } catch (error) {
        console.warn('expression重置失败:', error);
    }

    // 如存在常驻表情，清除后立即重放常驻，保证不被清掉
    // 注意：这里传入 skipBackup=true，因为我们只是重新应用已有的常驻表情，不需要再次备份
    this.applyPersistentExpressionsNative(true);
};

// 播放表情（优先使用 EmotionMapping.expressions）
Live2DManager.prototype.playExpression = async function(emotion, specifiedExpressionFile = null) {
    if (!this.currentModel || !this.emotionMapping) {
        console.warn('无法播放表情：模型或映射配置未加载');
        return;
    }

    // 如果指定了具体的表情文件，优先使用该文件
    let choiceFile = specifiedExpressionFile;
    
    if (!choiceFile) {
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

        choiceFile = this.getRandomElement(expressionFiles);
    }
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
        // 口型参数列表，手动设置时跳过以避免覆盖lipsync（使用共享常量）
        if (expressionData.Parameters) {
            for (const param of expressionData.Parameters) {
                // 跳过口型参数，避免覆盖lipsync
                if (window.LIPSYNC_PARAMS.includes(param.Id)) {
                    console.log(`跳过口型参数: ${param.Id}，避免覆盖lipsync`);
                    continue;
                }
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
    // skipBackup=true 因为只是重新应用，不需要再次备份
    try { await this.applyPersistentExpressionsNative(true); } catch (e) {}
};

// 播放动作
Live2DManager.prototype.playMotion = async function(emotion) {
    if (!this.currentModel) {
        console.warn('无法播放动作：模型未加载');
        return;
    }

    // 检查是否有对应的动作配置
    let hasMotion = false;
    if (this.emotionMapping && this.emotionMapping.motions && this.emotionMapping.motions[emotion]) {
        hasMotion = true;
    } else if (this.fileReferences && this.fileReferences.Motions && this.fileReferences.Motions[emotion]) {
        hasMotion = true;
    }

    if (!hasMotion) {
        console.warn(`未找到情感 ${emotion} 对应的动作，但将保持表情`);
        // 如果没有找到对应的motion，设置一个短定时器以确保expression能够显示
        this.motionTimer = setTimeout(() => {
            this.motionTimer = null;
        }, 500);
        return;
    }

    try {
        // 清除之前的动作定时器
        if (this.motionTimer) {
            if (this.motionTimer.type === 'animation') {
                cancelAnimationFrame(this.motionTimer.id);
            } else if (this.motionTimer.type === 'timeout') {
                clearTimeout(this.motionTimer.id);
            } else if (this.motionTimer.type === 'motion') {
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
        }

        // 播放简单动作
        this.playSimpleMotion(emotion);
    } catch (error) {
        console.error('播放动作失败:', error);
        this.playSimpleMotion(emotion);
    }
};

// 播放简单动作（回退方案）
Live2DManager.prototype.playSimpleMotion = function(emotion) {
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
};

// 清理当前情感效果（清除motion参数，但保留expression）
Live2DManager.prototype.clearEmotionEffects = function() {
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
    
    // 停止所有motion（但不重置expression参数）
    if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.motionManager) {
        try {
            // 使用官方API停止所有motion
            if (this.currentModel.internalModel.motionManager.stopAllMotions) {
                this.currentModel.internalModel.motionManager.stopAllMotions();
                console.log('已停止所有motion，保留expression参数');
                hasCleared = true;
            }
        } catch (motionError) {
            console.warn('停止motion失败:', motionError);
        }
    }
    
    // 只重置明显的motion相关参数，保留expression相关参数
    if (this.currentModel && this.currentModel.internalModel && this.currentModel.internalModel.coreModel) {
        try {
            const coreModel = this.currentModel.internalModel.coreModel;
            
            // 只重置明显的motion相关参数，避免影响expression
            const motionParams = [
                'ParamAngleX', 'ParamAngleY', 'ParamAngleZ', // 角度参数
                'ParamBodyAngleX', 'ParamBodyAngleY', 'ParamBodyAngleZ', // 身体角度
                'ParamBreath', 'ParamBreath2', 'ParamBreath3', // 呼吸参数
                'ParamLookAtX', 'ParamLookAtY', // 视线追踪
                'ParamShake' // 震动参数
            ];
            
            let resetCount = 0;
            for (const paramId of motionParams) {
                try {
                    coreModel.setParameterValueById(paramId, 0);
                    resetCount++;
                } catch (e) {
                    // 参数不存在，忽略
                }
            }
            
            console.log(`已重置${resetCount}个motion相关参数到默认值，expression参数已保留`);
        } catch (paramError) {
            console.warn('重置motion参数失败:', paramError);
        }
    }
    
    // 重新应用常驻表情（保护常驻expression不被影响）
    // skipBackup=true 因为只是重新应用，不需要再次备份
    try {
        this.applyPersistentExpressionsNative(true);
    } catch (e) {
        console.warn('重新应用常驻表情失败:', e);
    }
    
    console.log('motion效果清理完成，motion参数已重置，expression参数已保留');
};

// 设置情感并播放对应的表情和动作
Live2DManager.prototype.setEmotion = async function(emotion) {
    // 防止快速连续点击
    if (this.isEmotionChanging) {
        console.log('情感切换中，忽略新的情感请求');
        return;
    }
    
    // 获取将要使用的表情文件（用于精确比较）
    let targetExpressionFile = null;
    
    // 使用防御性模式计算expressionFiles
    let expressionFiles = (this.emotionMapping && this.emotionMapping.expressions && this.emotionMapping.expressions[emotion]) || [];
    
    // 如果为空，回退到检查FileReferences并按前缀推导
    if (expressionFiles.length === 0) {
        if (this.fileReferences && Array.isArray(this.fileReferences.Expressions)) {
            const candidates = this.fileReferences.Expressions.filter(e => (e.Name || '').startsWith(emotion));
            expressionFiles = (candidates.map(e => e.File) || []).filter(Boolean);
        } else {
            expressionFiles = [];
        }
    }
    
    // 如果有可用文件，随机选择一个
    if (expressionFiles.length > 0) {
        targetExpressionFile = this.getRandomElement(expressionFiles);
    }
    
    // 检查是否需要重置：如果情绪和表情都相同，则跳过重置
    if (this.currentEmotion === emotion && this.currentExpressionFile === targetExpressionFile) {
        // 相同情绪且相同表情，不触发重置，保留原有的50%概率随机播放动作机制
        if (Math.random() < 0.5) {
            console.log(`检测到相同情绪且相同表情: ${emotion} (${targetExpressionFile})，不触发重置，仅随机播放motion`);
            await this.playMotion(emotion);
        } else {
            console.log(`检测到相同情绪且相同表情: ${emotion} (${targetExpressionFile})，不触发重置，跳过播放`);
        }
        return;
    }
    
    // 相同情绪但不同表情，或者全新情绪，需要重置
    if (this.currentEmotion === emotion && this.currentExpressionFile !== targetExpressionFile) {
        console.log(`检测到相同情绪但不同表情: ${emotion}，表情从 ${this.currentExpressionFile} 切换到 ${targetExpressionFile}，需要重置`);
    } else {
        console.log(`新情感触发: ${emotion}，当前情感: ${this.currentEmotion}`);
    }
    
    // 设置标志，防止快速连续点击
    this.isEmotionChanging = true;
    
    try {
        console.log(`开始设置新情感: ${emotion}`);

        // 清理之前的motion效果（按照注释保留expression）
        this.clearEmotionEffects();

        this.currentEmotion = emotion;
        this.currentExpressionFile = targetExpressionFile;
        console.log(`情感已更新为: ${emotion}，表情文件: ${targetExpressionFile}`);

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

        // 播放表情（使用确定的表情文件以保持一致性）
        await this.playExpression(emotion, targetExpressionFile);

        // 播放动作
        await this.playMotion(emotion);

        console.log(`情感 ${emotion} 设置完成`);
    } catch (error) {
        console.error(`设置情感 ${emotion} 失败:`, error);
    } finally {
        // 重置标志
        this.isEmotionChanging = false;
    }
};

// 同步服务器端的情绪映射（可仅替换"常驻"表情组）
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
        // 先清除之前的常驻表情效果
        this.teardownPersistentExpressions();
        
        const files = this.collectPersistentExpressionFiles();
        if (!files || files.length === 0) {
            console.log('[setupPersistent] 未配置常驻表情');
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
        
        // 初始化当前表情文件记录（确保重置逻辑正常工作）
        this.currentExpressionFile = null;
    } catch (e) {
        console.warn('设置常驻表情失败:', e);
    }
};

Live2DManager.prototype.teardownPersistentExpressions = function() {
    // 先重置之前常驻表情应用的参数到保存的原始值
    const hasBackup = this._persistentParamsBackup && Object.keys(this._persistentParamsBackup).length > 0;
    console.log('[teardown] 开始清除常驻表情, 备份数据:', hasBackup ? Object.keys(this._persistentParamsBackup) : '无');
    
    if (this.currentModel && this.currentModel.internalModel) {
        // 先停止 expression manager，防止它继续覆盖我们的参数
        if (this.currentModel.internalModel.motionManager && 
            this.currentModel.internalModel.motionManager.expressionManager) {
            try {
                this.currentModel.internalModel.motionManager.expressionManager.stopAllExpressions();
                console.log('[teardown] 已停止所有表情');
            } catch (e) {
                console.warn('[teardown] 停止表情失败:', e);
            }
        }
        
        // 然后恢复参数
        if (this.currentModel.internalModel.coreModel && hasBackup) {
            const core = this.currentModel.internalModel.coreModel;
            for (const [paramId, originalValue] of Object.entries(this._persistentParamsBackup)) {
                try { 
                    core.setParameterValueById(paramId, originalValue); 
                    console.log(`[teardown] 恢复参数 ${paramId} = ${originalValue}`);
                } catch (e) {
                    console.warn(`[teardown] 恢复参数 ${paramId} 失败:`, e);
                }
            }
            console.log('[teardown] 已清除常驻表情参数');
        }
    }
    
    if (!hasBackup) {
        console.log('[teardown] 没有备份数据，跳过恢复');
    }
    this.persistentExpressionNames = [];
    this.persistentExpressionParamsByName = {};
    this._persistentParamsBackup = {};
};

Live2DManager.prototype.applyPersistentExpressionsNative = async function(skipBackup = false) {
    console.log('[applyPersistent] 开始应用常驻表情, skipBackup:', skipBackup);
    console.log('[applyPersistent] persistentExpressionNames:', this.persistentExpressionNames);
    
    if (!this.currentModel) {
        console.log('[applyPersistent] 退出: currentModel 不存在');
        return;
    }
    if (typeof this.currentModel.expression !== 'function') {
        console.log('[applyPersistent] 退出: expression 方法不存在');
        return;
    }
    
    const core = this.currentModel.internalModel && this.currentModel.internalModel.coreModel;
    
    // 在应用常驻表情前，备份将要修改的参数的当前值
    // skipBackup=true 时跳过备份（用于 clearExpression 后重新应用常驻表情的场景）
    if (!skipBackup && core) {
        // 初始化参数备份对象
        if (!this._persistentParamsBackup) {
            this._persistentParamsBackup = {};
        }
        
        console.log('[applyPersistent] 开始备份参数...');
        for (const name of this.persistentExpressionNames || []) {
            const params = this.persistentExpressionParamsByName[name];
            console.log(`[applyPersistent] 处理表情 ${name}, 参数数量:`, params ? params.length : 0);
            if (Array.isArray(params)) {
                for (const p of params) {
                    if (window.LIPSYNC_PARAMS && window.LIPSYNC_PARAMS.includes(p.Id)) continue;
                    // 如果还没有备份过这个参数，保存其当前值
                    if (this._persistentParamsBackup[p.Id] === undefined) {
                        try {
                            const currentValue = core.getParameterValueById(p.Id);
                            this._persistentParamsBackup[p.Id] = currentValue;
                            console.log(`[applyPersistent] 备份参数 ${p.Id} = ${currentValue}`);
                        } catch (e) {
                            console.warn(`[applyPersistent] 备份参数 ${p.Id} 失败:`, e);
                        }
                    }
                }
            }
        }
        console.log('[applyPersistent] 备份完成, 备份数据:', Object.keys(this._persistentParamsBackup));
    } else {
        console.log('[applyPersistent] 跳过备份, skipBackup:', skipBackup, 'core:', !!core);
    }
    
    for (const name of this.persistentExpressionNames || []) {
        try {
            const maybe = await this.currentModel.expression(name);
            if (!maybe && this.persistentExpressionParamsByName && Array.isArray(this.persistentExpressionParamsByName[name])) {
                // 回退：手动设置参数（跳过口型参数以避免覆盖lipsync）
                try {
                    const params = this.persistentExpressionParamsByName[name];
                    if (core) {
                        for (const p of params) {
                            if (window.LIPSYNC_PARAMS && window.LIPSYNC_PARAMS.includes(p.Id)) continue;
                            try { core.setParameterValueById(p.Id, p.Value); } catch (_) {}
                        }
                    }
                } catch (_) {}
            }
        } catch (e) {
            // 名称可能未注册，尝试回退到手动设置（跳过口型参数以避免覆盖lipsync）
            try {
                if (this.persistentExpressionParamsByName && Array.isArray(this.persistentExpressionParamsByName[name])) {
                    const params = this.persistentExpressionParamsByName[name];
                    if (core) {
                        for (const p of params) {
                            if (window.LIPSYNC_PARAMS && window.LIPSYNC_PARAMS.includes(p.Id)) continue;
                            try { core.setParameterValueById(p.Id, p.Value); } catch (_) {}
                        }
                    }
                }
            } catch (_) {}
        }
    }
};

