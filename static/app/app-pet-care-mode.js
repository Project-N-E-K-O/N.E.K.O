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
        CLEANUP_REQUEST: 'neko:pet-care:cleanup-request',
        CAT_LOCAL_ACTIVE_CHANGE: 'neko:cat-local-active-change',
    });

    // These are the Cat Mind autonomous actions that can conflict with a
    // future server-confirmed care operation.  The compatibility boundary
    // deliberately knows action ids only; it does not own Cat Mind scoring,
    // values, or action execution.
    var CAT_MIND_ACTION_IDS = Object.freeze([
        'cat1_social_ping',
        'cat1_eat_snack',
        'cat1_small_move',
        'cat1_play_yarn',
        'cat2_nap_feedback',
        'cat3_sleep_feedback',
    ]);

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
        updatedAt: Date.now(),
        reason: 'initial',
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
            updatedAt: state.updatedAt,
            reason: state.reason,
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

        state.generation += 1;
        state.accountId = nextAccountId;
        state.currentCharacterUid = nextCharacterUid;
        state.boundRoleId = nextBoundRoleId;
        state.updatedAt = Date.now();
        state.reason = normalizeString(next.reason) || 'context-change';
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
        if (normalizedMode !== MODES.PET_AVAILABLE && state.catMindSuppressed) {
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
            });
        }
        return current;
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
        if (!state.catMindSuppressed || !request || request.source !== 'cat_mind') {
            return { blocked: false, reason: '' };
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
        if (Object.prototype.hasOwnProperty.call(eventDetail, 'character_uid')) {
            context.currentCharacterUid = eventDetail.character_uid;
        } else if (Object.prototype.hasOwnProperty.call(eventDetail, 'currentCharacterUid')) {
            context.currentCharacterUid = eventDetail.currentCharacterUid;
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
            setCatLocalActive: setCatLocalActive,
            shouldBlockCatMindActionRequest: shouldBlockCatMindActionRequest,
            requestCleanup: requestCleanup,
        });
        window.addEventListener('chat-avatar-display-updated', function (event) {
            observeCurrentCharacter(event && event.detail);
        });
        window.addEventListener(EVENT_NAMES.CAT_LOCAL_ACTIVE_CHANGE, function (event) {
            var detail = event && event.detail;
            if (detail && typeof detail === 'object') setCatLocalActive(detail.active === true, detail);
        });
    }
})();
