/**
 * Event-driven PetCare role-alias retirement.
 *
 * A committed local character deletion supplies only its device-local UID.
 * This module remembers the unified accounts for which that UID reached a
 * service bind flow, persists fixed retirement evidence before sending, and
 * retries only when the same account is verified again. Stable role, pet,
 * binding, credentials, and character contents are never stored here.
 */
(function (global) {
    'use strict';

    var STORAGE_KEY = 'neko.pet-care.role-alias-retirements.v1';
    var OWNER_STORAGE_KEY = 'neko.pet-care.role-alias-owners.v1';
    var STATE_LOCK_NAME = 'neko.pet-care.role-alias-state.v1';
    var VERSION = 1;
    var UID_RE = /^[0-9a-f]{32}$/;
    var MAX_ACCOUNTS = 16;
    var MAX_RECORDS_PER_ACCOUNT = 128;
    var flushing = null;
    var flushQueued = false;
    var LOCAL_INTEGRATION_PREFIX = '/api/local-integrations/pet-care';

    function lockManager() {
        return global.navigator && global.navigator.locks;
    }

    function withStateLock(callback) {
        var locks = lockManager();
        if (!locks || typeof locks.request !== 'function') {
            return Promise.reject(new Error('pet_care_role_alias_lock_unavailable'));
        }
        if (typeof callback !== 'function') {
            return Promise.reject(new Error('pet_care_role_alias_lock_invalid'));
        }
        var requested;
        try {
            requested = locks.request(STATE_LOCK_NAME, { mode: 'exclusive' }, function (lock) {
                if (!lock) throw new Error('pet_care_role_alias_lock_unavailable');
                return callback();
            });
        } catch (error) {
            return Promise.reject(error);
        }
        return Promise.resolve(requested);
    }

    function text(value) {
        return typeof value === 'string' ? value.trim() : '';
    }

    function normalizeAccountId(value) {
        var result = text(value);
        if (!result || result.length > 128 || /[\u0000-\u001f\u007f]/.test(result)) {
            throw new Error('pet_care_role_alias_account_invalid');
        }
        return result;
    }

    function normalizeRetirement(value) {
        var input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
        var keys = Object.keys(input).sort();
        var uid = text(input.character_uid);
        var revision = text(input.source_revision);
        if (keys.join(',') !== 'character_uid,source_revision,status' ||
            !UID_RE.test(uid) || input.status !== 'retired' || revision !== 'delete:' + uid) {
            throw new Error('pet_care_role_alias_retirement_invalid');
        }
        return Object.freeze({
            character_uid: uid,
            source_revision: revision,
            status: 'retired',
        });
    }

    function emptyQueue() {
        return { version: VERSION, accounts: {} };
    }

    function storage() {
        if (!global.localStorage || typeof global.localStorage.getItem !== 'function' ||
            typeof global.localStorage.setItem !== 'function') {
            throw new Error('pet_care_role_alias_storage_unavailable');
        }
        return global.localStorage;
    }

    function readQueue() {
        var raw = storage().getItem(STORAGE_KEY);
        if (!raw) return emptyQueue();
        var parsed;
        try { parsed = JSON.parse(raw); } catch (_) {
            throw new Error('pet_care_role_alias_storage_corrupt');
        }
        if (!parsed || parsed.version !== VERSION || !parsed.accounts ||
            typeof parsed.accounts !== 'object' || Array.isArray(parsed.accounts)) {
            throw new Error('pet_care_role_alias_storage_corrupt');
        }
        var result = emptyQueue();
        var accountIds = Object.keys(parsed.accounts);
        if (accountIds.length > MAX_ACCOUNTS) throw new Error('pet_care_role_alias_storage_corrupt');
        accountIds.forEach(function (accountId) {
            var normalizedAccount = normalizeAccountId(accountId);
            var rows = parsed.accounts[accountId];
            if (!Array.isArray(rows) || rows.length > MAX_RECORDS_PER_ACCOUNT) {
                throw new Error('pet_care_role_alias_storage_corrupt');
            }
            result.accounts[normalizedAccount] = rows.map(normalizeRetirement);
        });
        return result;
    }

    function writeQueue(queue) {
        var raw = JSON.stringify(queue);
        storage().setItem(STORAGE_KEY, raw);
        if (storage().getItem(STORAGE_KEY) !== raw) {
            throw new Error('pet_care_role_alias_storage_failed');
        }
    }

    function readOwners() {
        var raw = storage().getItem(OWNER_STORAGE_KEY);
        if (!raw) return {};
        var parsed;
        try { parsed = JSON.parse(raw); } catch (_) {
            throw new Error('pet_care_role_alias_owner_storage_corrupt');
        }
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
            throw new Error('pet_care_role_alias_owner_storage_corrupt');
        }
        var result = {};
        var uids = Object.keys(parsed);
        if (uids.length > MAX_RECORDS_PER_ACCOUNT) {
            throw new Error('pet_care_role_alias_owner_storage_corrupt');
        }
        uids.forEach(function (uid) {
            if (!UID_RE.test(uid) || !Array.isArray(parsed[uid]) ||
                parsed[uid].length > MAX_ACCOUNTS) {
                throw new Error('pet_care_role_alias_owner_storage_corrupt');
            }
            result[uid] = parsed[uid].map(normalizeAccountId).filter(function (account, index, all) {
                return all.indexOf(account) === index;
            });
        });
        return result;
    }

    function writeOwners(owners) {
        var raw = JSON.stringify(owners);
        storage().setItem(OWNER_STORAGE_KEY, raw);
        if (storage().getItem(OWNER_STORAGE_KEY) !== raw) {
            throw new Error('pet_care_role_alias_owner_storage_failed');
        }
    }

    function ownersForCharacter(characterUid) {
        var uid = text(characterUid);
        if (!UID_RE.test(uid)) return [];
        return (readOwners()[uid] || []).slice();
    }

    function rememberOwnerLocked(characterUid, accountId) {
        var uid = text(characterUid);
        if (!UID_RE.test(uid)) throw new Error('pet_care_role_alias_character_invalid');
        var owner = normalizeAccountId(accountId);
        var owners = readOwners();
        var rows = owners[uid] || [];
        if (rows.indexOf(owner) < 0) {
            if (rows.length >= MAX_ACCOUNTS) throw new Error('pet_care_role_alias_owner_storage_full');
            rows.push(owner);
            owners[uid] = rows;
            writeOwners(owners);
        }
        return owner;
    }

    function rememberOwner(characterUid, accountId) {
        return withStateLock(function () {
            return rememberOwnerLocked(characterUid, accountId);
        }).then(function (owner) {
            // A bind must not reach the authority until this crash-safe local
            // routing fact exists. Browser storage alone cannot recover a
            // committed deletion after the renderer disappears. Bound
            // snapshot refreshes may catch this rejection and retry later,
            // while the bind pending-write path deliberately fails closed and
            // preserves its original idempotency key.
            return registerBackendOwner(characterUid, owner).then(function () {
                Promise.resolve().then(flushQuietly);
                return owner;
            });
        });
    }

    async function localIntegrationRequest(path, options) {
        if (typeof global.fetch !== 'function') {
            throw new Error('pet_care_role_alias_local_transport_unavailable');
        }
        var response = await global.fetch(LOCAL_INTEGRATION_PREFIX + path, Object.assign({
            credentials: 'same-origin', cache: 'no-store',
            headers: { Accept: 'application/json' },
        }, options || {}));
        var body = await response.json().catch(function () { return null; });
        return { response: response, body: body };
    }

    async function registerBackendOwner(characterUid, accountId) {
        var uid = text(characterUid);
        var account = normalizeAccountId(accountId);
        if (!UID_RE.test(uid)) throw new Error('pet_care_role_alias_character_invalid');
        var result = await localIntegrationRequest('/role-owners', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({ character_uid: uid, account_id: account }),
        });
        if (!result.response.ok || !result.body || result.body.status !== 'remembered') {
            throw new Error(responseCode(result.body) || 'pet_care_role_alias_owner_outbox_failed');
        }
        return true;
    }

    async function readBackendRetirements(accountId) {
        var result = await localIntegrationRequest('/role-alias-retirements', {
            headers: {
                Accept: 'application/json',
                'X-Neko-Expected-Account': normalizeAccountId(accountId),
            },
        });
        if (!result.response.ok || !result.body || !Array.isArray(result.body.events)) {
            throw new Error(responseCode(result.body) || 'pet_care_role_alias_outbox_read_failed');
        }
        return result.body.events.map(normalizeRetirement);
    }

    async function acknowledgeBackendRetirement(accountId, retirement) {
        var result = await localIntegrationRequest('/role-alias-retirements/ack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({
                account_id: normalizeAccountId(accountId),
                source_revision: normalizeRetirement(retirement).source_revision,
            }),
        });
        if (!result.response.ok || !result.body ||
            ['acknowledged', 'absent'].indexOf(result.body.status) < 0) {
            throw new Error(responseCode(result.body) || 'pet_care_role_alias_outbox_ack_failed');
        }
        return true;
    }

    function apiRequest(url, options) {
        var api = global.NekoPetCareApi;
        if (!api || typeof api.request !== 'function') {
            return Promise.reject(new Error('pet_care_role_alias_transport_unavailable'));
        }
        return api.request(url, options || {}, { timeoutMs: 15000 });
    }

    async function captureAccount() {
        if (typeof global.fetch !== 'function') {
            throw new Error('pet_care_role_alias_auth_transport_unavailable');
        }
        // Account state belongs to this N.E.K.O instance and is read through
        // its dedicated same-origin session endpoint, outside PetCare.
        var controller = typeof global.AbortController === 'function'
            ? new global.AbortController()
            : null;
        var timer = controller && typeof global.setTimeout === 'function'
            ? global.setTimeout(function () { controller.abort(); }, 5000)
            : 0;
        var response;
        try {
            response = await global.fetch('/api/card-drop/oauth/status', {
                credentials: 'same-origin',
                cache: 'no-store',
                headers: { Accept: 'application/json' },
                signal: controller ? controller.signal : undefined,
            });
        } finally {
            if (timer && typeof global.clearTimeout === 'function') {
                global.clearTimeout(timer);
            }
        }
        var body = await response.json().catch(function () { return null; });
        if (!response.ok || !body || body.logged_in !== true) return '';
        return normalizeAccountId(body.local_user_id);
    }

    function enqueueLocked(accountId, retirement) {
        accountId = normalizeAccountId(accountId);
        retirement = normalizeRetirement(retirement);
        var queue = readQueue();
        if (!Object.prototype.hasOwnProperty.call(queue.accounts, accountId) &&
            Object.keys(queue.accounts).length >= MAX_ACCOUNTS) {
            throw new Error('pet_care_role_alias_queue_full');
        }
        var rows = queue.accounts[accountId] || [];
        if (!rows.some(function (row) { return row.source_revision === retirement.source_revision; })) {
            if (rows.length >= MAX_RECORDS_PER_ACCOUNT) {
                throw new Error('pet_care_role_alias_queue_full');
            }
            rows.push(retirement);
            queue.accounts[accountId] = rows;
            writeQueue(queue);
        }
        return retirement;
    }

    function removeLocked(accountId, sourceRevision) {
        accountId = normalizeAccountId(accountId);
        sourceRevision = text(sourceRevision);
        var queue = readQueue();
        var rows = queue.accounts[accountId] || [];
        var remaining = rows.filter(function (row) { return row.source_revision !== sourceRevision; });
        if (remaining.length === rows.length) return;
        if (remaining.length) queue.accounts[accountId] = remaining;
        else delete queue.accounts[accountId];
        writeQueue(queue);
    }

    function responseCode(body) {
        return text(body && (body.code || body.detail || body.error));
    }

    async function send(accountId, retirement) {
        var response = await apiRequest('/api/pet-care/role-aliases/sync', {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: {
                'Content-Type': 'application/json',
                Accept: 'application/json',
                // This is an expectation, not account authority.  The local
                // gateway and Verse edge each compare it with their current
                // verified OAuth account.  Neither treats it as authority.
                'X-Neko-Expected-Account': accountId,
            },
            body: JSON.stringify(retirement),
        });
        var body;
        try { body = await response.json(); } catch (_) {
            throw new Error('pet_care_role_alias_response_invalid');
        }
        var code = responseCode(body);
        if (response.status === 404 && code === 'pet_care_role_alias_unknown') {
            // Current authorities create an immutable namespace tombstone for
            // retire-before-bind. This exact legacy response therefore proves
            // that the older authority has no alias to retire; keeping it in
            // the lifecycle queue would retry forever on every focus.
            await withStateLock(function () {
                removeLocked(accountId, retirement.source_revision);
            });
            acknowledgeBackendRetirement(accountId, retirement).catch(function () {});
            return { status: 'absent', character_uid: retirement.character_uid };
        }
        if (!response.ok || !body || body.status !== 'retired' ||
            body.character_uid !== retirement.character_uid ||
            body.source_revision !== retirement.source_revision) {
            throw new Error(code || 'pet_care_role_alias_sync_failed');
        }
        await withStateLock(function () {
            removeLocked(accountId, retirement.source_revision);
        });
        // A failed acknowledgement leaves the backend event ready. Its later
        // replay is harmless because the authority receipt is idempotent.
        acknowledgeBackendRetirement(accountId, retirement).catch(function () {});
        return body;
    }

    async function retire(retirement, accountId) {
        var normalized = normalizeRetirement(retirement);
        // Deleting an arbitrary local character must not create PetCare
        // server state. Only aliases recorded by a verified bind attempt are
        // eligible for retirement; the live account selects which remembered
        // owner may be sent immediately, but it cannot invent ownership.
        var verifiedOwner = text(accountId) ? normalizeAccountId(accountId) : '';
        var owners = await withStateLock(function () {
            var matchedOwners = ownersForCharacter(normalized.character_uid);
            if (!matchedOwners.length) return [];
            matchedOwners.forEach(function (owner) { enqueueLocked(owner, normalized); });
            return matchedOwners;
        });
        if (!owners.length) return { status: 'alias-untracked', queued: false };
        // Only the account captured from the live OAuth session may be sent
        // immediately. Other remembered owners remain queued until that same
        // account is verified again by flush().
        if (!verifiedOwner || owners.indexOf(verifiedOwner) < 0) {
            return { status: 'queued', queued: true };
        }
        return send(verifiedOwner, normalized);
    }

    async function flush() {
        if (flushing) {
            // Focus/pageshow/auth can arrive while an earlier pass still sees
            // the old session. Coalesce them into one trailing pass instead of
            // dropping the only event that observes the new account.
            flushQueued = true;
            return flushing;
        }
        var operation = (async function () {
            if (!lockManager() || typeof lockManager().request !== 'function') {
                throw new Error('pet_care_role_alias_lock_unavailable');
            }
            var queue = readQueue();
            var hasPending = Object.keys(queue.accounts).some(function (account) {
                return queue.accounts[account].length > 0;
            });
            var hasOwners = Object.keys(readOwners()).length > 0;
            if (!hasPending && !hasOwners) return { status: 'idle', attempted: 0 };
            var accountId = await captureAccount();
            if (!accountId) return { status: 'not-authenticated', attempted: 0 };
            try {
                var durableRows = await readBackendRetirements(accountId);
                if (durableRows.length) {
                    await withStateLock(function () {
                        durableRows.forEach(function (row) { enqueueLocked(accountId, row); });
                    });
                }
            } catch (error) {
                if (!hasPending) throw error;
            }
            queue = readQueue();
            var rows = (queue.accounts[accountId] || []).slice();
            var attempted = 0;
            for (var index = 0; index < rows.length; index += 1) {
                attempted += 1;
                try { await send(accountId, rows[index]); } catch (_) { break; }
            }
            return { status: 'processed', attempted: attempted };
        })();
        flushing = operation;
        operation.finally(function () {
            if (flushing === operation) flushing = null;
            if (flushQueued) {
                flushQueued = false;
                Promise.resolve().then(flushQuietly);
            }
        }).catch(function () {
            // The original operation is returned to the caller. This branch
            // only handles the internal finally-chain promise.
        });
        return flushing;
    }

    function flushQuietly() {
        flush().catch(function (error) {
            try { console.warn('[PetCare] 角色绑定退役同步待重试:', error && error.message); } catch (_) {}
        });
    }

    global.NekoPetCareRoleAliasSync = Object.freeze({
        captureAccount: captureAccount,
        rememberOwner: rememberOwner,
        registerBackendOwner: registerBackendOwner,
        ownersForCharacter: ownersForCharacter,
        retire: retire,
        flush: flush,
    });

    if (typeof global.addEventListener === 'function') {
        global.addEventListener('focus', flushQuietly);
        global.addEventListener('pageshow', flushQuietly);
        global.addEventListener('neko:auth-session-change', flushQuietly);
    }
    if (global.document && global.document.readyState === 'loading' &&
        typeof global.document.addEventListener === 'function') {
        global.document.addEventListener('DOMContentLoaded', flushQuietly, { once: true });
    } else {
        Promise.resolve().then(flushQuietly);
    }
})(window);
