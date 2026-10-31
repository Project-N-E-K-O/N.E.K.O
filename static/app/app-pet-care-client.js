/**
 * Pet-care service client.
 *
 * This layer owns only the browser contract for the pet-care HTTP API.  It
 * does not resolve the N.E.K.O account, bind a role, calculate values, or
 * update the panel.  It always addresses the same-origin N.E.K.O gateway;
 * Verse routing and service deployment remain behind that boundary.
 */
(function (global) {
    'use strict';

    function createClient(options) {
        var config = options && typeof options === 'object' ? options : {};
        var transport = config.transport || global.NekoPetCareApi;
        var timeoutMs = Number(config.timeoutMs);
        if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
            timeoutMs = transport && Number(transport.DEFAULT_TIMEOUT_MS) > 0
                ? Number(transport.DEFAULT_TIMEOUT_MS) : 15000;
        }
        var defaultUid = config.characterUid == null ? '' : String(config.characterUid);

        function path(suffix, characterUid) {
            var uid = characterUid == null ? defaultUid : characterUid;
            if (!transport || typeof transport.petPath !== 'function') {
                throw new Error('pet-care-transport-unavailable');
            }
            return transport.petPath(uid, suffix || '');
        }

        function request(url, requestOptions) {
            if (!transport || typeof transport.request !== 'function') {
                return Promise.reject(new Error('pet-care-transport-unavailable'));
            }
            return transport.request(url, requestOptions || {}, { timeoutMs: timeoutMs });
        }

        function petRequest(suffix, requestOptions, characterUid) {
            return request(path(suffix, characterUid), requestOptions);
        }

        return Object.freeze({
            path: path,
            request: request,
            petRequest: petRequest,
            snapshot: function (characterUid) {
                return petRequest('', {
                    credentials: 'same-origin', cache: 'no-store',
                    headers: { Accept: 'application/json' },
                }, characterUid);
            },
            context: function (characterUid) {
                var uid = characterUid == null ? defaultUid : characterUid;
                return request('/api/pet-care/context?character_uid=' +
                    encodeURIComponent(String(uid || '')), {
                    credentials: 'same-origin', cache: 'no-store',
                    headers: { Accept: 'application/json' },
                });
            },
            bind: function (bindOptions) {
                return request('/api/pet-care/bind', bindOptions || {});
            },
            sessionResume: function (requestOptions) {
                return request('/api/pet-care/session/resume', requestOptions || {});
            },
            careAction: function (characterUid, requestOptions) {
                return petRequest('/care-actions', requestOptions, characterUid);
            },
            statusRecovery: function (characterUid, requestOptions) {
                return petRequest('/status-recovery', requestOptions, characterUid);
            },
            issuePlayLease: function (characterUid, requestOptions) {
                return petRequest('/play-leases', requestOptions, characterUid);
            },
            completePlayLease: function (characterUid, leaseId, requestOptions) {
                return petRequest('/play-leases/' + encodeURIComponent(String(leaseId || '')) +
                    '/complete', requestOptions, characterUid);
            },
            tasks: function (characterUid, requestOptions) {
                return petRequest('/tasks', requestOptions, characterUid);
            },
            claimTask: function (characterUid, taskId, requestOptions) {
                return petRequest('/tasks/' + encodeURIComponent(String(taskId || '')) + '/claim', requestOptions, characterUid);
            },
            completeTask: function (characterUid, taskId, requestOptions) {
                return petRequest('/tasks/' + encodeURIComponent(String(taskId || '')) + '/complete', requestOptions, characterUid);
            },
            startExploration: function (characterUid, requestOptions) {
                return petRequest('/explorations', requestOptions, characterUid);
            },
            explorationStatus: function (characterUid, explorationId, requestOptions) {
                return petRequest('/explorations/' + encodeURIComponent(String(explorationId || '')),
                    requestOptions, characterUid);
            },
            completeExploration: function (characterUid, explorationId, requestOptions) {
                return petRequest('/explorations/' + encodeURIComponent(String(explorationId || '')) +
                    '/complete', requestOptions, characterUid);
            },
            recallExploration: function (characterUid, explorationId, requestOptions) {
                return petRequest('/explorations/' + encodeURIComponent(String(explorationId || '')) +
                    '/recall', requestOptions, characterUid);
            },
        });
    }

    global.NekoPetCareClient = Object.freeze({ create: createClient });
})(window);
