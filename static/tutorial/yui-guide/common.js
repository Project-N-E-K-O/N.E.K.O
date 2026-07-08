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

    function isNekoShortcutBlockedByTutorial(options) {
        const normalizedOptions = options || {};
        const host = normalizedOptions.window || root || {};
        const doc = normalizedOptions.document || host.document;
        const body = doc && doc.body;
        const rootElement = doc && doc.documentElement;
        const hasClass = function (node, className) {
            return !!(node && node.classList && node.classList.contains(className));
        };
        return host.isInTutorial === true
            || hasClass(body, 'yui-guide-home-ui-suppressed')
            || hasClass(body, 'yui-guide-input-shield-active')
            || hasClass(body, 'yui-guide-standalone-input-shield-active')
            || hasClass(body, 'yui-guide-chat-buttons-disabled')
            || hasClass(body, 'yui-guide-compact-chat-fixed')
            || hasClass(rootElement, 'yui-guide-plugin-dashboard-running')
            || hasClass(body, 'yui-guide-plugin-dashboard-running');
    }

    if (root && typeof root.isNekoShortcutBlockedByTutorial !== 'function') {
        root.isNekoShortcutBlockedByTutorial = function () {
            return isNekoShortcutBlockedByTutorial({ window: root });
        };
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

    function createPcTutorialRunId() {
        return 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
    }

    function readPcTutorialRunId(options, ensure) {
        const normalizedOptions = options || {};
        const host = normalizedOptions.window || root || {};
        try {
            const storage = normalizedOptions.localStorage || host.localStorage;
            const storedRunId = storage
                ? (storage.getItem('yuiGuidePcOverlayRunId') || '')
                : '';
            if (storedRunId || ensure !== true || !storage || typeof storage.setItem !== 'function') {
                return storedRunId;
            }
            const nextRunId = createPcTutorialRunId();
            storage.setItem('yuiGuidePcOverlayRunId', nextRunId);
            return nextRunId;
        } catch (_) {
            return '';
        }
    }

    function relayPcTutorialMessage(message, options, failureLabel) {
        const normalizedOptions = options || {};
        const host = normalizedOptions.window || root || {};
        const logger = normalizedOptions.console || host.console || (root && root.console);
        const warnRelayFailure = (target, error) => {
            try {
                if (logger && typeof logger.warn === 'function') {
                    logger.warn(failureLabel || '[YuiGuide] 同步 PC 教程消息失败:', target, error);
                }
            } catch (_) {}
        };
        const overlay = normalizedOptions.nekoTutorialOverlay || host.nekoTutorialOverlay;
        try {
            if (overlay && typeof overlay.relayToChat === 'function') {
                overlay.relayToChat(message);
            }
        } catch (error) {
            warnRelayFailure('relayToChat', error);
        }
        try {
            if (overlay && typeof overlay.relayToPet === 'function') {
                overlay.relayToPet(message);
            }
        } catch (error) {
            warnRelayFailure('relayToPet', error);
        }
        try {
            const channel = normalizedOptions.channel
                || (
                    host.appInterpage
                    && host.appInterpage.nekoBroadcastChannel
                );
            if (channel && typeof channel.postMessage === 'function') {
                channel.postMessage(message);
            }
        } catch (error) {
            warnRelayFailure('nekoBroadcastChannel', error);
        }
    }

    function syncPcTutorialLifecycleStarted(reason = 'tutorial-started', options) {
        relayPcTutorialMessage({
            action: 'yui_guide_tutorial_lifecycle_started',
            tutorialRunId: readPcTutorialRunId(options, true),
            reason: reason,
            timestamp: Date.now()
        }, options, '[YuiGuide] 同步 PC 教程生命周期开始失败:');
    }

    function syncPcSystemCursorHidden(hidden, reason = 'tutorial', options) {
        relayPcTutorialMessage({
            action: 'yui_guide_system_cursor_visibility',
            hidden: hidden === true,
            tutorialRunId: readPcTutorialRunId(options, hidden === true),
            reason: reason,
            timestamp: Date.now()
        }, options, '[YuiGuide] 同步 PC 系统鼠标状态失败:');
    }

    function syncPcSystemCursorTemporaryReveal(durationMs = 2000, reason = 'tutorial-temporary-reveal', options) {
        const normalizedDurationMs = Math.min(
            10000,
            Math.max(0, Math.floor(Number(durationMs) || 0))
        );
        syncPcSystemCursorHidden(false, reason, options);
        relayPcTutorialMessage({
            action: 'yui_guide_system_cursor_temporary_reveal',
            durationMs: normalizedDurationMs,
            tutorialRunId: readPcTutorialRunId(options, false),
            reason: reason,
            timestamp: Date.now()
        }, options, '[YuiGuide] 同步 PC 系统鼠标临时显示失败:');
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
        createTutorialVisualRuntime,
        isNekoShortcutBlockedByTutorial,
        syncPcTutorialLifecycleStarted,
        syncPcSystemCursorHidden,
        syncPcSystemCursorTemporaryReveal
    };
});
