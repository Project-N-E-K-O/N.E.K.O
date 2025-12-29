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
        this.autoChangeMood = true; 
        this.moodTimer = 0;
        this.nextMoodTime = 5.0; 
        this.currentMood = 'neutral'; 
        
        // 【关键】情绪映射表：把一种情绪映射到多种可能的 VRM 表情名上
        // 键是我们的内部情绪名，值是可能出现在模型里的表情名列表
        this.moodMap = {
            'neutral': ['neutral'],
            // 开心类：兼容 VRM1.0(happy), VRM0.0(joy, fun), 其他(smile, warau)
            'happy': ['happy', 'joy', 'fun', 'smile', 'joy_01', 'a'], 
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
     * 临时测试版 update：每秒切换一个表情，帮你找数字
     */
    update(delta) {
        if (!this.manager.currentModel || !this.manager.currentModel.vrm || !this.manager.currentModel.vrm.expressionManager) return;
        const expressionManager = this.manager.currentModel.vrm.expressionManager;
    }

    _printAvailableExpressions(manager) {
        let names = [];
        if (manager.expressions) names = Object.keys(manager.expressions);
        else if (manager._expressionMap) names = Object.keys(manager._expressionMap);
        console.log(`[VRM Expression] 模型支持的表情:`, names);
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
        const lerpSpeed = 3.0 * delta; 

        // 获取模型实际支持的所有表情名
        let modelExpressionNames = [];
        if (expressionManager.expressions) modelExpressionNames = Object.keys(expressionManager.expressions);
        else if (expressionManager._expressionMap) modelExpressionNames = Object.keys(expressionManager._expressionMap);

        // 获取当前心情对应的候选词列表 (例如 happy -> ['happy', 'joy', 'fun'])
        const targetCandidateNames = this.moodMap[this.currentMood] || [];

        modelExpressionNames.forEach(name => {
            let targetWeight = 0.0;
            const lowerName = name.toLowerCase();

            // 1. 眨眼控制
            if (lowerName.includes('blink')) {
                // 如果是左/右眼单独控制，统一应用 blinkWeight
                expressionManager.setValue(name, this.blinkWeight);
                return; 
            }

            // 2. 排除项 (口型等)
            if (this.excludeExpressions.some(ex => lowerName === ex || lowerName.includes(ex))) {
                return;
            }

            // 3. 情绪控制
            // 检查当前表情名(name) 是否存在于当前心情的候选列表(targetCandidateNames) 中
            // 例如：如果心情是 happy，候选是 [joy, fun]，如果当前 name 是 joy，则命中
            const isMatch = targetCandidateNames.some(candidate => lowerName === candidate.toLowerCase());

            if (isMatch) {
                targetWeight = 0.5; // 目标权重 (太高容易穿模，0.5 比较自然)
                if (lowerName === 'neutral') targetWeight = 0.0; // neutral 特殊处理
            }

            // 4. 平滑过渡
            if (this.currentWeights[name] === undefined) this.currentWeights[name] = 0.0;
            
            const diff = targetWeight - this.currentWeights[name];
            if (Math.abs(diff) < 0.005) {
                this.currentWeights[name] = targetWeight;
            } else {
                this.currentWeights[name] += diff * lerpSpeed;
            }

            // 5. 应用
            expressionManager.setValue(name, this.currentWeights[name]);
        });
    }

    setBaseExpression(name, weight = 0.5) {
        // 手动设置时，尝试反向查找最接近的情绪 key
        for (const [moodKey, candidates] of Object.entries(this.moodMap)) {
            if (candidates.includes(name) || moodKey === name) {
                this.currentMood = moodKey;
                this.moodTimer = 0;
                console.log(`[VRM Expression] 手动触发心情: ${moodKey}`);
                return;
            }
        }
        // 如果找不到，就强制作为一种新情绪
        this.currentMood = name;
        this.moodMap[name] = [name]; // 临时注册
    }
}

window.VRMExpression = VRMExpression;
console.log('[VRM Expression] 智能映射版已加载');