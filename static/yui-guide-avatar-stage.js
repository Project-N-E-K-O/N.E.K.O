(function () {
    'use strict';

    if (window.YuiGuideAvatarStage) {
        return;
    }

    const DEFAULT_DURATION_MS = 4000;
    const REDUCED_MOTION_DURATION_MS = 520;
    const LIVE2D_READY_WAIT_MS = 900;
    const LIVE2D_HANDOFF_MS = 620;
    const LIVE2D_REDUCED_HANDOFF_MS = 160;
    const WAKEUP_EYE_CLOSED_PROGRESS = 0.40;
    const WAKEUP_EYE_OPEN_PROGRESS = 0.40;
    const INTRO_GREETING_HUG_DURATION_MS = 7200;
    const INTRO_GREETING_HUG_READY_WAIT_MS = 700;
    const INTRO_GREETING_HUG_APPROACH_MS = 2200;
    const INTRO_GREETING_HUG_RELEASE_MS = 620;
    const INTRO_GREETING_HUG_SETTLE_MS = 1250;
    const INTRO_GREETING_HUG_CLOSE_SCALE = 1.38;
    const INTRO_GREETING_HUG_SHIFT_VIEWPORT_RATIO = 0.52;
    const INTRO_GREETING_HUG_MIN_SHIFT_PX = 320;
    const INTRO_GREETING_HUG_MAX_SHIFT_PX = 760;
    const INTRO_GREETING_HUG_FINAL_SCALE = 1.28;
    const INTRO_GREETING_HUG_FINAL_SHIFT_VIEWPORT_RATIO = 0.46;
    const INTRO_GREETING_HUG_FINAL_MIN_SHIFT_PX = 300;
    const INTRO_GREETING_HUG_FINAL_MAX_SHIFT_PX = 620;
    const INTRO_GIFT_HEART_READY_WAIT_MS = 700;
    const INTRO_GIFT_HEART_DURATION_MS = 2600;
    const INTRO_GIFT_HEART_RELEASE_MS = 420;
    const INTRO_GIFT_HEART_SWAY_PX = 68;
    const INTRO_GIFT_HEART_JUMP_UP_PX = 16;
    const INTRO_GIFT_HEART_JUMP_DOWN_PX = 18;
    const INTRO_GIFT_HEART_HOP_COUNT = 4;
    const INTRO_GIFT_HEART_BODY_SWAY_DEG = 3.4;
    const INTRO_GIFT_HEART_EAR_WIGGLE = 0.32;
    const INTRO_GIFT_HEART_LEG_BEND = 1.15;
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
    const YUI_INTRO_GREETING_HUG_PARAMS = Object.freeze({
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        eyeSmileLeft: 'ParamEyeLSmile',
        eyeSmileRight: 'ParamEyeRSmile',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ',
        browRightY: 'ParamBrowRY',
        browLeftY: 'ParamBrowLY',
        browRightAngle: 'ParamBrowRAngle',
        browLeftAngle: 'ParamBrowLAngle',
        mouthForm: 'ParamMouthForm',
        cheek: 'ParamCheek',
        yuiByExpression: 'Param66',
        yuiHeartSwitch: 'Param74',
        yuiMouthCoverSwitch: 'Param76',
        yuiRightWaveSwitch: 'Param75',
        yuiLeftWaveSwitch: 'Param77',
        yuiLeftMouthCoverAnim: 'Param94',
        yuiRightForearmAnim: 'Param90',
        yuiLeftForearmAnim: 'Param91',
        yuiRightHandAnim: 'Param92',
        yuiLeftHandAnim: 'Param93',
        yuiRightHandWave: 'Param95',
        yuiLeftHandWave: 'Param96'
    });
    const YUI_INTRO_GIFT_HEART_PARAMS = Object.freeze({
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ',
        yuiHeartSwitch: 'Param74',
        yuiMouthCoverSwitch: 'Param76',
        yuiRightWaveSwitch: 'Param75',
        yuiLeftWaveSwitch: 'Param77',
        yuiLeftMouthCoverAnim: 'Param94',
        yuiRightHandWave: 'Param95',
        yuiLeftHandWave: 'Param96',
        yuiLeftEarPerspective: 'Param44',
        yuiLeftEarRotate: 'Param45',
        yuiLeftEarWiggle1: 'Param46',
        yuiLeftEarWiggle2: 'Param47',
        yuiRightEarPerspective: 'Param49',
        yuiRightEarRotate: 'Param50',
        yuiRightEarWiggle1: 'Param51',
        yuiRightEarWiggle2: 'Param52',
        yuiLeftLegShadow1: 'Param_Angle_Rotation_3_ArtMesh274',
        yuiLeftLegShadow2: 'Param_Angle_Rotation_6_ArtMesh274',
        yuiLeftLegShadow3: 'Param_Angle_Rotation_9_ArtMesh274',
        yuiLeftShoeLace1: 'Param_Angle_Rotation_2_ArtMesh268',
        yuiLeftShoeLace2: 'Param_Angle_Rotation_3_ArtMesh269',
        yuiLeftShoeLace3: 'Param_Angle_Rotation_4_ArtMesh270',
        yuiLeftShoeLace4: 'Param_Angle_Rotation_5_ArtMesh271',
        yuiRightShoeLace1: 'Param_Angle_Rotation_2_ArtMesh276',
        yuiRightShoeLace2: 'Param_Angle_Rotation_3_ArtMesh277',
        yuiRightShoeLace3: 'Param_Angle_Rotation_4_ArtMesh278',
        yuiRightShoeLace4: 'Param_Angle_Rotation_5_ArtMesh279'
    });
    const YUI_INTRO_GIFT_HEART_LEG_PARAM_KEYS = Object.freeze([
        'yuiLeftLegShadow1',
        'yuiLeftLegShadow2',
        'yuiLeftLegShadow3',
        'yuiLeftShoeLace1',
        'yuiLeftShoeLace2',
        'yuiLeftShoeLace3',
        'yuiLeftShoeLace4',
        'yuiRightShoeLace1',
        'yuiRightShoeLace2',
        'yuiRightShoeLace3',
        'yuiRightShoeLace4'
    ]);
    const YUI_WAKEUP_POSE_BLEND_FACTORS = Object.freeze({
        eyeLeft: 0.96,
        eyeRight: 0.96,
        angleX: 0.66,
        angleY: 0.66,
        angleZ: 0.66,
        eyeBallX: 0.55,
        eyeBallY: 0.55,
        eyeSmileLeft: 0.88,
        eyeSmileRight: 0.88,
        bodyAngleX: 0.52,
        bodyAngleY: 0.52,
        bodyAngleZ: 0.52,
        yuiRightWaveSwitch: 1,
        yuiRightForearmAnim: 0.94,
        yuiRightHandAnim: 0.94,
        yuiRightHandWave: 0.94
    });
    const YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS = Object.freeze({
        angleX: 0.72,
        angleY: 0.72,
        angleZ: 0.72,
        eyeSmileLeft: 0.84,
        eyeSmileRight: 0.84,
        bodyAngleX: 0.58,
        bodyAngleY: 0.58,
        bodyAngleZ: 0.58,
        browRightY: 0.78,
        browLeftY: 0.78,
        browRightAngle: 0.78,
        browLeftAngle: 0.78,
        mouthForm: 0.8,
        cheek: 0.78,
        yuiByExpression: 1,
        yuiHeartSwitch: 1,
        yuiMouthCoverSwitch: 1,
        yuiRightWaveSwitch: 1,
        yuiLeftWaveSwitch: 1,
        yuiLeftMouthCoverAnim: 1,
        yuiRightForearmAnim: 1,
        yuiLeftForearmAnim: 1,
        yuiRightHandAnim: 1,
        yuiLeftHandAnim: 1,
        yuiRightHandWave: 1,
        yuiLeftHandWave: 1
    });
    const YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS = Object.freeze({
        angleX: 0.58,
        angleY: 0.58,
        angleZ: 0.78,
        bodyAngleX: 0.5,
        bodyAngleY: 0.5,
        bodyAngleZ: 0.72,
        yuiHeartSwitch: 1,
        yuiMouthCoverSwitch: 1,
        yuiRightWaveSwitch: 1,
        yuiLeftWaveSwitch: 1,
        yuiLeftMouthCoverAnim: 1,
        yuiRightHandWave: 1,
        yuiLeftHandWave: 1,
        yuiLeftEarPerspective: 0.78,
        yuiLeftEarRotate: 0.78,
        yuiLeftEarWiggle1: 0.82,
        yuiLeftEarWiggle2: 0.82,
        yuiRightEarPerspective: 0.78,
        yuiRightEarRotate: 0.78,
        yuiRightEarWiggle1: 0.82,
        yuiRightEarWiggle2: 0.82,
        yuiLeftLegShadow1: 0.72,
        yuiLeftLegShadow2: 0.72,
        yuiLeftLegShadow3: 0.72,
        yuiLeftShoeLace1: 0.72,
        yuiLeftShoeLace2: 0.72,
        yuiLeftShoeLace3: 0.72,
        yuiLeftShoeLace4: 0.72,
        yuiRightShoeLace1: 0.72,
        yuiRightShoeLace2: 0.72,
        yuiRightShoeLace3: 0.72,
        yuiRightShoeLace4: 0.72
    });
    let activeIntroGreetingHugSession = null;
    let activeIntroGiftHeartSession = null;

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
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? number : fallback;
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

    function getLive2DContainer(doc) {
        try {
            return (doc || document).getElementById('live2d-container');
        } catch (_) {
            return null;
        }
    }

    function resolveIntroGreetingHugFrameShift(container) {
        let viewportHeight = 0;
        try {
            viewportHeight = window.innerHeight || 0;
        } catch (_) {}
        if (!viewportHeight && container && typeof container.getBoundingClientRect === 'function') {
            try {
                viewportHeight = container.getBoundingClientRect().height || 0;
            } catch (_) {}
        }
        const target = viewportHeight * INTRO_GREETING_HUG_SHIFT_VIEWPORT_RATIO;
        return clamp(
            target || INTRO_GREETING_HUG_MIN_SHIFT_PX,
            INTRO_GREETING_HUG_MIN_SHIFT_PX,
            INTRO_GREETING_HUG_MAX_SHIFT_PX
        );
    }

    function resolveIntroGreetingHugFinalFrameShift(container) {
        let viewportHeight = 0;
        try {
            viewportHeight = window.innerHeight || 0;
        } catch (_) {}
        if (!viewportHeight && container && typeof container.getBoundingClientRect === 'function') {
            try {
                viewportHeight = container.getBoundingClientRect().height || 0;
            } catch (_) {}
        }
        const target = viewportHeight * INTRO_GREETING_HUG_FINAL_SHIFT_VIEWPORT_RATIO;
        return clamp(
            target || INTRO_GREETING_HUG_FINAL_MIN_SHIFT_PX,
            INTRO_GREETING_HUG_FINAL_MIN_SHIFT_PX,
            INTRO_GREETING_HUG_FINAL_MAX_SHIFT_PX
        );
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
        Object.keys(YUI_WAKEUP_PARAMS).forEach((key) => {
            const meta = readParamMeta(coreModel, YUI_WAKEUP_PARAMS[key]);
            if (meta) {
                params[key] = meta;
            }
        });
        return params;
    }

    function scanMappedLive2DParams(coreModel, paramMap) {
        const params = {};
        Object.keys(paramMap || {}).forEach((key) => {
            const meta = readParamMeta(coreModel, paramMap[key]);
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

    function computeWakeupPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = reducedMotion ? 1 : clamp(progress, 0, 1);
        const t = easeInOutCubic(normalizedProgress);
        const holdProgress = clamp(normalizedProgress / WAKEUP_EYE_CLOSED_PROGRESS, 0, 1);
        const wakeProgress = clamp((normalizedProgress - WAKEUP_EYE_CLOSED_PROGRESS) / WAKEUP_EYE_OPEN_PROGRESS, 0, 1);
        const wakeEase = easeOutCubic(wakeProgress);
        const waveProgress = clamp((normalizedProgress - 0.68) / 0.22, 0, 1);
        const waveOut = 1 - easeOutCubic(clamp((normalizedProgress - 0.88) / 0.12, 0, 1));
        const waveWeight = Math.sin(waveProgress * Math.PI) * waveOut;
        const waveCycle = Math.sin(waveProgress * Math.PI * 4);
        let eyeOpen = 0;

        if (normalizedProgress <= WAKEUP_EYE_CLOSED_PROGRESS) {
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

    function computeIntroGreetingHugPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = reducedMotion ? 1 : clamp(progress, 0, 1);
        const hugWeight = reducedMotion ? 0 : easeInOutCubic(normalizedProgress);
        const holdPulse = Math.sin(hugWeight * Math.PI) * 0.035;
        const softLean = hugWeight * (1 + holdPulse);
        const walkBob = reducedMotion ? 0 : Math.sin(normalizedProgress * Math.PI * 8) * Math.sin(normalizedProgress * Math.PI) * 20;
        const armReach = clamp(hugWeight * 1.18, 0, 1);
        const frameScale = Number.isFinite(Number(context && context.frameScale))
            ? Number(context.frameScale)
            : INTRO_GREETING_HUG_CLOSE_SCALE;
        const frameY = Number.isFinite(Number(context && context.frameY))
            ? Number(context.frameY)
            : INTRO_GREETING_HUG_MIN_SHIFT_PX;

        return {
            angleX: 0,
            angleY: -2.2 * softLean,
            angleZ: 1.4 * softLean,
            eyeSmileLeft: 0.34 * hugWeight,
            eyeSmileRight: 0.34 * hugWeight,
            bodyAngleX: -2.6 * softLean,
            bodyAngleY: -2.2 * softLean,
            bodyAngleZ: 1.7 * softLean,
            browRightY: 0.22 * hugWeight,
            browLeftY: 0.22 * hugWeight,
            browRightAngle: -3.6 * hugWeight,
            browLeftAngle: 3.6 * hugWeight,
            mouthForm: 0.48 * hugWeight,
            cheek: 0.58 * hugWeight,
            yuiByExpression: hugWeight,
            yuiHeartSwitch: 0,
            yuiMouthCoverSwitch: 0,
            yuiRightWaveSwitch: armReach,
            yuiLeftWaveSwitch: armReach,
            yuiLeftMouthCoverAnim: 0,
            yuiRightForearmAnim: 0.98 * armReach,
            yuiLeftForearmAnim: 0.98 * armReach,
            yuiRightHandAnim: 0.88 * armReach,
            yuiLeftHandAnim: 0.88 * armReach,
            yuiRightHandWave: 0.16 * armReach,
            yuiLeftHandWave: 0.16 * armReach,
            frameScale: 1 + (frameScale - 1) * hugWeight,
            frameY: frameY * hugWeight + walkBob
        };
    }

    function computeIntroGiftHeartPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = reducedMotion ? 1 : clamp(progress, 0, 1);
        const enterWeight = easeOutCubic(clamp(normalizedProgress / 0.22, 0, 1));
        const exitWeight = 1 - easeOutCubic(clamp((normalizedProgress - 0.82) / 0.18, 0, 1));
        const heartWeight = reducedMotion ? 1 : clamp(Math.min(enterWeight, exitWeight), 0, 1);
        const hopCount = INTRO_GIFT_HEART_HOP_COUNT;
        const hopProgress = reducedMotion ? 1 : clamp(normalizedProgress * hopCount, 0, hopCount);
        const hopIndex = Math.min(hopCount - 1, Math.floor(hopProgress));
        const hopLocal = clamp(hopProgress - hopIndex, 0, 1);
        const hopEase = easeInOutCubic(hopLocal);
        const hopDirection = hopIndex % 2 === 0 ? 1 : -1;
        const fromSide = hopIndex === 0 ? 0 : -hopDirection;
        const toSide = hopDirection;
        const lateral = reducedMotion ? 0 : lerp(fromSide, toSide, hopEase);
        const airWeight = Math.sin(hopLocal * Math.PI);
        const landingWeight = Math.pow(Math.max(0, Math.cos(hopLocal * Math.PI * 2)), 10);
        const takeoffWeight = Math.pow(Math.max(0, Math.sin((1 - hopLocal) * Math.PI)), 2);
        const leanWeight = Math.sin(hopLocal * Math.PI);
        const sway = lateral * INTRO_GIFT_HEART_SWAY_PX * heartWeight;
        const jump = reducedMotion ? 0 : (
            (-airWeight * INTRO_GIFT_HEART_JUMP_UP_PX)
            + (landingWeight * INTRO_GIFT_HEART_JUMP_DOWN_PX)
        ) * heartWeight;
        const bodySway = reducedMotion ? 0 : (
            ((-hopDirection * INTRO_GIFT_HEART_BODY_SWAY_DEG) * (0.3 + leanWeight * 0.7))
            + ((fromSide - toSide) * takeoffWeight * 0.35)
        ) * heartWeight;
        const bodySquash = landingWeight * heartWeight;
        const legBend = reducedMotion ? 0 : (
            (landingWeight * INTRO_GIFT_HEART_LEG_BEND)
            - (airWeight * INTRO_GIFT_HEART_LEG_BEND * 0.34)
        ) * heartWeight;
        const legSwing = reducedMotion ? 0 : hopDirection * airWeight * 0.48 * heartWeight;
        const earPhase = normalizedProgress * Math.PI * 14;
        const earWiggle = reducedMotion ? 0 : (
            Math.sin(earPhase)
            * INTRO_GIFT_HEART_EAR_WIGGLE
            * (0.62 + airWeight * 0.38)
            * heartWeight
        );
        const earFollow = reducedMotion ? 0 : (
            Math.sin(earPhase - Math.PI * 0.16)
            * INTRO_GIFT_HEART_EAR_WIGGLE
            * 0.58
            * heartWeight
        );

        return {
            angleX: (0.45 + lateral * 0.18) * heartWeight,
            angleY: (0.7 + hopDirection * airWeight * 0.18) * heartWeight,
            angleZ: bodySway * 0.86,
            bodyAngleX: (0.35 - bodySquash * 0.55) * heartWeight,
            bodyAngleY: (0.6 + lateral * 0.32) * heartWeight,
            bodyAngleZ: bodySway,
            yuiHeartSwitch: heartWeight,
            yuiMouthCoverSwitch: 0,
            yuiRightWaveSwitch: 0,
            yuiLeftWaveSwitch: 0,
            yuiLeftMouthCoverAnim: 0,
            yuiRightHandWave: 0,
            yuiLeftHandWave: 0,
            yuiLeftEarPerspective: earWiggle * 0.42,
            yuiLeftEarRotate: earWiggle,
            yuiLeftEarWiggle1: earWiggle,
            yuiLeftEarWiggle2: earFollow * 0.46,
            yuiRightEarPerspective: earFollow * 0.42,
            yuiRightEarRotate: earFollow,
            yuiRightEarWiggle1: earFollow,
            yuiRightEarWiggle2: earWiggle * 0.46,
            yuiLeftLegShadow1: legBend * 0.74,
            yuiLeftLegShadow2: legBend,
            yuiLeftLegShadow3: legBend * 0.62,
            yuiLeftShoeLace1: legBend + legSwing,
            yuiLeftShoeLace2: legBend * 0.72 + legSwing * 0.76,
            yuiLeftShoeLace3: legBend * 0.52 + legSwing * 0.56,
            yuiLeftShoeLace4: legBend * 0.36 + legSwing * 0.42,
            yuiRightShoeLace1: -legBend + legSwing,
            yuiRightShoeLace2: -legBend * 0.72 + legSwing * 0.76,
            yuiRightShoeLace3: -legBend * 0.52 + legSwing * 0.56,
            yuiRightShoeLace4: -legBend * 0.36 + legSwing * 0.42,
            frameX: sway,
            frameY: jump
        };
    }

    function blendIntroGreetingHugPose(fromPose, toPose, progress) {
        const t = clamp(progress, 0, 1);
        const pose = {};
        Object.keys(YUI_INTRO_GREETING_HUG_PARAMS).forEach((key) => {
            if (key === 'yuiRightWaveSwitch' || key === 'yuiLeftWaveSwitch') {
                pose[key] = t < 0.82 ? (fromPose && fromPose[key] ? 1 : 0) : (toPose && toPose[key] ? 1 : 0);
                return;
            }
            pose[key] = lerp(
                Number.isFinite(Number(fromPose && fromPose[key])) ? Number(fromPose[key]) : 0,
                Number.isFinite(Number(toPose && toPose[key])) ? Number(toPose[key]) : 0,
                t
            );
        });
        pose.frameScale = lerp(
            Number.isFinite(Number(fromPose && fromPose.frameScale)) ? Number(fromPose.frameScale) : 1,
            Number.isFinite(Number(toPose && toPose.frameScale)) ? Number(toPose.frameScale) : 1,
            t
        );
        pose.frameY = lerp(
            Number.isFinite(Number(fromPose && fromPose.frameY)) ? Number(fromPose.frameY) : 0,
            Number.isFinite(Number(toPose && toPose.frameY)) ? Number(toPose.frameY) : 0,
            t
        );
        return pose;
    }

    function resolveIntroGreetingHugFrameOrigin(container, manager) {
        let width = 0;
        let height = 0;
        if (container && typeof container.getBoundingClientRect === 'function') {
            try {
                const rect = container.getBoundingClientRect();
                width = Number(rect.width) || 0;
                height = Number(rect.height) || 0;
            } catch (_) {}
        }
        if (!width || !height) {
            const screen = manager && manager.pixi_app && manager.pixi_app.renderer
                ? manager.pixi_app.renderer.screen
                : null;
            width = Math.max(1, window.innerWidth || Number(screen && screen.width) || 1);
            height = Math.max(1, window.innerHeight || Number(screen && screen.height) || 1);
        }
        return {
            x: width / 2,
            y: height
        };
    }

    function readIntroGreetingHugModelFrame(model) {
        if (!model || !model.scale) {
            return null;
        }
        const scaleX = Number.isFinite(Number(model.scale.x)) ? Number(model.scale.x) : 1;
        const scaleY = Number.isFinite(Number(model.scale.y)) ? Number(model.scale.y) : scaleX;
        return {
            x: Number.isFinite(Number(model.x)) ? Number(model.x) : 0,
            y: Number.isFinite(Number(model.y)) ? Number(model.y) : 0,
            scaleX: scaleX,
            scaleY: scaleY
        };
    }

    function writeIntroGreetingHugModelFrame(model, frame) {
        if (!model || !model.scale || !frame) {
            return false;
        }
        if (typeof model.scale.set === 'function') {
            model.scale.set(frame.scaleX, frame.scaleY);
        } else {
            model.scale.x = frame.scaleX;
            model.scale.y = frame.scaleY;
        }
        model.x = frame.x;
        model.y = frame.y;
        return true;
    }

    function resolveIntroGreetingHugModelFrame(baseFrame, manager, container, frameScale, frameY) {
        if (!baseFrame) {
            return null;
        }
        const scale = clamp(frameScale, 0.5, 2.5);
        const shiftY = Number.isFinite(Number(frameY)) ? Number(frameY) : 0;
        const origin = resolveIntroGreetingHugFrameOrigin(container, manager);
        return {
            x: baseFrame.x,
            y: origin.y + ((baseFrame.y - origin.y) * scale) + shiftY,
            scaleX: baseFrame.scaleX * scale,
            scaleY: baseFrame.scaleY * scale
        };
    }

    function applyIntroGreetingHugFramePlacementToModel(model, manager, container, baseFrame, frameScale, frameY) {
        return writeIntroGreetingHugModelFrame(
            model,
            resolveIntroGreetingHugModelFrame(baseFrame || readIntroGreetingHugModelFrame(model), manager, container, frameScale, frameY)
        );
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
            this.onInitialPose = typeof normalizedOptions.onInitialPose === 'function'
                ? normalizedOptions.onInitialPose
                : null;
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
            if (this.manager && this.manager.pixi_app && this.manager.pixi_app.renderer) {
                try {
                    this.manager.pixi_app.renderer.render(this.manager.pixi_app.stage);
                } catch (_) {}
            }
            if (this.onInitialPose) {
                try {
                    this.onInitialPose(this);
                } catch (_) {}
            }
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.ticker.add(this.tick);
            } else {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return true;
        }

        stop(reason, options) {
            if (!this.active && this.finished) {
                return;
            }
            this.active = false;
            this.finished = true;
            this.result = reason || this.result || 'stopped';
            const preserveFinalPose = !!(options && options.preserveFinalPose);
            if (this.ticker && typeof this.ticker.remove === 'function') {
                try {
                    this.ticker.remove(this.tick);
                } catch (_) {}
            }
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
            if (this.manager) {
                this.manager._suspendEyeBlinkOverride = this.previousEyeBlinkSuspended;
                this.clearTemporaryPoseOverride();
            }
            if (!preserveFinalPose) {
                this.restoreCapturedParams();
            }
        }

        cancel(reason) {
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

        restoreCapturedParams() {
            if (!this.isCurrentModel()) {
                return;
            }
            Object.keys(this.params).forEach((key) => {
                const meta = this.params[key];
                writeParam(this.coreModel, meta, meta.initial);
            });
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
            return computeWakeupPose(progress, { reducedMotion: this.reducedMotion });
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
            this.writeWeighted('eyeLeft', pose.eyeLeft, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.eyeLeft || 1));
            this.writeWeighted('eyeRight', pose.eyeRight, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.eyeRight || 1));
            this.writeWeighted('angleX', pose.angleX, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.angleX || 1));
            this.writeWeighted('angleY', pose.angleY, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.angleY || 1));
            this.writeWeighted('angleZ', pose.angleZ, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.angleZ || 1));
            this.writeWeighted('eyeBallX', pose.eyeBallX, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.eyeBallX || 1));
            this.writeWeighted('eyeBallY', pose.eyeBallY, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.eyeBallY || 1));
            this.writeWeighted('eyeSmileLeft', pose.eyeSmileLeft, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.eyeSmileLeft || 1));
            this.writeWeighted('eyeSmileRight', pose.eyeSmileRight, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.eyeSmileRight || 1));
            this.writeWeighted('bodyAngleX', pose.bodyAngleX, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.bodyAngleX || 1));
            this.writeWeighted('bodyAngleY', pose.bodyAngleY, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.bodyAngleY || 1));
            this.writeWeighted('bodyAngleZ', pose.bodyAngleZ, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.bodyAngleZ || 1));
            this.writeWeighted('yuiRightWaveSwitch', pose.yuiRightWaveSwitch, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightWaveSwitch || 1));
            this.writeWeighted('yuiRightForearmAnim', pose.yuiRightForearmAnim, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightForearmAnim || 1));
            this.writeWeighted('yuiRightHandAnim', pose.yuiRightHandAnim, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightHandAnim || 1));
            this.writeWeighted('yuiRightHandWave', pose.yuiRightHandWave, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightHandWave || 1));
        }
    }

    class Live2DIntroGreetingHugSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.approachMs = normalizeDuration(normalizedOptions.approachMs, INTRO_GREETING_HUG_APPROACH_MS);
            this.settleMs = normalizeDuration(normalizedOptions.settleMs, INTRO_GREETING_HUG_SETTLE_MS);
            this.releaseMs = normalizeDuration(normalizedOptions.releaseMs, INTRO_GREETING_HUG_RELEASE_MS);
            this.durationMs = this.approachMs + this.settleMs + this.releaseMs;
            this.token = normalizedOptions.token || 0;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.params = scanMappedLive2DParams(this.coreModel, YUI_INTRO_GREETING_HUG_PARAMS);
            this.closeFrameScale = Number.isFinite(Number(normalizedOptions.frameScale))
                ? Number(normalizedOptions.frameScale)
                : INTRO_GREETING_HUG_CLOSE_SCALE;
            this.closeFrameY = Number.isFinite(Number(normalizedOptions.frameY))
                ? Number(normalizedOptions.frameY)
                : resolveIntroGreetingHugFrameShift(this.container);
            this.finalFrameScale = Number.isFinite(Number(normalizedOptions.finalFrameScale))
                ? Number(normalizedOptions.finalFrameScale)
                : this.closeFrameScale;
            this.finalFrameY = Number.isFinite(Number(normalizedOptions.finalFrameY))
                ? Number(normalizedOptions.finalFrameY)
                : this.closeFrameY;
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.poseOverrideSource = 'yui_guide_intro_greeting_hug_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
        }

        isUsable() {
            return hasAnyWakeupParam(this.params) || !!this.container;
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
            this.startedAt = performance.now();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            this.applyPose(this.computePose(0), 1);
            this.applyFrame(this.computePose(0));
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
                try {
                    this.ticker.remove(this.tick);
                } catch (_) {}
            }
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
            if (this.manager) {
                this.clearTemporaryPoseOverride();
            }
            if (activeIntroGreetingHugSession === this) {
                activeIntroGreetingHugSession = null;
            }
            this.restoreCapturedParams();
            if (this.result !== 'played') {
                this.restoreModelFrame();
            }
        }

        cancel(reason) {
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

        restoreModelFrame() {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return false;
            }
            return writeIntroGreetingHugModelFrame(this.model, this.initialModelFrame);
        }

        commitFinalPlacement() {
            if (!this.isCurrentModel()) {
                return false;
            }
            return applyIntroGreetingHugFramePlacementToModel(
                this.model,
                this.manager,
                this.container,
                this.initialModelFrame,
                this.finalFrameScale,
                this.finalFrameY
            );
        }

        restoreCapturedParams() {
            if (!this.isCurrentModel()) {
                return;
            }
            Object.keys(this.params).forEach((key) => {
                const meta = this.params[key];
                writeParam(this.coreModel, meta, meta.initial);
            });
            this.writeWeighted('yuiByExpression', 0, 1);
            this.writeWeighted('yuiHeartSwitch', 0, 1);
            this.writeWeighted('yuiMouthCoverSwitch', 0, 1);
            this.writeWeighted('yuiRightWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftMouthCoverAnim', 0, 1);
            this.writeWeighted('yuiRightHandWave', 0, 1);
            this.writeWeighted('yuiLeftHandWave', 0, 1);
            this.writeWeighted('yuiLeftEarPerspective', 0, 1);
            this.writeWeighted('yuiLeftEarRotate', 0, 1);
            this.writeWeighted('yuiLeftEarWiggle1', 0, 1);
            this.writeWeighted('yuiLeftEarWiggle2', 0, 1);
            this.writeWeighted('yuiRightEarPerspective', 0, 1);
            this.writeWeighted('yuiRightEarRotate', 0, 1);
            this.writeWeighted('yuiRightEarWiggle1', 0, 1);
            this.writeWeighted('yuiRightEarWiggle2', 0, 1);
            YUI_INTRO_GIFT_HEART_LEG_PARAM_KEYS.forEach((key) => {
                this.writeWeighted(key, 0, 1);
            });
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
            const totalDuration = this.durationMs > 0 ? this.durationMs : 1;
            const approachEnd = Math.max(0, this.approachMs);
            const settleEnd = approachEnd + Math.max(0, this.settleMs);
            let pose;
            if (elapsed <= approachEnd) {
                const progress = approachEnd > 0 ? clamp(elapsed / approachEnd, 0, 1) : 1;
                pose = this.computePose(progress);
            } else if (elapsed <= settleEnd) {
                pose = this.computePose(1);
            } else {
                const releaseProgress = this.releaseMs > 0 ? clamp((elapsed - settleEnd) / this.releaseMs, 0, 1) : 1;
                pose = blendIntroGreetingHugPose(this.computePose(1), this.getFinalRestPose(), easeInOutCubic(releaseProgress));
            }
            return {
                elapsed: elapsed,
                pose: pose,
                weight: 1,
                finished: elapsed >= totalDuration
            };
        }

        tick() {
            if (!this.active) {
                return;
            }
            if (this.isCancelled()) {
                this.stop('cancelled');
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
            this.applyFrame(frame.pose);

            if (frame.finished) {
                this.applyPose(frame.pose, frame.weight);
                this.commitFinalPlacement();
                this.stop('played');
                return;
            }
            if (!this.ticker) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
        }

        computePose(progress) {
            return computeIntroGreetingHugPose(progress, {
                reducedMotion: this.reducedMotion,
                frameScale: this.closeFrameScale,
                frameY: this.closeFrameY
            });
        }

        getRestPose() {
            const pose = {};
            Object.keys(YUI_INTRO_GREETING_HUG_PARAMS).forEach((key) => {
                const meta = this.params[key];
                pose[key] = meta && Number.isFinite(Number(meta.initial)) ? Number(meta.initial) : 0;
            });
            pose.yuiHeartSwitch = 0;
            pose.yuiMouthCoverSwitch = 0;
            pose.yuiByExpression = 0;
            pose.yuiRightWaveSwitch = 0;
            pose.yuiLeftWaveSwitch = 0;
            pose.yuiLeftMouthCoverAnim = 0;
            pose.yuiRightHandWave = 0;
            pose.yuiLeftHandWave = 0;
            pose.frameScale = 1;
            pose.frameY = 0;
            return pose;
        }

        getFinalRestPose() {
            const pose = this.getRestPose();
            pose.frameScale = this.finalFrameScale;
            pose.frameY = this.finalFrameY;
            return pose;
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
            const cheekBase = this.params.cheek ? this.params.cheek.initial : 0;
            this.writeWeighted('angleX', pose.angleX, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.angleX || 1));
            this.writeWeighted('angleY', pose.angleY, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.angleY || 1));
            this.writeWeighted('angleZ', pose.angleZ, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.angleZ || 1));
            this.writeWeighted('eyeSmileLeft', pose.eyeSmileLeft, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.eyeSmileLeft || 1));
            this.writeWeighted('eyeSmileRight', pose.eyeSmileRight, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.eyeSmileRight || 1));
            this.writeWeighted('bodyAngleX', pose.bodyAngleX, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.bodyAngleX || 1));
            this.writeWeighted('bodyAngleY', pose.bodyAngleY, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.bodyAngleY || 1));
            this.writeWeighted('bodyAngleZ', pose.bodyAngleZ, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.bodyAngleZ || 1));
            this.writeWeighted('browRightY', pose.browRightY, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.browRightY || 1));
            this.writeWeighted('browLeftY', pose.browLeftY, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.browLeftY || 1));
            this.writeWeighted('browRightAngle', pose.browRightAngle, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.browRightAngle || 1));
            this.writeWeighted('browLeftAngle', pose.browLeftAngle, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.browLeftAngle || 1));
            this.writeWeighted('mouthForm', pose.mouthForm, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.mouthForm || 1));
            this.writeWeighted('cheek', Math.max(cheekBase, pose.cheek || 0), w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.cheek || 1));
            this.writeWeighted('yuiByExpression', pose.yuiByExpression, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiByExpression || 1));
            this.writeWeighted('yuiHeartSwitch', 0, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiHeartSwitch || 1));
            this.writeWeighted('yuiMouthCoverSwitch', 0, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiMouthCoverSwitch || 1));
            this.writeWeighted('yuiRightWaveSwitch', pose.yuiRightWaveSwitch, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiRightWaveSwitch || 1));
            this.writeWeighted('yuiLeftWaveSwitch', pose.yuiLeftWaveSwitch, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiLeftWaveSwitch || 1));
            this.writeWeighted('yuiLeftMouthCoverAnim', 0, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiLeftMouthCoverAnim || 1));
            this.writeWeighted('yuiRightForearmAnim', pose.yuiRightForearmAnim, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiRightForearmAnim || 1));
            this.writeWeighted('yuiLeftForearmAnim', pose.yuiLeftForearmAnim, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiLeftForearmAnim || 1));
            this.writeWeighted('yuiRightHandAnim', pose.yuiRightHandAnim, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiRightHandAnim || 1));
            this.writeWeighted('yuiLeftHandAnim', pose.yuiLeftHandAnim, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiLeftHandAnim || 1));
            this.writeWeighted('yuiRightHandWave', pose.yuiRightHandWave, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiRightHandWave || 1));
            this.writeWeighted('yuiLeftHandWave', pose.yuiLeftHandWave, w * (YUI_INTRO_GREETING_HUG_POSE_BLEND_FACTORS.yuiLeftHandWave || 1));
        }

        applyFrame(pose) {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return;
            }
            const frameY = Number.isFinite(Number(pose.frameY)) ? Number(pose.frameY) : 0;
            const frameScale = Number.isFinite(Number(pose.frameScale)) ? Number(pose.frameScale) : 1;
            applyIntroGreetingHugFramePlacementToModel(
                this.model,
                this.manager,
                this.container,
                this.initialModelFrame,
                frameScale,
                frameY
            );
        }
    }

    class Live2DIntroGiftHeartSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.durationMs = normalizeDuration(normalizedOptions.durationMs, INTRO_GIFT_HEART_DURATION_MS);
            this.releaseMs = normalizeDuration(normalizedOptions.releaseMs, INTRO_GIFT_HEART_RELEASE_MS);
            this.totalDurationMs = this.durationMs + this.releaseMs;
            this.token = normalizedOptions.token || 0;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.params = scanMappedLive2DParams(this.coreModel, YUI_INTRO_GIFT_HEART_PARAMS);
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.poseOverrideSource = 'yui_guide_intro_gift_heart_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
        }

        isUsable() {
            return hasAnyWakeupParam(this.params) || !!this.params.yuiHeartSwitch || !!this.container;
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
            this.startedAt = performance.now();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            this.applyPose(this.computePose(0), 1);
            this.applyFrame(this.computePose(0));
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
                try {
                    this.ticker.remove(this.tick);
                } catch (_) {}
            }
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
            if (this.manager) {
                this.clearTemporaryPoseOverride();
            }
            if (activeIntroGiftHeartSession === this) {
                activeIntroGiftHeartSession = null;
            }
            this.restoreCapturedParams();
            this.restoreModelFrame();
        }

        cancel(reason) {
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

        restoreModelFrame() {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return false;
            }
            return writeIntroGreetingHugModelFrame(this.model, this.initialModelFrame);
        }

        restoreCapturedParams() {
            if (!this.isCurrentModel()) {
                return;
            }
            Object.keys(this.params).forEach((key) => {
                const meta = this.params[key];
                writeParam(this.coreModel, meta, meta.initial);
            });
            this.writeWeighted('yuiHeartSwitch', 0, 1);
            this.writeWeighted('yuiMouthCoverSwitch', 0, 1);
            this.writeWeighted('yuiRightWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftMouthCoverAnim', 0, 1);
            this.writeWeighted('yuiRightHandWave', 0, 1);
            this.writeWeighted('yuiLeftHandWave', 0, 1);
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
            const duration = this.durationMs > 0 ? this.durationMs : 1;
            const totalDuration = this.totalDurationMs > 0 ? this.totalDurationMs : duration;
            const progress = clamp(elapsed / duration, 0, 1);
            return {
                elapsed: elapsed,
                pose: this.computePose(progress),
                weight: 1,
                finished: elapsed >= totalDuration
            };
        }

        tick() {
            if (!this.active) {
                return;
            }
            if (this.isCancelled()) {
                this.stop('cancelled');
                return;
            }
            if (!this.isCurrentModel()) {
                this.stop('model_changed');
                return;
            }
            const frame = this.getFrameState(performance.now());
            if (!this.usesTemporaryPoseOverride) {
                this.applyPose(frame.pose, frame.weight);
            }
            this.applyFrame(frame.pose);
            if (frame.finished) {
                this.applyPose(frame.pose, frame.weight);
                this.stop('played');
                return;
            }
            if (!this.ticker) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
        }

        computePose(progress) {
            return computeIntroGiftHeartPose(progress, { reducedMotion: this.reducedMotion });
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
            this.writeWeighted('angleX', pose.angleX, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.angleX || 1));
            this.writeWeighted('angleY', pose.angleY, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.angleY || 1));
            this.writeWeighted('angleZ', pose.angleZ, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.angleZ || 1));
            this.writeWeighted('bodyAngleX', pose.bodyAngleX, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.bodyAngleX || 1));
            this.writeWeighted('bodyAngleY', pose.bodyAngleY, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.bodyAngleY || 1));
            this.writeWeighted('bodyAngleZ', pose.bodyAngleZ, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.bodyAngleZ || 1));
            this.writeWeighted('yuiHeartSwitch', pose.yuiHeartSwitch, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiHeartSwitch || 1));
            this.writeWeighted('yuiMouthCoverSwitch', 0, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiMouthCoverSwitch || 1));
            this.writeWeighted('yuiRightWaveSwitch', 0, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightWaveSwitch || 1));
            this.writeWeighted('yuiLeftWaveSwitch', 0, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftWaveSwitch || 1));
            this.writeWeighted('yuiLeftMouthCoverAnim', 0, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftMouthCoverAnim || 1));
            this.writeWeighted('yuiRightHandWave', 0, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightHandWave || 1));
            this.writeWeighted('yuiLeftHandWave', 0, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftHandWave || 1));
            this.writeWeighted('yuiLeftEarPerspective', pose.yuiLeftEarPerspective, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftEarPerspective || 1));
            this.writeWeighted('yuiLeftEarRotate', pose.yuiLeftEarRotate, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftEarRotate || 1));
            this.writeWeighted('yuiLeftEarWiggle1', pose.yuiLeftEarWiggle1, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftEarWiggle1 || 1));
            this.writeWeighted('yuiLeftEarWiggle2', pose.yuiLeftEarWiggle2, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftEarWiggle2 || 1));
            this.writeWeighted('yuiRightEarPerspective', pose.yuiRightEarPerspective, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightEarPerspective || 1));
            this.writeWeighted('yuiRightEarRotate', pose.yuiRightEarRotate, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightEarRotate || 1));
            this.writeWeighted('yuiRightEarWiggle1', pose.yuiRightEarWiggle1, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightEarWiggle1 || 1));
            this.writeWeighted('yuiRightEarWiggle2', pose.yuiRightEarWiggle2, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightEarWiggle2 || 1));
            YUI_INTRO_GIFT_HEART_LEG_PARAM_KEYS.forEach((key) => {
                this.writeWeighted(key, pose[key], w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS[key] || 1));
            });
        }

        applyFrame(pose) {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return;
            }
            const frameX = Number.isFinite(Number(pose.frameX)) ? Number(pose.frameX) : 0;
            const frameY = Number.isFinite(Number(pose.frameY)) ? Number(pose.frameY) : 0;
            writeIntroGreetingHugModelFrame(this.model, {
                x: this.initialModelFrame.x + frameX,
                y: this.initialModelFrame.y + frameY,
                scaleX: this.initialModelFrame.scaleX,
                scaleY: this.initialModelFrame.scaleY
            });
        }
    }

    function applyIntroGreetingHugFinalPlacement(options) {
        const normalizedOptions = options || {};
        const context = getLive2DContext();
        if (!context || !context.model || context.model.destroyed) {
            return false;
        }
        const model = context.model;
        const hasExplicitPlacement = Number.isFinite(Number(normalizedOptions.frameScale))
            || Number.isFinite(Number(normalizedOptions.frameY));
        if (!hasExplicitPlacement) {
            return false;
        }

        const frameScale = Number.isFinite(Number(normalizedOptions.frameScale))
            ? Number(normalizedOptions.frameScale)
            : INTRO_GREETING_HUG_FINAL_SCALE;
        const frameY = Number.isFinite(Number(normalizedOptions.frameY))
            ? Number(normalizedOptions.frameY)
            : resolveIntroGreetingHugFinalFrameShift(getLive2DContainer(normalizedOptions.document || document));
        return applyIntroGreetingHugFramePlacementToModel(
            model,
            context.manager,
            getLive2DContainer(normalizedOptions.document || document),
            null,
            frameScale,
            frameY
        );
    }

    async function playIntroGreetingHug(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, INTRO_GREETING_HUG_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return { result: 'fallback', reason: 'live2d_unavailable' };
        }
        const reducedMotion = !!normalizedOptions.reducedMotion;
        if (activeIntroGreetingHugSession && activeIntroGreetingHugSession.active) {
            if (!activeIntroGreetingHugSession.isCurrentModel()) {
                activeIntroGreetingHugSession.cancel('replaced');
            } else {
                const session = activeIntroGreetingHugSession;
                return new Promise((resolve) => {
                    const waitForFinish = () => {
                        if (session.finished) {
                            resolve({
                                result: session.result || 'played',
                                reason: session.result && session.result !== 'played' ? session.result : '',
                                paramCount: Object.keys(session.params || {}).length
                            });
                            return;
                        }
                        window.requestAnimationFrame(waitForFinish);
                    };
                    window.requestAnimationFrame(waitForFinish);
                });
            }
        }
        const session = new Live2DIntroGreetingHugSession(context, {
            document: normalizedOptions.document || document,
            reducedMotion: reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            approachMs: reducedMotion ? 0 : normalizeDuration(normalizedOptions.approachMs, INTRO_GREETING_HUG_APPROACH_MS),
            settleMs: reducedMotion ? 0 : normalizeDuration(normalizedOptions.settleMs, INTRO_GREETING_HUG_SETTLE_MS),
            releaseMs: reducedMotion ? 0 : normalizeDuration(normalizedOptions.releaseMs, INTRO_GREETING_HUG_RELEASE_MS)
        });
        if (!session.isUsable()) {
            return { result: 'fallback', reason: 'intro_greeting_hug_unavailable' };
        }
        if (!session.start()) {
            return { result: 'fallback', reason: 'intro_greeting_hug_start_failed' };
        }
        activeIntroGreetingHugSession = session;

        return new Promise((resolve) => {
            const poll = () => {
                if (session.finished) {
                    resolve({
                        result: session.result || 'played',
                        reason: session.result && session.result !== 'played' ? session.result : '',
                        paramCount: Object.keys(session.params || {}).length
                    });
                    return;
                }
                window.requestAnimationFrame(poll);
            };
            window.requestAnimationFrame(poll);
        });
    }

    async function playIntroGiftHeart(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, INTRO_GIFT_HEART_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return { result: 'fallback', reason: 'live2d_unavailable' };
        }
        const reducedMotion = !!normalizedOptions.reducedMotion;
        if (activeIntroGiftHeartSession && activeIntroGiftHeartSession.active) {
            if (!activeIntroGiftHeartSession.isCurrentModel()) {
                activeIntroGiftHeartSession.cancel('replaced');
            } else {
                const session = activeIntroGiftHeartSession;
                return new Promise((resolve) => {
                    const waitForFinish = () => {
                        if (session.finished) {
                            resolve({
                                result: session.result || 'played',
                                reason: session.result && session.result !== 'played' ? session.result : '',
                                paramCount: Object.keys(session.params || {}).length
                            });
                            return;
                        }
                        window.requestAnimationFrame(waitForFinish);
                    };
                    window.requestAnimationFrame(waitForFinish);
                });
            }
        }
        const session = new Live2DIntroGiftHeartSession(context, {
            document: normalizedOptions.document || document,
            reducedMotion: reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            durationMs: reducedMotion ? 0 : normalizeDuration(normalizedOptions.durationMs, INTRO_GIFT_HEART_DURATION_MS),
            releaseMs: reducedMotion ? 0 : normalizeDuration(normalizedOptions.releaseMs, INTRO_GIFT_HEART_RELEASE_MS)
        });
        if (!session.isUsable()) {
            return { result: 'fallback', reason: 'intro_gift_heart_unavailable' };
        }
        if (!session.start()) {
            return { result: 'fallback', reason: 'intro_gift_heart_start_failed' };
        }
        activeIntroGiftHeartSession = session;

        return new Promise((resolve) => {
            const poll = () => {
                if (session.finished) {
                    resolve({
                        result: session.result || 'played',
                        reason: session.result && session.result !== 'played' ? session.result : '',
                        paramCount: Object.keys(session.params || {}).length
                    });
                    return;
                }
                window.requestAnimationFrame(poll);
            };
            window.requestAnimationFrame(poll);
        });
    }

    window.YuiGuideAvatarStage = Object.freeze({
        createWakeupSession: function createWakeupSession(context, options) {
            return new Live2DWakeupSession(context, options);
        },
        playIntroGreetingHug: playIntroGreetingHug,
        playIntroGiftHeart: playIntroGiftHeart,
        applyIntroGreetingHugFinalPlacement: applyIntroGreetingHugFinalPlacement,
        Live2DWakeupSession: Live2DWakeupSession,
        Live2DIntroGreetingHugSession: Live2DIntroGreetingHugSession,
        Live2DIntroGiftHeartSession: Live2DIntroGiftHeartSession,
        computeWakeupPose: computeWakeupPose,
        computeIntroGreetingHugPose: computeIntroGreetingHugPose,
        computeIntroGiftHeartPose: computeIntroGiftHeartPose,
        waitForLive2DContext: waitForLive2DContext,
        YUI_WAKEUP_PARAMS: YUI_WAKEUP_PARAMS,
        YUI_INTRO_GREETING_HUG_PARAMS: YUI_INTRO_GREETING_HUG_PARAMS,
        YUI_INTRO_GIFT_HEART_PARAMS: YUI_INTRO_GIFT_HEART_PARAMS
    });
})();
