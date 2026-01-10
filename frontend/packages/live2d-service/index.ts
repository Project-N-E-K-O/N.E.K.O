export type {
  Live2DAdapter,
  Live2DCapabilities,
  Live2DEventSink,
  Live2DEvents,
  Live2DError,
  Live2DPlatform,
  Live2DService,
  Live2DState,
  Live2DStatus,
  ModelRef,
  MotionRef,
  ExpressionRef,
  Transform,
  Vec2,
} from "./src/types";

export { createLive2DService } from "./src/service";


export type { Live2DRuntime, Live2DParametersRuntime, TransformSnapshot, Rect } from "./src/runtime";

export type {
  Live2DManager,
  Live2DPreferencesRepository,
  Live2DPreferencesSnapshot,
  EmotionMapping,
  EmotionMappingProvider,
  Live2DInteractionOptions,
} from "./src/manager";

export { createLive2DManager } from "./src/manager";

