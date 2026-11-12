function _pickNekoIdleCat1EatActionAssetUrl() {
    const slotIndex = Math.floor(Math.random() * _NEKO_IDLE_CAT1_EAT_ASSET_SLOTS.length);
    return _getNekoCatAppearanceUrl(_NEKO_IDLE_CAT1_EAT_ASSET_SLOTS[slotIndex], { random: false });
}

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
            assetUrl: '',
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

function _getNekoIdleCat1EatActionAssetUrl(button) {
    const state = button && button.__nekoIdleCat1EatActionState;
    return state && state.active ? state.assetUrl : '';
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
    state.assetUrl = '';
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
    if (!_isNekoCatActionResourceAvailable('cat1_eat_snack')) return false;
    if (!button) return false;
    if (_isNekoIdleDesktopWindowInteractionActionActive(button)) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;
    if (_isNekoIdleCat1EatActionActive(button)) return false;
    if (_isNekoIdleCat1StretchActionActive(button) || _isNekoIdleCat1VisualActionActive(button)) return false;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!container || container.style.display === 'none') return false;
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) return false;
    const actionAssetUrl = _pickNekoIdleCat1EatActionAssetUrl();
    if (!actionAssetUrl) return false;

    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
    const state = _getNekoIdleCat1EatActionState(button);
    const journey = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    const shouldResumeJourney = _shouldResumeNekoIdleJourneyAfterActionTakeover(art, journey);
    if (shouldResumeJourney && !journey.paused) {
        _pauseNekoIdleCat1Journey(button);
    }

    _stopNekoIdleCat1AmbientSoundAudio();
    _fadeOutNekoIdleCat1DragSound();
    _clearNekoIdleHoverPlayback(art);
    _cleanupNekoIdleArtTransition(art);
    _clearNekoIdleGifPlaybackSource(art);

    state.active = true;
    state.token += 1;
    state.assetUrl = actionAssetUrl;
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
        actionAssetUrl,
        _NEKO_IDLE_TIER_CAT1,
        { animate: false }
    );
    _syncNekoIdleCat1CompactMirrorReaction(
        button,
        container,
        actionAssetUrl,
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

    _getNekoIdleAnimationDurationMs(actionAssetUrl).then((durationMs) => {
        const latestState = button.__nekoIdleCat1EatActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);
        latestState.timer = setTimeout(markGifDone, delayMs);
    });

    const audio = _playNekoIdleSound(
        state,
        _getNekoCatVoiceUrl('cat1.eat'),
        _NEKO_IDLE_CAT1_EAT_SOUND_VOLUME
    );
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

function _shouldResumeNekoIdleJourneyAfterActionTakeover(art, journey) {
    return !!(journey &&
        journey.profile &&
        journey.substate === journey.profile.walkingSubstate &&
        (!journey.paused || (art && art.__nekoIdleHoverPausedJourney === true)));
}

const _NEKO_IDLE_CAT1_VISUAL_ACTION_PROFILES = Object.freeze({
    happy: Object.freeze({
        key: 'happy',
        slot: 'action.cat1.happy',
        capability: 'cat1_happy',
        requestName: 'requestCat1Happy',
        className: 'is-cat1-happy',
        layout: 'normal',
        startedReason: 'cat1-happy-action',
        cancelledReason: 'cat1-happy-action-cancelled',
        finishedReason: 'cat1-happy-action-finished'
    }),
    touch: Object.freeze({
        key: 'touch',
        slot: 'action.cat1.touch',
        capability: 'cat1_touch',
        requestName: 'requestCat1Touch',
        className: 'is-cat1-touching',
        layout: 'normal',
        startedReason: 'cat1-touch-action',
        cancelledReason: 'cat1-touch-action-cancelled',
        finishedReason: 'cat1-touch-action-finished'
    }),
    cosplay1: Object.freeze({
        key: 'cosplay1',
        slot: 'action.cat1.cosplay_1',
        capability: 'cat1_cosplay_1',
        requestName: 'requestCat1Cosplay1',
        className: 'is-cat1-cosplay-1',
        layout: 'normal',
        startedReason: 'cat1-cosplay-1-action',
        cancelledReason: 'cat1-cosplay-1-action-cancelled',
        finishedReason: 'cat1-cosplay-1-action-finished'
    }),
    change: Object.freeze({
        key: 'change', slot: 'action.cat1.change', capability: 'cat1_change',
        requestName: 'requestCat1Change', layout: 'normal',
        startedReason: 'cat1-change-action', cancelledReason: 'cat1-change-action-cancelled',
        finishedReason: 'cat1-change-action-finished'
    }),
    cosplay2: Object.freeze({
        key: 'cosplay2', slot: 'action.cat1.cosplay_2', capability: 'cat1_cosplay_2',
        requestName: 'requestCat1Cosplay2', layout: 'normal',
        startedReason: 'cat1-cosplay-2-action', cancelledReason: 'cat1-cosplay-2-action-cancelled',
        finishedReason: 'cat1-cosplay-2-action-finished'
    }),
    eat2: Object.freeze({
        key: 'eat2', slot: 'action.cat1.eat_2', capability: 'cat1_eat_2',
        requestName: 'requestCat1Eat2', layout: 'normal',
        startedReason: 'cat1-eat-2-action', cancelledReason: 'cat1-eat-2-action-cancelled',
        finishedReason: 'cat1-eat-2-action-finished'
    }),
    eat3: Object.freeze({
        key: 'eat3', slot: 'action.cat1.eat_3', capability: 'cat1_eat_3',
        requestName: 'requestCat1Eat3', layout: 'normal',
        startedReason: 'cat1-eat-3-action', cancelledReason: 'cat1-eat-3-action-cancelled',
        finishedReason: 'cat1-eat-3-action-finished'
    }),
    laugh: Object.freeze({
        key: 'laugh', slot: 'action.cat1.laugh', capability: 'cat1_laugh',
        requestName: 'requestCat1Laugh', layout: 'normal',
        startedReason: 'cat1-laugh-action', cancelledReason: 'cat1-laugh-action-cancelled',
        finishedReason: 'cat1-laugh-action-finished'
    }),
    lionDance: Object.freeze({
        key: 'lionDance', slot: 'action.cat1.lion_dance', capability: 'cat1_lion_dance',
        requestName: 'requestCat1LionDance', layout: 'tall-bottom',
        startedReason: 'cat1-lion-dance-action', cancelledReason: 'cat1-lion-dance-action-cancelled',
        finishedReason: 'cat1-lion-dance-action-finished'
    }),
    love1: Object.freeze({
        key: 'love1', slot: 'action.cat1.love_1', capability: 'cat1_love_1',
        requestName: 'requestCat1Love1', layout: 'normal',
        startedReason: 'cat1-love-1-action', cancelledReason: 'cat1-love-1-action-cancelled',
        finishedReason: 'cat1-love-1-action-finished'
    }),
    mouse: Object.freeze({
        key: 'mouse', slot: 'action.cat1.mouse', capability: 'cat1_mouse',
        requestName: 'requestCat1Mouse', layout: 'wide-centered',
        startedReason: 'cat1-mouse-action', cancelledReason: 'cat1-mouse-action-cancelled',
        finishedReason: 'cat1-mouse-action-finished'
    }),
    wave: Object.freeze({
        key: 'wave', slot: 'action.cat1.wave', capability: 'cat1_wave',
        requestName: 'requestCat1Wave', layout: 'normal',
        startedReason: 'cat1-wave-action', cancelledReason: 'cat1-wave-action-cancelled',
        finishedReason: 'cat1-wave-action-finished'
    }),
    sad: Object.freeze({
        key: 'sad', slot: 'action.cat1.sad', capability: 'cat1_sad',
        requestName: 'requestCat1Sad', layout: 'normal',
        startedReason: 'cat1-sad-action', cancelledReason: 'cat1-sad-action-cancelled',
        finishedReason: 'cat1-sad-action-finished'
    }),
    scared: Object.freeze({
        key: 'scared', slot: 'action.cat1.scared', capability: 'cat1_scared',
        requestName: 'requestCat1Scared', layout: 'normal',
        startedReason: 'cat1-scared-action', cancelledReason: 'cat1-scared-action-cancelled',
        finishedReason: 'cat1-scared-action-finished'
    }),
    stroke: Object.freeze({
        key: 'stroke', slot: 'action.cat1.stroke', capability: 'cat1_stroke',
        requestName: 'requestCat1Stroke', layout: 'normal',
        startedReason: 'cat1-stroke-action', cancelledReason: 'cat1-stroke-action-cancelled',
        finishedReason: 'cat1-stroke-action-finished'
    }),
    wagging: Object.freeze({
        key: 'wagging', slot: 'action.cat1.wagging', capability: 'cat1_wagging',
        requestName: 'requestCat1Wagging', layout: 'normal',
        startedReason: 'cat1-wagging-action', cancelledReason: 'cat1-wagging-action-cancelled',
        finishedReason: 'cat1-wagging-action-finished'
    })
});

const _NEKO_IDLE_SLEEP_VISUAL_ACTION_PROFILES = Object.freeze({
    cat2Back1_1: Object.freeze({
        key: 'cat2Back1_1', slot: 'action.cat2.back1_1', capability: 'cat2_back1_1',
        requestName: 'requestCat2Back1_1', tier: 'cat2', terminalTier: 'cat1',
        layout: 'wide-125-centered',
        startedReason: 'cat2-back1-1-action', cancelledReason: 'cat2-back1-1-action-cancelled',
        finishedReason: 'cat2-back1-1-action-finished'
    }),
    cat2Turn1: Object.freeze({
        key: 'cat2Turn1', slot: 'action.cat2.turn_1', capability: 'cat2_turn_1',
        requestName: 'requestCat2Turn1', tier: 'cat2',
        layout: 'wide-centered',
        startedReason: 'cat2-turn-1-action', cancelledReason: 'cat2-turn-1-action-cancelled',
        finishedReason: 'cat2-turn-1-action-finished'
    }),
    cat3Turn2: Object.freeze({
        key: 'cat3Turn2', slot: 'action.cat3.turn_2', capability: 'cat3_turn_2',
        requestName: 'requestCat3Turn2', tier: 'cat3',
        layout: 'normal',
        startedReason: 'cat3-turn-2-action', cancelledReason: 'cat3-turn-2-action-cancelled',
        finishedReason: 'cat3-turn-2-action-finished'
    }),
    cat3Back1_1: Object.freeze({
        key: 'cat3Back1_1', slot: 'action.cat3.back1_1', capability: 'cat3_back1_1',
        requestName: 'requestCat3Back1_1', tier: 'cat3', terminalTier: 'cat1',
        layout: 'normal',
        startedReason: 'cat3-back1-1-action', cancelledReason: 'cat3-back1-1-action-cancelled',
        finishedReason: 'cat3-back1-1-action-finished'
    }),
    cat3Back1_2: Object.freeze({
        key: 'cat3Back1_2', slot: 'action.cat3.back1_2', capability: 'cat3_back1_2',
        requestName: 'requestCat3Back1_2', tier: 'cat3', terminalTier: 'cat1',
        layout: 'normal',
        startedReason: 'cat3-back1-2-action', cancelledReason: 'cat3-back1-2-action-cancelled',
        finishedReason: 'cat3-back1-2-action-finished'
    })
});

const _NEKO_IDLE_VISUAL_ACTION_PROFILES = Object.freeze({
    ..._NEKO_IDLE_CAT1_VISUAL_ACTION_PROFILES,
    ..._NEKO_IDLE_SLEEP_VISUAL_ACTION_PROFILES
});

const _NEKO_IDLE_CAT1_RANDOM_VISUAL_ACTION_KEYS = Object.freeze([
    'happy',
    'touch',
    'mouse',
    'laugh',
    'love1',
    'wagging'
]);
function _pickNekoIdleRandomVisualActionKey(tier) {
    const roll = Math.max(0, Math.min(0.999999999999, Number(Math.random()) || 0));
    if (tier === _NEKO_IDLE_TIER_CAT2) return roll < 0.8 ? 'cat2Turn1' : 'cat2Back1_1';
    if (tier === _NEKO_IDLE_TIER_CAT3) {
        if (roll < 0.8) return 'cat3Turn2';
        return roll < 0.9 ? 'cat3Back1_1' : 'cat3Back1_2';
    }
    if (tier !== _NEKO_IDLE_TIER_CAT1) return '';
    const index = Math.floor(roll * _NEKO_IDLE_CAT1_RANDOM_VISUAL_ACTION_KEYS.length);
    return _NEKO_IDLE_CAT1_RANDOM_VISUAL_ACTION_KEYS[index] || '';
}

function _getNekoIdleCat1VisualActionProfile(actionKey) {
    return _NEKO_IDLE_VISUAL_ACTION_PROFILES[actionKey] || null;
}

function _getNekoIdleVisualActionTier(profile) {
    return profile && profile.tier ? profile.tier : _NEKO_IDLE_TIER_CAT1;
}

function _getNekoIdleVisualActionTerminalTier(profile) {
    return profile && profile.terminalTier
        ? profile.terminalTier
        : _getNekoIdleVisualActionTier(profile);
}

function _getNekoIdleCat1VisualActionState(button) {
    if (!button) return null;
    if (!button.__nekoIdleCat1VisualActionState) {
        button.__nekoIdleCat1VisualActionState = {
            active: false,
            actionKey: '',
            token: 0,
            timer: 0,
            entryTimer: 0,
            entryPlaybackToken: 0,
            sourceLoadCleanup: null,
            resumeJourney: false,
            presentationOnTerminal: null
        };
    }
    // Keep the original happy state handle stable for callers that started using
    // the first visual action before the shared single-cycle runner existed.
    button.__nekoIdleCat1HappyActionState = button.__nekoIdleCat1VisualActionState;
    return button.__nekoIdleCat1VisualActionState;
}

function _getNekoIdleCat1ActiveVisualActionProfile(button) {
    const state = button && button.__nekoIdleCat1VisualActionState;
    return state && state.active
        ? _getNekoIdleCat1VisualActionProfile(state.actionKey)
        : null;
}

function _isNekoIdleCat1VisualActionActive(button, actionKey = '') {
    const state = button && button.__nekoIdleCat1VisualActionState;
    return !!(state && state.active && (!actionKey || state.actionKey === actionKey));
}

function _isAnyNekoIdleCat1VisualActionActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1VisualActionActive(button);
    });
    return active;
}

function _getNekoIdleCat1VisualActionAssetUrl(button) {
    const profile = _getNekoIdleCat1ActiveVisualActionProfile(button);
    return profile ? _getNekoIdleCat1VisualActionAssetUrlForSlot(profile.slot) : '';
}

function _setNekoIdleCat1VisualActionClass(button, activeProfile = null) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    const active = !!activeProfile;
    button.classList.toggle('is-cat1-visual-action', active);
    if (container) {
        container.classList.toggle('is-cat1-visual-action', active);
    }
    Object.values(_NEKO_IDLE_VISUAL_ACTION_PROFILES).forEach((profile) => {
        if (!profile.className) return;
        const profileActive = active && activeProfile.key === profile.key;
        button.classList.toggle(profile.className, profileActive);
        if (container) {
            container.classList.toggle(profile.className, profileActive);
        }
    });
    if (activeProfile) {
        button.setAttribute('data-neko-cat1-visual-action', activeProfile.key);
        if (activeProfile.layout && activeProfile.layout !== 'normal') {
            button.setAttribute('data-neko-cat1-visual-layout', activeProfile.layout);
        } else {
            button.removeAttribute('data-neko-cat1-visual-layout');
        }
    } else {
        button.removeAttribute('data-neko-cat1-visual-action');
        button.removeAttribute('data-neko-cat1-visual-layout');
    }
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
}

function _restoreNekoIdleCat1VisualActionArt(button, state, options = {}) {
    if (!button || options.restoreArt === false) return;
    const tier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    const profile = options.profile || null;
    if ((profile && tier !== _getNekoIdleVisualActionTier(profile)) ||
        _isNekoIdleReturnDragActionActive(button)) return;

    const art = button.querySelector('.neko-idle-return-art');
    const idleSrc = _getNekoIdleReturnCurrentArtUrl(button, tier);
    if (art) {
        _setNekoIdleReturnArtSource(art, idleSrc, tier, { animate: options.animate === true });
    }

    if (tier === _NEKO_IDLE_TIER_CAT1) {
        if (state && state.resumeJourney) {
            _resumeNekoIdleCat1Journey(button);
        } else {
            _scheduleNekoIdleCat1JourneySync(button);
        }

        _syncNekoIdleCat1CompactMirrorReaction(
            button,
            _getNekoIdleReturnContainerFromButton(button),
            idleSrc,
            options.reason || 'cat1-visual-action-finished'
        );
    }
}

function _finishNekoIdleVisualActionTierTransition(profile) {
    if (!profile ||
        _getNekoIdleVisualActionTerminalTier(profile) === _getNekoIdleVisualActionTier(profile)) {
        return true;
    }
    const autoGoodbye = typeof window !== 'undefined' ? window.nekoAutoGoodbye : null;
    if (_getNekoIdleVisualActionTerminalTier(profile) !== _NEKO_IDLE_TIER_CAT1 ||
        !autoGoodbye || typeof autoGoodbye.demoteVisualTierToCat1 !== 'function') {
        return false;
    }
    return autoGoodbye.demoteVisualTierToCat1(
        profile.finishedReason,
        profile.finishedReason
    ) === true;
}

function _cancelNekoIdleCat1VisualAction(button, options = {}) {
    const state = button && button.__nekoIdleCat1VisualActionState;
    if (!state || !state.active) return;
    const profile = _getNekoIdleCat1ActiveVisualActionProfile(button);
    if (!profile) return;
    const presentationOnTerminal = typeof state.presentationOnTerminal === 'function'
        ? state.presentationOnTerminal
        : null;
    state.presentationOnTerminal = null;
    state.token += 1;
    state.active = false;
    state.actionKey = '';
    if (state.sourceLoadCleanup) {
        state.sourceLoadCleanup();
        state.sourceLoadCleanup = null;
    }
    if (state.timer) {
        clearTimeout(state.timer);
        state.timer = 0;
    }
    if (state.entryTimer) {
        clearTimeout(state.entryTimer);
        state.entryTimer = 0;
    }
    _setNekoIdleCat1VisualActionClass(button, null);
    const requestedReason = options.reason || profile.cancelledReason;
    const isFinished = requestedReason === profile.finishedReason;
    const tierTransitionRequired = isFinished &&
        _getNekoIdleVisualActionTerminalTier(profile) !== _getNekoIdleVisualActionTier(profile);
    const tierTransitionFinished = !tierTransitionRequired ||
        _finishNekoIdleVisualActionTierTransition(profile);
    const reason = tierTransitionFinished ? requestedReason : profile.cancelledReason;
    if (!tierTransitionRequired || !tierTransitionFinished) {
        _restoreNekoIdleCat1VisualActionArt(button, state, { ...options, profile, reason });
    }
    state.resumeJourney = false;
    if (presentationOnTerminal) {
        const result = _getNekoCatMindCancelResult(reason, profile.finishedReason);
        try {
            presentationOnTerminal(result, { reason });
        } catch (_) {}
    }
}

function _finishNekoIdleCat1VisualAction(button, actionKey, token) {
    const state = button && button.__nekoIdleCat1VisualActionState;
    const profile = _getNekoIdleCat1VisualActionProfile(actionKey);
    if (!profile || !state || !state.active || state.actionKey !== actionKey || state.token !== token) return;
    _cancelNekoIdleCat1VisualAction(button, {
        animate: false,
        reason: profile.finishedReason
    });
}

function _playNekoIdleCat1VisualAction(button, actionKey, options = {}) {
    options = options && typeof options === 'object' ? options : {};
    const profile = _getNekoIdleCat1VisualActionProfile(actionKey);
    if (!profile || !_isNekoCatActionResourceAvailable(profile.capability)) return false;
    if (!button) return false;
    if (_isNekoIdleDesktopWindowInteractionActionActive(button)) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    const currentTier = _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier'));
    const sourceTier = profile.tier || _NEKO_IDLE_TIER_CAT1;
    const prepareCat1 = currentTier !== _NEKO_IDLE_TIER_CAT1 && actionKey === 'scared' &&
        typeof options.prepareCat1 === 'function' ? options.prepareCat1 : null;
    if (currentTier !== sourceTier && !prepareCat1) return false;
    if (_isNekoIdleReturnDragActionBlocking(button) || _isAnyNekoIdleReturnDragActionBlocking()) return false;
    if (_isNekoIdleCompactSurfaceDragging()) return false;
    if (_isNekoCatMindReturnPending(button) || _isAnyNekoCatMindReturnPending()) return false;
    if (_isNekoCatMindTransitionActive(button)) return false;
    if (_isNekoIdleCat1EdgePeekActive(button)) return false;
    if (_isNekoIdleCat1VisualActionActive(button) ||
        _isAnyNekoIdleCat1IndependentActionActive() ||
        (typeof _isNekoCatMindAudioActionActive === 'function' && _isNekoCatMindAudioActionActive())) return false;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!container || container.style.display === 'none') return false;
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) return false;
    const actionSrc = _getNekoIdleCat1VisualActionAssetUrlForSlot(profile.slot);
    if (!actionSrc) return false;
    if (prepareCat1) {
        if (prepareCat1() !== true) return false;
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    }

    const state = _getNekoIdleCat1VisualActionState(button);
    const journey = sourceTier === _NEKO_IDLE_TIER_CAT1
        ? button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey
        : null;
    const shouldResumeJourney = sourceTier === _NEKO_IDLE_TIER_CAT1 &&
        _shouldResumeNekoIdleJourneyAfterActionTakeover(art, journey);
    if (shouldResumeJourney && !journey.paused) {
        _pauseNekoIdleCat1Journey(button);
    }

    _clearNekoIdleHoverPlayback(art);
    _cleanupNekoIdleArtTransition(art);
    _clearNekoIdleGifPlaybackSource(art);

    state.active = true;
    state.actionKey = actionKey;
    state.token += 1;
    state.resumeJourney = shouldResumeJourney;
    state.presentationOnTerminal = typeof options.onTerminal === 'function'
        ? options.onTerminal
        : null;
    const token = state.token;

    _setNekoIdleCat1VisualActionClass(button, profile);
    let finishSchedulingStarted = false;
    const scheduleActionFinish = () => {
        if (finishSchedulingStarted) return;
        const currentState = button.__nekoIdleCat1VisualActionState;
        if (!currentState || !currentState.active || currentState.actionKey !== actionKey || currentState.token !== token) return;
        finishSchedulingStarted = true;
        if (currentState.entryTimer) {
            clearTimeout(currentState.entryTimer);
            currentState.entryTimer = 0;
        }
        const startedAt = Date.now();
        _getNekoIdleAnimationDurationMs(actionSrc).then((durationMs) => {
            const latestState = button.__nekoIdleCat1VisualActionState;
            if (!latestState || !latestState.active || latestState.actionKey !== actionKey || latestState.token !== token) return;
            const elapsedMs = Math.max(0, Date.now() - startedAt);
            const delayMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);
            latestState.timer = setTimeout(() => {
                latestState.timer = 0;
                _finishNekoIdleCat1VisualAction(button, actionKey, token);
            }, delayMs);
        });
    };
    const animateEntry = options.animateEntry === true ||
        (options.animateEntry !== false && profile.animateEntry === true);
    if (animateEntry) {
        // Chromium shares an animated-image timeline across elements using the
        // same URL. Alternate two bounded URLs so the post-fade main art starts
        // at frame zero without growing the decoded-image cache indefinitely.
        state.entryPlaybackToken = (state.entryPlaybackToken % 2) + 1;
        const entryPlaybackToken = state.entryPlaybackToken;
        const cancelFailedEntry = (restoreArt) => {
            const currentState = button.__nekoIdleCat1VisualActionState;
            if (!currentState || !currentState.active ||
                currentState.actionKey !== actionKey || currentState.token !== token) return;
            _cancelNekoIdleCat1VisualAction(button, {
                restoreArt: restoreArt === true,
                reason: profile.cancelledReason
            });
        };
        _setNekoIdleReturnArtSource(art, actionSrc, sourceTier, {
            animate: true,
            restartAnimation: true,
            playbackToken: entryPlaybackToken,
            onTransitionEnd: scheduleActionFinish,
            onTransitionError: () => cancelFailedEntry(true)
        });
        if (!finishSchedulingStarted) {
            const entryTransition = art.__nekoIdleTransitionNext;
            const entryDeadlineAt = Date.now() + Math.max(5000, _NEKO_IDLE_RETURN_TRANSITION_MS * 5);
            const verifyEntry = () => {
                const currentState = button.__nekoIdleCat1VisualActionState;
                if (!currentState || !currentState.active ||
                    currentState.actionKey !== actionKey || currentState.token !== token) return;
                const currentSrc = art.getAttribute('src') || '';
                if (art.__nekoIdleTransitionNext === entryTransition &&
                    art.__nekoIdleTransitionTo === actionSrc) {
                    if (Date.now() >= entryDeadlineAt) {
                        _cleanupNekoIdleArtTransition(art);
                        cancelFailedEntry(true);
                        return;
                    } else {
                        currentState.entryTimer = setTimeout(verifyEntry, 50);
                        return;
                    }
                }
                if (!_isNekoIdleAnimationRestartUrlForSource(
                    currentSrc,
                    actionSrc,
                    entryPlaybackToken
                )) {
                    cancelFailedEntry(false);
                    return;
                }
                scheduleActionFinish();
            };
            state.entryTimer = setTimeout(verifyEntry, _NEKO_IDLE_RETURN_TRANSITION_MS);
        }
    } else {
        let onLoad = null;
        let onError = null;
        if (sourceTier !== _NEKO_IDLE_TIER_CAT1) {
            onLoad = () => {
                if (!_isNekoIdleAnimationRestartUrlForSource(art.getAttribute('src') || '', actionSrc)) return;
                if (state.sourceLoadCleanup) {
                    state.sourceLoadCleanup();
                    state.sourceLoadCleanup = null;
                }
                scheduleActionFinish();
            };
            onError = () => {
                if (!_isNekoIdleAnimationRestartUrlForSource(art.getAttribute('src') || '', actionSrc)) return;
                _cancelNekoIdleCat1VisualAction(button, { reason: profile.cancelledReason });
            };
            art.addEventListener('load', onLoad);
            art.addEventListener('error', onError);
            state.sourceLoadCleanup = () => {
                art.removeEventListener('load', onLoad);
                art.removeEventListener('error', onError);
            };
            state.entryTimer = setTimeout(
                onError,
                Math.max(5000, _NEKO_IDLE_RETURN_TRANSITION_MS * 5)
            );
        }
        _setNekoIdleReturnArtSource(art, actionSrc, sourceTier, { animate: false });
        if (sourceTier === _NEKO_IDLE_TIER_CAT1) {
            scheduleActionFinish();
        } else if (art.complete) {
            if (art.naturalWidth > 0) {
                onLoad();
            } else {
                onError();
            }
        }
    }
    if (sourceTier === _NEKO_IDLE_TIER_CAT1) {
        _syncNekoIdleCat1CompactMirrorReaction(
            button,
            container,
            actionSrc,
            profile.startedReason,
            profile.layout
        );
    }

    return true;
}

function _getNekoIdleCat1HappyActionState(button) {
    return _getNekoIdleCat1VisualActionState(button);
}

function _isNekoIdleCat1HappyActionActive(button) {
    return _isNekoIdleCat1VisualActionActive(button, 'happy');
}

function _isAnyNekoIdleCat1HappyActionActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1HappyActionActive(button);
    });
    return active;
}

function _cancelNekoIdleCat1HappyAction(button, options = {}) {
    if (_isNekoIdleCat1HappyActionActive(button)) {
        _cancelNekoIdleCat1VisualAction(button, options);
    }
}

function _playNekoIdleCat1HappyAction(button, options = {}) {
    return _playNekoIdleCat1VisualAction(button, 'happy', options);
}

function _getNekoIdleCat1TouchActionState(button) {
    return _getNekoIdleCat1VisualActionState(button);
}

function _isNekoIdleCat1TouchActionActive(button) {
    return _isNekoIdleCat1VisualActionActive(button, 'touch');
}

function _cancelNekoIdleCat1TouchAction(button, options = {}) {
    if (_isNekoIdleCat1TouchActionActive(button)) {
        _cancelNekoIdleCat1VisualAction(button, options);
    }
}

function _playNekoIdleCat1TouchAction(button, options = {}) {
    return _playNekoIdleCat1VisualAction(button, 'touch', options);
}

function _getNekoIdleCat1Cosplay1ActionState(button) {
    return _getNekoIdleCat1VisualActionState(button);
}

function _isNekoIdleCat1Cosplay1ActionActive(button) {
    return _isNekoIdleCat1VisualActionActive(button, 'cosplay1');
}

function _cancelNekoIdleCat1Cosplay1Action(button, options = {}) {
    if (_isNekoIdleCat1Cosplay1ActionActive(button)) {
        _cancelNekoIdleCat1VisualAction(button, options);
    }
}

function _playNekoIdleCat1Cosplay1Action(button, options = {}) {
    return _playNekoIdleCat1VisualAction(button, 'cosplay1', options);
}

function _getNekoIdleCat1StretchActionState(button) {
    if (!button) return null;
    if (!button.__nekoIdleCat1StretchActionState) {
        button.__nekoIdleCat1StretchActionState = {
            active: false,
            token: 0,
            timer: 0,
            audio: null,
            fadeFrame: 0,
            fadeToken: 0,
            resumeJourney: false,
            presentationOnTerminal: null
        };
    }
    return button.__nekoIdleCat1StretchActionState;
}

function _isNekoIdleCat1StretchActionActive(button) {
    const state = button && button.__nekoIdleCat1StretchActionState;
    return !!(state && state.active);
}

function _isAnyNekoIdleCat1StretchActionActive() {
    let active = false;
    _forEachNekoIdleReturnButton((button) => {
        if (active) return;
        active = _isNekoIdleCat1StretchActionActive(button);
    });
    return active;
}

function _setNekoIdleCat1StretchActionClass(button, active) {
    if (!button) return;
    const container = _getNekoIdleReturnContainerFromButton(button);
    button.classList.toggle('is-cat1-stretching', !!active);
    if (container) {
        container.classList.toggle('is-cat1-stretching', !!active);
    }
    _syncNekoIdleCat1QuestionMarkKeyboardAvailabilityForButton(button);
}

function _restoreNekoIdleCat1StretchActionArt(button, state, options = {}) {
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
}

function _cancelNekoIdleCat1StretchAction(button, options = {}) {
    const state = button && button.__nekoIdleCat1StretchActionState;
    if (!state) return;
    const wasActive = !!state.active;
    const presentationOnTerminal = typeof state.presentationOnTerminal === 'function'
        ? state.presentationOnTerminal
        : null;
    state.presentationOnTerminal = null;
    state.token += 1;
    state.active = false;
    if (state.timer) {
        clearTimeout(state.timer);
        state.timer = 0;
    }
    _stopNekoIdleSoundAudio(state);
    _setNekoIdleCat1StretchActionClass(button, false);
    _restoreNekoIdleCat1StretchActionArt(button, state, options);
    state.resumeJourney = false;
    if (wasActive && presentationOnTerminal) {
        const reason = options.reason || 'cat1-stretch-action-cancelled';
        const result = _getNekoCatMindCancelResult(reason, 'cat1-stretch-action-finished');
        try {
            presentationOnTerminal(result, { reason });
        } catch (_) {}
    }
}

function _finishNekoIdleCat1StretchAction(button, token) {
    const state = button && button.__nekoIdleCat1StretchActionState;
    if (!state || !state.active || state.token !== token) return;
    _cancelNekoIdleCat1StretchAction(button, {
        animate: true,
        reason: 'cat1-stretch-action-finished'
    });
}

function _playNekoIdleCat1StretchAction(button, options = {}) {
    if (typeof _getNekoCatAppearanceUrl !== 'function' ||
        !_getNekoCatAppearanceUrl('movement.cat1.stretch', { random: false })) return false;
    if (!button) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    if (_isNekoIdleReturnDragActionBlocking(button) || _isAnyNekoIdleReturnDragActionBlocking()) return false;
    if (_isNekoIdleCompactSurfaceDragging()) return false;
    if (_isNekoCatMindReturnPending(button) || _isAnyNekoCatMindReturnPending()) return false;
    if (_isNekoCatMindTransitionActive(button)) return false;
    if (_isNekoIdleCat1EdgePeekActive(button)) return false;
    if (_isNekoIdleCat1StretchActionActive(button) ||
        _isNekoIdleCat1EatActionActive(button) ||
        _isNekoIdleCat1PlayActionActive(button) ||
        _isAnyNekoIdleCat1IndependentActionActive()) return false;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!container || container.style.display === 'none') return false;
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) return false;

    const state = _getNekoIdleCat1StretchActionState(button);
    const journey = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    const shouldResumeJourney = _shouldResumeNekoIdleJourneyAfterActionTakeover(art, journey);
    if (shouldResumeJourney && !journey.paused) {
        _pauseNekoIdleCat1Journey(button);
    }

    _clearNekoIdleHoverPlayback(art);
    _cleanupNekoIdleArtTransition(art);
    _clearNekoIdleGifPlaybackSource(art);

    state.active = true;
    state.token += 1;
    state.resumeJourney = shouldResumeJourney;
    state.presentationOnTerminal = typeof options.onTerminal === 'function'
        ? options.onTerminal
        : null;
    const token = state.token;
    const startedAt = Date.now();
    const stretchSrc = _getNekoIdleCat1StretchAssetUrl();

    _setNekoIdleCat1StretchActionClass(button, true);
    _setNekoIdleReturnArtSource(art, stretchSrc, _NEKO_IDLE_TIER_CAT1, { animate: true });

    _getNekoIdleAnimationDurationMs(stretchSrc).then((durationMs) => {
        const latestState = button.__nekoIdleCat1StretchActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs) +
            _NEKO_IDLE_CAT1_STRETCH_FINAL_HOLD_MS;
        latestState.timer = setTimeout(() => {
            latestState.timer = 0;
            _finishNekoIdleCat1StretchAction(button, token);
        }, delayMs);
    });
    return true;
}

function _findNekoIdleCat1PresentationButton() {
    const gate = _getNekoCatMindRuntimeGateSnapshot();
    if (!gate ||
        !gate.validCatRuntime ||
        gate.tier !== _NEKO_IDLE_TIER_CAT1 ||
        gate.returnPending ||
        gate.dragPending ||
        gate.dragging ||
        gate.edgePeekActive ||
        gate.transitionActive ||
        gate.activeIndependentAction ||
        !gate.returnBallVisible ||
        gate.chatSurfaceDragging ||
        gate.yarnDragActive ||
        gate.yarnSettling) return null;

    const button = _findNekoCatMindVisibleButtonForTier(_NEKO_IDLE_TIER_CAT1);
    const journey = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (journey && (journey.pairMovePlan || journey.pairMoveFrame)) return null;
    return button || null;
}

function _requestNekoIdleCat1HissStretchPresentation() {
    if (!_isNekoCatActionResourceAvailable('cat1_hiss_stretch')) return false;
    const button = _findNekoIdleCat1PresentationButton();
    if (!button || !_playNekoIdleCat1StretchAction(button)) return false;
    _playNekoIdleSound(
        _getNekoIdleCat1StretchActionState(button),
        _getNekoCatVoiceUrl('cat1.hiss'),
        _NEKO_IDLE_CAT1_CHAT_HISS_SOUND_VOLUME
    );
    return true;
}

function _requestNekoIdleCat1VisualActionPresentation(actionKey, options = {}) {
    const profile = _getNekoIdleCat1VisualActionProfile(actionKey);
    if (!profile || !_isNekoCatActionResourceAvailable(profile.capability)) return false;
    const userButton = options.userInitiated === true ? options.button : null;
    if (userButton && _isNekoCatMindAudioActionActive()) {
        const userTier = _normalizeNekoIdleReturnTier(
            userButton.getAttribute('data-neko-idle-tier')
        );
        if (userTier === _NEKO_IDLE_TIER_CAT1) {
            _stopNekoIdleCat1AmbientSoundAudio({ reason: 'user-visual-action' });
        } else if (userTier === _NEKO_IDLE_TIER_CAT2 || userTier === _NEKO_IDLE_TIER_CAT3) {
            _stopNekoIdleSleepSoundAudio({ reason: 'user-visual-action' });
        }
    }
    const preparedButton = actionKey === 'scared' && typeof options.prepareCat1 === 'function'
        ? options.button
        : null;
    const sourceTier = profile.tier || _NEKO_IDLE_TIER_CAT1;
    const gate = sourceTier === _NEKO_IDLE_TIER_CAT1 ? null : _getNekoCatMindRuntimeGateSnapshot();
    if (gate && (!gate.validCatRuntime || gate.tier !== sourceTier || gate.returnPending ||
        gate.dragPending || gate.dragging || gate.edgePeekActive || gate.transitionActive ||
        gate.activeIndependentAction || !gate.returnBallVisible || gate.chatSurfaceDragging ||
        gate.yarnDragActive || gate.yarnSettling)) return false;
    const button = preparedButton || (sourceTier === _NEKO_IDLE_TIER_CAT1
        ? _findNekoIdleCat1PresentationButton()
        : _findNekoCatMindVisibleButtonForTier(sourceTier));
    return !!(button && _playNekoIdleCat1VisualAction(button, actionKey, options));
}

function _requestNekoIdleCat1HappyPresentation(options = {}) {
    return _requestNekoIdleCat1VisualActionPresentation('happy', options);
}

function _requestNekoIdleCat1TouchPresentation(options = {}) {
    return _requestNekoIdleCat1VisualActionPresentation('touch', options);
}

function _requestNekoIdleCat1Cosplay1Presentation(options = {}) {
    return _requestNekoIdleCat1VisualActionPresentation('cosplay1', options);
}

if (typeof window !== 'undefined') {
    const presentationApi = {
        requestCat1HissStretch: _requestNekoIdleCat1HissStretchPresentation,
        requestCat1Happy: _requestNekoIdleCat1HappyPresentation,
        requestCat1Touch: _requestNekoIdleCat1TouchPresentation,
        requestCat1Cosplay1: _requestNekoIdleCat1Cosplay1Presentation
    };
    Object.values(_NEKO_IDLE_VISUAL_ACTION_PROFILES).forEach((profile) => {
        if (presentationApi[profile.requestName]) return;
        presentationApi[profile.requestName] = (options = {}) => (
            _requestNekoIdleCat1VisualActionPresentation(profile.key, options)
        );
    });
    window.NekoCatIdlePresentation = Object.freeze(presentationApi);
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
            presentationOnTerminal: null,
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

function _isNekoIdleDesktopWindowTopEdgeActionActive(button) {
    const desktopTopEdge = typeof window !== 'undefined'
        ? window.NekoDesktopWindowTopEdgePerch
        : null;
    return !!(desktopTopEdge
        && typeof desktopTopEdge.isActive === 'function'
        && desktopTopEdge.isActive(button));
}

function _isNekoIdleDesktopWindowEdgePeekActionActive(button) {
    const desktopEdgePeek = typeof window !== 'undefined'
        ? window.NekoDesktopWindowEdgePeek
        : null;
    return !!(desktopEdgePeek
        && typeof desktopEdgePeek.isActive === 'function'
        && desktopEdgePeek.isActive(button));
}

function _isNekoIdleDesktopWindowDoorWalkActionActive(button) {
    const desktopDoorWalk = typeof window !== 'undefined'
        ? window.NekoDesktopWindowDoorWalk
        : null;
    return !!(desktopDoorWalk
        && typeof desktopDoorWalk.isActive === 'function'
        && desktopDoorWalk.isActive(button));
}

function _isNekoIdleDesktopWindowInteractionActionActive(button) {
    const desktopInteractions = typeof window !== 'undefined'
        ? window.NekoDesktopWindowInteractions
        : null;
    const presentationActive = desktopInteractions
        && typeof desktopInteractions.isActive === 'function'
        ? desktopInteractions.isActive(button)
        : (_isNekoIdleDesktopWindowTopEdgeActionActive(button)
            || _isNekoIdleDesktopWindowEdgePeekActionActive(button));
    return presentationActive
        || _isNekoIdleDesktopWindowDoorWalkActionActive(button);
}

function _isNekoIdleCat1IndependentActionActive(button) {
    return _isNekoIdleCat1EatActionActive(button) ||
        _isNekoIdleCat1VisualActionActive(button) ||
        _isNekoIdleCat1StretchActionActive(button) ||
        _isNekoIdleCat1PlayActionActive(button) ||
        _isNekoIdleCat1PlaygroundEntryOrDropActive(button) ||
        _isNekoIdleDesktopWindowInteractionActionActive(button);
}

function _isAnyNekoIdleCat1IndependentActionActive() {
    return _isAnyNekoIdleCat1EatActionActive() ||
        _isAnyNekoIdleCat1VisualActionActive() ||
        _isAnyNekoIdleCat1StretchActionActive() ||
        _isAnyNekoIdleCat1PlayActionActive() ||
        _isAnyNekoIdleCat1PlaygroundDropLifecycleActive() ||
        _isNekoIdleDesktopWindowInteractionActionActive();
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

    // 原生 Wayland bridge 接收 58px 可见矩形，并在 Chat preload 中转换回
    // 88px 输入锚点；继续发送最终目标，避免只解除隐藏却留在上一次 pair-move 位置。
    const ballSize = _isNekoIdleCat1NativeWaylandSelfBallRuntime()
        ? _NEKO_IDLE_CAT1_NATIVE_YARN_VISIBLE_SIZE_PX
        : _NEKO_IDLE_CAT1_PLAY_YARN_RELEASE_SIZE_PX;
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
    const presentationOnTerminal = typeof state.presentationOnTerminal === 'function'
        ? state.presentationOnTerminal
        : null;
    state.presentationOnTerminal = null;
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
        const result = _getNekoCatMindCancelResult(
            options.reason || 'cat1-play-action-cancelled', 'cat1-play-action-finished'
        );
        _reportNekoCatMindStateActionResult(state, result, {
            reason: options.reason || 'cat1-play-action-cancelled'
        });
        if (presentationOnTerminal) {
            try {
                presentationOnTerminal(result, {
                    reason: options.reason || 'cat1-play-action-cancelled'
                });
            } catch (_) {}
        }
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
    if (!_isNekoCatActionResourceAvailable('cat1_play_yarn')) return false;
    if (!button) return false;
    if (_isNekoIdleDesktopWindowInteractionActionActive(button)) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;
    if (_isNekoIdleCat1PlayActionActive(button)) return false;
    if (_isNekoIdleCat1StretchActionActive(button) || _isNekoIdleCat1VisualActionActive(button)) return false;
    const container = _getNekoIdleReturnContainerFromButton(button);
    if (!container || container.style.display === 'none') return false;
    const art = button.querySelector('.neko-idle-return-art');
    if (!art) return false;

    _cancelNekoIdleCat1EatAction(button, { restoreArt: false });
    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false });
    const state = _getNekoIdleCat1PlayActionState(button);
    const journey = button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey;
    const shouldResumeJourney = _shouldResumeNekoIdleJourneyAfterActionTakeover(art, journey);
    if (shouldResumeJourney && !journey.paused) {
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
    state.presentationOnTerminal = !isCatMindRun && typeof catMindRunOptions.onTerminal === 'function'
        ? catMindRunOptions.onTerminal
        : null;
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

    _getNekoIdleAnimationDurationMs(_NEKO_IDLE_CAT1_PLAY_ASSET_URL).then((durationMs) => {
        const latestState = button.__nekoIdleCat1PlayActionState;
        if (!latestState || !latestState.active || latestState.token !== token) return;
        const elapsedMs = Math.max(0, Date.now() - startedAt);
        const delayMs = Math.max(0, (Number(durationMs) || 0) - elapsedMs);
        latestState.timer = setTimeout(markGifDone, delayMs);
    });

    _playNekoIdleSound(
        state,
        _getNekoCatVoiceUrl('cat1.play_yarn'),
        _NEKO_IDLE_CAT1_PLAY_SOUND_VOLUME
    );
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
    if (!_isNekoCatActionResourceAvailable(actionId)) return false;

    const run = _beginNekoCatMindStateAction(_nekoIdleSleepSoundState, actionId, tier, {
        source: catMindRunOptions.source || 'sleep-feedback-runner', requestId: catMindRunOptions.requestId
    });
    _notifyNekoCatMindRunnerAccepted(catMindRunOptions, run);

    const sleepSlot = tier === _NEKO_IDLE_TIER_CAT3 ? 'cat3.sleep' : 'cat2.sleep';
    const audio = _playNekoIdleSound(
        _nekoIdleSleepSoundState,
        _getNekoCatVoiceUrl(sleepSlot),
        config.volume
    );
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
    return _getNekoCatVoiceUrl('cat1.ambient');
}

function _playNekoIdleCat1AmbientSound(token) {
    const catMindRunOptions = arguments[1] || {};
    if (!_nekoIdleCat1AmbientSoundState.active ||
        token !== _nekoIdleCat1AmbientSoundState.token ||
        _isAnyNekoIdleCat1IndependentActionActive() ||
        _isAnyNekoIdleReturnDragActionActive()) {
        return false;
    }

    if (!_isNekoCatActionResourceAvailable(_NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING)) return false;

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

function _playNekoIdleCat1DragSound(tier, options = {}) {
    if (_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1) return;
    _stopNekoIdleCat1AmbientSound({ reason: options.reason || 'return-ball-drag-active' });
    _stopNekoIdleSoundAudio(_nekoIdleCat1RapidDragSoundState);
    _playNekoIdleSound(
        _nekoIdleCat1DragSoundState,
        _getNekoCatVoiceUrl('cat1.drag'),
        _NEKO_IDLE_CAT1_DRAG_SOUND_VOLUME
    );
}

function _playNekoIdleCat1RapidDragSound(tier, options = {}) {
    if (_normalizeNekoIdleReturnTier(tier) !== _NEKO_IDLE_TIER_CAT1) return;
    _stopNekoIdleCat1AmbientSound({ reason: options.reason || 'return-ball-drag-active' });
    _stopNekoIdleSoundAudio(_nekoIdleCat1DragSoundState);
    _playNekoIdleSound(
        _nekoIdleCat1RapidDragSoundState,
        _getNekoCatVoiceUrl('cat1.rapid_drag'),
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

function _syncNekoIdleCat1CompactMirrorReaction(button, container, assetUrl, reason, artLayout = '') {
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
        assetUrl: assetUrl,
        artLayout: artLayout
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

        _getNekoIdleAnimationDurationMs(reactionSrc).then((durationMs) => {
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
