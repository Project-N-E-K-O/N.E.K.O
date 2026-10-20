/**
 * Pet-care service client.
 *
 * This layer owns only the browser contract for the pet-care HTTP API.  It
 * does not resolve the N.E.K.O account, bind a role, calculate values, or
 * update the panel.  A future community transport can replace the transport
 * adapter without making the UI know where the service is deployed.
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
            if (transport && typeof transport.petPath === 'function') {
                return transport.petPath(uid, suffix || '');
            }
            return '/api/pet-care/pets/' + encodeURIComponent(String(uid || '')) + (suffix || '');
        }

        function request(url, requestOptions) {
            if (transport && typeof transport.request === 'function') {
                return transport.request(url, requestOptions || {}, { timeoutMs: timeoutMs });
            }
            return global.fetch(url, requestOptions || {});
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
            bind: function (bindOptions) {
                return request('/api/pet-care/bind', bindOptions || {});
            },
            careAction: function (characterUid, requestOptions) {
                return petRequest('/care-actions', requestOptions, characterUid);
            },
            tasks: function (characterUid, requestOptions) {
                return petRequest('/tasks', requestOptions, characterUid);
            },
            claimTask: function (characterUid, taskId, requestOptions) {
                return petRequest('/tasks/' + encodeURIComponent(String(taskId || '')) + '/claim', requestOptions, characterUid);
            },
            dailyCheckIn: function (characterUid, requestOptions) {
                return petRequest('/daily-check-in', requestOptions, characterUid);
            },
            debugFillStatus: function (characterUid, requestOptions) {
                return petRequest('/debug/fill-status', requestOptions, characterUid);
            },
            exploration: function (characterUid, suffix, requestOptions) {
                return petRequest('/explorations' + (suffix || ''), requestOptions, characterUid);
            },
        });
    }

    global.NekoPetCareClient = Object.freeze({ create: createClient });
})(window);
