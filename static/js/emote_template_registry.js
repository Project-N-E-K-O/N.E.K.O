/*
 * Renderer-neutral template registry.
 *
 * Package formats are registered separately from their rendering engines.
 * The registry does not load binary assets.  A renderer owns that decision.
 */
(function (root) {
    'use strict';

    const providers = new Map();

    function register(provider) {
        if (!provider || !provider.id || typeof provider.accept !== 'function') {
            throw new TypeError('A template provider needs an id and accept()');
        }
        providers.set(provider.id, provider);
        return provider;
    }

    function resolve(packageManifest) {
        for (const provider of providers.values()) {
            if (provider.accept(packageManifest)) return provider;
        }
        return null;
    }

    root.NekoEmoteTemplateRegistry = Object.freeze({
        resolve
    });

    register({
        id: 'spine',
        accept: (manifest) => manifest?.schemaVersion === 2 && manifest?.source?.kind === 'spine',
    });
}(window));
