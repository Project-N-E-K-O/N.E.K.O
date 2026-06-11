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
    const RETURN_CONTROL_CUE_WAVE_DURATION_MS = 4200;
    const RETURN_CONTROL_CUE_WAVE_READY_WAIT_MS = 260;
    const INTRO_GREETING_HUG_CLOSE_SCALE = 1.38;
    const INTRO_GREETING_HUG_SHIFT_VIEWPORT_RATIO = 0.58;
    const INTRO_GREETING_HUG_MIN_SHIFT_PX = 360;
    const INTRO_GREETING_HUG_MAX_SHIFT_PX = 820;
    const INTRO_GREETING_HUG_FINAL_SCALE = 1.28;
    const INTRO_GREETING_HUG_FINAL_SHIFT_VIEWPORT_RATIO = 0.52;
    const INTRO_GREETING_HUG_FINAL_MIN_SHIFT_PX = 340;
    const INTRO_GREETING_HUG_FINAL_MAX_SHIFT_PX = 700;
    const INTRO_GIFT_HEART_READY_WAIT_MS = 700;
    const INTRO_GIFT_HEART_DURATION_MS = 2600;
    const INTRO_GIFT_HEART_RELEASE_MS = 420;
    const INTRO_GIFT_HEART_SWAY_PX = 118;
    const INTRO_GIFT_HEART_JUMP_UP_PX = 16;
    const INTRO_GIFT_HEART_JUMP_DOWN_PX = 18;
    const INTRO_GIFT_HEART_HOP_COUNT = 4;
    const INTRO_GIFT_HEART_BODY_SWAY_DEG = 2.4;
    const INTRO_GIFT_HEART_EAR_WIGGLE = 0.32;
    const INTRO_GIFT_HEART_LEG_BEND = 1.15;
    const SETTINGS_PEEK_PANIC_READY_WAIT_MS = 700;
    const SETTINGS_PEEK_PANIC_REACT_MS = 260;
    const SETTINGS_PEEK_PANIC_SHAKE_MS = 520;
    const SETTINGS_PEEK_PANIC_SETTLE_MS = 680;
    const SETTINGS_PEEK_PANIC_RELEASE_MS = 260;
    const SETTINGS_PEEK_PANIC_SHIFT_RATIO = 0.12;
    const SETTINGS_PEEK_PANIC_MIN_SHIFT_PX = 54;
    const SETTINGS_PEEK_PANIC_MAX_SHIFT_PX = 118;
    const INTERRUPT_RESIST_READY_WAIT_MS = 560;
    const INTERRUPT_RESIST_DURATION_MS = 1180;
    const INTERRUPT_RESIST_REDUCED_DURATION_MS = 280;
    const INTERRUPT_RESIST_MIN_DURATION_MS = 920;
    const INTERRUPT_RESIST_MAX_DURATION_MS = 7600;
    const INTERRUPT_RESIST_BASE_SCALE = 0.1;
    const ANGRY_EXIT_READY_WAIT_MS = 560;
    const ANGRY_EXIT_DURATION_MS = 2200;
    const ANGRY_EXIT_REDUCED_DURATION_MS = 420;
    const ANGRY_EXIT_MIN_DURATION_MS = 1600;
    const ANGRY_EXIT_MAX_DURATION_MS = 16000;
    const PLUGIN_DASHBOARD_CORNER_READY_WAIT_MS = 700;
    const PLUGIN_DASHBOARD_CORNER_HIDE_MS = 520;
    const PLUGIN_DASHBOARD_CORNER_APPEAR_MS = 720;
    const PLUGIN_DASHBOARD_CORNER_ROTATION_DEG = 45;
    const PLUGIN_DASHBOARD_CORNER_CENTER_ABOVE_BOTTOM_RATIO = 0.08;
    const PLUGIN_DASHBOARD_CORNER_RIGHT_OUTSIDE_RATIO = 0.35;
    const PLUGIN_DASHBOARD_CORNER_ELEVATED_Z_INDEX = '2147483647';
    const TAKEOVER_TOP_PEEK_TOP_OUTSIDE_RATIO = 0.6;
    const TAKEOVER_TOP_PEEK_CENTER_ABOVE_TOP_RATIO = 0.6;
    const GUIDE_IDLE_SWAY_READY_WAIT_MS = 900;
    const GUIDE_IDLE_SWAY_BLEND_IN_MS = 360;
    const GUIDE_IDLE_SWAY_RELEASE_MS = 320;
    const YUI_GUIDE_AVATAR_ID = 'main-live2d';
    const YUI_GUIDE_CHARACTER_ID = 'yui';
    const YUI_GUIDE_PERFORMANCE_PRIORITY = 80;
    const YUI_WAKEUP_PERFORMANCE_CAPABILITIES = Object.freeze(['params', 'motion', 'lookAt', 'expression']);
    const YUI_INTRO_PERFORMANCE_CAPABILITIES = Object.freeze(['frame', 'params', 'lookAt', 'expression']);
    const YUI_INTRO_VOICE_LOOK_AT_CAPABILITIES = Object.freeze(['lookAt']);
    const YUI_SETTINGS_PEEK_PANIC_WITH_CURSOR_LOOK_AT_CAPABILITIES = Object.freeze(['frame', 'params', 'expression']);
    const YUI_RETURN_CONTROL_CUE_WAVE_CAPABILITIES = Object.freeze(['params']);
    const YUI_PLUGIN_DASHBOARD_FRAME_CAPABILITIES = Object.freeze(['frame']);
    const INTRO_VOICE_LOOK_AT_SMOOTHING = 0.2;
    const INTRO_VOICE_LOOK_AT_RELEASE_MS = 220;
    const INTRO_GREETING_HUG_BLEND_IN_MS = 460;
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
        yuiRightForearmAnim: 'Param90',
        yuiLeftForearmAnim: 'Param91',
        yuiRightHandAnim: 'Param92',
        yuiLeftHandAnim: 'Param93',
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
        hairFront: 'ParamHairFront',
        hairSide: 'ParamHairSide',
        hairBack: 'ParamHairBack',
        yuiRightPonytailY: 'Param40',
        yuiRightBowX: 'Param42',
        yuiRightBowY: 'Param43',
        skirtX1: 'Param54',
        skirtX2: 'Param55',
        skirtX3: 'Param56',
        skirtX4: 'Param57',
        skirtY1: 'Param58',
        skirtY2: 'Param59',
        skirtY3: 'Param60',
        skirtY4: 'Param61',
        pendantX: 'Param63',
        clothX1: 'Param64',
        clothY1: 'Param65',
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
    const YUI_SETTINGS_PEEK_PANIC_PARAMS = Object.freeze({
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ',
        browRightY: 'ParamBrowRY',
        browLeftY: 'ParamBrowLY',
        browRightAngle: 'ParamBrowRAngle',
        browLeftAngle: 'ParamBrowLAngle',
        mouthForm: 'ParamMouthForm',
        cheek: 'ParamCheek',
        eyeSmileLeft: 'ParamEyeLSmile',
        eyeSmileRight: 'ParamEyeRSmile',
        yuiPanicMouthZ2: 'Param72',
        yuiPanicEyesYyy: 'Param73',
        yuiSweat: 'Param69',
        yuiSweatAnim: 'Param83',
        yuiOuterSweatAnim1: 'Param85',
        yuiMouthCoverSwitch: 'Param76',
        yuiLeftMouthCoverAnim: 'Param94',
        yuiRightWaveSwitch: 'Param75',
        yuiLeftWaveSwitch: 'Param77',
        yuiRightForearmAnim: 'Param90',
        yuiLeftForearmAnim: 'Param91',
        yuiRightHandAnim: 'Param92',
        yuiLeftHandAnim: 'Param93',
        yuiRightHandWave: 'Param95',
        yuiLeftHandWave: 'Param96',
        yuiLeftEarPerspective: 'Param44',
        yuiLeftEarRotate: 'Param45',
        yuiLeftEarWiggle1: 'Param46',
        yuiRightEarPerspective: 'Param49',
        yuiRightEarRotate: 'Param50',
        yuiRightEarWiggle1: 'Param51',
        hairFront: 'ParamHairFront',
        hairSide: 'ParamHairSide',
        hairBack: 'ParamHairBack',
        pendantX: 'Param63',
        clothX1: 'Param64',
        clothY1: 'Param65',
        skirtX1: 'Param54',
        skirtX2: 'Param55',
        skirtX3: 'Param56',
        skirtX4: 'Param57',
        skirtY1: 'Param58',
        skirtY2: 'Param59',
        skirtY3: 'Param60',
        skirtY4: 'Param61'
    });
    const YUI_INTERRUPT_RESIST_PARAMS = Object.freeze({
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        eyeBallX: 'ParamEyeBallX',
        eyeBallY: 'ParamEyeBallY',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ',
        browRightY: 'ParamBrowRY',
        browLeftY: 'ParamBrowLY',
        browRightAngle: 'ParamBrowRAngle',
        browLeftAngle: 'ParamBrowLAngle',
        mouthForm: 'ParamMouthForm',
        cheek: 'ParamCheek',
        eyeSmileLeft: 'ParamEyeLSmile',
        eyeSmileRight: 'ParamEyeRSmile',
        yuiPanicMouthZ2: 'Param72',
        yuiAnnoyedPoutZ3: 'Param78',
        yuiRightWaveSwitch: 'Param75',
        yuiLeftWaveSwitch: 'Param77',
        yuiRightForearmAnim: 'Param90',
        yuiLeftForearmAnim: 'Param91',
        yuiRightHandAnim: 'Param92',
        yuiLeftHandAnim: 'Param93',
        yuiRightHandWave: 'Param95',
        yuiLeftHandWave: 'Param96'
    });
    const YUI_INTRO_VOICE_LOOK_AT_PARAMS = Object.freeze({
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        eyeBallX: 'ParamEyeBallX',
        eyeBallY: 'ParamEyeBallY',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ'
    });
    const YUI_ANGRY_EXIT_PARAMS = Object.freeze({
        angleX: 'ParamAngleX',
        angleY: 'ParamAngleY',
        angleZ: 'ParamAngleZ',
        eyeBallX: 'ParamEyeBallX',
        eyeBallY: 'ParamEyeBallY',
        bodyAngleX: 'ParamBodyAngleX',
        bodyAngleY: 'ParamBodyAngleY',
        bodyAngleZ: 'ParamBodyAngleZ',
        browRightY: 'ParamBrowRY',
        browLeftY: 'ParamBrowLY',
        browRightAngle: 'ParamBrowRAngle',
        browLeftAngle: 'ParamBrowLAngle',
        mouthForm: 'ParamMouthForm',
        cheek: 'ParamCheek',
        eyeSmileLeft: 'ParamEyeLSmile',
        eyeSmileRight: 'ParamEyeRSmile',
        yuiPanicMouthZ2: 'Param72',
        yuiAnnoyedPoutZ3: 'Param78',
        yuiAngryEyesWy: 'Param67',
        yuiRightWaveSwitch: 'Param75',
        yuiLeftWaveSwitch: 'Param77',
        yuiRightForearmAnim: 'Param90',
        yuiLeftForearmAnim: 'Param91',
        yuiRightHandAnim: 'Param92',
        yuiLeftHandAnim: 'Param93',
        yuiRightHandWave: 'Param95',
        yuiLeftHandWave: 'Param96'
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
    const activePerformanceLocks = new Map();
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
        angleX: 1,
        angleY: 0.9,
        angleZ: 1,
        bodyAngleX: 0.86,
        bodyAngleY: 0.86,
        bodyAngleZ: 0.28,
        yuiHeartSwitch: 1,
        yuiMouthCoverSwitch: 1,
        yuiRightWaveSwitch: 1,
        yuiLeftWaveSwitch: 1,
        yuiLeftMouthCoverAnim: 1,
        yuiRightForearmAnim: 0.78,
        yuiLeftForearmAnim: 0.78,
        yuiRightHandAnim: 0.72,
        yuiLeftHandAnim: 0.72,
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
        hairFront: 1,
        hairSide: 1,
        hairBack: 1,
        yuiRightPonytailY: 1,
        yuiRightBowX: 1,
        yuiRightBowY: 1,
        skirtX1: 1,
        skirtX2: 1,
        skirtX3: 1,
        skirtX4: 1,
        skirtY1: 1,
        skirtY2: 1,
        skirtY3: 1,
        skirtY4: 1,
        pendantX: 1,
        clothX1: 1,
        clothY1: 1,
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
    const YUI_SETTINGS_PEEK_PANIC_POSE_BLEND_FACTORS = Object.freeze({
        angleX: 0.8,
        angleY: 0.9,
        angleZ: 0.92,
        bodyAngleX: 0.82,
        bodyAngleY: 0.86,
        bodyAngleZ: 0.94,
        browRightY: 0.84,
        browLeftY: 0.84,
        browRightAngle: 0.88,
        browLeftAngle: 0.88,
        mouthForm: 0.8,
        cheek: 0.74,
        eyeSmileLeft: 0.5,
        eyeSmileRight: 0.5,
        yuiPanicMouthZ2: 1,
        yuiPanicEyesYyy: 1,
        yuiSweat: 1,
        yuiSweatAnim: 1,
        yuiOuterSweatAnim1: 1,
        yuiMouthCoverSwitch: 1,
        yuiLeftMouthCoverAnim: 1,
        yuiRightWaveSwitch: 1,
        yuiLeftWaveSwitch: 1,
        yuiRightForearmAnim: 0.92,
        yuiLeftForearmAnim: 0.96,
        yuiRightHandAnim: 0.92,
        yuiLeftHandAnim: 0.96,
        yuiRightHandWave: 1,
        yuiLeftHandWave: 1,
        yuiLeftEarPerspective: 0.72,
        yuiLeftEarRotate: 0.76,
        yuiLeftEarWiggle1: 0.82,
        yuiRightEarPerspective: 0.72,
        yuiRightEarRotate: 0.76,
        yuiRightEarWiggle1: 0.82,
        hairFront: 0.92,
        hairSide: 0.98,
        hairBack: 0.88,
        pendantX: 0.86,
        clothX1: 0.9,
        clothY1: 0.9,
        skirtX1: 0.9,
        skirtX2: 0.95,
        skirtX3: 1,
        skirtX4: 1,
        skirtY1: 0.74,
        skirtY2: 0.8,
        skirtY3: 0.84,
        skirtY4: 0.88
    });
    const YUI_INTERRUPT_RESIST_POSE_BLEND_FACTORS = Object.freeze({
        angleX: 0.84,
        angleY: 0.84,
        angleZ: 0.84,
        eyeBallX: 0.92,
        eyeBallY: 0.92,
        bodyAngleX: 0.8,
        bodyAngleY: 0.82,
        bodyAngleZ: 0.84,
        browRightY: 0.86,
        browLeftY: 0.86,
        browRightAngle: 0.9,
        browLeftAngle: 0.9,
        mouthForm: 0.84,
        cheek: 0.74,
        eyeSmileLeft: 0.46,
        eyeSmileRight: 0.46,
        yuiPanicMouthZ2: 1,
        yuiAnnoyedPoutZ3: 1,
        yuiRightWaveSwitch: 1,
        yuiLeftWaveSwitch: 1,
        yuiRightForearmAnim: 0.94,
        yuiLeftForearmAnim: 0.94,
        yuiRightHandAnim: 0.94,
        yuiLeftHandAnim: 0.94,
        yuiRightHandWave: 1,
        yuiLeftHandWave: 1
    });
    const YUI_ANGRY_EXIT_POSE_BLEND_FACTORS = Object.freeze({
        angleX: 0.9,
        angleY: 0.9,
        angleZ: 0.9,
        eyeBallX: 0.96,
        eyeBallY: 0.96,
        bodyAngleX: 0.9,
        bodyAngleY: 0.9,
        bodyAngleZ: 0.92,
        browRightY: 0.94,
        browLeftY: 0.94,
        browRightAngle: 0.98,
        browLeftAngle: 0.98,
        mouthForm: 0.9,
        cheek: 0.82,
        eyeSmileLeft: 0.46,
        eyeSmileRight: 0.46,
        yuiPanicMouthZ2: 1,
        yuiAnnoyedPoutZ3: 1,
        yuiAngryEyesWy: 1,
        yuiRightWaveSwitch: 1,
        yuiLeftWaveSwitch: 1,
        yuiRightForearmAnim: 0.98,
        yuiLeftForearmAnim: 0.98,
        yuiRightHandAnim: 0.98,
        yuiLeftHandAnim: 0.98,
        yuiRightHandWave: 1,
        yuiLeftHandWave: 1
    });
    let activeIntroGreetingHugSession = null;
    let activeIntroGiftHeartSession = null;
    let activeSettingsPeekPanicSession = null;
    let activeInterruptResistSession = null;
    let activeAngryExitSession = null;
    let activeIntroVoiceLookAtSession = null;
    let activePluginDashboardCornerSession = null;
    let activeReturnControlCueWaveSession = null;
    let activeGuideIdleSwaySession = null;

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

    function blendNumericPose(fromPose, toPose, weight) {
        const t = clamp(weight, 0, 1);
        const result = {};
        const keys = new Set([
            ...Object.keys(fromPose || {}),
            ...Object.keys(toPose || {})
        ]);
        keys.forEach((key) => {
            const fromValue = Number.isFinite(Number(fromPose && fromPose[key]))
                ? Number(fromPose[key])
                : (key === 'frameScale' ? 1 : 0);
            const toValue = Number.isFinite(Number(toPose && toPose[key]))
                ? Number(toPose[key])
                : (key === 'frameScale' ? 1 : 0);
            result[key] = lerp(fromValue, toValue, t);
        });
        return result;
    }

    function blendPoseTowardNeutral(pose, weight) {
        const neutral = {};
        Object.keys(pose || {}).forEach((key) => {
            neutral[key] = key === 'frameScale' ? 1 : 0;
        });
        return blendNumericPose(pose, neutral, weight);
    }

    function readMappedPose(coreModel, mapping, fallbackPose) {
        const pose = Object.assign({}, fallbackPose || {});
        if (!coreModel || !mapping) {
            return pose;
        }
        Object.keys(mapping).forEach((key) => {
            const paramId = mapping[key];
            if (!paramId) {
                return;
            }
            try {
                const idx = coreModel.getParameterIndex(paramId);
                if (idx >= 0) {
                    const value = coreModel.getParameterValueByIndex(idx);
                    if (Number.isFinite(Number(value))) {
                        pose[key] = Number(value);
                    }
                }
            } catch (_) {}
        });
        return pose;
    }

    function computeGuideIdleSwayPose(now, context) {
        const elapsedMs = Math.max(0, Number(now) - Number(context && context.startedAt || 0));
        const elapsedSeconds = elapsedMs / 1000;
        const reducedMotion = !!(context && context.reducedMotion);
        const swayAmplitude = reducedMotion ? 0.55 : 1;
        const shoulderAmplitude = reducedMotion ? 0.35 : 0.68;
        const headWave = Math.sin(elapsedSeconds * 1.18);
        const secondaryWave = Math.sin(elapsedSeconds * 2.07 + 0.6);
        const slowWave = Math.sin(elapsedSeconds * 0.72 + 1.1);
        return {
            angleX: (headWave * 1.1 + secondaryWave * 0.35) * swayAmplitude,
            angleY: slowWave * 0.55 * swayAmplitude,
            angleZ: (headWave * 0.38 + secondaryWave * 0.16) * swayAmplitude,
            bodyAngleX: (headWave * 1.25 + secondaryWave * 0.42) * shoulderAmplitude,
            bodyAngleY: slowWave * 0.44 * shoulderAmplitude,
            bodyAngleZ: (headWave * 0.3 + secondaryWave * 0.1) * shoulderAmplitude
        };
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

    function isInterruptResistOverrideActive(session) {
        return !!(
            activeInterruptResistSession
            && activeInterruptResistSession.active
            && activeInterruptResistSession !== session
        );
    }

    function isAngryExitOverrideActive(session) {
        return !!(
            activeAngryExitSession
            && activeAngryExitSession.active
            && activeAngryExitSession !== session
        );
    }

    function syncSessionInterruptPause(session, now) {
        if (!session) {
            return false;
        }
        const currentNow = Number.isFinite(Number(now)) ? Number(now) : performance.now();
        if (!isInterruptResistOverrideActive(session) && !isAngryExitOverrideActive(session)) {
            if (Number.isFinite(Number(session.interruptSuspendedAt)) && session.interruptSuspendedAt > 0) {
                session.startedAt += Math.max(0, currentNow - session.interruptSuspendedAt);
                session.interruptSuspendedAt = 0;
            }
            return false;
        }
        if (!Number.isFinite(Number(session.interruptSuspendedAt)) || session.interruptSuspendedAt <= 0) {
            session.interruptSuspendedAt = currentNow;
        }
        return true;
    }

    function getAvatarPerformanceCoordinator() {
        const api = window.AvatarPerformance;
        if (!api || typeof api.getDefaultCoordinator !== 'function') {
            return null;
        }
        try {
            const coordinator = api.getDefaultCoordinator();
            return coordinator && typeof coordinator.acquire === 'function' && typeof coordinator.release === 'function'
                ? coordinator
                : null;
        } catch (_) {
            return null;
        }
    }

    function createNoopPerformanceLock() {
        return {
            id: '',
            release: function () {}
        };
    }

    function acquireYuiGuidePerformanceLock(key, capabilities) {
        const lockKey = String(key || 'home-yui-guide').trim() || 'home-yui-guide';
        const existing = activePerformanceLocks.get(lockKey);
        if (existing) {
            existing.refs += 1;
            return {
                id: existing.session && existing.session.id ? existing.session.id : '',
                release: function (reason) {
                    releaseYuiGuidePerformanceLock(lockKey, reason || 'release');
                }
            };
        }

        const coordinator = getAvatarPerformanceCoordinator();
        if (!coordinator) {
            return createNoopPerformanceLock();
        }

        let session = null;
        const record = {
            refs: 1,
            session: null
        };
        try {
            session = coordinator.acquire({
                owner: lockKey,
                avatarId: YUI_GUIDE_AVATAR_ID,
                characterId: YUI_GUIDE_CHARACTER_ID,
                priority: YUI_GUIDE_PERFORMANCE_PRIORITY,
                force: true,
                capabilities: Array.isArray(capabilities) ? capabilities.slice() : [],
                onRelease: function (releasedSession) {
                    const current = activePerformanceLocks.get(lockKey);
                    if (current && current.session === releasedSession) {
                        activePerformanceLocks.delete(lockKey);
                    }
                }
            });
        } catch (_) {
            session = null;
        }
        if (!session) {
            return createNoopPerformanceLock();
        }

        record.session = session;
        activePerformanceLocks.set(lockKey, record);
        return {
            id: session.id || '',
            release: function (reason) {
                releaseYuiGuidePerformanceLock(lockKey, reason || 'release');
            }
        };
    }

    function releaseYuiGuidePerformanceLock(key, reason) {
        const lockKey = String(key || 'home-yui-guide').trim() || 'home-yui-guide';
        const record = activePerformanceLocks.get(lockKey);
        if (!record) {
            return false;
        }
        record.refs = Math.max(0, record.refs - 1);
        if (record.refs > 0) {
            return true;
        }
        activePerformanceLocks.delete(lockKey);
        const coordinator = getAvatarPerformanceCoordinator();
        if (!coordinator || !record.session) {
            return false;
        }
        try {
            return coordinator.release(record.session, reason || 'release') === true;
        } catch (_) {
            return false;
        }
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

    function computeWakeupRightHandWavePose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = clamp(progress, 0, 1);
        const wakeupWaveProgress = 0.68 + normalizedProgress * 0.32;
        const pose = computeWakeupPose(wakeupWaveProgress, {
            reducedMotion: reducedMotion
        });
        return {
            yuiRightWaveSwitch: pose.yuiRightWaveSwitch,
            yuiRightForearmAnim: pose.yuiRightForearmAnim,
            yuiRightHandAnim: pose.yuiRightHandAnim,
            yuiRightHandWave: pose.yuiRightHandWave
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
        const lateralVelocity = reducedMotion ? 0 : (toSide - fromSide) * Math.sin(hopLocal * Math.PI);
        const airWeight = Math.sin(hopLocal * Math.PI);
        const landingWeight = Math.pow(Math.max(0, Math.cos(hopLocal * Math.PI * 2)), 10);
        const takeoffWeight = Math.pow(Math.max(0, Math.sin((1 - hopLocal) * Math.PI)), 2);
        const leanWeight = Math.sin(hopLocal * Math.PI);
        const sway = (lateral + lateralVelocity * 0.12) * INTRO_GIFT_HEART_SWAY_PX * heartWeight;
        const jump = reducedMotion ? 0 : (
            (-airWeight * INTRO_GIFT_HEART_JUMP_UP_PX)
            + (landingWeight * INTRO_GIFT_HEART_JUMP_DOWN_PX)
        ) * heartWeight;
        const bodySway = reducedMotion ? 0 : (
            ((-lateral * INTRO_GIFT_HEART_BODY_SWAY_DEG) * (0.22 + leanWeight * 0.28))
            + ((-lateralVelocity * INTRO_GIFT_HEART_BODY_SWAY_DEG) * 0.12)
            + ((fromSide - toSide) * takeoffWeight * 0.18)
        ) * heartWeight;
        const bodySquash = landingWeight * heartWeight;
        const legBend = reducedMotion ? 0 : (
            (landingWeight * INTRO_GIFT_HEART_LEG_BEND)
            - (airWeight * INTRO_GIFT_HEART_LEG_BEND * 0.34)
        ) * heartWeight;
        const legSwing = reducedMotion ? 0 : hopDirection * airWeight * 0.48 * heartWeight;
        const inertialSwing = reducedMotion ? 0 : (
            (-lateral * 1.35)
            + (-lateralVelocity * 0.92)
            + (hopDirection * landingWeight * 0.62)
        ) * heartWeight;
        const softFollow = reducedMotion ? 0 : (
            (lateral * 0.72)
            + (lateralVelocity * 0.38)
        ) * heartWeight;
        const verticalFollow = reducedMotion ? 0 : (
            (airWeight * -0.42)
            + (landingWeight * 0.62)
            + (takeoffWeight * -0.18)
        ) * heartWeight;
        const armBounce = reducedMotion ? 0 : (
            (airWeight * 0.11)
            + (landingWeight * 0.22)
            + (takeoffWeight * 0.07)
        ) * heartWeight;
        const armCounterSwing = reducedMotion ? 0 : (
            (inertialSwing * 0.12)
            + (lateralVelocity * 0.07 * heartWeight)
            + (hopDirection * landingWeight * 0.06 * heartWeight)
        );
        const handBounce = reducedMotion ? 0 : (
            (airWeight * 0.08)
            + (landingWeight * 0.17)
            + (takeoffWeight * 0.05)
        ) * heartWeight;
        const handCounterSwing = reducedMotion ? 0 : (
            (inertialSwing * 0.09)
            + (lateralVelocity * 0.05 * heartWeight)
            + (hopDirection * landingWeight * 0.04 * heartWeight)
        );
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
            angleX: (0.45 + lateral * 2.25 + lateralVelocity * 0.62) * heartWeight,
            angleY: (0.7 + hopDirection * airWeight * 0.42 + lateral * 0.55) * heartWeight,
            angleZ: (bodySway * 0.22) + (lateral * 0.18 * heartWeight),
            bodyAngleX: (0.35 - bodySquash * 0.9 + airWeight * 0.34) * heartWeight,
            bodyAngleY: (0.6 + lateral * 1.55 + lateralVelocity * 0.32) * heartWeight,
            bodyAngleZ: bodySway * 0.34,
            yuiHeartSwitch: heartWeight,
            yuiMouthCoverSwitch: 0,
            yuiRightWaveSwitch: 0,
            yuiLeftWaveSwitch: 0,
            yuiLeftMouthCoverAnim: 0,
            yuiRightForearmAnim: reducedMotion ? 0 : clamp((0.42 * heartWeight) + armBounce - armCounterSwing, 0, 1),
            yuiLeftForearmAnim: reducedMotion ? 0 : clamp((0.42 * heartWeight) + armBounce + armCounterSwing, 0, 1),
            yuiRightHandAnim: reducedMotion ? 0 : clamp((0.34 * heartWeight) + handBounce - handCounterSwing, 0, 1),
            yuiLeftHandAnim: reducedMotion ? 0 : clamp((0.34 * heartWeight) + handBounce + handCounterSwing, 0, 1),
            yuiRightHandWave: 0,
            yuiLeftHandWave: 0,
            yuiLeftEarPerspective: earWiggle * 0.72 + inertialSwing * 0.18,
            yuiLeftEarRotate: earWiggle * 1.65 + inertialSwing * 0.35,
            yuiLeftEarWiggle1: earWiggle * 1.55 + inertialSwing * 0.3,
            yuiLeftEarWiggle2: earFollow * 0.92 + verticalFollow * 0.14,
            yuiRightEarPerspective: earFollow * 0.72 + inertialSwing * 0.14,
            yuiRightEarRotate: earFollow * 1.65 + inertialSwing * 0.32,
            yuiRightEarWiggle1: earFollow * 1.55 + inertialSwing * 0.28,
            yuiRightEarWiggle2: earWiggle * 0.92 + verticalFollow * 0.14,
            hairFront: inertialSwing * 2.2 + verticalFollow * 0.55,
            hairSide: inertialSwing * 2.65 + softFollow * 0.35,
            hairBack: inertialSwing * 1.85 + verticalFollow * 0.72,
            yuiRightPonytailY: verticalFollow * 1.7 + Math.abs(inertialSwing) * 0.55,
            yuiRightBowX: inertialSwing * 1.65,
            yuiRightBowY: verticalFollow * 1.25 + Math.abs(inertialSwing) * 0.18,
            skirtX1: inertialSwing * 1.45,
            skirtX2: inertialSwing * 1.85,
            skirtX3: inertialSwing * 2.25,
            skirtX4: inertialSwing * 2.55,
            skirtY1: verticalFollow * 1.15 + landingWeight * 0.24 * heartWeight,
            skirtY2: verticalFollow * 1.35 + landingWeight * 0.32 * heartWeight,
            skirtY3: verticalFollow * 1.55 + landingWeight * 0.42 * heartWeight,
            skirtY4: verticalFollow * 1.75 + landingWeight * 0.52 * heartWeight,
            pendantX: inertialSwing * 1.75 + softFollow * 0.52,
            clothX1: inertialSwing * 1.45,
            clothY1: verticalFollow * 1.35,
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

    function computeSettingsPeekPanicPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = reducedMotion ? 1 : clamp(progress, 0, 1);
        const react = easeOutCubic(clamp(normalizedProgress / 0.16, 0, 1));
        const shakeWindow = clamp((normalizedProgress - 0.08) / 0.26, 0, 1);
        const shakeEnvelope = Math.sin(shakeWindow * Math.PI);
        const shakePhase = Math.sin(shakeWindow * Math.PI * 3);
        const yieldProgress = easeOutCubic(clamp((normalizedProgress - 0.2) / 0.46, 0, 1));
        const holdWindow = clamp((normalizedProgress - 0.18) / 0.68, 0, 1);
        const holdWave = Math.sin(holdWindow * Math.PI * 2.2);
        const holdWaveFast = Math.sin(holdWindow * Math.PI * 4.6);
        const holdBob = Math.sin(holdWindow * Math.PI * 1.1);
        const holdBobFast = Math.sin(holdWindow * Math.PI * 2.7);
        const settle = easeOutCubic(clamp((normalizedProgress - 0.82) / 0.18, 0, 1));
        const direction = Number(context && context.direction) < 0 ? -1 : 1;
        const shiftX = Number.isFinite(Number(context && context.shiftX))
            ? Number(context.shiftX)
            : direction * SETTINGS_PEEK_PANIC_MIN_SHIFT_PX;
        const shiftY = Number.isFinite(Number(context && context.shiftY))
            ? Number(context.shiftY)
            : 18;
        const expressionEnter = reducedMotion
            ? 1
            : easeInOutCubic(clamp((normalizedProgress - 0.02) / 0.24, 0, 1));
        const expressionRelease = reducedMotion
            ? 0
            : easeOutCubic(clamp((normalizedProgress - 0.84) / 0.14, 0, 1));
        const expressionWeight = clamp(expressionEnter * (1 - expressionRelease * 0.92), 0, 1);
        const expressionPulse = reducedMotion ? 0 : (holdBob * 0.05 + shakeEnvelope * 0.04) * expressionWeight;
        const mouthEnter = reducedMotion
            ? 1
            : easeInOutCubic(clamp((normalizedProgress - 0.01) / 0.18, 0, 1));
        const mouthWeight = clamp(mouthEnter + yieldProgress * 0.06, 0, 1);
        const protectHold = reducedMotion
            ? 1
            : easeInOutCubic(clamp((normalizedProgress - 0.04) / 0.18, 0, 1));
        const interceptEnter = easeInOutCubic(clamp(normalizedProgress / 0.2, 0, 1));
        const interceptExit = easeOutCubic(clamp((normalizedProgress - 0.3) / 0.26, 0, 1));
        const interceptWeight = reducedMotion ? 0 : interceptEnter * (1 - interceptExit);
        const blockRightEnter = easeInOutCubic(clamp((normalizedProgress - 0.06) / 0.16, 0, 1));
        const blockRightExit = easeOutCubic(clamp((normalizedProgress - 0.58) / 0.34, 0, 1));
        const blockRightWeight = reducedMotion ? 0 : blockRightEnter * (1 - blockRightExit);
        const guardReach = reducedMotion
            ? 1
            : clamp((protectHold * 0.92) + (yieldProgress * 0.18) + (shakeEnvelope * 0.08), 0, 1);
        const guardPulse = reducedMotion ? 0 : ((holdWaveFast * 0.05) + (holdBobFast * 0.03)) * (1 - settle * 0.78);
        const handWobble = reducedMotion
            ? 0
            : ((shakePhase * 0.18) + (holdWaveFast * 0.12) + (holdBobFast * 0.06)) * protectHold * (1 - settle * 0.72);
        const leadArmWeight = clamp(guardReach + 0.14 + guardPulse, 0, 1);
        const leadRightArm = direction < 0 ? leadArmWeight : 0;
        const leadLeftArm = direction < 0 ? 0 : leadArmWeight;
        const naturalBodySway = reducedMotion
            ? 0
            : ((holdWave * 1.42) + (holdWaveFast * 0.36) + (holdBob * 0.42)) * (1 - settle * 0.84);
        const torsoBounce = reducedMotion
            ? 0
            : ((holdBob * 0.92) + (holdBobFast * 0.24) + (shakeEnvelope * 0.12)) * (1 - settle * 0.76);
        const shoulderTremor = reducedMotion
            ? 0
            : ((shakePhase * 0.26) + (holdWaveFast * 0.14)) * (1 - settle * 0.74);
        const swayEnvelope = Math.sin(holdWindow * Math.PI);
        const tiltSway = reducedMotion
            ? 0
            : Math.sin(holdWindow * Math.PI * 1.55 + Math.PI * 0.12) * swayEnvelope * 0.74 * (1 - settle * 0.82);
        const clothFollow = reducedMotion ? 0 : (shoulderTremor * 0.7 + naturalBodySway * 0.92 + torsoBounce * 0.16) * direction;
        const skirtFollow = reducedMotion ? 0 : (shoulderTremor * 0.84 + naturalBodySway * 1.18 + torsoBounce * 0.22) * direction;
        const hairFollow = reducedMotion ? 0 : (shoulderTremor * 0.98 + naturalBodySway * 1.42 + torsoBounce * 0.18) * direction;
        const frameShakeX = reducedMotion ? 0 : direction * (shakePhase * 2.8 + holdWaveFast * 0.52) * (1 - settle * 0.92);
        const frameShakeY = reducedMotion ? 0 : ((torsoBounce * -2.2) + (holdBobFast * -0.42) + (Math.abs(shakePhase) * -0.6)) * (1 - settle * 0.82);
        const interceptX = reducedMotion ? 0 : (-direction * 7 * interceptWeight);
        const blockRightX = reducedMotion
            ? 0
            : blockRightWeight * Math.min(388, Math.max(238, Math.abs(shiftX) * 2.94 + 82));
        const suppressedYieldX = (shiftX * yieldProgress) * (1 - blockRightWeight * 0.94);
        const walkMoveWeight = reducedMotion
            ? 0
            : clamp(
                blockRightWeight
                + (yieldProgress * (1 - blockRightWeight) * (1 - settle * 0.4))
                + (interceptWeight * 0.35),
                0,
                1
            );
        const walkCycle = reducedMotion ? 0 : Math.sin(normalizedProgress * Math.PI * 5.2);
        const walkBob = reducedMotion ? 0 : Math.abs(walkCycle) * 10 * walkMoveWeight;
        const walkDip = reducedMotion ? 0 : walkCycle * 1.1 * walkMoveWeight;
        const yieldedX = suppressedYieldX + interceptX + blockRightX;
        const yieldedY = shiftY * yieldProgress + (torsoBounce * 2.2) - (interceptWeight * 2.6) - walkBob + walkDip;

        return blendPoseTowardNeutral({
            angleX: direction * lerp(0, 7.1, react) + (shakePhase * 0.68 * direction * (1 - settle)) - (direction * 1.6 * settle) + ((naturalBodySway + shoulderTremor) * 0.74 * direction) - (direction * interceptWeight * 1.4) + (tiltSway * 0.18 * direction),
            angleY: lerp(0, -5.2, react) + (shakeEnvelope * 0.36) + (holdBob * 0.42 * (1 - settle * 0.74)) + (torsoBounce * 0.22) - (interceptWeight * 0.42) + (walkDip * 0.12),
            angleZ: (-direction * 6.8 * react) + (shakePhase * 0.92 * direction * (1 - settle)) + (direction * 1.2 * settle) + ((naturalBodySway * 0.92) + (shoulderTremor * 0.34)) * direction + (direction * interceptWeight * 1.2) + (tiltSway * 0.94 * direction),
            bodyAngleX: (-3.8 * react) + (shakeEnvelope * 0.32) + (settle * 0.92) + (holdBob * 0.38 * (1 - settle * 0.74)) + (torsoBounce * 0.52) - (interceptWeight * 1.1) + (Math.abs(tiltSway) * -0.18) - (walkBob * 0.06),
            bodyAngleY: direction * (4.4 * react + shakePhase * 0.52 * (1 - settle) - 0.7 * settle) + ((naturalBodySway * 0.58) + (shoulderTremor * 0.22)) * direction - (direction * interceptWeight * 1.2) + (tiltSway * 0.24 * direction),
            bodyAngleZ: (-direction * 7.9 * react) + (shakePhase * 1.14 * direction * (1 - settle)) + (direction * 1.52 * settle) + ((naturalBodySway * 1.18) + (shoulderTremor * 0.46)) * direction + (direction * interceptWeight * 1.54) + (tiltSway * 1.18 * direction),
            browRightY: 0.42 * expressionWeight,
            browLeftY: 0.42 * expressionWeight,
            browRightAngle: -8.4 * expressionWeight,
            browLeftAngle: 8.4 * expressionWeight,
            mouthForm: -0.36 * mouthWeight + expressionPulse * -0.04,
            cheek: 0.2 * expressionWeight,
            eyeSmileLeft: 0,
            eyeSmileRight: 0,
            yuiPanicMouthZ2: clamp(mouthWeight * 0.94 + expressionPulse * 0.36, 0, 1),
            yuiPanicEyesYyy: clamp(expressionWeight * 0.78 + shakeEnvelope * 0.08 + holdBob * 0.08, 0, 1),
            yuiSweat: clamp(expressionWeight, 0, 1),
            yuiSweatAnim: 5 * clamp(expressionWeight * 0.74 + holdBob * 0.14 + holdBobFast * 0.04 + shakeEnvelope * 0.04, 0, 1),
            yuiOuterSweatAnim1: clamp(expressionWeight * 0.82 + holdBob * 0.08 + holdWaveFast * 0.03, 0, 1),
            yuiMouthCoverSwitch: 0,
            yuiLeftMouthCoverAnim: 0,
            yuiRightWaveSwitch: clamp(leadRightArm * protectHold, 0, 1),
            yuiLeftWaveSwitch: clamp(leadLeftArm * protectHold, 0, 1),
            yuiRightForearmAnim: clamp((0.18 + leadRightArm * 0.58 + guardPulse * 0.08 + (direction < 0 ? handWobble : 0)) * protectHold, 0, 1),
            yuiLeftForearmAnim: clamp((0.16 + leadLeftArm * 0.6 + guardPulse * 0.08 + (direction > 0 ? handWobble : 0)) * protectHold, 0, 1),
            yuiRightHandAnim: clamp((0.14 + leadRightArm * 0.46 + guardPulse * 0.06 + (direction < 0 ? handWobble * 0.84 : 0)) * protectHold, 0, 1),
            yuiLeftHandAnim: clamp((0.12 + leadLeftArm * 0.48 + guardPulse * 0.06 + (direction > 0 ? handWobble * 0.84 : 0)) * protectHold, 0, 1),
            yuiRightHandWave: 0,
            yuiLeftHandWave: 0,
            yuiLeftEarPerspective: hairFollow * 0.38,
            yuiLeftEarRotate: hairFollow * 1.08,
            yuiLeftEarWiggle1: hairFollow * 1.22,
            yuiRightEarPerspective: hairFollow * 0.32,
            yuiRightEarRotate: hairFollow * 0.98,
            yuiRightEarWiggle1: hairFollow * 1.12,
            hairFront: hairFollow * 2.05,
            hairSide: hairFollow * 2.58,
            hairBack: hairFollow * 1.72,
            pendantX: clothFollow * 1.52,
            clothX1: clothFollow * 1.76,
            clothY1: (Math.abs(shakePhase) * 0.18) + (Math.abs(holdBob) * 0.28) + (Math.abs(holdBobFast) * 0.12) + (settle * 0.08),
            skirtX1: skirtFollow * 1.16,
            skirtX2: skirtFollow * 1.48,
            skirtX3: skirtFollow * 1.8,
            skirtX4: skirtFollow * 2.08,
            skirtY1: Math.abs(shakePhase) * 0.12 + Math.abs(holdBob) * 0.16 + Math.abs(holdBobFast) * 0.06,
            skirtY2: Math.abs(shakePhase) * 0.16 + Math.abs(holdBob) * 0.2 + Math.abs(holdBobFast) * 0.08,
            skirtY3: Math.abs(shakePhase) * 0.2 + Math.abs(holdBob) * 0.24 + Math.abs(holdBobFast) * 0.1,
            skirtY4: Math.abs(shakePhase) * 0.24 + Math.abs(holdBob) * 0.28 + Math.abs(holdBobFast) * 0.12,
            frameX: frameShakeX + yieldedX,
            frameY: frameShakeY + yieldedY
        }, settle);
    }

    function computeInterruptResistPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = reducedMotion ? 1 : clamp(progress, 0, 1);
        const pointerXNormalized = clamp(Number(context && context.pointerXNormalized) || 0, -1, 1);
        const pointerYNormalized = clamp(Number(context && context.pointerYNormalized) || 0, -1, 1);
        const direction = pointerXNormalized >= 0 ? 1 : -1;
        const enter = easeOutCubic(clamp(normalizedProgress / 0.12, 0, 1));
        const release = easeOutCubic(clamp((normalizedProgress - 0.86) / 0.14, 0, 1));
        const closeWeight = clamp(enter * (1 - release * 0.96), 0, 1);
        const focusRelease = easeOutCubic(clamp((normalizedProgress - 0.76) / 0.16, 0, 1));
        const lookWeight = clamp(enter * (1 - focusRelease * 0.52), 0, 1);
        const guardEnter = easeInOutCubic(clamp((normalizedProgress - 0.04) / 0.16, 0, 1));
        const guardExit = easeOutCubic(clamp((normalizedProgress - 0.84) / 0.14, 0, 1));
        const guardWeight = clamp(guardEnter * (1 - guardExit * 0.96), 0, 1);
        const dodgeEnter = easeInOutCubic(clamp((normalizedProgress - 0.12) / 0.18, 0, 1));
        const dodgeExit = easeOutCubic(clamp((normalizedProgress - 0.82) / 0.16, 0, 1));
        const dodgeWeight = clamp(dodgeEnter * (1 - dodgeExit), 0, 1);
        const settle = easeOutCubic(clamp((normalizedProgress - 0.88) / 0.12, 0, 1));
        const holdWindow = clamp((normalizedProgress - 0.08) / 0.74, 0, 1);
        const swayWave = Math.sin(holdWindow * Math.PI * 1.8);
        const swayWaveFast = Math.sin(holdWindow * Math.PI * 4.2);
        const bobWave = Math.sin(holdWindow * Math.PI * 2.1);
        const bobWaveFast = Math.sin(holdWindow * Math.PI * 5.1);
        const dodgeShiftX = Number(context && context.dodgeShiftX) || 0;
        const closeFrameY = Number(context && context.closeFrameY) || 0;
        const dodgeFrameY = Number(context && context.dodgeFrameY) || 0;
        const closeScaleDelta = Number.isFinite(Number(context && context.closeScaleDelta))
            ? Number(context.closeScaleDelta)
            : INTERRUPT_RESIST_BASE_SCALE;
        const handTremor = reducedMotion
            ? 0
            : ((swayWaveFast * 0.1) + (bobWaveFast * 0.06)) * guardWeight * (1 - settle * 0.9);
        const facialWeight = clamp(closeWeight * 0.92 + dodgeWeight * 0.44, 0, 1);
        const annoyedWeight = clamp(facialWeight * (0.9 + Math.abs(pointerXNormalized) * 0.08), 0, 1);
        const poutWeight = clamp(annoyedWeight * 1.06 + closeWeight * 0.14, 0, 1);
        const closeLean = closeWeight * (0.96 + Math.abs(pointerXNormalized) * 0.12);
        const dodgeLean = dodgeWeight * (0.72 + Math.abs(pointerXNormalized) * 0.12);
        const headSway = reducedMotion ? 0 : swayWave * 0.46 * (1 - settle * 0.84);
        const torsoSway = reducedMotion ? 0 : (swayWave * 0.72 + swayWaveFast * 0.18) * (1 - settle * 0.8);
        const bodyBob = reducedMotion ? 0 : (bobWave * 0.54 + bobWaveFast * 0.16) * (1 - settle * 0.82);
        const leadHandWeight = clamp(guardWeight * 0.96 + closeWeight * 0.06, 0, 1);
        const leadRight = direction > 0 ? leadHandWeight : 0;
        const leadLeft = direction > 0 ? 0 : leadHandWeight;
        const frameShakeX = reducedMotion
            ? 0
            : (swayWaveFast * 3.8 + swayWave * 1.2) * direction * dodgeWeight * (1 - settle * 0.82);
        const frameShakeY = reducedMotion
            ? 0
            : (
                (-Math.abs(bobWaveFast) * 2.6 * dodgeWeight)
                + (bobWave * -1.2 * dodgeWeight)
            ) * (1 - settle * 0.82);

        return blendPoseTowardNeutral({
            angleX: (pointerXNormalized * 7.8 * lookWeight) + (pointerXNormalized * 2.6 * dodgeWeight) + (headSway * 1.2 * direction),
            angleY: (pointerYNormalized * 5.4 * lookWeight) - (closeLean * 1.8) + (dodgeLean * 1.1) - (Math.abs(bobWaveFast) * 0.32 * dodgeWeight),
            angleZ: (-direction * 3.4 * closeLean) + (direction * 4.2 * dodgeLean) + (headSway * 1.6 * direction),
            eyeBallX: pointerXNormalized * 0.78 * lookWeight,
            eyeBallY: pointerYNormalized * 0.62 * lookWeight,
            bodyAngleX: (-4.4 * closeLean) + (2.6 * dodgeLean) - (Math.abs(bodyBob) * 0.36),
            bodyAngleY: (pointerXNormalized * 3.8 * lookWeight) - (direction * 2.2 * dodgeLean) + (torsoSway * 0.74 * direction),
            bodyAngleZ: (-direction * 4.8 * closeLean) + (direction * 5.8 * dodgeLean) + (torsoSway * 1.4 * direction),
            browRightY: -0.12 * annoyedWeight,
            browLeftY: -0.12 * annoyedWeight,
            browRightAngle: -8.6 * annoyedWeight,
            browLeftAngle: 8.6 * annoyedWeight,
            mouthForm: -0.1 * poutWeight,
            cheek: 0.16 * facialWeight,
            eyeSmileLeft: 0,
            eyeSmileRight: 0,
            yuiPanicMouthZ2: clamp(closeWeight * 0.86 + annoyedWeight * 0.12, 0, 1),
            yuiAnnoyedPoutZ3: clamp(poutWeight * 1.12, 0, 1),
            yuiRightWaveSwitch: clamp(leadRight, 0, 1),
            yuiLeftWaveSwitch: clamp(leadLeft, 0, 1),
            yuiRightForearmAnim: clamp((0.04 + leadRight * 0.74 + (direction > 0 ? handTremor : 0)) * (0.68 + closeWeight * 0.32), 0, 1),
            yuiLeftForearmAnim: clamp((0.04 + leadLeft * 0.74 + (direction < 0 ? handTremor : 0)) * (0.68 + closeWeight * 0.32), 0, 1),
            yuiRightHandAnim: clamp((0.04 + leadRight * 0.62 + (direction > 0 ? handTremor * 0.84 : 0)) * (0.66 + closeWeight * 0.34), 0, 1),
            yuiLeftHandAnim: clamp((0.04 + leadLeft * 0.62 + (direction < 0 ? handTremor * 0.84 : 0)) * (0.66 + closeWeight * 0.34), 0, 1),
            yuiRightHandWave: 0,
            yuiLeftHandWave: 0,
            frameX: (dodgeShiftX * dodgeWeight) + frameShakeX,
            frameY: (closeFrameY * closeWeight) + (dodgeFrameY * dodgeWeight) + frameShakeY,
            frameScale: 1 + (closeScaleDelta * closeWeight) - (closeScaleDelta * 0.18 * dodgeWeight)
        }, settle);
    }

    function computeAngryExitPose(progress, context) {
        const reducedMotion = !!(context && context.reducedMotion);
        const normalizedProgress = reducedMotion ? 1 : clamp(progress, 0, 1);
        const pointerXNormalized = clamp(Number(context && context.pointerXNormalized) || 0, -1, 1);
        const pointerYNormalized = clamp(Number(context && context.pointerYNormalized) || 0, -1, 1);
        const direction = pointerXNormalized === 0
            ? (Number(context && context.direction) < 0 ? -1 : 1)
            : (pointerXNormalized < 0 ? -1 : 1);
        const enter = easeOutCubic(clamp(normalizedProgress / 0.1, 0, 1));
        const hold = clamp((normalizedProgress - 0.08) / 0.82, 0, 1);
        const release = easeOutCubic(clamp((normalizedProgress - 0.94) / 0.06, 0, 1));
        const closeWeight = clamp(enter * (1 - release * 0.96), 0, 1);
        const angryWeight = clamp(closeWeight * (1 + Math.abs(pointerXNormalized) * 0.1), 0, 1);
        const focusWeight = clamp(enter * (1 - release * 0.78), 0, 1);
        const shakeEnvelope = Math.sin(clamp((normalizedProgress - 0.08) / 0.78, 0, 1) * Math.PI);
        const shakePhase = Math.sin(hold * Math.PI * 4.8);
        const shakePhaseFast = Math.sin(hold * Math.PI * 8.6);
        const stompWave = Math.sin(hold * Math.PI * 2.7);
        const poutPulse = Math.sin(hold * Math.PI * 1.7);
        const headSway = reducedMotion ? 0 : (shakePhase * 0.52 + shakePhaseFast * 0.16) * angryWeight * (1 - release * 0.84);
        const bodySway = reducedMotion ? 0 : (shakePhase * 1.24 + shakePhaseFast * 0.38) * angryWeight * (1 - release * 0.82);
        const bodyBob = reducedMotion ? 0 : (Math.abs(stompWave) * 1.34 + Math.abs(shakePhaseFast) * 0.46) * angryWeight * (1 - release * 0.86);
        const handTremor = reducedMotion ? 0 : (shakePhaseFast * 0.18 + shakePhase * 0.08) * angryWeight * (1 - release * 0.88);
        const frameShakeX = reducedMotion ? 0 : (shakePhase * 7.4 + shakePhaseFast * 2.8) * shakeEnvelope * direction * (1 - release * 0.84);
        const frameShakeY = reducedMotion ? 0 : -(Math.abs(stompWave) * 7.4 + Math.abs(shakePhaseFast) * 2.1) * (1 - release * 0.86);
        const closeFrameY = Number(context && context.closeFrameY) || 0;
        const closeScaleDelta = Number.isFinite(Number(context && context.closeScaleDelta))
            ? Number(context.closeScaleDelta)
            : 0.15;
        const expressionWeight = clamp(angryWeight * (1 + shakeEnvelope * 0.04), 0, 1);
        const mouthWeight = clamp(closeWeight * 0.76 + angryWeight * 0.28, 0, 1);
        const guardWeight = clamp(expressionWeight * 0.94, 0, 1);
        const armReachWeight = clamp(guardWeight * 0.9 + closeWeight * 0.12, 0, 1);
        const settleToIdle = easeInOutCubic(clamp((normalizedProgress - 0.8) / 0.2, 0, 1));

        return blendPoseTowardNeutral({
            angleX: (pointerXNormalized * 5.8 * focusWeight) + direction * (3.4 * angryWeight) + headSway * 1.04,
            angleY: (pointerYNormalized * 2.6 * focusWeight) - (5.6 * closeWeight) + bodyBob * 0.16,
            angleZ: (-direction * 6.2 * angryWeight) + bodySway * 0.64,
            eyeBallX: pointerXNormalized * 0.54 * focusWeight,
            eyeBallY: (-0.08 * closeWeight) + (pointerYNormalized * 0.18 * focusWeight),
            bodyAngleX: (-6.9 * closeWeight) - bodyBob * 0.3,
            bodyAngleY: (pointerXNormalized * 2.4 * focusWeight) + direction * (3 * angryWeight) + bodySway * 0.28,
            bodyAngleZ: (-direction * 8.2 * angryWeight) + bodySway * 0.94,
            browRightY: -0.22 * expressionWeight,
            browLeftY: -0.22 * expressionWeight,
            browRightAngle: -12.6 * expressionWeight,
            browLeftAngle: 12.6 * expressionWeight,
            mouthForm: -0.12 * mouthWeight - poutPulse * 0.03 * mouthWeight,
            cheek: 0.22 * expressionWeight,
            eyeSmileLeft: 0,
            eyeSmileRight: 0,
            yuiPanicMouthZ2: clamp(mouthWeight * 0.52 + shakeEnvelope * 0.04, 0, 1),
            yuiAnnoyedPoutZ3: clamp(expressionWeight * 1.02 + mouthWeight * 0.16 + Math.max(0, poutPulse) * 0.08, 0, 1),
            yuiAngryEyesWy: clamp(expressionWeight * 1.08, 0, 1),
            yuiRightWaveSwitch: clamp(guardWeight, 0, 1),
            yuiLeftWaveSwitch: clamp(guardWeight, 0, 1),
            yuiRightForearmAnim: clamp(0.18 + armReachWeight * 0.76 + handTremor, 0, 1),
            yuiLeftForearmAnim: clamp(0.18 + armReachWeight * 0.76 - handTremor, 0, 1),
            yuiRightHandAnim: clamp(0.16 + armReachWeight * 0.68 + handTremor * 0.88, 0, 1),
            yuiLeftHandAnim: clamp(0.16 + armReachWeight * 0.68 - handTremor * 0.88, 0, 1),
            yuiRightHandWave: 0,
            yuiLeftHandWave: 0,
            frameX: frameShakeX,
            frameY: (closeFrameY * closeWeight) + frameShakeY,
            frameScale: 1 + (closeScaleDelta * 1.08 * closeWeight)
        }, settleToIdle);
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
            scaleY: scaleY,
            rotation: Number.isFinite(Number(model.rotation)) ? Number(model.rotation) : 0
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
        if (Number.isFinite(Number(frame.rotation))) {
            model.rotation = Number(frame.rotation);
        }
        return true;
    }

    function readModelAlpha(model) {
        return Number.isFinite(Number(model && model.alpha)) ? Number(model.alpha) : 1;
    }

    function writeModelAlpha(model, alpha) {
        if (!model || !Number.isFinite(Number(alpha))) {
            return false;
        }
        model.alpha = clamp(Number(alpha), 0, 1);
        return true;
    }

    function freezeLive2DOverlayAnchors(manager) {
        if (!manager || !manager.pixi_app || !manager.pixi_app.ticker) {
            return null;
        }
        const ticker = manager.pixi_app.ticker;
        const frozen = {
            manager: manager,
            ticker: ticker,
            floatingButtonsTick: typeof manager._floatingButtonsTicker === 'function'
                ? manager._floatingButtonsTicker
                : null,
            lockIconTick: typeof manager._lockIconTicker === 'function'
                ? manager._lockIconTicker
                : null
        };

        if (frozen.floatingButtonsTick) {
            try {
                ticker.remove(frozen.floatingButtonsTick);
            } catch (_) {}
        }
        if (frozen.lockIconTick) {
            try {
                ticker.remove(frozen.lockIconTick);
            } catch (_) {}
        }
        return frozen;
    }

    function restoreLive2DOverlayAnchors(frozen) {
        if (!frozen || !frozen.manager || !frozen.ticker) {
            return;
        }
        if (frozen.floatingButtonsTick) {
            try {
                frozen.ticker.add(frozen.floatingButtonsTick);
                frozen.floatingButtonsTick();
            } catch (_) {}
        }
        if (frozen.lockIconTick) {
            try {
                frozen.ticker.add(frozen.lockIconTick);
                frozen.lockIconTick();
            } catch (_) {}
        }
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
            this.interruptSuspendedAt = 0;
            this.previousEyeBlinkSuspended = !!this.manager._suspendEyeBlinkOverride;
            this.poseOverrideSource = 'yui_guide_wakeup_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-wakeup';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_WAKEUP_PERFORMANCE_CAPABILITIES.slice();
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

            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
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
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
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
            if (syncSessionInterruptPause(this, performance.now())) {
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
            const now = performance.now();
            if (syncSessionInterruptPause(this, now)) {
                if (!this.ticker) {
                    this.frameId = window.requestAnimationFrame(this.tick);
                }
                return;
            }
            if (!this.isCurrentModel()) {
                this.stop('model_changed');
                return;
            }
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

    class Live2DReturnControlCueWaveSession extends Live2DWakeupSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            super(context, Object.assign({}, normalizedOptions, {
                durationMs: normalizeDuration(normalizedOptions.durationMs, RETURN_CONTROL_CUE_WAVE_DURATION_MS),
                performanceLockKey: normalizedOptions.performanceLockKey || 'home-yui-guide-return-control-wave',
                performanceLockCapabilities: YUI_RETURN_CONTROL_CUE_WAVE_CAPABILITIES.slice()
            }));
            this.poseOverrideSource = 'yui_guide_return_control_wave_' + this.token;
            const wakeupParams = this.params || {};
            this.params = {};
            [
                'yuiRightWaveSwitch',
                'yuiRightForearmAnim',
                'yuiRightHandAnim',
                'yuiRightHandWave'
            ].forEach((key) => {
                if (wakeupParams[key]) {
                    this.params[key] = wakeupParams[key];
                }
            });
        }

        isUsable() {
            return !!(
                this.params
                && (
                    this.params.yuiRightWaveSwitch
                    || this.params.yuiRightForearmAnim
                    || this.params.yuiRightHandAnim
                    || this.params.yuiRightHandWave
                )
            );
        }

        computePose(progress) {
            return computeWakeupRightHandWavePose(progress, {
                reducedMotion: this.reducedMotion
            });
        }

        applyPose(pose, weight) {
            const w = clamp(weight, 0, 1);
            this.writeWeighted('yuiRightWaveSwitch', pose.yuiRightWaveSwitch, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightWaveSwitch || 1));
            this.writeWeighted('yuiRightForearmAnim', pose.yuiRightForearmAnim, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightForearmAnim || 1));
            this.writeWeighted('yuiRightHandAnim', pose.yuiRightHandAnim, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightHandAnim || 1));
            this.writeWeighted('yuiRightHandWave', pose.yuiRightHandWave, w * (YUI_WAKEUP_POSE_BLEND_FACTORS.yuiRightHandWave || 1));
        }

        stop(reason, options) {
            super.stop(reason, options);
            if (activeReturnControlCueWaveSession === this) {
                activeReturnControlCueWaveSession = null;
            }
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
            this.entryBlendMs = normalizeDuration(normalizedOptions.entryBlendMs, INTRO_GREETING_HUG_BLEND_IN_MS);
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.interruptSuspendedAt = 0;
            this.poseOverrideSource = 'yui_guide_intro_greeting_hug_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.entryPose = null;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-intro-greeting';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_INTRO_PERFORMANCE_CAPABILITIES.slice();
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

            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
            this.active = true;
            this.startedAt = performance.now();
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            this.entryPose = this.captureEntryPose();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            this.applyPose(this.entryPose, 1);
            this.applyFrame(this.entryPose);
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
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
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
            this.writeWeighted('yuiRightForearmAnim', 0, 1);
            this.writeWeighted('yuiLeftForearmAnim', 0, 1);
            this.writeWeighted('yuiRightHandAnim', 0, 1);
            this.writeWeighted('yuiLeftHandAnim', 0, 1);
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
            this.writeWeighted('hairFront', 0, 1);
            this.writeWeighted('hairSide', 0, 1);
            this.writeWeighted('hairBack', 0, 1);
            this.writeWeighted('yuiRightPonytailY', 0, 1);
            this.writeWeighted('yuiRightBowX', 0, 1);
            this.writeWeighted('yuiRightBowY', 0, 1);
            this.writeWeighted('skirtX1', 0, 1);
            this.writeWeighted('skirtX2', 0, 1);
            this.writeWeighted('skirtX3', 0, 1);
            this.writeWeighted('skirtX4', 0, 1);
            this.writeWeighted('skirtY1', 0, 1);
            this.writeWeighted('skirtY2', 0, 1);
            this.writeWeighted('skirtY3', 0, 1);
            this.writeWeighted('skirtY4', 0, 1);
            this.writeWeighted('pendantX', 0, 1);
            this.writeWeighted('clothX1', 0, 1);
            this.writeWeighted('clothY1', 0, 1);
            YUI_INTRO_GIFT_HEART_LEG_PARAM_KEYS.forEach((key) => {
                this.writeWeighted(key, 0, 1);
            });
        }

        captureEntryPose() {
            const pose = readMappedPose(this.coreModel, YUI_INTRO_GREETING_HUG_PARAMS, {
                frameScale: 1,
                frameY: 0,
                frameX: 0
            });
            pose.frameScale = 1;
            pose.frameY = 0;
            pose.frameX = 0;
            return pose;
        }

        applyTemporaryPose(coreModel) {
            if (!this.active || coreModel !== this.coreModel || !this.isCurrentModel()) {
                return;
            }
            if (syncSessionInterruptPause(this, performance.now())) {
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
            if (this.entryPose && this.entryBlendMs > 0 && elapsed < this.entryBlendMs) {
                const entryProgress = easeInOutCubic(clamp(elapsed / this.entryBlendMs, 0, 1));
                pose = blendIntroGreetingHugPose(this.entryPose, pose, entryProgress);
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
            const now = performance.now();
            if (syncSessionInterruptPause(this, now)) {
                if (!this.ticker) {
                    this.frameId = window.requestAnimationFrame(this.tick);
                }
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
            this.interruptSuspendedAt = 0;
            this.poseOverrideSource = 'yui_guide_intro_gift_heart_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-intro-greeting';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_INTRO_PERFORMANCE_CAPABILITIES.slice();
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
            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
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
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
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
            if (syncSessionInterruptPause(this, performance.now())) {
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
            const now = performance.now();
            if (syncSessionInterruptPause(this, now)) {
                if (!this.ticker) {
                    this.frameId = window.requestAnimationFrame(this.tick);
                }
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
            const frame = this.getFrameState(now);
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
            this.writeWeighted('yuiRightForearmAnim', pose.yuiRightForearmAnim, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightForearmAnim || 1));
            this.writeWeighted('yuiLeftForearmAnim', pose.yuiLeftForearmAnim, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftForearmAnim || 1));
            this.writeWeighted('yuiRightHandAnim', pose.yuiRightHandAnim, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightHandAnim || 1));
            this.writeWeighted('yuiLeftHandAnim', pose.yuiLeftHandAnim, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiLeftHandAnim || 1));
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
            this.writeWeighted('hairFront', pose.hairFront, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.hairFront || 1));
            this.writeWeighted('hairSide', pose.hairSide, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.hairSide || 1));
            this.writeWeighted('hairBack', pose.hairBack, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.hairBack || 1));
            this.writeWeighted('yuiRightPonytailY', pose.yuiRightPonytailY, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightPonytailY || 1));
            this.writeWeighted('yuiRightBowX', pose.yuiRightBowX, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightBowX || 1));
            this.writeWeighted('yuiRightBowY', pose.yuiRightBowY, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.yuiRightBowY || 1));
            this.writeWeighted('skirtX1', pose.skirtX1, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtX1 || 1));
            this.writeWeighted('skirtX2', pose.skirtX2, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtX2 || 1));
            this.writeWeighted('skirtX3', pose.skirtX3, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtX3 || 1));
            this.writeWeighted('skirtX4', pose.skirtX4, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtX4 || 1));
            this.writeWeighted('skirtY1', pose.skirtY1, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtY1 || 1));
            this.writeWeighted('skirtY2', pose.skirtY2, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtY2 || 1));
            this.writeWeighted('skirtY3', pose.skirtY3, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtY3 || 1));
            this.writeWeighted('skirtY4', pose.skirtY4, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.skirtY4 || 1));
            this.writeWeighted('pendantX', pose.pendantX, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.pendantX || 1));
            this.writeWeighted('clothX1', pose.clothX1, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.clothX1 || 1));
            this.writeWeighted('clothY1', pose.clothY1, w * (YUI_INTRO_GIFT_HEART_POSE_BLEND_FACTORS.clothY1 || 1));
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

    class Live2DPluginDashboardCornerSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.hideMs = normalizeDuration(normalizedOptions.hideMs, PLUGIN_DASHBOARD_CORNER_HIDE_MS);
            this.appearMs = normalizeDuration(normalizedOptions.appearMs, PLUGIN_DASHBOARD_CORNER_APPEAR_MS);
            this.totalDurationMs = this.reducedMotion ? 0 : this.hideMs + this.appearMs;
            this.targetPreset = normalizedOptions.targetPreset === 'top_flipped'
                ? 'top_flipped'
                : 'corner';
            this.token = normalizedOptions.token || 0;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.initialModelFrame = null;
            this.initialAlpha = 1;
            this.initialBounds = null;
            this.hiddenFrame = null;
            this.cornerFrame = null;
            this.cornerHiddenFrame = null;
            this.originalContainerZIndex = null;
            this.containerZIndexElevated = false;
            this.floatingButtonsFreezeToken = null;
            this.floatingButtonsFrozen = false;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-plugin-dashboard-corner';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_PLUGIN_DASHBOARD_FRAME_CAPABILITIES.slice();
            this.phase = 'idle';
            this.tickerAttached = false;
            this.interruptSuspendedAt = 0;
            this.tick = this.tick.bind(this);
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
            if (!this.isCurrentModel()) {
                return false;
            }
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            if (!this.initialModelFrame) {
                return false;
            }
            this.initialAlpha = readModelAlpha(this.model);
            this.initialBounds = this.readBounds();
            this.hiddenFrame = this.resolveHiddenFrame();
            this.cornerFrame = this.resolveCornerFrame();
            this.cornerHiddenFrame = this.resolveCornerHiddenFrame();
            this.freezeFloatingButtonsPosition();
            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
            this.active = true;
            this.phase = 'enter';
            this.startedAt = performance.now();
            this.applyFrame(this.reducedMotion ? this.cornerFrame : this.initialModelFrame, this.reducedMotion ? 1 : this.initialAlpha);
            if (this.reducedMotion) {
                this.elevateContainerZIndex();
                return true;
            }
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.attachTicker();
            } else {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return true;
        }

        stop(reason) {
            return this.requestStop(reason || 'stopped', true);
        }

        requestStop(reason, animateReturn) {
            if (!this.active && this.finished) {
                return Promise.resolve();
            }
            if (this.reducedMotion || !animateReturn || !this.isCurrentModel()) {
                this.finish(reason || 'stopped');
                return Promise.resolve();
            }
            if (this.phase === 'exit') {
                return this.waitForFinish();
            }
            this.phase = 'exit';
            this.result = reason || this.result || 'stopped';
            this.startedAt = performance.now();
            this.active = true;
            this.applyFrame(this.cornerFrame, 1);
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.attachTicker();
            } else if (!this.frameId) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return this.waitForFinish();
        }

        finish(reason) {
            this.active = false;
            this.finished = true;
            this.phase = 'finished';
            this.result = reason || this.result || 'stopped';
            this.detachTicker();
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
            this.restoreFloatingButtonsPositionUpdates();
            this.restoreContainerZIndex();
            this.restoreModelFrame();
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
            }
            if (activePluginDashboardCornerSession === this) {
                activePluginDashboardCornerSession = null;
            }
        }

        attachTicker() {
            if (!this.ticker || typeof this.ticker.add !== 'function' || this.tickerAttached) {
                return;
            }
            this.ticker.add(this.tick);
            this.tickerAttached = true;
        }

        detachTicker() {
            if (!this.ticker || typeof this.ticker.remove !== 'function' || !this.tickerAttached) {
                return;
            }
            try {
                this.ticker.remove(this.tick);
            } catch (_) {}
            this.tickerAttached = false;
        }

        elevateContainerZIndex() {
            if (!this.container || !this.container.style || this.containerZIndexElevated) {
                return false;
            }
            this.originalContainerZIndex = this.container.style.zIndex;
            this.container.style.zIndex = PLUGIN_DASHBOARD_CORNER_ELEVATED_Z_INDEX;
            this.containerZIndexElevated = true;
            return true;
        }

        restoreContainerZIndex() {
            if (!this.container || !this.container.style || !this.containerZIndexElevated) {
                return false;
            }
            this.container.style.zIndex = this.originalContainerZIndex || '';
            this.originalContainerZIndex = null;
            this.containerZIndexElevated = false;
            return true;
        }

        freezeFloatingButtonsPosition() {
            if (!this.manager || this.floatingButtonsFrozen) {
                return false;
            }
            const token = this.floatingButtonsFreezeToken || {};
            this.floatingButtonsFreezeToken = token;
            if (!this.manager._floatingButtonsPositionFreezeTokens
                    || typeof this.manager._floatingButtonsPositionFreezeTokens.add !== 'function') {
                this.manager._floatingButtonsPositionFreezeTokens = new Set();
            }
            this.manager._floatingButtonsPositionFreezeTokens.add(token);
            this.manager._freezeFloatingButtonsPosition = true;
            this.floatingButtonsFrozen = true;
            return true;
        }

        restoreFloatingButtonsPositionUpdates() {
            if (!this.manager || !this.floatingButtonsFrozen) {
                return false;
            }
            const freezes = this.manager._floatingButtonsPositionFreezeTokens;
            if (freezes && typeof freezes.delete === 'function' && this.floatingButtonsFreezeToken) {
                freezes.delete(this.floatingButtonsFreezeToken);
            }
            this.manager._freezeFloatingButtonsPosition = !!(freezes && freezes.size > 0);
            this.floatingButtonsFreezeToken = null;
            this.floatingButtonsFrozen = false;
            return true;
        }

        cancel(reason) {
            return this.requestStop(reason || 'cancelled', false);
        }

        waitForFinish() {
            return new Promise((resolve) => {
                const poll = () => {
                    if (this.finished) {
                        resolve();
                        return;
                    }
                    window.requestAnimationFrame(poll);
                };
                window.requestAnimationFrame(poll);
            });
        }

        readBounds() {
            if (this.model && typeof this.model.getBounds === 'function') {
                try {
                    const bounds = this.model.getBounds();
                    if (bounds && bounds.width > 0 && bounds.height > 0) {
                        return {
                            x: Number(bounds.x) || 0,
                            y: Number(bounds.y) || 0,
                            width: Number(bounds.width) || 0,
                            height: Number(bounds.height) || 0
                        };
                    }
                } catch (_) {}
            }
            return null;
        }

        getViewportSize() {
            const screen = this.manager && this.manager.pixi_app && this.manager.pixi_app.renderer
                ? this.manager.pixi_app.renderer.screen
                : null;
            return {
                width: Math.max(1, Number(screen && screen.width) || window.innerWidth || 1),
                height: Math.max(1, Number(screen && screen.height) || window.innerHeight || 1)
            };
        }

        resolveHiddenFrame() {
            const base = this.initialModelFrame;
            const viewport = this.getViewportSize();
            const bounds = this.initialBounds;
            const downShift = bounds && bounds.height > 0 ? bounds.height * 0.72 : viewport.height * 0.55;
            return {
                x: base.x,
                y: base.y + Math.max(240, downShift),
                scaleX: base.scaleX,
                scaleY: base.scaleY,
                rotation: base.rotation
            };
        }

        resolveCornerFrame() {
            if (this.targetPreset === 'top_flipped') {
                return this.resolveTopFlippedFrame();
            }
            const base = this.initialModelFrame;
            const viewport = this.getViewportSize();
            const bounds = this.initialBounds || {
                x: base.x - viewport.width * 0.18,
                y: base.y - viewport.height * 0.55,
                width: viewport.width * 0.36,
                height: viewport.height * 0.7
            };
            const modelCenterOffsetX = (bounds.x + bounds.width * 0.5) - base.x;
            const modelCenterOffsetY = (bounds.y + bounds.height * 0.5) - base.y;
            const desiredCenterX = viewport.width + (bounds.width * PLUGIN_DASHBOARD_CORNER_RIGHT_OUTSIDE_RATIO);
            const desiredCenterY = viewport.height - Math.max(36, bounds.height * PLUGIN_DASHBOARD_CORNER_CENTER_ABOVE_BOTTOM_RATIO);
            return {
                x: desiredCenterX - modelCenterOffsetX,
                y: desiredCenterY - modelCenterOffsetY,
                scaleX: base.scaleX,
                scaleY: base.scaleY,
                rotation: base.rotation - (PLUGIN_DASHBOARD_CORNER_ROTATION_DEG * Math.PI / 180)
            };
        }

        resolveTopFlippedFrame() {
            const base = this.initialModelFrame;
            const viewport = this.getViewportSize();
            const bounds = this.initialBounds || {
                x: base.x - viewport.width * 0.18,
                y: base.y - viewport.height * 0.55,
                width: viewport.width * 0.36,
                height: viewport.height * 0.7
            };
            const modelCenterOffsetX = (bounds.x + bounds.width * 0.5) - base.x;
            const modelCenterOffsetY = (bounds.y + bounds.height * 0.5) - base.y;
            const desiredCenterX = viewport.width * 0.5;
            const desiredCenterY = -(bounds.height * TAKEOVER_TOP_PEEK_CENTER_ABOVE_TOP_RATIO);
            return {
                x: desiredCenterX - modelCenterOffsetX,
                y: desiredCenterY - modelCenterOffsetY,
                scaleX: base.scaleX,
                scaleY: base.scaleY,
                rotation: base.rotation + Math.PI
            };
        }

        resolveCornerHiddenFrame() {
            if (this.targetPreset === 'top_flipped') {
                return this.resolveTopFlippedHiddenFrame();
            }
            const viewport = this.getViewportSize();
            const bounds = this.initialBounds;
            const diagonalShift = bounds && bounds.height > 0
                ? Math.max(180, bounds.height * 0.42)
                : Math.max(180, viewport.height * 0.32);
            return {
                x: this.cornerFrame.x + diagonalShift,
                y: this.cornerFrame.y + diagonalShift,
                scaleX: this.cornerFrame.scaleX,
                scaleY: this.cornerFrame.scaleY,
                rotation: this.cornerFrame.rotation
            };
        }

        resolveTopFlippedHiddenFrame() {
            const viewport = this.getViewportSize();
            const bounds = this.initialBounds;
            const upwardShift = bounds && bounds.height > 0
                ? Math.max(180, bounds.height * TAKEOVER_TOP_PEEK_TOP_OUTSIDE_RATIO)
                : Math.max(180, viewport.height * 0.28);
            return {
                x: this.cornerFrame.x,
                y: this.cornerFrame.y - upwardShift,
                scaleX: this.cornerFrame.scaleX,
                scaleY: this.cornerFrame.scaleY,
                rotation: this.cornerFrame.rotation
            };
        }

        restoreModelFrame() {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return false;
            }
            writeModelAlpha(this.model, this.initialAlpha);
            return writeIntroGreetingHugModelFrame(this.model, this.initialModelFrame);
        }

        applyFrame(frame, alpha) {
            if (!this.isCurrentModel() || !frame) {
                return false;
            }
            writeModelAlpha(this.model, alpha);
            return writeIntroGreetingHugModelFrame(this.model, frame);
        }

        blendFrame(fromFrame, toFrame, weight) {
            return {
                x: lerp(fromFrame.x, toFrame.x, weight),
                y: lerp(fromFrame.y, toFrame.y, weight),
                scaleX: lerp(fromFrame.scaleX, toFrame.scaleX, weight),
                scaleY: lerp(fromFrame.scaleY, toFrame.scaleY, weight),
                rotation: lerp(
                    Number.isFinite(Number(fromFrame.rotation)) ? Number(fromFrame.rotation) : 0,
                    Number.isFinite(Number(toFrame.rotation)) ? Number(toFrame.rotation) : 0,
                    weight
                )
            };
        }

        tick() {
            if (!this.active) {
                return;
            }
            const now = performance.now();
            if (syncSessionInterruptPause(this, now)) {
                if (!this.ticker) {
                    this.frameId = window.requestAnimationFrame(this.tick);
                }
                return;
            }
            if (this.isCancelled()) {
                this.cancel('cancelled');
                return;
            }
            if (!this.isCurrentModel()) {
                this.cancel('model_changed');
                return;
            }
            const elapsed = Math.max(0, now - this.startedAt);
            if (this.phase === 'exit') {
                this.tickExit(elapsed);
            } else {
                this.tickEnter(elapsed);
            }

            if (!this.active) {
                return;
            }
            if (!this.ticker) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
        }

        tickEnter(elapsed) {
            if (elapsed <= this.hideMs) {
                const progress = this.hideMs > 0 ? easeInOutCubic(elapsed / this.hideMs) : 1;
                this.applyFrame(
                    this.blendFrame(this.initialModelFrame, this.hiddenFrame, progress),
                    lerp(this.initialAlpha, 0, progress)
                );
            } else if (elapsed <= this.totalDurationMs) {
                const progress = this.appearMs > 0 ? easeOutCubic((elapsed - this.hideMs) / this.appearMs) : 1;
                this.applyFrame(
                    this.blendFrame(this.cornerHiddenFrame, this.cornerFrame, progress),
                    lerp(0, 1, progress)
                );
            } else {
                this.phase = 'hold';
                this.applyFrame(this.cornerFrame, 1);
                this.elevateContainerZIndex();
            }
        }

        tickExit(elapsed) {
            if (elapsed <= this.hideMs) {
                const progress = this.hideMs > 0 ? easeInOutCubic(elapsed / this.hideMs) : 1;
                this.applyFrame(
                    this.blendFrame(this.cornerFrame, this.cornerHiddenFrame, progress),
                    lerp(1, 0, progress)
                );
            } else if (elapsed <= this.totalDurationMs) {
                const progress = this.appearMs > 0 ? easeOutCubic((elapsed - this.hideMs) / this.appearMs) : 1;
                this.applyFrame(
                    this.blendFrame(this.hiddenFrame, this.initialModelFrame, progress),
                    lerp(0, this.initialAlpha, progress)
                );
            } else {
                this.finish(this.result || 'stopped');
            }
        }
    }

    class Live2DSettingsPeekPanicSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.requestedTotalDurationMs = normalizeDuration(normalizedOptions.totalDurationMs, 0);
            if (this.requestedTotalDurationMs > 0) {
                if (this.reducedMotion) {
                    this.reactMs = 0;
                    this.shakeMs = 0;
                    this.settleMs = this.requestedTotalDurationMs;
                } else {
                    this.reactMs = Math.max(180, Math.round(this.requestedTotalDurationMs * 0.16));
                    this.shakeMs = Math.max(220, Math.round(this.requestedTotalDurationMs * 0.22));
                    this.settleMs = Math.max(
                        260,
                        this.requestedTotalDurationMs - this.reactMs - this.shakeMs
                    );
                }
            } else {
                this.reactMs = this.reducedMotion ? 0 : normalizeDuration(normalizedOptions.reactMs, SETTINGS_PEEK_PANIC_REACT_MS);
                this.shakeMs = this.reducedMotion ? 0 : normalizeDuration(normalizedOptions.shakeMs, SETTINGS_PEEK_PANIC_SHAKE_MS);
                this.settleMs = this.reducedMotion ? 260 : normalizeDuration(normalizedOptions.settleMs, SETTINGS_PEEK_PANIC_SETTLE_MS);
            }
            this.totalDurationMs = this.reactMs + this.shakeMs + this.settleMs;
            this.token = normalizedOptions.token || 0;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.targetRect = normalizedOptions.targetRect || null;
            this.params = scanMappedLive2DParams(this.coreModel, YUI_SETTINGS_PEEK_PANIC_PARAMS);
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.interruptSuspendedAt = 0;
            this.poseOverrideSource = 'yui_guide_settings_panic_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-settings-panic';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_INTRO_PERFORMANCE_CAPABILITIES.slice();
            this.preserveCursorLookAt = normalizedOptions.preserveCursorLookAt !== false;
            this.frozenOverlayAnchors = null;
            this.direction = -1;
            this.shiftX = -SETTINGS_PEEK_PANIC_MIN_SHIFT_PX;
            this.shiftY = 18;
            this.currentFramePose = null;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
        }

        isUsable() {
            return Object.keys(this.params || {}).length > 0;
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
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            if (!this.initialModelFrame) {
                return false;
            }
            const shiftMeta = this.resolveShiftMeta();
            this.direction = shiftMeta.direction;
            this.shiftX = shiftMeta.shiftX;
            this.shiftY = shiftMeta.shiftY;
            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
            this.frozenOverlayAnchors = freezeLive2DOverlayAnchors(this.manager);
            this.active = true;
            this.startedAt = performance.now();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            this.applyPose(this.computePose(0), 1);
            this.applyFrame(this.computePose(0));
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.ticker.add(this.tick);
            } else {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return true;
        }

        shouldPreserveLookAtRelease() {
            return this.preserveCursorLookAt
                && window.nekoYuiGuideIntroVoiceLookAtActive === true;
        }

        buildReleasePose(fromPose, progress) {
            const neutralPose = blendPoseTowardNeutral(fromPose, progress);
            if (this.shouldPreserveLookAtRelease()) {
                [
                    'angleX',
                    'angleY',
                    'angleZ',
                    'bodyAngleX',
                    'bodyAngleY',
                    'bodyAngleZ'
                ].forEach((key) => {
                    delete neutralPose[key];
                });
            }
            return neutralPose;
        }

        async animateRelease() {
            const durationMs = this.reducedMotion ? 0 : SETTINGS_PEEK_PANIC_RELEASE_MS;
            if (durationMs <= 0 || !this.isCurrentModel()) {
                return;
            }
            const fromPose = this.computePose(1);
            const fromFramePose = this.currentFramePose || fromPose;
            const startedAt = performance.now();
            await new Promise((resolve) => {
                const step = (now) => {
                    if (!this.isCurrentModel()) {
                        resolve();
                        return;
                    }
                    const progress = easeInOutCubic(clamp((now - startedAt) / durationMs, 0, 1));
                    const releasePose = this.buildReleasePose(fromPose, progress);
                    const releaseFrame = blendNumericPose(fromFramePose, { frameX: 0, frameY: 0 }, progress);
                    this.applyPose(releasePose, 1);
                    this.applyFrame(releaseFrame);
                    if (progress >= 1) {
                        resolve();
                        return;
                    }
                    window.requestAnimationFrame(step);
                };
                window.requestAnimationFrame(step);
            });
        }

        async stop(reason) {
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
            await this.animateRelease();
            if (this.manager) {
                this.clearTemporaryPoseOverride();
            }
            if (activeSettingsPeekPanicSession === this) {
                activeSettingsPeekPanicSession = null;
            }
            if (!this.shouldPreserveLookAtRelease()) {
                this.restoreCapturedParams();
            }
            this.restoreModelFrame();
            restoreLive2DOverlayAnchors(this.frozenOverlayAnchors);
            this.frozenOverlayAnchors = null;
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
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

        resolveShiftMeta() {
            const viewport = this.getViewportSize();
            const targetRect = this.targetRect && Number.isFinite(Number(this.targetRect.width)) && Number.isFinite(Number(this.targetRect.left))
                ? this.targetRect
                : null;
            const referenceCenterX = targetRect
                ? targetRect.left + targetRect.width / 2
                : (Number.isFinite(Number(this.initialModelFrame && this.initialModelFrame.x)) ? Number(this.initialModelFrame.x) : viewport.width / 2);
            const widthBasis = targetRect && Number.isFinite(Number(targetRect.width))
                ? Number(targetRect.width)
                : viewport.width * 0.32;
            const heightBasis = targetRect && Number.isFinite(Number(targetRect.height))
                ? Number(targetRect.height)
                : viewport.height * 0.22;
            const direction = referenceCenterX >= viewport.width / 2 ? -1 : 1;
            return {
                direction: direction,
                shiftX: direction * clamp(
                    (widthBasis * SETTINGS_PEEK_PANIC_SHIFT_RATIO) + (viewport.width * 0.02),
                    SETTINGS_PEEK_PANIC_MIN_SHIFT_PX,
                    SETTINGS_PEEK_PANIC_MAX_SHIFT_PX
                ),
                shiftY: clamp(heightBasis * 0.04, 8, 18)
            };
        }

        getViewportSize() {
            const screen = this.manager && this.manager.pixi_app && this.manager.pixi_app.renderer
                ? this.manager.pixi_app.renderer.screen
                : null;
            return {
                width: Math.max(1, Number(screen && screen.width) || window.innerWidth || 1),
                height: Math.max(1, Number(screen && screen.height) || window.innerHeight || 1)
            };
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
            this.writeWeighted('yuiPanicMouthZ2', 0, 1);
            this.writeWeighted('yuiPanicEyesYyy', 0, 1);
            this.writeWeighted('yuiSweat', 0, 1);
            this.writeWeighted('yuiSweatAnim', 0, 1);
            this.writeWeighted('yuiOuterSweatAnim1', 0, 1);
            this.writeWeighted('yuiMouthCoverSwitch', 0, 1);
            this.writeWeighted('yuiLeftMouthCoverAnim', 0, 1);
            this.writeWeighted('yuiRightWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftWaveSwitch', 0, 1);
            this.writeWeighted('yuiRightHandWave', 0, 1);
            this.writeWeighted('yuiLeftHandWave', 0, 1);
        }

        applyTemporaryPose(coreModel) {
            if (!this.active || coreModel !== this.coreModel || !this.isCurrentModel()) {
                return;
            }
            if (syncSessionInterruptPause(this, performance.now())) {
                return;
            }
            const frame = this.getFrameState(performance.now());
            this.applyPose(frame.pose, frame.weight);
        }

        getFrameState(now) {
            const elapsed = Math.max(0, now - this.startedAt);
            const duration = this.totalDurationMs > 0 ? this.totalDurationMs : 1;
            const progress = clamp(elapsed / duration, 0, 1);
            return {
                elapsed: elapsed,
                pose: this.computePose(progress),
                weight: 1,
                finished: elapsed >= duration
            };
        }

        tick() {
            if (!this.active) {
                return;
            }
            const now = performance.now();
            if (syncSessionInterruptPause(this, now)) {
                if (!this.ticker) {
                    this.frameId = window.requestAnimationFrame(this.tick);
                }
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
            const frame = this.getFrameState(now);
            this.currentFramePose = frame.pose;
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
            return computeSettingsPeekPanicPose(progress, {
                reducedMotion: this.reducedMotion,
                direction: this.direction,
                shiftX: this.shiftX,
                shiftY: this.shiftY
            });
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
            const skipLookAtPose = this.preserveCursorLookAt
                && window.nekoYuiGuideIntroVoiceLookAtActive === true;
            Object.keys(YUI_SETTINGS_PEEK_PANIC_PARAMS).forEach((key) => {
                if (!Object.prototype.hasOwnProperty.call(pose, key)) {
                    return;
                }
                if (
                    skipLookAtPose
                    && (
                        key === 'angleX'
                        || key === 'angleY'
                        || key === 'angleZ'
                        || key === 'bodyAngleX'
                        || key === 'bodyAngleY'
                        || key === 'bodyAngleZ'
                    )
                ) {
                    return;
                }
                const targetValue = key === 'cheek'
                    ? Math.max(cheekBase, pose.cheek || 0)
                    : pose[key];
                this.writeWeighted(key, targetValue, w * (YUI_SETTINGS_PEEK_PANIC_POSE_BLEND_FACTORS[key] || 1));
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
                scaleY: this.initialModelFrame.scaleY,
                rotation: this.initialModelFrame.rotation
            });
        }
    }

    class Live2DInterruptResistSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.totalDurationMs = this.reducedMotion
                ? INTERRUPT_RESIST_REDUCED_DURATION_MS
                : clamp(
                    normalizeDuration(normalizedOptions.totalDurationMs, INTERRUPT_RESIST_DURATION_MS),
                    INTERRUPT_RESIST_MIN_DURATION_MS,
                    INTERRUPT_RESIST_MAX_DURATION_MS
                );
            this.token = normalizedOptions.token || 0;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.pointerX = Number.isFinite(Number(normalizedOptions.pointerX))
                ? Number(normalizedOptions.pointerX)
                : null;
            this.pointerY = Number.isFinite(Number(normalizedOptions.pointerY))
                ? Number(normalizedOptions.pointerY)
                : null;
            this.params = scanMappedLive2DParams(this.coreModel, YUI_INTERRUPT_RESIST_PARAMS);
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.poseOverrideSource = 'yui_guide_interrupt_resist_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-interrupt-resist';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : ['frame', 'params'];
            this.frozenOverlayAnchors = null;
            this.pointerXNormalized = 0;
            this.pointerYNormalized = 0;
            this.dodgeShiftX = 0;
            this.closeFrameY = 0;
            this.dodgeFrameY = 0;
            this.closeScaleDelta = INTERRUPT_RESIST_BASE_SCALE;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
        }

        isUsable() {
            return Object.keys(this.params || {}).length > 0;
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
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            if (!this.initialModelFrame) {
                return false;
            }
            this.resolvePointerMeta();
            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
            this.frozenOverlayAnchors = freezeLive2DOverlayAnchors(this.manager);
            this.active = true;
            this.startedAt = performance.now();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            const initialPose = this.computePose(0);
            this.applyPose(initialPose, 1);
            this.applyFrame(initialPose);
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
            if (activeInterruptResistSession === this) {
                activeInterruptResistSession = null;
            }
            this.restoreCapturedParams();
            this.restoreModelFrame();
            restoreLive2DOverlayAnchors(this.frozenOverlayAnchors);
            this.frozenOverlayAnchors = null;
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
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

        getViewportSize() {
            const screen = this.manager && this.manager.pixi_app && this.manager.pixi_app.renderer
                ? this.manager.pixi_app.renderer.screen
                : null;
            return {
                width: Math.max(1, Number(screen && screen.width) || window.innerWidth || 1),
                height: Math.max(1, Number(screen && screen.height) || window.innerHeight || 1)
            };
        }

        resolvePointerMeta() {
            const viewport = this.getViewportSize();
            const centerX = viewport.width / 2;
            const centerY = viewport.height * 0.42;
            const pointerX = Number.isFinite(this.pointerX) ? this.pointerX : centerX;
            const pointerY = Number.isFinite(this.pointerY) ? this.pointerY : centerY;
            const pointerXNormalized = clamp((pointerX - centerX) / Math.max(180, viewport.width * 0.42), -1, 1);
            const pointerYNormalized = clamp((pointerY - centerY) / Math.max(160, viewport.height * 0.34), -1, 1);
            const direction = pointerXNormalized >= 0 ? 1 : -1;
            const closeScaleDelta = clamp(
                0.092 + Math.abs(pointerXNormalized) * 0.024,
                0.09,
                0.14
            );
            const hugShift = resolveIntroGreetingHugFrameShift(this.container);
            this.pointerXNormalized = pointerXNormalized;
            this.pointerYNormalized = pointerYNormalized;
            this.dodgeShiftX = -direction * clamp(
                viewport.width * (0.022 + Math.abs(pointerXNormalized) * 0.006),
                18,
                38
            );
            this.closeFrameY = clamp(
                hugShift * (closeScaleDelta / Math.max(0.01, INTRO_GREETING_HUG_CLOSE_SCALE - 1)),
                84,
                188
            );
            this.dodgeFrameY = clamp(viewport.height * 0.006, 4, 10);
            this.closeScaleDelta = closeScaleDelta;
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
            this.writeWeighted('yuiPanicMouthZ2', 0, 1);
            this.writeWeighted('yuiAnnoyedPoutZ3', 0, 1);
            this.writeWeighted('yuiRightWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftWaveSwitch', 0, 1);
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
            const duration = this.totalDurationMs > 0 ? this.totalDurationMs : 1;
            const progress = clamp(elapsed / duration, 0, 1);
            return {
                elapsed: elapsed,
                pose: this.computePose(progress),
                weight: 1,
                finished: elapsed >= duration
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
            return computeInterruptResistPose(progress, {
                reducedMotion: this.reducedMotion,
                pointerXNormalized: this.pointerXNormalized,
                pointerYNormalized: this.pointerYNormalized,
                dodgeShiftX: this.dodgeShiftX,
                closeFrameY: this.closeFrameY,
                dodgeFrameY: this.dodgeFrameY,
                closeScaleDelta: this.closeScaleDelta
            });
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
            Object.keys(YUI_INTERRUPT_RESIST_PARAMS).forEach((key) => {
                if (!Object.prototype.hasOwnProperty.call(pose, key)) {
                    return;
                }
                const targetValue = key === 'cheek'
                    ? Math.max(cheekBase, pose.cheek || 0)
                    : pose[key];
                this.writeWeighted(key, targetValue, w * (YUI_INTERRUPT_RESIST_POSE_BLEND_FACTORS[key] || 1));
            });
        }

        applyFrame(pose) {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return;
            }
            const frameX = Number.isFinite(Number(pose.frameX)) ? Number(pose.frameX) : 0;
            const frameY = Number.isFinite(Number(pose.frameY)) ? Number(pose.frameY) : 0;
            const frameScale = Number.isFinite(Number(pose.frameScale)) ? Number(pose.frameScale) : 1;
            const scaledFrame = resolveIntroGreetingHugModelFrame(
                this.initialModelFrame,
                this.manager,
                this.container,
                frameScale,
                frameY
            );
            if (!scaledFrame) {
                return;
            }
            writeIntroGreetingHugModelFrame(this.model, {
                x: scaledFrame.x + frameX,
                y: scaledFrame.y,
                scaleX: scaledFrame.scaleX,
                scaleY: scaledFrame.scaleY,
                rotation: this.initialModelFrame.rotation
            });
        }
    }

    class Live2DAngryExitSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.totalDurationMs = this.reducedMotion
                ? ANGRY_EXIT_REDUCED_DURATION_MS
                : clamp(
                    normalizeDuration(normalizedOptions.totalDurationMs, ANGRY_EXIT_DURATION_MS),
                    ANGRY_EXIT_MIN_DURATION_MS,
                    ANGRY_EXIT_MAX_DURATION_MS
                );
            this.token = normalizedOptions.token || 0;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.pointerX = Number.isFinite(Number(normalizedOptions.pointerX))
                ? Number(normalizedOptions.pointerX)
                : null;
            this.pointerY = Number.isFinite(Number(normalizedOptions.pointerY))
                ? Number(normalizedOptions.pointerY)
                : null;
            this.params = scanMappedLive2DParams(this.coreModel, YUI_ANGRY_EXIT_PARAMS);
            this.startedAt = 0;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.poseOverrideSource = 'yui_guide_angry_exit_' + this.token;
            this.usesTemporaryPoseOverride = false;
            this.initialModelFrame = null;
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-angry-exit';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : ['frame', 'params'];
            this.pointerDirection = 1;
            this.pointerXNormalized = 0;
            this.pointerYNormalized = 0;
            this.closeFrameY = 0;
            this.closeScaleDelta = 0.15;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
        }

        isUsable() {
            return Object.keys(this.params || {}).length > 0;
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
            this.initialModelFrame = readIntroGreetingHugModelFrame(this.model);
            if (!this.initialModelFrame) {
                return false;
            }
            this.resolveMeta();
            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
            this.active = true;
            this.startedAt = performance.now();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            const initialPose = this.computePose(0);
            this.applyPose(initialPose, 1);
            this.applyFrame(initialPose);
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
            if (activeAngryExitSession === this) {
                activeAngryExitSession = null;
            }
            this.restoreCapturedParams();
            this.restoreModelFrame();
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(reason || 'stopped');
                this.performanceLock = null;
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

        getViewportSize() {
            const screen = this.manager && this.manager.pixi_app && this.manager.pixi_app.renderer
                ? this.manager.pixi_app.renderer.screen
                : null;
            return {
                width: Math.max(1, Number(screen && screen.width) || window.innerWidth || 1),
                height: Math.max(1, Number(screen && screen.height) || window.innerHeight || 1)
            };
        }

        resolveMeta() {
            const viewport = this.getViewportSize();
            const centerX = viewport.width / 2;
            const centerY = viewport.height * 0.42;
            const pointerX = Number.isFinite(this.pointerX) ? this.pointerX : centerX;
            const pointerY = Number.isFinite(this.pointerY) ? this.pointerY : centerY;
            const normalizedPointerX = clamp((pointerX - centerX) / Math.max(180, viewport.width * 0.42), -1, 1);
            const normalizedPointerY = clamp((pointerY - centerY) / Math.max(160, viewport.height * 0.34), -1, 1);
            const hugShift = resolveIntroGreetingHugFrameShift(this.container);
            this.pointerXNormalized = normalizedPointerX;
            this.pointerYNormalized = normalizedPointerY;
            this.pointerDirection = normalizedPointerX >= 0 ? 1 : -1;
            this.closeScaleDelta = clamp(0.13 + Math.abs(normalizedPointerX) * 0.03, 0.13, 0.18);
            this.closeFrameY = clamp(
                hugShift * (this.closeScaleDelta / Math.max(0.01, INTRO_GREETING_HUG_CLOSE_SCALE - 1)),
                112,
                236
            );
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
            this.writeWeighted('yuiPanicMouthZ2', 0, 1);
            this.writeWeighted('yuiAnnoyedPoutZ3', 0, 1);
            this.writeWeighted('yuiAngryEyesWy', 0, 1);
            this.writeWeighted('yuiRightWaveSwitch', 0, 1);
            this.writeWeighted('yuiLeftWaveSwitch', 0, 1);
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
            const duration = this.totalDurationMs > 0 ? this.totalDurationMs : 1;
            const progress = clamp(elapsed / duration, 0, 1);
            return {
                elapsed: elapsed,
                pose: this.computePose(progress),
                weight: 1,
                finished: elapsed >= duration
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
            return computeAngryExitPose(progress, {
                reducedMotion: this.reducedMotion,
                pointerXNormalized: this.pointerXNormalized,
                pointerYNormalized: this.pointerYNormalized,
                direction: this.pointerDirection,
                closeFrameY: this.closeFrameY,
                closeScaleDelta: this.closeScaleDelta
            });
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
            Object.keys(YUI_ANGRY_EXIT_PARAMS).forEach((key) => {
                if (!Object.prototype.hasOwnProperty.call(pose, key)) {
                    return;
                }
                const targetValue = key === 'cheek'
                    ? Math.max(cheekBase, pose.cheek || 0)
                    : pose[key];
                this.writeWeighted(key, targetValue, w * (YUI_ANGRY_EXIT_POSE_BLEND_FACTORS[key] || 1));
            });
        }

        applyFrame(pose) {
            if (!this.isCurrentModel() || !this.initialModelFrame) {
                return;
            }
            const frameX = Number.isFinite(Number(pose.frameX)) ? Number(pose.frameX) : 0;
            const frameY = Number.isFinite(Number(pose.frameY)) ? Number(pose.frameY) : 0;
            const frameScale = Number.isFinite(Number(pose.frameScale)) ? Number(pose.frameScale) : 1;
            const scaledFrame = resolveIntroGreetingHugModelFrame(
                this.initialModelFrame,
                this.manager,
                this.container,
                frameScale,
                frameY
            );
            if (!scaledFrame) {
                return;
            }
            writeIntroGreetingHugModelFrame(this.model, {
                x: scaledFrame.x + frameX,
                y: scaledFrame.y,
                scaleX: scaledFrame.scaleX,
                scaleY: scaledFrame.scaleY,
                rotation: this.initialModelFrame.rotation
            });
        }
    }

    class Live2DIntroVoiceLookAtSession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.document = normalizedOptions.document || document;
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.container = normalizedOptions.container || getLive2DContainer(this.document);
            this.getPoint = typeof normalizedOptions.getPoint === 'function'
                ? normalizedOptions.getPoint
                : function () { return null; };
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.performanceLock = null;
            this.performanceLockKey = normalizedOptions.performanceLockKey || 'home-yui-guide-intro-voice-look-at';
            this.performanceLockCapabilities = Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_INTRO_VOICE_LOOK_AT_CAPABILITIES.slice();
            this.poseOverrideSource = 'home-yui-guide-intro-voice-look-at-' + (normalizedOptions.token || Date.now());
            this.continuationState = normalizedOptions.continuationState || null;
            this.params = scanMappedLive2DParams(this.coreModel, YUI_INTRO_VOICE_LOOK_AT_PARAMS);
            this.stage = null;
            this.stageSession = null;
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.frameId = 0;
            this.tickerAttached = false;
            this.introVoiceLookAtFlagEnabled = false;
            this.latestPoint = this.clonePoint(this.continuationState && this.continuationState.latestPoint);
            this.smoothedPoint = this.clonePoint(this.continuationState && this.continuationState.smoothedPoint);
            this.currentPose = this.clonePose(this.continuationState && this.continuationState.currentPose)
                || this.computeNeutralPose();
            this.lastTickAt = 0;
            this.usesTemporaryPoseOverride = false;
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
            this.tick = this.tick.bind(this);
        }

        clonePoint(point) {
            if (!point || !Number.isFinite(Number(point.x)) || !Number.isFinite(Number(point.y))) {
                return null;
            }
            return {
                x: Number(point.x),
                y: Number(point.y)
            };
        }

        clonePose(pose) {
            if (!pose || typeof pose !== 'object') {
                return null;
            }
            const clone = {};
            Object.keys(pose).forEach((key) => {
                const value = Number(pose[key]);
                clone[key] = Number.isFinite(value) ? value : 0;
            });
            return clone;
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

        createStage() {
            const api = window.AvatarPerformance;
            if (!api || typeof api.createLive2DPerformance !== 'function') {
                return null;
            }
            try {
                return api.createLive2DPerformance({
                    profile: {
                        lookAt: {
                            maxAngleX: 10,
                            maxAngleY: 6,
                            maxEyeX: 0.34,
                            maxEyeY: 0.24,
                            headWeight: 0.82,
                            eyeWeight: 1
                        }
                    },
                    driverOptions: {
                        managerResolver: () => this.manager,
                        containerResolver: () => this.container
                    }
                });
            } catch (_) {
                return null;
            }
        }

        normalizePoint(point) {
            return this.clonePoint(point);
        }

        enableIntroVoiceLookAtFlag() {
            if (this.introVoiceLookAtFlagEnabled) {
                return;
            }
            const current = Number(window.nekoYuiGuideIntroVoiceLookAtActiveCount || 0);
            window.nekoYuiGuideIntroVoiceLookAtActiveCount = Math.max(0, current) + 1;
            window.nekoYuiGuideIntroVoiceLookAtActive = true;
            this.introVoiceLookAtFlagEnabled = true;
        }

        releaseIntroVoiceLookAtFlag() {
            if (!this.introVoiceLookAtFlagEnabled) {
                return;
            }
            const current = Number(window.nekoYuiGuideIntroVoiceLookAtActiveCount || 0);
            const next = Math.max(0, current - 1);
            window.nekoYuiGuideIntroVoiceLookAtActiveCount = next;
            window.nekoYuiGuideIntroVoiceLookAtActive = next > 0;
            this.introVoiceLookAtFlagEnabled = false;
        }

        resetFocusController() {
            const model = this.model;
            const focusController = model
                && model.internalModel
                && model.internalModel.focusController
                ? model.internalModel.focusController
                : null;
            if (!focusController) {
                return;
            }
            try {
                focusController.targetX = 0;
                focusController.targetY = 0;
                if (Number.isFinite(Number(focusController.x))) {
                    focusController.x = 0;
                }
                if (Number.isFinite(Number(focusController.y))) {
                    focusController.y = 0;
                }
            } catch (_) {}
        }

        applyFocusPoint(point) {
            if (
                !point
                || !this.model
                || !Number.isFinite(Number(point.x))
                || !Number.isFinite(Number(point.y))
            ) {
                return;
            }
            this.invokeModelFocus(Number(point.x), Number(point.y));
        }

        invokeModelFocus(x, y) {
            if (!this.model) {
                return;
            }
            const focus = this.model['focus'];
            if (typeof focus !== 'function') {
                return;
            }
            try {
                focus.call(this.model, x, y);
            } catch (_) {}
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

        getLookAtOrigin() {
            const normalizePoint = function (point) {
                if (!point || !Number.isFinite(Number(point.x)) || !Number.isFinite(Number(point.y))) {
                    return null;
                }
                return {
                    x: Number(point.x),
                    y: Number(point.y)
                };
            };
            try {
                if (this.manager && typeof this.manager.getBubbleAnchorGeometryInfo === 'function') {
                    const geometry = this.manager.getBubbleAnchorGeometryInfo();
                    const headPoint = normalizePoint(
                        geometry && (geometry.headAnchor || geometry.rawHeadAnchor)
                    );
                    if (headPoint) {
                        return headPoint;
                    }
                }
            } catch (_) {}
            try {
                if (this.manager && typeof this.manager.getHeadScreenAnchor === 'function') {
                    const headPoint = normalizePoint(this.manager.getHeadScreenAnchor());
                    if (headPoint) {
                        return headPoint;
                    }
                }
            } catch (_) {}
            try {
                if (this.model && typeof this.model.getBounds === 'function') {
                    const bounds = this.model.getBounds();
                    if (
                        bounds
                        && Number.isFinite(Number(bounds.left))
                        && Number.isFinite(Number(bounds.right))
                        && Number.isFinite(Number(bounds.top))
                        && Number.isFinite(Number(bounds.bottom))
                        && Number(bounds.right) > Number(bounds.left)
                        && Number(bounds.bottom) > Number(bounds.top)
                    ) {
                        return {
                            x: (Number(bounds.left) + Number(bounds.right)) / 2,
                            y: (Number(bounds.top) + Number(bounds.bottom)) / 2
                        };
                    }
                }
            } catch (_) {}
            const rect = this.container && typeof this.container.getBoundingClientRect === 'function'
                ? this.container.getBoundingClientRect()
                : null;
            if (rect && rect.width > 0 && rect.height > 0) {
                return {
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2
                };
            }
            return {
                x: (window.innerWidth || 1) / 2,
                y: (window.innerHeight || 1) / 2
            };
        }

        computeLookAtPose(point) {
            const origin = this.getLookAtOrigin();
            const normalizeX = Math.max(96, (window.innerWidth || 1) * 0.22);
            const normalizeY = Math.max(96, (window.innerHeight || 1) * 0.24);
            const normX = clamp((point.x - origin.x) / normalizeX, -1, 1);
            const normY = clamp((origin.y - point.y) / normalizeY, -1, 1);
            return {
                angleX: normX * 24,
                angleY: normY * 14,
                angleZ: -normX * 6,
                eyeBallX: normX * 0.95,
                eyeBallY: normY * 0.68,
                bodyAngleX: normX * 7,
                bodyAngleY: normY * 4,
                bodyAngleZ: -normX * 2.8
            };
        }

        computeNeutralPose() {
            return {
                angleX: 0,
                angleY: 0,
                angleZ: 0,
                eyeBallX: 0,
                eyeBallY: 0,
                bodyAngleX: 0,
                bodyAngleY: 0,
                bodyAngleZ: 0
            };
        }

        blendPose(fromPose, toPose, weight) {
            return blendNumericPose(fromPose, toPose, weight);
        }

        getContinuationState() {
            return {
                latestPoint: this.clonePoint(this.latestPoint),
                smoothedPoint: this.clonePoint(this.smoothedPoint),
                currentPose: this.clonePose(this.currentPose)
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
            this.writeWeighted('angleX', pose.angleX, w);
            this.writeWeighted('angleY', pose.angleY, w);
            this.writeWeighted('angleZ', pose.angleZ, w);
            this.writeWeighted('eyeBallX', pose.eyeBallX, w);
            this.writeWeighted('eyeBallY', pose.eyeBallY, w);
            this.writeWeighted('bodyAngleX', pose.bodyAngleX, w);
            this.writeWeighted('bodyAngleY', pose.bodyAngleY, w);
            this.writeWeighted('bodyAngleZ', pose.bodyAngleZ, w);
        }

        applyTemporaryPose(coreModel) {
            if (!this.active || coreModel !== this.coreModel || !this.isCurrentModel()) {
                return;
            }
            this.applyPose(this.currentPose || this.computeNeutralPose(), 1);
        }

        start() {
            if (!this.isCurrentModel()) {
                return false;
            }
            this.performanceLock = acquireYuiGuidePerformanceLock(
                this.performanceLockKey,
                this.performanceLockCapabilities
            );
            this.stage = this.createStage();
            if (this.stage && typeof this.stage.acquire === 'function') {
                try {
                    this.stageSession = this.stage.acquire('home-yui-guide-intro-voice-look-at', {
                        priority: YUI_GUIDE_PERFORMANCE_PRIORITY,
                        force: true,
                        capabilities: this.performanceLockCapabilities.slice()
                    });
                } catch (_) {
                    this.stageSession = null;
                }
            }

            this.active = true;
            this.finished = false;
            this.result = 'playing';
            this.lastTickAt = performance.now();
            this.currentPose = this.clonePose(this.currentPose) || this.computeNeutralPose();
            this.enableIntroVoiceLookAtFlag();
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            this.applyCurrentPoint(this.lastTickAt);
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.attachTicker();
            } else {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return true;
        }

        applyCurrentPoint(now) {
            if (!this.active || !this.isCurrentModel()) {
                return false;
            }
            const currentNow = Number.isFinite(Number(now)) ? Number(now) : performance.now();
            const deltaMs = Math.max(0, currentNow - (this.lastTickAt || currentNow));
            this.lastTickAt = currentNow;
            const blendWeight = 1 - Math.pow(
                1 - INTRO_VOICE_LOOK_AT_SMOOTHING,
                Math.max(1, deltaMs / 16.67)
            );
            const point = this.normalizePoint(this.getPoint());
            if (point) {
                this.latestPoint = point;
                const lookAtOrigin = this.getLookAtOrigin();
                if (!this.smoothedPoint) {
                    this.smoothedPoint = {
                        x: lookAtOrigin && Number.isFinite(Number(lookAtOrigin.x))
                            ? Number(lookAtOrigin.x)
                            : point.x,
                        y: lookAtOrigin && Number.isFinite(Number(lookAtOrigin.y))
                            ? Number(lookAtOrigin.y)
                            : point.y
                    };
                }
                this.smoothedPoint = {
                    x: lerp(this.smoothedPoint.x, point.x, blendWeight),
                    y: lerp(this.smoothedPoint.y, point.y, blendWeight)
                };
            } else if (this.smoothedPoint) {
                this.smoothedPoint = {
                    x: lerp(this.smoothedPoint.x, this.getLookAtOrigin().x, blendWeight * 0.42),
                    y: lerp(this.smoothedPoint.y, this.getLookAtOrigin().y, blendWeight * 0.42)
                };
            }

            const targetPose = this.smoothedPoint
                ? this.computeLookAtPose(this.smoothedPoint)
                : this.computeNeutralPose();
            this.currentPose = this.blendPose(this.currentPose, targetPose, blendWeight);

            if (this.model && this.smoothedPoint) {
                this.invokeModelFocus(this.smoothedPoint.x, this.smoothedPoint.y);
            }
            if (!this.usesTemporaryPoseOverride) {
                this.applyPose(this.currentPose, 1);
            }
            return true;
        }

        attachTicker() {
            if (!this.ticker || typeof this.ticker.add !== 'function' || this.tickerAttached) {
                return;
            }
            this.ticker.add(this.tick);
            this.tickerAttached = true;
        }

        detachTicker() {
            if (!this.ticker || typeof this.ticker.remove !== 'function' || !this.tickerAttached) {
                return;
            }
            try {
                this.ticker.remove(this.tick);
            } catch (_) {}
            this.tickerAttached = false;
        }

        tick() {
            if (!this.active) {
                return;
            }
            if (this.isCancelled() || !this.isCurrentModel()) {
                this.stop('cancelled');
                return;
            }
            this.applyCurrentPoint(performance.now());
            if (!this.tickerAttached) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
        }

        async animateReleaseToNeutral() {
            const durationMs = INTRO_VOICE_LOOK_AT_RELEASE_MS;
            if (
                durationMs <= 0
                || !this.isCurrentModel()
                || typeof window.requestAnimationFrame !== 'function'
            ) {
                return;
            }
            const fromPose = this.currentPose ? Object.assign({}, this.currentPose) : this.computeNeutralPose();
            const neutralPose = this.computeNeutralPose();
            const originPoint = this.getLookAtOrigin();
            const fromPoint = this.clonePoint(this.smoothedPoint)
                || this.clonePoint(this.latestPoint)
                || this.clonePoint(originPoint);
            const startedAt = performance.now();
            await new Promise((resolve) => {
                const step = (now) => {
                    if (!this.isCurrentModel()) {
                        resolve();
                        return;
                    }
                    const progress = clamp((now - startedAt) / durationMs, 0, 1);
                    const easedProgress = easeInOutCubic(progress);
                    this.currentPose = this.blendPose(fromPose, neutralPose, easedProgress);
                    if (fromPoint && originPoint) {
                        const releasePoint = {
                            x: lerp(fromPoint.x, originPoint.x, easedProgress),
                            y: lerp(fromPoint.y, originPoint.y, easedProgress)
                        };
                        this.smoothedPoint = releasePoint;
                        this.latestPoint = releasePoint;
                        this.applyFocusPoint(releasePoint);
                    }
                    this.applyPose(this.currentPose, 1);
                    if (progress >= 1) {
                        this.smoothedPoint = this.clonePoint(originPoint);
                        this.latestPoint = this.clonePoint(originPoint);
                        resolve();
                        return;
                    }
                    window.requestAnimationFrame(step);
                };
                window.requestAnimationFrame(step);
            });
        }

        async stop(reason) {
            if (this.finished) {
                return Promise.resolve();
            }
            const normalizedReason = typeof reason === 'string' ? reason : '';
            const isHandoffStop = normalizedReason === 'replaced' || normalizedReason === 'handoff';
            this.active = false;
            this.finished = true;
            this.result = normalizedReason || this.result || 'stopped';
            this.detachTicker();
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
            if (!isHandoffStop) {
                await this.animateReleaseToNeutral();
            }
            if (this.stage && this.stageSession && this.stageSession.id) {
                try {
                    this.stage.release(this.stageSession.id, normalizedReason || 'stopped');
                } catch (_) {}
            }
            this.clearTemporaryPoseOverride();
            if (!isHandoffStop) {
                this.restoreCapturedParams();
                if (this.smoothedPoint) {
                    this.applyFocusPoint(this.smoothedPoint);
                }
                this.resetFocusController();
            }
            this.releaseIntroVoiceLookAtFlag();
            this.stageSession = null;
            this.stage = null;
            if (this.performanceLock && typeof this.performanceLock.release === 'function') {
                this.performanceLock.release(normalizedReason || 'stopped');
                this.performanceLock = null;
            }
            if (activeIntroVoiceLookAtSession === this) {
                activeIntroVoiceLookAtSession = null;
            }
            return Promise.resolve();
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

    function waitForSessionCompletion(session, resultBuilder) {
        return new Promise((resolve) => {
            const poll = () => {
                if (!session || session.finished) {
                    resolve(typeof resultBuilder === 'function' ? resultBuilder(session) : null);
                    return;
                }
                window.requestAnimationFrame(poll);
            };
            window.requestAnimationFrame(poll);
        });
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
                return waitForSessionCompletion(session, (finishedSession) => ({
                    result: finishedSession.result || 'played',
                    reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : '',
                    paramCount: Object.keys(finishedSession.params || {}).length
                }));
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

        return waitForSessionCompletion(session, (finishedSession) => ({
            result: finishedSession.result || 'played',
            reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : '',
            paramCount: Object.keys(finishedSession.params || {}).length
        }));
    }

    async function playReturnControlCueWave(options) {
        const normalizedOptions = options || {};
        const reducedMotion = !!normalizedOptions.reducedMotion;
        const waitMs = reducedMotion
            ? 0
            : normalizeDuration(normalizedOptions.readyWaitMs, RETURN_CONTROL_CUE_WAVE_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return { result: 'fallback', reason: 'live2d_unavailable' };
        }
        if (activeReturnControlCueWaveSession && activeReturnControlCueWaveSession.active) {
            if (!activeReturnControlCueWaveSession.isCurrentModel()) {
                activeReturnControlCueWaveSession.cancel('replaced');
            } else {
                const session = activeReturnControlCueWaveSession;
                return waitForSessionCompletion(session, (finishedSession) => ({
                    result: finishedSession.result || 'played',
                    reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : '',
                    paramCount: Object.keys(finishedSession.params || {}).length
                }));
            }
        }
        const session = new Live2DReturnControlCueWaveSession(context, {
            reducedMotion: reducedMotion,
            token: normalizedOptions.token || Date.now(),
            durationMs: reducedMotion ? 0 : normalizeDuration(normalizedOptions.durationMs, RETURN_CONTROL_CUE_WAVE_DURATION_MS)
        });
        if (!session.isUsable()) {
            return { result: 'fallback', reason: 'return_control_wave_unavailable' };
        }
        if (!session.start()) {
            return { result: 'fallback', reason: 'return_control_wave_start_failed' };
        }
        activeReturnControlCueWaveSession = session;

        return waitForSessionCompletion(session, (finishedSession) => ({
            result: finishedSession.result || 'played',
            reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : '',
            paramCount: Object.keys(finishedSession.params || {}).length
        }));
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
                return waitForSessionCompletion(session, (finishedSession) => ({
                    result: finishedSession.result || 'played',
                    reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : '',
                    paramCount: Object.keys(finishedSession.params || {}).length
                }));
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

        return waitForSessionCompletion(session, (finishedSession) => ({
            result: finishedSession.result || 'played',
            reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : '',
            paramCount: Object.keys(finishedSession.params || {}).length
        }));
    }

    async function startIntroVoiceCursorLookAt(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, LIVE2D_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return null;
        }
        let continuationState = normalizedOptions.continuationState || null;
        if (activeIntroVoiceLookAtSession && activeIntroVoiceLookAtSession.active) {
            if (typeof activeIntroVoiceLookAtSession.getContinuationState === 'function') {
                continuationState = activeIntroVoiceLookAtSession.getContinuationState();
            }
            await activeIntroVoiceLookAtSession.stop('replaced');
        }
        const session = new Live2DIntroVoiceLookAtSession(context, {
            document: normalizedOptions.document || document,
            getPoint: normalizedOptions.getPoint,
            isCancelled: normalizedOptions.isCancelled,
            continuationState: continuationState
        });
        if (!session.start()) {
            return null;
        }
        activeIntroVoiceLookAtSession = session;
        return {
            stop: function stopIntroVoiceCursorLookAt(reason) {
                return session.stop(reason || 'stopped');
            },
            isActive: function isIntroVoiceCursorLookAtActive() {
                return !!session.active;
            }
        };
    }

    class Live2DGuideIdleSwaySession {
        constructor(context, options) {
            const normalizedOptions = options || {};
            this.manager = context.manager;
            this.model = context.model;
            this.coreModel = context.coreModel;
            this.ticker = context.ticker || null;
            this.reducedMotion = !!normalizedOptions.reducedMotion;
            this.isCancelled = typeof normalizedOptions.isCancelled === 'function'
                ? normalizedOptions.isCancelled
                : function () { return false; };
            this.poseOverrideSource = 'home-yui-guide-idle-sway-' + (normalizedOptions.token || Date.now());
            this.entryBlendMs = normalizeDuration(normalizedOptions.entryBlendMs, GUIDE_IDLE_SWAY_BLEND_IN_MS);
            this.releaseMs = normalizeDuration(normalizedOptions.releaseMs, GUIDE_IDLE_SWAY_RELEASE_MS);
            this.params = scanMappedLive2DParams(this.coreModel, {
                angleX: 'ParamAngleX',
                angleY: 'ParamAngleY',
                angleZ: 'ParamAngleZ',
                bodyAngleX: 'ParamBodyAngleX',
                bodyAngleY: 'ParamBodyAngleY',
                bodyAngleZ: 'ParamBodyAngleZ'
            });
            this.active = false;
            this.finished = false;
            this.result = 'idle';
            this.startedAt = 0;
            this.frameId = 0;
            this.tickerAttached = false;
            this.usesTemporaryPoseOverride = false;
            this.currentPose = {
                angleX: 0,
                angleY: 0,
                angleZ: 0,
                bodyAngleX: 0,
                bodyAngleY: 0,
                bodyAngleZ: 0
            };
            this.entryPose = null;
            this.tick = this.tick.bind(this);
            this.applyTemporaryPose = this.applyTemporaryPose.bind(this);
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

        installTemporaryPoseOverride() {
            if (!this.manager || typeof this.manager.setTemporaryPoseOverride !== 'function') {
                return false;
            }
            try {
                return this.manager.setTemporaryPoseOverride(
                    this.poseOverrideSource,
                    this.applyTemporaryPose,
                    { priority: -100 }
                ) === true;
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

        writeWeighted(key, targetValue, weight) {
            const meta = this.params[key];
            if (!meta) {
                return;
            }
            const current = readParam(this.coreModel, meta);
            writeParam(this.coreModel, meta, lerp(current, targetValue, weight));
        }

        applyPose(pose, weight) {
            const w = clamp(weight, 0, 1);
            this.writeWeighted('angleX', pose.angleX, w);
            this.writeWeighted('angleY', pose.angleY, w);
            this.writeWeighted('angleZ', pose.angleZ, w);
            this.writeWeighted('bodyAngleX', pose.bodyAngleX, w);
            this.writeWeighted('bodyAngleY', pose.bodyAngleY, w);
            this.writeWeighted('bodyAngleZ', pose.bodyAngleZ, w);
        }

        getPose(now) {
            const swayPose = computeGuideIdleSwayPose(now, {
                startedAt: this.startedAt,
                reducedMotion: this.reducedMotion
            });
            const elapsed = Math.max(0, Number(now) - Number(this.startedAt || 0));
            if (!this.entryPose || this.entryBlendMs <= 0 || elapsed >= this.entryBlendMs) {
                return swayPose;
            }
            const progress = easeInOutCubic(clamp(elapsed / this.entryBlendMs, 0, 1));
            return blendNumericPose(this.entryPose, swayPose, progress);
        }

        applyTemporaryPose(coreModel, context) {
            if (!this.active || coreModel !== this.coreModel || !this.isCurrentModel()) {
                return;
            }
            const pose = this.getPose(context && context.now ? context.now : performance.now());
            this.currentPose = pose;
            this.applyPose(pose, 0.42);
        }

        start() {
            if (!this.isCurrentModel()) {
                return false;
            }
            this.active = true;
            this.finished = false;
            this.result = 'playing';
            this.startedAt = performance.now();
            this.entryPose = readMappedPose(this.coreModel, {
                angleX: 'ParamAngleX',
                angleY: 'ParamAngleY',
                angleZ: 'ParamAngleZ',
                bodyAngleX: 'ParamBodyAngleX',
                bodyAngleY: 'ParamBodyAngleY',
                bodyAngleZ: 'ParamBodyAngleZ'
            }, this.currentPose);
            this.usesTemporaryPoseOverride = this.installTemporaryPoseOverride();
            if (!this.usesTemporaryPoseOverride) {
                return false;
            }
            if (this.ticker && typeof this.ticker.add === 'function') {
                this.ticker.add(this.tick);
                this.tickerAttached = true;
            } else {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
            return true;
        }

        tick() {
            if (!this.active) {
                return;
            }
            if (this.isCancelled() || !this.isCurrentModel()) {
                this.stop(this.isCancelled() ? 'cancelled' : 'model_changed');
                return;
            }
            const pose = this.getPose(performance.now());
            this.currentPose = pose;
            if (!this.usesTemporaryPoseOverride) {
                this.applyPose(pose, 0.42);
            }
            if (!this.tickerAttached) {
                this.frameId = window.requestAnimationFrame(this.tick);
            }
        }

        detachTicker() {
            if (this.tickerAttached && this.ticker && typeof this.ticker.remove === 'function') {
                try {
                    this.ticker.remove(this.tick);
                } catch (_) {}
            }
            this.tickerAttached = false;
            if (this.frameId) {
                window.cancelAnimationFrame(this.frameId);
                this.frameId = 0;
            }
        }

        async animateRelease() {
            if (this.releaseMs <= 0 || !this.isCurrentModel()) {
                return;
            }
            const fromPose = Object.assign({}, this.currentPose);
            const startedAt = performance.now();
            await new Promise((resolve) => {
                const step = (now) => {
                    if (!this.isCurrentModel()) {
                        resolve();
                        return;
                    }
                    const progress = clamp((now - startedAt) / this.releaseMs, 0, 1);
                    const pose = blendPoseTowardNeutral(fromPose, easeInOutCubic(progress));
                    this.applyPose(pose, 0.42);
                    if (progress >= 1) {
                        resolve();
                        return;
                    }
                    window.requestAnimationFrame(step);
                };
                window.requestAnimationFrame(step);
            });
        }

        async stop(reason) {
            if (this.finished) {
                return;
            }
            this.active = false;
            this.finished = true;
            this.result = reason || 'stopped';
            this.detachTicker();
            await this.animateRelease();
            this.clearTemporaryPoseOverride();
            if (activeGuideIdleSwaySession === this) {
                activeGuideIdleSwaySession = null;
            }
        }
    }

    async function startGuideIdleSway(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, GUIDE_IDLE_SWAY_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return null;
        }
        if (activeGuideIdleSwaySession && activeGuideIdleSwaySession.active) {
            return {
                stop: function stopGuideIdleSway(reason) {
                    return activeGuideIdleSwaySession
                        ? activeGuideIdleSwaySession.stop(reason || 'stopped')
                        : Promise.resolve();
                },
                isActive: function isGuideIdleSwayActive() {
                    return !!(activeGuideIdleSwaySession && activeGuideIdleSwaySession.active);
                }
            };
        }
        const session = new Live2DGuideIdleSwaySession(context, {
            reducedMotion: !!normalizedOptions.reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            releaseMs: normalizedOptions.releaseMs
        });
        if (!session.start()) {
            return null;
        }
        activeGuideIdleSwaySession = session;
        return {
            stop: function stopGuideIdleSway(reason) {
                return session.stop(reason || 'stopped');
            },
            isActive: function isGuideIdleSwayActive() {
                return !!session.active;
            }
        };
    }

    async function startPluginDashboardCornerPeek(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, PLUGIN_DASHBOARD_CORNER_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return null;
        }
        if (activePluginDashboardCornerSession && activePluginDashboardCornerSession.active) {
            activePluginDashboardCornerSession.stop('replaced');
        }
        const session = new Live2DPluginDashboardCornerSession(context, {
            document: normalizedOptions.document || document,
            reducedMotion: !!normalizedOptions.reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            hideMs: normalizedOptions.hideMs,
            appearMs: normalizedOptions.appearMs,
            targetPreset: normalizedOptions.targetPreset
        });
        if (!session.start()) {
            return null;
        }
        activePluginDashboardCornerSession = session;
        return {
            stop: function stopPluginDashboardCornerPeek(reason) {
                return session.stop(reason || 'stopped');
            },
            isActive: function isPluginDashboardCornerPeekActive() {
                return !!session.active;
            }
        };
    }

    async function playSettingsPeekPanic(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, SETTINGS_PEEK_PANIC_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return { result: 'fallback', reason: 'live2d_unavailable' };
        }
        if (activeSettingsPeekPanicSession && activeSettingsPeekPanicSession.active) {
            if (!activeSettingsPeekPanicSession.isCurrentModel()) {
                activeSettingsPeekPanicSession.cancel('replaced');
            } else {
                const session = activeSettingsPeekPanicSession;
                return waitForSessionCompletion(session, (finishedSession) => ({
                    result: finishedSession.result || 'played',
                    reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : ''
                }));
            }
        }
        const session = new Live2DSettingsPeekPanicSession(context, {
            document: normalizedOptions.document || document,
            reducedMotion: !!normalizedOptions.reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            preserveCursorLookAt: normalizedOptions.preserveCursorLookAt !== false,
            performanceLockCapabilities: Array.isArray(normalizedOptions.performanceLockCapabilities)
                ? normalizedOptions.performanceLockCapabilities.slice()
                : YUI_SETTINGS_PEEK_PANIC_WITH_CURSOR_LOOK_AT_CAPABILITIES.slice(),
            targetRect: normalizedOptions.targetRect || null,
            totalDurationMs: normalizedOptions.totalDurationMs,
            reactMs: normalizedOptions.reactMs,
            shakeMs: normalizedOptions.shakeMs,
            settleMs: normalizedOptions.settleMs
        });
        if (!session.isUsable()) {
            return { result: 'fallback', reason: 'settings_panic_unavailable' };
        }
        if (!session.start()) {
            return { result: 'fallback', reason: 'settings_panic_start_failed' };
        }
        activeSettingsPeekPanicSession = session;

        return waitForSessionCompletion(session, (finishedSession) => ({
            result: finishedSession.result || 'played',
            reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : ''
        }));
    }

    async function playInterruptResist(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, INTERRUPT_RESIST_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return { result: 'fallback', reason: 'live2d_unavailable' };
        }
        if (activeInterruptResistSession && activeInterruptResistSession.active) {
            if (!activeInterruptResistSession.isCurrentModel()) {
                activeInterruptResistSession.cancel('replaced');
            } else {
                const session = activeInterruptResistSession;
                return waitForSessionCompletion(session, (finishedSession) => ({
                    result: finishedSession.result || 'played',
                    reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : ''
                }));
            }
        }
        const session = new Live2DInterruptResistSession(context, {
            document: normalizedOptions.document || document,
            reducedMotion: !!normalizedOptions.reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            pointerX: normalizedOptions.pointerX,
            pointerY: normalizedOptions.pointerY,
            totalDurationMs: normalizedOptions.totalDurationMs
        });
        if (!session.isUsable()) {
            return { result: 'fallback', reason: 'interrupt_resist_unavailable' };
        }
        if (!session.start()) {
            return { result: 'fallback', reason: 'interrupt_resist_start_failed' };
        }
        activeInterruptResistSession = session;

        return waitForSessionCompletion(session, (finishedSession) => ({
            result: finishedSession.result || 'played',
            reason: finishedSession.result && finishedSession.result !== 'played' ? finishedSession.result : ''
        }));
    }

    async function playAngryExit(options) {
        const normalizedOptions = options || {};
        const waitMs = normalizeDuration(normalizedOptions.readyWaitMs, ANGRY_EXIT_READY_WAIT_MS);
        const context = await waitForLive2DContext(waitMs);
        if (!context) {
            return { result: 'fallback', reason: 'live2d_unavailable' };
        }
        if (activeAngryExitSession && activeAngryExitSession.active) {
            activeAngryExitSession.cancel('replaced');
        }
        const session = new Live2DAngryExitSession(context, {
            document: normalizedOptions.document || document,
            reducedMotion: !!normalizedOptions.reducedMotion,
            token: normalizedOptions.token || Date.now(),
            isCancelled: normalizedOptions.isCancelled,
            pointerX: normalizedOptions.pointerX,
            pointerY: normalizedOptions.pointerY,
            totalDurationMs: normalizedOptions.totalDurationMs
        });
        if (!session.isUsable()) {
            return { result: 'fallback', reason: 'angry_exit_unavailable' };
        }
        if (!session.start()) {
            return { result: 'fallback', reason: 'angry_exit_start_failed' };
        }
        activeAngryExitSession = session;

        return new Promise((resolve) => {
            const poll = () => {
                if (session.finished) {
                    resolve({
                        result: session.result || 'played',
                        reason: session.result && session.result !== 'played' ? session.result : ''
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
        playReturnControlCueWave: playReturnControlCueWave,
        playIntroGiftHeart: playIntroGiftHeart,
        startIntroVoiceCursorLookAt: startIntroVoiceCursorLookAt,
        startGuideIdleSway: startGuideIdleSway,
        playSettingsPeekPanic: playSettingsPeekPanic,
        playInterruptResist: playInterruptResist,
        playAngryExit: playAngryExit,
        startPluginDashboardCornerPeek: startPluginDashboardCornerPeek,
        applyIntroGreetingHugFinalPlacement: applyIntroGreetingHugFinalPlacement,
        Live2DWakeupSession: Live2DWakeupSession,
        Live2DReturnControlCueWaveSession: Live2DReturnControlCueWaveSession,
        Live2DIntroGreetingHugSession: Live2DIntroGreetingHugSession,
        Live2DIntroGiftHeartSession: Live2DIntroGiftHeartSession,
        Live2DIntroVoiceLookAtSession: Live2DIntroVoiceLookAtSession,
        Live2DGuideIdleSwaySession: Live2DGuideIdleSwaySession,
        Live2DSettingsPeekPanicSession: Live2DSettingsPeekPanicSession,
        Live2DInterruptResistSession: Live2DInterruptResistSession,
        Live2DAngryExitSession: Live2DAngryExitSession,
        Live2DPluginDashboardCornerSession: Live2DPluginDashboardCornerSession,
        computeWakeupPose: computeWakeupPose,
        computeWakeupRightHandWavePose: computeWakeupRightHandWavePose,
        computeIntroGreetingHugPose: computeIntroGreetingHugPose,
        computeIntroGiftHeartPose: computeIntroGiftHeartPose,
        computeSettingsPeekPanicPose: computeSettingsPeekPanicPose,
        computeInterruptResistPose: computeInterruptResistPose,
        computeAngryExitPose: computeAngryExitPose,
        waitForLive2DContext: waitForLive2DContext,
        YUI_WAKEUP_PARAMS: YUI_WAKEUP_PARAMS,
        YUI_INTRO_GREETING_HUG_PARAMS: YUI_INTRO_GREETING_HUG_PARAMS,
        YUI_INTRO_GIFT_HEART_PARAMS: YUI_INTRO_GIFT_HEART_PARAMS,
        YUI_SETTINGS_PEEK_PANIC_PARAMS: YUI_SETTINGS_PEEK_PANIC_PARAMS,
        YUI_INTERRUPT_RESIST_PARAMS: YUI_INTERRUPT_RESIST_PARAMS,
        YUI_ANGRY_EXIT_PARAMS: YUI_ANGRY_EXIT_PARAMS
    });
})();
