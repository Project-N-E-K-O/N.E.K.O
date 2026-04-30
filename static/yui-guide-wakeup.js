(function () {
    'use strict';

    if (window.YuiGuideWakeup) {
        return;
    }

    const DEFAULT_DURATION_MS = 3800;
    const REDUCED_MOTION_DURATION_MS = 520;
    const LIVE2D_READY_WAIT_MS = 900;
    const LIVE2D_HANDOFF_MS = 620;
    const LIVE2D_REDUCED_HANDOFF_MS = 160;
    const LIVE2D_PARAMS = Object.freeze({
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
        // Existing yui_default parameters from yui_default.cdi3.json:
        // Param75: 【开关】右-挥手
        // Param90: 右小臂-动画
        // Param92: 右手-动画
        // Param95: 右手-摆手动画
        yuiRightWaveSwitch: 'Param75',
        yuiRightForearmAnim: 'Param90',
        yuiRightHandAnim: 'Param92',
        yuiRightHandWave: 'Param95'
    });

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

    function clamp(value, min, max) {
        const number = Number(value);
        if (!Number.isFinite(number)) {
            return min;
        }
        return Math.min(max, Math.max(min, number));
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

    function normalizeDuration(value, fallback) {
        return Number.isFinite(value) && value >= 0 ? value : fallback;
    }

    function getLive2DManager() {
        return window.live2dManager || null;
    }

    function getCurrentLive2DModel(manager) {
        if (!manager) {
            return null;
        }
        if (typeof manager.getCurrentModel === 'function') {
            return manager.getCurrentModel();
        }
        return manager.currentModel || null;
    }

    function getLive2DContext() {
        const manager = getLive2DManager();
        const model = getCurrentLive2DModel(manager);
        const coreModel = model && model.internalModel && model.internalModel.coreModel;
        if (!manager || !model || model.destroyed || !coreModel) {
            return null;
        }
        return {
            manager: manager,
            model: model,
            coreModel: coreModel,
            ticker: manager.pixi_app && manager.pixi_app.ticker
        };
    }

    function hasParam(coreModel, id) {
        if (!coreModel || !id || typeof coreModel.getParameterIndex !== 'function') {
            return false;
        }
        try {
            return coreModel.getParameterIndex(id) >= 0;
        } catch (_) {
            return false;
        }
    }

    function readParamMeta(coreModel, id) {
        if (!hasParam(coreModel, id)) {
            return null;
        }
        try {
            const index = coreModel.getParameterIndex(id);
            if (index < 0) {
                return null;
            }
            const current = coreModel.getParameterValueByIndex(index);
            let min = Number.NEGATIVE_INFINITY;
            let max = Number.POSITIVE_INFINITY;
            let defaultValue = current;
            try {
                if (typeof coreModel.getParameterMinimumValueByIndex === 'function') {
                    min = coreModel.getParameterMinimumValueByIndex(index);
                }
            } catch (_) {}
            try {
                if (typeof coreModel.getParameterMaximumValueByIndex === 'function') {
                    max = coreModel.getParameterMaximumValueByIndex(index);
                }
            } catch (_) {}
            try {
                if (typeof coreModel.getParameterDefaultValueByIndex === 'function') {
                    defaultValue = coreModel.getParameterDefaultValueByIndex(index);
                }
            } catch (_) {}
            if (!Number.isFinite(min)) {
                min = id.indexOf('EyeBall') >= 0 ? -1 : (id.indexOf('Eye') >= 0 ? 0 : -30);
            }
            if (!Number.isFinite(max)) {
                max = id.indexOf('EyeBall') >= 0 ? 1 : (id.indexOf('Eye') >= 0 ? 1 : 30);
            }
            return {
                id: id,
                index: index,
                initial: Number.isFinite(current) ? current : defaultValue,
                defaultValue: Number.isFinite(defaultValue) ? defaultValue : 0,
                min: min,
                max: max
            };
        } catch (_) {
            return null;
        }
    }

    function readParam(coreModel, meta) {
        if (!coreModel || !meta) {
            return 0;
        }
        try {
            const value = coreModel.getParameterValueByIndex(meta.index);
            return Number.isFinite(value) ? value : meta.defaultValue;
        } catch (_) {
            return meta.defaultValue;
        }
    }

    function writeParam(coreModel, meta, value) {
        if (!coreModel || !meta || typeof coreModel.setParameterValueByIndex !== 'function') {
            return false;
        }
        try {
            coreModel.setParameterValueByIndex(meta.index, clamp(value, meta.min, meta.max));
            return true;
        } catch (_) {
            return false;
        }
    }

    function lerp(from, to, weight) {
        const t = clamp(weight, 0, 1);
        return from + (to - from) * t;
    }

    function scanLive2DParams(coreModel) {
        const params = {};
        Object.keys(LIVE2D_PARAMS).forEach((key) => {
            const meta = readParamMeta(coreModel, LIVE2D_PARAMS[key]);
            if (meta) {
                params[key] = meta;
            }
        });
        return params;
    }

    function hasAnyWakeupParam(params) {
        return !!(
            params
            && (
                params.eyeLeft
                || params.eyeRight
                || params.angleX
                || params.angleY
                || params.angleZ
                || params.eyeBallX
                || params.eyeBallY
                || params.eyeSmileLeft
                || params.eyeSmileRight
                || params.bodyAngleX
                || params.bodyAngleY
                || params.bodyAngleZ
                || params.yuiRightWaveSwitch
                || params.yuiRightForearmAnim
                || params.yuiRightHandAnim
                || params.yuiRightHandWave
            )
        );
    }

    function waitForLive2DContext(timeoutMs) {
        const immediate = getLive2DContext();
        if (immediate) {
            return Promise.resolve(immediate);
        }

        const maxWait = Math.max(0, Math.round(timeoutMs || 0));
        if (maxWait <= 0) {
            return Promise.resolve(null);
        }

        return new Promise((resolve) => {
            const startedAt = performance.now();
            const check = () => {
                const context = getLive2DContext();
                if (context) {
                    resolve(context);
                    return;
                }
                if (performance.now() - startedAt >= maxWait) {
                    resolve(null);
                    return;
                }
                window.requestAnimationFrame(check);
            };
            window.requestAnimationFrame(check);
        });
    }

    class Live2DWakeupSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.durationMs = normalizeDuration(normalizedOptions.durationMs, DEFAULT_DURATION_MS);
            this.handoffMs = this.reducedMotion ? LIVE2D_REDUCED_HANDOFF_MS : LIVE2D_HANDOFF_MS;
            this.token = normalizedOptions.token || 0;
            this.timelineStartedAt = Number.isFinite(normalizedOptions.timelineStartedAt)
                ? normalizedOptions.timelineStartedAt
                : 0;
            this.params = scanLive2DParams(this.coreModel);
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.previousEyeBlinkSuspended = !!this.manager._suspendEyeBlinkOverride;
            this.poseOverrideSource = 'yui_guide_wakeup_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
        }

        isUsable() {
            return hasAnyWakeupParam(this.params);
        }

        isCurrentModel() {
            if (!this.manager || !this.model || this.model.destroyed || !this.coreModel) {
                return false;
            }
            const current = getCurrentLive2DModel(this.manager);
            return current === this.model
                && current.internalModel
                && current.internalModel.coreModel === this.coreModel;
        }

        start() {
            if (!this.isUsable() || !this.isCurrentModel()) {
                return false;
            }

            this.active = true;
            this.startedAt = this.timelineStartedAt || performance.now();
            this.manager._suspendEyeBlinkOverride = true;
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            this.applyPose(this.computePose(0), 1);
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.ticker.add(this.tick);
            } else {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return true;
        }

        stop(reason) {
            if (!this.active && this.finished) {
                return;
            }
            this.active = false;
            this.finished = true;
            this.result = reason || this.result || 'stopped';
            if (this.ticker && typeof this.ticker.remove === 'function') {
                try { this.ticker.remove(this.tick); } catch (_) {}
            }
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
            if (this.manager) {
                this.manager._suspendEyeBlinkOverride = this.previousEyeBlinkSuspended;
                this.clearTemporaryPoseOverride();
            }
        }

        cancel(reason) {
            if (this.isCurrentModel()) {
                Object.keys(this.params).forEach((key) => {
                    const meta = this.params[key];
                    writeParam(this.coreModel, meta, meta.initial);
                });
            }
            this.stop(reason || 'cancelled');
        }

        installTemporaryPoseOverride() {
            if (!this.manager || typeof this.manager.setTemporaryPoseOverride !== 'function') {
                return false;
            }
            try {
                return this.manager.setTemporaryPoseOverride(this.poseOverrideSource, this.applyTemporaryPose) === true;
            } catch (_) {
                return false;
            }
        }

        clearTemporaryPoseOverride() {
            if (!this.manager || typeof this.manager.clearTemporaryPoseOverride !== 'function') {
                return;
            }
            try {
                this.manager.clearTemporaryPoseOverride(this.poseOverrideSource);
            } catch (_) {}
        }

        applyTemporaryPose(coreModel) {
            if (!this.active || coreModel !== this.coreModel || !this.isCurrentModel()) {
                return;
            }
            const frame = this.getFrameState(performance.now());
            this.applyPose(frame.pose, frame.weight);
        }

        getFrameState(now) {
            const elapsed = Math.max(0, now - this.startedAt);
            const handoffStart = Math.max(0, this.durationMs - this.handoffMs);
            let wakeProgress = handoffStart > 0 ? clamp(elapsed / handoffStart, 0, 1) : 1;
            let weight = 1;
            if (elapsed >= handoffStart) {
                const handoffProgress = this.handoffMs > 0 ? clamp((elapsed - handoffStart) / this.handoffMs, 0, 1) : 1;
                weight = 1 - easeOutCubic(handoffProgress);
            }
            if (this.reducedMotion) {
                wakeProgress = 1;
            }
            return {
                elapsed: elapsed,
                pose: this.computePose(wakeProgress),
                weight: weight
            };
        }

        tick() {
            if (!this.active) {
                return;
            }
            if (!this.isCurrentModel()) {
                this.stop('model_changed');
                return;
            }

            const now = performance.now();
            const frame = this.getFrameState(now);
            if (!this.usesTemporaryPoseOverride) {
                this.applyPose(frame.pose, frame.weight);
            }

            if (frame.elapsed >= this.durationMs) {
                this.stop('played');
                return;
            }
            if (!this.ticker) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
        }

        computePose(progress) {
            const t = easeInOutCubic(progress);
            const holdProgress = clamp(progress / 0.22, 0, 1);
            const wakeProgress = clamp((progress - 0.22) / 0.42, 0, 1);
            const wakeEase = easeOutCubic(wakeProgress);
            const waveProgress = clamp((progress - 0.68) / 0.22, 0, 1);
            const waveOut = 1 - easeOutCubic(clamp((progress - 0.88) / 0.12, 0, 1));
            const waveWeight = Math.sin(waveProgress * Math.PI) * waveOut;
            const waveCycle = Math.sin(waveProgress * Math.PI * 4);
            let eyeOpen = 0;
            if (progress <= 0.22) {
                eyeOpen = 0.02 * holdProgress;
            } else {
                const flutter = Math.sin(wakeProgress * Math.PI * 3) * 0.08 * (1 - wakeProgress);
                eyeOpen = clamp((wakeEase * 0.98) + flutter, 0, 1);
            }

            return {
                eyeOpen: this.reducedMotion ? 1 : eyeOpen,
                angleX: lerp(0, 0, t),
                angleY: this.reducedMotion ? -2 : lerp(-18, 0, t),
                angleZ: this.reducedMotion ? 0 : lerp(-3.2, 0, t),
                eyeBallX: 0,
                eyeBallY: this.reducedMotion ? 0 : lerp(-0.38, 0, t),
                eyeSmile: this.reducedMotion ? 0 : clamp(wakeEase * 0.18, 0, 0.18),
                bodyAngleX: this.reducedMotion ? 0 : lerp(-6.5, 0, t),
                bodyAngleY: this.reducedMotion ? 0 : lerp(-3.2, 0, t),
                bodyAngleZ: this.reducedMotion ? 0 : lerp(3.6, 0, t),
                yuiRightWaveSwitch: this.reducedMotion ? 0 : clamp(waveWeight, 0, 1),
                yuiRightForearmAnim: this.reducedMotion ? 0 : clamp(0.5 + waveCycle * 0.5, 0, 1) * waveWeight,
                yuiRightHandAnim: this.reducedMotion ? 0 : clamp(0.56 + waveCycle * 0.44, 0, 1) * waveWeight,
                yuiRightHandWave: this.reducedMotion ? 0 : clamp(0.5 + waveCycle * 0.5, 0, 1) * waveWeight
            };
        }

        writeWeighted(key, targetValue, weight) {
            const meta = this.params[key];
            if (!meta) {
                return;
            }
            const current = readParam(this.coreModel, meta);
            const blended = lerp(current, targetValue, weight);
            writeParam(this.coreModel, meta, blended);
        }

        applyPose(pose, weight) {
            const w = clamp(weight, 0, 1);
            this.writeWeighted('eyeLeft', pose.eyeOpen, w);
            this.writeWeighted('eyeRight', pose.eyeOpen, w);
            this.writeWeighted('angleX', pose.angleX, w);
            this.writeWeighted('angleY', pose.angleY, w);
            this.writeWeighted('angleZ', pose.angleZ, w);
            this.writeWeighted('eyeBallX', pose.eyeBallX, w);
            this.writeWeighted('eyeBallY', pose.eyeBallY, w);
            this.writeWeighted('eyeSmileLeft', pose.eyeSmile, w);
            this.writeWeighted('eyeSmileRight', pose.eyeSmile, w);
            this.writeWeighted('bodyAngleX', pose.bodyAngleX, w);
            this.writeWeighted('bodyAngleY', pose.bodyAngleY, w);
            this.writeWeighted('bodyAngleZ', pose.bodyAngleZ, w);
            this.writeWeighted('yuiRightWaveSwitch', pose.yuiRightWaveSwitch, w);
            this.writeWeighted('yuiRightForearmAnim', pose.yuiRightForearmAnim, w);
            this.writeWeighted('yuiRightHandAnim', pose.yuiRightHandAnim, w);
            this.writeWeighted('yuiRightHandWave', pose.yuiRightHandWave, w);
        }
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

            const session = new Live2DWakeupSession(context, {
                durationMs: durationMs,
                reducedMotion: reducedMotion,
                timelineStartedAt: timelineStartedAt,
                token: token
            });
            if (!session.isUsable()) {
                return { result: 'fallback', reason: 'live2d_params_missing' };
            }
            if (!session.start()) {
                return { result: 'fallback', reason: 'live2d_session_start_failed' };
            }
            this.live2dSession = session;
            this.live2dSessionToken = token;
            return {
                result: 'played',
                reason: '',
                paramCount: Object.keys(session.params).length
            };
        }

        async run(options) {
            const normalizedOptions = options || {};
            if (isStorageLocationOverlayVisible(this.document)) {
                this.record('wakeup_skipped', { reason: 'storage_overlay_visible' });
                return { result: 'skipped', reason: 'storage_overlay_visible' };
            }

            this.cancel('replaced');
            removeBlockingGuideOverlay(this.document);
            const token = ++this.runToken;
            const reducedMotion = shouldReduceMotion();
            const durationMs = reducedMotion
                ? REDUCED_MOTION_DURATION_MS
                : (Number.isFinite(normalizedOptions.durationMs) ? normalizedOptions.durationMs : DEFAULT_DURATION_MS);
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
                            activeSession.stop('played');
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
                                finish('played', '');
                            }
                        }
                        return live2dResult;
                    })
                    .catch((error) => {
                        console.warn('[YuiGuideWakeup] Live2D 苏醒参数覆盖失败:', error);
                        live2dResult = { result: 'fallback', reason: 'live2d_exception' };
                        if (this.runToken === token && !settled) {
                            finish('played', '');
                        }
                        return live2dResult;
                    });
                this.storageWatchTimer = window.setInterval(() => {
                    if (isStorageLocationOverlayVisible(this.document)) {
                        finish('cancelled', 'storage_overlay_visible');
                    }
                }, 120);
                this.overlayWatchTimer = window.setInterval(() => {
                    removeBlockingGuideOverlay(this.document);
                }, 120);

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
