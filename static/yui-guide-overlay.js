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
    const SMOOTH_CURSOR_SHOW_DURATION_MS = 560;
    const PC_OVERLAY_CURSOR_EASE = Object.freeze([0.22, 1, 0.36, 1]);

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

    function sampleCubicBezier(progress, x1, y1, x2, y2) {
        const targetX = Math.max(0, Math.min(1, Number(progress) || 0));
        const ax = 3 * x1 - 3 * x2 + 1;
        const bx = -6 * x1 + 3 * x2;
        const cx = 3 * x1;
        const ay = 3 * y1 - 3 * y2 + 1;
        const by = -6 * y1 + 3 * y2;
        const cy = 3 * y1;
        let low = 0;
        let high = 1;
        let t = targetX;
        for (let index = 0; index < 12; index += 1) {
            t = (low + high) / 2;
            const x = ((ax * t + bx) * t + cx) * t;
            if (x < targetX) {
                low = t;
            } else {
                high = t;
            }
        }
        return Math.max(0, Math.min(1, ((ay * t + by) * t + cy) * t));
    }

    function easePcOverlayCursorProgress(progress) {
        return sampleCubicBezier(
            progress,
            PC_OVERLAY_CURSOR_EASE[0],
            PC_OVERLAY_CURSOR_EASE[1],
            PC_OVERLAY_CURSOR_EASE[2],
            PC_OVERLAY_CURSOR_EASE[3]
        );
    }

    function createPcOverlayBridge(doc) {
        const host = window.nekoTutorialOverlay;
        if (!host || typeof host.update !== 'function' || typeof host.getWindowMetricsSync !== 'function') {
            return null;
        }

        let runId = '';
        try {
            runId = window.localStorage.getItem('yuiGuidePcOverlayRunId') || '';
            if (!runId) {
                runId = 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
                window.localStorage.setItem('yuiGuidePcOverlayRunId', runId);
            }
        } catch (_) {
            runId = 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
        }
        let sequence = 0;
        let active = false;
        let remoteReady = false;
        let failed = false;
        let lastKey = '';
        let currentSpotlights = [];
        let currentCursor = null;
        let currentPetal = null;

        const getAssetUrl = (assetPath) => {
            try {
                return new URL(assetPath, window.location.href).toString();
            } catch (_) {
                return assetPath;
            }
        };

        const getMetrics = () => {
            try {
                const metrics = host.getWindowMetricsSync();
                if (metrics && metrics.contentBounds) {
                    return metrics;
                }
            } catch (_) {}
            return {
                contentBounds: {
                    x: Number.isFinite(window.screenX) ? window.screenX : 0,
                    y: Number.isFinite(window.screenY) ? window.screenY : 0,
                    width: window.innerWidth || 1,
                    height: window.innerHeight || 1
                },
                zoomFactor: 1
            };
        };

        const toScreenPoint = (x, y) => {
            const metrics = getMetrics();
            const bounds = metrics.bounds || metrics.contentBounds || { x: 0, y: 0 };
            const viewport = window.visualViewport || null;
            const offsetLeft = viewport && Number.isFinite(Number(viewport.offsetLeft)) ? Number(viewport.offsetLeft) : 0;
            const offsetTop = viewport && Number.isFinite(Number(viewport.offsetTop)) ? Number(viewport.offsetTop) : 0;
            return {
                x: Number(bounds.x || 0) + Number(x || 0) + offsetLeft,
                y: Number(bounds.y || 0) + Number(y || 0) + offsetTop
            };
        };

        const toScreenRect = (rect, kind, index, variant) => {
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }
            const topLeft = toScreenPoint(rect.left, rect.top);
            return {
                id: kind + '-' + index,
                kind: kind,
                shape: rect.isCircular ? 'circle' : 'rounded-rect',
                variant: variant || '',
                x: topLeft.x,
                y: topLeft.y,
                width: rect.width,
                height: rect.height,
                radius: rect.radius
            };
        };

        function withoutTransientCursorEffect(cursor) {
            if (!cursor) {
                return null;
            }
            const nextCursor = Object.assign({}, cursor);
            delete nextCursor.effect;
            delete nextCursor.effectDurationMs;
            return nextCursor;
        }

        const send = (patch, force) => {
            const hasCursor = patch && Object.prototype.hasOwnProperty.call(patch, 'cursor');
            const hasPetal = patch && Object.prototype.hasOwnProperty.call(patch, 'petal');
            if (patch && Object.prototype.hasOwnProperty.call(patch, 'spotlights')) {
                currentSpotlights = Array.isArray(patch.spotlights) ? patch.spotlights : [];
            }
            if (patch && Object.prototype.hasOwnProperty.call(patch, 'cursor')) {
                currentCursor = withoutTransientCursorEffect(patch.cursor);
            }
            if (patch && Object.prototype.hasOwnProperty.call(patch, 'petal')) {
                currentPetal = patch.petal || null;
            }
            const payload = {
                spotlights: currentSpotlights
            };
            if (hasCursor) {
                payload.cursor = patch.cursor || null;
            } else if (currentCursor) {
                payload.cursor = currentCursor;
            }
            if (currentPetal || hasPetal) {
                payload.petal = currentPetal;
            }
            const key = JSON.stringify(payload || {});
            if (!force && key === lastKey && remoteReady) {
                return;
            }
            lastKey = key;
            if (!active) {
                active = true;
                try {
                    Promise.resolve(host.begin({ tutorialRunId: runId })).then((result) => {
                        if (result && result.ok === false) {
                            failed = true;
                            remoteReady = false;
                        }
                    }).catch(() => {
                        active = false;
                        failed = true;
                        remoteReady = false;
                    });
                } catch (_) {
                    active = false;
                    failed = true;
                    remoteReady = false;
                }
            }
            sequence = Math.max(sequence + 1, Date.now() * 1000);
            try {
                Promise.resolve(host.update({
                    tutorialRunId: runId,
                    sceneId: doc && doc.body ? (doc.body.getAttribute('data-yui-guide-scene') || '') : '',
                    sequence: sequence,
                    payload: payload
                })).then((result) => {
                    if (result && result.ok === false) {
                        failed = true;
                        remoteReady = false;
                        return;
                    }
                    failed = false;
                    remoteReady = true;
                }).catch(() => {
                    active = false;
                    failed = true;
                    remoteReady = false;
                });
            } catch (_) {
                active = false;
                failed = true;
                remoteReady = false;
            }
        };

        return {
            isAvailable() {
                return true;
            },
            canRenderPetalTransition() {
                try {
                    if (host && typeof host.getCapabilities === 'function') {
                        const capabilities = host.getCapabilities() || {};
                        return capabilities.petalTransition === true;
                    }
                    return !!(host && host.capabilities && host.capabilities.petalTransition === true);
                } catch (_) {
                    return false;
                }
            },
            shouldSuppressDom() {
                return active && !failed;
            },
            setSpotlights(rects) {
                const spotlights = (Array.isArray(rects) ? rects : [])
                    .map((entry, index) => toScreenRect(entry.rect, entry.kind, index, entry.variant || ''))
                    .filter(Boolean);
                send({ spotlights: spotlights }, false);
            },
            showCursorAt(x, y) {
                const point = toScreenPoint(x, y);
                send({
                    cursor: { visible: true, x: point.x, y: point.y, durationMs: 0 }
                }, true);
            },
            moveCursorTo(x, y, durationMs, effect, effectDurationMs) {
                const point = toScreenPoint(x, y);
                send({
                    cursor: {
                        visible: true,
                        x: point.x,
                        y: point.y,
                        durationMs: Math.max(0, Math.round(Number(durationMs) || 0)),
                        effect: effect || '',
                        effectDurationMs: Math.max(0, Math.round(Number(effectDurationMs) || 0))
                    }
                }, true);
            },
            hideCursor() {
                send({ cursor: { visible: false } }, true);
            },
            playPetalTransition(origin, options) {
                const point = origin ? toScreenPoint(origin.x, origin.y) : toScreenPoint((window.innerWidth || 1) / 2, (window.innerHeight || 1) / 2);
                const normalized = options || {};
                const petalId = 'petal-' + Date.now() + '-' + sequence;
                const durationMs = Math.max(240, Math.round(Number(normalized.durationMs) || 2600));
                send({
                    petal: {
                        id: petalId,
                        url: getAssetUrl('/static/assets/tutorial/petals/yui-guide-petal-transition.webp'),
                        durationMs: durationMs,
                        originX: point.x,
                        originY: point.y,
                        finalOpacity: Number.isFinite(Number(normalized.finalOpacity)) ? Number(normalized.finalOpacity) : 0.92
                    }
                }, true);
                window.setTimeout(() => {
                    if (currentPetal && currentPetal.id === petalId) {
                        send({ petal: null }, true);
                    }
                }, durationMs + 900);
            },
            clear() {
                lastKey = '';
                remoteReady = false;
                failed = false;
                currentSpotlights = [];
                currentCursor = null;
                currentPetal = null;
                try {
                    if (window.localStorage.getItem('yuiGuidePcOverlayRunId') === runId) {
                        window.localStorage.removeItem('yuiGuidePcOverlayRunId');
                    }
                } catch (_) {}
                try {
                    Promise.resolve(host.clear({ tutorialRunId: runId })).catch(() => {});
                } catch (_) {}
            }
        };
    }

    function isCircularFloatingButtonElement(element) {
        if (!element) {
            return false;
        }

        const matchesCircularId = (candidate) => {
            return !!(
                candidate
                && typeof candidate.id === 'string'
                && /-(?:btn-(?:mic|screen|agent|settings|goodbye|return)|lock-icon)$/.test(candidate.id)
            );
        };

        if (matchesCircularId(element)) {
            return true;
        }

        if (typeof element.closest === 'function') {
            return !!element.closest(
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

    function applySpotlightPlainCircleMode(frame) {
        if (!frame) {
            return;
        }

        removeSpotlightImageDecorations(frame);
        const chrome = frame.querySelector('.yui-guide-spotlight-chrome');
        const circleSkin = frame.querySelector('.yui-guide-spotlight-circle-skin');

        if (chrome && chrome.style) {
            chrome.style.display = '';
        }

        if (circleSkin && circleSkin.style) {
            circleSkin.style.display = 'none';
        }
    }

    class YuiGuideOverlay {
        constructor(doc) {
            this.document = doc || document;
            this.root = null;
            this.stage = null;
            this.interactionShield = null;
            this.interactionShieldSuppressed = false;
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
            this.pcOverlayBridge = createPcOverlayBridge(this.document);
            this.cursorPosition = null;
            this.cursorVisible = false;
            this.suppressedCursorMotion = null;
            this.persistentHighlightedElement = null;
            this.actionHighlightedElement = null;
            this.secondaryActionHighlightedElement = null;
            this.extraSpotlightElements = [];
            this.extraSpotlightEntries = [];
            this.highlightedElements = new Set();
            this.spotlightsSuppressed = false;
            this.spotlightRefreshTimer = null;
            this.boundRefreshSpotlight = this.refreshSpotlight.bind(this);
            this.spotlightRefreshRaf = null;
            this.boundScheduleSpotlightRefresh = this.scheduleSpotlightRefresh.bind(this);
        }

        isPcOverlayActive() {
            return !!(
                this.pcOverlayBridge
                && typeof this.pcOverlayBridge.isAvailable === 'function'
                && this.pcOverlayBridge.isAvailable()
            );
        }

        shouldSuppressDomForPcOverlay() {
            return !!(
                this.pcOverlayBridge
                && typeof this.pcOverlayBridge.shouldSuppressDom === 'function'
                && this.pcOverlayBridge.shouldSuppressDom()
            );
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

                const interactionShield = createElement('div', 'yui-guide-interaction-shield');
                interactionShield.hidden = true;
                interactionShield.setAttribute('aria-hidden', 'true');
                interactionShield.setAttribute('data-yui-cursor-hidden', 'true');

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
                backdropFill.setAttribute('fill', BACKDROP_DIM_ENABLED ? 'rgba(3, 7, 18, 0.76)' : 'transparent');
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

                stage.appendChild(backdrop);
                stage.appendChild(interactionShield);
                stage.appendChild(persistentSpotlightFrame);
                stage.appendChild(actionSpotlightFrame);
                stage.appendChild(secondaryActionSpotlightFrame);
                stage.appendChild(bubble);
                stage.appendChild(preview);
                root.appendChild(stage);
                this.document.body.appendChild(root);

                this.stage = stage;
                this.interactionShield = interactionShield;
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
                this.extraSpotlightEntries = extraSpotlightEntries;
            } else {
                this.stage = root.querySelector('.yui-guide-stage');
                this.interactionShield = root.querySelector('.yui-guide-interaction-shield');
                this.backdrop = root.querySelector('.yui-guide-backdrop');
                this.backdropMask = root.querySelector('mask#' + BACKDROP_MASK_ID);
                this.backdropBase = root.querySelector('.yui-guide-backdrop-base');
                this.backdropPersistentCutout = root.querySelector('.yui-guide-backdrop-cutout-persistent');
                this.backdropActionCutout = root.querySelector('.yui-guide-backdrop-cutout-action');
                this.backdropSecondaryActionCutout = root.querySelector('.yui-guide-backdrop-cutout-action-secondary');
                this.backdropFill = root.querySelector('.yui-guide-backdrop-fill');
                if (this.backdropFill) {
                    this.backdropFill.setAttribute('fill', BACKDROP_DIM_ENABLED ? 'rgba(3, 7, 18, 0.76)' : 'transparent');
                }
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
            if (this.spotlightsSuppressed) {
                this.clearSpotlight();
                return;
            }
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
            const forcePlainCircle = variant === 'plain-circle';

            if (this.shouldSuppressDomForPcOverlay()) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                return;
            }

            if (!spotlightRect) {
                frame.hidden = true;
                frame.classList.remove('is-visible');
                frame.classList.remove('is-circular-mask');
                frame.classList.remove('is-circle-image');
                frame.classList.remove('is-plain-circle');
                frame.classList.remove('is-thin-variant');
                removeSpotlightImageDecorations(frame);
                return;
            }

            frame.hidden = false;
            frame.classList.add('is-visible');
            frame.classList.toggle('is-circular-mask', !!spotlightRect.isCircular && allowMask && !forcePlainCircle);
            frame.classList.toggle('is-circle-image', forceCircleImage);
            frame.classList.toggle('is-plain-circle', forcePlainCircle);
            frame.classList.toggle('is-thin-variant', variant === 'thin');
            if (forcePlainCircle) {
                applySpotlightPlainCircleMode(frame);
            } else if (forceCircleImage) {
                applySpotlightFrameDecorationMode(frame, true);
            } else {
                applySpotlightFrameDecorationMode(frame, !!spotlightRect.isCircular);
            }
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
                const hasBackdropCutout = !!(BACKDROP_DIM_ENABLED && (
                    persistentMaskRect || actionMaskRect || secondaryActionMaskRect || extraMaskRects.length > 0
                ));
                this.backdrop.hidden = !hasBackdropCutout;
                this.backdrop.classList.toggle('is-visible', hasBackdropCutout);
            }

            const getFrameVariantFromElement = (element) => {
                if (!element || typeof element.getAttribute !== 'function') {
                    return '';
                }
                const variant = (element.getAttribute('data-yui-guide-spotlight-variant') || '').trim().toLowerCase();
                if (variant) {
                    return variant;
                }
                const geometry = (element.getAttribute('data-yui-guide-spotlight-geometry') || '').trim().toLowerCase();
                if (geometry === 'circle' || isCircularFloatingButtonElement(element)) {
                    return 'circle-image';
                }
                return '';
            };

            if (this.isPcOverlayActive() && this.pcOverlayBridge && typeof this.pcOverlayBridge.setSpotlights === 'function') {
                const pcRects = [];
                if (persistentRect) {
                    pcRects.push({
                        kind: 'persistent',
                        rect: persistentRect,
                        variant: getFrameVariantFromElement(this.persistentHighlightedElement)
                    });
                }
                if (actionRect) {
                    pcRects.push({
                        kind: 'primary',
                        rect: actionRect,
                        variant: getFrameVariantFromElement(this.actionHighlightedElement)
                    });
                }
                if (secondaryActionRect) {
                    pcRects.push({
                        kind: 'secondary',
                        rect: secondaryActionRect,
                        variant: getFrameVariantFromElement(this.secondaryActionHighlightedElement)
                    });
                }
                extraRects.forEach((rect, index) => {
                    if (rect) {
                        pcRects.push({
                            kind: 'extra',
                            rect: rect,
                            variant: getFrameVariantFromElement(this.extraSpotlightElements[index] || null)
                        });
                    }
                });
                this.pcOverlayBridge.setSpotlights(pcRects);
            }

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
            this.updateBackdropCutout(this.backdropPersistentCutout, persistentMaskRect);
            this.updateBackdropCutout(this.backdropActionCutout, actionMaskRect);
            this.updateBackdropCutout(this.backdropSecondaryActionCutout, secondaryActionMaskRect);
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

        scheduleSpotlightRefresh() {
            if (this.spotlightRefreshRaf) {
                return;
            }

            this.spotlightRefreshRaf = window.requestAnimationFrame(() => {
                this.spotlightRefreshRaf = null;
                this.refreshSpotlight();
            });
        }

        startSpotlightTracking() {
            if (this.spotlightRefreshTimer) {
                return;
            }

            window.addEventListener('resize', this.boundScheduleSpotlightRefresh, true);
            window.addEventListener('scroll', this.boundScheduleSpotlightRefresh, true);
            this.spotlightRefreshTimer = window.setInterval(this.boundScheduleSpotlightRefresh, 240);
        }

        stopSpotlightTracking() {
            if (this.spotlightRefreshTimer) {
                window.clearInterval(this.spotlightRefreshTimer);
                this.spotlightRefreshTimer = null;
            }

            if (this.spotlightRefreshRaf) {
                window.cancelAnimationFrame(this.spotlightRefreshRaf);
                this.spotlightRefreshRaf = null;
            }

            window.removeEventListener('resize', this.boundScheduleSpotlightRefresh, true);
            window.removeEventListener('scroll', this.boundScheduleSpotlightRefresh, true);
        }

        setTakingOver(active) {
            this.ensureRoot();
            this.document.body.classList.toggle('yui-taking-over', !!active);
            this.root.classList.toggle('is-taking-over', !!active);
            this.setInteractionShieldEnabled(!!active && !this.interactionShieldSuppressed);
            var cursorValue = active ? 'none' : '';
            this.document.documentElement.style.cursor = cursorValue;
            this.document.body.style.cursor = cursorValue;
        }

        setInteractionShieldSuppressed(active) {
            this.ensureRoot();
            this.interactionShieldSuppressed = active === true;
            this.setInteractionShieldEnabled(
                !!(this.document.body && this.document.body.classList.contains('yui-taking-over'))
                && !this.interactionShieldSuppressed
            );
        }

        setInteractionShieldEnabled(active) {
            this.ensureRoot();
            if (!this.interactionShield) {
                return;
            }
            this.interactionShield.hidden = !(active === true && !this.interactionShieldSuppressed);
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
            const availableWidth = Math.max(1, viewportWidth - (viewportPadding * 2));
            const availableHeight = Math.max(1, viewportHeight - (viewportPadding * 2));
            const minWidth = Math.min(220, availableWidth);
            const minHeight = Math.min(96, availableHeight);
            const width = Math.max(minWidth, Math.min(this.bubble.offsetWidth || 340, availableWidth));
            const height = Math.max(minHeight, Math.min(this.bubble.offsetHeight || 120, availableHeight));

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
            const bubbleVariant = typeof normalizedOptions.bubbleVariant === 'string'
                ? normalizedOptions.bubbleVariant.trim()
                : '';

            this.bubbleTitle.textContent = title || 'Yui';
            this.bubbleTitle.hidden = false;
            this.bubbleMeta.textContent = meta;
            this.bubbleMeta.hidden = !meta;
            this.bubbleBody.textContent = text || '';
            this.bubble.hidden = false;
            this.bubble.dataset.emotion = emotion || 'neutral';
            if (bubbleVariant) {
                this.bubble.dataset.bubbleVariant = bubbleVariant;
            } else {
                delete this.bubble.dataset.bubbleVariant;
            }
            this.positionBubble(normalizedOptions.anchorRect || null, normalizedOptions);
            this.bubble.classList.add('is-visible');
        }

        hideBubble() {
            this.ensureRoot();
            this.bubble.hidden = true;
            this.bubble.classList.remove('is-visible');
            this.clearBubblePlacement();
            delete this.bubble.dataset.emotion;
            delete this.bubble.dataset.bubbleVariant;
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

        setSpotlightSuppressed(active) {
            this.spotlightsSuppressed = active === true;
            if (this.spotlightsSuppressed) {
                this.clearSpotlight();
            }
        }

        setPersistentSpotlight(element) {
            this.ensureRoot();
            if (this.spotlightsSuppressed) {
                this.clearSpotlight();
                return;
            }
            this.persistentHighlightedElement = element || null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        activateSpotlight(element) {
            this.ensureRoot();
            if (this.spotlightsSuppressed) {
                this.clearSpotlight();
                return;
            }
            this.actionHighlightedElement = element || null;
            this.secondaryActionHighlightedElement = null;
            this.syncHighlightedElementClasses();
            this.refreshSpotlight();
            this.startSpotlightTracking();
        }

        activateSecondarySpotlight(element) {
            this.ensureRoot();
            if (this.spotlightsSuppressed) {
                this.clearSpotlight();
                return;
            }
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
            if (this.isPcOverlayActive() && this.pcOverlayBridge && typeof this.pcOverlayBridge.setSpotlights === 'function') {
                this.pcOverlayBridge.setSpotlights([]);
            }

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
            this.updateSuppressedCursorMotion();
            return !!this.cursorPosition;
        }

        isCursorVisible() {
            return !!this.cursorVisible;
        }

        getCursorPosition() {
            this.updateSuppressedCursorMotion();
            if (!this.cursorPosition) {
                return null;
            }

            return {
                x: this.cursorPosition.x,
                y: this.cursorPosition.y
            };
        }

        clearCursorPosition() {
            this.finishSuppressedCursorMotion(false);
            this.cursorPosition = null;
            this.cursorVisible = false;
        }

        finishSuppressedCursorMotion(completed) {
            const motion = this.suppressedCursorMotion;
            if (!motion) {
                return;
            }
            this.suppressedCursorMotion = null;
            if (motion.timerId) {
                window.clearTimeout(motion.timerId);
                motion.timerId = 0;
            }
            if (completed) {
                this.cursorPosition = { x: motion.endX, y: motion.endY };
                this.cursorVisible = true;
            }
            if (typeof motion.resolve === 'function') {
                motion.resolve(completed !== false);
            }
        }

        updateSuppressedCursorMotion(now) {
            const motion = this.suppressedCursorMotion;
            if (!motion) {
                return null;
            }
            const currentNow = Number.isFinite(Number(now)) ? Number(now) : performance.now();
            if (motion.pauseCheck && motion.pauseCheck()) {
                if (!motion.pausedAt) {
                    motion.pausedAt = currentNow;
                }
                return this.cursorPosition;
            }
            if (motion.pausedAt) {
                motion.pausedTotalMs += Math.max(0, currentNow - motion.pausedAt);
                motion.pausedAt = 0;
            }
            const progress = motion.durationMs <= 0
                ? 1
                : Math.max(0, Math.min(1, (currentNow - motion.startedAt - motion.pausedTotalMs) / motion.durationMs));
            const easedProgress = easePcOverlayCursorProgress(progress);
            this.cursorPosition = {
                x: motion.startX + ((motion.endX - motion.startX) * easedProgress),
                y: motion.startY + ((motion.endY - motion.startY) * easedProgress)
            };
            this.cursorVisible = true;
            if (progress >= 1) {
                this.finishSuppressedCursorMotion(true);
            }
            return this.cursorPosition;
        }

        scheduleSuppressedCursorMotionTick() {
            const motion = this.suppressedCursorMotion;
            if (!motion) {
                return;
            }
            if (motion.timerId) {
                window.clearTimeout(motion.timerId);
                motion.timerId = 0;
            }
            motion.timerId = window.setTimeout(() => {
                const activeMotion = this.suppressedCursorMotion;
                if (!activeMotion || activeMotion !== motion) {
                    return;
                }
                if (activeMotion.cancelCheck && activeMotion.cancelCheck()) {
                    this.finishSuppressedCursorMotion(false);
                    return;
                }
                this.updateSuppressedCursorMotion(performance.now());
                if (this.suppressedCursorMotion === activeMotion) {
                    this.scheduleSuppressedCursorMotionTick();
                }
            }, 48);
        }

        animateSuppressedCursorPositionTo(x, y, durationMs, options) {
            const normalizedOptions = options || {};
            const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                ? normalizedOptions.pauseCheck
                : null;
            const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                ? normalizedOptions.cancelCheck
                : null;
            const startPoint = this.cursorPosition;
            if (!startPoint) {
                this.cursorPosition = { x: x, y: y };
                this.cursorVisible = true;
                return Promise.resolve(true);
            }

            const normalizedDurationMs = Math.max(0, Math.round(Number(durationMs) || 0));
            if (normalizedDurationMs <= 0) {
                this.finishSuppressedCursorMotion(false);
                this.cursorPosition = { x: x, y: y };
                this.cursorVisible = true;
                return Promise.resolve(true);
            }

            this.finishSuppressedCursorMotion(false);
            return new Promise((resolve) => {
                this.suppressedCursorMotion = {
                    startX: startPoint.x,
                    startY: startPoint.y,
                    endX: x,
                    endY: y,
                    durationMs: normalizedDurationMs,
                    startedAt: performance.now(),
                    pausedAt: 0,
                    pausedTotalMs: 0,
                    pauseCheck: pauseCheck,
                    cancelCheck: cancelCheck,
                    timerId: 0,
                    resolve: resolve
                };
                this.scheduleSuppressedCursorMotionTick();
            });
        }

        keepDomCursorSuppressedForPcOverlay() {
            this.cursorVisible = true;
        }

        getSmoothCursorShowDurationMs(x, y) {
            if (!this.cursorPosition || !this.isCursorVisible()) {
                return 0;
            }
            const distance = Math.hypot(x - this.cursorPosition.x, y - this.cursorPosition.y);
            if (distance < 2) {
                return 0;
            }
            return SMOOTH_CURSOR_SHOW_DURATION_MS;
        }

        showCursorAt(x, y) {
            this.ensureRoot();
            this.updateSuppressedCursorMotion();
            const previous = this.cursorPosition;
            const glideDurationMs = this.getSmoothCursorShowDurationMs(x, y);
            if (this.isPcOverlayActive()) {
                if (glideDurationMs > 0 && this.pcOverlayBridge && typeof this.pcOverlayBridge.moveCursorTo === 'function') {
                    this.pcOverlayBridge.moveCursorTo(x, y, glideDurationMs, '');
                } else if (this.pcOverlayBridge && typeof this.pcOverlayBridge.showCursorAt === 'function') {
                    this.pcOverlayBridge.showCursorAt(x, y);
                }
                this.keepDomCursorSuppressedForPcOverlay();
                if (previous && glideDurationMs > 0) {
                    return this.animateSuppressedCursorPositionTo(x, y, glideDurationMs);
                }
                this.cursorPosition = { x: x, y: y };
                this.cursorVisible = true;
                return Promise.resolve(true);
            }
            this.cursorPosition = { x: x, y: y };
            this.cursorVisible = false;
            return Promise.resolve(true);
        }

        moveCursorTo(x, y, options) {
            this.updateSuppressedCursorMotion();
            if (this.isPcOverlayActive()) {
                const normalizedOptions = options || {};
                const durationMs = Number.isFinite(normalizedOptions.durationMs) ? normalizedOptions.durationMs : 480;
                const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                    ? normalizedOptions.pauseCheck
                    : null;
                const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                    ? normalizedOptions.cancelCheck
                    : null;
                if (!this.cursorPosition) {
                    if (this.pcOverlayBridge && typeof this.pcOverlayBridge.showCursorAt === 'function') {
                        this.pcOverlayBridge.showCursorAt(x, y);
                    } else if (this.pcOverlayBridge && typeof this.pcOverlayBridge.moveCursorTo === 'function') {
                        this.pcOverlayBridge.moveCursorTo(x, y, durationMs, normalizedOptions.effect || '');
                    }
                    this.keepDomCursorSuppressedForPcOverlay();
                    this.cursorPosition = { x: x, y: y };
                    this.cursorVisible = true;
                    return Promise.resolve(true);
                }
                if (this.cursorPosition) {
                    if (this.pcOverlayBridge && typeof this.pcOverlayBridge.moveCursorTo === 'function') {
                        this.pcOverlayBridge.moveCursorTo(x, y, durationMs, normalizedOptions.effect || '');
                    }
                    this.keepDomCursorSuppressedForPcOverlay();
                    return this.animateSuppressedCursorPositionTo(x, y, durationMs, {
                        pauseCheck: pauseCheck,
                        cancelCheck: cancelCheck
                    });
                }
            }
            const normalizedOptions = options || {};
            const durationMs = Number.isFinite(normalizedOptions.durationMs) ? normalizedOptions.durationMs : 480;
            const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                ? normalizedOptions.pauseCheck
                : null;
            const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                ? normalizedOptions.cancelCheck
                : null;

            if (!this.cursorPosition) {
                this.cursorPosition = { x: x, y: y };
                this.cursorVisible = false;
                return Promise.resolve(true);
            }

            this.cursorVisible = false;
            return this.animateSuppressedCursorPositionTo(x, y, durationMs, {
                pauseCheck: pauseCheck,
                cancelCheck: cancelCheck
            });
        }

        clickCursor(durationMs) {
            if (this.isPcOverlayActive()) {
                if (this.cursorPosition && this.pcOverlayBridge && typeof this.pcOverlayBridge.moveCursorTo === 'function') {
                    this.pcOverlayBridge.moveCursorTo(
                        this.cursorPosition.x,
                        this.cursorPosition.y,
                        0,
                        'click',
                        durationMs || DEFAULT_CURSOR_CLICK_VISIBLE_MS
                    );
                }
                this.keepDomCursorSuppressedForPcOverlay();
                return;
            }
            this.cursorVisible = false;
        }

        wobbleCursor(effectDurationMs) {
            if (this.isPcOverlayActive()) {
                if (this.cursorPosition && this.pcOverlayBridge && typeof this.pcOverlayBridge.moveCursorTo === 'function') {
                    this.pcOverlayBridge.moveCursorTo(this.cursorPosition.x, this.cursorPosition.y, 0, 'wobble', effectDurationMs);
                }
                this.keepDomCursorSuppressedForPcOverlay();
                return;
            }
            this.cursorVisible = false;
        }

        runEllipseAnimation(centerX, centerY, radiusX, radiusY, cycleMs, abortCheck, pauseCheck, cancelCheck) {
            if (this.isPcOverlayActive()) {
                return this.runSuppressedPcOverlayEllipseAnimation(
                    centerX,
                    centerY,
                    radiusX,
                    radiusY,
                    cycleMs,
                    abortCheck,
                    pauseCheck,
                    cancelCheck
                );
            }
            var self = this;
            var startX = centerX + radiusX;
            var startY = centerY;
            if (typeof cancelCheck === 'function' && cancelCheck()) {
                return Promise.resolve(false);
            }
            if (typeof abortCheck === 'function' && abortCheck()) {
                return Promise.resolve(false);
            }
            self.cursorPosition = { x: startX, y: startY };
            self.cursorVisible = false;
            return Promise.resolve(true);
        }

        runSuppressedPcOverlayEllipseAnimation(centerX, centerY, radiusX, radiusY, cycleMs, abortCheck, pauseCheck, cancelCheck) {
            this.finishSuppressedCursorMotion(false);
            this.keepDomCursorSuppressedForPcOverlay();

            var self = this;
            var startX = centerX + radiusX;
            var startY = centerY;
            var normalizedCycleMs = Math.max(1, Number(cycleMs) || 1);
            if (typeof cancelCheck === 'function' && cancelCheck()) {
                return Promise.resolve(false);
            }
            if (typeof abortCheck === 'function' && abortCheck()) {
                return Promise.resolve(false);
            }

            var startDistance = self.cursorPosition
                ? Math.hypot(startX - self.cursorPosition.x, startY - self.cursorPosition.y)
                : 0;
            if (shouldReduceMotion()) {
                return self.moveCursorTo(startX, startY, { durationMs: 0 });
            }

            var prepareMove = self.cursorPosition && startDistance > 2
                ? self.moveCursorTo(startX, startY, {
                    durationMs: Math.min(520, Math.max(220, Math.round(normalizedCycleMs * 0.08))),
                    pauseCheck: pauseCheck,
                    cancelCheck: cancelCheck
                })
                : self.moveCursorTo(startX, startY, { durationMs: 0 });

            return prepareMove.then(function (prepared) {
                if (!prepared) {
                    return false;
                }
                self.keepDomCursorSuppressedForPcOverlay();

                return new Promise(function (resolve) {
                    var startedAt = performance.now();
                    var pausedTotalMs = 0;
                    var pausedAt = 0;
                    var lastSentAt = 0;

                    function tick(now) {
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

                        var progress = Math.max(0, Math.min(1, (now - startedAt - pausedTotalMs) / normalizedCycleMs));
                        var angle = progress * Math.PI * 2;
                        var x = centerX + Math.cos(angle) * radiusX;
                        var y = centerY + Math.sin(angle) * radiusY;
                        self.cursorPosition = { x: x, y: y };
                        self.cursorVisible = true;
                        self.keepDomCursorSuppressedForPcOverlay();

                        if (!lastSentAt || now - lastSentAt >= 32 || progress >= 1) {
                            lastSentAt = now;
                            if (self.pcOverlayBridge && typeof self.pcOverlayBridge.moveCursorTo === 'function') {
                                self.pcOverlayBridge.moveCursorTo(x, y, 40, '');
                            }
                        }

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
            if (this.isPcOverlayActive()) {
                if (this.pcOverlayBridge && typeof this.pcOverlayBridge.hideCursor === 'function') {
                    this.pcOverlayBridge.hideCursor();
                }
                this.cursorVisible = false;
                return;
            }
            this.cursorVisible = false;
        }

        playPetalTransition(origin, options) {
            if (!this.isPcOverlayActive() || !this.pcOverlayBridge || typeof this.pcOverlayBridge.playPetalTransition !== 'function') {
                return null;
            }
            this.pcOverlayBridge.playPetalTransition(origin, options || {});
            return (
                this.shouldSuppressDomForPcOverlay()
                && typeof this.pcOverlayBridge.canRenderPetalTransition === 'function'
                && this.pcOverlayBridge.canRenderPetalTransition()
            ) ? true : null;
        }

        destroy() {
            if (this.pcOverlayBridge && typeof this.pcOverlayBridge.clear === 'function') {
                this.pcOverlayBridge.clear();
            }
            this.document.body.classList.remove('yui-taking-over');
            this.document.documentElement.style.cursor = '';
            this.document.body.style.cursor = '';
            this.clearSpotlight();
            if (this.root && this.root.isConnected) {
                this.root.remove();
            }
            this.root = null;
            this.stage = null;
            this.interactionShield = null;
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
            this.cursorPosition = null;
            this.cursorVisible = false;
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
