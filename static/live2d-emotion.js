/**
 * Live2D Emotion - 情感/表情/动作相关功能
 * 依赖: live2d-core.js (提供 Live2DManager 类和 window.LIPSYNC_PARAMS)
 * 功能:
 * - 情感管理（如切换表情、设置情感参数）
 * - 动作管理（如切换动作、设置动作参数）
 * - 常驻表情管理（如设置和清除常驻表情）
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
    // 取消正在进行的平滑过渡和手动表情覆盖
    this._cancelSmoothReset();
    this._removeManualExpressionOverride();

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

/**
 * 平滑过渡恢复 —— 差分淡出（Differential Fade）
 *
 * 核心思想：
 *   **仅淡出表情（expression）叠加量，不干扰基础动作（idle motion）、
 *   鼠标追踪（focus）、呼吸（breathing）等持续行为。**
 *
 * 工作原理（三阶段）：
 *   Phase 0（第 1 帧 beforeModelUpdate）：
 *     - 读取所有参数值 valuesA（包含表情叠加）
 *     - 停止 expression 与手动覆盖（不停止 motion！）
 *     - 回写 valuesA，保证本帧渲染结果零跳变
 *   Phase 1（第 2 帧）：
 *     - 读取所有参数值 valuesB（不含已停止的表情，但仍含 motion/focus/breathing）
 *     - 计算 delta[i] = valuesA[i] − valuesB[i] ≈ 表情贡献量
 *     - 将 delta 全量加回，视觉上依然等同于 Phase 0
 *   Phase 2+（淡出帧）：
 *     - 每帧读取当前值后 **加性** 叠加 delta × (1 − easedProgress)
 *     - 基础动作照常演进，叠加量随时间衰减至 0
 *   完成后重新应用常驻表情。
 *
 * @param {number} duration - 淡出持续时间（毫秒），默认 800ms
 * @returns {Promise} 淡出完成后 resolve
 */
Live2DManager.prototype.smoothResetToInitialState = function(duration = 800) {
    // 钳制 duration：非法值回退到默认，范围 [0, 5000]
    if (!Number.isFinite(duration) || duration < 0) {
        duration = 800;
    }
    duration = Math.min(duration, 5000);

    return new Promise((resolve) => {
        this._cancelSmoothReset();

        if (!this.currentModel || !this.currentModel.internalModel || !this.currentModel.internalModel.coreModel) {
            this._removeManualExpressionOverride();
            try { this.clearExpression(); } catch (e) {}
            resolve();
            return;
        }

        const self = this;
        const emitter = this.currentModel.internalModel; // 捕获绑定时的 emitter 引用
        this._smoothResetEmitter = emitter;
        this._smoothResetResolve = resolve; // 存储 resolve 以便外部取消时也能结束 Promise
        let phase = 0;          // 0 = 采集含表情, 1 = 采集无表情 & 计算 delta, 2 = 淡出
        const valuesA = [];     // Phase 0 采集的全参数值（按索引）
        const deltaByIndex = {}; // { 参数索引: 差值 }
        let startTime = 0;

        const onBeforeUpdate = function() {
            if (!self.currentModel || !self.currentModel.internalModel || !self.currentModel.internalModel.coreModel) {
                self._cancelSmoothReset();
                resolve();
                return;
            }

            // 防御性检查：确保当前模型仍是绑定时的模型，避免切模后旧 delta 写入新模型
            if (self.currentModel.internalModel !== emitter) {
                self._cancelSmoothReset();
                resolve();
                return;
            }

            const cm = self.currentModel.internalModel.coreModel;
            const paramCount = cm.getParameterCount();

            // ── Phase 0：采集含表情的参数快照 ──
            if (phase === 0) {
                for (let i = 0; i < paramCount; i++) {
                    try { valuesA[i] = cm.getParameterValueByIndex(i); }
                    catch (e) { valuesA[i] = 0; }
                }

                // 停止表情源（下一帧生效），不停止 motion
                self._removeManualExpressionOverride();
                try {
                    const exprMgr = self.currentModel.internalModel.motionManager &&
                        self.currentModel.internalModel.motionManager.expressionManager;
                    if (exprMgr && typeof exprMgr.stopAllExpressions === 'function') {
                        exprMgr.stopAllExpressions();
                    }
                } catch (e) {}
                // ★ 此处不调用 stopAllMotions()，让 idle / 基础动作继续运行

                // 回写 valuesA 保证本帧渲染与上一帧视觉一致
                for (let i = 0; i < paramCount; i++) {
                    try { cm.setParameterValueByIndex(i, valuesA[i]); }
                    catch (e) {}
                }

                phase = 1;
                return;
            }

            // ── Phase 1：采集无表情的参数，计算差分 ──
            if (phase === 1) {
                for (let i = 0; i < paramCount; i++) {
                    try {
                        const b = cm.getParameterValueByIndex(i);
                        const a = valuesA[i];
                        if (a !== undefined && Math.abs(a - b) > 0.0005) {
                            deltaByIndex[i] = a - b;
                        }
                    } catch (e) {}
                }

                const deltaKeys = Object.keys(deltaByIndex);
                console.log(`[smoothReset] 差分计算完成: ${deltaKeys.length} 个参数存在表情叠加量`);

                if (deltaKeys.length === 0) {
                    // 没有活跃表情，无需淡出
                    self._cancelSmoothReset();
                    try { self.applyPersistentExpressionsNative(true); } catch (e) {}
                    resolve();
                    return;
                }

                startTime = performance.now();
                phase = 2;

                // 本帧加回全量 delta，保持视觉连续
                for (const idx of deltaKeys) {
                    const i = parseInt(idx);
                    try {
                        const cur = cm.getParameterValueByIndex(i);
                        cm.setParameterValueByIndex(i, cur + deltaByIndex[i]);
                    } catch (e) {}
                }
                return;
            }

            // ── Phase 2+：加性淡出 delta ──
            const elapsed = performance.now() - startTime;
            const progress = Math.min(elapsed / duration, 1);

            // 缓入缓出三次方
            const eased = progress < 0.5
                ? 4 * progress * progress * progress
                : 1 - Math.pow(-2 * progress + 2, 3) / 2;

            const weight = 1 - eased; // 1 → 0

            for (const idx of Object.keys(deltaByIndex)) {
                const i = parseInt(idx);
                try {
                    const cur = cm.getParameterValueByIndex(i);
                    cm.setParameterValueByIndex(i, cur + deltaByIndex[i] * weight);
                } catch (e) {}
            }

            if (progress >= 1) {
                self._cancelSmoothReset();
                // 淡出完成，重新应用常驻表情
                try { self.applyPersistentExpressionsNative(true); } catch (e) {}
                console.log('[smoothReset] 差分淡出完成');
                resolve();
            }
        };

        this._smoothResetListener = onBeforeUpdate;
        emitter.on('beforeModelUpdate', onBeforeUpdate);
        console.log(`[smoothReset] 差分淡出启动, 持续 ${duration}ms`);
    });
};

/**
 * 取消正在进行的平滑过渡恢复
 */
Live2DManager.prototype._cancelSmoothReset = function() {
    if (this._smoothResetListener) {
        const emitter = this._smoothResetEmitter || (this.currentModel && this.currentModel.internalModel);
        if (emitter) {
            emitter.off('beforeModelUpdate', this._smoothResetListener);
        }
    }
    this._smoothResetListener = null;
    this._smoothResetEmitter = null;
    // 外部取消时结束挂起的 Promise，避免调用方永久等待
    if (this._smoothResetResolve) {
        this._smoothResetResolve();
        this._smoothResetResolve = null;
    }
};

/**
 * 安装手动表情覆盖（Method 2 回退时使用，带淡入效果）
 *
 * 与旧版不同，不再在第一帧捕获静态基准值快照。而是每帧读取当前
 * 参数值并 lerp 到目标值。这样在表情生效期间，focus（鼠标追踪）、
 * breathing（呼吸）等持续修改的参数仍能正常演进，不会被冻结。
 *
 * @param {Array} params - 表情参数数组 [{Id, Value}, ...]
 * @param {number} fadeInDuration - 淡入持续时间（毫秒），默认 300ms
 */
Live2DManager.prototype._installManualExpressionOverride = function(params, fadeInDuration = 300) {
    this._removeManualExpressionOverride();

    if (!this.currentModel || !this.currentModel.internalModel || !params || params.length === 0) return;

    // 钳制 fadeInDuration：非法值回退到默认，范围 [50, 5000]
    if (!Number.isFinite(fadeInDuration) || fadeInDuration <= 0) {
        fadeInDuration = 300;
    }
    fadeInDuration = Math.max(50, Math.min(fadeInDuration, 5000));

    const self = this;
    const startTime = performance.now();
    const emitter = this.currentModel.internalModel; // 捕获绑定时的 emitter
    this._manualExpressionEmitter = emitter;

    this._manualExpressionParams = params;

    const onBeforeUpdate = function() {
        if (!self.currentModel || !self.currentModel.internalModel || !self.currentModel.internalModel.coreModel) {
            self._removeManualExpressionOverride();
            return;
        }

        // 防御性检查：确保当前模型仍是绑定时的模型，避免切模后跨模型写入
        if (self.currentModel.internalModel !== emitter) {
            self._removeManualExpressionOverride();
            return;
        }

        const coreModel = self.currentModel.internalModel.coreModel;

        const elapsed = performance.now() - startTime;
        const fadeProgress = Math.min(elapsed / fadeInDuration, 1);
        // 缓入缓出二次方
        const weight = fadeProgress < 0.5
            ? 2 * fadeProgress * fadeProgress
            : 1 - Math.pow(-2 * fadeProgress + 2, 2) / 2;

        for (const param of self._manualExpressionParams) {
            if (Array.isArray(window.LIPSYNC_PARAMS) && window.LIPSYNC_PARAMS.includes(param.Id)) continue;
            try {
                // 每帧读取当前值（含 motion/focus/breathing 的实时贡献）
                const current = coreModel.getParameterValueById(param.Id);
                // lerp(current, target, weight)：weight=0 维持当前值，weight=1 完全覆盖为目标
                const blendedVal = current + (param.Value - current) * weight;
                coreModel.setParameterValueById(param.Id, blendedVal);
            } catch (e) {}
        }
    };

    this._manualExpressionListener = onBeforeUpdate;
    emitter.on('beforeModelUpdate', onBeforeUpdate);
    console.log(`[ManualExpression] 安装手动表情覆盖，${params.length}个参数，淡入 ${fadeInDuration}ms`);
};

/**
 * 移除手动表情覆盖
 */
Live2DManager.prototype._removeManualExpressionOverride = function() {
    if (this._manualExpressionListener) {
        const emitter = this._manualExpressionEmitter || (this.currentModel && this.currentModel.internalModel);
        if (emitter) {
            emitter.off('beforeModelUpdate', this._manualExpressionListener);
        }
    }
    this._manualExpressionListener = null;
    this._manualExpressionEmitter = null;
    this._manualExpressionParams = null;
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

        // 过滤已确认失效的 expression，避免重复请求 404
        if (typeof this.isExpressionFileMissing === 'function') {
            expressionFiles = expressionFiles.filter(file => !this.isExpressionFileMissing(file));
        }

        if (!expressionFiles || expressionFiles.length === 0) {
            console.log(`情感 ${emotion} 的表情文件均已标记失效，跳过表情播放`);
            return;
        }

        choiceFile = this.getRandomElement(expressionFiles);
    }
    if (!choiceFile) return;

    // 将 basename（如 expression7.exp3.json）归一化回 FileReferences 中的真实路径（如 expressions/expression7.exp3.json）
    const resolvedRef = (typeof this.resolveExpressionReferenceByFile === 'function')
        ? this.resolveExpressionReferenceByFile(choiceFile)
        : null;
    const resolvedExpressionName = resolvedRef && resolvedRef.name ? resolvedRef.name : null;
    const canonicalChoiceFile = resolvedRef && resolvedRef.file ? resolvedRef.file : choiceFile;
    
    try {
        // 构造候选表达文件路径：优先 canonical，其次同名 FileReferences，再尝试 expressions/ 前缀
        const candidateFiles = [];
        const pushCandidate = (filePath) => {
            if (!filePath || typeof filePath !== 'string') return;
            const normalized = filePath.replace(/\\/g, '/');
            if (!candidateFiles.includes(normalized)) candidateFiles.push(normalized);
        };

        pushCandidate(canonicalChoiceFile);

        const baseName = String(canonicalChoiceFile).replace(/\\/g, '/').split('/').pop() || '';
        if (this.fileReferences && Array.isArray(this.fileReferences.Expressions) && baseName) {
            for (const expr of this.fileReferences.Expressions) {
                if (!expr || typeof expr !== 'object' || !expr.File) continue;
                const exprFile = String(expr.File).replace(/\\/g, '/');
                const exprBase = exprFile.split('/').pop() || '';
                if (exprBase === baseName) pushCandidate(exprFile);
            }
        }

        if (baseName && !baseName.includes('/')) {
            // 常见工坊结构：表达文件位于 expressions/ 子目录
            pushCandidate(`expressions/${baseName}`);
        }

        let expressionData = null;
        let loadedExpressionFile = null;
        let lastFetchError = null;

        for (const candidateFile of candidateFiles) {
            try {
                const expressionPath = this.resolveAssetPath(candidateFile);
                const response = await fetch(expressionPath);
                if (!response.ok) {
                    lastFetchError = new Error(`Failed to load expression: ${response.statusText}`);
                    continue;
                }
                expressionData = await response.json();
                loadedExpressionFile = candidateFile;
                break;
            } catch (e) {
                lastFetchError = e;
            }
        }

        if (!expressionData || !loadedExpressionFile) {
            if (typeof this.markExpressionFileMissing === 'function') {
                for (const file of candidateFiles) this.markExpressionFileMissing(file);
            }
            throw lastFetchError || new Error('Failed to load expression');
        }
        console.log(`加载表情文件: ${loadedExpressionFile}`, expressionData);
        
        // 方法1: 尝试使用原生expression API
        if (this.currentModel.expression) {
            try {
                const expressionName = resolvedExpressionName || ((typeof this.resolveExpressionNameByFile === 'function')
                    ? this.resolveExpressionNameByFile(canonicalChoiceFile)
                    : null);

                if (!expressionName) {
                    console.warn(`未找到表情名映射，将跳过原生API并回退到手动参数设置: ${loadedExpressionFile}`);
                    throw new Error('Expression name mapping not found');
                }

                // 一些工坊模型会把 Name/映射写成 *.exp3.json，底层会将其当文件路径并错误拼接，故直接回退手动参数应用
                const nameLooksLikeFile = /\.exp3\.json$/i.test(expressionName) || expressionName.includes('/');
                if (nameLooksLikeFile) {
                    console.warn(`表情名疑似文件路径，跳过原生API避免404: ${expressionName}`);
                    throw new Error('Expression name appears to be a file path');
                }
                
                console.log(`尝试使用原生API播放expression: ${expressionName} (file: ${loadedExpressionFile})`);
                
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
        
        // 方法2: 回退到手动参数设置（使用每帧应用 + 淡入效果，避免参数被 loadParameters 覆盖）
        console.log('使用手动参数设置播放expression（带淡入过渡）');
        if (expressionData.Parameters && expressionData.Parameters.length > 0) {
            // 使用 _installManualExpressionOverride 在每帧中持续应用参数，并带有淡入效果
            this._installManualExpressionOverride(expressionData.Parameters, 300);
        }
        
        console.log(`手动设置表情（带淡入过渡）: ${loadedExpressionFile}`);
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

    // 优先使用 Cubism 原生 Motion Group（FileReferences.Motions）
    // 格式: { emotion: [{ File: "motions/xxx.motion3.json" }, ...] }
    let motions = null;
    if (this.fileReferences && this.fileReferences.Motions && this.fileReferences.Motions[emotion]) {
        motions = this.fileReferences.Motions[emotion]; // 形如 [{ File: "motions/xxx.motion3.json" }, ...]
    } else if (this.emotionMapping && this.emotionMapping.motions && this.emotionMapping.motions[emotion]) {
        // 兼容 EmotionMapping.motions: { emotion: ["motions/xxx.motion3.json", ...] }
        const emotionMotions = this.emotionMapping.motions[emotion];
        if (Array.isArray(emotionMotions) && emotionMotions.length > 0) {
            // 检查是否已经是对象格式还是字符串格式
            if (typeof emotionMotions[0] === 'string') {
                motions = emotionMotions.map(f => ({ File: f }));
            } else {
                // 已经是对象格式
                motions = emotionMotions;
            }
        }
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
    if (!choice || !choice.File) {
        console.warn(`motion配置无效: ${JSON.stringify(choice)}，回退到简单动作`);
        this.playSimpleMotion(emotion);
        return;
    }

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

            // 使用模型的原生motion播放功能
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
                        console.warn('motion播放失败，返回值无效');
                    }
                } catch (error) {
                    console.warn('模型motion方法失败:', error);
                }
            }

            // 如果原生motion播放失败，回退到简单动作
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
    
    // 清除点击效果的 ID，这样点击效果的恢复定时器会检测到并跳过恢复
    // 避免点击效果的恢复覆盖正常的情感表达
    if (this._currentClickEffectId) {
        console.log('[setEmotion] 清除点击效果 ID，防止恢复定时器干扰');
        this._currentClickEffectId = null;
    }
    
    // 取消正在进行的平滑过渡，防止与新情感冲突
    this._cancelSmoothReset();
    
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
    
    // 确定要更换表情后，移除手动表情覆盖，防止与新情感冲突
    this._removeManualExpressionOverride();
    
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

