/**
 * PetCare renderer composition root for the model-owner surface.
 *
 * Transport, endpoint construction, pending-write recovery, role-alias sync
 * and mode arbitration live in dedicated app-pet-care-* modules. This file
 * coordinates the authorised service snapshot with the panel, cat actions,
 * exploration lifecycle and Cat Mind presentation boundary. It does not own
 * long-term rules, rewards, inventory or settlement.
 *
 * The entry surface belongs to the cat form, not a model button. It does not
 * treat Cat Mind hover observations as PetCare input.
 * Account identity comes from the shared N.E.K.O OAuth session; role binding
 * comes from the PetCare service.
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
    // Presentation-only grace period. It prevents the surface from vanishing
    // while the pointer crosses a transparent gap between its visible cards.
    var PANEL_LEAVE_DELAY_MS = 700;
    // Account entry is a presentation hint, not a PetCare business rule. It
    // appears at most once for one visible cat-form episode and then gets out
    // of the way without changing the ordinary-cat fallback.
    var LOGIN_HINT_AUTO_HIDE_MS = 8000;
    // Transport timeout only; it is not a pet-care rule, item CD or reward
    // value. A timed-out request never mutates the browser snapshot.
    var CARE_REQUEST_TIMEOUT_MS = 15000;
    var OVERLAY_ID = 'neko-pet-care-entry-overlay';
    var PET_CARE_UI_ICON_BASE = '/static/assets/neko-idle/pet-care-ui-icon/';
    var PET_CARE_UI_ICON_FILES = Object.freeze({
        overview: 'overview.png', tasks: 'tasks.png', letters: 'letters.png',
        inventory: 'inventory.png', profile: 'profile.png',
        feeding: 'feeding.png', cleaning: 'cleaning.png',
        play: 'play.png', exploration: 'exploration.png',
    });
    // Configuration may select a capability that has shipped, but it may not
    // name a window function or event source directly.  Each adapter keeps the
    // trusted source, concrete runner and terminal identity together so a new
    // item cannot accidentally borrow another item's animation contract.
    // Adding a new visual action therefore remains a reviewed code change.
    var PET_CARE_ACTION_ADAPTERS = Object.freeze({
        eat_snack: Object.freeze({
            kind: 'care', categories: Object.freeze(['feeding']),
            source: 'pet-care-user-action', runner: 'requestCat1Eat',
            presentation: 'cat1-eat', terminal: 'start-acknowledged',
        }),
        stretch: Object.freeze({
            kind: 'care', categories: Object.freeze(['cleaning']),
            source: 'pet-care-user-action', runner: 'requestCat1Stretch',
            presentation: 'cat1-stretch', terminal: 'start-acknowledged',
        }),
        cat1_play_yarn: Object.freeze({
            kind: 'play', source: 'cat-yarn-observation-adapter', runner: 'cat-mind',
            terminalSource: 'cat_mind', terminalActionId: 'cat1_play_yarn',
        }),
        play_teaser_wand: Object.freeze({
            kind: 'play', source: 'pet-care-toy', runner: 'requestCat1PlayTeaserWand',
            terminalSource: 'pet-care-toy', terminalActionId: 'play_teaser_wand',
        }),
    });
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
    // v2 was scoped only by account. Keep it as a read-only migration source;
    // a pet can be rebound, so new presentation receipts also include the
    // canonical bound role before suppressing an activity notice.
    var EXPLORATION_NOTICE_V2_STORAGE_PREFIX = 'neko.pet-care.exploration-notice.v2:';
    var EXPLORATION_NOTICE_STORAGE_PREFIX = 'neko.pet-care.exploration-notice.v3:';
    var SETTLEMENT_NOTICE_STORAGE_PREFIX = 'neko.pet-care.settlement-notice.v1:';
    // Session-scoped presentation lease only. It contains no status values,
    // inventory or reward; it lets a reload keep the nest/gate visible while
    // the authoritative service is temporarily unreachable.
    var EXPLORATION_LEASE_STORAGE_KEY = 'neko.pet-care.exploration-lease.v1';
    var EXPLORATION_LEASE_STORAGE_PREFIX = EXPLORATION_LEASE_STORAGE_KEY + ':';
    // This is only a stale-event lease for correlating an existing user yarn
    // gesture with the cat's later response. It is not an item cooldown,
    // reward limit, or business rule.
    var PLAY_ACTIVITY_LEASE_MS = 30000;
    // Projection schema v3 scopes this short lease to Cat Mind. Long-term
    // status display follows the containing authorised snapshot instead.
    var CAT_MIND_PROJECTION_MAX_WINDOW_MS = 5 * 60 * 1000;
    var state = {
        authStatus: 'unknown', accountId: '', user: null, sessionGeneration: '',
        accountResumePending: false, petRefreshRequired: false, petRefreshSource: '',
        currentCharacterUid: '', currentCharacterName: '', bindingStatus: 'unknown',
        bindingRoleId: '', petId: '', bindingRevision: null, petStateVersion: null,
        canRebind: false,
        serviceStatus: 'unknown', serviceReason: 'initial',
        snapshot: null, settlement: null, careActions: null, optimisticSnapshot: null,
        clientProjection: null, serverClockAnchor: null,
        generation: 0, reason: 'initial', panelOpen: false,
        hoverVisible: false, oauthPending: false, bindingPending: false,
        catLocalActive: false, catMindCompatibilityEnabled: false,
        featureHintVisible: false, loginHintVisible: false,
        loginHintShownForCatEpisode: false,
        selectedView: 'overview', selectedCategory: '', progressUnlockOpen: false,
        progressUnlockAllOpen: false, pendingAction: null,
        actionError: '', pendingWriteRetrying: false,
        pendingWriteError: '', tasks: null, taskLoading: false,
        taskPendingId: '', taskPendingOperation: '', taskError: '',
        expandedTaskId: '',
        recoveryPendingStatus: '',
        playActions: null, playActivity: null, playPending: null, playError: '',
        explorationPending: null, explorationError: '', explorationRecallRequested: false,
        explorationAutoCompleteRetryAt: 0,
        explorationAutoCompleteBlockedId: '',
        explorationReward: null,
        explorationLocations: null, explorationGateActive: false,
        // Presentation-only state. Closing an active exploration surface must
        // survive snapshot refreshes for that activity, while a new activity
        // or a reload may still restore the nest view once.
        dismissedExplorationId: '',
    };
    var ui = null, hoverTimer = 0, hideTimer = 0, panelLeaveTimer = 0,
        loginHintTimer = 0, authRequestGeneration = 0;
    var panelPointerActive = false, panelPointerInside = false;
    var bindingRequestGeneration = 0, actionRequestGeneration = 0, taskRequestGeneration = 0,
        oauthPollTimer = 0;
    var cooldownRefreshTimer = 0, statusRefreshTimer = 0, playActivityTimer = 0, playActivitySequence = 0;
    var explorationTimer = 0, explorationDepartureTimer = 0, explorationReturnTimer = 0,
        explorationRequestGeneration = 0;
    var explorationLetterRefreshTimer = 0, explorationLetterRefreshAttemptKey = '';
    var explorationStatusRead = null, explorationStatusReadGeneration = 0;
    // One user-visible recovery attempt may be started by reopening the
    // degraded surface or by its retry button. It is event-driven only: the
    // promise prevents duplicate reads while that attempt is still pending
    // and is discarded as soon as the read settles.
    var degradedSurfaceRecoveryPromise = null;
    // Keep only the identity of the currently published renderer gate. A
    // later authoritative snapshot may represent "at home" as exploration:
    // null, so the release event cannot recover this identity from that new
    // snapshot.
    var explorationGateIdentity = null;
    // A character/model switch can arrive after the renderer has already
    // started replacing the visible cat. Keep the old exploration identity
    // long enough to issue the same server recall used by the nest/return
    // entry; the new character must never inherit the old renderer snapshot.
    var pendingContextRecall = null;
    var oauthPopup = null, oauthCancelResolver = null, oauthAttemptGeneration = 0;
    var modelExitRecallPromise = null;
    // An account/session replacement must wait for the old pet context to
    // settle. Keep the transition serialized with model exits so a logout
    // event cannot clear the old exploration while its recall is in flight.
    var authContextExitPromise = null;
    // Account activity is independent from the visible cat form.  Keep one
    // request per authenticated account/session fence, and only replay when a
    // newer authentication generation arrived while the old request was in
    // flight.  The service hint is converted to a monotonic delay; the page
    // never treats its wall clock as settlement authority.
    var accountResumePromise = null;
    var accountResumeFence = '';
    var accountResumeTrailingFence = '';
    var accountResumeTrailingReason = '';
    var accountResumeDeferredFence = '';
    var accountResumeDeferredReason = '';
    var accountResumeThrottleFence = '';
    var accountResumeNotBeforeMonotonic = 0;
    var petRefreshFence = '';
    var authActivityGeneration = 0;
    // One renderer-local intent lane orders a verified user mutation against
    // account activity settlement.  It carries no business payload and is
    // never persisted: durable pending writes and the service remain the
    // authority for retries, idempotency and conflict resolution.
    var petMutationIntentCount = 0;
    var preauthorizedPlayActivities = Object.create(null);
    // Promise-only coordination between the user yarn observation and the
    // existing Cat Mind runner. It is deliberately not part of public state
    // or durable storage: the service lease and pending-write registry own
    // all authority and retry semantics.
    var pendingYarnAuthorizations = Object.create(null);
    var pendingWriteRegistry = null;
    var pendingExplorationNotices = Object.create(null);

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
    function getPendingWriteRegistry() {
        if (pendingWriteRegistry) return pendingWriteRegistry;
        var api = window.NekoPetCarePendingWrites;
        if (!api || typeof api.create !== 'function') return null;
        try { pendingWriteRegistry = api.create({ storage: window.localStorage }); } catch (_) {}
        return pendingWriteRegistry;
    }
    function currentPendingWrite(accountId) {
        var registry = getPendingWriteRegistry();
        var account = str(accountId == null ? state.accountId : accountId);
        if (!registry || !account) return null;
        try { return registry.current(account); } catch (error) {
            state.pendingWriteError = error && error.message || 'pet_care_pending_write_storage_corrupt';
            return { kind: 'storage-corrupt', phase: 'quarantined', account_id: account };
        }
    }
    function pendingWriteSummary() {
        var record = currentPendingWrite();
        if (!record) return null;
        return {
            kind: str(record.kind), phase: str(record.phase),
            operation_id: str(record.operation_id), character_uid: str(record.character_uid),
            quarantined: record.phase === 'quarantined',
        };
    }
    function liveAccountResumeOwns(record) {
        return !!record && record.kind === 'session-resume' &&
            !!accountResumePromise && state.accountResumePending &&
            str(record.account_id) === state.accountId;
    }
    function petMutationHardBlocked() {
        var record = currentPendingWrite();
        // A live resume owned by this renderer is a waitable barrier.  An
        // orphaned/unknown resume record is not: its exact durable key must be
        // reconciled before another business write can start.
        return !!(record && !liveAccountResumeOwns(record)) ||
            state.pendingWriteRetrying || state.petRefreshRequired;
    }
    function petMutationBlocked() {
        // Reads and background work must not race a live account settlement.
        // User writes use runPetMutationIntent() and wait instead of being
        // discarded, so their controls consult petMutationHardBlocked().
        return petMutationHardBlocked() || state.accountResumePending ||
            !!accountResumePromise;
    }
    function petMutationContext() {
        return {
            auth_fence: authFence(),
            account_id: str(state.accountId),
            current_character_uid: str(state.currentCharacterUid),
        };
    }
    function petMutationContextMatches(expected) {
        return !!expected && state.authStatus === 'logged-in' &&
            expected.auth_fence === authFence() &&
            expected.account_id === str(state.accountId) &&
            expected.current_character_uid === str(state.currentCharacterUid);
    }
    function afterCurrentEventTurn() {
        return new Promise(function (resolve) { window.setTimeout(resolve, 0); });
    }
    async function runPetMutationIntent(operation) {
        if (typeof operation !== 'function' || state.authStatus !== 'logged-in' ||
            !state.accountId) return null;
        var expected = petMutationContext();
        petMutationIntentCount += 1;
        try {
            // A changed auth fence can queue one trailing resume from the
            // current resume's finally callback.  Give that callback one event
            // turn to publish the next promise, then wait again.  Payloads,
            // cycle keys and CAS versions are deliberately built only after
            // this loop.
            while (accountResumePromise) {
                var observed = accountResumePromise;
                await observed;
                if (!petMutationContextMatches(expected)) return null;
                await afterCurrentEventTurn();
                if (accountResumePromise === observed) break;
            }
            if (!petMutationContextMatches(expected) || petMutationHardBlocked()) return null;
            return await operation(expected);
        } finally {
            petMutationIntentCount = Math.max(0, petMutationIntentCount - 1);
            if (!petMutationIntentCount) {
                flushDeferredAccountResume('pet-care-mutation-ended');
            }
        }
    }
    function rememberRoleAliasOwner(characterUid, accountId) {
        var bridge = window.NekoPetCareRoleAliasSync;
        if (!bridge || typeof bridge.rememberOwner !== 'function') {
            return Promise.reject(new Error('pet_care_role_alias_bridge_unavailable'));
        }
        try {
            return Promise.resolve(bridge.rememberOwner(characterUid, accountId)).then(function () {
                return true;
            });
        } catch (error) {
            return Promise.reject(error);
        }
    }
    function withPendingWriteAccountLock(accountId, options, callback) {
        var registry = getPendingWriteRegistry();
        var account = str(accountId);
        if (!registry || typeof registry.withAccountLock !== 'function') {
            return Promise.reject(new Error('pet_care_pending_write_lock_unavailable'));
        }
        if (!account) return Promise.reject(new Error('pet_care_account_required'));
        return registry.withAccountLock(account, options || {}, callback);
    }
    function registerPendingWrite(kind, body, target, contextOverride) {
        var registry = getPendingWriteRegistry();
        if (!registry) throw new Error('pet_care_pending_write_storage_unavailable');
        if (!state.accountId) throw new Error('pet_care_account_required');
        var contextValue = contextOverride && typeof contextOverride === 'object' ? contextOverride : {};
        var existing = currentPendingWrite();
        if (existing) {
            throw new Error(existing.phase === 'quarantined'
                ? 'pet_care_pending_write_quarantined' : 'pet_care_write_pending');
        }
        var accountOnly = kind === 'session-resume';
        var hasContext = function (key) {
            return Object.prototype.hasOwnProperty.call(contextValue, key);
        };
        return registry.begin({
            kind: kind,
            account_id: state.accountId,
            character_uid: accountOnly ? '' : str(hasContext('character_uid')
                ? contextValue.character_uid : state.currentCharacterUid),
            bound_role_id: accountOnly ? '' : str(hasContext('bound_role_id')
                ? contextValue.bound_role_id : state.bindingRoleId),
            pet_id: accountOnly ? '' : str(hasContext('pet_id')
                ? contextValue.pet_id : (state.petId || (state.snapshot && state.snapshot.pet_id))),
            target: target || {},
            body: body,
        });
    }
    function executeNewPendingWrite(kind, body, target, contextOverride) {
        var expectedAccount = state.accountId;
        var expectedGeneration = state.generation;
        return withPendingWriteAccountLock(expectedAccount, { ifAvailable: true }, async function () {
            if (state.authStatus !== 'logged-in' || state.accountId !== expectedAccount ||
                state.generation !== expectedGeneration) {
                throw new Error('pet-care-context-changed');
            }
            var record = registerPendingWrite(kind, body, target, contextOverride);
            return executePendingWrite(record);
        });
    }
    function pendingWriteContextMatches(record) {
        if (!record || str(record.account_id) !== state.accountId) return false;
        if (record.kind === 'session-resume') return true;
        if (str(record.character_uid) !== state.currentCharacterUid) return false;
        if (record.kind === 'bind-create' || record.kind === 'bind-rebind') return true;
        return str(record.bound_role_id) === state.bindingRoleId &&
            str(record.pet_id) === str(state.petId || (state.snapshot && state.snapshot.pet_id));
    }
    function pendingWriteOptions(record) {
        return {
            method: 'POST', credentials: 'same-origin', cache: 'no-store',
            headers: {
                'Content-Type': 'application/json', Accept: 'application/json',
                'Idempotency-Key': record.idempotency_key,
            },
            body: JSON.stringify(record.body),
        };
    }
    function dispatchPendingWrite(record) {
        var options = pendingWriteOptions(record);
        var uid = record.character_uid;
        var client = petCareClient(uid);
        switch (record.kind) {
        case 'session-resume':
            return client.sessionResume(options);
        case 'bind-create':
        case 'bind-rebind':
            return client.bind(options);
        case 'care-action':
            return client.careAction(uid, options);
        case 'task-claim':
            return client.claimTask(uid, record.target.task_id, options);
        case 'task-complete':
            return client.completeTask(uid, record.target.task_id, options);
        case 'status-recovery':
            return client.statusRecovery(uid, options);
        case 'play-lease-issue':
            return client.issuePlayLease(uid, options);
        case 'play-lease-complete':
            return client.completePlayLease(uid, record.target.lease_id, options);
        case 'exploration-start':
            return client.startExploration(uid, options);
        case 'exploration-complete':
            return client.completeExploration(uid, record.target.exploration_id, options);
        case 'exploration-recall':
            return client.recallExploration(uid, record.target.exploration_id, options);
        default:
            return Promise.reject(new Error('pet_care_pending_write_kind_invalid'));
        }
    }
    async function executePendingWrite(record) {
        var registry = getPendingWriteRegistry();
        if (!registry || !record) throw new Error('pet_care_pending_write_missing');
        if (record.kind === 'bind-create' || record.kind === 'bind-rebind') {
            // Every first attempt and replay must preserve the verified
            // account-to-local-alias deletion fence before the bind can reach
            // the service. The caller holds the account Web Lock and `record`
            // is already durable at this point.
            await rememberRoleAliasOwner(record.character_uid, record.account_id);
        }
        var prepared = registry.prepare(record.account_id, record.operation_id);
        var response;
        try {
            response = await dispatchPendingWrite(prepared);
        } catch (error) {
            error.petCarePendingWrite = true;
            throw error;
        }
        var body = null, parseError = null;
        try { body = await response.json(); } catch (error) { parseError = error; }
        var outcome = registry.classify(response, body, parseError, prepared.kind);
        if (outcome === 'quarantine') {
            registry.quarantine(prepared.account_id, prepared.operation_id,
                operationFailureCode(body, 'idempotency_key_reused'));
            var isolated = new Error(operationFailureCode(body, 'idempotency_key_reused'));
            isolated.petCarePendingWrite = true;
            isolated.petCarePendingWriteQuarantined = true;
            isolated.response = response;
            isolated.body = body;
            throw isolated;
        }
        if (outcome === 'pending') {
            var ambiguous = parseError || new Error(operationFailureCode(body, 'operation_in_progress'));
            ambiguous.petCarePendingWrite = true;
            ambiguous.response = response;
            ambiguous.body = body;
            throw ambiguous;
        }
        registry.complete(prepared.account_id, prepared.operation_id);
        return {
            response: response,
            body: body || {},
            record: prepared,
            confirmed: outcome === 'confirmed',
            rejected: outcome === 'rejected',
        };
    }
    function markConfirmedWriteRefreshRequired(accountId) {
        if (str(accountId) !== state.accountId) return;
        state.petRefreshRequired = true;
        state.petRefreshSource = 'confirmed-write';
        petRefreshFence = authFence();
        if (!state.pendingWriteError) state.pendingWriteError = 'pet_care_refresh_required';
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
    function authFence() {
        // This fence is persisted with an ambiguous account-resume request.
        // JSON escaping keeps arbitrary opaque session ids inside the durable
        // registry's printable-string contract without changing its identity.
        return JSON.stringify([
            str(state.accountId), str(state.sessionGeneration), authActivityGeneration,
        ]);
    }
    function monotonicNow() {
        try {
            if (window.performance && typeof window.performance.now === 'function') {
                var value = Number(window.performance.now());
                if (Number.isFinite(value)) return value;
            }
        } catch (_) {}
        return Date.now();
    }
    function resetAccountResumeContext() {
        accountResumeThrottleFence = '';
        accountResumeNotBeforeMonotonic = 0;
        accountResumeDeferredFence = '';
        accountResumeDeferredReason = '';
        state.petRefreshRequired = false;
        state.petRefreshSource = '';
        petRefreshFence = '';
    }
    function playSettlementWindowActive() {
        return !!state.playActivity || !!state.playPending ||
            Object.keys(preauthorizedPlayActivities).length > 0;
    }
    function clearPreauthorizedPlayActivities() {
        preauthorizedPlayActivities = Object.create(null);
    }
    function deferAccountResume(fence, reason) {
        accountResumeDeferredFence = str(fence);
        accountResumeDeferredReason = str(reason) || 'play-settlement-window';
    }
    function flushDeferredAccountResume(reason) {
        if (!accountResumeDeferredFence || playSettlementWindowActive() ||
            petMutationIntentCount > 0 || currentPendingWrite()) return false;
        var fence = accountResumeDeferredFence;
        var resumeReason = accountResumeDeferredReason || str(reason) || 'play-settlement-ended';
        if (state.authStatus !== 'logged-in' || fence !== authFence()) {
            accountResumeDeferredFence = '';
            accountResumeDeferredReason = '';
            return false;
        }
        accountResumeDeferredFence = '';
        accountResumeDeferredReason = '';
        window.setTimeout(function () {
            if (state.authStatus !== 'logged-in' || fence !== authFence()) return;
            requestAccountResume(resumeReason, { force: true });
        }, 0);
        return true;
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
    function showPetCareToast(message, duration, options) {
        if (!PET_CARE_SURFACE.ownsNotices) return false;
        message = str(message);
        if (!message || typeof window.showStatusToast !== 'function') return false;
        try {
            var result = window.showStatusToast(message, duration || 5000,
                options || { priority: 10 });
            if (result === false) return false;
            // The shared implementation predates an acknowledgement return
            // value. Confirm its synchronous DOM write so a missing surface or
            // a higher-priority toast cannot consume a one-time receipt.
            if (result == null) {
                var toast = document.getElementById('status-toast');
                var text = toast && typeof toast.querySelector === 'function'
                    ? toast.querySelector('#status-toast-text') : null;
                return !!(toast && text && str(text.textContent) === message &&
                    toast.style && toast.style.display !== 'none' &&
                    toast.style.visibility !== 'hidden');
            }
            return true;
        } catch (_) {
            return false;
        }
    }
    function settlementNoticeKey(accountId, petId) {
        accountId = str(accountId);
        petId = str(petId);
        if (!accountId || !petId) return '';
        return SETTLEMENT_NOTICE_STORAGE_PREFIX + encodeURIComponent(accountId) + ':' +
            encodeURIComponent(petId);
    }
    function settlementNoticeMessage(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
        var settlementId = str(value.settlement_id);
        if (!settlementId || settlementId.length > 128 ||
            !/^[A-Za-z0-9][A-Za-z0-9-]*$/.test(settlementId)) return '';
        var lines = [];
        var daily = value.daily_login;
        var dailyProgress = daily && Number(daily.progress_delta);
        if (Number.isInteger(dailyProgress) && dailyProgress > 0) {
            lines.push('每日登录：养成进度 +' + dailyProgress);
        }
        var rollback = Number(value.progress_rollback);
        if (Number.isInteger(rollback) && rollback > 0) {
            lines.push('离线期间：养成进度回退 ' + rollback);
        }
        var negativeStatuses = [];
        var appendNegative = function (candidate) {
            candidate = str(candidate).toLowerCase();
            if (['sick', 'sulk'].indexOf(candidate) < 0 ||
                negativeStatuses.indexOf(candidate) >= 0) return;
            negativeStatuses.push(candidate);
        };
        appendNegative(value.negative_status);
        if (Array.isArray(value.negative_statuses)) value.negative_statuses.forEach(appendNegative);
        if (negativeStatuses.indexOf('sick') >= 0) {
            lines.push('猫咪生病了，恢复任务已出现在任务列表');
        }
        if (negativeStatuses.indexOf('sulk') >= 0) {
            lines.push('猫咪在赌气，恢复任务已出现在任务列表');
        }
        return lines.length ? '本次回归结算：\n' + lines.join('\n') : '';
    }
    function notifySettlementOnce(snapshot) {
        if (state.authStatus !== 'logged-in' || !snapshot || typeof snapshot !== 'object') return false;
        var settlement = snapshot.settlement;
        var message = settlementNoticeMessage(settlement);
        var settlementId = str(settlement && settlement.settlement_id);
        var accountId = state.accountId;
        var snapshotAccountId = str(snapshot.account_id);
        var petId = str(snapshot.pet_id);
        if (!message || !settlementId || (snapshotAccountId && snapshotAccountId !== accountId)) return false;
        var key = settlementNoticeKey(accountId, petId);
        if (!key) return false;
        try {
            if (window.localStorage && window.localStorage.getItem(key) === settlementId) return false;
        } catch (_) {}
        if (!showPetCareNotice(message, 'info')) return false;
        try {
            if (window.localStorage) window.localStorage.setItem(key, settlementId);
        } catch (_) {}
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
        if (authReason === 'logout-exploration-pending') {
            window.setTimeout(function () {
                showPetCareNotice(
                    '已退出 N.E.K.O 账号；探索召回尚未完成，将在重新登录后继续确认。',
                    'warning'
                );
            }, 0);
            return;
        }
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
    function explorationNoticeMatches(previous, activityId, accountId, roleId, options) {
        options = options && typeof options === 'object' ? options : {};
        if (!previous || typeof previous !== 'object' || !activityId) return false;
        if (str(previous.accountId) !== accountId) return false;
        var previousRoleId = str(previous.roleId || previous.boundRoleId || previous.characterUid);
        if (previousRoleId && previousRoleId !== roleId) return false;
        if (options.requireRole === true && !previousRoleId) return false;
        if (Array.isArray(previous.activityIds)) {
            return previous.activityIds.indexOf(activityId) >= 0;
        }
        var previousActivityId = str(previous.activityId);
        if (!previousActivityId) {
            // Migrate the earlier session-only id:status format.  A terminal
            // exploration id is unique, so the old status suffix must not
            // make the same activity appear as a new notification.
            var legacyKey = str(previous.key);
            previousActivityId = legacyKey ? legacyKey.split(':')[0] : '';
        }
        return previousActivityId === activityId;
    }
    function explorationNoticeStorageKey(accountId, roleId) {
        accountId = str(accountId);
        roleId = str(roleId);
        return accountId && roleId ? EXPLORATION_NOTICE_STORAGE_PREFIX +
            encodeURIComponent(accountId) + ':' + encodeURIComponent(roleId) : '';
    }
    function explorationNoticeV2StorageKey(accountId) {
        accountId = str(accountId);
        return accountId ? EXPLORATION_NOTICE_V2_STORAGE_PREFIX + encodeURIComponent(accountId) : '';
    }
    function rememberExplorationNotice(storageKey, accountId, roleId, activityId) {
        if (!storageKey || !accountId || !roleId || !activityId) return;
        var previous = readLocalNotice(storageKey) || readSessionNotice(storageKey);
        var activityIds = previous && Array.isArray(previous.activityIds)
            ? previous.activityIds.filter(function (value) { return str(value); }) : [];
        if (activityIds.indexOf(activityId) < 0) activityIds.push(activityId);
        activityIds = activityIds.slice(-32);
        var record = {
            version: 3, accountId: accountId, roleId: roleId,
            activityIds: activityIds, updatedAt: Date.now(),
        };
        writeLocalNotice(storageKey, record);
        writeSessionNotice(storageKey, record);
    }
    function explorationRewardFromValue(value) {
        if (!value || typeof value !== 'object') return null;
        var nested = value.reward || value.rewards;
        if (nested != null) return nested;
        if (Array.isArray(value.drops) || (value.progress_delta != null &&
            Number.isFinite(Number(value.progress_delta)))) {
            return value;
        }
        return null;
    }
    function explorationRewardFromBody(body) {
        if (!body || typeof body !== 'object') return null;
        var direct = explorationRewardFromValue(body);
        if (direct) return direct;
        var result = explorationRewardFromValue(body.result);
        if (result) return result;
        var operation = explorationRewardFromValue(operationResultFromBody(body));
        if (operation) return operation;
        return explorationRewardFromValue(explorationFromBody(body));
    }
    function notifyExplorationTerminal(exploration, reward, operation) {
        if (!PET_CARE_SURFACE.ownsNotices) return;
        var activityId = explorationNoticeKey(exploration);
        if (!activityId) return;
        var accountId = str(state.accountId);
        var characterUid = str(state.currentCharacterUid);
        var boundRoleId = str(state.bindingRoleId);
        var roleId = boundRoleId || characterUid;
        var generation = state.generation;
        var storageKey = explorationNoticeStorageKey(accountId, roleId);
        if (!storageKey) return;
        var previous = readLocalNotice(storageKey) || readSessionNotice(storageKey);
        var previousV2Key = explorationNoticeV2StorageKey(accountId);
        var previousV2 = readLocalNotice(previousV2Key) || readSessionNotice(previousV2Key);
        var legacy = readLocalNotice(EXPLORATION_NOTICE_STORAGE_KEY) ||
            readSessionNotice(EXPLORATION_NOTICE_STORAGE_KEY);
        if (explorationNoticeMatches(previous, activityId, accountId, roleId, { requireRole: true }) ||
            explorationNoticeMatches(previousV2, activityId, accountId, roleId) ||
            explorationNoticeMatches(legacy, activityId, accountId, roleId)) return;
        var pendingKey = JSON.stringify([accountId, roleId, activityId, generation]);
        if (pendingExplorationNotices[pendingKey]) return;
        pendingExplorationNotices[pendingKey] = true;
        var rewardLabel = explorationRewardText(reward || explorationRewardFromValue(exploration));
        var status = str(exploration && exploration.status).toLowerCase();
        var title = operation === 'complete' || status === 'completed' ? '探索已完成' : '猫已召回';
        // The service confirmation gates this notice, but that transport fact
        // is not useful player-facing copy. Show only the exploration result.
        var message = rewardLabel ? title + '，收获：' + rewardLabel : title + '。';
        window.setTimeout(function () {
            delete pendingExplorationNotices[pendingKey];
            // A delayed result belongs only to the account/role/generation that
            // observed it. Account or role switches leave it unrecorded so the
            // original context may show it after a later authoritative read.
            if (state.generation !== generation || str(state.accountId) !== accountId ||
                str(state.currentCharacterUid) !== characterUid ||
                str(state.bindingRoleId) !== boundRoleId) return;
            // Persist only after a confirmable non-modal surface accepted the
            // notice. A page close before this callback therefore cannot consume
            // a result that the user never saw.
            if (showPetCareToast(message, 5000, { priority: 10 })) {
                rememberExplorationNotice(storageKey, accountId, roleId, activityId);
            }
        }, 0);
    }

    function str(value) { return typeof value === 'string' ? value.trim() : ''; }
    function playerFacingLabel(value, machineValues, fallback) {
        var label = str(value);
        if (!label) return fallback;
        var normalized = label.toLowerCase();
        var machineList = Array.isArray(machineValues) ? machineValues : [machineValues];
        var exposesMachineValue = machineList.some(function (candidate) {
            candidate = str(candidate);
            return candidate && candidate.toLowerCase() === normalized;
        });
        return exposesMachineValue ? fallback : label;
    }
    function publicClientProjection() {
        var projection = cloneSnapshot(state.clientProjection);
        if (!projection) return null;
        delete projection._effective_at_ms;
        delete projection._server_time_ms;
        delete projection._cat_mind_valid_until_ms;
        delete projection._status_valid_until_ms;
        delete projection._received_monotonic;
        return projection;
    }
    function snap() {
        return Object.freeze({
            authStatus: state.authStatus, accountId: state.accountId,
            user: state.user ? Object.assign({}, state.user) : null,
            sessionGeneration: state.sessionGeneration,
            accountResumePending: state.accountResumePending,
            petRefreshRequired: state.petRefreshRequired,
            currentCharacterUid: state.currentCharacterUid,
            currentCharacterName: state.currentCharacterName,
            bindingStatus: state.bindingStatus, bindingRoleId: state.bindingRoleId,
            petId: state.petId, bindingRevision: state.bindingRevision,
            petStateVersion: state.petStateVersion, canRebind: state.canRebind,
            serviceStatus: state.serviceStatus, serviceReason: state.serviceReason,
            snapshot: state.snapshot, settlement: state.settlement,
            clientProjection: publicClientProjection(),
            careActions: state.careActions, optimisticSnapshot: state.optimisticSnapshot,
            generation: state.generation,
            reason: state.reason, panelOpen: state.panelOpen,
            hoverVisible: state.hoverVisible, oauthPending: state.oauthPending,
            loginHintVisible: state.loginHintVisible,
            loginHintShownForCatEpisode: state.loginHintShownForCatEpisode,
            bindingPending: state.bindingPending,
            catLocalActive: state.catLocalActive,
            catMindCompatibilityEnabled: state.catMindCompatibilityEnabled,
            featureHintVisible: state.featureHintVisible,
            selectedView: state.selectedView,
            selectedCategory: state.selectedCategory,
            pendingAction: state.pendingAction ? Object.assign({}, state.pendingAction) : null,
            actionError: state.actionError,
            pendingWrite: pendingWriteSummary(),
            pendingWriteRetrying: state.pendingWriteRetrying,
            pendingWriteError: state.pendingWriteError,
            tasks: state.tasks, taskLoading: state.taskLoading,
            taskPendingId: state.taskPendingId,
            taskPendingOperation: state.taskPendingOperation,
            taskError: state.taskError,
            playActions: state.playActions,
            playActivity: state.playActivity ? Object.assign({}, state.playActivity) : null,
            playPending: state.playPending ? Object.assign({}, state.playPending) : null,
            playError: state.playError,
            exploration: state.snapshot && state.snapshot.exploration || null,
            explorationPending: state.explorationPending ? Object.assign({}, state.explorationPending) : null,
            explorationError: state.explorationError,
            explorationRecallRequested: state.explorationRecallRequested,
            explorationAutoCompleteRetryAt: state.explorationAutoCompleteRetryAt,
            explorationAutoCompleteBlockedId: state.explorationAutoCompleteBlockedId,
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
    function captureModeContext() {
        var api = window.nekoPetCareMode;
        if (!api || typeof api.captureContext !== 'function') return null;
        try { return api.captureContext(); } catch (_) { return null; }
    }
    function cancelPetCarePresentation(reason) {
        var presentation = window.NekoCatIdlePresentation;
        if (presentation && typeof presentation.cancelPetCare === 'function') {
            try { presentation.cancelPetCare({ reason: reason || 'pet-care-context-invalidated' }); } catch (_) {}
        }
    }
    function clearClientProjection(options) {
        options = options && typeof options === 'object' ? options : {};
        state.clientProjection = null;
        if (options.keepServerClock !== true) state.serverClockAnchor = null;
        var catMind = window.nekoCatMind;
        if (catMind && typeof catMind.clearPetCareProjection === 'function') {
            try { catMind.clearPetCareProjection(); } catch (_) {}
        }
    }
    function normalizeClientProjection(value) {
        var schemaVersion = Number(value && value.schema_version);
        if (!value || typeof value !== 'object' || [2, 3].indexOf(schemaVersion) < 0 ||
            !str(value.config_revision)) return null;
        var effectiveAt = Date.parse(str(value.effective_at));
        var serverTime = Date.parse(str(value.server_time));
        var catMindValidUntil = Date.parse(str(schemaVersion === 3
            ? value.cat_mind_valid_until : value.valid_until));
        if (!Number.isFinite(effectiveAt) || !Number.isFinite(serverTime) ||
            !Number.isFinite(catMindValidUntil) || effectiveAt > serverTime ||
            catMindValidUntil <= serverTime || (schemaVersion === 3 &&
                catMindValidUntil - serverTime > CAT_MIND_PROJECTION_MAX_WINDOW_MS)) return null;
        var rules = value.status_rules;
        var clamp = rules && rules.clamp;
        var rates = rules && rules.per_hour;
        var moodWeights = rules && rules.mood_weights;
        var statusSeed = value.status_seed;
        var minValue = clamp && clamp.min, maxValue = clamp && clamp.max;
        var fullThresholdValue = rules && rules.full_threshold;
        var min = Number(minValue), max = Number(maxValue);
        var fullThreshold = Number(fullThresholdValue);
        var moodWeightValues = ['satiety', 'energy', 'cleanliness', 'interaction'].map(function (key) {
            return moodWeights && moodWeights[key];
        });
        var catMind = value.cat_mind;
        var catMindActionIds = catMind && typeof catMind === 'object' && !Array.isArray(catMind)
            ? Object.keys(catMind) : [];
        if (!rules || typeof rules !== 'object' || !isFiniteValue(minValue) ||
            !isFiniteValue(maxValue) || min < 0 || max > 100 || min >= max ||
            !isFiniteValue(fullThresholdValue) || fullThreshold < min || fullThreshold > max ||
            rules.paused_during_exploration !== true ||
            !rates || typeof rates !== 'object' || Array.isArray(rates) ||
            !['satiety', 'energy', 'cleanliness', 'interaction'].every(function (key) {
                return isFiniteValue(rates[key]);
            }) || !moodWeights || typeof moodWeights !== 'object' || Array.isArray(moodWeights) ||
            !moodWeightValues.every(function (weight) {
                return isFiniteValue(weight) && Number(weight) >= 0 && Number(weight) <= 1;
            }) || Math.abs(moodWeightValues.reduce(function (total, weight) {
                return total + Number(weight);
            }, 0) - 1) > 1e-9 ||
            !statusSeed || typeof statusSeed !== 'object' || Array.isArray(statusSeed) ||
            !isFiniteValue(statusSeed.interaction) || Number(statusSeed.interaction) < min ||
            Number(statusSeed.interaction) > max || !catMindActionIds.length ||
            !catMindActionIds.every(function (actionId) {
                var action = catMind[actionId];
                var scoreBiasValue = action && typeof action === 'object' && !Array.isArray(action)
                    ? action.score_bias : null;
                var scoreBias = Number(scoreBiasValue);
                return !!str(actionId) && isFiniteValue(scoreBiasValue) &&
                    scoreBias >= -100 && scoreBias <= 100;
            })) return null;
        var normalized = cloneSnapshot(value);
        if (!normalized) return null;
        normalized._effective_at_ms = effectiveAt;
        normalized._server_time_ms = serverTime;
        normalized._cat_mind_valid_until_ms = catMindValidUntil;
        // Schema v2 defined one validity window for the complete projection.
        // Schema v3 scopes the short window to Cat Mind only; status rules
        // remain read-only display input while this verified snapshot lives.
        normalized._status_valid_until_ms = schemaVersion === 2 ? catMindValidUntil : null;
        normalized._received_monotonic = monotonicNow();
        return normalized;
    }
    function installClientProjection(snapshot) {
        var projection = normalizeClientProjection(snapshot && snapshot.client_projection);
        if (!projection) {
            clearClientProjection();
            return false;
        }
        state.clientProjection = projection;
        state.serverClockAnchor = {
            server_time_ms: projection._server_time_ms,
            received_monotonic: projection._received_monotonic,
        };
        var catMind = window.nekoCatMind;
        if (catMind && typeof catMind.setPetCareProjection === 'function') {
            var accepted = false;
            try { accepted = catMind.setPetCareProjection(projection) === true; } catch (_) {}
            if (!accepted && typeof catMind.clearPetCareProjection === 'function') {
                // A Cat Mind adapter failure withdraws only the short-lived
                // bias. It cannot invalidate the independently verified
                // status projection used for read-only display.
                try { catMind.clearPetCareProjection(); } catch (_) {}
            }
        }
        return true;
    }
    function updateServerClockFromEnvelope(value) {
        var serverTime = Date.parse(str(value && value.server_time));
        if (!Number.isFinite(serverTime)) return false;
        state.serverClockAnchor = {
            server_time_ms: serverTime,
            received_monotonic: monotonicNow(),
        };
        return true;
    }
    function estimatedServerTimeMs() {
        var anchor = state.serverClockAnchor;
        if (!anchor || !Number.isFinite(Number(anchor.server_time_ms)) ||
            !Number.isFinite(Number(anchor.received_monotonic))) return NaN;
        return Number(anchor.server_time_ms) +
            Math.max(0, monotonicNow() - Number(anchor.received_monotonic));
    }
    function activeStatusProjection() {
        var projection = state.clientProjection;
        if (!projection) return null;
        var estimatedServerTime = projection._server_time_ms +
            Math.max(0, monotonicNow() - projection._received_monotonic);
        if (estimatedServerTime < projection._effective_at_ms ||
            (Number.isFinite(projection._status_valid_until_ms) &&
                estimatedServerTime >= projection._status_valid_until_ms)) {
            // Legacy v2 status rules expire with their single validity
            // window. The server-time anchor remains useful for an active
            // exploration countdown and never authorises a mutation itself.
            clearClientProjection({ keepServerClock: true });
            return null;
        }
        return projection;
    }
    function invalidatePetContext(reason, options) {
        var next = options && typeof options === 'object' ? options : {};
        var keepAccount = next.clearAccount !== true;
        cancelPetCarePresentation(reason);
        state.bindingRoleId = '';
        state.petId = ''; state.bindingRevision = null; state.petStateVersion = null;
        state.canRebind = false;
        state.snapshot = null; state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
        clearClientProjection();
        state.pendingAction = null; state.actionError = '';
        state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
        state.taskError = '';
        clearPlayActivityTimer();
        clearPreauthorizedPlayActivities();
        state.playActions = null; state.playActivity = null; state.playPending = null; state.playError = '';
        clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
        state.explorationReward = null;
        state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
        state.explorationAutoCompleteBlockedId = '';
        state.explorationLocations = null; state.explorationGateActive = false;
        state.dismissedExplorationId = '';
        if (next.preserveExplorationLease !== true) clearExplorationLease();
        taskRequestGeneration += 1;
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
        flushDeferredAccountResume(reason || 'pet-care-context-invalidated');
    }
    function isPetCareCompatibilityReady() {
        return state.authStatus === 'logged-in' &&
            state.bindingStatus === 'bound' &&
            state.serviceStatus === 'ready' &&
            !!state.snapshot && state.catLocalActive;
    }
    function syncNegativePresentation(reason) {
        var api = window.nekoPetCareMode;
        if (!api || typeof api.setNegativePresentation !== 'function') return;
        var complete = state.authStatus === 'logged-in' &&
            state.bindingStatus === 'bound' &&
            state.serviceStatus === 'ready' && !!state.snapshot;
        var negative = complete && state.snapshot.negative_status &&
            typeof state.snapshot.negative_status === 'object'
            ? state.snapshot.negative_status : null;
        try {
            api.setNegativePresentation({
                sick: !!(negative && negative.sick && negative.sick.active === true),
                sulk: !!(negative && negative.sulk && negative.sulk.active === true),
                context: captureModeContext(),
                reason: reason || (complete
                    ? 'pet-care-negative-presentation-ready'
                    : 'pet-care-negative-presentation-unavailable'),
            });
        } catch (_) {}
    }
    function hasPetCarePresentationConflict() {
        // These values describe the short window in which a user-owned care
        // or play presentation owns the cat. Starting an exploration also
        // owns the still-visible cat while its authoritative POST is pending;
        // after confirmation the separate exploration-away gate takes over.
        // A preauthorized playground yarn response is already moving the
        // visible cat before its delayed terminal observation creates
        // playActivity. Reads, tasks and an otherwise idle complete snapshot
        // do not conflict with Cat Mind.
        return !!state.pendingAction || playSettlementWindowActive() ||
            !!(state.explorationPending && state.explorationPending.operation === 'start');
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
        // The locked interaction is hover-to-reveal.  The first-ready hint is
        // only an acknowledgement; it must not open the surface by itself or
        // create a second open path before the user moves over the cat.
        if (!showPetCareToast('已开启养宠功能，可将鼠标移到猫咪周围打开养宠面板。',
            6000, { priority: 10 })) return;
        state.featureHintVisible = true;
        try {
            if (window.localStorage) window.localStorage.setItem(FEATURE_HINT_STORAGE_KEY, '1');
        } catch (_) {}
    }
    function syncCatMindCompatibility(reason) {
        var api = window.nekoPetCareMode;
        var enabled = isPetCareCompatibilityReady();
        var presentationConflict = enabled && hasPetCarePresentationConflict();
        syncNegativePresentation(reason);
        if (state.authStatus !== 'logged-in' || state.bindingStatus !== 'bound' ||
            state.serviceStatus !== 'ready' || !state.snapshot) clearClientProjection();
        state.catMindCompatibilityEnabled = enabled;
        if (api && typeof api.setCatMindCompatibility === 'function') {
            try {
            api.setCatMindCompatibility({
                // A complete snapshot supplies a read-only Cat Mind projection
                // while the cat is idle. Suppress the legacy autonomous
                // requests only while a user-owned PetCare presentation or its
                // service-confirmation window owns the cat. The separate
                // exploration-away gate continues to cover the whole entity.
                suppressed: presentationConflict,
                actionIds: CAT_MIND_ACTION_IDS,
                // Entering and leaving the conflict window use the existing
                // source-filtered cleanup path, so no Cat Mind tail overlaps a
                // user presentation and no stale suppressed run survives it.
                cleanup: true,
                reason: reason || (presentationConflict
                    ? 'pet-care-presentation-active'
                    : (enabled ? 'pet-care-idle' : 'pet-care-unavailable')),
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
        state.canRebind = false;
        clearClientProjection();
        state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
        clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
        clearPreauthorizedPlayActivities();
        state.playPending = null; state.playError = '';
        clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
        state.explorationReward = null;
        state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
        state.explorationAutoCompleteBlockedId = '';
        state.explorationLocations = null; state.explorationGateActive = false;
        state.pendingAction = null; state.actionError = '';
        state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
        state.taskError = '';
        taskRequestGeneration += 1;
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
        // A model transition must not publish a recall intent that cannot yet
        // enter the shared mutation lane.  An in-flight exploration completion
        // is the one exception: recall is deliberately queued behind that
        // service decision and is settled only if the activity stays active.
        var queueBehindComplete = state.explorationPending &&
            state.explorationPending.operation === 'complete';
        if (!queueBehindComplete && petMutationHardBlocked()) return Promise.resolve(false);
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
        var queueBehindComplete = state.explorationPending &&
            state.explorationPending.operation === 'complete';
        if (!queueBehindComplete && petMutationHardBlocked()) return false;
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
        var persistedAccountId = str(persistedPresence && persistedPresence.accountId);
        var observedPreviousAuth = previousAuth === 'logged-in' || persistedPresence
            ? 'logged-in' : previousAuth;
        var logged = !!(next && next.logged_in && str(next.local_user_id));
        var auth = logged ? 'logged-in' : 'logged-out';
        var account = logged ? str(next.local_user_id) : '';
        var previousAccountId = str(state.accountId) || persistedAccountId;
        var persistedExplorationLease = readExplorationLease(previousAccountId);
        var accountReplaced = !!(logged && (
            (previousAccountId && previousAccountId !== account) ||
            (persistedExplorationLease && persistedExplorationLease.accountId !== account)
        ));
        // The local OAuth adapter exposes an opaque login ``session_id``;
        // remote adapters may expose a non-authorizing ``session_generation``.
        // Both are only a context fence for the UI.  Token refresh must keep
        // the same value, while a new credential publication changes it.
        var session = next && next.session_generation != null
            ? String(next.session_generation).trim()
            : (next && next.session_id != null ? String(next.session_id).trim() : '');
        var changed = auth !== state.authStatus || account !== state.accountId ||
            session !== state.sessionGeneration;
        if (changed) {
            authActivityGeneration += 1;
            resetAccountResumeContext();
        }
        // Once the account authority has confirmed logout, the old credential
        // can no longer authorise an exploration recall. Preserve only the
        // opaque activity lease for a future login, then apply the ordinary-cat
        // privacy boundary without waiting on a doomed business write.
        if ((!logged || accountReplaced) && explorationActive(currentExploration())) {
            writeExplorationLease(currentExploration());
            persistedExplorationLease = readExplorationLease(previousAccountId);
        }
        if (accountReplaced) {
            // The account authority has already replaced A with B, so every new
            // gateway request is authorized as B.  Keep A's presentation lease
            // only in A's account-scoped slot and remove the legacy unscoped
            // copy before B becomes visible.  A later login as A may reconcile
            // it; B can neither render it nor attempt to settle it.
            clearLegacyExplorationLease(previousAccountId);
        }
        // A same-account session replacement may settle its activity before
        // changing context. A different account is already authoritative at
        // the gateway, so it must never try to settle the old account's pet.
        // A confirmed logout is likewise applied immediately; explicit user
        // logout recalls before it asks the account service to end the session.
        var recalled = !changed || !logged || accountReplaced || await ensureExplorationEndedBeforeAuthChange(
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
            showPetCareNotice('账号切换前的探索结算尚未完成，探索状态已保留，请重试。', 'warning');
            return snap();
        }
        if (changed) {
            cancelPetCarePresentation(reason || 'auth-status');
            clearPreauthorizedPlayActivities();
        }
        // A same-account session replacement has already settled its old
        // activity. Logout and cross-account replacement invalidate the old
        // visible context immediately and leave any retained lease account-scoped.
        if (!logged || (changed && (account !== state.accountId ||
            session !== state.sessionGeneration))) pendingContextRecall = null;
        state.authStatus = auth; state.accountId = account;
        if (changed) {
            state.pendingWriteRetrying = false;
            state.pendingWriteError = '';
        }
        state.user = logged && next.user && typeof next.user === 'object'
            ? Object.assign({}, next.user) : null;
        state.sessionGeneration = session; state.reason = reason || 'auth-status';
        if (logged) {
            if (loginHintTimer) { window.clearTimeout(loginHintTimer); loginHintTimer = 0; }
            state.loginHintVisible = false;
        }
        if (!logged) {
            if (state.oauthPending) cancelOAuthLogin();
            state.bindingStatus = 'unknown'; state.bindingRoleId = ''; state.snapshot = null;
            state.petId = ''; state.bindingRevision = null; state.petStateVersion = null; state.canRebind = false;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playError = '';
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
                clearExplorationLease(previousAccountId);
            }
            state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
            state.explorationAutoCompleteBlockedId = '';
            state.explorationLocations = null; state.explorationGateActive = false;
            state.pendingAction = null; state.actionError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
            state.taskError = '';
            taskRequestGeneration += 1;
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
            state.petId = ''; state.bindingRevision = null; state.petStateVersion = null; state.canRebind = false;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playError = '';
            clearExplorationTimer(); state.explorationPending = null; state.explorationError = '';
            state.explorationReward = null;
            state.explorationRecallRequested = false; state.explorationAutoCompleteRetryAt = 0;
            state.explorationAutoCompleteBlockedId = '';
            state.explorationLocations = null; state.explorationGateActive = false;
            state.pendingAction = null; state.actionError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
            state.taskError = '';
            taskRequestGeneration += 1;
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
        if (!logged) {
            syncAnchor();
            return Promise.resolve(snap());
        }
        // Lifecycle activity is account-scoped and remains valid in ordinary
        // model or non-bound-role contexts. It deliberately precedes the
        // optional role snapshot read and never sends the role alias itself.
        return requestAccountResume(reason || 'auth-refresh', { force: changed }).then(function () {
            // Re-reading OAuth status is cheap and event-driven. Do not
            // re-fetch the long-lived pet snapshot when the account/session
            // did not change unless resume requested a recovery read.
            return !state.bindingPending && (changed || state.petRefreshRequired ||
                state.bindingStatus === 'unknown' || state.serviceStatus === 'unavailable')
                ? refreshBinding(reason || 'auth-refresh') : snap();
        });
    }
    function isFiniteValue(value) {
        if (typeof value === 'number') return Number.isFinite(value);
        if (typeof value === 'string' && value.trim() !== '') return Number.isFinite(Number(value));
        return false;
    }
    function isPlainRecord(value) {
        return !!value && typeof value === 'object' && !Array.isArray(value);
    }
    function registeredActionAdapter(feedbackAction, kind, category) {
        var adapter = PET_CARE_ACTION_ADAPTERS[str(feedbackAction)];
        if (!adapter || adapter.kind !== kind) return null;
        if (kind === 'care' && adapter.categories.indexOf(category) < 0) return null;
        return adapter;
    }
    function careActionAdapter(category, action) {
        return registeredActionAdapter(action && action.feedback_action, 'care', category);
    }
    function playActionAdapter(contentId, action) {
        var feedbackAction = str(action && action.feedback_action);
        // Immutable snapshots from before feedback_action was projected can
        // still complete the existing yarn gesture. No consumable action gets
        // such an implicit adapter.
        if (!feedbackAction && str(contentId) === 'yarn') feedbackAction = 'cat1_play_yarn';
        return registeredActionAdapter(feedbackAction, 'play');
    }
    function actionInventoryCategoryIsValid(category, action, itemDirectory, directoryRequired) {
        var itemId = str(action && action.inventory_item);
        if (!itemId) return true;
        if (!directoryRequired) return true;
        var entry = itemDirectory && itemDirectory[itemId];
        if (!isPlainRecord(entry)) return false;
        // Legacy item catalogs intentionally project only item_id. A revised
        // structured row, when present, must agree with the action category.
        var itemCategory = str(entry.category);
        if (!itemCategory) return true;
        var expected = category === 'feeding' ? 'food' :
            category === 'cleaning' ? 'cleaning_supply' :
                category === 'play' ? 'toy' : '';
        return !!expected && itemCategory === expected;
    }
    function actionConsumeQuantity(action) {
        if (!isPlainRecord(action)) return NaN;
        var inventoryItem = str(action.inventory_item);
        var hasQuantity = Object.prototype.hasOwnProperty.call(action, 'consume_quantity');
        if (!inventoryItem) {
            return !hasQuantity || action.consume_quantity == null ? 0 : NaN;
        }
        var quantity = Number(action.consume_quantity);
        return Number.isInteger(quantity) && quantity >= 1 ? quantity : NaN;
    }
    function actionCatalogConsumptionIsValid(careActions, playActions, itemDirectory, directoryRequired) {
        if (!isPlainRecord(careActions) || !isPlainRecord(playActions)) return false;
        var careValid = Object.keys(careActions).every(function (category) {
            var entries = careActions[category];
            return isPlainRecord(entries) && Object.keys(entries).every(function (contentId) {
                var action = entries[contentId];
                var feedbackAction = str(action && action.feedback_action);
                return isPlainRecord(action) &&
                    Number.isFinite(actionConsumeQuantity(action)) &&
                    (!feedbackAction || !!careActionAdapter(category, action)) &&
                    (!directoryRequired || !!feedbackAction) &&
                    actionInventoryCategoryIsValid(
                        category, action, itemDirectory, directoryRequired);
            });
        });
        if (!careValid) return false;
        return Object.keys(playActions).every(function (contentId) {
            var action = playActions[contentId];
            if (!isPlainRecord(action) || !Number.isFinite(actionConsumeQuantity(action))) return false;
            var feedbackAction = str(action.feedback_action);
            if ((feedbackAction && !playActionAdapter(contentId, action)) ||
                (directoryRequired && !feedbackAction)) return false;
            if (!actionInventoryCategoryIsValid(
                'play', action, itemDirectory, directoryRequired)) return false;
            var inventoryItem = str(action.inventory_item);
            var inventoryQuantity = action.inventory_quantity;
            if (inventoryItem) {
                return Number.isInteger(Number(inventoryQuantity)) && Number(inventoryQuantity) >= 0;
            }
            return inventoryQuantity == null;
        });
    }
    function isCanonicalItemDirectory(value) {
        if (!isPlainRecord(value)) return false;
        var itemIds = Object.keys(value);
        if (itemIds.length > 2000) return false;
        var seen = Object.create(null);
        return itemIds.every(function (itemId) {
            if (!isTaskText(itemId, 128) || /[\/\\\u0000\n\r]/.test(itemId) || seen[itemId]) return false;
            seen[itemId] = true;
            var entry = value[itemId];
            if (!isPlainRecord(entry)) return false;
            var projectedId = str(entry.item_id || entry.id);
            if (projectedId && projectedId !== itemId) return false;
            var hasMetadata = ['display_name', 'category', 'description', 'asset_url',
                'acquisition_sources'].some(function (field) {
                return Object.prototype.hasOwnProperty.call(entry, field);
            });
            // Old immutable revisions only had item ids. Their projection is
            // intentionally sparse. A structured revision is an all-or-none
            // public directory row so UI labels, resources and acquisition
            // explanations cannot silently come from different sources.
            if (hasMetadata && (
                !Object.prototype.hasOwnProperty.call(entry, 'display_name') ||
                !Object.prototype.hasOwnProperty.call(entry, 'category') ||
                !Object.prototype.hasOwnProperty.call(entry, 'asset_url') ||
                !Object.prototype.hasOwnProperty.call(entry, 'acquisition_sources') ||
                !isTaskText(entry.display_name, 200) ||
                playerFacingLabel(entry.display_name, [itemId, projectedId], '') === '' ||
                !isTaskText(entry.category, 64) ||
                ['food', 'cleaning_supply', 'toy', 'recovery', 'collectible']
                    .indexOf(entry.category) < 0 ||
                /[\/\\\u0000\n\r]/.test(entry.category))) return false;
            if (Object.prototype.hasOwnProperty.call(entry, 'description') &&
                !isTaskText(entry.description, 2000)) return false;
            if (Object.prototype.hasOwnProperty.call(entry, 'asset_url') &&
                (typeof entry.asset_url !== 'string' || !entry.asset_url.length ||
                    entry.asset_url.length > 2048 || entry.asset_url !== entry.asset_url.trim())) return false;
            if (Object.prototype.hasOwnProperty.call(entry, 'acquisition_sources')) {
                if (!Array.isArray(entry.acquisition_sources) || !entry.acquisition_sources.length ||
                    entry.acquisition_sources.length > 64) return false;
                var sources = Object.create(null);
                if (!entry.acquisition_sources.every(function (source) {
                    if (!isTaskText(source, 160) ||
                        !/^(?:initial|task:[A-Za-z0-9_-]{1,128}|exploration:[A-Za-z0-9_-]{1,128}|unlock:[A-Za-z0-9_-]{1,128}|recovery:(?:sick|sulk))$/.test(source) ||
                        sources[source]) return false;
                    sources[source] = true;
                    return true;
                })) return false;
            }
            return true;
        });
    }
    function hasOnlyKeys(value, allowed) {
        return isPlainRecord(value) && Object.keys(value).every(function (key) {
            return allowed.indexOf(key) >= 0;
        });
    }
    function isTaskText(value, maximumLength) {
        return typeof value === 'string' && value.length > 0 &&
            value.length <= maximumLength && value === value.trim();
    }
    function isCanonicalTaskItem(item) {
        var allowedKeys = [
            'task_id', 'cycle_key', 'title', 'description', 'status', 'reward', 'claimable',
            'completion_action', 'progress_source', 'reset_policy', 'recovery_status',
            'recovery_item_id', 'progress', 'target',
        ];
        if (!hasOnlyKeys(item, allowedKeys) ||
            !isTaskText(item.task_id, 128) || !isTaskText(item.cycle_key, 128) ||
            !isTaskText(item.title, 200) ||
            (Object.prototype.hasOwnProperty.call(item, 'description') &&
                !isTaskText(item.description, 2000)) ||
            ['active', 'completed', 'claimed'].indexOf(item.status) < 0 ||
            typeof item.claimable !== 'boolean' || !isPlainRecord(item.reward)) return false;

        var hasCompletion = Object.prototype.hasOwnProperty.call(item, 'completion_action');
        var hasProgressSource = Object.prototype.hasOwnProperty.call(item, 'progress_source');
        if (hasCompletion === hasProgressSource) return false;
        if (hasCompletion && (!hasOnlyKeys(item.completion_action, ['id', 'label']) ||
            !isTaskText(item.completion_action.id, 128) ||
            !isTaskText(item.completion_action.label, 200))) return false;
        if (hasProgressSource && (!hasOnlyKeys(item.progress_source, ['category', 'content_id']) ||
            !isTaskText(item.progress_source.category, 128) ||
            !isTaskText(item.progress_source.content_id, 128))) return false;

        var hasProgress = Object.prototype.hasOwnProperty.call(item, 'progress');
        var hasTarget = Object.prototype.hasOwnProperty.call(item, 'target');
        if (hasProgressSource) {
            if (!hasProgress || !hasTarget || !Number.isInteger(item.progress) || item.progress < 0 ||
                !Number.isInteger(item.target) || item.target < 1 || item.progress > item.target ||
                (item.status === 'active' && item.progress >= item.target) ||
                (item.status !== 'active' && item.progress < item.target)) return false;
        } else if (hasProgress || hasTarget) return false;

        var rewardKeys = Object.keys(item.reward);
        if (item.claimable) {
            if (rewardKeys.length !== 2 || rewardKeys.indexOf('item_id') < 0 ||
                rewardKeys.indexOf('quantity') < 0 || !isTaskText(item.reward.item_id, 128) ||
                !Number.isInteger(item.reward.quantity) || item.reward.quantity < 1) return false;
        } else if (rewardKeys.length || item.status === 'claimed') return false;

        var hasRecoveryStatus = Object.prototype.hasOwnProperty.call(item, 'recovery_status');
        var hasRecoveryItem = Object.prototype.hasOwnProperty.call(item, 'recovery_item_id');
        var hasResetPolicy = Object.prototype.hasOwnProperty.call(item, 'reset_policy');
        if (hasRecoveryStatus !== hasRecoveryItem) return false;
        if (hasRecoveryStatus) {
            if (['sick', 'sulk'].indexOf(item.recovery_status) < 0 ||
                !isTaskText(item.recovery_item_id, 128) || !item.claimable ||
                !hasCompletion || hasResetPolicy) return false;
        } else if (!hasResetPolicy || ['daily', 'persistent'].indexOf(item.reset_policy) < 0) return false;
        return true;
    }
    function isCanonicalTaskPayload(value) {
        if (!hasOnlyKeys(value, ['items']) || !Array.isArray(value.items)) return false;
        var visibleIds = Object.create(null);
        return value.items.every(function (item) {
            if (!isCanonicalTaskItem(item) || visibleIds[item.task_id]) return false;
            visibleIds[item.task_id] = true;
            return true;
        });
    }
    function isCanonicalExplorationTimestamp(value) {
        if (typeof value !== 'string' || value !== value.trim() || value.length > 64) return false;
        var match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(?:Z|[+-](\d{2}):(\d{2}))$/.exec(value);
        if (!match) return false;
        var year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
        var hour = Number(match[4]), minute = Number(match[5]), second = Number(match[6]);
        var offsetHour = match[7] == null ? 0 : Number(match[7]);
        var offsetMinute = match[8] == null ? 0 : Number(match[8]);
        var leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
        var daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
        return month >= 1 && month <= 12 && day >= 1 && day <= daysInMonth[month - 1] &&
            hour <= 23 && minute <= 59 && second <= 59 &&
            offsetHour <= 23 && offsetMinute <= 59 && Number.isFinite(Date.parse(value));
    }
    function isCanonicalExplorationLetter(value) {
        if (!hasOnlyKeys(value, [
            'id', 'text', 'image_url', 'requires_unlock_id', 'available_ratio',
        ]) || !isTaskText(value.id, 128) || !isTaskText(value.text, 4000) ||
            !Object.prototype.hasOwnProperty.call(value, 'available_ratio') ||
            typeof value.available_ratio !== 'number' ||
            !Number.isFinite(value.available_ratio)) return false;
        var availableRatio = value.available_ratio;
        if (availableRatio <= 0 || availableRatio > 1) return false;
        if (Object.prototype.hasOwnProperty.call(value, 'image_url') &&
            (typeof value.image_url !== 'string' || !value.image_url.length ||
                value.image_url.length > 2048)) return false;
        if (Object.prototype.hasOwnProperty.call(value, 'requires_unlock_id') &&
            !isTaskText(value.requires_unlock_id, 128)) return false;
        return true;
    }
    function isCanonicalExplorationLetters(value) {
        if (!Array.isArray(value)) return false;
        var ids = Object.create(null);
        var previousRatio = 0;
        return value.every(function (letter) {
            if (!isCanonicalExplorationLetter(letter) || ids[letter.id] ||
                Number(letter.available_ratio) <= previousRatio) return false;
            ids[letter.id] = true;
            previousRatio = Number(letter.available_ratio);
            return true;
        });
    }
    function isCanonicalExplorationDrops(value) {
        if (!Array.isArray(value)) return false;
        var ids = Object.create(null);
        return value.every(function (drop) {
            if (!hasOnlyKeys(drop, ['item_id', 'quantity']) ||
                !isTaskText(drop.item_id, 128) || ids[drop.item_id] ||
                !Number.isInteger(drop.quantity) || drop.quantity < 1) return false;
            ids[drop.item_id] = true;
            return true;
        });
    }
    function isCanonicalExplorationLocation(value, locationId) {
        if (!hasOnlyKeys(value, ['id', 'name', 'description', 'asset_url', 'note']) ||
            !isTaskText(value.id, 128) || value.id !== locationId ||
            !isTaskText(value.name, 200)) return false;
        if (Object.prototype.hasOwnProperty.call(value, 'description') &&
            !isTaskText(value.description, 2000)) return false;
        if (Object.prototype.hasOwnProperty.call(value, 'asset_url') &&
            (typeof value.asset_url !== 'string' || !value.asset_url.length ||
                value.asset_url.length > 2048)) return false;
        if (Object.prototype.hasOwnProperty.call(value, 'note') &&
            !isTaskText(value.note, 2000)) return false;
        return true;
    }
    function isCanonicalExploration(value) {
        if (value === null) return true;
        var requiredKeys = [
            'exploration_id', 'location_id', 'status', 'active',
            'started_at', 'departed_at', 'ends_at', 'due_at', 'duration_seconds',
            'settled_at', 'completed_at', 'letters_revealed', 'letters_available',
            'letters', 'drops', 'progress_delta', 'completion_ratio', 'location',
        ];
        var allowedKeys = requiredKeys.concat(['next_letter_at']);
        if (!hasOnlyKeys(value, allowedKeys) || requiredKeys.some(function (key) {
            return !Object.prototype.hasOwnProperty.call(value, key);
        }) || typeof value.active !== 'boolean' || !isTaskText(value.exploration_id, 128) ||
            !isTaskText(value.location_id, 128) || !isTaskText(value.status, 32) ||
            !isCanonicalExplorationTimestamp(value.started_at) ||
            !isCanonicalExplorationTimestamp(value.departed_at) ||
            !isCanonicalExplorationTimestamp(value.ends_at) ||
            !isCanonicalExplorationTimestamp(value.due_at) ||
            !Number.isInteger(value.duration_seconds) || value.duration_seconds < 1 ||
            !Number.isSafeInteger(value.duration_seconds) ||
            !isCanonicalExplorationLetters(value.letters_revealed) ||
            !Number.isInteger(value.letters_available) || value.letters_available < 0 ||
            value.letters_available !== value.letters_revealed.length ||
            !isCanonicalExplorationLetters(value.letters) ||
            !isCanonicalExplorationDrops(value.drops) ||
            !isCanonicalExplorationLocation(value.location, value.location_id)) return false;
        var startedAt = Date.parse(value.started_at);
        var departedAt = Date.parse(value.departed_at);
        var endsAt = Date.parse(value.ends_at);
        var dueAt = Date.parse(value.due_at);
        if (startedAt !== departedAt || endsAt !== dueAt || endsAt <= startedAt ||
            Math.floor((endsAt - startedAt) / 1000) !== value.duration_seconds) return false;
        var status = value.status;
        var activeStatus = status === 'active';
        var terminalStatus = ['completed', 'recalled'].indexOf(status) >= 0;
        if (value.active) {
            if (!activeStatus || value.settled_at !== null || value.completed_at !== null ||
                value.letters.length || value.drops.length || value.progress_delta !== null ||
                value.completion_ratio !== null) return false;
            if (Object.prototype.hasOwnProperty.call(value, 'next_letter_at')) {
                if (!isCanonicalExplorationTimestamp(value.next_letter_at)) return false;
                var nextLetterAt = Date.parse(value.next_letter_at);
                if (nextLetterAt <= startedAt || nextLetterAt > endsAt) return false;
            }
            return true;
        }
        if (!terminalStatus || Object.prototype.hasOwnProperty.call(value, 'next_letter_at') ||
            !isCanonicalExplorationTimestamp(value.settled_at) ||
            !isCanonicalExplorationTimestamp(value.completed_at) ||
            Date.parse(value.settled_at) !== Date.parse(value.completed_at) ||
            Date.parse(value.settled_at) < startedAt ||
            value.letters.length < 1 || value.letters_available !== value.letters.length ||
            JSON.stringify(value.letters_revealed) !== JSON.stringify(value.letters) ||
            !Number.isInteger(value.progress_delta) || value.progress_delta < 0 ||
            typeof value.completion_ratio !== 'number' ||
            !Number.isFinite(value.completion_ratio)) return false;
        var completionRatio = value.completion_ratio;
        return completionRatio >= 0 && completionRatio <= 1;
    }
    function isCompleteSnapshot(snapshot, accountId, boundRoleId) {
        if (!snapshot || typeof snapshot !== 'object') return false;
        var schemaVersion = Number(snapshot.schema_version);
        if (!Number.isInteger(schemaVersion) || schemaVersion < 1) return false;
        var snapshotAccountId = str(snapshot.account_id);
        if ((snapshotAccountId && snapshotAccountId !== accountId) ||
            (state.petId && str(snapshot.pet_id) !== state.petId) ||
            str(snapshot.bound_role_id) !== boundRoleId ||
            !str(snapshot.pet_id) || !str(snapshot.last_settled_at)) return false;
        var bindingRevision = Number(snapshot.binding_revision);
        var stateVersion = Number(snapshot.state_version);
        if (!Number.isInteger(bindingRevision) || bindingRevision < 1 ||
            !Number.isInteger(stateVersion) || stateVersion < 1) return false;
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
        var serviceCapabilities = snapshot.service_capabilities;
        var itemDirectoryRequired = isPlainRecord(serviceCapabilities) &&
            Number(serviceCapabilities.item_directory) === 1;
        if (!snapshot.inventory || typeof snapshot.inventory !== 'object' || Array.isArray(snapshot.inventory) ||
            !isCanonicalTaskPayload(snapshot.tasks) ||
            !Array.isArray(snapshot.unlocks) ||
            !Object.prototype.hasOwnProperty.call(snapshot, 'exploration') ||
            !isCanonicalExploration(snapshot.exploration) ||
            !snapshot.cleaning_cooldowns || typeof snapshot.cleaning_cooldowns !== 'object' ||
            Array.isArray(snapshot.cleaning_cooldowns) ||
            !snapshot.activity_counters || typeof snapshot.activity_counters !== 'object' ||
            Array.isArray(snapshot.activity_counters) ||
            !isFiniteValue(snapshot.unlock_checkpoint) || Number(snapshot.unlock_checkpoint) < 0 ||
            !Object.prototype.hasOwnProperty.call(snapshot, 'settlement') ||
            !(snapshot.settlement == null || typeof snapshot.settlement === 'object') ||
            !normalizeClientProjection(snapshot.client_projection) ||
            !snapshot.content_directory || typeof snapshot.content_directory !== 'object' ||
            Array.isArray(snapshot.content_directory) ||
            !snapshot.care_actions || typeof snapshot.care_actions !== 'object' ||
            Array.isArray(snapshot.care_actions) ||
            !snapshot.play_actions || typeof snapshot.play_actions !== 'object' ||
            Array.isArray(snapshot.play_actions) ||
            !actionCatalogConsumptionIsValid(
                snapshot.care_actions, snapshot.play_actions,
                snapshot.item_directory, itemDirectoryRequired) ||
            !Array.isArray(snapshot.exploration_locations) ||
            !isPlainRecord(serviceCapabilities) ||
            (itemDirectoryRequired && !isCanonicalItemDirectory(snapshot.item_directory))) return false;
        return true;
    }
    function installServiceSnapshot(snapshot, options) {
        options = options && typeof options === 'object' ? options : {};
        // A service endpoint can return a newer authoritative snapshot than
        // binding/task reads that are still in flight. Fence those older
        // reads before installing this envelope so they cannot roll back a
        // confirmed care/task/play/exploration result when they arrive later.
        if (state.bindingPending) {
            bindingRequestGeneration += 1;
            state.bindingPending = false;
        }
        if (state.taskLoading && options.keepCurrentTaskRead !== true) {
            taskRequestGeneration += 1;
            state.taskLoading = false;
        }
        state.snapshot = snapshot;
        state.petId = str(snapshot.pet_id);
        state.bindingRevision = Number(snapshot.binding_revision);
        state.petStateVersion = Number(snapshot.state_version);
        state.settlement = snapshot.settlement && typeof snapshot.settlement === 'object'
            ? snapshot.settlement : null;
        state.careActions = snapshot.care_actions;
        state.playActions = snapshot.play_actions;
        state.explorationLocations = snapshot.exploration_locations;
        // Tasks are part of the same versioned snapshot as inventory and
        // status. Keeping a second, older task payload after installing a new
        // snapshot can expose yesterday's claim button or hide today's yarn
        // progress when the follow-up task read fails.
        applyTaskPayload(snapshot);
        installClientProjection(snapshot);
        notifySettlementOnce(snapshot);
        syncNegativePresentation(options.reason || 'pet-care-snapshot-installed');
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
        state.canRebind = status === 'unbound' && (next.can_rebind === true || next.canRebind === true);
        state.serviceReason = str(next.reason || next.detail) || 'binding-confirmed';
        var candidateSnapshot = next.snapshot && typeof next.snapshot === 'object' ? next.snapshot : null;
        var candidatePetId = str(next.petId || next.pet_id || (candidateSnapshot && candidateSnapshot.pet_id));
        var candidateBindingRevision = Number(next.bindingRevision != null
            ? next.bindingRevision : next.binding_revision);
        var candidateStateVersion = Number(next.stateVersion != null
            ? next.stateVersion : (next.state_version != null ? next.state_version :
                candidateSnapshot && candidateSnapshot.state_version));
        state.petId = status === 'needs-binding' ? '' : candidatePetId;
        state.bindingRevision = status !== 'needs-binding' && Number.isInteger(candidateBindingRevision) &&
            candidateBindingRevision >= 1 ? candidateBindingRevision : null;
        state.petStateVersion = status !== 'needs-binding' && Number.isInteger(candidateStateVersion) &&
            candidateStateVersion >= 1 ? candidateStateVersion : null;
        var complete = status === 'bound' && isCompleteSnapshot(candidateSnapshot, account, state.bindingRoleId);
        state.snapshot = complete ? candidateSnapshot : null;
        if (state.taskLoading) {
            taskRequestGeneration += 1;
            state.taskLoading = false;
        }
        state.taskPendingId = '';
        state.taskPendingOperation = '';
        state.recoveryPendingStatus = '';
        state.taskError = '';
        applyTaskPayload(complete ? candidateSnapshot : null);
        state.settlement = complete && candidateSnapshot.settlement &&
            typeof candidateSnapshot.settlement === 'object' ? candidateSnapshot.settlement : null;
        // Action catalogs are part of the authoritative snapshot. Reading a
        // second copy from the response envelope would let the visible
        // controls drift away from the version fenced by state_version.
        state.careActions = complete ? candidateSnapshot.care_actions : null;
        state.playActions = complete ? candidateSnapshot.play_actions : null;
        state.explorationLocations = complete ? candidateSnapshot.exploration_locations : null;
        var boundExploration = candidateSnapshot && candidateSnapshot.exploration;
        state.explorationReward = boundExploration && !explorationActive(boundExploration)
            ? explorationRewardFromValue(boundExploration) : null;
        var preservePendingPlayLease = petMutationIntentCount > 0 && !state.playActivity &&
            state.playPending && state.playPending.phase === 'lease';
        state.playActivity = null;
        if (!preservePendingPlayLease) {
            state.playPending = null;
            state.playError = '';
        }
        state.optimisticSnapshot = null;
        if (complete) installClientProjection(candidateSnapshot);
        else clearClientProjection();
        if (!complete) { state.selectedCategory = ''; state.progressUnlockOpen = false; }
        state.serviceStatus = status === 'unavailable' ? 'unavailable' :
            status === 'bound' && !complete ? 'unavailable' : 'ready';
        if (status === 'bound' && candidateSnapshot && !complete) state.serviceReason = 'snapshot-incomplete';
        state.reason = state.serviceReason;
        if (status === 'bound') {
            // This device-local alias ledger only remembers which verified
            // account has successfully used the local character UID. It is
            // deletion/retry evidence; the service remains the sole binding
            // authority and rechecks the live account on every retirement.
            rememberRoleAliasOwner(state.currentCharacterUid, state.accountId).catch(function (error) {
                if (error && error.message === 'pet_care_role_alias_bridge_unavailable') return;
                try { console.warn('[PetCare] 角色归属记录待稍后重试:', error && error.message); } catch (_) {}
            });
        }
        context({ accountId: state.accountId, currentCharacterUid: state.currentCharacterUid,
            boundRoleId: state.bindingRoleId, reason: state.reason });
        // Context must be updated before the server-confirmed activity gate is
        // rebuilt. Otherwise updateContext() sees a role change and releases
        // the gate that reconcileExplorationFromSnapshot() just installed.
        if (complete) reconcileExplorationFromSnapshot(candidateSnapshot, 'binding-snapshot');
        if (complete) notifySettlementOnce(candidateSnapshot);
        var resumeContextRefreshed = state.petRefreshSource === 'resume' &&
            (status === 'needs-binding' || (status === 'unbound' && candidatePetId &&
                Number.isInteger(candidateBindingRevision) && candidateBindingRevision >= 1 &&
                Number.isInteger(candidateStateVersion) && candidateStateVersion >= 1));
        if ((complete || resumeContextRefreshed) && state.petRefreshRequired &&
            petRefreshFence === authFence()) {
            state.petRefreshRequired = false;
            state.petRefreshSource = '';
            petRefreshFence = '';
        }
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
        // The binding snapshot already carries the canonical task collection.
        // Do not immediately issue a second task snapshot read here: opening
        // the surface owns that one event-driven refresh.
        maybeReplayPendingWriteAfterContext();
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
        if (panelLeaveTimer) { window.clearTimeout(panelLeaveTimer); panelLeaveTimer = 0; }
        if (cooldownRefreshTimer) { window.clearTimeout(cooldownRefreshTimer); cooldownRefreshTimer = 0; }
        if (statusRefreshTimer) { window.clearTimeout(statusRefreshTimer); statusRefreshTimer = 0; }
        if (loginHintTimer) { window.clearTimeout(loginHintTimer); loginHintTimer = 0; }
        panelPointerActive = false;
        panelPointerInside = false;
    }
    function dismissLoginHint() {
        if (loginHintTimer) { window.clearTimeout(loginHintTimer); loginHintTimer = 0; }
        state.loginHintVisible = false;
        if (ui) ui.root.dataset.loginHint = 'false';
        render();
    }
    function loginHintEntryAvailable() {
        if (!state.catLocalActive || state.authStatus !== 'logged-out' ||
            state.loginHintShownForCatEpisode || state.oauthPending) return false;
        var current = anchorState();
        return !!(current && current.visible && current.rect && !current.busy);
    }
    function maybeShowLoginHint() {
        if (!loginHintEntryAvailable()) return false;
        state.loginHintShownForCatEpisode = true;
        state.loginHintVisible = true;
        if (ui) ui.root.dataset.loginHint = 'true';
        if (loginHintTimer) window.clearTimeout(loginHintTimer);
        loginHintTimer = window.setTimeout(function () {
            loginHintTimer = 0;
            state.loginHintVisible = false;
            render();
        }, LOGIN_HINT_AUTO_HIDE_MS);
        if (loginHintTimer && typeof loginHintTimer.unref === 'function') loginHintTimer.unref();
        render();
        return true;
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
    function cancelPanelAutoClose() {
        if (!panelLeaveTimer) return;
        window.clearTimeout(panelLeaveTimer);
        panelLeaveTimer = 0;
    }
    function schedulePanelAutoClose() {
        if (panelLeaveTimer || panelPointerActive || panelPointerInside ||
            !state.panelOpen || state.oauthPending) return;
        panelLeaveTimer = window.setTimeout(function () {
            panelLeaveTimer = 0;
            if (!state.panelOpen || state.oauthPending) return;
            closePanel({ cancelOAuth: false });
        }, PANEL_LEAVE_DELAY_MS);
        if (panelLeaveTimer && typeof panelLeaveTimer.unref === 'function') panelLeaveTimer.unref();
    }
    function semanticAnchorEntryAvailable() {
        // During exploration the cat entity is away, but the shared desktop
        // nest is still the service-confirmed interaction anchor.
        if (explorationActive(currentExploration())) return true;
        if (!state.catLocalActive || state.authStatus !== 'logged-in') return false;
        // Keep a read-only retry surface on the verified cat anchor during a
        // recoverable outage. No snapshot values or operations are exposed.
        if (state.serviceStatus === 'unavailable' || state.serviceStatus === 'auth-refreshing') return true;
        if (state.serviceStatus !== 'ready') return false;
        // First binding and an explicitly service-authorised rebind need a
        // visible entry. An unbound role still receives no pet snapshot or
        // usable care operations; the surface exposes only the confirm flow.
        return state.bindingStatus === 'needs-binding' ||
            (state.bindingStatus === 'unbound' && state.canRebind === true) ||
            isPetCareCompatibilityReady();
    }
    function degradedSurfaceNeedsAuthRefresh() {
        return state.authStatus === 'unknown' ||
            state.serviceStatus === 'auth-refreshing' ||
            state.serviceReason === 'oauth-status-unavailable' ||
            state.serviceReason === 'pet-care-auth-expired';
    }
    function recoverDegradedSurface(reason) {
        if (degradedSurfaceRecoveryPromise) return degradedSurfaceRecoveryPromise;
        // A confirmed logout is final until the user starts a new login. A
        // panel reopen must never reinterpret cached identity as permission
        // to restore the previous pet context.
        if (state.authStatus === 'logged-out' || state.oauthPending) {
            return Promise.resolve(null);
        }
        var recovery = null;
        if (degradedSurfaceNeedsAuthRefresh()) {
            recovery = function () { return refreshAuthStatus(reason || 'degraded-surface-retry'); };
        } else if (state.authStatus === 'logged-in' && !state.bindingPending &&
            (state.serviceStatus === 'unavailable' || state.bindingStatus === 'unknown' ||
                state.bindingStatus === 'unavailable' || state.petRefreshRequired)) {
            recovery = function () { return refreshBinding(reason || 'degraded-surface-retry'); };
        }
        if (!recovery) return Promise.resolve(null);
        var active = Promise.resolve().then(recovery);
        degradedSurfaceRecoveryPromise = active;
        render();
        active.then(function () {
            if (degradedSurfaceRecoveryPromise === active) degradedSurfaceRecoveryPromise = null;
            render();
        }, function () {
            if (degradedSurfaceRecoveryPromise === active) degradedSurfaceRecoveryPromise = null;
            render();
        });
        return active;
    }
    function releasePanelPointer(event) {
        var lostWindow = !!(event && event.type === 'blur');
        var wasActive = panelPointerActive;
        if (lostWindow) panelPointerInside = false;
        panelPointerActive = false;
        if ((wasActive || lostWindow) && !panelPointerInside) schedulePanelAutoClose();
    }
    function showSoon() {
        if (!semanticAnchorEntryAvailable()) return;
        cancelPanelAutoClose();
        if (hideTimer) { window.clearTimeout(hideTimer); hideTimer = 0; }
        if (anchorBusy()) return;
        if (state.hoverVisible || hoverTimer) return;
        hoverTimer = window.setTimeout(function () {
            hoverTimer = 0;
            var current = anchorState();
            if (semanticAnchorEntryAvailable() && current && current.visible &&
                current.rect && !current.busy) {
                // The demo's surface is the entry itself. There is no
                // separate "养宠·已绑定" launcher in front of it.
                state.dismissedExplorationId = '';
                state.panelOpen = true;
                visible(true);
                render();
                positionOverlay(current.rect);
                recoverDegradedSurface('panel-open-retry');
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
        var available = !!(semanticAnchorEntryAvailable() && current &&
            current.visible && current.rect && !current.busy);
        var loginHintAvailable = !!(current && current.visible && current.rect &&
            !current.busy && state.catLocalActive && state.authStatus === 'logged-out');
        // Keep the product contract explicit: `available` still means the
        // authenticated PetCare surface is available. The account hint is a
        // separate unified-auth affordance and must not masquerade as an
        // enabled养宠入口 while logged out.
        ui.root.dataset.available = available ? 'true' : 'false';
        ui.root.dataset.loginHintAvailable = loginHintAvailable ? 'true' : 'false';
        if (available || loginHintAvailable) {
            positionOverlay(current.rect);
            if (available) showFeatureHintOnce();
            if (loginHintAvailable) maybeShowLoginHint();
        }
        else {
            clearTimers();
            state.loginHintVisible = false;
            if (state.panelOpen || state.hoverVisible) closePanel({ cancelOAuth: false });
        }
        return current;
    }
    function handlePointerMove(event) {
        if (!event || event.pointerType === 'touch') return;
        if (!semanticAnchorEntryAvailable()) return;
        // Chromium can lose pointerup when a drag leaves the transparent
        // window. A subsequent mouse move with no pressed buttons releases
        // the presentation guard instead of disabling auto-close forever.
        if (panelPointerActive && event.pointerType === 'mouse' && event.buttons === 0) {
            panelPointerActive = false;
        }
        var current = syncAnchor();
        if (!current || !current.visible || current.busy) return;
        var rect = current.rect;
        var x = Number(event.clientX) || 0, y = Number(event.clientY) || 0;
        // The semantic anchor is the visible cat in ordinary mode and the
        // shared desktop nest while exploration is active.
        var overAnchor = pointInRect(x, y, rect, 24);
        var overEntry = !!(ui && ui.root && event.target &&
            typeof ui.root.contains === 'function' && ui.root.contains(event.target));
        panelPointerInside = overAnchor || overEntry;
        if (overAnchor || overEntry) {
            cancelPanelAutoClose();
            if (!state.hoverVisible) showSoon();
        } else if (state.panelOpen) {
            schedulePanelAutoClose();
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
    function projectedStatus(snapshot, options) {
        options = options && typeof options === 'object' ? options : {};
        var values = snapshot && snapshot.status_values;
        if (!values || typeof values !== 'object') return null;
        var result = {
            values: {}, interaction: NaN, applied: false,
            projected_at_ms: NaN, clamp_min: 0, clamp_max: 100,
        };
        ['satiety', 'energy', 'cleanliness', 'mood', 'progress'].forEach(function (key) {
            var current = Number(values[key]);
            if (Number.isFinite(current)) result.values[key] = current;
        });
        var projection = activeStatusProjection();
        var rules = projection && projection.status_rules;
        var settledAt = snapshot && Date.parse(snapshot.last_settled_at);
        var exploring = explorationActive(snapshot && snapshot.exploration);
        if (!rules || !Number.isFinite(settledAt) ||
            (rules.paused_during_exploration === true && exploring)) return result;
        var estimatedServerTime = projection._server_time_ms +
            Math.max(0, monotonicNow() - projection._received_monotonic);
        var projectedHours = Math.max(0, estimatedServerTime - settledAt) / 3600000;
        var projectionMin = Number(rules.clamp && rules.clamp.min);
        var projectionMax = Number(rules.clamp && rules.clamp.max);
        var bounded = function (number) {
            return Number.isFinite(projectionMin) && Number.isFinite(projectionMax)
                ? Math.max(projectionMin, Math.min(projectionMax, number)) : number;
        };
        ['satiety', 'energy', 'cleanliness'].forEach(function (key) {
            var base = Number(values[key]);
            var rate = Number(rules.per_hour && rules.per_hour[key]);
            if (Number.isFinite(base) && Number.isFinite(rate)) {
                result.values[key] = bounded(base + rate * projectedHours);
            }
        });
        if (options.include_mood === false) {
            result.applied = true;
            result.projected_at_ms = estimatedServerTime;
            if (Number.isFinite(projectionMin)) result.clamp_min = projectionMin;
            if (Number.isFinite(projectionMax)) result.clamp_max = projectionMax;
            return result;
        }
        var interaction = Number(projection.status_seed && projection.status_seed.interaction) +
            Number(rules.per_hour && rules.per_hour.interaction) * projectedHours;
        result.interaction = bounded(interaction);
        var weights = rules.mood_weights;
        var mood = ['satiety', 'energy', 'cleanliness'].reduce(function (total, key) {
            return total + Number(weights[key]) * Number(result.values[key]);
        }, 0) + Number(weights.interaction) * result.interaction;
        result.values.mood = bounded(mood);
        result.applied = true;
        result.projected_at_ms = estimatedServerTime;
        if (Number.isFinite(projectionMin)) result.clamp_min = projectionMin;
        if (Number.isFinite(projectionMax)) result.clamp_max = projectionMax;
        return result;
    }
    function optimisticPreviewBase() {
        var preview = cloneSnapshot(state.snapshot);
        var current = projectedStatus(preview, { include_mood: false });
        if (!preview || !current || !current.applied) return preview;
        ['satiety', 'energy', 'cleanliness'].forEach(function (key) {
            if (Number.isFinite(current.values[key])) preview.status_values[key] = current.values[key];
        });
        preview.last_settled_at = new Date(current.projected_at_ms).toISOString();
        return preview;
    }
    function applyOptimisticPreview(category, contentId) {
        var preview = optimisticPreviewBase();
        var action = actionPreview(category, contentId);
        if (!preview || !action) return null;
        var values = preview.status_values;
        if (!values || typeof values !== 'object') return null;
        var projected = projectedStatus(preview, { include_mood: false });
        var clampMin = projected ? projected.clamp_min : 0;
        var clampMax = projected ? projected.clamp_max : 100;
        var add = function (key, field) {
            var delta = Number(action[field] || 0), current = Number(values[key]);
            if (!Number.isFinite(delta) || !Number.isFinite(current)) return;
            values[key] = Math.max(clampMin, Math.min(clampMax, current + delta));
        };
        // Care actions use one shared effect vector.  Preview every direct
        // status component the server may apply, regardless of whether the
        // action is presented as feeding or cleaning.
        add('satiety', 'satiety_delta');
        add('energy', 'energy_delta');
        add('cleanliness', 'cleanliness_delta');
        // Mood, progress, inventory and rewards remain server-confirmed.
        return preview;
    }
    function applyOptimisticPlayPreview(contentId) {
        var preview = optimisticPreviewBase();
        var configured = state.playActions && state.playActions[contentId];
        if (!preview || !configured || typeof configured !== 'object') return null;
        var values = preview.status_values;
        if (!values || typeof values !== 'object') return null;
        var projected = projectedStatus(preview, { include_mood: false });
        var clampMin = projected ? projected.clamp_min : 0;
        var clampMax = projected ? projected.clamp_max : 100;
        var addBounded = function (key, field) {
            var delta = Number(configured[field] || 0), current = Number(values[key]);
            if (!Number.isFinite(delta) || !Number.isFinite(current)) return;
            values[key] = Math.max(clampMin, Math.min(clampMax, current + delta));
        };
        addBounded('energy', 'energy_delta');
        addBounded('cleanliness', 'cleanliness_delta');
        // Progress, inventory, rewards and unlocks are durable service results.
        // Keep the preview limited to presentation values until the operation
        // returns a confirmed snapshot.
        return preview;
    }
    function value(key) {
        var currentSnapshot = displaySnapshot();
        var values = currentSnapshot && currentSnapshot.status_values;
        if (!values || typeof values !== 'object' || values[key] == null) return '—';
        var number = Number(values[key]);
        if (!Number.isFinite(number)) return '—';
        // The service applies interaction_delta and then recomputes mood in
        // the same transaction as each care/play action. The optimistic
        // preview intentionally does not reproduce that business rule, so it
        // must not derive a partial mood from only the direct status deltas.
        // Keep mood on the authorised read-only projection until the complete
        // confirmed snapshot replaces the preview.
        var projectionSnapshot = key === 'mood' && state.optimisticSnapshot && state.snapshot
            ? state.snapshot : currentSnapshot;
        var projected = projectedStatus(projectionSnapshot, { include_mood: key === 'mood' });
        if (projected && Object.prototype.hasOwnProperty.call(projected.values, key)) {
            number = projected.values[key];
        }
        // Persisted snapshots from an older service may contain fractional
        // time-settlement values. Status cards are intentionally integer
        // values so a stale fractional snapshot cannot overflow the card.
        var bounded = key === 'progress' ? Math.max(0, number) : Math.max(0, Math.min(100, number));
        return String(Math.round(bounded));
    }
    function careReady(options) {
        options = options && typeof options === 'object' ? options : {};
        return isPetCareCompatibilityReady() && !state.pendingAction && !state.playActivity &&
            !state.playPending && (options.allowTaskRead === true || !state.taskLoading) &&
            !state.taskPendingId && !petMutationHardBlocked() &&
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
    function explorationRemainingMs(exploration) {
        var end = explorationEndMs(exploration);
        var serverNow = estimatedServerTimeMs();
        return Number.isFinite(end) && Number.isFinite(serverNow) ? end - serverNow : NaN;
    }
    function explorationDueByServerClock(exploration) {
        var remaining = explorationRemainingMs(exploration);
        return Number.isFinite(remaining) && remaining <= 0;
    }
    function normalizedExplorationLocation(location, fallbackId) {
        if (!location || typeof location !== 'object' || Array.isArray(location)) return null;
        var id = str(location.id || location.location_id || location.locationId || fallbackId);
        var normalized = Object.assign({}, location);
        if (id) normalized.id = id;
        normalized.title = playerFacingLabel(location.title || location.name, id, '探索地点');
        return normalized;
    }
    function configuredExplorationLocation(locationId) {
        var id = str(locationId);
        if (!id) return null;
        var configured = state.explorationLocations;
        if (Array.isArray(configured)) {
            for (var index = 0; index < configured.length; index += 1) {
                var candidate = normalizedExplorationLocation(configured[index]);
                if (candidate && candidate.id === id) return candidate;
            }
            return null;
        }
        if (!configured || typeof configured !== 'object') return null;
        if (Object.prototype.hasOwnProperty.call(configured, id)) {
            var keyed = normalizedExplorationLocation(configured[id], id);
            if (keyed) return keyed;
        }
        var keys = Object.keys(configured);
        for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
            var entry = normalizedExplorationLocation(configured[keys[keyIndex]]);
            if (entry && entry.id === id) return entry;
        }
        return null;
    }
    function defaultExplorationLocation() {
        var configured = state.explorationLocations;
        if (Array.isArray(configured)) {
            for (var index = 0; index < configured.length; index += 1) {
                var candidate = normalizedExplorationLocation(configured[index]);
                if (candidate && candidate.id) return candidate;
            }
        } else if (configured && typeof configured === 'object') {
            var keys = Object.keys(configured);
            for (var keyIndex = 0; keyIndex < keys.length; keyIndex += 1) {
                var entry = normalizedExplorationLocation(configured[keys[keyIndex]], keys[keyIndex]);
                if (entry && entry.id) return entry;
            }
        }
        return { id: '', title: '探索地点',
            description: '选择一个地点，猫会在倒计时结束后回来。' };
    }
    function explorationLocation(exploration) {
        var locationId = str(exploration && (exploration.location_id || exploration.locationId));
        var pinned = exploration && (exploration.location || exploration.location_config);
        if (typeof pinned === 'string') {
            var pinnedId = str(pinned);
            return configuredExplorationLocation(pinnedId) || { id: pinnedId, title: '探索地点' };
        }
        var pinnedLocation = normalizedExplorationLocation(pinned, locationId);
        if (pinnedLocation) return pinnedLocation;
        var configuredLocation = configuredExplorationLocation(locationId);
        if (configuredLocation) return configuredLocation;
        return { id: locationId,
            title: playerFacingLabel(
                exploration && (exploration.location_name || exploration.locationName || exploration.name),
                locationId,
                '探索地点'
            ),
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
            ? (safePetCareImageUrl(exploration.asset_url || exploration.assetUrl ||
                location.asset_url || location.assetUrl) || '/static/assets/neko-idle/cat-bad.png')
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
    function clearExplorationLetterRefreshTimer(resetAttempt) {
        if (explorationLetterRefreshTimer) {
            try { window.clearTimeout(explorationLetterRefreshTimer); } catch (_) {}
            explorationLetterRefreshTimer = 0;
        }
        if (resetAttempt === true) explorationLetterRefreshAttemptKey = '';
    }
    function explorationNextLetterHint(exploration) {
        if (!explorationActive(exploration)) return null;
        var at = Date.parse(str(exploration.next_letter_at || exploration.nextLetterAt));
        var startedAt = Date.parse(str(exploration.departed_at || exploration.departedAt ||
            exploration.started_at || exploration.startedAt));
        var endsAt = explorationEndMs(exploration);
        if (!Number.isFinite(at) || !Number.isFinite(startedAt) || !Number.isFinite(endsAt) ||
            at < startedAt || at > endsAt) return null;
        return { at: at, key: explorationId(exploration) + ':' + String(at) };
    }
    function scheduleExplorationLetterRefresh() {
        clearExplorationLetterRefreshTimer(false);
        var exploration = currentExploration();
        var hint = explorationNextLetterHint(exploration);
        if (!hint || !state.accountId || !state.currentCharacterUid ||
            state.authStatus !== 'logged-in' || state.bindingStatus !== 'bound' ||
            state.explorationPending || state.explorationRecallRequested ||
            document.visibilityState === 'hidden') return;
        var serverNow = estimatedServerTimeMs();
        if (!Number.isFinite(serverNow)) return;
        if (explorationLetterRefreshAttemptKey === hint.key) return;
        var expectedActivityId = explorationId(exploration);
        var expectedGeneration = state.generation;
        var delay = Math.max(25, hint.at - serverNow);
        var maximumDelay = 2147483647;
        explorationLetterRefreshTimer = window.setTimeout(function onLetterRefreshDue() {
            explorationLetterRefreshTimer = 0;
            var latest = currentExploration();
            var latestHint = explorationNextLetterHint(latest);
            if (expectedGeneration !== state.generation ||
                expectedActivityId !== explorationId(latest) || !latestHint ||
                latestHint.key !== hint.key || document.visibilityState === 'hidden' ||
                state.explorationPending || state.explorationRecallRequested) return;
            if (delay > maximumDelay) {
                scheduleExplorationLetterRefresh();
                return;
            }
            // One service hint authorises one automatic read. If a request
            // arrives slightly before the service boundary and returns the
            // same hint, it is not armed again into an accidental poll loop.
            explorationLetterRefreshAttemptKey = hint.key;
            refreshExploration();
        }, Math.min(delay, maximumDelay));
        if (explorationLetterRefreshTimer &&
            typeof explorationLetterRefreshTimer.unref === 'function') {
            explorationLetterRefreshTimer.unref();
        }
    }
    function clearExplorationTimer(options) {
        options = options && typeof options === 'object' ? options : {};
        if (explorationTimer) { try { window.clearTimeout(explorationTimer); } catch (_) {} explorationTimer = 0; }
        if (explorationDepartureTimer) {
            try { window.clearTimeout(explorationDepartureTimer); } catch (_) {}
            explorationDepartureTimer = 0;
        }
        if (explorationReturnTimer) {
            try { window.clearTimeout(explorationReturnTimer); } catch (_) {}
            explorationReturnTimer = 0;
        }
        if (options.keepLetterRefresh !== true) clearExplorationLetterRefreshTimer(true);
        if (options.keepStatusRead !== true) {
            explorationStatusReadGeneration += 1;
            explorationStatusRead = null;
        }
    }
    function explorationLeaseStorageKey(accountId) {
        var account = str(accountId);
        return account ? EXPLORATION_LEASE_STORAGE_PREFIX + encodeURIComponent(account) : '';
    }
    function parseExplorationLease(raw, accountId) {
        if (!raw) return null;
        try {
            var lease = JSON.parse(raw);
            if (!lease || typeof lease !== 'object' || !str(lease.accountId) ||
                !str(lease.characterUid) || !str(lease.explorationId) ||
                !lease.exploration || !explorationActive(lease.exploration)) return null;
            if (str(accountId) && lease.accountId !== str(accountId)) return null;
            return lease;
        } catch (_) { return null; }
    }
    function readExplorationLease(accountId) {
        var account = str(accountId == null ? state.accountId : accountId);
        try {
            if (!window.sessionStorage) return null;
            var scopedKey = explorationLeaseStorageKey(account);
            var scoped = scopedKey
                ? parseExplorationLease(window.sessionStorage.getItem(scopedKey), account)
                : null;
            if (scoped) return scoped;
            // Migrate the pre-account-scoped session record lazily.  A record
            // for another account is deliberately invisible to the caller.
            var legacy = parseExplorationLease(
                window.sessionStorage.getItem(EXPLORATION_LEASE_STORAGE_KEY), account
            );
            if (legacy && scopedKey) {
                window.sessionStorage.setItem(scopedKey, JSON.stringify(legacy));
            }
            return legacy;
        } catch (_) { return null; }
    }
    function writeExplorationLease(exploration) {
        var id = explorationId(exploration);
        if (!id || !explorationActive(exploration) || !state.accountId || !state.currentCharacterUid) return;
        try {
            if (!window.sessionStorage) return;
            var lease = {
                accountId: state.accountId, characterUid: state.currentCharacterUid,
                boundRoleId: state.bindingRoleId, explorationId: id,
                exploration: exploration, updatedAt: Date.now(),
            };
            window.sessionStorage.setItem(
                explorationLeaseStorageKey(state.accountId), JSON.stringify(lease)
            );
            // Keep one legacy copy while the same account is active so older
            // in-process readers can finish the current session. Account
            // replacement removes this unscoped copy before publishing B.
            window.sessionStorage.setItem(EXPLORATION_LEASE_STORAGE_KEY, JSON.stringify(lease));
        } catch (_) {}
    }
    function clearLegacyExplorationLease(accountId) {
        try {
            if (!window.sessionStorage) return;
            var legacy = parseExplorationLease(
                window.sessionStorage.getItem(EXPLORATION_LEASE_STORAGE_KEY)
            );
            if (!legacy || !str(accountId) || legacy.accountId === str(accountId)) {
                window.sessionStorage.removeItem(EXPLORATION_LEASE_STORAGE_KEY);
            }
        } catch (_) {}
    }
    function clearExplorationLease(accountId) {
        var account = str(accountId == null ? state.accountId : accountId);
        try {
            if (!window.sessionStorage) return;
            var scopedKey = explorationLeaseStorageKey(account);
            if (scopedKey) window.sessionStorage.removeItem(scopedKey);
            clearLegacyExplorationLease(account);
        } catch (_) {}
    }
    function clearExplorationLeaseIfMatches(accountId, characterUid, explorationIdValue) {
        var lease = readExplorationLease(accountId);
        if (!lease || lease.accountId !== str(accountId) || lease.characterUid !== str(characterUid) ||
            lease.explorationId !== str(explorationIdValue)) return false;
        clearExplorationLease(accountId);
        return true;
    }
    function applyExplorationRecoveryLease() {
        var lease = readExplorationLease(state.accountId);
        if (!lease || lease.accountId !== state.accountId || lease.characterUid !== state.currentCharacterUid) return false;
        var activityId = explorationId(lease.exploration);
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
        var dismissed = !!activityId && state.dismissedExplorationId === activityId;
        if (!dismissed) state.panelOpen = true;
        clearExplorationLetterRefreshTimer(true);
        scheduleExplorationTimer();
        if (ui) ui.root.dataset.available = 'true';
        if (!dismissed) visible(true);
        render();
        return true;
    }
    function scheduleExplorationTimer() {
        clearExplorationTimer({ keepLetterRefresh: true, keepStatusRead: true });
        var exploration = currentExploration();
        if (!explorationActive(exploration) || (state.explorationPending &&
            (state.explorationPending.operation === 'complete' ||
                state.explorationPending.operation === 'recall'))) return;
        var activeId = explorationId(exploration);
        if (state.explorationAutoCompleteBlockedId &&
            state.explorationAutoCompleteBlockedId !== activeId) {
            state.explorationAutoCompleteBlockedId = '';
            state.explorationAutoCompleteRetryAt = 0;
        }
        // A stable business rejection cannot become an unbounded POST loop.
        // The nest keeps its explicit completion/recall controls, so the user
        // can retry after correcting the condition without background traffic.
        if (state.explorationAutoCompleteBlockedId === activeId) return;
        var remaining = explorationRemainingMs(exploration);
        var monotonic = monotonicNow();
        var retryAt = Number(state.explorationAutoCompleteRetryAt) || 0;
        // A recovery-only lease contains no trusted server-time anchor. Keep
        // the nest visible, but wait for the next authoritative snapshot
        // instead of comparing the deadline with the device wall clock.
        if (!Number.isFinite(remaining)) return;
        var endPassed = remaining <= 0;
        var retryDelay = retryAt > monotonic ? retryAt - monotonic : 0;
        var delay = Math.max(250, endPassed ? retryDelay : remaining);
        explorationTimer = window.setTimeout(function tickExploration() {
            explorationTimer = 0;
            if (!explorationActive(currentExploration())) return;
            render();
            if (explorationDueByServerClock(currentExploration()) && !state.explorationPending &&
                monotonicNow() >= (Number(state.explorationAutoCompleteRetryAt) || 0)) {
                completeExploration();
            } else if (!state.explorationPending) scheduleExplorationTimer();
        }, endPassed ? delay : Math.min(delay, 1000));
        if (explorationTimer && typeof explorationTimer.unref === 'function') explorationTimer.unref();
    }
    function emitExplorationGate(active, reason) {
        var exploration = currentExploration();
        var activityId = explorationId(exploration);
        var activityGeneration = explorationGeneration(exploration);
        var gateContext = captureModeContext();
        if (active) {
            explorationGateIdentity = {
                activityId: activityId,
                activityGeneration: activityGeneration,
            };
            if (explorationReturnTimer) {
                window.clearTimeout(explorationReturnTimer);
                explorationReturnTimer = 0;
            }
            // A playground click may already hold a service lease while its
            // visible cat response is still in flight. Retire that local
            // authorization before broadcasting the away gate, so a
            // synchronous cancellation terminal cannot be mistaken for a
            // rewardable response from before departure.
            clearPreauthorizedPlayActivities();
            emit('neko:pet-care:exploration-gate', {
                active: true, exploration_id: activityId,
                explorationId: activityId, activityId: activityId,
                generation: activityGeneration, activityGeneration: activityGeneration,
                context: gateContext,
                reason: reason || 'exploration-state-change',
            });
            // Exploration owns the cat entity for its full activity window.
            // With the away gate now installed, end any yarn/consumable play
            // state so a late response cannot turn an away interaction into
            // a playing settlement.
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
            if (!activityId && explorationGateIdentity) {
                activityId = explorationGateIdentity.activityId;
                activityGeneration = explorationGateIdentity.activityGeneration;
            }
            explorationGateIdentity = null;
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
                var latestActivityId = explorationId(latest);
                if (explorationActive(latest) || (latestActivityId &&
                    (latestActivityId !== activityId ||
                        explorationGeneration(latest) !== activityGeneration))) return;
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
                    context: gateContext,
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
            var activityId = explorationId(snapshot && snapshot.exploration);
            if (state.dismissedExplorationId && state.dismissedExplorationId !== activityId) {
                state.dismissedExplorationId = '';
            }
            var dismissed = !!activityId && state.dismissedExplorationId === activityId;
            // Restore a server-confirmed activity once after refresh/login.
            // Once the user closes that activity's surface, later snapshot
            // refreshes leave it collapsed; the shared desktop nest remains
            // the semantic hover anchor for reopening it.
            if (!dismissed) state.panelOpen = true;
            if (ui) {
                ui.root.dataset.available = 'true';
                if (!dismissed) visible(true);
            }
            scheduleExplorationTimer();
            scheduleExplorationLetterRefresh();
            var gateMode = window.nekoPetCareMode;
            var modeGateMissing = !!(gateMode &&
                typeof gateMode.isCurrentExploration === 'function' &&
                !gateMode.isCurrentExploration({ activityId: activityId }));
            if (!state.explorationGateActive || modeGateMissing) {
                state.explorationGateActive = true;
                emitExplorationGate(true, reason || 'exploration-snapshot');
            }
        } else {
            clearExplorationLease();
            state.dismissedExplorationId = '';
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
    function inventoryRequirementLabel(owned, required) {
        var text = '库存 × ' + Math.max(0, Math.floor(Number(owned) || 0));
        return Number(required) > 1 ? text + ' · 本次 × ' + required : text;
    }
    function sulkRecoveryItemId() {
        var negative = state.snapshot && state.snapshot.negative_status;
        if (!(negative && negative.sulk && negative.sulk.active)) return '';
        var recoveryTask = taskItems().find(function (item) {
            return item && str(item.recovery_status).toLowerCase() === 'sulk';
        });
        return str(recoveryTask && recoveryTask.recovery_item_id);
    }
    function isSulkRecoveryPlay(contentId, action) {
        var negative = state.snapshot && state.snapshot.negative_status;
        if (!(negative && negative.sulk && negative.sulk.active)) return false;
        var recoveryItem = sulkRecoveryItemId();
        if (!recoveryItem) return false;
        action = action && typeof action === 'object'
            ? action : state.playActions && state.playActions[contentId];
        return str(action && action.inventory_item) === recoveryItem;
    }
    function playAllowedDuringSulk(contentId, action) {
        var negative = state.snapshot && state.snapshot.negative_status;
        if (!(negative && negative.sulk && negative.sulk.active)) return true;
        return isSulkRecoveryPlay(contentId, action);
    }
    function cooldownRemainingSeconds(category, contentId) {
        var action = category === 'play'
            ? state.playActions && state.playActions[contentId]
            : actionPreview(category, contentId);
        var availableAt = action && typeof action === 'object'
            ? (action.cooldown_available_at || action.cooldownAvailableAt) : null;
        // Older snapshots expose cleaning deadlines only in the compatibility
        // map. New snapshots put the effective (including shared-group)
        // deadline on every care/play action.
        if (!availableAt && category === 'cleaning') {
            var cooldowns = state.snapshot && state.snapshot.cleaning_cooldowns;
            var entry = cooldowns && typeof cooldowns === 'object' ? cooldowns[contentId] : null;
            availableAt = entry && typeof entry === 'object'
                ? (entry.available_at || entry.availableAt) : entry;
        }
        var availableAtMs = Date.parse(str(availableAt));
        if (!Number.isFinite(availableAtMs)) return 0;
        var serverNow = estimatedServerTimeMs();
        // Cooldown deadlines belong to the service clock. A skewed device
        // clock must not keep a confirmed action disabled too long or make it
        // appear available before the service deadline.
        if (!Number.isFinite(serverNow)) return 0;
        return Math.max(0, Math.ceil((availableAtMs - serverNow) / 1000));
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
        ['feeding', 'cleaning'].forEach(function (category) {
            careContent(category).forEach(function (item) {
                var remaining = cooldownRemainingSeconds(category, item.id);
                if (remaining > 0) shortest = Math.min(shortest, remaining);
            });
        });
        playActionEntries().forEach(function (item) {
            var remaining = cooldownRemainingSeconds('play', item.id);
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
        // This is a display-only refresh for the short-lived, versioned
        // service projection. It never reads the service or writes a snapshot.
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
        var fallbackLabel = category === 'feeding' ? '食物' :
            category === 'cleaning' ? '清洁道具' : '养宠道具';
        return Object.keys(configured).map(function (contentId) {
            var action = configured[contentId];
            if (!action || typeof action !== 'object' || Array.isArray(action)) return null;
            var itemId = str(action.inventory_item) || contentId;
            return {
                id: contentId,
                action: action,
                label: itemDirectoryLabel(itemId) || playerFacingLabel(
                    action.label || action.display_name,
                    [contentId, action.inventory_item],
                    fallbackLabel
                ),
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
                str(item.inventory_item) && !!playActionAdapter(contentId, item);
        }).map(function (contentId) {
            var item = configured[contentId];
            return { id: contentId, action: item,
                label: itemDirectoryLabel(str(item.inventory_item) || contentId) || playerFacingLabel(
                    item.label || item.display_name,
                    [contentId, item.inventory_item],
                    '陪玩道具'
                ) };
        });
    }
    function actionLabel(category) {
        return category === 'feeding' ? '喂食' : category === 'cleaning' ? '清洁' :
            category === 'play' ? '陪玩' : category === 'exploration' ? '探索' : '';
    }
    // Keep service diagnostics in state/events for development, but never
    // render an unknown service code, response body or exception message in
    // the pet surface.  The authority owns machine-readable error codes;
    // this single presentation boundary owns short, actionable user copy.
    function petCareUserFailureText(value, scope) {
        var raw = str(value);
        var code = raw.toLowerCase().replace(/[\s.\-]+/g, '_');
        if (code === 'inventory_insufficient') {
            if (scope === 'feeding') return '背包中没有可用食物';
            if (scope === 'cleaning') return '缺少这次清洁所需的道具';
            if (scope === 'play') return '背包中没有可用的陪玩道具';
            return '背包中没有足够的道具';
        }
        var messages = {
            satiety_full: '饱食度已满',
            cleanliness_full: '清洁度已满',
            pet_care_inventory_insufficient: '背包中没有足够的道具',
            pet_care_inventory_item_unavailable: '这个道具暂时不可用',
            play_inventory_insufficient: '背包中没有可用的陪玩道具',
            play_sulk_blocked: '猫咪正在赌气，只会回应恢复任务指定的互动',
            cleaning_cooldown: '清洁操作还在冷却中',
            play_daily_limit: '今日该陪玩次数已用完',
            play_cooldown: '陪玩还在冷却中',
            pet_care_action_cooldown: '这项操作还在冷却中',
            play_source_not_allowed: '这次玩耍不是用户主动发起的',
            play_user_not_initiated: '需要用户主动操作陪玩内容',
            play_no_response: '猫没有回应这次互动',
            play_not_completed: '陪玩动作没有完成',
            play_feedback_unavailable: '猫的陪玩反馈暂不可用',
            play_retry_pending: '上一次陪玩结果待确认，请重试同一种陪玩',
            energy_insufficient: '精力不足，暂时不能进行这项陪玩',
            activity_in_progress: '探索期间不能照料',
            exploration_blocked_by_status: '生病期间不能探索，请先完成求药恢复',
            exploration_location_required: '请选择可用的探索地点',
            recovery_task_not_claimed: '请先完成并领取恢复任务',
            session_stale: '账号状态已变化，请重新确认养宠状态',
            pet_context_invalid: '当前角色已变化，请重新确认养宠状态',
            care_action_timeout: '操作超时，请稍后重试',
            care_action_snapshot_incomplete: '最新养宠状态暂时无法确认，请稍后重试',
            care_action_rejected: '这次操作暂时不能完成',
            exploration_start_rejected: '暂时不能开始探索',
            exploration_start_timeout: '出发请求超时，请稍后重试',
            exploration_complete_rejected: '探索结算暂未完成',
            exploration_recall_rejected: '召回请求暂未完成，可稍后重试',
            exploration_settle_timeout: '返回请求超时，请稍后重试',
            exploration_load_failed: '探索状态暂时不可用，请稍后重试',
            recovery_status_not_active: '当前状态不需要恢复',
            recovery_item_insufficient: '背包中没有恢复所需物品',
            recovery_status_unavailable: '当前恢复状态不可用',
            use_recovery_item_failed: '恢复操作未完成',
            login_required: '登录状态已失效，请重新登录',
            auth_required: '登录状态已失效，请重新登录',
            unauthorized: '登录状态已失效，请重新登录',
            pet_care_auth_expired: '登录状态已失效，请重新登录',
            pet_care_account_required: '请先登录 N.E.K.O 账号',
            client_binding_required: '当前客户端尚未完成账号验证，请重新登录',
            role_not_bound: '当前角色没有绑定这只宠物',
            role_mismatch: '当前角色与宠物绑定不一致，请切回绑定角色',
            character_identity_unavailable: '暂时无法确认当前角色',
            pet_care_bind_failed: '暂时无法完成绑定，请稍后重试',
            pet_care_rebind_contract_invalid: '换绑结果暂时无法确认，请重新读取状态',
            rebind_version_required: '请先重新读取宠物状态再换绑',
            state_version_conflict: '宠物状态已更新，请重试',
            config_revision_conflict: '养宠规则已更新，请再试一次',
            pet_care_config_revision_required: '正在刷新养宠规则，请稍后再试',
            pet_care_config_revision_cas_unavailable: '养宠功能需要更新，请稍后再试',
            pet_care_binding_version_conflict: '绑定状态已更新，请重新确认',
            pet_care_task_revision_conflict: '任务状态已更新，请重试',
            activity_conflict: '猫咪正在进行其他活动，请稍后再试',
            pet_care_activity_conflict: '猫咪正在进行其他活动，请稍后再试',
            negative_status_conflict: '猫咪当前状态不能进行这项操作',
            pet_care_negative_status_conflict: '猫咪当前状态不能进行这项操作',
            exploration_not_complete: '探索还没有结束，请稍后再试',
            exploration_not_ready: '探索还没有准备好，请稍后再试',
            exploration_not_found: '这次探索已经结束或不存在',
            exploration_location_unavailable: '这个探索地点暂时不可用',
            exploration_requirement_failed: '还没有满足这个地点的探索条件',
            exploration_requirements_not_met: '还没有满足这个地点的探索条件',
            play_lease_expired: '这次陪玩已超时，请重新操作',
            play_lease_invalid: '这次陪玩已失效，请重新操作',
            pet_care_play_lease_invalid: '这次陪玩已失效，请重新操作',
            pet_care_play_lease_unavailable: '陪玩暂时不可用，请稍后重试',
            pet_care_write_pending: '上次操作结果还没有确认，请稍后重试',
            operation_in_progress: '上次操作还在处理中，请稍后重试',
            pet_care_pending_write_timeout: '上次操作结果还没有确认，请稍后重试',
            pet_care_pending_write_character_mismatch: '请切回发起上次操作的角色后再重试',
            pet_care_pending_write_context_mismatch: '当前宠物状态已变化，请重新读取后再试',
            pet_care_pending_write_storage_unavailable: '暂时无法安全提交操作，请稍后重试',
            pet_care_pending_write_storage_failed: '暂时无法安全提交操作，请稍后重试',
            pet_care_pending_write_storage_corrupt: '上次操作状态无法确认，请重新读取',
            pet_care_pending_write_lock_unavailable: '暂时无法安全提交操作，请稍后重试',
            pet_care_pending_write_lock_required: '暂时无法安全提交操作，请稍后重试',
            pet_care_pending_write_quarantined: '上次操作状态有冲突，请重新读取后再试',
            idempotency_key_reused: '上次操作状态有冲突，请重新读取后再试',
            pet_care_refresh_required: '操作已确认，最新状态仍需重新读取',
            task_already_claimed: '这项任务已经领取过了',
            task_already_completed: '这项任务已经完成了',
            task_completion_unavailable: '这项任务暂时无法完成',
            pet_care_task_completion_unavailable: '这项任务暂时无法完成',
            task_not_found: '这项任务或任务周期已失效',
            pet_care_task_not_found: '这项任务或任务周期已失效',
            task_unavailable: '今日任务暂不可用',
            task_condition_unavailable: '这项任务条件尚未开放',
            task_not_complete: '这项任务还没有完成',
            task_progress_unavailable: '这项任务进度暂时无法确认',
            task_reward_unavailable: '任务奖励暂不可用',
            task_not_claimable: '完成任务后才能领取奖励',
            pet_care_task_not_claimable: '完成任务后才能领取奖励',
            service_unavailable: '养宠功能暂时不可用，请稍后重试',
            pet_care_service_unavailable: '养宠功能暂时不可用，请稍后重试',
        };
        if (messages[code]) return messages[code];
        if (/(?:^|_)auth(?:_|$)|(?:^|_)login(?:_|$)|unauthori[sz]ed|session_(?:invalid|expired)/.test(code)) {
            return '登录状态已失效，请重新登录';
        }
        if (/role|character|binding/.test(code) && /mismatch|not_bound|invalid|unavailable|conflict/.test(code)) {
            return '当前角色与宠物绑定不一致，请重新确认';
        }
        if (/inventory/.test(code) && /insufficient|unavailable/.test(code)) {
            return '背包中没有足够的可用道具';
        }
        if (/version|revision|stale|context_mismatch/.test(code)) {
            return '养成状态已更新，请重新读取后再试';
        }
        if (/activity.*conflict|conflict.*activity/.test(code)) {
            return '猫咪正在进行其他活动，请稍后再试';
        }
        if (/timeout|timed_out/.test(code)) {
            return '操作超时，请稍后重试';
        }
        if (/service.*unavailable|unavailable.*service|network|fetch_failed/.test(code)) {
            return '养宠功能暂时不可用，请稍后重试';
        }
        if (scope === 'task') return '任务暂时无法处理，请稍后重试';
        if (scope === 'exploration') return '探索暂时无法处理，请稍后重试';
        return '这次操作没有完成，请稍后重试';
    }
    function careErrorText(code, scope) {
        return petCareUserFailureText(code, scope || 'action');
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
        // The persistent mailbox is itself unlockable content. Exploration
        // result presentation may still show a newly arrived letter, but it
        // must not create a second path into this locked module.
        if (view === 'letters' && !letterMailboxAvailable()) {
            view = 'overview';
        }
        var previousView = state.selectedView;
        state.selectedView = view;
        if (view !== 'tasks' || previousView !== 'tasks') state.expandedTaskId = '';
        if (view !== 'overview') state.selectedCategory = '';
        state.progressUnlockOpen = false;
        state.actionError = '';
        render();
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
    function playCareFeedback(category, contentId, action, options) {
        var adapter = careActionAdapter(category, action);
        if (!adapter) return false;
        var presentation = window.NekoCatIdlePresentation;
        var started = false;
        // `runner` is selected only from the closed registry above. Config
        // values are never used as property names.
        if (presentation && typeof presentation[adapter.runner] === 'function') {
            try { started = presentation[adapter.runner]() === true; } catch (_) {}
        }
        if (!started) return false;
        emit('neko:pet-care:feedback', {
            category: category, content_id: contentId,
            presentation: adapter.presentation, started: started,
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
    function explicitPlayActivityId(detail) {
        var payload = detail && typeof detail === 'object' ? detail : {};
        var nested = payload.detail && typeof payload.detail === 'object' ? payload.detail : payload;
        var explicit = str(nested.activityId || nested.activity_id || nested.sessionId || nested.session_id);
        return explicit ? explicit.slice(0, 128) : '';
    }
    function playActivityId(detail, prefix) {
        var explicit = explicitPlayActivityId(detail);
        if (explicit) return explicit;
        playActivitySequence += 1;
        return ((str(prefix) || 'yarn') + '-' + Date.now().toString(36) + '-' +
            playActivitySequence.toString(36)).slice(0, 128);
    }
    function playContextFields() {
        return {
            context_generation: state.generation,
            account_id: str(state.accountId),
            current_character_uid: str(state.currentCharacterUid),
            bound_role_id: str(state.bindingRoleId),
            pet_id: str(state.petId || (state.snapshot && state.snapshot.pet_id)),
            binding_revision: Number(state.bindingRevision),
        };
    }
    function stampPlayContext(activity) {
        return Object.assign({}, activity || {}, playContextFields());
    }
    function playContextMatches(activity) {
        if (!activity || typeof activity !== 'object') return false;
        var current = playContextFields();
        return Number(activity.context_generation) === current.context_generation &&
            str(activity.account_id) === current.account_id &&
            str(activity.current_character_uid) === current.current_character_uid &&
            str(activity.bound_role_id) === current.bound_role_id &&
            str(activity.pet_id) === current.pet_id &&
            Number(activity.binding_revision) === current.binding_revision;
    }
    function playMinimumDurationMs(contentId, actions) {
        var catalog = actions && typeof actions === 'object' && !Array.isArray(actions)
            ? actions : state.playActions;
        var action = catalog && catalog[str(contentId)];
        var seconds = action && action.minimum_duration_seconds;
        var milliseconds = seconds * 1000;
        return Number.isInteger(seconds) && seconds > 0 && Number.isFinite(milliseconds) &&
            milliseconds > 0 && milliseconds <= 0x7fffffff ? milliseconds : null;
    }
    function configuredPlayAdapter(contentId) {
        var action = state.playActions && state.playActions[str(contentId)];
        return playActionAdapter(contentId, action);
    }
    function playRuntimeIdentity(label) {
        return (str(label) + ':' + actionIdempotencyKey()).slice(0, 128);
    }
    function mergePendingPlayIdentity(activity) {
        var pending = state.playPending;
        var pendingActivity = pending && pending.activity;
        if (!pendingActivity || pending.activity_id !== activity.activity_id) return activity;
        return Object.assign({}, activity, {
            cat_mind_request_id: str(pendingActivity.cat_mind_request_id),
            cat_mind_run_id: str(pendingActivity.cat_mind_run_id),
        });
    }
    function currentYarnIdentityTarget(activityId) {
        if (state.playActivity && state.playActivity.content_id === 'yarn' &&
            state.playActivity.activity_id === activityId) return state.playActivity;
        var pending = state.playPending;
        if (pending && pending.content_id === 'yarn' && pending.activity_id === activityId &&
            pending.activity && typeof pending.activity === 'object') return pending.activity;
        return null;
    }
    function observePlayActionRequest(request) {
        var adapter = configuredPlayAdapter('yarn');
        if (!adapter || !request || typeof request !== 'object' ||
            request.source !== adapter.terminalSource ||
            str(request.actionId) !== adapter.terminalActionId || !str(request.requestId)) return;
        var detail = request.detail && typeof request.detail === 'object' ? request.detail : {};
        var activityId = explicitPlayActivityId(detail);
        var target = currentYarnIdentityTarget(activityId);
        if (!target || !playContextMatches(target) ||
            (target.expected_action_id && target.expected_action_id !== request.actionId) ||
            (target.cat_mind_request_id && target.cat_mind_request_id !== request.requestId)) return;
        target.cat_mind_request_id = str(request.requestId);
    }
    function observePlayActionStarted(eventDetail) {
        var adapter = configuredPlayAdapter('yarn');
        if (!adapter) return;
        var envelope = eventDetail && typeof eventDetail === 'object' ? eventDetail : {};
        if (envelope.reason !== 'action-started') return;
        var detail = envelope.detail && typeof envelope.detail === 'object' ? envelope.detail : {};
        var activityId = explicitPlayActivityId(detail);
        var target = currentYarnIdentityTarget(activityId);
        if (!target || !playContextMatches(target) || detail.source !== adapter.terminalSource ||
            str(detail.actionId) !== adapter.terminalActionId || !str(detail.requestId) || !str(detail.runId) ||
            target.cat_mind_request_id !== detail.requestId ||
            (target.cat_mind_run_id && target.cat_mind_run_id !== detail.runId)) return;
        target.cat_mind_run_id = str(detail.runId);
    }
    function playTerminalIdentityMatches(activity, result, expectedSource) {
        if (!playContextMatches(activity) || !result || typeof result !== 'object') return false;
        var detail = result.detail && typeof result.detail === 'object' ? result.detail : result;
        return str(result.source || detail.source) === expectedSource &&
            str(result.actionId || detail.actionId) === str(activity.expected_action_id || activity.lease_action_id) &&
            str(detail.activityId || detail.activity_id) === activity.activity_id &&
            str(detail.requestId || detail.request_id) === str(activity.action_request_id || activity.cat_mind_request_id) &&
            str(detail.runId || detail.run_id) === str(activity.action_run_id || activity.cat_mind_run_id) &&
            !!str(detail.requestId || detail.request_id) && !!str(detail.runId || detail.run_id);
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
        syncCatMindCompatibility(reason || 'pet-care-play-ended');
        render();
        flushDeferredAccountResume(reason || 'pet-care-play-ended');
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
    function invalidateTaskReadForPlay() {
        // A task GET that started before this settlement may carry the old
        // yarn counter. Fence it out before the play POST, otherwise a late
        // GET can roll the service-confirmed 1/3 result back to 0/3.
        taskRequestGeneration += 1;
        state.taskLoading = false;
    }
    function playLeaseContractEnabled() {
        var capabilities = state.snapshot && state.snapshot.service_capabilities;
        return !!(capabilities && Number(capabilities.play_lease_complete) === 1);
    }
    function serviceCapabilityEnabled(capability) {
        var capabilities = state.snapshot && state.snapshot.service_capabilities;
        return !!(capabilities && Number(capabilities[capability]) === 1);
    }
    function contentDirectoryEntry(capability, requireUnlocked) {
        if (!serviceCapabilityEnabled('content_directory')) return null;
        var directory = state.snapshot && state.snapshot.content_directory;
        var entry = directory && typeof directory === 'object' && !Array.isArray(directory)
            ? directory[capability] : null;
        if (!entry || typeof entry !== 'object' || Array.isArray(entry) ||
            typeof entry.unlocked !== 'boolean' || !str(entry.unlock_id)) return null;
        if (requireUnlocked !== false && entry.unlocked !== true) return null;
        return entry;
    }
    function syncCat1PettingCapability() {
        var presentation = window.NekoCatIdlePresentation;
        if (!presentation || typeof presentation.setCat1PettingUnlocked !== 'function') return;
        var entry = contentDirectoryEntry('cat1_petting', true);
        var enabled = state.authStatus === 'logged-in' &&
            state.bindingStatus === 'bound' &&
            state.serviceStatus === 'ready' &&
            !!state.snapshot &&
            !!entry &&
            str(entry.content_id) === 'cat1_petting' &&
            str(entry.feedback_action) === 'cat1_petting';
        try { presentation.setCat1PettingUnlocked(enabled); } catch (_) {}
    }
    function contentDirectoryAssetForItem(itemId) {
        if (!serviceCapabilityEnabled('content_directory')) return '';
        var directory = state.snapshot && state.snapshot.content_directory;
        if (!directory || typeof directory !== 'object' || Array.isArray(directory)) return '';
        var normalized = str(itemId);
        var asset = '';
        Object.keys(directory).some(function (capability) {
            var entry = contentDirectoryEntry(capability, true);
            if (!entry || (str(entry.item_id) !== normalized && str(entry.content_id) !== normalized)) return false;
            asset = safePetCareImageUrl(entry.asset_url || entry.assetUrl);
            return !!asset;
        });
        return asset;
    }
    function itemDirectoryEntry(itemId) {
        if (!serviceCapabilityEnabled('item_directory')) return null;
        var directory = state.snapshot && state.snapshot.item_directory;
        var normalized = str(itemId);
        var entry = normalized && directory && typeof directory === 'object' &&
            !Array.isArray(directory) ? directory[normalized] : null;
        if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
        var projectedId = str(entry.item_id || entry.id);
        if (projectedId && projectedId !== normalized) return null;
        return entry;
    }
    function itemDirectoryLabel(itemId) {
        var entry = itemDirectoryEntry(itemId);
        return entry ? playerFacingLabel(
            entry.display_name,
            [itemId, entry.item_id, entry.id],
            ''
        ) : '';
    }
    function itemDirectoryDescription(itemId) {
        var entry = itemDirectoryEntry(itemId);
        return entry ? str(entry.description) : '';
    }
    function itemDirectoryAssetForItem(itemId) {
        var entry = itemDirectoryEntry(itemId);
        return entry ? safePetCareImageUrl(entry.asset_url) : '';
    }
    function validatePlayLease(activity, body) {
        var lease = body && body.lease && typeof body.lease === 'object' ? body.lease : null;
        var leaseId = str(lease && lease.lease_id);
        var minimumDurationSeconds = lease && lease.minimum_duration_seconds;
        var normalizedMinimumDurationSeconds = Number(minimumDurationSeconds);
        var minimumDurationMs = normalizedMinimumDurationSeconds * 1000;
        var issuedStateVersion = Number(lease && lease.issued_state_version);
        var issuedBindingRevision = lease && lease.issued_binding_revision;
        var normalizedIssuedBindingRevision = Number(issuedBindingRevision);
        var issuedAtMs = Date.parse(str(lease && lease.issued_at));
        var expiresAtMs = Date.parse(str(lease && lease.expires_at));
        var expectedPetId = str(state.petId || (state.snapshot && state.snapshot.pet_id));
        if (!lease || !/^[A-Za-z0-9._~-]{1,4096}$/.test(leaseId) ||
            str(lease.pet_id) !== expectedPetId ||
            str(lease.bound_role_id) !== state.bindingRoleId ||
            str(lease.activity_id) !== activity.activity_id ||
            str(lease.content_id) !== activity.content_id || str(lease.source) !== activity.source ||
            !str(lease.action_id) || (activity.expected_action_id &&
                str(lease.action_id) !== activity.expected_action_id) ||
            minimumDurationSeconds == null ||
            !Number.isInteger(normalizedMinimumDurationSeconds) ||
            normalizedMinimumDurationSeconds <= 0 ||
            !Number.isSafeInteger(minimumDurationMs) || minimumDurationMs > 0x7fffffff ||
            !Number.isFinite(issuedAtMs) || !Number.isFinite(expiresAtMs) ||
            expiresAtMs <= issuedAtMs || minimumDurationMs >= expiresAtMs - issuedAtMs ||
            !Number.isInteger(issuedStateVersion) || issuedStateVersion < 1 ||
            (issuedBindingRevision != null &&
                (!Number.isInteger(normalizedIssuedBindingRevision) ||
                    normalizedIssuedBindingRevision < 1 ||
                    normalizedIssuedBindingRevision !== Number(activity.binding_revision))) ||
            !playContextMatches(activity)) return null;
        return Object.assign({}, activity, {
            lease_id: leaseId,
            lease_action_id: str(lease.action_id),
            lease_expires_at: str(lease.expires_at),
            issued_state_version: issuedStateVersion,
            issued_binding_revision: issuedBindingRevision == null
                ? null : normalizedIssuedBindingRevision,
            expected_state_version: issuedStateVersion,
            lease_minimum_duration_ms: minimumDurationMs,
        });
    }
    async function requestPlayLeaseAfterResume(activity) {
        if (!playContextMatches(activity)) return Promise.reject(new Error('pet-care-context-changed'));
        if (!str(activity && activity.activity_id) || str(activity.activity_id).length > 128) {
            return Promise.reject(new Error('play_activity_identity_invalid'));
        }
        if (activity && activity.lease_id) return Promise.resolve(activity);
        if (!playLeaseContractEnabled()) {
            return Promise.reject(new Error('play_lease_required'));
        }
        if (!serviceCapabilityEnabled('config_revision_write_cas')) {
            return Promise.reject(new Error('pet_care_config_revision_cas_unavailable'));
        }
        var expectedConfigRevision = writeConfigRevision();
        if (!expectedConfigRevision) {
            await refreshBinding('pet-care-config-revision-missing');
            return Promise.reject(new Error('pet_care_config_revision_required'));
        }
        // This is a new lease issue, so derive its CAS only after any earlier
        // account settlement and refresh have completed. Unknown outcomes are
        // replayed from the durable registry and never rebuilt through here.
        var expectedStateVersion = writeStateVersion();
        if (!Number.isInteger(expectedStateVersion) || expectedStateVersion < 1) {
            return Promise.reject(new Error('play_state_version_required'));
        }
        // The snapshot value is only a fail-closed preflight. The immutable
        // lease response supplies the duration authorised for this exact
        // activity and remains authoritative across a config activation.
        if (playMinimumDurationMs(activity.content_id) == null) {
            return Promise.reject(new Error('play_minimum_duration_invalid'));
        }
        // Configurable content/activity identifiers can each be 128
        // characters, while the gateway and formal DTO cap idempotency keys
        // at 200. Generate an independent key and persist it before transport
        // so unknown-outcome retries remain byte-for-byte identical.
        var leaseIdempotencyKey = str(activity && activity.lease_idempotency_key) ||
            actionIdempotencyKey();
        activity = Object.assign({}, activity, {
            expected_state_version: expectedStateVersion,
            lease_idempotency_key: leaseIdempotencyKey,
        });
        var expected = state.generation;
        var requestBody = {
            idempotency_key: leaseIdempotencyKey,
            expected_state_version: expectedStateVersion,
            expected_config_revision: expectedConfigRevision,
            activity_id: activity.activity_id,
            content_id: activity.content_id,
            source: activity.source,
        };
        var businessConfirmed = false;
        try {
            var writeResult = await executeNewPendingWrite('play-lease-issue', requestBody);
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (expected !== state.generation || !playContextMatches(activity)) {
                throw new Error('pet-care-context-changed');
            }
            if (isAuthExpiredResponse(response, body)) {
                await recoverFromAuthExpiry();
                throw new Error('pet-care-auth-expired');
            }
            body = await hydrateSnapshotRequired(body, expected);
            var refreshed = snapshotFromActionBody(body);
            if (refreshed && isCompleteSnapshot(refreshed, state.accountId, state.bindingRoleId)) {
                installServiceSnapshot(refreshed);
            }
            if (!response.ok || operationRejected(body)) {
                throw new Error(operationFailureCode(body, 'play_lease_rejected'));
            }
            var authorized = validatePlayLease(activity, body);
            if (!authorized) throw new Error('play_lease_invalid');
            return authorized;
        } catch (error) {
            if (error && error.petCarePendingWrite) state.pendingWriteError = error.message;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
            throw error;
        }
    }
    async function requestPlayLease(activity) {
        var authorized = await runPetMutationIntent(function () {
            return requestPlayLeaseAfterResume(activity);
        });
        if (!authorized) throw new Error('pet-care-write-pending');
        return authorized;
    }
    function authorizePlaygroundYarnActivation(event) {
        var detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail : null;
        if (!detail || typeof detail.waitUntil !== 'function') return;
        var adapter = configuredPlayAdapter('yarn');
        var activationId = str(detail.activationId || detail.activation_id);
        var activityId = activationId
            ? explicitPlayActivityId({ activityId: 'playground-yarn-click:' + activationId }) : '';
        if (!adapter || !activityId || !careReady() || state.recoveryPendingStatus ||
            !playAllowedDuringSulk('yarn') ||
            state.explorationPending || playMinimumDurationMs('yarn') == null) {
            detail.waitUntil(Promise.resolve(false));
            return;
        }
        var expected = state.generation;
        var activity = stampPlayContext({
            activity_id: activityId,
            content_id: 'yarn',
            source: adapter.source,
            expected_action_id: adapter.terminalActionId,
            user_initiated: true,
            started_at: Number(detail.timestamp) || Date.now(),
            observed_at: Number(detail.timestamp) || Date.now(),
            origin_source: str(detail.source) || 'cat1-playground',
        });
        state.playPending = { phase: 'lease', activity_id: activity.activity_id,
            content_id: activity.content_id, activity: activity };
        state.playError = '';
        syncCatMindCompatibility('pet-care-play-lease-pending');
        render();
        detail.waitUntil(requestPlayLease(activity).then(function (authorized) {
            if (expected !== state.generation || !state.playPending ||
                state.playPending.activity_id !== activity.activity_id) return false;
            var minimumDurationMs = Number(authorized && authorized.lease_minimum_duration_ms);
            state.playPending = null;
            if (!Number.isInteger(minimumDurationMs) || minimumDurationMs <= 0 ||
                !isPetCareCompatibilityReady() ||
                explorationActive(currentExploration())) {
                syncCatMindCompatibility('pet-care-play-lease-ended');
                render();
                flushDeferredAccountResume('play-lease-ended');
                return false;
            }
            preauthorizedPlayActivities[activityId] = authorized;
            var cleanup = window.setTimeout(function () {
                if (preauthorizedPlayActivities[activityId] === authorized) {
                    delete preauthorizedPlayActivities[activityId];
                    syncCatMindCompatibility('play-authorization-expired');
                    flushDeferredAccountResume('play-authorization-expired');
                }
            }, PLAY_ACTIVITY_LEASE_MS);
            if (cleanup && typeof cleanup.unref === 'function') cleanup.unref();
            syncCatMindCompatibility('pet-care-play-lease-ended');
            render();
            return { authorized: true, minimumDurationMs: minimumDurationMs };
        }, function (error) {
            if (expected === state.generation && state.playPending &&
                state.playPending.activity_id === activity.activity_id) {
                state.playPending = null;
                state.playError = error && error.message || 'play_lease_rejected';
                syncCatMindCompatibility('pet-care-play-lease-ended');
                render();
                flushDeferredAccountResume('play-lease-rejected');
            }
            return false;
        }));
    }
    function activateYarnPlayActivity(activity, observation, confirmedResponse) {
        state.playError = '';
        state.playActivity = activity;
        state.optimisticSnapshot = applyOptimisticPlayPreview('yarn');
        armPlayActivityLease(activity);
        wakeCat1ForCare({ category: 'play', content_id: 'yarn', source: activity.source,
            user_initiated: true });
        emit('neko:pet-care:play-activity-start', activity);
        syncCatMindCompatibility('pet-care-yarn-play-started');
        render();
        if (confirmedResponse) settlePlayActivity(activity, 'done', observation);
    }
    function beginPlayActivity(observation, options) {
        var detail = observation && observation.detail && typeof observation.detail === 'object'
            ? observation.detail : {};
        var confirmedResponse = options && options.confirmedResponse === true;
        if (detail.userInitiated !== true) return false;
        if (confirmedResponse) {
            if (detail.catResponded !== true || str(detail.terminalStatus).toLowerCase() !== 'done') return false;
        } else if (detail.endedNearCat !== true) return false;
        var adapter = configuredPlayAdapter('yarn');
        if (!adapter) return false;
        // The observation adapter is the normalized, service-approved source.
        // Platform emitters such as `neko-pc` remain diagnostic provenance in
        // originSource and must not leak into the server authorization field.
        var source = str(observation.source);
        if (source !== adapter.source) return false;
        // A task refresh is a read, not a competing pet activity. Once the
        // observation is proven to be a valid user yarn gesture, let the
        // write lane supersede that stale read. The generation fence below
        // prevents its older task snapshot from rolling the confirmed play
        // result back after settlement.
        if (!careReady({ allowTaskRead: true }) || state.recoveryPendingStatus ||
            !playAllowedDuringSulk('yarn') || state.explorationPending) return false;
        if (state.taskLoading) invalidateTaskReadForPlay();
        var activity = stampPlayContext({
            activity_id: playActivityId(observation),
            content_id: 'yarn',
            source: source,
            expected_action_id: adapter.terminalActionId,
            user_initiated: true,
            started_at: Number(detail.startedAt) || Number(observation.timestamp) || Date.now(),
            observed_at: Number(observation.timestamp) || Date.now(),
            origin_source: str(detail.originSource),
        });
        if (confirmedResponse) {
            var preauthorized = preauthorizedPlayActivities[activity.activity_id];
            delete preauthorizedPlayActivities[activity.activity_id];
            if (detail.petCareAuthorized !== true || !preauthorized) {
                syncCatMindCompatibility('play-authorization-invalid');
                flushDeferredAccountResume('play-authorization-invalid');
                return false;
            }
            activity = Object.assign(activity, preauthorized, {
                activity_id: activity.activity_id,
                started_at: activity.started_at,
                observed_at: activity.observed_at,
                origin_source: activity.origin_source,
            });
        }
        var expected = state.generation;
        state.playPending = { phase: 'lease', activity_id: activity.activity_id,
            content_id: activity.content_id, activity: activity };
        state.playError = '';
        syncCatMindCompatibility('pet-care-play-lease-pending');
        render();
        var authorization = requestPlayLease(activity).then(function (authorized) {
            if (expected !== state.generation || !state.playPending ||
                state.playPending.activity_id !== activity.activity_id) return false;
            authorized = mergePendingPlayIdentity(authorized);
            state.playPending = null;
            activateYarnPlayActivity(authorized, observation, confirmedResponse);
            return true;
        }, function (error) {
            if (expected !== state.generation || !state.playPending ||
                state.playPending.activity_id !== activity.activity_id) return false;
            state.playPending = null;
            state.playError = error && error.message || 'play_lease_rejected';
            state.optimisticSnapshot = null;
            syncCatMindCompatibility('pet-care-play-lease-ended');
            render();
            flushDeferredAccountResume('play-lease-rejected');
            return false;
        });
        pendingYarnAuthorizations[activity.activity_id] = authorization;
        authorization.finally(function () {
            if (pendingYarnAuthorizations[activity.activity_id] === authorization) {
                delete pendingYarnAuthorizations[activity.activity_id];
            }
        });
        return true;
    }
    function authorizeCatMindYarnAction(request) {
        var adapter = configuredPlayAdapter('yarn');
        if (!adapter || !request || typeof request !== 'object' ||
            request.source !== adapter.terminalSource ||
            str(request.actionId) !== adapter.terminalActionId || !str(request.requestId)) return null;
        var detail = request.detail && typeof request.detail === 'object' ? request.detail : {};
        var activityId = explicitPlayActivityId(detail);
        var active = state.playActivity;
        if (activityId && active && active.content_id === 'yarn' &&
            active.activity_id === activityId && playContextMatches(active) && !!str(active.lease_id)) {
            observePlayActionRequest(request);
            var activeMinimumDurationMs = Number(active.lease_minimum_duration_ms);
            return Promise.resolve(str(active.cat_mind_request_id) === str(request.requestId) &&
                Number.isInteger(activeMinimumDurationMs) && activeMinimumDurationMs > 0
                ? { authorized: true, minimumDurationMs: activeMinimumDurationMs }
                : false);
        }
        var pending = state.playPending;
        var authorization = pendingYarnAuthorizations[activityId];
        if (!activityId || !pending || pending.phase !== 'lease' ||
            pending.activity_id !== activityId || !authorization ||
            !pending.activity || !playContextMatches(pending.activity)) return null;
        observePlayActionRequest(request);
        return Promise.resolve(authorization).then(function (allowed) {
            var active = state.playActivity;
            var minimumDurationMs = Number(active && active.lease_minimum_duration_ms);
            return allowed === true && !!active && active.content_id === 'yarn' &&
                active.activity_id === activityId && playContextMatches(active) &&
                str(active.cat_mind_request_id) === str(request.requestId) &&
                !!str(active.lease_id) && Number.isInteger(minimumDurationMs) &&
                minimumDurationMs > 0
                ? { authorized: true, minimumDurationMs: minimumDurationMs }
                : false;
        }, function () { return false; });
    }
    function startToyPlayPresentation(activity, action) {
        var contentId = activity.content_id;
        var presentation = window.NekoCatIdlePresentation;
        var adapter = playActionAdapter(contentId, action);
        var minimumDurationMs = Number(activity && activity.lease_minimum_duration_ms);
        var requestPresentation = adapter && adapter.runner !== 'cat-mind' && presentation &&
            typeof presentation[adapter.runner] === 'function'
            ? presentation[adapter.runner] : null;
        // A consumable is tied to one explicit visual runner supplied by the
        // service catalogue. Unknown or unavailable feedback must fail closed:
        // falling back to the yarn runner would report the wrong interaction
        // and could consume an item after an unrelated animation.
        if (!requestPresentation || adapter.source !== activity.source ||
            adapter.terminalActionId !== activity.expected_action_id ||
            !Number.isInteger(minimumDurationMs) || minimumDurationMs <= 0) {
            state.playError = 'play_feedback_unavailable';
            syncCatMindCompatibility('pet-care-play-feedback-unavailable');
            render();
            flushDeferredAccountResume('pet-care-play-feedback-unavailable');
            return false;
        }
        state.playError = '';
        state.playActivity = activity;
        state.optimisticSnapshot = applyOptimisticPlayPreview(contentId);
        armPlayActivityLease(activity);
        emit('neko:pet-care:play-activity-start', activity);
        syncCatMindCompatibility('pet-care-toy-play-started');
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
            started = requestPresentation.call(presentation, {
                source: activity.source,
                activityId: activity.activity_id,
                requestId: activity.action_request_id,
                runId: activity.action_run_id,
                minimumDurationMs: minimumDurationMs,
                onTerminal: function (result, detail) {
                    if (!state.playActivity || state.playActivity.activity_id !== activity.activity_id) return;
                    var terminalInput = typeof result === 'string'
                        ? result : result && (result.result || result.reason || result.status);
                    var terminal = playResponseTerminal(terminalInput);
                    if (!playTerminalIdentityMatches(
                        activity, detail || result, adapter.terminalSource)) return;
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
    function beginToyPlayActivity(contentId) {
        contentId = str(contentId);
        if (!contentId || contentId === 'yarn' || !careReady() ||
            state.recoveryPendingStatus || state.explorationPending) return false;
        var action = state.playActions && state.playActions[contentId];
        if (!action || typeof action !== 'object') return false;
        var adapter = playActionAdapter(contentId, action);
        if (!adapter || adapter.runner === 'cat-mind') {
            state.playError = 'play_feedback_unavailable'; render(); return false;
        }
        var quantity = Number(action.inventory_quantity);
        var consumeQuantity = actionConsumeQuantity(action);
        var dailyCount = Number(action.daily_count);
        var dailyLimit = Number(action.daily_limit);
        var recoveryPlay = isSulkRecoveryPlay(contentId, action);
        if (!playAllowedDuringSulk(contentId, action)) {
            state.playError = 'play_sulk_blocked'; render(); return false;
        }
        if (!Number.isInteger(consumeQuantity) || consumeQuantity < 1 ||
            !Number.isInteger(quantity) || quantity < consumeQuantity) {
            state.playError = 'play_inventory_insufficient'; render(); return false;
        }
        if (!recoveryPlay && cooldownRemainingSeconds('play', contentId) > 0) {
            state.playError = 'play_cooldown'; render(); return false;
        }
        if (!recoveryPlay && Number.isFinite(dailyLimit) && Number.isFinite(dailyCount) && dailyCount >= dailyLimit) {
            state.playError = 'play_daily_limit'; render(); return false;
        }
        var activity = stampPlayContext({
            activity_id: playActivityId({ activity_id: contentId + '-' + Date.now() }, 'toy'),
            content_id: contentId,
            source: adapter.source,
            expected_action_id: adapter.terminalActionId,
            action_request_id: playRuntimeIdentity('pet-care-request'),
            action_run_id: playRuntimeIdentity('pet-care-run'),
            user_initiated: true,
            started_at: Date.now(),
            observed_at: Date.now(),
        });
        var expected = state.generation;
        state.playPending = { phase: 'lease', activity_id: activity.activity_id,
            content_id: activity.content_id, activity: activity };
        state.playError = '';
        syncCatMindCompatibility('pet-care-play-lease-pending');
        render();
        requestPlayLease(activity).then(function (authorized) {
            if (expected !== state.generation || !state.playPending ||
                state.playPending.activity_id !== activity.activity_id) return;
            state.playPending = null;
            var confirmedAction = state.playActions && state.playActions[contentId];
            startToyPlayPresentation(authorized, confirmedAction);
        }, function (error) {
            if (expected !== state.generation || !state.playPending ||
                state.playPending.activity_id !== activity.activity_id) return;
            state.playPending = null;
            state.playError = error && error.message || 'play_lease_rejected';
            state.optimisticSnapshot = null;
            syncCatMindCompatibility('pet-care-play-lease-ended');
            render();
            flushDeferredAccountResume('play-lease-rejected');
        });
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
            petMutationBlocked() ||
            !isPetCareCompatibilityReady() || !playContextMatches(activity) ||
            terminalStatus !== 'done') return null;
        // A live terminal may only settle the exact activity and lease that
        // currently owns the presentation. Formal transport retries have no
        // live presentation and replay the saved activity below instead.
        if (state.playActivity &&
            (state.playActivity.activity_id !== activity.activity_id ||
                str(state.playActivity.lease_id) !== str(activity.lease_id))) return null;
        invalidateTaskReadForPlay();
        clearPlayActivityTimer();
        state.playActivity = null;
        state.optimisticSnapshot = null;
        var expected = state.generation;
        var contentId = str(activity.content_id) || 'yarn';
        var idempotencyKey = str(activity.completion_idempotency_key) ||
            actionIdempotencyKey();
        state.playPending = {
            activity_id: activity.activity_id, source: activity.source,
            idempotency_key: idempotencyKey, phase: 'complete', activity: activity,
        };
        state.playError = '';
        syncCatMindCompatibility('pet-care-play-completion-pending');
        render();
        var businessConfirmed = false;
        try {
            if (!playLeaseContractEnabled() || !str(activity.lease_id)) {
                throw new Error('play_lease_required');
            }
            var requestBody = {
                idempotency_key: idempotencyKey,
                expected_state_version: Number(activity.expected_state_version),
                activity_id: activity.activity_id,
                terminal_status: 'done',
            };
            var writeResult = await executeNewPendingWrite('play-lease-complete', requestBody, {
                lease_id: str(activity.lease_id),
            });
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (expected !== state.generation || !playContextMatches(activity)) return null;
            if (explorationActive(currentExploration())) {
                throw new Error('activity_in_progress');
            }
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            body = await hydrateSnapshotRequired(body, expected);
            var confirmed = snapshotFromActionBody(body);
            if (!response.ok || operationRejected(body)) {
                // A rejection may still carry the service's current counters
                // (for example a consumable daily limit). Accept that
                // read-only snapshot, but never treat the rejected play as a
                // successful settlement.
                if (confirmed && isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(confirmed);
                }
                throw new Error(operationFailureCode(body, 'play-action-rejected'));
            }
            if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) {
                throw new Error(str(body && (body.code || body.detail || body.reason)) ||
                    'play-action-rejected');
            }
            var confirmedOperation = operationResultFromBody(body);
            if (!confirmedOperation || str(confirmedOperation.activity_id) !== activity.activity_id) {
                throw new Error('play_activity_identity_mismatch');
            }
            installServiceSnapshot(confirmed);
            state.optimisticSnapshot = null;
            state.serviceStatus = 'ready'; state.serviceReason = 'play-action-confirmed';
            state.reason = 'play-action-confirmed'; state.playError = '';
            emit('neko:pet-care:play-confirmed', {
                activity_id: activity.activity_id, content_id: contentId, source: activity.source,
                terminal_status: 'done', operation: confirmedOperation || body.result || null,
                snapshot: confirmed, response_detail: sourceDetail || null,
            });
            return confirmed;
        } catch (error) {
            if (expected !== state.generation) return null;
            var playFailure = error && error.name === 'AbortError'
                ? 'play-action-timeout' : error && error.message || 'play-action-failed';
            state.playError = contentId !== 'yarn' && playFailure === 'inventory_insufficient'
                ? 'play_inventory_insufficient' : playFailure;
            if (error && error.petCarePendingWrite) state.pendingWriteError = state.playError;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
            emit('neko:pet-care:play-rollback', {
                activity_id: activity.activity_id, content_id: contentId, source: activity.source,
                reason: state.playError,
            });
            return null;
        } finally {
            if (expected === state.generation) {
                state.playPending = null;
                syncCatMindCompatibility('pet-care-play-completion-ended');
                render();
                flushDeferredAccountResume('play-completion-ended');
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
        if (type === 'playground_yarn_click_responded') {
            beginPlayActivity(observation, { confirmedResponse: true });
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
        var adapter = configuredPlayAdapter('yarn');
        if (!adapter || !result || typeof result !== 'object' ||
            result.source !== adapter.terminalSource ||
            str(result.actionId) !== adapter.terminalActionId) return;
        // Non-Cat-Mind toy feedback uses the action runner's explicit
        // onTerminal callback.  Only a yarn observation lease may consume
        // the shared Cat Mind result event.
        if (!state.playActivity || state.playActivity.content_id !== 'yarn') return;
        if (!playTerminalIdentityMatches(state.playActivity, result, adapter.terminalSource)) return;
        var terminal = playResponseTerminal(result.result || result.reason);
        if (terminal === 'done') settlePlayActivity(state.playActivity, terminal, result);
        else clearPlayActivity(terminal);
    }
    function petCareClient(characterUid) {
        var clientFactory = window.NekoPetCareClient;
        if (!clientFactory || typeof clientFactory.create !== 'function') {
            throw new Error('pet-care-client-unavailable');
        }
        return clientFactory.create({
            characterUid: characterUid == null ? state.currentCharacterUid : characterUid,
            timeoutMs: CARE_REQUEST_TIMEOUT_MS,
        });
    }
    function resumeDelayFromServer(body) {
        var serverTime = Date.parse(str(body && body.server_time));
        var notBefore = Date.parse(str(body && body.resume_not_before));
        if (!Number.isFinite(serverTime) || !Number.isFinite(notBefore)) return 0;
        // The service contract does not allow this hint to cross the next
        // service day.  Clamp a malformed/stale response as a second line of
        // defence so a hint can optimize traffic but never disable activity
        // reporting indefinitely.
        return Math.max(0, Math.min(24 * 60 * 60 * 1000, notBefore - serverTime));
    }
    async function refreshPetAfterResume(expectedFence) {
        if (!state.petRefreshRequired || petRefreshFence !== expectedFence ||
            expectedFence !== authFence() || state.authStatus !== 'logged-in' ||
            !state.currentCharacterUid || state.bindingPending) return null;
        return refreshBinding('session-resume-refresh');
    }
    function requestAccountResume(reason, options) {
        options = options && typeof options === 'object' ? options : {};
        if (!isPetCareOwnerSurface() || state.authStatus !== 'logged-in' || !state.accountId) {
            return Promise.resolve(null);
        }
        var requestedFence = authFence();
        if (accountResumePromise) {
            if (requestedFence !== accountResumeFence) {
                accountResumeTrailingFence = requestedFence;
                accountResumeTrailingReason = str(reason) || 'auth-generation-changed';
            }
            return accountResumePromise;
        }
        // A play lease is bound to the state version issued before the cat's
        // one-shot response. Advancing account settlement inside that window
        // would invalidate the lease completion CAS or discard the terminal
        // event. Resume immediately after the play window closes instead.
        if (playSettlementWindowActive() || petMutationIntentCount > 0) {
            deferAccountResume(requestedFence, reason);
            return Promise.resolve(null);
        }
        if (options.force !== true && accountResumeThrottleFence === requestedFence &&
            monotonicNow() < accountResumeNotBeforeMonotonic) {
            return Promise.resolve(null);
        }
        var requestAccountId = state.accountId;
        var observedRecord = currentPendingWrite(requestAccountId);
        // Account activity, binding, care, play, tasks and exploration share
        // one durable mutation lane. Never let resume race an unresolved pet
        // write; that write is reconciled first with its original identity.
        if (observedRecord && observedRecord.kind !== 'session-resume') return Promise.resolve(null);
        accountResumeFence = requestedFence;
        state.accountResumePending = true;
        // Do not rebuild the DOM here. A verified pointer interaction can
        // start account settlement immediately before the control's click;
        // replacing that control would swallow the same user intent. The
        // write entry waits on accountResumePromise and revalidates afterward.
        accountResumePromise = withPendingWriteAccountLock(requestAccountId, {
            // A new lifecycle event must not queue behind another renderer
            // and become a second write. Recovery of an already durable key
            // may wait, then re-read that exact operation under the lock.
            ifAvailable: !observedRecord,
        }, async function () {
            if (state.authStatus !== 'logged-in' || state.accountId !== requestAccountId) return null;
            var record = currentPendingWrite(requestAccountId);
            if (observedRecord) {
                if (!record || record.operation_id !== observedRecord.operation_id ||
                    record.kind !== 'session-resume') return null;
            } else {
                if (record || requestedFence !== authFence()) return null;
                record = registerPendingWrite('session-resume', {
                    idempotency_key: actionIdempotencyKey(),
                }, {
                    auth_fence: requestedFence,
                }, {
                    character_uid: '', bound_role_id: '', pet_id: '',
                });
            }
            var reconcilingEarlierAuth =
                str(record.target && record.target.auth_fence) !== requestedFence;
            var writeResult = await executePendingWrite(record);
            var response = writeResult.response;
            var body = writeResult.body;
            if (isAuthExpiredResponse(response, body)) {
                if (requestedFence === authFence()) await recoverFromAuthExpiry();
                return null;
            }
            if (writeResult.rejected || !response.ok) {
                state.pendingWriteError = operationFailureCode(body, 'pet_care_resume_rejected');
                return null;
            }
            if (requestedFence !== authFence() || state.authStatus !== 'logged-in') return body;
            if (reconcilingEarlierAuth) {
                // Replaying an unknown request from the preceding auth
                // generation only reconciles that old activity. Queue one
                // fresh key for the new authenticated lifecycle event.
                accountResumeTrailingFence = requestedFence;
                accountResumeTrailingReason = 'reauth-after-resume-reconcile';
            }
            var delay = resumeDelayFromServer(body);
            accountResumeThrottleFence = requestedFence;
            accountResumeNotBeforeMonotonic = monotonicNow() + delay;
            if (body && body.pet_refresh_required === true) {
                state.petRefreshRequired = true;
                if (state.petRefreshSource !== 'confirmed-write') state.petRefreshSource = 'resume';
                petRefreshFence = requestedFence;
                await refreshPetAfterResume(requestedFence);
            }
            state.pendingWriteError = '';
            return body;
        }).catch(function (error) {
            // Unknown outcome: keep the persisted key and the current pet
            // snapshot. A later verified activity or lifecycle event retries
            // the same request; the page never awards or settles locally.
            state.pendingWriteError = error && error.message || 'pet_care_resume_pending';
            return null;
        }).finally(function () {
            var trailingFence = accountResumeTrailingFence;
            var trailingReason = accountResumeTrailingReason;
            accountResumePromise = null;
            accountResumeFence = '';
            accountResumeTrailingFence = '';
            accountResumeTrailingReason = '';
            state.accountResumePending = false;
            render();
            if (trailingFence && trailingFence === authFence() &&
                state.authStatus === 'logged-in') {
                window.setTimeout(function () {
                    requestAccountResume(trailingReason || 'auth-generation-changed', { force: true });
                }, 0);
            }
        });
        return accountResumePromise;
    }
    function handleVerifiedUserInteraction(event) {
        var detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail : {};
        if (detail.phase !== 'waiting' || state.authStatus !== 'logged-in') return;
        requestAccountResume(event.type || 'verified-user-interaction');
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
        installServiceSnapshot(confirmed);
        state.optimisticSnapshot = null;
        // Only retain a reward carried by this service-confirmed envelope.
        // Start/refresh responses clear the previous terminal result so a new
        // activity cannot display stale loot.
        var confirmedReward = explorationRewardFromBody(body);
        state.explorationReward = confirmedReward != null ? confirmedReward : null;
        state.serviceStatus = 'ready'; state.serviceReason = reason || 'exploration-confirmed';
        state.reason = state.serviceReason;
        state.explorationError = '';
        if (!explorationActive(confirmed.exploration)) {
            state.explorationAutoCompleteRetryAt = 0;
            state.explorationAutoCompleteBlockedId = '';
        }
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
    async function startExploration(locationId) {
        return runPetMutationIntent(function () {
            return startExplorationAfterResume(locationId);
        });
    }
    async function startExplorationAfterResume(locationId) {
        // Exploration is a direct cat interaction. It may be restored while
        // the cat is away, but a new activity can only start from the active
        // cat form; callers cannot bypass this through the panel API.
        if (!authoritativeSnapshotReady() || !state.catLocalActive || explorationActive(currentExploration()) ||
            state.explorationPending || state.pendingAction || state.playActivity || state.playPending ||
            state.taskLoading || state.taskPendingId || petMutationBlocked() ||
            !state.currentCharacterUid) return null;
        var resolvedLocationId = str(locationId);
        if (!resolvedLocationId) {
            state.explorationError = 'exploration_location_required';
            render();
            return null;
        }
        if (!serviceCapabilityEnabled('config_revision_write_cas')) {
            state.explorationError = 'pet_care_config_revision_cas_unavailable';
            render();
            return null;
        }
        if (!writeConfigRevision()) {
            state.explorationError = 'pet_care_config_revision_required';
            render();
            await refreshBinding('pet-care-config-revision-missing');
            return null;
        }
        var request = ++explorationRequestGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.explorationPending = { operation: 'start', idempotency_key: idempotencyKey };
        state.explorationError = '';
        syncCatMindCompatibility('pet-care-exploration-start-pending');
        render();
        var businessConfirmed = false;
        try {
            var requestBody = withCurrentWriteVersions({
                idempotency_key: idempotencyKey,
                location_id: resolvedLocationId,
            });
            var writeResult = await executeNewPendingWrite('exploration-start', requestBody);
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            body = await hydrateSnapshotRequired(body, expected);
            if (!response.ok || operationRejected(body)) {
                var rejected = snapshotFromActionBody(body);
                if (rejected && isCompleteSnapshot(rejected, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(rejected);
                }
                throw new Error(operationFailureCode(body, 'exploration-start-rejected'));
            }
            if (!applyExplorationEnvelope(body, 'exploration-start-confirmed')) throw new Error('care-action-snapshot-incomplete');
            // CAT2/CAT3 are the same pet's long-idle forms. Starting an
            // explicit care/exploration interaction wakes the existing visual
            // tier to CAT1; it does not create another pet or a new form.
            wakeCat1ForCare({ category: 'exploration', location_id: resolvedLocationId,
                source: 'pet-care-exploration' });
            emit('neko:pet-care:exploration-started', {
                exploration_id: explorationId(), generation: explorationGeneration(),
                location_id: resolvedLocationId,
                snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            state.explorationError = error && error.name === 'AbortError'
                ? 'exploration-start-timeout' : error && error.message || 'exploration-start-failed';
            if (error && error.petCarePendingWrite) state.pendingWriteError = state.explorationError;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
            return null;
        } finally {
            if (request === explorationRequestGeneration) {
                state.explorationPending = null;
                // A confirmed active snapshot has installed the entity-wide
                // exploration gate before this transient presentation gate is
                // released. Failure returns directly to the idle Cat Mind
                // projection. Complete/recall waits never enter this gate.
                syncCatMindCompatibility('pet-care-exploration-start-ended');
                if (explorationActive(currentExploration())) scheduleExplorationLetterRefresh();
                render();
            }
        }
    }
    async function refreshExplorationOnce() {
        var exploration = currentExploration();
        if (state.authStatus !== 'logged-in' || state.bindingStatus !== 'bound' || !state.snapshot ||
            !state.currentCharacterUid || !explorationActive(exploration) || !explorationId(exploration) ||
            state.explorationPending || state.explorationRecallRequested) return null;
        var request = ++explorationRequestGeneration, expected = state.generation;
        var statusReadGeneration = ++explorationStatusReadGeneration;
        try {
            var response = await petCareClient().explorationStatus(
                state.currentCharacterUid, explorationId(exploration), {
                method: 'GET', credentials: 'same-origin', cache: 'no-store',
                headers: { Accept: 'application/json' },
            });
            var body = await response.json();
            if (request !== explorationRequestGeneration || expected !== state.generation ||
                statusReadGeneration !== explorationStatusReadGeneration) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) throw new Error(str(body && (body.code || body.detail || body.reason)) || 'exploration-load-failed');
            var refreshed = explorationFromBody(body);
            // The exploration status endpoint is intentionally a narrow,
            // read-only view. It advances server-timed letter visibility but
            // carries no pet-state snapshot. Merge only the same active
            // activity into the already authorised snapshot; a terminal or
            // different activity requires a full snapshot refresh so reward,
            // inventory and state versions stay atomic.
            if (!refreshed || explorationId(refreshed) !== explorationId(exploration) ||
                str(body && body.binding_status).toLowerCase() !== 'bound') {
                throw new Error('exploration-status-contract-invalid');
            }
            // A status read may carry a fresher service clock. It corrects the
            // local countdown anchor, while the response's next_letter_at is
            // the only value allowed to arm another one-shot read. Neither
            // value authorises a mutation or exposes a future letter.
            updateServerClockFromEnvelope(body);
            if (!explorationActive(refreshed)) {
                return await refreshBinding('exploration-terminal-refresh');
            }
            var mergedSnapshot = Object.assign({}, state.snapshot, { exploration: refreshed });
            if (!isCompleteSnapshot(mergedSnapshot, state.accountId, state.bindingRoleId)) {
                throw new Error('exploration-status-contract-invalid');
            }
            state.snapshot = mergedSnapshot;
            state.explorationReward = null;
            state.serviceStatus = 'ready';
            state.serviceReason = 'exploration-refresh-confirmed';
            state.reason = state.serviceReason;
            state.explorationError = '';
            reconcileExplorationFromSnapshot(mergedSnapshot, state.serviceReason);
            render();
            return state.snapshot;
        } catch (error) {
            if (request !== explorationRequestGeneration || expected !== state.generation ||
                statusReadGeneration !== explorationStatusReadGeneration) return null;
            state.explorationError = error && error.message || 'exploration-load-failed'; render(); return null;
        }
    }
    function refreshExploration() {
        var exploration = currentExploration();
        var readKey = String(state.generation) + ':' + explorationId(exploration);
        if (explorationStatusRead && explorationStatusRead.key === readKey) {
            return explorationStatusRead.promise;
        }
        var promise = refreshExplorationOnce();
        var record = { key: readKey, promise: promise };
        explorationStatusRead = record;
        var clear = function () {
            if (explorationStatusRead === record) explorationStatusRead = null;
        };
        promise.then(clear, clear);
        return promise;
    }
    async function settleExploration(operation, detail) {
        return runPetMutationIntent(function () {
            return settleExplorationAfterResume(operation, detail);
        });
    }
    async function settleExplorationAfterResume(operation, detail) {
        var exploration = currentExploration();
        if (!explorationActive(exploration) || state.explorationPending || petMutationBlocked() ||
            !state.currentCharacterUid) return null;
        var id = explorationId(exploration), expected = state.generation;
        if (!id) return null;
        var request = ++explorationRequestGeneration;
        clearExplorationLetterRefreshTimer(false);
        // One fresh user/automatic attempt gets one fresh key. The durable
        // pending-write registry owns exact-key replay only when the outcome is
        // unknown; reusing an already rejected key would replay that rejection
        // forever even after the exploration becomes due.
        var idempotencyKey = actionIdempotencyKey();
        state.explorationPending = { operation: operation, exploration_id: id, idempotency_key: idempotencyKey };
        state.explorationError = ''; render();
        var businessConfirmed = false;
        try {
            var requestBody = withExpectedStateVersion({ idempotency_key: idempotencyKey });
            var writeResult = await executeNewPendingWrite('exploration-' + operation, requestBody, {
                exploration_id: id,
            });
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (request !== explorationRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            updateServerClockFromEnvelope(body);
            body = await hydrateSnapshotRequired(body, expected);
            if (!response.ok || operationRejected(body)) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(
                    rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(rejectedSnapshot);
                    reconcileExplorationFromSnapshot(rejectedSnapshot,
                        'exploration-' + operation + '-rejected-refresh');
                }
                throw new Error(operationFailureCode(body,
                    'exploration-' + operation + '-rejected'));
            }
            if (!applyExplorationEnvelope(body, 'exploration-' + operation + '-confirmed', {
                suppressNotice: detail && detail.suppressNotice === true,
            })) {
                throw new Error('care-action-snapshot-incomplete');
            }
            var reward = explorationRewardFromBody(body);
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
            if (error && error.petCarePendingWrite) state.pendingWriteError = state.explorationError;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
            if (operation === 'complete') {
                var completionCode = str(state.explorationError).toLowerCase()
                    .replace(/[\s.\-]+/g, '_');
                if (error && error.petCarePendingWrite) {
                    // Unknown transport/server outcome keeps the exact durable
                    // key and performs one delayed reconciliation attempt.
                    state.explorationAutoCompleteBlockedId = '';
                    state.explorationAutoCompleteRetryAt = monotonicNow() + 30000;
                } else if (completionCode === 'exploration_not_complete' ||
                    completionCode === 'exploration_not_ready') {
                    // The service clock remains authoritative. Re-arm only this
                    // explicit early case and wait until the refreshed deadline
                    // (with a small floor for clock precision), using a new key.
                    var remaining = explorationRemainingMs(currentExploration());
                    state.explorationAutoCompleteBlockedId = '';
                    state.explorationAutoCompleteRetryAt = monotonicNow() +
                        Math.max(500, Number.isFinite(remaining) && remaining > 0 ? remaining : 500);
                } else {
                    // Every other parseable rejection is stable for automatic
                    // completion. Stop background retries for this activity;
                    // the visible nest remains the manual recovery entry.
                    state.explorationAutoCompleteRetryAt = 0;
                    state.explorationAutoCompleteBlockedId = id;
                    clearExplorationTimer({ keepLetterRefresh: true, keepStatusRead: true });
                }
            }
            if (operation === 'recall') {
                // A failed recall leaves the server activity active. Clear the
                // local waiting flag so the same unified entry can be retried
                // with the same activity-scoped idempotency contract.
                state.explorationRecallRequested = false;
            }
            if (error && error.petCarePendingWrite) scheduleTerminalExplorationReplay(30000);
            // A failed settle leaves the service-owned activity in place. The
            // user can retry from the nest; no unconfirmed reward is shown.
            return null;
        } finally {
            if (request === explorationRequestGeneration) {
                state.explorationPending = null; render();
                if (operation === 'complete' && explorationActive(currentExploration())) {
                    scheduleExplorationTimer();
                }
                if (explorationActive(currentExploration()) &&
                    !state.explorationRecallRequested) {
                    scheduleExplorationLetterRefresh();
                }
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
        var queueBehindComplete = state.explorationPending &&
            state.explorationPending.operation === 'complete';
        // Do not leave a local "waiting for return" intent when another
        // account mutation currently owns the lane.  The complete->recall
        // queue above remains valid because it belongs to one exploration.
        if (!queueBehindComplete && petMutationHardBlocked()) return false;
        // Invalidate an in-flight status read before the unified return event
        // starts settlement. A late GET must not clear the recall lease by
        // applying an older active snapshot.
        if (!state.explorationPending) explorationRequestGeneration += 1;
        state.explorationRecallRequested = true; state.explorationError = ''; render();
        clearExplorationLetterRefreshTimer(false);
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
    function operationResultFromBody(body) {
        if (!body || typeof body !== 'object') return null;
        if (body.operation_result && typeof body.operation_result === 'object') return body.operation_result;
        if (body.operation && typeof body.operation === 'object') return body.operation;
        if (body.result && body.result.operation_result &&
            typeof body.result.operation_result === 'object') return body.result.operation_result;
        if (body.result && body.result.operation && typeof body.result.operation === 'object') {
            return body.result.operation;
        }
        return null;
    }
    function operationRejected(body) {
        var operation = operationResultFromBody(body);
        return str(operation && operation.status).toLowerCase() === 'rejected';
    }
    function operationFailureCode(body, fallback) {
        var operation = operationResultFromBody(body);
        return str(operation && (operation.code || operation.reason || operation.detail)) ||
            str(body && (body.code || body.detail || body.reason)) || fallback;
    }
    function writeStateVersion() {
        var snapshotVersion = Number(state.snapshot && state.snapshot.state_version);
        if (Number.isInteger(snapshotVersion) && snapshotVersion >= 1) return snapshotVersion;
        var contextVersion = Number(state.petStateVersion);
        return Number.isInteger(contextVersion) && contextVersion >= 1 ? contextVersion : null;
    }
    function withExpectedStateVersion(payload) {
        var body = Object.assign({}, payload || {});
        var version = writeStateVersion();
        if (version != null) body.expected_state_version = version;
        return body;
    }
    function writeConfigRevision() {
        return str(state.clientProjection && state.clientProjection.config_revision);
    }
    function withCurrentConfigRevision(payload) {
        var body = Object.assign({}, payload || {});
        var revision = writeConfigRevision();
        if (revision) body.expected_config_revision = revision;
        return body;
    }
    function withCurrentWriteVersions(payload) {
        return withCurrentConfigRevision(withExpectedStateVersion(payload));
    }
    async function hydrateSnapshotRequired(body, expectedGeneration, options) {
        if (!body || typeof body !== 'object' || body.snapshot_required !== true ||
            snapshotFromActionBody(body)) return body;
        var response = await fetchPetCareSnapshot(state.currentCharacterUid);
        var refreshBody = {};
        try { refreshBody = await response.json(); } catch (_) {}
        if (expectedGeneration != null && expectedGeneration !== state.generation) return body;
        if (isAuthExpiredResponse(response, refreshBody)) {
            await recoverFromAuthExpiry();
            throw new Error('pet-care-auth-expired');
        }
        if (applyInvalidBindingEnvelope(refreshBody, expectedGeneration)) {
            throw new Error('role_not_bound');
        }
        if (!response.ok) {
            throw new Error(str(refreshBody && (refreshBody.code || refreshBody.detail || refreshBody.reason)) ||
                'pet-care-snapshot-refresh-failed');
        }
        var snapshot = snapshotFromActionBody(refreshBody);
        var expectedRole = options && options.binding === true
            ? str(snapshot && snapshot.bound_role_id) : state.bindingRoleId;
        if (!snapshot || !expectedRole || !isCompleteSnapshot(snapshot, state.accountId, expectedRole)) {
            throw new Error('pet-care-snapshot-refresh-incomplete');
        }
        body.snapshot = snapshot;
        body.snapshot_required = false;
        return body;
    }
    function normalizeBindingEnvelope(body) {
        if (!body || typeof body !== 'object') return body;
        var snapshot = snapshotFromActionBody(body);
        if (!str(body.binding_status || body.bindingStatus)) {
            if (body.needs_binding === true || body.needsBinding === true) body.binding_status = 'needs-binding';
            else if (snapshot) body.binding_status = 'bound';
        }
        if (str(body.binding_status || body.bindingStatus).toLowerCase() === 'ordinary-cat') {
            body.binding_status = 'unbound';
        }
        if (!str(body.account_id || body.accountId)) body.account_id = state.accountId;
        if (!str(body.current_character_uid || body.currentCharacterUid)) {
            body.current_character_uid = state.currentCharacterUid;
        }
        if (!snapshot) return body;
        if (!str(body.pet_id || body.petId)) body.pet_id = str(snapshot.pet_id);
        if (!str(body.bound_role_id || body.boundRoleId)) {
            body.bound_role_id = str(snapshot.bound_role_id);
        }
        if (body.binding_revision == null && body.bindingRevision == null &&
            snapshot.binding_revision != null) body.binding_revision = snapshot.binding_revision;
        if (body.state_version == null && body.stateVersion == null &&
            snapshot.state_version != null) body.state_version = snapshot.state_version;
        return body;
    }
    function applyInvalidBindingEnvelope(body, expectedGeneration) {
        if (!body || typeof body !== 'object' || Array.isArray(body)) return false;
        var explicitStatus = str(body.binding_status || body.bindingStatus);
        if (!explicitStatus) return false;
        var normalized = normalizeBindingEnvelope(body);
        var status = str(normalized.binding_status || normalized.bindingStatus).toLowerCase();
        if (status === 'needs_binding') status = 'needs-binding';
        if (status === 'bound') return false;
        // Business endpoints can discover that another official device has
        // rebound or retired this alias. Treat every explicit non-bound status
        // as an authoritative context transition instead of leaving the old
        // pet snapshot and task buttons visible behind an operation error.
        return applyBinding(normalized, expectedGeneration);
    }
    async function replayPendingWrite(options) {
        options = options && typeof options === 'object' ? options : {};
        if (state.pendingWriteRetrying || state.authStatus !== 'logged-in') return null;
        var record = currentPendingWrite();
        if (!record) return null;
        if (record.phase === 'quarantined') {
            state.pendingWriteError = str(record.quarantine_reason) || 'idempotency_key_reused';
            render();
            return null;
        }
        if (record.kind === 'session-resume') {
            return requestAccountResume('pending-write-retry', { force: true });
        }
        var expectedAccount = state.accountId;
        var expectedOperationId = record.operation_id;
        state.pendingWriteRetrying = true;
        state.pendingWriteError = '';
        render();
        var businessConfirmed = false;
        try {
            var lockedResult = await withPendingWriteAccountLock(expectedAccount, {}, async function () {
                if (state.authStatus !== 'logged-in' || state.accountId !== expectedAccount) return null;
                var lockedRecord = currentPendingWrite(expectedAccount);
                if (!lockedRecord || lockedRecord.operation_id !== expectedOperationId ||
                    lockedRecord.phase === 'quarantined') return null;
                return {
                    record: lockedRecord,
                    visibleContextAtStart: pendingWriteContextMatches(lockedRecord),
                    writeResult: await executePendingWrite(lockedRecord),
                };
            });
            if (!lockedResult) return null;
            record = lockedResult.record;
            var visibleContextAtStart = lockedResult.visibleContextAtStart;
            var writeResult = lockedResult.writeResult;
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (state.accountId !== expectedAccount) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            var visibleContextNow = visibleContextAtStart && pendingWriteContextMatches(record);
            if (!visibleContextNow) {
                // The exact old-role request still belongs to this verified
                // account and must reach a terminal service receipt. Never
                // install its snapshot into the currently displayed role.
                // Once reconciled, reload only the current context.
                var backgroundRejected = writeResult.rejected || !response.ok || operationRejected(body);
                state.pendingWriteError = backgroundRejected
                    ? operationFailureCode(body, 'pet_care_write_rejected') : '';
                emit('neko:pet-care:pending-write-reconciled', {
                    kind: record.kind,
                    operation_id: record.operation_id,
                    character_uid: record.character_uid,
                    status: backgroundRejected ? 'rejected' : 'confirmed',
                    automatic: options.automatic === true,
                });
                window.setTimeout(function () {
                    if (state.authStatus === 'logged-in' && state.accountId === expectedAccount) {
                        refreshBinding('background-write-reconciled');
                    }
                }, 0);
                return null;
            }
            if (applyInvalidBindingEnvelope(body, state.generation)) return null;
            if (writeResult.rejected || !response.ok || operationRejected(body)) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && state.bindingRoleId &&
                    isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(rejectedSnapshot);
                    reconcileExplorationFromSnapshot(rejectedSnapshot, 'pending-write-rejected');
                }
                state.pendingWriteError = operationFailureCode(body, 'pet_care_write_rejected');
                render();
                return null;
            }
            body = await hydrateSnapshotRequired(body, state.generation, {
                binding: record.kind === 'bind-create' || record.kind === 'bind-rebind',
            });
            if (record.kind === 'bind-create' || record.kind === 'bind-rebind') {
                var normalized = normalizeBindingEnvelope(body);
                if (record.kind === 'bind-rebind') {
                    var bindingSnapshot = snapshotFromActionBody(normalized);
                    var responsePetId = str(normalized.pet_id || normalized.petId ||
                        (bindingSnapshot && bindingSnapshot.pet_id));
                    var responseBindingRevision = Number(normalized.binding_revision != null
                        ? normalized.binding_revision : normalized.bindingRevision);
                    var responseStateVersion = Number(normalized.state_version != null
                        ? normalized.state_version : (normalized.stateVersion != null
                            ? normalized.stateVersion : bindingSnapshot && bindingSnapshot.state_version));
                    if (responsePetId !== record.body.expected_pet_id ||
                        !Number.isInteger(responseBindingRevision) ||
                        responseBindingRevision <= Number(record.body.expected_binding_revision) ||
                        !Number.isInteger(responseStateVersion) ||
                        responseStateVersion < Number(record.body.expected_state_version)) {
                        throw new Error('pet-care-rebind-contract-invalid');
                    }
                }
                if (!applyBinding(normalized, state.generation)) {
                    throw new Error('pet-care-bind-snapshot-incomplete');
                }
            } else if (record.kind.indexOf('exploration-') === 0) {
                var explorationRecoveryReason = record.kind === 'exploration-start'
                    ? 'exploration-start-recovery'
                    : 'pending-write-' + record.kind + '-confirmed';
                if (!applyExplorationEnvelope(body, explorationRecoveryReason, {
                    suppressNotice: options.suppressNotice === true,
                })) throw new Error('care-action-snapshot-incomplete');
            } else if (!applyConfirmedOperationEnvelope(body)) {
                throw new Error('care-action-snapshot-incomplete');
            }
            state.pendingWriteError = '';
            state.actionError = '';
            state.taskError = '';
            state.playError = '';
            state.explorationError = '';
            emit('neko:pet-care:pending-write-confirmed', {
                kind: record.kind, operation_id: record.operation_id,
                automatic: options.automatic === true,
            });
            return state.snapshot;
        } catch (error) {
            if (state.accountId !== expectedAccount) return null;
            state.pendingWriteError = error && error.name === 'AbortError'
                ? 'pet_care_pending_write_timeout'
                : error && error.message || 'pet_care_pending_write_failed';
            if (businessConfirmed) markConfirmedWriteRefreshRequired(expectedAccount);
            return null;
        } finally {
            if (state.accountId === expectedAccount) {
                state.pendingWriteRetrying = false;
                render();
                flushDeferredAccountResume('pending-play-write-reconciled');
            }
        }
    }
    function maybeReplayPendingWriteAfterContext() {
        var record = currentPendingWrite();
        if (!record || record.kind === 'session-resume' || record.phase === 'quarantined' ||
            state.pendingWriteRetrying) return;
        window.setTimeout(function () {
            if (!state.pendingWriteRetrying && state.authStatus === 'logged-in' &&
                currentPendingWrite()) replayPendingWrite({ automatic: true });
        }, 0);
    }
    function scheduleTerminalExplorationReplay(delayMs) {
        var timer = window.setTimeout(function () {
            var record = currentPendingWrite();
            if (record && (record.kind === 'exploration-complete' ||
                record.kind === 'exploration-recall') && pendingWriteContextMatches(record) &&
                !state.pendingWriteRetrying) replayPendingWrite({ automatic: true });
        }, Math.max(1000, Number(delayMs) || 30000));
        if (timer && typeof timer.unref === 'function') timer.unref();
    }
    function authoritativeSnapshotReady() {
        return state.authStatus === 'logged-in' && state.bindingStatus === 'bound' &&
            state.serviceStatus === 'ready' && !!state.snapshot && !!state.currentCharacterUid;
    }
    function taskData() {
        if (state.tasks && typeof state.tasks === 'object') return state.tasks;
        var snapshotTasks = state.snapshot && state.snapshot.tasks;
        return snapshotTasks && typeof snapshotTasks === 'object' ? snapshotTasks : null;
    }
    function hasTaskPayload(value) {
        return !!(value && typeof value === 'object' && !Array.isArray(value) &&
            Array.isArray(value.items));
    }
    function applyTaskPayload(snapshot) {
        var tasks = snapshot && snapshot.tasks;
        state.tasks = hasTaskPayload(tasks) ? tasks : null;
        if (!state.tasks || !state.expandedTaskId || !state.tasks.items.some(function (item, index) {
            return String(item && item.task_id || 'task-' + index) === state.expandedTaskId;
        })) state.expandedTaskId = '';
        return state.tasks;
    }
    function taskItems() {
        var data = taskData();
        return data && Array.isArray(data.items) ? data.items.slice() : [];
    }
    function taskIsReadyToClaim(item) {
        if (!item || item.claimable === false) return false;
        var status = str(item.status).toLowerCase();
        // The authoritative service exposes an unclaimed task whose condition
        // is satisfied as ``completed``. Any other state fails closed so an
        // old or malformed snapshot cannot create a second reward path.
        return status === 'completed';
    }
    function taskCompletionAction(item) {
        if (!item || str(item.status).toLowerCase() !== 'active' ||
            !serviceCapabilityEnabled('task_complete')) return null;
        var action = item.completion_action;
        if (!action || typeof action !== 'object' || Array.isArray(action)) return null;
        var id = str(action.id), label = str(action.label);
        return id && label ? {
            id: id,
            label: playerFacingLabel(label, id, '完成任务'),
        } : null;
    }
    function applyConfirmedOperationEnvelope(body, options) {
        if (applyInvalidBindingEnvelope(body, state.generation)) return false;
        var confirmed = snapshotFromActionBody(body);
        if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) return false;
        installServiceSnapshot(confirmed, options);
        state.optimisticSnapshot = null;
        reconcileExplorationFromSnapshot(confirmed, 'stage5-envelope');
        state.serviceStatus = 'ready'; state.serviceReason = 'stage5-confirmed';
        state.reason = 'stage5-confirmed';
        return true;
    }
    function taskErrorText(code) {
        return petCareUserFailureText(code, 'task');
    }
    function taskRewardLabel(itemId) {
        var normalized = String(itemId || '');
        var directoryLabel = itemDirectoryLabel(normalized);
        if (directoryLabel) return directoryLabel;
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
                        return playerFacingLabel(
                            category.label || category.display_name,
                            [categoryId, category.inventory_item],
                            '道具'
                        );
                    }
                    continue;
                }
                var contentIds = Object.keys(category);
                for (var contentIndex = 0; contentIndex < contentIds.length; contentIndex += 1) {
                    var contentId = contentIds[contentIndex];
                    var action = category[contentId];
                    if (!action || typeof action !== 'object') continue;
                    if (contentId === normalized || str(action.inventory_item) === normalized) {
                        return playerFacingLabel(
                            action.label || action.display_name,
                            [contentId, action.inventory_item],
                            '道具'
                        );
                    }
                }
            }
        }
        // Schema-v1 item ids are machine contracts. Keep this small fallback
        // vocabulary at the presentation boundary so sparse task/exploration
        // envelopes never expose those ids to users. Future unknown ids stay
        // generic until the service projects a display label.
        var schemaV1Labels = {
            small_fish: '小鱼干',
            teaser_wand: '逗猫棒',
            medicine: '药品',
        };
        return schemaV1Labels[normalized] || '道具';
    }
    var PET_CARE_ITEM_ASSETS = {
        small_fish: '/static/assets/neko-idle/pet-care-tool/eat/small-fish.png',
        brushing: '/static/assets/neko-idle/pet-care-tool/wash/wool-comb.png',
        teaser_wand: '/static/assets/neko-idle/pet-care-tool/play/cat-teaser.png',
    };
    function petCareItemAsset(itemId) {
        return PET_CARE_ITEM_ASSETS[String(itemId || '')] || '';
    }
    function inventoryPresentation(itemId, entry) {
        var label = itemDirectoryLabel(itemId) || (entry && typeof entry === 'object'
            ? str(entry.label || entry.name || entry.display_name) : '');
        label = playerFacingLabel(label, itemId, '');
        var icon = entry && typeof entry === 'object'
            ? str(entry.icon || entry.emoji || entry.glyph) : '';
        var visit = function (catalog) {
            if (!catalog || typeof catalog !== 'object' || (label && icon)) return;
            Object.keys(catalog).some(function (key) {
                var candidate = catalog[key];
                if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return false;
                var matches = key === itemId || str(candidate.inventory_item) === itemId;
                if (matches) {
                    label = label || playerFacingLabel(
                        candidate.label || candidate.display_name,
                        [key, candidate.inventory_item, itemId],
                        ''
                    );
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
        return {
            label: label || taskRewardLabel(itemId),
            icon: icon || '✦',
            description: itemDirectoryDescription(itemId),
            assetUrl: itemDirectoryAssetForItem(itemId) ||
                contentDirectoryAssetForItem(itemId) || petCareItemAsset(itemId),
        };
    }
    function appendActionItemImage(actionButton, contentId, action) {
        var itemId = str(action && action.inventory_item) || str(contentId);
        var presentation = inventoryPresentation(itemId, null);
        presentation.assetUrl = itemDirectoryAssetForItem(itemId) ||
            safePetCareImageUrl(action && action.asset_url) || presentation.assetUrl;
        presentation.description = itemDirectoryDescription(itemId) ||
            str(action && action.description) || presentation.description;
        if (!presentation.assetUrl) return presentation;
        var asset = document.createElement('img');
        asset.className = 'neko-pet-care-content-image';
        asset.src = presentation.assetUrl;
        asset.alt = '';
        asset.draggable = false;
        asset.setAttribute('aria-hidden', 'true');
        actionButton.className += ' neko-pet-care-content-action-has-image';
        presentation.image = asset;
        return presentation;
    }
    async function refreshTasks() {
        if (!authoritativeSnapshotReady() || state.pendingAction || state.playActivity || state.playPending ||
            state.explorationPending || state.taskLoading || state.taskPendingId ||
            petMutationBlocked() || !state.currentCharacterUid) return null;
        var request = ++taskRequestGeneration, expected = state.generation;
        state.taskLoading = true; state.taskError = ''; render();
        try {
            var response = await petCareClient().tasks(state.currentCharacterUid, {
                    method: 'GET', credentials: 'same-origin', cache: 'no-store',
                    headers: { Accept: 'application/json' },
                });
            var body = await response.json();
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (!response.ok) throw new Error(str(body && (body.code || body.detail || body.reason)) || 'task-load-failed');
            if (!applyConfirmedOperationEnvelope(body, { keepCurrentTaskRead: true })) {
                throw new Error('care-action-snapshot-incomplete');
            }
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
    async function mutateTask(taskId, operationKind) {
        return runPetMutationIntent(function () {
            return mutateTaskAfterResume(taskId, operationKind);
        });
    }
    async function mutateTaskAfterResume(taskId, operationKind) {
        taskId = str(taskId);
        var item = taskItems().find(function (candidate) {
            return candidate && String(candidate.task_id) === taskId;
        });
        var completing = operationKind === 'complete';
        var completionAction = completing ? taskCompletionAction(item) : null;
        var mutationAllowed = completing ? !!completionAction : taskIsReadyToClaim(item);
        if (!authoritativeSnapshotReady() || state.pendingAction || state.playActivity || state.playPending || state.taskLoading ||
            petMutationBlocked() || !taskId || !str(item && item.cycle_key) ||
            !mutationAllowed || state.taskPendingId) return null;
        var request = ++taskRequestGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.taskPendingId = taskId; state.taskPendingOperation = operationKind;
        state.taskError = ''; render();
        var businessConfirmed = false;
        try {
            var requestBody = withExpectedStateVersion({
                idempotency_key: idempotencyKey, cycle_key: str(item.cycle_key),
            });
            var writeResult = await executeNewPendingWrite(
                completing ? 'task-complete' : 'task-claim',
                requestBody, { task_id: taskId }
            );
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            if (applyInvalidBindingEnvelope(body, expected)) return null;
            body = await hydrateSnapshotRequired(body, expected);
            if (!response.ok || operationRejected(body)) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(rejectedSnapshot);
                }
                throw new Error(operationFailureCode(body,
                    completing ? 'task-complete-failed' : 'task-claim-failed'));
            }
            if (!applyConfirmedOperationEnvelope(body)) throw new Error('care-action-snapshot-incomplete');
            var confirmedOperation = operationResultFromBody(body) || {};
            emit(completing ? 'neko:pet-care:task-completed' : 'neko:pet-care:task-claimed',
                completing ? {
                    task_id: taskId, cycle_key: str(item.cycle_key),
                    completion_action_id: completionAction.id,
                    idempotency_key: idempotencyKey, snapshot: state.snapshot,
                } : {
                    task_id: taskId, cycle_key: str(item.cycle_key),
                    idempotency_key: idempotencyKey,
                    reward: confirmedOperation.reward || null, snapshot: state.snapshot,
                });
            return state.snapshot;
        } catch (error) {
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            state.taskError = error && error.name === 'AbortError'
                ? (completing ? 'task-complete-timeout' : 'task-claim-timeout')
                : error && error.message || (completing ? 'task-complete-failed' : 'task-claim-failed');
            if (error && error.petCarePendingWrite) state.pendingWriteError = state.taskError;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
            return null;
        } finally {
            if (request === taskRequestGeneration) {
                state.taskPendingId = ''; state.taskPendingOperation = ''; render();
            }
        }
    }
    function completeTask(taskId) { return mutateTask(taskId, 'complete'); }
    function claimTask(taskId) { return mutateTask(taskId, 'claim'); }
    async function useRecoveryItem(statusKey) {
        return runPetMutationIntent(function () {
            return useRecoveryItemAfterResume(statusKey);
        });
    }
    async function useRecoveryItemAfterResume(statusKey) {
        statusKey = str(statusKey).toLowerCase();
        var recoveryTask = taskItems().find(function (candidate) {
            return candidate && str(candidate.recovery_status).toLowerCase() === statusKey &&
                str(candidate.status).toLowerCase() === 'claimed';
        });
        var recoveryTaskId = str(recoveryTask && recoveryTask.task_id);
        var recoveryCycleKey = str(recoveryTask && recoveryTask.cycle_key);
        if (!authoritativeSnapshotReady() || state.pendingAction || state.playActivity || state.playPending ||
            state.taskLoading || state.recoveryPendingStatus ||
            petMutationBlocked() || !state.currentCharacterUid || statusKey !== 'sick' ||
            !recoveryTaskId || !recoveryCycleKey) return null;
        var request = ++taskRequestGeneration, expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.recoveryPendingStatus = statusKey; state.taskError = ''; render();
        var businessConfirmed = false;
        try {
            var requestBody = withExpectedStateVersion({
                status: statusKey, task_id: recoveryTaskId, cycle_key: recoveryCycleKey,
                idempotency_key: idempotencyKey,
            });
            var writeResult = await executeNewPendingWrite('status-recovery', requestBody);
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            body = await hydrateSnapshotRequired(body, expected);
            if (!response.ok || operationRejected(body)) {
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(rejectedSnapshot);
                }
                throw new Error(operationFailureCode(body, 'use-recovery-item-failed'));
            }
            if (!applyConfirmedOperationEnvelope(body)) throw new Error('care-action-snapshot-incomplete');
            emit('neko:pet-care:recovery-confirmed', {
                status: statusKey, idempotency_key: idempotencyKey, snapshot: state.snapshot,
            });
            return state.snapshot;
        } catch (error) {
            if (request !== taskRequestGeneration || expected !== state.generation) return null;
            state.taskError = error && error.name === 'AbortError'
                ? 'use-recovery-item-timeout' : error && error.message || 'use-recovery-item-failed';
            if (error && error.petCarePendingWrite) state.pendingWriteError = state.taskError;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
            return null;
        } finally {
            if (request === taskRequestGeneration) { state.recoveryPendingStatus = ''; render(); }
        }
    }
    async function performCareAction(category, contentId) {
        return runPetMutationIntent(function () {
            return performCareActionAfterResume(category, contentId);
        });
    }
    async function performCareActionAfterResume(category, contentId) {
        if (!careReady() || state.playActivity || state.playPending || !state.currentCharacterUid ||
            petMutationBlocked() ||
            !careContent(category).some(function (item) { return item.id === contentId; })) return null;
        var configuredAction = actionPreview(category, contentId);
        var inventoryItem = str(configuredAction && configuredAction.inventory_item);
        var consumeQuantity = actionConsumeQuantity(configuredAction);
        if (!Number.isFinite(consumeQuantity) ||
            (inventoryItem && inventoryCount(inventoryItem) < consumeQuantity)) {
            state.actionError = 'inventory_insufficient';
            render();
            return null;
        }
        if (!serviceCapabilityEnabled('config_revision_write_cas')) {
            state.actionError = 'pet_care_config_revision_cas_unavailable';
            render();
            return null;
        }
        if (!writeConfigRevision()) {
            state.actionError = 'pet_care_config_revision_required';
            render();
            await refreshBinding('pet-care-config-revision-missing');
            return null;
        }
        var request = ++actionRequestGeneration;
        var expected = state.generation;
        var idempotencyKey = actionIdempotencyKey();
        state.pendingAction = { category: category, contentId: contentId, idempotencyKey: idempotencyKey };
        state.actionError = '';
        syncCatMindCompatibility('pet-care-action-pending');
        // Q13 is intentionally optimistic for the visible interaction: keep
        // the durable snapshot untouched, but preview the immediate care
        // status change while the server request is in flight. Inventory,
        // progress, rewards, and other durable effects remain server-confirmed.
        state.optimisticSnapshot = applyOptimisticPreview(category, contentId);
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
        playCareFeedback(category, contentId, configuredAction);
        render();
        var businessConfirmed = false;
        try {
            var requestBody = withCurrentWriteVersions({
                category: category, content_id: contentId, idempotency_key: idempotencyKey,
            });
            var writeResult = await executeNewPendingWrite('care-action', requestBody);
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (request !== actionRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) return await recoverFromAuthExpiry();
            body = await hydrateSnapshotRequired(body, expected);
            var confirmed = snapshotFromActionBody(body);
            if (!response.ok || operationRejected(body)) {
                // A rejection may still carry the service's time-settled
                // snapshot. Accept that read-only settlement if it is a
                // complete snapshot, but never apply the rejected care
                // operation itself.
                var rejectedSnapshot = snapshotFromActionBody(body);
                if (rejectedSnapshot && isCompleteSnapshot(rejectedSnapshot, state.accountId, state.bindingRoleId)) {
                    installServiceSnapshot(rejectedSnapshot);
                }
                var failure = operationFailureCode(body, 'care-action-rejected');
                throw new Error(failure);
            }
            if (!confirmed || !isCompleteSnapshot(confirmed, state.accountId, state.bindingRoleId)) {
                throw new Error('care-action-snapshot-incomplete');
            }
            var operation = operationResultFromBody(body) || {};
            installServiceSnapshot(confirmed);
            state.optimisticSnapshot = null;
            state.serviceStatus = 'ready'; state.serviceReason = 'care-action-confirmed';
            state.reason = 'care-action-confirmed'; state.actionError = '';
            emit('neko:pet-care:action-confirmed', {
                category: category, content_id: contentId, idempotency_key: idempotencyKey,
                operation: operation, snapshot: confirmed,
            });
            return confirmed;
        } catch (error) {
            if (request !== actionRequestGeneration || expected !== state.generation) return null;
            state.actionError = error && error.name === 'AbortError'
                ? 'care-action-timeout' : error && error.message || 'care-action-failed';
            if (error && error.petCarePendingWrite) state.pendingWriteError = state.actionError;
            if (businessConfirmed) markConfirmedWriteRefreshRequired(state.accountId);
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
                syncCatMindCompatibility('pet-care-action-ended');
                render();
            }
        }
    }
    function explorationRemainingLabel(exploration) {
        var remaining = explorationRemainingMs(exploration);
        if (!Number.isFinite(remaining)) return '等待确认时间';
        var seconds = Math.max(0, Math.ceil(remaining / 1000));
        var hours = Math.floor(seconds / 3600); seconds %= 3600;
        var minutes = Math.floor(seconds / 60); seconds %= 60;
        return hours ? hours + '小时' + String(minutes).padStart(2, '0') + '分' : minutes + '分' + String(seconds).padStart(2, '0') + '秒';
    }
    function explorationText(value, fallback) {
        if (typeof value === 'string' && value.trim()) return value.trim();
        if (value && typeof value === 'object') return str(value.text || value.body || value.message || value.content || value.title) || fallback;
        return fallback;
    }
    function createExplorationLetterRow(letter, location, options) {
        options = options && typeof options === 'object' ? options : {};
        var row = document.createElement('article');
        row.className = 'neko-pet-care-letter-row';
        if (options.letterStyle) row.dataset.letterStyle = str(options.letterStyle);
        var heading = document.createElement('strong');
        heading.textContent = str(letter && (letter.title || letter.name)) || '来信';
        var body = document.createElement('span');
        body.textContent = explorationText(letter, '有一封新的来信。');
        var imageUrl = safePetCareImageUrl(letter && (letter.image_url || letter.imageUrl) ||
            location && (location.letter_image_url || location.letterImageUrl));
        if (imageUrl) {
            var image = document.createElement('img');
            image.className = 'neko-pet-care-letter-thumbnail';
            image.alt = heading.textContent + '的探索地图片';
            image.loading = 'lazy'; image.decoding = 'async'; image.src = imageUrl;
            row.appendChild(image);
        }
        var copy = document.createElement('span'); copy.className = 'neko-pet-care-letter-copy';
        copy.appendChild(heading); copy.appendChild(body); row.appendChild(copy);
        return row;
    }
    function safePetCareImageUrl(value) {
        var raw = typeof value === 'string' ? value : '';
        if (!raw || raw.length > 2048 || raw !== raw.trim() ||
            /[\u0000-\u001f\u007f\\%?#]/.test(raw)) return '';
        try {
            var base = new URL(window.location && window.location.href
                ? window.location.href : 'http://127.0.0.1/');
            var parsed = new URL(raw, base);
            if (raw.charAt(0) !== '/' || raw.charAt(1) === '/' || raw.indexOf('//', 1) >= 0 ||
                parsed.username || parsed.password || parsed.search || parsed.hash ||
                (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') ||
                parsed.origin !== base.origin || parsed.pathname !== raw ||
                parsed.pathname === '/static/assets/neko-idle/' ||
                parsed.pathname.indexOf('/static/assets/neko-idle/') !== 0) return '';
            return parsed.pathname;
        } catch (_) {}
        return '';
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
    function availableServiceLetters() {
        var snapshotLetters = state.snapshot && (state.snapshot.letters || state.snapshot.inbox);
        return Array.isArray(snapshotLetters) ? snapshotLetters : explorationLetters(currentExploration());
    }
    function letterMailboxAvailable() {
        return !!contentDirectoryEntry('exploration_mailbox', true);
    }
    function explorationRewardText(reward) {
        if (reward == null) return '';
        var parts = [];
        var drops = Array.isArray(reward) ? reward : reward && typeof reward === 'object' &&
            (reward.drops || reward.items || reward.rewards);
        if (Array.isArray(drops)) drops.forEach(function (drop) {
            if (!drop || typeof drop !== 'object') return;
            var quantity = Number(drop.quantity != null ? drop.quantity : drop.count);
            var itemId = drop.item_id || drop.id;
            var explicitName = playerFacingLabel(
                drop.name || drop.label || drop.display_name,
                itemId,
                ''
            );
            var name = explicitName || taskRewardLabel(itemId);
            if (name) parts.push(name + (Number.isFinite(quantity) && quantity > 0 ? ' × ' + quantity : ''));
        });
        var progress = Number(reward && typeof reward === 'object' ? reward.progress_delta : NaN);
        if (Number.isFinite(progress) && progress !== 0) parts.push('养成进度 +' + progress);
        // Older confirmed envelopes may expose a single item id instead of
        // the canonical drops array. Keep that compatibility at the
        // presentation boundary, but never render the machine id itself.
        if (!parts.length && typeof reward === 'string') return taskRewardLabel(reward);
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
        var location = exploration ? explorationLocation(exploration) : defaultExplorationLocation();
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
            var arrivedLetters = explorationLetters(exploration);
            if (arrivedLetters.length) {
                var arrived = document.createElement('section');
                arrived.className = 'neko-pet-care-exploration-letters';
                var arrivedTitle = document.createElement('strong');
                arrivedTitle.className = 'neko-pet-care-exploration-letters-title';
                arrivedTitle.textContent = '新来信'; arrived.appendChild(arrivedTitle);
                arrivedLetters.slice(-3).forEach(function (letter) {
                    arrived.appendChild(createExplorationLetterRow(letter, location));
                });
                container.appendChild(arrived);
            }
            var recall = button(state.explorationRecallRequested ? '等待返回…' : '召回猫咪', 'neko-pet-care-exploration-recall');
            recall.disabled = !!pending || state.explorationRecallRequested || petMutationHardBlocked();
            recall.setAttribute('aria-disabled', recall.disabled ? 'true' : 'false');
            recall.title = recall.disabled ? '正在处理召回' : '结束探索并让猫回到猫形态';
            recall.dataset.explorationAction = 'recall'; container.appendChild(recall);
            if (explorationDueByServerClock(exploration) && !state.explorationRecallRequested) {
                var retryComplete = button(pending && pending.operation === 'complete' ? '结算中…' : '重试完成结算', 'neko-pet-care-exploration-complete');
                retryComplete.disabled = !!pending || petMutationHardBlocked();
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
            start.disabled = !!pending || !authoritativeSnapshotReady() || sickActive || petMutationHardBlocked();
            start.setAttribute('aria-disabled', start.disabled ? 'true' : 'false');
            start.title = start.disabled && sickActive ? '生病期间不能探索' :
                (start.disabled ? '当前不可用' : '开始探索');
            start.dataset.explorationAction = 'start'; container.appendChild(start);
        }
        if (state.explorationError) {
            var error = document.createElement('p'); error.className = 'neko-pet-care-exploration-error';
            error.textContent = careErrorText(state.explorationError, 'exploration'); container.appendChild(error);
        }
    }
    function progressDirectoryEntries() {
        var snapshot = state.snapshot;
        var directory = snapshot && snapshot.content_directory;
        var unlocks = snapshot && Array.isArray(snapshot.unlocks) ? snapshot.unlocks : [];
        if (!directory || typeof directory !== 'object' || Array.isArray(directory)) return [];
        var unlockedIds = Object.create(null);
        unlocks.forEach(function (unlock) {
            var unlockId = str(unlock && typeof unlock === 'object'
                ? (unlock.id || unlock.unlock_id) : unlock);
            if (unlockId) unlockedIds[unlockId] = true;
        });
        var seen = Object.create(null);
        return Object.keys(directory).map(function (capability, index) {
            var entry = directory[capability];
            if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
            var unlockId = str(entry.unlock_id);
            if (!unlockId || seen[unlockId]) return null;
            seen[unlockId] = true;
            var rawThreshold = Number(entry.threshold);
            var threshold = entry.threshold != null && Number.isInteger(rawThreshold) && rawThreshold >= 0
                ? rawThreshold : null;
            return {
                id: unlockId,
                name: playerFacingLabel(
                    entry.display_name || entry.name || entry.label,
                    unlockId,
                    '解锁内容'
                ),
                threshold: threshold,
                unlocked: entry.unlocked === true || !!unlockedIds[unlockId],
                order: index,
            };
        }).filter(Boolean);
    }
    function progressPresentation() {
        var current = Number(value('progress'));
        var milestones = progressDirectoryEntries().filter(function (entry) {
            return entry.threshold !== null;
        }).sort(function (left, right) {
            return left.threshold - right.threshold || left.order - right.order;
        });
        if (!Number.isFinite(current) || !milestones.length) {
            return { kind: 'missing', current: Number.isFinite(current) ? current : null,
                next: null, percent: null };
        }
        var next = milestones.find(function (entry) { return !entry.unlocked; }) || null;
        if (!next) return { kind: 'complete', current: current, next: null, percent: 100 };
        var percent = next.threshold > 0
            ? Math.max(0, Math.min(100, current / next.threshold * 100)) : 100;
        return { kind: 'next', current: current, next: next, percent: percent };
    }
    function progressUnlockedEntries() {
        var snapshot = state.snapshot;
        var unlocks = snapshot && Array.isArray(snapshot.unlocks) ? snapshot.unlocks : [];
        var directoryEntries = progressDirectoryEntries();
        var entries = directoryEntries.filter(function (entry) { return entry.unlocked; });
        var directoryCount = directoryEntries.length;
        var seen = Object.create(null);
        entries.forEach(function (entry) { seen[entry.id] = true; });
        unlocks.forEach(function (unlock, index) {
            var unlockId = str(unlock && typeof unlock === 'object'
                ? (unlock.id || unlock.unlock_id) : unlock);
            if (!unlockId || seen[unlockId]) return;
            seen[unlockId] = true;
            var unlockThreshold = unlock && typeof unlock === 'object' ? unlock.threshold : null;
            var rawThreshold = Number(unlockThreshold);
            entries.push({
                id: unlockId,
                name: playerFacingLabel(
                    unlock && typeof unlock === 'object' ? (unlock.name || unlock.label) : '',
                    unlockId,
                    '解锁内容'
                ),
                threshold: unlockThreshold != null && Number.isInteger(rawThreshold) && rawThreshold >= 0
                    ? rawThreshold : null,
                unlocked: true,
                order: directoryCount + index,
            });
        });
        return entries.sort(function (left, right) {
            if (left.threshold !== null && right.threshold !== null && left.threshold !== right.threshold) {
                return left.threshold - right.threshold;
            }
            return left.order - right.order;
        });
    }
    function renderProgressUnlockRow(panel, unlock) {
        var row = document.createElement('div');
        row.className = 'neko-pet-care-progress-unlock-row';
        var name = document.createElement('span'); name.textContent = unlock.name;
        var meta = document.createElement('span');
        meta.textContent = unlock.threshold === null ? '已解锁' : unlock.threshold + ' 点 ✓';
        row.appendChild(name); row.appendChild(meta); panel.appendChild(row);
    }
    function renderProgressUnlocks() {
        if (!ui || !ui.progressUnlockPanel) return;
        var panel = ui.progressUnlockPanel;
        if (typeof panel.replaceChildren === 'function') panel.replaceChildren();
        else { panel.children = []; panel.textContent = ''; }
        panel.hidden = !state.progressUnlockOpen;
        if (!state.progressUnlockOpen) return;
        var unlocks = progressUnlockedEntries().slice().reverse();
        if (!unlocks.length) {
            state.progressUnlockAllOpen = false;
            var empty = document.createElement('span');
            empty.className = 'neko-pet-care-progress-unlock-empty';
            empty.textContent = '暂无已解锁内容';
            panel.appendChild(empty);
            return;
        }
        if (unlocks.length <= 2) state.progressUnlockAllOpen = false;
        var head = document.createElement('div');
        head.className = 'neko-pet-care-progress-unlock-head';
        var title = document.createElement('strong'); title.textContent = '已解锁内容';
        var count = document.createElement('span'); count.textContent = unlocks.length + ' 项';
        head.appendChild(title); head.appendChild(count); panel.appendChild(head);
        var visibleUnlocks = state.progressUnlockAllOpen ? unlocks : unlocks.slice(0, 2);
        visibleUnlocks.forEach(function (unlock) {
            renderProgressUnlockRow(panel, unlock);
        });
        if (unlocks.length <= 2) return;
        var expand = button(state.progressUnlockAllOpen ? '收起完整列表' : '展开全部 ' + unlocks.length + ' 项',
            'neko-pet-care-progress-unlock-toggle');
        var chevron = document.createElement('span');
        chevron.className = 'neko-pet-care-progress-unlock-chevron';
        chevron.setAttribute('aria-hidden', 'true'); chevron.textContent = '›';
        expand.appendChild(chevron);
        expand.setAttribute('aria-expanded', state.progressUnlockAllOpen ? 'true' : 'false');
        expand.addEventListener('click', function (event) {
            if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
            state.progressUnlockAllOpen = !state.progressUnlockAllOpen;
            render();
        });
        panel.appendChild(expand);
    }
    function render() {
        syncCat1PettingCapability();
        if (!ui) return;
        var status = state.authStatus, binding = state.bindingStatus;
        var pendingWrite = pendingWriteSummary();
        var showLoginHint = state.loginHintVisible && state.catLocalActive &&
            status === 'logged-out' && !state.oauthPending;
        ui.loginHint.hidden = !showLoginHint;
        ui.root.dataset.loginHint = showLoginHint ? 'true' : 'false';
        var account = playerFacingLabel(
            state.user && (state.user.display_name || state.user.email),
            state.accountId,
            '已连接'
        );
        var authView = status === 'logged-out' || state.oauthPending;
        ui.closeButton.setAttribute('aria-label', authView ? '关闭登录' : '关闭养宠面板');
        ui.closeButton.setAttribute('title', authView ? '关闭登录' : '关闭');
        // Exploration uses the shared desktop nest as the same semantic
        // hover anchor, so every expanded pet-care view has a close control.
        ui.closeButton.hidden = !state.panelOpen;
        ui.root.dataset.authView = authView ? 'true' : 'false';
        ui.root.dataset.status = status; ui.root.dataset.bindingStatus = binding;
        ui.root.dataset.serviceStatus = state.serviceStatus;
        ui.root.dataset.degraded = status === 'logged-in' &&
            (state.serviceStatus === 'unavailable' || state.serviceStatus === 'auth-refreshing') &&
            !explorationActive(currentExploration()) ? 'true' : 'false';
        ui.root.dataset.panelOpen = state.panelOpen ? 'true' : 'false';
        ui.root.dataset.expanded = state.panelOpen ? 'true' : 'false';
        ui.root.dataset.view = state.selectedView || 'overview';
        ui.panel.dataset.view = state.selectedView || 'overview';
        // This label is the demo's surface chrome, not a second launcher.
        // Account/binding state remains in the small status text at the right.
        var authVerificationUnavailable = status === 'unknown' ||
            state.serviceStatus === 'auth-refreshing' ||
            state.serviceReason === 'oauth-status-unavailable';
        var serviceText = status === 'logged-out' ? '' :
            authVerificationUnavailable
                ? (state.serviceStatus === 'auth-refreshing' ||
                    (degradedSurfaceRecoveryPromise && degradedSurfaceNeedsAuthRefresh())
                    ? '正在重新确认账号状态…'
                    : '账号状态暂时无法确认，请重新检查。') :
            binding === 'needs-binding' ? '这是账号首次使用养宠，请确认绑定当前角色。' :
            binding === 'unbound' ? '当前角色不是账号绑定角色，继续使用普通猫。' :
            binding === 'bound' && !state.snapshot ? '角色已确认，但养宠状态暂不可用。' :
            state.serviceStatus === 'unavailable' ? '养宠功能暂时不可用，猫的普通行为不受影响。' :
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
        if (pendingWrite) {
            serviceText = pendingWrite.quarantined
                ? '上次操作状态有冲突，暂时无法重试。'
                : (pendingWrite.kind === 'session-resume'
                    ? '账号活动结果待确认，请重试。'
                    : pendingWrite.character_uid !== state.currentCharacterUid
                    ? '上次操作结果待确认，请切回发起操作的角色后重试。'
                    : '上次操作结果待确认，请重试。');
        } else if (state.petRefreshRequired) {
            serviceText = '操作已完成，正在同步最新养宠状态。';
        }
        ui.service.textContent = serviceText;
        ui.service.hidden = !serviceText;
        var mailboxContent = contentDirectoryEntry('exploration_mailbox', true);
        var availableLetters = availableServiceLetters();
        var mailboxAvailable = !!mailboxContent;
        var view = state.selectedView || 'overview';
        if (view === 'letters' && !mailboxAvailable) {
            view = 'overview';
            state.selectedView = view;
        }
        var activeExplorationAvailable = !!state.snapshot && explorationActive(currentExploration());
        var loggedInSurface = status === 'logged-in' && binding === 'bound' && !!state.snapshot &&
            (state.serviceStatus === 'ready' || activeExplorationAvailable);
        if (!loggedInSurface && serviceText && view !== 'overview') {
            // A degraded surface must never hide its own explanation behind a
            // now-disabled module. Return to the status view while preserving
            // fail-closed navigation and ordinary-cat behaviour.
            view = 'overview';
            state.selectedView = view;
            ui.root.dataset.view = view;
            ui.panel.dataset.view = view;
        }
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
            var nestAsset = safePetCareImageUrl(exploration &&
                (exploration.asset_url || exploration.assetUrl) ||
                location && (location.asset_url || location.assetUrl)) ||
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
            if (item.dataset.view === 'letters') item.hidden = !mailboxAvailable;
            if (item.classList && typeof item.classList.toggle === 'function') {
                item.classList.toggle('is-active', active);
            }
            item.setAttribute('aria-current', active ? 'page' : 'false');
            item.disabled = !loggedInSurface;
        });
        ui.overview.hidden = view !== 'overview' || (!loggedInSurface && !serviceText);
        ui.lettersModule.hidden = !loggedInSurface || view !== 'letters' || !mailboxAvailable;
        ui.lettersModule.dataset.letterStyle = mailboxContent
            ? str(mailboxContent.first_letter_style) : '';
        ui.inventoryModule.hidden = !loggedInSurface || view !== 'inventory';
        ui.profileModule.hidden = !loggedInSurface || view !== 'profile';
        // Unknown auth is recoverable: keep the login path visible until the
        // shared account authority confirms a logged-in session again.
        ui.loginButton.hidden = status === 'logged-in';
        ui.loginButton.disabled = state.oauthPending;
        ui.loginButton.textContent = state.oauthPending ? '登录中…' : '登录 N.E.K.O 账号';
        var firstBindAvailable = status === 'logged-in' && binding === 'needs-binding';
        var rebindAvailable = status === 'logged-in' && binding === 'unbound' && state.canRebind === true;
        ui.bindButton.hidden = !(firstBindAvailable || rebindAvailable);
        ui.bindButton.textContent = rebindAvailable ? '换绑到当前角色' : '绑定当前角色';
        ui.bindButton.disabled = petMutationHardBlocked();
        ui.retryButton.hidden = !(pendingWrite || state.petRefreshRequired ||
            status !== 'logged-in' || binding === 'unknown' ||
            state.serviceStatus === 'unavailable' || state.serviceStatus === 'auth-refreshing');
        ui.retryButton.textContent = pendingWrite
            ? (state.pendingWriteRetrying ? '确认中…' : pendingWrite.quarantined ? '暂时无法重试' : '重试确认上次操作')
            : (degradedSurfaceRecoveryPromise ? '重新确认中…' : '重新检查');
        ui.retryButton.disabled = !!(pendingWrite && (state.pendingWriteRetrying ||
            pendingWrite.quarantined || state.accountResumePending)) ||
            !!degradedSurfaceRecoveryPromise || state.bindingPending;
        ui.logoutButton.hidden = status !== 'logged-in';
        var authoritativeAvailable = authoritativeSnapshotReady();
        ui.task.hidden = !loggedInSurface || view !== 'tasks' || !authoritativeAvailable;
        // Account controls belong to the profile/auth slot. Keeping the
        // footer out of the overview prevents a logout button from appearing
        // next to inventory/task previews and preserves the demo hierarchy.
        ui.footer.hidden = pendingWrite || state.petRefreshRequired
            ? false : (loggedInSurface ? view !== 'profile' : false);
        if (!authoritativeAvailable) {
            state.expandedTaskId = '';
            ui.taskList.replaceChildren ? ui.taskList.replaceChildren() : (ui.taskList.children = []);
            ui.taskError.hidden = true; ui.taskError.textContent = '';
        } else {
            var items = taskItems();
            if (ui.taskList.replaceChildren) ui.taskList.replaceChildren();
            else { ui.taskList.children = []; ui.taskList.textContent = ''; }
            items.forEach(function (item, taskIndex) {
                    if (!item || typeof item !== 'object') return;
                    var row = document.createElement('article'); row.className = 'neko-pet-care-task-item';
                    var tick = document.createElement('span'); tick.className = 'neko-pet-care-task-tick';
                    var taskStatus = str(item.status).toLowerCase();
                    var taskCompletedWithoutClaim = taskStatus === 'completed' && item.claimable === false;
                    tick.dataset.status = taskCompletedWithoutClaim ? 'done' : (taskStatus || 'available');
                    tick.setAttribute('aria-hidden', 'true');
                    tick.textContent = taskCompletedWithoutClaim || taskStatus === 'claimed' ? '✓' : '';
                    var copy = document.createElement('div'); copy.className = 'neko-pet-care-task-copy';
                    var taskKey = String(item.task_id || '');
                    var taskPresentationKey = taskKey || 'task-' + taskIndex;
                    var name = document.createElement('strong');
                    name.textContent = playerFacingLabel(item.title, taskKey, '任务');
                    var descriptionText = str(item.description);
                    copy.appendChild(name);
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
                    var hasVisibleProgress = Number.isFinite(progress) && Number.isFinite(target) && target > 0;
                    var progressText = null;
                    var progressSummary = null;
                    var progressPercent = 0;
                    if (hasVisibleProgress) {
                        var displayProgress = Math.max(0, Math.floor(progress));
                        var displayTarget = Math.max(1, Math.floor(target));
                        progressText = document.createElement('span');
                        progressText.className = 'neko-pet-care-task-progress-value';
                        progressText.textContent = displayTarget === 1
                            ? (displayProgress >= displayTarget ? '已完成' : '待完成')
                            : displayProgress + '/' + displayTarget;
                        progressSummary = document.createElement('span');
                        progressSummary.className = 'neko-pet-care-task-progress-summary';
                        progressSummary.textContent = taskStatus === 'completed' || taskStatus === 'claimed'
                            ? '已完成' : progressText.textContent;
                        copy.appendChild(progressSummary);
                        progressPercent = Math.max(0, Math.min(100, displayProgress / displayTarget * 100));
                    }
                    var taskAction = null;
                    var recoveryStatus = str(item.recovery_status).toLowerCase();
                    var completionAction = taskCompletionAction(item);
                    if (completionAction) {
                        taskAction = button(
                            state.taskPendingId === taskKey && state.taskPendingOperation === 'complete'
                                ? '完成中…' : completionAction.label,
                            'neko-pet-care-task-claim neko-pet-care-task-complete');
                        taskAction.dataset.taskId = taskKey;
                        taskAction.dataset.taskAction = 'complete';
                        taskAction.dataset.completionActionId = completionAction.id;
                        var canComplete = state.taskPendingId !== taskKey && !state.taskPendingId &&
                            !state.taskLoading && !state.pendingAction && !state.playActivity &&
                            !state.playPending && !petMutationHardBlocked();
                        taskAction.disabled = !canComplete;
                        taskAction.setAttribute('aria-disabled', canComplete ? 'false' : 'true');
                    } else if (item.claimable !== false) {
                        var readyToClaim = taskIsReadyToClaim(item);
                        taskAction = button(item.status === 'claimed' ? '已领取' :
                            state.taskPendingId === taskKey && state.taskPendingOperation === 'claim' ? '领取中…' :
                                readyToClaim ? '领取' : '未完成', 'neko-pet-care-task-claim');
                        taskAction.dataset.taskId = taskKey;
                        taskAction.dataset.taskAction = 'claim';
                        var canClaim = readyToClaim &&
                            state.taskPendingId !== taskKey && !state.taskPendingId && !state.taskLoading &&
                            !state.pendingAction && !state.playActivity && !state.playPending &&
                            !petMutationHardBlocked();
                        taskAction.disabled = !canClaim;
                        taskAction.setAttribute('aria-disabled', canClaim ? 'false' : 'true');
                    }
                    var recovery = null;
                    // Medicine is the only direct recovery operation. A sulk
                    // task grants the configured teaser wand; the status is
                    // cleared only after its leased PlayTeaserWandAction
                    // reaches the service-confirmed done terminal.
                    if (recoveryStatus === 'sick' && item.status === 'claimed') {
                        recovery = button(state.recoveryPendingStatus === recoveryStatus ? '使用中…' : '使用', 'neko-pet-care-task-recovery');
                        recovery.dataset.taskId = taskKey;
                        recovery.dataset.recoveryStatus = recoveryStatus;
                        var activeState = state.snapshot && state.snapshot.negative_status && state.snapshot.negative_status[recoveryStatus];
                        var canUse = !!(activeState && activeState.active) && inventoryCount(str(item.recovery_item_id || reward.item_id)) > 0 &&
                            state.recoveryPendingStatus !== recoveryStatus && !state.taskPendingId && !state.taskLoading &&
                            !state.pendingAction && !state.playActivity && !state.playPending &&
                            !petMutationHardBlocked();
                        recovery.disabled = !canUse;
                        recovery.setAttribute('aria-disabled', canUse ? 'false' : 'true');
                        recovery.title = canUse ? '使用恢复物品' : '当前还不能使用';
                    }
                    var hasDetails = !!descriptionText || !!progressText;
                    var expanded = hasDetails && state.expandedTaskId === taskPresentationKey;
                    var details = null;
                    var toggle = null;
                    if (hasDetails) {
                        var detailsId = 'neko-pet-care-task-details-' + taskIndex;
                        details = document.createElement('div'); details.className = 'neko-pet-care-task-details';
                        details.id = detailsId;
                        details.hidden = !expanded;
                        if (descriptionText) {
                            var description = document.createElement('p');
                            description.className = 'neko-pet-care-task-description';
                            description.textContent = descriptionText;
                            details.appendChild(description);
                        }
                        if (progressText) {
                            var progressBlock = document.createElement('div'); progressBlock.className = 'neko-pet-care-task-progress';
                            var progressHeader = document.createElement('div'); progressHeader.className = 'neko-pet-care-task-progress-header';
                            var progressLabel = document.createElement('span'); progressLabel.textContent = '任务进度';
                            progressHeader.appendChild(progressLabel); progressHeader.appendChild(progressText);
                            var progressTrack = document.createElement('span'); progressTrack.className = 'neko-pet-care-task-progress-track';
                            progressTrack.setAttribute('role', 'progressbar');
                            progressTrack.setAttribute('aria-label', '任务进度');
                            var progressAriaMax = Math.max(1, Math.floor(target));
                            var progressAriaNow = Math.max(0, Math.min(progressAriaMax, Math.floor(progress)));
                            progressTrack.setAttribute('aria-valuemin', '0');
                            progressTrack.setAttribute('aria-valuemax', String(progressAriaMax));
                            progressTrack.setAttribute('aria-valuenow', String(progressAriaNow));
                            var progressFill = document.createElement('span'); progressFill.className = 'neko-pet-care-task-progress-fill';
                            progressFill.style.width = progressPercent + '%';
                            progressTrack.appendChild(progressFill);
                            progressBlock.appendChild(progressHeader); progressBlock.appendChild(progressTrack);
                            details.appendChild(progressBlock);
                        }
                        toggle = button('›', 'neko-pet-care-task-toggle');
                        toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
                        toggle.setAttribute('aria-controls', detailsId);
                        toggle.setAttribute('aria-label', expanded ? '收起任务详情' : '展开任务详情');
                        toggle.title = expanded ? '收起任务详情' : '展开任务详情';
                        toggle.addEventListener('click', function (event) {
                            if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
                            state.expandedTaskId = state.expandedTaskId === taskPresentationKey ? '' : taskPresentationKey;
                            render();
                        });
                    }
                    row.dataset.expanded = expanded ? 'true' : 'false';
                    row.appendChild(tick); row.appendChild(copy);
                    if (toggle) row.appendChild(toggle);
                    if (rewardText) row.appendChild(rewardText);
                    if (taskAction) row.appendChild(taskAction);
                    if (recovery) row.appendChild(recovery);
                    if (details) row.appendChild(details);
                    ui.taskList.appendChild(row);
            });
        }
        ui.taskError.textContent = state.taskError ? taskErrorText(state.taskError) : '';
        ui.taskError.hidden = !state.taskError || !authoritativeAvailable;
        // Tasks are a dedicated rail module. The overview deliberately has no
        // task preview or claim shortcut; this prevents service task data from
        // being duplicated in two different surfaces.
        if (ui.lettersList) {
            if (typeof ui.lettersList.replaceChildren === 'function') ui.lettersList.replaceChildren();
            else { ui.lettersList.children = []; ui.lettersList.textContent = ''; }
            if (mailboxAvailable && !availableLetters.length) {
                var emptyLetters = document.createElement('p'); emptyLetters.className = 'neko-pet-care-empty';
                emptyLetters.textContent = '暂无来信'; ui.lettersList.appendChild(emptyLetters);
            } else if (mailboxAvailable) availableLetters.slice(0, 20).forEach(function (letter, index) {
                var exploration = currentExploration();
                var location = explorationLocation(exploration);
                ui.lettersList.appendChild(createExplorationLetterRow(letter, location, {
                    letterStyle: index === 0 && mailboxContent && mailboxContent.first_letter_style,
                }));
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
                    var confirmedCount = Number(count);
                    // The service may retain zero-valued inventory keys after
                    // consumption. They are not items the user still owns and
                    // must not leave a misleading x0 slot in the backpack.
                    if (!Number.isFinite(confirmedCount) || confirmedCount <= 0) return;
                    inventoryEntries.push({ id: itemId, count: confirmedCount, label: item && typeof item === 'object' ? (item.label || item.name) : '' });
                });
            }
            if (!inventoryEntries.length) {
                var emptyInventory = document.createElement('p'); emptyInventory.className = 'neko-pet-care-empty neko-pet-care-inventory-empty';
                emptyInventory.textContent = '暂无可用道具'; ui.inventoryList.appendChild(emptyInventory);
            } else inventoryEntries.forEach(function (entry) {
                var presentation = inventoryPresentation(entry.id, inventory[entry.id]);
                var card = document.createElement('article'); card.className = 'neko-pet-care-inventory-item';
                card.dataset.itemId = entry.id;
                card.title = presentation.description
                    ? presentation.label + '：' + presentation.description : presentation.label;
                card.setAttribute('aria-label', presentation.label + '，数量 ' + String(entry.count == null ? 0 : entry.count));
                var icon = document.createElement('span'); icon.className = 'neko-pet-care-inventory-icon';
                icon.dataset.itemId = entry.id;
                icon.setAttribute('aria-hidden', 'true');
                if (presentation.assetUrl) {
                    var asset = document.createElement('img');
                    asset.className = 'neko-pet-care-inventory-image';
                    asset.src = presentation.assetUrl;
                    asset.alt = '';
                    asset.draggable = false;
                    icon.appendChild(asset);
                } else icon.textContent = presentation.icon;
                card.appendChild(icon);
                // Keep the item name in the hover/accessibility label, while
                // the owned quantity stays visible at the image's lower-right
                // corner as required by the compact backpack design.
                var quantity = document.createElement('span');
                quantity.className = 'neko-pet-care-inventory-count';
                quantity.textContent = '×' + String(entry.count);
                quantity.setAttribute('aria-hidden', 'true');
                card.appendChild(quantity);
                ui.inventoryList.appendChild(card);
            });
        }
        if (ui.profileBody) {
            if (typeof ui.profileBody.replaceChildren === 'function') ui.profileBody.replaceChildren();
            else { ui.profileBody.children = []; ui.profileBody.textContent = ''; }
            var profileRows = [
                ['账号', account],
                ['当前角色', playerFacingLabel(
                    state.currentCharacterName,
                    state.currentCharacterUid,
                    '当前角色'
                )],
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
                ? (authoritativeSnapshotReady() || activeExplorationAvailable) &&
                    (state.catLocalActive || activeExplorationAvailable) && !state.explorationPending &&
                    !sickActive && !petMutationHardBlocked()
                : ((hasConfiguredCareCategory(category) || category === 'play')) &&
                    careReady() && !state.pendingAction && !state.playActivity && !state.playPending;
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
                (selected === 'exploration' ? activeExplorationAvailable || authoritativeSnapshotReady() : isPetCareCompatibilityReady()) && state.snapshot &&
                (selected === 'exploration' || !explorationActive(state.snapshot.exploration)) &&
                (selected !== 'play' || playActionEntries().length > 0);
            ui.actionDetails.hidden = !canShowDetails;
            if (canShowDetails) {
                // The exploration card owns its place title; adding a generic
                // “探索” heading above it duplicates the demo hierarchy.
                if (selected !== 'exploration') {
                    var heading = document.createElement('div');
                    heading.className = 'neko-pet-care-action-heading';
                    heading.textContent = actionLabel(selected);
                    ui.actionDetails.appendChild(heading);
                }
                if (selected === 'exploration') {
                    renderExplorationDetails(ui.actionDetails);
                } else if (selected === 'play') {
                    playActionEntries().forEach(function (item) {
                        var counter = item.action || {};
                        var quantity = Number(counter.inventory_quantity);
                        var consumeQuantity = actionConsumeQuantity(counter);
                        var dailyCount = Number(counter.daily_count);
                        var dailyLimit = Number(counter.daily_limit);
                        var recoveryPlay = isSulkRecoveryPlay(item.id, counter);
                        var reason = '';
                        if (state.playActivity) reason = '操作处理中…';
                        else if (!playAllowedDuringSulk(item.id, counter)) {
                            reason = '赌气中不会回应';
                        }
                        else if (!recoveryPlay && cooldownRemainingSeconds('play', item.id) > 0) {
                            reason = cooldownLabel(cooldownRemainingSeconds('play', item.id));
                        }
                        else if (!recoveryPlay && Number.isFinite(dailyLimit) && Number.isFinite(dailyCount) && dailyCount >= dailyLimit) {
                            reason = '今日次数已用完';
                        } else if (!Number.isInteger(consumeQuantity) || consumeQuantity < 1 ||
                            !Number.isInteger(quantity) || quantity < consumeQuantity) {
                            reason = '库存不足';
                        }
                        var toyButton = button(item.label, 'neko-pet-care-content-action');
                        toyButton.dataset.category = 'play';
                        toyButton.dataset.contentId = item.id;
                        toyButton.dataset.itemId = item.action && item.action.inventory_item || item.id;
                        var toyPresentation = appendActionItemImage(toyButton, item.id, item.action);
                var available = careReady() && !state.pendingAction && !state.playActivity && !state.playPending &&
                    !reason;
                        toyButton.disabled = !available;
                        toyButton.setAttribute('aria-disabled', available ? 'false' : 'true');
                        toyButton.setAttribute('title', available
                            ? '使用' + item.label + (toyPresentation.description
                                ? '：' + toyPresentation.description : '')
                            : (reason || '当前不可用'));
                        var meta = document.createElement('span');
                        meta.className = 'neko-pet-care-content-meta';
                        meta.textContent = reason === '库存不足'
                            ? reason + ' · ' + inventoryRequirementLabel(quantity, consumeQuantity)
                            : reason || inventoryRequirementLabel(quantity, consumeQuantity);
                        toyButton.appendChild(meta);
                        if (toyPresentation.image) toyButton.appendChild(toyPresentation.image);
                        ui.actionDetails.appendChild(toyButton);
                    });
                } else careContent(selected).forEach(function (item) {
                    var contentButton = button(item.label, 'neko-pet-care-content-action');
                    contentButton.dataset.category = selected;
                    contentButton.dataset.contentId = item.id;
                    contentButton.dataset.itemId = item.action && item.action.inventory_item || item.id;
                    var contentPresentation = appendActionItemImage(contentButton, item.id, item.action);
                    var inventoryItem = str(item.action && item.action.inventory_item);
                    var count = inventoryItem ? inventoryCountForAction(selected, item.id) : null;
                    var consumeQuantity = actionConsumeQuantity(item.action);
                    // These are read-only hints from the confirmed snapshot.
                    // The service repeats every check and remains the
                    // authority, so a stale client can only fail closed or
                    // receive a normal server rejection.
                    var remaining = cooldownRemainingSeconds(selected, item.id);
                    var reason = '';
                    if (state.pendingAction) reason = '操作处理中…';
                    else if (remaining > 0) reason = cooldownLabel(remaining);
                    else if (inventoryItem &&
                        (!Number.isInteger(consumeQuantity) || consumeQuantity < 1 || count < consumeQuantity)) {
                        reason = '库存不足';
                    }
                        var available = careReady() && !state.pendingAction && !state.playActivity && !state.playPending &&
                            !reason;
                    contentButton.disabled = !available;
                    contentButton.setAttribute('aria-disabled', available ? 'false' : 'true');
                    contentButton.setAttribute('title', available
                        ? '执行' + item.label + (contentPresentation.description
                            ? '：' + contentPresentation.description : '')
                        : (reason || '当前不可用'));
                    var meta = document.createElement('span');
                    meta.className = 'neko-pet-care-content-meta';
                    meta.textContent = inventoryItem && reason === '库存不足'
                        ? reason + ' · ' + inventoryRequirementLabel(count, consumeQuantity)
                        : reason || (inventoryItem
                            ? inventoryRequirementLabel(count, consumeQuantity)
                            : selected === 'cleaning' ? '清洁操作' : actionLabel(selected));
                    contentButton.appendChild(meta);
                    if (contentPresentation.image) contentButton.appendChild(contentPresentation.image);
                    ui.actionDetails.appendChild(contentButton);
                });
            }
        }
        if (ui.actionError) {
            var visibleActionError = state.pendingWriteError || state.actionError ||
                (state.selectedCategory === 'play' ? state.playError : '');
            var actionErrorScope = state.selectedCategory ||
                (state.pendingAction && state.pendingAction.category) || 'action';
            ui.actionError.textContent = visibleActionError
                ? careErrorText(visibleActionError, actionErrorScope) : '';
            ui.actionError.hidden = !visibleActionError;
        }
        var progressView = progressPresentation();
        ui.stats.forEach(function (item) {
            var raw = value(item.key);
            item.value.textContent = raw;
            item.value.dataset.unit = item.unit || '';
            if (item.key === 'progress' && item.element) {
                item.element.setAttribute('aria-expanded', state.progressUnlockOpen ? 'true' : 'false');
                item.element.setAttribute('aria-label', state.progressUnlockOpen
                    ? '收起已解锁内容' : '展开已解锁内容');
                item.element.dataset.contract = progressView.kind;
                if (progressView.kind === 'missing') {
                    item.helperLeft.textContent = '下一阶段数据不可用';
                    item.helperRight.textContent = ''; item.helperRight.title = '';
                    item.helperRight.hidden = true;
                    ['aria-valuemin', 'aria-valuemax', 'aria-valuenow'].forEach(function (name) {
                        if (typeof item.bar.removeAttribute === 'function') item.bar.removeAttribute(name);
                        else item.bar[name] = '';
                    });
                    item.bar.setAttribute('aria-valuetext', progressView.current === null
                        ? '养成进度不可用'
                        : '当前累计 ' + progressView.current + ' 点，下一阶段阈值不可用');
                } else if (progressView.kind === 'complete') {
                    item.helperLeft.textContent = '全部阶段已解锁';
                    item.helperRight.textContent = '持续累计'; item.helperRight.title = '';
                    item.helperRight.hidden = false;
                    item.bar.setAttribute('aria-valuemin', '0');
                    item.bar.setAttribute('aria-valuemax', '100');
                    item.bar.setAttribute('aria-valuenow', '100');
                    item.bar.setAttribute('aria-valuetext',
                        '已完成全部阶段，当前累计 ' + progressView.current + ' 点');
                } else {
                    item.helperLeft.textContent = '下一阶段 · ' + progressView.next.threshold + ' 点';
                    item.helperRight.textContent = progressView.next.name;
                    item.helperRight.title = progressView.next.name;
                    item.helperRight.hidden = false;
                    item.bar.setAttribute('aria-valuemin', '0');
                    item.bar.setAttribute('aria-valuemax', String(Math.max(1, progressView.next.threshold)));
                    item.bar.setAttribute('aria-valuenow', String(Math.max(0,
                        Math.min(Math.max(1, progressView.next.threshold), progressView.current))));
                    item.bar.setAttribute('aria-valuetext', '当前累计 ' + progressView.current +
                        ' 点，下一阶段目标 ' + progressView.next.threshold + ' 点');
                }
            }
            if (item.unitNode) item.unitNode.textContent = item.unit || '';
            if (item.fill) {
                var numeric = Number(raw), percent;
                if (item.key === 'progress') {
                    percent = progressView.percent === null ? 0 : progressView.percent;
                } else {
                    percent = Number.isFinite(numeric) ? Math.max(0, Math.min(100, numeric)) : 0;
                }
                item.fill.style.width = percent + '%';
            }
        });
        renderProgressUnlocks();
        scheduleCooldownRefresh();
        scheduleStatusRefresh();
    }
    async function fetchPetCareContext(uid) {
        return petCareClient(uid).context(uid);
    }
    async function fetchPetCareSnapshot(uid) {
        return petCareClient(uid).snapshot(uid);
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
        state.petId = ''; state.bindingRevision = null; state.petStateVersion = null; state.canRebind = false;
        state.snapshot = null; state.settlement = null; state.careActions = null;
        state.optimisticSnapshot = null;
        clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
        state.playPending = null; state.playError = '';
        state.pendingAction = null; state.actionError = '';
        state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = '';
        state.recoveryPendingStatus = ''; state.taskError = '';
        state.explorationPending = null; state.explorationError = '';
        state.explorationRecallRequested = false; state.explorationReward = null;
        clearExplorationTimer(); state.explorationLocations = null; state.explorationGateActive = false;
        taskRequestGeneration += 1; actionRequestGeneration += 1;
        state.serviceStatus = 'unavailable'; state.serviceReason = 'oauth-status-unavailable';
        context({ accountId: account, currentCharacterUid: state.currentCharacterUid,
            boundRoleId: '', reason: state.serviceReason, forceNewGeneration: true });
        mode(MODES && MODES.PET_UNAVAILABLE, state.serviceReason);
        syncCatMindCompatibility(state.reason);
        render();
        return true;
    }
    async function recoverFromAuthExpiry() {
        if (explorationActive(currentExploration())) writeExplorationLease(currentExploration());
        state.bindingStatus = 'unavailable';
        state.serviceStatus = 'auth-refreshing';
        state.serviceReason = 'pet-care-auth-expired';
        invalidatePetContext(state.serviceReason, { preserveExplorationLease: true });
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
            state.canRebind = false;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            state.explorationReward = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
            state.taskError = '';
            taskRequestGeneration += 1;
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
            // Binding discovery is deliberately separate from pet-state
            // disclosure. A mismatched role receives only the account pet ID
            // and CAS revisions needed by the explicit official rebind flow.
            response = await fetchPetCareContext(state.currentCharacterUid);
            var body = await response.json();
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) {
                return await recoverFromAuthExpiry();
            }
            if (!response.ok) throw new Error((body && (body.detail || body.reason)) || 'pet-care-context-failed');
            body = normalizeBindingEnvelope(body);
            var discoveryStatus = str(body && (body.binding_status || body.bindingStatus)).toLowerCase();
            if (discoveryStatus !== 'bound') {
                applyBinding(body, expected);
                return snap();
            }
            response = await fetchPetCareSnapshot(state.currentCharacterUid);
            body = await response.json();
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) {
                return await recoverFromAuthExpiry();
            }
            if (!response.ok) throw new Error((body && (body.detail || body.reason)) || 'pet-care-snapshot-failed');
            body = normalizeBindingEnvelope(body);
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
    async function submitRoleBinding(kind) {
        return runPetMutationIntent(function () {
            return submitRoleBindingAfterResume(kind);
        });
    }
    async function submitRoleBindingAfterResume(kind) {
        var rebind = kind === 'rebind';
        if (state.authStatus !== 'logged-in' || !state.currentCharacterUid || state.bindingPending ||
            petMutationBlocked() ||
            (rebind ? state.bindingStatus !== 'unbound' : state.bindingStatus !== 'needs-binding')) return null;
        var expectedPetId = rebind ? str(state.petId) : '';
        var expectedBindingRevision = rebind ? Number(state.bindingRevision) : null;
        var expectedStateVersion = rebind ? Number(state.petStateVersion) : null;
        if (rebind && (!expectedPetId || !Number.isInteger(expectedBindingRevision) ||
            expectedBindingRevision < 1 || !Number.isInteger(expectedStateVersion) ||
            expectedStateVersion < 1 || state.canRebind !== true)) {
            state.serviceReason = 'rebind-version-required';
            render();
            return null;
        }
        var request = ++bindingRequestGeneration, expected = state.generation;
        var previousBindingStatus = state.bindingStatus;
        var idempotencyKey = actionIdempotencyKey();
        state.bindingPending = true; state.serviceStatus = 'loading';
        state.serviceReason = rebind ? 'binding-rebind' : 'binding-create'; render();
        var businessConfirmed = false;
        try {
            var bindBody = {
                character_uid: state.currentCharacterUid,
                idempotency_key: idempotencyKey,
            };
            if (rebind) {
                bindBody.expected_pet_id = expectedPetId;
                bindBody.expected_binding_revision = expectedBindingRevision;
                bindBody.expected_state_version = expectedStateVersion;
            }
            var writeResult = await executeNewPendingWrite(
                rebind ? 'bind-rebind' : 'bind-create', bindBody, {}, {
                character_uid: state.currentCharacterUid,
                pet_id: expectedPetId,
                }
            );
            var response = writeResult.response;
            var body = writeResult.body;
            businessConfirmed = writeResult.confirmed;
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) {
                return await recoverFromAuthExpiry();
            }
            // A service rejection is a confirmed business outcome, not a
            // transport outage. Keep the current alias/CAS context intact so
            // the user can retry after the stated conflict is resolved. In
            // particular, do not chase snapshot_required on a rejected
            // rebind: the current alias is intentionally ordinary-cat and a
            // follow-up read cannot disclose the bound role snapshot.
            if (operationRejected(body)) {
                var rejectionCode = operationFailureCode(body, 'pet-care-bind-failed');
                state.serviceStatus = 'ready';
                state.serviceReason = rejectionCode;
                state.reason = rejectionCode;
                mode(MODES && MODES.PET_UNAVAILABLE, rejectionCode);
                syncCatMindCompatibility(rejectionCode);
                render();
                return null;
            }
            if (!response.ok) {
                throw new Error(operationFailureCode(body, 'pet-care-bind-failed'));
            }
            body = await hydrateSnapshotRequired(body, expected, { binding: true });
            body = normalizeBindingEnvelope(body);
            if (rebind) {
                var responseSnapshot = body && body.snapshot && typeof body.snapshot === 'object'
                    ? body.snapshot : null;
                var responsePetId = str(body && (body.pet_id || body.petId) ||
                    (responseSnapshot && responseSnapshot.pet_id));
                var responseBindingRevision = Number(body && (body.binding_revision != null
                    ? body.binding_revision : body.bindingRevision));
                var responseStateVersion = Number(body && (body.state_version != null
                    ? body.state_version : (body.stateVersion != null ? body.stateVersion :
                        responseSnapshot && responseSnapshot.state_version)));
                if (responsePetId !== expectedPetId ||
                    !Number.isInteger(responseBindingRevision) ||
                    responseBindingRevision <= expectedBindingRevision ||
                    !Number.isInteger(responseStateVersion) ||
                    responseStateVersion < expectedStateVersion) {
                    throw new Error('pet-care-rebind-contract-invalid');
                }
            }
            applyBinding(body, expected); return snap();
        } catch (error) {
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            state.pendingWriteError = error && error.message || 'bind-failed';
            if (businessConfirmed) {
                invalidatePetContext(state.pendingWriteError);
                state.bindingStatus = 'unavailable';
                markConfirmedWriteRefreshRequired(state.accountId);
            } else {
                state.bindingStatus = previousBindingStatus;
            }
            state.serviceStatus = 'unavailable'; state.serviceReason = state.pendingWriteError;
            mode(MODES && MODES.PET_UNAVAILABLE, state.serviceReason);
            syncCatMindCompatibility(state.serviceReason);
            render(); return null;
        } finally {
            if (request === bindingRequestGeneration) { state.bindingPending = false; render(); }
        }
    }
    function bindCurrentRole() {
        return submitRoleBinding('first-bind');
    }
    function rebindCurrentRole() {
        return submitRoleBinding('rebind');
    }
    async function requestRoleBinding() {
        if (state.bindingStatus !== 'unbound') return bindCurrentRole();
        if (state.canRebind !== true) return null;
        var message = '换绑只会改变这只宠物关联的角色，现有养成数据会保留。确定换绑到当前角色吗？';
        var confirmed = false;
        if (typeof window.showConfirm === 'function') {
            confirmed = await window.showConfirm(message, '换绑宠物角色', {
                okText: '确认换绑', cancelText: '取消',
            });
        } else if (typeof window.confirm === 'function') {
            confirmed = window.confirm(message);
        }
        if (!confirmed) return null;
        return rebindCurrentRole();
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
        dismissLoginHint();
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
        } catch (error) {
            state.reason = error && error.message || 'oauth-start-failed';
            if (typeof window.showStatusToast === 'function') {
                window.showStatusToast('统一账号登录未完成，请稍后重试。', 3500);
            }
            render();
        }
        finally {
            oauthCancelResolver = null;
            closeOAuthPopup();
            state.oauthPending = false;
            if (oauthPollTimer) { window.clearTimeout(oauthPollTimer); oauthPollTimer = 0; }
            render();
        }
    }
    async function logout() {
        // Give the old account's activity one recall attempt while its session
        // is still valid. PetCare is subordinate to the unified account: an
        // unavailable activity service must never prevent the user from ending
        // that account session. Preserve only the account-scoped presentation
        // lease so a later login can re-read the authoritative activity.
        var recallConfirmed = false;
        try {
            recallConfirmed = await ensureExplorationEndedBeforeAuthChange(
                'logged-out', '', '', 'logout');
        } catch (error) {
            state.explorationError = error && error.message || 'exploration-recall-failed';
        }
        if (!recallConfirmed && explorationActive(currentExploration())) {
            writeExplorationLease(currentExploration());
        }
        try {
            var response = await fetch('/api/card-drop/oauth/logout', {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
            });
            if (!response.ok) throw new Error('oauth-logout-failed');
        } catch (error) {
            state.reason = error && error.message || 'oauth-logout-failed';
            state.bindingStatus = 'unavailable'; state.bindingRoleId = ''; state.snapshot = null;
            state.canRebind = false;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            state.explorationReward = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
            state.taskError = '';
            taskRequestGeneration += 1;
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
            showPetCareNotice('退出账号暂未完成，养宠状态已暂停，请检查连接后重试。', 'warning');
            return null;
        }
        await refreshAuthStatus(recallConfirmed ? 'logout' : 'logout-exploration-pending');
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
            state.petId = ''; state.bindingRevision = null; state.petStateVersion = null; state.canRebind = false;
            state.settlement = null; state.careActions = null; state.optimisticSnapshot = null;
            clearPlayActivityTimer(); state.playActions = null; state.playActivity = null;
            state.playPending = null; state.playError = '';
            state.tasks = null; state.taskLoading = false; state.taskPendingId = ''; state.taskPendingOperation = ''; state.recoveryPendingStatus = '';
            state.taskError = '';
            taskRequestGeneration += 1;
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
        cancelPanelAutoClose();
        panelPointerActive = false;
        panelPointerInside = false;
        var exploration = currentExploration();
        if (explorationActive(exploration)) {
            // This is only a presentation dismissal. The shared desktop nest
            // stays visible and hoverable; no recall or service write occurs.
            state.dismissedExplorationId = explorationId(exploration);
        }
        if ((!options || options.cancelOAuth !== false) && state.oauthPending) cancelOAuthLogin();
        dismissFeatureHint(); state.progressUnlockOpen = false; state.panelOpen = false;
        // There is no separate launcher. Closing hides the whole surface
        // until the semantic cat or shared-nest anchor is hovered again.
        visible(false); render();
    }
    function loadTasksWhenPanelOpens() {
        // Reopening the surface performs one event-driven authoritative task
        // refresh. Binding and view changes consume the canonical tasks that
        // already travel with their snapshots and do not issue extra reads.
        if (!state.panelOpen || !authoritativeSnapshotReady() || state.taskLoading) return;
        refreshTasks();
    }
    function markPetCareInputSurface(value) {
        if (value && typeof value.setAttribute === 'function') {
            value.setAttribute('data-neko-pet-care-input-surface', 'true');
        }
        return value;
    }
    function button(text, className) {
        var value = document.createElement('button'); value.type = 'button';
        value.textContent = text; value.className = className || '';
        // Interaction and visual hit regions are distinct contracts. A
        // disabled button is still opaque and must continue to block clicks
        // from falling through the desktop window.
        value.setAttribute('data-neko-pet-care-interactive', 'true');
        return markPetCareInputSurface(value);
    }
    function petCareUiIcon(name, className) {
        var filename = PET_CARE_UI_ICON_FILES[name];
        var value = document.createElement('img');
        value.className = className || '';
        value.src = PET_CARE_UI_ICON_BASE + filename;
        value.alt = '';
        value.draggable = false;
        value.setAttribute('aria-hidden', 'true');
        return value;
    }
    function ensureUi() {
        if (ui || !document || !document.body) return;
        var root = document.createElement('div'); root.id = OVERLAY_ID;
        root.dataset.visible = 'false'; root.dataset.expanded = 'false'; root.dataset.available = 'false';
        root.dataset.loginHint = 'false';
        root.dataset.loginHintAvailable = 'false';
        root.setAttribute('role', 'region'); root.setAttribute('aria-label', '宠物养成入口');
        var loginHint = document.createElement('aside');
        loginHint.className = 'neko-pet-care-login-hint';
        loginHint.setAttribute('aria-label', '登录 N.E.K.O 账号使用养宠功能');
        markPetCareInputSurface(loginHint);
        var loginHintCopy = document.createElement('span');
        loginHintCopy.className = 'neko-pet-care-login-hint-copy';
        loginHintCopy.textContent = '登录 N.E.K.O 账号，开启养宠';
        var loginHintAction = button('登录', 'neko-pet-care-login-hint-action');
        var loginHintClose = button('×', 'neko-pet-care-login-hint-close');
        loginHintClose.setAttribute('aria-label', '本次猫形态不再提示');
        loginHintClose.setAttribute('title', '关闭提示');
        loginHint.appendChild(loginHintCopy);
        loginHint.appendChild(loginHintAction);
        loginHint.appendChild(loginHintClose);
        loginHint.hidden = true;
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
        markPetCareInputSurface(explorationStatus);
        explorationStatus.hidden = true;
        var explorationStatusFace = document.createElement('span'); explorationStatusFace.className = 'neko-pet-care-exploration-status-face'; explorationStatusFace.textContent = '✦';
        var explorationStatusTitle = document.createElement('b'); explorationStatusTitle.className = 'neko-pet-care-exploration-status-title';
        var explorationStatusLocation = document.createElement('small'); explorationStatusLocation.className = 'neko-pet-care-exploration-status-location';
        explorationStatus.appendChild(explorationStatusFace); explorationStatus.appendChild(explorationStatusTitle); explorationStatus.appendChild(explorationStatusLocation);
        var grid = document.createElement('div'); grid.className = 'neko-pet-care-stats';
        markPetCareInputSurface(grid);
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
            var helperLeft = null, helperRight = null;
            if (keys[index] === 'progress') {
                // Keep this assignment compatible with the lightweight DOM
                // harness used by the older stage tests. The real browser
                // still receives the same class token, while initialization
                // does not require a DOMTokenList implementation.
                item.className += (item.className ? ' ' : '') + 'neko-pet-care-progress-stat';
                var chevron = document.createElement('span');
                chevron.className = 'neko-pet-care-progress-chevron';
                chevron.setAttribute('aria-hidden', 'true'); chevron.textContent = '›';
                valueLine.appendChild(chevron);
                var helper = document.createElement('div');
                helper.className = 'neko-pet-care-progress-helper';
                helperLeft = document.createElement('span');
                helperRight = document.createElement('span'); helperRight.hidden = true;
                helper.appendChild(helperLeft); helper.appendChild(helperRight); item.appendChild(helper);
                item.setAttribute('role', 'button');
                item.setAttribute('tabindex', '0');
                item.setAttribute('aria-label', '展开已解锁内容');
                item.setAttribute('aria-expanded', 'false');
                item.setAttribute('data-neko-pet-care-interactive', 'true');
                bar.setAttribute('role', 'progressbar');
                bar.setAttribute('aria-label', '下一阶段解锁进度');
            }
            grid.appendChild(item); stats.push({
                key: keys[index], value: valueNode, unitNode: unitNode, fill: fill,
                bar: bar, helperLeft: helperLeft, helperRight: helperRight,
                unit: unit, element: item,
            });
        });
        var progressStat = stats.find(function (item) { return item.key === 'progress'; });
        if (progressStat && progressStat.key === 'progress') {
            var toggleProgressUnlocks = function (event) {
                if (event && event.type === 'keydown' && event.key !== 'Enter' && event.key !== ' ') return;
                if (event && event.type === 'keydown') event.preventDefault();
                state.progressUnlockOpen = !state.progressUnlockOpen;
                state.progressUnlockAllOpen = false;
                progressStat.element.setAttribute('aria-expanded', state.progressUnlockOpen ? 'true' : 'false');
                render();
            };
            progressStat.element.addEventListener('click', toggleProgressUnlocks);
            progressStat.element.addEventListener('keydown', toggleProgressUnlocks);
        }
        var progressUnlockPanel = document.createElement('div');
        progressUnlockPanel.id = 'neko-pet-care-progress-unlock-panel';
        progressUnlockPanel.className = 'neko-pet-care-progress-unlock-panel';
        progressUnlockPanel.hidden = true;
        progressUnlockPanel.setAttribute('aria-label', '已解锁内容');
        if (progressStat && progressStat.element) {
            progressStat.element.setAttribute('aria-controls', progressUnlockPanel.id);
        }
        grid.appendChild(progressUnlockPanel);
        var actions = document.createElement('div'); actions.className = 'neko-pet-care-actions';
        var actionLabels = ['喂食', '清洁', '陪玩', '探索'];
        var actionCategories = ['feeding', 'cleaning', 'play', 'exploration'];
        var actionButtons = actionLabels.map(function (label, index) {
            var item = button('', 'neko-pet-care-action');
            var icon = petCareUiIcon(actionCategories[index], 'neko-pet-care-action-icon');
            var textNode = document.createElement('span'); textNode.className = 'neko-pet-care-action-label'; textNode.textContent = label;
            item.appendChild(icon); item.appendChild(textNode); item.dataset.category = actionCategories[index];
            item.dataset.icon = actionCategories[index];
            item.setAttribute('data-neko-pet-care-interactive', 'true'); actions.appendChild(item); return item;
        });
        var actionDetails = document.createElement('div'); actionDetails.className = 'neko-pet-care-action-details';
        markPetCareInputSurface(actionDetails);
        var actionError = document.createElement('p'); actionError.className = 'neko-pet-care-action-error'; actionError.hidden = true;
        markPetCareInputSurface(actionError);
        var task = document.createElement('section'); task.className = 'neko-pet-care-task neko-pet-care-module'; task.setAttribute('aria-label', '任务');
        markPetCareInputSurface(task);
        var taskHeader = document.createElement('div'); taskHeader.className = 'neko-pet-care-task-header';
        var taskTitle = document.createElement('strong'); taskTitle.textContent = '任务'; taskHeader.appendChild(taskTitle);
        var taskList = document.createElement('div'); taskList.className = 'neko-pet-care-task-list';
        var taskError = document.createElement('p'); taskError.className = 'neko-pet-care-task-error'; taskError.hidden = true;
        task.appendChild(taskHeader); task.appendChild(taskList); task.appendChild(taskError);
        var nav = document.createElement('nav'); nav.className = 'neko-pet-care-rail'; nav.setAttribute('aria-label', '养宠模块');
        markPetCareInputSurface(nav);
        var navItems = [['overview','总览'],['tasks','任务'],['letters','来信'],['inventory','背包'],['profile','资料']];
        var navButtons = navItems.map(function (entry) {
            var n = button('', 'neko-pet-care-nav-item');
            var icon = petCareUiIcon(entry[0], 'neko-pet-care-nav-icon');
            var label = document.createElement('span'); label.className = 'neko-pet-care-nav-label'; label.textContent = entry[1];
            n.appendChild(icon); n.appendChild(label); n.dataset.view = entry[0]; n.dataset.icon = entry[0];
            n.setAttribute('aria-label', entry[1]); n.setAttribute('data-neko-pet-care-interactive','true'); nav.appendChild(n); return n;
        });
        var content = document.createElement('div'); content.className = 'neko-pet-care-content';
        var overview = document.createElement('section'); overview.className = 'neko-pet-care-module neko-pet-care-overview'; overview.dataset.view = 'overview';
        var moduleTitle = document.createElement('h2'); moduleTitle.className = 'neko-pet-care-module-title'; moduleTitle.textContent = '总览'; overview.appendChild(moduleTitle);
        var service = document.createElement('p'); service.className = 'neko-pet-care-service';
        markPetCareInputSurface(service); overview.appendChild(service);
        // The action detail card is a separate block. Keeping it outside the
        // overview module prevents a selected operation from being nested in
        // the status/preview area and lets the demo slot it independently.
        var lettersModule = document.createElement('section'); lettersModule.className = 'neko-pet-care-module neko-pet-care-letters-module'; lettersModule.dataset.view = 'letters';
        markPetCareInputSurface(lettersModule);
        var lettersTitle = document.createElement('h2'); lettersTitle.className = 'neko-pet-care-module-title'; lettersTitle.textContent = '来信'; lettersModule.appendChild(lettersTitle);
        var lettersList = document.createElement('div'); lettersList.className = 'neko-pet-care-letters-list'; lettersModule.appendChild(lettersList);
        var inventoryModule = document.createElement('section'); inventoryModule.className = 'neko-pet-care-module neko-pet-care-inventory-module'; inventoryModule.dataset.view = 'inventory';
        markPetCareInputSurface(inventoryModule);
        var inventoryTitle = document.createElement('h2'); inventoryTitle.className = 'neko-pet-care-module-title'; inventoryTitle.textContent = '背包'; inventoryModule.appendChild(inventoryTitle);
        var inventoryList = document.createElement('div'); inventoryList.className = 'neko-pet-care-inventory-list'; inventoryModule.appendChild(inventoryList);
        var profileModule = document.createElement('section'); profileModule.className = 'neko-pet-care-module neko-pet-care-profile-module'; profileModule.dataset.view = 'profile';
        markPetCareInputSurface(profileModule);
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
        root.appendChild(panel); root.appendChild(loginHint); document.body.appendChild(root);
        ui = { root: root, panel: panel, closeButton: close, stats: stats,
            scene: scene, explorationVisual: explorationVisual, explorationNestImage: explorationNestImage,
            explorationStatus: explorationStatus, explorationStatusTitle: explorationStatusTitle,
            explorationStatusLocation: explorationStatusLocation,
            grid: grid, progressUnlockPanel: progressUnlockPanel, actions: actions, navButtons: navButtons, actionButtons: actionButtons, actionDetails: actionDetails, actionError: actionError,
            overview: overview, task: task, taskList: taskList, taskError: taskError,
            lettersModule: lettersModule, lettersList: lettersList,
            inventoryModule: inventoryModule, inventoryList: inventoryList,
            profileModule: profileModule, profileBody: profileBody, service: service,
            loginHint: loginHint, loginHintAction: loginHintAction, loginHintClose: loginHintClose,
            loginButton: login, bindButton: bind, retryButton: retry, logoutButton: logoutButton, footer: footer };
        navButtons.forEach(function (item) { item.addEventListener('click', function (event) { event.stopPropagation(); setPetCareView(item.dataset.view); }); });
        actionButtons.forEach(function (item) { item.addEventListener('click', function (event) { event.stopPropagation(); setActionCategory(item.dataset.category || ''); }); });
        actionDetails.addEventListener('click', function (event) {
            var target = event && event.target;
            while (target && target !== actionDetails && !target.dataset.contentId && !target.dataset.explorationAction) target = target.parentNode;
            if (!target || target === actionDetails || (!target.dataset.contentId && !target.dataset.explorationAction)) return;
            if (target.disabled || target.getAttribute && target.getAttribute('aria-disabled') === 'true') return;
            event.stopPropagation();
            if (target.dataset.explorationAction === 'start') startExploration(defaultExplorationLocation().id);
            else if (target.dataset.explorationAction === 'recall') requestExplorationRecall({ source: 'pet-care-nest', presentation: 'cat-form' });
            else if (target.dataset.explorationAction === 'complete') completeExploration();
            else if (target.dataset.category === 'play') beginToyPlayActivity(target.dataset.contentId);
            else performCareAction(target.dataset.category, target.dataset.contentId);
        });
        taskList.addEventListener('click', function (event) {
            var target = event && event.target;
            while (target && target !== taskList && !target.dataset.taskId && !target.dataset.recoveryStatus) target = target.parentNode;
            if (!target || target === taskList || !target.dataset.taskId || target.disabled) return;
            event.stopPropagation();
            if (target.dataset.recoveryStatus) useRecoveryItem(target.dataset.recoveryStatus);
            else if (target.dataset.taskAction === 'complete') completeTask(target.dataset.taskId);
            else if (target.dataset.taskAction === 'claim') claimTask(target.dataset.taskId);
        });
        close.addEventListener('click', function (event) { event.stopPropagation(); closePanel(); });
        loginHintAction.addEventListener('click', function (event) {
            event.stopPropagation(); startLogin();
        });
        loginHintClose.addEventListener('click', function (event) {
            event.stopPropagation(); dismissLoginHint();
        });
        login.addEventListener('click', function (event) { event.stopPropagation(); startLogin(); });
        bind.addEventListener('click', function (event) { event.stopPropagation(); requestRoleBinding(); });
        retry.addEventListener('click', function (event) {
            event.stopPropagation();
            if (currentPendingWrite()) replayPendingWrite({ automatic: false });
            else recoverDegradedSurface('manual-retry');
        });
        logoutButton.addEventListener('click', function (event) { event.stopPropagation(); logout(); });
        root.addEventListener('mouseenter', function () {
            panelPointerInside = true;
            cancelPanelAutoClose();
            showSoon();
        });
        root.addEventListener('mouseleave', function () {
            panelPointerInside = false;
            if (state.panelOpen) schedulePanelAutoClose();
            else hideSoon();
        });
        root.addEventListener('pointerdown', function () {
            panelPointerActive = true;
            panelPointerInside = true;
            cancelPanelAutoClose();
        });
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
        document.addEventListener('pointerup', releasePanelPointer, { passive: true });
        document.addEventListener('pointercancel', releasePanelPointer, { passive: true });
        window.addEventListener('blur', releasePanelPointer);
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
        // PetCare observes the existing user yarn gesture and the existing
        // cat action terminal events. A terminal event without a recent
        // user-initiated lease is ignored, so Cat Mind/autonomous play can
        // never be counted as user陪玩.
        window.addEventListener('neko:cat-mind:observation', function (event) {
            observePlayObservation(event && event.detail);
        });
        window.addEventListener('neko:cat-mind:action-request', function (event) {
            observePlayActionRequest(event && event.detail);
        });
        window.addEventListener('neko:cat-mind:state-change', function (event) {
            observePlayActionStarted(event && event.detail);
        });
        window.addEventListener('neko:idle-cat1-playground-yarn-activate',
            authorizePlaygroundYarnActivation);
        window.addEventListener('neko:pet-care:yarn-click-response', function (event) {
            observePlayObservation(event && event.detail);
        });
        window.addEventListener('neko:cat-mind:action-result', function (event) {
            observePlayActionResult(event && event.detail);
        });
        window.addEventListener('neko:cat-local-active-change', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail : {};
            var wasCatLocalActive = state.catLocalActive;
            state.catLocalActive = detail.active === true;
            if (!wasCatLocalActive && state.catLocalActive) {
                state.loginHintShownForCatEpisode = false;
                state.loginHintVisible = false;
            } else if (wasCatLocalActive && !state.catLocalActive) {
                if (loginHintTimer) { window.clearTimeout(loginHintTimer); loginHintTimer = 0; }
                state.loginHintVisible = false;
                state.loginHintShownForCatEpisode = false;
            }
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
                clearPreauthorizedPlayActivities();
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
                    state.playPending = null; state.playError = '';
                    flushDeferredAccountResume('cat-form-left');
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
        window.addEventListener('pageshow', function () {
            refreshAuthStatus('pageshow');
            scheduleExplorationLetterRefresh();
        });
        window.addEventListener('pagehide', function () {
            clearExplorationLetterRefreshTimer(false);
        });
        window.addEventListener('neko:widget-interaction-start', handleVerifiedUserInteraction);
        window.addEventListener('neko:widget-interaction-extend', handleVerifiedUserInteraction);
        document.addEventListener('click', function () {
            if (state.panelOpen) closePanel();
        });
        document.addEventListener('keydown', function (event) {
            if (event && event.key === 'Escape' && state.panelOpen) closePanel();
        });
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') {
                refreshAuthStatus('visible');
                scheduleExplorationLetterRefresh();
            } else {
                clearExplorationLetterRefreshTimer(false);
            }
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
                // The mode change gates cat-entity output. A previously
                // dismissed activity stays collapsed because its shared nest
                // remains available as the reopen anchor.
                if (explorationActive(currentExploration())) {
                    if (ui) ui.root.dataset.available = 'true';
                    if (state.dismissedExplorationId !== explorationId(currentExploration())) visible(true);
                    render();
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
        requestAccountResume: requestAccountResume,
        bindCurrentRole: bindCurrentRole, rebindCurrentRole: rebindCurrentRole,
        startLogin: startLogin, logout: logout,
        setBindingResult: setBindingResult, positionOverlay: positionOverlay,
        performCareAction: performCareAction, setActionCategory: setActionCategory,
        authorizeCatMindYarnAction: authorizeCatMindYarnAction,
        refreshTasks: refreshTasks, completeTask: completeTask, claimTask: claimTask,
        useRecoveryItem: useRecoveryItem,
        retryPendingWrite: replayPendingWrite,
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
