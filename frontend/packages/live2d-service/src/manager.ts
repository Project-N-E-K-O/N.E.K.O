import { createLive2DService } from "./service";
import type {
  ExpressionRef,
  Live2DAdapter,
  Live2DEvents,
  Live2DService,
  Live2DState,
  MotionRef,
  Transform,
} from "./types";
import type { Live2DRuntime, TransformSnapshot } from "./runtime";

export type Live2DModelUri = string;

export interface Live2DPreferencesSnapshot {
  modelUri: Live2DModelUri;
  position?: { x: number; y: number };
  scale?: { x: number; y: number };
  parameters?: Record<string, number>;
}

export interface Live2DPreferencesRepository {
  load: (modelUri: Live2DModelUri) => Promise<Live2DPreferencesSnapshot | null>;
  save: (snapshot: Live2DPreferencesSnapshot) => Promise<void>;
}

export type EmotionMapping = {
  motions?: Record<string, string[]>;
  expressions?: Record<string, string[]>;
};

export interface EmotionMappingProvider {
  /**
   * 传入模型 uri，返回该模型的 emotion mapping（如后端 /api/live2d/emotion_mapping/...）
   */
  getEmotionMapping: (modelUri: Live2DModelUri) => Promise<EmotionMapping | null>;
}

export interface Live2DInteractionOptions {
  drag?: boolean;
  wheelZoom?: boolean;
  pinchZoom?: boolean;
  tap?: boolean;
}

export interface Live2DManager {
  /**
   * 暴露底层 service（便于逐步迁移 legacy 代码）。
   * 上层应优先使用 Live2DManager 的语义化接口，而不是直接操作 service。
   */
  readonly service: Live2DService;

  /**
   * 暴露运行时能力（可选），用于参数编辑/吸附/调试。
   */
  getRuntime: () => Live2DRuntime | null;

  // === state/events ===
  getState: () => Live2DState;
  on: <K extends keyof Live2DEvents>(event: K, handler: (payload: Live2DEvents[K]) => void) => () => void;

  // === model lifecycle ===
  loadModel: (modelUri: Live2DModelUri, opts?: { preferences?: Live2DPreferencesSnapshot | null }) => Promise<void>;
  unloadModel: () => Promise<void>;

  // === transform / prefs ===
  getTransformSnapshot: () => TransformSnapshot | null;
  setTransform: (transform: Transform) => Promise<void>;
  resetModelPosition: () => Promise<void>;
  savePreferences: () => Promise<void>;
  loadPreferences: (modelUri: Live2DModelUri) => Promise<Live2DPreferencesSnapshot | null>;

  // === motions/expressions/emotion ===
  playMotion: (motion: MotionRef) => Promise<void>;
  setExpression: (expression: ExpressionRef) => Promise<void>;
  setEmotion: (emotion: string) => Promise<void>;
  clearExpression: () => Promise<void>;
  clearEmotionEffects: () => Promise<void>;

  // === mouth ===
  setMouth: (value01: number) => void;

  // === parameters (best-effort) ===
  applyModelParameters: (params: Record<string, number>) => Promise<void>;
  setSavedModelParameters: (params: Record<string, number> | null) => Promise<void>;
  setPersistentParameters: (params: Record<string, number> | null) => Promise<void>;
  setParameterOverrideMode: (mode: "off" | "override" | "additive") => void;

  // === interaction (best-effort) ===
  setLocked: (locked: boolean) => void;
  setInteractionOptions: (opts: Live2DInteractionOptions) => void;
  snapToScreen: () => Promise<boolean>;

  dispose: () => Promise<void>;
}

export interface CreateLive2DManagerOptions {
  onEventHandlerError?: Parameters<typeof createLive2DService>[1] extends { onEventHandlerError?: infer T } ? T : unknown;
  preferences?: Live2DPreferencesRepository;
  emotionMappingProvider?: EmotionMappingProvider;
}

function clamp01(v: number) {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(1, v));
}

function randomPick<T>(arr: T[]): T | null {
  if (!arr || arr.length === 0) return null;
  const i = Math.floor(Math.random() * arr.length);
  return arr[i] ?? null;
}

/**
 * createLive2DManager：跨端门面层（对齐 legacy Live2DManager 的语义）。
 *
 * 说明：
 * - 该实现会优先使用 Live2DService（跨端内核）
 * - 对于参数/吸附等“平台能力”，通过 adapter.getRuntime() best-effort 获取
 * - 更复杂的 UI/交互（浮动按钮/HUD 等）应由宿主（Web/RN App）实现，不放在这里
 */
export function createLive2DManager(adapter: Live2DAdapter, options?: CreateLive2DManagerOptions): Live2DManager {
  const service = createLive2DService(adapter, { onEventHandlerError: options?.onEventHandlerError as any });

  let locked = false;
  let interaction: Live2DInteractionOptions = { drag: true, wheelZoom: true, pinchZoom: true, tap: true };
  let currentModelUri: string | null = null;
  let mouthValue = 0;
  let overrideMode: "off" | "override" | "additive" = "additive";
  let savedParameters: Record<string, number> | null = null;
  let persistentParameters: Record<string, number> | null = null;

  const getRuntime = () => adapter.getRuntime?.() ?? null;

  const ensureOverrideLayer = () => {
    const rt = getRuntime();
    const paramsRt = rt?.parameters;
    if (!paramsRt?.installOverrideLayer) return;
    paramsRt.installOverrideLayer(() => ({
      mouthValue,
      mode: overrideMode,
      savedParameters,
      persistentParameters,
    }));
  };

  const getTransformSnapshot = (): TransformSnapshot | null => {
    const rt = getRuntime();
    return rt?.getTransformSnapshot?.() ?? null;
  };

  const loadPreferences = async (modelUri: Live2DModelUri) => {
    if (!options?.preferences) return null;
    try {
      return await options.preferences.load(modelUri);
    } catch {
      return null;
    }
  };

  const savePreferences = async () => {
    if (!options?.preferences) return;
    if (!currentModelUri) return;

    const snap = getTransformSnapshot();
    const payload: Live2DPreferencesSnapshot = {
      modelUri: currentModelUri,
      position: snap ? { x: snap.position.x, y: snap.position.y } : undefined,
      scale: snap ? { x: snap.scale.x, y: snap.scale.y } : undefined,
    };

    await options.preferences.save(payload);
  };

  const applyModelParameters = async (params: Record<string, number>) => {
    const rt = getRuntime();
    const api = rt?.parameters;
    if (!api?.setParameterValueById) {
      throw new Error("[live2d-service] 当前 adapter 不支持参数写入（parameters runtime 未实现）");
    }
    for (const [id, value] of Object.entries(params || {})) {
      if (typeof id !== "string") continue;
      if (typeof value !== "number" || !Number.isFinite(value)) continue;
      api.setParameterValueById(id, value, 1);
    }
  };

  const setEmotion = async (emotion: string) => {
    const modelUri = currentModelUri;
    if (!modelUri) return;
    const provider = options?.emotionMappingProvider;
    if (!provider) {
      // 没有 provider：退化为“把 emotion 当 expression id / motion group”
      await service.setExpression({ id: emotion });
      await service.playMotion({ group: emotion });
      return;
    }

    const mapping = await provider.getEmotionMapping(modelUri);
    const expressions = mapping?.expressions?.[emotion] ?? [];
    const motions = mapping?.motions?.[emotion] ?? [];

    const exp = randomPick(expressions);
    if (exp) {
      // 兼容：exp 可能是 "expressions/xxx.exp3.json"（Web），或是纯 id（RN）
      await service.setExpression({ id: exp });
    }

    const motion = randomPick(motions);
    if (motion) {
      await service.playMotion({ group: motion });
      return;
    }

    // 没有 motion：至少尝试播放同名 group
    await service.playMotion({ group: emotion });
  };

  const resetModelPosition = async () => {
    // 不把“默认布局策略”塞在 core：这里给一个最小兜底（回到 0,0 & scale=1）
    await service.setTransform({ position: { x: 0, y: 0 }, scale: { x: 1, y: 1 } });
  };

  const api: Live2DManager = {
    service,

    getRuntime,

    getState: () => service.getState(),
    on: (event, handler) => service.on(event, handler as any),

    async loadModel(modelUri, opts) {
      currentModelUri = modelUri;
      const pref = opts?.preferences ?? (await loadPreferences(modelUri));
      const optionsForAdapter: Record<string, unknown> = {};
      if (pref?.position || pref?.scale) {
        // 复用 service.setTransform（adapter 内部解释具体坐标系）
        try {
          await service.loadModel({ uri: modelUri, source: "url" }, optionsForAdapter);
          const position = pref?.position;
          const scale = pref?.scale;
          if (position || scale) {
            await service.setTransform({
              position: position ? { x: position.x, y: position.y } : undefined,
              scale: scale ? { x: scale.x, y: scale.y } : undefined,
            });
          }
        } catch (err) {
          // 重要：loadModel 失败必须向上抛出，不能吞掉；且不要在失败时 setTransform
          throw err;
        }
      } else {
        await service.loadModel({ uri: modelUri, source: "url" }, optionsForAdapter);
      }

      // 模型加载完成后，安装（或重装）参数覆盖层
      try {
        ensureOverrideLayer();
      } catch {
        // ignore
      }

      if (pref?.parameters) {
        try {
          await applyModelParameters(pref.parameters);
        } catch {
          // best-effort
        }
      }
    },

    unloadModel: () => service.unloadModel(),

    getTransformSnapshot,
    setTransform: (t) => service.setTransform(t),
    resetModelPosition,
    savePreferences,
    loadPreferences,

    playMotion: (m) => service.playMotion(m),
    setExpression: (e) => service.setExpression(e),
    setEmotion,
    clearExpression: async () => {
      // legacy 语义：回到初始表情。这里 best-effort：清空保存参数由宿主实现更合理
      // 若 runtime 支持 parameters，可在宿主层做“初始参数快照”恢复
    },
    clearEmotionEffects: async () => {
      // legacy 语义：停止 motion 并重置 motion 参数，但保留 expression
      // 当前 service 未暴露 stopAllMotions，因此先留空；后续可通过 runtime 扩展
    },

    setMouth(value01) {
      mouthValue = clamp01(value01);
      service.setMouthValue(mouthValue);
      // 口型被 motion 覆盖时，依赖 override layer 保持优先级；此处 best-effort 触发一次
      try {
        ensureOverrideLayer();
      } catch {
        // ignore
      }
    },

    applyModelParameters,
    async setSavedModelParameters(params) {
      savedParameters = params ? { ...params } : null;
      try {
        ensureOverrideLayer();
      } catch {
        // ignore
      }
    },
    async setPersistentParameters(params) {
      persistentParameters = params ? { ...params } : null;
      try {
        ensureOverrideLayer();
      } catch {
        // ignore
      }
    },
    setParameterOverrideMode(mode) {
      overrideMode = mode;
      try {
        ensureOverrideLayer();
      } catch {
        // ignore
      }
    },

    setLocked(next) {
      locked = Boolean(next);
      // 交互/指针事件属于宿主层；这里只存状态，供宿主读取
    },
    setInteractionOptions(next) {
      interaction = { ...interaction, ...(next || {}) };
      // 同上：宿主层负责绑定手势/禁用 pointer events
    },
    async snapToScreen() {
      // 吸附逻辑属于宿主（Web/Electron）层；这里仅提供接口占位
      return false;
    },

    async dispose() {
      await service.dispose();
      currentModelUri = null;
    },
  };

  // 先把 locked/interaction 状态暴露为非标准字段（迁移期临时用）
  (api as any)._locked = () => locked;
  (api as any)._interaction = () => interaction;

  return api;
}

