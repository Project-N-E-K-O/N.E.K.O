/**
 * PetCare transport adapter.
 *
 * This module deliberately knows only how to issue a request.  Account
 * resolution, role binding, state rules, rewards and presentation stay in
 * their owning layers.  The local API and the future community API therefore
 * share one browser contract without making the panel aware of deployment.
 */
(function (global) {
    'use strict';

    var DEFAULT_TIMEOUT_MS = 15000;

    function request(url, options, settings) {
        var config = settings && typeof settings === 'object' ? settings : {};
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var requestOptions = Object.assign({}, options || {});
        var timeoutMs = Number(config.timeoutMs);
        if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) timeoutMs = DEFAULT_TIMEOUT_MS;
        var timer = 0;
        if (controller) {
            requestOptions.signal = controller.signal;
            timer = global.setTimeout(function () { controller.abort(); }, timeoutMs);
        }
        return Promise.resolve()
            .then(function () { return global.fetch(url, requestOptions); })
            .finally(function () {
                if (timer) global.clearTimeout(timer);
            });
    }

    function petPath(characterUid, suffix) {
        var uid = encodeURIComponent(String(characterUid || ''));
        return '/api/pet-care/pets/' + uid + (suffix || '');
    }

    function parseJson(response) {
        return Promise.resolve(response).then(function (value) {
            if (!value || typeof value.json !== 'function') return {};
            return value.json();
        });
    }

    global.NekoPetCareApi = Object.freeze({
        DEFAULT_TIMEOUT_MS: DEFAULT_TIMEOUT_MS,
        request: request,
        petPath: petPath,
        parseJson: parseJson,
    });
})(window);
