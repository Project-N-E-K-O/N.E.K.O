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
            this.persistentSpotlightFrame = null;
            this.actionSpotlightFrame = null;
            this.bubble = null;
            this.bubbleTitle = null;
            this.bubbleBody = null;
            this.preview = null;
            this.previewTitle = null;
            this.previewList = null;
            this.cursorShell = null;
            this.cursorInner = null;
            this.cursorPosition = null;
            this.persistentHighlightedElement = null;
            this.actionHighlightedElement = null;
            this.highlightedElements = new Set();
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

                const persistentSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-persistent');
                persistentSpotlightFrame.hidden = true;
                persistentSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');

                const actionSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-action');
                actionSpotlightFrame.hidden = true;
                actionSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');

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
                stage.appendChild(persistentSpotlightFrame);
                stage.appendChild(actionSpotlightFrame);
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
                this.persistentSpotlightFrame = persistentSpotlightFrame;
                this.actionSpotlightFrame = actionSpotlightFrame;
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
                this.persistentSpotlightFrame = root.querySelector('.yui-guide-spotlight-frame-persistent');
                this.actionSpotlightFrame = root.querySelector('.yui-guide-spotlight-frame-action');
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
            const radius = this.getSpotlightRadius(element);
            const minEdge = Math.min(width, height);
            const isCircular = minEdge > 0
                && Math.abs(width - height) <= 18
                && radius >= ((minEdge / 2) - 6);

            return {
                left: left,
                top: top,
                right: right,
                bottom: bottom,
                width: width,
                height: height,
                radius: radius,
                isCircular: isCircular
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

        updateSpotlightFrame(frame, spotlightRect) {
            if (!frame) {
                return;
            }

            if (!spotlightRect) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                frame.classList.remove('is-circular-mask');
                return;
            }

            frame.hidden = false;
            frame.classList.add('is-visible');
            frame.classList.toggle('is-circular-mask', !!spotlightRect.isCircular);
            frame.style.left = spotlightRect.left + 'px';
            frame.style.top = spotlightRect.top + 'px';
            frame.style.width = spotlightRect.width + 'px';
            frame.style.height = spotlightRect.height + 'px';
            frame.style.borderRadius = spotlightRect.radius + 'px';
        }

        syncHighlightedElementClasses() {
            const nextElements = new Set();
            if (this.persistentHighlightedElement) {
                nextElements.add(this.persistentHighlightedElement);
            }
            if (this.actionHighlightedElement) {
                nextElements.add(this.actionHighlightedElement);
            }

            this.highlightedElements.forEach((element) => {
                if (!nextElements.has(element)) {
                    element.classList.remove('yui-guide-chat-target');
                }
            });

            nextElements.forEach((element) => {
                element.classList.add('yui-guide-chat-target');
            });

            this.highlightedElements = nextElements;
        }

        refreshSpotlight() {
            this.ensureRoot();

            const persistentRect = this.getSpotlightRect(this.persistentHighlightedElement);
            const actionRect = this.getSpotlightRect(this.actionHighlightedElement);

            if (this.backdrop) {
                if (actionRect) {
                    if (actionRect.isCircular) {
                        this.backdrop.hidden = true;
                        this.backdrop.classList.remove('is-visible');
                    } else {
                        this.backdrop.hidden = false;
                        this.backdrop.classList.add('is-visible');
                        this.applyBackdropMask(actionRect);
                    }
                } else {
                    this.backdrop.hidden = true;
                    this.backdrop.classList.remove('is-visible');
                }
            }

            this.updateSpotlightFrame(this.persistentSpotlightFrame, persistentRect);
            this.updateSpotlightFrame(this.actionSpotlightFrame, actionRect);
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

        setPersistentSpotlight(element) {
            this.ensureRoot();
            this.persistentHighlightedElement = element || null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        activateSpotlight(element) {
            this.ensureRoot();
            this.actionHighlightedElement = element || null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        clearActionSpotlight() {
            this.ensureRoot();
            this.actionHighlightedElement = null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            if (!this.persistentHighlightedElement) {
                this.stopSpotlightTracking();
            }
        }

        clearPersistentSpotlight() {
            this.ensureRoot();
            this.persistentHighlightedElement = null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            if (!this.actionHighlightedElement) {
                this.stopSpotlightTracking();
            }
        }

        clearSpotlight() {
            this.ensureRoot();
            this.stopSpotlightTracking();
            this.persistentHighlightedElement = null;
            this.actionHighlightedElement = null;
            this.syncHighlightedElementClasses();

            if (this.backdrop) {
                this.backdrop.hidden = true;
                this.backdrop.classList.remove('is-visible');
            }
            this.updateSpotlightFrame(this.persistentSpotlightFrame, null);
            this.updateSpotlightFrame(this.actionSpotlightFrame, null);
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
            this.document.body.classList.add('yui-guide-ghost-cursor-active');
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

            this.document.body.classList.add('yui-guide-ghost-cursor-active');
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
            this.document.body.classList.remove('yui-guide-ghost-cursor-active');
            this.cursorShell.hidden = true;
            this.cursorShell.classList.remove('is-visible');
        }

        destroy() {
            this.document.body.classList.remove('yui-taking-over');
            this.document.body.classList.remove('yui-guide-ghost-cursor-active');
            this.clearSpotlight();
            if (this.root && this.root.isConnected) {
                this.root.remove();
            }
            this.root = null;
            this.stage = null;
            this.backdrop = null;
            this.backdropPanels = null;
            this.persistentSpotlightFrame = null;
            this.actionSpotlightFrame = null;
            this.bubble = null;
            this.bubbleTitle = null;
            this.bubbleBody = null;
            this.preview = null;
            this.previewTitle = null;
            this.previewList = null;
            this.cursorShell = null;
            this.cursorInner = null;
            this.cursorPosition = null;
            this.persistentHighlightedElement = null;
            this.actionHighlightedElement = null;
            this.highlightedElements = new Set();
        }
    }

    window.YuiGuideOverlay = YuiGuideOverlay;
})();
