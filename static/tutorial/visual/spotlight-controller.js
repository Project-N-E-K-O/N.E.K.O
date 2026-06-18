(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialSpotlightController = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    class SpotlightController {
        constructor(highlightController, options) {
            const normalizedOptions = options || {};
            this.highlightController = highlightController || null;
            this.registry = normalizedOptions.registry || null;
        }

        resolveTargetEntry(targetKey) {
            if (!this.registry || typeof this.registry.resolve !== 'function') {
                return null;
            }
            return this.registry.resolve(targetKey) || null;
        }

        getExternalKind(targetKey) {
            if (this.registry && typeof this.registry.getExternalKind === 'function') {
                return this.registry.getExternalKind(targetKey) || '';
            }
            const entry = this.resolveTargetEntry(targetKey);
            return entry ? entry.externalKind || '' : '';
        }

        getLocalSelectors(targetKey) {
            if (this.registry && typeof this.registry.getLocalSelectors === 'function') {
                const selectors = this.registry.getLocalSelectors(targetKey);
                return Array.isArray(selectors) ? selectors.slice() : [];
            }
            const entry = this.resolveTargetEntry(targetKey);
            return entry && Array.isArray(entry.localSelectors) ? entry.localSelectors.slice() : [];
        }

        pause() {
            if (!this.highlightController || typeof this.highlightController.pause !== 'function') {
                return undefined;
            }
            return this.highlightController.pause();
        }

        resume() {
            if (!this.highlightController || typeof this.highlightController.resume !== 'function') {
                return undefined;
            }
            return this.highlightController.resume();
        }

        getElementRect(element) {
            if (!this.highlightController || typeof this.highlightController.getElementRect !== 'function') {
                return null;
            }
            return this.highlightController.getElementRect(element);
        }

        createVirtualSpotlight(key, rect, options) {
            if (!this.highlightController || typeof this.highlightController.createVirtualSpotlight !== 'function') {
                return null;
            }
            return this.highlightController.createVirtualSpotlight(key, rect, options);
        }

        createUnionSpotlight(key, elements, options) {
            if (!this.highlightController || typeof this.highlightController.createUnionSpotlight !== 'function') {
                return null;
            }
            return this.highlightController.createUnionSpotlight(key, elements, options);
        }

        clearVirtualSpotlight(key) {
            if (this.highlightController && typeof this.highlightController.clearVirtualSpotlight === 'function') {
                this.highlightController.clearVirtualSpotlight(key);
            }
        }

        clearAllVirtualSpotlights() {
            if (this.highlightController && typeof this.highlightController.clearAllVirtualSpotlights === 'function') {
                this.highlightController.clearAllVirtualSpotlights();
            }
        }

        clearSpotlightVariantHints() {
            if (this.highlightController && typeof this.highlightController.clearSpotlightVariantHints === 'function') {
                this.highlightController.clearSpotlightVariantHints();
            }
        }

        clearSpotlightGeometryHints() {
            if (this.highlightController && typeof this.highlightController.clearSpotlightGeometryHints === 'function') {
                this.highlightController.clearSpotlightGeometryHints();
            }
        }

        setSpotlightGeometryHint(element, options) {
            if (this.highlightController && typeof this.highlightController.setSpotlightGeometryHint === 'function') {
                this.highlightController.setSpotlightGeometryHint(element, options);
            }
        }

        setSpotlightVariantHints(entries) {
            if (this.highlightController && typeof this.highlightController.setSpotlightVariantHints === 'function') {
                this.highlightController.setSpotlightVariantHints(entries);
            }
        }

        syncExtraSpotlights() {
            if (this.highlightController && typeof this.highlightController.syncExtraSpotlights === 'function') {
                this.highlightController.syncExtraSpotlights();
            }
        }

        addRetainedExtraSpotlight(element) {
            if (this.highlightController && typeof this.highlightController.addRetainedExtraSpotlight === 'function') {
                this.highlightController.addRetainedExtraSpotlight(element);
            }
        }

        replaceRetainedExtraSpotlight(matcher, element) {
            if (this.highlightController && typeof this.highlightController.replaceRetainedExtraSpotlight === 'function') {
                this.highlightController.replaceRetainedExtraSpotlight(matcher, element);
            }
        }

        removeRetainedExtraSpotlight(matcher) {
            if (this.highlightController && typeof this.highlightController.removeRetainedExtraSpotlight === 'function') {
                this.highlightController.removeRetainedExtraSpotlight(matcher);
            }
        }

        clearRetainedExtraSpotlights() {
            if (this.highlightController && typeof this.highlightController.clearRetainedExtraSpotlights === 'function') {
                this.highlightController.clearRetainedExtraSpotlights();
            }
        }

        setSceneExtraSpotlights(elements) {
            if (this.highlightController && typeof this.highlightController.setSceneExtraSpotlights === 'function') {
                this.highlightController.setSceneExtraSpotlights(elements);
            }
        }

        clearSceneExtraSpotlights() {
            if (this.highlightController && typeof this.highlightController.clearSceneExtraSpotlights === 'function') {
                this.highlightController.clearSceneExtraSpotlights();
            }
        }

        clearAllExtraSpotlights() {
            if (this.highlightController && typeof this.highlightController.clearAllExtraSpotlights === 'function') {
                this.highlightController.clearAllExtraSpotlights();
            }
        }

        getFloatingButtonShell(element) {
            if (!this.highlightController || typeof this.highlightController.getFloatingButtonShell !== 'function') {
                return null;
            }
            return this.highlightController.getFloatingButtonShell(element);
        }

        isCircularFloatingButtonSpotlight(element) {
            if (!this.highlightController || typeof this.highlightController.isCircularFloatingButtonSpotlight !== 'function') {
                return false;
            }
            return this.highlightController.isCircularFloatingButtonSpotlight(element);
        }

        applyCircularFloatingButtonSpotlightHint(element) {
            if (!this.highlightController || typeof this.highlightController.applyCircularFloatingButtonSpotlightHint !== 'function') {
                return null;
            }
            return this.highlightController.applyCircularFloatingButtonSpotlightHint(element);
        }

        normalizeHighlightTarget(target, fallbackKey) {
            if (!this.highlightController || typeof this.highlightController.normalizeHighlightTarget !== 'function') {
                return null;
            }
            return this.highlightController.normalizeHighlightTarget(target, fallbackKey);
        }

        applyGuideHighlights(config) {
            if (!this.highlightController || typeof this.highlightController.applyGuideHighlights !== 'function') {
                return {};
            }
            return this.highlightController.applyGuideHighlights(config);
        }

        destroy() {
            if (this.highlightController && typeof this.highlightController.destroy === 'function') {
                this.highlightController.destroy();
            }
        }
    }

    return {
        SpotlightController
    };
});
