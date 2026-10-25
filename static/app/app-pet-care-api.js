/**
 * PetCare transport adapter.
 *
 * This module deliberately knows only how to issue a request to N.E.K.O's
 * same-origin PetCare gateway.  Account resolution, Verse authentication,
 * installation proof, state rules, rewards and presentation stay in their
 * owning layers, so deployment topology never enters the browser contract.
 */
(function (global) {
    'use strict';

    var DEFAULT_TIMEOUT_MS = 15000;
    var LOCAL_CAPABILITY_URL = '/api/pet-care/local-capability';
    var LOCAL_CAPABILITY_HEADER = 'X-Neko-Pet-Care-Capability';

    function copyHeaders(headers) {
        if (!headers || typeof headers !== 'object') return {};
        if (typeof headers.forEach === 'function') {
            var normalized = {};
            headers.forEach(function (value, key) {
                normalized[key] = value;
            });
            return normalized;
        }
        if (Array.isArray(headers)) {
            return headers.reduce(function (result, entry) {
                if (Array.isArray(entry) && entry.length >= 2) {
                    result[entry[0]] = entry[1];
                }
                return result;
            }, {});
        }
        return Object.assign({}, headers);
    }

    function idempotencyKeyFromOptions(requestOptions) {
        var headers = copyHeaders(requestOptions && requestOptions.headers);
        var headerKey = headers && (headers['Idempotency-Key'] || headers['idempotency-key']);
        if (typeof headerKey === 'string' && headerKey.trim()) return headerKey.trim();
        var explicit = requestOptions && (requestOptions.idempotencyKey || requestOptions.idempotency_key);
        if (typeof explicit === 'string' && explicit.trim()) return explicit.trim();
        var body = requestOptions && requestOptions.body;
        if (typeof body !== 'string' || !body.trim()) return '';
        try {
            var parsed = JSON.parse(body);
            var key = parsed && (parsed.idempotency_key || parsed.idempotencyKey);
            return typeof key === 'string' && key.trim() ? key.trim() : '';
        } catch (_) {
            return '';
        }
    }

    function prepareRequestOptions(options) {
        var requestOptions = Object.assign({}, options || {});
        // Unified-account cookies terminate at the N.E.K.O gateway.  Keep the
        // credential mode fixed so a caller cannot silently turn this adapter
        // into a direct community-service transport.
        requestOptions.credentials = 'same-origin';
        requestOptions.headers = copyHeaders(requestOptions.headers);
        var key = idempotencyKeyFromOptions(requestOptions);
        if (key) {
            if (!requestOptions.headers['Idempotency-Key'] &&
                !requestOptions.headers['idempotency-key']) {
                requestOptions.headers['Idempotency-Key'] = key;
            }
        }
        // These are transport guarantees.  A caller may replace Accept for a
        // different protocol, but ordinary PetCare calls should advertise the
        // JSON response contract without repeating it at every call site.
        requestOptions.headers = copyHeaders(requestOptions.headers);
        if (!requestOptions.headers.Accept && !requestOptions.headers.accept) {
            requestOptions.headers.Accept = 'application/json';
        }
        return requestOptions;
    }

    function requestMethod(requestOptions) {
        return String(requestOptions && requestOptions.method || 'GET').trim().toUpperCase();
    }

    function sameOriginGatewayUrl(value) {
        var path = typeof value === 'string' ? value : String(value || '');
        if (path.indexOf('/api/pet-care/') !== 0 || path.indexOf('#') >= 0 ||
            path.indexOf('\\') >= 0 || path.indexOf('//') >= 0) {
            return '';
        }
        var pathname = path.split('?', 1)[0];
        if (pathname.split('/').some(function (segment) {
            return /^(?:\.|%2e){1,2}$/i.test(segment);
        })) {
            return '';
        }
        return path;
    }

    function localGatewayPath(value) {
        var path = sameOriginGatewayUrl(value);
        if (!path || path.indexOf('?') >= 0 || path.charAt(path.length - 1) === '/') {
            return '';
        }
        return path;
    }

    function sha256Hex(value) {
        var cryptoApi = global.crypto;
        var Encoder = global.TextEncoder;
        if (!cryptoApi || !cryptoApi.subtle || typeof cryptoApi.subtle.digest !== 'function' ||
            typeof Encoder !== 'function') {
            return Promise.reject(capabilityError(
                0, 'pet-care-local-capability-crypto-unavailable'));
        }
        var bytes = new Encoder().encode(value);
        return Promise.resolve(cryptoApi.subtle.digest('SHA-256', bytes)).then(function (digest) {
            return Array.prototype.map.call(new Uint8Array(digest), function (byte) {
                return byte.toString(16).padStart(2, '0');
            }).join('');
        });
    }

    function capabilityError(status, code) {
        var error = new Error(code || 'pet-care-local-capability-unavailable');
        error.code = code || 'pet-care-local-capability-unavailable';
        error.status = Number(status) || 0;
        return error;
    }

    function issueLocalCapability(fetchImpl, capabilityUrl, gatewayPath, body, requestOptions) {
        if (typeof body !== 'string' || !body) {
            return Promise.reject(capabilityError(0, 'pet-care-local-capability-body-invalid'));
        }
        return sha256Hex(body).then(function (bodySha256) {
            var separator = capabilityUrl.indexOf('?') >= 0 ? '&' : '?';
            var url = capabilityUrl + separator +
                'method=POST&path=' + encodeURIComponent(gatewayPath) +
                '&body_sha256=' + encodeURIComponent(bodySha256);
            return fetchImpl(url, {
                method: 'GET',
                credentials: requestOptions.credentials || 'same-origin',
                cache: 'no-store',
                signal: requestOptions.signal,
                headers: { Accept: 'application/json' },
            });
        }).then(function (response) {
            return Promise.resolve(response && typeof response.json === 'function'
                ? response.json() : {}).catch(function () { return {}; }).then(function (payload) {
                if (!response || !response.ok) {
                    throw capabilityError(response && response.status,
                        payload && (payload.code || payload.detail));
                }
                var capability = payload && typeof payload.capability === 'string'
                    ? payload.capability.trim() : '';
                if (!/^[A-Za-z0-9_-]{32,256}$/.test(capability)) {
                    throw capabilityError(response.status,
                        'pet-care-local-capability-response-invalid');
                }
                return capability;
            });
        });
    }

    function request(url, options, settings, transportOptions) {
        var config = settings && typeof settings === 'object' ? settings : {};
        var transport = transportOptions && typeof transportOptions === 'object'
            ? transportOptions : {};
        var gatewayUrl = sameOriginGatewayUrl(url);
        if (!gatewayUrl) {
            return Promise.reject(capabilityError(0, 'pet-care-gateway-path-invalid'));
        }
        var controller = typeof AbortController === 'function' ? new AbortController() : null;
        var requestOptions = prepareRequestOptions(options);
        var method = requestMethod(requestOptions);
        var capabilityUrl = transport.localCapability === false ? '' : LOCAL_CAPABILITY_URL;
        var capabilityPath = localGatewayPath(gatewayUrl);
        var timeoutMs = Number(config.timeoutMs);
        if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) timeoutMs = DEFAULT_TIMEOUT_MS;
        var timer = 0;
        if (controller) {
            requestOptions.signal = controller.signal;
            timer = global.setTimeout(function () { controller.abort(); }, timeoutMs);
        }
        return Promise.resolve()
            .then(function () {
                var fetchImpl = typeof transport.fetch === 'function'
                    ? transport.fetch : global.fetch;
                if (typeof fetchImpl !== 'function') {
                    throw new Error('pet-care-fetch-unavailable');
                }
                if (method !== 'POST' || !capabilityUrl) {
                    return fetchImpl(gatewayUrl, requestOptions);
                }
                if (!capabilityPath) {
                    throw capabilityError(0, 'pet-care-local-capability-path-invalid');
                }
                return issueLocalCapability(
                    fetchImpl,
                    capabilityUrl,
                    capabilityPath,
                    requestOptions.body,
                    requestOptions
                ).then(function (capability) {
                    requestOptions.headers = copyHeaders(requestOptions.headers);
                    Object.keys(requestOptions.headers).forEach(function (name) {
                        if (name.toLowerCase() === LOCAL_CAPABILITY_HEADER.toLowerCase()) {
                            delete requestOptions.headers[name];
                        }
                    });
                    requestOptions.headers[LOCAL_CAPABILITY_HEADER] = capability;
                    return fetchImpl(gatewayUrl, requestOptions);
                });
            })
            .finally(function () {
                if (timer) global.clearTimeout(timer);
            });
    }

    function petPath(characterUid, suffix) {
        var uid = encodeURIComponent(String(characterUid || ''));
        return '/api/pet-care/pets/' + uid + (suffix || '');
    }

    function createTransport(options) {
        var config = options && typeof options === 'object' ? options : {};
        var fetchImpl = typeof config.fetch === 'function' ? config.fetch : null;
        var localCapability = config.localCapability !== false;
        var transport = {
            DEFAULT_TIMEOUT_MS: DEFAULT_TIMEOUT_MS,
            petPath: petPath,
            request: function (url, requestOptions, settings) {
                var callSettings = settings && typeof settings === 'object' ? settings : {};
                return request(url, requestOptions, callSettings, {
                    fetch: fetchImpl,
                    localCapability: localCapability,
                });
            },
        };
        return Object.freeze(transport);
    }

    function parseJson(response) {
        return Promise.resolve(response).then(function (value) {
            if (!value || typeof value.json !== 'function') return {};
            return value.json();
        });
    }

    var defaultTransport = createTransport({});
    global.NekoPetCareApi = Object.freeze({
        DEFAULT_TIMEOUT_MS: DEFAULT_TIMEOUT_MS,
        request: defaultTransport.request,
        petPath: defaultTransport.petPath,
        createTransport: createTransport,
        parseJson: parseJson,
    });
})(window);
