// 字幕（常驻字幕 + 按需翻译）
//
// 工作流程
// ──────────
//   1. 一个 AI 回合（turn）= 一段连续讲话，可能被切成多个聊天气泡。
//   2. 字幕跨气泡持久显示，不会被新气泡清空。
//   3. turn 进行中：流式原文只进入当前 turn 缓存和增量翻译队列。
//   4. 字幕面板只写入译文或结构化占位符；译文回来前沿用旧链路空白等待，不预览原文。
//   5. turn 结束：补齐剩余句子的翻译队列，不把整段原文写入面板。
//   6. 下一个 turn-start 才清空字幕，开始下一段。
//
// 翻译开关由 React 聊天窗口的 composer 按钮控制，状态走 window.subtitleBridge。
// 旧的字幕提示气泡（subtitle-prompt-message）已下线，相关 prompt/detect 代码全部移除。

var SubtitleShared = window.nekoSubtitleShared || null;
var subtitleUiController = null;
var webDanmakuModeSettingsCleanup = null;
var WEB_DANMAKU_AVATAR_GAP = 12;
var WEB_DANMAKU_VERTICAL_OFFSET_RATIO = 0.5;
var WEB_DANMAKU_STATE_SYNC_MS = 120;
var WEB_DANMAKU_MIN_PANEL_WIDTH = 228;
var WEB_DANMAKU_MIN_PANEL_HEIGHT = 40;
var WEB_DANMAKU_LAYOUT_EPSILON = 0.25;
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
    if (value.indexOf('es') === 0) return 'es';
    if (value.indexOf('pt') === 0) return 'pt';
    return 'zh';
}

let subtitleEnabled = initialSubtitleSettings
    ? !!initialSubtitleSettings.subtitleEnabled
    : ((typeof window.appState !== 'undefined' && typeof window.appState.subtitleEnabled !== 'undefined')
        ? window.appState.subtitleEnabled
        : localStorage.getItem('subtitleEnabled') === 'true');
let subtitleSuppressedByGoodbye = false;
let subtitleWasVisibleBeforeGoodbye = false;
let subtitleWindowWasVisibleBeforeGoodbye = false;
let subtitleDropCurrentTurnUntilNextStart = false;

function isSubtitleTranslationOwner() {
    return !(window.__NEKO_MULTI_WINDOW__ &&
             window.nekoChatWindow &&
             window.location &&
             window.location.pathname === '/chat');
}

function isWebSubtitleDanmakuHost() {
    var body = document.body;
    return !!(body &&
        body.classList.contains('subtitle-web-host') &&
        !body.classList.contains('subtitle-window-host') &&
        !body.classList.contains('electron-chat-window') &&
        !body.classList.contains('lanlan-pet-mode') &&
        !window.__LANLAN_IS_ELECTRON_PET__ &&
        !window.__NEKO_MULTI_WINDOW__ &&
        isSubtitleTranslationOwner());
}

function normalizeWebDanmakuRect(rect) {
    if (!rect) return null;
    var left = Number(rect.left);
    var top = Number(rect.top);
    if (!Number.isFinite(left)) left = Number(rect.x);
    if (!Number.isFinite(top)) top = Number(rect.y);
    var right = Number(rect.right);
    var bottom = Number(rect.bottom);
    var width = Number(rect.width);
    var height = Number(rect.height);
    if (!Number.isFinite(width) && Number.isFinite(left) && Number.isFinite(right)) {
        width = right - left;
    }
    if (!Number.isFinite(height) && Number.isFinite(top) && Number.isFinite(bottom)) {
        height = bottom - top;
    }
    if (!Number.isFinite(left) || !Number.isFinite(top) ||
        !Number.isFinite(width) || !Number.isFinite(height) ||
        width <= 0 || height <= 0) {
        return null;
    }
    if (!Number.isFinite(right)) right = left + width;
    if (!Number.isFinite(bottom)) bottom = top + height;
    var centerX = Number(rect.centerX);
    var centerY = Number(rect.centerY);
    if (!Number.isFinite(centerX)) centerX = left + width / 2;
    if (!Number.isFinite(centerY)) centerY = top + height / 2;
    return {
        left: left,
        top: top,
        right: right,
        bottom: bottom,
        width: width,
        height: height,
        centerX: centerX,
        centerY: centerY
    };
}

function resolveWebDanmakuManagerBounds(type) {
    var manager = type === 'live2d' ? window.live2dManager
        : (type === 'vrm' ? window.vrmManager
            : (type === 'mmd' ? window.mmdManager : null));
    if (!manager || !manager.currentModel || typeof manager.getModelScreenBounds !== 'function') {
        return null;
    }
    if (type === 'mmd' && !manager.currentModel.mesh) {
        return null;
    }
    try {
        return normalizeWebDanmakuRect(manager.getModelScreenBounds());
    } catch (_) {
        return null;
    }
}

function getVisibleElementRectForWebDanmaku(element) {
    if (!element || typeof element.getBoundingClientRect !== 'function') return null;
    try {
        return normalizeWebDanmakuRect(element.getBoundingClientRect());
    } catch (_) {
        return null;
    }
}

function resolveWebDanmakuPngtuberBounds() {
    var elements = [];
    var manager = window.pngtuberManager;
    if (manager) {
        if (manager.image) elements.push(manager.image);
        if (manager.imageElement) elements.push(manager.imageElement);
        if (manager.canvasElement) elements.push(manager.canvasElement);
    }
    var container = document.getElementById('pngtuber-container');
    if (container && typeof container.querySelectorAll === 'function') {
        var nodes = container.querySelectorAll('.pngtuber-image, canvas, img');
        for (var i = 0; i < nodes.length; i += 1) {
            elements.push(nodes[i]);
        }
    }
    for (var j = 0; j < elements.length; j += 1) {
        var rect = getVisibleElementRectForWebDanmaku(elements[j]);
        if (rect) return rect;
    }
    return null;
}

function getWebDanmakuAvatarBounds() {
    var configuredType = (window.lanlan_config && window.lanlan_config.model_type
        ? String(window.lanlan_config.model_type).toLowerCase()
        : '');
    var orderedTypes = [];
    ['live2d', 'vrm', 'mmd', 'pngtuber'].forEach(function(type) {
        if (type === configuredType) orderedTypes.unshift(type);
        else orderedTypes.push(type);
    });

    for (var i = 0; i < orderedTypes.length; i += 1) {
        var type = orderedTypes[i];
        var bounds = type === 'pngtuber'
            ? resolveWebDanmakuPngtuberBounds()
            : resolveWebDanmakuManagerBounds(type);
        if (bounds) return bounds;
    }
    return null;
}

function cloneWebDanmakuPanelBounds(bounds) {
    return bounds ? {
        width: Number(bounds.width),
        height: Number(bounds.height)
    } : null;
}

function cloneWebDanmakuPanelPosition(position) {
    return position ? {
        left: Number(position.left),
        top: Number(position.top),
        coordinateSpace: 'viewport'
    } : null;
}

function createWebDanmakuSettingsSnapshot() {
    if (!SubtitleShared || typeof SubtitleShared.getSettings !== 'function') return null;
    var state = SubtitleShared.getSettings();
    return {
        subtitlePanelBounds: cloneWebDanmakuPanelBounds(state.subtitlePanelBounds),
        subtitlePanelPosition: cloneWebDanmakuPanelPosition(state.subtitlePanelPosition),
        subtitlePanelLocked: !!state.subtitlePanelLocked,
        subtitleInteractionPassthrough: state.subtitleInteractionPassthrough !== false,
        subtitleOpacity: state.subtitleOpacity
    };
}

function getWebDanmakuViewportSize() {
    var doc = document.documentElement || {};
    return {
        width: Math.max(1, Number(window.innerWidth) || Number(doc.clientWidth) || 1),
        height: Math.max(1, Number(window.innerHeight) || Number(doc.clientHeight) || 1)
    };
}

function clampWebDanmakuNumber(value, min, max) {
    return Math.max(min, Math.min(value, max));
}

function computeWebDanmakuLayout(avatarBounds) {
    var avatar = normalizeWebDanmakuRect(avatarBounds);
    if (!avatar) return null;
    var viewport = getWebDanmakuViewportSize();
    var panelWidth = Math.round(clampWebDanmakuNumber(
        avatar.width,
        WEB_DANMAKU_MIN_PANEL_WIDTH,
        viewport.width
    ));
    var panelHeight = Math.round(clampWebDanmakuNumber(
        panelWidth / 3,
        WEB_DANMAKU_MIN_PANEL_HEIGHT,
        viewport.height
    ));
    var maxLeft = Math.max(0, viewport.width - panelWidth);
    var maxTop = Math.max(0, viewport.height - panelHeight);
    var left = clampWebDanmakuNumber(
        avatar.centerX - panelWidth / 2,
        0,
        maxLeft
    );
    var top = clampWebDanmakuNumber(
        avatar.top - panelHeight - WEB_DANMAKU_AVATAR_GAP +
            panelHeight * WEB_DANMAKU_VERTICAL_OFFSET_RATIO,
        0,
        maxTop
    );
    return {
        subtitlePanelBounds: {
            width: panelWidth,
            height: panelHeight
        },
        subtitlePanelPosition: {
            left: left,
            top: top,
            coordinateSpace: 'viewport'
        }
    };
}

function sameWebDanmakuPanelBounds(a, b) {
    if (!a && !b) return true;
    if (!a || !b) return false;
    return a.width === b.width && a.height === b.height;
}

function sameWebDanmakuPanelPosition(a, b) {
    if (!a && !b) return true;
    if (!a || !b) return false;
    return Math.abs(Number(a.left) - Number(b.left)) < WEB_DANMAKU_LAYOUT_EPSILON &&
        Math.abs(Number(a.top) - Number(b.top)) < WEB_DANMAKU_LAYOUT_EPSILON &&
        a.coordinateSpace === b.coordinateSpace;
}

function sameWebDanmakuLayout(a, b) {
    if (!a && !b) return true;
    if (!a || !b) return false;
    return sameWebDanmakuPanelBounds(a.subtitlePanelBounds, b.subtitlePanelBounds) &&
        sameWebDanmakuPanelPosition(a.subtitlePanelPosition, b.subtitlePanelPosition);
}

function cloneWebDanmakuLayout(layout) {
    if (!layout) return null;
    return {
        subtitlePanelBounds: cloneWebDanmakuPanelBounds(layout.subtitlePanelBounds),
        subtitlePanelPosition: cloneWebDanmakuPanelPosition(layout.subtitlePanelPosition)
    };
}

function applyWebDanmakuVisualLayout(controller, layout) {
    var refs = controller && controller.refs;
    var display = refs && refs.display;
    if (!display || !layout || !layout.subtitlePanelBounds || !layout.subtitlePanelPosition) return false;
    var currentState = SubtitleShared && typeof SubtitleShared.getSettings === 'function'
        ? SubtitleShared.getSettings()
        : null;
    if (SubtitleShared && typeof SubtitleShared.applySubtitlePanelBounds === 'function') {
        SubtitleShared.applySubtitlePanelBounds(display, layout.subtitlePanelBounds, {
            host: 'web',
            fontSize: currentState ? currentState.subtitleFontSize : undefined
        });
    } else {
        display.style.width = layout.subtitlePanelBounds.width + 'px';
        display.style.height = layout.subtitlePanelBounds.height + 'px';
    }
    display.style.left = layout.subtitlePanelPosition.left + 'px';
    display.style.top = layout.subtitlePanelPosition.top + 'px';
    display.style.bottom = 'auto';
    display.style.transform = 'none';
    display.style.animation = 'none';
    display.dataset.subtitlePositioned = 'true';
    return true;
}

function syncWebDanmakuLayoutState(layout) {
    if (!SubtitleShared || typeof SubtitleShared.updateSettings !== 'function') return false;
    if (!layout) return false;
    SubtitleShared.updateSettings({
        subtitlePanelBounds: layout.subtitlePanelBounds,
        subtitlePanelPosition: layout.subtitlePanelPosition
    }, {
        persist: false,
        source: 'subtitle-web-danmaku-layout'
    });
    return true;
}

function attachWebDanmakuModeLayout(controller) {
    if (!SubtitleShared ||
        typeof SubtitleShared.subscribeSettings !== 'function' ||
        typeof SubtitleShared.updateSettings !== 'function') {
        return function() {};
    }

    var active = false;
    var destroyed = false;
    var snapshot = null;
    var rafId = 0;
    var forceNextLayout = false;
    var lastStateSyncAt = 0;
    var lastVisualLayout = null;
    var previousTransition = null;
    var previousWillChange = null;

    function cancelLayoutLoop() {
        if (rafId) {
            window.cancelAnimationFrame(rafId);
            rafId = 0;
        }
        forceNextLayout = false;
    }

    function requestLayoutFrame() {
        if (!active || destroyed || rafId) return;
        rafId = window.requestAnimationFrame(function() {
            rafId = 0;
            if (!active || destroyed) return;
            var now = Date.now();
            var force = forceNextLayout;
            forceNextLayout = false;
            var layout = computeWebDanmakuLayout(getWebDanmakuAvatarBounds());
            if (layout) {
                if (force || !sameWebDanmakuLayout(lastVisualLayout, layout)) {
                    applyWebDanmakuVisualLayout(controller, layout);
                    lastVisualLayout = cloneWebDanmakuLayout(layout);
                }
                if (force || now - lastStateSyncAt >= WEB_DANMAKU_STATE_SYNC_MS) {
                    syncWebDanmakuLayoutState(layout);
                    lastStateSyncAt = now;
                }
            }
            requestLayoutFrame();
        });
    }

    function scheduleLayout(force) {
        if (!active || destroyed) return;
        if (force) {
            forceNextLayout = true;
        }
        requestLayoutFrame();
    }

    function onViewportChanged() {
        lastStateSyncAt = 0;
        scheduleLayout(true);
    }

    function maintainDanmakuTemporaryState() {
        if (!active || destroyed) return;
        var state = SubtitleShared.getSettings();
        var layoutMatches = !lastVisualLayout || sameWebDanmakuLayout({
            subtitlePanelBounds: state.subtitlePanelBounds,
            subtitlePanelPosition: state.subtitlePanelPosition
        }, lastVisualLayout);
        if (state.subtitlePanelLocked === true &&
            state.subtitleInteractionPassthrough === true &&
            state.subtitleOpacity === 0 &&
            layoutMatches) {
            return;
        }

        var patch = {
            subtitlePanelLocked: true,
            subtitleInteractionPassthrough: true,
            subtitleOpacity: 0
        };
        if (lastVisualLayout) {
            patch.subtitlePanelBounds = lastVisualLayout.subtitlePanelBounds;
            patch.subtitlePanelPosition = lastVisualLayout.subtitlePanelPosition;
        }
        SubtitleShared.updateSettings(patch, {
            persist: false,
            source: 'subtitle-web-danmaku-maintain'
        });
    }

    function start() {
        if (active || destroyed || !isWebSubtitleDanmakuHost()) return;
        active = true;
        snapshot = createWebDanmakuSettingsSnapshot();
        lastStateSyncAt = 0;
        lastVisualLayout = null;
        if (controller && typeof controller.closeSettingsForExternalInteraction === 'function') {
            controller.closeSettingsForExternalInteraction('clean');
        }
        var display = controller && controller.refs ? controller.refs.display : null;
        if (display) {
            previousTransition = display.style.transition;
            previousWillChange = display.style.willChange;
            display.style.transition = 'none';
            display.style.willChange = 'left, top, width, height';
        }
        maintainDanmakuTemporaryState();
        window.addEventListener('resize', onViewportChanged);
        window.addEventListener('scroll', onViewportChanged, true);
        scheduleLayout(true);
    }

    function stop() {
        if (!active) return;
        active = false;
        cancelLayoutLoop();
        window.removeEventListener('resize', onViewportChanged);
        window.removeEventListener('scroll', onViewportChanged, true);
        if (snapshot) {
            SubtitleShared.updateSettings(snapshot, {
                persist: false,
                source: 'subtitle-web-danmaku-restore'
            });
            snapshot = null;
        }
        var display = controller && controller.refs ? controller.refs.display : null;
        if (display) {
            display.style.transition = previousTransition || '';
            display.style.willChange = previousWillChange || '';
        }
        previousTransition = null;
        previousWillChange = null;
        lastVisualLayout = null;
    }

    var unsubscribe = SubtitleShared.subscribeSettings(function(state) {
        if (state && state.subtitleDanmakuMode) {
            start();
            maintainDanmakuTemporaryState();
        } else {
            stop();
        }
    }, { immediate: true });

    return function cleanupWebDanmakuModeLayout() {
        destroyed = true;
        if (typeof unsubscribe === 'function') unsubscribe();
        stop();
    };
}

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
    if (Object.prototype.hasOwnProperty.call(patch, 'userLanguage') && next.userLanguage) {
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
        subtitlePanelBounds: currentSettings ? currentSettings.subtitlePanelBounds : { width: 600, height: 68 },
        subtitlePanelPosition: currentSettings ? currentSettings.subtitlePanelPosition : null,
        subtitlePanelLocked: currentSettings ? !!currentSettings.subtitlePanelLocked : false,
        subtitleInteractionPassthrough: currentSettings ? currentSettings.subtitleInteractionPassthrough !== false : true,
        subtitleDanmakuMode: currentSettings ? !!currentSettings.subtitleDanmakuMode : false,
        subtitleFontSize: currentSettings ? currentSettings.subtitleFontSize : 26,
        subtitleColorScheme: currentSettings ? currentSettings.subtitleColorScheme : 'default'
    }, { source: source || 'subtitle-core' });
}

function hideSubtitleDisplayOnly(source) {
    const display = document.getElementById('subtitle-display');
    if (!display) return;
    clearSubtitleDanmakuLayer();
    display.classList.remove('show');
    display.classList.add('hidden');
    display.style.opacity = '0';
    syncSubtitleRenderState(source || 'subtitle-display-hidden');
}

function isSubtitleTemporarilySuppressed() {
    return subtitleSuppressedByGoodbye;
}

function isGoodbyeResourceStateActive() {
    try {
        if (typeof window.isNekoGoodbyeResourceSuspendingOrSuspended === 'function' &&
            window.isNekoGoodbyeResourceSuspendingOrSuspended()) {
            return true;
        }
        return window.goodbyeResourceSuspended === true ||
            window.__nekoGoodbyeResourceSuspendPending === true;
    } catch (_) {
        return false;
    }
}

function isSubtitleDisplayCurrentlyVisible() {
    const display = document.getElementById('subtitle-display');
    if (!display) return false;
    if (display.classList.contains('hidden')) return false;
    if (display.style.display === 'none' || display.style.opacity === '0') return false;
    try {
        const style = window.getComputedStyle ? window.getComputedStyle(display) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0')) {
            return false;
        }
    } catch (_) { /* ignore */ }
    return true;
}

function clearSubtitleTextOnly() {
    const subtitleText = document.getElementById('subtitle-text');
    if (subtitleText) {
        subtitleText.textContent = '';
        subtitleText.style.fontSize = '';
    }
    clearSubtitleDanmakuLayer();
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
let incrementalAbortController = null;
let incrementalRequestId = 0;
let incrementalQueuedCount = 0;
let incrementalTranslationQueue = [];
let incrementalTranslationActive = false;

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
    if (isSubtitleTemporarilySuppressed()) {
        hideSubtitleDisplayOnly('subtitle-goodbye-suppressed-visible-request');
        return;
    }
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
    clearSubtitleDanmakuLayer();
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
function getSubtitleDanmakuRefs() {
    if (subtitleUiController && subtitleUiController.refs) {
        return subtitleUiController.refs;
    }
    return {
        display: document.getElementById('subtitle-display'),
        scroll: document.getElementById('subtitle-scroll'),
        text: document.getElementById('subtitle-text')
    };
}

function renderSubtitleDanmakuLayer(text) {
    if (!SubtitleShared || typeof SubtitleShared.renderSubtitleDanmakuText !== 'function') return false;
    var state = typeof SubtitleShared.getSettings === 'function' ? SubtitleShared.getSettings() : null;
    var enabled = !!(state && state.subtitleDanmakuMode);
    SubtitleShared.renderSubtitleDanmakuText(getSubtitleDanmakuRefs(), text, { enabled: enabled });
    return enabled;
}

function clearSubtitleDanmakuLayer() {
    if (!SubtitleShared || typeof SubtitleShared.clearSubtitleDanmakuText !== 'function') return;
    SubtitleShared.clearSubtitleDanmakuText(getSubtitleDanmakuRefs());
}

function requestSubtitleContentAutoScroll() {
    if (SubtitleShared && typeof SubtitleShared.requestSubtitleAutoScroll === 'function') {
        SubtitleShared.requestSubtitleAutoScroll(document.getElementById('subtitle-scroll'));
    }
}

function writeSubtitleText(text) {
    const subtitleText = document.getElementById('subtitle-text');
    if (!subtitleText) return;
    subtitleText.textContent = text || '';
    subtitleText.style.fontSize = '';
    if (isSubtitleTemporarilySuppressed()) {
        clearSubtitleDanmakuLayer();
        syncSubtitleRenderState('subtitle-text-write-goodbye-suppressed');
        return;
    }
    var danmakuRendering = renderSubtitleDanmakuLayer(subtitleText.textContent);
    if (!danmakuRendering) {
        requestSubtitleContentAutoScroll();
    }
    syncSubtitleRenderState('subtitle-text-write');

    // 自适应字号：防抖测量，避免流式高频触发
    if (_subtitleFontResizeTimer) clearTimeout(_subtitleFontResizeTimer);
    if (!text || !text.trim()) {
        syncSubtitleRenderState('subtitle-text-clear');
        return;
    }
    if (danmakuRendering) return;
    _subtitleFontResizeTimer = setTimeout(function() {
        var display = document.getElementById('subtitle-display');
        if (!display) return;
        var preset = SubtitleShared && typeof SubtitleShared.getSettings === 'function'
            ? SubtitleShared.getSettings()
            : { subtitlePanelBounds: { width: display.offsetWidth || 600, height: display.offsetHeight || 68 } };
        var panelBounds = SubtitleShared && typeof SubtitleShared.getPanelBounds === 'function'
            ? SubtitleShared.getPanelBounds(preset.subtitlePanelBounds)
            : { width: display.offsetWidth || 600, height: display.offsetHeight || 68 };
        var baseFont = SubtitleShared && typeof SubtitleShared.normalizeSubtitleFontSize === 'function'
            ? SubtitleShared.normalizeSubtitleFontSize(preset.subtitleFontSize)
            : (Number(preset.subtitleFontSize) || 26);
        var layout = SubtitleShared && typeof SubtitleShared.measureSubtitleLayout === 'function'
            ? SubtitleShared.measureSubtitleLayout({
                mode: 'web',
                text: text,
                panelBounds: panelBounds,
                maxWidth: panelBounds.width,
                minHeight: panelBounds.height,
                maxHeight: panelBounds.height,
                baseFont: baseFont,
                // Keep in sync with PANEL_TEXT_HORIZONTAL_RESERVE in subtitle-shared.js.
                availableWidth: Math.max(0, (display.clientWidth || panelBounds.width) - 110),
                availableHeight: panelBounds.height
            })
            : { fontSize: baseFont };
        subtitleText.style.fontSize = layout.fontSize < baseFont ? layout.fontSize + 'px' : '';
        requestSubtitleContentAutoScroll();
        syncSubtitleRenderState('subtitle-text-resize');
    }, 200);
}

function enqueueIncrementalSentences(sentences) {
    if (!sentences || !sentences.length) return;
    for (var i = 0; i < sentences.length; i++) {
        incrementalTranslationQueue.push(sentences[i]);
    }
    processIncrementalTranslationQueue(incrementalRequestId);
}

function resetIncrementalTranslationState() {
    incrementalRequestId += 1;
    incrementalTranslatedCount = 0;
    incrementalTranslatedSentences = [];
    incrementalQueuedCount = 0;
    incrementalTranslationQueue = [];
    incrementalTranslationActive = false;
    if (incrementalAbortController) {
        incrementalAbortController.abort();
        incrementalAbortController = null;
    }
}

function cancelPendingSubtitleTranslations() {
    currentTranslationRequestId += 1;
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
        currentTranslateAbortController = null;
    }
    resetIncrementalTranslationState();
}

function resumeIncrementalTranslationQueue() {
    if (subtitleEnabled && incrementalTranslationQueue.length) {
        processIncrementalTranslationQueue(incrementalRequestId);
    }
}

function countCjkChars(text) {
    var matches = (text || '').match(/[\u1100-\u11FF\u3040-\u30FF\u31F0-\u31FF\u3400-\u9FFF\uAC00-\uD7AF\uF900-\uFAFF]/g);
    return matches ? matches.length : 0;
}

function hasUnexpectedSourceResidue(translatedText, targetLang) {
    var normalizedTarget = (targetLang || '').toLowerCase();
    if (!translatedText || normalizedTarget === 'zh' || normalizedTarget === 'ja' || normalizedTarget === 'ko') {
        return false;
    }
    return countCjkChars(translatedText) >= 4;
}

/**
 * 增量翻译核心：按发现顺序逐句翻译并追加到字幕显示。
 */
async function processIncrementalTranslationQueue(requestSnapId) {
    if (incrementalTranslationActive) return;
    if (!incrementalTranslationQueue.length) return;
    if (!subtitleEnabled) return;
    if (!isSubtitleTranslationOwner()) return;
    if (requestSnapId !== incrementalRequestId) return;

    var textToTranslate = incrementalTranslationQueue.shift();
    incrementalTranslationActive = true;

    if (incrementalAbortController) {
        incrementalAbortController.abort();
    }
    incrementalAbortController = new AbortController();
    var abortCtrl = incrementalAbortController;

    try {
        if (userLanguage === null) {
            await getUserLanguage();
        }
        if (!subtitleEnabled) return;
        if (requestSnapId !== incrementalRequestId) return;

        var targetLanguage = userLanguage !== null ? userLanguage : 'zh';
        var translateHeaders = { 'Content-Type': 'application/json' };
        var translateSec = window.nekoLocalMutationSecurity;
        if (translateSec && typeof translateSec.getMutationHeaders === 'function') {
            try { Object.assign(translateHeaders, await translateSec.getMutationHeaders()); } catch (_) { }
        }
        var response = await fetch('/api/translate', {
            method: 'POST',
            headers: translateHeaders,
            body: JSON.stringify({
                text: textToTranslate,
                target_lang: targetLanguage,
                source_lang: null
            }),
            signal: abortCtrl.signal
        });

        if (!response.ok) {
            markIncrementalSentenceHandled(requestSnapId);
            return;
        }

        var result = await response.json();
        if (requestSnapId !== incrementalRequestId) return;

        if (result.success && result.translated_text) {
            var translated = result.translated_text.trim();
            if (hasUnexpectedSourceResidue(translated, result.target_lang || targetLanguage)) {
                console.warn('字幕翻译结果仍包含源语言片段，已跳过该句。');
            } else {
                incrementalTranslatedSentences.push(translated);
            }
        }
        incrementalTranslatedCount += 1;
        updateIncrementalDisplay();
    } catch (error) {
        if (error.name === 'AbortError') return;
        markIncrementalSentenceHandled(requestSnapId);
    } finally {
        if (incrementalAbortController === abortCtrl) {
            incrementalAbortController = null;
        }
        incrementalTranslationActive = false;
        if (requestSnapId === incrementalRequestId && incrementalTranslationQueue.length) {
            processIncrementalTranslationQueue(requestSnapId);
        }
    }
}

function markIncrementalSentenceHandled(requestSnapId) {
    if (requestSnapId !== incrementalRequestId) return;
    incrementalTranslatedCount += 1;
    updateIncrementalDisplay();
}

function updateIncrementalDisplay() {
    if (!subtitleEnabled) return;
    var fullText = incrementalTranslatedSentences.join(' ');
    writeSubtitleText(fullText);
    ensureSubtitleVisibleIfEnabled();
}

function showSubtitleWithoutOriginalAndRestartCurrentTurn() {
    if (!isSubtitleTranslationOwner()) {
        syncSubtitleRenderState('subtitle-non-owner-skip-show');
        return;
    }
    if (isSubtitleTemporarilySuppressed() || subtitleDropCurrentTurnUntilNextStart) {
        hideSubtitleDisplayOnly('subtitle-goodbye-drop-current-turn');
        return;
    }

    if (currentTurnIsStructured) {
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(getStructuredPlaceholder());
        return;
    }

    var incrementalText = incrementalTranslatedSentences.join(' ');
    if (incrementalText.trim()) {
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(incrementalText);
    } else {
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText('');
    }
    resumeIncrementalTranslationQueue();

    if (!(currentTurnOriginalText && currentTurnOriginalText.trim())) {
        return;
    }
    if (isCurrentTurnFinalized) {
        translateAndShowSubtitle(currentTurnOriginalText);
        return;
    }
    updateSubtitleStreamingText(currentTurnOriginalText);
}

function suppressSubtitleForGoodbye() {
    if (!isSubtitleTranslationOwner()) {
        return;
    }
    if (!subtitleSuppressedByGoodbye) {
        subtitleWasVisibleBeforeGoodbye = isSubtitleDisplayCurrentlyVisible();
        subtitleWindowWasVisibleBeforeGoodbye = subtitleWasVisibleBeforeGoodbye;
    }
    subtitleSuppressedByGoodbye = true;
    subtitleDropCurrentTurnUntilNextStart = true;
    cancelPendingSubtitleTranslations();
    clearSubtitleTextOnly();
    hideSubtitleDisplayOnly('subtitle-goodbye-suppress');
    try {
        if (window.nekoSubtitleWindow && typeof window.nekoSubtitleWindow.hide === 'function') {
            window.nekoSubtitleWindow.hide();
        }
    } catch (_) { /* ignore */ }
}

function restoreSubtitleAfterGoodbye(options) {
    if (!subtitleSuppressedByGoodbye) {
        return;
    }
    if (isGoodbyeResourceStateActive()) {
        return;
    }
    subtitleSuppressedByGoodbye = false;
    cancelPendingSubtitleTranslations();
    clearSubtitleTextOnly();
    const restoreWindow = !options || options.restoreWindow !== false;
    const shouldRestoreVisible = subtitleEnabled && subtitleWasVisibleBeforeGoodbye;
    if (shouldRestoreVisible) {
        ensureSubtitleVisibleIfEnabled();
        if (restoreWindow && subtitleWindowWasVisibleBeforeGoodbye) {
            try {
                if (window.nekoSubtitleWindow && typeof window.nekoSubtitleWindow.show === 'function') {
                    window.nekoSubtitleWindow.show();
                }
            } catch (_) { /* ignore */ }
        }
    } else {
        syncSubtitleRenderState('subtitle-goodbye-restore-disabled');
        hideSubtitleDisplayOnly('subtitle-goodbye-restore-hidden');
    }
    subtitleWasVisibleBeforeGoodbye = false;
    subtitleWindowWasVisibleBeforeGoodbye = false;
}

function bindGoodbyeSubtitleVisibility() {
    window.addEventListener('live2d-goodbye-click', suppressSubtitleForGoodbye);
    var returnEvents = [
        'live2d-return-click',
        'vrm-return-click',
        'mmd-return-click',
        'pngtuber-return-click'
    ];
    returnEvents.forEach(function(eventName) {
        window.addEventListener(eventName, restoreSubtitleAfterGoodbye);
    });
    window.addEventListener('neko:goodbye-state-cleared', restoreSubtitleAfterGoodbye);
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
    if (isSubtitleTemporarilySuppressed() || subtitleDropCurrentTurnUntilNextStart) return;

    var cleaned = (text || '').toString();
    currentTurnOriginalText = cleaned;

    if (!subtitleEnabled) return;
    if (!isSubtitleTranslationOwner()) return;
    if (userLanguage === null) getUserLanguage();

    var splitResult = splitSubtitleSentences(cleaned);
    var allSentences = splitResult.sentences;
    var rest = splitResult.rest;
    var newCount = allSentences.length - incrementalQueuedCount;

    if (newCount > 0) {
        var newSentences = allSentences.slice(incrementalQueuedCount);
        incrementalQueuedCount = allSentences.length;
        enqueueIncrementalSentences(newSentences);
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
    if (isSubtitleTemporarilySuppressed() || subtitleDropCurrentTurnUntilNextStart) return;
    resetIncrementalTranslationState();
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
        currentTranslateAbortController = null;
    }
    currentTurnIsStructured = true;
    const placeholder = getStructuredPlaceholder();
    currentTurnOriginalText = placeholder;
    if (!subtitleEnabled) return;
    writeSubtitleText(placeholder);
    ensureSubtitleVisibleIfEnabled();
}

/**
 * turn_end 终态收尾（结构化版）：标记 finalize，只显示 [markdown] 占位，
 * 不发翻译请求。等价于 translateAndShowSubtitle 的结构化分支。
 */
function finalizeSubtitleAsStructured() {
    if (isSubtitleTemporarilySuppressed() || subtitleDropCurrentTurnUntilNextStart) {
        isCurrentTurnFinalized = true;
        cancelPendingSubtitleTranslations();
        clearSubtitleTextOnly();
        return;
    }
    isCurrentTurnFinalized = true;
    currentTurnIsStructured = true;
    resetIncrementalTranslationState();
    if (currentTranslateAbortController) {
        currentTranslateAbortController.abort();
        currentTranslateAbortController = null;
    }
    const placeholder = getStructuredPlaceholder();
    currentTurnOriginalText = placeholder;
    if (!subtitleEnabled) return;
    writeSubtitleText(placeholder);
    ensureSubtitleVisibleIfEnabled();
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
    resetIncrementalTranslationState();
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
function beginSubtitleTurn(options) {
    subtitleDropCurrentTurnUntilNextStart = isSubtitleTemporarilySuppressed();
    resetSubtitleTurnState();
    const skipLatch = !!(options && options.latch === false);
    turnBoundaryLatched = !skipLatch;
}

/**
 * 'neko-assistant-turn-start' 事件处理：
 *   - 如果 isNewMessage 路径已经 beginSubtitleTurn 过（闸门为真），只同步
 *     显示可见性，不再二次抹字幕 —— 否则会把首个 chunk 已经写好的文本擦掉。
 *   - 反之（事件在没有前置 isNewMessage 的通道上独立到达）走完整复位路径。
 */
function onAssistantTurnStart() {
    subtitleDropCurrentTurnUntilNextStart = isSubtitleTemporarilySuppressed();
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
    // 开关开启时保留显示框；译文尚未产生前沿用旧链路的空白等待。
    if (subtitleEnabled) {
        writeSubtitleText('');
        ensureSubtitleVisibleIfEnabled();
    } else {
        hideSubtitle();
    }
}

/**
 * Turn 结束时调用：把剩余未入队文本补进逐句翻译队列。
 */
async function translateAndShowSubtitle(text) {
    if (!text || !text.trim()) {
        return;
    }
    if (isSubtitleTemporarilySuppressed() || subtitleDropCurrentTurnUntilNextStart) {
        isCurrentTurnFinalized = true;
        cancelPendingSubtitleTranslations();
        clearSubtitleTextOnly();
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
    if (!isSubtitleTranslationOwner()) {
        return;
    }

    var splitResult = splitSubtitleSentences(text);
    var finalSentences = splitResult.sentences.slice();
    if (splitResult.rest && splitResult.rest.trim()) {
        finalSentences.push(splitResult.rest.trim());
    }
    var remainingSentences = finalSentences.slice(incrementalQueuedCount);
    if (remainingSentences.length) {
        incrementalQueuedCount = finalSentences.length;
        enqueueIncrementalSentences(remainingSentences);
        return;
    }
    if (!incrementalTranslationActive && incrementalTranslationQueue.length) {
        resumeIncrementalTranslationQueue();
        return;
    }
    if (!incrementalTranslationActive && !incrementalTranslationQueue.length) {
        ensureSubtitleVisibleIfEnabled();
        writeSubtitleText(incrementalTranslatedSentences.join(' '));
    }
}

function initSubtitleHostUi() {
    if (subtitleUiController || !SubtitleShared || typeof SubtitleShared.initSubtitleUI !== 'function') {
        return subtitleUiController;
    }
    subtitleUiController = SubtitleShared.initSubtitleUI({
        host: 'web',
        onClose: function() {
            if (window.subtitleBridge && typeof window.subtitleBridge.setSubtitleEnabled === 'function') {
                window.subtitleBridge.setSubtitleEnabled(false, {
                    source: 'subtitle-ui-close'
                });
            }
            if (window.appSettings && typeof window.appSettings.saveSettings === 'function') {
                window.appSettings.saveSettings();
            }
        },
        onLanguageChange: function(lang) {
            if (window.subtitleBridge && typeof window.subtitleBridge.setUserLanguage === 'function') {
                window.subtitleBridge.setUserLanguage(lang);
            }
        },
        onSettingsApplied: function(state, refs, detail) {
            var changedKeys = detail && Array.isArray(detail.changedKeys) ? detail.changedKeys : [];
            var shouldRemeasureText = detail && (
                detail.source === 'subtitle-ui-resize' ||
                detail.source === 'subtitle-ui-font-size' ||
                detail.source === 'subtitle-web-danmaku-restore' ||
                changedKeys.indexOf('subtitleDanmakuMode') !== -1 ||
                changedKeys.indexOf('subtitleFontSize') !== -1
            );
            if (shouldRemeasureText && refs && refs.text && refs.text.textContent) {
                writeSubtitleText(refs.text.textContent);
            }
            syncSubtitleRenderState(detail && detail.source ? detail.source : 'subtitle-ui-apply');
        }
    });
    if (subtitleUiController && !webDanmakuModeSettingsCleanup) {
        webDanmakuModeSettingsCleanup = attachWebDanmakuModeLayout(subtitleUiController);
        var baseDestroy = subtitleUiController.destroy;
        subtitleUiController.destroy = function() {
            if (webDanmakuModeSettingsCleanup) {
                webDanmakuModeSettingsCleanup();
                webDanmakuModeSettingsCleanup = null;
            }
            if (typeof baseDestroy === 'function') {
                return baseDestroy.apply(subtitleUiController, arguments);
            }
            return undefined;
        };
    }
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
    resetIncrementalTranslationState();
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

async function initSubtitleAfterStorageBarrier() {
    if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
        try {
            await window.waitForStorageLocationStartupBarrier();
        } catch (_) {}
    } else if (window.__nekoStorageLocationStartupBarrier
        && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
        try {
            await window.__nekoStorageLocationStartupBarrier;
        } catch (_) {}
    }

    initSubtitleHostUi();
    await getUserLanguage();
    syncSettingsPanel();
    if (subtitleEnabled) {
        showSubtitleWithoutOriginalAndRestartCurrentTurn();
    }
    syncSubtitleRenderState('subtitle-dom-ready');
    window.addEventListener('neko-assistant-turn-start', onAssistantTurnStart);
    bindGoodbyeSubtitleVisibility();

    // 通用引导管理器：index.html / chat.html 都加载 subtitle.js，
    // 但自身模板没有 init 调用，历史上靠这里兜底（其他子页面模板各自 init）。
    // 幂等保护防止跨页面重复 init。
    if (!window.__universalTutorialManagerInitialized &&
        typeof initUniversalTutorialManager === 'function') {
        try {
            const initialized = await initUniversalTutorialManager();
            if (initialized !== false) {
                window.__universalTutorialManagerInitialized = true;
                console.log('[App] 通用引导管理器已初始化');
            }
        } catch (error) {
            console.error('[App] 通用引导管理器初始化失败:', error);
        }
    }
}

// 初始化字幕模块（DOM 就绪后绑定拖拽 & turn 事件）
document.addEventListener('DOMContentLoaded', function() {
    initSubtitleAfterStorageBarrier();
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
    /** 同步开启状态并执行显示/隐藏副作用（用于服务器设置回灌和窗口控制） */
    setSubtitleEnabled: function(enabled, options) {
        var requestOptions = options || {};
        subtitleEnabled = !!enabled;
        applySharedSubtitleSettings({
            subtitleEnabled: subtitleEnabled
        }, {
            persist: requestOptions.persist !== false,
            source: requestOptions.source || 'subtitle-bridge-set-enabled'
        });

        if (subtitleEnabled) {
            showSubtitleWithoutOriginalAndRestartCurrentTurn();
        } else {
            cancelPendingSubtitleTranslations();
            hideSubtitle();
        }
        syncSettingsPanel();
        syncSubtitleRenderState(requestOptions.source || 'subtitle-bridge-set-enabled');
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
            cancelPendingSubtitleTranslations();
            hideSubtitle();
        } else {
            showSubtitleWithoutOriginalAndRestartCurrentTurn();
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
    finalizeTurnWithTranslation: translateAndShowSubtitle,
    suspendForGoodbye: function() {
        suppressSubtitleForGoodbye();
    },
    restoreAfterGoodbye: function(options) {
        restoreSubtitleAfterGoodbye(options || {});
    },
    cancelPendingTranslations: cancelPendingSubtitleTranslations,
    isSuppressedByGoodbye: function() {
        return subtitleSuppressedByGoodbye;
    },
    wasVisibleBeforeGoodbye: function() {
        return subtitleWasVisibleBeforeGoodbye || subtitleWindowWasVisibleBeforeGoodbye;
    }
};

// 向后兼容：保留全局函数名，但函数体已经精简
window.translateAndShowSubtitle = translateAndShowSubtitle;
window.updateSubtitleStreamingText = updateSubtitleStreamingText;
window.beginSubtitleTurn = beginSubtitleTurn;
window.markSubtitleStructured = markSubtitleStructured;
window.finalizeSubtitleAsStructured = finalizeSubtitleAsStructured;
window.getUserLanguage = getUserLanguage;
