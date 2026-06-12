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
const _NEKO_IDLE_RETURN_BUTTON_SELECTOR = '#live2d-btn-return, #vrm-btn-return, #mmd-btn-return';
const _NEKO_IDLE_RETURN_TRANSITION_MS = 820;
const _NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS = 900;
const _NEKO_IDLE_RETURN_GIF_DURATION_CACHE = new Map();
const _NEKO_IDLE_RETURN_GIF_PLAYBACK_SOURCE_CACHE = new Map();
const _NEKO_IDLE_CAT1_SUBSTATE_IDLE = 'idle';
const _NEKO_IDLE_CAT1_SUBSTATE_WALKING = 'walking-to-chat';
const _NEKO_IDLE_CAT1_SUBSTATE_STRETCH = 'stretch-near-chat';
const _NEKO_IDLE_CAT1_CHAT_GAP_PX = -12;
const _NEKO_IDLE_CAT1_MINIMIZED_RIGHT_TO_LEFT_APPROACH_PX = 35;
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
const _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS = 2500;
const _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS = 10 * 1000;
const _NEKO_IDLE_RETURN_DRAG_PENDING_CLASS = 'is-drag-action-pending';
const _NEKO_IDLE_RETURN_DRAG_ACTION_CLASS = 'is-drag-action';
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
const _NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL = '/static/assets/neko-idle/thought-items/cloud-thought-bubble.gif';
const _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_ASSET_URL = '/static/assets/neko-idle/thought-items/sleeping-zzz.gif';
const _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS = Object.freeze([
    '/static/assets/neko-idle/thought-items/catnip-pouch.png',
    '/static/assets/neko-idle/thought-items/fish-cookie.png',
    '/static/assets/neko-idle/thought-items/toy-mouse.png'
]);
const _NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS = 5000;
const _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS = 8000;
const _NEKO_IDLE_CAT1_LAYER_REQUEST_HEARTBEAT_MS = 250;
const _NEKO_IDLE_CAT1_LAYER_FOLLOW_REASSERT_MS = 80;
const _NEKO_IDLE_CAT1_LAYER_RELEASE_DELAY_MS = 2600;
const _NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS = 3 * 60 * 1000;
const _NEKO_IDLE_CAT1_AMBIENT_SOUND_VOLUME = 0.14;
const _NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME = 0.16;
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
const _NEKO_IDLE_SLEEP_SOUND_VOLUME = 0.09;
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
const _nekoIdleCat1DragSoundState = {
    audio: null,
    fadeFrame: 0,
    fadeToken: 0
};
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

function _logNekoIdleReturnDragDebug(stage, detail) {
    try {
        const enabled = window.__NEKO_IDLE_RETURN_DRAG_DEBUG === true ||
            (window.localStorage && window.localStorage.getItem('nekoIdleReturnDragDebug') === '1');
        if (!enabled || !window.console || typeof window.console.debug !== 'function') return;
        window.console.debug('[NekoIdleReturnDrag]', stage, detail || {});
    } catch (_) {}
}

function _getNekoIdleReturnAssetVersionSuffix() {
    return _NEKO_IDLE_RETURN_ASSET_VERSION
        ? `?v=${encodeURIComponent(_NEKO_IDLE_RETURN_ASSET_VERSION)}`
        : '';
}

function _normalizeNekoIdleReturnTier(tier) {
    if (tier === _NEKO_IDLE_TIER_CAT2 || tier === _NEKO_IDLE_TIER_CAT3 || tier === _NEKO_IDLE_TIER_NONE) {
        return tier;
    }
    return _NEKO_IDLE_TIER_CAT1;
}

function _isNekoNativeReturnBallDragDisabled() {
    const runtime = window.__NEKO_DESKTOP_RUNTIME__ || {};
    return !!(
        window.__NEKO_DISABLE_NATIVE_RETURN_BALL_DRAG__ ||
        runtime.disableNativeReturnBallDrag
    );
}

function _isNekoDesktopLinuxRuntime() {
    const runtime = window.__NEKO_DESKTOP_RUNTIME__ || {};
    return !!(
        runtime.isLinux ||
        runtime.isLinuxX11 ||
        runtime.platform === 'linux'
    );
}

function _getNekoIdleReturnAssetUrl(tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const versionSuffix = _getNekoIdleReturnAssetVersionSuffix();

    if (normalizedTier === _NEKO_IDLE_TIER_CAT2) {
        return `/static/assets/neko-idle/cat-idle-cat2.gif${versionSuffix}`;
    }
    if (normalizedTier === _NEKO_IDLE_TIER_CAT3) {
        return `/static/assets/neko-idle/cat-idle-cat3.gif${versionSuffix}`;
    }
    return `/static/assets/neko-idle/cat-idle-cat1.gif${versionSuffix}`;
}

function _getNekoIdleReturnClickAssetUrl(tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const versionSuffix = _getNekoIdleReturnAssetVersionSuffix();

    if (normalizedTier === _NEKO_IDLE_TIER_CAT2) {
        return `/static/assets/neko-idle/cat-idle-cat2-click.gif${versionSuffix}`;
    }
    if (normalizedTier === _NEKO_IDLE_TIER_CAT3) {
        return `/static/assets/neko-idle/cat-idle-cat3-click.gif${versionSuffix}`;
    }
    return `/static/assets/neko-idle/cat-idle-cat1-click.gif${versionSuffix}`;
}

function _getNekoIdleCat1WalkingAssetUrl() {
    return `/static/assets/neko-idle/cat-idle-cat4-1.gif${_getNekoIdleReturnAssetVersionSuffix()}`;
}

function _getNekoIdleCat1StretchAssetUrl() {
    return `/static/assets/neko-idle/cat-idle-cat4-2.gif${_getNekoIdleReturnAssetVersionSuffix()}`;
}

function _getNekoIdleCat1InteractiveAssetUrl() {
    return `/static/assets/neko-idle/cat-idle-cat4-3.gif${_getNekoIdleReturnAssetVersionSuffix()}`;
}

function _getNekoIdleReturnDragAssetUrl(tier) {
    const urls = _NEKO_IDLE_RETURN_DRAG_ASSET_URLS_BY_TIER[_normalizeNekoIdleReturnTier(tier)] || null;
    const src = urls && urls[0] ? urls[0] : '';
    return src ? `${src}${_getNekoIdleReturnAssetVersionSuffix()}` : '';
}

function _pickNekoIdleReturnDragAssetUrl(tier) {
    const urls = _NEKO_IDLE_RETURN_DRAG_ASSET_URLS_BY_TIER[_normalizeNekoIdleReturnTier(tier)] || null;
    if (!urls || !urls.length) return '';
    const src = urls[Math.floor(Math.random() * urls.length)] || urls[0] || '';
    return src ? `${src}${_getNekoIdleReturnAssetVersionSuffix()}` : '';
}

function _getNekoIdleSleepSoundConfig(tier) {
    return _NEKO_IDLE_SLEEP_SOUND_BY_TIER[_normalizeNekoIdleReturnTier(tier)] || null;
}

function _pickNekoIdleSleepSoundSrc(config) {
    const srcs = config && config.srcs;
    if (!srcs || !srcs.length) return '';
    return srcs[Math.floor(Math.random() * srcs.length)] || srcs[0] || '';
}

function _buildNekoIdleSoundUrl(src) {
    return src ? src + _getNekoIdleReturnAssetVersionSuffix() : '';
}

function _pickNekoIdleThoughtBubbleBgAsset(tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const roll = Math.random();
    const useSleeping = (normalizedTier === _NEKO_IDLE_TIER_CAT2 && roll < 1 / 3) ||
        (normalizedTier === _NEKO_IDLE_TIER_CAT3 && roll < 2 / 3);
    return {
        assetUrl: useSleeping ? _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_ASSET_URL : _NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL,
        visibleMs: _NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS,
        sleeping: useSleeping
    };
}

function _getNekoIdleThoughtBubbleBgAssetUrl(assetUrl, restartToken = 0) {
    const normalizedAssetUrl = assetUrl || _NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL;
    const baseUrl = `${normalizedAssetUrl}${_getNekoIdleReturnAssetVersionSuffix()}`;
    const normalizedToken = Math.max(0, Number(restartToken) || 0);
    if (!normalizedToken) return baseUrl;
    const separator = baseUrl.includes('?') ? '&' : '?';
    return `${baseUrl}${separator}restart=${encodeURIComponent(String(normalizedToken))}`;
}

function _getNekoIdleThoughtBubbleItemAssetUrl(assetUrl) {
    const normalizedUrl = assetUrl || _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS[0] || '';
    return normalizedUrl ? `${normalizedUrl}${_getNekoIdleReturnAssetVersionSuffix()}` : '';
}

function _pickNekoIdleThoughtBubbleItemAssetUrl(previousAssetUrl = '') {
    const urls = _NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS;
    if (!urls || !urls.length) return '';
    const availableUrls = urls.length > 1 && previousAssetUrl
        ? urls.filter((url) => url !== previousAssetUrl)
        : urls;
    return availableUrls[Math.floor(Math.random() * availableUrls.length)] || availableUrls[0] || urls[0] || '';
}

function _stopNekoIdleSoundAudio(state) {
    if (state && state.fadeFrame) {
        cancelAnimationFrame(state.fadeFrame);
        state.fadeFrame = 0;
    }
    if (state) {
        state.fadeToken = (state.fadeToken || 0) + 1;
    }
    const audio = state && state.audio;
    if (state) state.audio = null;
    if (!audio) return;
    try {
        audio.pause();
        audio.currentTime = 0;
    } catch (_) {}
}

function _fadeOutNekoIdleSoundAudio(state, durationMs) {
    const audio = state && state.audio;
    if (!state || !audio) return;

    if (state.fadeFrame) {
        cancelAnimationFrame(state.fadeFrame);
        state.fadeFrame = 0;
    }
    const token = (state.fadeToken || 0) + 1;
    state.fadeToken = token;
    const startAt = typeof performance !== 'undefined' && typeof performance.now === 'function'
        ? performance.now()
        : Date.now();
    const startVolume = Math.max(0, Math.min(1, Number(audio.volume) || 0));
    const fadeMs = Math.max(0, Number(durationMs) || 0);

    if (fadeMs <= 0 || startVolume <= 0) {
        _stopNekoIdleSoundAudio(state);
        return;
    }

    const step = (timestamp) => {
        if (state.fadeToken !== token || state.audio !== audio) return;
        const now = Number.isFinite(Number(timestamp)) ? Number(timestamp) : Date.now();
        const progress = Math.min(1, Math.max(0, (now - startAt) / fadeMs));
        try {
            audio.volume = Math.max(0, startVolume * (1 - progress));
        } catch (_) {}
        if (progress >= 1 || audio.paused || audio.ended) {
            _stopNekoIdleSoundAudio(state);
            return;
        }
        state.fadeFrame = requestAnimationFrame(step);
    };

    state.fadeFrame = requestAnimationFrame(step);
}

function _playNekoIdleSound(state, src, volume) {
    if (!state || !src) return null;

    _stopNekoIdleSoundAudio(state);
    try {
        const audio = new window.Audio(_buildNekoIdleSoundUrl(src));
        audio.preload = 'auto';
        audio.volume = Math.max(0, Math.min(1, Number(volume) || 0.2));
        state.audio = audio;
        audio.addEventListener('ended', () => {
            if (state.audio === audio) {
                state.audio = null;
            }
        }, { once: true });
        const playResult = audio.play();
        if (playResult && typeof playResult.catch === 'function') {
            playResult.catch(() => {
                if (state.audio === audio) {
                    state.audio = null;
                }
                try {
                    audio.dispatchEvent(new Event('error'));
                } catch (_) {}
            });
        }
        return audio;
    } catch (_) {
        state.audio = null;
        return null;
    }
}

function _clearNekoIdleThoughtBubble(button) {
    if (!button) return;
    if (button.__nekoIdleThoughtBubbleTimer) {
        clearTimeout(button.__nekoIdleThoughtBubbleTimer);
        button.__nekoIdleThoughtBubbleTimer = 0;
    }
    button.__nekoIdleThoughtBubbleTimerToken = (button.__nekoIdleThoughtBubbleTimerToken || 0) + 1;
    button.__nekoIdleThoughtBubbleTier = '';
    button.__nekoIdleThoughtBubbleDebugKey = '';
    button.__nekoIdleThoughtBubbleAudio = null;
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS);
}

function _getNekoIdleAudioRemainingMs(audio) {
    if (!audio) return 0;
    const durationMs = Number(audio.duration) * 1000;
    if (!Number.isFinite(durationMs) || durationMs <= 0) return 0;
    const currentMs = Math.max(0, Number(audio.currentTime) * 1000 || 0);
    return Math.max(0, Math.round(durationMs - currentMs));
}

function _getNekoIdleThoughtBubbleVisibleMs(bubbleConfig, audio) {
    if (!bubbleConfig || !bubbleConfig.sleeping) {
        return Math.max(0, Number(bubbleConfig && bubbleConfig.visibleMs) || _NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS);
    }
    if (audio) return _getNekoIdleAudioRemainingMs(audio) || _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS;
    return _NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_FALLBACK_VISIBLE_MS;
}

function _scheduleNekoIdleThoughtBubbleHide(button, token, visibleMs) {
    if (!button || button.__nekoIdleThoughtBubbleTimerToken !== token) return;
    const normalizedVisibleMs = Number(visibleMs);
    if (!Number.isFinite(normalizedVisibleMs) || normalizedVisibleMs <= 0) return;
    if (button.__nekoIdleThoughtBubbleTimer) {
        clearTimeout(button.__nekoIdleThoughtBubbleTimer);
    }
    button.__nekoIdleThoughtBubbleTimer = window.setTimeout(() => {
        _hideNekoIdleThoughtBubble(button, token);
    }, normalizedVisibleMs);
}

function _hideNekoIdleThoughtBubble(button, token) {
    if (!button) return;
    if (token != null && button.__nekoIdleThoughtBubbleTimerToken !== token) return;
    if (button.__nekoIdleThoughtBubbleTimer) {
        clearTimeout(button.__nekoIdleThoughtBubbleTimer);
        button.__nekoIdleThoughtBubbleTimer = 0;
    }
    button.__nekoIdleThoughtBubbleTier = '';
    button.__nekoIdleThoughtBubbleAudio = null;
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS);
}

function _restartNekoIdleThoughtBubbleArt(button, tier) {
    if (!button) {
        return {
            assetUrl: _NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL,
            visibleMs: _NEKO_IDLE_THOUGHT_BUBBLE_VISIBLE_MS,
            sleeping: false
        };
    }
    const bubbleConfig = _pickNekoIdleThoughtBubbleBgAsset(tier);
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);
    button.classList.toggle(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS, !!bubbleConfig.sleeping);
    button.__nekoIdleThoughtBubbleRestartToken = (button.__nekoIdleThoughtBubbleRestartToken || 0) + 1;
    const bg = button.querySelector('.neko-idle-thought-bubble-bg');
    if (bg) {
        bg.src = _getNekoIdleThoughtBubbleBgAssetUrl(bubbleConfig.assetUrl, button.__nekoIdleThoughtBubbleRestartToken);
    }
    const item = button.querySelector('.neko-idle-thought-bubble-item');
    if (item) {
        const itemAssetUrl = _pickNekoIdleThoughtBubbleItemAssetUrl(button.__nekoIdleThoughtBubbleItemAssetUrl);
        button.__nekoIdleThoughtBubbleItemAssetUrl = itemAssetUrl;
        item.src = _getNekoIdleThoughtBubbleItemAssetUrl(itemAssetUrl);
    }
    try {
        void button.offsetWidth;
    } catch (_) {}
    return bubbleConfig;
}

function _showNekoIdleThoughtBubbleForSound(tier, audio = null) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (normalizedTier === _NEKO_IDLE_TIER_NONE) return;
    _forEachNekoIdleReturnButton((button) => {
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== normalizedTier) return;
        const container = _getNekoIdleReturnContainerFromButton(button);
        if (!container || container.style.display === 'none') return;
        const bubbleConfig = _restartNekoIdleThoughtBubbleArt(button, normalizedTier);
        button.classList.add(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);
        button.__nekoIdleThoughtBubbleTier = normalizedTier;
        if (button.__nekoIdleThoughtBubbleTimer) {
            clearTimeout(button.__nekoIdleThoughtBubbleTimer);
        }
        button.__nekoIdleThoughtBubbleTimerToken = (button.__nekoIdleThoughtBubbleTimerToken || 0) + 1;
        const timerToken = button.__nekoIdleThoughtBubbleTimerToken;
        const visibleMs = _getNekoIdleThoughtBubbleVisibleMs(bubbleConfig, audio);
        button.__nekoIdleThoughtBubbleAudio = bubbleConfig.sleeping ? audio : null;
        _scheduleNekoIdleThoughtBubbleHide(button, timerToken, visibleMs);
        if (bubbleConfig.sleeping && audio && typeof audio.addEventListener === 'function') {
            audio.addEventListener('loadedmetadata', () => {
                if (button.__nekoIdleThoughtBubbleAudio === audio) {
                    _scheduleNekoIdleThoughtBubbleHide(
                        button,
                        timerToken,
                        _getNekoIdleThoughtBubbleVisibleMs(bubbleConfig, audio)
                    );
                }
            }, { once: true });
            audio.addEventListener('ended', () => {
                if (button.__nekoIdleThoughtBubbleAudio === audio) {
                    _hideNekoIdleThoughtBubble(button, timerToken);
                }
            }, { once: true });
            audio.addEventListener('error', () => {
                if (button.__nekoIdleThoughtBubbleAudio === audio) {
                    _hideNekoIdleThoughtBubble(button, timerToken);
                }
            }, { once: true });
        }
    });
}

function _clearNekoIdleSleepSoundTimer() {
    if (_nekoIdleSleepSoundState.timer) {
        clearTimeout(_nekoIdleSleepSoundState.timer);
        _nekoIdleSleepSoundState.timer = 0;
    }
}

function _stopNekoIdleSleepSoundAudio() {
    _stopNekoIdleSoundAudio(_nekoIdleSleepSoundState);
}

function _playNekoIdleSleepSound(tier, token) {
    const config = _getNekoIdleSleepSoundConfig(tier);
    if (!config || token !== _nekoIdleSleepSoundState.token || _nekoIdleSleepSoundState.tier !== tier) {
        return;
    }

    const audio = _playNekoIdleSound(_nekoIdleSleepSoundState, _pickNekoIdleSleepSoundSrc(config), config.volume);
    if (audio) _showNekoIdleThoughtBubbleForSound(tier, audio);
}

function _scheduleNekoIdleSleepSoundInterval(tier, intervalStartedAt) {
    const config = _getNekoIdleSleepSoundConfig(tier);
    if (!config || _nekoIdleSleepSoundState.tier !== tier) return;

    _clearNekoIdleSleepSoundTimer();
    const token = _nekoIdleSleepSoundState.token;
    const startedAt = Math.max(0, Number(intervalStartedAt) || Date.now());
    _nekoIdleSleepSoundState.intervalStartedAt = startedAt;

    const playAt = startedAt + Math.round(Math.random() * _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS);
    const delayMs = Math.max(0, playAt - Date.now());
    _nekoIdleSleepSoundState.timer = setTimeout(() => {
        _nekoIdleSleepSoundState.timer = 0;
        if (token !== _nekoIdleSleepSoundState.token || _nekoIdleSleepSoundState.tier !== tier) {
            return;
        }
        _playNekoIdleSleepSound(tier, token);
        _scheduleNekoIdleSleepSoundInterval(tier, startedAt + _NEKO_IDLE_SLEEP_SOUND_INTERVAL_MS);
    }, delayMs);
}

function _syncNekoIdleSleepSoundForTier(tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const config = _getNekoIdleSleepSoundConfig(normalizedTier);
    if (!config) {
        _nekoIdleSleepSoundState.tier = _NEKO_IDLE_TIER_NONE;
        _nekoIdleSleepSoundState.token += 1;
        _nekoIdleSleepSoundState.intervalStartedAt = 0;
        _clearNekoIdleSleepSoundTimer();
        _stopNekoIdleSleepSoundAudio();
        return;
    }

    if (_nekoIdleSleepSoundState.tier === normalizedTier && _nekoIdleSleepSoundState.timer) {
        return;
    }

    _nekoIdleSleepSoundState.tier = normalizedTier;
    _nekoIdleSleepSoundState.token += 1;
    _stopNekoIdleSleepSoundAudio();
    _scheduleNekoIdleSleepSoundInterval(normalizedTier, Date.now());
}

function _clearNekoIdleCat1AmbientSoundTimer() {
    if (_nekoIdleCat1AmbientSoundState.timer) {
        clearTimeout(_nekoIdleCat1AmbientSoundState.timer);
        _nekoIdleCat1AmbientSoundState.timer = 0;
    }
}

function _stopNekoIdleCat1AmbientSoundAudio() {
    _stopNekoIdleSoundAudio(_nekoIdleCat1AmbientSoundState);
}

function _pickNekoIdleCat1AmbientSoundUrl() {
    const urls = _NEKO_IDLE_CAT1_AMBIENT_SOUND_URLS;
    if (!urls || !urls.length) return '';
    return urls[Math.floor(Math.random() * urls.length)] || urls[0] || '';
}

function _playNekoIdleCat1AmbientSound(token) {
    if (!_nekoIdleCat1AmbientSoundState.active ||
        token !== _nekoIdleCat1AmbientSoundState.token ||
        _isAnyNekoIdleReturnDragActionActive()) {
        return;
    }

    const audio = _playNekoIdleSound(
        _nekoIdleCat1AmbientSoundState,
        _pickNekoIdleCat1AmbientSoundUrl(),
        _NEKO_IDLE_CAT1_AMBIENT_SOUND_VOLUME
    );
    if (audio) _showNekoIdleThoughtBubbleForSound(_NEKO_IDLE_TIER_CAT1);
    _playNekoIdleCat1SoundReaction();
}

function _scheduleNekoIdleCat1AmbientSoundInterval(intervalStartedAt) {
    if (!_nekoIdleCat1AmbientSoundState.active || _isAnyNekoIdleReturnDragActionActive()) return;

    _clearNekoIdleCat1AmbientSoundTimer();
    const token = _nekoIdleCat1AmbientSoundState.token;
    const startedAt = Math.max(0, Number(intervalStartedAt) || Date.now());
    _nekoIdleCat1AmbientSoundState.intervalStartedAt = startedAt;

    const playAt = startedAt + Math.round(Math.random() * _NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS);
    const delayMs = Math.max(0, playAt - Date.now());
    _nekoIdleCat1AmbientSoundState.timer = setTimeout(() => {
        _nekoIdleCat1AmbientSoundState.timer = 0;
        if (!_nekoIdleCat1AmbientSoundState.active ||
            token !== _nekoIdleCat1AmbientSoundState.token ||
            _isAnyNekoIdleReturnDragActionActive()) {
            return;
        }
        _playNekoIdleCat1AmbientSound(token);
        _scheduleNekoIdleCat1AmbientSoundInterval(startedAt + _NEKO_IDLE_CAT1_AMBIENT_SOUND_INTERVAL_MS);
    }, delayMs);
}

function _stopNekoIdleCat1AmbientSound() {
    _nekoIdleCat1AmbientSoundState.active = false;
    _nekoIdleCat1AmbientSoundState.token += 1;
    _nekoIdleCat1AmbientSoundState.intervalStartedAt = 0;
    _clearNekoIdleCat1AmbientSoundTimer();
    _stopNekoIdleCat1AmbientSoundAudio();
}

function _syncNekoIdleCat1AmbientSoundForTier(tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (normalizedTier !== _NEKO_IDLE_TIER_CAT1 || _isAnyNekoIdleReturnDragActionActive()) {
        _stopNekoIdleCat1AmbientSound();
        return;
    }

    if (_nekoIdleCat1AmbientSoundState.active && _nekoIdleCat1AmbientSoundState.timer) {
        return;
    }

    _nekoIdleCat1AmbientSoundState.active = true;
    _nekoIdleCat1AmbientSoundState.token += 1;
    _stopNekoIdleCat1AmbientSoundAudio();
    _scheduleNekoIdleCat1AmbientSoundInterval(Date.now());
}

function _playNekoIdleCat1DragSound(tier) {
    if (_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1) return;
    _stopNekoIdleCat1AmbientSound();
    _playNekoIdleSound(
        _nekoIdleCat1DragSoundState,
        _NEKO_IDLE_CAT1_DRAG_SOUND_URL,
        _NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME
    );
}

function _fadeOutNekoIdleCat1DragSound() {
    _fadeOutNekoIdleSoundAudio(_nekoIdleCat1DragSoundState, _NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS);
}

function _syncNekoIdleCat1CompactMirrorReaction(button, container, assetUrl, reason) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state ||
        !container ||
        !container.__nekoIdleCat1CompactMirrorActive ||
        state.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) {
        return;
    }

    const surfaceRect = _getNekoIdleChatCompactSurfaceRect();
    if (!surfaceRect) return;
    _setNekoIdleCat1CompactMirrorActive(button, container, true, {
        reason: reason || 'cat1-sound-reaction',
        surfaceRect: surfaceRect,
        target: {
            anchorRatio: state.compactFollowAnchorRatio
        },
        assetUrl: assetUrl
    });
}

function _playNekoIdleCat1SoundReaction() {
    _forEachNekoIdleReturnButton((button) => {
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return;
        if (_isNekoIdleReturnDragActionActive(button)) return;
        const container = _getNekoIdleReturnContainerFromButton(button);
        if (!container || container.style.display === 'none') return;
        const state = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!state || state.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) return;
        const art = button.querySelector('.neko-idle-return-art');
        if (!art) return;

        _playNekoIdleHoverArt(art, _NEKO_IDLE_TIER_CAT1);
        const reactionSrc = art.__nekoIdleHoverSrc;
        if (!reactionSrc) return;
        const reactionStartedAt = Math.max(0, Number(art.__nekoIdleHoverStartedAt) || Date.now());
        const hoverToken = art.__nekoIdleHoverToken || 0;
        const mirrorToken = (art.__nekoIdleCat1CompactMirrorReactionToken || 0) + 1;
        art.__nekoIdleCat1CompactMirrorReactionToken = mirrorToken;
        _finishNekoIdleHoverArtAfterPlayback(art, _NEKO_IDLE_TIER_CAT1);
        _syncNekoIdleCat1CompactMirrorReaction(button, container, reactionSrc, 'cat1-sound-reaction');

        _getNekoIdleGifDurationMs(reactionSrc).then((durationMs) => {
            if ((art.__nekoIdleCat1CompactMirrorReactionToken || 0) !== mirrorToken) return;
            const elapsedMs = Math.max(0, Date.now() - reactionStartedAt);
            const remainingMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);
            window.setTimeout(() => {
                if ((art.__nekoIdleCat1CompactMirrorReactionToken || 0) !== mirrorToken) return;
                const latestHoverToken = art.__nekoIdleHoverToken || 0;
                const hoverStillPlaying = latestHoverToken === hoverToken;
                const hoverFinishedThisReaction = latestHoverToken === hoverToken + 1 && !art.__nekoIdleHoverSrc;
                if (!hoverStillPlaying && !hoverFinishedThisReaction) return;
                const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
                const latestContainer = _getNekoIdleReturnContainerFromButton(button);
                if (!latestState ||
                    latestState.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE ||
                    !latestContainer ||
                    !latestContainer.__nekoIdleCat1CompactMirrorActive) {
                    return;
                }
                _syncNekoIdleCat1CompactMirrorReaction(
                    button,
                    latestContainer,
                    _getNekoIdleReturnCurrentArtUrl(button, _NEKO_IDLE_TIER_CAT1),
                    'cat1-sound-reaction-finished'
                );
            }, remainingMs);
        });
    });
}

const _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW = Object.freeze({
    id: 'cat1-chat-follow',
    tier: _NEKO_IDLE_TIER_CAT1,
    idleSubstate: _NEKO_IDLE_CAT1_SUBSTATE_IDLE,
    walkingSubstate: _NEKO_IDLE_CAT1_SUBSTATE_WALKING,
    finishingSubstate: _NEKO_IDLE_CAT1_SUBSTATE_STRETCH,
    classNames: Object.freeze({
        walking: 'is-cat1-walking',
        finishing: 'is-cat1-stretching',
        facingRight: 'is-cat1-facing-right',
        paused: 'is-cat1-hover-paused',
        compactTopEdge: 'is-cat1-on-compact-top-edge'
    }),
    dataAttributes: Object.freeze({
        substate: 'data-neko-cat1-substate',
        facing: 'data-neko-cat1-facing',
        targetKind: 'data-neko-cat1-target'
    }),
    assets: Object.freeze({
        idle: () => _getNekoIdleReturnAssetUrl(_NEKO_IDLE_TIER_CAT1),
        walking: _getNekoIdleCat1WalkingAssetUrl,
        finishing: _getNekoIdleCat1StretchAssetUrl,
        interactive: _getNekoIdleCat1InteractiveAssetUrl
    }),
    target: Object.freeze({
        gapPx: _NEKO_IDLE_CAT1_CHAT_GAP_PX,
        enterDistancePx: _NEKO_IDLE_CAT1_WALK_ENTER_DISTANCE_PX,
        exitDistancePx: _NEKO_IDLE_CAT1_WALK_EXIT_DISTANCE_PX,
        speedPxPerSec: _NEKO_IDLE_CAT1_WALK_SPEED_PX_PER_SEC,
        maxSpeedRate: _NEKO_IDLE_CAT1_WALK_MAX_SPEED_RATE,
        distanceIncreaseThresholdPx: _NEKO_IDLE_CAT1_WALK_DISTANCE_INCREASE_THRESHOLD_PX,
        distanceGrowthForMaxRatePx: _NEKO_IDLE_CAT1_WALK_DISTANCE_GROWTH_FOR_MAX_RATE_PX,
        minStepMs: _NEKO_IDLE_CAT1_WALK_MIN_STEP_MS,
        maxStepMs: _NEKO_IDLE_CAT1_WALK_MAX_STEP_MS
    }),
    settle: Object.freeze({
        finalHoldMs: _NEKO_IDLE_CAT1_STRETCH_FINAL_HOLD_MS,
        resetFacingAfterMs: _NEKO_IDLE_RETURN_TRANSITION_MS
    }),
    startDelay: Object.freeze({
        choices: Object.freeze([
            Object.freeze({ weight: 68, minMs: 0, maxMs: 0 }),
            Object.freeze({ weight: 22, minMs: _NEKO_IDLE_CAT1_WALK_SHORT_DELAY_MIN_MS, maxMs: _NEKO_IDLE_CAT1_WALK_SHORT_DELAY_MAX_MS }),
            Object.freeze({ weight: 8, minMs: _NEKO_IDLE_CAT1_WALK_MEDIUM_DELAY_MIN_MS, maxMs: _NEKO_IDLE_CAT1_WALK_MEDIUM_DELAY_MAX_MS }),
            Object.freeze({ weight: 2, minMs: _NEKO_IDLE_CAT1_WALK_LONG_DELAY_MIN_MS, maxMs: _NEKO_IDLE_CAT1_WALK_LONG_DELAY_MAX_MS })
        ])
    }),
    pairMove: Object.freeze({
        minDistancePx: _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DISTANCE_PX,
        maxDistancePx: _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX,
        minUsableDistancePx: _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX,
        speedPxPerSec: _NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC,
        minDurationMs: _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DURATION_MS,
        maxDurationMs: _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DURATION_MS,
        intervalChoices: Object.freeze([
            Object.freeze({ weight: 58, minMs: _NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MIN_MS, maxMs: _NEKO_IDLE_CAT1_PAIR_MOVE_SHORT_DELAY_MAX_MS }),
            Object.freeze({ weight: 34, minMs: _NEKO_IDLE_CAT1_PAIR_MOVE_MEDIUM_DELAY_MIN_MS, maxMs: _NEKO_IDLE_CAT1_PAIR_MOVE_MEDIUM_DELAY_MAX_MS }),
            Object.freeze({ weight: 8, minMs: _NEKO_IDLE_CAT1_PAIR_MOVE_LONG_DELAY_MIN_MS, maxMs: _NEKO_IDLE_CAT1_PAIR_MOVE_LONG_DELAY_MAX_MS })
        ])
    })
});

const _NEKO_IDLE_RETURN_SUBACTION_PROFILES = Object.freeze({
    [_NEKO_IDLE_TIER_CAT1]: _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW
});
let _nekoIdleDesktopChatMinimizedState = {
    minimized: false,
    screenRect: null,
    updatedAt: 0,
    sourceUpdatedAt: 0,
    expandedRecent: false
};
let _nekoIdleDesktopCompactSurfaceState = {
    visible: false,
    screenRect: null,
    updatedAt: 0,
    sourceUpdatedAt: 0
};
let _nekoIdleDesktopChatPairMoveLastDispatchAt = 0;
let _nekoIdleDesktopChatPairMoveLastDispatchSignature = '';
let _nekoIdleCompactSurfaceDragging = false;
let _nekoIdleCompactSurfaceSettleTimer = 0;

function _getNekoIdleDesktopStateSourceUpdatedAt(detail, fallbackUpdatedAt) {
    const timestamp = Number(detail && detail.timestamp);
    if (Number.isFinite(timestamp) && timestamp > 0) return timestamp;
    const fallback = Number(fallbackUpdatedAt);
    if (Number.isFinite(fallback) && fallback > 0) return fallback;
    return Date.now();
}

function _isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, state) {
    const incomingSourceUpdatedAt = Number(sourceUpdatedAt);
    const currentSourceUpdatedAt = Number(state && state.sourceUpdatedAt);
    return Number.isFinite(incomingSourceUpdatedAt) &&
        incomingSourceUpdatedAt > 0 &&
        Number.isFinite(currentSourceUpdatedAt) &&
        currentSourceUpdatedAt > 0 &&
        incomingSourceUpdatedAt < currentSourceUpdatedAt;
}

function _isNekoIdleDesktopStateNewerThan(sourceUpdatedAt, state) {
    const incomingSourceUpdatedAt = Number(sourceUpdatedAt);
    const currentSourceUpdatedAt = Number(state && state.sourceUpdatedAt);
    return Number.isFinite(incomingSourceUpdatedAt) &&
        incomingSourceUpdatedAt > 0 &&
        (!Number.isFinite(currentSourceUpdatedAt) ||
            currentSourceUpdatedAt <= 0 ||
            incomingSourceUpdatedAt >= currentSourceUpdatedAt);
}

function _makeNekoIdleDesktopChatMinimizedState(minimized, screenRect, updatedAt, sourceUpdatedAt, expandedRecent) {
    const active = !!(minimized && screenRect);
    return {
        minimized: active,
        screenRect: active ? screenRect : null,
        updatedAt: updatedAt,
        sourceUpdatedAt: sourceUpdatedAt,
        expandedRecent: !active && !!expandedRecent
    };
}

function _makeNekoIdleDesktopCompactSurfaceState(visible, screenRect, updatedAt, sourceUpdatedAt) {
    const active = !!(visible && screenRect);
    return {
        visible: active,
        screenRect: active ? screenRect : null,
        updatedAt: updatedAt,
        sourceUpdatedAt: sourceUpdatedAt
    };
}

function _shouldReduceNekoIdleMotion() {
    try {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (_) {}
    return false;
}

function _readUint16LittleEndian(bytes, offset) {
    if (!bytes || offset < 0 || offset + 1 >= bytes.length) return 0;
    return bytes[offset] | (bytes[offset + 1] << 8);
}

function _writeUint16LittleEndian(bytes, offset, value) {
    if (!bytes || offset < 0 || offset + 1 >= bytes.length) return;
    const normalized = Math.max(0, Math.min(0xffff, Math.round(Number(value) || 0)));
    bytes[offset] = normalized & 0xff;
    bytes[offset + 1] = (normalized >> 8) & 0xff;
}

function _parseGifDurationMs(bytes) {
    if (!bytes || bytes.length < 14) return 0;
    const isGif = bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46;
    if (!isGif) return 0;

    let offset = 13;
    const packed = bytes[10];
    if (packed & 0x80) {
        offset += 3 * (1 << ((packed & 0x07) + 1));
    }

    let totalMs = 0;
    let frameCount = 0;
    let pendingDelayCs = 0;

    while (offset < bytes.length) {
        const blockId = bytes[offset++];
        if (blockId === 0x3b) break;

        if (blockId === 0x21) {
            const label = bytes[offset++];
            if (label === 0xf9 && bytes[offset] === 0x04) {
                pendingDelayCs = _readUint16LittleEndian(bytes, offset + 2);
                offset += 6;
                continue;
            }

            while (offset < bytes.length) {
                const size = bytes[offset++];
                if (size === 0) break;
                offset += size;
            }
            continue;
        }

        if (blockId === 0x2c) {
            if (offset + 8 >= bytes.length) break;
            const imagePacked = bytes[offset + 8];
            offset += 9;
            if (imagePacked & 0x80) {
                offset += 3 * (1 << ((imagePacked & 0x07) + 1));
            }
            offset += 1; // LZW minimum code size
            while (offset < bytes.length) {
                const size = bytes[offset++];
                if (size === 0) break;
                offset += size;
            }
            frameCount += 1;
            totalMs += Math.max(20, pendingDelayCs * 10);
            pendingDelayCs = 0;
            continue;
        }

        break;
    }

    return frameCount > 0 ? totalMs : 0;
}

function _patchGifDelayRate(bytes, rate) {
    if (!bytes || bytes.length < 14) return null;
    const isGif = bytes[0] === 0x47 && bytes[1] === 0x49 && bytes[2] === 0x46;
    if (!isGif) return null;

    const playbackRate = Math.max(1, Number(rate) || 1);
    const patched = new Uint8Array(bytes);
    let offset = 13;
    const packed = patched[10];
    if (packed & 0x80) {
        offset += 3 * (1 << ((packed & 0x07) + 1));
    }

    let changed = false;
    while (offset < patched.length) {
        const blockId = patched[offset++];
        if (blockId === 0x3b) break;

        if (blockId === 0x21) {
            const label = patched[offset++];
            if (label === 0xf9 && patched[offset] === 0x04) {
                const delayOffset = offset + 2;
                const originalDelayCs = _readUint16LittleEndian(patched, delayOffset);
                if (originalDelayCs > 0) {
                    const nextDelayCs = Math.max(2, Math.round(originalDelayCs / playbackRate));
                    if (nextDelayCs !== originalDelayCs) {
                        _writeUint16LittleEndian(patched, delayOffset, nextDelayCs);
                        changed = true;
                    }
                }
                offset += 6;
                continue;
            }

            while (offset < patched.length) {
                const size = patched[offset++];
                if (size === 0) break;
                offset += size;
            }
            continue;
        }

        if (blockId === 0x2c) {
            if (offset + 8 >= patched.length) break;
            const imagePacked = patched[offset + 8];
            offset += 9;
            if (imagePacked & 0x80) {
                offset += 3 * (1 << ((imagePacked & 0x07) + 1));
            }
            offset += 1;
            while (offset < patched.length) {
                const size = patched[offset++];
                if (size === 0) break;
                offset += size;
            }
            continue;
        }

        break;
    }
    return changed ? patched : null;
}

function _normalizeNekoIdleGifPlaybackRate(rate) {
    const value = Number(rate);
    if (!Number.isFinite(value) || value <= 1.02) return 1;
    return Math.max(1, Math.min(1.5, Math.round(value * 10) / 10));
}

function _getNekoIdleGifPlaybackSource(src, rate) {
    const playbackRate = _normalizeNekoIdleGifPlaybackRate(rate);
    if (!src || playbackRate <= 1) return Promise.resolve(src || '');
    const cacheKey = `${src}@@${playbackRate}`;
    if (_NEKO_IDLE_RETURN_GIF_PLAYBACK_SOURCE_CACHE.has(cacheKey)) {
        return _NEKO_IDLE_RETURN_GIF_PLAYBACK_SOURCE_CACHE.get(cacheKey);
    }

    const sourcePromise = (async () => {
        try {
            if (typeof fetch !== 'function' || typeof Blob === 'undefined' || typeof URL === 'undefined') {
                return src;
            }
            const response = await fetch(src, { cache: 'force-cache' });
            if (!response || !response.ok) return src;
            const buffer = await response.arrayBuffer();
            const patched = _patchGifDelayRate(new Uint8Array(buffer), playbackRate);
            if (!patched) return src;
            return URL.createObjectURL(new Blob([patched], { type: 'image/gif' }));
        } catch (_) {
            return src;
        }
    })();

    _NEKO_IDLE_RETURN_GIF_PLAYBACK_SOURCE_CACHE.set(cacheKey, sourcePromise);
    return sourcePromise;
}

function _getNekoIdleGifDurationMs(src) {
    if (!src) return Promise.resolve(_NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS);
    if (_NEKO_IDLE_RETURN_GIF_DURATION_CACHE.has(src)) {
        return _NEKO_IDLE_RETURN_GIF_DURATION_CACHE.get(src);
    }

    const durationPromise = (async () => {
        try {
            if (typeof fetch !== 'function') return _NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS;
            const response = await fetch(src, { cache: 'force-cache' });
            if (!response || !response.ok) return _NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS;
            const buffer = await response.arrayBuffer();
            const durationMs = _parseGifDurationMs(new Uint8Array(buffer));
            return durationMs > 0 ? durationMs : _NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS;
        } catch (_) {
            return _NEKO_IDLE_RETURN_GIF_DURATION_FALLBACK_MS;
        }
    })();

    _NEKO_IDLE_RETURN_GIF_DURATION_CACHE.set(src, durationPromise);
    return durationPromise;
}

function _cleanupNekoIdleArtTransition(art) {
    if (!art) return;
    if (art.__nekoIdleTransitionTimer) {
        clearTimeout(art.__nekoIdleTransitionTimer);
        art.__nekoIdleTransitionTimer = 0;
    }
    if (art.__nekoIdleTransitionNext && art.__nekoIdleTransitionNext.parentNode) {
        art.__nekoIdleTransitionNext.parentNode.removeChild(art.__nekoIdleTransitionNext);
    }
    art.__nekoIdleTransitionNext = null;
    art.__nekoIdleTransitionTo = '';

    const button = art.closest('.neko-idle-return-btn');
    if (button) {
        button.classList.remove('is-tier-transitioning');
    }
}

function _clearNekoIdleGifPlaybackSource(art) {
    if (!art) return;
    art.__nekoIdleGifPlaybackToken = (art.__nekoIdleGifPlaybackToken || 0) + 1;
    art.__nekoIdleGifPlaybackBaseSrc = '';
    art.__nekoIdleGifPlaybackRate = 1;
}

function _clearNekoIdleHoverPlayback(art) {
    if (!art) return;
    if (art.__nekoIdleHoverTimer) {
        clearTimeout(art.__nekoIdleHoverTimer);
        art.__nekoIdleHoverTimer = 0;
    }
    art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
    art.__nekoIdleHoverSrc = '';
    art.__nekoIdleHoverTier = '';
    art.__nekoIdleHoverStartedAt = 0;
}

function _applyNekoIdleGifPlaybackRate(art, baseSrc, rate) {
    if (!art || !baseSrc) return;
    const playbackRate = _normalizeNekoIdleGifPlaybackRate(rate);
    if (playbackRate <= 1) {
        _clearNekoIdleGifPlaybackSource(art);
        if ((art.getAttribute('src') || '') !== baseSrc) {
            art.src = baseSrc;
        }
        return;
    }

    if (art.__nekoIdleGifPlaybackBaseSrc === baseSrc &&
        art.__nekoIdleGifPlaybackRate === playbackRate) {
        return;
    }

    const token = (art.__nekoIdleGifPlaybackToken || 0) + 1;
    art.__nekoIdleGifPlaybackToken = token;
    art.__nekoIdleGifPlaybackBaseSrc = baseSrc;
    art.__nekoIdleGifPlaybackRate = playbackRate;
    _getNekoIdleGifPlaybackSource(baseSrc, playbackRate).then((nextSrc) => {
        if ((art.__nekoIdleGifPlaybackToken || 0) !== token) return;
        if (art.__nekoIdleGifPlaybackBaseSrc !== baseSrc) return;
        if (art.__nekoIdleGifPlaybackRate !== playbackRate) return;
        if (!nextSrc || (art.getAttribute('src') || '') === nextSrc) return;
        art.src = nextSrc;
    });
}

function _getNekoIdleReturnCurrentArtUrl(button, tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier || (button && button.getAttribute('data-neko-idle-tier')));
    return normalizedTier === _NEKO_IDLE_TIER_CAT1
        ? _getNekoIdleCat1ArtSource(button)
        : _getNekoIdleReturnAssetUrl(normalizedTier);
}

function _getNekoIdleReturnButtonFromArt(art) {
    return art && typeof art.closest === 'function'
        ? art.closest('.neko-idle-return-btn')
        : null;
}

function _getNekoIdleReturnContainerFromButton(button) {
    return button && typeof button.closest === 'function'
        ? button.closest('[id$="-return-button-container"]')
        : null;
}

function _getNekoIdleReturnButtonFromContainer(container) {
    return container && typeof container.querySelector === 'function'
        ? container.querySelector('.neko-idle-return-btn')
        : null;
}

function _getNekoIdleReturnDragActionState(button) {
    if (!button) return null;
    if (!button.__nekoIdleReturnDragActionState) {
        button.__nekoIdleReturnDragActionState = {
            active: false,
            token: 0,
            tier: _NEKO_IDLE_TIER_NONE
        };
    }
    return button.__nekoIdleReturnDragActionState;
}

function _isNekoIdleReturnDragActionActive(button) {
    const state = button && button.__nekoIdleReturnDragActionState;
    return !!(state && state.active);
}

function _isAnyNekoIdleReturnDragActionActive() {
    let active = false;
    document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
        if (active) return;
        active = _isNekoIdleReturnDragActionActive(button);
    });
    return active;
}

function _setNekoIdleReturnDragActionClasses(button, active) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    button.classList.toggle(_NEKO_IDLE_RETURN_DRAG_ACTION_CLASS, !!active);
    if (container) {
        container.classList.toggle(_NEKO_IDLE_RETURN_DRAG_ACTION_CLASS, !!active);
    }
}

function _setNekoIdleReturnDragPendingClasses(button, active) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    button.classList.toggle(_NEKO_IDLE_RETURN_DRAG_PENDING_CLASS, !!active);
    if (container) {
        container.classList.toggle(_NEKO_IDLE_RETURN_DRAG_PENDING_CLASS, !!active);
    }
}

function _setNekoIdleReturnDragActionArt(button, tier) {
    if (!button) return;
    const art = button && button.querySelector('.neko-idle-return-art');
    const dragSrc = button.__nekoIdleReturnDragAssetUrl || _pickNekoIdleReturnDragAssetUrl(tier);
    if (!art || !dragSrc) return;
    button.__nekoIdleReturnDragAssetUrl = dragSrc;
    _setNekoIdleReturnArtSource(
        art,
        dragSrc,
        _normalizeNekoIdleReturnTier(tier),
        { animate: false }
    );
}

function _prepareNekoIdleReturnDragActionForContainer(container) {
    const button = _getNekoIdleReturnButtonFromContainer(container);
    if (!button) return;
    const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    if (currentState &&
        currentState.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) {
        currentState.compactTopEdgeRearmRequired = true;
    }
    _logNekoIdleReturnDragDebug('prepare', {
        containerId: container && container.id,
        tier: button.getAttribute('data-neko-idle-tier')
    });
    _setNekoIdleReturnDragPendingClasses(button, true);
    _cancelNekoIdleCat1Journey(button, {
        resetArt: false,
        preserveObservers: true
    });
}

function _startNekoIdleReturnDragActionForContainer(container) {
    const button = _getNekoIdleReturnButtonFromContainer(container);
    if (!button) return;
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (tier === _NEKO_IDLE_TIER_NONE) return;
    const state = _getNekoIdleReturnDragActionState(button);
    state.active = true;
    state.token += 1;
    state.tier = tier;
    button.__nekoIdleReturnDragAssetUrl = _pickNekoIdleReturnDragAssetUrl(tier);
    _cancelNekoIdleCat1Journey(button, {
        resetArt: false,
        preserveObservers: true
    });
    _setNekoIdleReturnDragPendingClasses(button, false);
    _setNekoIdleReturnDragActionClasses(button, true);
    _setNekoIdleReturnDragActionArt(button, tier);
    _playNekoIdleCat1DragSound(tier);
    _logNekoIdleReturnDragDebug('active', {
        containerId: container && container.id,
        tier: tier,
        src: button.__nekoIdleReturnDragAssetUrl
    });
}

function _finishNekoIdleReturnDragAction(button, options = {}) {
    if (!button) return;
    _setNekoIdleReturnDragPendingClasses(button, false);
    const state = button.__nekoIdleReturnDragActionState;
    if (!state) return;
    _logNekoIdleReturnDragDebug('finish', {
        buttonId: button.id,
        restoreArt: options.restoreArt !== false,
        tier: button.getAttribute('data-neko-idle-tier')
    });
    button.__nekoIdleReturnDragAssetUrl = '';
    state.active = false;
    state.token += 1;
    state.tier = _NEKO_IDLE_TIER_NONE;
    _setNekoIdleReturnDragActionClasses(button, false);
    _fadeOutNekoIdleCat1DragSound();

    if (options.restoreArt === false) return;
    _syncNekoIdleCat1AmbientSoundForTier(button.getAttribute('data-neko-idle-tier'));
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (tier === _NEKO_IDLE_TIER_NONE) return;
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            _getNekoIdleReturnCurrentArtUrl(button, tier),
            tier,
            { animate: false }
        );
    }
}

function _finishNekoIdleReturnDragActionForContainer(container, options = {}) {
    _finishNekoIdleReturnDragAction(_getNekoIdleReturnButtonFromContainer(container), options);
}

function _getNekoIdleReturnSubactionProfile(tier) {
    return _NEKO_IDLE_RETURN_SUBACTION_PROFILES[_normalizeNekoIdleReturnTier(tier)] || null;
}

function _getNekoIdleReturnSubactionProfileForButton(button) {
    return _getNekoIdleReturnSubactionProfile(button && button.getAttribute('data-neko-idle-tier'));
}

function _getNekoIdleReturnSubactionState(button, profile) {
    if (!button || !profile) return null;
    const currentState = button.__nekoIdleReturnSubactionState;
    if (currentState && currentState.profile === profile) {
        return currentState;
    }
    if (currentState) {
        _cancelNekoIdleReturnSubactionState(currentState);
    }
    button.__nekoIdleReturnSubactionState = {
        profile: profile,
        substate: profile.idleSubstate,
        target: null,
        frame: 0,
        syncFrame: 0,
        observer: null,
        containerObserver: null,
        paused: false,
        lastStepAt: 0,
        facingRight: false,
        targetKind: '',
        settleTimer: 0,
        settleToken: 0,
        pendingWalkTimer: 0,
        pendingWalkToken: 0,
        pendingWalkDelayMs: 0,
        pendingWalkReady: false,
        walkSpeedRate: 1,
        walkPreviousDistance: 0,
        walkDistanceGrowthPx: 0,
        actionSettled: false,
        pairMoveTimer: 0,
        pairMoveToken: 0,
        pairMoveFrame: 0,
        pairMovePlan: null,
        inputRegionMotionSuppressed: false,
        compactFollowLastSurfaceRect: null,
        compactFollowLastAt: 0,
        compactFollowAnchorRatio: null,
        compactTopEdgeDropUntil: 0,
        compactTopEdgeRearmRequired: false,
        compactTopEdgeDropAnimationTimer: 0,
        compactTopEdgeDropCooldownTimer: 0,
        compactTopEdgeFastMoveCount: 0
    };
    button.__nekoIdleCat1Journey = button.__nekoIdleReturnSubactionState;
    return button.__nekoIdleReturnSubactionState;
}

function _getNekoIdleCat1Journey(button) {
    return _getNekoIdleReturnSubactionState(button, _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW);
}

function _getNekoIdleCat1ArtSource(button) {
    const profile = _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state) return profile.assets.idle();
    if (state.substate === profile.walkingSubstate) {
        return profile.assets.walking();
    }
    if (state.substate === profile.finishingSubstate) {
        return profile.assets.finishing();
    }
    return profile.assets.idle();
}

function _formatNekoIdleCat1WalkSpeedRate(rate) {
    const value = Number(rate);
    if (!Number.isFinite(value) || value <= 0) return '1';
    return Math.round(value * 1000) / 1000 + '';
}

function _dispatchNekoIdleCat1LayerRequest(container, active, reason, options = {}) {
    if (!container) return;
    const nextActive = !!active;
    const now = Date.now();
    const previousActive = !!container.__nekoIdleCat1LayerRequestActive;
    const previousAt = Number(container.__nekoIdleCat1LayerRequestAt) || 0;
    if (!options.force &&
        previousActive === nextActive &&
        (!nextActive || now - previousAt < _NEKO_IDLE_CAT1_LAYER_REQUEST_HEARTBEAT_MS)) {
        return;
    }
    container.__nekoIdleCat1LayerRequestActive = nextActive;
    container.__nekoIdleCat1LayerRequestAt = now;
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-layer-request', {
            detail: {
                active: nextActive,
                reason: reason || (nextActive ? 'compact-top-edge' : 'default-layer'),
                targetKind: nextActive ? _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE : '',
                containerId: container.id || '',
                timestamp: now
            }
        }));
    } catch (_) {}
}

function _clearNekoIdleCat1LayerReleaseTimer(container) {
    if (!container || !container.__nekoIdleCat1LayerReleaseTimer) return;
    clearTimeout(container.__nekoIdleCat1LayerReleaseTimer);
    container.__nekoIdleCat1LayerReleaseTimer = 0;
}

function _stopNekoIdleCat1LayerHeartbeat(container) {
    if (!container || !container.__nekoIdleCat1LayerHeartbeatTimer) return;
    clearInterval(container.__nekoIdleCat1LayerHeartbeatTimer);
    container.__nekoIdleCat1LayerHeartbeatTimer = 0;
}

function _startNekoIdleCat1LayerHeartbeat(container) {
    if (!container || container.__nekoIdleCat1LayerHeartbeatTimer) return;
    container.__nekoIdleCat1LayerHeartbeatTimer = setInterval(() => {
        if (!container.isConnected ||
            container.style.display === 'none' ||
            !container.__nekoIdleCat1LayerRequestActive) {
            _stopNekoIdleCat1LayerHeartbeat(container);
            _clearNekoIdleCat1LayerReleaseTimer(container);
            _dispatchNekoIdleCat1LayerRequest(container, false, 'layer-heartbeat-ended', { force: true });
            return;
        }
        _dispatchNekoIdleCat1LayerRequest(container, true, 'compact-top-edge-heartbeat', { force: true });
    }, _NEKO_IDLE_CAT1_LAYER_REQUEST_HEARTBEAT_MS);
}

function _syncNekoIdleCat1LayerRequest(container, active, reason) {
    if (!container) return;
    if (active) {
        _clearNekoIdleCat1LayerReleaseTimer(container);
        _startNekoIdleCat1LayerHeartbeat(container);
        _dispatchNekoIdleCat1LayerRequest(container, true, reason || 'compact-top-edge');
        return;
    }

    if (!container.__nekoIdleCat1LayerRequestActive) return;
    if (container.__nekoIdleCat1LayerReleaseTimer) return;
    container.__nekoIdleCat1LayerReleaseTimer = setTimeout(() => {
        container.__nekoIdleCat1LayerReleaseTimer = 0;
        if (!container.isConnected) return;
        _stopNekoIdleCat1LayerHeartbeat(container);
        _dispatchNekoIdleCat1LayerRequest(container, false, reason || 'default-layer', { force: true });
    }, _NEKO_IDLE_CAT1_LAYER_RELEASE_DELAY_MS);
}

function _reassertNekoIdleCat1LayerForFollow(container) {
    if (!container) return;
    const now = Date.now();
    const previousAt = Number(container.__nekoIdleCat1LayerFollowReassertAt) || 0;
    if (now - previousAt < _NEKO_IDLE_CAT1_LAYER_FOLLOW_REASSERT_MS) return;
    container.__nekoIdleCat1LayerFollowReassertAt = now;
    _dispatchNekoIdleCat1LayerRequest(container, true, 'compact-top-edge-follow', { force: true });
}

function _getNekoIdleScreenRectFromCompactSurfaceRect(rect) {
    const normalized = _normalizeNekoIdleScreenRect(rect);
    if (!normalized) return null;
    const explicitLeft = Number(rect && rect.screenLeft);
    const explicitTop = Number(rect && rect.screenTop);
    const left = Number.isFinite(explicitLeft)
        ? explicitLeft
        : (Number.isFinite(window.screenX) ? window.screenX : 0) + normalized.left;
    const top = Number.isFinite(explicitTop)
        ? explicitTop
        : (Number.isFinite(window.screenY) ? window.screenY : 0) + normalized.top;
    return {
        left: Math.round(left),
        top: Math.round(top),
        width: Math.round(normalized.width),
        height: Math.round(normalized.height)
    };
}

function _postNekoIdleCat1CompactMirrorState(payload) {
    const message = Object.assign({
        action: 'idle_cat1_compact_mirror_state',
        source: 'pet-window',
        lanlan_name: _getNekoIdleCurrentLanlanName(),
        timestamp: Date.now()
    }, payload || {});
    let dispatchedLocal = false;
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-compact-mirror-state', {
            detail: Object.assign({
                via: 'local'
            }, message)
        }));
        dispatchedLocal = true;
    } catch (_) {}

    const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
    if (!channel || typeof channel.postMessage !== 'function') return dispatchedLocal;
    try {
        channel.postMessage(message);
        return true;
    } catch (error) {
        if (typeof console !== 'undefined' && console.warn) {
            console.warn('[NekoIdleCat1] compact mirror postMessage failed:', error && error.message ? error.message : error);
        }
        return dispatchedLocal;
    }
}

function _setNekoIdleCat1CompactMirrorActive(button, container, active, options = {}) {
    if (!container) return false;
    const nextActive = !!active;
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (nextActive && container.__nekoIdleCat1CompactMirrorSettleTimer) {
        clearTimeout(container.__nekoIdleCat1CompactMirrorSettleTimer);
        container.__nekoIdleCat1CompactMirrorSettleTimer = 0;
    }
    if (!nextActive) {
        const inactiveReason = options.reason || 'inactive';
        if (
            inactiveReason === 'compact-surface-settled' &&
            !options.forceImmediate &&
            container.__nekoIdleCat1CompactMirrorActive
        ) {
            if (!container.__nekoIdleCat1CompactMirrorSettleTimer) {
                container.__nekoIdleCat1CompactMirrorSettleTimer = setTimeout(() => {
                    container.__nekoIdleCat1CompactMirrorSettleTimer = 0;
                    _setNekoIdleCat1CompactMirrorActive(button, container, false, {
                        reason: inactiveReason,
                        forceImmediate: true
                    });
                }, _NEKO_IDLE_CAT1_COMPACT_MIRROR_SETTLE_HIDE_DELAY_MS);
            }
            return true;
        }
        if (container.__nekoIdleCat1CompactMirrorSettleTimer) {
            clearTimeout(container.__nekoIdleCat1CompactMirrorSettleTimer);
            container.__nekoIdleCat1CompactMirrorSettleTimer = 0;
        }
        if (!container.__nekoIdleCat1CompactMirrorActive) return true;
        container.__nekoIdleCat1CompactMirrorActive = false;
        container.classList.remove('is-cat1-compact-mirror-active');
        _postNekoIdleCat1CompactMirrorState({
            active: false,
            reason: options.reason || 'inactive',
            containerId: container.id || ''
        });
        return true;
    }

    const surfaceScreenRect = _getNekoIdleScreenRectFromCompactSurfaceRect(options.surfaceRect);
    const catRect = typeof container.getBoundingClientRect === 'function'
        ? container.getBoundingClientRect()
        : null;
    if (!surfaceScreenRect || !catRect || catRect.width <= 0 || catRect.height <= 0) return false;
    const target = options.target || null;
    const anchorRatio = target && Number.isFinite(Number(target.anchorRatio))
        ? Math.max(0, Math.min(1, Number(target.anchorRatio)))
        : (state && Number.isFinite(Number(state.compactFollowAnchorRatio))
            ? Math.max(0, Math.min(1, Number(state.compactFollowAnchorRatio)))
            : 0.5);
    const posted = _postNekoIdleCat1CompactMirrorState({
        active: true,
        reason: options.reason || 'compact-follow',
        containerId: container.id || '',
        surfaceScreenRect: surfaceScreenRect,
        anchorRatio: anchorRatio,
        catRect: {
            width: Math.round(catRect.width),
            height: Math.round(catRect.height)
        },
        overlapPx: _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_OVERLAP_PX,
        sidePaddingPx: _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX,
        assetUrl: options.assetUrl || _getNekoIdleReturnAssetUrl(_NEKO_IDLE_TIER_CAT1),
        facingRight: !!(state && state.facingRight)
    });
    if (!posted) return false;
    container.__nekoIdleCat1CompactMirrorActive = true;
    container.classList.add('is-cat1-compact-mirror-active');
    return true;
}

function _deactivateNekoIdleCat1CompactMirrors(reason) {
    _forEachNekoIdleReturnButton((button) => {
        const container = _getNekoIdleReturnContainerFromButton(button);
        if (container) {
            _setNekoIdleCat1CompactMirrorActive(button, container, false, { reason: reason || 'inactive' });
        }
    });
}

function _setNekoIdleCat1Classes(button, state) {
    if (!button) return;
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const container = _getNekoIdleReturnContainerFromButton(button);
    const substate = state ? state.substate : profile.idleSubstate;
    const paused = !!(state && state.paused);
    const facingRight = !!(state && state.facingRight);
    const targetKind = state && state.targetKind ? state.targetKind : '';
    const onCompactTopEdge = targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    button.classList.toggle(profile.classNames.walking, substate === profile.walkingSubstate);
    button.classList.toggle(profile.classNames.finishing, substate === profile.finishingSubstate);
    button.classList.toggle(profile.classNames.facingRight, facingRight);
    button.classList.toggle(profile.classNames.paused, paused);
    button.classList.toggle(profile.classNames.compactTopEdge, onCompactTopEdge);
    button.setAttribute(profile.dataAttributes.substate, substate);
    if (targetKind) {
        button.setAttribute(profile.dataAttributes.targetKind, targetKind);
    } else {
        button.removeAttribute(profile.dataAttributes.targetKind);
    }
    const speedRate = substate === profile.walkingSubstate
        ? _formatNekoIdleCat1WalkSpeedRate(state && state.walkSpeedRate)
        : '';
    if (speedRate) {
        button.setAttribute('data-neko-cat1-walk-speed-rate', speedRate);
        button.style.setProperty('--neko-idle-cat1-walk-speed-rate', speedRate);
    } else {
        button.removeAttribute('data-neko-cat1-walk-speed-rate');
        button.style.removeProperty('--neko-idle-cat1-walk-speed-rate');
    }
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        if (speedRate) {
            art.setAttribute('data-neko-gif-playback-rate', speedRate);
            art.style.setProperty('--neko-idle-gif-playback-rate', speedRate);
            if (substate === profile.walkingSubstate) {
                _applyNekoIdleGifPlaybackRate(art, profile.assets.walking(), state && state.walkSpeedRate);
            }
        } else {
            art.removeAttribute('data-neko-gif-playback-rate');
            art.style.removeProperty('--neko-idle-gif-playback-rate');
            _clearNekoIdleGifPlaybackSource(art);
        }
    }
    if (container) {
        container.setAttribute(profile.dataAttributes.substate, substate);
        container.setAttribute(profile.dataAttributes.facing, facingRight ? 'right' : 'left');
        if (targetKind) {
            container.setAttribute(profile.dataAttributes.targetKind, targetKind);
        } else {
            container.removeAttribute(profile.dataAttributes.targetKind);
        }
        container.classList.toggle(profile.classNames.walking, substate === profile.walkingSubstate);
        container.classList.toggle(profile.classNames.finishing, substate === profile.finishingSubstate);
        container.classList.toggle(profile.classNames.paused, paused);
        container.classList.toggle(profile.classNames.compactTopEdge, onCompactTopEdge);
        container.style.setProperty(
            'z-index',
            onCompactTopEdge ? _NEKO_IDLE_RETURN_COMPACT_SURFACE_Z_INDEX : _NEKO_IDLE_RETURN_DEFAULT_Z_INDEX,
            onCompactTopEdge ? 'important' : ''
        );
        _syncNekoIdleCat1LayerRequest(
            container,
            onCompactTopEdge,
            onCompactTopEdge ? 'compact-top-edge' : 'default-layer'
        );
        if (!onCompactTopEdge) {
            _setNekoIdleCat1CompactMirrorActive(button, container, false, { reason: 'default-layer' });
        }
        if (speedRate) {
            container.setAttribute('data-neko-cat1-walk-speed-rate', speedRate);
            container.style.setProperty('--neko-idle-cat1-walk-speed-rate', speedRate);
        } else {
            container.removeAttribute('data-neko-cat1-walk-speed-rate');
            container.style.removeProperty('--neko-idle-cat1-walk-speed-rate');
        }
    }
}

function _cancelNekoIdleCat1Frame(state) {
    if (state && state.frame) {
        window.cancelAnimationFrame(state.frame);
        state.frame = 0;
    }
    if (state && state.inputRegionMotionSuppressed) {
        _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-motion-cancel');
    }
}

function _cancelNekoIdleCat1SyncFrame(state) {
    if (state && state.syncFrame) {
        window.cancelAnimationFrame(state.syncFrame);
        state.syncFrame = 0;
    }
}

function _disconnectNekoIdleCat1Observer(state) {
    if (state && state.observer) {
        try { state.observer.disconnect(); } catch (_) {}
        state.observer = null;
    }
    if (state && state.containerObserver) {
        try { state.containerObserver.disconnect(); } catch (_) {}
        state.containerObserver = null;
    }
}

function _cancelNekoIdleReturnSubactionSettleTimer(state) {
    if (!state) return;
    if (state.settleTimer) {
        clearTimeout(state.settleTimer);
        state.settleTimer = 0;
    }
    state.settleToken = (state.settleToken || 0) + 1;
}

function _cancelNekoIdleReturnPendingWalk(state) {
    if (!state) return;
    if (state.pendingWalkTimer) {
        clearTimeout(state.pendingWalkTimer);
        state.pendingWalkTimer = 0;
    }
    state.pendingWalkToken = (state.pendingWalkToken || 0) + 1;
    state.pendingWalkDelayMs = 0;
    state.pendingWalkReady = false;
}

function _cancelNekoIdleCat1PairMove(state) {
    if (!state) return;
    const activePlan = state.pairMovePlan;
    if (activePlan) {
        _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-pair-move-cancel', activePlan);
    }
    if (state.pairMoveTimer) {
        clearTimeout(state.pairMoveTimer);
        state.pairMoveTimer = 0;
    }
    if (state.pairMoveFrame) {
        window.cancelAnimationFrame(state.pairMoveFrame);
        state.pairMoveFrame = 0;
    }
    state.pairMoveToken = (state.pairMoveToken || 0) + 1;
    state.pairMovePlan = null;
}

function _interruptNekoIdleCat1PairMoveForRetarget(button, state) {
    if (!button || !state || (!state.pairMovePlan && !state.pairMoveFrame)) return false;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    _cancelNekoIdleCat1PairMove(state);
    if (state.substate === profile.idleSubstate) {
        state.target = null;
        state.targetKind = '';
        state.actionSettled = false;
        _resetNekoIdleCat1WalkSpeed(state);
        _setNekoIdleCat1Classes(button, state);
        const art = button.querySelector('.neko-idle-return-art');
        if (art) {
            _setNekoIdleReturnArtSource(art, profile.assets.idle(), profile.tier, { animate: false });
        }
    }
    return true;
}

function _resetNekoIdleCat1WalkSpeed(state) {
    if (!state) return;
    state.walkSpeedRate = 1;
    state.walkPreviousDistance = 0;
    state.walkDistanceGrowthPx = 0;
}

function _getNekoIdleNowMs() {
    return (typeof performance !== 'undefined' && typeof performance.now === 'function')
        ? performance.now()
        : Date.now();
}

function _resetNekoIdleCat1CompactFollowState(state, options = {}) {
    if (!state) return;
    state.compactFollowLastSurfaceRect = null;
    state.compactFollowLastAt = 0;
    state.compactFollowAnchorRatio = null;
    state.compactTopEdgeFastMoveCount = 0;
    if (!options.keepDropCooldown) {
        state.compactTopEdgeDropUntil = 0;
    }
}

function _clearNekoIdleCat1CompactTopEdgeDropTimers(state) {
    if (!state) return;
    if (state.compactTopEdgeDropAnimationTimer) {
        clearTimeout(state.compactTopEdgeDropAnimationTimer);
        state.compactTopEdgeDropAnimationTimer = 0;
    }
    if (state.compactTopEdgeDropCooldownTimer) {
        clearTimeout(state.compactTopEdgeDropCooldownTimer);
        state.compactTopEdgeDropCooldownTimer = 0;
    }
}

function _getNekoIdleCat1CompactTopEdgeTargetDistance(container) {
    const surfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const target = _getNekoIdleCat1CompactTopEdgeTarget(container, surfaceRect);
    return target && Number.isFinite(Number(target.distance)) ? Number(target.distance) : Infinity;
}

function _updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove(container) {
    const button = _getNekoIdleReturnButtonFromContainer(container);
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !container) {
        return {
            rearmed: false,
            shouldSync: false
        };
    }

    const compactTopEdgeRearmWasRequired = !!state.compactTopEdgeRearmRequired ||
        state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE ||
        !!(Number(state.compactTopEdgeDropUntil) || 0);
    const distance = _getNekoIdleCat1CompactTopEdgeTargetDistance(container);
    if (!Number.isFinite(distance)) {
        state.compactTopEdgeRearmRequired = false;
        state.compactTopEdgeDropUntil = 0;
        state.compactFollowAnchorRatio = null;
        return {
            rearmed: false,
            shouldSync: false
        };
    }

    const rearmed = distance <= _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_REARM_DISTANCE_PX;
    if (rearmed) {
        state.compactTopEdgeRearmRequired = false;
        state.compactTopEdgeDropUntil = 0;
        return {
            rearmed: true,
            shouldSync: true
        };
    }

    if (!compactTopEdgeRearmWasRequired) {
        return {
            rearmed: false,
            shouldSync: false
        };
    }

    state.compactTopEdgeRearmRequired = true;
    state.compactFollowAnchorRatio = null;
    state.targetKind = '';
    return {
        rearmed: false,
        shouldSync: false
    };
}

function _cancelNekoIdleReturnSubactionState(state, options = {}) {
    _clearNekoIdleCat1CompactTopEdgeDropTimers(state);
    _cancelNekoIdleCat1Frame(state);
    _cancelNekoIdleCat1SyncFrame(state);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleCat1PairMove(state);
    _resetNekoIdleCat1WalkSpeed(state);
    _resetNekoIdleCat1CompactFollowState(state);
    if (!options.preserveObservers) {
        _disconnectNekoIdleCat1Observer(state);
    }
}

function _cancelNekoIdleCat1Journey(button, options = {}) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state) return;
    _cancelNekoIdleReturnSubactionState(state, {
        preserveObservers: options.preserveObservers === true
    });
    _clearNekoIdleCat1WalkApproachSide(_getNekoIdleReturnContainerFromButton(button));
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    state.substate = profile.idleSubstate;
    state.target = null;
    state.paused = false;
    state.lastStepAt = 0;
    state.facingRight = false;
    state.targetKind = '';
    state.actionSettled = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _resetNekoIdleCat1CompactFollowState(state);
    _setNekoIdleCat1Classes(button, state);
    if (options.resetArt) {
        const art = button.querySelector('.neko-idle-return-art');
        if (art) {
            _setNekoIdleReturnArtSource(
                art,
                profile.assets.idle(),
                profile.tier,
                { animate: false }
            );
        }
    }
}

function _cancelNekoIdleCat1JourneyForContainer(container, options = {}) {
    _cancelNekoIdleCat1Journey(_getNekoIdleReturnButtonFromContainer(container), {
        resetArt: options.resetArt !== false,
        preserveObservers: options.preserveObservers === true
    });
}

function _scheduleNekoIdleCat1JourneySyncForContainer(container) {
    const button = _getNekoIdleReturnButtonFromContainer(container);
    if (button) {
        _scheduleNekoIdleCat1JourneySync(button);
    }
}

function _forEachNekoIdleReturnButton(callback) {
    if (typeof callback !== 'function') return;
    document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach(callback);
}

function _interruptNekoIdleCat1PairMovesForRetarget(options = {}) {
    const scheduleSync = options.scheduleSync !== false;
    let interrupted = false;
    _forEachNekoIdleReturnButton((button) => {
        const state = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!_interruptNekoIdleCat1PairMoveForRetarget(button, state)) return;
        interrupted = true;
        if (scheduleSync) {
            _scheduleNekoIdleCat1JourneySync(button);
        }
    });
    return interrupted;
}

function _isNekoIdleCompactSurfaceDragging() {
    const body = document && document.body;
    return !!(_nekoIdleCompactSurfaceDragging ||
        (body && body.classList && body.classList.contains('react-chat-window-dragging')));
}

function _scheduleNekoIdleCompactSurfaceSettledSync(delayMs) {
    if (_nekoIdleCompactSurfaceSettleTimer) {
        clearTimeout(_nekoIdleCompactSurfaceSettleTimer);
        _nekoIdleCompactSurfaceSettleTimer = 0;
    }

    const waitMs = Math.max(0, Number(delayMs) || _NEKO_IDLE_CAT1_COMPACT_SURFACE_SETTLE_SYNC_MS);
    _nekoIdleCompactSurfaceSettleTimer = setTimeout(() => {
        _nekoIdleCompactSurfaceSettleTimer = 0;
        if (_isNekoIdleCompactSurfaceDragging()) {
            return;
        }
        _forEachNekoIdleReturnButton((button) => {
            _scheduleNekoIdleCat1JourneySync(button);
        });
    }, waitMs);
}

function _handleNekoIdleCompactSurfaceMoveState(detail) {
    const dragging = !!(detail && detail.dragging);
    const resizeActive = !!(detail && detail.resizeActive);
    const activeSurfaceAdjustment = dragging || resizeActive;
    const heartbeat = !!(detail && detail.heartbeat);
    _nekoIdleCompactSurfaceDragging = activeSurfaceAdjustment;
    const followedCompactSurface = _syncNekoIdleCat1CompactTopEdgeSurfaceFollow(detail);
    if (!heartbeat) {
        _interruptNekoIdleCat1PairMovesForRetarget({ scheduleSync: !activeSurfaceAdjustment });
    }
    if (heartbeat) return;
    if (activeSurfaceAdjustment) {
        if (_nekoIdleCompactSurfaceSettleTimer) {
            clearTimeout(_nekoIdleCompactSurfaceSettleTimer);
            _nekoIdleCompactSurfaceSettleTimer = 0;
        }
        return;
    }
    if (!followedCompactSurface) {
        _deactivateNekoIdleCat1CompactMirrors('compact-surface-idle');
    }
    if (followedCompactSurface) return;
    _scheduleNekoIdleCompactSurfaceSettledSync(_NEKO_IDLE_CAT1_COMPACT_SURFACE_SETTLE_SYNC_MS);
}

function _shouldRecheckNekoIdleCat1AfterManualMove(detail) {
    if (!detail || !Number.isFinite(Number(detail.movedDistancePx))) return true;
    return Number(detail.movedDistancePx) >= _NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX;
}

function _getNekoIdleRectCenterMoveDistance(previousRect, nextRect) {
    const previous = _normalizeNekoIdleScreenRect(previousRect);
    const next = _normalizeNekoIdleScreenRect(nextRect);
    if (!previous || !next) return Infinity;
    const previousX = previous.left + previous.width / 2;
    const previousY = previous.top + previous.height / 2;
    const nextX = next.left + next.width / 2;
    const nextY = next.top + next.height / 2;
    return Math.hypot(nextX - previousX, nextY - previousY);
}

function _rememberNekoIdleCat1CompactFollowSurface(state, surfaceRect, nowMs) {
    if (!state) return;
    const normalized = _normalizeNekoIdleScreenRect(surfaceRect);
    state.compactFollowLastSurfaceRect = normalized;
    state.compactFollowLastAt = normalized ? (Number(nowMs) || _getNekoIdleNowMs()) : 0;
}

function _rememberNekoIdleCat1CompactFollowAnchor(state, surfaceRect, target) {
    if (!state) return;
    if (target &&
        target.anchorRatio !== null &&
        target.anchorRatio !== undefined &&
        Number.isFinite(Number(target.anchorRatio))) {
        state.compactFollowAnchorRatio = Math.max(0, Math.min(1, Number(target.anchorRatio)));
    }
}

function _getNekoIdleCompactSurfaceFollowRect(detail) {
    const currentRect = _getNekoIdleChatCompactSurfaceRect();
    if (currentRect) return currentRect;
    if (detail && detail.visible !== false) {
        return _normalizeNekoIdleScreenRect(detail);
    }
    return null;
}

function _isNekoIdleCat1SettledOnCompactTopEdge(state, profile) {
    return !!(state &&
        profile &&
        state.substate === profile.idleSubstate &&
        state.actionSettled &&
        state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE);
}

function _getNekoIdleCat1CompactFollowSpeed(state, surfaceRect, nowMs) {
    const previousRect = state && state.compactFollowLastSurfaceRect;
    const previousAt = state ? Number(state.compactFollowLastAt) || 0 : 0;
    if (!previousRect || !previousAt) {
        return {
            distancePx: 0,
            speedPxPerSec: 0,
            hasPrevious: false
        };
    }
    const distancePx = _getNekoIdleRectCenterMoveDistance(previousRect, surfaceRect);
    const elapsedMs = Math.max(16, (Number(nowMs) || _getNekoIdleNowMs()) - previousAt);
    return {
        distancePx: Number.isFinite(distancePx) ? distancePx : Infinity,
        speedPxPerSec: Number.isFinite(distancePx) ? (distancePx * 1000) / elapsedMs : Infinity,
        elapsedMs: elapsedMs,
        hasPrevious: true
    };
}

function _finishNekoIdleCat1CompactTopEdgeDrop(button, state) {
    const latestState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!latestState || latestState !== state) return;
    _scheduleNekoIdleCat1JourneySync(button);
}

function _dropNekoIdleCat1FromCompactTopEdge(button, target, nowMs) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!state || !container) return;

    _clearNekoIdleCat1CompactTopEdgeDropTimers(state);
    _cancelNekoIdleCat1Frame(state);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleCat1PairMove(state);
    state.substate = profile.idleSubstate;
    state.target = null;
    state.lastStepAt = 0;
    state.targetKind = '';
    state.actionSettled = true;
    state.facingRight = !!(target && target.facingRight);
    state.compactTopEdgeDropUntil = (Number(nowMs) || _getNekoIdleNowMs()) + _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_COOLDOWN_MS;
    state.compactTopEdgeRearmRequired = true;
    _resetNekoIdleCat1WalkSpeed(state);
    _resetNekoIdleCat1CompactFollowState(state, { keepDropCooldown: true });

    const rect = container.getBoundingClientRect();
    if (rect && rect.width > 0 && rect.height > 0) {
        const nextPosition = _clampNekoIdleCat1Position(
            rect.left,
            rect.top + _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_PX,
            rect.width,
            rect.height
        );
        _setNekoIdleCat1ContainerPosition(container, nextPosition.left, nextPosition.top);
    }

    container.classList.add('is-cat1-dropping-from-compact-top-edge');
    state.compactTopEdgeDropAnimationTimer = setTimeout(() => {
        const latestState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
        if (latestState !== state || !state.compactTopEdgeDropAnimationTimer) return;
        state.compactTopEdgeDropAnimationTimer = 0;
        if (!container.isConnected) return;
        container.classList.remove('is-cat1-dropping-from-compact-top-edge');
    }, _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_ANIMATION_MS);

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(art, profile.assets.idle(), profile.tier, { animate: false });
    }
    _setNekoIdleCat1Classes(button, state);
    state.compactTopEdgeDropCooldownTimer = setTimeout(() => {
        const latestState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
        if (latestState !== state || !state.compactTopEdgeDropCooldownTimer) return;
        state.compactTopEdgeDropCooldownTimer = 0;
        _finishNekoIdleCat1CompactTopEdgeDrop(button, state);
    }, _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_COOLDOWN_MS);
}

function _syncNekoIdleCat1CompactTopEdgeSurfaceFollow(detail) {
    const surfaceRect = _getNekoIdleCompactSurfaceFollowRect(detail);
    if (!surfaceRect) return false;
    const nowMs = _getNekoIdleNowMs();
    const dragging = !!(detail && detail.dragging);
    const resizeActive = !!(detail && detail.resizeActive);
    const activeSurfaceAdjustment = dragging || resizeActive;
    let handled = false;

    _forEachNekoIdleReturnButton((button) => {
        const state = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
        if (!_isNekoIdleCat1SettledOnCompactTopEdge(state, profile)) return;
        if (state.compactTopEdgeDropUntil && nowMs < state.compactTopEdgeDropUntil) return;

        const container = _getNekoIdleReturnContainerFromButton(button);
        if (!container || container.style.display === 'none' || container.getAttribute('data-dragging') === 'true') return;
        const rawAnchorRatio = state.compactFollowAnchorRatio;
        const anchorRatio = rawAnchorRatio === null || rawAnchorRatio === undefined
            ? NaN
            : Number(rawAnchorRatio);
        const target = _getNekoIdleCat1CompactTopEdgeTarget(container, surfaceRect, {
            anchorRatio: Number.isFinite(anchorRatio) ? anchorRatio : null
        });
        if (!target) return;
        if (!Number.isFinite(anchorRatio)) {
            _rememberNekoIdleCat1CompactFollowAnchor(state, surfaceRect, target);
        }

        const motion = _getNekoIdleCat1CompactFollowSpeed(state, surfaceRect, nowMs);
        const fastMove = !resizeActive && motion.hasPrevious && (
            motion.speedPxPerSec > _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_STICK_MAX_SPEED_PX_PER_SEC ||
            (motion.elapsedMs <= 240 && motion.distancePx > _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_STICK_MAX_STEP_PX)
        );
        state.compactTopEdgeFastMoveCount = fastMove
            ? Math.min(
                _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_FAST_MOVE_COUNT,
                (Number(state.compactTopEdgeFastMoveCount) || 0) + 1
            )
            : 0;
        const tooFast = state.compactTopEdgeFastMoveCount >= _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_DROP_FAST_MOVE_COUNT;
        if (tooFast) {
            _setNekoIdleCat1CompactMirrorActive(button, container, false, { reason: 'drop-from-compact-top-edge' });
            _dropNekoIdleCat1FromCompactTopEdge(button, target, nowMs);
            handled = true;
            return;
        }

        _cancelNekoIdleReturnPendingWalk(state);
        _cancelNekoIdleCat1PairMove(state);
        state.target = null;
        state.actionSettled = true;
        state.targetKind = _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
        state.compactTopEdgeRearmRequired = false;
        _resetNekoIdleCat1WalkSpeed(state);
        _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
        _setNekoIdleCat1Classes(button, state);
        _reassertNekoIdleCat1LayerForFollow(container);
        if (activeSurfaceAdjustment) {
            _setNekoIdleCat1CompactMirrorActive(button, container, true, {
                reason: resizeActive ? 'compact-surface-resize' : 'compact-surface-drag',
                surfaceRect: surfaceRect,
                target: target
            });
        } else {
            _setNekoIdleCat1CompactMirrorActive(button, container, false, { reason: 'compact-surface-settled' });
        }
        _rememberNekoIdleCat1CompactFollowSurface(state, surfaceRect, nowMs);
        handled = true;
    });

    return handled;
}

function _isNekoIdleCat1Walking(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    return !!(state &&
        state.profile &&
        state.substate === state.profile.walkingSubstate &&
        !state.pairMovePlan &&
        !state.pairMoveFrame);
}

function _getNekoIdleCurrentLanlanName() {
    return (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
}

function _dispatchNekoIdleReturnBallManualMove(container, reason, extraDetail = {}) {
    _logNekoIdleReturnDragDebug('dispatch', {
        reason: reason,
        containerId: container && container.id,
        dragging: container && container.getAttribute && container.getAttribute('data-dragging'),
        movedDistancePx: extraDetail.movedDistancePx
    });
    window.dispatchEvent(new CustomEvent('neko:return-ball-manual-move', {
        detail: Object.assign({
            reason: reason,
            container: container
        }, extraDetail)
    }));
}

function _getNekoIdleReactChatMinimizedRect() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList || !shell.classList.contains('is-minimized')) return null;
    if (shell.classList.contains('is-collapsing') || shell.classList.contains('is-expanding')) return null;
    if (typeof shell.getBoundingClientRect !== 'function') return null;
    const rect = shell.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return rect;
}

function _getNekoIdleReactChatMinimizedShell() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList || !shell.classList.contains('is-minimized')) return null;
    if (shell.classList.contains('is-collapsing') ||
        shell.classList.contains('is-expanding') ||
        shell.classList.contains('is-dragging') ||
        shell.classList.contains('is-idle-docked')) {
        return null;
    }
    if (typeof shell.getBoundingClientRect !== 'function') return null;
    const rect = shell.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return shell;
}

function _getNekoIdleReactChatExpandedShell() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList || shell.classList.contains('is-minimized')) return null;
    if (shell.classList.contains('is-collapsing') ||
        shell.classList.contains('is-expanding') ||
        shell.classList.contains('is-dragging') ||
        shell.classList.contains('is-idle-docked')) {
        return null;
    }
    if (typeof shell.getBoundingClientRect !== 'function') return null;
    const rect = shell.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;
    return shell;
}

function _normalizeNekoIdleScreenRect(rect) {
    if (!rect || typeof rect !== 'object') return null;
    const left = Number.isFinite(Number(rect.left)) ? Number(rect.left) : Number(rect.x);
    const top = Number.isFinite(Number(rect.top)) ? Number(rect.top) : Number(rect.y);
    const width = Number(rect.width);
    const height = Number(rect.height);
    if (!Number.isFinite(left) || !Number.isFinite(top) ||
        !Number.isFinite(width) || !Number.isFinite(height) ||
        width <= 0 || height <= 0) {
        return null;
    }
    return {
        left: left,
        top: top,
        width: width,
        height: height,
        right: left + width,
        bottom: top + height
    };
}

function _getNekoIdleVisibleElementRect(element) {
    if (!element || element.hidden || typeof element.getBoundingClientRect !== 'function') return null;
    try {
        const style = typeof window.getComputedStyle === 'function'
            ? window.getComputedStyle(element)
            : null;
        if (style && (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) <= 0.01)) {
            return null;
        }
    } catch (_) {}
    return _normalizeNekoIdleScreenRect(element.getBoundingClientRect());
}

function _getNekoIdleReactChatCompactSurfaceRect() {
    const overlay = document.getElementById('react-chat-window-overlay');
    if (overlay && overlay.hidden) return null;
    const shell = document.getElementById('react-chat-window-shell');
    if (!shell || !shell.classList) return null;
    if (shell.getAttribute('data-chat-surface-mode') !== 'compact') return null;
    if (shell.classList.contains('is-minimized') ||
        shell.classList.contains('is-collapsing') ||
        shell.classList.contains('is-expanding') ||
        shell.classList.contains('is-idle-docked')) {
        return null;
    }

    const root = document.getElementById('react-chat-window-root');
    if (!root || !shell.contains(root)) return null;
    const candidates = [];
    const surfaceShell = root.querySelector('.compact-chat-surface-shell');
    if (surfaceShell) candidates.push(surfaceShell);
    root.querySelectorAll(
        '[data-compact-geometry-owner="surface"][data-compact-geometry-item="input"], ' +
        '[data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]'
    ).forEach((element) => {
        candidates.push(element);
    });

    for (let i = 0; i < candidates.length; i += 1) {
        const rect = _getNekoIdleVisibleElementRect(candidates[i]);
        if (rect) return rect;
    }
    return null;
}

function _getNekoIdleDesktopCompactSurfaceRect() {
    const state = _nekoIdleDesktopCompactSurfaceState;
    if (!state || !state.visible || !state.screenRect) return null;
    if (_nekoIdleDesktopChatMinimizedState &&
        _nekoIdleDesktopChatMinimizedState.minimized &&
        _isNekoIdleDesktopStateNewerThan(_nekoIdleDesktopChatMinimizedState.sourceUpdatedAt, state)) {
        return null;
    }
    if (Date.now() - (state.updatedAt || 0) > _NEKO_IDLE_DESKTOP_COMPACT_SURFACE_RECT_STALE_MS) return null;
    const screenRect = _normalizeNekoIdleScreenRect(state.screenRect);
    if (!screenRect) return null;
    const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
    const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
    return {
        left: screenRect.left - screenLeft,
        top: screenRect.top - screenTop,
        width: screenRect.width,
        height: screenRect.height,
        right: screenRect.right - screenLeft,
        bottom: screenRect.bottom - screenTop,
        screenLeft: screenRect.left,
        screenTop: screenRect.top,
        screenRight: screenRect.right,
        screenBottom: screenRect.bottom
    };
}

function _getNekoIdleChatCompactSurfaceRect() {
    return _getNekoIdleReactChatCompactSurfaceRect()
        || _getNekoIdleDesktopCompactSurfaceRect();
}

function _getNekoIdleDesktopChatMinimizedRect() {
    const state = _nekoIdleDesktopChatMinimizedState;
    if (!state || !state.minimized || !state.screenRect) return null;
    if (_nekoIdleDesktopCompactSurfaceState &&
        _nekoIdleDesktopCompactSurfaceState.visible &&
        _isNekoIdleDesktopStateNewerThan(_nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt, state)) {
        return null;
    }
    if (Date.now() - (state.updatedAt || 0) > _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS) return null;
    const screenRect = _normalizeNekoIdleScreenRect(state.screenRect);
    if (!screenRect) return null;
    const screenLeft = Number.isFinite(window.screenX) ? window.screenX : 0;
    const screenTop = Number.isFinite(window.screenY) ? window.screenY : 0;
    return {
        left: screenRect.left - screenLeft,
        top: screenRect.top - screenTop,
        width: screenRect.width,
        height: screenRect.height,
        right: screenRect.right - screenLeft,
        bottom: screenRect.bottom - screenTop,
        screenLeft: screenRect.left,
        screenTop: screenRect.top,
        screenRight: screenRect.right,
        screenBottom: screenRect.bottom
    };
}

function _isNekoIdleDesktopChatExpandedRecent() {
    const state = _nekoIdleDesktopChatMinimizedState;
    if (!state || state.minimized) return false;
    if (state.expandedRecent === false) return false;
    return Date.now() - (state.updatedAt || 0) <= _NEKO_IDLE_DESKTOP_CHAT_RECT_STALE_MS;
}

function _canNekoIdleCat1MoveSoloWithExpandedChat() {
    return !!(_getNekoIdleReactChatExpandedShell() || _isNekoIdleDesktopChatExpandedRecent());
}

function _getNekoIdleChatMinimizedRect() {
    return _getNekoIdleReactChatMinimizedRect()
        || _getNekoIdleDesktopChatMinimizedRect();
}

function _clampNekoIdleCat1Position(left, top, width, height) {
    return {
        left: Math.round(Math.max(0, Math.min(left, Math.max(0, window.innerWidth - width)))),
        top: Math.round(Math.max(0, Math.min(top, Math.max(0, window.innerHeight - height))))
    };
}

function _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect) {
    // The yarn ball's right side has trailing string space, so right-to-left approaches need an inward visual anchor.
    if (facingRight) return 0;
    const width = Number(chatRect && chatRect.width);
    if (!Number.isFinite(width) || width <= 0) return 0;
    return Math.max(0, Math.min(width, _NEKO_IDLE_CAT1_MINIMIZED_RIGHT_TO_LEFT_APPROACH_PX));
}

function _getNekoIdleCat1TargetMoveDirection(rect, targetLeft) {
    if (!rect || !Number.isFinite(Number(targetLeft))) return null;
    const dx = Number(targetLeft) - rect.left;
    if (Math.abs(dx) <= _NEKO_IDLE_CAT1_MINIMIZED_BACKWARD_RETREAT_TOLERANCE_PX) return null;
    return dx > 0;
}

function _resolveNekoIdleCat1TargetFacing(rect, target) {
    if (!target) return false;
    const moveFacingRight = _getNekoIdleCat1TargetMoveDirection(rect, target.left);
    if (moveFacingRight !== null) return moveFacingRight;
    if (Object.prototype.hasOwnProperty.call(target, 'lookFacingRight')) {
        return !!target.lookFacingRight;
    }
    return !!target.facingRight;
}

function _resolveNekoIdleCat1FinalTargetFacing(target) {
    if (!target) return false;
    if (Object.prototype.hasOwnProperty.call(target, 'lookFacingRight')) {
        return !!target.lookFacingRight;
    }
    return !!target.facingRight;
}

function _makeNekoIdleCat1SideTarget(rect, chatRect, options) {
    const facingRight = !!(options && options.facingRight);
    const rawLeft = Number(options && options.rawLeft);
    const approachOffsetPx = Number(options && options.approachOffsetPx) || 0;
    if (!Number.isFinite(rawLeft)) return null;
    const rawTop = chatRect.top + (chatRect.height - rect.height) / 2;
    const clamped = _clampNekoIdleCat1Position(rawLeft, rawTop, rect.width, rect.height);
    const targetCenterX = clamped.left + rect.width / 2;
    const targetCenterY = clamped.top + rect.height / 2;
    const currentCenterX = rect.left + rect.width / 2;
    const currentCenterY = rect.top + rect.height / 2;
    const dx = targetCenterX - currentCenterX;
    const dy = targetCenterY - currentCenterY;
    const moveFacingRight = _getNekoIdleCat1TargetMoveDirection(rect, clamped.left);
    return {
        left: clamped.left,
        top: clamped.top,
        distance: Math.hypot(dx, dy),
        facingRight: facingRight,
        lookFacingRight: facingRight,
        moveFacingRight: moveFacingRight,
        approachOffsetPx: approachOffsetPx,
        kind: _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE
    };
}

function _computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight) {
    const profile = _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const approachOffsetPx = _getNekoIdleCat1MinimizedSideApproachOffsetPx(lookFacingRight, chatRect);
    const rawLeft = lookFacingRight
        ? chatRect.left - rect.width - profile.target.gapPx
        : chatRect.right + profile.target.gapPx - approachOffsetPx;
    return _makeNekoIdleCat1SideTarget(rect, chatRect, {
        facingRight: lookFacingRight,
        rawLeft: rawLeft,
        approachOffsetPx: approachOffsetPx
    });
}

// #1749 的本意：在毛球两侧站位点里挑“朝毛球前进即可到达”的那个，避免明显倒退。
// 仅用于本次走路“首次”决定接近侧；之后由提交侧 + 滞回保持，避免每帧重判导致横跳。
function _pickNekoIdleCat1ForwardSideTarget(rect, chatRect) {
    const catCenterX = rect.left + rect.width / 2;
    const chatCenterX = chatRect.left + chatRect.width / 2;
    const lookFacingRight = chatCenterX > catCenterX;
    const sideTarget = _computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight);
    if (!sideTarget || sideTarget.moveFacingRight === null || sideTarget.moveFacingRight === lookFacingRight) {
        return sideTarget;
    }
    const alternateTarget = _computeNekoIdleCat1SideTargetForLook(rect, chatRect, !lookFacingRight);
    if (alternateTarget &&
        (alternateTarget.moveFacingRight === null || alternateTarget.moveFacingRight === lookFacingRight)) {
        return alternateTarget;
    }
    return sideTarget;
}

function _clearNekoIdleCat1WalkApproachSide(container) {
    if (container && _NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP in container) {
        delete container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP];
    }
}

// #1754：判定毛球中心是否已落进猫体 rect（猫已贴上球），贴球后据此避免再朝倒退方向取侧而前后蹭动。
function _isNekoIdleRectCenterInsideRect(innerRect, outerRect) {
    if (!innerRect || !outerRect) return false;
    const innerLeft = Number(innerRect.left);
    const innerTop = Number(innerRect.top);
    const innerWidth = Number(innerRect.width);
    const innerHeight = Number(innerRect.height);
    const outerLeft = Number(outerRect.left);
    const outerTop = Number(outerRect.top);
    const outerWidth = Number(outerRect.width);
    const outerHeight = Number(outerRect.height);
    if (!Number.isFinite(innerLeft) || !Number.isFinite(innerTop) ||
        !Number.isFinite(innerWidth) || !Number.isFinite(innerHeight) ||
        !Number.isFinite(outerLeft) || !Number.isFinite(outerTop) ||
        !Number.isFinite(outerWidth) || !Number.isFinite(outerHeight) ||
        innerWidth <= 0 || innerHeight <= 0 || outerWidth <= 0 || outerHeight <= 0) {
        return false;
    }
    const outerRight = Number.isFinite(Number(outerRect.right)) ? Number(outerRect.right) : outerLeft + outerWidth;
    const outerBottom = Number.isFinite(Number(outerRect.bottom)) ? Number(outerRect.bottom) : outerTop + outerHeight;
    const innerCenterX = innerLeft + innerWidth / 2;
    const innerCenterY = innerTop + innerHeight / 2;
    return innerCenterX >= outerLeft && innerCenterX <= outerRight &&
        innerCenterY >= outerTop && innerCenterY <= outerBottom;
}

// #1754：贴球后“原地以当前朝向站住”的侧目标（distance 0、moveFacingRight null，不再走动）。
function _makeNekoIdleCat1CurrentSideTarget(rect, chatRect, options) {
    const facingRight = !!(options && options.facingRight);
    return {
        left: rect.left,
        top: rect.top,
        distance: 0,
        facingRight: facingRight,
        lookFacingRight: facingRight,
        moveFacingRight: null,
        approachOffsetPx: _getNekoIdleCat1MinimizedSideApproachOffsetPx(facingRight, chatRect),
        kind: _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE
    };
}

function _getNekoIdleCat1SideTarget(container, chatRect) {
    if (!container || !chatRect || typeof container.getBoundingClientRect !== 'function') return null;
    const rect = container.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;

    // 提交本次走路的接近侧，且只在“猫已整体越到毛球另一侧”时才重选。
    // 若像旧实现那样每帧用 catCenter vs chatCenter 重判接近侧：两侧站位点都落在毛球“对侧”，
    // 猫一旦进入两站位点之间的区间，每帧目标都被指到对面 → 跨过球心就翻面、永不收敛，
    // 表现为返回猫贴着毛球一直抽搐（#1749 残留）。提交侧 + 滞回即可根除该横跳。
    const catCenterX = rect.left + rect.width / 2;
    const committed = container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP];
    const hasCommitted = committed === true || committed === false;
    let lookFacingRight = null;
    if (hasCommitted) {
        if (catCenterX >= chatRect.left && catCenterX <= chatRect.right) {
            lookFacingRight = committed; // 仍在毛球水平跨度内：保持提交侧，不在球心附近翻面
        } else if (committed === true && catCenterX > chatRect.right) {
            lookFacingRight = false; // 已整体越到毛球右侧 → 重选接近侧
        } else if (committed === false && catCenterX < chatRect.left) {
            lookFacingRight = true; // 已整体越到毛球左侧 → 重选接近侧
        } else {
            lookFacingRight = committed; // 在毛球外、且就处于提交侧 → 保持
        }
    }

    const target = lookFacingRight === null
        ? _pickNekoIdleCat1ForwardSideTarget(rect, chatRect)
        : _computeNekoIdleCat1SideTargetForLook(rect, chatRect, lookFacingRight);

    // #1754：毛球中心已落进猫体 rect（猫已贴上球），且到该侧位点仍需倒退（moveFacingRight 与朝向
    // 相反）时就别再走过去——原地以当前朝向站住，避免贴球时反复前后蹭动抽搐。提交侧随之钉在当前朝向。
    if (target &&
        _isNekoIdleRectCenterInsideRect(chatRect, rect) &&
        target.moveFacingRight !== null &&
        target.moveFacingRight !== target.lookFacingRight) {
        const currentSideTarget = _makeNekoIdleCat1CurrentSideTarget(rect, chatRect, {
            facingRight: target.lookFacingRight
        });
        if (currentSideTarget) {
            container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP] = !!currentSideTarget.lookFacingRight;
            return currentSideTarget;
        }
    }

    if (target) {
        container[_NEKO_IDLE_CAT1_WALK_SIDE_COMMIT_PROP] = !!target.lookFacingRight;
    }
    return target;
}

function _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect) {
    if (!surfaceRect) return null;
    const capInset = Math.max(
        _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX,
        surfaceRect.height / 2 + _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_SIDE_PADDING_PX
    );
    const edgePadding = Math.min(capInset, Math.max(0, surfaceRect.width / 2));
    const minEdgeCenterX = surfaceRect.left + edgePadding;
    const maxEdgeCenterX = surfaceRect.right - edgePadding;
    return {
        minEdgeCenterX: minEdgeCenterX,
        maxEdgeCenterX: maxEdgeCenterX,
        fallbackCenterX: surfaceRect.left + surfaceRect.width / 2
    };
}

function _getNekoIdleCat1CompactTopEdgeAnchorRatio(surfaceRect, targetEdgeCenterX) {
    const bounds = _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect);
    if (!bounds) return null;
    const span = bounds.maxEdgeCenterX - bounds.minEdgeCenterX;
    if (span <= 0) return 0.5;
    const ratio = (Number(targetEdgeCenterX) - bounds.minEdgeCenterX) / span;
    if (!Number.isFinite(ratio)) return null;
    return Math.max(0, Math.min(1, ratio));
}

function _getNekoIdleCat1CompactTopEdgeCenterFromAnchor(surfaceRect, anchorRatio) {
    const bounds = _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect);
    if (!bounds) return null;
    if (anchorRatio === null || anchorRatio === undefined || anchorRatio === '') return null;
    const ratio = Number(anchorRatio);
    if (!Number.isFinite(ratio)) return null;
    const span = bounds.maxEdgeCenterX - bounds.minEdgeCenterX;
    if (span <= 0) return bounds.fallbackCenterX;
    return bounds.minEdgeCenterX + Math.max(0, Math.min(1, ratio)) * span;
}

function _getNekoIdleCat1CompactTopEdgeTarget(container, surfaceRect, options = {}) {
    if (!container || !surfaceRect || typeof container.getBoundingClientRect !== 'function') return null;
    const rect = container.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return null;

    const catCenterX = rect.left + rect.width / 2;
    const bounds = _getNekoIdleCat1CompactTopEdgeBounds(surfaceRect);
    if (!bounds) return null;
    const anchoredCenterX = _getNekoIdleCat1CompactTopEdgeCenterFromAnchor(surfaceRect, options.anchorRatio);
    const targetEdgeCenterX = Number.isFinite(anchoredCenterX)
        ? anchoredCenterX
        : (bounds.maxEdgeCenterX >= bounds.minEdgeCenterX
            ? Math.max(bounds.minEdgeCenterX, Math.min(catCenterX, bounds.maxEdgeCenterX))
            : bounds.fallbackCenterX);
    const anchorRatio = _getNekoIdleCat1CompactTopEdgeAnchorRatio(surfaceRect, targetEdgeCenterX);
    const rawLeft = targetEdgeCenterX - rect.width / 2;

    const rawTop = surfaceRect.top - rect.height + _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_OVERLAP_PX;
    const clamped = _clampNekoIdleCat1Position(rawLeft, rawTop, rect.width, rect.height);
    const targetCenterX = clamped.left + rect.width / 2;
    const targetCenterY = clamped.top + rect.height / 2;
    const currentCenterX = rect.left + rect.width / 2;
    const currentCenterY = rect.top + rect.height / 2;
    const dx = targetCenterX - currentCenterX;
    const dy = targetCenterY - currentCenterY;
    return {
        left: clamped.left,
        top: clamped.top,
        distance: Math.hypot(dx, dy),
        facingRight: targetEdgeCenterX > catCenterX,
        anchorRatio: anchorRatio,
        kind: _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
    };
}

function _getNekoIdleCat1Target(container, chatRect, options = {}) {
    const minimizedSideTarget = _getNekoIdleCat1SideTarget(container, chatRect);
    if (minimizedSideTarget) {
        return minimizedSideTarget;
    }

    const compactSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const compactTarget = _getNekoIdleCat1CompactTopEdgeTarget(container, compactSurfaceRect, {
        anchorRatio: options.anchorRatio
    });
    const compactBlocked = !!(options && options.compactTopEdgeBlocked);
    if (!compactBlocked &&
        compactTarget &&
        compactTarget.distance <= _NEKO_IDLE_CAT1_COMPACT_TOP_EDGE_FOLLOW_DISTANCE_PX) {
        return compactTarget;
    }
    return null;
}

function _setNekoIdleCat1ContainerPosition(container, left, top) {
    if (!container) return;
    container.style.left = `${Math.round(left)}px`;
    container.style.top = `${Math.round(top)}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
}

function _setNekoIdleCat1PairMoveChatPosition(shell, left, top) {
    if (!shell) return;
    shell.style.left = `${Math.round(left)}px`;
    shell.style.top = `${Math.round(top)}px`;
    shell.style.right = '';
    shell.style.bottom = '';
    shell.style.transform = 'none';
}

function _rememberNekoIdleDesktopChatPairMoveRect(screenRect) {
    const normalized = _normalizeNekoIdleScreenRect(screenRect);
    if (!normalized) return null;
    const updatedAt = Date.now();
    _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
        true,
        normalized,
        updatedAt,
        updatedAt,
        false
    );
    _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
        false,
        null,
        updatedAt,
        updatedAt
    );
    return normalized;
}

function _getNekoIdleDesktopChatPairMoveSignature(screenRect) {
    const normalized = _normalizeNekoIdleScreenRect(screenRect);
    if (!normalized) return '';
    return [
        normalized.left,
        normalized.top,
        normalized.width,
        normalized.height
    ].join(':');
}

function _dispatchNekoIdleDesktopChatPairMoveBounds(screenRect, options = {}) {
    if (_isNekoDesktopLinuxRuntime()) return false;
    const normalized = _rememberNekoIdleDesktopChatPairMoveRect(screenRect);
    if (!normalized) return false;
    const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
    if (!channel || typeof channel.postMessage !== 'function') return false;
    const now = Date.now();
    const force = !!(options && options.force);
    const signature = _getNekoIdleDesktopChatPairMoveSignature(normalized);
    if (!force) {
        if (signature && signature === _nekoIdleDesktopChatPairMoveLastDispatchSignature) return false;
        if (now - _nekoIdleDesktopChatPairMoveLastDispatchAt < _NEKO_IDLE_CAT1_DESKTOP_PAIR_MOVE_SYNC_MIN_MS) return false;
    }
    _nekoIdleDesktopChatPairMoveLastDispatchAt = now;
    _nekoIdleDesktopChatPairMoveLastDispatchSignature = signature;
    channel.postMessage({
        action: 'idle_chat_pair_move_bounds',
        source: 'cat1-pair-move',
        lanlan_name: _getNekoIdleCurrentLanlanName(),
        screenRect: {
            left: normalized.left,
            top: normalized.top,
            width: normalized.width,
            height: normalized.height
        },
        timestamp: now
    });
    return true;
}

function _getNekoIdleCat1PairMoveChatTarget() {
    const shell = _getNekoIdleReactChatMinimizedShell();
    if (shell) {
        const rect = shell.getBoundingClientRect();
        if (rect && rect.width > 0 && rect.height > 0) {
            return {
                mode: 'dom',
                shell: shell,
                rect: rect
            };
        }
    }
    const desktopRect = _getNekoIdleDesktopChatMinimizedRect();
    if (desktopRect && desktopRect.width > 0 && desktopRect.height > 0) {
        return {
            mode: 'desktop',
            shell: null,
            rect: desktopRect,
            screenRect: {
                left: desktopRect.screenLeft,
                top: desktopRect.screenTop,
                width: desktopRect.width,
                height: desktopRect.height
            }
        };
    }
    return null;
}

function _clampNekoIdleCat1MoveVector(catRect, chatRect, desiredDx, desiredDy) {
    const minDx = chatRect ? Math.max(-catRect.left, -chatRect.left) : -catRect.left;
    const maxDx = chatRect
        ? Math.min(window.innerWidth - catRect.right, window.innerWidth - chatRect.right)
        : window.innerWidth - catRect.right;
    const minDy = chatRect ? Math.max(-catRect.top, -chatRect.top) : -catRect.top;
    const maxDy = chatRect
        ? Math.min(window.innerHeight - catRect.bottom, window.innerHeight - chatRect.bottom)
        : window.innerHeight - catRect.bottom;
    const dx = Math.max(minDx, Math.min(desiredDx, maxDx));
    const dy = Math.max(minDy, Math.min(desiredDy, maxDy));
    return {
        dx: dx,
        dy: dy,
        distance: Math.hypot(dx, dy)
    };
}

function _pickNekoIdleCat1MoveVector(catRect, chatRect, distance, minUsableDistance) {
    const attempts = 10;
    const fallbackAngles = [0, Math.PI, Math.PI / 2, -Math.PI / 2, Math.PI / 4, -Math.PI / 4, Math.PI * 3 / 4, -Math.PI * 3 / 4];
    for (let i = 0; i < attempts + fallbackAngles.length; i += 1) {
        const angle = i < attempts ? Math.random() * Math.PI * 2 : fallbackAngles[i - attempts];
        const vector = _clampNekoIdleCat1MoveVector(
            catRect,
            chatRect,
            Math.cos(angle) * distance,
            Math.sin(angle) * distance
        );
        if (vector.distance >= minUsableDistance) return vector;
    }
    return null;
}

function _hasNekoIdleCat1MoveVectorSpace(catRect, chatRect, distance, minUsableDistance) {
    const angles = [0, Math.PI, Math.PI / 2, -Math.PI / 2, Math.PI / 4, -Math.PI / 4, Math.PI * 3 / 4, -Math.PI * 3 / 4];
    for (let i = 0; i < angles.length; i += 1) {
        const angle = angles[i];
        const vector = _clampNekoIdleCat1MoveVector(
            catRect,
            chatRect,
            Math.cos(angle) * distance,
            Math.sin(angle) * distance
        );
        if (vector.distance >= minUsableDistance) return true;
    }
    return false;
}

function _getNekoIdleCat1PairMovePlan(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const config = profile.pairMove || {};
    const container = _getNekoIdleReturnContainerFromButton(button);
    const chatTarget = _getNekoIdleCat1PairMoveChatTarget();
    if (chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()) return null;
    const canMoveSolo = chatTarget ? false : _canNekoIdleCat1MoveSoloWithExpandedChat();
    if (!container || (!chatTarget && !canMoveSolo)) return null;
    if (container.getAttribute('data-dragging') === 'true') return null;
    if (_isNekoIdleReturnDragActionActive(button)) return null;
    const catRect = container.getBoundingClientRect();
    const chatRect = chatTarget ? chatTarget.rect : null;
    if (!catRect || catRect.width <= 0 || catRect.height <= 0) {
        return null;
    }
    if (chatTarget) {
        if (!chatRect || chatRect.width <= 0 || chatRect.height <= 0) return null;
        const target = _getNekoIdleCat1Target(container, chatRect, {
            compactTopEdgeBlocked: !!(state && state.compactTopEdgeRearmRequired)
        });
        if (!target || target.distance > profile.target.exitDistancePx) return null;
    }

    const minDistance = Math.max(1, Number(config.minDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DISTANCE_PX);
    const maxDistance = Math.max(minDistance, Number(config.maxDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX);
    const minUsableDistance = Math.max(1, Number(config.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX);
    const desiredDistance = minDistance + Math.random() * (maxDistance - minDistance);
    const moveVector = _pickNekoIdleCat1MoveVector(catRect, chatTarget ? chatRect : null, desiredDistance, minUsableDistance);
    if (!moveVector) return null;
    const speed = Math.max(1, Number(config.speedPxPerSec) || _NEKO_IDLE_CAT1_PAIR_MOVE_SPEED_PX_PER_SEC);
    const minDuration = Math.max(1, Number(config.minDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_DURATION_MS);
    const maxDuration = Math.max(minDuration, Number(config.maxDurationMs) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DURATION_MS);
    const durationMs = Math.max(minDuration, Math.min(maxDuration, Math.round(moveVector.distance / speed * 1000)));
    return {
        chatMode: chatTarget ? chatTarget.mode : 'solo',
        shell: chatTarget ? chatTarget.shell : null,
        container: container,
        catStartLeft: catRect.left,
        catStartTop: catRect.top,
        chatStartLeft: chatRect ? chatRect.left : null,
        chatStartTop: chatRect ? chatRect.top : null,
        chatStartScreenLeft: chatTarget && chatTarget.screenRect ? chatTarget.screenRect.left : null,
        chatStartScreenTop: chatTarget && chatTarget.screenRect ? chatTarget.screenRect.top : null,
        chatWidth: chatRect ? chatRect.width : null,
        chatHeight: chatRect ? chatRect.height : null,
        dx: moveVector.dx,
        dy: moveVector.dy,
        durationMs: durationMs
    };
}

function _easeNekoIdleCat1PairMove(progress) {
    const p = Math.max(0, Math.min(1, Number(progress) || 0));
    return p < 0.5
        ? 2 * p * p
        : 1 - Math.pow(-2 * p + 2, 2) / 2;
}

function _applyNekoIdleCat1PairMovePlan(plan, progress) {
    if (!plan || !plan.container) return;
    const eased = _easeNekoIdleCat1PairMove(progress);
    const offsetX = plan.dx * eased;
    const offsetY = plan.dy * eased;
    _setNekoIdleCat1ContainerPosition(plan.container, plan.catStartLeft + offsetX, plan.catStartTop + offsetY);
    if (plan.chatMode === 'desktop') {
        _dispatchNekoIdleDesktopChatPairMoveBounds({
            left: plan.chatStartScreenLeft + offsetX,
            top: plan.chatStartScreenTop + offsetY,
            width: plan.chatWidth,
            height: plan.chatHeight
        }, {
            force: progress >= 1
        });
    } else if (plan.chatMode === 'dom') {
        _setNekoIdleCat1PairMoveChatPosition(plan.shell, plan.chatStartLeft + offsetX, plan.chatStartTop + offsetY);
    }
}

function _dispatchNekoIdleCat1MotionInputRegionState(state, active, reason, plan) {
    if (!state || !_isNekoDesktopLinuxRuntime()) return;
    if (active) {
        const shouldSuppress = (plan && plan.chatMode === 'solo' && _canNekoIdleCat1MoveSoloWithExpandedChat()) ||
            state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
        if (!shouldSuppress) return;
        state.inputRegionMotionSuppressed = true;
        if (plan) plan.inputRegionSuppressed = true;
    } else if (!state.inputRegionMotionSuppressed && !(plan && plan.inputRegionSuppressed)) {
        return;
    }
    if (!active) state.inputRegionMotionSuppressed = false;
    const container = plan && plan.container ? plan.container : null;
    window.dispatchEvent(new CustomEvent('neko:idle-cat1-motion-input-region-state', {
        detail: {
            active: !!active,
            reason: reason || 'cat1-motion',
            containerId: container && container.id ? container.id : '',
            chatMode: plan && plan.chatMode ? plan.chatMode : ''
        }
    }));
}

function _setNekoIdleCat1Substate(button, substate, options = {}) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const previousSubstate = state.substate;
    if (substate === profile.walkingSubstate) {
        _cancelNekoIdleReturnPendingWalk(state);
    }
    if (substate !== profile.finishingSubstate) {
        _cancelNekoIdleReturnSubactionSettleTimer(state);
    }
    if (substate === profile.walkingSubstate) {
        state.actionSettled = false;
    }
    state.substate = substate;
    if (Object.prototype.hasOwnProperty.call(options, 'facingRight')) {
        state.facingRight = !!options.facingRight;
    }
    _setNekoIdleCat1Classes(button, state);
    if (state.paused) return;
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            _getNekoIdleCat1ArtSource(button),
            profile.tier,
            { animate: options.animate !== false }
        );
    }
    if (
        substate === profile.finishingSubstate &&
        previousSubstate !== profile.finishingSubstate &&
        !state.paused
    ) {
        _scheduleNekoIdleReturnSubactionSettle(button);
    }
}

function _finishNekoIdleCat1Walk(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    _cancelNekoIdleCat1Frame(state);
    _clearNekoIdleCat1WalkApproachSide(_getNekoIdleReturnContainerFromButton(button));
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-walk-finish');
    state.target = null;
    state.lastStepAt = 0;
    state.actionSettled = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Substate(button, state.profile.finishingSubstate, { animate: true });
}

function _finishNekoIdleCat1CompactTopEdgeWalk(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const settledSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
    const settledTarget = state.target;
    _cancelNekoIdleCat1Frame(state);
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-compact-top-edge-walk-finish');
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleCat1PairMove(state);
    const settleToken = state.settleToken || 0;
    state.substate = profile.idleSubstate;
    state.target = null;
    state.lastStepAt = 0;
    state.targetKind = _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    state.actionSettled = true;
    state.compactTopEdgeRearmRequired = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _rememberNekoIdleCat1CompactFollowAnchor(state, settledSurfaceRect, settledTarget);
    _rememberNekoIdleCat1CompactFollowSurface(state, settledSurfaceRect, _getNekoIdleNowMs());
    _setNekoIdleCat1Classes(button, state);

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            profile.assets.idle(),
            profile.tier,
            { animate: true }
        );
    }

    setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState ||
            latestState.settleToken !== settleToken ||
            latestState.substate !== profile.idleSubstate ||
            latestState.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE ||
            !latestState.actionSettled) {
            return;
        }
        latestState.facingRight = false;
        _setNekoIdleCat1Classes(button, latestState);
        _cancelNekoIdleCat1PairMove(latestState);
    }, profile.settle.resetFacingAfterMs);
}

function _settleNekoIdleReturnSubactionToIdle(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.substate !== state.profile.finishingSubstate || state.paused) return;
    const profile = state.profile;
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    state.substate = profile.idleSubstate;
    state.target = null;
    state.lastStepAt = 0;
    state.actionSettled = true;
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Classes(button, state);

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            profile.assets.idle(),
            profile.tier,
            { animate: true }
        );
    }

    setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState ||
            latestState.substate !== profile.idleSubstate ||
            !latestState.actionSettled) {
            return;
        }
        latestState.facingRight = false;
        _setNekoIdleCat1Classes(button, latestState);
        if (latestState.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) {
            _cancelNekoIdleCat1PairMove(latestState);
            return;
        }
        _scheduleNekoIdleCat1PairMove(button);
    }, profile.settle.resetFacingAfterMs);
}

function _scheduleNekoIdleReturnSubactionSettle(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.paused || state.substate !== state.profile.finishingSubstate) return;
    if (state.settleTimer) return;

    const profile = state.profile;
    const token = (state.settleToken || 0) + 1;
    state.settleToken = token;
    const startedAt = Date.now();
    const finishingSrc = profile.assets.finishing();
    _getNekoIdleGifDurationMs(finishingSrc).then((durationMs) => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState || latestState.settleToken !== token) return;
        if (state.substate !== profile.finishingSubstate || state.paused) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, durationMs - elapsedMs) + profile.settle.finalHoldMs;
        state.settleTimer = setTimeout(() => {
            const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
            if (!currentState || currentState.settleToken !== token) return;
            state.settleTimer = 0;
            _settleNekoIdleReturnSubactionToIdle(button);
        }, delayMs);
    });
}

function _pickNekoIdleWeightedDelayMs(choices) {
    if (!choices || choices.length === 0) return 0;

    const totalWeight = choices.reduce((sum, choice) => {
        const weight = Number(choice && choice.weight);
        return sum + (Number.isFinite(weight) && weight > 0 ? weight : 0);
    }, 0);
    if (totalWeight <= 0) return 0;

    let cursor = Math.random() * totalWeight;
    for (const choice of choices) {
        const weight = Number(choice && choice.weight);
        if (!Number.isFinite(weight) || weight <= 0) continue;
        cursor -= weight;
        if (cursor > 0) continue;

        const minMs = Math.max(0, Math.round(Number(choice.minMs) || 0));
        const maxMs = Math.max(minMs, Math.round(Number(choice.maxMs) || minMs));
        if (maxMs <= minMs) return minMs;
        return minMs + Math.round(Math.random() * (maxMs - minMs));
    }
    return 0;
}

function _pickNekoIdleReturnSubactionStartDelayMs(profile) {
    const choices = profile && profile.startDelay && Array.isArray(profile.startDelay.choices)
        ? profile.startDelay.choices
        : null;
    return _pickNekoIdleWeightedDelayMs(choices);
}

function _pickNekoIdleCat1PairMoveDelayMs(profile) {
    const choices = profile && profile.pairMove && Array.isArray(profile.pairMove.intervalChoices)
        ? profile.pairMove.intervalChoices
        : null;
    return _pickNekoIdleWeightedDelayMs(choices);
}

function _updateNekoIdleCat1WalkSpeedRate(button, state, distance) {
    if (!state) return 1;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const targetConfig = profile.target || {};
    const maxRate = Math.max(1, Number(targetConfig.maxSpeedRate) || 1);
    const previousDistance = Number(state.walkPreviousDistance) || 0;
    const currentDistance = Math.max(0, Number(distance) || 0);
    const threshold = Math.max(0, Number(targetConfig.distanceIncreaseThresholdPx) || 0);
    const growthForMaxRate = Math.max(1, Number(targetConfig.distanceGrowthForMaxRatePx) || 1);

    if (previousDistance > 0) {
        if (currentDistance > previousDistance + threshold) {
            // 落后了（毛球被移远）：累计落后量，提升追赶倍率
            state.walkDistanceGrowthPx = Math.max(
                0,
                (Number(state.walkDistanceGrowthPx) || 0) + (currentDistance - previousDistance)
            );
        } else if (currentDistance < previousDistance) {
            // 正在收敛：回落累计落后量，避免一次瞬时变远把倍率永久钉死在 maxRate
            state.walkDistanceGrowthPx = Math.max(
                0,
                (Number(state.walkDistanceGrowthPx) || 0) - (previousDistance - currentDistance)
            );
        }
        const progress = Math.min(1, (Number(state.walkDistanceGrowthPx) || 0) / growthForMaxRate);
        const nextRate = Math.min(maxRate, 1 + (maxRate - 1) * progress);
        if (nextRate !== state.walkSpeedRate) {
            state.walkSpeedRate = nextRate;
            _setNekoIdleCat1Classes(button, state);
        }
    }

    state.walkPreviousDistance = currentDistance;
    return Math.max(1, Number(state.walkSpeedRate) || 1);
}

function _stepNekoIdleCat1Walk(button, timestamp) {
    const state = _getNekoIdleCat1Journey(button);
    const profile = state && state.profile ? state.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!state || !container || state.paused || state.substate !== profile.walkingSubstate) {
        if (state) state.frame = 0;
        return;
    }

    const chatRect = _getNekoIdleChatMinimizedRect();
    const rawCompactAnchorRatio = state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
        ? state.compactFollowAnchorRatio
        : null;
    const compactAnchorRatio = rawCompactAnchorRatio === null || rawCompactAnchorRatio === undefined
        ? NaN
        : Number(rawCompactAnchorRatio);
    const target = _getNekoIdleCat1Target(container, chatRect, {
        anchorRatio: Number.isFinite(compactAnchorRatio) ? compactAnchorRatio : null,
        compactTopEdgeBlocked: !!state.compactTopEdgeRearmRequired
    });
    if (!target) {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }

    state.target = target;
    state.targetKind = target.kind || '';
    const rect = container.getBoundingClientRect();
    state.facingRight = _resolveNekoIdleCat1TargetFacing(rect, target);
    _setNekoIdleCat1Classes(button, state);
    const speedRate = _updateNekoIdleCat1WalkSpeedRate(button, state, target.distance);
    if (target.distance <= profile.target.exitDistancePx) {
        _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
        if (target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) {
            _finishNekoIdleCat1CompactTopEdgeWalk(button);
        } else {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _finishNekoIdleCat1Walk(button);
        }
        return;
    }

    const lastStepAt = state.lastStepAt || timestamp;
    const elapsedMs = Math.max(
        profile.target.minStepMs,
        Math.min(timestamp - lastStepAt, profile.target.maxStepMs)
    );
    state.lastStepAt = timestamp;
    const stepDistance = (profile.target.speedPxPerSec * speedRate * elapsedMs) / 1000;
    const ratio = target.distance > 0 ? Math.min(1, stepDistance / target.distance) : 1;
    const nextLeft = rect.left + (target.left - rect.left) * ratio;
    const nextTop = rect.top + (target.top - rect.top) * ratio;
    _setNekoIdleCat1ContainerPosition(container, nextLeft, nextTop);

    state.frame = window.requestAnimationFrame((nextTimestamp) => {
        _stepNekoIdleCat1Walk(button, nextTimestamp);
    });
}

function _startNekoIdleCat1Walk(button, target) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state) return;
    if (_isNekoIdleReturnDragActionActive(button)) return;
    const walkContainer = _getNekoIdleReturnContainerFromButton(button);
    const walkDragging = walkContainer && walkContainer.getAttribute('data-dragging');
    if (walkDragging && walkDragging !== 'false') return;
    const profile = state.profile;
    const currentRect = walkContainer && walkContainer.getBoundingClientRect
        ? walkContainer.getBoundingClientRect()
        : null;
    state.target = target;
    state.targetKind = target && target.kind ? target.kind : '';
    state.facingRight = _resolveNekoIdleCat1TargetFacing(currentRect, target);
    if (state.substate !== profile.walkingSubstate) {
        state.lastStepAt = 0;
        _resetNekoIdleCat1WalkSpeed(state);
        state.walkPreviousDistance = Math.max(0, Number(target && target.distance) || 0);
        _setNekoIdleCat1Substate(button, profile.walkingSubstate, { animate: false, facingRight: state.facingRight });
    } else {
        _setNekoIdleCat1Classes(button, state);
    }
    _dispatchNekoIdleCat1MotionInputRegionState(state, true, 'cat1-walk-start');
    if (!state.frame && !state.paused) {
        const timestamp = (typeof performance !== 'undefined' && typeof performance.now === 'function')
            ? performance.now()
            : Date.now();
        _stepNekoIdleCat1Walk(button, timestamp);
    }
}

function _scheduleNekoIdleCat1WalkStart(button, target) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.paused) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    _cancelNekoIdleCat1PairMove(state);
    if (state.substate === profile.walkingSubstate) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    state.target = target;
    state.targetKind = target && target.kind ? target.kind : '';
    const container = _getNekoIdleReturnContainerFromButton(button);
    const rect = container && container.getBoundingClientRect ? container.getBoundingClientRect() : null;
    state.facingRight = _resolveNekoIdleCat1TargetFacing(rect, target);
    _setNekoIdleCat1Classes(button, state);
    const art = button.querySelector('.neko-idle-return-art');
    if (art && art.__nekoIdleHoverSrc) {
        if (!art.__nekoIdleHoverTimer) {
            _finishNekoIdleHoverArtAfterPlayback(art, profile.tier);
        }
        return;
    }
    if (state.pendingWalkReady) {
        state.pendingWalkReady = false;
        _startNekoIdleCat1Walk(button, target);
        return;
    }
    if (state.pendingWalkTimer) return;

    const compactTopEdgeTarget = target && target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    const delayMs = compactTopEdgeTarget ? 0 : _pickNekoIdleReturnSubactionStartDelayMs(profile);
    state.pendingWalkDelayMs = delayMs;
    if (delayMs <= 0) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    const token = (state.pendingWalkToken || 0) + 1;
    state.pendingWalkToken = token;
    state.pendingWalkTimer = setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState || latestState.pendingWalkToken !== token) return;
        latestState.pendingWalkTimer = 0;
        latestState.pendingWalkDelayMs = 0;
        latestState.pendingWalkReady = true;
        _syncNekoIdleCat1Journey(button);
    }, delayMs);
}

function _canScheduleNekoIdleCat1PairMove(button, state) {
    if (!button || !state || state.paused || state.pairMovePlan || state.pairMoveFrame) return false;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    if (state.substate !== profile.idleSubstate || !state.actionSettled) return false;
    if (state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE) return false;
    if (state.pendingWalkTimer || state.pendingWalkReady || state.frame || state.settleTimer) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;

    const art = button.querySelector('.neko-idle-return-art');
    if (art && art.__nekoIdleHoverSrc) {
        if (!art.__nekoIdleHoverTimer) {
            _finishNekoIdleHoverArtAfterPlayback(art, profile.tier);
        }
        return false;
    }

    const container = _getNekoIdleReturnContainerFromButton(button);
    const chatTarget = _getNekoIdleCat1PairMoveChatTarget();
    if (chatTarget && chatTarget.mode === 'desktop' && _isNekoDesktopLinuxRuntime()) return false;
    const canMoveSolo = chatTarget ? false : _canNekoIdleCat1MoveSoloWithExpandedChat();
    if (!container || (!chatTarget && !canMoveSolo)) return false;
    if (container.style.display === 'none' || container.getAttribute('data-dragging') === 'true') return false;

    const catRect = container.getBoundingClientRect();
    const chatRect = chatTarget ? chatTarget.rect : null;
    if (!catRect || catRect.width <= 0 || catRect.height <= 0) {
        return false;
    }

    if (chatTarget) {
        if (!chatRect || chatRect.width <= 0 || chatRect.height <= 0) return false;
        const target = _getNekoIdleCat1Target(container, chatRect, {
            compactTopEdgeBlocked: !!(state && state.compactTopEdgeRearmRequired)
        });
        if (!target || target.distance > profile.target.exitDistancePx) return false;
    }

    const config = profile.pairMove || {};
    const minUsableDistance = Math.max(1, Number(config.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX);
    const maxDistance = Math.max(1, Number(config.maxDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX);
    return _hasNekoIdleCat1MoveVectorSpace(
        catRect,
        chatTarget ? chatRect : null,
        maxDistance,
        minUsableDistance
    );
}

function _finishNekoIdleCat1PairMove(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.pairMovePlan) return;
    const profile = state.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    _applyNekoIdleCat1PairMovePlan(state.pairMovePlan, 1);
    _dispatchNekoIdleCat1MotionInputRegionState(state, false, 'cat1-pair-move-finish', state.pairMovePlan);
    state.pairMoveFrame = 0;
    state.pairMovePlan = null;
    state.substate = profile.idleSubstate;
    state.target = null;
    state.targetKind = '';
    state.actionSettled = true;
    state.facingRight = false;
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Classes(button, state);
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(art, profile.assets.idle(), profile.tier, { animate: false });
    }
    _scheduleNekoIdleCat1PairMove(button);
}

function _stepNekoIdleCat1PairMove(button, startedAt, timestamp) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.pairMovePlan || state.paused) {
        if (state) state.pairMoveFrame = 0;
        return;
    }
    const plan = state.pairMovePlan;
    const chatAvailable = plan.chatMode === 'desktop'
        ? _getNekoIdleDesktopChatMinimizedRect()
        : (plan.chatMode === 'dom'
            ? _getNekoIdleReactChatMinimizedShell()
            : _canNekoIdleCat1MoveSoloWithExpandedChat());
    if (!chatAvailable || plan.container.getAttribute('data-dragging') === 'true') {
        _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        return;
    }
    const elapsedMs = Math.max(0, timestamp - startedAt);
    const progress = plan.durationMs > 0 ? Math.min(1, elapsedMs / plan.durationMs) : 1;
    if (progress >= 1) {
        // 末帧只由 _finishNekoIdleCat1PairMove 强制同步一次原生 bounds；
        // 若先在此处 apply(progress=1) 再 finish，会触发两次 force dispatch（绕过节流/去重）的重复同步。
        _finishNekoIdleCat1PairMove(button);
        return;
    }
    _applyNekoIdleCat1PairMovePlan(plan, progress);
    state.pairMoveFrame = window.requestAnimationFrame((nextTimestamp) => {
        _stepNekoIdleCat1PairMove(button, startedAt, nextTimestamp);
    });
}

function _startNekoIdleCat1PairMove(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || !_canScheduleNekoIdleCat1PairMove(button, state)) {
        return false;
    }
    const plan = _getNekoIdleCat1PairMovePlan(button);
    if (!plan) {
        return false;
    }
    state.pairMoveToken += 1;
    state.pairMoveTimer = 0;
    state.pairMovePlan = plan;
    state.facingRight = plan.dx > 0;
    if (plan.chatMode === 'solo' && _canNekoIdleCat1MoveSoloWithExpandedChat()) {
        _dispatchNekoIdleCat1MotionInputRegionState(state, true, 'cat1-pair-move-start', plan);
    }
    _cancelNekoIdleReturnPendingWalk(state);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _resetNekoIdleCat1WalkSpeed(state);
    _setNekoIdleCat1Classes(button, state);
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(art, state.profile.assets.walking(), state.profile.tier, { animate: false });
    }
    const startedAt = (typeof performance !== 'undefined' && typeof performance.now === 'function')
        ? performance.now()
        : Date.now();
    state.pairMoveFrame = window.requestAnimationFrame((timestamp) => {
        _stepNekoIdleCat1PairMove(button, startedAt, timestamp);
    });
    return true;
}

function _scheduleNekoIdleCat1PairMove(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.pairMoveTimer) return;
    if (!_canScheduleNekoIdleCat1PairMove(button, state)) return;
    const delayMs = _pickNekoIdleCat1PairMoveDelayMs(state.profile);
    const token = (state.pairMoveToken || 0) + 1;
    state.pairMoveToken = token;
    state.pairMoveTimer = setTimeout(() => {
        const latestState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
        if (!latestState || latestState.pairMoveToken !== token) {
            return;
        }
        latestState.pairMoveTimer = 0;
        if (!_startNekoIdleCat1PairMove(button)) {
            _scheduleNekoIdleCat1PairMove(button);
        }
    }, delayMs);
}

function _refreshNekoIdleCat1Observer(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || typeof MutationObserver !== 'function') return;

    if (!state.observer) {
        const shell = document.getElementById('react-chat-window-shell');
        if (shell) {
            state.observer = new MutationObserver(() => {
                _scheduleNekoIdleCat1JourneySync(button);
            });
            state.observer.observe(shell, {
                attributes: true,
                attributeFilter: ['class', 'style']
            });
        }
    }

    if (!state.containerObserver) {
        const container = _getNekoIdleReturnContainerFromButton(button);
        if (container) {
            state.containerObserver = new MutationObserver(() => {
                const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
                if (!currentState || currentState.paused) return;
                if (currentState.substate === currentState.profile.walkingSubstate) return;
                const observerDragging = container.getAttribute('data-dragging');
                if (observerDragging && observerDragging !== 'false') return;
                _scheduleNekoIdleCat1JourneySync(button);
            });
            state.containerObserver.observe(container, {
                attributes: true,
                attributeFilter: ['style', 'data-dragging']
            });
        }
    }
}

function _syncNekoIdleCat1Journey(button, tier) {
    if (!button) return;
    if (_isNekoIdleCompactSurfaceDragging()) return;
    const initialContainer = _getNekoIdleReturnContainerFromButton(button);
    const initialDragging = initialContainer && initialContainer.getAttribute('data-dragging');
    if (initialDragging && initialDragging !== 'false') return;
    const normalizedTier = _normalizeNekoIdleReturnTier(tier || button.getAttribute('data-neko-idle-tier'));
    const profile = _getNekoIdleReturnSubactionProfile(normalizedTier);
    const state = _getNekoIdleReturnSubactionState(button, profile);
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!profile || !state || !container || container.style.display === 'none') {
        _cancelNekoIdleCat1Journey(button);
        return;
    }

    _refreshNekoIdleCat1Observer(button);
    if (state.paused) return;
    if (state.pairMovePlan || state.pairMoveFrame) return;

    const chatRect = _getNekoIdleChatMinimizedRect();
    const rawCompactAnchorRatio = state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
        ? state.compactFollowAnchorRatio
        : null;
    const compactAnchorRatio = rawCompactAnchorRatio === null || rawCompactAnchorRatio === undefined
        ? NaN
        : Number(rawCompactAnchorRatio);
    const target = _getNekoIdleCat1Target(container, chatRect, {
        anchorRatio: Number.isFinite(compactAnchorRatio) ? compactAnchorRatio : null,
        compactTopEdgeBlocked: !!state.compactTopEdgeRearmRequired
    });
    if (!target) {
        state.targetKind = '';
        _cancelNekoIdleReturnPendingWalk(state);
        if (state.substate === profile.idleSubstate) {
            state.target = null;
            state.facingRight = false;
            state.actionSettled = true;
            _resetNekoIdleCat1WalkSpeed(state);
            _setNekoIdleCat1Classes(button, state);
            _scheduleNekoIdleCat1PairMove(button);
            return;
        }
        _cancelNekoIdleCat1PairMove(state);
        if (state.substate !== profile.idleSubstate) {
            _cancelNekoIdleCat1Journey(button, { resetArt: true, preserveObservers: true });
        }
        return;
    }

    const compactDropUntil = Number(state.compactTopEdgeDropUntil) || 0;
    if (target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE &&
        compactDropUntil &&
        _getNekoIdleNowMs() < compactDropUntil) {
        state.target = null;
        state.targetKind = '';
        state.facingRight = false;
        _cancelNekoIdleReturnPendingWalk(state);
        _cancelNekoIdleCat1PairMove(state);
        _setNekoIdleCat1Classes(button, state);
        return;
    }
    if (compactDropUntil) {
        state.compactTopEdgeDropUntil = 0;
    }

    state.target = target;
    state.targetKind = target.kind || '';
    const compactTopEdgeTarget = target.kind === _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE;
    if (compactTopEdgeTarget) {
        _cancelNekoIdleCat1PairMove(state);
        if (target.distance <= profile.target.exitDistancePx) {
            _cancelNekoIdleReturnPendingWalk(state);
        }
    }

    if (target.distance < profile.target.enterDistancePx && state.substate !== profile.walkingSubstate && !compactTopEdgeTarget) {
        _cancelNekoIdleReturnPendingWalk(state);
    }

    if (state.substate === profile.walkingSubstate && target.distance > profile.target.exitDistancePx) {
        _startNekoIdleCat1Walk(button, target);
        return;
    }

    if (target.distance >= profile.target.enterDistancePx ||
        (compactTopEdgeTarget && target.distance > profile.target.exitDistancePx)) {
        state.actionSettled = false;
        _cancelNekoIdleCat1PairMove(state);
        _scheduleNekoIdleCat1WalkStart(button, target);
        return;
    }

    if (state.substate === profile.walkingSubstate) {
        _cancelNekoIdleReturnPendingWalk(state);
        if (compactTopEdgeTarget) {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
            _finishNekoIdleCat1CompactTopEdgeWalk(button);
        } else {
            state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
            _finishNekoIdleCat1Walk(button);
        }
        return;
    }

    if (state.substate === profile.finishingSubstate) {
        state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
        _setNekoIdleCat1Classes(button, state);
        _scheduleNekoIdleReturnSubactionSettle(button);
        return;
    }

    if (state.substate === profile.idleSubstate && !state.actionSettled) {
        if (compactTopEdgeTarget && target.distance <= profile.target.exitDistancePx) {
            _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
        }
        state.target = null;
        state.facingRight = _resolveNekoIdleCat1FinalTargetFacing(target);
        state.actionSettled = true;
        _resetNekoIdleCat1WalkSpeed(state);
        _setNekoIdleCat1Classes(button, state);
    }

    if (state.substate === profile.idleSubstate && state.actionSettled) {
        if (compactTopEdgeTarget) {
            _cancelNekoIdleCat1PairMove(state);
            state.compactTopEdgeRearmRequired = false;
            if (target.distance <= profile.target.exitDistancePx) {
                _setNekoIdleCat1ContainerPosition(container, target.left, target.top);
                const compactSurfaceRect = _getNekoIdleChatCompactSurfaceRect();
                _rememberNekoIdleCat1CompactFollowAnchor(state, compactSurfaceRect, target);
                _rememberNekoIdleCat1CompactFollowSurface(state, compactSurfaceRect, _getNekoIdleNowMs());
                _setNekoIdleCat1Classes(button, state);
            }
            return;
        }
        _scheduleNekoIdleCat1PairMove(button);
    }
}

function _scheduleNekoIdleCat1JourneySync(button) {
    const state = _getNekoIdleCat1Journey(button);
    if (!state || state.syncFrame) return;
    if (_isNekoIdleCompactSurfaceDragging() || _nekoIdleCompactSurfaceSettleTimer) return;
    state.syncFrame = window.requestAnimationFrame(() => {
        state.syncFrame = 0;
        _syncNekoIdleCat1Journey(button);
    });
}

function _pauseNekoIdleCat1Journey(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || (
        state.substate !== state.profile.walkingSubstate &&
        state.substate !== state.profile.finishingSubstate
    )) {
        return;
    }
    state.paused = true;
    _cancelNekoIdleCat1Frame(state);
    _cancelNekoIdleReturnSubactionSettleTimer(state);
    _setNekoIdleCat1Classes(button, state);
}

function _resumeNekoIdleCat1Journey(button) {
    const state = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!state || !state.paused) return;
    state.paused = false;
    state.lastStepAt = 0;
    _setNekoIdleCat1Classes(button, state);
    _syncNekoIdleCat1Journey(button);
    if (state.substate === state.profile.finishingSubstate) {
        _scheduleNekoIdleReturnSubactionSettle(button);
    }
}

function _setNekoIdleReturnArtSource(art, nextSrc, tier, options = {}) {
    if (!art || !nextSrc) return;

    if (!options.keepHoverPlayback) {
        _clearNekoIdleHoverPlayback(art);
    }
    art.setAttribute('data-neko-idle-tier', tier);

    const currentSrc = art.getAttribute('src') || '';
    const shouldAnimate = options.animate !== false
        && currentSrc
        && currentSrc !== nextSrc
        && !_shouldReduceNekoIdleMotion();

    if (!shouldAnimate) {
        _cleanupNekoIdleArtTransition(art);
        _clearNekoIdleGifPlaybackSource(art);
        art.src = nextSrc;
        return;
    }

    if (art.__nekoIdleTransitionTo === nextSrc) {
        return;
    }

    _cleanupNekoIdleArtTransition(art);

    const button = art.closest('.neko-idle-return-btn');
    if (!button) {
        art.src = nextSrc;
        return;
    }

    const nextArt = document.createElement('img');
    nextArt.className = 'neko-idle-return-art neko-idle-return-art-next';
    nextArt.src = nextSrc;
    nextArt.alt = art.alt || '';
    nextArt.draggable = false;
    nextArt.setAttribute('data-neko-idle-tier', tier);

    const finish = () => {
        _clearNekoIdleGifPlaybackSource(art);
        art.src = nextSrc;
        _cleanupNekoIdleArtTransition(art);
    };

    art.__nekoIdleTransitionNext = nextArt;
    art.__nekoIdleTransitionTo = nextSrc;
    button.appendChild(nextArt);
    void nextArt.offsetWidth;
    button.classList.add('is-tier-transitioning');
    art.__nekoIdleTransitionTimer = setTimeout(finish, _NEKO_IDLE_RETURN_TRANSITION_MS);
}

function _playNekoIdleHoverArt(art, tier) {
    if (!art || !tier || tier === _NEKO_IDLE_TIER_NONE) return;
    _cleanupNekoIdleArtTransition(art);

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const button = _getNekoIdleReturnButtonFromArt(art);
    if (_isNekoIdleReturnDragActionActive(button)) return;
    const profile = _getNekoIdleReturnSubactionProfile(normalizedTier);
    const subactionState = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (subactionState && subactionState.profile === profile) {
        _cancelNekoIdleCat1PairMove(subactionState);
    }
    const useSubactionInteractive = !!(profile
        && subactionState
        && subactionState.profile === profile
        && (subactionState.substate === profile.walkingSubstate ||
            subactionState.substate === profile.finishingSubstate));
    if (useSubactionInteractive) {
        _pauseNekoIdleCat1Journey(button);
    }
    const clickSrc = useSubactionInteractive
        ? profile.assets.interactive()
        : _getNekoIdleReturnClickAssetUrl(normalizedTier);
    if (art.__nekoIdleHoverSrc === clickSrc) {
        if (art.__nekoIdleHoverTimer) {
            clearTimeout(art.__nekoIdleHoverTimer);
            art.__nekoIdleHoverTimer = 0;
        }
        art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
        _clearNekoIdleGifPlaybackSource(art);
        if ((art.getAttribute('src') || '') !== clickSrc) {
            art.src = clickSrc;
        }
        return;
    }

    _clearNekoIdleHoverPlayback(art);
    _clearNekoIdleGifPlaybackSource(art);
    art.__nekoIdleHoverToken = (art.__nekoIdleHoverToken || 0) + 1;
    art.__nekoIdleHoverSrc = clickSrc;
    art.__nekoIdleHoverTier = normalizedTier;
    art.__nekoIdleHoverStartedAt = Date.now();
    art.src = clickSrc;
}

function _finishNekoIdleHoverArtAfterPlayback(art, tier) {
    if (!art || !tier || tier === _NEKO_IDLE_TIER_NONE) return;

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (_isNekoIdleReturnDragActionActive(_getNekoIdleReturnButtonFromArt(art))) return;
    const token = art.__nekoIdleHoverToken || 0;
    const startedAt = art.__nekoIdleHoverStartedAt || 0;
    const hoverSrc = art.__nekoIdleHoverSrc || _getNekoIdleReturnClickAssetUrl(normalizedTier);

    if (art.__nekoIdleHoverTimer) {
        clearTimeout(art.__nekoIdleHoverTimer);
        art.__nekoIdleHoverTimer = 0;
    }

    _getNekoIdleGifDurationMs(hoverSrc).then((durationMs) => {
        if ((art.__nekoIdleHoverToken || 0) !== token) return;
        if (art.__nekoIdleHoverTier !== normalizedTier) return;

        const elapsedMs = startedAt ? Math.max(0, Date.now() - startedAt) : durationMs;
        const delayMs = Math.max(0, durationMs - elapsedMs);
        art.__nekoIdleHoverTimer = setTimeout(() => {
            if ((art.__nekoIdleHoverToken || 0) !== token) return;
            if (art.__nekoIdleHoverTier !== normalizedTier) return;
            art.__nekoIdleHoverTimer = 0;
            art.__nekoIdleHoverSrc = '';
            art.__nekoIdleHoverTier = '';
            art.__nekoIdleHoverStartedAt = 0;
            _setNekoIdleReturnArtSource(
                art,
                _getNekoIdleReturnCurrentArtUrl(_getNekoIdleReturnButtonFromArt(art), normalizedTier),
                normalizedTier,
                { animate: false, keepHoverPlayback: true }
            );
            _clearNekoIdleHoverPlayback(art);
            _resumeNekoIdleCat1Journey(_getNekoIdleReturnButtonFromArt(art));
            _scheduleNekoIdleCat1JourneySync(_getNekoIdleReturnButtonFromArt(art));
        }, delayMs);
    });
}

function _applyNekoIdleReturnPresentation(button, tier) {
    if (!button) return;
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (button.__nekoIdleThoughtBubbleTier && button.__nekoIdleThoughtBubbleTier !== normalizedTier) {
        _clearNekoIdleThoughtBubble(button);
    }
    const dragActive = _isNekoIdleReturnDragActionActive(button);
    _syncNekoIdleSleepSoundForTier(normalizedTier);
    _syncNekoIdleCat1AmbientSoundForTier(normalizedTier);
    if (normalizedTier !== _NEKO_IDLE_TIER_CAT1) {
        _cancelNekoIdleCat1Journey(button);
    }
    button.setAttribute('data-neko-idle-tier', normalizedTier);

    const container = button.closest('[id$="-return-button-container"]');
    if (container) {
        container.setAttribute('data-neko-idle-tier', normalizedTier);
    }

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        if (dragActive && normalizedTier !== _NEKO_IDLE_TIER_NONE) {
            _setNekoIdleReturnDragActionArt(button, normalizedTier);
        } else {
            if (normalizedTier === _NEKO_IDLE_TIER_NONE) {
                _finishNekoIdleReturnDragAction(button, { restoreArt: false });
            }
            _setNekoIdleReturnArtSource(art, _getNekoIdleReturnAssetUrl(normalizedTier), normalizedTier);
        }
    }
    if (normalizedTier === _NEKO_IDLE_TIER_CAT1 && !dragActive) {
        _scheduleNekoIdleCat1JourneySync(button);
    }
}

function _readNekoAutoGoodbyeVisualTier() {
    try {
        if (window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.getState === 'function') {
            const currentState = window.nekoAutoGoodbye.getState();
            return _normalizeNekoIdleReturnTier(currentState && currentState.visualTier);
        }
    } catch (_) {}
    return _NEKO_IDLE_TIER_NONE;
}

function _syncAllNekoIdleReturnButtons(tier) {
    document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
        _applyNekoIdleReturnPresentation(button, tier);
    });
}

function _ensureNekoIdleReturnPresentationBridge() {
    if (window.__nekoIdleReturnPresentationBridgeBound) return;
    window.__nekoIdleReturnPresentationBridgeBound = true;

    window.addEventListener('neko:auto-goodbye:state-change', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (!detail || detail.type !== 'visual-tier') {
            return;
        }
        _syncNekoIdleSleepSoundForTier(detail.tier);
        _syncNekoIdleCat1AmbientSoundForTier(detail.tier);
        _syncAllNekoIdleReturnButtons(detail.tier);
    });

    window.addEventListener('resize', () => {
        document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
            _scheduleNekoIdleCat1JourneySync(button);
        });
    });

    window.addEventListener('neko:compact-surface-layout-change', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        _handleNekoIdleCompactSurfaceMoveState(detail);
    });

    window.addEventListener('neko:return-ball-manual-move', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        if (!detail || !detail.container) return;
        if (detail.reason === 'return-ball-drag-end') {
            _finishNekoIdleReturnDragActionForContainer(detail.container);
            const compactTopEdgeRearmState = _updateNekoIdleCat1CompactTopEdgeRearmAfterManualMove(detail.container);
            if (compactTopEdgeRearmState.shouldSync || _shouldRecheckNekoIdleCat1AfterManualMove(detail)) {
                _scheduleNekoIdleCat1JourneySyncForContainer(detail.container);
            }
            return;
        }
        if (detail.reason === 'return-ball-drag-cancel') {
            _finishNekoIdleReturnDragActionForContainer(detail.container, { restoreArt: false });
            return;
        }
        if (detail.reason === 'return-ball-drag-start') {
            _prepareNekoIdleReturnDragActionForContainer(detail.container);
            return;
        }
        if (detail.reason === 'return-ball-drag-active') {
            _startNekoIdleReturnDragActionForContainer(detail.container);
            return;
        }
        _cancelNekoIdleCat1JourneyForContainer(detail.container);
    });

    window.addEventListener('neko:idle-chat-minimized-state', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const receivedAt = Date.now();
        const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);
        if (_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopChatMinimizedState)) return;
        const screenRect = detail && detail.minimized
            ? _normalizeNekoIdleScreenRect(detail.screenRect)
            : null;
        const nextMinimized = !!(detail && detail.minimized && screenRect);
        const compactSurfaceCurrentlyVisible = !!_getNekoIdleDesktopCompactSurfaceRect();
        if (nextMinimized &&
            _nekoIdleDesktopCompactSurfaceState &&
            _nekoIdleDesktopCompactSurfaceState.visible &&
            _isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopCompactSurfaceState)) {
            return;
        }
        const previousState = _nekoIdleDesktopChatMinimizedState;
        const previousScreenRect = previousState && previousState.minimized
            ? previousState.screenRect
            : null;
        const desktopChatMoveDistance = _getNekoIdleRectCenterMoveDistance(previousScreenRect, screenRect);
        const isSmallDesktopChatMove = !!(previousScreenRect && screenRect) &&
            desktopChatMoveDistance < _NEKO_IDLE_CAT1_RECHECK_MOVE_DISTANCE_PX;
        _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
            nextMinimized,
            screenRect,
            receivedAt,
            sourceUpdatedAt,
            !!(detail && !detail.minimized && !compactSurfaceCurrentlyVisible)
        );
        if (nextMinimized) {
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                false,
                null,
                receivedAt,
                sourceUpdatedAt
            );
        }
        const pairMoveFeedback = !!(detail && detail.reason === 'cat1-pair-move');
        document.querySelectorAll(_NEKO_IDLE_RETURN_BUTTON_SELECTOR).forEach((button) => {
            const currentState = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
            if (currentState && (currentState.pairMovePlan || currentState.pairMoveFrame)) {
                if (pairMoveFeedback) return;
                const interrupted = _interruptNekoIdleCat1PairMoveForRetarget(button, currentState);
                if (isSmallDesktopChatMove && !interrupted && !_isNekoIdleCat1Walking(button)) return;
                _scheduleNekoIdleCat1JourneySync(button);
                return;
            }
            if (isSmallDesktopChatMove && !_isNekoIdleCat1Walking(button)) return;
            _scheduleNekoIdleCat1JourneySync(button);
        });
    });

    window.addEventListener('neko:idle-chat-compact-surface-state', (event) => {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : null;
        const receivedAt = Date.now();
        const sourceUpdatedAt = _getNekoIdleDesktopStateSourceUpdatedAt(detail, receivedAt);
        if (_isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopCompactSurfaceState)) return;
        const screenRect = detail && detail.visible
            ? _normalizeNekoIdleScreenRect(detail.screenRect)
            : null;
        const heartbeat = !!(detail && detail.heartbeat);
        const nextVisible = !!(detail && detail.visible && screenRect);
        if (nextVisible &&
            _nekoIdleDesktopChatMinimizedState &&
            _nekoIdleDesktopChatMinimizedState.minimized &&
            _isNekoIdleDesktopStateStaleAgainst(sourceUpdatedAt, _nekoIdleDesktopChatMinimizedState)) {
            return;
        }
        // heartbeat 只用于维持 compact-top-edge 贴附位置同步，不得改变可见性状态：
        // - 禁止通过 heartbeat 覆写 minimized state（聊天框最小化后心跳仍广播可见态，
        //   清掉 minimized 会导致 CAT1 在最小化后 1s 内失去毛线球步行目标）。
        // - 但 compact surface 可见时，心跳必须刷新缓存时间戳，防止 _NEKO_IDLE_DESKTOP_
        //   COMPACT_SURFACE_RECT_STALE_MS (10s) 过期后 _getNekoIdleDesktopCompactSurfaceRect
        //   返回 null，导致 CAT1 失去 compact-top-edge 目标。
        // - 心跳用 receivedAt 刷新 updatedAt 防过期，但保留原 sourceUpdatedAt 避免扰乱
        //   跨状态时间戳排序（心跳自身的新鲜时间戳会让 isStaleAgainst 永远判不出旧）。
        if (!heartbeat) {
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                nextVisible,
                screenRect,
                receivedAt,
                sourceUpdatedAt
            );
            if (nextVisible) {
                _nekoIdleDesktopChatMinimizedState = _makeNekoIdleDesktopChatMinimizedState(
                    false,
                    null,
                    receivedAt,
                    sourceUpdatedAt,
                    false
                );
            }
        } else if (nextVisible &&
            _nekoIdleDesktopCompactSurfaceState &&
            _nekoIdleDesktopCompactSurfaceState.visible) {
            // 最小化时 compact state 已被 minimized listener 清为 visible:false，
            // 此处不会进入——心跳不会把 minimized 态的「不可见 compact」刷回可见。
            var prevCompactSourceUpdatedAt = _nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt;
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                true,
                screenRect || _nekoIdleDesktopCompactSurfaceState.screenRect,
                receivedAt,
                prevCompactSourceUpdatedAt
            );
        } else if (nextVisible &&
            _nekoIdleDesktopChatMinimizedState &&
            !_nekoIdleDesktopChatMinimizedState.minimized) {
            // 还原后来的心跳 catch-up：Electron setMinimized(false) 早退不发布
            // compact-surface-state，compact 缓存仍为 minimize 时写下的
            // visible:false。心跳说 visible + minimized 已 false → 信任心跳
            // 恢复 compact 可用性，保留原 sourceUpdatedAt 不乱排序。
            var prevCompactSourceUpdatedAt = _nekoIdleDesktopCompactSurfaceState
                ? _nekoIdleDesktopCompactSurfaceState.sourceUpdatedAt
                : sourceUpdatedAt;
            _nekoIdleDesktopCompactSurfaceState = _makeNekoIdleDesktopCompactSurfaceState(
                true,
                screenRect,
                receivedAt,
                prevCompactSourceUpdatedAt
            );
        }
        _handleNekoIdleCompactSurfaceMoveState(detail);
    });

    const currentTier = _readNekoAutoGoodbyeVisualTier();
    _syncNekoIdleSleepSoundForTier(currentTier);
    _syncNekoIdleCat1AmbientSoundForTier(currentTier);
}

_ensureNekoIdleReturnPresentationBridge();

const AvatarButtonMixin = {
    /**
     * 应用按钮 mixin 到指定的 Manager 类
     * @param {Object} ManagerPrototype - 目标 Manager 的原型
     * @param {string} prefix - 前缀（如 'vrm', 'mmd'）
     * @param {Object} options - 配置选项
     */
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
        ManagerPrototype.setupFloatingButtonsBase = function(model) {
            // 清理旧事件监听
            if (!this._uiWindowHandlers) {
                this._uiWindowHandlers = [];
            }
            if (this._uiWindowHandlers.length > 0) {
                this._uiWindowHandlers.forEach(({ event, handler, target, options: opts }) => {
                    const eventTarget = target || window;
                    eventTarget.removeEventListener(event, handler, opts);
                });
                this._uiWindowHandlers = [];
            }

            if (this._returnButtonDragHandlers) {
                document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
                document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
                document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
                document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
                this._returnButtonDragHandlers = null;
            }

            // 清理旧 DOM（自身类型）—— 先清理旧容器上的入场动画状态，避免定时器残留
            document.querySelectorAll(`#${options.containerElementId}, #${options.lockIconId}, #${options.returnContainerId}`)
                .forEach(el => {
                    _removeFloatingButtonsElement(el);
                });
            if (options.excludeLiveD2Elements && options.excludeLiveD2Elements.length > 0) {
                options.excludeLiveD2Elements.forEach(selector => {
                    document.querySelectorAll(selector).forEach(el => el.remove());
                });
            }

            // 清理所有其他模型类型的悬浮按钮 DOM（全类型互斥，防止模型切换后出现多组按钮）
            const allButtonIds = [
                'live2d-floating-buttons', 'live2d-lock-icon', 'live2d-return-button-container',
                'vrm-floating-buttons', 'vrm-lock-icon', 'vrm-return-button-container',
                'mmd-floating-buttons', 'mmd-lock-icon', 'mmd-return-button-container'
            ];
            const selfIds = [options.containerElementId, options.lockIconId, options.returnContainerId];
            allButtonIds.forEach(id => {
                if (selfIds.indexOf(id) === -1) {
                    const el = document.getElementById(id);
                    if (el) {
                        _removeFloatingButtonsElement(el);
                    }
                }
            });

            // 调用其他管理器的完整清理 API，防止幽灵回调及残留事件监听
            const otherPrefixes = ['live2d', 'vrm', 'mmd'].filter(p => p !== prefix);
            otherPrefixes.forEach(p => {
                const mgr = p === 'live2d' ? window.live2dManager
                          : p === 'vrm'    ? window.vrmManager
                          :                   window.mmdManager;
                if (!mgr) return;
                const manualCleanup = () => {
                    if (mgr._uiUpdateLoopId !== null && mgr._uiUpdateLoopId !== undefined) {
                        cancelAnimationFrame(mgr._uiUpdateLoopId);
                        mgr._uiUpdateLoopId = null;
                    }
                    if (mgr._floatingButtonsTicker && mgr.pixi_app && mgr.pixi_app.ticker) {
                        try { mgr.pixi_app.ticker.remove(mgr._floatingButtonsTicker); } catch (_) {}
                        mgr._floatingButtonsTicker = null;
                    }
                    if (mgr._uiWindowHandlers) {
                        mgr._uiWindowHandlers.forEach(({ event, handler, target, options: opts }) => {
                            (target || window).removeEventListener(event, handler, opts);
                        });
                        mgr._uiWindowHandlers = [];
                    }
                    mgr._floatingButtonsContainer = null;
                    mgr._returnButtonContainer = null;
                };
                if (typeof mgr.cleanupFloatingButtons === 'function') {
                    try { mgr.cleanupFloatingButtons(); } catch (_) { manualCleanup(); }
                } else {
                    manualCleanup();
                }
            });

            // 清理所有模型类型的侧边面板
            ['live2d', 'vrm', 'mmd'].forEach(p => {
                document.querySelectorAll(`[data-neko-sidepanel-owner^="${p}-popup-"]`).forEach(panel => {
                    if (typeof window.clearAvatarSidePanelHoverState === 'function') {
                        window.clearAvatarSidePanelHoverState(panel);
                    } else {
                        if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
                        if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
                        if (typeof panel._stopHoverPointerTracking === 'function') panel._stopHoverPointerTracking();
                    }
                    panel.remove();
                });
            });

            // 创建按钮容器
            const buttonsContainer = document.createElement('div');
            buttonsContainer.id = options.containerElementId;
            document.body.appendChild(buttonsContainer);

            Object.assign(buttonsContainer.style, {
                position: 'fixed',
                zIndex: '99999',
                pointerEvents: 'auto',
                display: 'none',
                flexDirection: 'column',
                gap: '12px',
                visibility: 'visible',
                opacity: '1',
                transform: 'none'
            });

            this._floatingButtonsContainer = buttonsContainer;

            // 阻止容器内事件传播
            const stopContainerEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend', 'click'].forEach(evt => {
                buttonsContainer.addEventListener(evt, stopContainerEvent);
            });

            // 挂入场动画触发器（仅监听 display 'none' → 可见，不观察定位 style 更新）
            _setupFloatingButtonsEntranceHooks(buttonsContainer);

            return buttonsContainer;
        };

        /**
         * 创建按钮配置数组
         */
        ManagerPrototype.getDefaultButtonConfigs = function() {
            const iconVersion = window.APP_VERSION ? `?v=${window.APP_VERSION}` : `?v=${Date.now()}`;
            return [
                {
                    id: 'mic',
                    emoji: '🎤',
                    title: window.t ? window.t('buttons.voiceControl') : '语音控制',
                    titleKey: 'buttons.voiceControl',
                    hasPopup: true,
                    toggle: true,
                    separatePopupTrigger: true,
                    iconOff: `/static/icons/mic_icon_off.png${iconVersion}`,
                    iconOn: `/static/icons/mic_icon_on.png${iconVersion}`
                },
                {
                    id: 'screen',
                    emoji: '🖥️',
                    title: window.t ? window.t('buttons.screenShare') : '屏幕分享',
                    titleKey: 'buttons.screenShare',
                    hasPopup: true,
                    toggle: true,
                    separatePopupTrigger: true,
                    iconOff: `/static/icons/screen_icon_off.png${iconVersion}`,
                    iconOn: `/static/icons/screen_icon_on.png${iconVersion}`
                },
                {
                    id: 'agent',
                    emoji: '🔨',
                    title: window.t ? window.t('buttons.agentTools') : 'Agent工具',
                    titleKey: 'buttons.agentTools',
                    hasPopup: true,
                    popupToggle: true,
                    exclusive: 'settings',
                    iconOff: `/static/icons/Agent_off.png${iconVersion}`,
                    iconOn: `/static/icons/Agent_on.png${iconVersion}`
                },
                {
                    id: 'settings',
                    emoji: '⚙️',
                    title: window.t ? window.t('buttons.settings') : '设置',
                    titleKey: 'buttons.settings',
                    hasPopup: true,
                    popupToggle: true,
                    exclusive: 'agent',
                    iconOff: `/static/icons/set_off.png${iconVersion}`,
                    iconOn: `/static/icons/set_on.png${iconVersion}`
                },
                {
                    id: 'goodbye',
                    emoji: '💤',
                    title: window.t ? window.t('buttons.leave') : '请她离开',
                    titleKey: 'buttons.leave',
                    hasPopup: false,
                    iconOff: `/static/icons/rest_off.png${iconVersion}`,
                    iconOn: `/static/icons/rest_on.png${iconVersion}`
                }
            ];
        };

        /**
         * 创建单个按钮及其包装器
         */
        ManagerPrototype.createButtonElement = function(config, buttonsContainer, index) {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;

            // 创建包装器
            const btnWrapper = document.createElement('div');
            btnWrapper.style.position = 'relative';
            btnWrapper.style.display = 'flex';
            btnWrapper.style.alignItems = 'center';
            btnWrapper.style.gap = '8px';
            btnWrapper.style.pointerEvents = 'auto';

            const stopWrapperEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                btnWrapper.addEventListener(evt, stopWrapperEvent);
            });

            // 创建按钮
            const btn = document.createElement('div');
            btn.id = `${prefix}-btn-${config.id}`;
            btn.className = opts.buttonClassPrefix;
            btn.title = config.title;
            if (config.titleKey) {
                btn.setAttribute('data-i18n-title', config.titleKey);
            }

            let imgOff = null;
            let imgOn = null;

            // 创建按钮内容（图片或 emoji）
            if (config.iconOff && config.iconOn) {
                const imgContainer = document.createElement('div');
                Object.assign(imgContainer.style, {
                    position: 'relative',
                    width: '48px',
                    height: '48px',
                    boxSizing: 'border-box',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center'
                });

                imgOff = document.createElement('img');
                imgOff.src = config.iconOff;
                imgOff.alt = config.title;
                Object.assign(imgOff.style, {
                    position: 'absolute',
                    left: '50%',
                    top: '50%',
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    display: 'block',
                    pointerEvents: 'none',
                    opacity: '0.75',
                    transition: 'opacity 0.3s ease',
                    transform: 'translate(-50%, -50%)',
                    transformOrigin: 'center center',
                    imageRendering: 'crisp-edges'
                });

                imgOn = document.createElement('img');
                imgOn.src = config.iconOn;
                imgOn.alt = config.title;
                Object.assign(imgOn.style, {
                    position: 'absolute',
                    left: '50%',
                    top: '50%',
                    width: '48px',
                    height: '48px',
                    objectFit: 'contain',
                    display: 'block',
                    pointerEvents: 'none',
                    opacity: '0',
                    transition: 'opacity 0.3s ease',
                    transform: 'translate(-50%, -50%)',
                    transformOrigin: 'center center',
                    imageRendering: 'crisp-edges'
                });

                imgContainer.appendChild(imgOff);
                imgContainer.appendChild(imgOn);
                btn.appendChild(imgContainer);
            } else if (config.emoji) {
                btn.innerText = config.emoji;
            }

            // 按钮样式
            Object.assign(btn.style, {
                width: '48px',
                height: '48px',
                boxSizing: 'border-box',
                borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255, 255, 255, 0.18))',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '24px',
                cursor: 'pointer',
                userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'all 0.1s ease',
                pointerEvents: 'auto'
            });

            // 阻止按钮上的指针事件传播
            const stopBtnEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'pointermove', 'pointerup', 'mousedown', 'mousemove', 'mouseup', 'touchstart', 'touchmove', 'touchend'].forEach(evt => {
                btn.addEventListener(evt, stopBtnEvent);
            });

            // 悬停效果
            btn.addEventListener('mouseenter', () => {
                btn.style.transform = 'scale(1.05)';
                btn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                btn.style.background = 'var(--neko-btn-bg-hover, rgba(255, 255, 255, 0.8))';

                if (config.separatePopupTrigger) {
                    const popup = document.getElementById(`${prefix}-popup-${config.id}`);
                    const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                    if (isPopupVisible) return;
                }

                if (imgOff && imgOn) {
                    imgOff.style.opacity = '0';
                    imgOn.style.opacity = '1';
                }
            });

            btn.addEventListener('mouseleave', () => {
                btn.style.transform = 'scale(1)';
                btn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                const isActive = btn.dataset.active === 'true';
                const popup = document.getElementById(`${prefix}-popup-${config.id}`);
                const isPopupVisible = popup && popup.style.display === 'flex' && popup.style.opacity === '1';
                const shouldShowOnIcon = config.separatePopupTrigger
                    ? isActive
                    : (isActive || isPopupVisible);

                btn.style.background = shouldShowOnIcon
                    ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
                    : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

                if (imgOff && imgOn) {
                    imgOff.style.opacity = shouldShowOnIcon ? '0' : '0.75';
                    imgOn.style.opacity = shouldShowOnIcon ? '1' : '0';
                }
            });

            return { btnWrapper, btn, imgOff, imgOn };
        };

        /**
         * 创建"请她回来"按钮
         */
        ManagerPrototype.createReturnButton = function() {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;
            const currentTier = _readNekoAutoGoodbyeVisualTier();

            const returnButtonContainer = document.createElement('div');
            returnButtonContainer.id = opts.returnContainerId;
            returnButtonContainer.className = 'neko-idle-return-button-container';
            Object.assign(returnButtonContainer.style, {
                position: 'fixed',
                top: '0',
                left: '0',
                transform: 'none',
                zIndex: _NEKO_IDLE_RETURN_DEFAULT_Z_INDEX,
                pointerEvents: 'auto',
                display: 'none'
            });

            const returnBtn = document.createElement('div');
            returnBtn.id = opts.returnBtnId;
            returnBtn.className = `${opts.returnBtnClass} neko-idle-return-btn`;
            returnBtn.title = window.t ? window.t('buttons.return') : '请她回来';
            returnBtn.setAttribute('data-i18n-title', 'buttons.return');
            returnBtn.setAttribute('data-neko-idle-tier', currentTier);

            const returnArt = document.createElement('img');
            returnArt.className = 'neko-idle-return-art';
            returnArt.src = _getNekoIdleReturnAssetUrl(currentTier);
            returnArt.alt = window.t ? window.t('buttons.return') : '请她回来';
            returnArt.draggable = false;
            Object.assign(returnArt.style, {
                width: '100%',
                height: '100%',
                objectFit: 'contain',
                pointerEvents: 'none',
                userSelect: 'none',
                display: 'block',
                transition: 'transform 0.18s ease, filter 0.18s ease, opacity 0.18s ease'
            });

            Object.assign(returnBtn.style, {
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                userSelect: 'none',
                pointerEvents: 'auto',
                position: 'relative'
            });

            returnBtn.addEventListener('mouseenter', () => {
                const tier = returnBtn.getAttribute('data-neko-idle-tier');
                if (tier && tier !== 'none') {
                    _playNekoIdleHoverArt(returnArt, tier);
                }
            });

            returnBtn.addEventListener('mouseleave', () => {
                const tier = returnBtn.getAttribute('data-neko-idle-tier');
                if (tier && tier !== 'none') {
                    _finishNekoIdleHoverArtAfterPlayback(returnArt, tier);
                }
            });

            returnBtn.addEventListener('click', (e) => {
                if (
                    returnButtonContainer.getAttribute('data-dragging') === 'true' ||
                    returnButtonContainer.getAttribute('data-neko-model-cat-transitioning') === 'cat-to-model' ||
                    (typeof window.isNekoModelCatTransitionActive === 'function' && window.isNekoModelCatTransitionActive())
                ) {
                    e.preventDefault();
                    e.stopPropagation();
                    return;
                }
                e.stopPropagation();
                _finishNekoIdleReturnDragAction(returnBtn, { restoreArt: false });
                _cancelNekoIdleCat1Journey(returnBtn);
                const rect = returnButtonContainer.getBoundingClientRect();
                const event = new CustomEvent(`${prefix}-return-click`, {
                    detail: {
                        returnButtonRect: {
                            left: rect.left,
                            top: rect.top,
                            width: rect.width,
                            height: rect.height
                        }
                    }
                });
                const dispatchReturnEvent = () => {
                    window.dispatchEvent(event);
                };
                if (typeof window.playNekoModelCatTransition === 'function') {
                    returnButtonContainer.setAttribute('data-neko-model-cat-transitioning', 'cat-to-model');
                    window.playNekoModelCatTransition({
                        direction: 'cat-to-model',
                        anchorRect: rect,
                        coverRect: window._savedGoodbyeRect || null,
                        container: returnButtonContainer
                    }).catch((error) => {
                        console.warn('[AvatarButtonMixin] model/cat return transition failed:', error);
                        returnButtonContainer.removeAttribute('data-neko-model-cat-transitioning');
                    });
                    dispatchReturnEvent();
                    return;
                }
                dispatchReturnEvent();
            });

            const thoughtBubble = document.createElement('span');
            thoughtBubble.className = 'neko-idle-thought-bubble';
            Object.assign(thoughtBubble.style, {
                position: 'absolute',
                pointerEvents: 'none',
                userSelect: 'none'
            });

            const thoughtBubbleBg = document.createElement('img');
            thoughtBubbleBg.className = 'neko-idle-thought-bubble-bg';
            thoughtBubbleBg.src = _getNekoIdleThoughtBubbleBgAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ASSET_URL);
            thoughtBubbleBg.alt = '';
            thoughtBubbleBg.draggable = false;

            const thoughtBubbleItem = document.createElement('img');
            thoughtBubbleItem.className = 'neko-idle-thought-bubble-item';
            thoughtBubbleItem.src = _getNekoIdleThoughtBubbleItemAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_ITEM_ASSET_URLS[0]);
            thoughtBubbleItem.alt = '';
            thoughtBubbleItem.draggable = false;

            thoughtBubble.appendChild(thoughtBubbleBg);
            thoughtBubble.appendChild(thoughtBubbleItem);

            returnBtn.appendChild(returnArt);
            returnBtn.appendChild(thoughtBubble);
            returnButtonContainer.appendChild(returnBtn);
            document.body.appendChild(returnButtonContainer);
            this._returnButtonContainer = returnButtonContainer;
            _applyNekoIdleReturnPresentation(returnBtn, currentTier);
            if (!window.__NEKO_MULTI_WINDOW__ || _isNekoNativeReturnBallDragDisabled()) {
                this._setupReturnButtonDrag(returnButtonContainer);
            }

            return returnButtonContainer;
        };

        /**
         * 设置返回按钮拖拽功能
         */
        ManagerPrototype._setupReturnButtonDrag = function(container) {
            let isDragging = false;
            let dragActiveDispatched = false;
            let dragSafetyTimer = 0;
            let dragSafetyToken = 0;
            let dragStartX = 0, dragStartY = 0, containerStartX = 0, containerStartY = 0;

            const clearDragSafetyTimer = () => {
                if (!dragSafetyTimer) return;
                clearTimeout(dragSafetyTimer);
                dragSafetyTimer = 0;
            };

            const finishDragState = (moved, safetyToken) => {
                if (safetyToken !== dragSafetyToken) return;
                container.setAttribute('data-dragging', 'false');
                if (moved) {
                    _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-end', {
                        movedDistancePx: Math.hypot(
                            (parseFloat(container.style.left) || containerStartX) - containerStartX,
                            (parseFloat(container.style.top) || containerStartY) - containerStartY
                        )
                    });
                } else {
                    _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-cancel', {
                        movedDistancePx: 0,
                        dragCancelled: true
                    });
                }
            };

            const resetDragStateAfterMissingEnd = (safetyToken) => {
                if (dragSafetyToken !== safetyToken || !isDragging) return;
                const moved = container.getAttribute('data-dragging') === 'true';
                isDragging = false;
                dragActiveDispatched = false;
                container.style.cursor = 'grab';
                finishDragState(moved, safetyToken);
            };

            const handleStart = (clientX, clientY) => {
                clearDragSafetyTimer();
                _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-start');
                isDragging = true;
                dragActiveDispatched = false;
                dragStartX = clientX;
                dragStartY = clientY;
                const rect = container.getBoundingClientRect();
                containerStartX = rect.left;
                containerStartY = rect.top;
                container.style.transform = 'none';
                container.style.right = '';
                container.style.bottom = '';
                container.style.left = `${containerStartX}px`;
                container.style.top = `${containerStartY}px`;
                container.setAttribute('data-dragging', 'pending');
                container.style.cursor = 'grabbing';
                const safetyToken = dragSafetyToken + 1;
                dragSafetyToken = safetyToken;
                dragSafetyTimer = setTimeout(() => {
                    dragSafetyTimer = 0;
                    resetDragStateAfterMissingEnd(safetyToken);
                }, 5000);
            };

            const handleMove = (clientX, clientY) => {
                if (!isDragging) return;
                const deltaX = clientX - dragStartX;
                const deltaY = clientY - dragStartY;
                if (Math.abs(deltaX) > 5 || Math.abs(deltaY) > 5) {
                    container.setAttribute('data-dragging', 'true');
                    if (!dragActiveDispatched) {
                        dragActiveDispatched = true;
                        _dispatchNekoIdleReturnBallManualMove(container, 'return-ball-drag-active');
                    }
                }
                const w = container.offsetWidth || 64;
                const h = container.offsetHeight || 64;
                container.style.left = `${Math.max(0, Math.min(containerStartX + deltaX, window.innerWidth - w))}px`;
                container.style.top = `${Math.max(0, Math.min(containerStartY + deltaY, window.innerHeight - h))}px`;
            };

            const handleEnd = () => {
                clearDragSafetyTimer();
                if (isDragging) {
                    const safetyToken = dragSafetyToken;
                    const moved = container.getAttribute('data-dragging') === 'true';
                    isDragging = false;
                    dragActiveDispatched = false;
                    container.style.cursor = 'grab';
                    setTimeout(() => {
                        finishDragState(moved, safetyToken);
                    }, 10);
                }
            };

            container.addEventListener('mousedown', (e) => {
                if (e.button !== 0) {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    return;
                }
                if (container.contains(e.target)) {
                    e.preventDefault();
                    handleStart(e.clientX, e.clientY);
                }
            });

            this._returnButtonDragHandlers = {
                mouseMove: (e) => handleMove(e.clientX, e.clientY),
                mouseUp: handleEnd,
                touchMove: (e) => {
                    if (isDragging) {
                        e.preventDefault();
                        handleMove(e.touches[0].clientX, e.touches[0].clientY);
                    }
                },
                touchEnd: handleEnd
            };

            document.addEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
            document.addEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
            container.addEventListener('touchstart', (e) => {
                if (container.contains(e.target)) {
                    handleStart(e.touches[0].clientX, e.touches[0].clientY);
                }
            }, { passive: true });
            document.addEventListener('touchmove', this._returnButtonDragHandlers.touchMove, { passive: false });
            document.addEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
            container.style.cursor = 'grab';
        };

        /**
         * 添加返回按钮呼吸灯动画
         */
        ManagerPrototype._addReturnButtonBreathingAnimation = function() {
            // No-op: breathing animation removed, images provide visual identity.
        };

        /**
         * 创建麦克风静音按钮（附加在麦克风按钮左侧）
         * @param {HTMLElement} btnWrapper - 麦克风按钮的包装器
         * @returns {Object|null} 静音按钮数据，包含 button, updateVisibility 等
         */
        ManagerPrototype.createMicMuteButton = function(btnWrapper) {
            const opts = this._avatarButtonOptions;
            const prefix = this._avatarPrefix;

            const muteBtn = document.createElement('div');
            muteBtn.id = `${prefix}-btn-mic-mute`;
            muteBtn.className = `${opts.buttonClassPrefix} ${prefix}-mic-mute-btn`;
            muteBtn.title = window.t ? window.t('buttons.micMute') : '静音麦克风';
            muteBtn.setAttribute('data-i18n-title', 'buttons.micMute');

            const muteSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            muteSvg.setAttribute('viewBox', '0 0 24 24');
            muteSvg.setAttribute('width', '16');
            muteSvg.setAttribute('height', '16');
            Object.assign(muteSvg.style, {
                pointerEvents: 'none',
                display: 'block'
            });

            const micPath = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            micPath.setAttribute('d', 'M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z');
            micPath.setAttribute('fill', '#4a90d9');
            micPath.setAttribute('class', 'mic-mute-body');

            const micStand = document.createElementNS('http://www.w3.org/2000/svg', 'path');
            micStand.setAttribute('d', 'M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z');
            micStand.setAttribute('fill', '#4a90d9');
            micStand.setAttribute('class', 'mic-mute-stand');

            const slashLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            slashLine.setAttribute('x1', '4');
            slashLine.setAttribute('y1', '4');
            slashLine.setAttribute('x2', '20');
            slashLine.setAttribute('y2', '20');
            slashLine.setAttribute('stroke', '#ff4757');
            slashLine.setAttribute('stroke-width', '2.5');
            slashLine.setAttribute('stroke-linecap', 'round');
            slashLine.setAttribute('opacity', '0');
            slashLine.setAttribute('class', 'mic-mute-slash');

            muteSvg.appendChild(micPath);
            muteSvg.appendChild(micStand);
            muteSvg.appendChild(slashLine);
            muteBtn.appendChild(muteSvg);

            Object.assign(muteBtn.style, {
                width: '24px', height: '24px', borderRadius: '50%',
                background: 'var(--neko-btn-bg, rgba(255,255,255,0.65))',
                backdropFilter: 'saturate(180%) blur(20px)',
                border: 'var(--neko-btn-border, 1px solid rgba(255,255,255,0.18))',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                cursor: 'pointer', userSelect: 'none',
                boxShadow: 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))',
                transition: 'all 0.1s ease', pointerEvents: 'auto',
                position: 'absolute',
                left: '-28px',
                top: '50%',
                transform: 'translateY(-50%)'
            });

            const stopMuteEvent = (e) => { e.stopPropagation(); };
            ['pointerdown', 'mousedown', 'touchstart'].forEach(evt => muteBtn.addEventListener(evt, stopMuteEvent));

            const updateMuteButtonState = (isMuted) => {
                if (isMuted) {
                    micPath.setAttribute('fill', '#999');
                    micStand.setAttribute('fill', '#999');
                    slashLine.setAttribute('opacity', '1');
                    muteBtn.style.background = 'rgba(255, 71, 87, 0.25)';
                    muteBtn.title = window.t ? window.t('buttons.micUnmute') : '取消静音';
                } else {
                    micPath.setAttribute('fill', '#4a90d9');
                    micStand.setAttribute('fill', '#4a90d9');
                    slashLine.setAttribute('opacity', '0');
                    muteBtn.style.background = 'var(--neko-btn-bg, rgba(255,255,255,0.65))';
                    muteBtn.title = window.t ? window.t('buttons.micMute') : '静音麦克风';
                }
            };

            const isRecording = window.isRecording || false;
            muteBtn.style.display = isRecording ? 'flex' : 'none';

            const updateMuteButtonVisibility = (visible) => {
                muteBtn.style.display = visible ? 'flex' : 'none';
            };

            if (typeof window.isMicMuted === 'function') {
                updateMuteButtonState(window.isMicMuted());
            }

            muteBtn.addEventListener('mouseenter', () => {
                muteBtn.style.transform = 'translateY(-50%) scale(1.1)';
                muteBtn.style.boxShadow = 'var(--neko-btn-shadow-hover, 0 4px 8px rgba(0,0,0,0.08), 0 8px 16px rgba(0,0,0,0.08))';
                const isMuted = typeof window.isMicMuted === 'function' && window.isMicMuted();
                if (!isMuted) {
                    muteBtn.style.background = 'var(--neko-btn-bg-hover, rgba(255,255,255,0.8))';
                }
            });

            muteBtn.addEventListener('mouseleave', () => {
                muteBtn.style.transform = 'translateY(-50%) scale(1)';
                muteBtn.style.boxShadow = 'var(--neko-btn-shadow, 0 2px 4px rgba(0,0,0,0.04), 0 4px 8px rgba(0,0,0,0.08))';
                const isMuted = typeof window.isMicMuted === 'function' && window.isMicMuted();
                updateMuteButtonState(isMuted);
            });

            muteBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                e.preventDefault();
                if (typeof window.toggleMicMute === 'function') {
                    const newMuted = window.toggleMicMute();
                    updateMuteButtonState(newMuted);
                }
            });

            const micMuteStateChangedHandler = (e) => {
                updateMuteButtonState(Boolean(e && e.detail && e.detail.muted));
            };
            window.addEventListener('mic-mute-state-changed', micMuteStateChangedHandler);
            if (!this._uiWindowHandlers) {
                this._uiWindowHandlers = [];
            }
            this._uiWindowHandlers.push({
                event: 'mic-mute-state-changed',
                handler: micMuteStateChangedHandler,
                target: window
            });

            btnWrapper.appendChild(muteBtn);

            const muteData = {
                button: muteBtn,
                svg: muteSvg,
                micPath: micPath,
                micStand: micStand,
                slashLine: slashLine,
                updateVisibility: updateMuteButtonVisibility
            };

            if (this._floatingButtons) {
                this._floatingButtons['mic-mute'] = muteData;
            }

            return muteData;
        };

        /**
         * 同步独立弹窗触发器（三角形）方向
         */
        ManagerPrototype.updateSeparatePopupTriggerIcon = function(buttonId, expanded) {
            if (!buttonId) return;

            const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
            const triggerIcon = buttonData && buttonData.triggerImg
                ? buttonData.triggerImg
                : document.querySelector(`.${this._avatarPrefix}-trigger-icon-${buttonId}`);
            if (!triggerIcon) return;

            if (typeof expanded === 'boolean') {
                triggerIcon.style.transform = expanded ? 'rotate(180deg)' : 'rotate(0deg)';
                return;
            }

            const buttonActive = !!(buttonData && buttonData.button && buttonData.button.dataset.active === 'true');
            const popup = document.getElementById(`${this._avatarPrefix}-popup-${buttonId}`);
            const popupExpanded = !!(
                popup &&
                popup.style.display === 'flex' &&
                (popup.style.opacity !== '0' || popup.classList.contains('is-positioning'))
            );
            triggerIcon.style.transform = (buttonActive || popupExpanded) ? 'rotate(180deg)' : 'rotate(0deg)';
        };

        /**
         * 设置按钮激活状态
         */
        ManagerPrototype.setButtonActive = function(buttonId, active) {
            const buttonData = this._floatingButtons && this._floatingButtons[buttonId];
            if (!buttonData || !buttonData.button) return;

            buttonData.button.dataset.active = active ? 'true' : 'false';
            buttonData.button.style.background = active
                ? 'var(--neko-btn-bg-active, rgba(255, 255, 255, 0.75))'
                : 'var(--neko-btn-bg, rgba(255, 255, 255, 0.65))';

            if (buttonData.imgOff) {
                buttonData.imgOff.style.opacity = active ? '0' : '0.75';
            }
            if (buttonData.imgOn) {
                buttonData.imgOn.style.opacity = active ? '1' : '0';
            }

            this.updateSeparatePopupTriggerIcon(buttonId);

            // 同步静音按钮的显示状态
            if (buttonId === 'mic') {
                const muteButtonData = this._floatingButtons && this._floatingButtons['mic-mute'];
                if (muteButtonData && muteButtonData.updateVisibility) {
                    muteButtonData.updateVisibility(active);
                }
            }
        };

        /**
         * 重置所有按钮状态
         */
        ManagerPrototype.resetAllButtons = function() {
            if (!this._floatingButtons) return;
            Object.keys(this._floatingButtons).forEach(btnId => {
                this.setButtonActive(btnId, false);
            });
        };

        /**
         * 同步按钮状态与全局状态
         */
        ManagerPrototype._syncButtonStatesWithGlobalState = function() {
            if (!this._floatingButtons) return;

            // 麦克风状态
            const isRecording = window.isRecording || false;
            if (this._floatingButtons.mic) {
                this.setButtonActive('mic', isRecording);
            }

            // 屏幕分享状态
            let isScreenSharing = false;
            const screenButton = document.getElementById('screenButton');
            const stopButton = document.getElementById('stopButton');
            if (screenButton && screenButton.classList.contains('active')) {
                isScreenSharing = true;
            } else if (stopButton && !stopButton.disabled) {
                isScreenSharing = true;
            }
            if (this._floatingButtons.screen) {
                this.setButtonActive('screen', isScreenSharing);
            }
        };

        /**
         * 清理浮动按钮
         */
        ManagerPrototype.cleanupFloatingButtons = function() {
            const opts = this._avatarButtonOptions;

            // 停止 RAF 循环
            if (this._uiUpdateLoopId !== null && this._uiUpdateLoopId !== undefined) {
                cancelAnimationFrame(this._uiUpdateLoopId);
                this._uiUpdateLoopId = null;
            }

            // 移除 DOM 元素（先清理自己的入场动画状态）
            document.querySelectorAll(`#${opts.containerElementId}, #${opts.lockIconId}, #${opts.returnContainerId}`)
                .forEach(el => _removeFloatingButtonsElement(el));

            // 移除侧边面板
            document.querySelectorAll(`[data-neko-sidepanel-owner^="${opts.popupPrefix}-popup-"]`).forEach(panel => {
                if (typeof window.clearAvatarSidePanelHoverState === 'function') {
                    window.clearAvatarSidePanelHoverState(panel);
                } else {
                    if (panel._collapseTimeout) { clearTimeout(panel._collapseTimeout); panel._collapseTimeout = null; }
                    if (panel._hoverCollapseTimer) { clearTimeout(panel._hoverCollapseTimer); panel._hoverCollapseTimer = null; }
                    if (typeof panel._stopHoverPointerTracking === 'function') panel._stopHoverPointerTracking();
                }
                panel.remove();
            });

            // 移除事件监听
            if (this._uiWindowHandlers) {
                this._uiWindowHandlers.forEach(({ event, handler, target, options: opts }) => {
                    (target || window).removeEventListener(event, handler, opts);
                });
                this._uiWindowHandlers = [];
            }

            if (this._returnButtonDragHandlers) {
                document.removeEventListener('mousemove', this._returnButtonDragHandlers.mouseMove);
                document.removeEventListener('mouseup', this._returnButtonDragHandlers.mouseUp);
                document.removeEventListener('touchmove', this._returnButtonDragHandlers.touchMove);
                document.removeEventListener('touchend', this._returnButtonDragHandlers.touchEnd);
                this._returnButtonDragHandlers = null;
            }

            if (this._physicsRestoreTimer) {
                clearTimeout(this._physicsRestoreTimer);
                this._physicsRestoreTimer = null;
            }

            // 清理锁定淡化相关的键盘 / blur 监听器
            if (this._mmdCtrlKeyDownListener) {
                window.removeEventListener('keydown', this._mmdCtrlKeyDownListener);
                this._mmdCtrlKeyDownListener = null;
            }
            if (this._mmdCtrlKeyUpListener) {
                window.removeEventListener('keyup', this._mmdCtrlKeyUpListener);
                this._mmdCtrlKeyUpListener = null;
            }
            if (this._mmdWindowBlurListener) {
                window.removeEventListener('blur', this._mmdWindowBlurListener);
                this._mmdWindowBlurListener = null;
            }
            if (this._mmdLockedHoverFadeChangedListener) {
                window.removeEventListener('neko-locked-hover-fade-changed', this._mmdLockedHoverFadeChangedListener);
                this._mmdLockedHoverFadeChangedListener = null;
            }
            this._setMmdLockedHoverFade = null;

            // 清理引用
            this._floatingButtons = null;
            this._floatingButtonsContainer = null;
            this._returnButtonContainer = null;
            this._buttonConfigs = null;
        };
    }
};

// 导出 mixin
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AvatarButtonMixin;
}
