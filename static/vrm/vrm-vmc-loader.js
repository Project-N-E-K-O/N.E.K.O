/**
 * Lazy VMC API facade.
 *
 * The full sender is intentionally not loaded until a caller explicitly uses
 * a control method. Disabled VRM pages therefore perform no VMC status polls,
 * timers, frame sampling, WebSocket work, or UDP setup.
 */
(function () {
    'use strict';

    if (window.vrmVmcSender) return;

    const loaderScriptSrc = document.currentScript && document.currentScript.src;
    const assetVersion = loaderScriptSrc
        ? (new URL(loaderScriptSrc, window.location.href).searchParams.get('v') || '1.0.1')
        : '1.0.1';
    let loadPromise = null;

    function loadSender() {
        if (window.vrmVmcSender !== facade) {
            return Promise.resolve(window.vrmVmcSender);
        }
        if (loadPromise) return loadPromise;

        loadPromise = new Promise(function (resolve, reject) {
            const script = document.createElement('script');
            script.src = `/static/vrm/vrm-vmc-sender.js?v=${encodeURIComponent(assetVersion)}`;
            script.dataset.nekoVmcSender = 'true';
            script.onload = function () {
                const api = window.vrmVmcSender;
                if (!api || api === facade) {
                    reject(new Error('VMC sender loaded without installing its API'));
                    return;
                }
                resolve(api);
            };
            script.onerror = function () {
                reject(new Error('Failed to load the VMC sender module'));
            };
            (document.head || document.body || document.documentElement).appendChild(script);
        }).catch(function (error) {
            loadPromise = null;
            console.error('[VRM-VMC] lazy sender load failed:', error);
            throw error;
        });
        return loadPromise;
    }

    const facade = {
        __isVmcLazyLoader: true,
        enable: async function (...args) {
            return (await loadSender()).enable(...args);
        },
        disable: async function (...args) {
            return (await loadSender()).disable(...args);
        },
        requestTPose: async function (...args) {
            return (await loadSender()).requestTPose(...args);
        },
        syncStatusFromBackend: async function (...args) {
            return (await loadSender()).syncStatusFromBackend(...args);
        },
        releaseVrm: function () { return false; },
        sample: function () {},
        suspendSampling: function () {},
        isEnabled: function () { return false; },
        getSendRateHz: function () { return 60; },
    };

    window.__NEKO_VMC_ACTIVE__ = false;
    window.vrmVmcSender = facade;
})();
