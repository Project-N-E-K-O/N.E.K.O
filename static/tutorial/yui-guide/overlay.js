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
    const PC_OVERLAY_SEQUENCE_STORAGE_KEY = 'yuiGuidePcOverlaySequence';
    const OverlayRendererApi = window.TutorialOverlayRendererApi || {};
    const OverlayCursorStateStore = OverlayRendererApi.OverlayCursorStateStore
        || (window.TutorialOverlayRenderer && window.TutorialOverlayRenderer.OverlayCursorStateStore);
    const OverlaySpotlightStateStore = OverlayRendererApi.OverlaySpotlightStateStore
        || (window.TutorialOverlayRenderer && window.TutorialOverlayRenderer.OverlaySpotlightStateStore);
    const OverlaySpotlightDomRenderer = OverlayRendererApi.OverlaySpotlightDomRenderer
        || (window.TutorialOverlayRenderer && window.TutorialOverlayRenderer.OverlaySpotlightDomRenderer);

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

    function getOverlayAssetUrl(assetPath) {
        try {
            return new URL(assetPath, window.location.href).toString();
        } catch (_) {
            return assetPath;
        }
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

    function createPcOverlayBridge(doc) {
        const host = window.nekoTutorialOverlay;
        if (!host || typeof host.update !== 'function' || typeof host.getWindowMetricsSync !== 'function') {
            return null;
        }

        const createRunId = () => 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
        const storeRunId = (nextRunId) => {
            try {
                window.localStorage.setItem('yuiGuidePcOverlayRunId', nextRunId);
            } catch (_) {}
        };
        const readStoredRunId = () => {
            try {
                return window.localStorage.getItem('yuiGuidePcOverlayRunId') || '';
            } catch (_) {
                return '';
            }
        };
        let runId = '';
        runId = readStoredRunId();
        if (!runId) {
            runId = createRunId();
            storeRunId(runId);
        }
        let sequence = 0;
        let active = false;
        let cleared = false;
        let remoteReady = false;
        let failed = false;
        let lastKey = '';
        const createCompleteStateStore = OverlayRendererApi.createPcOverlayCompleteStateStore
            || (window.TutorialOverlayRenderer && window.TutorialOverlayRenderer.createPcOverlayCompleteStateStore);
        if (typeof createCompleteStateStore !== 'function') {
            return null;
        }
        const completeStateStore = createCompleteStateStore({
            storage: window.localStorage,
            defaultCursorClickVisibleMs: DEFAULT_CURSOR_CLICK_VISIBLE_MS
        });

        const rotateRunId = () => {
            runId = createRunId();
            storeRunId(runId);
            sequence = 0;
            active = false;
            remoteReady = false;
            failed = false;
            lastKey = '';
            return runId;
        };
        const adoptRunId = (nextRunId) => {
            if (!nextRunId || nextRunId === runId) {
                return false;
            }
            runId = nextRunId;
            sequence = 0;
            active = false;
            remoteReady = false;
            failed = false;
            lastKey = '';
            return true;
        };
        const syncRunIdFromStorage = () => adoptRunId(readStoredRunId());

        const nextSequence = () => {
            const wallSequence = Date.now() * 1000;
            let storedSequence = 0;
            try {
                storedSequence = Math.max(
                    0,
                    Math.floor(Number(window.localStorage.getItem(PC_OVERLAY_SEQUENCE_STORAGE_KEY)) || 0)
                );
            } catch (_) {
                storedSequence = 0;
            }

            sequence = Math.max(sequence + 1, storedSequence + 1, wallSequence);
            try {
                window.localStorage.setItem(PC_OVERLAY_SEQUENCE_STORAGE_KEY, String(sequence));
            } catch (_) {}
            return sequence;
        };

        const handleStaleResult = (result, patch, force, retried, attemptedRunId) => {
            if (!result || result.stale !== true || retried || cleared || attemptedRunId !== runId) {
                return;
            }
            if (syncRunIdFromStorage()) {
                send(patch, force, true);
                return;
            }
            rotateRunId();
            send(patch, force, true);
        };
        const handleCursorOnlyStaleResult = (result, cursor, retried, attemptedRunId) => {
            if (!result || result.stale !== true || retried || cleared || attemptedRunId !== runId) {
                return;
            }
            if (syncRunIdFromStorage()) {
                sendCursorOnly(cursor, true);
                return;
            }
            rotateRunId();
            sendCursorOnly(cursor, true);
        };

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

        const send = (patch, force, retried) => {
            if (cleared) {
                return;
            }
            syncRunIdFromStorage();
            const payload = completeStateStore.applyPatch(patch || {});
            const key = JSON.stringify(payload || {});
            if (!force && key === lastKey && remoteReady) {
                return;
            }
            lastKey = key;
            if (!active) {
                active = true;
                const beginRunId = runId;
                try {
                    Promise.resolve(host.begin({ tutorialRunId: runId })).then((result) => {
                        if (result && result.stale === true) {
                            handleStaleResult(result, patch, force, retried === true, beginRunId);
                            return;
                        }
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
            sequence = nextSequence();
            const updateRunId = runId;
            try {
                Promise.resolve(host.update({
                    tutorialRunId: runId,
                    sceneId: doc && doc.body ? (doc.body.getAttribute('data-yui-guide-scene') || '') : '',
                    sequence: sequence,
                    payload: payload
                })).then((result) => {
                    if (result && result.stale === true) {
                        handleStaleResult(result, patch, force, retried === true, updateRunId);
                        return;
                    }
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
        const sendCursorOnly = (cursor, retried) => {
            if (cleared || !cursor) {
                return;
            }
            syncRunIdFromStorage();
            const payload = completeStateStore.applyPatch({ cursor: cursor });
            if (!active) {
                active = true;
                const beginRunId = runId;
                try {
                    Promise.resolve(host.begin({ tutorialRunId: runId })).then((result) => {
                        if (result && result.stale === true) {
                            handleCursorOnlyStaleResult(result, cursor, retried === true, beginRunId);
                            return;
                        }
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
            sequence = nextSequence();
            const updateRunId = runId;
            try {
                Promise.resolve(host.update({
                    tutorialRunId: runId,
                    sceneId: doc && doc.body ? (doc.body.getAttribute('data-yui-guide-scene') || '') : '',
                    sequence: sequence,
                    payload: payload
                })).then((result) => {
                    if (result && result.stale === true) {
                        handleCursorOnlyStaleResult(result, cursor, retried === true, updateRunId);
                        return;
                    }
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
            moveCursorOnlyTo(x, y, durationMs, effect, effectDurationMs) {
                const point = toScreenPoint(x, y);
                sendCursorOnly({
                    visible: true,
                    x: point.x,
                    y: point.y,
                    durationMs: Math.max(0, Math.round(Number(durationMs) || 0)),
                    effect: effect || '',
                    effectDurationMs: Math.max(0, Math.round(Number(effectDurationMs) || 0))
                });
            },
            hideCursor() {
                send({ cursor: { visible: false } }, true);
            },
            clearCursorCache() {
                completeStateStore.clearCursorCache();
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
                    const currentPetal = completeStateStore.getPetal();
                    if (currentPetal && currentPetal.id === petalId) {
                        send({ petal: null }, true);
                    }
                }, durationMs + 900);
            },
            showAvatarStandIn(standIn) {
                if (!standIn || !standIn.url) {
                    send({ avatarStandIn: null }, true);
                    return;
                }
                send({
                    avatarStandIn: {
                        visible: true,
                        url: getAssetUrl(standIn.url),
                        resource: String(standIn.resource || ''),
                        position: String(standIn.position || ''),
                        durationMs: Math.max(0, Math.round(Number(standIn.durationMs) || 0))
                    }
                }, true);
            },
            clearAvatarStandIn() {
                send({ avatarStandIn: null }, true);
            },
            clear() {
                active = false;
                cleared = true;
                lastKey = '';
                remoteReady = false;
                failed = false;
                completeStateStore.reset();
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

    const OverlayRendererClass = window.TutorialOverlayRenderer;

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
            this.pcCursorOutputSuppressed = false;
            this.pcOverlayBridge = createPcOverlayBridge(this.document);
            this.overlayRenderer = new OverlayRendererClass(this.pcOverlayBridge);
            this.spotlightDomRenderer = new OverlaySpotlightDomRenderer({
                document: this.document,
                backdropCutoutInset: BACKDROP_CUTOUT_INSET,
                defaultSpotlightPadding: DEFAULT_SPOTLIGHT_PADDING,
                getWindow: () => window,
                isCircularElement: isCircularFloatingButtonElement,
                shouldSuppressDom: () => this.shouldSuppressDomForPcOverlay()
            });
            this.installPcOverlayBridgeAccessor();
            this.cursorState = new OverlayCursorStateStore({
                now: () => performance.now(),
                setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
                clearTimeout: (timerId) => window.clearTimeout(timerId)
            });
            this.installCursorStateAccessors();
            this.spotlightState = new OverlaySpotlightStateStore();
            this.installSpotlightStateAccessors();
            this.extraSpotlightElements = [];
            this.extraSpotlightEntries = [];
            this.spotlightRefreshTimer = null;
            this.boundRefreshSpotlight = this.refreshSpotlight.bind(this);
            this.spotlightRefreshRaf = null;
            this.boundScheduleSpotlightRefresh = this.scheduleSpotlightRefresh.bind(this);
        }

        installPcOverlayBridgeAccessor() {
            if (this.pcOverlayBridgeAccessorInstalled) {
                return;
            }
            const currentBridge = this.pcOverlayBridge || null;
            Object.defineProperty(this, 'pcOverlayBridge', {
                configurable: true,
                enumerable: false,
                get: () => this._pcOverlayBridge || null,
                set: (value) => {
                    this._pcOverlayBridge = value || null;
                    if (this.overlayRenderer) {
                        this.overlayRenderer.pcOverlayBridge = this._pcOverlayBridge;
                    }
                }
            });
            this.pcOverlayBridgeAccessorInstalled = true;
            this.pcOverlayBridge = currentBridge;
        }

        installCursorStateAccessors() {
            if (!this.cursorState || this.cursorStateAccessorsInstalled) {
                return;
            }
            Object.defineProperty(this, 'cursorPosition', {
                configurable: true,
                enumerable: false,
                get: () => this.cursorState.getRawPosition(),
                set: (value) => {
                    this.cursorState.setRawPosition(value);
                }
            });
            Object.defineProperty(this, 'cursorVisible', {
                configurable: true,
                enumerable: false,
                get: () => this.cursorState.getRawVisible(),
                set: (value) => {
                    this.cursorState.setRawVisible(value);
                }
            });
            Object.defineProperty(this, 'suppressedCursorMotion', {
                configurable: true,
                enumerable: false,
                get: () => this.cursorState.getRawMotion(),
                set: (value) => {
                    this.cursorState.setRawMotion(value);
                }
            });
            this.cursorStateAccessorsInstalled = true;
        }

        installSpotlightStateAccessors() {
            if (!this.spotlightState || this.spotlightStateAccessorsInstalled) {
                return;
            }
            Object.defineProperty(this, 'persistentHighlightedElement', {
                configurable: true,
                enumerable: false,
                get: () => this.spotlightState.getRawPersistent(),
                set: (value) => {
                    this.spotlightState.setRawPersistent(value);
                }
            });
            Object.defineProperty(this, 'actionHighlightedElement', {
                configurable: true,
                enumerable: false,
                get: () => this.spotlightState.getRawAction(),
                set: (value) => {
                    this.spotlightState.setRawAction(value);
                }
            });
            Object.defineProperty(this, 'secondaryActionHighlightedElement', {
                configurable: true,
                enumerable: false,
                get: () => this.spotlightState.getRawSecondaryAction(),
                set: (value) => {
                    this.spotlightState.setRawSecondaryAction(value);
                }
            });
            Object.defineProperty(this, 'extraSpotlightElements', {
                configurable: true,
                enumerable: false,
                get: () => this.spotlightState.getRawExtra(),
                set: (value) => {
                    this.spotlightState.setRawExtra(value);
                }
            });
            Object.defineProperty(this, 'highlightedElements', {
                configurable: true,
                enumerable: false,
                get: () => this.spotlightState.getRawHighlightedElements(),
                set: (value) => {
                    this.spotlightState.setRawHighlightedElements(value);
                }
            });
            Object.defineProperty(this, 'spotlightsSuppressed', {
                configurable: true,
                enumerable: false,
                get: () => this.spotlightState.isSuppressed(),
                set: (value) => {
                    this.spotlightState.setSuppressed(value);
                }
            });
            this.spotlightStateAccessorsInstalled = true;
        }

        isPcOverlayActive() {
            return this.overlayRenderer.isAvailable();
        }

        shouldSuppressDomForPcOverlay() {
            return this.overlayRenderer.shouldSuppressDom();
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
                this.spotlightDomRenderer.ensureSpotlightFrameDecorations(persistentSpotlightFrame);

                const actionSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-action');
                actionSpotlightFrame.hidden = true;
                actionSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');
                this.spotlightDomRenderer.ensureSpotlightFrameDecorations(actionSpotlightFrame);

                const secondaryActionSpotlightFrame = createElement('div', 'yui-guide-spotlight-frame yui-guide-spotlight-frame-action yui-guide-spotlight-frame-action-secondary');
                secondaryActionSpotlightFrame.hidden = true;
                secondaryActionSpotlightFrame.setAttribute('data-yui-cursor-hidden', 'true');
                this.spotlightDomRenderer.ensureSpotlightFrameDecorations(secondaryActionSpotlightFrame);

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
                    this.spotlightDomRenderer.ensureSpotlightFrameDecorations(frame);
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
                this.spotlightDomRenderer.ensureSpotlightFrameDecorations(this.persistentSpotlightFrame);
                this.spotlightDomRenderer.ensureSpotlightFrameDecorations(this.actionSpotlightFrame);
                this.spotlightDomRenderer.ensureSpotlightFrameDecorations(this.secondaryActionSpotlightFrame);
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
                    this.spotlightDomRenderer.ensureSpotlightFrameDecorations(frames[index] || null);
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
            if (this.clearIfSpotlightSuppressed()) {
                return;
            }
            this.spotlightState.setExtra(elements);
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        clearExtraSpotlights() {
            this.ensureRoot();
            this.spotlightState.clearExtra();
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.spotlightDomRenderer.updateBackdropCutout(entry.cutout, null);
                this.spotlightDomRenderer.updateSpotlightFrame(entry.frame, null);
            });
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        clearIfSpotlightSuppressed() {
            if (!this.spotlightsSuppressed) {
                return false;
            }
            return true;
        }

        syncSpotlightTracking() {
            if (this.spotlightState && this.spotlightState.hasAny()) {
                this.startSpotlightTracking();
                return;
            }
            this.stopSpotlightTracking();
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
            this.spotlightDomRenderer.updateBackdropCutout(this.backdropPersistentCutout, null);
            this.spotlightDomRenderer.updateBackdropCutout(this.backdropActionCutout, null);
            this.spotlightDomRenderer.updateBackdropCutout(this.backdropSecondaryActionCutout, null);
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.spotlightDomRenderer.updateBackdropCutout(entry.cutout, null);
            });
        }

        syncHighlightedElementClasses() {
            this.spotlightState.syncHighlightedElementClasses();
        }

        refreshSpotlight() {
            const spotlightTargets = this.spotlightDomRenderer.resolveSpotlightTargets({
                persistent: this.persistentHighlightedElement,
                action: this.actionHighlightedElement,
                secondaryAction: this.secondaryActionHighlightedElement,
                extra: this.extraSpotlightElements
            });

            if (this.isPcOverlayActive()) {
                if (this.pcCursorOutputSuppressed === true) {
                    this.overlayRenderer.clearCursorCache();
                }
                this.overlayRenderer.setSpotlights(
                    this.spotlightDomRenderer.buildPcSpotlights(spotlightTargets)
                );
                return;
            }

            this.ensureRoot();

            if (this.backdrop) {
                this.syncBackdropViewport();
                const hasBackdropCutout = !!(
                    BACKDROP_DIM_ENABLED
                    && this.spotlightDomRenderer.hasAnySpotlightRect(spotlightTargets)
                );
                this.backdrop.hidden = !hasBackdropCutout;
                this.backdrop.classList.toggle('is-visible', hasBackdropCutout);
            }

            this.spotlightDomRenderer.renderDomSpotlights({
                targets: spotlightTargets,
                frames: {
                    persistent: this.persistentSpotlightFrame,
                    action: this.actionSpotlightFrame,
                    secondaryAction: this.secondaryActionSpotlightFrame
                },
                cutouts: {
                    persistent: this.backdropPersistentCutout,
                    action: this.backdropActionCutout,
                    secondaryAction: this.backdropSecondaryActionCutout
                },
                extraEntries: this.extraSpotlightEntries,
                ensureExtraSpotlightEntry: (index) => this.ensureExtraSpotlightEntry(index)
            });
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
            this.document.documentElement.style.cursor = '';
            this.document.body.style.cursor = '';
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

        showAvatarStandIn(standIn) {
            const normalized = standIn || {};
            const resource = String(normalized.resource || '');
            const url = normalized.url
                || (window.YuiGuideAvatarStandIn && typeof window.YuiGuideAvatarStandIn.getResourcePath === 'function'
                    ? window.YuiGuideAvatarStandIn.getResourcePath(resource)
                    : '');
            const payload = {
                url,
                resource,
                position: String(normalized.position || ''),
                durationMs: Math.max(0, Math.round(Number(normalized.durationMs) || 0))
            };
            this.overlayRenderer.showAvatarStandIn(payload);
        }

        clearAvatarStandIn() {
            this.overlayRenderer.clearAvatarStandIn();
        }

        setSpotlightSuppressed(active) {
            this.spotlightState.setSuppressed(active);
            if (!this.spotlightsSuppressed) {
                this.refreshSpotlight();
                this.syncSpotlightTracking();
            }
        }

        setPersistentSpotlight(element) {
            this.ensureRoot();
            if (this.clearIfSpotlightSuppressed()) {
                return;
            }
            this.spotlightState.setPersistent(element);
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        activateSpotlight(element) {
            this.ensureRoot();
            if (this.clearIfSpotlightSuppressed()) {
                return;
            }
            this.spotlightState.setAction(element);
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        activateSecondarySpotlight(element) {
            this.ensureRoot();
            if (this.clearIfSpotlightSuppressed()) {
                return;
            }
            this.spotlightState.setSecondaryAction(element);
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        clearActionSpotlight() {
            this.ensureRoot();
            this.spotlightState.clearAction();
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        clearPersistentSpotlight() {
            this.ensureRoot();
            this.spotlightState.clearPersistent();
            this.refreshSpotlight();
            this.syncSpotlightTracking();
        }

        clearSpotlight(options) {
            const preservePcOverlaySpotlights = !!(
                options
                && options.preservePcOverlaySpotlights === true
            );
            this.ensureRoot();
            this.stopSpotlightTracking();
            this.spotlightState.clearAll();
            if (this.isPcOverlayActive() && !preservePcOverlaySpotlights) {
                if (this.pcCursorOutputSuppressed === true) {
                    this.overlayRenderer.clearCursorCache();
                }
                this.overlayRenderer.setSpotlights([]);
            }

            if (this.backdrop) {
                this.hideBackdrop();
            }
            this.spotlightDomRenderer.updateSpotlightFrame(this.persistentSpotlightFrame, null);
            this.spotlightDomRenderer.updateSpotlightFrame(this.actionSpotlightFrame, null);
            this.spotlightDomRenderer.updateSpotlightFrame(this.secondaryActionSpotlightFrame, null);
            this.extraSpotlightEntries.forEach((entry) => {
                if (!entry) {
                    return;
                }
                this.spotlightDomRenderer.updateSpotlightFrame(entry.frame, null);
            });
        }

        hasCursorPosition() {
            this.cursorState.updateMotion();
            return this.cursorState.hasPosition();
        }

        isCursorVisible() {
            return this.cursorState.isVisible();
        }

        getCursorPosition() {
            this.cursorState.updateMotion();
            return this.cursorState.getPosition();
        }

        syncCursorPosition(x, y, visible) {
            const didSync = this.cursorState.syncPosition(x, y, visible);
            if (didSync && this.isPcOverlayActive() && this.cursorState.isVisible()) {
                this.overlayRenderer.moveCursorTo(x, y, 0, '');
                this.keepDomCursorSuppressedForPcOverlay();
            }
            return didSync;
        }

        clearCursorPosition() {
            this.cursorState.clear();
            if (this.isPcOverlayActive()) {
                this.overlayRenderer.clearCursorCache();
            }
        }

        finishSuppressedCursorMotion(completed) {
            return this.cursorState.finishMotion(completed);
        }

        updateSuppressedCursorMotion(now) {
            return this.cursorState.updateMotion(now);
        }

        scheduleSuppressedCursorMotionTick() {
            return this.cursorState.scheduleMotionTick();
        }

        animateSuppressedCursorPositionTo(x, y, durationMs, options) {
            return this.cursorState.animateTo(x, y, durationMs, options);
        }

        keepDomCursorSuppressedForPcOverlay() {
            this.cursorState.markVisible();
        }

        setPcCursorOutputSuppressed(suppressed) {
            this.pcCursorOutputSuppressed = suppressed === true;
            if (this.pcCursorOutputSuppressed && this.isPcOverlayActive()) {
                this.overlayRenderer.clearCursorCache();
            }
        }

        shouldForwardCursorToPcOverlay() {
            return this.isPcOverlayActive() && this.pcCursorOutputSuppressed !== true;
        }

        getSmoothCursorShowDurationMs(x, y) {
            return this.cursorState.getSmoothShowDurationMs(x, y, SMOOTH_CURSOR_SHOW_DURATION_MS);
        }

        showCursorAt(x, y) {
            this.ensureRoot();
            this.updateSuppressedCursorMotion();
            const previous = this.cursorPosition;
            const glideDurationMs = this.getSmoothCursorShowDurationMs(x, y);
            if (this.shouldForwardCursorToPcOverlay()) {
                if (glideDurationMs > 0) {
                    this.overlayRenderer.moveCursorTo(x, y, glideDurationMs, '');
                } else {
                    this.overlayRenderer.showCursorAt(x, y);
                }
                this.keepDomCursorSuppressedForPcOverlay();
                if (previous && glideDurationMs > 0) {
                    return this.animateSuppressedCursorPositionTo(x, y, glideDurationMs);
                }
                this.cursorPosition = { x: x, y: y };
                this.cursorVisible = true;
                return Promise.resolve(true);
            }
            if (this.isPcOverlayActive()) {
                this.keepDomCursorSuppressedForPcOverlay();
            }
            this.cursorPosition = { x: x, y: y };
            this.cursorVisible = this.isPcOverlayActive();
            return Promise.resolve(true);
        }

        moveCursorTo(x, y, options) {
            this.updateSuppressedCursorMotion();
            const normalizedOptions = options || {};
            const durationMs = Number.isFinite(normalizedOptions.durationMs) ? normalizedOptions.durationMs : 480;
            const cursorEffect = normalizedOptions.effect || '';
            const cursorEffectDurationMs = Math.max(0, Math.round(Number(normalizedOptions.effectDurationMs) || 0));
            const forcePcOverlayCursorOnly = normalizedOptions.forcePcOverlay === true
                && this.overlayRenderer
                && this.overlayRenderer.pcOverlayBridge
                && typeof this.overlayRenderer.pcOverlayBridge.moveCursorOnlyTo === 'function';
            if (this.shouldForwardCursorToPcOverlay() || forcePcOverlayCursorOnly) {
                const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                    ? normalizedOptions.pauseCheck
                    : null;
                const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                    ? normalizedOptions.cancelCheck
                    : null;
                if (!this.cursorPosition) {
                    if (forcePcOverlayCursorOnly) {
                        this.overlayRenderer.pcOverlayBridge.moveCursorOnlyTo(x, y, 0, cursorEffect, cursorEffectDurationMs);
                    } else {
                        this.overlayRenderer.showCursorAt(x, y);
                    }
                    this.keepDomCursorSuppressedForPcOverlay();
                    this.cursorPosition = { x: x, y: y };
                    this.cursorVisible = true;
                    return Promise.resolve(true);
                }
                if (this.cursorPosition) {
                    if (forcePcOverlayCursorOnly) {
                        this.overlayRenderer.pcOverlayBridge.moveCursorOnlyTo(
                            x,
                            y,
                            durationMs,
                            cursorEffect,
                            cursorEffectDurationMs
                        );
                    } else {
                        this.overlayRenderer.moveCursorTo(
                            x,
                            y,
                            durationMs,
                            cursorEffect,
                            cursorEffectDurationMs
                        );
                    }
                    this.keepDomCursorSuppressedForPcOverlay();
                    return this.animateSuppressedCursorPositionTo(x, y, durationMs, {
                        pauseCheck: pauseCheck,
                        cancelCheck: cancelCheck
                    });
                }
            }
            const pauseCheck = typeof normalizedOptions.pauseCheck === 'function'
                ? normalizedOptions.pauseCheck
                : null;
            const cancelCheck = typeof normalizedOptions.cancelCheck === 'function'
                ? normalizedOptions.cancelCheck
                : null;

            if (this.isPcOverlayActive()) {
                this.keepDomCursorSuppressedForPcOverlay();
            }
            if (!this.cursorPosition) {
                this.cursorPosition = { x: x, y: y };
                this.cursorVisible = this.isPcOverlayActive();
                return Promise.resolve(true);
            }

            this.cursorVisible = this.isPcOverlayActive();
            return this.animateSuppressedCursorPositionTo(x, y, durationMs, {
                pauseCheck: pauseCheck,
                cancelCheck: cancelCheck
            });
        }

        clickCursor(durationMs) {
            if (this.shouldForwardCursorToPcOverlay()) {
                if (this.cursorPosition) {
                    this.overlayRenderer.moveCursorTo(
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
            if (this.isPcOverlayActive()) {
                this.keepDomCursorSuppressedForPcOverlay();
                return;
            }
            this.cursorVisible = false;
        }

        wobbleCursor(effectDurationMs) {
            if (this.shouldForwardCursorToPcOverlay()) {
                if (this.cursorPosition) {
                    this.overlayRenderer.moveCursorTo(this.cursorPosition.x, this.cursorPosition.y, 0, 'wobble', effectDurationMs);
                }
                this.keepDomCursorSuppressedForPcOverlay();
                return;
            }
            if (this.isPcOverlayActive()) {
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

                        if (self.shouldForwardCursorToPcOverlay() && (!lastSentAt || now - lastSentAt >= 32 || progress >= 1)) {
                            lastSentAt = now;
                            self.overlayRenderer.moveCursorTo(x, y, 40, '');
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
            if (this.shouldForwardCursorToPcOverlay()) {
                this.overlayRenderer.hideCursor();
                this.cursorVisible = false;
                return;
            }
            if (this.isPcOverlayActive()) {
                this.cursorVisible = false;
                return;
            }
            this.cursorVisible = false;
        }

        playPetalTransition(origin, options) {
            if (!this.isPcOverlayActive() || !this.overlayRenderer.playPetalTransition(origin, options || {})) {
                return null;
            }
            return null;
        }

        destroy() {
            this.clearAvatarStandIn();
            this.overlayRenderer.clear();
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
