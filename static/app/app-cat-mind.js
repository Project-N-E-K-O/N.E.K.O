/**
 * app-cat-mind.js - observation runtime for the cat idle state machine.
 *
 * This runtime observes current cat-form facts and action results. The
 * selector issues a DOM-free action request on its queued decision turn;
 * the renderer-side adapter remains the only code that starts a runner. This
 * module never alters existing triggers or writes DOM. The separate
 * app-cat-mind-debug module owns opt-in visual debugging.
 */
(function () {
    'use strict';

    // Action result is consumed as observation feedback from existing avatar
    // action runners.
    var EVENT_NAMES = Object.freeze({
        OBSERVATION: 'neko:cat-mind:observation',
        STATE_CHANGE: 'neko:cat-mind:state-change',
        ACTION_REQUEST: 'neko:cat-mind:action-request',
        ACTION_RESULT: 'neko:cat-mind:action-result',
        RETURN_SUMMARY: 'neko:cat-mind:return-summary',
        CAT_LOCAL_ACTIVE_CHANGE: 'neko:cat-local-active-change',
    });

    // Debug visibility is deliberately separate from Cat Mind operation:
    // Cat Mind always owns its scheduler while the inspector remains opt-in.
    var DEBUG_SETTING_KEY = 'neko.catMind.debug';
    var DEBUG_QUERY_PARAM = 'cat_mind_debug';

    var OBSERVATION_PAYLOAD_FIELDS = Object.freeze([
        'type',
        'source',
        'tier',
        'timestamp',
        'detail',
    ]);

    var TIERS = Object.freeze({
        CAT1: 'cat1',
        CAT2: 'cat2',
        CAT3: 'cat3',
    });

    var ACTION_IDS = Object.freeze({
        CAT1_SOCIAL_PING: 'cat1_social_ping',
        CAT1_EAT_SNACK: 'cat1_eat_snack',
        CAT1_SMALL_MOVE: 'cat1_small_move',
        CAT1_PLAY_YARN: 'cat1_play_yarn',
        CAT2_NAP_FEEDBACK: 'cat2_nap_feedback',
        CAT3_SLEEP_FEEDBACK: 'cat3_sleep_feedback',
        QUIET: 'quiet',
        STAY_IDLE: 'stay_idle',
    });

    // Return memory is intentionally smaller than recentEvents. It only keeps
    // the last trustworthy activity/rest chapter and never participates in
    // scoring, cooldowns, providers, or desktop bridges.
    var RETURN_EPISODE_ACTIVITY_ORDER = Object.freeze([
        ACTION_IDS.CAT1_SOCIAL_PING,
        ACTION_IDS.CAT1_EAT_SNACK,
        ACTION_IDS.CAT1_SMALL_MOVE,
        ACTION_IDS.CAT1_PLAY_YARN,
    ]);
    var RETURN_EPISODE_HIGHLIGHTS = Object.freeze({
        cat1_social_ping: 'social_ping',
        cat1_eat_snack: 'ate_snack',
        cat1_small_move: 'small_move',
        cat1_play_yarn: 'played_yarn',
    });

    var OBSERVATION_TYPES = Object.freeze({
        CAT_ENTERED: 'cat_entered',
        CAT_ELAPSED: 'cat_elapsed',
        INACTIVE_ELAPSED: 'inactive_elapsed',
        SINCE_LAST_ACTION: 'since_last_action',
        DRAG_START: 'drag_start',
        DRAG_END: 'drag_end',
        DRAG_CANCELLED: 'drag_cancelled',
        RAPID_DRAG: 'rapid_drag',
        CAT_HOVER_REACTION: 'cat_hover_reaction',
        THOUGHT_BUBBLE_POP: 'thought_bubble_pop',
        RETURN_CLICK: 'return_click',
        TIER_CHANGED: 'tier_changed',
        TIER_DEMOTED_BY_DRAG: 'tier_demoted_by_drag',
        CHAT_MINIMIZED_VISIBLE: 'chat_minimized_visible',
        CHAT_MINIMIZED_MOVED_FAR: 'chat_minimized_moved_far',
        CHAT_YARN_DRAG_COMPLETED: 'chat_yarn_drag_completed',
        CHAT_COMPACT_SURFACE_VISIBLE: 'chat_compact_surface_visible',
        CHAT_EXPANDED: 'chat_expanded',
        CHAT_IDLE_DOCKED_NEAR_CAT: 'chat_idle_docked_near_cat',
        DESKTOP_OCCLUSION_OR_LAYER_CHANGE: 'desktop_occlusion_or_layer_change',
        CAT1_WALK_DONE_NEAR_CHAT: 'cat1_walk_done_near_chat',
        CAT1_STRETCH_DONE_NEAR_CHAT: 'cat1_stretch_done_near_chat',
        CAT1_LOCAL_PLAY_DONE: 'cat1_local_play_done',
        CAT1_LOCAL_PLAY_CANCELLED: 'cat1_local_play_cancelled',
        CAT1_COMPACT_TOP_EDGE_DONE: 'cat1_compact_top_edge_done',
        CAT1_COMPACT_TOP_EDGE_DROP: 'cat1_compact_top_edge_drop',
        EDGE_PEEK_AFTER_DRAG: 'edge_peek_after_drag',
        SOCIAL_PING_DONE: 'social_ping_done',
        SOCIAL_PING_FAILED: 'social_ping_failed',
        SMALL_MOVE_DONE: 'small_move_done',
        SMALL_MOVE_CANCELLED: 'small_move_cancelled',
        EAT_DONE: 'eat_done',
        EAT_CANCELLED: 'eat_cancelled',
        PLAY_DONE: 'play_done',
        PLAY_CANCELLED: 'play_cancelled',
        SLEEP_FEEDBACK_DONE: 'sleep_feedback_done',
        SLEEP_FEEDBACK_FAILED: 'sleep_feedback_failed',
        ACTION_INTERRUPTED_BY_DRAG: 'action_interrupted_by_drag',
        ACTION_INTERRUPTED_BY_RETURN: 'action_interrupted_by_return',
        ACTION_INTERRUPTED_BY_TIER_CHANGE: 'action_interrupted_by_tier_change',
    });

    var MIND_FIELDS = Object.freeze([
        'appetite',
        'sleepiness',
        'energy',
        'social_need',
        'stimulation_need',
    ]);
    var RECENT_EVENT_LIMIT = 40;
    var SEEN_EVENT_LIMIT = 80;
    var CHAT_MOVED_FAR_DISTANCE_PX = 24;
    var AUTONOMOUS_TICK_INTERVAL_MS = 30 * 1000;
    var ACTION_REQUEST_LEASE_MS = 5 * 1000;
    var ACTION_ACCEPTED_START_LEASE_MS = 12 * 1000;
    var TIME_RATE_PER_MINUTE = Object.freeze({
        cat1: Object.freeze({ appetite: 0.0055, sleepiness: 0.0045, energy: -0.0045, social_need: 0.024, stimulation_need: 0.032 }),
        cat2: Object.freeze({ appetite: 0.0045, sleepiness: 0.0135, energy: -0.008, social_need: 0.01, stimulation_need: 0.0065 }),
        cat3: Object.freeze({ appetite: 0.003, sleepiness: 0.0085, energy: -0.0035, social_need: 0.0055, stimulation_need: 0.003 }),
    });
    // Every registered action uses the same scoring layers and monotonic
    // response curves. Positive need surplus rises quickly enough to answer a
    // dense interaction burst, while the deeper cadence floor and the action's
    // own power-recovery cooldown keep ordinary idle and repeats restrained.
    var ACTION_SCORE_POLICY = Object.freeze({
        negativeNeedCurveRange: 14,
        positiveNeedCurveRange: 8,
        positiveNeedCurveCeiling: 78,
        positiveNeedCurveTailCeiling: 20,
        positiveNeedCurveTailScale: 8,
        cadenceFloor: -58,
        cadenceCeiling: 18,
        cadenceRecoveryMs: 4.85 * 60 * 1000,
        cooldownPenaltyPoints: 36,
        cooldownMultiplier: 4.2,
        cooldownRecoveryExponent: 1.9,
    });
    // Short-lived action intent is selector context, not a sixth need. A
    // strong, explicit invitation may move the matching action ahead of the
    // ordinary cadence, while the action's own cooldown still dominates a
    // just-started repeat.
    var ACTION_INTENT_POLICY = Object.freeze({
        maxContribution: 84,
        cooldownPenaltyRelief: 0.5,
        freshHoldMs: 30 * 1000,
        halfLifeMs: 90 * 1000,
        yarnOfferStrongStrength: 0.9,
        yarnOfferNearStrength: 0.62,
    });
    // Physical work is settled from terminal renderer facts. It changes only
    // the existing five dimensions and is deliberately separate from action
    // intent: moving can make the cat hungry/tired without secretly selecting
    // a particular runner.
    var PHYSICAL_ACTIVITY_POLICY = Object.freeze({
        referenceDistancePx: 160,
        referenceDurationMs: 2200,
        appetiteDelta: 0.04,
        energyDelta: -0.045,
        sleepinessDelta: 0.02,
    });
    var INTERACTION_FEEDBACK_POLICY = Object.freeze({
        hover: Object.freeze({ social_need: 0.006, stimulation_need: 0.018, energy: -0.001 }),
        dragStart: Object.freeze({ social_need: 0.01, stimulation_need: 0.035 }),
        dragEnd: Object.freeze({ social_need: 0.025, stimulation_need: 0.18 }),
        dragCancelled: Object.freeze({ social_need: 0.012, stimulation_need: 0.016 }),
        rapidImmediate: Object.freeze({ social_need: 0.08, stimulation_need: 0.4 }),
        rapidTerminal: Object.freeze({ social_need: 0.03, stimulation_need: 0.12 }),
        bubbleSocialSatisfaction: 0.28,
        bubbleStimulationSatisfaction: 0.18,
    });
    // Ordinary idle pressure still grows at the existing rate. Only overflow
    // above a tier comfort band leaks away, so a short burst is answered now
    // instead of leaving the fields pinned at 1 for many later minutes.
    var NEED_OVERFLOW_POLICY = Object.freeze({
        cat1: Object.freeze({ socialComfort: 0.62, socialHalfLifeMinutes: 4, stimulationComfort: 0.7, stimulationHalfLifeMinutes: 3 }),
        cat2: Object.freeze({ socialComfort: 0.48, socialHalfLifeMinutes: 5, stimulationComfort: 0.42, stimulationHalfLifeMinutes: 5 }),
        cat3: Object.freeze({ socialComfort: 0.36, socialHalfLifeMinutes: 6, stimulationComfort: 0.3, stimulationHalfLifeMinutes: 6 }),
    });
    var ACCOUNTED_PHYSICAL_ACTIVITY_LIMIT = 48;
    var ACCOUNTED_INTERACTION_ACTIVITY_LIMIT = 48;
    var ACTION_SCORE_CONFIG = Object.freeze({
        cat1_social_ping: Object.freeze({ threshold: 36, cooldownMs: 8 * 60 * 1000 }),
        cat1_eat_snack: Object.freeze({ threshold: 40, cooldownMs: 8 * 60 * 1000 }),
        cat1_small_move: Object.freeze({ threshold: 51, cooldownMs: 8 * 60 * 1000 }),
        cat1_play_yarn: Object.freeze({ threshold: 52, cooldownMs: 10 * 60 * 1000 }),
        cat2_nap_feedback: Object.freeze({ threshold: 54, cooldownMs: 10 * 60 * 1000 }),
        cat3_sleep_feedback: Object.freeze({ threshold: 50, cooldownMs: 12 * 60 * 1000 }),
    });
    var ACTION_TIE_BREAK = Object.freeze([
        ACTION_IDS.CAT1_SOCIAL_PING,
        ACTION_IDS.CAT2_NAP_FEEDBACK,
        ACTION_IDS.CAT3_SLEEP_FEEDBACK,
        ACTION_IDS.CAT1_SMALL_MOVE,
        ACTION_IDS.CAT1_EAT_SNACK,
        ACTION_IDS.CAT1_PLAY_YARN,
    ]);
    var autonomousClockGeneration = 0;
    var actionRequestLeaseTimer = 0;
    var actionRequestLeaseGeneration = 0;

    var runtimeState = createInitialRuntimeState();

    function hasOwn(object, key) {
        return Object.prototype.hasOwnProperty.call(object, key);
    }

    function coerceBoolean(value, fallback) {
        if (value === true || value === false) {
            return value;
        }
        if (typeof value === 'string') {
            var normalized = value.trim().toLowerCase();
            if (normalized === 'true' || normalized === '1' || normalized === 'yes' || normalized === 'on') {
                return true;
            }
            if (normalized === 'false' || normalized === '0' || normalized === 'no' || normalized === 'off') {
                return false;
            }
        }
        return fallback;
    }

    function getDebugQueryOverride() {
        try {
            var search = window.location && typeof window.location.search === 'string'
                ? window.location.search.replace(/^\?/, '')
                : '';
            if (!search) return null;
            var pairs = search.split('&');
            for (var index = 0; index < pairs.length; index += 1) {
                var parts = pairs[index].split('=');
                var key = decodeURIComponent(parts.shift() || '');
                if (key !== DEBUG_QUERY_PARAM) continue;
                var value = decodeURIComponent(parts.join('=').replace(/\+/g, ' '));
                return coerceBoolean(value, null);
            }
        } catch (_) {}
        return null;
    }

    function isDebugEnabled() {
        var queryOverride = getDebugQueryOverride();
        if (queryOverride !== null) {
            return queryOverride;
        }
        if (hasOwn(window, '__NEKO_CAT_MIND_DEBUG__')) {
            return coerceBoolean(window.__NEKO_CAT_MIND_DEBUG__, false);
        }
        try {
            if (window.localStorage) {
                var storedValue = window.localStorage.getItem(DEBUG_SETTING_KEY);
                if (storedValue !== null) {
                    return coerceBoolean(storedValue, false);
                }
            }
        } catch (_) {}
        return false;
    }

    function nowMs() {
        return Date.now();
    }

    function clamp01(value) {
        var number = Number(value);
        if (!Number.isFinite(number)) {
            return 0;
        }
        return Math.max(0, Math.min(1, number));
    }

    function clonePlain(value) {
        if (value === null || value === undefined) {
            return value;
        }
        try {
            return JSON.parse(JSON.stringify(value));
        } catch (_) {
            return null;
        }
    }

    function createInitialMindFields(entry) {
        if (entry === 'auto') {
            return {
                appetite: 0.32,
                sleepiness: 0.22,
                energy: 0.66,
                social_need: 0.32,
                stimulation_need: 0.42,
            };
        }
        return {
            appetite: 0.22,
            sleepiness: 0.12,
            energy: 0.75,
            social_need: 0.22,
            stimulation_need: 0.28,
        };
    }

    function createReturnEpisodeAccumulator() {
        return {
            activeChapter: {
                interactionSeen: false,
                activityKinds: [],
            },
            lastRest: null,
        };
    }

    function createInitialRuntimeState() {
        return {
            active: false,
            entry: null,
            tier: 'none',
            enteredAt: 0,
            updatedAt: 0,
            sequence: 0,
            fields: createInitialMindFields(),
            recentEvents: [],
            seenEventKeys: [],
            actionIntentEvidence: {},
            accountedPhysicalActivityIds: [],
            accountedInteractionActivityIds: [],
            interactionGesture: null,
            lastChatMinimizedRect: null,
            lastChatMinimizedState: null,
            lastChatIdleDocked: false,
            returnSummaryDraft: null,
            returnEpisodeAccumulator: createReturnEpisodeAccumulator(),
            lastDecision: null,
            scheduler: {
                queued: false,
                pendingTriggers: [],
                deferredTriggers: [],
                providerRecheckNeeded: false,
                lastEvaluatedAt: 0,
                pendingActionRequest: null,
                activeAction: null,
                actionCooldowns: {},
                postActionSettle: null,
                lastIgnoredActionResult: null,
                lastProtocolFailure: null,
            },
            clock: {
                timer: 0,
                generation: 0,
                lastTickAt: 0,
                lastUserInteractionAt: 0,
                lastActionStartedAt: 0,
            },
            lastResetReason: '',
        };
    }

    function normalizeTier(value) {
        if (value === TIERS.CAT1 || value === TIERS.CAT2 || value === TIERS.CAT3) {
            return value;
        }
        return 'none';
    }

    function isKnownObservationType(type) {
        return Object.keys(OBSERVATION_TYPES).some(function (key) {
            return OBSERVATION_TYPES[key] === type;
        });
    }

    function normalizeRect(rect) {
        if (!rect || typeof rect !== 'object') {
            return null;
        }
        var left = Number(rect.left);
        var top = Number(rect.top);
        var width = Number(rect.width);
        var height = Number(rect.height);
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
        };
    }

    function rectCenterMoveDistance(previousRect, nextRect) {
        var previous = normalizeRect(previousRect);
        var next = normalizeRect(nextRect);
        if (!previous || !next) {
            return Infinity;
        }
        var previousX = previous.left + previous.width / 2;
        var previousY = previous.top + previous.height / 2;
        var nextX = next.left + next.width / 2;
        var nextY = next.top + next.height / 2;
        return Math.hypot(nextX - previousX, nextY - previousY);
    }

    function areRectsEqual(previousRect, nextRect) {
        var previous = normalizeRect(previousRect);
        var next = normalizeRect(nextRect);
        return !!previous && !!next &&
            previous.left === next.left &&
            previous.top === next.top &&
            previous.width === next.width &&
            previous.height === next.height;
    }

    function currentDurationSeconds() {
        if (!runtimeState.enteredAt) {
            return 0;
        }
        return Math.max(0, Math.floor((nowMs() - runtimeState.enteredAt) / 1000));
    }

    function clearAutonomousClock() {
        var clock = runtimeState && runtimeState.clock;
        if (!clock) return;
        if (clock.timer && typeof window.clearInterval === 'function') {
            window.clearInterval(clock.timer);
        }
        clock.timer = 0;
    }

    function emitAutonomousTimeObservations(generation) {
        if (!runtimeState.active) return;
        var clock = runtimeState.clock;
        if (generation && clock.generation !== generation) return;
        var timestamp = nowMs();
        var elapsedMs = Math.max(0, timestamp - (Number(clock.lastTickAt) || timestamp));
        if (!elapsedMs) return;
        clock.lastTickAt = timestamp;
        var interactionAnchor = Number(clock.lastUserInteractionAt) || runtimeState.enteredAt || timestamp;
        var actionAnchor = Number(clock.lastActionStartedAt) || runtimeState.enteredAt || timestamp;
        var detail = {
            elapsedMs: elapsedMs,
            inactiveElapsedMs: Math.max(0, timestamp - interactionAnchor),
            sinceLastActionMs: Math.max(0, timestamp - actionAnchor),
        };
        observe({
            type: OBSERVATION_TYPES.CAT_ELAPSED,
            source: 'cat-mind-clock',
            tier: runtimeState.tier,
            timestamp: timestamp,
            detail: detail,
        });
        observe({
            type: OBSERVATION_TYPES.INACTIVE_ELAPSED,
            source: 'cat-mind-clock',
            tier: runtimeState.tier,
            timestamp: timestamp,
            detail: detail,
        });
        observe({
            type: OBSERVATION_TYPES.SINCE_LAST_ACTION,
            source: 'cat-mind-clock',
            tier: runtimeState.tier,
            timestamp: timestamp,
            detail: detail,
        });
    }

    function startAutonomousClock(timestamp) {
        var clock = runtimeState.clock;
        clearAutonomousClock();
        var startedAt = Number.isFinite(Number(timestamp)) ? Number(timestamp) : nowMs();
        clock.lastTickAt = startedAt;
        clock.lastUserInteractionAt = startedAt;
        clock.lastActionStartedAt = startedAt;
        clock.generation = ++autonomousClockGeneration;
        if (typeof window.setInterval !== 'function') return;
        var generation = clock.generation;
        clock.timer = window.setInterval(function () {
            emitAutonomousTimeObservations(generation);
        }, AUTONOMOUS_TICK_INTERVAL_MS);
    }

    function resetRuntime(reason) {
        clearAutonomousClock();
        clearActionRequestLeaseTimer();
        runtimeState = createInitialRuntimeState();
        runtimeState.lastResetReason = typeof reason === 'string' ? reason : '';
        emitStateChange('reset');
        return getState();
    }

    function dispatchRuntimeEvent(name, detail) {
        if (!name || typeof window.dispatchEvent !== 'function' || typeof CustomEvent !== 'function') {
            return false;
        }
        try {
            window.dispatchEvent(new CustomEvent(name, { detail: detail }));
            return true;
        } catch (_) {
            return false;
        }
    }

    function getDebugSnapshot() {
        var snapshotAt = nowMs();
        return {
            state: getState(snapshotAt),
            recentEvents: getRecentEvents(),
            returnEpisode: getReturnEpisodeDebugSnapshot(),
            lastDecision: clonePlain(runtimeState.lastDecision),
            scheduler: getSchedulerSnapshot(snapshotAt),
            clock: {
                tickIntervalMs: AUTONOMOUS_TICK_INTERVAL_MS,
                lastTickAt: runtimeState.clock.lastTickAt,
                lastUserInteractionAt: runtimeState.clock.lastUserInteractionAt,
                lastActionStartedAt: runtimeState.clock.lastActionStartedAt,
            },
            actionIntentEvidence: getActionIntentSnapshot(snapshotAt),
            actionScores: getActionScoreSnapshot(snapshotAt, { prune: false }),
            debugEnabled: isDebugEnabled(),
        };
    }

    function getActiveActionCooldownSnapshot(timestamp) {
        var result = {};
        Object.keys(runtimeState.scheduler.actionCooldowns).forEach(function (actionId) {
            var cooldown = getActionCooldown(actionId, timestamp, { prune: false });
            if (cooldown.active) {
                result[actionId] = clonePlain(runtimeState.scheduler.actionCooldowns[actionId]);
            }
        });
        return result;
    }

    function getSchedulerSnapshot(timestamp) {
        var snapshot = clonePlain(runtimeState.scheduler) || {};
        snapshot.actionCooldowns = getActiveActionCooldownSnapshot(timestamp);
        return snapshot;
    }

    function emitStateChange(reason, detail) {
        // State-change carries a complete debug snapshot and is only consumed by
        // the opt-in inspector. Keep Cat Mind's runtime independent from it.
        if (!isDebugEnabled()) return false;
        return dispatchRuntimeEvent(EVENT_NAMES.STATE_CHANGE, {
            reason: typeof reason === 'string' ? reason : 'state-update',
            timestamp: nowMs(),
            detail: clonePlain(detail) || {},
            snapshot: getDebugSnapshot(),
        });
    }

    function normalizeDebugDecision(decision) {
        if (!decision || typeof decision !== 'object') {
            return null;
        }
        var candidates = Array.isArray(decision.candidates) ? decision.candidates : [];
        return {
            trigger: typeof decision.trigger === 'string' ? decision.trigger : '',
            triggerTypes: Array.isArray(decision.triggerTypes)
                ? decision.triggerTypes.slice(0, 12).filter(function (type) { return typeof type === 'string'; })
                : [],
            outcome: typeof decision.outcome === 'string' ? decision.outcome : '',
            reason: typeof decision.reason === 'string' ? decision.reason : '',
            timestamp: Number.isFinite(Number(decision.timestamp)) ? Number(decision.timestamp) : nowMs(),
            candidates: candidates.slice(0, 12).map(function (candidate) {
                var item = candidate && typeof candidate === 'object' ? candidate : {};
                var score = item.score === null || item.score === undefined ? null : Number(item.score);
                var baseScore = item.baseScore === null || item.baseScore === undefined ? null : Number(item.baseScore);
                var threshold = item.threshold === null || item.threshold === undefined ? null : Number(item.threshold);
                var needSurplus = item.needSurplus === null || item.needSurplus === undefined
                    ? null
                    : Number(item.needSurplus);
                var needContribution = item.needContribution === null || item.needContribution === undefined
                    ? null
                    : Number(item.needContribution);
                var cadenceAdjustment = item.cadenceAdjustment === null || item.cadenceAdjustment === undefined
                    ? null
                    : Number(item.cadenceAdjustment);
                var intentLevel = item.intentLevel === null || item.intentLevel === undefined
                    ? null
                    : Number(item.intentLevel);
                var intentContribution = item.intentContribution === null || item.intentContribution === undefined
                    ? null
                    : Number(item.intentContribution);
                var intentCurveFactor = item.intentCurveFactor === null || item.intentCurveFactor === undefined
                    ? null
                    : Number(item.intentCurveFactor);
                var utilityScore = item.utilityScore === null || item.utilityScore === undefined
                    ? null
                    : Number(item.utilityScore);
                var cooldownPenalty = item.cooldownPenalty === null || item.cooldownPenalty === undefined
                    ? null
                    : Number(item.cooldownPenalty);
                var cooldownRemainingMs = item.cooldownRemainingMs === null || item.cooldownRemainingMs === undefined
                    ? null
                    : Number(item.cooldownRemainingMs);
                var cooldownRecoveryFactor = item.cooldownRecoveryFactor === null || item.cooldownRecoveryFactor === undefined
                    ? null
                    : Number(item.cooldownRecoveryFactor);
                var cadenceCurveProgress = item.cadenceCurveProgress === null || item.cadenceCurveProgress === undefined
                    ? null
                    : Number(item.cadenceCurveProgress);
                var cadenceCurveFactor = item.cadenceCurveFactor === null || item.cadenceCurveFactor === undefined
                    ? null
                    : Number(item.cadenceCurveFactor);
                var cooldownCurveFactor = item.cooldownCurveFactor === null || item.cooldownCurveFactor === undefined
                    ? null
                    : Number(item.cooldownCurveFactor);
                return {
                    actionId: typeof item.actionId === 'string' ? item.actionId : '',
                    score: Number.isFinite(score) ? score : null,
                    baseScore: Number.isFinite(baseScore) ? baseScore : null,
                    threshold: Number.isFinite(threshold) ? threshold : null,
                    needSurplus: Number.isFinite(needSurplus) ? needSurplus : null,
                    needContribution: Number.isFinite(needContribution) ? needContribution : null,
                    cadenceAdjustment: Number.isFinite(cadenceAdjustment) ? cadenceAdjustment : null,
                    cadenceCurveProgress: Number.isFinite(cadenceCurveProgress) ? cadenceCurveProgress : null,
                    cadenceCurveFactor: Number.isFinite(cadenceCurveFactor) ? cadenceCurveFactor : null,
                    intentLevel: Number.isFinite(intentLevel) ? intentLevel : null,
                    intentContribution: Number.isFinite(intentContribution) ? intentContribution : null,
                    intentCurveFactor: Number.isFinite(intentCurveFactor) ? intentCurveFactor : null,
                    intentReason: typeof item.intentReason === 'string' ? item.intentReason : '',
                    utilityScore: Number.isFinite(utilityScore) ? utilityScore : null,
                    cooldownApplied: item.cooldownApplied === true,
                    cooldownPenalty: Number.isFinite(cooldownPenalty) ? cooldownPenalty : null,
                    cooldownRemainingMs: Number.isFinite(cooldownRemainingMs) ? cooldownRemainingMs : null,
                    cooldownRecoveryFactor: Number.isFinite(cooldownRecoveryFactor) ? cooldownRecoveryFactor : null,
                    cooldownCurveFactor: Number.isFinite(cooldownCurveFactor) ? cooldownCurveFactor : null,
                    allowed: item.allowed === true,
                    reason: typeof item.reason === 'string' ? item.reason : '',
                    providerDetail: sanitizeDetail(item.providerDetail),
                };
            }),
            request: normalizeActionRequestForDebug(decision.request),
            execution: normalizeActionExecutionForDebug(decision.execution),
        };
    }

    function normalizeActionRequestForDebug(request) {
        if (!request || typeof request !== 'object') return null;
        return {
            requestId: typeof request.requestId === 'string' ? request.requestId : '',
            actionId: typeof request.actionId === 'string' ? request.actionId : '',
            source: typeof request.source === 'string' ? request.source : '',
            tier: normalizeTier(request.tier),
            timestamp: Number.isFinite(Number(request.timestamp)) ? Number(request.timestamp) : 0,
            runId: typeof request.runId === 'string' ? request.runId : '',
        };
    }

    function normalizeActionExecutionForDebug(execution) {
        if (!execution || typeof execution !== 'object') return null;
        return {
            state: typeof execution.state === 'string' ? execution.state : '',
            reason: typeof execution.reason === 'string' ? execution.reason : '',
            requestId: typeof execution.requestId === 'string' ? execution.requestId : '',
            actionId: typeof execution.actionId === 'string' ? execution.actionId : '',
            runId: typeof execution.runId === 'string' ? execution.runId : '',
            timestamp: Number.isFinite(Number(execution.timestamp)) ? Number(execution.timestamp) : 0,
        };
    }

    function recordDecision(decision) {
        runtimeState.lastDecision = normalizeDebugDecision(decision);
        emitStateChange('decision', { decision: runtimeState.lastDecision });
        return clonePlain(runtimeState.lastDecision);
    }

    function updateDecisionExecution(execution) {
        if (!runtimeState.lastDecision) return;
        runtimeState.lastDecision.execution = normalizeActionExecutionForDebug(execution);
    }

    function createActionRequest(actionId, candidate, triggerTypes, timestamp) {
        var requestId = [
            'cat-mind',
            actionId,
            runtimeState.sequence + 1,
            timestamp,
        ].join(':');
        return Object.freeze({
            requestId: requestId,
            actionId: actionId,
            source: 'cat_mind',
            tier: runtimeState.tier,
            timestamp: timestamp,
            detail: Object.freeze({
                triggerTypes: Array.isArray(triggerTypes) ? triggerTypes.slice(0, 12) : [],
                score: candidate && Number.isFinite(Number(candidate.score)) ? Number(candidate.score) : null,
            }),
        });
    }

    function clearActionRequestLeaseTimer() {
        actionRequestLeaseGeneration += 1;
        if (!actionRequestLeaseTimer) return;
        if (typeof window.clearTimeout === 'function') {
            window.clearTimeout(actionRequestLeaseTimer);
        }
        actionRequestLeaseTimer = 0;
    }

    function getActionRequestLeaseDeadline(pending) {
        if (!pending || typeof pending !== 'object') return null;
        var acceptedAt = Number(pending.acceptedAt);
        var requestedAt = Number(pending.timestamp);
        var accepted = Number.isFinite(acceptedAt) && acceptedAt > 0;
        var anchor = accepted ? acceptedAt : requestedAt;
        if (!Number.isFinite(anchor)) return null;
        return anchor + (accepted ? ACTION_ACCEPTED_START_LEASE_MS : ACTION_REQUEST_LEASE_MS);
    }

    function armActionRequestLeaseTimer(pending) {
        clearActionRequestLeaseTimer();
        if (!runtimeState.active || !pending ||
            typeof window.setTimeout !== 'function' ||
            typeof window.clearTimeout !== 'function') {
            return false;
        }
        var deadline = getActionRequestLeaseDeadline(pending);
        if (!Number.isFinite(deadline)) return false;
        var requestId = pending.requestId;
        var runId = pending.runId || '';
        var generation = actionRequestLeaseGeneration;
        actionRequestLeaseTimer = window.setTimeout(function () {
            if (generation !== actionRequestLeaseGeneration) return;
            actionRequestLeaseTimer = 0;
            var current = runtimeState.scheduler.pendingActionRequest;
            if (!runtimeState.active || !current ||
                current.requestId !== requestId ||
                (current.runId || '') !== runId) {
                return;
            }
            var timestamp = nowMs();
            if (timestamp < deadline) return;
            if (expireStaleActionRequest(timestamp)) {
                // Only retained input/provider triggers are reconsidered here.
                // An expired request must not become a self-retrying 5s loop.
                queueDeferredDecisionIfNeeded();
            }
        }, Math.max(0, deadline - nowMs()));
        return true;
    }

    function acknowledgeActionRequest(detail) {
        var response = detail && typeof detail === 'object' ? detail : {};
        var pending = runtimeState.scheduler.pendingActionRequest;
        if (!pending ||
            response.requestId !== pending.requestId ||
            response.actionId !== pending.actionId) {
            return false;
        }
        var status = typeof response.status === 'string' ? response.status : '';
        var timestamp = Number(response.timestamp);
        if (!Number.isFinite(timestamp)) timestamp = nowMs();
        var reason = typeof response.reason === 'string' && response.reason
            ? response.reason
            : status;
        var runId = typeof response.runId === 'string' ? response.runId : '';
        if (status === 'accepted') {
            if (!runId || (pending.runId && pending.runId !== runId)) return false;
            if (pending.runId === runId) return true;
            runtimeState.scheduler.pendingActionRequest = Object.assign({}, pending, {
                runId: runId,
                acceptedAt: timestamp,
            });
            armActionRequestLeaseTimer(runtimeState.scheduler.pendingActionRequest);
            updateDecisionExecution({
                state: 'accepted',
                reason: reason || 'runner_accepted',
                requestId: pending.requestId,
                actionId: pending.actionId,
                runId: runId,
                timestamp: timestamp,
            });
            emitStateChange('action-request-accepted', { actionId: pending.actionId, requestId: pending.requestId });
            return true;
        }
        if (status === 'started') {
            if (!runId || pending.runId !== runId) return false;
            var scoreConfig = getActionScoreConfig(pending.actionId);
            clearActionRequestLeaseTimer();
            runtimeState.scheduler.pendingActionRequest = null;
            runtimeState.scheduler.activeAction = {
                requestId: pending.requestId,
                actionId: pending.actionId,
                source: pending.source,
                tier: pending.tier,
                startedAt: timestamp,
                runId: runId,
            };
            runtimeState.scheduler.actionCooldowns[pending.actionId] = {
                startedAt: timestamp,
                requestId: pending.requestId,
                fullCooldownMs: scoreConfig ? scoreConfig.cooldownMs : 0,
            };
            runtimeState.clock.lastActionStartedAt = timestamp;
            // Intent is consumed only after the existing runner proves it
            // really started. accepted/rejected/provider dry-run never spend it.
            clearActionIntentEvidence(pending.actionId);
            if (!hasFreshActionIntent(timestamp)) {
                runtimeState.scheduler.providerRecheckNeeded = false;
            }
            updateDecisionExecution({
                state: 'started',
                reason: reason || 'runner_started',
                requestId: pending.requestId,
                actionId: pending.actionId,
                runId: runId,
                timestamp: timestamp,
            });
            emitStateChange('action-started', { actionId: pending.actionId, requestId: pending.requestId });
            return true;
        }
        if (status === 'rejected') {
            if (pending.runId) return false;
            clearActionRequestLeaseTimer();
            runtimeState.scheduler.pendingActionRequest = null;
            updateDecisionExecution({
                state: 'rejected',
                reason: reason || 'adapter_rejected',
                requestId: pending.requestId,
                actionId: pending.actionId,
                timestamp: timestamp,
            });
            emitStateChange('action-request-rejected', { actionId: pending.actionId, requestId: pending.requestId });
            return true;
        }
        return false;
    }

    function settleActionLifecycleFromResult(detail) {
        var result = detail && typeof detail === 'object' ? detail : {};
        var actionId = typeof result.actionId === 'string' ? result.actionId : '';
        var requestId = result.detail && typeof result.detail.requestId === 'string'
            ? result.detail.requestId
            : '';
        var runId = result.detail && typeof result.detail.runId === 'string'
            ? result.detail.runId
            : '';
        var isCatMindRun = result.source === 'cat_mind';
        var isTerminal = result.result === 'done' ||
            result.result === 'failed' ||
            result.result === 'cancelled' ||
            result.result === 'interrupted';
        // Legacy presentation may still emit its own completion signals. Cat Mind
        // only settles a lifecycle it started itself; anything else is debug-only.
        if (!isCatMindRun) return { settled: false, applyResult: false };
        if (!isTerminal) return { settled: false, applyResult: false };
        var timestamp = Number(result.timestamp);
        if (!Number.isFinite(timestamp)) timestamp = nowMs();
        var scheduler = runtimeState.scheduler;
        var pending = scheduler.pendingActionRequest;
        var active = scheduler.activeAction;
        if (pending &&
            pending.actionId === actionId &&
            pending.requestId === requestId &&
            pending.runId &&
            pending.runId === runId) {
            clearActionRequestLeaseTimer();
            scheduler.pendingActionRequest = null;
            scheduler.lastProtocolFailure = {
                type: 'result_before_started',
                requestId: pending.requestId,
                actionId: pending.actionId,
                runId: pending.runId,
                result: result.result,
                timestamp: timestamp,
            };
            updateDecisionExecution({
                state: 'result_before_started',
                reason: typeof result.reason === 'string' ? result.reason : 'runner_result',
                requestId: pending.requestId,
                actionId: pending.actionId,
                runId: pending.runId,
                timestamp: timestamp,
            });
            emitStateChange('action-protocol-failure', clonePlain(scheduler.lastProtocolFailure));
            return { settled: true, applyResult: false };
        }
        if (active &&
            active.actionId === actionId &&
            active.requestId === requestId &&
            active.runId === runId) {
            scheduler.activeAction = null;
            scheduler.postActionSettle = {
                requestId: active.requestId,
                actionId: active.actionId,
                runId: active.runId,
            };
            updateDecisionExecution({
                state: 'result',
                reason: typeof result.reason === 'string' ? result.reason : 'runner_result',
                requestId: active.requestId,
                actionId: active.actionId,
                runId: active.runId,
                timestamp: timestamp,
            });
            return { settled: true, applyResult: true };
        }
        return { settled: false, applyResult: false };
    }

    function getActionScoreConfig(actionId) {
        return ACTION_SCORE_CONFIG[actionId] || null;
    }

    function getActionTieBreakIndex(actionId) {
        var index = ACTION_TIE_BREAK.indexOf(actionId);
        return index === -1 ? ACTION_TIE_BREAK.length : index;
    }

    function smoothstep01(value) {
        var normalized = Math.max(0, Math.min(1, Number(value) || 0));
        return normalized * normalized * (3 - 2 * normalized);
    }

    function getDecayedActionIntentLevel(record, timestamp) {
        if (!record || typeof record !== 'object') return 0;
        var level = clamp01(record.level);
        var updatedAt = Number(record.updatedAt);
        if (!level || !Number.isFinite(updatedAt)) return 0;
        var ageMs = Math.max(0, (Number(timestamp) || nowMs()) - updatedAt);
        if (ageMs <= ACTION_INTENT_POLICY.freshHoldMs) return level;
        var decayMs = ageMs - ACTION_INTENT_POLICY.freshHoldMs;
        return level * Math.pow(0.5, decayMs / ACTION_INTENT_POLICY.halfLifeMs);
    }

    function addActionIntentEvidence(actionId, strength, timestamp, reason) {
        if (!getActionScoreConfig(actionId)) return 0;
        var evidenceAt = Number.isFinite(Number(timestamp)) ? Number(timestamp) : nowMs();
        var current = getDecayedActionIntentLevel(runtimeState.actionIntentEvidence[actionId], evidenceAt);
        var normalizedStrength = clamp01(strength);
        if (!normalizedStrength) return current;
        var next = current + normalizedStrength * (1 - current);
        runtimeState.actionIntentEvidence[actionId] = {
            level: next,
            updatedAt: evidenceAt,
            reason: typeof reason === 'string' ? reason : '',
        };
        return next;
    }

    function clearActionIntentEvidence(actionId) {
        if (!runtimeState.actionIntentEvidence || typeof runtimeState.actionIntentEvidence !== 'object') {
            runtimeState.actionIntentEvidence = {};
            return;
        }
        delete runtimeState.actionIntentEvidence[actionId];
    }

    function getActionIntentContribution(actionId, timestamp) {
        var record = runtimeState.actionIntentEvidence && runtimeState.actionIntentEvidence[actionId];
        var level = getDecayedActionIntentLevel(record, timestamp);
        if (level < 0.0001) level = 0;
        var curveFactor = smoothstep01(level);
        return {
            level: level,
            contribution: ACTION_INTENT_POLICY.maxContribution * curveFactor,
            curveFactor: curveFactor,
            updatedAt: record && Number.isFinite(Number(record.updatedAt)) ? Number(record.updatedAt) : 0,
            reason: record && typeof record.reason === 'string' ? record.reason : '',
        };
    }

    function getActionIntentSnapshot(timestamp) {
        var snapshot = {};
        [
            ACTION_IDS.CAT1_SOCIAL_PING,
            ACTION_IDS.CAT1_EAT_SNACK,
            ACTION_IDS.CAT1_SMALL_MOVE,
            ACTION_IDS.CAT1_PLAY_YARN,
            ACTION_IDS.CAT2_NAP_FEEDBACK,
            ACTION_IDS.CAT3_SLEEP_FEEDBACK,
        ].forEach(function (actionId) {
            var intent = getActionIntentContribution(actionId, timestamp);
            if (!intent.level) return;
            snapshot[actionId] = {
                level: Math.round(intent.level * 10000) / 10000,
                contribution: Math.round(intent.contribution * 100) / 100,
                updatedAt: intent.updatedAt,
                reason: intent.reason,
            };
        });
        return snapshot;
    }

    function getYarnOfferIntentStrength(observation) {
        var detail = observation && observation.detail && typeof observation.detail === 'object'
            ? observation.detail
            : {};
        if (detail.userInitiated !== true || detail.endedNearCat !== true) return 0;
        var startDistance = Number(detail.startDistanceToCatPx);
        var endDistance = Number(detail.endDistanceToCatPx);
        var directApproachDistance = Number(detail.directApproachDistancePx);
        if (!Number.isFinite(directApproachDistance) &&
            Number.isFinite(startDistance) && Number.isFinite(endDistance)) {
            directApproachDistance = startDistance - endDistance;
        }
        var pathDistance = Number(detail.pathDistancePx);
        var movementThreshold = Math.max(1, Number(detail.movementThresholdPx) || CHAT_MOVED_FAR_DISTANCE_PX);
        if (detail.startedFarFromCat === true &&
            Number.isFinite(directApproachDistance) && directApproachDistance >= movementThreshold) {
            return ACTION_INTENT_POLICY.yarnOfferStrongStrength;
        }
        // A meaningful near-to-near offer is weaker on its own, but repeated
        // offers saturate naturally through the same evidence merge curve.
        if ((Number.isFinite(directApproachDistance) && directApproachDistance >= movementThreshold) ||
            (Number.isFinite(pathDistance) && pathDistance >= movementThreshold)) {
            return ACTION_INTENT_POLICY.yarnOfferNearStrength;
        }
        return 0;
    }

    function applyActionIntentObservation(observation) {
        if (!observation || !observation.type) return;
        if (observation.type === OBSERVATION_TYPES.CAT1_LOCAL_PLAY_DONE) {
            // The existing 25% journey-local runner can genuinely fulfil the
            // same explicit invitation. Consume only the evidence: this local
            // tail still writes no Cat Mind cooldown or return-episode action.
            clearActionIntentEvidence(ACTION_IDS.CAT1_PLAY_YARN);
            runtimeState.scheduler.providerRecheckNeeded = false;
            return;
        }
        if (observation.type !== OBSERVATION_TYPES.CHAT_YARN_DRAG_COMPLETED) return;
        var strength = getYarnOfferIntentStrength(observation);
        if (strength) {
            addActionIntentEvidence(
                ACTION_IDS.CAT1_PLAY_YARN,
                strength,
                observation.timestamp,
                strength === ACTION_INTENT_POLICY.yarnOfferStrongStrength
                    ? 'yarn_offer_far_to_near'
                    : 'yarn_offer_near'
            );
            return;
        }
        var detail = observation.detail || {};
        var startDistance = Number(detail.startDistanceToCatPx);
        var endDistance = Number(detail.endDistanceToCatPx);
        var movementThreshold = Math.max(1, Number(detail.movementThresholdPx) || CHAT_MOVED_FAR_DISTANCE_PX);
        if (detail.userInitiated === true && Number.isFinite(startDistance) && Number.isFinite(endDistance) &&
            endDistance - startDistance >= movementThreshold) {
            clearActionIntentEvidence(ACTION_IDS.CAT1_PLAY_YARN);
        }
    }

    function getNeedContribution(needSurplus) {
        var negativeRange = ACTION_SCORE_POLICY.negativeNeedCurveRange;
        if (needSurplus < 0) {
            if (needSurplus <= -negativeRange) return needSurplus;
            return -negativeRange * smoothstep01(Math.abs(needSurplus) / negativeRange);
        }
        var positiveRange = ACTION_SCORE_POLICY.positiveNeedCurveRange;
        if (needSurplus <= positiveRange) {
            return ACTION_SCORE_POLICY.positiveNeedCurveCeiling * smoothstep01(
                needSurplus / positiveRange
            );
        }
        // Preserve the responsive 0..+8 curve, then keep a shallow asymptotic
        // tail instead of flattening every highly-needed action to the same
        // score. This restores state-driven ordering during dense interaction.
        var tailSurplus = needSurplus - positiveRange;
        return ACTION_SCORE_POLICY.positiveNeedCurveCeiling +
            ACTION_SCORE_POLICY.positiveNeedCurveTailCeiling *
            (1 - Math.exp(-tailSurplus / ACTION_SCORE_POLICY.positiveNeedCurveTailScale));
    }

    function getActionCooldown(actionId, timestamp, options) {
        var shouldPrune = !(options && options.prune === false);
        var config = getActionScoreConfig(actionId);
        var cooldown = runtimeState.scheduler.actionCooldowns[actionId];
        if (!config || !cooldown) {
            return { active: false, penalty: 0, remainingMs: 0, recoveryFactor: 0, curveFactor: 0 };
        }
        var startedAt = Number(cooldown.startedAt);
        var fullCooldownMs = Number(cooldown.fullCooldownMs) || config.cooldownMs;
        if (!Number.isFinite(startedAt) || !Number.isFinite(fullCooldownMs) || fullCooldownMs <= 0) {
            if (shouldPrune) delete runtimeState.scheduler.actionCooldowns[actionId];
            return { active: false, penalty: 0, remainingMs: 0, recoveryFactor: 0, curveFactor: 0 };
        }
        var elapsedMs = Math.max(0, (Number(timestamp) || nowMs()) - startedAt);
        var remainingMs = Math.max(0, fullCooldownMs - elapsedMs);
        if (!remainingMs) {
            if (shouldPrune) delete runtimeState.scheduler.actionCooldowns[actionId];
            return { active: false, penalty: 0, remainingMs: 0, recoveryFactor: 0, curveFactor: 0 };
        }
        var recoveryFactor = Math.min(1, remainingMs / fullCooldownMs);
        var elapsedProgress = 1 - recoveryFactor;
        var curveFactor = 1 - Math.pow(elapsedProgress, ACTION_SCORE_POLICY.cooldownRecoveryExponent);
        return {
            active: true,
            penalty: ACTION_SCORE_POLICY.cooldownPenaltyPoints *
                ACTION_SCORE_POLICY.cooldownMultiplier * curveFactor,
            remainingMs: remainingMs,
            recoveryFactor: recoveryFactor,
            curveFactor: curveFactor,
        };
    }

    function pruneExpiredActionCooldowns(timestamp) {
        Object.keys(runtimeState.scheduler.actionCooldowns).forEach(function (actionId) {
            getActionCooldown(actionId, timestamp);
        });
    }

    function isActionTierAllowed(actionId, tier) {
        if (tier === TIERS.CAT1) {
            return actionId === ACTION_IDS.CAT1_SOCIAL_PING ||
                actionId === ACTION_IDS.CAT1_EAT_SNACK ||
                actionId === ACTION_IDS.CAT1_SMALL_MOVE ||
                actionId === ACTION_IDS.CAT1_PLAY_YARN;
        }
        if (tier === TIERS.CAT2) return actionId === ACTION_IDS.CAT2_NAP_FEEDBACK;
        if (tier === TIERS.CAT3) return actionId === ACTION_IDS.CAT3_SLEEP_FEEDBACK;
        return false;
    }

    function getHardGateReason(gates) {
        if (!gates || typeof gates !== 'object') return 'runtime_gate_unavailable';
        if (gates.returnPending) return 'return_pending';
        if (gates.dragPending) return 'drag_pending';
        if (gates.dragging) return 'dragging';
        if (gates.transitionActive) return 'transition_active';
        if (gates.activeIndependentAction) return 'active_independent_action';
        if (!gates.returnBallVisible) return 'return_ball_not_visible';
        if (!gates.validCatRuntime) return 'invalid_cat_runtime';
        if (gates.chatSurfaceDragging) return 'chat_surface_dragging';
        if (gates.yarnDragActive) return 'chat_yarn_dragging';
        if (gates.yarnSettling) return 'chat_yarn_settling';
        if (gates.edgePeekActive) return 'edge_peek_active';
        return '';
    }

    function getCadenceScore(timestamp) {
        var scoreAt = Number(timestamp) || nowMs();
        var anchor = Number(runtimeState.clock.lastActionStartedAt) || runtimeState.enteredAt || scoreAt;
        var elapsedMs = Math.max(0, scoreAt - anchor);
        var progress = Math.min(1, elapsedMs / ACTION_SCORE_POLICY.cadenceRecoveryMs);
        var curveFactor = smoothstep01(progress);
        var adjustment = ACTION_SCORE_POLICY.cadenceFloor +
            (ACTION_SCORE_POLICY.cadenceCeiling - ACTION_SCORE_POLICY.cadenceFloor) * curveFactor;
        return {
            elapsedMs: elapsedMs,
            progress: progress,
            curveFactor: curveFactor,
            adjustment: adjustment,
        };
    }

    function directionalActionScore(actionId, timestamp, cooldownOptions) {
        var fields = runtimeState.fields;
        var config = getActionScoreConfig(actionId);
        var baseScore = 0;
        if (actionId === ACTION_IDS.CAT1_SOCIAL_PING) {
            // Social need remains the driver. Stimulation is only a small nudge:
            // otherwise the time flow that should lead into movement/play keeps
            // selecting the same vocal response again.
            baseScore = 12 + fields.social_need * 55 + fields.stimulation_need * 8 + (1 - fields.sleepiness) * 4;
        } else if (actionId === ACTION_IDS.CAT1_SMALL_MOVE) {
            baseScore = 10 + fields.stimulation_need * 40 + fields.energy * 34 + fields.social_need * 6 -
                fields.sleepiness * 12 - fields.appetite * 5;
        } else if (actionId === ACTION_IDS.CAT1_PLAY_YARN) {
            baseScore = 5 + fields.stimulation_need * 48 + fields.energy * 36 + fields.social_need * 12 -
                fields.sleepiness * 18 - fields.appetite * 8;
        } else if (actionId === ACTION_IDS.CAT1_EAT_SNACK) {
            baseScore = 18 + fields.appetite * 55 + (1 - fields.energy) * 6 + fields.sleepiness * 4 +
                fields.social_need * 6 - fields.stimulation_need * 5;
        } else {
            baseScore = 12 + fields.sleepiness * 55 + (1 - fields.energy) * 22 - fields.stimulation_need * 8 +
                fields.appetite * 3 - fields.social_need * 4;
            baseScore += actionId === ACTION_IDS.CAT3_SLEEP_FEEDBACK ? 20 : 8;
        }
        var threshold = config ? config.threshold : Infinity;
        var needSurplus = baseScore - threshold;
        var needContribution = getNeedContribution(needSurplus);
        var cadence = getCadenceScore(timestamp);
        var cooldown = getActionCooldown(actionId, timestamp, cooldownOptions);
        var intent = getActionIntentContribution(actionId, timestamp);
        var intentCooldownRelief = ACTION_INTENT_POLICY.cooldownPenaltyRelief * intent.curveFactor;
        var cooldownPenalty = cooldown.penalty * (1 - intentCooldownRelief);
        var utilityScore = needContribution + cadence.adjustment + intent.contribution - cooldownPenalty;
        return {
            baseScore: baseScore,
            score: threshold + utilityScore,
            threshold: threshold,
            needSurplus: needSurplus,
            needContribution: needContribution,
            cadenceAdjustment: cadence.adjustment,
            cadenceElapsedMs: cadence.elapsedMs,
            cadenceCurveProgress: cadence.progress,
            cadenceCurveFactor: cadence.curveFactor,
            intentLevel: intent.level,
            intentContribution: intent.contribution,
            intentCurveFactor: intent.curveFactor,
            intentReason: intent.reason,
            utilityScore: utilityScore,
            cooldownApplied: cooldown.active,
            cooldownPenalty: cooldownPenalty,
            cooldownBasePenalty: cooldown.penalty,
            intentCooldownRelief: intentCooldownRelief,
            cooldownRemainingMs: cooldown.remainingMs,
            cooldownRecoveryFactor: cooldown.recoveryFactor,
            cooldownCurveFactor: cooldown.curveFactor,
        };
    }

    function getActionScoreSnapshot(timestamp, cooldownOptions) {
        var snapshotAt = Number.isFinite(Number(timestamp)) ? Number(timestamp) : nowMs();
        return [
            ACTION_IDS.CAT1_SOCIAL_PING,
            ACTION_IDS.CAT1_EAT_SNACK,
            ACTION_IDS.CAT1_SMALL_MOVE,
            ACTION_IDS.CAT1_PLAY_YARN,
            ACTION_IDS.CAT2_NAP_FEEDBACK,
            ACTION_IDS.CAT3_SLEEP_FEEDBACK,
        ].map(function (actionId) {
            var scoring = directionalActionScore(actionId, snapshotAt, cooldownOptions);
            return {
                actionId: actionId,
                baseScore: Math.round(scoring.baseScore * 100) / 100,
                score: Math.round(scoring.score * 100) / 100,
                threshold: scoring.threshold,
                needSurplus: Math.round(scoring.needSurplus * 100) / 100,
                needContribution: Math.round(scoring.needContribution * 100) / 100,
                cadenceAdjustment: Math.round(scoring.cadenceAdjustment * 100) / 100,
                cadenceElapsedMs: Math.round(scoring.cadenceElapsedMs),
                cadenceCurveProgress: Math.round(scoring.cadenceCurveProgress * 10000) / 10000,
                cadenceCurveFactor: Math.round(scoring.cadenceCurveFactor * 10000) / 10000,
                intentLevel: Math.round(scoring.intentLevel * 10000) / 10000,
                intentContribution: Math.round(scoring.intentContribution * 100) / 100,
                intentCurveFactor: Math.round(scoring.intentCurveFactor * 10000) / 10000,
                intentReason: scoring.intentReason,
                utilityScore: Math.round(scoring.utilityScore * 100) / 100,
                cooldownApplied: scoring.cooldownApplied,
                cooldownPenalty: Math.round(scoring.cooldownPenalty * 100) / 100,
                cooldownRemainingMs: Math.round(scoring.cooldownRemainingMs),
                cooldownRecoveryFactor: Math.round(scoring.cooldownRecoveryFactor * 10000) / 10000,
                cooldownCurveFactor: Math.round(scoring.cooldownCurveFactor * 10000) / 10000,
            };
        });
    }

    function hasFreshActionIntent(timestamp) {
        if (!runtimeState.actionIntentEvidence || typeof runtimeState.actionIntentEvidence !== 'object') {
            return false;
        }
        return Object.keys(runtimeState.actionIntentEvidence).some(function (actionId) {
            return getDecayedActionIntentLevel(runtimeState.actionIntentEvidence[actionId], timestamp) >= 0.05;
        });
    }

    function appendUniqueTrigger(target, type) {
        if (!Array.isArray(target) || typeof type !== 'string' || !type) return;
        if (target.indexOf(type) === -1) target.push(type);
    }

    function deferUserDecisionTriggers(triggerTypes) {
        var scheduler = runtimeState.scheduler;
        (Array.isArray(triggerTypes) ? triggerTypes : []).forEach(function (type) {
            if (isUserInteractionObservationType(type)) {
                appendUniqueTrigger(scheduler.deferredTriggers, type);
            }
        });
    }

    function queueDeferredDecisionIfNeeded() {
        if (!runtimeState.active || typeof window.setTimeout !== 'function') return false;
        var scheduler = runtimeState.scheduler;
        var deferred = scheduler.deferredTriggers.slice();
        scheduler.deferredTriggers = [];
        if (scheduler.providerRecheckNeeded && hasFreshActionIntent(nowMs())) {
            appendUniqueTrigger(deferred, 'provider_availability_changed');
        }
        if (!deferred.length) return false;
        deferred.forEach(function (type) {
            appendUniqueTrigger(scheduler.pendingTriggers, type);
        });
        if (scheduler.queued) return true;
        scheduler.queued = true;
        window.setTimeout(evaluateQueuedDecision, 0);
        return true;
    }

    function expireStaleActionRequest(timestamp) {
        var scheduler = runtimeState.scheduler;
        var pending = scheduler.pendingActionRequest;
        if (!pending) return false;
        var acceptedAt = Number(pending.acceptedAt);
        var requestedAt = Number(pending.timestamp);
        var anchor = Number.isFinite(acceptedAt) && acceptedAt > 0 ? acceptedAt : requestedAt;
        if (!Number.isFinite(anchor)) anchor = timestamp;
        var leaseMs = Number.isFinite(acceptedAt) && acceptedAt > 0
            ? ACTION_ACCEPTED_START_LEASE_MS
            : ACTION_REQUEST_LEASE_MS;
        if (timestamp - anchor < leaseMs) return false;
        clearActionRequestLeaseTimer();
        scheduler.pendingActionRequest = null;
        scheduler.lastProtocolFailure = {
            type: Number.isFinite(acceptedAt) && acceptedAt > 0
                ? 'accepted_not_started_timeout'
                : 'request_unacknowledged_timeout',
            requestId: pending.requestId,
            actionId: pending.actionId,
            runId: pending.runId || '',
            timestamp: timestamp,
        };
        updateDecisionExecution({
            state: 'protocol_timeout',
            reason: scheduler.lastProtocolFailure.type,
            requestId: pending.requestId,
            actionId: pending.actionId,
            runId: pending.runId || '',
            timestamp: timestamp,
        });
        emitStateChange('action-protocol-failure', clonePlain(scheduler.lastProtocolFailure));
        return true;
    }

    function evaluateQueuedDecision() {
        var scheduler = runtimeState.scheduler;
        var triggerTypes = scheduler.pendingTriggers.slice();
        scheduler.pendingTriggers = [];
        scheduler.queued = false;
        scheduler.lastEvaluatedAt = nowMs();
        pruneExpiredActionCooldowns(scheduler.lastEvaluatedAt);
        if (!runtimeState.active) {
            return;
        }

        expireStaleActionRequest(scheduler.lastEvaluatedAt);
        if (scheduler.providerRecheckNeeded && !hasFreshActionIntent(scheduler.lastEvaluatedAt)) {
            scheduler.providerRecheckNeeded = false;
        }

        if (scheduler.pendingActionRequest) {
            deferUserDecisionTriggers(triggerTypes);
            recordDecision({
                trigger: 'queued',
                triggerTypes: triggerTypes,
                outcome: ACTION_IDS.QUIET,
                reason: 'action_request_pending',
                timestamp: scheduler.lastEvaluatedAt,
                candidates: [],
                request: scheduler.pendingActionRequest,
            });
            return;
        }

        if (scheduler.activeAction) {
            deferUserDecisionTriggers(triggerTypes);
            recordDecision({
                trigger: 'queued',
                triggerTypes: triggerTypes,
                outcome: ACTION_IDS.QUIET,
                reason: 'active_action_pending',
                timestamp: scheduler.lastEvaluatedAt,
                candidates: [],
                request: scheduler.activeAction,
            });
            return;
        }

        if (scheduler.postActionSettle) {
            deferUserDecisionTriggers(triggerTypes);
            var settledAction = scheduler.postActionSettle;
            scheduler.postActionSettle = null;
            recordDecision({
                trigger: 'queued',
                triggerTypes: triggerTypes,
                outcome: ACTION_IDS.STAY_IDLE,
                reason: 'post_action_settle',
                timestamp: scheduler.lastEvaluatedAt,
                candidates: [],
                request: settledAction,
            });
            queueDeferredDecisionIfNeeded();
            return;
        }

        if (scheduler.deferredTriggers.length) {
            scheduler.deferredTriggers.forEach(function (type) {
                appendUniqueTrigger(triggerTypes, type);
            });
            scheduler.deferredTriggers = [];
        }

        var providers = window.NekoCatMindActionProviders;
        var gates = providers && typeof providers.getRuntimeGateSnapshot === 'function'
            ? providers.getRuntimeGateSnapshot()
            : null;
        var hardGateReason = getHardGateReason(gates);
        var base = {
            trigger: 'queued',
            outcome: hardGateReason ? ACTION_IDS.QUIET : ACTION_IDS.STAY_IDLE,
            reason: hardGateReason || 'no_eligible_candidate',
            timestamp: scheduler.lastEvaluatedAt,
            gates: clonePlain(gates) || {},
            candidates: [],
        };
        if (hardGateReason) {
            deferUserDecisionTriggers(triggerTypes);
            if (hasFreshActionIntent(scheduler.lastEvaluatedAt)) {
                scheduler.providerRecheckNeeded = true;
            }
            recordDecision(base);
            return;
        }

        var actionIds = [
            ACTION_IDS.CAT1_SOCIAL_PING,
            ACTION_IDS.CAT1_EAT_SNACK,
            ACTION_IDS.CAT1_SMALL_MOVE,
            ACTION_IDS.CAT1_PLAY_YARN,
            ACTION_IDS.CAT2_NAP_FEEDBACK,
            ACTION_IDS.CAT3_SLEEP_FEEDBACK,
        ];
        actionIds.forEach(function (actionId) {
            var candidate = {
                actionId: actionId,
                score: null,
                baseScore: null,
                threshold: null,
                needSurplus: null,
                needContribution: null,
                cadenceAdjustment: null,
                cadenceCurveProgress: null,
                cadenceCurveFactor: null,
                intentLevel: null,
                intentContribution: null,
                intentCurveFactor: null,
                intentReason: '',
                utilityScore: null,
                cooldownApplied: false,
                cooldownPenalty: 0,
                cooldownRemainingMs: 0,
                cooldownRecoveryFactor: 0,
                cooldownCurveFactor: 0,
                allowed: false,
                reason: '',
                providerDetail: {},
            };
            if (!isActionTierAllowed(actionId, runtimeState.tier)) {
                candidate.reason = 'tier_not_allowed';
            } else if (!providers || typeof providers.dryRun !== 'function') {
                candidate.reason = 'provider_unavailable';
            } else {
                var providerDecision = providers.dryRun(actionId, { source: 'cat-mind-selector-read-only' }) || {};
                candidate.providerDetail = sanitizeDetail(providerDecision.detail);
                if (providerDecision.allowed !== true) {
                    candidate.reason = typeof providerDecision.reason === 'string'
                        ? providerDecision.reason
                        : 'provider_rejected';
                    if (getActionIntentContribution(actionId, scheduler.lastEvaluatedAt).level >= 0.05) {
                        scheduler.providerRecheckNeeded = true;
                    }
                } else {
                    var scoring = directionalActionScore(actionId, scheduler.lastEvaluatedAt);
                    candidate.baseScore = Math.round(scoring.baseScore * 100) / 100;
                    candidate.threshold = scoring.threshold;
                    candidate.needSurplus = Math.round(scoring.needSurplus * 100) / 100;
                    candidate.needContribution = Math.round(scoring.needContribution * 100) / 100;
                    candidate.cadenceAdjustment = Math.round(scoring.cadenceAdjustment * 100) / 100;
                    candidate.cadenceCurveProgress = Math.round(scoring.cadenceCurveProgress * 10000) / 10000;
                    candidate.cadenceCurveFactor = Math.round(scoring.cadenceCurveFactor * 10000) / 10000;
                    candidate.intentLevel = Math.round(scoring.intentLevel * 10000) / 10000;
                    candidate.intentContribution = Math.round(scoring.intentContribution * 100) / 100;
                    candidate.intentCurveFactor = Math.round(scoring.intentCurveFactor * 10000) / 10000;
                    candidate.intentReason = scoring.intentReason;
                    candidate.utilityScore = Math.round(scoring.utilityScore * 100) / 100;
                    candidate.cooldownApplied = scoring.cooldownApplied;
                    candidate.cooldownPenalty = Math.round(scoring.cooldownPenalty * 100) / 100;
                    candidate.cooldownRemainingMs = Math.round(scoring.cooldownRemainingMs);
                    candidate.cooldownRecoveryFactor = Math.round(scoring.cooldownRecoveryFactor * 10000) / 10000;
                    candidate.cooldownCurveFactor = Math.round(scoring.cooldownCurveFactor * 10000) / 10000;
                    candidate.score = Math.round(scoring.score * 100) / 100;
                    if (candidate.utilityScore >= 0) {
                        candidate.allowed = true;
                        candidate.reason = 'allowed';
                    } else {
                        candidate.reason = 'below_threshold';
                    }
                }
            }
            base.candidates.push(candidate);
        });
        var allowed = base.candidates.filter(function (candidate) { return candidate.allowed; });
        if (allowed.length) {
            allowed.sort(function (left, right) {
                if (right.utilityScore !== left.utilityScore) return right.utilityScore - left.utilityScore;
                return getActionTieBreakIndex(left.actionId) - getActionTieBreakIndex(right.actionId);
            });
            base.outcome = allowed[0].actionId;
            base.reason = 'read_only_candidate';
        } else if (base.candidates.some(function (candidate) { return candidate.reason === 'below_threshold'; })) {
            base.reason = 'below_action_threshold';
        }
        base.triggerTypes = triggerTypes;
        if (allowed.length) {
            var request = createActionRequest(base.outcome, allowed[0], triggerTypes, scheduler.lastEvaluatedAt);
            scheduler.pendingActionRequest = Object.assign({}, request);
            armActionRequestLeaseTimer(scheduler.pendingActionRequest);
            base.reason = 'action_request_dispatched';
            base.request = request;
        }
        recordDecision(base);
        var isCurrentRequest = runtimeState.active &&
            runtimeState.scheduler.pendingActionRequest &&
            runtimeState.scheduler.pendingActionRequest.requestId === base.request.requestId;
        if (base.request &&
            isCurrentRequest) {
            dispatchRuntimeEvent(EVENT_NAMES.ACTION_REQUEST, clonePlain(base.request));
        }
    }

    function scheduleDecision(observation) {
        if (!runtimeState.active) return;
        var scheduler = runtimeState.scheduler;
        var type = observation && typeof observation.type === 'string' ? observation.type : 'unknown';
        if (scheduler.pendingTriggers.indexOf(type) === -1) scheduler.pendingTriggers.push(type);
        if (scheduler.queued || typeof window.setTimeout !== 'function') return;
        scheduler.queued = true;
        window.setTimeout(evaluateQueuedDecision, 0);
    }

    function shouldScheduleDecisionForObservation(observation) {
        var type = observation && typeof observation.type === 'string' ? observation.type : '';
        // These two events close the existing walk-to-yarn local presentation
        // tail (one 25% play / otherwise stretch). They remain full Cat Mind
        // observations for fields, recent events, and debug. They may only
        // wake a previously provider-blocked explicit intent; they never become
        // candidates or create a standalone walk/stretch action opportunity.
        if (type === OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT ||
            type === OBSERVATION_TYPES.CAT1_STRETCH_DONE_NEAR_CHAT) {
            return runtimeState.scheduler.deferredTriggers.length > 0 ||
                (runtimeState.scheduler.providerRecheckNeeded &&
                    hasFreshActionIntent(observation && observation.timestamp));
        }
        if (type === OBSERVATION_TYPES.CAT1_LOCAL_PLAY_DONE ||
            type === OBSERVATION_TYPES.CAT1_LOCAL_PLAY_CANCELLED) {
            return false;
        }
        return true;
    }

    function shouldSkipDetailValue(value) {
        return !!(
            value &&
            typeof value === 'object' &&
            (
                value.nodeType ||
                value === window ||
                typeof value.addEventListener === 'function' ||
                typeof value.dispatchEvent === 'function'
            )
        );
    }

    function sanitizeValue(value, depth) {
        if (value === null || value === undefined) {
            return value;
        }
        if (typeof value === 'string' || typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'number') {
            return Number.isFinite(value) ? value : null;
        }
        if (depth > 2 || shouldSkipDetailValue(value)) {
            return undefined;
        }
        if (Array.isArray(value)) {
            return value.slice(0, 12).map(function (item) {
                return sanitizeValue(item, depth + 1);
            }).filter(function (item) {
                return item !== undefined;
            });
        }
        if (typeof value === 'object') {
            var result = {};
            Object.keys(value).slice(0, 24).forEach(function (key) {
                if (key === 'button' || key === 'container' || key === 'originalEvent' || key === 'target') {
                    return;
                }
                var sanitized = sanitizeValue(value[key], depth + 1);
                if (sanitized !== undefined) {
                    result[key] = sanitized;
                }
            });
            return result;
        }
        return undefined;
    }

    function sanitizeDetail(detail) {
        if (!detail || typeof detail !== 'object') {
            return {};
        }
        var sanitized = sanitizeValue(detail, 0);
        return sanitized && typeof sanitized === 'object' && !Array.isArray(sanitized) ? sanitized : {};
    }

    function normalizeObservationPayload(payload) {
        if (!payload || typeof payload !== 'object') {
            return null;
        }
        var type = typeof payload.type === 'string' ? payload.type : '';
        if (!type || !isKnownObservationType(type)) {
            return null;
        }
        var detail = sanitizeDetail(payload.detail || {});
        var tier = normalizeTier(payload.tier || detail.tier || runtimeState.tier);
        var timestamp = Number(payload.timestamp || detail.timestamp);
        if (!Number.isFinite(timestamp)) {
            timestamp = nowMs();
        }
        return {
            type: type,
            source: typeof payload.source === 'string' && payload.source
                ? payload.source
                : (typeof detail.source === 'string' && detail.source ? detail.source : 'cat-mind'),
            tier: tier,
            timestamp: timestamp,
            detail: detail,
        };
    }

    function observationKey(observation) {
        if (!observation) {
            return '';
        }
        var detail = observation.detail || {};
        return [
            observation.type,
            observation.source,
            observation.tier,
            observation.timestamp,
            detail.reason || '',
            detail.action || '',
        ].join('|');
    }

    function rememberObservationKey(key) {
        if (!key) {
            return false;
        }
        if (runtimeState.seenEventKeys.indexOf(key) !== -1) {
            return true;
        }
        runtimeState.seenEventKeys.push(key);
        if (runtimeState.seenEventKeys.length > SEEN_EVENT_LIMIT) {
            runtimeState.seenEventKeys.splice(0, runtimeState.seenEventKeys.length - SEEN_EVENT_LIMIT);
        }
        return false;
    }

    function categorizeObservation(type) {
        if (type === OBSERVATION_TYPES.DRAG_START ||
            type === OBSERVATION_TYPES.DRAG_END ||
            type === OBSERVATION_TYPES.DRAG_CANCELLED ||
            type === OBSERVATION_TYPES.RAPID_DRAG ||
            type === OBSERVATION_TYPES.CAT_HOVER_REACTION ||
            type === OBSERVATION_TYPES.CHAT_YARN_DRAG_COMPLETED ||
            type === OBSERVATION_TYPES.THOUGHT_BUBBLE_POP ||
            type === OBSERVATION_TYPES.RETURN_CLICK) {
            return 'user';
        }
        if (type === OBSERVATION_TYPES.CHAT_MINIMIZED_VISIBLE ||
            type === OBSERVATION_TYPES.CHAT_MINIMIZED_MOVED_FAR ||
            type === OBSERVATION_TYPES.CHAT_COMPACT_SURFACE_VISIBLE ||
            type === OBSERVATION_TYPES.CHAT_EXPANDED ||
            type === OBSERVATION_TYPES.CHAT_IDLE_DOCKED_NEAR_CAT ||
            type === OBSERVATION_TYPES.DESKTOP_OCCLUSION_OR_LAYER_CHANGE) {
            return 'window';
        }
        if (type === OBSERVATION_TYPES.SOCIAL_PING_DONE ||
            type === OBSERVATION_TYPES.SOCIAL_PING_FAILED ||
            type === OBSERVATION_TYPES.SMALL_MOVE_DONE ||
            type === OBSERVATION_TYPES.SMALL_MOVE_CANCELLED ||
            type === OBSERVATION_TYPES.EAT_DONE ||
            type === OBSERVATION_TYPES.EAT_CANCELLED ||
            type === OBSERVATION_TYPES.PLAY_DONE ||
            type === OBSERVATION_TYPES.PLAY_CANCELLED ||
            type === OBSERVATION_TYPES.SLEEP_FEEDBACK_DONE ||
            type === OBSERVATION_TYPES.SLEEP_FEEDBACK_FAILED ||
            type === OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_DRAG ||
            type === OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_RETURN ||
            type === OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_TIER_CHANGE) {
            return 'action_result';
        }
        if (type === OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT ||
            type === OBSERVATION_TYPES.CAT1_STRETCH_DONE_NEAR_CHAT ||
            type === OBSERVATION_TYPES.CAT1_LOCAL_PLAY_DONE ||
            type === OBSERVATION_TYPES.CAT1_LOCAL_PLAY_CANCELLED ||
            type === OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DONE ||
            type === OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DROP ||
            type === OBSERVATION_TYPES.EDGE_PEEK_AFTER_DRAG ||
            type === OBSERVATION_TYPES.TIER_CHANGED ||
            type === OBSERVATION_TYPES.TIER_DEMOTED_BY_DRAG) {
            return 'presentation';
        }
        return 'time';
    }

    function isClockObservationType(type) {
        return type === OBSERVATION_TYPES.CAT_ELAPSED ||
            type === OBSERVATION_TYPES.INACTIVE_ELAPSED ||
            type === OBSERVATION_TYPES.SINCE_LAST_ACTION;
    }

    function isUserInteractionObservationType(type) {
        return type === OBSERVATION_TYPES.DRAG_START ||
            type === OBSERVATION_TYPES.DRAG_END ||
            type === OBSERVATION_TYPES.DRAG_CANCELLED ||
            type === OBSERVATION_TYPES.RAPID_DRAG ||
            type === OBSERVATION_TYPES.CAT_HOVER_REACTION ||
            type === OBSERVATION_TYPES.CHAT_YARN_DRAG_COMPLETED ||
            type === OBSERVATION_TYPES.THOUGHT_BUBBLE_POP;
    }

    function getReturnEpisodeAccumulator() {
        if (!runtimeState.returnEpisodeAccumulator ||
            typeof runtimeState.returnEpisodeAccumulator !== 'object') {
            runtimeState.returnEpisodeAccumulator = createReturnEpisodeAccumulator();
        }
        return runtimeState.returnEpisodeAccumulator;
    }

    function resetReturnEpisodeActiveChapter(accumulator) {
        accumulator.activeChapter = {
            interactionSeen: false,
            activityKinds: [],
        };
    }

    function getReturnEpisodeActivityKinds(activeChapter) {
        var kinds = activeChapter && Array.isArray(activeChapter.activityKinds)
            ? activeChapter.activityKinds
            : [];
        return RETURN_EPISODE_ACTIVITY_ORDER.filter(function (actionId) {
            return kinds.indexOf(actionId) !== -1;
        });
    }

    function getReturnEpisodeHighlight(activityKinds) {
        if (!Array.isArray(activityKinds) || activityKinds.length !== 1) {
            return '';
        }
        return RETURN_EPISODE_HIGHLIGHTS[activityKinds[0]] || '';
    }

    function recordReturnEpisodeInteraction() {
        getReturnEpisodeAccumulator().activeChapter.interactionSeen = true;
    }

    function recordReturnEpisodeActionDone(actionId) {
        var accumulator = getReturnEpisodeAccumulator();
        var activeChapter = accumulator.activeChapter;
        if (RETURN_EPISODE_ACTIVITY_ORDER.indexOf(actionId) !== -1) {
            if (activeChapter.activityKinds.indexOf(actionId) === -1) {
                activeChapter.activityKinds.push(actionId);
            }
            return;
        }
        if (actionId !== ACTION_IDS.CAT2_NAP_FEEDBACK &&
            actionId !== ACTION_IDS.CAT3_SLEEP_FEEDBACK) {
            return;
        }

        var activityKinds = getReturnEpisodeActivityKinds(activeChapter);
        if (activityKinds.length) {
            accumulator.lastRest = {
                hadActivityBeforeRest: true,
                highlight: activityKinds.length === 1 ? activityKinds[0] : null,
            };
            resetReturnEpisodeActiveChapter(accumulator);
            return;
        }
        if (activeChapter.interactionSeen || !accumulator.lastRest) {
            accumulator.lastRest = {
                hadActivityBeforeRest: false,
                highlight: null,
            };
            resetReturnEpisodeActiveChapter(accumulator);
        }
    }

    function buildReturnEpisode() {
        var accumulator = getReturnEpisodeAccumulator();
        var activeChapter = accumulator.activeChapter || {};
        var activityKinds = getReturnEpisodeActivityKinds(activeChapter);
        var highlight = getReturnEpisodeHighlight(activityKinds);
        if (activityKinds.length) {
            var activityEpisode = { kind: 'activity' };
            if (highlight) activityEpisode.highlight = highlight;
            return activityEpisode;
        }
        if (activeChapter.interactionSeen) {
            return null;
        }
        var lastRest = accumulator.lastRest;
        if (!lastRest || typeof lastRest !== 'object') {
            return null;
        }
        if (lastRest.hadActivityBeforeRest === true) {
            var restAfterActivity = { kind: 'rest_after_activity' };
            var restHighlight = RETURN_EPISODE_HIGHLIGHTS[lastRest.highlight] || '';
            if (restHighlight) restAfterActivity.highlight = restHighlight;
            return restAfterActivity;
        }
        return { kind: 'rested' };
    }

    function getReturnEpisodeDebugSnapshot() {
        var accumulator = getReturnEpisodeAccumulator();
        return {
            activeChapter: clonePlain(accumulator.activeChapter) || createReturnEpisodeAccumulator().activeChapter,
            lastRest: clonePlain(accumulator.lastRest),
            preview: clonePlain(buildReturnEpisode()),
        };
    }

    function adjustMind(delta) {
        Object.keys(delta || {}).forEach(function (field) {
            if (MIND_FIELDS.indexOf(field) === -1) {
                return;
            }
            runtimeState.fields[field] = clamp01((runtimeState.fields[field] || 0) + Number(delta[field] || 0));
        });
    }

    function mergeInteractionNeed(evidence) {
        ['social_need', 'stimulation_need'].forEach(function (field) {
            var dose = Math.max(0, Number(evidence && evidence[field]) || 0);
            if (!dose) return;
            var current = clamp01(runtimeState.fields[field]);
            // Evidence merging has a natural headroom term: many meaningful
            // interactions still accumulate, while repeated samples from one
            // gesture cannot linearly force the field to 1.
            runtimeState.fields[field] = clamp01(
                current + dose * (1 - current) * (1 + 0.8 * current)
            );
        });
        var energyDelta = Number(evidence && evidence.energy);
        if (Number.isFinite(energyDelta) && energyDelta) {
            adjustMind({ energy: energyDelta });
        }
    }

    function satisfyInteractionNeed(field, fraction) {
        if (MIND_FIELDS.indexOf(field) === -1) return;
        var normalizedFraction = clamp01(fraction);
        runtimeState.fields[field] = clamp01(runtimeState.fields[field] * (1 - normalizedFraction));
    }

    function beginInteractionGesture(observation) {
        var observationAt = Number(observation && observation.timestamp) || nowMs();
        if (runtimeState.interactionGesture &&
            observationAt - Number(runtimeState.interactionGesture.startedAt || 0) > 10 * 1000) {
            runtimeState.interactionGesture = null;
        }
        if (runtimeState.interactionGesture) return runtimeState.interactionGesture;
        runtimeState.interactionGesture = {
            startedAt: observationAt,
            rapidApplied: false,
        };
        mergeInteractionNeed(INTERACTION_FEEDBACK_POLICY.dragStart);
        return runtimeState.interactionGesture;
    }

    function applyRapidGestureFeedback(observation) {
        var gesture = beginInteractionGesture(observation);
        if (!gesture || gesture.rapidApplied) return;
        gesture.rapidApplied = true;
        mergeInteractionNeed(INTERACTION_FEEDBACK_POLICY.rapidImmediate);
    }

    function getInteractionActivityId(detail) {
        return detail && typeof detail.activityId === 'string' ? detail.activityId : '';
    }

    function hasAccountedInteractionActivity(activityId) {
        return !!activityId && runtimeState.accountedInteractionActivityIds.indexOf(activityId) !== -1;
    }

    function rememberAccountedInteractionActivity(activityId) {
        if (!activityId || hasAccountedInteractionActivity(activityId)) return;
        runtimeState.accountedInteractionActivityIds.push(activityId);
        if (runtimeState.accountedInteractionActivityIds.length > ACCOUNTED_INTERACTION_ACTIVITY_LIMIT) {
            runtimeState.accountedInteractionActivityIds.splice(
                0,
                runtimeState.accountedInteractionActivityIds.length - ACCOUNTED_INTERACTION_ACTIVITY_LIMIT
            );
        }
    }

    function settleInteractionGesture(cancelled, observation) {
        var detail = observation && observation.detail;
        var activityId = getInteractionActivityId(detail);
        // Native, DOM and compatibility producers may report the same terminal
        // fact through more than one alias. Preserve an in-flight newer gesture
        // when such a duplicate arrives, and settle semantic feedback only once.
        if (activityId && hasAccountedInteractionActivity(activityId)) return false;
        var gesture = runtimeState.interactionGesture;
        var observationAt = Number(observation && observation.timestamp) || nowMs();
        if (gesture && observationAt - Number(gesture.startedAt || 0) > 10 * 1000) {
            gesture = null;
        }
        runtimeState.interactionGesture = null;
        // A stable terminal id is sufficient evidence for end-only platforms.
        // An unidentified orphan terminal is not safe to count as interaction.
        if (!gesture && !activityId) return false;
        rememberAccountedInteractionActivity(activityId);
        if (cancelled) {
            mergeInteractionNeed(INTERACTION_FEEDBACK_POLICY.dragCancelled);
            return true;
        }
        mergeInteractionNeed(gesture && gesture.rapidApplied
            ? INTERACTION_FEEDBACK_POLICY.rapidTerminal
            : INTERACTION_FEEDBACK_POLICY.dragEnd);
        return true;
    }

    function getPhysicalActivityFact(detail) {
        var source = detail && typeof detail === 'object' ? detail : {};
        var activityId = typeof source.activityId === 'string' ? source.activityId : '';
        // Terminal producers own a stable activity identity. Without it Cat
        // Mind cannot prove that duplicate aliases describe the same movement,
        // so it must not charge an unbounded physical delta.
        if (!activityId) return null;
        var distanceCandidates = [
            source.pathDistancePx,
            source.distancePx,
            source.movedDistancePx,
            source.displacementPx,
        ];
        var pathDistancePx = 0;
        for (var index = 0; index < distanceCandidates.length; index += 1) {
            var distance = Number(distanceCandidates[index]);
            if (Number.isFinite(distance) && distance > 0) {
                pathDistancePx = distance;
                break;
            }
        }
        if (!pathDistancePx) return null;
        var durationMs = Number(source.durationMs);
        if (!Number.isFinite(durationMs) || durationMs < 0) {
            durationMs = Number(source.plannedDurationMs);
        }
        var distanceFactor = clamp01(pathDistancePx / PHYSICAL_ACTIVITY_POLICY.referenceDistancePx);
        var durationFactor = Number.isFinite(durationMs) && durationMs > 0
            ? clamp01(durationMs / PHYSICAL_ACTIVITY_POLICY.referenceDurationMs)
            : distanceFactor;
        var blendedLoad = 0.8 * distanceFactor + 0.2 * Math.min(distanceFactor, durationFactor);
        return {
            activityId: activityId,
            pathDistancePx: pathDistancePx,
            durationMs: Number.isFinite(durationMs) && durationMs > 0 ? durationMs : 0,
            load: smoothstep01(blendedLoad),
        };
    }

    function hasAccountedPhysicalActivity(activityId) {
        return !!activityId && runtimeState.accountedPhysicalActivityIds.indexOf(activityId) !== -1;
    }

    function rememberAccountedPhysicalActivity(activityId) {
        if (!activityId || hasAccountedPhysicalActivity(activityId)) return;
        runtimeState.accountedPhysicalActivityIds.push(activityId);
        if (runtimeState.accountedPhysicalActivityIds.length > ACCOUNTED_PHYSICAL_ACTIVITY_LIMIT) {
            runtimeState.accountedPhysicalActivityIds.splice(
                0,
                runtimeState.accountedPhysicalActivityIds.length - ACCOUNTED_PHYSICAL_ACTIVITY_LIMIT
            );
        }
    }

    function applyPhysicalActivity(detail) {
        var fact = getPhysicalActivityFact(detail);
        if (!fact || !fact.load || hasAccountedPhysicalActivity(fact.activityId)) return false;
        adjustMind({
            appetite: PHYSICAL_ACTIVITY_POLICY.appetiteDelta * fact.load,
            energy: PHYSICAL_ACTIVITY_POLICY.energyDelta * fact.load,
            sleepiness: PHYSICAL_ACTIVITY_POLICY.sleepinessDelta * fact.load,
        });
        rememberAccountedPhysicalActivity(fact.activityId);
        return true;
    }

    function applyTierToMind(tier) {
        if (tier === TIERS.CAT2) {
            runtimeState.fields.sleepiness = Math.max(runtimeState.fields.sleepiness, 0.55);
            runtimeState.fields.energy = Math.min(runtimeState.fields.energy, 0.45);
        } else if (tier === TIERS.CAT3) {
            runtimeState.fields.sleepiness = Math.max(runtimeState.fields.sleepiness, 0.78);
            runtimeState.fields.energy = Math.min(runtimeState.fields.energy, 0.25);
        } else if (tier === TIERS.CAT1) {
            runtimeState.fields.energy = Math.max(runtimeState.fields.energy, 0.45);
        }
    }

    function advanceNeedWithOverflowLeak(field, ratePerMinute, elapsedMinutes, comfort, halfLifeMinutes) {
        var current = clamp01(runtimeState.fields[field]);
        var next = current + Math.max(0, Number(ratePerMinute) || 0) * elapsedMinutes;
        if (next > comfort && halfLifeMinutes > 0) {
            next = comfort + (next - comfort) * Math.pow(0.5, elapsedMinutes / halfLifeMinutes);
        }
        runtimeState.fields[field] = clamp01(next);
    }

    function applyElapsedMindTime(observation) {
        if (!observation || observation.source !== 'cat-mind-clock') return;
        var elapsedMs = Number(observation.detail && observation.detail.elapsedMs);
        if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) return;
        var rate = TIME_RATE_PER_MINUTE[runtimeState.tier];
        var overflow = NEED_OVERFLOW_POLICY[runtimeState.tier];
        if (!rate) return;
        var elapsedMinutes = elapsedMs / (60 * 1000);
        adjustMind({
            appetite: rate.appetite * elapsedMinutes,
            sleepiness: rate.sleepiness * elapsedMinutes,
            energy: rate.energy * elapsedMinutes,
        });
        if (overflow) {
            advanceNeedWithOverflowLeak(
                'social_need', rate.social_need, elapsedMinutes,
                overflow.socialComfort, overflow.socialHalfLifeMinutes
            );
            advanceNeedWithOverflowLeak(
                'stimulation_need', rate.stimulation_need, elapsedMinutes,
                overflow.stimulationComfort, overflow.stimulationHalfLifeMinutes
            );
        } else {
            adjustMind({
                social_need: rate.social_need * elapsedMinutes,
                stimulation_need: rate.stimulation_need * elapsedMinutes,
            });
        }
    }

    function reduceObservation(observation) {
        var type = observation.type;
        if (type === OBSERVATION_TYPES.CAT_ELAPSED) {
            applyElapsedMindTime(observation);
            return;
        }
        if (type === OBSERVATION_TYPES.TIER_CHANGED) {
            runtimeState.tier = normalizeTier(observation.tier || observation.detail.tier);
            applyTierToMind(runtimeState.tier);
            return;
        }
        if (type === OBSERVATION_TYPES.DRAG_START) {
            beginInteractionGesture(observation);
        } else if (type === OBSERVATION_TYPES.DRAG_END) {
            settleInteractionGesture(false, observation);
            applyPhysicalActivity(observation.detail);
        } else if (type === OBSERVATION_TYPES.DRAG_CANCELLED) {
            settleInteractionGesture(true, observation);
            applyPhysicalActivity(observation.detail);
        } else if (type === OBSERVATION_TYPES.RAPID_DRAG) {
            applyRapidGestureFeedback(observation);
        } else if (type === OBSERVATION_TYPES.CAT_HOVER_REACTION) {
            // Hover is easy to trigger and therefore low-confidence evidence.
            // It only nudges the five fields; the shared score still decides the
            // legal action without an event-to-runner mapping.
            mergeInteractionNeed(INTERACTION_FEEDBACK_POLICY.hover);
        } else if (type === OBSERVATION_TYPES.THOUGHT_BUBBLE_POP) {
            // Popping the bubble is a completed moment of contact, so it
            // satisfies social/stimulation need instead of feeding another
            // bubble-producing action loop.
            satisfyInteractionNeed('social_need', INTERACTION_FEEDBACK_POLICY.bubbleSocialSatisfaction);
            satisfyInteractionNeed('stimulation_need', INTERACTION_FEEDBACK_POLICY.bubbleStimulationSatisfaction);
        } else if (type === OBSERVATION_TYPES.CHAT_MINIMIZED_VISIBLE ||
            type === OBSERVATION_TYPES.CHAT_MINIMIZED_MOVED_FAR ||
            type === OBSERVATION_TYPES.CHAT_COMPACT_SURFACE_VISIBLE ||
            type === OBSERVATION_TYPES.CHAT_IDLE_DOCKED_NEAR_CAT ||
            type === OBSERVATION_TYPES.CHAT_EXPANDED) {
            // Window geometry is an execution/provider fact, not a user need.
            // It still enters recent events and schedules the next decision.
            return;
        } else if (type === OBSERVATION_TYPES.CAT1_WALK_DONE_NEAR_CHAT) {
            // A cancelled journey still settles the distance already walked,
            // but must not receive the semantic reward for reaching chat.
            if (!(observation.detail &&
                (observation.detail.completed === false || observation.detail.cancelled === true))) {
                adjustMind({ social_need: 0.05, stimulation_need: -0.04 });
            }
            applyPhysicalActivity(observation.detail);
        } else if (type === OBSERVATION_TYPES.CAT1_STRETCH_DONE_NEAR_CHAT) {
            adjustMind({ stimulation_need: -0.04, energy: -0.03, sleepiness: 0.02 });
        } else if (type === OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DONE) {
            adjustMind({ social_need: 0.04, stimulation_need: -0.06, energy: -0.03 });
            applyPhysicalActivity(observation.detail);
        } else if (type === OBSERVATION_TYPES.CAT1_COMPACT_TOP_EDGE_DROP ||
            type === OBSERVATION_TYPES.EDGE_PEEK_AFTER_DRAG) {
            adjustMind({ stimulation_need: 0.04, energy: -0.02 });
        } else if (type === OBSERVATION_TYPES.SOCIAL_PING_DONE) {
            adjustMind({ social_need: -0.34, energy: -0.01 });
        } else if (type === OBSERVATION_TYPES.SMALL_MOVE_DONE) {
            adjustMind({ stimulation_need: -0.14 });
            applyPhysicalActivity(observation.detail);
        } else if (type === OBSERVATION_TYPES.SMALL_MOVE_CANCELLED) {
            // Cancellation does not claim the action's completion feedback,
            // but any movement that already happened is still physical work.
            applyPhysicalActivity(observation.detail);
        } else if (type === OBSERVATION_TYPES.EAT_DONE) {
            adjustMind({ appetite: -0.34, energy: 0.14, stimulation_need: 0.02, sleepiness: -0.01 });
        } else if (type === OBSERVATION_TYPES.PLAY_DONE ||
            type === OBSERVATION_TYPES.CAT1_LOCAL_PLAY_DONE) {
            adjustMind({ stimulation_need: -0.28, energy: -0.08, appetite: 0.07, sleepiness: 0.05 });
        } else if (type === OBSERVATION_TYPES.SLEEP_FEEDBACK_DONE) {
            if (observation.tier === TIERS.CAT3) {
                adjustMind({ sleepiness: -0.38, energy: 0.2, stimulation_need: 0.02, appetite: 0.02 });
            } else {
                adjustMind({ sleepiness: -0.3, energy: 0.16, stimulation_need: 0.02, appetite: 0.02 });
            }
        } else if (type === OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_DRAG ||
            type === OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_RETURN ||
            type === OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_TIER_CHANGE) {
            // Interruption is lifecycle metadata. The drag/tier/return source
            // observation already owns its five-dimensional feedback; applying
            // another fixed delta here would double-count the same interaction.
            return;
        }
    }

    function addRecentEvent(observation) {
        if (isClockObservationType(observation.type)) return;
        runtimeState.sequence += 1;
        runtimeState.recentEvents.push(Object.freeze({
            seq: runtimeState.sequence,
            type: observation.type,
            category: categorizeObservation(observation.type),
            source: observation.source,
            tier: observation.tier,
            timestamp: observation.timestamp,
            detail: clonePlain(observation.detail) || {},
        }));
        if (runtimeState.recentEvents.length > RECENT_EVENT_LIMIT) {
            runtimeState.recentEvents.splice(0, runtimeState.recentEvents.length - RECENT_EVENT_LIMIT);
        }
    }

    function observe(payload) {
        var observation = normalizeObservationPayload(payload);
        if (!observation) {
            return null;
        }
        if (!runtimeState.active && observation.type !== OBSERVATION_TYPES.CAT_ENTERED) {
            return null;
        }
        if (rememberObservationKey(observationKey(observation))) {
            return null;
        }

        runtimeState.updatedAt = observation.timestamp;
        reduceObservation(observation);
        applyActionIntentObservation(observation);
        if (isUserInteractionObservationType(observation.type)) {
            runtimeState.clock.lastUserInteractionAt = observation.timestamp;
            recordReturnEpisodeInteraction();
        }
        addRecentEvent(observation);
        if (shouldScheduleDecisionForObservation(observation)) {
            scheduleDecision(observation);
        }
        emitStateChange('observation', { observation: observation });
        return clonePlain(observation);
    }

    function buildReturnSummaryDraft() {
        var summary = {
            duration_seconds: currentDurationSeconds(),
            entry: runtimeState.entry || 'manual',
            final_tier: runtimeState.tier,
        };
        var episode = buildReturnEpisode();
        if (episode) summary.episode = episode;
        return summary;
    }

    function beginCatMind(detail) {
        var eventDetail = detail && typeof detail === 'object' ? detail : {};
        var timestamp = Number(eventDetail.timestamp);
        if (!Number.isFinite(timestamp)) {
            timestamp = nowMs();
        }
        if (runtimeState.active) {
            return getState();
        }
        clearActionRequestLeaseTimer();
        runtimeState = createInitialRuntimeState();
        runtimeState.active = true;
        var isStartupDefaultCat = eventDetail.startupDefaultForm === 'cat';
        runtimeState.entry = eventDetail.autoGoodbye === true ||
            eventDetail.source === 'auto-goodbye' || isStartupDefaultCat
            ? 'auto'
            : 'manual';
        runtimeState.fields = createInitialMindFields(runtimeState.entry);
        runtimeState.tier = TIERS.CAT1;
        runtimeState.enteredAt = timestamp;
        runtimeState.updatedAt = timestamp;
        startAutonomousClock(timestamp);
        observe({
            type: OBSERVATION_TYPES.CAT_ENTERED,
            source: isStartupDefaultCat
                ? 'startup-default-form'
                : (runtimeState.entry === 'auto' ? 'auto-goodbye' : 'manual-goodbye'),
            tier: TIERS.CAT1,
            timestamp: timestamp,
            detail: {
                entry: runtimeState.entry,
                reason: eventDetail.reason || (isStartupDefaultCat
                    ? 'startup-default-cat'
                    : (runtimeState.entry === 'auto' ? 'idle-timeout' : 'manual-goodbye')),
                autoGoodbye: eventDetail.autoGoodbye === true,
                startupDefaultForm: isStartupDefaultCat ? 'cat' : undefined,
            },
        });
    }

    function isCatGreetingReturnSource(source) {
        return source === 'live2d-return-click' ||
            source === 'vrm-return-click' ||
            source === 'mmd-return-click' ||
            source === 'pngtuber-return-click';
    }

    function finishCatMindReturn(source) {
        if (!runtimeState.active) {
            return;
        }
        var returnSource = source || 'return-click';
        observe({
            type: OBSERVATION_TYPES.RETURN_CLICK,
            source: returnSource,
            tier: runtimeState.tier,
            timestamp: nowMs(),
            detail: {
                reason: returnSource,
            },
        });
        var summary = Object.freeze(buildReturnSummaryDraft());
        clearAutonomousClock();
        clearActionRequestLeaseTimer();
        runtimeState = createInitialRuntimeState();
        if (isCatGreetingReturnSource(returnSource)) {
            runtimeState.returnSummaryDraft = summary;
        }
        runtimeState.lastResetReason = 'return';
        runtimeState.updatedAt = nowMs();
        emitStateChange('return', { source: returnSource });
        dispatchRuntimeEvent(EVENT_NAMES.RETURN_SUMMARY, {
            source: returnSource,
            timestamp: runtimeState.updatedAt,
            summary: clonePlain(summary),
        });
    }

    function syncCatAppearanceLifecycle(detail) {
        var eventDetail = detail && typeof detail === 'object' ? detail : {};
        if (eventDetail.active === true) {
            beginCatMind(eventDetail);
            var activeTier = normalizeTier(eventDetail.tier);
            if (activeTier !== 'none' && activeTier !== runtimeState.tier) {
                observeTierChange({
                    type: 'visual-tier',
                    tier: activeTier,
                    source: eventDetail.source || 'cat-appearance',
                    reason: eventDetail.reason || 'cat-appearance-active',
                    timestamp: eventDetail.timestamp,
                });
            }
            return getState();
        }
        if (eventDetail.active !== false) {
            return getState();
        }
        if (eventDetail.returnCommitted === true) {
            return finishCatMindReturn(eventDetail.returnSource || eventDetail.source || 'return-click');
        }
        if (runtimeState.active || (eventDetail.discardReturnSummary === true && runtimeState.returnSummaryDraft)) {
            return resetRuntime(eventDetail.reason || 'cat-appearance-ended');
        }
        return getState();
    }

    function observeTierChange(detail) {
        if (!detail || typeof detail !== 'object' || detail.type !== 'visual-tier') {
            return;
        }
        var tier = normalizeTier(detail.tier);
        observe({
            type: OBSERVATION_TYPES.TIER_CHANGED,
            source: detail.source || 'auto-goodbye',
            tier: tier,
            timestamp: detail.timestamp,
            detail: {
                reason: detail.reason || '',
                sourceType: detail.type,
            },
        });
        if (detail.source === 'return-ball-drag-demotion') {
            observe({
                type: OBSERVATION_TYPES.TIER_DEMOTED_BY_DRAG,
                source: detail.source,
                tier: tier,
                timestamp: detail.timestamp,
                detail: {
                    reason: detail.reason || 'return-ball-drag-end',
                },
            });
        }
    }

    function observeReturnBallManualMove(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        var reason = typeof detail.reason === 'string' ? detail.reason : '';
        var type = '';
        if (reason === 'return-ball-drag-start') {
            type = OBSERVATION_TYPES.DRAG_START;
        } else if (reason === 'return-ball-drag-active') {
            return;
        } else if (reason === 'return-ball-drag-cancel') {
            type = OBSERVATION_TYPES.DRAG_CANCELLED;
        } else if (reason === 'return-ball-drag-end') {
            type = detail.dragCancelled === true
                ? OBSERVATION_TYPES.DRAG_CANCELLED
                : OBSERVATION_TYPES.DRAG_END;
        } else if (reason === 'return-ball-drag-motion' && detail.rapidDrag === true) {
            type = OBSERVATION_TYPES.RAPID_DRAG;
        }
        if (!type) {
            return;
        }
        observe({
            type: type,
            source: 'return-ball',
            tier: detail.tier || runtimeState.tier,
            timestamp: detail.timestamp,
            detail: detail,
        });
    }

    function observeThoughtBubblePop(detail) {
        observe({
            type: OBSERVATION_TYPES.THOUGHT_BUBBLE_POP,
            source: detail && detail.source ? detail.source : 'thought-bubble',
            tier: detail && detail.tier ? detail.tier : runtimeState.tier,
            timestamp: detail && detail.timestamp,
            detail: detail || {},
        });
    }

    function observeDesktopChatMinimized(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        var rect = normalizeRect(detail.screenRect);
        if (detail.reason === 'idle-dock-enter') {
            if (runtimeState.lastChatIdleDocked &&
                runtimeState.lastChatMinimizedState === true &&
                areRectsEqual(runtimeState.lastChatMinimizedRect, rect)) {
                return;
            }
            runtimeState.lastChatMinimizedRect = rect || runtimeState.lastChatMinimizedRect;
            runtimeState.lastChatMinimizedState = true;
            runtimeState.lastChatIdleDocked = true;
            observe({
                type: OBSERVATION_TYPES.CHAT_IDLE_DOCKED_NEAR_CAT,
                source: detail.source || 'chat-window',
                tier: runtimeState.tier,
                timestamp: detail.timestamp,
                detail: detail,
            });
            return;
        }
        if (detail.reason === 'idle-dock-exit') {
            // An already-minimized chat can leave dock without changing its
            // rect. Keep the state/rect deduplication, but allow a later dock
            // entry at that same rect to become a new observation.
            runtimeState.lastChatIdleDocked = false;
        }
        if (detail.minimized && rect) {
            var previousRect = runtimeState.lastChatMinimizedRect;
            // Native IPC, BroadcastChannel and local UI notifications can
            // report the same chat state with different reasons. A matching
            // minimized state and rect is one observation, not another
            // five-dimensional experience or selector opportunity.
            if (runtimeState.lastChatMinimizedState === true &&
                areRectsEqual(previousRect, rect)) {
                return;
            }
            var movedFar = !!previousRect &&
                rectCenterMoveDistance(previousRect, rect) >= CHAT_MOVED_FAR_DISTANCE_PX;
            runtimeState.lastChatMinimizedRect = rect;
            runtimeState.lastChatMinimizedState = true;
            runtimeState.lastChatIdleDocked = false;
            observe({
                type: movedFar ? OBSERVATION_TYPES.CHAT_MINIMIZED_MOVED_FAR : OBSERVATION_TYPES.CHAT_MINIMIZED_VISIBLE,
                source: detail.source || 'chat-window',
                tier: runtimeState.tier,
                timestamp: detail.timestamp,
                detail: detail,
            });
            return;
        }
        // The same applies to expanded notifications from any bridge.
        if (runtimeState.lastChatMinimizedState === false) {
            return;
        }
        runtimeState.lastChatMinimizedRect = null;
        runtimeState.lastChatMinimizedState = false;
        runtimeState.lastChatIdleDocked = false;
        observe({
            type: OBSERVATION_TYPES.CHAT_EXPANDED,
            source: detail.source || 'chat-window',
            tier: runtimeState.tier,
            timestamp: detail.timestamp,
            detail: detail,
        });
    }

    function observeCompactSurface(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        if (detail.heartbeat) {
            return;
        }
        var visible = detail.visible !== false;
        if (!visible && !detail.screenRect && !detail.left && !detail.width) {
            return;
        }
        observe({
            type: OBSERVATION_TYPES.CHAT_COMPACT_SURFACE_VISIBLE,
            source: detail.source || 'compact-surface',
            tier: runtimeState.tier,
            timestamp: detail.timestamp,
            detail: detail,
        });
    }

    function observeIdleReturnBallState(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        observe({
            type: OBSERVATION_TYPES.DESKTOP_OCCLUSION_OR_LAYER_CHANGE,
            source: detail.source || 'return-ball',
            tier: detail.tier || runtimeState.tier,
            timestamp: detail.timestamp,
            detail: detail,
        });
    }

    function observeChatSurfaceMode(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        var mode = typeof detail.mode === 'string' ? detail.mode : '';
        if (!mode) {
            return;
        }
        var type = mode === 'minimized'
            ? OBSERVATION_TYPES.CHAT_MINIMIZED_VISIBLE
            : (mode === 'compact' ? OBSERVATION_TYPES.CHAT_COMPACT_SURFACE_VISIBLE : OBSERVATION_TYPES.CHAT_EXPANDED);
        observe({
            type: type,
            source: 'react-chat-window',
            tier: runtimeState.tier,
            timestamp: detail.timestamp,
            detail: detail,
        });
    }

    function observeExternalObservation(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        observe({
            type: detail.type,
            source: detail.source || 'cat-idle-source',
            tier: detail.tier,
            timestamp: detail.timestamp,
            detail: detail.detail || {},
        });
    }

    function actionInterruptionObservationType(reason) {
        var normalizedReason = typeof reason === 'string' ? reason : '';
        if (normalizedReason.indexOf('drag') !== -1) {
            return OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_DRAG;
        }
        if (normalizedReason.indexOf('return') !== -1 ||
            normalizedReason.indexOf('cat-to-model') !== -1) {
            return OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_RETURN;
        }
        if (normalizedReason.indexOf('tier') !== -1) {
            return OBSERVATION_TYPES.ACTION_INTERRUPTED_BY_TIER_CHANGE;
        }
        return '';
    }

    function actionResultObservationType(actionId, result) {
        if (actionId === ACTION_IDS.CAT1_SOCIAL_PING) {
            return result === 'done'
                ? OBSERVATION_TYPES.SOCIAL_PING_DONE
                : OBSERVATION_TYPES.SOCIAL_PING_FAILED;
        }
        if (actionId === ACTION_IDS.CAT1_EAT_SNACK) {
            return result === 'done'
                ? OBSERVATION_TYPES.EAT_DONE
                : OBSERVATION_TYPES.EAT_CANCELLED;
        }
        if (actionId === ACTION_IDS.CAT1_SMALL_MOVE) {
            return result === 'done'
                ? OBSERVATION_TYPES.SMALL_MOVE_DONE
                : OBSERVATION_TYPES.SMALL_MOVE_CANCELLED;
        }
        if (actionId === ACTION_IDS.CAT1_PLAY_YARN) {
            return result === 'done'
                ? OBSERVATION_TYPES.PLAY_DONE
                : OBSERVATION_TYPES.PLAY_CANCELLED;
        }
        if (actionId === ACTION_IDS.CAT2_NAP_FEEDBACK ||
            actionId === ACTION_IDS.CAT3_SLEEP_FEEDBACK) {
            return result === 'done'
                ? OBSERVATION_TYPES.SLEEP_FEEDBACK_DONE
                : OBSERVATION_TYPES.SLEEP_FEEDBACK_FAILED;
        }
        return '';
    }

    function observeActionResult(detail) {
        if (!detail || typeof detail !== 'object') {
            return;
        }
        var actionId = typeof detail.actionId === 'string' ? detail.actionId : '';
        var result = typeof detail.result === 'string' ? detail.result : '';
        var reason = typeof detail.reason === 'string' ? detail.reason : '';
        if (!actionId || !result) {
            return;
        }
        var lifecycle = settleActionLifecycleFromResult(detail);
        if (!lifecycle.settled) {
            runtimeState.scheduler.lastIgnoredActionResult = {
                actionId: actionId,
                requestId: detail && detail.detail && typeof detail.detail.requestId === 'string'
                    ? detail.detail.requestId
                    : '',
                result: result,
                timestamp: Number.isFinite(Number(detail.timestamp)) ? Number(detail.timestamp) : nowMs(),
                reason: 'unmatched_or_nonterminal_result',
            };
            emitStateChange('action-result-ignored', {
                actionId: actionId,
                requestId: detail && detail.detail && detail.detail.requestId,
            });
            return;
        }
        // A renderer may accept a request and then report a terminal result
        // without ever proving that playback started. Recover the lease, but
        // do not invent cooldown, completion feedback, or return-episode facts.
        if (!lifecycle.applyResult) {
            queueDeferredDecisionIfNeeded();
            return;
        }
        if (result === 'done') {
            recordReturnEpisodeActionDone(actionId);
        }
        var terminalType = actionResultObservationType(actionId, result);
        if (!terminalType) {
            return;
        }
        var actionDetail = clonePlain(detail.detail) || {};
        actionDetail.actionId = actionId;
        actionDetail.result = result;
        actionDetail.reason = reason;
        observe({
            type: terminalType,
            source: detail.source || 'cat-mind-runner',
            tier: detail.tier || runtimeState.tier,
            timestamp: detail.timestamp,
            detail: actionDetail,
        });
        var interruptedType = result === 'interrupted'
            ? actionInterruptionObservationType(reason)
            : '';
        if (interruptedType) {
            var interruptionTimestamp = Number(detail.timestamp);
            if (!Number.isFinite(interruptionTimestamp)) interruptionTimestamp = nowMs();
            observe({
                type: interruptedType,
                source: detail.source || 'cat-mind-runner',
                tier: detail.tier || runtimeState.tier,
                timestamp: interruptionTimestamp + 0.001,
                detail: actionDetail,
            });
        }
    }

    function installObservationListeners() {
        window.addEventListener(EVENT_NAMES.CAT_LOCAL_ACTIVE_CHANGE, function (event) {
            syncCatAppearanceLifecycle(event && event.detail);
        });
        window.addEventListener('neko:goodbye-state-cleared', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            syncCatAppearanceLifecycle({
                active: false,
                reason: detail.reason || 'goodbye-state-cleared',
                discardReturnSummary: true,
            });
        });
        window.addEventListener('neko:auto-goodbye:state-change', function (event) {
            observeTierChange(event && event.detail);
        });
        window.addEventListener('neko:return-ball-manual-move', function (event) {
            observeReturnBallManualMove(event && event.detail);
        });
        window.addEventListener('neko:idle-return-ball-state', function (event) {
            observeIdleReturnBallState(event && event.detail);
        });
        window.addEventListener('neko:thought-bubble-pop', function (event) {
            observeThoughtBubblePop(event && event.detail);
        });
        window.addEventListener(EVENT_NAMES.OBSERVATION, function (event) {
            observeExternalObservation(event && event.detail);
        });
        window.addEventListener(EVENT_NAMES.ACTION_RESULT, function (event) {
            observeActionResult(event && event.detail);
        });
        window.addEventListener('neko:idle-chat-minimized-state', function (event) {
            observeDesktopChatMinimized(event && event.detail);
        });
        window.addEventListener('neko:idle-chat-compact-surface-state', function (event) {
            observeCompactSurface(event && event.detail);
        });
        window.addEventListener('neko:compact-surface-layout-change', function (event) {
            observeCompactSurface(event && event.detail);
        });
        window.addEventListener('react-chat-window:chat-surface-mode-change', function (event) {
            observeChatSurfaceMode(event && event.detail);
        });
    }

    function getState(snapshotAt) {
        var timestamp = Number.isFinite(Number(snapshotAt)) ? Number(snapshotAt) : nowMs();
        return {
            active: runtimeState.active,
            entry: runtimeState.entry,
            tier: runtimeState.tier,
            enteredAt: runtimeState.enteredAt,
            updatedAt: runtimeState.updatedAt,
            durationSeconds: currentDurationSeconds(),
            fields: clonePlain(runtimeState.fields),
            recentEventCount: runtimeState.recentEvents.length,
            pendingActionRequest: clonePlain(runtimeState.scheduler.pendingActionRequest),
            activeAction: clonePlain(runtimeState.scheduler.activeAction),
            actionCooldowns: getActiveActionCooldownSnapshot(timestamp),
            clock: {
                lastTickAt: runtimeState.clock.lastTickAt,
                lastUserInteractionAt: runtimeState.clock.lastUserInteractionAt,
                lastActionStartedAt: runtimeState.clock.lastActionStartedAt,
            },
            lastResetReason: runtimeState.lastResetReason,
            returnSummaryDraft: clonePlain(runtimeState.returnSummaryDraft),
        };
    }

    function getRecentEvents() {
        return clonePlain(runtimeState.recentEvents) || [];
    }

    function getReturnSummaryDraft() {
        return clonePlain(runtimeState.returnSummaryDraft);
    }

    function consumeReturnSummaryDraft() {
        var summary = clonePlain(runtimeState.returnSummaryDraft);
        runtimeState.returnSummaryDraft = null;
        return summary;
    }

    window.NekoCatMindContract = Object.freeze({
        EVENT_NAMES: EVENT_NAMES,
        DEBUG_SETTING_KEY: DEBUG_SETTING_KEY,
        DEBUG_QUERY_PARAM: DEBUG_QUERY_PARAM,
        OBSERVATION_PAYLOAD_FIELDS: OBSERVATION_PAYLOAD_FIELDS,
        OBSERVATION_TYPES: OBSERVATION_TYPES,
        TIERS: TIERS,
        ACTION_IDS: ACTION_IDS,
        isDebugEnabled: isDebugEnabled,
    });

    window.nekoCatMind = Object.freeze({
        getState: getState,
        getRecentEvents: getRecentEvents,
        getReturnSummaryDraft: getReturnSummaryDraft,
        consumeReturnSummaryDraft: consumeReturnSummaryDraft,
        getDebugSnapshot: getDebugSnapshot,
        recordDecision: recordDecision,
        acknowledgeActionRequest: acknowledgeActionRequest,
        observe: observe,
        reset: resetRuntime,
    });

    installObservationListeners();
})();
