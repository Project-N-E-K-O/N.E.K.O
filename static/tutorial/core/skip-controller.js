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
            this.styleId = `${this.buttonId}-style`;
        }

        getElement() {
            return this.document.getElementById(this.buttonId) || this.currentButton || null;
        }

        ensureStyles() {
            if (this.document.getElementById(this.styleId)) {
                return;
            }

            const selector = `#${typeof CSS !== 'undefined' && CSS && typeof CSS.escape === 'function'
                ? CSS.escape(this.buttonId)
                : String(this.buttonId).replace(/[^a-zA-Z0-9_-]/g, '\\$&')}`;
            const style = this.document.createElement('style');
            style.id = this.styleId;
            style.textContent = `
${selector} {
  position: fixed;
  top: max(14px, env(safe-area-inset-top));
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

        show(options) {
            const normalizedOptions = options || {};
            const label = typeof normalizedOptions.label === 'string' && normalizedOptions.label
                ? normalizedOptions.label
                : '跳过';
            const onSkip = typeof normalizedOptions.onSkip === 'function'
                ? normalizedOptions.onSkip
                : null;

            this.ensureStyles();
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
            const addListener = resources
                ? (type, listenerOptions) => resources.addEventListener(button, type, handleSkipRequest, listenerOptions)
                : (type, listenerOptions) => button.addEventListener(type, handleSkipRequest, listenerOptions);

            addListener('pointerdown');
            addListener('mousedown');
            addListener('touchstart', { passive: false });
            addListener('click');
            this.document.body.appendChild(button);

            this.currentButton = button;
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
            this.currentCleanup = null;
            this.currentResources = null;

            const existing = this.getElement();
            if (existing) {
                existing.remove();
            }
            this.currentButton = null;
        }

        destroy() {
            this.hide();
        }
    }

    window.TutorialSkipController = {
        createController: function (options) {
            return new TutorialSkipController(options);
        }
    };
})();
