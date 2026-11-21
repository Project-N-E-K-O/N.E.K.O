/*
 * Optional Spine runtime bridge for imported Spine models.
 *
 * The runtime is deliberately not loaded at page startup.  Metadata import
 * remains the default path; callers that have chosen to try native preview
 * may call window.NekoSpineRuntime.load() explicitly.  The pinned IIFE is the
 * official @esotericsoftware/spine-webgl 4.3.13 bundle (which includes the
 * matching spine-core runtime).  Model assets are supplied by the user.
 */
(function (root) {
    'use strict';

    const RUNTIME_VERSION = '4.3.13';
    const SCRIPT_URL = '/static/libs/spine/spine-webgl-4.3.13.min.js';
    const REQUIRED_SYMBOLS = Object.freeze([
        'SkeletonJson',
        'SkeletonBinary',
        'TextureAtlas',
        'AtlasAttachmentLoader',
        'SpineCanvas'
    ]);

    let loadPromise = null;
    let loadedScript = null;

    function getRuntime() {
        return root && root.spine ? root.spine : null;
    }

    function detect() {
        const runtime = getRuntime();
        const missingSymbols = runtime
            ? REQUIRED_SYMBOLS.filter((name) => typeof runtime[name] !== 'function')
            : [...REQUIRED_SYMBOLS];
        const apiCompatible = Boolean(runtime) && missingSymbols.length === 0;
        const version = loadedScript?.dataset?.nekoSpineVersion || null;
        const versionCompatible = version === RUNTIME_VERSION;
        let reason = '';

        if (!runtime) {
            reason = 'Spine runtime is not loaded';
        } else if (!apiCompatible) {
            reason = `Spine runtime is missing: ${missingSymbols.join(', ')}`;
        } else if (!versionCompatible) {
            reason = `Spine runtime version is unverified (expected ${RUNTIME_VERSION})`;
        }

        return Object.freeze({
            available: apiCompatible && versionCompatible,
            apiCompatible,
            versionCompatible,
            loaded: Boolean(runtime),
            version,
            expectedVersion: RUNTIME_VERSION,
            scriptUrl: SCRIPT_URL,
            missingSymbols: Object.freeze(missingSymbols),
            reason
        });
    }

    function load(options = {}) {
        const current = detect();
        if (current.available) return Promise.resolve(current);
        if (loadPromise) return loadPromise;

        const document = root && root.document;
        if (!document || typeof document.createElement !== 'function') {
            return Promise.reject(new Error('Spine runtime requires a browser document'));
        }

        const source = options.src || SCRIPT_URL;
        if (typeof source !== 'string' || !source) {
            return Promise.reject(new Error('Spine runtime script URL is invalid'));
        }
        try {
            const base = document.baseURI || root.location?.href || 'http://localhost/';
            if (new URL(source, base).href !== new URL(SCRIPT_URL, base).href) {
                return Promise.reject(new Error('Spine runtime script URL is not the pinned local bundle'));
            }
        } catch (_) {
            return Promise.reject(new Error('Spine runtime script URL is invalid'));
        }

        const pending = new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.async = true;
            script.src = source;
            if (script.dataset) {
                // The marker is attached only to a script created by this
                // loader. A pre-existing global is never claimed as 4.3.13.
                script.dataset.nekoSpineRuntime = 'true';
                script.dataset.nekoSpineVersion = RUNTIME_VERSION;
            }
            if (options.nonce) script.nonce = String(options.nonce);

            script.onload = () => {
                loadedScript = script;
                const status = detect();
                if (!status.available) {
                    reject(new Error(status.reason || 'Spine runtime failed feature detection'));
                    return;
                }
                resolve(status);
            };
            script.onerror = () => {
                loadedScript = null;
                reject(new Error(`Unable to load Spine runtime: ${source}`));
            };

            const parent = document.head || document.body || document.documentElement;
            if (!parent || typeof parent.appendChild !== 'function') {
                reject(new Error('Spine runtime has no document insertion point'));
                return;
            }
            parent.appendChild(script);
        });

        const shared = pending.catch((error) => {
            if (loadPromise === shared) loadPromise = null;
            throw error;
        });
        loadPromise = shared;
        return shared;
    }

    root.NekoSpineRuntime = Object.freeze({
        version: RUNTIME_VERSION,
        scriptUrl: SCRIPT_URL,
        requiredSymbols: REQUIRED_SYMBOLS,
        getRuntime,
        detect,
        load
    });
}(typeof window !== 'undefined' ? window : globalThis));
