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

    window.getDesktopCaptureProvider = function () {
        if (window.tauriDesktopCapturer) return window.tauriDesktopCapturer;
        if (window.electronDesktopCapturer) return window.electronDesktopCapturer;
        return null;
    };
})();
