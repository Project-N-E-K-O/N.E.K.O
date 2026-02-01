import type { Transform, Vec2 } from "./types";

export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
  width: number;
  height: number;
}

export interface TransformSnapshot {
  position: Vec2;
  scale: Vec2;
}

export interface Live2DParametersRuntime {
  /**
   * 设置单个参数（按 Id）
   *
   * - Web: 通过 coreModel.setParameterValueById / setParameterValueByIndex
   * - Native: 通过 Cubism 参数 API
   */
  setParameterValueById: (id: string, value: number, weight?: number) => void;

  /**
   * 读取单个参数（按 Id）；若不支持返回 null。
   */
  getParameterValueById?: (id: string) => number | null;

  /**
   * 读取参数默认值（用于 additive offset 策略）；若不支持返回 null。
   */
  getParameterDefaultValueById?: (id: string) => number | null;

  /**
   * 获取参数总数；若不支持返回 null。
   */
  getParameterCount?: () => number | null;

  /**
   * 获取参数 Id 列表；若不支持可返回空数组（或使用 param_{i} 兜底命名）。
   */
  getParameterIds?: () => string[];

  /**
   * 安装参数覆盖层（可选）：
   * 用于对齐 legacy Live2DManager 的“口型优先级 + 参数叠加/覆盖 + 常驻表情保护”等能力。
   *
   * - Web: 推荐通过覆盖 motionManager.update / coreModel.update（best-effort）
   * - Native: 可通过渲染循环回调或 SDK hook 实现
   */
  installOverrideLayer?: (
    getState: () => {
      mouthValue: number;
      mode: "off" | "override" | "additive";
      savedParameters: Record<string, number> | null;
      persistentParameters: Record<string, number> | null;
    }
  ) => void;

  uninstallOverrideLayer?: () => void;
}

/**
 * Live2DRuntime：平台相关的“运行时能力”访问口。
 *
 * 设计目标：
 * - 让上层（Live2DManager / UI / 参数编辑器）在不强耦合底层 SDK 的情况下拿到必要能力
 * - 保持“可选”与“best-effort”，避免把 Web 的 Pixi/DOM 细节污染到 core
 */
export interface Live2DRuntime {
  /**
   * 读取当前 Transform（position/scale），用于偏好保存/吸附/编辑器。
   */
  getTransformSnapshot?: () => TransformSnapshot | null;

  /**
   * 写入 Transform（等价于 service.setTransform），用于交互层。
   */
  setTransform?: (transform: Transform) => Promise<void>;

  /**
   * 读取模型在当前渲染坐标系下的 bounds，用于吸附/命中/调试。
   */
  getBounds?: () => Rect | null;

  /**
   * 参数读写能力（对齐 legacy Live2DManager 的参数编辑/叠加能力）。
   */
  parameters?: Live2DParametersRuntime;
}

