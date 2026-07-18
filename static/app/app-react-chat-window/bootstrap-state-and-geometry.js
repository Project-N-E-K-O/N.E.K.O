/**
 * app-react-chat-window/bootstrap-state-and-geometry.js
 * Host-side controller for the exported React chat window.
 *
 * Contract copied from the original entrypoint:
 * - Dynamically loads the React bundle if needed
 * - Owns window open/close/minimize/drag state
 * - Owns chat view props + messages state
 * - Exposes a stable bridge for host code / IPC adapters
 * Load all parts in filename order; this is a classic global script (no import/export).
 */
(function () {
    'use strict';

    window.reactChatWindowHost = window.reactChatWindowHost || {};
    const I = window.__appReactChatWindowParts || (window.__appReactChatWindowParts = {});
I.BUNDLE_SRC = '/static/react/neko-chat/neko-chat-window.iife.js';
    I.STORAGE_LEFT_KEY = 'neko.reactChatWindow.left';
    I.STORAGE_TOP_KEY = 'neko.reactChatWindow.top';
    I.STORAGE_WIDTH_KEY = 'neko.reactChatWindow.width';
    I.STORAGE_HEIGHT_KEY = 'neko.reactChatWindow.height';
    var GALGAME_STORAGE_KEY = 'neko.reactChatWindow.galgameMode';
    var CHAT_SURFACE_MODE_STORAGE_KEY = 'neko.reactChatWindow.chatSurfaceMode';
    I.GALGAME_HISTORY_LIMIT = 6;
    I.EVENT_PREFIX = 'react-chat-window:';
    I.CHAT_MINIMIZED_BALL_ICON_SRC = '/static/assets/neko-idle/chat-minimized-yarn-ball-116.png';
    I.CHAT_MINIMIZED_BALL_ICON_SRCSET = '/static/assets/neko-idle/chat-minimized-yarn-ball-116.png 1x, /static/assets/neko-idle/chat-minimized-yarn-ball-232.png 2x';
    // Frozen legacy `full` keeps its era's minimized orb — the glowing "breathing
    // light" ball (old icon + box-shadow pulse from full-chat-minimize.css) —
    // instead of the active compact yarn ball. Strictly gated on the restorable
    // surface being full so the compact minimize path is untouched.
    I.CHAT_MINIMIZED_BALL_LEGACY_FULL_ICON_SRC = '/static/icons/expand_icon_off_ball.png';

    I.loadedPromise = null;
    I.mounted = false;
    I.dragState = null;
    I.suppressDragReleaseClick = false;
    I.resizeState = null;
    I.minimized = false;
    I.savedShellSize = null;
    I.savedShellPosition = null; // {left, top} before minimize – used to fly back on expand
    I.HOME_IDLE_DOCK_GAP = -12;
    I.IDLE_DOCK_TIER_NONE = 'none';
    I.IDLE_DOCK_TIER_CAT2 = 'cat2';
    I.IDLE_DOCK_TIER_CAT3 = 'cat3';
    I.idleDockTier = I.IDLE_DOCK_TIER_NONE;
    I.idleDockActive = false;
    I.idleDockSavedPosition = null;
    I.idleDockTriggeredMinimize = false;
    I.idleDockMinimizeObserver = null;
    I.electronIdleDockActive = false;
    I.electronIdleDockTriggeredCollapse = false;
    I.electronIdleDockSavedBounds = null;
    I.electronIdleDockLastScreenRect = null;
    I.electronIdleDockEntering = false;
    I.electronIdleDockRetryTimer = 0;
    I.electronIdleDockDesired = false;
    I.electronIdleDockGeneration = 0;
    I.electronIdleDockPositionFrame = 0;
    I.electronIdleDockPositionSeq = 0;
    I.electronIdleDockCurrentBounds = null;
    I.electronIdleDockWorkArea = null;
    I.electronChatMinimizedStateFrame = 0;
    I.electronChatMinimizedStateTimer = 0;
    I.electronChatMinimizedStateSignature = '';
    I.electronChatMinimizedStatePublishedAt = 0;
    I.electronCat1PairMoveBoundsFrame = 0;
    I.electronCat1PairMovePendingBounds = null;
    I.electronCat1PairMovePendingForce = false;
    I.electronCat1PairMovePendingReason = '';
    I.ELECTRON_CHAT_MINIMIZED_STATE_HEARTBEAT_MS = 1000;
    I.savedExpandedShellPosition = null; // last known full-surface desktop position
    I.lastRestorableChatSurfaceMode = 'compact';
    I.tutorialChatRequestSeq = 0;
    I._sortKeySeq = 0; // monotonically increasing sortKey counter
    var COMPACT_CHAT_STATES = ['default', 'options', 'input'];
    // The active compact↔minimized cycle. `full` is intentionally NOT here: it is
    // the frozen legacy surface, entered only via an explicit setChatSurfaceMode
    // ('full') (e.g. the NEKO-PC tray toggle), never by cycling through compact.
    var CHAT_SURFACE_MODE_SEQUENCE = ['compact', 'minimized'];
    // Full set of valid surface modes for normalization. Keeps `full` a first-class
    // mode the host honors when set, without letting it pollute the compact cycle.
    var CHAT_SURFACE_MODES = ['full', 'compact', 'minimized'];

    I.state = {
        viewProps: null,
        messages: [],
        composerAttachments: [],
        composerHidden: false,
        goodbyeComposerHidden: false,
        onMessageAction: null,
        onComposerImportImage: null,
        onComposerScreenshot: null,
        onComposerRemoveAttachment: null,
        onComposerSubmit: null,
        onAvatarInteraction: null,
        onAvatarToolStateChange: null,
        pendingAvatarInteractions: [],
        pendingRollbackDrafts: Object.create(null),
        rollbackDraft: '',
        _avatarToolDeactivationKey: '',
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
        homeTutorialInputLocked: false,
        _avatarToolMenuOpenRequestSeq: 0,
        _compactToolFanOpenRequestSeq: 0,
        _compactToolWheelRotateRequestSeq: 0,
        _compactToolWheelIndexRequestSeq: 0,
        _galgameRequestSeq: 0,
        // 通用 ChoicePrompt 框架。当前承载 mini_game_invite 与新手破冰；
        // galgame mode 仍走 galgameOptions 路径（BC，渐进迁移）。
        // shape: { source, sessionId, gameType, options: [{choice,label}] } | null
        choicePrompt: null,
        // dedupe set：已经 window.open 过的 mini-game session_id。键集，行为按 set 用。
        // 防止 endpoint 路径 + WS push 路径同一 session 双开窗口。
        _launchedMiniGameSessionIds: Object.create(null)
    };

    function normalizeChatSurfaceMode(mode) {
        return CHAT_SURFACE_MODES.indexOf(mode) >= 0 ? mode : 'compact';
    }

    function isCompactOnlyElectronChatHost() {
        var body = document.body;
        return !!(
            body
            && I.isElectronChatWindow()
            && body.getAttribute('data-chat-host-kind') === 'compact'
        );
    }

    function isElectronChatRuntime() {
        var body = document.body;
        return !!(
            (body && body.classList.contains('neko-electron-runtime')) ||
            window.nekoChatWindow ||
            /Electron/i.test(navigator.userAgent || '') ||
            (window.process && window.process.versions && window.process.versions.electron)
        );
    }

    I.isCompactOnlyElectronRuntimeChatHost = function isCompactOnlyElectronRuntimeChatHost() {
        return !!(isCompactOnlyElectronChatHost() && isElectronChatRuntime());
    }

    I.coerceChatSurfaceModeForHost = function coerceChatSurfaceModeForHost(mode) {
        var normalized = normalizeChatSurfaceMode(mode);
        // /chat 是 Electron 紧凑宿主；full 由 /chat_full 独立窗口承载，避免历史 full 状态污染透明承载窗。
        if (normalized === 'full' && isCompactOnlyElectronChatHost()) {
            return 'compact';
        }
        return normalized;
    }

    I.normalizeCompactChatState = function normalizeCompactChatState(mode) {
        return COMPACT_CHAT_STATES.indexOf(mode) >= 0 ? mode : 'default';
    }

    I.getCurrentChatSurfaceMode = function getCurrentChatSurfaceMode() {
        return I.coerceChatSurfaceModeForHost(I.state.chatSurfaceMode);
    }

    I.getCurrentCompactChatState = function getCurrentCompactChatState() {
        return I.normalizeCompactChatState(I.state.compactChatState);
    }

    I.isHomeCompactSurfaceRoute = function isHomeCompactSurfaceRoute() {
        var body = document.body;
        return !!(
            body
            && body.classList.contains('subtitle-web-host')
            && I.getCurrentChatSurfaceMode() === 'compact'
        );
    }

    I.isDesktopHomeCompactSurfaceRoute = function isDesktopHomeCompactSurfaceRoute() {
        var body = document.body;
        return !!(
            I.isElectronChatWindow()
            && body
            && body.classList.contains('subtitle-web-host')
            && document.querySelector('.compact-chat-surface-shell')
        );
    }

    I.getNextChatSurfaceMode = function getNextChatSurfaceMode(mode) {
        var normalized = I.coerceChatSurfaceModeForHost(mode);
        if (normalized === 'minimized') {
            return I.coerceChatSurfaceModeForHost(I.lastRestorableChatSurfaceMode);
        }
        // Any real surface — compact or the revived legacy full — minimizes to the
        // ball; restoring returns to the last real surface via the branch above.
        // full is deliberately kept out of CHAT_SURFACE_MODE_SEQUENCE so it never
        // pollutes the compact cycle, but that made the old index-based fallback
        // resolve full -> sequence[0] -> compact, which surfaced as "full minimize
        // jumps to compact". Minimizing is the same intent from either surface.
        return 'minimized';
    }

    I.resetCompactChatState = function resetCompactChatState() {
        I.state.compactChatState = 'default';
    }

    // Default surface when the user has no persisted preference yet.
    //   - Web / browser (wide) → `full` (the complete chat window).
    //   - Web / browser (mobile width, non-Electron) → `compact` (the floating
    //     bar): the full window is built for desktop and overflows a phone, so a
    //     fresh narrow visitor opens compact. `isMobileWidth()` already excludes
    //     both Electron runtimes, so this only ever fires for narrow web.
    //   - Electron desktop shell → `compact` (the floating bar): the chat window
    //     (chat.html, electron chat body class) and the pet window (index.html,
    //     __LANLAN_IS_ELECTRON_PET__) both opt into compact.
    // An explicit `window.__NEKO_CHAT_DEFAULT_COMPACT__` boolean overrides either
    // way, so a host can force the default without relying on the markers above.
    function getDefaultChatSurfaceMode() {
        try {
            if (window.__NEKO_CHAT_DEFAULT_COMPACT__ === true) return 'compact';
            if (window.__NEKO_CHAT_DEFAULT_COMPACT__ === false) return 'full';
        } catch (_) {}
        if (I.isElectronChatWindow()) return 'compact';
        try {
            if (window.__LANLAN_IS_ELECTRON_PET__) return 'compact';
        } catch (_) {}
        if (I.isMobileWidth()) return 'compact';
        return 'full';
    }

    function shouldPersistChatSurfaceModePreference() {
        // Desktop and web share the same page state contract. The caller only
        // persists compact only; minimized still restores to the last real surface.
        return true;
    }

    function readChatSurfaceModePreference() {
        var fallback = I.coerceChatSurfaceModeForHost(getDefaultChatSurfaceMode());
        if (!shouldPersistChatSurfaceModePreference()) {
            return fallback;
        }
        try {
            var raw = localStorage.getItem(CHAT_SURFACE_MODE_STORAGE_KEY);
            // The storage key holds the last restorable surface: 'compact' or the
            // revived legacy 'full'. An explicit stored choice wins; otherwise we
            // fall back to the per-runtime default (web=full / Electron=compact).
            // A stray 'minimized' never lingers — rewrite it to the fallback.
            if (raw === 'full') return I.coerceChatSurfaceModeForHost('full');
            if (raw === 'compact') return I.coerceChatSurfaceModeForHost('compact');
            if (raw === 'minimized') {
                localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, fallback);
            }
            return fallback;
        } catch (_) {
            return fallback;
        }
    }

    I.readInitialChatSurfaceMode = function readInitialChatSurfaceMode() {
        var body = document.body;
        var declaredModeAttr = body
            ? body.getAttribute('data-initial-chat-surface-mode')
            : '';
        var declaredMode = declaredModeAttr ? normalizeChatSurfaceMode(declaredModeAttr) : '';
        if (declaredMode === 'compact' || declaredMode === 'full') {
            if (declaredMode === 'full' && isCompactOnlyElectronChatHost()) {
                return 'compact';
            }
            return declaredMode;
        }
        return readChatSurfaceModePreference();
    }

    I.persistChatSurfaceModePreference = function persistChatSurfaceModePreference(mode) {
        // Persist only the restorable surfaces (compact/full); minimized restores
        // to lastRestorableChatSurfaceMode rather than being persisted directly.
        if (mode !== 'compact' && mode !== 'full') return;
        if (!shouldPersistChatSurfaceModePreference()) return;
        try {
            localStorage.setItem(CHAT_SURFACE_MODE_STORAGE_KEY, mode);
        } catch (_) {}
    }

    I.readGalgameModePreference = function readGalgameModePreference() {
        try {
            var raw = localStorage.getItem(GALGAME_STORAGE_KEY);
            if (raw === null) return true; // default ON per spec
            return raw === 'true';
        } catch (_) {
            return true;
        }
    }

    I.persistGalgameModePreference = function persistGalgameModePreference(enabled) {
        try {
            localStorage.setItem(GALGAME_STORAGE_KEY, enabled ? 'true' : 'false');
        } catch (_) {}
    }

    // composer 隐藏（请她离开）时强制视为 OFF：保留 state.galgameModeEnabled，
    // 但摘掉 body class，让 chat.html / preload-chat-react 里依赖该 class 的
    // 高最小高度 / 窗口最小高度 CSS 不再撑住空白输入区。
    // body class 切换、change 事件 payload 都走这个 helper，避免逻辑分叉。
    I.getEffectiveComposerHidden = function getEffectiveComposerHidden() {
        return !!(I.state.composerHidden || I.state.goodbyeComposerHidden);
    }

    I.getNekoGoodbyeModeActive = function getNekoGoodbyeModeActive() {
        try {
            if (typeof window.isNekoGoodbyeModeActive === 'function') {
                return !!window.isNekoGoodbyeModeActive();
            }
        } catch (_) {}
        return !!(
            (window.live2dManager && window.live2dManager._goodbyeClicked)
            || (window.vrmManager && window.vrmManager._goodbyeClicked)
            || (window.mmdManager && window.mmdManager._goodbyeClicked)
            || (window.__nekoGoodbyeSilentState && window.__nekoGoodbyeSilentState.active === true)
        );
    }

    I.hasLocalGoodbyeModeSource = function hasLocalGoodbyeModeSource() {
        // Standalone chat pages inherit window.isNekoGoodbyeModeActive from
        // app-state.js even though they do not own model managers. A false
        // hook result is therefore not authoritative here; only a true hook
        // result or an actual local manager/silent-state object can safely
        // drive localOnly goodbye recomputation.
        try {
            if (typeof window.isNekoGoodbyeModeActive === 'function'
                && window.isNekoGoodbyeModeActive()) {
                return true;
            }
        } catch (_) {}
        return !!(
            window.live2dManager
            || window.vrmManager
            || window.mmdManager
            || (window.__nekoGoodbyeSilentState && typeof window.__nekoGoodbyeSilentState === 'object')
        );
    }

    I.getEffectiveGalgameEnabled = function getEffectiveGalgameEnabled() {
        return !!I.state.galgameModeEnabled && !I.getEffectiveComposerHidden();
    }

    I.applyGalgameBodyClass = function applyGalgameBodyClass() {
        if (typeof document === 'undefined' || !document.body) return;
        document.body.classList.toggle('galgame-mode-enabled', I.getEffectiveGalgameEnabled());
    }

    // 镜像 galgame 的 body class 策略：附件区（截图 / 导入图片）出现时贴
    // body 上 composer-has-attachments，让 chat.html 的 min-height 兜底和
    // preload-chat-react.js 的 Electron resize 下限同时感知。否则附件直接
    // 把 .composer-input 顶出可视区域 —— galgame 的 385px 兜底不覆盖它。
    function applyAttachmentsBodyClass(hasAttachments) {
        if (typeof document === 'undefined' || !document.body) return;
        document.body.classList.toggle('composer-has-attachments', !!hasAttachments);
    }

    I.getEffectiveComposerAttachmentsVisible = function getEffectiveComposerAttachmentsVisible() {
        return !!(
            I.state.composerAttachments
            && I.state.composerAttachments.length > 0
            && !I.getEffectiveComposerHidden()
        );
    }

    I.syncComposerAttachmentsVisibility = function syncComposerAttachmentsVisibility(previousVisible) {
        var nextVisible = I.getEffectiveComposerAttachmentsVisible();
        applyAttachmentsBodyClass(nextVisible);
        if (previousVisible !== undefined && previousVisible !== nextVisible) {
            I.dispatchHostEvent('composer-attachments-change', { hasAttachments: nextVisible });
        }
        return nextVisible;
    }
    // No module-eval apply: state defaults to off here; init() resolves the
    // persisted preference and calls setGalgameModeEnabled(...) which flips
    // the class and fires the change event chat.html listens to.

    var MOBILE_MAX_HEIGHT_RATIO = 0.85;
    I.MOBILE_MESSAGE_MIN_HEIGHT = 60;
    I.DESKTOP_DEFAULT_LEFT_RATIO = 0.05;
    I.MOBILE_MIN_HEIGHT = 150;
    I.MOBILE_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.mobileHeight';
    I.MOBILE_EXPAND_CLICK_GUARD_MS = 700;
    I.MOBILE_EXPAND_CLICK_GUARD_RADIUS = 24;
    I.MOBILE_EXPAND_VISUAL_GUARD_MS = 900;
    var COMPACT_MINIMIZE_BALL_VIEWPORT_PAD = 12;
    var COMPACT_MINIMIZE_BALL_AVATAR_GAP = -4;
    var COMPACT_MINIMIZE_BALL_AVATAR_VERTICAL_RATIO = 0.58;
    I.COMPACT_SURFACE_MAX_WIDTH = 430;
    var COMPACT_SURFACE_RESIZE_MAX_WIDTH = 720;
    var COMPACT_SURFACE_MOBILE_MIN_WIDTH = 180;
    // 桌面端 compact surface 可拖到的最短宽度。默认/初始宽度仍为 COMPACT_SURFACE_MAX_WIDTH=430
    // （见 getCompactSurfaceMetrics），这里只放宽 resize 下限，让用户能把对话条拖得更窄。
    // 须与 react-neko-chat App.tsx 的 COMPACT_SURFACE_RESIZE_(MOBILE_)MIN_WIDTH 同步，
    // 否则 React 侧拖到的宽度会在 phase=end 时被 host 这份 clamp 顶回去。
    I.COMPACT_SURFACE_DESKTOP_MIN_WIDTH = 180;
    var COMPACT_SURFACE_MOBILE_VIEWPORT_GUTTER = 16;
    I.COMPACT_SURFACE_VIEWPORT_PAD_X = 16;
    I.COMPACT_SURFACE_VIEWPORT_PAD_TOP = 12;
    I.COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM = 18;
    I.COMPACT_SURFACE_ELECTRON_DEFAULT_BOTTOM_GAP = 320;
    I.COMPACT_SURFACE_DEFAULT_HEIGHT = 64;
    I.COMPACT_SURFACE_AVATAR_VERTICAL_RATIO = 0.72;
    var COMPACT_SURFACE_POSITION_STORAGE_KEY = 'neko.reactChatWindow.compactSurfacePosition';
    I.mobileUserHeight = 0; // 用户手动设置的手机端高度（0 = 自动）
    I.mobileLayoutFrame = 0;
    I.mobileExpandClickGuard = null;
    I.mobileExpandVisualGuardTimer = 0;
    I.compactMinimizeBallFrame = 0;
    // surface 锚点跟踪：拖拽/缩放会话每帧同步保证跟手；静止时只做短暂 settle，
    // 后续由 layout/avatar/resize/geometry 事件重新唤醒，避免 compact 打开后长期空转。
    I.COMPACT_SURFACE_IDLE_SETTLE_FRAME_COUNT = 3;
    I.compactSurfaceTrackingSettleFramesRemaining = 0;
    I.compactSurfaceAnchorSnapshot = '';
    var compactDesktopSurfaceAnchorSnapshot = '';
    I.compactInteractionGeometrySnapshot = '';
    I.compactSurfaceAnchorLocked = false;
    I.compactSurfacePendingModelOpen = false;
    I.compactSurfaceResizeSession = null;
    I.compactSurfaceDesktopResizeActive = false;
    I.compactSurfaceDesktopDragActive = false;
    var IDLE_CAT1_COMPACT_MIRROR_TIMEOUT_MS = 1600;
    var idleCat1CompactMirrorElement = null;
    var idleCat1CompactMirrorTimer = 0;
    var idleCat1CompactMirrorLastDetail = null;

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

    I.handleDesktopCompactLayoutChange = function handleDesktopCompactLayoutChange(layout) {
        I.compactSurfaceDesktopDragActive = !!(layout && layout.dragging);
        var nextAnchorSnapshot = getCompactDesktopLayoutAnchorSnapshot(layout);
        var baseAnchorChanged = false;
        if (!nextAnchorSnapshot) {
            baseAnchorChanged = !!compactDesktopSurfaceAnchorSnapshot || !layout;
            compactDesktopSurfaceAnchorSnapshot = '';
        } else if (nextAnchorSnapshot !== compactDesktopSurfaceAnchorSnapshot) {
            baseAnchorChanged = true;
            compactDesktopSurfaceAnchorSnapshot = nextAnchorSnapshot;
        }
        if (baseAnchorChanged && !I.compactSurfaceDesktopResizeActive) {
            I.compactSurfaceAnchorLocked = false;
            I.compactSurfaceAnchorSnapshot = '';
        }
        // 桌面侧布局变化（窗口移动/跨屏等）：立即唤醒短 settle，避免静止态停帧后漏掉新 anchor。
        I.scheduleCompactMinimizeBallTracking();
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

    I.getElectronCompactLayoutOverride = function getElectronCompactLayoutOverride() {
        if (!I.isElectronChatWindow()) return null;
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

    I.getMobileMaxHeight = function getMobileMaxHeight() {
        return Math.max(I.MOBILE_MIN_HEIGHT, Math.floor(window.innerHeight * MOBILE_MAX_HEIGHT_RATIO));
    }

    I.$ = function $(id) {
        return document.getElementById(id);
    }

    I.isElectronChatWindow = function isElectronChatWindow() {
        // chat.html 用 <body class="electron-chat-window">；Electron 独立聊天窗口。
        // 本 PR 的所有移动端改动都必须对它无感，作为显式隔离在全局 touch 处理里用来短路。
        return !!(document.body && document.body.classList.contains('electron-chat-window'));
    }

    I.isElectronLinuxRuntime = function isElectronLinuxRuntime() {
        var runtime = window.__NEKO_DESKTOP_RUNTIME__ || {};
        return !!(
            I.isElectronChatWindow()
            && (
                runtime.isLinux ||
                runtime.isLinuxX11 ||
                runtime.platform === 'linux'
            )
        );
    }

    I.isMobileWidth = function isMobileWidth() {
        // chat.html 是 Electron 独立窗口，始终按 PC 行为处理（即使用户把窗口拖窄到 <768px），
        // 通过 <body class="electron-chat-window"> 从"手机端布局"中排除。
        if (I.isElectronChatWindow()) {
            return false;
        }
        // index.html 的 Electron Pet 窗口同理：永不进入手机模式（黑背景 + 窄布局）。
        // 标记 __LANLAN_IS_ELECTRON_PET__ 由 index.html 头部脚本同步注入。
        if (window.__LANLAN_IS_ELECTRON_PET__) {
            return false;
        }
        return window.innerWidth <= 768;
    }

    I.isCompactHomeMinimizeBallEnabled = function isCompactHomeMinimizeBallEnabled() {
        var overlay = I.getOverlay();
        return !!(
            I.isHomeCompactSurfaceRoute()
            && overlay
            && !overlay.hidden
            && !I.minimized
        );
    }

    I.isElectronCompactExternalBallEnabled = function isElectronCompactExternalBallEnabled() {
        return !!(I.isElectronChatWindow() && window.__nekoDesktopCompactExternalBall);
    }

    I.isHomeCompactMinimizeBallRoute = function isHomeCompactMinimizeBallRoute() {
        var overlay = I.getOverlay();
        var body = document.body;
        return !!(
            body
            && body.classList.contains('subtitle-web-host')
            && overlay
            && !overlay.hidden
        );
    }

    I.getCompactMinimizeBallAvatarBounds = function getCompactMinimizeBallAvatarBounds() {
        if (I.isElectronChatWindow()) {
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
        var left = normalized.left - I.MINIMIZED_SIZE - COMPACT_MINIMIZE_BALL_AVATAR_GAP;
        var top = normalized.top + normalized.height * COMPACT_MINIMIZE_BALL_AVATAR_VERTICAL_RATIO - I.MINIMIZED_SIZE / 2 + I.MINIMIZED_DOWN_OFFSET;
        var maxLeft = Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, window.innerWidth - I.MINIMIZED_SIZE - COMPACT_MINIMIZE_BALL_VIEWPORT_PAD);
        var maxTop = Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, window.innerHeight - I.MINIMIZED_SIZE - COMPACT_MINIMIZE_BALL_VIEWPORT_PAD);
        return {
            width: I.MINIMIZED_SIZE,
            height: I.MINIMIZED_SIZE,
            left: Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, Math.min(Math.round(left), maxLeft)),
            top: Math.max(COMPACT_MINIMIZE_BALL_VIEWPORT_PAD, Math.min(Math.round(top), maxTop))
        };
    }

    function getCompactMinimizeBallTarget() {
        if (!I.isHomeCompactMinimizeBallRoute()) {
            return null;
        }
        if (I.isElectronCompactExternalBallEnabled()) {
            return null;
        }

        var avatarBounds = I.getCompactMinimizeBallAvatarBounds();
        var avatarPlacement = getCompactMinimizeBallPlacement(avatarBounds);
        if (avatarPlacement) {
            window.__nekoCompactMinimizeBallFallbackActive = false;
            return avatarPlacement;
        }

        window.__nekoCompactMinimizeBallFallbackActive = true;
        return {
            width: I.MINIMIZED_SIZE,
            height: I.MINIMIZED_SIZE,
            left: COMPACT_MINIMIZE_BALL_VIEWPORT_PAD,
            top: Math.max(
                COMPACT_MINIMIZE_BALL_VIEWPORT_PAD,
                window.innerHeight - I.MINIMIZED_SIZE - 34
            )
        };
    }

    I.shouldDelayCompactSurfaceOpenForModel = function shouldDelayCompactSurfaceOpenForModel() {
        return false;
    }

    I.getCompactSurfaceMobileWidthBounds = function getCompactSurfaceMobileWidthBounds() {
        var viewportWidth = Math.max(1, window.innerWidth || 0);
        var viewportMax = Math.max(
            1,
            Math.min(COMPACT_SURFACE_RESIZE_MAX_WIDTH, viewportWidth - COMPACT_SURFACE_MOBILE_VIEWPORT_GUTTER)
        );
        var minWidth = Math.min(COMPACT_SURFACE_MOBILE_MIN_WIDTH, viewportMax);
        return {
            minWidth: Math.round(minWidth),
            maxWidth: Math.round(Math.max(minWidth, viewportMax))
        };
    }

    I.getCompactSurfaceResizeMaxWidth = function getCompactSurfaceResizeMaxWidth() {
        if (I.isMobileWidth()) {
            return I.getCompactSurfaceMobileWidthBounds().maxWidth;
        }
        return Math.max(
            I.COMPACT_SURFACE_MAX_WIDTH,
            Math.min(COMPACT_SURFACE_RESIZE_MAX_WIDTH, window.innerWidth - (I.COMPACT_SURFACE_VIEWPORT_PAD_X * 2))
        );
    }

    I.getCompactSurfaceMetrics = function getCompactSurfaceMetrics() {
        var shell = I.getShell();
        var rect = I.getCompactSurfaceBaseRect() || (shell ? I.normalizeCompactDomRect(shell.getBoundingClientRect()) : null);
        var mobileWidthBounds = I.isMobileWidth() ? I.getCompactSurfaceMobileWidthBounds() : null;
        var defaultWidth = I.isMobileWidth()
            ? mobileWidthBounds.maxWidth
            : Math.min(I.COMPACT_SURFACE_MAX_WIDTH, Math.max(280, window.innerWidth - (I.COMPACT_SURFACE_VIEWPORT_PAD_X * 2)));
        var measuredWidth = rect && rect.width > 0 ? rect.width : 0;
        var storedWidth = loadCompactSurfaceStoredWidth();
        // 有用户拖拽记忆（stored）时以它为准——桌面端允许小于默认 430（仅 clamp 到 [桌面最短, resize 上限]），
        // 否则 applyCompactSurfacePosition 等重算会用 max(default,…) 把拖窄后的宽度顶回 430。
        // 无 stored 时才回退默认宽度（首次/重置仍为 430，不改默认）。
        var width;
        if (I.isMobileWidth() && storedWidth) {
            width = storedWidth;
        } else if (storedWidth && storedWidth > 0) {
            // 桌面端有拖拽记忆：以 stored 为准，可小于默认 430（仅 clamp 到 [桌面最短, resize 上限]）。
            width = Math.round(Math.min(
                Math.max(storedWidth, I.COMPACT_SURFACE_DESKTOP_MIN_WIDTH),
                I.getCompactSurfaceResizeMaxWidth()
            ));
        } else {
            width = Math.round(Math.min(
                Math.max(defaultWidth, measuredWidth),
                I.getCompactSurfaceResizeMaxWidth()
            ));
        }
        var height = rect && rect.height > 0 ? rect.height : I.COMPACT_SURFACE_DEFAULT_HEIGHT;
        return {
            width: width,
            height: height
        };
    }

    I.clampCompactSurfacePosition = function clampCompactSurfacePosition(left, top, metrics) {
        var width = metrics.width || I.COMPACT_SURFACE_MAX_WIDTH;
        var height = metrics.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT;
        var layoutOverride = I.getElectronCompactLayoutOverride();
        if (layoutOverride && layoutOverride.windowBounds && layoutOverride.workArea) {
            var windowBounds = layoutOverride.windowBounds;
            var workArea = layoutOverride.workArea;
            var screenLeft = windowBounds.x + left;
            var screenTop = windowBounds.y + top;
            var screenMinLeft = workArea.left + I.COMPACT_SURFACE_VIEWPORT_PAD_X;
            var screenMaxLeft = Math.max(screenMinLeft, workArea.right - width - I.COMPACT_SURFACE_VIEWPORT_PAD_X);
            var screenMinTop = workArea.top + I.COMPACT_SURFACE_VIEWPORT_PAD_TOP;
            var screenMaxTop = Math.max(screenMinTop, workArea.bottom - height - I.COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM);
            return {
                left: Math.max(screenMinLeft, Math.min(screenLeft, screenMaxLeft)) - windowBounds.x,
                top: Math.max(screenMinTop, Math.min(screenTop, screenMaxTop)) - windowBounds.y
            };
        }
        var minLeft = I.isMobileWidth() ? 8 : I.COMPACT_SURFACE_VIEWPORT_PAD_X;
        var maxLeft = Math.max(minLeft, window.innerWidth - width - minLeft);
        var maxTop = Math.max(
            I.COMPACT_SURFACE_VIEWPORT_PAD_TOP,
            window.innerHeight - height - I.COMPACT_SURFACE_VIEWPORT_PAD_BOTTOM
        );
        return {
            left: Math.max(minLeft, Math.min(left, maxLeft)),
            top: Math.max(I.COMPACT_SURFACE_VIEWPORT_PAD_TOP, Math.min(top, maxTop))
        };
    }

    I.loadCompactSurfacePosition = function loadCompactSurfacePosition(metrics) {
        try {
            var raw = window.localStorage.getItem(COMPACT_SURFACE_POSITION_STORAGE_KEY);
            if (!raw) return null;
            var parsed = JSON.parse(raw);
            var left = Number(parsed && parsed.left);
            var top = Number(parsed && parsed.top);
            if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
            return I.clampCompactSurfacePosition(left, top, metrics);
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
            var maxWidth = I.getCompactSurfaceResizeMaxWidth();
            var minWidth = I.isMobileWidth()
                ? I.getCompactSurfaceMobileWidthBounds().minWidth
                : I.COMPACT_SURFACE_DESKTOP_MIN_WIDTH;
            return Math.round(Math.max(minWidth, Math.min(width, maxWidth)));
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

    I.saveCompactSurfaceWidth = function saveCompactSurfaceWidth(width) {
        try {
            var raw = window.localStorage.getItem(COMPACT_SURFACE_POSITION_STORAGE_KEY);
            var payload = raw ? JSON.parse(raw) : {};
            if (!payload || typeof payload !== 'object') payload = {};
            payload.width = Math.round(Number(width));
            window.localStorage.setItem(COMPACT_SURFACE_POSITION_STORAGE_KEY, JSON.stringify(payload));
        } catch (_) {}
    }

    I.getCompactSurfaceDesktopWindowBounds = function getCompactSurfaceDesktopWindowBounds() {
        var layoutOverride = I.getElectronCompactLayoutOverride();
        var windowBounds = layoutOverride && layoutOverride.windowBounds;
        if (!windowBounds) return null;
        var x = Number(windowBounds.x);
        var y = Number(windowBounds.y);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        return windowBounds;
    }

    I.getCompactSurfaceResizeScreenRect = function getCompactSurfaceResizeScreenRect(rect) {
        if (!rect) return null;
        if (I.compactSurfaceResizeSession) {
            var screenLeft = I.compactSurfaceResizeSession.side === 'left'
                ? I.compactSurfaceResizeSession.anchorRightScreen - rect.width
                : I.compactSurfaceResizeSession.anchorLeftScreen;
            return {
                left: Math.round(screenLeft),
                top: Math.round(I.compactSurfaceResizeSession.anchorTopScreen),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            };
        }
        var windowBounds = I.getCompactSurfaceDesktopWindowBounds();
        if (!windowBounds) return null;
        return {
            left: Math.round(Number(windowBounds.x) + rect.left),
            top: Math.round(Number(windowBounds.y) + rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
        };
    }

    function getIdleCat1CompactMirrorElement() {
        if (idleCat1CompactMirrorElement && idleCat1CompactMirrorElement.isConnected) {
            return idleCat1CompactMirrorElement;
        }
        var host = document.body;
        if (!host) return null;
        var element = document.createElement('div');
        element.id = 'neko-idle-cat1-compact-mirror';
        element.className = 'neko-idle-cat1-compact-mirror';
        element.setAttribute('data-compact-geometry-owner', 'surface');
        element.setAttribute('data-compact-geometry-item', 'cat1Mirror');
        element.setAttribute('aria-hidden', 'true');
        element.hidden = true;
        var image = document.createElement('img');
        image.className = 'neko-idle-cat1-compact-mirror-art';
        image.alt = '';
        image.draggable = false;
        element.appendChild(image);
        host.appendChild(element);
        idleCat1CompactMirrorElement = element;
        return element;
    }

    function clearIdleCat1CompactMirrorTimer() {
        if (!idleCat1CompactMirrorTimer) return;
        window.clearTimeout(idleCat1CompactMirrorTimer);
        idleCat1CompactMirrorTimer = 0;
    }

    I.hideIdleCat1CompactMirror = function hideIdleCat1CompactMirror(reason) {
        clearIdleCat1CompactMirrorTimer();
        var element = idleCat1CompactMirrorElement;
        if (element) {
            element.hidden = true;
            element.removeAttribute('data-active');
            element.removeAttribute('data-neko-cat1-wide-art');
            element.style.removeProperty('left');
            element.style.removeProperty('top');
            element.style.removeProperty('width');
            element.style.removeProperty('height');
        }
        idleCat1CompactMirrorLastDetail = null;
        I.scheduleCompactMinimizeBallTracking();
        I.syncCompactInteractionGeometry();
    }

    function getIdleCat1CompactMirrorWindowBounds() {
        var windowBounds = I.getCompactSurfaceDesktopWindowBounds();
        if (windowBounds) return windowBounds;
        var x = Number(window.screenX);
        var y = Number(window.screenY);
        return {
            x: Number.isFinite(x) ? x : 0,
            y: Number.isFinite(y) ? y : 0
        };
    }

    function getIdleCat1CompactMirrorPageRect(detail) {
        var surface = normalizeCompactDesktopRect(detail && detail.surfaceScreenRect);
        if (!surface) return null;
        var catRect = detail && detail.catRect ? detail.catRect : null;
        var catWidth = Math.round(Number(catRect && catRect.width) || 112);
        var catHeight = Math.round(Number(catRect && catRect.height) || 112);
        if (catWidth <= 0 || catHeight <= 0) return null;
        var windowBounds = getIdleCat1CompactMirrorWindowBounds();
        var surfaceLeft = surface.left - (Number(windowBounds.x) || 0);
        var surfaceTop = surface.top - (Number(windowBounds.y) || 0);
        var sidePadding = Math.max(0, Number(detail && detail.sidePaddingPx) || 12);
        var capInset = Math.max(sidePadding, surface.height / 2 + sidePadding);
        var edgePadding = Math.min(capInset, Math.max(0, surface.width / 2));
        var minCenterX = surfaceLeft + edgePadding;
        var maxCenterX = surfaceLeft + surface.width - edgePadding;
        var ratio = Number(detail && detail.anchorRatio);
        if (!Number.isFinite(ratio)) ratio = 0.5;
        ratio = Math.max(0, Math.min(1, ratio));
        var centerX = maxCenterX >= minCenterX
            ? minCenterX + (maxCenterX - minCenterX) * ratio
            : surfaceLeft + surface.width / 2;
        var overlap = Number(detail && detail.overlapPx);
        if (!Number.isFinite(overlap)) overlap = 28;
        return {
            left: Math.round(centerX - catWidth / 2),
            top: Math.round(surfaceTop - catHeight + overlap),
            width: catWidth,
            height: catHeight
        };
    }

    function showIdleCat1CompactMirror(detail) {
        var element = getIdleCat1CompactMirrorElement();
        var rect = getIdleCat1CompactMirrorPageRect(detail);
        if (!element || !rect) {
            I.hideIdleCat1CompactMirror('invalid');
            return;
        }
        idleCat1CompactMirrorLastDetail = Object.assign({}, detail || {});
        var image = element.querySelector('.neko-idle-cat1-compact-mirror-art');
        if (image) {
            var src = detail && detail.assetUrl ? String(detail.assetUrl) : '/static/assets/neko-idle/cat-idle-cat1.gif';
            if (image.getAttribute('src') !== src) image.setAttribute('src', src);
            if (src.indexOf('/static/assets/neko-idle/cat-idle-cat-play-1.gif') !== -1) {
                element.setAttribute('data-neko-cat1-wide-art', 'true');
            } else {
                element.removeAttribute('data-neko-cat1-wide-art');
            }
            image.style.transform = detail && detail.facingRight ? 'scaleX(-1)' : 'scaleX(1)';
        }
        element.style.left = rect.left + 'px';
        element.style.top = rect.top + 'px';
        element.style.width = rect.width + 'px';
        element.style.height = rect.height + 'px';
        element.hidden = false;
        element.setAttribute('data-active', 'true');
        clearIdleCat1CompactMirrorTimer();
        idleCat1CompactMirrorTimer = window.setTimeout(function () {
            I.hideIdleCat1CompactMirror('timeout');
        }, IDLE_CAT1_COMPACT_MIRROR_TIMEOUT_MS);
        I.scheduleCompactMinimizeBallTracking();
        I.syncCompactInteractionGeometry();
    }

    I.refreshIdleCat1CompactMirrorPosition = function refreshIdleCat1CompactMirrorPosition() {
        var element = idleCat1CompactMirrorElement;
        if (!element || element.hidden || !idleCat1CompactMirrorLastDetail) return;
        var rect = getIdleCat1CompactMirrorPageRect(idleCat1CompactMirrorLastDetail);
        if (!rect) return;
        element.style.left = rect.left + 'px';
        element.style.top = rect.top + 'px';
        element.style.width = rect.width + 'px';
        element.style.height = rect.height + 'px';
        I.syncCompactInteractionGeometry();
    }

    function shouldIgnoreIdleCat1CompactMirrorState(detail) {
        return !!(
            window.__LANLAN_IS_ELECTRON_PET__
            && detail
            && detail.via === 'local'
            && detail.source === 'pet-window'
        );
    }

    I.handleIdleCat1CompactMirrorState = function handleIdleCat1CompactMirrorState(event) {
        var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (shouldIgnoreIdleCat1CompactMirrorState(detail)) return;
        if (!detail || !detail.active) {
            I.hideIdleCat1CompactMirror(detail && detail.reason ? detail.reason : 'inactive');
            return;
        }
        showIdleCat1CompactMirror(detail);
    }

    function getIdleCat1PlayYarnReleaseTargetRect(detail) {
        if (!detail || typeof detail !== 'object') return null;
        var screenTarget = normalizeCompactDesktopRect(detail.targetScreenRect);
        if (screenTarget && !I.isElectronChatWindow()) {
            var screenX = Number.isFinite(Number(window.screenX)) ? Number(window.screenX) : Number(window.screenLeft);
            var screenY = Number.isFinite(Number(window.screenY)) ? Number(window.screenY) : Number(window.screenTop);
            if (Number.isFinite(screenX) && Number.isFinite(screenY)) {
                return normalizeCompactDesktopRect({
                    left: screenTarget.left - screenX,
                    top: screenTarget.top - screenY,
                    width: screenTarget.width,
                    height: screenTarget.height
                });
            }
        }
        return normalizeCompactDesktopRect(detail.targetRect);
    }

    function applyIdleCat1PlayYarnRelease(detail) {
        if (!detail || !detail.releaseDrag) return;
        if (I.dragState) {
            I.stopDrag({ suppressClick: true });
        }
        if (I.isElectronChatWindow()) return;
        var shell = I.getShell();
        if (!shell || !shell.classList || !shell.classList.contains('is-minimized')) return;
        var target = getIdleCat1PlayYarnReleaseTargetRect(detail);
        if (!target) return;
        var shellRect = shell.getBoundingClientRect();
        var width = shellRect && shellRect.width > 0 ? shellRect.width : I.MINIMIZED_SIZE;
        var height = shellRect && shellRect.height > 0 ? shellRect.height : I.MINIMIZED_SIZE;
        I.applyPosition(
            target.left + target.width / 2 - width / 2,
            target.top + target.height / 2 - height / 2
        );
        I.syncCompactInteractionGeometry();
    }

    I.handleIdleCat1PlayYarnVisibility = function handleIdleCat1PlayYarnVisibility(event) {
        var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        var hidden = !!(detail && detail.hidden);
        var shell = I.getShell();
        if (shell && shell.classList) {
            if (hidden && shell.classList.contains('is-minimized')) {
                shell.setAttribute('data-neko-cat1-play-hidden', 'true');
                I.syncCompactInteractionGeometry();
            } else if (!hidden) {
                shell.removeAttribute('data-neko-cat1-play-hidden');
                I.syncCompactInteractionGeometry();
            }
        }
        if (!hidden) {
            applyIdleCat1PlayYarnRelease(detail);
        }
        var bridge = window.nekoChatWindow;
        if (bridge && typeof bridge.setCompactChatBallTemporarilyHidden === 'function') {
            try {
                bridge.setCompactChatBallTemporarilyHidden(hidden, {
                    releaseDrag: !!(detail && detail.releaseDrag),
                    targetScreenRect: detail && detail.targetScreenRect ? detail.targetScreenRect : null,
                    releaseReason: detail && detail.releaseReason ? detail.releaseReason : ''
                });
            } catch (_) {}
        }
    }

    I.dispatchCompactSurfaceLayoutChange = function dispatchCompactSurfaceLayoutChange(rect) {
        var detail = rect || null;
        if (detail && I.isElectronChatWindow()) {
            detail = Object.assign({}, detail, {
                screenRect: I.getCompactSurfaceResizeScreenRect(detail),
                resizeActive: !!I.compactSurfaceResizeSession
            });
        }
        if (detail) {
            detail = Object.assign({}, detail, {
                dragging: !!(I.dragState && I.dragState.compactSurface) || I.compactSurfaceDesktopDragActive
            });
        }
        window.dispatchEvent(new CustomEvent('neko:compact-surface-layout-change', {
            detail: detail
        }));
    }

    I.applyCompactSurfaceRect = function applyCompactSurfaceRect(left, top, width, height, options) {
        var shell = I.getShell();
        if (!shell) return null;

        var safeWidth = Number(width);
        var safeHeight = Number(height);
        if (!Number.isFinite(safeWidth) || safeWidth <= 0) {
            safeWidth = I.COMPACT_SURFACE_MAX_WIDTH;
        }
        if (!Number.isFinite(safeHeight) || safeHeight <= 0) {
            safeHeight = I.COMPACT_SURFACE_DEFAULT_HEIGHT;
        }

        var clamped = I.clampCompactSurfacePosition(Number(left) || 0, Number(top) || 0, {
            width: safeWidth,
            height: safeHeight
        });
        var rect = {
            left: Math.round(clamped.left),
            top: Math.round(clamped.top),
            width: Math.round(safeWidth),
            height: Math.round(safeHeight)
        };

        I.compactSurfaceAnchorSnapshot = [
            rect.left,
            rect.top,
            rect.width,
            rect.height
        ].join(':');
        I.compactSurfaceAnchorLocked = true;
        shell.style.setProperty('--compact-surface-left', rect.left + 'px');
        shell.style.setProperty('--compact-surface-top', rect.top + 'px');
        shell.style.setProperty('--compact-surface-width', rect.width + 'px');
        shell.style.setProperty('--compact-surface-height', rect.height + 'px');
        document.documentElement.style.setProperty('--compact-surface-left', rect.left + 'px');
        document.documentElement.style.setProperty('--compact-surface-top', rect.top + 'px');
        document.documentElement.style.setProperty('--compact-surface-width', rect.width + 'px');
        document.documentElement.style.setProperty('--compact-surface-height', rect.height + 'px');
        if (I.isElectronChatWindow()) {
            shell.style.setProperty('--desktop-compact-surface-left', rect.left + 'px');
            shell.style.setProperty('--desktop-compact-surface-top', rect.top + 'px');
            shell.style.setProperty('--desktop-compact-surface-width', rect.width + 'px');
            shell.style.setProperty('--desktop-compact-surface-height', rect.height + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-left', rect.left + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-top', rect.top + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-width', rect.width + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-height', rect.height + 'px');
        }
        shell.setAttribute('data-compact-surface-anchor-ready', 'true');
        shell.style.transform = 'none';
        if (options && options.persist && !I.isElectronChatWindow()) {
            saveCompactSurfacePosition(rect.left, rect.top, rect.width);
        }
        I.dispatchCompactSurfaceLayoutChange(rect);
        I.syncCompactInteractionGeometry();
        return rect;
    }

    I.seedCompactSurfaceAnchorForRender = function seedCompactSurfaceAnchorForRender() {
        var shell = I.getShell();
        if (!shell || I.getCurrentChatSurfaceMode() !== 'compact') return;
        if (I.compactSurfaceAnchorLocked) return;
        if (I.compactSurfaceDesktopResizeActive || I.compactSurfaceResizeSession) return;
        if ((I.dragState && I.dragState.compactSurface) || I.compactSurfaceDesktopDragActive) return;
        if (shell.hasAttribute('data-compact-surface-anchor-ready')) return;
        var layoutOverride = I.getElectronCompactLayoutOverride();
        var target = I.getCompactSurfaceTarget(layoutOverride);
        if (!target) {
            shell.removeAttribute('data-compact-surface-anchor-ready');
            return;
        }
        var left = Math.round(target.left);
        var top = Math.round(target.top);
        var width = Math.round(target.width);
        var height = Math.round(target.height || I.COMPACT_SURFACE_DEFAULT_HEIGHT);
        shell.style.setProperty('--compact-surface-left', left + 'px');
        shell.style.setProperty('--compact-surface-top', top + 'px');
        shell.style.setProperty('--compact-surface-width', width + 'px');
        shell.style.setProperty('--compact-surface-height', height + 'px');
        document.documentElement.style.setProperty('--compact-surface-left', left + 'px');
        document.documentElement.style.setProperty('--compact-surface-top', top + 'px');
        document.documentElement.style.setProperty('--compact-surface-width', width + 'px');
        document.documentElement.style.setProperty('--compact-surface-height', height + 'px');
        if (I.isElectronChatWindow() || (layoutOverride && layoutOverride.surface)) {
            shell.style.setProperty('--desktop-compact-surface-left', left + 'px');
            shell.style.setProperty('--desktop-compact-surface-top', top + 'px');
            shell.style.setProperty('--desktop-compact-surface-width', width + 'px');
            shell.style.setProperty('--desktop-compact-surface-height', height + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-left', left + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-top', top + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-width', width + 'px');
            document.documentElement.style.setProperty('--desktop-compact-surface-height', height + 'px');
        }
        if (layoutOverride && layoutOverride.workArea) {
            document.documentElement.style.setProperty('--compact-desktop-workarea-width', Math.round(layoutOverride.workArea.width) + 'px');
            document.documentElement.style.setProperty('--compact-desktop-workarea-height', Math.round(layoutOverride.workArea.height) + 'px');
        }
        shell.setAttribute('data-compact-surface-anchor-ready', 'true');
    }

    I.getCurrentCompactSurfaceRect = function getCurrentCompactSurfaceRect() {
        var shell = I.getShell();
        if (!shell) return null;
        var domRect = I.normalizeCompactDomRect(shell.getBoundingClientRect());
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

    I.getCompactSurfaceDesktopWindowX = function getCompactSurfaceDesktopWindowX() {
        var windowBounds = I.getCompactSurfaceDesktopWindowBounds();
        var x = Number(windowBounds && windowBounds.x);
        return Number.isFinite(x) ? x : 0;
    }

})();
