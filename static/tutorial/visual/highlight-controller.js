(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialHighlightController = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DEFAULT_SPOTLIGHT_PADDING = 12;
    const DEFAULT_VIRTUAL_SPOTLIGHT_RADIUS = 20;
    const FLOATING_BUTTON_SELECTOR = (
        '#live2d-btn-mic, #vrm-btn-mic, #mmd-btn-mic, ' +
        '#live2d-btn-screen, #vrm-btn-screen, #mmd-btn-screen, ' +
        '#live2d-btn-agent, #vrm-btn-agent, #mmd-btn-agent, ' +
        '#live2d-btn-settings, #vrm-btn-settings, #mmd-btn-settings, ' +
        '#live2d-btn-goodbye, #vrm-btn-goodbye, #mmd-btn-goodbye, ' +
        '#live2d-btn-return, #vrm-btn-return, #mmd-btn-return, ' +
        '#live2d-lock-icon, #vrm-lock-icon, #mmd-lock-icon, ' +
        '[id$="-btn-mic"], [id$="-btn-screen"], [id$="-btn-agent"], ' +
        '[id$="-btn-settings"], [id$="-btn-goodbye"], [id$="-btn-return"], [id$="-lock-icon"], ' +
        '.composer-tool-btn, .composer-icon-button[data-avatar-tool-id]'
    );

    function unionRects(rects) {
        const validRects = (Array.isArray(rects) ? rects : []).filter((rect) => {
            return !!rect && rect.width > 0 && rect.height > 0;
        });
        if (!validRects.length) {
            return null;
        }

        return validRects.reduce((merged, rect) => {
            return {
                left: Math.min(merged.left, rect.left),
                top: Math.min(merged.top, rect.top),
                right: Math.max(merged.right, rect.right),
                bottom: Math.max(merged.bottom, rect.bottom),
                width: Math.max(merged.right, rect.right) - Math.min(merged.left, rect.left),
                height: Math.max(merged.bottom, rect.bottom) - Math.min(merged.top, rect.top)
            };
        });
    }

    function getRectRight(rect) {
        if (Number.isFinite(rect.right)) {
            return rect.right;
        }
        if (Number.isFinite(rect.left) && Number.isFinite(rect.width)) {
            return rect.left + rect.width;
        }
        return 0;
    }

    function getRectBottom(rect) {
        if (Number.isFinite(rect.bottom)) {
            return rect.bottom;
        }
        if (Number.isFinite(rect.top) && Number.isFinite(rect.height)) {
            return rect.top + rect.height;
        }
        return 0;
    }

    class TutorialHighlightController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || getBrowserDocument();
            this.window = normalizedOptions.window || getBrowserWindow();
            this.overlay = normalizedOptions.overlay || null;
            this.defaultPadding = Number.isFinite(normalizedOptions.defaultPadding)
                ? normalizedOptions.defaultPadding
                : DEFAULT_SPOTLIGHT_PADDING;
            this.resolveElement = typeof normalizedOptions.resolveElement === 'function'
                ? normalizedOptions.resolveElement
                : (selector) => {
                    return this.document && typeof this.document.querySelector === 'function'
                        ? this.document.querySelector(selector)
                        : null;
                };
            this.floatingButtonSelector = normalizedOptions.floatingButtonSelector || FLOATING_BUTTON_SELECTOR;
            this.virtualSpotlights = new Map();
            this.spotlightVariantElements = new Set();
            this.spotlightGeometryHintElements = new Set();
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            this.destroyed = false;
            this.paused = false;
            this.lastGuideHighlights = {
                persistent: null,
                primary: null,
                secondary: null
            };
        }

        pause() {
            this.paused = true;
        }

        resume() {
            this.paused = false;
        }

        isSpotlightMutationPaused() {
            return this.paused && !this.destroyed;
        }

        getElementRect(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }

            return rect;
        }

        createVirtualSpotlight(key, rect, options) {
            if (this.destroyed || !key || !rect || !this.document || !this.document.body) {
                return null;
            }

            const normalizedOptions = options || {};
            const padding = Number.isFinite(normalizedOptions.padding)
                ? normalizedOptions.padding
                : this.defaultPadding;
            const radius = Number.isFinite(normalizedOptions.radius)
                ? normalizedOptions.radius
                : DEFAULT_VIRTUAL_SPOTLIGHT_RADIUS;
            const elementKey = String(key);
            let element = this.virtualSpotlights.get(elementKey) || null;
            if (!element) {
                element = this.document.createElement('div');
                element.setAttribute('data-yui-guide-virtual-spotlight', elementKey);
                Object.assign(element.style, {
                    position: 'fixed',
                    pointerEvents: 'none',
                    opacity: '0',
                    zIndex: '-1'
                });
                this.document.body.appendChild(element);
                this.virtualSpotlights.set(elementKey, element);
            }

            const rawLeft = Number.isFinite(rect.left) ? rect.left : 0;
            const rawTop = Number.isFinite(rect.top) ? rect.top : 0;
            const left = Math.max(0, Math.floor(rawLeft));
            const top = Math.max(0, Math.floor(rawTop));
            const viewportWidth = this.window && Number.isFinite(this.window.innerWidth)
                ? this.window.innerWidth
                : Math.ceil(getRectRight(rect));
            const viewportHeight = this.window && Number.isFinite(this.window.innerHeight)
                ? this.window.innerHeight
                : Math.ceil(getRectBottom(rect));
            const right = Math.min(viewportWidth, Math.ceil(getRectRight(rect)));
            const bottom = Math.min(viewportHeight, Math.ceil(getRectBottom(rect)));
            element.style.left = left + 'px';
            element.style.top = top + 'px';
            element.style.width = Math.max(0, right - left) + 'px';
            element.style.height = Math.max(0, bottom - top) + 'px';
            element.style.borderRadius = radius + 'px';
            element.setAttribute('data-yui-guide-spotlight-padding', String(padding));
            element.setAttribute('data-yui-guide-spotlight-radius', String(radius));

            if (typeof normalizedOptions.geometry === 'string' && normalizedOptions.geometry.trim()) {
                element.setAttribute('data-yui-guide-spotlight-geometry', normalizedOptions.geometry.trim().toLowerCase());
            } else {
                element.removeAttribute('data-yui-guide-spotlight-geometry');
            }

            if (normalizedOptions.variant) {
                element.setAttribute('data-yui-guide-spotlight-variant', String(normalizedOptions.variant));
            } else {
                element.removeAttribute('data-yui-guide-spotlight-variant');
            }

            return element;
        }

        createUnionSpotlight(key, elements, options) {
            const rect = unionRects((Array.isArray(elements) ? elements : []).map((element) => this.getElementRect(element)));
            return rect ? this.createVirtualSpotlight(key, rect, options) : null;
        }

        clearVirtualSpotlight(key) {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            if (!key) {
                return;
            }

            const element = this.virtualSpotlights.get(String(key));
            if (element && element.parentNode) {
                element.parentNode.removeChild(element);
            }
            this.virtualSpotlights.delete(String(key));
        }

        clearAllVirtualSpotlights() {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            this.virtualSpotlights.forEach((element) => {
                if (element && element.parentNode) {
                    element.parentNode.removeChild(element);
                }
            });
            this.virtualSpotlights.clear();
        }

        clearSpotlightVariantHints() {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            this.spotlightVariantElements.forEach((element) => {
                if (!element || typeof element.removeAttribute !== 'function') {
                    return;
                }

                element.removeAttribute('data-yui-guide-spotlight-variant');
            });
            this.spotlightVariantElements.clear();
        }

        clearSpotlightGeometryHints() {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            this.spotlightGeometryHintElements.forEach((element) => {
                if (!element || typeof element.removeAttribute !== 'function') {
                    return;
                }

                element.removeAttribute('data-yui-guide-spotlight-padding');
                element.removeAttribute('data-yui-guide-spotlight-radius');
                element.removeAttribute('data-yui-guide-spotlight-geometry');
            });
            this.spotlightGeometryHintElements.clear();
        }

        setSpotlightGeometryHint(element, options) {
            if (this.destroyed || this.isSpotlightMutationPaused() || !element || typeof element.setAttribute !== 'function') {
                return;
            }

            const normalizedOptions = options || {};
            const padding = Number.isFinite(normalizedOptions.padding) ? normalizedOptions.padding : null;
            const radius = Number.isFinite(normalizedOptions.radius) ? normalizedOptions.radius : null;
            const geometry = typeof normalizedOptions.geometry === 'string'
                ? normalizedOptions.geometry.trim().toLowerCase()
                : '';

            if (padding !== null) {
                element.setAttribute('data-yui-guide-spotlight-padding', String(padding));
            } else {
                element.removeAttribute('data-yui-guide-spotlight-padding');
            }

            if (radius !== null) {
                element.setAttribute('data-yui-guide-spotlight-radius', String(radius));
            } else {
                element.removeAttribute('data-yui-guide-spotlight-radius');
            }

            if (geometry) {
                element.setAttribute('data-yui-guide-spotlight-geometry', geometry);
            } else {
                element.removeAttribute('data-yui-guide-spotlight-geometry');
            }

            this.spotlightGeometryHintElements.add(element);
        }

        setSpotlightVariantHints(entries) {
            if (this.destroyed || this.isSpotlightMutationPaused()) {
                return;
            }

            this.clearSpotlightVariantHints();
            (Array.isArray(entries) ? entries : []).forEach((entry) => {
                const element = entry && entry.element;
                const variant = entry && entry.variant;
                if (!element || typeof element.setAttribute !== 'function' || !variant) {
                    return;
                }

                element.setAttribute('data-yui-guide-spotlight-variant', String(variant));
                this.spotlightVariantElements.add(element);
            });
        }

        getFloatingButtonShell(element) {
            if (!element) {
                return null;
            }

            if (typeof element.closest === 'function') {
                const shell = element.closest(this.floatingButtonSelector);
                if (shell) {
                    return shell;
                }
            }

            return element;
        }

        isCircularFloatingButtonSpotlight(element) {
            const target = this.getFloatingButtonShell(element) || element;
            if (!target) {
                return false;
            }

            if (
                typeof target.matches === 'function'
                && target.matches('.composer-tool-btn, .composer-icon-button[data-avatar-tool-id]')
            ) {
                return true;
            }

            if (typeof target.id !== 'string') {
                return false;
            }

            return /-(?:btn-(?:mic|screen|agent|settings|goodbye|return)|lock-icon)$/.test(target.id);
        }

        applyCircularFloatingButtonSpotlightHint(element) {
            if (this.destroyed || !this.isCircularFloatingButtonSpotlight(element)) {
                return false;
            }

            const target = this.getFloatingButtonShell(element) || element;
            this.setSpotlightGeometryHint(target, {
                padding: 4,
                geometry: 'circle'
            });
            return true;
        }

        syncExtraSpotlights() {
            if (this.destroyed || this.isSpotlightMutationPaused()) {
                return;
            }

            const nextElements = [];
            const seen = new Set();
            [this.retainedExtraSpotlightElements, this.sceneExtraSpotlightElements].forEach((elements) => {
                (Array.isArray(elements) ? elements : []).forEach((element) => {
                    const isVirtualSpotlight = !!(
                        element
                        && typeof element.getAttribute === 'function'
                        && element.getAttribute('data-yui-guide-virtual-spotlight')
                    );
                    if (
                        !element
                        || typeof element.getBoundingClientRect !== 'function'
                        || (!isVirtualSpotlight && element.isConnected === false)
                        || seen.has(element)
                    ) {
                        return;
                    }
                    seen.add(element);
                    this.applyCircularFloatingButtonSpotlightHint(element);
                    nextElements.push(element);
                });
            });

            if (this.overlay && typeof this.overlay.setExtraSpotlights === 'function') {
                this.overlay.setExtraSpotlights(nextElements);
            }
        }

        addRetainedExtraSpotlight(element) {
            if (
                this.destroyed
                || this.isSpotlightMutationPaused()
                || !element
                || typeof element.getBoundingClientRect !== 'function'
            ) {
                return;
            }

            if (!this.retainedExtraSpotlightElements.includes(element)) {
                this.retainedExtraSpotlightElements.push(element);
            }
            this.syncExtraSpotlights();
        }

        replaceRetainedExtraSpotlight(matcher, element) {
            if (this.destroyed || this.isSpotlightMutationPaused()) {
                return;
            }

            const normalizedMatcher = typeof matcher === 'function'
                ? matcher
                : (candidate) => candidate === matcher;
            this.retainedExtraSpotlightElements = this.retainedExtraSpotlightElements.filter((candidate) => {
                try {
                    return !normalizedMatcher(candidate);
                } catch (_) {
                    return true;
                }
            });
            if (element && typeof element.getBoundingClientRect === 'function') {
                this.retainedExtraSpotlightElements.push(element);
            }
            this.syncExtraSpotlights();
        }

        removeRetainedExtraSpotlight(matcher) {
            if (this.destroyed || this.isSpotlightMutationPaused()) {
                return;
            }

            const normalizedMatcher = typeof matcher === 'function'
                ? matcher
                : (candidate) => candidate === matcher;
            this.retainedExtraSpotlightElements = this.retainedExtraSpotlightElements.filter((candidate) => {
                try {
                    return !normalizedMatcher(candidate);
                } catch (_) {
                    return true;
                }
            });
            this.syncExtraSpotlights();
        }

        clearRetainedExtraSpotlights() {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            this.retainedExtraSpotlightElements = [];
            this.syncExtraSpotlights();
        }

        setSceneExtraSpotlights(elements) {
            if (this.destroyed || this.isSpotlightMutationPaused()) {
                return;
            }

            this.sceneExtraSpotlightElements = (Array.isArray(elements) ? elements : [])
                .filter((element) => !!element && typeof element.getBoundingClientRect === 'function');
            this.syncExtraSpotlights();
        }

        clearSceneExtraSpotlights() {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            this.sceneExtraSpotlightElements = [];
            this.syncExtraSpotlights();
        }

        clearAllExtraSpotlights() {
            if (this.isSpotlightMutationPaused()) {
                return;
            }
            this.retainedExtraSpotlightElements = [];
            this.sceneExtraSpotlightElements = [];
            if (this.overlay && typeof this.overlay.clearExtraSpotlights === 'function') {
                this.overlay.clearExtraSpotlights();
            }
        }

        normalizeHighlightTarget(target, fallbackKey) {
            if (!target) {
                return null;
            }

            if (Array.isArray(target)) {
                return this.createUnionSpotlight(fallbackKey || 'highlight-union', target, {
                    padding: this.defaultPadding,
                    radius: 18
                });
            }

            if (typeof target === 'string') {
                return this.resolveElement(target);
            }

            if (target && typeof target === 'object') {
                if (target.element) {
                    return target.element;
                }
                if (target.selector) {
                    return this.resolveElement(target.selector);
                }
                if (Array.isArray(target.elements)) {
                    return this.createUnionSpotlight(
                        target.key || fallbackKey || 'highlight-union',
                        target.elements,
                        target.options || {}
                    );
                }
                if (target.rect) {
                    return this.createVirtualSpotlight(
                        target.key || fallbackKey || 'highlight-rect',
                        target.rect,
                        target.options || {}
                    );
                }
            }

            return target;
        }

        applyGuideHighlights(config) {
            if (this.paused) {
                return this.lastGuideHighlights;
            }

            if (this.destroyed) {
                return {
                    persistent: null,
                    primary: null,
                    secondary: null
                };
            }

            const normalized = config || {};
            const keyBase = normalized.key || 'guide-highlight';
            const hasPersistent = Object.prototype.hasOwnProperty.call(normalized, 'persistent');
            const hasPrimary = Object.prototype.hasOwnProperty.call(normalized, 'primary');
            const hasSecondary = Object.prototype.hasOwnProperty.call(normalized, 'secondary');
            const persistentTarget = hasPersistent
                ? this.normalizeHighlightTarget(normalized.persistent, keyBase + '-persistent')
                : null;
            let primaryTarget = hasPrimary
                ? this.normalizeHighlightTarget(normalized.primary, keyBase + '-primary')
                : null;
            let secondaryTarget = hasSecondary
                ? this.normalizeHighlightTarget(normalized.secondary, keyBase + '-secondary')
                : null;

            if (primaryTarget && primaryTarget === persistentTarget) {
                primaryTarget = null;
            }
            if (
                secondaryTarget
                && (secondaryTarget === persistentTarget || secondaryTarget === primaryTarget)
            ) {
                secondaryTarget = null;
            }

            if (hasPersistent && this.overlay) {
                if (persistentTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(persistentTarget);
                    this.overlay.setPersistentSpotlight(persistentTarget);
                } else {
                    this.overlay.clearPersistentSpotlight();
                }
            }

            if (hasPrimary && this.overlay) {
                if (primaryTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(primaryTarget);
                    this.overlay.activateSpotlight(primaryTarget);
                } else {
                    this.overlay.clearActionSpotlight();
                }
            }

            if (hasSecondary && this.overlay) {
                if (secondaryTarget) {
                    this.applyCircularFloatingButtonSpotlightHint(secondaryTarget);
                    this.overlay.activateSecondarySpotlight(secondaryTarget);
                } else if (!hasPrimary) {
                    this.overlay.clearActionSpotlight();
                }
            }

            this.lastGuideHighlights = {
                persistent: persistentTarget,
                primary: primaryTarget,
                secondary: secondaryTarget
            };
            return this.lastGuideHighlights;
        }

        destroy() {
            if (this.destroyed) {
                return;
            }

            this.destroyed = true;
            this.clearAllVirtualSpotlights();
            this.clearSpotlightVariantHints();
            this.clearSpotlightGeometryHints();
            this.clearAllExtraSpotlights();
        }
    }


    function createHighlightController(options) {
        return new TutorialHighlightController(options);
    }

    function getBrowserWindow() {
        if (typeof window !== 'undefined') {
            return window;
        }
        return typeof globalThis !== 'undefined' ? globalThis.window : null;
    }

    function getBrowserDocument() {
        if (typeof document !== 'undefined') {
            return document;
        }
        return typeof globalThis !== 'undefined' ? globalThis.document : null;
    }


    return {
        TutorialHighlightController,
        createController: createHighlightController,
        createHighlightController
    };
});
