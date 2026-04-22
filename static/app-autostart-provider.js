(function () {
    'use strict';

    const DESKTOP_PROVIDER_NAME = 'neko-pc';
    const UNSUPPORTED_MECHANISM = 'desktop-bridge-unavailable';
    const AUTOSTART_NOT_SUPPORTED_ERROR = 'autostart_not_supported';

    function getNavigatorPlatform() {
        if (navigator.userAgentData && navigator.userAgentData.platform) {
            return String(navigator.userAgentData.platform);
        }
        if (navigator.platform) {
            return String(navigator.platform);
        }
        return 'unknown';
    }

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

    function buildUnsupportedDesktopResult(overrides) {
        return normalizeResult(Object.assign({
            ok: true,
            supported: false,
            enabled: false,
            authoritative: true,
            provider: DESKTOP_PROVIDER_NAME,
            mechanism: UNSUPPORTED_MECHANISM,
            platform: getNavigatorPlatform(),
        }, overrides || {}), getDesktopDefaults());
    }

    function getStatus() {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return Promise.resolve(buildUnsupportedDesktopResult());
        }

        return Promise.resolve().then(function () {
            return bridge.getStatus();
        }).then(function (result) {
            return normalizeResult(result, getDesktopDefaults());
        });
    }

    function enable() {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return Promise.resolve(buildUnsupportedDesktopResult({
                ok: false,
                error: AUTOSTART_NOT_SUPPORTED_ERROR,
                error_code: AUTOSTART_NOT_SUPPORTED_ERROR,
            }));
        }

        return Promise.resolve().then(function () {
            return bridge.enable();
        }).then(function (result) {
            return normalizeResult(result, getDesktopDefaults());
        });
    }

    function disable() {
        const bridge = getDesktopBridge();
        if (!bridge) {
            return Promise.resolve(buildUnsupportedDesktopResult({
                ok: false,
                error: AUTOSTART_NOT_SUPPORTED_ERROR,
                error_code: AUTOSTART_NOT_SUPPORTED_ERROR,
            }));
        }
        if (typeof bridge.disable !== 'function') {
            // 和无桥接分支保持对象契约一致：老版本 PC 暴露 enable 但没 disable 时，
            // 调用方同样能按 provider 响应对象路径处理，不会被迫走 .catch。
            return Promise.resolve(buildUnsupportedDesktopResult({
                ok: false,
                error: AUTOSTART_NOT_SUPPORTED_ERROR,
                error_code: AUTOSTART_NOT_SUPPORTED_ERROR,
            }));
        }

        return Promise.resolve().then(function () {
            return bridge.disable();
        }).then(function (result) {
            return normalizeResult(result, getDesktopDefaults());
        });
    }

    const existingProvider = window.nekoAutostartProvider;
    if (
        existingProvider
        && typeof existingProvider.getStatus === 'function'
        && typeof existingProvider.enable === 'function'
    ) {
        return;
    }

    window.nekoAutostartProvider = {
        getStatus: getStatus,
        enable: enable,
        disable: disable,
    };
})();
