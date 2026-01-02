import type { ExpressionRef, Live2DAdapter, Live2DCapabilities, Live2DEventSink, ModelRef, MotionRef, Transform, Vec2 } from "../types";

type AnyRecord = Record<string, any>;

export interface PixiLive2DAdapterOptions {
  /**
   * 画布与容器：
   * - 推荐直接传 Element（React 下更好）
   * - 也支持传入 elementId（内部用 document.getElementById 查找）
   */
  canvas: HTMLCanvasElement | string;
  container: HTMLElement | string;

  /**
   * PIXI / Live2DModel 注入方式（优先级：显式传入 > window.PIXI > 抛错）
   * - PIXI 需要包含 PIXI.Application、PIXI.Point，以及 PIXI.live2d.Live2DModel
   */
  PIXI?: any;
  Live2DModel?: any;

  /**
   * 创建 PIXI.Application 的配置补充
   */
  appOptions?: AnyRecord;

  /**
   * Live2DModel.from 的附加参数（取决于使用的 live2d 显示库）
   */
  modelOptions?: AnyRecord;

  /**
   * 默认 anchor（可选）
   */
  defaultAnchor?: Vec2;
}

function getElementByIdOrThrow<T extends HTMLElement>(id: string, expected: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`[live2d-service:web] 找不到 ${expected} 元素: #${id}`);
  return el as T;
}

function resolveCanvas(opt: HTMLCanvasElement | string): HTMLCanvasElement {
  if (typeof opt === "string") return getElementByIdOrThrow<HTMLCanvasElement>(opt, "canvas");
  return opt;
}

function resolveContainer(opt: HTMLElement | string): HTMLElement {
  if (typeof opt === "string") return getElementByIdOrThrow<HTMLElement>(opt, "container");
  return opt;
}

function getGlobalPIXI(): any {
  try {
    return (globalThis as any).PIXI;
  } catch {
    return undefined;
  }
}

function isMobileWidth(): boolean {
  try {
    if (typeof window === "undefined") return false;
    return window.matchMedia?.("(max-width: 768px)")?.matches ?? window.innerWidth <= 768;
  } catch {
    return false;
  }
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

function setModelScale(model: any, scale: number | Vec2) {
  if (!model || !model.scale) return;
  if (typeof scale === "number") {
    model.scale.set(scale);
  } else {
    model.scale.set(scale.x, scale.y);
  }
}

function setModelPosition(model: any, position: Vec2) {
  if (!model) return;
  if (Number.isFinite(position.x)) model.x = position.x;
  if (Number.isFinite(position.y)) model.y = position.y;
}

function getCoreModel(model: any): any | null {
  return model?.internalModel?.coreModel ?? null;
}

function safeGetClientSize(containerEl: HTMLElement, fallback: { w: number; h: number }) {
  const w = Number(containerEl?.clientWidth) || fallback.w;
  const h = Number(containerEl?.clientHeight) || fallback.h;
  return { w, h };
}

function safeInitialResize(app: any, containerEl: HTMLElement) {
  try {
    const renderer = app?.renderer;
    if (!renderer || typeof renderer.resize !== "function") return;
    const { w, h } = safeGetClientSize(containerEl, { w: Number(renderer.width) || 0, h: Number(renderer.height) || 0 });
    if (w > 0 && h > 0) renderer.resize(w, h);
  } catch {
    // ignore
  }
}

function applyDefaultLayout(model: any, app: any, containerEl: HTMLElement) {
  if (!model || !app) return;

  // 参考 legacy `static/live2d-core.js` 的默认布局策略：
  // - desktop: 右下角 (x=renderer.width, y=renderer.height)
  // - mobile: 居中偏上 (x=width*0.5, y=height*0.28)
  // 同时增加按容器 fit 的兜底，避免不同模型尺寸差异导致看不见。
  try {
    const renderer = app.renderer;
    const rw = Number(renderer?.width) || 0;
    const rh = Number(renderer?.height) || 0;
    const { w: cw, h: ch } = safeGetClientSize(containerEl, { w: rw || 360, h: rh || 520 });

    // 1) fit scale（更通用）
    let fitScale = 0;
    try {
      const mw = Number(model.width) || 0;
      const mh = Number(model.height) || 0;
      if (mw > 0 && mh > 0) {
        fitScale = Math.min(cw / mw, ch / mh) * 0.9;
      }
    } catch {
      // ignore
    }

    // 2) legacy 经验 scale（兜底/上限）
    let legacyScale = 0.25;
    try {
      const vw = typeof window !== "undefined" ? window.innerWidth : cw;
      const vh = typeof window !== "undefined" ? window.innerHeight : ch;
      if (isMobileWidth()) {
        legacyScale = Math.min(0.5, (vh * 1.3) / 4000, (vw * 1.2) / 2000);
      } else {
        legacyScale = Math.min(0.5, (vh * 0.75) / 7000, (vw * 0.6) / 7000);
      }
    } catch {
      // ignore
    }

    const raw = fitScale > 0 ? fitScale : legacyScale;
    const scale = Math.max(0.0001, Math.min(0.5, raw || 0.25));
    setModelScale(model, scale);

    // position（非常关键：不设 x/y 时模型可能完全在可视区外）
    if (isMobileWidth()) {
      setModelPosition(model, { x: (Number(renderer?.width) || cw) * 0.5, y: (Number(renderer?.height) || ch) * 0.28 });
    } else {
      setModelPosition(model, { x: Number(renderer?.width) || cw, y: Number(renderer?.height) || ch });
    }
  } catch {
    // ignore
  }
}

function setMouthCoreParams(coreModel: any, value: number) {
  if (!coreModel) return;
  // 与你们 static/live2d-model.js 的约定保持一致
  const ids = ["ParamMouthOpenY", "ParamO"];
  for (const id of ids) {
    try {
      const idx = coreModel.getParameterIndex(id);
      if (idx >= 0) {
        // pixi-live2d-display 对 coreModel 的 API 在不同版本有差异：
        // - 有的支持 setParameterValueById(id, value, weight?)
        // - 有的只支持 setParameterValueByIndex
        if (typeof coreModel.setParameterValueById === "function") {
          coreModel.setParameterValueById(id, value, 1);
        } else if (typeof coreModel.setParameterValueByIndex === "function") {
          coreModel.setParameterValueByIndex(idx, value);
        }
      }
    } catch {
      // best-effort
    }
  }
}

export function createPixiLive2DAdapter(options: PixiLive2DAdapterOptions): Live2DAdapter {
  const capabilities: Live2DCapabilities = {
    motions: true,
    expressions: true,
    mouth: true,
    transform: true,
    // parameters: 暂不对外承诺（后续加入叠加层/常驻表情等再打开）
    parameters: false,
  };

  const canvasEl = resolveCanvas(options.canvas);
  const containerEl = resolveContainer(options.container);

  const PIXI = options.PIXI ?? getGlobalPIXI();
  if (!PIXI) {
    throw new Error(
      "[live2d-service:web] PIXI 未提供。请通过 options.PIXI 注入，或确保页面已加载并暴露 window.PIXI。"
    );
  }

  // 兼容两种注入方式：显式 Live2DModel / PIXI.live2d.Live2DModel
  const Live2DModel = options.Live2DModel ?? PIXI?.live2d?.Live2DModel;
  if (!Live2DModel || typeof Live2DModel.from !== "function") {
    throw new Error(
      "[live2d-service:web] Live2DModel 未找到。请注入 options.Live2DModel，或确保 PIXI.live2d.Live2DModel 可用。"
    );
  }

  let sink: Live2DEventSink | null = null;
  let app: any | null = null;
  let model: any | null = null;

  const ensureApp = () => {
    if (app) return app;

    // 参考 static/live2d-core.js 的初始化策略：透明背景 + resizeTo container
    const safeAppOptions: Record<string, unknown> = { ...(options.appOptions ?? {}) };
    // PIXI v7+ 已弃用 `transparent`，并且不允许用户通过 appOptions 重新引入该配置。
    if ("transparent" in safeAppOptions) {
      delete (safeAppOptions as any).transparent;
    }

    app = new PIXI.Application({
      view: canvasEl,
      resizeTo: containerEl,
      autoStart: true,
      ...(safeAppOptions ?? {}),
      // 显式覆盖：确保透明背景（alpha=0）
      backgroundAlpha: 0,
    });
    return app;
  };

  const detachModel = () => {
    if (!app || !model) return;
    try {
      app.stage?.removeChild?.(model);
    } catch {
      // ignore
    }
    try {
      model.destroy?.({ children: true });
    } catch {
      // ignore
    }
    model = null;
  };

  const adapter: Live2DAdapter = {
    platform: "web",
    capabilities,

    setEventSink(nextSink) {
      sink = nextSink;
    },

    async loadModel(modelRef: ModelRef) {
      const pixiApp = ensureApp();
      // 强制一次初始 resize，避免 renderer.width/height 为 0 导致布局计算跑偏
      safeInitialResize(pixiApp, containerEl);

      // 切换模型时先清理旧模型（保持行为确定）
      detachModel();

      const nextModel = await Live2DModel.from(modelRef.uri, {
        autoFocus: false,
        ...(options.modelOptions ?? {}),
      });

      model = nextModel;

      // 默认 anchor（可选）
      if (options.defaultAnchor && model?.anchor?.set) {
        try {
          model.anchor.set(options.defaultAnchor.x, options.defaultAnchor.y);
        } catch {
          // ignore
        }
      }

      // 基础交互：tap → service 事件（坐标为 Pixi global）
      try {
        model.interactive = true;
        model.on?.("pointertap", (e: any) => {
          const p = e?.data?.global;
          if (!p) return;
          sink?.("tap", { x: Number(p.x) || 0, y: Number(p.y) || 0 });
        });
      } catch {
        // ignore
      }

      pixiApp.stage.addChild(model);

      // 默认布局（非常关键）：不设置 x/y/scale 时，模型可能完全在可视区之外
      applyDefaultLayout(model, pixiApp, containerEl);
    },

    async unloadModel() {
      detachModel();
      // 不销毁 app，允许复用 canvas（如需彻底销毁用 dispose）
    },

    async playMotion(motion: MotionRef) {
      if (!model) throw new Error("[live2d-service:web] playMotion: 模型未加载");
      if (!model.motion) throw new Error("[live2d-service:web] playMotion: 当前 Live2DModel 不支持 motion()");

      // pixi-live2d-display 的 motion API：motion(group, index?, priority?)
      // 但不同版本签名可能略有差异，因此采用 best-effort 调用。
      const group = motion.group;
      const index = typeof motion.index === "number" ? motion.index : undefined;
      const priority = typeof motion.priority === "number" ? motion.priority : undefined;

      try {
        if (index !== undefined && priority !== undefined) {
          await model.motion(group, index, priority);
        } else if (index !== undefined) {
          await model.motion(group, index);
        } else {
          await model.motion(group);
        }
      } catch (e) {
        // 上抛给 service，由 service 发 error/state
        throw e;
      }
    },

    async setExpression(expression: ExpressionRef) {
      if (!model) throw new Error("[live2d-service:web] setExpression: 模型未加载");
      if (!model.expression) throw new Error("[live2d-service:web] setExpression: 当前 Live2DModel 不支持 expression()");
      await model.expression(expression.id);
    },

    setMouthValue(value: number) {
      if (!model) return;
      const core = getCoreModel(model);
      setMouthCoreParams(core, clamp01(value));
    },

    async setTransform(transform: Transform) {
      if (!model) throw new Error("[live2d-service:web] setTransform: 模型未加载");
      if (transform.position) setModelPosition(model, transform.position);
      if (transform.scale !== undefined) setModelScale(model, transform.scale);
    },

    dispose() {
      detachModel();
      if (app) {
        try {
          // destroy 会移除 ticker / WebGL 资源
          app.destroy?.(true, { children: true });
        } catch {
          // ignore
        }
        app = null;
      }
      sink = null;
    },
  };

  return adapter;
}

