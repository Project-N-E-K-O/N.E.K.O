/**
 * Lightweight runtime loader for the transparent card-maker embed.
 * It resolves the active model type first, then loads only that provider.
 */
(function () {
    'use strict';

    const params = new URLSearchParams(window.location.search);
    const characterName = params.get('name') || params.get('lanlan_name') || '';
    const bootstrapSrc = document.currentScript?.src || '';
    const assetVersion = new URL(bootstrapSrc, window.location.href).searchParams.get('v') || '0';
    const versioned = (src) => `${src}${src.includes('?') ? '&' : '?'}v=${encodeURIComponent(assetVersion)}`;

    function notifyFailure() {
        if (window.parent === window) return;
        window.parent.postMessage({
            type: 'neko-card-maker-embed',
            status: 'error'
        }, '*');
    }

    function loadClassicScript(src) {
        const baseSrc = src.split('?')[0];
        if (document.querySelector(`script[src^="${baseSrc}"]`)) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = versioned(src);
            script.onload = resolve;
            script.onerror = () => reject(new Error(`Failed to load ${src}`));
            (document.head || document.documentElement).appendChild(script);
        });
    }

    async function loadScriptsInOrder(sources) {
        for (const src of sources) await loadClassicScript(src);
    }

    async function ensureThree() {
        if (window.THREE) return;
        window.THREE = await import('three');
        window.dispatchEvent(new CustomEvent('three-ready'));
    }

    async function loadLive2DRuntime() {
        await loadScriptsInOrder([
            '/static/libs/live2dcubismcore.min.js',
            '/static/libs/live2d.min.js',
            '/static/libs/pixi.min.js',
            '/static/libs/index.min.js',
            '/static/live2d/live2d-core.js',
            '/static/live2d/live2d-emotion.js',
            '/static/live2d/live2d-model.js'
        ]);
    }

    async function loadVRMRuntime() {
        await ensureThree();
        await loadClassicScript('/static/vrm/vrm-init.js');
    }

    async function loadMMDRuntime() {
        await ensureThree();
        await loadClassicScript('/static/mmd/mmd-init.js');
    }

    async function loadPNGTuberRuntime() {
        await loadClassicScript('/static/pngtuber-core.js');
    }

    function effectiveModelType(config) {
        const modelType = String(config?.model_type || 'live2d').trim().toLowerCase();
        if (modelType === 'pngtuber') return 'pngtuber';
        if (modelType === 'vrm') return 'vrm';
        if (modelType === 'live3d') {
            return String(config?.live3d_sub_type || '').trim().toLowerCase() === 'mmd'
                ? 'mmd'
                : 'vrm';
        }
        return 'live2d';
    }

    async function loadRuntime(config) {
        const loaders = {
            live2d: loadLive2DRuntime,
            vrm: loadVRMRuntime,
            mmd: loadMMDRuntime,
            pngtuber: loadPNGTuberRuntime
        };
        await loaders[effectiveModelType(config)]();
    }

    const configPromise = fetch(`/api/config/page_config?lanlan_name=${encodeURIComponent(characterName)}`)
        .then((response) => {
            if (!response.ok) throw new Error(`page_config HTTP ${response.status}`);
            return response.json();
        })
        .then((config) => {
            if (!config?.success) throw new Error(config?.error || 'Invalid page_config response');
            return config;
        });
    window.__NEKO_CARD_MAKER_CONFIG_PROMISE__ = configPromise;

    (async () => {
        try {
            const config = await configPromise;
            await loadRuntime(config);
            await loadClassicScript('/static/js/card_maker.js');
        } catch (error) {
            console.error('[card_maker embed] bootstrap failed:', error);
            notifyFailure();
        }
    })();
})();
