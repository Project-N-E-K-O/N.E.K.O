(function (root, factory) {
    'use strict';

    const api = factory(root);
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialPetalTransitionController = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const RETURN_PETAL_ANIMATION_EXTRA_MS = 1000;
    const RETURN_PETAL_SEQUENCE_URL = '/static/assets/tutorial/petals/yui-guide-petal-transition.webp';
    const RETURN_PETAL_SEQUENCE_DURATION_MS = 6200;
    const RETURN_PETAL_FINAL_OPACITY = 0.6;

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function easeInOutCubic(value) {
        const progress = clamp(Number(value) || 0, 0, 1);
        return progress < 0.5
            ? 4 * progress * progress * progress
            : 1 - Math.pow(-2 * progress + 2, 3) / 2;
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

    class PetalTransitionController {
        constructor(director) {
            this.director = director;
            this.active = false;
            this.returnPetalSequencePromise = null;
            this.returnOpacityRestores = null;
        }

        isActive() {
            return this.active;
        }

        createReturnOptions(options) {
            const normalizedOptions = Object.assign({}, options || {});
            const originalOnTransitionStart = normalizedOptions.onTransitionStart;
            let transitionStartNotified = false;
            normalizedOptions.onTransitionStart = () => {
                if (transitionStartNotified) {
                    return;
                }
                transitionStartNotified = true;
                if (typeof originalOnTransitionStart === 'function') {
                    try {
                        originalOnTransitionStart();
                    } catch (error) {
                        console.warn('[YuiGuide] 花瓣转场开始回调失败:', error);
                    }
                }
            };
            return normalizedOptions;
        }

        normalizeReturnTiming(options) {
            const normalizedOptions = options || {};
            const reducedMotion = !!(
                this.director
                && typeof this.director.shouldReduceTutorialMotion === 'function'
                && this.director.shouldReduceTutorialMotion()
            );
            const explicitDurationMs = Number(normalizedOptions.durationMs);
            const hasExplicitDuration = Number.isFinite(explicitDurationMs) && explicitDurationMs >= 0;
            const baseTransitionDurationMs = hasExplicitDuration
                ? Math.round(explicitDurationMs)
                : (reducedMotion
                    ? clamp(Math.round(Number(normalizedOptions.durationMs) || 420), 240, 720)
                    : clamp(Math.round(Number(normalizedOptions.durationMs) || 4800), 2600, 7600));
            const transitionDurationMs = reducedMotion
                ? baseTransitionDurationMs
                : Math.max(
                    baseTransitionDurationMs + RETURN_PETAL_ANIMATION_EXTRA_MS,
                    RETURN_PETAL_SEQUENCE_DURATION_MS
                );
            return {
                baseTransitionDurationMs,
                transitionDurationMs,
                finalOpacity: RETURN_PETAL_FINAL_OPACITY
            };
        }

        preloadReturnPetalSequence() {
            if (!this.returnPetalSequencePromise) {
                this.returnPetalSequencePromise = this.loadReturnPetalSequence().catch(() => null);
            }
            return this.returnPetalSequencePromise;
        }

        getReturnPetalSequenceUrl() {
            return this.director
                && typeof this.director.getReturnPetalSequenceUrl === 'function'
                ? this.director.getReturnPetalSequenceUrl()
                : RETURN_PETAL_SEQUENCE_URL;
        }

        loadReturnPetalSequence() {
            const sequenceUrl = this.getReturnPetalSequenceUrl();
            const ImageClass = typeof Image !== 'undefined'
                ? Image
                : (typeof globalThis !== 'undefined' ? globalThis.Image : null);
            if (!ImageClass || !sequenceUrl) {
                return Promise.resolve(null);
            }

            return new Promise((resolve) => {
                const image = new ImageClass();
                image.decoding = 'async';
                image.onload = () => {
                    resolve({
                        url: sequenceUrl,
                        image: image,
                        width: image.naturalWidth || image.width || 0,
                        height: image.naturalHeight || image.height || 0
                    });
                };
                image.onerror = () => {
                    console.warn('[YuiGuide] 花瓣 animated WebP 加载失败:', sequenceUrl);
                    resolve(null);
                };
                image.src = sequenceUrl;
            });
        }

        resolveReturnOrigin() {
            return this.director
                && typeof this.director.getReturnPetalTransitionOrigin === 'function'
                ? this.director.getReturnPetalTransitionOrigin()
                : null;
        }

        canStartPcPetalImmediately() {
            return !!(
                this.director
                && this.director.overlay
                && typeof this.director.overlay.isPcOverlayActive === 'function'
                && this.director.overlay.isPcOverlayActive()
                && typeof this.director.overlay.playPetalTransition === 'function'
            );
        }

        createReturnPlaybackContext(options) {
            const returnOptions = this.createReturnOptions(options);
            if (!returnOptions.petalSequencePromise) {
                returnOptions.petalSequencePromise = this.preloadReturnPetalSequence();
            }
            return Object.assign(
                returnOptions,
                this.normalizeReturnTiming(returnOptions),
                {
                    origin: this.resolveReturnOrigin(),
                    canStartPcPetalImmediately: this.canStartPcPetalImmediately(),
                    fadeModelOut: (durationMs) => {
                        const director = this.director;
                        return director && typeof director.fadeReturnPetalTransitionModelOut === 'function'
                            ? director.fadeReturnPetalTransitionModelOut(durationMs)
                            : this.fadeReturnModelOut(durationMs);
                    },
                    createReturnPetalTransition: (origin, transitionOptions) => {
                        const director = this.director;
                        return director && typeof director.createReturnPetalTransition === 'function'
                            ? director.createReturnPetalTransition(origin, transitionOptions)
                            : this.createReturnPetalTransition(origin, transitionOptions);
                    },
                    restoreTutorialAvatar: () => this.restoreTutorialAvatarForReturn(),
                    restoreOpacityTargets: () => {
                        this.restoreOpacityTargets();
                    }
                }
            );
        }

        notifyTransitionStart(options) {
            if (options && typeof options.onTransitionStart === 'function') {
                options.onTransitionStart();
            }
        }

        isCancelled() {
            const director = this.director || {};
            return !!(
                director.destroyed
                || (
                    typeof director.isStopping === 'function'
                    && director.isStopping()
                )
            );
        }

        waitForNarrationEnd(durationMs) {
            const win = getBrowserWindow();
            const totalMs = Math.max(0, Number(durationMs) || 0);
            if (totalMs <= 0 || this.isCancelled()) {
                return Promise.resolve(!this.isCancelled());
            }
            return new Promise((resolve) => {
                const startedAt = Date.now();
                const schedule = win && typeof win.setTimeout === 'function'
                    ? win.setTimeout.bind(win)
                    : setTimeout;
                const tick = () => {
                    if (this.isCancelled()) {
                        resolve(false);
                        return;
                    }
                    if (Date.now() - startedAt >= totalMs) {
                        resolve(true);
                        return;
                    }
                    schedule(tick, Math.min(80, totalMs));
                };
                schedule(tick, Math.min(80, totalMs));
            });
        }

        async finishTransition(transition) {
            if (!transition || transition.__yuiGuideFinished) {
                return;
            }
            transition.__yuiGuideFinished = true;
            if (this.isCancelled()) {
                if (typeof transition.finish === 'function') {
                    await transition.finish();
                }
                return;
            }
            if (typeof transition.done === 'function') {
                const completed = await this.cancelWhenStopped(transition.done());
                if (completed === false) {
                    if (typeof transition.finish === 'function') {
                        await transition.finish();
                    }
                    return;
                }
            }
            if (typeof transition.finish === 'function') {
                await transition.finish();
            }
        }

        async executeReturnTransition(options) {
            const normalizedOptions = options || {};
            const director = this.director || {};
            if (this.isCancelled()) {
                return;
            }

            const petalSequencePromise = normalizedOptions.petalSequencePromise || Promise.resolve(null);
            const origin = normalizedOptions.origin || this.resolveReturnOrigin();
            const baseTransitionDurationMs = normalizedOptions.baseTransitionDurationMs;
            const transitionDurationMs = normalizedOptions.transitionDurationMs;
            const finalOpacity = normalizedOptions.finalOpacity;
            const fadeModelOut = typeof normalizedOptions.fadeModelOut === 'function'
                ? normalizedOptions.fadeModelOut
                : (durationMs) => this.fadeReturnModelOut(durationMs);
            const restoreTutorialAvatar = typeof normalizedOptions.restoreTutorialAvatar === 'function'
                ? normalizedOptions.restoreTutorialAvatar
                : () => this.restoreTutorialAvatarForReturn();
            const restoreOpacityTargets = typeof normalizedOptions.restoreOpacityTargets === 'function'
                ? normalizedOptions.restoreOpacityTargets
                : () => this.restoreOpacityTargets();
            const createReturnPetalTransition = typeof normalizedOptions.createReturnPetalTransition === 'function'
                ? normalizedOptions.createReturnPetalTransition
                : (transitionOrigin, transitionOptions) => this.createReturnPetalTransition(transitionOrigin, transitionOptions);
            const canStartPcPetalImmediately = typeof normalizedOptions.canStartPcPetalImmediately === 'boolean'
                ? normalizedOptions.canStartPcPetalImmediately
                : this.canStartPcPetalImmediately();
            let transition = null;
            let fadePromise = null;
            try {
                if (canStartPcPetalImmediately) {
                    const overlay = director.overlay || null;
                    if (overlay && typeof overlay.playPetalTransition === 'function') {
                        overlay.playPetalTransition(origin, {
                            durationMs: transitionDurationMs,
                            finalOpacity: finalOpacity
                        });
                    }
                    this.notifyTransitionStart(normalizedOptions);
                    fadePromise = this.cancelWhenStopped(fadeModelOut(baseTransitionDurationMs));
                    const loadedPetalSequence = await this.cancelWhenStopped(petalSequencePromise);
                    if (this.isCancelled()) {
                        await this.finishTransition(transition);
                        return;
                    }
                    if (loadedPetalSequence) {
                        transition = createReturnPetalTransition(origin, {
                            durationMs: transitionDurationMs,
                            finalOpacity: finalOpacity,
                            sequence: loadedPetalSequence,
                            skipPcOverlay: true
                        });
                    }
                } else {
                    const loadedPetalSequence = await this.cancelWhenStopped(petalSequencePromise);
                    if (this.isCancelled()) {
                        await this.finishTransition(transition);
                        return;
                    }
                    transition = createReturnPetalTransition(origin, {
                        durationMs: transitionDurationMs,
                        finalOpacity: finalOpacity,
                        sequence: loadedPetalSequence,
                        onStart: () => this.notifyTransitionStart(normalizedOptions)
                    });
                    if (!transition) {
                        this.notifyTransitionStart(normalizedOptions);
                    }
                    fadePromise = this.cancelWhenStopped(fadeModelOut(baseTransitionDurationMs));
                }
                await Promise.all([
                    this.waitForNarrationEnd(baseTransitionDurationMs),
                    fadePromise
                ]);
                if (this.isCancelled()) {
                    return;
                }
                await restoreTutorialAvatar();
                if (this.isCancelled()) {
                    return;
                }
                restoreOpacityTargets();
            } finally {
                await this.finishTransition(transition);
                restoreOpacityTargets();
            }
        }

        cancelWhenStopped(promise) {
            if (this.isCancelled()) {
                return Promise.resolve(false);
            }
            const win = getBrowserWindow();
            return new Promise((resolve) => {
                let settled = false;
                let timer = null;
                const done = (value) => {
                    if (settled) {
                        return;
                    }
                    settled = true;
                    if (timer && win && typeof win.clearInterval === 'function') {
                        win.clearInterval(timer);
                    } else if (timer) {
                        clearInterval(timer);
                    }
                    resolve(value);
                };
                const check = () => {
                    if (this.isCancelled()) {
                        done(false);
                    }
                };
                const setTimer = win && typeof win.setInterval === 'function'
                    ? win.setInterval.bind(win)
                    : setInterval;
                timer = setTimer(check, 50);
                Promise.resolve(promise).then(done).catch(() => done(false));
                check();
            });
        }

        fadeReturnModelOut(durationMs) {
            const win = getBrowserWindow();
            const doc = getBrowserDocument();
            const director = this.director || {};
            const model = this.getReturnModel();
            const targets = this.prepareReturnOpacityTargets(model);
            if (!win || !doc || !doc.body || targets.length <= 0 || typeof win.requestAnimationFrame !== 'function') {
                return Promise.resolve(false);
            }

            const duration = this.shouldReduceMotion()
                ? Math.min(320, Math.max(160, Number(durationMs) || 260))
                : Math.max(240, Number(durationMs) || 920);
            const performanceApi = win.performance && typeof win.performance.now === 'function'
                ? win.performance
                : null;
            const startedAt = performanceApi ? performanceApi.now() : Date.now();
            doc.body.classList.add('yui-guide-return-petal-fade');
            doc.body.style.setProperty('--yui-guide-return-avatar-opacity', '1');
            return new Promise((resolve) => {
                const tick = (now) => {
                    if (this.isCancelled()) {
                        resolve(false);
                        return;
                    }
                    const currentNow = Number.isFinite(Number(now))
                        ? Number(now)
                        : (performanceApi ? performanceApi.now() : Date.now());
                    const progress = duration > 0 ? clamp((currentNow - startedAt) / duration, 0, 1) : 1;
                    const opacity = 1 - easeInOutCubic(progress);
                    doc.body.style.setProperty('--yui-guide-return-avatar-opacity', String(clamp(opacity, 0, 1)));
                    targets.forEach((target) => target.apply(target.from * opacity));
                    if (progress >= 1) {
                        resolve(true);
                        return;
                    }
                    win.requestAnimationFrame(tick);
                };
                win.requestAnimationFrame(tick);
            });
        }

        async restoreTutorialAvatarForReturn() {
            const director = this.director || {};
            if (
                !director.tutorialManager
                || typeof director.tutorialManager.restoreTutorialAvatarOverride !== 'function'
            ) {
                return false;
            }

            try {
                await director.tutorialManager.restoreTutorialAvatarOverride();
                return true;
            } catch (error) {
                console.warn('[YuiGuide] 花瓣转场期间恢复新手教程前模型失败:', error);
                return false;
            }
        }

        getReturnModel() {
            const managers = this.collectReturnManagers();
            for (let index = 0; index < managers.length; index += 1) {
                const manager = managers[index];
                try {
                    if (typeof manager.getCurrentModel === 'function') {
                        const model = manager.getCurrentModel();
                        if (model && Number.isFinite(Number(model.alpha))) {
                            return model;
                        }
                    }
                    if (manager.currentModel && Number.isFinite(Number(manager.currentModel.alpha))) {
                        return manager.currentModel;
                    }
                } catch (_) {}
            }
            return null;
        }

        collectReturnManagers() {
            const win = getBrowserWindow();
            if (!win) {
                return [];
            }
            return [
                win.live2dManager,
                win.vrmManager,
                win.mmdManager
            ].filter(Boolean);
        }

        getReturnOpacityElements() {
            const win = getBrowserWindow();
            const doc = getBrowserDocument();
            if (!win || !doc || typeof doc.querySelector !== 'function') {
                return [];
            }
            const director = this.director || {};
            const prefix = typeof director.resolveModelPrefix === 'function'
                ? director.resolveModelPrefix()
                : 'live2d';
            const selectors = [
                '#' + prefix + '-container',
                '#' + prefix + '-canvas',
                '#live2d-container',
                '#live2d-canvas',
                '#vrm-container',
                '#vrm-canvas',
                '#mmd-container',
                '#mmd-canvas'
            ];
            const elements = [];
            for (let index = 0; index < selectors.length; index += 1) {
                const element = doc.querySelector(selectors[index]);
                if (element && typeof element.getBoundingClientRect === 'function') {
                    const rect = element.getBoundingClientRect();
                    if (rect && rect.width > 0 && rect.height > 0) {
                        elements.push(element);
                    }
                }
            }
            this.collectReturnManagers().forEach((manager) => {
                if (manager.pixi_app && manager.pixi_app.view) {
                    elements.push(manager.pixi_app.view);
                }
                if (manager.renderer && manager.renderer.domElement) {
                    elements.push(manager.renderer.domElement);
                }
                if (manager.canvas) {
                    elements.push(manager.canvas);
                }
                if (manager.container) {
                    elements.push(manager.container);
                }
            });
            return elements.filter((element, index) => elements.indexOf(element) === index);
        }

        prepareReturnOpacityTargets(model) {
            const win = getBrowserWindow();
            const elements = this.getReturnOpacityElements();
            const targets = [];

            this.collectReturnManagers().forEach((manager) => {
                if (manager && manager._canvasRevealTimer) {
                    if (win && typeof win.clearTimeout === 'function') {
                        win.clearTimeout(manager._canvasRevealTimer);
                    }
                    manager._canvasRevealTimer = null;
                }
            });

            if (elements.length > 0) {
                elements.forEach((element) => {
                    const computedStyle = win && typeof win.getComputedStyle === 'function'
                        ? win.getComputedStyle(element)
                        : null;
                    const computedOpacity = parseFloat(computedStyle ? computedStyle.opacity : element.style.opacity);
                    const fromOpacity = Number.isFinite(computedOpacity) ? computedOpacity : 1;
                    const originalInlineOpacity = element.style.opacity;
                    const originalInlineTransition = element.style.transition;
                    targets.push({
                        apply: (opacity) => {
                            element.style.setProperty('transition', 'none', 'important');
                            element.style.setProperty('opacity', String(clamp(opacity, 0, 1)), 'important');
                        },
                        restore: () => {
                            if (originalInlineTransition) {
                                element.style.setProperty('transition', originalInlineTransition);
                            } else {
                                element.style.removeProperty('transition');
                            }
                            if (originalInlineOpacity) {
                                element.style.setProperty('opacity', originalInlineOpacity);
                            } else {
                                element.style.removeProperty('opacity');
                            }
                        },
                        from: fromOpacity
                    });
                });
            }

            if (model && Number.isFinite(Number(model.alpha))) {
                const fromAlpha = Number(model.alpha);
                targets.push({
                    apply: (opacity) => {
                        try {
                            model.alpha = opacity;
                        } catch (_) {}
                    },
                    restore: () => {
                        try {
                            model.alpha = fromAlpha;
                        } catch (_) {}
                    },
                    from: fromAlpha
                });
            }

            this.returnOpacityRestores = targets.map((target) => target.restore);
            return targets;
        }

        restoreOpacityTargets() {
            const doc = getBrowserDocument();
            const restores = Array.isArray(this.returnOpacityRestores)
                ? this.returnOpacityRestores
                : [];
            this.returnOpacityRestores = null;
            if (doc && doc.body) {
                doc.body.classList.remove('yui-guide-return-petal-fade');
                doc.body.style.removeProperty('--yui-guide-return-avatar-opacity');
            }
            restores.forEach((restore) => {
                try {
                    restore();
                } catch (_) {}
            });
        }

        shouldReduceMotion() {
            return !!(
                this.director
                && typeof this.director.shouldReduceTutorialMotion === 'function'
                && this.director.shouldReduceTutorialMotion()
            );
        }

        getViewportCenter() {
            const win = getBrowserWindow();
            if (this.director && typeof this.director.getViewportCenter === 'function') {
                return this.director.getViewportCenter();
            }
            return {
                x: Math.max(1, (win && win.innerWidth) || 1) / 2,
                y: Math.max(1, (win && win.innerHeight) || 1) / 2
            };
        }

        createReturnPetalTransition(origin, options) {
            const win = getBrowserWindow();
            const doc = getBrowserDocument();
            const normalizedOptions = options || {};
            const finalPetalOpacity = Number.isFinite(Number(normalizedOptions.finalOpacity))
                ? clamp(Number(normalizedOptions.finalOpacity), 0, 1)
                : RETURN_PETAL_FINAL_OPACITY;
            if (!win || !doc) {
                return null;
            }

            const overlay = this.director && this.director.overlay;
            if (
                !normalizedOptions.skipPcOverlay
                && overlay
                && typeof overlay.playPetalTransition === 'function'
            ) {
                const pcTransition = overlay.playPetalTransition(origin, {
                    durationMs: normalizedOptions.durationMs,
                    finalOpacity: finalPetalOpacity
                });
                if (pcTransition) {
                    const transitionMs = this.shouldReduceMotion()
                        ? clamp(Math.round(Number(normalizedOptions.durationMs) || 420), 240, 720)
                        : clamp(Math.round(Number(normalizedOptions.durationMs) || 1600), 900, 8600);
                    if (typeof normalizedOptions.onStart === 'function') {
                        win.requestAnimationFrame(() => {
                            try {
                                normalizedOptions.onStart();
                            } catch (error) {
                                console.warn('[YuiGuide] PC 全局花瓣转场启动回调失败:', error);
                            }
                        });
                    }
                    return {
                        done: () => new Promise((resolve) => win.setTimeout(resolve, transitionMs)),
                        finish: () => Promise.resolve()
                    };
                }
            }

            const root = overlay && typeof overlay.ensureRoot === 'function'
                ? overlay.ensureRoot()
                : null;
            const stage = root ? root.querySelector('.yui-guide-stage') : null;
            if (!stage) {
                return null;
            }

            const oldLayer = stage.querySelector('.yui-guide-petal-transition');
            if (oldLayer && oldLayer.parentNode) {
                oldLayer.parentNode.removeChild(oldLayer);
            }

            const sequence = normalizedOptions.sequence || null;
            if (!sequence || !sequence.url) {
                return null;
            }

            const layer = doc.createElement('div');
            layer.className = 'yui-guide-petal-transition';
            layer.setAttribute('aria-hidden', 'true');
            const width = Math.max(1, win.innerWidth || 1);
            const height = Math.max(1, win.innerHeight || 1);
            const start = origin || this.getViewportCenter();
            const reducedMotion = this.shouldReduceMotion();
            const transitionMs = reducedMotion
                ? clamp(Math.round(Number(normalizedOptions.durationMs) || 420), 240, 720)
                : clamp(Math.round(Number(normalizedOptions.durationMs) || 1600), 900, 8600);
            const playback = doc.createElement('img');
            playback.className = 'yui-guide-petal-sequence';
            playback.alt = '';
            playback.decoding = 'async';
            playback.draggable = false;
            playback.src = sequence.url;
            playback.style.animationDuration = transitionMs + 'ms';
            playback.style.setProperty('--yui-guide-petal-origin-x', clamp(start.x, -width, width * 2) + 'px');
            playback.style.setProperty('--yui-guide-petal-origin-y', clamp(start.y, -height, height * 2) + 'px');
            playback.style.setProperty('--yui-guide-petal-final-opacity', String(finalPetalOpacity));
            layer.appendChild(playback);

            let doneTimer = 0;
            let playbackStopTimer = 0;
            let doneResolved = false;
            let playbackStopped = false;
            let resolveDone = null;
            const playbackStopMs = reducedMotion
                ? transitionMs
                : Math.min(transitionMs, RETURN_PETAL_SEQUENCE_DURATION_MS);
            const stopPlayback = () => {
                if (playbackStopped) {
                    return;
                }
                playbackStopped = true;
                playback.style.animationPlayState = 'paused';
                playback.removeAttribute('src');
                playback.style.display = 'none';
            };
            const donePromise = new Promise((resolve) => {
                resolveDone = resolve;
                if (playbackStopMs < transitionMs) {
                    playbackStopTimer = win.setTimeout(() => {
                        playbackStopTimer = 0;
                        stopPlayback();
                    }, playbackStopMs);
                }
                doneTimer = win.setTimeout(() => {
                    doneResolved = true;
                    doneTimer = 0;
                    if (playbackStopTimer) {
                        win.clearTimeout(playbackStopTimer);
                        playbackStopTimer = 0;
                    }
                    stopPlayback();
                    resolve();
                }, transitionMs);
            });
            const settleDone = () => {
                if (doneResolved) {
                    return;
                }
                doneResolved = true;
                if (doneTimer) {
                    win.clearTimeout(doneTimer);
                    doneTimer = 0;
                }
                if (playbackStopTimer) {
                    win.clearTimeout(playbackStopTimer);
                    playbackStopTimer = 0;
                }
                stopPlayback();
                if (typeof resolveDone === 'function') {
                    resolveDone();
                }
            };

            stage.appendChild(layer);
            win.requestAnimationFrame(() => {
                layer.classList.add('is-active');
                if (typeof normalizedOptions.onStart === 'function') {
                    try {
                        normalizedOptions.onStart();
                    } catch (error) {
                        console.warn('[YuiGuide] 花瓣转场启动回调失败:', error);
                    }
                }
            });

            return {
                done: () => donePromise,
                finish: () => new Promise((resolve) => {
                    settleDone();
                    layer.classList.add('is-exiting');
                    win.setTimeout(() => {
                        if (layer.parentNode) {
                            layer.parentNode.removeChild(layer);
                        }
                        resolve();
                    }, reducedMotion ? 220 : 520);
                })
            };
        }

        playReturn(options) {
            if (this.active || (this.director && this.director.destroyed)) {
                return undefined;
            }

            this.active = true;
            if (this.director) {
                this.director.returnPetalTransitionActive = true;
            }
            const finish = () => {
                this.active = false;
                if (this.director) {
                    this.director.returnPetalTransitionActive = false;
                }
            };
            try {
                const returnOptions = this.createReturnPlaybackContext(options);
                const result = this.executeReturnTransition(returnOptions);
                if (result && typeof result.then === 'function') {
                    return result.finally(finish);
                }
                finish();
                return result;
            } catch (error) {
                finish();
                throw error;
            }
        }

        async playAtCue(scene, sceneRunId, voiceKey, text, narrationStartedAt, cueWindowMs) {
            const director = this.director;
            const petalSequencePromise = this.preloadReturnPetalSequence();
            const durationMs = director.getAvatarFloatingNarrationDurationMs(voiceKey, text);
            const reducedMotion = director.shouldReduceTutorialMotion();
            const defaultCueWindowMs = reducedMotion ? 900 : 2600;
            const minimumPetalDurationMs = Number.isFinite(cueWindowMs)
                ? Math.max(1, Math.floor(cueWindowMs))
                : defaultCueWindowMs;
            // 修改原因：所有新手教程的花瓣转场都包含挥手和模型恢复，开始点应由动画窗口反推，
            // 并复用 timeline 事件的 beforeAudioEndMs，避免派发 cue 和实际动画窗口漂移。
            const cueMs = clamp(Math.round(durationMs - minimumPetalDurationMs), 900, Math.max(900, durationMs));
            const elapsedMs = Math.max(0, Date.now() - narrationStartedAt);
            const waitMs = Math.max(0, cueMs - elapsedMs);
            if (!(await director.waitForSceneDelay(waitMs))) {
                return;
            }
            if (
                sceneRunId !== director.sceneRunId
                || director.destroyed
                || (typeof director.isStopping === 'function' && director.isStopping())
            ) {
                return;
            }

            let clearedForPetalTransition = false;
            const clearForPetalTransition = () => {
                if (clearedForPetalTransition) {
                    return;
                }
                clearedForPetalTransition = true;
                director.cursor.hide();
                director.clearExternalizedChatGuideTarget({
                    clearCursor: true
                });
                director.overlay.clearPersistentSpotlight();
                director.overlay.clearActionSpotlight();
                director.clearSceneExtraSpotlights();
                director.clearRetainedExtraSpotlights();
                director.clearAllVirtualSpotlights();
                director.clearSpotlightGeometryHints();
                director.clearSpotlightVariantHints();
                director.disableInterrupts();
            };

            director.runReturnControlCueWavePerformance().catch((error) => {
                console.warn('[YuiGuide] 悬浮窗教程收尾挥手动作播放失败:', error);
            });
            const remainingMs = Math.max(0, durationMs - cueMs);
            try {
                await this.playReturn({
                    durationMs: Math.max(remainingMs, minimumPetalDurationMs),
                    petalSequencePromise,
                    onTransitionStart: clearForPetalTransition
                });
            } finally {
                clearForPetalTransition();
            }
        }
    }


    return {
        PetalTransitionController
    };
});
