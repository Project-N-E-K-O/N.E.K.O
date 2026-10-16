/**
 * Stage 2 pet-care account, role binding, local snapshot and independent entry
 * surface, plus the stage 3 Cat Mind compatibility boundary.
 *
 * The launcher belongs to the cat-form surface. It is deliberately not a
 * model button, never registers a model floating button, does not read model bounds/managers, and does not observe
 * Cat Mind hover events. Account identity comes from the shared N.E.K.O
 * account session (the current local adapter is the existing OAuth session);
 * role binding comes from the pet-care service.
 */
(function () {
    'use strict';

    var HOVER_DELAY_MS = 400;
    var HIDE_DELAY_MS = 180;
    var OVERLAY_ID = 'neko-pet-care-entry-overlay';
    var MODES = window.NekoPetCareModeContract && window.NekoPetCareModeContract.MODES;
    var CAT_MIND_ACTION_IDS = window.NekoPetCareModeContract &&
        window.NekoPetCareModeContract.CAT_MIND_ACTION_IDS || [
            'cat1_social_ping', 'cat1_eat_snack', 'cat1_small_move',
            'cat1_play_yarn', 'cat2_nap_feedback', 'cat3_sleep_feedback',
        ];
    var FEATURE_HINT_STORAGE_KEY = 'neko.pet-care.feature-hint-shown';
    var state = {
        authStatus: 'unknown', accountId: '', user: null, sessionGeneration: '',
        currentCharacterUid: '', currentCharacterName: '', bindingStatus: 'unknown',
        bindingRoleId: '', serviceStatus: 'unknown', serviceReason: 'initial',
        snapshot: null, generation: 0, reason: 'initial', panelOpen: false,
        hoverVisible: false, oauthPending: false, bindingPending: false,
        catLocalActive: false, catMindCompatibilityEnabled: false,
        featureHintVisible: false,
    };
    var ui = null, hoverTimer = 0, hideTimer = 0, authRequestGeneration = 0;
    var bindingRequestGeneration = 0, oauthPollTimer = 0;
    var oauthPopup = null, oauthCancelResolver = null, oauthAttemptGeneration = 0;

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
            snapshot: state.snapshot, generation: state.generation,
            reason: state.reason, panelOpen: state.panelOpen,
            hoverVisible: state.hoverVisible, oauthPending: state.oauthPending,
            bindingPending: state.bindingPending,
            catLocalActive: state.catLocalActive,
            catMindCompatibilityEnabled: state.catMindCompatibilityEnabled,
            featureHintVisible: state.featureHintVisible,
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
    function invalidatePetContext(reason, options) {
        var next = options && typeof options === 'object' ? options : {};
        var keepAccount = next.clearAccount !== true;
        state.bindingRoleId = '';
        state.snapshot = null;
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
        state.panelOpen = true;
        if (ui) ui.root.dataset.available = 'true';
        visible(true);
        positionOverlay(anchor.rect);
        emit('neko:pet-care:feature-hint', {
            message: '已开启养宠功能，可在猫形态右侧打开养宠面板。',
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
        state.currentCharacterUid = uid; state.currentCharacterName = name;
        state.bindingStatus = 'unknown'; state.bindingRoleId = ''; state.snapshot = null;
        state.serviceStatus = state.authStatus === 'logged-in' ? 'loading' : 'unknown';
        state.reason = reason || 'character-change';
        context({ accountId: state.accountId, currentCharacterUid: uid, boundRoleId: '',
            reason: state.reason });
        if (state.authStatus === 'logged-in') mode(MODES && MODES.PET_LOADING, state.reason);
        syncCatMindCompatibility(state.reason);
        emit('neko:pet-care:identity-change', snap()); render();
        if (state.authStatus === 'logged-in') refreshBinding('character-change');
    }
    function setAuth(next, reason) {
        var logged = !!(next && next.logged_in && str(next.local_user_id));
        var auth = logged ? 'logged-in' : 'logged-out';
        var account = logged ? str(next.local_user_id) : '';
        var session = next && next.session_generation != null ? String(next.session_generation).trim() : '';
        var changed = auth !== state.authStatus || account !== state.accountId ||
            session !== state.sessionGeneration;
        state.authStatus = auth; state.accountId = account;
        state.user = logged && next.user && typeof next.user === 'object'
            ? Object.assign({}, next.user) : null;
        state.sessionGeneration = session; state.reason = reason || 'auth-status';
        if (!logged) {
            if (state.oauthPending) cancelOAuthLogin();
            state.bindingStatus = 'unknown'; state.bindingRoleId = ''; state.snapshot = null;
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
            state.serviceStatus = 'loading'; state.serviceReason = 'binding-loading';
            context({ accountId: account, currentCharacterUid: state.currentCharacterUid,
                boundRoleId: '', reason: state.reason, forceNewGeneration: true });
            mode(MODES && MODES.PET_LOADING, state.reason);
        }
        syncCatMindCompatibility(state.reason);
        emit('neko:pet-care:auth-change', snap()); render();
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
                return numeric >= 0 && numeric <= 100;
            })) return false;
        if (!snapshot.negative_status || typeof snapshot.negative_status !== 'object') return false;
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
        state.serviceStatus = status === 'unavailable' ? 'unavailable' :
            status === 'bound' && !complete ? 'unavailable' : 'ready';
        if (status === 'bound' && candidateSnapshot && !complete) state.serviceReason = 'snapshot-incomplete';
        state.reason = state.serviceReason;
        context({ accountId: state.accountId, currentCharacterUid: state.currentCharacterUid,
            boundRoleId: state.bindingRoleId, reason: state.reason });
        if (status === 'bound' && complete) mode(MODES && MODES.PET_AVAILABLE, state.reason);
        else mode(MODES && MODES.PET_UNAVAILABLE, state.reason);
        syncCatMindCompatibility(state.reason);
        emit('neko:pet-care:binding-change', snap()); render(); return true;
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
    }
    function visible(value) {
        state.hoverVisible = !!value;
        if (ui) {
            ui.root.dataset.visible = value ? 'true' : 'false';
            // Hover reveals only the launcher; the operation panel opens on click.
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
            if (current && current.visible && current.rect && !current.busy) visible(true);
        }, HOVER_DELAY_MS);
    }
    function anchorState() {
        var anchor = window.nekoCatInteractionAnchor;
        if (!anchor || typeof anchor.getState !== 'function') return null;
        try { return anchor.getState(); } catch (_) { return null; }
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
        var width = ui.button.offsetWidth || 54;
        var panelWidth = ui.panel.offsetWidth || Math.min(304, window.innerWidth - 32);
        var left = rect.right + 12;
        // Keep the panel on the far side of the cat. The launcher itself is
        // the small bridge between the cat and the expandable panel.
        var side = 'right';
        if (left + 76 + panelWidth > window.innerWidth - 8) {
            left = rect.left - width - 12;
            side = 'left';
        }
        if (left < 8) left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right + 12));
        ui.root.dataset.panelSide = side;
        ui.root.style.left = Math.round(left) + 'px';
        ui.root.style.right = 'auto';
        var height = ui.button.offsetHeight || 54;
        var center = rect.top + ((Number(rect.height) || (rect.bottom - rect.top) || height) / 2);
        var top = center - (height / 2);
        ui.root.style.top = Math.round(Math.max(8, Math.min(top,
            window.innerHeight - height - 8))) + 'px';
    }
    function value(key) {
        var values = state.snapshot && state.snapshot.status_values;
        return values && typeof values === 'object' && values[key] != null
            ? String(values[key]) : '—';
    }
    function render() {
        if (!ui) return;
        var status = state.authStatus, binding = state.bindingStatus;
        var account = state.user && (state.user.display_name || state.user.email || state.accountId);
        var authView = status === 'logged-out' || state.oauthPending;
        ui.closeButton.setAttribute('aria-label', authView ? '关闭登录' : '关闭养宠面板');
        ui.closeButton.setAttribute('title', authView ? '关闭登录' : '关闭');
        ui.root.dataset.authView = authView ? 'true' : 'false';
        ui.button.textContent = status === 'logged-out' ? '养宠 · 登录' :
            binding === 'bound' ? '养宠 · 已绑定' : '养宠';
        ui.root.dataset.status = status; ui.root.dataset.bindingStatus = binding;
        ui.root.dataset.serviceStatus = state.serviceStatus;
        ui.root.dataset.panelOpen = state.panelOpen ? 'true' : 'false';
        ui.root.dataset.expanded = state.panelOpen ? 'true' : 'false';
        ui.title.textContent = '养宠';
        ui.badge.textContent = status !== 'logged-in' ? '未登录' :
            binding === 'bound' ? '角色已绑定' : binding === 'needs-binding' ? '待绑定' :
            binding === 'unbound' ? '普通猫' : state.serviceStatus === 'unavailable' ? '服务不可用' : '校验中';
        ui.identity.textContent = status !== 'logged-in' ? '登录 N.E.K.O 账号后查看账号宠物。' :
            '账号：' + (account || state.accountId || '已连接') + ' · 当前角色：' +
            (state.currentCharacterName || state.currentCharacterUid || '读取中');
        ui.service.textContent = status === 'logged-out' ? '养成入口处于灰色状态，登录后可用。' :
            binding === 'needs-binding' ? '这是账号首次使用养宠，请确认绑定当前角色。' :
            binding === 'unbound' ? '当前角色不是账号绑定角色，继续使用普通猫。' :
            binding === 'bound' && !state.snapshot ? '角色已由服务确认，但完整养成快照暂不可用。' :
            state.serviceStatus === 'unavailable' ? '养成服务暂时不可用，猫的普通行为不受影响。' :
            state.serviceStatus === 'loading' ? '正在确认账号宠物和当前角色。' : '养成状态由服务确认后显示。';
        if (binding === 'bound' && state.snapshot && state.serviceStatus === 'ready') {
            ui.service.textContent = state.featureHintVisible
                ? '已开启养宠功能，可在猫形态右侧打开养宠面板。'
                : '状态由服务确认；喂食、清洁、陪玩和探索将在后续阶段开放。';
        }
        // Unknown auth is recoverable: keep the login path visible until the
        // shared account authority confirms a logged-in session again.
        ui.loginButton.hidden = status === 'logged-in';
        ui.loginButton.disabled = state.oauthPending;
        ui.loginButton.textContent = state.oauthPending ? '登录中…' : '登录 N.E.K.O 账号';
        ui.bindButton.hidden = !(status === 'logged-in' && binding === 'needs-binding');
        ui.retryButton.hidden = !(status !== 'logged-in' || binding === 'unknown' ||
            state.serviceStatus === 'unavailable' || state.serviceStatus === 'auth-refreshing');
        ui.logoutButton.hidden = status !== 'logged-in';
        ui.task.hidden = !(status === 'logged-in' && binding === 'bound' &&
            state.snapshot && state.serviceStatus === 'ready');
        ui.actionButtons.forEach(function (button) {
            // Stage 2 is read-only. Action handlers are introduced only after
            // their server-confirmed contracts land in later phases.
            button.disabled = true; button.setAttribute('aria-disabled', 'true');
            button.setAttribute('title', '后续阶段开放');
        });
        ui.stats.forEach(function (item) { item.value.textContent = value(item.key); });
    }
    async function fetchPetCareSnapshot(uid) {
        var path = '/api/pet-care/pets/' + encodeURIComponent(uid);
        var response = await fetch(path, {
            credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json' },
        });
        // Stage 1/local deployments may only expose /context. Keep that
        // endpoint as a compatibility fallback; a real service error is not
        // silently converted into a different read path.
        if (response.status === 404 || response.status === 405 || response.status == null) {
            return fetch('/api/pet-care/context?character_uid=' + encodeURIComponent(uid), {
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
            state.serviceStatus = 'unavailable'; state.serviceReason = 'character-identity-unavailable';
            mode(MODES && MODES.PET_UNAVAILABLE, state.serviceReason); render();
            syncCatMindCompatibility(state.serviceReason);
            return null;
        }
        var request = ++bindingRequestGeneration, expected = state.generation;
        state.bindingPending = true; state.serviceStatus = 'loading';
        state.serviceReason = reason || 'binding-loading'; mode(MODES && MODES.PET_LOADING, state.serviceReason); render();
        try {
            var response = await fetchPetCareSnapshot(state.currentCharacterUid);
            var body = await response.json();
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
            if (isAuthExpiredResponse(response, body)) {
                return await recoverFromAuthExpiry();
            }
            if (!response.ok) throw new Error((body && (body.detail || body.reason)) || 'pet-care-context-failed');
            applyBinding(body, expected); return snap();
        } catch (error) {
            if (request !== bindingRequestGeneration || expected !== state.generation) return null;
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
            var response = await fetch('/api/pet-care/bind', {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ character_uid: state.currentCharacterUid }),
            });
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
        try {
            var response = await fetch('/api/card-drop/oauth/logout', {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
            });
            if (!response.ok) throw new Error('oauth-logout-failed');
        } catch (error) {
            state.reason = error && error.message || 'oauth-logout-failed';
            state.bindingStatus = 'unavailable'; state.bindingRoleId = ''; state.snapshot = null;
            state.serviceStatus = 'unavailable';
            // A failed logout request cannot prove that the account session
            // ended, but it does invalidate the pet-care context locally.
            // Release Cat Mind compatibility and return to ordinary-cat mode
            // so stale autonomous runs are cleaned instead of being left
            // behind the failed account operation.
            mode(MODES && MODES.PET_UNAVAILABLE, state.reason);
            syncCatMindCompatibility(state.reason);
            render();
            return null;
        }
        await refreshAuthStatus('logout');
    }
    async function refreshAuthStatus(reason) {
        var request = ++authRequestGeneration;
        try {
            var response = await fetch('/api/card-drop/oauth/status', { credentials: 'same-origin', cache: 'no-store' });
            var body = await response.json();
            if (request !== authRequestGeneration) return null;
            if (!response.ok) throw new Error('oauth-status-failed');
            await setAuth(body, reason || 'auth-refresh'); return snap();
        } catch (_) {
            if (request !== authRequestGeneration) return null;
            // Do not keep displaying a previously confirmed pet snapshot when
            // the account authority cannot currently be reached.
            state.bindingStatus = 'unavailable'; state.bindingRoleId = ''; state.snapshot = null;
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
        if ((!options || options.cancelOAuth !== false) && state.oauthPending) cancelOAuthLogin();
        dismissFeatureHint(); state.panelOpen = false; visible(state.hoverVisible); render();
    }
    function togglePanel() {
        if (state.panelOpen) { closePanel(); return; }
        state.panelOpen = true; visible(true); render();
    }
    function button(text, className) {
        var value = document.createElement('button'); value.type = 'button';
        value.textContent = text; value.className = className || ''; return value;
    }
    function ensureUi() {
        if (ui || !document || !document.body) return;
        var root = document.createElement('div'); root.id = OVERLAY_ID;
        root.dataset.visible = 'false'; root.dataset.expanded = 'false'; root.dataset.available = 'false';
        root.setAttribute('role', 'region'); root.setAttribute('aria-label', '宠物养成入口');
        var trigger = button('养宠', 'neko-pet-care-entry-button');
        trigger.setAttribute('aria-label', '打开养宠面板');
        var panel = document.createElement('section'); panel.className = 'neko-pet-care-entry-panel';
        panel.setAttribute('aria-label', '养宠状态和操作');
        var header = document.createElement('div'); header.className = 'neko-pet-care-panel-header';
        var title = document.createElement('strong'), badge = document.createElement('span');
        var close = button('×', 'neko-pet-care-close-button');
        close.setAttribute('aria-label', '关闭养宠面板');
        close.setAttribute('title', '关闭');
        badge.className = 'neko-pet-care-status-badge'; header.appendChild(title); header.appendChild(badge);
        header.appendChild(close);
        var identityText = document.createElement('p'); identityText.className = 'neko-pet-care-identity';
        var grid = document.createElement('div'); grid.className = 'neko-pet-care-stats';
        var labels = ['饱食度', '精力', '清洁度', '心情', '养成进度'];
        var keys = ['satiety', 'energy', 'cleanliness', 'mood', 'progress'], stats = [];
        labels.forEach(function (label, index) {
            var item = document.createElement('div'); item.className = 'neko-pet-care-stat';
            var name = document.createElement('span'), valueNode = document.createElement('b');
            name.textContent = label; valueNode.textContent = '—'; item.appendChild(name); item.appendChild(valueNode);
            grid.appendChild(item); stats.push({ key: keys[index], value: valueNode });
        });
        var actions = document.createElement('div'); actions.className = 'neko-pet-care-actions';
        // These are business categories. Concrete food, cleaning operation,
        // toy and exploration locations are selected by later service-backed
        // stages; do not turn a current candidate item into the category name.
        var actionLabels = ['喂食', '清洁', '陪玩', '探索'];
        var actionButtons = actionLabels.map(function (label) {
            var item = button(label, 'neko-pet-care-action'); actions.appendChild(item); return item;
        });
        var inventory = document.createElement('div'); inventory.className = 'neko-pet-care-inventory';
        inventory.textContent = '物品与背包 · 后续阶段开放';
        var task = document.createElement('div'); task.className = 'neko-pet-care-task';
        task.textContent = '每日任务 · 阶段 5 开放';
        var service = document.createElement('p'); service.className = 'neko-pet-care-service';
        var footer = document.createElement('div'); footer.className = 'neko-pet-care-panel-footer';
        var login = button('登录 N.E.K.O 账号', 'neko-pet-care-login-button');
        var bind = button('绑定当前角色', 'neko-pet-care-bind-button');
        var retry = button('重新检查', 'neko-pet-care-retry-button');
        var logoutButton = button('退出账号', 'neko-pet-care-logout-button');
        footer.appendChild(login); footer.appendChild(bind); footer.appendChild(retry); footer.appendChild(logoutButton);
        panel.appendChild(header); panel.appendChild(identityText); panel.appendChild(grid);
        panel.appendChild(actions); panel.appendChild(task); panel.appendChild(service);
        panel.appendChild(inventory); panel.appendChild(footer);
        root.appendChild(trigger); root.appendChild(panel); document.body.appendChild(root);
        ui = { root: root, button: trigger, panel: panel, closeButton: close, title: title, badge: badge,
            identity: identityText, stats: stats, actionButtons: actionButtons, task: task,
            inventory: inventory, service: service, loginButton: login, bindButton: bind, retryButton: retry,
            logoutButton: logoutButton };
        trigger.addEventListener('click', function (event) { event.stopPropagation(); togglePanel(); });
        close.addEventListener('click', function (event) {
            event.stopPropagation(); closePanel();
        });
        login.addEventListener('click', function (event) { event.stopPropagation(); startLogin(); });
        bind.addEventListener('click', function (event) { event.stopPropagation(); bindCurrentRole(); });
        retry.addEventListener('click', function (event) { event.stopPropagation(); refreshBinding('manual-retry'); });
        logoutButton.addEventListener('click', function (event) { event.stopPropagation(); logout(); });
        root.addEventListener('mouseenter', showSoon);
        root.addEventListener('mouseleave', function () { if (!state.panelOpen) hideSoon(); });
        root.addEventListener('click', function (event) { event.stopPropagation(); });
        render(); positionOverlay();
    }
    function initialize() {
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
        setCharacter(null, 'initial-character'); refreshAuthStatus('initial-auth');
        document.addEventListener('pointermove', handlePointerMove, { passive: true });
        window.addEventListener('resize', positionOverlay);
        window.addEventListener('scroll', positionOverlay, true);
        window.addEventListener('neko:idle-return-ball-state', syncAnchor);
        window.addEventListener('neko:cat-return-commit', syncAnchor);
        window.addEventListener('neko:cat-return-complete', syncAnchor);
        window.addEventListener('neko:cat-local-active-change', function (event) {
            var detail = event && event.detail && typeof event.detail === 'object'
                ? event.detail : {};
            state.catLocalActive = detail.active === true;
            var api = window.nekoPetCareMode;
            if (api && typeof api.setCatLocalActive === 'function') {
                try { api.setCatLocalActive(state.catLocalActive, detail); } catch (_) {}
            }
            if (!state.catLocalActive) {
                clearTimers();
                state.panelOpen = false;
                state.featureHintVisible = false;
                if (ui) ui.root.dataset.available = 'false';
                visible(false);
                mode(MODES && MODES.PET_UNAVAILABLE, 'cat-form-left');
            } else {
                syncAnchor();
                if (isPetCareCompatibilityReady()) mode(MODES && MODES.PET_AVAILABLE, 'cat-form-entered');
            }
            syncCatMindCompatibility(state.catLocalActive ? 'cat-entered' : 'cat-left');
        });
        window.addEventListener('focus', function () { refreshAuthStatus('focus'); });
        window.addEventListener('pageshow', function () { refreshAuthStatus('pageshow'); });
        document.addEventListener('click', function () {
            if (state.panelOpen) closePanel();
        });
        document.addEventListener('keydown', function (event) {
            if (event && event.key === 'Escape' && state.panelOpen) closePanel();
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
        window.addEventListener('chat-avatar-display-updated', function (event) {
            setCharacter(event && event.detail, event && event.detail && event.detail.reason ||
                'character-display-updated');
        });
        window.addEventListener('neko:pet-care:mode-change', function (event) {
            if (event && event.detail && event.detail.current &&
                event.detail.current.mode === 'exploration-away') {
                clearTimers(); closePanel({ cancelOAuth: false });
            }
        });
    }
    window.nekoPetCareEntry = Object.freeze({
        getState: snap, refreshAuthStatus: refreshAuthStatus, refreshBinding: refreshBinding,
        bindCurrentRole: bindCurrentRole, startLogin: startLogin, logout: logout,
        setBindingResult: setBindingResult, positionOverlay: positionOverlay,
    });
    if (typeof window !== 'undefined') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize, { once: true });
        else initialize();
    }
})();
