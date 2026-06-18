(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.YuiGuideCommon = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
    'use strict';

    function loadTutorialScopedResourcesApi() {
        if (root && root.TutorialScopedResources) {
            return root.TutorialScopedResources;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/scoped-resources.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialGuideHelpersApi() {
        if (root && root.TutorialGuideHelpers) {
            return root.TutorialGuideHelpers;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/guide-helpers.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialBridgeCommandBusApi() {
        if (root && root.TutorialBridgeCommandBus) {
            return root.TutorialBridgeCommandBus;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/bridge-command-bus.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialTargetGeometryRegistryApi() {
        if (root && root.TutorialTargetGeometryRegistry) {
            return root.TutorialTargetGeometryRegistry;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/target-geometry-registry.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialChatWindowAdapterApi() {
        if (root && root.TutorialChatWindowAdapter) {
            return root.TutorialChatWindowAdapter;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/chat-window-adapter.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialCommandRegistryApi() {
        if (root && root.TutorialCommandRegistry) {
            return root.TutorialCommandRegistry;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/command-registry.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialScriptNormalizerApi() {
        if (root && root.TutorialScriptNormalizer) {
            return root.TutorialScriptNormalizer;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/script-normalizer.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialTimelineEngineApi() {
        if (root && root.TutorialTimelineEngine) {
            return root.TutorialTimelineEngine;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/timeline-engine.js');
            } catch (_) {}
        }
        return null;
    }

    function loadTutorialVisualRuntimeApi() {
        if (root && root.TutorialVisualRuntime) {
            return root.TutorialVisualRuntime;
        }
        if (typeof require === 'function') {
            try {
                return require('../core/visual-runtime.js');
            } catch (_) {}
        }
        return null;
    }

    const tutorialGuideHelpersApi = loadTutorialGuideHelpersApi();
    const tutorialScopedResourcesApi = loadTutorialScopedResourcesApi();
    const tutorialBridgeCommandBusApi = loadTutorialBridgeCommandBusApi();
    const tutorialTargetGeometryRegistryApi = loadTutorialTargetGeometryRegistryApi();
    const tutorialChatWindowAdapterApi = loadTutorialChatWindowAdapterApi();
    const tutorialCommandRegistryApi = loadTutorialCommandRegistryApi();
    const tutorialScriptNormalizerApi = loadTutorialScriptNormalizerApi();
    const tutorialTimelineEngineApi = loadTutorialTimelineEngineApi();
    const tutorialVisualRuntimeApi = loadTutorialVisualRuntimeApi();

    function deepFreeze(value) {
        if (
            tutorialGuideHelpersApi
            && typeof tutorialGuideHelpersApi.deepFreeze === 'function'
        ) {
            return tutorialGuideHelpersApi.deepFreeze(value);
        }
        throw new Error('TutorialGuideHelpers is required before tutorial/yui-guide/common.js');
    }

    function registerGuide(config, options) {
        if (
            tutorialGuideHelpersApi
            && typeof tutorialGuideHelpersApi.registerGuide === 'function'
        ) {
            return tutorialGuideHelpersApi.registerGuide(config, options);
        }
        throw new Error('TutorialGuideHelpers is required before tutorial/yui-guide/common.js');
    }

    function audioFilesForAllLocales(fileName) {
        if (
            tutorialGuideHelpersApi
            && typeof tutorialGuideHelpersApi.audioFilesForAllLocales === 'function'
        ) {
            return tutorialGuideHelpersApi.audioFilesForAllLocales(fileName);
        }
        throw new Error('TutorialGuideHelpers is required before tutorial/yui-guide/common.js');
    }

    function createScopedTutorialResources(options) {
        if (
            tutorialScopedResourcesApi
            && typeof tutorialScopedResourcesApi.createScopedTutorialResources === 'function'
        ) {
            return tutorialScopedResourcesApi.createScopedTutorialResources(options);
        }
        throw new Error('TutorialScopedResources is required before tutorial/yui-guide/common.js');
    }

    function createTutorialBridgeCommandBus(options) {
        if (
            tutorialBridgeCommandBusApi
            && typeof tutorialBridgeCommandBusApi.createTutorialBridgeCommandBus === 'function'
        ) {
            return tutorialBridgeCommandBusApi.createTutorialBridgeCommandBus(options);
        }
        throw new Error('TutorialBridgeCommandBus is required before tutorial/yui-guide/common.js');
    }

    function createTutorialTargetGeometryRegistry(options) {
        if (
            tutorialTargetGeometryRegistryApi
            && typeof tutorialTargetGeometryRegistryApi.createTutorialTargetGeometryRegistry === 'function'
        ) {
            return tutorialTargetGeometryRegistryApi.createTutorialTargetGeometryRegistry(options);
        }
        throw new Error('TutorialTargetGeometryRegistry is required before tutorial/yui-guide/common.js');
    }

    function createReactChatTutorialHostAdapter(options) {
        if (
            tutorialChatWindowAdapterApi
            && typeof tutorialChatWindowAdapterApi.createReactChatTutorialHostAdapter === 'function'
        ) {
            return tutorialChatWindowAdapterApi.createReactChatTutorialHostAdapter(options);
        }
        throw new Error('TutorialChatWindowAdapter is required before tutorial/yui-guide/common.js');
    }

    function createChatWindowAdapter(options) {
        if (
            tutorialChatWindowAdapterApi
            && typeof tutorialChatWindowAdapterApi.createChatWindowAdapter === 'function'
        ) {
            return tutorialChatWindowAdapterApi.createChatWindowAdapter(options);
        }
        throw new Error('TutorialChatWindowAdapter is required before tutorial/yui-guide/common.js');
    }

    function createTutorialCommandRegistry(options) {
        if (
            tutorialCommandRegistryApi
            && typeof tutorialCommandRegistryApi.createTutorialCommandRegistry === 'function'
        ) {
            return tutorialCommandRegistryApi.createTutorialCommandRegistry(options);
        }
        throw new Error('TutorialCommandRegistry is required before tutorial/yui-guide/common.js');
    }

    function normalizeTutorialScene(scene, options) {
        if (
            tutorialScriptNormalizerApi
            && typeof tutorialScriptNormalizerApi.normalizeTutorialScene === 'function'
        ) {
            return tutorialScriptNormalizerApi.normalizeTutorialScene(scene, options);
        }
        throw new Error('TutorialScriptNormalizer is required before tutorial/yui-guide/common.js');
    }

    function createTutorialTimelineEngine(options) {
        if (
            tutorialTimelineEngineApi
            && typeof tutorialTimelineEngineApi.createTutorialTimelineEngine === 'function'
        ) {
            return tutorialTimelineEngineApi.createTutorialTimelineEngine(options);
        }
        throw new Error('TutorialTimelineEngine is required before tutorial/yui-guide/common.js');
    }

    function createTutorialVisualRuntime(director, options) {
        if (
            tutorialVisualRuntimeApi
            && typeof tutorialVisualRuntimeApi.createTutorialVisualRuntime === 'function'
        ) {
            return tutorialVisualRuntimeApi.createTutorialVisualRuntime(director, options);
        }
        throw new Error('TutorialVisualRuntime is required before tutorial/yui-guide/common.js');
    }

    return {
        deepFreeze,
        registerGuide,
        audioFilesForAllLocales,
        createScopedTutorialResources,
        createTutorialBridgeCommandBus,
        createTutorialTargetGeometryRegistry,
        createReactChatTutorialHostAdapter,
        createChatWindowAdapter,
        createTutorialCommandRegistry,
        normalizeTutorialScene,
        createTutorialTimelineEngine,
        createTutorialVisualRuntime
    };
});
