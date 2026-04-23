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

var SubtitleShared = window.nekoSubtitleShared || null;
var subtitleUiController = null;
var initialSubtitleSettings = SubtitleShared && typeof SubtitleShared.getSettings === 'function'
    ? SubtitleShared.getSettings()
    : null;

function normalizeLanguageCode(lang) {
    if (SubtitleShared && typeof SubtitleShared.normalizeTranslationLanguageCode === 'function') {
        return SubtitleShared.normalizeTranslationLanguageCode(lang);
    }
    if (!lang) return 'zh';
    var value = String(lang).toLowerCase();
    if (value.indexOf('ja') === 0) return 'ja';
    if (value.indexOf('en') === 0) return 'en';
    if (value.indexOf('ko') === 0) return 'ko';
    if (value.indexOf('ru') === 0) return 'ru';
    return 'zh';
}

let subtitleEnabled = initialSubtitleSettings
    ? !!initialSubtitleSettings.subtitleEnabled
    : ((typeof window.appState !== 'undefined' && typeof window.appState.subtitleEnabled !== 'undefined')
        ? window.appState.subtitleEnabled
        : localStorage.getItem('subtitleEnabled') === 'true');

function applySharedSubtitleSettings(patch, options) {
    if (!SubtitleShared || typeof SubtitleShared.updateSettings !== 'function') {
        if (Object.prototype.hasOwnProperty.call(patch, 'subtitleEnabled')) {
            subtitleEnabled = !!patch.subtitleEnabled;
        }
        if (Object.prototype.hasOwnProperty.call(patch, 'userLanguage')) {
            userLanguage = normalizeLanguageCode(patch.userLanguage);
        }
        return {
            subtitleEnabled: subtitleEnabled,
            userLanguage: userLanguage || 'zh'
        };
    }
    var next = SubtitleShared.updateSettings(patch, {
        persist: !options || options.persist !== false,
        source: options && options.source ? options.source : 'subtitle-core',
        silent: options && options.silent === true,
        refreshUiLocale: options && options.refreshUiLocale === true
    });
    subtitleEnabled = !!next.subtitleEnabled;
    if (next.userLanguage) {
        userLanguage = next.userLanguage;
    }
    return next;
}

function syncSubtitleRenderState(source) {
    if (!SubtitleShared || typeof SubtitleShared.updateRenderState !== 'function') return;
    var display = document.getElementById('subtitle-display');
    var subtitleText = document.getElementById('subtitle-text');
    var currentSettings = SubtitleShared.getSettings ? SubtitleShared.getSettings() : initialSubtitleSettings;
    SubtitleShared.updateRenderState({
        text: subtitleText ? (subtitleText.textContent || '') : '',
        visible: !!display && !display.classList.contains('hidden'),
        subtitleEnabled: subtitleEnabled,
        userLanguage: userLanguage || (currentSettings && currentSettings.userLanguage) || 'zh',
        uiLocale: currentSettings ? currentSettings.uiLocale : (SubtitleShared.getCurrentUiLocale ? SubtitleShared.getCurrentUiLocale() : 'zh-CN'),
        subtitleOpacity: currentSettings ? currentSettings.subtitleOpacity : 95,
        subtitleDragAnywhere: currentSettings ? currentSettings.subtitleDragAnywhere : false,
        subtitleSize: currentSettings ? currentSettings.subtitleSize : 'medium'
    }, { source: source || 'subtitle-core' });
}

/**
 * 设置用户语言并同步到 appState
 */
function commitUserLanguage(lang, options) {
    var next = applySharedSubtitleSettings({
        userLanguage: normalizeLanguageCode(lang)
    }, {
        source: options && options.source ? options.source : 'subtitle-language',
        persist: !options || options.persist !== false
    });
    userLanguage = next.userLanguage;
    if (options && options.saveSettings === false) {
        return userLanguage;
    }
    if (typeof window.appSettings !== 'undefined' && window.appSettings.saveSettings) {
        window.appSettings.saveSettings();
    }
    return userLanguage;
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
                commitUserLanguage(normalizeLanguageCode(data.language), {
                    source: 'subtitle-api-language',
                    saveSettings: false
                });
                return userLanguage;
            }
        } catch (error) {
            console.warn('从API获取用户语言失败，尝试使用缓存或浏览器语言:', error);
        }
        const cachedLang = localStorage.getItem('userLanguage');
        if (cachedLang) {
            commitUserLanguage(normalizeLanguageCode(cachedLang), {
                source: 'subtitle-cached-language',
                saveSettings: false
            });
            return userLanguage;
        }
        const browserLang = navigator.language || navigator.userLanguage;
        commitUserLanguage(normalizeLanguageCode(browserLang), {
            source: 'subtitle-browser-language',
            saveSettings: false
        });
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

// --- 增量逐句翻译状态 ---
let incrementalTranslatedCount = 0;
let incrementalTranslatedSentences = [];
let incrementalTranslateTimer = null;
let incrementalAbortController = null;
let incrementalRequestId = 0;

/**
 * 句子分割（用于增量翻译）。
 * 逻辑源自 app-chat.js splitIntoSentences，自包含无外部依赖。
 * 返回 { sentences: string[], rest: string }。
 */
function splitSubtitleSentences(buffer) {
    var sentences = [];
    var s = (buffer || '').replace(/\r\n/g, '\n');
    var start = 0;

    function isPunctForBoundary(ch) {
        return ch === '\u3002' || ch === '\uFF01' || ch === '\uFF1F' ||
               ch === '!' || ch === '?' || ch === '.' || ch === '\u2026';
    }

    function isBoundary(ch, next) {
        if (ch === '\n') return true;
        if (isPunctForBoundary(ch) && next && isPunctForBoundary(next)) return false;
        if (isPunctForBoundary(ch) && !next) return false;
        if (ch === '\u3002' || ch === '\uFF01' || ch === '\uFF1F') return true;
        if (ch === '!' || ch === '?') return true;
        if (ch === '\u2026') return true;
        if (ch === '.') {
            if (!next) return true;
            return /\s|\n|["')\]]/.test(next);
        }
        return false;
    }

    for (var i = 0; i < s.length; i++) {
        var ch = s[i];
        var next = i + 1 < s.length ? s[i + 1] : '';
        if (isBoundary(ch, next)) {
            var piece = s.slice(start, i + 1);
            var trimmed = piece.replace(/^\s+/, '').replace(/\s+$/, '');
            if (trimmed) sentences.push(trimmed);
            start = i + 1;
        }
    }

    return { sentences: sentences, rest: s.slice(start) };
}

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
    syncSubtitleRenderState('subtitle-visible');
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
    syncSubtitleRenderState('subtitle-hidden');
}

/**
 * 写入字幕文本（不影响显示/隐藏状态）
 * 长文本自动缩小字号以保持在可视范围内。
 */
var _subtitleFontResizeTimer = null;
function writeSubtitleText(text) {
    const subtitleText = document.getElementById('subtitle-text');
    if (!subtitleText) return;
    subtitleText.textContent = text || '';
    syncSubtitleRenderState('subtitle-text-write');

    // 自适应字号：防抖测量，避免流式高频触发
    if (_subtitleFontResizeTimer) clearTimeout(_subtitleFontResizeTimer);
    if (!text || !text.trim()) {
        subtitleText.style.fontSize = '';
        syncSubtitleRenderState('subtitle-text-clear');
        return;
    }
    _subtitleFontResizeTimer = setTimeout(function() {
        var display = document.getElementById('subtitle-display');
        if (!display) return;
        var preset = SubtitleShared && typeof SubtitleShared.getSettings === 'function'
            ? SubtitleShared.getSettings()
            : { subtitleSize: 'medium' };
        var presetSize = SubtitleShared && typeof SubtitleShared.getSizePreset === 'function'
            ? SubtitleShared.getSizePreset(preset.subtitleSize)
            : { width: display.offsetWidth || 600, minHeight: parseInt(display.style.minHeight, 10) || 80 };
        var layout = SubtitleShared && typeof SubtitleShared.measureSubtitleLayout === 'function'
            ? SubtitleShared.measureSubtitleLayout({
                mode: 'web',
                text: text,
                presetKey: preset.subtitleSize,
                maxWidth: presetSize.width,
                minHeight: presetSize.minHeight,
                availableWidth: Math.max(0, (display.clientWidth || presetSize.width) - 48),
                availableHeight: Math.max(display.offsetHeight || presetSize.minHeight, presetSize.minHeight)
            })
            : { fontSize: 17 };
        subtitleText.style.fontSize = layout.fontSize < 17 ? layout.fontSize + 'px' : '';
        syncSubtitleRenderState('subtitle-text-resize');
    }, 200);
}

/**
 * 增量翻译核心：翻译新完成的句子并追加到字幕显示。
 */
async function translateNewSentencesIncremental(newSentences, requestSnapId) {
    if (!newSentences || newSentences.length === 0) return;
    if (!subtitleEnabled) return;
    if (requestSnapId !== incrementalRequestId) return;

    var textToTranslate = newSentences.join(' ');

    if (incrementalAbortController) {
        incrementalAbortController.abort();
    }
    incrementalAbortController = new AbortController();
    var abortCtrl = incrementalAbortController;

    try {
        var response = await fetch('/api/translate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text: textToTranslate,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'),
                source_lang: null
            }),
            signal: abortCtrl.signal
        });

        if (!response.ok) {
            appendIncrementalFallback(newSentences, requestSnapId);
            return;
        }

        var result = await response.json();
        if (requestSnapId !== incrementalRequestId) return;

        if (result.success && result.translated_text &&
            result.source_lang && result.target_lang &&
            result.source_lang !== result.target_lang &&
            result.source_lang !== 'unknown') {
            incrementalTranslatedSentences.push(result.translated_text.trim());
        } else {
            for (var j = 0; j < newSentences.length; j++) {
                incrementalTranslatedSentences.push(newSentences[j]);
            }
        }
        incrementalTranslatedCount += newSentences.length;
        updateIncrementalDisplay();
    } catch (error) {
        if (error.name === 'AbortError') return;
        appendIncrementalFallback(newSentences, requestSnapId);
    } finally {
        if (incrementalAbortController === abortCtrl) {
            incrementalAbortController = null;
        }
    }
}

function appendIncrementalFallback(sentences, requestSnapId) {
    if (requestSnapId !== incrementalRequestId) return;
    for (var i = 0; i < sentences.length; i++) {
        incrementalTranslatedSentences.push(sentences[i]);
    }
    incrementalTranslatedCount += sentences.length;
    updateIncrementalDisplay();
}

function updateIncrementalDisplay() {
    if (!subtitleEnabled) return;
    var fullText = incrementalTranslatedSentences.join(' ');
    ensureSubtitleVisibleIfEnabled();
    writeSubtitleText(fullText);
}

/**
 * 流式更新：本回合 AI 文本累积时调用。
 * 检测完整句子后防抖触发增量翻译，逐句显示。
 *
 * 竞态保护（PR #778）：
 *   - 已 finalize 后，丢弃后续流式写入。
 *   - 已判定为结构化的 turn 不接受原文写入，继续维持 [markdown] 占位。
 */
function updateSubtitleStreamingText(text) {
    if (isCurrentTurnFinalized) return;
    if (currentTurnIsStructured) return;

    var cleaned = (text || '').toString();
    currentTurnOriginalText = cleaned;

    if (!subtitleEnabled) return;
    if (userLanguage === null) getUserLanguage();

    var splitResult = splitSubtitleSentences(cleaned);
    var allSentences = splitResult.sentences;
    var rest = splitResult.rest;
    var newCount = allSentences.length - incrementalTranslatedCount;

    if (newCount > 0) {
        var newSentences = allSentences.slice(incrementalTranslatedCount);

        if (incrementalTranslateTimer) clearTimeout(incrementalTranslateTimer);
        var requestSnapId = ++incrementalRequestId;
        incrementalTranslateTimer = setTimeout(function() {
            incrementalTranslateTimer = null;
            translateNewSentencesIncremental(newSentences, requestSnapId);
        }, 300);

        // 防抖期间先显示原文 preview
        var preview = incrementalTranslatedSentences.slice();
        for (var i = 0; i < newSentences.length; i++) {
            preview.push(newSentences[i]);
        }
        if (rest.trim()) preview.push(rest.trim());
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(preview.join(' '));
    } else if (rest.trim()) {
        // 无新句子但有尾部更新
        var displayParts = incrementalTranslatedSentences.slice();
        displayParts.push(rest.trim());
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(displayParts.join(' '));
    }
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
    // 增量翻译状态复位
    incrementalTranslatedCount = 0;
    incrementalTranslatedSentences = [];
    if (incrementalTranslateTimer) {
        clearTimeout(incrementalTranslateTimer);
        incrementalTranslateTimer = null;
    }
    if (incrementalAbortController) {
        incrementalAbortController.abort();
        incrementalAbortController = null;
    }
    // 不重置 incrementalRequestId；保持单调递增作为失效令牌，
    // 使旧的翻译响应无法与新的请求 ID 碰撞。
}

/**
 * 供 app-chat.js / app-chat-adapter.js 在 isNewMessage 分支、首个
 * updateSubtitleStreamingText 调用之前先行触发的复位入口。
 *
 * 为什么不能只靠事件：
 *   neko-assistant-turn-start 事件的派发时机取决于首个 chunk 是否可渲染：
 *   可渲染时 app-websocket.js 会在 `gemini_response_first_chunk` 分支
 *   （进入 appendMessage 之前）就派发；不可渲染时要等 appendMessage 创建
 *   出可见气泡（`gemini_response_visible_bubble` 分支）才派发，比首个
 *   chunk 晚一拍。如果只在事件里解锁，后一条路径上轮残留的
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
 * Turn 结束时调用：翻译剩余未处理部分并追加到增量翻译结果。
 */
async function translateAndShowSubtitle(text) {
    if (!text || !text.trim()) {
        return;
    }

    // 结构化 turn 走占位符分支
    if (currentTurnIsStructured) {
        finalizeSubtitleAsStructured();
        return;
    }

    const requestTurnId = currentTurnId;
    const requestId = ++currentTranslationRequestId;
    isCurrentTurnFinalized = true;

    // 取消 pending 的增量翻译定时器
    if (incrementalTranslateTimer) {
        clearTimeout(incrementalTranslateTimer);
        incrementalTranslateTimer = null;
    }

    if (userLanguage === null) {
        await getUserLanguage();
    }

    if (requestTurnId !== currentTurnId || requestId !== currentTranslationRequestId) {
        return;
    }

    currentTurnOriginalText = text;

    if (!subtitleEnabled) {
        return;
    }

    // 计算剩余未翻译部分
    var splitResult = splitSubtitleSentences(text);
    var totalSentences = splitResult.sentences;
    var trailingRest = splitResult.rest;
    var remainingSentences = totalSentences.slice(incrementalTranslatedCount);
    var remainingText = remainingSentences.join(' ');
    if (trailingRest && trailingRest.trim()) {
        remainingText = (remainingText ? remainingText + ' ' : '') + trailingRest.trim();
    }

    // 无剩余 → 直接显示已有的增量翻译
    if (!remainingText.trim()) {
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(incrementalTranslatedSentences.join(' ') || text);
        return;
    }

    if (incrementalAbortController) {
        incrementalAbortController.abort();
        incrementalAbortController = null;
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
                text: remainingText,
                target_lang: (userLanguage !== null ? userLanguage : 'zh'),
                source_lang: null
            }),
            signal: abortController.signal
        });

        if (!response.ok) {
            console.warn('字幕翻译请求失败:', response.status);
            var fallbackParts = incrementalTranslatedSentences.slice();
            if (remainingSentences.length) fallbackParts = fallbackParts.concat(remainingSentences);
            if (trailingRest && trailingRest.trim()) fallbackParts.push(trailingRest.trim());
            ensureSubtitleVisibleIfEnabled();
            writeSubtitleText(fallbackParts.join(' ') || text);
            return;
        }

        const result = await response.json();

        if (requestTurnId !== currentTurnId || requestId !== currentTranslationRequestId) {
            return;
        }
        if (!subtitleEnabled) return;

        if (result.success && result.translated_text &&
            result.source_lang && result.target_lang &&
            result.source_lang !== result.target_lang &&
            result.source_lang !== 'unknown') {
            incrementalTranslatedSentences.push(result.translated_text.trim());
            ensureSubtitleVisibleIfEnabled();
            writeSubtitleText(incrementalTranslatedSentences.join(' '));
            console.log('字幕翻译完成:', incrementalTranslatedSentences.join(' ').substring(0, 80));
        } else {
            if (remainingSentences.length) {
                incrementalTranslatedSentences = incrementalTranslatedSentences.concat(remainingSentences);
            }
            if (trailingRest && trailingRest.trim()) {
                incrementalTranslatedSentences.push(trailingRest.trim());
            }
            ensureSubtitleVisibleIfEnabled();
            writeSubtitleText(incrementalTranslatedSentences.join(' '));
        }
    } catch (error) {
        if (error.name === 'AbortError') return;
        if (subtitleEnabled && requestTurnId === currentTurnId) {
            ensureSubtitleVisibleIfEnabled();
            writeSubtitleText(text);
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

function initSubtitleHostUi() {
    if (subtitleUiController || !SubtitleShared || typeof SubtitleShared.initSubtitleUI !== 'function') {
        return subtitleUiController;
    }
    subtitleUiController = SubtitleShared.initSubtitleUI({
        host: 'web',
        onLanguageChange: function(lang) {
            if (window.subtitleBridge && typeof window.subtitleBridge.setUserLanguage === 'function') {
                window.subtitleBridge.setUserLanguage(lang);
            }
        },
        onSettingsApplied: function(state, refs, detail) {
            if (detail && detail.source === 'subtitle-ui-size' && refs && refs.text && refs.text.textContent) {
                writeSubtitleText(refs.text.textContent);
            }
            syncSubtitleRenderState(detail && detail.source ? detail.source : 'subtitle-ui-apply');
        }
    });
    return subtitleUiController;
}

function syncSettingsPanel() {
    var toggle = document.getElementById('subtitle-translate-toggle');
    var select = document.getElementById('subtitle-lang-select');
    if (toggle) toggle.checked = subtitleEnabled;
    if (select && userLanguage) select.value = userLanguage;
    if (subtitleUiController && typeof subtitleUiController.applyCurrentState === 'function') {
        subtitleUiController.applyCurrentState();
    }
}

function initSubtitleSettings() {
    return initSubtitleHostUi();
}

function initSubtitleDrag() {
    return initSubtitleHostUi();
}

function retranslateCurrentSubtitle() {
    incrementalTranslatedCount = 0;
    incrementalTranslatedSentences = [];
    if (incrementalTranslateTimer) {
        clearTimeout(incrementalTranslateTimer);
        incrementalTranslateTimer = null;
    }
    if (incrementalAbortController) {
        incrementalAbortController.abort();
        incrementalAbortController = null;
    }
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
        currentTranslateAbortController = null;
    }

    if (!(subtitleEnabled && currentTurnOriginalText && currentTurnOriginalText.trim())) {
        syncSubtitleRenderState('subtitle-language-idle');
        return;
    }

    if (isCurrentTurnFinalized) {
        isCurrentTurnFinalized = false;
        translateAndShowSubtitle(currentTurnOriginalText);
    } else {
        updateSubtitleStreamingText(currentTurnOriginalText);
    }
}

// 初始化字幕模块（DOM 就绪后绑定拖拽 & turn 事件）
document.addEventListener('DOMContentLoaded', async function() {
    initSubtitleHostUi();
    await getUserLanguage();
    syncSettingsPanel();
    syncSubtitleRenderState('subtitle-dom-ready');
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

// ======================== 外部桥接接口 ========================
// 供 app-settings.js / React 聊天窗口在合并服务器设置或用户点击开关时调用
window.subtitleBridge = {
    /** 供 preload-pet.js watchOriginalText 读取当前 turn 的完整原文 */
    getCurrentTurnOriginalText: function() {
        return currentTurnOriginalText || '';
    },
    /** 供 preload-pet.js 判断当前 turn 是否已完成（流式阶段不转发原文） */
    isCurrentTurnFinalized: function() {
        return isCurrentTurnFinalized;
    },
    /** 仅同步状态，不做副作用（用于服务器设置回灌） */
    setSubtitleEnabled: function(enabled) {
        subtitleEnabled = !!enabled;
        applySharedSubtitleSettings({
            subtitleEnabled: subtitleEnabled
        }, {
            source: 'subtitle-bridge-set-enabled'
        });

        if (subtitleEnabled) {
            // 优先显示已有的增量翻译，其次原文
            var incrementalText = incrementalTranslatedSentences.join(' ');
            if (incrementalText.trim()) {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText(incrementalText);
            } else if (currentTurnOriginalText && currentTurnOriginalText.trim()) {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText(currentTurnOriginalText);
            } else {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText('');
            }
        } else {
            hideSubtitle();
        }
        syncSettingsPanel();
        syncSubtitleRenderState('subtitle-bridge-set-enabled');
    },
    /** 完整切换：翻转开关 + 执行运行时副作用（隐藏/补显字幕，并在开启时翻译当前文本） */
    toggle: function() {
        subtitleEnabled = !subtitleEnabled;
        applySharedSubtitleSettings({
            subtitleEnabled: subtitleEnabled
        }, {
            source: 'subtitle-bridge-toggle'
        });
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
            // 优先显示已有的增量翻译
            var incrementalText = incrementalTranslatedSentences.join(' ');
            if (incrementalText.trim()) {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText(incrementalText);
                if (isCurrentTurnFinalized) {
                    translateAndShowSubtitle(currentTurnOriginalText);
                }
            } else if (currentTurnOriginalText && currentTurnOriginalText.trim()) {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText(currentTurnOriginalText);
                if (isCurrentTurnFinalized) {
                    translateAndShowSubtitle(currentTurnOriginalText);
                }
            } else {
                ensureSubtitleVisibleIfEnabled();
                writeSubtitleText('');
            }
        }

        // 同步设置面板状态
        syncSettingsPanel();
        syncSubtitleRenderState('subtitle-bridge-toggle');

        return subtitleEnabled;
    },
    setUserLanguage: function(lang) {
        if (!lang || typeof lang !== 'string') {
            lang = 'zh';
        }
        userLanguage = commitUserLanguage(lang.trim().toLowerCase(), {
            source: 'subtitle-bridge-language'
        });
        syncSettingsPanel();
        retranslateCurrentSubtitle();
        syncSubtitleRenderState('subtitle-bridge-language');
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
