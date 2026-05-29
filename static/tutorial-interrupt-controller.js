(function () {
    'use strict';

    const DEFAULT_RESISTANCE_VOICE_KEYS = Object.freeze([
        'interrupt_resist_light_1',
        'interrupt_resist_light_3'
    ]);

    function call(callbacks, name, fallbackValue, ...args) {
        const callback = callbacks && callbacks[name];
        if (typeof callback !== 'function') {
            return fallbackValue;
        }

        return callback(...args);
    }

    function getCount(value) {
        return Math.max(0, Math.floor(Number.isFinite(value) ? value : 0));
    }

    class TutorialInterruptController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.overlay = normalizedOptions.overlay || null;
            this.cursor = normalizedOptions.cursor || null;
            this.callbacks = normalizedOptions.callbacks || {};
            this.resistanceVoiceKeys = Array.isArray(normalizedOptions.resistanceVoiceKeys)
                && normalizedOptions.resistanceVoiceKeys.length
                ? normalizedOptions.resistanceVoiceKeys.slice()
                : DEFAULT_RESISTANCE_VOICE_KEYS.slice();
            this.destroyed = false;
            this.lightResistanceActive = false;
        }

        isDestroyed() {
            return !!(this.destroyed || call(this.callbacks, 'isDestroyed', false));
        }

        isStopping() {
            return !!(this.isDestroyed() || call(this.callbacks, 'isStopping', false));
        }

        getResistanceMessage(performance) {
            const voices = call(this.callbacks, 'resolveResistanceVoices', [], performance);
            const count = getCount(call(this.callbacks, 'getInterruptCount', 0));
            const voiceIndex = Math.max(0, Math.min(this.resistanceVoiceKeys.length - 1, count - 1));
            const defaultText = call(this.callbacks, 'resolveBubbleText', '', performance);
            const message = Array.isArray(voices) && voices.length > 0
                ? voices[(Math.max(1, count) - 1) % voices.length]
                : defaultText || '不要拽我啦，还没结束呢！';

            return {
                message: message,
                voiceKey: this.resistanceVoiceKeys[voiceIndex] || '',
                textKey: this.resistanceVoiceKeys[voiceIndex] === 'interrupt_resist_light_3'
                    ? 'tutorial.yuiGuide.lines.interruptResistLight3'
                    : 'tutorial.yuiGuide.lines.interruptResistLight1'
            };
        }

        playLightResistance(x, y, options) {
            if (
                this.lightResistanceActive
                || this.isDestroyed()
                || this.isStopping()
                || call(this.callbacks, 'isResistancePaused', false)
            ) {
                return Promise.resolve();
            }

            const normalizedOptions = options || {};
            const resistanceStep = call(this.callbacks, 'getStep', null, 'interrupt_resist_light');
            if (!resistanceStep) {
                return Promise.resolve();
            }

            this.lightResistanceActive = true;
            const performance = resistanceStep.performance || {};
            const resistanceMessage = this.getResistanceMessage(performance);
            const presentationSnapshot = call(this.callbacks, 'capturePresentationSnapshot', null);

            if (!normalizedOptions.suppressCursorReveal) {
                call(this.callbacks, 'prepareResistanceCursorReveal', null, normalizedOptions);
            }

            call(this.callbacks, 'pauseCurrentSceneForResistance', null);
            call(this.callbacks, 'interruptNarrationForResistance', null);

            if (this.overlay && typeof this.overlay.hideBubble === 'function') {
                this.overlay.hideBubble();
            }

            call(this.callbacks, 'appendGuideChatMessage', null, resistanceMessage.message, {
                textKey: resistanceMessage.textKey,
                voiceKey: resistanceMessage.voiceKey,
                streamPauseWithScene: false
            });
            call(this.callbacks, 'applyGuideEmotion', null, performance.emotion || 'surprised', {
                allowDuringInterrupt: true
            });

            const cursorResistancePromise = normalizedOptions.suppressCursorReaction
                ? Promise.resolve()
                : Promise.resolve(
                    this.cursor && typeof this.cursor.resistTo === 'function'
                        ? this.cursor.resistTo(x, y, {
                            motionDx: normalizedOptions.motionDx,
                            motionDy: normalizedOptions.motionDy
                        })
                        : null
                );
            const interruptPerformancePromise = Promise.resolve(call(
                this.callbacks,
                'runInterruptResistPerformance',
                null,
                {
                    x: x,
                    y: y,
                    voiceKey: resistanceMessage.voiceKey
                }
            )).catch(() => null);
            const voicePromise = Promise.resolve(call(
                this.callbacks,
                'speakResistanceLine',
                null,
                resistanceMessage.message,
                {
                    voiceKey: resistanceMessage.voiceKey
                }
            ));

            return Promise.all([
                voicePromise,
                cursorResistancePromise,
                interruptPerformancePromise
            ]).finally(() => {
                this.lightResistanceActive = false;
                call(this.callbacks, 'resumeCurrentSceneAfterResistance', null);
                if (this.isStopping()) {
                    return;
                }

                const didRestorePresentationSnapshot = call(
                    this.callbacks,
                    'restoreGuidePresentationSnapshot',
                    false,
                    presentationSnapshot
                );
                const narration = call(this.callbacks, 'getActiveNarration', null);
                if (narration && narration.interrupted) {
                    call(this.callbacks, 'scheduleNarrationResume', null, {
                        skipEmotion: didRestorePresentationSnapshot
                    });
                    return;
                }

                call(this.callbacks, 'restoreCurrentScenePresentation', null, {
                    skipEmotion: didRestorePresentationSnapshot
                });
            });
        }

        async abortAsAngryExit(source) {
            if (
                this.isDestroyed()
                || this.isStopping()
                || call(this.callbacks, 'isAngryExitTriggered', false)
            ) {
                return;
            }

            call(this.callbacks, 'recordExperienceMetric', null, 'angry_exit', {
                sceneId: call(this.callbacks, 'getCurrentSceneId', null) || 'interrupt_angry_exit',
                reason: source || 'pointer_interrupt',
                interruptCount: getCount(call(this.callbacks, 'getInterruptCount', 0))
            });
            call(this.callbacks, 'setAngryExitTriggered', null, true);
            call(this.callbacks, 'clearSceneTimers', null);
            call(this.callbacks, 'disableInterrupts', null);
            call(this.callbacks, 'cancelActiveNarration', null);
            call(this.callbacks, 'beginGuideInterruptPresentation', null);

            const angryStep = call(this.callbacks, 'getStep', null, 'interrupt_angry_exit');
            const performance = (angryStep && angryStep.performance) || {};
            const bubbleText = call(this.callbacks, 'resolveBubbleText', '', performance);
            const lastPointerPoint = call(this.callbacks, 'getLastPointerPoint', null);
            const pointerPoint = lastPointerPoint
                && Number.isFinite(lastPointerPoint.x)
                && Number.isFinite(lastPointerPoint.y)
                ? lastPointerPoint
                : null;

            call(this.callbacks, 'setTutorialTakingOver', null, true);
            if (this.overlay && typeof this.overlay.setAngry === 'function') {
                this.overlay.setAngry(true);
            }
            if (this.overlay && typeof this.overlay.hidePluginPreview === 'function') {
                this.overlay.hidePluginPreview();
            }
            if (this.overlay && typeof this.overlay.hideBubble === 'function') {
                this.overlay.hideBubble();
            }

            call(this.callbacks, 'appendGuideChatMessage', null, bubbleText || '人类！你真的很没礼貌喵！', {
                textKey: performance.bubbleTextKey || '',
                voiceKey: performance.voiceKey,
                streamPauseWithScene: false,
                streamAllowDuringAngryExit: true
            });
            call(this.callbacks, 'applyGuideEmotion', null, performance.emotion || 'angry', {
                allowDuringInterrupt: true
            });

            const angryExitPerformancePromise = Promise.resolve(call(
                this.callbacks,
                'runAngryExitPerformance',
                null,
                {
                    x: pointerPoint ? pointerPoint.x : null,
                    y: pointerPoint ? pointerPoint.y : null,
                    voiceKey: performance.voiceKey
                }
            )).catch(() => null);
            await Promise.all([
                Promise.resolve(call(this.callbacks, 'speakGuideLine', null, bubbleText || '', {
                    voiceKey: performance.voiceKey
                })),
                angryExitPerformancePromise
            ]);
            call(this.callbacks, 'notifyPluginDashboardNarrationFinished', null);
            if (this.isDestroyed()) {
                return;
            }

            call(this.callbacks, 'requestTermination', null, source || 'angry_exit', 'angry_exit');
        }

        destroy() {
            this.destroyed = true;
            this.lightResistanceActive = false;
        }
    }

    window.TutorialInterruptController = {
        createController(options) {
            return new TutorialInterruptController(options);
        }
    };
})();
