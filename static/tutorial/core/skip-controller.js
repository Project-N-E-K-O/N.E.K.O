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
        }

        getElement() {
            return this.document.getElementById(this.buttonId) || this.currentButton || null;
        }

        show(options) {
            const normalizedOptions = options || {};
            const label = typeof normalizedOptions.label === 'string' && normalizedOptions.label
                ? normalizedOptions.label
                : '跳过';
            const onSkip = typeof normalizedOptions.onSkip === 'function'
                ? normalizedOptions.onSkip
                : null;

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
