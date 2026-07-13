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

function _normalizeNekoGoodbyeIdleAppearance(mode) {
    return mode === _NEKO_GOODBYE_IDLE_APPEARANCE_BALL
        ? _NEKO_GOODBYE_IDLE_APPEARANCE_BALL
        : _NEKO_GOODBYE_IDLE_APPEARANCE_CAT;
}

function _getNekoGoodbyeIdleAppearance() {
    try {
        if (typeof window.getNekoGoodbyeIdleAppearance === 'function') {
            return _normalizeNekoGoodbyeIdleAppearance(window.getNekoGoodbyeIdleAppearance());
        }
    } catch (_) {}
    return _normalizeNekoGoodbyeIdleAppearance(window.__nekoGoodbyeIdleAppearance);
}

function _setNekoGoodbyeIdleAppearanceForButton(button, mode) {
    if (!button) return;
    const appearance = _normalizeNekoGoodbyeIdleAppearance(mode);
    button.setAttribute(_NEKO_GOODBYE_IDLE_APPEARANCE_ATTR, appearance);
    const container = button.closest('[id$="-return-button-container"]');
    if (container) {
        container.setAttribute(_NEKO_GOODBYE_IDLE_APPEARANCE_ATTR, appearance);
    }
}

function _isNekoGoodbyeIdleBallButton(button) {
    if (!button) return false;
    const container = button.closest('[id$="-return-button-container"]');
    const raw = (container && container.getAttribute(_NEKO_GOODBYE_IDLE_APPEARANCE_ATTR)) ||
        button.getAttribute(_NEKO_GOODBYE_IDLE_APPEARANCE_ATTR) ||
        _getNekoGoodbyeIdleAppearance();
    return _normalizeNekoGoodbyeIdleAppearance(raw) === _NEKO_GOODBYE_IDLE_APPEARANCE_BALL;
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

function _preloadNekoIdleThoughtBubblePopAsset() {
    if (_nekoIdleThoughtBubblePopPreloadImage || typeof window === 'undefined' || typeof window.Image !== 'function') return;
    try {
        const img = new window.Image();
        img.decoding = 'async';
        img.src = _getNekoIdleThoughtBubbleBgAssetUrl(_NEKO_IDLE_THOUGHT_BUBBLE_POP_ASSET_URL);
        _nekoIdleThoughtBubblePopPreloadImage = img;
    } catch (_) {}
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

function _setNekoIdleThoughtBubbleFocusable(button, focusable) {
    const bubble = button && button.querySelector('.neko-idle-thought-bubble');
    if (!bubble) return;
    bubble.tabIndex = focusable ? 0 : -1;
}

function _isNekoIdleThoughtBubbleEventTarget(event) {
    const target = event && event.target;
    return !!(target && typeof target.closest === 'function' && target.closest('.neko-idle-thought-bubble'));
}

function _isNekoIdleThoughtBubbleEventHit(button, event) {
    if (!button || !event) return false;
    if (_isNekoIdleThoughtBubbleEventTarget(event)) return true;
    if (!button.classList.contains(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS)) return false;
    const bubble = button.querySelector('.neko-idle-thought-bubble');
    if (!bubble || typeof bubble.getBoundingClientRect !== 'function') return false;
    const clientX = Number(event.clientX);
    const clientY = Number(event.clientY);
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return false;
    const rect = bubble.getBoundingClientRect();
    return clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
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
    if (!isNekoIdleCatAudioEnabled()) {
        _stopNekoIdleSoundAudio(state);
        return null;
    }

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
        if (playResult && typeof playResult.then === 'function') {
            const playStarted = playResult.then(() => audio);
            playStarted.catch(() => {});
            audio.__nekoIdlePlayStarted = playStarted;
        } else {
            audio.__nekoIdlePlayStarted = null;
        }
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


function _runAfterNekoIdleSoundStarted(state, audio, callback) {
    if (!state || !audio || typeof callback !== 'function') return;

    const run = () => {
        if (state.audio !== audio || audio.paused || audio.ended) return;
        callback(audio);
    };
    const playStarted = audio.__nekoIdlePlayStarted;
    if (playStarted && typeof playStarted.then === 'function') {
        playStarted.then(run).catch(() => {});
        return;
    }
    run();
}

function _getNekoIdleCat1QuestionMarkAssetUrl() {
    return `${_NEKO_IDLE_CAT1_QUESTION_MARK_ASSET_URL}${_getNekoIdleReturnAssetVersionSuffix()}`;
}

function _getNekoIdleCat1QuestionMarkLayerAssetUrl() {
    try {
        return new URL(_getNekoIdleCat1QuestionMarkAssetUrl(), window.location.href).href;
    } catch (_) {
        return _getNekoIdleCat1QuestionMarkAssetUrl();
    }
}

function _getNekoIdleCat1QuestionMarkState(button) {
    if (!button) return null;
    if (!button.__nekoIdleCat1QuestionMarkState) {
        button.__nekoIdleCat1QuestionMarkState = {
            active: false,
            token: 0,
            timer: 0,
            mark: null
        };
    }
    return button.__nekoIdleCat1QuestionMarkState;
}

function _ensureNekoIdleCat1QuestionMarkElement(button) {
    if (!button) return null;
    const state = _getNekoIdleCat1QuestionMarkState(button);
    if (!state) return null;
    let mark = state.mark && state.mark.isConnected ? state.mark : null;
    if (!mark) {
        mark = document.createElement('span');
        mark.className = 'neko-idle-cat1-question-mark';
        mark.setAttribute('aria-hidden', 'true');
        Object.assign(mark.style, {
            position: 'fixed',
            left: '0',
            top: '0',
            width: '72px',
            height: '72px',
            minWidth: '38px',
            minHeight: '38px',
            transform: 'translate(-50%, -100%) scale(0.96)',
            opacity: '0',
            visibility: 'hidden',
            pointerEvents: 'auto',
            cursor: 'pointer',
            zIndex: _NEKO_IDLE_RETURN_DEFAULT_Z_INDEX,
            transition: 'opacity 180ms ease, transform 180ms ease, visibility 0s linear 180ms'
        });
        mark.addEventListener('click', (event) => {
            _handleNekoIdleCat1QuestionMarkClick(button, event);
        });
        const img = document.createElement('img');
        img.className = 'neko-idle-cat1-question-mark-art';
        img.alt = '';
        img.draggable = false;
        img.src = _getNekoIdleCat1QuestionMarkAssetUrl();
        Object.assign(img.style, {
            width: '100%',
            height: '100%',
            display: 'block',
            objectFit: 'contain',
            pointerEvents: 'none',
            userSelect: 'none'
        });
        mark.appendChild(img);
        document.body.appendChild(mark);
        state.mark = mark;
    }
    const img = mark.querySelector('.neko-idle-cat1-question-mark-art');
    if (img) {
        const src = _getNekoIdleCat1QuestionMarkAssetUrl();
        if (img.getAttribute('src') !== src) img.setAttribute('src', src);
    }
    return mark;
}

function _positionNekoIdleCat1QuestionMark(mark, button) {
    if (!mark || !button || typeof button.getBoundingClientRect !== 'function') return false;
    const rect = button.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return false;
    const size = Math.round(Math.max(38, Math.min(96, Math.max(rect.width, rect.height) * 0.42)));
    const left = Math.round(rect.left + rect.width * 0.52);
    const top = Math.round(rect.top + rect.height * 0.08);
    mark.style.width = `${size}px`;
    mark.style.height = `${size}px`;
    mark.style.left = `${left}px`;
    mark.style.top = `${top}px`;
    return true;
}

function _getNekoIdleCat1QuestionMarkScreenRect(mark) {
    if (!mark) return null;
    const styleLeft = parseFloat(mark.style.left);
    const styleTop = parseFloat(mark.style.top);
    const styleWidth = parseFloat(mark.style.width);
    const styleHeight = parseFloat(mark.style.height);
    if (![styleLeft, styleTop, styleWidth, styleHeight].every(Number.isFinite) ||
        styleWidth <= 0 || styleHeight <= 0) {
        return null;
    }
    const screenX = Number(window.screenX);
    const screenY = Number(window.screenY);
    const offsetX = Number.isFinite(screenX) ? screenX : 0;
    const offsetY = Number.isFinite(screenY) ? screenY : 0;
    return {
        left: Math.round(offsetX + styleLeft - styleWidth / 2),
        top: Math.round(offsetY + styleTop - styleHeight),
        width: Math.round(styleWidth),
        height: Math.round(styleHeight)
    };
}

function _dispatchNekoIdleCat1QuestionMarkLayer(button, active, reason) {
    const state = button && button.__nekoIdleCat1QuestionMarkState;
    const mark = state && state.mark && state.mark.isConnected ? state.mark : null;
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-question-mark-layer', {
            detail: {
                active: !!active,
                reason: reason || '',
                assetUrl: _getNekoIdleCat1QuestionMarkLayerAssetUrl(),
                screenRect: active ? _getNekoIdleCat1QuestionMarkScreenRect(mark) : null,
                visibleMs: _NEKO_IDLE_CAT1_QUESTION_MARK_VISIBLE_MS
            }
        }));
    } catch (_) {}
}

function _dispatchNekoIdleCat1PlaygroundEntryRequest(button, source) {
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-playground-entry-request', {
            detail: {
                source: source || 'question-mark',
                trigger: 'cat1-question-mark',
                timestamp: Date.now()
            }
        }));
    } catch (_) {}
}

function _dispatchNekoIdleCat1PlaygroundQuestionBlockClick(element) {
    const rect = element && typeof element.getBoundingClientRect === 'function'
        ? element.getBoundingClientRect()
        : null;
    try {
        window.dispatchEvent(new CustomEvent(_NEKO_IDLE_CAT1_PLAYGROUND_QUESTION_BLOCK_CLICK_EVENT, {
            detail: {
                source: 'cat1-playground',
                bodyId: 'question-block',
                timestamp: Date.now(),
                rect: rect ? {
                    left: rect.left,
                    top: rect.top,
                    width: rect.width,
                    height: rect.height
                } : null
            }
        }));
    } catch (_) {}
}

function _restoreNekoIdleCat1PlaygroundStartPositions(button) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    const starts = state && state.start && state.start.bodies ? state.start.bodies : null;
    if (!state || !state.bodies || !starts) return false;
    let restored = false;
    ['cat', 'yarn', 'desktop-yarn'].forEach((id) => {
        const body = state.bodies.get(id);
        const start = starts[id];
        if (!body || !start) return;
        body.vx = 0;
        body.vy = 0;
        body.dragging = false;
        body.grounded = false;
        _setNekoIdleCat1PlaygroundBodyPosition(body, start.x, start.y, { force: true });
        restored = true;
    });
    if (restored) _setNekoIdleCat1PlaygroundCatGroundedArt(button);
    return restored;
}

function _handleNekoIdleCat1PlaygroundQuestionBlockCloneClick(button, element, event) {
    const state = button && button.__nekoIdleCat1PlaygroundDropState;
    if (event) {
        try { event.preventDefault(); } catch (_) {}
        try { event.stopPropagation(); } catch (_) {}
    }
    if (state && (state.draggingBodyId || state.suppressClickBodyId === 'question-block')) return true;
    _dispatchNekoIdleCat1PlaygroundQuestionBlockClick(element);
    if (_isNekoIdleCat1PlaygroundDropActive(button)) {
        _stopNekoIdleCat1PlaygroundPhysics(button);
        _clearNekoIdleCat1PlaygroundPointerListeners(button);
        _restoreNekoIdleCat1PlaygroundStartPositions(button);
        _releaseNekoIdleCat1PlaygroundDropLifecycle(button, 'question-block-click');
    }
    return true;
}

function _storeNekoIdleCat1PlaygroundQuestionBlockClone(button, element) {
    if (!button) return null;
    const previous = button.__nekoIdleCat1PlaygroundQuestionBlockClone;
    if (previous && previous !== element && previous.parentNode) {
        try { previous.parentNode.removeChild(previous); } catch (_) {}
    }
    button.__nekoIdleCat1PlaygroundQuestionBlockClone = element && element.isConnected ? element : null;
    return button.__nekoIdleCat1PlaygroundQuestionBlockClone;
}

function _clearNekoIdleCat1PlaygroundQuestionBlockClone(button) {
    _storeNekoIdleCat1PlaygroundQuestionBlockClone(button, null);
}

function _consumeNekoIdleCat1PlaygroundQuestionBlockClone(button) {
    const element = button && button.__nekoIdleCat1PlaygroundQuestionBlockClone;
    if (button) button.__nekoIdleCat1PlaygroundQuestionBlockClone = null;
    return element && element.isConnected ? element : null;
}

function _createNekoIdleCat1PlaygroundQuestionBlockCloneFromMark(button) {
    const state = button && button.__nekoIdleCat1QuestionMarkState;
    const mark = state && state.mark && state.mark.isConnected ? state.mark : null;
    if (!mark || typeof mark.getBoundingClientRect !== 'function') return null;
    return _createNekoIdleCat1PlaygroundQuestionBlockClone(mark.getBoundingClientRect(), button);
}

function _createNekoIdleCat1PlaygroundQuestionBlockCloneFromScreenRect(screenRect, button) {
    const normalized = _normalizeNekoIdleScreenRect(screenRect);
    if (!normalized) return null;
    const screenLeft = Number.isFinite(Number(window.screenX)) ? Number(window.screenX) : 0;
    const screenTop = Number.isFinite(Number(window.screenY)) ? Number(window.screenY) : 0;
    return _createNekoIdleCat1PlaygroundQuestionBlockClone({
        left: normalized.left - screenLeft,
        top: normalized.top - screenTop,
        width: normalized.width,
        height: normalized.height
    }, button);
}

function _handleNekoIdleCat1QuestionMarkClick(button, event) {
    if (event) {
        try { event.preventDefault(); } catch (_) {}
        try { event.stopPropagation(); } catch (_) {}
    }
    _storeNekoIdleCat1PlaygroundQuestionBlockClone(
        button,
        _createNekoIdleCat1PlaygroundQuestionBlockCloneFromMark(button)
    );
    _clearNekoIdleCat1QuestionMark(button);
    _dispatchNekoIdleCat1PlaygroundEntryRequest(button, 'question-mark');
}

function _clearNekoIdleCat1QuestionMark(button) {
    const state = button && button.__nekoIdleCat1QuestionMarkState;
    if (!state) return;
    state.token += 1;
    state.active = false;
    if (state.timer) {
        clearTimeout(state.timer);
        state.timer = 0;
    }
    const mark = state.mark;
    _dispatchNekoIdleCat1QuestionMarkLayer(button, false, 'clear');
    if (mark) {
        mark.style.opacity = '0';
        mark.style.visibility = 'hidden';
        mark.style.transform = 'translate(-50%, -100%) scale(0.96)';
        mark.style.transition = 'opacity 180ms ease, transform 180ms ease, visibility 0s linear 180ms';
        if (mark.parentNode) {
            mark.parentNode.removeChild(mark);
        }
    }
    state.mark = null;
}

function _showNekoIdleCat1QuestionMark(button) {
    if (!button) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    const state = _getNekoIdleCat1QuestionMarkState(button);
    const mark = _ensureNekoIdleCat1QuestionMarkElement(button);
    if (!state || !mark) return false;
    if (!_positionNekoIdleCat1QuestionMark(mark, button)) {
        state.token += 1;
        state.active = false;
        if (state.timer) {
            clearTimeout(state.timer);
            state.timer = 0;
        }
        if (mark.parentNode) {
            mark.parentNode.removeChild(mark);
        }
        state.mark = null;
        return false;
    }
    if (state.timer) {
        clearTimeout(state.timer);
        state.timer = 0;
    }
    state.active = true;
    state.token += 1;
    const token = state.token;
    mark.style.transition = 'opacity 180ms ease, transform 180ms ease, visibility 0s';
    mark.style.transform = 'translate(-50%, -100%) scale(1)';
    if (!window.__NEKO_MULTI_WINDOW__) {
        mark.style.visibility = 'visible';
        mark.style.opacity = '1';
    } else {
        mark.style.visibility = 'hidden';
        mark.style.opacity = '0';
    }
    _dispatchNekoIdleCat1QuestionMarkLayer(button, true, 'show');
    state.timer = setTimeout(() => {
        const latestState = button.__nekoIdleCat1QuestionMarkState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        _clearNekoIdleCat1QuestionMark(button);
    }, _NEKO_IDLE_CAT1_QUESTION_MARK_VISIBLE_MS);
    return true;
}

function _resetNekoIdleCat1QuestionMarkKeyboardProgress() {
    _nekoIdleCat1QuestionMarkKeyboardState.progress = 0;
}

function _isNekoIdleCat1QuestionMarkKeyboardEditableTarget(target) {
    if (!target || typeof Element === 'undefined' || !(target instanceof Element)) return false;
    if (target.isContentEditable) return true;
    return !!(typeof target.closest === 'function' &&
        target.closest('input, textarea, select, [contenteditable="true"], [contenteditable="plaintext-only"]'));
}

function _normalizeNekoIdleCat1QuestionMarkKeyboardKey(event) {
    if (!event) return '';
    const code = typeof event.code === 'string' ? event.code : '';
    if (code === 'ArrowUp' ||
        code === 'ArrowDown' ||
        code === 'ArrowLeft' ||
        code === 'ArrowRight' ||
        code === 'KeyA' ||
        code === 'KeyB') {
        return code;
    }

    const key = typeof event.key === 'string' ? event.key : '';
    if (key === 'ArrowUp' || key === 'Up') return 'ArrowUp';
    if (key === 'ArrowDown' || key === 'Down') return 'ArrowDown';
    if (key === 'ArrowLeft' || key === 'Left') return 'ArrowLeft';
    if (key === 'ArrowRight' || key === 'Right') return 'ArrowRight';
    const lowerKey = key.toLowerCase();
    if (lowerKey === 'a') return 'KeyA';
    if (lowerKey === 'b') return 'KeyB';
    return '';
}

function _handleNekoIdleCat1QuestionMarkKeyboardEvent(event) {
    const state = _nekoIdleCat1QuestionMarkKeyboardState;
    if (!state.button) return false;
    if (_isNekoIdleCat1QuestionMarkKeyboardEditableTarget(event && event.target)) return false;

    const normalizedKey = _normalizeNekoIdleCat1QuestionMarkKeyboardKey(event);
    if (!normalizedKey) return false;

    const expectedKey = _NEKO_IDLE_CAT1_QUESTION_MARK_KEY_SEQUENCE[state.progress];
    if (normalizedKey !== expectedKey) {
        state.progress = normalizedKey === _NEKO_IDLE_CAT1_QUESTION_MARK_KEY_SEQUENCE[0] ? 1 : 0;
        return false;
    }

    state.progress += 1;
    if (state.progress < _NEKO_IDLE_CAT1_QUESTION_MARK_KEY_SEQUENCE.length) return false;

    _resetNekoIdleCat1QuestionMarkKeyboardProgress();
    return _showNekoIdleCat1QuestionMark(state.button);
}

function _setNekoIdleCat1QuestionMarkKeyboardTarget(button) {
    const state = _nekoIdleCat1QuestionMarkKeyboardState;
    if (button && state.button === button && state.listening) return;

    state.button = button || null;
    _resetNekoIdleCat1QuestionMarkKeyboardProgress();

    if (state.button) {
        if (!state.listening) {
            document.addEventListener('keydown', _handleNekoIdleCat1QuestionMarkKeyboardEvent, true);
            state.listening = true;
        }
        return;
    }

    if (state.listening) {
        document.removeEventListener('keydown', _handleNekoIdleCat1QuestionMarkKeyboardEvent, true);
        state.listening = false;
    }
}

function _normalizeNekoIdleCat1QuestionMarkKeyboardAssetPath(src) {
    if (!src) return '';
    try {
        return new URL(src, window.location.href).pathname;
    } catch (_) {
        return String(src || '').split(/[?#]/)[0];
    }
}

function _isNekoIdleCat1QuestionMarkKeyboardDefaultAsset(src) {
    const normalizedPath = _normalizeNekoIdleCat1QuestionMarkKeyboardAssetPath(src);
    if (!normalizedPath) return false;
    return normalizedPath === _normalizeNekoIdleCat1QuestionMarkKeyboardAssetPath(_getNekoIdleReturnAssetUrl(_NEKO_IDLE_TIER_CAT1)) ||
        normalizedPath === _normalizeNekoIdleCat1QuestionMarkKeyboardAssetPath(_getNekoIdleReturnClickAssetUrl(_NEKO_IDLE_TIER_CAT1));
}

function _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button) {
    if (!button) {
        _setNekoIdleCat1QuestionMarkKeyboardTarget(null);
        return;
    }
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) {
        if (_nekoIdleCat1QuestionMarkKeyboardState.button === button) {
            _setNekoIdleCat1QuestionMarkKeyboardTarget(null);
        }
        return;
    }
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    const src = art.getAttribute('src') || '';
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, src);
}

function _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForArt(art, tier, src) {
    const button = _getNekoIdleReturnButtonFromArt(art);
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const enabled = !!(button &&
        normalizedTier === _NEKO_IDLE_TIER_CAT1 &&
        _isNekoIdleCat1QuestionMarkKeyboardDefaultAsset(src) &&
        !_isNekoIdleReturnDragActionActive(button) &&
        !_isNekoIdleCat1IndependentActionActive(button) &&
        !_isNekoIdleCat1PlaygroundEntryOrDropActive(button) &&
        !_isNekoIdleCat1EdgePeekActive(button));

    if (enabled) {
        _setNekoIdleCat1QuestionMarkKeyboardTarget(button);
        return;
    }
    if (_nekoIdleCat1QuestionMarkKeyboardState.button === button || !button) {
        _setNekoIdleCat1QuestionMarkKeyboardTarget(null);
    }
}
