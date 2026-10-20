/**
 * Pet-care account, role binding, local snapshot, stage-4 care actions and
 * independent entry surface, plus the stage-3 Cat Mind compatibility boundary.
 *
 * The entry surface belongs to the cat form. It is deliberately not a
 * model button, never registers a model floating button, does not read model bounds/managers, and does not observe
 * Cat Mind hover events. Account identity comes from the shared N.E.K.O
 * account session (the current local adapter is the existing OAuth session);
 * role binding comes from the pet-care service.
 */
(function () {
    'use strict';

    // /chat and /chat_full are follower surfaces.  They mirror chat state but
    // do not host the model/cat entity.  Pet-care UI, service writes,
    // lifecycle exits and user-visible acknowledgements must have one owner;
    // otherwise each renderer creates its own modal and its own in-memory
    // exploration coordinator.
    var PET_CARE_FOLLOWER_PATH = /^\/chat(?:_full)?(?:\/|$)/;
    function isPetCareFollowerSurface() {
        try {
            return PET_CARE_FOLLOWER_PATH.test(String(window.location && window.location.pathname || ''));
        } catch (_) { return false; }
    }
    var PET_CARE_SURFACE = Object.freeze({
        kind: isPetCareFollowerSurface() ? 'chat-follower' : 'model-owner',
        ownsUi: !isPetCareFollowerSurface(),
        ownsLifecycle: !isPetCareFollowerSurface(),
        ownsNotices: !isPetCareFollowerSurface(),
    });
    window.nekoPetCareSurface = PET_CARE_SURFACE;
    function isPetCareOwnerSurface() { return PET_CARE_SURFACE.ownsLifecycle === true; }

    var HOVER_DELAY_MS = 400;
    var HIDE_DELAY_MS = 180;
    // Transport timeout only; it is not a pet-care rule, item CD or reward
    // value. A timed-out request never mutates the browser snapshot.
    var CARE_REQUEST_TIMEOUT_MS = 15000;
    var OVERLAY_ID = 'neko-pet-care-entry-overlay';
    var MODES = window.NekoPetCareModeContract && window.NekoPetCareModeContract.MODES;
    var CAT_MIND_ACTION_IDS = window.NekoPetCareModeContract &&
        window.NekoPetCareModeContract.CAT_MIND_ACTION_IDS || [
            'cat1_social_ping', 'cat1_eat_snack', 'cat1_small_move',
            'cat1_play_yarn', 'cat2_nap_feedback', 'cat3_sleep_feedback',
        ];
    var FEATURE_HINT_STORAGE_KEY = 'neko.pet-care.feature-hint-shown';
    var AUTH_NOTICE_STORAGE_KEY = 'neko.pet-care.auth-notice.v1';
    var AUTH_PRESENCE_STORAGE_KEY = 'neko.pet-care.auth-presence.v1';
    var EXPLORATION_NOTICE_STORAGE_KEY = 'neko.pet-care.exploration-notice.v1';
    // Session-scoped presentation lease only. It contains no status values,
    // inventory or reward; it lets a reload keep the nest/gate visible while
    // the authoritative service is temporarily unreachable.
    var EXPLORATION_LEASE_STORAGE_KEY = 'neko.pet-care.exploration-lease.v1';
    // This is only a stale-event lease for correlating an existing user yarn
    // gesture with the cat's later response. It is not an item cooldown,
    // reward limit, or business rule.
    var PLAY_ACTIVITY_LEASE_MS = 30000;
    var state = {
        authStatus: 'unknown', accountId: '', user: null, sessionGeneration: '',
        currentCharacterUid: '', currentCharacterName: '', bindingStatus: 'unknown',
        bindingRoleId: '', serviceStatus: 'unknown', serviceReason: 'initial',
        snapshot: null, settlement: null, careActions: null, optimisticSnapshot: null,
        devTools: null, devFillPending: false,
        generation: 0, reason: 'initial', panelOpen: false,
        hoverVisible: false, oauthPending: false, bindingPending: false,
        catLocalActive: false, catMindCompatibilityEnabled: false,
        featureHintVisible: false, selectedView: 'overview', selectedCategory: '', progressUnlockOpen: false, pendingAction: null,
        actionError: '', retryAction: null, tasks: null, taskLoading: false,
        taskPendingId: '', taskError: '', dailyCheckInPending: false,
        recoveryPendingStatus: '',
        playActions: null, playActivity: null, playPending: null, playRetry: null, playError: '',
        explorationPending: null, explorationError: '', explorationRecallRequested: false,
        explorationAutoCompleteRetryAt: 0,
        explorationReward: null,
        explorationLocations: null, explorationGateActive: false,
    };
    var ui = null, hoverTimer = 0, hideTimer = 0, authRequestGeneration = 0;
    var bindingRequestGeneration = 0, actionRequestGeneration = 0, taskRequestGeneration = 0,
        dailyCheckInGeneration = 0, oauthPollTimer = 0;
    var cooldownRefreshTimer = 0, statusRefreshTimer = 0, playActivityTimer = 0, playActivitySequence = 0;
    var explorationTimer = 0, explorationDepartureTimer = 0, explorationReturnTimer = 0,
        explorationRequestGeneration = 0;
    // A character/model switch can arrive after the renderer has already
    // started replacing the visible cat. Keep the old exploration identity
    // long enough to issue the same server recall used by the nest/return
    // entry; the new character must never inherit the old local snapshot.
    var pendingContextRecall = null;
    var oauthPopup = null, oauthCancelResolver = null, oauthAttemptGeneration = 0;
    var modelExitRecallPromise = null;
    // An account/session replacement must wait for the old pet context to
    // settle. Keep the transition serialized with model exits so a logout
    // event cannot clear the old exploration while its recall is in flight.
    var authContextExitPromise = null;

    function readSessionNotice(key) {
        try {
            var raw = window.sessionStorage && window.sessionStorage.getItem(key);
            if (!raw) return null;
            var value = JSON.parse(raw);
            return value && typeof value === 'object' ? value : null;
        } catch (_) { return null; }
    }
    function writeSessionNotice(key, value) {
        try {
            if (window.sessionStorage) window.sessionStorage.setItem(key, JSON.stringify(value));
        } catch (_) {}
    }
    function removeSessionNotice(key) {
        try { if (window.sessionStorage) window.sessionStorage.removeItem(key); } catch (_) {}
    }
    // Exploration terminal acknowledgement is a device-level presentation
    // fact.  A new renderer/webview or a full reload must not replay the same
    // confirmed activity; sessionStorage alone is not stable across those
    // boundaries.  Keep the existing session slot as a migration/fallback,
    // but prefer localStorage for this one presentation key only.
    function readLocalNotice(key) {
        try {
            var raw = window.localStorage && window.localStorage.getItem(key);
            if (!raw) return null;
            var value = JSON.parse(raw);
            return value && typeof value === 'object' ? value : null;
        } catch (_) { return null; }
    }
    function writeLocalNotice(key, value) {
        try {
            if (window.localStorage) window.localStorage.setItem(key, JSON.stringify(value));
        } catch (_) {}
    }
    function removeLocalNotice(key) {
        try { if (window.localStorage) window.localStorage.removeItem(key); } catch (_) {}
    }
    function readAuthPresence() {
        var value = readSessionNotice(AUTH_PRESENCE_STORAGE_KEY);
        return value && value.status === 'logged-in' ? value : null;
    }
    function writeAuthPresence(status, accountId, sessionGeneration) {
        try {
            if (!window.sessionStorage) return;
            window.sessionStorage.setItem(AUTH_PRESENCE_STORAGE_KEY, JSON.stringify({
                status: status === 'logged-in' ? 'logged-in' : 'logged-out',
                accountId: str(accountId), sessionGeneration: str(sessionGeneration),
                updatedAt: Date.now(),
            }));
        } catch (_) {}
    }
    var petCareNoticeActive = false;
    var petCareNoticeActiveMessage = '';
    var petCareNoticeQueue = [];
    function drainPetCareNoticeQueue() {
        if (petCareNoticeActive || !petCareNoticeQueue.length || !PET_CARE_SURFACE.ownsNotices) return;
        var next = petCareNoticeQueue.shift();
        petCareNoticeActive = true;
        petCareNoticeActiveMessage = next.message;
        var result = null;
        try {
            var dialog = window.showAlertDialog;
            if (typeof dialog === 'function') {
                result = dialog(next.message, { type: next.type || 'info', title: '养宠' });
            } else if (typeof window.showAlert === 'function') {
                result = window.showAlert(next.message, '养宠');
            } else if (typeof window.alert === 'function') {
                window.alert(next.message);
            } else {
                petCareNoticeActive = false;
                petCareNoticeActiveMessage = '';
                drainPetCareNoticeQueue();
                return;
            }
        } catch (_) {
            petCareNoticeActive = false;
            petCareNoticeActiveMessage = '';
            drainPetCareNoticeQueue();
            return;
        }
        if (result && typeof result.then === 'function') {
            result.then(function () {}, function () {}).then(function () {
                petCareNoticeActive = false;
                petCareNoticeActiveMessage = '';
                drainPetCareNoticeQueue();
            });
        } else {
            petCareNoticeActive = false;
            petCareNoticeActiveMessage = '';
            drainPetCareNoticeQueue();
        }
    }
    function showPetCareNotice(message, type) {
        if (!PET_CARE_SURFACE.ownsNotices) return false;
        message = str(message);
        if (!message) return false;
        if (typeof window.showAlertDialog !== 'function' &&
            typeof window.showAlert !== 'function' && typeof window.alert !== 'function') {
            return false;
        }
        // Keep notifications serial within the owner document.  A second
        // state transition is queued behind the visible acknowledgement,
        // never rendered as a second overlay that traps the user.
        if (petCareNoticeActiveMessage === message ||
            petCareNoticeQueue.some(function (item) { return item.message === message; })) return true;
        petCareNoticeQueue.push({ message: message, type: type || 'info' });
        drainPetCareNoticeQueue();
        return true;
    }
    function notifyAuthTransition(previousAuth, nextAuth, reason) {
        if (!PET_CARE_SURFACE.ownsNotices) return;
        var authReason = str(reason);
        if (nextAuth === 'logged-in' && previousAuth !== 'logged-in') {
            var pending = readSessionNotice(AUTH_NOTICE_STORAGE_KEY);
            removeSessionNotice(AUTH_NOTICE_STORAGE_KEY);
            if (!pending && authReason !== 'oauth-complete' && authReason !== 'external-session-change') return;
            var message = pending && str(pending.message)
                ? pending.message
                : '已重新登录 N.E.K.O 账号，正在确认当前角色和账号宠物。';
            window.setTimeout(function () { showPetCareNotice(message, 'info'); }, 0);
            return;
        }
        if (nextAuth !== 'logged-out' || previousAuth !== 'logged-in') return;
        if (authReason === 'logout') {
            window.setTimeout(function () { showPetCareNotice('已退出 N.E.K.O 账号，养宠数据不会在本地继续使用。', 'success'); }, 0);
            return;
        }
        // An external logout may happen while this surface is not able to ask
        // the user for acknowledgement. Keep the explanation for the next
        // successful login instead of pretending a transient network failure
        // was a confirmed logout.
        if (readSessionNotice(AUTH_NOTICE_STORAGE_KEY)) return;
        writeSessionNotice(AUTH_NOTICE_STORAGE_KEY, {
            message: '上次账号会话已在其他窗口或设备退出，养宠功能已重新校验。',
            updatedAt: Date.now(),
        });
    }
    function explorationNoticeKey(exploration) {
        var id = explorationId(exploration);
        var status = str(exploration && exploration.status).toLowerCase();
        return id && status && ['completed', 'recalled', 'returned'].indexOf(status) >= 0
            ? id : '';
    }
    function explorationNoticeMatches(previous, activityId) {
        if (!previous || typeof previous !== 'object' || !activityId) return false;
        var previousActivityId = str(previous.activityId);
        if (!previousActivityId) {
            // Migrate the earlier session-only id:status format.  A terminal
            // exploration id is unique, so the old status suffix must not
            // make the same activity appear as a new notification.
            var legacyKey = str(previous.key);
            previousActivityId = legacyKey ? legacyKey.split(':')[0] : '';
        }
        if (previousActivityId !== activityId) return false;
        return !previous.accountId || previous.accountId === str(state.accountId);
    }
    function notifyExplorationTerminal(exploration, reward, operation) {
        if (!PET_CARE_SURFACE.ownsNotices) return;
        var activityId = explorationNoticeKey(exploration);
        if (!activityId) return;
        var previous = readLocalNotice(EXPLORATION_NOTICE_STORAGE_KEY) ||
            readSessionNotice(EXPLORATION_NOTICE_STORAGE_KEY);
        if (explorationNoticeMatches(previous, activityId)) return;
        var noticeRecord = {
            activityId: activityId, key: activityId,
            accountId: str(state.accountId), characterUid: str(state.currentCharacterUid),
            status: str(exploration && exploration.status).toLowerCase(), updatedAt: Date.now(),
        };
        writeLocalNotice(EXPLORATION_NOTICE_STORAGE_KEY, noticeRecord);
        writeSessionNotice(EXPLORATION_NOTICE_STORAGE_KEY, noticeRecord);
        var rewardLabel = explorationRewardText(reward || (exploration && (exploration.rewards || exploration.reward)));
        var status = str(exploration && exploration.status).toLowerCase();
        var title = operation === 'complete' || status === 'completed' ? '探索已完成' : '猫已召回';
        var message = rewardLabel ? title + '，服务已确认收获：' + rewardLabel : title + '，服务已确认结算。';
        window.setTimeout(function () {
            // Keep the persistent dedupe only after a confirmable surface
            // actually accepted the notice.  Early page boot can precede the
            // shared dialog bundle; in that case a later refresh/re-login
            // must still be allowed to explain the confirmed result.
            if (!showPetCareNotice(message, 'success')) {
                var current = readLocalNotice(EXPLORATION_NOTICE_STORAGE_KEY) ||
                    readSessionNotice(EXPLORATION_NOTICE_STORAGE_KEY);
                if (explorationNoticeMatches(current, activityId)) {
                    removeLocalNotice(EXPLORATION_NOTICE_STORAGE_KEY);
                    removeSessionNotice(EXPLORATION_NOTICE_STORAGE_KEY);
                }
            }
        }, 0);
    }

    function str(value) { return typeof value === 'string' ? value.trim() : ''; }
    function snap() {
        return Object.freeze({
            authStatus: state.authStatus, accountId: state.accountId,
            user: state.user ? Object.assign({}, state.user) : null,
            sessionGeneration: state.sessionGeneration,
            currentCharacterUid: state.currentCharacterUid,
            currentCharacterName: state.currentCharacterName,
            bindingStatus: state.bindingStatus, bindingRoleId: state.bindingRoleId,
            serviceStatus: state.serviceStatus, serviceReason: state.serviceReason,
            snapshot: state.snapshot, settlement: state.settlement,
            careActions: state.careActions, optimisticSnapshot: state.optimisticSnapshot,
            devTools: state.devTools,
            devFillPending: state.devFillPending,
            generation: state.generation,
            reason: state.reason, panelOpen: state.panelOpen,
            hoverVisible: state.hoverVisible, oauthPending: state.oauthPending,
            bindingPending: state.bindingPending,
            catLocalActive: state.catLocalActive,
            catMindCompatibilityEnabled: state.catMindCompatibilityEnabled,
            featureHintVisible: state.featureHintVisible,
            selectedView: state.selectedView,
            selectedCategory: state.selectedCategory,
            pendingAction: state.pendingAction ? Object.assign({}, state.pendingAction) : null,
            actionError: state.actionError,
            retryAction: state.retryAction ? Object.assign({}, state.retryAction) : null,
            tasks: state.tasks, taskLoading: state.taskLoading,
            taskPendingId: state.taskPendingId, taskError: state.taskError,
            dailyCheckInPending: state.dailyCheckInPending,
            playActions: state.playActions,
            playActivity: state.playActivity ? Object.assign({}, state.playActivity) : null,
            playPending: state.playPending ? Object.assign({}, state.playPending) : null,
            playRetry: state.playRetry ? Object.assign({}, state.playRetry) : null,
            playError: state.playError,
            exploration: state.snapshot && state.snapshot.exploration || null,
            explorationPending: state.explorationPending ? Object.assign({}, state.explorationPending) : null,
            explorationError: state.explorationError,
            explorationRecallRequested: state.explorationRecallRequested,
            explorationAutoCompleteRetryAt: state.explorationAutoCompleteRetryAt,
            explorationReward: state.explorationReward,
            explorationLocations: state.explorationLocations,
            explorationGateActive: state.explorationGateActive,
        });
    }
    function emit(name, detail) {
        if (typeof window === 'undefined' || typeof window.dispatchEvent !== 'function' ||
            typeof CustomEvent !== 'function') return;
        try { window.dispatchEvent(new CustomEvent(name, { detail: detail })); } catch (_) {}
    }
    function mode(mode, reason) {
        var api = window.nekoPetCareMode;
        if (!api || typeof api.setMode !== 'function' || !MODES || !mode) return;
        try { api.setMode(mode, { reason: reason || 'pet-care-entry' }); } catch (_) {}
    }
    function identity() {
        var api = window.appChatAvatarState;
        if (api && typeof api.getIdentity === 'function') {
            try { var value = api.getIdentity(); if (value && value.uid) return value; } catch (_) {}
        }
        return null;
    }
    function context(detail) {
        var api = window.nekoPetCareMode;
        if (api && typeof api.updateContext === 'function') {
            var value = api.updateContext(detail || {});
            state.generation = Number(value && value.generation) || state.generation;
        } else state.generation += 1;
    }
    function cancelPetCarePresentation(reason) {
        var presentation = window.NekoCatIdlePresentation;
        if (presentation && typeof presentation.cancelPetCare === 'function') {
            try { presentation.cancelPetCare({ reason: reason || 'pet-care-context-invalidated' }); } catch (_) {}
        }
    }
    function invalidatePetContext(reason, options) {
        var next = options && typeof options === 'object' ? options : {};
        var keepAccount = next.clearAccount !== true;
        cancelPetCarePresentation(reason);
        state.bindingRoleId = '';
        state.snapshot = null; state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
        state.devTools = null; state.devFillPending = false;
        state.pendingAction = null; state.actionError = ''; state.retryAction = null;
        state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
        state.taskError = ''; state.dailyCheckInPending = false;
        clearPlayActivityTimer();
        state.playActions = null; state.playActivity = null; state.playPending = null; state.playRetry = null; state.playError = '';
        clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
        state.explorationReward = null;
        state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
        state.explorationLocations = null; state.explorationGateActive = false;
        clearExplorationLease();
        taskRequestGeneration += 1; dailyCheckInGeneration += 1;
        state.selectedCategory = '';
        state.progressUnlockOpen = false;
        state.selectedView = 'overview';
        actionRequestGeneration += 1;
        if (!keepAccount) {
            state.accountId = '';
            state.user = null;
            state.sessionGeneration = '';
        }
        context({
            accountId: keepAccount ? state.accountId : '',
            currentCharacterUid: state.currentCharacterUid,
            boundRoleId: '',
            reason: reason || 'pet-care-context-invalidated',
            forceNewGeneration: true,
        });
        mode(MODES && MODES.PET_UNAVAILABLE, reason || 'pet-care-context-invalidated');
        syncCatMindCompatibility(reason || 'pet-care-context-invalidated');
    }
    function isPetCareCompatibilityReady() {
        return state.authStatus === 'logged-in' &&
            state.bindingStatus === 'bound' &&
            state.serviceStatus === 'ready' &&
            !!state.snapshot && state.catLocalActive;
    }
    function showFeatureHintOnce() {
        if (!isPetCareCompatibilityReady() || state.featureHintVisible) return;
        var anchor = anchorState();
        if (!anchor || !anchor.visible || !anchor.rect || anchor.busy) return;
        var shown = false;
        try {
            shown = !!(window.localStorage &&
                window.localStorage.getItem(FEATURE_HINT_STORAGE_KEY) === '1');
        } catch (_) {}
        if (shown) return;
        try {
            if (window.localStorage) window.localStorage.setItem(FEATURE_HINT_STORAGE_KEY, '1');
        } catch (_) {}
        state.featureHintVisible = true;
        // The locked interaction is hover-to-reveal.  The first-ready hint is
        // only an acknowledgement; it must not open the surface by itself or
        // create a second open path before the user moves over the cat.
        emit('neko:pet-care:feature-hint', {
            message: '已开启养宠功能，可在猫形态周围打开养宠面板。',
            storageKey: FEATURE_HINT_STORAGE_KEY,
        });
    }
    function syncCatMindCompatibility(reason) {
        var api = window.nekoPetCareMode;
        var enabled = isPetCareCompatibilityReady();
        state.catMindCompatibilityEnabled = enabled;
        if (api && typeof api.setCatMindCompatibility === 'function') {
            try {
            api.setCatMindCompatibility({
                suppressed: enabled,
                actionIds: CAT_MIND_ACTION_IDS,
                // Both entering and leaving compatibility must use the
                // existing source-filtered cleanup path. On leave this
                // prevents a Cat Mind bubble/audio/tail from outliving the
                // authenticated cat-form context.
                cleanup: true,
                reason: reason || (enabled ? 'pet-care-ready' : 'pet-care-unavailable'),
            });
            } catch (_) {}
        }
        if (enabled) showFeatureHintOnce();
        render();
    }
    function setCharacter(detail, reason) {
        var old = identity();
        var uid = detail && typeof detail === 'object'
            ? str(detail.character_uid || detail.currentCharacterUid || detail.characterUid) : '';
        var name = detail && typeof detail === 'object'
            ? str(detail.character_name || detail.currentCharacterName || detail.name) : '';
        uid = uid || str(old && old.uid); name = name || str(old && old.name);
        if (uid === state.currentCharacterUid && name === state.currentCharacterName) return;
        cancelPetCarePresentation(reason || 'character-change');
        state.currentCharacterUid = uid; state.currentCharacterName = name;
        state.bindingStatus = 'unknown'; state.bindingRoleId = ''; state.snapshot = null;
        state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
        state.devTools = null; state.devFillPending = false;
        clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
        state.playPending = null; state.playRetry = null; state.playError = '';
        clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
        state.explorationReward = null;
        state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
        state.explorationLocations = null; state.explorationGateActive = false;
        state.pendingAction = null; state.actionError = ''; state.retryAction = null;
        state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
        state.taskError = ''; state.dailyCheckInPending = false;
        taskRequestGeneration += 1; dailyCheckInGeneration += 1;
        state.selectedCategory = ''; state.selectedView = 'overview'; state.progressUnlockOpen = false; actionRequestGeneration += 1;
        state.serviceStatus = state.authStatus === 'logged-in' ? 'loading' : 'unknown';
        state.reason = reason || 'character-change';
        context({ accountId: state.accountId, currentCharacterUid: uid, boundRoleId: '',
            reason: state.reason });
        if (state.authStatus === 'logged-in') mode(MODES && MODES.PET_LOADING, state.reason);
        syncCatMindCompatibility(state.reason);
        emit('neko:pet-care:identity-change', snap()); render();
        if (state.authStatus === 'logged-in') refreshBinding('character-change');
    }

    // A recall can overlap an account/session replacement or a newer role
    // event.  The lifecycle owner must never apply an older context request
    // after that fence moved; otherwise a late model event could reattach the
    // old role to a newly logged-in account.  The mode adapter supplies the
    // same generation/account/role facts in context-change-request, while
    // direct display events use the current in-memory fence captured below.
    function contextFenceMatches(expected) {
        if (!expected || typeof expected !== 'object') return true;
        if (expected.generation != null && expected.generation !== '' &&
            Number(expected.generation) !== Number(state.generation)) return false;
        if (expected.accountId != null && str(expected.accountId) !== str(state.accountId)) return false;
        if (expected.currentCharacterUid != null &&
            str(expected.currentCharacterUid) !== str(state.currentCharacterUid)) return false;
        if (expected.boundRoleId != null && str(expected.boundRoleId) !== str(state.bindingRoleId)) return false;
        return true;
    }

    function waitForExplorationWriteIdle(activityId) {
        var startedAt = Date.now();
        return new Promise(function (resolve) {
            function check() {
                var active = currentExploration();
                if (!explorationActive(active) || explorationId(active) !== activityId) {
                    resolve(true); return;
                }
                if (!state.explorationPending) { resolve(true); return; }
                if (Date.now() - startedAt >= CARE_REQUEST_TIMEOUT_MS + 1000) {
                    resolve(false); return;
                }
                window.setTimeout(check, 60);
            }
            check();
        });
    }

    // Single lifecycle coordinator for every path that makes the model/cat
    // surface leave the current cat form.  It owns the one service recall
    // request and returns false on an unconfirmed failure so callers can keep
    // the old presentation instead of switching away with a live activity.
    function ensureExplorationEndedBeforeModelExit(options) {
        options = options && typeof options === 'object' ? options : {};
        if (!isPetCareOwnerSurface()) return Promise.resolve(true);
        var exploration = currentExploration();
        if (!explorationActive(exploration) || !state.currentCharacterUid) return Promise.resolve(true);
        if (modelExitRecallPromise) return modelExitRecallPromise;
        var activityId = explorationId(exploration);
        if (!activityId) return Promise.resolve(true);
        var generation = explorationGeneration(exploration);
        var payload = {
            explorationRecall: true,
            exploration_id: activityId, explorationId: activityId, activityId: activityId,
            generation: generation, activityGeneration: generation,
            source: str(options.source) || 'model-exit', presentation: 'model-return',
            suppressNotice: options.suppressNotice === true,
        };
        pendingContextRecall = {
            activityId: activityId, characterUid: state.currentCharacterUid,
            accountId: state.accountId, sessionGeneration: state.sessionGeneration,
        };
        state.explorationRecallRequested = true;
        state.explorationError = '';
        render();
        modelExitRecallPromise = (async function () {
            if (state.explorationPending) {
                var idle = await waitForExplorationWriteIdle(activityId);
                if (!idle) return false;
            }
            var latest = currentExploration();
            if (!explorationActive(latest)) return true;
            var confirmed = await settleExploration('recall', payload);
            var terminal = confirmed && !explorationActive(currentExploration());
            if (terminal && pendingContextRecall && pendingContextRecall.activityId === activityId) {
                pendingContextRecall = null;
            }
            return terminal;
        })().finally(function () {
            modelExitRecallPromise = null;
            render();
        });
        return modelExitRecallPromise;
    }

    function requestContextExplorationRecall(detail, reason) {
        if (!isPetCareOwnerSurface()) return false;
        var exploration = currentExploration();
        if (!explorationActive(exploration) || !state.currentCharacterUid ||
            pendingContextRecall || modelExitRecallPromise) return false;
        var activityId = explorationId(exploration);
        if (!activityId) return false;
        var payload = {
            explorationRecall: true,
            exploration_id: activityId, explorationId: activityId, activityId: activityId,
            generation: explorationGeneration(exploration),
            activityGeneration: explorationGeneration(exploration),
            source: reason || 'character-switch', presentation: 'model-return',
        };
        pendingContextRecall = {
            activityId: activityId, characterUid: state.currentCharacterUid,
            accountId: state.accountId, sessionGeneration: state.sessionGeneration,
        };
        state.explorationRecallRequested = true;
        // The model-return surface is the single visual owner.  It invokes
        // ensureExplorationEndedBeforeModelExit before changing presentation;
        // showCurrentModel also calls that coordinator as the final guard for
        // direct model restore paths that do not emit a return event.
        emit('neko:pet-care:exploration-recall-request', payload);
        return true;
    }
    function ensureExplorationEndedBeforeAuthChange(nextAuth, nextAccount, nextSession, reason) {
        if (!isPetCareOwnerSurface()) return Promise.resolve(true);
        var contextChange = nextAuth !== state.authStatus ||
            str(nextAccount) !== str(state.accountId) ||
            str(nextSession) !== str(state.sessionGeneration);
        if (!contextChange || !explorationActive(currentExploration()) || !state.currentCharacterUid) {
            return Promise.resolve(true);
        }
        if (authContextExitPromise) return authContextExitPromise;
        authContextExitPromise = ensureExplorationEndedBeforeModelExit({
            source: reason ? 'auth-context-' + reason : 'auth-context-change',
            // The following auth transition supplies its own one-time
            // confirmation. Do not queue a second modal for the intermediate
            // exploration terminal result.
            suppressNotice: true,
        }).finally(function () { authContextExitPromise = null; });
        return authContextExitPromise;
    }
    async function setAuth(next, reason, expectedRequestGeneration) {
        var previousAuth = state.authStatus;
        // A reload recreates the in-memory state as "unknown".  Retain only
        // the previous session presence so a server-confirmed logout during
        // refresh can still be explained on the next login; no pet values or
        // credentials are persisted here.
        var persistedPresence = readAuthPresence();
        var persistedExplorationLease = readExplorationLease();
        var persistedAccountId = str(persistedPresence && persistedPresence.accountId);
        var observedPreviousAuth = previousAuth === 'logged-in' || persistedPresence
            ? 'logged-in' : previousAuth;
        var logged = !!(next && next.logged_in && str(next.local_user_id));
        var auth = logged ? 'logged-in' : 'logged-out';
        var account = logged ? str(next.local_user_id) : '';
        // The local OAuth adapter exposes an opaque login ``session_id``;
        // remote adapters may expose a non-authorizing ``session_generation``.
        // Both are only a context fence for the UI.  Token refresh must keep
        // the same value, while a new credential publication changes it.
        var session = next && next.session_generation != null
            ? String(next.session_generation).trim()
            : (next && next.session_id != null ? String(next.session_id).trim() : '');
        var changed = auth !== state.authStatus || account !== state.accountId ||
            session !== state.sessionGeneration;
        // A different account must never inherit a presentation-only lease
        // from the previous account.  A same-account lease is retained until
        // the next successful service read can confirm its terminal state.
        if (logged && persistedExplorationLease &&
            persistedExplorationLease.accountId !== account) {
            clearExplorationLease();
            persistedExplorationLease = null;
        }
        // Do not invalidate the old account/role snapshot before the service
        // confirms recall.  This is especially important for an external
        // logout/account replacement: the status response can arrive while
        // the old exploration is still active, and clearing it here would
        // make the activity impossible to retry from this surface.
        var recalled = !changed || await ensureExplorationEndedBeforeAuthChange(
            auth, account, session, reason || 'auth-status');
        // A newer status request may have superseded this one while the
        // service recall was in flight. Do not let this stale response undo
        // the newer account transition.
        if (expectedRequestGeneration != null &&
            expectedRequestGeneration !== authRequestGeneration) return snap();
        if (!recalled) {
            state.explorationError = 'auth-transition-recall-failed';
            state.serviceStatus = 'unavailable';
            state.serviceReason = 'auth-transition-recall-failed';
            state.reason = state.serviceReason;
            render();
            showPetCareNotice('账号切换前的探索结算未获服务确认，探索状态已保留，请重试。', 'warning');
            return snap();
        }
        if (changed) cancelPetCarePresentation(reason || 'auth-status');
        // A logout or account/session replacement no longer has an old
        // activity to recall: the serialized preflight above settled it.
        if (!logged || (changed && (account !== state.accountId ||
            session !== state.sessionGeneration))) pendingContextRecall = null;
        state.authStatus = auth; state.accountId = account;
        state.user = logged && next.user && typeof next.user === 'object'
            ? Object.assign({}, next.user) : null;
        state.sessionGeneration = session; state.reason = reason || 'auth-status';
        if (!logged) {
            if (state.oauthPending) cancelOAuthLogin();
            state.bindingStatus = 'unknown'; state.bindingRoleId = ''; state.snapshot = null;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            state.devTools = null; state.devFillPending = false;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playRetry = null; state.playError = '';
            clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
            state.explorationReward = null;
            // A reload can observe an already-confirmed external logout after
            // the in-memory snapshot has disappeared.  Keep only the
            // same-account, session-scoped activity lease so the next login
            // can re-read the authoritative activity and settle it.  The
            // lease contains no status, inventory or reward and does not
            // enable writes while logged out.
            var retainAwayLease = persistedExplorationLease &&
                (!persistedAccountId || persistedExplorationLease.accountId === persistedAccountId) &&
                (!state.accountId || persistedExplorationLease.accountId === state.accountId);
            if (retainAwayLease) {
                writeSessionNotice(AUTH_NOTICE_STORAGE_KEY, {
                    message: '上次账号会话已退出，养宠探索将在重新登录后继续确认。',
                    updatedAt: Date.now(),
                });
            } else {
                clearExplorationLease();
            }
            state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
            state.explorationLocations = null; state.explorationGateActive = false;
            state.pendingAction = null; state.actionError = ''; state.retryAction = null;
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
            state.taskError = ''; state.dailyCheckInPending = false;
            taskRequestGeneration += 1; dailyCheckInGeneration += 1;
            state.selectedCategory = ''; state.progressUnlockOpen = false; actionRequestGeneration += 1;
            state.serviceStatus = 'unknown'; state.serviceReason = 'session-invalid';
            state.panelOpen = false;
            if (changed) {
                context({ accountId: '', currentCharacterUid: state.currentCharacterUid,
                    boundRoleId: '', reason: state.reason, forceNewGeneration: true });
            }
            if (state.hoverVisible) visible(false);
            mode(MODES && MODES.SESSION_INVALID, state.reason);
        } else if (changed) {
            state.bindingStatus = 'unknown'; state.bindingRoleId = ''; state.snapshot = null;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            state.devTools = null; state.devFillPending = false;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playRetry = null; state.playError = '';
            clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
            state.explorationReward = null;
            state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
            state.explorationLocations = null; state.explorationGateActive = false;
            state.pendingAction = null; state.actionError = ''; state.retryAction = null;
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
            state.taskError = ''; state.dailyCheckInPending = false;
            taskRequestGeneration += 1; dailyCheckInGeneration += 1;
            state.selectedCategory = ''; state.progressUnlockOpen = false; actionRequestGeneration += 1;
            state.serviceStatus = 'loading'; state.serviceReason = 'binding-loading';
            context({ accountId: account, currentCharacterUid: state.currentCharacterUid,
                boundRoleId: '', reason: state.reason, forceNewGeneration: true });
            mode(MODES && MODES.PET_LOADING, state.reason);
        }
        syncCatMindCompatibility(state.reason);
        emit('neko:pet-care:auth-change', snap()); render();
        notifyAuthTransition(observedPreviousAuth, auth, reason || state.reason);
        writeAuthPresence(auth, account, session);
        // Re-reading OAuth status is cheap and event-driven. Do not re-fetch
        // the long-lived pet snapshot when the account/session did not change.
        return logged && !state.bindingPending && (changed ||
            state.bindingStatus === 'unknown' || state.serviceStatus === 'unavailable')
            ? refreshBinding(reason || 'auth-refresh') : Promise.resolve(snap());
    }
    function isFiniteValue(value) {
        if (typeof value === 'number') return Number.isFinite(value);
        if (typeof value === 'string' && value.trim() !== '') return Number.isFinite(Number(value));
        return false;
    }
    function isCompleteSnapshot(snapshot, accountId, boundRoleId) {
        if (!snapshot || typeof snapshot !== 'object') return false;
        var schemaVersion = Number(snapshot.schema_version);
        if (!Number.isInteger(schemaVersion) || schemaVersion < 1) return false;
        if (str(snapshot.account_id) !== accountId || str(snapshot.bound_role_id) !== boundRoleId ||
            !str(snapshot.pet_id) || !str(snapshot.last_settled_at)) return false;
        var values = snapshot.status_values;
        if (!values || typeof values !== 'object' ||
            !['satiety', 'energy', 'cleanliness', 'mood', 'progress'].every(function (key) {
                if (!isFiniteValue(values[key])) return false;
                var numeric = Number(values[key]);
                return numeric >= 0 && (key === 'progress' || numeric <= 100);
            })) return false;
        if (!snapshot.negative_status || typeof snapshot.negative_status !== 'object') return false;
        if (['sick', 'sulk'].some(function (key) {
            return snapshot.negative_status[key] != null &&
                (!snapshot.negative_status[key] || typeof snapshot.negative_status[key] !== 'object' ||
                    typeof snapshot.negative_status[key].active !== 'boolean');
        })) return false;
        if (!snapshot.inventory || typeof snapshot.inventory !== 'object' ||
            !snapshot.tasks || typeof snapshot.tasks !== 'object' || !Array.isArray(snapshot.unlocks) ||
            !Object.prototype.hasOwnProperty.call(snapshot, 'exploration')) return false;
        return true;
    }
    function applyBinding(next, expected) {
        next = next && typeof next === 'object' ? next : {};
        var account = str(next.accountId || next.account_id || next.local_user_id || state.accountId);
        var uid = str(next.currentCharacterUid || next.current_character_uid || next.character_uid || next.characterUid ||
            state.currentCharacterUid);
        if (state.authStatus !== 'logged-in' || account !== state.accountId ||
            uid !== state.currentCharacterUid ||
            (expected != null && Number(expected) !== state.generation)) return false;
        var status = str(next.status || next.bindingStatus || next.binding_status).toLowerCase();
        if (status === 'needs_binding') status = 'needs-binding';
        if (['bound', 'unbound', 'needs-binding', 'unknown', 'unavailable'].indexOf(status) < 0) status = 'unknown';
        state.bindingStatus = status;
        state.bindingRoleId = status === 'bound' ? str(next.boundRoleId || next.bound_role_id) : '';
        state.serviceReason = str(next.reason || next.detail) || 'binding-confirmed';
        var candidateSnapshot = next.snapshot && typeof next.snapshot === 'object' ? next.snapshot : null;
        var complete = status === 'bound' && isCompleteSnapshot(candidateSnapshot, account, state.bindingRoleId);
        state.snapshot = complete ? candidateSnapshot : null;
        state.settlement = complete && next.settlement && typeof next.settlement === 'object'
            ? next.settlement : null;
        state.careActions = complete && next.care_actions && typeof next.care_actions === 'object'
            ? next.care_actions : null;
        state.playActions = complete && next.play_actions && typeof next.play_actions === 'object'
            ? next.play_actions : (state.careActions && state.careActions.playing) || null;
        state.devTools = complete && next.dev_tools && typeof next.dev_tools === 'object'
            ? next.dev_tools : null;
        state.devFillPending = false;
        state.explorationLocations = complete && (next.locations || next.exploration_locations || next.explorationLocations)
            ? (next.locations || next.exploration_locations || next.explorationLocations) : null;
        var boundExploration = candidateSnapshot && candidateSnapshot.exploration;
        state.explorationReward = boundExploration && !explorationActive(boundExploration)
            ? (boundExploration.rewards || boundExploration.reward || null) : null;
        state.playActivity = null; state.playPending = null; state.playRetry = null; state.playError = '';
        state.optimisticSnapshot = null;
        if (!complete) { state.selectedCategory = ''; state.progressUnlockOpen = false; }
        state.serviceStatus = status === 'unavailable' ? 'unavailable' :
            status === 'bound' && !complete ? 'unavailable' : 'ready';
        if (status === 'bound' && candidateSnapshot && !complete) state.serviceReason = 'snapshot-incomplete';
        state.reason = state.serviceReason;
        context({ accountId: state.accountId, currentCharacterUid: state.currentCharacterUid,
            boundRoleId: state.bindingRoleId, reason: state.reason });
        // Context must be updated before the server-confirmed activity gate is
        // rebuilt. Otherwise updateContext() sees a role change and releases
        // the gate that reconcileExplorationFromSnapshot() just installed.
        if (complete) reconcileExplorationFromSnapshot(candidateSnapshot, 'binding-snapshot');
        // A refresh or a re-login can observe a terminal activity without
        // going through the POST completion/recall response that normally
        // shows the acknowledgement.  The terminal notice is presentation
        // only and is deduplicated by account/activity id in persistent
        // localStorage (with the old session record as a migration fallback);
        // it never creates a reward or changes the authoritative snapshot.
        if (complete && boundExploration && !explorationActive(boundExploration)) {
            var terminalStatus = str(boundExploration.status).toLowerCase();
            if (['completed', 'recalled', 'returned'].indexOf(terminalStatus) >= 0) {
                notifyExplorationTerminal(boundExploration, state.explorationReward,
                    terminalStatus === 'completed' ? 'complete' : 'recall');
            }
        }
        // Exploration is an activity gate layered over the bound pet mode;
        // it must not turn a valid bound account into the ordinary/unavailable
        // mode while the nest and unified return entry remain usable.
        if (status === 'bound' && complete) mode(MODES && MODES.PET_AVAILABLE, state.reason);
        else mode(MODES && MODES.PET_UNAVAILABLE, state.reason);
        syncCatMindCompatibility(state.reason);
        emit('neko:pet-care:binding-change', snap()); render();
        // The user may open the panel while binding is still in flight. Once
        // the authoritative snapshot becomes ready, reconcile the task
        // collection even though the panel was not opened again.
        if (complete && state.panelOpen) loadTasksWhenPanelOpens();
        return true;
    }
    function setBindingResult(next) {
        next = next && typeof next === 'object' ? next : {};
        var generation = next.generation == null ? state.generation : Number(next.generation);
        if (!Number.isFinite(generation) || generation !== state.generation) return snap();
        applyBinding(next, generation); return snap();
    }
    function clearTimers() {
        if (hoverTimer) { window.clearTimeout(hoverTimer); hoverTimer = 0; }
        if (hideTimer) { window.clearTimeout(hideTimer); hideTimer = 0; }
        if (cooldownRefreshTimer) { window.clearTimeout(cooldownRefreshTimer); cooldownRefreshTimer = 0; }
        if (statusRefreshTimer) { window.clearTimeout(statusRefreshTimer); statusRefreshTimer = 0; }
    }
    function visible(value) {
        state.hoverVisible = !!value;
        if (ui) {
            ui.root.dataset.visible = value ? 'true' : 'false';
            ui.root.dataset.expanded = state.panelOpen ? 'true' : 'false';
        }
        emit('neko:pet-care:entry-visibility', snap());
    }
    function hideSoon() {
        if (state.panelOpen) return;
        if (hideTimer) window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(function () {
            hideTimer = 0; if (!state.panelOpen) visible(false);
        }, HIDE_DELAY_MS);
    }
    function showSoon() {
        if (!state.catLocalActive) return;
        if (hideTimer) { window.clearTimeout(hideTimer); hideTimer = 0; }
        if (anchorBusy()) return;
        if (state.hoverVisible || hoverTimer) return;
        hoverTimer = window.setTimeout(function () {
            hoverTimer = 0;
            var current = anchorState();
            if (current && current.visible && current.rect && !current.busy) {
                // The demo's surface is the entry itself. There is no
                // separate "养宠·已绑定" launcher in front of it.
                state.panelOpen = true;
                visible(true);
                render();
                positionOverlay(current.rect);
                loadTasksWhenPanelOpens();
            }
        }, HOVER_DELAY_MS);
    }
    function anchorState() {
        var anchor = window.nekoCatInteractionAnchor;
        if (!anchor || typeof anchor.getState !== 'function') return null;
        try { return anchor.getState(); } catch (_) { return null; }
    }
    function adoptCatFactFromAnchor(reason) {
        if (state.catLocalActive) return false;
        // During an active exploration the shared return container hosts the
        // nest asset and recall hit region. Its rectangle is intentionally
        // still available for panel placement, but it is not evidence that
        // the cat entity has returned to cat form.
        if (explorationActive(currentExploration())) return false;
        try {
            if (window.nekoPetCareMode && typeof window.nekoPetCareMode.isExplorationAway === 'function' &&
                window.nekoPetCareMode.isExplorationAway()) return false;
        } catch (_) {}
        var current = anchorState();
        if (!current || current.visible !== true || !current.rect) return false;
        // A renderer can start after the cat-local-active event was emitted.
        // The semantic anchor is the only fallback: it reports the visible
        // cat-form surface without exposing model managers or implementation
        // details. It never infers a hidden/model state from absence alone.
        state.catLocalActive = true;
        var modeApi = window.nekoPetCareMode;
        if (modeApi && typeof modeApi.setCatLocalActive === 'function') {
            try { modeApi.setCatLocalActive(true, { reason: reason || 'semantic-anchor-visible' }); } catch (_) {}
        }
        if (isPetCareCompatibilityReady()) mode(MODES && MODES.PET_AVAILABLE, reason || 'semantic-anchor-visible');
        syncCatMindCompatibility(reason || 'semantic-anchor-visible');
        syncAnchor();
        return true;
    }
    function anchorBusy() {
        var current = anchorState();
        return !!(current && current.busy);
    }
    function pointInRect(x, y, rect, padding) {
        return !!rect && x >= rect.left - padding && x <= rect.right + padding &&
            y >= rect.top - padding && y <= rect.bottom + padding;
    }
    function syncAnchor() {
        if (!ui) return null;
        var current = anchorState();
        var away = explorationActive(currentExploration());
        if (away) {
            // The cat entity is intentionally absent during exploration. The
            // panel remains anchored at its last position and continues to
            // expose the nest, note, letters and recall entry.
            ui.root.dataset.available = 'true';
            if (current && current.rect) positionOverlay(current.rect);
            return current;
        }
        var available = !!(state.catLocalActive && current && current.visible && current.rect && !current.busy);
        ui.root.dataset.available = available ? 'true' : 'false';
        if (available) {
            positionOverlay(current.rect);
            showFeatureHintOnce();
        }
        else {
            clearTimers();
            if (state.panelOpen || state.hoverVisible) closePanel({ cancelOAuth: false });
        }
        return current;
    }
    function handlePointerMove(event) {
        if (!event || event.pointerType === 'touch') return;
        if (!state.catLocalActive) return;
        var current = syncAnchor();
        if (!current || !current.visible || current.busy) return;
        var rect = current.rect;
        var x = Number(event.clientX) || 0, y = Number(event.clientY) || 0;
        var overCat = pointInRect(x, y, rect, 24);
        var overEntry = !!(ui && ui.root && event.target &&
            typeof ui.root.contains === 'function' && ui.root.contains(event.target));
        if (overCat || overEntry) {
            if (!state.hoverVisible) showSoon();
        } else if (!state.panelOpen) {
            if (hoverTimer) { window.clearTimeout(hoverTimer); hoverTimer = 0; }
            hideSoon();
        }
    }
    function positionOverlay() {
        if (!ui) return;
        /* The anchor is a semantic app contract; no model manager is read here. */
        var current = arguments.length ? { rect: arguments[0] } : anchorState();
        var rect = current && current.visible !== false && current.rect;
        if (!rect) return;
        var anchorWidth = Number(rect.width) || (rect.right - rect.left) || 1;
        var anchorHeight = Number(rect.height) || (rect.bottom - rect.top) || 1;
        var centerX = Number(rect.left) + (anchorWidth / 2);
        var centerY = Number(rect.top) + (anchorHeight / 2);
        // The desktop Pet window is already a transparent full-work-area
        // surface. The selected demo has one fixed 760x560 coordinate system:
        // the cat slot is exactly (380, 280), the center of that surface. The
        // anchor only translates that system; it never chooses a side,
        // candidate, bottom drawer, or viewport-clamped replacement.
        var left = centerX - 380;
        var top = centerY - 280;
        // Real Chromium exposes CSSStyleDeclaration#setProperty.  Keep the
        // geometry path tolerant of the tiny style doubles used by the
        // renderer contract tests; this is only a CSS custom property and
        // must never make the lifecycle fail closed.
        if (ui.panel.style && typeof ui.panel.style.setProperty === 'function') {
            ui.panel.style.setProperty('--pet-scene-center', '380px');
        } else if (ui.panel.style) {
            ui.panel.style['--pet-scene-center'] = '380px';
        }
        ui.root.style.left = Math.round(left) + 'px';
        ui.root.style.right = 'auto';
        ui.root.style.top = Math.round(top) + 'px';
    }
    function displaySnapshot() {
        return state.optimisticSnapshot || state.snapshot;
    }
    function cloneSnapshot(snapshot) {
        if (!snapshot || typeof snapshot !== 'object') return null;
        try { return JSON.parse(JSON.stringify(snapshot)); } catch (_) { return null; }
    }
    function actionPreview(category, contentId) {
        var categoryConfig = state.careActions && state.careActions[category];
        var action = categoryConfig && categoryConfig[contentId];
        return action && typeof action === 'object' ? action : null;
    }
    function applyOptimisticPreview(category, contentId) {
        var preview = cloneSnapshot(state.snapshot);
        var action = actionPreview(category, contentId);
        if (!preview || !action) return null;
        var values = preview.status_values;
        if (!values || typeof values !== 'object') return null;
        var add = function (key, field) {
            var delta = Number(action[field] || 0), current = Number(values[key]);
            if (!Number.isFinite(delta) || !Number.isFinite(current)) return;
            values[key] = Math.max(0, Math.min(100, current + delta));
        };
        if (category === 'feeding') {
            add('satiety', 'satiety_delta');
        } else if (category === 'cleaning') {
            add('cleanliness', 'cleanliness_delta');
        }
        return preview;
    }
    function applyOptimisticPlayPreview(contentId) {
        var preview = cloneSnapshot(state.snapshot);
        var configured = state.playActions && state.playActions[contentId];
        if (!preview || !configured || typeof configured !== 'object') return null;
        var values = preview.status_values;
        if (!values || typeof values !== 'object') return null;
        var addBounded = function (key, field) {
            var delta = Number(configured[field] || 0), current = Number(values[key]);
            if (!Number.isFinite(delta) || !Number.isFinite(current)) return;
            values[key] = Math.max(0, Math.min(100, current + delta));
        };
        addBounded('energy', 'energy_delta');
        addBounded('cleanliness', 'cleanliness_delta');
        var progressDelta = Number(configured.progress_delta || 0);
        var dailyCount = Number(configured.daily_count);
        var progressLimit = Number(configured.daily_progress_limit);
        var grantsProgress = !Number.isFinite(progressLimit) ||
            !Number.isFinite(dailyCount) || dailyCount < progressLimit;
        if (grantsProgress && Number.isFinite(progressDelta)) {
            var progress = Number(values.progress);
            if (Number.isFinite(progress)) values.progress = Math.max(0, progress + progressDelta);
        }
        return preview;
    }
    function value(key) {
        var currentSnapshot = displaySnapshot();
        var values = currentSnapshot && currentSnapshot.status_values;
        if (!values || typeof values !== 'object' || values[key] == null) return '—';
        var number = Number(values[key]);
        if (!Number.isFinite(number)) return '—';
        var rules = state.settlement;
        var settledAt = currentSnapshot && Date.parse(currentSnapshot.last_settled_at);
        if (rules && Number.isFinite(settledAt) && settledAt < Date.now() &&
            !explorationActive(currentSnapshot && currentSnapshot.exploration)) {
            var elapsedHours = (Date.now() - settledAt) / 3600000;
            if (key === 'satiety' && isFiniteValue(rules.satiety_decay_per_hour)) {
                number -= Number(rules.satiety_decay_per_hour) * elapsedHours;
            } else if (key === 'cleanliness' && isFiniteValue(rules.cleanliness_decay_per_hour)) {
                number -= Number(rules.cleanliness_decay_per_hour) * elapsedHours;
            } else if (key === 'energy' && isFiniteValue(rules.energy_recovery_per_hour)) {
                number += Number(rules.energy_recovery_per_hour) * elapsedHours;
            }
        }
        // Persisted snapshots from an older service may contain fractional
        // time-settlement values. Status cards are intentionally integer
        // values so a stale fractional snapshot cannot overflow the card.
        var bounded = key === 'progress' ? Math.max(0, number) : Math.max(0, Math.min(100, number));
        return String(Math.round(bounded));
    }
    function careReady() {
        return isPetCareCompatibilityReady() && !state.pendingAction && !state.playActivity &&
            !state.playPending && !state.taskPendingId && !state.dailyCheckInPending &&
            !explorationActive(state.snapshot && state.snapshot.exploration);
    }
    function explorationActive(exploration) {
        if (!exploration || typeof exploration !== 'object') return false;
        if (!Object.keys(exploration).length) return false;
        var status = str(exploration.status).toLowerCase();
        // An active record without a stable server activity id cannot be
        // safely gated, recalled or settled. Treat it as unavailable rather
        // than showing a nest while leaving entity output unblocked.
        if (!str(exploration.exploration_id || exploration.explorationId ||
            exploration.activity_id || exploration.activityId || exploration.id)) return false;
        return exploration.active !== false && ['completed', 'inactive', 'cancelled', 'recalled', 'returned'].indexOf(status) < 0;
    }
    function currentExploration() {
        var exploration = state.snapshot && state.snapshot.exploration;
        return exploration && typeof exploration === 'object' ? exploration : null;
    }
    function explorationId(exploration) {
        exploration = exploration || currentExploration() || {};
        return str(exploration.exploration_id || exploration.explorationId || exploration.activity_id ||
            exploration.activityId || exploration.id);
    }
    function explorationGeneration(exploration) {
        exploration = exploration || currentExploration() || {};
        return str(exploration.generation || exploration.activity_generation || exploration.activityGeneration) ||
            explorationId(exploration);
    }
    function explorationTimestamp(value) {
        if (typeof value === 'number' && Number.isFinite(value)) {
            return value < 100000000000 ? value * 1000 : value;
        }
        if (typeof value === 'string' && value.trim()) {
            var numeric = Number(value);
            if (Number.isFinite(numeric)) return numeric < 100000000000 ? numeric * 1000 : numeric;
            var parsed = Date.parse(value);
            if (Number.isFinite(parsed)) return parsed;
        }
        return NaN;
    }
    function explorationEndMs(exploration) {
        exploration = exploration || currentExploration() || {};
        var direct = explorationTimestamp(exploration.ends_at || exploration.end_at || exploration.expires_at ||
            exploration.completes_at || exploration.completion_at || exploration.endsAt || exploration.endAt ||
            exploration.expiresAt || exploration.completesAt || exploration.completionAt);
        if (Number.isFinite(direct)) return direct;
        var started = explorationTimestamp(exploration.started_at || exploration.start_at ||
            exploration.startedAt || exploration.startAt);
        var duration = Number(exploration.duration_seconds || exploration.duration || exploration.durationSeconds);
        return Number.isFinite(started) && Number.isFinite(duration) && duration > 0
            ? started + duration * 1000 : NaN;
    }
    function explorationLocation(exploration) {
        var location = exploration && (exploration.location || exploration.location_config);
        if (typeof location === 'string') return { id: location, title: location };
        if (location && typeof location === 'object') {
            return Object.assign({}, location, { title: str(location.title || location.name) || '探索地点' });
        }
        var configured = state.explorationLocations;
        if (Array.isArray(configured) && configured.length && configured[0] && typeof configured[0] === 'object') {
            return configured[0];
        }
        if (configured && typeof configured === 'object') {
            var firstKey = Object.keys(configured)[0];
            if (firstKey) return Object.assign({ id: firstKey }, configured[firstKey] || {});
        }
        return { id: str(exploration && (exploration.location_id || exploration.locationId)),
            title: str(exploration && (exploration.location_name || exploration.locationName || exploration.name)) || '探索地点',
            description: '选择一个地点，猫会在倒计时结束后回来。' };
    }
    function explorationAsset(active, reason) {
        var exploration = currentExploration() || {};
        var location = explorationLocation(exploration) || {};
        var presentationNote = str(exploration.note || exploration.nest_note || exploration.away_note || location.note);
        var endMs = explorationEndMs(exploration);
        var presentationEndsAt = Number.isFinite(endMs)
            ? new Date(endMs).toISOString()
            : (exploration.ends_at || exploration.endsAt || '');
        var assetUrl = active === true
            ? (str(exploration.asset_url || exploration.assetUrl || location.asset_url || location.assetUrl) ||
                '/static/assets/neko-idle/cat-bad.png')
            : '';
        emit('neko:pet-care:exploration-asset', {
            active: active === true,
            asset_url: assetUrl,
            assetUrl: assetUrl,
            exploration_id: explorationId(exploration), explorationId: explorationId(exploration), activityId: explorationId(exploration),
            generation: explorationGeneration(exploration), activityGeneration: explorationGeneration(exploration),
            location_id: str(exploration.location_id || exploration.locationId || location.id),
            // The yellow paper is the cat's departure note.  Revealed
            // exploration letters belong to the inbox module only.
            note: presentationNote,
            started_at: exploration.started_at || exploration.startedAt || '',
            // The service contract may provide started_at + duration_seconds
            // without materialising ends_at.  The presentation note still
            // needs the same server-confirmed deadline as the panel timer.
            ends_at: presentationEndsAt,
            reason: reason || 'exploration-state-change',
        });
    }
    function clearExplorationTimer() {
        if (explorationTimer) { try { window.clearTimeout(explorationTimer); } catch (_) {} explorationTimer = 0; }
        if (explorationDepartureTimer) {
            try { window.clearTimeout(explorationDepartureTimer); } catch (_) {}
            explorationDepartureTimer = 0;
        }
        if (explorationReturnTimer) {
            try { window.clearTimeout(explorationReturnTimer); } catch (_) {}
            explorationReturnTimer = 0;
        }
    }
    function readExplorationLease() {
        try {
            var raw = window.sessionStorage && window.sessionStorage.getItem(EXPLORATION_LEASE_STORAGE_KEY);
            if (!raw) return null;
            var lease = JSON.parse(raw);
            if (!lease || typeof lease !== 'object' || !str(lease.accountId) ||
                !str(lease.characterUid) || !str(lease.explorationId) ||
                !lease.exploration || !explorationActive(lease.exploration)) return null;
            return lease;
        } catch (_) { return null; }
    }
    function writeExplorationLease(exploration) {
        var id = explorationId(exploration);
        if (!id || !explorationActive(exploration) || !state.accountId || !state.currentCharacterUid) return;
        try {
            if (window.sessionStorage) window.sessionStorage.setItem(EXPLORATION_LEASE_STORAGE_KEY, JSON.stringify({
                accountId: state.accountId, characterUid: state.currentCharacterUid,
                boundRoleId: state.bindingRoleId, explorationId: id,
                exploration: exploration, updatedAt: Date.now(),
            }));
        } catch (_) {}
    }
    function clearExplorationLease() {
        try { if (window.sessionStorage) window.sessionStorage.removeItem(EXPLORATION_LEASE_STORAGE_KEY); } catch (_) {}
    }
    function clearExplorationLeaseIfMatches(accountId, characterUid, explorationIdValue) {
        var lease = readExplorationLease();
        if (!lease || lease.accountId !== str(accountId) || lease.characterUid !== str(characterUid) ||
            lease.explorationId !== str(explorationIdValue)) return false;
        clearExplorationLease();
        return true;
    }
    function applyExplorationRecoveryLease() {
        var lease = readExplorationLease();
        if (!lease || lease.accountId !== state.accountId || lease.characterUid !== state.currentCharacterUid) return false;
        state.bindingStatus = 'bound'; state.bindingRoleId = str(lease.boundRoleId);
        state.snapshot = {
            schema_version: 1, account_id: state.accountId,
            bound_role_id: state.bindingRoleId, pet_id: 'recovery-only',
            last_settled_at: new Date().toISOString(),
            status_values: {}, negative_status: {}, inventory: {}, tasks: {}, unlocks: [],
            exploration: lease.exploration,
        };
        state.serviceStatus = 'unavailable'; state.serviceReason = 'exploration-recovery-lease';
        state.selectedCategory = 'exploration';
        mode(MODES && MODES.PET_AVAILABLE, state.serviceReason);
        if (!state.explorationGateActive) {
            state.explorationGateActive = true;
            emitExplorationGate(true, 'exploration-recovery-lease');
        }
        state.panelOpen = true;
        scheduleExplorationTimer();
        if (ui) ui.root.dataset.available = 'true';
        visible(true); render();
        return true;
    }
    function scheduleExplorationTimer() {
        clearExplorationTimer();
        var exploration = currentExploration();
        if (!explorationActive(exploration) || (state.explorationPending &&
            (state.explorationPending.operation === 'complete' ||
                state.explorationPending.operation === 'recall'))) return;
        var end = explorationEndMs(exploration);
        var now = Date.now();
        var retryAt = Number(state.explorationAutoCompleteRetryAt) || 0;
        var endPassed = Number.isFinite(end) && end <= now;
        var timerTarget = endPassed && retryAt > now ? retryAt : end;
        var delay = Number.isFinite(timerTarget) ? Math.max(250, timerTarget - now) : 1000;
        explorationTimer = window.setTimeout(function tickExploration() {
            explorationTimer = 0;
            if (!explorationActive(currentExploration())) return;
            var now = Date.now();
            render();
            var nextEnd = explorationEndMs(currentExploration());
            if (Number.isFinite(nextEnd) && nextEnd <= Date.now() && !state.explorationPending &&
                Date.now() >= (Number(state.explorationAutoCompleteRetryAt) || 0)) {
                completeExploration();
            } else if (!state.explorationPending) scheduleExplorationTimer();
        }, endPassed ? delay : Math.min(delay, 1000));
        if (explorationTimer && typeof explorationTimer.unref === 'function') explorationTimer.unref();
    }
    function emitExplorationGate(active, reason) {
        var exploration = currentExploration();
        var activityId = explorationId(exploration);
        var activityGeneration = explorationGeneration(exploration);
        if (active) {
            if (explorationReturnTimer) {
                window.clearTimeout(explorationReturnTimer);
                explorationReturnTimer = 0;
            }
            emit('neko:pet-care:exploration-gate', {
                active: true, exploration_id: activityId,
                explorationId: activityId, activityId: activityId,
                generation: activityGeneration, activityGeneration: activityGeneration,
                reason: reason || 'exploration-state-change',
            });
            // Exploration owns the cat entity for its full activity window.
            // End any yarn/consumable play lease before the gate is published
            // so a late response cannot turn an away interaction into a
            // playing settlement.
            if (state.playActivity) clearPlayActivity('exploration-away');
            if (state.playPending) {
                state.playPending = null;
                state.optimisticSnapshot = null;
            }
            // The service activity is authoritative immediately and the
            // entity gate starts immediately. The visual adapter gets a
            // bounded departure window before the nest asset is installed.
            emit('neko:pet-care:exploration-departure-request', {
                exploration_id: activityId, explorationId: activityId, activityId: activityId,
                generation: activityGeneration, activityGeneration: activityGeneration,
                reason: reason || 'exploration-started',
            });
            if (explorationDepartureTimer) window.clearTimeout(explorationDepartureTimer);
            explorationDepartureTimer = window.setTimeout(function () {
                explorationDepartureTimer = 0;
                var current = currentExploration();
                if (!explorationActive(current) || explorationId(current) !== activityId ||
                    explorationGeneration(current) !== activityGeneration) return;
                explorationAsset(true, 'exploration-departure-complete');
                emit('neko:pet-care:exploration-departure-complete', {
                    exploration_id: activityId, explorationId: activityId, activityId: activityId,
                    generation: activityGeneration, activityGeneration: activityGeneration,
                });
            }, 260);
            if (explorationDepartureTimer && typeof explorationDepartureTimer.unref === 'function') {
                explorationDepartureTimer.unref();
            }
        } else {
            if (explorationDepartureTimer) {
                window.clearTimeout(explorationDepartureTimer);
                explorationDepartureTimer = 0;
            }
            emit('neko:pet-care:exploration-return-request', {
                exploration_id: activityId, explorationId: activityId, activityId: activityId,
                generation: activityGeneration, activityGeneration: activityGeneration,
                reason: reason || 'exploration-home',
            });
            if (explorationReturnTimer) window.clearTimeout(explorationReturnTimer);
            explorationReturnTimer = window.setTimeout(function () {
                explorationReturnTimer = 0;
                var latest = currentExploration();
                if (explorationActive(latest) || explorationId(latest) !== activityId ||
                    explorationGeneration(latest) !== activityGeneration) return;
                explorationAsset(false, reason);
                emit('neko:pet-care:exploration-return-complete', {
                    exploration_id: activityId, explorationId: activityId, activityId: activityId,
                    generation: activityGeneration, activityGeneration: activityGeneration,
                    reason: reason || 'exploration-home',
                });
                // Keep the entity gate active until the bounded return
                // presentation has restored the original cat art.
                emit('neko:pet-care:exploration-gate', {
                    active: false, exploration_id: activityId,
                    explorationId: activityId, activityId: activityId,
                    generation: activityGeneration, activityGeneration: activityGeneration,
                    reason: reason || 'exploration-home',
                });
            }, 220);
            if (explorationReturnTimer && typeof explorationReturnTimer.unref === 'function') {
                explorationReturnTimer.unref();
            }
            if (state.catLocalActive) mode(MODES && MODES.PET_AVAILABLE, reason || 'exploration-home');
        }
    }
    function reconcileExplorationFromSnapshot(snapshot, reason) {
        var active = explorationActive(snapshot && snapshot.exploration);
        if (active) {
            writeExplorationLease(snapshot && snapshot.exploration);
            state.explorationRecallRequested = false;
            state.selectedCategory = 'exploration';
            // A server-confirmed active exploration is a standalone view. On
            // refresh or re-login there may be no subsequent cat-local-active
            // event to reopen the surface, so restore its expanded state here
            // instead of leaving the nest data behind a hidden panel.
            state.panelOpen = true;
            if (ui) {
                ui.root.dataset.available = 'true';
                visible(true);
            }
            scheduleExplorationTimer();
            if (!state.explorationGateActive) {
                state.explorationGateActive = true;
                emitExplorationGate(true, reason || 'exploration-snapshot');
            }
        } else {
            clearExplorationLease();
            state.explorationRecallRequested = false;
            clearExplorationTimer();
            if (state.explorationGateActive) {
                state.explorationGateActive = false;
                emitExplorationGate(false, reason || 'exploration-home');
            }
            // The unified return chain may mark the cat form inactive before
            // the service recall response arrives. Once the activity is
            // terminal, restore the cat-form-only entry boundary; on a
            // failed recall the active snapshot remains and the nest stays
            // available for retry.
            if (!state.catLocalActive) {
                clearTimers();
                state.panelOpen = false;
                state.selectedCategory = '';
                state.progressUnlockOpen = false;
                state.featureHintVisible = false;
                if (ui) ui.root.dataset.available = 'false';
                visible(false);
                mode(MODES && MODES.PET_UNAVAILABLE, reason || 'exploration-home');
            }
        }
    }
    function inventoryCount(contentId) {
        var currentSnapshot = displaySnapshot();
        var inventory = currentSnapshot && currentSnapshot.inventory;
        if (!inventory || typeof inventory !== 'object') return 0;
        var entry = inventory[contentId];
        if (typeof entry === 'number') return Number.isFinite(entry) ? Math.max(0, Math.floor(entry)) : 0;
        if (entry && typeof entry === 'object') {
            var count = entry.count != null ? entry.count : entry.quantity;
            return Number.isFinite(Number(count)) ? Math.max(0, Math.floor(Number(count))) : 0;
        }
        return 0;
    }
    function inventoryCountForAction(category, contentId) {
        var action = actionPreview(category, contentId);
        var inventoryItem = str(action && action.inventory_item) || contentId;
        return inventoryCount(inventoryItem);
    }
    function cooldownRemainingSeconds(category, contentId) {
        if (category !== 'cleaning') return 0;
        var action = actionPreview(category, contentId);
        var cooldownSeconds = Number(action && action.cooldown_seconds || 0);
        var cooldowns = state.snapshot && state.snapshot.cleaning_cooldowns;
        var startedAt = cooldowns && typeof cooldowns === 'object' ? Date.parse(cooldowns[contentId]) : NaN;
        if (!Number.isFinite(cooldownSeconds) || cooldownSeconds <= 0 || !Number.isFinite(startedAt)) return 0;
        return Math.max(0, Math.ceil(cooldownSeconds - (Date.now() - startedAt) / 1000));
    }
    function cooldownLabel(seconds) {
        if (seconds >= 3600) return '冷却中 · ' + Math.ceil(seconds / 3600) + '小时';
        if (seconds >= 60) return '冷却中 · ' + Math.ceil(seconds / 60) + '分钟';
        return '冷却中 · ' + Math.max(1, seconds) + '秒';
    }
    function scheduleCooldownRefresh() {
        if (cooldownRefreshTimer) { window.clearTimeout(cooldownRefreshTimer); cooldownRefreshTimer = 0; }
        if (!state.panelOpen || !state.snapshot) return;
        var shortest = Infinity;
        careContent('cleaning').forEach(function (item) {
            var remaining = cooldownRemainingSeconds('cleaning', item.id);
            if (remaining > 0) shortest = Math.min(shortest, remaining);
        });
        if (Number.isFinite(shortest)) {
            cooldownRefreshTimer = window.setTimeout(function () {
                cooldownRefreshTimer = 0; render();
            }, Math.min(shortest, 86400) * 1000 + 50);
            // Browser timers are owned by the page. Node-based harnesses may
            // expose a Timeout object instead; do not let a long cooldown
            // keep a test process alive after the panel is gone.
            if (cooldownRefreshTimer && typeof cooldownRefreshTimer.unref === 'function') {
                cooldownRefreshTimer.unref();
            }
        }
    }
    function scheduleStatusRefresh() {
        if (statusRefreshTimer) { window.clearTimeout(statusRefreshTimer); statusRefreshTimer = 0; }
        if (!state.panelOpen || !state.snapshot || explorationActive(currentExploration())) return;
        // This is a display-only timer for the Q4 local formula. It never
        // reads the service or writes a snapshot.
        statusRefreshTimer = window.setTimeout(function () {
            statusRefreshTimer = 0;
            if (state.panelOpen && state.snapshot && !explorationActive(currentExploration())) render();
        }, 15000);
        if (statusRefreshTimer && typeof statusRefreshTimer.unref === 'function') statusRefreshTimer.unref();
    }
    // Concrete food and cleaning operations belong to the confirmed service
    // configuration. The client only turns the category map into display
    // entries; it must not decide which future item exists or what it does.
    function careContent(category) {
        var configured = state.careActions && state.careActions[category];
        if (!configured || typeof configured !== 'object' || Array.isArray(configured)) return [];
        return Object.keys(configured).map(function (contentId) {
            var action = configured[contentId];
            if (!action || typeof action !== 'object' || Array.isArray(action)) return null;
            return {
                id: contentId,
                action: action,
                label: str(action.label || action.display_name) || contentId,
            };
        }).filter(Boolean);
    }
    function hasConfiguredCareCategory(category) {
        return (category === 'feeding' || category === 'cleaning') && careContent(category).length > 0;
    }
    function playActionEntries() {
        var configured = state.playActions && typeof state.playActions === 'object'
            ? state.playActions : {};
        return Object.keys(configured).filter(function (contentId) {
            var item = configured[contentId];
            return contentId !== 'yarn' && item && typeof item === 'object' &&
                str(item.inventory_item);
        }).map(function (contentId) {
            var item = configured[contentId];
            return { id: contentId, action: item,
                label: str(item.label || item.display_name) || '陪玩道具' };
        });
    }
    function actionLabel(category) {
        return category === 'feeding' ? '喂食' : category === 'cleaning' ? '清洁' :
            category === 'play' ? '陪玩' : category === 'exploration' ? '探索' : '';
    }
    function careErrorText(code) {
        var messages = {
            satiety_full: '饱食度已满',
            cleanliness_full: '清洁度已满',
            inventory_insufficient: '背包中没有可用食物',
            play_inventory_insufficient: '背包中没有可用的陪玩道具',
            cleaning_cooldown: '清洁操作还在冷却中',
            play_daily_limit: '今日该陪玩次数已用完',
            play_source_not_allowed: '这次玩耍不是用户主动发起的',
            play_user_not_initiated: '需要用户主动操作陪玩内容',
            play_no_response: '猫没有回应这次互动',
            play_not_completed: '陪玩动作没有完成',
            play_feedback_unavailable: '猫的陪玩反馈暂不可用',
            energy_insufficient: '精力不足，暂时不能进行这项陪玩',
            activity_in_progress: '探索期间不能照料',
            exploration_blocked_by_status: '生病期间不能探索，请先完成求药恢复',
            recovery_task_not_claimed: '请先领取服务确认的恢复任务',
            session_stale: '账号状态已变化，请重新确认养宠状态',
            dev_tools_unavailable: '开发调试工具未启用',
            dev_fill_failed: '开发调试操作未完成',
            pet_context_invalid: '当前角色已变化，请重新确认养宠状态',
            care_action_timeout: '服务响应超时，请稍后重试',
            care_action_snapshot_incomplete: '服务返回的养宠状态不完整',
            care_action_rejected: '服务未接受这次操作',
            exploration_start_rejected: '暂时不能开始探索',
            exploration_start_timeout: '出发请求超时，请稍后重试',
            exploration_complete_rejected: '探索结算暂未完成',
            exploration_recall_rejected: '召回请求暂未完成，可稍后重试',
            exploration_settle_timeout: '返回请求超时，请稍后重试',
            exploration_load_failed: '探索状态暂时不可用，请稍后重试',
            recovery_status_not_active: '当前状态不需要恢复',
            recovery_item_insufficient: '背包中没有服务确认的恢复物品',
            recovery_status_unavailable: '当前恢复状态不可用',
            use_recovery_item_failed: '恢复操作未完成',
        };
        var normalized = String(code || '').replace(/-/g, '_');
        return messages[code] || messages[normalized] || code || '服务未接受这次操作';
    }
    function setActionCategory(category) {
        if (category !== 'play' && category !== 'exploration' &&
            ['feeding', 'cleaning'].indexOf(category) < 0) return;
        state.selectedView = 'overview';
        state.selectedCategory = state.selectedCategory === category ? '' : category;
        state.progressUnlockOpen = false;
        state.actionError = '';
        render();
        if (state.selectedCategory === 'exploration' && explorationActive(currentExploration())) refreshExploration();
    }
    function setPetCareView(view) {
        var allowed = ['overview', 'tasks', 'letters', 'inventory', 'profile'];
        view = str(view).toLowerCase();
        if (allowed.indexOf(view) < 0) view = 'overview';
        state.selectedView = view;
        if (view !== 'overview') state.selectedCategory = '';
        state.progressUnlockOpen = false;
        state.actionError = '';
        render();
        if (view === 'tasks') loadTasksWhenPanelOpens();
        if (view === 'letters' && explorationActive(currentExploration())) refreshExploration();
    }
    function wakeCat1ForCare(detail) {
        var goodbye = window.nekoAutoGoodbye;
        if (goodbye && typeof goodbye.restartVisualTierForPetCare === 'function') {
            var restarted = false;
            try { restarted = goodbye.restartVisualTierForPetCare('pet-care-user-action') === true; } catch (_) {}
            // The countdown helper is only available while the goodbye
            // lifecycle is active. A user action must still wake CAT1 when a
            // host exposes the direct tier setter without that lifecycle.
            if (!restarted && typeof goodbye.setVisualTier === 'function') {
                try { goodbye.setVisualTier('cat1', { source: 'pet-care-user-action', reason: 'care-action' }); } catch (_) {}
            }
        } else if (goodbye && typeof goodbye.setVisualTier === 'function') {
            try { goodbye.setVisualTier('cat1', { source: 'pet-care-user-action', reason: 'care-action' }); } catch (_) {}
        }
        emit('neko:pet-care:wake-cat1', Object.assign({ source: 'pet-care-user-action' }, detail || {}));
    }
    function playCareFeedback(category, contentId, configuredAction, options) {
        var presentation = window.NekoCatIdlePresentation;
        var presentationName = '';
        var started = false;
        var feedbackAction = str(configuredAction);
        if (feedbackAction === 'stretch') {
            presentationName = 'cat1-stretch';
            if (presentation && typeof presentation.requestCat1Stretch === 'function') {
                try { started = presentation.requestCat1Stretch() === true; } catch (_) {}
            }
        } else if (feedbackAction === 'eat_snack') {
            presentationName = 'cat1-eat';
            if (presentation && typeof presentation.requestCat1Eat === 'function') {
                try { started = presentation.requestCat1Eat() === true; } catch (_) {}
            }
        }
        if (!started) return false;
        emit('neko:pet-care:feedback', {
            category: category, content_id: contentId,
            presentation: started ? presentationName : 'none', started: started,
            optimistic: !options || options.optimistic !== false,
        });
        return true;
    }
    function actionIdempotencyKey() {
        try {
            if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        } catch (_) {}
        return 'pet-care-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    }
    function clearPlayActivityTimer() {
        if (!playActivityTimer) return;
        try { window.clearTimeout(playActivityTimer); } catch (_) {}
        playActivityTimer = 0;
    }
    function playActivityId(detail, prefix) {
        var payload = detail && typeof detail === 'object' ? detail : {};
        var nested = payload.detail && typeof payload.detail === 'object' ? payload.detail : payload;
        var explicit = str(nested.activityId || nested.activity_id || nested.sessionId || nested.session_id);
        if (explicit) return explicit.slice(0, 180);
        playActivitySequence += 1;
        return (str(prefix) || 'yarn') + '-' + Date.now().toString(36) + '-' + playActivitySequence.toString(36);
    }
    function clearPlayActivity(reason) {
        clearPlayActivityTimer();
        var previous = state.playActivity;
        state.playActivity = null;
        if (previous) state.optimisticSnapshot = null;
        if (previous) cancelPetCarePresentation(reason || 'pet-care-play-ended');
        if (previous && reason) {
            emit('neko:pet-care:play-activity-ended', {
                activity_id: previous.activity_id, source: previous.source,
                user_initiated: true, cat_responded: false, terminal_status: reason,
            });
        }
        render();
    }
    function armPlayActivityLease(activity) {
        clearPlayActivityTimer();
        playActivityTimer = window.setTimeout(function () {
            playActivityTimer = 0;
            if (!state.playActivity || state.playActivity.activity_id !== activity.activity_id) return;
            clearPlayActivity('timeout');
        }, PLAY_ACTIVITY_LEASE_MS);
        if (playActivityTimer && typeof playActivityTimer.unref === 'function') playActivityTimer.unref();
    }
    function beginPlayActivity(observation) {
        if (!isPetCareCompatibilityReady() || explorationActive(currentExploration()) ||
            state.playPending || state.playActivity) return false;
        var detail = observation && observation.detail && typeof observation.detail === 'object'
            ? observation.detail : {};
        if (detail.userInitiated !== true || detail.endedNearCat !== true) return false;
        var source = str(detail.originSource || observation.source || 'chat-yarn');
        if (!source || /cat[_-]?mind|autonomous/i.test(source)) return false;
        var retry = state.playRetry && state.playRetry.content_id === 'yarn'
            ? state.playRetry : null;
        state.playRetry = null;
        var activity = {
            // A transport failure is ambiguous: the server may have accepted
            // the previous activity. Reusing its activity/idempotency identity
            // makes the next user gesture a safe read/commit retry instead of
            // a second progress event.
            activity_id: retry ? retry.activity_id : playActivityId(observation),
            content_id: 'yarn',
            source: retry && retry.source || source,
            user_initiated: true,
            started_at: Number(detail.startedAt) || Number(observation.timestamp) || Date.now(),
            observed_at: Number(observation.timestamp) || Date.now(),
        };
        state.playError = '';
        // Establish the activity lease before waking CAT1. A host may deliver
        // the existing cat1_play_yarn response synchronously while changing
        // the visual tier; the terminal must never arrive before the lease
        // is visible to the settlement adapter.
        state.playActivity = activity;
        state.optimisticSnapshot = applyOptimisticPlayPreview('yarn');
        armPlayActivityLease(activity);
        // CAT2/CAT3 are the long-idle visual forms. A user-owned yarn gesture
        // is an active care interaction and must first return the cat to CAT1
        // so the existing cat1_play_yarn action runner can respond.
        wakeCat1ForCare({ category: 'play', content_id: 'yarn', source: activity.source,
            user_initiated: true });
        emit('neko:pet-care:play-activity-start', activity);
        render();
        return true;
    }
    function beginToyPlayActivity(contentId) {
        contentId = str(contentId);
        if (!contentId || contentId === 'yarn' || !isPetCareCompatibilityReady() ||
            state.playPending || state.playActivity || !careReady()) return false;
        var action = state.playActions && state.playActions[contentId];
        if (!action || typeof action !== 'object') return false;
        var quantity = Number(action.inventory_quantity);
        var dailyCount = Number(action.daily_count);
        var dailyLimit = Number(action.daily_limit);
        if (Number.isFinite(quantity) && quantity <= 0) {
            state.playError = 'play_inventory_insufficient'; render(); return false;
        }
        if (Number.isFinite(dailyLimit) && Number.isFinite(dailyCount) && dailyCount >= dailyLimit) {
            state.playError = 'play_daily_limit'; render(); return false;
        }
        var retry = state.playRetry && state.playRetry.content_id === contentId
            ? state.playRetry : null;
        state.playRetry = null;
        var activity = {
            activity_id: retry ? retry.activity_id : playActivityId({ activity_id: contentId + '-' + Date.now() }, 'toy'),
            content_id: contentId,
            source: retry && retry.source || 'pet-care-toy',
            user_initiated: true,
            started_at: Date.now(),
            observed_at: Date.now(),
        };
        var presentation = window.NekoCatIdlePresentation;
        if (!presentation || typeof presentation.requestCat1Play !== 'function') {
            state.playError = 'play_feedback_unavailable'; render(); return false;
        }
        state.playError = '';
        state.playActivity = activity;
        state.optimisticSnapshot = applyOptimisticPlayPreview(contentId);
        armPlayActivityLease(activity);
        emit('neko:pet-care:play-activity-start', activity);
        var wakeAdapter = window.NekoPetCareActionAdapter;
        if (wakeAdapter && typeof wakeAdapter.wakeCat1 === 'function') {
            try {
                wakeAdapter.wakeCat1({ category: 'play', content_id: contentId,
                    feedback_action: action.feedback_action, optimistic: true });
            } catch (_) {}
        } else wakeCat1ForCare({ category: 'play', content_id: contentId,
            feedback_action: action.feedback_action, optimistic: true });
        render();
        var started = false;
        try {
            started = presentation.requestCat1Play({
                source: activity.source,
                onTerminal: function (result, detail) {
                    if (!state.playActivity || state.playActivity.activity_id !== activity.activity_id) return;
                    var terminalInput = typeof result === 'string'
                        ? result : result && (result.result || result.reason || result.status);
                    var terminal = playResponseTerminal(terminalInput);
                    if (terminal === 'done') settlePlayActivity(activity, terminal, detail || result);
                    else clearPlayActivity(terminal);
                },
            }) === true;
        } catch (_) {}
        if (!started) {
            clearPlayActivity('not_started');
            state.playError = 'play_feedback_unavailable';
            render();
            return false;
        }
        return true;
    }
    function playResponseTerminal(result) {
        var normalized = str(result).toLowerCase();
        if (normalized === 'done' || normalized === 'play_done' || normalized === 'cat1_local_play_done') return 'done';
        if (normalized === 'timeout') return 'timeout';
        if (normalized === 'failed') return 'failed';
        return 'cancelled';
    }
    async function settlePlayActivity(activity, terminalStatus, sourceDetail) {
        if (!activity || state.playPending || explorationActive(currentExploration()) ||
            !isPetCareCompatibilityReady() ||
            terminalStatus !== 'done') return null;
        clearPlayActivityTimer();
        state.playActivity = null;
        state.optimisticSnapshot = null;
        var expected = state.generation;
        var contentId = str(activity.content_id) || 'yarn';
        var idempotencyKey = 'pet-play-' + contentId + '-' + activity.activity_id;
        state.playPending = {
            activity_id: activity.activity_id, source: activity.source,
            idempotency_key: idempotencyKey,
        };
        state.playError = '';
        render();
        try {
            var response = await submitCareAction(petCareEndpoint('/care-actions'), {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({
                        category: 'playing', content_id: contentId, idempotency_key: idempotencyKey,
                        source: activity.source, activity_id: activity.activity_id,
                        user_initiated: true, cat_responded: true, terminal_status: 'done',
                    }),
                });
            var body = await response.json();
            if (expected !== state.generation) return null;
            if (explorationActive(currentExploration())) {
                throw new Error('activity_in_progress');
            }
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            var confirmed = snapshotFromActionBody(body);
            if (!response.ok) {
                // A rejection may still carry the service's current counters
                // (for example a consumable daily limit). Accept that
                // read-only snapshot, but never treat the rejected play as a
                // successful settlement.
                if (confirmed && isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) {
                    state.snapshot = confirmed;
                    applyTaskPayload(confirmed);
                    if (body && body.play_actions && typeof body.play_actions === 'object') {
                        state.playActions = body.play_actions;
                    }
                }
                throw new Error(str(body && (body.code || body.detail || body.reason)) || 'play-action-rejected');
            }
            if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) {
                throw new Error(str(body && (body.code || body.detail || body.reason)) ||
                    'play-action-rejected');
            }
            state.snapshot = confirmed;
            applyTaskPayload(confirmed);
            state.optimisticSnapshot = null;
            state.playRetry = null;
            if (body && body.settlement && typeof body.settlement === 'object') state.settlement = body.settlement;
            if (body && body.play_actions && typeof body.play_actions === 'object') state.playActions = body.play_actions;
            if (body && body.care_actions && typeof body.care_actions === 'object') {
                state.careActions = body.care_actions;
                if (!state.playActions && body.care_actions.playing) state.playActions = body.care_actions.playing;
            }
            state.serviceStatus = 'ready'; state.serviceReason = 'play-action-confirmed';
            state.reason = 'play-action-confirmed'; state.playError = '';
            emit('neko:pet-care:play-confirmed', {
                activity_id: activity.activity_id, content_id: contentId, source: activity.source,
                terminal_status: 'done', operation: body.operation || body.result || null,
                snapshot: confirmed, response_detail: sourceDetail || null,
            });
            return confirmed;
        } catch (error) {
            if (expected !== state.generation) return null;
            var transportFailure = error && (error.name === 'AbortError' || error.name === 'TypeError');
            var playFailure = error && error.name === 'AbortError'
                ? 'play-action-timeout' : error && error.message || 'play-action-failed';
            state.playError = contentId !== 'yarn' && playFailure === 'inventory_insufficient'
                ? 'play_inventory_insufficient' : playFailure;
            if (transportFailure) {
                // Keep the same activity key for an explicit retry. The
                // server may have committed before the response was lost.
                state.playRetry = {
                    activity_id: activity.activity_id, content_id: contentId,
                    source: activity.source, idempotency_key: idempotencyKey,
                };
            }
            emit('neko:pet-care:play-rollback', {
                activity_id: activity.activity_id, content_id: contentId, source: activity.source,
                reason: state.playError,
            });
            return null;
        } finally {
            if (expected === state.generation) {
                state.playPending = null;
                render();
            }
        }
    }
    function observePlayObservation(observation) {
        if (!observation || typeof observation !== 'object') return;
        var type = str(observation.type);
        if (type === 'chat_yarn_drag_completed') {
            beginPlayActivity(observation);
            return;
        }
        if (type === 'cat1_local_play_done' || type === 'cat1_local_play_cancelled') {
            // The local journey is autonomous and its existing terminal event
            // has no user activity id. Keep it out of the pet-care settlement;
            // a future adapter may opt in only by carrying the exact active
            // activity id back through this event.
            var localDetail = observation.detail && typeof observation.detail === 'object'
                ? observation.detail : {};
            var localActivityId = str(localDetail.activityId || localDetail.activity_id ||
                observation.activityId || observation.activity_id);
            if (!state.playActivity || !localActivityId ||
                localActivityId !== state.playActivity.activity_id) return;
            if (type === 'cat1_local_play_done') settlePlayActivity(state.playActivity, 'done', observation);
            else clearPlayActivity('cancelled');
        }
    }
    function observePlayActionResult(result) {
        if (!result || typeof result !== 'object' || str(result.actionId) !== 'cat1_play_yarn') return;
        // Non-Cat-Mind toy feedback uses the action runner's explicit
        // onTerminal callback.  Only a yarn observation lease may consume
        // the shared Cat Mind result event.
        if (!state.playActivity || state.playActivity.content_id !== 'yarn') return;
        var terminal = playResponseTerminal(result.result || result.reason);
        if (terminal === 'done') settlePlayActivity(state.playActivity, terminal, result);
        else clearPlayActivity(terminal);
    }
    function petCareEndpoint(suffix, characterUid) {
        var clientFactory = window.NekoPetCareClient;
        if (clientFactory && typeof clientFactory.create === 'function') {
            return clientFactory.create({
                characterUid: characterUid == null ? state.currentCharacterUid : characterUid,
                timeoutMs: CARE_REQUEST_TIMEOUT_MS,
            }).path(suffix || '');
        }
        var transport = window.NekoPetCareApi;
        var uid = characterUid == null ? state.currentCharacterUid : characterUid;
        if (transport && typeof transport.petPath === 'function') {
            return transport.petPath(uid, suffix || '');
        }
        return '/api/pet-care/pets/' + encodeURIComponent(uid) + (suffix || '');
    }
    async function submitCareAction(url, options) {
        var clientFactory = window.NekoPetCareClient;
        if (clientFactory && typeof clientFactory.create === 'function') {
            return clientFactory.create({
                characterUid: state.currentCharacterUid,
                timeoutMs: CARE_REQUEST_TIMEOUT_MS,
            }).request(url, options);
        }
        var transport = window.NekoPetCareApi;
        if (transport && typeof transport.request === 'function') {
            return transport.request(url, options, { timeoutMs: CARE_REQUEST_TIMEOUT_MS });
        }
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var timer = 0;
        var requestOptions = Object.assign({}, options || {});
        if (controller) {
            requestOptions.signal = controller.signal;
            timer = window.setTimeout(function () { controller.abort(); }, CARE_REQUEST_TIMEOUT_MS);
        }
        try {
            return await fetch(url, requestOptions);
        } finally {
            if (timer) window.clearTimeout(timer);
        }
    }
    function explorationFromBody(body) {
        if (!body || typeof body !== 'object') return null;
        if (body.exploration && typeof body.exploration === 'object') return body.exploration;
        if (body.activity && typeof body.activity === 'object') return body.activity;
        if (body.result && body.result.exploration && typeof body.result.exploration === 'object') return body.result.exploration;
        if (body.result && body.result.activity && typeof body.result.activity === 'object') return body.result.activity;
        var snapshot = snapshotFromActionBody(body);
        return snapshot && snapshot.exploration && typeof snapshot.exploration === 'object'
            ? snapshot.exploration : null;
    }
    function applyExplorationEnvelope(body, reason, options) {
        options = options && typeof options === 'object' ? options : {};
        var confirmed = snapshotFromActionBody(body);
        if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) return false;
        state.snapshot = confirmed;
        state.optimisticSnapshot = null;
        // Only retain a reward carried by this service-confirmed envelope.
        // Start/refresh responses clear the previous terminal result so a new
        // activity cannot display stale loot.
        var confirmedReward = body && (body.reward || body.rewards ||
            (body.result && (body.result.reward || body.result.rewards)));
        state.explorationReward = confirmedReward != null ? confirmedReward : null;
        var settlement = body && (body.settlement || (body.result && body.result.settlement));
        if (settlement && typeof settlement === 'object') state.settlement = settlement;
        if (body && (body.locations || body.exploration_locations || body.explorationLocations)) {
            state.explorationLocations = body.locations || body.exploration_locations || body.explorationLocations;
        }
        applyTaskPayload(confirmed);
        state.serviceStatus = 'ready'; state.serviceReason = reason || 'exploration-confirmed';
        state.reason = state.serviceReason;
        state.explorationError = '';
        if (!explorationActive(confirmed.exploration)) state.explorationAutoCompleteRetryAt = 0;
        reconcileExplorationFromSnapshot(confirmed, reason || 'exploration-confirmed');
        if (!explorationActive(confirmed.exploration)) {
            if (pendingContextRecall && pendingContextRecall.activityId === explorationId(confirmed.exploration)) {
                pendingContextRecall = null;
            }
            if (options.suppressNotice !== true) {
                notifyExplorationTerminal(confirmed.exploration, confirmedReward,
                    reason && reason.indexOf('complete') >= 0 ? 'complete' : 'recall');
            }
        }
        return true;
    }
    function explorationEndpoint(suffix) {
        return petCareEndpoint('/explorations' + (suffix || ''));
    }
    async function startExploration(locationId) {
        // Exploration is a direct cat interaction. It may be restored while
        // the cat is away, but a new activity can only start from the active
        // cat form; callers cannot bypass this through the panel API.
        if (!stage5Ready() || !state.catLocalActive || explorationActive(currentExploration()) ||
            state.explorationPending || state.pendingAction || state.playActivity || state.playPending ||
            state.taskPendingId || state.dailyCheckInPending || !state.currentCharacterUid) return null;
        var request = ++explorationRequestGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.explorationPending = { operation: 'start', idempotency_key: idempotencyKey };
        state.explorationError = ''; render();
        try {
            var response = await submitCareAction(explorationEndpoint(), {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify(Object.assign({ idempotency_key: idempotencyKey },
                    str(locationId) ? { location_id: str(locationId) } : {})),
            });
            var body = await response.json();
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) {
                var rejected = snapshotFromActionBody(body);
                if (rejected && isCompleteSnapshot(rejected, state.accountId, state.bindingRoleId)) state.snapshot = rejected;
                throw new Error(str(body && (body.code || body.detail || body.reason)) || 'exploration-start-rejected');
            }
            if (!applyExplorationEnvelope(body, 'exploration-start-confirmed')) throw new Error('care-action-snapshot-incomplete');
            // CAT2/CAT3 are the same pet's long-idle forms. Starting an
            // explicit care/exploration interaction wakes the existing visual
            // tier to CAT1; it does not create another pet or a new form.
            wakeCat1ForCare({ category: 'exploration', location_id: str(locationId) || '',
                source: 'pet-care-exploration' });
            emit('neko:pet-care:exploration-started', {
                exploration_id: explorationId(), generation: explorationGeneration(),
                location_id: str(locationId) || explorationLocation(currentExploration()).id || null,
                snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            state.explorationError = error && error.name === 'AbortError'
                ? 'exploration-start-timeout' : error && error.message || 'exploration-start-failed';
            // Start is the one write whose response may be lost after the
            // service has already committed the activity. Re-read the
            // authoritative snapshot once so a timeout cannot leave the
            // panel offering a second start while the server says active.
            if (error && (error.name === 'AbortError' || error.name === 'TypeError')) {
                refreshBinding('exploration-start-recovery').catch(function () {});
            }
            return null;
        } finally {
            if (request === explorationRequestGeneration) { state.explorationPending = null; render(); }
        }
    }
    async function refreshExploration() {
        var exploration = currentExploration();
        if (state.authStatus !== 'logged-in' || state.bindingStatus !== 'bound' || !state.snapshot ||
            !state.currentCharacterUid || !explorationActive(exploration) || !explorationId(exploration) ||
            state.explorationPending || state.explorationRecallRequested) return null;
        var request = ++explorationRequestGeneration, expected = state.generation;
        try {
            var response = await submitCareAction(explorationEndpoint('/' + encodeURIComponent(explorationId(exploration))), {
                method: 'GET', credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'application/json' },
            });
            var body = await response.json();
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) throw new Error(str(body && (body.code || body.detail || body.reason)) || 'exploration-load-failed');
            if (!applyExplorationEnvelope(body, 'exploration-refresh-confirmed')) throw new Error('care-action-snapshot-incomplete');
            return state.snapshot;
        } catch (error) {
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            state.explorationError = error && error.message || 'exploration-load-failed'; render(); return null;
        }
    }
    async function settleExploration(operation, detail) {
        var exploration = currentExploration();
        if (!explorationActive(exploration) || state.explorationPending || !state.currentCharacterUid) return null;
        var id = explorationId(exploration), expected = state.generation;
        if (!id) return null;
        var request = ++explorationRequestGeneration;
        var idempotencyKey = operation === 'recall'
            ? 'pet-exploration-recall-' + id
            : 'pet-exploration-' + operation + '-' + id;
        state.explorationPending = { operation: operation, exploration_id: id, idempotency_key: idempotencyKey };
        state.explorationError = ''; render();
        try {
            var response = await submitCareAction(explorationEndpoint('/' + encodeURIComponent(id) + '/' + operation), {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ exploration_id: id, idempotency_key: idempotencyKey }),
            });
            var body = await response.json();
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) throw new Error(str(body && (body.code || body.detail || body.reason)) ||
                'exploration-' + operation + '-rejected');
            if (!applyExplorationEnvelope(body, 'exploration-' + operation + '-confirmed', {
                suppressNotice: detail && detail.suppressNotice === true,
            })) {
                throw new Error('care-action-snapshot-incomplete');
            }
            var reward = body && (body.reward || body.rewards || (body.result && (body.result.reward || body.result.rewards)));
            emit('neko:pet-care:exploration-settled', {
                operation: operation, exploration_id: id, generation: explorationGeneration(),
                reward: reward || null, snapshot: state.snapshot, detail: detail || null,
            });
            // The service decides the terminal winner when completion and an
            // explicit recall overlap. If it leaves the activity active after
            // a completion attempt, the queued recall can still settle it;
            // an accepted terminal completion remains final and is not
            // rewritten by a late client preference.
            if (operation === 'complete' && state.explorationRecallRequested &&
                explorationActive(currentExploration())) {
                window.setTimeout(function () {
                    if (explorationActive(currentExploration()) &&
                        !state.explorationPending && state.explorationRecallRequested) {
                        settleExploration('recall', { source: 'queued-return-after-complete' });
                    }
                }, 0);
            }
            return state.snapshot;
        } catch (error) {
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            state.explorationError = error && error.name === 'AbortError'
                ? 'exploration-settle-timeout' : error && error.message || 'exploration-settle-failed';
            if (operation === 'complete') {
                // Automatic completion must not hammer an unavailable service
                // once per second. Manual retry remains available from the
                // nest; the same idempotency key safely replays a late commit.
                state.explorationAutoCompleteRetryAt = Date.now() + 30000;
            }
            if (operation === 'recall') {
                // A failed recall leaves the server activity active. Clear the
                // local waiting flag so the same unified entry can be retried
                // with the same activity-scoped idempotency contract.
                state.explorationRecallRequested = false;
            }
            // A failed settle leaves the service-owned activity in place. The
            // user can retry from the nest; no unconfirmed reward is shown.
            return null;
        } finally {
            if (request === explorationRequestGeneration) {
                state.explorationPending = null; render();
                if (operation === 'complete' && state.explorationRecallRequested &&
                    explorationActive(currentExploration())) {
                    window.setTimeout(function () {
                        if (explorationActive(currentExploration()) &&
                            !state.explorationPending && state.explorationRecallRequested) {
                            settleExploration('recall', { source: 'queued-return-after-complete' });
                        }
                    }, 0);
                }
            }
        }
    }
    async function completeExploration() { return settleExploration('complete', { automatic: true }); }
    function requestExplorationRecall(options) {
        options = options && typeof options === 'object' ? options : {};
        var exploration = currentExploration();
        if (!explorationActive(exploration) || state.explorationRecallRequested) return false;
        // Do not cancel an already submitted completion request. Mark the
        // explicit return as a pending intent; the service decides which
        // terminal result is accepted for this activity.
        if (state.explorationPending && state.explorationPending.operation !== 'complete') return false;
        // Invalidate an in-flight status read before the unified return event
        // starts settlement. A late GET must not clear the recall lease by
        // applying an older active snapshot.
        if (!state.explorationPending) explorationRequestGeneration += 1;
        state.explorationRecallRequested = true; state.explorationError = ''; render();
        var payload = {
            explorationRecall: true,
            exploration_id: explorationId(exploration), generation: explorationGeneration(exploration),
            explorationId: explorationId(exploration), activityId: explorationId(exploration),
            activityGeneration: explorationGeneration(exploration),
            source: options.source || 'pet-care-nest',
            presentation: options.presentation || 'cat-form',
        };
        if (payload.presentation === 'cat-form') {
            // The nest button is an activity operation: settle the exploration
            // and restore the cat-form presentation in place.  It must not
            // enter the model return chain, which would make “召回猫咪” show
            // the model and conflate two different exits.
            settleExploration('recall', payload);
        } else {
            // Model/character switches use the existing unified model-return
            // transition.  The transition commits the same service recall,
            // but only this path is allowed to show the model.
            emit('neko:pet-care:exploration-recall-request', payload);
        }
        return true;
    }
    function commitExplorationRecall(detail) {
        var exploration = currentExploration();
        var payload = detail && typeof detail === 'object' ? detail : {};
        var requestedId = str(payload.exploration_id || payload.explorationId || payload.activity_id || payload.activityId);
        var requestedGeneration = str(payload.generation || payload.activityGeneration);
        var currentGeneration = explorationGeneration(exploration);
        if (!explorationActive(exploration) || !state.explorationRecallRequested ||
            (requestedId && requestedId !== explorationId(exploration)) ||
            (requestedGeneration && currentGeneration && requestedGeneration !== currentGeneration)) return null;
        if (state.explorationPending) return null;
        state.explorationRecallRequested = false;
        return settleExploration('recall', payload);
    }
    function snapshotFromActionBody(body) {
        if (!body || typeof body !== 'object') return null;
        if (body.snapshot && typeof body.snapshot === 'object') return body.snapshot;
        if (body.result && body.result.snapshot && typeof body.result.snapshot === 'object') return body.result.snapshot;
        if (body.operation && body.operation.snapshot && typeof body.operation.snapshot === 'object') return body.operation.snapshot;
        return null;
    }
    function stage5Ready() {
        return state.authStatus === 'logged-in' && state.bindingStatus === 'bound' &&
            state.serviceStatus === 'ready' && !!state.snapshot && !!state.currentCharacterUid;
    }
    function taskData() {
        if (state.tasks && typeof state.tasks === 'object') return state.tasks;
        var snapshotTasks = state.snapshot && state.snapshot.tasks;
        return snapshotTasks && typeof snapshotTasks === 'object' ? snapshotTasks : null;
    }
    function hasTaskPayload(value) {
        return !!(value && typeof value === 'object' &&
            (Array.isArray(value.items) || Array.isArray(value.tasks) || Array.isArray(value.groups) ||
                typeof value.service_day === 'string' || value.daily_login));
    }
    function applyTaskPayload(snapshot) {
        var tasks = snapshot && snapshot.tasks;
        if (hasTaskPayload(tasks)) state.tasks = tasks;
    }
    function taskItems() {
        var data = taskData();
        var items = [];
        if (data) {
            if (Array.isArray(data.items)) items = data.items.slice();
            else if (Array.isArray(data.tasks)) items = data.tasks.slice();
            else if (Array.isArray(data.groups)) {
                items = data.groups.reduce(function (all, group) {
                    return all.concat(group && Array.isArray(group.items) ? group.items : []);
                }, []);
            }
        }
        return items;
    }
    function applyConfirmedStage5Envelope(body) {
        var confirmed = snapshotFromActionBody(body);
        if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) return false;
        state.snapshot = confirmed;
        state.optimisticSnapshot = null;
        var responseSettlement = body && (body.settlement || (body.result && body.result.settlement));
        if (responseSettlement && typeof responseSettlement === 'object') state.settlement = responseSettlement;
        state.playActions = body && body.play_actions && typeof body.play_actions === 'object'
            ? body.play_actions : (body && body.care_actions && body.care_actions.playing) || state.playActions;
        if (body && body.dev_tools && typeof body.dev_tools === 'object') state.devTools = body.dev_tools;
        if (body && body.care_actions && typeof body.care_actions === 'object') {
            state.careActions = body.care_actions;
        }
        var nextTasks = body && body.tasks && typeof body.tasks === 'object' ? body.tasks : confirmed.tasks;
        if (hasTaskPayload(nextTasks)) state.tasks = nextTasks;
        reconcileExplorationFromSnapshot(confirmed, 'stage5-envelope');
        state.serviceStatus = 'ready'; state.serviceReason = 'stage5-confirmed';
        state.reason = 'stage5-confirmed';
        return true;
    }
    function taskErrorText(code) {
        var messages = {
            task_already_claimed: '这项任务已经领取过了',
            task_unavailable: '今日任务暂不可领取',
            task_condition_unavailable: '这项任务条件尚未开放',
            task_not_complete: '这项任务还没有完成',
            task_progress_unavailable: '这项任务进度暂不可确认',
            task_reward_unavailable: '任务奖励暂不可用',
            daily_check_in_failed: '今日登录确认未完成',
            service_unavailable: '养成服务暂时不可用',
        };
        var normalized = String(code || '').replace(/-/g, '_');
        return messages[code] || messages[normalized] || code || '任务操作未完成';
    }
    function taskRewardLabel(itemId) {
        var normalized = String(itemId || '');
        var catalogs = [state.careActions, state.playActions];
        for (var catalogIndex = 0; catalogIndex < catalogs.length; catalogIndex += 1) {
            var catalog = catalogs[catalogIndex];
            if (!catalog || typeof catalog !== 'object') continue;
            var categories = Object.keys(catalog);
            for (var categoryIndex = 0; categoryIndex < categories.length; categoryIndex += 1) {
                var categoryId = categories[categoryIndex];
                var category = catalog[categoryId];
                if (!category || typeof category !== 'object') continue;
                // play_actions is already a direct content map, while
                // care_actions is grouped by business category.
                if (str(category.inventory_item) || str(category.label) || str(category.display_name)) {
                    if (categoryId === normalized || str(category.inventory_item) === normalized) {
                        return str(category.label || category.display_name) || normalized || '道具';
                    }
                    continue;
                }
                var contentIds = Object.keys(category);
                for (var contentIndex = 0; contentIndex < contentIds.length; contentIndex += 1) {
                    var contentId = contentIds[contentIndex];
                    var action = category[contentId];
                    if (!action || typeof action !== 'object') continue;
                    if (contentId === normalized || str(action.inventory_item) === normalized) {
                        return str(action.label || action.display_name) || normalized || '道具';
                    }
                }
            }
        }
        return normalized || '道具';
    }
    function inventoryPresentation(itemId, entry) {
        var label = entry && typeof entry === 'object'
            ? str(entry.label || entry.name || entry.display_name) : '';
        var icon = entry && typeof entry === 'object'
            ? str(entry.icon || entry.emoji || entry.glyph) : '';
        var visit = function (catalog) {
            if (!catalog || typeof catalog !== 'object' || (label && icon)) return;
            Object.keys(catalog).some(function (key) {
                var candidate = catalog[key];
                if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return false;
                var matches = key === itemId || str(candidate.inventory_item) === itemId;
                if (matches) {
                    label = label || str(candidate.label || candidate.display_name);
                    icon = icon || str(candidate.icon || candidate.emoji || candidate.glyph);
                    return true;
                }
                // care_actions is grouped by business category while
                // play_actions is a direct content map. Recurse only through
                // plain config objects so presentation metadata stays service
                // driven and never becomes a client business rule.
                visit(candidate);
                return !!(label && icon);
            });
        };
        visit(state.careActions); visit(state.playActions);
        return { label: label || itemId || '道具', icon: icon || '✦' };
    }
    async function dailyCheckIn() {
        if (!stage5Ready() || state.pendingAction || state.dailyCheckInPending || state.taskPendingId ||
            !state.currentCharacterUid) return null;
        var request = ++dailyCheckInGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.dailyCheckInPending = true; state.taskError = ''; render();
        try {
            var response = await submitCareAction(petCareEndpoint('/daily-check-in'), {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({ idempotency_key: idempotencyKey }),
                });
            var body = await response.json();
            if (request !== dailyCheckInGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    state.snapshot = rejectedSnapshot;
                    applyTaskPayload(rejectedSnapshot);
                }
                throw new Error(str(body && (body.code || body.detail || body.reason)) || 'daily-check-in-failed');
            }
            if (!applyConfirmedStage5Envelope(body)) throw new Error('care-action-snapshot-incomplete');
            emit('neko:pet-care:daily-check-in-confirmed', {
                idempotency_key: idempotencyKey,
                progress_delta: body && body.progress_delta,
                unlocked: body && body.unlocked || [],
                snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== dailyCheckInGeneration || expected !== state.generation) return null;
            state.taskError = error && error.name === 'AbortError'
                ? 'daily-check-in-timeout' : error && error.message || 'daily-check-in-failed';
            return null;
        } finally {
            if (request === dailyCheckInGeneration) { state.dailyCheckInPending = false; render(); }
        }
    }
    async function refreshTasks() {
        if (!stage5Ready() || state.pendingAction || state.playActivity || state.playPending || state.taskLoading || state.taskPendingId ||
            state.dailyCheckInPending || !state.currentCharacterUid) return null;
        var request = ++taskRequestGeneration, expected = state.generation;
        state.taskLoading = true; state.taskError = ''; render();
        try {
            var response = await submitCareAction(petCareEndpoint('/tasks'), {
                    method: 'GET', credentials: 'same-origin', cache: 'no-store',
                    headers: { Accept: 'application/json' },
                });
            var body = await response.json();
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) throw new Error(str(body && (body.code || body.detail || body.reason)) || 'task-load-failed');
            if (!applyConfirmedStage5Envelope(body)) throw new Error('care-action-snapshot-incomplete');
            var loginState = taskData() && taskData().daily_login;
            if (loginState && loginState.checked !== true) await dailyCheckIn();
            return state.tasks;
        } catch (error) {
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            state.taskError = error && error.name === 'AbortError'
                ? 'task-load-timeout' : error && error.message || 'task-load-failed';
            return null;
        } finally {
            if (request === taskRequestGeneration) { state.taskLoading = false; render(); }
        }
    }
    async function claimTask(taskId) {
        taskId = str(taskId);
        var item = taskItems().find(function (candidate) {
            return candidate && String(candidate.task_id || candidate.id) === taskId;
        });
        var recoveryStatus = str(item && item.recovery_status).toLowerCase();
        var recoveryAlreadyResolved = ['sick', 'sulk'].indexOf(recoveryStatus) >= 0 && item.status === 'completed';
        if (!stage5Ready() || state.pendingAction || state.playActivity || state.playPending || state.taskLoading || state.dailyCheckInPending ||
            !taskId || !item || item.claimable === false || item.status === 'claimed' || recoveryAlreadyResolved || state.taskPendingId) return null;
        var request = ++taskRequestGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.taskPendingId = taskId; state.taskError = ''; render();
        try {
            var response = await submitCareAction(petCareEndpoint('/tasks/' + encodeURIComponent(taskId) + '/claim'), {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                    body: JSON.stringify({ idempotency_key: idempotencyKey }),
                });
            var body = await response.json();
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    state.snapshot = rejectedSnapshot;
                    applyTaskPayload(rejectedSnapshot);
                }
                throw new Error(str(body && (body.code || body.detail || body.reason)) || 'task-claim-failed');
            }
            if (!applyConfirmedStage5Envelope(body)) throw new Error('care-action-snapshot-incomplete');
            emit('neko:pet-care:task-claimed', {
                task_id: taskId, idempotency_key: idempotencyKey,
                reward: body && body.reward, snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            state.taskError = error && error.name === 'AbortError'
                ? 'task-claim-timeout' : error && error.message || 'task-claim-failed';
            return null;
        } finally {
            if (request === taskRequestGeneration) { state.taskPendingId = ''; render(); }
        }
    }
    async function useRecoveryItem(statusKey) {
        statusKey = str(statusKey).toLowerCase();
        if (!stage5Ready() || state.pendingAction || state.playActivity || state.playPending ||
            state.taskLoading || state.dailyCheckInPending || state.recoveryPendingStatus ||
            !state.currentCharacterUid || ['sick', 'sulk'].indexOf(statusKey) < 0) return null;
        var request = ++taskRequestGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.recoveryPendingStatus = statusKey; state.taskError = ''; render();
        try {
            var response = await submitCareAction(petCareEndpoint('/status-recovery'), {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ status: statusKey, idempotency_key: idempotencyKey }),
            });
            var body = await response.json();
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    state.snapshot = rejectedSnapshot; applyTaskPayload(rejectedSnapshot);
                }
                throw new Error(str(body && (body.code || body.detail || body.reason)) || 'use-recovery-item-failed');
            }
            if (!applyConfirmedStage5Envelope(body)) throw new Error('care-action-snapshot-incomplete');
            emit('neko:pet-care:recovery-confirmed', {
                status: statusKey, idempotency_key: idempotencyKey, snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            state.taskError = error && error.name === 'AbortError'
                ? 'use-recovery-item-timeout' : error && error.message || 'use-recovery-item-failed';
            return null;
        } finally {
            if (request === taskRequestGeneration) { state.recoveryPendingStatus = ''; render(); }
        }
    }
    async function performCareAction(category, contentId) {
        if (!careReady() || state.playActivity || state.playPending || !state.currentCharacterUid ||
            !careContent(category).some(function (item) { return item.id === contentId; })) return null;
        var request = ++actionRequestGeneration;
        var expected = state.generation;
        var retry = state.retryAction && state.retryAction.category === category &&
            state.retryAction.contentId === contentId ? state.retryAction : null;
        var idempotencyKey = retry ? retry.idempotencyKey : actionIdempotencyKey();
        state.retryAction = null;
        state.pendingAction = { category: category, contentId: contentId, idempotencyKey: idempotencyKey };
        state.actionError = '';
        // Q13 is intentionally optimistic for the visible interaction: keep
        // the durable snapshot untouched, but preview the immediate care
        // status change while the server request is in flight. Inventory,
        // progress, rewards, and other durable effects remain server-confirmed.
        state.optimisticSnapshot = applyOptimisticPreview(category, contentId);
        var configuredAction = actionPreview(category, contentId);
        var feedbackAction = configuredAction && configuredAction.feedback_action;
        emit('neko:pet-care:action-start', {
            category: category, content_id: contentId, idempotency_key: idempotencyKey,
            optimistic: true,
        });
        var wakeAdapter = window.NekoPetCareActionAdapter;
        if (wakeAdapter && typeof wakeAdapter.wakeCat1 === 'function') {
            try {
                wakeAdapter.wakeCat1({ category: category, content_id: contentId,
                    feedback_action: feedbackAction, optimistic: true });
            } catch (_) {}
        } else wakeCat1ForCare({ category: category, content_id: contentId,
            feedback_action: feedbackAction, optimistic: true });
        playCareFeedback(category, contentId, feedbackAction);
        render();
        try {
            var response = await submitCareAction(petCareEndpoint('/care-actions'), {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ category: category, content_id: contentId, idempotency_key: idempotencyKey }),
            });
            var body = await response.json();
            if (request !== actionRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            var confirmed = snapshotFromActionBody(body);
            if (!response.ok) {
                // A rejection may still carry the service's time-settled
                // snapshot. Accept that read-only settlement if it is a
                // complete snapshot, but never apply the rejected care
                // operation itself.
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    state.snapshot = rejectedSnapshot;
                    applyTaskPayload(rejectedSnapshot);
                }
                var failure = str(body && (body.code || body.detail || body.reason)) || 'care-action-rejected';
                throw new Error(failure);
            }
            if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) {
                throw new Error('care-action-snapshot-incomplete');
            }
            var operation = body.operation || (body.result && body.result.operation) || {};
            state.snapshot = confirmed;
            applyTaskPayload(confirmed);
            state.optimisticSnapshot = null;
            var responseSettlement = body.settlement || (body.result && body.result.settlement);
            if (responseSettlement && typeof responseSettlement === 'object') state.settlement = responseSettlement;
            state.serviceStatus = 'ready'; state.serviceReason = 'care-action-confirmed';
            state.reason = 'care-action-confirmed'; state.actionError = '';
            emit('neko:pet-care:action-confirmed', {
                category: category, content_id: contentId, idempotency_key: idempotencyKey,
                operation: body.operation || body.result || null, snapshot: confirmed,
            });
            return confirmed;
        } catch (error) {
            if (request !== actionRequestGeneration || expected !== state.generation) return null;
            state.actionError = error && error.name === 'AbortError'
                ? 'care-action-timeout' : error && error.message || 'care-action-failed';
            if (error && (error.name === 'AbortError' || error.name === 'TypeError')) {
                // The server may have committed just before the transport
                // timed out or the network dropped after commit. Reusing this
                // key makes a manual retry resolve the same idempotent result
                // instead of applying the care action twice.
                state.retryAction = { category: category, contentId: contentId, idempotencyKey: idempotencyKey };
            }
            state.optimisticSnapshot = null;
            cancelPetCarePresentation(state.actionError);
            emit('neko:pet-care:action-rollback', {
                category: category, content_id: contentId, idempotency_key: idempotencyKey,
                reason: state.actionError,
            });
            return null;
        } finally {
            if (request === actionRequestGeneration) {
                state.pendingAction = null;
                render();
            }
        }
    }
    async function debugFillStatus() {
        if (!stage5Ready() || !state.devTools || state.devTools.fill_status !== true ||
            state.devFillPending || state.pendingAction || state.playActivity ||
            state.playPending || explorationActive(currentExploration())) return null;
        var request = ++actionRequestGeneration;
        var expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.devFillPending = true;
        state.actionError = '';
        render();
        try {
            var response = await submitCareAction(petCareEndpoint('/debug/fill-status'), {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ idempotency_key: idempotencyKey }),
            });
            var body = await response.json();
            if (request !== actionRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    state.snapshot = rejectedSnapshot;
                }
                throw new Error(str(body && (body.code || body.detail || body.reason)) || 'dev_fill_failed');
            }
            if (!applyConfirmedStage5Envelope(body)) throw new Error('care-action-snapshot-incomplete');
            if (body && body.dev_tools && typeof body.dev_tools === 'object') state.devTools = body.dev_tools;
            state.serviceStatus = 'ready';
            state.serviceReason = 'dev-fill-confirmed';
            state.reason = 'dev-fill-confirmed';
            state.actionError = '';
            emit('neko:pet-care:dev-fill-confirmed', {
                idempotency_key: idempotencyKey, snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== actionRequestGeneration || expected !== state.generation) return null;
            state.actionError = error && error.name === 'AbortError'
                ? 'care-action-timeout' : error && error.message || 'dev_fill_failed';
            return null;
        } finally {
            if (request === actionRequestGeneration) {
                state.devFillPending = false;
                render();
            }
        }
    }
    function explorationRemainingLabel(exploration) {
        var end = explorationEndMs(exploration);
        if (!Number.isFinite(end)) return '等待服务确认时间';
        var seconds = Math.max(0, Math.ceil((end - Date.now()) / 1000));
        var hours = Math.floor(seconds / 3600); seconds %= 3600;
        var minutes = Math.floor(seconds / 60); seconds %= 60;
        return hours ? hours + '小时' + String(minutes).padStart(2, '0') + '分' : minutes + '分' + String(seconds).padStart(2, '0') + '秒';
    }
    function explorationText(value, fallback) {
        if (typeof value === 'string' && value.trim()) return value.trim();
        if (value && typeof value === 'object') return str(value.text || value.message || value.content || value.title) || fallback;
        return fallback;
    }
    function explorationLetters(exploration) {
        if (!exploration || typeof exploration !== 'object') return [];
        if (Array.isArray(exploration.letters_revealed)) return exploration.letters_revealed;
        if (Array.isArray(exploration.revealed_letters)) return exploration.revealed_letters;
        // `letters` is the service's selected pool while an activity is away.
        // It is only displayable after the activity reaches a terminal state,
        // or when an older service shape has no reveal field at all.
        if (!explorationActive(exploration) && Array.isArray(exploration.letters)) return exploration.letters;
        return [];
    }
    function explorationRewardText(reward) {
        if (reward == null) return '';
        var parts = [];
        var drops = Array.isArray(reward) ? reward : reward && typeof reward === 'object' &&
            (reward.drops || reward.items || reward.rewards);
        if (Array.isArray(drops)) drops.forEach(function (drop) {
            if (!drop || typeof drop !== 'object') return;
            var quantity = Number(drop.quantity != null ? drop.quantity : drop.count);
            var name = str(drop.name || drop.label || drop.display_name || drop.item_id || drop.id);
            if (name) parts.push(name + (Number.isFinite(quantity) && quantity > 0 ? ' × ' + quantity : ''));
        });
        var progress = Number(reward && typeof reward === 'object' ? reward.progress_delta : NaN);
        if (Number.isFinite(progress) && progress !== 0) parts.push('养成进度 +' + progress);
        if (!parts.length && typeof reward === 'string') return reward;
        return parts.join('、');
    }
    function renderExplorationSideCards() {
        if (!ui || !ui.explorationStatus) return;
        var exploration = currentExploration();
        if (!explorationActive(exploration)) {
            ui.explorationStatus.hidden = true;
            return;
        }
        var location = explorationLocation(exploration);
        ui.explorationStatus.hidden = false;
        ui.explorationStatusTitle.textContent = '探索进行中';
        ui.explorationStatusLocation.textContent = explorationText(location.title || location.name, '探索地点');
    }
    function renderExplorationDetails(container) {
        var exploration = currentExploration();
        var pending = state.explorationPending;
        var location = explorationLocation(exploration);
        var summary = document.createElement('div'); summary.className = 'neko-pet-care-exploration-summary';
        var copy = document.createElement('div'); copy.className = 'neko-pet-care-exploration-copy';
        var title = document.createElement('strong'); title.textContent = explorationActive(exploration)
            ? explorationText(location.title || location.name, '探索地点') : explorationText(location.title || location.name, '探索地点');
        copy.appendChild(title);
        if (!explorationActive(exploration)) {
            var description = document.createElement('span');
            description.textContent = explorationText(location.description, '选择一个地点，猫会在倒计时结束后带回发现。');
            copy.appendChild(description);
        }
        summary.appendChild(copy); container.appendChild(summary);
        var statusBlock = document.createElement('div'); statusBlock.className = 'neko-pet-care-exploration-status-block';
        var status = document.createElement('div'); status.className = 'neko-pet-care-exploration-status';
        if (explorationActive(exploration)) {
            status.textContent = '剩余时间 · ' + explorationRemainingLabel(exploration);
            statusBlock.appendChild(status); container.appendChild(statusBlock);
            var recall = button(state.explorationRecallRequested ? '等待返回…' : '召回猫咪', 'neko-pet-care-exploration-recall');
            recall.disabled = !!pending || state.explorationRecallRequested;
            recall.setAttribute('aria-disabled', recall.disabled ? 'true' : 'false');
            recall.title = recall.disabled ? '正在处理召回' : '结束探索并让猫回到猫形态';
            recall.dataset.explorationAction = 'recall'; container.appendChild(recall);
            var end = explorationEndMs(exploration);
            if (Number.isFinite(end) && end <= Date.now() && !state.explorationRecallRequested) {
                var retryComplete = button(pending && pending.operation === 'complete' ? '结算中…' : '重试完成结算', 'neko-pet-care-exploration-complete');
                retryComplete.disabled = !!pending;
                retryComplete.setAttribute('aria-disabled', retryComplete.disabled ? 'true' : 'false');
                retryComplete.dataset.explorationAction = 'complete'; container.appendChild(retryComplete);
            }
        } else {
            // A completed or recalled result is delivered through the
            // one-shot pet-care notice.  Do not pin that result or its reward
            // to the exploration card; the card returns to its start state.
            var start = button(pending && pending.operation === 'start' ? '出发中…' : '开始探索', 'neko-pet-care-exploration-start');
            var sickActive = !!(state.snapshot && state.snapshot.negative_status &&
                state.snapshot.negative_status.sick && state.snapshot.negative_status.sick.active);
            start.disabled = !!pending || !stage5Ready() || sickActive;
            start.setAttribute('aria-disabled', start.disabled ? 'true' : 'false');
            start.title = start.disabled && sickActive ? '生病期间不能探索' :
                (start.disabled ? '当前不可用' : '开始探索');
            start.dataset.explorationAction = 'start'; container.appendChild(start);
        }
        if (state.explorationError) {
            var error = document.createElement('p'); error.className = 'neko-pet-care-exploration-error';
            error.textContent = '探索操作未完成：' + careErrorText(state.explorationError); container.appendChild(error);
        }
    }
    function renderProgressUnlocks() {
        if (!ui || !ui.progressUnlockPanel) return;
        var panel = ui.progressUnlockPanel;
        if (typeof panel.replaceChildren === 'function') panel.replaceChildren();
        else { panel.children = []; panel.textContent = ''; }
        panel.hidden = !state.progressUnlockOpen;
        if (!state.progressUnlockOpen) return;
        var title = document.createElement('strong');
        title.className = 'neko-pet-care-progress-unlock-title';
        title.textContent = '已解锁内容';
        panel.appendChild(title);
        var unlocks = state.snapshot && Array.isArray(state.snapshot.unlocks) ? state.snapshot.unlocks : [];
        if (!unlocks.length) {
            var empty = document.createElement('span');
            empty.className = 'neko-pet-care-progress-unlock-empty';
            empty.textContent = '暂无已解锁内容';
            panel.appendChild(empty);
            return;
        }
        unlocks.forEach(function (unlock) {
            var row = document.createElement('span');
            row.className = 'neko-pet-care-progress-unlock-row';
            row.textContent = String(unlock && typeof unlock === 'object'
                ? (unlock.name || unlock.id || '已解锁内容') : unlock || '已解锁内容');
            panel.appendChild(row);
        });
    }
    function render() {
        if (!ui) return;
        var status = state.authStatus, binding = state.bindingStatus;
        var account = state.user && (state.user.display_name || state.user.email || state.accountId);
        var authView = status === 'logged-out' || state.oauthPending;
        ui.closeButton.setAttribute('aria-label', authView ? '关闭登录' : '关闭养宠面板');
        ui.closeButton.setAttribute('title', authView ? '关闭登录' : '关闭');
        // The demo surface has no persistent close affordance.  Keep the
        // obvious close control only on the account login view, where it
        // closes the login UI rather than the pet surface itself.
        ui.closeButton.hidden = !authView;
        ui.root.dataset.authView = authView ? 'true' : 'false';
        ui.root.dataset.status = status; ui.root.dataset.bindingStatus = binding;
        ui.root.dataset.serviceStatus = state.serviceStatus;
        ui.root.dataset.panelOpen = state.panelOpen ? 'true' : 'false';
        ui.root.dataset.expanded = state.panelOpen ? 'true' : 'false';
        ui.root.dataset.view = state.selectedView || 'overview';
        ui.panel.dataset.view = state.selectedView || 'overview';
        // This label is the demo's surface chrome, not a second launcher.
        // Account/binding state remains in the small status text at the right.
        var serviceText = status === 'logged-out' ? '养成入口处于灰色状态，登录后可用。' :
            binding === 'needs-binding' ? '这是账号首次使用养宠，请确认绑定当前角色。' :
            binding === 'unbound' ? '当前角色不是账号绑定角色，继续使用普通猫。' :
            binding === 'bound' && !state.snapshot ? '角色已由服务确认，但完整养成快照暂不可用。' :
            state.serviceStatus === 'unavailable' ? '养成服务暂时不可用，猫的普通行为不受影响。' :
            state.serviceStatus === 'loading' ? '正在确认账号宠物和当前角色。' : '';
        if (binding === 'bound' && state.snapshot && state.serviceStatus === 'ready') {
            // The surface itself is the entry; do not repeat an entry hint
            // inside the overview.
            serviceText = '';
            var negativeStatus = state.snapshot.negative_status && typeof state.snapshot.negative_status === 'object'
                ? state.snapshot.negative_status : {};
            var activeNegativeLabels = [];
            if (negativeStatus.sick && negativeStatus.sick.active) activeNegativeLabels.push('生病');
            if (negativeStatus.sulk && negativeStatus.sulk.active) activeNegativeLabels.push('赌气');
            if (activeNegativeLabels.length) {
                serviceText = '当前状态：' + activeNegativeLabels.join('、') + '。恢复任务在任务列表中处理。';
            }
        }
        ui.service.textContent = serviceText;
        ui.service.hidden = !serviceText;
        var view = state.selectedView || 'overview';
        var activeExplorationAvailable = !!state.snapshot && explorationActive(currentExploration());
        var loggedInSurface = status === 'logged-in' && binding === 'bound' && !!state.snapshot &&
            (state.serviceStatus === 'ready' || activeExplorationAvailable);
        renderExplorationSideCards();
        // The exploration state keeps the same scene slot as the normal cat
        // state. The cat itself is gone, while the nest/timer occupy its
        // place; hiding the entire scene made the desktop surface lose the
        // visual relationship shown in the reference demo.
        ui.scene.hidden = !loggedInSurface || (!activeExplorationAvailable && view !== 'overview');
        ui.explorationVisual.hidden = !activeExplorationAvailable;
        if (activeExplorationAvailable) {
            var exploration = currentExploration();
            var location = explorationLocation(exploration);
            var nestAsset = str(exploration && (exploration.asset_url || exploration.assetUrl)) ||
                str(location && (location.asset_url || location.assetUrl)) ||
                '/static/assets/neko-idle/cat-bad.png';
            if (ui.explorationNestImage && ui.explorationNestImage.getAttribute('src') !== nestAsset) {
                ui.explorationNestImage.src = nestAsset;
            }
            ui.scene.dataset.exploration = 'active';
        } else {
            ui.scene.dataset.exploration = 'inactive';
            if (ui.explorationNestImage) {
                if (typeof ui.explorationNestImage.removeAttribute === 'function') {
                    ui.explorationNestImage.removeAttribute('src');
                } else if (typeof ui.explorationNestImage.setAttribute === 'function') {
                    // Keep the lightweight stage harness compatible with the real DOM.
                    ui.explorationNestImage.setAttribute('src', '');
                }
            }
        }
        ui.explorationStatus.hidden = !activeExplorationAvailable;
        // The status card is the overview landing block. Once an immediate
        // operation is selected, its detail card takes this same slot.
        ui.grid.hidden = !loggedInSurface || activeExplorationAvailable || view !== 'overview' || !!state.selectedCategory;
        ui.actions.hidden = !loggedInSurface || view !== 'overview' || activeExplorationAvailable;
        ui.navButtons.forEach(function (item) {
            var active = item.dataset.view === view;
            if (item.classList && typeof item.classList.toggle === 'function') {
                item.classList.toggle('is-active', active);
            }
            item.setAttribute('aria-current', active ? 'page' : 'false');
            item.disabled = !loggedInSurface;
        });
        ui.overview.hidden = !loggedInSurface || view !== 'overview';
        ui.lettersModule.hidden = !loggedInSurface || view !== 'letters';
        ui.inventoryModule.hidden = !loggedInSurface || view !== 'inventory';
        ui.profileModule.hidden = !loggedInSurface || view !== 'profile';
        // Unknown auth is recoverable: keep the login path visible until the
        // shared account authority confirms a logged-in session again.
        ui.loginButton.hidden = status === 'logged-in';
        ui.loginButton.disabled = state.oauthPending;
        ui.loginButton.textContent = state.oauthPending ? '登录中…' : '登录 N.E.K.O 账号';
        ui.bindButton.hidden = !(status === 'logged-in' && binding === 'needs-binding');
        ui.retryButton.hidden = !(status !== 'logged-in' || binding === 'unknown' ||
            state.serviceStatus === 'unavailable' || state.serviceStatus === 'auth-refreshing');
        ui.logoutButton.hidden = status !== 'logged-in';
        var stage5Available = stage5Ready();
        ui.task.hidden = !loggedInSurface || view !== 'tasks' || !stage5Available;
        // Account controls belong to the profile/auth slot. Keeping the
        // footer out of the overview prevents a logout button from appearing
        // next to inventory/task previews and preserves the demo hierarchy.
        ui.footer.hidden = loggedInSurface ? view !== 'profile' : false;
        if (!stage5Available) {
            ui.taskList.replaceChildren ? ui.taskList.replaceChildren() : (ui.taskList.children = []);
            ui.taskError.hidden = true; ui.taskError.textContent = '';
        } else {
            var items = taskItems();
            if (ui.taskList.replaceChildren) ui.taskList.replaceChildren();
            else { ui.taskList.children = []; ui.taskList.textContent = ''; }
            items.forEach(function (item) {
                    if (!item || typeof item !== 'object') return;
                    var row = document.createElement('div'); row.className = 'neko-pet-care-task-item';
                    var tick = document.createElement('span'); tick.className = 'neko-pet-care-task-tick';
                    var taskStatus = str(item.status).toLowerCase();
                    tick.dataset.status = taskStatus || 'available';
                    tick.setAttribute('aria-hidden', 'true');
                    tick.textContent = taskStatus === 'claimed' ? '✓' : '';
                    var copy = document.createElement('div'); copy.className = 'neko-pet-care-task-copy';
                    var taskKey = String(item.task_id || item.id || '');
                    var name = document.createElement('strong'); name.textContent = String(item.title || taskKey || '任务');
                    var descriptionText = str(item.description);
                    if (!descriptionText && typeof item.condition === 'string') descriptionText = str(item.condition);
                    copy.appendChild(name);
                    if (descriptionText) {
                        var description = document.createElement('span');
                        description.textContent = descriptionText;
                        copy.appendChild(description);
                    }
                    var reward = item.reward && typeof item.reward === 'object' ? item.reward : {};
                    var quantity = Number(reward.quantity);
                    if (!Number.isFinite(quantity) || quantity <= 0) quantity = 0;
                    var rewardText = null;
                    if (quantity > 0) {
                        rewardText = document.createElement('span'); rewardText.className = 'neko-pet-care-task-reward';
                        rewardText.textContent = '+' + quantity + ' ' + taskRewardLabel(reward.item_id);
                    }
                    // Reward-only tasks do not have a
                    // progress contract.  Do not coerce missing/null values
                    // through Number(), because Number(null) creates the
                    // misleading 0/0 counter.  Progress is shown only when
                    // the service returned both sides of the contract.
                    var hasProgressContract = item.progress != null && item.target != null;
                    var progress = hasProgressContract ? Number(item.progress) : NaN;
                    var target = hasProgressContract ? Number(item.target) : NaN;
                    var progressText = null;
                    if (Number.isFinite(progress) && Number.isFinite(target)) {
                        progressText = document.createElement('span');
                        progressText.className = 'neko-pet-care-task-progress-value';
                        progressText.textContent = Number.isFinite(target) ?
                            Math.max(0, Math.floor(progress)) + '/' + Math.max(0, Math.floor(target)) :
                            String(Math.max(0, Math.floor(progress)));
                    }
                    var claim = null;
                    var recoveryStatus = str(item.recovery_status).toLowerCase();
                    var recoveryAlreadyResolved = ['sick', 'sulk'].indexOf(recoveryStatus) >= 0 && item.status === 'completed';
                    if (item.claimable !== false && !recoveryAlreadyResolved) {
                        claim = button(item.status === 'claimed' ? '已领取' :
                            state.taskPendingId === taskKey ? '领取中…' : '领取', 'neko-pet-care-task-claim');
                        claim.dataset.taskId = taskKey;
                        var canClaim = item.status !== 'claimed' && item.claimable !== false &&
                            state.taskPendingId !== taskKey && !state.taskPendingId && !state.taskLoading &&
                            !state.dailyCheckInPending && !state.pendingAction && !state.playActivity && !state.playPending;
                        claim.disabled = !canClaim;
                        claim.setAttribute('aria-disabled', canClaim ? 'false' : 'true');
                    }
                    var recovery = null;
                    if (recoveryStatus && item.status === 'claimed' && ['sick', 'sulk'].indexOf(recoveryStatus) >= 0) {
                        recovery = button(state.recoveryPendingStatus === recoveryStatus ? '使用中…' : '使用', 'neko-pet-care-task-recovery');
                        recovery.dataset.taskId = taskKey;
                        recovery.dataset.recoveryStatus = recoveryStatus;
                        var activeState = state.snapshot && state.snapshot.negative_status && state.snapshot.negative_status[recoveryStatus];
                        var canUse = !!(activeState && activeState.active) && inventoryCount(str(item.recovery_item_id || reward.item_id)) > 0 &&
                            state.recoveryPendingStatus !== recoveryStatus && !state.taskPendingId && !state.taskLoading &&
                            !state.pendingAction && !state.playActivity && !state.playPending;
                        recovery.disabled = !canUse;
                        recovery.setAttribute('aria-disabled', canUse ? 'false' : 'true');
                        recovery.title = canUse ? '使用恢复物品' : '等待服务确认恢复条件';
                    }
                    row.appendChild(tick); row.appendChild(copy);
                    if (progressText) row.appendChild(progressText);
                    if (rewardText) row.appendChild(rewardText);
                    if (claim) row.appendChild(claim);
                    if (recovery) row.appendChild(recovery);
                    ui.taskList.appendChild(row);
            });
        }
        ui.taskError.textContent = state.taskError ? '任务操作未完成：' + taskErrorText(state.taskError) : '';
        ui.taskError.hidden = !state.taskError || !stage5Available;
        // Tasks are a dedicated rail module. The overview deliberately has no
        // task preview or claim shortcut; this prevents service task data from
        // being duplicated in two different surfaces.
        if (ui.lettersList) {
            if (typeof ui.lettersList.replaceChildren === 'function') ui.lettersList.replaceChildren();
            else { ui.lettersList.children = []; ui.lettersList.textContent = ''; }
            var availableLetters = [];
            var snapshotLetters = state.snapshot && (state.snapshot.letters || state.snapshot.inbox);
            if (Array.isArray(snapshotLetters)) availableLetters = snapshotLetters;
            if (!availableLetters.length && activeExplorationAvailable) availableLetters = explorationLetters(currentExploration());
            if (!availableLetters.length) {
                var emptyLetters = document.createElement('p'); emptyLetters.className = 'neko-pet-care-empty';
                emptyLetters.textContent = '暂无来信'; ui.lettersList.appendChild(emptyLetters);
            } else availableLetters.slice(0, 20).forEach(function (letter) {
                var row = document.createElement('article'); row.className = 'neko-pet-care-letter-row';
                var heading = document.createElement('strong'); heading.textContent = str(letter && (letter.title || letter.name)) || '来信';
                var body = document.createElement('span'); body.textContent = explorationText(letter, '有一封新的来信。');
                row.appendChild(heading); row.appendChild(body); ui.lettersList.appendChild(row);
            });
            // A single unread marker belongs to the letters rail item as in
            // the demo; the complete list remains in the letters module.
            var hasLetters = availableLetters.length > 0;
            ui.navButtons.forEach(function (item) {
                if (item.dataset.view !== 'letters' || !item.classList || typeof item.classList.toggle !== 'function') return;
                item.classList.toggle('has-alert', hasLetters);
            });
        }
        if (ui.inventoryList) {
            if (typeof ui.inventoryList.replaceChildren === 'function') ui.inventoryList.replaceChildren();
            else { ui.inventoryList.children = []; ui.inventoryList.textContent = ''; }
            var inventory = state.snapshot && state.snapshot.inventory;
            var inventoryEntries = [];
            if (inventory && typeof inventory === 'object' && !Array.isArray(inventory)) {
                Object.keys(inventory).forEach(function (itemId) {
                    var item = inventory[itemId]; var count = item && typeof item === 'object'
                        ? (item.quantity != null ? item.quantity : item.count) : item;
                    inventoryEntries.push({ id: itemId, count: count, label: item && typeof item === 'object' ? (item.label || item.name) : '' });
                });
            }
            if (!inventoryEntries.length) {
                var emptyInventory = document.createElement('p'); emptyInventory.className = 'neko-pet-care-empty';
                emptyInventory.textContent = '背包暂无已确认道具'; ui.inventoryList.appendChild(emptyInventory);
            } else inventoryEntries.forEach(function (entry) {
                var presentation = inventoryPresentation(entry.id, inventory[entry.id]);
                var card = document.createElement('article'); card.className = 'neko-pet-care-inventory-item';
                card.dataset.itemId = entry.id;
                var icon = document.createElement('span'); icon.className = 'neko-pet-care-inventory-icon';
                icon.setAttribute('aria-hidden', 'true'); icon.textContent = presentation.icon;
                var label = document.createElement('span'); label.className = 'neko-pet-care-inventory-label';
                label.textContent = presentation.label;
                var count = document.createElement('b'); count.className = 'neko-pet-care-inventory-count';
                count.textContent = String(entry.count == null ? 0 : entry.count);
                card.appendChild(icon); card.appendChild(label); card.appendChild(count);
                ui.inventoryList.appendChild(card);
            });
        }
        if (ui.profileBody) {
            if (typeof ui.profileBody.replaceChildren === 'function') ui.profileBody.replaceChildren();
            else { ui.profileBody.children = []; ui.profileBody.textContent = ''; }
            var profileRows = [
                ['账号', account || state.accountId || '已连接'],
                ['当前角色', state.currentCharacterName || state.currentCharacterUid || '—'],
                ['账号宠物', state.snapshot && state.snapshot.pet_id ? '1 只' : '1 只'],
                ['绑定状态', binding === 'bound' ? '已绑定' : (binding || '—')],
            ];
            profileRows.forEach(function (entry) {
                var row = document.createElement('div'); row.className = 'neko-pet-care-profile-row';
                var label = document.createElement('span'); label.textContent = entry[0];
                var val = document.createElement('b'); val.textContent = String(entry[1]); row.appendChild(label); row.appendChild(val);
                ui.profileBody.appendChild(row);
            });
        }
        ui.actionButtons.forEach(function (button) {
            var category = button.dataset.category || '';
            // The play category only exposes configured consumable content.
            // Existing yarn interactions are tracked through the task
            // progress area and never become a new click-to-play action here.
            var explorationButton = category === 'exploration';
            var sickActive = !!(state.snapshot && state.snapshot.negative_status &&
                state.snapshot.negative_status.sick && state.snapshot.negative_status.sick.active);
            var enabled = explorationButton
                ? (stage5Ready() || activeExplorationAvailable) &&
                    (state.catLocalActive || activeExplorationAvailable) && !state.explorationPending &&
                    !state.devFillPending && !sickActive
                : ((hasConfiguredCareCategory(category) || category === 'play')) &&
                    careReady() && !state.pendingAction && !state.playActivity && !state.playPending && !state.devFillPending;
            button.disabled = !enabled;
            button.setAttribute('aria-disabled', enabled ? 'false' : 'true');
            button.setAttribute('title', enabled ? (category === 'play' ? '查看陪玩' : category === 'exploration' ? '查看探索' : '查看' + actionLabel(category) + '操作') :
                (sickActive && explorationButton ? '生病期间不能探索' : state.pendingAction ? '操作处理中…' : '当前不可用'));
            button.setAttribute('aria-expanded', state.selectedCategory === category ? 'true' : 'false');
        });
        if (ui.actionDetails) {
            if (typeof ui.actionDetails.replaceChildren === 'function') ui.actionDetails.replaceChildren();
            else { ui.actionDetails.children = []; ui.actionDetails.textContent = ''; }
            // An active exploration always owns the independent right-hand
            // activity card, even after refresh or a view transition. Do not
            // require a hidden exploration button click to rediscover it.
            var selected = activeExplorationAvailable ? 'exploration' : state.selectedCategory;
            var canShowDetails = (activeExplorationAvailable || view === 'overview') && selected && (careContent(selected).length > 0 || selected === 'play' || selected === 'exploration') &&
                (selected === 'exploration' ? activeExplorationAvailable || stage5Ready() : isPetCareCompatibilityReady()) && state.snapshot &&
                (selected === 'exploration' || !explorationActive(state.snapshot.exploration)) &&
                (selected !== 'play' || playActionEntries().length > 0);
            ui.actionDetails.hidden = !canShowDetails;
            if (canShowDetails) {
                // The exploration card owns its place title; adding a generic
                // “探索” heading above it duplicates the demo hierarchy.
                if (selected !== 'exploration') {
                    var heading = document.createElement('div');
                    heading.className = 'neko-pet-care-action-heading';
                    heading.textContent = selected === 'play' ? '陪玩' : actionLabel(selected) + ' · 选择具体操作';
                    ui.actionDetails.appendChild(heading);
                }
                if (selected === 'exploration') {
                    renderExplorationDetails(ui.actionDetails);
                } else if (selected === 'play') {
                    playActionEntries().forEach(function (item) {
                        var counter = item.action || {};
                        var quantity = Number(counter.inventory_quantity);
                        var dailyCount = Number(counter.daily_count);
                        var dailyLimit = Number(counter.daily_limit);
                        var reason = '';
                        if (state.playActivity) reason = '操作处理中…';
                        else if (Number.isFinite(dailyLimit) && Number.isFinite(dailyCount) && dailyCount >= dailyLimit) {
                            reason = '今日次数已用完';
                        } else if (Number.isFinite(quantity) && quantity <= 0) {
                            reason = '库存不足';
                        }
                        var toyButton = button(item.label, 'neko-pet-care-content-action');
                        toyButton.dataset.category = 'play';
                        toyButton.dataset.contentId = item.id;
                var available = careReady() && !state.pendingAction && !state.playActivity && !state.playPending &&
                    !state.devFillPending && !reason;
                        toyButton.disabled = !available;
                        toyButton.setAttribute('aria-disabled', available ? 'false' : 'true');
                        toyButton.setAttribute('title', available ? '使用' + item.label : (reason || '当前不可用'));
                        var meta = document.createElement('span');
                        meta.className = 'neko-pet-care-content-meta';
                        meta.textContent = reason || '库存 × ' + (Number.isFinite(quantity) ? Math.max(0, Math.floor(quantity)) : 0);
                        toyButton.appendChild(meta);
                        ui.actionDetails.appendChild(toyButton);
                    });
                } else careContent(selected).forEach(function (item) {
                    var contentButton = button(item.label, 'neko-pet-care-content-action');
                    contentButton.dataset.category = selected;
                    contentButton.dataset.contentId = item.id;
                    var count = selected === 'feeding' ? inventoryCountForAction(selected, item.id) : null;
                    // These are read-only hints from the confirmed snapshot.
                    // The service repeats every check and remains the
                    // authority, so a stale client can only fail closed or
                    // receive a normal server rejection.
                    var remaining = cooldownRemainingSeconds(selected, item.id);
                    var reason = '';
                    if (state.pendingAction) reason = '操作处理中…';
                    else if (remaining > 0) reason = cooldownLabel(remaining);
                    else if (selected === 'feeding' && count <= 0) reason = '库存不足';
                        var available = careReady() && !state.pendingAction && !state.playActivity && !state.playPending &&
                            !state.devFillPending && !reason;
                    contentButton.disabled = !available;
                    contentButton.setAttribute('aria-disabled', available ? 'false' : 'true');
                    contentButton.setAttribute('title', available ? '执行' + item.label :
                        (reason || '当前不可用'));
                    var meta = document.createElement('span');
                    meta.className = 'neko-pet-care-content-meta';
                    meta.textContent = reason || (selected === 'feeding' ? '库存 × ' + count : '清洁操作');
                    contentButton.appendChild(meta);
                    ui.actionDetails.appendChild(contentButton);
                });
            }
        }
        if (ui.actionError) {
        var visibleActionError = state.actionError || (state.selectedCategory === 'play' ? state.playError : '');
        ui.actionError.textContent = visibleActionError ? '操作未完成：' + careErrorText(visibleActionError) : '';
        ui.actionError.hidden = !visibleActionError;
        }
        ui.stats.forEach(function (item) {
            var raw = value(item.key);
            item.value.textContent = raw;
            item.value.dataset.unit = item.unit || '';
            if (item.key === 'progress' && item.element) {
                item.element.setAttribute('aria-expanded', state.progressUnlockOpen ? 'true' : 'false');
            }
            if (item.unitNode) item.unitNode.textContent = item.unit || '';
            if (item.fill) {
                var numeric = Number(raw);
                var percent = Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
                item.fill.style.width = percent + '%';
            }
        });
        renderProgressUnlocks();
        scheduleCooldownRefresh();
        scheduleStatusRefresh();
    }
    async function fetchPetCareSnapshot(uid) {
        var clientFactory = window.NekoPetCareClient;
        var client = clientFactory && typeof clientFactory.create === 'function'
            ? clientFactory.create({ characterUid: uid, timeoutMs: CARE_REQUEST_TIMEOUT_MS }) : null;
        var path = client ? client.path('') : petCareEndpoint('', uid);
        var response = await (client ? client.snapshot(uid) : fetch(path, {
            credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json' },
        }));
        // Stage 1/local deployments may only expose /context. Keep that
        // endpoint as a compatibility fallback; a real service error is not
        // silently converted into a different read path.
        if (response.status === 404 || response.status === 405 || response.status == null) {
            var fallbackPath = '/api/pet-care/context?character_uid=' + encodeURIComponent(uid);
            return client
                ? client.request(fallbackPath, {
                    credentials: 'same-origin', cache: 'no-store',
                    headers: { Accept: 'application/json' },
                })
                : fetch(fallbackPath, {
                    credentials: 'same-origin', cache: 'no-store',
                    headers: { Accept: 'application/json' },
                });
        }
        return response;
    }
    function isAuthExpiredResponse(response, body) {
        var code = str(body && (body.code || body.detail || body.reason)).toLowerCase();
        return !!(response && Number(response.status) === 401) ||
            code === 'login_required' || code === 'unauthorized' || code === 'auth_required';
    }
    function isTransientServiceFailure(response, error) {
        var status = Number(response && response.status);
        if (!response || status === 0 || status === 408 || status === 429 || status >= 500) {
            return true;
        }
        return !!(error && (error.name === 'TypeError' || error.name === 'AbortError'));
    }
    function shouldPreserveExplorationOnServiceFailure(response, error) {
        return explorationActive(currentExploration()) && isTransientServiceFailure(response, error);
    }
    function isSavedSessionStatusUnavailable(body) {
        // The OAuth adapter deliberately keeps a saved local session when a
        // cloud lookup is temporarily unavailable.  This is an auth-status
        // uncertainty, not a confirmed logout and must not enter setAuth's
        // logged-out cleanup path.
        return !!(body && body.logged_in === false && body.session_saved === true &&
            body.rejected !== true);
    }
    function preserveSavedSessionStatusUnavailable(body, reason) {
        var persistedPresence = readAuthPresence();
        var persistedAccountId = str(persistedPresence && persistedPresence.accountId);
        var persistedSession = str(persistedPresence && persistedPresence.sessionGeneration);
        var account = str(state.accountId) || persistedAccountId;
        var session = str(state.sessionGeneration) ||
            str(body && (body.session_generation != null ? body.session_generation : body.session_id)) ||
            persistedSession;
        var currentIdentity = !state.currentCharacterUid ? identity() : null;
        if (currentIdentity && currentIdentity.uid) state.currentCharacterUid = str(currentIdentity.uid);
        if (account) {
            state.authStatus = 'logged-in';
            state.accountId = account;
            state.sessionGeneration = session;
            if (!state.user && body && body.user && typeof body.user === 'object') {
                state.user = Object.assign({}, body.user);
            }
            writeAuthPresence('logged-in', account, session);
        } else {
            // Without a prior account identity, keep the status unknown.  The
            // login path remains available, but no account or pet values are
            // invented from a saved-session boolean alone.
            state.authStatus = 'unknown';
        }
        state.reason = reason || 'oauth-status-unavailable';
        var active = !!account && explorationActive(currentExploration());
        var lease = readExplorationLease();
        if (!active && account && lease && lease.accountId === account &&
            state.currentCharacterUid && lease.characterUid === state.currentCharacterUid &&
            applyExplorationRecoveryLease()) {
            state.serviceReason = 'oauth-status-unavailable';
            state.reason = state.serviceReason;
            syncCatMindCompatibility(state.reason);
            render();
            return true;
        }
        if (active) {
            // The away/nest presentation is the only recoverable entity when
            // the service cannot currently verify auth. Keep it visible, but
            // leave all entity writes and status cards unavailable.
            state.bindingStatus = 'bound';
            state.serviceStatus = 'unavailable';
            state.serviceReason = 'oauth-status-unavailable';
            state.reason = state.serviceReason;
            mode(MODES && MODES.PET_AVAILABLE, state.serviceReason);
            syncCatMindCompatibility(state.reason);
            render();
            return true;
        }
        // A confirmed snapshot must not remain visible while the account
        // authority is uncertain. Clear only the pet-care context; retain the
        // account identity so a successful retry can rebind it.
        state.bindingStatus = 'unavailable'; state.bindingRoleId = '';
        state.snapshot = null; state.settlement = null; state.careActions = null;
        state.optimisticSnapshot = null; state.devTools = null; state.devFillPending = false;
        clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
        state.playPending = null; state.playRetry = null; state.playError = '';
        state.pendingAction = null; state.actionError = ''; state.retryAction = null;
        state.tasks = null; state.taskLoading = false; state.taskPendingId = '';
        state.recoveryPendingStatus = ''; state.taskError = ''; state.dailyCheckInPending = false;
        state.explorationPending = null; state.explorationError = '';
        state.explorationRecallRequested = false; state.explorationReward = null;
        clearExplorationTimer(); state.explorationLocations = null; state.explorationGateActive = false;
        taskRequestGeneration += 1; dailyCheckInGeneration += 1; actionRequestGeneration += 1;
        state.serviceStatus = 'unavailable'; state.serviceReason = 'oauth-status-unavailable';
        context({ accountId: account, currentCharacterUid: state.currentCharacterUid,
            boundRoleId: '', reason: state.serviceReason, forceNewGeneration: true });
        mode(MODES && MODES.PET_UNAVAILABLE, state.serviceReason);
        syncCatMindCompatibility(state.reason);
        render();
        return true;
    }
    async function recoverFromAuthExpiry() {
        state.bindingStatus = 'unavailable';
        state.serviceStatus = 'auth-refreshing';
        state.serviceReason = 'pet-care-auth-expired';
        invalidatePetContext(state.serviceReason);
        // Keep the recoverable state explicit after invalidatePetContext's
        // ordinary-cat transition; setAuth() will either clear it as logged
        // out or leave the retry path visible for a still-valid session.
        state.serviceStatus = 'auth-refreshing';
        await refreshAuthStatus(state.serviceReason);
        return null;
    }
    async function refreshBinding(reason) {
        if (state.authStatus !== 'logged-in') return null;
        if (!state.currentCharacterUid) {
            var currentIdentity = identity();
            if (currentIdentity && currentIdentity.uid) {
                setCharacter(currentIdentity, 'identity-ready');
                return null;
            }
            state.bindingStatus = 'unavailable'; state.bindingRoleId = ''; state.snapshot = null;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            state.devTools = null; state.devFillPending = false;
            state.explorationReward = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playRetry = null; state.playError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
            state.taskError = ''; state.dailyCheckInPending = false;
            taskRequestGeneration += 1; dailyCheckInGeneration += 1;
            state.selectedCategory = '';
            state.progressUnlockOpen = false;
            state.serviceStatus = 'unavailable'; state.serviceReason = 'character-identity-unavailable';
            mode(MODES && MODES.PET_UNAVAILABLE, state.serviceReason); render();
            syncCatMindCompatibility(state.serviceReason);
            return null;
        }
        var request = ++bindingRequestGeneration, expected = state.generation;
        state.bindingPending = true; state.serviceStatus = 'loading';
        state.serviceReason = reason || 'binding-loading'; mode(MODES && MODES.PET_LOADING, state.serviceReason); render();
        var response = null;
        try {
            response = await fetchPetCareSnapshot(state.currentCharacterUid);
            var body = await response.json();
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) {
                return await recoverFromAuthExpiry();
            }
            if (!response.ok) throw new Error((body && (body.detail || body.reason)) || 'pet-care-context-failed');
            applyBinding(body, expected); return snap();
        } catch (error) {
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            // On a reload the in-memory snapshot is gone, so preserve only a
            // previously confirmed away activity through the session-scoped
            // presentation lease. It contains no values or rewards and never
            // enables writes; the next successful binding read replaces it.
            if (!explorationActive(currentExploration()) &&
                isTransientServiceFailure(response, error) && applyExplorationRecoveryLease()) {
                return null;
            }
            if (shouldPreserveExplorationOnServiceFailure(response, error)) {
                // A transient service failure cannot prove that the server
                // activity ended. Keep the last confirmed away snapshot and
                // its entity gate; writes stay unavailable until a later
                // focus/pageshow retry confirms the service again.
                state.bindingStatus = 'bound';
                state.serviceStatus = 'unavailable';
                state.serviceReason = error && error.message || 'service-unavailable';
                mode(MODES && MODES.PET_AVAILABLE, 'exploration-service-unavailable');
                syncCatMindCompatibility(state.serviceReason);
                render();
                return null;
            }
            state.bindingStatus = 'unavailable';
            state.serviceStatus = 'unavailable'; state.serviceReason = error && error.message || 'service-unavailable';
            invalidatePetContext(state.serviceReason); render(); return null;
        } finally {
            if (request === bindingRequestGeneration) { state.bindingPending = false; render(); }
        }
    }
    async function bindCurrentRole() {
        if (state.authStatus !== 'logged-in' || !state.currentCharacterUid || state.bindingPending) return null;
        var request = ++bindingRequestGeneration, expected = state.generation;
        state.bindingPending = true; state.serviceStatus = 'loading'; state.serviceReason = 'binding-create'; render();
        try {
            var bindOptions = {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ character_uid: state.currentCharacterUid }),
            };
            var clientFactory = window.NekoPetCareClient;
            var response = clientFactory && typeof clientFactory.create === 'function'
                ? await clientFactory.create({ timeoutMs: CARE_REQUEST_TIMEOUT_MS }).bind(bindOptions)
                : await fetch('/api/pet-care/bind', bindOptions);
            var body = await response.json();
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) {
                return await recoverFromAuthExpiry();
            }
            if (!response.ok) throw new Error((body && (body.detail || body.reason)) || 'pet-care-bind-failed');
            applyBinding(body, expected); return snap();
        } catch (error) {
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            state.bindingStatus = 'unavailable';
            state.serviceStatus = 'unavailable'; state.serviceReason = error && error.message || 'bind-failed';
            invalidatePetContext(state.serviceReason); render(); return null;
        } finally {
            if (request === bindingRequestGeneration) { state.bindingPending = false; render(); }
        }
    }
    function closeOAuthPopup() {
        var popup = oauthPopup;
        oauthPopup = null;
        if (!popup || popup.closed || typeof popup.close !== 'function') return;
        try { popup.close(); } catch (_) {}
    }
    function cancelOAuthLogin() {
        if (!state.oauthPending) return false;
        oauthAttemptGeneration += 1;
        if (oauthPollTimer) { window.clearTimeout(oauthPollTimer); oauthPollTimer = 0; }
        closeOAuthPopup();
        var resolve = oauthCancelResolver;
        oauthCancelResolver = null;
        state.oauthPending = false;
        state.reason = 'oauth-cancelled-by-user';
        if (resolve) resolve('oauth-cancelled-by-user');
        render();
        return true;
    }
    async function startLogin() {
        if (state.oauthPending) return;
        var attempt = ++oauthAttemptGeneration;
        state.oauthPending = true; state.reason = 'oauth-start'; render();
        try {
            var response = await fetch('/api/card-drop/oauth/start', {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
            });
            if (attempt !== oauthAttemptGeneration) return;
            var body = await response.json();
            if (!response.ok || !body || !body.auth_url || !body.state) throw new Error('oauth-start-failed');
            var popup = typeof window.open === 'function'
                ? window.open(body.auth_url, 'neko-pet-care-login', 'popup,width=520,height=720') : null;
            if (!popup) throw new Error('oauth-popup-blocked');
            oauthPopup = popup;
            var token = body.state, started = Date.now();
            var result = await new Promise(function (resolve) {
                oauthCancelResolver = resolve;
                var poll = async function () {
                    if (attempt !== oauthAttemptGeneration) { resolve('oauth-cancelled-by-user'); return; }
                    if (Date.now() - started > 10 * 60 * 1000) { resolve(); return; }
                    try {
                        var done = await fetch('/api/card-drop/oauth/completion?state=' +
                            encodeURIComponent(token), { credentials: 'same-origin', cache: 'no-store' });
                        if (done.ok && (await done.json()).logged_in) { resolve('oauth-complete'); return; }
                    } catch (_) {}
                    if (popup.closed) { resolve('oauth-cancelled'); return; }
                    oauthPollTimer = window.setTimeout(poll, 700);
                }; poll();
            });
            if (result === 'oauth-cancelled-by-user') return;
            if (result === 'oauth-cancelled') throw new Error('oauth-cancelled');
            closeOAuthPopup();
            await refreshAuthStatus('oauth-complete');
        } catch (error) { state.reason = error && error.message || 'oauth-start-failed'; render(); }
        finally {
            oauthCancelResolver = null;
            closeOAuthPopup();
            state.oauthPending = false;
            if (oauthPollTimer) { window.clearTimeout(oauthPollTimer); oauthPollTimer = 0; }
            render();
        }
    }
    async function logout() {
        // End the old account's exploration while its session is still
        // available. If the service cannot confirm recall, keep the account
        // and activity in place so the user can retry instead of logging out
        // into an orphaned server activity.
        if (!(await ensureExplorationEndedBeforeAuthChange(
            'logged-out', '', '', 'logout'))) {
            showPetCareNotice('退出前的探索结算未获服务确认，仍保留当前账号和探索状态，请重试。', 'warning');
            return null;
        }
        try {
            var response = await fetch('/api/card-drop/oauth/logout', {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
            });
            if (!response.ok) throw new Error('oauth-logout-failed');
        } catch (error) {
            state.reason = error && error.message || 'oauth-logout-failed';
            state.bindingStatus = 'unavailable'; state.bindingRoleId = ''; state.snapshot = null;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            state.devTools = null; state.devFillPending = false;
            state.explorationReward = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playRetry = null; state.playError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
            state.taskError = ''; state.dailyCheckInPending = false;
            taskRequestGeneration += 1; dailyCheckInGeneration += 1;
            state.selectedCategory = '';
            state.progressUnlockOpen = false;
            state.serviceStatus = 'unavailable';
            // A failed logout request cannot prove that the account session
            // ended, but it does invalidate the pet-care context locally.
            // Release Cat Mind compatibility and return to ordinary-cat mode
            // so stale autonomous runs are cleaned instead of being left
            // behind the failed account operation.
            mode(MODES && MODES.PET_UNAVAILABLE, state.reason);
            syncCatMindCompatibility(state.reason);
            render();
            showPetCareNotice('退出账号未获得服务确认，养宠状态已暂停，请检查连接后重试。', 'warning');
            return null;
        }
        await refreshAuthStatus('logout');
    }
    async function refreshAuthStatus(reason) {
        var request = ++authRequestGeneration;
        var response = null;
        try {
            response = await fetch('/api/card-drop/oauth/status', { credentials: 'same-origin', cache: 'no-store' });
            var body = await response.json();
            if (request !== authRequestGeneration) return null;
            if (!response.ok) throw new Error('oauth-status-failed');
            if (isSavedSessionStatusUnavailable(body)) {
                preserveSavedSessionStatusUnavailable(body, reason || 'auth-refresh');
                return snap();
            }
            await setAuth(body, reason || 'auth-refresh', request); return snap();
        } catch (error) {
            if (request !== authRequestGeneration) return null;
            var lease = readExplorationLease();
            if (isTransientServiceFailure(response, error) && lease &&
                (!state.accountId || state.accountId === lease.accountId) &&
                (!state.currentCharacterUid || state.currentCharacterUid === lease.characterUid)) {
                state.accountId = lease.accountId;
                state.authStatus = 'logged-in';
                if (!state.currentCharacterUid) state.currentCharacterUid = lease.characterUid;
                if (applyExplorationRecoveryLease()) return null;
            }
            if (shouldPreserveExplorationOnServiceFailure(response, error)) {
                // A transient account-status transport failure does not prove
                // logout. Keep the last confirmed away activity and nest
                // visible, stop writes, and let the next focus/pageshow retry
                // re-authorize the same account before changing context.
                state.bindingStatus = 'bound';
                state.serviceStatus = 'unavailable';
                state.serviceReason = 'oauth-status-unavailable';
                mode(MODES && MODES.PET_AVAILABLE, state.serviceReason);
                syncCatMindCompatibility(state.serviceReason);
                render();
                return null;
            }
            // Do not keep displaying a previously confirmed pet snapshot when
            // the account authority cannot currently be reached.
            state.bindingStatus = 'unavailable'; state.bindingRoleId = ''; state.snapshot = null;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.recoveryPendingStatus = '';
            state.taskError = ''; state.dailyCheckInPending = false;
            taskRequestGeneration += 1; dailyCheckInGeneration += 1;
            state.serviceStatus = 'unavailable'; state.serviceReason = 'oauth-status-unavailable';
            // Auth authority is unavailable, so fail closed to ordinary-cat
            // mode even if the previous local state said logged-in.
            state.authStatus = 'unknown';
            invalidatePetContext('oauth-status-unavailable', { clearAccount: true });
            render(); return null;
        }
    }
    function dismissFeatureHint() { state.featureHintVisible = false; }
    function closePanel(options) {
        // Exploration has no cat entity to hover again.  Hiding the only
        // surface in this state would strand the nest, letters and recall
        // controls, so an ordinary outside-click/Escape is intentionally a
        // no-op.  Model/role/account exits still use the explicit lifecycle
        // paths and may pass force=true when they have already recalled the
        // activity.
        if (explorationActive(currentExploration()) && !(options && options.force === true)) {
            state.panelOpen = true;
            visible(true);
            render();
            positionOverlay();
            return;
        }
        if ((!options || options.cancelOAuth !== false) && state.oauthPending) cancelOAuthLogin();
        dismissFeatureHint(); state.progressUnlockOpen = false; state.panelOpen = false;
        // There is no separate launcher. Closing the surface hides the whole
        // entry until the semantic cat anchor becomes available again.
        visible(false); render();
    }
    function loadTasksWhenPanelOpens() {
        // The pet snapshot may contain a task catalogue materialized by an
        // older service/config revision. Always reconcile through the task
        // endpoint when the panel opens; otherwise a truthy stale
        // `state.tasks` would prevent newly configured same-day items (such
        // as the yarn task) from ever reaching the list.
        if (!state.panelOpen || !stage5Ready() || state.taskLoading) return;
        refreshTasks();
    }
    function button(text, className) {
        var value = document.createElement('button'); value.type = 'button';
        value.textContent = text; value.className = className || '';
        // Mark individual controls for the desktop input-region bridge. The
        // surface and its transparent layout containers remain unmarked.
        value.setAttribute('data-neko-pet-care-interactive', 'true');
        return value;
    }
    function ensureUi() {
        if (ui || !document || !document.body) return;
        var root = document.createElement('div'); root.id = OVERLAY_ID;
        root.dataset.visible = 'false'; root.dataset.expanded = 'false'; root.dataset.available = 'false';
        root.setAttribute('role', 'region'); root.setAttribute('aria-label', '宠物养成入口');
        var panel = document.createElement('section'); panel.className = 'neko-pet-care-surface';
        panel.setAttribute('aria-label', '养宠状态和操作');
        var close = button('×', 'neko-pet-care-close-button');
        close.setAttribute('aria-label', '关闭养宠面板'); close.setAttribute('title', '关闭');
        var scene = document.createElement('div'); scene.className = 'neko-pet-care-scene';
        var explorationVisual = document.createElement('div'); explorationVisual.className = 'neko-pet-care-exploration-visual';
        explorationVisual.hidden = true;
        var explorationNest = document.createElement('div'); explorationNest.className = 'neko-pet-care-exploration-nest';
        var explorationNestImage = document.createElement('img'); explorationNestImage.className = 'neko-pet-care-exploration-nest-image';
        explorationNestImage.alt = '猫窝'; explorationNestImage.draggable = false;
        explorationNest.appendChild(explorationNestImage); explorationVisual.appendChild(explorationNest); scene.appendChild(explorationVisual);
        var explorationStatus = document.createElement('div'); explorationStatus.className = 'neko-pet-care-exploration-status-card';
        explorationStatus.hidden = true;
        var explorationStatusFace = document.createElement('span'); explorationStatusFace.className = 'neko-pet-care-exploration-status-face'; explorationStatusFace.textContent = '✦';
        var explorationStatusTitle = document.createElement('b'); explorationStatusTitle.className = 'neko-pet-care-exploration-status-title';
        var explorationStatusLocation = document.createElement('small'); explorationStatusLocation.className = 'neko-pet-care-exploration-status-location';
        explorationStatus.appendChild(explorationStatusFace); explorationStatus.appendChild(explorationStatusTitle); explorationStatus.appendChild(explorationStatusLocation);
        var grid = document.createElement('div'); grid.className = 'neko-pet-care-stats';
        var statsHead = document.createElement('div'); statsHead.className = 'neko-pet-care-stats-head';
        var statsTitle = document.createElement('strong'); statsTitle.textContent = '宠物状态';
        statsHead.appendChild(statsTitle); grid.appendChild(statsHead);
        var labels = ['饱食度', '精力', '清洁度', '心情', '养成进度'];
        var keys = ['satiety', 'energy', 'cleanliness', 'mood', 'progress'], stats = [];
        labels.forEach(function (label, index) {
            var item = document.createElement('div'); item.className = 'neko-pet-care-stat';
            var top = document.createElement('div'); top.className = 'neko-pet-care-stat-top';
            var name = document.createElement('span'), valueNode = document.createElement('b');
            var unitNode = document.createElement('small'); unitNode.className = 'neko-pet-care-stat-unit';
            var unit = keys[index] === 'progress' ? '点' : '/100';
            name.textContent = label; valueNode.textContent = '—'; unitNode.textContent = unit;
            valueNode.dataset.unit = unit; top.appendChild(name);
            var valueLine = document.createElement('span'); valueLine.className = 'neko-pet-care-stat-value';
            valueLine.appendChild(valueNode); valueLine.appendChild(unitNode); top.appendChild(valueLine);
            var bar = document.createElement('div'); bar.className = 'neko-pet-care-stat-bar';
            var fill = document.createElement('i'); bar.appendChild(fill);
            item.appendChild(top); item.appendChild(bar);
            if (keys[index] === 'progress') {
                item.classList.add('neko-pet-care-progress-stat');
                item.setAttribute('role', 'button');
                item.setAttribute('tabindex', '0');
                item.setAttribute('aria-label', '查看已解锁内容');
                item.setAttribute('aria-expanded', 'false');
                item.setAttribute('data-neko-pet-care-interactive', 'true');
            }
            grid.appendChild(item); stats.push({ key: keys[index], value: valueNode, unitNode: unitNode, fill: fill, unit: unit, element: item });
        });
        var progressStat = stats.find(function (item) { return item.key === 'progress'; });
        if (progressStat && progressStat.key === 'progress') {
            var toggleProgressUnlocks = function (event) {
                if (event && event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
                if (event && event.type === 'keydown') event.preventDefault();
                state.progressUnlockOpen = !state.progressUnlockOpen;
                progressStat.element.setAttribute('aria-expanded', state.progressUnlockOpen ? 'true' : 'false');
                render();
            };
            progressStat.element.addEventListener('click', toggleProgressUnlocks);
            progressStat.element.addEventListener('keydown', toggleProgressUnlocks);
        }
        var progressUnlockPanel = document.createElement('div');
        progressUnlockPanel.className = 'neko-pet-care-progress-unlock-panel';
        progressUnlockPanel.hidden = true;
        progressUnlockPanel.setAttribute('aria-label', '已解锁内容');
        grid.appendChild(progressUnlockPanel);
        var actions = document.createElement('div'); actions.className = 'neko-pet-care-actions';
        var actionLabels = ['喂食', '清洁', '陪玩', '探索'];
        var actionIcons = ['♨', '⌁', '✦', '↗'];
        var actionCategories = ['feeding', 'cleaning', 'play', 'exploration'];
        var actionButtons = actionLabels.map(function (label, index) {
            var item = button('', 'neko-pet-care-action');
            var icon = document.createElement('b'); icon.className = 'neko-pet-care-action-icon'; icon.textContent = actionIcons[index];
            var textNode = document.createElement('span'); textNode.className = 'neko-pet-care-action-label'; textNode.textContent = label;
            item.appendChild(icon); item.appendChild(textNode); item.dataset.category = actionCategories[index];
            item.dataset.icon = actionIcons[index];
            item.setAttribute('data-neko-pet-care-interactive', 'true'); actions.appendChild(item); return item;
        });
        var actionDetails = document.createElement('div'); actionDetails.className = 'neko-pet-care-action-details';
        var actionError = document.createElement('p'); actionError.className = 'neko-pet-care-action-error'; actionError.hidden = true;
        var task = document.createElement('section'); task.className = 'neko-pet-care-task neko-pet-care-module'; task.setAttribute('aria-label', '任务');
        var taskHeader = document.createElement('div'); taskHeader.className = 'neko-pet-care-task-header';
        var taskTitle = document.createElement('strong'); taskTitle.textContent = '任务'; taskHeader.appendChild(taskTitle);
        var taskList = document.createElement('div'); taskList.className = 'neko-pet-care-task-list';
        var taskError = document.createElement('p'); taskError.className = 'neko-pet-care-task-error'; taskError.hidden = true;
        task.appendChild(taskHeader); task.appendChild(taskList); task.appendChild(taskError);
        var nav = document.createElement('nav'); nav.className = 'neko-pet-care-rail'; nav.setAttribute('aria-label', '养宠模块');
        var navItems = [['overview','总览','⌂'],['tasks','任务','✓'],['letters','来信','✉'],['inventory','背包','▣'],['profile','资料','◎']];
        var navButtons = navItems.map(function (entry) {
            var n = button('', 'neko-pet-care-nav-item');
            var icon = document.createElement('b'); icon.className = 'neko-pet-care-nav-icon'; icon.textContent = entry[2];
            var label = document.createElement('span'); label.className = 'neko-pet-care-nav-label'; label.textContent = entry[1];
            n.appendChild(icon); n.appendChild(label); n.dataset.view = entry[0]; n.dataset.icon = entry[2];
            n.setAttribute('aria-label', entry[1]); n.setAttribute('data-neko-pet-care-interactive','true'); nav.appendChild(n); return n;
        });
        var content = document.createElement('div'); content.className = 'neko-pet-care-content';
        var overview = document.createElement('section'); overview.className = 'neko-pet-care-module neko-pet-care-overview'; overview.dataset.view = 'overview';
        var moduleTitle = document.createElement('h2'); moduleTitle.className = 'neko-pet-care-module-title'; moduleTitle.textContent = '总览'; overview.appendChild(moduleTitle);
        var service = document.createElement('p'); service.className = 'neko-pet-care-service'; overview.appendChild(service);
        // The action detail card is a separate block. Keeping it outside the
        // overview module prevents a selected operation from being nested in
        // the status/preview area and lets the demo slot it independently.
        var lettersModule = document.createElement('section'); lettersModule.className = 'neko-pet-care-module neko-pet-care-letters-module'; lettersModule.dataset.view = 'letters';
        var lettersTitle = document.createElement('h2'); lettersTitle.className = 'neko-pet-care-module-title'; lettersTitle.textContent = '来信'; lettersModule.appendChild(lettersTitle);
        var lettersList = document.createElement('div'); lettersList.className = 'neko-pet-care-letters-list'; lettersModule.appendChild(lettersList);
        var inventoryModule = document.createElement('section'); inventoryModule.className = 'neko-pet-care-module neko-pet-care-inventory-module'; inventoryModule.dataset.view = 'inventory';
        var inventoryTitle = document.createElement('h2'); inventoryTitle.className = 'neko-pet-care-module-title'; inventoryTitle.textContent = '背包'; inventoryModule.appendChild(inventoryTitle);
        var inventoryList = document.createElement('div'); inventoryList.className = 'neko-pet-care-inventory-list'; inventoryModule.appendChild(inventoryList);
        var profileModule = document.createElement('section'); profileModule.className = 'neko-pet-care-module neko-pet-care-profile-module'; profileModule.dataset.view = 'profile';
        var profileTitle = document.createElement('h2'); profileTitle.className = 'neko-pet-care-module-title'; profileTitle.textContent = '资料'; profileModule.appendChild(profileTitle);
        var profileBody = document.createElement('div'); profileBody.className = 'neko-pet-care-profile-body'; profileModule.appendChild(profileBody);
        content.appendChild(overview); content.appendChild(task); content.appendChild(lettersModule); content.appendChild(inventoryModule); content.appendChild(profileModule);
        var footer = document.createElement('div'); footer.className = 'neko-pet-care-panel-footer';
        var login = button('登录 N.E.K.O 账号', 'neko-pet-care-login-button'); var bind = button('绑定当前角色', 'neko-pet-care-bind-button');
        var retry = button('重新检查', 'neko-pet-care-retry-button'); var logoutButton = button('退出账号', 'neko-pet-care-logout-button');
        footer.appendChild(login); footer.appendChild(bind); footer.appendChild(retry); footer.appendChild(logoutButton);
        panel.appendChild(close); panel.appendChild(nav); panel.appendChild(scene); panel.appendChild(grid); panel.appendChild(actions);
        panel.appendChild(explorationStatus); panel.appendChild(actionDetails); panel.appendChild(actionError);
        panel.appendChild(content); panel.appendChild(footer);
        root.appendChild(panel); document.body.appendChild(root);
        ui = { root: root, panel: panel, closeButton: close, stats: stats,
            scene: scene, explorationVisual: explorationVisual, explorationNestImage: explorationNestImage,
            explorationStatus: explorationStatus, explorationStatusTitle: explorationStatusTitle,
            explorationStatusLocation: explorationStatusLocation,
            grid: grid, progressUnlockPanel: progressUnlockPanel, actions: actions, navButtons: navButtons, actionButtons: actionButtons, actionDetails: actionDetails, actionError: actionError,
            overview: overview, task: task, taskList: taskList, taskError: taskError,
            lettersModule: lettersModule, lettersList: lettersList, inventoryModule: inventoryModule, inventoryList: inventoryList,
            profileModule: profileModule, profileBody: profileBody, service: service,
            loginButton: login, bindButton: bind, retryButton: retry, logoutButton: logoutButton, footer: footer };
        navButtons.forEach(function (item) { item.addEventListener('click', function (event) { event.stopPropagation(); setPetCareView(item.dataset.view); }); });
        actionButtons.forEach(function (item) { item.addEventListener('click', function (event) { event.stopPropagation(); setActionCategory(item.dataset.category || ''); }); });
        actionDetails.addEventListener('click', function (event) {
            var target = event && event.target;
            while (target && target !== actionDetails && !target.dataset.contentId && !target.dataset.explorationAction) target = target.parentNode;
            if (!target || target === actionDetails || (!target.dataset.contentId && !target.dataset.explorationAction)) return;
            if (target.disabled || target.getAttribute && target.getAttribute('aria-disabled') === 'true') return;
            event.stopPropagation();
            if (target.dataset.explorationAction === 'start') startExploration(explorationLocation(currentExploration()).id);
            else if (target.dataset.explorationAction === 'recall') requestExplorationRecall({ source: 'pet-care-nest', presentation: 'cat-form' });
            else if (target.dataset.explorationAction === 'complete') completeExploration();
            else if (target.dataset.category === 'play') beginToyPlayActivity(target.dataset.contentId);
            else performCareAction(target.dataset.category, target.dataset.contentId);
        });
        taskList.addEventListener('click', function (event) {
            var target = event && event.target;
            while (target && target !== taskList && !target.dataset.taskId && !target.dataset.recoveryStatus) target = target.parentNode;
            if (!target || target === taskList || !target.dataset.taskId || target.disabled) return;
            event.stopPropagation(); if (target.dataset.recoveryStatus) useRecoveryItem(target.dataset.recoveryStatus); else claimTask(target.dataset.taskId);
        });
        close.addEventListener('click', function (event) { event.stopPropagation(); closePanel(); });
        login.addEventListener('click', function (event) { event.stopPropagation(); startLogin(); });
        bind.addEventListener('click', function (event) { event.stopPropagation(); bindCurrentRole(); });
        retry.addEventListener('click', function (event) { event.stopPropagation(); refreshBinding('manual-retry'); });
        logoutButton.addEventListener('click', function (event) { event.stopPropagation(); logout(); });
        root.addEventListener('mouseenter', showSoon); root.addEventListener('mouseleave', function () { if (!state.panelOpen) hideSoon(); });
        root.addEventListener('click', function (event) { event.stopPropagation(); });
        render(); positionOverlay();
    }
    function initialize() {
        // The standalone chat window is a follower.  It must not create a
        // second surface, refresh the pet snapshot, run model-exit
        // coordination, or show account/exploration notices.  Existing chat
        // yarn events are relayed by the canonical model page's adapter.
        if (!isPetCareOwnerSurface()) return;
        ensureUi();
        // The cat-form fact is published as an event. If the event happened
        // before this surface was initialized, reuse the mode contract's
        // last observed fact rather than guessing from a model implementation.
        try {
            var modeApi = window.nekoPetCareMode;
            var modeState = modeApi && typeof modeApi.getState === 'function'
                ? modeApi.getState() : null;
            if (modeState && typeof modeState.catLocalActive === 'boolean') {
                state.catLocalActive = modeState.catLocalActive;
            }
        } catch (_) {}
        adoptCatFactFromAnchor('semantic-anchor-initial');
        setCharacter(null, 'initial-character'); refreshAuthStatus('initial-auth');
        document.addEventListener('pointermove', handlePointerMove, { passive: true });
        window.addEventListener('resize', positionOverlay);
        window.addEventListener('scroll', positionOverlay, true);
        window.addEventListener('neko:idle-return-ball-state', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            if (detail.visible === true) adoptCatFactFromAnchor(detail.reason || 'semantic-anchor-visible');
            syncAnchor();
        });
        window.addEventListener('neko:idle-return-ball-state', function () {
            // Hiding the return container publishes this event asynchronously.
            // A model return has already committed the cat-to-model transition
            // by then, so a late replay must not put the exploration nest back
            // on top of the visible model.
            if (explorationActive(currentExploration()) &&
                !state.explorationRecallRequested && !state.explorationPending) {
                explorationAsset(true, 'return-container-ready');
            }
        });
        window.addEventListener('neko:cat-return-commit', syncAnchor);
        window.addEventListener('neko:cat-return-complete', syncAnchor);
        // Stage 6 observes the existing user yarn gesture and the existing
        // cat action terminal events. A terminal event without a recent
        // user-initiated lease is ignored, so Cat Mind/autonomous play can
        // never be counted as user陪玩.
        window.addEventListener('neko:cat-mind:observation', function (event) {
            observePlayObservation(event && event.detail);
        });
        window.addEventListener('neko:cat-mind:action-result', function (event) {
            observePlayActionResult(event && event.detail);
        });
        window.addEventListener('neko:cat-local-active-change', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail : {};
            state.catLocalActive = detail.active === true;
            var api = window.nekoPetCareMode;
            if (api && typeof api.setCatLocalActive === 'function') {
                try { api.setCatLocalActive(state.catLocalActive, detail); } catch (_) {}
            }
            // A model/cat switch can keep the same character_uid.  In that
            // case no character-display event is emitted, so the active
            // exploration must be recalled from this form transition itself.
            // Exploration departure does not publish this event; any false
            // transition while an activity is active therefore means the
            // model form has become the visible character.
            if (!state.catLocalActive && explorationActive(currentExploration()) &&
                !state.explorationRecallRequested && !state.explorationPending) {
                ensureExplorationEndedBeforeModelExit({
                    source: detail.reason || 'cat-form-left',
                }).then(function (confirmed) {
                    if (!confirmed) {
                        state.explorationError = 'exploration-recall-failed';
                        render();
                    }
                }, function () {
                    state.explorationError = 'exploration-recall-failed';
                    render();
                });
            }
            if (!state.catLocalActive) {
                if (explorationActive(currentExploration())) {
                    // Cat absence is the expected exploration state. Keep the
                    // panel and nest controls available while entity outputs
                    // are gated by the exploration adapter.
                    clearTimers(); clearPlayActivity('exploration-away');
                    if (ui) ui.root.dataset.available = 'true';
                    visible(true); render();
                } else {
                    clearTimers();
                    clearPlayActivity('cat-form-left');
                    state.playPending = null; state.playRetry = null; state.playError = '';
                    state.panelOpen = false;
                    state.selectedCategory = '';
                    state.progressUnlockOpen = false;
                    state.featureHintVisible = false;
                    if (ui) ui.root.dataset.available = 'false';
                    visible(false);
                    mode(MODES && MODES.PET_UNAVAILABLE, 'cat-form-left');
                }
            } else {
                syncAnchor();
                if (isPetCareCompatibilityReady()) mode(MODES && MODES.PET_AVAILABLE, 'cat-form-entered');
                if (explorationActive(currentExploration())) explorationAsset(true, 'cat-form-entered');
            }
            syncCatMindCompatibility(state.catLocalActive ? 'cat-entered' : 'cat-left');
        });
        window.addEventListener('focus', function () { refreshAuthStatus('focus'); });
        window.addEventListener('pageshow', function () { refreshAuthStatus('pageshow'); });
        document.addEventListener('click', function () {
            if (state.panelOpen && !explorationActive(currentExploration())) closePanel();
        });
        document.addEventListener('keydown', function (event) {
            if (event && event.key === 'Escape' && state.panelOpen && !explorationActive(currentExploration())) closePanel();
        });
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') refreshAuthStatus('visible');
        });
        window.addEventListener('neko:auth-session-change', function (event) {
            // A managed community window only publishes a state change. The
            // local N.E.K.O account adapter remains authoritative here; both
            // login and logout invalidate the cached account/role snapshot.
            if (event && event.detail && typeof event.detail.logged_in === 'boolean') {
                refreshAuthStatus('external-session-change');
            }
        });
        // The mode adapter emits this request before a character display
        // change when an exploration is active.  Consume it at the single
        // lifecycle owner as well as the legacy display broadcast below; the
        // recall promise is shared, so a renderer cannot create a second
        // service settlement.  This keeps the mode contract useful when a
        // caller publishes only the context request.
        window.addEventListener('neko:pet-care:context-change-request', async function (event) {
            if (!isPetCareOwnerSurface()) return;
            var detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail : {};
            var nextUid = str(detail.nextCharacterUid || detail.character_uid ||
                detail.currentCharacterUid || detail.characterUid);
            if (!nextUid || nextUid === state.currentCharacterUid) return;
            var requestContext = detail.current && typeof detail.current === 'object'
                ? detail.current : null;
            var expectedFence = requestContext ? {
                generation: requestContext.generation,
                accountId: requestContext.accountId,
                currentCharacterUid: requestContext.currentCharacterUid || requestContext.characterUid,
                boundRoleId: requestContext.boundRoleId,
            } : {
                generation: state.generation,
                accountId: state.accountId,
                currentCharacterUid: state.currentCharacterUid,
                boundRoleId: state.bindingRoleId,
            };
            if (!contextFenceMatches(expectedFence)) return;
            if (!explorationActive(currentExploration())) {
                setCharacter({ character_uid: nextUid }, detail.reason || 'context-change-request');
                return;
            }
            var recalled = await ensureExplorationEndedBeforeModelExit({
                source: detail.reason || 'context-change-request',
            });
            if (!recalled) {
                state.explorationError = 'exploration-recall-failed';
                render();
                return;
            }
            if (!contextFenceMatches(expectedFence) || state.authStatus !== 'logged-in') return;
            setCharacter({ character_uid: nextUid }, detail.reason || 'context-change-request');
        });
        window.addEventListener('chat-avatar-display-updated', async function (event) {
            var detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail : {};
            var nextUid = str(detail.character_uid || detail.currentCharacterUid || detail.characterUid);
            if (nextUid && nextUid !== state.currentCharacterUid && explorationActive(currentExploration())) {
                var expectedFence = {
                    generation: state.generation,
                    accountId: state.accountId,
                    currentCharacterUid: state.currentCharacterUid,
                    boundRoleId: state.bindingRoleId,
                };
                // setCharacter() clears the old snapshot.  It must therefore
                // run only after the old account/role activity is confirmed
                // recalled; otherwise a role switch loses the retry context.
                var recalled = await ensureExplorationEndedBeforeModelExit({
                    source: detail.reason || 'character-switch',
                });
                if (!recalled) {
                    state.explorationError = 'exploration-recall-failed';
                    render();
                    return;
                }
                if (!contextFenceMatches(expectedFence) || state.authStatus !== 'logged-in') return;
            }
            setCharacter(detail, detail.reason || 'character-display-updated');
        });
        window.addEventListener('neko:pet-care:mode-change', function (event) {
            if (event && event.detail && event.detail.current &&
                event.detail.current.mode === 'exploration-away') {
                clearTimers();
                // The mode change gates cat-entity output; it must not close
                // the panel because the nest, note, letters and recall entry
                // remain usable during exploration.
                if (explorationActive(currentExploration())) {
                    if (ui) ui.root.dataset.available = 'true';
                    visible(true); render();
                }
            }
        });
        window.addEventListener('neko:cat-return-commit', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            var active = currentExploration();
            var hasRecallIdentity = str(detail.exploration_id || detail.explorationId ||
                detail.activity_id || detail.activityId);
            // A model-return commit is the second unified recall entry. If
            // the cat is away, settle the server activity before accepting
            // the new model context, even when the return event has no pet
            // specific fields.
            if (explorationActive(active) && !state.explorationRecallRequested && !state.explorationPending) {
                state.explorationRecallRequested = true;
            }
            if (state.explorationRecallRequested || hasRecallIdentity) {
                commitExplorationRecall(detail);
            }
            syncAnchor();
        });
        // `cat-return-complete` is already a presentation-only sync listener
        // above. Settlement is intentionally owned by the earlier commit
        // hook; re-arming recall here would leave the nest stuck in
        // "waiting" when a no-goodbye return publishes commit and complete
        // back-to-back.
        window.addEventListener('neko:cat-return-abort', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object' ? event.detail : {};
            var active = currentExploration();
            if (!explorationActive(active) || !state.explorationRecallRequested) return;
            var abortedId = str(detail.exploration_id || detail.explorationId ||
                detail.activity_id || detail.activityId);
            if (abortedId && abortedId !== explorationId(active)) return;
            state.explorationRecallRequested = false;
            state.explorationError = 'exploration-recall-rejected';
            render(); syncAnchor();
        });
    }
    window.NekoPetCareActionAdapter = Object.freeze({
        wakeCat1: wakeCat1ForCare,
    });
    window.nekoPetCareEntry = Object.freeze({
        getState: snap, refreshAuthStatus: refreshAuthStatus, refreshBinding: refreshBinding,
        bindCurrentRole: bindCurrentRole, startLogin: startLogin, logout: logout,
        setBindingResult: setBindingResult, positionOverlay: positionOverlay,
        performCareAction: performCareAction, setActionCategory: setActionCategory,
        debugFillStatus: debugFillStatus,
        refreshTasks: refreshTasks, claimTask: claimTask, useRecoveryItem: useRecoveryItem, dailyCheckIn: dailyCheckIn,
        startExploration: startExploration, completeExploration: completeExploration,
        refreshExploration: refreshExploration,
        requestExplorationRecall: requestExplorationRecall,
        commitExplorationRecall: commitExplorationRecall,
        ensureExplorationEndedBeforeModelExit: ensureExplorationEndedBeforeModelExit,
    });
    if (typeof window !== 'undefined') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
        else initialize();
    }
})();
