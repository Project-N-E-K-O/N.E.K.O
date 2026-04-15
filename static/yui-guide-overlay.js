(function () {
    'use strict';

    const ROOT_ID = 'yui-guide-overlay';

    function createElement(tagName, className) {
        const element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        return element;
    }

    class YuiGuideOverlay {
        constructor(doc) {
            this.document = doc || document;
            this.root = null;
            this.stage = null;
            this.backdrop = null;
            this.backdropPanels = null;
            this.spotlightFrame = null;
            this.bubble = null;
            this.bubbleTitle = null;
            this.bubbleBody = null;
            this.preview = null;
            this.previewTitle = null;
            this.previewList = null;
            this.cursorShell = null;
            this.cursorInner = null;
            this.cursorPosition = null;
            this.highlightedElement = null;
            this.spotlightRefreshTimer = null;
            this.boundRefreshSpotlight = this.refreshSpotlight.bind(this);
        }

        ensureRoot() {
            if (this.root && this.root.isConnected) {
                return this.root;
            }

            let root = this.document.getElementById(ROOT_ID);
            if (!root) {
                root = createElement('div', 'yui-guide-overlay');
                root.id = ROOT_ID;
                root.setAttribute('aria-hidden', 'true');
                root.setAttribute('data-yui-cursor-hidden', 'true');

                const stage = createElement('div', 'yui-guide-stage');
                stage.setAttribute('data-yui-cursor-hidden', 'true');

                const backdrop = createElement('div', 'yui-guide-backdrop');
                backdrop.hidden = true;
                backdrop.setAttribute('data-yui-cursor-hidden', 'true');
                const backdropTop = createElement('div', 'yui-guide-backdrop-panel yui-guide-backdrop-top');
                const backdropLeft = createElement('div', 'yui-guide-backdrop-panel yui-guide-backdrop-left');
                const backdropRight = createElement('div', 'yui-guide-backdrop-panel yui-guide-backdrop-right');
                const backdropBottom = createElement('div', 'yui-guide-backdrop-panel yui-guide-backdrop-bottom');
                backdrop.appendChild(backdropTop);
                backdrop.appendChild(backdropLeft);
                backdrop.appendChild(backdropRight);
                backdrop.appendChild(backdropBottom);

                const spotlightFrame = createElement('div', 'yui-guide-spotlight-frame');
                spotlightFrame.hidden = true;
                spotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');

                const bubble = createElement('section', 'yui-guide-bubble');
                bubble.hidden = true;
                const bubbleTitle = createElement('div', 'yui-guide-bubble-title');
                const bubbleBody = createElement('div', 'yui-guide-bubble-body');
                bubble.appendChild(bubbleTitle);
                bubble.appendChild(bubbleBody);

                const preview = createElement('section', 'yui-guide-preview');
                preview.hidden = true;
                const previewTitle = createElement('div', 'yui-guide-preview-title');
                const previewList = createElement('div', 'yui-guide-preview-list');
                preview.appendChild(previewTitle);
                preview.appendChild(previewList);

                const cursorShell = createElement('div', 'yui-guide-cursor-shell');
                cursorShell.hidden = true;
                const cursorInner = createElement('div', 'yui-guide-cursor');
                cursorShell.appendChild(cursorInner);

                stage.appendChild(backdrop);
                stage.appendChild(spotlightFrame);
                stage.appendChild(bubble);
                stage.appendChild(preview);
                stage.appendChild(cursorShell);
                root.appendChild(stage);
                this.document.body.appendChild(root);

                this.stage = stage;
                this.backdrop = backdrop;
                this.backdropPanels = {
                    top: backdropTop,
                    left: backdropLeft,
                    right: backdropRight,
                    bottom: backdropBottom
                };
                this.spotlightFrame = spotlightFrame;
                this.bubble = bubble;
                this.bubbleTitle = bubbleTitle;
                this.bubbleBody = bubbleBody;
                this.preview = preview;
                this.previewTitle = previewTitle;
                this.previewList = previewList;
                this.cursorShell = cursorShell;
                this.cursorInner = cursorInner;
            } else {
                this.stage = root.querySelector('.yui-guide-stage');
                this.backdrop = root.querySelector('.yui-guide-backdrop');
                this.backdropPanels = {
                    top: root.querySelector('.yui-guide-backdrop-top'),
                    left: root.querySelector('.yui-guide-backdrop-left'),
                    right: root.querySelector('.yui-guide-backdrop-right'),
                    bottom: root.querySelector('.yui-guide-backdrop-bottom')
                };
                this.spotlightFrame = root.querySelector('.yui-guide-spotlight-frame');
                this.bubble = root.querySelector('.yui-guide-bubble');
                this.bubbleTitle = root.querySelector('.yui-guide-bubble-title');
                this.bubbleBody = root.querySelector('.yui-guide-bubble-body');
                this.preview = root.querySelector('.yui-guide-preview');
                this.previewTitle = root.querySelector('.yui-guide-preview-title');
                this.previewList = root.querySelector('.yui-guide-preview-list');
                this.cursorShell = root.querySelector('.yui-guide-cursor-shell');
                this.cursorInner = root.querySelector('.yui-guide-cursor');
            }

            this.root = root;
            return root;
        }

        getSpotlightRect(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }

            const padding = 12;
            const left = Math.max(0, Math.floor(rect.left - padding));
            const top = Math.max(0, Math.floor(rect.top - padding));
            const right = Math.min(window.innerWidth, Math.ceil(rect.right + padding));
            const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom + padding));
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);

            return {
                left: left,
                top: top,
                right: right,
                bottom: bottom,
                width: width,
                height: height,
                radius: this.getSpotlightRadius(element)
            };
        }

        getSpotlightRadius(element) {
            if (!element || typeof window.getComputedStyle !== 'function') {
                return 24;
            }

            try {
                const computed = window.getComputedStyle(element);
                const radius = parseFloat(computed.borderTopLeftRadius || computed.borderRadius || '');
                if (Number.isFinite(radius) && radius > 0) {
                    return radius + 12;
                }
            } catch (_) {}

            return 24;
        }

        applyBackdropMask(spotlightRect) {
            if (!this.backdropPanels || !spotlightRect) {
                return;
            }

            const panels = this.backdropPanels;
            const viewportWidth = window.innerWidth;
            const viewportHeight = window.innerHeight;

            panels.top.style.top = '0px';
            panels.top.style.left = '0px';
            panels.top.style.width = viewportWidth + 'px';
            panels.top.style.height = spotlightRect.top + 'px';

            panels.left.style.top = spotlightRect.top + 'px';
            panels.left.style.left = '0px';
            panels.left.style.width = spotlightRect.left + 'px';
            panels.left.style.height = spotlightRect.height + 'px';

            panels.right.style.top = spotlightRect.top + 'px';
            panels.right.style.left = spotlightRect.right + 'px';
            panels.right.style.width = Math.max(0, viewportWidth - spotlightRect.right) + 'px';
            panels.right.style.height = spotlightRect.height + 'px';

            panels.bottom.style.top = spotlightRect.bottom + 'px';
            panels.bottom.style.left = '0px';
            panels.bottom.style.width = viewportWidth + 'px';
            panels.bottom.style.height = Math.max(0, viewportHeight - spotlightRect.bottom) + 'px';
        }

        refreshSpotlight() {
            this.ensureRoot();

            if (!this.highlightedElement) {
                return;
            }

            const spotlightRect = this.getSpotlightRect(this.highlightedElement);
            if (!spotlightRect) {
                return;
            }

            this.applyBackdropMask(spotlightRect);

            if (this.spotlightFrame) {
                this.spotlightFrame.hidden = false;
                this.spotlightFrame.classList.add('is-visible');
                this.spotlightFrame.style.left = spotlightRect.left + 'px';
                this.spotlightFrame.style.top = spotlightRect.top + 'px';
                this.spotlightFrame.style.width = spotlightRect.width + 'px';
                this.spotlightFrame.style.height = spotlightRect.height + 'px';
                this.spotlightFrame.style.borderRadius = spotlightRect.radius + 'px';
            }
        }

        startSpotlightTracking() {
            if (this.spotlightRefreshTimer) {
                return;
            }

            window.addEventListener('resize', this.boundRefreshSpotlight, true);
            window.addEventListener('scroll', this.boundRefreshSpotlight, true);
            this.spotlightRefreshTimer = window.setInterval(this.boundRefreshSpotlight, 120);
        }

        stopSpotlightTracking() {
            if (this.spotlightRefreshTimer) {
                window.clearInterval(this.spotlightRefreshTimer);
                this.spotlightRefreshTimer = null;
            }

            window.removeEventListener('resize', this.boundRefreshSpotlight, true);
            window.removeEventListener('scroll', this.boundRefreshSpotlight, true);
        }

        setTakingOver(active) {
            this.ensureRoot();
            this.document.body.classList.toggle('yui-taking-over', !!active);
            this.root.classList.toggle('is-taking-over', !!active);
        }

        setAngry(active) {
            this.ensureRoot();
            this.root.classList.toggle('is-angry', !!active);
            if (this.bubble) {
                this.bubble.classList.toggle('is-angry', !!active);
            }
        }

        positionBubble(anchorRect) {
            this.ensureRoot();

            let left = Math.max(24, window.innerWidth - 360);
            let top = 32;

            if (anchorRect && Number.isFinite(anchorRect.left) && Number.isFinite(anchorRect.top)) {
                left = anchorRect.right + 24;
                top = Math.max(20, anchorRect.top - 8);

                if (left + 320 > window.innerWidth - 16) {
                    left = Math.max(16, anchorRect.left - 336);
                }

                if (top + 220 > window.innerHeight - 16) {
                    top = Math.max(16, window.innerHeight - 236);
                }
            }

            this.bubble.style.left = Math.round(left) + 'px';
            this.bubble.style.top = Math.round(top) + 'px';
        }

        showBubble(text, options) {
            this.ensureRoot();

            const normalizedOptions = options || {};
            const title = typeof normalizedOptions.title === 'string' ? normalizedOptions.title.trim() : '';
            const emotion = typeof normalizedOptions.emotion === 'string' ? normalizedOptions.emotion.trim() : 'neutral';

            this.positionBubble(normalizedOptions.anchorRect || null);
            this.bubbleTitle.textContent = title || 'Yui';
            this.bubbleTitle.hidden = false;
            this.bubbleBody.textContent = text || '';
            this.bubble.hidden = false;
            this.bubble.dataset.emotion = emotion || 'neutral';
            this.bubble.classList.add('is-visible');
        }

        hideBubble() {
            this.ensureRoot();
            this.bubble.hidden = true;
            this.bubble.classList.remove('is-visible');
            delete this.bubble.dataset.emotion;
        }

        showPluginPreview(items, options) {
            this.ensureRoot();

            const previewItems = Array.isArray(items) && items.length > 0 ? items : [
                'WebSearch',
                'B站弹幕',
                '米家控制',
                '天气同步',
                '日程提醒'
            ];

            this.previewTitle.textContent = (options && options.title) || '插件预演';
            this.previewList.innerHTML = '';
            previewItems.forEach(function (item, index) {
                const card = createElement('div', 'yui-guide-preview-card');
                card.style.setProperty('--yui-guide-preview-order', String(index));

                const chip = createElement('div', 'yui-guide-preview-card-chip');
                chip.textContent = 'Plugin';
                const label = createElement('div', 'yui-guide-preview-card-label');
                label.textContent = String(item);

                card.appendChild(chip);
                card.appendChild(label);
                this.previewList.appendChild(card);
            }, this);

            this.preview.hidden = false;
            this.preview.classList.add('is-visible');
        }

        hidePluginPreview() {
            this.ensureRoot();
            this.preview.hidden = true;
            this.preview.classList.remove('is-visible');
            this.previewList.innerHTML = '';
        }

        activateSpotlight(element) {
            this.ensureRoot();

            if (this.highlightedElement && this.highlightedElement !== element) {
                this.highlightedElement.classList.remove('yui-guide-chat-target');
            }

            this.highlightedElement = element || null;

            if (this.backdrop) {
                this.backdrop.hidden = false;
                this.backdrop.classList.add('is-visible');
            }

            if (this.highlightedElement) {
                this.highlightedElement.classList.add('yui-guide-chat-target');
            }

            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        clearSpotlight() {
            this.ensureRoot();
            this.stopSpotlightTracking();

            if (this.backdrop) {
                this.backdrop.hidden = true;
                this.backdrop.classList.remove('is-visible');
            }

            if (this.spotlightFrame) {
                this.spotlightFrame.hidden = true;
                this.spotlightFrame.classList.remove('is-visible');
            }

            if (this.highlightedElement) {
                this.highlightedElement.classList.remove('yui-guide-chat-target');
                this.highlightedElement = null;
            }
        }

        hasCursorPosition() {
            return !!this.cursorPosition;
        }

        getCursorPosition() {
            if (!this.cursorPosition) {
                return null;
            }

            return {
                x: this.cursorPosition.x,
                y: this.cursorPosition.y
            };
        }

        showCursorAt(x, y) {
            this.ensureRoot();
            this.cursorShell.hidden = false;
            this.cursorShell.classList.add('is-visible');
            this.cursorShell.style.transitionDuration = '0ms';
            this.cursorShell.style.transform = 'translate(' + Math.round(x) + 'px, ' + Math.round(y) + 'px)';
            this.cursorPosition = { x: x, y: y };
        }

        moveCursorTo(x, y, options) {
            this.ensureRoot();

            const normalizedOptions = options || {};
            const durationMs = Number.isFinite(normalizedOptions.durationMs) ? normalizedOptions.durationMs : 480;

            if (!this.cursorPosition) {
                this.showCursorAt(x, y);
                return Promise.resolve();
            }

            this.cursorShell.hidden = false;
            this.cursorShell.classList.add('is-visible');

            return new Promise((resolve) => {
                let settled = false;
                const finish = () => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    this.cursorShell.removeEventListener('transitionend', onTransitionEnd);
                    resolve();
                };
                const onTransitionEnd = (event) => {
                    if (event.target === this.cursorShell) {
                        finish();
                    }
                };

                this.cursorShell.addEventListener('transitionend', onTransitionEnd);
                this.cursorShell.style.transitionDuration = String(durationMs) + 'ms';

                window.requestAnimationFrame(() => {
                    this.cursorShell.style.transform = 'translate(' + Math.round(x) + 'px, ' + Math.round(y) + 'px)';
                });

                window.setTimeout(finish, durationMs + 80);
                this.cursorPosition = { x: x, y: y };
            });
        }

        clickCursor() {
            this.ensureRoot();
            if (!this.cursorInner) {
                return;
            }
            this.cursorInner.classList.remove('is-clicking');
            void this.cursorInner.offsetWidth;
            this.cursorInner.classList.add('is-clicking');
            window.setTimeout(() => {
                if (this.cursorInner) {
                    this.cursorInner.classList.remove('is-clicking');
                }
            }, 260);
        }

        wobbleCursor() {
            this.ensureRoot();
            if (!this.cursorInner) {
                return;
            }
            this.cursorInner.classList.remove('is-wobbling');
            void this.cursorInner.offsetWidth;
            this.cursorInner.classList.add('is-wobbling');
            window.setTimeout(() => {
                if (this.cursorInner) {
                    this.cursorInner.classList.remove('is-wobbling');
                }
            }, 700);
        }

        hideCursor() {
            this.ensureRoot();
            this.cursorShell.hidden = true;
            this.cursorShell.classList.remove('is-visible');
        }

        destroy() {
            this.document.body.classList.remove('yui-taking-over');
            this.clearSpotlight();
            if (this.root && this.root.isConnected) {
                this.root.remove();
            }
            this.root = null;
            this.stage = null;
            this.backdrop = null;
            this.backdropPanels = null;
            this.spotlightFrame = null;
            this.bubble = null;
            this.bubbleTitle = null;
            this.bubbleBody = null;
            this.preview = null;
            this.previewTitle = null;
            this.previewList = null;
            this.cursorShell = null;
            this.cursorInner = null;
            this.cursorPosition = null;
            this.highlightedElement = null;
        }
    }

    window.YuiGuideOverlay = YuiGuideOverlay;
})();
