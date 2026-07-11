(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.TutorialResistanceControllers = api;
    }
})(typeof window !== 'undefined' ? window : globalThis, function () {
    'use strict';

    const DEFAULT_RESISTANCE_VOICE_KEYS = Object.freeze([
        'interrupt_resist_light_1',
        'interrupt_resist_light_2',
        'interrupt_resist_light_3'
    ]);
    const DEFAULT_RESISTANCE_TEXT_KEYS = Object.freeze([
        'tutorial.yuiGuide.lines.interruptResistLight1',
        'tutorial.yuiGuide.lines.interruptResistLight2',
        'tutorial.yuiGuide.lines.interruptResistLight3'
    ]);
    const DEFAULT_CURSOR_RESISTANCE_DISTANCE = 30;
    const DEFAULT_INTERRUPT_SHAKE_WINDOW_MS = 1100;
    const DEFAULT_INTERRUPT_SHAKE_MIN_DISTANCE = 50;
    const DEFAULT_INTERRUPT_SHAKE_MIN_SPAN_MS = 600;
    const DEFAULT_INTERRUPT_SHAKE_MIN_SUSTAINED_SPEED = 1100;
    const DEFAULT_INTERRUPT_SHAKE_REQUIRED_REVERSALS = 8;
    const DEFAULT_INTERRUPT_SHAKE_REVERSE_DOT_THRESHOLD = 0;
    const DEFAULT_RESISTANCE_LINES = Object.freeze([
        '喵！现在是人家的教学时间，不可以乱动鼠标和键盘啦！乖乖看着人家，好不好嘛？',
        '真是的，又在乱动鼠标和键盘！再不听话的话，人家可真的要生气了喵！',
        '最后警告一次喵！你要是再乱动一下，人家就直接退出新手教程，不教你了！'
    ]);
    const DEFAULT_ANGRY_EXIT_TEXT = '人家已经忍你很久了！既然你就是不肯乖乖听话，那新手教程到此结束，接下来你自己慢慢研究吧，哼！';
    const DEFAULT_ANGRY_EXIT_VOICE_KEY = 'interrupt_angry_exit';

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

    function createInterruptShakeMotion() {
        return {
            lastX: null,
            lastY: null,
            lastAt: 0,
            lastVector: null,
            reversals: []
        };
    }

    function isInterruptShakeReversal(previousVector, currentVector) {
        if (!previousVector || !currentVector) {
            return false;
        }

        const denominator = previousVector.distance * currentVector.distance;
        if (!Number.isFinite(denominator) || denominator <= 0) {
            return false;
        }

        const dot = (
            previousVector.dx * currentVector.dx
            + previousVector.dy * currentVector.dy
        ) / denominator;
        return Number.isFinite(dot) && dot <= DEFAULT_INTERRUPT_SHAKE_REVERSE_DOT_THRESHOLD;
    }

    function isInterruptShakeReady(reversals) {
        if (!Array.isArray(reversals) || reversals.length < DEFAULT_INTERRUPT_SHAKE_REQUIRED_REVERSALS) {
            return false;
        }

        const first = reversals[0];
        const last = reversals[reversals.length - 1];
        const spanMs = Number(last.at) - Number(first.at);
        if (!Number.isFinite(spanMs) || spanMs < DEFAULT_INTERRUPT_SHAKE_MIN_SPAN_MS) {
            return false;
        }

        const totalDistance = reversals.slice(1).reduce((sum, item) => sum + Number(item.distance || 0), 0);
        const sustainedSpeed = totalDistance / Math.max(0.001, spanMs / 1000);
        return sustainedSpeed >= DEFAULT_INTERRUPT_SHAKE_MIN_SUSTAINED_SPEED;
    }

    class ResetInterruptController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.overlay = normalizedOptions.overlay || null;
            this.cursor = normalizedOptions.cursor || null;
            // syncSystemCursorHidden: optional callback for PC builds that need
            // to keep the real cursor hidden during takeover and resistance.
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
            const fallbackMessage = DEFAULT_RESISTANCE_LINES[voiceIndex] || DEFAULT_RESISTANCE_LINES[0] || '';
            const message = Array.isArray(voices) && voices.length > 0
                ? voices[(Math.max(1, count) - 1) % voices.length]
                : defaultText || fallbackMessage || '不要拽我啦，还没结束呢！';

            return {
                message: message,
                voiceKey: this.resistanceVoiceKeys[voiceIndex] || '',
                textKey: DEFAULT_RESISTANCE_TEXT_KEYS[voiceIndex] || DEFAULT_RESISTANCE_TEXT_KEYS[0]
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
            const resistanceStepConfig = call(this.callbacks, 'getStep', null, 'interrupt_resist_light');
            if (!resistanceStepConfig && typeof console !== 'undefined' && typeof console.debug === 'function') {
                console.debug('[YuiGuide] interrupt_resist_light step config missing; using resistance fallback defaults.');
            }
            const resistanceStep = resistanceStepConfig || {};

            this.lightResistanceActive = true;
            const performance = resistanceStep.performance || {};
            const resistanceMessage = this.getResistanceMessage(performance);
            const presentationSnapshot = call(this.callbacks, 'capturePresentationSnapshot', null);

            if (!normalizedOptions.suppressCursorReveal) {
                call(this.callbacks, 'syncSystemCursorHidden', null, true, 'interrupt_resist_light');
                call(this.callbacks, 'suppressResistanceCursorReveal', null, normalizedOptions);
            }

            call(this.callbacks, 'pauseCurrentSceneForResistance', null);
            call(this.callbacks, 'interruptNarrationForResistance', null);

            if (this.overlay && typeof this.overlay.hideBubble === 'function') {
                this.overlay.hideBubble();
            }
            call(this.overlay, 'emphasizeControlBanner', null);

            call(this.callbacks, 'appendGuideChatMessage', null, resistanceMessage.message, {
                textKey: resistanceMessage.textKey,
                voiceKey: resistanceMessage.voiceKey,
                streamPauseWithScene: false
            });
            call(this.callbacks, 'applyGuideEmotion', null, 'angry', {
                allowDuringInterrupt: true
            });

            const cursorResistancePromise = Promise.resolve(
                this.cursor && typeof this.cursor.resistTo === 'function'
                    ? this.cursor.resistTo(x, y, {
                        motionDx: normalizedOptions.motionDx,
                        motionDy: normalizedOptions.motionDy,
                        forcePcOverlay: true
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
                        skipEmotion: didRestorePresentationSnapshot,
                        preserveSpotlights: true
                    });
                    return;
                }

                call(this.callbacks, 'restoreCurrentScenePresentation', null, {
                    skipEmotion: didRestorePresentationSnapshot,
                    preserveSpotlights: true
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
            // 修改原因：生气退出会脱离教程接管态，必须先恢复真实鼠标，避免退出台词播放时系统鼠标仍被隐藏。
            call(this.callbacks, 'syncSystemCursorHidden', null, false, 'interrupt_angry_exit');

            const angryStep = call(this.callbacks, 'getStep', null, 'interrupt_angry_exit') || {};
            const performance = (angryStep && angryStep.performance) || {};
            const bubbleText = call(this.callbacks, 'resolveBubbleText', '', performance);
            const angryExitText = bubbleText || DEFAULT_ANGRY_EXIT_TEXT;
            const angryExitVoiceKey = performance.voiceKey || DEFAULT_ANGRY_EXIT_VOICE_KEY;
            const angryExitNarrationDurationMs = call(
                this.callbacks,
                'getGuideVoiceDurationMs',
                0,
                angryExitVoiceKey
            );
            const lastPointerPoint = call(this.callbacks, 'getLastPointerPoint', null);
            const pointerPoint = lastPointerPoint
                && Number.isFinite(lastPointerPoint.x)
                && Number.isFinite(lastPointerPoint.y)
                ? lastPointerPoint
                : null;

            call(this.callbacks, 'setTutorialTakingOver', null, true, {
                syncSystemCursor: false
            });
            if (this.overlay && typeof this.overlay.setAngry === 'function') {
                this.overlay.setAngry(true);
            }
            if (this.overlay && typeof this.overlay.hidePluginPreview === 'function') {
                this.overlay.hidePluginPreview();
            }
            if (this.overlay && typeof this.overlay.hideBubble === 'function') {
                this.overlay.hideBubble();
            }

            call(this.callbacks, 'appendGuideChatMessage', null, angryExitText, {
                textKey: performance.bubbleTextKey || '',
                voiceKey: angryExitVoiceKey,
                streamPauseWithScene: false,
                streamAllowDuringAngryExit: true
            });
            call(this.callbacks, 'applyGuideEmotion', null, 'angry', {
                allowDuringInterrupt: true
            });

            const angryExitPerformancePromise = Promise.resolve(call(
                this.callbacks,
                'runAngryExitPerformance',
                null,
                {
                    x: pointerPoint ? pointerPoint.x : null,
                    y: pointerPoint ? pointerPoint.y : null,
                    voiceKey: angryExitVoiceKey
                }
            )).catch(() => null);
            await Promise.all([
                Promise.resolve(call(this.callbacks, 'speakGuideLine', null, angryExitText, {
                    voiceKey: angryExitVoiceKey,
                    minDurationMs: Number.isFinite(angryExitNarrationDurationMs)
                        ? angryExitNarrationDurationMs
                        : 0
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

    class ResistanceController {
        constructor(director) {
            this.director = director;
            this.destroyed = false;
            this.lightResistanceActive = false;
            this.resistanceVoiceKeys = DEFAULT_RESISTANCE_VOICE_KEYS.slice();
            this.interruptShakeMotion = createInterruptShakeMotion();
        }

        getInterruptCount() {
            const director = this.director;
            return Math.max(0, Math.floor(Number.isFinite(director.interruptCount) ? director.interruptCount : 0));
        }

        isDestroyed() {
            const director = this.director;
            return !!(this.destroyed || director.destroyed);
        }

        isStopping() {
            const director = this.director;
            return !!(this.isDestroyed() || director.isStopping());
        }

        getResistanceMessage(performance) {
            const director = this.director;
            const voices = director.resolvePerformanceResistanceVoices(performance);
            const count = this.getInterruptCount();
            const voiceIndex = Math.max(0, Math.min(this.resistanceVoiceKeys.length - 1, count - 1));
            const defaultText = director.resolvePerformanceBubbleText(performance);
            const fallbackMessage = DEFAULT_RESISTANCE_LINES[voiceIndex] || DEFAULT_RESISTANCE_LINES[0] || '';
            const message = Array.isArray(voices) && voices.length > 0
                ? voices[(Math.max(1, count) - 1) % voices.length]
                : defaultText || fallbackMessage || '不要拽我啦，还没结束呢！';
            const voiceKey = this.resistanceVoiceKeys[voiceIndex] || '';

            return {
                message: message,
                voiceKey: voiceKey,
                textKey: DEFAULT_RESISTANCE_TEXT_KEYS[voiceIndex] || DEFAULT_RESISTANCE_TEXT_KEYS[0]
            };
        }

        resetInterruptShakeMotion() {
            this.interruptShakeMotion = createInterruptShakeMotion();
            this.director.interruptQualifyingMoveStreak = 0;
        }

        getInterruptShakePoint(event, now) {
            const screenX = Number.isFinite(event.screenX) ? event.screenX : null;
            const screenY = Number.isFinite(event.screenY) ? event.screenY : null;
            const hasScreenPoint = screenX !== null && screenY !== null;
            return {
                x: hasScreenPoint ? screenX : event.clientX,
                y: hasScreenPoint ? screenY : event.clientY,
                at: now
            };
        }

        trackInterruptShakeMotion(point) {
            const motion = this.interruptShakeMotion;
            if (
                !Number.isFinite(motion.lastX)
                || !Number.isFinite(motion.lastY)
            ) {
                motion.lastX = point.x;
                motion.lastY = point.y;
                motion.lastAt = point.at;
                return null;
            }

            const vector = {
                dx: point.x - motion.lastX,
                dy: point.y - motion.lastY
            };
            vector.distance = Math.hypot(vector.dx, vector.dy);
            if (!Number.isFinite(vector.distance) || vector.distance < DEFAULT_INTERRUPT_SHAKE_MIN_DISTANCE) {
                return null;
            }

            const elapsedMs = point.at - motion.lastAt;
            let shakeReady = false;
            if (elapsedMs > 0 && isInterruptShakeReversal(motion.lastVector, vector)) {
                const cutoff = point.at - DEFAULT_INTERRUPT_SHAKE_WINDOW_MS;
                motion.reversals = motion.reversals.filter((item) => item.at >= cutoff);
                motion.reversals.push({
                    at: point.at,
                    distance: vector.distance
                });
                this.director.interruptQualifyingMoveStreak = motion.reversals.length;
                shakeReady = isInterruptShakeReady(motion.reversals);
            }

            motion.lastX = point.x;
            motion.lastY = point.y;
            motion.lastAt = point.at;
            motion.lastVector = vector;
            return shakeReady ? vector : null;
        }

        recordPointerDown(event) {
            const director = this.director;
            if (!event || event.isTrusted === false) {
                return;
            }

            const x = Number.isFinite(event.clientX) ? event.clientX : null;
            const y = Number.isFinite(event.clientY) ? event.clientY : null;
            if (x === null || y === null) {
                return;
            }

            const now = Date.now();
            director.lastPointerPoint = {
                x: x,
                y: y,
                t: now,
                speed: 0
            };
            this.resetInterruptShakeMotion();
        }

        handleInterrupt(event) {
            const director = this.director;
            if (
                director.destroyed
                || director.angryExitTriggered
                || director.scenePausedForResistance
                || this.lightResistanceActive
                || !director.interruptsEnabled
                || !event
                || event.isTrusted === false
            ) {
                return;
            }

            const step = director.currentStep;
            const performance = (step && step.performance) || {};
            const interrupts = (step && step.interrupts) || {};
            if (performance.interruptible === false) {
                return;
            }

            const x = Number.isFinite(event.clientX) ? event.clientX : null;
            const y = Number.isFinite(event.clientY) ? event.clientY : null;
            if (x === null || y === null) {
                return;
            }

            if (!director.shouldAllowInterruptDuringCurrentScene()) {
                return;
            }

            const shouldRequireDocumentFocus = !(
                director.platformCapabilities
                && director.platformCapabilities.windowBoundsSource === 'electron-window-bounds'
            );
            if (
                shouldRequireDocumentFocus
                && director.page === 'home'
                && typeof document !== 'undefined'
                && typeof document.hasFocus === 'function'
                && !document.hasFocus()
            ) {
                return;
            }

            let sampleDx = null;
            let sampleDy = null;
            if (event.type === 'mousemove') {
                const movementX = Number.isFinite(event.movementX) ? event.movementX : null;
                const movementY = Number.isFinite(event.movementY) ? event.movementY : null;
                if (movementX !== null && movementY !== null) {
                    const movementDistance = Math.hypot(movementX, movementY);
                    if (movementDistance <= 0) {
                        return;
                    }
                    sampleDx = movementX;
                    sampleDy = movementY;
                }
            }

            const now = Date.now();
            const shakePoint = this.getInterruptShakePoint(event, now);
            const previousPoint = director.lastPointerPoint;
            if (!previousPoint || !Number.isFinite(previousPoint.t)) {
                director.lastPointerPoint = {
                    x: x,
                    y: y,
                    t: now,
                    speed: 0
                };
                if (sampleDx !== null || sampleDy !== null) {
                    const initialDx = sampleDx === null ? 0 : sampleDx;
                    const initialDy = sampleDy === null ? 0 : sampleDy;
                    const initialDistance = Math.hypot(initialDx, initialDy);
                    director.noteUserCursorRevealSuppressionAttempt(initialDistance, now);
                    if (initialDistance > DEFAULT_CURSOR_RESISTANCE_DISTANCE) {
                        director.playCursorResistanceToUserMotion(x, y, initialDistance, initialDx, initialDy);
                    }
                }
                this.resetInterruptShakeMotion();
                this.trackInterruptShakeMotion(shakePoint);
                return;
            }

            const dx = sampleDx === null ? x - previousPoint.x : sampleDx;
            const dy = sampleDy === null ? y - previousPoint.y : sampleDy;
            const distance = Math.hypot(dx, dy);
            const dt = Math.max(1, now - previousPoint.t);
            const speed = distance / dt;

            director.lastPointerPoint = {
                x: x,
                y: y,
                t: now,
                speed: speed
            };

            director.noteUserCursorRevealSuppressionAttempt(distance, now);
            if (distance > DEFAULT_CURSOR_RESISTANCE_DISTANCE) {
                director.playCursorResistanceToUserMotion(x, y, distance, dx, dy);
            }

            const interruptMotion = this.trackInterruptShakeMotion(shakePoint);
            if (!interruptMotion) {
                return;
            }
            this.resetInterruptShakeMotion();

            const throttleMs = Number.isFinite(interrupts.throttleMs) ? interrupts.throttleMs : 500;
            if (now - director.lastInterruptAt < throttleMs) {
                return;
            }
            director.lastInterruptAt = now;

            director.interruptCount += 1;
            const threshold = Number.isFinite(interrupts.threshold) ? interrupts.threshold : 3;

            if (director.interruptCount >= threshold) {
                director.lastPointerPoint = null;
                director.abortAsAngryExit('pointer_interrupt');
                return;
            }

            // 修改原因：轻对抗计数成立时先刷新 2 秒真实鼠标显示；
            // 即使上一段台词演出还在 active 保护内，第二次触发也不能只剩第一次显示的尾巴。
            const cursorRevealAlreadyRequested = typeof director.revealSystemCursorTemporarily === 'function';
            if (cursorRevealAlreadyRequested) {
                director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');
            }
            director.lastPointerPoint = null;
            director.playLightResistance(x, y, {
                motionDx: interruptMotion.dx,
                motionDy: interruptMotion.dy,
                forceSystemCursorReveal: true,
                suppressCursorReveal: true,
                cursorRevealAlreadyRequested: cursorRevealAlreadyRequested
            });
        }

        playLightResistance(x, y, options) {
            const director = this.director;
            if (
                this.lightResistanceActive
                || this.isDestroyed()
                || this.isStopping()
                || director.scenePausedForResistance
            ) {
                return Promise.resolve();
            }

            const normalizedOptions = options || {};
            const resistanceStep = director.getStep('interrupt_resist_light') || {};

            this.lightResistanceActive = true;
            const performance = resistanceStep.performance || {};
            const resistanceMessage = this.getResistanceMessage(performance);
            const presentationSnapshot = director.captureCurrentGuidePresentationSnapshot();

            if (!normalizedOptions.suppressCursorReveal) {
                director.suppressResistanceCursorReveal(normalizedOptions);
            }
            // 修改原因：正常进入轻对抗演出时仍由这里兜底显示真实鼠标；
            // 已在触发点刷新过的场景跳过，避免同一次轻对抗重复发送两次 PC 临时显示事件。
            if (
                !normalizedOptions.cursorRevealAlreadyRequested
                && typeof director.revealSystemCursorTemporarily === 'function'
            ) {
                director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');
            }

            director.pauseCurrentSceneForResistance();
            director.interruptNarrationForResistance();

            if (director.overlay && typeof director.overlay.hideBubble === 'function') {
                director.overlay.hideBubble();
            }
            if (director.overlay && typeof director.overlay.emphasizeControlBanner === 'function') {
                director.overlay.emphasizeControlBanner();
            }

            director.appendGuideChatMessage(resistanceMessage.message, {
                textKey: resistanceMessage.textKey,
                voiceKey: resistanceMessage.voiceKey,
                streamPauseWithScene: false
            });
            director.applyGuideEmotion('angry', {
                allowDuringInterrupt: true
            });

            const cursorResistancePromise = Promise.resolve(
                director.cursor && typeof director.cursor.resistTo === 'function'
                    ? director.cursor.resistTo(x, y, {
                        motionDx: normalizedOptions.motionDx,
                        motionDy: normalizedOptions.motionDy,
                        forcePcOverlay: true
                    })
                    : null
            );
            const interruptPerformancePromise = Promise.resolve(director.runInterruptResistPerformance({
                x: x,
                y: y,
                voiceKey: resistanceMessage.voiceKey
            })).catch(() => null);
            const voicePromise = Promise.resolve(director.voiceQueue.speak(resistanceMessage.message, {
                voiceKey: resistanceMessage.voiceKey
            }));

            return Promise.all([
                voicePromise,
                cursorResistancePromise,
                interruptPerformancePromise
            ]).finally(() => {
                this.lightResistanceActive = false;
                director.resumeCurrentSceneAfterResistance();
                if (this.isStopping()) {
                    return;
                }

                const didRestorePresentationSnapshot = director.restoreGuidePresentationSnapshot(presentationSnapshot);
                const narration = director.activeNarration;
                if (narration && narration.interrupted) {
                    director.scheduleNarrationResume({
                        skipEmotion: didRestorePresentationSnapshot,
                        preserveSpotlights: true
                    });
                    return;
                }

                director.restoreCurrentScenePresentation({
                    skipEmotion: didRestorePresentationSnapshot,
                    preserveSpotlights: true
                });
            });
        }

        abortAsAngryExit(source) {
            const director = this.director;
            if (
                this.isDestroyed()
                || this.isStopping()
                || director.angryExitTriggered
            ) {
                return Promise.resolve();
            }

            director.recordExperienceMetric('angry_exit', {
                sceneId: director.currentSceneId || 'interrupt_angry_exit',
                reason: source || 'pointer_interrupt',
                interruptCount: this.getInterruptCount()
            });
            director.angryExitTriggered = true;
            director.clearSceneTimers();
            director.disableInterrupts();
            director.cancelActiveNarration();
            director.beginGuideInterruptPresentation();
            // 修改原因：生气退出会脱离教程接管态，必须先取消页面侧轻对抗临时显示 timer；
            // 随后的 interrupt_angry_exit 可见性消息也是 PC 侧清理临时显示 timer 的跨端契约。
            if (director.resistanceCursorTimer) {
                window.clearTimeout(director.resistanceCursorTimer);
                director.resistanceCursorTimer = null;
            }
            this.syncSystemCursorHidden(false, 'interrupt_angry_exit');

            const angryStep = director.getStep('interrupt_angry_exit') || {};
            const performance = (angryStep && angryStep.performance) || {};
            const bubbleText = director.resolvePerformanceBubbleText(performance);
            const angryExitText = bubbleText || DEFAULT_ANGRY_EXIT_TEXT;
            const angryExitVoiceKey = performance.voiceKey || DEFAULT_ANGRY_EXIT_VOICE_KEY;
            const angryExitNarrationDurationMs = director.getGuideVoiceDurationMs(
                angryExitVoiceKey
            );
            const lastPointerPoint = director.lastPointerPoint;
            const pointerPoint = lastPointerPoint
                && Number.isFinite(lastPointerPoint.x)
                && Number.isFinite(lastPointerPoint.y)
                ? lastPointerPoint
                : null;

            director.setTutorialTakingOver(true, {
                syncSystemCursor: false
            });
            if (director.overlay && typeof director.overlay.setAngry === 'function') {
                director.overlay.setAngry(true);
            }
            if (director.overlay && typeof director.overlay.hidePluginPreview === 'function') {
                director.overlay.hidePluginPreview();
            }
            if (director.overlay && typeof director.overlay.hideBubble === 'function') {
                director.overlay.hideBubble();
            }

            director.appendGuideChatMessage(angryExitText, {
                textKey: performance.bubbleTextKey || '',
                voiceKey: angryExitVoiceKey,
                streamPauseWithScene: false,
                streamAllowDuringAngryExit: true
            });
            director.applyGuideEmotion('angry', {
                allowDuringInterrupt: true
            });

            const angryExitPerformancePromise = Promise.resolve(director.runAngryExitPerformance({
                x: pointerPoint ? pointerPoint.x : null,
                y: pointerPoint ? pointerPoint.y : null,
                voiceKey: angryExitVoiceKey
            })).catch(() => null);

            const angryExitPresentationPromise = Promise.all([
                Promise.resolve(director.speakGuideLine(angryExitText, {
                    voiceKey: angryExitVoiceKey,
                    minDurationMs: Number.isFinite(angryExitNarrationDurationMs)
                        ? angryExitNarrationDurationMs
                        : 0
                })),
                angryExitPerformancePromise
            ]).then(() => {
                director.notifyPluginDashboardNarrationFinished();
                if (this.isDestroyed()) {
                    return;
                }

                director.requestTermination(source || 'angry_exit', 'angry_exit');
            }).finally(() => {
                if (director.angryExitPresentationPromise === angryExitPresentationPromise) {
                    director.angryExitPresentationPromise = null;
                }
            });
            director.angryExitPresentationPromise = angryExitPresentationPromise;
            return angryExitPresentationPromise;
        }

        destroy() {
            this.destroyed = true;
            this.lightResistanceActive = false;
            this.resetInterruptShakeMotion();
        }

        syncSystemCursorHidden(hidden, reason) {
            const director = this.director;
            if (director && typeof director.syncSystemCursorHidden === 'function') {
                director.syncSystemCursorHidden(hidden, reason);
            }
        }
    }

    class SidebarPauseController {
        constructor(options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || (typeof document !== 'undefined' ? document : null);
            this.trackedPanels = new Set();
            this.pausedPanelStyles = new Map();
        }

        trackPanel(panel) {
            if (panel && panel.nodeType === 1) {
                this.trackedPanels.add(panel);
            }
            return panel || null;
        }

        getTrackedPanels() {
            const panels = new Set();
            this.trackedPanels.forEach((panel) => {
                if (panel && panel.isConnected !== false) {
                    panels.add(panel);
                }
            });
            if (this.document && typeof this.document.querySelectorAll === 'function') {
                this.document.querySelectorAll([
                    '[data-neko-sidepanel-type="chat-settings"]',
                    '[data-neko-sidepanel-type="animation-settings"]',
                    '[data-neko-sidepanel-type="character-settings"]',
                    '[data-neko-sidepanel-type="interval-proactive-vision"]'
                ].join(',')).forEach((panel) => panels.add(panel));
            }
            return Array.from(panels);
        }

        pause() {
            this.getTrackedPanels().forEach((panel) => {
                if (!panel || this.pausedPanelStyles.has(panel)) {
                    return;
                }
                this.pausedPanelStyles.set(panel, {
                    transition: panel.style.transition,
                    animationPlayState: panel.style.animationPlayState
                });
                panel.setAttribute('data-yui-guide-sidebar-paused', 'true');
                panel.style.transition = 'none';
                panel.style.animationPlayState = 'paused';
            });
        }

        resume() {
            this.pausedPanelStyles.forEach((style, panel) => {
                if (!panel) {
                    return;
                }
                if (panel.getAttribute && panel.getAttribute('data-yui-guide-sidebar-paused') === 'true') {
                    panel.removeAttribute('data-yui-guide-sidebar-paused');
                }
                panel.style.transition = style.transition || '';
                panel.style.animationPlayState = style.animationPlayState || '';
            });
            this.pausedPanelStyles.clear();
        }

        getPauseToken() {
            return {
                pause: () => this.pause(),
                resume: () => this.resume()
            };
        }
    }

    class PauseCoordinator {
        constructor(options) {
            const normalizedOptions = options || {};
            this.pauseTokens = new Map();
            this.getResistancePaused = typeof normalizedOptions.getResistancePaused === 'function'
                ? normalizedOptions.getResistancePaused
                : () => false;
            this.setResistancePaused = typeof normalizedOptions.setResistancePaused === 'function'
                ? normalizedOptions.setResistancePaused
                : () => {};
            this.setPausedAt = typeof normalizedOptions.setPausedAt === 'function'
                ? normalizedOptions.setPausedAt
                : () => {};
            this.beginInterruptPresentation = typeof normalizedOptions.beginInterruptPresentation === 'function'
                ? normalizedOptions.beginInterruptPresentation
                : () => {};
            this.endInterruptPresentation = typeof normalizedOptions.endInterruptPresentation === 'function'
                ? normalizedOptions.endInterruptPresentation
                : () => {};
            this.takeScenePauseResolvers = typeof normalizedOptions.takeScenePauseResolvers === 'function'
                ? normalizedOptions.takeScenePauseResolvers
                : () => [];
            this.registerPauseToken('cursor', normalizedOptions.cursor);
            this.registerPauseToken('spotlight', normalizedOptions.spotlightController);
        }

        registerPauseToken(name, token) {
            const normalizedName = typeof name === 'string' ? name.trim() : '';
            if (
                !normalizedName
                || !token
                || (
                    typeof token.pause !== 'function'
                    && typeof token.resume !== 'function'
                )
            ) {
                return () => {};
            }

            this.pauseTokens.set(normalizedName, token);
            return () => {
                if (this.pauseTokens.get(normalizedName) === token) {
                    this.pauseTokens.delete(normalizedName);
                }
            };
        }

        pauseForResistance() {
            if (this.getResistancePaused()) {
                return;
            }
            this.setResistancePaused(true);
            this.setPausedAt(Date.now());
            this.pauseTokens.forEach((token) => {
                if (token && typeof token.pause === 'function') {
                    try {
                        token.pause();
                    } catch (_) {}
                }
            });
            this.beginInterruptPresentation();
        }

        resumeAfterResistance() {
            if (!this.getResistancePaused()) {
                return;
            }
            this.setResistancePaused(false);
            this.setPausedAt(0);
            this.pauseTokens.forEach((token) => {
                if (token && typeof token.resume === 'function') {
                    try {
                        token.resume();
                    } catch (_) {}
                }
            });
            this.endInterruptPresentation();
            const resolvers = this.takeScenePauseResolvers();
            resolvers.forEach((resolve) => {
                try {
                    resolve();
                } catch (_) {}
            });
        }
    }

    class TutorialTerminationRouter {
        constructor(director) {
            this.director = director;
        }

        requestTermination(reason, tutorialReason) {
            const director = this.director;
            if (director.destroyed || director.terminationRequested) {
                return;
            }

            director.terminationRequested = true;
            director.clearPendingGuideMessageAction();
            const finalReason = tutorialReason || reason || 'skip';
            director.setGuideChatInputLocked(false, 'avatar-floating-guide-' + finalReason);
            director.notifyPluginDashboardTerminationRequested(finalReason);
            if (typeof director.recordAvatarFloatingGuideRoundEndForTermination === 'function') {
                director.recordAvatarFloatingGuideRoundEndForTermination(finalReason);
            }
            director.closePluginDashboardWindowIfCreatedByGuide('终止请求').catch((error) => {
                console.warn('[YuiGuide] 终止请求时关闭插件面板失败:', error);
            });
            director.cancelActiveNarration();
            director.resumeCurrentSceneAfterResistance();
            if (director.tutorialManager && typeof director.tutorialManager.requestTutorialEnd === 'function') {
                return director.tutorialManager.requestTutorialEnd(finalReason);
            }
            if (director.tutorialManager && typeof director.tutorialManager.requestTutorialDestroy === 'function') {
                return director.tutorialManager.requestTutorialDestroy(finalReason);
            } else {
                director.destroy();
            }
        }

        skip(reason, tutorialReason) {
            const director = this.director;
            director.recordExperienceMetric('skip', {
                reason: reason || 'skip',
                tutorialReason: tutorialReason || reason || 'skip'
            });
            return this.requestTermination(reason, tutorialReason);
        }

        async handlePluginDashboardSkipRequest(data) {
            const director = this.director;
            const detail = data && data.detail && typeof data.detail === 'object'
                ? data.detail
                : null;
            const forwardResult = await director.forwardPluginDashboardSkipRequestToButton(detail);
            if (forwardResult === 'forwarded') {
                return;
            }

            if (forwardResult === 'rejected' && !director.isPluginDashboardDirectSkipRequest(data)) {
                return;
            }

            if (director.tutorialManager && typeof director.tutorialManager.handleTutorialSkipRequest === 'function') {
                await director.tutorialManager.handleTutorialSkipRequest();
            } else {
                await director.skip('skip', 'skip');
            }
        }
    }

    return {
        ResetInterruptController,
        createResetInterruptController(options) {
            return new ResetInterruptController(options);
        },
        ResistanceController,
        SidebarPauseController,
        PauseCoordinator,
        TutorialTerminationRouter
    };
});
