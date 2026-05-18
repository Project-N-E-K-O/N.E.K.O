(function () {
    'use strict';

    if (window.YuiGuideWakeup) {
        return;
    }

    const DEFAULT_DURATION_MS = 4000;
    const REDUCED_MOTION_DURATION_MS = 520;
    const LIVE2D_READY_WAIT_MS = 900;
    const STORAGE_WATCH_INTERVAL_MS = 120;
    const OVERLAY_CLEANUP_INTERVAL_MS = 120;

    function getAvatarStageApi() {
        return window.YuiGuideAvatarStage || null;
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

    function isElementVisible(element) {
        if (!element || element.hidden) {
            return false;
        }

        try {
            const style = window.getComputedStyle(element);
            if (
                style.display === 'none'
                || style.visibility === 'hidden'
                || Number.parseFloat(style.opacity || '1') <= 0
            ) {
                return false;
            }
        } catch (_) {}

        return true;
    }

    function isStorageLocationOverlayVisible(doc) {
        const root = (doc || document).querySelector('#storage-location-overlay');
        return isElementVisible(root);
    }

    function removeBlockingGuideOverlay(doc) {
        const ownerDocument = doc || document;
        try {
            ownerDocument.querySelectorAll(
                '#yui-guide-overlay, .yui-guide-wakeup-stage, .yui-guide-wakeup-backdrop, .yui-guide-wakeup-particles'
            ).forEach((element) => {
                if (element && element.parentNode) {
                    element.parentNode.removeChild(element);
                }
            });
        } catch (_) {}

        try {
            ownerDocument.body.classList.remove('yui-taking-over');
            ownerDocument.body.classList.remove('yui-guide-ghost-cursor-active');
            ownerDocument.documentElement.style.cursor = '';
            ownerDocument.body.style.cursor = '';
        } catch (_) {}
    }

    function revealPreparedTutorialLive2D(reason) {
        try {
            if (document && document.body) {
                document.body.classList.remove('yui-guide-live2d-preparing');
            }
            window.dispatchEvent(new CustomEvent('neko:yui-guide:live2d-prepared-revealed', {
                detail: {
                    reason: reason || '',
                    timestamp: Date.now()
                }
            }));
        } catch (_) {}
    }

    function normalizeDuration(value, fallback) {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? number : fallback;
    }

    function waitForLive2DContext(timeoutMs) {
        const api = getAvatarStageApi();
        if (api && typeof api.waitForLive2DContext === 'function') {
            return api.waitForLive2DContext(timeoutMs);
        }
        return Promise.resolve(null);
    }

    function createWakeupSession(context, options) {
        const api = getAvatarStageApi();
        if (!api || typeof api.createWakeupSession !== 'function') {
            return null;
        }
        return api.createWakeupSession(context, options);
    }

    function getSessionParamCount(session) {
        return session && session.params && typeof session.params === 'object'
            ? Object.keys(session.params).length
            : 0;
    }

    class YuiGuideWakeupController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.metrics = normalizedOptions.metrics || null;
            this.live2dSession = null;
            this.live2dSessionToken = 0;
            this.runToken = 0;
            this.finishCurrentRun = null;
            this.storageWatchTimer = 0;
            this.overlayWatchTimer = 0;
        }

        isSupported() {
            return !isStorageLocationOverlayVisible(this.document);
        }

        record(type, detail) {
            if (!this.metrics || typeof this.metrics.record !== 'function') {
                return;
            }

            try {
                this.metrics.record(type, Object.assign({
                    page: 'home',
                    source: 'yui_guide_wakeup'
                }, detail && typeof detail === 'object' ? detail : {}));
            } catch (_) {}
        }

        clearStorageWatch() {
            if (this.storageWatchTimer) {
                window.clearInterval(this.storageWatchTimer);
                this.storageWatchTimer = 0;
            }
        }

        clearOverlayWatch() {
            if (this.overlayWatchTimer) {
                window.clearInterval(this.overlayWatchTimer);
                this.overlayWatchTimer = 0;
            }
        }

        async startLive2DWakeupSession(token, durationMs, reducedMotion, timelineStartedAt) {
            const api = getAvatarStageApi();
            if (!api) {
                return { result: 'fallback', reason: 'avatar_stage_unavailable' };
            }

            const waitBudget = reducedMotion
                ? 0
                : Math.min(LIVE2D_READY_WAIT_MS, Math.max(0, Math.round(durationMs - 180)));
            const context = await waitForLive2DContext(waitBudget);
            if (this.runToken !== token) {
                return { result: 'cancelled', reason: 'session_replaced' };
            }
            if (!context) {
                return { result: 'fallback', reason: 'live2d_unavailable' };
            }

            const session = createWakeupSession(context, {
                durationMs: durationMs,
                reducedMotion: reducedMotion,
                timelineStartedAt: timelineStartedAt,
                token: token,
                onInitialPose: () => {
                    revealPreparedTutorialLive2D('wakeup_initial_pose');
                }
            });
            if (!session || typeof session.isUsable !== 'function') {
                return { result: 'fallback', reason: 'live2d_session_unavailable' };
            }
            if (!session.isUsable()) {
                return { result: 'fallback', reason: 'live2d_params_missing' };
            }
            if (!session.start || !session.start()) {
                return { result: 'fallback', reason: 'live2d_session_start_failed' };
            }
            this.live2dSession = session;
            this.live2dSessionToken = token;
            return {
                result: 'played',
                reason: '',
                paramCount: getSessionParamCount(session)
            };
        }

        async run(options) {
            const normalizedOptions = options || {};
            if (isStorageLocationOverlayVisible(this.document)) {
                revealPreparedTutorialLive2D('storage_overlay_visible');
                this.record('wakeup_skipped', { reason: 'storage_overlay_visible' });
                return { result: 'skipped', reason: 'storage_overlay_visible' };
            }

            this.cancel('replaced');
            removeBlockingGuideOverlay(this.document);
            const token = ++this.runToken;
            const reducedMotion = shouldReduceMotion();
            const durationMs = reducedMotion
                ? normalizeDuration(REDUCED_MOTION_DURATION_MS, DEFAULT_DURATION_MS)
                : normalizeDuration(normalizedOptions.durationMs, DEFAULT_DURATION_MS);
            const timelineStartedAt = 0;
            let live2dResult = { result: 'pending', reason: '' };
            let live2dSessionPromise = null;
            this.record('wakeup_started', { reducedMotion: reducedMotion });

            return new Promise((resolve) => {
                let settled = false;
                let finishTimer = 0;

                const finish = async (result, reason) => {
                    if (settled || this.runToken !== token) {
                        return;
                    }
                    settled = true;
                    this.finishCurrentRun = null;
                    this.clearStorageWatch();
                    this.clearOverlayWatch();
                    if (this.runToken === token) {
                        this.runToken += 1;
                    }
                    if (finishTimer) {
                        window.clearTimeout(finishTimer);
                        finishTimer = 0;
                    }

                    if (result === 'played' && live2dSessionPromise && live2dResult.result === 'pending') {
                        try {
                            live2dResult = await live2dSessionPromise;
                        } catch (_) {
                            live2dResult = { result: 'fallback', reason: 'live2d_exception' };
                        }
                    } else if (result !== 'played' && live2dResult.result === 'pending') {
                        live2dResult = { result: 'cancelled', reason: reason || result || 'cancelled' };
                    }

                    const activeSession = this.live2dSessionToken === token ? this.live2dSession : null;
                    if (activeSession) {
                        if (result === 'played') {
                            activeSession.stop('played', {
                                preserveFinalPose: true
                            });
                            if (activeSession.result && activeSession.result !== 'played') {
                                live2dResult = {
                                    result: activeSession.result,
                                    reason: activeSession.result
                                };
                            }
                        } else {
                            activeSession.cancel(reason || result || 'cancelled');
                            live2dResult = {
                                result: 'cancelled',
                                reason: reason || result || 'cancelled'
                            };
                        }
                        this.live2dSession = null;
                        this.live2dSessionToken = 0;
                    }

                    const payload = {
                        result: result,
                        reason: reason || '',
                        live2d: live2dResult && live2dResult.result ? live2dResult.result : 'unknown',
                        live2dReason: live2dResult && live2dResult.reason ? live2dResult.reason : '',
                        live2dParamCount: live2dResult && Number.isFinite(live2dResult.paramCount) ? live2dResult.paramCount : 0
                    };
                    this.record(result === 'played' ? 'wakeup_played' : 'wakeup_' + result, payload);
                    resolve(payload);
                };

                this.finishCurrentRun = finish;
                live2dSessionPromise = this.startLive2DWakeupSession(token, durationMs, reducedMotion, timelineStartedAt)
                    .then((result) => {
                        live2dResult = result || { result: 'fallback', reason: 'live2d_unknown' };
                        if (this.runToken === token && !settled) {
                            if (live2dResult.result === 'played') {
                                finishTimer = window.setTimeout(() => {
                                    finish('played', '');
                                }, Math.max(0, Math.round(durationMs)));
                            } else {
                                revealPreparedTutorialLive2D(live2dResult.reason || live2dResult.result);
                                finish(live2dResult.result, live2dResult.reason || '');
                            }
                        }
                        return live2dResult;
                    })
                    .catch((error) => {
                        console.warn('[YuiGuideWakeup] Live2D 苏醒动作失败:', error);
                        live2dResult = { result: 'fallback', reason: 'live2d_exception' };
                        if (this.runToken === token && !settled) {
                            revealPreparedTutorialLive2D(live2dResult.reason);
                            finish(live2dResult.result, live2dResult.reason || '');
                        }
                        return live2dResult;
                    });
                this.storageWatchTimer = window.setInterval(() => {
                    if (isStorageLocationOverlayVisible(this.document)) {
                        finish('cancelled', 'storage_overlay_visible');
                    }
                }, STORAGE_WATCH_INTERVAL_MS);
                this.overlayWatchTimer = window.setInterval(() => {
                    removeBlockingGuideOverlay(this.document);
                }, OVERLAY_CLEANUP_INTERVAL_MS);
            });
        }

        cancel(reason) {
            const finish = this.finishCurrentRun;
            if (typeof finish === 'function') {
                finish('cancelled', reason || 'cancelled');
                return;
            }

            if (this.live2dSession) {
                this.live2dSession.cancel(reason || 'cancelled');
                this.live2dSession = null;
                this.live2dSessionToken = 0;
            }
            this.clearStorageWatch();
            this.clearOverlayWatch();
        }

        destroy() {
            this.cancel('destroy');
        }
    }

    window.YuiGuideWakeup = Object.freeze({
        create: function create(options) {
            return new YuiGuideWakeupController(options);
        },
        isStorageLocationOverlayVisible: isStorageLocationOverlayVisible,
        removeBlockingGuideOverlay: removeBlockingGuideOverlay,
        shouldReduceMotion: shouldReduceMotion
    });
})();
