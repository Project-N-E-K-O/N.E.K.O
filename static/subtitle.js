// 字幕（常驻字幕 + 按需翻译）
//
// 工作流程
// ──────────
//   1. 一个 AI 回合（turn）= 一段连续讲话，可能被切成多个聊天气泡。
//   2. 字幕跨气泡持久显示，不会被新气泡清空。
//   3. turn 进行中：流式原文实时写入字幕（updateSubtitleStreamingText）。
//   4. turn 结束：调用 /api/translate；如果需要翻译则用译文替换原文，
//      否则保留原文。
//   5. 下一个 turn-start 才清空字幕，开始下一段。
//
// 翻译开关由 React 聊天窗口的 composer 按钮控制，状态走 window.subtitleBridge。
// 旧的字幕提示气泡（subtitle-prompt-message）已下线，相关 prompt/detect 代码全部移除。

// 归一化语言代码：将 BCP-47 格式（如 'zh-CN', 'en-US'）归一化为简单代码（'zh', 'en', 'ja', 'ko', 'ru'）
function normalizeLanguageCode(lang) {
    if (!lang) return 'zh'; // 默认中文
    const langLower = lang.toLowerCase();
    if (langLower.startsWith('zh')) {
        return 'zh';
    } else if (langLower.startsWith('ja')) {
        return 'ja';
    } else if (langLower.startsWith('en')) {
        return 'en';
    } else if (langLower.startsWith('ko')) {
        return 'ko';
    } else if (langLower.startsWith('ru')) {
        return 'ru';
    }
    return 'zh'; // 默认中文
}

// 字幕开关状态（优先从 appState 读取，否则从 localStorage 读取）
let subtitleEnabled = (typeof window.appState !== 'undefined' && typeof window.appState.subtitleEnabled !== 'undefined')
    ? window.appState.subtitleEnabled
    : localStorage.getItem('subtitleEnabled') === 'true';

/**
 * 设置用户语言并同步到 appState
 */
function setUserLanguage(lang) {
    userLanguage = lang;
    localStorage.setItem('userLanguage', userLanguage);
    if (typeof window.appState !== 'undefined') {
        window.appState.userLanguage = userLanguage;
    }
    if (typeof window.appSettings !== 'undefined' && window.appSettings.saveSettings) {
        window.appSettings.saveSettings();
    }
}
// 用户语言（懒加载，避免使用 localStorage 旧值）
let userLanguage = null;
// 用户语言初始化 Promise（用于确保只初始化一次）
let userLanguageInitPromise = null;

// 获取用户语言（支持语言代码归一化，懒加载）
async function getUserLanguage() {
    if (userLanguage !== null) {
        return userLanguage;
    }
    if (userLanguageInitPromise) {
        return await userLanguageInitPromise;
    }
    userLanguageInitPromise = (async () => {
        try {
            const response = await fetch('/api/config/user_language');
            const data = await response.json();
            if (data.success && data.language) {
                setUserLanguage(normalizeLanguageCode(data.language));
                return userLanguage;
            }
        } catch (error) {
            console.warn('从API获取用户语言失败，尝试使用缓存或浏览器语言:', error);
        }
        const cachedLang = localStorage.getItem('userLanguage');
        if (cachedLang) {
            setUserLanguage(normalizeLanguageCode(cachedLang));
            return userLanguage;
        }
        const browserLang = navigator.language || navigator.userLanguage;
        setUserLanguage(normalizeLanguageCode(browserLang));
        return userLanguage;
    })();
    return await userLanguageInitPromise;
}

// 当前 turn 的原始（未翻译）累积文本，写在主线程上随时可读
let currentTurnOriginalText = '';
// 终态翻译请求的取消器
let currentTranslateAbortController = null;
// 单调递增的 turn id / request id，用于丢弃来自旧 turn 或旧请求的响应。
// 不要用原文做去重键：同一字幕在相邻 turn 有可能字面量相同。
let currentTurnId = 0;
let currentTranslationRequestId = 0;
// 当前 turn 是否已收到 turn-end（即 translateAndShowSubtitle 被调用过）。
// 用途：
//   1. toggle 开启时判断要不要对当前缓存发起翻译；流式途中不会标记 true。
//   2. 防止"早停的 turn_end 已 finalize → 延迟渲染的拟真 bubble / 迟到 chunk
//      又回来调 updateSubtitleStreamingText 把字幕刷回原文"的竞态（PR #778 修复）。
let isCurrentTurnFinalized = false;
// 当前 turn 是否判定为结构化富文本（markdown/code/table/latex 等）。
// 结构化 turn 的字幕显示 [markdown] 占位符，不做翻译也不回落原文。
let currentTurnIsStructured = false;
// 闸门：标记"本轮 turn 边界已在 isNewMessage 路径被提前复位过"。
// 背景：neko-assistant-turn-start 事件只在首个可见 bubble 创建后才派发，
// 但 appendMessage 处理首个 chunk 时就已经调了 updateSubtitleStreamingText。
// 没有这个闸门就会：
//   a) isNewMessage 路径先调 updateSubtitleStreamingText → 被上一轮残留的
//      isCurrentTurnFinalized=true 闸门吞掉，首个 chunk / 单 chunk 回复
//      完全不上屏，直到 turn_end。（Codex P2 / CodeRabbit Major）
//   b) 若仅在事件里复位，事件到达时已经过了首个 chunk，onAssistantTurnStart
//      会把刚写好的字幕再 writeSubtitleText('') 抹掉，产生闪烁。
// 解法：isNewMessage 入口立即 beginSubtitleTurn() 复位状态并拉高此闸门；
// 稍后到来的 neko-assistant-turn-start 事件看到闸门已拉高，仅同步显示可见性，
// 不再二次擦除 currentTurnOriginalText / 字幕文本。
let turnBoundaryLatched = false;

// 结构化/不可朗读内容的字幕占位符
function getStructuredPlaceholder() {
    try {
        if (typeof window.t === 'function') {
            const translated = window.t('subtitle.markdownPlaceholder');
            if (translated && translated !== 'subtitle.markdownPlaceholder') return translated;
        }
    } catch (e) { /* i18n 未就绪时静默回落 */ }
    return '[markdown]';
}

/**
 * 内部：把字幕显示元素切换到“可见”状态（如果开关开启）
 */
function ensureSubtitleVisibleIfEnabled() {
    const display = document.getElementById('subtitle-display');
    if (!display) return;
    if (subtitleEnabled) {
        display.classList.remove('hidden');
        display.classList.add('show');
        display.style.opacity = '1';
    }
}

/**
 * 把字幕显示元素隐藏并清空文字（开关关闭或手动 hideSubtitle 时使用）
 */
function hideSubtitle() {
    const display = document.getElementById('subtitle-display');
    if (!display) return;
    const subtitleText = document.getElementById('subtitle-text');
    if (subtitleText) subtitleText.textContent = '';
    display.classList.remove('show');
    display.classList.add('hidden');
    display.style.opacity = '0';
}

/**
 * 写入字幕文本（不影响显示/隐藏状态）
 */
function writeSubtitleText(text) {
    const subtitleText = document.getElementById('subtitle-text');
    if (subtitleText) subtitleText.textContent = text || '';
}

/**
 * 流式更新：本回合 AI 文本累积时调用。
 * 立即把原文显示到字幕里，跨多个气泡持续写入。
 * 仅在字幕开关开启时才上屏，但内部状态始终维护，方便用户中途打开开关时直接补显。
 *
 * 竞态保护（PR #778）：
 *   - 已 finalize（收到 turn_end，翻译已起）后，丢弃后续流式写入，避免
 *     拟真模式 2s/气泡延迟导致的"迟到 bubble 把已翻译字幕刷回原文"。
 *   - 已判定为结构化的 turn 不接受原文写入，继续维持 [markdown] 占位。
 */
function updateSubtitleStreamingText(text) {
    if (isCurrentTurnFinalized) return;
    if (currentTurnIsStructured) return;

    const cleaned = (text || '').toString();
    currentTurnOriginalText = cleaned;

    if (!subtitleEnabled) return;
    if (!cleaned.trim()) return;

    ensureSubtitleVisibleIfEnabled();
    writeSubtitleText(cleaned);
}

/**
 * 把当前 turn 切换成"结构化富文本"显示模式：字幕显示 [markdown] 占位符，
 * 后续的 updateSubtitleStreamingText 不再覆盖它，turn_end 也会跳过翻译。
 *
 * 场景：本回合文本里检测到 markdown/table/code block/latex 等不适合朗读的结构。
 * 由 app-chat.js / app-chat-adapter.js 在 looksLikeStructuredRichText 命中时调用。
 */
function markSubtitleStructured() {
    if (isCurrentTurnFinalized) return;
    if (currentTurnIsStructured) return; // 已是结构化，幂等
    currentTurnIsStructured = true;
    const placeholder = getStructuredPlaceholder();
    currentTurnOriginalText = placeholder;
    if (!subtitleEnabled) return;
    ensureSubtitleVisibleIfEnabled();
    writeSubtitleText(placeholder);
}

/**
 * turn_end 终态收尾（结构化版）：标记 finalize，只显示 [markdown] 占位，
 * 不发翻译请求。等价于 translateAndShowSubtitle 的结构化分支。
 */
function finalizeSubtitleAsStructured() {
    isCurrentTurnFinalized = true;
    currentTurnIsStructured = true;
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
        currentTranslateAbortController = null;
    }
    const placeholder = getStructuredPlaceholder();
    currentTurnOriginalText = placeholder;
    if (!subtitleEnabled) return;
    ensureSubtitleVisibleIfEnabled();
    writeSubtitleText(placeholder);
}

/**
 * 纯状态复位：bump turnId、清空累积文本与闸门、取消在途翻译。
 * 不动显示文本（调用方自行决定是否 writeSubtitleText('')）。
 */
function resetSubtitleTurnState() {
    currentTurnId += 1;
    currentTurnOriginalText = '';
    isCurrentTurnFinalized = false;
    currentTurnIsStructured = false;
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
        currentTranslateAbortController = null;
    }
}

/**
 * 供 app-chat.js / app-chat-adapter.js 在 isNewMessage 分支、首个
 * updateSubtitleStreamingText 调用之前先行触发的复位入口。
 *
 * 为什么不能只靠事件：
 *   neko-assistant-turn-start 事件要等 ensureAssistantTurnStarted 确认
 *   首个可见气泡创建后才会派发（app-websocket.js:385），比首个 chunk
 *   进入 appendMessage 晚一拍。如果只在事件里解锁，上一轮残留的
 *   isCurrentTurnFinalized=true 会把本轮首个 chunk / 单 chunk 回复的
 *   流式写入全部吞掉。
 */
function beginSubtitleTurn() {
    resetSubtitleTurnState();
    turnBoundaryLatched = true;
}

/**
 * 'neko-assistant-turn-start' 事件处理：
 *   - 如果 isNewMessage 路径已经 beginSubtitleTurn 过（闸门为真），只同步
 *     显示可见性，不再二次抹字幕 —— 否则会把首个 chunk 已经写好的文本擦掉。
 *   - 反之（事件在没有前置 isNewMessage 的通道上独立到达）走完整复位路径。
 */
function onAssistantTurnStart() {
    if (turnBoundaryLatched) {
        turnBoundaryLatched = false;
        if (subtitleEnabled) {
            ensureSubtitleVisibleIfEnabled();
        } else {
            hideSubtitle();
        }
        return;
    }
    resetSubtitleTurnState();
    // 开关开启时保留显示框（保持空白等待新文本），关闭时连框一起隐藏
    if (subtitleEnabled) {
        writeSubtitleText('');
        ensureSubtitleVisibleIfEnabled();
    } else {
        hideSubtitle();
    }
}

/**
 * Turn 结束时调用：尝试翻译并替换字幕文本。
 * 如果不需要翻译（同语言 / 检测失败 / 用户禁用），保留原文。
 */
async function translateAndShowSubtitle(text) {
    if (!text || !text.trim()) {
        return;
    }

    // 结构化 turn 走占位符分支：不翻译，不写原文，避免把大段 markdown/code 送去 LLM
    if (currentTurnIsStructured) {
        finalizeSubtitleAsStructured();
        return;
    }

    // 快照本次请求归属的 turn 与 request 序号。响应回来时必须跟当前值匹配，
    // 否则说明已被新 turn / 新请求抢占（哪怕原文字面量相同也要丢弃）。
    const requestTurnId = currentTurnId;
    const requestId = ++currentTranslationRequestId;
    // 收到 turn-end 才算当前 turn 已结算；此后 updateSubtitleStreamingText 的
    // 迟到调用会被丢弃，避免被延迟渲染的拟真 bubble 刷回原文（PR #778 修复）。
    isCurrentTurnFinalized = true;

    if (userLanguage === null) {
        await getUserLanguage();
    }

    // 请求之前再校验一次 — getUserLanguage 本身是 await，可能已经跨 turn
    if (requestTurnId !== currentTurnId || requestId !== currentTranslationRequestId) {
        return;
    }

    // 始终把原文当作字幕基准
    currentTurnOriginalText = text;
    if (subtitleEnabled) {
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(text);
    }

    if (!subtitleEnabled) {
        return; // 开关关闭，不发翻译请求
    }

    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
    }
    currentTranslateAbortController = new AbortController();
    const abortController = currentTranslateAbortController;

    try {
        const response = await fetch('/api/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: text,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'),
                source_lang: null
            }),
            signal: abortController.signal
        });

        if (!response.ok) {
            console.warn('字幕翻译请求失败:', response.status);
            return;
        }

        const result = await response.json();

        // 已经被新 turn / 新请求抢占，丢弃过期结果（用单调序号判断，不用原文）
        if (requestTurnId !== currentTurnId || requestId !== currentTranslationRequestId) {
            return;
        }

        if (!subtitleEnabled) {
            return; // 翻译期间用户关掉了开关
        }

        // 真正发生了翻译才替换；同语言/未知语言/失败保留原文
        if (result.success && result.translated_text &&
            result.source_lang && result.target_lang &&
            result.source_lang !== result.target_lang &&
            result.source_lang !== 'unknown' &&
            result.translated_text !== text) {
            ensureSubtitleVisibleIfEnabled();
            writeSubtitleText(result.translated_text);
            console.log('字幕已翻译:', result.translated_text.substring(0, 50));
        }
        // else: 不需要翻译，保留刚才写入的原文，什么也不做
    } catch (error) {
        if (error.name === 'AbortError') {
            return;
        }
        console.error('字幕翻译异常:', {
            error: error.message,
            text: text.substring(0, 50) + '...',
            userLanguage: userLanguage
        });
    } finally {
        if (currentTranslateAbortController === abortController) {
            currentTranslateAbortController = null;
        }
    }
}

// 初始化字幕模块（DOM 就绪后绑定拖拽 & turn 事件）
document.addEventListener('DOMContentLoaded', async function() {
    initSubtitleDrag();
    await getUserLanguage();
    window.addEventListener('neko-assistant-turn-start', onAssistantTurnStart);

    // 通用引导管理器：index.html / chat.html 都加载 subtitle.js，
    // 但自身模板没有 init 调用，历史上靠这里兜底（其他子页面模板各自 init）。
    // 幂等保护防止跨页面重复 init。
    if (!window.__universalTutorialManagerInitialized &&
        typeof initUniversalTutorialManager === 'function') {
        try {
            initUniversalTutorialManager();
            window.__universalTutorialManagerInitialized = true;
            console.log('[App] 通用引导管理器已初始化');
        } catch (error) {
            console.error('[App] 通用引导管理器初始化失败:', error);
        }
    }
});

// 字幕拖拽功能
function initSubtitleDrag() {
    const subtitleDisplay = document.getElementById('subtitle-display');
    const dragHandle = document.getElementById('subtitle-drag-handle');

    if (!subtitleDisplay || !dragHandle) {
        console.warn('[Subtitle] 无法找到字幕元素或拖拽句柄');
        return;
    }

    let isDragging = false;
    let pendingDrag = false;
    let isManualPosition = false;
    let startX, startY;
    let initialX, initialY;

    function handleMouseDown(e) {
        if (e.button !== 0) return;

        pendingDrag = true;
        document.body.style.userSelect = 'none';

        const rect = subtitleDisplay.getBoundingClientRect();
        startX = e.clientX;
        startY = e.clientY;
        initialX = rect.left;
        initialY = rect.top;

        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
    }

    function handleTouchStart(e) {
        const touch = e.touches[0];
        handleMouseDown({
            button: 0,
            clientX: touch.clientX,
            clientY: touch.clientY
        });
    }

    function commitDragPosition() {
        isDragging = true;
        pendingDrag = false;
        isManualPosition = true;
        // animation forwards 填充层优先级高于 inline style，
        // 必须先 kill animation 再设置 transform，否则 transform 被动画覆盖导致跳变
        subtitleDisplay.style.animation = 'none';
        subtitleDisplay.style.transition = 'none';
        subtitleDisplay.classList.add('dragging');
        subtitleDisplay.style.transform = 'none';
        subtitleDisplay.style.left = initialX + 'px';
        subtitleDisplay.style.top = initialY + 'px';
        subtitleDisplay.style.bottom = 'auto';
    }

    function handleMouseMove(e) {
        if (!pendingDrag && !isDragging) return;

        e.preventDefault();

        const dx = e.clientX - startX;
        const dy = e.clientY - startY;

        // 超过 4px 阈值后才正式进入拖动模式，避免单纯点击破坏居中布局
        if (!isDragging) {
            if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
            commitDragPosition();
        }

        let newX = initialX + dx;
        let newY = initialY + dy;

        const maxX = window.innerWidth - subtitleDisplay.offsetWidth;
        const maxY = window.innerHeight - subtitleDisplay.offsetHeight;

        newX = Math.max(0, Math.min(newX, maxX));
        newY = Math.max(0, Math.min(newY, maxY));

        subtitleDisplay.style.left = newX + 'px';
        subtitleDisplay.style.top = newY + 'px';
    }

    function handleTouchMove(e) {
        const touch = e.touches[0];
        handleMouseMove({
            preventDefault: () => e.preventDefault(),
            clientX: touch.clientX,
            clientY: touch.clientY
        });
    }

    function handleMouseUp() {
        if (!pendingDrag && !isDragging) return;

        pendingDrag = false;
        isDragging = false;
        document.body.style.userSelect = '';
        subtitleDisplay.classList.remove('dragging');

        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
    }

    function handleTouchUp() {
        handleMouseUp();
    }

    dragHandle.addEventListener('mousedown', handleMouseDown);
    dragHandle.addEventListener('touchstart', handleTouchStart, { passive: false });

    document.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.addEventListener('touchend', handleTouchUp);
    document.addEventListener('touchcancel', handleTouchUp);

    window.addEventListener('resize', () => {
        if (!isManualPosition) return;

        const rect = subtitleDisplay.getBoundingClientRect();
        const maxX = Math.max(0, window.innerWidth - subtitleDisplay.offsetWidth);
        const maxY = Math.max(0, window.innerHeight - subtitleDisplay.offsetHeight);

        if (rect.right > window.innerWidth) {
            subtitleDisplay.style.left = maxX + 'px';
        }
        if (rect.bottom > window.innerHeight) {
            subtitleDisplay.style.top = maxY + 'px';
        }
    });
}

// ======================== 外部桥接接口 ========================
// 供 app-settings.js / React 聊天窗口在合并服务器设置或用户点击开关时调用
window.subtitleBridge = {
    /** 仅同步状态，不做副作用（用于服务器设置回灌） */
    setSubtitleEnabled: function(enabled) {
        subtitleEnabled = !!enabled;
        if (typeof window.appState !== 'undefined') {
            window.appState.subtitleEnabled = subtitleEnabled;
        }
        localStorage.setItem('subtitleEnabled', subtitleEnabled.toString());

        if (subtitleEnabled) {
            // 重新启用：把当前 turn 已有原文补显到字幕
            if (currentTurnOriginalText && currentTurnOriginalText.trim()) {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText(currentTurnOriginalText);
            } else {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText('');
            }
        } else {
            hideSubtitle();
        }
    },
    /** 完整切换：翻转开关 + 执行运行时副作用（隐藏/补显字幕，并在开启时翻译当前文本） */
    toggle: function() {
        subtitleEnabled = !subtitleEnabled;
        if (typeof window.appState !== 'undefined') {
            window.appState.subtitleEnabled = subtitleEnabled;
        }
        localStorage.setItem('subtitleEnabled', subtitleEnabled.toString());
        if (typeof window.appSettings !== 'undefined' && window.appSettings.saveSettings) {
            window.appSettings.saveSettings();
        }

        console.log('字幕开关:', subtitleEnabled ? '开启' : '关闭');

        if (!subtitleEnabled) {
            if (currentTranslateAbortController) {
                currentTranslateAbortController.abort();
                currentTranslateAbortController = null;
            }
            hideSubtitle();
        } else {
            // 立即把当前 turn 已有原文补显
            if (currentTurnOriginalText && currentTurnOriginalText.trim()) {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText(currentTurnOriginalText);
                // 仅当本 turn 已经收到 turn-end 时，才对缓存原文补发一次翻译；
                // 流式途中打开开关时，让字幕跟随流式文本更新，避免"半句翻译 → 被后续 chunk 覆盖"的闪烁。
                if (isCurrentTurnFinalized) {
                    translateAndShowSubtitle(currentTurnOriginalText);
                }
            } else {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText('');
            }
        }

        return subtitleEnabled;
    },
    setUserLanguage: function(lang) {
        if (!lang || typeof lang !== 'string') {
            lang = 'zh';
        }
        userLanguage = normalizeLanguageCode(lang.trim().toLowerCase());
        if (typeof window.appState !== 'undefined') {
            window.appState.userLanguage = userLanguage;
        }
        localStorage.setItem('userLanguage', userLanguage);
    },
    /** 供 app-chat.js / app-chat-adapter.js 在 isNewMessage 分支首个 chunk 前调用 */
    beginTurn: beginSubtitleTurn,
    /** 供 app-chat.js 在 _geminiTurnFullText 累积时调用 */
    updateStreamingText: updateSubtitleStreamingText,
    /** 供 app-chat.js / app-chat-adapter.js 命中结构化富文本检测时调用 */
    markStructured: markSubtitleStructured,
    /** 供 app-websocket.js 在结构化 turn 的 turn end 时调用（跳过翻译） */
    finalizeAsStructured: finalizeSubtitleAsStructured,
    /** 供 app-websocket.js 在 turn end 时调用 */
    finalizeTurnWithTranslation: translateAndShowSubtitle
};

// 向后兼容：保留全局函数名，但函数体已经精简
window.translateAndShowSubtitle = translateAndShowSubtitle;
window.updateSubtitleStreamingText = updateSubtitleStreamingText;
window.beginSubtitleTurn = beginSubtitleTurn;
window.markSubtitleStructured = markSubtitleStructured;
window.finalizeSubtitleAsStructured = finalizeSubtitleAsStructured;
window.getUserLanguage = getUserLanguage;
