/**
 * Resolve the desktop capture bridge exposed by the active desktop shell.
 *
 * Electron installs `window.electronDesktopCapturer` from its preload script.
 * Tauri injects `window.tauriDesktopCapturer` after navigating to the local
 * backend page. Consumers must resolve the provider at call time because the
 * Tauri bridge is not present while the startup document is loading.
 */
(function installDesktopCaptureProviderResolver() {
    'use strict';

    var DESKTOP_CAPTURE_TIMEOUT_MS = 3000;

    window.getDesktopCaptureProvider = function () {
        if (window.tauriDesktopCapturer) return window.tauriDesktopCapturer;
        if (window.electronDesktopCapturer) return window.electronDesktopCapturer;
        return null;
    };

    /**
     * Invoke a desktop capture bridge method with one shared timeout contract.
     *
     * Calling through the provider preserves Electron preload methods that rely
     * on `this`, while resolving the provider separately keeps the late-injected
     * Tauri bridge behavior unchanged. The underlying native request cannot be
     * cancelled, but its late result is detached after the bounded wait.
     */
    window.captureDesktopSourceWithTimeout = async function (
        provider,
        methodName,
        sourceId,
        options,
        timeoutMs
    ) {
        if (!provider || typeof provider[methodName] !== 'function') {
            throw new Error('Desktop capture method unavailable: ' + methodName);
        }

        var effectiveTimeoutMs = Number(timeoutMs);
        if (!Number.isFinite(effectiveTimeoutMs) || effectiveTimeoutMs <= 0) {
            effectiveTimeoutMs = DESKTOP_CAPTURE_TIMEOUT_MS;
        }

        var timeoutId = null;
        try {
            return await Promise.race([
                Promise.resolve().then(function () {
                    return provider[methodName](sourceId, options);
                }),
                new Promise(function (_, reject) {
                    timeoutId = setTimeout(function () {
                        var error = new Error('Desktop capture timeout: ' + methodName);
                        error.code = 'DESKTOP_CAPTURE_TIMEOUT';
                        reject(error);
                    }, effectiveTimeoutMs);
                })
            ]);
        } finally {
            if (timeoutId !== null) clearTimeout(timeoutId);
        }
    };
})();
