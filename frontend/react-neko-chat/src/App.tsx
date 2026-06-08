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
import FullChatSurface from './FullChatSurface';
import CompactExportHistoryPanel, {
  COMPACT_EXPORT_SELECTION_LIMIT,
  isCompactExportMessageSelectable,
  type CompactExportActionRequest,
  type CompactExportPreviewResult,
} from './CompactExportHistoryPanel';
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
};

type CompactInlineExportBridge = {
  buildCompactInlinePreview?: (request: CompactExportActionRequest) => Promise<CompactExportPreviewResult> | CompactExportPreviewResult;
  copyCompactInlineSelection?: (request: CompactExportActionRequest) => Promise<void> | void;
  downloadCompactInlineSelection?: (request: CompactExportActionRequest) => Promise<void> | void;
};

const defaultMessages: ChatMessage[] = [];
type AvatarToolId = AvatarInteractionPayload['toolId'];

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

function isGuideChatButtonLockActive(): boolean {
  return document.body?.classList.contains('yui-guide-chat-buttons-disabled') === true;
}

const COMPACT_SPEECH_REVEAL_MAX_CHARS_PER_SECOND = 8;
const COMPACT_SPEECH_TURN_MERGE_WINDOW_MS = 12000;
const COMPACT_SPEECH_FALLBACK_REVEAL_DELAY_MS = 700;
const SPEECH_PLAYBACK_STATE_STORAGE_KEY = 'neko_speech_playback_state';
const SPEECH_PLAYBACK_CHANNEL_NAME = 'neko_speech_playback_channel';
const COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY = 'neko.reactChatWindow.compactExportHistoryOpen';
const COMPACT_HISTORY_HEIGHT_STORAGE_KEY = 'neko.reactChatWindow.compactHistorySlotHeight';
const COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY = 'neko.reactChatWindow.compactInputToolWheelIndex';
export const COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS = 560;
const COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT = 7;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD = 22;
const COMPACT_INPUT_TOOL_WHEEL_SCROLL_DEADZONE = 0.5;
const COMPACT_INPUT_TOOL_WHEEL_DRAG_GUARD_MS = 4000;
const COMPACT_INPUT_TOOL_WHEEL_FAST_GESTURE_MS = 140;
const COMPACT_INPUT_TOOL_WHEEL_FAST_ANIMATION_MS = 180;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_START_STEPS = COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT * 4;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_LAP_STEPS = COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT * 2;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_MAX_STEPS = COMPACT_INPUT_TOOL_WHEEL_CHARGE_LAP_STEPS * 2;
const COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_STEP_MS = 36;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_X = 116;
const COMPACT_INPUT_TOOL_WHEEL_CENTER_Y = 116;
// Drag-to-rotate sensitivity scalar (arc = radius * angleDelta), NOT a layout
// value. Kept at 91.92 so the rotate-by-drag feel is unchanged even though the
// visual orbit radius (--compact-tool-wheel-orbit-radius in styles.css) was
// halved — the angle itself is measured from real geometry, this only scales
// how much angular travel counts as one step.
const COMPACT_INPUT_TOOL_WHEEL_ORBIT_RADIUS = 91.92;
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
const COMPACT_SURFACE_RESIZE_MIN_WIDTH = 280;
// compact 对话条默认/初始宽度（无法从 rect/CSS 量到时的回退值）。与 resize 下限解耦：
// 减小最短宽度只动 RESIZE_MIN_WIDTH，默认仍是这个值，保证「默认宽度不变」。
const COMPACT_SURFACE_DEFAULT_WIDTH = 430;
const COMPACT_SURFACE_RESIZE_MOBILE_MIN_WIDTH = 280;
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
  lastHeight: number;
  moved: boolean;
  captureTarget: Element | null;
};

type CompactToolWheelPointerState = {
  id: number;
  x: number;
  y: number;
  angle: number | null;
  angleRemainder: number;
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
  if (typeof window === 'undefined') return true;
  try {
    const persisted = window.localStorage?.getItem(COMPACT_EXPORT_HISTORY_OPEN_STORAGE_KEY);
    return persisted === null ? true : persisted === 'true';
  } catch {
    return true;
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

function normalizeCompactInputToolWheelIndex(value: unknown): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return 0;
  const index = Math.trunc(parsed);
  return ((index % COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT) + COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT) % COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT;
}

function readPersistedCompactInputToolWheelIndex(): number {
  if (typeof window === 'undefined') return 0;
  try {
    return normalizeCompactInputToolWheelIndex(window.localStorage?.getItem(COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY));
  } catch {
    return 0;
  }
}

function persistCompactInputToolWheelIndex(index: number) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage?.setItem(
      COMPACT_INPUT_TOOL_WHEEL_INDEX_STORAGE_KEY,
      String(normalizeCompactInputToolWheelIndex(index)),
    );
  } catch {}
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
      if (index !== latestStreamingAssistantIndex && latestStreamingTurnId && message.turnId !== latestStreamingTurnId) {
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
        isGuide: isGuideMessageId(latestStreamingMessage?.id),
      };
    }
  }

  return null;
}

type ToolIconItem = {
  id: AvatarToolId;
  labelKey: string;
  labelFallback: string;
  iconImagePath: string;
  iconImagePathAlt?: string;
  iconImagePathAlt2?: string;
  menuIconScale?: number;
  menuIconOffsetX?: number;
  menuIconOffsetY?: number;
  menuIconOffsetXAlt?: number;
  menuIconOffsetYAlt?: number;
  menuIconOffsetXAlt2?: number;
  menuIconOffsetYAlt2?: number;
  cursorImagePath: string;
  cursorImagePathAlt?: string;
  cursorImagePathAlt2?: string;
  cursorHotspotX?: number;
  cursorHotspotY?: number;
};

const toolIconItems: ToolIconItem[] = [
  {
    id: 'lollipop',
    labelKey: 'chat.toolLollipop',
    labelFallback: '棒棒糖',
    iconImagePath: '/static/icons/chat_sugar1.png',
    iconImagePathAlt: '/static/icons/chat_sugar2.png',
    iconImagePathAlt2: '/static/icons/chat_sugar3.png',
    cursorImagePath: '/static/icons/chat_sugar1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_sugar2_cursor.png',
    menuIconScale: 1.18,
    cursorHotspotX: 27,
    cursorHotspotY: 46,
  },
  {
    id: 'fist',
    labelKey: 'chat.toolFist',
    labelFallback: '猫爪',
    iconImagePath: '/static/icons/cat_claw1.png',
    iconImagePathAlt: '/static/icons/cat_claw2.png',
    cursorImagePath: '/static/icons/cat_claw1_cursor.png',
    cursorImagePathAlt: '/static/icons/cat_claw2_cursor.png',
    cursorHotspotX: 39,
    cursorHotspotY: 46,
  },
  {
    id: 'hammer',
    labelKey: 'chat.toolHammer',
    labelFallback: '锤子',
    iconImagePath: '/static/icons/chat_hammer1.png',
    iconImagePathAlt: '/static/icons/chat_hammer2.png',
    cursorImagePath: '/static/icons/chat_hammer1_cursor.png',
    cursorImagePathAlt: '/static/icons/chat_hammer2_cursor.png',
    menuIconScale: 1.42,
    menuIconOffsetX: -6,
    menuIconOffsetY: 1,
    menuIconOffsetXAlt: 1,
    menuIconOffsetYAlt: -1,
    cursorHotspotX: 50,
    cursorHotspotY: 54,
  },
];

const hammerToolItem = toolIconItems.find(item => item.id === 'hammer') ?? null;
const hammerOverlayTransformOrigin = {
  x: 60,
  y: 118,
};

const avatarToolSoundPaths = {
  lollipopBite: '/static/sounds/avatar-tools/lollipop-bite.mp3',
  coinDrop: '/static/sounds/avatar-tools/coin-drop.mp3',
  hammerSmall: '/static/sounds/avatar-tools/hammer-small.mp3',
  hammerBig: '/static/sounds/avatar-tools/hammer-big.mp3',
} as const;

function getToolItemLabel(item: ToolIconItem): string {
  return i18n(item.labelKey, item.labelFallback);
}

const avatarToolRangePadding = 100;
const avatarToolRangeHoldMs = 180;
const compactCursorZoneSelector = [
  '.composer-bottom-tools',
  '.composer-tool-menu',
  '.composer-icon-popover',
  '.composer-tool-btn',
  '.composer-icon-button',
  '.compact-input-tool-fan',
  '.compact-input-tool-toggle',
  '.compact-export-history-anchor',
  '.compact-history-visibility-handle',
  '.send-button-circle',
  '.window-topbar-actions',
  '.topbar-action-btn',
  '.message-action-button',
  '#live2d-floating-buttons',
  '#vrm-floating-buttons',
  '#mmd-floating-buttons',
  '#live2d-return-button-container',
  '#vrm-return-button-container',
  '#mmd-return-button-container',
  '#live2d-lock-icon',
  '#vrm-lock-icon',
  '#mmd-lock-icon',
  '.live2d-floating-btn',
  '.vrm-floating-btn',
  '.mmd-floating-btn',
  '.live2d-trigger-btn',
  '.vrm-trigger-btn',
  '.mmd-trigger-btn',
  '.live2d-return-btn',
  '.vrm-return-btn',
  '.mmd-return-btn',
  '.live2d-popup',
  '.vrm-popup',
  '.mmd-popup',
  '[id^="live2d-popup-"]',
  '[id^="vrm-popup-"]',
  '[id^="mmd-popup-"]',
  '[data-neko-sidepanel]',
].join(', ');

type CursorVariant = 'primary' | 'secondary' | 'tertiary';
type ToolCursorVariantState = Record<string, CursorVariant>;
type InteractionIntensity = NonNullable<AvatarInteractionPayload['intensity']>;
type AvatarInteractionToolId = AvatarToolId;
type AvatarTouchZone = 'ear' | 'head' | 'face' | 'body';
type CompactInputToolWheelLayout = 'default' | 'viewport-fit';
type AvatarInteractionPayloadByTool = {
  [K in AvatarInteractionToolId]: Extract<AvatarInteractionPayload, { toolId: K }>;
};

type HostAvatarBounds = {
  left: number;
  right: number;
  top: number;
  bottom: number;
  width: number;
  height: number;
  centerX?: number;
  centerY?: number;
};

type HostAvatarManager = {
  currentModel?: unknown;
  getModelScreenBounds?: () => HostAvatarBounds | null;
};

type AvatarBoundsCacheEntry = {
  bounds: HostAvatarBounds;
};

type AvatarToolCacheState = {
  loadedCursorImageCache: Map<string, Promise<HTMLImageElement>>;
  compactCursorValueCache: Map<string, Promise<string>>;
  avatarBoundsCacheTtlMs: number;
  avatarBoundsCache: {
    expiresAt: number;
    entries: AvatarBoundsCacheEntry[];
  };
};

type AvatarRangeHit = {
  bounds: HostAvatarBounds;
  touchZone: AvatarTouchZone;
};

function normalizeHostAvatarBounds(bounds: unknown): HostAvatarBounds | null {
  if (!bounds || typeof bounds !== 'object') return null;
  const raw = bounds as Partial<HostAvatarBounds>;
  const left = Number(raw.left);
  const top = Number(raw.top);
  const width = Number(raw.width);
  const height = Number(raw.height);
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
  const right = Number.isFinite(Number(raw.right)) ? Number(raw.right) : left + width;
  const bottom = Number.isFinite(Number(raw.bottom)) ? Number(raw.bottom) : top + height;
  return {
    left,
    top,
    right,
    bottom,
    width,
    height,
    centerX: Number.isFinite(Number(raw.centerX)) ? Number(raw.centerX) : left + width / 2,
    centerY: Number.isFinite(Number(raw.centerY)) ? Number(raw.centerY) : top + height / 2,
  };
}

type FloatingHeart = {
  id: number;
  x: number;
  y: number;
  driftX: number;
  driftY: number;
  scale: number;
  delayMs: number;
};

type FloatingFistDrop = {
  id: number;
  x: number;
  y: number;
  driftX: number;
  driftY: number;
  rotation: number;
  scale: number;
  delayMs: number;
};

function resolveToolImagePaths(item: ToolIconItem, variant: CursorVariant) {
  return {
    iconImagePath: variant === 'tertiary' && item.iconImagePathAlt2
      ? item.iconImagePathAlt2
      : variant === 'secondary' && item.iconImagePathAlt
        ? item.iconImagePathAlt
        : item.iconImagePath,
    cursorImagePath: variant === 'tertiary' && item.cursorImagePathAlt2
      ? item.cursorImagePathAlt2
      : variant === 'secondary' && item.cursorImagePathAlt
        ? item.cursorImagePathAlt
        : variant === 'tertiary' && item.cursorImagePathAlt
          ? item.cursorImagePathAlt
          : item.cursorImagePath,
  };
}

function resolveMenuIconVisual(item: ToolIconItem, variant: CursorVariant) {
  const imagePath = variant === 'tertiary' && item.iconImagePathAlt2
    ? item.iconImagePathAlt2
    : variant === 'secondary' && item.iconImagePathAlt
      ? item.iconImagePathAlt
      : item.iconImagePath;
  const offsetX = variant === 'tertiary'
    ? (item.menuIconOffsetXAlt2 ?? item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
    : variant === 'secondary'
      ? (item.menuIconOffsetXAlt ?? item.menuIconOffsetX ?? 0)
      : (item.menuIconOffsetX ?? 0);
  const offsetY = variant === 'tertiary'
    ? (item.menuIconOffsetYAlt2 ?? item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
    : variant === 'secondary'
      ? (item.menuIconOffsetYAlt ?? item.menuIconOffsetY ?? 0)
      : (item.menuIconOffsetY ?? 0);

  return {
    imagePath,
    offsetX,
    offsetY,
  };
}

function loadCursorImage(imagePath: string, cacheState: AvatarToolCacheState): Promise<HTMLImageElement> {
  const cached = cacheState.loadedCursorImageCache.get(imagePath);
  if (cached) return cached;

  const pending = new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.decoding = 'async';
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error(`Failed to load cursor image: ${imagePath}`));
    image.src = imagePath;
  });

  cacheState.loadedCursorImageCache.set(imagePath, pending);
  return pending;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

async function resolveCompactCursorValue(
  item: ToolIconItem,
  variant: CursorVariant,
  cacheState: AvatarToolCacheState,
): Promise<string> {
  const { iconImagePath, cursorImagePath } = resolveToolImagePaths(item, variant);
  const cursorScale = item.menuIconScale ?? 1;
  const cacheKey = [
    iconImagePath,
    cursorImagePath,
    cursorScale,
    item.cursorHotspotX ?? 18,
    item.cursorHotspotY ?? 18,
  ].join('|');

  const cached = cacheState.compactCursorValueCache.get(cacheKey);
  if (cached) return cached;

  const pending = Promise.all([
    loadCursorImage(iconImagePath, cacheState),
    loadCursorImage(cursorImagePath, cacheState),
  ]).then(([iconImage, cursorImage]) => {
    const boxSize = Math.max(32, Math.round(40 * cursorScale));
    const scale = Math.min(boxSize / iconImage.naturalWidth, boxSize / iconImage.naturalHeight);
    const drawWidth = Math.max(1, Math.round(iconImage.naturalWidth * scale));
    const drawHeight = Math.max(1, Math.round(iconImage.naturalHeight * scale));
    const offsetX = Math.round((boxSize - drawWidth) / 2);
    const offsetY = Math.round((boxSize - drawHeight) / 2);

    const canvas = document.createElement('canvas');
    canvas.width = boxSize;
    canvas.height = boxSize;
    const context = canvas.getContext('2d');
    if (!context) {
      return resolveCursorValue(item, variant);
    }

    context.clearRect(0, 0, boxSize, boxSize);
    context.drawImage(iconImage, offsetX, offsetY, drawWidth, drawHeight);

    const hotspotRatioX = (item.cursorHotspotX ?? 18) / Math.max(cursorImage.naturalWidth, 1);
    const hotspotRatioY = (item.cursorHotspotY ?? 18) / Math.max(cursorImage.naturalHeight, 1);
    const hotspotX = clamp(Math.round(offsetX + drawWidth * hotspotRatioX), 0, boxSize - 1);
    const hotspotY = clamp(Math.round(offsetY + drawHeight * hotspotRatioY), 0, boxSize - 1);

    return `url("${canvas.toDataURL('image/png')}") ${hotspotX} ${hotspotY}, auto`;
  }).catch(() => resolveCursorValue(item, variant));

  cacheState.compactCursorValueCache.set(cacheKey, pending);
  return pending;
}

function resolveCursorValue(item: ToolIconItem, variant: CursorVariant): string {
  const { cursorImagePath: imagePath } = resolveToolImagePaths(item, variant);
  const hotspotX = typeof item.cursorHotspotX === 'number' ? item.cursorHotspotX : 18;
  const hotspotY = typeof item.cursorHotspotY === 'number' ? item.cursorHotspotY : 18;
  return `url("${imagePath}") ${hotspotX} ${hotspotY}, auto`;
}

function playAvatarToolSound(soundPath: string) {
  if (typeof Audio === 'undefined') return;
  try {
    const audio = new Audio(soundPath);
    audio.preload = 'auto';
    audio.volume = 0.9;
    const playPromise = audio.play();
    if (playPromise && typeof playPromise.catch === 'function') {
      playPromise.catch(() => {});
    }
  } catch {
    // Ignore autoplay or unsupported-audio failures; the interaction itself should continue.
  }
}

function supportsDesktopFinePointer(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return true;
  }

  try {
    return window.matchMedia('(pointer: fine)').matches;
  } catch {
    return true;
  }
}

function isElectronMultiWindowHost(): boolean {
  return typeof window !== 'undefined'
    && (window as Window & { __NEKO_MULTI_WINDOW__?: boolean }).__NEKO_MULTI_WINDOW__ === true;
}

function clearForcedNativeCursorFallback() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.style.removeProperty('cursor');
  document.body?.style.removeProperty('cursor');
}

function clearGlobalToolCursorState() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.classList.remove('neko-tool-cursor-active');
  root.style.removeProperty('--neko-chat-tool-cursor');
  root.style.setProperty('cursor', 'auto', 'important');
  document.body?.style.setProperty('cursor', 'auto', 'important');
}

function isElementVisible(elementId: string): boolean {
  const element = document.getElementById(elementId);
  if (!element) return false;
  const computedStyle = window.getComputedStyle(element);
  return computedStyle.display !== 'none'
    && computedStyle.visibility !== 'hidden'
    && computedStyle.opacity !== '0'
    && element.getClientRects().length > 0;
}

function isPointInsideAvatarBounds(bounds: HostAvatarBounds, clientX: number, clientY: number): boolean {
  if (
    clientX < bounds.left - avatarToolRangePadding
    || clientX > bounds.right + avatarToolRangePadding
    || clientY < bounds.top - avatarToolRangePadding
    || clientY > bounds.bottom + avatarToolRangePadding
  ) {
    return false;
  }

  const centerX = typeof bounds.centerX === 'number'
    ? bounds.centerX
    : (bounds.left + bounds.right) / 2;
  const centerY = typeof bounds.centerY === 'number'
    ? bounds.centerY
    : (bounds.top + bounds.bottom) / 2;
  const radiusX = bounds.width * 0.3 + avatarToolRangePadding;
  const radiusY = bounds.height * 0.475 + avatarToolRangePadding;
  if (radiusX <= 0 || radiusY <= 0) return false;

  const normalizedX = (clientX - centerX) / radiusX;
  const normalizedY = (clientY - centerY) / radiusY;
  return normalizedX * normalizedX + normalizedY * normalizedY <= 1;
}

function getAvatarBoundsEntries(cacheState: AvatarToolCacheState): AvatarBoundsCacheEntry[] {
  const now = performance.now();
  if (cacheState.avatarBoundsCache.expiresAt <= now) {
    const hostWindow = window as Window & {
      mmdManager?: HostAvatarManager;
      vrmManager?: HostAvatarManager;
      live2dManager?: HostAvatarManager;
      __nekoDesktopAvatarBounds?: HostAvatarBounds | null;
    };
    const desktopAvatarBounds = normalizeHostAvatarBounds(hostWindow.__nekoDesktopAvatarBounds);

    const candidates: Array<{ containerId: string; manager: HostAvatarManager | undefined }> = [
      { containerId: 'mmd-container', manager: hostWindow.mmdManager },
      { containerId: 'vrm-container', manager: hostWindow.vrmManager },
      { containerId: 'live2d-container', manager: hostWindow.live2dManager },
    ];

    cacheState.avatarBoundsCache = {
      expiresAt: now + cacheState.avatarBoundsCacheTtlMs,
      entries: [
        ...(desktopAvatarBounds ? [{ bounds: desktopAvatarBounds }] : []),
        ...candidates.flatMap(({ containerId, manager }) => {
          if (!manager?.currentModel || typeof manager.getModelScreenBounds !== 'function') {
            return [];
          }
          if (!isElementVisible(containerId)) return [];

          try {
            const bounds = manager.getModelScreenBounds();
            return bounds ? [{ bounds }] : [];
          } catch {
            return [];
          }
        }),
      ],
    };
  }

  return cacheState.avatarBoundsCache.entries;
}

function classifyAvatarTouchZone(bounds: HostAvatarBounds, clientX: number, clientY: number): AvatarTouchZone {
  if (bounds.width <= 0 || bounds.height <= 0) {
    return 'body';
  }

  const relativeX = clamp((clientX - bounds.left) / bounds.width, 0, 1);
  const relativeY = clamp((clientY - bounds.top) / bounds.height, 0, 1);

  if (relativeY <= 0.24 && (relativeX <= 0.24 || relativeX >= 0.76)) {
    return 'ear';
  }
  if (relativeY <= 0.34) {
    return 'head';
  }
  if (relativeY <= 0.62) {
    return 'face';
  }
  return 'body';
}

function getAvatarRangeHit(
  clientX: number,
  clientY: number,
  cacheState: AvatarToolCacheState,
): AvatarRangeHit | null {
  const matchedEntry = getAvatarBoundsEntries(cacheState).find(({ bounds }) => (
    isPointInsideAvatarBounds(bounds, clientX, clientY)
  ));
  if (!matchedEntry) {
    return null;
  }
  return {
    bounds: matchedEntry.bounds,
    touchZone: classifyAvatarTouchZone(matchedEntry.bounds, clientX, clientY),
  };
}

function isPointerWithinAvatarRange(
  clientX: number,
  clientY: number,
  cacheState: AvatarToolCacheState,
): boolean {
  return getAvatarRangeHit(clientX, clientY, cacheState) !== null;
}

function clearAvatarBoundsCache(cacheState: AvatarToolCacheState) {
  cacheState.avatarBoundsCache = {
    expiresAt: 0,
    entries: [],
  };
}

function isPointerOverCompactCursorZone(target: EventTarget | null): boolean {
  return target instanceof Element && !!target.closest(compactCursorZoneSelector);
}

function isPointWithinCompactCursorZone(clientX: number, clientY: number): boolean {
  if (typeof document === 'undefined') return false;

  const hitElements = typeof document.elementsFromPoint === 'function'
    ? document.elementsFromPoint(clientX, clientY)
    : (
      typeof document.elementFromPoint === 'function'
        ? [document.elementFromPoint(clientX, clientY)].filter((element): element is Element => element instanceof Element)
        : []
    );

  return hitElements.some(element => !!element.closest(compactCursorZoneSelector));
}

function resolveEffectiveCursorVariant(
  toolId: string | null,
  avatarRangeVariants: ToolCursorVariantState,
  outsideRangeVariants: ToolCursorVariantState,
  isWithinAvatarRange: boolean,
): CursorVariant {
  const avatarRangeVariant = toolId ? (avatarRangeVariants[toolId] ?? 'primary') : 'primary';
  const outsideRangeVariant = toolId ? (outsideRangeVariants[toolId] ?? 'primary') : 'primary';
  if (toolId === 'lollipop') {
    return avatarRangeVariant;
  }
  if (toolId === 'hammer') {
    return isWithinAvatarRange
      ? 'primary'
      : outsideRangeVariant;
  }
  return isWithinAvatarRange ? avatarRangeVariant : outsideRangeVariant;
}

function createDefaultToolCursorVariantState(): ToolCursorVariantState {
  return Object.fromEntries(toolIconItems.map(item => [item.id, 'primary'])) as ToolCursorVariantState;
}

function createAvatarInteractionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `avatar-int-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function sanitizeInteractionTextContext(text: string): string | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  return trimmed.length > 80 ? trimmed.slice(0, 80).trimEnd() : trimmed;
}

/**
 * Top-level dispatcher. The host mounts this as the chat window root. It routes
 * the frozen legacy `full` surface to the self-contained {@link FullChatSurface}
 * and keeps the active `compact`/`minimized` experience on {@link CompactChatApp}.
 * The two are sibling subtrees — only one mounts at a time, so their hooks and
 * state stay fully isolated. Compact work never touches full, and vice versa.
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
  chatSurfaceMode = 'compact',
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
  onCompactChatStateChange,
  onCompactMinimizeRequest,
  rollbackDraft,
  _rollbackKey,
  _toolCursorResetKey,
}: ChatWindowProps) {
  const [draft, setDraft] = useState('');
  const [toolMenuOpen, setToolMenuOpen] = useState(false);
  const [activeCursorToolId, setActiveCursorToolId] = useState<string | null>(null);
  const [avatarRangeCursorVariants, setAvatarRangeCursorVariants] = useState<ToolCursorVariantState>(() => createDefaultToolCursorVariantState());
  const [outsideRangeCursorVariants, setOutsideRangeCursorVariants] = useState<ToolCursorVariantState>(() => createDefaultToolCursorVariantState());
  const [isCursorOverAvatarRange, setIsCursorOverAvatarRange] = useState(false);
  const [isCursorOverCompactCursorZone, setIsCursorOverCompactCursorZone] = useState(false);
  const [isCursorInsideHostWindow, setIsCursorInsideHostWindow] = useState(true);
  const [hammerSwingPhase, setHammerSwingPhase] = useState<'idle' | 'windup' | 'swing' | 'impact' | 'recover'>('idle');
  const [isInnerHammerEasterEggActive, setIsInnerHammerEasterEggActive] = useState(false);
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
  // 工具轮盘原点（toggle / fan 中心）的「按住拖动文本框」手势追踪。与轮盘旋转
  // (compactInputToolWheelPointerRef) 互斥：原点按下时不建立旋转 pointer，旋转路径自然 no-op。
  const compactToolOriginDragRef = useRef<{
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startScreenX: number;
    startScreenY: number;
    moved: boolean;
    captureTarget: Element | null;
  } | null>(null);
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
  const avatarCursorOverlayRef = useRef<HTMLDivElement | null>(null);
  const hammerCursorOverlayRef = useRef<HTMLDivElement | null>(null);
  const hammerSwingTimeoutIdsRef = useRef<number[]>([]);
  const outsideHammerResetTimeoutRef = useRef<number | null>(null);
  const floatingHeartIdRef = useRef(0);
  const floatingHeartTimeoutIdsRef = useRef<number[]>([]);
  const floatingFistDropIdRef = useRef(0);
  const floatingFistDropTimeoutIdsRef = useRef<number[]>([]);
  const interactionBurstHistoryRef = useRef<Record<string, number[]>>({});
  const latestPointerPositionRef = useRef({ x: 0, y: 0 });
  const latestPointerTargetRef = useRef<EventTarget | null>(null);
  const avatarRangeHoldUntilRef = useRef(0);
  const avatarRangeHoldTimerRef = useRef<number | null>(null);
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
  const avatarInteractionCallbackRef = useRef(onAvatarInteraction);
  const avatarToolCacheState = useMemo<AvatarToolCacheState>(() => ({
    loadedCursorImageCache: new Map<string, Promise<HTMLImageElement>>(),
    compactCursorValueCache: new Map<string, Promise<string>>(),
    avatarBoundsCacheTtlMs: 80,
    avatarBoundsCache: {
      expiresAt: 0,
      entries: [],
    },
  }), []);
  const [floatingHearts, setFloatingHearts] = useState<FloatingHeart[]>([]);
  const [floatingFistDrops, setFloatingFistDrops] = useState<FloatingFistDrop[]>([]);
  const [compactPreviewTextVisible, setCompactPreviewTextVisible] = useState('');
  const [compactSpeechVisibleLength, setCompactSpeechVisibleLength] = useState(0);
  const [compactSpeechFallbackRevealActive, setCompactSpeechFallbackRevealActive] = useState(false);
  const [speechPlaybackState, setSpeechPlaybackState] = useState<SpeechPlaybackState | null>(null);
  const [compactCaptionState, setCompactCaptionState] = useState<CompactCaptionState | null>(null);
  const [compactAssistantStreamingGap, setCompactAssistantStreamingGap] = useState<{
    turnId: string;
    acceptStreaming: boolean;
  } | null>(null);
  const [compactChoiceLayerPlacement, setCompactChoiceLayerPlacement] = useState<'above' | 'below'>('above');
  const [compactInputToolFanOpen, setCompactInputToolFanOpen] = useState(false);
  const [compactInputToolFanInteractive, setCompactInputToolFanInteractive] = useState(false);
  const [compactInputToolWheelLayout, setCompactInputToolWheelLayout] = useState<CompactInputToolWheelLayout>('default');
  const [compactInputToolWheelIndex, setCompactInputToolWheelIndex] = useState(readPersistedCompactInputToolWheelIndex);
  const [compactInputToolWheelFastAnimation, setCompactInputToolWheelFastAnimation] = useState(false);
  const [compactInputToolWheelChargeRatio, setCompactInputToolWheelChargeRatio] = useState(0);
  const [compactInputToolWheelChargeDirection, setCompactInputToolWheelChargeDirection] = useState<1 | -1 | null>(null);
  const [compactInputToolWheelChargeReleaseActive, setCompactInputToolWheelChargeReleaseActive] = useState(false);
  const [compactSurfaceResizeWidth, setCompactSurfaceResizeWidth] = useState<number | null>(null);
  const [compactHistorySlotHeight, setCompactHistorySlotHeight] = useState<number | null>(readPersistedCompactHistorySlotHeight);
  const [compactHistoryResizeActive, setCompactHistoryResizeActive] = useState(false);
  const [compactExportHistoryOpen, setCompactExportHistoryOpen] = useState(readPersistedCompactExportHistoryOpen);
  const [compactExportHistoryMounted, setCompactExportHistoryMounted] = useState(readPersistedCompactExportHistoryOpen);
  const [compactExportHistoryClosingMessages, setCompactExportHistoryClosingMessages] = useState<ChatMessage[] | null>(null);
  const [compactExportControlsOpen, setCompactExportControlsOpen] = useState(false);
  const [compactExportPreviewOpen, setCompactExportPreviewOpen] = useState(false);
  const [compactExportSelectedIds, setCompactExportSelectedIds] = useState<Set<string>>(() => new Set());
  const [compactExportAutoScrollToBottom, setCompactExportAutoScrollToBottom] = useState(true);
  const compactSurfaceResizeStateRef = useRef<CompactSurfaceResizeState | null>(null);
  const compactHistoryResizeStateRef = useRef<CompactHistoryResizeState | null>(null);
  const compactHistoryVisibilitySuppressClickRef = useRef(false);
  const compactExportHistoryUnmountTimerRef = useRef<number | null>(null);
  const submittingRef = useRef(false);
  const lastRollbackKeyRef = useRef('');
  const lastToolCursorResetKeyRef = useRef('');
  const compactInputHasPayload = draft.trim().length > 0 || composerAttachments.length > 0;
  const canSubmit = !composerDisabled && compactInputHasPayload;
  const clearActiveCursorToolSelection = useCallback(() => {
    clearGlobalToolCursorState();
    latestPointerTargetRef.current = null;
    avatarRangeHoldUntilRef.current = 0;
    if (avatarRangeHoldTimerRef.current !== null) {
      window.clearTimeout(avatarRangeHoldTimerRef.current);
      avatarRangeHoldTimerRef.current = null;
    }
    setActiveCursorToolId(null);
    setToolMenuOpen(false);
    setIsCursorOverAvatarRange(false);
    setIsCursorOverCompactCursorZone(false);
  }, []);
  const setCursorOverAvatarRange = useCallback((nextValue: boolean, options?: { allowHold?: boolean }) => {
    if (avatarRangeHoldTimerRef.current !== null) {
      window.clearTimeout(avatarRangeHoldTimerRef.current);
      avatarRangeHoldTimerRef.current = null;
    }

    if (nextValue) {
      const holdUntil = performance.now() + avatarToolRangeHoldMs;
      avatarRangeHoldUntilRef.current = holdUntil;
      setIsCursorOverAvatarRange(previousValue => (
        previousValue === true ? previousValue : true
      ));
      return;
    }

    setIsCursorOverAvatarRange(previousValue => {
      const shouldHold = options?.allowHold !== false
        && previousValue
        && performance.now() <= avatarRangeHoldUntilRef.current;
      if (shouldHold) {
        if (avatarRangeHoldTimerRef.current === null) {
          const delay = Math.max(0, avatarRangeHoldUntilRef.current - performance.now());
          avatarRangeHoldTimerRef.current = window.setTimeout(() => {
            avatarRangeHoldTimerRef.current = null;
            if (performance.now() < avatarRangeHoldUntilRef.current) return;
            avatarRangeHoldUntilRef.current = 0;
            setIsCursorOverAvatarRange(currentValue => (currentValue ? false : currentValue));
          }, delay);
        }
        return true;
      }
      if (avatarRangeHoldTimerRef.current !== null) {
        window.clearTimeout(avatarRangeHoldTimerRef.current);
        avatarRangeHoldTimerRef.current = null;
      }
      if (avatarRangeHoldUntilRef.current !== 0) {
        avatarRangeHoldUntilRef.current = 0;
      }
      return previousValue ? false : previousValue;
    });
  }, []);

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
    if (_toolCursorResetKey && _toolCursorResetKey !== lastToolCursorResetKeyRef.current) {
      lastToolCursorResetKeyRef.current = _toolCursorResetKey;
      clearActiveCursorToolSelection();
    }
  }, [_toolCursorResetKey, clearActiveCursorToolSelection]);

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
  const compactExportControlsButtonLabel = compactExportControlsVisible
    ? i18n('chat.compactHistoryControlsHide', 'Hide history actions')
    : i18n('chat.compactHistoryControlsShow', 'Show history actions');
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
  const openCompactExportHistory = useCallback(() => {
    clearCompactExportHistoryUnmountTimer();
    setCompactExportHistoryClosingMessages(null);
    setCompactExportHistoryMounted(true);
    setCompactExportHistoryOpen(true);
    persistCompactExportHistoryOpen(true);
    setCompactExportAutoScrollToBottom(true);
  }, [clearCompactExportHistoryUnmountTimer]);
  const closeCompactExportHistory = useCallback(() => {
    clearCompactExportHistoryUnmountTimer();
    setCompactExportHistoryClosingMessages(messages);
    setCompactExportHistoryOpen(false);
    persistCompactExportHistoryOpen(false);
    setCompactExportPreviewOpen(false);
    compactExportHistoryUnmountTimerRef.current = window.setTimeout(() => {
      setCompactExportHistoryMounted(false);
      setCompactExportHistoryClosingMessages(null);
      compactExportHistoryUnmountTimerRef.current = null;
    }, COMPACT_EXPORT_HISTORY_VISIBILITY_ANIMATION_MS);
  }, [clearCompactExportHistoryUnmountTimer, messages]);
  useEffect(() => () => {
    clearCompactExportHistoryUnmountTimer();
  }, [clearCompactExportHistoryUnmountTimer]);
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
  const compactPreviewText = compactSuppressAssistantFallback
    ? ''
    : compactSpeechModeActive
      ? (
        compactMessagePreview?.isStreaming
          ? compactMessagePreview?.fullText || ''
          : compactSpeechPreservedText || compactMessagePreview?.fullText || ''
      )
      : compactMessagePreview?.text
      || i18n('chat.emptyState', 'Chat content will appear here.');
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
  const emojiButtonAriaLabel = i18n('chat.emojiButtonAriaLabel', 'Emoji');
  const toolIconsAriaLabel = i18n('chat.toolIconsAriaLabel', 'Tool icons');
  const clearCursorToolAriaLabel = i18n('chat.clearCursorToolAriaLabel', '恢复鼠标');
  const overflowMenuAriaLabel = i18n('chat.composerOverflowMenu', '更多工具');
  const effectiveCursorVariant = resolveEffectiveCursorVariant(
    activeCursorToolId,
    avatarRangeCursorVariants,
    outsideRangeCursorVariants,
    isCursorOverAvatarRange,
  );
  const avatarRangeCursorVariant = activeCursorToolId
    ? (avatarRangeCursorVariants[activeCursorToolId] ?? 'primary')
    : 'primary';
  const activeToolItem = toolIconItems.find(item => item.id === activeCursorToolId) ?? null;
  const activeToolImagePaths = activeToolItem
    ? resolveToolImagePaths(activeToolItem, avatarRangeCursorVariant)
    : null;
  const isElectronMultiWindow = isElectronMultiWindowHost();
  const shouldUseLocalDesktopCursorOverlay = !!activeToolItem
    && supportsDesktopFinePointer()
    && !isElectronMultiWindow;
  const shouldRenderLocalDesktopCursorOverlay = shouldUseLocalDesktopCursorOverlay
    && isCursorInsideHostWindow;
  const shouldRenderAvatarRangeOverlay = isCursorOverAvatarRange && !isCursorOverCompactCursorZone;
  const avatarCursorOverlayActive = !!activeToolItem
    && activeCursorToolId !== 'hammer'
    && shouldRenderLocalDesktopCursorOverlay;
  const avatarCursorOverlayCompact = avatarCursorOverlayActive && !shouldRenderAvatarRangeOverlay;
  const hammerCursorOverlayActive = activeCursorToolId === 'hammer' && shouldRenderLocalDesktopCursorOverlay;
  const hammerCursorOverlayCompact = hammerCursorOverlayActive && !shouldRenderAvatarRangeOverlay;
  const hammerCursorOverlayMotionActive = hammerSwingPhase !== 'idle';
  const hammerCompactImagePaths = hammerToolItem
    ? resolveToolImagePaths(hammerToolItem, effectiveCursorVariant)
    : null;
  const hammerCursorOverlayUsesCompactImage = hammerCursorOverlayCompact && !hammerCursorOverlayMotionActive;
  const avatarCursorOverlayImagePath = activeToolItem && activeCursorToolId !== 'hammer'
    ? (
      avatarCursorOverlayCompact
        ? (activeToolImagePaths?.cursorImagePath ?? '')
        : (activeToolImagePaths?.iconImagePath ?? '')
    )
    : '';
  const hammerCursorOverlayCompactImagePath = hammerCursorOverlayUsesCompactImage
    ? (hammerCompactImagePaths?.cursorImagePath ?? '')
    : '';
  const hammerCursorOverlayPrimaryImagePath = hammerToolItem
    ? resolveToolImagePaths(hammerToolItem, 'primary').iconImagePath
    : '';
  const hammerCursorOverlaySecondaryImagePath = hammerToolItem
    ? resolveToolImagePaths(hammerToolItem, 'secondary').iconImagePath
    : '';
  const activeToolMenuVisual = activeToolItem
    ? resolveMenuIconVisual(activeToolItem, effectiveCursorVariant)
    : null;
  const activeToolLabel = activeToolItem ? getToolItemLabel(activeToolItem) : '';
  const selectedEmojiButtonAriaLabel = activeToolItem
    ? `${emojiButtonAriaLabel}: ${activeToolLabel}`
    : emojiButtonAriaLabel;
  const isCursorWithinAvatarToolRange = isCursorInsideHostWindow
    && isCursorOverAvatarRange
    && !isCursorOverCompactCursorZone;
  const avatarToolImageKind = activeToolItem
    ? (isCursorWithinAvatarToolRange ? 'icon' : 'cursor')
    : 'cursor';

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

    const getDesktopPlacementSpace = (shellRect: DOMRect) => {
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

      const surfaceTop = Number(layout?.surface?.top);
      const surfaceHeight = Number(layout?.surface?.height);
      const surfaceScreenTop = windowY + (Number.isFinite(surfaceTop) ? surfaceTop : shellRect.top);
      const surfaceScreenBottom = surfaceScreenTop
        + (Number.isFinite(surfaceHeight) && surfaceHeight > 0 ? surfaceHeight : shellRect.height);
      const workAreaBottom = workAreaY + workAreaHeight;
      return {
        availableAbove: Math.max(0, surfaceScreenTop - workAreaY),
        availableBelow: Math.max(0, workAreaBottom - surfaceScreenBottom),
      };
    };

    const updatePlacement = () => {
      const nextShellNode = compactInputShellRef.current;
      const nextLayerNode = compactChoiceLayerRef.current;
      if (!nextShellNode || !nextLayerNode) return;

      const desktopForcedPlacement = ((window as typeof window & {
        __nekoDesktopCompactLayout?: DesktopCompactChoicePlacementLayout | null;
      }).__nekoDesktopCompactLayout?.compactChoicePlacement);
      if (desktopForcedPlacement === 'above' || desktopForcedPlacement === 'below') {
        setCompactChoiceLayerPlacement(current => (current === desktopForcedPlacement ? current : desktopForcedPlacement));
        return;
      }
      const shellRect = nextShellNode.getBoundingClientRect();
      const layerRect = nextLayerNode.getBoundingClientRect();
      const layerHeight = Math.max(layerRect.height, nextLayerNode.scrollHeight);
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const desktopSpace = getDesktopPlacementSpace(shellRect);
      const availableBelow = desktopSpace?.availableBelow ?? Math.max(0, viewportHeight - shellRect.bottom);
      const availableAbove = desktopSpace?.availableAbove ?? Math.max(0, shellRect.top);
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

    const schedulePlacementUpdate = () => {
      if (frameId !== null) {
        window.cancelAnimationFrame(frameId);
      }
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        updatePlacement();
      });
    };

    schedulePlacementUpdate();

    const visualViewport = window.visualViewport;
    window.addEventListener('resize', schedulePlacementUpdate);
    window.addEventListener('neko:compact-surface-layout-change', schedulePlacementUpdate);
    window.addEventListener('neko:desktop-compact-layout-change', schedulePlacementUpdate);
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
      window.removeEventListener('neko:desktop-compact-layout-change', schedulePlacementUpdate);
      visualViewport?.removeEventListener('resize', schedulePlacementUpdate);
      visualViewport?.removeEventListener('scroll', schedulePlacementUpdate);
      observer?.disconnect();
    };
  }, [compactChoiceLayerOpen, galgameOptions.length, galgameOptionsLoading, isCompactSurface, choicePrompt]);

  const requestCompactChatState = useCallback((nextState: CompactChatState) => {
    if (!isCompactSurface) return;
    if (!isCompactChatStateControlled) {
      setUncontrolledCompactChatState(nextState);
    }
    onCompactChatStateChange?.(nextState);
  }, [isCompactSurface, isCompactChatStateControlled, onCompactChatStateChange]);

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
      detail: { side, width, phase, screenRect },
    }));
  }, []);

  const finishCompactSurfaceResize = useCallback((event?: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactSurfaceResizeStateRef.current;
    if (!resizeState) return;
    if (event && resizeState.pointerId !== event.pointerId) return;
    dispatchCompactSurfaceResizeRequest(resizeState.side, resizeState.lastWidth, 'end');
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
    const startWidth = compactSurfaceEffectiveWidth ?? getCurrentCompactSurfaceWidth();
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
    const anchorLeftScreen = Number.isFinite(desktopSurface?.left)
      ? Number(desktopSurface?.left)
      : (shellRect ? window.screenX + shellRect.left : 0);
    const anchorRightScreen = Number.isFinite(desktopSurface?.right)
      ? Number(desktopSurface?.right)
      : anchorLeftScreen + startWidth;
    const anchorTopScreen = Number.isFinite(desktopSurface?.top)
      ? Number(desktopSurface?.top)
      : (shellRect ? window.screenY + shellRect.top : 0);
    const surfaceHeight = Number.isFinite(desktopSurface?.height) && Number(desktopSurface?.height) > 0
      ? Number(desktopSurface?.height)
      : Math.max(1, shellRect?.height ?? 58);
    compactSurfaceResizeStateRef.current = {
      pointerId: event.pointerId,
      side,
      startPointerX: getCompactSurfaceResizePointerX(event),
      startWidth,
      lastWidth: startWidth,
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
  }, [applyCompactSurfaceResizeWidthVar, compactSurfaceEffectiveWidth, dispatchCompactSurfaceResizeRequest, getCurrentCompactSurfaceWidth, isCompactSurface]);

  const handleCompactSurfaceResizePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactSurfaceResizeStateRef.current;
    if (!resizeState || resizeState.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    const deltaX = getCompactSurfaceResizePointerX(event) - resizeState.startPointerX;
    const signedDelta = resizeState.side === 'right' ? deltaX : -deltaX;
    const nextWidth = getClampedCompactSurfaceResizeWidthForSide(
      resizeState.side,
      resizeState.startWidth + signedDelta,
      resizeState,
    );
    resizeState.lastWidth = nextWidth;
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

  const finishCompactHistoryResize = useCallback((event?: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactHistoryResizeStateRef.current;
    if (!resizeState) return;
    if (event && resizeState.pointerId !== event.pointerId) return;
    // 只在真正拖动过才落库：纯点击不该把响应式默认高度锁成固定像素值（否则之后视口/宽度变化不再响应）。
    if (resizeState.moved) {
      persistCompactHistorySlotHeight(resizeState.lastHeight);
      setCompactHistorySlotHeight(resizeState.lastHeight);
    }
    const captureTarget = resizeState.captureTarget;
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(resizeState.pointerId)) {
          captureTarget.releasePointerCapture(resizeState.pointerId);
        }
      } catch (_) {}
    }
    compactHistoryResizeStateRef.current = null;
    setCompactHistoryResizeActive(false);
  }, []);

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
      lastHeight: getClampedCompactHistorySlotHeight(startHeight),
      moved: false,
      captureTarget: event.currentTarget,
    };
    setCompactHistoryResizeActive(true);
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  }, [getClampedCompactHistorySlotHeight, getCompactHistoryStartHeight, isCompactSurface]);

  const handleCompactHistoryResizePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const resizeState = compactHistoryResizeStateRef.current;
    if (!resizeState || resizeState.pointerId !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    // bar 在堆砌区顶部：上拖（deltaY < 0）增高，下拖减高。
    const deltaY = getCompactHistoryResizePointerY(event) - resizeState.startPointerY;
    if (deltaY !== 0) resizeState.moved = true;
    const nextHeight = getClampedCompactHistorySlotHeight(resizeState.startHeight - deltaY);
    resizeState.lastHeight = nextHeight;
    setCompactHistorySlotHeight(nextHeight);
    applyCompactHistorySlotHeightVar(nextHeight);
  }, [applyCompactHistorySlotHeightVar, getClampedCompactHistorySlotHeight]);

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
  }, []);

  const resetCompactInputToolWheelCharge = useCallback(() => {
    compactInputToolWheelChargeRef.current = createCompactToolWheelChargeState();
    setCompactInputToolWheelChargeRatio(0);
    setCompactInputToolWheelChargeDirection(null);
  }, []);

  const dispatchCompactToolWheelDragState = useCallback((active: boolean, pointerId?: number) => {
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

  const resolveCompactInputToolWheelLayout = useCallback((): CompactInputToolWheelLayout => {
    if (!window.matchMedia?.('(max-width: 820px)').matches) return 'default';
    const fanElement = compactInputToolFanRef.current;
    const fanRect = fanElement?.getBoundingClientRect();
    if (!fanElement || !fanRect || fanRect.width <= 0 || fanRect.height <= 0) return 'default';

    const visualViewport = window.visualViewport;
    const viewportLeft = visualViewport?.offsetLeft ?? 0;
    const viewportTop = visualViewport?.offsetTop ?? 0;
    const viewportWidth = visualViewport?.width ?? window.innerWidth;
    const viewportHeight = visualViewport?.height ?? window.innerHeight;
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
    const minX = viewportLeft + COMPACT_INPUT_TOOL_WHEEL_VIEWPORT_MARGIN;
    const minY = viewportTop + COMPACT_INPUT_TOOL_WHEEL_VIEWPORT_MARGIN;
    const maxX = viewportLeft + viewportWidth - COMPACT_INPUT_TOOL_WHEEL_VIEWPORT_MARGIN;
    const maxY = viewportTop + viewportHeight - COMPACT_INPUT_TOOL_WHEEL_VIEWPORT_MARGIN;

    const wheelLayoutFitsViewport = (slots: ReadonlyArray<{ angleDeg: number; scale: number }>) => slots.every(({ angleDeg, scale }) => {
      const angle = angleDeg * (Math.PI / 180);
      const itemCenterX = centerX + (Math.cos(angle) * orbitRadius);
      const itemCenterY = centerY + (Math.sin(angle) * orbitRadius);
      const halfSize = (buttonSize * scale) / 2;
      return itemCenterX - halfSize >= minX
        && itemCenterX + halfSize <= maxX
        && itemCenterY - halfSize >= minY
        && itemCenterY + halfSize <= maxY;
    });

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
    dispatchCompactToolWheelDragState(false);
    compactInputToolWheelLastRotationAtRef.current = 0;
    resetCompactInputToolWheelCharge();
    setCompactInputToolWheelFastAnimation(false);
    setCompactInputToolWheelLayout('default');
    setCompactInputToolFanInteractiveState(false);
    compactInputToolFanPositionSyncRef.current?.();
    compactInputToolFanOpenRef.current = false;
    setCompactInputToolFanOpen(false);
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
    activeCursorToolId,
    compactInputToolFanInteractive,
    compactInputToolFanOpen,
    compactInputToolWheelIndex,
    effectiveCompactChatState,
    isCompactSurface,
    toolMenuOpen,
  ]);

  useEffect(() => {
    persistCompactInputToolWheelIndex(compactInputToolWheelIndex);
  }, [compactInputToolWheelIndex]);

  useLayoutEffect(() => {
    if (!compactInputToolFanOpen) {
      setCompactInputToolWheelLayout('default');
      return undefined;
    }

    syncCompactInputToolWheelLayout();
    const frameId = window.requestAnimationFrame(syncCompactInputToolWheelLayout);
    window.addEventListener('resize', syncCompactInputToolWheelLayout);
    window.addEventListener('neko:compact-interaction-geometry-change', syncCompactInputToolWheelLayout);
    window.visualViewport?.addEventListener('resize', syncCompactInputToolWheelLayout);
    window.visualViewport?.addEventListener('scroll', syncCompactInputToolWheelLayout);

    return () => {
      window.cancelAnimationFrame(frameId);
      window.removeEventListener('resize', syncCompactInputToolWheelLayout);
      window.removeEventListener('neko:compact-interaction-geometry-change', syncCompactInputToolWheelLayout);
      window.visualViewport?.removeEventListener('resize', syncCompactInputToolWheelLayout);
      window.visualViewport?.removeEventListener('scroll', syncCompactInputToolWheelLayout);
    };
  }, [
    compactInputToolFanOpen,
    compactSurfaceResizeWidth,
    effectiveCompactChatState,
    syncCompactInputToolWheelLayout,
  ]);

  const openCompactInputToolFan = useCallback((intent: 'click' | 'hover') => {
    if (composerDisabled || compactInputHasPayload) return;
    // 展开时延续上次轮盘中心索引；hover 抖动重入或重新打开都不应把用户刚滚到的位置弹回默认位。
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
    return pointerType === 'mouse';
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
  }, [closeCompactInputToolFan]);

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
    }
  }, [markCompactInputToolWheelMotion, rotateCompactInputToolWheel]);

  const recordCompactInputToolWheelCharge = useCallback((direction: 1 | -1, stepCount: number) => {
    if (stepCount <= 0) return;
    const previous = compactInputToolWheelChargeRef.current;
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
  }, []);

  const startCompactInputToolWheelChargeRelease = useCallback((direction: 1 | -1, stepCount: number) => {
    const releaseSteps = Math.max(0, Math.round(stepCount));
    clearCompactInputToolWheelChargeReleaseTimer();
    if (releaseSteps <= 0) return;

    let remainingSteps = releaseSteps;
    compactInputToolWheelChargeReleaseActiveRef.current = true;
    setCompactInputToolWheelChargeReleaseActive(true);

    const runReleaseStep = () => {
      if (!compactInputToolFanOpenRef.current || remainingSteps <= 0) {
        compactInputToolWheelChargeReleaseTimerRef.current = null;
        compactInputToolWheelChargeReleaseActiveRef.current = false;
        setCompactInputToolWheelChargeReleaseActive(false);
        return;
      }

      rotateCompactInputToolWheelSteps(direction, 1, { forceFast: true });
      remainingSteps -= 1;
      if (remainingSteps <= 0) {
        compactInputToolWheelChargeReleaseTimerRef.current = null;
        compactInputToolWheelChargeReleaseActiveRef.current = false;
        setCompactInputToolWheelChargeReleaseActive(false);
        return;
      }
      compactInputToolWheelChargeReleaseTimerRef.current = window.setTimeout(
        runReleaseStep,
        COMPACT_INPUT_TOOL_WHEEL_CHARGE_RELEASE_STEP_MS,
      );
    };

    compactInputToolWheelChargeReleaseTimerRef.current = window.setTimeout(runReleaseStep, 0);
  }, [clearCompactInputToolWheelChargeReleaseTimer, rotateCompactInputToolWheelSteps]);

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

  const rotateCompactInputToolWheelByScroll = useCallback((event: ReactWheelEvent<HTMLDivElement>) => {
    const normalizedDelta = getCompactInputToolWheelNormalizedDelta(event);
    if (Math.abs(normalizedDelta) < COMPACT_INPUT_TOOL_WHEEL_SCROLL_DEADZONE) return;

    event.preventDefault();
    event.stopPropagation();

    const direction: 1 | -1 = normalizedDelta > 0 ? 1 : -1;
    rotateCompactInputToolWheelSteps(direction, 1, { forceFast: true });
  }, [getCompactInputToolWheelNormalizedDelta, rotateCompactInputToolWheelSteps]);

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

  // ── 工具轮盘原点「按住拖动文本框」手势 ────────────────────────────────────
  // 在 toggle（轮盘关闭时）或 fan 中心（轮盘展开时，命中原点）按下后移动超阈值 → 把 surface
  // 拖拽交给宿主（web: app-react-chat-window.js / Electron: preload-chat-react.js）经
  // neko:compact-surface-drag-grab 接管。
  // 点按（无移动）语义保持原样：toggle 由自身 onClick 展开/关闭；fan 原点由 onPointerDownCapture
  // 的 markCompactToolFanOriginClickSuppressed 收起（这条收起+抑制路径不动，保证既有命中测试不回归）。
  // 用独立的 compactToolOriginSuppressClickRef 抑制拖动后补发的 click——不能复用
  // compactInputToolWheelSuppressClickRef，因为关闭轮盘的 effect 会把它清掉（见下方 fan 关闭 effect）。
  const beginCompactToolOriginDrag = useCallback((event: ReactPointerEvent) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    // 每次新的原点按下都清掉可能残留的抑制标志（上一次拖拽若没补发 click 会留下 true），
    // 保证本次点按/拖拽自洁——抑制只靠「拖动置位 + click 消费 / 下次按下清零」，不再用定时器。
    compactToolOriginSuppressClickRef.current = false;
    const previous = compactToolOriginDragRef.current;
    if (previous && previous.captureTarget && previous.captureTarget.hasPointerCapture?.(previous.pointerId)) {
      // 兜底：上一手势没收到 pointerup（罕见）→ 释放旧捕获再重置，避免卡死。
      try { previous.captureTarget.releasePointerCapture(previous.pointerId); } catch (_) {}
    }
    const captureTarget = event.currentTarget instanceof Element ? event.currentTarget : null;
    compactToolOriginDragRef.current = {
      pointerId: event.pointerId,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startScreenX: event.screenX,
      startScreenY: event.screenY,
      moved: false,
      captureTarget,
    };
    try {
      captureTarget?.setPointerCapture?.(event.pointerId);
    } catch (_) {}
  }, []);

  const updateCompactToolOriginDrag = useCallback((event: ReactPointerEvent) => {
    const state = compactToolOriginDragRef.current;
    if (!state || state.pointerId !== event.pointerId || state.moved) return;
    const dx = event.clientX - state.startClientX;
    const dy = event.clientY - state.startClientY;
    if (Math.hypot(dx, dy) < COMPACT_INPUT_TOOL_ORIGIN_DRAG_THRESHOLD) return;
    state.moved = true;
    // 吞掉本次指针序列随后补发的 click，避免拖完误触发 toggle 展开 / 工具按钮。
    // 一直 armed 到那次 click 被消费（或下次原点/轮盘按下清零）——不能用定时器，慢速拖拽
    // 往往远超任何固定时长，定时器会在 release click 之前清掉、导致拖完轮盘被误开关。
    compactToolOriginSuppressClickRef.current = true;
    // 拖动是「移动文本框」手势而非工具手势，收起轮盘。
    closeCompactInputToolFan();
    // 把 surface 拖拽交给宿主，锚点用按下点（而非当前点），避免 surface 跳变。
    window.dispatchEvent(new CustomEvent('neko:compact-surface-drag-grab', {
      detail: {
        clientX: state.startClientX,
        clientY: state.startClientY,
        screenX: state.startScreenX,
        screenY: state.startScreenY,
      },
    }));
  }, [closeCompactInputToolFan]);

  const endCompactToolOriginDrag = useCallback((event: ReactPointerEvent) => {
    const state = compactToolOriginDragRef.current;
    if (!state || state.pointerId !== event.pointerId) return;
    const captureTarget = state.captureTarget;
    compactToolOriginDragRef.current = null;
    if (captureTarget && typeof captureTarget.releasePointerCapture === 'function') {
      try {
        if (captureTarget.hasPointerCapture?.(event.pointerId)) {
          captureTarget.releasePointerCapture(event.pointerId);
        }
      } catch (_) {}
    }
    // 无移动 = 点按：toggle 交给自身 onClick；fan 原点已由 onPointerDownCapture 收起。这里不再处理。
  }, []);

  useEffect(() => () => {
    clearCompactInputToolFanCloseTimer();
    clearCompactInputToolFanInteractiveTimer();
    clearCompactInputToolWheelDragGuardTimer();
    clearCompactInputToolWheelFastAnimationTimer();
    clearCompactInputToolWheelChargeReleaseTimer();
  }, [
    clearCompactInputToolFanCloseTimer,
    clearCompactInputToolFanInteractiveTimer,
    clearCompactInputToolWheelDragGuardTimer,
    clearCompactInputToolWheelFastAnimationTimer,
    clearCompactInputToolWheelChargeReleaseTimer,
  ]);

  useEffect(() => {
    if (!isCompactSurface || effectiveCompactChatState !== 'input') {
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
    composerDisabled,
    effectiveCompactChatState,
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
    const chargeState = compactInputToolWheelChargeRef.current;
    const releaseDirection = chargeState.direction === null
      ? null
      : (chargeState.direction === 1 ? -1 : 1);
    const releaseSteps = chargeState.chargeSteps;
    resetCompactInputToolWheelCharge();
    clearCompactInputToolWheelDragGuardTimer();
    compactInputToolWheelPointerRef.current = null;
    compactInputToolWheelDragActiveRef.current = false;
    dispatchCompactToolWheelDragState(false, pointerState.id);
    if (releaseDirection !== null && releaseSteps > 0) {
      startCompactInputToolWheelChargeRelease(releaseDirection, releaseSteps);
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
      const angleDelta = normalizeCompactToolWheelAngleDelta(dragPoint.angle - pointerState.angle);
      const arcDelta = angleDelta * COMPACT_INPUT_TOOL_WHEEL_ORBIT_RADIUS;
      const totalDelta = pointerState.angleRemainder + arcDelta;
      const stepCount = Math.floor(Math.abs(totalDelta) / COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD);
      pointerState.x = dragPoint.x;
      pointerState.y = dragPoint.y;
      pointerState.angle = dragPoint.angle;
      if (stepCount <= 0) {
        pointerState.angleRemainder = totalDelta;
        return true;
      }

      input.preventDefault?.();
      const direction: 1 | -1 = totalDelta > 0 ? 1 : -1;
      rotateCompactInputToolWheelSteps(direction, stepCount);
      recordCompactInputToolWheelCharge(direction, stepCount);
      pointerState.angleRemainder = totalDelta - (
        direction * stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD
      );
      pointerState.didRotate = true;
      return true;
    }

    const deltaX = dragPoint.x - pointerState.x;
    const deltaY = dragPoint.y - pointerState.y;
    const useVerticalDelta = Math.abs(deltaY) >= Math.abs(deltaX);
    const primaryDelta = useVerticalDelta ? deltaY : deltaX;
    const stepCount = Math.floor(Math.abs(primaryDelta) / COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD);
    if (stepCount <= 0) return true;

    input.preventDefault?.();
    const direction: 1 | -1 = useVerticalDelta
      ? (primaryDelta > 0 ? 1 : -1)
      : (primaryDelta < 0 ? 1 : -1);
    rotateCompactInputToolWheelSteps(direction, stepCount);
    recordCompactInputToolWheelCharge(direction, stepCount);
    if (useVerticalDelta) {
      pointerState.x = dragPoint.x;
      pointerState.y += direction === 1
        ? stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD
        : -(stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD);
    } else {
      pointerState.x += direction === 1
        ? -(stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD)
        : stepCount * COMPACT_INPUT_TOOL_WHEEL_DRAG_THRESHOLD;
      pointerState.y = dragPoint.y;
    }
    pointerState.angle = dragPoint.angle;
    pointerState.angleRemainder = 0;
    pointerState.didRotate = true;
    return true;
  }, [
    dispatchCompactToolWheelDragState,
    finishCompactToolWheelPointer,
    getCompactToolWheelBoundedDragPoint,
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
    if (!isCompactSurface || composerHidden || composerDisabled || compactInputHasPayload) {
      closeCompactInputToolFan();
    }
  }, [
    closeCompactInputToolFan,
    compactInputHasPayload,
    compactInputToolFanOpen,
    composerDisabled,
    composerHidden,
    isCompactSurface,
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
    compactInputToolWheelLastRotationAtRef.current = 0;
    resetCompactInputToolWheelCharge();
    setCompactInputToolWheelFastAnimation(false);
    dispatchCompactToolWheelDragState(false);
  }, [
    clearCompactInputToolFanCloseTimer,
    clearCompactInputToolWheelDragGuardTimer,
    clearCompactInputToolWheelFastAnimationTimer,
    clearCompactInputToolWheelChargeReleaseTimer,
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
    avatarInteractionCallbackRef.current = onAvatarInteraction;
  }, [onAvatarInteraction]);

  useEffect(() => {
    if (!onAvatarToolStateChange) return;

    const outsideRangeVariant = activeCursorToolId
      ? (outsideRangeCursorVariants[activeCursorToolId] ?? 'primary')
      : 'primary';
    const textContext = sanitizeInteractionTextContext(draft);

    onAvatarToolStateChange({
      active: !!activeToolItem,
      toolId: activeToolItem?.id ?? null,
      variant: effectiveCursorVariant,
      avatarRangeVariant: avatarRangeCursorVariant,
      outsideRangeVariant,
      imageKind: avatarToolImageKind,
      withinAvatarRange: isCursorWithinAvatarToolRange,
      overCompactZone: isCursorOverCompactCursorZone,
      insideHostWindow: isCursorInsideHostWindow,
      tool: activeToolItem
        ? {
          id: activeToolItem.id,
          label: getToolItemLabel(activeToolItem),
          iconImagePath: activeToolItem.iconImagePath,
          iconImagePathAlt: activeToolItem.iconImagePathAlt,
          iconImagePathAlt2: activeToolItem.iconImagePathAlt2,
          cursorImagePath: activeToolItem.cursorImagePath,
          cursorImagePathAlt: activeToolItem.cursorImagePathAlt,
          cursorImagePathAlt2: activeToolItem.cursorImagePathAlt2,
          cursorHotspotX: activeToolItem.cursorHotspotX,
          cursorHotspotY: activeToolItem.cursorHotspotY,
          menuIconScale: activeToolItem.menuIconScale,
        }
        : null,
      textContext,
      timestamp: Date.now(),
    });
  }, [
    activeCursorToolId,
    activeToolItem,
    avatarRangeCursorVariant,
    draft,
    effectiveCursorVariant,
    avatarToolImageKind,
    isCursorInsideHostWindow,
    isCursorOverCompactCursorZone,
    isCursorWithinAvatarToolRange,
    onAvatarToolStateChange,
    outsideRangeCursorVariants,
  ]);

  function clearHammerSwingAnimation() {
    hammerSwingTimeoutIdsRef.current.forEach(timeoutId => window.clearTimeout(timeoutId));
    hammerSwingTimeoutIdsRef.current = [];
    setHammerSwingPhase('idle');
    setIsInnerHammerEasterEggActive(false);
  }

  function clearOutsideHammerResetTimer(shouldResetToPrimary = true) {
    if (outsideHammerResetTimeoutRef.current !== null) {
      window.clearTimeout(outsideHammerResetTimeoutRef.current);
      outsideHammerResetTimeoutRef.current = null;
    }
    if (shouldResetToPrimary) {
      setOutsideRangeCursorVariants(prev => ({ ...prev, hammer: 'primary' }));
    }
  }

  function spawnLollipopHearts(clientX: number, clientY: number) {
    const hearts: FloatingHeart[] = [
      { id: floatingHeartIdRef.current += 1, x: clientX - 12, y: clientY - 26, driftX: -26, driftY: -124, scale: 0.92, delayMs: 0 },
      { id: floatingHeartIdRef.current += 1, x: clientX + 10, y: clientY - 20, driftX: 24, driftY: -138, scale: 1.06, delayMs: 110 },
      { id: floatingHeartIdRef.current += 1, x: clientX - 4, y: clientY - 40, driftX: -18, driftY: -154, scale: 0.84, delayMs: 190 },
    ];
    setFloatingHearts(prev => [...prev, ...hearts]);
    hearts.forEach(heart => {
      const timeoutId = window.setTimeout(() => {
        setFloatingHearts(prev => prev.filter(item => item.id !== heart.id));
        floatingHeartTimeoutIdsRef.current = floatingHeartTimeoutIdsRef.current.filter(id => id !== timeoutId);
      }, 2100 + heart.delayMs);
      floatingHeartTimeoutIdsRef.current.push(timeoutId);
    });
  }

  function spawnFistDrops(clientX: number, clientY: number) {
    const drops: FloatingFistDrop[] = Array.from({ length: 3 }, () => {
      const launchAngleDeg = -140 + Math.random() * 100;
      const launchAngleRad = (launchAngleDeg * Math.PI) / 180;
      const distance = 76 + Math.random() * 42;
      return {
        id: floatingFistDropIdRef.current += 1,
        x: clientX - 8 + (Math.random() * 28 - 14),
        y: clientY - 24 + (Math.random() * 18 - 9),
        driftX: Math.round(Math.cos(launchAngleRad) * distance),
        driftY: Math.round(Math.sin(launchAngleRad) * distance),
        rotation: Math.round(-120 + Math.random() * 240),
        scale: Number((0.82 + Math.random() * 0.38).toFixed(2)),
        delayMs: Math.round(Math.random() * 140),
      };
    });
    setFloatingFistDrops(prev => [...prev, ...drops]);
    drops.forEach(drop => {
      const timeoutId = window.setTimeout(() => {
        setFloatingFistDrops(prev => prev.filter(item => item.id !== drop.id));
        floatingFistDropTimeoutIdsRef.current = floatingFistDropTimeoutIdsRef.current.filter(id => id !== timeoutId);
      }, 920 + drop.delayMs);
      floatingFistDropTimeoutIdsRef.current.push(timeoutId);
    });
  }

  function recordInteractionBurst(key: string, windowMs: number) {
    const now = Date.now();
    const recentTimestamps = (interactionBurstHistoryRef.current[key] ?? [])
      .filter(timestamp => now - timestamp <= windowMs);
    recentTimestamps.push(now);
    interactionBurstHistoryRef.current[key] = recentTimestamps;
    return recentTimestamps.length;
  }

  function updateHammerCursorOverlayPosition(clientX: number, clientY: number) {
    latestPointerPositionRef.current = { x: clientX, y: clientY };
    const overlayNode = hammerCursorOverlayRef.current;
    if (!overlayNode || !hammerToolItem) return;
    const hotspotX = hammerToolItem.cursorHotspotX ?? 18;
    const hotspotY = hammerToolItem.cursorHotspotY ?? 18;
    overlayNode.style.transform = `translate3d(${clientX - hotspotX}px, ${clientY - hotspotY}px, 0)`;
  }

  function updateAvatarCursorOverlayPosition(clientX: number, clientY: number) {
    latestPointerPositionRef.current = { x: clientX, y: clientY };
    const overlayNode = avatarCursorOverlayRef.current;
    if (!overlayNode || !activeToolItem) return;
    const hotspotX = activeToolItem.cursorHotspotX ?? 18;
    const hotspotY = activeToolItem.cursorHotspotY ?? 18;
    overlayNode.style.transform = `translate3d(${clientX - hotspotX}px, ${clientY - hotspotY}px, 0)`;
  }

  function emitAvatarInteraction<T extends AvatarInteractionToolId>(
    toolId: T,
    actionId: AvatarInteractionPayloadByTool[T]['actionId'],
    target: AvatarInteractionPayload['target'],
    clientX: number,
    clientY: number,
    options?: {
      intensity?: InteractionIntensity;
      rewardDrop?: boolean;
      easterEgg?: boolean;
      touchZone?: AvatarTouchZone;
    },
  ) {
    const callback = avatarInteractionCallbackRef.current;
    if (!callback) return;

    const payload = {
      interactionId: createAvatarInteractionId(),
      toolId,
      actionId,
      target,
      pointer: {
        clientX,
        clientY,
      },
      timestamp: Date.now(),
    } as AvatarInteractionPayloadByTool[T];

    const textContext = sanitizeInteractionTextContext(draftRef.current);
    if (textContext) {
      payload.textContext = textContext;
    }
    if (options?.intensity) {
      payload.intensity = options.intensity;
    }
    if (options?.touchZone && toolId !== 'lollipop') {
      (payload as { touchZone?: AvatarTouchZone }).touchZone = options.touchZone;
    }
    if (options?.rewardDrop && toolId === 'fist') {
      (payload as Extract<AvatarInteractionPayload, { toolId: 'fist' }>).rewardDrop = true;
    }
    if (options?.easterEgg && toolId === 'hammer') {
      (payload as Extract<AvatarInteractionPayload, { toolId: 'hammer' }>).easterEgg = true;
    }

    callback(payload);
  }

  useEffect(() => {
    if (!toolMenuOpen) return;

    const closeMenuOnOutsideClick = (event: MouseEvent) => {
      const menuNode = toolMenuRef.current;
      if (!menuNode) return;
      if (menuNode.contains(event.target as Node)) return;
      if (compactInputToolFanRef.current?.contains(event.target as Node)) return;
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
    if (!activeCursorToolId) return;

    const resetFistCursorVariant = () => {
      setAvatarRangeCursorVariants(prev => ({ ...prev, fist: 'primary' }));
      setOutsideRangeCursorVariants(prev => ({ ...prev, fist: 'primary' }));
    };

    const toggleCursorVariantOnPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      const isOverCompactCursorZoneAtPointer = isPointWithinCompactCursorZone(event.clientX, event.clientY);
      setIsCursorOverCompactCursorZone(previousValue => (
        previousValue === isOverCompactCursorZoneAtPointer ? previousValue : isOverCompactCursorZoneAtPointer
      ));
      if (isOverCompactCursorZoneAtPointer) {
        return;
      }
      const avatarRangeHit = getAvatarRangeHit(event.clientX, event.clientY, avatarToolCacheState);
      const isOverAvatarAtPointer = avatarRangeHit !== null;
      setCursorOverAvatarRange(isOverAvatarAtPointer, { allowHold: true });

      if (activeCursorToolId === 'lollipop') {
        if (isOverAvatarAtPointer) {
          const currentVariant = avatarRangeCursorVariants.lollipop ?? 'primary';
          const actionId = currentVariant === 'primary'
            ? 'offer'
            : currentVariant === 'secondary'
              ? 'tease'
              : 'tap_soft';
          const lollipopTapCount = currentVariant === 'tertiary'
            ? recordInteractionBurst('lollipop:tap_soft', 1800)
            : 0;
          const intensity: InteractionIntensity = currentVariant === 'tertiary'
            ? (lollipopTapCount >= 4 ? 'burst' : 'rapid')
            : 'normal';
          emitAvatarInteraction('lollipop', actionId, 'avatar', event.clientX, event.clientY, {
            intensity,
          });
          playAvatarToolSound(avatarToolSoundPaths.lollipopBite);

          if (currentVariant === 'tertiary') {
            spawnLollipopHearts(event.clientX, event.clientY);
            return;
          }
          const nextVariant: CursorVariant = currentVariant === 'primary' ? 'secondary' : 'tertiary';
          setAvatarRangeCursorVariants(prev => (
            prev.lollipop === nextVariant ? prev : { ...prev, lollipop: nextVariant }
          ));
          return;
        }
        return;
      }
      if (activeCursorToolId === 'fist') {
        const shouldSpawnRewardDrop = isOverAvatarAtPointer && Math.random() < 0.25;
        const fistTapCount = isOverAvatarAtPointer
          ? recordInteractionBurst('fist:poke', 1400)
          : 0;
        setAvatarRangeCursorVariants(prev => ({ ...prev, fist: 'secondary' }));
        setOutsideRangeCursorVariants(prev => ({ ...prev, fist: 'secondary' }));
        if (isOverAvatarAtPointer) {
          emitAvatarInteraction(
            'fist',
            'poke',
            'avatar',
            event.clientX,
            event.clientY,
            {
              intensity: fistTapCount >= 4 ? 'rapid' : 'normal',
              rewardDrop: shouldSpawnRewardDrop,
              touchZone: avatarRangeHit?.touchZone,
            },
          );
        }
        if (shouldSpawnRewardDrop) {
          playAvatarToolSound(avatarToolSoundPaths.coinDrop);
          spawnFistDrops(event.clientX, event.clientY);
        }
        return;
      }
      if (activeCursorToolId === 'hammer') {
        if (!isOverAvatarAtPointer) {
          clearOutsideHammerResetTimer(false);
          setOutsideRangeCursorVariants(prev => ({ ...prev, hammer: 'secondary' }));
          outsideHammerResetTimeoutRef.current = window.setTimeout(() => {
            setOutsideRangeCursorVariants(prev => ({ ...prev, hammer: 'primary' }));
            outsideHammerResetTimeoutRef.current = null;
          }, 220);
          return;
        }
        if (hammerSwingPhase !== 'idle') {
          return;
        }
        const shouldTriggerInnerHammerEasterEgg = Math.random() < 0.05;
        const hammerBonkCount = recordInteractionBurst('hammer:bonk', 3200);
        const hammerIntensity: InteractionIntensity = shouldTriggerInnerHammerEasterEgg
          ? 'easter_egg'
          : hammerBonkCount >= 3
            ? 'burst'
            : hammerBonkCount >= 2
              ? 'rapid'
              : 'normal';
        emitAvatarInteraction('hammer', 'bonk', 'avatar', event.clientX, event.clientY, {
          intensity: hammerIntensity,
          easterEgg: shouldTriggerInnerHammerEasterEgg,
          touchZone: avatarRangeHit?.touchZone,
        });
        playAvatarToolSound(
          shouldTriggerInnerHammerEasterEgg
            ? avatarToolSoundPaths.hammerBig
            : avatarToolSoundPaths.hammerSmall,
        );
        setIsInnerHammerEasterEggActive(shouldTriggerInnerHammerEasterEgg);
        setHammerSwingPhase('windup');
        hammerSwingTimeoutIdsRef.current = [
          window.setTimeout(() => {
            setHammerSwingPhase('swing');
          }, 240),
          window.setTimeout(() => {
            setHammerSwingPhase('impact');
          }, 420),
          window.setTimeout(() => {
            setHammerSwingPhase('recover');
          }, 520),
          window.setTimeout(() => {
            setHammerSwingPhase('idle');
            if (shouldTriggerInnerHammerEasterEgg) {
              setIsInnerHammerEasterEggActive(false);
            }
            hammerSwingTimeoutIdsRef.current = [];
          }, 620),
        ];
        return;
      }
      if (isOverAvatarAtPointer) {
        setAvatarRangeCursorVariants(prev => ({
          ...prev,
          [activeCursorToolId]: prev[activeCursorToolId] === 'primary' ? 'secondary' : 'primary',
        }));
      } else {
        setOutsideRangeCursorVariants(prev => ({
          ...prev,
          [activeCursorToolId]: prev[activeCursorToolId] === 'primary' ? 'secondary' : 'primary',
        }));
      }
    };

    const handlePointerUp = () => {
      if (activeCursorToolId !== 'fist') return;
      resetFistCursorVariant();
    };

    window.addEventListener('pointerdown', toggleCursorVariantOnPointerDown, true);
    window.addEventListener('pointerup', handlePointerUp, true);
    window.addEventListener('pointercancel', handlePointerUp, true);
    window.addEventListener('blur', handlePointerUp);
    return () => {
      window.removeEventListener('pointerdown', toggleCursorVariantOnPointerDown, true);
      window.removeEventListener('pointerup', handlePointerUp, true);
      window.removeEventListener('pointercancel', handlePointerUp, true);
      window.removeEventListener('blur', handlePointerUp);
    };
  }, [activeCursorToolId, avatarRangeCursorVariants, hammerSwingPhase, setCursorOverAvatarRange]);

  useEffect(() => {
    if (activeCursorToolId === 'hammer') return;
    clearHammerSwingAnimation();
    clearOutsideHammerResetTimer();
  }, [activeCursorToolId, avatarToolCacheState]);

  useEffect(() => () => {
    clearHammerSwingAnimation();
    clearOutsideHammerResetTimer();
    if (avatarRangeHoldTimerRef.current !== null) {
      window.clearTimeout(avatarRangeHoldTimerRef.current);
      avatarRangeHoldTimerRef.current = null;
    }
    floatingHeartTimeoutIdsRef.current.forEach(timeoutId => window.clearTimeout(timeoutId));
    floatingHeartTimeoutIdsRef.current = [];
    floatingFistDropTimeoutIdsRef.current.forEach(timeoutId => window.clearTimeout(timeoutId));
    floatingFistDropTimeoutIdsRef.current = [];
  }, []);

  useEffect(() => {
    if (!activeCursorToolId) {
      setCursorOverAvatarRange(false, { allowHold: false });
      setIsCursorOverCompactCursorZone(false);
      return;
    }

    let frameId = 0;

    const updateCursorRangeState = (clientX: number, clientY: number) => {
      const nextValue = isPointerWithinAvatarRange(clientX, clientY, avatarToolCacheState);
      setCursorOverAvatarRange(nextValue, { allowHold: true });
    };

    const handlePointerMove = (event: PointerEvent) => {
      setIsCursorInsideHostWindow(true);
      latestPointerPositionRef.current = { x: event.clientX, y: event.clientY };
      latestPointerTargetRef.current = event.target;
      if (frameId) return;

      frameId = window.requestAnimationFrame(() => {
        frameId = 0;
        const { x, y } = latestPointerPositionRef.current;
        const isOverCompactCursorZone = isPointerOverCompactCursorZone(latestPointerTargetRef.current);
        if (activeCursorToolId === 'hammer') {
          updateHammerCursorOverlayPosition(x, y);
        } else if (activeCursorToolId) {
          updateAvatarCursorOverlayPosition(x, y);
        }
        updateCursorRangeState(x, y);
        setIsCursorOverCompactCursorZone(previousValue => (
          previousValue === isOverCompactCursorZone ? previousValue : isOverCompactCursorZone
        ));
      });
    };

    const hideLocalCursorOverlay = () => {
      clearAvatarBoundsCache(avatarToolCacheState);
      latestPointerTargetRef.current = null;
      setCursorOverAvatarRange(false, { allowHold: false });
      setIsCursorOverCompactCursorZone(false);
      setIsCursorInsideHostWindow(false);
    };

    const isPointerOutsideViewport = (event: MouseEvent | PointerEvent) => (
      event.clientX <= 0
      || event.clientY <= 0
      || event.clientX >= window.innerWidth
      || event.clientY >= window.innerHeight
    );

    const handleMouseOut = (event: MouseEvent) => {
      if (event.relatedTarget !== null) return;
      if (!isPointerOutsideViewport(event)) return;
      hideLocalCursorOverlay();
    };

    const handlePointerOut = (event: PointerEvent) => {
      if (event.relatedTarget !== null) return;
      if (!isPointerOutsideViewport(event)) return;
      hideLocalCursorOverlay();
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        hideLocalCursorOverlay();
      }
    };

    window.addEventListener('pointermove', handlePointerMove, { passive: true, capture: true });
    document.addEventListener('mouseleave', hideLocalCursorOverlay);
    window.addEventListener('pointerout', handlePointerOut, true);
    window.addEventListener('mouseout', handleMouseOut, true);
    window.addEventListener('blur', hideLocalCursorOverlay);
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      if (frameId) {
        window.cancelAnimationFrame(frameId);
      }
      clearAvatarBoundsCache(avatarToolCacheState);
      window.removeEventListener('pointermove', handlePointerMove, true);
      document.removeEventListener('mouseleave', hideLocalCursorOverlay);
      window.removeEventListener('pointerout', handlePointerOut, true);
      window.removeEventListener('mouseout', handleMouseOut, true);
      window.removeEventListener('blur', hideLocalCursorOverlay);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [activeCursorToolId, avatarToolCacheState, setCursorOverAvatarRange]);

  useEffect(() => {
    const root = document.documentElement;
    let cancelled = false;

    if (!activeCursorToolId || composerHidden) {
      clearGlobalToolCursorState();
      return;
    }

    if ((shouldUseLocalDesktopCursorOverlay || isElectronMultiWindow) && !isCursorInsideHostWindow) {
      clearGlobalToolCursorState();
      return;
    }

    const selected = toolIconItems.find(item => item.id === activeCursorToolId);
    if (!selected) {
      clearGlobalToolCursorState();
      return;
    }

    clearForcedNativeCursorFallback();
    root.classList.add('neko-tool-cursor-active');

    const applyResolvedCursor = async () => {
      let cursorValue: string;
      if (shouldUseLocalDesktopCursorOverlay || isElectronMultiWindow) {
        cursorValue = 'none';
      } else if (isCursorOverAvatarRange && !isCursorOverCompactCursorZone) {
        cursorValue = resolveCursorValue(selected, effectiveCursorVariant);
      } else {
        cursorValue = await resolveCompactCursorValue(selected, effectiveCursorVariant, avatarToolCacheState);
      }
      if (cancelled) return;
      root.style.setProperty('--neko-chat-tool-cursor', cursorValue);
    };

    void applyResolvedCursor();

    return () => {
      cancelled = true;
    };
  }, [activeCursorToolId, composerHidden, avatarToolCacheState, effectiveCursorVariant, isCursorInsideHostWindow, isCursorOverAvatarRange, isCursorOverCompactCursorZone, isElectronMultiWindow, shouldUseLocalDesktopCursorOverlay]);

  useEffect(() => {
    if (!activeToolItem) return;
    void resolveCompactCursorValue(activeToolItem, effectiveCursorVariant, avatarToolCacheState);
  }, [activeToolItem, avatarToolCacheState, effectiveCursorVariant]);

  useEffect(() => {
    if (!avatarCursorOverlayActive) return;
    updateAvatarCursorOverlayPosition(
      latestPointerPositionRef.current.x,
      latestPointerPositionRef.current.y,
    );
  }, [avatarCursorOverlayActive, avatarCursorOverlayImagePath, activeToolItem]);

  useEffect(() => {
    if (!hammerCursorOverlayActive) return;
    updateHammerCursorOverlayPosition(
      latestPointerPositionRef.current.x,
      latestPointerPositionRef.current.y,
    );
  }, [hammerCursorOverlayActive, hammerSwingPhase]);

  useEffect(() => {
    if (composerHidden || composerDisabled) {
      clearActiveCursorToolSelection();
    }
  }, [clearActiveCursorToolSelection, composerHidden, composerDisabled]);

  useEffect(() => {
    function handleDeactivate() {
      clearActiveCursorToolSelection();
    }
    window.addEventListener('neko:deactivate-tool-cursor', handleDeactivate);
    return () => window.removeEventListener('neko:deactivate-tool-cursor', handleDeactivate);
  }, []);

  useEffect(() => () => {
    clearGlobalToolCursorState();
  }, []);

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
    if (composerDisabled) return;
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

  const getCompactToolWheelSlot = (toolIndex: number): number | null => {
    const forwardDistance = (toolIndex - compactInputToolWheelIndex + COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT) % COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT;
    if (forwardDistance <= 2) {
      return forwardDistance;
    }
    if (forwardDistance >= COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT - 2) {
      return forwardDistance - COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT;
    }
    return null;
  };

  const getCompactToolWheelTabIndex = (toolIndex: number): number => {
    const slot = getCompactToolWheelSlot(toolIndex);
    return compactInputToolFanOpen && slot !== null && Math.abs(slot) <= 2 ? 0 : -1;
  };

  const getCompactToolWheelAriaHidden = (toolIndex: number): 'true' | 'false' => {
    const slot = getCompactToolWheelSlot(toolIndex);
    return compactInputToolFanOpen && slot !== null && Math.abs(slot) <= 2 ? 'false' : 'true';
  };

  const getCompactToolWheelSlotValue = (toolIndex: number): string => {
    const slot = getCompactToolWheelSlot(toolIndex);
    if (slot !== null) return String(slot);

    const forwardDistance = (toolIndex - compactInputToolWheelIndex + COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT) % COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT;
    if (forwardDistance === 3) return 'hidden-forward';
    if (forwardDistance === COMPACT_INPUT_TOOL_WHEEL_ITEM_COUNT - 3) return 'hidden-backward';
    return 'hidden';
  };

  const compactInputToolFanActionsDisabled = composerDisabled
    || !compactInputToolFanOpen
    || !compactInputToolFanInteractive;
  const compactInputToolWheelChargeLapRatio = compactInputToolWheelChargeRatio * 2;
  const compactInputToolWheelChargeFirstLapAngle = Math.round(
    Math.min(1, compactInputToolWheelChargeLapRatio) * 360,
  );
  const compactInputToolWheelChargeSecondLapAngle = Math.round(
    Math.min(1, Math.max(0, compactInputToolWheelChargeLapRatio - 1)) * 360,
  );
  const compactInputToolWheelChargeStyle = {
    '--compact-tool-wheel-charge-first-angle': `${compactInputToolWheelChargeFirstLapAngle}deg`,
    '--compact-tool-wheel-charge-second-angle': `${compactInputToolWheelChargeSecondLapAngle}deg`,
  } as CSSProperties;
  const compactToolToggleVisible = isCompactSurface && !composerHidden;
  const compactToolToggleActsAsSubmit = effectiveCompactChatState === 'input' && compactInputHasPayload;
  const compactInputToolToggleButton = compactToolToggleVisible ? (
    <button
      className={`send-button-circle compact-input-tool-toggle${compactInputToolFanOpen ? ' is-open' : ''}`}
      ref={compactInputToolToggleRef}
      type={compactToolToggleActsAsSubmit ? 'submit' : 'button'}
      data-compact-no-drag="true"
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
        onCompactMinimizeRequest?.();
      }}
    >
      <img
        className="compact-chat-minimize-ball-icon"
        src="/static/assets/neko-idle/chat-minimized-yarn-ball.png"
        alt=""
        aria-hidden="true"
      />
    </button>
  );

  const compactInputToolFanNode = compactToolToggleVisible ? (
    <div
      ref={compactInputToolFanRef}
      className="compact-input-tool-fan"
      role="group"
      aria-label={overflowMenuAriaLabel}
      data-compact-geometry-item="toolFan"
      data-compact-geometry-owner="surface"
      data-compact-no-drag="true"
      data-compact-input-tool-fan-open={compactInputToolFanOpen ? 'true' : 'false'}
      data-compact-input-tool-fan-interactive={compactInputToolFanInteractive ? 'true' : 'false'}
      data-compact-tool-wheel-layout={compactInputToolWheelLayout}
      data-compact-tool-wheel-fast-animation={compactInputToolWheelFastAnimation ? 'true' : 'false'}
      data-compact-tool-wheel-charge-active={compactInputToolWheelChargeRatio > 0 ? 'true' : 'false'}
      data-compact-tool-wheel-charge-direction={compactInputToolWheelChargeDirection === 1 ? 'forward' : compactInputToolWheelChargeDirection === -1 ? 'backward' : 'none'}
      data-compact-tool-wheel-charge-release-active={compactInputToolWheelChargeReleaseActive ? 'true' : 'false'}
      aria-hidden={compactInputToolFanOpen ? 'false' : 'true'}
      onPointerEnter={handleCompactInputToolHoverEnter}
      onPointerLeave={handleCompactInputToolHoverLeave}
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
          didRotate: false,
          captureTarget,
        };
        try {
          captureTarget.setPointerCapture?.(event.pointerId);
        } catch (_) {}
      }}
      onPointerMove={(event) => {
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
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-import"
        type="button"
        aria-label={resolvedImportImageAriaLabel}
        title={importImageButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(4)}
        aria-hidden={getCompactToolWheelAriaHidden(4)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(4)}
        onClick={compactFanRunAction(onComposerImportImage)}
      >
        <img src="/static/icons/import_image_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-screenshot"
        type="button"
        aria-label={resolvedScreenshotAriaLabel}
        title={screenshotButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(0)}
        aria-hidden={getCompactToolWheelAriaHidden(0)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(0)}
        onClick={compactFanRunAction(onComposerScreenshot)}
      >
        <img src="/static/icons/screenshot_new_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className={`composer-tool-btn composer-galgame-btn compact-input-tool-item compact-input-tool-item-galgame${galgameModeEnabled ? ' is-active' : ''}`}
        type="button"
        aria-label={resolvedGalgameAriaLabel}
        aria-pressed={galgameModeEnabled}
        title={galgameToggleButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(6)}
        aria-hidden={getCompactToolWheelAriaHidden(6)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(6)}
        data-compact-tool-active={galgameModeEnabled ? 'true' : 'false'}
        onClick={compactFanToggleOnAction(onGalgameModeToggle)}
      >
        <span className="composer-galgame-btn-glyph" aria-hidden="true">G</span>
      </button>
      <button
        className={`composer-tool-btn composer-translate-btn compact-input-tool-item compact-input-tool-item-translate${translateEnabled ? ' is-active' : ''}`}
        type="button"
        aria-label={resolvedTranslateAriaLabel}
        aria-pressed={translateEnabled}
        title={translateButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(2)}
        aria-hidden={getCompactToolWheelAriaHidden(2)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(2)}
        data-compact-tool-active={translateEnabled ? 'true' : 'false'}
        onClick={compactFanToggleOnAction(onTranslateToggle)}
      >
        <img src="/static/icons/translate_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className="composer-tool-btn compact-input-tool-item compact-input-tool-item-jukebox"
        type="button"
        aria-label={jukeboxButtonAriaLabel}
        title={jukeboxButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(3)}
        aria-hidden={getCompactToolWheelAriaHidden(3)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(3)}
        onClick={compactFanRunAction(onJukeboxClick)}
      >
        <img src="/static/icons/jukebox_icon.png" alt="" aria-hidden="true" />
      </button>
      <button
        className={`composer-tool-btn compact-input-tool-item compact-input-tool-item-export${compactExportControlsVisible ? ' is-active' : ''}`}
        type="button"
        aria-label={compactExportControlsButtonLabel}
        aria-pressed={compactExportControlsVisible}
        title={compactExportControlsButtonLabel}
        disabled={compactInputToolFanActionsDisabled}
        tabIndex={getCompactToolWheelTabIndex(5)}
        aria-hidden={getCompactToolWheelAriaHidden(5)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(5)}
        data-compact-tool-active={compactExportControlsVisible ? 'true' : 'false'}
        onClick={compactFanRunAction(handleCompactExportControlsToggle)}
      >
        <svg viewBox="0 0 1024 1024" width="24" height="24" fill="currentColor" aria-hidden="true">
          <path d="M855.467 501.333c-17.067 0-32 14.934-32 32v198.4c0 70.4-59.734 130.134-130.134 130.134H356.267c-83.2 0-151.467-66.134-151.467-149.334V358.4c0-64 53.333-117.333 117.333-117.333h168.534c17.066 0 32-14.934 32-32s-14.934-32-32-32H322.133c-100.266 0-181.333 81.066-181.333 181.333v352c0 117.333 96 213.333 215.467 213.333h337.066c106.667 0 194.134-87.466 194.134-194.133V533.333c0-17.066-14.934-32-32-32zM680.533 256H761.6L458.667 569.6A30.933 30.933 0 0 0 480 622.933c8.533 0 17.067-4.266 23.467-10.666l305.066-313.6v89.6c0 17.066 14.934 32 32 32s32-14.934 32-32v-147.2c0-27.734-23.466-51.2-51.2-51.2h-140.8c-17.066 0-32 14.933-32 32s14.934 34.133 32 34.133z" />
        </svg>
      </button>
      <div
        className="composer-tool-menu compact-input-tool-item compact-input-tool-item-avatar"
        ref={toolMenuRef}
        aria-hidden={getCompactToolWheelAriaHidden(1)}
        data-compact-tool-wheel-slot={getCompactToolWheelSlotValue(1)}
        data-compact-tool-active={toolMenuOpen || activeToolItem ? 'true' : 'false'}
      >
        <button
          className={`composer-tool-btn composer-emoji-btn${toolMenuOpen || activeToolItem ? ' is-active' : ''}`}
          type="button"
          aria-label={selectedEmojiButtonAriaLabel}
          title={selectedEmojiButtonAriaLabel}
          aria-controls={toolMenuOpen ? 'composer-tool-popover-compact' : undefined}
          aria-expanded={toolMenuOpen}
          disabled={compactInputToolFanActionsDisabled}
          tabIndex={getCompactToolWheelTabIndex(1)}
          onClick={(event) => {
            if (shouldSuppressCompactToolClick(event)) {
              event.preventDefault();
              event.stopPropagation();
              return;
            }
            if (activeToolItem) {
              clearActiveCursorToolSelection();
              return;
            }
            compactInputToolFanOpenIntentRef.current = 'click';
            clearCompactInputToolFanCloseTimer();
            setToolMenuOpen(open => !open);
          }}
        >
          <img
            src={activeToolMenuVisual?.imagePath || '/static/icons/emoji_icon.png'}
            style={activeToolItem ? {
              transform: `translate(${activeToolMenuVisual?.offsetX ?? 0}px, ${activeToolMenuVisual?.offsetY ?? 0}px) scale(${activeToolItem.menuIconScale ?? 1})`,
            } : undefined}
            alt=""
            aria-hidden="true"
          />
        </button>
        {activeToolItem ? (
          <button
            className="composer-tool-clear-btn"
            type="button"
            aria-label={clearCursorToolAriaLabel}
            title={clearCursorToolAriaLabel}
            disabled={compactInputToolFanActionsDisabled}
            tabIndex={compactInputToolFanOpen ? 0 : -1}
            onClick={(event) => {
              if (shouldSuppressCompactToolClick(event)) {
                event.preventDefault();
                event.stopPropagation();
                return;
              }
              event.stopPropagation();
              setIsCursorInsideHostWindow(true);
              setActiveCursorToolId(null);
              setToolMenuOpen(false);
            }}
          >
            <span className="composer-tool-clear-icon" aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {toolMenuOpen && compactInputToolFanOpen ? (
        <div
          id="composer-tool-popover-compact"
          className="composer-icon-popover"
          role="group"
          aria-label={toolIconsAriaLabel}
        >
          {toolIconItems.map(item => {
            const itemLabel = getToolItemLabel(item);
            const menuVariant = activeCursorToolId === item.id
              ? effectiveCursorVariant
              : 'primary';
            const menuVisual = resolveMenuIconVisual(item, menuVariant);
            return (
            <button
              key={item.id}
              className={`composer-icon-button${activeCursorToolId === item.id ? ' is-active' : ''}`}
              type="button"
              aria-pressed={activeCursorToolId === item.id}
              aria-label={itemLabel}
              title={itemLabel}
              disabled={compactInputToolFanActionsDisabled}
              onClick={(event) => {
                if (shouldSuppressCompactToolClick(event)) {
                  event.preventDefault();
                  event.stopPropagation();
                  return;
                }
                latestPointerPositionRef.current = {
                  x: event.clientX,
                  y: event.clientY,
                };
                latestPointerTargetRef.current = event.currentTarget;
                setIsCursorInsideHostWindow(true);
                setIsCursorOverCompactCursorZone(true);
                setCursorOverAvatarRange(
                  isPointerWithinAvatarRange(event.clientX, event.clientY, avatarToolCacheState),
                  { allowHold: true },
                );
                if (activeCursorToolId === item.id) {
                  setActiveCursorToolId(null);
                  setToolMenuOpen(false);
                  return;
                }
                setAvatarRangeCursorVariants(prev => ({ ...prev, [item.id]: 'primary' }));
                setOutsideRangeCursorVariants(prev => ({ ...prev, [item.id]: 'primary' }));
                setActiveCursorToolId(item.id);
                setToolMenuOpen(false);
              }}
            >
              <img
                className="composer-icon-button-image"
                src={menuVisual.imagePath}
                style={{
                  transform: `translate(${menuVisual.offsetX}px, ${menuVisual.offsetY}px) scale(${item.menuIconScale ?? 1})`,
                }}
                alt=""
                aria-hidden="true"
              />
            </button>
            );
          })}
        </div>
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
      onHistoryResizePointerDown={handleCompactHistoryResizePointerDown}
      onHistoryResizePointerMove={handleCompactHistoryResizePointerMove}
      onHistoryResizePointerUp={handleCompactHistoryResizePointerUp}
      onHistoryResizePointerCancel={handleCompactHistoryResizePointerCancel}
    />
  ) : null;
  const compactExportHistoryNode = compactExportHistoryElement;
  const compactHistoryVisibilityHandleNode = isCompactSurface ? (
    <button
      className={`compact-history-visibility-handle${compactExportHistoryOpen ? ' is-open' : ''}`}
      type="button"
      aria-label={compactExportHistoryToggleLabel}
      aria-expanded={compactExportHistoryOpen}
      title={compactExportHistoryToggleLabel}
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
  const compactMusicPlayerVisibility = compactExportHistoryOpen
    ? 'open'
    : (compactExportHistoryMounted ? 'closing' : 'closed');
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
    >
      {compactExportHistoryNode}
      {compactHistoryVisibilityHandleNode}
      {compactMusicPlayerMountNode}
      {compactChoiceLayerNode}
      {floatingFistDrops.map(drop => (
        <span
          key={drop.id}
          className="fist-floating-drop"
          aria-hidden="true"
          style={{
            left: `${drop.x}px`,
            top: `${drop.y}px`,
            '--drop-drift-x': `${drop.driftX}px`,
            '--drop-drift-y': `${drop.driftY}px`,
            '--drop-rotation': `${drop.rotation}deg`,
            '--drop-scale': drop.scale,
            '--drop-delay': `${drop.delayMs}ms`,
          } as CSSProperties}
        >
            <img
            className="fist-floating-drop-image"
            src="/static/icons/cat_moneny.png"
            alt=""
          />
        </span>
      ))}
      {floatingHearts.map(heart => (
        <span
          key={heart.id}
          className="lollipop-floating-heart"
          aria-hidden="true"
          style={{
            left: `${heart.x}px`,
            top: `${heart.y}px`,
            '--heart-drift-x': `${heart.driftX}px`,
            '--heart-drift-y': `${heart.driftY}px`,
            '--heart-sway-x': `${Math.max(8, Math.round(Math.abs(heart.driftX) * 0.32)) * (heart.driftX < 0 ? -1 : 1)}px`,
            '--heart-scale': heart.scale,
            '--heart-delay': `${heart.delayMs}ms`,
          } as CSSProperties}
        >
          <span className="lollipop-floating-heart-glyph">*</span>
        </span>
      ))}
      {activeToolItem && activeCursorToolId !== 'hammer' && avatarCursorOverlayActive ? (
        <div
          ref={avatarCursorOverlayRef}
          className={`avatar-cursor-overlay avatar-cursor-overlay-${activeToolItem.id}${avatarCursorOverlayActive ? ' is-visible' : ''}${avatarCursorOverlayCompact ? ' is-compact' : ''}`}
          aria-hidden="true"
        >
          <div
            className="avatar-cursor-overlay-stage"
            style={{
              transformOrigin: `${activeToolItem.cursorHotspotX ?? 18}px ${activeToolItem.cursorHotspotY ?? 18}px`,
            }}
          >
            <img
              className={`avatar-cursor-overlay-image avatar-cursor-overlay-image-${activeToolItem.id}`}
              src={avatarCursorOverlayImagePath}
              alt=""
            />
          </div>
        </div>
      ) : null}
      {hammerToolItem && hammerCursorOverlayActive ? (
        <div
          ref={hammerCursorOverlayRef}
          className={`hammer-cursor-overlay${hammerCursorOverlayActive ? ' is-visible' : ''}${hammerCursorOverlayCompact ? ' is-compact' : ''}${isInnerHammerEasterEggActive ? ' is-easter-egg' : ''}`}
          aria-hidden="true"
        >
          <div
            className="hammer-cursor-overlay-stage"
            style={{
              transformOrigin: `${hammerToolItem.cursorHotspotX ?? 18}px ${hammerToolItem.cursorHotspotY ?? 18}px`,
            }}
          >
            {hammerCursorOverlayUsesCompactImage ? (
              <img
                className="hammer-cursor-overlay-compact-image"
                src={hammerCursorOverlayCompactImagePath}
                alt=""
              />
            ) : (
              <div
                className={`hammer-cursor-overlay-visual${hammerCursorOverlayMotionActive ? ' is-active' : ' is-idle'}${hammerSwingPhase === 'impact' ? ' is-impact' : ''}`}
                style={{
                  transformOrigin: `${hammerOverlayTransformOrigin.x}px ${hammerOverlayTransformOrigin.y}px`,
                }}
              >
                <img
                  className="hammer-cursor-overlay-image hammer-cursor-overlay-image-primary"
                  src={hammerCursorOverlayPrimaryImagePath}
                  alt=""
                />
                <img
                  className="hammer-cursor-overlay-image hammer-cursor-overlay-image-secondary"
                  src={hammerCursorOverlaySecondaryImagePath}
                  alt=""
                />
              </div>
	                  )}
                </div>
              </div>
      ) : null}
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

        <footer
          className={`composer-panel ${surfaceModeClassName}${galgameModeEnabled ? ' is-galgame-mode' : ''}`}
          style={composerHidden && !isCompactSurface ? { display: 'none' } : undefined}
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
                className="compact-chat-surface-shell"
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
                  data-compact-tool-toggle-visible={compactToolToggleVisible ? 'true' : 'false'}
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
                        placeholder={inputPlaceholder}
                        aria-label={inputPlaceholder}
                        rows={1}
                        value={draft}
                        readOnly={composerDisabled}
                        disabled={composerDisabled}
                        onChange={(event) => {
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
                        disabled={composerDisabled}
                        onClick={() => {
                          if (composerHidden) return;
                          if (isGuideChatButtonLockActive()) return;
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
      </section>
    </main>
  );
}
