import {
  useState,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useCallback,
  type CSSProperties,
  type FocusEvent as ReactFocusEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from 'react';
import { createPortal } from 'react-dom';
import AvatarToolItemManager, { type AvatarToolManagerAnchorRect } from './AvatarToolItemManager';
import AvatarToolQuickbar from './AvatarToolQuickbar';
import FullChatSurface from './FullChatSurface';
import AvatarToolVisuals from './avatar-tools/presentation';
import { useAvatarToolRuntime } from './avatar-tools/runtime';
import {
  COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS,
  COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC,
  COMPACT_TOOL_WHEEL_REBOUND_VISUAL_SOFT_INTENSITY,
  getCompactToolWheelReboundVisualIntensity,
  getCompactToolWheelReboundVolume,
  playCompactToolWheelDetentSound,
  playCompactToolWheelReboundSound,
  preloadCompactToolWheelSounds,
  resetCompactToolWheelDetentAudioForTests,
  useCompactToolWheelAudioPreload,
} from './compactToolWheelAudio';
import { useFocusGlow } from './useFocusGlow';
import { useGuideChatButtonLock } from './useGuideChatButtonLock';
import CompactExportHistoryPanel, {
  COMPACT_EXPORT_SELECTION_LIMIT,
  COMPACT_HISTORY_ROUTED_WHEEL_EVENT,
  isCompactExportMessageSelectable,
  type CompactExportActionRequest,
  type CompactExportPreviewResult,
} from './CompactExportHistoryPanel';
import { getChatCompanionEmptyStateFallback, getChatEmptyStateFallback } from './chat-copy';
import { swapImageToMemeLoadFailedSticker } from './memeImageFallback';
import { i18n } from './i18n';
import {
  type ChatMessage,
  type MessageAction,
  type ChatWindowSchemaProps,
  type ComposerSubmitPayload,
  type ComposerAttachment,
  type AvatarInteractionPayload,
  type AvatarToolStatePayload,
  type CompactChatState,
  type GalgameOption,
  type ChoiceOption,
  type ChoicePromptSource,
} from './message-schema';
import {
  AVAILABLE_AVATAR_TOOLS,
  DEFAULT_ACTIVE_AVATAR_TOOL_IDS,
  persistActiveAvatarToolIds,
  readPersistedActiveAvatarToolIds,
  sanitizeAvatarToolIds,
  type AvatarToolId,
  type AvatarToolItem,
} from './avatarTools';

export {
  COMPACT_TOOL_WHEEL_DETENT_SOUND_SRCS,
  COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC,
  getCompactToolWheelReboundVisualIntensity,
  getCompactToolWheelReboundVolume,
  preloadCompactToolWheelSounds,
  playCompactToolWheelDetentSound,
  playCompactToolWheelReboundSound,
  resetCompactToolWheelDetentAudioForTests,
};

export type ChatWindowProps = ChatWindowSchemaProps & {
  onMessageAction?: (message: ChatMessage, action: MessageAction) => void;
  onComposerImportImage?: () => void;
  onComposerScreenshot?: () => void;
  onComposerRemoveAttachment?: (attachmentId: ComposerAttachment['id']) => void;
  onComposerSubmit?: (payload: ComposerSubmitPayload) => void;
  onAvatarInteraction?: (payload: AvatarInteractionPayload) => void;
  onAvatarToolStateChange?: (payload: AvatarToolStatePayload) => void;
  onJukeboxClick?: () => void;
  onExportConversationClick?: () => void;
  onTranslateToggle?: () => void;
  onGalgameModeToggle?: () => void;
  onGalgameOptionSelect?: (option: GalgameOption) => void;
  // ChoicePrompt remains part of ChatWindowSchemaProps. Keep the legacy galgame
  // callback path until the host fully migrates to the shared choice slot.
  onChoiceSelect?: (option: ChoiceOption, source: ChoicePromptSource) => void;
  onCompactChatStateChange?: (state: CompactChatState) => void;
  onCompactMinimizeRequest?: () => void;
  // 注：compactMinimizeCancelSeq 在 message-schema.ts 的 chatWindowPropsSchema 里声明
  // （ChatWindowSchemaProps 已含），必须在 schema 里、否则 parse 会 strip 掉（Codex P2）。
};

type CompactInlineExportBridge = {
  buildCompactInlinePreview?: (request: CompactExportActionRequest) => Promise<CompactExportPreviewResult> | CompactExportPreviewResult;
  copyCompactInlineSelection?: (request: CompactExportActionRequest) => Promise<void> | void;
  downloadCompactInlineSelection?: (request: CompactExportActionRequest) => Promise<void> | void;
};

const defaultMessages: ChatMessage[] = [];

function getEffectiveCompactChatState(
  requestedState: CompactChatState,
  hasVisibleChoices: boolean,
  composerHidden: boolean,
): CompactChatState {
  if (composerHidden) {
    return 'default';
  }
  if (requestedState === 'input') {
    return 'input';
  }
  if (hasVisibleChoices) {
    return 'options';
  }
  if (requestedState === 'options') {
    return 'default';
  }
  return requestedState;
}

const COMPACT_SPEECH_REVEAL_MAX_CHARS_PER_SECOND = 8;
const COMPACT_SPEECH_TURN_MERGE_WINDOW_MS = 12000;
const COMPACT_SPEECH_FALLBACK_REVEAL_DELAY_MS = 700;
const SPEECH_PLAYBACK_STATE_STORAGE_KEY = 'neko_speech_playback_state';
const SPEECH_PLAYBACK_CHANNEL_NAME = 'neko_speech_playback_channel';
const COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY = 'neko.reactChatWindow.compactExportHistoryOpen';
// ⚠️ 'neko.experiment.compactHistoryDefault' 是已退役的「历史首启默认」A/B 分组 key（退役记录见
// utils/token_tracker.py 的已退役实验清单）：存量用户 localStorage 里仍残留 'open'/'closed' 值，
// 只停读写、不清理（保留取证窗口），勿把该 key 名挪作他用。
const COMPACT_HISTORY_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.compactHistorySlotHeight';
export const COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS = 560;
const COMPACT_INPUT_TOOL_WHEEL_TOOL_ORDER = [
  'screenshot',
  'avatar',
  'translate',
  'jukebox',
  'import',
  'export',
  'galgame',
] as const;
const COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT = COMPACT_INPUT_TOOL_WHEEL_TOOL_ORDER.length;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD = 28;
const COMPACT_INPUT_TOOL_WHEEL_SCROLL_DEADZONE = 0.5;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_GUARD_MS = 4000;
const COMPACT_INPUT_TOOL_WHEEL_FAST_GESTURE_MS = 140;
const COMPACT_INPUT_TOOL_WHEEL_FAST_ANIMATION_MS = 180;
const COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO = 0.68;
const COMPACT_INPUT_TOOL_WHEEL_DETENT_HOLD_RATIO = 0.86;
const COMPACT_INPUT_TOOL_WHEEL_DETENT_BREAK_RATIO = 1.16;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_TURNS = 3;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_ACTIVE_TURNS = 15;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS = COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT * COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_TURNS;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS = COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT * COMPACT_INPUT_TOOL_WHEEL_CHARGE_ACTIVE_TURNS;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MIN_STEP_MS = 25;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MID_STEP_MS = 42;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MAX_STEP_MS = 125;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_LOW_CHARGE_MIN_STEP_MS = 25;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MAX_TURNS = COMPACT_INPUT_TOOL_WHEEL_CHARGE_ACTIVE_TURNS;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MAX_TAIL_STEPS = 8;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_WEAK_RATIO = 0.5;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_STRONG_RATIO = 0.8;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_X = 116;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_Y = 116;
const COMPACT_INPUT_TOOL_WHEEL_HOVER_RADIUS = 116;
const COMPACT_INPUT_TOOL_WHEEL_ANGLE_MIN_RADIUS = 16;
const COMPACT_INPUT_TOOL_WHEEL_VIEWPORT_MARGIN = 8;
const COMPACT_INPUT_TOOL_TOGGLE_HOVER_OUTSET = 14;
const COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE = 48;
// 在工具轮盘中心（toggle / fan 原点）按下后，指针移动超过此像素阈值即视为「拖动文本框」
// 而非「点一下展开/关闭轮盘」。与宿主 surface 拖拽的 CLICK_THRESHOLD(5px) 量级一致。
const COMPACT_INPUT_TOOL_ORIGIN_DRAG_THRESHOLD = 6;
const COMPACT_INPUT_TOOL_FAN_INTERACTIVE_DELAY_MS = 220;
const COMPACT_INPUT_TOOL_FAN_TRANSIENT_CLOSE_DELAY_MS = 360;
const COMPACT_INPUT_TOOL_FAN_OUTSIDE_CLOSE_DELAY_MS = 650;
const compactInputToolWheelDefaultVisibleSlots = [
  { angleDeg: 107.35, scale: 0.86 },
  { angleDeg: 75.82, scale: 0.98 },
  { angleDeg: 45, scale: 1.04 },
  { angleDeg: 14.18, scale: 0.98 },
  { angleDeg: -17.35, scale: 0.86 },
] as const;
const compactInputToolWheelViewportFitVisibleSlots = [
  { angleDeg: -200, scale: 0.86 },
  { angleDeg: -170, scale: 0.98 },
  { angleDeg: -140, scale: 1.04 },
  { angleDeg: -110, scale: 0.98 },
  { angleDeg: -80, scale: 0.86 },
] as const;
const COMPACT_TOOL_WHEEL_CHARGE_RELEASE_REBOUND_OVERSHOOT_RATIO = 0.18;
const COMPACT_TOOL_WHEEL_CHARGE_RELEASE_REBOUND_VISUAL_MS = 120;
const COMPACT_TOOL_WHEEL_DEFAULT_DRAG_ANGLE_STEP_DEG = Math.abs(
  compactInputToolWheelDefaultVisibleSlots[2].angleDeg - compactInputToolWheelDefaultVisibleSlots[3].angleDeg,
);
const COMPACT_TOOL_WHEEL_VIEWPORT_DRAG_ANGLE_STEP_DEG = Math.abs(
  compactInputToolWheelViewportFitVisibleSlots[2].angleDeg - compactInputToolWheelViewportFitVisibleSlots[3].angleDeg,
);
const COMPACT_SURFACE_RESIZE_MIN_WIDTH = 180;
// compact 对话条默认/初始宽度（无法从 rect/CSS 量到时的回退值）。与 resize 下限解耦：
// 减小最短宽度只动 RESIZE_MIN_WIDTH，默认仍是这个值，保证「默认宽度不变」。
const COMPACT_SURFACE_DEFAULT_WIDTH = 430;
const COMPACT_SURFACE_RESIZE_MOBILE_MIN_WIDTH = 180;
const COMPACT_SURFACE_RESIZE_MAX_WIDTH = 720;
const COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER = 32;
const COMPACT_SURFACE_RESIZE_MOBILE_VIEWPORT_GUTTER = 16;
// compact 历史堆砌区（CompactExportHistoryPanel）顶部 resize bar 的高度上限钳位参数。
// 下限压到 ~1-2 个气泡以便节约屏幕；上限对齐 anchor 的 max-height（width*1.46 / 78% 视口），
// 避免拖超 anchor 二次截断产生「拖了没反应」的死区。默认（未拖动）公式仍是 width*1.18 / 63%。
const COMPACT_HISTORY_SLOT_MIN_HEIGHT = 120;
const COMPACT_HISTORY_SLOT_MAX_VIEWPORT_RATIO = 0.78;
const COMPACT_HISTORY_SLOT_DEFAULT_WIDTH_RATIO = 1.18;
const COMPACT_HISTORY_SLOT_DEFAULT_VIEWPORT_RATIO = 0.63;
// scroll 区上方的 bar(12px+margin) 与下方 controls(展开块 ≤44px) 的固定 chrome；
// 从 anchor max-height 里扣掉，避免拖到上限时 scroll 吃满 anchor、controls 溢出被裁成非交互。
const COMPACT_HISTORY_SLOT_CHROME_RESERVE = 72;
const COMPACT_CHOICE_PLACEMENT_HYSTERESIS = 24;
const COMPOSER_OPTION_MARQUEE_MIN_DISTANCE = 6;
const COMPOSER_OPTION_MARQUEE_MIN_DURATION_MS = 1400;
const COMPOSER_OPTION_MARQUEE_MAX_DURATION_MS = 12000;
const COMPOSER_OPTION_MARQUEE_PIXELS_PER_SECOND = 96;
const COMPOSER_OPTION_MARQUEE_END_PADDING = 28;

type CompactSurfaceResizeSide = 'left' | 'right';

type CompactSurfaceResizeState = {
  pointerId: number;
  side: CompactSurfaceResizeSide;
  startPointerX: number;
  startWidth: number;
  lastWidth: number;
  limitStalled: boolean;
  anchorLeftScreen: number;
  anchorRightScreen: number;
  anchorTopScreen: number;
  surfaceHeight: number;
  captureTarget: Element | null;
};

type CompactHistoryResizeState = {
  pointerId: number;
  startPointerY: number;
  startHeight: number;
  initialHeight: number;
  lastHeight: number;
  startedSlotHeight: number | null;
  heightChanged: boolean;
  limitStalled: boolean;
  captureTarget: Element | null;
};

type CompactToolWheelPointerState = {
  id: number;
  x: number;
  y: number;
  angle: number | null;
  angleRemainder: number;
  dragOffsetRatio: number;
  didRotate: boolean;
  captureTarget: Element | null;
};

type CompactToolWheelChargeState = {
  direction: 1 | -1 | null;
  sameDirectionSteps: number;
  chargeSteps: number;
};

type CompactToolWheelDragPoint = {
  x: number;
  y: number;
  angle: number | null;
};

type CompactToolWheelDragInput = {
  pointerId: number;
  clientX: number;
  clientY: number;
  buttons?: number;
  pointerType?: string;
  preventDefault?: () => void;
};

function normalizeCompactToolWheelAngleDelta(delta: number): number {
  const fullTurn = Math.PI * 2;
  return ((((delta + Math.PI) % fullTurn) + fullTurn) % fullTurn) - Math.PI;
}

function getCompactToolWheelDetentStepCount(offsetRatio: number): number {
  const absRatio = Math.abs(offsetRatio);
  if (absRatio < COMPACT_INPUT_TOOL_WHEEL_DETENT_BREAK_RATIO) return 0;
  return Math.floor(absRatio - COMPACT_INPUT_TOOL_WHEEL_DETENT_BREAK_RATIO) + 1;
}

function getCompactToolWheelDetentDisplayRatio(offsetRatio: number): number {
  if (offsetRatio === 0) return 0;
  const sign = offsetRatio > 0 ? 1 : -1;
  const absRatio = Math.abs(offsetRatio);
  if (absRatio <= COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO) {
    return offsetRatio;
  }
  const resistanceSpan = COMPACT_INPUT_TOOL_WHEEL_DETENT_BREAK_RATIO
    - COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO;
  const t = clamp(
    (absRatio - COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO) / resistanceSpan,
    0,
    1,
  );
  const easedT = 1 - ((1 - t) ** 2);
  return sign * (
    COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO
    + (
      COMPACT_INPUT_TOOL_WHEEL_DETENT_HOLD_RATIO
      - COMPACT_INPUT_TOOL_WHEEL_DETENT_RESISTANCE_START_RATIO
    ) * easedT
  );
}

function getCompactToolWheelTimestamp(): number {
  return window.performance?.now?.() ?? Date.now();
}

function createCompactToolWheelChargeState(): CompactToolWheelChargeState {
  return {
    direction: null,
    sameDirectionSteps: 0,
    chargeSteps: 0,
  };
}

function getCompactToolWheelChargeProgressRatio(chargeSteps: number): number {
  return clamp(chargeSteps / COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS, 0, 1);
}

function getCompactToolWheelChargeLapAngles(chargeRatio: number): [number, number, number] {
  const clampedRatio = clamp(chargeRatio, 0, 1);
  const firstLapRatio = clamp(
    clampedRatio / COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_WEAK_RATIO,
    0,
    1,
  );
  const secondLapRatio = clamp(
    (clampedRatio - COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_WEAK_RATIO)
    / (
      COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_STRONG_RATIO
      - COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_WEAK_RATIO
    ),
    0,
    1,
  );
  const thirdLapRatio = clamp(
    (clampedRatio - COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_STRONG_RATIO)
    / (1 - COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_STRONG_RATIO),
    0,
    1,
  );
  return [
    Math.round(firstLapRatio * 360),
    Math.round(secondLapRatio * 360),
    Math.round(thirdLapRatio * 360),
  ];
}

function getCompactToolWheelChargeReleaseStepDelayMs(
  progressRatio: number,
  chargeProgressRatio: number,
  remainingSteps: number,
  releaseSteps: number,
): number {
  const clampedProgress = clamp(progressRatio, 0, 1);
  const clampedChargeProgress = clamp(chargeProgressRatio, 0, 1);
  const minStepMs = Math.round(
    COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_LOW_CHARGE_MIN_STEP_MS
    - (
      COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_LOW_CHARGE_MIN_STEP_MS
      - COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MIN_STEP_MS
    ) * clampedChargeProgress,
  );
  const midProgress = clamp((clampedProgress - 0.68) / 0.24, 0, 1);
  const midDelayMs = minStepMs + (
    COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MID_STEP_MS
    - minStepMs
  ) * (midProgress ** 2);
  const tailStepCount = Math.min(
    COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MAX_TAIL_STEPS,
    Math.max(4, Math.ceil(releaseSteps * 0.08)),
  );
  const tailProgress = clamp(
    (tailStepCount - remainingSteps) / Math.max(1, tailStepCount - 1),
    0,
    1,
  );
  const easedTailProgress = tailProgress ** 2.25;
  return Math.round(
    midDelayMs
    + (
      COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MAX_STEP_MS
      - midDelayMs
    ) * easedTailProgress,
  );
}

function getCompactToolWheelChargeReleaseVisualStepCount(chargeSteps: number, itemCount: number): number {
  if (chargeSteps <= 0 || itemCount <= 0) return 0;
  const chargeProgress = getCompactToolWheelChargeProgressRatio(chargeSteps);
  const releaseTurns = Math.max(1, Math.round(
    1 + chargeProgress * (COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_MAX_TURNS - 1),
  ));
  return itemCount * releaseTurns;
}

function normalizeCompactToolWheelStepOffset(stepOffset: number, itemCount: number): number {
  if (itemCount <= 0) return 0;
  return ((stepOffset % itemCount) + itemCount) % itemCount;
}

function getCompactToolWheelSlotForIndex(
  toolIndex: number,
  visualIndex: number,
  itemCount: number,
): number | null {
  const forwardDistance = (toolIndex - visualIndex + itemCount) % itemCount;
  if (forwardDistance <= 2) {
    return forwardDistance;
  }
  if (forwardDistance >= itemCount - 2) {
    return forwardDistance - itemCount;
  }
  return null;
}

function getCompactToolWheelSlotValueForIndex(
  toolIndex: number,
  visualIndex: number,
  itemCount: number,
): string {
  const slot = getCompactToolWheelSlotForIndex(toolIndex, visualIndex, itemCount);
  if (slot !== null) return String(slot);

  const forwardDistance = (toolIndex - visualIndex + itemCount) % itemCount;
  if (forwardDistance === 3) return 'hidden-forward';
  if (forwardDistance === itemCount - 3) return 'hidden-backward';
  return 'hidden';
}

type CompactMessagePreview = {
  messageId: string;
  // Stable identity of the whole merged turn: the id of the earliest message
  // folded into this preview. Unchanged as more bubbles stream into the same
  // turn (messageId re-keys to the latest bubble, this does not), and changes
  // when a genuinely new turn begins. Used to tell an appended bubble from a
  // new turn without relying on text-prefix matching.
  turnStartId: string;
  turnId?: string;
  author: string;
  text: string;
  fullText: string;
  isStreaming: boolean;
  isAssistant: boolean;
  isGuide: boolean;
};

type CompactCaptionState = {
  turnId: string;
  segmentId: string;
  lastSegmentText: string;
  segments: Array<{
    segmentId: string;
    text: string;
  }>;
  text: string;
  isEnded?: boolean;
};

type DesktopCompactChoicePlacementLayout = {
  compactChoicePlacement?: 'above' | 'below' | null;
  surface?: {
    left?: number;
    top?: number;
    width?: number;
    height?: number;
  } | null;
  windowBounds?: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
  } | null;
  workArea?: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
  } | null;
};

function clampCompactSurfaceResizeWidth(
  width: number,
  maxAvailableWidth: number,
  minAvailableWidth = COMPACT_SURFACE_RESIZE_MIN_WIDTH,
  viewportGutter = COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER,
): number {
  const maxWidth = Math.max(
    0,
    Math.min(COMPACT_SURFACE_RESIZE_MAX_WIDTH, maxAvailableWidth - viewportGutter),
  );
  const minWidth = Math.min(minAvailableWidth, maxWidth || minAvailableWidth);
  return Math.round(Math.max(minWidth, Math.min(width, Math.max(minWidth, maxWidth))));
}

function getCompactSurfaceResizePointerX(event: ReactPointerEvent<HTMLDivElement>): number {
  const screenX = Number(event.screenX);
  if (Number.isFinite(screenX)) {
    return screenX;
  }
  return event.clientX;
}

function isDesktopCompactSurfaceLayoutActive(): boolean {
  return typeof window !== 'undefined'
    && !!(window as typeof window & {
      __nekoDesktopCompactLayout?: { windowBounds?: unknown } | null;
    }).__nekoDesktopCompactLayout?.windowBounds;
}

function readPersistedCompactExportHistoryOpen(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    const persisted = window.localStorage?.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
    if (persisted !== null) return persisted === 'true';
    // 无显式偏好：一律折叠（原「历史首启默认」A/B 已退役，closed 即最终默认；用户手动展开写入偏好后照旧恢复）。
    return false;
  } catch {
    return false;
  }
}

function persistCompactExportHistoryOpen(open: boolean) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage?.setItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY, open ? 'true' : 'false');
  } catch {
    // localStorage can be unavailable in restricted hosts; keep the in-memory state.
  }
}

function readPersistedCompactHistorySlotHeight(): number | null {
  if (typeof window === 'undefined') return null;
  try {
    const persisted = window.localStorage?.getItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY);
    if (persisted === null || persisted === undefined) return null;
    const value = Number(persisted);
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

function persistCompactHistorySlotHeight(value: number | null) {
  if (typeof window === 'undefined') return;
  try {
    if (value === null) {
      window.localStorage?.removeItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY);
    } else {
      window.localStorage?.setItem(COMPACT_HISTORY_HEIGHT_STORAGE_KEY, String(Math.round(value)));
    }
  } catch {
    // localStorage can be unavailable in restricted hosts; keep the in-memory state.
  }
}

// 历史区高度上限的基数：Electron 独立窗口用工作区高度（窗口可能只覆盖部分屏，不能用 innerHeight），
// 网页路径用视口高度。与 styles.css 里默认公式的 63vh / workarea*0.63 取同一基数。
function getCompactHistoryViewportBase(): number {
  if (typeof window === 'undefined') return 900;
  const desktopLayout = (window as typeof window & {
    __nekoDesktopCompactLayout?: { workArea?: { height?: number } | null } | null;
  }).__nekoDesktopCompactLayout;
  const workAreaHeight = Number(desktopLayout?.workArea?.height);
  if (isDesktopCompactSurfaceLayoutActive() && Number.isFinite(workAreaHeight) && workAreaHeight > 0) {
    return workAreaHeight;
  }
  return window.innerHeight || 900;
}

function getCompactHistoryResizePointerY(event: ReactPointerEvent<HTMLDivElement>): number {
  const screenY = Number(event.screenY);
  if (Number.isFinite(screenY)) {
    return screenY;
  }
  return event.clientY;
}

type SpeechPlaybackState = {
  active: boolean;
  turnId?: string | null;
  playbackTurnId?: string | null;
  speechId?: string | null;
  audioContextTime: number;
  playbackStartAudioTime: number;
  playbackEndAudioTime: number;
  updatedAt: number;
};

function normalizeCompactPreviewText(text: string): string {
  return text
    .replace(/\[play_music:[^\]]*(\]|$)/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function splitCompactPreviewGraphemes(text: string): string[] {
  const segmenter = (Intl as typeof Intl & {
    Segmenter?: new (
      locale?: string,
      options?: { granularity?: 'grapheme' },
    ) => { segment(input: string): Iterable<{ segment: string }> };
  }).Segmenter;
  if (typeof segmenter === 'function') {
    return Array.from(new segmenter(undefined, { granularity: 'grapheme' }).segment(text), part => part.segment);
  }
  return Array.from(text);
}

function getCompactSpeechRevealDuration(textLength: number, audioDuration: number): number {
  const readableDuration = textLength / COMPACT_SPEECH_REVEAL_MAX_CHARS_PER_SECOND;
  return Math.max(audioDuration, readableDuration, 0.05);
}

function getEstimatedSpeechAudioTime(state: SpeechPlaybackState): number {
  if (!state.active) {
    return state.audioContextTime;
  }
  const elapsedSinceUpdate = Math.max(0, (Date.now() - state.updatedAt) / 1000);
  return state.audioContextTime + elapsedSinceUpdate;
}

function isSpeechPlaybackStateForCompactPreview(
  state: SpeechPlaybackState | null,
  preview: { turnId?: string } | null,
): state is SpeechPlaybackState {
  if (!state) return false;
  const previewTurnId = preview?.turnId;
  if (!previewTurnId) return true;
  const stateTurnIds = [state.playbackTurnId, state.turnId].filter((value): value is string => !!value);
  if (stateTurnIds.length === 0) return true;
  return stateTurnIds.includes(previewTurnId);
}

function getMessageBlockPreviewText(message: ChatMessage): string {
  if (!Array.isArray(message.blocks)) {
    return '';
  }

  const text = message.blocks.flatMap((block) => {
    switch (block.type) {
      case 'text':
      case 'status':
        return [block.text];
      case 'link':
        return [block.title || block.description || block.url];
      default:
        return [];
    }
  }).join(' ');

  return normalizeCompactPreviewText(text);
}

function isGuideMessageId(id: unknown): boolean {
  return typeof id === 'string' && id.startsWith('yui-guide-');
}

function getCompactMessagePreview(messages: ChatMessage[]): CompactMessagePreview | null {
  let latestStreamingAssistantIndex = -1;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (
      message?.role === 'assistant'
      && message.status === 'streaming'
      && getMessageBlockPreviewText(message)
    ) {
      latestStreamingAssistantIndex = index;
      break;
    }
  }

  if (latestStreamingAssistantIndex >= 0) {
    const turnTexts: string[] = [];
    let turnAuthor = '';
    const latestStreamingMessage = messages[latestStreamingAssistantIndex];
    const latestStreamingTurnId = latestStreamingMessage?.turnId;
    const turnMessageId = String(latestStreamingMessage?.id || 'assistant-streaming');
    const latestStreamingIsGuide = isGuideMessageId(turnMessageId);
    // Walks backward to the earliest merged bubble, so the last assignment in
    // the loop is the turn's anchor id.
    let turnStartId = latestStreamingTurnId ? `assistant-turn:${latestStreamingTurnId}` : turnMessageId;
    let previousIncludedCreatedAt = typeof latestStreamingMessage?.createdAt === 'number'
      && Number.isFinite(latestStreamingMessage.createdAt)
      ? latestStreamingMessage.createdAt
      : null;
    for (let index = latestStreamingAssistantIndex; index >= 0; index -= 1) {
      const message = messages[index];
      if (!message) continue;
      if (message.role !== 'assistant') {
        break;
      }
      if (
        index !== latestStreamingAssistantIndex
        && (
          (latestStreamingTurnId && message.turnId !== latestStreamingTurnId)
          || latestStreamingIsGuide
          || isGuideMessageId(message.id)
        )
      ) {
        break;
      }
      if (index !== latestStreamingAssistantIndex && message.status !== 'streaming') {
        const createdAt = typeof message.createdAt === 'number' && Number.isFinite(message.createdAt)
          ? message.createdAt
          : null;
        if (!latestStreamingTurnId && (
          previousIncludedCreatedAt === null
          || createdAt === null
          || Math.abs(previousIncludedCreatedAt - createdAt) > COMPACT_SPEECH_TURN_MERGE_WINDOW_MS
        )) {
          break;
        }
        previousIncludedCreatedAt = createdAt;
      }
      // Anchor the turn to every message folded in, before the empty-text skip.
      // A bubble can be momentarily text-less (still streaming, image-only) then
      // gain text; if the anchor only moved on text-bearing bubbles it would
      // drift to a later bubble and back, re-keying the same turn as a new one
      // and replaying the caption.
      if (!latestStreamingTurnId) {
        turnStartId = String(message.id || turnMessageId);
      }
      const text = getMessageBlockPreviewText(message);
      if (!text) continue;
      turnTexts.unshift(text);
      turnAuthor = message.author || turnAuthor;
    }
    if (turnTexts.length > 0) {
      const turnText = normalizeCompactPreviewText(turnTexts.join(' '));
      return {
        messageId: turnMessageId || 'assistant-streaming',
        turnStartId,
        turnId: latestStreamingTurnId,
        author: turnAuthor,
        text: turnText,
        fullText: turnText,
        isStreaming: true,
        isAssistant: true,
        isGuide: latestStreamingIsGuide,
      };
    }
  }

  return null;
}

type ToolIconItem = AvatarToolItem;

const toolIconItems = AVAILABLE_AVATAR_TOOLS;

function getToolItemLabel(item: ToolIconItem): string {
  return i18n(item.labelKey, item.labelFallback);
}

const compactToolWheelControlWheelTargetSelector = [
  '.compact-input-tool-item',
  '.composer-icon-popover',
  '.avatar-tool-quickbar',
  '.avatar-tool-quickbar-edit',
].join(', ');

const compactHistoryOpenScrollSelector = [
  '.compact-export-history-anchor[data-compact-export-history-visibility="open"]:not(.under-choice-prompt)',
  '.compact-export-history-scroll',
].join(' ');

type CompactInputToolWheelLayout = 'default' | 'viewport-fit';

function getCompactToolWheelVisualDirectionMultiplier(layout: CompactInputToolWheelLayout): 1 | -1 {
  return layout === 'viewport-fit' ? -1 : 1;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function isPointInElementRect(element: Element, clientX: number, clientY: number): boolean {
  const rect = element.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  return clientX >= rect.left
    && clientX <= rect.right
    && clientY >= rect.top
    && clientY <= rect.bottom;
}

function isCompactToolWheelControlWheelTarget(target: EventTarget | null): boolean {
  return target instanceof Element
    && !!target.closest(compactToolWheelControlWheelTargetSelector);
}

function getCompactHistoryScrollFromElement(element: Element): HTMLDivElement | null {
  const scrollNode = element.closest<HTMLDivElement>(compactHistoryOpenScrollSelector);
  return scrollNode instanceof HTMLDivElement ? scrollNode : null;
}

function getCompactHistoryScrollUnderCompactToolWheel(
  event: ReactWheelEvent<HTMLDivElement>,
): HTMLDivElement | null {
  if (isCompactToolWheelControlWheelTarget(event.target)) return null;
  const clientX = event.clientX;
  const clientY = event.clientY;
  if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
  const fanElement = event.currentTarget;
  const pointElements = typeof document.elementsFromPoint === 'function'
    ? document.elementsFromPoint(clientX, clientY)
    : [];

  for (const element of pointElements) {
    if (!(element instanceof Element)) continue;
    if (fanElement === element || fanElement.contains(element)) continue;
    const scrollNode = getCompactHistoryScrollFromElement(element);
    if (scrollNode && isPointInElementRect(scrollNode, clientX, clientY)) {
      return scrollNode;
    }
  }

  return Array.from(document.querySelectorAll<HTMLDivElement>(compactHistoryOpenScrollSelector))
    .find(scrollNode => isPointInElementRect(scrollNode, clientX, clientY)) ?? null;
}

/**
 * Top-level dispatcher. The host mounts this as the chat window root. It routes
 * the `full` layout to {@link FullChatSurface} and the `compact`/`minimized`
 * layouts to {@link CompactChatApp}. The layouts stay separate while avatar-tool
 * selection, lifecycle, hit testing, feedback, payloads, and visuals use the
 * same shared runtime.
 */
export default function ChatWindowRoot(props: ChatWindowProps) {
  if (props.chatSurfaceMode === 'full') {
    return <FullChatSurface {...props} />;
  }
  return <CompactChatApp {...props} />;
}

function CompactChatApp({
  title = i18n('chat.title', 'N.E.K.O Chat'),
  iconSrc = '/static/icons/chat_icon.png',
  messages = defaultMessages,
  inputPlaceholder = i18n('chat.textInputPlaceholder', 'Type a message...'),
  sendButtonLabel = i18n('chat.send', 'Send'),
  chatWindowAriaLabel = i18n('chat.reactWindowAriaLabel', 'Neko chat window'),
  messageListAriaLabel: _messageListAriaLabel,
  composerToolsAriaLabel: _composerToolsAriaLabel,
  composerHidden = false,
  composerDisabled = false,
  compactInputLocked = false,
  chatSurfaceMode = 'compact',
  compactMinimizeCancelSeq = 0,
  compactChatState,
  composerAttachments = [],
  composerAttachmentsAriaLabel = i18n('chat.pendingImagesAriaLabel', 'Pending attachments'),
  importImageButtonLabel = i18n('chat.importImage', 'Import Image'),
  screenshotButtonLabel = i18n('chat.screenshot', 'Screenshot'),
  importImageButtonAriaLabel,
  screenshotButtonAriaLabel,
  removeAttachmentButtonAriaLabel = i18n('chat.removePendingImage', 'Remove image'),
  failedStatusLabel = i18n('chat.messageFailed', 'Failed'),
  jukeboxButtonLabel = i18n('chat.jukeboxLabel', 'Jukebox'),
  jukeboxButtonAriaLabel = i18n('chat.jukebox', 'Jukebox'),
  translateEnabled = false,
  translateButtonLabel = i18n('subtitle.enable', 'Subtitle Translation'),
  translateButtonAriaLabel,
  galgameModeEnabled = false,
  galgameOptions = [],
  galgameOptionsLoading = false,
  galgameToggleButtonLabel = i18n('chat.galgameToggle', 'GalGame Mode'),
  galgameToggleButtonAriaLabel,
  galgameLoadingLabel = i18n('chat.galgameLoading', 'Generating options...'),
  // Retained for host API compatibility; the compact-only surface no longer
  // renders the per-message action menu, so this handler is currently unused
  // (the `_` prefix opts it out of unused-var lint).
  onMessageAction: _onMessageAction,
  onComposerImportImage,
  onComposerScreenshot,
  onComposerRemoveAttachment,
  onComposerSubmit,
  onAvatarInteraction,
  onAvatarToolStateChange,
  onJukeboxClick,
  // Retained for host API compatibility; export lives outside the compact
  // surface now, so this handler is currently unused (the `_` prefix opts it
  // out of unused-var lint).
  onExportConversationClick: _onExportConversationClick,
  onTranslateToggle,
  onGalgameModeToggle,
  onGalgameOptionSelect,
  choicePrompt = null,
  onChoiceSelect,
  avatarToolMenuOpenRequest = null,
  compactToolFanOpenRequest = null,
  compactToolWheelRotateRequest = null,
  compactToolWheelIndexRequest = null,
  compactHistoryOpenRequest = null,
  onCompactChatStateChange,
  onCompactMinimizeRequest,
  rollbackDraft,
  _rollbackKey,
  _avatarToolDeactivationKey,
}: ChatWindowProps) {
  useCompactToolWheelAudioPreload();

  const [draft, setDraft] = useState('');
  const guideChatButtonsLocked = useGuideChatButtonLock();
  const compactTextEntryLocked = composerDisabled || compactInputLocked || guideChatButtonsLocked;
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  const [activeAvatarToolIds, setActiveAvatarToolIds] = useState<AvatarToolId[]>(readPersistedActiveAvatarToolIds);
  const [avatarToolManagerOpen, setAvatarToolManagerOpen] = useState(false);
  const [avatarToolManagerAnchorRect, setAvatarToolManagerAnchorRect] = useState<AvatarToolManagerAnchorRect | null>(null);
  const appShellRef = useRef<HTMLElement | null>(null);
  const toolMenuRef = useRef<HTMLDivElement | null>(null);
  const compactInputShellRef = useRef<HTMLDivElement | null>(null);
  const compactInputToolToggleRef = useRef<HTMLButtonElement | null>(null);
  const compactInputToolFanRef = useRef<HTMLDivElement | null>(null);
  const compactInputToolWheelPointerRef = useRef<CompactToolWheelPointerState | null>(null);
  const compactInputToolWheelDragActiveRef = useRef(false);
  const compactInputToolWheelDragGuardTimerRef = useRef<number | null>(null);
  const compactInputToolWheelFastAnimationTimerRef = useRef<number | null>(null);
  const compactInputToolWheelChargeReleaseTimerRef = useRef<number | null>(null);
  const compactInputToolWheelLastRotationAtRef = useRef(0);
  const compactInputToolWheelChargeRef = useRef<CompactToolWheelChargeState>(createCompactToolWheelChargeState());
  const compactInputToolWheelChargeReleaseActiveRef = useRef(false);
  const compactInputToolWheelSuppressClickRef = useRef(false);
  const compactInputToolWheelHoverPointerRef = useRef<{ clientX: number; clientY: number } | null>(null);
  const compactInputToolWheelHoveredIndexRef = useRef<number | null>(null);
  // compact surface 控件的「按住拖动对话框」手势追踪。与轮盘旋转
  // (compactInputToolWheelPointerRef) 互斥：原点按下时不建立旋转 pointer，旋转路径自然 no-op。
  const compactToolOriginDragRef = useRef<{
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startScreenX: number;
    startScreenY: number;
    moved: boolean;
    hostDragEnded?: boolean;
    primeEnded?: boolean;
    lastForwardedClientX?: number;
    lastForwardedClientY?: number;
    lastForwardedScreenX?: number;
    lastForwardedScreenY?: number;
    captureTarget: Element | null;
  } | null>(null);
  const compactToolOriginDocumentCleanupRef = useRef<(() => void) | null>(null);
  // 专用于「拖动文本框后吞掉补发 click」的标志。独立于 compactInputToolWheelSuppressClickRef，
  // 因为轮盘关闭 effect 会重置后者，无法跨「关闭轮盘 + 随后 click」存活。
  const compactToolOriginSuppressClickRef = useRef(false);
  const compactInputToolFanPositionSyncRef = useRef<(() => void) | null>(null);
  const compactInputToolFanCloseTimerRef = useRef<number | null>(null);
  const compactInputToolFanInteractiveTimerRef = useRef<number | null>(null);
  const compactInputToolFanOpenIntentRef = useRef<'click' | 'hover' | null>(null);
  const compactInputToolFanOpenRef = useRef(false);
  const compactInputToolFanHoverInsideRef = useRef(false);
  const compactInputToolFanSuppressHoverUntilLeaveRef = useRef(false);
  const compactInputToolFanInteractiveRef = useRef(false);
  const compactInputRef = useRef<HTMLTextAreaElement | null>(null);
  const compactChoiceLayerRef = useRef<HTMLDivElement | null>(null);
  const draftRef = useRef(draft);
  const compactPreviewTextVisibleRef = useRef('');
  const previousCompactPreviewTextRef = useRef('');
  const compactPreviewTextRef = useRef<HTMLSpanElement | null>(null);
  const compactSpeechVisibleLengthRef = useRef(0);
  const compactSpeechPlaybackStartedRef = useRef(false);
  const compactSpeechAnimationFrameRef = useRef<number | null>(null);
  const compactSpeechRevealCarryRef = useRef(0);
  const compactSpeechLastFrameTimeRef = useRef(0);
  const compactSpeechPreviewIdRef = useRef('');
  const compactSpeechPreviewTextRef = useRef('');
  const compactSpeechPreviewTurnIdRef = useRef('');
  // Identity of the turn the speech reveal is currently walking through (the
  // preview's turnStartId). Updated when the preview re-keys (messageId change),
  // so it holds the *previous* turn's anchor at the moment a new bubble arrives
  // — used to tell an appended bubble (same turn → keep revealing) from a
  // brand-new turn (rewind to the start) without text-prefix guessing.
  const compactSpeechRevealTurnIdRef = useRef('');
  const compactSpeechFallbackRevealRef = useRef(false);
  const compactSpeechFallbackTimerRef = useRef<number | null>(null);
  const isCompactSurfaceRef = useRef(false);
  const speechPlaybackStateRef = useRef<SpeechPlaybackState | null>(null);
  const [compactPreviewTextVisible, setCompactPreviewTextVisible] = useState('');
  const [compactSpeechVisibleLength, setCompactSpeechVisibleLength] = useState(0);
  const [compactSpeechFallbackRevealActive, setCompactSpeechFallbackRevealActive] = useState(false);
  const compactCapsuleEntryLocked = compactTextEntryLocked;
  const [speechPlaybackState, setSpeechPlaybackState] = useState<SpeechPlaybackState | null>(null);
  const [compactCaptionState, setCompactCaptionState] = useState<CompactCaptionState | null>(null);
  // 用户手动叉掉的表情包 id（会话级，不持久化）：overlay 的 meme id 命中即隐藏。下一张新 meme 是不同
  // id，自然重新显示；刷新后状态重置（与 compactCaptionState 等紧凑挂件一致，均为 ephemeral state）。
  const [dismissedMemeId, setDismissedMemeId] = useState<string | null>(null);
  const [loadedMemeOverlayKey, setLoadedMemeOverlayKey] = useState<string | null>(null);
  const [compactAssistantStreamingGap, setCompactAssistantStreamingGap] = useState<{
    turnId: string;
    acceptStreaming: boolean;
  } | null>(null);
  const [compactChoiceLayerPlacement, setCompactChoiceLayerPlacement] = useState<'above' | 'below'>('above');
  const [compactInputToolFanOpen, setCompactInputToolFanOpen] = useState(false);
  const [compactInputToolFanInteractive, setCompactInputToolFanInteractive] = useState(false);
  const [compactInputToolWheelLayout, setCompactInputToolWheelLayout] = useState<CompactInputToolWheelLayout>('default');
  // 环位转角是组件级 state：会话内（组件存活期间）一直延续上次滚到的位置，
  // 但不持久化到 localStorage —— 页面刷新/组件重挂时会随初值复位到环位 0（默认布局）。
  const [compactInputToolWheelIndex, setCompactInputToolWheelIndex] = useState(0);
  const [compactInputToolWheelFastAnimation, setCompactInputToolWheelFastAnimation] = useState(false);
  const [compactInputToolWheelDragActive, setCompactInputToolWheelDragActive] = useState(false);
  const [compactInputToolWheelDragOffsetRatio, setCompactInputToolWheelDragOffsetRatio] = useState(0);
  const [compactInputToolWheelChargeRatio, setCompactInputToolWheelChargeRatio] = useState(0);
  const [compactInputToolWheelChargeDirection, setCompactInputToolWheelChargeDirection] = useState<1 | -1 | null>(null);
  const [compactInputToolWheelChargeReleaseActive, setCompactInputToolWheelChargeReleaseActive] = useState(false);
  const [compactInputToolWheelChargeReleaseVisualStepOffset, setCompactInputToolWheelChargeReleaseVisualStepOffset] = useState(0);
  const [compactInputToolWheelHoveredIndex, setCompactInputToolWheelHoveredIndex] = useState<number | null>(null);
  const [compactSurfaceResizeWidth, setCompactSurfaceResizeWidth] = useState<number | null>(null);
  // Focus 凝神: subtle cognition indicator. Driven by the backend `focus_state`
  // ws message (app-websocket.js → 'neko-focus-state' CustomEvent). Inert by
  // default — the backend only emits it when FOCUS_MODE_ENABLED.
  const [focusActive, setFocusActive] = useState(false);
  // 凝神 thinking-dots: backend pulses `focus_thinking` (app-websocket.js →
  // 'neko-focus-thinking') while a Focus turn thinks-on but hasn't spoken yet.
  const [focusThinking, setFocusThinking] = useState(false);
  const [compactHistorySlotHeight, setCompactHistorySlotHeight] = useState<number | null>(readPersistedCompactHistorySlotHeight);
  const [compactHistoryResizeActive, setCompactHistoryResizeActive] = useState(false);
  const [compactHistoryResizeContentLocked, setCompactHistoryResizeContentLocked] = useState(false);
  // 快照一次再复用：open/mounted 必须来自同一次持久化读取，单读单源避免后续给初始化引入副作用时，
  // 两个 useState 各算一次导致 open/mounted 分裂初态。无偏好一律折叠（「历史首启默认」A/B 已退役）。
  const [initialCompactExportHistoryOpen] = useState(readPersistedCompactExportHistoryOpen);
  const [compactExportHistoryOpen, setCompactExportHistoryOpen] = useState(initialCompactExportHistoryOpen);
  const [compactExportHistoryMounted, setCompactExportHistoryMounted] = useState(initialCompactExportHistoryOpen);
  const [compactExportHistoryClosingMessages, setCompactExportHistoryClosingMessages] = useState<ChatMessage[] | null>(null);
  const [compactExportControlsOpen, setCompactExportControlsOpen] = useState(false);
  const [compactExportPreviewOpen, setCompactExportPreviewOpen] = useState(false);
  const [compactExportSelectedIds, setCompactExportSelectedIds] = useState<Set<string>>(() => new Set());
  const [compactExportAutoScrollToBottom, setCompactExportAutoScrollToBottom] = useState(true);
  const compactExportHistoryGeometryStateRef = useRef<{ mounted: boolean; open: boolean } | null>(null);
  // 折叠进行中：点最小化时置 true → 蓝条（历史区开关）淡出（#2）+ 胶囊右→左擦除收走。mode 切到
  // minimized 后由下方 useEffect 复位。展开进行中：minimized→compact 时置 true → 胶囊左→右展开 reveal。
  // 这两个擦除/reveal 类必须由 React state 写进 className（而非 host/preload 用 classList 加）—— 否则
  // React 重渲染会用 JSX className 覆盖、把类删掉，导致动画被打断、胶囊瞬跳（偶发"展开销毁闪一下"）。
  const [compactCollapsing, setCompactCollapsing] = useState(false);
  const [compactExpanding, setCompactExpanding] = useState(false);
  const prevChatSurfaceModeRef = useRef(chatSurfaceMode);
  // 折叠时若历史区是开的，记下「恢复后应重新打开历史区」。配合 closeCompactExportHistory
  // ({persist:false})：折叠只播收回动画、不写偏好，恢复（minimized→compact）时据此重开。
  const compactHistoryReopenAfterRestoreRef = useRef(false);
  // host 折叠取消序号上次值，用于检测「取消」事件（见下方 useLayoutEffect）。
  const prevCompactMinimizeCancelSeqRef = useRef(compactMinimizeCancelSeq);
  const compactSurfaceResizeStateRef = useRef<CompactSurfaceResizeState | null>(null);
  const compactHistoryResizeStateRef = useRef<CompactHistoryResizeState | null>(null);
  const compactHistoryVisibilitySuppressClickRef = useRef(false);
  const compactExportHistoryUnmountTimerRef = useRef<number | null>(null);
  const submittingRef = useRef(false);
  const lastRollbackKeyRef = useRef('');
  const lastAvatarToolMenuOpenRequestIdRef = useRef('');
  const lastCompactToolFanOpenRequestIdRef = useRef('');
  const lastCompactToolWheelRotateRequestIdRef = useRef('');
  const lastCompactHistoryOpenRequestIdRef = useRef('');
  const lastCompactToolWheelIndexRequestIdRef = useRef('');
  const compactInputHasPayload = draft.trim().length > 0 || composerAttachments.length > 0;
  const canSubmit = !compactTextEntryLocked && compactInputHasPayload;
  const avatarToolRuntime = useAvatarToolRuntime({
    composerHidden,
    composerDisabled,
    interactionDisabled: guideChatButtonsLocked,
    deactivationKey: _avatarToolDeactivationKey,
    onInteraction: onAvatarInteraction,
    onStateChange: onAvatarToolStateChange,
    getToolLabel: getToolItemLabel,
    onDeactivate: () => setToolMenuOpen(false),
  });
  const activeAvatarToolId = avatarToolRuntime.activeToolId;
  const activeToolItem = avatarToolRuntime.activeTool;
  const effectiveAvatarToolVariant = avatarToolRuntime.effectiveVariant;
  const clearActiveAvatarToolSelection = avatarToolRuntime.clearTool;
  const handleAvatarQuickbarToolClick = avatarToolRuntime.selectTool;

  const handleAvatarToolManagerSave = useCallback((toolIds: AvatarToolId[]) => {
    const nextToolIds = sanitizeAvatarToolIds(toolIds);
    setActiveAvatarToolIds(nextToolIds);
    persistActiveAvatarToolIds(nextToolIds);
    setAvatarToolManagerOpen(false);
    if (activeAvatarToolId && !nextToolIds.includes(activeAvatarToolId)) {
      clearActiveAvatarToolSelection();
    }
  }, [activeAvatarToolId, clearActiveAvatarToolSelection]);

  useEffect(() => {
    if (!activeAvatarToolId) return;
    if (activeAvatarToolIds.includes(activeAvatarToolId as AvatarToolId)) return;
    clearActiveAvatarToolSelection();
  }, [activeAvatarToolIds, activeAvatarToolId, clearActiveAvatarToolSelection]);

  // Rollback draft when host signals a RESPONSE_TOO_LONG error
  // Use _rollbackKey for dedup. It changes on every rollbackLastDraft() call
  // and stays the same across intermediate renderWindow() calls, so the rollback
  // is applied exactly once regardless of how many times renderWindow fires.
  useEffect(() => {
    if (rollbackDraft && _rollbackKey && _rollbackKey !== lastRollbackKeyRef.current) {
      lastRollbackKeyRef.current = _rollbackKey;
      if (!draft || draft.trim() === '') {
        setDraft(rollbackDraft);
      }
    }
  }, [rollbackDraft, _rollbackKey, draft]);

  useEffect(() => {
    const markImage = (img: HTMLImageElement) => {
      img.draggable = false;
      img.setAttribute('draggable', 'false');
    };

    const markImages = (root: ParentNode | HTMLImageElement = document) => {
      if (root instanceof HTMLImageElement) {
        markImage(root);
        return;
      }
      root.querySelectorAll?.<HTMLImageElement>('img').forEach(markImage);
    };

    const handleDragStart = (event: DragEvent) => {
      if (event.target instanceof HTMLImageElement) {
        event.preventDefault();
      }
    };

    markImages(document);
    document.addEventListener('dragstart', handleDragStart, true);

    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node instanceof Element) {
            markImages(node);
          }
        });
      });
    });
    observer.observe(document.documentElement, { childList: true, subtree: true });

    return () => {
      observer.disconnect();
      document.removeEventListener('dragstart', handleDragStart, true);
    };
  }, []);

  const resolvedImportImageAriaLabel = importImageButtonAriaLabel || importImageButtonLabel;
  const resolvedScreenshotAriaLabel = screenshotButtonAriaLabel || screenshotButtonLabel;
  const resolvedTranslateAriaLabel = translateButtonAriaLabel || translateButtonLabel;
  const resolvedGalgameAriaLabel = galgameToggleButtonAriaLabel || galgameToggleButtonLabel;
  const compactExportControlsVisible = compactExportHistoryOpen && compactExportControlsOpen;
  const compactExportHistoryToggleLabel = compactExportHistoryOpen
    ? i18n('chat.compactHistoryToggleClose', 'Hide history')
    : i18n('chat.compactHistoryToggleOpen', 'Show history');
  const compactExportControlsButtonLabel = i18n(
    'chat.compactHistoryExport',
    'Export conversation history',
  );
  // ChoicePrompt and galgame options share the same composer-anchored slot.
  // The transient invite should win when both are present so we do not stack
  // two button groups in the same compact surface.
  const compactChoiceInteractionsAllowed = !composerHidden;
  const choicePromptHasOptions = compactChoiceInteractionsAllowed
    && !!(choicePrompt && choicePrompt.options.length > 0);
  const galgameOptionsVisible =
    compactChoiceInteractionsAllowed && galgameModeEnabled && !choicePromptHasOptions
    && (galgameOptionsLoading || galgameOptions.length > 0);
  const compactSurfaceChoicesVisible = choicePromptHasOptions || galgameOptionsVisible;
  const isCompactSurface = chatSurfaceMode !== 'minimized';
  // compactChatState 受控时跟随外部 prop；未受控（独立挂载 / 开发预览 main.tsx）时用
  // 内部 state 兜底，让字幕胶囊点击能真正切到输入态，而不是停在胶囊里出不来喵。
  const isCompactChatStateControlled = compactChatState !== undefined;
  const [uncontrolledCompactChatState, setUncontrolledCompactChatState] =
    useState<CompactChatState>('default');
  const requestedCompactChatState = compactChatState ?? uncontrolledCompactChatState;
  const effectiveCompactChatState = isCompactSurface
    ? getEffectiveCompactChatState(requestedCompactChatState, compactSurfaceChoicesVisible, composerHidden)
    : requestedCompactChatState;
  const getCompactSurfaceResizeMaxAvailableWidth = useCallback(() => {
    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: DesktopCompactChoicePlacementLayout | null;
    };
    const workAreaWidth = Number(desktopWindow.__nekoDesktopCompactLayout?.workArea?.width);
    if (Number.isFinite(workAreaWidth) && workAreaWidth > 0) {
      return workAreaWidth;
    }
    return window.innerWidth || COMPACT_SURFACE_RESIZE_MIN_WIDTH + COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER;
  }, []);
  const getCompactSurfaceResizeMinAvailableWidth = useCallback(() => (
    !document.body?.classList.contains('electron-chat-window')
      && !document.body?.classList.contains('lanlan-pet-mode')
      && !(window as typeof window & { __LANLAN_IS_ELECTRON_PET__?: boolean }).__LANLAN_IS_ELECTRON_PET__
      && window.innerWidth <= 768
      ? COMPACT_SURFACE_RESIZE_MOBILE_MIN_WIDTH
      : COMPACT_SURFACE_RESIZE_MIN_WIDTH
  ), []);
  const getCompactSurfaceResizeViewportGutter = useCallback(() => (
    !document.body?.classList.contains('electron-chat-window')
      && !document.body?.classList.contains('lanlan-pet-mode')
      && !(window as typeof window & { __LANLAN_IS_ELECTRON_PET__?: boolean }).__LANLAN_IS_ELECTRON_PET__
      && window.innerWidth <= 768
      ? COMPACT_SURFACE_RESIZE_MOBILE_VIEWPORT_GUTTER
      : COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER
  ), []);
  const getClampedCompactSurfaceResizeWidth = useCallback((width: number) => (
    clampCompactSurfaceResizeWidth(
      width,
      getCompactSurfaceResizeMaxAvailableWidth(),
      getCompactSurfaceResizeMinAvailableWidth(),
      getCompactSurfaceResizeViewportGutter(),
    )
  ), [
    getCompactSurfaceResizeMaxAvailableWidth,
    getCompactSurfaceResizeMinAvailableWidth,
    getCompactSurfaceResizeViewportGutter,
  ]);
  const getClampedCompactSurfaceResizeWidthForSide = useCallback((
    side: CompactSurfaceResizeSide,
    width: number,
    resizeState?: CompactSurfaceResizeState | null,
  ) => {
    const desktopWindow = window as typeof window & {
      __nekoDesktopCompactLayout?: DesktopCompactChoicePlacementLayout | null;
    };
    const workArea = desktopWindow.__nekoDesktopCompactLayout?.workArea;
    const areaX = Number(workArea?.x);
    const areaWidth = Number(workArea?.width);
    if (
      resizeState
      && isDesktopCompactSurfaceLayoutActive()
      && Number.isFinite(areaX)
      && Number.isFinite(areaWidth)
      && areaWidth > 0
    ) {
      const edgePad = COMPACT_SURFACE_RESIZE_VIEWPORT_GUTTER / 2;
      const areaLeft = areaX + edgePad;
      const areaRight = areaX + areaWidth - edgePad;
      const maxWidth = side === 'left'
        ? resizeState.anchorRightScreen - areaLeft
        : areaRight - resizeState.anchorLeftScreen;
      if (Number.isFinite(maxWidth) && maxWidth > 0) {
        const viewportGutter = getCompactSurfaceResizeViewportGutter();
        return clampCompactSurfaceResizeWidth(
          width,
          maxWidth + viewportGutter,
          getCompactSurfaceResizeMinAvailableWidth(),
          viewportGutter,
        );
      }
    }
    return getClampedCompactSurfaceResizeWidth(width);
  }, [
    getCompactSurfaceResizeMinAvailableWidth,
    getCompactSurfaceResizeViewportGutter,
    getClampedCompactSurfaceResizeWidth,
  ]);
  const getCurrentCompactSurfaceWidth = useCallback(() => {
    const frameWidth = compactInputShellRef.current
      ?.querySelector<HTMLElement>('.compact-chat-surface-frame, [data-compact-geometry-part="inputBody"], [data-compact-geometry-part="capsuleBody"]')
      ?.getBoundingClientRect().width;
    if (Number.isFinite(frameWidth) && frameWidth && frameWidth > 0) {
      return getClampedCompactSurfaceResizeWidth(frameWidth);
    }
    const desktopLayout = (window as typeof window & {
      __nekoDesktopCompactLayout?: {
        surfaceScreenRect?: {
          width?: number;
        } | null;
      } | null;
    }).__nekoDesktopCompactLayout;
    const desktopSurfaceWidth = Number(desktopLayout?.surfaceScreenRect?.width);
    if (isDesktopCompactSurfaceLayoutActive() && Number.isFinite(desktopSurfaceWidth) && desktopSurfaceWidth > 0) {
      return getClampedCompactSurfaceResizeWidth(desktopSurfaceWidth);
    }
    const rectWidth = compactInputShellRef.current?.getBoundingClientRect().width;
    if (Number.isFinite(rectWidth) && rectWidth && rectWidth > 0) {
      return getClampedCompactSurfaceResizeWidth(rectWidth);
    }
    const cssWidth = Number.parseFloat(
      window.getComputedStyle(document.documentElement).getPropertyValue('--compact-surface-width'),
    );
    if (Number.isFinite(cssWidth) && cssWidth > 0) {
      return getClampedCompactSurfaceResizeWidth(cssWidth);
    }
    return getClampedCompactSurfaceResizeWidth(COMPACT_SURFACE_DEFAULT_WIDTH);
  }, [getClampedCompactSurfaceResizeWidth]);
  const getCurrentCompactSurfaceFrameRect = useCallback(() => {
    const shell = compactInputShellRef.current;
    const rect = shell
      ?.querySelector<HTMLElement>('.compact-chat-surface-frame, [data-compact-geometry-part="inputBody"], [data-compact-geometry-part="capsuleBody"]')
      ?.getBoundingClientRect();
    if (
      rect
      && Number.isFinite(rect.left)
      && Number.isFinite(rect.top)
      && Number.isFinite(rect.width)
      && Number.isFinite(rect.height)
      && rect.width > 0
      && rect.height > 0
    ) {
      return rect;
    }
    const shellRect = shell?.getBoundingClientRect();
    if (
      shellRect
      && Number.isFinite(shellRect.left)
      && Number.isFinite(shellRect.top)
      && Number.isFinite(shellRect.width)
      && Number.isFinite(shellRect.height)
      && shellRect.width > 0
      && shellRect.height > 0
    ) {
      return shellRect;
    }
    return null;
  }, []);
  const compactSurfaceEffectiveWidth = isCompactSurface
    && compactSurfaceResizeWidth !== null
    ? getClampedCompactSurfaceResizeWidth(compactSurfaceResizeWidth)
    : null;
  const compactChoiceLayerOpen = isCompactSurface && compactSurfaceChoicesVisible;
  const compactExportSelectedCount = compactExportSelectedIds.size;
  const compactExportSelectableMessages = useMemo(
    () => messages.filter(isCompactExportMessageSelectable),
    [messages],
  );
  const compactExportSelectableIds = useMemo(
    () => new Set(compactExportSelectableMessages.map(message => message.id)),
    [compactExportSelectableMessages],
  );
  const compactExportSelectableCount = compactExportSelectableMessages.length;
  const clearCompactExportHistoryUnmountTimer = useCallback(() => {
    if (compactExportHistoryUnmountTimerRef.current === null) return;
    window.clearTimeout(compactExportHistoryUnmountTimerRef.current);
    compactExportHistoryUnmountTimerRef.current = null;
  }, []);
  const openCompactExportHistory = useCallback((opts?: { persist?: boolean }) => {
    clearCompactExportHistoryUnmountTimer();
    setCompactExportHistoryClosingMessages(null);
    setCompactExportHistoryMounted(true);
    setCompactExportHistoryOpen(true);
    if (opts?.persist !== false) persistCompactExportHistoryOpen(true);
    setCompactExportAutoScrollToBottom(true);
  }, [clearCompactExportHistoryUnmountTimer]);
  // persist=false：只播收回动画、不把「历史区关闭」写进持久化偏好。折叠（minimize）时用，
  // 这样 minimize→恢复后历史区按折叠前状态重新打开，而不是把临时折叠误当成偏好变更。
  // 显式关闭（点蓝条/handle）仍默认 persist=true，正常写偏好。
  const closeCompactExportHistory = useCallback((opts?: { persist?: boolean }) => {
    const persist = opts?.persist !== false;
    clearCompactExportHistoryUnmountTimer();
    setCompactExportHistoryClosingMessages(messages);
    setCompactExportHistoryOpen(false);
    if (persist) persistCompactExportHistoryOpen(false);
    setCompactExportPreviewOpen(false);
    compactExportHistoryUnmountTimerRef.current = window.setTimeout(() => {
      setCompactExportHistoryMounted(false);
      setCompactExportHistoryClosingMessages(null);
      compactExportHistoryUnmountTimerRef.current = null;
    }, COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
  }, [clearCompactExportHistoryUnmountTimer, messages]);

  useEffect(() => {
    if (!isCompactSurface) {
      compactExportHistoryGeometryStateRef.current = null;
      return;
    }
    const previous = compactExportHistoryGeometryStateRef.current;
    compactExportHistoryGeometryStateRef.current = {
      mounted: compactExportHistoryMounted,
      open: compactExportHistoryOpen,
    };
    if (!previous) return;
    if (
      previous.mounted === compactExportHistoryMounted
      && previous.open === compactExportHistoryOpen
    ) {
      return;
    }
    window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
  }, [compactExportHistoryMounted, compactExportHistoryOpen, isCompactSurface]);

  useEffect(() => {
    const request = compactHistoryOpenRequest;
    if (!request || !request.id || request.id === lastCompactHistoryOpenRequestIdRef.current) return;
    lastCompactHistoryOpenRequestIdRef.current = request.id;
    if (request.open) {
      // 教程演示历史区只临时展开，不写用户偏好（与 close 对称）——演示动作不该变成用户的持久化选择。
      openCompactExportHistory({ persist: false });
      return;
    }
    closeCompactExportHistory({ persist: false });
  }, [closeCompactExportHistory, compactHistoryOpenRequest, openCompactExportHistory]);

  useEffect(() => () => {
    clearCompactExportHistoryUnmountTimer();
  }, [clearCompactExportHistoryUnmountTimer]);
  // 展开 reveal：minimized → compact（恢复）时置 compactExpanding → 胶囊左→右展开（React state 写
   // className，不被覆盖）。~340ms 后复位。prev ref 仅在此 effect（deps 只 mode）更新，准确跟踪模式变化。
   // 用 useLayoutEffect 而非 useEffect：passive effect 在首帧绘制后才置 compactExpanding，胶囊会先
   // 无 mask 全显一帧再重启擦除（Codex P2）。Electron 路径有壳侧 opacity-0 稳定门挡着看不出，但 web
   // compact 路径没有那道门，layout effect 让首帧绘制前就挂上 mask，消除 web 端首帧闪。仅提前 1 帧，
   // 340ms 擦除时长不变，prev ref 无其它时序读者 → 无竞态。
  useLayoutEffect(() => {
    const prev = prevChatSurfaceModeRef.current;
    prevChatSurfaceModeRef.current = chatSurfaceMode;
    // 重新进入 compact（从 minimized 或 full）时消费历史区重开请求（折叠时 persist:false 记下的）。
    // 不限于 minimized→compact——经公开 setChatSurfaceMode 的 minimized→full→compact 间接恢复也要重开，
    // 否则历史区该开没开、而偏好其实仍是开（Codex P2）。ref 仅在「带历史区折叠」时置位，无请求不触发。
    // 注意用 prev!=='compact' 而非裸 mode==='compact'：折叠点击当帧 mode 仍是 compact，裸判会立即把刚
    // 关掉的历史区又开回来。
    if (chatSurfaceMode === 'compact' && prev !== 'compact'
      && compactHistoryReopenAfterRestoreRef.current) {
      compactHistoryReopenAfterRestoreRef.current = false;
      openCompactExportHistory({ persist: false }); // restore 重开不持久化：manual-open OPEN_KEY 本就 true（幂等），教程临时展开不变成偏好
    }
    // 方向性 reveal 擦除只在毛线球 minimized→compact 恢复时播（full→compact 不播）。~340ms 后复位。
    // useLayoutEffect 让首帧绘制前就挂 mask，消除 web 端首帧闪（壳侧有 opacity-0 门、Electron 看不出）。
    if (prev === 'minimized' && chatSurfaceMode === 'compact') {
      setCompactExpanding(true);
      const t = window.setTimeout(() => setCompactExpanding(false), 340);
      return () => window.clearTimeout(t);
    }
    return undefined;
  }, [chatSurfaceMode, openCompactExportHistory]);
  // 折叠完成（mode→minimized）后复位 compactCollapsing：此刻蓝条/胶囊已随 isCompactSurface 卸载，
  // 复位无视觉副作用，只为下次恢复干净。
  useEffect(() => {
    if (chatSurfaceMode === 'minimized' && compactCollapsing) {
      setCompactCollapsing(false);
    }
  }, [chatSurfaceMode, compactCollapsing]);
  // 展开被打断时复位 compactExpanding：展开 reveal 是「minimized→compact 置 expanding→true，340ms
  // 后复位」。若这 340ms 内 mode 又被切走（如公开 setChatSurfaceMode('full') / setViewProps），上面
  // 展开 effect 的 cleanup 取消了那次 setCompactExpanding(false)，而该转换不是从这里发起 → expanding
  // 残留 true 带进后续 compact 渲染，令 full→compact 重放/保留展开 mask（Codex P2）。离开 compact 即复位。
  useEffect(() => {
    if (chatSurfaceMode !== 'compact' && compactExpanding) {
      setCompactExpanding(false);
    }
  }, [chatSurfaceMode, compactExpanding]);
  // 安全兜底：折叠流程是「compact 态置 collapsing→true，host ~280ms 后把 mode 切 minimized，
  // 上面的 minimized 复位 effect 清掉 collapsing」。但若这 280ms 内窗口被关闭 / 折叠被取消，
  // mode 永远到不了 minimized，collapsing 会卡在 true；而 host 的 closeWindow 是隐藏而非卸载
  // React 树，重开时复用同一组件 → 胶囊带着 neko-compact-collapsing 的 mask 卡在不可见末态
  // （Codex P2）。这里挂一道超过擦除时长（280ms wipe）的兜底超时确保复位：正常折叠会在
  // mode→minimized 时先复位、cleanup 清掉本超时（永不触发）；仅在取消场景兜底，此时窗口已隐藏，
  // 复位无可见副作用。
  useEffect(() => {
    if (!compactCollapsing) return undefined;
    const t = window.setTimeout(() => {
      setCompactCollapsing(false);
      // 折叠被取消（mode 没到 minimized，故正常的 minimized→compact 重开路径不会触发）→ 若折叠时
      // 用 persist:false 关掉了历史区，这里把它恢复回开，否则历史区会被一次未完成的折叠永久收起、
      // 而持久化偏好其实仍是开的（Codex P2）。openCompactExportHistory 在非 compact 态仅置状态、
      // 不渲染（受 isCompactSurface 门控），无副作用。正常折叠走 minimized→compact 重开、不到这里。
      if (compactHistoryReopenAfterRestoreRef.current) {
        compactHistoryReopenAfterRestoreRef.current = false;
        openCompactExportHistory({ persist: false }); // restore 重开不持久化：manual-open OPEN_KEY 本就 true（幂等），教程临时展开不变成偏好
      }
    }, 600);
    return () => window.clearTimeout(t);
  }, [compactCollapsing, openCompactExportHistory]);
  // host 折叠取消序号变化 = 一次进行中的折叠被取消（如 280ms 折叠延时内 closeWindow）。host 关窗
  // 只隐藏 overlay、React 树与 state 存活；重开时该 prop 携新值到达。用 useLayoutEffect 在重开
  // 首帧绘制前立即复位 compactCollapsing 并恢复历史区——否则要等上面 600ms 兜底，快速「关→重开」
  // 会让 compact 表面带着擦除 mask 的不可见 forwards 末态 + 历史区临时关闭重新出现（Codex P2）。
  // 600ms 兜底保留作为「取消后一直没重开」场景的更晚双保险。
  useLayoutEffect(() => {
    if (compactMinimizeCancelSeq === prevCompactMinimizeCancelSeqRef.current) return;
    prevCompactMinimizeCancelSeqRef.current = compactMinimizeCancelSeq;
    setCompactCollapsing(false);
    if (compactHistoryReopenAfterRestoreRef.current) {
      compactHistoryReopenAfterRestoreRef.current = false;
      openCompactExportHistory({ persist: false }); // restore 重开不持久化：manual-open OPEN_KEY 本就 true（幂等），教程临时展开不变成偏好
    }
  }, [compactMinimizeCancelSeq, openCompactExportHistory]);
  const handleCompactHistoryVisibilityToggle = useCallback(() => {
    if (compactExportHistoryOpen) {
      closeCompactExportHistory();
      return;
    }
    openCompactExportHistory();
  }, [closeCompactExportHistory, compactExportHistoryOpen, openCompactExportHistory]);
  const handleCompactHistoryVisibilityPress = useCallback((event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    compactHistoryVisibilitySuppressClickRef.current = true;
    handleCompactHistoryVisibilityToggle();
  }, [handleCompactHistoryVisibilityToggle]);
  const handleCompactHistoryVisibilityClick = useCallback((event: ReactMouseEvent<HTMLButtonElement>) => {
    if (compactHistoryVisibilitySuppressClickRef.current) {
      compactHistoryVisibilitySuppressClickRef.current = false;
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    handleCompactHistoryVisibilityToggle();
  }, [handleCompactHistoryVisibilityToggle]);
  const handleCompactHistoryVisibilityPointerCancel = useCallback(() => {
    compactHistoryVisibilitySuppressClickRef.current = false;
  }, []);
  const handleCompactExportControlsToggle = useCallback(() => {
    if (!compactExportHistoryOpen) {
      openCompactExportHistory();
      setCompactExportControlsOpen(true);
      return;
    }
    if (compactExportPreviewOpen) {
      setCompactExportPreviewOpen(false);
      setCompactExportControlsOpen(true);
      return;
    }
    setCompactExportControlsOpen((open) => {
      if (open) {
        setCompactExportSelectedIds(prev => (prev.size === 0 ? prev : new Set()));
      }
      return !open;
    });
  }, [compactExportHistoryOpen, compactExportPreviewOpen, openCompactExportHistory]);
  const handleCompactExportToggleMessage = useCallback((messageId: string) => {
    if (!compactExportSelectableIds.has(messageId)) return;
    setCompactExportSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        if (next.size >= COMPACT_EXPORT_SELECTION_LIMIT) return prev;
        next.add(messageId);
      }
      return next;
    });
  }, [compactExportSelectableIds]);
  const handleCompactExportSelectAll = useCallback(() => {
    setCompactExportSelectedIds(new Set(
      compactExportSelectableMessages
        .slice(0, COMPACT_EXPORT_SELECTION_LIMIT)
        .map(message => message.id),
    ));
  }, [compactExportSelectableMessages]);
  const handleCompactExportClearSelection = useCallback(() => {
    setCompactExportSelectedIds(prev => (prev.size === 0 ? prev : new Set()));
  }, []);
  const handleCompactExportInvertSelection = useCallback(() => {
    setCompactExportSelectedIds((prev) => {
      const next = new Set<string>();
      for (const message of compactExportSelectableMessages) {
        if (prev.has(message.id)) continue;
        if (next.size >= COMPACT_EXPORT_SELECTION_LIMIT) break;
        next.add(message.id);
      }
      return next;
    });
  }, [compactExportSelectableMessages]);
  const handleCompactExportPreviewRequest = useCallback(() => {
    setCompactExportPreviewOpen(true);
  }, []);
  const handleCompactExportPreviewClose = useCallback(() => {
    setCompactExportPreviewOpen(false);
  }, []);
  const handleCompactInlineBuildPreview = useCallback(async (
    request: CompactExportActionRequest,
  ): Promise<CompactExportPreviewResult> => {
    if (request.messageIds.length <= 0) return { previewKind: 'empty' };
    const exportBridge = (window as typeof window & {
      appChatExport?: CompactInlineExportBridge;
    }).appChatExport;
    if (typeof exportBridge?.buildCompactInlinePreview !== 'function') {
      throw new Error(i18n('chat.exportPreviewFailed', 'Failed to build the preview.'));
    }
    return exportBridge.buildCompactInlinePreview(request);
  }, []);
  const handleCompactInlineExportAction = useCallback(async (
    request: CompactExportActionRequest,
    action: 'copy' | 'download',
  ) => {
    if (request.messageIds.length <= 0) return;
    const exportBridge = (window as typeof window & {
      appChatExport?: CompactInlineExportBridge;
      showStatusToast?: (message: string, duration?: number) => void;
    }).appChatExport;
    const method = action === 'copy'
      ? exportBridge?.copyCompactInlineSelection
      : exportBridge?.downloadCompactInlineSelection;
    if (typeof method !== 'function') {
      (window as typeof window & { showStatusToast?: (message: string, duration?: number) => void })
        .showStatusToast?.(i18n('chat.exportPreviewFailed', 'Failed to build the preview.'), 3000);
      return;
    }
    await method(request);
  }, []);
  const handleCompactInlineCopyExport = useCallback((request: CompactExportActionRequest) => (
    handleCompactInlineExportAction(request, 'copy')
  ), [handleCompactInlineExportAction]);
  const handleCompactInlineDownloadExport = useCallback((request: CompactExportActionRequest) => (
    handleCompactInlineExportAction(request, 'download')
  ), [handleCompactInlineExportAction]);

  useEffect(() => {
    if (isCompactSurface) return;
    setCompactExportPreviewOpen(false);
    setCompactExportSelectedIds(prev => (prev.size === 0 ? prev : new Set()));
    setCompactExportAutoScrollToBottom(true);
    setCompactExportControlsOpen(false);
  }, [isCompactSurface]);

  useEffect(() => {
    if (isCompactSurface) return;
    setAvatarToolManagerOpen(false);
    setAvatarToolManagerAnchorRect(null);
  }, [isCompactSurface]);

  useEffect(() => {
    if (!compactExportHistoryOpen) return;
    if (messages.length > 0) return;
    setCompactExportPreviewOpen(false);
    setCompactExportSelectedIds(current => (current.size === 0 ? current : new Set()));
  }, [compactExportHistoryOpen, messages.length]);

  useEffect(() => {
    if (compactExportSelectedIds.size === 0) return;
    let changed = false;
    const next = new Set<string>();
    compactExportSelectedIds.forEach((id) => {
      if (compactExportSelectableIds.has(id)) {
        next.add(id);
      } else {
        changed = true;
      }
    });
    if (changed) {
      setCompactExportSelectedIds(next);
    }
  }, [compactExportSelectedIds, compactExportSelectableIds]);
  const surfaceModeClassName = `chat-surface-mode-${chatSurfaceMode}`;
  const compactMessagePreviewFromMessages = useMemo(() => getCompactMessagePreview(messages), [messages]);
  // 主动分享的表情包是 image-only 消息（id 以 'meme-' 开头），原本只活在会折叠的历史里。把「最新一条
  // 若是表情包」抽成一个独立 overlay 显示（仿音乐条），常显到「用户开口」或「新一轮助手发言」出现即收起
  // （换场规则详见下方 memo 注释）。
  const compactMemeOverlay = useMemo<{ id: string; url: string; alt: string } | null>(() => {
    if (!isCompactSurface) return null;
    let memeIdx = -1;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (typeof messages[i]?.id === 'string' && messages[i].id.startsWith('meme-')) { memeIdx = i; break; }
    }
    if (memeIdx < 0) return null;
    const meme = messages[memeIdx];
    // 表情包是「仿音乐条」的独立常显挂件，换场规则：
    //  1) 用户开口（出现 role==='user' 的消息）→ 收起；
    //  2) 出现「不同 turnId 的助手发言」→ 收起（host 给 meme 打了它所属主动搭话轮的 turnId，
    //     见 app-proactive.js `_showMemeBubbles`）。这样真正的新一轮回复/主动搭话会顶掉旧图。
    // 同一轮紧随表情包落地的台词(assistant)与 meme 共享 turnId，不算换场、不收起——否则图会一瞬间
    // 被台词顶掉(#2031 回归)。turnId 缺失（meme 或后续消息任一方无 turnId，如纯音乐卡）时退化为只看
    // 规则 1，保持旧行为。下一张新表情包由上面「从尾部取最新 meme」自然替换。
    const memeTurnId = typeof meme.turnId === 'string' && meme.turnId ? meme.turnId : null;
    for (let i = memeIdx + 1; i < messages.length; i += 1) {
      const later = messages[i];
      if (later?.role === 'user') return null;
      // 仅「不同 turnId 的助手发言」算新一轮换场；tool/system 不是发言、且通常与 assistant 同轮，
      // 不参与收起（更新的表情包另由上面「从尾部取最新 meme」自然替换，不走这里）。
      if (
        later?.role === 'assistant'
        && memeTurnId
        && typeof later.turnId === 'string'
        && later.turnId
        && later.turnId !== memeTurnId
      ) {
        return null;
      }
    }
    for (const block of meme.blocks ?? []) {
      if (block.type === 'image') return { id: meme.id, url: block.url, alt: block.alt || 'Meme' };
    }
    return null;
  }, [messages, isCompactSurface]);
  const compactMemeOverlayVisible = !!(
    isCompactSurface
    && !compactExportHistoryMounted
    && compactMemeOverlay
    && compactMemeOverlay.id !== dismissedMemeId
  );
  const compactMemeGeometryKey = compactMemeOverlay
    ? `${compactMemeOverlay.id}:${compactMemeOverlayVisible ? 'visible' : 'hidden'}`
    : 'none';
  const compactMemeOverlayLoadKey = compactMemeOverlay
    ? `${compactMemeOverlay.id}:${compactMemeOverlay.url}`
    : null;
  const compactMemeOverlayImageSettled = compactMemeOverlayLoadKey !== null
    && loadedMemeOverlayKey === compactMemeOverlayLoadKey;
  const lastCompactMemeGeometryKeyRef = useRef<string | null>(null);
  const compactMemeGeometryFrameRef = useRef<number | null>(null);
  const requestCompactMemeGeometryRefresh = useCallback(() => {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
  }, []);
  const scheduleCompactMemeGeometryRefresh = useCallback(() => {
    if (typeof window === 'undefined') return;
    if (compactMemeGeometryFrameRef.current !== null) return;
    const raf = window.requestAnimationFrame
      || ((callback: FrameRequestCallback) => window.setTimeout(() => callback(window.performance.now()), 16));
    compactMemeGeometryFrameRef.current = raf(() => {
      compactMemeGeometryFrameRef.current = null;
      requestCompactMemeGeometryRefresh();
    });
  }, [requestCompactMemeGeometryRefresh]);
  const markCompactMemeOverlayImageSettled = useCallback(() => {
    if (compactMemeOverlayLoadKey === null) return;
    setLoadedMemeOverlayKey(compactMemeOverlayLoadKey);
    scheduleCompactMemeGeometryRefresh();
  }, [compactMemeOverlayLoadKey, scheduleCompactMemeGeometryRefresh]);
  const handleCompactMemeOverlayImageError = useCallback((event: React.SyntheticEvent<HTMLImageElement>) => {
    swapImageToMemeLoadFailedSticker(event.currentTarget, compactMemeOverlay?.url || '');
    markCompactMemeOverlayImageSettled();
  }, [compactMemeOverlay?.url, markCompactMemeOverlayImageSettled]);
  const handleCompactMemeOverlayImageRef = useCallback((node: HTMLImageElement | null) => {
    if (!node?.complete) return;
    markCompactMemeOverlayImageSettled();
  }, [markCompactMemeOverlayImageSettled]);

  useLayoutEffect(() => {
    setLoadedMemeOverlayKey(current => {
      if (!compactMemeOverlayVisible || compactMemeOverlayLoadKey === null) {
        return current === null ? current : null;
      }
      return current === compactMemeOverlayLoadKey ? current : null;
    });
  }, [compactMemeOverlayLoadKey, compactMemeOverlayVisible]);

  useEffect(() => () => {
    if (typeof window === 'undefined') return;
    if (compactMemeGeometryFrameRef.current === null) return;
    const cancel = window.cancelAnimationFrame || window.clearTimeout;
    cancel(compactMemeGeometryFrameRef.current);
    compactMemeGeometryFrameRef.current = null;
  }, []);

  useEffect(() => {
    if (!isCompactSurface) {
      lastCompactMemeGeometryKeyRef.current = null;
      return undefined;
    }
    const previousKey = lastCompactMemeGeometryKeyRef.current;
    lastCompactMemeGeometryKeyRef.current = compactMemeGeometryKey;
    if (previousKey === compactMemeGeometryKey) return undefined;
    if (previousKey === null && compactMemeGeometryKey === 'none') return undefined;
    scheduleCompactMemeGeometryRefresh();
    return undefined;
  }, [
    compactMemeGeometryKey,
    isCompactSurface,
    scheduleCompactMemeGeometryRefresh,
  ]);
  const compactCaptionPreview = useMemo<CompactMessagePreview | null>(() => {
    if (!compactCaptionState?.turnId || !compactCaptionState.text) {
      return null;
    }
    const captionMessageId = `compact-caption:${compactCaptionState.turnId}`;
    return {
      messageId: captionMessageId,
      turnStartId: captionMessageId,
      turnId: compactCaptionState.turnId,
      author: 'Neko',
      text: compactCaptionState.text,
      fullText: compactCaptionState.text,
      isStreaming: !compactCaptionState.isEnded,
      isAssistant: true,
      isGuide: false,
    };
  }, [compactCaptionState]);
  const compactMessagePreview = compactCaptionPreview || compactMessagePreviewFromMessages;
  const compactMatchedSpeechPlaybackState = isSpeechPlaybackStateForCompactPreview(
    speechPlaybackState,
    compactMessagePreview,
  ) ? speechPlaybackState : null;
  const compactPreviewMatchesStreamingGap = !!compactAssistantStreamingGap
    && !!compactMessagePreview?.isAssistant
    && !!compactMessagePreview.isStreaming
    && !!compactMessagePreview.turnId
    && compactMessagePreview.turnId === compactAssistantStreamingGap.turnId;
  const compactPreservedSpeechMatchesEndingGap = !!compactAssistantStreamingGap
    && !compactAssistantStreamingGap.acceptStreaming
    && !!compactSpeechPreviewTurnIdRef.current
    && compactSpeechPreviewTurnIdRef.current === compactAssistantStreamingGap.turnId;
  const compactSuppressAssistantFallback = !!compactAssistantStreamingGap
    && !compactPreviewMatchesStreamingGap
    && !compactPreservedSpeechMatchesEndingGap;
  const compactPreservedSpeechActive = !compactMessagePreview
    && !compactSuppressAssistantFallback
    && !!compactSpeechPreviewIdRef.current
    && !!compactSpeechPreviewTextRef.current;
  const compactSpeechModeActive = compactPreservedSpeechActive
    || (!compactSuppressAssistantFallback
    && !!compactMessagePreview?.isAssistant
    && !!compactMessagePreview?.messageId
    && !compactMessagePreview.isGuide
    && (
      compactMessagePreview.isStreaming
      || compactSpeechPreviewIdRef.current === compactMessagePreview.messageId
    ));
  const compactSpeechPreservedText = (compactSpeechModeActive && !compactMessagePreview?.isStreaming)
    ? compactSpeechPreviewTextRef.current
    : '';
  const compactEmptyStateText = guideChatButtonsLocked
    ? ''
    : composerHidden
      ? i18n('chat.companionEmptyState', getChatCompanionEmptyStateFallback())
      : i18n('chat.emptyState', getChatEmptyStateFallback());
  const compactPreviewText = compactSuppressAssistantFallback
    ? ''
    : compactSpeechModeActive
      ? (
        compactMessagePreview?.isStreaming
          ? compactMessagePreview?.fullText || ''
          : compactSpeechPreservedText || compactMessagePreview?.fullText || ''
      )
      : compactMessagePreview?.text
      || compactEmptyStateText;
  const compactPreviewIsStreaming = compactSpeechModeActive;
  const compactPreviewAllowsScroll = compactPreviewIsStreaming || !!compactMessagePreview?.isGuide;
  const compactPreviewSpeechDuration = useMemo(() => {
    if (!compactPreviewIsStreaming || !compactMatchedSpeechPlaybackState) {
      return null;
    }
    const audioDuration = compactMatchedSpeechPlaybackState.playbackEndAudioTime
      - compactMatchedSpeechPlaybackState.playbackStartAudioTime;
    if (!Number.isFinite(audioDuration) || audioDuration <= 0.05) {
      return null;
    }
    return getCompactSpeechRevealDuration(compactPreviewText.length, audioDuration);
  }, [compactMatchedSpeechPlaybackState, compactPreviewIsStreaming, compactPreviewText.length]);
  const compactPreviewDisplayText = useMemo(() => {
    if (!compactPreviewIsStreaming) {
      if (!compactPreviewText) {
        return '';
      }
      return compactPreviewTextVisible || compactPreviewText;
    }
    const visibleLength = Math.min(compactPreviewText.length, compactSpeechVisibleLength);
    if (visibleLength <= 0) {
      return '';
    }
    return compactPreviewText.slice(0, visibleLength);
  }, [
    compactPreviewIsStreaming,
    compactPreviewSpeechDuration,
    compactSpeechVisibleLength,
    compactPreviewText,
    compactPreviewTextVisible,
  ]);
  const compactPreviewDisplayContent = useMemo(() => {
    if (!compactPreviewIsStreaming || !compactPreviewDisplayText) {
      return compactPreviewDisplayText;
    }
    const graphemes = splitCompactPreviewGraphemes(compactPreviewDisplayText);
    const latestIndex = graphemes.length - 1;
    if (latestIndex < 0) {
      return '';
    }
    const prefix = graphemes.slice(0, latestIndex).join('');
    const latestGrapheme = graphemes[latestIndex];
    const keyPrefix = compactMessagePreview?.turnStartId || compactMessagePreview?.messageId || 'compact-preview';
    return (
      <>
        {prefix}
        <span
          key={`${keyPrefix}:${latestIndex}:${latestGrapheme}`}
          className="compact-chat-capsule-glyph"
        >
          {latestGrapheme}
        </span>
      </>
    );
  }, [
    compactMessagePreview?.messageId,
    compactMessagePreview?.turnStartId,
    compactPreviewDisplayText,
    compactPreviewIsStreaming,
  ]);
  const overflowMenuAriaLabel = i18n('chat.composerOverflowMenu', '更多工具');
  const clearAvatarToolAriaLabel = i18n('chat.clearAvatarToolAriaLabel', '取消道具');

  useEffect(() => {
    draftRef.current = draft;
  }, [draft]);

  useEffect(() => {
    compactPreviewTextVisibleRef.current = compactPreviewTextVisible;
  }, [compactPreviewTextVisible]);

  useEffect(() => {
    speechPlaybackStateRef.current = speechPlaybackState;
  }, [speechPlaybackState]);

  useEffect(() => {
    const handleAssistantTurnStart = (event: Event) => {
      const detail = (event as CustomEvent).detail as Record<string, unknown> | undefined;
      const turnId = detail?.turnId ? String(detail.turnId) : `assistant-gap-${Date.now()}`;
      setCompactCaptionState(current => (
        current?.turnId === turnId ? current : null
      ));
      setCompactAssistantStreamingGap({
        turnId,
        acceptStreaming: true,
      });
    };
    const handleAssistantTurnBoundary = (event: Event) => {
      const detail = (event as CustomEvent).detail as Record<string, unknown> | undefined;
      const turnId = detail?.turnId ? String(detail.turnId) : `assistant-gap-ended-${Date.now()}`;
      setCompactCaptionState(current => (
        current?.turnId === turnId
          ? {
            ...current,
            isEnded: true,
          }
          : current
      ));
      setCompactAssistantStreamingGap({
        turnId,
        acceptStreaming: false,
      });
    };
    const handleCompactCaptionUpdate = (event: Event) => {
      const detail = (event as CustomEvent).detail as Record<string, unknown> | undefined;
      const turnId = detail?.turnId ? String(detail.turnId) : '';
      const text = detail?.text ? normalizeCompactPreviewText(String(detail.text)) : '';
      const rawSegmentId = detail?.segmentId ?? detail?.segmentIndex;
      const explicitSegmentId = rawSegmentId !== undefined && rawSegmentId !== null && rawSegmentId !== ''
        ? String(rawSegmentId)
        : '';
      if (!turnId || !text) {
        return;
      }
      setCompactCaptionState(current => {
        if (!current || current.turnId !== turnId) {
          const segmentId = explicitSegmentId || 'legacy-segment-0';
          return {
            turnId,
            segmentId,
            lastSegmentText: text,
            segments: [{
              segmentId,
              text,
            }],
            text,
          };
        }
        const currentSegments = current.segments.length > 0
          ? current.segments
          : [{
            segmentId: current.segmentId || 'legacy-segment-0',
            text: current.lastSegmentText || current.text,
          }];
        const previousSegmentText = current.lastSegmentText || '';
        const segmentId = explicitSegmentId
          || (
            !previousSegmentText
            || text === previousSegmentText
            || text.startsWith(previousSegmentText)
            || previousSegmentText.startsWith(text)
              ? current.segmentId
              : `legacy-segment-${currentSegments.length}`
          );
        const existingSegmentIndex = currentSegments.findIndex(segment => segment.segmentId === segmentId);
        const nextSegments = existingSegmentIndex >= 0
          ? currentSegments.map((segment, index) => (
            index === existingSegmentIndex
              ? {
                segmentId,
                text,
              }
              : segment
          ))
          : currentSegments.concat({
            segmentId,
            text,
          });
        const nextText = normalizeCompactPreviewText(nextSegments.map(segment => segment.text).join(' '));
        return {
          turnId,
          segmentId,
          lastSegmentText: text,
          segments: nextSegments,
          text: nextText,
          isEnded: false,
        };
      });
    };

    window.addEventListener('neko-assistant-turn-start', handleAssistantTurnStart);
    window.addEventListener('neko-assistant-turn-ending', handleAssistantTurnBoundary);
    window.addEventListener('neko-assistant-turn-end', handleAssistantTurnBoundary);
    window.addEventListener('neko-compact-caption-update', handleCompactCaptionUpdate);
    return () => {
      window.removeEventListener('neko-assistant-turn-start', handleAssistantTurnStart);
      window.removeEventListener('neko-assistant-turn-ending', handleAssistantTurnBoundary);
      window.removeEventListener('neko-assistant-turn-end', handleAssistantTurnBoundary);
      window.removeEventListener('neko-compact-caption-update', handleCompactCaptionUpdate);
    };
  }, []);

  // Focus 凝神 indicator: reflect backend enter/exit. The bridge in
  // app-websocket.js translates the `focus_state` ws message into this event.
  useEffect(() => {
    const handleFocusState = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean }>).detail;
      setFocusActive(Boolean(detail && detail.active));
    };
    window.addEventListener('neko-focus-state', handleFocusState);
    return () => {
      window.removeEventListener('neko-focus-state', handleFocusState);
    };
  }, []);

  // Focus 凝神 thinking-dots: show a "…" bubble at the tail of the history while
  // a Focus turn is thinking-on but hasn't emitted visible content yet. Cleared
  // when she starts speaking (backend) or on Focus exit (defensive).
  useEffect(() => {
    const handleThinking = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean }>).detail;
      setFocusThinking(Boolean(detail && detail.active));
    };
    const handleFocusState = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean }>).detail;
      if (!(detail && detail.active)) setFocusThinking(false);
    };
    window.addEventListener('neko-focus-thinking', handleThinking);
    window.addEventListener('neko-focus-state', handleFocusState);
    return () => {
      window.removeEventListener('neko-focus-thinking', handleThinking);
      window.removeEventListener('neko-focus-state', handleFocusState);
    };
  }, []);

  // Focus 凝神 edge glow: charge-driven, scaled on the app-shell via CSS vars.
  useFocusGlow(appShellRef);

  useEffect(() => {
    if (compactMessagePreview?.isAssistant && compactMessagePreview.isStreaming) {
      setCompactAssistantStreamingGap(currentGap => {
        if (!currentGap) {
          return null;
        }
        if (
          !compactMessagePreview.turnId
          || compactMessagePreview.turnId !== currentGap.turnId
        ) {
          return currentGap;
        }
        if (!currentGap.acceptStreaming) {
          return currentGap;
        }
        return null;
      });
    }
  }, [
    compactAssistantStreamingGap?.acceptStreaming,
    compactAssistantStreamingGap?.turnId,
    compactMessagePreview?.isAssistant,
    compactMessagePreview?.isStreaming,
    compactMessagePreview?.messageId,
    compactMessagePreview?.turnId,
  ]);

  useEffect(() => {
    isCompactSurfaceRef.current = isCompactSurface;
  }, [isCompactSurface]);

  useEffect(() => {
    compactSpeechVisibleLengthRef.current = compactSpeechVisibleLength;
  }, [compactSpeechVisibleLength]);

  useEffect(() => {
    if (compactMessagePreview?.isGuide) {
      compactSpeechPreviewIdRef.current = '';
      compactSpeechPreviewTextRef.current = '';
      compactSpeechPreviewTurnIdRef.current = '';
      return;
    }
    if (compactMessagePreview?.isStreaming && compactMessagePreview.isAssistant) {
      compactSpeechPreviewIdRef.current = compactMessagePreview.messageId;
      compactSpeechPreviewTextRef.current = compactMessagePreview.fullText || compactMessagePreview.text || '';
      compactSpeechPreviewTurnIdRef.current = compactMessagePreview.turnId || '';
    } else if (compactSpeechPreviewIdRef.current && (
      compactMessagePreview?.messageId
      && compactSpeechPreviewIdRef.current !== compactMessagePreview.messageId
    )) {
      compactSpeechPreviewIdRef.current = '';
      compactSpeechPreviewTextRef.current = '';
      compactSpeechPreviewTurnIdRef.current = '';
    }
  }, [
    compactMessagePreview?.fullText,
    compactMessagePreview?.isAssistant,
    compactMessagePreview?.isGuide,
    compactMessagePreview?.isStreaming,
    compactMessagePreview?.messageId,
    compactMessagePreview?.text,
    compactMessagePreview?.turnId,
  ]);

  useEffect(() => {
    if (!compactMessagePreview && compactPreservedSpeechActive) {
      return;
    }
    if (compactSpeechFallbackTimerRef.current !== null) {
      window.clearTimeout(compactSpeechFallbackTimerRef.current);
      compactSpeechFallbackTimerRef.current = null;
    }
    // Decouple the input-bar caption from per-bubble identity. The merged-turn
    // preview is re-keyed to the latest streaming bubble's id, so every new
    // bubble changes messageId even though it belongs to the same turn. While
    // we're still inside that turn (same turnStartId), keep the revealed length
    // so the caption continues appending instead of replaying the whole turn;
    // only a genuinely new turn rewinds the reveal to the start. Keyed on the
    // turn anchor rather than a text prefix, so two turns whose text happens to
    // share a prefix can't be mistaken for a continuation.
    const previousRevealTurnId = compactSpeechRevealTurnIdRef.current;
    const nextRevealTurnId = compactPreviewIsStreaming ? (compactMessagePreview?.turnStartId || '') : '';
    const continuesPreviousTurn = nextRevealTurnId.length > 0
      && nextRevealTurnId === previousRevealTurnId;
    const seedVisibleLength = continuesPreviousTurn
      ? Math.min(compactSpeechVisibleLengthRef.current, compactPreviewText.length)
      : 0;
    // When the same turn continues, keep or immediately start the fallback
    // reveal driver so appended text continues without a 700ms idle gap. The
    // playback state is still reset to false so a finished bubble's audio can't
    // snap the appended text to full.
    const sameTurnHasAppendedText = continuesPreviousTurn
      && seedVisibleLength < compactPreviewText.length;
    const keepFallbackReveal = continuesPreviousTurn && (
      compactSpeechFallbackRevealRef.current
      || sameTurnHasAppendedText
    );
    compactSpeechRevealTurnIdRef.current = nextRevealTurnId;

    compactSpeechVisibleLengthRef.current = seedVisibleLength;
    compactSpeechPlaybackStartedRef.current = false;
    compactSpeechFallbackRevealRef.current = keepFallbackReveal;
    compactSpeechRevealCarryRef.current = 0;
    compactSpeechLastFrameTimeRef.current = 0;
    setCompactSpeechVisibleLength(seedVisibleLength);
    setCompactSpeechFallbackRevealActive(keepFallbackReveal);
  }, [compactMessagePreview, compactMessagePreview?.messageId, compactPreservedSpeechActive]);

  useEffect(() => {
    if (!compactPreviewIsStreaming) {
      if (compactSpeechFallbackTimerRef.current !== null) {
        window.clearTimeout(compactSpeechFallbackTimerRef.current);
        compactSpeechFallbackTimerRef.current = null;
      }
      compactSpeechVisibleLengthRef.current = 0;
      compactSpeechPlaybackStartedRef.current = false;
      compactSpeechFallbackRevealRef.current = false;
      compactSpeechRevealCarryRef.current = 0;
      compactSpeechLastFrameTimeRef.current = 0;
      setCompactSpeechVisibleLength(0);
      setCompactSpeechFallbackRevealActive(false);
      return;
    }

    if (!compactMatchedSpeechPlaybackState?.active) {
      return;
    }
    const estimatedAudioTime = getEstimatedSpeechAudioTime(compactMatchedSpeechPlaybackState);
    if (estimatedAudioTime >= compactMatchedSpeechPlaybackState.playbackStartAudioTime) {
      compactSpeechPlaybackStartedRef.current = true;
      if (compactSpeechFallbackTimerRef.current !== null) {
        window.clearTimeout(compactSpeechFallbackTimerRef.current);
        compactSpeechFallbackTimerRef.current = null;
      }
      if (compactSpeechFallbackRevealRef.current) {
        compactSpeechFallbackRevealRef.current = false;
        setCompactSpeechFallbackRevealActive(false);
      }
    }
  }, [compactMatchedSpeechPlaybackState, compactPreviewIsStreaming]);

  useEffect(() => {
    if (compactSpeechFallbackTimerRef.current !== null) {
      window.clearTimeout(compactSpeechFallbackTimerRef.current);
      compactSpeechFallbackTimerRef.current = null;
    }
    if (!compactPreviewIsStreaming || compactPreviewText.length <= 0) {
      return undefined;
    }

    compactSpeechFallbackTimerRef.current = window.setTimeout(() => {
      compactSpeechFallbackTimerRef.current = null;
      const playbackState = speechPlaybackStateRef.current;
      const playbackMatchesPreview = isSpeechPlaybackStateForCompactPreview(
        playbackState,
        compactMessagePreview,
      );
      const playbackHasStarted = !!playbackState?.active
        && playbackMatchesPreview
        && getEstimatedSpeechAudioTime(playbackState) >= playbackState.playbackStartAudioTime;
      if (
        !isCompactSurfaceRef.current
        || compactSpeechPlaybackStartedRef.current
        || playbackHasStarted
        // Bail when the reveal is already being driven or has finished — NOT
        // merely when visibleLength > 0. A same-turn continuation seeds a
        // nonzero prefix that may still be stalled (e.g. the previous bubble was
        // revealed by speech, the appended bubble gets no playback/unavailable
        // signal); the old `> 0` guard left that frozen. Let the timer engage so
        // it reveals the appended text instead.
        || compactSpeechFallbackRevealRef.current
        || compactSpeechVisibleLengthRef.current >= compactPreviewText.length
      ) {
        return;
      }
      compactSpeechFallbackRevealRef.current = true;
      compactSpeechRevealCarryRef.current = 0;
      compactSpeechLastFrameTimeRef.current = 0;
      // Resume from the already-seeded prefix (continuation) rather than rewinding
      // to the first char; only an unseeded first bubble starts at 1.
      compactSpeechVisibleLengthRef.current = Math.max(
        compactSpeechVisibleLengthRef.current,
        Math.min(1, compactPreviewText.length),
      );
      setCompactSpeechVisibleLength(compactSpeechVisibleLengthRef.current);
      setCompactSpeechFallbackRevealActive(true);
    }, COMPACT_SPEECH_FALLBACK_REVEAL_DELAY_MS);

    return () => {
      if (compactSpeechFallbackTimerRef.current !== null) {
        window.clearTimeout(compactSpeechFallbackTimerRef.current);
        compactSpeechFallbackTimerRef.current = null;
      }
    };
  }, [compactMessagePreview, compactPreviewIsStreaming, compactPreviewText.length]);

  useEffect(() => {
    function handleAssistantSpeechUnavailable(event: Event) {
      if (!isCompactSurfaceRef.current || !compactPreviewIsStreaming || !compactMessagePreview?.isAssistant) {
        return;
      }
      const detail = (event as CustomEvent).detail as Record<string, unknown> | undefined;
      const eventTurnId = detail?.turnId ? String(detail.turnId) : '';
      if (eventTurnId && compactMessagePreview.turnId && eventTurnId !== compactMessagePreview.turnId) {
        return;
      }
      compactSpeechFallbackRevealRef.current = true;
      compactSpeechRevealCarryRef.current = 0;
      compactSpeechLastFrameTimeRef.current = 0;
      setCompactSpeechFallbackRevealActive(true);
    }

    window.addEventListener('neko-assistant-speech-unavailable', handleAssistantSpeechUnavailable);
    return () => {
      window.removeEventListener('neko-assistant-speech-unavailable', handleAssistantSpeechUnavailable);
    };
  }, [compactMessagePreview, compactPreviewIsStreaming]);

  useEffect(() => {
    if (compactSpeechAnimationFrameRef.current !== null) {
      window.cancelAnimationFrame(compactSpeechAnimationFrameRef.current);
      compactSpeechAnimationFrameRef.current = null;
    }
    compactSpeechRevealCarryRef.current = 0;
    compactSpeechLastFrameTimeRef.current = 0;

    if (!compactPreviewIsStreaming) {
      return;
    }

    const tick = (frameTime: number) => {
      const playbackState = isSpeechPlaybackStateForCompactPreview(
        speechPlaybackStateRef.current,
        compactMessagePreview,
      ) ? speechPlaybackStateRef.current : null;
      const fallbackReveal = compactSpeechFallbackRevealRef.current;
      const shouldContinueAfterSpeech = (compactSpeechPlaybackStartedRef.current || fallbackReveal)
        && compactSpeechVisibleLengthRef.current < compactPreviewText.length;
      if (!playbackState?.active && compactSpeechPlaybackStartedRef.current && !fallbackReveal) {
        if (compactSpeechVisibleLengthRef.current < compactPreviewText.length) {
          compactSpeechVisibleLengthRef.current = compactPreviewText.length;
          setCompactSpeechVisibleLength(compactPreviewText.length);
        }
        compactSpeechAnimationFrameRef.current = null;
        return;
      }
      if (!playbackState?.active && !shouldContinueAfterSpeech) {
        compactSpeechAnimationFrameRef.current = null;
        return;
      }
      const audioDuration = playbackState
        ? playbackState.playbackEndAudioTime - playbackState.playbackStartAudioTime
        : 0;
      if (compactPreviewText.length <= 0) {
        compactSpeechAnimationFrameRef.current = window.requestAnimationFrame(tick);
        return;
      }
      const estimatedAudioTime = playbackState ? getEstimatedSpeechAudioTime(playbackState) : 0;
      const speechHasStarted = !!playbackState?.active
        && estimatedAudioTime >= playbackState.playbackStartAudioTime;
      if (!speechHasStarted && !shouldContinueAfterSpeech) {
        compactSpeechAnimationFrameRef.current = window.requestAnimationFrame(tick);
        return;
      }
      if (speechHasStarted) {
        compactSpeechPlaybackStartedRef.current = true;
      }

      if (compactSpeechLastFrameTimeRef.current <= 0) {
        compactSpeechLastFrameTimeRef.current = frameTime;
      }
      const deltaSeconds = Math.max(0, (frameTime - compactSpeechLastFrameTimeRef.current) / 1000);
      compactSpeechLastFrameTimeRef.current = frameTime;

      const charsPerSecond = playbackState?.active && audioDuration > 0.05
        ? compactPreviewText.length / getCompactSpeechRevealDuration(compactPreviewText.length, audioDuration)
        : COMPACT_SPEECH_REVEAL_MAX_CHARS_PER_SECOND;
      compactSpeechRevealCarryRef.current += charsPerSecond * deltaSeconds;
      const step = Math.floor(compactSpeechRevealCarryRef.current);
      if (step > 0) {
        compactSpeechRevealCarryRef.current -= step;
        const nextLength = Math.min(compactPreviewText.length, compactSpeechVisibleLengthRef.current + step);
        if (nextLength > compactSpeechVisibleLengthRef.current) {
          compactSpeechVisibleLengthRef.current = nextLength;
          setCompactSpeechVisibleLength(nextLength);
        }
      }

      compactSpeechAnimationFrameRef.current = window.requestAnimationFrame(tick);
    };

    compactSpeechAnimationFrameRef.current = window.requestAnimationFrame(tick);
    return () => {
      if (compactSpeechAnimationFrameRef.current !== null) {
        window.cancelAnimationFrame(compactSpeechAnimationFrameRef.current);
        compactSpeechAnimationFrameRef.current = null;
      }
    };
  }, [
    compactMessagePreview,
    compactPreviewIsStreaming,
    compactPreviewText.length,
    compactPreviewSpeechDuration,
    compactSpeechFallbackRevealActive,
  ]);

  useEffect(() => {
    const readState = (value: unknown): SpeechPlaybackState | null => {
      if (!value || typeof value !== 'object') {
        return null;
      }
      const state = value as Record<string, unknown>;
      const audioContextTime = Number(state.audioContextTime);
      const playbackStartAudioTime = Number(state.playbackStartAudioTime);
      const playbackEndAudioTime = Number(state.playbackEndAudioTime);
      return {
        active: !!state.active,
        turnId: state.turnId ? String(state.turnId) : null,
        playbackTurnId: state.playbackTurnId ? String(state.playbackTurnId) : null,
        speechId: state.speechId ? String(state.speechId) : null,
        audioContextTime: Number.isFinite(audioContextTime) ? audioContextTime : 0,
        playbackStartAudioTime: Number.isFinite(playbackStartAudioTime) ? playbackStartAudioTime : 0,
        playbackEndAudioTime: Number.isFinite(playbackEndAudioTime) ? playbackEndAudioTime : 0,
        updatedAt: typeof state.updatedAt === 'number' ? state.updatedAt : Date.now(),
      };
    };

    const applySpeechPlaybackState = (nextState: SpeechPlaybackState | null) => {
      if (!nextState) return;
      speechPlaybackStateRef.current = nextState;
      if (isCompactSurfaceRef.current) {
        setSpeechPlaybackState(nextState);
      }
    };

    const existingState = readState((window as Window & { NekoSpeechPlaybackState?: unknown }).NekoSpeechPlaybackState);
    if (existingState) {
      applySpeechPlaybackState(existingState);
    } else {
      try {
        applySpeechPlaybackState(readState(JSON.parse(localStorage.getItem(SPEECH_PLAYBACK_STATE_STORAGE_KEY) || 'null')));
      } catch (_) {
        // Ignore corrupt cross-window playback state snapshots.
      }
    }

    const handleSpeechPlaybackState = (event: Event) => {
      const nextState = readState((event as CustomEvent).detail);
      applySpeechPlaybackState(nextState);
    };
    const handleStoragePlaybackState = (event: StorageEvent) => {
      if (event.key !== SPEECH_PLAYBACK_STATE_STORAGE_KEY) return;
      try {
        const nextState = readState(JSON.parse(event.newValue || 'null'));
        applySpeechPlaybackState(nextState);
      } catch (_) {
        // Ignore corrupt cross-window playback state snapshots.
      }
    };
    let speechPlaybackChannel: BroadcastChannel | null = null;
    if (typeof BroadcastChannel !== 'undefined') {
      try {
        speechPlaybackChannel = new BroadcastChannel(SPEECH_PLAYBACK_CHANNEL_NAME);
        speechPlaybackChannel.addEventListener('message', (event) => {
          const nextState = readState(event.data);
          applySpeechPlaybackState(nextState);
        });
      } catch (_) {
        speechPlaybackChannel = null;
      }
    }
    window.addEventListener('neko-speech-playback-state', handleSpeechPlaybackState);
    window.addEventListener('storage', handleStoragePlaybackState);
    return () => {
      window.removeEventListener('neko-speech-playback-state', handleSpeechPlaybackState);
      window.removeEventListener('storage', handleStoragePlaybackState);
      speechPlaybackChannel?.close();
    };
  }, []);

  useEffect(() => {
    const textNode = compactPreviewTextRef.current;
    if (!textNode) return;
    if (!isCompactSurface || !compactPreviewAllowsScroll) {
      textNode.scrollLeft = 0;
      return;
    }
    textNode.scrollLeft = textNode.scrollWidth;
  }, [compactPreviewAllowsScroll, compactPreviewDisplayText, isCompactSurface]);

  const handleCompactPreviewWheel = useCallback((event: ReactWheelEvent<HTMLSpanElement>) => {
    const textNode = event.currentTarget;
    const maxScrollLeft = Math.max(0, textNode.scrollWidth - textNode.clientWidth);
    if (maxScrollLeft <= 0) return;

    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
      ? event.deltaX
      : event.deltaY;
    if (delta === 0) return;

    event.preventDefault();
    event.stopPropagation();
    textNode.scrollLeft = Math.max(0, Math.min(maxScrollLeft, textNode.scrollLeft + delta));
  }, []);

  useEffect(() => {
    if (!isCompactSurface) return;
    if (effectiveCompactChatState !== 'input') return;
    if (composerDisabled) return;
    const inputNode = compactInputRef.current;
    if (!inputNode) return;
    if (document.activeElement === inputNode) return;
    inputNode.focus();
    const selectionEnd = inputNode.value.length;
    inputNode.setSelectionRange(selectionEnd, selectionEnd);
  }, [composerDisabled, effectiveCompactChatState, isCompactSurface]);

  useEffect(() => {
    if (!isCompactSurface) return;
    if (!compactChoiceLayerOpen) return;

    const shellNode = compactInputShellRef.current;
    const layerNode = compactChoiceLayerRef.current;
    if (!shellNode || !layerNode) return;

    const gap = 16;
    let frameId: number | null = null;

    const syncChoiceLayerSurfaceVars = (
      surfaceRect: { left: number; top: number; width: number; height: number },
      layerNode: HTMLElement,
    ) => {
      layerNode.style.setProperty('--compact-choice-surface-left', `${surfaceRect.left}px`);
      layerNode.style.setProperty('--compact-choice-surface-top', `${surfaceRect.top}px`);
      layerNode.style.setProperty('--compact-choice-surface-width', `${Math.max(1, surfaceRect.width)}px`);
      layerNode.style.setProperty('--compact-choice-surface-height', `${Math.max(1, surfaceRect.height)}px`);
    };

    const getDesktopCompactLayout = (event?: Event) => {
      const eventDetail = event instanceof CustomEvent ? event.detail : null;
      if (eventDetail && typeof eventDetail === 'object') {
        return eventDetail as DesktopCompactChoicePlacementLayout;
      }
      return (window as typeof window & {
        __nekoDesktopCompactLayout?: DesktopCompactChoicePlacementLayout | null;
      }).__nekoDesktopCompactLayout;
    };

    const getDesktopLayoutSurfaceRect = (layout?: DesktopCompactChoicePlacementLayout | null) => {
      const surface = layout?.surface;
      const left = Number(surface?.left);
      const top = Number(surface?.top);
      const width = Number(surface?.width);
      const height = Number(surface?.height);
      if (
        !Number.isFinite(left)
        || !Number.isFinite(top)
        || !Number.isFinite(width)
        || !Number.isFinite(height)
        || width <= 0
        || height <= 0
      ) {
        return null;
      }
      return {
        left,
        top,
        right: left + width,
        bottom: top + height,
        width,
        height,
      };
    };

    const syncChoiceLayerSurfaceVarsFromDesktopLayout = (layout?: DesktopCompactChoicePlacementLayout | null) => {
      const nextLayerNode = compactChoiceLayerRef.current;
      if (!nextLayerNode) return false;
      const surfaceRect = getDesktopLayoutSurfaceRect(layout ?? getDesktopCompactLayout());
      if (!surfaceRect) return false;
      syncChoiceLayerSurfaceVars(surfaceRect, nextLayerNode);
      return true;
    };

    const getDesktopPlacementSpace = (surfaceRect: { top: number; height: number }) => {
      const layout = (window as typeof window & {
        __nekoDesktopCompactLayout?: DesktopCompactChoicePlacementLayout | null;
      }).__nekoDesktopCompactLayout;
      const windowBounds = layout?.windowBounds;
      const workArea = layout?.workArea;
      const windowY = Number(windowBounds?.y);
      const workAreaY = Number(workArea?.y);
      const workAreaHeight = Number(workArea?.height);
      if (
        !Number.isFinite(windowY)
        || !Number.isFinite(workAreaY)
        || !Number.isFinite(workAreaHeight)
        || workAreaHeight <= 0
      ) {
        return null;
      }

      const layoutSurfaceTop = Number(layout?.surface?.top);
      const layoutSurfaceHeight = Number(layout?.surface?.height);
      const measuredTop = Number.isFinite(surfaceRect.top) ? surfaceRect.top : layoutSurfaceTop;
      const measuredHeight = Number.isFinite(surfaceRect.height) && surfaceRect.height > 0
        ? surfaceRect.height
        : layoutSurfaceHeight;
      const surfaceScreenTop = windowY + (Number.isFinite(measuredTop) ? measuredTop : 0);
      const surfaceScreenBottom = surfaceScreenTop
        + (Number.isFinite(measuredHeight) && measuredHeight > 0 ? measuredHeight : Math.max(1, surfaceRect.height));
      const workAreaBottom = workAreaY + workAreaHeight;
      return {
        availableAbove: Math.max(0, surfaceScreenTop - workAreaY),
        availableBelow: Math.max(0, workAreaBottom - surfaceScreenBottom),
      };
    };

    const updatePlacement = (desktopLayoutOverride?: DesktopCompactChoicePlacementLayout | null) => {
      const nextShellNode = compactInputShellRef.current;
      const nextLayerNode = compactChoiceLayerRef.current;
      if (!nextShellNode || !nextLayerNode) return;

      const desktopLayout = desktopLayoutOverride ?? getDesktopCompactLayout();
      const desktopForcedPlacement = desktopLayout?.compactChoicePlacement;
      const shellRect = nextShellNode.getBoundingClientRect();
      const desktopLayoutSurfaceRect = getDesktopLayoutSurfaceRect(desktopLayout);
      const surfaceRect = desktopLayoutSurfaceRect ?? shellRect;
      syncChoiceLayerSurfaceVars(surfaceRect, nextLayerNode);
      if (desktopForcedPlacement === 'above' || desktopForcedPlacement === 'below') {
        setCompactChoiceLayerPlacement(current => (current === desktopForcedPlacement ? current : desktopForcedPlacement));
        return;
      }
      const layerRect = nextLayerNode.getBoundingClientRect();
      const layerHeight = Math.max(layerRect.height, nextLayerNode.scrollHeight);
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const desktopSpace = getDesktopPlacementSpace(surfaceRect);
      const availableBelow = desktopSpace?.availableBelow ?? Math.max(0, viewportHeight - surfaceRect.bottom);
      const availableAbove = desktopSpace?.availableAbove ?? Math.max(0, surfaceRect.top);
      const requiredSpace = layerHeight + gap;
      const nextPlacement = availableBelow >= requiredSpace
        ? 'below'
        : availableAbove >= requiredSpace
          ? 'above'
          : availableBelow >= availableAbove
            ? 'below'
            : 'above';
      setCompactChoiceLayerPlacement((current) => {
        if (current === nextPlacement) return current;
        if (
          current === 'above'
          && nextPlacement === 'below'
          && availableBelow < requiredSpace + COMPACT_CHOICE_PLACEMENT_HYSTERESIS
        ) {
          return current;
        }
        if (
          current === 'below'
          && nextPlacement === 'above'
          && availableAbove < requiredSpace + COMPACT_CHOICE_PLACEMENT_HYSTERESIS
        ) {
          return current;
        }
        return nextPlacement;
      });
    };

    let scheduledDesktopLayoutOverride: DesktopCompactChoicePlacementLayout | null | undefined;
    const schedulePlacementUpdate = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        const desktopLayoutOverride = scheduledDesktopLayoutOverride;
        scheduledDesktopLayoutOverride = undefined;
        updatePlacement(desktopLayoutOverride);
      });
    };
    const schedulePlacementUpdateWithDesktopLayout = (layout?: DesktopCompactChoicePlacementLayout | null) => {
      scheduledDesktopLayoutOverride = layout;
      schedulePlacementUpdate();
    };

    syncChoiceLayerSurfaceVarsFromDesktopLayout();
    schedulePlacementUpdate();

    const visualViewport = window.visualViewport;
    const handleDesktopCompactLayoutChange = (event: Event) => {
      const layout = getDesktopCompactLayout(event);
      syncChoiceLayerSurfaceVarsFromDesktopLayout(layout);
      schedulePlacementUpdateWithDesktopLayout(layout);
    };
    window.addEventListener('resize', schedulePlacementUpdate);
    window.addEventListener('neko:compact-surface-layout-change', schedulePlacementUpdate);
    window.addEventListener('neko:desktop-compact-layout-change', handleDesktopCompactLayoutChange);
    visualViewport?.addEventListener('resize', schedulePlacementUpdate);
    visualViewport?.addEventListener('scroll', schedulePlacementUpdate);

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => {
        schedulePlacementUpdate();
      });
      observer.observe(shellNode);
      observer.observe(layerNode);
    }

    return () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      window.removeEventListener('resize', schedulePlacementUpdate);
      window.removeEventListener('neko:compact-surface-layout-change', schedulePlacementUpdate);
      window.removeEventListener('neko:desktop-compact-layout-change', handleDesktopCompactLayoutChange);
      visualViewport?.removeEventListener('resize', schedulePlacementUpdate);
      visualViewport?.removeEventListener('scroll', schedulePlacementUpdate);
      observer?.disconnect();
    };
  }, [compactChoiceLayerOpen, galgameOptions.length, galgameOptionsLoading, isCompactSurface, choicePrompt]);

  const requestCompactChatState = useCallback((nextState: CompactChatState) => {
    if (!isCompactSurface) return;
    if (nextState === 'input' && compactTextEntryLocked) return;
    if (!isCompactChatStateControlled) {
      setUncontrolledCompactChatState(nextState);
    }
    onCompactChatStateChange?.(nextState);
  }, [compactTextEntryLocked, isCompactSurface, isCompactChatStateControlled, onCompactChatStateChange]);

  const applyCompactSurfaceResizeWidthVar = useCallback((width: number | null) => {
    const shell = compactInputShellRef.current;
    if (isDesktopCompactSurfaceLayoutActive()) {
      document.documentElement.style.removeProperty('--compact-surface-resize-width');
      shell?.style.removeProperty('--compact-surface-resize-width');
      return;
    }
    if (width === null) {
      document.documentElement.style.removeProperty('--compact-surface-resize-width');
      shell?.style.removeProperty('--compact-surface-resize-width');
      return;
    }
    const value = `${getClampedCompactSurfaceResizeWidth(width)}px`;
    document.documentElement.style.setProperty('--compact-surface-resize-width', value);
    shell?.style.setProperty('--compact-surface-resize-width', value);
  }, [getClampedCompactSurfaceResizeWidth]);

  const dispatchCompactSurfaceResizeRequest = useCallback((
    side: CompactSurfaceResizeSide,
    width: number,
    phase: 'start' | 'move' | 'end',
    options?: { keepCarrier?: boolean },
  ) => {
    const resizeState = compactSurfaceResizeStateRef.current;
    const screenRect = resizeState ? {
      left: side === 'left' ? resizeState.anchorRightScreen - width : resizeState.anchorLeftScreen,
      top: resizeState.anchorTopScreen,
      width,
      height: resizeState.surfaceHeight,
      right: side === 'left' ? resizeState.anchorRightScreen : resizeState.anchorLeftScreen + width,
      bottom: resizeState.anchorTopScreen + resizeState.surfaceHeight,
    } : undefined;
    window.dispatchEvent(new CustomEvent('neko:compact-surface-resize-request', {
      detail: { side, width, phase, screenRect, keepCarrier: options?.keepCarrier === true },
    }));
  }, []);

  const finishCompactSurfaceResize = useCallback((event?: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactSurfaceResizeStateRef.current;
    if (!resizeState) return;
    if (event && resizeState.pointerId !== event.pointerId) return;
    dispatchCompactSurfaceResizeRequest(resizeState.side, resizeState.lastWidth, 'end', {
      keepCarrier: resizeState.limitStalled,
    });
    applyCompactSurfaceResizeWidthVar(null);
    setCompactSurfaceResizeWidth(null);
    const captureTarget = resizeState.captureTarget;
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(resizeState.pointerId)) {
          captureTarget.releasePointerCapture(resizeState.pointerId);
        }
      } catch (_) {}
    }
    compactSurfaceResizeStateRef.current = null;
  }, [applyCompactSurfaceResizeWidthVar, dispatchCompactSurfaceResizeRequest]);

  const handleCompactSurfaceResizePointerDown = useCallback((
    side: CompactSurfaceResizeSide,
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    if (!isCompactSurface) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startWidth = getCurrentCompactSurfaceWidth();
    const frameRect = getCurrentCompactSurfaceFrameRect();
    const shellRect = compactInputShellRef.current?.getBoundingClientRect();
    const desktopLayout = (window as typeof window & {
      __nekoDesktopCompactLayout?: {
        surfaceScreenRect?: {
          left?: number;
          top?: number;
          width?: number;
          height?: number;
          right?: number;
        };
      };
    }).__nekoDesktopCompactLayout;
    const desktopSurface = desktopLayout?.surfaceScreenRect;
    const anchorLeftScreen = frameRect
      ? window.screenX + frameRect.left
      : (
        Number.isFinite(desktopSurface?.left)
          ? Number(desktopSurface?.left)
          : (shellRect ? window.screenX + shellRect.left : 0)
      );
    const anchorRightScreen = frameRect
      ? anchorLeftScreen + startWidth
      : (
        Number.isFinite(desktopSurface?.right)
          ? Number(desktopSurface?.right)
          : anchorLeftScreen + startWidth
      );
    const anchorTopScreen = frameRect
      ? window.screenY + frameRect.top
      : (
        Number.isFinite(desktopSurface?.top)
          ? Number(desktopSurface?.top)
          : (shellRect ? window.screenY + shellRect.top : 0)
      );
    const surfaceHeight = frameRect
      ? Math.max(1, frameRect.height)
      : (
        Number.isFinite(desktopSurface?.height) && Number(desktopSurface?.height) > 0
          ? Number(desktopSurface?.height)
          : Math.max(1, shellRect?.height ?? 58)
      );
    compactSurfaceResizeStateRef.current = {
      pointerId: event.pointerId,
      side,
      startPointerX: getCompactSurfaceResizePointerX(event),
      startWidth,
      lastWidth: startWidth,
      limitStalled: false,
      anchorLeftScreen,
      anchorRightScreen,
      anchorTopScreen,
      surfaceHeight,
      captureTarget: event.currentTarget,
    };
    applyCompactSurfaceResizeWidthVar(startWidth);
    compactInputToolFanPositionSyncRef.current?.();
    if (!isDesktopCompactSurfaceLayoutActive()) {
      setCompactSurfaceResizeWidth(startWidth);
    }
    dispatchCompactSurfaceResizeRequest(side, startWidth, 'start');
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  }, [
    applyCompactSurfaceResizeWidthVar,
    dispatchCompactSurfaceResizeRequest,
    getCurrentCompactSurfaceFrameRect,
    getCurrentCompactSurfaceWidth,
    isCompactSurface,
  ]);

  const handleCompactSurfaceResizePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactSurfaceResizeStateRef.current;
    if (!resizeState || resizeState.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    const pointerX = getCompactSurfaceResizePointerX(event);
    const deltaX = pointerX - resizeState.startPointerX;
    const signedDelta = resizeState.side === 'right' ? deltaX : -deltaX;
    const rawWidth = resizeState.startWidth + signedDelta;
    const nextWidth = getClampedCompactSurfaceResizeWidthForSide(
      resizeState.side,
      rawWidth,
      resizeState,
    );
    const hitLimit = nextWidth !== Math.round(rawWidth);
    if (nextWidth === resizeState.lastWidth) {
      if (hitLimit) {
        resizeState.startPointerX = pointerX;
        resizeState.startWidth = nextWidth;
        resizeState.limitStalled = true;
      }
      return;
    }
    resizeState.limitStalled = hitLimit;
    resizeState.lastWidth = nextWidth;
    if (hitLimit) {
      resizeState.startPointerX = pointerX;
      resizeState.startWidth = nextWidth;
    }
    applyCompactSurfaceResizeWidthVar(nextWidth);
    compactInputToolFanPositionSyncRef.current?.();
    if (!isDesktopCompactSurfaceLayoutActive()) {
      setCompactSurfaceResizeWidth(nextWidth);
    }
    dispatchCompactSurfaceResizeRequest(resizeState.side, nextWidth, 'move');
  }, [applyCompactSurfaceResizeWidthVar, dispatchCompactSurfaceResizeRequest, getClampedCompactSurfaceResizeWidthForSide]);

  const handleCompactSurfaceResizePointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    finishCompactSurfaceResize(event);
  }, [finishCompactSurfaceResize]);

  const handleCompactSurfaceResizePointerCancel = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    finishCompactSurfaceResize(event);
  }, [finishCompactSurfaceResize]);

  const getCompactHistorySlotMaxHeight = useCallback(() => {
    const base = getCompactHistoryViewportBase();
    // 上限只受「屏幕可用高度」约束（去掉对话条宽度挂钩那条）；panel 里 scroll 上方有 bar、下方有 controls，
    // 先扣掉这部分非滚动 chrome，scroll 区才不会吃满 anchor 把 controls / 底部气泡顶出可视/可点区。
    const anchorMax = base * COMPACT_HISTORY_SLOT_MAX_VIEWPORT_RATIO;
    return Math.round(Math.max(
      COMPACT_HISTORY_SLOT_MIN_HEIGHT,
      anchorMax - COMPACT_HISTORY_SLOT_CHROME_RESERVE,
    ));
  }, []);

  const getClampedCompactHistorySlotHeight = useCallback((height: number) => (
    Math.round(Math.max(
      COMPACT_HISTORY_SLOT_MIN_HEIGHT,
      Math.min(height, getCompactHistorySlotMaxHeight()),
    ))
  ), [getCompactHistorySlotMaxHeight]);

  // 用户未拖动过时（slot 为 null），起拖高度取 styles.css 默认公式值（width*1.18 / 63%），
  // 保证拖动第一帧从当前可见高度连续起步、不跳变。
  const getCompactHistoryStartHeight = useCallback(() => {
    // 起拖基准必须是「当前可见高度」（按当前约束 clamp 后）。存量高度可能来自更大屏 / 更宽 surface，
    // 此时面板已被钳到 max、若用 stale 大值做基准，向下拖会出现「先拖一段没反应」的死区。
    if (compactHistorySlotHeight !== null) {
      return getClampedCompactHistorySlotHeight(compactHistorySlotHeight);
    }
    const surfaceWidth = getCurrentCompactSurfaceWidth();
    const base = getCompactHistoryViewportBase();
    return getClampedCompactHistorySlotHeight(Math.round(Math.min(
      surfaceWidth * COMPACT_HISTORY_SLOT_DEFAULT_WIDTH_RATIO,
      base * COMPACT_HISTORY_SLOT_DEFAULT_VIEWPORT_RATIO,
    )));
  }, [compactHistorySlotHeight, getClampedCompactHistorySlotHeight, getCurrentCompactSurfaceWidth]);

  const applyCompactHistorySlotHeightVar = useCallback((height: number | null) => {
    if (typeof document === 'undefined') return;
    // 内容布局高度锚定在「最大高度」上（scroll-content min-height 用它），缩小 slot 只裁剪可视窗口、
    // 不再让内容随 slot 收缩 reflow。max 只挂屏幕/工作区高度，会随视口变化，故每次 apply 一并刷新。
    document.documentElement.style.setProperty(
      '--compact-history-slot-max-height',
      `${getCompactHistorySlotMaxHeight()}px`,
    );
    if (height === null) {
      document.documentElement.style.removeProperty('--compact-history-slot-height');
    } else {
      document.documentElement.style.setProperty(
        '--compact-history-slot-height',
        `${getClampedCompactHistorySlotHeight(height)}px`,
      );
    }
    // CSS 变量变更不会自己通知宿主；让宿主重算 history 命中 rect / Electron 窗口 bounds / 鼠标穿透区。
    // 同时通知 panel：slot/max 已变，按 autoScrollToBottom 把可视窗口重新锚定到下端（卷帘从上往下收）。
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-refresh'));
    }
  }, [getClampedCompactHistorySlotHeight, getCompactHistorySlotMaxHeight]);

  const dispatchCompactHistoryResizeRequest = useCallback((phase: 'start' | 'end' | 'cancel', options?: { keepCarrier?: boolean }) => {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent('neko:compact-history-resize-request', {
      detail: { phase, keepCarrier: options?.keepCarrier === true },
    }));
  }, []);

  const releaseCompactHistoryResizeCapture = useCallback((resizeState: CompactHistoryResizeState) => {
    const captureTarget = resizeState.captureTarget;
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(resizeState.pointerId)) {
          captureTarget.releasePointerCapture(resizeState.pointerId);
        }
      } catch (_) {}
    }
  }, []);

  const cancelCompactHistoryResize = useCallback(() => {
    const resizeState = compactHistoryResizeStateRef.current;
    if (!resizeState) return;
    releaseCompactHistoryResizeCapture(resizeState);
    if (resizeState.heightChanged) {
      setCompactHistorySlotHeight(resizeState.startedSlotHeight);
      applyCompactHistorySlotHeightVar(resizeState.startedSlotHeight);
    }
    compactHistoryResizeStateRef.current = null;
    setCompactHistoryResizeActive(false);
    setCompactHistoryResizeContentLocked(false);
    dispatchCompactHistoryResizeRequest('cancel', { keepCarrier: resizeState.limitStalled });
  }, [applyCompactHistorySlotHeightVar, dispatchCompactHistoryResizeRequest, releaseCompactHistoryResizeCapture]);

  const finishCompactHistoryResize = useCallback((event?: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactHistoryResizeStateRef.current;
    if (!resizeState) return;
    if (event && resizeState.pointerId !== event.pointerId) return;
    const phase = event && event.type === 'pointercancel' ? 'cancel' : 'end';
    // 只在真正拖动过才落库：纯点击不该把响应式默认高度锁成固定像素值（否则之后视口/宽度变化不再响应）。
    if (phase !== 'cancel' && resizeState.heightChanged && resizeState.lastHeight !== resizeState.initialHeight) {
      persistCompactHistorySlotHeight(resizeState.lastHeight);
      setCompactHistorySlotHeight(resizeState.lastHeight);
    } else if (resizeState.heightChanged) {
      setCompactHistorySlotHeight(resizeState.startedSlotHeight);
      applyCompactHistorySlotHeightVar(resizeState.startedSlotHeight);
    }
    releaseCompactHistoryResizeCapture(resizeState);
    compactHistoryResizeStateRef.current = null;
    setCompactHistoryResizeActive(false);
    setCompactHistoryResizeContentLocked(false);
    dispatchCompactHistoryResizeRequest(phase, { keepCarrier: resizeState.limitStalled });
  }, [applyCompactHistorySlotHeightVar, dispatchCompactHistoryResizeRequest, releaseCompactHistoryResizeCapture]);

  const handleCompactHistoryResizePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (!isCompactSurface) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const startHeight = getCompactHistoryStartHeight();
    compactHistoryResizeStateRef.current = {
      pointerId: event.pointerId,
      startPointerY: getCompactHistoryResizePointerY(event),
      startHeight,
      initialHeight: startHeight,
      lastHeight: getClampedCompactHistorySlotHeight(startHeight),
      startedSlotHeight: compactHistorySlotHeight,
      heightChanged: false,
      limitStalled: false,
      captureTarget: event.currentTarget,
    };
    setCompactHistoryResizeActive(true);
    setCompactHistoryResizeContentLocked(false);
    dispatchCompactHistoryResizeRequest('start');
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  }, [compactHistorySlotHeight, dispatchCompactHistoryResizeRequest, getClampedCompactHistorySlotHeight, getCompactHistoryStartHeight, isCompactSurface]);

  const handleCompactHistoryResizePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactHistoryResizeStateRef.current;
    if (!resizeState || resizeState.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    // bar 在堆砌区顶部：上拖（deltaY < 0）增高，下拖减高。
    const pointerY = getCompactHistoryResizePointerY(event);
    const deltaY = pointerY - resizeState.startPointerY;
    const rawHeight = resizeState.startHeight - deltaY;
    const maxHeight = getCompactHistorySlotMaxHeight();
    const nextHeight = getClampedCompactHistorySlotHeight(rawHeight);
    const hitLimit = rawHeight < COMPACT_HISTORY_SLOT_MIN_HEIGHT || rawHeight > maxHeight;
    if (nextHeight === resizeState.lastHeight) {
      if (hitLimit) {
        resizeState.startPointerY = pointerY;
        resizeState.startHeight = nextHeight;
        resizeState.limitStalled = true;
      }
      return;
    }
    resizeState.heightChanged = true;
    resizeState.limitStalled = hitLimit;
    resizeState.lastHeight = nextHeight;
    setCompactHistoryResizeContentLocked(true);
    if (hitLimit) {
      resizeState.startPointerY = pointerY;
      resizeState.startHeight = nextHeight;
    }
    setCompactHistorySlotHeight(nextHeight);
    applyCompactHistorySlotHeightVar(nextHeight);
  }, [applyCompactHistorySlotHeightVar, getClampedCompactHistorySlotHeight, getCompactHistorySlotMaxHeight]);

  const handleCompactHistoryResizePointerUp = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    finishCompactHistoryResize(event);
  }, [finishCompactHistoryResize]);

  const handleCompactHistoryResizePointerCancel = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    finishCompactHistoryResize(event);
  }, [finishCompactHistoryResize]);

  useEffect(() => {
    if (isCompactSurface && compactExportHistoryMounted) return;
    cancelCompactHistoryResize();
  }, [cancelCompactHistoryResize, compactExportHistoryMounted, isCompactSurface]);

  useEffect(() => () => {
    cancelCompactHistoryResize();
  }, [cancelCompactHistoryResize]);

  // 把已存/恢复的高度写进 CSS 变量（覆盖默认公式）；slot 为 null 时清掉、回落默认。
  useEffect(() => {
    if (!isCompactSurface) return;
    applyCompactHistorySlotHeightVar(compactHistorySlotHeight);
  }, [applyCompactHistorySlotHeightVar, compactHistorySlotHeight, isCompactSurface]);

  // 视口 / 工作区 / compact surface 宽度变化后，按新约束重写 CSS 变量（用新 max clamp 显示高度）。
  // 刻意不改 state、不覆盖 storage：存量 raw 值保留，换屏 / 改宽再放大时能恢复；起拖死区另由
  // getCompactHistoryStartHeight 对基准 clamp 解决。
  useEffect(() => {
    if (!isCompactSurface) return undefined;
    const reapplySlotHeight = () => applyCompactHistorySlotHeightVar(compactHistorySlotHeight);
    window.addEventListener('resize', reapplySlotHeight);
    window.addEventListener('neko:desktop-compact-layout-change', reapplySlotHeight);
    window.addEventListener('neko:compact-surface-resize-width-change', reapplySlotHeight);
    return () => {
      window.removeEventListener('resize', reapplySlotHeight);
      window.removeEventListener('neko:desktop-compact-layout-change', reapplySlotHeight);
      window.removeEventListener('neko:compact-surface-resize-width-change', reapplySlotHeight);
    };
  }, [applyCompactHistorySlotHeightVar, compactHistorySlotHeight, isCompactSurface]);

  useEffect(() => {
    if (!isCompactSurface || compactSurfaceEffectiveWidth === null) {
      applyCompactSurfaceResizeWidthVar(null);
      return;
    }
    applyCompactSurfaceResizeWidthVar(compactSurfaceEffectiveWidth);
    window.dispatchEvent(new CustomEvent('neko:compact-surface-resize-width-change', {
      detail: { width: compactSurfaceEffectiveWidth },
    }));
  }, [applyCompactSurfaceResizeWidthVar, compactSurfaceEffectiveWidth, isCompactSurface]);

  useEffect(() => () => {
    applyCompactSurfaceResizeWidthVar(null);
  }, [applyCompactSurfaceResizeWidthVar]);

  useEffect(() => {
    if (!isCompactSurface) return undefined;
    const clampExistingWidth = () => {
      if (isDesktopCompactSurfaceLayoutActive()) {
        setCompactSurfaceResizeWidth(null);
        applyCompactSurfaceResizeWidthVar(null);
        return;
      }
      setCompactSurfaceResizeWidth(current => (
        current === null ? current : getClampedCompactSurfaceResizeWidth(current)
      ));
    };
    window.addEventListener('resize', clampExistingWidth);
    window.addEventListener('neko:desktop-compact-layout-change', clampExistingWidth);
    return () => {
      window.removeEventListener('resize', clampExistingWidth);
      window.removeEventListener('neko:desktop-compact-layout-change', clampExistingWidth);
    };
  }, [getClampedCompactSurfaceResizeWidth, isCompactSurface]);

  useEffect(() => {
    if (!isCompactSurface) return undefined;
    const syncAppliedResizeWidth = (event: Event) => {
      if (isDesktopCompactSurfaceLayoutActive()) {
        setCompactSurfaceResizeWidth(null);
        applyCompactSurfaceResizeWidthVar(null);
        return;
      }
      const resizeState = compactSurfaceResizeStateRef.current;
      if (!resizeState) return;
      const width = Number((event as CustomEvent).detail?.width);
      if (!Number.isFinite(width) || width <= 0) return;
      const appliedWidth = getClampedCompactSurfaceResizeWidth(width);
      resizeState.lastWidth = appliedWidth;
      applyCompactSurfaceResizeWidthVar(appliedWidth);
      setCompactSurfaceResizeWidth(appliedWidth);
    };
    window.addEventListener('neko:compact-surface-layout-change', syncAppliedResizeWidth);
    return () => {
      window.removeEventListener('neko:compact-surface-layout-change', syncAppliedResizeWidth);
    };
  }, [applyCompactSurfaceResizeWidthVar, getClampedCompactSurfaceResizeWidth, isCompactSurface]);

  const clearCompactInputToolFanCloseTimer = useCallback(() => {
    if (compactInputToolFanCloseTimerRef.current === null) return;
    window.clearTimeout(compactInputToolFanCloseTimerRef.current);
    compactInputToolFanCloseTimerRef.current = null;
  }, []);

  const clearCompactInputToolFanInteractiveTimer = useCallback(() => {
    if (compactInputToolFanInteractiveTimerRef.current === null) return;
    window.clearTimeout(compactInputToolFanInteractiveTimerRef.current);
    compactInputToolFanInteractiveTimerRef.current = null;
  }, []);

  const clearCompactInputToolWheelDragGuardTimer = useCallback(() => {
    if (compactInputToolWheelDragGuardTimerRef.current === null) return;
    window.clearTimeout(compactInputToolWheelDragGuardTimerRef.current);
    compactInputToolWheelDragGuardTimerRef.current = null;
  }, []);

  const clearCompactInputToolWheelFastAnimationTimer = useCallback(() => {
    if (compactInputToolWheelFastAnimationTimerRef.current === null) return;
    window.clearTimeout(compactInputToolWheelFastAnimationTimerRef.current);
    compactInputToolWheelFastAnimationTimerRef.current = null;
  }, []);

  const clearCompactInputToolWheelChargeReleaseTimer = useCallback(() => {
    if (compactInputToolWheelChargeReleaseTimerRef.current !== null) {
      window.clearTimeout(compactInputToolWheelChargeReleaseTimerRef.current);
      compactInputToolWheelChargeReleaseTimerRef.current = null;
    }
    compactInputToolWheelChargeReleaseActiveRef.current = false;
    setCompactInputToolWheelChargeReleaseActive(false);
    setCompactInputToolWheelChargeReleaseVisualStepOffset(0);
    setCompactInputToolWheelDragOffsetRatio(0);
  }, []);

  const resetCompactInputToolWheelCharge = useCallback(() => {
    compactInputToolWheelChargeRef.current = createCompactToolWheelChargeState();
    setCompactInputToolWheelChargeRatio(0);
    setCompactInputToolWheelChargeDirection(null);
  }, []);

  const dispatchCompactToolWheelDragState = useCallback((active: boolean, pointerId?: number) => {
    setCompactInputToolWheelDragActive(active);
    window.dispatchEvent(new CustomEvent('neko:compact-tool-wheel-drag-state-change', {
      detail: {
        active,
        pointerId,
        timestamp: Date.now(),
      },
    }));
  }, []);

  // Tell the desktop shell to keep the whole wheel region solid while the fan is
  // open, so mouse-wheel scrolling responds across the entire circle (not only
  // over the icon buttons). Released when the fan closes. See preload
  // neko:compact-tool-fan-open-state-change.
  const dispatchCompactToolFanOpenState = useCallback((open: boolean) => {
    window.dispatchEvent(new CustomEvent('neko:compact-tool-fan-open-state-change', {
      detail: {
        open,
        timestamp: Date.now(),
      },
    }));
  }, []);

  const scheduleCompactInputToolWheelDragGuardRelease = useCallback(() => {
    clearCompactInputToolWheelDragGuardTimer();
    compactInputToolWheelDragGuardTimerRef.current = window.setTimeout(() => {
      compactInputToolWheelDragGuardTimerRef.current = null;
      compactInputToolWheelDragActiveRef.current = false;
      compactInputToolWheelPointerRef.current = null;
      setCompactInputToolWheelDragOffsetRatio(0);
      dispatchCompactToolWheelDragState(false);
    }, COMPACT_INPUT_TOOL_WHEEL_DRAG_GUARD_MS);
  }, [clearCompactInputToolWheelDragGuardTimer, dispatchCompactToolWheelDragState]);

  const setCompactInputToolFanInteractiveState = useCallback((interactive: boolean) => {
    compactInputToolFanInteractiveRef.current = interactive;
    setCompactInputToolFanInteractive(interactive);
  }, []);

  const resetCompactInputToolFanHoverBlock = useCallback(() => {
    compactInputToolFanHoverInsideRef.current = false;
    compactInputToolFanSuppressHoverUntilLeaveRef.current = false;
  }, []);

  const setCompactInputToolWheelHoveredIndexState = useCallback((toolIndex: number | null) => {
    if (compactInputToolWheelHoveredIndexRef.current === toolIndex) return;
    compactInputToolWheelHoveredIndexRef.current = toolIndex;
    setCompactInputToolWheelHoveredIndex(current => (
      current === toolIndex ? current : toolIndex
    ));
  }, []);

  const clearCompactInputToolWheelPointerHover = useCallback(() => {
    compactInputToolWheelHoverPointerRef.current = null;
    setCompactInputToolWheelHoveredIndexState(null);
  }, [setCompactInputToolWheelHoveredIndexState]);

  const recordCompactInputToolWheelPointerPosition = useCallback((clientX: number, clientY: number) => {
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return;
    compactInputToolWheelHoverPointerRef.current = { clientX, clientY };
  }, []);

  const resolveCompactInputToolWheelLayout = useCallback((): CompactInputToolWheelLayout => {
    const desktopLayout = (window as typeof window & {
      __nekoDesktopCompactLayout?: {
        windowBounds?: { x?: number; y?: number; width?: number; height?: number } | null;
        workArea?: { x?: number; y?: number; width?: number; height?: number } | null;
      } | null;
    }).__nekoDesktopCompactLayout;
    const windowX = Number(desktopLayout?.windowBounds?.x);
    const windowY = Number(desktopLayout?.windowBounds?.y);
    const workAreaX = Number(desktopLayout?.workArea?.x);
    const workAreaY = Number(desktopLayout?.workArea?.y);
    const workAreaWidth = Number(desktopLayout?.workArea?.width);
    const workAreaHeight = Number(desktopLayout?.workArea?.height);
    const hasDesktopWorkArea = isDesktopCompactSurfaceLayoutActive()
      && Number.isFinite(windowX)
      && Number.isFinite(windowY)
      && Number.isFinite(workAreaX)
      && Number.isFinite(workAreaY)
      && Number.isFinite(workAreaWidth)
      && workAreaWidth > 0
      && Number.isFinite(workAreaHeight)
      && workAreaHeight > 0;
    const isMobileViewport = window.matchMedia?.('(max-width: 820px)').matches === true;
    if (!isMobileViewport && !hasDesktopWorkArea) return 'default';
    const fanElement = compactInputToolFanRef.current;
    const fanRect = fanElement?.getBoundingClientRect();
    if (!fanElement || !fanRect || fanRect.width <= 0 || fanRect.height <= 0) return 'default';

    const visualViewport = window.visualViewport;
    const viewportLeft = hasDesktopWorkArea ? workAreaX - windowX : (visualViewport?.offsetLeft ?? 0);
    const viewportTop = hasDesktopWorkArea ? workAreaY - windowY : (visualViewport?.offsetTop ?? 0);
    const viewportWidth = hasDesktopWorkArea ? workAreaWidth : (visualViewport?.width ?? window.innerWidth);
    const viewportHeight = hasDesktopWorkArea ? workAreaHeight : (visualViewport?.height ?? window.innerHeight);
    if (!Number.isFinite(viewportWidth) || viewportWidth <= 0 || !Number.isFinite(viewportHeight) || viewportHeight <= 0) {
      return 'default';
    }

    const fanStyle = window.getComputedStyle ? window.getComputedStyle(fanElement) : null;
    const readFanPixelVar = (name: string, fallback: number) => {
      const rawValue = fanStyle?.getPropertyValue(name).trim() || '';
      const parsedValue = Number.parseFloat(rawValue);
      return Number.isFinite(parsedValue) ? parsedValue : fallback;
    };

    const centerX = fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', COMPACT_INPUT_TOOL_WHEEL_CENTER_X);
    const centerY = fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', COMPACT_INPUT_TOOL_WHEEL_CENTER_Y);
    const orbitRadius = readFanPixelVar('--compact-tool-wheel-orbit-radius', 80);
    const buttonSize = readFanPixelVar('--compact-tool-button-size', 38);
    const viewportMargin = hasDesktopWorkArea ? 0 : COMPACT_INPUT_TOOL_WHEEL_VIEWPORT_MARGIN;
    const minX = viewportLeft + viewportMargin;
    const minY = viewportTop + viewportMargin;
    const maxX = viewportLeft + viewportWidth - viewportMargin;
    const maxY = viewportTop + viewportHeight - viewportMargin;

    let prefersViewportFitFromBottomGap = false;
    if (hasDesktopWorkArea) {
      const workAreaBottom = workAreaY + workAreaHeight;
      const wheelCenterScreenY = windowY + centerY;
      const bottomGap = workAreaBottom - wheelCenterScreenY;
      const bottomFlipThreshold = Math.max(
        COMPACT_INPUT_TOOL_WHEEL_HOVER_RADIUS,
        orbitRadius + (buttonSize / 2),
      );
      prefersViewportFitFromBottomGap = bottomGap < bottomFlipThreshold;
    }

    const wheelLayoutFitsViewport = (
      slots: ReadonlyArray<{ angleDeg: number; scale: number }>,
      options?: { axis?: 'both' | 'horizontal' },
    ) => slots.every(({ angleDeg, scale }) => {
      const angle = angleDeg * (Math.PI / 180);
      const itemCenterX = centerX + (Math.cos(angle) * orbitRadius);
      const itemCenterY = centerY + (Math.sin(angle) * orbitRadius);
      const halfSize = (buttonSize * scale) / 2;
      const fitsHorizontally = itemCenterX - halfSize >= minX
        && itemCenterX + halfSize <= maxX;
      if (options?.axis === 'horizontal') return fitsHorizontally;
      return fitsHorizontally
        && itemCenterY - halfSize >= minY
        && itemCenterY + halfSize <= maxY;
    });

    if (
      prefersViewportFitFromBottomGap
      && wheelLayoutFitsViewport(compactInputToolWheelViewportFitVisibleSlots, { axis: 'horizontal' })
    ) {
      return 'viewport-fit';
    }
    if (wheelLayoutFitsViewport(compactInputToolWheelDefaultVisibleSlots)) return 'default';
    if (wheelLayoutFitsViewport(compactInputToolWheelViewportFitVisibleSlots)) return 'viewport-fit';
    return 'default';
  }, []);

  const syncCompactInputToolWheelLayout = useCallback(() => {
    const nextLayout = resolveCompactInputToolWheelLayout();
    setCompactInputToolWheelLayout(currentLayout => (
      currentLayout === nextLayout ? currentLayout : nextLayout
    ));
  }, [resolveCompactInputToolWheelLayout]);

  const closeCompactInputToolFan = useCallback((options?: {
    afterClose?: () => void;
    deferDesktopAction?: boolean;
  }) => {
    clearCompactInputToolFanCloseTimer();
    clearCompactInputToolFanInteractiveTimer();
    clearCompactInputToolWheelDragGuardTimer();
    clearCompactInputToolWheelFastAnimationTimer();
    clearCompactInputToolWheelChargeReleaseTimer();
    compactInputToolFanOpenIntentRef.current = null;
    compactInputToolWheelDragActiveRef.current = false;
    compactInputToolWheelPointerRef.current = null;
    setCompactInputToolWheelDragOffsetRatio(0);
    dispatchCompactToolWheelDragState(false);
    compactInputToolWheelLastRotationAtRef.current = 0;
    resetCompactInputToolWheelCharge();
    setCompactInputToolWheelFastAnimation(false);
    setCompactInputToolWheelLayout('default');
    clearCompactInputToolWheelPointerHover();
    setCompactInputToolFanInteractiveState(false);
    compactInputToolFanPositionSyncRef.current?.();
    compactInputToolFanOpenRef.current = false;
    setCompactInputToolFanOpen(false);
    setToolMenuOpen(false);
    dispatchCompactToolFanOpenState(false);
    if (!options?.afterClose) return;
    const desktopWindow = window as Window & {
      __nekoDesktopCompactLayout?: {
        windowBounds?: unknown;
      } | null;
    };
    if (options.deferDesktopAction && desktopWindow.__nekoDesktopCompactLayout?.windowBounds) {
      window.setTimeout(options.afterClose, 220);
      return;
    }
    options.afterClose();
  }, [
    clearCompactInputToolFanCloseTimer,
    clearCompactInputToolFanInteractiveTimer,
    clearCompactInputToolWheelDragGuardTimer,
    clearCompactInputToolWheelFastAnimationTimer,
    clearCompactInputToolWheelChargeReleaseTimer,
    clearCompactInputToolWheelPointerHover,
    dispatchCompactToolWheelDragState,
    dispatchCompactToolFanOpenState,
    resetCompactInputToolWheelCharge,
    setCompactInputToolFanInteractiveState,
  ]);

  const updateCompactInputToolFanPosition = useCallback(() => {}, []);

  const scheduleCompactInputToolFanTransientClose = useCallback((options?: {
    delayMs?: number;
    force?: boolean;
    keepExistingTimer?: boolean;
  }) => {
    if (!compactInputToolFanOpenRef.current) return;
    if (!options?.force && compactInputToolFanOpenIntentRef.current !== 'hover') return;
    if (
      compactInputToolWheelDragActiveRef.current
      || compactInputToolWheelPointerRef.current
      || compactInputToolWheelChargeReleaseActiveRef.current
    ) return;
    if (options?.keepExistingTimer && compactInputToolFanCloseTimerRef.current !== null) return;
    clearCompactInputToolFanCloseTimer();
    compactInputToolFanCloseTimerRef.current = window.setTimeout(() => {
      compactInputToolFanCloseTimerRef.current = null;
      if (
        compactInputToolWheelDragActiveRef.current
        || compactInputToolWheelPointerRef.current
        || compactInputToolWheelChargeReleaseActiveRef.current
      ) return;
      closeCompactInputToolFan();
    }, options?.delayMs ?? COMPACT_INPUT_TOOL_FAN_TRANSIENT_CLOSE_DELAY_MS);
  }, [clearCompactInputToolFanCloseTimer, closeCompactInputToolFan]);

  useEffect(() => {
    if (!isCompactSurface) return;
    const frameId = window.requestAnimationFrame(() => {
      window.dispatchEvent(new CustomEvent('neko:compact-interaction-geometry-change'));
    });
    return () => window.cancelAnimationFrame(frameId);
  }, [
    activeAvatarToolId,
    compactInputToolFanInteractive,
    compactInputToolFanOpen,
    compactInputToolWheelIndex,
    effectiveCompactChatState,
    isCompactSurface,
    toolMenuOpen,
  ]);

  useLayoutEffect(() => {
    if (!compactInputToolFanOpen) {
      setCompactInputToolWheelLayout('default');
      return undefined;
    }

    syncCompactInputToolWheelLayout();
    const frameId = window.requestAnimationFrame(syncCompactInputToolWheelLayout);
    let desktopLayoutFrameId = 0;
    const syncAfterDesktopCompactLayoutChange = () => {
      syncCompactInputToolWheelLayout();
      if (desktopLayoutFrameId) window.cancelAnimationFrame(desktopLayoutFrameId);
      desktopLayoutFrameId = window.requestAnimationFrame(() => {
        desktopLayoutFrameId = 0;
        syncCompactInputToolWheelLayout();
      });
    };
    window.addEventListener('resize', syncCompactInputToolWheelLayout);
    window.addEventListener('neko:compact-interaction-geometry-change', syncCompactInputToolWheelLayout);
    window.addEventListener('neko:desktop-compact-layout-change', syncAfterDesktopCompactLayoutChange);
    window.visualViewport?.addEventListener('resize', syncCompactInputToolWheelLayout);
    window.visualViewport?.addEventListener('scroll', syncCompactInputToolWheelLayout);

    return () => {
      window.cancelAnimationFrame(frameId);
      if (desktopLayoutFrameId) window.cancelAnimationFrame(desktopLayoutFrameId);
      window.removeEventListener('resize', syncCompactInputToolWheelLayout);
      window.removeEventListener('neko:compact-interaction-geometry-change', syncCompactInputToolWheelLayout);
      window.removeEventListener('neko:desktop-compact-layout-change', syncAfterDesktopCompactLayoutChange);
      window.visualViewport?.removeEventListener('resize', syncCompactInputToolWheelLayout);
      window.visualViewport?.removeEventListener('scroll', syncCompactInputToolWheelLayout);
    };
  }, [
    compactInputToolFanOpen,
    compactSurfaceResizeWidth,
    effectiveCompactChatState,
    syncCompactInputToolWheelLayout,
  ]);

  const openCompactInputToolFan = useCallback((intent: 'click' | 'hover', options?: { ignoreDisabled?: boolean }) => {
    if ((!options?.ignoreDisabled && composerDisabled) || compactInputHasPayload) return false;
    if (compactInputToolFanOpenRef.current) {
      clearCompactInputToolFanCloseTimer();
      if (compactInputToolFanOpenIntentRef.current !== 'click') {
        compactInputToolFanOpenIntentRef.current = intent;
      }
      updateCompactInputToolFanPosition();
      return true;
    }
    // 展开时延续上次轮盘中心索引（compactInputToolWheelIndex 是组件级 state，会话内常驻）：
    // hover 抖动重入或重新打开都不主动把用户刚滚到的位置弹回默认位。复位只随页面刷新/组件
    // 重挂发生（useState 初值为环位 0）。取舍脉络：#1697 曾在此「每次展开复位 index=0」，
    // #1703 改为 localStorage 持久化记忆，本次去掉持久化、只保留会话内记忆 + 刷新复位。
    // 因此这里不要再加 setCompactInputToolWheelIndex(0)，也不要重新引入 localStorage 持久化。
    clearCompactInputToolFanCloseTimer();
    clearCompactInputToolFanInteractiveTimer();
    compactInputToolFanOpenIntentRef.current = intent;
    setCompactInputToolWheelLayout('default');
    setCompactInputToolFanInteractiveState(false);
    updateCompactInputToolFanPosition();
    compactInputToolFanOpenRef.current = true;
    setCompactInputToolFanOpen(true);
    dispatchCompactToolFanOpenState(true);
    compactInputToolFanInteractiveTimerRef.current = window.setTimeout(() => {
      compactInputToolFanInteractiveTimerRef.current = null;
      if (!compactInputToolFanOpenIntentRef.current) return;
      setCompactInputToolFanInteractiveState(true);
    }, COMPACT_INPUT_TOOL_FAN_INTERACTIVE_DELAY_MS);
    return true;
  }, [
    clearCompactInputToolFanCloseTimer,
    clearCompactInputToolFanInteractiveTimer,
    compactInputHasPayload,
    composerDisabled,
    dispatchCompactToolFanOpenState,
    setCompactInputToolFanInteractiveState,
    updateCompactInputToolFanPosition,
  ]);

  const shouldOpenCompactToolFanOnHover = useCallback((pointerType: string) => {
    return pointerType === 'mouse' || pointerType === '';
  }, []);

  const isCompactInputToolPointerInToggleHoverRegion = useCallback((clientX: number, clientY: number, relatedTarget?: EventTarget | null) => {
    if (relatedTarget instanceof Node && compactInputToolToggleRef.current?.contains(relatedTarget)) return true;
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return false;
    const toggleRect = compactInputToolToggleRef.current?.getBoundingClientRect();
    if (!toggleRect || toggleRect.width <= 0 || toggleRect.height <= 0) return false;
    const centerX = toggleRect.left + (toggleRect.width / 2);
    const centerY = toggleRect.top + (toggleRect.height / 2);
    const radius = (Math.max(toggleRect.width, toggleRect.height) / 2) + COMPACT_INPUT_TOOL_TOGGLE_HOVER_OUTSET;
    return Math.hypot(clientX - centerX, clientY - centerY) <= radius;
  }, []);

  const getCompactInputToolFanCircularHoverRegion = useCallback(() => {
    const fanElement = compactInputToolFanRef.current;
    const fanRect = fanElement?.getBoundingClientRect();
    if (!fanRect || fanRect.width <= 0 || fanRect.height <= 0) return null;
    const fanStyle = fanElement && window.getComputedStyle ? window.getComputedStyle(fanElement) : null;
    const readFanPixelVar = (name: string, fallback: number) => {
      const rawValue = fanStyle?.getPropertyValue(name).trim() || '';
      const parsedValue = Number.parseFloat(rawValue);
      return Number.isFinite(parsedValue) ? parsedValue : fallback;
    };
    const centerX = fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', COMPACT_INPUT_TOOL_WHEEL_CENTER_X);
    const centerY = fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', COMPACT_INPUT_TOOL_WHEEL_CENTER_Y);
    const radius = readFanPixelVar('--compact-tool-wheel-hover-radius', COMPACT_INPUT_TOOL_WHEEL_HOVER_RADIUS);
    if (!Number.isFinite(radius) || radius <= 0) return null;
    return { centerX, centerY, radius };
  }, []);

  const isCompactInputToolPointerInHoverRegion = useCallback((clientX: number, clientY: number, relatedTarget?: EventTarget | null) => {
    if (relatedTarget instanceof Node) {
      if (compactInputToolToggleRef.current?.contains(relatedTarget)) return true;
      if (compactInputToolFanRef.current?.contains(relatedTarget)) return true;
    }
    if (isCompactInputToolPointerInToggleHoverRegion(clientX, clientY, relatedTarget)) return true;
    const fanRegion = getCompactInputToolFanCircularHoverRegion();
    if (!fanRegion) return false;
    return Math.hypot(clientX - fanRegion.centerX, clientY - fanRegion.centerY) <= fanRegion.radius;
  }, [
    getCompactInputToolFanCircularHoverRegion,
    isCompactInputToolPointerInToggleHoverRegion,
  ]);

  const handleCompactInputToolHoverEnter = useCallback((event: ReactPointerEvent) => {
    if (!shouldOpenCompactToolFanOnHover(event.pointerType)) return;
    if (compactInputToolFanSuppressHoverUntilLeaveRef.current) return;
    if (compactInputToolFanHoverInsideRef.current && compactInputToolFanOpenRef.current) return;
    compactInputToolFanHoverInsideRef.current = true;
    openCompactInputToolFan('hover');
  }, [openCompactInputToolFan, shouldOpenCompactToolFanOnHover]);

  const handleCompactInputToolHoverLeave = useCallback((event: ReactPointerEvent) => {
    if (compactInputToolWheelDragActiveRef.current || compactInputToolWheelPointerRef.current) return;
    if (isCompactInputToolPointerInHoverRegion(event.clientX, event.clientY, event.relatedTarget)) return;
    resetCompactInputToolFanHoverBlock();
    scheduleCompactInputToolFanTransientClose();
  }, [isCompactInputToolPointerInHoverRegion, resetCompactInputToolFanHoverBlock, scheduleCompactInputToolFanTransientClose]);

  const closeCompactInputToolFanFromUserClick = useCallback(() => {
    compactInputToolFanSuppressHoverUntilLeaveRef.current = true;
    closeCompactInputToolFan();
    window.requestAnimationFrame(() => {
      compactInputToolToggleRef.current?.focus({ preventScroll: true });
    });
  }, [closeCompactInputToolFan]);

  const clearActiveAvatarToolAndCloseCompactFan = useCallback(() => {
    clearActiveAvatarToolSelection({ insideHostWindow: true });
    closeCompactInputToolFanFromUserClick();
  }, [clearActiveAvatarToolSelection, closeCompactInputToolFanFromUserClick]);

  const closeCompactInputToolFanFromDesktopOutside = useCallback(() => {
    resetCompactInputToolFanHoverBlock();
    scheduleCompactInputToolFanTransientClose({
      delayMs: COMPACT_INPUT_TOOL_FAN_OUTSIDE_CLOSE_DELAY_MS,
      force: true,
      keepExistingTimer: true,
    });
  }, [
    resetCompactInputToolFanHoverBlock,
    scheduleCompactInputToolFanTransientClose,
  ]);

  const toggleCompactInputToolFanByClick = useCallback(() => {
    if (compactInputToolFanOpenRef.current) {
      closeCompactInputToolFanFromUserClick();
      return;
    }
    compactInputToolFanSuppressHoverUntilLeaveRef.current = false;
    openCompactInputToolFan('click');
  }, [closeCompactInputToolFanFromUserClick, openCompactInputToolFan]);

  const rotateCompactInputToolWheel = useCallback((direction: 1 | -1) => {
    setCompactInputToolWheelIndex(current => (
      (current + direction + COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT) % COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT
    ));
  }, []);

  const markCompactInputToolWheelMotion = useCallback((stepCount: number, options?: { forceFast?: boolean }) => {
    const now = getCompactToolWheelTimestamp();
    const elapsed = compactInputToolWheelLastRotationAtRef.current > 0
      ? now - compactInputToolWheelLastRotationAtRef.current
      : Number.POSITIVE_INFINITY;
    compactInputToolWheelLastRotationAtRef.current = now;
    if (!options?.forceFast && stepCount <= 1 && elapsed > COMPACT_INPUT_TOOL_WHEEL_FAST_GESTURE_MS) return;

    clearCompactInputToolWheelFastAnimationTimer();
    setCompactInputToolWheelFastAnimation(true);
    compactInputToolWheelFastAnimationTimerRef.current = window.setTimeout(() => {
      compactInputToolWheelFastAnimationTimerRef.current = null;
      setCompactInputToolWheelFastAnimation(false);
    }, COMPACT_INPUT_TOOL_WHEEL_FAST_ANIMATION_MS);
  }, [clearCompactInputToolWheelFastAnimationTimer]);

  const rotateCompactInputToolWheelSteps = useCallback((direction: 1 | -1, stepCount: number, options?: { forceFast?: boolean }) => {
    if (stepCount <= 0) return;
    markCompactInputToolWheelMotion(stepCount, options);
    for (let step = 0; step < stepCount; step += 1) {
      rotateCompactInputToolWheel(direction);
      playCompactToolWheelDetentSound();
    }
  }, [markCompactInputToolWheelMotion, rotateCompactInputToolWheel]);

  const recordCompactInputToolWheelCharge = useCallback((direction: 1 | -1, stepCount: number): CompactToolWheelChargeState => {
    const previous = compactInputToolWheelChargeRef.current;
    if (stepCount <= 0) return previous;
    let nextDirection: 1 | -1 | null = direction;
    let sameDirectionSteps = previous.direction === direction ? previous.sameDirectionSteps + stepCount : stepCount;
    let chargeSteps = 0;

    if (previous.chargeSteps > 0) {
      if (previous.direction === direction) {
        chargeSteps = Math.min(COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS, previous.chargeSteps + stepCount);
        sameDirectionSteps = COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS + chargeSteps;
      } else {
        const remainingChargeSteps = previous.chargeSteps - stepCount;
        if (remainingChargeSteps > 0) {
          nextDirection = previous.direction;
          chargeSteps = remainingChargeSteps;
          sameDirectionSteps = COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS + remainingChargeSteps;
        } else {
          const leftoverSteps = Math.abs(remainingChargeSteps);
          if (leftoverSteps <= 0) {
            nextDirection = null;
            sameDirectionSteps = 0;
            chargeSteps = 0;
          } else {
            nextDirection = direction;
            sameDirectionSteps = leftoverSteps;
            chargeSteps = Math.min(
              COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS,
              Math.max(0, leftoverSteps - COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS),
            );
          }
        }
      }
    } else {
      chargeSteps = Math.min(
        COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS,
        Math.max(0, sameDirectionSteps - COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS),
      );
    }

    compactInputToolWheelChargeRef.current = {
      direction: nextDirection,
      sameDirectionSteps,
      chargeSteps,
    };
    setCompactInputToolWheelChargeRatio(chargeSteps / COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS);
    setCompactInputToolWheelChargeDirection(chargeSteps > 0 ? nextDirection : null);
    return compactInputToolWheelChargeRef.current;
  }, []);

  const startCompactInputToolWheelChargeRelease = useCallback((
    direction: 1 | -1,
    chargeSteps: number,
    reboundVisualIntensity: number | null,
  ) => {
    const releaseSteps = getCompactToolWheelChargeReleaseVisualStepCount(
      Math.round(chargeSteps),
      COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT,
    );
    const chargeProgressRatio = getCompactToolWheelChargeProgressRatio(chargeSteps);
    clearCompactInputToolWheelChargeReleaseTimer();
    if (releaseSteps <= 0) {
      return;
    }

    let completedSteps = 0;
    compactInputToolWheelChargeReleaseActiveRef.current = true;
    setCompactInputToolWheelChargeReleaseActive(true);
    setCompactInputToolWheelChargeReleaseVisualStepOffset(0);

    const finishRelease = (playRebound: boolean) => {
      compactInputToolWheelChargeReleaseTimerRef.current = null;
      compactInputToolWheelChargeReleaseActiveRef.current = false;
      setCompactInputToolWheelChargeReleaseActive(false);
      setCompactInputToolWheelChargeReleaseVisualStepOffset(0);
      if (playRebound) {
        setCompactInputToolWheelDragOffsetRatio(
          direction * COMPACT_TOOL_WHEEL_CHARGE_RELEASE_REBOUND_OVERSHOOT_RATIO,
        );
        compactInputToolWheelChargeReleaseTimerRef.current = window.setTimeout(() => {
          compactInputToolWheelChargeReleaseTimerRef.current = null;
          setCompactInputToolWheelDragOffsetRatio(0);
        }, COMPACT_TOOL_WHEEL_CHARGE_RELEASE_REBOUND_VISUAL_MS);
        playCompactToolWheelReboundSound(
          COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC,
          reboundVisualIntensity ?? COMPACT_TOOL_WHEEL_REBOUND_VISUAL_SOFT_INTENSITY,
        );
      }
    };

    const runReleaseStep = () => {
      if (!compactInputToolFanOpenRef.current || completedSteps >= releaseSteps) {
        finishRelease(false);
        return;
      }

      completedSteps += 1;
      markCompactInputToolWheelMotion(1, { forceFast: true });
      setCompactInputToolWheelChargeReleaseVisualStepOffset(
        normalizeCompactToolWheelStepOffset(
          direction * completedSteps,
          COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT,
        ),
      );
      playCompactToolWheelDetentSound();
      if (completedSteps >= releaseSteps) {
        finishRelease(true);
        return;
      }
      compactInputToolWheelChargeReleaseTimerRef.current = window.setTimeout(
        runReleaseStep,
        getCompactToolWheelChargeReleaseStepDelayMs(
          completedSteps / releaseSteps,
          chargeProgressRatio,
          releaseSteps - completedSteps,
          releaseSteps,
        ),
      );
    };

    compactInputToolWheelChargeReleaseTimerRef.current = window.setTimeout(runReleaseStep, 0);
  }, [clearCompactInputToolWheelChargeReleaseTimer, markCompactInputToolWheelMotion]);

  const getCompactInputToolWheelNormalizedDelta = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    const rawDelta = Math.abs(event.deltaY) >= Math.abs(event.deltaX)
      ? event.deltaY
      : event.deltaX;
    if (!Number.isFinite(rawDelta) || rawDelta === 0) return 0;
    if (event.deltaMode === 1) {
      return rawDelta * 16;
    }
    if (event.deltaMode === 2) {
      return rawDelta * Math.max(window.innerHeight || 1, 1);
    }
    return rawDelta;
  }, []);

  const routeCompactToolWheelScrollToHistory = useCallback((
    event: ReactWheelEvent<HTMLDivElement>,
    normalizedDelta: number,
  ) => {
    if (!compactExportHistoryOpen) return false;
    if (compactInputToolWheelDragActiveRef.current || compactInputToolWheelPointerRef.current) return false;
    const scrollNode = getCompactHistoryScrollUnderCompactToolWheel(event);
    if (!scrollNode) return false;

    const maxScrollTop = Math.max(0, scrollNode.scrollHeight - scrollNode.clientHeight);
    if (maxScrollTop <= 0) return false;

    const nextScrollTop = clamp(scrollNode.scrollTop + normalizedDelta, 0, maxScrollTop);
    if (nextScrollTop === scrollNode.scrollTop) return false;

    event.preventDefault();
    event.stopPropagation();

    scrollNode.scrollTop = nextScrollTop;
    scrollNode.dispatchEvent(new CustomEvent(COMPACT_HISTORY_ROUTED_WHEEL_EVENT, { bubbles: true }));
    return true;
  }, [compactExportHistoryOpen]);

  const rotateCompactInputToolWheelByScroll = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    recordCompactInputToolWheelPointerPosition(event.clientX, event.clientY);
    const normalizedDelta = getCompactInputToolWheelNormalizedDelta(event);
    if (Math.abs(normalizedDelta) < COMPACT_INPUT_TOOL_WHEEL_SCROLL_DEADZONE) return;

    if (routeCompactToolWheelScrollToHistory(event, normalizedDelta)) return;

    event.preventDefault();
    event.stopPropagation();

    setCompactInputToolWheelHoveredIndexState(null);
    const visualDirectionMultiplier = getCompactToolWheelVisualDirectionMultiplier(compactInputToolWheelLayout);
    const direction: 1 | -1 = normalizedDelta * visualDirectionMultiplier > 0 ? 1 : -1;
    rotateCompactInputToolWheelSteps(direction, 1, { forceFast: true });
  }, [
    compactInputToolWheelLayout,
    getCompactInputToolWheelNormalizedDelta,
    recordCompactInputToolWheelPointerPosition,
    rotateCompactInputToolWheelSteps,
    routeCompactToolWheelScrollToHistory,
    setCompactInputToolWheelHoveredIndexState,
  ]);

  const getCompactToolWheelBoundedDragPoint = useCallback((clientX: number, clientY: number): CompactToolWheelDragPoint => {
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) {
      return { x: clientX, y: clientY, angle: null };
    }
    const fanElement = compactInputToolFanRef.current;
    const fanRect = fanElement?.getBoundingClientRect();
    if (!fanRect || fanRect.width <= 0 || fanRect.height <= 0) {
      return { x: clientX, y: clientY, angle: null };
    }
    const fanStyle = fanElement && window.getComputedStyle ? window.getComputedStyle(fanElement) : null;
    const readFanPixelVar = (name: string, fallback: number) => {
      const rawValue = fanStyle?.getPropertyValue(name).trim() || '';
      const parsedValue = Number.parseFloat(rawValue);
      return Number.isFinite(parsedValue) ? parsedValue : fallback;
    };
    const centerX = fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', COMPACT_INPUT_TOOL_WHEEL_CENTER_X);
    const centerY = fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', COMPACT_INPUT_TOOL_WHEEL_CENTER_Y);
    const radius = Math.max(
      centerX - fanRect.left,
      fanRect.right - centerX,
      centerY - fanRect.top,
      fanRect.bottom - centerY,
    );
    if (!Number.isFinite(radius) || radius <= 0) {
      return { x: clientX, y: clientY, angle: null };
    }
    const deltaX = clientX - centerX;
    const deltaY = clientY - centerY;
    const distance = Math.hypot(deltaX, deltaY);
    if (distance <= 0) {
      return { x: clientX, y: clientY, angle: null };
    }
    const angle = distance >= COMPACT_INPUT_TOOL_WHEEL_ANGLE_MIN_RADIUS
      ? Math.atan2(deltaY, deltaX)
      : null;
    if (distance <= radius) {
      return { x: clientX, y: clientY, angle };
    }
    const scale = radius / distance;
    return {
      x: centerX + deltaX * scale,
      y: centerY + deltaY * scale,
      angle,
    };
  }, []);

  const isCompactToolFanOriginPoint = useCallback((clientX: number, clientY: number) => {
    if (!Number.isFinite(clientX) || !Number.isFinite(clientY)) return false;
    const toggleRect = compactInputToolToggleRef.current?.getBoundingClientRect();
    if (toggleRect && toggleRect.width > 0 && toggleRect.height > 0) {
      return clientX >= toggleRect.left
        && clientX <= toggleRect.right
        && clientY >= toggleRect.top
        && clientY <= toggleRect.bottom;
    }
    const fanRect = compactInputToolFanRef.current?.getBoundingClientRect();
    if (!fanRect) return false;
    if (fanRect.width <= 0 || fanRect.height <= 0) return false;
    const localX = clientX - fanRect.left;
    const localY = clientY - fanRect.top;
    return localX >= 0
      && localY >= 0
      && localX <= COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE
      && localY <= COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE;
  }, []);

  const shouldSuppressCompactToolClick = useCallback((event?: ReactMouseEvent) => {
    if (compactInputToolWheelSuppressClickRef.current) {
      compactInputToolWheelSuppressClickRef.current = false;
      return true;
    }
    if (event && isCompactToolFanOriginPoint(event.clientX, event.clientY)) {
      closeCompactInputToolFanFromUserClick();
      return true;
    }
    if (compactInputToolFanOpen && !compactInputToolFanInteractiveRef.current) {
      return true;
    }
    return false;
  }, [closeCompactInputToolFanFromUserClick, compactInputToolFanOpen, isCompactToolFanOriginPoint]);

  const markCompactToolFanOriginClickSuppressed = useCallback(() => {
    compactInputToolWheelSuppressClickRef.current = true;
    window.setTimeout(() => {
      compactInputToolWheelSuppressClickRef.current = false;
    }, 120);
    closeCompactInputToolFanFromUserClick();
  }, [closeCompactInputToolFanFromUserClick]);

  // ── compact surface 控件「按住拖动对话框」手势 ────────────────────────────
  // 在 toggle / fan 中心 / 毛球 / 胶囊 / textarea 按下后移动超阈值 → 把 surface
  // 拖拽交给宿主（web: app-react-chat-window / Electron: preload-chat-react.js）经
  // neko:compact-surface-drag-grab 接管。
  // 点按（无移动）语义保持原样：toggle 展开/关闭，fan 原点收起，毛球折叠，胶囊进入 input，
  // textarea 正常聚焦输入。
  // 用独立的 compactToolOriginSuppressClickRef 抑制拖动后补发的 click——不能复用
  // compactInputToolWheelSuppressClickRef，因为关闭轮盘的 effect 会把它清掉（见下方 fan 关闭 effect）。
  const clearCompactToolOriginDocumentListeners = useCallback(() => {
    const cleanup = compactToolOriginDocumentCleanupRef.current;
    compactToolOriginDocumentCleanupRef.current = null;
    cleanup?.();
  }, []);

  const dispatchCompactToolOriginDragMove = useCallback((event: Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY' | 'screenX' | 'screenY'>) => {
    const state = compactToolOriginDragRef.current;
    if (!state || state.pointerId !== event.pointerId || !state.moved || state.hostDragEnded) return false;
    if (state.lastForwardedClientX === event.clientX && state.lastForwardedClientY === event.clientY) {
      return false;
    }
    state.lastForwardedClientX = event.clientX;
    state.lastForwardedClientY = event.clientY;
    state.lastForwardedScreenX = event.screenX;
    state.lastForwardedScreenY = event.screenY;
    window.dispatchEvent(new CustomEvent('neko:compact-surface-drag-move', {
      detail: {
        pointerId: state.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        screenX: event.screenX,
        screenY: event.screenY,
      },
    }));
    return true;
  }, []);

  const dispatchCompactToolOriginDragEnd = useCallback((
    event: Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY' | 'screenX' | 'screenY'>,
    reason?: string,
  ) => {
    const state = compactToolOriginDragRef.current;
    if (!state || state.pointerId !== event.pointerId || !state.moved || state.hostDragEnded) return false;
    state.hostDragEnded = true;
    window.dispatchEvent(new CustomEvent('neko:compact-surface-drag-end', {
      detail: {
        pointerId: state.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        screenX: event.screenX,
        screenY: event.screenY,
        reason: reason || 'pointerend',
      },
    }));
    return true;
  }, []);

  const updateCompactToolOriginDragFromPointer = useCallback((event: Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY' | 'screenX' | 'screenY'>) => {
    const state = compactToolOriginDragRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    if (state.moved) {
      dispatchCompactToolOriginDragMove(event);
      return;
    }
    const dx = event.clientX - state.startClientX;
    const dy = event.clientY - state.startClientY;
    if (Math.hypot(dx, dy) < COMPACT_INPUT_TOOL_ORIGIN_DRAG_THRESHOLD) return;
    state.moved = true;
    state.lastForwardedClientX = event.clientX;
    state.lastForwardedClientY = event.clientY;
    state.lastForwardedScreenX = event.screenX;
    state.lastForwardedScreenY = event.screenY;
    // 吞掉本次指针序列随后补发的 click，避免拖完误触发 toggle 展开 / 工具按钮。
    // 一直 armed 到那次 click 被消费（或下次原点/轮盘按下清零）——不能用定时器，慢速拖拽
    // 往往远超任何固定时长，定时器会在 release click 之前清掉、导致拖完轮盘被误开关。
    compactToolOriginSuppressClickRef.current = true;
    // 拖动是「移动对话框」手势而非工具/输入手势，收起轮盘。
    closeCompactInputToolFan();
    // 把 surface 拖拽交给宿主，锚点用按下点（而非当前点），同时带上当前点。
    // Wayland 下宿主优先用 renderer client delta，避免 screenX/screenY 在合成器下不可靠。
    window.dispatchEvent(new CustomEvent('neko:compact-surface-drag-grab', {
      detail: {
        pointerId: state.pointerId,
        clientX: state.startClientX,
        clientY: state.startClientY,
        screenX: state.startScreenX,
        screenY: state.startScreenY,
        currentClientX: event.clientX,
        currentClientY: event.clientY,
        currentScreenX: event.screenX,
        currentScreenY: event.screenY,
      },
    }));
  }, [closeCompactInputToolFan, dispatchCompactToolOriginDragMove]);

  const dispatchCompactToolOriginDragPrime = useCallback((event: ReactPointerEvent) => {
    window.dispatchEvent(new CustomEvent('neko:compact-surface-drag-prime', {
      detail: {
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        screenX: event.screenX,
        screenY: event.screenY,
      },
    }));
  }, []);

  const dispatchCompactToolOriginDragPrimeEnd = useCallback((pointerId: number) => {
    window.dispatchEvent(new CustomEvent('neko:compact-surface-drag-prime-end', {
      detail: { pointerId },
    }));
  }, []);

  const finishCompactToolOriginDragFromPointer = useCallback((
    event: Pick<PointerEvent, 'pointerId' | 'clientX' | 'clientY' | 'screenX' | 'screenY'> & { type?: string },
  ) => {
    const state = compactToolOriginDragRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    dispatchCompactToolOriginDragEnd(event, event.type);
    if (!state.primeEnded) {
      state.primeEnded = true;
      dispatchCompactToolOriginDragPrimeEnd(state.pointerId);
    }
    const captureTarget = state.captureTarget;
    compactToolOriginDragRef.current = null;
    clearCompactToolOriginDocumentListeners();
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(event.pointerId)) {
          captureTarget.releasePointerCapture(event.pointerId);
        }
      } catch (_) {}
    }
    // 无移动 = 点按：toggle 交给自身 onClick；fan 原点已由 onPointerDownCapture 收起。这里不再处理。
  }, [
    clearCompactToolOriginDocumentListeners,
    dispatchCompactToolOriginDragEnd,
    dispatchCompactToolOriginDragPrimeEnd,
  ]);

  const cancelCompactToolOriginDrag = useCallback((reason: string) => {
    const state = compactToolOriginDragRef.current;
    if (!state) {
      clearCompactToolOriginDocumentListeners();
      return;
    }
    const pointerId = state.pointerId;
    dispatchCompactToolOriginDragEnd({
      pointerId,
      clientX: state.lastForwardedClientX ?? state.startClientX,
      clientY: state.lastForwardedClientY ?? state.startClientY,
      screenX: state.lastForwardedScreenX ?? state.startScreenX,
      screenY: state.lastForwardedScreenY ?? state.startScreenY,
    }, reason);
    if (!state.primeEnded) {
      state.primeEnded = true;
      dispatchCompactToolOriginDragPrimeEnd(pointerId);
    }
    const captureTarget = state.captureTarget;
    compactToolOriginDragRef.current = null;
    clearCompactToolOriginDocumentListeners();
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(pointerId)) {
          captureTarget.releasePointerCapture(pointerId);
        }
      } catch (_) {}
    }
  }, [
    clearCompactToolOriginDocumentListeners,
    dispatchCompactToolOriginDragEnd,
    dispatchCompactToolOriginDragPrimeEnd,
  ]);

  const beginCompactToolOriginDrag = useCallback((event: ReactPointerEvent) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const existing = compactToolOriginDragRef.current;
    if (existing && existing.pointerId === event.pointerId) return;
    if (existing) {
      cancelCompactToolOriginDrag('replaced');
    } else {
      clearCompactToolOriginDocumentListeners();
    }
    // 每次新的原点按下都清掉可能残留的抑制标志（上一次拖拽若没补发 click 会留下 true），
    // 保证本次点按/拖拽自洁——抑制只靠「拖动置位 + click 消费 / 下次按下清零」，不再用定时器。
    compactToolOriginSuppressClickRef.current = false;
    const captureTarget = event.target instanceof Element
      ? event.target
      : (event.currentTarget instanceof Element ? event.currentTarget : null);
    try {
      captureTarget?.setPointerCapture?.(event.pointerId);
    } catch (_) {}
    dispatchCompactToolOriginDragPrime(event);
    compactToolOriginDragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startScreenX: event.screenX,
      startScreenY: event.screenY,
      moved: false,
      captureTarget,
    };
    const pointerId = event.pointerId;
    const handleDocumentPointerMove = (nativeEvent: PointerEvent) => {
      if (nativeEvent.pointerId !== pointerId) return;
      updateCompactToolOriginDragFromPointer(nativeEvent);
    };
    const handleDocumentPointerEnd = (nativeEvent: PointerEvent) => {
      if (nativeEvent.pointerId !== pointerId) return;
      finishCompactToolOriginDragFromPointer(nativeEvent);
    };
    document.addEventListener('pointermove', handleDocumentPointerMove, true);
    document.addEventListener('pointerup', handleDocumentPointerEnd, true);
    document.addEventListener('pointercancel', handleDocumentPointerEnd, true);
    compactToolOriginDocumentCleanupRef.current = () => {
      document.removeEventListener('pointermove', handleDocumentPointerMove, true);
      document.removeEventListener('pointerup', handleDocumentPointerEnd, true);
      document.removeEventListener('pointercancel', handleDocumentPointerEnd, true);
    };
  }, [
    cancelCompactToolOriginDrag,
    clearCompactToolOriginDocumentListeners,
    dispatchCompactToolOriginDragPrime,
    finishCompactToolOriginDragFromPointer,
    updateCompactToolOriginDragFromPointer,
  ]);

  const updateCompactToolOriginDrag = useCallback((event: ReactPointerEvent) => {
    updateCompactToolOriginDragFromPointer(event.nativeEvent);
  }, [updateCompactToolOriginDragFromPointer]);

  const endCompactToolOriginDrag = useCallback((event: ReactPointerEvent) => {
    finishCompactToolOriginDragFromPointer(event.nativeEvent);
  }, [finishCompactToolOriginDragFromPointer]);

  const suppressCompactToolOriginClickAfterDrag = useCallback((event: ReactMouseEvent) => {
    if (!compactToolOriginSuppressClickRef.current) return;
    compactToolOriginSuppressClickRef.current = false;
    event.preventDefault();
    event.stopPropagation();
  }, []);

  useEffect(() => () => {
    cancelCompactToolOriginDrag('unmount');
    clearCompactInputToolFanCloseTimer();
    clearCompactInputToolFanInteractiveTimer();
    clearCompactInputToolWheelDragGuardTimer();
    clearCompactInputToolWheelFastAnimationTimer();
    clearCompactInputToolWheelChargeReleaseTimer();
  }, [
    cancelCompactToolOriginDrag,
    clearCompactInputToolFanCloseTimer,
    clearCompactInputToolFanInteractiveTimer,
    clearCompactInputToolWheelDragGuardTimer,
    clearCompactInputToolWheelFastAnimationTimer,
    clearCompactInputToolWheelChargeReleaseTimer,
  ]);

  useEffect(() => {
    if (!isCompactSurface || composerHidden) {
      resetCompactInputToolFanHoverBlock();
      return;
    }

    const handlePointerMove = (event: PointerEvent) => {
      // 工具轮盘原点拖拽进行中时不跑悬停展开/收起逻辑，避免拖动文本框时悬停又把轮盘弹开。
      if (compactToolOriginDragRef.current) return;
      const pointerInHoverRegion = isCompactInputToolPointerInHoverRegion(event.clientX, event.clientY, event.target);
      if (compactInputToolFanSuppressHoverUntilLeaveRef.current) {
        if (!pointerInHoverRegion) {
          resetCompactInputToolFanHoverBlock();
        }
        return;
      }
      if (pointerInHoverRegion) {
        compactInputToolFanHoverInsideRef.current = true;
        clearCompactInputToolFanCloseTimer();
      }
      if (
        !compactInputHasPayload
        && !composerDisabled
        && shouldOpenCompactToolFanOnHover(event.pointerType)
        && isCompactInputToolPointerInToggleHoverRegion(event.clientX, event.clientY, event.target)
      ) {
        if (!compactInputToolFanOpenRef.current) {
          openCompactInputToolFan('hover');
        }
        return;
      }
      if (
        compactInputToolFanOpenRef.current
        && !pointerInHoverRegion
        && !compactInputToolWheelDragActiveRef.current
        && !compactInputToolWheelPointerRef.current
      ) {
        resetCompactInputToolFanHoverBlock();
        scheduleCompactInputToolFanTransientClose();
      }
    };

    window.addEventListener('pointermove', handlePointerMove, true);
    return () => {
      window.removeEventListener('pointermove', handlePointerMove, true);
    };
  }, [
    clearCompactInputToolFanCloseTimer,
    compactInputHasPayload,
    composerHidden,
    composerDisabled,
    isCompactInputToolPointerInHoverRegion,
    isCompactInputToolPointerInToggleHoverRegion,
    isCompactSurface,
    openCompactInputToolFan,
    resetCompactInputToolFanHoverBlock,
    scheduleCompactInputToolFanTransientClose,
    shouldOpenCompactToolFanOnHover,
  ]);

  const finishCompactToolWheelPointer = useCallback((event?: { pointerId: number }) => {
    const pointerState = compactInputToolWheelPointerRef.current;
    if (!pointerState) return;
    if (event && pointerState.id !== event.pointerId) return;

    const captureTarget = pointerState.captureTarget;
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(pointerState.id)) {
          captureTarget.releasePointerCapture(pointerState.id);
        }
      } catch (_) {}
    }

    if (pointerState.didRotate) {
      compactInputToolWheelSuppressClickRef.current = true;
      window.setTimeout(() => {
        compactInputToolWheelSuppressClickRef.current = false;
      }, 0);
    }
    const reboundVisualIntensity = getCompactToolWheelReboundVisualIntensity(pointerState.dragOffsetRatio);
    const chargeState = compactInputToolWheelChargeRef.current;
    const releaseDirection = chargeState.direction === null
      ? null
      : (chargeState.direction === 1 ? -1 : 1);
    const releaseSteps = chargeState.chargeSteps;
    resetCompactInputToolWheelCharge();
    clearCompactInputToolWheelDragGuardTimer();
    compactInputToolWheelPointerRef.current = null;
    compactInputToolWheelDragActiveRef.current = false;
    setCompactInputToolWheelDragOffsetRatio(0);
    dispatchCompactToolWheelDragState(false, pointerState.id);
    if (releaseDirection !== null && releaseSteps > 0) {
      startCompactInputToolWheelChargeRelease(releaseDirection, releaseSteps, reboundVisualIntensity);
    } else if (reboundVisualIntensity !== null) {
      playCompactToolWheelReboundSound(COMPACT_TOOL_WHEEL_REBOUND_SOUND_SRC, reboundVisualIntensity);
    }
  }, [
    clearCompactInputToolWheelDragGuardTimer,
    dispatchCompactToolWheelDragState,
    resetCompactInputToolWheelCharge,
    startCompactInputToolWheelChargeRelease,
  ]);

  const updateCompactInputToolWheelDrag = useCallback((input: CompactToolWheelDragInput) => {
    const pointerState = compactInputToolWheelPointerRef.current;
    if (!pointerState || pointerState.id !== input.pointerId) return false;
    dispatchCompactToolWheelDragState(true, input.pointerId);
    scheduleCompactInputToolWheelDragGuardRelease();
    if (input.pointerType === 'mouse' && input.buttons === 0) {
      finishCompactToolWheelPointer({ pointerId: input.pointerId });
      return true;
    }

    const dragPoint = getCompactToolWheelBoundedDragPoint(input.clientX, input.clientY);
    if (pointerState.angle !== null && dragPoint.angle !== null) {
      const angleStepRad = (
        compactInputToolWheelLayout === 'viewport-fit'
          ? COMPACT_TOOL_WHEEL_VIEWPORT_DRAG_ANGLE_STEP_DEG
          : COMPACT_TOOL_WHEEL_DEFAULT_DRAG_ANGLE_STEP_DEG
      ) * (Math.PI / 180);
      const visualDirectionMultiplier = getCompactToolWheelVisualDirectionMultiplier(compactInputToolWheelLayout);
      const angleDelta = normalizeCompactToolWheelAngleDelta(dragPoint.angle - pointerState.angle);
      const totalDelta = pointerState.angleRemainder + angleDelta;
      const visualOffsetRatio = totalDelta / angleStepRad;
      const logicalOffsetRatio = visualOffsetRatio * visualDirectionMultiplier;
      const stepCount = getCompactToolWheelDetentStepCount(logicalOffsetRatio);
      pointerState.x = dragPoint.x;
      pointerState.y = dragPoint.y;
      pointerState.angle = dragPoint.angle;
      if (stepCount <= 0) {
        pointerState.angleRemainder = totalDelta;
        const dragOffsetRatio = clamp(
          getCompactToolWheelDetentDisplayRatio(visualOffsetRatio),
          -0.98,
          0.98,
        );
        pointerState.dragOffsetRatio = dragOffsetRatio;
        setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
        return true;
      }

      input.preventDefault?.();
      const direction: 1 | -1 = logicalOffsetRatio > 0 ? 1 : -1;
      rotateCompactInputToolWheelSteps(direction, stepCount);
      const chargeState = recordCompactInputToolWheelCharge(direction, stepCount);
      pointerState.angleRemainder = totalDelta - (
        direction * visualDirectionMultiplier * stepCount * angleStepRad
      );
      const remainingOffsetRatio = pointerState.angleRemainder / angleStepRad;
      const dragOffsetRatio = clamp(
        getCompactToolWheelDetentDisplayRatio(remainingOffsetRatio),
        -0.98,
        0.98,
      );
      pointerState.dragOffsetRatio = dragOffsetRatio;
      setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
      pointerState.didRotate = true;
      if (chargeState.chargeSteps >= COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS) {
        finishCompactToolWheelPointer({ pointerId: input.pointerId });
      }
      return true;
    }

    const deltaX = dragPoint.x - pointerState.x;
    const deltaY = dragPoint.y - pointerState.y;
    const useVerticalDelta = Math.abs(deltaY) >= Math.abs(deltaX);
    const primaryDelta = useVerticalDelta ? deltaY : deltaX;
    const directionalDelta = useVerticalDelta ? primaryDelta : -primaryDelta;
    const visualDirectionMultiplier = getCompactToolWheelVisualDirectionMultiplier(compactInputToolWheelLayout);
    const visualOffsetRatio = directionalDelta / COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
    const logicalOffsetRatio = visualOffsetRatio * visualDirectionMultiplier;
    const stepCount = getCompactToolWheelDetentStepCount(logicalOffsetRatio);
    if (stepCount <= 0) {
      pointerState.angle = dragPoint.angle;
      if (dragPoint.angle !== null) {
        pointerState.angleRemainder = 0;
      }
      const dragOffsetRatio = clamp(
        getCompactToolWheelDetentDisplayRatio(visualOffsetRatio),
        -0.98,
        0.98,
      );
      pointerState.dragOffsetRatio = dragOffsetRatio;
      setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
      return true;
    }

    input.preventDefault?.();
    const direction: 1 | -1 = logicalOffsetRatio > 0 ? 1 : -1;
    rotateCompactInputToolWheelSteps(direction, stepCount);
    const chargeState = recordCompactInputToolWheelCharge(direction, stepCount);
    const consumedDelta = direction * visualDirectionMultiplier * stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
    const remainingDelta = directionalDelta - consumedDelta;
    if (useVerticalDelta) {
      pointerState.x = dragPoint.x;
      pointerState.y += consumedDelta;
    } else {
      pointerState.x -= consumedDelta;
      pointerState.y = dragPoint.y;
    }
    pointerState.angle = dragPoint.angle;
    pointerState.angleRemainder = 0;
    const remainingOffsetRatio = remainingDelta / COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
    const dragOffsetRatio = clamp(
      getCompactToolWheelDetentDisplayRatio(remainingOffsetRatio),
      -0.98,
      0.98,
    );
    pointerState.dragOffsetRatio = dragOffsetRatio;
    setCompactInputToolWheelDragOffsetRatio(dragOffsetRatio);
    pointerState.didRotate = true;
    if (chargeState.chargeSteps >= COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS) {
      finishCompactToolWheelPointer({ pointerId: input.pointerId });
    }
    return true;
  }, [
    dispatchCompactToolWheelDragState,
    finishCompactToolWheelPointer,
    getCompactToolWheelBoundedDragPoint,
    compactInputToolWheelLayout,
    recordCompactInputToolWheelCharge,
    rotateCompactInputToolWheelSteps,
    scheduleCompactInputToolWheelDragGuardRelease,
  ]);

  useEffect(() => {
    const handleGlobalPointerMove = (event: PointerEvent) => {
      updateCompactInputToolWheelDrag({
        pointerId: event.pointerId,
        clientX: event.clientX,
        clientY: event.clientY,
        buttons: event.buttons,
        pointerType: event.pointerType,
        preventDefault: () => event.preventDefault(),
      });
    };

    const handleGlobalPointerEnd = (event: PointerEvent) => {
      const pointerState = compactInputToolWheelPointerRef.current;
      if (!pointerState || pointerState.id !== event.pointerId) return;
      finishCompactToolWheelPointer({ pointerId: event.pointerId });
    };

    const handleGlobalMouseUp = () => {
      if (!compactInputToolWheelDragActiveRef.current) return;
      finishCompactToolWheelPointer();
    };

    const handleWindowBlur = () => {
      if (!compactInputToolWheelDragActiveRef.current && !compactInputToolWheelPointerRef.current) return;
      scheduleCompactInputToolWheelDragGuardRelease();
    };

    window.addEventListener('pointermove', handleGlobalPointerMove, true);
    window.addEventListener('pointerup', handleGlobalPointerEnd, true);
    window.addEventListener('pointercancel', handleGlobalPointerEnd, true);
    window.addEventListener('mouseup', handleGlobalMouseUp, true);
    window.addEventListener('blur', handleWindowBlur);
    return () => {
      window.removeEventListener('pointermove', handleGlobalPointerMove, true);
      window.removeEventListener('pointerup', handleGlobalPointerEnd, true);
      window.removeEventListener('pointercancel', handleGlobalPointerEnd, true);
      window.removeEventListener('mouseup', handleGlobalMouseUp, true);
      window.removeEventListener('blur', handleWindowBlur);
    };
  }, [finishCompactToolWheelPointer, scheduleCompactInputToolWheelDragGuardRelease, updateCompactInputToolWheelDrag]);

  useEffect(() => {
    compactInputToolFanPositionSyncRef.current = () => updateCompactInputToolFanPosition();
    return () => {
      compactInputToolFanPositionSyncRef.current = null;
    };
  }, [updateCompactInputToolFanPosition]);

  useEffect(() => () => {
    clearCompactInputToolFanCloseTimer();
  }, [clearCompactInputToolFanCloseTimer]);

  const collapseCompactInputIfEmpty = useCallback((options?: { ignoreFocusedShell?: boolean; ignoreToolFan?: boolean }) => {
    if (!isCompactSurface) return;
    if (effectiveCompactChatState !== 'input') return;
    if (!options?.ignoreToolFan && compactInputToolFanOpen) return;
    if (draftRef.current.trim().length > 0) return;
    if (composerAttachments.length > 0) return;
    const activeElement = document.activeElement;
    if (
      !options?.ignoreFocusedShell
      && activeElement instanceof Node
      && (
        !!compactInputShellRef.current?.contains(activeElement)
        || (
          activeElement instanceof Element
          && !!activeElement.closest('.compact-export-history-anchor, .compact-history-visibility-handle')
        )
      )
    ) {
      return;
    }
    requestCompactChatState('default');
  }, [
    compactInputToolFanOpen,
    composerAttachments.length,
    effectiveCompactChatState,
    isCompactSurface,
    requestCompactChatState,
  ]);

  const scheduleCompactInputCollapse = useCallback(() => {
    window.setTimeout(() => {
      collapseCompactInputIfEmpty();
    }, 0);
  }, [collapseCompactInputIfEmpty]);

  const scheduleForcedCompactInputCollapse = useCallback(() => {
    window.setTimeout(() => {
      collapseCompactInputIfEmpty({ ignoreFocusedShell: true });
    }, 0);
  }, [collapseCompactInputIfEmpty]);

  useEffect(() => {
    if (!isCompactSurface) return;
    if (effectiveCompactChatState !== 'input') return;

    const isInsideCompactInputIsland = (target: EventTarget | null) => (
      target instanceof Node
      && (
        !!compactInputShellRef.current?.contains(target)
        || !!compactInputToolFanRef.current?.contains(target)
        || !!compactChoiceLayerRef.current?.contains(target)
        || (
          target instanceof Element
          && !!target.closest('.compact-export-history-anchor, .compact-history-visibility-handle')
        )
      )
    );

    const handlePointerDown = (event: PointerEvent) => {
      if (isInsideCompactInputIsland(event.target)) return;
      scheduleForcedCompactInputCollapse();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      scheduleForcedCompactInputCollapse();
    };

    window.addEventListener('blur', scheduleForcedCompactInputCollapse);
    document.addEventListener('pointerdown', handlePointerDown, true);
    document.addEventListener('keydown', handleKeyDown, true);
    return () => {
      window.removeEventListener('blur', scheduleForcedCompactInputCollapse);
      document.removeEventListener('pointerdown', handlePointerDown, true);
      document.removeEventListener('keydown', handleKeyDown, true);
    };
  }, [effectiveCompactChatState, isCompactSurface, scheduleForcedCompactInputCollapse]);

  useEffect(() => {
    if (!compactInputToolFanOpen) return;
    const shouldCloseForDisabled = composerDisabled && !toolMenuOpen;
    if (!isCompactSurface || composerHidden || shouldCloseForDisabled || compactInputHasPayload) {
      closeCompactInputToolFan();
    }
  }, [
    closeCompactInputToolFan,
    compactInputHasPayload,
    compactInputToolFanOpen,
    composerDisabled,
    composerHidden,
    isCompactSurface,
    toolMenuOpen,
  ]);

  useEffect(() => {
    if (compactInputToolFanOpen) return;
    clearCompactInputToolFanCloseTimer();
    clearCompactInputToolWheelDragGuardTimer();
    clearCompactInputToolWheelFastAnimationTimer();
    clearCompactInputToolWheelChargeReleaseTimer();
    compactInputToolFanOpenIntentRef.current = null;
    compactInputToolWheelPointerRef.current = null;
    compactInputToolWheelDragActiveRef.current = false;
    compactInputToolWheelSuppressClickRef.current = false;
    setCompactInputToolWheelDragOffsetRatio(0);
    compactInputToolWheelLastRotationAtRef.current = 0;
    resetCompactInputToolWheelCharge();
    setCompactInputToolWheelFastAnimation(false);
    clearCompactInputToolWheelPointerHover();
    dispatchCompactToolWheelDragState(false);
  }, [
    clearCompactInputToolFanCloseTimer,
    clearCompactInputToolWheelDragGuardTimer,
    clearCompactInputToolWheelFastAnimationTimer,
    clearCompactInputToolWheelChargeReleaseTimer,
    clearCompactInputToolWheelPointerHover,
    compactInputToolFanOpen,
    dispatchCompactToolWheelDragState,
    resetCompactInputToolWheelCharge,
  ]);

  useEffect(() => {
    if (!isCompactSurface) return;

    const handleDesktopCompactPointerOutside = () => {
      if (
        compactInputToolWheelDragActiveRef.current
        || compactInputToolWheelPointerRef.current
        || compactInputToolWheelChargeReleaseActiveRef.current
      ) return;
      closeCompactInputToolFanFromDesktopOutside();
      if (compactInputToolFanOpenRef.current) {
        window.setTimeout(() => {
          collapseCompactInputIfEmpty({ ignoreFocusedShell: true, ignoreToolFan: true });
        }, COMPACT_INPUT_TOOL_FAN_OUTSIDE_CLOSE_DELAY_MS);
        return;
      }
      collapseCompactInputIfEmpty({ ignoreFocusedShell: true, ignoreToolFan: true });
    };

    window.addEventListener('neko:desktop-compact-pointer-outside', handleDesktopCompactPointerOutside);
    return () => {
      window.removeEventListener('neko:desktop-compact-pointer-outside', handleDesktopCompactPointerOutside);
    };
  }, [
    collapseCompactInputIfEmpty,
    closeCompactInputToolFanFromDesktopOutside,
    isCompactSurface,
  ]);

  useEffect(() => {
    if (!compactInputToolFanOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (compactInputToolToggleRef.current && target instanceof Node && compactInputToolToggleRef.current.contains(target)) {
        return;
      }
      if (compactInputToolFanRef.current && target instanceof Node && compactInputToolFanRef.current.contains(target)) {
        return;
      }
      closeCompactInputToolFan();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeCompactInputToolFan();
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [closeCompactInputToolFan, compactInputToolFanOpen]);

  useEffect(() => {
    if (!isCompactSurface) {
      setCompactPreviewTextVisible(compactPreviewText);
      previousCompactPreviewTextRef.current = compactPreviewText;
      return;
    }

    if (compactMessagePreview?.isGuide) {
      setCompactPreviewTextVisible(compactPreviewText);
      previousCompactPreviewTextRef.current = compactPreviewText;
      return;
    }

    if (!compactPreviewText) {
      setCompactPreviewTextVisible('');
      previousCompactPreviewTextRef.current = '';
      return;
    }

    let active = true;
    let timeoutId: number | null = null;
    const previousPreviewText = previousCompactPreviewTextRef.current;
    const previousVisibleText = compactPreviewTextVisibleRef.current;
    const seedText = compactPreviewText.startsWith(previousVisibleText)
      ? previousVisibleText
      : compactPreviewText.startsWith(previousPreviewText)
        ? previousPreviewText
        : '';
    setCompactPreviewTextVisible(seedText);
    previousCompactPreviewTextRef.current = compactPreviewText;

    const run = (index: number) => {
      if (!active) return;
      const nextIndex = Math.min(compactPreviewText.length, index + Math.max(1, Math.ceil(compactPreviewText.length / 28)));
      setCompactPreviewTextVisible(compactPreviewText.slice(0, nextIndex));
      if (nextIndex >= compactPreviewText.length) {
        return;
      }
      timeoutId = window.setTimeout(() => run(nextIndex), 24);
    };

    if (seedText.length < compactPreviewText.length) {
      timeoutId = window.setTimeout(() => run(seedText.length), 18);
    }

    return () => {
      active = false;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [compactMessagePreview?.isGuide, compactPreviewText, isCompactSurface]);

  useEffect(() => {
    if (!isCompactSurface) return;
    if (composerAttachments.length === 0) return;
    if (effectiveCompactChatState === 'input') return;
    requestCompactChatState('input');
  }, [composerAttachments.length, effectiveCompactChatState, isCompactSurface, requestCompactChatState]);

  useEffect(() => {
    if (!toolMenuOpen) return;

    const closeMenuOnOutsideClick = (event: MouseEvent) => {
      const menuNode = toolMenuRef.current;
      if (!menuNode) return;
      if (menuNode.contains(event.target as Node)) return;
      if (compactInputToolFanRef.current?.contains(event.target as Node)) return;
      const target = event.target as Element | null;
      if (target?.closest('.avatar-tool-manager-overlay, .avatar-tool-manager-dialog')) return;
      setToolMenuOpen(false);
    };

    const closeMenuOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setToolMenuOpen(false);
      }
    };

    document.addEventListener('mousedown', closeMenuOnOutsideClick);
    document.addEventListener('keydown', closeMenuOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeMenuOnOutsideClick);
      document.removeEventListener('keydown', closeMenuOnEscape);
    };
  }, [toolMenuOpen]);

  useEffect(() => {
    const request = avatarToolMenuOpenRequest;
    if (!request || !request.id || request.id === lastAvatarToolMenuOpenRequestIdRef.current) return;
    const requestId = request.id;
    lastAvatarToolMenuOpenRequestIdRef.current = requestId;
    if (request.open) {
      const opened = openCompactInputToolFan('click', { ignoreDisabled: true });
      if (!opened) return;
      if (activeAvatarToolIds.length === 0) {
        setActiveAvatarToolIds([...DEFAULT_ACTIVE_AVATAR_TOOL_IDS]);
      }
      clearActiveAvatarToolSelection();
      setToolMenuOpen(opened);
      return;
    }
    setToolMenuOpen(false);
  }, [activeAvatarToolIds.length, avatarToolMenuOpenRequest, clearActiveAvatarToolSelection, openCompactInputToolFan]);

  useEffect(() => {
    const request = compactToolFanOpenRequest;
    if (!request || !request.id || request.id === lastCompactToolFanOpenRequestIdRef.current) return;
    lastCompactToolFanOpenRequestIdRef.current = request.id;
    if (request.open) {
      lastCompactToolFanOpenRequestIdRef.current = request.id;
      const requestReason = typeof request.reason === 'string' ? request.reason : '';
      const requestIntent = requestReason.startsWith('desktop-compact-tool-toggle') ? 'hover' : 'click';
      const opened = openCompactInputToolFan(requestIntent, { ignoreDisabled: true });
      if (!opened) return;
      return;
    }
    closeCompactInputToolFan();
  }, [closeCompactInputToolFan, compactToolFanOpenRequest, openCompactInputToolFan]);

  useEffect(() => {
    const request = compactToolWheelIndexRequest;
    if (!request || !request.id || request.id === lastCompactToolWheelIndexRequestIdRef.current) return;
    lastCompactToolWheelIndexRequestIdRef.current = request.id;
    clearCompactInputToolWheelFastAnimationTimer();
    clearCompactInputToolWheelChargeReleaseTimer();
    resetCompactInputToolWheelCharge();
    setCompactInputToolWheelFastAnimation(false);
    setCompactInputToolWheelIndex(request.index);
  }, [
    clearCompactInputToolWheelChargeReleaseTimer,
    clearCompactInputToolWheelFastAnimationTimer,
    compactToolWheelIndexRequest,
    resetCompactInputToolWheelCharge,
  ]);

  useEffect(() => {
    const request = compactToolWheelRotateRequest;
    if (!request || !request.id || request.id === lastCompactToolWheelRotateRequestIdRef.current) return;
    lastCompactToolWheelRotateRequestIdRef.current = request.id;
    const opened = openCompactInputToolFan('click', { ignoreDisabled: true });
    if (!opened) return;
    rotateCompactInputToolWheelSteps(request.direction, request.stepCount, {
      forceFast: request.forceFast !== false,
    });
  }, [compactToolWheelRotateRequest, openCompactInputToolFan, rotateCompactInputToolWheelSteps]);

  function restoreCompactExportHistoryToBottomForOutgoingMessage() {
    if (compactExportHistoryOpen) {
      setCompactExportAutoScrollToBottom(true);
    }
  }

  function focusCompactInputForContinuousTyping() {
    const inputNode = compactInputRef.current;
    if (!inputNode || inputNode.disabled || inputNode.readOnly) return;
    const activeElement = document.activeElement;
    if (
      activeElement instanceof Node
      && compactInputShellRef.current
      && !compactInputShellRef.current.contains(activeElement)
    ) return;
    inputNode.focus();
    const selectionEnd = inputNode.value.length;
    inputNode.setSelectionRange(selectionEnd, selectionEnd);
  }

  function submitDraft() {
    if (compactTextEntryLocked) return;
    if (submittingRef.current) return;
    const text = draft.trim();
    if (!text && composerAttachments.length === 0) return;
    closeCompactInputToolFan();
    submittingRef.current = true;
    let shouldRefocusCompactInput = false;
    try {
      onComposerSubmit?.({ text });
      setDraft('');
      restoreCompactExportHistoryToBottomForOutgoingMessage();
      shouldRefocusCompactInput = isCompactSurface
        && effectiveCompactChatState === 'input'
        && text.length > 0;
    } finally {
      requestAnimationFrame(() => {
        submittingRef.current = false;
        if (shouldRefocusCompactInput) {
          focusCompactInputForContinuousTyping();
        }
      });
    }
  }

  const compactFanRunAction = (action: (() => void) | undefined) => (event: ReactMouseEvent) => {
    if (shouldSuppressCompactToolClick(event)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    compactInputToolFanOpenIntentRef.current = 'click';
    clearCompactInputToolFanCloseTimer();
    action?.();
  };

  const compactFanToggleOnAction = (action: (() => void) | undefined) => (event: ReactMouseEvent) => {
    if (shouldSuppressCompactToolClick(event)) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    compactInputToolFanOpenIntentRef.current = 'click';
    clearCompactInputToolFanCloseTimer();
    action?.();
  };

  const compactInputToolWheelVisualIndex = (
    compactInputToolWheelIndex
    + compactInputToolWheelChargeReleaseVisualStepOffset
    + COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT
  ) % COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT;

  const getCompactToolWheelSlot = (toolIndex: number): number | null => {
    return getCompactToolWheelSlotForIndex(
      toolIndex,
      compactInputToolWheelVisualIndex,
      COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT,
    );
  };

  const getCompactToolWheelTabIndex = (toolIndex: number): number => {
    const slot = getCompactToolWheelSlot(toolIndex);
    return compactInputToolFanOpen && slot !== null && Math.abs(slot) <= 1 ? 0 : -1;
  };

  const isCompactToolWheelActionable = (toolIndex: number): boolean => {
    const slot = getCompactToolWheelSlot(toolIndex);
    return compactInputToolFanOpen && slot !== null && Math.abs(slot) <= 1;
  };

  const getCompactToolWheelAriaHidden = (toolIndex: number): 'true' | 'false' => {
    const slot = getCompactToolWheelSlot(toolIndex);
    return compactInputToolFanOpen && slot !== null && Math.abs(slot) <= 2 ? 'false' : 'true';
  };

  const getCompactToolWheelSlotValue = (toolIndex: number): string => {
    return getCompactToolWheelSlotValueForIndex(
      toolIndex,
      compactInputToolWheelVisualIndex,
      COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT,
    );
  };

  const compactInputToolFanActionsDisabled = composerDisabled
    || !compactInputToolFanOpen
    || !compactInputToolFanInteractive
    || compactInputToolWheelChargeReleaseActive;
  const isCompactToolWheelActionDisabled = (toolIndex: number): boolean => (
    compactInputToolFanActionsDisabled || !isCompactToolWheelActionable(toolIndex)
  );
  const compactAvatarToolActionsDisabled = isCompactToolWheelActionDisabled(1);
  const renderCompactInputToolTooltip = (label: string) => (
    <span className="compact-input-tool-tooltip" aria-hidden="true">{label}</span>
  );
  const [
    compactInputToolWheelChargeFirstLapAngle,
    compactInputToolWheelChargeSecondLapAngle,
    compactInputToolWheelChargeThirdLapAngle,
  ] = getCompactToolWheelChargeLapAngles(compactInputToolWheelChargeRatio);
  const compactInputToolWheelChargeRattleLevel = compactInputToolWheelChargeReleaseActive
    ? 'none'
    : compactInputToolWheelChargeRatio >= COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_STRONG_RATIO
      ? 'strong'
      : compactInputToolWheelChargeRatio >= COMPACT_INPUT_TOOL_WHEEL_CHARGE_RATTLE_WEAK_RATIO
        ? 'weak'
        : 'none';
  const compactInputToolWheelVisualChargeDirection = compactInputToolWheelChargeDirection === null
    ? null
    : compactInputToolWheelChargeDirection * getCompactToolWheelVisualDirectionMultiplier(compactInputToolWheelLayout) as 1 | -1;
  const compactInputToolWheelChargeStyle = {
    '--compact-tool-wheel-charge-first-angle': `${compactInputToolWheelChargeFirstLapAngle}deg`,
    '--compact-tool-wheel-charge-second-angle': `${compactInputToolWheelChargeSecondLapAngle}deg`,
    '--compact-tool-wheel-charge-third-angle': `${compactInputToolWheelChargeThirdLapAngle}deg`,
  } as CSSProperties;
  const compactInputToolWheelDragAngleStep = compactInputToolWheelLayout === 'viewport-fit'
    ? COMPACT_TOOL_WHEEL_VIEWPORT_DRAG_ANGLE_STEP_DEG
    : COMPACT_TOOL_WHEEL_DEFAULT_DRAG_ANGLE_STEP_DEG;
  const compactInputToolWheelDragAngle = compactInputToolWheelDragOffsetRatio * compactInputToolWheelDragAngleStep;
  const compactInputToolWheelVisibleSlots = compactInputToolWheelLayout === 'viewport-fit'
    ? compactInputToolWheelViewportFitVisibleSlots
    : compactInputToolWheelDefaultVisibleSlots;
  const compactInputToolWheelSelectionTargetIndex = compactInputToolWheelHoveredIndex !== null
    && !isCompactToolWheelActionDisabled(compactInputToolWheelHoveredIndex)
      ? compactInputToolWheelHoveredIndex
      : compactInputToolWheelVisualIndex;
  const compactInputToolWheelSelectionTargetSlot = getCompactToolWheelSlotForIndex(
    compactInputToolWheelSelectionTargetIndex,
    compactInputToolWheelVisualIndex,
    COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT,
  );
  const compactInputToolWheelSelectionTargetVisual = (
    compactInputToolWheelSelectionTargetSlot !== null
      ? compactInputToolWheelVisibleSlots[compactInputToolWheelSelectionTargetSlot + 2]
      : undefined
  ) ?? compactInputToolWheelVisibleSlots[2];
  const compactInputToolWheelSelectionAngle = compactInputToolWheelSelectionTargetVisual.angleDeg
    + compactInputToolWheelDragAngle;
  const compactInputToolWheelDragStyle = {
    '--compact-tool-wheel-drag-angle': `${compactInputToolWheelDragAngle}deg`,
    '--compact-tool-wheel-drag-counter-angle': `${-compactInputToolWheelDragAngle}deg`,
    '--compact-tool-wheel-selection-angle': `${compactInputToolWheelSelectionAngle}deg`,
  } as CSSProperties;
  const syncCompactInputToolWheelPointerHover = useCallback((nextPointer?: { clientX: number; clientY: number }) => {
    const pointer = nextPointer ?? compactInputToolWheelHoverPointerRef.current;
    if (
      !pointer
      || compactInputToolFanActionsDisabled
    ) {
      setCompactInputToolWheelHoveredIndexState(null);
      return;
    }

    const fanElement = compactInputToolFanRef.current;
    const fanRect = fanElement?.getBoundingClientRect();
    if (!fanElement || !fanRect) {
      setCompactInputToolWheelHoveredIndexState(null);
      return;
    }

    const fanStyle = window.getComputedStyle ? window.getComputedStyle(fanElement) : null;
    const readFanPixelVar = (name: string, fallback: number) => {
      const rawValue = fanStyle?.getPropertyValue(name).trim() || '';
      const parsedValue = Number.parseFloat(rawValue);
      return Number.isFinite(parsedValue) ? parsedValue : fallback;
    };
    const centerX = fanRect.left + readFanPixelVar('--compact-tool-wheel-center-x', COMPACT_INPUT_TOOL_WHEEL_CENTER_X);
    const centerY = fanRect.top + readFanPixelVar('--compact-tool-wheel-center-y', COMPACT_INPUT_TOOL_WHEEL_CENTER_Y);
    const orbitRadius = readFanPixelVar('--compact-tool-wheel-orbit-radius', 80);
    const buttonSize = readFanPixelVar('--compact-tool-button-size', 38);
    const visibleSlots = compactInputToolWheelLayout === 'viewport-fit'
      ? compactInputToolWheelViewportFitVisibleSlots
      : compactInputToolWheelDefaultVisibleSlots;
    const dragAngleRad = compactInputToolWheelDragAngle * (Math.PI / 180);
    let hoveredIndex: number | null = null;
    let hoveredDistanceSquared = Number.POSITIVE_INFINITY;

    for (let toolIndex = 0; toolIndex < COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT; toolIndex += 1) {
      const slot = getCompactToolWheelSlotForIndex(
        toolIndex,
        compactInputToolWheelVisualIndex,
        COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT,
      );
      if (slot === null || Math.abs(slot) > 1) continue;
      const slotVisual = visibleSlots[slot + 2];
      if (!slotVisual) continue;
      const angleRad = (slotVisual.angleDeg * (Math.PI / 180)) + dragAngleRad;
      const itemCenterX = centerX + (Math.cos(angleRad) * orbitRadius);
      const itemCenterY = centerY + (Math.sin(angleRad) * orbitRadius);
      const hitRadius = (buttonSize * slotVisual.scale) / 2;
      const dx = pointer.clientX - itemCenterX;
      const dy = pointer.clientY - itemCenterY;
      const distanceSquared = (dx * dx) + (dy * dy);
      if (distanceSquared <= hitRadius * hitRadius && distanceSquared < hoveredDistanceSquared) {
        hoveredIndex = toolIndex;
        hoveredDistanceSquared = distanceSquared;
      }
    }

    setCompactInputToolWheelHoveredIndexState(hoveredIndex);
  }, [
    compactInputToolFanActionsDisabled,
    compactInputToolFanInteractive,
    compactInputToolFanOpen,
    compactInputToolWheelDragAngle,
    compactInputToolWheelLayout,
    compactInputToolWheelVisualIndex,
    setCompactInputToolWheelHoveredIndexState,
  ]);

  useLayoutEffect(() => {
    syncCompactInputToolWheelPointerHover();
  }, [syncCompactInputToolWheelPointerHover]);

  const getCompactToolWheelPointerHoveredValue = (toolIndex: number): 'true' | 'false' => (
    compactInputToolWheelHoveredIndex === toolIndex
    && !isCompactToolWheelActionDisabled(toolIndex)
      ? 'true'
      : 'false'
  );
  const compactToolToggleVisible = isCompactSurface && !composerHidden;
  const compactToolToggleActsAsSubmit = effectiveCompactChatState === 'input' && compactInputHasPayload;
  const compactInputToolToggleButton = compactToolToggleVisible ? (
    <button
      className={`send-button-circle compact-input-tool-toggle${compactInputToolFanOpen ? ' is-open' : ''}`}
      ref={compactInputToolToggleRef}
      type={compactToolToggleActsAsSubmit ? 'submit' : 'button'}
      data-compact-no-drag="true"
      data-compact-hit-region="true"
      data-compact-hit-region-id="input:tool-toggle"
      data-compact-hit-region-kind="input-tool-toggle"
      aria-label={compactToolToggleActsAsSubmit ? sendButtonLabel : overflowMenuAriaLabel}
      aria-haspopup={compactToolToggleActsAsSubmit ? undefined : 'true'}
      aria-expanded={compactToolToggleActsAsSubmit ? undefined : compactInputToolFanOpen}
      disabled={compactToolToggleActsAsSubmit ? !canSubmit : composerDisabled}
      onPointerEnter={compactToolToggleActsAsSubmit ? undefined : handleCompactInputToolHoverEnter}
      onPointerLeave={compactToolToggleActsAsSubmit ? undefined : handleCompactInputToolHoverLeave}
      onPointerDown={compactToolToggleActsAsSubmit ? undefined : beginCompactToolOriginDrag}
      onPointerMove={compactToolToggleActsAsSubmit ? undefined : updateCompactToolOriginDrag}
      onPointerUp={compactToolToggleActsAsSubmit ? undefined : endCompactToolOriginDrag}
      onPointerCancel={compactToolToggleActsAsSubmit ? undefined : endCompactToolOriginDrag}
      onFocus={compactToolToggleActsAsSubmit ? undefined : clearCompactInputToolFanCloseTimer}
      onBlur={compactToolToggleActsAsSubmit ? scheduleCompactInputCollapse : () => {
        scheduleCompactInputToolFanTransientClose();
        scheduleCompactInputCollapse();
      }}
      onClick={compactToolToggleActsAsSubmit ? undefined : () => {
        // 拖动文本框后补发的 click 已在 origin-drag 里置位抑制，这里消费掉，避免误展开/收起轮盘。
        if (compactToolOriginSuppressClickRef.current) {
          compactToolOriginSuppressClickRef.current = false;
          return;
        }
        toggleCompactInputToolFanByClick();
      }}
    >
      <img
        className={compactToolToggleActsAsSubmit ? undefined : 'compact-input-tool-toggle-icon'}
        src={compactToolToggleActsAsSubmit ? '/static/icons/send_new_icon.png' : '/static/icons/dropdown_arrow.png'}
        alt=""
        aria-hidden="true"
      />
    </button>
  ) : null;

  // compact 输入框/胶囊左侧毛绒球：点按=折叠为 minimized（毛绒球小窗口），按住拖>6px=拖动整个 surface。
  // 复用与右侧工具轮盘原点对偶的 origin-drag 手势：data-compact-no-drag 让宿主被动 hit-test 不重复起拖，
  // 真正拖拽经 neko:compact-surface-drag-grab 交宿主接管（web: startDrag / Electron: preload 原生窗口拖拽）。
  const compactMinimizeButton = (
    <button
      type="button"
      className="compact-chat-minimize-ball"
      aria-label={i18n('chat.reactWindowMinimize', 'Minimize')}
      title={i18n('chat.reactWindowMinimize', 'Minimize')}
      data-compact-no-drag="true"
      data-compact-hit-region="true"
      data-compact-hit-region-id="input:minimize"
      data-compact-hit-region-kind="input-minimize"
      onPointerDown={beginCompactToolOriginDrag}
      onPointerMove={updateCompactToolOriginDrag}
      onPointerUp={endCompactToolOriginDrag}
      onPointerCancel={endCompactToolOriginDrag}
      onClick={() => {
        // 拖动后补发的 click 已在 origin-drag 里置位抑制，这里消费掉；仅「无拖动的纯点按」折叠。
        if (compactToolOriginSuppressClickRef.current) {
          compactToolOriginSuppressClickRef.current = false;
          return;
        }
        // onCompactMinimizeRequest 是可选 prop：真正把 surfaceMode 切到 'minimized'
        // 的是宿主回调，切回 minimized 后才会复位 compactCollapsing。宿主没传回调时
        // 若仍 setCompactCollapsing(true)，模式永不变、collapsing 永不复位，蓝条/胶囊
        // 会一直卡在折叠样式（CodeRabbit Major）。所以缺回调时直接早退、不进折叠态。
        if (!onCompactMinimizeRequest) {
          return;
        }
        clearActiveAvatarToolSelection();
        // #3 折叠时若历史区已开，异步触发其收回动画（与折叠并行，不阻塞）。
        // 用 persist:false：只播收回动画、不把「关闭」写进偏好；并记下恢复后重开，
        // 这样 minimize→恢复后历史区按折叠前状态重新打开（不把临时折叠误当偏好变更）。
        if (compactExportHistoryOpen) {
          compactHistoryReopenAfterRestoreRef.current = true;
          closeCompactExportHistory({ persist: false });
        }
        // #2 折叠时蓝条（历史区开关）淡出。compactCollapsing→true 给蓝条加 is-collapsing。
        setCompactCollapsing(true);
        onCompactMinimizeRequest();
      }}
    >
      <span
        className="compact-chat-minimize-ball-icon"
        aria-hidden="true"
      />
    </button>
  );

  const compactInputToolFanNode = compactToolToggleVisible ? (
    <div
      ref={compactInputToolFanRef}
      className="compact-input-tool-fan"
      style={compactInputToolWheelDragStyle}
      role="group"
      aria-label={overflowMenuAriaLabel}
      data-compact-geometry-item="toolFan"
      data-compact-geometry-owner="surface"
      data-compact-no-drag="true"
      data-compact-input-tool-fan-open={compactInputToolFanOpen ? 'true' : 'false'}
      data-compact-input-tool-fan-interactive={compactInputToolFanInteractive ? 'true' : 'false'}
      data-compact-tool-wheel-layout={compactInputToolWheelLayout}
      data-compact-tool-wheel-fast-animation={compactInputToolWheelFastAnimation ? 'true' : 'false'}
      data-compact-tool-wheel-drag-active={compactInputToolWheelDragActive ? 'true' : 'false'}
      data-compact-tool-wheel-charge-active={compactInputToolWheelChargeRatio > 0 ? 'true' : 'false'}
      data-compact-tool-wheel-charge-rattle={compactInputToolWheelChargeRattleLevel}
      data-compact-tool-wheel-charge-direction={compactInputToolWheelVisualChargeDirection === 1 ? 'forward' : compactInputToolWheelVisualChargeDirection === -1 ? 'backward' : 'none'}
      data-compact-tool-wheel-charge-release-active={compactInputToolWheelChargeReleaseActive ? 'true' : 'false'}
      data-compact-tool-wheel-charge-release-offset={compactInputToolWheelChargeReleaseVisualStepOffset}
      aria-hidden={compactInputToolFanOpen ? 'false' : 'true'}
      onPointerEnter={(event) => {
        recordCompactInputToolWheelPointerPosition(event.clientX, event.clientY);
        syncCompactInputToolWheelPointerHover({ clientX: event.clientX, clientY: event.clientY });
        handleCompactInputToolHoverEnter(event);
      }}
      onPointerLeave={(event) => {
        clearCompactInputToolWheelPointerHover();
        handleCompactInputToolHoverLeave(event);
      }}
      onFocus={() => {
        clearCompactInputToolFanCloseTimer();
      }}
      onBlur={() => {
        scheduleCompactInputToolFanTransientClose();
      }}
      onClickCapture={(event) => {
        if (
          compactInputToolWheelSuppressClickRef.current
          || compactToolOriginSuppressClickRef.current
          || (compactInputToolFanOpen && !compactInputToolFanInteractiveRef.current)
        ) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
      onPointerDownCapture={(event) => {
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        const fanRect = event.currentTarget.getBoundingClientRect();
        const localX = event.clientX - fanRect.left;
        const localY = event.clientY - fanRect.top;
        const toggleRect = compactInputToolToggleRef.current?.getBoundingClientRect();
        const isOriginClick = toggleRect && toggleRect.width > 0 && toggleRect.height > 0
          ? (
            event.clientX >= toggleRect.left
            && event.clientX <= toggleRect.right
            && event.clientY >= toggleRect.top
            && event.clientY <= toggleRect.bottom
          )
          : (
            localX >= 0
            && localY >= 0
            && localX <= COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE
            && localY <= COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE
          );
        if (!isOriginClick) return;
        // 按在轮盘中心：保持原有「即时收起 + 抑制随后 click」语义（既有命中测试依赖此路径），
        // 同时额外开启原点拖拽追踪——setPointerCapture 让 fan 关闭后仍能收到 pointermove/up，
        // 以便检测是否要拖动文本框。stopPropagation 阻止冒泡 onPointerDown 启动轮盘旋转。
        event.preventDefault();
        event.stopPropagation();
        markCompactToolFanOriginClickSuppressed();
        beginCompactToolOriginDrag(event);
      }}
      onPointerDown={(event) => {
        if (event.pointerType === 'mouse' && event.button !== 0) return;
        const fanRect = event.currentTarget.getBoundingClientRect();
        const localX = event.clientX - fanRect.left;
        const localY = event.clientY - fanRect.top;
        const toggleRect = compactInputToolToggleRef.current?.getBoundingClientRect();
        const isOriginClick = toggleRect && toggleRect.width > 0 && toggleRect.height > 0
          ? (
            event.clientX >= toggleRect.left
            && event.clientX <= toggleRect.right
            && event.clientY >= toggleRect.top
            && event.clientY <= toggleRect.bottom
          )
          : (
            localX >= 0
            && localY >= 0
            && localX <= COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE
            && localY <= COMPACT_INPUT_TOOL_FAN_ORIGIN_CLOSE_SIZE
          );
        if (isOriginClick) {
          // 防御：通常 onPointerDownCapture 已 stopPropagation 接管原点；这里兜底维持原收起语义。
          event.preventDefault();
          event.stopPropagation();
          markCompactToolFanOriginClickSuppressed();
          return;
        }
        const captureTarget = event.target instanceof Element ? event.target : event.currentTarget;
        const dragPoint = getCompactToolWheelBoundedDragPoint(event.clientX, event.clientY);
        clearCompactInputToolWheelChargeReleaseTimer();
        resetCompactInputToolWheelCharge();
        setCompactInputToolWheelDragOffsetRatio(0);
        compactInputToolWheelSuppressClickRef.current = false;
        // 清掉可能残留的原点拖拽抑制（上次原点拖拽若无补发 click），避免误吞这次轮盘按钮 click。
        compactToolOriginSuppressClickRef.current = false;
        compactInputToolWheelDragActiveRef.current = true;
        dispatchCompactToolWheelDragState(true, event.pointerId);
        clearCompactInputToolFanCloseTimer();
        scheduleCompactInputToolWheelDragGuardRelease();
        compactInputToolWheelPointerRef.current = {
          id: event.pointerId,
          x: dragPoint.x,
          y: dragPoint.y,
          angle: dragPoint.angle,
          angleRemainder: 0,
          dragOffsetRatio: 0,
          didRotate: false,
          captureTarget,
        };
        try {
          captureTarget.setPointerCapture?.(event.pointerId);
        } catch (_) {}
      }}
      onPointerMove={(event) => {
        recordCompactInputToolWheelPointerPosition(event.clientX, event.clientY);
        syncCompactInputToolWheelPointerHover({ clientX: event.clientX, clientY: event.clientY });
        if (compactToolOriginDragRef.current) {
          updateCompactToolOriginDrag(event);
          return;
        }
        updateCompactInputToolWheelDrag({
          pointerId: event.pointerId,
          clientX: event.clientX,
          clientY: event.clientY,
          buttons: event.buttons,
          pointerType: event.pointerType,
          preventDefault: () => event.preventDefault(),
        });
      }}
      onWheel={rotateCompactInputToolWheelByScroll}
      onPointerUp={(event) => {
        if (compactToolOriginDragRef.current) {
          endCompactToolOriginDrag(event);
          return;
        }
        finishCompactToolWheelPointer(event);
      }}
      onPointerCancel={(event) => {
        if (compactToolOriginDragRef.current) {
          endCompactToolOriginDrag(event);
          return;
        }
        finishCompactToolWheelPointer(event);
      }}
    >
      <div className="compact-input-tool-fan-hit-region" aria-hidden="true" />
      <div
        className="compact-input-tool-wheel-charge"
        style={compactInputToolWheelChargeStyle}
        aria-hidden="true"
      />
      <div className="compact-input-tool-wheel-selection-pointer" aria-hidden="true" />
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-import"
        type="button"
        aria-label={resolvedImportImageAriaLabel}
        disabled={isCompactToolWheelActionDisabled(4)}
        tabIndex={getCompactToolWheelTabIndex(4)}
        aria-hidden={getCompactToolWheelAriaHidden(4)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(4)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(4)}
        onClick={compactFanRunAction(onComposerImportImage)}
      >
        <img src="/static/icons/import_image_icon.png" alt="" aria-hidden="true" />
        {renderCompactInputToolTooltip(importImageButtonLabel)}
      </button>
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-screenshot"
        type="button"
        aria-label={resolvedScreenshotAriaLabel}
        disabled={isCompactToolWheelActionDisabled(0)}
        tabIndex={getCompactToolWheelTabIndex(0)}
        aria-hidden={getCompactToolWheelAriaHidden(0)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(0)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(0)}
        onClick={compactFanRunAction(onComposerScreenshot)}
      >
        <img src="/static/icons/screenshot_new_icon.png" alt="" aria-hidden="true" />
        {renderCompactInputToolTooltip(screenshotButtonLabel)}
      </button>
      <button
        className={`composer-tool-btn composer-galgame-btn compact-input-tool-item compact-input-tool-item-galgame${galgameModeEnabled ? ' is-active' : ''}`}
        type="button"
        aria-label={resolvedGalgameAriaLabel}
        aria-pressed={galgameModeEnabled}
        disabled={isCompactToolWheelActionDisabled(6)}
        tabIndex={getCompactToolWheelTabIndex(6)}
        aria-hidden={getCompactToolWheelAriaHidden(6)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(6)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(6)}
        data-compact-tool-active={galgameModeEnabled ? 'true' : 'false'}
        onClick={compactFanToggleOnAction(onGalgameModeToggle)}
      >
        <span className="composer-galgame-btn-glyph" aria-hidden="true">G</span>
        {renderCompactInputToolTooltip(galgameToggleButtonLabel)}
      </button>
      <button
        className={`composer-tool-btn composer-translate-btn compact-input-tool-item compact-input-tool-item-translate${translateEnabled ? ' is-active' : ''}`}
        type="button"
        aria-label={resolvedTranslateAriaLabel}
        aria-pressed={translateEnabled}
        disabled={isCompactToolWheelActionDisabled(2)}
        tabIndex={getCompactToolWheelTabIndex(2)}
        aria-hidden={getCompactToolWheelAriaHidden(2)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(2)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(2)}
        data-compact-tool-active={translateEnabled ? 'true' : 'false'}
        onClick={compactFanToggleOnAction(onTranslateToggle)}
      >
        <img src="/static/icons/translate_icon.png" alt="" aria-hidden="true" />
        {renderCompactInputToolTooltip(translateButtonLabel)}
      </button>
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-jukebox"
        type="button"
        aria-label={jukeboxButtonAriaLabel}
        disabled={isCompactToolWheelActionDisabled(3)}
        tabIndex={getCompactToolWheelTabIndex(3)}
        aria-hidden={getCompactToolWheelAriaHidden(3)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(3)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(3)}
        onClick={compactFanRunAction(onJukeboxClick)}
      >
        <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
        {renderCompactInputToolTooltip(jukeboxButtonLabel)}
      </button>
      <button
        className={`composer-tool-btn compact-input-tool-item compact-input-tool-item-export${compactExportControlsVisible ? ' is-active' : ''}`}
        type="button"
        aria-label={compactExportControlsButtonLabel}
        aria-pressed={compactExportControlsVisible}
        disabled={isCompactToolWheelActionDisabled(5)}
        tabIndex={getCompactToolWheelTabIndex(5)}
        aria-hidden={getCompactToolWheelAriaHidden(5)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(5)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(5)}
        data-compact-tool-active={compactExportControlsVisible ? 'true' : 'false'}
        onClick={compactFanRunAction(handleCompactExportControlsToggle)}
      >
        <svg viewBox="0 0 1024 1024" width="24" height="24" fill="currentColor" aria-hidden="true">
          <path d="M855.467 501.333c-17.067 0-32 14.934-32 32v198.4c0 70.4-59.734 130.134-130.134 130.134H356.267c-83.2 0-151.467-66.134-151.467-149.334V358.4c0-64 53.333-117.333 117.333-117.333h168.534c17.066 0 32-14.934 32-32s-14.934-32-32-32H322.133c-100.266 0-181.333 81.066-181.333 181.333v352c0 117.333 96 213.333 215.467 213.333h337.066c106.667 0 194.134-87.466 194.134-194.133V533.333c0-17.066-14.934-32-32-32zM680.533 256H761.6L458.667 569.6A30.933 30.933 0 0 0 480 622.933c8.533 0 17.067-4.266 23.467-10.666l305.066-313.6v89.6c0 17.066 14.934 32 32 32s32-14.934 32-32v-147.2c0-27.734-23.466-51.2-51.2-51.2h-140.8c-17.066 0-32 14.933-32 32s14.934 34.133 32 34.133z" />
        </svg>
        {renderCompactInputToolTooltip(compactExportControlsButtonLabel)}
      </button>
      <div
        className="composer-tool-menu compact-input-tool-item compact-input-tool-item-avatar"
        ref={toolMenuRef}
        aria-hidden={getCompactToolWheelAriaHidden(1)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(1)}
        data-compact-tool-pointer-hovered={getCompactToolWheelPointerHoveredValue(1)}
        data-compact-tool-active={toolMenuOpen || activeToolItem ? 'true' : 'false'}
      >
        <button
          className={`composer-tool-btn composer-emoji-btn${toolMenuOpen || activeToolItem ? ' is-active' : ''}`}
          type="button"
          aria-label={i18n('chat.avatarToolsButtonAriaLabel', 'Avatar tools')}
          aria-controls={toolMenuOpen ? 'composer-avatar-tool-quickbar' : undefined}
          aria-expanded={toolMenuOpen}
          disabled={compactAvatarToolActionsDisabled}
          tabIndex={getCompactToolWheelTabIndex(1)}
          onClick={(event) => {
            if (shouldSuppressCompactToolClick(event)) {
              event.preventDefault();
              event.stopPropagation();
              return;
            }
            if (activeToolItem) {
              event.stopPropagation();
              clearActiveAvatarToolAndCloseCompactFan();
              return;
            }
            compactInputToolFanOpenIntentRef.current = 'click';
            clearCompactInputToolFanCloseTimer();
            setToolMenuOpen(open => !open);
          }}
        >
          <img
            src="/static/icons/emoji_icon.png"
            alt=""
            aria-hidden="true"
          />
        </button>
        {renderCompactInputToolTooltip(i18n('chat.avatarToolsButtonAriaLabel', 'Avatar tools'))}
        {activeToolItem ? (
          <button
            className="composer-tool-clear-btn"
            type="button"
            aria-label={clearAvatarToolAriaLabel}
            title={clearAvatarToolAriaLabel}
            disabled={compactAvatarToolActionsDisabled}
            tabIndex={getCompactToolWheelTabIndex(1)}
            onClick={(event) => {
              if (shouldSuppressCompactToolClick(event)) {
                event.preventDefault();
                event.stopPropagation();
                return;
              }
              event.stopPropagation();
              clearActiveAvatarToolAndCloseCompactFan();
            }}
          >
            <span className="composer-tool-clear-icon" aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {toolMenuOpen && compactInputToolFanOpen ? (
        <AvatarToolQuickbar
          activeToolIds={activeAvatarToolIds}
          activeAvatarToolId={activeAvatarToolId}
          availableTools={toolIconItems}
          disabled={compactAvatarToolActionsDisabled}
          getToolVariant={(toolId) => (
            activeAvatarToolId === toolId ? effectiveAvatarToolVariant : 'primary'
          )}
          onToolClick={(item, event) => {
            if (shouldSuppressCompactToolClick(event)) {
              event.preventDefault();
              event.stopPropagation();
              return;
            }
            handleAvatarQuickbarToolClick(item, event);
            closeCompactInputToolFanFromUserClick();
          }}
          onEditClick={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            setAvatarToolManagerAnchorRect({
              left: rect.left,
              top: rect.top,
              right: rect.right,
              bottom: rect.bottom,
              width: rect.width,
              height: rect.height,
            });
            setAvatarToolManagerOpen(true);
          }}
        />
      ) : null}
    </div>
  ) : null;
  const composerAttachmentPreviewNode = composerAttachments.length > 0 ? (
    <div
      className={`composer-attachment-viewport${isCompactSurface ? ' composer-attachment-viewport-compact' : ''}`}
      aria-label={composerAttachmentsAriaLabel}
      data-compact-geometry-item={isCompactSurface ? 'attachments' : undefined}
      data-compact-geometry-owner={isCompactSurface ? 'surface' : undefined}
      data-compact-no-drag={isCompactSurface ? 'true' : undefined}
    >
      <div className="composer-attachments" role="list">
        {composerAttachments.map((attachment) => (
          <figure key={attachment.id} className="composer-attachment-card" role="listitem">
            <img
              className="composer-attachment-image"
              src={attachment.url}
              alt={attachment.alt || ''}
              loading="lazy"
            />
            <button
              className="composer-attachment-remove"
              type="button"
              aria-label={`${removeAttachmentButtonAriaLabel}: ${attachment.alt || attachment.id}`}
              aria-disabled={composerDisabled}
              disabled={composerDisabled}
              onClick={() => {
                if (!composerDisabled) {
                  onComposerRemoveAttachment?.(attachment.id);
                }
              }}
            >
              <span className="composer-attachment-remove-icon" aria-hidden="true" />
            </button>
          </figure>
        ))}
      </div>
    </div>
  ) : null;

  function prepareComposerOptionMarquee(
    event: ReactMouseEvent<HTMLButtonElement> | ReactFocusEvent<HTMLButtonElement>,
  ) {
    const button = event.currentTarget;
    const text = button.querySelector<HTMLElement>('.composer-galgame-option-text');
    const inner = button.querySelector<HTMLElement>('.composer-galgame-option-text-inner');

    button.removeAttribute('data-composer-option-marquee');
    button.style.removeProperty('--composer-option-marquee-distance');
    button.style.removeProperty('--composer-option-marquee-duration');

    if (!text || !inner) return;

    const overflowDistance = Math.ceil(inner.scrollWidth - text.clientWidth);
    if (overflowDistance <= COMPOSER_OPTION_MARQUEE_MIN_DISTANCE) return;

    const distance = overflowDistance + COMPOSER_OPTION_MARQUEE_END_PADDING;

    const duration = Math.min(
      Math.max(
        Math.ceil((distance / COMPOSER_OPTION_MARQUEE_PIXELS_PER_SECOND) * 1000),
        COMPOSER_OPTION_MARQUEE_MIN_DURATION_MS,
      ),
      COMPOSER_OPTION_MARQUEE_MAX_DURATION_MS,
    );

    button.style.setProperty('--composer-option-marquee-distance', `${distance}px`);
    button.style.setProperty('--composer-option-marquee-duration', `${duration}ms`);
    button.setAttribute('data-composer-option-marquee', 'true');
  }

  function clearComposerOptionMarquee(
    event: ReactMouseEvent<HTMLButtonElement> | ReactFocusEvent<HTMLButtonElement>,
  ) {
    event.currentTarget.removeAttribute('data-composer-option-marquee');
    event.currentTarget.style.removeProperty('--composer-option-marquee-distance');
    event.currentTarget.style.removeProperty('--composer-option-marquee-duration');
  }

  const choiceLayerNode = (
    <div
      className={`composer-choice-layer${isCompactSurface ? ' compact-chat-choice-anchor' : ''}`}
      ref={isCompactSurface ? compactChoiceLayerRef : undefined}
      data-compact-geometry-item={isCompactSurface ? 'choice' : undefined}
      data-compact-geometry-owner={isCompactSurface ? 'surface' : undefined}
      data-compact-no-drag={isCompactSurface ? 'true' : undefined}
      data-choice-layer-open={compactChoiceLayerOpen ? 'true' : 'false'}
      data-chat-surface-mode={chatSurfaceMode}
      data-compact-choice-placement={isCompactSurface ? compactChoiceLayerPlacement : undefined}
    >
      {galgameOptionsVisible ? (
        <div
          className={`composer-galgame-slot${compactChoiceLayerOpen && galgameOptionsVisible ? ' is-open' : ''}`}
          aria-hidden={!(compactChoiceLayerOpen && galgameOptionsVisible)}
        >
          <div
            className={`composer-galgame-options${galgameOptionsLoading ? ' is-loading' : ''}`}
            role="group"
            aria-label={galgameToggleButtonLabel}
          >
            {galgameOptions.length > 0
              ? galgameOptions.slice(0, 3).map((option, index) => (
                  <button
                    key={`${index}-${option.label}`}
                    type="button"
                    className="composer-galgame-option"
                    disabled={composerDisabled || galgameOptionsLoading}
                    tabIndex={compactChoiceLayerOpen && galgameOptionsVisible ? 0 : -1}
                    onMouseEnter={prepareComposerOptionMarquee}
                    onMouseLeave={clearComposerOptionMarquee}
                    onFocus={prepareComposerOptionMarquee}
                    onBlur={clearComposerOptionMarquee}
                    onClick={() => {
                      if (submittingRef.current) return;
                      submittingRef.current = true;
                      try {
                        restoreCompactExportHistoryToBottomForOutgoingMessage();
                        onGalgameOptionSelect?.(option);
                        requestCompactChatState('default');
                      } finally {
                        requestAnimationFrame(() => { submittingRef.current = false; });
                      }
                    }}
                  >
                    <span className="composer-galgame-option-label" aria-hidden="true">{option.label}.</span>
                    <span className="composer-galgame-option-text">
                      <span className="composer-galgame-option-text-inner">{option.text}</span>
                    </span>
                  </button>
                ))
              : galgameOptionsLoading
                ? ['A', 'B', 'C'].map((label) => (
                    <button
                      key={label}
                      type="button"
                      className="composer-galgame-option is-placeholder"
                      disabled
                      tabIndex={-1}
                    >
                      <span className="composer-galgame-option-label" aria-hidden="true">{label}.</span>
                      <span className="composer-galgame-option-text">
                        <span className="composer-galgame-option-text-inner">{galgameLoadingLabel}</span>
                      </span>
                    </button>
                  ))
                : null}
          </div>
        </div>
      ) : null}
      {choicePromptHasOptions ? (
        <div
          className={`composer-galgame-slot composer-choice-slot${compactChoiceLayerOpen ? ' is-open' : ''} is-${choicePrompt.source}`}
          aria-hidden={compactChoiceLayerOpen ? 'false' : 'true'}
          data-choice-source={choicePrompt.source}
        >
          <div
            className="composer-galgame-options composer-choice-options"
            role="group"
            aria-label={choicePrompt.source === 'mini_game_invite'
              ? i18n('chat.miniGameInviteOptionsAriaLabel', 'Mini-game invite options')
              : choicePrompt.source === 'new_user_icebreaker'
                ? i18n('chat.newUserIcebreakerOptionsAriaLabel', 'New user icebreaker options')
                : galgameToggleButtonLabel}
          >
            {choicePrompt.options.slice(0, 3).map((option, index) => (
              <button
                key={`${index}-${option.choice}`}
                type="button"
                className="composer-galgame-option composer-choice-option"
                disabled={composerDisabled}
                onMouseEnter={prepareComposerOptionMarquee}
                onMouseLeave={clearComposerOptionMarquee}
                onFocus={prepareComposerOptionMarquee}
                onBlur={clearComposerOptionMarquee}
                onClick={() => {
                  if (submittingRef.current) return;
                  submittingRef.current = true;
                  try {
                    restoreCompactExportHistoryToBottomForOutgoingMessage();
                    onChoiceSelect?.(option, choicePrompt.source);
                    requestCompactChatState('default');
                  } finally {
                    requestAnimationFrame(() => { submittingRef.current = false; });
                  }
                }}
              >
                <span className="composer-galgame-option-text composer-choice-option-text">
                  <span className="composer-galgame-option-text-inner">{option.label}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
  const compactChoiceLayerNode = isCompactSurface
    ? (typeof document !== 'undefined' ? createPortal(choiceLayerNode, document.body) : choiceLayerNode)
    : null;
  const compactExportHistoryMessages = compactExportHistoryOpen
    ? messages
    : (compactExportHistoryClosingMessages || messages);
  const compactExportHistoryElement = isCompactSurface && compactExportHistoryMounted ? (
    <CompactExportHistoryPanel
      messages={compactExportHistoryMessages}
      selectedIds={compactExportSelectedIds}
      selectedCount={compactExportSelectedCount}
      selectableCount={compactExportSelectableCount}
      autoScrollToBottom={compactExportAutoScrollToBottom}
      previewOpen={compactExportPreviewOpen}
      controlsOpen={compactExportControlsOpen}
      choiceLayerAbove={compactChoiceLayerOpen && compactChoiceLayerPlacement === 'above'}
      visibilityState={compactExportHistoryOpen ? 'open' : 'closing'}
      thinking={focusThinking}
      failedStatusLabel={failedStatusLabel}
      onAutoScrollToBottomChange={setCompactExportAutoScrollToBottom}
      onToggleMessage={handleCompactExportToggleMessage}
      onSelectAll={handleCompactExportSelectAll}
      onClearSelection={handleCompactExportClearSelection}
      onInvertSelection={handleCompactExportInvertSelection}
      onRequestPreview={handleCompactExportPreviewRequest}
      onClosePreview={handleCompactExportPreviewClose}
      onBuildPreview={handleCompactInlineBuildPreview}
      onCopyExport={handleCompactInlineCopyExport}
      onDownloadExport={handleCompactInlineDownloadExport}
      historyResizeActive={compactHistoryResizeActive}
      historyResizeContentLocked={compactHistoryResizeContentLocked}
      onHistoryResizePointerDown={handleCompactHistoryResizePointerDown}
      onHistoryResizePointerMove={handleCompactHistoryResizePointerMove}
      onHistoryResizePointerUp={handleCompactHistoryResizePointerUp}
      onHistoryResizePointerCancel={handleCompactHistoryResizePointerCancel}
    />
  ) : null;
  const compactExportHistoryNode = compactExportHistoryElement;
  const compactHistoryVisibilityHandleNode = isCompactSurface ? (
    <button
      className={`compact-history-visibility-handle${compactExportHistoryOpen ? ' is-open' : ''}${compactCollapsing ? ' is-collapsing' : ''}`}
      type="button"
      aria-label={compactExportHistoryToggleLabel}
      aria-expanded={compactExportHistoryOpen}
      title={compactExportHistoryToggleLabel}
      disabled={composerDisabled}
      data-compact-geometry-owner="surface"
      data-compact-geometry-item="historyHandle"
      data-compact-no-drag="true"
      data-compact-history-open={compactExportHistoryOpen ? 'true' : 'false'}
      onPointerDown={handleCompactHistoryVisibilityPress}
      onPointerCancel={handleCompactHistoryVisibilityPointerCancel}
      onClick={handleCompactHistoryVisibilityClick}
    >
      <span className="compact-history-visibility-handle-triangle" aria-hidden="true" />
    </button>
  ) : null;
  // 音乐条可见性与「聊天历史折叠」解耦：只要有音乐内容就常显（空态由 CSS `:empty { display:none }`
  // 兜底），不再随历史区收起而隐藏——否则历史默认折叠的 A/B closed 分支会连带看不到主动分享音乐条。
  const compactMusicPlayerVisibility = 'open' as const;
  const closeMemeButtonAriaLabel = i18n('chat.closeMemeAriaLabel', 'Close image');
  const compactMemeOverlayImageLoadingProps = { loading: 'eager' as const, fetchpriority: 'high' as const };
  const compactMemeOverlayNode = compactMemeOverlayVisible && compactMemeOverlay ? (
    <div
      className="compact-meme-overlay"
      data-compact-meme-overlay="compact-surface"
      data-compact-geometry-owner="surface"
      data-compact-geometry-item="meme"
      data-compact-geometry-hit-scope="children"
    >
      {/* frame 收紧到图片实际尺寸，让关闭叉贴在「图片」右上角而非更宽的 overlay 右上角（图片在 overlay
          里居中、常比 overlay 窄）。 */}
      <div className="compact-meme-overlay-frame">
        {/* 被动弹出的单图挂件仅在历史区收起后显示；历史打开时由历史列表承载同一条图片消息，避免重复展示。
            一渲染就 fixed 钉在视口内，没有「视口外延迟加载」的场景——lazy 对它零
            收益（实测 lazy/eager 行为一致，图都会立刻加载），eager 语义更直接、也省掉一层
            IntersectionObserver 判定。注：表情包「常显、不被同轮台词顶掉」靠的是上面 compactMemeOverlay
            的 role 收起逻辑，不是这个属性。 */}
        <img
          key={compactMemeOverlay.url}
          src={compactMemeOverlay.url}
          alt={compactMemeOverlay.alt}
          {...compactMemeOverlayImageLoadingProps}
          decoding="async"
          ref={handleCompactMemeOverlayImageRef}
          onLoad={markCompactMemeOverlayImageSettled}
          onError={handleCompactMemeOverlayImageError}
        />
        {/* 关闭叉：overlay 整体 pointer-events:none（点击穿透到桌面/下层），唯独这个按钮 CSS 里单独开
            auto 才接得住点击；点了把当前 meme id 记进 dismissedMemeId（会话级），下一张新 meme 照常显示。
            ⚠️ data-compact-hit-region 必带：overlay 的 data-compact-geometry-hit-scope="children" 让 host
            只把带该标记的子元素登记成 native 可交互区（见 app-react-chat-window collectCompactCompositeGeometryItems）。
            漏了它，Electron pass-through 窗口会把按钮当穿透区、点击穿到桌面（普通浏览器窗口测不出，对齐音乐条）。 */}
        {compactMemeOverlayImageSettled ? (
          <button
            type="button"
            className="compact-meme-overlay-close"
            data-compact-hit-region="true"
            data-compact-hit-region-id="meme:close"
            data-compact-hit-region-kind="meme-close"
            aria-label={closeMemeButtonAriaLabel}
            title={closeMemeButtonAriaLabel}
            onClick={(event) => {
              event.stopPropagation();
              setDismissedMemeId(compactMemeOverlay.id);
            }}
          >
            <svg
              className="compact-meme-overlay-close-icon"
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
            >
              <path
                d="M4.5 4.5 11.5 11.5 M11.5 4.5 4.5 11.5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </button>
        ) : null}
      </div>
    </div>
  ) : null;
  const compactMusicPlayerMountNode = isCompactSurface ? (
    <div
      id="music-player-mount"
      className="compact-music-player-mount"
      data-music-player-mount="compact-surface"
      data-compact-music-player-visibility={compactMusicPlayerVisibility}
      data-compact-geometry-owner="surface"
      data-compact-geometry-item="musicPlayer"
      data-compact-geometry-hit-scope="children"
      data-compact-no-drag="true"
      aria-hidden={compactMusicPlayerVisibility === 'open' ? undefined : true}
    />
  ) : null;
  const compactSurfaceShellStyle = isCompactSurface
    && compactSurfaceEffectiveWidth !== null
    && !isDesktopCompactSurfaceLayoutActive()
    ? ({
      '--compact-surface-resize-width': `${compactSurfaceEffectiveWidth}px`,
    } as CSSProperties)
    : undefined;
  const chatBodyNode = isCompactSurface ? (
    <section
      className="chat-body chat-body-compact-surface"
      data-compact-chat-state={effectiveCompactChatState}
      data-compact-has-visible-choices={compactSurfaceChoicesVisible ? 'true' : 'false'}
    >
      <div
        className={`compact-chat-stage compact-chat-stage-${effectiveCompactChatState}`}
        data-compact-chat-state={effectiveCompactChatState}
        data-compact-stage-layout="stage2"
      >
        <div
          className="compact-chat-stage-body-slot"
          data-compact-stage-slot="body"
          data-compact-stage-fallback="message-list"
        />
      </div>
    </section>
  ) : null;
  const shouldRenderComposerPanel = isCompactSurface || !composerHidden;

  return (
    <main
      className={`app-shell ${surfaceModeClassName}`}
      ref={appShellRef}
      data-chat-surface-mode={chatSurfaceMode}
      data-compact-chat-state={effectiveCompactChatState}
      data-compact-export-history-open={isCompactSurface && compactExportHistoryOpen ? 'true' : 'false'}
      data-compact-export-controls-open={isCompactSurface && compactExportControlsVisible ? 'true' : 'false'}
      data-compact-export-preview-open={isCompactSurface && compactExportPreviewOpen ? 'true' : 'false'}
      data-compact-export-selected-count={isCompactSurface ? compactExportSelectedCount : 0}
      data-compact-export-auto-scroll={isCompactSurface && compactExportAutoScrollToBottom ? 'true' : 'false'}
      data-compact-tool-layer-open={isCompactSurface && compactInputToolFanOpen ? 'true' : 'false'}
      data-focus-active={focusActive ? 'true' : 'false'}
    >
      {focusActive ? (
        <div
          className="chat-surface-focus-indicator"
          role="status"
          aria-live="polite"
          title={i18n('chat.focusIndicator', '凝神中')}
        >
          <span className="chat-surface-focus-indicator-label">
            {i18n('chat.focusIndicator', '凝神中')}
          </span>
        </div>
      ) : null}
      <div className="chat-focus-overlay" aria-hidden="true" />
      {compactExportHistoryNode}
      {compactHistoryVisibilityHandleNode}
      {compactMusicPlayerMountNode}
      {compactMemeOverlayNode}
      {compactChoiceLayerNode}
      <AvatarToolItemManager
        open={isCompactSurface && avatarToolManagerOpen}
        activeToolIds={activeAvatarToolIds}
        availableTools={toolIconItems}
        anchorRect={avatarToolManagerAnchorRect}
        onSave={handleAvatarToolManagerSave}
        onCancel={() => setAvatarToolManagerOpen(false)}
      />
      <AvatarToolVisuals model={avatarToolRuntime.visualModel} />
      <section
        className={`chat-window ${surfaceModeClassName}`}
        aria-label={chatWindowAriaLabel}
        data-chat-surface-mode={chatSurfaceMode}
        data-compact-chat-state={effectiveCompactChatState}
      >
        <header className="window-topbar">
          <div className="window-title-group">
            <div className="window-avatar window-avatar-image-shell">
              <img className="window-avatar-image" src={iconSrc} alt={title} />
            </div>
            <h1 className="window-title" id="react-chat-window-title">{title}</h1>
          </div>
          {/* Avatar button moved to #react-chat-window-header-actions in host template */}
        </header>

        {chatBodyNode}

        {shouldRenderComposerPanel ? (
          <footer
            className={`composer-panel ${surfaceModeClassName}${galgameModeEnabled ? ' is-galgame-mode' : ''}`}
            data-chat-surface-mode={chatSurfaceMode}
            data-compact-chat-state={effectiveCompactChatState}
          >
            {!isCompactSurface ? <div id="music-player-mount" className="composer-music-player-mount" /> : null}
            <form className="composer" onSubmit={(event) => {
              event.preventDefault();
              submitDraft();
            }}>
              {isCompactSurface ? (
                <div
                  className={`compact-chat-surface-shell${
                    compactCollapsing
                      ? ' neko-compact-collapsing'
                      : compactExpanding
                        ? ' neko-compact-expanding'
                        : ''
                  }`}
                  ref={compactInputShellRef}
                  data-compact-chat-state={effectiveCompactChatState}
                  data-compact-tool-layer-open={compactToolToggleVisible && compactInputToolFanOpen ? 'true' : 'false'}
                  style={compactSurfaceShellStyle}
                  onBlurCapture={effectiveCompactChatState === 'input' ? scheduleCompactInputCollapse : undefined}
                >
                  <div
                    className="compact-chat-resize-handle compact-chat-resize-handle-left"
                    data-compact-resize-side="left"
                    data-compact-geometry-item="resizeHandle"
                    data-compact-geometry-owner="surface"
                    data-compact-no-drag="true"
                    aria-hidden="true"
                    onPointerDown={(event) => handleCompactSurfaceResizePointerDown('left', event)}
                    onPointerMove={handleCompactSurfaceResizePointerMove}
                    onPointerUp={handleCompactSurfaceResizePointerUp}
                    onPointerCancel={handleCompactSurfaceResizePointerCancel}
                    onLostPointerCapture={handleCompactSurfaceResizePointerCancel}
                  />
                  <div
                    className="compact-chat-resize-handle compact-chat-resize-handle-right"
                    data-compact-resize-side="right"
                    data-compact-geometry-item="resizeHandle"
                    data-compact-geometry-owner="surface"
                    data-compact-no-drag="true"
                    aria-hidden="true"
                    onPointerDown={(event) => handleCompactSurfaceResizePointerDown('right', event)}
                    onPointerMove={handleCompactSurfaceResizePointerMove}
                    onPointerUp={handleCompactSurfaceResizePointerUp}
                    onPointerCancel={handleCompactSurfaceResizePointerCancel}
                    onLostPointerCapture={handleCompactSurfaceResizePointerCancel}
                  />
                  <div
                    className="compact-chat-surface-frame"
                    data-compact-geometry-item={effectiveCompactChatState === 'input' ? 'input' : 'capsule'}
                    data-compact-geometry-owner="surface"
                    data-compact-drag-surface="true"
                    data-compact-chat-state={effectiveCompactChatState}
                    data-compact-geometry-part={effectiveCompactChatState === 'input' ? 'inputBody' : 'capsuleBody'}
                    data-compact-geometry-hit-scope={!composerHidden ? 'children' : undefined}
                    data-compact-tool-toggle-visible={compactToolToggleVisible ? 'true' : 'false'}
                    onPointerDown={beginCompactToolOriginDrag}
                    onPointerMove={updateCompactToolOriginDrag}
                    onPointerUp={endCompactToolOriginDrag}
                    onPointerCancel={endCompactToolOriginDrag}
                    onClickCapture={suppressCompactToolOriginClickAfterDrag}
                  >
                    {effectiveCompactChatState === 'input' ? (
                      <>
                        {/* 输入态左侧毛绒球：点按折叠为 minimized，按住拖动整个输入框（见 compactMinimizeButton 定义）。
                            按住把手不会让 textarea 失焦收起输入态——宿主 mousedown 会 preventDefault。 */}
                        {compactMinimizeButton}
                        <textarea
                          className="composer-input"
                          ref={compactInputRef}
                          data-compact-no-drag="true"
                          data-compact-hit-region="true"
                          data-compact-hit-region-id="input:text"
                          data-compact-hit-region-kind="input-text"
                          placeholder={inputPlaceholder}
                          aria-label={inputPlaceholder}
                          rows={1}
                          value={draft}
                          readOnly={compactTextEntryLocked}
                          disabled={composerDisabled}
                          onChange={(event) => {
                            if (compactTextEntryLocked) return;
                            setDraft(event.target.value);
                            if (event.target.value.trim().length > 0) {
                              closeCompactInputToolFan();
                            }
                          }}
                          onBlur={scheduleCompactInputCollapse}
                          onKeyDown={(event) => {
                            if (event.nativeEvent.isComposing) return;
                            if (event.key === 'Enter' && !event.shiftKey) {
                              event.preventDefault();
                              submitDraft();
                            }
                          }}
                        />
                        {compactInputToolToggleButton}
                      </>
                    ) : (
                      <>
                        {compactMinimizeButton}
                        <button
                          className="compact-chat-capsule-button"
                          type="button"
                          data-compact-hit-region="true"
                          data-compact-hit-region-id="capsule:text"
                          data-compact-hit-region-kind="capsule-text"
                          disabled={compactCapsuleEntryLocked}
                          onClick={() => {
                            if (composerHidden) return;
                            if (compactCapsuleEntryLocked) return;
                            requestCompactChatState('input');
                          }}
                        >
                          <span
                            ref={compactPreviewTextRef}
                            className="compact-chat-capsule-text"
                            data-compact-preview-streaming={compactPreviewIsStreaming ? 'true' : 'false'}
                            data-compact-preview-scrollable={compactPreviewAllowsScroll ? 'true' : 'false'}
                            onWheel={handleCompactPreviewWheel}
                          >
                            {compactPreviewDisplayContent}
                          </span>
                        </button>
                        {compactInputToolToggleButton}
                      </>
                    )}
                  </div>
                  {composerAttachmentPreviewNode}
                  {compactInputToolFanNode}
                </div>
              ) : null}
            </form>
          </footer>
        ) : null}
      </section>
    </main>
  );
}
