(function () {
    'use strict';

    const DESKTOP_PROVIDER_NAME = 'neko-pc';
    const BACKEND_PROVIDER_NAME = 'backend';
    const AUTOSTART_CSRF_HEADER_NAME = 'X-CSRF-Token';
    const AUTOSTART_STATUS_EVENT_NAME = 'neko:autostart-provider-status';
    const DESKTOP_STATUS_EVENT_NAME = 'neko:autostart-status-changed';
    const PAGE_CONFIG_PATH = '/api/config/page_config';
    const RESERVED_PATH_PREFIXES = ['focus', 'api', 'static', 'templates'];

    let cachedPageConfig = null;
    let hasLoadedPageConfig = false;
    let pageConfigLoadRequest = null;
    let pageConfigRefreshRequest = null;

    function getNavigatorPlatform() {
        if (navigator.userAgentData && navigator.userAgentData.platform) {
            return String(navigator.userAgentData.platform);
        }
        if (navigator.platform) {
            return String(navigator.platform);
        }
        return 'unknown';
    }

    function rememberPageConfig(pageConfig) {
        cachedPageConfig = pageConfig && typeof pageConfig === 'object' ? pageConfig : null;
        hasLoadedPageConfig = true;
        return cachedPageConfig;
    }

    function getPageConfigLanlanName() {
        try {
            const urlParams = new URLSearchParams(window.location.search);
            const lanlanName = urlParams.get('lanlan_name');
            if (lanlanName) {
                return lanlanName;
            }
        } catch (_) {
            // Ignore malformed URLs and fall back to cached config below.
        }

        if (
            window.lanlan_config
            && typeof window.lanlan_config === 'object'
            && typeof window.lanlan_config.lanlan_name === 'string'
            && window.lanlan_config.lanlan_name
        ) {
            return window.lanlan_config.lanlan_name;
        }

        try {
            const pathParts = window.location.pathname.split('/').filter(Boolean);
            if (pathParts.length > 0 && RESERVED_PATH_PREFIXES.indexOf(pathParts[0]) === -1) {
                return decodeURIComponent(pathParts[0]);
            }
        } catch (_) {
            // Ignore invalid path decoding and fall back to root page_config.
        }

        return '';
    }

    function buildPageConfigUrl() {
        const lanlanName = getPageConfigLanlanName();
        if (!lanlanName) {
            return PAGE_CONFIG_PATH;
        }
        return PAGE_CONFIG_PATH + '?lanlan_name=' + encodeURIComponent(lanlanName);
    }

    function waitForPageConfig(options) {
        const requestOptions = options || {};
        if (requestOptions.forceRefresh) {
            return refreshPageConfig();
        }

        if (hasLoadedPageConfig) {
            return Promise.resolve(cachedPageConfig);
        }
        if (pageConfigLoadRequest) {
            return pageConfigLoadRequest;
        }

        if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
            let request = null;
            request = Promise.resolve(window.pageConfigReady).then(function (pageConfig) {
                return rememberPageConfig(pageConfig);
            }).catch(function () {
                return rememberPageConfig(null);
            }).finally(function () {
                if (pageConfigLoadRequest === request) {
                    pageConfigLoadRequest = null;
                }
            });
            pageConfigLoadRequest = request;
            return request;
        }
        hasLoadedPageConfig = true;
        return Promise.resolve(cachedPageConfig);
    }

    function refreshPageConfig() {
        if (pageConfigRefreshRequest) {
            return pageConfigRefreshRequest;
        }

        let request = null;
        request = fetch(buildPageConfigUrl(), {
            cache: 'no-store',
        }).then(function (response) {
            if (!response || !response.ok) {
                return null;
            }
            return response.json().catch(function () {
                return null;
            });
        }).then(function (pageConfig) {
            if (
                pageConfig
                && typeof pageConfig === 'object'
                && pageConfig.success === true
            ) {
                return rememberPageConfig(pageConfig);
            }
            return rememberPageConfig(null);
        }).catch(function () {
            return rememberPageConfig(null);
        }).finally(function () {
            if (pageConfigRefreshRequest === request) {
                pageConfigRefreshRequest = null;
            }
        });

        window.pageConfigReady = request;
        pageConfigRefreshRequest = request;
        return request;
    }

    function getAutostartCsrfTokenValue(pageConfig) {
        if (
            pageConfig
            && typeof pageConfig === 'object'
            && typeof pageConfig.autostart_csrf_token === 'string'
            && pageConfig.autostart_csrf_token
        ) {
            return pageConfig.autostart_csrf_token;
        }
        return '';
    }

    function getAutostartCsrfToken(options) {
        return waitForPageConfig(options).then(function (pageConfig) {
            return getAutostartCsrfTokenValue(pageConfig);
        });
    }

    function getAutostartMutationHeaders(options) {
        return getAutostartCsrfToken(options).then(function (csrfToken) {
            if (!csrfToken) {
                return {};
            }
            const headers = {};
            headers[AUTOSTART_CSRF_HEADER_NAME] = csrfToken;
            return headers;
        });
    }

    function refreshAutostartMutationHeaders() {
        return getAutostartMutationHeaders({ forceRefresh: true });
    }

    function shouldRetryMutationRequest(error) {
        return !!(
            error
            && Number(error.status) === 403
            && error.error_code === 'csrf_validation_failed'
        );
    }

    const mutationSecurityApi = {
        getCsrfToken: getAutostartCsrfToken,
        getMutationHeaders: getAutostartMutationHeaders,
        refreshMutationHeaders: refreshAutostartMutationHeaders,
        shouldRetryRequest: shouldRetryMutationRequest,
    };
    window.nekoAutostartSecurity = mutationSecurityApi;
    window.nekoLocalMutationSecurity = mutationSecurityApi;

    let lastKnownStatus = null;

    // Desktop shells can inject window.nekoAutostart with getStatus/enable/disable methods.
    function getDesktopBridge() {
        const bridge = window.nekoAutostart;
        if (!bridge || typeof bridge !== 'object') {
            return null;
        }
        if (typeof bridge.getStatus !== 'function' || typeof bridge.enable !== 'function') {
            return null;
        }
        return bridge;
    }

    function normalizeResult(result, defaults) {
        const normalized = Object.assign({}, defaults);
        if (result && typeof result === 'object') {
            Object.assign(normalized, result);
        }

        normalized.provider = String(normalized.provider || defaults.provider || '');
        normalized.platform = String(normalized.platform || defaults.platform || getNavigatorPlatform());
        normalized.mechanism = String(normalized.mechanism || defaults.mechanism || '');
        normalized.supported = normalized.supported !== false;
        normalized.enabled = normalized.enabled === true;
        normalized.authoritative = normalized.authoritative === true;
        if (typeof normalized.ok !== 'boolean') {
            normalized.ok = true;
        }
        if (!normalized.supported) {
            normalized.enabled = false;
        }
        return normalized;
    }

    function emitStatusChanged(status, source) {
        lastKnownStatus = status && typeof status === 'object'
            ? Object.assign({}, status)
            : null;

        try {
            window.dispatchEvent(new CustomEvent(AUTOSTART_STATUS_EVENT_NAME, {
                detail: {
                    status: lastKnownStatus,
                    source: source || '',
                },
            }));
        } catch (_) {
            // Ignore dispatch failures in non-browser contexts.
        }

        return lastKnownStatus;
    }

    function rememberStatus(status, source) {
        return emitStatusChanged(status, source);
    }

    function getCachedStatus() {
        return lastKnownStatus ? Object.assign({}, lastKnownStatus) : null;
    }

    function getBackendDefaults() {
        return {
            ok: true,
            supported: false,
            enabled: false,
            provider: BACKEND_PROVIDER_NAME,
            mechanism: 'backend-disabled',
            platform: getNavigatorPlatform(),
            authoritative: true,
        };
    }

    function getDesktopDefaults() {
        return {
            ok: true,
            supported: true,
            enabled: false,
            provider: DESKTOP_PROVIDER_NAME,
            mechanism: 'desktop-bridge',
            platform: getNavigatorPlatform(),
            authoritative: true,
        };
    }

    function buildBackendUnsupportedResult(overrides) {
        return normalizeResult(Object.assign({
            ok: true,
            supported: false,
            enabled: false,
            manageable: false,
            provider: BACKEND_PROVIDER_NAME,
            mechanism: 'backend-disabled',
            authoritative: true,
            platform: getNavigatorPlatform(),
            reason: 'backend_autostart_removed',
        }, overrides || {}), getBackendDefaults());
    }

    function getBackendStatus() {
        return Promise.resolve(buildBackendUnsupportedResult());
    }

    function enableBackendAutostart() {
        return Promise.resolve(buildBackendUnsupportedResult({
            ok: false,
            error_code: 'launch_command_unavailable',
            error: 'Autostart launch command is unavailable',
        }));
    }

    function disableBackendAutostart() {
        return Promise.resolve(buildBackendUnsupportedResult());
    }

    function isAvailable() {
        return true;
    }

    function callProviderAction(actionName, backendAction) {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return backendAction().then(function (result) {
                return rememberStatus(result, 'backend:' + actionName);
            });
        }

        if (typeof bridge[actionName] !== 'function') {
            return Promise.reject(new Error('autostart_' + actionName + '_not_supported'));
        }

        return Promise.resolve().then(function () {
            return bridge[actionName]();
        }).then(function (result) {
            return rememberStatus(normalizeResult(result, getDesktopDefaults()), 'desktop:' + actionName);
        });
    }

    function getStatus() {
        return callProviderAction('getStatus', getBackendStatus);
    }

    function enable() {
        return callProviderAction('enable', enableBackendAutostart);
    }

    function disable() {
        return callProviderAction('disable', disableBackendAutostart);
    }

    window.addEventListener(DESKTOP_STATUS_EVENT_NAME, function (event) {
        const detail = event && event.detail;
        if (!detail || typeof detail !== 'object') {
            return;
        }
        rememberStatus(
            normalizeResult(detail, getDesktopDefaults()),
            'desktop:event'
        );
    });

    const existingProvider = window.nekoAutostartProvider;
    if (
        existingProvider
        && typeof existingProvider.getStatus === 'function'
        && typeof existingProvider.enable === 'function'
    ) {
        if (typeof existingProvider.isAvailable !== 'function') {
            existingProvider.isAvailable = isAvailable;
        }
        if (typeof existingProvider.getCachedStatus !== 'function') {
            existingProvider.getCachedStatus = getCachedStatus;
        }
        if (!existingProvider.events || typeof existingProvider.events !== 'object') {
            existingProvider.events = {};
        }
        if (!existingProvider.events.STATUS_CHANGED) {
            existingProvider.events.STATUS_CHANGED = AUTOSTART_STATUS_EVENT_NAME;
        }
        return;
    }

    window.nekoAutostartProvider = {
        isAvailable: isAvailable,
        getStatus: getStatus,
        enable: enable,
        disable: disable,
        getCachedStatus: getCachedStatus,
        events: {
            STATUS_CHANGED: AUTOSTART_STATUS_EVENT_NAME,
        },
    };
})();
