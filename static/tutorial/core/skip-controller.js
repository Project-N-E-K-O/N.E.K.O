(function () {
    'use strict';

    class TutorialSkipController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.buttonId = normalizedOptions.buttonId || 'neko-tutorial-skip-btn';
            this.currentButton = null;
            this.currentCleanup = null;
            this.currentResources = null;
            this.safeAreaCleanup = null;
            this.styleId = `${this.buttonId}-style`;
            this.portalId = normalizedOptions.portalId || 'neko-tutorial-fixed-ui-root';
        }

        getElement() {
            return this.document.getElementById(this.buttonId) || this.currentButton || null;
        }

        getPortalElement() {
            return this.document.getElementById(this.portalId);
        }

        usesFixedPortal() {
            const root = this.document.documentElement;
            return !!(root && typeof root.appendChild === 'function');
        }

        ensureFixedPortal() {
            if (!this.usesFixedPortal()) {
                return this.document.body || null;
            }
            let portal = this.getPortalElement();
            if (!portal) {
                portal = this.document.createElement('div');
                portal.id = this.portalId;
                portal.setAttribute('data-neko-tutorial-fixed-ui-root', 'true');
                this.document.documentElement.appendChild(portal);
            }
            if (portal.style && typeof portal.style.setProperty === 'function') {
                portal.style.setProperty('position', 'fixed', 'important');
                portal.style.setProperty('inset', '0', 'important');
                portal.style.setProperty('z-index', '2147483647', 'important');
                portal.style.setProperty('pointer-events', 'none', 'important');
                portal.style.setProperty('transform', 'none', 'important');
            }
            return portal;
        }

        getButtonHost() {
            return this.usesFixedPortal() ? this.ensureFixedPortal() : (this.document.body || null);
        }

        isElementInFixedPortal(element) {
            if (!element) {
                return false;
            }
            try {
                if (typeof element.closest === 'function') {
                    return !!element.closest(`#${this.portalId}`);
                }
            } catch (_) {}
            let node = element.parentNode || null;
            while (node) {
                if (node.id === this.portalId) {
                    return true;
                }
                node = node.parentNode || null;
            }
            return false;
        }

        removeEmptyFixedPortal() {
            const portal = this.getPortalElement();
            if (!portal) {
                return;
            }
            const hasChildren = portal.children
                ? portal.children.length > 0
                : !!portal.firstChild;
            if (!hasChildren && typeof portal.remove === 'function') {
                portal.remove();
            }
        }

        ensureStyles() {
            if (this.document.getElementById(this.styleId)) {
                return;
            }

            const escapeCssId = (value) => (typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function'
                ? CSS.escape(value)
                : String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&'));
            const selector = `#${escapeCssId(this.buttonId)}`;
            const portalSelector = `#${escapeCssId(this.portalId)}`;
            const baseTop = this.getBaseTopInset();
            const style = this.document.createElement('style');
            style.id = this.styleId;
            style.textContent = `
${portalSelector} {
  position: fixed !important;
  inset: 0 !important;
  z-index: 2147483647 !important;
  pointer-events: none !important;
  transform: none !important;
  contain: layout style paint;
}

${portalSelector} ${selector} {
  top: calc(max(${baseTop}px, env(safe-area-inset-top)) + var(--neko-tutorial-visible-safe-area-top, var(--neko-tutorial-safe-area-top, 0px))) !important;
}

${selector} {
  position: fixed;
  --neko-tutorial-crop-safe-area-top: max(var(--neko-tutorial-safe-area-top, 0px), calc(var(--neko-niri-pet-crop-offset-y, 0) * 1px));
  top: calc(max(${baseTop}px, env(safe-area-inset-top)) + var(--neko-tutorial-crop-safe-area-top));
  right: max(18px, env(safe-area-inset-right));
  z-index: 2147483647;
  width: auto;
  height: auto;
  min-width: 82px;
  min-height: 46px;
  padding: 9px 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 252, 248, 0.78) !important;
  color: rgba(48, 59, 74, 0.82);
  border: 1px solid rgba(47, 131, 255, 0.28);
  border-radius: 8px;
  box-shadow: 0 10px 26px rgba(15, 23, 42, 0.12), 0 0 0 1px rgba(255, 255, 255, 0.36) inset;
  font-size: 22px;
  font-weight: 700;
  line-height: 1.2;
  cursor: pointer !important;
  transition: color 0.2s ease, border-color 0.2s ease, background 0.2s ease, transform 0.15s ease, box-shadow 0.2s ease !important;
  backdrop-filter: blur(12px) saturate(1.08);
  pointer-events: auto !important;
  user-select: none;
  outline: none !important;
  white-space: nowrap;
  box-sizing: border-box !important;
  -webkit-appearance: none;
  -moz-appearance: none;
  appearance: none;
}

${selector}:hover {
  color: rgba(20, 33, 49, 0.96);
  border-color: rgba(47, 131, 255, 0.5);
  background: rgba(255, 255, 255, 0.9) !important;
  box-shadow: 0 14px 34px rgba(47, 131, 255, 0.16), 0 0 0 1px rgba(47, 131, 255, 0.12) inset;
  transform: translateY(-1px);
}

${selector}:active {
  opacity: 0.8;
  transform: translateY(0);
}

${selector}:focus-visible {
  outline: 2px solid rgba(68, 183, 254, 0.6) !important;
  outline-offset: 2px;
}

html[data-theme='dark'] ${selector},
html.dark ${selector} {
  background: rgba(18, 25, 36, 0.78) !important;
  color: rgba(236, 243, 252, 0.86);
  border-color: rgba(104, 183, 255, 0.34);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.26), 0 0 0 1px rgba(255, 255, 255, 0.05) inset;
}

html[data-theme='dark'] ${selector}:hover,
html.dark ${selector}:hover {
  color: #ffffff;
  border-color: rgba(104, 183, 255, 0.58);
  background: rgba(28, 38, 53, 0.94) !important;
}
`;
            this.document.head.appendChild(style);
        }

        getDesktopMetrics() {
            const host = window.nekoTutorialOverlay;
            if (!host || typeof host.getWindowMetricsSync !== 'function') {
                return null;
            }
            try {
                return host.getWindowMetricsSync();
            } catch (_) {
                return null;
            }
        }

        normalizePositivePixel(value) {
            const number = Number.parseFloat(String(value || '').trim());
            return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
        }

        getNiriFixedUiMinimumTopInset() {
            return 40;
        }

        hasNiriFixedUiEvidence(metrics) {
            if (!metrics || typeof metrics !== 'object') {
                return false;
            }
            if (metrics.niriWaylandRuntime === true
                || metrics.niriPetPhysicalCrop === true
                || metrics.niriPetPhysicalCropMetricsVirtualized === true) {
                return true;
            }
            if (metrics.niriRenderBounds && typeof metrics.niriRenderBounds === 'object') {
                return true;
            }
            return Number(metrics.niriWindowTopInset) > 0
                || Number(metrics.niriPetPhysicalCropVisibleTopInset) > 0;
        }

        getRootCssPixelVariable(name) {
            const root = this.document.documentElement;
            if (!root) {
                return 0;
            }
            const inlineValue = root.style && typeof root.style.getPropertyValue === 'function'
                ? root.style.getPropertyValue(name)
                : '';
            const inlineInset = this.normalizePositivePixel(inlineValue);
            if (inlineInset > 0) {
                return inlineInset;
            }
            try {
                const computedValue = window.getComputedStyle(root).getPropertyValue(name);
                return this.normalizePositivePixel(computedValue);
            } catch (_) {
                return 0;
            }
        }

        getNiriPetPhysicalCropCssTopInset() {
            return this.getRootCssPixelVariable('--neko-niri-pet-crop-offset-y');
        }

        getCropTopInsetFromBounds(cropBounds, virtualBounds) {
            const cropY = Number(cropBounds && cropBounds.y);
            const virtualY = Number(virtualBounds && virtualBounds.y);
            const derivedInset = cropY - virtualY;
            return Number.isFinite(derivedInset) && derivedInset > 0 ? Math.round(derivedInset) : 0;
        }

        getDesktopWorkAreaTopInset(options) {
            const normalizedOptions = options || {};
            const includeWorkAreaTop = normalizedOptions.includeWorkAreaTop === true;
            try {
                const screenRef = window.screen || null;
                const availTop = this.normalizePositivePixel(screenRef && screenRef.availTop);
                const screenHeight = Number(screenRef && screenRef.height);
                const availHeight = Number(screenRef && screenRef.availHeight);
                const heightReservedInset = Number.isFinite(screenHeight)
                    && Number.isFinite(availHeight)
                    && screenHeight > availHeight
                    ? Math.max(0, Math.round(screenHeight - availHeight - availTop))
                    : 0;
                const hasHostMetrics = !!(window.nekoTutorialOverlay
                    && typeof window.nekoTutorialOverlay.getWindowMetricsSync === 'function');
                const candidateInset = Math.max(
                    availTop,
                    includeWorkAreaTop || hasHostMetrics ? heightReservedInset : 0
                );
                if (candidateInset <= 0) {
                    return 0;
                }
                if (includeWorkAreaTop || hasHostMetrics) {
                    return candidateInset;
                }
                const screenY = Number(window.screenY);
                if (!Number.isFinite(screenY)) {
                    return 0;
                }
                const threshold = Math.max(4, candidateInset / 2);
                return screenY <= threshold ? candidateInset : 0;
            } catch (_) {
                return 0;
            }
        }

        getNiriPetPhysicalCropTopInset() {
            const metrics = this.getDesktopMetrics();
            let cropInset = 0;
            let desktopWorkAreaInset = 0;
            let nonCropDesktopInset = 0;
            let hasCropEvidence = false;
            if (metrics && metrics.niriPetPhysicalCrop === true) {
                hasCropEvidence = true;
                const offset = Number(metrics.niriPetPhysicalCropOffsetY);
                if (Number.isFinite(offset) && offset > 0) {
                    cropInset = Math.max(cropInset, Math.round(offset));
                }
                const metricTopInset = Number(metrics.niriPetPhysicalCropTopInset);
                if (Number.isFinite(metricTopInset) && metricTopInset > 0) {
                    cropInset = Math.max(cropInset, Math.round(metricTopInset));
                }
                cropInset = Math.max(cropInset, this.getCropTopInsetFromBounds(
                    metrics.niriPetPhysicalCropBounds || metrics.contentBounds || metrics.bounds,
                    metrics.niriPetPhysicalCropVirtualBounds
                ));
            }
            if (metrics) {
                const metricDesktopWorkAreaInset = Number(metrics.desktopWorkAreaTopInset);
                if (Number.isFinite(metricDesktopWorkAreaInset) && metricDesktopWorkAreaInset > 0) {
                    if (hasCropEvidence) {
                        desktopWorkAreaInset = Math.max(desktopWorkAreaInset, Math.round(metricDesktopWorkAreaInset));
                    } else {
                        nonCropDesktopInset = Math.max(nonCropDesktopInset, Math.round(metricDesktopWorkAreaInset));
                    }
                }
            }
            const cssInset = this.getNiriPetPhysicalCropCssTopInset();
            if (cssInset > 0) {
                hasCropEvidence = true;
                cropInset = Math.max(cropInset, cssInset);
            }

            try {
                const api = window.__nekoNiriPetPhysicalCrop;
                if (!api || typeof api !== 'object') {
                    const combinedInset = hasCropEvidence ? cropInset + desktopWorkAreaInset : nonCropDesktopInset;
                    return Math.max(
                        combinedInset,
                        this.getDesktopWorkAreaTopInset({ includeWorkAreaTop: hasCropEvidence || combinedInset > 0 })
                    );
                }
                if (!(typeof api.isActive === 'function' && !api.isActive())) {
                    hasCropEvidence = true;
                    const state = typeof api.getState === 'function' ? api.getState() : null;
                    const offset = Number(state && state.offsetY);
                    if (Number.isFinite(offset) && offset > 0) {
                        cropInset = Math.max(cropInset, Math.round(offset));
                    }
                    cropInset = Math.max(cropInset, this.getCropTopInsetFromBounds(
                        state && state.cropBounds,
                        state && state.virtualBounds
                    ));
                }
            } catch (_) {
                const combinedInset = hasCropEvidence ? cropInset + desktopWorkAreaInset : nonCropDesktopInset;
                return Math.max(
                    combinedInset,
                    this.getDesktopWorkAreaTopInset({ includeWorkAreaTop: hasCropEvidence || combinedInset > 0 })
                );
            }
            const combinedInset = hasCropEvidence ? cropInset + desktopWorkAreaInset : nonCropDesktopInset;
            return Math.max(
                combinedInset,
                this.getDesktopWorkAreaTopInset({ includeWorkAreaTop: hasCropEvidence || combinedInset > 0 })
            );
        }

        getNiriPetVisibleTopSafeInset() {
            const metrics = this.getDesktopMetrics();
            let visibleInset = 0;
            let cropInset = 0;
            let hasCropEvidence = false;
            const recordVisibleInset = (value) => {
                const number = Number(value);
                if (Number.isFinite(number) && number > 0) {
                    visibleInset = Math.max(visibleInset, Math.round(number));
                }
            };
            const recordCropInset = (value) => {
                const number = Number(value);
                if (Number.isFinite(number) && number > 0) {
                    cropInset = Math.max(cropInset, Math.round(number));
                }
            };

            if (metrics) {
                if (this.hasNiriFixedUiEvidence(metrics)) {
                    recordVisibleInset(this.getNiriFixedUiMinimumTopInset());
                }
                recordVisibleInset(metrics.desktopWorkAreaTopInset);
                recordVisibleInset(metrics.niriWindowTopInset);
                recordVisibleInset(metrics.niriPetPhysicalCropVisibleTopInset);
                if (metrics.niriPetPhysicalCrop === true) {
                    hasCropEvidence = true;
                    recordCropInset(metrics.niriPetPhysicalCropOffsetY);
                    recordCropInset(metrics.niriPetPhysicalCropTopInset);
                    recordCropInset(this.getCropTopInsetFromBounds(
                        metrics.niriPetPhysicalCropBounds || metrics.contentBounds || metrics.bounds,
                        metrics.niriPetPhysicalCropVirtualBounds
                    ));
                }
            }

            const cssInset = this.getNiriPetPhysicalCropCssTopInset();
            if (cssInset > 0) {
                hasCropEvidence = true;
                recordCropInset(cssInset);
            }

            try {
                const api = window.__nekoNiriPetPhysicalCrop;
                if (api && typeof api === 'object'
                    && !(typeof api.isActive === 'function' && !api.isActive())) {
                    hasCropEvidence = true;
                    const state = typeof api.getState === 'function' ? api.getState() : null;
                    recordCropInset(state && state.offsetY);
                    recordCropInset(this.getCropTopInsetFromBounds(
                        state && state.cropBounds,
                        state && state.virtualBounds
                    ));
                }
            } catch (_) {}

            recordVisibleInset(this.getDesktopWorkAreaTopInset({
                includeWorkAreaTop: hasCropEvidence || visibleInset > 0 || cropInset > 0
            }));
            if (visibleInset <= 0 && hasCropEvidence) {
                recordVisibleInset(cropInset);
            }
            return visibleInset;
        }

        getBaseTopInset() {
            return this.buttonId === 'neko-page-tutorial-skip-btn' ? 18 : 14;
        }

        applyButtonSafeAreaFrame(inset) {
            const button = this.getElement();
            if (!button || !button.style || typeof button.style.setProperty !== 'function') {
                return;
            }
            const baseTop = this.getBaseTopInset();
            const safeInset = Number.isFinite(Number(inset)) ? Math.max(0, Math.round(Number(inset))) : 0;
            button.style.setProperty(
                'top',
                `calc(max(${baseTop}px, env(safe-area-inset-top)) + ${safeInset}px)`,
                'important'
            );
        }

        applySafeAreaVariables() {
            const root = this.document.documentElement;
            if (!root || !root.style) {
                return;
            }
            const transformedInset = this.getNiriPetPhysicalCropTopInset();
            const visibleInset = this.getNiriPetVisibleTopSafeInset();
            const fixedUiInset = Math.max(visibleInset, transformedInset);
            const button = this.getElement();
            const buttonUsesPortal = button ? this.isElementInFixedPortal(button) : this.usesFixedPortal();
            root.style.setProperty('--neko-tutorial-safe-area-top', transformedInset + 'px');
            root.style.setProperty('--neko-tutorial-visible-safe-area-top', fixedUiInset + 'px');
            this.applyButtonSafeAreaFrame(buttonUsesPortal ? fixedUiInset : transformedInset);
        }

        clearSafeAreaRefreshHooks() {
            if (typeof this.safeAreaCleanup === 'function') {
                this.safeAreaCleanup();
            }
            this.safeAreaCleanup = null;
        }

        installSafeAreaRefreshHooks(resources) {
            this.clearSafeAreaRefreshHooks();
            const refresh = () => this.applySafeAreaVariables();
            if (resources && typeof resources.addEventListener === 'function') {
                resources.addEventListener(window, 'neko:niri-pet-physical-crop-state-applied', refresh);
                if (typeof resources.setTimeout === 'function') {
                    [0, 80, 240, 600].forEach((delayMs) => resources.setTimeout(refresh, delayMs));
                }
                this.safeAreaCleanup = () => {};
                return;
            }

            const timers = [0, 80, 240, 600].map((delayMs) => window.setTimeout(refresh, delayMs));
            window.addEventListener('neko:niri-pet-physical-crop-state-applied', refresh);
            this.safeAreaCleanup = () => {
                window.removeEventListener('neko:niri-pet-physical-crop-state-applied', refresh);
                timers.forEach((timerId) => window.clearTimeout(timerId));
            };
        }

        show(options) {
            const normalizedOptions = options || {};
            const label = typeof normalizedOptions.label === 'string' && normalizedOptions.label
                ? normalizedOptions.label
                : '跳过';
            const onSkip = typeof normalizedOptions.onSkip === 'function'
                ? normalizedOptions.onSkip
                : null;

            this.ensureStyles();
            this.applySafeAreaVariables();
            this.hide();

            const button = this.document.createElement('button');
            button.id = this.buttonId;
            button.textContent = label;
            button.style.pointerEvents = 'auto';
            button.style.position = 'fixed';
            button.style.zIndex = '2147483647';
            button.style.touchAction = 'manipulation';

            let skipHandled = false;
            const resetSkipHandled = () => {
                skipHandled = false;
                button.disabled = false;
                button.removeAttribute('aria-disabled');
            };
            const handleSkipRequest = (event) => {
                if (skipHandled) {
                    return;
                }
                skipHandled = true;
                button.disabled = true;
                button.setAttribute('aria-disabled', 'true');

                if (event && typeof event.preventDefault === 'function') {
                    event.preventDefault();
                }
                if (event && typeof event.stopImmediatePropagation === 'function') {
                    event.stopImmediatePropagation();
                }
                if (event && typeof event.stopPropagation === 'function') {
                    event.stopPropagation();
                }

                if (!onSkip) {
                    return;
                }

                try {
                    Promise.resolve(onSkip(event)).catch((error) => {
                        console.warn('[TutorialSkipController] skip handler failed:', error);
                        resetSkipHandled();
                    });
                } catch (error) {
                    console.warn('[TutorialSkipController] skip handler threw:', error);
                    resetSkipHandled();
                }
            };

            const common = window.YuiGuideCommon;
            const resources = common && typeof common.createScopedTutorialResources === 'function'
                ? common.createScopedTutorialResources({ window: window })
                : null;
            this.installSafeAreaRefreshHooks(resources);
            const addListener = resources
                ? (type, listenerOptions) => resources.addEventListener(button, type, handleSkipRequest, listenerOptions)
                : (type, listenerOptions) => button.addEventListener(type, handleSkipRequest, listenerOptions);

            addListener('pointerdown');
            addListener('mousedown');
            addListener('touchstart', { passive: false });
            addListener('click');
            const host = this.getButtonHost();
            if (host && typeof host.appendChild === 'function') {
                host.appendChild(button);
            } else {
                this.document.body.appendChild(button);
            }

            this.currentButton = button;
            this.applySafeAreaVariables();
            this.currentResources = resources;
            this.currentCleanup = () => {
                if (this.currentResources && typeof this.currentResources.destroy === 'function') {
                    this.currentResources.destroy();
                    this.currentResources = null;
                    return;
                }
                button.removeEventListener('pointerdown', handleSkipRequest);
                button.removeEventListener('mousedown', handleSkipRequest);
                button.removeEventListener('touchstart', handleSkipRequest, { passive: false });
                button.removeEventListener('click', handleSkipRequest);
            };
        }

        hide() {
            if (typeof this.currentCleanup === 'function') {
                this.currentCleanup();
            }
            this.clearSafeAreaRefreshHooks();
            this.currentCleanup = null;
            this.currentResources = null;

            const existing = this.getElement();
            if (existing) {
                existing.remove();
            }
            this.removeEmptyFixedPortal();
            this.currentButton = null;
        }

        destroy() {
            this.hide();
        }
    }

    window.TutorialSkipController = {
        createController: function (options) {
            return new TutorialSkipController(options);
        },
        applySafeAreaVariables: function (options) {
            const normalizedOptions = options || {};
            const controller = new TutorialSkipController({
                document: normalizedOptions.document || document,
                buttonId: normalizedOptions.buttonId || 'neko-tutorial-skip-btn'
            });
            controller.applySafeAreaVariables();
        }
    };
})();
