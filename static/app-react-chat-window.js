/**
 * app-react-chat-window.js
 * Host-side controller for the exported React chat window.
 * - Dynamically loads the React bundle if needed
 * - Owns window open/close/minimize/drag state
 * - Owns chat view props + messages state
 * - Exposes a stable bridge for host code / IPC adapters
 */
(function () {
    'use strict';

    var BUNDLE_SRC = '/static/react/neko-chat/neko-chat-window.iife.js';
    var STORAGE_LEFT_KEY = 'neko.reactChatWindow.left';
    var STORAGE_TOP_KEY = 'neko.reactChatWindow.top';
    var STORAGE_WIDTH_KEY = 'neko.reactChatWindow.width';
    var STORAGE_HEIGHT_KEY = 'neko.reactChatWindow.height';
    var GALGAME_STORAGE_KEY = 'neko.reactChatWindow.galgameMode';
    var CHAT_SURFACE_MODE_STORAGE_KEY = 'neko.reactChatWindow.chatSurfaceMode';
    var GALGAME_HISTORY_LIMIT = 6;
    var EVENT_PREFIX = 'react-chat-window:';

    var loadedPromise = null;
    var mounted = false;
    var dragState = null;
    var resizeState = null;
    var minimized = false;
    var savedShellSize = null;
    var savedShellPosition = null; // {left, top} before minimize – used to fly back on expand
    var HOME_IDLE_DOCK_GAP = 12;
    var IDLE_DOCK_TIER_NONE = 'none';
    var IDLE_DOCK_TIER_CAT2 = 'cat2';
    var IDLE_DOCK_TIER_CAT3 = 'cat3';
    var idleDockTier = IDLE_DOCK_TIER_NONE;
    var idleDockActive = false;
    var idleDockSavedPosition = null;
    var idleDockTriggeredMinimize = false;
    var idleDockMinimizeObserver = null;
    var idleDockContainerObserver = null;
    var idleDockSyncFrame = 0;
    var electronIdleDockActive = false;
    var electronIdleDockTriggeredCollapse = false;
    var electronIdleDockSavedBounds = null;
    var electronIdleDockLastScreenRect = null;
    var electronIdleDockEntering = false;
    var electronIdleDockRetryTimer = 0;
    var electronIdleDockDesired = false;
    var electronIdleDockGeneration = 0;
    var electronIdleDockPositionFrame = 0;
    var electronIdleDockPositionSeq = 0;
    var electronIdleDockCurrentBounds = null;
    var electronIdleDockWorkArea = null;
    var electronChatMinimizedStateFrame = 0;
    var electronChatMinimizedStateTimer = 0;
    var electronChatMinimizedStateSignature = '';
    var electronChatMinimizedStatePublishedAt = 0;
    var electronCat1PairMoveBoundsFrame = 0;
    var electronCat1PairMovePendingBounds = null;
    var ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS = 1000;
    var savedExpandedShellPosition = null; // last known full-surface desktop position
    var lastRestorableChatSurfaceMode = 'compact';
    var _sortKeySeq = 0; // monotonically increasing sortKey counter
    var COMPACT_CHAT_STATES = ['default', 'options', 'input'];
    var CHAT_SURFACE_MODE_SEQUENCE = ['compact', 'minimized'];

    var state = {
        viewProps: null,
        messages: [],
        composerAttachments: [],
        composerHidden: false,
        onMessageAction: null,
        onComposerImportImage: null,
        onComposerScreenshot: null,
        onComposerRemoveAttachment: null,
        onComposerSubmit: null,
        onCompactHistoryDrop: null,
        onCompactHistoryDragStateChange: null,
        onAvatarInteraction: null,
        onAvatarToolStateChange: null,
        pendingRollbackDrafts: Object.create(null),
        rollbackDraft: '',
        _toolCursorResetKey: '',
        compactChatState: 'default',
        chatSurfaceMode: 'compact',
        // Off until init() reads the persisted preference post-barrier and
        // calls setGalgameModeEnabled(true) — that path fires the
        // galgame-mode-change event, which is the only signal chat.html's
        // syncWindowToMinH uses to bump Electron window height.
        // Defaulting to true here would leave saved-OFF users permanently
        // bumped: chat.html's listener only ever grows the window.
        galgameModeEnabled: false,
        galgameOptions: [],
        galgameOptionsLoading: false,
        galgameTemporarilyDisabled: false,
        homeTutorialInteractionLocked: false,
        _galgameRequestSeq: 0,
        // 通用 ChoicePrompt 框架（PR #1141 follow-up #2）。当前承载 mini_game_invite
        // 三选项；galgame mode 仍走 galgameOptions 路径（BC，渐进迁移）。
        // shape: { source: 'mini_game_invite', sessionId, gameType, options: [{choice,label}] } | null
        choicePrompt: null,
        // dedupe set：已经 window.open 过的 mini-game session_id。键集，行为按 set 用。
        // 防止 endpoint 路径 + WS push 路径同一 session 双开窗口。
        _launchedMiniGameSessionIds: Object.create(null)
    };

    function normalizeChatSurfaceMode(mode) {
        if (mode === 'full') return 'compact';
        return CHAT_SURFACE_MODE_SEQUENCE.indexOf(mode) >= 0 ? mode : 'compact';
    }

    function normalizeCompactChatState(mode) {
        return COMPACT_CHAT_STATES.indexOf(mode) >= 0 ? mode : 'default';
    }

    function getCurrentChatSurfaceMode() {
        return normalizeChatSurfaceMode(state.chatSurfaceMode);
    }

    function getCurrentCompactChatState() {
        return normalizeCompactChatState(state.compactChatState);
    }

    function isHomeCompactSurfaceRoute() {
        var body = document.body;
        return !!(
            body
            && body.classList.contains('subtitle-web-host')
            && getCurrentChatSurfaceMode() === 'compact'
        );
    }

    function isDesktopHomeCompactSurfaceRoute() {
        var body = document.body;
        return !!(
            isElectronChatWindow()
            && body
            && body.classList.contains('subtitle-web-host')
            && document.querySelector('.compact-chat-surface-shell')
        );
    }

    function getNextChatSurfaceMode(mode) {
        var normalized = normalizeChatSurfaceMode(mode);
        if (normalized === 'minimized') {
            return normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);
        }
        var currentIndex = CHAT_SURFACE_MODE_SEQUENCE.indexOf(normalized);
        var nextIndex = currentIndex >= 0
            ? (currentIndex + 1) % CHAT_SURFACE_MODE_SEQUENCE.length
            : 0;
        return CHAT_SURFACE_MODE_SEQUENCE[nextIndex];
    }

    function resetCompactChatState() {
        state.compactChatState = 'default';
    }

    function shouldPersistChatSurfaceModePreference() {
        // Desktop and web share the same page state contract. The caller only
        // persists compact only; minimized still restores to the last real surface.
        return true;
    }

    function readChatSurfaceModePreference() {
        if (!shouldPersistChatSurfaceModePreference()) {
            return 'compact';
        }
        try {
            var raw = localStorage.getItem(CHAT_SURFACE_MODE_STORAGE_KEY);
            // The storage key only ever holds the restorable surface ('compact').
            // Migrate any legacy value persisted by the old three-state build
            // ('full') or a stray 'minimized' back to 'compact' so stale values
            // don't linger in storage. Any other value (or first run) just
            // resolves to 'compact' without an extra write.
            if (raw === 'full' || raw === 'minimized') {
                localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, 'compact');
            }
            return 'compact';
        } catch (_) {
            return 'compact';
        }
    }

    function persistChatSurfaceModePreference(mode) {
        if (mode !== 'compact') return;
        if (!shouldPersistChatSurfaceModePreference()) return;
        try {
            localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, mode);
        } catch (_) {}
    }

    function readGalgameModePreference() {
        try {
            var raw = localStorage.getItem(GALGAME_STORAGE_KEY);
            if (raw === null) return true; // default ON per spec
            return raw === 'true';
        } catch (_) {
            return true;
        }
    }

    function persistGalgameModePreference(enabled) {
        try {
            localStorage.setItem(GALGAME_STORAGE_KEY, enabled ? 'true' : 'false');
        } catch (_) {}
    }

    // composer 隐藏（请她离开）时强制视为 OFF：保留 state.galgameModeEnabled，
    // 但摘掉 body class，让 chat.html / preload-chat-react 里依赖该 class 的
    // 高最小高度 / 窗口最小高度 CSS 不再撑住空白输入区。
    // body class 切换、change 事件 payload 都走这个 helper，避免逻辑分叉。
    function getEffectiveGalgameEnabled() {
        return !!state.galgameModeEnabled && !state.composerHidden;
    }

    function applyGalgameBodyClass() {
        if (typeof document === 'undefined' || !document.body) return;
        document.body.classList.toggle('galgame-mode-enabled', getEffectiveGalgameEnabled());
    }

    // 镜像 galgame 的 body class 策略：附件区（截图 / 导入图片）出现时贴
    // body 上 composer-has-attachments，让 chat.html 的 min-height 兜底和
    // preload-chat-react.js 的 Electron resize 下限同时感知。否则附件直接
    // 把 .composer-input 顶出可视区域 —— galgame 的 385px 兜底不覆盖它。
    function applyAttachmentsBodyClass(hasAttachments) {
        if (typeof document === 'undefined' || !document.body) return;
        document.body.classList.toggle('composer-has-attachments', !!hasAttachments);
    }
    // No module-eval apply: state defaults to off here; init() resolves the
    // persisted preference and calls setGalgameModeEnabled(...) which flips
    // the class and fires the change event chat.html listens to.

    var MOBILE_MAX_HEIGHT_RATIO = 0.85;
    var MOBILE_MESSAGE_MIN_HEIGHT = 60;
    var DESKTOP_DEFAULT_LEFT_RATIO = 0.05;
    var MOBILE_MIN_HEIGHT = 150;
    var MOBILE_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.mobileHeight';
    var MOBILE_EXPAND_CLICK_GUARD_MS = 700;
    var MOBILE_EXPAND_CLICK_GUARD_RADIUS = 24;
    var MOBILE_EXPAND_VISUAL_GUARD_MS = 900;
    var COMPACT_MINIMIZE_BALL_VIEWPORT_PAD = 12;
    var COMPACT_MINIMIZE_BALL_AVATAR_GAP = 12;
    var COMPACT_MINIMIZE_BALL_AVATAR_VERTICAL_RATIO = 0.58;
    var COMPACT_SURFACE_MAX_WIDTH = 430;
    var COMPACT_SURFACE_RESIZE_MAX_WIDTH = 720;
    var COMPACT_SURFACE_VIEWPORT_PAD_X = 16;
    var COMPACT_SURFACE_VIEWPORT_PAD_TOP = 12;
    var COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM = 18;
    var COMPACT_SURFACE_DEFAULT_HEIGHT = 64;
    var COMPACT_SURFACE_AVATAR_VERTICAL_RATIO = 0.72;
    var COMPACT_SURFACE_POSITION_STORAGE_KEY = 'neko.reactChatWindow.compactSurfacePosition';
    var mobileUserHeight = 0; // 用户手动设置的手机端高度（0 = 自动）
    var mobileLayoutFrame = 0;
    var mobileExpandClickGuard = null;
    var mobileExpandVisualGuardTimer = 0;
    var compactMinimizeBallFrame = 0;
    var compactMinimizeBallSnapshot = '';
    var compactSurfaceAnchorSnapshot = '';
    var compactDesktopSurfaceAnchorSnapshot = '';
    var compactInteractionGeometrySnapshot = '';
    var compactSurfaceAnchorLocked = false;
    var compactSurfacePendingModelOpen = false;
    var compactSurfaceResizeSession = null;
    var compactSurfaceDesktopResizeActive = false;

    function normalizeCompactDesktopRect(raw) {
        if (!raw) return null;
        var left = Number(raw.left);
        var top = Number(raw.top);
        var width = Number(raw.width);
        var height = Number(raw.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return null;
        }
        if (width <= 0 || height <= 0) return null;
        return {
            left: left,
            top: top,
            width: width,
            height: height,
            right: Number.isFinite(Number(raw.right)) ? Number(raw.right) : left + width,
            bottom: Number.isFinite(Number(raw.bottom)) ? Number(raw.bottom) : top + height
        };
    }

    function serializeCompactSurfaceRectSnapshot(rect) {
        var normalized = normalizeCompactDesktopRect(rect);
        if (!normalized) return '';
        return [
            Math.round(normalized.left),
            Math.round(normalized.top),
            Math.round(normalized.width),
            Math.round(normalized.height)
        ].join(':');
    }

    function getCompactDesktopLayoutAnchorSnapshot(layout) {
        if (!layout) return '';
        var anchorVersion = Number(layout.anchorVersion);
        if (Number.isFinite(anchorVersion)) {
            return 'version:' + Math.round(anchorVersion);
        }
        var screenSnapshot = serializeCompactSurfaceRectSnapshot(layout.surfaceScreenRect);
        if (screenSnapshot) return 'screen:' + screenSnapshot;
        var pageSnapshot = serializeCompactSurfaceRectSnapshot(layout.surface);
        return pageSnapshot ? 'page:' + pageSnapshot : '';
    }

    function handleDesktopCompactLayoutChange(layout) {
        var nextAnchorSnapshot = getCompactDesktopLayoutAnchorSnapshot(layout);
        var baseAnchorChanged = false;
        if (!nextAnchorSnapshot) {
            baseAnchorChanged = !!compactDesktopSurfaceAnchorSnapshot || !layout;
            compactDesktopSurfaceAnchorSnapshot = '';
        } else if (nextAnchorSnapshot !== compactDesktopSurfaceAnchorSnapshot) {
            baseAnchorChanged = true;
            compactDesktopSurfaceAnchorSnapshot = nextAnchorSnapshot;
        }
        if (baseAnchorChanged && !compactSurfaceDesktopResizeActive) {
            compactSurfaceAnchorLocked = false;
            compactSurfaceAnchorSnapshot = '';
        }
        scheduleCompactMinimizeBallTracking();
    }

    function normalizeCompactDesktopWorkArea(raw) {
        if (!raw) return null;
        var left = Number.isFinite(Number(raw.left)) ? Number(raw.left) : Number(raw.x);
        var top = Number.isFinite(Number(raw.top)) ? Number(raw.top) : Number(raw.y);
        var width = Number(raw.width);
        var height = Number(raw.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return null;
        }
        if (width <= 0 || height <= 0) return null;
        return {
            left: left,
            top: top,
            width: width,
            height: height,
            right: left + width,
            bottom: top + height
        };
    }

    function normalizeCompactDesktopWindowBounds(raw) {
        var area = normalizeCompactDesktopWorkArea(raw);
        if (!area) return null;
        return {
            x: area.left,
            y: area.top,
            width: area.width,
            height: area.height,
            left: area.left,
            top: area.top,
            right: area.right,
            bottom: area.bottom
        };
    }

    function getElectronCompactLayoutOverride() {
        if (!isElectronChatWindow()) return null;
        var layout = window.__nekoDesktopCompactLayout;
        if (!layout) return null;
        var surface = normalizeCompactDesktopRect(layout.surface);
        if (!surface) return null;
        var surfaceScreenRect = normalizeCompactDesktopRect(layout.surfaceScreenRect);
        var ball = normalizeCompactDesktopRect(layout.ball);
        var workArea = normalizeCompactDesktopWorkArea(layout.workArea);
        var windowBounds = normalizeCompactDesktopWindowBounds(layout.windowBounds);
        var compactChoicePlacement = layout.compactChoicePlacement === 'above' || layout.compactChoicePlacement === 'below'
            ? layout.compactChoicePlacement
            : null;
        return {
            surface: surface,
            surfaceScreenRect: surfaceScreenRect,
            ball: ball,
            workArea: workArea,
            windowBounds: windowBounds,
            compactChoicePlacement: compactChoicePlacement
        };
    }

    function getMobileMaxHeight() {
        return Math.max(MOBILE_MIN_HEIGHT, Math.floor(window.innerHeight * MOBILE_MAX_HEIGHT_RATIO));
    }

    function $(id) {
        return document.getElementById(id);
    }

    function isElectronChatWindow() {
        // chat.html 用 <body class="electron-chat-window">；Electron 独立聊天窗口。
        // 本 PR 的所有移动端改动都必须对它无感，作为显式隔离在全局 touch 处理里用来短路。
        return !!(document.body && document.body.classList.contains('electron-chat-window'));
    }

    function isMobileWidth() {
        // chat.html 是 Electron 独立窗口，始终按 PC 行为处理（即使用户把窗口拖窄到 <768px），
        // 通过 <body class="electron-chat-window"> 从"手机端布局"中排除。
        if (isElectronChatWindow()) {
            return false;
        }
        // index.html 的 Electron Pet 窗口同理：永不进入手机模式（黑背景 + 窄布局）。
        // 标记 __LANLAN_IS_ELECTRON_PET__ 由 index.html 头部脚本同步注入。
        if (window.__LANLAN_IS_ELECTRON_PET__) {
            return false;
        }
        return window.innerWidth <= 768;
    }

    function isCompactHomeMinimizeBallEnabled() {
        var overlay = getOverlay();
        return !!(
            isHomeCompactSurfaceRoute()
            && overlay
            && !overlay.hidden
            && !minimized
        );
    }

    function isElectronCompactExternalBallEnabled() {
        return !!(isElectronChatWindow() && window.__nekoDesktopCompactExternalBall);
    }

    function isHomeCompactMinimizeBallRoute() {
        var overlay = getOverlay();
        var body = document.body;
        return !!(
            body
            && body.classList.contains('subtitle-web-host')
            && overlay
            && !overlay.hidden
        );
    }

    function getCompactMinimizeBallAvatarBounds() {
        if (isElectronChatWindow()) {
            return normalizeCompactDesktopRect(window.__nekoDesktopAvatarBounds);
        }

        var managers = [
            window.live2dManager,
            window.vrmManager,
            window.mmdManager
        ];
        for (var i = 0; i < managers.length; i += 1) {
            var manager = managers[i];
            if (!manager || !manager.currentModel || typeof manager.getModelScreenBounds !== 'function') continue;
            if (manager === window.mmdManager && !manager.currentModel.mesh) continue;
            try {
                var bounds = normalizeCompactDesktopRect(manager.getModelScreenBounds());
                if (bounds) return bounds;
            } catch (_) {}
        }
        return null;
    }

    function getCompactMinimizeBallPlacement(bounds) {
        var normalized = normalizeCompactDesktopRect(bounds);
        if (!normalized) return null;
        var left = normalized.left - MINIMIZED_SIZE - COMPACT_MINIMIZE_BALL_AVATAR_GAP;
        var top = normalized.top + normalized.height * COMPACT_MINIMIZE_BALL_AVATAR_VERTICAL_RATIO - MINIMIZED_SIZE / 2;
        var maxLeft = Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, window.innerWidth - MINIMIZED_SIZE - COMPACT_MINIMIZE_BALL_VIEWPORT_PAD);
        var maxTop = Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, window.innerHeight - MINIMIZED_SIZE - COMPACT_MINIMIZE_BALL_VIEWPORT_PAD);
        return {
            width: MINIMIZED_SIZE,
            height: MINIMIZED_SIZE,
            left: Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, Math.min(Math.round(left), maxLeft)),
            top: Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, Math.min(Math.round(top), maxTop))
        };
    }

    function getCompactMinimizeBallTarget() {
        if (!isHomeCompactMinimizeBallRoute()) {
            return null;
        }
        if (isElectronCompactExternalBallEnabled()) {
            return null;
        }

        var avatarBounds = getCompactMinimizeBallAvatarBounds();
        var avatarPlacement = getCompactMinimizeBallPlacement(avatarBounds);
        if (avatarPlacement) {
            window.__nekoCompactMinimizeBallFallbackActive = false;
            return avatarPlacement;
        }

        window.__nekoCompactMinimizeBallFallbackActive = true;
        return {
            width: MINIMIZED_SIZE,
            height: MINIMIZED_SIZE,
            left: COMPACT_MINIMIZE_BALL_VIEWPORT_PAD,
            top: Math.max(
                COMPACT_MINIMIZE_BALL_VIEWPORT_PAD,
                window.innerHeight - MINIMIZED_SIZE - 34
            )
        };
    }

    function shouldDelayCompactSurfaceOpenForModel() {
        return false;
    }

    function getCompactSurfaceResizeMaxWidth() {
        return Math.max(
            COMPACT_SURFACE_MAX_WIDTH,
            Math.min(COMPACT_SURFACE_RESIZE_MAX_WIDTH, window.innerWidth - (COMPACT_SURFACE_VIEWPORT_PAD_X * 2))
        );
    }

    function getCompactSurfaceMetrics() {
        var shell = getShell();
        var rect = getCompactSurfaceBaseRect() || (shell ? normalizeCompactDomRect(shell.getBoundingClientRect()) : null);
        var defaultWidth = isMobileWidth()
            ? Math.max(280, window.innerWidth - 16)
            : Math.min(COMPACT_SURFACE_MAX_WIDTH, Math.max(280, window.innerWidth - (COMPACT_SURFACE_VIEWPORT_PAD_X * 2)));
        var measuredWidth = rect && rect.width > 0 ? rect.width : 0;
        var storedWidth = loadCompactSurfaceStoredWidth();
        var width = Math.round(Math.min(
            Math.max(defaultWidth, measuredWidth, storedWidth || 0),
            getCompactSurfaceResizeMaxWidth()
        ));
        var height = rect && rect.height > 0 ? rect.height : COMPACT_SURFACE_DEFAULT_HEIGHT;
        return {
            width: width,
            height: height
        };
    }

    function clampCompactSurfacePosition(left, top, metrics) {
        var width = metrics.width || COMPACT_SURFACE_MAX_WIDTH;
        var height = metrics.height || COMPACT_SURFACE_DEFAULT_HEIGHT;
        var layoutOverride = getElectronCompactLayoutOverride();
        if (layoutOverride && layoutOverride.windowBounds && layoutOverride.workArea) {
            var windowBounds = layoutOverride.windowBounds;
            var workArea = layoutOverride.workArea;
            var screenLeft = windowBounds.x + left;
            var screenTop = windowBounds.y + top;
            var screenMinLeft = workArea.left + COMPACT_SURFACE_VIEWPORT_PAD_X;
            var screenMaxLeft = Math.max(screenMinLeft, workArea.right - width - COMPACT_SURFACE_VIEWPORT_PAD_X);
            var screenMinTop = workArea.top + COMPACT_SURFACE_VIEWPORT_PAD_TOP;
            var screenMaxTop = Math.max(screenMinTop, workArea.bottom - height - COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM);
            return {
                left: Math.max(screenMinLeft, Math.min(screenLeft, screenMaxLeft)) - windowBounds.x,
                top: Math.max(screenMinTop, Math.min(screenTop, screenMaxTop)) - windowBounds.y
            };
        }
        var minLeft = isMobileWidth() ? 8 : COMPACT_SURFACE_VIEWPORT_PAD_X;
        var maxLeft = Math.max(minLeft, window.innerWidth - width - minLeft);
        var maxTop = Math.max(
            COMPACT_SURFACE_VIEWPORT_PAD_TOP,
            window.innerHeight - height - COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM
        );
        return {
            left: Math.max(minLeft, Math.min(left, maxLeft)),
            top: Math.max(COMPACT_SURFACE_VIEWPORT_PAD_TOP, Math.min(top, maxTop))
        };
    }

    function loadCompactSurfacePosition(metrics) {
        try {
            var raw = window.localStorage.getItem(COMPACT_SURFACE_POSITION_STORAGE_KEY);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            var left = Number(parsed && parsed.left);
            var top = Number(parsed && parsed.top);
            if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
            return clampCompactSurfacePosition(left, top, metrics);
        } catch (_) {
            return null;
        }
    }

    function loadCompactSurfaceStoredWidth() {
        try {
            var raw = window.localStorage.getItem(COMPACT_SURFACE_POSITION_STORAGE_KEY);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            var width = Number(parsed && parsed.width);
            if (!Number.isFinite(width) || width <= 0) return null;
            var maxWidth = getCompactSurfaceResizeMaxWidth();
            return Math.round(Math.max(COMPACT_SURFACE_MAX_WIDTH, Math.min(width, maxWidth)));
        } catch (_) {
            return null;
        }
    }

    function saveCompactSurfacePosition(left, top, width) {
        try {
            var payload = {
                left: Math.round(left),
                top: Math.round(top)
            };
            if (Number.isFinite(Number(width)) && Number(width) > 0) {
                payload.width = Math.round(Number(width));
            }
            window.localStorage.setItem(COMPACT_SURFACE_POSITION_STORAGE_KEY, JSON.stringify(payload));
        } catch (_) {}
    }

    function saveCompactSurfaceWidth(width) {
        try {
            var raw = window.localStorage.getItem(COMPACT_SURFACE_POSITION_STORAGE_KEY);
            var payload = raw ? JSON.parse(raw) : {};
            if (!payload || typeof payload !== 'object') payload = {};
            payload.width = Math.round(Number(width));
            window.localStorage.setItem(COMPACT_SURFACE_POSITION_STORAGE_KEY, JSON.stringify(payload));
        } catch (_) {}
    }

    function getCompactSurfaceDesktopWindowBounds() {
        var layoutOverride = getElectronCompactLayoutOverride();
        var windowBounds = layoutOverride && layoutOverride.windowBounds;
        if (!windowBounds) return null;
        var x = Number(windowBounds.x);
        var y = Number(windowBounds.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        return windowBounds;
    }

    function getCompactSurfaceResizeScreenRect(rect) {
        if (!rect) return null;
        if (compactSurfaceResizeSession) {
            var screenLeft = compactSurfaceResizeSession.side === 'left'
                ? compactSurfaceResizeSession.anchorRightScreen - rect.width
                : compactSurfaceResizeSession.anchorLeftScreen;
            return {
                left: Math.round(screenLeft),
                top: Math.round(compactSurfaceResizeSession.anchorTopScreen),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            };
        }
        var windowBounds = getCompactSurfaceDesktopWindowBounds();
        if (!windowBounds) return null;
        return {
            left: Math.round(Number(windowBounds.x) + rect.left),
            top: Math.round(Number(windowBounds.y) + rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
        };
    }

    function dispatchCompactSurfaceLayoutChange(rect) {
        var detail = rect || null;
        if (detail && isElectronChatWindow()) {
            detail = Object.assign({}, detail, {
                screenRect: getCompactSurfaceResizeScreenRect(detail),
                resizeActive: !!compactSurfaceResizeSession
            });
        }
        window.dispatchEvent(new CustomEvent('neko:compact-surface-layout-change', {
            detail: detail
        }));
    }

    function applyCompactSurfaceRect(left, top, width, height, options) {
        var shell = getShell();
        if (!shell) return null;

        var safeWidth = Number(width);
        var safeHeight = Number(height);
        if (!Number.isFinite(safeWidth) || safeWidth <= 0) {
            safeWidth = COMPACT_SURFACE_MAX_WIDTH;
        }
        if (!Number.isFinite(safeHeight) || safeHeight <= 0) {
            safeHeight = COMPACT_SURFACE_DEFAULT_HEIGHT;
        }

        var clamped = clampCompactSurfacePosition(Number(left) || 0, Number(top) || 0, {
            width: safeWidth,
            height: safeHeight
        });
        var rect = {
            left: Math.round(clamped.left),
            top: Math.round(clamped.top),
            width: Math.round(safeWidth),
            height: Math.round(safeHeight)
        };

        compactSurfaceAnchorSnapshot = [
            rect.left,
            rect.top,
            rect.width,
            rect.height
        ].join(':');
        compactSurfaceAnchorLocked = true;
        shell.style.setProperty('--compact-surface-left', rect.left + 'px');
        shell.style.setProperty('--compact-surface-top', rect.top + 'px');
        shell.style.setProperty('--compact-surface-width', rect.width + 'px');
        shell.style.setProperty('--compact-surface-height', rect.height + 'px');
        document.documentElement.style.setProperty('--compact-surface-left', rect.left + 'px');
        document.documentElement.style.setProperty('--compact-surface-top', rect.top + 'px');
        document.documentElement.style.setProperty('--compact-surface-width', rect.width + 'px');
        document.documentElement.style.setProperty('--compact-surface-height', rect.height + 'px');
        if (isElectronChatWindow()) {
            shell.style.setProperty('--desktop-compact-surface-left', rect.left + 'px');
            shell.style.setProperty('--desktop-compact-surface-top', rect.top + 'px');
            shell.style.setProperty('--desktop-compact-surface-width', rect.width + 'px');
            shell.style.setProperty('--desktop-compact-surface-height', rect.height + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-left', rect.left + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-top', rect.top + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-width', rect.width + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-height', rect.height + 'px');
        }
        shell.style.transform = 'none';
        if (options && options.persist && !isElectronChatWindow()) {
            saveCompactSurfacePosition(rect.left, rect.top, rect.width);
        }
        dispatchCompactSurfaceLayoutChange(rect);
        syncCompactInteractionGeometry();
        return rect;
    }

    function getCurrentCompactSurfaceRect() {
        var shell = getShell();
        if (!shell) return null;
        var domRect = normalizeCompactDomRect(shell.getBoundingClientRect());
        if (!domRect) return null;
        var css = window.getComputedStyle ? window.getComputedStyle(document.documentElement) : null;
        var cssLeft = css ? parseFloat(css.getPropertyValue('--compact-surface-left')) : NaN;
        var cssTop = css ? parseFloat(css.getPropertyValue('--compact-surface-top')) : NaN;
        var cssWidth = css ? parseFloat(css.getPropertyValue('--compact-surface-width')) : NaN;
        var cssHeight = css ? parseFloat(css.getPropertyValue('--compact-surface-height')) : NaN;
        return {
            left: Number.isFinite(cssLeft) ? cssLeft : domRect.left,
            top: Number.isFinite(cssTop) ? cssTop : domRect.top,
            width: Number.isFinite(cssWidth) && cssWidth > 0 ? cssWidth : domRect.width,
            height: Number.isFinite(cssHeight) && cssHeight > 0 ? cssHeight : domRect.height
        };
    }

    function getCompactSurfaceDesktopWindowX() {
        var windowBounds = getCompactSurfaceDesktopWindowBounds();
        var x = Number(windowBounds && windowBounds.x);
        return Number.isFinite(x) ? x : 0;
    }

    function getCompactSurfaceDesktopWindowY() {
        var windowBounds = getCompactSurfaceDesktopWindowBounds();
        var y = Number(windowBounds && windowBounds.y);
        return Number.isFinite(y) ? y : 0;
    }

    function getCompactSurfaceDesktopScreenRect() {
        var layoutOverride = getElectronCompactLayoutOverride();
        return layoutOverride && layoutOverride.surfaceScreenRect
            ? layoutOverride.surfaceScreenRect
            : null;
    }

    function getCompactDesktopWorkAreaEdge(workArea, edge) {
        if (!workArea) return NaN;
        var explicit = Number(workArea[edge]);
        if (Number.isFinite(explicit)) return explicit;
        var x = Number(workArea.x);
        var y = Number(workArea.y);
        var width = Number(workArea.width);
        var height = Number(workArea.height);
        if (edge === 'left' && Number.isFinite(x)) return x;
        if (edge === 'top' && Number.isFinite(y)) return y;
        if (edge === 'right' && Number.isFinite(x) && Number.isFinite(width)) return x + width;
        if (edge === 'bottom' && Number.isFinite(y) && Number.isFinite(height)) return y + height;
        return NaN;
    }

    function clampCompactSurfaceResizeWidthForSide(side, desiredWidth, currentRect) {
        var width = Number(desiredWidth);
        if (!Number.isFinite(width) || width <= 0) {
            width = currentRect && currentRect.width ? currentRect.width : COMPACT_SURFACE_MAX_WIDTH;
        }
        var layoutOverride = getElectronCompactLayoutOverride();
        var sideMax;
        if (layoutOverride && layoutOverride.windowBounds && layoutOverride.workArea) {
            var windowBounds = layoutOverride.windowBounds;
            var workArea = layoutOverride.workArea;
            var anchorLeftScreen = compactSurfaceResizeSession
                ? compactSurfaceResizeSession.anchorLeftScreen
                : currentRect.left + windowBounds.x;
            var anchorRightScreen = compactSurfaceResizeSession
                ? compactSurfaceResizeSession.anchorRightScreen
                : currentRect.left + currentRect.width + windowBounds.x;
            var workAreaLeft = getCompactDesktopWorkAreaEdge(workArea, 'left');
            var workAreaRight = getCompactDesktopWorkAreaEdge(workArea, 'right');
            sideMax = side === 'left'
                ? anchorRightScreen - (workAreaLeft + COMPACT_SURFACE_VIEWPORT_PAD_X)
                : (workAreaRight - COMPACT_SURFACE_VIEWPORT_PAD_X) - anchorLeftScreen;
            if (!Number.isFinite(sideMax) || sideMax <= 0) {
                sideMax = currentRect && currentRect.width ? currentRect.width : COMPACT_SURFACE_MAX_WIDTH;
            }
        } else {
            var minLeft = isMobileWidth() ? 8 : COMPACT_SURFACE_VIEWPORT_PAD_X;
            var maxRight = window.innerWidth - minLeft;
            sideMax = side === 'left'
                ? (currentRect.left + currentRect.width) - minLeft
                : maxRight - currentRect.left;
        }
        var maxWidth = Math.max(
            COMPACT_SURFACE_MAX_WIDTH,
            Math.min(COMPACT_SURFACE_RESIZE_MAX_WIDTH, sideMax)
        );
        return Math.round(Math.max(COMPACT_SURFACE_MAX_WIDTH, Math.min(width, maxWidth)));
    }

    function applyCompactSurfaceResizeRequest(detail) {
        if (!isHomeCompactSurfaceRoute() && !isDesktopHomeCompactSurfaceRoute()) return;
        var side = detail && detail.side === 'left' ? 'left' : 'right';
        var phase = detail && detail.phase;
        if (isElectronChatWindow() && detail && detail.screenRect) {
            compactSurfaceDesktopResizeActive = phase !== 'end';
            if (phase === 'end') {
                compactSurfaceResizeSession = null;
            }
            return;
        }
        var currentRect = getCurrentCompactSurfaceRect();
        if (!currentRect) return;
        var windowX = getCompactSurfaceDesktopWindowX();
        var desktopSurfaceRect = getCompactSurfaceDesktopScreenRect();
        if (phase === 'start' || !compactSurfaceResizeSession || compactSurfaceResizeSession.side !== side) {
            compactSurfaceResizeSession = {
                side: side,
                anchorLeftScreen: desktopSurfaceRect
                    ? desktopSurfaceRect.left
                    : currentRect.left + windowX,
                anchorRightScreen: desktopSurfaceRect
                    ? desktopSurfaceRect.right
                    : currentRect.left + currentRect.width + windowX,
                anchorTopScreen: desktopSurfaceRect
                    ? desktopSurfaceRect.top
                    : currentRect.top + getCompactSurfaceDesktopWindowY()
            };
        }
        var width = clampCompactSurfaceResizeWidthForSide(side, detail && detail.width, currentRect);
        var left = side === 'left'
            ? compactSurfaceResizeSession.anchorRightScreen - windowX - width
            : compactSurfaceResizeSession.anchorLeftScreen - windowX;
        var appliedRect = applyCompactSurfaceRect(left, currentRect.top, width, currentRect.height, {
            persist: phase === 'end'
        });
        if (appliedRect && detail && typeof detail === 'object') {
            try {
                detail.screenRect = getCompactSurfaceResizeScreenRect(appliedRect);
            } catch (_) {}
        }
        if (phase === 'end' && isElectronChatWindow()) {
            saveCompactSurfaceWidth(width);
        }
        if (phase === 'end') {
            compactSurfaceResizeSession = null;
        }
    }

    function getCompactSurfaceTarget(layoutOverride) {
        layoutOverride = layoutOverride || getElectronCompactLayoutOverride();
        if (layoutOverride && layoutOverride.surface) {
            var overrideMetrics = getCompactSurfaceMetrics();
            return {
                width: layoutOverride.surface.width,
                height: overrideMetrics.height,
                left: layoutOverride.surface.left,
                top: layoutOverride.surface.top
            };
        }

        var metrics = getCompactSurfaceMetrics();
        var viewportWidth = window.innerWidth;
        var viewportHeight = window.innerHeight;
        var fallbackTop = Math.max(
            COMPACT_SURFACE_VIEWPORT_PAD_TOP,
            viewportHeight - metrics.height - COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM
        );

        if (isElectronChatWindow()) {
            return {
                width: metrics.width,
                height: metrics.height,
                left: Math.max(
                    COMPACT_SURFACE_VIEWPORT_PAD_X,
                    Math.min(
                        Math.round((viewportWidth - metrics.width) / 2),
                        viewportWidth - metrics.width - COMPACT_SURFACE_VIEWPORT_PAD_X
                    )
                ),
                top: fallbackTop
            };
        }

        var storedPosition = loadCompactSurfacePosition(metrics);
        if (storedPosition) {
            return {
                width: metrics.width,
                height: metrics.height,
                left: storedPosition.left,
                top: storedPosition.top
            };
        }

        var avatarBounds = getCompactMinimizeBallAvatarBounds();
        if (!isMobileWidth() && avatarBounds) {
            var avatarLeft = avatarBounds.left + avatarBounds.width / 2 - metrics.width / 2;
            var avatarTop = avatarBounds.top + avatarBounds.height * COMPACT_SURFACE_AVATAR_VERTICAL_RATIO - metrics.height / 2;
            var avatarClamped = clampCompactSurfacePosition(
                Math.round(avatarLeft),
                Math.round(avatarTop),
                metrics
            );
            return {
                width: metrics.width,
                height: metrics.height,
                left: avatarClamped.left,
                top: avatarClamped.top
            };
        }

        if (isMobileWidth()) {
            return {
                width: metrics.width,
                height: metrics.height,
                left: 8,
                top: fallbackTop
            };
        }

        return {
            width: metrics.width,
            height: metrics.height,
            left: Math.max(
                COMPACT_SURFACE_VIEWPORT_PAD_X,
                Math.min(
                    Math.round((viewportWidth - metrics.width) / 2),
                    viewportWidth - metrics.width - COMPACT_SURFACE_VIEWPORT_PAD_X
                )
            ),
            top: fallbackTop
        };
    }

    function normalizeCompactDomRect(rect) {
        if (!rect) return null;
        var left = Number(rect.left);
        var top = Number(rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) || !Number.isFinite(width) || !Number.isFinite(height)) {
            return null;
        }
        if (width <= 0 || height <= 0) return null;
        return {
            left: left,
            top: top,
            width: width,
            height: height,
            right: Number.isFinite(Number(rect.right)) ? Number(rect.right) : left + width,
            bottom: Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : top + height
        };
    }

    function getCompactSurfaceBaseRect() {
        var root = getRoot();
        var compactSurfaceShell = root ? root.querySelector('.compact-chat-surface-shell') : null;
        if (compactSurfaceShell && shouldIncludeCompactGeometryElement(compactSurfaceShell)) {
            var shellRect = normalizeCompactDomRect(compactSurfaceShell.getBoundingClientRect());
            if (shellRect) return shellRect;
        }
        var candidates = [
            '[data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
            '[data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]'
        ];
        for (var i = 0; i < candidates.length; i += 1) {
            var element = document.querySelector(candidates[i]);
            if (!element || (root && !root.contains(element))) continue;
            if (!shouldIncludeCompactGeometryElement(element)) continue;
            var rect = normalizeCompactDomRect(element.getBoundingClientRect());
            if (rect) return rect;
        }
        return null;
    }

    function unionCompactRects(rects) {
        var valid = (rects || []).filter(Boolean);
        if (!valid.length) return null;
        var left = valid.reduce(function (min, rect) { return Math.min(min, rect.left); }, valid[0].left);
        var top = valid.reduce(function (min, rect) { return Math.min(min, rect.top); }, valid[0].top);
        var right = valid.reduce(function (max, rect) { return Math.max(max, rect.right); }, valid[0].right);
        var bottom = valid.reduce(function (max, rect) { return Math.max(max, rect.bottom); }, valid[0].bottom);
        return {
            left: left,
            top: top,
            width: right - left,
            height: bottom - top,
            right: right,
            bottom: bottom
        };
    }

    function intersectCompactRects(a, b) {
        var leftRect = normalizeCompactDomRect(a);
        var rightRect = normalizeCompactDomRect(b);
        if (!leftRect || !rightRect) return null;
        var left = Math.max(leftRect.left, rightRect.left);
        var top = Math.max(leftRect.top, rightRect.top);
        var right = Math.min(leftRect.right, rightRect.right);
        var bottom = Math.min(leftRect.bottom, rightRect.bottom);
        if (right <= left || bottom <= top) return null;
        return {
            left: left,
            top: top,
            width: right - left,
            height: bottom - top,
            right: right,
            bottom: bottom
        };
    }

    function shouldIncludeCompactGeometryElement(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') return false;
        var item = element.getAttribute('data-compact-geometry-item') || '';
        if (item === 'choice' && element.getAttribute('data-choice-layer-open') !== 'true') return false;
        if (item === 'toolFan' && element.getAttribute('data-compact-input-tool-fan-open') !== 'true') return false;
        if (element.getAttribute('aria-hidden') === 'true' && item !== 'dragHandle' && item !== 'resizeHandle') return false;
        var style = window.getComputedStyle ? window.getComputedStyle(element) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
        return true;
    }

    function getCompactGeometryElementRect(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') return null;
        var item = element.getAttribute('data-compact-geometry-item') || '';
        if (item === 'choice') {
            var choiceRects = Array.prototype.slice.call(element.querySelectorAll('.composer-galgame-option'))
                .map(function (child) {
                    var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                    if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                    return normalizeCompactDomRect(child.getBoundingClientRect());
                })
                .filter(Boolean);
            return unionCompactRects(choiceRects);
        }
        var ownRect = normalizeCompactDomRect(element.getBoundingClientRect());
        if (ownRect) return ownRect;

        var childRects = Array.prototype.slice.call(element.querySelectorAll('button'))
            .map(function (child) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                return normalizeCompactDomRect(child.getBoundingClientRect());
            })
            .filter(Boolean);
        return unionCompactRects(childRects);
    }

    function getCompactHistoryScrollbarRect(element, parentRect) {
        if (!element || !parentRect) return null;
        var scrollNode = element.querySelector('.compact-export-history-scroll');
        if (!scrollNode || typeof scrollNode.getBoundingClientRect !== 'function') return null;
        if (scrollNode.scrollHeight <= scrollNode.clientHeight + 1) return null;
        var style = window.getComputedStyle ? window.getComputedStyle(scrollNode) : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none')) return null;
        var scrollRect = intersectCompactRects(scrollNode.getBoundingClientRect(), parentRect);
        if (!scrollRect) return null;
        var gutterWidth = Math.min(Math.max(Number(scrollNode.offsetWidth - scrollNode.clientWidth) || 0, 8), 14);
        return {
            left: scrollRect.right - gutterWidth,
            top: scrollRect.top,
            width: gutterWidth,
            height: scrollRect.height,
            right: scrollRect.right,
            bottom: scrollRect.bottom
        };
    }

    var COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT = 18;

    function readCompactToolFanPixelVar(style, name, fallback) {
        var rawValue = style ? style.getPropertyValue(name) : '';
        var parsedValue = parseFloat(rawValue);
        return Number.isFinite(parsedValue) ? parsedValue : fallback;
    }

    function buildCompactToolFanCircleSliceRects(rect, element) {
        if (!rect) return null;
        var style = window.getComputedStyle ? window.getComputedStyle(element) : null;
        var centerX = rect.left + readCompactToolFanPixelVar(style, '--compact-tool-wheel-center-x', 116);
        var centerY = rect.top + readCompactToolFanPixelVar(style, '--compact-tool-wheel-center-y', 116);
        var radius = readCompactToolFanPixelVar(style, '--compact-tool-wheel-hover-radius', 116);
        if (!Number.isFinite(radius) || radius <= 0) return null;
        var sliceHeight = (radius * 2) / COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT;
        var slices = [];
        for (var index = 0; index < COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT; index += 1) {
            var top = centerY - radius + (sliceHeight * index);
            var bottom = index === COMPACT_TOOL_FAN_CIRCLE_SLICE_COUNT - 1
                ? centerY + radius
                : top + sliceHeight;
            var middleY = (top + bottom) / 2;
            var halfWidth = Math.sqrt(Math.max(0, (radius * radius) - ((middleY - centerY) * (middleY - centerY))));
            var left = centerX - halfWidth;
            var right = centerX + halfWidth;
            slices.push({
                left: left,
                top: top,
                width: right - left,
                height: bottom - top,
                right: right,
                bottom: bottom
            });
        }
        return slices;
    }

    function isCompactSurfaceBaseAnchorKind(kind) {
        return kind === 'surfaceShell' || kind === 'capsule' || kind === 'input';
    }

    function getCompactSurfaceGeometryRole(kind) {
        if (isCompactSurfaceBaseAnchorKind(kind)) return 'baseAnchor';
        if (kind === 'dragHandle') return 'baseHit';
        return 'extraIsland';
    }

    function assignCompactSurfaceGeometryRole(item) {
        if (!item) return item;
        item.geometryRole = getCompactSurfaceGeometryRole(item.kind);
        return item;
    }

    function collectCompactToolFanGeometryItems(element) {
        if (!element || element.getAttribute('data-compact-geometry-item') !== 'toolFan') return [];
        var parentRect = getCompactGeometryElementRect(element);
        var items = [];
        if (parentRect) {
            var nativeRects = buildCompactToolFanCircleSliceRects(parentRect, element) || [parentRect];
            nativeRects.forEach(function (nativeRect, index) {
                items.push({
                    id: index === 0 ? 'toolFan:native' : 'toolFan:native:' + index,
                    owner: 'surface',
                    kind: 'toolFan',
                    visualRect: nativeRect,
                    hitRect: nativeRect,
                    nativeRect: nativeRect,
                    interactive: true
                });
            });
        }
        return items.concat(Array.prototype.slice.call(element.querySelectorAll('.compact-input-tool-item, .composer-icon-popover .composer-icon-button'))
            .map(function (child, index) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                if (style && Number(style.opacity) <= 0.01) return null;
                var slot = child.getAttribute('data-compact-tool-wheel-slot') || '';
                var isAvatarToolChoice = child.classList && child.classList.contains('composer-icon-button');
                if (!isAvatarToolChoice && (!slot || slot.indexOf('hidden') === 0)) return null;
                var rect = normalizeCompactDomRect(child.getBoundingClientRect());
                if (!rect) return null;
                return {
                    id: isAvatarToolChoice
                        ? 'toolFan:avatarToolChoice:' + index
                        : 'toolFan:' + slot + ':' + index,
                    owner: 'surface',
                    kind: 'toolFan',
                    visualRect: rect,
                    hitRect: rect,
                    nativeRect: rect,
                    interactive: true
                };
            })
            .filter(Boolean));
    }

    function collectCompactCompositeGeometryItems(element, kind) {
        var parentRect = getCompactGeometryElementRect(element);
        var items = [];
        if (parentRect) {
            items.push({
                id: kind + ':native',
                owner: 'surface',
                kind: kind || 'unknown',
                visualRect: parentRect,
                hitRect: null,
                nativeRect: parentRect,
                interactive: false
            });
            if (kind === 'history') {
                var scrollbarRect = getCompactHistoryScrollbarRect(element, parentRect);
                if (scrollbarRect) {
                    items.push({
                        id: 'history:scrollbar',
                        owner: 'surface',
                        kind: kind || 'unknown',
                        visualRect: scrollbarRect,
                        hitRect: scrollbarRect,
                        nativeRect: null,
                        interactive: true,
                        hitRegionKind: 'scrollbar'
                    });
                }
            }
        }
        return items.concat(Array.prototype.slice.call(element.querySelectorAll('[data-compact-hit-region="true"]'))
            .map(function (child, index) {
                var style = window.getComputedStyle ? window.getComputedStyle(child) : null;
                if (style && (style.display === 'none' || style.visibility === 'hidden')) return null;
                if (style && Number(style.opacity) <= 0.01) return null;
                var rect = normalizeCompactDomRect(child.getBoundingClientRect());
                if (!rect) return null;
                var clippedRect = parentRect ? intersectCompactRects(rect, parentRect) : rect;
                if (!clippedRect) return null;
                var interactive = style ? style.pointerEvents !== 'none' : true;
                if (!interactive) return null;
                var hitRegionKind = child.getAttribute('data-compact-hit-region-kind') || null;
                return {
                    id: child.getAttribute('data-compact-hit-region-id') || (kind + ':hit:' + index),
                    owner: 'surface',
                    kind: kind || 'unknown',
                    visualRect: clippedRect,
                    hitRect: clippedRect,
                    nativeRect: kind === 'history' ? null : clippedRect,
                    interactive: true,
                    hitRegionKind: hitRegionKind
                };
            })
            .filter(Boolean));
    }

    function collectCompactSurfaceGeometryItems() {
        var root = getRoot();
        if (!root) return [];
        var compactSurfaceShell = root.querySelector('.compact-chat-surface-shell');
        var shellRect = compactSurfaceShell
            ? normalizeCompactDomRect(compactSurfaceShell.getBoundingClientRect())
            : null;
        var elements = Array.prototype.slice.call(document.querySelectorAll('[data-compact-geometry-owner="surface"]'));
        var initialItems = [];
        if (shellRect) {
            initialItems.push({
                id: 'surface:shell',
                owner: 'surface',
                kind: 'surfaceShell',
                visualRect: shellRect,
                hitRect: null,
                nativeRect: shellRect,
                interactive: false
            });
        }
        return elements.reduce(function (items, element) {
            if (
                !root.contains(element)
                && !element.classList.contains('compact-input-tool-fan')
                && !element.classList.contains('compact-chat-choice-anchor')
            ) return items;
            if (!shouldIncludeCompactGeometryElement(element)) return items;
            var compactGeometryItem = element.getAttribute('data-compact-geometry-item');
            if (compactGeometryItem === 'toolFan') {
                return items.concat(collectCompactToolFanGeometryItems(element));
            }
            if (element.getAttribute('data-compact-geometry-hit-scope') === 'children') {
                return items.concat(collectCompactCompositeGeometryItems(element, compactGeometryItem));
            }
            var rect = getCompactGeometryElementRect(element);
            if (!rect) return items;
            items.push({
                id: element.id || compactGeometryItem || element.className || 'compact-item',
                owner: 'surface',
                kind: compactGeometryItem || 'unknown',
                visualRect: rect,
                hitRect: rect,
                nativeRect: rect,
                interactive: true
            });
            return items;
        }, initialItems).map(assignCompactSurfaceGeometryRole);
    }

    function getCompactInteractionGeometrySnapshot() {
        if (!isHomeCompactMinimizeBallRoute()) return null;
        var layoutOverride = getElectronCompactLayoutOverride();
        var surfaceItems = isCompactHomeMinimizeBallEnabled() ? collectCompactSurfaceGeometryItems() : [];
        var baseSurfaceItems = surfaceItems.filter(function (item) {
            return item && item.geometryRole === 'baseAnchor';
        });
        var extraIslandItems = surfaceItems.filter(function (item) {
            return item && item.geometryRole === 'extraIsland';
        });
        var surfaceRects = surfaceItems.map(function (item) { return item.nativeRect; });
        var baseSurfaceRects = baseSurfaceItems.map(function (item) { return item.nativeRect; });
        var ballRect = isCompactHomeMinimizeBallEnabled() && !isElectronCompactExternalBallEnabled()
            ? normalizeCompactDomRect(getCompactMinimizeBallTarget())
            : null;
        return {
            mode: getCurrentChatSurfaceMode(),
            compactChatState: getCurrentCompactChatState(),
            viewport: {
                width: window.innerWidth,
                height: window.innerHeight
            },
            surfaceItems: surfaceItems,
            surfaceUnion: unionCompactRects(surfaceRects),
            baseSurfaceItems: baseSurfaceItems,
            baseSurfaceRect: unionCompactRects(baseSurfaceRects),
            baseSurfaceNativeRects: baseSurfaceItems.map(function (item) { return item.nativeRect; }).filter(Boolean),
            baseSurfaceHitRects: surfaceItems
                .filter(function (item) { return item && (item.geometryRole === 'baseAnchor' || item.geometryRole === 'baseHit'); })
                .map(function (item) { return item.hitRect; })
                .filter(Boolean),
            extraIslandItems: extraIslandItems,
            extraIslandNativeRects: extraIslandItems.map(function (item) { return item.nativeRect; }).filter(Boolean),
            extraIslandHitRects: extraIslandItems.map(function (item) { return item.hitRect; }).filter(Boolean),
            surfaceHitRects: surfaceItems.map(function (item) { return item.hitRect; }).filter(Boolean),
            surfaceNativeRects: surfaceItems.map(function (item) { return item.nativeRect; }).filter(Boolean),
            compactChoicePlacement: layoutOverride ? layoutOverride.compactChoicePlacement : null,
            ballRect: ballRect,
            externalBall: isElectronCompactExternalBallEnabled()
                ? (layoutOverride && layoutOverride.ball) || normalizeCompactDomRect(window.__nekoDesktopCompactBallScreenRect)
                : null
        };
    }

    function syncCompactInteractionGeometry() {
        var snapshot = getCompactInteractionGeometrySnapshot();
        var serialized = snapshot ? JSON.stringify(snapshot) : '';
        if (serialized === compactInteractionGeometrySnapshot) return;
        compactInteractionGeometrySnapshot = serialized;
        window.__nekoCompactInteractionGeometry = snapshot;
        window.__nekoGetCompactInteractionGeometry = getCompactInteractionGeometrySnapshot;
        window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-change', {
            detail: snapshot
        }));
    }

    function clearCompactSurfaceAnchor() {
        var shell = getShell();
        if (!shell) return;
        shell.style.removeProperty('--compact-surface-left');
        shell.style.removeProperty('--compact-surface-top');
        shell.style.removeProperty('--compact-surface-width');
        shell.style.removeProperty('--compact-surface-height');
        shell.style.removeProperty('--desktop-compact-surface-left');
        shell.style.removeProperty('--desktop-compact-surface-top');
        shell.style.removeProperty('--desktop-compact-surface-width');
        shell.style.removeProperty('--desktop-compact-surface-height');
        document.documentElement.style.removeProperty('--compact-surface-left');
        document.documentElement.style.removeProperty('--compact-surface-top');
        document.documentElement.style.removeProperty('--compact-surface-width');
        document.documentElement.style.removeProperty('--compact-surface-height');
        document.documentElement.style.removeProperty('--desktop-compact-surface-left');
        document.documentElement.style.removeProperty('--desktop-compact-surface-top');
        document.documentElement.style.removeProperty('--desktop-compact-surface-width');
        document.documentElement.style.removeProperty('--desktop-compact-surface-height');
        document.documentElement.style.removeProperty('--compact-desktop-workarea-width');
        document.documentElement.style.removeProperty('--compact-desktop-workarea-height');
        compactSurfaceAnchorSnapshot = '';
        compactSurfaceAnchorLocked = false;
        dispatchCompactSurfaceLayoutChange(null);
        syncCompactInteractionGeometry();
    }

    function syncCompactSurfaceAnchor() {
        var shell = getShell();
        if (!shell) return;
        if (!isCompactHomeMinimizeBallEnabled()) {
            clearCompactSurfaceAnchor();
            return;
        }
        if (compactSurfaceAnchorLocked) {
            return;
        }
        if (compactSurfaceDesktopResizeActive && isElectronChatWindow()) {
            return;
        }
        if (compactSurfaceResizeSession && !isElectronChatWindow()) {
            return;
        }

        var layoutOverride = getElectronCompactLayoutOverride();
        var target = getCompactSurfaceTarget(layoutOverride);
        if (!target) {
            clearCompactSurfaceAnchor();
            return;
        }
        var snapshot = [
            Math.round(target.left),
            Math.round(target.top),
            Math.round(target.width),
            Math.round(target.height || COMPACT_SURFACE_DEFAULT_HEIGHT)
        ].join(':');
        if (snapshot === compactSurfaceAnchorSnapshot) {
            return;
        }

        compactSurfaceAnchorSnapshot = snapshot;
        shell.style.setProperty('--compact-surface-left', Math.round(target.left) + 'px');
        shell.style.setProperty('--compact-surface-top', Math.round(target.top) + 'px');
        shell.style.setProperty('--compact-surface-width', Math.round(target.width) + 'px');
        shell.style.setProperty('--compact-surface-height', Math.round(target.height || COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
        document.documentElement.style.setProperty('--compact-surface-left', Math.round(target.left) + 'px');
        document.documentElement.style.setProperty('--compact-surface-top', Math.round(target.top) + 'px');
        document.documentElement.style.setProperty('--compact-surface-width', Math.round(target.width) + 'px');
        document.documentElement.style.setProperty('--compact-surface-height', Math.round(target.height || COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
        if (layoutOverride && layoutOverride.surface) {
            shell.style.setProperty('--desktop-compact-surface-left', Math.round(target.left) + 'px');
            shell.style.setProperty('--desktop-compact-surface-top', Math.round(target.top) + 'px');
            shell.style.setProperty('--desktop-compact-surface-width', Math.round(target.width) + 'px');
            shell.style.setProperty('--desktop-compact-surface-height', Math.round(target.height || COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-left', Math.round(target.left) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-top', Math.round(target.top) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-width', Math.round(target.width) + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-height', Math.round(target.height || COMPACT_SURFACE_DEFAULT_HEIGHT) + 'px');
        }
        if (layoutOverride && layoutOverride.workArea) {
            document.documentElement.style.setProperty('--compact-desktop-workarea-width', Math.round(layoutOverride.workArea.width) + 'px');
            document.documentElement.style.setProperty('--compact-desktop-workarea-height', Math.round(layoutOverride.workArea.height) + 'px');
        } else {
            document.documentElement.style.removeProperty('--compact-desktop-workarea-width');
            document.documentElement.style.removeProperty('--compact-desktop-workarea-height');
        }
        dispatchCompactSurfaceLayoutChange({
            left: Math.round(target.left),
            top: Math.round(target.top),
            width: Math.round(target.width),
            height: Math.round(target.height || COMPACT_SURFACE_DEFAULT_HEIGHT)
        });
        syncCompactInteractionGeometry();
    }

    function clearCompactMinimizeBallAnchor() {
        var shell = getShell();
        if (!shell) return;
        shell.style.removeProperty('--compact-minimize-ball-left');
        shell.style.removeProperty('--compact-minimize-ball-top');
        compactMinimizeBallSnapshot = '';
        syncCompactInteractionGeometry();
    }

    function syncCompactMinimizeBallAnchor() {
        var shell = getShell();
        if (!shell) return;
        if (!isCompactHomeMinimizeBallEnabled()) {
            clearCompactMinimizeBallAnchor();
            return;
        }

        var placement = getCompactMinimizeBallTarget();
        if (!placement) {
            clearCompactMinimizeBallAnchor();
            return;
        }

        var snapshot = Math.round(placement.left) + ':' + Math.round(placement.top);
        if (snapshot === compactMinimizeBallSnapshot) {
            return;
        }

        compactMinimizeBallSnapshot = snapshot;
        shell.style.setProperty('--compact-minimize-ball-left', Math.round(placement.left) + 'px');
        shell.style.setProperty('--compact-minimize-ball-top', Math.round(placement.top) + 'px');
        syncCompactInteractionGeometry();
    }

    function stopCompactMinimizeBallTracking() {
        if (compactMinimizeBallFrame) {
            window.cancelAnimationFrame(compactMinimizeBallFrame);
            compactMinimizeBallFrame = 0;
        }
        compactSurfacePendingModelOpen = false;
        clearCompactMinimizeBallAnchor();
        clearCompactSurfaceAnchor();
    }

    function scheduleCompactMinimizeBallTracking() {
        if (!isCompactHomeMinimizeBallEnabled()) {
            stopCompactMinimizeBallTracking();
            return;
        }
        if (compactMinimizeBallFrame) {
            return;
        }

        var loop = function () {
            compactMinimizeBallFrame = 0;
            if (!isCompactHomeMinimizeBallEnabled()) {
                stopCompactMinimizeBallTracking();
                return;
            }
            syncCompactSurfaceAnchor();
            syncCompactMinimizeBallAnchor();
            syncCompactInteractionGeometry();
            compactMinimizeBallFrame = window.requestAnimationFrame(loop);
        };

        syncCompactSurfaceAnchor();
        syncCompactMinimizeBallAnchor();
        syncCompactInteractionGeometry();
        compactMinimizeBallFrame = window.requestAnimationFrame(loop);
    }

    function revealPendingCompactSurfaceOpen() {
        if (!compactSurfacePendingModelOpen) return false;
        if (shouldDelayCompactSurfaceOpenForModel()) return false;
        var overlay = getOverlay();
        if (!overlay) return false;
        compactSurfacePendingModelOpen = false;
        overlay.hidden = false;
        document.body.classList.add('react-chat-window-open');
        syncCompactSurfaceAnchor();
        scheduleCompactMinimizeBallTracking();
        scheduleMobileContentLayout();
        return true;
    }

    function clearMobileExpandVisualGuard() {
        if (mobileExpandVisualGuardTimer) {
            window.clearTimeout(mobileExpandVisualGuardTimer);
            mobileExpandVisualGuardTimer = 0;
        }
        var shell = getShell();
        if (shell) {
            shell.classList.remove('is-mobile-expand-guarding');
        }
    }

    function armMobileExpandClickGuard(clientX, clientY) {
        if (!isMobileWidth()) return;
        if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
        mobileExpandClickGuard = {
            clientX: clientX,
            clientY: clientY,
            expiresAt: Date.now() + MOBILE_EXPAND_CLICK_GUARD_MS
        };
        var shell = getShell();
        if (shell) {
            shell.classList.add('is-mobile-expand-guarding');
        }
        if (mobileExpandVisualGuardTimer) {
            window.clearTimeout(mobileExpandVisualGuardTimer);
        }
        mobileExpandVisualGuardTimer = window.setTimeout(clearMobileExpandVisualGuard, MOBILE_EXPAND_VISUAL_GUARD_MS);
    }

    function shouldBlockMobileExpandClick(event) {
        if (!mobileExpandClickGuard) return false;
        var guard = mobileExpandClickGuard;
        if (Date.now() > guard.expiresAt) {
            mobileExpandClickGuard = null;
            clearMobileExpandVisualGuard();
            return false;
        }
        if (!isMobileWidth()) {
            mobileExpandClickGuard = null;
            clearMobileExpandVisualGuard();
            return false;
        }
        var dx = event.clientX - guard.clientX;
        var dy = event.clientY - guard.clientY;
        var withinGuardRadius = Math.sqrt(dx * dx + dy * dy) <= MOBILE_EXPAND_CLICK_GUARD_RADIUS;
        if (!withinGuardRadius) return false;

        var shell = getShell();
        if (shell && !shell.contains(event.target)) return false;
        if (event.type === 'click') {
            mobileExpandClickGuard = null;
        }
        return true;
    }

    function blockMobileExpandSyntheticPointerEvent(event) {
        if (!shouldBlockMobileExpandClick(event)) return;
        // 手机端触摸展开后浏览器会补发同坐标鼠标事件；从 mousedown 起吞掉，避免按钮出现按压反馈。
        event.preventDefault();
        event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') {
            event.stopImmediatePropagation();
        }
    }

    function getOverlay() {
        return $('react-chat-window-overlay');
    }

    function getShell() {
        return $('react-chat-window-shell');
    }

    function getHeader() {
        return $('react-chat-window-drag-handle');
    }

    function isYuiGuideDragLocked() {
        var body = document.body;
        if (!body) return false;
        return body.classList.contains('yui-guide-home-driver-hidden')
            || body.classList.contains('yui-taking-over')
            || body.classList.contains('yui-guide-chat-buttons-disabled');
    }

    function getMinimizeButton() {
        return $('reactChatWindowMinimizeButton');
    }

    function getMinimizeIcon() {
        return $('reactChatWindowMinimizeIcon');
    }

    function getRoot() {
        return $('react-chat-window-root');
    }

    function clearMobileContentCap() {
        var shell = getShell();
        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        if (shell.dataset.mobileAutoHeight !== undefined) {
            shell.style.removeProperty('height');
            delete shell.dataset.mobileAutoHeight;
        }
    }

    function resetMobileContentLayoutState(shell, topbar, composer, messageList) {
        [topbar, composer, messageList].forEach(function (element) {
            if (!element) return;
            element.style.removeProperty('height');
            if (element.dataset && element.dataset.mobileAutoHeight) {
                delete element.dataset.mobileAutoHeight;
            }
        });

        if (!shell) return;

        shell.classList.remove('is-mobile-content-capped');
        shell.style.removeProperty('height');
        if (shell.dataset.mobileAutoHeight) {
            delete shell.dataset.mobileAutoHeight;
        }
    }

    function syncMobileContentLayout() {
        var overlay = getOverlay();
        var shell = getShell();
        var root = getRoot();
        if (!overlay || overlay.hidden || !shell || !root || minimized || !isMobileWidth()) {
            clearMobileContentCap();
            return;
        }

        // 正在拖拽调整高度时不覆盖，等 stopResize() 结束后再同步
        if (resizeState) return;

        // 如果用户手动设置了高度，使用用户高度，不自动计算
        if (mobileUserHeight > 0) {
            var h = Math.min(mobileUserHeight, getMobileMaxHeight());
            shell.style.height = h + 'px';
            shell.dataset.mobileAutoHeight = 'false';
            shell.classList.remove('is-mobile-content-capped');
            return;
        }

        var topbar = root.querySelector('.window-topbar');
        var composer = root.querySelector('.composer-panel');
        var messageList = root.querySelector('.message-list');
        var compactStage = root.querySelector('.chat-body-compact-surface');
        var contentNode = messageList;
        if (!contentNode && getCurrentChatSurfaceMode() === 'compact') {
            contentNode = compactStage || root.querySelector('.compact-chat-stage') || root.querySelector('.compact-chat-surface-shell');
        }
        if (!topbar || !composer || !contentNode) {
            resetMobileContentLayoutState(shell, topbar, composer, messageList || compactStage);
            return;
        }

        var maxHeight = getMobileMaxHeight();
        if (!maxHeight) return;

        var desiredMessageHeight = getCurrentChatSurfaceMode() === 'compact'
            ? Math.max(0, Math.ceil(contentNode.getBoundingClientRect().height))
            : Math.max(MOBILE_MESSAGE_MIN_HEIGHT, messageList.scrollHeight);
        var desiredHeight = Math.ceil(
            topbar.getBoundingClientRect().height
            + composer.getBoundingClientRect().height
            + desiredMessageHeight
        );
        var nextHeight = Math.min(maxHeight, desiredHeight);

        shell.style.height = nextHeight + 'px';
        shell.dataset.mobileAutoHeight = 'true';
        shell.classList.toggle('is-mobile-content-capped', desiredHeight > maxHeight);
    }

    function scheduleMobileContentLayout() {
        if (mobileLayoutFrame) return;

        mobileLayoutFrame = window.requestAnimationFrame(function () {
            mobileLayoutFrame = 0;
            syncMobileContentLayout();
        });
    }

    function getI18nText(key, fallback) {
        if (typeof window.safeT === 'function') {
            return window.safeT(key, fallback);
        }

        if (typeof window.t === 'function') {
            try {
                var translated = window.t(key, fallback);
                if (translated && translated !== key) {
                    return translated;
                }
            } catch (_) {}
        }

        return fallback;
    }

    function getTextContent(node) {
        return node && node.textContent ? node.textContent.trim() : '';
    }

    function sanitizeDisplayName(value) {
        if (value == null) return '';
        return String(value).trim();
    }

    function getCurrentAssistantName() {
        return sanitizeDisplayName(
            window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__
            || (window.lanlan_config && window.lanlan_config.lanlan_name)
            || window._currentCatgirl
            || window.currentCatgirl
        ) || 'Neko';
    }

    function getCurrentUserName() {
        var candidates = [
            window.master_display_name,
            window.lanlan_config && window.lanlan_config.master_display_name,
            window.master_nickname,
            window.lanlan_config && window.lanlan_config.master_nickname,
            window.master_name,
            window.lanlan_config && window.lanlan_config.master_name,
            window.currentUser && (window.currentUser.nickname || window.currentUser.display_name || window.currentUser.displayName || window.currentUser.username || window.currentUser.name),
            window.userProfile && (window.userProfile.nickname || window.userProfile.display_name || window.userProfile.displayName || window.userProfile.username || window.userProfile.name),
            window.appUser && (window.appUser.nickname || window.appUser.display_name || window.appUser.displayName || window.appUser.username || window.appUser.name),
            window.username,
            window.userName,
            window.displayName,
            window.nickname
        ];

        for (var i = 0; i < candidates.length; i += 1) {
            var resolved = sanitizeDisplayName(candidates[i]);
            if (resolved) return resolved;
        }

        try {
            var storageKeys = ['nickname', 'displayName', 'userName', 'username'];
            for (var j = 0; j < storageKeys.length; j += 1) {
                var stored = sanitizeDisplayName(localStorage.getItem(storageKeys[j]));
                if (stored) return stored;
            }
        } catch (_) {}

        return 'You';
    }

    function getDefaultAuthorByRole(role) {
        return role === 'user' ? getCurrentUserName() : getCurrentAssistantName();
    }

    function createBaseViewProps() {
        var titleNode = $('chat-title');
        var textSendButton = $('textSendButton');
        var sendButtonLabelNode = textSendButton ? textSendButton.querySelector('[data-i18n="chat.send"]') : null;
        var title = getTextContent(titleNode)
            || getI18nText('chat.title', '对话')
            || '对话';
        var inputPlaceholder = getI18nText('chat.textInputPlaceholderCompact', '')
            || getI18nText('chat.textInputPlaceholderShort', '')
            || getI18nText('chat.textInputPlaceholder', '')
            || '输入消息...';
        var sendButtonLabel = getTextContent(sendButtonLabelNode)
            || getI18nText('chat.send', '发送')
            || '发送';

        return {
            title: title,
            iconSrc: '/static/icons/chat_icon.png',
            inputPlaceholder: inputPlaceholder,
            sendButtonLabel: sendButtonLabel,
            emptyText: getI18nText('chat.emptyState', '聊天内容接入后会显示在这里。'),
            chatWindowAriaLabel: getI18nText('chat.reactWindowAriaLabel', 'Neko chat window'),
            messageListAriaLabel: getI18nText('chat.messageListAriaLabel', 'Chat messages'),
            composerToolsAriaLabel: getI18nText('chat.composerToolsAriaLabel', 'Composer tools'),
            composerAttachmentsAriaLabel: getI18nText('chat.pendingImagesAriaLabel', 'Pending attachments'),
            importImageButtonLabel: getI18nText('chat.importImage', '导入图片'),
            screenshotButtonLabel: isMobileWidth()
                ? getI18nText('chat.takePhoto', '拍照')
                : getI18nText('chat.screenshot', '截图'),
            importImageButtonAriaLabel: getI18nText('chat.importImageAriaLabel', '导入图片'),
            screenshotButtonAriaLabel: isMobileWidth()
                ? getI18nText('chat.takePhotoAriaLabel', '拍照')
                : getI18nText('chat.screenshotAriaLabel', '截图'),
            removeAttachmentButtonAriaLabel: getI18nText('chat.removePendingImage', '移除图片'),
            failedStatusLabel: getI18nText('chat.messageFailed', '发送失败'),
            inputHint: getI18nText('chat.reactWindowInputHint', 'Enter 发送，Shift + Enter 换行'),
            jukeboxButtonLabel: getI18nText('chat.jukeboxLabel', '点歌台'),
            jukeboxButtonAriaLabel: getI18nText('chat.jukebox', '点歌台'),
            avatarGeneratorButtonLabel: getI18nText('chat.avatarPreviewLabel', '头像'),
            avatarGeneratorButtonAriaLabel: getI18nText('chat.avatarPreview', '生成头像'),
            exportConversationButtonLabel: getI18nText('chat.exportConversation', '导出对话'),
            exportConversationButtonAriaLabel: getI18nText('chat.exportConversation', '导出对话'),
            chatSurfaceMode: getCurrentChatSurfaceMode(),
            compactChatState: getCurrentCompactChatState(),
            translateEnabled: (window.appState && typeof window.appState.subtitleEnabled !== 'undefined')
                ? !!window.appState.subtitleEnabled
                : localStorage.getItem('subtitleEnabled') === 'true',
            translateButtonLabel: getI18nText('subtitle.enable', '字幕翻译'),
            translateButtonAriaLabel: getI18nText('subtitle.enableAriaLabel', '字幕翻译开关'),
            galgameToggleButtonLabel: getI18nText('chat.galgameToggle', 'GalGame 模式'),
            galgameToggleButtonAriaLabel: getI18nText('chat.galgameToggleAriaLabel', '切换 GalGame 选项模式'),
            galgameLoadingLabel: getI18nText('chat.galgameLoading', '生成回复选项中…'),
            composerDisabled: !!state.homeTutorialInteractionLocked
        };
    }

    function ensureViewProps() {
        if (!state.viewProps) {
            state.viewProps = createBaseViewProps();
        }
        return state.viewProps;
    }

    function cloneMessage(message) {
        if (!message || typeof message !== 'object') return null;
        return {
            id: message.id,
            role: message.role,
            author: message.author,
            time: message.time,
            createdAt: message.createdAt,
            avatarLabel: message.avatarLabel,
            avatarUrl: message.avatarUrl,
            blocks: Array.isArray(message.blocks) ? message.blocks.map(function (block) {
                if (!block || typeof block !== 'object') return null;
                if (block.type === 'buttons' && Array.isArray(block.buttons)) {
                    return {
                        type: 'buttons',
                        buttons: block.buttons.map(function (button) {
                            if (!button || typeof button !== 'object') return null;
                            return {
                                id: button.id,
                                label: button.label,
                                action: button.action,
                                variant: button.variant,
                                disabled: !!button.disabled,
                                payload: button.payload || undefined
                            };
                        }).filter(Boolean)
                    };
                }
                return Object.assign({}, block);
            }).filter(Boolean) : [],
            actions: Array.isArray(message.actions) ? message.actions.map(function (action) {
                if (!action || typeof action !== 'object') return null;
                return {
                    id: action.id,
                    label: action.label,
                    action: action.action,
                    variant: action.variant,
                    disabled: !!action.disabled,
                    payload: action.payload || undefined
                };
            }).filter(Boolean) : undefined,
            status: message.status,
            sortKey: message.sortKey
        };
    }

    function normalizeMessage(rawMessage, fallbackSortKey) {
        var message = cloneMessage(rawMessage);
        if (!message || !message.id) return null;

        var now = Date.now();
        var createdAt = typeof message.createdAt === 'number' ? message.createdAt : now;
        var time = message.time;
        if (!time) {
            try {
                time = new Date(createdAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            } catch (_) {
                time = '';
            }
        }

        return {
            id: String(message.id),
            role: message.role || 'assistant',
            author: sanitizeDisplayName(message.author) || getDefaultAuthorByRole(message.role || 'assistant'),
            time: time,
            createdAt: createdAt,
            avatarLabel: message.avatarLabel,
            avatarUrl: message.avatarUrl,
            blocks: Array.isArray(message.blocks) ? message.blocks : [],
            actions: Array.isArray(message.actions) ? message.actions : undefined,
            status: message.status,
            sortKey: typeof message.sortKey === 'number' ? message.sortKey : fallbackSortKey
        };
    }

    function sortMessages(messages) {
        return messages.slice().sort(function (a, b) {
            var sortA = typeof a.sortKey === 'number' ? a.sortKey : (typeof a.createdAt === 'number' ? a.createdAt : 0);
            var sortB = typeof b.sortKey === 'number' ? b.sortKey : (typeof b.createdAt === 'number' ? b.createdAt : 0);
            if (sortA !== sortB) return sortA - sortB;
            return String(a.id).localeCompare(String(b.id));
        });
    }

    function buildRenderProps() {
        if (state.rollbackDraft) {
            console.log('[ROLLBACK] buildRenderProps: rollbackDraftPresent=true length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        }
        return Object.assign({}, ensureViewProps(), {
            messages: state.messages,
            composerAttachments: state.composerAttachments,
            rollbackDraft: state.rollbackDraft || undefined,
            _rollbackKey: state._rollbackKey || undefined,
            _toolCursorResetKey: state._toolCursorResetKey || undefined,
            composerHidden: state.composerHidden,
            chatSurfaceMode: getCurrentChatSurfaceMode(),
            compactChatState: getCurrentCompactChatState(),
            galgameModeEnabled: !!state.galgameModeEnabled,
            galgameOptions: Array.isArray(state.galgameOptions) ? state.galgameOptions : [],
            galgameOptionsLoading: !!state.galgameOptionsLoading,
            choicePrompt: state.choicePrompt || null,
            onMessageAction: handleMessageAction,
            onComposerImportImage: handleComposerImportImage,
            onComposerScreenshot: handleComposerScreenshot,
            onComposerRemoveAttachment: handleComposerRemoveAttachment,
            onComposerSubmit: handleComposerSubmit,
            onCompactHistoryDrop: handleCompactHistoryDrop,
            onCompactHistoryDragStateChange: handleCompactHistoryDragStateChange,
            onAvatarInteraction: handleAvatarInteraction,
            onAvatarToolStateChange: handleAvatarToolStateChange,
            onJukeboxClick: handleJukeboxClick,
            onAvatarGeneratorClick: handleAvatarGeneratorClick,
            onExportConversationClick: handleExportConversationClick,
            onTranslateToggle: handleTranslateToggle,
            onGalgameModeToggle: handleGalgameModeToggle,
            onGalgameOptionSelect: handleGalgameOptionSelect,
            onChoiceSelect: handleChoiceSelect,
            onCompactChatStateChange: handleCompactChatStateChange
        });
    }

    function showToast(message, duration) {
        if (typeof window.showStatusToast === 'function') {
            window.showStatusToast(message, duration || 3000);
        }
    }

    function ensureBundleLoaded() {
        if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
            return Promise.resolve(window.NekoChatWindow);
        }

        if (loadedPromise) return loadedPromise;

        loadedPromise = new Promise(function (resolve, reject) {
            var existing = document.querySelector('script[data-react-chat-window-bundle="true"]');
            if (existing) {
                // Script already finished loading but API is missing — re-create it
                if (existing.readyState === 'loaded' || existing.readyState === 'complete' || existing.dataset.loaded === 'true') {
                    if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
                        resolve(window.NekoChatWindow);
                    } else {
                        existing.parentNode.removeChild(existing);
                        // Fall through to create a fresh script element below
                    }
                } else if (existing.dataset.error === 'true') {
                    // Script previously failed to load — remove stale element and recreate
                    existing.parentNode.removeChild(existing);
                    // Fall through to create a fresh script element below
                } else {
                    existing.addEventListener('load', function () {
                        existing.dataset.loaded = 'true';
                        if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
                            resolve(window.NekoChatWindow);
                        } else {
                            reject(new Error('React chat bundle loaded but API is missing'));
                        }
                    }, { once: true });
                    existing.addEventListener('error', function () {
                        existing.dataset.error = 'true';
                        reject(new Error('React chat bundle failed to load'));
                    }, { once: true });
                    return;
                }
            }

            var script = document.createElement('script');
            script.src = BUNDLE_SRC + '?v=' + Date.now();
            script.async = true;
            script.dataset.reactChatWindowBundle = 'true';

            script.onload = function () {
                if (window.NekoChatWindow && (typeof window.NekoChatWindow.mount === 'function' || typeof window.NekoChatWindow.mountChatWindow === 'function')) {
                    resolve(window.NekoChatWindow);
                } else {
                    reject(new Error('React chat bundle loaded but API is missing'));
                }
            };

            script.onerror = function () {
                script.dataset.error = 'true';
                reject(new Error('React chat bundle failed to load'));
            };

            document.body.appendChild(script);
        }).catch(function (error) {
            loadedPromise = null;
            throw error;
        });

        return loadedPromise;
    }

    function getStoredPosition() {
        try {
            var rawLeft = localStorage.getItem(STORAGE_LEFT_KEY);
            var rawTop = localStorage.getItem(STORAGE_TOP_KEY);
            if (rawLeft === null || rawTop === null) return null;
            var left = Number(rawLeft);
            var top = Number(rawTop);
            if (Number.isFinite(left) && Number.isFinite(top)) {
                return { left: left, top: top };
            }
        } catch (_) {}
        return null;
    }

    function persistPosition(left, top) {
        try {
            localStorage.setItem(STORAGE_LEFT_KEY, String(Math.round(left)));
            localStorage.setItem(STORAGE_TOP_KEY, String(Math.round(top)));
        } catch (_) {}
    }

    function rememberExpandedShellPosition(left, top) {
        if (isMobileWidth()) return;
        if (!Number.isFinite(left) || !Number.isFinite(top)) return;
        savedExpandedShellPosition = {
            left: Math.round(left),
            top: Math.round(top)
        };
    }

    function snapshotExpandedShellPositionFromShell() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;
        var rect = shell.getBoundingClientRect();
        rememberExpandedShellPosition(rect.left, rect.top);
    }

    function persistSize(width, height) {
        try {
            localStorage.setItem(STORAGE_WIDTH_KEY, String(Math.round(width)));
            localStorage.setItem(STORAGE_HEIGHT_KEY, String(Math.round(height)));
        } catch (_) {}
    }

    function getStoredSize() {
        try {
            var rawWidth = localStorage.getItem(STORAGE_WIDTH_KEY);
            var rawHeight = localStorage.getItem(STORAGE_HEIGHT_KEY);
            if (rawWidth === null || rawHeight === null) return null;
            var width = Number(rawWidth);
            var height = Number(rawHeight);
            if (Number.isFinite(width) && Number.isFinite(height) && width >= 320 && height >= 280) {
                return { width: width, height: height };
            }
        } catch (_) {}
        return null;
    }

    function restoreSize() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var stored = getStoredSize();
        if (stored) {
            shell.style.width = stored.width + 'px';
            shell.style.height = stored.height + 'px';
        }
    }

    function clampPosition(left, top) {
        var shell = getShell();
        if (!shell) {
            return { left: left, top: top };
        }

        var rect = shell.getBoundingClientRect();
        var width = rect.width || 960;
        var headerHeight = 52;
        var maxLeft = Math.max(0, window.innerWidth - width);
        var maxTop = Math.max(0, window.innerHeight - headerHeight);

        return {
            left: Math.max(0, Math.min(maxLeft, left)),
            top: Math.max(0, Math.min(maxTop, top))
        };
    }

    function applyPosition(left, top) {
        var shell = getShell();
        if (!shell) return;

        var clamped = clampPosition(left, top);
        shell.style.left = clamped.left + 'px';
        shell.style.top = clamped.top + 'px';
        shell.style.transform = 'none';
    }

    function applyCompactSurfacePosition(left, top) {
        var shell = getShell();
        if (!shell) return;

        var rect = shell.getBoundingClientRect();
        var metrics = getCompactSurfaceMetrics();
        var width = metrics.width || rect.width || COMPACT_SURFACE_MAX_WIDTH;
        var height = metrics.height || COMPACT_SURFACE_DEFAULT_HEIGHT;
        applyCompactSurfaceRect(left, top, width, height, { persist: true });
    }

    function positionWindowAtLeftMiddle() {
        var shell = getShell();
        if (!shell || isMobileWidth()) return;

        var rect = shell.getBoundingClientRect();
        var left = Math.max(0, Math.round(window.innerWidth * DESKTOP_DEFAULT_LEFT_RATIO));
        var top = Math.max(0, Math.round((window.innerHeight - rect.height) / 2));
        applyPosition(left, top);
        rememberExpandedShellPosition(left, top);
        persistPosition(left, top);
    }

    function restorePosition() {
        var shell = getShell();
        if (!shell) return;

        if (isMobileWidth()) {
            // 宽度由 CSS calc(100vw - 12px) 控制；transform 的 translate 会污染 applyPosition 坐标。
            shell.style.removeProperty('width');
            shell.style.removeProperty('transform');
            // 不清 height：清掉会让 shell 瞬间回到 CSS 的 `height:auto;max-height:85vh`，
            // grid `auto 1fr auto` 父容器塌缩会把 .message-list 的 clientHeight 挤到几十 px，
            // 浏览器 clamp scrollTop → 0，下一帧 syncMobileContentLayout() 恢复 height 时已经来不及。
            // 保留旧像素值，让紧随其后的 syncMobileContentLayout() 直接覆写，避免中间态。
            // 不清 left/top：手机端允许 expanded 在任意位置飘；只按新视口 clamp 一次，避免旋屏/键盘后溢出。
            if (shell.style.left || shell.style.top) {
                var rect = shell.getBoundingClientRect();
                var clampedLeft = Math.max(0, Math.min(rect.left, window.innerWidth - rect.width));
                var clampedTop = Math.max(0, Math.min(rect.top, window.innerHeight - rect.height));
                shell.style.left = clampedLeft + 'px';
                shell.style.top = clampedTop + 'px';
            }
            return;
        }

        restoreSize();

        var stored = getStoredPosition();
        if (stored) {
            applyPosition(stored.left, stored.top);
            rememberExpandedShellPosition(stored.left, stored.top);
        } else {
            positionWindowAtLeftMiddle();
        }
    }

    function mountWindow() {
        var root = getRoot();
        if (!root) return false;

        var api = window.NekoChatWindow;
        var mount = api && (api.mount || api.mountChatWindow);
        if (typeof mount !== 'function') return false;

        mount(root, buildRenderProps());
        mounted = true;
        return true;
    }

    function renderWindow() {
        var overlay = getOverlay();
        if (!overlay || overlay.hidden) return;
        mountWindow();
        scheduleMobileContentLayout();
    }

    function dispatchHostEvent(name, detail) {
        window.dispatchEvent(new CustomEvent(EVENT_PREFIX + name, { detail: detail }));
    }

    function handleMessageAction(message, action) {
        var detail = {
            message: message,
            action: action
        };

        if (typeof state.onMessageAction === 'function') {
            try {
                state.onMessageAction(message, action);
            } catch (error) {
                console.error('[ReactChatWindow] onMessageAction failed:', error);
            }
        }

        dispatchHostEvent('action', detail);
    }

    function handleComposerSubmit(payload) {
        if (state.homeTutorialInteractionLocked) {
            return;
        }
        var requestId = payload && typeof payload.requestId === 'string' && payload.requestId
            ? payload.requestId
            : ('req-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8));
        var detail = {
            text: payload && typeof payload.text === 'string' ? payload.text : '',
            requestId: requestId
        };

        var hasAttachments = state.composerAttachments && state.composerAttachments.length > 0;
        if (!detail.text.trim() && !hasAttachments) return;

        // Clear stale GalGame options as soon as the user sends anything; the
        // next turn-end will trigger a fresh fetch if the mode is still on.
        // invalidatePendingGalgameRequest unconditionally bumps the seq + aborts
        // the in-flight fetch (so a still-pending wait callback or response
        // can't land into the new turn context); we only re-render when the
        // visible state actually changed.
        if (invalidatePendingGalgameRequest()) {
            renderWindow();
        }

        // Store last submitted text for rollback on RESPONSE_TOO_LONG
        // Preserve original whitespace so rollback restores exactly what the user typed
        if (detail.text.trim()) {
            state.pendingRollbackDrafts[detail.requestId] = detail.text;
        } else {
            delete state.pendingRollbackDrafts[detail.requestId];
        }
        // Clear any stale rollback so it won't overwrite this new draft
        if (state.rollbackDraft) {
            console.log('[ROLLBACK] handleComposerSubmit: clearing rollbackDraft length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        }
        state.rollbackDraft = '';

        if (typeof state.onComposerSubmit === 'function') {
            try {
                state.onComposerSubmit(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onComposerSubmit failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.sendTextPayload === 'function') {
            window.appButtons.sendTextPayload(detail.text, { source: 'react-chat-window', requestId: detail.requestId });
        } else {
            var input = $('textInputBox');
            var sendButton = $('textSendButton');
            if (input && sendButton) {
                input.value = detail.text;
                sendButton.click();
            } else {
                console.warn('[ReactChatWindow] no composer submit handler available');
            }
        }

        dispatchHostEvent('submit', detail);
    }

    function handleCompactHistoryDrop(payload) {
        var detail = payload || {};

        if (typeof state.onCompactHistoryDrop === 'function') {
            try {
                return state.onCompactHistoryDrop(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onCompactHistoryDrop failed:', error);
                return false;
            }
        }
        if (window.appButtons && typeof window.appButtons.sendCompactHistoryDropPayload === 'function') {
            return window.appButtons.sendCompactHistoryDropPayload(detail);
        }
        if ((!detail.images || !detail.images.length) && typeof detail.text === 'string' && detail.text.trim()) {
            handleComposerSubmit({
                text: detail.text,
                requestId: detail.requestId
            });
            return true;
        }
        console.warn('[ReactChatWindow] no compact history drop handler available');
        return false;
    }

    function prepareCompactHistoryDropSubmit(payload) {
        var detail = payload || {};
        var text = typeof detail.text === 'string' ? detail.text.trim() : '';
        var images = Array.isArray(detail.images) ? detail.images : [];
        if (!text && images.length === 0) return false;

        if (invalidatePendingGalgameRequest()) {
            renderWindow();
        }

        var requestId = typeof detail.requestId === 'string' ? detail.requestId : '';
        if (requestId) {
            if (text) {
                state.pendingRollbackDrafts[requestId] = text;
            } else {
                delete state.pendingRollbackDrafts[requestId];
            }
        }
        if (state.rollbackDraft) {
            console.log('[ROLLBACK] prepareCompactHistoryDropSubmit: clearing rollbackDraft length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        }
        state.rollbackDraft = '';
        return true;
    }

    function handleCompactHistoryDragStateChange(payload) {
        var detail = payload || {};

        if (typeof state.onCompactHistoryDragStateChange === 'function') {
            try {
                state.onCompactHistoryDragStateChange(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onCompactHistoryDragStateChange failed:', error);
            }
        }

        dispatchHostEvent('compact-history-drag-state-change', detail);
        window.dispatchEvent(new CustomEvent('neko:compact-history-drag-state-change', { detail: detail }));
    }

    function handleAvatarInteraction(payload) {
        var detail = payload || {};

        if (typeof state.onAvatarInteraction === 'function') {
            try {
                state.onAvatarInteraction(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onAvatarInteraction failed:', error);
            }
        } else {
            console.warn('[ReactChatWindow] no avatar interaction handler registered; dispatching host event only');
        }

        dispatchHostEvent('avatar-interaction', detail);
    }

    function handleAvatarToolStateChange(payload) {
        var detail = payload || {};

        if (typeof state.onAvatarToolStateChange === 'function') {
            try {
                state.onAvatarToolStateChange(detail);
            } catch (error) {
                console.error('[ReactChatWindow] onAvatarToolStateChange failed:', error);
            }
        }

        dispatchHostEvent('avatar-tool-state', detail);
    }

    function handleComposerImportImage() {
        if (typeof state.onComposerImportImage === 'function') {
            try {
                state.onComposerImportImage();
            } catch (error) {
                console.error('[ReactChatWindow] onComposerImportImage failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.openImageImportPicker === 'function') {
            window.appButtons.openImageImportPicker();
        } else {
            console.warn('[ReactChatWindow] no import image handler available');
        }

        dispatchHostEvent('import-image', {});
    }

    function handleComposerScreenshot() {
        if (typeof state.onComposerScreenshot === 'function') {
            try {
                state.onComposerScreenshot();
            } catch (error) {
                console.error('[ReactChatWindow] onComposerScreenshot failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.captureScreenshotToPendingList === 'function') {
            window.appButtons.captureScreenshotToPendingList();
        } else {
            console.warn('[ReactChatWindow] no screenshot handler available');
        }

        dispatchHostEvent('screenshot', {});
    }

    function handleComposerRemoveAttachment(attachmentId) {
        if (typeof state.onComposerRemoveAttachment === 'function') {
            try {
                state.onComposerRemoveAttachment(String(attachmentId || ''));
            } catch (error) {
                console.error('[ReactChatWindow] onComposerRemoveAttachment failed:', error);
            }
        } else if (window.appButtons && typeof window.appButtons.removePendingAttachmentById === 'function') {
            window.appButtons.removePendingAttachmentById(String(attachmentId || ''));
        } else {
            console.warn('[ReactChatWindow] no remove attachment handler available');
        }

        dispatchHostEvent('remove-attachment', { attachmentId: attachmentId });
    }

    /**
     * Rollback last submitted text to the React composer input.
     * Called when backend discards response due to RESPONSE_TOO_LONG.
     */
    function rollbackLastDraft(requestId) {
        var rollbackText = (requestId && Object.prototype.hasOwnProperty.call(state.pendingRollbackDrafts, requestId))
            ? state.pendingRollbackDrafts[requestId]
            : '';
        if (!rollbackText) return;
        // Use a unique key each time so React useEffect can distinguish invocations
        state.rollbackDraft = rollbackText;
        state._rollbackKey = 'rb-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
        delete state.pendingRollbackDrafts[requestId];
        console.log('[ROLLBACK] rollbackLastDraft: rollbackDraftPresent=true length=' + state.rollbackDraft.length + ' key=' + state._rollbackKey);
        renderWindow();
    }

    function clearPendingRollbackDraft(requestId) {
        if (!requestId) return;
        delete state.pendingRollbackDrafts[requestId];
    }

    function handleJukeboxClick() {
        try {
            if (typeof window.__nekoJukeboxToggle === 'function') {
                // Electron 多窗口模式：通过 IPC 打开独立 Jukebox 窗口
                window.__nekoJukeboxToggle();
            } else if (typeof window.Jukebox !== 'undefined' && typeof window.Jukebox.toggle === 'function') {
                window.Jukebox.toggle();
            } else {
                console.warn('[ReactChatWindow] Jukebox not available');
            }
        } finally {
            dispatchHostEvent('jukebox-click', {});
        }
    }

    function captureAvatarDirect() {
        if (!window.avatarPortrait || typeof window.avatarPortrait.capture !== 'function') {
            // Electron 多窗口模式：通过 IPC 请求 Pet 窗口截取头像
            if (window.__NEKO_MULTI_WINDOW__ && typeof window.__nekoRequestAvatarPreview === 'function') {
                // 优先使用已缓存的外部头像
                if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                    var cached = window.appChatAvatar.getCurrentAvatarDataUrl();
                    if (cached) {
                        window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
                            detail: { dataUrl: cached, source: 'cached' }
                        }));
                        showToast(getI18nText('chat.avatarPreviewReady', '头像已更新'), 2500);
                        return;
                    }
                }
                showToast(getI18nText('chat.avatarPreviewGenerating', '正在生成当前头像...'), 2000);
                var finished = false;
                var timerId = null;
                var finish = function (success) {
                    if (finished) return;
                    finished = true;
                    window.removeEventListener('neko:avatar-preview-ipc-result', onResult);
                    if (timerId) { clearTimeout(timerId); timerId = null; }
                    if (success) {
                        showToast(getI18nText('chat.avatarPreviewReady', '头像已更新'), 2500);
                    } else {
                        showToast(getI18nText('chat.avatarPreviewFailed', '生成头像失败'), 3000);
                    }
                };
                var onResult = function (e) {
                    finish(!!(e.detail && e.detail.dataUrl));
                };
                window.addEventListener('neko:avatar-preview-ipc-result', onResult);
                timerId = setTimeout(function () { finish(false); }, 10000);
                try {
                    window.__nekoRequestAvatarPreview();
                } catch (err) {
                    console.error('[ReactChatWindow] __nekoRequestAvatarPreview threw:', err);
                    finish(false);
                }
                return;
            }
            showToast(getI18nText('chat.avatarPreviewUnavailable', '头像预览功能尚未就绪。'), 3000);
            return;
        }

        showToast(getI18nText('chat.avatarPreviewGenerating', '正在生成当前头像...'), 2000);

        window.avatarPortrait.capture({
            width: 320, height: 320, padding: 0.035,
            shape: 'rounded', radius: 40,
            background: 'rgba(255, 255, 255, 0.96)',
            includeDataUrl: true
        }).then(function (result) {
            if (result && result.dataUrl) {
                // Dispatch the same event that app-chat-adapter.js already listens to
                window.dispatchEvent(new CustomEvent('chat-avatar-preview-updated', {
                    detail: {
                        dataUrl: result.dataUrl,
                        modelType: result.modelType || '',
                        source: 'react-chat-window'
                    }
                }));
                showToast(getI18nText('chat.avatarPreviewReady', '头像已更新'), 2500);
            } else {
                console.warn('[ReactChatWindow] Avatar capture completed without dataUrl');
                showToast(getI18nText('chat.avatarPreviewFailed', '生成头像失败'), 3000);
            }
        }).catch(function (error) {
            console.error('[ReactChatWindow] Avatar capture failed:', error);
            showToast(getI18nText('chat.avatarPreviewFailed', '生成头像失败'), 3000);
        });
    }

    function handleAvatarGeneratorClick() {
        try {
            // 统一走独立头像预览弹窗；弹窗模块自行处理缓存与 IPC 回退。
            if (window.appChatAvatar && typeof window.appChatAvatar.showPopup === 'function') {
                var anchor = document.getElementById('avatarPreviewHeaderButton')
                    || document.getElementById('avatarPreviewButton')
                    || null;
                window.appChatAvatar.showPopup(anchor);
                return;
            }
            // 极端兜底：弹窗模块加载失败时仍保持原有直采逻辑。
            captureAvatarDirect();
        } finally {
            dispatchHostEvent('avatar-generator-click', {});
        }
    }

    function handleExportConversationClick() {
        try {
            if (window.appChatExport && typeof window.appChatExport.open === 'function') {
                window.appChatExport.open();
                return;
            }
            var exportButton = document.getElementById('exportConversationButton');
            if (exportButton && typeof exportButton.click === 'function') {
                exportButton.click();
                return;
            }
            showToast(getI18nText('chat.exportPreviewFailed', '导出预览生成失败'), 3000);
        } finally {
            dispatchHostEvent('chat-export-click', {});
        }
    }

    function handleTranslateToggle() {
        var bridge = window.subtitleBridge;
        var next;

        try {
            if (bridge && typeof bridge.toggle === 'function') {
                // Use full toggle with runtime side effects (hide/show subtitle, clear timers, re-translate)
                next = bridge.toggle();
            } else {
                throw new Error('subtitleBridge.toggle unavailable');
            }
        } catch (err) {
            console.warn('[ReactChatWindow] bridge.toggle failed, using fallback:', err);
            // Fallback: flip flag manually if bridge not loaded or threw
            var appSt = window.appState;
            var subtitleStore = window.nekoSubtitleShared;
            var subtitleState = subtitleStore && typeof subtitleStore.getSettings === 'function'
                ? subtitleStore.getSettings()
                : null;
            var current = (appSt && typeof appSt.subtitleEnabled !== 'undefined')
                ? appSt.subtitleEnabled
                : (subtitleState ? !!subtitleState.subtitleEnabled : (localStorage.getItem('subtitleEnabled') === 'true'));
            next = !current;
            if (appSt) appSt.subtitleEnabled = next;
            if (subtitleStore && typeof subtitleStore.updateSettings === 'function') {
                subtitleStore.updateSettings({
                    subtitleEnabled: next
                }, {
                    source: 'react-chat-fallback-toggle'
                });
            } else {
                localStorage.setItem('subtitleEnabled', String(next));
            }
            if (window.appSettings && typeof window.appSettings.saveSettings === 'function') {
                window.appSettings.saveSettings();
            }
        }

        // Update React prop to reflect new state
        state.viewProps = Object.assign({}, ensureViewProps(), { translateEnabled: next });
        renderWindow();

        dispatchHostEvent('translate-toggle', { enabled: next });
    }

    // ============================ GalGame mode ============================
    function isGalgameModeTemporarilyDisabled() {
        return !!state.galgameTemporarilyDisabled;
    }

    function isHomeTutorialRunning() {
        var manager = window.universalTutorialManager;
        return !!(
            manager
            && manager.currentPage === 'home'
            && manager.isTutorialRunning
        );
    }

    function isHomeTutorialInteractionLocked() {
        if (state.homeTutorialInteractionLocked || isHomeTutorialRunning()) {
            return true;
        }
        try {
            return typeof window.isNekoHomeTutorialInteractionLocked === 'function'
                && window.isNekoHomeTutorialInteractionLocked() === true;
        } catch (_) {
            return false;
        }
    }

    function setGalgameModeTemporarilyDisabled(disabled) {
        var next = !!disabled;
        var changed = state.galgameTemporarilyDisabled !== next;
        state.galgameTemporarilyDisabled = next;

        if (next) {
            setGalgameModeEnabled(false, { persist: false });
        } else if (changed) {
            setGalgameModeEnabled(readGalgameModePreference(), {
                persist: false,
                suppressRefetch: true
            });
        }
    }

    function setGalgameModeEnabled(enabled, options) {
        var requestOptions = options || {};
        var next = !!enabled;
        if (next && !requestOptions.force && (isGalgameModeTemporarilyDisabled() || isHomeTutorialInteractionLocked())) {
            next = false;
        }
        var changed = state.galgameModeEnabled !== next;
        state.galgameModeEnabled = next;
        if (!next) {
            state.galgameOptions = [];
            state.galgameOptionsLoading = false;
            state._galgameRequestSeq += 1;
            // Toggling off mid-fetch must also kill the in-flight request so
            // the summary-tier inference doesn't keep running uselessly until
            // the 30s timeout (or finishes and is silently discarded).
            abortPendingGalgameFetch();
        }
        applyGalgameBodyClass();
        if ((!requestOptions || requestOptions.persist !== false) && !isGalgameModeTemporarilyDisabled()) {
            persistGalgameModePreference(next);
        }
        renderWindow();
        if (changed) {
            // 派发 effective 值（与 body class 一致）：composer 隐藏期间即使
            // setGalgameModeEnabled(true) 也广播 enabled=false，避免监听器
            // (chat.html syncWindowToGalgameMin 等) 与 body class 状态分歧。
            dispatchHostEvent('galgame-mode-change', { enabled: getEffectiveGalgameEnabled() });
            // OFF→ON: if the chat overlay is currently visible, refetch the
            // latest turn's options so the user sees A/B/C immediately rather
            // than waiting for the next turn-end. Gating on overlay visibility
            // avoids wasting a summary-tier call during init() (where the
            // window is still hidden) and respects the same skip rule the
            // turn-end handler uses for voice-only / proactive paths.
            if (next && !requestOptions.suppressRefetch) {
                var overlay = getOverlay();
                if (overlay && !overlay.hidden) {
                    fetchGalgameOptionsForLatestTurn();
                }
            }
        }
    }

    function waitForAssistantBubblesFlushed(maxWaitMs) {
        // Resolve as soon as app-chat-adapter's realistic-mode queue is empty
        // and not in the middle of processing a sentence. In merge / non-Gemini
        // paths the queue is never populated and the predicate is true on the
        // first check, so this just collapses to a microtask.
        return new Promise(function (resolve) {
            var deadline = Date.now() + (typeof maxWaitMs === 'number' ? maxWaitMs : 4000);
            function isDrained() {
                var q = window._realisticGeminiQueue;
                var processing = !!window._isProcessingRealisticQueue;
                var queueEmpty = !Array.isArray(q) || q.length === 0;
                return queueEmpty && !processing;
            }
            if (isDrained()) {
                resolve();
                return;
            }
            var pollId = setInterval(function () {
                if (isDrained() || Date.now() >= deadline) {
                    clearInterval(pollId);
                    resolve();
                }
            }, 100);
        });
    }

    function getRecentGalgameMessageHistory() {
        var msgs = Array.isArray(state.messages) ? state.messages : [];
        var collected = [];
        for (var i = msgs.length - 1; i >= 0 && collected.length < GALGAME_HISTORY_LIMIT; i--) {
            var m = msgs[i];
            if (!m) continue;
            if (m.role !== 'assistant' && m.role !== 'user') continue;
            var text = '';
            if (Array.isArray(m.blocks)) {
                for (var j = 0; j < m.blocks.length; j++) {
                    var block = m.blocks[j];
                    if (block && block.type === 'text' && typeof block.text === 'string') {
                        text += (text ? '\n' : '') + block.text;
                    }
                }
            }
            text = text.replace(/\[play_music:[^\]]*(\]|$)/g, '').trim();
            if (!text) continue;
            collected.push({ role: m.role, text: text });
        }
        return collected.reverse();
    }

    function pickAcceptLanguage() {
        try {
            if (typeof window.getCurrentLocale === 'function') {
                var loc = window.getCurrentLocale();
                if (loc) return String(loc);
            }
        } catch (_) {}
        if (window.i18next && typeof window.i18next.language === 'string') return window.i18next.language;
        if (typeof navigator !== 'undefined' && typeof navigator.language === 'string') return navigator.language;
        return '';
    }

    var GALGAME_FETCH_TIMEOUT_MS = 30000;
    var _galgameAbortController = null;

    function abortPendingGalgameFetch() {
        if (_galgameAbortController) {
            try { _galgameAbortController.abort(); } catch (_) {}
            _galgameAbortController = null;
        }
    }

    function fetchGalgameOptionsForLatestTurn() {
        if (isGalgameModeTemporarilyDisabled()) return;
        if (!state.galgameModeEnabled) return;
        var history = getRecentGalgameMessageHistory();
        if (!history.length) return;
        if (history[history.length - 1].role !== 'assistant') return;

        // Cancel any prior in-flight request — keeps summary-tier load down
        // when turns arrive faster than the model can answer, and ensures a
        // hung server side isn't held open while the panel is no longer
        // listening for it.
        abortPendingGalgameFetch();
        var controller = (typeof AbortController === 'function') ? new AbortController() : null;
        _galgameAbortController = controller;
        var requestSeq = ++state._galgameRequestSeq;
        state.galgameOptions = [];
        state.galgameOptionsLoading = true;
        renderWindow();

        // 30s timeout cleanup: clears loading state in addition to aborting,
        // so the catch's blanket AbortError swallow doesn't leave the panel
        // stuck. Aborts triggered by invalidation paths instead bump the seq
        // *and* clear state up front, so the catch's seq-mismatch return is
        // still the right thing for those.
        var timeoutId = controller ? setTimeout(function () {
            timeoutId = null;
            if (_galgameAbortController === controller) {
                _galgameAbortController = null;
            }
            try { controller.abort(); } catch (_) {}
            if (requestSeq !== state._galgameRequestSeq) return;
            state.galgameOptions = [];
            state.galgameOptionsLoading = false;
            renderWindow();
        }, GALGAME_FETCH_TIMEOUT_MS) : null;

        var payload = {
            messages: history,
            language: pickAcceptLanguage()
        };
        try {
            if (window.appState && typeof window.appState.lanlan_name === 'string' && window.appState.lanlan_name) {
                payload.lanlan_name = window.appState.lanlan_name;
            }
        } catch (_) {}
        try {
            // getCurrentUserName() returns the literal English placeholder 'You'
            // when no real user name can be resolved. Sending that overrides the
            // backend's localized GALGAME_DEFAULT_MASTER_PLACEHOLDER fallback,
            // so we only forward a name when it's a genuine user-set value.
            var currentUserName = getCurrentUserName();
            if (typeof currentUserName === 'string' && currentUserName && currentUserName !== 'You') {
                payload.master_name = currentUserName;
            }
        } catch (_) {}

        function clearTimer() {
            if (timeoutId !== null) {
                clearTimeout(timeoutId);
                timeoutId = null;
            }
            if (_galgameAbortController === controller) {
                _galgameAbortController = null;
            }
        }

        fetch('/api/galgame/options', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller ? controller.signal : undefined
        }).then(function (resp) {
            if (!resp || !resp.ok) throw new Error('HTTP ' + (resp && resp.status));
            return resp.json();
        }).then(function (data) {
            clearTimer();
            if (requestSeq !== state._galgameRequestSeq) return;
            var opts = (data && Array.isArray(data.options)) ? data.options.slice(0, 3) : [];
            opts = opts.filter(function (o) {
                return o && typeof o.label === 'string' && typeof o.text === 'string';
            }).map(function (o) {
                return { label: String(o.label).slice(0, 4), text: String(o.text) };
            });
            state.galgameOptions = opts;
            state.galgameOptionsLoading = false;
            renderWindow();
        }).catch(function (err) {
            clearTimer();
            if (requestSeq !== state._galgameRequestSeq) return;
            // Aborts come from invalidation paths that have already cleared
            // visible state, so swallow them silently.
            if (err && err.name === 'AbortError') return;
            console.warn('[ReactChatWindow] galgame options fetch failed:', err);
            state.galgameOptions = [];
            state.galgameOptionsLoading = false;
            renderWindow();
        });
    }

    function handleGalgameModeToggle() {
        if (isHomeTutorialInteractionLocked()) {
            setGalgameModeEnabled(false, { persist: false });
            return;
        }
        if (isGalgameModeTemporarilyDisabled()) {
            setGalgameModeEnabled(false, { persist: false });
            return;
        }
        // setGalgameModeEnabled handles the OFF→ON refetch internally.
        setGalgameModeEnabled(!state.galgameModeEnabled);
    }

    function handleGalgameOptionSelect(option) {
        if (isHomeTutorialInteractionLocked()) return;
        if (!option || typeof option.text !== 'string') return;
        var text = option.text.trim();
        if (!text) return;
        // Clear options immediately so the panel doesn't keep stale entries while
        // the next turn streams in.
        state.galgameOptions = [];
        state.galgameOptionsLoading = false;
        state._galgameRequestSeq += 1;
        renderWindow();

        var detail = {
            text: text,
            requestId: 'galgame-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8),
            source: 'galgame-option',
            label: option.label
        };
        handleComposerSubmit(detail);
        dispatchHostEvent('galgame-option-select', detail);
    }

    // ---- 通用 ChoicePrompt：mini-game invite 三选项 ----
    // React 组件 onChoice 回调把 option + source 一起传上来。source==='galgame'
    // 走旧路径（dummy fallback，正常不会到这里——galgame 仍然走 onGalgameOptionSelect
    // 直接 callback；这里只是 BC 兜底）；source==='mini_game_invite' 走新逻辑。

    function handleChoiceSelect(option, source) {
        if (isHomeTutorialInteractionLocked()) return;
        if (!option || typeof option.choice !== 'string') return;
        if (source === 'galgame') {
            // Forward to legacy galgame handler if it shows up here
            if (typeof option.text === 'string') {
                handleGalgameOptionSelect(option);
            }
            return;
        }
        if (source === 'mini_game_invite') {
            handleMiniGameInviteChoice(option);
            return;
        }
    }

    function handleCompactChatStateChange(nextCompactChatState) {
        setCompactChatState(nextCompactChatState);
    }

    function handleMiniGameInviteChoice(option) {
        if (isHomeTutorialInteractionLocked()) return;
        var prompt = state.choicePrompt;
        if (!prompt || prompt.source !== 'mini_game_invite') return;
        var sessionId = prompt.sessionId || '';
        // 暂存原 prompt 用于失败回滚——网络异常时让用户能再点一次（CodeRabbit
        // Major 指出原版 fetch fail 仅 console.warn，用户看着空 UI 不知道发生
        // 啥）。立即清 prompt 防连点；fail catch 里恢复。
        var rollbackPrompt = prompt;
        state.choicePrompt = null;
        renderWindow();

        var lanlanName = '';
        try {
            // 优先读 window.appState.lanlan_name —— 角色切换时 appState 先更新，
            // window.lanlan_config 可能滞后；用旧 lanlan_name 调 endpoint 会被
            // 后端按错误角色查 pending invite 直接 expired。同 GalGame 请求路径
            // 保持一致（CodeRabbit Major 指出）。
            lanlanName = (window.appState && window.appState.lanlan_name)
                || (window.lanlan_config && window.lanlan_config.lanlan_name)
                || '';
        } catch (_) {}

        var requestBody = {
            lanlan_name: lanlanName,
            choice: option.choice,
            session_id: sessionId
        };

        // accept 路径预开 popup（**仍在用户点击的同步上下文**）保留 user-gesture
        // 上下文。后续 fetch resolve 后再 window.open 会被浏览器 popup blocker
        // 识别为非手势触发拦截——pre-open 后 .location.href 注入 URL 不会被拦
        // （codex P2 指出原版 fetch 后 window.open 失败时 state 已 responded
        // 用户失去重试入口）。decline / later 路径不开窗口，无此处理。
        var preOpenedWindow = null;
        if (option.choice === 'accept') {
            try {
                preOpenedWindow = window.open('', '_blank');
                if (preOpenedWindow) {
                    // 给个临时占位文本免得用户看到 about:blank 一闪
                    try {
                        preOpenedWindow.document.write(
                            '<title>Loading…</title><body style="background:#111;color:#888;font:14px sans-serif;padding:20px">Loading mini-game…</body>'
                        );
                    } catch (_) {}
                }
            } catch (_) {
                preOpenedWindow = null;
            }
        }
        var closePreOpened = function () {
            if (preOpenedWindow && !preOpenedWindow.closed) {
                try { preOpenedWindow.close(); } catch (_) {}
            }
        };

        // 必须带 CSRF token：后端 endpoint 用 _validate_local_mutation_request
        // 拒绝缺 token 的请求，否则所有合法点击都会被 403 reject、prompt 已清掉
        // 但 invite state 没更新 —— codex P1 指出。沿用 nekoLocalMutationSecurity
        // 共享 helper（其它 prompt endpoint 同款），含 token 缺失时 refresh + 重
        // 试一次的协议。
        var bodyJson = JSON.stringify(requestBody);
        var doFetch = function (headers) {
            return fetch('/api/mini_game/invite/respond', {
                method: 'POST',
                headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
                body: bodyJson
            });
        };
        var sec = window.nekoLocalMutationSecurity;
        var firstHeadersPromise = sec && typeof sec.getMutationHeaders === 'function'
            ? sec.getMutationHeaders()
            : Promise.resolve({});
        firstHeadersPromise.then(doFetch).then(function (resp) {
            // 403 + csrf_validation_failed → refresh token 重试一次（与 prompt
            // endpoint 同协议）
            if (resp.status === 403 && sec && typeof sec.refreshToken === 'function') {
                return resp.clone().json().catch(function () { return null; }).then(function (errBody) {
                    var code = errBody && errBody.error_code;
                    if (code === 'csrf_validation_failed') {
                        return sec.refreshToken().then(function () {
                            return sec.getMutationHeaders();
                        }).then(doFetch);
                    }
                    return resp;
                });
            }
            return resp;
        }).then(function (resp) {
            return resp.ok ? resp.json() : Promise.reject(new Error('HTTP ' + resp.status));
        }).then(function (data) {
            if (!data || data.action !== 'open_game' || !data.game_url) {
                // 非 accept outcome（cooldown / suppress / expired）→ 关掉占位 popup
                closePreOpened();
                return;
            }
            // accept：优先注入 URL 进 pre-opened popup（保留用户手势上下文，
            // 浏览器 popup blocker 不拦）；pre-open 失败时 fallback 调
            // launchMiniGameInternal（可能被拦但留个 console.warn 兜底）。
            if (preOpenedWindow && !preOpenedWindow.closed) {
                try {
                    preOpenedWindow.location.href = data.game_url;
                    if (sessionId) {
                        state._launchedMiniGameSessionIds[sessionId] = true;
                    }
                    return;
                } catch (err) {
                    console.warn('[MiniGameInvite] pre-opened window navigation failed:', err);
                    closePreOpened();
                }
            }
            // pre-open 失败 fallback：直接 window.open（有被 popup blocker 拦
            // 的风险，但 accept-path-via-pre-open 已是主路径，到此处罕见）。
            launchMiniGameInternal({
                sessionId: sessionId,
                gameType: data.game_type || rollbackPrompt.gameType || '',
                url: data.game_url,
                source: 'button'
            });
        }).catch(function (err) {
            console.warn('[MiniGameInvite] respond endpoint failed:', err);
            closePreOpened();
            // 网络/服务异常 → 回滚 prompt 让用户能再试。但只在当前 prompt 仍是
            // null（即用户没在 fetch 期间触发新 prompt）且会话仍未被 launch 过的
            // 情况下回滚——否则强复活旧 UI 可能撞新邀请。
            if (state.choicePrompt === null
                    && sessionId
                    && !state._launchedMiniGameSessionIds[sessionId]) {
                state.choicePrompt = rollbackPrompt;
                renderWindow();
            }
        });
    }

    function setMiniGameInvitePrompt(payload) {
        if (!payload) return;
        var sessionId = String(payload.sessionId || '');
        if (!sessionId) return;
        // 已经为该 session 开过游戏了 → 忽略 stale options（罕见：邀请 push 比
        // 用户键盘/按钮路径慢，但为了对偶仍 guard 一下）
        if (state._launchedMiniGameSessionIds[sessionId]) return;
        var rawOptions = Array.isArray(payload.options) ? payload.options : [];
        if (!rawOptions.length) return;
        // map → filter，再 recheck 长度——后端数据异常导致全部 filter 掉时不
        // 渲染空按钮 prompt（CodeRabbit Minor 指出原版只检 raw 长度漏了这条）。
        var cleanedOptions = rawOptions.map(function (o) {
            return {
                choice: String((o && o.choice) || ''),
                label: String((o && o.label) || '')
            };
        }).filter(function (o) { return o.choice && o.label; });
        if (!cleanedOptions.length) {
            console.warn('[MiniGameInvite] all options filtered out, skipping render', payload);
            return;
        }
        state.choicePrompt = {
            source: 'mini_game_invite',
            sessionId: sessionId,
            gameType: String(payload.gameType || ''),
            options: cleanedOptions
        };
        // mini-game invite 占用 composer 底部 slot 视觉位 → galgame options
        // 让位（App.tsx 已把 galgame slot 在 choicePromptHasOptions 下不挂树）。
        // 这里同步 abort 任何 in-flight / pending wait 的 galgame fetch + 清掉
        // 残留 loading/options state：
        //   1) 不再浪费 summary tier 推理（proactive invite 文本基本是
        //      sudden-context，galgame option 生成大概率 timeout / unparseable
        //      → 全是 fallback，纯浪费）
        //   2) 防止 fetch 在 invite 解决前才返回，写回 state.galgameOptions
        //      让 invite dismiss 后老结果突然冒出来（A/B/C 选项是基于 invite
        //      文本生成的，与后续对话无关）
        invalidatePendingGalgameRequest();
        renderWindow();
    }

    function dismissChoicePromptIfMatches(sessionId) {
        if (!sessionId) return;
        if (state.choicePrompt
                && state.choicePrompt.source === 'mini_game_invite'
                && state.choicePrompt.sessionId === sessionId) {
            state.choicePrompt = null;
            renderWindow();
        }
    }

    function handleMiniGameInviteResolved(payload) {
        if (!payload) return;
        var sessionId = String(payload.sessionId || '');
        // 任一 outcome（open_game / cooldown / suppress）都 dismiss 当前 prompt——
        // 跨窗口一致性。即便本 page 不是触发方，也保持 UI 同步。
        dismissChoicePromptIfMatches(sessionId);
        // launch path（仅 keyword 触发会带 game_url，button path backend 已不推
        // game_url）：多窗口 Electron 模式下 backend 通过 RAW_MESSAGE IPC 把
        // event 转给所有 page (pet + chat.html mirrors)，每个 page 都执行此函数。
        // 不分 ownership 直接 window.open 会让所有 page 各自开一个 game 窗口
        // （codex P2 指出，per-page _launchedMiniGameSessionIds 跨 page 不 dedupe）。
        // 约定：only **non-follower** owner page (pet / 单窗口) 处理 WS-trigger
        // launch；chat.html follower (window.__NEKO_MULTI_WINDOW__ === true) 仅
        // dismiss UI。Button path 不走这条 WS launch（HTTP 响应里 chat.html 自己
        // launch），所以不会双开。
        if (payload.action === 'open_game' && payload.url) {
            if (window.__NEKO_MULTI_WINDOW__) {
                return;  // chat.html follower：let pet leader 处理 launch
            }
            launchMiniGameInternal({
                sessionId: sessionId,
                gameType: String(payload.gameType || ''),
                url: payload.url,
                source: 'ws'
            });
        }
    }

    function launchMiniGameInternal(payload) {
        if (!payload || !payload.url) return;
        var sessionId = String(payload.sessionId || '');
        // 同一 session 只 open 一次：按钮 endpoint 直接 open 后，backend 还会 push
        // mini_game_invite_resolved（cross-window 一致性广播）；不 dedupe 会双开。
        if (sessionId && state._launchedMiniGameSessionIds[sessionId]) return;
        // window.open 在 Electron 模式下被主进程 setWindowOpenHandler 拦截开独立
        // BrowserWindow；普通浏览器是新 tab。'_blank' target 让浏览器治理一致。
        // dedupe flag 只在成功后设——popup blocker / throw 时让用户能再触发一次
        // (codex P2 + CodeRabbit Major 指出原版 set-before-open 会让失败的 session
        // 永远被 dedupe 锁死，prompt 已清掉用户彻底失去入口)。
        var opened = false;
        try {
            var w = window.open(payload.url, '_blank');
            if (!w) {
                console.warn('[MiniGameInvite] window.open returned null (popup blocked?)');
            } else {
                opened = true;
            }
        } catch (err) {
            console.warn('[MiniGameInvite] window.open failed:', err);
        }
        if (opened && sessionId) {
            state._launchedMiniGameSessionIds[sessionId] = true;
        }
    }

    function setViewProps(nextViewProps) {
        var nextProps = nextViewProps || {};
        if (Object.prototype.hasOwnProperty.call(nextProps, 'chatSurfaceMode')) {
            var normalizedChatSurfaceMode = normalizeChatSurfaceMode(nextProps.chatSurfaceMode);
            var previousChatSurfaceMode = getCurrentChatSurfaceMode();
            if (normalizedChatSurfaceMode !== previousChatSurfaceMode
                && !Object.prototype.hasOwnProperty.call(nextProps, 'compactChatState')) {
                resetCompactChatState();
            }
            if (normalizedChatSurfaceMode !== 'minimized') {
                lastRestorableChatSurfaceMode = normalizedChatSurfaceMode;
            } else if (previousChatSurfaceMode !== 'minimized') {
                lastRestorableChatSurfaceMode = previousChatSurfaceMode;
            }
            state.chatSurfaceMode = normalizedChatSurfaceMode;
        }
        if (Object.prototype.hasOwnProperty.call(nextProps, 'compactChatState')) {
            state.compactChatState = normalizeCompactChatState(nextProps.compactChatState);
        }
        state.viewProps = Object.assign({}, ensureViewProps(), nextProps, {
            chatSurfaceMode: getCurrentChatSurfaceMode(),
            compactChatState: getCurrentCompactChatState()
        });
        renderWindow();
        return state.viewProps;
    }

    function invalidatePendingGalgameRequest() {
        // Conversation advanced / switched / cleared — drop any in-flight
        // options fetch (or pending wait callback that hasn't fired yet) so
        // its response can't render stale A/B/C into the new context.
        // The seq bump must be UNCONDITIONAL: callers like
        // waitForAssistantBubblesFlushed snapshot _galgameRequestSeq before
        // their fetch goes out, so even when the panel is idle (loading
        // false, options empty) we still need to advance the seq to invalidate
        // those pending callbacks. The fetch itself is also aborted.
        state._galgameRequestSeq += 1;
        abortPendingGalgameFetch();
        var hadVisibleState = state.galgameOptionsLoading
            || (state.galgameOptions && state.galgameOptions.length > 0);
        if (hadVisibleState) {
            state.galgameOptions = [];
            state.galgameOptionsLoading = false;
        }
        return hadVisibleState;
    }

    function setMessages(messages) {
        // Compute fallback start past any explicit sortKey in incoming batch
        var maxIncomingSortKey = Array.isArray(messages)
            ? messages.reduce(function (max, message) {
                var key = message && typeof message.sortKey === 'number' && Number.isFinite(message.sortKey)
                    ? message.sortKey : null;
                return (key !== null && key > max) ? key : max;
            }, -1)
            : -1;
        var nextSortKey = Math.max(_sortKeySeq, maxIncomingSortKey + 1);
        var normalized = Array.isArray(messages)
            ? messages.map(function (message) {
                return normalizeMessage(message, nextSortKey++);
            }).filter(Boolean)
            : [];
        state.messages = sortMessages(normalized);
        _sortKeySeq = nextSortKey;
        if (state.messages.length > MAX_MESSAGES) {
            state.messages = state.messages.slice(-MAX_MESSAGES);
        }
        invalidatePendingGalgameRequest();
        renderWindow();
        return state.messages;
    }

    function setComposerHidden(hidden) {
        var next = !!hidden;
        var changed = state.composerHidden !== next;
        state.composerHidden = next;
        if (changed) {
            // composer 隐藏/显示切换会改变 effective galgame body class（参见
            // applyGalgameBodyClass），同步刷新一次；否则在 galgame ON 期间
            // 触发请她离开，body 仍带 galgame-mode-enabled，min-height:385px 撑住
            // 窗口底部一片空白，被用户感知为"输入框没隐藏"。
            applyGalgameBodyClass();
            // 复用现有 change 事件通知 chat.html 的 syncWindowToGalgameMin 等监听器
            // 重新评估窗口最小高度；effective OFF 时它会跳过撑高（b.height >= minH 兜底）。
            dispatchHostEvent('galgame-mode-change', { enabled: getEffectiveGalgameEnabled() });
        }
        renderWindow();
    }

    function setHomeTutorialInteractionLocked(locked, reason) {
        var next = !!locked;
        if (state.homeTutorialInteractionLocked === next) {
            return;
        }
        state.homeTutorialInteractionLocked = next;
        state.viewProps = Object.assign({}, ensureViewProps(), {
            composerDisabled: next
        });
        renderWindow();
    }

    function deactivateToolCursor() {
        state._toolCursorResetKey = 'tcr-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
        renderWindow();
    }

    function setComposerAttachments(attachments) {
        var prevHas = state.composerAttachments && state.composerAttachments.length > 0;
        state.composerAttachments = Array.isArray(attachments)
            ? attachments.map(function (attachment, index) {
                if (!attachment || typeof attachment !== 'object' || !attachment.url) return null;
                return {
                    id: String(attachment.id || ('attachment-' + index)),
                    url: String(attachment.url),
                    alt: attachment.alt ? String(attachment.alt) : ''
                };
            }).filter(Boolean)
            : [];
        var nextHas = state.composerAttachments.length > 0;
        applyAttachmentsBodyClass(nextHas);
        renderWindow();
        if (prevHas !== nextHas) {
            // chat.html 的 syncWindowToAttachmentsMin 监听这条事件，0→N 时
            // 主动 setBounds 把 Electron 窗口撑高到能容纳附件 + 输入框，避免
            // 附件刚贴上来就把输入区顶出可视区。和 galgame-mode-change 对称。
            dispatchHostEvent('composer-attachments-change', { hasAttachments: nextHas });
        }
        return state.composerAttachments;
    }

    var MAX_MESSAGES = 50;

    function appendMessage(message) {
        var normalized = normalizeMessage(message, _sortKeySeq++);
        if (!normalized) return null;

        state.messages = sortMessages(state.messages.concat([normalized]));
        if (state.messages.length > MAX_MESSAGES) {
            state.messages = state.messages.slice(-MAX_MESSAGES);
        }
        // A new user-role message means the conversation has advanced — even
        // when the message came in via voice / proactive / sendTextPayload
        // rather than the React composer. Invalidate any pending GalGame fetch
        // so its response can't render against the old turn context.
        if (normalized.role === 'user') {
            invalidatePendingGalgameRequest();
        }
        renderWindow();
        return normalized;
    }

    function updateMessage(messageId, patch) {
        var updatedMessage = null;

        state.messages = state.messages.map(function (message, index) {
            if (String(message.id) !== String(messageId)) return message;
            updatedMessage = normalizeMessage(Object.assign({}, message, patch || {}), index);
            return updatedMessage || message;
        });

        state.messages = sortMessages(state.messages);
        renderWindow();
        return updatedMessage;
    }

    function removeMessage(messageId) {
        var beforeLength = state.messages.length;
        state.messages = state.messages.filter(function (message) {
            return String(message.id) !== String(messageId);
        });
        var changed = state.messages.length !== beforeLength;
        if (changed) {
            renderWindow();
        }
        return changed;
    }

    function clearMessages() {
        state.messages = [];
        _sortKeySeq = 0;
        invalidatePendingGalgameRequest();
        // 角色切换 / cloud reload 等触发 clearMessages 的路径也必须清掉 mini-game
        // invite prompt——否则旧角色的按钮残留在新 context 里，用户点了 endpoint
        // 会按新 lanlan_name 查旧 session_id 直接 expired。dedupe set 也清，防止
        // 上一会话的 launched 标记错误地阻断新会话同 session_id 的 launch（虽然
        // session_id 是 uuid 实际撞概率几乎 0，对偶清理更干净）。codex P2 指出。
        state.choicePrompt = null;
        state._launchedMiniGameSessionIds = Object.create(null);
        renderWindow();
    }

    function getStateSnapshot() {
        return {
            mounted: mounted,
            minimized: minimized,
            chatSurfaceMode: getCurrentChatSurfaceMode(),
            compactChatState: getCurrentCompactChatState(),
            viewProps: Object.assign({}, ensureViewProps()),
            messages: state.messages.map(cloneMessage),
            composerAttachments: state.composerAttachments.slice(),
            composerHidden: state.composerHidden
        };
    }

    var MINIMIZED_SIZE = 50;            // 桌面/手机：圆球直径
    var isMinimizeTransitioning = false;
    var activeAnimationCleanup = null; // 当前进行中动画的清理函数

    // ── Idle-dock: independent orchestration (Phase 4) ──────────
    // Positions the minimized ball next to CAT2/CAT3 return-ball.
    // Completely separated from setMinimized() — only reads minimized
    // state and calls setMinimized(true/false) externally when needed.

    function isIdleDockTierActive() {
        return idleDockTier === IDLE_DOCK_TIER_CAT2 || idleDockTier === IDLE_DOCK_TIER_CAT3;
    }

    function getVisibleReturnButtonContainer() {
        if (isElectronChatWindow()) return null;
        return document.querySelector('[id$="-return-button-container"][data-neko-return-visible="true"]');
    }

    function getIdleDockTarget() {
        if (!idleDockActive || !isIdleDockTierActive()) return null;
        var container = getVisibleReturnButtonContainer();
        if (!container || typeof container.getBoundingClientRect !== 'function') return null;
        var rect = container.getBoundingClientRect();
        if (!rect || rect.width <= 0 || rect.height <= 0) return null;
        var left = Math.round(rect.left - MINIMIZED_SIZE - HOME_IDLE_DOCK_GAP);
        var top = Math.round(rect.top + ((rect.height - MINIMIZED_SIZE) / 2));
        return {
            left: Math.max(0, Math.min(left, window.innerWidth - MINIMIZED_SIZE)),
            top: Math.max(0, Math.min(top, window.innerHeight - MINIMIZED_SIZE))
        };
    }

    function stopIdleDockMinimizeObserver() {
        if (idleDockMinimizeObserver) {
            try { idleDockMinimizeObserver.disconnect(); } catch (_) {}
            idleDockMinimizeObserver = null;
        }
    }

    function stopIdleDockContainerObserver() {
        if (idleDockContainerObserver) {
            try { idleDockContainerObserver.disconnect(); } catch (_) {}
            idleDockContainerObserver = null;
        }
    }

    function cancelIdleDockSync() {
        if (idleDockSyncFrame) {
            window.cancelAnimationFrame(idleDockSyncFrame);
            idleDockSyncFrame = 0;
        }
    }

    function clearIdleDockState() {
        stopIdleDockMinimizeObserver();
        stopIdleDockContainerObserver();
        cancelIdleDockSync();
        idleDockActive = false;
        idleDockSavedPosition = null;
        idleDockTriggeredMinimize = false;
    }

    function hasIdleDockPendingOrActive() {
        return !!(idleDockActive || idleDockTriggeredMinimize || idleDockMinimizeObserver);
    }

    function applyIdleDockPosition() {
        if (!minimized || isElectronChatWindow()) return;
        var shell = getShell();
        var target = getIdleDockTarget();
        if (!shell || !target) return;
        shell.style.left = target.left + 'px';
        shell.style.top = target.top + 'px';
        shell.classList.add('is-idle-docked');
    }

    function finishIdleDockMinimize(shell) {
        if (!shell || !isIdleDockTierActive() || idleDockActive) return;
        stopIdleDockMinimizeObserver();
        var rect = shell.getBoundingClientRect();
        idleDockSavedPosition = { left: rect.left, top: rect.top };
        idleDockActive = true;
        applyIdleDockPosition();
        refreshIdleDockContainerObserver();
    }

    function scheduleIdleDockMinimizeFallback(shell) {
        if (!shell) return;
        window.setTimeout(function () {
            if (!idleDockTriggeredMinimize || idleDockActive || !isIdleDockTierActive()) return;
            var latestShell = getShell();
            if (!latestShell) return;
            minimized = true;
            latestShell.classList.remove('is-collapsing', 'is-expanding');
            latestShell.style.transform = 'none';
            latestShell.style.removeProperty('width');
            latestShell.style.removeProperty('height');
            latestShell.style.removeProperty('right');
            latestShell.style.removeProperty('bottom');
            latestShell.classList.add('is-minimized');
            syncChatSurfaceModeUI();
            finishIdleDockMinimize(latestShell);
        }, 460);
    }

    function refreshIdleDockContainerObserver() {
        if (isElectronChatWindow() || !idleDockActive || !isIdleDockTierActive()) {
            stopIdleDockContainerObserver();
            return;
        }
        var container = getVisibleReturnButtonContainer();
        if (!container || typeof MutationObserver !== 'function') {
            stopIdleDockContainerObserver();
            return;
        }
        stopIdleDockContainerObserver();
        idleDockContainerObserver = new MutationObserver(function () {
            scheduleIdleDockSync();
        });
        idleDockContainerObserver.observe(container, {
            attributes: true,
            attributeFilter: ['style', 'class', 'data-dragging', 'data-neko-idle-tier', 'data-neko-return-visible']
        });
    }

    function syncIdleDockPosition() {
        idleDockSyncFrame = 0;
        if (!minimized || !idleDockActive || isElectronChatWindow()) return;
        applyIdleDockPosition();
    }

    function scheduleIdleDockSync() {
        if (!minimized || !idleDockActive || isElectronChatWindow() || idleDockSyncFrame) return;
        idleDockSyncFrame = window.requestAnimationFrame(syncIdleDockPosition);
    }

    function getElectronIdleDockBridge() {
        if (!isElectronChatWindow()) return null;
        var bridge = window.nekoChatWindow;
        if (!bridge || typeof bridge.getBounds !== 'function' || typeof bridge.setBounds !== 'function') {
            return null;
        }
        return bridge;
    }

    function normalizeElectronRect(rect) {
        if (!rect || typeof rect !== 'object') return null;
        var left = Number(rect.left);
        var top = Number(rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0) {
            return null;
        }
        return { left: left, top: top, width: width, height: height };
    }

    function normalizeElectronWindowBoundsRect(bounds) {
        if (!bounds || typeof bounds !== 'object') return null;
        var left = Number.isFinite(Number(bounds.left)) ? Number(bounds.left) : Number(bounds.x);
        var top = Number.isFinite(Number(bounds.top)) ? Number(bounds.top) : Number(bounds.y);
        var width = Number(bounds.width);
        var height = Number(bounds.height);
        if (!Number.isFinite(left) || !Number.isFinite(top) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0) {
            return null;
        }
        left = Math.round(left);
        top = Math.round(top);
        width = Math.round(width);
        height = Math.round(height);
        return {
            left: left,
            top: top,
            width: width,
            height: height,
            right: left + width,
            bottom: top + height
        };
    }

    function rememberElectronIdleDockBounds(bounds) {
        var rect = normalizeElectronWindowBoundsRect(bounds);
        if (!rect) return null;
        electronIdleDockCurrentBounds = {
            x: rect.left,
            y: rect.top,
            width: rect.width,
            height: rect.height
        };
        return electronIdleDockCurrentBounds;
    }

    function isElectronChatWindowCollapsed(bridge) {
        if (!bridge || typeof bridge.isCollapsed !== 'function') return false;
        try {
            return !!bridge.isCollapsed();
        } catch (_) {
            return false;
        }
    }

    function getElectronChatMinimizedStateSignature(minimizedState, rect) {
        if (!minimizedState || !rect) return '0';
        return [
            '1',
            rect.left,
            rect.top,
            rect.width,
            rect.height
        ].join(':');
    }

    function dispatchElectronChatMinimizedState(reason) {
        if (!isElectronChatWindow()) return;
        var bridge = window.nekoChatWindow;
        if (!bridge || typeof bridge.getBounds !== 'function') return;

        var now = Date.now();
        var collapsed = isElectronChatWindowCollapsed(bridge);
        if (!collapsed) {
            if (electronChatMinimizedStateSignature === '0' &&
                reason === 'poll' &&
                now - electronChatMinimizedStatePublishedAt < ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS) {
                return;
            }
            electronChatMinimizedStateSignature = '0';
            electronChatMinimizedStatePublishedAt = now;
            window.dispatchEvent(new CustomEvent('neko:idle-chat-minimized-state', {
                detail: {
                    action: 'idle_chat_minimized_state',
                    source: 'chat-window',
                    reason: reason || 'sync',
                    minimized: false,
                    screenRect: null,
                    timestamp: now
                }
            }));
            return;
        }

        bridge.getBounds().then(function (bounds) {
            var rect = normalizeElectronWindowBoundsRect(bounds);
            if (!rect) return;
            var now = Date.now();
            var signature = getElectronChatMinimizedStateSignature(true, rect);
            if (signature === electronChatMinimizedStateSignature &&
                reason === 'poll' &&
                now - electronChatMinimizedStatePublishedAt < ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS) {
                return;
            }
            electronChatMinimizedStateSignature = signature;
            electronChatMinimizedStatePublishedAt = now;
            window.dispatchEvent(new CustomEvent('neko:idle-chat-minimized-state', {
                detail: {
                    action: 'idle_chat_minimized_state',
                    source: 'chat-window',
                    reason: reason || 'sync',
                    minimized: true,
                    screenRect: rect,
                    timestamp: now
                }
            }));
        }).catch(function () {});
    }

    function scheduleElectronChatMinimizedState(reason) {
        if (!isElectronChatWindow() || electronChatMinimizedStateFrame) return;
        electronChatMinimizedStateFrame = window.requestAnimationFrame(function () {
            electronChatMinimizedStateFrame = 0;
            dispatchElectronChatMinimizedState(reason || 'sync');
        });
    }

    function ensureElectronChatMinimizedStateBridge() {
        if (!isElectronChatWindow() || electronChatMinimizedStateTimer) return;
        scheduleElectronChatMinimizedState('init');
        electronChatMinimizedStateTimer = window.setInterval(function () {
            scheduleElectronChatMinimizedState('poll');
        }, 500);
        window.addEventListener('resize', function () {
            scheduleElectronChatMinimizedState('resize');
        });
        window.addEventListener('mousemove', function () {
            scheduleElectronChatMinimizedState('pointer');
        }, { passive: true });
        window.addEventListener('mouseup', function () {
            scheduleElectronChatMinimizedState('pointer');
        }, { passive: true });
    }

    function clampElectronDockBounds(bounds, workArea) {
        if (!bounds) return null;
        var area = workArea && Number.isFinite(Number(workArea.x)) && Number.isFinite(Number(workArea.y))
            ? workArea
            : { x: 0, y: 0, width: window.screen && window.screen.availWidth || 0, height: window.screen && window.screen.availHeight || 0 };
        var maxX = Number(area.x) + Math.max(0, Number(area.width) - bounds.width);
        var maxY = Number(area.y) + Math.max(0, Number(area.height) - bounds.height);
        return {
            x: Math.round(Math.max(Number(area.x), Math.min(bounds.x, maxX))),
            y: Math.round(Math.max(Number(area.y), Math.min(bounds.y, maxY))),
            width: Math.round(bounds.width),
            height: Math.round(bounds.height)
        };
    }

    function electronRectToBounds(rect) {
        if (!rect || typeof rect !== 'object') return null;
        var normalized = normalizeElectronRect({
            left: Number.isFinite(Number(rect.left)) ? rect.left : rect.x,
            top: Number.isFinite(Number(rect.top)) ? rect.top : rect.y,
            width: rect.width,
            height: rect.height
        });
        if (!normalized) return null;
        return {
            x: Math.round(normalized.left),
            y: Math.round(normalized.top),
            width: Math.round(normalized.width),
            height: Math.round(normalized.height)
        };
    }

    async function applyElectronCat1PairMoveBounds(bounds) {
        var targetBounds = electronRectToBounds(bounds);
        if (!targetBounds) return;
        var bridge = getElectronIdleDockBridge();
        if (!bridge || !isElectronChatWindowCollapsed(bridge)) return;
        if (hasElectronIdleDockPendingOrActive()) return;
        try {
            if (typeof bridge.idleDockCommitCollapsedBounds === 'function') {
                await bridge.idleDockCommitCollapsedBounds(targetBounds);
            } else {
                bridge.setBounds(targetBounds.x, targetBounds.y, targetBounds.width, targetBounds.height);
            }
            scheduleElectronChatMinimizedState('cat1-pair-move');
        } catch (_) {
            // A transient desktop move failure should not break the CAT1 animation loop.
        }
    }

    function scheduleElectronCat1PairMoveBounds(bounds) {
        if (!isElectronChatWindow()) return;
        electronCat1PairMovePendingBounds = electronRectToBounds(bounds);
        if (!electronCat1PairMovePendingBounds || electronCat1PairMoveBoundsFrame) return;
        electronCat1PairMoveBoundsFrame = window.requestAnimationFrame(function () {
            var pendingBounds = electronCat1PairMovePendingBounds;
            electronCat1PairMovePendingBounds = null;
            electronCat1PairMoveBoundsFrame = 0;
            applyElectronCat1PairMoveBounds(pendingBounds);
        });
    }

    function isElectronIdleDockCurrent(generation) {
        return electronIdleDockDesired && generation === electronIdleDockGeneration;
    }

    function clearElectronIdleDockPositionFrame() {
        if (electronIdleDockPositionFrame) {
            window.cancelAnimationFrame(electronIdleDockPositionFrame);
            electronIdleDockPositionFrame = 0;
        }
    }

    function setElectronIdleDockTargetRect(targetRect) {
        electronIdleDockLastScreenRect = targetRect;
        electronIdleDockPositionSeq += 1;
    }

    function scheduleElectronIdleDockPosition() {
        if (!electronIdleDockActive || !electronIdleDockDesired || electronIdleDockPositionFrame) return;
        electronIdleDockPositionFrame = window.requestAnimationFrame(function () {
            electronIdleDockPositionFrame = 0;
            applyElectronIdleDockPosition();
        });
    }

    async function applyElectronIdleDockPosition() {
        var bridge = getElectronIdleDockBridge();
        var targetRect = normalizeElectronRect(electronIdleDockLastScreenRect);
        var positionSeq = electronIdleDockPositionSeq;
        if (!bridge || !targetRect || !electronIdleDockActive || !electronIdleDockDesired) return;

        var bounds = electronIdleDockCurrentBounds;
        if (!bounds) {
            try {
                bounds = rememberElectronIdleDockBounds(await bridge.getBounds());
            } catch (_) {
                bounds = null;
            }
            if (positionSeq !== electronIdleDockPositionSeq || !electronIdleDockActive || !electronIdleDockDesired) return;
        }
        if (!bounds || !Number.isFinite(Number(bounds.width)) || !Number.isFinite(Number(bounds.height))) {
            return;
        }

        var width = Math.max(1, Math.round(Number(bounds.width)));
        var height = Math.max(1, Math.round(Number(bounds.height)));
        var nextBounds = {
            x: Math.round(targetRect.left - width - HOME_IDLE_DOCK_GAP),
            y: Math.round(targetRect.top + (targetRect.height - height) / 2),
            width: width,
            height: height
        };

        if (!electronIdleDockWorkArea && typeof bridge.getWorkArea === 'function') {
            try {
                electronIdleDockWorkArea = await bridge.getWorkArea();
            } catch (_) {
                electronIdleDockWorkArea = null;
            }
            if (positionSeq !== electronIdleDockPositionSeq || !electronIdleDockActive || !electronIdleDockDesired) return;
        }
        nextBounds = clampElectronDockBounds(nextBounds, electronIdleDockWorkArea);
        if (positionSeq !== electronIdleDockPositionSeq || !electronIdleDockActive || !electronIdleDockDesired) return;

        bridge.setBounds(nextBounds.x, nextBounds.y, nextBounds.width, nextBounds.height);
        rememberElectronIdleDockBounds(nextBounds);
    }

    function clearElectronIdleDockRetry() {
        if (electronIdleDockRetryTimer) {
            window.clearTimeout(electronIdleDockRetryTimer);
            electronIdleDockRetryTimer = 0;
        }
    }

    function scheduleElectronIdleDockRetry(generation) {
        if (electronIdleDockRetryTimer || !electronIdleDockLastScreenRect || !isElectronIdleDockCurrent(generation)) return;
        electronIdleDockRetryTimer = window.setTimeout(function () {
            electronIdleDockRetryTimer = 0;
            if (electronIdleDockLastScreenRect && !electronIdleDockActive && isElectronIdleDockCurrent(generation)) {
                enterElectronIdleDock(electronIdleDockLastScreenRect);
            }
        }, 120);
    }

    function hasElectronIdleDockPendingOrActive() {
        return electronIdleDockActive || electronIdleDockEntering || electronIdleDockDesired || electronIdleDockRetryTimer;
    }

    function shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier) {
        return !!(detail && detail.reason === 'viewport-resize' && !activeTier);
    }

    function waitElectronIdleDockCommitRetry(delayMs) {
        return new Promise(function (resolve) {
            setTimeout(resolve, Math.max(0, delayMs || 0));
        });
    }

    async function commitElectronIdleDockCollapsedBounds(bridge, bounds, generation) {
        if (!bridge || !bounds) return false;
        if (typeof bridge.idleDockCommitCollapsedBounds === 'function') {
            for (var attempt = 0; attempt < 4; attempt += 1) {
                var result = null;
                try {
                    result = await bridge.idleDockCommitCollapsedBounds(bounds);
                } catch (_) {
                    result = null;
                }
                if (generation !== electronIdleDockGeneration || electronIdleDockDesired) return false;
                if (result !== false && result !== null && result !== undefined) {
                    rememberElectronIdleDockBounds(result);
                    return true;
                }
                if (attempt >= 3) break;
                await waitElectronIdleDockCommitRetry(80);
                if (generation !== electronIdleDockGeneration || electronIdleDockDesired) return false;
            }
        }
        if (typeof bridge.setBounds === 'function') {
            bridge.setBounds(bounds.x, bounds.y, bounds.width, bounds.height);
            rememberElectronIdleDockBounds(bounds);
            return true;
        }
        return false;
    }

    async function enterElectronIdleDock(screenRect) {
        var bridge = getElectronIdleDockBridge();
        var targetRect = normalizeElectronRect(screenRect);
        if (!bridge || !targetRect) return;
        if (!electronIdleDockDesired) {
            electronIdleDockDesired = true;
            electronIdleDockGeneration += 1;
        }
        var generation = electronIdleDockGeneration;
        setElectronIdleDockTargetRect(targetRect);

        if (electronIdleDockActive) {
            scheduleElectronIdleDockPosition();
            return;
        }

        if (electronIdleDockEntering) {
            return;
        }

        electronIdleDockEntering = true;
        try {
            electronIdleDockSavedBounds = await bridge.getBounds();
        } catch (_) {
            electronIdleDockSavedBounds = null;
        }
        var entrySavedBounds = electronIdleDockSavedBounds;
        if (!isElectronIdleDockCurrent(generation)) {
            electronIdleDockEntering = false;
            return;
        }

        var alreadyCollapsed = false;
        try {
            alreadyCollapsed = typeof bridge.isCollapsed === 'function' && bridge.isCollapsed();
        } catch (_) {
            alreadyCollapsed = false;
        }
        var shouldCollapseForIdleDock = !alreadyCollapsed;
        if (alreadyCollapsed) {
            rememberElectronIdleDockBounds(entrySavedBounds);
        }
        if (!alreadyCollapsed && typeof bridge.idleDockCollapse !== 'function') {
            electronIdleDockEntering = false;
            scheduleElectronIdleDockRetry(generation);
            return;
        }

        if (shouldCollapseForIdleDock) {
            var collapsedResult = null;
            try {
                collapsedResult = await bridge.idleDockCollapse();
            } catch (_) {
                collapsedResult = null;
            }
            try {
                alreadyCollapsed = typeof bridge.isCollapsed === 'function' && bridge.isCollapsed();
            } catch (_) {
                alreadyCollapsed = false;
            }
            if (!isElectronIdleDockCurrent(generation)) {
                try {
                    if (collapsedResult && alreadyCollapsed && entrySavedBounds && typeof bridge.idleDockExpand === 'function') {
                        await bridge.idleDockExpand(entrySavedBounds);
                    }
                } catch (_) {
                    // Best effort rollback; the newer generation owns the next visible state.
                }
                electronIdleDockEntering = false;
                return;
            }
            if (!collapsedResult || !alreadyCollapsed) {
                electronIdleDockEntering = false;
                scheduleElectronIdleDockRetry(generation);
                return;
            }
            rememberElectronIdleDockBounds(collapsedResult);
        }

        if (!isElectronIdleDockCurrent(generation)) {
            electronIdleDockEntering = false;
            return;
        }
        if (!electronIdleDockWorkArea && typeof bridge.getWorkArea === 'function') {
            try {
                electronIdleDockWorkArea = await bridge.getWorkArea();
            } catch (_) {
                electronIdleDockWorkArea = null;
            }
        }
        if (!isElectronIdleDockCurrent(generation)) {
            electronIdleDockEntering = false;
            return;
        }
        electronIdleDockTriggeredCollapse = shouldCollapseForIdleDock;
        electronIdleDockActive = true;
        electronIdleDockEntering = false;
        clearElectronIdleDockRetry();
        scheduleElectronIdleDockPosition();
        scheduleElectronChatMinimizedState('idle-dock-enter');
    }

    async function exitElectronIdleDock(options) {
        var preserveCurrentPosition = !!(options && options.preserveCurrentPosition);
        var preserveScreenRect = normalizeElectronRect(options && options.preserveScreenRect);
        var bridge = getElectronIdleDockBridge();
        var wasActive = electronIdleDockActive;
        var triggeredCollapse = electronIdleDockTriggeredCollapse;
        var savedBounds = electronIdleDockSavedBounds;
        var currentBounds = electronIdleDockCurrentBounds;
        var workArea = electronIdleDockWorkArea;

        electronIdleDockDesired = false;
        electronIdleDockGeneration += 1;
        var exitGeneration = electronIdleDockGeneration;
        electronIdleDockActive = false;
        electronIdleDockTriggeredCollapse = false;
        electronIdleDockSavedBounds = null;
        electronIdleDockLastScreenRect = null;
        electronIdleDockEntering = false;
        electronIdleDockCurrentBounds = null;
        electronIdleDockWorkArea = null;
        clearElectronIdleDockRetry();
        clearElectronIdleDockPositionFrame();
        electronIdleDockPositionSeq += 1;

        if (!bridge || !wasActive) return;

        if (preserveCurrentPosition) {
            var preserveBounds = null;
            if (preserveScreenRect) {
                var basisBounds = currentBounds || savedBounds;
                if (!basisBounds && typeof bridge.getBounds === 'function') {
                    try {
                        basisBounds = await bridge.getBounds();
                    } catch (_) {
                        basisBounds = null;
                    }
                }
                if (exitGeneration !== electronIdleDockGeneration || electronIdleDockDesired) return;
                if (basisBounds &&
                    Number.isFinite(Number(basisBounds.width)) &&
                    Number.isFinite(Number(basisBounds.height))) {
                    preserveBounds = {
                        x: Math.round(preserveScreenRect.left - Math.max(1, Math.round(Number(basisBounds.width))) - HOME_IDLE_DOCK_GAP),
                        y: Math.round(preserveScreenRect.top + (preserveScreenRect.height - Math.max(1, Math.round(Number(basisBounds.height)))) / 2),
                        width: Math.max(1, Math.round(Number(basisBounds.width))),
                        height: Math.max(1, Math.round(Number(basisBounds.height)))
                    };
                    if (!workArea && typeof bridge.getWorkArea === 'function') {
                        try {
                            workArea = await bridge.getWorkArea();
                        } catch (_) {
                            workArea = null;
                        }
                    }
                    if (exitGeneration !== electronIdleDockGeneration || electronIdleDockDesired) return;
                    preserveBounds = clampElectronDockBounds(preserveBounds, workArea);
                }
            }
            await commitElectronIdleDockCollapsedBounds(bridge, preserveBounds, exitGeneration);
            if (exitGeneration !== electronIdleDockGeneration || electronIdleDockDesired) return;
            scheduleElectronChatMinimizedState('idle-dock-exit-preserve');
            return;
        }

        if (!savedBounds) return;

        if (triggeredCollapse && typeof bridge.idleDockExpand === 'function') {
            try {
                await bridge.idleDockExpand(savedBounds);
                scheduleElectronChatMinimizedState('idle-dock-exit');
                return;
            } catch (_) {}
        }
        bridge.setBounds(savedBounds.x, savedBounds.y, savedBounds.width, savedBounds.height);
        scheduleElectronChatMinimizedState('idle-dock-exit');
    }

    function handleElectronIdleReturnBallState(detail) {
        if (!isElectronChatWindow()) return;
        var tier = detail && detail.tier;
        var activeTier = tier === IDLE_DOCK_TIER_CAT2 || tier === IDLE_DOCK_TIER_CAT3;
        if (detail && detail.visible && activeTier && detail.screenRect) {
            enterElectronIdleDock(detail.screenRect);
            return;
        }
        if (hasElectronIdleDockPendingOrActive()) {
            if (shouldIgnoreElectronIdleDockInactiveViewportResize(detail, activeTier)) {
                return;
            }
            var shouldPreserveCurrentPosition = detail && (
                detail.reason === 'return-ball-drag-demotion'
                || detail.reason === 'return-ball-drag-end'
            );
            exitElectronIdleDock({
                preserveCurrentPosition: shouldPreserveCurrentPosition,
                preserveScreenRect: shouldPreserveCurrentPosition ? detail.screenRect : null,
            });
        }
    }

    // Enter idle-dock: minimize if needed, then position next to return-ball.
    // Enters through chatSurfaceMode so compact/full/minimized state stays in sync.
    function enterIdleDock() {
        if (isElectronChatWindow()) return;

        if (minimized) {
            // Already minimized — save current position and dock immediately.
            var shell = getShell();
            if (shell) {
                var rect = shell.getBoundingClientRect();
                idleDockSavedPosition = { left: rect.left, top: rect.top };
            }
            idleDockActive = true;
            idleDockTriggeredMinimize = false;
            applyIdleDockPosition();
            refreshIdleDockContainerObserver();
        } else {
            // Not minimized — trigger normal minimize, observe for completion.
            idleDockTriggeredMinimize = true;
            stopIdleDockMinimizeObserver();
            var shell = getShell();
            if (!shell) return;

            idleDockMinimizeObserver = new MutationObserver(function () {
                if (shell.classList.contains('is-minimized') && !shell.classList.contains('is-collapsing')) {
                    finishIdleDockMinimize(shell);
                }
            });
            idleDockMinimizeObserver.observe(shell, { attributes: true, attributeFilter: ['class'] });

            setChatSurfaceMode('minimized');
            scheduleIdleDockMinimizeFallback(shell);
        }
    }

    // Exit idle-dock: restore position and un-minimize if idle-dock triggered it.
    function exitIdleDock(options) {
        var preserveCurrentPosition = !!(options && options.preserveCurrentPosition);
        var wasActive = idleDockActive;
        var triggered = idleDockTriggeredMinimize;
        var wasTransitioning = isMinimizeTransitioning;
        var saved = idleDockSavedPosition;
        var shell = getShell();

        clearIdleDockState();

        if (shell) {
            shell.classList.remove('is-idle-docked');
            if (wasActive && saved && !preserveCurrentPosition) {
                shell.style.left = saved.left + 'px';
                shell.style.top = saved.top + 'px';
            }
        }

        if (triggered && !wasActive && wasTransitioning) {
            cancelActiveAnimation();
            minimized = false;
            if (shell) {
                shell.classList.remove('is-minimized', 'is-collapsing', 'is-idle-docked');
                shell.style.transform = 'none';
                if (savedShellSize) {
                    if (savedShellSize.width) shell.style.width = savedShellSize.width;
                    if (savedShellSize.height) shell.style.height = savedShellSize.height;
                }
                if (savedShellPosition) {
                    shell.style.left = savedShellPosition.left + 'px';
                    shell.style.top = savedShellPosition.top + 'px';
                }
            }
            savedShellSize = null;
            savedShellPosition = null;
            state.chatSurfaceMode = normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);
            renderWindow();
            syncMinimizeUI();
            syncChatSurfaceModeUI();
            return;
        }

        if (wasActive && triggered && minimized && preserveCurrentPosition) {
            syncChatSurfaceModeUI();
            return;
        }

        if (wasActive && triggered && minimized) {
            setChatSurfaceMode(normalizeChatSurfaceMode(lastRestorableChatSurfaceMode));
        }
    }

    // ── End idle-dock ────────────────────────────────────────────

    // 返回最小化后 shell 应达到的像素几何。
    // 桌面：50x50 圆球，锚定在对话框原左下角（clamp 到视口内）。
    // 手机：全宽底部胶囊，贴屏幕底边（类似移动 App 的底栏收起态）。
    // 由于 collapse/expand 动画的 transform-origin = 0% 100%（左下角），
    // target.left 应等于 rect.left 同列，target 底边应与 rect 底边对齐
    // （即 target.top = rect.bottom - target.height），这样动画过程中底边不漂移。
    function getMinimizedTarget(rect) {
        var compactBallTarget = getCompactMinimizeBallTarget();
        if (compactBallTarget) {
            return compactBallTarget;
        }

        // 桌面端和移动端统一使用 50px 圆形悬浮球
        return {
            width: MINIMIZED_SIZE,
            height: MINIMIZED_SIZE,
            left: Math.max(0, Math.min(rect.left, window.innerWidth - MINIMIZED_SIZE)),
            top: Math.max(0, Math.min(rect.bottom - MINIMIZED_SIZE, window.innerHeight - MINIMIZED_SIZE))
        };
    }

    function getExpandedTargetFromSavedState() {
        var shell = getShell();
        if (!shell) return null;
        if (isMobileWidth()) return null;

        var width = savedShellSize ? parseFloat(savedShellSize.width) : NaN;
        var height = savedShellSize ? parseFloat(savedShellSize.height) : NaN;
        if (!Number.isFinite(width) || width <= 0 || !Number.isFinite(height) || height <= 0) {
            var rect = shell.getBoundingClientRect();
            width = rect.width;
            height = rect.height;
        }

        if (getCurrentChatSurfaceMode() === 'compact') {
            var compactTarget = getCompactSurfaceTarget();
            if (compactTarget) {
                return {
                    width: width,
                    height: height,
                    left: compactTarget.left,
                    top: compactTarget.top
                };
            }
        }

        var expandedTargetPosition = savedExpandedShellPosition
            || getStoredPosition()
            || (savedShellPosition
                ? {
                    left: savedShellPosition.left,
                    top: savedShellPosition.top
                }
                : null);

        return {
            width: width,
            height: height,
            left: expandedTargetPosition ? expandedTargetPosition.left : 0,
            top: expandedTargetPosition ? expandedTargetPosition.top : 0
        };
    }

    function cancelActiveAnimation() {
        if (activeAnimationCleanup) {
            activeAnimationCleanup();
            activeAnimationCleanup = null;
        }
        isMinimizeTransitioning = false;
    }

    function ensureMinimizedBallIcon() {
        if (isElectronChatWindow()) return null;
        var shell = getShell();
        if (!shell) return null;
        var icon = shell.querySelector('.react-chat-minimized-icon');
        if (!icon) {
            icon = document.createElement('img');
            icon.className = 'react-chat-minimized-icon';
            icon.src = '/static/icons/expand_icon_off_ball.png';
            icon.alt = '';
            icon.draggable = false;
            var handle = getHeader();
            if (handle) {
                handle.appendChild(icon);
            } else {
                shell.appendChild(icon);
            }
        }
        return icon;
    }

    function setCompactChatState(nextCompactChatState) {
        var normalized = normalizeCompactChatState(nextCompactChatState);
        if (state.compactChatState === normalized) {
            return normalized;
        }
        state.compactChatState = normalized;
        renderWindow();
        syncChatSurfaceModeUI();
        dispatchHostEvent('compact-chat-state-change', {
            state: normalized
        });
        return normalized;
    }

    function setChatSurfaceMode(nextMode) {
        var normalized = normalizeChatSurfaceMode(nextMode);
        var previousMode = getCurrentChatSurfaceMode();
        var nextMinimized = normalized === 'minimized';
        var previousMinimized = previousMode === 'minimized';
        if (previousMode === normalized) {
            syncChatSurfaceModeUI();
            return normalized;
        }

        if (!nextMinimized) {
            lastRestorableChatSurfaceMode = normalized;
        } else if (!previousMinimized) {
            lastRestorableChatSurfaceMode = previousMode;
        }

        resetCompactChatState();
        state.chatSurfaceMode = normalized;
        persistChatSurfaceModePreference(normalized);
        renderWindow();

        if (nextMinimized !== previousMinimized) {
            setMinimized(nextMinimized);
        } else {
            syncChatSurfaceModeUI();
        }

        dispatchHostEvent('chat-surface-mode-change', {
            mode: normalized,
            previousMode: previousMode
        });
        return normalized;
    }

    function cycleChatSurfaceMode() {
        return setChatSurfaceMode(getNextChatSurfaceMode(getCurrentChatSurfaceMode()));
    }

    function setMinimized(nextMinimized) {
        var shell = getShell();
        if (!shell) return;

        var wasMinimized = minimized;
        var willMinimize = !!nextMinimized;
        if (wasMinimized === willMinimize) return;
        if (isMinimizeTransitioning) return; // 防止动画期间重复触发
        isMinimizeTransitioning = true;

        minimized = willMinimize;

        if (isElectronChatWindow()) {
            shell.classList.remove('is-collapsing', 'is-expanding', 'is-minimized');
            shell.style.removeProperty('transform');
            shell.style.removeProperty('transform-origin');
            shell.style.removeProperty('opacity');
            isMinimizeTransitioning = false;
            syncChatSurfaceModeUI();
            return;
        }

        if (willMinimize) {
            // ---- 折叠动画：向对话框左下角缩放 ----
            var rect = shell.getBoundingClientRect();

            // 1. 保存当前位置和尺寸，展开时用
            //    如果没有内联宽高（如 chat.html 全屏模式），
            //    使用计算后的像素值，确保展开时能正确恢复
            savedShellSize = {
                width: shell.style.width || (rect.width + 'px'),
                height: shell.style.height || (rect.height + 'px')
            };
            savedShellPosition = {
                left: rect.left,
                top: rect.top
            };

            // 1b. 锁定当前像素几何到内联样式，防止切类后尺寸跳变
            //     （chat.html 全屏规则退出后 shell 会回落到默认尺寸）
            shell.style.width = rect.width + 'px';
            shell.style.height = rect.height + 'px';
            shell.style.left = rect.left + 'px';
            shell.style.top = rect.top + 'px';

            // 2. 最小化后的目标几何：桌面=50px 圆球 / 手机=全宽底部胶囊
            var target = getMinimizedTarget(rect);
            var targetLeft = target.left;
            var targetTop = target.top;

            // 3. 计算缩放比（transform-origin 为 0% 100% 即左下角，无需 translate）
            var sx = rect.width > 0 ? target.width / rect.width : 1;
            var sy = rect.height > 0 ? target.height / rect.height : 1;

            // 4. 初始 transform = identity，添加过渡类
            shell.style.transform = 'scale(1, 1)';
            shell.classList.add('is-collapsing');
            void shell.offsetHeight; // 强制 reflow

            // 5. 设置目标 transform，触发动画
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    shell.style.left = targetLeft + 'px';
                    shell.style.top = targetTop + 'px';
                    shell.style.transform = 'scale(' + sx + ', ' + sy + ')';
                });
            });

            // 6. 过渡结束后切换到最终的 minimized 状态
            var handled = false;
            var collapseTimer = null;
            var finishCollapse = function () {
                if (handled) return;
                handled = true;
                clearTimeout(collapseTimer);
                shell.removeEventListener('transitionend', onEnd);
                activeAnimationCleanup = null;
                shell.classList.remove('is-collapsing');
                shell.style.transform = 'none';
                // 清除内联尺寸，让 .is-minimized 的 CSS 生效
                shell.style.removeProperty('width');
                shell.style.removeProperty('height');
                shell.classList.remove('is-mobile-content-capped');
                shell.style.removeProperty('right');
                shell.style.removeProperty('bottom');
                // 将位置设为对话框左下角
                shell.style.left = targetLeft + 'px';
                shell.style.top = targetTop + 'px';
                shell.classList.add('is-minimized');
                isMinimizeTransitioning = false;
            };
            var onEnd = function (e) {
                if (e.target !== shell || e.propertyName !== 'transform') return;
                finishCollapse();
            };
            shell.addEventListener('transitionend', onEnd);
            collapseTimer = setTimeout(finishCollapse, 420); // 兜底

            // 注册清理句柄，供 closeWindow / 下次动画调用
            activeAnimationCleanup = function () {
                clearTimeout(collapseTimer);
                shell.removeEventListener('transitionend', onEnd);
                shell.classList.remove('is-collapsing');
                shell.style.transform = 'none';
                handled = true;
            };

        } else {
            // ---- 展开动画：从最小化态（桌面圆球 / 手机底部胶囊）展开 ----
            var curRect = shell.getBoundingClientRect();
            var ballLeft = curRect.left;
            var collapsedTop = curRect.top;
            // 桌面圆球的 height≈50，手机胶囊的 height≈48；curRect 直接反映真实值
            var ballBottom = curRect.top + (curRect.height || MINIMIZED_SIZE);

            // 恢复保存的尺寸
            shell.classList.remove('is-minimized');
            shell.style.removeProperty('right');
            shell.style.removeProperty('bottom');
            if (isMobileWidth()) {
                // 手机端：宽度由 CSS calc(100vw - 12px) 控制，清除内联宽度
                shell.style.removeProperty('width');
                // 高度：优先使用用户手动设置的高度，否则自动计算上限 85vh
                var mobileMaxH = getMobileMaxHeight();
                var savedHeightPx = savedShellSize ? parseFloat(savedShellSize.height) : NaN;
                var restoreHeight;
                if (mobileUserHeight > 0) {
                    restoreHeight = Math.min(mobileUserHeight, mobileMaxH);
                } else if (isFinite(savedHeightPx) && savedHeightPx > 0) {
                    restoreHeight = Math.min(savedHeightPx, mobileMaxH);
                } else {
                    restoreHeight = mobileMaxH;
                }
                if (restoreHeight > 0) shell.style.height = restoreHeight + 'px';
            } else if (savedShellSize) {
                if (savedShellSize.width) shell.style.width = savedShellSize.width;
                if (savedShellSize.height) shell.style.height = savedShellSize.height;
            }

            // 以球的位置为展开后对话框的左下角来计算展开位置
            // 先设临时位置以获取真实尺寸
            shell.style.left = '0px';
            shell.style.top = '0px';
            var expandedTarget = getExpandedTargetFromSavedState();
            if (expandedTarget) {
                shell.style.left = expandedTarget.left + 'px';
                shell.style.top = expandedTarget.top + 'px';
            }
            shell.style.transform = 'none';
            void shell.offsetHeight;
            var expandedRect = shell.getBoundingClientRect();

            // 尺寸无效时（overlay 仍隐藏等边界情况）跳过动画，直接恢复
            if (!expandedRect.width || !expandedRect.height) {
                shell.style.transform = 'none';
                // 尝试恢复到保存的位置
                if (savedShellPosition) {
                    shell.style.left = savedShellPosition.left + 'px';
                    shell.style.top = savedShellPosition.top + 'px';
                } else if (!isMobileWidth()) {
                    restorePosition();
                }
                savedShellSize = null;
                savedShellPosition = null;
                isMinimizeTransitioning = false;
                requestAnimationFrame(function () {
                    var r = shell.getBoundingClientRect();
                    var clamped = clampPosition(r.left, r.top);
                    if (clamped.left !== r.left || clamped.top !== r.top) {
                        applyPosition(clamped.left, clamped.top);
                    }
                });
            } else {

            // 球的左下角 = 展开后对话框的左下角
            var expandedLeft = ballLeft;
            var expandedTop = ballBottom - expandedRect.height;
            if (expandedTarget) {
                expandedLeft = expandedTarget.left;
                expandedTop = expandedTarget.top;
            }

            // 先不 clamp，让动画从球位置自然展开，动画结束后再 clamp
            shell.style.left = ballLeft + 'px';
            shell.style.top = collapsedTop + 'px';
            shell.style.transform = 'none';
            void shell.offsetHeight;

            // 重新获取展开后的真实 rect（位置可能已改变）
            expandedRect = shell.getBoundingClientRect();

            // 计算初始缩放：transform-origin 为左下角 (0% 100%)
            // 从当前最小化态的真实尺寸缩回（桌面 50x50 / 手机 full-width x 48），
            // 视觉上的左下角保持不变。
            var sx2 = curRect.width > 0 ? curRect.width / expandedRect.width : 1;
            var sy2 = curRect.height > 0 ? curRect.height / expandedRect.height : 1;

            // 设置初始 transform（看起来还是左下角的小圆）
            shell.style.transform = 'scale(' + sx2 + ', ' + sy2 + ')';
            shell.classList.add('is-expanding');
            void shell.offsetHeight; // 强制 reflow

            // 动画到 identity（展开到完整尺寸）
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    shell.style.left = expandedLeft + 'px';
                    shell.style.top = expandedTop + 'px';
                    shell.style.transform = 'scale(1, 1)';
                });
            });

            // 动画结束后清理
            var expandHandled = false;
            var expandTimer = null;
            var finishExpand = function () {
                if (expandHandled) return;
                expandHandled = true;
                clearTimeout(expandTimer);
                shell.removeEventListener('transitionend', onExpandEnd);
                activeAnimationCleanup = null;
                shell.classList.remove('is-expanding');
                shell.style.transform = 'none';
                var surfaceModeAfterExpand = getCurrentChatSurfaceMode();
                savedShellSize = null;
                savedShellPosition = null;
                isMinimizeTransitioning = false;
                scheduleMobileContentLayout();
                // 确保位置不溢出；全屏模式（/chat）不持久化，
                // 否则 (0,0) 会覆盖 index.html 中用户保存的窗口位置
                requestAnimationFrame(function () {
                    if (isMobileWidth()) {
                        restorePosition();
                        return;
                    }
                    if (surfaceModeAfterExpand === 'minimized') {
                        return;
                    }
                    syncCompactSurfaceAnchor();
                });
            };
            var onExpandEnd = function (e) {
                if (e.target !== shell || e.propertyName !== 'transform') return;
                finishExpand();
            };
            shell.addEventListener('transitionend', onExpandEnd);
            expandTimer = setTimeout(finishExpand, 420); // 兜底

            // 注册清理句柄
            activeAnimationCleanup = function () {
                clearTimeout(expandTimer);
                shell.removeEventListener('transitionend', onExpandEnd);
                shell.classList.remove('is-expanding');
                shell.style.transform = 'none';
                expandHandled = true;
            };

            } // end of else (valid dimensions)
        }

        // 更新按钮图标和 aria
        syncChatSurfaceModeUI();
    }

    function syncMinimizeUI() {
        var button = getMinimizeButton();
        var btnIcon = getMinimizeIcon();
        var ballIcon = ensureMinimizedBallIcon();
        if (button) {
            button.setAttribute('aria-label', minimized ? getI18nText('chat.reactWindowRestore', '恢复聊天框') : getI18nText('chat.reactWindowMinimize', '最小化聊天框'));
            button.title = minimized ? getI18nText('chat.reactWindowRestoreShort', '恢复') : getI18nText('chat.reactWindowMinimizeShort', '最小化');
        }
        if (btnIcon) {
            btnIcon.src = minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            btnIcon.alt = minimized ? getI18nText('chat.reactWindowRestore', '恢复聊天框') : getI18nText('chat.reactWindowMinimize', '最小化聊天框');
        }
        // 重置悬浮球图标到默认态（清除可能残留的 hover 图标）
        if (ballIcon) {
            ballIcon.src = '/static/icons/expand_icon_off_ball.png';
        }
    }

    function syncChatSurfaceModeUI() {
        var shell = getShell();
        var button = getMinimizeButton();
        var btnIcon = getMinimizeIcon();
        var ballIcon = ensureMinimizedBallIcon();
        var surfaceMode = getCurrentChatSurfaceMode();
        var ariaLabel = surfaceMode === 'compact'
            ? getI18nText('chat.reactWindowMinimize', '最小化聊天框')
            : surfaceMode === 'minimized'
                ? getI18nText('chat.reactWindowRestore', '恢复聊天框')
                : getI18nText('chat.reactWindowCompact', '切换到紧凑聊天框');
        var shortLabel = surfaceMode === 'compact'
            ? getI18nText('chat.reactWindowMinimizeShort', '最小化')
            : surfaceMode === 'minimized'
                ? getI18nText('chat.reactWindowRestoreShort', '恢复')
                : getI18nText('chat.reactWindowCompactShort', '紧凑');
        if (button) {
            button.setAttribute('aria-label', ariaLabel);
            button.title = shortLabel;
        }
        if (btnIcon) {
            btnIcon.src = minimized ? '/static/icons/expand_icon_on.png' : '/static/icons/expand_icon_off.png';
            btnIcon.alt = ariaLabel;
        }
        if (ballIcon) {
            ballIcon.src = '/static/icons/expand_icon_off_ball.png';
        }
        if (shell) {
            shell.setAttribute('data-chat-surface-mode', surfaceMode);
            shell.setAttribute('data-compact-chat-state', getCurrentCompactChatState());
        }
        scheduleCompactMinimizeBallTracking();
    }

    function toggleMinimized() {
        if (minimized && idleDockActive && idleDockSavedPosition) {
            var shell = getShell();
            if (shell) {
                shell.style.left = idleDockSavedPosition.left + 'px';
                shell.style.top = idleDockSavedPosition.top + 'px';
                shell.classList.remove('is-idle-docked');
            }
            idleDockActive = false;
            idleDockSavedPosition = null;
            idleDockTier = IDLE_DOCK_TIER_NONE;
            stopIdleDockMinimizeObserver();
            stopIdleDockContainerObserver();
            cancelIdleDockSync();
        }
        cycleChatSurfaceMode();
    }

    function prewarmUserDisplayName() {
        if (!window.appChat || typeof window.appChat.ensureUserDisplayName !== 'function') return;
        Promise.resolve(window.appChat.ensureUserDisplayName()).catch(function (error) {
            console.warn('[ReactChatWindow] preload user display name failed:', error);
        });
    }

    function isMainUIHiddenByModelManager() {
        try {
            if (typeof window.isMainUIHiddenByModelManager === 'function') {
                return window.isMainUIHiddenByModelManager();
            }
        } catch (_) {}
        return !!(document.body && document.body.classList.contains('neko-main-ui-hidden-by-model-manager'));
    }

    var pendingOpenAfterModelManagerHidden = false;

    function openWindow() {
        if (!isElectronChatWindow() && isMainUIHiddenByModelManager()) {
            pendingOpenAfterModelManagerHidden = true;
            return;
        }
        pendingOpenAfterModelManagerHidden = false;

        var overlay = getOverlay();
        if (!overlay) return;

        prewarmUserDisplayName();
        ensureBundleLoaded()
            .then(function () {
                if (!isElectronChatWindow() && isMainUIHiddenByModelManager()) {
                    pendingOpenAfterModelManagerHidden = true;
                    return;
                }
                // closeWindow 已经会重置 minimized，所以到这里通常 minimized=false
                // 但如果外部直接调用 openWindow（未经 closeWindow），仍需处理
                var wasMinimized = minimized;
                if (wasMinimized) {
                    // Opening a minimized window restores the last real surface.
                    // Reset the logical surface BEFORE mountWindow() so React
                    // rebuilds the compact body instead of the (blank) minimized
                    // surface; closeWindow performs the same reset when it clears
                    // the minimized shell.
                    state.chatSurfaceMode = normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);
                    resetCompactChatState();
                }
                if (!mountWindow()) {
                    showToast(getI18nText('chat.reactWindowMountFailed', '聊天框挂载失败'), 3000);
                    return;
                }
                if (wasMinimized) {
                    // overlay 可能还隐藏，先显示再做展开动画
                    overlay.hidden = false;
                    document.body.classList.add('react-chat-window-open');
                    setMinimized(false);
                    scheduleMobileContentLayout();
                } else {
                    if (shouldDelayCompactSurfaceOpenForModel()) {
                        compactSurfacePendingModelOpen = true;
                        overlay.hidden = true;
                        document.body.classList.remove('react-chat-window-open');
                        return;
                    }
                    overlay.hidden = false;
                    document.body.classList.add('react-chat-window-open');
                    if (getCurrentChatSurfaceMode() === 'compact') {
                        syncCompactSurfaceAnchor();
                        scheduleCompactMinimizeBallTracking();
                    }
                    scheduleMobileContentLayout();
                }
                // closeWindow / hidden-state turn-end both invalidate the
                // GalGame option list, so reopening must re-fetch for the
                // latest assistant turn or the user would see a permanently
                // empty panel until the next reply arrives.
                // Wait for app-chat-adapter's realistic queue to drain before
                // building the request — same race the turn-end handler
                // protects against, just with a shorter cap because by the
                // time the user reopens the window the queue has usually
                // already finished.
                if (state.galgameModeEnabled) {
                    var seqAtOpen = state._galgameRequestSeq;
                    waitForAssistantBubblesFlushed(2000).then(function () {
                        if (!state.galgameModeEnabled) return;
                        if (state._galgameRequestSeq !== seqAtOpen) return;
                        var overlayNow = getOverlay();
                        if (!overlayNow || overlayNow.hidden) return;
                        fetchGalgameOptionsForLatestTurn();
                    });
                }
            })
            .catch(function (error) {
                console.error('[ReactChatWindow] open failed:', error);
                showToast(getI18nText('chat.reactWindowLoadFailed', '聊天框资源加载失败'), 3500);
            });
    }

    function closeWindow() {
        var overlay = getOverlay();
        if (!overlay) return;
        pendingOpenAfterModelManagerHidden = false;
        // Closing the overlay should also abort any in-flight GalGame fetch
        // (parity with setGalgameModeEnabled(false) / setMessages /
        // clearMessages). Without this, a request that lands after close
        // still passes the seq guard and writes options into hidden state,
        // surfacing stale A/B/C the next time the user opens the window.
        invalidatePendingGalgameRequest();
        cancelActiveAnimation(); // 清理进行中的折叠/展开回调
        clearIdleDockState();
        deactivateToolCursor();

        // 如果当前处于最小化状态，恢复 shell 到正常态
        if (minimized) {
            var shell = getShell();
            if (shell) {
                shell.classList.remove('is-minimized');
                if (savedShellSize) {
                    if (savedShellSize.width) shell.style.width = savedShellSize.width;
                    if (savedShellSize.height) shell.style.height = savedShellSize.height;
                }
                if (savedShellPosition) {
                    shell.style.left = savedShellPosition.left + 'px';
                    shell.style.top = savedShellPosition.top + 'px';
                }
                shell.style.removeProperty('right');
                shell.style.removeProperty('bottom');
                shell.style.transform = 'none';
            }
            minimized = false;
            // closeWindow clears the minimized shell directly without routing
            // through setChatSurfaceMode, so the logical surface must be reset
            // too. Otherwise state.chatSurfaceMode stays 'minimized' and the next
            // openWindow() rebuilds the React props with chatSurfaceMode:
            // 'minimized', rendering a blank body over a no-longer-minimized
            // shell.
            state.chatSurfaceMode = normalizeChatSurfaceMode(lastRestorableChatSurfaceMode);
            resetCompactChatState();
            savedShellSize = null;
            savedShellPosition = null;
            syncChatSurfaceModeUI();
        }

        overlay.hidden = true;
        resetCompactChatState();
        document.body.classList.remove('react-chat-window-open');
        stopCompactMinimizeBallTracking();
        clearMobileContentCap();
        handleAvatarToolStateChange({
            active: false,
            toolId: null,
            tool: null,
            timestamp: Date.now()
        });
    }

    window.addEventListener('neko:main-ui-hidden-by-model-manager-changed', function(event) {
        if (isElectronChatWindow()) return;
        var hidden = !!(event && event.detail && event.detail.hidden);
        if (hidden || !pendingOpenAfterModelManagerHidden) return;
        pendingOpenAfterModelManagerHidden = false;
        openWindow();
    });

    var CLICK_THRESHOLD = 5; // px – 移动距离低于此值视为点击

    function isCompactDragHandleTarget(target) {
        return !!(
            target
            && typeof target.closest === 'function'
            && target.closest('[data-compact-drag-handle="true"]')
        );
    }

    function startDrag(clientX, clientY, options) {
        var shell = getShell();
        if (!shell) return;
        if (isYuiGuideDragLocked()) return;

        var opts = options || {};
        var compactSurface = !!(opts.compactSurface && getCurrentChatSurfaceMode() === 'compact' && !minimized);
        var rect = shell.getBoundingClientRect();
        dragState = {
            pointerOffsetX: clientX - rect.left,
            pointerOffsetY: clientY - rect.top,
            startClientX: clientX,
            startClientY: clientY,
            compactSurface: compactSurface,
            moved: false
        };

        shell.classList.add('is-dragging');
        document.body.classList.add('react-chat-window-dragging');
    }

    function updateDrag(clientX, clientY) {
        if (!dragState) return;
        if (isYuiGuideDragLocked()) {
            // 教程接管期强制中断拖拽：抑制后续 toggleMinimized，避免最小化球被误展开
            stopDrag({ suppressClick: true });
            return;
        }

        var dx = clientX - dragState.startClientX;
        var dy = clientY - dragState.startClientY;
        if (Math.abs(dx) > CLICK_THRESHOLD || Math.abs(dy) > CLICK_THRESHOLD) {
            dragState.moved = true;
        }

        var left = clientX - dragState.pointerOffsetX;
        var top = clientY - dragState.pointerOffsetY;
        if (dragState.compactSurface) {
            applyCompactSurfacePosition(left, top);
            return;
        }
        var clamped = clampPosition(left, top);
        applyPosition(clamped.left, clamped.top);
    }

    function stopDrag(options) {
        if (!dragState) return;
        var opts = options || {};
        var changedTouch = opts.changedTouches && opts.changedTouches.length > 0 ? opts.changedTouches[0] : null;

        var wasMoved = dragState.moved;

        var shell = getShell();
        if (shell) {
            shell.classList.remove('is-dragging');
            var rect = shell.getBoundingClientRect();
            // 最小化态下不持久化悬浮球坐标到展开态存储，
            // 否则 restorePosition 会把完整窗口放到悬浮球位置
            // 移动端坐标也不持久化，避免污染桌面端保存的位置
        }

        dragState = null;
        document.body.classList.remove('react-chat-window-dragging');

        // 最小化状态下，未发生拖拽移动 → 视为点击，恢复窗口
        // 但 suppressClick=true（如教程接管强制中断）时不触发，避免误展开
        if (minimized && !wasMoved && !opts.suppressClick) {
            if (changedTouch && isMobileWidth()) {
                armMobileExpandClickGuard(changedTouch.clientX, changedTouch.clientY);
            }
            toggleMinimized();
        }
    }

    function bindDragging() {
        var header = getHeader();
        if (!header) return;

        header.addEventListener('mousedown', function (event) {
            if (event.button !== 0) return;
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            var minimizeButton = $('reactChatWindowMinimizeButton');
            if (minimizeButton && minimizeButton.contains(event.target)) return;
            var avatarHeaderBtn = $('avatarPreviewHeaderButton');
            if (avatarHeaderBtn && avatarHeaderBtn.contains(event.target)) return;
            startDrag(event.clientX, event.clientY);
            event.preventDefault();
        });

        // touchstart 不 preventDefault：让浏览器自行决定是滚动还是点击，
        // 真正进入拖拽后由 touchmove（passive: false）阻止滚动即可。
        header.addEventListener('touchstart', function (event) {
            var closeButton = $('reactChatWindowCloseButton');
            if (closeButton && closeButton.contains(event.target)) return;
            var minimizeButton = $('reactChatWindowMinimizeButton');
            if (minimizeButton && minimizeButton.contains(event.target)) return;
            var avatarHeaderBtn = $('avatarPreviewHeaderButton');
            if (avatarHeaderBtn && avatarHeaderBtn.contains(event.target)) return;
            if (!event.touches || event.touches.length === 0) return;
            startDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: true });

        document.addEventListener('mousedown', function (event) {
            if (event.button !== 0) return;
            if (isElectronChatWindow()) return;
            if (!isCompactDragHandleTarget(event.target)) return;
            startDrag(event.clientX, event.clientY, {
                compactSurface: true
            });
            event.preventDefault();
            event.stopPropagation();
        }, true);

        document.addEventListener('touchstart', function (event) {
            if (isElectronChatWindow()) return;
            if (!isCompactDragHandleTarget(event.target)) return;
            if (!event.touches || event.touches.length === 0) return;
            startDrag(event.touches[0].clientX, event.touches[0].clientY, {
                compactSurface: true
            });
            event.preventDefault();
            event.stopPropagation();
        }, { capture: true, passive: false });

        document.addEventListener('mousemove', function (event) {
            if (!dragState) return;
            updateDrag(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!dragState || !event.touches || event.touches.length === 0) return;
            // chat.html 不走 mobile 路径，保留原 passive: true 语义，不吞原生滚动。
            if (!isElectronChatWindow()) event.preventDefault();
            updateDrag(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: false });

        document.addEventListener('mouseup', stopDrag);
        document.addEventListener('touchend', function (event) {
            stopDrag({ changedTouches: event.changedTouches });
        });
        document.addEventListener('touchcancel', function (event) {
            stopDrag({ changedTouches: event.changedTouches, suppressClick: true });
        });
    }

    var MIN_WIDTH = 320;
    var MIN_HEIGHT = 280;
    var GALGAME_MIN_HEIGHT = 420;
    var RESIZE_DIRECTIONS = ['n', 's', 'w', 'e', 'nw', 'ne', 'sw', 'se'];

    function getDesktopMinHeight() {
        if (!state.galgameModeEnabled) return MIN_HEIGHT;
        // 与 CSS 的 galgame min-height 对齐，避免拖拽时 JS 先把高度压到 280px。
        return Math.min(GALGAME_MIN_HEIGHT, Math.max(MIN_HEIGHT, window.innerHeight - 22));
    }

    function createResizeEdges() {
        var shell = getShell();
        if (!shell) return;

        RESIZE_DIRECTIONS.forEach(function (dir) {
            var edge = document.createElement('div');
            edge.className = 'react-chat-resize-edge react-chat-resize-' + dir;
            edge.dataset.resizeDir = dir;
            shell.appendChild(edge);
        });
    }

    function startResize(clientX, clientY, direction) {
        var shell = getShell();
        if (!shell) return;
        // 教程接管期禁止 resize，否则用户拉伸会让教程锚点和高亮错位
        if (isYuiGuideDragLocked()) return;
        // 手机端仅允许向上拖动调整高度（北侧边缘）
        if (isMobileWidth() && direction !== 'n') return;
        if (minimized) return;

        var rect = shell.getBoundingClientRect();
        resizeState = {
            dir: direction,
            startX: clientX,
            startY: clientY,
            origLeft: rect.left,
            origTop: rect.top,
            origWidth: rect.width,
            origHeight: rect.height
        };

        document.body.classList.add('react-chat-window-resizing');
    }

    function updateResize(clientX, clientY) {
        if (!resizeState) return;
        // 教程接管期强制中断 resize，与 updateDrag 的 lock 行为对称
        if (isYuiGuideDragLocked()) {
            stopResize();
            return;
        }

        var shell = getShell();
        if (!shell) return;

        var dx = clientX - resizeState.startX;
        var dy = clientY - resizeState.startY;
        var dir = resizeState.dir;

        var newLeft = resizeState.origLeft;
        var newTop = resizeState.origTop;
        var newWidth = resizeState.origWidth;
        var newHeight = resizeState.origHeight;

        // 手机端仅处理高度变化
        var mobile = isMobileWidth();

        if (!mobile && dir.indexOf('e') !== -1) {
            newWidth = Math.max(MIN_WIDTH, resizeState.origWidth + dx);
        }
        if (!mobile && dir.indexOf('w') !== -1) {
            var proposedWidth = resizeState.origWidth - dx;
            if (proposedWidth >= MIN_WIDTH) {
                newWidth = proposedWidth;
                newLeft = resizeState.origLeft + dx;
            } else {
                newWidth = MIN_WIDTH;
                newLeft = resizeState.origLeft + resizeState.origWidth - MIN_WIDTH;
            }
        }
        var desktopMinHeight = getDesktopMinHeight();

        if (!mobile && dir.indexOf('s') !== -1) {
            newHeight = Math.max(desktopMinHeight, resizeState.origHeight + dy);
        }
        if (dir.indexOf('n') !== -1) {
            var minH = mobile ? MOBILE_MIN_HEIGHT : desktopMinHeight;
            var proposedHeight = resizeState.origHeight - dy;
            if (proposedHeight >= minH) {
                newHeight = proposedHeight;
                newTop = resizeState.origTop + dy;
            } else {
                newHeight = minH;
                newTop = resizeState.origTop + resizeState.origHeight - minH;
            }
        }

        // Clamp to viewport
        newLeft = Math.max(0, Math.min(newLeft, window.innerWidth - 50));
        newTop = Math.max(0, Math.min(newTop, window.innerHeight - 50));
        newWidth = Math.min(newWidth, window.innerWidth);
        newHeight = Math.min(newHeight, window.innerHeight);

        if (mobile) {
            // 手机端：更新高度和 top，保持 CSS 控制的 left/width
            var maxMobileH = getMobileMaxHeight();
            var clampedH = Math.min(newHeight, maxMobileH);
            // 高度被截断时重新计算 top，保持面板底部不动
            if (clampedH < newHeight) {
                newTop = window.innerHeight - clampedH;
            }
            shell.style.height = clampedH + 'px';
            // 设置 top 并清除 bottom，使北侧拖拽正确向上扩展
            shell.style.top = newTop + 'px';
            shell.style.bottom = 'auto';
        } else {
            shell.style.width = newWidth + 'px';
            shell.style.height = newHeight + 'px';
            shell.style.left = newLeft + 'px';
            shell.style.top = newTop + 'px';
            shell.style.transform = 'none';
        }
    }

    function stopResize() {
        if (!resizeState) return;

        var shell = getShell();
        if (shell) {
            var rect = shell.getBoundingClientRect();
            if (isMobileWidth()) {
                // 手机端：保存用户设置的高度，恢复底部锚定
                mobileUserHeight = Math.round(rect.height);
                shell.style.removeProperty('top');
                shell.style.removeProperty('bottom');
                try {
                    localStorage.setItem(MOBILE_HEIGHT_STORAGE_KEY, String(mobileUserHeight));
                } catch (_) {}
            }
        }

        resizeState = null;
        document.body.classList.remove('react-chat-window-resizing');
    }

    function bindResizing() {
        var shell = getShell();
        if (!shell) return;

        shell.addEventListener('mousedown', function (event) {
            if (event.button !== 0) return;
            var target = event.target;
            if (!target || !target.dataset || !target.dataset.resizeDir) return;
            startResize(event.clientX, event.clientY, target.dataset.resizeDir);
            event.preventDefault();
        });

        shell.addEventListener('touchstart', function (event) {
            var target = event.target;
            if (!target || !target.dataset || !target.dataset.resizeDir) return;
            if (!event.touches || event.touches.length === 0) return;
            startResize(event.touches[0].clientX, event.touches[0].clientY, target.dataset.resizeDir);
            // chat.html 保留原 passive 语义；只在真正进入 resize（非 chat.html）才吞事件。
            if (resizeState && !isElectronChatWindow()) event.preventDefault();
        }, { passive: false });

        document.addEventListener('mousemove', function (event) {
            if (!resizeState) return;
            updateResize(event.clientX, event.clientY);
        });

        document.addEventListener('touchmove', function (event) {
            if (!resizeState || !event.touches || event.touches.length === 0) return;
            if (!isElectronChatWindow()) event.preventDefault();
            updateResize(event.touches[0].clientX, event.touches[0].clientY);
        }, { passive: false });

        document.addEventListener('mouseup', stopResize);
        document.addEventListener('touchend', stopResize);
        document.addEventListener('touchcancel', stopResize);
    }

    function bindBridgeEvents() {
        window.addEventListener(EVENT_PREFIX + 'set-messages', function (event) {
            setMessages(event.detail && event.detail.messages);
        });

        window.addEventListener(EVENT_PREFIX + 'append-message', function (event) {
            appendMessage(event.detail && event.detail.message);
        });

        window.addEventListener(EVENT_PREFIX + 'update-message', function (event) {
            var detail = event.detail || {};
            updateMessage(detail.messageId, detail.patch);
        });

        window.addEventListener(EVENT_PREFIX + 'remove-message', function (event) {
            removeMessage(event.detail && event.detail.messageId);
        });

        window.addEventListener(EVENT_PREFIX + 'clear-messages', function () {
            clearMessages();
        });

        window.addEventListener(EVENT_PREFIX + 'set-view-props', function (event) {
            setViewProps(event.detail && event.detail.viewProps);
        });

        window.addEventListener(EVENT_PREFIX + 'set-composer-attachments', function (event) {
            setComposerAttachments(event.detail && event.detail.attachments);
        });

        window.addEventListener(EVENT_PREFIX + 'set-composer-hidden', function (event) {
            setComposerHidden(event.detail && event.detail.hidden);
        });

        window.addEventListener(EVENT_PREFIX + 'set-galgame-mode', function (event) {
            var detail = event.detail || {};
            setGalgameModeEnabled(!!detail.enabled, { persist: detail.persist !== false });
        });

        ['live2d-floating-buttons-ready', 'vrm-model-loaded', 'mmd-model-loaded'].forEach(function (eventName) {
            window.addEventListener(eventName, revealPendingCompactSurfaceOpen);
        });

        window.addEventListener('neko:tutorial-started', function (event) {
            var detail = event && event.detail ? event.detail : {};
            if (detail.page !== 'home') return;
            setGalgameModeTemporarilyDisabled(true);
        });

        window.addEventListener('neko:tutorial-completed', function (event) {
            var detail = event && event.detail ? event.detail : {};
            if (detail.page !== 'home') return;
            setGalgameModeTemporarilyDisabled(false);
        });

        window.addEventListener('neko:tutorial-skipped', function (event) {
            var detail = event && event.detail ? event.detail : {};
            if (detail.page !== 'home') return;
            setGalgameModeTemporarilyDisabled(false);
        });

        window.addEventListener('neko:tutorial-ended-without-completion', function (event) {
            var detail = event && event.detail ? event.detail : {};
            if (detail.page !== 'home') return;
            setGalgameModeTemporarilyDisabled(false);
        });

        // Refresh option list whenever an assistant turn finishes streaming.
        window.addEventListener('neko-assistant-turn-end', function () {
            if (!state.galgameModeEnabled) return;
            // Skip when the chat overlay is hidden — otherwise galgame mode's
            // default-on flag would spam /api/galgame/options (and summary-tier
            // inference) on every assistant turn even for users who never
            // opened the React chat window (voice-only / proactive paths).
            var overlay = getOverlay();
            if (!overlay || overlay.hidden) return;
            // app-chat-adapter's processRealisticQueue can still be sleeping
            // 1-2s between bubble flushes when turn-end fires, so the message
            // list may not yet contain the final assistant sentences. Wait
            // until the queue is drained and the lock is released before
            // building the request, with a hard cap so a stuck queue can't
            // permanently block the option fetch.
            //
            // Snapshot _galgameRequestSeq before waiting: invalidatePending…
            // (called by setMessages / clearMessages / handleComposerSubmit)
            // bumps the seq when the conversation switches or the user moves
            // on. If that happens during the wait window, we drop this fetch
            // so a stale turn-end can't render A/B/C into the new context.
            var seqAtSchedule = state._galgameRequestSeq;
            waitForAssistantBubblesFlushed(4000).then(function () {
                if (!state.galgameModeEnabled) return;
                if (state._galgameRequestSeq !== seqAtSchedule) return;
                // The overlay may have been closed during the 4s wait — re-check
                // before firing the fetch so closing the chat mid-turn doesn't
                // still kick off a background summary-tier inference.
                var overlayNow = getOverlay();
                if (!overlayNow || overlayNow.hidden) return;
                fetchGalgameOptionsForLatestTurn();
            });
        });
    }

    function init() {
        var trigger = $('reactChatWindowButton');
        var closeButton = $('reactChatWindowCloseButton');
        var minimizeButton = getMinimizeButton();
        var backdrop = $('react-chat-window-backdrop');
        var avatarHeaderButton = $('avatarPreviewHeaderButton');

        ensureViewProps();
        state.chatSurfaceMode = readChatSurfaceModePreference();
        lastRestorableChatSurfaceMode = state.chatSurfaceMode;
        resetCompactChatState();
        state.viewProps = Object.assign({}, ensureViewProps(), {
            chatSurfaceMode: getCurrentChatSurfaceMode(),
            compactChatState: getCurrentCompactChatState()
        });
        syncChatSurfaceModeUI();
        prewarmUserDisplayName();
        // Resolve the persisted GalGame preference now that the storage-location
        // barrier has settled (initAfterStorageBarrier has awaited it before
        // calling init). Reading at module-eval would risk capturing the value
        // from a storage namespace the barrier is about to remap.
        // setGalgameModeEnabled idempotently syncs state + body class + fires
        // the change event when the resolved pref differs from the safe default.
        if (isHomeTutorialInteractionLocked()) {
            setGalgameModeTemporarilyDisabled(true);
        } else {
            setGalgameModeEnabled(readGalgameModePreference(), { persist: false });
        }

        if (trigger) {
            trigger.addEventListener('click', openWindow);
        }
        if (closeButton) {
            closeButton.addEventListener('click', closeWindow);
        }
        if (minimizeButton) {
            minimizeButton.addEventListener('click', function (event) {
                event.stopPropagation();
                toggleMinimized();
            });
        }
        // Note: the avatarPreviewHeaderButton click is bound by app-chat-avatar.js
        // (it owns the standalone avatar preview popup and toggling behavior).
        // We only fire the host event here for external listeners/analytics.
        if (avatarHeaderButton) {
            avatarHeaderButton.addEventListener('click', function () {
                dispatchHostEvent('avatar-generator-click', {});
            });
        }
        if (backdrop) {
            // When chat adapter is active (primary mode), backdrop should not
            // block interaction with the model behind it.
            if (!window._chatAdapterActive) {
                backdrop.addEventListener('click', closeWindow);
            } else {
                backdrop.style.pointerEvents = 'none';
            }
        }

        document.addEventListener('mousedown', blockMobileExpandSyntheticPointerEvent, true);
        document.addEventListener('mouseup', blockMobileExpandSyntheticPointerEvent, true);
        document.addEventListener('click', blockMobileExpandSyntheticPointerEvent, true);
        bindDragging();
        createResizeEdges();
        bindResizing();
        bindBridgeEvents();
        ensureElectronChatMinimizedStateBridge();

        // 恢复手机端用户设置的高度
        try {
            var storedMobileHeight = localStorage.getItem(MOBILE_HEIGHT_STORAGE_KEY);
            if (storedMobileHeight) {
                var parsed = Number(storedMobileHeight);
                if (Number.isFinite(parsed) && parsed >= MOBILE_MIN_HEIGHT) {
                    mobileUserHeight = parsed;
                }
            }
        } catch (_) {}

        // 悬浮球 hover 效果（参考原版 #chat-container 实现）
        var header = getHeader();
        if (header) {
            header.addEventListener('mouseenter', function () {
                if (!minimized) return;
                var shell = getShell();
                var ico = shell && shell.querySelector('.react-chat-minimized-icon');
                if (ico) ico.src = '/static/icons/expand_icon_on.png';
            });
            header.addEventListener('mouseleave', function () {
                if (!minimized) return;
                var shell = getShell();
                var ico = shell && shell.querySelector('.react-chat-minimized-icon');
                if (ico) ico.src = '/static/icons/expand_icon_off_ball.png';
            });
        }

        window.addEventListener('keydown', function (event) {
            if (window._chatAdapterActive) return;
            var overlay = getOverlay();
            if (event.key === 'Escape' && overlay && !overlay.hidden) {
                closeWindow();
            }
        });

        window.addEventListener('resize', function () {
            scheduleCompactMinimizeBallTracking();
            var overlay = getOverlay();
            if (overlay && !overlay.hidden) {
                if (minimized) {
                    var dockTarget = getIdleDockTarget();
                    if (dockTarget) {
                        var dockShell = getShell();
                        if (dockShell) {
                            dockShell.style.left = dockTarget.left + 'px';
                            dockShell.style.top = dockTarget.top + 'px';
                        }
                        return;
                    }
                    // 最小化态下，根据当前布局（桌面圆球 / 手机胶囊）重新贴到视口内。
                    // 手机胶囊宽度由 CSS !important 控制（width: calc(100vw - 12px)），
                    // 这里只需修正左上角坐标，避免旋转屏或拖窗后溢出。
                    var shell = getShell();
                    if (shell) {
                        var r = shell.getBoundingClientRect();
                        var minW = r.width || MINIMIZED_SIZE;
                        var minH = r.height || MINIMIZED_SIZE;
                        var safeLeft, safeTop;
                        if (isMobileWidth()) {
                            // 圆形悬浮球：保持用户拖拽位置，仅 clamp 到视口内
                            safeLeft = Math.max(0, Math.min(r.left, window.innerWidth - minW));
                            safeTop = Math.max(0, Math.min(r.top, window.innerHeight - minH));
                        } else {
                            safeLeft = Math.max(0, Math.min(r.left, window.innerWidth - minW));
                            safeTop = Math.max(0, Math.min(r.top, window.innerHeight - minH));
                        }
                        if (safeLeft !== r.left || safeTop !== r.top) {
                            shell.style.left = safeLeft + 'px';
                            shell.style.top = safeTop + 'px';
                        }
                    }
                } else {
                    restorePosition();
                    syncCompactSurfaceAnchor();
                    scheduleMobileContentLayout();
                }
            }
        });

        window.addEventListener('localechange', function () {
            state.viewProps = createBaseViewProps();
            renderWindow();
        });

        window.addEventListener('neko:auto-goodbye:state-change', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
            if (!detail || detail.type !== 'visual-tier') return;

            idleDockTier = detail.tier === IDLE_DOCK_TIER_CAT2 || detail.tier === IDLE_DOCK_TIER_CAT3
                ? detail.tier
                : IDLE_DOCK_TIER_NONE;

            var overlay = getOverlay();
            if (!overlay || overlay.hidden || isElectronChatWindow()) return;

            if (isIdleDockTierActive()) {
                if (!idleDockActive) {
                    enterIdleDock();
                } else {
                    scheduleIdleDockSync();
                    refreshIdleDockContainerObserver();
                }
                return;
            }

            if (hasIdleDockPendingOrActive()) {
                exitIdleDock({
                    preserveCurrentPosition: detail.source === 'return-ball-drag-demotion',
                });
                return;
            }

            clearIdleDockState();
        });
        window.addEventListener('neko:idle-return-ball-state', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
            if (!detail) return;
            handleElectronIdleReturnBallState(detail);
        });
        window.addEventListener('neko:idle-chat-pair-move-bounds', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
            if (!detail) return;
            scheduleElectronCat1PairMoveBounds(detail.screenRect || detail.bounds);
        });
        window.addEventListener('live2d-return-click', function () {
            if (hasElectronIdleDockPendingOrActive()) { exitElectronIdleDock(); }
            if (hasIdleDockPendingOrActive()) { exitIdleDock(); return; }
            clearIdleDockState();
        });
        window.addEventListener('vrm-return-click', function () {
            if (hasElectronIdleDockPendingOrActive()) { exitElectronIdleDock(); }
            if (hasIdleDockPendingOrActive()) { exitIdleDock(); return; }
            clearIdleDockState();
        });
        window.addEventListener('mmd-return-click', function () {
            if (hasElectronIdleDockPendingOrActive()) { exitElectronIdleDock(); }
            if (hasIdleDockPendingOrActive()) { exitIdleDock(); return; }
            clearIdleDockState();
        });

        window.addEventListener('neko:desktop-compact-layout-change', function (event) {
            var layout = event && event.detail ? event.detail : window.__nekoDesktopCompactLayout;
            handleDesktopCompactLayoutChange(layout || null);
        });
        if (window.__nekoDesktopCompactLayout) {
            handleDesktopCompactLayoutChange(window.__nekoDesktopCompactLayout);
        }
        window.addEventListener('neko:desktop-avatar-bounds-change', function () {
            scheduleCompactMinimizeBallTracking();
        });
        window.addEventListener('neko:compact-surface-resize-request', function (event) {
            applyCompactSurfaceResizeRequest(event.detail || {});
        }, true);
        window.addEventListener('neko:compact-surface-resize-width-change', function () {
            syncCompactInteractionGeometry();
        });
    }

    function applyInitialComposerHiddenState() {
        // 独立 Chat 刷新时，语音态广播可能早于 React host 初始化到达。
        // 初始化完成后补读一次共享状态，避免 composer 以默认显示态首绘。
        try {
            var initialComposerShouldHide = false;
            if (typeof window.shouldKeepVoiceComposerHidden === 'function') {
                initialComposerShouldHide = window.shouldKeepVoiceComposerHidden();
            } else if (window.appState) {
                initialComposerShouldHide = !!(
                    window.appState.isRecording ||
                    window.appState.voiceChatActive ||
                    window.appState.voiceStartPending ||
                    window.isMicStarting
                );
            }
            if (initialComposerShouldHide) {
                setComposerHidden(true);
            }
        } catch (_) {
            // 首绘兜底失败不影响后续 session_started 同步
        }
    }

    async function initAfterStorageBarrier() {
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
        init();
        applyInitialComposerHiddenState();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initAfterStorageBarrier);
    } else {
        initAfterStorageBarrier();
    }

    window.reactChatWindowHost = {
        ensureBundleLoaded: ensureBundleLoaded,
        openWindow: openWindow,
        closeWindow: closeWindow,
        setViewProps: setViewProps,
        setMessages: setMessages,
        setComposerAttachments: setComposerAttachments,
        setComposerHidden: setComposerHidden,
        setHomeTutorialInteractionLocked: setHomeTutorialInteractionLocked,
        deactivateToolCursor: deactivateToolCursor,
        appendMessage: appendMessage,
        updateMessage: updateMessage,
        removeMessage: removeMessage,
        clearMessages: clearMessages,
        getState: getStateSnapshot,
        setOnMessageAction: function (handler) {
            state.onMessageAction = typeof handler === 'function' ? handler : null;
        },
        setOnComposerImportImage: function (handler) {
            state.onComposerImportImage = typeof handler === 'function' ? handler : null;
        },
        setOnComposerScreenshot: function (handler) {
            state.onComposerScreenshot = typeof handler === 'function' ? handler : null;
        },
        setOnComposerRemoveAttachment: function (handler) {
            state.onComposerRemoveAttachment = typeof handler === 'function' ? handler : null;
        },
        setOnComposerSubmit: function (handler) {
            state.onComposerSubmit = typeof handler === 'function' ? handler : null;
        },
        setOnCompactHistoryDrop: function (handler) {
            state.onCompactHistoryDrop = typeof handler === 'function' ? handler : null;
        },
        prepareCompactHistoryDropSubmit: prepareCompactHistoryDropSubmit,
        setOnCompactHistoryDragStateChange: function (handler) {
            state.onCompactHistoryDragStateChange = typeof handler === 'function' ? handler : null;
        },
        setOnAvatarInteraction: function (handler) {
            state.onAvatarInteraction = typeof handler === 'function' ? handler : null;
        },
        setOnAvatarToolStateChange: function (handler) {
            state.onAvatarToolStateChange = typeof handler === 'function' ? handler : null;
        },
        rollbackLastDraft: rollbackLastDraft,
        clearPendingRollbackDraft: clearPendingRollbackDraft,
        setChatSurfaceMode: setChatSurfaceMode,
        cycleChatSurfaceMode: cycleChatSurfaceMode,
        setCompactChatState: setCompactChatState,
        setGalgameModeEnabled: function (enabled, options) {
            setGalgameModeEnabled(enabled, options || {});
        },
        isGalgameModeEnabled: function () { return !!state.galgameModeEnabled; },
        getChatSurfaceMode: function () { return getCurrentChatSurfaceMode(); },
        refreshGalgameOptions: fetchGalgameOptionsForLatestTurn,
        // Mini-game invite ChoicePrompt：app-websocket.js 收到对应 WS message 时调
        setMiniGameInvitePrompt: setMiniGameInvitePrompt,
        // unified resolved handler：accept 兼 launch / decline / suppress 都通过
        // 这条入口分发——前端 dismiss prompt UI + accept 时 window.open。替代了
        // 旧 launchMiniGame（accept-only）路径，让 codex P2 的 cross-window
        // dismiss 一致性能 cover decline / later 路径。
        handleMiniGameInviteResolved: handleMiniGameInviteResolved,
        isMounted: function () { return mounted; }
    };

})();
