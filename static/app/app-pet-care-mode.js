/**
 * app-pet-care-mode.js - stage 0 pet-care mode and context gate.
 *
 * This module owns only the client-side compatibility boundary. It does not
 * fetch pet data, calculate pet values, or settle any pet operation. Later
 * stages can feed an authenticated snapshot into this context; Cat Mind keeps
 * its own short-term runtime and only receives the explicitly allowed adapter
 * input described by the product design.
 */
(function () {
    'use strict';

    var MODES = Object.freeze({
        SESSION_INVALID: 'session-invalid',
        PET_LOADING: 'pet-loading',
        PET_AVAILABLE: 'pet-available',
        PET_UNAVAILABLE: 'pet-unavailable',
        EXPLORATION_AWAY: 'exploration-away',
    });

    var EVENT_NAMES = Object.freeze({
        MODE_CHANGE: 'neko:pet-care:mode-change',
        CONTEXT_CHANGE: 'neko:pet-care:context-change',
        COMPATIBILITY_CHANGE: 'neko:pet-care:compatibility-change',
        NEGATIVE_PRESENTATION_CHANGE: 'neko:pet-care:negative-presentation-change',
        CLEANUP_REQUEST: 'neko:pet-care:cleanup-request',
        CAT_LOCAL_ACTIVE_CHANGE: 'neko:cat-local-active-change',
        EXPLORATION_GATE: 'neko:pet-care:exploration-gate',
        CONTEXT_CHANGE_REQUEST: 'neko:pet-care:context-change-request',
    });

    // These are the Cat Mind autonomous actions that can conflict while a
    // user-owned PetCare care/play presentation owns the cat. The lifecycle
    // owner opens and closes that short conflict window; this boundary only
    // knows action ids and does not own Cat Mind scoring, values or execution.
    var CAT_MIND_ACTION_IDS = Object.freeze([
        'cat1_social_ping',
        'cat1_eat_snack',
        'cat1_small_move',
        'cat1_play_yarn',
        'cat2_nap_feedback',
        'cat3_sleep_feedback',
    ]);
    var TERMINAL_EXPLORATION_ACTIVITY_LIMIT = 32;

    var VALID_MODES = Object.freeze(Object.keys(MODES).reduce(function (result, key) {
        result[MODES[key]] = true;
        return result;
    }, {}));

    var state = {
        mode: MODES.SESSION_INVALID,
        generation: 0,
        accountId: '',
        currentCharacterUid: '',
        boundRoleId: '',
        catLocalActive: false,
        catMindSuppressed: false,
        blockedCatMindActionIds: null,
        negativePresentation: { sick: false, sulk: false },
        updatedAt: Date.now(),
        reason: 'initial',
        explorationAway: null,
        explorationGeneration: 0,
        terminalExplorationActivityIds: [],
    };

    function normalizeString(value) {
        return typeof value === 'string' ? value.trim() : '';
    }

    function normalizeActionIds(value) {
        if (!Array.isArray(value)) return null;
        var result = [];
        value.forEach(function (item) {
            var actionId = normalizeString(item);
            if (actionId && result.indexOf(actionId) < 0) result.push(actionId);
        });
        return result.length ? result : [];
    }

    function snapshot() {
        return Object.freeze({
            mode: state.mode,
            generation: state.generation,
            accountId: state.accountId,
            currentCharacterUid: state.currentCharacterUid,
            boundRoleId: state.boundRoleId,
            catLocalActive: state.catLocalActive,
            catMindSuppressed: state.catMindSuppressed,
            blockedCatMindActionIds: state.blockedCatMindActionIds
                ? state.blockedCatMindActionIds.slice()
                : null,
            negativePresentation: Object.freeze({
                sick: state.negativePresentation.sick === true,
                sulk: state.negativePresentation.sulk === true,
            }),
            updatedAt: state.updatedAt,
            reason: state.reason,
            explorationAway: state.explorationAway
                ? Object.freeze(Object.assign({}, state.explorationAway))
                : null,
            explorationGeneration: state.explorationGeneration,
        });
    }

    function emit(name, detail) {
        if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function' ||
            typeof CustomEvent !== 'function') return false;
        try {
            window.dispatchEvent(new CustomEvent(name, { detail: detail }));
            return true;
        } catch (_) {
            return false;
        }
    }

    function updateContext(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        var hasOwn = Object.prototype.hasOwnProperty;
        var nextAccountId = hasOwn.call(next, 'accountId')
            ? normalizeString(next.accountId)
            : state.accountId;
        var nextCharacterUid = hasOwn.call(next, 'currentCharacterUid')
            ? normalizeString(next.currentCharacterUid)
            : (hasOwn.call(next, 'characterUid') ? normalizeString(next.characterUid) : state.currentCharacterUid);
        var nextBoundRoleId = hasOwn.call(next, 'boundRoleId')
            ? normalizeString(next.boundRoleId)
            : state.boundRoleId;
        var changed = nextAccountId !== state.accountId ||
            nextCharacterUid !== state.currentCharacterUid ||
            nextBoundRoleId !== state.boundRoleId;
        if (!changed && next.forceNewGeneration !== true) return snapshot();

        // A context switch invalidates the old account/role activity token.
        // Release the local entity gate without settling anything; the new
        // account must reload its own server activity before it can gate the
        // renderer again.
        if (state.explorationAway && state.explorationAway.active) {
            endExplorationAway({
                activityId: state.explorationAway.activityId,
                activityGeneration: state.explorationAway.generation,
                reason: 'pet-care-context-changed',
                confirmedTerminal: false,
            });
        }
        state.generation += 1;
        state.accountId = nextAccountId;
        state.currentCharacterUid = nextCharacterUid;
        state.boundRoleId = nextBoundRoleId;
        state.updatedAt = Date.now();
        var contextReason = normalizeString(next.reason) || 'context-change';
        state.reason = contextReason;
        // Negative status is a presentation fact scoped to the exact
        // account/binding generation. Never let the previous pet's visible
        // state survive while the new authoritative snapshot is loading.
        setNegativePresentation({
            sick: false,
            sulk: false,
            reason: 'pet-care-context-changed',
        });
        state.reason = contextReason;
        // A changed account, role, or binding invalidates any old autonomous
        // action gate before a later service response can re-enable it.
        if (state.catMindSuppressed) {
            setCatMindCompatibility({
                suppressed: false,
                cleanup: true,
                reason: 'pet-care-context-changed',
            });
        }
        var current = snapshot();
        emit(EVENT_NAMES.CONTEXT_CHANGE, current);
        return current;
    }

    function setMode(mode, detail) {
        var normalizedMode = normalizeString(mode);
        if (!VALID_MODES[normalizedMode]) {
            throw new TypeError('unknown pet-care mode');
        }
        var next = detail && typeof detail === 'object' ? detail : {};
        var previous = snapshot();
        // Exploration is an activity gate layered over the existing pet mode.
        // It must not release a currently active care/play conflict window
        // merely because the visual cat is temporarily away.
        if (normalizedMode !== MODES.PET_AVAILABLE &&
            normalizedMode !== MODES.EXPLORATION_AWAY && state.catMindSuppressed) {
            setCatMindCompatibility({
                suppressed: false,
                cleanup: true,
                reason: 'pet-care-mode-' + normalizedMode,
            });
        }
        state.mode = normalizedMode;
        state.updatedAt = Date.now();
        state.reason = normalizeString(next.reason) || 'mode-change';
        if (next.context && typeof next.context === 'object') updateContext(next.context);
        var current = snapshot();
        emit(EVENT_NAMES.MODE_CHANGE, { previous: previous, current: current });
        if (next.cleanup === true) requestCleanup(next.cleanupDetail || { reason: state.reason });
        return current;
    }

    function setCatMindCompatibility(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        var nextSuppressed = next.suppressed === true;
        var normalizedIds = nextSuppressed ? normalizeActionIds(next.actionIds) : null;
        if (nextSuppressed && normalizedIds === null) normalizedIds = CAT_MIND_ACTION_IDS.slice();
        var previous = snapshot();
        var wasSuppressed = state.catMindSuppressed;
        var cleanupActionIds = wasSuppressed && Array.isArray(state.blockedCatMindActionIds)
            ? state.blockedCatMindActionIds.slice()
            : (nextSuppressed && Array.isArray(normalizedIds) ? normalizedIds.slice() : null);
        var sameIds = state.blockedCatMindActionIds === normalizedIds ||
            (Array.isArray(state.blockedCatMindActionIds) &&
                Array.isArray(normalizedIds) &&
                state.blockedCatMindActionIds.length === normalizedIds.length &&
                state.blockedCatMindActionIds.every(function (item, index) {
                    return item === normalizedIds[index];
                }));
        if (state.catMindSuppressed === nextSuppressed && sameIds) return previous;
        state.catMindSuppressed = nextSuppressed;
        state.blockedCatMindActionIds = normalizedIds;
        state.updatedAt = Date.now();
        state.reason = normalizeString(next.reason) || 'cat-mind-compatibility-change';
        var current = snapshot();
        emit(EVENT_NAMES.COMPATIBILITY_CHANGE, { previous: previous, current: current });
        if (next.cleanup !== false && (nextSuppressed || wasSuppressed)) {
            requestCleanup({
                reason: state.reason,
                actionIds: cleanupActionIds,
                source: 'cat_mind',
                suppressed: nextSuppressed,
            });
        }
        return current;
    }

    function setNegativePresentation(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        if (next.context && !isCurrentContext(next.context)) return snapshot();
        var sick = next.sick === true;
        var sulk = next.sulk === true;
        if (state.negativePresentation.sick === sick &&
            state.negativePresentation.sulk === sulk) return snapshot();
        var previous = Object.freeze({
            sick: state.negativePresentation.sick === true,
            sulk: state.negativePresentation.sulk === true,
        });
        state.negativePresentation = { sick: sick, sulk: sulk };
        state.updatedAt = Date.now();
        state.reason = normalizeString(next.reason) || 'negative-presentation-change';
        var current = Object.freeze({ sick: sick, sulk: sulk });
        emit(EVENT_NAMES.NEGATIVE_PRESENTATION_CHANGE, {
            previous: previous,
            current: current,
            generation: state.generation,
            reason: state.reason,
        });
        return snapshot();
    }

    function shouldSuppressHoverResponse() {
        return state.negativePresentation.sulk === true;
    }

    function setCatLocalActive(active, detail) {
        var nextActive = active === true;
        var next = detail && typeof detail === 'object' ? detail : {};
        if (state.catLocalActive === nextActive) return snapshot();
        state.catLocalActive = nextActive;
        state.updatedAt = Date.now();
        state.reason = normalizeString(next.reason) || 'cat-local-active-change';
        if (!nextActive && state.catMindSuppressed) {
            setCatMindCompatibility({
                suppressed: false,
                cleanup: true,
                reason: 'pet-care-cat-form-left',
            });
        }
        return snapshot();
    }

    function shouldBlockCatMindActionRequest(request) {
        if (state.explorationAway && state.explorationAway.active &&
            request && request.source === 'cat_mind') {
            return {
                blocked: true,
                reason: 'pet-care-exploration-away',
                mode: state.mode,
                generation: state.generation,
                activityId: state.explorationAway.activityId,
                activityGeneration: state.explorationAway.generation,
            };
        }
        if (!state.catMindSuppressed || !request || request.source !== 'cat_mind') {
            return { blocked: false, reason: '' };
        }
        // A yarn drag is a user-owned interaction source. Cat Mind still
        // decides whether the cat responds, but the request must pass through
        // so Stage 6 can wait for that real response. Autonomous play with no
        // matching user trigger remains suppressed.
        if (request.actionId === 'cat1_play_yarn' && request.detail &&
            Array.isArray(request.detail.triggerTypes) &&
            request.detail.triggerTypes.indexOf('chat_yarn_drag_completed') >= 0) {
            return { blocked: false, reason: 'pet-care-user-yarn-play' };
        }
        var actionIds = state.blockedCatMindActionIds;
        if (Array.isArray(actionIds) && actionIds.indexOf(request.actionId) < 0) {
            return { blocked: false, reason: '' };
        }
        return {
            blocked: true,
            reason: 'pet-care-cat-mind-suppressed',
            mode: state.mode,
            generation: state.generation,
        };
    }

    function isExplorationAway() {
        return !!(state.explorationAway && state.explorationAway.active);
    }

    function shouldBlockEntityOutput(detail) {
        if (!isExplorationAway()) return { blocked: false, reason: '' };
        return {
            blocked: true,
            reason: 'pet-care-exploration-away',
            activityId: state.explorationAway.activityId,
            activityGeneration: state.explorationAway.generation,
        };
    }

    function beginExplorationAway(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        var activityId = normalizeString(next.activityId || next.explorationId);
        if (!activityId) throw new TypeError('exploration activityId is required');
        if (isExplorationAway() && state.explorationAway.activityId === activityId) {
            return snapshot();
        }
        state.explorationGeneration += 1;
        state.explorationAway = {
            active: true,
            activityId: activityId,
            generation: state.explorationGeneration,
            reason: normalizeString(next.reason) || 'exploration-started',
            startedAt: Number(next.startedAt) || Date.now(),
        };
        state.updatedAt = Date.now();
        state.reason = state.explorationAway.reason;
        // This is deliberately independent from setMode(): changing mode
        // would release an active care/play conflict window as a side effect.
        var current = snapshot();
        if (next.emit !== false) {
            emit(EVENT_NAMES.EXPLORATION_GATE, {
                source: 'pet-care-mode',
                active: true,
                activityId: activityId,
                generation: state.explorationAway.generation,
                reason: state.explorationAway.reason,
                context: captureContext(),
                current: current,
            });
        }
        // `emit: false` only prevents this adapter from re-broadcasting an
        // external gate event.  The first accepted active activity still owns
        // the cat entity and must cancel any work that was already in flight.
        // The same-activity early return above keeps this cleanup idempotent.
        requestCleanup({
            source: 'exploration',
            reason: state.explorationAway.reason,
            activityId: activityId,
            activityGeneration: state.explorationAway.generation,
        });
        return current;
    }

    function rememberTerminalExplorationActivity(activityId) {
        var normalized = normalizeString(activityId);
        if (!normalized) return;
        var terminalIds = state.terminalExplorationActivityIds;
        var existingIndex = terminalIds.indexOf(normalized);
        if (existingIndex >= 0) terminalIds.splice(existingIndex, 1);
        terminalIds.push(normalized);
        if (terminalIds.length > TERMINAL_EXPLORATION_ACTIVITY_LIMIT) {
            terminalIds.splice(0, terminalIds.length - TERMINAL_EXPLORATION_ACTIVITY_LIMIT);
        }
    }

    function isTerminalExplorationActivity(activityId) {
        return state.terminalExplorationActivityIds.indexOf(activityId) >= 0;
    }

    function endExplorationAway(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        var currentAway = state.explorationAway;
        if (!currentAway || !currentAway.active) return snapshot();
        var activityId = normalizeString(next.activityId || next.explorationId);
        var activityGeneration = Number(next.activityGeneration || next.generation);
        if (!activityId) return snapshot();
        if (activityId && activityId !== currentAway.activityId) return snapshot();
        if (Number.isFinite(activityGeneration) && activityGeneration !== currentAway.generation) {
            return snapshot();
        }
        var confirmedTerminal = next.confirmedTerminal !== false;
        if (confirmedTerminal) rememberTerminalExplorationActivity(currentAway.activityId);
        state.explorationAway = null;
        state.updatedAt = Date.now();
        state.reason = normalizeString(next.reason) || 'exploration-ended';
        var current = snapshot();
        if (next.emit !== false) {
            emit(EVENT_NAMES.EXPLORATION_GATE, {
                source: 'pet-care-mode',
                active: false,
                activityId: currentAway.activityId,
                generation: currentAway.generation,
                reason: state.reason,
                confirmedTerminal: confirmedTerminal,
                context: captureContext(),
                current: current,
            });
        }
        return current;
    }

    function isCurrentExploration(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        var currentAway = state.explorationAway;
        if (!currentAway || !currentAway.active) return false;
        var activityId = normalizeString(next.activityId || next.explorationId);
        var activityGeneration = Number(next.activityGeneration || next.generation);
        return (!activityId || activityId === currentAway.activityId) &&
            (!Number.isFinite(activityGeneration) || activityGeneration === currentAway.generation);
    }

    // Cross-module gate events can arrive after a newer account/role context
    // has already been applied.  The captured context generation rejects that
    // stale event; within the current context only the same activity may
    // change the local renderer gate.
    function shouldApplyExplorationGate(detail) {
        var next = detail && typeof detail === 'object' ? detail : {};
        var activityId = normalizeString(next.activityId || next.explorationId || next.exploration_id);
        var currentAway = state.explorationAway;
        if (!activityId || !isCurrentContext(next.context)) return false;
        if (next.active === true) {
            if (!currentAway || !currentAway.active) {
                // Do not resurrect a confirmed terminal activity when its
                // delayed active event arrives after the gate ended.
                return !isTerminalExplorationActivity(activityId);
            }
            return currentAway.activityId === activityId;
        }
        if (next.active !== false) return false;
        return !!((currentAway && currentAway.active && currentAway.activityId === activityId) ||
            (!currentAway && (isTerminalExplorationActivity(activityId) ||
                next.confirmedTerminal === false)));
    }

    function captureContext() {
        return snapshot();
    }

    function isCurrentContext(context) {
        if (!context || typeof context !== 'object') return false;
        return Number(context.generation) === state.generation &&
            normalizeString(context.accountId) === state.accountId &&
            normalizeString(context.currentCharacterUid || context.characterUid) === state.currentCharacterUid &&
            normalizeString(context.boundRoleId) === state.boundRoleId;
    }

    function requestCleanup(detail) {
        var payload = Object.assign({
            reason: 'pet-care-mode-change',
            generation: state.generation,
            mode: state.mode,
        }, detail && typeof detail === 'object' ? detail : {});
        return emit(EVENT_NAMES.CLEANUP_REQUEST, payload);
    }

    function observeCurrentCharacter(detail) {
        var eventDetail = detail && typeof detail === 'object' ? detail : {};
        var context = { reason: eventDetail.reason || 'character-display-updated' };
        var nextUid = '';
        if (Object.prototype.hasOwnProperty.call(eventDetail, 'character_uid')) {
            context.currentCharacterUid = eventDetail.character_uid;
            nextUid = normalizeString(eventDetail.character_uid);
        } else if (Object.prototype.hasOwnProperty.call(eventDetail, 'currentCharacterUid')) {
            context.currentCharacterUid = eventDetail.currentCharacterUid;
            nextUid = normalizeString(eventDetail.currentCharacterUid);
        } else if (Object.prototype.hasOwnProperty.call(eventDetail, 'characterUid')) {
            context.currentCharacterUid = eventDetail.characterUid;
            nextUid = normalizeString(eventDetail.characterUid);
        }
        // The mode adapter is loaded before the lifecycle owner.  During a
        // character/model switch it must not release the exploration entity
        // gate before the owner has confirmed the server recall.  Emit a
        // request and keep the old context intact; app-pet-care's single
        // lifecycle coordinator will recall first, then call updateContext.
        if (state.explorationAway && state.explorationAway.active &&
            nextUid && nextUid !== state.currentCharacterUid) {
            emit(EVENT_NAMES.CONTEXT_CHANGE_REQUEST, {
                current: snapshot(),
                nextCharacterUid: nextUid,
                reason: context.reason,
            });
            return snapshot();
        }
        return updateContext(context);
    }

    if (typeof window !== 'undefined') {
        window.NekoPetCareModeContract = Object.freeze({
            MODES: MODES,
            EVENT_NAMES: EVENT_NAMES,
            CAT_MIND_ACTION_IDS: CAT_MIND_ACTION_IDS,
        });
        window.nekoPetCareMode = Object.freeze({
            getState: snapshot,
            setMode: setMode,
            updateContext: updateContext,
            captureContext: captureContext,
            isCurrentContext: isCurrentContext,
            setCatMindCompatibility: setCatMindCompatibility,
            setNegativePresentation: setNegativePresentation,
            setCatLocalActive: setCatLocalActive,
            shouldSuppressHoverResponse: shouldSuppressHoverResponse,
            shouldBlockCatMindActionRequest: shouldBlockCatMindActionRequest,
            shouldBlockEntityOutput: shouldBlockEntityOutput,
            isExplorationAway: isExplorationAway,
            beginExplorationAway: beginExplorationAway,
            endExplorationAway: endExplorationAway,
            isCurrentExploration: isCurrentExploration,
            shouldApplyExplorationGate: shouldApplyExplorationGate,
            requestCleanup: requestCleanup,
        });
        window.addEventListener('chat-avatar-display-updated', function (event) {
            observeCurrentCharacter(event && event.detail);
        });
        window.addEventListener(EVENT_NAMES.CAT_LOCAL_ACTIVE_CHANGE, function (event) {
            var detail = event && event.detail;
            if (detail && typeof detail === 'object') setCatLocalActive(detail.active === true, detail);
        });
        // The exploration UI may publish the server-confirmed activity fact
        // before it has a direct reference to this mode adapter. Consume that
        // fact here so every renderer owner sees one authoritative local gate.
        window.addEventListener(EVENT_NAMES.EXPLORATION_GATE, function (event) {
            var detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail : {};
            if (detail.source === 'pet-care-mode') return;
            var activityId = normalizeString(detail.activityId || detail.explorationId || detail.exploration_id);
            var generation = detail.activityGeneration || detail.generation;
            if (detail.active === true && activityId) {
                if (!shouldApplyExplorationGate(detail)) return;
                beginExplorationAway({
                    activityId: activityId,
                    activityGeneration: generation,
                    reason: detail.reason || 'exploration-gate-event',
                    emit: false,
                });
            } else if (detail.active !== true && shouldApplyExplorationGate(detail)) {
                // The activity generation in this cross-module event belongs
                // to the service snapshot (and may be a UUID or a numeric
                // server revision).  The mode adapter owns a separate local
                // generation for stale renderer events.  Releasing with the
                // service value would therefore be able to ignore a valid
                // return after another local activity has been created.
                // Activity ID is the shared identity; the local generation is
                // checked by calls that originate inside this adapter.
                endExplorationAway({
                    activityId: activityId,
                    reason: detail.reason || 'exploration-gate-event',
                    emit: false,
                });
            }
        });
    }
})();
