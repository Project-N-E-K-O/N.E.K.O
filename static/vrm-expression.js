/**
 * VRM 表情模块 - 智能映射版
 * 解决 VRM 0.x 和 1.0 表情命名不一致导致的面瘫问题
 */

class VRMExpression {
    constructor(manager) {
        this.manager = manager;
        
        // --- 眨眼配置 ---
        this.autoBlink = true;
        this.blinkTimer = 0;
        this.nextBlinkTime = 3.0;
        this.blinkState = 0; // 0:睁眼, 1:闭眼, 2:睁眼
        this.blinkWeight = 0.0; 

        // --- 情绪配置 ---
        this.autoChangeMood = false; 
        this.moodTimer = 0;
        this.nextMoodTime = 5.0; 
        this.currentMood = 'neutral'; 
        
        // 【关键】情绪映射表：把一种情绪映射到多种可能的 VRM 表情名上
        // 键是我们的内部情绪名，值是可能出现在模型里的表情名列表
        this.moodMap = {
            'neutral': ['neutral'],
            // 开心类：兼容 VRM1.0(happy), VRM0.0(joy, fun), 其他(smile, warau)
            'happy': ['happy', 'joy', 'fun', 'smile', 'joy_01'], 
            // 放松类：
            'relaxed': ['relaxed', 'joy', 'fun', 'content'],
            // 惊讶类：
            'surprised': ['surprised', 'surprise', 'shock', 'e', 'o'],
            // 悲伤类 (偶尔用一下)
            'sad': ['sad', 'sorrow', 'angry', 'grief']
        };

        this.availableMoods = Object.keys(this.moodMap);
        
        // 排除列表 (不参与情绪切换，由 blink 或 lipSync 控制)
        this.excludeExpressions = [
            'blink', 'blink_l', 'blink_r', 'blinkleft', 'blinkright',
            'aa', 'ih', 'ou', 'ee', 'oh',
            'lookup', 'lookdown', 'lookleft', 'lookright'
        ];

        this.currentWeights = {}; 
        this._hasPrintedDebug = false; // 防止日志刷屏
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
            console.log(`[VRM Expression] 情绪切换为: ${moodName}`);
        } else {
            console.warn(`[VRM Expression] 未知情绪: ${moodName}，忽略切换`);
        }
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

        // 3. 【关键】计算并应用所有权重
        this._updateWeights(delta, expressionManager);
    }
    /**
     * 【核心修复】统一获取表情名称列表
     * 自动判断 expressions 是 Map、Object 还是 Array，解决显示 0-13 的问题
     */
    _getExpressionNames(expressionManager) {
        if (!expressionManager) return [];
        
        const exprs = expressionManager.expressions;

        // 1. 如果是 Map (VRM 1.0 标准)，转为数组
        if (exprs instanceof Map) {
            return Array.from(exprs.keys());
        }

        // 2. 如果是 Array (某些加载器版本)，提取内部的 name 属性
        if (Array.isArray(exprs)) {
            return exprs.map(e => e.expressionName || e.name || e.presetName).filter(n => n);
        }

        // 3. 如果是普通 Object (VRM 0.0)，直接取键名
        if (typeof exprs === 'object') {
            return Object.keys(exprs);
        }

        // 4. 备用方案：检查内部属性 _expressionMap
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
        // 【注意】这里删除了 'blink'，这样你就能在列表里看到眨眼了
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
            this.blinkWeight += delta * 12.0; // 眨眼速度
            if (this.blinkWeight >= 1.0) {
                this.blinkWeight = 1.0;
                this.blinkState = 2;
            }
        } else if (this.blinkState === 2) {
            this.blinkWeight -= delta * 10.0; 
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
            console.log(`[VRM Expression] 切换心情: ${this.currentMood}`);
        }
    }

    _updateWeights(delta, expressionManager) {
        // 提高响应速度
        const lerpSpeed = 15.0 * delta; 

        // 1. 获取所有表情名
        const modelExpressionNames = this._getExpressionNames(expressionManager);

        // 2. 获取当前目标表情 (例如 "angry" 或 "blinkLeft")
        const targetName = this.currentMood;
        
        // 判断当前用户是不是正在手动测试眨眼
        const isUserTestingBlink = targetName.toLowerCase().includes('blink');

        // 3. 遍历每一个表情进行设置
        modelExpressionNames.forEach(name => {
            let targetWeight = 0.0;
            const lowerName = name.toLowerCase();
            const targetNameLower = targetName.toLowerCase();

            // --- A. 判断是否为选中项 (最高优先级) ---
            // 直接名字匹配
            let isMatch = (name === targetName || lowerName === targetNameLower);
            // 2. 【修复】如果没有直接匹配，检查映射表 (moodMap)
            // 解决 pickRandomMood 选出 'happy' 但模型只有 'Joy' 的情况
            if (!isMatch && this.moodMap[targetName]) {
                const candidates = this.moodMap[targetName];
                // 检查候选词里是否有当前这个 name
                isMatch = candidates.some(candidate => candidate.toLowerCase() === lowerName);
            }

            if (isMatch) {
                targetWeight = 1.0;
            }
            // --- B. 处理自动眨眼 (次优先级) ---
            // 条件：
            // 1. 当前表情是眨眼类 (blink)
            // 2. 用户【没有】在手动测试眨眼 (防止手动 blinkLeft 时被自动 blink 覆盖)
            // 3. 该表情【不是】选中的那个 (否则会在上面 A 步被设为 1.0)
            else if (lowerName.includes('blink') && !isUserTestingBlink) {
                expressionManager.setValue(name, this.blinkWeight);
                return; // 眨眼由定时器控制，处理完直接跳过后续插值
            }
            // --- C. 其他情况归零 ---
            else {
                targetWeight = 0.0;
            }

            // --- D. 执行插值和应用 ---
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

    setBaseExpression(name) {
        console.log(`[VRM Expression] 手动设置表情: ${name}`);
        // 彻底关闭自动切换，防止干扰
        this.autoChangeMood = false;
        this.currentMood = name || 'neutral';
          
    }
}

window.VRMExpression = VRMExpression;
console.log('[VRM Expression] 智能映射版已加载');