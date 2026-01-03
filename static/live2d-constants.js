/**
 * Live2D Constants - 共享常量定义
 */

// 口型同步参数列表常量
// 这些参数用于控制模型的嘴部动作，在处理表情和常驻表情时需要跳过，以避免覆盖实时的口型同步
export const LIPSYNC_PARAMS = [
    'ParamMouthOpenY',
    'ParamMouthForm',
    'ParamMouthOpen',
    'ParamA',
    'ParamI',
    'ParamU',
    'ParamE',
    'ParamO'
];
