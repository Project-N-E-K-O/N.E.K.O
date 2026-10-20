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
    if (!_isNekoCatActionResourceAvailable('cat1_eat_snack')) return false;
    if (!button) return false;
    if (_isNekoIdleDesktopWindowInteractionActionActive(button)) return false;
    if (_isNekoIdleCat1PlaygroundEntryOrDropActive(button)) return false;
    if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) !== _NEKO_IDLE_TIER_CAT1) return false;
    if (_isNekoIdleReturnDragActionActive(button)) return false;
    if (_isNekoIdleCat1EatActionActive(button)) return false;
    if (_isNekoIdleCat1StretchActionActive(button)) return false;
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
        journey.substate === journey.profile.walkingSubstate);
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
            // Stretch has more than one owner: the local chat presentation and
            // the journey tail are user/legacy presentation paths, while a
            // Cat Mind run is the only owner pet-care cleanup may interrupt.
            actionSource: '',
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
    state.actionSource = '';
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
    const shouldResumeJourney = !!(journey &&
        !journey.paused &&
        journey.profile &&
        journey.substate === journey.profile.walkingSubstate);
    if (shouldResumeJourney) {
        _pauseNekoIdleCat1Journey(button);
    }

    _clearNekoIdleHoverPlayback(art);
    _cleanupNekoIdleArtTransition(art);
    _clearNekoIdleGifPlaybackSource(art);

    state.active = true;
    state.token += 1;
    state.resumeJourney = shouldResumeJourney;
    state.actionSource = typeof options.source === 'string' && options.source
        ? options.source
        : 'local';
    state.presentationOnTerminal = typeof options.onTerminal === 'function'
        ? options.onTerminal
        : null;
    const token = state.token;
    const startedAt = Date.now();
    const stretchSrc = _getNekoIdleCat1StretchAssetUrl();

    _setNekoIdleCat1StretchActionClass(button, true);
    _setNekoIdleReturnArtSource(art, stretchSrc, _NEKO_IDLE_TIER_CAT1, { animate: true });

    _getNekoIdleGifDurationMs(stretchSrc).then((durationMs) => {
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

function _findNekoIdleCat1StretchPresentationButton() {
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
    if (window.nekoPetCareMode?.shouldBlockEntityOutput?.({ source: 'cat-local-chat' }).blocked) return false;
    if (!_isNekoCatActionResourceAvailable('cat1_hiss_stretch')) return false;
    const button = _findNekoIdleCat1StretchPresentationButton();
    if (!button || !_playNekoIdleCat1StretchAction(button, { source: 'cat-local-chat' })) return false;
    _playNekoIdleSound(
        _getNekoIdleCat1StretchActionState(button),
        _getNekoCatVoiceUrl('cat1.hiss'),
        _NEKO_IDLE_CAT1_CHAT_HISS_SOUND_VOLUME
    );
    return true;
}
function _requestNekoPetCareCat1StretchPresentation() {
    if (window.nekoPetCareMode?.shouldBlockEntityOutput?.({ source: 'pet-care-action' }).blocked) return false;
    // Brushing reuses the stretch appearance only.  The chat-specific hiss
    // voice is not a prerequisite for a PetCare feedback action.
    const button = _findNekoIdleCat1StretchPresentationButton();
    if (!button || !_playNekoIdleCat1StretchAction(button, { source: 'pet-care' })) return false;
    return true;
}

// Narrow presentation bridge for PetCare user actions.  PetCare asks for a
// CAT1 response only; it does not enter Cat Mind's autonomous selector or
// settle any business value.  The existing action runners still own their
// normal terminal and cleanup lifecycle.
function _requestNekoPetCareCat1EatPresentation() {
    if (window.nekoPetCareMode?.shouldBlockEntityOutput?.({ source: 'pet-care-action' }).blocked) return false;
    const button = _findNekoIdleCat1StretchPresentationButton();
    return !!(button && _playNekoIdleCat1EatAction(button, { source: 'pet-care' }));
}
function _requestNekoPetCareCat1PlayPresentation(options = {}) {
    if (window.nekoPetCareMode?.shouldBlockEntityOutput?.({ source: 'pet-care-action' }).blocked) return false;
    const button = _findNekoIdleCat1StretchPresentationButton();
    if (!button) return false;
    const onTerminal = typeof options.onTerminal === 'function' ? options.onTerminal : null;
    return !!_playNekoIdleCat1PlayAction(button, {
        source: typeof options.source === 'string' && options.source
            ? options.source : 'pet-care-toy',
        onTerminal,
    });
}
function _cancelNekoPetCarePresentation() {
    let cancelled = false;
    _forEachNekoIdleReturnButton((button) => {
        const eatState = button && button.__nekoIdleCat1EatActionState;
        if (eatState && eatState.active && eatState.catMindSource === 'pet-care') {
            _cancelNekoIdleCat1EatAction(button, { reason: 'pet-care-action-failed' });
            cancelled = true;
        }
        const stretchState = button && button.__nekoIdleCat1StretchActionState;
        if (stretchState && stretchState.active && stretchState.actionSource === 'pet-care') {
            _cancelNekoIdleCat1StretchAction(button, { reason: 'pet-care-action-failed' });
            cancelled = true;
        }
        const playState = button && button.__nekoIdleCat1PlayActionState;
        if (playState && playState.active && playState.presentationSource === 'pet-care-toy') {
            _cancelNekoIdleCat1PlayAction(button, { reason: 'pet-care-action-failed' });
            cancelled = true;
        }
    });
    return cancelled;
}

if (typeof window !== 'undefined') {
    const addNekoPetCareListener = typeof window.addEventListener === 'function'
        ? window.addEventListener.bind(window)
        : () => {};
    let activeExplorationAssetDetail = null;
    let explorationPresentationSuppressedByModel = false;
    function _clearNekoPetCareExplorationOverlays(button) {
        if (!button) return;
        const overlay = button.querySelector && button.querySelector('.neko-pet-care-exploration-note-overlay');
        if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
        const state = button.__nekoPetCareExplorationOverlay;
        if (state && state.timer) window.clearInterval(state.timer);
        delete button.__nekoPetCareExplorationOverlay;
    }
    function _explorationRemainingLabel(endsAt) {
        const numeric = Number(endsAt);
        const end = Number.isFinite(numeric) ? (numeric < 100000000000 ? numeric * 1000 : numeric) : Date.parse(String(endsAt || ''));
        if (!Number.isFinite(end)) return '';
        const seconds = Math.max(0, Math.ceil((end - Date.now()) / 1000));
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const remainder = seconds % 60;
        return hours > 0
            ? `${hours}小时${String(minutes).padStart(2, '0')}分`
            : `${minutes}分${String(remainder).padStart(2, '0')}秒`;
    }
    function _installNekoPetCareExplorationNote(button, detail) {
        if (!button) return;
        _clearNekoPetCareExplorationOverlays(button);
        const note = typeof detail.note === 'string' ? detail.note.trim() : '';
        const endsAt = detail.ends_at || detail.endsAt || '';
        if (!note && !endsAt) return;
        const overlay = document.createElement('div');
        overlay.className = 'neko-pet-care-exploration-note-overlay';
        overlay.setAttribute('aria-label', '探索状态');
        const noteText = document.createElement('span');
        noteText.className = 'neko-pet-care-exploration-note-text';
        noteText.textContent = note || '探索中';
        overlay.appendChild(noteText);
        const countdown = document.createElement('span');
        countdown.className = 'neko-pet-care-exploration-countdown';
        overlay.appendChild(countdown);
        button.appendChild(overlay);
        const update = () => {
            const label = _explorationRemainingLabel(endsAt);
            countdown.textContent = label ? `剩余 ${label}` : '探索中';
        };
        update();
        const timer = window.setInterval(update, 1000);
        // Do not let a presentation-only countdown keep a test/process alive
        // after the caller has otherwise finished. Browsers ignore unref;
        // Node-based harnesses can release the timer immediately.
        if (timer && typeof timer.unref === 'function') timer.unref();
        button.__nekoPetCareExplorationOverlay = { timer, activityId: String(detail.activityId || detail.explorationId || detail.exploration_id || '') };
    }
    addNekoPetCareListener('neko:cat-return-commit', function (event) {
        // The model return has committed the single visible character form.
        // Keep the away activity in service state, but never re-show its nest
        // while the model is visible or the recall request is resolving.
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        if (activeExplorationAssetDetail || detail.explorationRecall === true) {
            explorationPresentationSuppressedByModel = true;
        }
    });
    addNekoPetCareListener('neko:cat-local-active-change', function (event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        if (detail.active === true) explorationPresentationSuppressedByModel = false;
    });
    // Exploration owns a short presentation-only departure phase. This is
    // deliberately separate from Cat Mind and from the service activity:
    // the pet-care adapter decides when the bounded phase is over, while
    // this module only fades the current cat art before the nest is shown.
    addNekoPetCareListener('neko:pet-care:exploration-departure-request', function (event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        const activityId = detail.activityId || detail.explorationId || detail.exploration_id || '';
        _forEachNekoIdleReturnButton((button) => {
            if (!button) return;
            const art = button.querySelector && button.querySelector('.neko-idle-return-art');
            if (!art) return;
            if (!button.__nekoPetCareExplorationDeparture) {
                button.__nekoPetCareExplorationDeparture = {
                    activityId: String(activityId || ''),
                    opacity: art.style.opacity,
                    transition: art.style.transition,
                };
            }
            // A CAT1/CAT2/CAT3 transition owns a temporary second <img>.
            // The nest must take the whole shared presentation slot, so drop
            // that transition before the departure fade starts.
            _cleanupNekoIdleArtTransition(art);
            _clearNekoIdleHoverPlayback(art);
            _clearNekoIdleGifPlaybackSource(art);
            art.style.transition = 'opacity 220ms ease';
            art.style.opacity = '0';
            button.dataset.nekoExplorationDeparture = 'true';
        });
    });
    addNekoPetCareListener('neko:pet-care:exploration-return-request', function () {
        _forEachNekoIdleReturnButton((button) => {
            const art = button && button.querySelector && button.querySelector('.neko-idle-return-art');
            if (!art) return;
            art.style.transition = 'opacity 180ms ease';
            art.style.opacity = '0';
            button.dataset.nekoExplorationReturn = 'true';
        });
    });
    // Exploration owns only the temporary presentation asset.  Reuse the
    // existing cat return container and hit region so the nest remains the
    // single unified recall entry; no cat form or Cat Mind action is added.
    addNekoPetCareListener('neko:pet-care:exploration-asset', function (event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        const active = detail.active === true;
        const assetUrl = typeof detail.asset_url === 'string' && detail.asset_url
            ? detail.asset_url
            : (typeof detail.assetUrl === 'string' && detail.assetUrl
                ? detail.assetUrl : '/static/assets/neko-idle/cat-bad.png');
        if (active && explorationPresentationSuppressedByModel) return;
        if (active) {
            const petCare = window.nekoPetCareEntry;
            const petState = petCare && typeof petCare.getState === 'function' ? petCare.getState() : null;
            // The model and the cat form are one character presentation.  An
            // active server exploration may be restored while the model is
            // already visible (for example after refresh); in that state the
            // desktop nest must stay hidden until the cat form is entered.
            if (petState && (!petState.catLocalActive ||
                petState.explorationRecallRequested || petState.explorationPending)) return;
            const detailId = String(detail.activityId || detail.explorationId || detail.exploration_id || '').trim();
            const currentExploration = petState && petState.exploration && typeof petState.exploration === 'object'
                ? petState.exploration : null;
            const currentId = String(currentExploration && (
                currentExploration.activityId || currentExploration.activity_id ||
                currentExploration.explorationId || currentExploration.exploration_id ||
                currentExploration.id || '')).trim();
            const currentStatus = String(currentExploration && currentExploration.status || '').toLowerCase();
            const terminal = ['completed', 'inactive', 'cancelled', 'recalled', 'returned'].includes(currentStatus);
            if ((detailId && currentId && detailId !== currentId) || terminal ||
                (currentExploration && currentExploration.active === false)) return;
            activeExplorationAssetDetail = Object.assign({}, detail);
        } else {
            activeExplorationAssetDetail = null;
        }
        _forEachNekoIdleReturnButton((button) => {
            if (!button) return;
            const art = button.querySelector && button.querySelector('.neko-idle-return-art');
            const container = _getNekoIdleReturnContainerFromButton(button);
            if (!art) return;
            if (active) {
                // Only the currently visible semantic hit region may host the
                // away asset.  Other model containers remain hidden and must
                // never be made visible just because the service activity was
                // restored.
                if (!container || container.getAttribute('data-neko-return-visible') !== 'true') return;
                if (!button.__nekoPetCareExplorationAsset) {
                    button.__nekoPetCareExplorationAsset = {
                        src: art.getAttribute('src') || '',
                        srcset: art.getAttribute('srcset') || '',
                        tier: button.getAttribute('data-neko-idle-tier') || ''
                    };
                }
                _cleanupNekoIdleArtTransition(art);
                _clearNekoIdleHoverPlayback(art);
                _clearNekoIdleGifPlaybackSource(art);
                art.src = assetUrl;
                art.removeAttribute('srcset');
                art.dataset.nekoExplorationAsset = 'true';
                button.dataset.nekoExplorationAsset = 'true';
                if (container) container.dataset.nekoExplorationAsset = 'true';
                // The departure adapter owns the fade; the service-confirmed
                // asset event is the point at which the nest becomes visible.
                art.style.opacity = '1';
                art.style.transition = 'opacity 180ms ease';
                _installNekoPetCareExplorationNote(button, detail);
            } else if (button.__nekoPetCareExplorationAsset) {
                const previous = button.__nekoPetCareExplorationAsset;
                _cleanupNekoIdleArtTransition(art);
                _clearNekoIdleHoverPlayback(art);
                _clearNekoIdleGifPlaybackSource(art);
                if (previous.src) art.src = previous.src;
                else art.removeAttribute('src');
                if (previous.srcset) art.setAttribute('srcset', previous.srcset);
                else art.removeAttribute('srcset');
                if (previous.tier) button.setAttribute('data-neko-idle-tier', previous.tier);
                delete art.dataset.nekoExplorationAsset;
                delete button.dataset.nekoExplorationAsset;
                if (container) delete container.dataset.nekoExplorationAsset;
                delete button.__nekoPetCareExplorationAsset;
                _clearNekoPetCareExplorationOverlays(button);
            }
            if (!active && button.__nekoPetCareExplorationDeparture) {
                const departure = button.__nekoPetCareExplorationDeparture;
                art.style.opacity = departure.opacity || '';
                art.style.transition = departure.transition || '';
                delete button.__nekoPetCareExplorationDeparture;
                delete button.dataset.nekoExplorationDeparture;
            }
            if (!active && button.dataset.nekoExplorationReturn) {
                art.style.opacity = '';
                art.style.transition = '';
                delete button.dataset.nekoExplorationReturn;
            }
        });
    });
    // The service fact may arrive before the renderer creates its return
    // container. Re-apply it when that existing container later announces its
    // visibility; no pet-care state is stored in the renderer.
    addNekoPetCareListener('neko:idle-return-ball-state', function () {
        if (!activeExplorationAssetDetail || explorationPresentationSuppressedByModel || typeof window.dispatchEvent !== 'function') return;
        // A renderer can announce a newly-created return container after a
        // model switch or recall has already released the old activity. Do
        // not replay the stale nest over the new model. The presentation
        // layer only reads the pet-care semantic snapshot; it never owns it.
        const petCare = window.nekoPetCareEntry;
        const petState = petCare && typeof petCare.getState === 'function'
            ? petCare.getState() : null;
        const exploration = petState && petState.exploration;
        const status = String(exploration && exploration.status || '').toLowerCase();
        const terminal = ['completed', 'inactive', 'cancelled', 'recalled', 'returned'].includes(status);
        if (!exploration || exploration.active === false || terminal ||
            petState.explorationRecallRequested || petState.explorationPending) return;
        const detailId = String(activeExplorationAssetDetail.activityId ||
            activeExplorationAssetDetail.explorationId ||
            activeExplorationAssetDetail.exploration_id || '');
        const currentId = String(exploration.activityId || exploration.activity_id ||
            exploration.id || '');
        if (detailId && currentId && detailId !== currentId) return;
        try {
            window.dispatchEvent(new CustomEvent('neko:pet-care:exploration-asset', {
                detail: Object.assign({}, activeExplorationAssetDetail),
            }));
        } catch (_) {}
    });
    // Context/account changes can release the shared gate before the
    // pet-care snapshot adapter gets a chance to publish its normal asset
    // restore event. Reuse the same restore path for that fail-closed case.
    addNekoPetCareListener('neko:pet-care:exploration-gate', function (event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        const activityId = String(detail.activityId || detail.explorationId || detail.exploration_id || '').trim();
        if (!activityId) return;
        const petCareMode = window.nekoPetCareMode;
        if (petCareMode && typeof petCareMode.shouldApplyExplorationGate === 'function'
            && !petCareMode.shouldApplyExplorationGate(detail)) return;
        if (detail.active === false) {
            window.dispatchEvent(new CustomEvent('neko:pet-care:exploration-asset', {
                detail: Object.assign({}, detail, { active: false, asset_url: '', assetUrl: '' })
            }));
        }
    });
    window.NekoCatIdlePresentation = Object.freeze({
        requestCat1HissStretch: _requestNekoIdleCat1HissStretchPresentation,
        requestCat1Stretch: _requestNekoPetCareCat1StretchPresentation,
        requestCat1Eat: _requestNekoPetCareCat1EatPresentation,
        requestCat1Play: _requestNekoPetCareCat1PlayPresentation,
        cancelPetCare: _cancelNekoPetCarePresentation,
    });
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
            presentationSource: '',
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
        _isNekoIdleCat1StretchActionActive(button) ||
        _isNekoIdleCat1PlayActionActive(button) ||
        _isNekoIdleCat1PlaygroundEntryOrDropActive(button) ||
        _isNekoIdleDesktopWindowInteractionActionActive(button);
}

function _isAnyNekoIdleCat1IndependentActionActive() {
    return _isAnyNekoIdleCat1EatActionActive() ||
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
    state.presentationSource = '';
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
    if (_isNekoIdleCat1StretchActionActive(button)) return false;
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
        journey.substate === journey.profile.walkingSubstate);
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
    state.presentationSource = isCatMindRun ? '' :
        (typeof catMindRunOptions.source === 'string' && catMindRunOptions.source
            ? catMindRunOptions.source : 'local');
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

    _getNekoIdleGifDurationMs(_NEKO_IDLE_CAT1_PLAY_ASSET_URL).then((durationMs) => {
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

// Stage 0 pet-care compatibility cleanup. The event is intentionally
// separate from Cat Mind: callers can stop autonomous presentation runners
// without changing the runner APIs used by future user actions.
if (typeof window !== 'undefined') {
    window.addEventListener('neko:pet-care:cleanup-request', function (event) {
        const detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
        // Exploration is a whole-entity activity gate. Unlike the Stage 3
        // compatibility cleanup it must stop every presentation owned by this
        // module, including a user-triggered action that is still running.
        if (detail.source === 'exploration') {
            const reason = detail.reason || 'exploration-away';
            _forEachNekoIdleReturnButton((button) => {
                const eatState = button && button.__nekoIdleCat1EatActionState;
                if (eatState && eatState.active) {
                    _cancelNekoIdleCat1EatAction(button, { restoreArt: false, reason });
                }
                const playState = button && button.__nekoIdleCat1PlayActionState;
                if (playState && playState.active) {
                    _cancelNekoIdleCat1PlayAction(button, { restoreArt: false, reason });
                }
                const stretchState = button && button.__nekoIdleCat1StretchActionState;
                if (stretchState && stretchState.active) {
                    _cancelNekoIdleCat1StretchAction(button, { restoreArt: false, reason });
                }
            });
            _stopNekoIdleSleepSound({ reason });
            _stopNekoIdleCat1AmbientSound({ reason });
            return;
        }
        const actionIds = Array.isArray(detail.actionIds) ? detail.actionIds : null;
        const shouldClean = (actionId) => !actionIds || actionIds.indexOf(actionId) >= 0;
        const reason = detail.reason || 'pet-care-mode-change';
        _forEachNekoIdleReturnButton((button) => {
            const eatState = button && button.__nekoIdleCat1EatActionState;
            if (shouldClean('cat1_eat_snack') && eatState && eatState.catMindSource === 'cat_mind') {
                _cancelNekoIdleCat1EatAction(button, { restoreArt: false, reason });
            }
            const playState = button && button.__nekoIdleCat1PlayActionState;
            if (shouldClean('cat1_play_yarn') && playState && playState.catMindSource === 'cat_mind') {
                _cancelNekoIdleCat1PlayAction(button, { restoreArt: false, reason });
            }
            const stretchState = button && button.__nekoIdleCat1StretchActionState;
            // Stretch is the terminal presentation of cat1_small_move. It
            // is not a separate Cat Mind request id, so inherit the parent
            // cleanup allowance while still requiring the Cat Mind owner.
            if ((shouldClean('cat1_stretch') || shouldClean('cat1_small_move')) && stretchState &&
                stretchState.active && stretchState.actionSource === 'cat_mind') {
                _cancelNekoIdleCat1StretchAction(button, { restoreArt: false, reason });
            }
        });
        const sleepActionId = _nekoIdleSleepSoundState.catMindActionId || '';
        const shouldCleanSleep = (sleepActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK &&
            shouldClean('cat2_nap_feedback')) ||
            (sleepActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK &&
                shouldClean('cat3_sleep_feedback'));
        if (shouldCleanSleep) {
            _stopNekoIdleSleepSound({ reason });
        }
        if (shouldClean('cat1_social_ping')) _stopNekoIdleCat1AmbientSound({ reason });
    });
}
