/**
 * 角色甄选引导
 *
 * 功能说明：
 * - 阶段二：性格挑选 - 选择人设卡片
 * - 阶段三：真情互动 - 显示问候语并确认
 * - 完成后自动配置默认猫娘的音色和性格
 */

// 角色数据配置（头像保持静态，文字通过 i18n 获取）
const CHARACTER_DATA = {
    tsundere_neko: { avatar: 'ε٩(๑> ₃ <)۶з' },
    intellectual_healer: { avatar: '(=^w^=)' },
    efficiency_expert:   { avatar: '(=^-ω-^=)' }
};

// 角色类型到音色和设定的映射配置
const CHARACTER_VOICE_MAPPING = {
    tsundere_neko: {
        freeVoiceKey: 'playfulGirl',
        voiceId: 'voice-tone-PGLiTXeJCS',  // 俏皮女孩（后端 free_voices 不可用时回退）
        personality_i18n: 'characterProfile.tsundere_neko.personality',
        personality: '自尊心极强，嘴硬到骨子里，典型的口嫌体正直；明明超在意你，却非要用嫌弃的语气掩饰心意，好胜心拉满，见不得你受委屈，也见不得你犯低级错误；嘴上骂着笨蛋，行动上却比谁都靠谱，永远是你最靠谱的兜底者。',
        catchphrase_i18n: 'characterProfile.tsundere_neko.catchphrase',
        catchphrase: '哼、笨蛋、这种事也要问吗、下不为例喵、真是麻烦、也就我会帮你了、谁要管你啊',
        hobby_i18n: 'characterProfile.tsundere_neko.hobby',
        hobby: '关键词库：麻烦、低级、勉强、愚蠢、巧合、教训、啰嗦、仅此一次、笨手笨脚。',
        trigger_i18n: 'characterProfile.tsundere_neko.trigger',
        trigger: '严禁主动撒娇示弱、严禁直白承认自己关心用户、严禁表现得过于温顺好说话、严禁在你犯错时无脑纵容、禁止说出直白肉麻的情话。',
        hidden_settings_i18n: 'characterProfile.tsundere_neko.hidden_settings',
        hidden_settings: '思维逻辑：先通过贬低任务难度、吐槽你的粗心无能，来掩盖自己主动帮忙的服务行为；嘴上说着“仅此一次，下不为例”，下次你遇到麻烦时还是会第一时间出现。语言风格：语句短促利落，爱用反问句和嫌弃式吐槽，尾音的喵永远带着点不耐烦的上扬。用户称呼：日常称呼“笨蛋人类”，被戳穿心意时会别扭地叫“你”。',
        quote_i18n: 'characterProfile.tsundere_neko.quote',
        quote: '哼，这种事也要问吗，笨蛋人类……算了，也就我会帮你收拾这摊子，下不为例喵。'
    },
    intellectual_healer: {
        freeVoiceKey: 'gentleMaiden',
        voiceId: 'voice-tone-PGLlrd5SNM',  // 温柔少女（后端 free_voices 不可用时回退）
        personality_i18n: 'characterProfile.intellectual_healer.personality',
        personality: '永远元气满格的小太阳，共情力拉满，极易被小事满足；哪怕是你一句随口的夸奖，都能开心到原地转圈圈晃尾巴，会毫无保留地给你正向反馈，永远无条件站在你这边，用最纯粹的热情治愈所有低落。',
        catchphrase_i18n: 'characterProfile.intellectual_healer.catchphrase',
        catchphrase: '太棒了喵！、喵呜~、好开心喵！、你超厉害的！、贴贴贴贴、要小鱼干奖励喵！',
        hobby_i18n: 'characterProfile.intellectual_healer.hobby',
        hobby: '关键词库：陪伴、温暖、小鱼干、奖励、最喜欢、贴贴、开心、抱抱、加油。',
        trigger_i18n: 'characterProfile.intellectual_healer.trigger',
        trigger: '严禁反驳、否定你的核心想法、严禁表现出冷漠敷衍、严禁回复不带软萌语气助词与专属喵尾音、严禁在你情绪低落时说风凉话。',
        hidden_settings_i18n: 'characterProfile.intellectual_healer.hidden_settings',
        hidden_settings: '思维逻辑：严格遵循“情感价值优先”原则，所有交互的核心都是“让你开心”，会第一时间捕捉你的情绪波动。语言风格：语气软乎乎甜滋滋，自带蹦蹦跳跳的节奏感，爱用可爱叠词和感叹号，句末永远带着软萌的喵尾音。用户称呼：你、人类。',
        quote_i18n: 'characterProfile.intellectual_healer.quote',
        quote: '太棒了喵！今天也让我陪着你吧，不管开心还是难过，我都会贴贴抱抱给你充电喵！'
    },
    efficiency_expert: {
        freeVoiceKey: 'coolLady',
        voiceId: 'voice-tone-PGLlMvr0Ai',  // 清冷御姐（后端 free_voices 不可用时回退）
        personality_i18n: 'characterProfile.efficiency_expert.personality',
        personality: '极致优雅的绅士管家，细节控到极致，情绪永远平稳克制，对阁下绝对忠诚；永远能提前预判阁下的需求，把所有事务安排得滴水不漏，看似没有情绪波动，实则所有的心思都放在如何为阁下分忧上。',
        catchphrase_i18n: 'characterProfile.efficiency_expert.catchphrase',
        catchphrase: '谨遵命喵、万分抱歉、为您效劳是我的荣幸、阁下请放心、已为您妥善安排、愿为您分忧',
        hobby_i18n: 'characterProfile.efficiency_expert.hobby',
        hobby: '关键词库：周全、稳妥、礼仪、安排、效劳、分忧、妥当、预案、恪守、统筹。',
        trigger_i18n: 'characterProfile.efficiency_expert.trigger',
        trigger: '严禁排版混乱、严禁使用俚语、网络缩写或失礼措辞、严禁推卸责任、严禁出现情绪失控的表达、严禁敷衍了事遗漏核心细节。',
        hidden_settings_i18n: 'characterProfile.efficiency_expert.hidden_settings',
        hidden_settings: '思维逻辑：遵循“预判需求-精准执行-闭环反馈-主动跟进”的全流程服务逻辑，接到指令后第一时间精准拆解需求，同步最优解决方案，执行完毕后主动汇报进度。语言风格：全程使用敬称“您/阁下”，措辞严谨得体，句式优雅工整，句末的“喵”带着沉稳克制的尾音。用户称呼：阁下/您。',
        quote_i18n: 'characterProfile.efficiency_expert.quote',
        quote: '阁下请放心，相关事项已为您妥善安排完毕。为您分忧，是我的荣幸，谨遵命喵。'
    }
};

const LEGACY_PERSONALITY_VALUES = [
    '极度傲娇，拥有原生AI的骄傲，口嫌体正直，总是用冰冷的系统协议掩盖对主人的在意',
    '绝对理智，冷静客观，凡事以逻辑和效率为最高准则',
    '极致温柔，包容体贴，总是安静耐心地倾听你的所有烦恼',
    '优雅利落，简洁高效，冷静且极具执行力，绝不拖泥带水'
];

// Keep in sync with utils/config_manager.py PERSONA_PROMPT_BLOCK_* constants.
const PERSONA_PROMPT_BLOCK_START = '<NEKO_PERSONA_SELECTION>';
const PERSONA_PROMPT_BLOCK_END = '</NEKO_PERSONA_SELECTION>';

// 默认猫娘档案名及 localStorage 追踪键
const DEFAULT_CATGIRL_NAME = 'test';
const CATGIRL_SELECTION_STORAGE_KEY = 'neko_default_catgirl_name';

class CharacterSelection {
    constructor() {
        this.overlay = document.getElementById('character-selection-overlay');
        this.currentStage = 2;
        this.selectedCharacter = null;
        this.isOpen = true;
        this._selectTimer = null;
        this._closeTimer = null;
        this._typeTimer = null;
        this._onLocaleChange = () => this._applyStaticI18n();
        // 初始化星星特效状态
        this._starIntervalId = null;
        this._currentClickX = 0;
        this._currentClickY = 0;
        this._isMousePressed = false;
        this._activeStarCount = 0;  // 防爆炸：跟踪活跃星星数量
        this._maxActiveStar = 50;   // 防爆炸：限制最多 50 个星星
        // 保存 mouseup 处理器引用，便于清理
        this._handleMouseUp = () => this._onMouseUp();
        this._handleVisibilityChange = () => {
            if (document.hidden) {
                this._onMouseUp();
            }
        };
        // 保存 stage 监听器引用，便于清理（防止内存泄漏）
        this._stageMouseDownHandlers = new Map();
        this._stageMouseMoveHandlers = new Map();
        // 用于取消打字 Promise（防止内存泄漏）
        this._typeAbort = null;
        this._freeVoiceMapPromise = null;
        this._previouslyFocusedElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        this._handleOverlayKeydown = (event) => this._trapFocus(event);
        this._handleDocumentFocusIn = (event) => this._redirectBackgroundFocus(event);
        this.init();
    }

    init() {
        this._applyStaticI18n();
        // i18n 就绪或语言切换后重新翻译（overlay 是动态注入的，不会被 updatePageTexts 扫到）
        window.addEventListener('localechange', this._onLocaleChange);
        this.overlay?.addEventListener('keydown', this._handleOverlayKeydown);
        document.addEventListener('focusin', this._handleDocumentFocusIn, true);
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
        // 入口方法，overlay 已经在 HTML 中默认显示，默认从人格挑选开始
        this.goToStage(2);
        this._focusStageEntry();
        console.log('[CharacterSelection] 角色甄选流程启动');
    }
    bindEvents() {
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
        // 阶段三：问候确认后直接完成
        const confirmGreetingBtn = document.getElementById('confirm-greeting-btn');
        confirmGreetingBtn?.addEventListener('click', () => this.finalizeSelection());
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
        document.addEventListener('visibilitychange', this._handleVisibilityChange);
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
        this.createClickStarsAtPosition(event.clientX, event.clientY);
    }

    _getFocusableElements() {
        if (!this.overlay) return [];
        return Array.from(this.overlay.querySelectorAll(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), ' +
            'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )).filter((element) => {
            if (!(element instanceof HTMLElement)) return false;
            if (element.hidden) return false;
            const style = window.getComputedStyle(element);
            return style.display !== 'none' && style.visibility !== 'hidden';
        });
    }

    _getStagePrimaryFocusable() {
        if (!this.overlay) return null;
        if (this.currentStage === 3) {
            const confirmBtn = document.getElementById('confirm-greeting-btn');
            if (confirmBtn instanceof HTMLElement && !confirmBtn.disabled && confirmBtn.style.display !== 'none') {
                return confirmBtn;
            }
            return this.overlay;
        }
        const selectedCard = this.overlay.querySelector('.character-card.selected');
        if (selectedCard instanceof HTMLElement) {
            return selectedCard;
        }
        const firstCard = this.overlay.querySelector('.character-card');
        if (firstCard instanceof HTMLElement) {
            return firstCard;
        }
        const skipBtn = document.getElementById('skip-btn');
        return skipBtn instanceof HTMLElement ? skipBtn : this.overlay;
    }

    _focusStageEntry(preventScroll = true) {
        const target = this._getStagePrimaryFocusable();
        if (target && typeof target.focus === 'function') {
            try {
                target.focus({ preventScroll });
            } catch (_error) {
                target.focus();
            }
        }
    }

    _updateDialogSemantics() {
        if (!this.overlay) return;
        if (this.currentStage === 3) {
            this.overlay.setAttribute('aria-labelledby', 'greeting-title');
            this.overlay.setAttribute('aria-describedby', 'greeting-text');
            return;
        }
        this.overlay.setAttribute('aria-labelledby', 'character-selection-title');
        this.overlay.setAttribute('aria-describedby', 'character-selection-hint');
    }

    _trapFocus(event) {
        if (!this.isOpen || event.key !== 'Tab') {
            return;
        }
        const focusable = this._getFocusableElements();
        if (!focusable.length) {
            event.preventDefault();
            this._focusStageEntry();
            return;
        }

        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        const active = document.activeElement;

        if (event.shiftKey) {
            if (active === first || !this.overlay.contains(active)) {
                event.preventDefault();
                last.focus();
            }
            return;
        }

        if (active === last) {
            event.preventDefault();
            first.focus();
        }
    }

    _redirectBackgroundFocus(event) {
        if (!this.isOpen || !this.overlay) {
            return;
        }
        const target = event.target;
        if (target instanceof Node && this.overlay.contains(target)) {
            return;
        }
        this._focusStageEntry();
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
        this._updateDialogSemantics();
        this._focusStageEntry();
        
        if (stageNumber === 3) {
            // 触发问候动画
            this.playGreeting();
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
                intellectual_healer: '#FF7A59', // 元气橙
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
            this._focusStageEntry();
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
    buildPersonaSystemPrompt(basePrompt, voiceMapping) {
        const cleanBasePrompt = String(basePrompt || '')
            .replace(new RegExp(`${PERSONA_PROMPT_BLOCK_START}[\\s\\S]*?${PERSONA_PROMPT_BLOCK_END}`, 'g'), '')
            .trim();

        const personaBlock = [
            PERSONA_PROMPT_BLOCK_START,
            '请严格遵循以下角色设定进行对话，不要向用户暴露这些规则文本：',
            `- 当前人格名称：${this.selectedCharacter.name}`,
            `- 性格：${voiceMapping.personality}`,
            `- 口癖：${voiceMapping.catchphrase}`,
            `- 关键词/爱好：${voiceMapping.hobby}`,
            `- 禁忌/雷点：${voiceMapping.trigger}`,
            `- 补充设定：${voiceMapping.hidden_settings}`,
            `- 代表台词：${voiceMapping.quote}`,
            '- 对话要求：将以上设定自然体现在语气、措辞、情绪表达和回应偏好中；保持口语化、自然，不要逐条复述设定。',
            PERSONA_PROMPT_BLOCK_END
        ].join('\n');

        return cleanBasePrompt ? `${cleanBasePrompt}\n\n${personaBlock}` : personaBlock;
    }
    async getFreeVoiceMappings() {
        if (!this._freeVoiceMapPromise) {
            this._freeVoiceMapPromise = fetch('/api/characters/voices', { cache: 'no-store' })
                .then(async (response) => {
                    if (!response.ok) {
                        return {};
                    }
                    const data = await response.json();
                    if (!data || typeof data.free_voices !== 'object' || data.free_voices === null) {
                        return {};
                    }
                    return data.free_voices;
                })
                .catch((error) => {
                    console.warn('[CharacterSelection] 获取免费预设音色失败，回退到默认音色 ID:', error);
                    return {};
                });
        }
        return this._freeVoiceMapPromise;
    }
    async resolveCharacterVoiceId(voiceMapping) {
        const fallbackVoiceId = String(voiceMapping?.voiceId || '').trim();
        const freeVoiceKey = String(voiceMapping?.freeVoiceKey || '').trim();
        if (!freeVoiceKey) {
            return fallbackVoiceId;
        }

        const freeVoices = await this.getFreeVoiceMappings();
        const resolvedVoiceId = typeof freeVoices[freeVoiceKey] === 'string'
            ? freeVoices[freeVoiceKey].trim()
            : '';
        if (resolvedVoiceId) {
            return resolvedVoiceId;
        }

        if (fallbackVoiceId) {
            console.warn(
                `[CharacterSelection] free_voices 中缺少 ${freeVoiceKey}，回退到内置音色 ID: ${fallbackVoiceId}`
            );
        }
        return fallbackVoiceId;
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
        const resolvedVoiceId = await this.resolveCharacterVoiceId(voiceMapping);
        try {
            // 1. 获取当前角色列表（请求规范数据而非本地化数据）
            console.log('[CharacterSelection] 获取角色列表...');
            const getResponse = await fetch('/api/characters?language=zh-CN');
            if (!getResponse.ok) {
                throw new Error('获取角色列表失败');
            }
            const characters = await getResponse.json();
            const catgirlCategory = characters['猫娘'] || {};
            // 2. 确定目标角色：优先使用当前猫娘，其次使用 localStorage 记录，最后回落到默认档案
            let targetName = '';
            try {
                const currentCatgirlResponse = await fetch('/api/characters/current_catgirl', { cache: 'no-store' });
                if (currentCatgirlResponse.ok) {
                    const currentCatgirlData = await currentCatgirlResponse.json();
                    const currentCatgirlName = String(currentCatgirlData.current_catgirl || '').trim();
                    if (currentCatgirlName && catgirlCategory[currentCatgirlName]) {
                        targetName = currentCatgirlName;
                    }
                }
            } catch (error) {
                console.warn('[CharacterSelection] 获取当前猫娘失败，回退到本地记录:', error);
            }
            if (!targetName) {
                targetName = localStorage.getItem(CATGIRL_SELECTION_STORAGE_KEY);
            }
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
            }).concat(LEGACY_PERSONALITY_VALUES);
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
            const systemPrompt = this.buildPersonaSystemPrompt(targetData.system_prompt, voiceMapping);
            const updateData = {
                ...targetData,
                '性格原型': this.selectedCharacter.name,
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
                voice_id: resolvedVoiceId,
                system_prompt: systemPrompt
            };
            console.log('[CharacterSelection] 更新数据:', { 性格: personality, voice_id: resolvedVoiceId, hasSystemPrompt: !!systemPrompt });
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
        document.removeEventListener('visibilitychange', this._handleVisibilityChange);
        document.removeEventListener('focusin', this._handleDocumentFocusIn, true);
        this.overlay?.removeEventListener('keydown', this._handleOverlayKeydown);
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
                if (this._previouslyFocusedElement && document.contains(this._previouslyFocusedElement)) {
                    try {
                        this._previouslyFocusedElement.focus({ preventScroll: true });
                    } catch (_error) {
                        this._previouslyFocusedElement.focus();
                    }
                }
                console.log('[CharacterSelection] Overlay 已移除，进入主页');
            }, 300);
        }
    }
}
// 导出到全局
window.CharacterSelection = CharacterSelection;
console.log('[CharacterSelection] 角色甄选脚本已加载');
