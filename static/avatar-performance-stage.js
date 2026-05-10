(function () {
    'use strict';

    if (window.AvatarPerformanceStage) {
        return;
    }

    const DEFAULT_FRAME = Object.freeze({
        x: 0,
        y: 0,
        scale: 1,
        rotate: 0,
        opacity: ''
    });

    function createNoopDriver() {
        return {
            kind: 'noop',
            isAvailable: function () { return false; },
            acquireSession: function () {},
            releaseSession: function () {},
            applyFrame: function () {},
            hasMotion: function () { return false; },
            playMotion: function () { return Promise.resolve(false); },
            resolveParamId: function () { return ''; },
            setParam: function () { return false; },
            captureParams: function () { return {}; },
            restoreParams: function () {},
            lookAt: function () { return false; },
            clearLookAt: function () {}
        };
    }

    function now() {
        return (window.performance && typeof window.performance.now === 'function')
            ? window.performance.now()
            : Date.now();
    }

    function easeOutCubic(t) {
        const clamped = Math.max(0, Math.min(1, t));
        return 1 - Math.pow(1 - clamped, 3);
    }

    function mergeFrame(base, next) {
        return {
            x: Number.isFinite(Number(next.x)) ? Number(next.x) : base.x,
            y: Number.isFinite(Number(next.y)) ? Number(next.y) : base.y,
            scale: Number.isFinite(Number(next.scale)) ? Number(next.scale) : base.scale,
            rotate: Number.isFinite(Number(next.rotate)) ? Number(next.rotate) : base.rotate,
            opacity: next.opacity === '' || next.opacity == null ? base.opacity : next.opacity
        };
    }

    function prefersReducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }

    class AvatarPerformanceStage {
        constructor(options) {
            const normalized = options || {};
            this.driver = normalized.driver || createNoopDriver();
            this.profile = normalized.profile || {};
            this.presets = normalized.presets || {};
            this.sequences = normalized.sequences || this.profile.sequences || {};
            this.logger = normalized.logger || console;
            this.frameState = Object.assign({}, DEFAULT_FRAME, this.profile.defaultFrame || {});
            this.activeSession = null;
            this.sessionSeq = 0;
            this.tweens = new Map();
            this.temporaryParamSnapshots = new Map();
            this.destroyed = false;
            this.reducedMotion = prefersReducedMotion();
        }

        isAvailable() {
            return !!(this.driver && typeof this.driver.isAvailable === 'function' && this.driver.isAvailable());
        }

        acquire(owner, options) {
            if (this.destroyed) {
                return null;
            }

            const normalized = options || {};
            const priority = Number.isFinite(Number(normalized.priority)) ? Number(normalized.priority) : 0;
            if (this.activeSession && !this.activeSession.cancelled) {
                if (priority < this.activeSession.priority && !normalized.force) {
                    return null;
                }
                this.release(this.activeSession.id, 'preempted');
            }

            const session = {
                id: 'avatar-performance-' + (++this.sessionSeq),
                owner: String(owner || 'anonymous'),
                priority: priority,
                cancelled: false
            };
            this.activeSession = session;
            if (this.driver && typeof this.driver.acquireSession === 'function') {
                try {
                    this.driver.acquireSession(session);
                } catch (error) {
                    this.warn('driver acquire failed', error);
                }
            }
            return session;
        }

        release(sessionId, reason) {
            const session = this.activeSession;
            if (!session || session.id !== sessionId) {
                return false;
            }

            this.cancelTweens(sessionId);
            this.clearLookAt({
                sessionId: sessionId,
                reason: reason || 'release'
            });
            session.cancelled = true;
            this.clearTemporaryParams(reason || 'release');
            this.frameState = Object.assign({}, DEFAULT_FRAME, this.profile.defaultFrame || {});
            if (this.driver && typeof this.driver.applyFrame === 'function') {
                try {
                    this.driver.applyFrame(this.frameState, session);
                } catch (error) {
                    this.warn('driver reset frame failed', error);
                }
            }
            if (this.driver && typeof this.driver.releaseSession === 'function') {
                try {
                    this.driver.releaseSession(session, reason || 'release');
                } catch (error) {
                    this.warn('driver release failed', error);
                }
            }
            this.activeSession = null;
            return true;
        }

        getActiveSessionId() {
            return this.activeSession && !this.activeSession.cancelled ? this.activeSession.id : '';
        }

        isSessionActive(sessionId) {
            return !!(this.activeSession && this.activeSession.id === sessionId && !this.activeSession.cancelled && !this.destroyed);
        }

        resolveFrame(frameOrMode) {
            if (typeof frameOrMode === 'string') {
                const composition = this.profile.composition || {};
                return Object.assign({}, composition[frameOrMode] || {});
            }
            if (frameOrMode && typeof frameOrMode === 'object') {
                return Object.assign({}, frameOrMode);
            }
            return {};
        }

        frame(frameOrMode, options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId)) {
                return Promise.resolve(false);
            }

            this.cancelTweens(sessionId);
            const target = mergeFrame(this.frameState, this.resolveFrame(frameOrMode));
            const duration = Math.max(0, Number(normalized.durationMs || normalized.duration || 0));
            if (duration <= 0 || this.reducedMotion) {
                this.frameState = target;
                this.applyFrame(sessionId);
                return Promise.resolve(true);
            }

            return this.tweenFrame(sessionId, target, {
                durationMs: duration,
                easing: typeof normalized.easing === 'function' ? normalized.easing : easeOutCubic
            });
        }

        tweenFrame(sessionId, target, options) {
            const start = Object.assign({}, this.frameState);
            const durationMs = Math.max(1, Number(options.durationMs || 1));
            const easing = typeof options.easing === 'function' ? options.easing : easeOutCubic;

            return new Promise((resolve) => {
                if (!this.isSessionActive(sessionId)) {
                    resolve(false);
                    return;
                }

                const tween = { rafId: 0, done: false };
                const tweenKey = sessionId + ':' + now() + ':' + Math.random();
                this.tweens.set(tweenKey, tween);
                const startedAt = now();
                const step = () => {
                    if (tween.done || !this.isSessionActive(sessionId)) {
                        tween.done = true;
                        this.tweens.delete(tweenKey);
                        resolve(false);
                        return;
                    }

                    const progress = Math.min(1, (now() - startedAt) / durationMs);
                    const eased = easing(progress);
                    this.frameState = {
                        x: start.x + (target.x - start.x) * eased,
                        y: start.y + (target.y - start.y) * eased,
                        scale: start.scale + (target.scale - start.scale) * eased,
                        rotate: start.rotate + (target.rotate - start.rotate) * eased,
                        opacity: target.opacity === '' ? start.opacity : target.opacity
                    };
                    this.applyFrame(sessionId);

                    if (progress >= 1) {
                        tween.done = true;
                        this.tweens.delete(tweenKey);
                        resolve(true);
                        return;
                    }
                    tween.rafId = window.requestAnimationFrame(step);
                };
                tween.rafId = window.requestAnimationFrame(step);
            });
        }

        applyFrame(sessionId) {
            if (!this.isSessionActive(sessionId) || !this.driver || typeof this.driver.applyFrame !== 'function') {
                return;
            }
            try {
                this.driver.applyFrame(this.frameState, this.activeSession);
            } catch (error) {
                this.warn('driver apply frame failed', error);
            }
        }

        playMotion(group, options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId) || !group || !this.driver || typeof this.driver.playMotion !== 'function') {
                return Promise.resolve(false);
            }
            return Promise.resolve(this.driver.playMotion(group, normalized)).catch((error) => {
                this.warn('driver motion failed', error);
                return false;
            });
        }

        runPreset(name, options) {
            const preset = (this.presets && this.presets[name]) || this.getBuiltInPreset(name);
            if (typeof preset !== 'function') {
                return Promise.resolve(false);
            }
            return Promise.resolve(preset(this, options || {}));
        }

        resolveSequence(sequenceOrName) {
            if (Array.isArray(sequenceOrName)) {
                return sequenceOrName.slice();
            }
            if (sequenceOrName && Array.isArray(sequenceOrName.steps)) {
                return sequenceOrName.steps.slice();
            }
            if (typeof sequenceOrName === 'string') {
                const sequence = this.sequences && this.sequences[sequenceOrName];
                if (Array.isArray(sequence)) {
                    return sequence.slice();
                }
                if (sequence && Array.isArray(sequence.steps)) {
                    return sequence.steps.slice();
                }
            }
            return [];
        }

        async runSequence(sequenceOrName, options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId)) {
                return false;
            }

            const steps = this.resolveSequence(sequenceOrName);
            if (!steps.length) {
                return false;
            }

            for (let index = 0; index < steps.length; index += 1) {
                if (!this.isSessionActive(sessionId)) {
                    return false;
                }
                const step = steps[index];
                const completed = await this.runSequenceStep(step, sessionId, normalized);
                if (completed === false && step && step.required === true) {
                    return false;
                }
            }
            return this.isSessionActive(sessionId);
        }

        async runOwnedSequence(owner, sequenceOrName, options) {
            const normalized = options || {};
            const session = this.acquire(owner || 'sequence', {
                priority: normalized.priority,
                force: normalized.force
            });
            if (!session || !session.id) {
                return {
                    completed: false,
                    sessionId: ''
                };
            }

            let completed = false;
            try {
                completed = await this.runSequence(sequenceOrName, Object.assign({}, normalized, {
                    sessionId: session.id
                }));
            } finally {
                if (normalized.releaseOnComplete === true && this.isSessionActive(session.id)) {
                    this.release(session.id, normalized.releaseReason || 'sequence-complete');
                }
            }

            return {
                completed: completed,
                sessionId: session.id
            };
        }

        async runSequenceStep(step, sessionId, sequenceOptions) {
            if (!this.isSessionActive(sessionId)) {
                return false;
            }
            if (!step || typeof step !== 'object') {
                return true;
            }
            if (this.reducedMotion && step.skipWhenReducedMotion === true) {
                return true;
            }

            const type = String(step.type || step.action || '').trim();
            const stepOptions = Object.assign({}, sequenceOptions.stepOptions || {}, step.options || {}, {
                sessionId: sessionId
            });

            const durationScale = Number.isFinite(Number(sequenceOptions.durationScale))
                ? Number(sequenceOptions.durationScale)
                : 1;
            const explicitDurationMs = Number.isFinite(Number(step.durationMs))
                ? Number(step.durationMs)
                : (Number.isFinite(Number(step.duration)) ? Number(step.duration) : null);
            const fallbackDurationMs = Number.isFinite(Number(sequenceOptions.durationMs))
                ? Number(sequenceOptions.durationMs)
                : null;
            const resolvedDurationMs = explicitDurationMs !== null ? explicitDurationMs : fallbackDurationMs;
            if (resolvedDurationMs !== null) {
                stepOptions.durationMs = Math.max(0, Math.round(resolvedDurationMs * durationScale));
            }

            if (sequenceOptions.reducedMotion === true) {
                stepOptions.durationMs = 0;
            }

            if (type === 'frame') {
                return this.frame(step.mode || step.frame || step.target || step.to || {}, stepOptions);
            }
            if (type === 'motion' || type === 'playMotion') {
                return this.playMotion(step.group || step.motion || step.name, stepOptions);
            }
            if (type === 'motionWithFallback' || type === 'optionalMotion') {
                const group = step.group || step.motion || step.name;
                const fallback = step.fallback || step.fallbackSequence || step.sequence;
                const hasMotion = group && this.driver && typeof this.driver.hasMotion === 'function'
                    ? this.driver.hasMotion(group)
                    : false;
                if (hasMotion) {
                    const played = await this.playMotion(group, stepOptions);
                    if (stepOptions.durationMs > 0) {
                        await this.waitForSequenceDelay(sessionId, stepOptions.durationMs);
                    }
                    if (played !== false) {
                        return true;
                    }
                }
                if (fallback) {
                    return this.runSequence(fallback, Object.assign({}, sequenceOptions, {
                        sessionId: sessionId
                    }));
                }
                return false;
            }
            if (type === 'preset' || type === 'runPreset') {
                return this.runPreset(step.name || step.preset, Object.assign({}, stepOptions, step.params || {}));
            }
            if (type === 'param' || type === 'setParam' || type === 'setTemporaryParam') {
                return this.setTemporaryParam(step.key || step.param || step.id, step.value, stepOptions);
            }
            if (type === 'lookAt') {
                const target = this.resolveSequenceTarget(step.target || step.point || step.element || step.key, sequenceOptions);
                const looked = this.lookAt(target, Object.assign({}, stepOptions, step.params || {}));
                if (stepOptions.durationMs > 0) {
                    await this.waitForSequenceDelay(sessionId, stepOptions.durationMs);
                }
                return looked;
            }
            if (type === 'clearLookAt') {
                this.clearLookAt(Object.assign({}, stepOptions, {
                    reason: step.reason || 'sequence'
                }));
                return true;
            }
            if (type === 'clearParams' || type === 'clearTemporaryParams') {
                this.clearTemporaryParams(step.reason || 'sequence');
                return true;
            }
            if (type === 'wait' || type === 'delay') {
                return this.waitForSequenceDelay(sessionId, stepOptions.durationMs || step.ms);
            }
            if (type === 'sequence') {
                return this.runSequence(step.name || step.sequence || step.steps, Object.assign({}, sequenceOptions, {
                    sessionId: sessionId
                }));
            }

            return true;
        }

        resolveSequenceTarget(target, sequenceOptions) {
            if (typeof target === 'function') {
                try {
                    return target(sequenceOptions || {});
                } catch (error) {
                    this.warn('sequence target resolver failed', error);
                    return null;
                }
            }
            if (typeof target === 'string') {
                const targets = sequenceOptions && sequenceOptions.targets ? sequenceOptions.targets : null;
                if (targets && Object.prototype.hasOwnProperty.call(targets, target)) {
                    return targets[target];
                }
            }
            return target || null;
        }

        waitForSequenceDelay(sessionId, durationMs) {
            const delayMs = Math.max(0, Number(durationMs || 0));
            if (delayMs <= 0 || this.reducedMotion) {
                return Promise.resolve(this.isSessionActive(sessionId));
            }
            return new Promise((resolve) => {
                window.setTimeout(() => {
                    resolve(this.isSessionActive(sessionId));
                }, delayMs);
            });
        }

        getBuiltInPreset(name) {
            if (name === 'idleFloat') {
                return (stage, options) => stage.startIdleFloat(options || {});
            }
            if (name === 'pulse') {
                return (stage, options) => stage.runPulse(options || {});
            }
            return null;
        }

        startIdleFloat(options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId) || this.reducedMotion) {
                return false;
            }

            this.cancelPresetTweens(sessionId, 'idleFloat');
            const base = Object.assign({}, this.frameState);
            const amplitudeY = Number.isFinite(Number(normalized.amplitudeY)) ? Number(normalized.amplitudeY) : 5;
            const amplitudeX = Number.isFinite(Number(normalized.amplitudeX)) ? Number(normalized.amplitudeX) : 0;
            const periodMs = Math.max(1200, Number(normalized.periodMs || 5200));
            const phase = Number.isFinite(Number(normalized.phase)) ? Number(normalized.phase) : 0;
            const startedAt = now();
            const tween = { rafId: 0, done: false };
            const tweenKey = sessionId + ':preset:idleFloat:' + startedAt + ':' + Math.random();
            this.tweens.set(tweenKey, tween);

            const step = () => {
                if (tween.done || !this.isSessionActive(sessionId)) {
                    tween.done = true;
                    this.tweens.delete(tweenKey);
                    return;
                }

                const elapsed = now() - startedAt;
                const wave = Math.sin((elapsed / periodMs) * Math.PI * 2 + phase);
                this.frameState = Object.assign({}, base, {
                    x: base.x + amplitudeX * wave,
                    y: base.y + amplitudeY * wave
                });
                this.applyFrame(sessionId);
                tween.rafId = window.requestAnimationFrame(step);
            };
            tween.rafId = window.requestAnimationFrame(step);
            return true;
        }

        async runPulse(options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId) || this.reducedMotion) {
                return false;
            }

            const base = Object.assign({}, this.frameState);
            const scaleAmount = Number.isFinite(Number(normalized.scaleAmount)) ? Number(normalized.scaleAmount) : 0.04;
            const yAmount = Number.isFinite(Number(normalized.yAmount)) ? Number(normalized.yAmount) : -6;
            const durationMs = Math.max(120, Number(normalized.durationMs || 420));
            await this.frame({
                x: base.x,
                y: base.y + yAmount,
                scale: base.scale + scaleAmount,
                rotate: base.rotate,
                opacity: base.opacity
            }, {
                sessionId: sessionId,
                durationMs: Math.round(durationMs * 0.45)
            });
            if (!this.isSessionActive(sessionId)) {
                return false;
            }
            return this.frame(base, {
                sessionId: sessionId,
                durationMs: Math.round(durationMs * 0.55)
            });
        }

        resolveParamId(keyOrId) {
            const params = this.profile.params || {};
            return params[keyOrId] || keyOrId || '';
        }

        setTemporaryParam(keyOrId, value, options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId) || !this.driver || typeof this.driver.setParam !== 'function') {
                return false;
            }

            const paramId = this.resolveParamId(keyOrId);
            if (!paramId) {
                return false;
            }
            const snapshotKey = sessionId + ':' + paramId;
            if (!this.temporaryParamSnapshots.has(snapshotKey) && typeof this.driver.captureParams === 'function') {
                this.temporaryParamSnapshots.set(snapshotKey, this.driver.captureParams([paramId]));
            }
            return this.driver.setParam(paramId, value, normalized);
        }

        clearTemporaryParams(reason) {
            if (!this.driver || typeof this.driver.restoreParams !== 'function') {
                this.temporaryParamSnapshots.clear();
                return;
            }

            this.temporaryParamSnapshots.forEach((snapshot) => {
                try {
                    this.driver.restoreParams(snapshot, reason || 'clear');
                } catch (error) {
                    this.warn('driver restore param failed', error);
                }
            });
            this.temporaryParamSnapshots.clear();
        }

        lookAt(target, options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (!this.isSessionActive(sessionId) || !this.driver || typeof this.driver.lookAt !== 'function') {
                return false;
            }
            return this.driver.lookAt(target, Object.assign({}, this.profile.lookAt || {}, normalized), this.activeSession);
        }

        clearLookAt(options) {
            const normalized = options || {};
            const sessionId = normalized.sessionId || this.getActiveSessionId();
            if (sessionId && !this.isSessionActive(sessionId)) {
                return false;
            }
            if (!this.driver || typeof this.driver.clearLookAt !== 'function') {
                return false;
            }
            this.driver.clearLookAt(normalized);
            return true;
        }

        cancelTweens(sessionId) {
            this.tweens.forEach((tween, key) => {
                if (!sessionId || key.indexOf(sessionId + ':') === 0) {
                    tween.done = true;
                    if (tween.rafId) {
                        window.cancelAnimationFrame(tween.rafId);
                    }
                    this.tweens.delete(key);
                }
            });
        }

        cancelPresetTweens(sessionId, name) {
            const prefix = sessionId + ':preset:' + String(name || '') + ':';
            this.tweens.forEach((tween, key) => {
                if (key.indexOf(prefix) === 0) {
                    tween.done = true;
                    if (tween.rafId) {
                        window.cancelAnimationFrame(tween.rafId);
                    }
                    this.tweens.delete(key);
                }
            });
        }

        destroy(reason) {
            if (this.destroyed) {
                return;
            }
            const sessionId = this.getActiveSessionId();
            if (sessionId) {
                this.release(sessionId, reason || 'destroy');
            }
            this.cancelTweens();
            this.clearTemporaryParams(reason || 'destroy');
            this.destroyed = true;
        }

        warn(message, error) {
            if (this.logger && typeof this.logger.warn === 'function') {
                this.logger.warn('[AvatarPerformanceStage] ' + message, error);
            }
        }
    }

    class Live2DAvatarPerformanceDriver {
        constructor(options) {
            const normalized = options || {};
            this.managerResolver = typeof normalized.managerResolver === 'function'
                ? normalized.managerResolver
                : function () { return window.live2dManager || null; };
            this.containerResolver = typeof normalized.containerResolver === 'function'
                ? normalized.containerResolver
                : function () { return document.getElementById('live2d-container'); };
            this.styleSnapshot = null;
            this.ownerSessionId = '';
            this.lookAtSnapshot = null;
            this.lookAtSource = '';
            this.lookAtSessionId = '';
            this.lookAtParams = Object.assign({
                angleX: 'ParamAngleX',
                angleY: 'ParamAngleY',
                eyeX: 'ParamEyeBallX',
                eyeY: 'ParamEyeBallY'
            }, normalized.lookAtParams || {});
        }

        getManager() {
            return this.managerResolver() || null;
        }

        getModel() {
            const manager = this.getManager();
            if (!manager) {
                return null;
            }
            if (typeof manager.getCurrentModel === 'function') {
                return manager.getCurrentModel();
            }
            return manager.currentModel || null;
        }

        getCoreModel() {
            const model = this.getModel();
            return model && !model.destroyed && model.internalModel
                ? model.internalModel.coreModel || null
                : null;
        }

        getContainer() {
            return this.containerResolver() || null;
        }

        isAvailable() {
            return !!(this.getManager() && this.getModel() && this.getContainer());
        }

        acquireSession(session) {
            const container = this.getContainer();
            this.ownerSessionId = session && session.id ? session.id : '';
            if (!container || this.styleSnapshot) {
                return;
            }
            this.styleSnapshot = {
                transform: container.style.transform || '',
                transition: container.style.transition || '',
                transformOrigin: container.style.transformOrigin || '',
                opacity: container.style.opacity || '',
                willChange: container.style.willChange || ''
            };
            container.style.transformOrigin = container.style.transformOrigin || 'center bottom';
            container.style.willChange = 'transform, opacity';
        }

        releaseSession(session) {
            if (session && this.ownerSessionId && session.id !== this.ownerSessionId) {
                return;
            }
            const container = this.getContainer();
            if (container && this.styleSnapshot) {
                container.style.transform = this.styleSnapshot.transform;
                container.style.transition = this.styleSnapshot.transition;
                container.style.transformOrigin = this.styleSnapshot.transformOrigin;
                container.style.opacity = this.styleSnapshot.opacity;
                container.style.willChange = this.styleSnapshot.willChange;
            }
            this.styleSnapshot = null;
            this.ownerSessionId = '';
            this.clearLookAt({
                sessionId: session && session.id ? session.id : ''
            });
        }

        applyFrame(frame, session) {
            if (session && this.ownerSessionId && session.id !== this.ownerSessionId) {
                return;
            }

            const container = this.getContainer();
            if (!container) {
                return;
            }
            const baseTransform = this.styleSnapshot && this.styleSnapshot.transform
                ? this.styleSnapshot.transform
                : '';
            const transform = [
                baseTransform,
                'translate3d(' + Number(frame.x || 0).toFixed(2) + 'px, ' + Number(frame.y || 0).toFixed(2) + 'px, 0)',
                'scale(' + Number(frame.scale || 1).toFixed(4) + ')',
                'rotate(' + Number(frame.rotate || 0).toFixed(3) + 'deg)'
            ].filter(Boolean).join(' ');
            container.style.transform = transform;
            if (frame.opacity !== '') {
                container.style.opacity = String(frame.opacity);
            }
        }

        playMotion(group) {
            const manager = this.getManager();
            if (!manager || typeof manager.playMotion !== 'function') {
                return Promise.resolve(false);
            }
            return manager.playMotion(group);
        }

        hasMotion(group) {
            const groupName = String(group || '').trim();
            if (!groupName) {
                return false;
            }
            const manager = this.getManager();
            const model = this.getModel();
            const motionManager = model && model.internalModel
                ? model.internalModel.motionManager || null
                : null;

            const sources = [
                manager && manager.fileReferences && manager.fileReferences.Motions,
                manager && manager.emotionMapping && manager.emotionMapping.motions,
                motionManager && motionManager.definitions,
                motionManager && motionManager._definitions,
                motionManager && motionManager.motionGroups,
                motionManager && motionManager._motionGroups
            ].filter(Boolean);

            for (let index = 0; index < sources.length; index += 1) {
                const source = sources[index];
                const motions = source && source[groupName];
                if (Array.isArray(motions) && motions.length > 0) {
                    return true;
                }
            }
            return false;
        }

        captureParams(ids) {
            const coreModel = this.getCoreModel();
            const snapshot = {};
            if (!coreModel || !Array.isArray(ids)) {
                return snapshot;
            }
            ids.forEach((id) => {
                const paramId = String(id || '');
                if (!paramId || typeof coreModel.getParameterIndex !== 'function') {
                    return;
                }
                try {
                    const index = coreModel.getParameterIndex(paramId);
                    if (index >= 0 && typeof coreModel.getParameterValueByIndex === 'function') {
                        snapshot[paramId] = coreModel.getParameterValueByIndex(index);
                    }
                } catch (_) {}
            });
            return snapshot;
        }

        restoreParams(snapshot) {
            if (!snapshot || typeof snapshot !== 'object') {
                return;
            }
            Object.keys(snapshot).forEach((paramId) => {
                this.setParam(paramId, snapshot[paramId]);
            });
        }

        resolvePoint(target) {
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
            if (
                Number.isFinite(Number(target.left))
                && Number.isFinite(Number(target.top))
                && Number.isFinite(Number(target.width))
                && Number.isFinite(Number(target.height))
            ) {
                return {
                    x: Number(target.left) + Number(target.width) / 2,
                    y: Number(target.top) + Number(target.height) / 2
                };
            }
            if (Number.isFinite(Number(target.x)) && Number.isFinite(Number(target.y))) {
                return {
                    x: Number(target.x),
                    y: Number(target.y)
                };
            }
            if (Number.isFinite(Number(target.clientX)) && Number.isFinite(Number(target.clientY))) {
                return {
                    x: Number(target.clientX),
                    y: Number(target.clientY)
                };
            }
            return null;
        }

        resolveLookAtValues(target, options) {
            const point = this.resolvePoint(target);
            if (!point) {
                return null;
            }
            const container = this.getContainer();
            const rect = container && typeof container.getBoundingClientRect === 'function'
                ? container.getBoundingClientRect()
                : null;
            const origin = options && options.origin && Number.isFinite(Number(options.origin.x)) && Number.isFinite(Number(options.origin.y))
                ? {
                    x: Number(options.origin.x),
                    y: Number(options.origin.y)
                }
                : (rect && rect.width > 0 && rect.height > 0
                    ? {
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2
                    }
                    : {
                        x: (window.innerWidth || 1) / 2,
                        y: (window.innerHeight || 1) / 2
                    });
            const normalizeX = Math.max(120, (window.innerWidth || 1) * 0.45);
            const normalizeY = Math.max(120, (window.innerHeight || 1) * 0.45);
            const clamp = function (value, min, max) {
                return Math.max(min, Math.min(max, value));
            };
            const normX = clamp((point.x - origin.x) / normalizeX, -1, 1);
            const normY = clamp((origin.y - point.y) / normalizeY, -1, 1);
            const maxAngleX = Number.isFinite(Number(options.maxAngleX)) ? Number(options.maxAngleX) : 12;
            const maxAngleY = Number.isFinite(Number(options.maxAngleY)) ? Number(options.maxAngleY) : 8;
            const maxEyeX = Number.isFinite(Number(options.maxEyeX)) ? Number(options.maxEyeX) : 0.42;
            const maxEyeY = Number.isFinite(Number(options.maxEyeY)) ? Number(options.maxEyeY) : 0.32;
            const headWeight = Number.isFinite(Number(options.headWeight)) ? Number(options.headWeight) : 1;
            const eyeWeight = Number.isFinite(Number(options.eyeWeight)) ? Number(options.eyeWeight) : 1;
            return {
                angleX: normX * maxAngleX * headWeight,
                angleY: normY * maxAngleY * headWeight,
                eyeX: normX * maxEyeX * eyeWeight,
                eyeY: normY * maxEyeY * eyeWeight
            };
        }

        applyLookAtValues(coreModel, values) {
            if (!coreModel || !values) {
                return false;
            }
            const params = this.lookAtParams || {};
            const writes = [
                [params.angleX, values.angleX],
                [params.angleY, values.angleY],
                [params.eyeX, values.eyeX],
                [params.eyeY, values.eyeY]
            ];
            let wrote = false;
            writes.forEach((entry) => {
                const paramId = entry[0];
                const value = entry[1];
                if (!paramId || !Number.isFinite(Number(value))) {
                    return;
                }
                try {
                    if (typeof coreModel.setParameterValueById === 'function') {
                        coreModel.setParameterValueById(paramId, Number(value));
                        wrote = true;
                        return;
                    }
                    if (typeof coreModel.getParameterIndex === 'function' && typeof coreModel.setParameterValueByIndex === 'function') {
                        const index = coreModel.getParameterIndex(paramId);
                        if (index >= 0) {
                            coreModel.setParameterValueByIndex(index, Number(value));
                            wrote = true;
                        }
                    }
                } catch (_) {}
            });
            return wrote;
        }

        resolveParamId(id) {
            return String(id || '');
        }

        setParam(id, value) {
            const coreModel = this.getCoreModel();
            const paramId = this.resolveParamId(id);
            if (!coreModel || !paramId) {
                return false;
            }
            try {
                if (typeof coreModel.setParameterValueById === 'function') {
                    coreModel.setParameterValueById(paramId, Number(value));
                    return true;
                }
                if (typeof coreModel.getParameterIndex === 'function' && typeof coreModel.setParameterValueByIndex === 'function') {
                    const index = coreModel.getParameterIndex(paramId);
                    if (index >= 0) {
                        coreModel.setParameterValueByIndex(index, Number(value));
                        return true;
                    }
                }
            } catch (_) {}
            return false;
        }

        lookAt(target, options, session) {
            const normalized = options || {};
            if (session && this.ownerSessionId && session.id !== this.ownerSessionId) {
                return false;
            }
            const values = this.resolveLookAtValues(target, normalized);
            if (!values) {
                return false;
            }

            const sessionId = session && session.id ? session.id : (normalized.sessionId || this.ownerSessionId || '');
            const params = this.lookAtParams || {};
            const paramIds = [params.angleX, params.angleY, params.eyeX, params.eyeY].filter(Boolean);
            if (!this.lookAtSnapshot) {
                this.lookAtSnapshot = this.captureParams(paramIds);
            }
            this.lookAtSessionId = sessionId;
            this.lookAtSource = 'avatar-performance-look-at-' + (sessionId || 'session');

            const manager = this.getManager();
            if (manager && typeof manager.setTemporaryPoseOverride === 'function') {
                const source = this.lookAtSource;
                manager.setTemporaryPoseOverride(source, (coreModel) => {
                    this.applyLookAtValues(coreModel, values);
                });
                return true;
            }

            return this.applyLookAtValues(this.getCoreModel(), values);
        }

        clearLookAt(options) {
            const normalized = options || {};
            if (normalized.sessionId && this.lookAtSessionId && normalized.sessionId !== this.lookAtSessionId) {
                return false;
            }
            const manager = this.getManager();
            if (manager && typeof manager.clearTemporaryPoseOverride === 'function' && this.lookAtSource) {
                try {
                    manager.clearTemporaryPoseOverride(this.lookAtSource);
                } catch (_) {}
            }
            if (this.lookAtSnapshot) {
                this.restoreParams(this.lookAtSnapshot);
            }
            this.lookAtSnapshot = null;
            this.lookAtSource = '';
            this.lookAtSessionId = '';
            return true;
        }
    }

    window.AvatarPerformanceStage = {
        create: function (options) {
            return new AvatarPerformanceStage(options || {});
        },
        createLive2DDriver: function (options) {
            return new Live2DAvatarPerformanceDriver(options || {});
        },
        createLive2DStage: function (options) {
            const normalized = options || {};
            const driverOptions = normalized.driverOptions || {};
            const driver = normalized.driver || new Live2DAvatarPerformanceDriver(driverOptions);
            return new AvatarPerformanceStage(Object.assign({}, normalized, {
                driver: driver
            }));
        },
        AvatarPerformanceStage: AvatarPerformanceStage,
        Live2DAvatarPerformanceDriver: Live2DAvatarPerformanceDriver
    };
})();
