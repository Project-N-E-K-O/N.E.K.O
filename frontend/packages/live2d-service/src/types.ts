export type Live2DPlatform = "web" | "native" | "unknown";

export type Live2DModelSource = "url" | "file" | "asset" | "id";

export interface ModelRef {
  /**
   * 模型标识：
   * - web: URL（例如 /static/mao_pro/mao_pro.model3.json）
   * - native: file:// 或 assets 内路径（由 adapter 解释）
   */
  uri: string;
  source?: Live2DModelSource;
  id?: string;
  meta?: Record<string, unknown>;
}

export interface MotionRef {
  /**
   * 统一寻址：motion group + index
   * - web adapter 可自行映射到 motion3.json 或 SDK group
   * - native adapter 直接对应 Cubism Motion Group
   */
  group: string;
  index?: number;
  priority?: number;
}

export interface ExpressionRef {
  /**
   * 统一寻址：expression id / name
   * - web adapter 可自行映射到 exp3.json 文件或 expression name
   * - native adapter 通常直接是 exp 名称（去掉 .exp3.json 后缀）
   */
  id: string;
}

export interface Vec2 {
  x: number;
  y: number;
}

export interface Transform {
  /**
   * 注意：不同平台的坐标系可能不同（像素/逻辑坐标/锚点坐标），由 adapter 决定解释方式。
   */
  position?: Vec2;

  /**
   * 统一缩放：优先使用 uniform scale（number）。
   * 若平台需要非等比缩放，可用 Vec2。
   */
  scale?: number | Vec2;
}

export type Live2DStatus = "idle" | "loading" | "ready" | "error";

export interface Live2DError {
  code: string;
  message: string;
  cause?: unknown;
}

export interface Live2DState {
  status: Live2DStatus;
  model?: ModelRef;
  error?: Live2DError;
}

export interface Live2DEvents {
  stateChanged: { prev: Live2DState; next: Live2DState };
  modelLoaded: { model: ModelRef };
  modelUnloaded: { prevModel?: ModelRef };
  tap: { x: number; y: number };
  motionFinished: { motion?: MotionRef };
  error: Live2DError;
}

export type Live2DEventSink = <K extends keyof Live2DEvents>(event: K, payload: Live2DEvents[K]) => void;

export interface Live2DCapabilities {
  /**
   * 适配器是否支持 parameter 层（ParamAngleX/ParamMouthOpenY 等参数写入）
   * 后续用于对齐 web 的“参数叠加/常驻表情保护”等能力。
   */
  parameters?: boolean;
  expressions?: boolean;
  motions?: boolean;
  mouth?: boolean;
  transform?: boolean;
}

export interface Live2DAdapter {
  platform: Live2DPlatform;
  capabilities?: Live2DCapabilities;

  /**
   * 平台运行时访问口（可选）：
   * - Web: 可暴露 Pixi/Live2DModel 的 bounds/parameters 等能力
   * - Native: 可暴露 Cubism 参数读写、bounds 等能力
   *
   * 该接口用于实现“旧版 Live2DManager 等级”的功能（参数编辑、吸附等），
   * 但不要求所有 adapter 实现。
   */
  getRuntime?: () => import("./runtime").Live2DRuntime | null;

  /**
   * service 会把事件 sink 注入到 adapter，adapter 收到平台事件后应调用 sink 上报。
   * （例如 RN view 的 onTap/onModelLoaded；Web 的 hit-test/tap 等）
   */
  setEventSink?: (sink: Live2DEventSink) => void;

  loadModel: (model: ModelRef, options?: Record<string, unknown>) => Promise<void>;
  unloadModel?: () => Promise<void>;

  playMotion?: (motion: MotionRef) => Promise<void>;
  setExpression?: (expression: ExpressionRef) => Promise<void>;

  setMouthValue?: (value: number) => void;
  setTransform?: (transform: Transform) => Promise<void>;

  /**
   * RN 场景：adapter 可以返回 ViewProps（由上层组件透传给 Native View）
   * Web 场景：通常不需要。
   */
  getViewProps?: () => Record<string, unknown>;

  dispose?: () => void | Promise<void>;
}

export interface Live2DService {
  getState: () => Live2DState;
  on: <K extends keyof Live2DEvents>(event: K, handler: (payload: Live2DEvents[K]) => void) => () => void;

  loadModel: (model: ModelRef, options?: Record<string, unknown>) => Promise<void>;
  unloadModel: () => Promise<void>;

  playMotion: (motion: MotionRef) => Promise<void>;
  setExpression: (expression: ExpressionRef) => Promise<void>;

  setMouthValue: (value: number) => void;
  setTransform: (transform: Transform) => Promise<void>;

  /**
   * 适配 RN 的声明式渲染：用于拿到当前 view props。
   * - 若 adapter 未实现则返回空对象。
   */
  getViewProps: () => Record<string, unknown>;

  dispose: () => Promise<void>;
}

