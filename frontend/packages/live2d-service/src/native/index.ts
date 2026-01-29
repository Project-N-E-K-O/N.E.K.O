/**
 * Native 侧 adapter 入口（占位）
 *
 * 说明：
 * - 后续将适配 @N.E.K.O.-RN/packages/react-native-live2d
 * - 此处不引入 react-native / expo 依赖，避免污染 web bundle
 */

export type { Live2DAdapter } from "../types";

/**
 * Native adapter 需要的最小能力契约（由宿主注入，不在本包内直接依赖具体 RN 模块）。
 *
 * 你们当前 `react-native-live2d`（expo module）已实现其中一部分：
 * - startMotion / setExpression / setMouthValue / getMouthValue ✅
 * - getAvailableModels / getAvailableMotions / getAvailableExpressions ✅（偏文件系统/默认值）
 *
 * 为了对齐 legacy Live2DManager “参数系统/叠加/编辑器”能力，还需要补齐：
 * - setParameters / getParameterValueById / getParameterIds / getParameterCount 等（见上次清单）
 */
export interface NativeLive2DModuleLike {
  initializeLive2D?: () => Promise<boolean>;
  startMotion: (motionGroup: string, motionIndex: number, priority: number) => Promise<boolean>;
  setExpression: (expressionId: string) => Promise<boolean>;
  setMouthValue: (value: number) => void;
  getMouthValue?: () => number;
}


