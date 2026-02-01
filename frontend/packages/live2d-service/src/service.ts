import { TinyEmitter } from "@project_neko/common";
import type {
  Live2DAdapter,
  Live2DEventSink,
  Live2DEvents,
  Live2DError,
  Live2DService,
  Live2DState,
  ModelRef,
  MotionRef,
  ExpressionRef,
  Transform,
} from "./types";

function toError(code: string, message: string, cause?: unknown): Live2DError {
  return { code, message, cause };
}

function ensureFiniteNumber(value: number, name: string) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(`[live2d-service] ${name} 必须是有限数值，收到: ${String(value)}`);
  }
}

export function createLive2DService(adapter: Live2DAdapter, opts?: { onEventHandlerError?: TinyEmitter<Live2DEvents>["onError"] }): Live2DService {
  const emitter = new TinyEmitter<Live2DEvents>({ onError: opts?.onEventHandlerError });

  let state: Live2DState = { status: "idle" };

  const setState = (next: Live2DState) => {
    const prev = state;
    state = next;
    emitter.emit("stateChanged", { prev, next });
  };

  const sink: Live2DEventSink = (event, payload) => {
    // 适配器上报事件时，service 不做复杂逻辑（避免跨端差异），仅透传 + 按需更新 state
    if (event === "error") {
      const err = payload as Live2DError;
      setState({ status: "error", model: state.model, error: err });
    }
    emitter.emit(event, payload as any);
  };

  try {
    adapter.setEventSink?.(sink);
  } catch (e) {
    // adapter 事件注入失败不应阻断 service 创建
    // 上层可通过 error 监听到后续操作失败
  }

  const api: Live2DService = {
    getState: () => state,

    on: (event, handler) => emitter.on(event, handler as any),

    async loadModel(model: ModelRef, options?: Record<string, unknown>) {
      if (!model || typeof model !== "object" || typeof model.uri !== "string") {
        const err = toError("INVALID_MODEL_REF", "model 必须是包含 uri: string 的对象");
        setState({ status: "error", error: err });
        emitter.emit("error", err);
        throw new TypeError(err.message);
      }

      setState({ status: "loading", model });
      try {
        await adapter.loadModel(model, options);
        setState({ status: "ready", model });
        emitter.emit("modelLoaded", { model });
      } catch (cause) {
        const err = toError("MODEL_LOAD_FAILED", `加载模型失败: ${model.uri}`, cause);
        setState({ status: "error", model, error: err });
        emitter.emit("error", err);
        throw cause;
      }
    },

    async unloadModel() {
      const prevModel = state.model;
      try {
        await adapter.unloadModel?.();
      } finally {
        setState({ status: "idle" });
        emitter.emit("modelUnloaded", { prevModel });
      }
    },

    async playMotion(motion: MotionRef) {
      if (!adapter.playMotion) {
        throw new Error("[live2d-service] 当前 adapter 不支持 playMotion()");
      }
      if (!motion || typeof motion !== "object" || typeof motion.group !== "string") {
        throw new TypeError("[live2d-service] motion 必须是包含 group: string 的对象");
      }
      return await adapter.playMotion(motion);
    },

    async setExpression(expression: ExpressionRef) {
      if (!adapter.setExpression) {
        throw new Error("[live2d-service] 当前 adapter 不支持 setExpression()");
      }
      if (!expression || typeof expression !== "object" || typeof expression.id !== "string") {
        throw new TypeError("[live2d-service] expression 必须是包含 id: string 的对象");
      }
      return await adapter.setExpression(expression);
    },

    setMouthValue(value: number) {
      if (!adapter.setMouthValue) {
        throw new Error("[live2d-service] 当前 adapter 不支持 setMouthValue()");
      }
      ensureFiniteNumber(value, "mouthValue");
      adapter.setMouthValue(Math.max(0, Math.min(1, value)));
    },

    async setTransform(transform: Transform) {
      if (!adapter.setTransform) {
        throw new Error("[live2d-service] 当前 adapter 不支持 setTransform()");
      }
      if (!transform || typeof transform !== "object") {
        throw new TypeError("[live2d-service] transform 必须是对象");
      }
      return await adapter.setTransform(transform);
    },

    getViewProps() {
      try {
        return adapter.getViewProps?.() ?? {};
      } catch {
        return {};
      }
    },

    async dispose() {
      try {
        await api.unloadModel();
      } catch (_) {
        // best-effort
      }
      try {
        await adapter.dispose?.();
      } catch (_) {
        // best-effort
      }
    },
  };

  return api;
}

