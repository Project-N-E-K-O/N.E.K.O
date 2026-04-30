(function () {
    'use strict';

    const ROOT_ID = 'yui-guide-overlay';
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const BACKDROP_MASK_ID = ROOT_ID + '-mask';
    const EXTRA_SPOTLIGHT_ENTRY_COUNT = 6;
    const DEFAULT_SPOTLIGHT_PADDING = 6;
    const BACKDROP_CUTOUT_INSET = 4;
    const BACKDROP_DIM_ENABLED = false;
    const DEFAULT_CURSOR_CLICK_VISIBLE_MS = 420;
    const CURSOR_CLICK_STAR_COUNT = 7;
    const CURSOR_CLICK_STAR_LIFETIME_MS = 760;
    const CURSOR_TRAIL_PARTICLE_LIFETIME_MS = 300;
    const CURSOR_TRAIL_MIN_DISTANCE = 3;
    const CURSOR_TRAIL_MIN_INTERVAL_MS = 8;
    const CURSOR_TRAIL_SEGMENT_SPACING = 9;
    const CURSOR_TRAIL_MAX_SEGMENTS_PER_FRAME = 6;
    const CURSOR_TRAIL_MAX_POINTS = 34;
    const CURSOR_TRAIL_MAX_PARTICLES = 12;
    const CURSOR_TRAIL_ICON_CHANCE = 0.045;
    const CURSOR_TRAIL_BODY_HEAD_WIDTH = 34;
    const CURSOR_TRAIL_BODY_TAIL_WIDTH = 3;
    const CURSOR_TRAIL_CORE_HEAD_WIDTH = 14;
    const CURSOR_TRAIL_CORE_TAIL_WIDTH = 1.5;
    const CURSOR_TRAIL_HEAD_RADIUS = 15;
    const CURSOR_TRAIL_ICON_URLS = Object.freeze([
        '/static/icons/send_icon.png',
        '/static/icons/paw_ui.png'
    ]);

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

    function shouldReduceMotion() {
        try {
            return !!(
                window.matchMedia
                && window.matchMedia('(prefers-reduced-motion: reduce)').matches
            );
        } catch (_) {
            return false;
        }
    }

    function isCircularFloatingButtonElement(element) {
        if (!element) {
            return false;
        }

        const matchesCircularId = (candidate) => {
            return !!(
                candidate
                && typeof candidate.id === 'string'
                && /-btn-(mic|agent|settings)$/.test(candidate.id)
            );
        };

        if (matchesCircularId(element)) {
            return true;
        }

        if (typeof element.closest === 'function') {
            return !!element.closest(
                '#live2d-btn-mic, #vrm-btn-mic, #mmd-btn-mic, ' +
                '#live2d-btn-agent, #vrm-btn-agent, #mmd-btn-agent, ' +
                '#live2d-btn-settings, #vrm-btn-settings, #mmd-btn-settings, ' +
                '[id$="-btn-mic"], [id$="-btn-agent"], [id$="-btn-settings"]'
            );
        }

        return false;
    }

    function ensureSpotlightFrameDecorations(frame) {
        if (!frame) {
            return;
        }

        if (!frame.querySelector('.yui-guide-spotlight-chrome')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-chrome'));
        }
        if (!frame.querySelector('.yui-guide-spotlight-sweep')) {
            frame.appendChild(createElement('span', 'yui-guide-spotlight-sweep'));
        }
        if (!frame.querySelector('.yui-guide-spotlight-circle-skin')) {
            frame.appendChild(createElement('div', 'yui-guide-spotlight-circle-skin'));
        }
    }

    function ensureSpotlightImageDecorations(frame) {
        if (!frame) {
            return;
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
    }

    function removeSpotlightImageDecorations(frame) {
        if (!frame || typeof frame.querySelectorAll !== 'function') {
            return;
        }

        frame.querySelectorAll(
            '.yui-guide-spotlight-ear-left, .yui-guide-spotlight-ear-right, .yui-guide-spotlight-paw'
        ).forEach((element) => {
            if (element && element.parentNode) {
                element.parentNode.removeChild(element);
            }
        });
    }

    function applySpotlightFrameDecorationMode(frame, useCircleImage) {
        if (!frame) {
            return;
        }

        const chrome = frame.querySelector('.yui-guide-spotlight-chrome');
        const circleSkin = frame.querySelector('.yui-guide-spotlight-circle-skin');

        if (useCircleImage) {
            removeSpotlightImageDecorations(frame);
        } else {
            ensureSpotlightImageDecorations(frame);
        }

        if (chrome && chrome.style) {
            chrome.style.display = useCircleImage ? 'none' : '';
        }

        if (circleSkin && circleSkin.style) {
            circleSkin.style.display = useCircleImage ? 'block' : '';
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
            this.bubbleHeader = null;
            this.bubbleTitle = null;
            this.bubbleMeta = null;
            this.bubbleBody = null;
            this.preview = null;
            this.previewTitle = null;
            this.previewList = null;
            this.cursorShell = null;
            this.cursorInner = null;
            this.cursorPosition = null;
            this.cursorClickTimer = 0;
            this.activeClickStars = new Set();
            this.activeTrailParticles = new Set();
            this.cursorTrailLastPoint = null;
            this.cursorTrailLastAt = 0;
            this.cursorTrailSvg = null;
            this.cursorTrailBody = null;
            this.cursorTrailCore = null;
            this.cursorTrailHead = null;
            this.cursorTrailHeadCore = null;
            this.cursorTrailGradient = null;
            this.cursorTrailPoints = [];
            this.cursorTrailDecayFrame = 0;
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
                bubble.setAttribute('role', 'status');
                bubble.setAttribute('aria-live', 'polite');
                const bubbleHeader = createElement('div', 'yui-guide-bubble-header');
                const bubbleTitle = createElement('div', 'yui-guide-bubble-title');
                const bubbleMeta = createElement('div', 'yui-guide-bubble-meta');
                const bubbleBody = createElement('div', 'yui-guide-bubble-body');
                bubbleHeader.appendChild(bubbleTitle);
                bubbleHeader.appendChild(bubbleMeta);
                bubble.appendChild(bubbleHeader);
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
                const cursorTrailSvg = this.createCursorTrailLayer();

                stage.appendChild(backdrop);
                stage.appendChild(persistentSpotlightFrame);
                stage.appendChild(actionSpotlightFrame);
                stage.appendChild(secondaryActionSpotlightFrame);
                stage.appendChild(bubble);
                stage.appendChild(preview);
                stage.appendChild(cursorTrailSvg);
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
                this.bubbleHeader = bubbleHeader;
                this.bubbleTitle = bubbleTitle;
                this.bubbleMeta = bubbleMeta;
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
                this.bubbleHeader = root.querySelector('.yui-guide-bubble-header');
                this.bubbleTitle = root.querySelector('.yui-guide-bubble-title');
                this.bubbleMeta = root.querySelector('.yui-guide-bubble-meta');
                this.bubbleBody = root.querySelector('.yui-guide-bubble-body');
                this.ensureBubbleHeader();
                this.preview = root.querySelector('.yui-guide-preview');
                this.previewTitle = root.querySelector('.yui-guide-preview-title');
                this.previewList = root.querySelector('.yui-guide-preview-list');
                this.cursorShell = root.querySelector('.yui-guide-cursor-shell');
                this.cursorInner = root.querySelector('.yui-guide-cursor');
                this.cursorTrailSvg = root.querySelector('.yui-guide-cursor-trail-layer');
                this.cursorTrailBody = root.querySelector('.yui-guide-cursor-trail-ribbon');
                this.cursorTrailCore = root.querySelector('.yui-guide-cursor-trail-core');
                this.cursorTrailHead = root.querySelector('.yui-guide-cursor-trail-head');
                this.cursorTrailHeadCore = root.querySelector('.yui-guide-cursor-trail-head-core');
                this.cursorTrailGradient = root.querySelector('#' + ROOT_ID + '-cursor-trail-gradient');
                if (!this.cursorTrailSvg && this.stage && this.cursorShell) {
                    this.stage.insertBefore(this.createCursorTrailLayer(), this.cursorShell);
                }
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

        createCursorTrailLayer() {
            const trailSvg = createSvgElement('svg', 'yui-guide-cursor-trail-layer');
            trailSvg.setAttribute('aria-hidden', 'true');
            trailSvg.setAttribute('data-yui-cursor-hidden', 'true');
            trailSvg.setAttribute('preserveAspectRatio', 'none');

            const defs = createSvgElement('defs');
            const gradient = createSvgElement('linearGradient');
            gradient.id = ROOT_ID + '-cursor-trail-gradient';
            gradient.setAttribute('gradientUnits', 'userSpaceOnUse');

            [
                ['0%', '#3157e8', '0'],
                ['30%', '#396dff', '0.16'],
                ['68%', '#26bfff', '0.58'],
                ['100%', '#55efff', '0.92']
            ].forEach((entry) => {
                const stop = createSvgElement('stop');
                stop.setAttribute('offset', entry[0]);
                stop.setAttribute('stop-color', entry[1]);
                stop.setAttribute('stop-opacity', entry[2]);
                gradient.appendChild(stop);
            });

            const headGradient = createSvgElement('radialGradient');
            headGradient.id = ROOT_ID + '-cursor-trail-head-gradient';
            headGradient.setAttribute('cx', '50%');
            headGradient.setAttribute('cy', '50%');
            headGradient.setAttribute('r', '58%');
            [
                ['0%', '#7df7ff', '0.78'],
                ['48%', '#31c8ff', '0.36'],
                ['100%', '#2d5cff', '0']
            ].forEach((entry) => {
                const stop = createSvgElement('stop');
                stop.setAttribute('offset', entry[0]);
                stop.setAttribute('stop-color', entry[1]);
                stop.setAttribute('stop-opacity', entry[2]);
                headGradient.appendChild(stop);
            });

            defs.appendChild(gradient);
            defs.appendChild(headGradient);

            const body = createSvgElement('path', 'yui-guide-cursor-trail-ribbon');
            body.setAttribute('fill', 'url(#' + ROOT_ID + '-cursor-trail-gradient)');

            const core = createSvgElement('path', 'yui-guide-cursor-trail-core');
            core.setAttribute('fill', 'url(#' + ROOT_ID + '-cursor-trail-gradient)');

            const head = createSvgElement('circle', 'yui-guide-cursor-trail-head');
            head.setAttribute('fill', 'url(#' + ROOT_ID + '-cursor-trail-head-gradient)');

            const headCore = createSvgElement('circle', 'yui-guide-cursor-trail-head-core');
            headCore.setAttribute('fill', '#66f3ff');

            trailSvg.appendChild(defs);
            trailSvg.appendChild(body);
            trailSvg.appendChild(core);
            trailSvg.appendChild(head);
            trailSvg.appendChild(headCore);

            this.cursorTrailSvg = trailSvg;
            this.cursorTrailBody = body;
            this.cursorTrailCore = core;
            this.cursorTrailHead = head;
            this.cursorTrailHeadCore = headCore;
            this.cursorTrailGradient = gradient;

            return trailSvg;
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

        ensureBubbleHeader() {
            if (!this.bubble) {
                return;
            }

            if (!this.bubbleHeader) {
                this.bubbleHeader = createElement('div', 'yui-guide-bubble-header');
                this.bubble.insertBefore(this.bubbleHeader, this.bubble.firstChild || null);
            }

            if (!this.bubbleTitle) {
                this.bubbleTitle = createElement('div', 'yui-guide-bubble-title');
            }
            if (!this.bubbleTitle.parentNode || this.bubbleTitle.parentNode !== this.bubbleHeader) {
                this.bubbleHeader.insertBefore(this.bubbleTitle, this.bubbleHeader.firstChild || null);
            }

            if (!this.bubbleMeta) {
                this.bubbleMeta = createElement('div', 'yui-guide-bubble-meta');
            }
            if (!this.bubbleMeta.parentNode || this.bubbleMeta.parentNode !== this.bubbleHeader) {
                this.bubbleHeader.appendChild(this.bubbleMeta);
            }

            if (!this.bubbleBody) {
                this.bubbleBody = createElement('div', 'yui-guide-bubble-body');
                this.bubble.appendChild(this.bubbleBody);
            }
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

        hideBackdrop() {
            if (!this.backdrop) {
                return;
            }

            this.backdrop.hidden = true;
            this.backdrop.classList.remove('is-visible');
            this.updateBackdropCutout(this.backdropPersistentCutout, null);
            this.updateBackdropCutout(this.backdropActionCutout, null);
            this.updateBackdropCutout(this.backdropSecondaryActionCutout, null);
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.updateBackdropCutout(entry.cutout, null);
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
            const radiusOverride = readSpotlightNumberAttr(element, 'data-yui-guide-spotlight-radius');
            const geometryHint = typeof element.getAttribute === 'function'
                ? (element.getAttribute('data-yui-guide-spotlight-geometry') || '').trim().toLowerCase()
                : '';
            const inferredCircularButton = isCircularFloatingButtonElement(element);
            const rawRadius = radiusOverride != null
                ? Math.max(0, radiusOverride)
                : Math.max(0, this.getSpotlightRadius(element, padding) - padding);
            const left = Math.max(0, Math.floor(rect.left - padding));
            const top = Math.max(0, Math.floor(rect.top - padding));
            const right = Math.min(window.innerWidth, Math.ceil(rect.right + padding));
            const bottom = Math.min(window.innerHeight, Math.ceil(rect.bottom + padding));
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);
            const radius = this.getSpotlightRadius(element, padding);
            const isCircular = geometryHint === 'circle' || inferredCircularButton;

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
            const forceCircleImage = variant === 'circle-image';

            if (!spotlightRect) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                frame.classList.remove('is-circular-mask');
                frame.classList.remove('is-circle-image');
                frame.classList.remove('is-thin-variant');
                applySpotlightFrameDecorationMode(frame, false);
                return;
            }

            frame.hidden = false;
            frame.classList.add('is-visible');
            frame.classList.toggle('is-circular-mask', !!spotlightRect.isCircular && allowMask);
            frame.classList.toggle('is-circle-image', forceCircleImage);
            frame.classList.toggle('is-thin-variant', variant === 'thin');
            applySpotlightFrameDecorationMode(frame, !!spotlightRect.isCircular || forceCircleImage);
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
                this.hideBackdrop();
            }

            const getFrameVariantFromElement = (element) => {
                if (!element || typeof element.getAttribute !== 'function') {
                    return '';
                }
                const geometry = (element.getAttribute('data-yui-guide-spotlight-geometry') || '').trim().toLowerCase();
                if (geometry === 'circle' || isCircularFloatingButtonElement(element)) {
                    return 'circle-image';
                }
                return element.getAttribute('data-yui-guide-spotlight-variant') || '';
            };

            this.updateSpotlightFrame(this.persistentSpotlightFrame, persistentRect, {
                allowMask: true,
                variant: getFrameVariantFromElement(this.persistentHighlightedElement)
            });
            this.updateSpotlightFrame(this.actionSpotlightFrame, actionRect, {
                allowMask: true,
                variant: getFrameVariantFromElement(this.actionHighlightedElement)
            });
            this.updateSpotlightFrame(this.secondaryActionSpotlightFrame, secondaryActionRect, {
                allowMask: true,
                variant: getFrameVariantFromElement(this.secondaryActionHighlightedElement)
            });
            extraRects.forEach((rect, index) => {
                const entry = this.ensureExtraSpotlightEntry(index);
                if (!entry) {
                    return;
                }
                const maskRect = rect || null;
                const sourceElement = this.extraSpotlightElements[index] || null;
                const variant = getFrameVariantFromElement(sourceElement);
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

        clearBubblePlacement() {
            this.ensureRoot();

            if (!this.bubble) {
                return;
            }
            this.bubble.classList.remove(
                'is-placement-top',
                'is-placement-right',
                'is-placement-bottom',
                'is-placement-left',
                'is-placement-floating'
            );
        }

        scoreBubbleCandidate(candidate, width, height, viewportWidth, viewportHeight, viewportPadding) {
            const overflowLeft = Math.max(0, viewportPadding - candidate.left);
            const overflowTop = Math.max(0, viewportPadding - candidate.top);
            const overflowRight = Math.max(0, candidate.left + width - (viewportWidth - viewportPadding));
            const overflowBottom = Math.max(0, candidate.top + height - (viewportHeight - viewportPadding));
            const overflow = overflowLeft + overflowTop + overflowRight + overflowBottom;
            return (overflow * 1000) + candidate.priority;
        }

        positionBubble(anchorRect, options) {
            this.ensureRoot();
            this.clearBubblePlacement();

            const normalizedOptions = options || {};
            const viewportPadding = Number.isFinite(normalizedOptions.viewportPadding)
                ? Math.max(8, normalizedOptions.viewportPadding)
                : 16;
            const gap = Number.isFinite(normalizedOptions.gap) ? Math.max(8, normalizedOptions.gap) : 18;
            const viewportWidth = Math.max(1, window.innerWidth || 0);
            const viewportHeight = Math.max(1, window.innerHeight || 0);
            const width = Math.min(
                this.bubble.offsetWidth || 340,
                Math.max(220, viewportWidth - (viewportPadding * 2))
            );
            const height = Math.min(
                this.bubble.offsetHeight || 120,
                Math.max(96, viewportHeight - (viewportPadding * 2))
            );

            const clampLeft = (value) => Math.max(viewportPadding, Math.min(value, viewportWidth - width - viewportPadding));
            const clampTop = (value) => Math.max(viewportPadding, Math.min(value, viewportHeight - height - viewportPadding));
            let placement = 'floating';
            let left = clampLeft(viewportWidth - width - 24);
            let top = viewportPadding + 16;

            if (anchorRect && Number.isFinite(anchorRect.left) && Number.isFinite(anchorRect.top)) {
                const anchorCenterX = anchorRect.left + (anchorRect.width / 2);
                const anchorCenterY = anchorRect.top + (anchorRect.height / 2);
                const candidates = [
                    {
                        placement: 'right',
                        left: anchorRect.right + gap,
                        top: anchorCenterY - (height / 2),
                        priority: 0
                    },
                    {
                        placement: 'left',
                        left: anchorRect.left - width - gap,
                        top: anchorCenterY - (height / 2),
                        priority: 1
                    },
                    {
                        placement: 'top',
                        left: anchorCenterX - (width / 2),
                        top: anchorRect.top - height - gap,
                        priority: 2
                    },
                    {
                        placement: 'bottom',
                        left: anchorCenterX - (width / 2),
                        top: anchorRect.bottom + gap,
                        priority: 3
                    }
                ].sort((a, b) => {
                    return this.scoreBubbleCandidate(a, width, height, viewportWidth, viewportHeight, viewportPadding)
                        - this.scoreBubbleCandidate(b, width, height, viewportWidth, viewportHeight, viewportPadding);
                });

                const best = candidates[0];
                placement = best.placement;
                left = clampLeft(best.left);
                top = clampTop(best.top);
            }

            this.bubble.classList.add('is-placement-' + placement);
            this.bubble.style.left = Math.round(left) + 'px';
            this.bubble.style.top = Math.round(top) + 'px';
        }

        showBubble(text, options) {
            this.ensureRoot();
            this.ensureBubbleHeader();

            const normalizedOptions = options || {};
            const title = typeof normalizedOptions.title === 'string' ? normalizedOptions.title.trim() : '';
            const meta = typeof normalizedOptions.meta === 'string' ? normalizedOptions.meta.trim() : '';
            const emotion = typeof normalizedOptions.emotion === 'string' ? normalizedOptions.emotion.trim() : 'neutral';

            this.bubbleTitle.textContent = title || 'Yui';
            this.bubbleTitle.hidden = false;
            this.bubbleMeta.textContent = meta;
            this.bubbleMeta.hidden = !meta;
            this.bubbleBody.textContent = text || '';
            this.bubble.hidden = false;
            this.bubble.dataset.emotion = emotion || 'neutral';
            this.positionBubble(normalizedOptions.anchorRect || null, normalizedOptions);
            this.bubble.classList.add('is-visible');
        }

        hideBubble() {
            this.ensureRoot();
            this.bubble.hidden = true;
            this.bubble.classList.remove('is-visible');
            this.clearBubblePlacement();
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
                this.hideBackdrop();
            }
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
            const previous = this.cursorPosition;
            const shouldGlide = !!(
                previous
                && this.cursorShell
                && !this.cursorShell.hidden
                && this.cursorShell.classList.contains('is-visible')
            );
            this.document.body.classList.add('yui-guide-ghost-cursor-active');
            this.cursorShell.hidden = false;
            this.cursorShell.classList.add('is-visible');
            this.cursorShell.style.transitionDuration = shouldGlide ? '360ms' : '0ms';
            this.cursorShell.style.transform = 'translate(' + Math.round(x) + 'px, ' + Math.round(y) + 'px)';
            this.cursorPosition = { x: x, y: y };
            this.cursorTrailLastPoint = shouldGlide ? { x: previous.x, y: previous.y } : null;
            this.cursorTrailLastAt = 0;
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
                this.cursorTrailLastPoint = { x: startX, y: startY };
                this.cursorTrailLastAt = 0;
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
                    const previousX = this.cursorPosition ? this.cursorPosition.x : startX;
                    const previousY = this.cursorPosition ? this.cursorPosition.y : startY;

                    this.cursorShell.style.transitionDuration = '0ms';
                    this.cursorShell.style.transform = 'translate(' + Math.round(nextX) + 'px, ' + Math.round(nextY) + 'px)';
                    this.cursorPosition = { x: nextX, y: nextY };
                    this.maybeSpawnCursorTrail(nextX, nextY, previousX, previousY, now);

                    if (progress >= 1) {
                        finish(true);
                        return;
                    }

                    frameId = window.requestAnimationFrame(tick);
                };

                frameId = window.requestAnimationFrame(tick);
            });
        }

        removeCursorTrailEntry(entry) {
            if (!entry) {
                return;
            }
            if (entry.timer) {
                window.clearTimeout(entry.timer);
            }
            if (entry.element && entry.element.parentNode) {
                entry.element.parentNode.removeChild(entry.element);
            }
            this.activeTrailParticles.delete(entry);
        }

        trimCursorTrailParticles() {
            while (this.activeTrailParticles.size > CURSOR_TRAIL_MAX_PARTICLES) {
                const first = this.activeTrailParticles.values().next().value;
                if (!first) {
                    return;
                }
                this.removeCursorTrailEntry(first);
            }
        }

        clearCursorTrailParticles() {
            if (this.cursorTrailDecayFrame) {
                window.cancelAnimationFrame(this.cursorTrailDecayFrame);
                this.cursorTrailDecayFrame = 0;
            }

            if (this.activeTrailParticles && this.activeTrailParticles.size > 0) {
                Array.from(this.activeTrailParticles).forEach((entry) => {
                    this.removeCursorTrailEntry(entry);
                });
            }

            this.cursorTrailPoints = [];
            this.cursorTrailLastPoint = null;
            this.cursorTrailLastAt = 0;
            if (this.cursorTrailSvg) {
                this.cursorTrailSvg.classList.remove('is-visible');
            }
            if (this.cursorTrailBody) {
                this.cursorTrailBody.setAttribute('d', '');
            }
            if (this.cursorTrailCore) {
                this.cursorTrailCore.setAttribute('d', '');
            }
        }

        spawnCursorTrailParticle(x, y, angle) {
            if (!this.stage || shouldReduceMotion()) {
                return;
            }

            const particle = createElement('span', 'yui-guide-cursor-trail is-icon');
            const width = 7 + Math.random() * 5;
            const opacity = 0.1 + Math.random() * 0.12;
            const drift = 10 + Math.random() * 16;
            const sideJitter = (Math.random() - 0.5) * 20;
            const backOffset = 22 + Math.random() * 20;
            const cos = Math.cos(angle);
            const sin = Math.sin(angle);
            const baseX = x - (cos * backOffset) - (sin * sideJitter);
            const baseY = y - (sin * backOffset) + (cos * sideJitter);

            particle.setAttribute('aria-hidden', 'true');
            particle.style.left = baseX.toFixed(2) + 'px';
            particle.style.top = baseY.toFixed(2) + 'px';
            particle.style.setProperty('--trail-width', width.toFixed(2) + 'px');
            particle.style.setProperty('--trail-height', width.toFixed(2) + 'px');
            particle.style.setProperty('--trail-angle', (angle * 180 / Math.PI).toFixed(2) + 'deg');
            particle.style.setProperty('--trail-drift-x', (-cos * drift).toFixed(2) + 'px');
            particle.style.setProperty('--trail-drift-y', (-sin * drift).toFixed(2) + 'px');
            particle.style.setProperty('--trail-opacity', opacity.toFixed(2));
            particle.style.setProperty('--trail-brightness', (0.78 + Math.random() * 0.2).toFixed(2));

            const iconUrl = CURSOR_TRAIL_ICON_URLS[Math.floor(Math.random() * CURSOR_TRAIL_ICON_URLS.length)];
            particle.style.setProperty('--trail-icon', 'url("' + iconUrl + '")');

            const entry = {
                element: particle,
                timer: 0
            };
            entry.timer = window.setTimeout(() => {
                this.removeCursorTrailEntry(entry);
            }, CURSOR_TRAIL_PARTICLE_LIFETIME_MS + 120);

            this.activeTrailParticles.add(entry);
            this.stage.appendChild(particle);
            this.trimCursorTrailParticles();
        }

        getCursorTrailNow(now) {
            if (Number.isFinite(now)) {
                return now;
            }
            if (window.performance && typeof window.performance.now === 'function') {
                return window.performance.now();
            }
            return Date.now();
        }

        syncCursorTrailViewport() {
            if (!this.cursorTrailSvg) {
                return;
            }
            const width = Math.max(1, window.innerWidth || this.document.documentElement.clientWidth || 1);
            const height = Math.max(1, window.innerHeight || this.document.documentElement.clientHeight || 1);
            this.cursorTrailSvg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
        }

        trimCursorTrailPoints(now) {
            const cutoff = now - CURSOR_TRAIL_PARTICLE_LIFETIME_MS;
            this.cursorTrailPoints = (this.cursorTrailPoints || [])
                .filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y) && point.t >= cutoff);

            if (this.cursorTrailPoints.length > CURSOR_TRAIL_MAX_POINTS) {
                this.cursorTrailPoints = this.cursorTrailPoints.slice(this.cursorTrailPoints.length - CURSOR_TRAIL_MAX_POINTS);
            }
        }

        formatCursorTrailPoint(point) {
            return point.x.toFixed(1) + ' ' + point.y.toFixed(1);
        }

        appendSmoothCursorTrailPath(points, useMove) {
            if (!points || points.length === 0) {
                return '';
            }

            let path = (useMove ? 'M ' : 'L ') + this.formatCursorTrailPoint(points[0]);
            if (points.length === 1) {
                return path;
            }

            for (let index = 1; index < points.length - 1; index += 1) {
                const current = points[index];
                const next = points[index + 1];
                const mid = {
                    x: (current.x + next.x) / 2,
                    y: (current.y + next.y) / 2
                };
                path += ' Q ' + this.formatCursorTrailPoint(current) + ' ' + this.formatCursorTrailPoint(mid);
            }

            path += ' L ' + this.formatCursorTrailPoint(points[points.length - 1]);
            return path;
        }

        buildCursorTrailRibbonPath(points, headWidth, tailWidth) {
            if (!points || points.length < 2) {
                return '';
            }

            const left = [];
            const right = [];
            const count = points.length;

            for (let index = 0; index < count; index += 1) {
                const point = points[index];
                const previous = points[Math.max(0, index - 1)];
                const next = points[Math.min(count - 1, index + 1)];
                let dx = next.x - previous.x;
                let dy = next.y - previous.y;
                let length = Math.hypot(dx, dy);

                if (length < 0.001 && index > 0) {
                    dx = point.x - points[index - 1].x;
                    dy = point.y - points[index - 1].y;
                    length = Math.hypot(dx, dy);
                }
                if (length < 0.001) {
                    dx = 1;
                    dy = 0;
                    length = 1;
                }

                const progress = count <= 1 ? 1 : index / (count - 1);
                const eased = progress * progress * (3 - (2 * progress));
                const width = tailWidth + ((headWidth - tailWidth) * eased);
                const normalX = -dy / length;
                const normalY = dx / length;
                const halfWidth = width / 2;

                left.push({
                    x: point.x + (normalX * halfWidth),
                    y: point.y + (normalY * halfWidth)
                });
                right.push({
                    x: point.x - (normalX * halfWidth),
                    y: point.y - (normalY * halfWidth)
                });
            }

            return this.appendSmoothCursorTrailPath(left, true)
                + ' '
                + this.appendSmoothCursorTrailPath(right.slice().reverse(), false)
                + ' Z';
        }

        updateCursorTrail(now) {
            if (!this.stage || shouldReduceMotion()) {
                this.clearCursorTrailParticles();
                return;
            }

            if (!this.cursorTrailSvg || !this.cursorTrailBody || !this.cursorTrailCore) {
                const layer = this.createCursorTrailLayer();
                if (this.cursorShell) {
                    this.stage.insertBefore(layer, this.cursorShell);
                } else {
                    this.stage.appendChild(layer);
                }
            }

            const currentNow = this.getCursorTrailNow(now);
            this.syncCursorTrailViewport();
            this.trimCursorTrailPoints(currentNow);

            if (this.cursorTrailPoints.length < 2) {
                if (this.cursorTrailSvg) {
                    this.cursorTrailSvg.classList.remove('is-visible');
                }
                this.cursorTrailBody.setAttribute('d', '');
                this.cursorTrailCore.setAttribute('d', '');
                return;
            }

            const points = this.cursorTrailPoints;
            const tail = points[0];
            const head = points[points.length - 1];
            const bodyPath = this.buildCursorTrailRibbonPath(
                points,
                CURSOR_TRAIL_BODY_HEAD_WIDTH,
                CURSOR_TRAIL_BODY_TAIL_WIDTH
            );
            const corePath = this.buildCursorTrailRibbonPath(
                points,
                CURSOR_TRAIL_CORE_HEAD_WIDTH,
                CURSOR_TRAIL_CORE_TAIL_WIDTH
            );

            if (this.cursorTrailGradient) {
                this.cursorTrailGradient.setAttribute('x1', tail.x.toFixed(1));
                this.cursorTrailGradient.setAttribute('y1', tail.y.toFixed(1));
                this.cursorTrailGradient.setAttribute('x2', head.x.toFixed(1));
                this.cursorTrailGradient.setAttribute('y2', head.y.toFixed(1));
            }

            this.cursorTrailBody.setAttribute('d', bodyPath);
            this.cursorTrailCore.setAttribute('d', corePath);
            if (this.cursorTrailHead) {
                this.cursorTrailHead.setAttribute('cx', head.x.toFixed(1));
                this.cursorTrailHead.setAttribute('cy', head.y.toFixed(1));
                this.cursorTrailHead.setAttribute('r', String(CURSOR_TRAIL_HEAD_RADIUS));
            }
            if (this.cursorTrailHeadCore) {
                this.cursorTrailHeadCore.setAttribute('cx', head.x.toFixed(1));
                this.cursorTrailHeadCore.setAttribute('cy', head.y.toFixed(1));
                this.cursorTrailHeadCore.setAttribute('r', '3.8');
            }
            if (this.cursorTrailSvg) {
                this.cursorTrailSvg.classList.add('is-visible');
            }
        }

        scheduleCursorTrailDecay() {
            if (this.cursorTrailDecayFrame || shouldReduceMotion()) {
                return;
            }

            const tick = (now) => {
                this.cursorTrailDecayFrame = 0;
                this.updateCursorTrail(now);
                if (this.cursorTrailPoints && this.cursorTrailPoints.length > 0) {
                    this.cursorTrailDecayFrame = window.requestAnimationFrame(tick);
                }
            };

            this.cursorTrailDecayFrame = window.requestAnimationFrame(tick);
        }

        maybeSpawnCursorTrail(x, y, previousX, previousY, now) {
            if (shouldReduceMotion()) {
                return;
            }

            const dx = x - previousX;
            const dy = y - previousY;
            const stepDistance = Math.hypot(dx, dy);
            if (stepDistance < 0.6) {
                return;
            }

            const currentNow = this.getCursorTrailNow(now);
            const lastPoint = this.cursorTrailLastPoint;
            const elapsedMs = Number.isFinite(currentNow) && Number.isFinite(this.cursorTrailLastAt)
                ? currentNow - this.cursorTrailLastAt
                : CURSOR_TRAIL_MIN_INTERVAL_MS;
            const distanceFromLast = lastPoint
                ? Math.hypot(x - lastPoint.x, y - lastPoint.y)
                : CURSOR_TRAIL_MIN_DISTANCE;

            if (distanceFromLast < CURSOR_TRAIL_MIN_DISTANCE && elapsedMs < CURSOR_TRAIL_MIN_INTERVAL_MS) {
                return;
            }

            const startPoint = lastPoint
                ? {
                    x: lastPoint.x,
                    y: lastPoint.y,
                    t: Number.isFinite(lastPoint.t) ? lastPoint.t : Math.max(0, currentNow - 16)
                }
                : {
                    x: previousX,
                    y: previousY,
                    t: Math.max(0, currentNow - 16)
                };
            if (!lastPoint || this.cursorTrailPoints.length === 0) {
                this.cursorTrailPoints.push(startPoint);
            }

            const distance = Math.hypot(x - startPoint.x, y - startPoint.y);
            const segmentCount = Math.max(
                1,
                Math.min(CURSOR_TRAIL_MAX_SEGMENTS_PER_FRAME, Math.ceil(distance / CURSOR_TRAIL_SEGMENT_SPACING))
            );
            const startTime = Number.isFinite(startPoint.t) ? startPoint.t : currentNow - 16;

            for (let index = 1; index <= segmentCount; index += 1) {
                const ratio = index / segmentCount;
                this.cursorTrailPoints.push({
                    x: startPoint.x + ((x - startPoint.x) * ratio),
                    y: startPoint.y + ((y - startPoint.y) * ratio),
                    t: startTime + ((currentNow - startTime) * ratio)
                });
            }

            this.cursorTrailLastPoint = { x: x, y: y, t: currentNow };
            this.cursorTrailLastAt = currentNow;
            this.updateCursorTrail(currentNow);
            this.scheduleCursorTrailDecay();

            if (Math.random() < CURSOR_TRAIL_ICON_CHANCE && distance > 16) {
                this.spawnCursorTrailParticle(x, y, Math.atan2(dy, dx));
            }
        }

        clearCursorClickStars() {
            if (!this.activeClickStars || this.activeClickStars.size === 0) {
                return;
            }

            this.activeClickStars.forEach((entry) => {
                if (!entry) {
                    return;
                }
                if (entry.timer) {
                    window.clearTimeout(entry.timer);
                }
                if (entry.element && entry.element.parentNode) {
                    entry.element.parentNode.removeChild(entry.element);
                }
            });
            this.activeClickStars.clear();
        }

        spawnCursorClickStars() {
            if (!this.cursorShell || shouldReduceMotion()) {
                return;
            }

            const fragment = this.document.createDocumentFragment();
            for (let index = 0; index < CURSOR_CLICK_STAR_COUNT; index += 1) {
                const angle = ((Math.PI * 2) * (index / CURSOR_CLICK_STAR_COUNT)) + ((Math.random() - 0.5) * 0.92);
                const distance = 24 + Math.random() * 30;
                const size = 5 + Math.random() * 5;
                const x = Math.cos(angle) * distance;
                const y = Math.sin(angle) * distance;
                const star = createElement('span', 'yui-guide-click-star');
                star.setAttribute('aria-hidden', 'true');
                star.style.setProperty('--star-x', x.toFixed(2) + 'px');
                star.style.setProperty('--star-y', y.toFixed(2) + 'px');
                star.style.setProperty('--star-mid-x', (x * 0.76).toFixed(2) + 'px');
                star.style.setProperty('--star-mid-y', (y * 0.76).toFixed(2) + 'px');
                star.style.setProperty('--star-size', size.toFixed(2) + 'px');
                star.style.setProperty('--star-rotate', Math.round(Math.random() * 180) + 'deg');
                star.style.setProperty('--star-delay', Math.round(Math.random() * 60) + 'ms');
                star.style.setProperty('--star-hue', String(Math.round(36 + Math.random() * 28)));
                fragment.appendChild(star);

                const entry = {
                    element: star,
                    timer: 0
                };
                entry.timer = window.setTimeout(() => {
                    if (star.parentNode) {
                        star.parentNode.removeChild(star);
                    }
                    this.activeClickStars.delete(entry);
                }, CURSOR_CLICK_STAR_LIFETIME_MS + 120);
                this.activeClickStars.add(entry);
            }

            this.cursorShell.appendChild(fragment);
        }

        clickCursor(durationMs) {
            this.ensureRoot();
            if (!this.cursorInner) {
                return;
            }
            const visibleMs = Number.isFinite(durationMs)
                ? Math.max(DEFAULT_CURSOR_CLICK_VISIBLE_MS, Math.round(durationMs))
                : DEFAULT_CURSOR_CLICK_VISIBLE_MS;
            if (this.cursorClickTimer) {
                window.clearTimeout(this.cursorClickTimer);
                this.cursorClickTimer = 0;
            }
            if (this.cursorShell) {
                this.document.body.classList.add('yui-guide-ghost-cursor-active');
                this.cursorShell.hidden = false;
                this.cursorShell.classList.add('is-visible');
            }
            this.cursorInner.classList.remove('is-clicking');
            void this.cursorInner.offsetWidth;
            this.cursorInner.classList.add('is-clicking');
            this.spawnCursorClickStars();
            this.cursorClickTimer = window.setTimeout(() => {
                this.cursorClickTimer = 0;
                if (this.cursorInner) {
                    this.cursorInner.classList.remove('is-clicking');
                }
            }, visibleMs);
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
            var startX = centerX + radiusX;
            var startY = centerY;

            self.document.body.classList.add('yui-guide-ghost-cursor-active');
            self.cursorShell.hidden = false;
            self.cursorShell.classList.add('is-visible');
            var startDistance = self.cursorPosition
                ? Math.hypot(startX - self.cursorPosition.x, startY - self.cursorPosition.y)
                : 0;
            var prepareMove = self.cursorPosition && startDistance > 2
                ? self.moveCursorTo(startX, startY, {
                    durationMs: Math.min(520, Math.max(220, Math.round(cycleMs * 0.08))),
                    pauseCheck: pauseCheck,
                    cancelCheck: cancelCheck
                })
                : Promise.resolve(true);

            return prepareMove.then(function (prepared) {
                if (!prepared) {
                    return false;
                }

                var startedAt = performance.now();
                var pausedTotalMs = 0;
                var pausedAt = 0;
                self.cursorTrailLastPoint = self.cursorPosition
                    ? { x: self.cursorPosition.x, y: self.cursorPosition.y }
                    : null;
                self.cursorTrailLastAt = 0;

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
                    var previousX = self.cursorPosition ? self.cursorPosition.x : x;
                    var previousY = self.cursorPosition ? self.cursorPosition.y : y;
                    self.cursorShell.style.transitionDuration = '80ms';
                    self.cursorShell.style.transform = 'translate(' + Math.round(x) + 'px, ' + Math.round(y) + 'px)';
                    self.cursorPosition = { x: x, y: y };
                    self.maybeSpawnCursorTrail(x, y, previousX, previousY, now);

                    if (progress >= 1) {
                        resolve(true);
                        return;
                    }
                    window.requestAnimationFrame(tick);
                }

                window.requestAnimationFrame(tick);
            });
            });
        }

        hideCursor() {
            this.ensureRoot();
            this.document.body.classList.remove('yui-guide-ghost-cursor-active');
            if (this.cursorClickTimer) {
                window.clearTimeout(this.cursorClickTimer);
                this.cursorClickTimer = 0;
            }
            if (this.cursorInner) {
                this.cursorInner.classList.remove('is-clicking');
            }
            this.clearCursorClickStars();
            this.clearCursorTrailParticles();
            this.cursorShell.hidden = true;
            this.cursorShell.classList.remove('is-visible');
        }

        destroy() {
            this.document.body.classList.remove('yui-taking-over');
            this.document.body.classList.remove('yui-guide-ghost-cursor-active');
            this.document.documentElement.style.cursor = '';
            this.document.body.style.cursor = '';
            if (this.cursorClickTimer) {
                window.clearTimeout(this.cursorClickTimer);
                this.cursorClickTimer = 0;
            }
            this.clearCursorClickStars();
            this.clearCursorTrailParticles();
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
            this.bubbleHeader = null;
            this.bubbleTitle = null;
            this.bubbleMeta = null;
            this.bubbleBody = null;
            this.preview = null;
            this.previewTitle = null;
            this.previewList = null;
            this.cursorShell = null;
            this.cursorInner = null;
            this.cursorPosition = null;
            this.cursorTrailSvg = null;
            this.cursorTrailBody = null;
            this.cursorTrailCore = null;
            this.cursorTrailHead = null;
            this.cursorTrailHeadCore = null;
            this.cursorTrailGradient = null;
            this.cursorTrailPoints = [];
            this.cursorTrailLastPoint = null;
            this.cursorTrailLastAt = 0;
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
