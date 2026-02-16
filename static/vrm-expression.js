/**
 * VRM 表情模块 - 智能映射版
 * 解决 VRM 0.x 和 1.0 表情命名不一致导致的面瘫问题
 * 支持从后端动态加载情感映射配置
 */

class VRMExpression {
    constructor(manager) {
        this.manager = manager;

        // 眨眼配置
        this.autoBlink = true;
        this.blinkTimer = 0;
        this.nextBlinkTime = 3.0;
        this.blinkState = 0; // 0:睁眼, 1:闭眼, 2:睁眼
        this.blinkWeight = 0.0;

        // 手动眨眼标志 (防止自动更新干扰)
        this.manualBlinkInProgress = null; // 存储正在手动播放的眨眼表情名称

        // 手动设置的表情标志 (防止自动更新干扰手动设置的表情)
        this.manualExpressionInProgress = null; // 存储正在手动播放的表情名称

        // 情绪配置
        this.autoChangeMood = false;
        this.moodTimer = 0;
        this.nextMoodTime = 5.0;
        this.currentMood = 'neutral';

        // 自动回到 neutral 配置
        this.autoReturnToNeutral = true; // 是否自动回到 neutral
        this.neutralReturnDelay = 3000; // 多少毫秒后回到 neutral (从5秒改为3秒，更快恢复)
        this.neutralReturnTimer = null; // 回到 neutral 的定时器

        // 情绪映射表：把一种情绪映射到多种可能的 VRM 表情名上
        // 默认值，可通过 loadMoodMap() 从后端加载覆盖
        this.moodMap = {
            'neutral': ['neutral'],
            // 开心类：兼容 VRM1.0(happy), VRM0.0(joy, fun), 其他(smile, warau)
            'happy': ['happy', 'joy', 'fun', 'smile', 'joy_01'],
            // 放松类：
            'relaxed': ['relaxed', 'joy', 'fun', 'content'],
            // 惊讶类：
            'surprised': ['surprised', 'surprise', 'shock', 'e', 'o'],
            // 悲伤类 (偶尔用一下)
            'sad': ['sad', 'sorrow', 'grief'],
            // 生气类
            'angry': ['angry', 'anger']
        };

        this.currentWeights = {};
    }

    /**
     * 从后端加载模型特定的情感映射配置
     * @param {string} modelName - 模型名称
     */
    async loadMoodMap(modelName) {
        if (!modelName) {
            console.warn('[VRM Expression] loadMoodMap: 模型名称为空');
            return;
        }

        try {
            const response = await fetch(`/api/model/vrm/emotion_mapping/${encodeURIComponent(modelName)}`);
            if (!response.ok) {
                console.warn(`[VRM Expression] 加载情感映射失败: HTTP ${response.status}`);
                return;
            }

            const data = await response.json();
            if (data.success && data.config) {
                // 规范化配置：确保每个映射值都是字符串数组
                const normalizedConfig = {};
                for (const [key, value] of Object.entries(data.config)) {
                    const normalized = this._normalizeMoodMapValue(value);
                    if (normalized) {
                        normalizedConfig[key] = normalized;
                    }
                }
                // 合并配置，保留默认值作为后备
                this.moodMap = { ...this.moodMap, ...normalizedConfig };
                console.log(`[VRM Expression] 已加载模型 ${modelName} 的情感映射配置`, this.moodMap);
            }
        } catch (error) {
            console.warn('[VRM Expression] 加载情感映射配置失败:', error);
        }
    }

    /**
     * 获取当前的情绪映射配置
     * @returns {Object} 情绪映射表
     */
    getMoodMap() {
        return { ...this.moodMap };
    }

    /**
     * 归一化情绪映射配置值为字符串数组
     * @param {*} value - 原始值（可能是字符串或数组）
     * @returns {string[]|null} 归一化后的字符串数组，或 null（表示跳过）
     */
    _normalizeMoodMapValue(value) {
        if (typeof value === 'string') {
            // 单个字符串转换为数组
            if (value.trim()) {
                return [value.trim()];
            }
        } else if (Array.isArray(value)) {
            // 过滤并保留字符串项，同时进行trim
            const strItems = value
                .filter(item => typeof item === 'string' && item.trim())
                .map(item => item.trim());
            if (strItems.length > 0) {
                return strItems;
            }
        }
        // 其他类型或空值跳过
        return null;
    }

    /**
     * 设置情绪映射配置（运行时更新）
     * @param {Object} newMoodMap - 新的情绪映射表
     */
    setMoodMap(newMoodMap) {
        if (newMoodMap && typeof newMoodMap === 'object') {
            // 归一化配置：确保每个映射值都是字符串数组
            const normalizedConfig = {};
            for (const [key, value] of Object.entries(newMoodMap)) {
                const normalized = this._normalizeMoodMapValue(value);
                if (normalized) {
                    normalizedConfig[key] = normalized;
                }
            }
            this.moodMap = { ...this.moodMap, ...normalizedConfig };
            console.log('[VRM Expression] 已更新情感映射配置', this.moodMap);
        }
    }
    /**
     * 【新增】手动设置当前情绪
     * @param {string} moodName - 情绪名称 (如 'neutral', 'happy')
     */
    setMood(moodName) {
        // 检查情绪是否存在于映射表中
        if (this.moodMap && this.moodMap[moodName]) {
            this.currentMood = moodName;
            this.moodTimer = 0; // 重置自动切换计时器，避免马上被切走

            // 清除之前的回到 neutral 定时器
            if (this.neutralReturnTimer) {
                clearTimeout(this.neutralReturnTimer);
                this.neutralReturnTimer = null;
            }

            // 立即应用表情，不等待下一帧
            this._applyMoodImmediately(moodName);

            // 如果不是 neutral 且开启了自动回到 neutral，设置定时器
            if (this.autoReturnToNeutral && moodName !== 'neutral') {
                this.neutralReturnTimer = setTimeout(() => {
                    this.currentMood = 'neutral';
                    this.manualExpressionInProgress = null; // 清除手动表情标志
                    this._applyMoodImmediately('neutral');
                    this.neutralReturnTimer = null;
                }, this.neutralReturnDelay);
            }
        } else {
            console.warn(`[VRM Expression] 未知情绪: ${moodName}，忽略切换`);
        }
    }

    /**
     * 立即应用情绪到VRM模型（不等待下一帧）
     * @param {string} moodName - 情绪名称或直接表情名
     */
    _applyMoodImmediately(moodName) {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) {
            return;
        }

        const expressionManager = this.manager.currentModel.vrm.expressionManager;
        const expressionNames = this._getExpressionNames(expressionManager);

        // 如果 moodMap 中有对应条目，使用映射；否则将 moodName 本身作为候选
        const moodCandidates = this.moodMap[moodName] || [moodName];

        // 先清除所有表情（除了口型和视线控制）
        expressionNames.forEach(exprName => {
            const lowerExprName = exprName.toLowerCase();
            // 跳过口型和视线控制
            if (!['aa', 'ih', 'ou', 'ee', 'oh', 'look'].some(keyword => lowerExprName.includes(keyword))) {
                expressionManager.setValue(exprName, 0.0);
                this.currentWeights[exprName] = 0.0;
            }
        });

        // 如果是 neutral，直接返回（已经清除所有表情）
        if (moodName === 'neutral') {
            this.manualExpressionInProgress = null;
            return;
        }

        // 查找匹配的表情并立即应用（仅精确匹配，避免短名称误匹配）
        for (const candidate of moodCandidates) {
            const matchedExpression = expressionNames.find(exprName => {
                return exprName.toLowerCase() === candidate.toLowerCase();
            });

            if (matchedExpression) {
                // 设置手动表情标志，防止 _updateWeights 干扰
                this.manualExpressionInProgress = matchedExpression;

                // 立即设置为1.0
                expressionManager.setValue(matchedExpression, 1.0);
                this.currentWeights[matchedExpression] = 1.0;
                return; // 找到匹配的表情后立即返回
            }
        }

        // 如果没有找到匹配的表情，清除标志
        this.manualExpressionInProgress = null;
        console.warn(`[VRM Expression] 未找到匹配的表情 (名称: ${moodName}, 候选: ${moodCandidates.join(', ')})`);
    }

    // ... 下面是原有的 update(delta) ...
    /**
     * 临时测试版 update：每秒切换一个表情，帮你找数字
     */
    update(delta) {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) return;
        
        const expressionManager = this.manager.currentModel.vrm.expressionManager;
        
        // 1. 更新眨眼逻辑 (计算 blinkWeight)
        this._updateBlink(delta);

        // 2. 更新情绪切换逻辑 (如果有自动切换的话)
        this._updateMoodLogic(delta);

        // 计算并应用所有权重
        this._updateWeights(delta, expressionManager);
    }
    /**
     * 【核心修复】统一获取表情名称列表
     * 自动判断 expressions 是 Map、Object 还是 Array，解决显示 0-13 的问题
     */
    _getExpressionNames(expressionManager) {
        if (!expressionManager) return [];
        
        const exprs = expressionManager.expressions;

        // 1. 如果 exprs 缺失或为 null，优先检查内部属性 _expressionMap
        if (!exprs) {
            if (expressionManager._expressionMap) {
                return Object.keys(expressionManager._expressionMap);
            }
            return [];
        }

        // 2. 如果是 Map (VRM 1.0 标准)，转为数组
        if (exprs instanceof Map) {
            return Array.from(exprs.keys());
        }

        // 3. 如果是 Array (某些加载器版本)，提取内部的 name 属性
        if (Array.isArray(exprs)) {
            return exprs.map(e => e.expressionName || e.name || e.presetName).filter(n => n);
        }

        // 4. 如果是普通 Object (VRM 0.0)，直接取键名
        // 注意：typeof null === 'object'，所以需要额外检查 null
        if (exprs && typeof exprs === 'object') {
            return Object.keys(exprs);
        }

        // 5. 最后的备用方案：检查内部属性 _expressionMap
        if (expressionManager._expressionMap) {
            return Object.keys(expressionManager._expressionMap);
        }

        return [];
    }
    /**
     * 【修改】获取模型支持的所有表情名称列表
     * 保留眨眼 (blink)，但过滤掉口型 (aa, ih) 和视线 (look)
     */
    getExpressionList() {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) {
            return [];
        }
        const manager = this.manager.currentModel.vrm.expressionManager;
        
        // 获取所有表情名
        const allExpressions = this._getExpressionNames(manager);

        // 定义需要排除的关键词
        const excludeKeywords = [
            'look',        // 视线控制通常不需要手动预览
            'aa', 'ih', 'ou', 'ee', 'oh', // 口型通常由麦克风控制
            'neutral'      // 中立状态
        ];

        // 执行过滤
        const filtered = allExpressions.filter(name => {
            const lowerName = name.toLowerCase();
            return !excludeKeywords.some(keyword => lowerName.includes(keyword));
        });

        return filtered.sort();
    }

    _updateBlink(delta) {
        if (!this.autoBlink) return;
        this.blinkTimer += delta;
        if (this.blinkState === 0) {
            if (this.blinkTimer >= this.nextBlinkTime) {
                this.blinkState = 1;
                this.blinkTimer = 0;
            }
        } else if (this.blinkState === 1) {
            this.blinkWeight += delta * 4.0; // 眨眼速度（乘数 4.0，更自然）
            if (this.blinkWeight >= 1.0) {
                this.blinkWeight = 1.0;
                this.blinkState = 2;
            }
        } else if (this.blinkState === 2) {
            this.blinkWeight -= delta * 4.0; // 睁眼速度（乘数 4.0，更自然）
            if (this.blinkWeight <= 0.0) {
                this.blinkWeight = 0.0;
                this.blinkState = 0;
                this.nextBlinkTime = Math.random() * 3.0 + 2.0;
            }
        }
    }

    _updateMoodLogic(delta) {
        if (!this.autoChangeMood) return;
        this.moodTimer += delta;
        if (this.moodTimer >= this.nextMoodTime) {
            this.pickRandomMood();
            this.moodTimer = 0;
            this.nextMoodTime = Math.random() * 5.0 + 5.0; 
        }
    }

    pickRandomMood() {
        const moods = ['neutral', 'happy', 'relaxed', 'surprised']; // 减少出现 sad 的概率
        const randomMood = moods[Math.floor(Math.random() * moods.length)];
        if (randomMood !== this.currentMood) {
            this.currentMood = randomMood;
        }
    }

    _updateWeights(delta, expressionManager) {
        // 提高响应速度
        const lerpSpeed = 15.0 * delta;

        // 1. 获取所有表情名
        const modelExpressionNames = this._getExpressionNames(expressionManager);

        // 2. 获取当前目标表情 (例如 "angry" 或 "blinkLeft")
        const targetName = this.currentMood;

        // 判断用户是否正在手动测试眨眼
        const isUserTestingBlink = targetName.toLowerCase().includes('blink');

        // 3. 遍历每一个表情进行设置
        modelExpressionNames.forEach(name => {
            let targetWeight = 0.0;
            const lowerName = name.toLowerCase();
            const targetNameLower = targetName.toLowerCase();

            // 如果是正在手动播放的单眼眨眼，跳过自动更新
            if (this.manualBlinkInProgress && name === this.manualBlinkInProgress) {
                return; // 跳过，让手动设置的值保持
            }

            // 如果是正在手动播放的表情，跳过自动更新（除非是 neutral）
            if (this.manualExpressionInProgress && name === this.manualExpressionInProgress && targetName !== 'neutral') {
                return; // 跳过，让手动设置的值保持
            }

            // 跳过口型表情，避免与口型同步模块冲突
            const lipSyncExpressions = ['aa', 'ih', 'ou', 'ee', 'oh'];
            const isLipSyncExpression = lipSyncExpressions.some(lip => lowerName.includes(lip));

            if (isLipSyncExpression) {
                // 跳过口型表情的权重设置，让 VRMAnimation 的 _updateLipSync 独立控制
                return;
            }

            // 判断是否为选中项 (最高优先级)
            // 注意：当 currentMood = 'neutral' 时，不应用任何表情，让模型保持默认状态
            if (targetName === 'neutral') {
                targetWeight = 0.0;
            } else {
                // 直接名字匹配
                let isMatch = (name === targetName || lowerName === targetNameLower);
                // 如果没有直接匹配，检查映射表 (moodMap)
                // 解决 pickRandomMood 选出 'happy' 但模型只有 'Joy' 的情况
                if (!isMatch && this.moodMap[targetName]) {
                    const candidates = this.moodMap[targetName];
                    // 检查候选词里是否有当前这个 name
                    isMatch = candidates.some(candidate => candidate.toLowerCase() === lowerName);
                }

                if (isMatch) {
                    targetWeight = 1.0;
                } else {
                    // 只有在没有匹配到目标表情时，才将权重设为 0
                    targetWeight = 0.0;
                }
            }
            
            // 处理自动眨眼 (次优先级)
            // 条件：
            // 1. 当前表情是 blink (双眼眨眼，不包括 blinkLeft/blinkRight)
            // 2. 用户没有在手动测试眨眼 (防止手动 blinkLeft 时被自动 blink 覆盖)
            // 3. 该表情不是选中的那个 (否则会在上面被设为 1.0)
            // 注意：眨眼是自然生理反应，不受情绪状态影响，neutral 时也允许眨眼
            if (lowerName === 'blink' && !isUserTestingBlink) {
                expressionManager.setValue(name, this.blinkWeight);
                return; // 眨眼由定时器控制，处理完直接跳过后续插值
            }
           
            // 如果当前权重很小(接近0) 且 目标权重也是0，说明这个表情处于静止状态，直接跳过
            // 这能节省大量 CPU 计算资源
            const currentWeight = this.currentWeights[name] || 0.0;
            if (targetWeight === 0 && currentWeight < 0.001) {
                if (currentWeight !== 0) {
                    // 确保最后一次归零
                    this.currentWeights[name] = 0.0;
                    expressionManager.setValue(name, 0.0);
                }
                return; // 跳过本次循环
            }

            // 执行插值和应用
            if (this.currentWeights[name] === undefined) this.currentWeights[name] = 0.0;
            
            const diff = targetWeight - this.currentWeights[name];
            
            // 优化：微小差距直接到位
            if (Math.abs(diff) < 0.01) {
                this.currentWeights[name] = targetWeight;
            } else {
                this.currentWeights[name] += diff * lerpSpeed;
            }

            // 最终设置
            expressionManager.setValue(name, this.currentWeights[name]);
        });
    }

    resetBaseExpression() {
        this.autoReturnToNeutral = true; // 恢复自动回到 neutral
        this.setBaseExpression('neutral');
    }

    setBaseExpression(name) {
        // 彻底关闭自动切换，防止干扰
        this.autoChangeMood = false;

        // 如果是眨眼表情，区分处理单眼和双眼
        const lowerName = (name || '').toLowerCase();
        if (lowerName.includes('blink')) {
            if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) {
                return;
            }

            const expressionManager = this.manager.currentModel.vrm.expressionManager;

            // 判断是单眼还是双眼
            if (lowerName.includes('left') || lowerName.includes('right')) {
                // 单眼眨眼：直接设置权重，不使用 blinkWeight 动画

                // 设置手动眨眼标志，防止 _updateWeights 干扰
                this.manualBlinkInProgress = name;

                // 立即设置为1.0（闭眼）
                expressionManager.setValue(name, 1.0);

                // 150ms后保持闭眼状态（确保眼睛完全闭上）
                setTimeout(() => {
                    expressionManager.setValue(name, 1.0);
                }, 150);

                // 300ms后开始睁眼
                setTimeout(() => {
                    expressionManager.setValue(name, 0.0);
                    this.currentMood = 'neutral';
                    this.manualBlinkInProgress = null; // 清除标志
                }, 300);

            } else {
                // 双眼眨眼：使用 blinkWeight 动画（原有逻辑）

                // 强制触发一次眨眼动画
                this.blinkState = 1;  // 开始闭眼
                this.blinkTimer = 0;
                this.blinkWeight = 0.0;

                // 启用自动眨眼来完成这次动画
                const wasAutoBlink = this.autoBlink;
                this.autoBlink = true;

                // 动画完成后恢复原状态
                setTimeout(() => {
                    this.autoBlink = wasAutoBlink;
                    this.currentMood = 'neutral';
                }, 500);
            }

            return;
        }

        // 非眨眼表情：复用 _applyMoodImmediately 逻辑
        this.currentMood = name || 'neutral';

        // 清除之前的回到 neutral 定时器
        if (this.neutralReturnTimer) {
            clearTimeout(this.neutralReturnTimer);
            this.neutralReturnTimer = null;
        }

        // 统一使用 _applyMoodImmediately 处理（支持映射表和直接表情名）
        this._applyMoodImmediately(name || 'neutral');

        // 设置自动返回 neutral 的定时器
        if (name && name !== 'neutral' && this.autoReturnToNeutral) {
            this.neutralReturnTimer = setTimeout(() => {
                this.currentMood = 'neutral';
                this.manualExpressionInProgress = null;
                this.neutralReturnTimer = null;
                this._applyMoodImmediately('neutral');
            }, this.neutralReturnDelay);
        }
    }
}

window.VRMExpression = VRMExpression;