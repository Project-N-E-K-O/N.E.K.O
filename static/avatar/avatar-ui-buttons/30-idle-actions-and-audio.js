function _getNekoIdleCat1EatActionState(button) {
    if (!button) return null;
    if (!button.__nekoIdleCat1EatActionState) {
        button.__nekoIdleCat1EatActionState = {
            active: false,
            token: 0,
            timer: 0,
            audioTimer: 0,
            audio: null,
            fadeFrame: 0,
            fadeToken: 0,
            resumeJourney: false,
            catMindActionId: '',
            catMindRunId: '',
            catMindStartedAt: 0,
            catMindSource: '',
            catMindRequestId: '',
            catMindTier: _NEKO_IDLE_TIER_NONE,
            catMindResultReported: false
        };
    }
    return button.__nekoIdleCat1EatActionState;
}

function _isNekoIdleCat1EatActionActive(button) {
    const state = button && button.__nekoIdleCat1EatActionState;
    return !!(state && state.active);
}

function _isAnyNekoIdleCat1EatActionActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1EatActionActive(button);
    });
    return active;
}

function _clearNekoIdleCat1EatActionTimers(state) {
    if (!state) return;
    if (state.timer) {
        clearTimeout(state.timer);
        state.timer = 0;
    }
    if (state.audioTimer) {
        clearTimeout(state.audioTimer);
        state.audioTimer = 0;
    }
}

function _setNekoIdleCat1EatActionClass(button, active) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    button.classList.toggle('is-cat1-eating', !!active);
    if (container) {
        container.classList.toggle('is-cat1-eating', !!active);
    }
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
}

function _restoreNekoIdleCat1EatActionArt(button, state, options = {}) {
    if (!button || options.restoreArt === false) return;
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (tier !== _NEKO_IDLE_TIER_CAT1 || _isNekoIdleReturnDragActionActive(button)) return;

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            _getNekoIdleReturnCurrentArtUrl(button, tier),
            tier,
            { animate: options.animate !== false }
        );
    }

    if (state && state.resumeJourney) {
        _resumeNekoIdleCat1Journey(button);
    } else {
        _scheduleNekoIdleCat1JourneySync(button);
    }

    const container = _getNekoIdleReturnContainerFromButton(button);
    _syncNekoIdleCat1CompactMirrorReaction(
        button,
        container,
        _getNekoIdleReturnCurrentArtUrl(button, tier),
        options.reason || 'cat1-eat-action-finished'
    );
}

function _cancelNekoIdleCat1EatAction(button, options = {}) {
    const state = button && button.__nekoIdleCat1EatActionState;
    if (!state) return;
    const wasActive = !!state.active;
    state.token += 1;
    state.active = false;
    _clearNekoIdleCat1EatActionTimers(state);
    _stopNekoIdleSoundAudio(state);
    _setNekoIdleCat1EatActionClass(button, false);
    _restoreNekoIdleCat1EatActionArt(button, state, options);
    state.resumeJourney = false;
    if (wasActive && options.restoreArt !== false) {
        _syncNekoIdleCat1AmbientSoundForTier(button.getAttribute('data-neko-idle-tier'));
    }
    if (wasActive) {
        _reportNekoCatMindStateActionResult(state, _getNekoCatMindCancelResult(
            options.reason || 'cat1-eat-action-cancelled', 'cat1-eat-action-finished'
        ), { reason: options.reason || 'cat1-eat-action-cancelled' });
    }
}

function _finishNekoIdleCat1EatAction(button, token) {
    const state = button && button.__nekoIdleCat1EatActionState;
    if (!state || !state.active || state.token !== token) return;
    _cancelNekoIdleCat1EatAction(button, {
        animate: false,
        reason: 'cat1-eat-action-finished'
    });
}

function _playNekoIdleCat1EatAction(button) {
    const catMindRunOptions = arguments[1] || {};
    if (!button) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;
    if (_isNekoIdleCat1EatActionActive(button)) return false;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!container || container.style.display === 'none') return false;
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) return false;

    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
    const state = _getNekoIdleCat1EatActionState(button);
    const journey = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    const shouldResumeJourney = !!(journey &&
        !journey.paused &&
        journey.profile &&
        (journey.substate === journey.profile.walkingSubstate ||
            journey.substate === journey.profile.finishingSubstate));
    if (journey) {
        _pauseNekoIdleCat1Journey(button);
    }

    _stopNekoIdleCat1AmbientSoundAudio();
    _fadeOutNekoIdleCat1DragSound();
    _clearNekoIdleHoverPlayback(art);
    _cleanupNekoIdleArtTransition(art);
    _clearNekoIdleGifPlaybackSource(art);

    state.active = true;
    state.token += 1;
    state.resumeJourney = shouldResumeJourney;
    const run = _beginNekoCatMindStateAction(state, _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK, _NEKO_IDLE_TIER_CAT1, {
        source: catMindRunOptions.source || 'cat1-eat-runner', requestId: catMindRunOptions.requestId
    });
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);
    const token = state.token;
    const startedAt = Date.now();
    let gifDone = false;
    let audioDone = false;

    _setNekoIdleCat1EatActionClass(button, true);
    _notifyNekoCatMindRunnerStarted(catMindRunOptions, run);
    _setNekoIdleReturnArtSource(
        art,
        _NEKO_IDLE_CAT1_EAT_ASSET_URL,
        _NEKO_IDLE_TIER_CAT1,
        { animate: false }
    );
    _syncNekoIdleCat1CompactMirrorReaction(
        button,
        container,
        _NEKO_IDLE_CAT1_EAT_ASSET_URL,
        'cat1-eat-action'
    );

    const finishIfReady = () => {
        const latestState = button.__nekoIdleCat1EatActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        if (!gifDone || !audioDone) return;
        _finishNekoIdleCat1EatAction(button, token);
    };
    const markGifDone = () => {
        gifDone = true;
        finishIfReady();
    };
    const markAudioDone = () => {
        audioDone = true;
        finishIfReady();
    };

    _getNekoIdleGifDurationMs(_NEKO_IDLE_CAT1_EAT_ASSET_URL).then((durationMs) => {
        const latestState = button.__nekoIdleCat1EatActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);
        latestState.timer = setTimeout(markGifDone, delayMs);
    });

    const audio = _playNekoIdleSound(state, _NEKO_IDLE_CAT1_EAT_SOUND_URL, _NEKO_IDLE_CAT1_EAT_SOUND_VOLUME);
    if (!audio) {
        audioDone = true;
    } else {
        const scheduleAudioFallback = () => {
            const latestState = button.__nekoIdleCat1EatActionState;
            if (!latestState || !latestState.active || latestState.token !== token) return;
            if (latestState.audioTimer) {
                clearTimeout(latestState.audioTimer);
            }
            const remainingMs = _getNekoIdleAudioRemainingMs(audio) || _NEKO_IDLE_CAT1_EAT_SOUND_FALLBACK_MS;
            latestState.audioTimer = setTimeout(markAudioDone, remainingMs + 250);
        };
        audio.addEventListener('loadedmetadata', scheduleAudioFallback, { once: true });
        audio.addEventListener('ended', markAudioDone, { once: true });
        audio.addEventListener('error', markAudioDone, { once: true });
        scheduleAudioFallback();
    }
    finishIfReady();
    return true;
}

function _getNekoIdleCat1PlayActionState(button) {
    if (!button) return null;
    if (!button.__nekoIdleCat1PlayActionState) {
        button.__nekoIdleCat1PlayActionState = {
            active: false,
            token: 0,
            timer: 0,
            audioTimer: 0,
            audio: null,
            fadeFrame: 0,
            fadeToken: 0,
            resumeJourney: false,
            yarnShell: null,
            yarnHidden: false,
            catMindActionId: '',
            catMindRunId: '',
            catMindStartedAt: 0,
            catMindSource: '',
            catMindRequestId: '',
            catMindTier: _NEKO_IDLE_TIER_NONE,
            catMindResultReported: false
        };
    }
    return button.__nekoIdleCat1PlayActionState;
}

function _isNekoIdleCat1PlayActionActive(button) {
    const state = button && button.__nekoIdleCat1PlayActionState;
    return !!(state && state.active);
}

function _isAnyNekoIdleCat1PlayActionActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1PlayActionActive(button);
    });
    return active;
}

function _isNekoIdleCat1IndependentActionActive(button) {
    return _isNekoIdleCat1EatActionActive(button) ||
        _isNekoIdleCat1PlayActionActive(button) ||
        _isNekoIdleCat1PlaygroundEntryOrDropActive(button);
}

function _isAnyNekoIdleCat1IndependentActionActive() {
    return _isAnyNekoIdleCat1EatActionActive() ||
        _isAnyNekoIdleCat1PlayActionActive() ||
        _isAnyNekoIdleCat1PlaygroundDropLifecycleActive();
}

function _clearNekoIdleCat1PlayActionTimers(state) {
    if (!state) return;
    if (state.timer) {
        clearTimeout(state.timer);
        state.timer = 0;
    }
    if (state.audioTimer) {
        clearTimeout(state.audioTimer);
        state.audioTimer = 0;
    }
}

function _setNekoIdleCat1PlayActionClass(button, active) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    button.classList.toggle('is-cat1-playing', !!active);
    if (container) {
        container.classList.toggle('is-cat1-playing', !!active);
    }
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
}

function _normalizeNekoIdleRectForMessage(rect) {
    if (!rect) return null;
    const left = Math.round(Number(rect.left));
    const top = Math.round(Number(rect.top));
    const width = Math.round(Number(rect.width));
    const height = Math.round(Number(rect.height));
    if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) return null;
    return {
        left,
        top,
        width,
        height,
        right: Math.round(Number.isFinite(Number(rect.right)) ? Number(rect.right) : left + width),
        bottom: Math.round(Number.isFinite(Number(rect.bottom)) ? Number(rect.bottom) : top + height)
    };
}

function _getNekoIdleScreenOffset() {
    const x = Number.isFinite(Number(window.screenX)) ? Number(window.screenX) : Number(window.screenLeft);
    const y = Number.isFinite(Number(window.screenY)) ? Number(window.screenY) : Number(window.screenTop);
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    return { x, y };
}

function _getNekoIdleCat1PlayYarnReleasePayload(button, state, reason) {
    const payload = {
        releaseDrag: true,
        releaseReason: reason || 'cat1-play-action-finished'
    };
    const container = _getNekoIdleReturnContainerFromButton(button);
    const rect = container && typeof container.getBoundingClientRect === 'function'
        ? container.getBoundingClientRect()
        : null;
    if (!rect || rect.width <= 0 || rect.height <= 0) return payload;

    const ballSize = _NEKO_IDLE_CAT1_PLAY_YARN_RELEASE_SIZE_PX;
    const gap = 12;
    const facingRight = state && typeof state.releaseFacingRight === 'boolean'
        ? state.releaseFacingRight
        : !!(button && button.classList && button.classList.contains('is-cat1-facing-right'));
    const left = facingRight ? rect.right + gap : rect.left - ballSize - gap;
    const top = rect.top + rect.height * 0.58 - ballSize / 2;
    const maxLeft = Math.max(0, window.innerWidth - ballSize);
    const maxTop = Math.max(0, window.innerHeight - ballSize);
    const targetRect = _normalizeNekoIdleRectForMessage({
        left: Math.max(0, Math.min(left, maxLeft)),
        top: Math.max(0, Math.min(top, maxTop)),
        width: ballSize,
        height: ballSize
    });
    if (!targetRect) return payload;
    payload.targetRect = targetRect;

    const screenOffset = _getNekoIdleScreenOffset();
    if (screenOffset) {
        payload.targetScreenRect = _normalizeNekoIdleRectForMessage({
            left: targetRect.left + screenOffset.x,
            top: targetRect.top + screenOffset.y,
            width: targetRect.width,
            height: targetRect.height
        });
    }
    return payload;
}

function _postNekoIdleCat1PlayYarnVisibilityState(hidden, detail = {}) {
    const message = Object.assign({}, detail && typeof detail === 'object' ? detail : {}, {
        action: 'idle_cat1_play_yarn_visibility',
        source: 'pet-window',
        lanlan_name: _getNekoIdleCurrentLanlanName(),
        hidden: !!hidden,
        timestamp: Date.now()
    });
    try {
        window.dispatchEvent(new CustomEvent('neko:idle-cat1-play-yarn-visibility', {
            detail: Object.assign({ via: 'local' }, message)
        }));
    } catch (_) {}
    const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
    if (!channel || typeof channel.postMessage !== 'function') return;
    try {
        channel.postMessage(message);
    } catch (error) {
        if (typeof console !== 'undefined' && console.warn) {
            console.warn('[NekoIdleCat1] play yarn visibility postMessage failed:', error && error.message ? error.message : error);
        }
    }
}

function _setNekoIdleCat1PlayYarnHidden(state, hidden, detail = {}) {
    if (!state) return;
    if (hidden) {
        if (state.yarnHidden) return;
        const shell = _getNekoIdleReactChatMinimizedShell();
        if (shell) {
            shell.setAttribute('data-neko-cat1-play-hidden', 'true');
            state.yarnShell = shell;
        }
        state.yarnHidden = true;
        _postNekoIdleCat1PlayYarnVisibilityState(true);
        return;
    }
    const shell = state.yarnShell;
    state.yarnShell = null;
    const wasHidden = state.yarnHidden;
    state.yarnHidden = false;
    if (shell && shell.isConnected) {
        shell.removeAttribute('data-neko-cat1-play-hidden');
    }
    if (wasHidden) {
        _postNekoIdleCat1PlayYarnVisibilityState(false, detail);
    }
}

function _restoreNekoIdleCat1PlayActionArt(button, state, options = {}) {
    if (!button || options.restoreArt === false) return;
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    if (tier !== _NEKO_IDLE_TIER_CAT1 || _isNekoIdleReturnDragActionActive(button)) return;

    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        _setNekoIdleReturnArtSource(
            art,
            _getNekoIdleReturnCurrentArtUrl(button, tier),
            tier,
            { animate: options.animate !== false }
        );
    }

    if (state && state.resumeJourney) {
        _resumeNekoIdleCat1Journey(button);
    } else {
        _scheduleNekoIdleCat1JourneySync(button);
    }

    const container = _getNekoIdleReturnContainerFromButton(button);
    _syncNekoIdleCat1CompactMirrorReaction(
        button,
        container,
        _getNekoIdleReturnCurrentArtUrl(button, tier),
        options.reason || 'cat1-play-action-finished'
    );
}

function _cancelNekoIdleCat1PlayAction(button, options = {}) {
    const state = button && button.__nekoIdleCat1PlayActionState;
    if (!state) return;
    const wasActive = !!state.active;
    state.token += 1;
    state.active = false;
    _clearNekoIdleCat1PlayActionTimers(state);
    _stopNekoIdleSoundAudio(state);
    _setNekoIdleCat1PlayActionClass(button, false);
    _setNekoIdleCat1PlayYarnHidden(
        state,
        false,
        _getNekoIdleCat1PlayYarnReleasePayload(button, state, options.reason || 'cat1-play-action-cancelled')
    );
    _restoreNekoIdleCat1PlayActionArt(button, state, options);
    state.resumeJourney = false;
    if (wasActive && options.restoreArt !== false) {
        _syncNekoIdleCat1AmbientSoundForTier(button.getAttribute('data-neko-idle-tier'));
    }
    if (wasActive) {
        _reportNekoCatMindStateActionResult(state, _getNekoCatMindCancelResult(
            options.reason || 'cat1-play-action-cancelled', 'cat1-play-action-finished'
        ), { reason: options.reason || 'cat1-play-action-cancelled' });
    }
}

function _finishNekoIdleCat1PlayAction(button, token) {
    const state = button && button.__nekoIdleCat1PlayActionState;
    if (!state || !state.active || state.token !== token) return;
    const art = button.querySelector('.neko-idle-return-art');
    if (art) {
        art.setAttribute(_NEKO_IDLE_CAT1_PLAY_FINISHING_ATTR, 'true');
    }
    _cancelNekoIdleCat1PlayAction(button, {
        animate: true,
        reason: 'cat1-play-action-finished'
    });
}

function _playNekoIdleCat1PlayAction(button) {
    const catMindRunOptions = arguments[1] || {};
    const isCatMindRun = catMindRunOptions.source === 'cat_mind';
    if (!button) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;
    if (_isNekoIdleCat1PlayActionActive(button)) return false;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!container || container.style.display === 'none') return false;
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) return false;

    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
    const state = _getNekoIdleCat1PlayActionState(button);
    const journey = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    const shouldResumeJourney = !!(journey &&
        !journey.paused &&
        journey.profile &&
        (journey.substate === journey.profile.walkingSubstate ||
            journey.substate === journey.profile.finishingSubstate));
    if (journey) {
        _pauseNekoIdleCat1Journey(button);
    }

    _stopNekoIdleCat1AmbientSoundAudio();
    _fadeOutNekoIdleCat1DragSound();
    _clearNekoIdleHoverPlayback(art);
    _cleanupNekoIdleArtTransition(art);
    _clearNekoIdleGifPlaybackSource(art);

    state.active = true;
    state.token += 1;
    state.resumeJourney = shouldResumeJourney;
    const run = isCatMindRun
        ? _beginNekoCatMindStateAction(state, _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN, _NEKO_IDLE_TIER_CAT1, {
            source: catMindRunOptions.source, requestId: catMindRunOptions.requestId
        })
        : null;
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);
    state.releaseFacingRight = journey && typeof journey.facingRight === 'boolean'
        ? journey.facingRight
        : !!(button.classList && button.classList.contains('is-cat1-facing-right'));
    const token = state.token;
    const startedAt = Date.now();
    let gifDone = false;

    _setNekoIdleCat1PlayActionClass(button, true);
    _notifyNekoCatMindRunnerStarted(catMindRunOptions, run);
    _setNekoIdleCat1PlayYarnHidden(state, true);
    _setNekoIdleReturnArtSource(
        art,
        _NEKO_IDLE_CAT1_PLAY_ASSET_URL,
        _NEKO_IDLE_TIER_CAT1,
        { animate: false }
    );
    _syncNekoIdleCat1CompactMirrorReaction(
        button,
        container,
        _NEKO_IDLE_CAT1_PLAY_ASSET_URL,
        'cat1-play-action'
    );

    const finishIfReady = () => {
        const latestState = button.__nekoIdleCat1PlayActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        if (!gifDone) return;
        _finishNekoIdleCat1PlayAction(button, token);
    };
    const markGifDone = () => {
        gifDone = true;
        finishIfReady();
    };

    _getNekoIdleGifDurationMs(_NEKO_IDLE_CAT1_PLAY_ASSET_URL).then((durationMs) => {
        const latestState = button.__nekoIdleCat1PlayActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);
        latestState.timer = setTimeout(markGifDone, delayMs);
    });

    _playNekoIdleSound(state, _NEKO_IDLE_CAT1_PLAY_SOUND_URL, _NEKO_IDLE_CAT1_PLAY_SOUND_VOLUME);
    finishIfReady();
    return true;
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
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS);
    _setNekoIdleThoughtBubbleFocusable(button, false);
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
    _setNekoIdleThoughtBubbleFocusable(button, false);
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
    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS);
    _setNekoIdleThoughtBubbleFocusable(button, false);
    button.classList.toggle(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS, !!bubbleConfig.sleeping);
    // cache-bust 用有界的 1/2 交替：相邻两次 URL 不同足以让浏览器重载、重启 GIF 动画从头播，
    // 同时把唯一 URL 总数钉死在每个 asset 最多 2 个。此前用单调递增 token（…?restart=N，N 永增）
    // 会让每次待机气泡的图都是全新 URL，Chromium 按 URL 缓存解码位图（~2MB/张，mapped 共享内存、
    // 不计入进程私有提交），猫咪待机数日后累积到 10G+ committed —— 「挂机久了已提交内存暴涨」的根因。
    button.__nekoIdleThoughtBubbleRestartToken = ((button.__nekoIdleThoughtBubbleRestartToken || 0) % 2) + 1;
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

function _dispatchNekoIdleThoughtBubblePop(button, detail = {}) {
    if (!button || typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    window.dispatchEvent(new CustomEvent('neko:thought-bubble-pop', {
        detail: {
            button,
            container,
            tier: _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')),
            previousTier: button.__nekoIdleThoughtBubbleTier || '',
            source: detail.source || 'click',
            originalEvent: detail.originalEvent || null
        }
    }));
}

function _popNekoIdleThoughtBubble(button, detail = {}) {
    if (!button || !button.classList.contains(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS)) return false;
    if (button.classList.contains(_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS)) return false;
    _preloadNekoIdleThoughtBubblePopAsset();

    if (button.__nekoIdleThoughtBubbleTimer) {
        clearTimeout(button.__nekoIdleThoughtBubbleTimer);
        button.__nekoIdleThoughtBubbleTimer = 0;
    }
    button.__nekoIdleThoughtBubbleAudio = null;
    button.__nekoIdleThoughtBubbleTimerToken = (button.__nekoIdleThoughtBubbleTimerToken || 0) + 1;
    // cache-bust 用有界的 1/2 交替：相邻两次 URL 不同足以让浏览器重载、重启 GIF 动画从头播，
    // 同时把唯一 URL 总数钉死在每个 asset 最多 2 个。此前用单调递增 token（…?restart=N，N 永增）
    // 会让每次待机气泡的图都是全新 URL，Chromium 按 URL 缓存解码位图（~2MB/张，mapped 共享内存、
    // 不计入进程私有提交），猫咪待机数日后累积到 10G+ committed —— 「挂机久了已提交内存暴涨」的根因。
    button.__nekoIdleThoughtBubbleRestartToken = ((button.__nekoIdleThoughtBubbleRestartToken || 0) % 2) + 1;
    const timerToken = button.__nekoIdleThoughtBubbleTimerToken;

    button.classList.remove(_NEKO_IDLE_THOUGHT_BUBBLE_SLEEPING_CLASS);
    button.classList.add(_NEKO_IDLE_THOUGHT_BUBBLE_POPPING_CLASS);
    _setNekoIdleThoughtBubbleFocusable(button, false);
    const bg = button.querySelector('.neko-idle-thought-bubble-bg');
    if (bg) {
        bg.src = _getNekoIdleThoughtBubbleBgAssetUrl(
            _NEKO_IDLE_THOUGHT_BUBBLE_POP_ASSET_URL,
            button.__nekoIdleThoughtBubbleRestartToken
        );
    }
    _dispatchNekoIdleThoughtBubblePop(button, detail);
    _scheduleNekoIdleThoughtBubbleHide(button, timerToken, _NEKO_IDLE_THOUGHT_BUBBLE_POP_VISIBLE_MS);
    return true;
}

function _handleNekoIdleThoughtBubbleClick(button, event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const now = Date.now();
    if (button && now - (Number(button.__nekoIdleThoughtBubbleClickHandledAt) || 0) < 800) {
        return;
    }
    if (button) {
        button.__nekoIdleThoughtBubbleClickHandledAt = now;
    }
    const popped = _popNekoIdleThoughtBubble(button, {
        source: event && event.type === 'keydown' ? 'keyboard' : 'click',
        originalEvent: event || null
    });
    // A thought bubble is a user observation, never a direct autonomous eat.
    // Cat Mind consumes the existing pop event on a later scheduler turn.
    return popped;
}

function _showNekoIdleThoughtBubbleForSound(tier, audio = null) {
    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (normalizedTier === _NEKO_IDLE_TIER_NONE) return;
    _forEachNekoIdleReturnButton((button) => {
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== normalizedTier) return;
        const container = _getNekoIdleReturnContainerFromButton(button);
        if (!container || container.style.display === 'none') return;
        const bubbleConfig = _restartNekoIdleThoughtBubbleArt(button, normalizedTier);
        _preloadNekoIdleThoughtBubblePopAsset();
        button.classList.add(_NEKO_IDLE_THOUGHT_BUBBLE_ACTIVE_CLASS);
        _setNekoIdleThoughtBubbleFocusable(button, true);
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

function _stopNekoIdleSleepSoundAudio(options = {}) {
    const reason = options.reason || 'sleep-feedback-audio-stopped';
    const tier = _nekoIdleSleepSoundState.catMindTier || _nekoIdleSleepSoundState.tier;
    _stopNekoIdleSoundAudio(_nekoIdleSleepSoundState);
    _clearNekoIdleThoughtBubbleForTier(tier);
    _reportNekoCatMindStateActionResult(_nekoIdleSleepSoundState, _getNekoCatMindCancelResult(reason, ''), { reason, tier });
}

function _stopNekoIdleSleepSound(options = {}) {
    const reason = options.reason || 'sleep-feedback-stopped';
    _nekoIdleSleepSoundState.tier = _NEKO_IDLE_TIER_NONE;
    _nekoIdleSleepSoundState.token += 1;
    _stopNekoIdleSleepSoundAudio({ reason });
}

function _playNekoIdleSleepSound(tier, token) {
    const catMindRunOptions = arguments[2] || {};
    const config = _getNekoIdleSleepSoundConfig(tier);
    if (!config || token !== _nekoIdleSleepSoundState.token || _nekoIdleSleepSoundState.tier !== tier) {
        return false;
    }

    const actionId = tier === _NEKO_IDLE_TIER_CAT3
        ? _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK
        : _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK;
    const run = _beginNekoCatMindStateAction(_nekoIdleSleepSoundState, actionId, tier, {
        source: catMindRunOptions.source || 'sleep-feedback-runner', requestId: catMindRunOptions.requestId
    });
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);

    const audio = _playNekoIdleSound(_nekoIdleSleepSoundState, _pickNekoIdleSleepSoundSrc(config), config.volume);
    if (!audio) {
        _reportNekoCatMindStateActionRunResult(_nekoIdleSleepSoundState, run, null, _NEKO_CAT_MIND_ACTION_RESULTS.FAILED, { reason: 'audio_not_started' });
        return false;
    }
    audio.addEventListener('ended', () => {
        if (!_isNekoCatMindStateActionRunCurrent(_nekoIdleSleepSoundState, run, audio)) return;
        _clearNekoIdleThoughtBubbleForTier(tier);
        _reportNekoCatMindStateActionRunResult(_nekoIdleSleepSoundState, run, audio, _NEKO_CAT_MIND_ACTION_RESULTS.DONE, { reason: 'audio_ended' });
    }, { once: true });
    audio.addEventListener('error', () => {
        if (!_isNekoCatMindStateActionRunCurrent(_nekoIdleSleepSoundState, run, audio)) return;
        _clearNekoIdleThoughtBubbleForTier(tier);
        _reportNekoCatMindStateActionRunResult(_nekoIdleSleepSoundState, run, audio, _NEKO_CAT_MIND_ACTION_RESULTS.FAILED, { reason: 'audio_error' });
    }, { once: true });
    _runAfterNekoIdleSoundStarted(_nekoIdleSleepSoundState, audio, () => {
        _notifyNekoCatMindRunnerStarted(catMindRunOptions, run);
        if (token !== _nekoIdleSleepSoundState.token || _nekoIdleSleepSoundState.tier !== tier) return;
        _showNekoIdleThoughtBubbleForSound(tier, audio);
    });
    return true;
}

function _syncNekoIdleSleepSoundForTier(tier) {
    if (!isNekoIdleCatAudioEnabled()) {
        _stopNekoIdleSleepSound({ reason: 'audio-disabled' });
        return;
    }

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    const config = _getNekoIdleSleepSoundConfig(normalizedTier);
    if (!config) {
        _stopNekoIdleSleepSound({ reason: 'tier-change' });
        return;
    }

    if (_nekoIdleSleepSoundState.tier === normalizedTier) return;

    _nekoIdleSleepSoundState.tier = normalizedTier;
    _nekoIdleSleepSoundState.token += 1;
    _stopNekoIdleSleepSoundAudio({ reason: 'tier-change' });
}

function _clearNekoIdleCat1AmbientSoundTimer() {
    if (_nekoIdleCat1AmbientSoundState.timer) {
        clearTimeout(_nekoIdleCat1AmbientSoundState.timer);
        _nekoIdleCat1AmbientSoundState.timer = 0;
    }
}

function _stopNekoIdleCat1AmbientSoundAudio(options = {}) {
    const reason = options.reason || 'cat1-social-ping-audio-stopped';
    _stopNekoIdleSoundAudio(_nekoIdleCat1AmbientSoundState);
    _clearNekoIdleThoughtBubbleForTier(_NEKO_IDLE_TIER_CAT1);
    _reportNekoCatMindStateActionResult(_nekoIdleCat1AmbientSoundState, _getNekoCatMindCancelResult(reason, ''), {
        reason, tier: _NEKO_IDLE_TIER_CAT1
    });
}

function _pickNekoIdleCat1AmbientSoundUrl() {
    const urls = _NEKO_IDLE_CAT1_AMBIENT_SOUND_URLS;
    if (!urls || !urls.length) return '';
    return urls[Math.floor(Math.random() * urls.length)] || urls[0] || '';
}

function _playNekoIdleCat1AmbientSound(token) {
    const catMindRunOptions = arguments[1] || {};
    if (!_nekoIdleCat1AmbientSoundState.active ||
        token !== _nekoIdleCat1AmbientSoundState.token ||
        _isAnyNekoIdleCat1IndependentActionActive() ||
        _isAnyNekoIdleReturnDragActionActive()) {
        return false;
    }

    const run = _beginNekoCatMindStateAction(_nekoIdleCat1AmbientSoundState, _NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING, _NEKO_IDLE_TIER_CAT1, {
        source: catMindRunOptions.source || 'cat1-social-ping-runner', requestId: catMindRunOptions.requestId
    });
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);

    const audio = _playNekoIdleSound(
        _nekoIdleCat1AmbientSoundState,
        _pickNekoIdleCat1AmbientSoundUrl(),
        _NEKO_IDLE_CAT1_AMBIENT_SOUND_VOLUME
    );
    if (!audio) {
        _reportNekoCatMindStateActionRunResult(_nekoIdleCat1AmbientSoundState, run, null, _NEKO_CAT_MIND_ACTION_RESULTS.FAILED, { reason: 'audio_not_started' });
        return false;
    }
    audio.addEventListener('ended', () => {
        if (!_isNekoCatMindStateActionRunCurrent(_nekoIdleCat1AmbientSoundState, run, audio)) return;
        _clearNekoIdleThoughtBubbleForTier(_NEKO_IDLE_TIER_CAT1);
        _reportNekoCatMindStateActionRunResult(_nekoIdleCat1AmbientSoundState, run, audio, _NEKO_CAT_MIND_ACTION_RESULTS.DONE, { reason: 'audio_ended' });
    }, { once: true });
    audio.addEventListener('error', () => {
        if (!_isNekoCatMindStateActionRunCurrent(_nekoIdleCat1AmbientSoundState, run, audio)) return;
        _clearNekoIdleThoughtBubbleForTier(_NEKO_IDLE_TIER_CAT1);
        _reportNekoCatMindStateActionRunResult(_nekoIdleCat1AmbientSoundState, run, audio, _NEKO_CAT_MIND_ACTION_RESULTS.FAILED, { reason: 'audio_error' });
    }, { once: true });
    _runAfterNekoIdleSoundStarted(_nekoIdleCat1AmbientSoundState, audio, () => {
        _notifyNekoCatMindRunnerStarted(catMindRunOptions, run);
        if (!_nekoIdleCat1AmbientSoundState.active ||
            token !== _nekoIdleCat1AmbientSoundState.token ||
            _isAnyNekoIdleCat1IndependentActionActive() ||
            _isAnyNekoIdleReturnDragActionActive()) {
            return;
        }
        _showNekoIdleThoughtBubbleForSound(_NEKO_IDLE_TIER_CAT1, audio);
        _playNekoIdleCat1SoundReaction();
    });
    return true;
}

function _stopNekoIdleCat1AmbientSound(options = {}) {
    const reason = options.reason || 'cat1-social-ping-stopped';
    _nekoIdleCat1AmbientSoundState.active = false;
    _nekoIdleCat1AmbientSoundState.token += 1;
    _stopNekoIdleCat1AmbientSoundAudio({ reason });
}

function _syncNekoIdleCat1AmbientSoundForTier(tier) {
    if (!isNekoIdleCatAudioEnabled()) {
        _stopNekoIdleCat1AmbientSound({ reason: 'audio-disabled' });
        return;
    }

    const normalizedTier = _normalizeNekoIdleReturnTier(tier);
    if (normalizedTier !== _NEKO_IDLE_TIER_CAT1 || _isAnyNekoIdleReturnDragActionActive()) {
        _stopNekoIdleCat1AmbientSound({ reason: normalizedTier !== _NEKO_IDLE_TIER_CAT1 ? 'tier-change' : 'return-ball-drag-active' });
        return;
    }

    if (_nekoIdleCat1AmbientSoundState.active) return;

    _nekoIdleCat1AmbientSoundState.active = true;
    _nekoIdleCat1AmbientSoundState.token += 1;
    _stopNekoIdleCat1AmbientSoundAudio({ reason: 'cat1-social-ping-rescheduled' });
}

function _playNekoIdleCat1DragSound(tier) {
    if (_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1) return;
    _stopNekoIdleCat1AmbientSound();
    _stopNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState);
    _playNekoIdleSound(
        _nekoIdleCat1DragSoundState,
        _NEKO_IDLE_CAT1_DRAG_SOUND_URL,
        _NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME
    );
}

function _playNekoIdleCat1RapidDragSound(tier) {
    if (_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1) return;
    _stopNekoIdleCat1AmbientSound();
    _stopNekoIdleSoundAudio(_nekoIdleCat1DragSoundState);
    _playNekoIdleSound(
        _nekoIdleCat1RapidDragSoundState,
        _NEKO_IDLE_CAT1_RAPID_DRAG_SOUND_URL,
        _NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME
    );
}

function _fadeOutNekoIdleCat1DragSound() {
    _fadeOutNekoIdleSoundAudio(_nekoIdleCat1DragSoundState, _NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS);
    _fadeOutNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState, _NEKO_IDLE_CAT1_DRAG_SOUND_FADE_OUT_MS);
}

function _stopNekoGoodbyeIdleBallCatSounds() {
    _syncNekoIdleSleepSoundForTier(_NEKO_IDLE_TIER_NONE);
    _stopNekoIdleCat1AmbientSound();
    _stopNekoIdleSoundAudio(_nekoIdleCat1DragSoundState);
    _stopNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState);
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
