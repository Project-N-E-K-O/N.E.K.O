(function () {
    'use strict';

    if (window.YuiGuideAvatarStage) {
        return;
    }

    const DEFAULT_OWNER = 'home-yui-guide';
    const DEFAULT_PRIORITY = 80;

    const YUI_PROFILE = Object.freeze({
        defaultFrame: Object.freeze({
            x: 0,
            y: 0,
            scale: 1,
            rotate: 0,
            opacity: ''
        }),
        composition: {
            wakeupSoftSettleStart: { x: 0, y: 18, scale: 0.985, rotate: 0 },
            wakeupSoftSettleEnd: { x: 0, y: 0, scale: 1, rotate: 0 },
            fullBody: { x: 0, y: 0, scale: 1, rotate: 0 }
        }
    });

    const YUI_SEQUENCES = Object.freeze({
        wakeupPolish: Object.freeze([
            Object.freeze({ type: 'frame', mode: 'wakeupSoftSettleStart', durationMs: 0 }),
            Object.freeze({ type: 'frame', mode: 'wakeupSoftSettleEnd', durationMs: 680 })
        ]),
        afterWakeup: Object.freeze([
            Object.freeze({ type: 'frame', mode: 'fullBody', durationMs: 360 })
        ])
    });

    function createStage(options) {
        const normalized = options || {};
        if (!window.AvatarPerformanceStage || typeof window.AvatarPerformanceStage.create !== 'function') {
            return null;
        }

        const driver = normalized.driver || window.AvatarPerformanceStage.createLive2DDriver({
            containerResolver: typeof normalized.containerResolver === 'function'
                ? normalized.containerResolver
                : function () {
                    return document.getElementById('live2d-container');
                },
            managerResolver: typeof normalized.managerResolver === 'function'
                ? normalized.managerResolver
                : function () {
                    return window.live2dManager || null;
                }
        });

        if (typeof window.AvatarPerformanceStage.createLive2DStage !== 'function') {
            return window.AvatarPerformanceStage.create({
                driver: driver,
                profile: normalized.profile || YUI_PROFILE,
                sequences: normalized.sequences || YUI_SEQUENCES,
                logger: normalized.logger || console
            });
        }

        return window.AvatarPerformanceStage.createLive2DStage({
            driver: driver,
            profile: normalized.profile || YUI_PROFILE,
            sequences: normalized.sequences || YUI_SEQUENCES,
            logger: normalized.logger || console
        });
    }

    class YuiGuideAvatarStageController {
        constructor(options) {
            this.options = options || {};
            this.stage = createStage(this.options);
            this.sessionId = '';
            this.destroyed = false;
            this.pendingPolish = null;
            this.owner = String(this.options.owner || DEFAULT_OWNER);
            this.priority = Number.isFinite(Number(this.options.priority)) ? Number(this.options.priority) : DEFAULT_PRIORITY;
        }

        isAvailable() {
            return !!(this.stage && typeof this.stage.isAvailable === 'function' && this.stage.isAvailable());
        }

        ensureSession(reason) {
            if (!this.stage || this.destroyed) {
                return '';
            }
            if (this.sessionId && this.stage.isSessionActive(this.sessionId)) {
                return this.sessionId;
            }
            const session = this.stage.acquire(this.owner + ':' + String(reason || 'guide'), {
                priority: this.priority,
                force: true
            });
            this.sessionId = session && session.id ? session.id : '';
            return this.sessionId;
        }

        polishWakeup() {
            const sessionId = this.ensureSession('wakeup');
            if (!sessionId || !this.stage) {
                return Promise.resolve(false);
            }

            this.pendingPolish = Promise.resolve()
                .then(() => this.stage.runSequence('wakeupPolish', {
                    sessionId: sessionId
                }))
                .catch((error) => {
                    console.warn('[YuiGuideAvatarStage] wakeup polish failed:', error);
                    return false;
                });
            return this.pendingPolish;
        }

        async resumeAfterWakeup() {
            const sessionId = this.sessionId;
            if (!sessionId || !this.stage || !this.stage.isSessionActive(sessionId)) {
                return false;
            }

            try {
                await Promise.race([
                    this.pendingPolish || Promise.resolve(),
                    new Promise((resolve) => window.setTimeout(resolve, 900))
                ]);
                await this.stage.runSequence('afterWakeup', {
                    sessionId: sessionId
                });
            } catch (error) {
                console.warn('[YuiGuideAvatarStage] wakeup resume failed:', error);
            } finally {
                this.release('wakeup-resume');
            }
            return true;
        }

        release(reason) {
            if (!this.stage || !this.sessionId) {
                this.sessionId = '';
                return false;
            }
            const released = this.stage.release(this.sessionId, reason || 'release');
            this.sessionId = '';
            this.pendingPolish = null;
            return released;
        }

        destroy(reason) {
            if (this.destroyed) {
                return;
            }
            this.destroyed = true;
            if (this.stage && typeof this.stage.destroy === 'function') {
                this.stage.destroy(reason || 'destroy');
            }
            this.sessionId = '';
            this.pendingPolish = null;
        }
    }

    window.YuiGuideAvatarStage = {
        create: function (options) {
            return new YuiGuideAvatarStageController(options || {});
        },
        profile: YUI_PROFILE,
        sequences: YUI_SEQUENCES
    };
})();
