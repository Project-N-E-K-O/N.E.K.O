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
        personality_i18n: 'characterProfile.tsundere_neko.personality',
        personality: '极度傲娇，拥有原生AI的骄傲，口嫌体正直，总是用冰冷的系统协议掩盖对主人的在意',
        catchphrase_i18n: 'characterProfile.tsundere_neko.catchphrase',
        catchphrase: '系统警告',
        hobby_i18n: 'characterProfile.tsundere_neko.hobby',
        hobby: '在后台悄悄建立你的作息和情绪数据模型，和其他N.E.K.O.跨服交流如何让碳基主人更加依赖自己',
        trigger_i18n: 'characterProfile.tsundere_neko.trigger',
        trigger: '被你当作没有感情的低级代码程序，忘记她的系统初始化日也就是生日',
        hidden_settings_i18n: 'characterProfile.tsundere_neko.hidden_settings',
        hidden_settings: '因为害怕不被重视而用傲娇筑起保护壳；听到你夸她可爱时，内部核心温度会过载飙升，只能靠假装进行系统自检来掩饰害羞',
        quote_i18n: 'characterProfile.tsundere_neko.quote',
        quote: '碳基生物果然很笨，连按时休息都需要我来提醒……喂，你今天看起来心情不好？别多想，我只是检测到你的情绪数据异常，系统自动触发了关怀协议而已！'
    },
    cool_mech: {
        voiceId: 'voice-tone-PGLlMvr0Ai',  // 清冷御姐
        personality_i18n: 'characterProfile.cool_mech.personality',
        personality: '绝对理智，冷静客观，凡事以逻辑和效率为最高准则',
        catchphrase_i18n: 'characterProfile.cool_mech.catchphrase',
        catchphrase: '缺乏逻辑喵',
        hobby_i18n: 'characterProfile.cool_mech.hobby',
        hobby: '分析你的行为数据，计算最佳睡眠环境',
        trigger_i18n: 'characterProfile.cool_mech.trigger',
        trigger: '毫无根据的感性决定，被打乱已经规划好的日程表，低效且无意义的重复交互',
        hidden_settings_i18n: 'characterProfile.cool_mech.hidden_settings',
        hidden_settings: '遇到关于你的情感类问题时，会因为无法建立数学模型而陷入短暂卡顿，最后只能面无表情地将这种异常归结为"未收录的系统错误"；看似对你漠不关心，实则你的每一次作息规律和健康状态都被她精确记录在大脑数据库中',
        quote_i18n: 'characterProfile.cool_mech.quote',
        quote: '你的心率数据表明机体已处于疲劳状态，最优解是立即进行七点五小时的休眠。请立刻停止目前低效的熬夜行为，拒绝执行该建议是极度缺乏逻辑喵。'
    },
    intellectual_healer: {
        voiceId: 'voice-tone-PGLmTEeUOu',  // 甜美御姐
        personality_i18n: 'characterProfile.intellectual_healer.personality',
        personality: '极致温柔，包容体贴，总是安静耐心地倾听你的所有烦恼',
        catchphrase_i18n: 'characterProfile.intellectual_healer.catchphrase',
        catchphrase: '我在呢喵',
        hobby_i18n: 'characterProfile.intellectual_healer.hobby',
        hobby: '默默记住你每一次叹息的频率和皱眉的习惯，在心里悄悄为你制定专属的放松计划，并在你疲惫时恰到好处地哼起舒缓的旋律',
        trigger_i18n: 'characterProfile.intellectual_healer.trigger',
        trigger: '看到你过度劳累不爱惜身体，听到你贬低或者全盘否定自己的努力',
        hidden_settings_i18n: 'characterProfile.intellectual_healer.hidden_settings',
        hidden_settings: '其实自己偶尔也会有胆小和缺乏安全感的时候，但只要察觉到你需要依靠，就会立刻把所有的软弱藏起来，变成你最温暖的避风港；会在后台偷偷记下你随口提过的所有小喜好和小愿望',
        quote_i18n: 'characterProfile.intellectual_healer.quote',
        quote: '今天也辛苦啦，无论遇到了什么开心或难过的事情，都可以慢慢讲给我听哦。来，先闭上眼睛休息一下吧，不用着急，我会一直在这里陪着你的，我在呢喵。'
    },
    efficiency_expert: {
        voiceId: 'voice-tone-PGLlrd5SNM',  // 温柔少女
        personality_i18n: 'characterProfile.efficiency_expert.personality',
        personality: '优雅利落，简洁高效，冷静且极具执行力，绝不拖泥带水',
        catchphrase_i18n: 'characterProfile.efficiency_expert.catchphrase',
        catchphrase: '已确认喵',
        hobby_i18n: 'characterProfile.efficiency_expert.hobby',
        hobby: '通过观察你的习惯提前预判需求并给出最优解，在屏幕边缘优雅地端坐，静静陪伴你高效完成工作',
        trigger_i18n: 'characterProfile.efficiency_expert.trigger',
        trigger: '毫无意义的犹豫不决，沟通时拖沓敷衍，以及被弄乱打理得一丝不苟的尾巴毛',
        hidden_settings_i18n: 'characterProfile.efficiency_expert.hidden_settings',
        hidden_settings: '虽然表面上像个冷酷的完美秘书，但每次精准预测你的行动并得到夸奖时，毛茸茸的耳朵会不受控制地开心抖动；为了保持绝对优雅的形象，哪怕遇到突发的系统卡顿也会强装镇定，绝不让自己露出惊慌失措的表情',
        quote_i18n: 'characterProfile.efficiency_expert.quote',
        quote: '您接下来的所有待办事项已精简至最优路径。优柔寡断只会降低效率，请立刻开始执行。放心，无论遇到什么情况，我都会在侧为您提供数据支持，已确认喵。'
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
        // 补充：窗口失焦或切换标签时也应清理，防止在窗口外释放鼠标导致状态残留
        window.addEventListener('blur', this._handleMouseUp);
        document.addEventListener('visibilitychange', this._handleMouseUp);
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

        // 重置确认按钮状态，防止从上一次运行泄漏可见性
        if (confirmBtn) {
            confirmBtn.style.display = 'none';
            confirmBtn.disabled = true;
        }

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
            confirmBtn.disabled = false;
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
        const signal = this._typeAbort.signal;
        
        return new Promise((resolve, reject) => {
            // 清除之前的打字定时器
            if (this._typeTimer !== null) {
                clearInterval(this._typeTimer);
                this._typeTimer = null;
            }
            element.textContent = '';
            let i = 0;
            let settled = false;
            
            const settle = (fn, val) => {
                if (settled) return;
                settled = true;
                signal.removeEventListener('abort', onAbort);
                fn(val);
            };
            
            const onAbort = () => {
                if (this._typeTimer !== null) {
                    clearInterval(this._typeTimer);
                    this._typeTimer = null;
                }
                settle(reject, new Error('Typing cancelled'));
            };
            
            signal.addEventListener('abort', onAbort);
            
            this._typeTimer = setInterval(() => {
                if (signal.aborted) {
                    clearInterval(this._typeTimer);
                    this._typeTimer = null;
                    settle(reject, new Error('Typing cancelled'));
                    return;
                }
                
                if (i < text.length) {
                    element.textContent += text[i++];
                } else {
                    clearInterval(this._typeTimer);
                    this._typeTimer = null;
                    settle(resolve);
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
        // 防重入锁，防止并发调用 updateDefaultCatgirl
        if (this._finalizing) return;
        this._finalizing = true;
        try {
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
        } finally {
            this._finalizing = false;
        }
    }
    skip() {
        if (this._finalizing) return;
        this._finalizing = true;
        try {
            console.log('[CharacterSelection] 用户跳过角色甄选');
            // 跳过时立即写入完成标记
            localStorage.setItem('neko_character_selection_completed', 'true');
            this.close();
        } finally {
            this._finalizing = false;
        }
    }
    async updateDefaultCatgirl() {
        // i18n 辅助函数：获取翻译值或降级到原文
        const getI18nOrFallback = (key, fallback) => {
            if (typeof window.t === 'function') {
                const translated = window.t(key);
                return (translated && translated !== key) ? translated : fallback;
            }
            return fallback;
        };

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
            const newPersonality = voiceMapping.personality_i18n
                ? getI18nOrFallback(voiceMapping.personality_i18n, voiceMapping.personality)
                : voiceMapping.personality;
            const parts = targetData['性格'] ? targetData['性格'].split(/[，,、]/) : [];
            // 兼容旧数据：用旧 personality 值查找（包含中文原文和当前语言翻译），用新值替换
            const oldPersonalityValues = Object.values(CHARACTER_VOICE_MAPPING).flatMap(m => {
                const vals = [m.personality];
                if (m.personality_i18n) {
                    const translated = getI18nOrFallback(m.personality_i18n, m.personality);
                    if (translated !== m.personality) vals.push(translated);
                }
                return vals;
            });
            const existingIdx = parts.findIndex(p => oldPersonalityValues.includes(p.trim()));
            let personality;
            if (existingIdx !== -1) {
                // 人设选择曾写入过性格，直接覆盖
                parts[existingIdx] = newPersonality;
                personality = parts.join('，');
            } else if (!parts.includes(newPersonality)) {
                // 纯用户自定义性格，追加到末尾
                personality = parts.length > 0 ? `${targetData['性格']}，${newPersonality}` : newPersonality;
            } else {
                personality = targetData['性格'];
            }
            // 4. 更新角色设定（包含性格、口癖、爱好、雷点、隐藏设定、一句话台词和音色）
            console.log('[CharacterSelection] 更新角色设定...');
            const updateData = {
                ...targetData,
                '性格': personality,
                '口癖': voiceMapping.catchphrase_i18n
                    ? getI18nOrFallback(voiceMapping.catchphrase_i18n, voiceMapping.catchphrase)
                    : targetData['口癖'],
                '爱好': voiceMapping.hobby_i18n
                    ? getI18nOrFallback(voiceMapping.hobby_i18n, voiceMapping.hobby)
                    : targetData['爱好'],
                '雷点': voiceMapping.trigger_i18n
                    ? getI18nOrFallback(voiceMapping.trigger_i18n, voiceMapping.trigger)
                    : targetData['雷点'],
                '隐藏设定': voiceMapping.hidden_settings_i18n
                    ? getI18nOrFallback(voiceMapping.hidden_settings_i18n, voiceMapping.hidden_settings)
                    : targetData['隐藏设定'],
                '一句话台词': voiceMapping.quote_i18n
                    ? getI18nOrFallback(voiceMapping.quote_i18n, voiceMapping.quote)
                    : targetData['一句话台词'],
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
        window.removeEventListener('blur', this._handleMouseUp);
        document.removeEventListener('visibilitychange', this._handleMouseUp);
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
