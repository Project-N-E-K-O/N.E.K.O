/** Persistent display avatars. Model captures and their IPC/cache remain independent. */
(function () {
    'use strict';

    const api = {};
    const UID = /^[0-9a-f]{32}$/;
    const REQUEST_TIMEOUT_MS = 15000;
    const CREDENTIAL_WAIT_MS = 3000;
    const TIMED_OUT = {};
    const DISPLAYED_LIMIT = 64;
    let identity = null;
    let epoch = 0;
    let record = null;
    let limits = null;
    let error = null;
    let readGeneration = 0;
    let initialized = false;
    let switchOwner = null;
    let bootstrapGeneration = 0;
    let lateConfigRetry = false;
    const cancelledEdits = new WeakSet();
    const submittedEdits = new WeakSet();
    // Cancelled while a send was pending; cleared by every new send of the edit.
    const cancelledInFlight = new WeakSet();
    // Custom images shown on this page, so message fallbacks can tell them from model captures.
    const displayedDataUrls = new Set();

    function failure(code, status) {
        const result = new Error(code);
        result.code = code;
        result.status = status || 0;
        return result;
    }

    function emit(reason) {
        window.dispatchEvent(new CustomEvent('chat-avatar-display-updated', {
            detail: {
                character_uid: identity && identity.uid,
                revision: record && record.revision,
                dataUrl: record && record.data_url || '',
                identityEpoch: epoch,
                reason: reason,
                error: error && error.code || null
            }
        }));
    }

    function matches(binding) {
        return !!identity && identity.uid === binding.uid && epoch === binding.identityEpoch;
    }

    function endpoint(uid) {
        return '/api/characters/by-uid/' + encodeURIComponent(uid) + '/chat-avatar';
    }

    function operationId() {
        if (typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
        // Remote HTTP deployments do not expose randomUUID, but getRandomValues remains available.
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        return Array.from(bytes, function (byte) { return byte.toString(16).padStart(2, '0'); }).join('');
    }

    async function request(uid, options) {
        const controller = new AbortController();
        const timer = window.setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
        try {
            const response = await fetch(endpoint(uid), Object.assign({
                credentials: 'same-origin', cache: 'no-store'
            }, options || {}, { signal: controller.signal }));
            let body;
            try { body = await response.json(); }
            catch (cause) {
                // A response body can fail after the server has committed and sent successful headers.
                if (cause && (cause.name === 'AbortError' || cause.name === 'TypeError')) throw cause;
                body = null;
            }
            if (!response.ok) {
                // An absent route is not an unset avatar or a deleted character.
                throw failure(body && (body.code || body.error_code || body.error) || 'chat_avatar_unavailable', response.status);
            }
            if (!body || body.character_uid !== uid || typeof body.revision !== 'string'
                || !(body.data_url === null || typeof body.data_url === 'string')) {
                throw failure('chat_avatar_invalid_response');
            }
            return body;
        } catch (cause) {
            if (cause && typeof cause.code === 'string') throw cause;
            throw failure(cause && cause.name === 'AbortError' ? 'chat_avatar_timeout' : 'chat_avatar_network_error');
        } finally {
            window.clearTimeout(timer);
        }
    }

    function accept(body, reason) {
        record = body;
        if (body.data_url) {
            displayedDataUrls.delete(body.data_url);
            displayedDataUrls.add(body.data_url);
            if (displayedDataUrls.size > DISPLAYED_LIMIT) displayedDataUrls.delete(displayedDataUrls.values().next().value);
        }
        limits = body.limits || limits;
        error = null;
        emit(reason);
    }

    api.getIdentity = function () {
        return identity && { uid: identity.uid, name: identity.name, identityEpoch: epoch };
    };
    api.getRecord = function () { return record && Object.assign({}, record); };
    api.getLimits = function () { return limits && Object.assign({}, limits); };
    api.getError = function () { return error; };
    api.getDataUrl = function () { return record && record.data_url || ''; };
    api.isCustomDataUrl = function (url) { return !!url && displayedDataUrls.has(url); };
    api.captureEdit = function () {
        if (!identity || !record || error) throw failure('chat_avatar_unavailable');
        return {
            uid: identity.uid,
            identityEpoch: epoch,
            baseRevision: record.revision,
            operationId: operationId()
        };
    };
    api.isCurrent = matches;
    api.cancelEdit = function (binding) {
        if (!binding) return;
        // Once sent, a write may already be committed. Keep confirming its outcome.
        if (submittedEdits.has(binding)) cancelledInFlight.add(binding);
        else cancelledEdits.add(binding);
    };

    api.refresh = async function (reason) {
        if (!identity) {
            if (initialized && !switchOwner && reason !== 'identity') return bootstrap();
            return null;
        }
        const binding = api.getIdentity();
        const generation = ++readGeneration;
        try {
            const body = await request(binding.uid);
            if (!matches(binding) || generation !== readGeneration) return null;
            accept(body, reason || 'read');
            return body;
        } catch (cause) {
            if (matches(binding) && generation === readGeneration) {
                error = cause;
                emit('read-error');
            }
            throw cause;
        }
    };

    api.setIdentity = function (next) {
        ++bootstrapGeneration;
        ++epoch;
        ++readGeneration;
        identity = next && UID.test(next.uid) ? { uid: next.uid, name: next.name || '' } : null;
        record = null;
        limits = null;
        error = identity ? null : failure('chat_avatar_unavailable');
        emit('identity');
        return api.refresh('identity').catch(function () { return null; });
    };

    api.beginCharacterSwitch = function (attemptId) {
        // A newer switch keeps the last committed identity as its rollback target.
        const previous = switchOwner ? switchOwner.previous : { identity: identity, record: record, limits: limits, error: error };
        const token = { attemptId: attemptId, previous: previous };
        switchOwner = token;
        ++bootstrapGeneration;
        ++epoch;
        ++readGeneration;
        identity = null;
        record = null;
        error = null;
        emit('switch-start');
        return token;
    };

    function ownsSwitch(owner) {
        return switchOwner && (owner === switchOwner || owner === switchOwner.attemptId);
    }

    api.commitCharacterSwitch = function (owner, next) {
        if (!ownsSwitch(owner)) return Promise.resolve(null);
        switchOwner = null;
        return api.setIdentity(next);
    };
    api.rollbackCharacterSwitch = function (owner) {
        if (!ownsSwitch(owner)) return;
        const previous = switchOwner.previous;
        switchOwner = null;
        ++epoch;
        ++readGeneration;
        identity = previous.identity;
        record = previous.record;
        limits = previous.limits;
        error = previous.error;
        emit('switch-rollback');
        api.refresh('rollback').catch(function () {});
    };

    api.onBackendChanged = function (detail) {
        if (!identity || !detail || detail.character_uid !== identity.uid) return;
        if (record && record.revision === detail.revision) return;
        api.refresh('notification').catch(function () {});
    };
    api.onChanged = api.onBackendChanged;

    function withDeadline(promise, ms) {
        let timer;
        const deadline = new Promise(function (resolve) {
            timer = window.setTimeout(function () { resolve(TIMED_OUT); }, ms);
        });
        return Promise.race([promise, deadline]).finally(function () { window.clearTimeout(timer); });
    }

    async function mutationHeaders(binding, method, refresh) {
        const security = window.nekoLocalMutationSecurity;
        function fence() {
            if (!matches(binding) || cancelledEdits.has(binding)) throw failure('chat_avatar_stale_edit');
        }
        if (refresh) {
            try { await withDeadline(security.refreshToken(), REQUEST_TIMEOUT_MS); }
            catch (_) { /* The resend reports the guard's verdict. */ }
        }
        let headers = await withDeadline(security.getMutationHeaders(), CREDENTIAL_WAIT_MS);
        if (headers === TIMED_OUT) {
            // The first token read waits on page config without a deadline, and the page may have
            // gone on without it. Fetch the token directly; a fetched token is cached for the headers.
            let token = '';
            try { token = await withDeadline(security.refreshToken(), REQUEST_TIMEOUT_MS); }
            catch (_) { /* Reported below. */ }
            fence();
            if (token && token !== TIMED_OUT) headers = await withDeadline(security.getMutationHeaders(), CREDENTIAL_WAIT_MS);
            if (headers === TIMED_OUT) throw failure('chat_avatar_credentials_unavailable');
        }
        fence();
        if (method !== 'PUT') headers['Content-Type'] = 'application/json';
        return headers;
    }

    async function write(method, blob, binding) {
        if (!matches(binding) || cancelledEdits.has(binding)) throw failure('chat_avatar_stale_edit');
        let body;
        if (method === 'PUT') {
            body = new FormData();
            body.append('image', blob, 'avatar.png');
            body.append('base_revision', binding.baseRevision);
            body.append('operation_id', binding.operationId);
        } else {
            body = JSON.stringify({ base_revision: binding.baseRevision, operation_id: binding.operationId });
        }
        let generation;
        function submit(headers) {
            generation = readGeneration;
            cancelledInFlight.delete(binding);
            submittedEdits.add(binding);
            return request(binding.uid, { method: method, body: body, headers: headers });
        }
        let saved;
        try {
            try {
                saved = await submit(await mutationHeaders(binding, method, false));
            } catch (cause) {
                if (cause.status !== 403 || cause.code !== 'csrf_validation_failed') throw cause;
                // The guard rejects before the handler runs, so nothing was written. A restarted
                // backend rotates the token: refresh it once and resend the same operation.
                // The resend is a new send, so a cancel made while the first was pending applies.
                submittedEdits.delete(binding);
                if (cancelledInFlight.has(binding)) cancelledEdits.add(binding);
                saved = await submit(await mutationHeaders(binding, method, true));
            }
        } catch (cause) {
            if (!matches(binding)) throw failure('chat_avatar_stale_edit');
            if (['chat_avatar_timeout', 'chat_avatar_network_error', 'chat_avatar_invalid_response'].includes(cause.code)) {
                let current;
                try { current = await api.refresh('reconcile'); }
                catch (_) { throw failure('chat_avatar_unknown_outcome'); }
                if (!matches(binding)) throw failure('chat_avatar_stale_edit');
                if (current && current.last_operation_id === binding.operationId) return current;
                // A late commit may still be running. Never replay from this failure path.
                throw failure('chat_avatar_unknown_outcome');
            }
            if (cause.status === 409) await api.refresh('conflict').catch(function () {});
            throw cause;
        }
        if (!matches(binding)) return saved;
        if (generation !== readGeneration) {
            // A notification/read overtook this response; opaque revisions cannot be ordered.
            let current;
            try { current = await api.refresh('write-confirm'); }
            catch (_) { throw failure('chat_avatar_unknown_outcome'); }
            if (!matches(binding)) throw failure('chat_avatar_stale_edit');
            if (!current) throw failure('chat_avatar_unknown_outcome');
            if (current.last_operation_id !== binding.operationId) throw failure('chat_avatar_conflict', 409);
            return current;
        }
        ++readGeneration;
        accept(saved, method === 'PUT' ? 'saved' : 'restored');
        return saved;
    }

    api.save = function (blob, binding) { return write('PUT', blob, binding); };
    api.restore = function (binding) { return write('DELETE', null, binding || api.captureEdit()); };

    function waitForPageConfig() {
        const ready = window.pageConfigReady;
        if (!ready || typeof ready.then !== 'function') return Promise.resolve();
        let timer;
        const deadline = new Promise(function (_, reject) {
            timer = window.setTimeout(function () {
                if (!lateConfigRetry) {
                    // The rest of the page goes on without page config; finish once it lands.
                    lateConfigRetry = true;
                    const retry = function () { if (!identity && !switchOwner) bootstrap(); };
                    ready.then(retry, retry);
                }
                reject(failure('chat_avatar_timeout'));
            }, REQUEST_TIMEOUT_MS);
        });
        return Promise.race([ready, deadline]).finally(function () { window.clearTimeout(timer); });
    }

    async function bootstrap() {
        const generation = ++bootstrapGeneration;
        try {
            await waitForPageConfig();
            if (generation !== bootstrapGeneration || switchOwner) return;
            const controller = new AbortController();
            const timer = window.setTimeout(function () { controller.abort(); }, REQUEST_TIMEOUT_MS);
            let config;
            try {
                // Only the UID is read: skip persona translation, which may call an LLM.
                const response = await fetch('/api/characters?language=zh-CN', {
                    credentials: 'same-origin', cache: 'no-store', signal: controller.signal
                });
                if (!response.ok) throw failure('chat_avatar_read_failed', response.status);
                config = await response.json();
            } finally {
                window.clearTimeout(timer);
            }
            if (generation !== bootstrapGeneration || switchOwner) return;
            const name = window.lanlan_config && window.lanlan_config.lanlan_name;
            const character = config && config['猫娘'] && config['猫娘'][name];
            const uid = character && character._reserved && character._reserved.character_uid;
            if (!UID.test(uid || '')) throw failure('chat_avatar_character_not_found', 404);
            await api.setIdentity({ uid: uid, name: name });
        } catch (cause) {
            if (generation === bootstrapGeneration) {
                error = cause && cause.name === 'AbortError' ? failure('chat_avatar_timeout') : cause;
                emit('bootstrap-error');
            }
        }
    }

    api.initialize = function () {
        if (initialized) return;
        initialized = true;
        bootstrap();
        window.addEventListener('focus', function () {
            if (identity) api.refresh('focus').catch(function () {});
            else if (!switchOwner) bootstrap();
        });
        window.addEventListener('pageshow', function () {
            if (identity) api.refresh('pageshow').catch(function () {});
        });
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible' && identity) api.refresh('visible').catch(function () {});
        });
        window.addEventListener('neko:config-injected', function () {
            if (!switchOwner) bootstrap();
        });
    };

    window.appChatAvatarState = api;
})();
