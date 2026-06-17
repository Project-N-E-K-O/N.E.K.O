(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialTargetGeometryRegistry = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DEFAULT_TARGET_GEOMETRY_ENTRIES = Object.freeze({
        'chat-input': Object.freeze({
            key: 'chat-input',
            externalKind: 'input',
            shape: 'rounded-rect',
            fallbackGroup: '',
            localSelectors: Object.freeze([
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="input"]',
                '#react-chat-window-root [data-compact-geometry-part="inputBody"]',
                '#react-chat-window-root .composer-input',
                '#text-input-area'
            ])
        }),
        'chat-capsule-input': Object.freeze({
            key: 'chat-capsule-input',
            externalKind: 'capsule-input',
            shape: 'rounded-rect',
            fallbackGroup: 'chat-input',
            localSelectors: Object.freeze([
                '#react-chat-window-root [data-compact-geometry-part="capsuleBody"]',
                '#react-chat-window-root [data-compact-geometry-owner="surface"][data-compact-geometry-item="capsule"]',
                '#react-chat-window-root [data-compact-geometry-part="inputBody"]',
                '#react-chat-window-root .composer-input-shell',
                '#text-input-area'
            ])
        }),
        'chat-history-handle': Object.freeze({
            key: 'chat-history-handle',
            externalKind: 'history',
            shape: 'rounded-rect',
            fallbackGroup: '',
            localSelectors: Object.freeze([
                '#react-chat-window-root .compact-history-visibility-handle',
                '.compact-history-visibility-handle'
            ])
        }),
        'chat-tool-toggle': Object.freeze({
            key: 'chat-tool-toggle',
            externalKind: 'tool-toggle',
            shape: 'circle',
            fallbackGroup: '',
            localSelectors: Object.freeze([
                '#react-chat-window-root .send-button-circle.compact-input-tool-toggle',
                '.send-button-circle.compact-input-tool-toggle'
            ])
        }),
        'chat-avatar-tools': Object.freeze({
            key: 'chat-avatar-tools',
            externalKind: 'avatar-tools',
            shape: 'rounded-rect',
            fallbackGroup: 'chat-tool-toggle',
            localSelectors: Object.freeze([
                '#react-chat-window-root .compact-input-tool-item-avatar > .composer-emoji-btn',
                '#react-chat-window-root .compact-input-tool-item-avatar'
            ])
        }),
        'chat-avatar-tool-items': Object.freeze({
            key: 'chat-avatar-tool-items',
            externalKind: 'avatar-tool-items',
            shape: 'rounded-rect',
            fallbackGroup: 'chat-avatar-tools',
            localSelectors: Object.freeze([
                '#react-chat-window-root #composer-tool-popover-compact .composer-icon-button[data-avatar-tool-id]',
                '#react-chat-window-root #composer-avatar-tool-quickbar .composer-icon-button[data-avatar-tool-id]',
                '#react-chat-window-root .composer-icon-button[data-avatar-tool-id]'
            ])
        }),
        'chat-galgame': Object.freeze({
            key: 'chat-galgame',
            externalKind: 'galgame',
            shape: 'rounded-rect',
            fallbackGroup: 'chat-tool-toggle',
            localSelectors: Object.freeze([
                '#react-chat-window-root [data-avatar-tool-id="galgame"]',
                '#react-chat-window-root .composer-icon-button[data-avatar-tool-id="galgame"]'
            ])
        })
    });

    function cloneTargetGeometryEntry(entry) {
        if (!entry) {
            return null;
        }
        return {
            key: entry.key,
            externalKind: entry.externalKind,
            shape: entry.shape,
            fallbackGroup: entry.fallbackGroup,
            localSelectors: Array.prototype.slice.call(entry.localSelectors || [])
        };
    }

    function createTutorialTargetGeometryRegistry(options) {
        const normalizedOptions = options || {};
        const entries = Object.assign(
            {},
            DEFAULT_TARGET_GEOMETRY_ENTRIES,
            normalizedOptions.entries || {}
        );

        function resolve(key) {
            const normalizedKey = typeof key === 'string' ? key.trim() : '';
            return cloneTargetGeometryEntry(entries[normalizedKey] || null);
        }

        function getByExternalKind(externalKind) {
            const normalizedKind = typeof externalKind === 'string' ? externalKind.trim() : '';
            if (!normalizedKind) {
                return null;
            }
            const keys = Object.keys(entries);
            for (let index = 0; index < keys.length; index += 1) {
                const entry = entries[keys[index]];
                if (entry && entry.externalKind === normalizedKind) {
                    return cloneTargetGeometryEntry(entry);
                }
            }
            return null;
        }

        return {
            resolve,
            getByExternalKind,
            getExternalKind(key) {
                const entry = resolve(key);
                return entry ? entry.externalKind || '' : '';
            },
            getLocalSelectors(key) {
                const entry = resolve(key);
                return entry ? entry.localSelectors : [];
            },
            register(key, entry) {
                const normalizedKey = typeof key === 'string' ? key.trim() : '';
                if (!normalizedKey || !entry || typeof entry !== 'object') {
                    return false;
                }
                entries[normalizedKey] = Object.assign({}, entry, {
                    key: normalizedKey,
                    localSelectors: Array.isArray(entry.localSelectors)
                        ? entry.localSelectors.slice()
                        : []
                });
                return true;
            }
        };
    }

    return {
        createTutorialTargetGeometryRegistry
    };
});
