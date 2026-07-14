/**
 * Avatar UI Buttons Mixin - 统一的浮动按钮系统
 * 为 Live2D/VRM/MMD 提供通用的按钮逻辑
 *
 * 使用方式：
 *   AvatarButtonMixin.apply(XXXManager.prototype, 'xxx', { options });
 */

// 浮动按钮入场动画（错位级联滑入 + 淡入；从上往下）。
// 退场不做动画 —— 直接 display:none，因为浏览器在 microtask 拦截前已 commit
// display:none 到下一帧渲染流程，可靠的退场需要改大量调用点，权衡之下放弃。
function isNekoYuiGuideFloatingToolbarSuppressed() {
    return !!(
        typeof window !== 'undefined'
        && window.nekoYuiGuideFloatingToolbarSuppressed === true
    );
}

const NEKO_YUI_GUIDE_LOCK_SPOTLIGHT_DEFAULT_BOTTOM_PX = 112;

function isNekoYuiGuideLockSpotlightSafeAreaActive() {
    return !!(
        typeof window !== 'undefined'
        && window.nekoYuiGuideLockSpotlightSafeAreaActive === true
    );
}

function getNekoYuiGuideLockSpotlightBottomPx() {
    if (typeof window === 'undefined') {
        return NEKO_YUI_GUIDE_LOCK_SPOTLIGHT_DEFAULT_BOTTOM_PX;
    }
    const configured = Number(window.nekoYuiGuideLockSpotlightSafeAreaBottomPx);
    return Number.isFinite(configured) && configured >= 0
        ? configured
        : NEKO_YUI_GUIDE_LOCK_SPOTLIGHT_DEFAULT_BOTTOM_PX;
}

function getNekoYuiGuideLockIconMaxTop(defaultMaxTop, iconSize) {
    const fallbackMaxTop = Number(defaultMaxTop);
    const normalizedDefault = Number.isFinite(fallbackMaxTop) ? fallbackMaxTop : 0;
    if (!isNekoYuiGuideLockSpotlightSafeAreaActive() || typeof window === 'undefined') {
        return normalizedDefault;
    }
    const viewportHeight = Number(window.innerHeight);
    if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) {
        return normalizedDefault;
    }
    const normalizedIconSize = Number.isFinite(Number(iconSize)) && Number(iconSize) > 0
        ? Number(iconSize)
        : 40;
    const safeMaxTop = Math.max(
        0,
        viewportHeight - normalizedIconSize - getNekoYuiGuideLockSpotlightBottomPx()
    );
    return Math.min(normalizedDefault, safeMaxTop);
}

if (typeof window !== 'undefined') {
    window.isNekoYuiGuideFloatingToolbarSuppressed = isNekoYuiGuideFloatingToolbarSuppressed;
    window.isNekoYuiGuideLockSpotlightSafeAreaActive = isNekoYuiGuideLockSpotlightSafeAreaActive;
    window.getNekoYuiGuideLockIconMaxTop = getNekoYuiGuideLockIconMaxTop;
}

function _ensureFloatingButtonsAnimationStyles() {
    if (document.getElementById('neko-floating-buttons-animation-styles')) return;
    const style = document.createElement('style');
    style.id = 'neko-floating-buttons-animation-styles';
    // 入场延迟梯度：第一个子元素 0ms（顶部最先到达，往下级联）
    let staggerCss = '';
    for (let i = 1; i <= 8; i++) {
        const enterDelay = (i - 1) * 70;
        staggerCss += `.neko-floating-buttons-animating[data-anim-state="entering"] > *:nth-child(${i}) { animation-delay: ${enterDelay}ms; }\n`;
    }
    staggerCss += `.neko-floating-buttons-animating[data-anim-state="entering"] > *:nth-child(n+9) { animation-delay: 560ms; }\n`;

    style.textContent = `
        @keyframes nekoFloatingBtnIn {
            0%   { opacity: 0; transform: translate3d(0, -16px, 0) scale(0.82); }
            60%  { opacity: 1; transform: translate3d(0, 2px, 0)  scale(1.04); }
            100% { opacity: 1; transform: translate3d(0, 0, 0)    scale(1);    }
        }
        .neko-floating-buttons-animating > * {
            will-change: opacity, transform;
        }
        .neko-floating-buttons-animating[data-anim-state="entering"] > * {
            animation: nekoFloatingBtnIn 0.42s cubic-bezier(0.22, 1.0, 0.36, 1) both;
        }
        @media (prefers-reduced-motion: reduce) {
            .neko-floating-buttons-animating[data-anim-state="entering"] > * {
                animation-duration: 0.01ms;
            }
        }
        ${staggerCss}
    `;
    document.head.appendChild(style);
}

function _cleanupFloatingButtonsEntrance(container) {
    if (!container) return;
    if (container._nekoEntranceTimer) {
        clearTimeout(container._nekoEntranceTimer);
        container._nekoEntranceTimer = null;
    }
    if (typeof container._nekoRestoreDisplayHooks === 'function') {
        try { container._nekoRestoreDisplayHooks(); } catch (_) {}
        container._nekoRestoreDisplayHooks = null;
    }
    container.classList.remove('neko-floating-buttons-animating');
    container.removeAttribute('data-anim-state');
    container._nekoPlayEntrance = null;
}

function _removeFloatingButtonsElement(el) {
    if (!el) return;
    if (el.matches && el.matches('[id$="-return-button-container"]')) {
        const returnButton = el.querySelector('.neko-idle-return-btn');
        if (returnButton) {
            _cancelNekoIdleCat1EatAction(returnButton, { restoreArt: false });
            _cancelNekoIdleCat1PlayAction(returnButton, { restoreArt: false });
            _finishNekoIdleReturnDragAction(returnButton, { restoreArt: false });
            _cancelNekoIdleCat1Journey(returnButton);
        }
    }
    _cleanupFloatingButtonsEntrance(el);
    if (el._nekoVisibilityObserver) {
        try { el._nekoVisibilityObserver.disconnect(); } catch (_) {}
        el._nekoVisibilityObserver = null;
    }
    el.remove();
}

function _setupFloatingButtonsEntranceHooks(container) {
    _ensureFloatingButtonsAnimationStyles();

    const styleDecl = container.style;

    const findDisplayDescriptor = () => {
        let proto = styleDecl;
        while (proto) {
            const descriptor = Object.getOwnPropertyDescriptor(proto, 'display');
            if (descriptor) return descriptor;
            proto = Object.getPrototypeOf(proto);
        }
        return null;
    };

    const displayDescriptor = findDisplayDescriptor();
    const originalSetProperty = styleDecl.setProperty;
    const originalRemoveProperty = styleDecl.removeProperty;
    const readDisplay = () => {
        if (displayDescriptor && displayDescriptor.get) {
            return displayDescriptor.get.call(styleDecl);
        }
        return styleDecl.getPropertyValue('display');
    };
    const writeDisplay = (value) => {
        if (displayDescriptor && displayDescriptor.set) {
            displayDescriptor.set.call(styleDecl, value);
        } else {
            originalSetProperty.call(styleDecl, 'display', value);
        }
    };

    const clearAnim = () => {
        if (container._nekoEntranceTimer) {
            clearTimeout(container._nekoEntranceTimer);
            container._nekoEntranceTimer = null;
        }
        container.classList.remove('neko-floating-buttons-animating');
        container.removeAttribute('data-anim-state');
    };

    const playEntrance = () => {
        if (!container.children.length) return;
        clearAnim();
        container.classList.add('neko-floating-buttons-animating');
        container.setAttribute('data-anim-state', 'entering');
        // 强制 reflow，确保 keyframes 重新触发
        void container.offsetWidth;
        const childCount = Math.min(container.children.length, 8);
        const totalMs = (childCount - 1) * 70 + 420 + 80;
        container._nekoEntranceTimer = setTimeout(() => {
            if (container.getAttribute('data-anim-state') === 'entering') {
                clearAnim();
            }
        }, totalMs);
    };

    const maybePlayAfterDisplayChange = (prev) => {
        const cur = readDisplay();
        if (cur === prev) return;
        if (cur !== 'none' && prev === 'none') {
            playEntrance();
        }
        lastDisplay = cur;
    };

    let lastDisplay = readDisplay() || 'none';

    try {
        Object.defineProperty(styleDecl, 'display', {
            configurable: true,
            enumerable: displayDescriptor ? displayDescriptor.enumerable : true,
            get: readDisplay,
            set: (value) => {
                const prev = readDisplay();
                writeDisplay(value);
                maybePlayAfterDisplayChange(prev);
            }
        });
    } catch (_) {
        container._nekoPlayEntrance = playEntrance;
        return;
    }

    styleDecl.setProperty = function(name, value, priority) {
        const isDisplay = String(name).toLowerCase() === 'display';
        const prev = isDisplay ? readDisplay() : null;
        const result = originalSetProperty.call(this, name, value, priority);
        if (isDisplay) maybePlayAfterDisplayChange(prev);
        return result;
    };
    styleDecl.removeProperty = function(name) {
        const isDisplay = String(name).toLowerCase() === 'display';
        const prev = isDisplay ? readDisplay() : null;
        const result = originalRemoveProperty.call(this, name);
        if (isDisplay) maybePlayAfterDisplayChange(prev);
        return result;
    };

    container._nekoPlayEntrance = playEntrance;
    container._nekoRestoreDisplayHooks = () => {
        try { delete styleDecl.display; } catch (_) {}
        styleDecl.setProperty = originalSetProperty;
        styleDecl.removeProperty = originalRemoveProperty;
    };
}

window._removeNekoFloatingButtonsElement = _removeFloatingButtonsElement;
window._cleanupNekoFloatingButtonsEntrance = _cleanupFloatingButtonsEntrance;

const _NEKO_IDLE_TIER_NONE = 'none';
const _NEKO_IDLE_TIER_CAT1 = 'cat1';
const _NEKO_IDLE_TIER_CAT2 = 'cat2';
const _NEKO_IDLE_TIER_CAT3 = 'cat3';
const _NEKO_IDLE_RETURN_BUTTON_SELECTOR = '#live2d-btn-return, #vrm-btn-return, #mmd-btn-return, #pngtuber-btn-return';
const _NEKO_GOODBYE_IDLE_APPEARANCE_CAT = 'cat';
const _NEKO_GOODBYE_IDLE_APPEARANCE_BALL = 'ball';
const _NEKO_GOODBYE_IDLE_APPEARANCE_ATTR = 'data-neko-goodbye-idle-appearance';
const _NEKO_IDLE_CAT_AUDIO_ENABLED_STORAGE_KEY = 'neko.idleCatAudio.enabled';
const _NEKO_IDLE_RETURN_TRANSITION_MS = 820;
const _NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS = 900;
const _NEKO_IDLE_RETURN_GIF_DURATION_CACHE = new Map();
const _NEKO_IDLE_RETURN_GIF_PLAYBACK_SOURCE_CACHE = new Map();
const _NEKO_IDLE_CAT1_SUBSTATE_IDLE = 'idle';
const _NEKO_IDLE_CAT1_SUBSTATE_WALKING = 'walking-to-chat';
const _NEKO_IDLE_CAT1_SUBSTATE_STRETCH = 'stretch-near-chat';
const _NEKO_IDLE_CAT1_CHAT_GAP_PX = -5;
const _NEKO_IDLE_CHAT_MINIMIZED_SIZE_PX = 51;
const _NEKO_IDLE_CAT1_MINIMIZED_RIGHT_TO_LEFT_APPROACH_PX = 35;
// GNOME Wayland 的自带毛球发布真实的 58px 可见区域。CAT1 与毛球素材仍使用
// 各自的透明画布，因此这一条路径按素材坐标计算接触点，再应用实机截图校准。
const _NEKO_IDLE_CAT1_NATIVE_YARN_ASSET_SIZE_PX = 116;
const _NEKO_IDLE_CAT1_NATIVE_YARN_VISIBLE_SIZE_PX = 58;
const _NEKO_IDLE_CAT1_NATIVE_YARN_BODY_LEFT_PX = 5;
const _NEKO_IDLE_CAT1_NATIVE_YARN_BODY_RIGHT_PX = 90;
const _NEKO_IDLE_CAT1_ASSET_SIZE_PX = 512;
const _NEKO_IDLE_CAT1_IDLE_VISIBLE_LEFT_PX = 89;
const _NEKO_IDLE_CAT1_IDLE_VISIBLE_RIGHT_PX = 394;
// 猫位于毛球左侧（朝右）时，截图与同时间 trace 显示待机和侧身素材均少走约 34px。
// 猫位于毛球右侧时容器终点保持不动，待机与侧身素材的 33px 校准由 CSS 按状态隔离。
const _NEKO_IDLE_CAT1_NATIVE_YARN_LEFT_SIDE_CONTACT_CORRECTION_PX = 34;
const _NEKO_IDLE_CAT1_NATIVE_YARN_VISUAL_ANCHOR_ATTR = 'data-neko-cat1-native-yarn-visual-anchor';
const _NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_ATTR = 'data-neko-cat1-native-yarn-side';
const _NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_LEFT = 'left';
const _NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_RIGHT = 'right';
const _NEKO_IDLE_CAT1_MINIMIZED_BACKWARD_RETREAT_TOLERANCE_PX = 2;
// 容器属性名：本次走路提交的接近侧（true=站毛球左侧/朝右，false=站毛球右侧/朝左）。
const _NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP = '__nekoIdleCat1WalkApproachLookRight';
const _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE = 'compact-top-edge';
const _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE = 'minimized-side';
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_OVERLAP_PX = 28;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX = 12;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_FOLLOW_DISTANCE_PX = 200;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_REARM_DISTANCE_PX = 100;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_STICK_MAX_SPEED_PX_PER_SEC = 1100;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_STICK_MAX_STEP_PX = 210;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_FAST_MOVE_COUNT = 3;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_PX = 52;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_ANIMATION_MS = 360;
const _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_COOLDOWN_MS = 900;
const _NEKO_IDLE_CAT1_COMPACT_SURFACE_SETTLE_SYNC_MS = 160;
const _NEKO_IDLE_CAT1_COMPACT_MIRROR_SETTLE_HIDE_DELAY_MS = 180;
const _NEKO_IDLE_CAT1_WALK_ENTER_DISTANCE_PX = 120;
const _NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX = 14;
const _NEKO_IDLE_CAT1_WALK_SPEED_PX_PER_SEC = 82;
const _NEKO_IDLE_CAT1_WALK_MAX_SPEED_RATE = 1.5;
const _NEKO_IDLE_CAT1_WALK_DISTANCE_INCREASE_THRESHOLD_PX = 6;
const _NEKO_IDLE_CAT1_WALK_DISTANCE_GROWTH_FOR_MAX_RATE_PX = 220;
const _NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX = 24;
const _NEKO_IDLE_CAT1_WALK_MIN_STEP_MS = 12;
const _NEKO_IDLE_CAT1_WALK_MAX_STEP_MS = 48;
const _NEKO_IDLE_CAT1_STRETCH_FINAL_HOLD_MS = 700;
const _NEKO_IDLE_CAT1_WALK_SHORT_DELAY_MIN_MS = 3 * 1000;
const _NEKO_IDLE_CAT1_WALK_SHORT_DELAY_MAX_MS = 18 * 1000;
const _NEKO_IDLE_CAT1_WALK_MEDIUM_DELAY_MIN_MS = 30 * 1000;
const _NEKO_IDLE_CAT1_WALK_MEDIUM_DELAY_MAX_MS = 90 * 1000;
const _NEKO_IDLE_CAT1_WALK_LONG_DELAY_MIN_MS = 2 * 60 * 1000;
const _NEKO_IDLE_CAT1_WALK_LONG_DELAY_MAX_MS = 5 * 60 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MIN_MS = 5 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MAX_MS = 90 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MEDIUM_DELAY_MIN_MS = 90 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MEDIUM_DELAY_MAX_MS = 3 * 60 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_LONG_DELAY_MIN_MS = 3 * 60 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_LONG_DELAY_MAX_MS = 5 * 60 * 1000;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DISTANCE_PX = 72;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX = 160;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX = 36;
const _NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC = 82;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DURATION_MS = 720;
const _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DURATION_MS = 2200;
const _NEKO_IDLE_CAT1_DESKTOP_PAIR_MOVE_SYNC_MIN_MS = 50;
const _NEKO_IDLE_CAT1_WALK_FINISH_PLAY_PROBABILITY = 0.25;
const _NEKO_IDLE_CAT1_PAIR_MOVE_PLAY_PROBABILITY = 0.05;
const _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS = 2500;
const _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS = 10 * 1000;
const _NEKO_IDLE_RETURN_DRAG_PENDING_CLASS = 'is-drag-action-pending';
const _NEKO_IDLE_RETURN_DRAG_ACTION_CLASS = 'is-drag-action';
const _NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR = 'data-neko-cat1-play-finishing';
const _NEKO_IDLE_CAT1_PLAY_YARN_RELEASE_SIZE_PX = 51;
const _NEKO_IDLE_CAT1_RAPID_DRAG_ASSET_URL = '/static/assets/neko-idle/cat-idle-cat-move-5.gif';
const _NEKO_IDLE_CAT1_RAPID_DRAG_SOUND_URL = '/static/assets/neko-idle/cat1-voice-funny.mp3';
const _NEKO_IDLE_CAT1_RAPID_DRAG_REACTION_MS = 5000;
const _NEKO_IDLE_CAT1_RAPID_DRAG_WINDOW_MS = 1100;
const _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_DISTANCE_PX = 28;
const _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SPAN_MS = 420;
const _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SUSTAINED_SPEED_PX_PER_SEC = 800;
const _NEKO_IDLE_CAT1_RAPID_DRAG_REQUIRED_REVERSALS = 6;
const _NEKO_IDLE_CAT1_RAPID_DRAG_REVERSE_DOT_THRESHOLD = 0;
const _NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO = 0.025;
const _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO = 0.4;
const _NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES = Object.freeze([
    'is-cat1-edge-peek-left',
    'is-cat1-edge-peek-right',
    'is-cat1-edge-peek-top',
    'is-cat1-edge-peek-bottom',
    'is-cat1-edge-peek-top-left',
    'is-cat1-edge-peek-top-right',
    'is-cat1-edge-peek-bottom-left',
    'is-cat1-edge-peek-bottom-right'
]);
const _NEKO_IDLE_RETURN_DRAG_ASSET_URLS_BY_TIER = Object.freeze({
    [_NEKO_IDLE_TIER_CAT1]: Object.freeze([
        '/static/assets/neko-idle/cat-idle-cat-move-1.gif',
        '/static/assets/neko-idle/cat-idle-cat-move-2.gif'
    ]),
    [_NEKO_IDLE_TIER_CAT2]: Object.freeze([
        '/static/assets/neko-idle/cat-idle-cat-move-2.gif',
        '/static/assets/neko-idle/cat-idle-cat-move-3.gif'
    ]),
    [_NEKO_IDLE_TIER_CAT3]: Object.freeze([
        '/static/assets/neko-idle/cat-idle-cat-move-3.gif',
        '/static/assets/neko-idle/cat-idle-cat-move-4.gif'
    ])
});
const _NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS = 'is-thought-bubble-active';
const _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS = 'is-thought-bubble-sleeping';
const _NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS = 'is-thought-bubble-popping';
const _NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL = '/static/assets/neko-idle/thought-items/cloud-thought-bubble.gif';
const _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_ASSET_URL = '/static/assets/neko-idle/thought-items/sleeping-zzz.gif';
const _NEKO_IDLE_THOUGHT_BUBBLE_POP_ASSET_URL = '/static/assets/neko-idle/thought-items/cloud-thought-bubble-pop.gif';
const _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS = Object.freeze([
    '/static/assets/neko-idle/thought-items/catnip-pouch.png',
    '/static/assets/neko-idle/thought-items/fish-cookie.png',
    '/static/assets/neko-idle/thought-items/toy-mouse.png'
]);
const _NEKO_IDLE_CAT1_QUESTION_MARK_ASSET_URL = '/static/assets/neko-idle/cat1-question-mark.png';
const _NEKO_IDLE_CAT1_QUESTION_MARK_VISIBLE_MS = 10 * 1000;
const _NEKO_IDLE_CAT1_QUESTION_MARK_KEY_SEQUENCE = Object.freeze([
    'ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown',
    'ArrowLeft', 'ArrowLeft', 'ArrowRight', 'ArrowRight',
    'KeyB', 'KeyA', 'KeyB', 'KeyA'
]);
const _NEKO_IDLE_CAT1_PLAYGROUND_AIR_ASSET_URL = '/static/assets/neko-idle/cat-idle-cat-move-2.gif';
const _NEKO_IDLE_CAT1_PLAYGROUND_GRAVITY_PX_PER_SECOND2 = 2600;
const _NEKO_IDLE_CAT1_PLAYGROUND_MAX_DELTA_MS = 50;
const _NEKO_IDLE_CAT1_PLAYGROUND_HORIZONTAL_DAMPING = 0.992;
const _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_DAMPING = 0.988;
const _NEKO_IDLE_CAT1_PLAYGROUND_WALL_RESTITUTION = 0.42;
const _NEKO_IDLE_CAT1_PLAYGROUND_BODY_RESTITUTION = 0.48;
const _NEKO_IDLE_CAT1_PLAYGROUND_BODY_PUSH_VELOCITY_PX_PER_SEC = 220;
const _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_BODY_MASS = 1;
const _NEKO_IDLE_CAT1_PLAYGROUND_MIN_BODY_MASS = 0.2;
const _NEKO_IDLE_CAT1_PLAYGROUND_MAX_BODY_MASS = 8;
const _NEKO_IDLE_CAT1_PLAYGROUND_CAT_BODY_MASS = 2;
const _NEKO_IDLE_CAT1_PLAYGROUND_YARN_BODY_MASS = 0.65;
const _NEKO_IDLE_CAT1_PLAYGROUND_QUESTION_BLOCK_BODY_MASS = 5;
const _NEKO_IDLE_CAT1_PLAYGROUND_QUESTION_BLOCK_CLICK_EVENT = 'neko:idle-cat1-playground-question-block-click';
const _NEKO_IDLE_CAT1_PLAYGROUND_PAIR_MOVE_SOURCE = 'cat1-playground-pair-move';
const _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_ANGULAR_DAMPING = 0.986;
const _NEKO_IDLE_CAT1_PLAYGROUND_DEFAULT_GROUND_ANGULAR_DAMPING = 0.88;
const _NEKO_IDLE_CAT1_PLAYGROUND_MAX_ANGULAR_VELOCITY_RAD_PER_SEC = 14;
const _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_STOP_RAD_PER_SEC = 0.02;
const _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_SPEED_RAD_PER_SEC = 8;
const _NEKO_IDLE_CAT1_PLAYGROUND_ROTATION_SETTLE_EPSILON_RAD = 0.01;
const _NEKO_IDLE_CAT1_PLAYGROUND_CAT_VISIBLE_INSET_RATIOS = Object.freeze({
    left: 112 / 512,
    top: 2 / 512,
    right: 97 / 512,
    bottom: 15 / 512
});
const _NEKO_IDLE_CAT1_PLAYGROUND_YARN_VISIBLE_INSET_RATIOS = Object.freeze({
    left: 35 / 963,
    top: 36 / 930,
    right: 36 / 963,
    bottom: 35 / 930
});
const _NEKO_IDLE_CAT1_PLAYGROUND_QUESTION_BLOCK_VISIBLE_INSET_RATIOS = Object.freeze({
    left: 80 / 960,
    top: 60 / 960,
    right: 80 / 960,
    bottom: 60 / 960
});
const _NEKO_IDLE_CAT1_PLAYGROUND_GROUND_STOP_VELOCITY_PX_PER_SEC = 3;
const _NEKO_IDLE_CAT1_PLAYGROUND_POINTER_SAMPLE_LIMIT = 5;
const _NEKO_IDLE_CAT1_PLAYGROUND_MIN_CLICK_DRAG_PX = 5;
const _NEKO_IDLE_CAT1_PLAYGROUND_YARN_TARGET_WAIT_MS = 900;
const _NEKO_IDLE_CAT1_PLAYGROUND_YARN_ASSET_URL = '/static/assets/neko-idle/chat-minimized-yarn-ball.png';
let _nekoIdleCat1PlaygroundViewportBottomPx = null;
let _nekoIdleCat1PlaygroundViewportBottomRefreshSeq = 0;
const _NEKO_IDLE_CAT1_EAT_ASSET_URL = '/static/assets/neko-idle/cat-idle-cat1-eat.gif';
const _NEKO_IDLE_CAT1_EAT_SOUND_URL = '/static/assets/neko-idle/cat1-voice-eat.mp3';
const _NEKO_IDLE_CAT1_EAT_SOUND_VOLUME = 0.12;
const _NEKO_IDLE_CAT1_EAT_SOUND_FALLBACK_MS = 5000;
const _NEKO_IDLE_CAT1_PLAY_ASSET_URL = '/static/assets/neko-idle/cat-idle-cat-play-1.gif';
const _NEKO_IDLE_CAT1_PLAY_SOUND_URL = '/static/assets/neko-idle/cat1-voice3.mp3';
const _NEKO_IDLE_CAT1_PLAY_SOUND_VOLUME = 0.10;
const _NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS = 5000;
const _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS = 8000;
const _NEKO_IDLE_THOUGHT_BUBBLE_POP_VISIBLE_MS = 540;
const _NEKO_IDLE_CAT1_LAYER_REQUEST_HEARTBEAT_MS = 250;
const _NEKO_IDLE_CAT1_LAYER_FOLLOW_REASSERT_MS = 80;
const _NEKO_IDLE_CAT1_LAYER_RELEASE_DELAY_MS = 2600;
const _NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS = 3 * 60 * 1000;
const _NEKO_IDLE_CAT1_AMBIENT_SOUND_VOLUME = 0.10;
const _NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME = 0.12;
const _NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS = 900;
const _NEKO_IDLE_RETURN_DEFAULT_Z_INDEX = '99999';
const _NEKO_IDLE_RETURN_COMPACT_SURFACE_Z_INDEX = '100050';
const _NEKO_IDLE_CAT1_AMBIENT_SOUND_URLS = Object.freeze([
    '/static/assets/neko-idle/cat1-voice1.mp3',
    '/static/assets/neko-idle/cat1-voice2.mp3',
    '/static/assets/neko-idle/cat1-voice3.mp3'
]);
const _NEKO_IDLE_CAT1_DRAG_SOUND_URL = '/static/assets/neko-idle/cat1-voice-click.mp3';
const _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS = 5 * 60 * 1000;
const _NEKO_IDLE_SLEEP_SOUND_VOLUME = 0.06;
const _NEKO_IDLE_SLEEP_SOUND_BY_TIER = Object.freeze({
    [_NEKO_IDLE_TIER_CAT2]: Object.freeze({
        srcs: Object.freeze([
            '/static/assets/neko-idle/cat2-sleep1.mp3',
            '/static/assets/neko-idle/cat2-sleep2.mp3'
        ]),
        volume: _NEKO_IDLE_SLEEP_SOUND_VOLUME
    }),
    [_NEKO_IDLE_TIER_CAT3]: Object.freeze({
        srcs: Object.freeze([
            '/static/assets/neko-idle/cat3-sleep1.mp3',
            '/static/assets/neko-idle/cat3-sleep2.mp3'
        ]),
        volume: _NEKO_IDLE_SLEEP_SOUND_VOLUME
    })
});
const _nekoIdleSleepSoundState = {
    tier: _NEKO_IDLE_TIER_NONE,
    timer: 0,
    token: 0,
    intervalStartedAt: 0,
    audio: null
};
const _nekoIdleCat1AmbientSoundState = {
    active: false,
    timer: 0,
    token: 0,
    intervalStartedAt: 0,
    audio: null
};
const _nekoIdleCat1QuestionMarkKeyboardState = {
    button: null,
    progress: 0,
    listening: false
};
const _nekoIdleCat1DragSoundState = {
    audio: null,
    fadeFrame: 0,
    fadeToken: 0
};
const _nekoIdleCat1RapidDragSoundState = {
    audio: null,
    fadeFrame: 0,
    fadeToken: 0
};
let _nekoIdleCatAudioEnabledMemory = true;

function isNekoIdleCatAudioEnabled() {
    try {
        const enabled = window.localStorage.getItem(_NEKO_IDLE_CAT_AUDIO_ENABLED_STORAGE_KEY) !== 'false';
        _nekoIdleCatAudioEnabledMemory = enabled;
        return enabled;
    } catch (_) {
        return _nekoIdleCatAudioEnabledMemory;
    }
}

function setNekoIdleCatAudioEnabled(enabled) {
    const next = enabled !== false;
    _nekoIdleCatAudioEnabledMemory = next;
    try {
        window.localStorage.setItem(_NEKO_IDLE_CAT_AUDIO_ENABLED_STORAGE_KEY, next ? 'true' : 'false');
    } catch (_) {}

    if (!next) {
        _stopNekoIdleSleepSound();
        _stopNekoIdleCat1AmbientSound();
        _stopNekoIdleSoundAudio(_nekoIdleCat1DragSoundState);
        _stopNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState);
        _stopNekoIdleCat1ActionSounds();
    } else {
        _syncNekoIdleSleepSoundForTier(_getActiveNekoIdleReturnTier());
        _syncNekoIdleCat1AmbientSoundForTier(_getActiveNekoIdleReturnTier());
    }
}

function _getActiveNekoIdleReturnTier() {
    // 球形态下按钮 tier 被强制为 none，此处不能再 fallback 到 visualTier，
    // 否则重新打开猫音频开关会在呼吸球上排猫叫/睡觉声
    if (_getNekoGoodbyeIdleAppearance() === _NEKO_GOODBYE_IDLE_APPEARANCE_BALL) {
        return _NEKO_IDLE_TIER_NONE;
    }
    let activeTier = _NEKO_IDLE_TIER_NONE;
    _forEachNekoIdleReturnButton((button) => {
        if (activeTier !== _NEKO_IDLE_TIER_NONE) return;
        activeTier = _normalizeNekoIdleReturnTier(button && button.getAttribute('data-neko-idle-tier'));
    });
    return activeTier !== _NEKO_IDLE_TIER_NONE ? activeTier : _readNekoAutoGoodbyeVisualTier();
}

function _stopNekoIdleCat1ActionSounds() {
    _forEachNekoIdleReturnButton((button) => {
        _stopNekoIdleSoundAudio(button.__nekoIdleCat1EatActionState);
        _stopNekoIdleSoundAudio(button.__nekoIdleCat1PlayActionState);
    });
}
let _nekoIdleThoughtBubblePopPreloadImage = null;
const _NEKO_IDLE_RETURN_ASSET_VERSION = (() => {
    try {
        const currentScript = document.currentScript;
        if (currentScript && currentScript.src) {
            const version = new URL(currentScript.src, window.location.href).searchParams.get('v');
            if (version) {
                return version;
            }
        }
    } catch (_) {}

    try {
        if (typeof window.APP_VERSION === 'string' && window.APP_VERSION) {
            return window.APP_VERSION;
        }
    } catch (_) {}

    return String(Date.now());
})();



const AvatarButtonMixin = {
    methods: {},
    apply: function(ManagerPrototype, prefix, options = {}) {
        options = Object.assign({
            containerElementId: `${prefix}-floating-buttons`,
            returnContainerId: `${prefix}-return-button-container`,
            returnBtnId: `${prefix}-btn-return`,
            lockIconId: `${prefix}-lock-icon`,
            popupPrefix: prefix,
            buttonClassPrefix: `${prefix}-floating-btn`,
            triggerBtnClass: `${prefix}-trigger-btn`,
            triggerIconClass: `${prefix}-trigger-icon`,
            returnBtnClass: `${prefix}-return-btn`,
            returnBreathingStyleId: `${prefix}-return-button-breathing-styles`,
            excludeLiveD2Elements: []
        }, options);

        // 存储前缀供实例方法使用
        ManagerPrototype._avatarPrefix = prefix;
        ManagerPrototype._avatarButtonOptions = options;

        /**
         * 设置浮动按钮系统的基础框架
         * 注：具体的位置更新逻辑由系统特定的实现处理
         */

        Object.values(AvatarButtonMixin.methods).forEach((installMethods) => {
            installMethods(ManagerPrototype, prefix, options);
        });
    }
};
