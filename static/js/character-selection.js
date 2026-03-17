/**
 * 角色甄选引导
 *
 * 功能说明：
 * - 阶段一：氛围唤醒 - 点击激活按钮
 * - 阶段二：性格挑选 - 选择人设卡片
 * - 阶段三:真情互动 - 显示问候语
 * - 阶段四:用户确认 - 签订契约
 * - 完成后自动配置默认猫娘的音色和性格
 */

// 角色数据配置（头像保持静态，文字通过 i18n 获取）
const CHARACTER_DATA = {
    tsundere_neko: { avatar: 'ε٩(๑> ₃ <)۶з' },
    cool_mech:     { avatar: '┗|*｀0′*|┛' },
    intellectual_healer: { avatar: '(๑╙◡╙๑)' },
    efficiency_expert:   { avatar: '(☆- v -)' }
};

// 角色类型到音色和设定的映射配置
const CHARACTER_VOICE_MAPPING = {
    tsundere_neko: {
        voiceId: 'voice-tone-PGLiTXeJCS',  // 俏皮女孩
        personality: '傲娇猫娘'
    },
    cool_mech: {
        voiceId: 'voice-tone-PGLlMvr0Ai',  // 清冷御姐
        personality: '高冷机器人'
    },
    intellectual_healer: {
        voiceId: 'voice-tone-PGLmTEeUOu',  // 甜美御姐
        personality: '知心大姐姐'
    },
    efficiency_expert: {
        voiceId: 'voice-tone-PGLlrd5SNM',  // 温柔少女
        personality: '高效直接，学识渊博的专家'
    }
};

// 默认猫娘档案名及 localStorage 追踪键
const DEFAULT_CATGIRL_NAME = 'test';
const CATGIRL_SELECTION_STORAGE_KEY = 'neko_default_catgirl_name';

class CharacterSelection {
    constructor() {
        this.overlay = document.getElementById('character-selection-overlay');
        this.currentStage = 1;
        this.selectedCharacter = null;
        this.isOpen = true;
        this._selectTimer = null;
        this._closeTimer = null;
        this._typeTimer = null;
        this._onLocaleChange = () => this._applyStaticI18n();
        // 初始化背景音乐
        this.bgmAudio = null;
        this._initBgm();
        // 初始化星星特效状态
        this._starIntervalId = null;
        this._currentClickX = 0;
        this._currentClickY = 0;
        this._isMousePressed = false;
        this._activeStarCount = 0;  // 防爆炸：跟踪活跃星星数量
        this._maxActiveStar = 50;   // 防爆炸：限制最多 50 个星星
        // 保存 mouseup 处理器引用，便于清理
        this._handleMouseUp = () => this._onMouseUp();
        // 保存 stage 监听器引用，便于清理（防止内存泄漏）
        this._stageMouseDownHandlers = new Map();
        this._stageMouseMoveHandlers = new Map();
        // 用于取消打字 Promise（防止内存泄漏）
        this._typeAbort = null;
        this.init();
    }

    _initBgm() {
        // 初始化背景音乐
        this.bgmAudio = new Audio('/static/default/Y-1.mp3');
        this.bgmAudio.loop = true;
        this.bgmAudio.volume = 0.5;
        // 添加错误处理（网络错误、404等）
        this.bgmAudio.addEventListener('error', () => {
            console.warn('[CharacterSelection] 背景音乐加载失败:', {
                errorCode: this.bgmAudio.error?.code,
                errorMsg: this.bgmAudio.error?.message,
                src: this.bgmAudio.src
            });
        });
    }

    init() {
        this._applyStaticI18n();
        // i18n 就绪或语言切换后重新翻译（overlay 是动态注入的，不会被 updatePageTexts 扫到）
        window.addEventListener('localechange', this._onLocaleChange);
        this.bindEvents();
    }

    /**
     * 主动翻译 overlay 内所有 data-i18n 元素。
     * window.t 不可用时保留 HTML 中的中文 fallback，不影响显示。
     */
    _applyStaticI18n() {
        if (typeof window.t !== 'function' || !this.overlay) return;
        this.overlay.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            const translated = window.t(key);
            // 翻译失败时 i18next 返回 key 本身，保留原文不覆盖
            if (translated && translated !== key) {
                el.textContent = translated;
            }
        });
    }

    start() {
        // 入口方法，overlay 已经在 HTML 中默认显示
        console.log('[CharacterSelection] 角色甄选流程启动');
    }
    bindEvents() {
        // 阶段一：开始按钮
        const startBtn = document.getElementById('start-btn');
        startBtn?.addEventListener('click', () => {
            this.playBgm();  // 点击时启动背景音乐
            this.goToStage(2);
        });
        // 阶段二：卡片选择
        document.querySelectorAll('.character-card').forEach(card => {
            card.addEventListener('click', (e) => this.selectCharacter(e));
            card.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.selectCharacter(e);
                }
            });
        });
        // 阶段三：问候确认
        const confirmGreetingBtn = document.getElementById('confirm-greeting-btn');
        confirmGreetingBtn?.addEventListener('click', () => this.goToStage(4));
        // 阶段四：最终确认
        const finalConfirmBtn = document.getElementById('final-confirm-btn');
        finalConfirmBtn?.addEventListener('click', () => this.finalizeSelection());
        const restartBtn = document.getElementById('restart-btn');
        restartBtn?.addEventListener('click', () => this.goToStage(2));
        // 跳过按钮
        const skipBtn = document.getElementById('skip-btn');
        skipBtn?.addEventListener('click', () => this.skip());
        
        // 为character-overlay添加点击特效（在阶段区域内）
        const stageAreas = this.overlay?.querySelectorAll('.character-stage');
        stageAreas?.forEach(stage => {
            // 鼠标按下事件 - 保存引用便于清理
            const mouseDownHandler = (e) => {
                if (!e.target.closest('button') && !e.target.closest('.character-card')) {
                    this._isMousePressed = true;
                    this._currentClickX = e.clientX;
                    this._currentClickY = e.clientY;
                    
                    // 立即生成一次
                    this.createClickStars(e);
                    
                    // 然后开始持续生成（间隔80ms）
                    this._starIntervalId = setInterval(() => {
                        if (this._isMousePressed) {
                            this.createClickStarsAtPosition(this._currentClickX, this._currentClickY);
                        }
                    }, 80);
                }
            };
            
            // 鼠标移动事件（更新位置形成拖尾）
            const mouseMoveHandler = (e) => {
                if (this._isMousePressed) {
                    this._currentClickX = e.clientX;
                    this._currentClickY = e.clientY;
                }
            };
            
            stage.addEventListener('mousedown', mouseDownHandler);
            stage.addEventListener('mousemove', mouseMoveHandler);
            
            // 保存引用便于后续清理（防止内存泄漏）
            this._stageMouseDownHandlers.set(stage, mouseDownHandler);
            this._stageMouseMoveHandlers.set(stage, mouseMoveHandler);
        });
        
        // 鼠标松开事件（全局监听） - 注意：_handleMouseUp 已在构造函数中创建
        document.addEventListener('mouseup', this._handleMouseUp);
    }
    
    _onMouseUp() {
        this._isMousePressed = false;
        if (this._starIntervalId !== null) {
            clearInterval(this._starIntervalId);
            this._starIntervalId = null;
        }
    }
    
    createClickStarsAtPosition(clickX, clickY) {
        // 防爆炸：限制同时存在的星星数量（避免卡顿）
        if (this._activeStarCount >= this._maxActiveStar) {
            return;
        }
        
        // 生成2-3个星星（较少）
        const starCount = Math.floor(Math.random() * 2) + 2;
        for (let i = 0; i < starCount; i++) {
            // 再次检查是否超过限制（防止在循环中超过）
            if (this._activeStarCount >= this._maxActiveStar) {
                break;
            }
            
            const star = document.createElement('div');
            star.className = 'click-star';
            star.textContent = '✦';
            star.style.left = clickX + 'px';
            star.style.top = clickY + 'px';
            
            // 随机分散方向
            const angle = (Math.PI * 2 / starCount) * i + (Math.random() - 0.5) * 0.8;
            const distance = 60 + Math.random() * 80;
            const tx = Math.cos(angle) * distance;
            const ty = Math.sin(angle) * distance;
            
            star.style.setProperty('--tx', tx + 'px');
            star.style.setProperty('--ty', ty + 'px');
            
            document.body.appendChild(star);
            this._activeStarCount++;  // 递增计数
            
            // 动画完成后移除
            setTimeout(() => {
                star.remove();
                this._activeStarCount--;  // 递减计数
            }, 1600);
        }
    }
    
    createClickStars(event) {
        const clickX = event.clientX;
        const clickY = event.clientY;
        
        // 生成2-3个星星（较少）
        const starCount = Math.floor(Math.random() * 2) + 2;
        for (let i = 0; i < starCount; i++) {
            // 检查是否超过限制
            if (this._activeStarCount >= this._maxActiveStar) {
                break;
            }
            
            const star = document.createElement('div');
            star.className = 'click-star';
            star.textContent = '✦';
            star.style.left = clickX + 'px';
            star.style.top = clickY + 'px';
            
            // 随机分散方向
            const angle = (Math.PI * 2 / starCount) * i + (Math.random() - 0.5) * 0.8;
            const distance = 60 + Math.random() * 80;
            const tx = Math.cos(angle) * distance;
            const ty = Math.sin(angle) * distance;
            
            star.style.setProperty('--tx', tx + 'px');
            star.style.setProperty('--ty', ty + 'px');
            
            document.body.appendChild(star);
            this._activeStarCount++;  // 递增计数
            
            // 动画完成后移除
            setTimeout(() => {
                star.remove();
                this._activeStarCount--;  // 递减计数
            }, 1600);
        }
    }
    goToStage(stageNumber) {
        console.log(`[CharacterSelection] 切换到阶段 ${stageNumber}`);
        // 隐藏当前阶段
        const currentStage = document.querySelector('.character-stage.active');
        if (currentStage) {
            currentStage.classList.remove('active');
        }
        // 显示目标阶段
        const targetStage = document.getElementById(`stage-${stageNumber}`);
        if (targetStage) {
            targetStage.classList.add('active');
        }
        this.currentStage = stageNumber;
        
        // 各阶段播放背景音乐
        if (stageNumber === 1) {
            // 阶段一：氛围唤醒 - 尝试播放音乐
            this.playBgm();
        } else if (stageNumber === 2) {
            // 阶段二：性格挑选 - 继续播放
            this.playBgm();
        } else if (stageNumber === 3) {
            // 阶段三：真情互动 - 继续播放
            this.playBgm();
            // 触发问候动画
            this.playGreeting();
        } else if (stageNumber === 4) {
            // 阶段四：用户确认 - 继续播放
            this.playBgm();
            // 更新最终信息
            this.updateFinalInfo();
        }
    }

    playBgm() {
        if (this.bgmAudio && this.bgmAudio.paused) {
            this.bgmAudio.play().catch(err => {
                console.warn('[CharacterSelection] 背景音乐播放失败:', err);
            });
        }
    }

    stopBgm() {
        if (this.bgmAudio) {
            this.bgmAudio.pause();
            this.bgmAudio.currentTime = 0;
        }
    }
    selectCharacter(e) {
        const card = e.currentTarget;
        // 移除之前的选中状态
        const prev = document.querySelector('.character-card.selected');
        if (prev) {
            prev.classList.remove('selected');
            prev.setAttribute('aria-pressed', 'false');
        }
        // 添加新的选中状态
        card.classList.add('selected');
        card.setAttribute('aria-pressed', 'true');
        // 保存选中的人设
        this.selectedCharacter = {
            id: card.dataset.id,
            name: card.querySelector('.card-name').textContent,
            desc: card.querySelector('.card-desc').textContent
        };
        console.log('[CharacterSelection] 选中角色:', this.selectedCharacter);
        // 延迟进入阶段三（清除已有定时器防止重复触发）
        if (this._selectTimer != null) {
            clearTimeout(this._selectTimer);
            this._selectTimer = null;
        }
        this._selectTimer = setTimeout(() => {
            this._selectTimer = null;
            this.goToStage(3);
        }, 600);
    }
    async playGreeting() {
        const data = CHARACTER_DATA[this.selectedCharacter.id];
        const t = window.t || ((_key, fallback) => fallback);
        const greetingText = document.getElementById('greeting-text');
        const greetingTitle = document.getElementById('greeting-title');
        const confirmBtn = document.getElementById('confirm-greeting-btn');
        const avatar = document.getElementById('greeting-avatar');

        // 显示角色头像
        if (avatar) {
            avatar.textContent = data.avatar;
            // 根据角色设置颜色
            const colorMap = {
                tsundere_neko: '#FFB800',      // 金色
                cool_mech: '#0066cc',          // 蓝色
                intellectual_healer: '#C71585', // 紫色
                efficiency_expert: '#008B8B'   // 青绿色
            };
            avatar.style.color = colorMap[this.selectedCharacter.id] || '#44b7fe';
        } else {
            console.warn('[CharacterSelection] playGreeting: 元素 #greeting-avatar 不存在');
        }

        // 更新标题
        if (greetingTitle) {
            greetingTitle.textContent = t(
                'memory.characterSelection.connectingTitle',
                '时空穿越中——'
            ).replace('{{name}}', this.selectedCharacter.name);
        } else {
            console.warn('[CharacterSelection] playGreeting: 元素 #greeting-title 不存在');
        }

        // 打字机效果
        if (greetingText) {
            const greeting = t(
                `memory.characterSelection.${this.selectedCharacter.id}.greeting`,
                ''
            );
            greetingText.classList.add('typing');
            await this.typeText(greetingText, greeting);
            greetingText.classList.remove('typing');
        } else {
            console.warn('[CharacterSelection] playGreeting: 元素 #greeting-text 不存在');
        }

        // 显示确认按钮
        if (confirmBtn) {
            confirmBtn.style.display = 'inline-block';
        } else {
            console.warn('[CharacterSelection] playGreeting: 元素 #confirm-greeting-btn 不存在');
        }
    }
    typeText(element, text) {
        // 取消之前未完成的打字任务（防止内存泄漏）
        if (this._typeAbort) {
            this._typeAbort.abort();
        }
        
        this._typeAbort = new AbortController();
        
        return new Promise((resolve, reject) => {
            // 清除之前的打字定时器
            if (this._typeTimer !== null) {
                clearInterval(this._typeTimer);
                this._typeTimer = null;
            }
            element.textContent = '';
            let i = 0;
            this._typeTimer = setInterval(() => {
                // 如果任务被取消，立即停止
                if (this._typeAbort.signal.aborted) {
                    clearInterval(this._typeTimer);
                    this._typeTimer = null;
                    reject(new Error('Typing cancelled'));
                    return;
                }
                
                if (i < text.length) {
                    element.textContent += text[i++];
                } else {
                    clearInterval(this._typeTimer);
                    this._typeTimer = null;
                    resolve();
                }
            }, 80);
        });
    }
    clearTypeTimer() {
        if (this._typeTimer !== null) {
            clearInterval(this._typeTimer);
            this._typeTimer = null;
        }
        // 取消打字任务（防止引用泄漏）
        if (this._typeAbort) {
            this._typeAbort.abort();
            this._typeAbort = null;
        }
    }
    updateFinalInfo() {
        const t = window.t || ((_key, fallback) => fallback);
        const descEl = document.getElementById('confirm-desc');
        const titleEl = document.querySelector('.confirm-title');
        if (!this.selectedCharacter) return;
        const charId = this.selectedCharacter.id;
        // 按角色取 readyTitle，回退到通用键
        if (titleEl) {
            titleEl.textContent = t(
                `memory.characterSelection.${charId}.readyTitle`,
                t('memory.characterSelection.readyTitle', '她来啦~')
            );
        }
        // 按角色取 readyDesc，回退到通用键
        if (descEl) {
            descEl.textContent = t(
                `memory.characterSelection.${charId}.readyDesc`,
                t('memory.characterSelection.readyDesc', '快去和她打招呼吧~')
            );
        }
    }
    async finalizeSelection() {
        console.log('[CharacterSelection] 用户确认选择:', this.selectedCharacter);
        if (this.selectedCharacter) {
            const success = await this.updateDefaultCatgirl();
            if (success) {
                // 仅在更新成功时写入完成标记
                localStorage.setItem('neko_character_selection_completed', 'true');
                console.log('[CharacterSelection] 角色甄选已完成并保存');
            } else {
                // 更新失败，允许重试，不关闭 overlay
                return;
            }
        }
        this.close();
    }
    skip() {
        console.log('[CharacterSelection] 用户跳过角色甄选');
        // 跳过时立即写入完成标记
        localStorage.setItem('neko_character_selection_completed', 'true');
        this.close();
    }
    async updateDefaultCatgirl() {
        const voiceMapping = CHARACTER_VOICE_MAPPING[this.selectedCharacter.id];
        if (!voiceMapping) {
            console.warn('[CharacterSelection] 找不到角色音色映射:', this.selectedCharacter.id);
            return false;
        }
        try {
            // 1. 获取当前角色列表（请求规范数据而非本地化数据）
            console.log('[CharacterSelection] 获取角色列表...');
            const getResponse = await fetch('/api/characters?language=zh-CN');
            if (!getResponse.ok) {
                throw new Error('获取角色列表失败');
            }
            const characters = await getResponse.json();
            const catgirlCategory = characters['猫娘'] || {};
            // 2. 确定目标角色：优先使用 localStorage 记录的名称
            let targetName = localStorage.getItem(CATGIRL_SELECTION_STORAGE_KEY);
            let targetData = targetName ? catgirlCategory[targetName] : null;
            if (!targetData) {
                // 记录的角色不存在（已被删除）或尚无记录，回落到默认名称
                if (catgirlCategory[DEFAULT_CATGIRL_NAME]) {
                    targetName = DEFAULT_CATGIRL_NAME;
                    targetData = catgirlCategory[DEFAULT_CATGIRL_NAME];
                } else {
                    // 默认角色也不存在，新建一个
                    console.log('[CharacterSelection] 默认猫娘不存在，正在新建...');
                    const createRes = await fetch('/api/characters/catgirl', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ '档案名': DEFAULT_CATGIRL_NAME })
                    });
                    if (!createRes.ok) {
                        throw new Error('创建默认猫娘失败');
                    }
                    targetName = DEFAULT_CATGIRL_NAME;
                    targetData = {};
                    console.log('[CharacterSelection] 默认猫娘创建成功');
                }
                // 记录目标角色名，供后续重命名时同步
                localStorage.setItem(CATGIRL_SELECTION_STORAGE_KEY, targetName);
            }
            // 3. 计算新性格（区分人设选择写入 vs 用户自定义）
            const knownPersonalities = Object.values(CHARACTER_VOICE_MAPPING).map(m => m.personality);
            const parts = targetData['性格'] ? targetData['性格'].split(/[，,、]/) : [];
            const existingIdx = parts.findIndex(p => knownPersonalities.includes(p.trim()));
            let personality;
            if (existingIdx !== -1) {
                // 人设选择曾写入过性格，直接覆盖
                parts[existingIdx] = voiceMapping.personality;
                personality = parts.join('，');
            } else if (!parts.includes(voiceMapping.personality)) {
                // 纯用户自定义性格，追加到末尾
                personality = parts.length > 0 ? `${targetData['性格']}，${voiceMapping.personality}` : voiceMapping.personality;
            } else {
                personality = targetData['性格'];
            }
            // 4. 更新角色设定（包含性格和音色）
            console.log('[CharacterSelection] 更新角色设定...');
            const updateData = {
                ...targetData,
                '性格': personality,
                voice_id: voiceMapping.voiceId
            };
            console.log('[CharacterSelection] 更新数据:', { 性格: personality, voice_id: voiceMapping.voiceId });
            const updateResponse = await fetch(`/api/characters/catgirl/${encodeURIComponent(targetName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(updateData)
            });
            if (!updateResponse.ok) {
                throw new Error('更新角色设定失败');
            }
            console.log('[CharacterSelection] 默认猫娘配置完成');
            return true;
        } catch (error) {
            console.error('[CharacterSelection] 更新默认猫娘失败:', error);
            return false;
        }
    }
    close() {
        if (!this.isOpen) return;
        this.isOpen = false;
        // 停止背景音乐
        this.stopBgm();
        // 清理挂起的定时器
        if (this._selectTimer !== null) {
            clearTimeout(this._selectTimer);
            this._selectTimer = null;
        }
        if (this._closeTimer !== null) {
            clearTimeout(this._closeTimer);
            this._closeTimer = null;
        }
        // 清理星星生成定时器
        if (this._starIntervalId !== null) {
            clearInterval(this._starIntervalId);
            this._starIntervalId = null;
        }
        // 清除 mouseup 监听器（防止监听器堆积）
        document.removeEventListener('mouseup', this._handleMouseUp);
        // 清理 stage 上的 mousedown/mousemove 监听器（防止内存泄漏）
        this._stageMouseDownHandlers.forEach((handler, stage) => {
            stage.removeEventListener('mousedown', handler);
        });
        this._stageMouseMoveHandlers.forEach((handler, stage) => {
            stage.removeEventListener('mousemove', handler);
        });
        this._stageMouseDownHandlers.clear();
        this._stageMouseMoveHandlers.clear();
        // 清除打字定时器
        this.clearTypeTimer();
        // 移除 localechange 监听
        window.removeEventListener('localechange', this._onLocaleChange);
        if (this.overlay) {
            // 添加淡出效果
            this.overlay.classList.add('fade-out');
            // 等待动画完成后完全移除
            this._closeTimer = setTimeout(() => {
                this._closeTimer = null;
                if (this.overlay) {
                    this.overlay.remove();
                    this.overlay = null;
                }
                console.log('[CharacterSelection] Overlay 已移除，进入主页');
            }, 300);
        }
    }
}
// 导出到全局
window.CharacterSelection = CharacterSelection;
console.log('[CharacterSelection] 角色甄选脚本已加载');
