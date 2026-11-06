/**
 * Durable PetCare write registry.
 *
 * The registry contains only the minimum request identity needed to ask the
 * service about an ambiguous write again.  It deliberately stores no URL,
 * credential, proof, snapshot, inventory, reward, or presentation state.
 * Callers must dispatch a record through their fixed operation-kind map.
 */
(function (global) {
    'use strict';

    var STORAGE_KEY = 'neko.pet-care.pending-writes.v1';
    var ACCOUNT_STORAGE_PREFIX = 'neko.pet-care.pending-write-account.v1:';
    var INSTANCE_KEY = 'neko.pet-care.client-instance.v1';
    var VERSION = 1;
    var MAX_STRING = 4096;
    var OPERATION_SCHEMAS = Object.freeze({
        'session-resume': Object.freeze({
            body: Object.freeze({ idempotency_key: 'string' }),
            target: Object.freeze({ auth_fence: 'string' }),
            required: Object.freeze(['idempotency_key']),
            targetRequired: Object.freeze(['auth_fence']),
        }),
        'bind-create': Object.freeze({
            body: Object.freeze({ character_uid: 'string', idempotency_key: 'string' }),
            required: Object.freeze(['character_uid', 'idempotency_key']),
        }),
        'bind-rebind': Object.freeze({
            body: Object.freeze({
                character_uid: 'string', idempotency_key: 'string', expected_pet_id: 'string',
                expected_binding_revision: 'integer', expected_state_version: 'integer',
            }),
            required: Object.freeze(['character_uid', 'idempotency_key', 'expected_pet_id',
                'expected_binding_revision', 'expected_state_version']),
        }),
        'care-action': Object.freeze({
            body: Object.freeze({
                category: 'string', content_id: 'string', idempotency_key: 'string',
                expected_state_version: 'integer', expected_config_revision: 'string',
            }),
            required: Object.freeze(['category', 'content_id', 'idempotency_key',
                'expected_state_version', 'expected_config_revision']),
        }),
        'task-claim': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer', cycle_key: 'string',
            }),
            target: Object.freeze({ task_id: 'string' }),
            required: Object.freeze(['idempotency_key', 'expected_state_version', 'cycle_key']),
            targetRequired: Object.freeze(['task_id']),
        }),
        // Reserved for the server-authoritative daily work action. Keeping it
        // in the same lane prevents that endpoint from growing another retry
        // store when its UI is connected.
        'task-complete': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer', cycle_key: 'string',
            }),
            target: Object.freeze({ task_id: 'string' }),
            required: Object.freeze(['idempotency_key', 'expected_state_version', 'cycle_key']),
            targetRequired: Object.freeze(['task_id']),
        }),
        'status-recovery': Object.freeze({
            body: Object.freeze({
                status: 'string', task_id: 'string', cycle_key: 'string',
                idempotency_key: 'string', expected_state_version: 'integer',
            }),
            required: Object.freeze(['status', 'task_id', 'cycle_key', 'idempotency_key',
                'expected_state_version']),
        }),
        'play-lease-issue': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer', activity_id: 'string',
                content_id: 'string', source: 'string', expected_config_revision: 'string',
            }),
            required: Object.freeze(['idempotency_key', 'expected_state_version', 'activity_id',
                'content_id', 'source', 'expected_config_revision']),
        }),
        'play-lease-complete': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer', activity_id: 'string',
                terminal_status: 'string',
            }),
            target: Object.freeze({ lease_id: 'string' }),
            required: Object.freeze(['idempotency_key', 'expected_state_version', 'activity_id',
                'terminal_status']),
            targetRequired: Object.freeze(['lease_id']),
        }),
        'exploration-start': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer', location_id: 'string',
                expected_config_revision: 'string',
            }),
            required: Object.freeze(['idempotency_key', 'expected_state_version', 'location_id',
                'expected_config_revision']),
        }),
        'exploration-complete': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer',
            }),
            target: Object.freeze({ exploration_id: 'string' }),
            required: Object.freeze(['idempotency_key', 'expected_state_version']),
            targetRequired: Object.freeze(['exploration_id']),
        }),
        'exploration-recall': Object.freeze({
            body: Object.freeze({
                idempotency_key: 'string', expected_state_version: 'integer',
            }),
            target: Object.freeze({ exploration_id: 'string' }),
            required: Object.freeze(['idempotency_key', 'expected_state_version']),
            targetRequired: Object.freeze(['exploration_id']),
        }),
    });

    function own(object, key) {
        return !!object && Object.prototype.hasOwnProperty.call(object, key);
    }
    function text(value) {
        return typeof value === 'string' ? value.trim() : '';
    }
    function safeString(value, optional) {
        var result = text(value);
        if (!result && optional) return '';
        if (!result || result.length > MAX_STRING || /[\u0000-\u001f\u007f]/.test(result)) {
            throw new Error('pet_care_pending_write_invalid');
        }
        return result;
    }
    function positiveInteger(value) {
        var number = Number(value);
        if (!Number.isInteger(number) || number < 1) {
            throw new Error('pet_care_pending_write_invalid');
        }
        return number;
    }
    function normalizeObject(value, schema, required) {
        var input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
        var allowed = schema || {};
        Object.keys(input).forEach(function (key) {
            if (!own(allowed, key)) throw new Error('pet_care_pending_write_invalid');
        });
        (required || []).forEach(function (key) {
            if (!own(input, key)) throw new Error('pet_care_pending_write_invalid');
        });
        var result = {};
        Object.keys(allowed).forEach(function (key) {
            if (!own(input, key)) return;
            var type = allowed[key];
            if (type === 'integer') result[key] = positiveInteger(input[key]);
            else if (type === 'string?') result[key] = safeString(input[key], true);
            else result[key] = safeString(input[key], false);
        });
        return result;
    }
    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }
    function stable(value) {
        if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
        if (value && typeof value === 'object') {
            return '{' + Object.keys(value).sort().map(function (key) {
                return JSON.stringify(key) + ':' + stable(value[key]);
            }).join(',') + '}';
        }
        return JSON.stringify(value);
    }
    function codeFromBody(body) {
        if (!body || typeof body !== 'object') return '';
        var operation = body.operation_result || body.operation ||
            (body.result && (body.result.operation_result || body.result.operation));
        return text(operation && (operation.code || operation.reason || operation.detail)) ||
            text(body.code || body.detail || body.reason);
    }
    function operationStatus(body) {
        if (!body || typeof body !== 'object') return '';
        var operation = body.operation_result || body.operation ||
            (body.result && (body.result.operation_result || body.result.operation));
        return text(operation && operation.status).toLowerCase();
    }
    function normalizedCode(value) {
        return text(value).toLowerCase().replace(/-/g, '_').replace(/^pet_care_/, '');
    }

    function create(options) {
        var config = options && typeof options === 'object' ? options : {};
        var storage = config.storage || global.localStorage;
        var now = typeof config.now === 'function' ? config.now : Date.now;
        var lockManager = config.locks || (global.navigator && global.navigator.locks);
        var heldAccountLocks = Object.create(null);
        var volatileCleared = Object.create(null);

        function accountIdFromInput(input) {
            return safeString(input && (input.account_id || input.accountId), false);
        }
        function requireAccountLock(accountId) {
            if (!heldAccountLocks[accountId]) {
                throw new Error('pet_care_pending_write_lock_required');
            }
        }
        function withAccountLock(accountId, lockOptions, callback) {
            accountId = safeString(accountId, false);
            var optionsValue = lockOptions && typeof lockOptions === 'object' ? lockOptions : {};
            if (typeof lockOptions === 'function') {
                callback = lockOptions;
                optionsValue = {};
            }
            if (typeof callback !== 'function') {
                return Promise.reject(new Error('pet_care_pending_write_invalid'));
            }
            if (!lockManager || typeof lockManager.request !== 'function') {
                return Promise.reject(new Error('pet_care_pending_write_lock_unavailable'));
            }
            if (heldAccountLocks[accountId]) {
                return Promise.reject(new Error('pet_care_pending_write_lock_reentrant'));
            }
            var requestOptions = { mode: 'exclusive' };
            if (optionsValue.ifAvailable === true) requestOptions.ifAvailable = true;
            var requested;
            try {
                requested = lockManager.request(
                    'neko.pet-care.pending-write.v1:' + accountId,
                    requestOptions,
                    function (lock) {
                        if (!lock) {
                            throw new Error(optionsValue.ifAvailable === true
                                ? 'pet_care_write_pending'
                                : 'pet_care_pending_write_lock_unavailable');
                        }
                        migrateLegacyAccount(accountId);
                        heldAccountLocks[accountId] = true;
                        var result;
                        try {
                            result = callback();
                        } catch (error) {
                            delete heldAccountLocks[accountId];
                            throw error;
                        }
                        return Promise.resolve(result).finally(function () {
                            delete heldAccountLocks[accountId];
                        });
                    }
                );
            } catch (error) {
                return Promise.reject(error);
            }
            return Promise.resolve(requested);
        }

        function storageGet(key) {
            if (!storage || typeof storage.getItem !== 'function') {
                throw new Error('pet_care_pending_write_storage_unavailable');
            }
            return storage.getItem(key);
        }
        function storageSet(key, value) {
            if (!storage || typeof storage.setItem !== 'function') {
                throw new Error('pet_care_pending_write_storage_unavailable');
            }
            storage.setItem(key, value);
            if (storageGet(key) !== value) throw new Error('pet_care_pending_write_storage_failed');
        }
        function storageRemove(key) {
            if (!storage || typeof storage.removeItem !== 'function') return false;
            storage.removeItem(key);
            return storageGet(key) == null;
        }
        function accountStorageKey(accountId) {
            return ACCOUNT_STORAGE_PREFIX + encodeURIComponent(safeString(accountId, false));
        }
        function clientInstanceId() {
            var existing = text(storageGet(INSTANCE_KEY));
            if (existing && existing.length <= 200 && /^[A-Za-z0-9._~-]+$/.test(existing)) return existing;
            var created = '';
            try {
                if (global.crypto && typeof global.crypto.randomUUID === 'function') {
                    created = global.crypto.randomUUID();
                }
            } catch (_) {}
            if (!created) {
                created = 'pet-client-' + Number(now()).toString(36) + '-' +
                    Math.random().toString(36).slice(2);
            }
            created = safeString(created, false).slice(0, 200);
            storageSet(INSTANCE_KEY, created);
            return created;
        }
        function emptyContainer() {
            return { version: VERSION, accounts: {} };
        }
        function readContainer() {
            var raw = storageGet(STORAGE_KEY);
            if (!raw) return emptyContainer();
            var value;
            try { value = JSON.parse(raw); } catch (_) {
                throw new Error('pet_care_pending_write_storage_corrupt');
            }
            if (!value || value.version !== VERSION || !value.accounts ||
                typeof value.accounts !== 'object' || Array.isArray(value.accounts)) {
                throw new Error('pet_care_pending_write_storage_corrupt');
            }
            return value;
        }
        function readAccountEnvelope(accountId) {
            var raw = storageGet(accountStorageKey(accountId));
            if (raw == null) return { present: false, record: null };
            var value;
            try { value = JSON.parse(raw); } catch (_) {
                throw new Error('pet_care_pending_write_storage_corrupt');
            }
            if (!value || value.version !== VERSION || !own(value, 'record') ||
                Object.keys(value).some(function (key) {
                    return key !== 'version' && key !== 'record';
                }) || (value.record != null &&
                    (typeof value.record !== 'object' || Array.isArray(value.record)))) {
                throw new Error('pet_care_pending_write_storage_corrupt');
            }
            return { present: true, record: value.record };
        }
        function writeAccountRecord(accountId, record) {
            storageSet(accountStorageKey(accountId), stable({
                version: VERSION,
                record: record == null ? null : record,
            }));
        }
        function migrateLegacyAccount(accountId) {
            accountId = safeString(accountId, false);
            var envelope = readAccountEnvelope(accountId);
            var legacy = readContainer();
            if (!envelope.present && legacy.accounts[accountId]) {
                writeAccountRecord(accountId, normalizeRecord(legacy.accounts[accountId], true));
            }
            var legacyAccounts = Object.keys(legacy.accounts);
            if (legacyAccounts.length && legacyAccounts.every(function (legacyAccountId) {
                return storageGet(accountStorageKey(legacyAccountId)) != null;
            })) {
                storageRemove(STORAGE_KEY);
            }
        }
        function normalizeRecord(input, existing) {
            if (!input || typeof input !== 'object' || Array.isArray(input)) {
                throw new Error('pet_care_pending_write_invalid');
            }
            var recordKeys = {
                version: true, operation_id: true, kind: true, phase: true,
                account_id: true, accountId: true,
                character_uid: true, characterUid: true,
                bound_role_id: true, boundRoleId: true,
                pet_id: true, petId: true,
                client_instance_id: true, idempotency_key: true,
                target: true, body: true, created_at: true,
                last_attempt_at: true, attempt_count: true, quarantine_reason: true,
            };
            Object.keys(input).forEach(function (key) {
                if (!recordKeys[key]) throw new Error('pet_care_pending_write_invalid');
            });
            var kind = safeString(input.kind, false);
            var schema = OPERATION_SCHEMAS[kind];
            if (!schema) throw new Error('pet_care_pending_write_kind_invalid');
            var body = normalizeObject(input.body, schema.body, schema.required);
            var target = normalizeObject(input.target, schema.target || {}, schema.targetRequired || []);
            var accountId = safeString(input.account_id || input.accountId, false);
            var idempotencyKey = safeString(body.idempotency_key, false);
            if (input.idempotency_key != null && safeString(input.idempotency_key, false) !== idempotencyKey) {
                throw new Error('pet_care_pending_write_invalid');
            }
            var phase = input.phase == null ? 'pending' : safeString(input.phase, false);
            if (phase !== 'pending' && phase !== 'quarantined') {
                throw new Error('pet_care_pending_write_invalid');
            }
            var createdAt = existing ? Number(input.created_at) : Number(now());
            var lastAttemptAt = Number(input.last_attempt_at || 0);
            var attemptCount = Number(input.attempt_count || 0);
            if (!Number.isFinite(createdAt) || createdAt <= 0 || !Number.isFinite(lastAttemptAt) ||
                lastAttemptAt < 0 || !Number.isInteger(attemptCount) || attemptCount < 0) {
                throw new Error('pet_care_pending_write_invalid');
            }
            var accountOnly = kind === 'session-resume';
            var characterUid = safeString(input.character_uid || input.characterUid, accountOnly);
            var boundRoleId = safeString(input.bound_role_id || input.boundRoleId, true);
            var petId = safeString(input.pet_id || input.petId, true);
            if (accountOnly && (characterUid || boundRoleId || petId)) {
                throw new Error('pet_care_pending_write_invalid');
            }
            if (!accountOnly && kind !== 'bind-create' && kind !== 'bind-rebind' &&
                (!boundRoleId || !petId)) {
                throw new Error('pet_care_pending_write_invalid');
            }
            if (kind === 'bind-rebind' && (!petId || petId !== body.expected_pet_id)) {
                throw new Error('pet_care_pending_write_invalid');
            }
            if ((kind === 'bind-create' || kind === 'bind-rebind') && body.character_uid !== characterUid) {
                throw new Error('pet_care_pending_write_invalid');
            }
            return {
                version: VERSION,
                operation_id: safeString(input.operation_id || (kind + ':' + idempotencyKey), false),
                kind: kind,
                phase: phase,
                account_id: accountId,
                character_uid: characterUid,
                bound_role_id: boundRoleId,
                pet_id: petId,
                client_instance_id: safeString(input.client_instance_id || clientInstanceId(), false),
                idempotency_key: idempotencyKey,
                target: target,
                body: body,
                created_at: createdAt,
                last_attempt_at: lastAttemptAt,
                attempt_count: attemptCount,
                quarantine_reason: phase === 'quarantined'
                    ? safeString(input.quarantine_reason, false) : '',
            };
        }
        function readAccountRecord(accountId) {
            accountId = safeString(accountId, false);
            var envelope = readAccountEnvelope(accountId);
            var raw = envelope.present ? envelope.record : readContainer().accounts[accountId];
            if (!raw) return null;
            if (volatileCleared[accountId] && volatileCleared[accountId] === text(raw.operation_id)) return null;
            return normalizeRecord(raw, true);
        }
        function current(accountId) {
            return clone(readAccountRecord(accountId));
        }
        function begin(input) {
            requireAccountLock(accountIdFromInput(input));
            var candidate = normalizeRecord(input, false);
            var existing = readAccountRecord(candidate.account_id);
            if (existing) {
                if (stable(existing) === stable(candidate)) return clone(existing);
                throw new Error(existing.phase === 'quarantined'
                    ? 'pet_care_pending_write_quarantined' : 'pet_care_write_pending');
            }
            writeAccountRecord(candidate.account_id, candidate);
            var persisted = readAccountRecord(candidate.account_id);
            if (!persisted || stable(persisted) !== stable(candidate)) {
                throw new Error('pet_care_pending_write_storage_failed');
            }
            return clone(persisted);
        }
        function prepare(accountId, operationId) {
            accountId = safeString(accountId, false);
            requireAccountLock(accountId);
            var record = readAccountRecord(accountId);
            if (!record || record.operation_id !== safeString(operationId, false) || record.phase !== 'pending') {
                throw new Error('pet_care_pending_write_missing');
            }
            record.attempt_count += 1;
            record.last_attempt_at = Number(now());
            writeAccountRecord(accountId, record);
            var persisted = readAccountRecord(accountId);
            if (!persisted || stable(persisted) !== stable(record)) {
                throw new Error('pet_care_pending_write_storage_failed');
            }
            return clone(persisted);
        }
        function complete(accountId, operationId) {
            accountId = safeString(accountId, false);
            requireAccountLock(accountId);
            var record = readAccountRecord(accountId);
            if (!record) return true;
            if (record.operation_id !== safeString(operationId, false)) return false;
            try {
                writeAccountRecord(accountId, null);
                var cleared = readAccountRecord(accountId) == null;
                if (cleared) delete volatileCleared[accountId];
                return cleared;
            } catch (_) {
                // The business response is already terminal. Never replay it
                // in this renderer merely because localStorage refused cleanup.
                volatileCleared[accountId] = record.operation_id;
                return false;
            }
        }
        function quarantine(accountId, operationId, reason) {
            accountId = safeString(accountId, false);
            requireAccountLock(accountId);
            var record = readAccountRecord(accountId);
            if (!record || record.operation_id !== safeString(operationId, false)) {
                throw new Error('pet_care_pending_write_missing');
            }
            record.phase = 'quarantined';
            record.quarantine_reason = safeString(reason || 'idempotency_key_reused', false);
            writeAccountRecord(accountId, record);
            return current(accountId);
        }
        function validResumeEnvelope(body) {
            if (!body || typeof body !== 'object' || Array.isArray(body)) return false;
            var requestId = text(body.request_id);
            var serverTime = Date.parse(text(body.server_time));
            var resumeNotBefore = Date.parse(text(body.resume_not_before));
            return !!requestId && requestId.length <= MAX_STRING &&
                Number.isFinite(serverTime) && Number.isFinite(resumeNotBefore) &&
                typeof body.needs_binding === 'boolean' &&
                typeof body.pet_refresh_required === 'boolean';
        }
        function classify(response, body, parseError, kind) {
            var status = Number(response && response.status);
            if (!Number.isFinite(status) || status <= 0) {
                if (response && response.ok === true) status = 200;
                else if (response && response.ok === false) status = 400;
            }
            var code = normalizedCode(codeFromBody(body));
            var operation = operationStatus(body);
            if (code === 'idempotency_key_reused' || code === 'idempotency_key_conflict') {
                return 'quarantine';
            }
            if (code === 'operation_in_progress' || operation === 'in_progress' || operation === 'pending') {
                return 'pending';
            }
            if (Number.isFinite(status) && status >= 500) return 'pending';
            // A truncated or non-JSON response cannot prove that a request
            // was rejected, including 408/409/429 paths. Keep the exact
            // request identity until a parseable terminal envelope arrives.
            if (parseError) return 'pending';
            if (!Number.isFinite(status) || status <= 0) return 'pending';
            if (status >= 400 && status < 500) return 'rejected';
            if (operation === 'rejected') return 'rejected';
            // HTTP delivery alone does not prove that a mutation committed.
            // A syntactically valid but incomplete 2xx envelope must retain
            // the exact key/body for reconciliation, just like truncated JSON.
            if (status >= 200 && status < 300 && kind === 'session-resume' &&
                validResumeEnvelope(body)) return 'confirmed';
            if (status >= 200 && status < 300 && operation === 'confirmed') return 'confirmed';
            return 'pending';
        }

        return Object.freeze({
            current: current,
            withAccountLock: withAccountLock,
            begin: begin,
            prepare: prepare,
            complete: complete,
            quarantine: quarantine,
            classify: classify,
        });
    }

    global.NekoPetCarePendingWrites = Object.freeze({
        STORAGE_KEY: STORAGE_KEY,
        ACCOUNT_STORAGE_PREFIX: ACCOUNT_STORAGE_PREFIX,
        INSTANCE_KEY: INSTANCE_KEY,
        KINDS: Object.freeze(Object.keys(OPERATION_SCHEMAS)),
        create: create,
    });
})(window);
