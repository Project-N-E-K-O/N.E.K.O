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
    if (art.__nekoIdleTransitionNext) {
        // 丢弃过渡临时 <img> 前先清空 src，让 Blink 立即释放该动画 GIF 的解码帧缓存
        // （MEM_MAPPED 共享内存）。否则每次 idle 美术过渡都 createElement 一个新 <img>
        // 播 cat-idle GIF，removeChild 后解码缓冲不及时回收，长期累积撑高 committed。
        try { art.__nekoIdleTransitionNext.removeAttribute('src'); } catch (_) {}
        if (art.__nekoIdleTransitionNext.parentNode) {
            art.__nekoIdleTransitionNext.parentNode.removeChild(art.__nekoIdleTransitionNext);
        }
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
    if (normalizedTier === _NEKO_IDLE_TIER_CAT1 && _isNekoIdleCat1PlayActionActive(button)) {
        return _NEKO_IDLE_CAT1_PLAY_ASSET_URL;
    }
    if (normalizedTier === _NEKO_IDLE_TIER_CAT1 && _isNekoIdleCat1EatActionActive(button)) {
        return _NEKO_IDLE_CAT1_EAT_ASSET_URL;
    }
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
            tier: _NEKO_IDLE_TIER_NONE,
            rapidActive: false,
            rapidTimer: 0,
            rapidToken: 0,
            rapidMotion: null
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

function _clampNekoIdleCat1EdgePeekCoordinate(value, minValue, maxValue) {
    const normalized = Number(value);
    const min = Number(minValue);
    const max = Number(maxValue);
    if (!Number.isFinite(normalized)) return Number.isFinite(min) ? min : 0;
    if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return normalized;
    return Math.max(min, Math.min(normalized, max));
}

function _getNekoIdleCat1EdgePeekButton(containerOrButton) {
    if (!containerOrButton) return null;
    if (containerOrButton.classList && containerOrButton.classList.contains('neko-idle-return-btn')) {
        return containerOrButton;
    }
    return _getNekoIdleReturnButtonFromContainer(containerOrButton);
}

function _clearNekoIdleCat1EdgePeek(containerOrButton) {
    const button = _getNekoIdleCat1EdgePeekButton(containerOrButton);
    if (!button) return;
    _NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES.forEach((className) => {
        button.classList.remove(className);
    });
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
}

function _isNekoIdleCat1EdgePeekActive(containerOrButton) {
    const button = _getNekoIdleCat1EdgePeekButton(containerOrButton);
    return !!(button && _NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES.some((className) => {
        return button.classList.contains(className);
    }));
}

function _getNekoIdleCat1EdgePeekActiveEdge(containerOrButton) {
    const button = _getNekoIdleCat1EdgePeekButton(containerOrButton);
    if (!button) return '';
    const activeClass = _NEKO_IDLE_CAT1_EDGE_PEEK_CLASSES.find((className) => {
        return button.classList.contains(className);
    });
    return activeClass ? activeClass.replace('is-cat1-edge-peek-', '') : '';
}

function _isNekoIdleCat1EdgePeekEligible(containerOrButton) {
    const button = _getNekoIdleCat1EdgePeekButton(containerOrButton);
    return _normalizeNekoIdleReturnTier(button && button.getAttribute('data-neko-idle-tier')) === _NEKO_IDLE_TIER_CAT1;
}

function _getNekoIdleCat1EdgePeekPlacement(left, top, width, height, viewportWidth, viewportHeight) {
    const w = Math.max(1, Number(width) || 0);
    const h = Math.max(1, Number(height) || 0);
    const viewportW = Math.max(w, Number(viewportWidth) || 0);
    const viewportH = Math.max(h, Number(viewportHeight) || 0);
    const currentLeft = Number(left);
    const currentTop = Number(top);
    if (!Number.isFinite(currentLeft) || !Number.isFinite(currentTop)) return null;

    const horizontalThreshold = w * _NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
    const verticalThreshold = h * _NEKO_IDLE_CAT1_EDGE_PEEK_TRIGGER_RATIO;
    const nearLeft = currentLeft <= horizontalThreshold;
    const nearRight = viewportW - (currentLeft + w) <= horizontalThreshold;
    const nearTop = currentTop <= verticalThreshold;
    const nearBottom = viewportH - (currentTop + h) <= verticalThreshold;
    if (!nearLeft && !nearRight && !nearTop && !nearBottom) return null;

    let edge = '';
    const centerX = currentLeft + w / 2;
    if (nearTop) {
        if (nearLeft || centerX <= w) edge = 'top-left';
        else if (nearRight || centerX >= viewportW - w) edge = 'top-right';
        else edge = 'top';
    } else if (nearBottom) {
        if (nearLeft || centerX <= w) edge = 'bottom-left';
        else if (nearRight || centerX >= viewportW - w) edge = 'bottom-right';
        else edge = 'bottom';
    } else if (nearLeft) {
        edge = 'left';
    } else if (nearRight) {
        edge = 'right';
    }

    const hiddenX = w * _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
    const hiddenY = h * _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
    let nextLeft = currentLeft;
    let nextTop = currentTop;
    if (edge === 'left' || edge === 'top-left' || edge === 'bottom-left') {
        nextLeft = -hiddenX;
    } else if (edge === 'right' || edge === 'top-right' || edge === 'bottom-right') {
        nextLeft = viewportW - w + hiddenX;
    } else {
        nextLeft = _clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w);
    }

    if (edge === 'top' || edge === 'top-left' || edge === 'top-right') {
        nextTop = -hiddenY;
    } else if (edge === 'bottom' || edge === 'bottom-left' || edge === 'bottom-right') {
        nextTop = viewportH - h + hiddenY;
    } else {
        nextTop = _clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h);
    }

    return {
        edge,
        left: Math.round(nextLeft),
        top: Math.round(nextTop)
    };
}

function _applyNekoIdleCat1EdgePeek(container, placement) {
    const button = _getNekoIdleCat1EdgePeekButton(container);
    if (!container || !button || !placement || !placement.edge) return false;
    _clearNekoIdleCat1EdgePeek(button);
    button.classList.add(`is-cat1-edge-peek-${placement.edge}`);
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
    _cancelNekoIdleCat1Journey(button, { resetArt: false, preserveObservers: true });
    container.style.left = `${placement.left}px`;
    container.style.top = `${placement.top}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
    return true;
}

function _applyNekoIdleCat1EdgePeekAfterDrag(container, left, top, viewportWidth, viewportHeight) {
    if (!container || !_isNekoIdleCat1EdgePeekEligible(container)) return false;
    const button = _getNekoIdleCat1EdgePeekButton(container);
    const w = container.offsetWidth || 64;
    const h = container.offsetHeight || 64;
    const placement = _getNekoIdleCat1EdgePeekPlacement(left, top, w, h, viewportWidth, viewportHeight);
    const applied = _applyNekoIdleCat1EdgePeek(container, placement);
    if (applied) {
        _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.EDGE_PEEK_AFTER_DRAG, {
            source: 'return-ball',
            tier: button && button.getAttribute('data-neko-idle-tier'),
            reason: 'drag-edge-peek',
            edge: placement && placement.edge ? placement.edge : ''
        });
    }
    return applied;
}

function _reclampNekoIdleCat1EdgePeekToViewport(containerOrButton) {
    const button = _getNekoIdleCat1EdgePeekButton(containerOrButton);
    const container = _getNekoIdleReturnContainerFromButton(button);
    const edge = _getNekoIdleCat1EdgePeekActiveEdge(button);
    if (!container || !button || !edge || !_isNekoIdleCat1EdgePeekEligible(button)) return false;

    const w = container.offsetWidth || 64;
    const h = container.offsetHeight || 64;
    const viewportW = Math.max(w, window.innerWidth || 0);
    const viewportH = Math.max(h, window.innerHeight || 0);
    const hiddenX = w * _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
    const hiddenY = h * _NEKO_IDLE_CAT1_EDGE_PEEK_HIDDEN_RATIO;
    const rawLeft = parseFloat(container.style.left);
    const rawTop = parseFloat(container.style.top);
    const rect = container.getBoundingClientRect && container.getBoundingClientRect();
    const currentLeft = Number.isFinite(rawLeft) ? rawLeft : (rect ? rect.left : 0);
    const currentTop = Number.isFinite(rawTop) ? rawTop : (rect ? rect.top : 0);

    const nextLeft = edge.includes('left')
        ? -hiddenX
        : (edge.includes('right')
            ? viewportW - w + hiddenX
            : _clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w));
    const nextTop = edge.includes('top')
        ? -hiddenY
        : (edge.includes('bottom')
            ? viewportH - h + hiddenY
            : _clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h));

    container.style.left = `${Math.round(nextLeft)}px`;
    container.style.top = `${Math.round(nextTop)}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
    return true;
}

function _restoreNekoIdleCat1EdgePeekBeforeDrag(container) {
    if (!container) return;
    _clearNekoIdleCat1EdgePeek(container);
    if (!_isNekoIdleCat1EdgePeekEligible(container)) return;
    const w = container.offsetWidth || 64;
    const h = container.offsetHeight || 64;
    const viewportW = Math.max(w, window.innerWidth || 0);
    const viewportH = Math.max(h, window.innerHeight || 0);
    const rawLeft = parseFloat(container.style.left);
    const rawTop = parseFloat(container.style.top);
    const rect = container.getBoundingClientRect && container.getBoundingClientRect();
    const currentLeft = Number.isFinite(rawLeft) ? rawLeft : (rect ? rect.left : 0);
    const currentTop = Number.isFinite(rawTop) ? rawTop : (rect ? rect.top : 0);
    container.style.left = `${Math.round(_clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w))}px`;
    container.style.top = `${Math.round(_clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h))}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
}

function _clearNekoIdleCat1EdgePeekForTierExit(container) {
    if (!container) return;
    const wasEdgePeekActive = _isNekoIdleCat1EdgePeekActive(container);
    _clearNekoIdleCat1EdgePeek(container);
    if (!wasEdgePeekActive) return;

    const w = container.offsetWidth || 64;
    const h = container.offsetHeight || 64;
    const viewportW = Math.max(w, window.innerWidth || 0);
    const viewportH = Math.max(h, window.innerHeight || 0);
    const rawLeft = parseFloat(container.style.left);
    const rawTop = parseFloat(container.style.top);
    const rect = container.getBoundingClientRect && container.getBoundingClientRect();
    const currentLeft = Number.isFinite(rawLeft) ? rawLeft : (rect ? rect.left : 0);
    const currentTop = Number.isFinite(rawTop) ? rawTop : (rect ? rect.top : 0);
    container.style.left = `${Math.round(_clampNekoIdleCat1EdgePeekCoordinate(currentLeft, 0, viewportW - w))}px`;
    container.style.top = `${Math.round(_clampNekoIdleCat1EdgePeekCoordinate(currentTop, 0, viewportH - h))}px`;
    container.style.right = '';
    container.style.bottom = '';
    container.style.transform = 'none';
}

function _getNekoIdleCat1RapidDragAssetUrl(button, tier) {
    if (_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1) return '';
    const state = button && button.__nekoIdleReturnDragActionState;
    return state && state.active && state.rapidActive
        ? `${_NEKO_IDLE_CAT1_RAPID_DRAG_ASSET_URL}${_getNekoIdleReturnAssetVersionSuffix()}`
        : '';
}

function _isNekoIdleCat1RapidDragCurrentTier(button) {
    return _normalizeNekoIdleReturnTier(button && button.getAttribute('data-neko-idle-tier')) === _NEKO_IDLE_TIER_CAT1;
}

function _resetNekoIdleCat1RapidDragMotion(button) {
    const state = _getNekoIdleReturnDragActionState(button);
    if (!state) return;
    state.rapidMotion = {
        lastX: null,
        lastY: null,
        lastAt: 0,
        lastVector: null,
        reversals: []
    };
}

function _restoreNekoIdleCat1NormalDragArt(button) {
    if (!button) return;
    const state = button.__nekoIdleReturnDragActionState;
    if (!state || !state.active) return;
    const currentTier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (currentTier !== _NEKO_IDLE_TIER_CAT1 || state.tier !== _NEKO_IDLE_TIER_CAT1) return;
    _setNekoIdleReturnDragActionArt(button, currentTier);
    _playNekoIdleCat1DragSound(currentTier);
}

function _clearNekoIdleCat1RapidDragReaction(button) {
    const state = button && button.__nekoIdleReturnDragActionState;
    if (!state) return;
    if (state.rapidTimer) {
        clearTimeout(state.rapidTimer);
        state.rapidTimer = 0;
    }
    state.rapidActive = false;
    state.rapidToken += 1;
    _resetNekoIdleCat1RapidDragMotion(button);
}

function _activateNekoIdleCat1RapidDragReaction(button, tier) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (!button || normalizedTier !== _NEKO_IDLE_TIER_CAT1) return false;
    if (!_isNekoIdleCat1RapidDragCurrentTier(button)) return false;
    const state = _getNekoIdleReturnDragActionState(button);
    if (!state || !state.active) return false;
    if (state.rapidActive) return true;

    if (state.rapidTimer) {
        clearTimeout(state.rapidTimer);
        state.rapidTimer = 0;
    }
    state.rapidActive = true;
    state.rapidToken += 1;
    const rapidToken = state.rapidToken;
    _setNekoIdleReturnDragActionArt(button, normalizedTier);
    _playNekoIdleCat1RapidDragSound(normalizedTier);
    _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.RAPID_DRAG, {
        source: 'return-ball',
        tier: normalizedTier,
        timestamp: Date.now(),
        reason: 'rapid-drag-detected'
    });
    state.rapidTimer = setTimeout(() => {
        if (state.rapidToken !== rapidToken || !state.active || state.tier !== _NEKO_IDLE_TIER_CAT1) return;
        const currentTier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
        state.rapidTimer = 0;
        state.rapidActive = false;
        _resetNekoIdleCat1RapidDragMotion(button);
        if (currentTier !== _NEKO_IDLE_TIER_CAT1) return;
        _restoreNekoIdleCat1NormalDragArt(button);
    }, _NEKO_IDLE_CAT1_RAPID_DRAG_REACTION_MS);
    return true;
}

function _getNekoIdleDragMotionPoint(detail) {
    const screenX = Number(detail && detail.screenX);
    const screenY = Number(detail && detail.screenY);
    const clientX = Number(detail && detail.clientX);
    const clientY = Number(detail && detail.clientY);
    const hasScreenPoint = Number.isFinite(screenX) && Number.isFinite(screenY);
    const rawX = hasScreenPoint ? screenX : clientX;
    const rawY = hasScreenPoint ? screenY : clientY;
    if (!Number.isFinite(rawX) || !Number.isFinite(rawY)) return null;
    const rawAt = Number(detail && detail.timestamp);
    return {
        x: rawX,
        y: rawY,
        at: Number.isFinite(rawAt) && rawAt > 0 ? rawAt : Date.now()
    };
}

function _isNekoIdleCat1RapidDragWindowReady(reversals) {
    if (!Array.isArray(reversals) ||
        reversals.length < _NEKO_IDLE_CAT1_RAPID_DRAG_REQUIRED_REVERSALS) {
        return false;
    }
    const first = reversals[0];
    const last = reversals[reversals.length - 1];
    const spanMs = Math.max(0, Number(last && last.at) - Number(first && first.at));
    if (spanMs < _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SPAN_MS) return false;
    const totalDistance = reversals.reduce((sum, item) => {
        return sum + Math.max(0, Number(item && item.distance) || 0);
    }, 0);
    const sustainedSpeed = totalDistance / (spanMs / 1000);
    return sustainedSpeed >= _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_SUSTAINED_SPEED_PX_PER_SEC;
}

function _getNekoIdleCat1RapidDragVector(point, motion) {
    if (!point || !motion || motion.lastX === null || motion.lastY === null) return null;
    const dx = point.x - motion.lastX;
    const dy = point.y - motion.lastY;
    return {
        dx,
        dy,
        distance: Math.hypot(dx, dy)
    };
}

function _isNekoIdleCat1RapidDragReversal(previousVector, currentVector) {
    if (!previousVector || !currentVector) return false;
    const previousLength = Math.hypot(previousVector.dx, previousVector.dy);
    const currentLength = Math.hypot(currentVector.dx, currentVector.dy);
    if (previousLength <= 0 || currentLength <= 0) return false;
    const dot = previousVector.dx * currentVector.dx + previousVector.dy * currentVector.dy;
    const cosine = dot / (previousLength * currentLength);
    return cosine <= _NEKO_IDLE_CAT1_RAPID_DRAG_REVERSE_DOT_THRESHOLD;
}

function _handleNekoIdleCat1RapidDragMotionForContainer(container, detail) {
    const button = _getNekoIdleReturnButtonFromContainer(container);
    if (!button) return false;
    const state = _getNekoIdleReturnDragActionState(button);
    const tier = _normalizeNekoIdleReturnTier(state && state.tier);
    if (!state || !state.active || tier !== _NEKO_IDLE_TIER_CAT1 || state.rapidActive) return false;
    if (!_isNekoIdleCat1RapidDragCurrentTier(button)) return false;

    const point = _getNekoIdleDragMotionPoint(detail);
    if (!point) return false;
    if (!state.rapidMotion) _resetNekoIdleCat1RapidDragMotion(button);
    const motion = state.rapidMotion;
    if (motion.lastX === null || motion.lastY === null || !motion.lastAt) {
        motion.lastX = point.x;
        motion.lastY = point.y;
        motion.lastAt = point.at;
        return false;
    }

    const currentVector = _getNekoIdleCat1RapidDragVector(point, motion);
    if (!currentVector || currentVector.distance < _NEKO_IDLE_CAT1_RAPID_DRAG_MIN_DISTANCE_PX) {
        return false;
    }
    const distance = currentVector.distance;
    const elapsedMs = Math.max(1, point.at - motion.lastAt);

    if (_isNekoIdleCat1RapidDragReversal(motion.lastVector, currentVector) && elapsedMs > 0) {
        const cutoff = point.at - _NEKO_IDLE_CAT1_RAPID_DRAG_WINDOW_MS;
        motion.reversals = motion.reversals.filter((item) => item && item.at >= cutoff);
        motion.reversals.push({
            at: point.at,
            distance: distance
        });
        if (_isNekoIdleCat1RapidDragWindowReady(motion.reversals)) {
            motion.lastX = point.x;
            motion.lastY = point.y;
            motion.lastAt = point.at;
            motion.lastVector = currentVector;
            return _activateNekoIdleCat1RapidDragReaction(button, tier);
        }
    }

    motion.lastX = point.x;
    motion.lastY = point.y;
    motion.lastAt = point.at;
    motion.lastVector = currentVector;
    return false;
}

function _setNekoIdleReturnDragActionClasses(button, active) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    button.classList.toggle(_NEKO_IDLE_RETURN_DRAG_ACTION_CLASS, !!active);
    if (container) {
        container.classList.toggle(_NEKO_IDLE_RETURN_DRAG_ACTION_CLASS, !!active);
    }
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
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
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const rapidSrc = _getNekoIdleCat1RapidDragAssetUrl(button, normalizedTier);
    const cachedDragSrc = button.__nekoIdleReturnDragAssetTier === normalizedTier
        ? button.__nekoIdleReturnDragAssetUrl
        : '';
    const dragSrc = rapidSrc || cachedDragSrc || _pickNekoIdleReturnDragAssetUrl(normalizedTier);
    if (!art || !dragSrc) return;
    if (!rapidSrc) {
        button.__nekoIdleReturnDragAssetUrl = dragSrc;
        button.__nekoIdleReturnDragAssetTier = normalizedTier;
    }
    _setNekoIdleReturnArtSource(
        art,
        dragSrc,
        normalizedTier,
        { animate: false }
    );
}

function _prepareNekoIdleReturnDragActionForContainer(container) {
    const button = _getNekoIdleReturnButtonFromContainer(container);
    if (!button) return;
    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
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
    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (tier === _NEKO_IDLE_TIER_NONE) return;
    const state = _getNekoIdleReturnDragActionState(button);
    state.active = true;
    state.token += 1;
    state.tier = tier;
    _resetNekoIdleCat1RapidDragMotion(button);
    button.__nekoIdleReturnDragAssetTier = tier;
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
    _clearNekoIdleCat1RapidDragReaction(button);
    button.__nekoIdleReturnDragAssetUrl = '';
    button.__nekoIdleReturnDragAssetTier = _NEKO_IDLE_TIER_NONE;
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
        // One minimized-side approach has one local tail only: play or stretch.
        // Keep the resolution until a later, genuinely new walk starts so a
        // duplicated finish callback cannot re-roll or append the other tail.
        walkFinishResolution: '',
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
    const nativeYarnRect = targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE
        ? _getNekoIdleChatMinimizedRect()
        : null;
    const nativeYarnVisualAnchor = _usesNekoIdleCat1NativeYarnVisualAnchor(nativeYarnRect);
    const nativeYarnSide = nativeYarnVisualAnchor
        ? _getNekoIdleCat1NativeYarnSide(container, nativeYarnRect)
        : '';
    if (nativeYarnVisualAnchor) {
        button.setAttribute(_NEKO_IDLE_CAT1_NATIVE_YARN_VISUAL_ANCHOR_ATTR, 'true');
    } else {
        button.removeAttribute(_NEKO_IDLE_CAT1_NATIVE_YARN_VISUAL_ANCHOR_ATTR);
    }
    if (nativeYarnSide) {
        button.setAttribute(_NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_ATTR, nativeYarnSide);
    } else {
        button.removeAttribute(_NEKO_IDLE_CAT1_NATIVE_YARN_SIDE_ATTR);
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

function _queueNekoCatMindSmallMoveCancelledResult(state, plan, reason) {
    if (!state || state.catMindActionId !== _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE) return;
    const complete = () => {
        if (state.catMindActionId !== _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE) return;
        _reportNekoCatMindStateActionResult(state, _getNekoCatMindCancelResult(reason, 'cat1-small-move-finished'), {
            reason,
            tier: state.profile && state.profile.tier,
            detail: { chatMode: plan && plan.chatMode ? plan.chatMode : '', restored: true }
        });
    };
    // The journey caller synchronously restores its art/classes after cancelling
    // the plan. Queue the terminal result so that restoration is observable first.
    if (typeof window !== 'undefined' && typeof window.queueMicrotask === 'function') window.queueMicrotask(complete);
    else Promise.resolve().then(complete);
}

function _cancelNekoIdleCat1PairMove(state, options = {}) {
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
    if (activePlan) {
        _queueNekoCatMindSmallMoveCancelledResult(state, activePlan, options.reason || 'cat1-small-move-cancelled');
    }
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

function _resetNekoIdleCat1WalkFinishResolution(state) {
    if (!state) return;
    state.walkFinishResolution = '';
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
    _cancelNekoIdleCat1PairMove(state, { reason: options.reason });
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
    _resetNekoIdleCat1WalkFinishResolution(state);
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

function _isNekoIdleCat1SettledOnMinimizedSide(state, profile) {
    return !!(state &&
        profile &&
        state.substate === profile.idleSubstate &&
        state.actionSettled &&
        state.targetKind === _NEKO_IDLE_CAT1_TARGET_KIND_MINIMIZED_SIDE);
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
    _dispatchNekoCatIdleObservationSource(_NEKO_CAT_IDLE_OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DROP, {
        source: 'cat1-journey',
        tier: profile.tier,
        reason: 'drop-from-compact-top-edge',
        targetKind: _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE
    });
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
