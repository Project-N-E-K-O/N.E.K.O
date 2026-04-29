(function () {
    'use strict';

    const ROOT_ID = 'yui-guide-overlay';
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const BACKDROP_MASK_ID = ROOT_ID + '-mask';
    const EXTRA_SPOTLIGHT_ENTRY_COUNT = 6;
    const DEFAULT_SPOTLIGHT_PADDING = 6;
    const BACKDROP_CUTOUT_INSET = 4;

    function createElement(tagName, className) {
        const element = document.createElement(tagName);
        if (className) {
            element.className = className;
        }
        return element;
    }

    function createSvgElement(tagName, className) {
        const element = document.createElementNS(SVG_NS, tagName);
        if (className) {
            element.setAttribute('class', className);
        }
        return element;
    }

    function readSpotlightNumberAttr(element, attributeName) {
        if (!element || typeof element.getAttribute !== 'function' || !attributeName) {
            return null;
        }

        const rawValue = element.getAttribute(attributeName);
        const value = Number.parseFloat(rawValue || '');
        return Number.isFinite(value) ? value : null;
    }

    function ensureSpotlightFrameDecorations(frame) {
        if (!frame) {
            return;
        }

        if (!frame.querySelector('.yui-guide-spotlight-chrome')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-chrome'));
        }
        if (!frame.querySelector('.yui-guide-spotlight-ear-left')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-decoration yui-guide-spotlight-ear-left'));
        }
        if (!frame.querySelector('.yui-guide-spotlight-ear-right')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-decoration yui-guide-spotlight-ear-right'));
        }
        if (!frame.querySelector('.yui-guide-spotlight-paw')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-decoration yui-guide-spotlight-paw'));
        }
        if (!frame.querySelector('.yui-guide-spotlight-circle-skin')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-circle-skin'));
        }
    }

    class YuiGuideOverlay {
        constructor(doc) {
            this.document = doc || document;
            this.root = null;
            this.stage = null;
            this.backdrop = null;
            this.backdropMask = null;
            this.backdropBase = null;
            this.backdropPersistentCutout = null;
            this.backdropActionCutout = null;
            this.backdropSecondaryActionCutout = null;
            this.backdropFill = null;
            this.persistentSpotlightFrame = null;
            this.actionSpotlightFrame = null;
            this.secondaryActionSpotlightFrame = null;
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
            this.secondaryActionHighlightedElement = null;
            this.extraSpotlightElements = [];
            this.extraSpotlightEntries = [];
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

                const backdrop = createSvgElement('svg', 'yui-guide-backdrop');
                backdrop.hidden = true;
                backdrop.setAttribute('data-yui-cursor-hidden', 'true');
                backdrop.setAttribute('aria-hidden', 'true');
                backdrop.setAttribute('preserveAspectRatio', 'none');

                const defs = createSvgElement('defs');
                const mask = createSvgElement('mask');
                mask.id = BACKDROP_MASK_ID;
                mask.setAttribute('maskUnits', 'userSpaceOnUse');
                mask.setAttribute('maskContentUnits', 'userSpaceOnUse');

                const backdropBase = createSvgElement('rect', 'yui-guide-backdrop-base');
                backdropBase.setAttribute('fill', 'white');

                const backdropPersistentCutout = createSvgElement('rect', 'yui-guide-backdrop-cutout yui-guide-backdrop-cutout-persistent');
                backdropPersistentCutout.setAttribute('fill', 'black');
                backdropPersistentCutout.hidden = true;

                const backdropActionCutout = createSvgElement('rect', 'yui-guide-backdrop-cutout yui-guide-backdrop-cutout-action');
                backdropActionCutout.setAttribute('fill', 'black');
                backdropActionCutout.hidden = true;

                const backdropSecondaryActionCutout = createSvgElement('rect', 'yui-guide-backdrop-cutout yui-guide-backdrop-cutout-action yui-guide-backdrop-cutout-action-secondary');
                backdropSecondaryActionCutout.setAttribute('fill', 'black');
                backdropSecondaryActionCutout.hidden = true;

                const extraSpotlightEntries = [];

                const backdropFill = createSvgElement('rect', 'yui-guide-backdrop-fill');
                backdropFill.setAttribute('fill', 'rgba(3, 7, 18, 0.76)');
                backdropFill.setAttribute('mask', 'url(#' + BACKDROP_MASK_ID + ')');

                mask.appendChild(backdropBase);
                mask.appendChild(backdropPersistentCutout);
                mask.appendChild(backdropActionCutout);
                mask.appendChild(backdropSecondaryActionCutout);
                defs.appendChild(mask);
                backdrop.appendChild(defs);
                backdrop.appendChild(backdropFill);

                const persistentSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-persistent');
                persistentSpotlightFrame.hidden = true;
                persistentSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');
                ensureSpotlightFrameDecorations(persistentSpotlightFrame);

                const actionSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-action');
                actionSpotlightFrame.hidden = true;
                actionSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');
                ensureSpotlightFrameDecorations(actionSpotlightFrame);

                const secondaryActionSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-action yui-guide-spotlight-frame-action-secondary');
                secondaryActionSpotlightFrame.hidden = true;
                secondaryActionSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');
                ensureSpotlightFrameDecorations(secondaryActionSpotlightFrame);

                for (let index = 0; index < EXTRA_SPOTLIGHT_ENTRY_COUNT; index += 1) {
                    const cutout = createSvgElement(
                        'rect',
                        'yui-guide-backdrop-cutout yui-guide-backdrop-cutout-action yui-guide-backdrop-cutout-extra'
                    );
                    cutout.setAttribute('fill', 'black');
                    cutout.hidden = true;
                    cutout.setAttribute('data-yui-guide-extra-index', String(index));
                    mask.appendChild(cutout);

                    const frame = createElement(
                        'div',
                        'yui-guide-spotlight-frame yui-guide-spotlight-frame-action yui-guide-spotlight-frame-extra'
                    );
                    frame.hidden = true;
                    frame.setAttribute('data-yui-cursor-hidden', 'true');
                    frame.setAttribute('data-yui-guide-extra-index', String(index));
                    ensureSpotlightFrameDecorations(frame);
                    stage.appendChild(frame);

                    extraSpotlightEntries.push({ cutout: cutout, frame: frame });
                }

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
                stage.appendChild(secondaryActionSpotlightFrame);
                stage.appendChild(bubble);
                stage.appendChild(preview);
                stage.appendChild(cursorShell);
                root.appendChild(stage);
                this.document.body.appendChild(root);

                this.stage = stage;
                this.backdrop = backdrop;
                this.backdropMask = mask;
                this.backdropBase = backdropBase;
                this.backdropPersistentCutout = backdropPersistentCutout;
                this.backdropActionCutout = backdropActionCutout;
                this.backdropSecondaryActionCutout = backdropSecondaryActionCutout;
                this.backdropFill = backdropFill;
                this.persistentSpotlightFrame = persistentSpotlightFrame;
                this.actionSpotlightFrame = actionSpotlightFrame;
                this.secondaryActionSpotlightFrame = secondaryActionSpotlightFrame;
                this.bubble = bubble;
                this.bubbleTitle = bubbleTitle;
                this.bubbleBody = bubbleBody;
                this.preview = preview;
                this.previewTitle = previewTitle;
                this.previewList = previewList;
                this.cursorShell = cursorShell;
                this.cursorInner = cursorInner;
                this.extraSpotlightEntries = extraSpotlightEntries;
            } else {
                this.stage = root.querySelector('.yui-guide-stage');
                this.backdrop = root.querySelector('.yui-guide-backdrop');
                this.backdropMask = root.querySelector('mask#' + BACKDROP_MASK_ID);
                this.backdropBase = root.querySelector('.yui-guide-backdrop-base');
                this.backdropPersistentCutout = root.querySelector('.yui-guide-backdrop-cutout-persistent');
                this.backdropActionCutout = root.querySelector('.yui-guide-backdrop-cutout-action');
                this.backdropSecondaryActionCutout = root.querySelector('.yui-guide-backdrop-cutout-action-secondary');
                this.backdropFill = root.querySelector('.yui-guide-backdrop-fill');
                this.persistentSpotlightFrame = root.querySelector('.yui-guide-spotlight-frame-persistent');
                this.actionSpotlightFrame = root.querySelector('.yui-guide-spotlight-frame-action');
                this.secondaryActionSpotlightFrame = root.querySelector('.yui-guide-spotlight-frame-action-secondary');
                ensureSpotlightFrameDecorations(this.persistentSpotlightFrame);
                ensureSpotlightFrameDecorations(this.actionSpotlightFrame);
                ensureSpotlightFrameDecorations(this.secondaryActionSpotlightFrame);
                this.bubble = root.querySelector('.yui-guide-bubble');
                this.bubbleTitle = root.querySelector('.yui-guide-bubble-title');
                this.bubbleBody = root.querySelector('.yui-guide-bubble-body');
                this.preview = root.querySelector('.yui-guide-preview');
                this.previewTitle = root.querySelector('.yui-guide-preview-title');
                this.previewList = root.querySelector('.yui-guide-preview-list');
                this.cursorShell = root.querySelector('.yui-guide-cursor-shell');
                this.cursorInner = root.querySelector('.yui-guide-cursor');
                this.extraSpotlightEntries = [];
                const cutouts = root.querySelectorAll('.yui-guide-backdrop-cutout-extra');
                const frames = root.querySelectorAll('.yui-guide-spotlight-frame-extra');
                const count = Math.max(cutouts.length, frames.length);
                for (let index = 0; index < count; index += 1) {
                    ensureSpotlightFrameDecorations(frames[index] || null);
                    this.extraSpotlightEntries.push({
                        cutout: cutouts[index] || null,
                        frame: frames[index] || null
                    });
                }
            }

            this.root = root;
            return root;
        }

        ensureExtraSpotlightEntry(index) {
            const normalizedIndex = Number(index);
            if (!Number.isInteger(normalizedIndex) || normalizedIndex < 0) {
                return null;
            }

            this.ensureRoot();
            if (this.extraSpotlightEntries[normalizedIndex]) {
                return this.extraSpotlightEntries[normalizedIndex];
            }
            return null;
        }

        setExtraSpotlights(elements) {
            this.ensureRoot();
            this.extraSpotlightElements = (Array.isArray(elements) ? elements : [])
                .filter((element) => !!element && typeof element.getBoundingClientRect === 'function');
            this.refreshSpotlight();
            if (
                this.persistentHighlightedElement
                || this.actionHighlightedElement
                || this.secondaryActionHighlightedElement
                || this.extraSpotlightElements.length > 0
            ) {
                this.startSpotlightTracking();
            } else {
                this.stopSpotlightTracking();
            }
        }

        clearExtraSpotlights() {
            this.ensureRoot();
            this.extraSpotlightElements = [];
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.updateBackdropCutout(entry.cutout, null);
                this.updateSpotlightFrame(entry.frame, null);
            });
            this.refreshSpotlight();
            if (
                !this.persistentHighlightedElement
                && !this.actionHighlightedElement
                && !this.secondaryActionHighlightedElement
            ) {
                this.stopSpotlightTracking();
            }
        }

        syncBackdropViewport() {
            if (!this.backdrop) {
                return;
            }

            const width = Math.max(1, Math.round(window.innerWidth || 0));
            const height = Math.max(1, Math.round(window.innerHeight || 0));
            this.backdrop.setAttribute('viewBox', '0 0 ' + width + ' ' + height);

            [this.backdropBase, this.backdropFill].forEach((rect) => {
                if (!rect) {
                    return;
                }
                rect.setAttribute('x', '0');
                rect.setAttribute('y', '0');
                rect.setAttribute('width', String(width));
                rect.setAttribute('height', String(height));
            });
        }

        getSpotlightRect(element) {
            if (!element || typeof element.getBoundingClientRect !== 'function') {
                return null;
            }

            const rect = element.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }

            const paddingValue = readSpotlightNumberAttr(element, 'data-yui-guide-spotlight-padding');
            const padding = paddingValue == null ? DEFAULT_SPOTLIGHT_PADDING : paddingValue;
            const rawWidth = Math.max(0, Math.round(rect.width));
            const rawHeight = Math.max(0, Math.round(rect.height));
            const rawMinEdge = Math.min(rawWidth, rawHeight);
            const radiusOverride = readSpotlightNumberAttr(element, 'data-yui-guide-spotlight-radius');
            const geometryHint = typeof element.getAttribute === 'function'
                ? (element.getAttribute('data-yui-guide-spotlight-geometry') || '').trim().toLowerCase()
                : '';
            const rawRadius = radiusOverride != null
                ? Math.max(0, radiusOverride)
                : Math.max(0, this.getSpotlightRadius(element, padding) - padding);
            const sizeTolerance = Math.max(8, Math.round(rawMinEdge * 0.12));
            const left = Math.max(0, Math.floor(rect.left - padding));
            const top = Math.max(0, Math.floor(rect.top - padding));
            const right = Math.min(window.innerWidth, Math.ceil(rect.right + padding));
            const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom + padding));
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);
            const radius = this.getSpotlightRadius(element, padding);
            const isCircular = geometryHint === 'circle'
                && rawMinEdge > 0
                && Math.abs(rawWidth - rawHeight) <= sizeTolerance
                && rawRadius >= ((rawMinEdge / 2) - 4);

            return {
                left: left,
                top: top,
                right: right,
                bottom: bottom,
                width: width,
                height: height,
                radius: radius,
                padding: padding,
                isCircular: isCircular
            };
        }

        getSpotlightRadius(element, padding) {
            if (!element || typeof window.getComputedStyle !== 'function') {
                return 24;
            }

            const radiusPadding = Number.isFinite(padding) ? padding : DEFAULT_SPOTLIGHT_PADDING;
            const radiusOverride = readSpotlightNumberAttr(element, 'data-yui-guide-spotlight-radius');
            if (radiusOverride != null) {
                return Math.max(0, radiusOverride);
            }

            try {
                const computed = window.getComputedStyle(element);
                const radius = parseFloat(computed.borderTopLeftRadius || computed.borderRadius || '');
                if (Number.isFinite(radius) && radius > 0) {
                    return Math.max(0, radius + radiusPadding);
                }
            } catch (_) {}

            return 24;
        }

        updateBackdropCutout(cutout, spotlightRect) {
            if (!cutout) {
                return;
            }

            if (!spotlightRect) {
                cutout.hidden = true;
                cutout.setAttribute('x', '0');
                cutout.setAttribute('y', '0');
                cutout.setAttribute('width', '0');
                cutout.setAttribute('height', '0');
                cutout.setAttribute('rx', '0');
                cutout.setAttribute('ry', '0');
                cutout.style.display = 'none';
                return;
            }

            cutout.hidden = false;
            cutout.style.removeProperty('display');
            const maxInset = spotlightRect.padding == null
                ? BACKDROP_CUTOUT_INSET
                : Math.max(0, spotlightRect.padding);
            const inset = Math.max(0, Math.min(
                BACKDROP_CUTOUT_INSET,
                maxInset,
                Math.floor(spotlightRect.width / 2),
                Math.floor(spotlightRect.height / 2)
            ));
            const x = spotlightRect.left + inset;
            const y = spotlightRect.top + inset;
            const width = Math.max(0, spotlightRect.width - (inset * 2));
            const height = Math.max(0, spotlightRect.height - (inset * 2));
            const radius = Math.max(0, spotlightRect.radius - inset);
            cutout.setAttribute('x', String(x));
            cutout.setAttribute('y', String(y));
            cutout.setAttribute('width', String(width));
            cutout.setAttribute('height', String(height));
            cutout.setAttribute('rx', String(radius));
            cutout.setAttribute('ry', String(radius));
        }

        updateSpotlightFrame(frame, spotlightRect, options) {
            if (!frame) {
                return;
            }

            const normalizedOptions = options || {};
            const allowMask = normalizedOptions.allowMask !== false;
            const variant = normalizedOptions.variant || '';

            if (!spotlightRect) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                frame.classList.remove('is-circular-mask');
                frame.classList.remove('is-thin-variant');
                return;
            }

            frame.hidden = false;
            frame.classList.add('is-visible');
            frame.classList.toggle('is-circular-mask', !!spotlightRect.isCircular && allowMask);
            frame.classList.toggle('is-thin-variant', variant === 'thin');
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
            const secondaryActionRect = this.getSpotlightRect(this.secondaryActionHighlightedElement);
            const extraRects = this.extraSpotlightElements.map((element) => this.getSpotlightRect(element));
            const persistentMaskRect = persistentRect || null;
            const actionMaskRect = actionRect || null;
            const secondaryActionMaskRect = secondaryActionRect || null;
            const extraMaskRects = extraRects.filter((rect) => !!rect);

            if (this.backdrop) {
                this.syncBackdropViewport();
                if (persistentMaskRect || actionMaskRect || secondaryActionMaskRect || extraMaskRects.length > 0) {
                    this.backdrop.hidden = false;
                    this.backdrop.classList.add('is-visible');
                } else {
                    this.backdrop.hidden = true;
                    this.backdrop.classList.remove('is-visible');
                }
                this.updateBackdropCutout(this.backdropPersistentCutout, persistentMaskRect);
                this.updateBackdropCutout(this.backdropActionCutout, actionMaskRect);
                this.updateBackdropCutout(this.backdropSecondaryActionCutout, secondaryActionMaskRect);
            }

            this.updateSpotlightFrame(this.persistentSpotlightFrame, persistentRect, {
                allowMask: true
            });
            this.updateSpotlightFrame(this.actionSpotlightFrame, actionRect, {
                allowMask: true
            });
            this.updateSpotlightFrame(this.secondaryActionSpotlightFrame, secondaryActionRect, {
                allowMask: true
            });
            extraRects.forEach((rect, index) => {
                const entry = this.ensureExtraSpotlightEntry(index);
                if (!entry) {
                    return;
                }
                const maskRect = rect || null;
                const sourceElement = this.extraSpotlightElements[index] || null;
                const variant = sourceElement && typeof sourceElement.getAttribute === 'function'
                    ? sourceElement.getAttribute('data-yui-guide-spotlight-variant')
                    : '';
                this.updateBackdropCutout(entry.cutout, maskRect);
                this.updateSpotlightFrame(entry.frame, rect || null, {
                    allowMask: true,
                    variant: variant
                });
            });
            for (let index = extraRects.length; index < this.extraSpotlightEntries.length; index += 1) {
                const entry = this.extraSpotlightEntries[index];
                if (!entry) {
                    continue;
                }
                this.updateBackdropCutout(entry.cutout, null);
                this.updateSpotlightFrame(entry.frame, null);
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
            var cursorValue = active ? 'none' : '';
            this.document.documentElement.style.cursor = cursorValue;
            this.document.body.style.cursor = cursorValue;
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
            this.secondaryActionHighlightedElement = null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        activateSecondarySpotlight(element) {
            this.ensureRoot();
            this.secondaryActionHighlightedElement = element || null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        clearActionSpotlight() {
            this.ensureRoot();
            this.actionHighlightedElement = null;
            this.secondaryActionHighlightedElement = null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            if (!this.persistentHighlightedElement && this.extraSpotlightElements.length === 0) {
                this.stopSpotlightTracking();
            }
        }

        clearPersistentSpotlight() {
            this.ensureRoot();
            this.persistentHighlightedElement = null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            if (
                !this.actionHighlightedElement
                && !this.secondaryActionHighlightedElement
                && this.extraSpotlightElements.length === 0
            ) {
                this.stopSpotlightTracking();
            }
        }

        clearSpotlight() {
            this.ensureRoot();
            this.stopSpotlightTracking();
            this.persistentHighlightedElement = null;
            this.actionHighlightedElement = null;
            this.secondaryActionHighlightedElement = null;
            this.extraSpotlightElements = [];
            this.syncHighlightedElementClasses();

            if (this.backdrop) {
                this.backdrop.hidden = true;
                this.backdrop.classList.remove('is-visible');
            }
            this.updateBackdropCutout(this.backdropPersistentCutout, null);
            this.updateBackdropCutout(this.backdropActionCutout, null);
            this.updateBackdropCutout(this.backdropSecondaryActionCutout, null);
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.updateBackdropCutout(entry.cutout, null);
            });
            this.updateSpotlightFrame(this.persistentSpotlightFrame, null);
            this.updateSpotlightFrame(this.actionSpotlightFrame, null);
            this.updateSpotlightFrame(this.secondaryActionSpotlightFrame, null);
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.updateSpotlightFrame(entry.frame, null);
            });
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
            const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                ? normalizedOptions.pauseCheck
                : null;
            const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                ? normalizedOptions.cancelCheck
                : null;

            if (!this.cursorPosition) {
                this.showCursorAt(x, y);
                return Promise.resolve(true);
            }

            this.document.body.classList.add('yui-guide-ghost-cursor-active');
            this.cursorShell.hidden = false;
            this.cursorShell.classList.add('is-visible');

            return new Promise((resolve) => {
                let settled = false;
                let frameId = 0;
                let elapsedMs = 0;
                let lastNow = 0;
                const startX = this.cursorPosition.x;
                const startY = this.cursorPosition.y;
                const deltaX = x - startX;
                const deltaY = y - startY;
                const finish = (completed) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (frameId) {
                        window.cancelAnimationFrame(frameId);
                        frameId = 0;
                    }
                    if (completed) {
                        this.cursorPosition = { x: x, y: y };
                    }
                    resolve(completed !== false);
                };

                const tick = (now) => {
                    if (settled || !this.cursorShell || !this.cursorShell.isConnected) {
                        finish(false);
                        return;
                    }

                    if (cancelCheck && cancelCheck()) {
                        finish(false);
                        return;
                    }

                    if (pauseCheck && pauseCheck()) {
                        lastNow = now;
                        frameId = window.requestAnimationFrame(tick);
                        return;
                    }

                    if (!lastNow) {
                        lastNow = now;
                    }

                    elapsedMs += Math.max(0, now - lastNow);
                    lastNow = now;

                    const progress = durationMs <= 0
                        ? 1
                        : Math.max(0, Math.min(1, elapsedMs / durationMs));
                    const nextX = startX + (deltaX * progress);
                    const nextY = startY + (deltaY * progress);

                    this.cursorShell.style.transitionDuration = '0ms';
                    this.cursorShell.style.transform = 'translate(' + Math.round(nextX) + 'px, ' + Math.round(nextY) + 'px)';
                    this.cursorPosition = { x: nextX, y: nextY };

                    if (progress >= 1) {
                        finish(true);
                        return;
                    }

                    frameId = window.requestAnimationFrame(tick);
                };

                frameId = window.requestAnimationFrame(tick);
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

        runEllipseAnimation(centerX, centerY, radiusX, radiusY, cycleMs, abortCheck, pauseCheck, cancelCheck) {
            this.ensureRoot();
            if (!this.cursorShell) {
                return Promise.resolve(false);
            }

            var self = this;
            var startedAt = performance.now();
            var pausedTotalMs = 0;
            var pausedAt = 0;

            self.document.body.classList.add('yui-guide-ghost-cursor-active');
            self.cursorShell.hidden = false;
            self.cursorShell.classList.add('is-visible');

            return new Promise(function (resolve) {
                function tick(now) {
                    if (!self.cursorShell || !self.cursorShell.isConnected) {
                        resolve(false);
                        return;
                    }

                    if (typeof cancelCheck === 'function' && cancelCheck()) {
                        resolve(false);
                        return;
                    }

                    if (typeof abortCheck === 'function' && abortCheck()) {
                        if (pausedAt) {
                            pausedTotalMs += Math.max(0, now - pausedAt);
                            pausedAt = 0;
                        }
                        resolve(false);
                        return;
                    }

                    if (typeof pauseCheck === 'function' && pauseCheck()) {
                        if (!pausedAt) {
                            pausedAt = now;
                        }
                        window.requestAnimationFrame(tick);
                        return;
                    }

                    if (pausedAt) {
                        pausedTotalMs += Math.max(0, now - pausedAt);
                        pausedAt = 0;
                    }

                    var progress = Math.max(0, Math.min(1, (now - startedAt - pausedTotalMs) / cycleMs));
                    var angle = progress * Math.PI * 2;
                    var x = centerX + Math.cos(angle) * radiusX;
                    var y = centerY + Math.sin(angle) * radiusY;
                    self.cursorShell.style.transitionDuration = '80ms';
                    self.cursorShell.style.transform = 'translate(' + Math.round(x) + 'px, ' + Math.round(y) + 'px)';
                    self.cursorPosition = { x: x, y: y };

                    if (progress >= 1) {
                        resolve(true);
                        return;
                    }
                    window.requestAnimationFrame(tick);
                }

                window.requestAnimationFrame(tick);
            });
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
            this.document.documentElement.style.cursor = '';
            this.document.body.style.cursor = '';
            this.clearSpotlight();
            if (this.root && this.root.isConnected) {
                this.root.remove();
            }
            this.root = null;
            this.stage = null;
            this.backdrop = null;
            this.backdropMask = null;
            this.backdropBase = null;
            this.backdropPersistentCutout = null;
            this.backdropActionCutout = null;
            this.backdropSecondaryActionCutout = null;
            this.backdropFill = null;
            this.persistentSpotlightFrame = null;
            this.actionSpotlightFrame = null;
            this.secondaryActionSpotlightFrame = null;
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
            this.secondaryActionHighlightedElement = null;
            this.extraSpotlightElements = [];
            this.extraSpotlightEntries = [];
            this.highlightedElements = new Set();
        }
    }

    window.YuiGuideOverlay = YuiGuideOverlay;
})();
