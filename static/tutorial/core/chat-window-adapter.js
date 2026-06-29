(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialChatWindowAdapter = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    function loadTargetGeometryRegistryApi() {
        if (root && root.TutorialTargetGeometryRegistry) {
            return root.TutorialTargetGeometryRegistry;
        }
        if (typeof require === 'function') {
            try {
                return require('./target-geometry-registry.js');
            } catch (_) {}
        }
        return null;
    }

    const targetGeometryRegistryApi = loadTargetGeometryRegistryApi();

    function createFallbackTargetGeometryRegistry() {
        return {
            resolve() {
                return null;
            },
            getExternalKind(key) {
                return key || '';
            },
            getLocalSelectors() {
                return [];
            }
        };
    }

    function createReactChatTutorialHostAdapter(options) {
        const normalizedOptions = options || {};
        const win = normalizedOptions.window || root || {};
        const explicitHost = normalizedOptions.host || null;

        function resolveHost() {
            return explicitHost || (win && win.reactChatWindowHost) || null;
        }

        function callHost(methodName, args) {
            const host = resolveHost();
            if (!host || typeof host[methodName] !== 'function') {
                return false;
            }
            try {
                host[methodName].apply(host, args || []);
                return true;
            } catch (error) {
                console.warn('[TutorialChatWindowAdapter] host call failed:', methodName, error);
                return false;
            }
        }

        return {
            lockInput(locked, reason) {
                return callHost('setHomeTutorialInputLocked', [locked === true, reason || '']);
            },
            setButtonsDisabled(disabled, reason) {
                return callHost('setHomeTutorialInteractionLocked', [disabled === true, reason || '']);
            },
            setAvatarToolMenuOpen(open, reason) {
                return callHost('setAvatarToolMenuOpen', [open === true, reason || '']);
            },
            setCompactToolFanOpen(open, reason) {
                return callHost('setCompactToolFanOpen', [open === true, reason || '']);
            },
            setCompactHistoryOpen(open, reason) {
                return callHost('setCompactHistoryOpen', [open === true, reason || '']);
            },
            rotateCompactToolWheel(direction, stepCount, reason) {
                return callHost('rotateCompactToolWheel', [direction, stepCount, reason || '']);
            },
            setCompactToolWheelIndex(index, reason) {
                return callHost('setCompactToolWheelIndex', [index, reason || '']);
            }
        };
    }

    function createDefaultRegistry() {
        if (
            targetGeometryRegistryApi
            && typeof targetGeometryRegistryApi.createTutorialTargetGeometryRegistry === 'function'
        ) {
            return targetGeometryRegistryApi.createTutorialTargetGeometryRegistry();
        }
        return createFallbackTargetGeometryRegistry();
    }

    function createChatWindowAdapter(options) {
        const normalizedOptions = options || {};
        const registry = normalizedOptions.registry || createDefaultRegistry();
        const mode = normalizedOptions.mode === 'externalized' ? 'externalized' : 'local';
        const interactionTakeover = normalizedOptions.interactionTakeover || null;
        const reactHostAdapter = normalizedOptions.reactHostAdapter || createReactChatTutorialHostAdapter({
            window: normalizedOptions.window,
            host: normalizedOptions.reactHost
        });
        const resolveLocalTarget = typeof normalizedOptions.resolveLocalTarget === 'function'
            ? normalizedOptions.resolveLocalTarget
            : () => null;
        const beforeExternalizedSpotlight = typeof normalizedOptions.beforeExternalizedSpotlight === 'function'
            ? normalizedOptions.beforeExternalizedSpotlight
            : null;

        function getExternalKind(targetKey) {
            return registry.getExternalKind(targetKey) || targetKey || '';
        }

        function getExternalizedRunMeta() {
            if (
                interactionTakeover
                && typeof interactionTakeover.getExternalizedChatTutorialRunId === 'function'
            ) {
                const tutorialRunId = interactionTakeover.getExternalizedChatTutorialRunId();
                if (tutorialRunId) {
                    return {
                        tutorialRunId,
                        pcOverlayRunId: tutorialRunId
                    };
                }
            }
            return {};
        }

        function notifyBeforeExternalizedSpotlight(kind) {
            if (!beforeExternalizedSpotlight) {
                return;
            }
            try {
                beforeExternalizedSpotlight(kind, getExternalizedRunMeta());
            } catch (error) {
                console.warn('[YuiGuideChatWindowAdapter] beforeExternalizedSpotlight failed:', error);
            }
        }

        return {
            mode,
            isExternalized() {
                return mode === 'externalized';
            },
            getExternalKind,
            resolveTarget(targetKey) {
                if (mode === 'externalized') {
                    return null;
                }
                return resolveLocalTarget(targetKey, registry.resolve(targetKey));
            },
            setSpotlight(targetKey) {
                if (mode !== 'externalized') {
                    return false;
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatSpotlight === 'function'
                ) {
                    const externalKind = getExternalKind(targetKey);
                    notifyBeforeExternalizedSpotlight(externalKind);
                    interactionTakeover.setExternalizedChatSpotlight(externalKind);
                    return true;
                }
                return false;
            },
            setCursor(targetKey, cursorOptions) {
                if (mode !== 'externalized') {
                    return false;
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatCursor === 'function'
                ) {
                    interactionTakeover.setExternalizedChatCursor(getExternalKind(targetKey), cursorOptions || {});
                    return true;
                }
                return false;
            },
            lockInput(locked, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.lockInput(locked === true, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatInputLocked === 'function'
                ) {
                    interactionTakeover.setExternalizedChatInputLocked(locked === true, reason || '');
                    return true;
                }
                return false;
            },
            setButtonsDisabled(disabled, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.setButtonsDisabled(disabled === true, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatButtonsDisabled === 'function'
                ) {
                    interactionTakeover.setExternalizedChatButtonsDisabled(disabled === true, reason || '');
                    return true;
                }
                return false;
            },
            setAvatarToolMenuOpen(open, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.setAvatarToolMenuOpen(open === true, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatAvatarToolMenuOpen === 'function'
                ) {
                    interactionTakeover.setExternalizedChatAvatarToolMenuOpen(open === true, reason || '');
                    return true;
                }
                return false;
            },
            setCompactToolFanOpen(open, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.setCompactToolFanOpen(open === true, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatCompactToolFanOpen === 'function'
                ) {
                    interactionTakeover.setExternalizedChatCompactToolFanOpen(open === true, reason || '');
                    return true;
                }
                return false;
            },
            setCompactHistoryOpen(open, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.setCompactHistoryOpen(open === true, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatCompactHistoryOpen === 'function'
                ) {
                    interactionTakeover.setExternalizedChatCompactHistoryOpen(open === true, reason || '');
                    return true;
                }
                return false;
            },
            rotateCompactToolWheel(direction, stepCount, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.rotateCompactToolWheel(direction, stepCount, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.rotateExternalizedChatCompactToolWheel === 'function'
                ) {
                    interactionTakeover.rotateExternalizedChatCompactToolWheel(direction, stepCount, reason || '');
                    return true;
                }
                return false;
            },
            setCompactToolWheelIndex(index, reason) {
                if (mode !== 'externalized') {
                    return reactHostAdapter.setCompactToolWheelIndex(index, reason || '');
                }
                if (
                    interactionTakeover
                    && typeof interactionTakeover.setExternalizedChatCompactToolWheelIndex === 'function'
                ) {
                    interactionTakeover.setExternalizedChatCompactToolWheelIndex(index, reason || '');
                    return true;
                }
                return false;
            }
        };
    }

    return {
        createReactChatTutorialHostAdapter,
        createChatWindowAdapter
    };
});
