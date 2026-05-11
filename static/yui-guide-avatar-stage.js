(function () {
    'use strict';

    if (window.YuiGuideAvatarStage) {
        return;
    }

    const DEFAULT_OWNER = 'home-yui-guide';
    const DEFAULT_PRIORITY = 80;
    const DEFAULT_AVATAR_ID = 'main-live2d';
    const DEFAULT_CHARACTER_ID = 'yui';
    const DEFAULT_CAPABILITIES = Object.freeze([
        'params',
        'lookAt'
    ]);
    const WAKEUP_CAPABILITIES = Object.freeze([
        'params',
        'motion',
        'lookAt',
        'expression'
    ]);
    const DEFAULT_WAKEUP_DURATION_MS = 4000;
    const REDUCED_WAKEUP_DURATION_MS = 520;
    const LIVE2D_READY_WAIT_MS = 900;
    const LIVE2D_HANDOFF_MS = 620;
    const LIVE2D_REDUCED_HANDOFF_MS = 160;
    const WAKEUP_EYE_CLOSED_PROGRESS = 0.40;
    const WAKEUP_EYE_OPEN_PROGRESS = 0.40;
    const YUI_WAKEUP_PARAMS = Object.freeze({
        eyeLeft: 'ParamEyeLOpen',
        eyeRight: 'ParamEyeROpen',
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        eyeBallX: 'ParamEyeBallX',
        eyeBallY: 'ParamEyeBallY',
        eyeSmileLeft: 'ParamEyeLSmile',
        eyeSmileRight: 'ParamEyeRSmile',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ',
        yuiRightWaveSwitch: 'Param75',
        yuiRightForearmAnim: 'Param90',
        yuiRightHandAnim: 'Param92',
        yuiRightHandWave: 'Param95'
    });

    const YUI_PROFILE = Object.freeze({
        defaultFrame: Object.freeze({
            x: 0,
            y: 0,
            scale: 1,
            rotate: 0,
            opacity: ''
        }),
        composition: {}
    });

    const YUI_SEQUENCES = Object.freeze({
        stepEnter: Object.freeze([
            Object.freeze({ type: 'lookAt', target: 'spotlight', durationMs: 120 })
        ]),
        speechStart: Object.freeze([
            Object.freeze({ type: 'lookAt', target: 'spotlight', durationMs: 120 })
        ]),
        speechEnd: Object.freeze([]),
        stepExit: Object.freeze([
            Object.freeze({ type: 'clearLookAt' })
        ])
    });

    function toFiniteNumber(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function clamp(value, min, max) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return min;
        }
        return Math.min(max, Math.max(min, number));
    }

    function lerp(from, to, weight) {
        const t = clamp(weight, 0, 1);
        return from + (to - from) * t;
    }

    function easeOutCubic(value) {
        const t = clamp(value, 0, 1);
        return 1 - Math.pow(1 - t, 3);
    }

    function easeInOutCubic(value) {
        const t = clamp(value, 0, 1);
        return t < 0.5
            ? 4 * t * t * t
            : 1 - Math.pow(-2 * t + 2, 3) / 2;
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

    function computeYuiWakeupPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const t = easeInOutCubic(progress);
        const holdProgress = clamp(progress / WAKEUP_EYE_CLOSED_PROGRESS, 0, 1);
        const wakeProgress = clamp((progress - WAKEUP_EYE_CLOSED_PROGRESS) / WAKEUP_EYE_OPEN_PROGRESS, 0, 1);
        const wakeEase = easeOutCubic(wakeProgress);
        const waveProgress = clamp((progress - 0.68) / 0.22, 0, 1);
        const waveOut = 1 - easeOutCubic(clamp((progress - 0.88) / 0.12, 0, 1));
        const waveWeight = Math.sin(waveProgress * Math.PI) * waveOut;
        const waveCycle = Math.sin(waveProgress * Math.PI * 4);
        let eyeOpen = 0;
        if (progress <= WAKEUP_EYE_CLOSED_PROGRESS) {
            eyeOpen = 0.02 * holdProgress;
        } else {
            const flutter = Math.sin(wakeProgress * Math.PI * 3) * 0.08 * (1 - wakeProgress);
            eyeOpen = clamp((wakeEase * 0.98) + flutter, 0, 1);
        }

        return {
            eyeLeft: reducedMotion ? 1 : eyeOpen,
            eyeRight: reducedMotion ? 1 : eyeOpen,
            angleX: 0,
            angleY: reducedMotion ? -2 : lerp(-18, 0, t),
            angleZ: reducedMotion ? 0 : lerp(-3.2, 0, t),
            eyeBallX: 0,
            eyeBallY: reducedMotion ? 0 : lerp(-0.38, 0, t),
            eyeSmileLeft: reducedMotion ? 0 : clamp(wakeEase * 0.18, 0, 0.18),
            eyeSmileRight: reducedMotion ? 0 : clamp(wakeEase * 0.18, 0, 0.18),
            bodyAngleX: reducedMotion ? 0 : lerp(-6.5, 0, t),
            bodyAngleY: reducedMotion ? 0 : lerp(-3.2, 0, t),
            bodyAngleZ: reducedMotion ? 0 : lerp(3.6, 0, t),
            yuiRightWaveSwitch: reducedMotion ? 0 : clamp(waveWeight, 0, 1),
            yuiRightForearmAnim: reducedMotion ? 0 : clamp(0.5 + waveCycle * 0.5, 0, 1) * waveWeight,
            yuiRightHandAnim: reducedMotion ? 0 : clamp(0.56 + waveCycle * 0.44, 0, 1) * waveWeight,
            yuiRightHandWave: reducedMotion ? 0 : clamp(0.5 + waveCycle * 0.5, 0, 1) * waveWeight
        };
    }

    function resolvePointFromTarget(target) {
        if (!target) {
            return null;
        }
        if (typeof target.getBoundingClientRect === 'function') {
            const rect = target.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) {
                return null;
            }
            return {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2
            };
        }
        const left = toFiniteNumber(target.left);
        const top = toFiniteNumber(target.top);
        const width = toFiniteNumber(target.width);
        const height = toFiniteNumber(target.height);
        if (left !== null && top !== null && width !== null && height !== null) {
            return {
                x: left + width / 2,
                y: top + height / 2
            };
        }
        const x = toFiniteNumber(target.x);
        const y = toFiniteNumber(target.y);
        if (x !== null && y !== null) {
            return { x: x, y: y };
        }
        return null;
    }

    function resolveContextTarget(context) {
        const normalized = context || {};
        const targets = normalized.targets && typeof normalized.targets === 'object'
            ? normalized.targets
            : {};
        return targets.actionTarget || targets.spotlight || targets.cursorTarget || targets.anchor || normalized.target || null;
    }

    function buildSequenceTargets(context) {
        const point = resolvePointFromTarget(resolveContextTarget(context));
        if (point) {
            return {
                spotlight: point
            };
        }
        return {};
    }

    function hasSequenceTarget(context) {
        return !!resolvePointFromTarget(resolveContextTarget(context));
    }

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
            this.coordinator = this.resolveCoordinator(this.options);
            this.sessionId = '';
            this.coordinatorSessionId = '';
            this.sessionCapabilities = [];
            this.destroyed = false;
            this.currentStepId = '';
            this.activeSpeechId = '';
            this.owner = String(this.options.owner || DEFAULT_OWNER);
            this.avatarId = String(this.options.avatarId || DEFAULT_AVATAR_ID);
            this.characterId = String(this.options.characterId || DEFAULT_CHARACTER_ID);
            this.capabilities = Array.isArray(this.options.capabilities) && this.options.capabilities.length
                ? this.options.capabilities.slice()
                : DEFAULT_CAPABILITIES.slice();
            this.priority = Number.isFinite(Number(this.options.priority)) ? Number(this.options.priority) : DEFAULT_PRIORITY;
        }

        isAvailable() {
            return !!(this.stage && typeof this.stage.isAvailable === 'function' && this.stage.isAvailable());
        }

        resolveCoordinator(options) {
            const normalized = options || {};
            if (normalized.coordinator) {
                return normalized.coordinator;
            }
            if (window.AvatarPerformance && typeof window.AvatarPerformance.getDefaultCoordinator === 'function') {
                return window.AvatarPerformance.getDefaultCoordinator();
            }
            if (window.AvatarPerformance && typeof window.AvatarPerformance.createNoopCoordinator === 'function') {
                return window.AvatarPerformance.createNoopCoordinator();
            }
            return null;
        }

        isCoordinatorSessionActive() {
            if (!this.coordinator || !this.coordinatorSessionId || typeof this.coordinator.getActiveSession !== 'function') {
                return !!this.coordinatorSessionId;
            }
            return !!this.coordinator.getActiveSession({
                sessionId: this.coordinatorSessionId
            });
        }

        normalizeCapabilities(capabilities) {
            const source = Array.isArray(capabilities) && capabilities.length
                ? capabilities
                : this.capabilities;
            const seen = {};
            const result = [];
            source.forEach((capability) => {
                const normalized = String(capability || '').trim();
                if (!normalized || seen[normalized]) {
                    return;
                }
                seen[normalized] = true;
                result.push(normalized);
            });
            return result.length ? result : DEFAULT_CAPABILITIES.slice();
        }

        hasCapabilities(capabilities) {
            const required = this.normalizeCapabilities(capabilities);
            return required.every((capability) => this.sessionCapabilities.indexOf(capability) >= 0);
        }

        resolveSequenceSteps(sequenceOrName) {
            if (Array.isArray(sequenceOrName)) {
                return sequenceOrName.slice();
            }
            if (sequenceOrName && Array.isArray(sequenceOrName.steps)) {
                return sequenceOrName.steps.slice();
            }
            if (this.stage && typeof this.stage.resolveSequence === 'function') {
                try {
                    return this.stage.resolveSequence(sequenceOrName);
                } catch (_) {}
            }
            if (typeof sequenceOrName === 'string') {
                const sequence = YUI_SEQUENCES[sequenceOrName];
                return Array.isArray(sequence) ? sequence.slice() : [];
            }
            return [];
        }

        resolveSequenceCapabilities(sequenceOrName) {
            const capabilities = [];
            const add = (capability) => {
                if (capabilities.indexOf(capability) < 0) {
                    capabilities.push(capability);
                }
            };
            this.resolveSequenceSteps(sequenceOrName).forEach((step) => {
                const type = String(step && (step.type || step.action) || '').trim();
                if (type === 'frame') {
                    add('frame');
                } else if (type === 'motion' || type === 'playMotion' || type === 'motionWithFallback' || type === 'optionalMotion') {
                    add('motion');
                } else if (type === 'expression' || type === 'applyExpression' || type === 'clearExpression') {
                    add('expression');
                } else if (type === 'emotion' || type === 'setEmotion' || type === 'applyEmotion') {
                    add('motion');
                    add('expression');
                } else if (type === 'param' || type === 'setParam' || type === 'setTemporaryParam' || type === 'clearParams' || type === 'clearTemporaryParams') {
                    add('params');
                } else if (type === 'poseTimeline' || type === 'runPoseTimeline') {
                    add('params');
                    add('motion');
                } else if (type === 'lookAt' || type === 'clearLookAt') {
                    add('params');
                    add('lookAt');
                }
            });
            return this.normalizeCapabilities(capabilities);
        }

        acquireCoordinator(reason, capabilities) {
            if (!this.coordinator || typeof this.coordinator.acquire !== 'function') {
                return null;
            }
            if (this.coordinatorSessionId && this.isCoordinatorSessionActive()) {
                if (!this.hasCapabilities(capabilities)) {
                    this.release('capability-change');
                } else {
                    return {
                        id: this.coordinatorSessionId
                    };
                }
            }
            const lockCapabilities = this.normalizeCapabilities(capabilities);

            const session = this.coordinator.acquire({
                owner: this.owner + ':' + String(reason || 'guide'),
                avatarId: this.avatarId,
                characterId: this.characterId,
                priority: this.priority,
                force: true,
                capabilities: lockCapabilities,
                onRelease: (_session, releaseReason) => {
                    this.releaseStageSession(releaseReason || 'coordinator-release');
                    this.coordinatorSessionId = '';
                    this.sessionCapabilities = [];
                }
            });
            this.coordinatorSessionId = session && session.id ? session.id : '';
            this.sessionCapabilities = this.coordinatorSessionId ? lockCapabilities.slice() : [];
            return session || null;
        }

        ensureSession(reason, capabilities) {
            if (!this.stage || this.destroyed) {
                return '';
            }
            if (this.sessionId && this.stage.isSessionActive(this.sessionId)) {
                if (!this.hasCapabilities(capabilities)) {
                    this.release('capability-change');
                } else {
                    return this.sessionId;
                }
            }
            const lockCapabilities = this.normalizeCapabilities(capabilities);
            const coordinatorSession = this.acquireCoordinator(reason, lockCapabilities);
            if (this.coordinator && !coordinatorSession) {
                return '';
            }
            const session = this.stage.acquire(this.owner + ':' + String(reason || 'guide'), {
                priority: this.priority,
                force: true,
                capabilities: lockCapabilities
            });
            this.sessionId = session && session.id ? session.id : '';
            this.sessionCapabilities = this.sessionId ? lockCapabilities.slice() : [];
            if (!this.sessionId && this.coordinator && this.coordinatorSessionId && typeof this.coordinator.release === 'function') {
                this.coordinator.release(this.coordinatorSessionId, 'stage-acquire-failed');
                this.coordinatorSessionId = '';
                this.sessionCapabilities = [];
            }
            return this.sessionId;
        }

        runSequence(sequenceOrName, context) {
            if (!this.hasSequenceSteps(sequenceOrName)) {
                return Promise.resolve(false);
            }
            const sessionId = this.ensureSession(
                context && context.reason ? context.reason : 'guide-step',
                this.resolveSequenceCapabilities(sequenceOrName)
            );
            if (!sessionId || !this.stage) {
                return Promise.resolve(false);
            }
            return Promise.resolve(this.stage.runSequence(sequenceOrName, {
                sessionId: sessionId,
                targets: buildSequenceTargets(context || {}),
                reducedMotion: context && context.reducedMotion === true
            })).catch((error) => {
                console.warn('[YuiGuideAvatarStage] sequence failed:', error);
                return false;
            });
        }

        hasSequenceSteps(sequenceOrName) {
            if (Array.isArray(sequenceOrName)) {
                return sequenceOrName.length > 0;
            }
            if (sequenceOrName && Array.isArray(sequenceOrName.steps)) {
                return sequenceOrName.steps.length > 0;
            }
            if (this.stage && typeof this.stage.resolveSequence === 'function') {
                try {
                    return this.stage.resolveSequence(sequenceOrName).length > 0;
                } catch (_) {}
            }
            if (typeof sequenceOrName === 'string') {
                const sequence = YUI_SEQUENCES[sequenceOrName];
                return Array.isArray(sequence) && sequence.length > 0;
            }
            return false;
        }

        buildStepEnterSequence(context) {
            return hasSequenceTarget(context) ? YUI_SEQUENCES.stepEnter.slice() : [];
        }

        buildSpeechStartSequence(context) {
            return hasSequenceTarget(context) ? YUI_SEQUENCES.speechStart.slice() : [];
        }

        buildTimelineActionSequence(action, context) {
            const normalizedAction = typeof action === 'string' ? action.trim() : '';
            if (!normalizedAction) {
                return [];
            }
            if (normalizedAction === 'returnControl' || normalizedAction === 'handoffPluginDashboard') {
                return [
                    Object.freeze({ type: 'clearLookAt' })
                ];
            }
            return hasSequenceTarget(context)
                ? [
                    Object.freeze({ type: 'lookAt', target: 'spotlight', durationMs: 120 })
                ]
                : [];
        }

        applyEmotion(emotion, context) {
            if (this.destroyed) {
                return Promise.resolve(false);
            }
            const normalizedEmotion = typeof emotion === 'string' ? emotion.trim() : '';
            if (!normalizedEmotion) {
                return Promise.resolve(false);
            }
            const normalizedContext = Object.assign({}, context || {}, {
                emotion: normalizedEmotion,
                reason: context && context.reason ? context.reason : 'emotion'
            });
            return this.runSequence([
                Object.freeze({ type: 'emotion', emotion: normalizedEmotion })
            ], normalizedContext);
        }

        enterStep(stepId, context) {
            if (this.destroyed || !stepId) {
                return Promise.resolve(false);
            }
            this.currentStepId = String(stepId);
            return this.runSequence(this.buildStepEnterSequence(Object.assign({}, context || {}, {
                stepId: this.currentStepId,
                reason: 'step-enter'
            })), context || {});
        }

        onSpeechStart(stepId, context) {
            if (this.destroyed) {
                return Promise.resolve(false);
            }
            const normalizedContext = Object.assign({}, context || {});
            const speechId = String(normalizedContext.speechId || '');
            if (speechId) {
                this.activeSpeechId = speechId;
            }
            if (stepId) {
                this.currentStepId = String(stepId);
            }
            return this.runSequence(this.buildSpeechStartSequence(Object.assign({}, normalizedContext, {
                stepId: stepId || this.currentStepId,
                reason: 'speech-start'
            })), normalizedContext);
        }

        onSpeechEnd(stepId, context) {
            if (this.destroyed) {
                return Promise.resolve(false);
            }
            const normalizedContext = context || {};
            const speechId = String(normalizedContext.speechId || '');
            if (speechId && this.activeSpeechId && speechId !== this.activeSpeechId) {
                return Promise.resolve(false);
            }
            if (speechId) {
                this.activeSpeechId = '';
            }
            return this.runSequence(YUI_SEQUENCES.speechEnd, Object.assign({}, normalizedContext, {
                stepId: stepId || this.currentStepId,
                reason: 'speech-end'
            }));
        }

        onTimelineAction(stepId, action, context) {
            if (this.destroyed) {
                return Promise.resolve(false);
            }
            if (stepId) {
                this.currentStepId = String(stepId);
            }
            const normalizedContext = Object.assign({}, context || {}, {
                stepId: stepId || this.currentStepId,
                action: typeof action === 'string' ? action.trim() : '',
                reason: 'timeline-action'
            });
            return this.runSequence(this.buildTimelineActionSequence(action, normalizedContext), normalizedContext);
        }

        exitStep(stepId) {
            if (stepId && this.currentStepId && String(stepId) !== this.currentStepId) {
                return Promise.resolve(false);
            }
            this.currentStepId = '';
            this.activeSpeechId = '';
            return this.runSequence(YUI_SEQUENCES.stepExit, {
                reason: 'step-exit'
            }).then(() => {
                return this.release('step-exit');
            });
        }

        buildWakeupSequence(options, resultRef) {
            const normalized = options || {};
            const durationMs = Number.isFinite(Number(normalized.durationMs))
                ? Math.max(0, Math.round(Number(normalized.durationMs)))
                : DEFAULT_WAKEUP_DURATION_MS;
            const onInitialPose = typeof normalized.onInitialPose === 'function'
                ? normalized.onInitialPose
                : null;
            return [
                {
                    type: 'poseTimeline',
                    name: 'yuiWakeup',
                    params: YUI_WAKEUP_PARAMS,
                    durationMs: durationMs,
                    reducedMotionDurationMs: REDUCED_WAKEUP_DURATION_MS,
                    handoffMs: LIVE2D_HANDOFF_MS,
                    reducedHandoffMs: LIVE2D_REDUCED_HANDOFF_MS,
                    readyWaitMs: LIVE2D_READY_WAIT_MS,
                    suspendEyeBlink: true,
                    restoreOnComplete: true,
                    computePose: computeYuiWakeupPose,
                    onInitialPose: () => {
                        revealPreparedTutorialLive2D('wakeup_initial_pose');
                    },
                    onResult: (result) => {
                        if (resultRef) {
                            resultRef.result = result || null;
                        }
                    }
                }
            ];
        }

        async runWakeup(options) {
            if (this.destroyed) {
                return false;
            }
            const normalized = options || {};
            const sessionId = this.ensureSession('wakeup', WAKEUP_CAPABILITIES);
            if (!sessionId || !this.stage) {
                return false;
            }

            const resultRef = {
                result: null
            };
            let completed = false;
            try {
                completed = await this.stage.runSequence(this.buildWakeupSequence(normalized, resultRef), {
                    sessionId: sessionId,
                    reducedMotion: shouldReduceMotion()
                });
            } catch (error) {
                console.warn('[YuiGuideAvatarStage] wakeup takeover failed:', error);
                resultRef.result = {
                    result: 'failed',
                    reason: 'exception'
                };
            } finally {
                this.release('wakeup-complete');
            }

            return {
                handled: true,
                result: resultRef.result || {
                    result: completed ? 'played' : 'failed',
                    reason: completed ? '' : 'pose_timeline_failed'
                }
            };
        }

        releaseStageSession(reason) {
            if (!this.stage || !this.sessionId) {
                this.sessionId = '';
                return false;
            }
            const released = this.stage.release(this.sessionId, reason || 'release');
            this.sessionId = '';
            this.sessionCapabilities = [];
            return released;
        }

        release(reason) {
            const coordinatorSessionId = this.coordinatorSessionId;
            const released = this.releaseStageSession(reason || 'release');
            this.coordinatorSessionId = '';
            this.currentStepId = '';
            this.activeSpeechId = '';
            this.sessionCapabilities = [];
            if (this.coordinator && coordinatorSessionId && typeof this.coordinator.release === 'function') {
                this.coordinator.release(coordinatorSessionId, reason || 'release');
            }
            return released || !!coordinatorSessionId;
        }

        destroy(reason) {
            if (this.destroyed) {
                return;
            }
            this.destroyed = true;
            if (this.coordinatorSessionId) {
                this.release(reason || 'destroy');
            }
            if (this.stage && typeof this.stage.destroy === 'function') {
                this.stage.destroy(reason || 'destroy');
            }
            this.sessionId = '';
            this.coordinatorSessionId = '';
            this.currentStepId = '';
            this.activeSpeechId = '';
            this.sessionCapabilities = [];
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
