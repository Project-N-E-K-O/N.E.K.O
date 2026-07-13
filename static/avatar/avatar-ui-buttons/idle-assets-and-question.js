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

// Cat Mind owns scheduling; this file remains the capability/read-only provider
// and the sole adapter from an accepted request to the existing runners.
function _getNekoCatMindActionRequestEventName() {
    try {
        const name = window.NekoCatMindContract && window.NekoCatMindContract.EVENT_NAMES && window.NekoCatMindContract.EVENT_NAMES.ACTION_REQUEST;
        if (typeof name === 'string' && name) return name;
    } catch (_) {}
    return _NEKO_CAT_MIND_ACTION_REQUEST_EVENT;
}

function _getNekoCatMindActionResultEventName() {
    try {
        const name = window.NekoCatMindContract && window.NekoCatMindContract.EVENT_NAMES && window.NekoCatMindContract.EVENT_NAMES.ACTION_RESULT;
        if (typeof name === 'string' && name) return name;
    } catch (_) {}
    return _NEKO_CAT_MIND_ACTION_RESULT_EVENT;
}

function _nextNekoCatMindActionRunId(actionId) {
    _nekoCatMindActionRunSequence += 1;
    return `${actionId || 'action'}:${_nekoCatMindActionRunSequence}`;
}

function _beginNekoCatMindStateAction(state, actionId, tier, detail = {}) {
    if (!state || !actionId) return null;
    state.catMindActionId = actionId;
    state.catMindRunId = _nextNekoCatMindActionRunId(actionId);
    state.catMindStartedAt = Number.isFinite(Number(detail.timestamp)) ? Number(detail.timestamp) : Date.now();
    state.catMindSource = detail.source || 'avatar-ui-buttons';
    state.catMindRequestId = detail.requestId || '';
    state.catMindTier = _normalizeNekoIdleReturnTier(tier);
    state.catMindResultReported = false;
    return { actionId: state.catMindActionId, runId: state.catMindRunId, requestId: state.catMindRequestId,
        startedAt: state.catMindStartedAt, source: state.catMindSource, tier: state.catMindTier };
}

function _clearNekoCatMindStateAction(state) {
    if (!state) return;
    state.catMindActionId = '';
    state.catMindRunId = '';
    state.catMindStartedAt = 0;
    state.catMindSource = '';
    state.catMindRequestId = '';
    state.catMindTier = _NEKO_IDLE_TIER_NONE;
    state.catMindResultReported = false;
}

function _dispatchNekoCatMindActionResult(actionId, result, detail = {}) {
    if (!actionId || !result || typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return false;
    const endedAt = Number.isFinite(Number(detail.timestamp)) ? Number(detail.timestamp) : Date.now();
    const startedAt = Number(detail.startedAt);
    const payload = {
        actionId, result, source: detail.source || 'avatar-ui-buttons',
        tier: _normalizeNekoIdleReturnTier(detail.tier), timestamp: endedAt,
        reason: detail.reason || result,
        detail: _sanitizeNekoCatIdleObservationDetail(Object.assign({}, detail.detail || {}, {
            runId: detail.runId || '', requestId: detail.requestId || '',
            durationMs: Number.isFinite(startedAt) && startedAt > 0 ? Math.max(0, endedAt - startedAt) : 0
        }))
    };
    try {
        window.dispatchEvent(new CustomEvent(_getNekoCatMindActionResultEventName(), { detail: payload }));
        return true;
    } catch (_) { return false; }
}

function _reportNekoCatMindStateActionResult(state, result, detail = {}) {
    if (!state || !state.catMindActionId || state.catMindResultReported) return false;
    state.catMindResultReported = true;
    const reported = _dispatchNekoCatMindActionResult(state.catMindActionId, result, Object.assign({}, detail, {
        source: detail.source || state.catMindSource, tier: detail.tier || state.catMindTier,
        runId: detail.runId || state.catMindRunId, requestId: detail.requestId || state.catMindRequestId,
        startedAt: detail.startedAt || state.catMindStartedAt
    }));
    _clearNekoCatMindStateAction(state);
    return reported;
}

function _isNekoCatMindStateActionRunCurrent(state, run, audio = null) {
    return !!(state && run && run.actionId && run.runId && state.catMindActionId === run.actionId &&
        state.catMindRunId === run.runId && (!audio || !state.audio || state.audio === audio));
}

function _reportNekoCatMindStateActionRunResult(state, run, audio, result, detail = {}) {
    if (!_isNekoCatMindStateActionRunCurrent(state, run, audio)) return false;
    return _reportNekoCatMindStateActionResult(state, result, Object.assign({
        source: run.source, tier: run.tier, runId: run.runId, requestId: run.requestId, startedAt: run.startedAt
    }, detail));
}

function _notifyNekoCatMindRunnerAccepted(options, run) {
    try { if (options && typeof options.onAccepted === 'function' && run && run.runId) options.onAccepted(run); } catch (_) {}
}
function _notifyNekoCatMindRunnerStarted(options, run) {
    try { if (options && typeof options.onStarted === 'function' && run && run.runId) options.onStarted(run); } catch (_) {}
}
function _getNekoCatMindCancelResult(reason, finishedReason) {
    if (finishedReason && reason === finishedReason) return _NEKO_CAT_MIND_ACTION_RESULTS.DONE;
    return /drag|return|tier|container|cat-to-model/.test(String(reason || ''))
        ? _NEKO_CAT_MIND_ACTION_RESULTS.INTERRUPTED : _NEKO_CAT_MIND_ACTION_RESULTS.CANCELLED;
}

function _makeNekoCatMindProviderDecision(allowed, reason, detail = {}) {
    return { allowed: allowed === true, reason: reason || (allowed ? 'allowed' : 'rejected'), detail };
}
function _attachNekoCatMindProviderDiagnostics(actionId, decision, context = {}) {
    const result = decision && typeof decision === 'object' ? decision : _makeNekoCatMindProviderDecision(false, 'provider_rejected');
    const tier = actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK ? _NEKO_IDLE_TIER_CAT2 :
        (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK ? _NEKO_IDLE_TIER_CAT3 : _NEKO_IDLE_TIER_CAT1);
    const button = context.button || _findNekoCatMindVisibleButtonForTier(tier);
    const container = _getNekoIdleReturnContainerFromButton(button);
    const journey = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    const profile = journey && journey.profile ? journey.profile : _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    let chatTarget = null;
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) {
        try { chatTarget = _getNekoIdleCat1PairMoveChatTarget(); } catch (_) { chatTarget = null; }
    }
    const catRect = container && container.getBoundingClientRect ? container.getBoundingClientRect() : null;
    const chatRect = chatTarget && chatTarget.rect;
    const pairMove = profile.pairMove || {};
    const minUsableDistancePx = Math.max(1, Number(pairMove.minUsableDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MIN_USABLE_DISTANCE_PX);
    const maxDistancePx = Math.max(1, Number(pairMove.maxDistancePx) || _NEKO_IDLE_CAT1_PAIR_MOVE_MAX_DISTANCE_PX);
    const geometryKnown = !!(catRect && catRect.width > 0 && catRect.height > 0 && chatRect && chatRect.width > 0 && chatRect.height > 0);
    const art = button && button.querySelector ? button.querySelector('.neko-idle-return-art') : null;
    let playYarnCapability = false;
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) {
        try { playYarnCapability = _canNekoCatMindControlPlayYarn(); } catch (_) { playYarnCapability = false; }
    }
    const facts = {
        tier: _getActiveNekoIdleReturnTier(), buttonFound: !!button, returnBallVisible: _isNekoCatMindButtonContainerVisible(button),
        hasArt: !!art,
        returnBallDragBlocking: _isNekoIdleReturnDragActionBlocking(button) || _isAnyNekoIdleReturnDragActionBlocking(),
        returnPending: _isNekoCatMindReturnPending(button) || _isAnyNekoCatMindReturnPending(),
        transitionActive: _isNekoCatMindTransitionActive(button), compactSurfaceDragging: _isNekoIdleCompactSurfaceDragging(),
        independentActionActive: _isAnyNekoIdleCat1IndependentActionActive() || _isNekoCatMindAudioActionActive(),
        audioEnabled: isNekoIdleCatAudioEnabled(), ambientAudioActive: !!_nekoIdleCat1AmbientSoundState.active,
        playYarnCapability,
        sleepTierMatches: actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK
            ? _nekoIdleSleepSoundState.tier === tier : null,
        nearChat: actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN
            ? _isNekoCatMindCat1NearChat(button) : null,
        smallMove: actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE ? {
            chatTargetAvailable: !!chatTarget, chatTargetMode: chatTarget && chatTarget.mode || '', geometryKnown,
            vectorSpaceAvailable: geometryKnown && _hasNekoIdleCat1MoveVectorSpace(catRect, chatRect, maxDistancePx, minUsableDistancePx),
            minUsableDistancePx, maxDistancePx
        } : null,
        journey: journey ? { exists: true, paused: !!journey.paused, substate: journey.substate || '',
            idleSubstate: profile.idleSubstate || '', actionSettled: !!journey.actionSettled,
            targetKind: journey.targetKind || '', pairMoveActive: !!(journey.pairMovePlan || journey.pairMoveFrame),
            pendingWalk: !!(journey.pendingWalkTimer || journey.pendingWalkReady || journey.frame || journey.settleTimer) } : { exists: false }
    };
    const checks = [
        { id: 'known_action', passed: Object.values(_NEKO_CAT_MIND_ACTION_IDS).includes(actionId) },
        { id: 'return_ball_drag_free', passed: !facts.returnBallDragBlocking }, { id: 'return_not_pending', passed: !facts.returnPending },
        { id: 'transition_idle', passed: !facts.transitionActive }, { id: 'no_independent_action', passed: !facts.independentActionActive },
        { id: 'compact_surface_idle', passed: !facts.compactSurfaceDragging }, { id: 'return_ball_visible', passed: facts.returnBallVisible }
    ];
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) {
        checks.push({ id: 'return_art_ready', passed: facts.hasArt });
    }
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING) {
        checks.push({ id: 'audio_enabled', passed: facts.audioEnabled }, { id: 'ambient_audio_active', passed: facts.ambientAudioActive });
    }
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) {
        checks.push({ id: 'near_chat', passed: facts.nearChat === true }, { id: 'play_yarn_capability', passed: facts.playYarnCapability });
    }
    if (facts.smallMove) {
        const moveJourney = facts.journey || {};
        checks.push({ id: 'journey_settled_idle', passed: moveJourney.exists && !moveJourney.paused && !moveJourney.pairMoveActive && !moveJourney.pendingWalk && moveJourney.substate === moveJourney.idleSubstate && moveJourney.actionSettled },
            { id: 'not_compact_top_edge', passed: moveJourney.targetKind !== _NEKO_IDLE_CAT1_TARGET_KIND_COMPACT_TOP_EDGE },
            { id: 'near_chat', passed: facts.nearChat === true }, { id: 'chat_target_available', passed: facts.smallMove.chatTargetAvailable },
            { id: 'small_move_geometry_known', passed: facts.smallMove.geometryKnown }, { id: 'move_vector_space', passed: facts.smallMove.vectorSpaceAvailable });
    }
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK) {
        checks.push({ id: 'audio_enabled', passed: facts.audioEnabled }, { id: 'sleep_tier_matches', passed: facts.tier === tier }, { id: 'sleep_audio_active', passed: facts.sleepTierMatches === true });
    }
    return _makeNekoCatMindProviderDecision(result.allowed === true, result.reason, Object.assign({}, result.detail || {}, {
        actionId, facts, checks, failedCheck: result.allowed ? '' : result.reason || 'provider_rejected'
    }));
}
function _isNekoCatMindButtonContainerVisible(button) {
    const container = _getNekoIdleReturnContainerFromButton(button);
    return !!(container && container.style.display !== 'none');
}
function _findNekoCatMindVisibleButtonForTier(tier) {
    let found = null;
    _forEachNekoIdleReturnButton((button) => {
        if (!found && _normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) === _normalizeNekoIdleReturnTier(tier) &&
            _isNekoCatMindButtonContainerVisible(button)) found = button;
    });
    return found;
}
function _isNekoCatMindReturnPending(button) {
    const container = button && _getNekoIdleReturnContainerFromButton(button);
    return !!(container && (container.getAttribute('data-neko-return-click-suppressed') === 'true' ||
        container.getAttribute('data-neko-model-cat-transitioning') === 'cat-to-model'));
}
function _isAnyNekoCatMindReturnPending() {
    let pending = false;
    _forEachNekoIdleReturnButton((button) => { pending = pending || _isNekoCatMindReturnPending(button); });
    return pending;
}
function _isNekoIdleReturnDragActionBlocking(button) {
    const container = button && _getNekoIdleReturnContainerFromButton(button);
    const dragging = container && container.getAttribute('data-dragging');
    return dragging === 'pending' || dragging === 'true' || _isNekoIdleReturnDragActionActive(button);
}
function _isAnyNekoIdleReturnDragActionBlocking() {
    let blocking = false;
    _forEachNekoIdleReturnButton((button) => { blocking = blocking || _isNekoIdleReturnDragActionBlocking(button); });
    return blocking;
}
function _isNekoCatMindTransitionActive(button) {
    const container = button && _getNekoIdleReturnContainerFromButton(button);
    const shell = typeof document !== 'undefined' && typeof document.getElementById === 'function'
        ? document.getElementById('react-chat-window-shell') : null;
    return !!((container && container.getAttribute('data-neko-model-cat-transitioning') &&
        container.getAttribute('data-neko-model-cat-transitioning') !== 'false') ||
        (typeof window.isNekoModelCatTransitionActive === 'function' && window.isNekoModelCatTransitionActive()) ||
        (shell && (shell.classList.contains('is-collapsing') || shell.classList.contains('is-expanding'))));
}
function _isNekoCatMindChatTransitionActive() {
    return _isNekoCatMindTransitionActive(null);
}
function _isNekoCatMindCat1NearChat(button) {
    const journey = button && (button.__nekoIdleReturnSubactionState || button.__nekoIdleCat1Journey);
    if (!journey) return false;
    const profile = journey.profile || _NEKO_IDLE_RETURN_SUBACTION_CAT1_CHAT_FOLLOW;
    return _isNekoIdleCat1SettledOnMinimizedSide(journey, profile);
}
function _canNekoCatMindControlPlayYarn() {
    return !!(_getNekoIdleReactChatMinimizedShell() || (_getNekoIdleDesktopChatMinimizedRect() && typeof window.dispatchEvent === 'function'));
}
function _evaluateNekoCatMindActionProvider(actionId, context = {}) {
    if (actionId && typeof actionId === 'object') { context = actionId; actionId = context.actionId; }
    const tier = actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK ? _NEKO_IDLE_TIER_CAT2 :
        (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK ? _NEKO_IDLE_TIER_CAT3 : _NEKO_IDLE_TIER_CAT1);
    const button = context.button || _findNekoCatMindVisibleButtonForTier(tier);
    const facts = { tier: _getActiveNekoIdleReturnTier(), buttonFound: !!button,
        returnBallVisible: _isNekoCatMindButtonContainerVisible(button), returnPending: _isAnyNekoCatMindReturnPending(),
        transitionActive: _isNekoCatMindTransitionActive(button), compactSurfaceDragging: _isNekoIdleCompactSurfaceDragging(),
        independentActionActive: _isAnyNekoIdleCat1IndependentActionActive() || _isNekoCatMindAudioActionActive(),
        audioEnabled: isNekoIdleCatAudioEnabled(),
        nearChat: _isNekoCatMindCat1NearChat(button) };
    let reason = '';
    if (!button) reason = 'missing_button';
    else if (facts.tier !== tier) reason = 'tier_mismatch';
    else if (_isNekoIdleReturnDragActionBlocking(button) || _isAnyNekoIdleReturnDragActionBlocking()) reason = 'return_ball_drag_active';
    else if (facts.compactSurfaceDragging) reason = 'compact_surface_dragging';
    else if (facts.returnPending) reason = 'return_pending';
    else if (facts.transitionActive) reason = 'transition_active';
    else if (facts.independentActionActive) reason = 'active_independent_action';
    else if (!facts.returnBallVisible) reason = 'return_ball_not_visible';
    else if ((actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) && !button.querySelector('.neko-idle-return-art')) reason = 'missing_art';
    else if ((actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE) && !facts.nearChat) reason = 'near_chat_unavailable';
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN && !_canNekoCatMindControlPlayYarn()) reason = 'play_yarn_unavailable';
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE && (!button.__nekoIdleCat1Journey || !_canScheduleNekoIdleCat1PairMove(button, button.__nekoIdleCat1Journey))) reason = 'small_move_unavailable';
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING && (!facts.audioEnabled || !_nekoIdleCat1AmbientSoundState.active)) reason = facts.audioEnabled ? 'ambient_inactive' : 'audio_disabled';
    else if ((actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK) &&
        (!facts.audioEnabled || _nekoIdleSleepSoundState.tier !== tier)) reason = facts.audioEnabled ? 'sleep_feedback_inactive' : 'audio_disabled';
    else if (!Object.values(_NEKO_CAT_MIND_ACTION_IDS).includes(actionId)) reason = 'unknown_action';
    return _makeNekoCatMindProviderDecision(!reason, reason || 'allowed', { actionId, facts });
}

function _dryRunNekoCatMindCat1ButtonProvider(actionId, button) {
    if (!button) return _makeNekoCatMindProviderDecision(false, 'missing_button');
    if (_isNekoIdleReturnDragActionBlocking(button) || _isAnyNekoIdleReturnDragActionBlocking()) return _makeNekoCatMindProviderDecision(false, 'return_ball_drag_active');
    if (_isNekoIdleCompactSurfaceDragging()) return _makeNekoCatMindProviderDecision(false, 'compact_surface_dragging');
    if (_isNekoCatMindReturnPending(button) || _isAnyNekoCatMindReturnPending()) return _makeNekoCatMindProviderDecision(false, 'return_pending');
    if (_isNekoCatMindTransitionActive(button)) return _makeNekoCatMindProviderDecision(false, 'transition_active');
    if (_isNekoIdleCat1IndependentActionActive(button) || _isAnyNekoIdleCat1IndependentActionActive() || _isNekoCatMindAudioActionActive()) return _makeNekoCatMindProviderDecision(false, 'active_independent_action');
    return _evaluateNekoCatMindActionProvider(actionId, { button });
}
function _dryRunNekoCatMindSocialPingProvider(context = {}) {
    if (_isNekoIdleCompactSurfaceDragging()) return _makeNekoCatMindProviderDecision(false, 'compact_surface_dragging');
    const decision = _dryRunNekoCatMindCat1ButtonProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING, context.button || _findNekoCatMindVisibleButtonForTier(_NEKO_IDLE_TIER_CAT1));
    return decision.allowed ? _evaluateNekoCatMindActionProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING, context) : decision;
}
function _dryRunNekoCatMindPlayYarnProvider(context = {}) {
    if (_isNekoIdleCompactSurfaceDragging()) return _makeNekoCatMindProviderDecision(false, 'compact_surface_dragging');
    if (_isNekoCatMindChatTransitionActive()) return _makeNekoCatMindProviderDecision(false, 'transition_active');
    const button = context.button || _findNekoCatMindVisibleButtonForTier(_NEKO_IDLE_TIER_CAT1);
    const decision = _dryRunNekoCatMindCat1ButtonProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN, button);
    if (!decision.allowed) return decision;
    if (!_isNekoCatMindCat1NearChat(button)) return _makeNekoCatMindProviderDecision(false, 'near_chat_unavailable');
    if (!_canNekoCatMindControlPlayYarn()) return _makeNekoCatMindProviderDecision(false, 'play_yarn_unavailable');
    return _evaluateNekoCatMindActionProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN, context);
}
function _dryRunNekoCatMindSmallMoveProvider(context = {}) {
    return _evaluateNekoCatMindActionProvider(_NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE, context);
}
function _dryRunNekoCatMindSleepFeedbackProvider(actionId, context = {}) {
    if (_isNekoIdleCompactSurfaceDragging()) return _makeNekoCatMindProviderDecision(false, 'compact_surface_dragging');
    if (_isNekoCatMindAudioActionActive()) return _makeNekoCatMindProviderDecision(false, 'active_independent_action');
    if (_isAnyNekoCatMindReturnPending()) return _makeNekoCatMindProviderDecision(false, 'return_pending');
    if (_isNekoCatMindTransitionActive()) return _makeNekoCatMindProviderDecision(false, 'transition_active');
    return _evaluateNekoCatMindActionProvider(actionId, context);
}
function _dryRunNekoCatMindActionProvider(actionId, context = {}) {
    if (actionId && typeof actionId === 'object') { context = actionId; actionId = context.actionId; }
    const normalizedActionId = typeof actionId === 'string' ? actionId : '';
    let decision;
    if (normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK) decision = _dryRunNekoCatMindCat1ButtonProvider(normalizedActionId, context.button || _findNekoCatMindVisibleButtonForTier(_NEKO_IDLE_TIER_CAT1));
    else if (normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING) decision = _dryRunNekoCatMindSocialPingProvider(context);
    else if (normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) decision = _dryRunNekoCatMindPlayYarnProvider(context);
    else if (normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE) decision = _dryRunNekoCatMindSmallMoveProvider(context);
    else if (normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK || normalizedActionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK) decision = _dryRunNekoCatMindSleepFeedbackProvider(normalizedActionId, context);
    else decision = _makeNekoCatMindProviderDecision(false, 'unknown_action', { actionId: normalizedActionId });
    return _attachNekoCatMindProviderDiagnostics(normalizedActionId, decision, context);
}

function _acknowledgeNekoCatMindActionRequest(request, status, detail = {}) {
    try {
        return !!(window.nekoCatMind && typeof window.nekoCatMind.acknowledgeActionRequest === 'function' &&
            window.nekoCatMind.acknowledgeActionRequest({ requestId: request.requestId, actionId: request.actionId,
                status, reason: detail.reason || status, runId: detail.runId || '', timestamp: Date.now() }) === true);
    } catch (_) { return false; }
}
function _runNekoCatMindActionRequest(request) {
    if (!request || request.source !== 'cat_mind' || !request.requestId || !request.actionId || !request.tier) return;
    const actionId = request.actionId;
    const expectedTier = actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK ? _NEKO_IDLE_TIER_CAT2 :
        (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK ? _NEKO_IDLE_TIER_CAT3 : _NEKO_IDLE_TIER_CAT1);
    if (request.tier !== expectedTier) { _acknowledgeNekoCatMindActionRequest(request, 'rejected', { reason: 'request_tier_mismatch' }); return; }
    const provider = _dryRunNekoCatMindActionProvider(actionId, { source: 'cat-mind-action-adapter' });
    if (!provider.allowed) { _acknowledgeNekoCatMindActionRequest(request, 'rejected', { reason: provider.reason }); return; }
    let bound = false;
    const options = { source: 'cat_mind', requestId: request.requestId,
        onAccepted(run) { bound = _acknowledgeNekoCatMindActionRequest(request, 'accepted', { reason: 'runner_accepted', runId: run && run.runId }) || bound; },
        onStarted(run) { _acknowledgeNekoCatMindActionRequest(request, 'started', { reason: 'runner_started', runId: run && run.runId }); } };
    const button = _findNekoCatMindVisibleButtonForTier(expectedTier);
    let accepted = false;
    if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_EAT_SNACK) accepted = _playNekoIdleCat1EatAction(button, options);
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SMALL_MOVE) accepted = _startNekoIdleCat1PairMove(button, options);
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_PLAY_YARN) accepted = _playNekoIdleCat1PlayAction(button, options);
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT1_SOCIAL_PING) accepted = _playNekoIdleCat1AmbientSound(_nekoIdleCat1AmbientSoundState.token, options);
    else if (actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT2_NAP_FEEDBACK || actionId === _NEKO_CAT_MIND_ACTION_IDS.CAT3_SLEEP_FEEDBACK) accepted = _playNekoIdleSleepSound(expectedTier, _nekoIdleSleepSoundState.token, options);
    if (!accepted && !bound) _acknowledgeNekoCatMindActionRequest(request, 'rejected', { reason: 'runner_not_started' });
}
function _isNekoCatMindAudioActionActive() {
    return !!(_nekoIdleCat1AmbientSoundState.catMindActionId || _nekoIdleSleepSoundState.catMindActionId);
}
function _getNekoCatMindRuntimeGateSnapshot() {
    const tier = _getActiveNekoIdleReturnTier(); const button = _findNekoCatMindVisibleButtonForTier(tier);
    return Object.freeze({ returnPending: _isAnyNekoCatMindReturnPending(), dragPending: _isAnyNekoIdleReturnDragActionBlocking(), dragging: _isAnyNekoIdleReturnDragActionActive(),
        transitionActive: _isNekoCatMindTransitionActive(button), activeIndependentAction: _isAnyNekoIdleCat1IndependentActionActive() || _isNekoCatMindAudioActionActive(),
        returnBallVisible: !!button, validCatRuntime: tier !== _NEKO_IDLE_TIER_NONE, chatSurfaceDragging: _isNekoIdleCompactSurfaceDragging(), tier });
}
function _sanitizeNekoCatIdleObservationDetail(detail = {}) {
    const result = {};
    Object.keys(detail || {}).forEach((key) => {
        if (key === 'button' || key === 'container' || key === 'target' || key === 'originalEvent') return;
        const value = detail[key];
        if (value === null || value === undefined || typeof value === 'string' || typeof value === 'boolean') result[key] = value;
        else if (typeof value === 'number') result[key] = Number.isFinite(value) ? value : null;
    });
    return result;
}
function _dispatchNekoCatIdleObservationSource(type, detail = {}) {
    if (!type || typeof window === 'undefined' || typeof window.dispatchEvent !== 'function') return false;
    try {
        window.dispatchEvent(new CustomEvent(_NEKO_CAT_IDLE_OBSERVATION_SOURCE_EVENT, { detail: {
            type, source: detail.source || 'avatar-ui-buttons', tier: _normalizeNekoIdleReturnTier(detail.tier),
            timestamp: Number.isFinite(Number(detail.timestamp)) ? Number(detail.timestamp) : Date.now(),
            detail: _sanitizeNekoCatIdleObservationDetail(detail)
        }}));
        return true;
    } catch (_) { return false; }
}
function _clearNekoIdleThoughtBubbleForTier(tier) {
    _forEachNekoIdleReturnButton((button) => {
        if (_normalizeNekoIdleReturnTier(button.getAttribute('data-neko-idle-tier')) === _normalizeNekoIdleReturnTier(tier)) {
            _clearNekoIdleThoughtBubble(button);
        }
    });
}
if (typeof window !== 'undefined') {
    window.NekoCatMindActionProviders = Object.freeze({ ACTION_IDS: _NEKO_CAT_MIND_ACTION_IDS, RESULTS: _NEKO_CAT_MIND_ACTION_RESULTS,
        dryRun: _dryRunNekoCatMindActionProvider, getRuntimeGateSnapshot: _getNekoCatMindRuntimeGateSnapshot });
    window.addEventListener(_getNekoCatMindActionRequestEventName(), function (event) {
        _runNekoCatMindActionRequest(event && event.detail);
    });
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
