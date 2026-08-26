/**
 * MMD 表情模块 - MorphTarget 控制、情感系统集成
 * 参考 vrm-expression.js 的情感映射系统
 */

// 共振峰分析器输出的 VRM blendshape 键 → MMD 五元音 morph 键。
// 冻结在模块作用域，供 update() 每帧零分配读取（见 MMDExpression.FORMANT_TO_MMD_VOWEL）。
const FORMANT_TO_MMD_VOWEL = Object.freeze({ aa: 'a', ih: 'i', ou: 'u', ee: 'e', oh: 'o' });
// 遍历顺序也预先固定，避免每帧 Object.keys 分配新数组。
const FORMANT_KEYS = Object.freeze(Object.keys(FORMANT_TO_MMD_VOWEL));

class MMDExpression {
    constructor(manager) {
        this.manager = manager;

        // 眨眼配置
        this.autoBlink = true;
        this.blinkTimer = 0;
        this.nextBlinkTime = 3.0;
        this.blinkState = 0; // 0:睁眼, 1:闭眼中, 2:睁开中
        this.blinkWeight = 0.0;

        this.manualBlinkInProgress = null;
        this.manualExpressionInProgress = null;

        // 情绪配置
        this.currentMood = 'neutral';
        this.autoReturnToNeutral = true;
        this.neutralReturnDelay = 3000;
        this.neutralReturnTimer = null;

        // 当前各 morph 的权重
        this.currentWeights = {};

        // 常见 MMD 表情名（日文/英文）到情感的映射
        // 默认值，可通过 loadMoodMap() 从后端加载覆盖
        this.moodMap = {
            'neutral': ['default', 'ニュートラル'],
            'happy': ['笑い', 'にやり', 'にこり', 'smile', 'happy', 'joy', 'ワ'],
            'sad': ['悲しい', '泣き', 'sad', 'sorrow', 'しょんぼり'],
            'angry': ['怒り', 'angry', 'anger', 'むっ'],
            'surprised': ['驚き', 'びっくり', 'surprised', 'shock', 'おっ'],
            'relaxed': ['穏やか', 'relaxed', 'calm', '微笑み'],
            'fear': ['恐怖', 'fear', 'scared', 'おびえ']
        };

        // MMD 常见眨眼 morph 名
        this.blinkMorphNames = ['まばたき', 'blink', 'まばたき左', 'まばたき右', 'blink_l', 'blink_r'];

        // MMD 常见口型 morph 名（用于口型同步）
        this.lipMorphNames = {
            'a': ['あ', 'a'],
            'i': ['い', 'i'],
            'u': ['う', 'u'],
            'e': ['え', 'e'],
            'o': ['お', 'o']
        };
    }

    // ═══════════════════ 后端配置加载 ═══════════════════

    async loadMoodMap(modelName) {
        if (!modelName) return;
        try {
            const response = await fetch(`/api/model/mmd/emotion_mapping?model=${encodeURIComponent(modelName)}`);
            if (response.ok) {
                const data = await response.json();
                if (data.success && data.mapping) {
                    this.moodMap = { ...this.moodMap, ...data.mapping };
                    console.log('[MMD Expression] 从后端加载了情感映射');
                }
            }
        } catch (error) {
            console.warn('[MMD Expression] 加载情感映射失败，使用默认配置:', error);
        }
    }

    // ═══════════════════ Morph 控制 ═══════════════════

    _getMesh() {
        return this.manager.currentModel?.mesh || null;
    }

    _getMorphDict() {
        const mesh = this._getMesh();
        return mesh?.morphTargetDictionary || null;
    }

    _getMorphInfluences() {
        const mesh = this._getMesh();
        return mesh?.morphTargetInfluences || null;
    }

    /**
     * 获取模型所有 morph 名称列表
     */
    getMorphNames() {
        const dict = this._getMorphDict();
        return dict ? Object.keys(dict) : [];
    }

    /**
     * 设置单个 morph 权重
     */
    setMorphWeight(morphName, weight) {
        const dict = this._getMorphDict();
        const influences = this._getMorphInfluences();
        if (!dict || !influences) return false;

        const index = dict[morphName];
        if (index === undefined) return false;

        const clampedWeight = Math.max(0, Math.min(1, weight));
        influences[index] = clampedWeight;
        this.currentWeights[morphName] = clampedWeight;
        return true;
    }

    /**
     * 获取单个 morph 权重
     */
    getMorphWeight(morphName) {
        const dict = this._getMorphDict();
        const influences = this._getMorphInfluences();
        if (!dict || !influences) return 0;

        const index = dict[morphName];
        if (index === undefined) return 0;
        return influences[index] || 0;
    }

    /**
     * 批量设置 morph 权重
     */
    setMorphWeights(weightsMap) {
        if (!weightsMap) return;
        for (const [name, weight] of Object.entries(weightsMap)) {
            this.setMorphWeight(name, weight);
        }
    }

    /**
     * 重置所有 morph 为 0
     */
    resetAllMorphs() {
        const influences = this._getMorphInfluences();
        if (!influences) return;

        for (let i = 0; i < influences.length; i++) {
            influences[i] = 0;
        }
        this.currentWeights = {};
    }

    // ═══════════════════ 情感系统 ═══════════════════

    /**
     * 设置情感（兼容 LanLan1 API）
     * 根据 moodMap 查找对应的 morph 名称并设置
     */
    setEmotion(emotion) {
        if (!emotion) return;

        if (emotion === 'neutral') {
            if (this.neutralReturnTimer) {
                clearTimeout(this.neutralReturnTimer);
                this.neutralReturnTimer = null;
            }
            this._clearEmotionMorphs();
            this.currentMood = 'neutral';
            this.manualExpressionInProgress = null;
            return;
        }

        const morphNames = this.moodMap[emotion];
        if (!morphNames || morphNames.length === 0) {
            console.warn(`[MMD Expression] 未知情感: ${emotion}`);
            return;
        }

        const dict = this._getMorphDict();
        if (!dict) return;

        // 先确认目标 morph 存在，再清除旧表情
        const matchedName = morphNames.find(name => dict[name] !== undefined);
        if (!matchedName) {
            console.warn(`[MMD Expression] 情感 "${emotion}" 在当前模型中无匹配 morph`);
            return;
        }

        this._clearEmotionMorphs();
        this.setMorphWeight(matchedName, 1.0);
        this.currentMood = emotion;
        this.manualExpressionInProgress = matchedName;

        if (this.autoReturnToNeutral) {
            this._scheduleNeutralReturn();
        }
    }

    _clearEmotionMorphs() {
        const allEmotionMorphs = new Set();
        for (const names of Object.values(this.moodMap)) {
            names.forEach(n => allEmotionMorphs.add(n));
        }
        for (const name of allEmotionMorphs) {
            this.setMorphWeight(name, 0);
        }
        this.manualExpressionInProgress = null;
    }

    _scheduleNeutralReturn() {
        if (this.neutralReturnTimer) {
            clearTimeout(this.neutralReturnTimer);
        }
        this.neutralReturnTimer = setTimeout(() => {
            this._clearEmotionMorphs();
            this.currentMood = 'neutral';
            this.neutralReturnTimer = null;
        }, this.neutralReturnDelay);
    }

    // ═══════════════════ 眨眼 ═══════════════════

    updateBlink(delta) {
        if (!this.autoBlink || this.manualBlinkInProgress) return;

        this.blinkTimer += delta;

        switch (this.blinkState) {
            case 0: // 睁眼等待
                if (this.blinkTimer >= this.nextBlinkTime) {
                    this.blinkState = 1;
                    this.blinkTimer = 0;
                }
                break;
            case 1: // 闭眼中
                this.blinkWeight = Math.min(1, this.blinkWeight + delta * 15);
                this._applyBlink(this.blinkWeight);
                if (this.blinkWeight >= 1) {
                    this.blinkState = 2;
                    this.blinkTimer = 0;
                }
                break;
            case 2: // 睁开中
                this.blinkWeight = Math.max(0, this.blinkWeight - delta * 10);
                this._applyBlink(this.blinkWeight);
                if (this.blinkWeight <= 0) {
                    this.blinkState = 0;
                    this.blinkTimer = 0;
                    // 随机下次眨眼间隔 2-6 秒
                    this.nextBlinkTime = 2 + Math.random() * 4;
                }
                break;
        }
    }

    _applyBlink(weight) {
        const dict = this._getMorphDict();
        if (!dict) return;

        for (const name of this.blinkMorphNames) {
            if (dict[name] !== undefined) {
                this.setMorphWeight(name, weight);
            }
        }
    }

    // ═══════════════════ 口型同步 ═══════════════════

    /**
     * 设置口型值（0-1），映射到"あ"morph
     */
    setMouth(value) {
        const clamped = Math.max(0, Math.min(1, value));

        // 主要映射到 "あ"（张嘴）
        for (const name of (this.lipMorphNames['a'] || [])) {
            this.setMorphWeight(name, clamped);
        }

        // 轻微映射到 "お"（嘴唇圆形），增加自然感
        for (const name of (this.lipMorphNames['o'] || [])) {
            this.setMorphWeight(name, clamped * 0.3);
        }
    }

    /**
     * 高级口型同步：根据音素设置多个口型 morph
     */
    setLipSync(phoneme, weight) {
        const names = this.lipMorphNames[phoneme];
        if (!names) return;

        for (const name of names) {
            this.setMorphWeight(name, weight);
        }
    }

    /**
     * 统一重置全部五元音口型 morph 为 0。
     * stopLipSync / 切换表情时调用，防止 formant 模式下
     * い/う/え 等 morph 残留（setMouth(0) 只清 あ + お×0.3）。
     */
    resetAllLipMorphs() {
        for (const vowel of Object.keys(this.lipMorphNames)) {
            for (const name of (this.lipMorphNames[vowel] || [])) {
                this.setMorphWeight(name, 0);
            }
        }
    }

    // ═══════════════════ 帧更新 ═══════════════════

    // 分析器输出的 VRM blendshape 键 → MMD 五元音 morph 键（lipMorphNames 的键）。
    // 引用模块级 frozen 常量而非在 getter 里现造字面量：update() 每帧都会读它，
    // 每帧新建一个对象 + 一个 Object.keys 数组是 60fps 热路径上的无谓 GC 压力。
    static get FORMANT_TO_MMD_VOWEL() {
        return FORMANT_TO_MMD_VOWEL;
    }

    update(delta) {
        this.updateBlink(delta);

        // 口型同步（如果动画模块有音频分析）
        const anim = this.manager.animationModule;
        if (anim && anim._lipSyncEnabled) {
            if (anim._formantAnalyzer) {
                // 五元音共振峰路径：每帧产出 {aa,ee,ih,oh,ou} 连续权重，映射到
                // あ/い/う/え/お morph。
                // delta 的夹紧（非有限值回退、负值归零、上界截断）已收口到
                // FormantLipSyncAnalyzer.update 内部，两条接入路径不再各自防御。
                const weights = anim._formantAnalyzer.update(delta);
                const map = FORMANT_TO_MMD_VOWEL;
                // 发声帧写全五个（含 0），覆盖待机 VMD 可能残留的口型轨道；
                // 静音帧只把 lip sync 主驱动的 あ/お 归零，不碰 い/う/え，
                // 让待机 VMD 的口型轨道照常播放——与旧单通道路径
                // 「清零只在 lipValue>0.05 分支执行」的取舍保持一致。
                const speaking = FORMANT_KEYS.some((k) => (weights[k] ?? 0) > 0);
                for (const formantKey of FORMANT_KEYS) {
                    const vowel = map[formantKey];
                    if (!speaking && vowel !== 'a' && vowel !== 'o') continue;
                    const target = weights[formantKey] ?? 0;
                    for (const name of (this.lipMorphNames[vowel] || [])) {
                        this.setMorphWeight(name, target);
                    }
                }
                return;
            }
            // 旧单通道路径（FormantLipSyncAnalyzer 未加载时回退）
            const lipValue = anim.getLipSyncValue();
            if (window.DEBUG_AUDIO) {
                console.log('[MMD Expression] 口型同步检测:', { 
                    lipValue, 
                    threshold: 0.05,
                    willUpdate: lipValue > 0.05 
                });
            }
            if (lipValue > 0.05) {
                // mixer.update 在本帧可能已写入待机 VMD 的 い/う/え 口型轨道，
                // setMouth 之后只覆盖 あ/お，其余元音残留会与 lip sync 叠加成混合口型。
                // 这里在写 あ/お 之前先把 lip sync 不主动驱动的 い/う/え 置 0，确保
                // 语音口型同步期间嘴部完全由 lip sync 驱动。清零只在 lipValue>0.05
                // 分支执行——非 lip sync 帧仍保留 VMD 口型轨道的正常播放。
                for (const phoneme of ['i', 'u', 'e']) {
                    for (const name of (this.lipMorphNames[phoneme] || [])) {
                        this.setMorphWeight(name, 0);
                    }
                }
                this.setMouth(lipValue);
            } else {
                this.setMouth(0);
            }
        }
    }

    // ═══════════════════ 清理 ═══════════════════

    dispose() {
        if (this.neutralReturnTimer) {
            clearTimeout(this.neutralReturnTimer);
            this.neutralReturnTimer = null;
        }
        this.currentWeights = {};
        this.manualBlinkInProgress = null;
        this.manualExpressionInProgress = null;
    }
}
